"""Member-owned ChatGPT connections through the official Codex app-server.

Only device authorization is exposed. Codex owns OAuth tokens and refresh; this
module never reads tokens, the operator's Codex home, or the OS credential store.
HTTP callers must authenticate members and protect mutations against CSRF.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import stat
import subprocess
import threading
import time

from . import codex_intake
from .member_auth import AuthError, _reject_links

LOGIN_TIMEOUT = 10 * 60
RPC_TIMEOUT = 15
MAX_LOGINS = 4
MAX_OUTPUT = 512 * 1024
SAFE_ERROR_CODES = codex_intake.SAFE_ERROR_CODES | frozenset({
    "codex_connection_not_found", "codex_connection_busy", "codex_connection_failed",
    "codex_connection_expired", "codex_connection_cancelled", "codex_unsafe_storage",
})
_USER_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_LOGIN_ID = re.compile(r"[A-Za-z0-9_-]{8,128}\Z", re.ASCII)
_USER_CODE = re.compile(r"[A-Z0-9]{4,12}-[A-Z0-9]{4,12}\Z", re.ASCII)
_VERIFICATION_URL = "https://auth.openai.com/codex/device"
_CONFIG = ('cli_auth_credentials_store = "file"\n'
           'forced_login_method = "chatgpt"\nmodel_provider = "openai"\n'
           '[analytics]\nenabled = false\n')


class MemberCodexError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in SAFE_ERROR_CODES else "codex_connection_failed"
        super().__init__(self.code)


def _safe_file(path):
    """Reject redirected/hard-linked credential files without opening them."""
    _reject_links(path)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MemberCodexError("codex_unsafe_storage")
        if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise MemberCodexError("codex_unsafe_storage")


def _session_digest(session_key):
    if type(session_key) is not str or not 16 <= len(session_key) <= 256:
        raise MemberCodexError("codex_connection_not_found")
    try:
        return hashlib.sha256(session_key.encode("ascii")).hexdigest()
    except UnicodeError:
        raise MemberCodexError("codex_connection_not_found") from None


class _AppServer:
    """Bounded stdio JSON-RPC; no Codex threads or model turns are started."""

    def __init__(self, home, cancel):
        self.cancel = cancel
        self.messages = queue.Queue(maxsize=64)
        self.notifications = deque(maxlen=64)
        self.problem = None
        self.sequence = 0
        self.total = 0
        self.output_lock = threading.Lock()
        arguments = [codex_intake._executable(), "--no-daemon", "-a", "never", "app-server",
                     "--listen", "stdio://", "--strict-config", "-c", 'cli_auth_credentials_store="file"',
                     "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
                     "--enable", "skip_host_skill_discovery"]
        for feature in codex_intake._DISABLED_FEATURES:
            arguments += ["--disable", feature]
        options = {"cwd": str(home), "env": codex_intake._child_environment(home), "shell": False,
                   "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                   "bufsize": 0}
        if os.name == "nt":
            options["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            options["start_new_session"] = True
        try:
            self.process = subprocess.Popen(arguments, **options)
        except OSError:
            raise MemberCodexError("codex_unavailable") from None
        self.readers = [threading.Thread(target=self._read, args=(stream, structured), daemon=True)
                        for stream, structured in ((self.process.stdout, True), (self.process.stderr, False))]
        for reader in self.readers:
            reader.start()

    def _read(self, stream, structured):
        try:
            while True:
                raw = stream.readline(65537) if structured else stream.read(4096)
                if not raw:
                    return
                with self.output_lock:
                    self.total += len(raw)
                    if self.total > MAX_OUTPUT or (structured and len(raw) > 65536):
                        self.problem = "codex_output_limit"
                        return
                if structured:
                    value = json.loads(raw.decode("utf-8"))
                    if type(value) is not dict:
                        raise ValueError()
                    self.messages.put_nowait(value)
        except (OSError, UnicodeError, ValueError, RecursionError, queue.Full):
            self.problem = "codex_connection_failed"

    def _check(self):
        if self.cancel.is_set():
            raise MemberCodexError("codex_connection_cancelled")
        if self.problem:
            raise MemberCodexError(self.problem)
        if self.process.poll() is not None and self.messages.empty():
            raise MemberCodexError("codex_connection_failed")

    def send(self, message):
        self._check()
        try:
            self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        except (BrokenPipeError, OSError, ValueError):
            raise MemberCodexError("codex_connection_failed") from None

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            self._check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return self.messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                pass

    def request(self, method, params=None):
        self.sequence += 1
        identifier = self.sequence
        self.send({"id": identifier, "method": method, "params": params or {}})
        deadline = time.monotonic() + RPC_TIMEOUT
        while True:
            item = self.receive(max(0, deadline - time.monotonic()))
            if item is None:
                raise MemberCodexError("codex_timeout")
            if item.get("id") == identifier:
                if "error" in item or type(item.get("result")) is not dict:
                    raise MemberCodexError("codex_connection_failed")
                return item["result"]
            if "id" in item:
                # No tools, approval prompts, or external-token callbacks are supported.
                raise MemberCodexError("codex_unsupported_cli")
            if len(self.notifications) >= 64:
                raise MemberCodexError("codex_output_limit")
            self.notifications.append(item)

    def notification(self, timeout):
        self._check()
        return self.notifications.popleft() if self.notifications else self.receive(timeout)

    def close(self):
        try:
            codex_intake._terminate(self.process)
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
            for reader in self.readers:
                reader.join(timeout=1)


@dataclass
class _Connection:
    user_id: str
    session: str
    home: Path
    identifier: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    state: str = "starting"
    reason: str = "codex_authentication_required"
    user_code: str | None = None
    login_id: str | None = None
    thread: threading.Thread | None = None


class MemberCodex:
    def __init__(self, root):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.lock = threading.RLock()
        self.connections = {}
        self.user_locks = {}
        self.closed = False

    def _user_lock(self, user_id):
        if type(user_id) is not str or not _USER_ID.fullmatch(user_id):
            raise MemberCodexError("codex_connection_not_found")
        with self.lock:
            return self.user_locks.setdefault(user_id, threading.RLock())

    def _home(self, user_id, *, create=False):
        self._user_lock(user_id)
        home = self.root / "workspaces" / user_id / "codex"
        try:
            _reject_links(home)
            if create:
                # pathlib's parents=True gives intermediate directories the default
                # mode. Create each member boundary explicitly before a workspace
                # HTTP request can encounter it.
                for folder in (self.root, self.root / "workspaces", home.parent, home):
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    _reject_links(folder)
                    if os.name != "nt" and (folder.stat().st_uid != os.getuid()
                                              or stat.S_IMODE(folder.stat().st_mode) & 0o077):
                        raise MemberCodexError("codex_unsafe_storage")
                _reject_links(home)
            if home.exists():
                info = home.stat()
                if not stat.S_ISDIR(info.st_mode):
                    raise MemberCodexError("codex_unsafe_storage")
                if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                    raise MemberCodexError("codex_unsafe_storage")
                for name in ("auth.json", "config.toml"):
                    _safe_file(home / name)
            if create:
                descriptor = os.open(home / "config.toml", os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(_CONFIG)
        except (OSError, AuthError):
            raise MemberCodexError("codex_unsafe_storage") from None
        return home

    @staticmethod
    def _disconnected(reason="codex_authentication_required", state="disconnected"):
        return {"state": state, "available": True, "authenticated": False, "can_execute": False,
                "auth_mode": "none", "cli_version": None, "reason": reason}

    def status(self, user_id):
        user_lock = self._user_lock(user_id)
        if not user_lock.acquire(blocking=False):
            # Page refresh must not queue behind a bounded but long model call.
            # No auth files or CLI probes are needed to report this safe state.
            return self._disconnected(reason="codex_busy", state="busy")
        try:
            home = self._home(user_id)
            with self.lock:
                item = self.connections.get(user_id)
                if item and not item.done.is_set():
                    return self._disconnected(state="pending")
            if not (home / "auth.json").exists():
                return self._disconnected()
            result = codex_intake.status(codex_home=home)
            return {**result, "state": "connected" if result["can_execute"] else "disconnected"}
        finally:
            user_lock.release()

    def _view(self, item):
        with self.lock:
            result = {"connection_id": item.identifier, "state": item.state, "reason": item.reason}
            if item.state == "pending":
                result.update(verification_url=_VERIFICATION_URL, user_code=item.user_code,
                              expires_in=max(0, int(LOGIN_TIMEOUT - (time.monotonic() - item.created))))
            return result

    def connect(self, user_id, session_key):
        session = _session_digest(session_key)
        with self._user_lock(user_id):
            with self.lock:
                if self.closed:
                    raise MemberCodexError("codex_unavailable")
                old = self.connections.get(user_id)
                if old and not old.done.is_set():
                    if hmac.compare_digest(old.session, session) and old.state == "pending":
                        return self._view(old)
                    raise MemberCodexError("codex_connection_busy")
                if sum(not item.done.is_set() for item in self.connections.values()) >= MAX_LOGINS:
                    raise MemberCodexError("codex_connection_busy")
                home = self._home(user_id, create=True)
                item = _Connection(user_id, session, home)
                self.connections[user_id] = item
            # Reserve a slot before CLI capability/auth probes; never launch login on GET.
            try:
                current = codex_intake.status(codex_home=home)
                if current["can_execute"]:
                    item.state, item.reason = "connected", "ready"
                    item.done.set()
                    return self._view(item)
                if current["reason"] != "codex_authentication_required":
                    raise MemberCodexError(current["reason"])
                self._erase_auth(home)
                item.thread = threading.Thread(target=self._login, args=(item,), daemon=True)
                item.thread.start()
            except (codex_intake.CodexIntakeError, MemberCodexError):
                item.state = "failed"
                item.done.set()
                raise
        if not item.ready.wait(RPC_TIMEOUT * 2 + 2):
            self._stop(item)
            raise MemberCodexError("codex_timeout")
        if item.state == "failed":
            raise MemberCodexError(item.reason)
        return self._view(item)

    def _login(self, item):
        server = None
        success = False
        try:
            server = _AppServer(item.home, item.cancel)
            server.request("initialize", {"clientInfo": {"name": "channelshift_members", "version": "0.1.0"}})
            server.send({"method": "initialized", "params": {}})
            result = server.request("account/login/start", {"type": "chatgptDeviceCode"})
            if (result.get("type") != "chatgptDeviceCode"
                    or result.get("verificationUrl") != _VERIFICATION_URL
                    or type(result.get("loginId")) is not str or not _LOGIN_ID.fullmatch(result["loginId"])
                    or type(result.get("userCode")) is not str or not _USER_CODE.fullmatch(result["userCode"])):
                raise MemberCodexError("codex_authentication_unverified")
            with self.lock:
                item.login_id, item.user_code = result["loginId"], result["userCode"]
                item.state = "pending"
                item.ready.set()
            while time.monotonic() - item.created < LOGIN_TIMEOUT:
                message = server.notification(0.25)
                if message is None:
                    continue
                if "id" in message:
                    raise MemberCodexError("codex_unsupported_cli")
                if message.get("method") != "account/login/completed":
                    continue
                params = message.get("params")
                if type(params) is not dict or params.get("loginId") != item.login_id:
                    continue
                if params.get("success") is not True:
                    raise MemberCodexError("codex_connection_failed")
                result = server.request("account/read", {"refreshToken": False})
                account = result.get("account")
                if type(account) is not dict or account.get("type") != "chatgpt":
                    raise MemberCodexError("codex_subscription_required")
                _safe_file(item.home / "auth.json")
                if not (item.home / "auth.json").is_file():
                    raise MemberCodexError("codex_authentication_unverified")
                success = True
                break
            if not success:
                raise MemberCodexError("codex_connection_expired")
        except (codex_intake.CodexIntakeError, MemberCodexError) as error:
            with self.lock:
                item.reason = error.code
                item.state = "expired" if error.code == "codex_connection_expired" else "failed"
        except (OSError, ValueError, TypeError, KeyError):
            with self.lock:
                item.state, item.reason = "failed", "codex_connection_failed"
        finally:
            if server:
                try:
                    server.close()
                except (codex_intake.CodexIntakeError, OSError):
                    success = False
                    item.state, item.reason = "failed", "codex_stop_failed"
            with self.lock:
                if item.cancel.is_set():
                    success = False
                    item.state, item.reason = "cancelled", "codex_connection_cancelled"
                if success:
                    item.state, item.reason = "connected", "ready"
                else:
                    try:
                        self._erase_auth(item.home)
                    except MemberCodexError:
                        item.state, item.reason = "failed", "codex_unsafe_storage"
                item.user_code = None
                item.done.set()
                item.ready.set()

    @staticmethod
    def _erase_auth(home):
        try:
            path = home / "auth.json"
            _safe_file(path)
            path.unlink(missing_ok=True)
        except (OSError, AuthError):
            raise MemberCodexError("codex_unsafe_storage") from None

    def _owned(self, user_id, session_key, connection_id):
        self._user_lock(user_id)
        session = _session_digest(session_key)
        with self.lock:
            item = self.connections.get(user_id)
            if (not item or type(connection_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43}", connection_id, re.ASCII)
                    or not hmac.compare_digest(item.identifier, connection_id)
                    or not hmac.compare_digest(item.session, session)):
                raise MemberCodexError("codex_connection_not_found")
            return item

    def poll(self, user_id, session_key, connection_id):
        return self._view(self._owned(user_id, session_key, connection_id))

    def _stop(self, item):
        with self.lock:
            if item.done.is_set():
                return
            item.cancel.set()
        if item.thread:
            item.thread.join(timeout=12)
            if item.thread.is_alive():
                raise MemberCodexError("codex_stop_failed")

    def cancel(self, user_id, session_key, connection_id):
        with self._user_lock(user_id):
            item = self._owned(user_id, session_key, connection_id)
            self._stop(item)
            return self._view(item)

    def cancel_session(self, user_id, session_key):
        session = _session_digest(session_key)
        with self._user_lock(user_id):
            with self.lock:
                item = self.connections.get(user_id)
            if item and hmac.compare_digest(item.session, session):
                self._stop(item)

    def cancel_user(self, user_id):
        with self._user_lock(user_id):
            with self.lock:
                item = self.connections.get(user_id)
            if item:
                self._stop(item)

    def disconnect(self, user_id):
        with self._user_lock(user_id):
            self.cancel_user(user_id)
            self._erase_auth(self._home(user_id))
            with self.lock:
                self.connections.pop(user_id, None)
            return self._disconnected()

    def extract_requirements(self, user_id, client_request):
        with self._user_lock(user_id):
            home = self._home(user_id)
            with self.lock:
                item = self.connections.get(user_id)
                if self.closed or (item and not item.done.is_set()):
                    raise MemberCodexError("codex_connection_busy")
            if not (home / "auth.json").exists():
                raise MemberCodexError("codex_authentication_required")
            return codex_intake.extract_requirements(client_request, codex_home=home)

    def generate_erd(self, user_id, review_snapshot, database):
        from .codex_erd import generate_erd
        with self._user_lock(user_id):
            home = self._home(user_id)
            with self.lock:
                item = self.connections.get(user_id)
                if self.closed or (item and not item.done.is_set()):
                    raise MemberCodexError("codex_connection_busy")
            if not (home / "auth.json").exists():
                raise MemberCodexError("codex_authentication_required")
            return generate_erd(review_snapshot, database, codex_home=home)

    def close(self):
        with self.lock:
            self.closed = True
            items = list(self.connections.values())
            for item in items:
                if not item.done.is_set():
                    item.cancel.set()
        for item in items:
            self._stop(item)
