import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from channelshift.service_client import (ServiceClient, ServiceClientError, MAX_RESPONSE,
                                       TIMEOUT_SECONDS, _destination, _token)


class ServiceClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'connection-token'
        self.token = 'cs_mcp_' + 'A' * 43
        self.path.write_text(self.token + '\n', encoding='ascii')
        self.path.chmod(0o600)
        self.env = patch.dict(os.environ, {'CHANNELSHIFT_SERVICE_TOKEN_FILE': str(self.path),
                                          'CHANNELSHIFT_SERVICE_URL': 'http://127.0.0.1:5189',
                                          'APIFY_TOKEN': 'VENDOR-SECRET',
                                          'TYPESAFE_API_KEY': 'OTHER-VENDOR-SECRET'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = '관리자만 문의를 볼 수 있습니다.'
        self.rows = [{'id': 'REQ-1', 'text': '관리자 문의 조회', 'quote': self.source, 'origin': 'client'}]

    def request(self, callback, response=None, status=200, content=None, headers=None):
        requests = []
        real_client = httpx.Client
        def respond(request):
            requests.append(request)
            if content is not None:
                return httpx.Response(status, content=content, headers=headers or {'Content-Type': 'application/json'})
            return httpx.Response(status, json=response)
        def make_client(**kwargs):
            self.assertFalse(kwargs['trust_env'])
            self.assertFalse(kwargs['follow_redirects'])
            self.assertEqual(kwargs['timeout'], TIMEOUT_SECONDS)
            return real_client(transport=httpx.MockTransport(respond), **kwargs)
        with patch('channelshift.service_client.httpx.Client', side_effect=make_client):
            result = callback(ServiceClient())
        for request in requests:
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + self.token)
            self.assertNotIn('Cookie', request.headers)
            self.assertNotIn('VENDOR-SECRET', str(request.headers))
            self.assertNotIn('VENDOR-SECRET', request.content.decode())
        return result, requests

    def test_configured_status_makes_authenticated_get(self):
        services = {'requirements_review': {'available': True, 'label': '요구사항 검토', 'status': 'configured'}}
        result, requests = self.request(lambda client: client.status(), {'ok': True, 'services': services})
        self.assertEqual(result, services)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, 'GET')
        self.assertEqual(str(requests[0].url), 'http://127.0.0.1:5189/api/services/status')
        self.assertEqual(requests[0].content, b'')

    def test_review_sends_only_explicit_bounded_input(self):
        result, requests = self.request(lambda client: client.review_requirements(self.source, self.rows),
                                       {'ok': True, 'result': {'status': 'REVIEW_REQUIRED', 'approved': False}})
        self.assertFalse(result['approved'])
        self.assertEqual(requests[0].url.path, '/api/services/review')
        self.assertEqual(requests[0].method, 'POST')
        self.assertEqual(json.loads(requests[0].content), {'source': self.source, 'requirements': self.rows})

    def test_collection_calls_platform_not_reference_host(self):
        result, requests = self.request(lambda client: client.collect_reference('https://WWW.PYTHON.ORG'),
                                       {'ok': True, 'result': {'kind': 'reference', 'text': 'content', 'approved': False}})
        self.assertEqual(result['kind'], 'reference')
        self.assertEqual(requests[0].url.host, '127.0.0.1')
        self.assertEqual(requests[0].url.path, '/api/services/reference')
        self.assertEqual(json.loads(requests[0].content), {'url': 'https://www.python.org/'})

    def test_missing_token_and_wrong_vendor_token_do_not_connect(self):
        with patch('channelshift.service_client.httpx.Client') as transport:
            for content in ('', 'APIFY-PRIVATE-KEY' + 'x' * 35, 'cs_mcp_' + 'x' * 44, 'cs_mcp_' + 'x' * 42):
                self.path.write_text(content, encoding='ascii')
                with self.subTest(content_length=len(content)), self.assertRaisesRegex(ServiceClientError, '^service_connection_required$'):
                    ServiceClient().status()
            self.path.unlink()
            with self.assertRaisesRegex(ServiceClientError, '^service_connection_required$'):
                ServiceClient().status()
        transport.assert_not_called()

    def test_file_is_absolute_small_regular_and_not_reparse(self):
        for path in ('relative-token', self.temp.name):
            with patch.dict(os.environ, {'CHANNELSHIFT_SERVICE_TOKEN_FILE': path}):
                with self.assertRaisesRegex(ServiceClientError, '^service_connection_required$'):
                    _token()
        self.path.write_text('a' * 4096, encoding='ascii')
        with self.assertRaisesRegex(ServiceClientError, '^service_connection_required$'):
            _token()
        info = MagicMock(st_mode=0o100600, st_file_attributes=0x400)
        with patch.object(Path, 'lstat', return_value=info), patch('channelshift.service_client.os.open') as opened:
            with self.assertRaisesRegex(ServiceClientError, '^service_connection_required$'):
                _token()
        opened.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX file mode checks')
    def test_world_readable_token_file_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ServiceClientError, '^service_connection_required$'):
            _token()

    def test_destination_only_accepts_configured_https_or_numeric_loopback_http(self):
        invalid = ['http://localhost:5189', 'http://example.com:5189', 'http://127.0.0.1',
                   'http://127.1:5189', 'https://user:secret@example.com', 'https://example.com/path',
                   'https://example.com/?token=PRIVATE', 'https://example.com/#x',
                   'file:///tmp', 'https://example.com:0', 'https://example..com',
                   'https://example.com\\@localhost', 'https://example.com\n']
        for url in invalid:
            with self.subTest(url=url), patch.dict(os.environ, {'CHANNELSHIFT_SERVICE_URL': url}):
                with self.assertRaisesRegex(ServiceClientError, '^service_connection_invalid$'):
                    _destination()
        with patch.dict(os.environ, {'CHANNELSHIFT_SERVICE_URL': 'https://platform.example.com:8443/'}):
            self.assertEqual(_destination(), 'https://platform.example.com:8443')

    def test_invalid_review_or_reference_never_connects(self):
        with patch('channelshift.service_client.httpx.Client') as transport:
            for source, rows in [('', self.rows), ('x' * 12001, self.rows), (self.source, []),
                                 (self.source, self.rows * 33), (self.source, ['PRIVATE-MARKER']),
                                 (self.source, [dict(self.rows[0], actor='arbitrary')]),
                                 (self.source, [dict(self.rows[0], quote='fabricated')])]:
                with self.assertRaisesRegex(ServiceClientError, '^service_invalid_input$'):
                    ServiceClient().review_requirements(source, rows)
            with self.assertRaisesRegex(ServiceClientError, '^reference_url_invalid$'):
                ServiceClient().collect_reference('https://127.0.0.1')
        transport.assert_not_called()

    def test_http_errors_and_redirects_have_safe_codes(self):
        for status, code in [(401, 'service_connection_required'), (403, 'service_connection_required'),
                             (302, 'service_unavailable'), (429, 'service_rate_limited'), (500, 'service_unavailable')]:
            with self.subTest(status=status), self.assertRaisesRegex(ServiceClientError, '^' + code + '$'):
                self.request(lambda client: client.status(), status=status, content=b'PRIVATE-MARKER')
        with self.assertRaisesRegex(ServiceClientError, '^service_busy$'):
            self.request(lambda client: client.status(), {'ok': False, 'error': 'service_busy'}, status=503)
        with self.assertRaisesRegex(ServiceClientError, '^service_unavailable$'):
            self.request(lambda client: client.status(), {'ok': False, 'error': 'PRIVATE-MARKER'}, status=503)

    def test_429_preserves_safe_busy_code_and_falls_back_for_malformed_body(self):
        for code in ('service_busy', 'service_rate_limited'):
            with self.subTest(code=code), self.assertRaisesRegex(ServiceClientError, '^' + code + '$'):
                self.request(lambda client: client.status(), {'ok': False, 'error': code}, status=429)
        for body in (b'PRIVATE-MARKER', b'[]', b'{"ok":false,"error":"PRIVATE-MARKER"}',
                     b'{"ok":false,"error":"service_busy","error":"service_rate_limited"}'):
            with self.subTest(body=body[:25]), self.assertRaisesRegex(ServiceClientError, '^service_rate_limited$'):
                self.request(lambda client: client.status(), status=429, content=body)

    def test_strict_response_shapes_limits_and_token_reflection_are_rejected(self):
        bodies = [b'[]', b'{"ok":1,"services":{}}', b'{"ok":true,"services":[]}',
                  b'{"ok":false,"ok":true,"services":{}}', b'{"ok":true,"services":{"score":NaN}}',
                  b'x' * (MAX_RESPONSE + 1),
                  json.dumps({'ok': True, 'services': {'secret': self.token}}).encode(),
                  ('{"ok":true,"services":{"secret":"\\u0063' + self.token[1:] + '"}}').encode()]
        for body in bodies:
            with self.subTest(body=body[:25]), self.assertRaisesRegex(ServiceClientError, '^service_unavailable$'):
                self.request(lambda client: client.status(), content=body)

    def test_transport_failures_do_not_expose_raw_exceptions(self):
        with patch('channelshift.service_client.httpx.Client', side_effect=httpx.ConnectError('PRIVATE-MARKER')):
            with self.assertRaisesRegex(ServiceClientError, '^service_unavailable$'):
                ServiceClient().status()


if __name__ == '__main__':
    unittest.main()
