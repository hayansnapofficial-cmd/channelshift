"""Source-file adapter boundary tests; no login, model, shell or generated code runs."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift import codex_intake as intake
from channelshift import codex_pipeline as pipeline
from channelshift import site_obligations
from channelshift.member_codex import MemberCodex, MemberCodexError


USER_A, USER_B = "a" * 32, "b" * 32
SOURCE = "회사 소개와 문의 화면을 만들어 주세요. 문의 내용은 저장해 주세요."


def spec():
    return {"name": "예제 홈페이지", "database": "sqlite", "requirements": {
        "source": {"text": SOURCE}, "candidate_input": {"text": SOURCE},
        "candidate": {"requirements": [
            {"id": "REQ-001", "text": "회사 소개와 문의 화면을 제공한다.",
             "quote": "회사 소개와 문의 화면을 만들어 주세요.", "origin": "client"},
            {"id": "REQ-002", "text": "문의 내용을 저장한다.",
             "quote": "문의 내용은 저장해 주세요.", "origin": "client"},
            {"id": "REQ-003", "text": "INTERNAL_BILLING_SUGGESTION", "quote": "", "origin": "internal"},
        ], "questions": [{"id": "Q-001", "text": "문의에 어떤 내용을 받나요?", "blocking": False}],
            "out_of_scope": []},
        "answers": [{"question_id": "Q-001", "question_text": "문의에 어떤 내용을 받나요?",
                     "answer": "이름과 내용만 받습니다."}]},
        "obligations": site_obligations.catalog()["empty_values"]}


def candidate(stage):
    values = {
        "wireframe": [
            {"path": "wireframe/index.html", "content": "<!doctype html><html><body><h1>문의 화면</h1></body></html>"},
            {"path": "wireframe/screens.json", "content": json.dumps({"screens": [
                {"id": "SCREEN-001", "title": "문의", "path": "/", "requirement_ids": ["REQ-001", "REQ-002"]}]})}],
        "api": [{"path": "api/openapi.json", "content": json.dumps({"openapi": "3.1.0",
            "info": {"title": "Site", "version": "1.0"}, "paths": {"/api/inquiries": {"post": {
                "operationId": "createInquiry", "x-channelshift-requirement-ids": ["REQ-002"],
                "x-channelshift-fields": [], "x-channelshift-screens": ["SCREEN-001"],
                "responses": {"201": {"description": "Created"}}}}}})}],
        "backend": [{"path": "backend/app.py", "content": "import sqlite3\n# Source candidate; not run by this test.\n"},
                    {"path": "backend/README.md", "content": "Run python backend/app.py after review.\n"},
                    {"path": "backend/routes.json", "content": json.dumps({"routes": [{
                        "operation_id": "createInquiry", "handler": "createInquiry",
                        "test_file": "backend/test_app.py", "test_symbol": "test_create"}]})}],
        "frontend": [{"path": "frontend/index.html", "content": (
            "<!doctype html><html><body><h1>문의</h1>"
            + "".join('<a href="' + url + '">정책</a>' for url in sorted(pipeline.POLICY_LINKS))
            + pipeline.FOOTER_MARKER + "</body></html>")},
            {"path": "frontend/app.js", "content": "document.querySelector('h1').textContent = '문의';\n"},
            {"path": "frontend/style.css", "content": "body { font-family: sans-serif; }\n"},
            {"path": "frontend/screens.json", "content": json.dumps({"screens": [{
                "screen_id": "SCREEN-001", "file": "frontend/index.html", "operation_ids": ["createInquiry"]}]})}],
    }
    return {"files": values[stage], "notes": ["실행 및 업무 검수가 필요합니다."]}


def dependencies():
    return {"wireframe": candidate("wireframe"), "api": candidate("api"),
            "erd": {"files": [{"path": "erd/schema.json", "content": '{"entities":[]}'}]},
            "database": {"files": [{"path": "database/schema.sql", "content": "CREATE TABLE inquiry (id INTEGER PRIMARY KEY);"},
                                    {"path": "database/checks.json", "content": '{"foreign_keys":true}'}]}}


def ready():
    return {"available": True, "authenticated": True, "auth_mode": "chatgpt", "can_execute": True,
            "cli_version": "test", "reason": "ready"}


class ValidationTests(unittest.TestCase):
    def invalid(self, value, stage="wireframe"):
        with self.assertRaisesRegex(intake.CodexIntakeError, "^codex_invalid_pipeline_output$"):
            pipeline.validate_stage(value, stage)

    def test_each_stage_has_required_files_and_returned_values_are_detached(self):
        for stage in pipeline.REQUIRED_FILES:
            with self.subTest(stage=stage):
                value = candidate(stage)
                result = pipeline.validate_stage(value, stage)
                self.assertEqual(result, value)
                result["files"][0]["content"] = "changed"
                result["notes"].append("new")
                self.assertNotEqual(result, value)
                self.invalid({"files": value["files"][1:], "notes": []}, stage)

    def test_envelope_file_and_note_properties_cannot_smuggle_execution_or_approval(self):
        for value in (None, [], {}, {**candidate("wireframe"), "approved": True},
                      {**candidate("wireframe"), "tests_passed": True},
                      {"files": [{**candidate("wireframe")["files"][0], "mode": "executable"}], "notes": []}):
            with self.subTest(value_type=type(value).__name__):
                self.invalid(value)
        for notes in (["same", "same"], [""] * 17, [None], ["\ud800"], ["bad\x00note"], ["a" * 2001]):
            self.invalid({**candidate("wireframe"), "notes": notes})

    def test_only_restricted_stage_relative_portable_paths_are_accepted(self):
        hostile = [
            "/wireframe/extra.html", "C:/wireframe/extra.html", "wireframe\\extra.html",
            "wireframe/../extra.html", "wireframe/./extra.html", "wireframe//extra.html",
            "wireframe/%2e%2e/extra.html", "wireframe/.codex/auth.json", "wireframe/.env",
            "wireframe/extra.html:stream", "wireframe/CON.html", "wireframe/com1/report.json",
            "wireframe/nul.json", "wireframe/extra .html", "wireframe/extra.html.",
            "wireframe/화면.html", "frontend/extra.html", "Wireframe/extra.html",
            "wireframe/script.ps1", "wireframe/script.cmd", "wireframe/extra.py",
            "wireframe/auth.json", "wireframe/credentials.json", "wireframe/a/b/c/d/e/f.html",
        ]
        for path in hostile:
            with self.subTest(path=path):
                value = candidate("wireframe")
                value["files"].append({"path": path, "content": "{}"})
                self.invalid(value)
        value = candidate("wireframe")
        value["files"].append({"path": "wireframe/components/summary.html", "content": "<p>요약</p>"})
        pipeline.validate_stage(value, "wireframe")

    def test_path_duplicates_case_aliases_and_file_directory_collisions_fail(self):
        for path in ("wireframe/index.html", "wireframe/INDEX.html", "wireframe/index.html/child.html"):
            value = candidate("wireframe")
            value["files"].append({"path": path, "content": "text"})
            self.invalid(value)
        value = candidate("wireframe")
        value["files"] = list(reversed(value["files"]))
        value["files"].insert(0, {"path": "wireframe/index.html/child.html", "content": "text"})
        self.invalid(value)

    def test_content_byte_and_total_limits_are_independent_of_character_count(self):
        value = candidate("wireframe")
        value["files"].append({"path": "wireframe/details.md", "content": "a" * pipeline.MAX_FILE_BYTES})
        pipeline.validate_stage(value, "wireframe")
        for content in ("a" * (pipeline.MAX_FILE_BYTES + 1), "가" * 21846, "bad\x00content", "", "bad\x7fcontent"):
            value["files"][-1]["content"] = content
            self.invalid(value)
        value = candidate("wireframe")
        value["files"] += [{"path": f"wireframe/{i}.md", "content": "a" * 60000} for i in range(2)]
        self.invalid(value)
        # JSON escaping has its own 128KiB envelope bound below the UTF-8 content bound.
        value["files"][-2:] = [{"path": f"wireframe/{i}.md", "content": "\\" * 50000} for i in range(2)]
        self.invalid(value)
        value = candidate("wireframe")
        value["files"] += [{"path": f"wireframe/{i}.md", "content": "content"} for i in range(23)]
        self.invalid(value)

    def test_json_duplicates_nonfinite_and_malformed_output_fail_safely(self):
        for content in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{bad json', '[' * 1500):
            value = candidate("wireframe")
            value["files"][1]["content"] = content
            self.invalid(value)

    def test_common_literal_credentials_are_rejected_in_files_and_notes(self):
        for secret in ("cs_mcp_" + "a" * 43, "sk-proj-" + "a" * 30,
                       "ghp_" + "a" * 30, "-----BEGIN PRIVATE KEY-----"):
            value = candidate("backend")
            value["files"][0]["content"] = "# " + secret
            self.invalid(value, "backend")
            value = candidate("backend")
            value["notes"] = [secret]
            self.invalid(value, "backend")

    def test_frontend_uses_real_footer_comment_fixed_links_and_server_owned_policy_files(self):
        original = candidate("frontend")
        for content in (
            original["files"][0]["content"].replace(pipeline.FOOTER_MARKER, ""),
            original["files"][0]["content"] + pipeline.FOOTER_MARKER,
            original["files"][0]["content"].replace("/privacy.html", "/wrong.html"),
            original["files"][0]["content"].replace(pipeline.FOOTER_MARKER, '<script>"' + pipeline.FOOTER_MARKER + '"</script>'),
            '<![bogus]>' + original["files"][0]["content"],
        ):
            value = copy.deepcopy(original)
            value["files"][0]["content"] = content
            self.invalid(value, "frontend")
        for name in ("privacy.html", "terms.html", "refund.html", "contact.html", "footer.html", "PRIVACY.html"):
            value = copy.deepcopy(original)
            value["files"].append({"path": "frontend/" + name, "content": "<body>invented policy</body>"})
            self.invalid(value, "frontend")

    def test_frontend_rejects_marked_declarations_without_rejecting_literal_examples(self):
        for declaration in ("<![bogus]>", "<![CDATA[unexpected]]>", "<![if IE]>"):
            with self.subTest(declaration=declaration):
                value = candidate("frontend")
                value["files"][0]["content"] = declaration + value["files"][0]["content"]
                self.invalid(value, "frontend")
        for literal in ('<!-- <![bogus]> -->', '<script>const example = "<![bogus]>";</script>'):
            with self.subTest(literal=literal):
                value = candidate("frontend")
                value["files"][0]["content"] = literal + value["files"][0]["content"]
                self.assertEqual(pipeline.validate_stage(value, "frontend"), value)


class GenerationTests(unittest.TestCase):
    def test_invalid_inputs_never_start_model_transport(self):
        invalid_specs = [None, {}, {**spec(), "database": "postgresql"}, {**spec(), "name": "bad\nname"},
                         {**spec(), "extra": "unexpected"}, {**spec(), "obligations": {}},
                         {**spec(), "requirements": {"candidate": {}}}]
        secret_spec = spec()
        secret_spec["requirements"]["answers"][0]["answer"] = "cs_mcp_" + "a" * 43
        invalid_specs.append(secret_spec)
        with patch.object(intake, "_execute_json") as execute:
            for value in invalid_specs:
                with self.subTest(spec_type=type(value).__name__):
                    with self.assertRaisesRegex(intake.CodexIntakeError, "^invalid_pipeline_input$"):
                        pipeline.generate_stage("wireframe", value, {})
            for stage in ("database", "erd", "unknown", None, []):
                with self.assertRaisesRegex(intake.CodexIntakeError, "^invalid_pipeline_input$"):
                    pipeline.generate_stage(stage, spec(), {})
            for deps in (None, [], {"unknown": {}}, {"frontend": candidate("frontend")},
                         {"api": {"files": [{"path": "api/../secret.json", "content": "{}"}]}}):
                with self.assertRaisesRegex(intake.CodexIntakeError, "^invalid_pipeline_input$"):
                    pipeline.generate_stage("backend", spec(), deps)
            execute.assert_not_called()

    def test_only_client_source_current_answers_and_dependency_files_reach_model(self):
        context = spec()
        context["requirements"]["history"] = "HIDDEN_REVIEW_HISTORY"
        context["requirements"]["approved"] = "HIDDEN_APPROVAL_METADATA"
        context["obligations"]["policies"]["privacy"] = "PRIVATE_POLICY_TEXT_FOR_DETERMINISTIC_RENDERING"
        deps = dependencies()
        deps["api"]["checks"] = {"credential": "PRIVATE_PROVIDER_METADATA"}
        deps["api"]["notes"] = ["NOT_TRANSMITTED_NOTE"]
        with patch.object(intake, "_execute_json", return_value=candidate("backend")) as execute:
            result = pipeline.generate_stage("backend", context, deps, codex_home=Path("member-private-home"))
        self.assertEqual(result, candidate("backend"))
        prompt = execute.call_args.args[0]
        transmitted = json.loads(prompt.split("\n", 1)[1])
        self.assertIn(SOURCE, prompt)
        self.assertIn("이름과 내용만 받습니다.", prompt)
        self.assertEqual(len(transmitted["site_disclosures"]), 7)
        self.assertEqual(set(transmitted["dependency_artifacts"]["api"]), {"files"})
        self.assertEqual([row["id"] for row in transmitted["confirmed_spec"]["client_requirements"]], ["REQ-001", "REQ-002"])
        for hidden in ("INTERNAL_BILLING_SUGGESTION", "HIDDEN_REVIEW_HISTORY", "HIDDEN_APPROVAL_METADATA",
                       "PRIVATE_PROVIDER_METADATA", "NOT_TRANSMITTED_NOTE", "PRIVATE_POLICY_TEXT_FOR_DETERMINISTIC_RENDERING"):
            self.assertNotIn(hidden, prompt)
        self.assertEqual(execute.call_args.kwargs, {"codex_home": Path("member-private-home")})
        self.assertEqual(context["requirements"]["history"], "HIDDEN_REVIEW_HISTORY")
        self.assertEqual(deps["api"]["checks"], {"credential": "PRIVATE_PROVIDER_METADATA"})

    def test_disclosure_value_changes_do_not_change_model_input(self):
        before, after = spec(), spec()
        after["obligations"]["hosting"]["name"] = "나중에 정한 호스팅사"
        with patch.object(intake, "_execute_json", return_value=candidate("wireframe")) as execute:
            pipeline.generate_stage("wireframe", before, {})
            pipeline.generate_stage("wireframe", after, {})
        self.assertEqual(execute.call_args_list[0], execute.call_args_list[1])

    def test_model_output_is_validated_even_with_trusted_transport(self):
        value = candidate("api")
        value["files"][0]["path"] = "api/../../host.json"
        with patch.object(intake, "_execute_json", return_value=value):
            with self.assertRaisesRegex(intake.CodexIntakeError, "^codex_invalid_pipeline_output$"):
                pipeline.generate_stage("api", spec(), {})

    def test_synthetic_transport_has_no_tools_host_home_or_files_and_cleans_workspace(self):
        seen = []

        def transport(arguments, **options):
            self.assertEqual(arguments[-1], "-")
            self.assertNotIn(SOURCE, " ".join(arguments))
            self.assertIn(SOURCE, options["input_text"])
            self.assertEqual(options["codex_home"], home)
            child_env = intake._child_environment(codex_home=home)
            self.assertEqual(child_env["CODEX_HOME"], str(home))
            self.assertNotIn("OPENAI_API_KEY", child_env)
            self.assertNotIn("SERVICE_TOKEN", child_env)
            self.assertIn('cli_auth_credentials_store="file"', arguments)
            self.assertIn('--ignore-user-config', arguments)
            self.assertEqual(arguments[arguments.index('--sandbox') + 1], 'read-only')
            for feature in intake._DISABLED_FEATURES:
                self.assertIn(feature, arguments)
            workspace = Path(options["cwd"])
            seen.append(workspace)
            self.assertEqual({path.name for path in workspace.iterdir()}, {".git"})
            schema = json.loads(Path(arguments[arguments.index("--output-schema") + 1]).read_text(encoding="utf-8"))
            self.assertEqual(schema["properties"]["files"]["items"]["properties"]["path"]["pattern"], "^wireframe/")
            output = Path(arguments[arguments.index("--output-last-message") + 1])
            output.write_text(json.dumps(candidate("wireframe")), encoding="utf-8")
            return 0, b"", b""

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with patch.dict(os.environ, {"CODEX_HOME": "private-host-home", "OPENAI_API_KEY": "private-host-key",
                                         "SERVICE_TOKEN": "private-service-token"}), \
                 patch.object(intake, "_executable", return_value="codex"), \
                 patch.object(intake, "_probe", return_value=ready()) as probe, \
                 patch.object(intake, "_run_bounded", side_effect=transport):
                self.assertEqual(pipeline.generate_stage("wireframe", spec(), {}, codex_home=home), candidate("wireframe"))
                probe.assert_called_once_with("codex", codex_home=home)
                self.assertEqual(os.environ["CODEX_HOME"], "private-host-home")
        self.assertFalse(seen[0].exists())

    def test_subscription_authentication_and_global_model_reservation_are_preserved(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(intake, "_executable", return_value="codex"), \
             patch.object(intake, "_probe", return_value={**ready(), "can_execute": False,
                                                        "reason": "codex_subscription_required"}), \
             patch.object(intake, "_run_bounded") as execute:
            with self.assertRaisesRegex(intake.CodexIntakeError, "^codex_subscription_required$"):
                pipeline.generate_stage("wireframe", spec(), {})
            execute.assert_not_called()
        intake._EXECUTION_LOCK.acquire()
        try:
            with patch.object(intake, "_probe") as probe, self.assertRaisesRegex(intake.CodexIntakeError, "^codex_busy$"):
                pipeline.generate_stage("wireframe", spec(), {})
            probe.assert_not_called()
        finally:
            intake._EXECUTION_LOCK.release()

    def test_member_stage_uses_only_own_auth_and_status_is_responsive_while_busy(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = MemberCodex(Path(folder))
            self.addCleanup(manager.close)
            with patch.object(pipeline, "generate_stage") as generate:
                with self.assertRaisesRegex(MemberCodexError, "^codex_authentication_required$"):
                    manager.generate_stage(USER_A, "wireframe", spec(), {})
                generate.assert_not_called()
            home = manager._home(USER_A, create=True)
            descriptor = os.open(home / "auth.json", os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
            started, release = threading.Event(), threading.Event()
            errors, results = [], []

            def generate(stage, confirmed_spec, deps, *, codex_home):
                self.assertEqual((stage, confirmed_spec, deps, codex_home), ("wireframe", spec(), {}, home))
                started.set()
                release.wait(5)
                return candidate("wireframe")

            def run():
                try:
                    results.append(manager.generate_stage(USER_A, "wireframe", spec(), {}))
                except BaseException as exc:
                    errors.append(exc)

            with patch.object(pipeline, "generate_stage", side_effect=generate):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(started.wait(2))
                    self.assertEqual(manager.status(USER_A)["state"], "busy")
                    self.assertEqual(manager.status(USER_B)["state"], "disconnected")
                    with self.assertRaisesRegex(MemberCodexError, "^codex_authentication_required$"):
                        manager.generate_stage(USER_B, "wireframe", spec(), {})
                finally:
                    release.set()
                    worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
            self.assertEqual(results, [candidate("wireframe")])


if __name__ == "__main__":
    unittest.main()
