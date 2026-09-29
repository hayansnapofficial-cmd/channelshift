"""Small loopback-only HTTP surface for the independent ChannelShift editor."""
from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .core import create_schema, export_java, export_sql, list_templates, validate_schema
from .store import MAX_BYTES, ProjectStore

WEB = Path(__file__).with_name("web")
ASSETS = {"/": ("studio.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/studio": ("studio.html", "text/html; charset=utf-8"),
          "/editor": ("index.html", "text/html; charset=utf-8"),
          "/studio.js": ("studio.js", "text/javascript; charset=utf-8"),
          "/studio-dom.js": ("studio-dom.js", "text/javascript; charset=utf-8"),
          "/studio-policy.js": ("studio-policy.js", "text/javascript; charset=utf-8"),
          "/studio-account.js": ("studio-account.js", "text/javascript; charset=utf-8"),
          "/studio.css": ("studio.css", "text/css; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/delivery": ("studio.html", "text/html; charset=utf-8"),
          "/delivery.js": ("delivery.js", "text/javascript; charset=utf-8"),
          "/delivery.css": ("delivery.css", "text/css; charset=utf-8"),
          "/workbench": ("workbench.html", "text/html; charset=utf-8"),
          "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
          "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
          "/impact.js": ("impact.js", "text/javascript; charset=utf-8"),
          "/impact.css": ("impact.css", "text/css; charset=utf-8")}


def error_code(error):
    from .pipeline_workspace import ERRORS
    allowed = {"invalid_schema", "invalid_template", "unknown_template", "invalid_project", "invalid_database", "invalid_package",
               "invalid_java_package", "invalid_project_id", "project_not_found", "storage_index_limit", "invalid_label", "unsafe_storage",
               "unsupported_default", "unsupported_mysql_type", "unsupported_mysql_index",
               "java_json_mapping_unsupported", "java_name_collision", "invalid_format",
               "invalid_delivery_input", "invalid_delivery_project", "delivery_project_not_found",
               "delivery_storage_limit", "delivery_busy", "delivery_recovery_required", "delivery_candidate_required",
               "delivery_revision_conflict", "delivery_input_limit", "delivery_answers_required",
               "invalid_impact_graph", "invalid_impact_field", "impact_graph_too_complex",
               "service_not_configured", "invalid_workbench_settings",
               "delivery_review_required", "delivery_erd_required", "delivery_erd_stale",
               "delivery_client_requirements_required", "invalid_erd_input", "invalid_erd_schema", "invalid_pipeline_preview"}
    return str(error) if type(error) is ValueError and str(error) in allowed | ERRORS else "operation_failed"


def normalize_public_origin(value):
    """Validate operator configuration, never forwarded or client headers."""
    if (type(value) is not str or not value.isascii() or not value.startswith('https://')
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or any(char in value for char in ('\\', '?', '#'))):
        raise ValueError('invalid_public_origin')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (not host or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or len(host) > 253
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                       for label in host.split('.'))
                or (port is not None and not 1 <= port <= 65535)
                or parsed.netloc.lower() != host + (':' + str(port) if port is not None else '')):
            raise ValueError()
        return 'https://' + host + (':' + str(port) if port not in (None, 443) else '')
    except (TypeError, ValueError):
        raise ValueError('invalid_public_origin') from None


def handler_factory(store=None, token=None, delivery=None, pipeline=None, *, public_origin=None):
    configured_origin = normalize_public_origin(public_origin) if public_origin is not None else None
    projects = store or ProjectStore()
    token = token or secrets.token_urlsafe(32)
    delivery_lock = threading.Lock()

    def intake_workspace():
        nonlocal delivery
        with delivery_lock:
            if delivery is None:
                from .delivery_workspace import DeliveryWorkspace
                delivery = DeliveryWorkspace()
        return delivery

    def studio_workspace():
        nonlocal pipeline
        if pipeline is None:
            from .pipeline_workspace import PipelineWorkspace
            intake = intake_workspace()
            with delivery_lock:
                if pipeline is None:
                    pipeline = PipelineWorkspace(intake)
        return pipeline

    class Handler(BaseHTTPRequestHandler):
        server_version = "ChannelShift"
        sys_version = ""

        def log_message(self, *args):
            pass

        @property
        def origin(self):
            return configured_origin or "http://127.0.0.1:" + str(self.server.server_port)

        def send(self, status, data, mime="application/json; charset=utf-8"):
            payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if configured_origin:
                self.send_header('Strict-Transport-Security', 'max-age=31536000')
            if self.path.startswith('/studio-preview/'):
                self.send_header('Content-Security-Policy', "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; frame-ancestors 'self'; base-uri 'none'")
            else:
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command != "HEAD":
                self.wfile.write(payload)

        def guard(self, api=False, write=False):
            # A local TLS proxy must preserve Host and Origin. Forwarded headers
            # are never used as authority, including on loopback connections.
            if (len(self.headers.get_all('Host', [])) != 1
                    or self.headers.get("Host") != urlsplit(self.origin).netloc):
                self.send(403, {"ok": False, "error": "invalid_host"})
                return False
            origin = self.headers.get("Origin")
            if (len(self.headers.get_all('Origin', [])) > 1
                    or (write and origin != self.origin) or (origin is not None and origin != self.origin)):
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
            asset_path = self.path.split('?', 1)[0]
            if asset_path in ASSETS:
                name, mime = ASSETS[asset_path]
                if asset_path == '/' and self.path.startswith('/?delivery='):
                    name = 'index.html'
                payload = (WEB / name).read_bytes()
                if name.endswith(".html"):
                    payload = payload.replace(b"__CHANNELSHIFT_TOKEN__", token.encode("ascii"))
                self.send(200, payload, mime)
                return
            if self.path == "/health":
                self.send(200, {"ok": True, "service": "channelshift-independent", "version": __version__})
                return
            try:
                if self.path.startswith('/studio-preview/'):
                    from .pipeline_preview import render_preview
                    parts = self.path.split('/')
                    if len(parts) != 4 or parts[3] not in {'wireframe', 'frontend'}:
                        raise ValueError('invalid_pipeline_input')
                    view = studio_workspace().get(parts[2])
                    stage = next(s for s in view['pipeline']['stages'] if s['id'] == parts[3])
                    if stage['state'] not in {'generated', 'approved'} or not stage['artifact']:
                        raise ValueError('pipeline_artifact_required')
                    self.send(200, render_preview(stage['artifact']['files'], stage=parts[3]), 'text/html; charset=utf-8')
                    return
                elif self.path == '/api/studio/catalog':
                    from .site_obligations import catalog
                    value = {'ok': True, 'obligations': catalog()}
                elif self.path == '/api/studio/projects':
                    value = {'ok': True, 'items': intake_workspace().list()}
                elif self.path.startswith('/api/studio/projects/'):
                    value = studio_workspace().get(self.path.removeprefix('/api/studio/projects/'))
                elif self.path == "/api/delivery/status":
                    from .codex_intake import status
                    from .jev_review import _credential, JevError
                    from .delivery_profile import standard_site_profile
                    try:
                        configured = bool(_credential())
                    except JevError:
                        configured = False
                    value = {"ok": True, "member_mode": False, "codex": status(), "jev_configured": configured,
                             "stages": [{"id": stage['id'], "title": stage['title']} for stage in standard_site_profile()['stages']]}
                elif self.path == "/api/workbench/profile":
                    from .workbench import profile
                    value = {"ok": True, **profile()}
                elif self.path == "/api/delivery/projects":
                    value = {"ok": True, "items": intake_workspace().list()}
                elif self.path.startswith("/api/delivery/projects/"):
                    value = {"ok": True, "project": intake_workspace().get(self.path.removeprefix("/api/delivery/projects/"))}
                elif self.path == "/api/templates":
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
                if self.path == '/api/studio/projects' and set(data) == {'name', 'client_request', 'site_type'}:
                    value = studio_workspace().create(**data)
                elif self.path == '/api/studio/action' and set(data) == {'project_id', 'expected_revision', 'action', 'payload'}:
                    value = studio_workspace().action(**data)
                elif self.path == '/api/studio/bundle' and set(data) == {'project_id', 'expected_revision'}:
                    self.send(200, studio_workspace().download(**data), 'application/zip')
                    return
                elif self.path == "/api/impact" and set(data) == {"schema", "table_id", "field_id", "graph"}:
                    from .impact import analyze_impact
                    value = {"ok": True, "impact": analyze_impact(data['schema'], data['table_id'], data['field_id'], data['graph'])}
                elif self.path == "/api/delivery/projects" and set(data) == {"name", "client_request"}:
                    value = {"ok": True, "project": intake_workspace().create(data['name'], data['client_request'])}
                elif self.path in {"/api/delivery/extract", "/api/delivery/jev"} and set(data) == {"project_id"}:
                    operation = "extract" if self.path.endswith("/extract") else "jev"
                    value = {"ok": True, "project": intake_workspace().start(data['project_id'], operation)}
                elif self.path == "/api/delivery/intervention" and set(data) == {"project_id", "stage_id", "reason", "note", "decision", "outcome", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().intervene(**data)}
                elif self.path == "/api/delivery/answer" and set(data) in (
                        {"project_id", "candidate_revision", "question_id", "question_digest", "answer", "expected_revision"},
                        {"project_id", "candidate_revision", "question_id", "question_digest", "answer", "expected_revision", "expected_context_revision"}):
                    value = {"ok": True, "project": intake_workspace().answer(**data)}
                elif self.path == "/api/delivery/consent" and set(data) == {"project_id", "mode", "operator_label", "reason", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().consent(**data)}
                elif self.path == "/api/delivery/next" and set(data) == {"project_id", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().advance_to_review(**data)}
                elif self.path == "/api/delivery/back" and set(data) == {"project_id", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().return_to_intake(**data)}
                elif self.path == "/api/delivery/references" and set(data) == {"project_id", "url", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().collect_reference(**data)}
                elif self.path == "/api/delivery/erd" and set(data) == {"project_id", "database", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().generate_erd(**data)}
                elif self.path == "/api/delivery/erd/save" and set(data) == {"project_id", "schema", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().save_erd(**data)}
                elif self.path == "/api/workbench/settings" and set(data) == {"project_id", "section", "values", "expected_revision"}:
                    value = {"ok": True, "project": intake_workspace().save_settings(**data)}
                elif self.path == "/api/generate" and set(data) <= {"templateId", "project", "database"}:
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
                code = error_code(error)
                self.send(409 if code in {'delivery_revision_conflict', 'pipeline_revision_conflict', 'pipeline_busy'} else 400, {"ok": False, "error": code})

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
