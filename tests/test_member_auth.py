"""Member identity boundary tests; SMTP is mocked and no mail is sent."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift.member_auth import (AuthError, MemberAuth, PENDING_TTL, RATE_WINDOW,
                                      SESSION_TTL, VERIFICATION_TTL, _derive, _HASH_SLOTS,
                                      normalize_email, normalize_username)
from channelshift.member_mail import SMTPMailer, from_environment


OLD_PASSWORD = "initial password phrase"
NEW_PASSWORD = "verified owner password"


def fast_derive(password, salt):
    return hashlib.sha256(salt + password).digest()


class MemberAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "auth" / "members.sqlite3"
        self.now = 1000000.0
        self.messages = []
        self.hash_mock = patch("channelshift.member_auth._derive", side_effect=fast_derive).start()
        self.addCleanup(patch.stopall)
        self.auth = MemberAuth(self.path, mailer=lambda email, token: self.messages.append((email, token)),
                               clock=lambda: self.now)

    def register(self, username="member_one", email="member@example.com"):
        return self.auth.register(username, email, OLD_PASSWORD)

    def verified(self):
        self.register()
        self.auth.verify(self.messages[-1][1], NEW_PASSWORD)

    def scalar(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchone()[0]

    def test_signup_returns_no_token_and_unverified_account_cannot_login(self):
        result = self.register("MEMBER_ONE", " Member@Example.com ")
        self.assertEqual(set(result), {"message"})
        self.assertEqual(self.messages[0][0], "member@example.com")
        self.assertEqual(len(self.messages[0][1]), 43)
        with self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
            self.auth.login("member_one", OLD_PASSWORD)
        self.assertEqual(self.scalar("SELECT count(*) FROM sessions"), 0)
        self.assertNotIn(self.messages[0][1].encode(), self.path.read_bytes())
        self.assertNotIn(OLD_PASSWORD.encode(), self.path.read_bytes())

    def test_verification_replaces_pre_registration_password_and_is_single_use(self):
        self.register()
        token = self.messages[0][1]
        self.assertEqual(self.auth.verify(token, NEW_PASSWORD), {"verified": True})
        with self.assertRaisesRegex(AuthError, "^invalid_verification_token$"):
            self.auth.verify(token, NEW_PASSWORD)
        with self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
            self.auth.login("member_one", OLD_PASSWORD)
        result = self.auth.login("MEMBER_ONE", NEW_PASSWORD)
        self.assertEqual(set(result), {"session_token", "user"})
        self.assertEqual(set(result["user"]), {"id", "username", "email", "role", "email_verified"})
        self.assertEqual(result["user"]["role"], "member")
        self.assertTrue(result["user"]["email_verified"])
        self.assertRegex(result["user"]["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.auth.authenticate(result["session_token"]), result["user"])
        self.assertNotIn(result["session_token"].encode(), self.path.read_bytes())

    def test_expiry_and_resend_invalidate_old_token(self):
        self.register()
        old = self.messages[-1][1]
        self.now += VERIFICATION_TTL
        with self.assertRaisesRegex(AuthError, "invalid_verification_token"):
            self.auth.verify(old, NEW_PASSWORD)
        self.auth.resend("member@example.com")
        newer = self.messages[-1][1]
        self.auth.resend("member@example.com")
        newest = self.messages[-1][1]
        for token in (old, newer):
            with self.assertRaisesRegex(AuthError, "invalid_verification_token"):
                self.auth.verify(token, NEW_PASSWORD)
        self.assertEqual(self.auth.verify(newest, NEW_PASSWORD), {"verified": True})

    def test_expired_pending_registration_can_be_reclaimed(self):
        self.register()
        token = self.messages[-1][1]
        self.now += PENDING_TTL
        self.register()
        self.assertEqual(self.scalar("SELECT count(*) FROM members"), 1)
        with self.assertRaisesRegex(AuthError, "invalid_verification_token"):
            self.auth.verify(token, NEW_PASSWORD)
        self.auth.verify(self.messages[-1][1], NEW_PASSWORD)

    def test_duplicates_and_unknown_resend_use_generic_response_without_overwrite(self):
        first = self.register()
        self.assertEqual(self.register("MEMBER_ONE", "another@example.com"), first)
        self.assertEqual(self.register("member_two", "MEMBER@EXAMPLE.COM"), first)
        self.assertEqual(self.auth.resend("unknown@example.com"), first)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM members"), 1)

    def test_missing_mail_fails_closed_and_provider_error_is_redacted(self):
        auth = MemberAuth(self.path, clock=lambda: self.now)
        with self.assertRaisesRegex(AuthError, "^email_not_configured$"):
            auth.register("member_one", "member@example.com", OLD_PASSWORD)
        self.assertEqual(self.scalar("SELECT count(*) FROM members"), 0)
        self.auth.mailer = lambda *args: (_ for _ in ()).throw(RuntimeError("secret smtp password"))
        with self.assertRaisesRegex(AuthError, "^email_delivery_failed$"):
            self.register()
        self.assertEqual(self.scalar("SELECT count(*) FROM members"), 0)

    def test_resend_mail_failure_revokes_the_new_and_old_tokens(self):
        self.register()
        token = self.messages[-1][1]
        self.auth.mailer = lambda *args: (_ for _ in ()).throw(RuntimeError("secret"))
        with self.assertRaisesRegex(AuthError, "^email_delivery_failed$"):
            self.auth.resend("member@example.com")
        self.assertEqual(self.scalar("SELECT count(*) FROM verification"), 0)
        with self.assertRaisesRegex(AuthError, "invalid_verification_token"):
            self.auth.verify(token, NEW_PASSWORD)

    def test_login_dummy_work_and_durable_account_limit_precede_more_hash_work(self):
        for _ in range(5):
            with self.assertRaisesRegex(AuthError, "invalid_credentials"):
                self.auth.login("missing_user", NEW_PASSWORD)
        self.assertEqual(self.hash_mock.call_count, 5)
        fresh = MemberAuth(self.path, clock=lambda: self.now)
        with self.assertRaisesRegex(AuthError, "^rate_limited$"):
            fresh.login("missing_user", NEW_PASSWORD)
        self.assertEqual(self.hash_mock.call_count, 5)
        self.now += RATE_WINDOW
        with self.assertRaisesRegex(AuthError, "invalid_credentials"):
            fresh.login("missing_user", NEW_PASSWORD)
        self.assertEqual(self.hash_mock.call_count, 6)

    def test_blocked_account_cannot_spend_another_clients_login_budget(self):
        self.verified()
        for _ in range(100):
            with self.assertRaises(AuthError):
                self.auth.login('missing_user', NEW_PASSWORD, client_ip='192.0.2.1')
        self.assertEqual(self.hash_mock.call_count, 7)  # register, verify, five dummy hashes
        fresh = MemberAuth(self.path, clock=lambda: self.now)
        self.assertTrue(fresh.login('member_one', NEW_PASSWORD, client_ip='192.0.2.2')['session_token'])
        self.assertEqual(self.scalar("SELECT count(*) FROM rate_limits WHERE bucket LIKE 'login:%'"), 0)

    def test_client_limit_covers_rotating_accounts_without_allocating_after_rejection(self):
        self.verified()
        before = self.hash_mock.call_count
        for index in range(30):
            with self.assertRaisesRegex(AuthError, "invalid_credentials"):
                self.auth.login(f"missing_{index}", NEW_PASSWORD, client_ip='192.0.2.1')
        for index in range(50):
            with self.assertRaisesRegex(AuthError, "rate_limited"):
                self.auth.login(f"blocked_{index}", NEW_PASSWORD, client_ip='192.0.2.1')
        self.assertEqual(self.hash_mock.call_count - before, 30)
        self.assertEqual(self.scalar('SELECT count(*) FROM login_accounts'), 30)
        self.assertEqual(self.scalar('SELECT max(count) FROM login_clients'), 30)
        self.assertTrue(self.auth.login('member_one', NEW_PASSWORD, client_ip='192.0.2.2')['session_token'])

    def test_legacy_and_other_auth_buckets_cannot_exhaust_login_state(self):
        self.verified()
        with self.auth._connect() as db:
            db.execute('INSERT INTO rate_limits VALUES (?, ?, ?)', ('login:global', self.now, 999))
        with patch('channelshift.member_auth.MAX_RATE_BUCKETS', 8):
            for index in range(30):
                try:
                    self.auth.resend(f'unknown{index}@example.test')
                except AuthError as error:
                    self.assertEqual(str(error), 'rate_limited')
        self.assertGreaterEqual(self.scalar('SELECT count(*) FROM rate_limits'), 8)
        self.assertTrue(self.auth.login('member_one', NEW_PASSWORD)['session_token'])

    def test_pool_saturation_preserves_member_budget_and_allows_unrelated_client(self):
        self.verified()
        self.auth.bootstrap_master('other_member', 'other@example.test', NEW_PASSWORD)
        for _ in range(5):
            with self.assertRaisesRegex(AuthError, '^invalid_credentials$'):
                self.auth.login('member_one', 'wrong-password', client_ip='192.0.2.1')
        with patch('channelshift.member_auth.MAX_LOGIN_CLIENTS', 4), \
                patch('channelshift.member_auth.MAX_UNKNOWN_LOGINS', 4):
            for index in range(20):
                with self.assertRaisesRegex(AuthError, '^invalid_credentials$'):
                    self.auth.login(f'unknown_{index}', NEW_PASSWORD, client_ip=f'198.51.100.{index + 1}')
            before = self.hash_mock.call_count
            with self.assertRaisesRegex(AuthError, '^rate_limited$'):
                self.auth.login('member_one', NEW_PASSWORD, client_ip='203.0.113.1')
            self.assertEqual(self.hash_mock.call_count, before)
            # A new client can still be evaluated, without a blanket full-table denial.
            with self.assertRaisesRegex(AuthError, '^invalid_credentials$'):
                self.auth.login('fresh_user', NEW_PASSWORD, client_ip='203.0.113.2')
            self.assertTrue(self.auth.login('other_member', NEW_PASSWORD, client_ip='203.0.113.3')['session_token'])
            self.assertLessEqual(self.scalar('SELECT count(*) FROM login_clients'), 4)
            self.assertEqual(self.scalar('SELECT count(*) FROM login_accounts WHERE member_id IS NOT NULL'), 2)
            self.assertLessEqual(self.scalar('SELECT count(*) FROM login_accounts WHERE member_id IS NULL'), 4)
        self.now += RATE_WINDOW
        self.assertTrue(self.auth.login('member_one', NEW_PASSWORD, client_ip='203.0.113.2')['session_token'])

    def test_client_limit_is_durable_expires_and_canonicalizes_ipv4_mapped_addresses(self):
        for index in range(30):
            with self.assertRaisesRegex(AuthError, '^invalid_credentials$'):
                self.auth.login(f'unknown_{index}', NEW_PASSWORD, client_ip='192.0.2.1')
        fresh = MemberAuth(self.path, clock=lambda: self.now)
        with self.assertRaisesRegex(AuthError, '^rate_limited$'):
            fresh.login('fresh_user', NEW_PASSWORD, client_ip='::ffff:192.0.2.1')
        self.assertEqual(self.hash_mock.call_count, 30)
        self.now += RATE_WINDOW
        with self.assertRaisesRegex(AuthError, '^invalid_credentials$'):
            fresh.login('fresh_user', NEW_PASSWORD, client_ip='192.0.2.1')
        self.assertEqual(self.hash_mock.call_count, 31)

    def test_concurrent_account_admission_never_exceeds_five_hashes(self):
        def attempt(index):
            try:
                self.auth.login('missing_user', NEW_PASSWORD, client_ip=f'192.0.2.{index + 1}')
            except AuthError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(results.count('invalid_credentials'), 5)
        self.assertEqual(results.count('rate_limited'), 7)
        self.assertEqual(self.hash_mock.call_count, 5)

    def test_registration_capacity_is_bounded(self):
        with patch("channelshift.member_auth.MAX_PENDING", 1):
            self.register()
            with self.assertRaisesRegex(AuthError, "registration_unavailable"):
                self.register("member_two", "second@example.com")
        self.assertEqual(self.scalar("SELECT count(*) FROM members"), 1)

    def test_sessions_expire_logout_revokes_and_old_sessions_are_bounded(self):
        self.verified()
        tokens = [self.auth.login("member_one", NEW_PASSWORD)["session_token"] for _ in range(5)]
        self.now += RATE_WINDOW
        newest = self.auth.login("member_one", NEW_PASSWORD)["session_token"]
        self.assertEqual(self.scalar("SELECT count(*) FROM sessions"), 5)
        self.assertIsNone(self.auth.authenticate(tokens[0]))
        self.auth.logout(newest)
        self.auth.logout(newest)
        self.assertIsNone(self.auth.authenticate(newest))
        self.assertIsNotNone(self.auth.authenticate(tokens[-1]))
        self.now += SESSION_TTL
        self.assertIsNone(self.auth.authenticate(tokens[-1]))
        for token in (None, [], "", "x" * 10000, "not-a-token"):
            self.assertIsNone(self.auth.authenticate(token))
            self.auth.logout(token)

    def test_concurrent_verification_consumes_token_only_once(self):
        self.register()
        token = self.messages[-1][1]
        barrier = threading.Barrier(2)
        def derive(password, salt):
            barrier.wait(timeout=5)
            return fast_derive(password, salt)
        def verify():
            try:
                return self.auth.verify(token, NEW_PASSWORD)
            except AuthError as error:
                return str(error)
        with patch("channelshift.member_auth._derive", side_effect=derive), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: verify(), range(2)))
        self.assertEqual(results.count({"verified": True}), 1)
        self.assertEqual(results.count("invalid_verification_token"), 1)

    def test_concurrent_registration_keeps_one_unique_identity(self):
        barrier = threading.Barrier(2)
        def derive(password, salt):
            barrier.wait(timeout=5)
            return fast_derive(password, salt)
        with patch("channelshift.member_auth._derive", side_effect=derive), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.register(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.scalar("SELECT count(*) FROM members"), 1)
        self.assertEqual(len(self.messages), 1)

    def test_input_limits_unicode_password_and_normalization(self):
        for username in ("abc", "a" * 33, "한국계정", "Kimtest", "name-with-dash", True):
            with self.assertRaisesRegex(AuthError, "invalid_username"):
                normalize_username(username)
        for email in ("name", "a@b", "a..b@example.com", ".a@example.com", "a@-example.com", "a\r\nb@example.com", []):
            with self.assertRaisesRegex(AuthError, "invalid_email"):
                normalize_email(email)
        for password in ("x" * 7, "x" * 129, "x" * 8 + "\x00", "x" * 8 + "\ud800", None):
            with self.assertRaisesRegex(AuthError, "invalid_password"):
                self.auth.register("member_one", "member@example.com", password)
        unicode_password = "비밀번호설정12"
        self.auth.register("member_one", "member@example.com", unicode_password)
        self.auth.verify(self.messages[-1][1], unicode_password)
        self.assertTrue(self.auth.login("member_one", unicode_password)["session_token"])

    @unittest.skipIf(os.name == "nt", "POSIX owner/mode assertions; Windows uses inherited ACLs")
    def test_private_storage_modes_and_symlinks_fail_closed(self):
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(AuthError, "unsafe_auth_storage"):
            MemberAuth(self.path)
        self.path.chmod(0o600)
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaisesRegex(AuthError, "unsafe_auth_storage"):
            MemberAuth(link / "other.sqlite3")


class PasswordCostTests(unittest.TestCase):
    def test_actual_scrypt_parameters_and_serial_work_bound(self):
        with patch("channelshift.member_auth.hashlib.scrypt", wraps=hashlib.scrypt) as real:
            value = _derive(NEW_PASSWORD.encode(), bytes(range(16)))
            self.assertEqual(value, _derive(NEW_PASSWORD.encode(), bytes(range(16))))
            self.assertEqual(len(value), 32)
            self.assertEqual(real.call_args.kwargs["n"], 2**17)
            self.assertEqual(real.call_args.kwargs["r"], 8)
            self.assertEqual(real.call_args.kwargs["p"], 1)
            self.assertEqual(real.call_args.kwargs["maxmem"], 256 * 1024 * 1024)
        self.assertTrue(_HASH_SLOTS.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(AuthError, "^auth_busy$"):
                _derive(NEW_PASSWORD.encode(), bytes(16))
        finally:
            _HASH_SLOTS.release()


class MemberMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.password_file = Path(self.temp.name) / "smtp-password"
        self.password_file.write_text("synthetic-app-password", encoding="utf-8")
        self.password_file.chmod(0o600)

    def mailer(self, mode="starttls", base_url="http://127.0.0.1:9000"):
        return SMTPMailer("smtp.example.com", 587 if mode == "starttls" else 465,
                          "sender@example.com", "sender@example.com", self.password_file, mode, base_url)

    def test_unconfigured_and_partial_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(from_environment("http://127.0.0.1:9000"))
        with patch.dict(os.environ, {"CHANNELSHIFT_SMTP_HOST": "smtp.example.com"}, clear=True):
            with self.assertRaisesRegex(AuthError, "invalid_smtp_configuration"):
                from_environment("http://127.0.0.1:9000")
        settings = {"CHANNELSHIFT_SMTP_HOST": "smtp.example.com", "CHANNELSHIFT_SMTP_FROM": "sender@example.com",
                    "CHANNELSHIFT_SMTP_USER": "sender@example.com", "CHANNELSHIFT_SMTP_PASSWORD_FILE": str(self.password_file)}
        with patch.dict(os.environ, settings, clear=True):
            self.assertEqual(from_environment("http://127.0.0.1:9000").mode, "starttls")

    def test_starttls_verifies_cert_before_login_and_token_is_fragment_only(self):
        token = secrets.token_urlsafe(32)
        with patch("channelshift.member_mail.smtplib.SMTP") as smtp_class:
            self.mailer().send_verification("member@example.com", token, "member_one")
        smtp = smtp_class.return_value.__enter__.return_value
        self.assertEqual([call[0] for call in smtp.method_calls], ["ehlo", "starttls", "ehlo", "login", "send_message"])
        context = smtp.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        message = smtp.send_message.call_args.args[0]
        self.assertIn("/login#verify=" + token, message.get_content())
        self.assertNotIn("?verify=", message.get_content())
        self.assertIn("member_one", message.get_content())
        self.assertIn("직접 가입하지 않았다면", message.get_content())
        self.assertNotIn("synthetic-app-password", message.get_content())

    def test_tls_failure_does_not_fall_back_or_expose_provider_message(self):
        with patch("channelshift.member_mail.smtplib.SMTP") as smtp_class, patch("channelshift.member_mail.smtplib.SMTP_SSL") as ssl_class:
            smtp = smtp_class.return_value.__enter__.return_value
            smtp.starttls.side_effect = RuntimeError("synthetic-app-password secret response")
            with self.assertRaisesRegex(AuthError, "^email_delivery_failed$"):
                self.mailer()("member@example.com", secrets.token_urlsafe(32))
            smtp.login.assert_not_called()
            smtp.send_message.assert_not_called()
            ssl_class.assert_not_called()

    def test_smtps_and_untrusted_origins(self):
        with patch("channelshift.member_mail.smtplib.SMTP_SSL") as smtp_class:
            self.mailer("smtps", "https://members.example.com")("member@example.com", secrets.token_urlsafe(32))
            self.assertTrue(smtp_class.call_args.kwargs["context"].check_hostname)
        for origin in ("http://example.com", "http://localhost:9000", "https://u:p@example.com", "https://example.com/path",
                       "https://example.com?x=1", "https://example.com/#x", "https://example.com:0", "javascript:alert(1)"):
            with self.subTest(origin=origin), self.assertRaisesRegex(AuthError, "invalid_public_base_url"):
                self.mailer(base_url=origin)


if __name__ == "__main__":
    unittest.main()
