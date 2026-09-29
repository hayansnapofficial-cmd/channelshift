"""Storage isolation and the loopback HTTP boundary using synthetic schemas."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer

from channelshift.core import create_schema
from channelshift.store import ProjectStore
from channelshift.web import handler_factory


class StoreAndHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="channelshift-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root)
        self.schema = create_schema("booking", "Test booking")
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(self.store, "test-token"))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_http)
        self.port = self.http.server_port
        self.origin = "http://127.0.0.1:" + str(self.port)

    def close_http(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        values = {"X-ChannelShift-Token": "test-token", "Origin": self.origin, "Content-Type": "application/json"}
        values.update(headers or {})
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=values)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def test_immutable_save_roundtrip_and_topic_paths(self):
        first = self.store.save(self.schema, "예약 / 개발")
        self.assertTrue(first["stored"])
        files = list(self.root.rglob("*.json"))
        before = files[0].read_bytes()
        self.assertFalse(self.store.save(self.schema, "예약 / 개발")["stored"])
        self.assertEqual(before, files[0].read_bytes())
        self.assertEqual(self.schema, self.store.get(first["id"])["schema"])
        self.assertEqual(1, len(self.store.list()["items"]))

    def test_corrupt_files_ignored_and_paths_refused(self):
        result = self.store.save(self.schema)
        next(self.root.rglob("*.json")).write_text('{"secret":"test marker"}', encoding="utf-8")
        self.assertEqual([], self.store.list()["items"])
        with self.assertRaises(ValueError):
            self.store.get(result["id"])
        with self.assertRaises(ValueError):
            self.store.get("../../file")

    def test_different_content_preserves_history(self):
        first = self.store.save(self.schema)
        self.schema["name"] = "Renamed project"
        second = self.store.save(self.schema)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(2, len(self.store.list()["items"]))

    def test_long_valid_project_name_is_saved(self):
        self.schema["name"] = "Project" + "x" * 193
        saved = self.store.save(self.schema)
        self.assertEqual(self.schema, self.store.get(saved["id"])["schema"])

    def test_http_generate_save_list_get_sql_java(self):
        status, _, data = self.request("/api/generate", "POST", {"templateId": "booking", "project": "HTTP project", "database": "sqlite"})
        self.assertEqual(status, 200)
        model = json.loads(data)["schema"]
        self.assertTrue(json.loads(self.request("/api/validate", "POST", {"schema": model})[2])["valid"])
        saved = json.loads(self.request("/api/save", "POST", {"schema": model, "topic": "booking"})[2])
        loaded = json.loads(self.request("/api/projects/" + saved["id"])[2])
        self.assertEqual(loaded["schema"], model)
        self.assertEqual(1, len(json.loads(self.request("/api/projects")[2])["items"]))
        for format_ in ("sql", "java"):
            exported = json.loads(self.request("/api/export", "POST", {"schema": model, "format": format_})[2])
            self.assertTrue(exported["ok"])
            self.assertTrue(exported["files"])

    def test_origin_host_token_required(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"X-ChannelShift-Token": ""}, {"X-ChannelShift-Token": "é"}):
            self.assertEqual(403, self.request("/api/projects", headers=headers)[0])
            self.assertEqual(403, self.request("/api/save", "POST", {"schema": self.schema}, headers=headers)[0])

    def test_bounded_body_unknown_path_and_no_arbitrary_reads(self):
        status, _, _ = self.request("/api/save", "POST", {}, {"Content-Length": "3000000"})
        self.assertEqual(413, status)
        self.assertEqual(404, self.request("/../LICENSE")[0])
        self.assertEqual(404, self.request("/api/export", "POST", {"schema": self.schema, "format": "sql", "path": "sensitive"})[0])

    def test_privacy_headers_and_sanitized_error(self):
        status, headers, raw = self.request("/api/generate", "POST", {"templateId": "../../sensitive", "project": "TEST_SECRET_MARKER"})
        self.assertEqual(400, status)
        self.assertNotIn(b"TEST_SECRET_MARKER", raw)
        self.assertNotIn(b"sensitive", raw)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)


if __name__ == "__main__":
    unittest.main()
