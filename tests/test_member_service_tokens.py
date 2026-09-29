"""Platform MCP credentials are separate, member-bound, expiring hash records."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift.member_auth import AuthError, MemberAuth, SERVICE_TOKEN_TTL


PASSWORD = "synthetic service member password"


class MemberServiceTokenTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "members.sqlite3"
        self.now = 1000000.0
        self.messages = []
        derive = patch("channelshift.member_auth._derive",
                       side_effect=lambda password, salt: hashlib.sha256(salt + password).digest())
        derive.start()
        self.addCleanup(derive.stop)
        self.auth = MemberAuth(self.path, clock=lambda: self.now,
                               mailer=lambda email, token: self.messages.append((email, token)))
        self.master = self.auth.bootstrap_master("master_user", "master@example.test", PASSWORD)
        self.member = self.create_member("member_one", "one@example.test")

    def create_member(self, username, email, *, verified=True):
        self.auth.register(username, email, PASSWORD)
        if verified:
            self.auth.verify(self.messages[-1][1], PASSWORD)
        with closing(sqlite3.connect(self.path)) as db:
            user_id = db.execute("SELECT id FROM members WHERE username=?", (username,)).fetchone()[0]
        return {"id": user_id, "username": username}

    def records(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT token_hash, member_id, created, expires FROM service_tokens").fetchall()

    def test_issue_returns_raw_once_but_persists_only_hash_and_expiry(self):
        result = self.auth.create_service_token(self.member["id"])
        self.assertEqual(set(result), {"token", "expires_at", "token_type"})
        self.assertRegex(result["token"], r"\Acs_mcp_[A-Za-z0-9_-]{43}\Z")
        self.assertEqual(result["token_type"], "Bearer")
        self.assertEqual(result["expires_at"], self.now + SERVICE_TOKEN_TTL)
        digest = hashlib.sha256(result["token"].encode("ascii")).hexdigest()
        self.assertEqual(self.records(), [(digest, self.member["id"], self.now, result["expires_at"])])
        self.assertNotIn(result["token"].encode("ascii"), self.path.read_bytes())
        status = self.auth.service_token_status(self.member["id"])
        self.assertEqual(status, {"connected": True, "expires_at": result["expires_at"]})
        self.assertNotIn(result["token"], json.dumps(status))
        self.assertNotIn(digest, json.dumps(status))
        user = self.auth.authenticate_service_token(result["token"])
        self.assertEqual(user["id"], self.member["id"])
        self.assertEqual(set(user), {"id", "username", "email", "role", "email_verified"})

    def test_rotation_revokes_old_token_atomically_and_survives_restart(self):
        first = self.auth.create_service_token(self.member["id"])
        second = self.auth.create_service_token(self.member["id"])
        self.assertNotEqual(first["token"], second["token"])
        fresh = MemberAuth(self.path, clock=lambda: self.now)
        self.assertIsNone(fresh.authenticate_service_token(first["token"]))
        self.assertEqual(fresh.authenticate_service_token(second["token"])["id"], self.member["id"])
        self.assertEqual(len(self.records()), 1)
        for item in (first, second):
            self.assertNotIn(item["token"].encode("ascii"), self.path.read_bytes())

    def test_expiry_is_enforced_at_boundary_without_requiring_cleanup(self):
        result = self.auth.create_service_token(self.member["id"])
        self.now = result["expires_at"] - 0.01
        self.assertIsNotNone(self.auth.authenticate_service_token(result["token"]))
        self.now = result["expires_at"]
        self.assertIsNone(self.auth.authenticate_service_token(result["token"]))
        self.assertEqual(self.auth.service_token_status(self.member["id"]),
                         {"connected": False, "expires_at": None})
        self.auth.login(self.member["username"], PASSWORD)
        self.assertEqual(self.records(), [])

    def test_unverified_missing_and_disabled_members_cannot_issue(self):
        pending = self.create_member("pending_user", "pending@example.test", verified=False)
        self.auth.admin_set_disabled(self.master["id"], self.member["id"], True, "Synthetic suspension")
        for user_id in (pending["id"], self.member["id"], "f" * 32):
            with self.subTest(user_id=user_id), self.assertRaisesRegex(AuthError, "^invalid_credentials$"):
                self.auth.create_service_token(user_id)
            self.assertEqual(self.auth.service_token_status(user_id), {"connected": False, "expires_at": None})
        self.assertEqual(self.records(), [])
        # The offline master can authenticate without claiming email verification.
        token = self.auth.create_service_token(self.master["id"])["token"]
        user = self.auth.authenticate_service_token(token)
        self.assertEqual(user["role"], "master")
        self.assertFalse(user["email_verified"])

    def test_suspension_deletes_token_and_restore_does_not_restore_it(self):
        result = self.auth.create_service_token(self.member["id"])
        session = self.auth.login(self.member["username"], PASSWORD)["session_token"]
        self.auth.admin_set_disabled(self.master["id"], self.member["id"], True, "Synthetic suspension")
        self.assertIsNone(self.auth.authenticate_service_token(result["token"]))
        self.assertIsNone(self.auth.authenticate(session))
        self.assertEqual(self.records(), [])
        self.auth.admin_set_disabled(self.master["id"], self.member["id"], False, "Synthetic restoration")
        self.assertIsNone(self.auth.authenticate_service_token(result["token"]))
        self.assertEqual(self.auth.service_token_status(self.member["id"]),
                         {"connected": False, "expires_at": None})

    def test_authentication_rechecks_member_eligibility_even_if_row_remains(self):
        result = self.auth.create_service_token(self.member["id"])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE members SET disabled=? WHERE id=?", (self.now, self.member["id"]))
        self.assertEqual(len(self.records()), 1)
        self.assertIsNone(self.auth.authenticate_service_token(result["token"]))
        self.assertEqual(self.auth.service_token_status(self.member["id"]),
                         {"connected": False, "expires_at": None})

    def test_member_revocation_is_idempotent_and_does_not_revoke_other_member(self):
        other = self.create_member("member_two", "two@example.test")
        first = self.auth.create_service_token(self.member["id"])
        second = self.auth.create_service_token(other["id"])
        self.assertEqual(self.auth.authenticate_service_token(second["token"])["id"], other["id"])
        self.auth.revoke_service_token(self.member["id"])
        self.auth.revoke_service_token(self.member["id"])
        self.assertIsNone(self.auth.authenticate_service_token(first["token"]))
        self.assertEqual(self.auth.authenticate_service_token(second["token"])["id"], other["id"])
        self.assertEqual(len(self.records()), 1)

    def test_service_tokens_never_substitute_for_browser_or_verification_tokens(self):
        result = self.auth.create_service_token(self.member["id"])
        session = self.auth.login(self.member["username"], PASSWORD)["session_token"]
        verification = self.messages[-1][1]
        self.assertIsNone(self.auth.authenticate(result["token"]))
        self.assertIsNone(self.auth.authenticate_service_token(session))
        self.assertIsNone(self.auth.authenticate_service_token(verification))
        self.assertIsNone(self.auth.authenticate_service_token(result["token"].removeprefix("cs_mcp_")))
        with self.assertRaisesRegex(AuthError, "^invalid_verification_token$"):
            self.auth.verify(result["token"], PASSWORD)
        self.auth.logout(result["token"])
        self.assertIsNotNone(self.auth.authenticate(session))
        self.auth.logout(session)
        self.assertIsNotNone(self.auth.authenticate_service_token(result["token"]))

    def test_invalid_shapes_fail_without_database_access_or_secret_errors(self):
        for token in (None, [], 1, "", "cs_mcp_", "cs_mcp_" + "a" * 44,
                      "cs_mcp_" + "가" * 43, "cs_mcp_" + "a" * 43 + "\n",
                      "Bearer cs_mcp_" + "a" * 43, "cs_mcp_" + "\ud800" * 43):
            with self.subTest(token=repr(token)), patch.object(self.auth, "_connect") as connect:
                self.assertIsNone(self.auth.authenticate_service_token(token))
                connect.assert_not_called()
        for user_id in (None, [], "", "../member", "F" * 32, "a" * 32 + "\n"):
            for operation in (self.auth.create_service_token, self.auth.service_token_status, self.auth.revoke_service_token):
                with self.subTest(user_id=repr(user_id), operation=operation.__name__), self.assertRaisesRegex(
                        AuthError, "^invalid_member_input$"):
                    operation(user_id)

    def test_concurrent_rotations_leave_exactly_one_active_token(self):
        barrier = threading.Barrier(3)

        def issue(_):
            barrier.wait(timeout=5)
            return self.auth.create_service_token(self.member["id"])["token"]

        with ThreadPoolExecutor(max_workers=3) as pool:
            tokens = list(pool.map(issue, range(3)))
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(sum(self.auth.authenticate_service_token(token) is not None for token in tokens), 1)

    def test_member_deletion_cascades_to_service_credentials(self):
        token = self.auth.create_service_token(self.member["id"])["token"]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM members WHERE id=?", (self.member["id"],))
        self.assertEqual(self.records(), [])
        self.assertIsNone(self.auth.authenticate_service_token(token))


if __name__ == "__main__":
    unittest.main()
