"""Real state/SQLite/bundle workflow with synthetic Codex responses only."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
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
                                                      'responses': {'200': {'description': 'List'}}}}}})},
        'backend': {'backend/app.py': 'from http.server import HTTPServer\n# Synthetic fixture, never executed.\n',
                    'backend/README.md': 'Synthetic review fixture.'},
        'frontend': {'frontend/index.html': '<html><head><link rel="stylesheet" href="/style.css"></head><body><h1>문의</h1><script src="/app.js"></script>' + ''.join(
                         '<a href="/' + name + '.html">' + name + '</a>' for name in ['privacy', 'terms', 'refund', 'contact'])
                         + '<!-- CHANNELSHIFT_SITE_FOOTER --></body></html>',
                     'frontend/app.js': 'fetch("/api/inquiries");', 'frontend/style.css': 'body{background:white}'}
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
        self.delivery.save_erd(self.project_id, schema, project['erd_draft']['revision'])
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
        self.delivery.save_erd(self.project_id, schema, project['erd_draft']['revision'])
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


if __name__ == '__main__':
    unittest.main()
