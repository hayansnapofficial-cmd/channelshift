"""Member-scoped, revision-bound website production workflow.

Provider code is stored as text, never executed here. Only deterministic SQLite
DDL is exercised by the artifact harness. Reviews are local operator decisions.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .delivery_workspace import BUSY_STATES, _digest, _extraction_snapshot, _now
from . import site_obligations

STAGES = (('requirements', '요구사양'), ('wireframe', '화면 설계'), ('erd', 'ERD'),
          ('api', 'API 계약'), ('database', '데이터베이스'), ('backend', '백엔드'),
          ('frontend', '프론트'), ('delivery', '검수·납품'))
ORDER = [key for key, _ in STAGES]
GENERATED = {'wireframe', 'api', 'backend', 'frontend'}
VALIDATION_PROFILE = 'channelshift.pipeline-contract/v2'
MAX_EVENTS = 1000
ERRORS = {'pipeline_revision_conflict', 'pipeline_stage_locked', 'pipeline_busy',
          'pipeline_review_note_required', 'pipeline_analysis_required',
          'pipeline_artifact_required', 'pipeline_storage_limit', 'pipeline_job_failed',
          'pipeline_artifact_stale', 'invalid_pipeline_input', 'invalid_pipeline_artifact',
          'pipeline_database_check_failed', 'site_obligations_incomplete',
           'invalid_site_obligations', 'pipeline_recovery_required',
           'invalid_feature_decisions', 'feature_guidance_required', 'feature_guidance_unavailable'}


def _empty():
    return {'site_type': 'service', 'obligations': site_obligations.catalog()['empty_values'],
            'confirmation': None, 'artifacts': {}, 'approvals': {}, 'job': None}


def _artifact(files, notes=None, checks=None):
    result = {'files': files, 'notes': notes or [], 'checks': checks or {}}
    result['digest'] = _digest(result)
    return result


def _process_identity(pid):
    """Return OS lifetime identity, never infer death from an access failure."""
    if type(pid) is not int or pid <= 0:
        return 'unknown', None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            return ('dead', None) if ctypes.get_last_error() == 87 else ('unknown', None)
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return 'dead', None
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(system), ctypes.byref(user)):
                return 'unknown', None
            return 'alive', str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    proc = Path('/proc')
    if proc.is_dir():
        try:
            fields = (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return 'dead', None
            return 'alive', fields[19]
        except FileNotFoundError:
            return 'dead', None
        except (OSError, IndexError):
            return 'unknown', None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return 'dead', None
    except OSError:
        return 'unknown', None
    # On platforms without a creation identity a reused live PID is left alone.
    return 'alive', str(pid)


def _owner():
    state, identity = _process_identity(os.getpid())
    if state != 'alive' or identity is None:
        raise ValueError('pipeline_recovery_required')
    return {'pid': os.getpid(), 'identity': identity}


def _owner_dead(owner):
    if type(owner) is not dict or set(owner) != {'pid', 'identity'} or type(owner['identity']) is not str:
        return False
    state, identity = _process_identity(owner['pid'])
    return state == 'dead' or (state == 'alive' and identity != owner['identity'])


class PipelineWorkspace:
    def __init__(self, delivery, path=None, generate=None):
        self.delivery = delivery
        self.path = Path(path or delivery.path.with_name('pipeline.sqlite3'))
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('unsafe_storage')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.generate = generate
        self._lock = threading.RLock()
        self._jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix='channelshift-pipeline')
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pipeline_projects (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pipeline_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            ''')
        self._recover_dead_jobs()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _read(db, project_id):
        row = db.execute('SELECT state,version FROM pipeline_projects WHERE id=?', (project_id,)).fetchone()
        return (json.loads(row['state']), row['version']) if row else (_empty(), 0)

    @staticmethod
    def _write(db, project_id, state, version, kind, payload):
        terminal = kind in {'artifact_generated', 'generation_failed', 'generation_recovered'}
        count = db.execute('SELECT COUNT(*) FROM pipeline_events WHERE project_id=?', (project_id,)).fetchone()[0]
        reserve = int(bool(state.get('job') and state['job']['state'] == 'running'))
        if not terminal and count >= MAX_EVENTS - reserve:
            raise ValueError('pipeline_storage_limit')
        db.execute('INSERT INTO pipeline_projects VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,version=excluded.version',
                   (project_id, json.dumps(state, ensure_ascii=False, allow_nan=False), version + 1))
        db.execute('INSERT INTO pipeline_events(project_id,at,kind,payload) VALUES(?,?,?,?)',
                   (project_id, _now(), kind, json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def _recover_dead_jobs(self):
        with self._lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT id,state,version FROM pipeline_projects').fetchall():
                state = json.loads(row['state'])
                job = state.get('job')
                if not job or job.get('state') != 'running' or not _owner_dead(job.get('owner')):
                    continue
                if job['stage'] == 'erd':
                    # The delivery method additionally checks its exact child job
                    # lineage; an unrelated legacy job cannot be reset here.
                    self.delivery._recover_pipeline_erd(row['id'], job['id'])
                state['job'] = dict(job, state='failed', error='pipeline_recovery_required')
                self._write(db, row['id'], state, row['version'], 'generation_recovered',
                            {'stage': job['stage'], 'job_id': job['id'], 'error': 'pipeline_recovery_required'})

    @staticmethod
    def _validation_dependencies(state, dependencies):
        result = copy.deepcopy(dependencies)
        confirmation = state.get('confirmation')
        if confirmation:
            result['confirmed_requirement_ids'] = [item['id'] for item in
                confirmation['snapshot']['candidate']['requirements'] if item.get('origin') == 'client']
        return result

    def create(self, name, client_request, site_type, *, guided=True):
        if site_type not in site_obligations.catalog()['site_types']:
            raise ValueError('invalid_pipeline_input')
        project = self.delivery.create(name, client_request, guided=guided)
        with self._lock, self._db() as db:
            state = _empty()
            state['site_type'] = site_type
            self._write(db, project['id'], state, 0, 'created', {'site_type': site_type})
        return self.get(project['id'])

    @staticmethod
    def _erd(project):
        if not project.get('erd_current') or not project.get('erd_draft'):
            return None
        draft = project['erd_draft']
        return _artifact([
            {'path': 'erd/schema.json', 'content': json.dumps(draft['result']['schema'], ensure_ascii=False, indent=2)},
            {'path': 'erd/traceability.json', 'content': json.dumps({
                'traceability': draft['result']['traceability'],
                'unmapped_requirements': draft['result']['unmapped_requirements'],
                'traceability_current': draft['traceability_current']}, ensure_ascii=False, indent=2)}],
            draft['result']['notes'], {'schema_valid': True, 'database_executed': False,
                                     'draft_revision': draft['revision'],
                                     'wireframe_digest': draft.get('wireframe_digest'),
                                     'traceability_current': draft['traceability_current']})

    def _view(self, db, project_id):
        project = self.delivery.get(project_id)
        state, version = self._read(db, project_id)
        confirmation = state['confirmation']
        try:
            analysis_current = bool(project['candidate_input'] and
                                    project['candidate_input']['digest'] == _extraction_snapshot(project)['digest'])
            if project['guidance']['enabled']:
                analysis_current = bool(analysis_current and
                    project['candidate_input'].get('feature_revision') == project['feature_revision'])
            if project['production_guides']:
                analysis_current = bool(analysis_current and project['candidate_input'].get('production_guides')
                                        == project['production_guides'])
        except ValueError:
            analysis_current = False
        confirmed = bool(analysis_current and confirmation and project['requirements_review'] and
                         confirmation['review_digest'] == _digest(project['requirements_review']))
        revision = _digest({'version': version, 'state': state, 'intake': project['intervention_revision']})
        dependencies = {}
        stages = []
        previous_ready = confirmed
        busy = project['state'] in BUSY_STATES or bool(state['job'] and state['job']['state'] == 'running')
        for stage, label in STAGES:
            if stage == 'requirements':
                stages.append({'id': stage, 'label': label, 'state': 'approved' if confirmed else 'ready',
                               'artifact': None, 'can_generate': False, 'can_approve': False})
                continue
            input_basis = {'spec': confirmation['digest'] if confirmed else None,
                           'validation_profile': VALIDATION_PROFILE,
                           'dependencies': {key: value['digest'] for key, value in dependencies.items()},
                           'obligations': state['obligations'] if stage == 'delivery' else None}
            if project['production_guides']:
                input_basis['production_guides'] = project['production_guides']
            input_key = _digest(input_basis)
            record = state['artifacts'].get(stage)
            artifact = copy.deepcopy(record['artifact']) if record else None
            if stage == 'erd' and record:
                artifact = self._erd(project)
            current = bool(previous_ready and record and record['input_key'] == input_key and artifact)
            if stage == 'erd' and current:
                # A standalone draft cannot inherit a pipeline review just by
                # returning the same schema bytes without its approved design.
                current = bool(project['erd_draft'].get('wireframe_digest')
                               == dependencies.get('wireframe', {}).get('digest'))
            approval = state['approvals'].get(stage)
            approved = bool(current and approval and approval['digest'] == artifact['digest']
                            and approval['input_key'] == input_key)
            running = bool(state['job'] and state['job']['state'] == 'running' and state['job']['stage'] == stage)
            stage_state = ('running' if running else 'approved' if approved else 'generated' if current
                           else 'stale' if record else 'ready' if previous_ready else 'locked')
            stages.append({'id': stage, 'label': label, 'state': stage_state, 'artifact': artifact,
                           'can_generate': bool(previous_ready and not busy),
                           'can_edit': bool(stage in GENERATED and artifact and previous_ready and not busy),
                           'can_approve': bool(current and not approved and not busy), 'input_key': input_key,
                           'review': approval if approved else None})
            if artifact:
                dependencies[stage] = artifact
            previous_ready = approved
        history = []
        for row in db.execute('SELECT seq,at,kind,payload FROM pipeline_events WHERE project_id=? ORDER BY seq DESC LIMIT 100', (project_id,)):
            payload = json.loads(row['payload'])
            history.append({'id': 'pipeline:' + str(row['seq']), 'at': row['at'], 'kind': row['kind'],
                            'stage': payload.get('stage'), 'note': payload.get('note', '')})
        for event in project['events']:
            if event['kind'] in {'erd_edited', 'feature_decisions_saved'}:
                history.append({'id': 'delivery:' + str(event['sequence']), 'at': event['created_at'],
                                'kind': event['kind'], 'stage': 'erd' if event['kind'] == 'erd_edited' else 'requirements',
                                'note': event['payload'].get('note', '')})
        history.sort(key=lambda event: (event['at'], event['id'].partition(':')[0],
                                        int(event['id'].partition(':')[2])), reverse=True)
        result = {'ok': True, 'project': project, 'pipeline': {
            'revision': revision, 'site_type': state['site_type'], 'confirmed': confirmed,
            'analysis_current': analysis_current,
            'guidance': copy.deepcopy(project['guidance']),
            'production_guides': copy.deepcopy(project['production_guides']),
            'job': state['job'], 'stages': stages,
            'obligations': {'values': state['obligations'], 'assessment': site_obligations.assess(state['obligations'])},
            'ready_for_delivery': stages[-1]['state'] == 'approved', 'history': history[:100],
            'validation_profile': VALIDATION_PROFILE,
            'execution': {'database': 'sqlite', 'application_executed': False, 'deployed': False}}}
        result['pipeline']['advice'] = []
        result['pipeline']['advice_job'] = copy.deepcopy(project['advice_job'])
        for advice in project['workflow_advice']:
            from .workflow_advice import build_context
            try:
                _, _, basis = build_context(result, advice['stage'])
                current = basis == advice['basis_digest']
            except ValueError:
                current = False
            result['pipeline']['advice'].append(dict(advice, current=current))
        return result

    def get(self, project_id):
        self._recover_dead_jobs()
        with self._lock, self._db() as db:
            db.execute('BEGIN')
            return self._view(db, project_id)

    def traceability(self, project_id):
        from .pipeline_traceability import build_traceability
        return build_traceability(self.get(project_id))

    def _check(self, db, project_id, expected_revision):
        view = self._view(db, project_id)
        if type(expected_revision) is not str or view['pipeline']['revision'] != expected_revision:
            raise ValueError('pipeline_revision_conflict')
        return view

    def action(self, project_id, expected_revision, action, payload):
        if type(payload) is not dict or type(action) is not str:
            raise ValueError('invalid_pipeline_input')
        self._recover_dead_jobs()
        with self._lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            view = self._check(db, project_id, expected_revision)
            state, version = self._read(db, project_id)
            project = view['project']
            busy = project['state'] in BUSY_STATES or bool(state['job'] and state['job']['state'] == 'running')
            if busy and action != 'save_obligations':
                raise ValueError('pipeline_busy')
            # Reject before delivery-side writes or provider scheduling: those
            # operations live in a separate database and cannot roll back here.
            reserved = int(action == 'generate' or bool(state['job'] and state['job']['state'] == 'running'))
            if db.execute('SELECT COUNT(*) FROM pipeline_events WHERE project_id=?', (project_id,)).fetchone()[0] >= MAX_EVENTS - reserved:
                raise ValueError('pipeline_storage_limit')
            if action == 'save_obligations' and set(payload) == {'values'}:
                state['obligations'] = site_obligations.validate(payload['values'])
                self._write(db, project_id, state, version, 'obligations_saved', {})
            elif action == 'add_request' and set(payload) == {'text'}:
                self.delivery.add_request(project_id, payload['text'], project['intervention_revision'])
                state['confirmation'] = None
                self._write(db, project_id, state, version, 'client_request_added', {'stage': 'requirements'})
            elif action == 'save_features' and set(payload) == {'decisions'}:
                updated = self.delivery.save_features(project_id, payload['decisions'], project['intervention_revision'])
                if updated['feature_revision'] != project['feature_revision']:
                    state['confirmation'] = None
                    self._write(db, project_id, state, version, 'features_saved', {'stage': 'requirements'})
            elif action == 'review' and not payload:
                if not view['pipeline']['analysis_current']:
                    raise ValueError('pipeline_analysis_required')
                self.delivery.start(project_id, 'review')
                self._write(db, project_id, state, version, 'requirements_advice_requested', {'stage': 'requirements'})
            elif action == 'collect_reference' and set(payload) == {'url'}:
                self.delivery.collect_reference(project_id, payload['url'], project['intervention_revision'])
                self._write(db, project_id, state, version, 'reference_requested', {'stage': 'requirements'})
            elif action == 'advise' and set(payload) == {'stage'}:
                from .workflow_advice import build_context
                provider_stage, context, basis = build_context(view, payload['stage'])
                self.delivery.advise(project_id, payload['stage'], context, project['intervention_revision'],
                                     basis_digest=basis, provider_stage=provider_stage)
                self._write(db, project_id, state, version, 'workflow_advice_requested',
                            {'stage': payload['stage']})
            elif action in {'analyze', 'save_answers'} and set(payload) == {'answers'}:
                answers = payload['answers']
                if type(answers) is not list or len(answers) > 30:
                    raise ValueError('invalid_pipeline_input')
                matched = {}
                current_questions = {q['question_id']: q for q in project['question_answers']}
                # Validate the whole batch before any evidence write.
                for item in answers:
                    if type(item) is not dict or set(item) != {'question_id', 'question_digest', 'answer'}:
                        raise ValueError('invalid_pipeline_input')
                    qid = item['question_id']
                    if type(qid) is not str or qid in matched or qid not in current_questions:
                        raise ValueError('invalid_pipeline_input')
                    if item['question_digest'] != current_questions[qid]['question_digest']:
                        raise ValueError('pipeline_revision_conflict')
                    answer = item['answer']
                    if type(answer) is not str or len(answer) > 2000 or any(0xD800 <= ord(c) <= 0xDFFF for c in answer):
                        raise ValueError('invalid_pipeline_input')
                    matched[qid] = item
                for qid, item in matched.items():
                    selected = next(q for q in project['question_answers'] if q['question_id'] == qid)
                    if selected['answer'] != item['answer']:
                        project = self.delivery.answer(project_id, project['candidate_revision'], qid,
                            item['question_digest'], item['answer'], selected['answer_revision'], project['intervention_revision'])
                if action == 'analyze':
                    self.delivery.start(project_id, 'extract')
                state['confirmation'] = None
                self._write(db, project_id, state, version, action, {'stage': 'requirements'})
            elif action == 'confirm' and not payload:
                if project['guidance']['unresolved']:
                    raise ValueError('feature_guidance_required')
                if not project['candidate'] or project['answer_summary']['blocking_unanswered']:
                    raise ValueError('delivery_answers_required')
                if not view['pipeline']['analysis_current']:
                    raise ValueError('pipeline_analysis_required')
                if not any(row.get('origin') == 'client' for row in project['candidate']['requirements']):
                    raise ValueError('delivery_client_requirements_required')
                project = self.delivery.advance_to_review(project_id, project['intervention_revision'])
                review = project['requirements_review']
                state['confirmation'] = {'review_digest': _digest(review), 'digest': _digest(review),
                                         'snapshot': review, 'at': _now(), 'actor_type': 'member_operator'}
                self._write(db, project_id, state, version, 'requirements_confirmed', {'stage': 'requirements'})
            elif action == 'return' and not payload:
                self.delivery.return_to_intake(project_id, project['intervention_revision'])
                state['confirmation'] = None
                self._write(db, project_id, state, version, 'requirements_reopened', {'stage': 'requirements'})
            elif action == 'generate' and set(payload) == {'stage'}:
                stage = payload['stage']
                selected = next((s for s in view['pipeline']['stages'] if s['id'] == stage), None)
                if not selected or not selected['can_generate']:
                    raise ValueError('pipeline_stage_locked')
                if stage == 'delivery' and not view['pipeline']['obligations']['assessment']['ready']:
                    raise ValueError('site_obligations_incomplete')
                if any(json.loads(row['state']).get('job', {}).get('state') == 'running'
                       for row in db.execute("SELECT state FROM pipeline_projects WHERE json_type(state,'$.job')='object'")):
                    raise ValueError('pipeline_busy')
                job = {'id': uuid.uuid4().hex, 'stage': stage, 'state': 'running', 'error': None,
                       'input_key': selected['input_key'], 'at': _now(), 'owner': _owner()}
                state['job'] = job
                self._write(db, project_id, state, version, 'generation_started', {'stage': stage, 'job_id': job['id']})
                # Commit the reservation before the background worker reads it.
                db.commit()
                try:
                    self._jobs.submit(self._run, project_id, copy.deepcopy(state), copy.deepcopy(view), job)
                except RuntimeError:
                    db.execute('BEGIN IMMEDIATE')
                    current, current_version = self._read(db, project_id)
                    if current['job'] and current['job']['id'] == job['id'] and current['job']['state'] == 'running':
                        current['job'] = dict(job, state='failed', error='pipeline_job_failed')
                        self._write(db, project_id, current, current_version, 'generation_failed',
                                    {'stage': stage, 'job_id': job['id'], 'error': 'pipeline_job_failed'})
            elif action == 'approve' and set(payload) == {'stage', 'note'}:
                stage, note = payload['stage'], payload['note']
                selected = next((s for s in view['pipeline']['stages'] if s['id'] == stage), None)
                if not selected or not selected['can_approve']:
                    raise ValueError('pipeline_stage_locked')
                if type(note) is not str or not note.strip() or len(note) > 2000:
                    raise ValueError('pipeline_review_note_required')
                state['approvals'][stage] = {'digest': selected['artifact']['digest'],
                    'input_key': selected['input_key'], 'note': note.strip(), 'at': _now(), 'actor_type': 'member_operator'}
                self._write(db, project_id, state, version, 'artifact_reviewed', dict(state['approvals'][stage], stage=stage))
            elif action == 'edit' and set(payload) == {'stage', 'files', 'notes', 'note'}:
                stage, note = payload['stage'], payload['note']
                selected = next((s for s in view['pipeline']['stages'] if s['id'] == stage), None)
                if not selected or not selected.get('can_edit'):
                    raise ValueError('pipeline_stage_locked')
                if type(note) is not str or not note.strip() or len(note) > 2000:
                    raise ValueError('pipeline_review_note_required')
                from .codex_pipeline import validate_stage as validate_output
                from .pipeline_artifacts import validate_stage
                result = validate_output({'files': payload['files'], 'notes': payload['notes']}, stage)
                dependencies = {s['id']: s['artifact'] for s in view['pipeline']['stages']
                                if ORDER.index(s['id']) < ORDER.index(stage) and s['artifact']}
                checks = validate_stage(stage, result['files'], self._validation_dependencies(state, dependencies))
                artifact = _artifact(result['files'], result['notes'], checks)
                state['artifacts'][stage] = {'input_key': selected['input_key'], 'artifact': artifact}
                state['approvals'].pop(stage, None)
                self._write(db, project_id, state, version, 'artifact_edited',
                            {'stage': stage, 'note': note.strip(), 'artifact': artifact})
            else:
                raise ValueError('invalid_pipeline_input')
        return self.get(project_id)

    def _run(self, project_id, state, view, job):
        from .pipeline_artifacts import build_database, validate_stage
        stage = job['stage']
        try:
            dependencies = {s['id']: s['artifact'] for s in view['pipeline']['stages']
                            if ORDER.index(s['id']) < ORDER.index(stage) and s['artifact']}
            if stage == 'erd':
                project = self.delivery.get(project_id)
                project = self.delivery.generate_erd(project_id, 'sqlite', project['intervention_revision'],
                                                     pipeline_job_id=job['id'],
                                                     wireframe_context=copy.deepcopy(dependencies['wireframe']))
                child = next((event['payload']['job_id'] for event in reversed(project['events'])
                              if event['kind'] == 'erd_started'
                              and event['payload'].get('pipeline_job_id') == job['id']), None)
                if not child:
                    raise ValueError('pipeline_job_failed')
                deadline = time.monotonic() + 210
                while True:
                    project = self.delivery.get(project_id)
                    if project['state'] != 'DESIGNING_ERD':
                        break
                    if time.monotonic() > deadline:
                        raise ValueError('pipeline_job_failed')
                    time.sleep(.1)
                # Do not replace a failed generation with an older draft.
                terminal = next((e for e in reversed(project['events'])
                                 if e['kind'] in {'erd_recorded', 'erd_failed'}
                                 and e['payload'].get('job_id') == child), None)
                if not terminal or terminal['kind'] != 'erd_recorded':
                    raise ValueError('pipeline_job_failed')
                artifact = self._erd(project)
                if not artifact or project['erd_draft']['generation_id'] != child:
                    raise ValueError('pipeline_artifact_stale')
            elif stage == 'database':
                schema = json.loads(next(f['content'] for f in dependencies['erd']['files'] if f['path'] == 'erd/schema.json'))
                result = build_database(schema)
                artifact = _artifact(result['files'], result.get('notes'), result['checks'])
            elif stage == 'delivery':
                # A summary of evidence, explicitly not an executed app test.
                report = {'format': 'channelshift.delivery-review/v1', 'checks': {
                    key: value['checks'] for key, value in dependencies.items()},
                    'obligations': site_obligations.assess(state['obligations']),
                    'application_executed': False, 'deployment_performed': False,
                    'remaining_reviews': ['실제 앱 실행 및 고객 인수 검사', '운영 환경의 보안·권한 검사', '정책 내용 및 실제 사업자정보 확인']}
                from .pipeline_artifacts import bundle
                bundle(view['project']['name'], dependencies, state['obligations'], evidence={
                    'requirements': state['confirmation']['snapshot'], 'reviews': state['approvals'], 'delivery': report})
                artifact = _artifact([{'path': 'review/delivery.json', 'content': json.dumps(report, ensure_ascii=False, indent=2)}],
                    report['remaining_reviews'], {'artifact_bundle_checked': True, 'application_executed': False})
            else:
                generate = self.generate
                if generate is None:
                    from .codex_pipeline import generate_stage
                    generate = generate_stage
                spec = {'name': view['project']['name'], 'database': 'sqlite',
                        'requirements': state['confirmation']['snapshot'],
                        'obligations': site_obligations.catalog()['empty_values']}
                if view['project']['production_guides']:
                    spec['production_guides'] = copy.deepcopy(view['project']['production_guides'])
                result = generate(stage, copy.deepcopy(spec), copy.deepcopy(dependencies))
                from .codex_pipeline import validate_stage as validate_output
                result = validate_output(result, stage)
                checks = validate_stage(stage, result['files'], self._validation_dependencies(state, dependencies))
                artifact = _artifact(result['files'], result['notes'], checks)
            with self._lock, self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                current, version = self._read(db, project_id)
                if not current['job'] or current['job']['id'] != job['id'] or current['job']['state'] != 'running':
                    return
                latest = self._view(db, project_id)
                selected = next(s for s in latest['pipeline']['stages'] if s['id'] == stage)
                if selected['input_key'] != job['input_key'] or not latest['pipeline']['confirmed']:
                    raise ValueError('pipeline_artifact_stale')
                # All predecessors must still be approved, even if files have not changed.
                if any(s['state'] != 'approved' for s in latest['pipeline']['stages'] if ORDER.index(s['id']) < ORDER.index(stage)):
                    raise ValueError('pipeline_artifact_stale')
                current['artifacts'][stage] = {'input_key': job['input_key'], 'artifact': artifact}
                current['approvals'].pop(stage, None)
                current['job'] = dict(job, state='completed')
                self._write(db, project_id, current, version, 'artifact_generated', {'stage': stage, 'artifact': artifact, 'job_id': job['id']})
        except Exception as error:
            from .codex_intake import SAFE_ERROR_CODES
            code = str(error) if str(error) in ERRORS | SAFE_ERROR_CODES else 'pipeline_job_failed'
            with self._lock, self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                current, version = self._read(db, project_id)
                if current['job'] and current['job']['id'] == job['id'] and current['job']['state'] == 'running':
                    current['job'] = dict(job, state='failed', error=code)
                    self._write(db, project_id, current, version, 'generation_failed', {'stage': stage, 'error': code})

    def download(self, project_id, expected_revision):
        from .pipeline_artifacts import bundle
        with self._lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            view = self._check(db, project_id, expected_revision)
            if not view['pipeline']['ready_for_delivery']:
                raise ValueError('pipeline_stage_locked')
            state, version = self._read(db, project_id)
            artifacts = {s['id']: s['artifact'] for s in view['pipeline']['stages']
                         if s['id'] not in {'requirements', 'delivery'}}
            report = json.loads(next(file['content'] for file in view['pipeline']['stages'][-1]['artifact']['files']
                                     if file['path'] == 'review/delivery.json'))
            data = bundle(view['project']['name'], artifacts, state['obligations'], evidence={
                'requirements': state['confirmation']['snapshot'], 'reviews': state['approvals'], 'delivery': report})
            self._write(db, project_id, state, version, 'bundle_downloaded', {'stage': 'delivery'})
            return data

    def close(self):
        self._jobs.shutdown(wait=True)
