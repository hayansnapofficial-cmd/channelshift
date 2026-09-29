"""Only member-scoped platform tokens access the shared service gateway."""
import hashlib
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from channelshift.member_auth import MemberAuth
from channelshift.member_web import member_handler_factory


class ServiceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        stub = patch('channelshift.member_auth._derive', side_effect=lambda pwd,salt: hashlib.sha256(salt+pwd).digest())
        stub.start(); self.addCleanup(stub.stop)
        self.auth = MemberAuth(Path(self.temp.name)/'auth.sqlite3')
        user = self.auth.bootstrap_master('synthetic', 'owner@example.test', 'Synthetic8')
        self.user = user['id']; self.session = self.auth.login('synthetic', 'Synthetic8')['session_token']
        self.services, self.codex = Mock(), Mock()
        self.services.status.return_value = {'requirements_review': {'available': True}, 'reference_collect': {'available': True}}
        self.services.collect_reference.return_value = {'text': 'Synthetic public reference', 'approved': False}
        self.services.review_for.return_value = Mock(return_value={'items': [], 'approved': False})
        handler = member_handler_factory(self.auth, self.temp.name, 'csrf-test', services=self.services, codex=self.codex)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        self.server.RequestHandlerClass.close_resources()

    def request(self,path,body=None,*,bearer=None,cookie=True,csrf=True,origin=None):
        headers={}
        if cookie: headers['Cookie']=f'channelshift_member_{self.server.server_port}={self.session}'
        if csrf:
            headers.update({'X-ChannelShift-Token':'csrf-test','Origin':f'http://127.0.0.1:{self.server.server_port}'})
        if origin: headers['Origin']=origin
        if bearer is not None: headers['Authorization']='Bearer '+bearer
        raw=None if body is None else json.dumps(body).encode()
        if raw is not None: headers['Content-Type']='application/json'
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        conn.request('GET' if raw is None else 'POST',path,raw,headers)
        reply=conn.getresponse(); result=reply.status,json.loads(reply.read());conn.close();return result

    def issue(self):
        status, result=self.request('/api/connections/mcp/create',{})
        self.assertEqual(status,200)
        return result['connection']['token']

    def test_issue_requires_session_and_csrf_status_never_returns_token(self):
        self.assertEqual(self.request('/api/connections/mcp/create',{},cookie=False)[0],401)
        self.assertEqual(self.request('/api/connections/mcp/create',{},csrf=False)[0],403)
        self.assertEqual(self.request('/api/connections/mcp/create',{'user_id':'b'*32})[0],400)
        token=self.issue()
        status,result=self.request('/api/connections/mcp')
        self.assertEqual(status,200);self.assertTrue(result['connection']['connected'])
        self.assertNotIn(token,json.dumps(result))
        self.assertEqual(self.request('/api/delivery/projects',cookie=False,bearer=token)[0],401)

    def test_gateway_ignores_cookies_requires_separate_token_and_keeps_fixed_identity(self):
        self.assertEqual(self.request('/api/services/status')[0],401)
        self.assertEqual(self.request('/api/services/status',bearer=self.session)[0],401)
        token=self.issue()
        self.assertEqual(self.request('/api/services/status',bearer=token,cookie=False,csrf=False)[0],200)
        self.assertEqual(self.request('/api/services/reference',{'url':'https://example.com'},bearer=token,cookie=False,csrf=False)[0],200)
        self.services.collect_reference.assert_called_once_with(self.user,'https://example.com')
        self.assertEqual(self.request('/api/services/reference',{'url':'https://example.com','member_id':'b'*32},bearer=token)[0],400)
        self.assertEqual(self.request('/api/services/reference',{'url':'https://example.com'},bearer=token,origin='https://hostile.example')[0],403)
        self.codex.extract_requirements.assert_not_called()

    def test_rotation_and_revocation_invalidate_prior_client_connections(self):
        first=self.issue();second=self.issue()
        self.assertEqual(self.request('/api/services/status',bearer=first)[0],401)
        self.assertEqual(self.request('/api/services/status',bearer=second)[0],200)
        self.assertEqual(self.request('/api/connections/mcp/revoke',{})[0],200)
        self.assertEqual(self.request('/api/services/status',bearer=second)[0],401)
        self.assertFalse(self.request('/api/connections/mcp')[1]['connection']['connected'])

    def test_service_json_rejects_top_level_and_nested_duplicate_keys(self):
        token = self.issue()
        bodies = [('/api/services/reference',
                   '{"url":"https://example.com/first","url":"https://example.com/last"}'),
                  ('/api/services/review',
                   '{"source":"text","requirements":[{"id":"REQ-1","text":"first",'
                   '"text":"PRIVATE-MARKER","quote":"text","origin":"client"}]}')]
        for path, body in bodies:
            with self.subTest(path=path):
                connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
                try:
                    connection.request('POST', path, body.encode('utf-8'),
                                       {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                    reply = connection.getresponse()
                    result = json.loads(reply.read())
                    self.assertEqual(reply.status, 400)
                    self.assertEqual(result, {'ok': False, 'error': 'service_invalid_input'})
                finally:
                    connection.close()
        self.services.collect_reference.assert_not_called()
        self.services.review_for.assert_not_called()


if __name__=='__main__':unittest.main()
