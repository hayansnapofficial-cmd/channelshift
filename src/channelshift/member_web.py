"""Loopback member service, optionally behind a local HTTPS reverse proxy.

An explicit public origin is required for HTTPS deployment. Legacy data is untouched.
Codex identities are per member. Shared service credentials remain server-side.
"""
from __future__ import annotations

import argparse
import ipaddress
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
from .member_auth import normalize_client_ip
from .delivery_workspace import DeliveryWorkspace
from .store import ProjectStore, directory
from .web import WEB, handler_factory, normalize_public_origin

AUTH_ASSETS = {'/login': ('login.html', 'text/html; charset=utf-8'),
               '/login.js': ('login.js', 'text/javascript; charset=utf-8'),
               '/login.css': ('login.css', 'text/css; charset=utf-8')}
ADMIN_ASSETS = {'/admin': ('admin.html', 'text/html; charset=utf-8'),
                '/admin.js': ('admin.js', 'text/javascript; charset=utf-8'),
                '/admin.css': ('admin.css', 'text/css; charset=utf-8')}
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
               'auth_storage_unavailable': 503, 'unsafe_auth_storage': 503,
               'admin_forbidden': 403, 'master_protected': 403,
               'member_not_found': 404, 'invalid_admin_request': 400}


def member_handler_factory(auth, root, token=None, *, codex=None, services=None, public_origin=None,
                           trusted_proxy=False):
    from .member_codex import MemberCodex
    from .shared_services import SharedServices, ServiceError
    public_origin = normalize_public_origin(public_origin) if public_origin is not None else None
    if trusted_proxy and public_origin is None:
        raise ValueError('invalid_proxy_configuration')
    root = Path(root)
    codex = codex if codex is not None else MemberCodex(root)
    services = services if services is not None else SharedServices(root / 'shared-services.sqlite3')
    token = token or secrets.token_urlsafe(32)
    base = handler_factory(token=token, public_origin=public_origin)
    members = {}
    workspaces = {}
    pipelines = {}
    members_lock = threading.Lock()

    def ensure_active(user_id):
        if not auth.active_member(user_id):
            raise ServiceError('service_unavailable')

    def extract_for(user_id, text):
        ensure_active(user_id)
        return codex.extract_requirements(user_id, text)

    def extract_guided_for(user_id, text, guide_descriptor):
        ensure_active(user_id)
        return codex.extract_requirements(user_id, text, guide_descriptor=guide_descriptor)

    def generate_erd_for(user_id, snapshot, database):
        ensure_active(user_id)
        return codex.generate_erd(user_id, snapshot, database)

    def generate_stage_for(user_id, stage, spec, dependencies):
        ensure_active(user_id)
        return codex.generate_stage(user_id, stage, spec, dependencies)

    def review_for(user_id, source, requirements):
        ensure_active(user_id)
        return services.review_for(user_id)(source, requirements)

    def collect_for(user_id, url):
        ensure_active(user_id)
        return services.collect_reference(user_id, url)

    def advise_for(user_id, stage, context):
        ensure_active(user_id)
        return services.advise_workflow(user_id, stage, context)

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
                delivery = DeliveryWorkspace(member_root / 'delivery.sqlite3',
                    extract=lambda text: extract_for(user_id, text),
                    extract_guided=lambda text, descriptor: extract_guided_for(user_id, text, descriptor),
                    review=lambda source, items: review_for(user_id, source, items),
                    collect=lambda url: collect_for(user_id, url),
                    advise=lambda stage, context: advise_for(user_id, stage, context),
                    generate_erd=lambda snapshot, database: generate_erd_for(user_id, snapshot, database))
                workspaces[user_id] = delivery
                from .pipeline_workspace import PipelineWorkspace
                pipeline = PipelineWorkspace(delivery, member_root / 'pipeline.sqlite3',
                    generate=lambda stage, spec, dependencies: generate_stage_for(user_id, stage, spec, dependencies))
                pipelines[user_id] = pipeline
                members[user_id] = handler_factory(ProjectStore(member_root / 'schemas'), token, delivery, pipeline,
                                                  public_origin=public_origin)
            return members[user_id]

    class MemberHandler(base):
        pending_cookie = None
        redirect_to = None

        @staticmethod
        def close_resources():
            for pipeline in pipelines.values():
                pipeline.close()
            for workspace in workspaces.values():
                workspace.close()
            codex.close()

        def end_headers(self):
            if self.pending_cookie:
                self.send_header('Set-Cookie', self.pending_cookie)
            if self.redirect_to:
                self.send_header('Location', self.redirect_to)
            super().end_headers()

        @property
        def cookie_name(self):
            return '__Host-channelshift_member' if public_origin else 'channelshift_member_' + str(self.server.server_port)

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
            cookie = SimpleCookie()
            cookie[self.cookie_name] = value
            cookie[self.cookie_name]['path'] = '/'
            cookie[self.cookie_name]['httponly'] = True
            cookie[self.cookie_name]['samesite'] = 'Strict'
            cookie[self.cookie_name]['max-age'] = age
            if public_origin:
                cookie[self.cookie_name]['secure'] = True
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

        def require_master(self, user):
            if user.get('role') != 'master':
                self.send(403, {'ok': False, 'error': 'admin_forbidden'})
                return False
            return True

        def auth_error(self, error):
            code = str(error) if isinstance(error, ValueError) and str(error) in AUTH_ERRORS else 'operation_failed'
            self.send(AUTH_ERRORS.get(code, 503), {'ok': False, 'error': code})

        def do_GET(self):
            if self.path.startswith('/api/services/'):
                if self.guard(api=False):
                    self.service_request()
                return
            if not self.guard(api=self.path.startswith('/api/')):
                return
            if self.path in AUTH_ASSETS:
                name, mime = AUTH_ASSETS[self.path]
                payload = (WEB / name).read_bytes()
                if name.endswith('.html'):
                    payload = payload.replace(b'__CHANNELSHIFT_TOKEN__', token.encode('ascii'))
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
                self.send(200, {'ok': True, 'service': 'channelshift-members' if public_origin else 'channelshift-members-local'})
                return
            user = self.authenticated_user()
            if not user:
                return
            if self.path == '/api/connections/codex':
                try:
                    self.send(200, {'ok': True, 'codex': codex.status(user['id'])})
                except Exception as error:
                    self.connection_error(error)
                return
            if self.path == '/api/connections/mcp':
                try:
                    self.send(200, {'ok': True, 'connection': auth.service_token_status(user['id'])})
                except Exception as error:
                    self.auth_error(error)
                return
            if self.path in ADMIN_ASSETS or self.path.startswith('/api/admin/'):
                if not self.require_master(user):
                    return
                if self.path in ADMIN_ASSETS:
                    name, mime = ADMIN_ASSETS[self.path]
                    payload = (WEB / name).read_bytes()
                    if name.endswith('.html'):
                        payload = payload.replace(b'__CHANNELSHIFT_TOKEN__', token.encode('ascii'))
                    self.send(200, payload, mime)
                elif self.path == '/api/admin/overview':
                    try:
                        self.send(200, {'ok': True, 'members': auth.admin_members(user['id']),
                                        'audit': auth.admin_audit(user['id'])})
                    except Exception as error:
                        self.auth_error(error)
                else:
                    self.send(404, {'ok': False, 'error': 'not_found'})
                return
            if self.path == '/api/delivery/status':
                try:
                    shared = services.status()
                    self.send(200, {'ok': True, 'member_mode': True,
                                'codex': codex.status(user['id']), 'services': shared,
                                'jev_configured': shared['requirements_review']['available'],
                                'stages': [{'id': s['id'], 'title': s['title']}
                                           for s in standard_site_profile()['stages']]})
                except Exception as error:
                    self.connection_error(error)
                return
            asset_path = self.path.split('?', 1)[0]
            if asset_path in {'/', '/delivery', '/studio', '/editor', '/workbench'}:
                name = {'/': 'studio.html', '/delivery': 'studio.html', '/studio': 'studio.html',
                        '/editor': 'index.html', '/workbench': 'workbench.html'}[asset_path]
                if asset_path == '/' and self.path.startswith('/?delivery='):
                    name = 'index.html'
                payload = (WEB / name).read_text(encoding='utf-8')
                payload = payload.replace('__CHANNELSHIFT_TOKEN__', token)
                navigation = '<a class="text-button" href="/login">내 계정</a>'
                if user.get('role') == 'master':
                    navigation += '<a class="text-button" href="/admin">관리자</a>'
                if name != 'studio.html':
                    payload = payload.replace('</header>', navigation + '</header>', 1)
                elif user.get('role') == 'master':
                    payload = payload.replace('<button id="logout"', '<a class="text-button" href="/admin">회원 관리</a><button id="logout"', 1)
                self.send(200, payload.encode('utf-8'), 'text/html; charset=utf-8')
                return
            try:
                workspace_handler(user).do_GET(self)
            except (ValueError, OSError):
                self.send(400, {'ok': False, 'error': 'operation_failed'})

        def do_POST(self):
            if self.path.startswith('/api/services/'):
                if self.guard(api=False):
                    self.service_request(write=True)
                return
            if not self.guard(api=True, write=True):
                return
            if self.path.startswith('/api/auth/'):
                self.auth_post()
                return
            user = self.authenticated_user()
            if not user:
                return
            if self.path.startswith('/api/admin/'):
                if self.require_master(user):
                    self.admin_post(user)
                return
            if self.path.startswith('/api/connections/codex/'):
                self.connection_post(user)
                return
            if self.path.startswith('/api/connections/mcp/'):
                self.mcp_connection_post(user)
                return
            if self.path in {'/api/delivery/extract', '/api/delivery/erd'}:
                try:
                    connection = codex.status(user['id'])
                    if not connection['can_execute']:
                        self.send(409 if connection.get('state') == 'busy' else 403,
                                  {'ok': False, 'error': 'codex_busy' if connection.get('state') == 'busy' else 'codex_authentication_required'})
                        return
                except Exception as error:
                    self.connection_error(error)
                    return
            try:
                workspace_handler(user).do_POST(self)
            except (ValueError, OSError):
                self.send(400, {'ok': False, 'error': 'operation_failed'})

        def read_service_json(self, limit=131072):
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise ValueError('service_invalid_input')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 < length <= limit:
                raise ValueError('service_invalid_input')
            self.connection.settimeout(10)
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('service_invalid_input')
                    result[key] = value
                return result
            data = json.loads(self.rfile.read(length), object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(data) is not dict:
                raise ValueError('service_invalid_input')
            return data

        def mcp_connection_post(self, user):
            action = self.path.removeprefix('/api/connections/mcp/')
            if action not in {'create', 'revoke'}:
                self.send(404, {'ok': False, 'error': 'not_found'})
                return
            try:
                if self.read_service_json(128) != {}:
                    raise ValueError('invalid_member_input')
                if action == 'create':
                    connection = auth.create_service_token(user['id'])
                else:
                    auth.revoke_service_token(user['id'])
                    connection = auth.service_token_status(user['id'])
                self.send(200, {'ok': True, 'connection': connection})
            except (ValueError, TypeError, UnicodeError):
                self.send(400, {'ok': False, 'error': 'invalid_member_input'})
            except Exception:
                self.send(503, {'ok': False, 'error': 'operation_failed'})

        def service_request(self, write=False):
            # MCP uses a distinct member-scoped bearer token, never browser
            # cookies, host Codex credentials, or a vendor API key from callers.
            authorization = self.headers.get('Authorization', '')
            raw = authorization[7:] if authorization.startswith('Bearer ') and len(authorization) < 128 else ''
            try:
                user = auth.authenticate_service_token(raw)
            except Exception:
                self.send(503, {'ok': False, 'error': 'service_unavailable'})
                return
            if not user:
                self.send(401, {'ok': False, 'error': 'service_connection_required'})
                return
            routes = {'/api/services/review': {'source', 'requirements'}, '/api/services/reference': {'url'}}
            if not write and self.path == '/api/services/status':
                self.send(200, {'ok': True, 'services': services.status()})
                return
            if not write or self.path not in routes:
                self.send(404, {'ok': False, 'error': 'not_found'})
                return
            try:
                data = self.read_service_json()
                if set(data) != routes[self.path]:
                    raise ValueError('service_invalid_input')
                ensure_active(user['id'])
                if self.path.endswith('/review'):
                    result = services.review_for(user['id'])(data['source'], data['requirements'])
                else:
                    result = services.collect_reference(user['id'], data['url'])
                self.send(200, {'ok': True, 'result': result})
            except ServiceError as error:
                from .shared_services import SAFE_ERROR_CODES
                code = str(error) if str(error) in SAFE_ERROR_CODES else 'service_unavailable'
                self.send(429 if code in {'service_rate_limited', 'service_busy'} else 400,
                          {'ok': False, 'error': code})
            except (ValueError, TypeError, UnicodeError):
                self.send(400, {'ok': False, 'error': 'service_invalid_input'})
            except Exception:
                self.send(503, {'ok': False, 'error': 'service_unavailable'})

        def connection_error(self, error):
            from .member_codex import MemberCodexError
            from .codex_intake import CodexIntakeError
            code = str(error) if isinstance(error, (MemberCodexError, CodexIntakeError)) else 'codex_connection_failed'
            status = 409 if code == 'codex_connection_busy' else 400
            self.send(status, {'ok': False, 'error': code})

        def connection_post(self, user):
            action = self.path.removeprefix('/api/connections/codex/')
            expected = {'start': set(), 'poll': {'connection_id'}, 'cancel': {'connection_id'}, 'disconnect': set()}
            if action not in expected:
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
                if type(data) is not dict or set(data) != expected[action]:
                    self.send(400, {'ok': False, 'error': 'invalid_member_input'})
                    return
                if action == 'start':
                    value = {'connection': codex.connect(user['id'], self.session_token())}
                elif action in {'poll', 'cancel'}:
                    value = {'connection': getattr(codex, action)(user['id'], self.session_token(), data['connection_id'])}
                else:
                    value = {'codex': codex.disconnect(user['id'])}
                self.send(200, {'ok': True, **value})
            except Exception as error:
                self.connection_error(error)

        def admin_post(self, user):
            if self.path != '/api/admin/member-status':
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
                if type(data) is not dict or set(data) != {'user_id', 'disabled', 'reason'}:
                    raise ValueError('invalid_admin_request')
                member = auth.admin_set_disabled(user['id'], **data)
                cleanup_pending = False
                if data['disabled']:
                    try:
                        codex.cancel_user(data['user_id'])
                    except Exception:
                        cleanup_pending = True
                self.send(200, {'ok': True, 'member': member, 'connection_cleanup_pending': cleanup_pending})
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                if str(error) not in AUTH_ERRORS:
                    error = ValueError('invalid_admin_request')
                self.auth_error(error)
            except Exception as error:
                self.auth_error(error)

        def login_client_ip(self):
            peer = normalize_client_ip(self.client_address[0])
            if not trusted_proxy:
                return peer
            # Opt-in only for the dedicated local proxy, which overwrites this
            # header. Forwarded/X-Forwarded-For and login JSON never grant identity.
            values = self.headers.get_all('X-ChannelShift-Client-IP', [])
            if not ipaddress.ip_address(peer).is_loopback or len(values) != 1:
                raise ValueError('invalid_member_input')
            return normalize_client_ip(values[0])

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
                    current = auth.authenticate(self.session_token())
                    auth.logout(self.session_token())
                    self.set_session_cookie('', 0)
                    result = {}
                    if current:
                        try:
                            codex.cancel_session(current['id'], self.session_token())
                        except Exception:
                            result['connection_cleanup_pending'] = True
                elif self.path == '/api/auth/login':
                    previous = self.session_token()
                    previous_user = auth.authenticate(previous) if previous else None
                    result = auth.login(**data, client_ip=self.login_client_ip())
                    if previous:
                        auth.logout(previous)
                    self.set_session_cookie(result['session_token'])
                    result = {'user': result['user']}
                    if previous_user:
                        try:
                            codex.cancel_session(previous_user['id'], previous)
                        except Exception:
                            result['connection_cleanup_pending'] = True
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
    parser.add_argument('--public-origin', default=os.environ.get('CHANNELSHIFT_PUBLIC_ORIGIN'),
                        help='Exact HTTPS origin served by the local reverse proxy')
    parser.add_argument('--trust-proxy-client-ip', action='store_true',
                        help='Require X-ChannelShift-Client-IP overwritten by the dedicated local proxy')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    try:
        public_origin = normalize_public_origin(args.public_origin) if args.public_origin is not None else None
    except ValueError:
        parser.error('Public origin must be an HTTPS origin without credentials, path, query or fragment')
    if args.trust_proxy_client_ip and public_origin is None:
        parser.error('--trust-proxy-client-ip requires --public-origin')
    root = directory() / 'members'
    origin = public_origin or 'http://127.0.0.1:' + str(args.port)
    auth = MemberAuth(root / 'accounts.sqlite3', mailer=from_environment(origin))
    handler = member_handler_factory(auth, root, public_origin=public_origin,
                                     trusted_proxy=args.trust_proxy_client_ip)
    with ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
        print('ChannelShift members: ' + origin + '/login', file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            handler.close_resources()


if __name__ == '__main__':
    main()
