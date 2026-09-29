"""Durable local intake behavior using synthetic data and injected providers."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock

from channelshift.codex_intake import CodexIntakeError
from channelshift.delivery_workspace import DeliveryWorkspace
from channelshift.jev_review import JevError


SOURCE = "합성 테스트 요청입니다. 회사 소개와 문의 화면을 만들어 주세요."


def candidate(label="첫 번째 후보"):
    return {"requirements": [{"id": "REQ-001", "text": label,
                              "quote": "문의 화면을 만들어 주세요.", "origin": "client"}],
            "questions": [{"id": "Q-001", "text": "합성 질문: 보관 기간은 얼마인가요?", "blocking": True}],
            "out_of_scope": []}


def advice(label="supported"):
    return {"format": "channelshift.jev-advice/v1", "status": "REVIEW_REQUIRED", "approved": False,
            "input_digest": "a" * 64, "model": "synthetic-model",
            "items": [{"requirement_id": "REQ-001", "judgment": label, "confidence": 0.8,
                       "probabilities": {key: float(key == label)
                                         for key in ("supported", "unsupported", "contradicted", "unclear")}}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


class DeliveryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="channelshift-delivery-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "delivery.sqlite3"
        self.extract = Mock(return_value=candidate())
        self.review = Mock(return_value=advice())
        self.workspace = self.open_workspace()

    def open_workspace(self, extract=None, review=None):
        workspace = DeliveryWorkspace(self.path, extract=extract or self.extract, review=review or self.review)
        self.addCleanup(workspace.close)
        return workspace

    def drain(self, workspace=None):
        # A queued sentinel completes after the injected provider job, including
        # its durable event write and in-memory lock release. No model is called.
        (workspace or self.workspace)._jobs.submit(lambda: None).result(timeout=5)

    def create(self, name="합성 프로젝트"):
        return self.workspace.create(name, SOURCE)

    def run_operation(self, project_id, operation, workspace=None):
        workspace = workspace or self.workspace
        workspace.start(project_id, operation)
        self.drain(workspace)
        return workspace.get(project_id)

    def test_no_job_before_source_registration_or_before_candidate(self):
        for source in ("", " \n\t", None, "x" * 12001):
            with self.subTest(source_type=type(source).__name__), self.assertRaises(ValueError):
                self.workspace.create("합성 프로젝트", source)
        self.assertEqual(self.workspace.list(), [])
        with self.assertRaisesRegex(ValueError, "^delivery_project_not_found$"):
            self.workspace.start("0" * 32, "extract")
        project = self.create()
        with self.assertRaisesRegex(ValueError, "^delivery_candidate_required$"):
            self.workspace.start(project["id"], "jev")
        self.extract.assert_not_called()
        self.review.assert_not_called()
        self.assertEqual([event["kind"] for event in project["events"]], ["source_registered"])

    def test_source_is_verbatim_and_returned_objects_cannot_mutate_storage(self):
        source = "  합성 원문\n문의 화면을 만들어 주세요. 😀\r\n끝  "
        project = self.workspace.create("합성 보존", source)
        original = copy.deepcopy(project)
        project["source"]["text"] = "변조"
        project["events"][0]["payload"]["digest"] = "변조"
        self.assertEqual(self.workspace.get(project["id"]), original)
        result = self.run_operation(project["id"], "extract")
        self.assertEqual(result["source"]["text"], source)
        self.assertEqual(result["source"], original["source"])
        self.extract.assert_called_once_with(source)
        result["candidate"]["requirements"][0]["text"] = "변조"
        self.assertNotEqual(self.workspace.get(project["id"])["candidate"], result["candidate"])

    def test_source_events_and_human_reasons_survive_restart_without_approval(self):
        project = self.create()
        project_id = project["id"]
        result = self.workspace.intervene(project_id, "intake", "missing_client_info",
                                          "합성 질문의 답이 부족합니다.", "고객에게 추가 확인", "답변 대기",
                                          project["intervention_revision"])
        event = result["interventions"][0]
        self.assertEqual({key: event[key] for key in ("stage_id", "reason", "note", "decision", "outcome")},
                         {"stage_id": "intake", "reason": "missing_client_info",
                          "note": "합성 질문의 답이 부족합니다.", "decision": "고객에게 추가 확인", "outcome": "답변 대기"})
        self.assertFalse(event["approval_granted"])
        self.assertEqual(event["actor_type"], "local_operator")
        self.assertEqual(result["state"], "RECEIVED")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["events"][:1], project["events"])
        self.workspace.close()
        reopened = self.open_workspace()
        self.assertEqual(reopened.get(project_id), result)
        second = reopened.intervene(project_id, "intake", "other", "합성 후속 기록", "승인했다고 적은 메모", "기록만 저장",
                                    result["intervention_revision"])
        self.assertEqual(second["events"][:-1], result["events"])
        self.assertEqual(second["state"], "RECEIVED")
        self.assertFalse(second["interventions"][-1]["approval_granted"])
        self.assertEqual(second["intervention_revision"], project["intervention_revision"])
        sequences = [entry["sequence"] for entry in second["events"]]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_human_intervention_validation_does_not_append_bad_events(self):
        project = self.create()
        original = self.workspace.get(project["id"])
        base = [project["id"], "intake", "other", "합성 사유", "다음 결정", "", project["intervention_revision"]]
        for index, value in ((1, "unknown"), (2, "unknown"), (3, ""), (4, ""), (5, "x" * 2001),
                             (6, None), (6, True), (6, ""), (6, "x" * 64)):
            with self.subTest(index=index):
                args = list(base)
                args[index] = value
                with self.assertRaisesRegex(ValueError, "^invalid_delivery_input$"):
                    self.workspace.intervene(*args)
                self.assertEqual(self.workspace.get(project["id"]), original)

    def test_successful_extraction_and_review_keep_all_old_result_snapshots(self):
        project = self.create()
        project_id = project["id"]
        first = self.run_operation(project_id, "extract")
        self.assertEqual(first["state"], "REVIEW_REQUIRED")
        self.assertEqual(first["candidate"], candidate())
        reviewed = self.run_operation(project_id, "jev")
        self.review.assert_called_once_with(SOURCE, candidate()["requirements"])
        self.assertEqual(reviewed["jev"], advice())
        self.assertFalse(reviewed["jev"]["approved"])
        self.extract.return_value = candidate("두 번째 후보")
        revised = self.run_operation(project_id, "extract")
        self.assertEqual(revised["candidate"], candidate("두 번째 후보"))
        self.assertIsNone(revised["jev"])
        self.assertEqual(revised["events"][:len(reviewed["events"])], reviewed["events"])
        stored_candidates = [event["payload"]["result"] for event in revised["events"]
                             if event["kind"] == "candidate_recorded"]
        stored_advice = [event["payload"]["result"] for event in revised["events"]
                        if event["kind"] == "advice_recorded"]
        self.assertEqual(stored_candidates, [candidate(), candidate("두 번째 후보")])
        self.assertEqual(stored_advice, [advice()])
        starts = [event["payload"] for event in revised["events"] if event["kind"] == "job_started"]
        completions = [event["payload"] for event in revised["events"]
                       if event["kind"] in {"candidate_recorded", "advice_recorded"}]
        self.assertEqual(len({event["job_id"] for event in starts}), 3)
        self.assertEqual([event["job_id"] for event in starts], [event["job_id"] for event in completions])
        for event in completions:
            self.assertIs(type(event["elapsed_ms"]), int)
            self.assertGreaterEqual(event["elapsed_ms"], 0)
            self.assertEqual(event["source_digest"], project["source"]["digest"])
        self.workspace.close()
        self.assertEqual(self.open_workspace().get(project_id), revised)

    def test_failed_retry_preserves_previous_candidate_advice_and_history(self):
        project_id = self.create()["id"]
        self.run_operation(project_id, "extract")
        old = self.run_operation(project_id, "jev")
        self.extract.side_effect = RuntimeError("SYNTHETIC_SECRET_SHOULD_NOT_BE_STORED")
        failed = self.run_operation(project_id, "extract")
        self.assertEqual(failed["state"], "NEEDS_ATTENTION")
        self.assertEqual(failed["candidate"], old["candidate"])
        self.assertEqual(failed["jev"], old["jev"])
        self.assertEqual(failed["events"][:len(old["events"])], old["events"])
        self.assertEqual(failed["events"][-1]["payload"]["code"], "delivery_job_failed")
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(failed))
        self.workspace.close()
        self.assertEqual(self.open_workspace().get(project_id), failed)

    def test_provider_exception_messages_never_enter_persisted_events(self):
        secret = "SYNTHETIC_PRIVATE_PROVIDER_DETAIL"
        for error_type in (RuntimeError, CodexIntakeError, JevError):
            with self.subTest(error_type=error_type.__name__):
                project_id = self.create(error_type.__name__)["id"]
                self.extract.side_effect = error_type(secret)
                result = self.run_operation(project_id, "extract")
                self.assertEqual(result["state"], "NEEDS_ATTENTION")
                self.assertNotIn(secret, json.dumps(result))
                self.assertIn(result["events"][-1]["payload"]["code"],
                              {"delivery_job_failed", "codex_execution_failed", "jev_request_failed"})
                self.assertNotIn(secret, json.dumps(self.open_workspace().get(project_id)))

    def test_intervention_records_exact_job_and_candidate_context(self):
        project_id = self.create()["id"]
        before = self.run_operation(project_id, "extract")
        result = self.workspace.intervene(project_id, "intake", "quality_issue", "합성 품질 문제",
                                          "문구를 다시 검토", "진행 보류", before["intervention_revision"])
        intervention = result["interventions"][-1]
        start = next(event for event in before["events"] if event["kind"] == "job_started")
        self.assertEqual(intervention["job_id"], start["payload"]["job_id"])
        self.assertEqual(intervention["source_digest"], before["source"]["digest"])
        self.assertEqual(intervention["state_at_intervention"], "REVIEW_REQUIRED")
        self.assertRegex(intervention["candidate_digest"], "^[a-f0-9]{64}$")
        self.assertEqual(intervention["intervention_revision"], before["intervention_revision"])
        self.assertFalse(intervention["approval_granted"])
        self.assertEqual(result["candidate"], before["candidate"])
        self.assertEqual(result["state"], before["state"])
        self.workspace.close()
        self.assertEqual(self.open_workspace().get(project_id)["interventions"][-1], intervention)

    def test_stale_tab_cannot_attach_its_note_to_a_replacement_candidate(self):
        project_id = self.create()["id"]
        viewed = self.run_operation(project_id, "extract")
        other_tab = self.open_workspace()
        self.extract.return_value = candidate("두 번째 후보")
        latest = self.run_operation(project_id, "extract", other_tab)
        self.assertNotEqual(viewed["intervention_revision"], latest["intervention_revision"])
        with self.assertRaisesRegex(ValueError, "^delivery_revision_conflict$"):
            self.workspace.intervene(project_id, "requirements", "quality_issue", "첫 후보의 메모",
                                     "첫 후보 재검토", "", viewed["intervention_revision"])
        self.assertEqual(self.workspace.get(project_id), latest)
        recorded = self.workspace.intervene(project_id, "requirements", "quality_issue", "두 번째 후보 확인",
                                             "검토 계속", "", latest["intervention_revision"])
        self.assertEqual(recorded["interventions"][-1]["intervention_revision"], latest["intervention_revision"])
        self.assertFalse(recorded["interventions"][-1]["approval_granted"])

    def test_revision_binds_project_and_repeated_job_even_with_identical_output(self):
        first, second = self.create(), self.create()
        self.assertNotEqual(first["intervention_revision"], second["intervention_revision"])
        with self.assertRaisesRegex(ValueError, "^delivery_revision_conflict$"):
            self.workspace.intervene(first["id"], "intake", "other", "메모", "확인", "",
                                     second["intervention_revision"])
        previous = self.run_operation(first["id"], "extract")
        latest = self.run_operation(first["id"], "extract")
        self.assertEqual(previous["candidate"], latest["candidate"])
        self.assertEqual(previous["state"], latest["state"])
        self.assertNotEqual(previous["intervention_revision"], latest["intervention_revision"])
        with self.assertRaisesRegex(ValueError, "^delivery_revision_conflict$"):
            self.workspace.intervene(first["id"], "requirements", "other", "메모", "확인", "",
                                     previous["intervention_revision"])
        self.assertEqual(self.workspace.get(first["id"]), latest)

    def test_running_and_completed_job_have_distinct_intervention_revisions(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_extract(source):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test_release_timeout")
            return candidate()

        self.extract.side_effect = blocked_extract
        before = self.create()
        try:
            running = self.workspace.start(before["id"], "extract")
            self.assertTrue(entered.wait(timeout=2))
            self.assertNotEqual(before["intervention_revision"], running["intervention_revision"])
            with self.assertRaisesRegex(ValueError, "^delivery_revision_conflict$"):
                self.workspace.intervene(before["id"], "intake", "other", "메모", "확인", "",
                                         before["intervention_revision"])
            recorded = self.workspace.intervene(before["id"], "requirements", "other", "실행 중 메모", "대기", "",
                                                  running["intervention_revision"])
            self.assertEqual(recorded["interventions"][-1]["state_at_intervention"], "EXTRACTING")
        finally:
            release.set()
            self.drain()
        finished = self.workspace.get(before["id"])
        self.assertNotEqual(running["intervention_revision"], finished["intervention_revision"])
        with self.assertRaisesRegex(ValueError, "^delivery_revision_conflict$"):
            self.workspace.intervene(before["id"], "requirements", "other", "메모", "확인", "",
                                     running["intervention_revision"])
        self.assertEqual(self.workspace.get(before["id"]), finished)

    def test_failed_review_preserves_previous_candidate_and_advice(self):
        project_id = self.create()["id"]
        self.run_operation(project_id, "extract")
        previous = self.run_operation(project_id, "jev")
        self.review.side_effect = JevError("jev_unavailable")
        result = self.run_operation(project_id, "jev")
        self.assertEqual(result["state"], "NEEDS_ATTENTION")
        self.assertEqual(result["candidate"], previous["candidate"])
        self.assertEqual(result["jev"], previous["jev"])
        self.assertEqual(result["events"][:len(previous["events"])], previous["events"])
        self.assertEqual(result["events"][-1]["payload"]["code"], "jev_unavailable")

    def test_known_safe_provider_failure_code_is_preserved(self):
        project_id = self.create()["id"]
        self.extract.side_effect = CodexIntakeError("codex_authentication_required")
        result = self.run_operation(project_id, "extract")
        self.assertEqual(result["events"][-1]["payload"]["code"], "codex_authentication_required")
        self.assertEqual(result["state"], "NEEDS_ATTENTION")

    def test_concurrent_starts_across_instances_execute_exactly_one_job(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_extract(source):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test_release_timeout")
            return candidate()

        self.extract.side_effect = blocked_extract
        second = self.open_workspace()
        first_id, second_id = self.create("첫 프로젝트")["id"], self.create("둘째 프로젝트")["id"]
        barrier = threading.Barrier(2)

        def start(workspace, project_id):
            barrier.wait(timeout=5)
            try:
                workspace.start(project_id, "extract")
                return "started"
            except ValueError as error:
                return str(error)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(start, self.workspace, first_id),
                           executor.submit(start, second, second_id)]
                outcomes = [future.result(timeout=5) for future in futures]
            self.assertTrue(entered.wait(timeout=2))
            self.assertEqual(outcomes.count("started"), 1)
            self.assertEqual(outcomes.count("delivery_recovery_required"), 1)
            self.assertEqual(self.extract.call_count, 1)
        finally:
            release.set()
            self.drain(self.workspace)
            self.drain(second)
        projects = [self.workspace.get(first_id), second.get(second_id)]
        self.assertEqual(sum(project["state"] == "REVIEW_REQUIRED" for project in projects), 1)
        self.assertEqual(sum(project["state"] == "RECEIVED" for project in projects), 1)

    def test_active_job_blocks_same_instance_until_completion(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_extract(source):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test_release_timeout")
            return candidate()

        self.extract.side_effect = blocked_extract
        first_id, second_id = self.create("첫 프로젝트")["id"], self.create("둘째 프로젝트")["id"]
        try:
            self.workspace.start(first_id, "extract")
            self.assertTrue(entered.wait(timeout=2))
            with self.assertRaisesRegex(ValueError, "^delivery_busy$"):
                self.workspace.start(second_id, "extract")
        finally:
            release.set()
            self.drain()
        self.extract.side_effect = None
        self.run_operation(second_id, "extract")
        self.assertEqual(self.extract.call_count, 2)

    def test_orphan_executing_state_is_not_reset_or_silently_retried(self):
        for state in ("EXTRACTING", "REVIEWING"):
            with self.subTest(state=state):
                project_id = self.create(state)["id"]
                with closing(sqlite3.connect(self.path)) as db, db:
                    # Simulate a process ending after its state was committed;
                    # opening a new instance is not proof that its writer stopped.
                    db.execute("UPDATE projects SET state=? WHERE id=?", (state, project_id))
                reopened = self.open_workspace()
                old = reopened.get(project_id)
                self.assertEqual(old["state"], state)
                with self.assertRaisesRegex(ValueError, "^delivery_recovery_required$"):
                    reopened.start(project_id, "extract")
                other_id = reopened.create("다른 합성 프로젝트", SOURCE)["id"]
                with self.assertRaisesRegex(ValueError, "^delivery_recovery_required$"):
                    reopened.start(other_id, "extract")
                self.assertEqual(reopened.get(project_id), old)
        self.extract.assert_not_called()
        self.review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
