"""Public-origin transport boundaries over a real loopback HTTP listener."""
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from channelshift.member_web import main, member_handler_factory
from channelshift.web import normalize_public_origin


class Identity:
    mailer = None

    def __init__(self):
        self.user = {'id': 'a' * 32, 'username': 'alice', 'email': 'alice@example.test'}
        self.sessions = {'synthetic-session-token': self.user}

    def authenticate(self, token):
        return self.sessions.get(token)

    def active_member(self, member_id):
        return member_id == self.user['id']

    def login(self, **_):
        return {'session_token': 'synthetic-session-token', 'user': self.user}

    def logout(self, token):
        self.sessions.pop(token, None)


class PublicOriginTests(unittest.TestCase):
    def test_https_origin_normalization_and_rejection(self):
        for supplied, expected in (
                ('https://CHANNELSHIFT.example/', 'https://channelshift.example'),
                ('https://channelshift.example:443', 'https://channelshift.example'),
                ('https://channelshift.example:8443', 'https://channelshift.example:8443')):
            self.assertEqual(normalize_public_origin(supplied), expected)
        for value in (None, '', 'http://channelshift.example', '//channelshift.example',
                      'https://', 'https://channelshift.example/app',
                      'https://user@channelshift.example', 'https://user:pass@channelshift.example',
                      'https://channelshift.example?next=evil', 'https://channelshift.example#fragment',
                      'https://channelshift.example?', 'https://channelshift.example#',
                      'https://channelshift.example:0', 'https://channelshift.example:65536',
                      'https://channelshift.example:', 'https://channelshift.example:0443',
                      'https://channelshift.example.', 'https://-bad.example',
                      'https://bad..example', 'https://bad_name.example',
                      'https://채널.example', ' https://channelshift.example',
                      'https://channelshift.example\r\nHost:evil.example',
                      'https://channelshift.example\\@evil.example'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '^invalid_public_origin$'):
                normalize_public_origin(value)

    def test_launcher_keeps_loopback_and_configures_mail_with_public_origin(self):
        with patch('sys.argv', ['members', '--port', '5199', '--public-origin', 'https://channelshift.example/']), \
                patch('channelshift.member_web.directory', return_value=Path('/unused')), \
                patch('channelshift.member_mail.from_environment') as mail, \
                patch('channelshift.member_auth.MemberAuth') as auth, \
                patch('channelshift.member_web.member_handler_factory') as handler, \
                patch('channelshift.member_web.ThreadingHTTPServer') as server:
            server.return_value.__enter__.return_value.serve_forever.side_effect = KeyboardInterrupt
            main()
            mail.assert_called_once_with('https://channelshift.example')
            self.assertEqual(handler.call_args.kwargs['public_origin'], 'https://channelshift.example')
            self.assertEqual(server.call_args.args[0], ('127.0.0.1', 5199))
            self.assertEqual(auth.call_args.kwargs['mailer'], mail.return_value)
            handler.return_value.close_resources.assert_called_once()

    def test_environment_public_origin_is_validated_before_storage(self):
        with patch('sys.argv', ['members']), \
                patch.dict('os.environ', {'CHANNELSHIFT_PUBLIC_ORIGIN': 'http://unsafe.example'}), \
                patch('channelshift.member_web.directory') as directory, \
                patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                main()
            directory.assert_not_called()


class PublicMemberHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-public-http-')
        self.addCleanup(self.temp.cleanup)
        self.auth = Identity()
        self.handler = member_handler_factory(self.auth, Path(self.temp.name), 'csrf-synthetic',
            codex=Mock(), services=Mock(), public_origin='https://channelshift.example')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.handler.close_resources()

    def call(self, path, data=None, *, extra=(), omit=()):
        headers = {'Host': 'channelshift.example', 'Origin': 'https://channelshift.example',
                   'X-ChannelShift-Token': 'csrf-synthetic',
                   'Cookie': '__Host-channelshift_member=synthetic-session-token'}
        for name in omit:
            headers.pop(name, None)
        body = json.dumps(data).encode() if data is not None else b''
        if data is not None:
            headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(body))})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        conn.putrequest('POST' if data is not None else 'GET', path, skip_host=True)
        for key, value in [*headers.items(), *extra]:
            conn.putheader(key, value)
        conn.endheaders(body)
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def test_https_proxy_serves_login_and_authenticated_project_routes(self):
        status, body, headers = self.call('/login', omit=('Cookie', 'Origin'))
        self.assertEqual(status, 200)
        self.assertIn(b'csrf-synthetic', body)
        self.assertEqual(headers['Strict-Transport-Security'], 'max-age=31536000')
        status, body, _ = self.call('/api/studio/projects', {
            'name': 'Public synthetic', 'client_request': 'Build an inquiry page.', 'site_type': 'service'})
        self.assertEqual(status, 200)
        project_id = json.loads(body)['project']['id']
        self.assertEqual(self.call('/api/studio/projects/' + project_id)[0], 200)
        self.assertEqual(self.call('/api/studio/projects/' + project_id, omit=('Cookie',))[0], 401)

    def test_exact_host_origin_and_csrf_required_despite_forwarded_headers(self):
        forwarded = [('Forwarded', 'host=channelshift.example;proto=https'),
                     ('X-Forwarded-Host', 'channelshift.example'), ('X-Forwarded-Proto', 'https')]
        data = {'username': 'alice', 'password': 'synthetic-password'}
        variants = [
            (('Host',), [('Host', 'evil.example')]),
            (('Host',), [('Host', '127.0.0.1:' + str(self.server.server_port))]),
            (('Origin',), [('Origin', 'https://evil.example')]),
            (('Origin',), [('Origin', 'http://channelshift.example')]),
            (('Origin',), []),
            (('X-ChannelShift-Token',), [('X-ChannelShift-Token', 'wrong')]),
            ((), [('Host', 'channelshift.example')]),
            ((), [('Origin', 'https://channelshift.example')]),
        ]
        for omit, extra in variants:
            with self.subTest(omit=omit, extra=extra):
                self.assertEqual(self.call('/api/auth/login', data,
                    omit=omit, extra=extra + forwarded)[0], 403)
        self.assertEqual(self.call('/health', omit=('Origin',),
            extra=[('X-Forwarded-Host', 'evil.example'), ('X-Forwarded-Proto', 'http')])[0], 200)

    def test_secure_host_cookie_is_used_for_login_and_logout(self):
        status, body, headers = self.call('/api/auth/login',
            {'username': 'alice', 'password': 'synthetic-password'}, omit=('Cookie',))
        self.assertEqual(status, 200)
        self.assertNotIn(b'synthetic-session-token', body)
        cookie = headers['Set-Cookie']
        for flag in ('__Host-channelshift_member=', 'HttpOnly', 'SameSite=Strict', 'Path=/', 'Secure'):
            self.assertIn(flag, cookie)
        self.assertNotIn('Domain=', cookie)
        status, _, headers = self.call('/api/auth/logout', {})
        self.assertEqual(status, 200)
        for flag in ('__Host-channelshift_member=', 'Max-Age=0', 'Secure'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(self.call('/api/studio/projects')[0], 401)


if __name__ == '__main__':
    unittest.main()
