"""Actual HTTP boundaries for the studio; no real user/model credentials."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from channelshift.member_web import member_handler_factory
from test_member_http import IdentityFixture
from test_pipeline_workspace import SOURCE, candidate, erd, generated


class StudioHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-studio-http-')
        self.addCleanup(self.temp.cleanup)
        self.codex = Mock()
        self.codex.status.return_value = {'can_execute': True, 'state': 'connected'}
        self.codex.extract_requirements.side_effect = lambda user, text: candidate(text)
        self.codex.generate_erd.side_effect = lambda user, snapshot, database: erd(snapshot, database)
        self.codex.generate_stage.side_effect = lambda user, stage, spec, deps: generated(stage, spec, deps)
        self.auth = IdentityFixture()
        handler = member_handler_factory(self.auth, Path(self.temp.name), 'studio-test', codex=self.codex, services=Mock())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.server.RequestHandlerClass.close_resources()

    def call(self, path, body=None, *, session='session-a-synthetic', token='studio-test', origin=True):
        headers = {'X-ChannelShift-Token': token, 'Cookie': f'channelshift_member_{self.server.server_port}={session}'}
        if origin:
            headers['Origin'] = f'http://127.0.0.1:{self.server.server_port}'
        raw = None if body is None else json.dumps(body).encode('utf-8')
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request('GET' if body is None else 'POST', path, raw, headers)
            response = conn.getresponse()
            data, result_headers = response.read(), dict(response.getheaders())
            return response.status, json.loads(data) if 'application/json' in result_headers['Content-Type'] else data, result_headers
        finally:
            conn.close()

    def create(self):
        status, view, _ = self.call('/api/studio/projects', {'name': 'HTTP 합성 프로젝트', 'client_request': SOURCE, 'site_type': 'saas'})
        self.assertEqual(status, 200)
        return view

    def act(self, view, action, **payload):
        status, result, _ = self.call('/api/studio/action', {'project_id': view['project']['id'],
            'expected_revision': view['pipeline']['revision'], 'action': action, 'payload': payload})
        self.assertEqual(status, 200, result)
        return result

    def drain(self, view):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, result, _ = self.call('/api/studio/projects/' + view['project']['id'])
            self.assertEqual(status, 200)
            if result['project']['state'] not in {'EXTRACTING', 'DESIGNING_ERD'} and (result['pipeline']['job'] or {}).get('state') != 'running':
                return result
            time.sleep(.01)
        self.fail('Synthetic worker stalled')

    def test_member_isolation_csrf_stale_revision_and_required_seven(self):
        view = self.create()
        self.assertEqual(view['pipeline']['obligations']['assessment']['required_count'], 7)
        path = '/api/studio/projects/' + view['project']['id']
        self.assertEqual(self.call(path, session='')[0], 401)
        self.assertEqual(self.call(path, session='session-b-synthetic')[0], 400)
        self.assertEqual(self.call(path + '/traceability', session='')[0], 401)
        self.assertEqual(self.call(path + '/traceability', session='session-b-synthetic')[0], 400)
        self.assertEqual(self.call('/api/studio/projects', session='session-b-synthetic')[1]['items'], [])
        body = {'project_id': view['project']['id'], 'expected_revision': view['pipeline']['revision'],
                'action': 'generate', 'payload': {'stage': 'frontend'}}
        self.assertEqual(self.call('/api/studio/action', body, token='wrong')[0], 403)
        self.assertEqual(self.call('/api/studio/action', body, origin=False)[0], 403)
        self.assertEqual(self.call('/api/studio/action', body)[1]['error'], 'pipeline_stage_locked')
        self.assertEqual(self.call('/api/studio/action', dict(body, expected_revision='0'*64))[0], 409)
        self.assertEqual(self.call('/api/studio/bundle', {'project_id': view['project']['id'], 'expected_revision': view['pipeline']['revision']})[1]['error'], 'pipeline_stage_locked')
        self.codex.generate_stage.assert_not_called()

    def test_sandbox_preview_and_generation_use_the_authenticated_members_workspace(self):
        view = self.drain(self.act(self.create(), 'analyze', answers=[]))
        view = self.act(view, 'confirm')
        view = self.drain(self.act(view, 'generate', stage='wireframe'))
        self.assertEqual(view['pipeline']['job']['state'], 'completed')
        self.assertEqual(self.codex.generate_stage.call_args.args[0], 'a'*32)
        path = '/studio-preview/' + view['project']['id'] + '/wireframe'
        status, html, headers = self.call(path)
        self.assertEqual(status, 200)
        self.assertIn('문의 접수', html.decode())
        csp = headers['Content-Security-Policy']
        self.assertIn('sandbox;', csp)
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn('allow-scripts', csp)
        self.assertNotIn('allow-same-origin', csp)
        self.assertEqual(self.call(path, session='')[0], 302)
        self.assertEqual(self.call(path, session='session-b-synthetic')[0], 400)
        self.assertEqual(self.call(path.replace('wireframe', 'backend'))[0], 400)

    def test_new_workspace_is_default_and_editor_remains_accessible(self):
        for route in ('/', '/delivery', '/studio'):
            status, html, _ = self.call(route)
            self.assertEqual(status, 200)
            self.assertIn(b'/studio.js', html)
            self.assertIn('운영·정책', html.decode())
            self.assertNotIn('href="/admin"', html.decode())
        status, html, _ = self.call('/editor')
        self.assertEqual(status, 200)
        self.assertIn(b'/app.js', html)
        self.assertIn('제작 작업실로 돌아가기', html.decode())

    def test_traceability_endpoint_uses_current_member_artifacts_and_invalidates_on_edit(self):
        view = self.drain(self.act(self.create(), 'analyze', answers=[]))
        view = self.act(view, 'confirm')
        for stage in ('wireframe', 'erd', 'api', 'database', 'backend', 'frontend'):
            view = self.drain(self.act(view, 'generate', stage=stage))
            view = self.act(view, 'approve', stage=stage, note='합성 요구사항과 대조했습니다.')
        path = '/api/studio/projects/' + view['project']['id'] + '/traceability'
        status, evidence, _ = self.call(path)
        self.assertEqual(status, 200)
        self.assertEqual(evidence['reason'], 'current')
        self.assertEqual(set(evidence['graph']['covered_kinds']), {'api', 'backend', 'screen', 'test'})
        self.assertFalse(evidence['runtime_behavior_verified'])
        self.assertEqual(self.call(path, session='session-b-synthetic')[0], 400)
        draft = view['project']['erd_draft']
        model = draft['result']['schema']
        model['entities'][0]['attributes'][1]['nullable'] = True
        status, _, _ = self.call('/api/delivery/erd/save', {'project_id': view['project']['id'],
            'schema': model, 'expected_revision': draft['revision'], 'note': '고객 요청으로 선택 입력으로 변경'})
        self.assertEqual(status, 200)
        status, evidence, _ = self.call(path)
        self.assertEqual(status, 200)
        self.assertIsNone(evidence['graph'])
        self.assertEqual(evidence['reason'], 'contract_incomplete')


if __name__ == '__main__':
    unittest.main()
