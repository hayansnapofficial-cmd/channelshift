"""ERD generation boundaries; every model transport is synthetic or mocked."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from channelshift import codex_erd as erd
from channelshift import codex_intake as intake
from channelshift import core
from channelshift.member_codex import MemberCodex, MemberCodexError


SOURCE = "문의 내용을 저장해 주세요. 회사 소개 문구를 보여 주세요."
USER = "a" * 32


def snapshot():
    return {"name": "고객 홈페이지", "approval_granted": False,
            "source": {"text": SOURCE}, "candidate_input": {"text": SOURCE},
            "candidate": {"requirements": [
                {"id": "REQ-001", "text": "문의 내용을 보관한다.", "quote": "문의 내용을 저장해 주세요.", "origin": "client"},
                {"id": "REQ-002", "text": "회사 소개 문구를 표시한다.", "quote": "회사 소개 문구를 보여 주세요.", "origin": "client"},
                {"id": "REQ-003", "text": "INTERNAL_ONLY_BILLING: 결제 기능을 제안한다.", "quote": "", "origin": "internal"},
            ], "questions": [], "out_of_scope": []}, "answers": []}


def attribute(name="id", kind="bigint", *, primary=True, nullable=False, unique=False):
    return {"name": name, "type": kind, "nullable": nullable, "primary_key": primary, "unique": unique}


def design(database="postgresql"):
    return {"schema": {"format": core.FORMAT, "name": snapshot()["name"], "database": database,
                       "entities": [{"name": "inquiries", "description": "고객이 요청한 문의 보관",
                                     "attributes": [attribute(), attribute("message", "text", primary=False)]}],
                       "relations": []},
            "traceability": [{"entity": "inquiries", "requirement_ids": ["REQ-001"]}],
            "unmapped_requirements": [{"requirement_id": "REQ-002", "reason": "화면 문구이며 저장 요구가 없습니다."}],
            "notes": ["보관 기간은 사람이 확인해야 합니다."]}


def ready():
    return {"available": True, "authenticated": True, "auth_mode": "chatgpt", "can_execute": True,
            "cli_version": "test", "reason": "ready"}


class ValidationTests(unittest.TestCase):
    def invalid(self, value):
        with self.assertRaisesRegex(intake.CodexIntakeError, "^codex_invalid_erd_output$"):
            erd.validate_erd(value, snapshot(), "postgresql")

    def test_valid_schema_is_native_detached_and_uses_fixed_type_defaults(self):
        for database in sorted(core.DATABASES):
            value = design(database)
            value["schema"]["entities"][0]["attributes"] += [
                attribute("label", "varchar", primary=False), attribute("amount", "decimal", primary=False)]
            result = erd.validate_erd(value, snapshot(), database)
            self.assertTrue(core.validate_schema(result["schema"])["valid"])
            sql = core.export_sql(result["schema"])
            if database != "sqlite":
                self.assertIn("VARCHAR(255)", sql)
                self.assertRegex(sql, r"DECIMAL\(19,\s*2\)")
            result["schema"]["entities"].clear()
            self.assertEqual(len(value["schema"]["entities"]), 1)

    def test_every_entity_traces_client_requirements_with_complete_disjoint_coverage(self):
        mutations = [
            lambda value: value["traceability"].clear(),
            lambda value: value["traceability"][0].update(entity="unknown_entity"),
            lambda value: value["traceability"][0].update(requirement_ids=[]),
            lambda value: value["traceability"][0].update(requirement_ids=["REQ-003"]),
            lambda value: value["traceability"][0].update(requirement_ids=["REQ-999"]),
            lambda value: value["traceability"][0].update(requirement_ids=["REQ-001", "REQ-001"]),
            lambda value: value["traceability"].append(copy.deepcopy(value["traceability"][0])),
            lambda value: value["unmapped_requirements"].clear(),
            lambda value: value["unmapped_requirements"][0].update(requirement_id="REQ-001"),
            lambda value: value["unmapped_requirements"][0].update(requirement_id="REQ-003"),
            lambda value: value["unmapped_requirements"][0].update(requirement_id="REQ-999"),
            lambda value: value["unmapped_requirements"].append(copy.deepcopy(value["unmapped_requirements"][0])),
            lambda value: value["unmapped_requirements"][0].update(reason=" "),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                value = design()
                mutate(value)
                self.invalid(value)

    def test_one_client_requirement_can_need_multiple_related_entities(self):
        value = design()
        value["schema"]["entities"].append({"name": "inquiry_notes", "description": "문의에 속한 메모",
            "attributes": [attribute(), attribute("inquiry_id", primary=False)]})
        value["schema"]["relations"].append({"name": "fk_inquiry_notes_inquiry",
            "from": {"entity": "inquiry_notes", "columns": ["inquiry_id"]},
            "to": {"entity": "inquiries", "columns": ["id"]}, "on_delete": "restrict"})
        value["traceability"].append({"entity": "inquiry_notes", "requirement_ids": ["REQ-001"]})
        erd.validate_erd(value, snapshot(), "postgresql")
        value["schema"]["relations"][0]["to"]["columns"] = ["message"]
        self.invalid(value)

    def test_non_database_requirements_can_all_remain_unmapped(self):
        value = design()
        value["schema"]["entities"] = []
        value["traceability"] = []
        value["unmapped_requirements"].append({"requirement_id": "REQ-001", "reason": "저장 대상이 확정되지 않았습니다."})
        erd.validate_erd(value, snapshot(), "postgresql")

    def test_unknown_properties_approval_sql_records_and_defaults_are_rejected(self):
        for path, key, addition in [
            ([], "approved", True), ([], "sql", "DROP TABLE anything"),
            (["schema"], "rows", [{"private": "record"}]),
            (["schema", "entities", 0], "indexes", []),
            (["schema", "entities", 0, "attributes", 0], "default", "private-record"),
            (["schema", "entities", 0, "attributes", 0], "length", 255),
            (["traceability", 0], "approved", True),
        ]:
            with self.subTest(path=path, key=key):
                value = design()
                target = value
                for step in path:
                    target = target[step]
                target[key] = addition
                self.invalid(value)
        for key, replacement in [("format", "other"), ("name", "Another member's project"), ("database", "mysql")]:
            value = design()
            value["schema"][key] = replacement
            self.invalid(value)

    def test_native_invalid_names_keys_types_and_relationships_fail_closed(self):
        for key, replacement in [("name", "id;drop_table"), ("primary_key", False),
                                 ("nullable", True), ("unique", 1), ("type", "raw_sql")]:
            with self.subTest(key=key):
                value = design()
                value["schema"]["entities"][0]["attributes"][0][key] = replacement
                self.invalid(value)
        value = design()
        value["schema"]["entities"].append(copy.deepcopy(value["schema"]["entities"][0]))
        self.invalid(value)

    def test_output_sizes_types_duplicates_and_control_text_are_bounded(self):
        for key, count in [("entities", 21), ("relations", 81)]:
            value = design()
            value["schema"][key] = [{}] * count
            self.invalid(value)
        value = design()
        value["schema"]["entities"][0]["attributes"] = [attribute()] * 41
        self.invalid(value)
        for notes in (["note"] * 17, ["duplicate", "duplicate"], ["\ud800"], ["\x00"], ["x" * 2001], [None]):
            value = design()
            value["notes"] = notes
            self.invalid(value)
        for value in (None, [], {**design(), "notes": float("nan")}):
            self.invalid(value)

    def test_invalid_snapshot_and_database_never_start_a_process(self):
        values = [None, {}, {**snapshot(), "name": "bad\nname"},
                  {**snapshot(), "candidate_input": {"text": "fabricated source"}},
                  {**snapshot(), "oversized_metadata": "x" * erd.MAX_SNAPSHOT_BYTES}]
        internal_only = snapshot()
        internal_only["candidate"]["requirements"] = [internal_only["candidate"]["requirements"][2]]
        values.append(internal_only)
        for value in values:
            with self.subTest(value_type=type(value).__name__), patch.object(intake, "_execute_json") as execute:
                with self.assertRaisesRegex(intake.CodexIntakeError, "^invalid_erd_input$"):
                    erd.generate_erd(value, "postgresql")
                execute.assert_not_called()
        for database in (None, "oracle", [], "sqlite\n"):
            with patch.object(intake, "_execute_json") as execute:
                with self.assertRaisesRegex(intake.CodexIntakeError, "^invalid_erd_input$"):
                    erd.generate_erd(snapshot(), database)
                execute.assert_not_called()


class GenerationTests(unittest.TestCase):
    def test_generation_reuses_disabled_tools_stdin_workspace_and_explicit_home(self):
        seen_workspace = []
        context = snapshot()
        context["candidate"]["questions"] = [{"id": "Q-001", "text": "보관 기간은?", "blocking": True}]
        context["answers"] = [{"question_id": "Q-001", "question_text": "보관 기간은?", "answer": "30일"}]

        def transport(arguments, **options):
            self.assertEqual(arguments[:5], ["codex", "--no-daemon", "-a", "never", "exec"])
            self.assertEqual(arguments[-1], "-")
            self.assertNotIn(SOURCE, arguments)
            self.assertIn(SOURCE, options["input_text"])
            self.assertIn("30일", options["input_text"])
            self.assertNotIn("INTERNAL_ONLY_BILLING", options["input_text"])
            self.assertNotIn("REQ-003", options["input_text"])
            self.assertIn('cli_auth_credentials_store="file"', arguments)
            self.assertIn('--ignore-user-config', arguments)
            self.assertEqual(arguments[arguments.index('--sandbox') + 1], 'read-only')
            for feature in intake._DISABLED_FEATURES:
                self.assertIn(feature, arguments)
            self.assertEqual(options["codex_home"], home)
            workspace = Path(options["cwd"])
            seen_workspace.append(workspace)
            self.assertEqual(set(item.name for item in workspace.iterdir()), {".git"})
            schema = json.loads(Path(arguments[arguments.index("--output-schema") + 1]).read_text(encoding="utf-8"))
            self.assertEqual(schema["properties"]["schema"]["properties"]["name"]["enum"], [context["name"]])
            self.assertEqual(schema["properties"]["schema"]["properties"]["database"]["enum"], ["postgresql"])
            Path(arguments[arguments.index("--output-last-message") + 1]).write_text(json.dumps(design()), encoding="utf-8")
            return 0, b"", b""

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with patch.dict(os.environ, {"CODEX_HOME": "host-private-home", "OPENAI_API_KEY": "host-key"}), \
                 patch.object(intake, "_executable", return_value="codex"), \
                 patch.object(intake, "_probe", return_value=ready()) as probe, \
                 patch.object(intake, "_run_bounded", side_effect=transport):
                self.assertEqual(erd.generate_erd(context, "postgresql", codex_home=home), design())
                probe.assert_called_once_with("codex", codex_home=home)
                self.assertEqual(os.environ["CODEX_HOME"], "host-private-home")
        self.assertFalse(seen_workspace[0].exists())

    def test_generation_requires_chatgpt_and_shares_extraction_process_lock(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(intake, "_executable", return_value="codex"), \
             patch.object(intake, "_probe", return_value={**ready(), "can_execute": False,
                                                        "reason": "codex_subscription_required"}), \
             patch.object(intake, "_run_bounded") as execute:
            with self.assertRaisesRegex(intake.CodexIntakeError, "^codex_subscription_required$"):
                erd.generate_erd(snapshot(), "postgresql")
            execute.assert_not_called()
        intake._EXECUTION_LOCK.acquire()
        try:
            with patch.object(intake, "_probe") as probe, self.assertRaisesRegex(intake.CodexIntakeError, "^codex_busy$"):
                erd.generate_erd(snapshot(), "postgresql")
            probe.assert_not_called()
        finally:
            intake._EXECUTION_LOCK.release()

    def test_untrusted_output_is_validated_after_shared_model_transport(self):
        value = design()
        value["traceability"][0]["requirement_ids"] = ["REQ-003"]
        with patch.object(intake, "_execute_json", return_value=value) as execute:
            with self.assertRaisesRegex(intake.CodexIntakeError, "^codex_invalid_erd_output$"):
                erd.generate_erd(snapshot(), "postgresql")
            execute.assert_called_once()

    def test_member_generation_never_falls_back_to_host_and_status_remains_responsive(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = MemberCodex(Path(folder))
            self.addCleanup(manager.close)
            with patch.object(erd, "generate_erd") as generate:
                with self.assertRaisesRegex(MemberCodexError, "^codex_authentication_required$"):
                    manager.generate_erd(USER, snapshot(), "postgresql")
                generate.assert_not_called()
            home = manager._home(USER, create=True)
            descriptor = os.open(home / "auth.json", os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
            started, release = threading.Event(), threading.Event()

            def generate(context, database, *, codex_home):
                self.assertEqual(context, snapshot())
                self.assertEqual(database, "postgresql")
                self.assertEqual(codex_home, home)
                started.set()
                release.wait(5)
                return design()

            with patch.object(erd, "generate_erd", side_effect=generate):
                worker = threading.Thread(target=manager.generate_erd, args=(USER, snapshot(), "postgresql"))
                worker.start()
                try:
                    self.assertTrue(started.wait(2))
                    self.assertEqual(manager.status(USER)["state"], "busy")
                finally:
                    release.set()
                    worker.join(5)
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
