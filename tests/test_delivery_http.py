"""HTTP intake boundaries with isolated storage and no paid provider calls."""
import copy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from channelshift.delivery_workspace import DeliveryWorkspace
from channelshift.store import ProjectStore
from channelshift.web import handler_factory


SOURCE = '회사 소개와 문의 폼이 필요합니다.'
CANDIDATE = {
    'requirements': [{'id': 'REQ-001', 'text': '문의 폼 제공', 'quote': '문의 폼', 'origin': 'client'}],
    'questions': [{'id': 'Q-001', 'text': '문의 보관 기간은?', 'blocking': True}],
    'out_of_scope': [],
}


class DeliveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-delivery-http-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extract = Mock(side_effect=lambda text: copy.deepcopy(CANDIDATE))
        self.review = Mock(return_value={'advisory_only': True})
        self.workspace = DeliveryWorkspace(self.root / 'delivery.sqlite3', extract=self.extract, review=self.review)
        self.addCleanup(self.workspace.close)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0),
                                        handler_factory(ProjectStore(self.root / 'schemas'), 'test-delivery-token',
                                                        delivery=self.workspace))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_http)
        self.origin = 'http://127.0.0.1:' + str(self.http.server_port)

    def close_http(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method='GET', body=None, headers=None):
        values = {'X-ChannelShift-Token': 'test-delivery-token', 'Origin': self.origin,
                  'Content-Type': 'application/json'}
        for key, value in (headers or {}).items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        try:
            encoded = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
            connection.request(method, path, body=encoded, headers=values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def create(self):
        status, _, raw = self.request('/api/delivery/projects', 'POST', {'name': '테스트 회사', 'client_request': SOURCE})
        self.assertEqual(status, 200)
        return json.loads(raw)['project']

    def finished(self, project_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, _, raw = self.request('/api/delivery/projects/' + project_id)
            self.assertEqual(status, 200)
            project = json.loads(raw)['project']
            if project['state'] not in {'EXTRACTING', 'REVIEWING'}:
                return project
            time.sleep(0.01)
        self.fail('Mock provider job did not finish')

    def test_delivery_page_injects_request_token_and_restrictive_csp(self):
        status, headers, raw = self.request('/delivery', headers={'X-ChannelShift-Token': None})
        self.assertEqual(status, 200)
        self.assertIn(b'test-delivery-token', raw)
        self.assertNotIn(b'__CHANNELSHIFT_TOKEN__', raw)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_creation_requires_source_and_preserves_utf8_source_without_execution(self):
        for body in ({'name': 'Demo'}, {'name': 'Demo', 'client_request': ''},
                     {'name': 'Demo', 'client_request': True}):
            with self.subTest(body=body):
                status, _, raw = self.request('/api/delivery/projects', 'POST', body)
                self.assertGreaterEqual(status, 400)
                self.assertFalse(json.loads(raw)['ok'])
        self.assertEqual(self.workspace.list(), [])
        project = self.create()
        self.assertEqual(project['source']['text'], SOURCE)
        self.assertEqual(project['state'], 'RECEIVED')
        self.assertEqual(project['events'][0]['kind'], 'source_registered')
        self.assertIsNone(project['candidate'])
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_impact_uses_registered_graph_without_provider_or_storage_writes(self):
        examples = Path(__file__).resolve().parents[1] / 'docs' / 'examples'
        schema = json.loads((examples / 'booking-schema.json').read_text(encoding='utf-8'))
        graph = json.loads((examples / 'booking-impact-example.json').read_text(encoding='utf-8'))
        body = {'schema': schema, 'table_id': 'customers', 'field_id': 'email', 'graph': graph}
        status, _, raw = self.request('/api/impact', 'POST', body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['impact']['counts'], {'api': 4, 'backend': 3, 'screen': 6, 'test': 11})
        for headers in ({'Origin': 'https://foreign.example'}, {'X-ChannelShift-Token': None}):
            self.assertEqual(self.request('/api/impact', 'POST', body, headers=headers)[0], 403)
        self.assertEqual(self.workspace.list(), [])
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_impact_unknown_and_stale_revision_never_report_zero_safe(self):
        examples = Path(__file__).resolve().parents[1] / 'docs' / 'examples'
        schema = json.loads((examples / 'booking-schema.json').read_text(encoding='utf-8'))
        graph = json.loads((examples / 'booking-impact-example.json').read_text(encoding='utf-8'))
        graph['schema_digest'] = 'a' * 64
        for trace in (None, graph):
            status, _, raw = self.request('/api/impact', 'POST', {'schema': schema, 'table_id': 'customers', 'field_id': 'email', 'graph': trace})
            self.assertEqual(status, 200)
            result = json.loads(raw)['impact']
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertTrue(all(value is None for value in result['counts'].values()))

    def test_foreign_host_origin_and_missing_token_block_all_new_actions(self):
        project = self.create()
        actions = [('/api/delivery/projects', {'name': 'Demo', 'client_request': SOURCE}),
                   ('/api/delivery/extract', {'project_id': project['id']}),
                   ('/api/delivery/jev', {'project_id': project['id']}),
                   ('/api/delivery/intervention', {'project_id': project['id'], 'stage_id': 'requirements',
                                                   'reason': 'missing_client_info', 'note': '자료 누락',
                                                   'decision': '자료 요청', 'outcome': '',
                                                   'expected_revision': project['intervention_revision']})]
        for headers in ({'Host': 'foreign.example'}, {'Origin': 'https://foreign.example'},
                        {'Origin': None}, {'X-ChannelShift-Token': None}):
            for path, body in actions:
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(path, 'POST', body, headers)[0], 403)
        for path in ('/api/delivery/projects', '/api/delivery/status', '/api/delivery/projects/' + project['id']):
            self.assertEqual(self.request(path, headers={'X-ChannelShift-Token': None})[0], 403)
        self.assertEqual(len(self.workspace.get(project['id'])['events']), 1)
        self.assertEqual(len(self.workspace.list()), 1)
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_unknown_fields_cannot_supply_commands_approvals_or_identity(self):
        project = self.create()
        cases = [('/api/delivery/projects', {'name': 'Demo', 'client_request': SOURCE, 'approved': True}),
                 ('/api/delivery/extract', {'project_id': project['id'], 'shell': 'untrusted command'}),
                 ('/api/delivery/jev', {'project_id': project['id'], 'approved': True}),
                 ('/api/delivery/intervention', {'project_id': project['id'], 'stage_id': 'requirements',
                                                'reason': 'other', 'note': 'note', 'decision': 'decision',
                                                'outcome': '', 'actor_id': 'admin',
                                                'expected_revision': project['intervention_revision']})]
        for path, body in cases:
            with self.subTest(path=path):
                status, _, raw = self.request(path, 'POST', body)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(raw)['error'], 'invalid_request')
        self.assertEqual(len(self.workspace.get(project['id'])['events']), 1)
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_explicit_mock_jobs_record_candidate_then_advice_without_approval(self):
        project = self.create()
        self.assertEqual(self.request('/api/delivery/jev', 'POST', {'project_id': project['id']})[0], 400)
        self.assertEqual(self.request('/api/delivery/extract', 'POST', {'project_id': project['id']})[0], 200)
        extracted = self.finished(project['id'])
        self.assertEqual(extracted['state'], 'REVIEW_REQUIRED')
        self.assertEqual(extracted['candidate'], CANDIDATE)
        self.extract.assert_called_once_with(SOURCE)
        self.review.assert_not_called()
        self.assertEqual(self.request('/api/delivery/jev', 'POST', {'project_id': project['id']})[0], 200)
        reviewed = self.finished(project['id'])
        self.assertEqual(reviewed['state'], 'REVIEW_REQUIRED')
        self.review.assert_called_once_with(SOURCE, CANDIDATE['requirements'])
        self.assertIn('advice_recorded', [event['kind'] for event in reviewed['events']])
        self.assertNotIn('approval', reviewed)

    def test_intervention_survives_reopen_without_granting_gate_approval(self):
        project = self.create()
        body = {'project_id': project['id'], 'stage_id': 'requirements', 'reason': 'missing_client_info',
                'note': '보관 기간이 없습니다.', 'decision': '고객에게 확인 요청', 'outcome': '답변 대기',
                'expected_revision': project['intervention_revision']}
        status, _, raw = self.request('/api/delivery/intervention', 'POST', body)
        self.assertEqual(status, 200)
        current = json.loads(raw)['project']
        self.assertEqual(current['state'], 'RECEIVED')
        self.assertFalse(current['interventions'][0]['approval_granted'])
        reopened = DeliveryWorkspace(self.workspace.path, extract=self.extract, review=self.review)
        try:
            stored = reopened.get(project['id'])
            self.assertEqual(stored['state'], 'RECEIVED')
            self.assertEqual(stored['interventions'][0]['decision'], body['decision'])
            self.assertEqual(stored['interventions'][0]['actor_type'], 'local_operator')
            self.assertFalse(stored['interventions'][0]['approval_granted'])
            self.assertEqual(stored['interventions'][0]['intervention_revision'], project['intervention_revision'])
            self.assertIsNone(stored['candidate'])
        finally:
            reopened.close()

    def test_intervention_requires_revision_and_rejects_stale_candidate_with_409(self):
        project = self.create()
        self.request('/api/delivery/extract', 'POST', {'project_id': project['id']})
        viewed = self.finished(project['id'])
        body = {'project_id': project['id'], 'stage_id': 'requirements', 'reason': 'quality_issue',
                'note': '첫 후보를 보고 작성한 메모', 'decision': '다시 확인', 'outcome': ''}
        self.assertEqual(self.request('/api/delivery/intervention', 'POST', body)[0], 404)
        for invalid in (None, True, '', 'z' * 64):
            with self.subTest(invalid=invalid):
                status, _, raw = self.request('/api/delivery/intervention', 'POST',
                                              {**body, 'expected_revision': invalid})
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(raw)['error'], 'invalid_delivery_input')
        replacement = copy.deepcopy(CANDIDATE)
        replacement['requirements'][0]['text'] = '수정된 문의 폼 후보'
        self.extract.side_effect = lambda _: copy.deepcopy(replacement)
        self.workspace._jobs.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.request('/api/delivery/extract', 'POST', {'project_id': project['id']})[0], 200)
        latest = self.finished(project['id'])
        status, _, raw = self.request('/api/delivery/intervention', 'POST',
                                      {**body, 'expected_revision': viewed['intervention_revision']})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(raw), {'ok': False, 'error': 'delivery_revision_conflict'})
        self.assertEqual(self.workspace.get(project['id']), latest)
        self.assertEqual(latest['interventions'], [])
        self.assertEqual(self.extract.call_count, 2)
        self.review.assert_not_called()
        body['expected_revision'] = latest['intervention_revision']
        status, _, raw = self.request('/api/delivery/intervention', 'POST', body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['project']['interventions'][-1]['note'], body['note'])

    def test_status_get_returns_metadata_only_and_never_calls_paid_providers(self):
        safe = {'available': True, 'authenticated': True, 'auth_mode': 'chatgpt',
                'can_execute': True, 'cli_version': 'test', 'reason': 'ready'}
        with patch('channelshift.codex_intake.status', return_value=safe) as status_probe, \
             patch('channelshift.jev_review._credential', return_value='private-secret'), \
             patch('channelshift.codex_intake.extract_requirements') as paid_codex, \
             patch('channelshift.jev_review.review_requirements') as paid_jev:
            status, _, raw = self.request('/api/delivery/status')
            self.assertEqual(status, 200)
            value = json.loads(raw)
            self.assertEqual({key: value[key] for key in ('ok', 'codex', 'jev_configured')}, {'ok': True, 'codex': safe, 'jev_configured': True})
            self.assertEqual(value['stages'][0]['id'], 'intake')
            self.assertIn('design_candidate', {stage['id'] for stage in value['stages']})
            self.assertNotIn(b'private-secret', raw)
            status_probe.assert_called_once_with()
            paid_codex.assert_not_called()
            paid_jev.assert_not_called()
        self.extract.assert_not_called()
        self.review.assert_not_called()


if __name__ == '__main__':
    unittest.main()
