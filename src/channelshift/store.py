"""Immutable native schema versions in an operator-owned local directory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .core import validate_schema

MAX_BYTES = 2 * 1024 * 1024
MAX_FILES = 10000
MAX_STORAGE_BYTES = 256 * 1024 * 1024
ID = re.compile(r"[0-9a-f]{64}\Z")


def canonical(schema):
    try:
        data = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ValueError("invalid_schema") from None
    if len(data) > MAX_BYTES or not validate_schema(schema)["valid"]:
        raise ValueError("invalid_schema")
    return data


def directory():
    return Path(os.environ.get("CHANNELSHIFT_HOME", str(Path.home() / ".channelshift"))).expanduser().absolute()


def label_slug(label, limit=120):
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= limit:
        raise ValueError("invalid_label")
    normal = unicodedata.normalize("NFKC", label.strip())
    readable = re.sub(r"[^\w-]", "-", normal, flags=re.UNICODE).strip("-_")[:40] or "item"
    return "p-" + readable + "-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]


class ProjectStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else directory()

    def _root(self, create=False):
        if self.root.is_symlink():
            raise ValueError("unsafe_storage")
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("unsafe_storage")
        return self.root / "projects"

    def _files(self):
        def entries(folder):
            try:
                yield from folder.iterdir()
            except FileNotFoundError:
                pass  # A failed save may remove its newly-created empty folders.

        base = self._root()
        if base.is_symlink():
            raise ValueError("unsafe_storage")
        if not base.exists():
            return
        count = 0
        for topic in entries(base):
            if topic.is_symlink() or not topic.is_dir():
                continue
            for project in entries(topic):
                if project.is_symlink() or not project.is_dir():
                    continue
                for path in entries(project):
                    # Publication briefly has both a pending and final name.
                    # Reserved pending names consume write quota, not history
                    # entries, even if removed after this directory snapshot.
                    if path.name.startswith(".pending-"):
                        continue
                    count += 1
                    if count > MAX_FILES:
                        raise ValueError("storage_index_limit")
                    if ID.fullmatch(path.stem) and path.suffix == ".json" and not path.is_symlink() and path.is_file():
                        yield path

    def _read(self, path):
        if path.is_symlink() or path.stat().st_size > MAX_BYTES + 4096:
            raise ValueError("invalid_stored_project")
        data = path.read_bytes()
        if len(data) > MAX_BYTES + 4096:
            raise ValueError("invalid_stored_project")
        value = json.loads(data)
        digest = hashlib.sha256(canonical(value["schema"])).hexdigest()
        if value.get("id") != digest or path.stem != digest:
            raise ValueError("invalid_stored_project")
        if not isinstance(value.get("updatedAt"), str) or not isinstance(value.get("topic"), str):
            raise ValueError("invalid_stored_project")
        return value

    @contextmanager
    def _save_lock(self):
        self._root(create=True)
        lock = self.root / ".schema-save.sqlite3"
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(str(lock) + suffix)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("unsafe_storage")
        # SQLite's OS locks serialize independent processes on Windows and Unix.
        # The files, not database counters, remain authoritative after a crash.
        with closing(sqlite3.connect(lock, timeout=30, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield
            finally:
                db.rollback()

    def _check_quota(self, base, incoming_bytes):
        count, size = 0, incoming_bytes
        if MAX_FILES < 1 or size > MAX_STORAGE_BYTES:
            raise ValueError("storage_quota_exceeded")
        folders = [base] if base.exists() else []
        while folders:
            for path in folders.pop().iterdir():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("unsafe_storage")
                if stat.S_ISDIR(info.st_mode):
                    folders.append(path)
                elif stat.S_ISREG(info.st_mode):
                    # Count each physical path, including corrupt/unknown files
                    # and abandoned pending files, even for repeated digests.
                    count += 1
                    size += info.st_size
                    if count >= MAX_FILES or size > MAX_STORAGE_BYTES:
                        raise ValueError("storage_quota_exceeded")
                else:
                    raise ValueError("unsafe_storage")

    def save(self, schema, topic="general"):
        payload = canonical(schema)
        digest = hashlib.sha256(payload).hexdigest()
        topic_name, project_name = label_slug(topic), label_slug(schema["name"], 200)
        value = {"id": digest, "topic": topic, "updatedAt": datetime.now(timezone.utc).isoformat(), "schema": schema}
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        with self._save_lock():
            base = self._root()
            topic_folder = base / topic_name
            folder = topic_folder / project_name
            directories = (base, topic_folder, folder)
            for path in directories:
                if path.is_symlink() or (path.exists() and not path.is_dir()):
                    raise ValueError("unsafe_storage")
            target = folder / (digest + ".json")
            if target.is_symlink():
                raise ValueError("unsafe_storage")
            if target.exists():
                self._read(target)
                return {"ok": True, "id": digest, "stored": False}
            self._check_quota(base, len(encoded))
            created, pending = [], None
            try:
                for path in directories:
                    if not path.exists():
                        path.mkdir(mode=0o700)
                        created.append(path)
                descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=folder)
                pending = Path(temporary)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Keep immutable publication even if a non-cooperating writer
                # creates the same target while this process holds the lock.
                try:
                    os.link(pending, target)
                except FileExistsError:
                    self._read(target)
                    return {"ok": True, "id": digest, "stored": False}
            finally:
                try:
                    if pending is not None:
                        pending.unlink(missing_ok=True)
                finally:
                    for path in reversed(created):
                        try:
                            path.rmdir()
                        except OSError:
                            pass  # Published versions keep their directories.
        return {"ok": True, "id": digest, "stored": True}

    def list(self):
        values = {}
        skipped = 0
        for path in self._files():
            try:
                value = self._read(path)
                schema = value["schema"]
                values[value["id"]] = {"id": value["id"], "name": schema["name"], "database": schema["database"],
                                       "topic": value["topic"], "updatedAt": value["updatedAt"], "tableCount": len(schema["entities"])}
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                skipped += 1
        return {"ok": True, "items": sorted(values.values(), key=lambda item: item["updatedAt"], reverse=True), "skipped": skipped}

    def get(self, project_id):
        if not isinstance(project_id, str) or not ID.fullmatch(project_id):
            raise ValueError("invalid_project_id")
        for path in self._files():
            if path.stem == project_id:
                try:
                    value = self._read(path)
                    return {"ok": True, "id": project_id, "schema": value["schema"]}
                except (OSError, ValueError, KeyError, TypeError, RecursionError):
                    continue
        raise ValueError("project_not_found")
