"""Real auth and HTTP boundaries for masters who also own regular workspaces."""
import hashlib
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift.member_auth import MemberAuth
from channelshift.member_web import member_handler_factory


class MasterHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-master-http-')
        self.addCleanup(self.temp.cleanup)
        hash_stub = patch('channelshift.member_auth._derive', side_effect=lambda password, salt: hashlib.sha256(salt + password).digest())
        hash_stub.start()
        self.addCleanup(hash_stub.stop)
        self.sent = []
        self.auth = MemberAuth(Path(self.temp.name) / 'auth.sqlite3', mailer=lambda email, token: self.sent.append(token))
        self.master = self.auth.bootstrap_master('localowner', 'owner@example.test', 'QaOwner!9')
        self.auth.register('visitor', 'visitor@example.test', 'synthetic-member-passphrase')
        self.auth.verify(self.sent[-1], 'synthetic-member-passphrase')
        self.master_session = self.auth.login('localowner', 'QaOwner!9')['session_token']
        member_login = self.auth.login('visitor', 'synthetic-member-passphrase')
        self.member = member_login['user']
        self.member_session = member_login['session_token']
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), member_handler_factory(self.auth, self.temp.name, 'csrf-admin'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def call(self, path, session='', data=None, overrides=None):
        headers = {'Origin': self.origin, 'X-ChannelShift-Token': 'csrf-admin'}
        if session:
            headers['Cookie'] = f'channelshift_member_{self.server.server_port}={session}'
        raw = None
        if data is not None:
            raw = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        headers.update(overrides or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST' if raw else 'GET', path, raw, headers)
        response = connection.getresponse()
        body = response.read()
        content_type = response.getheader('Content-Type', '')
        connection.close()
        return response.status, json.loads(body) if content_type.startswith('application/json') else body.decode()

    def test_only_master_reads_administration_and_assets(self):
        self.assertEqual(self.call('/api/admin/overview')[0], 401)
        for route in ('/api/admin/overview', '/admin', '/admin.js', '/admin.css'):
            with self.subTest(route=route):
                self.assertEqual(self.call(route, self.member_session)[0], 403)
        status, body = self.call('/api/admin/overview', self.master_session)
        self.assertEqual(status, 200)
        self.assertEqual({item['username'] for item in body['members']}, {'localowner', 'visitor'})
        for forbidden in ('password_hash', 'salt', 'session_token', 'token_hash'):
            self.assertNotIn(forbidden, json.dumps(body))
        status, page = self.call('/admin', self.master_session)
        self.assertEqual(status, 200)
        self.assertNotIn('__CHANNELSHIFT_TOKEN__', page)
        self.assertIn('csrf-admin', page)

    def test_master_has_regular_workspace_without_other_member_access(self):
        status, created = self.call('/api/delivery/projects', self.master_session,
                                    {'name': 'Owner project', 'client_request': 'Own synthetic request'})
        self.assertEqual(status, 200)
        owner_project = created['project']['id']
        self.assertEqual(self.call('/api/delivery/projects', self.member_session)[1]['items'], [])
        self.assertEqual(self.call('/api/delivery/projects/' + owner_project, self.member_session)[0], 400)
        _, created = self.call('/api/delivery/projects', self.member_session,
                               {'name': 'Member project', 'client_request': 'Private synthetic request'})
        self.assertEqual(self.call('/api/delivery/projects/' + created['project']['id'], self.master_session)[0], 400)
        self.assertIn('href="/admin"', self.call('/delivery', self.master_session)[1])
        self.assertNotIn('href="/admin"', self.call('/delivery', self.member_session)[1])
        for operation in ('extract', 'jev'):
            self.assertEqual(self.call('/api/delivery/' + operation, self.master_session,
                                       {'project_id': owner_project})[0], 403)

    def test_suspend_restore_revoke_and_audit(self):
        command = {'user_id': self.member['id'], 'disabled': True, 'reason': 'Synthetic QA suspension'}
        self.assertEqual(self.call('/api/admin/member-status', self.member_session, command)[0], 403)
        self.assertEqual(self.call('/api/admin/member-status', self.master_session, command,
                                   {'Origin': 'https://untrusted.example'})[0], 403)
        self.assertEqual(self.call('/api/admin/member-status', self.master_session, command,
                                   {'X-ChannelShift-Token': 'invalid'})[0], 403)
        self.assertEqual(self.call('/api/admin/member-status', self.master_session, dict(command, role='master'))[0], 400)
        self.assertEqual(self.call('/api/admin/member-status', self.master_session, command)[0], 200)
        self.assertEqual(self.call('/api/delivery/projects', self.member_session)[0], 401)
        self.assertEqual(self.call('/api/auth/login', data={'username': 'visitor', 'password': 'synthetic-member-passphrase'})[0], 401)
        command.update(disabled=False, reason='Synthetic QA restore')
        self.assertEqual(self.call('/api/admin/member-status', self.master_session, command)[0], 200)
        self.assertEqual(self.call('/api/delivery/projects', self.member_session)[0], 401)
        self.assertEqual(self.call('/api/auth/login', data={'username': 'visitor', 'password': 'synthetic-member-passphrase'})[0], 200)
        audit = self.call('/api/admin/overview', self.master_session)[1]['audit']
        self.assertTrue(any(row['action'] == 'member_disabled' and row['target_id'] == self.member['id'] for row in audit))
        self.assertTrue(any(row['action'] == 'member_enabled' and row['target_id'] == self.member['id'] for row in audit))
        command.update(user_id=self.master['id'], disabled=True)
        self.assertEqual(self.call('/api/admin/member-status', self.master_session, command)[0], 403)

    def test_public_auth_cannot_bootstrap_or_assign_a_role(self):
        self.assertEqual(self.call('/api/auth/bootstrap', data={'username': 'intruder', 'role': 'master'})[0], 404)
        self.assertEqual(self.call('/api/auth/register', data={'username': 'intruder', 'email': 'bad@example.test',
                                                             'password': 'synthetic-member-passphrase', 'role': 'master'})[0], 400)


if __name__ == '__main__':
    unittest.main()
