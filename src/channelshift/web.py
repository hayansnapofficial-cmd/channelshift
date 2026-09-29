"""Small loopback-only HTTP surface for the independent ChannelShift editor."""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .core import create_schema, export_java, export_sql, list_templates, validate_schema
from .store import MAX_BYTES, ProjectStore

WEB = Path(__file__).with_name("web")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def error_code(error):
    allowed = {"invalid_schema", "invalid_template", "unknown_template", "invalid_project", "invalid_database", "invalid_package",
               "invalid_java_package", "invalid_project_id", "project_not_found", "storage_index_limit", "invalid_label", "unsafe_storage",
               "unsupported_default", "unsupported_mysql_type", "unsupported_mysql_index",
               "java_json_mapping_unsupported", "java_name_collision", "invalid_format"}
    return str(error) if type(error) is ValueError and str(error) in allowed else "operation_failed"


def handler_factory(store=None, token=None):
    projects = store or ProjectStore()
    token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ChannelShift"
        sys_version = ""

        def log_message(self, *args):
            pass

        @property
        def origin(self):
            return "http://127.0.0.1:" + str(self.server.server_port)

        def send(self, status, data, mime="application/json; charset=utf-8"):
            payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                self.wfile.write(payload)

        def guard(self, api=False, write=False):
            if self.headers.get("Host") != self.origin.removeprefix("http://"):
                self.send(403, {"ok": False, "error": "invalid_host"})
                return False
            origin = self.headers.get("Origin")
            if (write and origin != self.origin) or (origin is not None and origin != self.origin):
                self.send(403, {"ok": False, "error": "invalid_origin"})
                return False
            supplied = self.headers.get("X-ChannelShift-Token", "")
            if api and (not supplied.isascii() or not hmac.compare_digest(supplied, token)):
                self.send(403, {"ok": False, "error": "invalid_token"})
                return False
            return True

        def do_GET(self):
            if not self.guard(api=self.path.startswith("/api/")):
                return
            if self.path in ASSETS:
                name, mime = ASSETS[self.path]
                payload = (WEB / name).read_bytes()
                if name == "index.html":
                    payload = payload.replace(b"__CHANNELSHIFT_TOKEN__", token.encode("ascii"))
                self.send(200, payload, mime)
                return
            if self.path == "/health":
                self.send(200, {"ok": True, "service": "channelshift-independent", "version": __version__})
                return
            try:
                if self.path == "/api/templates":
                    value = {"ok": True, "items": list_templates()}
                elif self.path == "/api/projects":
                    value = projects.list()
                elif self.path.startswith("/api/projects/"):
                    value = projects.get(self.path.removeprefix("/api/projects/"))
                else:
                    self.send(404, {"ok": False, "error": "not_found"})
                    return
                self.send(200, value)
            except Exception as error:
                self.send(400, {"ok": False, "error": error_code(error)})

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            if not self.guard(api=True, write=True):
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json" or self.headers.get("Transfer-Encoding"):
                self.send(415, {"ok": False, "error": "json_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= MAX_BYTES:
                    self.send(413, {"ok": False, "error": "payload_too_large"})
                    return
                self.connection.settimeout(10)
                data = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_json")))
                if not isinstance(data, dict):
                    raise ValueError("invalid_schema")
                if self.path == "/api/generate" and set(data) <= {"templateId", "project", "database"}:
                    value = {"ok": True, "schema": create_schema(data["templateId"], data["project"], data.get("database", "postgresql"))}
                elif self.path == "/api/validate" and set(data) == {"schema"}:
                    value = {"ok": True, **validate_schema(data["schema"])}
                elif self.path == "/api/save" and set(data) <= {"schema", "topic"}:
                    value = projects.save(data["schema"], data.get("topic", "general"))
                elif self.path == "/api/export" and set(data) <= {"schema", "format", "package"}:
                    if data["format"] == "sql":
                        files = [{"path": "schema.sql", "content": export_sql(data["schema"])}]
                    elif data["format"] == "java":
                        files = export_java(data["schema"], data.get("package", "com.example.app"))
                    else:
                        raise ValueError("invalid_format")
                    value = {"ok": True, "files": files}
                else:
                    self.send(404, {"ok": False, "error": "invalid_request"})
                    return
                self.send(200, value)
            except Exception as error:
                self.send(400, {"ok": False, "error": error_code(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5187)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler_factory()) as server:
            print(f"ChannelShift {__version__}: http://127.0.0.1:{args.port}/", file=sys.stderr)
            server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
