"""Member transport isolation across real schema/intake stores, with fake identity."""
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift.core import create_schema
from channelshift.member_web import member_handler_factory


class IdentityFixture:
    mailer = None
    users = {name: {'id': key * 32, 'username': name, 'email': name + '@example.com'}
             for name, key in [('alice', 'a'), ('bobby', 'b')]}

    def __init__(self):
        self.sessions = {'session-a-synthetic': self.users['alice'], 'session-b-synthetic': self.users['bobby']}

    def authenticate(self, token):
        return self.sessions.get(token)

    def active_member(self, user_id):
        return any(user['id'] == user_id for user in self.sessions.values())

    def login(self, username, password):
        self.sessions['session-new-synthetic'] = self.users[username]
        return {'session_token': 'session-new-synthetic', 'user': self.users[username]}

    def logout(self, token):
        self.sessions.pop(token, None)

    def register(self, **_):
        raise ValueError('email_not_configured')


class MemberHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-member-http-')
        self.addCleanup(self.temp.cleanup)
        self.auth = IdentityFixture()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), member_handler_factory(self.auth, Path(self.temp.name), 'csrf-test'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def call(self, path, data=None, session='session-a-synthetic', headers=None):
        request_headers = {'X-ChannelShift-Token': 'csrf-test', 'Origin': self.origin}
        if session:
            request_headers['Cookie'] = f'channelshift_member_{self.server.server_port}={session}'
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST' if data is not None else 'GET', path, body, request_headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw), dict(response.getheaders())

    def test_absent_session_cannot_read_or_write(self):
        for path, data in [('/api/delivery/projects', None), ('/api/projects', None),
                           ('/api/delivery/projects', {'name': 'private', 'client_request': 'private'})]:
            with self.subTest(path=path, data=data):
                status, value, _ = self.call(path, data, session=None)
                self.assertEqual(status, 401)
                self.assertEqual(value['error'], 'login_required')
        status, _, headers = self.call('/delivery', session=None)
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/login')

    def test_login_token_is_injected_only_into_html(self):
        from channelshift.web import WEB
        for path, filename in [('/login', 'login.html'), ('/login.js', 'login.js'), ('/login.css', 'login.css')]:
            with self.subTest(path=path):
                connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
                connection.request('GET', path)
                response = connection.getresponse()
                raw = response.read()
                connection.close()
                expected = (WEB / filename).read_bytes()
                if filename.endswith('.html'):
                    expected = expected.replace(b'__CHANNELSHIFT_TOKEN__', b'csrf-test')
                self.assertEqual(response.status, 200)
                self.assertEqual(raw, expected)

    def test_intake_read_write_and_history_are_isolated_by_member(self):
        _, result, _ = self.call('/api/delivery/projects', {'name': 'A only', 'client_request': 'A original'})
        project = result['project']
        _, own, _ = self.call('/api/delivery/projects')
        _, other, _ = self.call('/api/delivery/projects', session='session-b-synthetic')
        self.assertEqual([p['id'] for p in own['items']], [project['id']])
        self.assertEqual(other['items'], [])
        status, result, _ = self.call('/api/delivery/projects/' + project['id'], session='session-b-synthetic')
        self.assertEqual(status, 400)
        self.assertNotIn('A original', json.dumps(result))
        status, _, _ = self.call('/api/delivery/consent', {'project_id': project['id'], 'mode': 'required',
            'operator_label': 'B', 'reason': 'scope test', 'expected_revision': project['consent_revision']}, session='session-b-synthetic')
        self.assertEqual(status, 400)
        _, unchanged, _ = self.call('/api/delivery/projects/' + project['id'])
        self.assertEqual(unchanged['project']['consent_history'], [])

    def test_schema_versions_are_isolated_even_with_known_digest(self):
        schema = create_schema('membership', 'Only A')
        status, result, _ = self.call('/api/save', {'schema': schema, 'topic': 'general'})
        self.assertEqual(status, 200)
        digest = result['id']
        self.assertEqual(self.call('/api/projects/' + digest)[0], 200)
        self.assertNotEqual(self.call('/api/projects/' + digest, session='session-b-synthetic')[0], 200)
        self.assertEqual(self.call('/api/projects', session='session-b-synthetic')[1]['items'], [])

    def test_host_provider_credentials_are_never_shared(self):
        with patch('channelshift.codex_intake.status', side_effect=AssertionError('host account accessed')):
            status, value, _ = self.call('/api/delivery/status')
        self.assertEqual(status, 200)
        self.assertFalse(value['codex']['can_execute'])
        self.assertIn('requirements_review', value['services'])
        for path in ('/api/delivery/extract', '/api/delivery/jev'):
            self.assertNotEqual(self.call(path, {'project_id': 'a' * 32})[0], 200)

    def test_auth_origin_token_cookie_rotation_and_logout(self):
        for headers in ({'Origin': 'https://example.com'}, {'X-ChannelShift-Token': 'wrong'}):
            self.assertEqual(self.call('/api/auth/login', {'username': 'alice', 'password': 'synthetic'}, headers=headers)[0], 403)
        status, value, headers = self.call('/api/auth/login', {'username': 'alice', 'password': 'synthetic'})
        self.assertEqual(status, 200)
        self.assertNotIn('session_token', value)
        self.assertNotIn('session-a-synthetic', self.auth.sessions)
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(self.call('/api/auth/logout', {}, session='session-new-synthetic')[0], 200)
        self.assertEqual(self.call('/api/delivery/projects', session='session-new-synthetic')[0], 401)

    def test_mail_missing_and_extra_role_fields_fail_closed(self):
        self.assertFalse(self.call('/api/auth/status')[1]['email_configured'])
        registration = {'username': 'testuser', 'email': 'test@example.com', 'password': 'synthetic-passphrase'}
        self.assertEqual(self.call('/api/auth/register', registration, session=None)[0], 503)
        self.assertEqual(self.call('/api/auth/register', dict(registration, role='admin'), session=None)[0], 400)

    def test_auth_storage_failure_returns_safe_503_without_workspace_access(self):
        with patch.object(self.auth, 'authenticate', side_effect=ValueError('private internal path')):
            for path in ('/api/auth/status', '/api/delivery/projects'):
                status, value, _ = self.call(path)
                self.assertEqual(status, 503)
                self.assertEqual(value, {'ok': False, 'error': 'auth_storage_unavailable'})


class VerifiedMemberFlowTests(unittest.TestCase):
    def test_real_signup_email_verification_login_isolation_and_expiry(self):
        from channelshift.member_auth import MemberAuth, SESSION_TTL
        sent = []
        now = [1000000.0]
        with tempfile.TemporaryDirectory(prefix='channelshift-member-flow-') as folder:
            auth = MemberAuth(Path(folder) / 'auth.sqlite3', mailer=lambda address, token: sent.append((address, token)), clock=lambda: now[0])
            server = ThreadingHTTPServer(('127.0.0.1', 0), member_handler_factory(auth, Path(folder), 'csrf-flow'))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f'http://127.0.0.1:{server.server_port}'

            def request(path, body=None, cookie=''):
                headers = {'X-ChannelShift-Token': 'csrf-flow', 'Origin': origin, 'Cookie': cookie}
                raw = json.dumps(body).encode() if body is not None else None
                if raw is not None:
                    headers['Content-Type'] = 'application/json'
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
                conn.request('POST' if raw else 'GET', path, raw, headers)
                response = conn.getresponse()
                result = response.status, json.loads(response.read()), response.getheader('Set-Cookie', '').split(';')[0]
                conn.close()
                return result

            try:
                original, chosen = 'synthetic-original-passphrase', 'synthetic-owner-passphrase'
                self.assertEqual(request('/api/auth/register', {'username': 'alice', 'email': 'alice@example.com', 'password': original})[0], 200)
                self.assertEqual(request('/api/auth/login', {'username': 'alice', 'password': original})[0], 401)
                token = sent[-1][1]
                self.assertEqual(request('/api/auth/verify', {'token': token, 'password': chosen})[0], 200)
                self.assertEqual(request('/api/auth/verify', {'token': token, 'password': chosen})[0], 400)
                self.assertEqual(request('/api/auth/login', {'username': 'alice', 'password': original})[0], 401)
                status, body, alice = request('/api/auth/login', {'username': 'alice', 'password': chosen})
                self.assertEqual(status, 200)
                self.assertNotIn('session_token', body)
                self.assertTrue(alice)
                project = request('/api/delivery/projects', {'name': 'Alice private', 'client_request': 'private source'}, alice)[1]['project']
                self.assertEqual(request('/api/auth/register', {'username': 'bobby', 'email': 'bobby@example.com', 'password': original})[0], 200)
                self.assertEqual(request('/api/auth/verify', {'token': sent[-1][1], 'password': chosen})[0], 200)
                status, _, bobby = request('/api/auth/login', {'username': 'bobby', 'password': chosen})
                self.assertEqual(status, 200)
                self.assertEqual(request('/api/delivery/projects', cookie=bobby)[1]['items'], [])
                self.assertEqual(request('/api/delivery/projects/' + project['id'], cookie=bobby)[0], 400)
                self.assertEqual(request('/api/auth/logout', {}, bobby)[0], 200)
                self.assertEqual(request('/api/delivery/projects', cookie=bobby)[0], 401)
                now[0] += SESSION_TTL + 1
                self.assertEqual(request('/api/delivery/projects', cookie=alice)[0], 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
