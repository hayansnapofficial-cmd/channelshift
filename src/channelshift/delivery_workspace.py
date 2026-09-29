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
MAX_INPUT = 12000
MAX_ANSWER = 2000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def _hex_digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _source_snapshot(project):
    text = project['source']['text']
    return {'text': text, 'digest': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'source_digest': project['source']['digest'], 'answer_refs': []}


def _extraction_snapshot(project):
    """Keep the latest answer for each historical candidate/question, not just Q IDs.

    Saved answers are local operator evidence, never authenticated client approval.
    Keeping historical evidence here prevents a follow-up extraction from losing
    the answers which informed an earlier candidate. No text is truncated.
    """
    latest = {}
    for answer in project['answer_history']:
        latest[(answer['candidate_revision'], answer['question_digest'])] = answer
    snapshot = _source_snapshot(project)
    for answer in latest.values():
        if not answer['answer'].strip():
            continue
        snapshot['text'] += ('\n\n[추가 답변 · 로컬 작업자 기록 · 승인 아님]\n'
                             + answer['question_id'] + ' 질문: ' + answer['question_text']
                             + '\n답변: ' + answer['answer'])
        snapshot['answer_refs'].append({key: answer[key] for key in
                                       ('candidate_revision', 'question_id', 'question_digest',
                                        'answer_revision', 'answer_digest')})
    if len(snapshot['text']) > MAX_INPUT:
        raise ValueError('delivery_input_limit')
    snapshot['digest'] = hashlib.sha256(snapshot['text'].encode('utf-8')).hexdigest()
    return snapshot


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
    def __init__(self, path=None, extract=None, review=None, collect=None):
        from .codex_intake import extract_requirements
        from .jev_review import review_requirements
        self.extract = extract or extract_requirements
        self.review = review or review_requirements
        self.collect = collect
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
        result['references'] = [dict(event['payload']['result']) for event in events
                                if event['kind'] == 'reference_recorded']
        settings = [event['payload'] for event in events if event['kind'] == 'workspace_settings_saved']
        result['workspace_settings'] = {item['section']: item['values'] for item in settings}
        result['settings_revisions'] = {'seo': _digest({'project_id': project_id, 'section': 'seo', 'values': None})}
        result['settings_revisions'].update({item['section']: item['revision'] for item in settings})
        result['consent_history'] = [dict(event['payload'], created_at=event['created_at'])
                                     for event in events if event['kind'] == 'consent_policy_recorded']
        consent = result['consent_history'][-1] if result['consent_history'] else None
        result['consent_policy'] = {key: consent[key] if consent else default for key, default in
                                    (('mode', 'undecided'), ('operator_label', ''), ('reason', ''),
                                     ('created_at', None), ('approval_granted', False), ('tracking_enabled', False))}
        result['consent_revision'] = consent['policy_revision'] if consent else _digest({
            'format': 'channelshift.consent-policy/v1', 'project_id': project_id,
            'source_digest': result['source']['digest'], 'mode': 'undecided'})
        candidates = [event for event in events if event['kind'] == 'candidate_recorded']
        candidate_event = candidates[-1] if candidates else None
        result['candidate_revision'] = (_digest({'project_id': project_id,
                                                 'job_id': candidate_event['payload']['job_id'],
                                                 'candidate': result['candidate']})
                                        if candidate_event and result['candidate'] else None)
        result['candidate_input'] = (candidate_event['payload'].get('input_snapshot', _source_snapshot(result))
                                     if candidate_event else None)
        result['answer_history'] = [dict(event['payload'], created_at=event['created_at'])
                                    for event in events if event['kind'] == 'question_answered']
        answers = {(answer['candidate_revision'], answer['question_digest']): answer
                   for answer in result['answer_history']}
        result['answer_context_digest'] = _digest([
            {key: answers[identity][key] for key in ('candidate_revision', 'question_digest', 'answer_revision')}
            for identity in sorted(answers)])
        result['question_answers'] = []
        for question in (result['candidate'] or {}).get('questions', []):
            question_digest = _digest(question)
            saved = answers.get((result['candidate_revision'], question_digest))
            initial_revision = _digest({'candidate_revision': result['candidate_revision'],
                                        'question_digest': question_digest, 'answer': None})
            result['question_answers'].append({
                'question_id': question['id'], 'question_digest': question_digest,
                'answer': saved['answer'] if saved else '',
                'answer_revision': saved['answer_revision'] if saved else initial_revision,
                'answered': bool(saved and saved['answer'].strip()), 'blocking': question['blocking']})
        result['answer_summary'] = {'total': len(result['question_answers']),
                                    'answered': sum(item['answered'] for item in result['question_answers']),
                                    'blocking_unanswered': sum(item['blocking'] and not item['answered']
                                                               for item in result['question_answers'])}
        result['interventions'] = [dict(event['payload'], created_at=event['created_at']) for event in events if event['kind'] == 'human_intervention']
        jobs = [event for event in events if event['kind'] == 'job_started']
        context = {key: result[key] for key in ('id', 'source', 'state', 'candidate', 'jev',
                                               'answer_context_digest', 'consent_revision')}
        context['job_id'] = jobs[-1]['payload']['job_id'] if jobs else None
        context['format'] = 'channelshift.intervention-revision/v1'
        # The review snapshot binds the evidence. The write revision additionally
        # binds stage navigation so an old tab cannot undo a newer navigation.
        result['review_context_revision'] = _digest(context)
        navigation = [event for event in events if event['kind'] in
                      {'requirements_review_requested', 'intake_reopened'}]
        last_navigation = navigation[-1] if navigation else None
        result['review_history'] = [dict(event['payload'], created_at=event['created_at'])
                                    for event in navigation if event['kind'] == 'requirements_review_requested']
        active_review = bool(last_navigation and last_navigation['kind'] == 'requirements_review_requested'
                             and last_navigation['payload']['review_context_revision'] == result['review_context_revision'])
        result['workflow_stage'] = 'requirements_review' if active_review else 'intake'
        result['requirements_review'] = result['review_history'][-1] if active_review else None
        if last_navigation:
            context['workflow_sequence'] = last_navigation['sequence']
        reference_events = [event for event in events if event['kind'] in
                            {'reference_started', 'reference_recorded', 'reference_failed'}]
        if reference_events:
            # A retry of an old collection request must not launch another paid
            # call. Keep this out of the client-evidence review snapshot.
            context['reference_sequence'] = reference_events[-1]['sequence']
        result['intervention_revision'] = _digest(context)
        return result

    def advance_to_review(self, project_id, expected_revision):
        """Freeze the saved intake evidence for a local requirements review."""
        if not _hex_digest(expected_revision):
            raise ValueError('invalid_delivery_input')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._get(db, project_id)
            if expected_revision != current['intervention_revision']:
                raise ValueError('delivery_revision_conflict')
            if current['state'] in {'EXTRACTING', 'REVIEWING', 'COLLECTING_REFERENCE'}:
                raise ValueError('delivery_busy')
            if not current['candidate']:
                raise ValueError('delivery_candidate_required')
            if current['answer_summary']['blocking_unanswered']:
                raise ValueError('delivery_answers_required')
            if len(current['events']) >= 1000:
                raise ValueError('delivery_storage_limit')
            if current['workflow_stage'] != 'requirements_review':
                questions = {item['id']: item for item in current['candidate'].get('questions', [])}
                payload = {'workflow_stage': 'requirements_review', 'actor_type': 'local_operator',
                           'approval_granted': False, 'intervention_revision': expected_revision,
                           'review_context_revision': current['review_context_revision'],
                           'candidate_revision': current['candidate_revision'],
                           'source_digest': current['source']['digest'],
                           'answer_context_digest': current['answer_context_digest'],
                           'consent_revision': current['consent_revision'],
                           'source': current['source'], 'candidate': current['candidate'],
                           'candidate_input': current['candidate_input'],
                           'answers': [dict(item, question_text=questions[item['question_id']]['text'])
                                       for item in current['question_answers']]}
                self._event(db, project_id, 'requirements_review_requested', payload)
            result = self._get(db, project_id)
        return result

    def return_to_intake(self, project_id, expected_revision):
        if not _hex_digest(expected_revision):
            raise ValueError('invalid_delivery_input')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._get(db, project_id)
            if expected_revision != current['intervention_revision']:
                raise ValueError('delivery_revision_conflict')
            if current['state'] in {'EXTRACTING', 'REVIEWING', 'COLLECTING_REFERENCE'}:
                raise ValueError('delivery_busy')
            if len(current['events']) >= 1000:
                raise ValueError('delivery_storage_limit')
            self._event(db, project_id, 'intake_reopened', {
                'workflow_stage': 'intake', 'intervention_revision': expected_revision,
                'review_context_revision': current['review_context_revision'],
                'candidate_revision': current['candidate_revision'], 'actor_type': 'local_operator',
                'approval_granted': False})
            result = self._get(db, project_id)
        return result

    def consent(self, project_id, mode, operator_label, reason, expected_revision):
        """Record a local product preference, never legal consent or permission."""
        if type(mode) is not str or mode not in {'undecided', 'required', 'not_required'} or not _hex_digest(expected_revision):
            raise ValueError('invalid_delivery_input')
        payload = {'mode': mode, 'operator_label': _text(operator_label, 100), 'reason': _text(reason, 2000),
                   'actor_type': 'local_operator', 'approval_granted': False, 'tracking_enabled': False,
                   'previous_revision': expected_revision}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._get(db, project_id)
            if expected_revision != current['consent_revision']:
                raise ValueError('delivery_revision_conflict')
            if len(current['events']) >= 1000:
                raise ValueError('delivery_storage_limit')
            payload['source_digest'] = current['source']['digest']
            payload['policy_revision'] = _digest(dict(payload, project_id=project_id))
            self._event(db, project_id, 'consent_policy_recorded', payload)
        return self.get(project_id)

    def answer(self, project_id, candidate_revision, question_id, question_digest, answer, expected_revision,
               expected_context_revision=None):
        """Append a revision-bound answer locally; no provider call or approval."""
        if not all(_hex_digest(value) for value in (candidate_revision, question_digest, expected_revision)):
            raise ValueError('invalid_delivery_input')
        if expected_context_revision is not None and not _hex_digest(expected_context_revision):
            raise ValueError('invalid_delivery_input')
        _text(question_id, 64)
        answer = _text(answer, MAX_ANSWER, False)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._get(db, project_id)
            if expected_context_revision is not None and expected_context_revision != current['intervention_revision']:
                raise ValueError('delivery_revision_conflict')
            if candidate_revision != current['candidate_revision']:
                raise ValueError('delivery_revision_conflict')
            selected = next((item for item in current['question_answers']
                             if item['question_id'] == question_id and item['question_digest'] == question_digest), None)
            if selected is None or selected['answer_revision'] != expected_revision:
                raise ValueError('delivery_revision_conflict')
            if current['state'] in {'EXTRACTING', 'REVIEWING', 'COLLECTING_REFERENCE'}:
                raise ValueError('delivery_busy')
            if len(current['events']) >= 1000:
                raise ValueError('delivery_storage_limit')
            question = next(item for item in current['candidate']['questions'] if item['id'] == question_id)
            payload = {'candidate_revision': candidate_revision, 'candidate_digest': _digest(current['candidate']),
                       'source_digest': current['source']['digest'], 'question_id': question_id,
                       'question_digest': question_digest, 'question_text': question['text'], 'answer': answer,
                       'answer_digest': hashlib.sha256(answer.encode('utf-8')).hexdigest(),
                       'previous_revision': expected_revision, 'actor_type': 'local_operator', 'approval_granted': False}
            payload['answer_revision'] = _digest(payload)
            self._event(db, project_id, 'question_answered', payload)
            result = self._get(db, project_id)
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
            payload['answer_context_digest'] = current['answer_context_digest']
            payload['consent_revision'] = current['consent_revision']
            payload['state_at_intervention'] = current['state']
            payload['candidate_digest'] = hashlib.sha256(json.dumps(current['candidate'], sort_keys=True, ensure_ascii=True).encode()).hexdigest() if current['candidate'] else None
            jobs = [event for event in current['events'] if event['kind'] == 'job_started']
            payload['job_id'] = jobs[-1]['payload']['job_id'] if jobs else None
            count = db.execute('SELECT COUNT(*) FROM events WHERE project_id=?', (project_id,)).fetchone()[0]
            if count >= 1000:
                raise ValueError('delivery_storage_limit')
            self._event(db, project_id, 'human_intervention', payload)
        return self.get(project_id)

    def save_settings(self, project_id, section, values, expected_revision):
        from .workbench import validate_settings
        values = validate_settings(section, values)
        if not _hex_digest(expected_revision):
            raise ValueError('invalid_delivery_input')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._get(db, project_id)
            if project['settings_revisions'][section] != expected_revision:
                raise ValueError('delivery_revision_conflict')
            if len(project['events']) >= 1000:
                raise ValueError('delivery_storage_limit')
            payload = {'section': section, 'values': values, 'previous_revision': expected_revision,
                       'approved': False, 'applied_to_site': False}
            payload['revision'] = _digest(payload)
            self._event(db, project_id, 'workspace_settings_saved', payload)
            return self._get(db, project_id)

    def start(self, project_id, operation):
        if operation not in {'extract', 'jev'}:
            raise ValueError('invalid_delivery_input')
        with self._lock:
            if self._active:
                raise ValueError('delivery_busy')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM projects WHERE state IN ('EXTRACTING','REVIEWING','COLLECTING_REFERENCE') LIMIT 1").fetchone():
                    raise ValueError('delivery_recovery_required')
                project = self._get(db, project_id)
                if operation == 'jev' and not project['candidate']:
                    raise ValueError('delivery_candidate_required')
                if db.execute('SELECT COUNT(*) FROM events WHERE project_id=?', (project_id,)).fetchone()[0] >= 990:
                    raise ValueError('delivery_storage_limit')
                # Freeze the exact input in this transaction before a worker starts.
                snapshot = _extraction_snapshot(project) if operation == 'extract' else project['candidate_input']
                if snapshot is None or len(snapshot['text']) > MAX_INPUT:
                    raise ValueError('delivery_input_limit')
                project['job_input'] = snapshot
                state = 'EXTRACTING' if operation == 'extract' else 'REVIEWING'
                db.execute('UPDATE projects SET state=? WHERE id=?', (state, project_id))
                job_id = uuid.uuid4().hex
                self._event(db, project_id, 'job_started', {'job_id': job_id, 'operation': operation,
                            'source_digest': project['source']['digest'], 'input_snapshot': snapshot})
            self._active = True
            self._jobs.submit(self._run, project, operation, job_id)
        return self.get(project_id)

    def collect_reference(self, project_id, url, expected_revision):
        """Collect one explicitly requested reference without changing client evidence."""
        url = _text(url, 2048)
        if not _hex_digest(expected_revision):
            raise ValueError('invalid_delivery_input')
        if self.collect is None:
            raise ValueError('service_not_configured')
        with self._lock:
            if self._active:
                raise ValueError('delivery_busy')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM projects WHERE state IN ('EXTRACTING','REVIEWING','COLLECTING_REFERENCE') LIMIT 1").fetchone():
                    raise ValueError('delivery_recovery_required')
                project = self._get(db, project_id)
                if project['intervention_revision'] != expected_revision:
                    raise ValueError('delivery_revision_conflict')
                if len(project['events']) >= 990 or len(project['references']) >= 20:
                    raise ValueError('delivery_storage_limit')
                job_id = uuid.uuid4().hex
                db.execute("UPDATE projects SET state='COLLECTING_REFERENCE' WHERE id=?", (project_id,))
                self._event(db, project_id, 'reference_started', {'job_id': job_id, 'url': url})
            self._active = True
            self._jobs.submit(self._run_reference, project, url, job_id)
        return self.get(project_id)

    def _run_reference(self, project, url, job_id):
        from .shared_services import ServiceError, SAFE_ERROR_CODES as SERVICE_CODES
        started = time.monotonic()
        try:
            result = self.collect(url)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode('utf-8')) > 131072:
                raise ServiceError('reference_too_large')
            payload = {'job_id': job_id, 'result': result,
                       'elapsed_ms': round((time.monotonic() - started) * 1000)}
            kind = 'reference_recorded'
        except Exception as error:
            code = str(error) if isinstance(error, ServiceError) and str(error) in SERVICE_CODES else 'service_unavailable'
            payload = {'job_id': job_id, 'code': code,
                       'elapsed_ms': round((time.monotonic() - started) * 1000)}
            kind = 'reference_failed'
        try:
            with self._connect() as db:
                db.execute('UPDATE projects SET state=? WHERE id=?', (project['state'], project['id']))
                self._event(db, project['id'], kind, payload)
        finally:
            with self._lock:
                self._active = False

    def _run(self, project, operation, job_id):
        started = time.monotonic()
        try:
            if operation == 'extract':
                result = self.extract(project['job_input']['text'])
                candidate, jev = json.dumps(result, ensure_ascii=False, allow_nan=False), None
                if len(candidate.encode('utf-8')) > 131072:
                    from .codex_intake import CodexIntakeError
                    raise CodexIntakeError('codex_invalid_output')
            else:
                result = self.review(project['job_input']['text'], project['candidate']['requirements'])
                candidate = json.dumps(project['candidate'], ensure_ascii=False, allow_nan=False)
                jev = json.dumps(result, ensure_ascii=False, allow_nan=False)
            with self._connect() as db:
                db.execute('UPDATE projects SET state=?,candidate=?,jev=? WHERE id=?',
                           ('REVIEW_REQUIRED', candidate, jev, project['id']))
                self._event(db, project['id'], 'candidate_recorded' if operation == 'extract' else 'advice_recorded',
                            {'job_id': job_id, 'operation': operation, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                             'source_digest': project['source']['digest'], 'input_snapshot': project['job_input'],
                             'result': result})
        except Exception as error:
            from .codex_intake import CodexIntakeError, SAFE_ERROR_CODES as CODEX_CODES
            from .jev_review import JevError, SAFE_ERROR_CODES as JEV_CODES
            from .shared_services import ServiceError, SAFE_ERROR_CODES as SERVICE_CODES
            from .member_codex import MemberCodexError, SAFE_ERROR_CODES as MEMBER_CODEX_CODES
            code = str(error) if isinstance(error, (CodexIntakeError, MemberCodexError, JevError, ServiceError)) and str(error) in CODEX_CODES | MEMBER_CODEX_CODES | JEV_CODES | SERVICE_CODES else 'delivery_job_failed'
            with self._connect() as db:
                db.execute("UPDATE projects SET state='NEEDS_ATTENTION' WHERE id=?", (project['id'],))
                self._event(db, project['id'], 'job_failed', {'job_id': job_id, 'operation': operation,
                                                          'elapsed_ms': round((time.monotonic() - started) * 1000), 'code': code})
        finally:
            with self._lock:
                self._active = False

    def close(self):
        self._jobs.shutdown(wait=True)
