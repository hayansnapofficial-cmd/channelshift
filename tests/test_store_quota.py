"""Physical schema quotas, crash accounting and cross-process admission."""
import hashlib
import http.client
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from channelshift import store as storage
from channelshift.store import ProjectStore, canonical, label_slug
from channelshift.web import error_code, handler_factory


class FrozenDateTime:
    @staticmethod
    def now(_):
        return datetime(2026, 9, 30, 0, 0, 0, 123456, timezone.utc)


def schema(name="Quota project"):
    return {"format": "channelshift.schema/v1", "name": name,
            "database": "postgresql", "entities": [], "relations": []}


def envelope(model, topic="general"):
    digest = hashlib.sha256(canonical(model)).hexdigest()
    value = {"id": digest, "topic": topic, "updatedAt": FrozenDateTime.now(None).isoformat(), "schema": model}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def save_in_process(root, name, ready, start, results):
    """Spawn-safe worker: independent interpreter, store and SQLite connection."""
    original_link = os.link

    def slow_publish(*args, **kwargs):
        time.sleep(0.15)  # Expose check/publish races if serialization is removed.
        return original_link(*args, **kwargs)

    with patch.object(storage, "MAX_FILES", 1), patch.object(storage.os, "link", slow_publish):
        ready.put(name)
        if not start.wait(15):
            results.put("start_timeout")
            return
        try:
            results.put(ProjectStore(root).save(schema(name))["stored"])
        except ValueError as error:
            results.put(str(error))


class StoreQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="channelshift-quota-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root)
        self.addCleanup(patch.stopall)
        patch.object(storage, "datetime", FrozenDateTime).start()

    def files(self):
        return sorted(path for path in (self.root / "projects").rglob("*") if path.is_file())

    def tree(self):
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))

    def reject(self, model, topic="general"):
        with self.assertRaisesRegex(ValueError, "^storage_quota_exceeded$"):
            self.store.save(model, topic)

    def legacy_file(self, model, topic="general"):
        """Create a pre-quota store without touching its new lock file."""
        folder = self.root / "projects" / label_slug(topic) / label_slug(model["name"], 200)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (hashlib.sha256(canonical(model)).hexdigest() + ".json")
        path.write_bytes(envelope(model, topic))
        return path

    def test_count_boundary_rejection_does_not_create_directories(self):
        with patch.object(storage, "MAX_FILES", 2):
            self.assertTrue(self.store.save(schema("first"))["stored"])
            self.assertTrue(self.store.save(schema("second"))["stored"])
            before = self.tree()
            for number in range(3):
                self.reject(schema("rejected " + str(number)), "new topic " + str(number))
            self.assertEqual(self.tree(), before)
            self.assertEqual(len(self.files()), 2)
            self.assertEqual(len(self.store.list()["items"]), 2)

    def test_envelope_bytes_have_exact_inclusive_boundary(self):
        first, second = schema("first"), schema("second")
        size = len(envelope(first)) + len(envelope(second))
        with patch.object(storage, "MAX_STORAGE_BYTES", size):
            self.store.save(first)
            self.store.save(second)
            self.assertEqual(sum(path.stat().st_size for path in self.files()), size)
            before = self.tree()
            self.reject(schema("third"), "new topic")
            self.assertEqual(before, self.tree())
        self.assertEqual(len(self.store.list()["items"]), 2)

    def test_canonical_payload_fits_but_envelope_does_not(self):
        model = schema("한글 프로젝트")
        self.assertGreater(len(envelope(model)), len(canonical(model)))
        for size in (len(canonical(model)), len(envelope(model)) - 1):
            with self.subTest(size=size), patch.object(storage, "MAX_STORAGE_BYTES", size):
                self.reject(model)
                self.assertFalse((self.root / "projects").exists())

    def test_duplicate_is_immutable_even_when_already_over_quota(self):
        model = schema()
        saved = self.store.save(model)
        path = self.files()[0]
        before = path.read_bytes(), path.stat().st_mtime_ns
        with patch.object(storage, "MAX_FILES", 0), patch.object(storage, "MAX_STORAGE_BYTES", 0):
            self.assertEqual(self.store.save(model), {"ok": True, "id": saved["id"], "stored": False})
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual(self.store.get(saved["id"])["schema"], model)

    def test_cross_topic_identical_digest_consumes_count_and_bytes(self):
        model = schema()
        with patch.object(storage, "MAX_FILES", 2):
            first = self.store.save(model, "one")
            second = self.store.save(model, "two")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(self.files()), 2)
            self.assertEqual(len(self.store.list()["items"]), 1)
            self.assertFalse(self.store.save(model, "one")["stored"])
            self.reject(model, "three")
        with patch.object(storage, "MAX_STORAGE_BYTES", sum(path.stat().st_size for path in self.files())):
            self.reject(model, "new topic")

    def test_existing_store_needs_no_quota_metadata(self):
        first, second = schema("legacy"), schema("new version")
        self.legacy_file(first)
        self.assertFalse((self.root / ".schema-save.sqlite3").exists())
        with patch.object(storage, "MAX_FILES", 2):
            self.assertFalse(self.store.save(first)["stored"])
            self.assertTrue(self.store.save(second)["stored"])
            self.reject(schema("third"))
        self.assertEqual(len(self.store.list()["items"]), 2)

    def test_unknown_corrupt_nested_and_abandoned_pending_files_are_counted(self):
        base = self.root / "projects"
        (base / "topic" / "project" / "nested").mkdir(parents=True)
        paths = [base / "unexpected.bin", base / "topic" / "project" / ("0" * 64 + ".json"),
                 base / "topic" / "project" / "nested" / ".pending-abandoned"]
        for path in paths:
            path.write_bytes(b"invalid but occupies storage")
        with patch.object(storage, "MAX_FILES", len(paths)):
            self.reject(schema())
        with patch.object(storage, "MAX_STORAGE_BYTES", sum(path.stat().st_size for path in paths) + len(envelope(schema())) - 1):
            self.reject(schema())
        self.assertEqual({path.read_bytes() for path in paths}, {b"invalid but occupies storage"})

    def test_crash_after_link_counts_pending_and_published_paths(self):
        model = schema()
        target = self.legacy_file(model)
        pending = target.with_name(".pending-crashed")
        os.link(target, pending)
        with patch.object(storage, "MAX_FILES", 2):
            self.assertFalse(self.store.save(model)["stored"])
            self.reject(schema("another"))
        self.assertTrue(pending.exists())
        self.assertEqual(self.store.get(target.stem)["schema"], model)

    def test_write_failures_clean_pending_and_new_directories_and_release_lock(self):
        for operation in ("tempfile.mkstemp", "os.fsync", "os.link"):
            with self.subTest(operation=operation):
                root = self.root / operation
                store = ProjectStore(root)
                with patch("channelshift.store." + operation, side_effect=OSError("synthetic failure")):
                    with self.assertRaises(OSError):
                        store.save(schema())
                self.assertFalse((root / "projects").exists())
                self.assertTrue(store.save(schema())["stored"])
                self.assertEqual(list(root.rglob(".pending-*")), [])

    def test_failed_write_preserves_existing_directories_and_versions(self):
        first = self.store.save(schema("first"))
        before = self.tree()
        with patch.object(storage.os, "link", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.store.save(schema("second"), "new topic")
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.store.get(first["id"])["schema"], schema("first"))

    def test_stream_write_failure_closes_file_and_cleans_directories(self):
        original_fdopen = os.fdopen

        @contextmanager
        def failing_stream(descriptor, mode):
            with original_fdopen(descriptor, mode) as stream:
                with patch.object(stream, "write", side_effect=OSError("synthetic write failure")):
                    yield stream

        with patch.object(storage.os, "fdopen", failing_stream):
            with self.assertRaises(OSError):
                self.store.save(schema())
        self.assertFalse((self.root / "projects").exists())
        self.assertTrue(self.store.save(schema())["stored"])

    def test_symlinks_are_not_followed_for_accounting_or_locking(self):
        outside = self.root / "outside"
        outside.mkdir()
        for location in ("projects", ".schema-save.sqlite3"):
            root = self.root / ("case-" + location)
            root.mkdir()
            link = root / location
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest("symlinks unavailable: " + str(error))
            with self.assertRaisesRegex(ValueError, "^unsafe_storage$"):
                ProjectStore(root).save(schema())
        self.assertEqual(list(outside.iterdir()), [])

    def test_spawned_processes_compete_for_last_slot(self):
        context = multiprocessing.get_context("spawn")
        ready, results, start = context.Queue(), context.Queue(), context.Event()
        processes = [context.Process(target=save_in_process, args=(str(self.root), name, ready, start, results))
                     for name in ("process one", "process two")]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                ready.get(timeout=20)
            start.set()
            outcomes = [results.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual(outcomes, [True, "storage_quota_exceeded"])
            self.assertEqual(len(self.files()), 1)
            self.assertEqual(list(self.root.rglob(".pending-*")), [])
        finally:
            start.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=5)
                    process.close()
            ready.close()
            results.close()

    def test_full_store_remains_readable_between_publish_and_pending_cleanup(self):
        original_link = os.link
        observed = []

        def publish_and_read(source, target):
            original_link(source, target)
            observed.append(self.store.list())
            self.assertEqual(self.store.get(Path(target).stem)["schema"], schema("last slot"))

        with patch.object(storage, "MAX_FILES", 2):
            self.store.save(schema("first"))
            with patch.object(storage.os, "link", publish_and_read):
                self.assertTrue(self.store.save(schema("last slot"))["stored"])
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(observed[0]["items"]), 2)
        self.assertEqual(list(self.root.rglob(".pending-*")), [])

    def test_full_store_read_ignores_pending_removed_after_directory_snapshot(self):
        original_link, original_iterdir = os.link, Path.iterdir
        observed = []

        def snapshot_then_remove_pending(folder):
            entries = list(original_iterdir(folder))
            for path in entries:
                if path.name.startswith(".pending-"):
                    path.unlink()
            return iter(entries)

        def publish_and_read(source, target):
            original_link(source, target)
            with patch.object(Path, "iterdir", snapshot_then_remove_pending):
                observed.append(self.store.list())

        with patch.object(storage, "MAX_FILES", 1), patch.object(storage.os, "link", publish_and_read):
            self.assertTrue(self.store.save(schema())["stored"])
        self.assertEqual(len(observed[0]["items"]), 1)

    def test_read_tolerates_failed_save_removing_empty_directories(self):
        original_iterdir = Path.iterdir
        for level in ("projects", "topic", "project"):
            with self.subTest(level=level):
                root = self.root / level
                store = ProjectStore(root)
                saved = store.save(schema("existing")) if level != "projects" else None
                base = root / "projects"
                topic, project = base / "incoming-topic", base / "incoming-topic" / "incoming-project"
                victim = {"projects": base, "topic": topic, "project": project}[level]
                victim.mkdir(parents=True, exist_ok=True)

                def remove_before_read(folder):
                    if folder == victim:
                        folder.rmdir()
                    return original_iterdir(folder)

                with patch.object(Path, "iterdir", remove_before_read):
                    result = store.list()
                self.assertEqual(len(result["items"]), 1 if saved else 0)
                if saved:
                    self.assertEqual(store.get(saved["id"])["schema"], schema("existing"))

    def test_http_quota_response_is_sanitized_and_duplicate_still_succeeds(self):
        model = schema("PRIVATE_SCHEMA_MARKER")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(self.store, "quota-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(body):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("POST", "/api/save", json.dumps(body), {
                    "Origin": "http://127.0.0.1:" + str(server.server_port),
                    "X-ChannelShift-Token": "quota-token", "Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            with patch.object(storage, "MAX_FILES", 1):
                self.assertEqual(request({"schema": model})[0], 200)
                self.assertEqual(request({"schema": model, "topic": "PRIVATE_TOPIC_MARKER"}),
                                 (400, {"ok": False, "error": "storage_quota_exceeded"}))
                status, result = request({"schema": model})
                self.assertEqual(status, 200)
                self.assertFalse(result["stored"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_mcp_quota_mapping_and_unknown_errors_are_sanitized(self):
        from channelshift.mcp_server import call, save_project
        with patch.dict(os.environ, {"CHANNELSHIFT_HOME": str(self.root)}), patch.object(storage, "MAX_FILES", 1):
            self.assertFalse(save_project(schema()).is_error)
            result = save_project(schema("PRIVATE_SCHEMA_MARKER"), "PRIVATE_TOPIC_MARKER")
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content, {"ok": False, "error": "storage_quota_exceeded"})
            self.assertNotIn("PRIVATE", str(result.model_dump()))
            self.assertFalse(save_project(schema()).structured_content["stored"])
        self.assertEqual(error_code(ValueError("private disk path")), "operation_failed")
        self.assertEqual(error_code(OSError("storage_quota_exceeded")), "operation_failed")
        result = call(lambda: (_ for _ in ()).throw(OSError("private disk path")))
        self.assertEqual(result.structured_content, {"ok": False, "error": "operation_failed"})


if __name__ == "__main__":
    unittest.main()
