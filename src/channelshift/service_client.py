"""MCP bridge to member-authorized platform features, using a platform token.

The destination and credential file come only from the operator's environment.
No vendor credentials or browser session are read, and no token is a tool input.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .apify_reference import normalize_public_url
from .service_errors import SAFE_SERVICE_ERRORS, ServiceError
from .shared_services import _review_input

MAX_RESPONSE = 262144
TIMEOUT_SECONDS = 110
SAFE_CLIENT_ERRORS = SAFE_SERVICE_ERRORS | {'service_connection_required', 'service_connection_invalid'}


class ServiceClientError(ValueError):
    """Safe codes only, never transport errors, response bodies or credentials."""


def _destination():
    value = os.environ.get('CHANNELSHIFT_SERVICE_URL', 'http://127.0.0.1:5189')
    try:
        if (not 1 <= len(value) <= 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
                or '\\' in value):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        port = parsed.port
        if (not host or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment or '%' in host
                or host.endswith('.') or port is not None and not 1 <= port <= 65535):
            raise ValueError
        host = host.encode('idna').decode('ascii').lower()
        if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                  for label in host.split('.')):
            raise ValueError
        if parsed.scheme == 'http':
            if host != '127.0.0.1' or port is None:
                raise ValueError
        elif parsed.scheme != 'https':
            raise ValueError
        authority = host + (':' + str(port) if port is not None else '')
        return urlunsplit((parsed.scheme, authority, '', '', ''))
    except (TypeError, ValueError, UnicodeError):
        raise ServiceClientError('service_connection_invalid') from None


def _token():
    value = os.environ.get('CHANNELSHIFT_SERVICE_TOKEN_FILE', '')
    descriptor = None
    try:
        if not value or len(value) > 4096:
            raise ValueError
        path = Path(value)
        if not path.is_absolute():
            raise ValueError
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 50 <= info.st_size <= 52:
            raise ValueError
        if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        opened = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError
        token = os.read(descriptor, 53).decode('ascii').strip()
        # Reject unrelated credentials rather than forwarding a vendor key.
        if not re.fullmatch(r'cs_mcp_[A-Za-z0-9_-]{43}', token):
            raise ValueError
        return token
    except (OSError, ValueError, UnicodeError):
        raise ServiceClientError('service_connection_required') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError


class ServiceClient:
    """Explicit requests only; constructing a client does not read or call anything."""

    def _request(self, path, payload=None):
        if path not in {'/api/services/status', '/api/services/review', '/api/services/reference'}:
            raise ServiceClientError('service_connection_invalid')
        token = _token()
        url = _destination() + path
        deadline = time.monotonic() + TIMEOUT_SECONDS
        fallback = 'service_unavailable'
        try:
            body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if body is not None and len(body) > 131072:
                raise ServiceClientError('service_invalid_input')
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=TIMEOUT_SECONDS,
                              headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                                       'Content-Type': 'application/json'}) as client:
                with client.stream('GET' if payload is None else 'POST', url, content=body) as reply:
                    if reply.status_code in {401, 403}:
                        raise ServiceClientError('service_connection_required')
                    if 300 <= reply.status_code < 400:
                        raise ServiceClientError('service_unavailable')
                    if reply.status_code == 429:
                        fallback = 'service_rate_limited'
                    if reply.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                        raise ServiceClientError(fallback)
                    raw = bytearray()
                    for chunk in reply.iter_bytes():
                        if time.monotonic() > deadline:
                            raise ServiceClientError(fallback)
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE:
                            raise ServiceClientError(fallback)
                    # A misconfigured server must not reflect this credential to a tool.
                    if token.encode('ascii') in raw:
                        raise ServiceClientError(fallback)
                    result = json.loads(raw, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant)
                    if token in json.dumps(result, ensure_ascii=False):
                        raise ServiceClientError(fallback)
                    if type(result) is not dict or type(result.get('ok')) is not bool:
                        raise ServiceClientError(fallback)
                    if result['ok'] is False:
                        code = result.get('error')
                        raise ServiceClientError(code if type(code) is str and code in SAFE_CLIENT_ERRORS else fallback)
                    if reply.status_code != 200:
                        raise ServiceClientError(fallback)
                    field = 'services' if payload is None else 'result'
                    if type(result.get(field)) is not dict:
                        raise ServiceClientError(fallback)
                    return result[field]
        except ServiceClientError:
            raise
        except Exception:
            raise ServiceClientError(fallback) from None

    def status(self):
        return self._request('/api/services/status')

    def review_requirements(self, source, requirements):
        try:
            _review_input(source, requirements)
        except ServiceError:
            raise ServiceClientError('service_invalid_input') from None
        return self._request('/api/services/review', {'source': source, 'requirements': requirements})

    def collect_reference(self, url):
        try:
            url = normalize_public_url(url)
        except ServiceError:
            raise ServiceClientError('reference_url_invalid') from None
        return self._request('/api/services/reference', {'url': url})
