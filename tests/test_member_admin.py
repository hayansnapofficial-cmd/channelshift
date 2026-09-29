"""Local master provisioning and member administration use synthetic credentials."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift.member_auth import (
    AuthError, MAX_AUDIT_RESULTS, MemberAuth, PENDING_TTL, VERIFICATION_TTL,
)


MASTER_PASSWORD = "Local!12"
MEMBER_PASSWORD = "synthetic member phrase"


def fast_derive(password, salt):
    return hashlib.sha256(salt + password).digest()


class MemberAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "auth" / "members.sqlite3"
        self.now = 1000000.0
        self.messages = []
        self.hash_mock = patch("channelshift.member_auth._derive", side_effect=fast_derive)
        self.hash_mock.start()
        self.addCleanup(self.hash_mock.stop)
        self.auth = MemberAuth(self.path, mailer=lambda email, token: self.messages.append((email, token)),
                               clock=lambda: self.now)

    def master(self):
        return self.auth.bootstrap_master("local_master", "master@example.com", MASTER_PASSWORD)

    def member(self, verified=True):
        self.auth.register("member_one", "member@example.com", MEMBER_PASSWORD)
        if verified:
            self.auth.verify(self.messages[-1][1], MEMBER_PASSWORD)
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT id FROM members WHERE username='member_one'").fetchone()[0]

    def row(self, user_id):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM members WHERE id=?", (user_id,)).fetchone())

    def test_bootstrap_is_offline_and_provides_ordinary_member_auth_without_email_verification(self):
        self.auth.mailer = None
        master = self.master()
        self.assertEqual(set(master), {"id", "username", "email", "role", "email_verified"})
        self.assertEqual(master["role"], "master")
        self.assertFalse(master["email_verified"])
        stored = self.row(master["id"])
        self.assertIsNone(stored["verified"])
        self.assertEqual(stored["bootstrapped"], self.now)
        self.assertEqual(self.messages, [])
        self.assertNotIn(MASTER_PASSWORD.encode(), self.path.read_bytes())
        login = self.auth.login("LOCAL_MASTER", MASTER_PASSWORD)
        self.assertEqual(login["user"], master)
        self.assertEqual(self.auth.authenticate(login["session_token"]), master)
        self.auth.logout(login["session_token"])
        self.assertIsNone(self.auth.authenticate(login["session_token"]))
        self.now += PENDING_TTL * 2
        self.assertEqual(self.auth.login("local_master", MASTER_PASSWORD)["user"], master)
        self.assertIsNone(self.row(master["id"])["verified"])

    def test_bootstrap_never_promotes_existing_username_or_email(self):
        member_id = self.member()
        original = self.row(member_id)
        for username, email in (("member_one", "new@example.com"), ("new_master", "member@example.com")):
            with self.subTest(username=username), self.assertRaisesRegex(AuthError, "^member_already_exists$"):
                self.auth.bootstrap_master(username, email, MASTER_PASSWORD)
        self.assertEqual(self.row(member_id), original)
        self.assertEqual(self.auth.login("member_one", MEMBER_PASSWORD)["user"]["role"], "member")
        master = self.master()
        before = self.row(master["id"])
        with self.assertRaisesRegex(AuthError, "^master_already_exists$"):
            self.auth.bootstrap_master("local_master", "master@example.com", "changed-password")
        self.assertEqual(self.row(master["id"]), before)

    def test_concurrent_bootstrap_creates_exactly_one_master_and_one_audit_entry(self):
        barrier = threading.Barrier(2)
        def provision(index):
            other = MemberAuth(self.path, clock=lambda: self.now)
            barrier.wait(timeout=5)
            try:
                return other.bootstrap_master(f"master_{index}", f"master{index}@example.com", MASTER_PASSWORD)
            except AuthError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(provision, range(2)))
        masters = [item for item in results if isinstance(item, dict)]
        self.assertEqual(len(masters), 1)
        self.assertEqual(results.count("master_already_exists"), 1)
        self.assertEqual(len(self.auth.admin_members(masters[0]["id"])), 1)
        self.assertEqual(len(self.auth.admin_audit(masters[0]["id"])), 1)

    def test_bootstrap_cannot_reclaim_expired_pending_identity(self):
        member_id = self.member(verified=False)
        self.now += PENDING_TTL * 2
        with self.assertRaisesRegex(AuthError, "^member_already_exists$"):
            self.auth.bootstrap_master("member_one", "member@example.com", MASTER_PASSWORD)
        self.assertEqual(self.row(member_id)["role"], "member")

    def test_master_cannot_be_reset_by_registration_resend_or_verification(self):
        master = self.master()
        original = self.row(master["id"])
        result = self.auth.register("local_master", "master@example.com", MEMBER_PASSWORD)
        self.assertEqual(set(result), {"message"})
        self.auth.resend("master@example.com")
        self.assertEqual(self.messages, [])
        self.assertEqual(self.row(master["id"]), original)
        # Even a stale verification record must never activate the pending flow.
        token = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE members SET pending_expires=? WHERE id=?", (self.now + PENDING_TTL, master["id"]))
            db.execute("INSERT INTO verification VALUES (?, ?, ?)",
                       (hashlib.sha256(token.encode("ascii")).hexdigest(), master["id"], self.now + VERIFICATION_TTL))
        with self.assertRaisesRegex(AuthError, "^invalid_verification_token$"):
            self.auth.verify(token, MEMBER_PASSWORD)
        self.assertIsNone(self.row(master["id"])["verified"])
        self.assertEqual(self.auth.login("local_master", MASTER_PASSWORD)["user"], master)

    def test_password_policy_uses_same_eight_character_minimum_for_master_and_members(self):
        for invalid in ("x" * 7, "x" * 129, "x" * 8 + "\x00", None):
            with self.subTest(password=invalid), self.assertRaisesRegex(AuthError, "^invalid_password$"):
                self.auth.bootstrap_master("local_master", "master@example.com", invalid)
        self.master()
        self.assertTrue(self.auth.login("local_master", MASTER_PASSWORD)["session_token"])
        with self.assertRaisesRegex(AuthError, "^invalid_password$"):
            self.auth.register("member_one", "member@example.com", "x" * 7)
        self.auth.register("member_one", "member@example.com", MASTER_PASSWORD)
        with self.assertRaisesRegex(AuthError, "^invalid_password$"):
            self.auth.verify(self.messages[-1][1], "x" * 7)
        self.assertEqual(self.auth.verify(self.messages[-1][1], MASTER_PASSWORD), {"verified": True})
        self.assertTrue(self.auth.login("member_one", MASTER_PASSWORD)["session_token"])
        for invalid in ("", "x" * 129, None, "x\x00"):
            with self.subTest(password=invalid), self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
                self.auth.login("local_master", invalid)
        # Short failed logins still do the stored-credential check.
        with patch("channelshift.member_auth._derive", side_effect=fast_derive) as derive:
            with self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
                self.auth.login("local_master", "x")
            self.assertEqual(derive.call_count, 1)

    def test_master_does_not_consume_pending_member_capacity(self):
        self.master()
        with patch("channelshift.member_auth.MAX_PENDING", 1):
            self.member(verified=False)

    def test_regular_missing_and_inactive_actors_cannot_administer_members(self):
        master = self.master()
        member_id = self.member()
        for actor in (member_id, "missing", None):
            for method in (lambda: self.auth.admin_members(actor), lambda: self.auth.admin_audit(actor),
                           lambda: self.auth.admin_set_disabled(actor, member_id, True, "Synthetic reason")):
                with self.subTest(actor=actor), self.assertRaisesRegex(AuthError, "^admin_forbidden$"):
                    method()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE members SET disabled=? WHERE id=?", (self.now, master["id"]))
        with self.assertRaisesRegex(AuthError, "^admin_forbidden$"):
            self.auth.admin_members(master["id"])

    def test_suspension_revokes_all_sessions_and_reactivation_requires_new_login(self):
        master = self.master()
        member_id = self.member()
        tokens = [self.auth.login("member_one", MEMBER_PASSWORD)["session_token"] for _ in range(2)]
        before = self.row(member_id)
        disabled = self.auth.admin_set_disabled(master["id"], member_id, True, "  Synthetic suspension  ")
        self.assertEqual(disabled["disabled"], self.now)
        self.assertEqual(disabled["role"], "member")
        self.assertTrue(disabled["email_verified"])
        for token in tokens:
            self.assertIsNone(self.auth.authenticate(token))
        with self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
            self.auth.login("member_one", MEMBER_PASSWORD)
        enabled = self.auth.admin_set_disabled(master["id"], member_id, False, "Synthetic restoration")
        self.assertIsNone(enabled["disabled"])
        for token in tokens:
            self.assertIsNone(self.auth.authenticate(token))
        self.assertTrue(self.auth.login("member_one", MEMBER_PASSWORD)["session_token"])
        after = self.row(member_id)
        for key in ("role", "password_hash", "salt", "verified", "bootstrapped"):
            self.assertEqual(after[key], before[key])
        audit = self.auth.admin_audit(master["id"])
        self.assertEqual([item["action"] for item in audit], ["member_enabled", "member_disabled", "master_bootstrap"])
        self.assertEqual(audit[1]["reason"], "Synthetic suspension")
        self.assertEqual(audit[1]["actor_id"], master["id"])
        self.assertEqual(audit[1]["target_id"], member_id)
        self.assertEqual(audit[-1]["actor_id"], "local-bootstrap")

    def test_suspension_racing_password_check_cannot_issue_a_new_session(self):
        master = self.master()
        member_id = self.member()
        def suspend_during_hash(password, salt):
            self.auth.admin_set_disabled(master["id"], member_id, True, "Concurrent suspension")
            return fast_derive(password, salt)
        with patch("channelshift.member_auth._derive", side_effect=suspend_during_hash):
            with self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
                self.auth.login("member_one", MEMBER_PASSWORD)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id=?", (member_id,)).fetchone()[0], 0)

    def test_suspended_pending_member_cannot_verify_or_resend(self):
        master = self.master()
        member_id = self.member(verified=False)
        token = self.messages[-1][1]
        self.auth.admin_set_disabled(master["id"], member_id, True, "Pending suspension")
        self.auth.resend("member@example.com")
        self.assertEqual(len(self.messages), 1)
        with self.assertRaisesRegex(AuthError, "^invalid_verification_token$"):
            self.auth.verify(token, MEMBER_PASSWORD)
        self.now += PENDING_TTL * 2
        self.auth.register("member_one", "member@example.com", MEMBER_PASSWORD)
        self.assertEqual(len(self.messages), 1)
        self.assertIsNotNone(self.row(member_id)["disabled"])

    def test_master_cannot_be_suspended_and_role_has_no_public_mutation_parameter(self):
        master = self.master()
        for disabled in (True, False):
            with self.assertRaisesRegex(AuthError, "^master_protected$"):
                self.auth.admin_set_disabled(master["id"], master["id"], disabled, "Self modification")
        with self.assertRaises(TypeError):
            self.auth.register("member_one", "member@example.com", MEMBER_PASSWORD, role="master")
        with self.assertRaises(TypeError):
            self.auth.admin_set_disabled(master["id"], master["id"], False, "Role change", role="member")
        self.assertEqual(self.row(master["id"])["role"], "master")
        self.assertIsNone(self.row(master["id"])["disabled"])
        self.assertEqual(len(self.auth.admin_audit(master["id"])), 1)

    def test_admin_input_validation_and_audit_are_atomic(self):
        master = self.master()
        member_id = self.member()
        for disabled, reason in ((1, "reason"), ("false", "reason"), (True, ""), (True, "  "),
                                 (True, "x" * 501), (True, None), (True, "bad\x00reason"), (True, "bad\ud800reason")):
            with self.subTest(disabled=disabled, reason=reason), self.assertRaisesRegex(AuthError, "^invalid_admin_request$"):
                self.auth.admin_set_disabled(master["id"], member_id, disabled, reason)
        with self.assertRaisesRegex(AuthError, "^member_not_found$"):
            self.auth.admin_set_disabled(master["id"], "missing", True, "Synthetic reason")
        with patch.object(self.auth, "_audit", side_effect=AuthError("audit_failure")):
            with self.assertRaisesRegex(AuthError, "^audit_failure$"):
                self.auth.admin_set_disabled(master["id"], member_id, True, "Synthetic reason")
        self.assertIsNone(self.row(member_id)["disabled"])
        self.assertEqual(len(self.auth.admin_audit(master["id"])), 1)

    def test_safe_admin_metadata_and_bounded_newest_audit_records(self):
        master = self.master()
        member_id = self.member()
        members = self.auth.admin_members(master["id"])
        safe_fields = {"id", "username", "email", "role", "email_verified", "created", "verified", "disabled", "locally_bootstrapped"}
        for member in members:
            self.assertEqual(set(member), safe_fields)
            self.assertEqual(member["locally_bootstrapped"], member["role"] == "master")
        with closing(sqlite3.connect(self.path)) as db, db:
            for index in range(MAX_AUDIT_RESULTS + 5):
                db.execute("INSERT INTO member_audit (created, actor_id, target_id, action, reason) VALUES (?, ?, ?, ?, ?)",
                           (self.now + index, master["id"], member_id, "member_disabled", f"Synthetic reason {index}"))
        audit = self.auth.admin_audit(master["id"])
        self.assertEqual(len(audit), MAX_AUDIT_RESULTS)
        self.assertEqual(audit[0]["reason"], f"Synthetic reason {MAX_AUDIT_RESULTS + 4}")
        for item in audit:
            self.assertEqual(set(item), {"id", "created", "actor_id", "target_id", "action", "reason"})
        self.assertNotIn(MASTER_PASSWORD, str(members) + str(audit))
        self.assertNotIn(MEMBER_PASSWORD, str(members) + str(audit))

    def test_legacy_schema_migration_preserves_verified_identity_and_existing_session(self):
        legacy_path = self.path.parent / "legacy.sqlite3"
        salt = bytes(range(16))
        password_hash = fast_derive(MEMBER_PASSWORD.encode(), salt)
        user_id = "1" * 32
        token = secrets.token_urlsafe(32)
        legacy_path.touch(mode=0o600)
        with closing(sqlite3.connect(legacy_path)) as db, db:
            db.executescript("""
                CREATE TABLE members (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL, created REAL NOT NULL,
                    verified REAL, pending_expires REAL NOT NULL
                );
                CREATE TABLE sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    created REAL NOT NULL, expires REAL NOT NULL
                );
            """)
            db.execute("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (user_id, "legacy_member", "legacy@example.com", salt, password_hash, self.now, self.now, self.now))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (hashlib.sha256(token.encode("ascii")).hexdigest(), user_id, self.now, self.now + 3600))
        first = MemberAuth(legacy_path, clock=lambda: self.now)
        second = MemberAuth(legacy_path, clock=lambda: self.now)
        user = first.authenticate(token)
        self.assertEqual(user["id"], user_id)
        self.assertEqual(user["role"], "member")
        self.assertTrue(user["email_verified"])
        self.assertEqual(second.login("legacy_member", MEMBER_PASSWORD)["user"], user)
        with closing(sqlite3.connect(legacy_path)) as db:
            self.assertEqual(db.execute("SELECT salt, password_hash, verified, disabled, bootstrapped FROM members").fetchone(),
                             (salt, password_hash, self.now, None, None))
            self.assertEqual(db.execute("SELECT count(*) FROM member_audit").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
