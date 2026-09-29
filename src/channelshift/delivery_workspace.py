"""Local intake pilot: durable sources, explicit provider jobs and human reasons.

Single OS user only. This is not member authentication or a production scheduler.
No route promotes an intake candidate to approved or runs design/build/deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .delivery_profile import STAGES

REASONS = {"missing_client_info", "contradictory_requirements", "model_error", "nonstandard_request",
           "access_approval", "quality_issue", "other"}
STAGE_IDS = {stage[0] for stage in STAGES}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, limit, required=True):
    if type(value) is not str or len(value) > limit or (required and not value.strip()):
        raise ValueError("invalid_delivery_input")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid_delivery_input") from None
    return value


class DeliveryWorkspace:
    def __init__(self, path=None, extract=None, review=None):
        from .codex_intake import extract_requirements
        from .jev_review import review_requirements
        self.extract = extract or extract_requirements
        self.review = review or review_requirements
        root = Path(os.environ.get("CHANNELSHIFT_HOME", Path.home() / ".channelshift"))
        self.path = Path(path) if path is not None else root / "delivery.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="channelshift-intake")
        self._lock = threading.Lock()
        self._active = False
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                  source TEXT NOT NULL, state TEXT NOT NULL, candidate TEXT, jev TEXT);
                CREATE TABLE IF NOT EXISTS events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                  created_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  FOREIGN KEY(project_id) REFERENCES projects(id));
            ''')
            # Do not reset unfinished jobs: another process could still be alive.
            # Manual recovery requires proving the previous worker has stopped.

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db, project_id, kind, payload):
        db.execute("INSERT INTO events(project_id,created_at,kind,payload) VALUES(?,?,?,?)",
                   (project_id, _now(), kind, json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def create(self, name, client_request):
        name, text = _text(name, 200), _text(client_request, 12000)
        source = {'id': 'SRC-001', 'text': text, 'digest': hashlib.sha256(text.encode('utf-8')).hexdigest()}
        project_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] >= 1000:
                raise ValueError('delivery_storage_limit')
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?)',
                       (project_id, name, _now(), json.dumps(source, ensure_ascii=False), 'RECEIVED', None, None))
            self._event(db, project_id, 'source_registered', {'source_id': source['id'], 'digest': source['digest']})
        return self.get(project_id)

    def list(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT id,name,created_at,state FROM projects ORDER BY created_at DESC LIMIT 1000')]

    def get(self, project_id):
        with self._connect() as db:
            # Keep the project and its job history in the same read snapshot.
            db.execute('BEGIN')
            return self._get(db, project_id)

    @staticmethod
    def _get(db, project_id):
        if type(project_id) is not str or len(project_id) != 32 or any(c not in '0123456789abcdef' for c in project_id):
            raise ValueError('invalid_delivery_project')
        row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise ValueError('delivery_project_not_found')
        result = dict(row)
        for key in ('source', 'candidate', 'jev'):
            result[key] = json.loads(result[key]) if result[key] else None
        events = [dict(event) for event in db.execute(
            'SELECT sequence,created_at,kind,payload FROM events WHERE project_id=? ORDER BY sequence', (project_id,))]
        for event in events:
            event['payload'] = json.loads(event['payload'])
        result['events'] = events
        result['interventions'] = [dict(event['payload'], created_at=event['created_at']) for event in events if event['kind'] == 'human_intervention']
        jobs = [event for event in events if event['kind'] == 'job_started']
        context = {key: result[key] for key in ('id', 'source', 'state', 'candidate', 'jev')}
        context['job_id'] = jobs[-1]['payload']['job_id'] if jobs else None
        context['format'] = 'channelshift.intervention-revision/v1'
        result['intervention_revision'] = hashlib.sha256(
            json.dumps(context, sort_keys=True, ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()
        return result

    def intervene(self, project_id, stage_id, reason, note, decision, outcome, expected_revision):
        if type(stage_id) is not str or stage_id not in STAGE_IDS or type(reason) is not str or reason not in REASONS:
            raise ValueError('invalid_delivery_input')
        if type(expected_revision) is not str or len(expected_revision) != 64 or any(c not in '0123456789abcdef' for c in expected_revision):
            raise ValueError('invalid_delivery_input')
        payload = {'stage_id': stage_id, 'reason': reason, 'note': _text(note, 2000),
                   'decision': _text(decision, 2000), 'outcome': _text(outcome, 2000, False),
                   'actor_type': 'local_operator', 'approval_granted': False}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._get(db, project_id)
            if expected_revision != current['intervention_revision']:
                raise ValueError('delivery_revision_conflict')
            payload['intervention_revision'] = expected_revision
            payload['source_digest'] = current['source']['digest']
            payload['state_at_intervention'] = current['state']
            payload['candidate_digest'] = hashlib.sha256(json.dumps(current['candidate'], sort_keys=True, ensure_ascii=True).encode()).hexdigest() if current['candidate'] else None
            jobs = [event for event in current['events'] if event['kind'] == 'job_started']
            payload['job_id'] = jobs[-1]['payload']['job_id'] if jobs else None
            count = db.execute('SELECT COUNT(*) FROM events WHERE project_id=?', (project_id,)).fetchone()[0]
            if count >= 1000:
                raise ValueError('delivery_storage_limit')
            self._event(db, project_id, 'human_intervention', payload)
        return self.get(project_id)

    def start(self, project_id, operation):
        if operation not in {'extract', 'jev'}:
            raise ValueError('invalid_delivery_input')
        with self._lock:
            if self._active:
                raise ValueError('delivery_busy')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM projects WHERE state IN ('EXTRACTING','REVIEWING') LIMIT 1").fetchone():
                    raise ValueError('delivery_recovery_required')
                project = self._get(db, project_id)
                if operation == 'jev' and not project['candidate']:
                    raise ValueError('delivery_candidate_required')
                if db.execute('SELECT COUNT(*) FROM events WHERE project_id=?', (project_id,)).fetchone()[0] >= 990:
                    raise ValueError('delivery_storage_limit')
                state = 'EXTRACTING' if operation == 'extract' else 'REVIEWING'
                db.execute('UPDATE projects SET state=? WHERE id=?', (state, project_id))
                job_id = uuid.uuid4().hex
                self._event(db, project_id, 'job_started', {'job_id': job_id, 'operation': operation, 'source_digest': project['source']['digest']})
            self._active = True
            self._jobs.submit(self._run, project, operation, job_id)
        return self.get(project_id)

    def _run(self, project, operation, job_id):
        started = time.monotonic()
        try:
            if operation == 'extract':
                result = self.extract(project['source']['text'])
                candidate, jev = json.dumps(result, ensure_ascii=False, allow_nan=False), None
            else:
                result = self.review(project['source']['text'], project['candidate']['requirements'])
                candidate = json.dumps(project['candidate'], ensure_ascii=False, allow_nan=False)
                jev = json.dumps(result, ensure_ascii=False, allow_nan=False)
            with self._connect() as db:
                db.execute('UPDATE projects SET state=?,candidate=?,jev=? WHERE id=?',
                           ('REVIEW_REQUIRED', candidate, jev, project['id']))
                self._event(db, project['id'], 'candidate_recorded' if operation == 'extract' else 'advice_recorded',
                            {'job_id': job_id, 'operation': operation, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                             'source_digest': project['source']['digest'], 'result': result})
        except Exception as error:
            from .codex_intake import CodexIntakeError, SAFE_ERROR_CODES as CODEX_CODES
            from .jev_review import JevError, SAFE_ERROR_CODES as JEV_CODES
            code = str(error) if isinstance(error, (CodexIntakeError, JevError)) and str(error) in CODEX_CODES | JEV_CODES else 'delivery_job_failed'
            with self._connect() as db:
                db.execute("UPDATE projects SET state='NEEDS_ATTENTION' WHERE id=?", (project['id'],))
                self._event(db, project['id'], 'job_failed', {'job_id': job_id, 'operation': operation,
                                                          'elapsed_ms': round((time.monotonic() - started) * 1000), 'code': code})
        finally:
            with self._lock:
                self._active = False

    def close(self):
        self._jobs.shutdown(wait=True)
