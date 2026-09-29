"""Isolated member Codex authorization; never start a real login or model call."""
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from channelshift import codex_intake as intake
from channelshift import member_codex as member

USER_A, USER_B = "a" * 32, "b" * 32
SESSION_A, SESSION_B = "a-session-" * 5, "b-session-" * 5
SIGNED_OUT = {"available": True, "authenticated": False, "can_execute": False,
              "auth_mode": "none", "cli_version": "test", "reason": "codex_authentication_required"}
SIGNED_IN = {**SIGNED_OUT, "authenticated": True, "can_execute": True,
             "auth_mode": "chatgpt", "reason": "ready"}


class FakeServer:
    instances = []
    login = {"type": "chatgptDeviceCode", "loginId": "test-login-1234",
             "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "ABCD-1234"}

    def __init__(self, home, cancel):
        self.home, self.cancel = home, cancel
        self.complete = threading.Event()
        self.closed = False
        self.calls = []
        self.instances.append(self)

    def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "account/login/start":
            return dict(self.login)
        if method == "account/read":
            descriptor = os.open(self.home / "auth.json", os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write("synthetic-private-auth")
            return {"account": {"type": "chatgpt", "email": "private@example.test", "planType": "plus"}}
        return {}

    def send(self, message):
        self.calls.append((message["method"], message.get("params")))

    def notification(self, timeout):
        if self.cancel.is_set():
            raise member.MemberCodexError("codex_connection_cancelled")
        if self.complete.wait(min(timeout, 0.01)):
            return {"method": "account/login/completed",
                    "params": {"loginId": self.login["loginId"], "success": True}}
        return None

    def close(self):
        self.closed = True


class MemberConnectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.manager = member.MemberCodex(self.root)
        FakeServer.instances = []
        self.server_patch = patch.object(member, "_AppServer", FakeServer)
        self.status_patch = patch.object(intake, "status", return_value=SIGNED_OUT)
        self.server_patch.start()
        self.status = self.status_patch.start()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.status_patch.stop)
        self.addCleanup(self.server_patch.stop)
        self.addCleanup(self.manager.close)

    def test_absent_auth_status_and_extract_never_use_host_login(self):
        with patch.dict(os.environ, {"CODEX_HOME": "private-host-home", "OPENAI_API_KEY": "private-key"}):
            result = self.manager.status(USER_A)
            self.assertEqual(result["state"], "disconnected")
            with self.assertRaisesRegex(member.MemberCodexError, "^codex_authentication_required$"):
                self.manager.extract_requirements(USER_A, "a request")
        self.status.assert_not_called()
        self.assertFalse((self.root / "workspaces").exists())

    def test_pending_codes_are_bound_to_member_session_and_opaque_connection(self):
        first = self.manager.connect(USER_A, SESSION_A)
        second = self.manager.connect(USER_B, SESSION_B)
        self.assertNotEqual(first["connection_id"], second["connection_id"])
        self.assertNotEqual(FakeServer.instances[0].home, FakeServer.instances[1].home)
        self.assertEqual(first["user_code"], "ABCD-1234")
        self.assertEqual(first["verification_url"], member._VERIFICATION_URL)
        self.assertEqual(self.manager.connect(USER_A, SESSION_A), first)
        self.assertNotIn("user_code", self.manager.status(USER_A))
        self.assertNotIn("verification_url", self.manager.status(USER_A))
        for user, session, identifier in [(USER_B, SESSION_A, first["connection_id"]),
                                           (USER_A, SESSION_B, first["connection_id"]),
                                           (USER_A, SESSION_A, second["connection_id"])]:
            with self.subTest(user=user, session=session), self.assertRaisesRegex(
                    member.MemberCodexError, "^codex_connection_not_found$"):
                self.manager.poll(user, session, identifier)
        with self.assertRaisesRegex(member.MemberCodexError, "^codex_connection_busy$"):
            self.manager.connect(USER_A, SESSION_B)
        for identifier in ('\uac00', '\ud800', first['connection_id'] + '\n', []):
            with self.subTest(identifier=repr(identifier)), self.assertRaisesRegex(
                    member.MemberCodexError, '^codex_connection_not_found$'):
                self.manager.poll(USER_A, SESSION_A, identifier)

    @unittest.skipIf(os.name == 'nt', 'POSIX directory modes')
    def test_connect_first_creates_private_workspace_intermediate_directories(self):
        self.manager.connect(USER_A, SESSION_A)
        for path in (self.root, self.root / 'workspaces', self.root / 'workspaces' / USER_A,
                     self.root / 'workspaces' / USER_A / 'codex'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_success_verifies_chatgpt_without_exposing_account_or_auth(self):
        result = self.manager.connect(USER_A, SESSION_A)
        server = FakeServer.instances[0]
        server.complete.set()
        self.assertTrue(self.manager.connections[USER_A].done.wait(2))
        connected = self.manager.poll(USER_A, SESSION_A, result["connection_id"])
        self.assertEqual(connected["state"], "connected")
        self.assertNotIn("user_code", connected)
        self.assertNotIn("private", json.dumps(connected))
        self.assertTrue(server.closed)
        self.assertIn(("account/read", {"refreshToken": False}), server.calls)
        self.status.return_value = SIGNED_IN
        self.assertTrue(self.manager.status(USER_A)["can_execute"])
        self.status.assert_called_with(codex_home=server.home)
        with patch.object(intake, "extract_requirements", return_value={"candidate": True}) as extract:
            self.assertEqual(self.manager.extract_requirements(USER_A, "request"), {"candidate": True})
            extract.assert_called_once_with("request", codex_home=server.home)
        self.manager.cancel_session(USER_A, SESSION_A)
        self.assertTrue((server.home / "auth.json").exists())
        self.manager.disconnect(USER_A)
        self.assertFalse((server.home / "auth.json").exists())

    def test_hostile_verification_urls_are_rejected_without_returning_them(self):
        for url in ("http://auth.openai.com/codex/device", "https://auth.openai.com.evil.test/codex/device",
                    "https://auth.openai.com@evil.test/codex/device", "javascript:alert(1)",
                    "https://auth.openai.com/codex/device?redirect=https://evil.test", "//evil.test"):
            with self.subTest(url=url), patch.object(FakeServer, "login", {**FakeServer.login, "verificationUrl": url}):
                with self.assertRaisesRegex(member.MemberCodexError, "^codex_authentication_unverified$"):
                    self.manager.connect(USER_A, SESSION_A)
                self.assertTrue(FakeServer.instances[-1].closed)
                self.assertNotIn(url, json.dumps(self.manager.status(USER_A)))

    def test_cancel_logout_disable_and_close_stop_worker_and_delete_only_own_auth(self):
        for operation in ("cancel", "cancel_session", "cancel_user", "close"):
            with self.subTest(operation=operation):
                result = self.manager.connect(USER_A, SESSION_A)
                server = FakeServer.instances[-1]
                path = server.home / "auth.json"
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
                os.close(descriptor)
                if operation == "cancel":
                    self.manager.cancel(USER_A, SESSION_A, result["connection_id"])
                elif operation == "cancel_session":
                    self.manager.cancel_session(USER_A, SESSION_A)
                elif operation == "cancel_user":
                    self.manager.cancel_user(USER_A)
                else:
                    self.manager.close()
                self.assertTrue(server.closed)
                self.assertFalse(path.exists())
                self.assertEqual(self.manager.poll(USER_A, SESSION_A, result["connection_id"])["state"], "cancelled")

    def test_timeout_and_process_limit_release_worker_slots(self):
        with patch.object(member, "LOGIN_TIMEOUT", 0.02):
            result = self.manager.connect(USER_A, SESSION_A)
            self.assertTrue(self.manager.connections[USER_A].done.wait(2))
            expired = self.manager.poll(USER_A, SESSION_A, result["connection_id"])
            self.assertEqual(expired["state"], "expired")
            self.assertNotIn("user_code", expired)
            self.assertTrue(FakeServer.instances[0].closed)
        with patch.object(member, "MAX_LOGINS", 1):
            self.manager.connect(USER_A, SESSION_A)
            with self.assertRaisesRegex(member.MemberCodexError, "^codex_connection_busy$"):
                self.manager.connect(USER_B, SESSION_B)

    def test_invalid_member_ids_and_hardlinked_auth_are_rejected(self):
        for user in ("../host", "A" * 32, "a" * 31, "a" * 32 + "/../host", None):
            with self.subTest(user=user), self.assertRaises(member.MemberCodexError):
                self.manager.status(user)
        home = self.manager._home(USER_A, create=True)
        original = self.root / "private-host-auth"
        original.write_text("never-read-or-delete", encoding="utf-8")
        os.link(original, home / "auth.json")
        with self.assertRaisesRegex(member.MemberCodexError, "^codex_unsafe_storage$"):
            self.manager.disconnect(USER_A)
        self.assertEqual(original.read_text(encoding="utf-8"), "never-read-or-delete")

    def test_disconnect_waits_for_member_extraction_before_erasing_auth(self):
        home = self.manager._home(USER_A, create=True)
        descriptor = os.open(home / "auth.json", os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
        started, release, disconnected = threading.Event(), threading.Event(), threading.Event()

        def extraction(*args, **kwargs):
            started.set()
            release.wait(2)
            self.assertTrue((home / "auth.json").exists())

        with patch.object(intake, "extract_requirements", side_effect=extraction):
            worker = threading.Thread(target=self.manager.extract_requirements, args=(USER_A, "request"))
            worker.start()
            self.assertTrue(started.wait(2))
            # Status must respond while extraction still owns the member lock.
            busy = self.manager.status(USER_A)
            self.assertEqual(busy['state'], 'busy')
            self.assertEqual(busy['reason'], 'codex_busy')
            self.assertFalse(busy['can_execute'])
            self.status.assert_not_called()
            shutdown = threading.Thread(target=lambda: (self.manager.disconnect(USER_A), disconnected.set()))
            shutdown.start()
            self.assertFalse(disconnected.wait(0.05))
            release.set()
            worker.join(2)
            shutdown.join(2)
            self.assertTrue(disconnected.is_set())
            self.assertFalse((home / "auth.json").exists())


class TransportTests(unittest.TestCase):
    def launch_synthetic(self, script, home, cancel, environment=None):
        real_popen = subprocess.Popen

        def launch(arguments, **kwargs):
            self.assertEqual(arguments[:5], ["codex", "--no-daemon", "-a", "never", "app-server"])
            self.assertIn('cli_auth_credentials_store="file"', arguments)
            self.assertIn('forced_login_method="chatgpt"', arguments)
            self.assertEqual(kwargs["env"]["CODEX_HOME"], str(home))
            self.assertFalse(kwargs["shell"])
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertNotIn("TYPESAFE_API_KEY", kwargs["env"])
            return real_popen([sys.executable, "-B", "-c", script], **kwargs)

        with patch.dict(os.environ, environment or {}, clear=False), \
             patch.object(intake, "_executable", return_value="codex"), \
             patch.object(member.subprocess, "Popen", side_effect=launch):
            return member._AppServer(home, cancel)

    def test_real_synthetic_rpc_uses_isolated_environment_and_structured_errors(self):
        script = ('import json,sys\nfor line in sys.stdin:\n'
                  ' x=json.loads(line)\n if "id" in x:\n'
                  '  print(json.dumps({"id":x["id"],"result":{"ok":True}}),flush=True)\n')
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            environment = {"CODEX_HOME": "host-home", "OPENAI_API_KEY": "private", "TYPESAFE_API_KEY": "private"}
            transport = self.launch_synthetic(script, home, threading.Event(), environment)
            try:
                self.assertEqual(transport.request("initialize", {}), {"ok": True})
                self.assertNotEqual(os.environ.get("CODEX_HOME"), str(home))
            finally:
                transport.close()
            self.assertIsNotNone(transport.process.poll())

    def test_real_synthetic_rpc_times_out_cancels_and_bounds_output(self):
        cases = [("import time; time.sleep(10)", "timeout"),
                 ("import time; time.sleep(10)", "cancel"),
                 ('import sys,time; sys.stdout.write("x"*70000); sys.stdout.flush(); time.sleep(10)', "overflow"),
                 ('import sys,time; sys.stdout.write("private-token\\n"); sys.stdout.flush(); time.sleep(10)', "malformed")]
        for script, mode in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                cancel = threading.Event()
                transport = self.launch_synthetic(script, Path(folder), cancel)
                try:
                    if mode == "cancel":
                        cancel.set()
                    expected = {"timeout": "codex_timeout", "cancel": "codex_connection_cancelled",
                                "overflow": "codex_output_limit", "malformed": "codex_connection_failed"}[mode]
                    # Output cases must let the synthetic Python process start on
                    # loaded Windows hosts before testing its malformed/large pipe.
                    # Keep the intentionally unresponsive case's short deadline.
                    rpc_timeout = 5 if mode in {"overflow", "malformed"} else 0.3
                    with patch.object(member, "RPC_TIMEOUT", rpc_timeout), self.assertRaisesRegex(
                            member.MemberCodexError, "^" + expected + "$"):
                        transport.request("initialize", {})
                finally:
                    transport.close()
                self.assertIsNotNone(transport.process.poll())


if __name__ == "__main__":
    unittest.main()
