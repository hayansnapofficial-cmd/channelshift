"""A fixed, bounded, explicitly requested public-page reference collection.

The host's HTTPS preflight pins a validated public address and follows no
redirects. The hosted crawler fetches separately; its public input schema has no
redirect-disable switch. Reject changed loaded URLs as well as redirected
preflights, but do not claim that the hosted service shares the host's DNS pin.
"""
from __future__ import annotations

import http.client
import importlib.util
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from .service_errors import ServiceError

ACTOR = 'apify/website-content-crawler'
MAX_PAGE_BYTES = 524288
MAX_TEXT = 12000
MAX_SECONDS = 100
_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='reference-dns')
_DNS_SLOTS = threading.BoundedSemaphore(2)
_BLOCKED_SUFFIXES = ('localhost', 'local', 'localdomain', 'internal', 'lan', 'home',
                     'intranet', 'invalid', 'test', 'onion', 'arpa', 'ts.net')


def _valid_secret(value):
    return (type(value) is str and 1 <= len(value) <= 512 and value.isascii()
            and not any(char.isspace() or ord(char) < 33 for char in value))


def _server_setting(name):
    value = os.environ.get(name, '')
    if not value and os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value = winreg.QueryValueEx(key, name)[0]
        except OSError:
            value = ''
    return value


def _credential_file(value):
    """Only an absolute operator-configured path, never a member-supplied path."""
    if type(value) is not str or not value or len(value) > 4096:
        return ''
    descriptor = None
    try:
        path = Path(value)
        if not path.is_absolute():
            return ''
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return ''
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 514:
            return ''
        if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            return ''
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        opened = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            return ''
        return os.read(descriptor, 515).decode('ascii').strip()
    except (OSError, ValueError, UnicodeError):
        return ''
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _credential():
    """Only server configuration; no project, browser or general config search."""
    token = _server_setting('APIFY_TOKEN')
    if not token:
        token = _credential_file(_server_setting('APIFY_TOKEN_FILE'))
    if not _valid_secret(token):
        raise ServiceError('service_not_configured')
    return token


def configured():
    try:
        _credential()
        return all(importlib.util.find_spec(name) is not None for name in ('apify_client', 'httpx'))
    except (ServiceError, ImportError, ValueError):
        return False


def normalize_public_url(value):
    """Pure syntax validation before any quota reservation or DNS request."""
    if (type(value) is not str or not 1 <= len(value) <= 2048 or value != value.strip()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or '\\' in value):
        raise ServiceError('reference_url_invalid')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not host or parsed.fragment
                or '%' in host or host.endswith('.')):
            raise ValueError
        host = host.encode('idna').decode('ascii').lower()
        # Require public DNS names; reject every literal and alternate IP notation.
        labels = host.split('.')
        if (len(host) > 253 or len(labels) < 2 or not re.fullmatch('[a-z]{2,63}', labels[-1])
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
                or any(host == suffix or host.endswith('.' + suffix) for suffix in _BLOCKED_SUFFIXES)):
            raise ValueError
        if any(key.lower() in {'token', 'access_token', 'api_key', 'apikey', 'password',
                               'secret', 'session', 'signature'} for key, _ in parse_qsl(parsed.query)):
            raise ValueError
        path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="%:@!$&'()*+,;=/?-._~")
        normalized = urlunsplit(('https', host, path, query, ''))
        if len(normalized) > 2048:
            raise ValueError
        return normalized
    except (ValueError, UnicodeError):
        raise ServiceError('reference_url_invalid') from None


def _public_addresses(host):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise ServiceError('service_busy')
    try:
        future = _DNS_POOL.submit(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    except Exception:
        _DNS_SLOTS.release()
        raise ServiceError('reference_url_unavailable') from None
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    try:
        records = future.result(timeout=5)
        addresses = []
        for family, _, _, _, address in records:
            ip = ipaddress.ip_address(address[0])
            if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip, 'ipv4_mapped', None):
                raise ServiceError('reference_url_invalid')
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise ServiceError('reference_url_invalid')
            addresses.append((family, address))
        if not addresses:
            raise ServiceError('reference_url_unavailable')
        return addresses
    except ServiceError:
        raise
    except (OSError, ValueError, FutureTimeout):
        raise ServiceError('reference_url_unavailable') from None


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, 443, timeout=4, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        family, address = self.address
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(address)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def preflight(url):
    """Read at most 512 KiB from one pinned public TLS endpoint, without cookies."""
    parsed = urlsplit(url)
    address = _public_addresses(parsed.hostname)[0]
    connection = _PinnedHTTPS(parsed.hostname, address)
    deadline = time.monotonic() + 12
    try:
        connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''),
                           headers={'Accept': 'text/html,application/xhtml+xml',
                                    'Accept-Encoding': 'identity',
                                    'User-Agent': 'ChannelShift-Reference/1.0'})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ServiceError('reference_redirect_refused')
        if response.status != 200:
            raise ServiceError('reference_url_unavailable')
        content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
        if content_type not in {'text/html', 'application/xhtml+xml'}:
            raise ServiceError('reference_url_unavailable')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise ServiceError('reference_url_unavailable')
        length = response.getheader('Content-Length')
        if length and (not length.isdigit() or int(length) > MAX_PAGE_BYTES):
            raise ServiceError('reference_too_large')
        count = 0
        while True:
            if time.monotonic() >= deadline:
                raise ServiceError('reference_url_unavailable')
            chunk = response.read1(min(65536, MAX_PAGE_BYTES + 1 - count))
            count += len(chunk)
            if count > MAX_PAGE_BYTES:
                raise ServiceError('reference_too_large')
            if not chunk:
                break
    except ServiceError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise ServiceError('reference_url_unavailable') from None
    finally:
        connection.close()


def actor_input(url):
    # These are documented website-content-crawler input fields, not arbitrary
    # member options. Raw HTTP avoids JavaScript, iframe and subresource fetches.
    return {'startUrls': [{'url': url}], 'crawlerType': 'cheerio', 'maxCrawlDepth': 0,
            'maxCrawlPages': 1, 'maxResults': 1, 'initialConcurrency': 1,
            'maxConcurrency': 1, 'useSitemaps': False, 'useLlmsTxt': False,
            'respectRobotsTxtFile': True, 'proxyConfiguration': {'useApifyProxy': False},
            'requestTimeoutSecs': 20, 'maxRequestRetries': 0, 'maxSessionRotations': 0,
            'ignoreHttpsErrors': False, 'ignoreCanonicalUrl': True,
            'saveFiles': False, 'saveContentTypes': '', 'saveHtml': False,
            'saveHtmlAsFile': False, 'saveMarkdown': False, 'saveScreenshots': False,
            'summarize': False, 'debugMode': False, 'debugLog': False}


class _BoundedApiTransport:
    """Small adapter for the pinned apify-client 2.5 HTTP response protocol.

    Its default transport follows redirects. Replace that transport to protect
    the server token, disable ambient proxies, and bound all response bodies.
    """
    def __init__(self, client, deadline):
        self.client, self.deadline = client, deadline

    def request(self, *, method, url, headers, content, timeout, stream):
        import httpx
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'api.apify.com' or not parsed.path.startswith('/v2/'):
            raise ServiceError('service_unavailable')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceError('service_unavailable')
        with self.client.stream(method, url, headers=headers, content=content,
                                timeout=min(timeout, remaining)) as response:
            if 300 <= response.status_code < 400:
                raise ServiceError('service_unavailable')
            body = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() > self.deadline:
                    raise ServiceError('service_unavailable')
                body.extend(chunk)
                if len(body) > MAX_PAGE_BYTES:
                    raise ServiceError('reference_too_large')
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {'content-encoding', 'content-length'}}
            return httpx.Response(response.status_code, headers=headers,
                                  content=bytes(body), request=response.request)


def _sdk_client(token, deadline):
    from apify_client import ApifyClient
    import httpx
    client = ApifyClient(token=token, max_retries=0, timeout_secs=10)
    transport = httpx.Client(headers={'Authorization': 'Bearer ' + token},
                             trust_env=False, follow_redirects=False)
    client.http_client.impit_client = _BoundedApiTransport(transport, deadline)
    return client, transport


def collect_reference(url):
    """Collect text only. No actor, run, dataset or execution options are inputs."""
    url = normalize_public_url(url)
    token = _credential()
    deadline = time.monotonic() + MAX_SECONDS
    preflight(url)
    client = transport = None
    run_id = None
    finished = False
    try:
        client, transport = _sdk_client(token, deadline)
        run = client.actor(ACTOR).start(run_input=actor_input(url), timeout_secs=60,
                                       memory_mbytes=256, max_items=1,
                                       max_total_charge_usd=Decimal('0.05'), restart_on_error=False)
        run_id = run.get('id') if type(run) is dict else None
        if type(run_id) is not str or not re.fullmatch('[a-zA-Z0-9]{1,64}', run_id):
            raise ServiceError('service_unavailable')
        wait_deadline = min(deadline - 10, time.monotonic() + 65)
        while run.get('status') not in {'SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'}:
            if time.monotonic() >= wait_deadline:
                raise ServiceError('service_unavailable')
            run = client.run(run_id).wait_for_finish(wait_secs=5)
            if type(run) is not dict:
                raise ServiceError('service_unavailable')
        finished = True
        if type(run) is not dict or run.get('status') != 'SUCCEEDED':
            raise ServiceError('service_unavailable')
        dataset_id = run.get('defaultDatasetId')
        if type(dataset_id) is not str or not re.fullmatch('[a-zA-Z0-9]{1,64}', dataset_id):
            raise ServiceError('service_unavailable')
        items = client.dataset(dataset_id).list_items(limit=1,
            fields=['url', 'text', 'metadata', 'crawl']).items
        if type(items) is not list or len(items) != 1 or type(items[0]) is not dict:
            raise ServiceError('reference_empty')
        item = items[0]
        crawl = item.get('crawl')
        if type(crawl) is not dict or crawl.get('httpStatusCode') != 200:
            raise ServiceError('service_unavailable')
        if normalize_public_url(item.get('url')) != url or normalize_public_url(crawl.get('loadedUrl')) != url:
            raise ServiceError('reference_redirect_refused')
        text = item.get('text')
        metadata = item.get('metadata', {})
        title = metadata.get('title', '') if type(metadata) is dict else ''
        if type(text) is not str or not text.strip():
            raise ServiceError('reference_empty')
        if type(title) is not str:
            title = ''
        text.encode('utf-8')
        title.encode('utf-8')
        return {'format': 'channelshift.reference/v1', 'url': url, 'title': title[:200],
                'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT,
                'collected_at': datetime.now(timezone.utc).isoformat(),
                'kind': 'reference', 'approved': False}
    except ServiceError:
        raise
    except ImportError:
        raise ServiceError('service_not_configured') from None
    except Exception:
        raise ServiceError('service_unavailable') from None
    finally:
        # The remote run has a hard timeout even if the API becomes unreachable.
        if run_id and client and not finished:
            try:
                client.run(run_id).abort()
            except Exception:
                pass
        if transport:
            transport.close()
