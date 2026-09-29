"""Real state/SQLite/bundle workflow with synthetic Codex responses only."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from channelshift.delivery_workspace import DeliveryWorkspace
from channelshift.pipeline_workspace import PipelineWorkspace
from channelshift.site_obligations import catalog


SOURCE = '고객이 문의를 남기고 담당자가 확인하는 홈페이지를 만들어 주세요.'


def candidate(text):
    return {'requirements': [{'id': 'REQ-001', 'text': '문의 접수', 'quote': SOURCE, 'origin': 'client'}],
            'questions': [], 'out_of_scope': []}


def erd(snapshot, database):
    return {'schema': {'format': 'channelshift.schema/v1', 'name': snapshot['name'], 'database': database,
                      'entities': [{'name': 'inquiries', 'description': '문의 기록', 'attributes': [
                          {'name': 'id', 'type': 'integer', 'primary_key': True, 'nullable': False, 'unique': True},
                          {'name': 'message', 'type': 'text', 'primary_key': False, 'nullable': False, 'unique': False}]}],
                      'relations': []},
            'traceability': [{'entity': 'inquiries', 'requirement_ids': ['REQ-001']}],
            'unmapped_requirements': [], 'notes': []}


def generated(stage, *_):
    contents = {
        'wireframe': {'wireframe/index.html': '<html><body><h1>문의 접수</h1></body></html>',
                      'wireframe/screens.json': json.dumps({'screens': [{'id': 'SCREEN-001', 'title': '문의', 'path': '/', 'requirement_ids': ['REQ-001']}]})},
        'api': {'api/openapi.json': json.dumps({'openapi': '3.1.0', 'info': {'title': '문의', 'version': '1.0'},
                   'paths': {'/api/inquiries': {'get': {'operationId': 'listInquiries', 'x-channelshift-table': 'inquiries',
                                                      'x-channelshift-requirement-ids': ['REQ-001'],
                                                      'x-channelshift-fields': ['inquiries.id', 'inquiries.message'],
                                                      'x-channelshift-screens': ['SCREEN-001'],
                                                      'responses': {'200': {'description': 'List'}}}}}})},
        'backend': {'backend/app.py': '''import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

def list_inquiries():
    with sqlite3.connect(os.environ.get('APP_DB', 'app.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT id, message FROM inquiries ORDER BY id')]

ROUTES = {('GET', '/api/inquiries'): list_inquiries}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        handler = ROUTES.get(('GET', urlsplit(self.path).path))
        if handler is None:
            self.send_error(404)
            return
        payload = json.dumps(handler()).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8080), Handler).serve_forever()
''',
                    'backend/README.md': 'Synthetic review fixture. Initialize the supplied SQLite schema before starting.',
                    'backend/routes.json': json.dumps({'routes': [{'operation_id': 'listInquiries', 'handler': 'list_inquiries',
                        'test_file': 'backend/test_app.py', 'test_symbol': 'InquiryTests.test_list'}]}),
                    'backend/test_app.py': '''import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import list_inquiries

class InquiryTests(unittest.TestCase):
    def test_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'test.sqlite3')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE inquiries (id INTEGER PRIMARY KEY, message TEXT NOT NULL)')
                db.execute('INSERT INTO inquiries VALUES (1, ?)', ('hello',))
            with patch.dict(os.environ, {'APP_DB': path}):
                self.assertEqual(list_inquiries(), [{'id': 1, 'message': 'hello'}])
'''},
        'frontend': {'frontend/index.html': '<html><head><link rel="stylesheet" href="/style.css"></head><body><h1>문의</h1><script src="/app.js"></script>' + ''.join(
                         '<a href="/' + name + '.html">' + name + '</a>' for name in ['privacy', 'terms', 'refund', 'contact'])
                         + '<!-- CHANNELSHIFT_SITE_FOOTER --></body></html>',
                     'frontend/app.js': 'fetch("/api/inquiries");', 'frontend/style.css': 'body{background:white}',
                     'frontend/screens.json': json.dumps({'screens': [{'screen_id': 'SCREEN-001', 'file': 'frontend/index.html',
                                                                      'operation_ids': ['listInquiries']}]})}
    }
    return {'files': [{'path': path, 'content': content} for path, content in contents[stage].items()], 'notes': []}


def obligations():
    values = catalog()['empty_values']
    values.update(company={'name': '합성 사업자', 'representative': '테스트', 'business_number': '123-45-67890', 'address': '합성 주소 1'},
                  commerce={'registration_number': '합성 통신판매 번호'}, contact={'email': 'test@example.test', 'phone': '0200000000'},
                  hosting={'name': '합성 호스팅'}, policies={'privacy': '합성 개인정보처리방침', 'terms': '합성 이용약관', 'refund': '합성 취소 환불 규정'})
    return values


class PipelineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='channelshift-pipeline-test-')
        self.addCleanup(folder.cleanup)
        self.extract = Mock(side_effect=candidate)
        self.generator = Mock(side_effect=generated)
        self.delivery = DeliveryWorkspace(Path(folder.name) / 'delivery.sqlite3', extract=self.extract, generate_erd=erd)
        self.pipeline = PipelineWorkspace(self.delivery, generate=self.generator)
        self.addCleanup(self.delivery.close)
        self.addCleanup(self.pipeline.close)
        self.view = self.pipeline.create('합성 문의 서비스', SOURCE, 'service')
        self.project_id = self.view['project']['id']

    def refresh(self):
        self.view = self.pipeline.get(self.project_id)
        return self.view

    def act(self, action, **payload):
        self.view = self.pipeline.action(self.project_id, self.view['pipeline']['revision'], action, payload)
        return self.view

    def drain(self):
        self.delivery._jobs.submit(lambda: None).result(5)
        self.pipeline._jobs.submit(lambda: None).result(10)
        self.refresh()

    def confirm(self):
        self.act('analyze', answers=[])
        self.drain()
        self.act('confirm')
        self.assertTrue(self.view['pipeline']['confirmed'])

    def generate(self, stage):
        self.act('generate', stage=stage)
        self.drain()
        selected = next(s for s in self.view['pipeline']['stages'] if s['id'] == stage)
        self.assertEqual(selected['state'], 'generated', self.view['pipeline']['job'])
        return selected

    def approve(self, stage):
        self.act('approve', stage=stage, note='합성 자료와 일치하는지 작업자가 확인했습니다.')

    def through(self, target='frontend'):
        self.confirm()
        for stage in ['wireframe', 'erd', 'api', 'database', 'backend', 'frontend']:
            self.generate(stage)
            self.approve(stage)
            if stage == target:
                return

    def test_complete_ordered_flow_includes_all_mandatory_pages_and_actual_sqlite_evidence(self):
        self.through()
        with self.assertRaisesRegex(ValueError, '^site_obligations_incomplete$'):
            self.act('generate', stage='delivery')
        self.act('save_obligations', values=obligations())
        selected = self.generate('delivery')
        self.assertFalse(selected['artifact']['checks']['application_executed'])
        self.approve('delivery')
        raw = self.pipeline.download(self.project_id, self.view['pipeline']['revision'])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in ['privacy', 'terms', 'refund', 'contact', 'footer']:
                self.assertIn('frontend/' + name + '.html', archive.namelist())
            index = archive.read('frontend/index.html').decode()
            self.assertIn('합성 사업자', index)
            checks = json.loads(archive.read('database/checks.json'))
            self.assertTrue(checks['sqlite_executed'])
            self.assertEqual(checks['integrity_check'], 'ok')
            self.assertFalse(checks['persistent_database_created'])
        self.assertEqual(self.generator.call_count, 4)

    def test_answers_must_inform_reanalysis_and_stale_tabs_cannot_overwrite(self):
        self.extract.side_effect = lambda text: dict(candidate(text), questions=[] if '30일' in text else [
            {'id': 'Q-001', 'text': '문의 보관 기간은?', 'blocking': True}])
        self.act('analyze', answers=[])
        self.drain()
        with self.assertRaisesRegex(ValueError, 'delivery_answers_required'):
            self.act('confirm')
        stale = self.view['pipeline']['revision']
        question = self.view['project']['question_answers'][0]
        self.act('save_answers', answers=[{'question_id': question['question_id'], 'question_digest': question['question_digest'], 'answer': '30일'}])
        with self.assertRaisesRegex(ValueError, 'pipeline_analysis_required'):
            self.act('confirm')
        with self.assertRaisesRegex(ValueError, 'pipeline_revision_conflict'):
            self.pipeline.action(self.project_id, stale, 'confirm', {})
        self.act('analyze', answers=[])
        self.drain()
        self.act('confirm')
        self.assertIn('30일', self.extract.call_args.args[0])
        self.assertTrue(self.view['pipeline']['confirmed'])

    def test_erd_edit_invalidates_approved_downstream_without_losing_files(self):
        self.through('database')
        project = self.view['project']
        schema = copy.deepcopy(project['erd_draft']['result']['schema'])
        schema['entities'][0]['attributes'].append({'name': 'note', 'type': 'text', 'nullable': True})
        self.delivery.save_erd(self.project_id, schema, project['erd_draft']['revision'], note='고객 요청으로 메모 항목 추가')
        states = {s['id']: s['state'] for s in self.refresh()['pipeline']['stages']}
        self.assertEqual(states['erd'], 'generated')
        self.assertEqual(states['api'], 'stale')
        self.assertEqual(states['database'], 'stale')
        with self.assertRaisesRegex(ValueError, 'pipeline_stage_locked'):
            self.act('generate', stage='backend')

    def test_policy_change_only_invalidates_final_review_and_blocks_download(self):
        self.through()
        self.act('save_obligations', values=obligations())
        self.generate('delivery')
        self.approve('delivery')
        values = obligations()
        values['policies']['refund'] = ''
        self.act('save_obligations', values=values)
        self.assertFalse(self.view['pipeline']['ready_for_delivery'])
        states = {s['id']: s['state'] for s in self.view['pipeline']['stages']}
        self.assertEqual(states['frontend'], 'approved')
        self.assertEqual(states['delivery'], 'stale')
        with self.assertRaisesRegex(ValueError, 'pipeline_stage_locked'):
            self.pipeline.download(self.project_id, self.view['pipeline']['revision'])

    def test_generation_failure_preserves_previous_artifact_and_duplicate_job_is_blocked(self):
        self.confirm()
        previous = self.generate('wireframe')['artifact']['digest']
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def fail(*_):
            started.set()
            release.wait(5)
            raise RuntimeError('private-provider-detail')
        self.generator.side_effect = fail
        self.act('generate', stage='wireframe')
        self.assertTrue(started.wait(5))
        with self.assertRaisesRegex(ValueError, 'pipeline_busy'):
            self.act('generate', stage='wireframe')
        release.set()
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['error'], 'pipeline_job_failed')
        self.assertNotIn('private-provider-detail', json.dumps(self.view))
        self.assertEqual(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')['artifact']['digest'], previous)

    def test_upstream_change_during_generation_discards_completion(self):
        self.through('database')
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed(stage, *args):
            started.set()
            release.wait(5)
            return generated(stage)
        self.generator.side_effect = delayed
        self.act('generate', stage='backend')
        self.assertTrue(started.wait(5))
        project = self.delivery.get(self.project_id)
        schema = copy.deepcopy(project['erd_draft']['result']['schema'])
        schema['entities'][0]['attributes'].append({'name': 'changed', 'type': 'text', 'nullable': True})
        self.delivery.save_erd(self.project_id, schema, project['erd_draft']['revision'], note='진행 중 변경 검사')
        release.set()
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['error'], 'pipeline_artifact_stale')
        self.assertIsNone(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'backend')['artifact'])

    def test_manual_artifact_edit_requires_reason_and_resets_review(self):
        self.through('erd')
        wire = next(s for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')['artifact']
        files = copy.deepcopy(wire['files'])
        files[0]['content'] = files[0]['content'].replace('문의 접수', '문의 접수 화면')
        with self.assertRaisesRegex(ValueError, 'pipeline_review_note_required'):
            self.act('edit', stage='wireframe', files=files, notes=[], note='')
        self.act('edit', stage='wireframe', files=files, notes=[], note='고객이 제목을 더 명확하게 요청함')
        states = {s['id']: s['state'] for s in self.view['pipeline']['stages']}
        self.assertEqual(states['wireframe'], 'generated')
        self.assertEqual(states['erd'], 'stale')
        self.assertEqual(self.view['pipeline']['history'][0]['note'], '고객이 제목을 더 명확하게 요청함')

    def test_legacy_intake_is_visible_but_not_silently_confirmed(self):
        project = self.delivery.create('기존 프로젝트', SOURCE)
        view = self.pipeline.get(project['id'])
        self.assertEqual(view['project']['name'], '기존 프로젝트')
        self.assertFalse(view['pipeline']['confirmed'])
        self.assertEqual(view['pipeline']['obligations']['assessment']['required_count'], 7)

    def test_pipeline_passes_approved_wireframe_to_erd_and_binds_its_provenance(self):
        self.confirm()
        wireframe = self.generate('wireframe')['artifact']
        self.approve('wireframe')
        generator = Mock(side_effect=erd)
        self.delivery._generate_erd = generator
        self.generate('erd')
        snapshot = generator.call_args.args[0]
        self.assertEqual(snapshot['wireframe_context'], wireframe)
        draft = self.view['project']['erd_draft']
        self.assertEqual(draft['wireframe_digest'], wireframe['digest'])
        artifact = next(s for s in self.view['pipeline']['stages'] if s['id'] == 'erd')['artifact']
        self.assertEqual(artifact['checks']['draft_revision'], draft['revision'])

    def test_standalone_identical_regeneration_cannot_inherit_pipeline_approval(self):
        self.through('api')
        prior = self.view['project']['erd_draft']['result']['schema']
        project = self.delivery.get(self.project_id)
        self.delivery.generate_erd(self.project_id, 'sqlite', project['intervention_revision'])
        self.delivery._jobs.submit(lambda: None).result(10)
        self.refresh()
        self.assertEqual(self.view['project']['erd_draft']['result']['schema'], prior)
        self.assertIsNone(self.view['project']['erd_draft']['wireframe_digest'])
        stages = {s['id']: s for s in self.view['pipeline']['stages']}
        self.assertEqual(stages['erd']['state'], 'stale')
        self.assertFalse(stages['erd']['can_approve'])
        self.assertEqual(stages['api']['state'], 'stale')
        self.assertIsNone(self.pipeline.traceability(self.project_id)['graph'])

    def test_erd_edit_reason_survives_reopen_in_unified_history(self):
        self.through('erd')
        draft = self.view['project']['erd_draft']
        schema = copy.deepcopy(draft['result']['schema'])
        schema['entities'][0]['attributes'].append({'name': 'memo', 'type': 'text', 'nullable': True})
        self.delivery.save_erd(self.project_id, schema, draft['revision'], note='고객 문의 분류를 위해 메모 추가')
        self.pipeline.close()
        self.pipeline = PipelineWorkspace(self.delivery, generate=self.generator)
        self.addCleanup(self.pipeline.close)
        entries = [event for event in self.refresh()['pipeline']['history'] if event['kind'] == 'erd_edited']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['stage'], 'erd')
        self.assertEqual(entries[0]['note'], '고객 문의 분류를 위해 메모 추가')

    def test_history_preserves_event_order_when_multiple_events_share_timestamp(self):
        from channelshift.pipeline_workspace import _now
        with patch('channelshift.pipeline_workspace._now', return_value=_now()):
            self.through()
        groups = {}
        for event in self.view['pipeline']['history']:
            if event['id'].startswith('pipeline:'):
                groups.setdefault(event['at'], []).append(int(event['id'].partition(':')[2]))
        self.assertTrue(any(len(sequence) > 10 for sequence in groups.values()))
        for sequence in groups.values():
            self.assertEqual(sequence, sorted(sequence, reverse=True))

    def test_validation_profile_changes_preserve_files_and_require_explicit_revalidation(self):
        with patch('channelshift.pipeline_workspace.VALIDATION_PROFILE', 'legacy-test-profile'):
            self.through()
            self.act('save_obligations', values=obligations())
            self.generate('delivery')
            self.approve('delivery')
            previous = next(s['artifact'] for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')
        stages = {s['id']: s for s in self.refresh()['pipeline']['stages']}
        self.assertFalse(self.view['pipeline']['ready_for_delivery'])
        self.assertEqual(stages['wireframe']['state'], 'stale')
        self.assertEqual(stages['wireframe']['artifact'], previous)
        self.assertTrue(stages['wireframe']['can_edit'])
        self.assertFalse(stages['api']['can_edit'])
        with self.assertRaisesRegex(ValueError, 'pipeline_stage_locked'):
            self.pipeline.download(self.project_id, self.view['pipeline']['revision'])
        with self.assertRaisesRegex(ValueError, 'pipeline_review_note_required'):
            self.act('edit', stage='wireframe', files=previous['files'], notes=previous['notes'], note='')
        self.act('edit', stage='wireframe', files=previous['files'], notes=previous['notes'], note='새 검사 기준으로 재검토')
        stages = {s['id']: s for s in self.view['pipeline']['stages']}
        self.assertEqual(stages['wireframe']['state'], 'generated')
        self.assertEqual(stages['wireframe']['artifact']['files'], previous['files'])
        self.assertIsNone(stages['wireframe']['review'])

    def test_validation_profile_change_fences_inflight_generation(self):
        self.confirm()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed(stage, *_):
            started.set()
            release.wait(5)
            return generated(stage)
        self.generator.side_effect = delayed
        with patch('channelshift.pipeline_workspace.VALIDATION_PROFILE', 'legacy-test-profile'):
            self.refresh()
            self.act('generate', stage='wireframe')
            self.assertTrue(started.wait(5))
        release.set()
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['error'], 'pipeline_artifact_stale')
        self.assertIsNone(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')['artifact'])

    def test_impact_is_derived_from_current_artifacts_and_becomes_unknown_when_stale(self):
        from channelshift.impact import analyze_impact
        self.through()
        value = self.pipeline.traceability(self.project_id)
        self.assertEqual(value['reason'], 'current')
        self.assertFalse(value['runtime_behavior_verified'])
        self.assertEqual(value['pipeline_revision'], self.view['pipeline']['revision'])
        schema = self.view['project']['erd_draft']['result']['schema']
        impact = analyze_impact(schema, 'inquiries', 'message', value['graph'])
        self.assertEqual(impact['counts'], {'api': 1, 'backend': 1, 'screen': 1, 'test': 1})
        files = generated('wireframe')['files']
        files[0]['content'] = files[0]['content'].replace('문의 접수', '문의 접수 변경')
        self.act('edit', stage='wireframe', files=files, notes=[], note='고객이 화면 제목 변경 요청')
        current = self.pipeline.traceability(self.project_id)
        self.assertIsNone(current['graph'])
        self.assertEqual(current['reason'], 'erd_not_current')


if __name__ == '__main__':
    unittest.main()
