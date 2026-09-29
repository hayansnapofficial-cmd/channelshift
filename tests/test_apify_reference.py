import gzip
import importlib.util
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from channelshift import apify_reference as reference
from channelshift.service_errors import ServiceError


class ReferenceValidationTests(unittest.TestCase):
    def test_https_public_dns_only(self):
        invalid = ['http://www.python.org', 'https://user:pass@www.python.org',
                   'https://www.python.org:8443', 'https://localhost', 'https://localhost.localdomain',
                   'https://127.0.0.1', 'https://[::1]', 'https://100.64.0.1', 'https://169.254.169.254',
                   'https://[::ffff:127.0.0.1]', 'https://2130706433', 'https://0x7f000001',
                   'https://metadata.google.internal', 'https://machine.tailnet.ts.net',
                   'https://server.lan', 'https://server.home', 'https://foo.onion',
                   'https://foo.test', 'https://www.python.org.', 'https://www.python.org/#fragment',
                   'https://www.python.org/?access_token=secret', 'https://www.python.org\\@localhost',
                   ' https://www.python.org', 'https://www.python.org\n', {'url': 'https://www.python.org'}]
        for url in invalid:
            with self.subTest(url=url), self.assertRaisesRegex(ServiceError, '^reference_url_invalid$'):
                reference.normalize_public_url(url)
        self.assertEqual(reference.normalize_public_url('https://WWW.PYTHON.ORG:443/about'),
                         'https://www.python.org/about')

    def test_all_resolved_addresses_must_be_public(self):
        for ip in ('127.0.0.1', '10.0.0.1', '100.100.100.100', '169.254.169.254', '192.0.2.1',
                   '224.0.0.1', '::1', 'fc00::1', 'fe80::1', '::ffff:8.8.8.8'):
            family = socket.AF_INET6 if ':' in ip else socket.AF_INET
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                       (family, socket.SOCK_STREAM, 6, '', (ip, 443))]
            with self.subTest(ip=ip), patch.object(reference.socket, 'getaddrinfo', return_value=records):
                with self.assertRaisesRegex(ServiceError, '^reference_url_invalid$'):
                    reference._public_addresses('rebind.public.net')

    def preflight(self, status=200, content_type='text/html', length=None, chunks=None):
        response = MagicMock()
        response.status = status
        values = {'Content-Type': content_type, 'Content-Encoding': 'identity', 'Content-Length': length}
        response.getheader.side_effect = lambda key, default=None: values.get(key, default)
        response.read1.side_effect = chunks or [b'hello', b'']
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(reference, '_public_addresses', return_value=[(socket.AF_INET, ('8.8.8.8', 443))]), \
             patch.object(reference, '_PinnedHTTPS', return_value=connection):
            reference.preflight('https://www.python.org/about')
        return response, connection

    def test_preflight_refuses_redirects_and_large_or_non_html_content(self):
        for kwargs, code in [({'status': 302}, 'reference_redirect_refused'),
                             ({'length': str(reference.MAX_PAGE_BYTES + 1)}, 'reference_too_large'),
                             ({'content_type': 'application/pdf'}, 'reference_url_unavailable'),
                             ({'chunks': [b'x' * (reference.MAX_PAGE_BYTES + 1)]}, 'reference_too_large')]:
            with self.subTest(kwargs=str(kwargs)[:80]), self.assertRaisesRegex(ServiceError, '^' + code + '$'):
                self.preflight(**kwargs)

    def test_preflight_get_has_no_credentials_or_cookies(self):
        response, connection = self.preflight()
        args, kwargs = connection.request.call_args
        self.assertEqual(args, ('GET', '/about'))
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertNotIn('Cookie', kwargs['headers'])
        connection.close.assert_called_once()

    def test_tcp_connection_is_pinned_and_tls_verifies_hostname(self):
        with patch.object(reference.socket, 'socket') as sock, patch.object(reference.ssl, 'create_default_context') as context:
            connection = reference._PinnedHTTPS('www.python.org', (socket.AF_INET, ('8.8.8.8', 443)))
            connection.connect()
            sock.return_value.connect.assert_called_once_with(('8.8.8.8', 443))
            context.return_value.wrap_socket.assert_called_once_with(sock.return_value, server_hostname='www.python.org')

    def test_credentials_missing_is_not_fake_connected(self):
        with patch.object(reference, '_server_setting', return_value=''):
            self.assertFalse(reference.configured())
            with self.assertRaisesRegex(ServiceError, '^service_not_configured$'):
                reference._credential()

    def test_credential_file_is_explicit_bounded_and_not_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-token'
            path.write_text('TEST-PRIVATE\n', encoding='ascii')
            path.chmod(0o600)
            with patch.object(reference, '_server_setting', side_effect=lambda name: str(path) if name == 'APIFY_TOKEN_FILE' else ''):
                self.assertEqual(reference._credential(), 'TEST-PRIVATE')
            path.write_text('x' * 515, encoding='ascii')
            self.assertEqual(reference._credential_file(str(path)), '')
            self.assertEqual(reference._credential_file('relative-token-file'), '')
            self.assertEqual(reference._credential_file(directory), '')

    def test_credential_file_refuses_reparse_points_before_reading(self):
        info = MagicMock(st_mode=0o100600, st_file_attributes=0x400)
        absolute = str(Path.cwd() / 'operator-secret-file')
        with patch.object(Path, 'lstat', return_value=info), patch.object(reference.os, 'open') as opened:
            self.assertEqual(reference._credential_file(absolute), '')
        opened.assert_not_called()


class ReferenceApiTests(unittest.TestCase):
    def fake_client(self, *, item=None, status='SUCCEEDED'):
        client, transport = MagicMock(), MagicMock()
        client.actor.return_value.start.return_value = {'id': 'run123', 'status': status, 'defaultDatasetId': 'dataset123'}
        client.run.return_value.wait_for_finish.return_value = {'id': 'run123', 'status': status, 'defaultDatasetId': 'dataset123'}
        client.dataset.return_value.list_items.return_value.items = [item or {
            'url': 'https://www.python.org/', 'text': '참고용 자료', 'metadata': {'title': '공개 자료'},
            'crawl': {'loadedUrl': 'https://www.python.org/', 'httpStatusCode': 200}}]
        return client, transport

    def collect(self, client):
        with patch.object(reference, '_credential', return_value='TEST-PRIVATE'), \
             patch.object(reference, 'preflight'), patch.object(reference, '_sdk_client', return_value=client):
            return reference.collect_reference('https://www.python.org/')

    def test_fixed_actor_one_page_no_recursion_files_or_ai(self):
        client, transport = self.fake_client()
        result = self.collect((client, transport))
        self.assertEqual(result['kind'], 'reference')
        self.assertFalse(result['approved'])
        self.assertNotIn('TEST-PRIVATE', json.dumps(result))
        self.assertNotIn('dataset123', json.dumps(result))
        client.actor.assert_called_once_with(reference.ACTOR)
        options = client.actor.return_value.start.call_args.kwargs
        self.assertEqual(options['timeout_secs'], 60)
        self.assertEqual(options['memory_mbytes'], 256)
        payload = options['run_input']
        self.assertEqual((payload['maxCrawlDepth'], payload['maxCrawlPages'], payload['maxResults']), (0, 1, 1))
        for key in ('saveFiles', 'useSitemaps', 'useLlmsTxt', 'summarize', 'saveScreenshots'):
            self.assertFalse(payload[key])
        self.assertEqual(payload['crawlerType'], 'cheerio')
        client.dataset.assert_called_once_with('dataset123')
        self.assertEqual(client.dataset.return_value.list_items.call_args.kwargs['limit'], 1)
        client.run.return_value.abort.assert_not_called()
        transport.close.assert_called_once()

    def test_redirected_result_and_provider_secret_are_rejected(self):
        client, transport = self.fake_client()
        client.dataset.return_value.list_items.return_value.items[0]['crawl']['loadedUrl'] = 'https://www.other.org/'
        with self.assertRaisesRegex(ServiceError, '^reference_redirect_refused$'):
            self.collect((client, transport))
        client, transport = self.fake_client()
        client.actor.return_value.start.side_effect = RuntimeError('TEST-PRIVATE')
        with self.assertRaisesRegex(ServiceError, '^service_unavailable$'):
            self.collect((client, transport))

    def test_failed_and_timed_out_runs_do_not_fetch_datasets(self):
        for status in ('FAILED', 'TIMED-OUT', 'ABORTED'):
            client, transport = self.fake_client(status=status)
            with self.subTest(status=status), self.assertRaisesRegex(ServiceError, '^service_unavailable$'):
                self.collect((client, transport))
            client.dataset.assert_not_called()

    def test_output_is_bounded_and_never_contains_remote_html_or_controls(self):
        client, transport = self.fake_client()
        item = client.dataset.return_value.list_items.return_value.items[0]
        item.update(text='a' * 20000, html='<script>steal()</script>', apiToken='PRIVATE')
        result = self.collect((client, transport))
        self.assertEqual(len(result['text']), 12000)
        self.assertTrue(result['truncated'])
        self.assertNotIn('html', result)
        self.assertNotIn('apiToken', result)

    def test_api_transport_refuses_redirects_and_caps_response_bytes(self):
        for status, body, code in [(302, b'', 'service_unavailable'),
                                   (200, b'x' * (reference.MAX_PAGE_BYTES + 1), 'reference_too_large')]:
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body)),
                                 follow_redirects=False)
            self.addCleanup(client.close)
            transport = reference._BoundedApiTransport(client, time.monotonic() + 10)
            with self.subTest(status=status), self.assertRaisesRegex(ServiceError, '^' + code + '$'):
                transport.request(method='GET', url='https://api.apify.com/v2/datasets/d/items',
                                  headers={}, content=None, timeout=5, stream=False)

    @unittest.skipUnless(importlib.util.find_spec('apify_client'), 'optional client not installed')
    def test_real_sdk_with_fake_http_uses_supported_request_and_response_shapes(self):
        real_httpx = httpx.Client
        requests = []
        def respond(request):
            requests.append(request)
            if request.url.path == '/v2/acts/apify~website-content-crawler/runs':
                payload = gzip.decompress(request.content) if request.headers.get('content-encoding') == 'gzip' else request.content
                self.assertEqual(json.loads(payload)['maxCrawlPages'], 1)
                return httpx.Response(201, json={'data': {'id': 'run123', 'status': 'RUNNING', 'defaultDatasetId': 'dataset123'}})
            if request.url.path == '/v2/actor-runs/run123':
                self.assertEqual(request.url.params.get('waitForFinish'), '5')
                return httpx.Response(200, json={'data': {'id': 'run123', 'status': 'SUCCEEDED', 'defaultDatasetId': 'dataset123'}})
            if request.url.path == '/v2/datasets/dataset123/items':
                return httpx.Response(200, json=[{'url': 'https://www.python.org/', 'text': 'Text',
                    'metadata': {'title': 'Title'}, 'crawl': {'loadedUrl': 'https://www.python.org/', 'httpStatusCode': 200}}],
                    headers={'x-apify-pagination-count': '1', 'x-apify-pagination-offset': '0',
                             'x-apify-pagination-limit': '1', 'x-apify-pagination-total': '1',
                             'x-apify-pagination-desc': 'false'})
            raise AssertionError('Unexpected external request path')
        def make_httpx(**kwargs):
            self.assertIs(kwargs['trust_env'], False)
            self.assertIs(kwargs['follow_redirects'], False)
            return real_httpx(transport=httpx.MockTransport(respond), **kwargs)
        with patch.object(reference, '_credential', return_value='TEST-PRIVATE'), \
             patch.object(reference, 'preflight'), patch('httpx.Client', side_effect=make_httpx):
            result = reference.collect_reference('https://www.python.org/')
        self.assertEqual(result['text'], 'Text')
        self.assertEqual(len(requests), 3)
        for request in requests:
            self.assertEqual(request.headers['Authorization'], 'Bearer TEST-PRIVATE')
            self.assertEqual(request.url.host, 'api.apify.com')


if __name__ == '__main__':
    unittest.main()
