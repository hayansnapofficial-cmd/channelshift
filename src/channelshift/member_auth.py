"""Isolated member credentials for the explicit localhost member preview.

No existing workspace is assigned to a member. Tokens are never returned by
registration or stored in clear text. Transport, cookies and CSRF belong to the
member HTTP boundary; the verification endpoint must supply a new password.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time
import unicodedata


class AuthError(ValueError):
    """A safe error code, without credentials, addresses or provider responses."""


VERIFICATION_TTL = 30 * 60
PENDING_TTL = 24 * 60 * 60
SESSION_TTL = 8 * 60 * 60
MAX_MEMBERS = 1000
MAX_PENDING = 100
MAX_SESSIONS_PER_USER = 5
MAX_RATE_BUCKETS = 512
MAX_AUDIT_RESULTS = 100
RATE_WINDOW = 10 * 60
_HASH_SLOTS = threading.BoundedSemaphore(1)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
_USERNAME = re.compile(r"[a-z0-9_]{4,32}\Z", re.ASCII)
_EMAIL_LOCAL = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+\Z", re.ASCII)
_EMAIL_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)
_GENERIC = {"message": "입력한 정보로 진행할 수 있으면 인증 메일을 보냈습니다. 메일함을 확인해 주세요."}


def normalize_username(value):
    if type(value) is not str or not value.isascii():
        raise AuthError("invalid_username")
    value = value.strip().lower()
    if not _USERNAME.fullmatch(value):
        raise AuthError("invalid_username")
    return value


def normalize_email(value):
    if type(value) is not str or len(value) > 254:
        raise AuthError("invalid_email")
    value = value.strip().lower()
    if value.count("@") != 1:
        raise AuthError("invalid_email")
    local, domain = value.split("@")
    labels = domain.split(".")
    if (not 1 <= len(local) <= 64 or not _EMAIL_LOCAL.fullmatch(local)
            or local.startswith(".") or local.endswith(".") or ".." in local
            or len(labels) < 2 or not all(_EMAIL_LABEL.fullmatch(label) for label in labels)):
        raise AuthError("invalid_email")
    return value


def _password(value, minimum=15):
    if type(value) is not str:
        raise AuthError("invalid_password")
    value = unicodedata.normalize("NFC", value)
    if not minimum <= len(value) <= 128 or "\x00" in value:
        raise AuthError("invalid_password")
    try:
        return value.encode("utf-8")
    except UnicodeError:
        raise AuthError("invalid_password") from None


def _derive(password, salt):
    if not _HASH_SLOTS.acquire(blocking=False):
        raise AuthError("auth_busy")
    try:
        return hashlib.scrypt(password, salt=salt, n=2**17, r=8, p=1,
                              maxmem=256 * 1024 * 1024, dklen=32)
    except (ValueError, MemoryError):
        raise AuthError("auth_unavailable") from None
    finally:
        _HASH_SLOTS.release()


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _valid_token(token):
    return type(token) is str and bool(_TOKEN.fullmatch(token))


def _reject_links(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        # Also reject Windows junctions and other reparse points.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AuthError("unsafe_auth_storage")


def _private_path(path):
    path = Path(os.path.abspath(os.fspath(path)))
    _reject_links(path)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _reject_links(path)
        parent_info = path.parent.stat()
        if not stat.S_ISDIR(parent_info.st_mode):
            raise AuthError("unsafe_auth_storage")
        if os.name != "nt" and (parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o077):
            raise AuthError("unsafe_auth_storage")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        _reject_links(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AuthError("unsafe_auth_storage")
        if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise AuthError("unsafe_auth_storage")
    except OSError:
        raise AuthError("unsafe_auth_storage") from None
    return path


class MemberAuth:
    def __init__(self, path, mailer=None, clock=time.time):
        self.path = _private_path(path)
        self.mailer = mailer
        self.clock = clock
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL, created REAL NOT NULL,
                    verified REAL, pending_expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
                    expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    created REAL NOT NULL, expires REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, created);
                CREATE TABLE IF NOT EXISTS rate_limits (
                    bucket TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL
                );
            """)
            # Upgrade pre-role databases without changing credentials, verification,
            # or ownership. Serialize migrations across independently started hosts.
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(members)")}
            for name, declaration in (
                ("role", "TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member', 'master'))"),
                ("disabled", "REAL"),
                ("bootstrapped", "REAL"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE members ADD COLUMN {name} {declaration}")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS members_one_master ON members(role) WHERE role='master'")
            db.execute("CREATE TABLE IF NOT EXISTS member_audit ("
                       "id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, "
                       "actor_id TEXT NOT NULL, target_id TEXT NOT NULL, "
                       "action TEXT NOT NULL, reason TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        _private_path(self.path)
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        except sqlite3.Error:
            raise AuthError("auth_storage_unavailable") from None
        finally:
            if db is not None:
                db.close()

    def _cleanup(self, db, now):
        db.execute("DELETE FROM members WHERE verified IS NULL AND bootstrapped IS NULL "
                   "AND disabled IS NULL AND pending_expires <= ?", (now,))
        db.execute("DELETE FROM verification WHERE expires <= ?", (now,))
        db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        db.execute("DELETE FROM rate_limits WHERE started <= ?", (now - RATE_WINDOW,))

    def _rate(self, action, identity, account_limit, global_limit):
        """Persist attempts before password work, including failed attempts."""
        now = self.clock()
        keys = ((action + ":global", global_limit),
                (action + ":" + hashlib.sha256(identity.encode("utf-8")).hexdigest(), account_limit))
        limited = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, now)
            count = db.execute("SELECT count(*) FROM rate_limits").fetchone()[0]
            for key, limit in keys:
                row = db.execute("SELECT count FROM rate_limits WHERE bucket=?", (key,)).fetchone()
                if row:
                    db.execute("UPDATE rate_limits SET count=count+1 WHERE bucket=?", (key,))
                    limited |= row[0] >= limit
                elif count >= MAX_RATE_BUCKETS:
                    limited = True
                else:
                    db.execute("INSERT INTO rate_limits VALUES (?, ?, 1)", (key, now))
                    count += 1
        if limited:
            raise AuthError("rate_limited")

    def _send(self, email, token, username):
        try:
            sender = getattr(self.mailer, "send_verification", None)
            if sender:
                sender(email, token, username)
            else:
                self.mailer(email, token)
        except Exception:
            # A provider error can contain an address, credentials or message body.
            raise AuthError("email_delivery_failed") from None

    def _require_mail(self):
        if self.mailer is None:
            raise AuthError("email_not_configured")

    def bootstrap_master(self, username, email, password):
        """Provision one local master offline; never promote or reset an identity.

        This is deliberately not an HTTP registration flow. Local provisioning
        permits login but does not claim that the email address was verified.
        """
        username, email = normalize_username(username), normalize_email(email)
        encoded = _password(password, minimum=8)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM members WHERE role='master'").fetchone():
                raise AuthError("master_already_exists")
            if db.execute("SELECT 1 FROM members WHERE username=? OR email=?", (username, email)).fetchone():
                raise AuthError("member_already_exists")
            if db.execute("SELECT count(*) FROM members").fetchone()[0] >= MAX_MEMBERS:
                raise AuthError("registration_unavailable")
            salt = secrets.token_bytes(16)
            password_hash = _derive(encoded, salt)
            now, user_id = self.clock(), secrets.token_hex(16)
            db.execute("INSERT INTO members "
                       "(id, username, email, salt, password_hash, created, verified, pending_expires, role, bootstrapped) "
                       "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'master', ?)",
                       (user_id, username, email, salt, password_hash, now, now, now))
            self._audit(db, now, "local-bootstrap", user_id, "master_bootstrap", "local_provisioning")
            row = db.execute("SELECT * FROM members WHERE id=?", (user_id,)).fetchone()
        return self._user(row)

    def register(self, username, email, password):
        self._require_mail()
        username, email = normalize_username(username), normalize_email(email)
        encoded = _password(password)
        self._rate("register", email, 3, 10)
        salt = secrets.token_bytes(16)
        password_hash = _derive(encoded, salt)
        now, user_id, token = self.clock(), secrets.token_hex(16), secrets.token_urlsafe(32)
        token_hash = _digest(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, now)
            if db.execute("SELECT 1 FROM members WHERE username=? OR email=?", (username, email)).fetchone():
                return dict(_GENERIC)
            total, pending = db.execute("SELECT count(*), count(CASE WHEN verified IS NULL AND bootstrapped IS NULL "
                                        "THEN 1 END) FROM members").fetchone()
            if total >= MAX_MEMBERS or pending >= MAX_PENDING:
                raise AuthError("registration_unavailable")
            db.execute("INSERT INTO members (id, username, email, salt, password_hash, created, verified, pending_expires) "
                       "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                       (user_id, username, email, salt, password_hash, now, now + PENDING_TTL))
            db.execute("INSERT INTO verification VALUES (?, ?, ?)", (token_hash, user_id, now + VERIFICATION_TTL))
        try:
            self._send(email, token, username)
        except AuthError:
            with self._connect() as db:
                db.execute("DELETE FROM members WHERE id=? AND verified IS NULL AND EXISTS "
                           "(SELECT 1 FROM verification WHERE token_hash=? AND user_id=members.id)", (user_id, token_hash))
            raise
        return dict(_GENERIC)

    def resend(self, email):
        self._require_mail()
        email = normalize_email(email)
        self._rate("resend", email, 3, 10)
        token, now = secrets.token_urlsafe(32), self.clock()
        token_hash = _digest(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, now)
            row = db.execute("SELECT id, username FROM members WHERE email=? AND verified IS NULL "
                             "AND bootstrapped IS NULL AND role='member' AND disabled IS NULL", (email,)).fetchone()
            if row is None:
                return dict(_GENERIC)
            db.execute("DELETE FROM verification WHERE user_id=?", (row["id"],))
            db.execute("INSERT INTO verification VALUES (?, ?, ?)", (token_hash, row["id"], now + VERIFICATION_TTL))
        try:
            self._send(email, token, row["username"])
        except AuthError:
            with self._connect() as db:
                db.execute("DELETE FROM verification WHERE token_hash=?", (token_hash,))
            raise
        return dict(_GENERIC)

    def verify(self, token, password):
        """Email ownership activates a fresh password, not a pre-registrant's one."""
        if not _valid_token(token):
            raise AuthError("invalid_verification_token")
        encoded = _password(password)
        self._rate("verify", token, 5, 30)
        token_hash, now = _digest(token), self.clock()
        with self._connect() as db:
            row = db.execute("SELECT v.user_id FROM verification v JOIN members m ON m.id=v.user_id "
                             "WHERE v.token_hash=? AND v.expires>? AND m.verified IS NULL "
                             "AND m.bootstrapped IS NULL AND m.role='member' AND m.disabled IS NULL "
                             "AND m.pending_expires>?", (token_hash, now, now)).fetchone()
            if row is None:
                raise AuthError("invalid_verification_token")
        salt = secrets.token_bytes(16)
        password_hash = _derive(encoded, salt)
        now = self.clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT v.user_id FROM verification v JOIN members m ON m.id=v.user_id "
                             "WHERE v.token_hash=? AND v.expires>? AND m.verified IS NULL AND m.pending_expires>? "
                             "AND m.bootstrapped IS NULL AND m.role='member' AND m.disabled IS NULL",
                             (token_hash, now, now)).fetchone()
            if row is None:
                raise AuthError("invalid_verification_token")
            db.execute("UPDATE members SET verified=?, salt=?, password_hash=? WHERE id=?",
                       (now, salt, password_hash, row["user_id"]))
            db.execute("DELETE FROM verification WHERE user_id=?", (row["user_id"],))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
        return {"verified": True}

    def login(self, username, password):
        try:
            username, encoded = normalize_username(username), _password(password, minimum=1)
        except AuthError:
            raise AuthError("invalid_credentials") from None
        self._rate("login", username, 5, 30)
        with self._connect() as db:
            row = db.execute("SELECT * FROM members WHERE username=?", (username,)).fetchone()
        # Missing users perform the same configured scrypt work as existing users.
        actual = _derive(encoded, row["salt"] if row else bytes(16))
        expected = row["password_hash"] if row else bytes(32)
        matches = hmac.compare_digest(actual, expected)
        if not matches or row is None or not self._active(row):
            raise AuthError("invalid_credentials")
        token, now = secrets.token_urlsafe(32), self.clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, now)
            current = db.execute("SELECT * FROM members WHERE id=?", (row["id"],)).fetchone()
            if current is None or not self._active(current) or not hmac.compare_digest(current["password_hash"], expected):
                raise AuthError("invalid_credentials")
            db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN "
                       "(SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created DESC, rowid DESC LIMIT ?)",
                       (row["id"], row["id"], MAX_SESSIONS_PER_USER - 1))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (_digest(token), row["id"], now, now + SESSION_TTL))
        return {"session_token": token, "user": self._user(current)}

    @staticmethod
    def _active(row):
        return row["disabled"] is None and (row["verified"] is not None or
                (row["role"] == "master" and row["bootstrapped"] is not None))

    @staticmethod
    def _user(row):
        return {"id": row["id"], "username": row["username"], "email": row["email"],
                "role": row["role"], "email_verified": row["verified"] is not None}

    def authenticate(self, token):
        if not _valid_token(token):
            return None
        with self._connect() as db:
            row = db.execute("SELECT m.id, m.username, m.email, m.role, m.verified FROM sessions s JOIN members m ON m.id=s.user_id "
                             "WHERE s.token_hash=? AND s.expires>? AND m.disabled IS NULL "
                             "AND (m.verified IS NOT NULL OR (m.role='master' AND m.bootstrapped IS NOT NULL))",
                             (_digest(token), self.clock())).fetchone()
        return self._user(row) if row else None

    def logout(self, token):
        if _valid_token(token):
            with self._connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (_digest(token),))

    @staticmethod
    def _require_master(db, actor_id):
        if type(actor_id) is not str:
            raise AuthError("admin_forbidden")
        row = db.execute("SELECT * FROM members WHERE id=?", (actor_id,)).fetchone()
        if row is None or row["role"] != "master" or not MemberAuth._active(row):
            raise AuthError("admin_forbidden")

    @staticmethod
    def _member_metadata(row):
        return {**MemberAuth._user(row), "created": row["created"], "verified": row["verified"],
                "disabled": row["disabled"], "locally_bootstrapped": row["bootstrapped"] is not None}

    @staticmethod
    def _audit(db, now, actor_id, target_id, action, reason):
        db.execute("INSERT INTO member_audit (created, actor_id, target_id, action, reason) "
                   "VALUES (?, ?, ?, ?, ?)", (now, actor_id, target_id, action, reason))

    def admin_members(self, actor_id):
        with self._connect() as db:
            db.execute("BEGIN")
            self._require_master(db, actor_id)
            rows = db.execute("SELECT id, username, email, role, verified, created, disabled, bootstrapped "
                              "FROM members ORDER BY created DESC, id").fetchall()
        return [self._member_metadata(row) for row in rows]

    def admin_set_disabled(self, actor_id, user_id, disabled, reason):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_master(db, actor_id)
            if (type(user_id) is not str or not user_id or len(user_id) > 128 or type(disabled) is not bool
                    or type(reason) is not str or not 1 <= len(reason.strip()) <= 500 or "\x00" in reason):
                raise AuthError("invalid_admin_request")
            try:
                reason.encode("utf-8")
            except UnicodeError:
                raise AuthError("invalid_admin_request") from None
            row = db.execute("SELECT * FROM members WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise AuthError("member_not_found")
            if row["role"] == "master" or user_id == actor_id:
                raise AuthError("master_protected")
            now = self.clock()
            if (row["disabled"] is not None) != disabled:
                db.execute("UPDATE members SET disabled=? WHERE id=?", (now if disabled else None, user_id))
                self._audit(db, now, actor_id, user_id, "member_disabled" if disabled else "member_enabled", reason.strip())
            if disabled:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            row = db.execute("SELECT * FROM members WHERE id=?", (user_id,)).fetchone()
        return self._member_metadata(row)

    def admin_audit(self, actor_id):
        with self._connect() as db:
            db.execute("BEGIN")
            self._require_master(db, actor_id)
            rows = db.execute("SELECT id, created, actor_id, target_id, action, reason FROM member_audit "
                              "ORDER BY id DESC LIMIT ?", (MAX_AUDIT_RESULTS,)).fetchall()
        return [dict(row) for row in rows]
