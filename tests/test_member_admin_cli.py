"""Master CLI tests use synthetic credentials and temporary or mocked storage."""
from contextlib import redirect_stderr, redirect_stdout
import getpass
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import warnings
from unittest.mock import patch

from channelshift.member_admin import main
from channelshift.member_auth import AuthError, MemberAuth


ARGS = ["create-master", "--username", "test_master", "--email", "master@example.com"]
PASSWORD = "Test!123"


class TerminalOutput(io.StringIO):
    def isatty(self):
        return True


class MemberAdminCLITests(unittest.TestCase):
    def test_matching_hidden_prompts_create_only_a_temporary_master(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "private"
            output, errors = io.StringIO(), TerminalOutput()
            with patch("channelshift.member_admin.directory", return_value=root), \
                    patch("channelshift.member_admin.sys.stdin.isatty", return_value=True), \
                    patch("channelshift.member_admin.getpass.getpass", side_effect=[PASSWORD, PASSWORD]) as prompt, \
                    patch("channelshift.member_auth._derive", side_effect=lambda password, salt: hashlib.sha256(salt + password).digest()), \
                    redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(ARGS), 0)
                self.assertEqual(prompt.call_count, 2)
                auth = MemberAuth(root / "members" / "accounts.sqlite3")
                user = auth.login("test_master", PASSWORD)["user"]
            self.assertEqual(user["role"], "master")
            self.assertFalse(user["email_verified"])
            self.assertEqual(output.getvalue(), "master_created\n")
            self.assertEqual(errors.getvalue(), "")
            self.assertNotIn(PASSWORD.encode(), (root / "members" / "accounts.sqlite3").read_bytes())

    def test_noninteractive_mismatch_and_echo_fallback_never_open_storage(self):
        def fallback(prompt):
            warnings.warn("synthetic private warning", getpass.GetPassWarning)
            self.fail("Echo fallback must stop before reading a password")
        cases = ((False, [PASSWORD, PASSWORD], "interactive_terminal_required", 0),
                 (True, [PASSWORD, "different"], "password_confirmation_mismatch", 2),
                 (True, fallback, "interactive_terminal_required", 1))
        for is_tty, prompts, error, call_count in cases:
            with self.subTest(error=error, tty=is_tty):
                output, errors = io.StringIO(), TerminalOutput()
                with patch("channelshift.member_admin.directory") as location, \
                        patch("channelshift.member_admin.MemberAuth") as auth, \
                        patch("channelshift.member_admin.sys.stdin.isatty", return_value=is_tty), \
                        patch("channelshift.member_admin.getpass.getpass", side_effect=prompts) as prompt, \
                        redirect_stdout(output), redirect_stderr(errors):
                    self.assertEqual(main(ARGS), 1)
                self.assertEqual(prompt.call_count, call_count)
                location.assert_not_called()
                auth.assert_not_called()
                self.assertEqual(errors.getvalue(), error + "\n")
                self.assertEqual(output.getvalue(), "")

    def test_errors_are_redacted_and_password_arguments_are_rejected(self):
        for exception, expected in ((AuthError("master_already_exists"), "master_already_exists"),
                                    (AuthError("synthetic private error"), "master_creation_failed"),
                                    (RuntimeError("synthetic private error"), "master_creation_failed")):
            with self.subTest(exception=exception):
                output, errors = io.StringIO(), TerminalOutput()
                with patch("channelshift.member_admin.directory", return_value=Path("synthetic-storage")), \
                        patch("channelshift.member_admin.MemberAuth") as auth, \
                        patch("channelshift.member_admin.sys.stdin.isatty", return_value=True), \
                        patch("channelshift.member_admin.getpass.getpass", side_effect=[PASSWORD, PASSWORD]), \
                        redirect_stdout(output), redirect_stderr(errors):
                    auth.return_value.bootstrap_master.side_effect = exception
                    self.assertEqual(main(ARGS), 1)
                self.assertEqual(errors.getvalue(), expected + "\n")
                self.assertEqual(output.getvalue(), "")
        output, errors = io.StringIO(), TerminalOutput()
        with patch("channelshift.member_admin.MemberAuth") as auth, \
                patch("channelshift.member_admin.getpass.getpass") as prompt, \
                redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as stopped:
                main(ARGS + ["--password", PASSWORD])
        self.assertEqual(stopped.exception.code, 2)
        auth.assert_not_called()
        prompt.assert_not_called()
        self.assertEqual(errors.getvalue(), "invalid_arguments\n")
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
