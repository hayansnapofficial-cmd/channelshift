"""Per-member connections and server features over the real HTTP boundary."""
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from channelshift.member_web import member_handler_factory
from channelshift.member_codex import MemberCodexError
from test_member_http import IdentityFixture


class MemberConnectionsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = IdentityFixture()
        self.codex = Mock()
        self.codex.status.return_value = {'available': True, 'authenticated': True, 'can_execute': True, 'state': 'connected'}
        self.codex.extract_requirements.side_effect = lambda user, text: {
            'requirements': [{'id': 'REQ-001', 'text': text, 'quote': text, 'origin': 'client'}],
            'questions': [], 'out_of_scope': []}
        self.services = Mock()
        self.services.status.return_value = {key: {'available': True, 'status': 'configured'}
            for key in ('requirements_review', 'reference_collect')}
        self.review = Mock(return_value={'items': [], 'approved': False})
        self.services.review_for.return_value = self.review
        self.services.collect_reference.return_value = {'url': 'https://example.com', 'title': 'Reference', 'text': 'Reference only',
            'collected_at': '2026-09-29T00:00:00Z', 'kind': 'reference', 'approved': False}
        handler = member_handler_factory(self.auth, Path(self.temp.name), 'csrf-test', codex=self.codex, services=self.services)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.server.RequestHandlerClass.close_resources()

    def call(self, path, body=None, session='session-a-synthetic', token='csrf-test'):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        headers = {'Origin': origin, 'X-ChannelShift-Token': token,
                   'Cookie': f'channelshift_member_{self.server.server_port}={session}'}
        raw = None if body is None else json.dumps(body).encode()
        if raw is not None: headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        conn.request('GET' if raw is None else 'POST', path, raw, headers)
        response = conn.getresponse(); raw = response.read(); conn.close()
        return response.status, json.loads(raw)

    def project(self):
        return self.call('/api/delivery/projects', {'name': 'Synthetic', 'client_request': 'Client original'})[1]['project']

    def completed(self, project_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            project = self.call('/api/delivery/projects/' + project_id)[1]['project']
            if project['state'] not in {'EXTRACTING', 'REVIEWING', 'COLLECTING_REFERENCE'}: return project
            time.sleep(.01)
        self.fail('Synthetic job did not complete')

    def test_connection_is_authenticated_session_bound_and_exact_shape(self):
        self.assertEqual(self.call('/api/connections/codex', session='')[0], 401)
        self.assertEqual(self.call('/api/connections/codex/start', {}, token='wrong')[0], 403)
        self.assertEqual(self.call('/api/connections/codex/start', {'user_id': 'b' * 32})[0], 400)
        self.codex.connect.return_value = {'state': 'pending', 'connection_id': 'fixture-id', 'user_code': 'TEST-CODE'}
        self.assertEqual(self.call('/api/connections/codex/start', {})[0], 200)
        self.codex.connect.assert_called_once_with('a' * 32, 'session-a-synthetic')
        self.codex.poll.side_effect = MemberCodexError('codex_connection_not_found')
        self.assertEqual(self.call('/api/connections/codex/poll', {'connection_id': 'fixture-id'}, session='session-b-synthetic')[0], 400)
        self.codex.poll.assert_called_once_with('b' * 32, 'session-b-synthetic', 'fixture-id')
        self.assertEqual(self.call('/api/auth/logout', {})[0], 200)
        self.codex.cancel_session.assert_called_once_with('a' * 32, 'session-a-synthetic')

    def test_logout_revokes_session_even_when_connection_cleanup_fails(self):
        self.codex.cancel_session.side_effect = MemberCodexError('codex_stop_failed')
        status, body = self.call('/api/auth/logout', {})
        self.assertEqual(status, 200)
        self.assertTrue(body['connection_cleanup_pending'])
        self.assertEqual(self.call('/api/connections/codex')[0], 401)

    def test_member_extraction_and_shared_review_keep_identity_and_project_boundary(self):
        project = self.project()
        self.assertEqual(self.call('/api/delivery/extract', {'project_id': project['id']}, session='session-b-synthetic')[0], 400)
        self.codex.extract_requirements.assert_not_called()
        self.assertEqual(self.call('/api/delivery/extract', {'project_id': project['id']})[0], 200)
        ready = self.completed(project['id'])
        self.codex.extract_requirements.assert_called_once_with('a' * 32, 'Client original')
        self.assertEqual(self.call('/api/delivery/jev', {'project_id': project['id']})[0], 200)
        self.completed(project['id'])
        self.services.review_for.assert_called_once_with('a' * 32)
        self.review.assert_called_once_with('Client original', ready['candidate']['requirements'])

    def test_reference_collect_is_revision_bound_and_never_becomes_client_source(self):
        project = self.project()
        request = {'project_id': project['id'], 'url': 'https://example.com', 'expected_revision': project['intervention_revision']}
        self.assertEqual(self.call('/api/delivery/references', request, session='session-b-synthetic')[0], 400)
        self.assertEqual(self.call('/api/delivery/references', {**request, 'expected_revision': '0' * 64})[0], 409)
        self.services.collect_reference.assert_not_called()
        self.assertEqual(self.call('/api/delivery/references', request)[0], 200)
        ready = self.completed(project['id'])
        self.services.collect_reference.assert_called_once_with('a' * 32, 'https://example.com')
        self.assertEqual(ready['source'], project['source'])
        self.assertIsNone(ready['candidate'])
        self.assertEqual(ready['references'][0]['text'], 'Reference only')
        self.assertFalse(ready['references'][0]['approved'])
        self.assertEqual(self.call('/api/delivery/references', request)[0], 409)
        self.services.collect_reference.assert_called_once()

    def test_workbench_profile_and_seo_drafts_do_not_claim_execution(self):
        status, profile = self.call('/api/workbench/profile')
        self.assertEqual(status, 200)
        self.assertEqual({item['id'] for item in profile['sections']}, {'backend', 'security', 'seo', 'delivery'})
        self.assertTrue(all(item['execution_enabled'] is False for item in profile['sections']))
        project = self.project()
        values = {'site_name': 'Synthetic', 'title': 'Title', 'description': 'Description', 'url': 'https://example.com', 'image_url': '', 'noindex': True}
        body = {'project_id': project['id'], 'section': 'seo', 'values': values, 'expected_revision': project['settings_revisions']['seo']}
        self.assertEqual(self.call('/api/workbench/settings', body, session='session-b-synthetic')[0], 400)
        self.assertEqual(self.call('/api/workbench/settings', {**body, 'values': {**values, 'url': 'http://127.0.0.1'}})[0], 400)
        status, result = self.call('/api/workbench/settings', body)
        self.assertEqual(status, 200)
        self.assertEqual(result['project']['workspace_settings']['seo'], values)
        self.assertFalse(result['project']['events'][-1]['payload']['applied_to_site'])
        self.assertEqual(self.call('/api/workbench/settings', body)[0], 409)
        self.assertEqual(self.call('/api/delivery/projects/' + project['id'])[1]['project']['workspace_settings']['seo'], values)


if __name__ == '__main__': unittest.main()
