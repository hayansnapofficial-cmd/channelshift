"""Loopback membership pilot with verified accounts and isolated workspaces.

This is not a public hosting server. Legacy single-operator data is untouched.
Host Codex/TypeSafe credentials are never delegated to registered members.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import threading
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path

from .delivery_profile import standard_site_profile
from .delivery_workspace import DeliveryWorkspace
from .store import ProjectStore, directory
from .web import WEB, handler_factory

AUTH_ASSETS = {'/login': ('login.html', 'text/html; charset=utf-8'),
               '/login.js': ('login.js', 'text/javascript; charset=utf-8'),
               '/login.css': ('login.css', 'text/css; charset=utf-8')}
AUTH_INPUTS = {'/api/auth/register': {'username', 'email', 'password'},
               '/api/auth/login': {'username', 'password'},
               '/api/auth/verify': {'token', 'password'},
               '/api/auth/resend': {'email'}, '/api/auth/logout': set()}
AUTH_ERRORS = {'invalid_member_input': 400, 'invalid_input': 400, 'invalid_username': 400,
               'invalid_email': 400, 'invalid_password': 400, 'invalid_credentials': 401,
               'invalid_verification_token': 400, 'rate_limited': 429,
               'email_not_configured': 503, 'email_delivery_failed': 503,
               'member_storage_limit': 429, 'registration_unavailable': 429,
               'auth_busy': 429, 'auth_unavailable': 503,
               'auth_storage_unavailable': 503, 'unsafe_auth_storage': 503}


def _disabled_provider(*_):
    raise ValueError('member_provider_not_linked')


def member_handler_factory(auth, root, token=None):
    root = Path(root)
    token = token or secrets.token_urlsafe(32)
    base = handler_factory(token=token)
    members = {}
    members_lock = threading.Lock()

    def workspace_handler(user):
        user_id = user['id']
        if not isinstance(user_id, str) or not re.fullmatch(r'[a-f0-9]{32}', user_id):
            raise ValueError('invalid_member_input')
        with members_lock:
            if user_id not in members:
                member_root = root / 'workspaces' / user_id
                for folder in (root, root / 'workspaces', member_root):
                    if folder.is_symlink():
                        raise ValueError('unsafe_storage')
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if os.name != 'nt' and folder.stat().st_mode & 0o077:
                        raise ValueError('unsafe_storage')
                delivery = DeliveryWorkspace(member_root / 'delivery.sqlite3', extract=_disabled_provider,
                                             review=_disabled_provider)
                members[user_id] = handler_factory(ProjectStore(member_root / 'schemas'), token, delivery)
            return members[user_id]

    class MemberHandler(base):
        pending_cookie = None
        redirect_to = None

        def end_headers(self):
            if self.pending_cookie:
                self.send_header('Set-Cookie', self.pending_cookie)
            if self.redirect_to:
                self.send_header('Location', self.redirect_to)
            super().end_headers()

        @property
        def cookie_name(self):
            return 'channelshift_member_' + str(self.server.server_port)

        def session_token(self):
            raw = self.headers.get('Cookie', '')
            if len(raw) > 4096:
                return ''
            try:
                cookie = SimpleCookie(raw)
                value = cookie.get(self.cookie_name)
                return value.value if value else ''
            except Exception:
                return ''

        def set_session_cookie(self, value, age=28800):
            # Local-only HTTP preview. Production requires TLS and Secure cookies.
            cookie = SimpleCookie()
            cookie[self.cookie_name] = value
            cookie[self.cookie_name]['path'] = '/'
            cookie[self.cookie_name]['httponly'] = True
            cookie[self.cookie_name]['samesite'] = 'Strict'
            cookie[self.cookie_name]['max-age'] = age
            self.pending_cookie = cookie.output(header='').strip()

        def session_user(self):
            try:
                return auth.authenticate(self.session_token())
            except Exception:
                self.send(503, {'ok': False, 'error': 'auth_storage_unavailable'})
                return False

        def authenticated_user(self):
            user = self.session_user()
            if user is False:
                return None
            if user:
                return user
            if self.path.startswith('/api/'):
                self.send(401, {'ok': False, 'error': 'login_required'})
            else:
                self.redirect_to = '/login'
                self.send(302, {'ok': False, 'error': 'login_required'})
            return None

        def do_GET(self):
            if not self.guard(api=self.path.startswith('/api/')):
                return
            if self.path in AUTH_ASSETS:
                name, mime = AUTH_ASSETS[self.path]
                payload = (WEB / name).read_bytes().replace(b'__CHANNELSHIFT_TOKEN__', token.encode('ascii'))
                self.send(200, payload, mime)
                return
            if self.path == '/api/auth/status':
                user = self.session_user()
                if user is False:
                    return
                self.send(200, {'ok': True, 'email_configured': auth.mailer is not None,
                                'user': user})
                return
            if self.path == '/health':
                self.send(200, {'ok': True, 'service': 'channelshift-members-local'})
                return
            user = self.authenticated_user()
            if not user:
                return
            if self.path == '/api/delivery/status':
                self.send(200, {'ok': True, 'member_mode': True,
                                'codex': {'can_execute': False, 'available': False,
                                          'reason': 'member_provider_not_linked'}, 'jev_configured': False,
                                'stages': [{'id': s['id'], 'title': s['title']}
                                           for s in standard_site_profile()['stages']]})
                return
            if self.path in {'/', '/delivery'}:
                name = 'index.html' if self.path == '/' else 'delivery.html'
                payload = (WEB / name).read_text(encoding='utf-8')
                payload = payload.replace('__CHANNELSHIFT_TOKEN__', token)
                payload = payload.replace('</header>', '<a class="text-button" href="/login">내 계정</a></header>', 1)
                self.send(200, payload.encode('utf-8'), 'text/html; charset=utf-8')
                return
            try:
                workspace_handler(user).do_GET(self)
            except (ValueError, OSError):
                self.send(400, {'ok': False, 'error': 'operation_failed'})

        def do_POST(self):
            if not self.guard(api=True, write=True):
                return
            if self.path.startswith('/api/auth/'):
                self.auth_post()
                return
            user = self.authenticated_user()
            if not user:
                return
            if self.path in {'/api/delivery/extract', '/api/delivery/jev'}:
                self.send(403, {'ok': False, 'error': 'member_provider_not_linked'})
                return
            try:
                workspace_handler(user).do_POST(self)
            except (ValueError, OSError):
                self.send(400, {'ok': False, 'error': 'operation_failed'})

        def auth_post(self):
            fields = AUTH_INPUTS.get(self.path)
            if fields is None:
                self.send(404, {'ok': False, 'error': 'not_found'})
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json' or self.headers.get('Transfer-Encoding'):
                self.send(415, {'ok': False, 'error': 'json_required'})
                return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 < length <= 4096:
                    self.send(413, {'ok': False, 'error': 'payload_too_large'})
                    return
                self.connection.settimeout(10)
                data = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if type(data) is not dict or set(data) != fields:
                    raise ValueError('invalid_member_input')
                if self.path == '/api/auth/logout':
                    auth.logout(self.session_token())
                    self.set_session_cookie('', 0)
                    result = {}
                elif self.path == '/api/auth/login':
                    result = auth.login(**data)
                    previous = self.session_token()
                    if previous:
                        auth.logout(previous)
                    self.set_session_cookie(result['session_token'])
                    result = {'user': result['user']}
                elif self.path == '/api/auth/register':
                    result = auth.register(**data)
                elif self.path == '/api/auth/verify':
                    result = auth.verify(**data)
                else:
                    result = auth.resend(**data)
                self.send(200, {'ok': True, **result})
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                code = str(error) if str(error) in AUTH_ERRORS else 'invalid_member_input'
                self.send(AUTH_ERRORS[code], {'ok': False, 'error': code})
            except Exception:
                self.send(503, {'ok': False, 'error': 'operation_failed'})

    return MemberHandler


def main():
    from .member_auth import MemberAuth
    from .member_mail import from_environment
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5189)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    root = directory() / 'members'
    origin = 'http://127.0.0.1:' + str(args.port)
    auth = MemberAuth(root / 'accounts.sqlite3', mailer=from_environment(origin))
    with ThreadingHTTPServer(('127.0.0.1', args.port), member_handler_factory(auth, root)) as server:
        print('ChannelShift members: ' + origin + '/login', file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
