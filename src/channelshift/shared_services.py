"""Server-managed member features with durable shared usage limits.

Credentials are read on the host and never included in status, quota records,
callback arguments, or results. Attempts count against the daily budget even
when the external provider fails; a restart does not reset the budget.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import apify_reference, jev_review
from .service_errors import SAFE_SERVICE_ERRORS, ServiceError

SAFE_ERROR_CODES = SAFE_SERVICE_ERRORS

# (per member per UTC day, all members per UTC day)
LIMITS = {'requirements_review': (10, 100), 'reference_collect': (3, 30)}
LABELS = {'requirements_review': '요구사항 검토', 'reference_collect': '참고자료 수집'}


def _review_configured():
    try:
        jev_review._credential()
        return True
    except (ValueError, OSError):
        return False


def _member(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{32}', value):
        raise ServiceError('service_invalid_input')
    return value


def _review_input(source, requirements):
    """Validate the bounded wrapper input before reserving shared capacity."""
    if type(source) is not str or not source.strip() or len(source) > 12000:
        raise ServiceError('service_invalid_input')
    if type(requirements) is not list or not 1 <= len(requirements) <= 32:
        raise ServiceError('service_invalid_input')
    seen = set()
    for row in requirements:
        if type(row) is not dict or set(row) != {'id', 'text', 'quote', 'origin'}:
            raise ServiceError('service_invalid_input')
        if any(type(value) is not str for value in row.values()):
            raise ServiceError('service_invalid_input')
        if (not 1 <= len(row['id']) <= 64 or row['id'] in seen or not row['text'].strip()
                or len(row['text']) > 2000 or len(row['quote']) > 12000
                or row['origin'] not in {'client', 'internal'}
                or (row['origin'] == 'client' and (not row['quote'].strip() or row['quote'] not in source))
                or (row['origin'] == 'internal' and row['quote'])):
            raise ServiceError('service_invalid_input')
        seen.add(row['id'])
    try:
        if len(json.dumps([source, requirements], ensure_ascii=False, allow_nan=False).encode('utf-8')) > 100000:
            raise ServiceError('service_invalid_input')
    except (ValueError, UnicodeError):
        raise ServiceError('service_invalid_input') from None


class SharedServices:
    def __init__(self, path, *, review=None, collect=None, availability=None, clock=None):
        self.path = Path(path)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ServiceError('service_unavailable')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._review = review or jev_review.review_requirements
        self._collect = collect or apify_reference.collect_reference
        self._availability = availability or {
            'requirements_review': _review_configured,
            'reference_collect': apify_reference.configured,
        }
        self._clock = clock or time.time
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS feature_usage (
                    id TEXT PRIMARY KEY, member_id TEXT NOT NULL, feature TEXT NOT NULL,
                    day INTEGER NOT NULL, started REAL NOT NULL, finished INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS feature_usage_day ON feature_usage(day,feature,member_id);
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            with db:
                yield db
        except sqlite3.Error:
            raise ServiceError('service_unavailable') from None
        finally:
            if db:
                db.close()

    def status(self):
        result = {}
        for feature in LIMITS:
            try:
                available = bool(self._availability[feature]())
            except Exception:
                available = False
            result[feature] = {'available': available, 'label': LABELS[feature],
                               'status': 'configured' if available else 'not_configured'}
        return result

    @contextmanager
    def _reserve(self, member_id, feature):
        _member(member_id)
        if not self.status()[feature]['available']:
            raise ServiceError('service_not_configured')
        now = self._clock()
        day = int(now // 86400)
        job_id = uuid.uuid4().hex
        member_limit, global_limit = LIMITS[feature]
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Elapsed time cannot prove an external operation has ended. A
            # process interrupted before releasing its reservation needs an
            # operator check; restarting never silently grants new capacity.
            active = db.execute('SELECT member_id FROM feature_usage WHERE finished=0').fetchall()
            if len(active) >= 2 or any(row[0] == member_id for row in active):
                raise ServiceError('service_busy')
            counts = db.execute('SELECT member_id,COUNT(*) FROM feature_usage WHERE day=? AND feature=? GROUP BY member_id',
                                (day, feature)).fetchall()
            if sum(row[1] for row in counts) >= global_limit or sum(row[1] for row in counts if row[0] == member_id) >= member_limit:
                raise ServiceError('service_rate_limited')
            db.execute('DELETE FROM feature_usage WHERE day<? AND finished=1', (day - 7,))
            db.execute('INSERT INTO feature_usage(id,member_id,feature,day,started) VALUES(?,?,?,?,?)',
                       (job_id, member_id, feature, day, now))
        try:
            yield
        finally:
            with self._connect() as db:
                db.execute('UPDATE feature_usage SET finished=1 WHERE id=?', (job_id,))

    def review_for(self, member_id):
        _member(member_id)

        def review(source, requirements):
            _review_input(source, requirements)
            with self._reserve(member_id, 'requirements_review'):
                try:
                    return self._review(source, requirements)
                except jev_review.JevError as error:
                    code = {'jev_key_missing': 'service_not_configured',
                            'invalid_jev_input': 'service_invalid_input',
                            'jev_quote_not_in_source': 'service_invalid_input',
                            'jev_rate_limited': 'service_rate_limited'}.get(str(error), 'service_unavailable')
                    raise ServiceError(code) from None
                except ServiceError as error:
                    raise ServiceError(str(error) if str(error) in SAFE_SERVICE_ERRORS else 'service_unavailable') from None
                except Exception:
                    raise ServiceError('service_unavailable') from None
        return review

    def collect_reference(self, member_id, url):
        _member(member_id)
        url = apify_reference.normalize_public_url(url)
        with self._reserve(member_id, 'reference_collect'):
            try:
                return self._collect(url)
            except ServiceError as error:
                raise ServiceError(str(error) if str(error) in SAFE_SERVICE_ERRORS else 'service_unavailable') from None
            except Exception:
                raise ServiceError('service_unavailable') from None
