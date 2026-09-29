"""Requirement-to-editor handoff across real member HTTP boundaries, with fake AI."""
import copy
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
from test_member_http import IdentityFixture


class ERDHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-erd-http-')
        self.addCleanup(self.temp.cleanup)
        self.codex = Mock()
        self.codex.status.return_value = {'can_execute': True, 'state': 'connected'}
        self.codex.extract_requirements.side_effect = lambda user, text: {
            'requirements': [{'id': 'REQ-001', 'text': '콘텐츠 게시물 관리', 'quote': text, 'origin': 'client'}],
            'questions': [], 'out_of_scope': []}

        def design(user, snapshot, database):
            schema = {'format': 'channelshift.schema/v1', 'name': snapshot['name'], 'database': database,
                      'entities': [{'name': 'posts', 'description': '게시물', 'attributes': [
                          {'name': 'id', 'type': 'uuid', 'nullable': False, 'primary_key': True, 'unique': True},
                          {'name': 'title', 'type': 'text', 'nullable': False, 'primary_key': False, 'unique': False}]}],
                      'relations': []}
            return {'schema': schema,
                    'traceability': [{'entity': row['name'], 'requirement_ids': ['REQ-001']} for row in schema['entities']],
                    'unmapped_requirements': [], 'notes': ['합성 테스트 결과']}

        self.codex.generate_erd.side_effect = design
        self.services = Mock()
        handler = member_handler_factory(IdentityFixture(), Path(self.temp.name), 'erd-csrf',
                                         codex=self.codex, services=self.services)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server.RequestHandlerClass.close_resources()

    def request(self, path, body=None, session='session-a-synthetic', token='erd-csrf'):
        headers = {'Origin': f'http://127.0.0.1:{self.server.server_port}',
                   'X-ChannelShift-Token': token,
                   'Cookie': f'channelshift_member_{self.server.server_port}={session}'}
        encoded = None if body is None else json.dumps(body).encode('utf-8')
        if encoded is not None:
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET' if body is None else 'POST', path, encoded, headers)
            reply = connection.getresponse()
            raw = reply.read()
            return reply.status, json.loads(raw) if 'application/json' in reply.getheader('Content-Type', '') else raw.decode('utf-8')
        finally:
            connection.close()

    def complete(self, project_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, body = self.request('/api/delivery/projects/' + project_id)
            self.assertEqual(status, 200)
            if body['project']['state'] not in {'EXTRACTING', 'REVIEWING', 'DESIGNING_ERD', 'COLLECTING_REFERENCE'}:
                return body['project']
            time.sleep(.01)
        self.fail('Synthetic job did not complete')

    def ready(self):
        status, body = self.request('/api/delivery/projects', {'name': '요구사항 연결', 'client_request': '게시물을 관리합니다.'})
        self.assertEqual(status, 200)
        project = body['project']
        self.assertEqual(self.request('/api/delivery/extract', {'project_id': project['id']})[0], 200)
        project = self.complete(project['id'])
        status, body = self.request('/api/delivery/next', {'project_id': project['id'], 'expected_revision': project['intervention_revision']})
        self.assertEqual(status, 200)
        return body['project']

    def generate(self, project):
        status, _ = self.request('/api/delivery/erd', {'project_id': project['id'], 'database': 'postgresql',
                                                       'expected_revision': project['intervention_revision']})
        self.assertEqual(status, 200)
        project = self.complete(project['id'])
        self.assertTrue(project['erd_current'])
        return project

    def test_review_to_erd_to_editor_save_and_sql_remains_linked_and_private(self):
        project = self.generate(self.ready())
        called_user, snapshot, database = self.codex.generate_erd.call_args.args
        self.assertEqual(called_user, 'a' * 32)
        self.assertEqual(snapshot['source']['text'], '게시물을 관리합니다.')
        self.assertEqual(database, 'postgresql')
        self.assertFalse(project['erd_draft']['approval_granted'])
        self.assertFalse(project['erd_draft']['database_executed'])
        for route in ('/?delivery=' + project['id'], '/delivery?project=' + project['id']):
            status, html = self.request(route)
            self.assertEqual(status, 200)
            self.assertIn('erd-csrf', html)
            self.assertEqual(self.request(route, session='')[0], 302)
        schema = copy.deepcopy(project['erd_draft']['result']['schema'])
        schema['entities'][0]['attributes'].append({'name': 'editor_note', 'type': 'text', 'nullable': True})
        data = {'project_id': project['id'], 'schema': schema, 'expected_revision': project['erd_draft']['revision']}
        self.assertEqual(self.request('/api/delivery/erd/save', data, session='session-b-synthetic')[0], 400)
        status, body = self.request('/api/delivery/erd/save', data)
        self.assertEqual(status, 200)
        self.assertEqual(body['project']['erd_draft']['result']['schema'], schema)
        self.assertFalse(body['project']['erd_draft']['traceability_current'])
        self.assertEqual(self.complete(project['id'])['erd_draft']['result']['schema'], schema)
        self.assertEqual(self.request('/api/delivery/erd/save', data)[0], 409)
        self.assertEqual(self.request('/api/delivery/projects/' + project['id'], session='session-b-synthetic')[0], 400)
        status, output = self.request('/api/export', {'schema': schema, 'format': 'sql'})
        self.assertEqual(status, 200)
        self.assertIn('CREATE TABLE', output['files'][0]['content'])
        self.codex.generate_erd.assert_called_once()

    def test_generation_gate_auth_csrf_exact_input_and_other_member(self):
        project = self.request('/api/delivery/projects', {'name': 'No review', 'client_request': 'Source'})[1]['project']
        request = {'project_id': project['id'], 'database': 'postgresql', 'expected_revision': project['intervention_revision']}
        self.assertEqual(self.request('/api/delivery/erd', request)[0], 400)
        project = self.ready()
        request.update(project_id=project['id'], expected_revision=project['intervention_revision'])
        self.assertEqual(self.request('/api/delivery/erd', request, session='')[0], 401)
        self.assertEqual(self.request('/api/delivery/erd', request, token='wrong')[0], 403)
        self.assertEqual(self.request('/api/delivery/erd', request, session='session-b-synthetic')[0], 400)
        self.assertEqual(self.request('/api/delivery/erd', dict(request, member_id='b'*32))[0], 404)
        self.assertEqual(self.request('/api/delivery/erd', dict(request, expected_revision='0'*64))[0], 409)
        self.codex.status.return_value = {'can_execute': False, 'state': 'disconnected'}
        self.assertEqual(self.request('/api/delivery/erd', request)[0], 403)
        self.codex.generate_erd.assert_not_called()

    def test_evidence_change_invalidates_linked_draft_without_losing_edited_schema(self):
        project = self.generate(self.ready())
        original = project['erd_draft']
        status, body = self.request('/api/delivery/consent', {'project_id': project['id'], 'mode': 'required',
            'operator_label': 'Synthetic operator', 'reason': 'changed decision', 'expected_revision': project['consent_revision']})
        self.assertEqual(status, 200)
        self.assertFalse(body['project']['erd_current'])
        self.assertEqual(body['project']['erd_draft']['revision'], original['revision'])
        self.assertEqual(body['project']['erd_draft']['result'], original['result'])
        status, body = self.request('/api/delivery/erd/save', {'project_id': project['id'],
            'schema': original['result']['schema'], 'expected_revision': original['revision']})
        self.assertEqual(status, 400)
        self.assertEqual(body['error'], 'delivery_erd_stale')


if __name__ == '__main__':
    unittest.main()
