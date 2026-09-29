"""Create the local member-mode master through an interactive terminal."""
from __future__ import annotations

import argparse
import getpass
import sys
import warnings

from .member_auth import AuthError, MemberAuth
from .store import directory


_SAFE_ERRORS = {
    "invalid_username", "invalid_email", "invalid_password", "master_already_exists",
    "member_already_exists", "registration_unavailable", "auth_busy", "auth_unavailable",
    "unsafe_auth_storage", "auth_storage_unavailable",
}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error can repeat a mistakenly supplied password.
        print("invalid_arguments", file=sys.stderr)
        raise SystemExit(2)


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-master", help="Create one local master with a hidden password prompt")
    create.add_argument("--username", required=True)
    create.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    try:
        if not (sys.stdin and sys.stdin.isatty() and sys.stderr and sys.stderr.isatty()):
            print("interactive_terminal_required", file=sys.stderr)
            return 1
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Master password: ")
            confirmation = getpass.getpass("Confirm master password: ")
        if password != confirmation:
            print("password_confirmation_mismatch", file=sys.stderr)
            return 1
        auth = MemberAuth(directory() / "members" / "accounts.sqlite3")
        auth.bootstrap_master(args.username, args.email, password)
    except getpass.GetPassWarning:
        print("interactive_terminal_required", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("master_creation_cancelled", file=sys.stderr)
        return 1
    except AuthError as error:
        code = str(error)
        print(code if code in _SAFE_ERRORS else "master_creation_failed", file=sys.stderr)
        return 1
    except Exception:
        print("master_creation_failed", file=sys.stderr)
        return 1
    print("master_created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
