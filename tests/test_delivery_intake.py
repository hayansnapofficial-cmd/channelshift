"""B0 source-first planning checks without storage or authentication claims."""
import copy
import unittest

from channelshift.delivery_intake import (
    evaluate_intake, requirement_digest, source_digest, source_snapshot_digest,
)


def fixture():
    text = "예약 페이지가 필요합니다."
    return {
        "sources": [{"source_id": "SRC-1", "text": text}],
        "requirements": [{"requirement_id": "REQ-1", "text": "예약 페이지를 제공한다.",
                          "origin": "client", "scope": "required",
                          "source_refs": [{"source_id": "SRC-1", "start": 0,
                                           "end": len(text), "quote": text}]}],
        "classifications": [], "questions": [], "customer_confirmations": [],
    }


def internal_requirement():
    return {"requirement_id": "REQ-2", "text": "추천 위젯을 제공한다.", "origin": "internal",
            "scope": "required", "source_refs": []}


def stored_decision(intake, requirement, status="confirmed", customer_id="customer-1"):
    """Synthetic fixture standing in for a future canonical store's record."""
    return {"requirement_id": requirement["requirement_id"],
            "requirement_digest": requirement_digest(requirement),
            "source_snapshot_digest": source_snapshot_digest(intake["sources"]),
            "customer_id": customer_id, "status": status}


class DeliveryIntakeTests(unittest.TestCase):
    def assert_status(self, intake, status, code=None):
        result = evaluate_intake(intake)
        self.assertEqual(result["status"], status)
        if code:
            self.assertIn(code, {reason["code"] for reason in result["reasons"]})
        return result

    def test_source_first_ready_only_for_review_and_inputs_unchanged(self):
        intake = fixture()
        original = copy.deepcopy(intake)
        result = self.assert_status(intake, "READY_FOR_REVIEW")
        self.assertEqual(result["coverage"]["unclassified_characters"], 0)
        self.assertEqual(result["counts"], {"sources": 1, "requirements": 1,
                                           "open_blocking_questions": 0})
        self.assertEqual(intake, original)
        self.assertNotIn("approved", result)
        self.assertNotIn("finalized", result)
        self.assertEqual(result, evaluate_intake(intake))

    def test_sources_must_precede_requirements(self):
        intake = fixture()
        intake["sources"].clear()
        self.assert_status(intake, "HOLD", "source_missing")
        intake["requirements"].clear()
        result = self.assert_status(intake, "HOLD", "requirement_missing")
        self.assertEqual(result["coverage"]["total_characters"], 0)

    def test_registered_source_without_requirement_is_not_ready(self):
        intake = fixture()
        intake["requirements"].clear()
        self.assert_status(intake, "HOLD", "requirement_missing")

    def test_client_requirement_needs_exact_source_reference(self):
        intake = fixture()
        intake["requirements"][0]["source_refs"].clear()
        self.assert_status(intake, "HOLD", "client_source_missing")
        for field, value in (("source_id", "SRC-other"), ("start", 1), ("end", 100),
                             ("quote", "예약 페이지가 좋겠습니다.")):
            with self.subTest(field=field):
                intake = fixture()
                intake["requirements"][0]["source_refs"][0][field] = value
                self.assert_status(intake, "HOLD", "source_reference_invalid")

    def test_source_edit_invalidates_exact_citation(self):
        intake = fixture()
        intake["sources"][0]["text"] = "결제 페이지가 필요합니다."
        self.assert_status(intake, "HOLD", "source_reference_invalid")

    def test_unclassified_spans_hold_even_when_requirements_exist(self):
        intake = fixture()
        intake["sources"][0]["text"] += " 일정은 미정입니다."
        result = self.assert_status(intake, "HOLD", "source_unclassified")
        self.assertEqual(result["coverage"]["unclassified_characters"], len(" 일정은 미정입니다."))
        start = intake["requirements"][0]["source_refs"][0]["end"]
        text = intake["sources"][0]["text"]
        intake["classifications"] = [{"source_id": "SRC-1", "start": start, "end": len(text),
                                       "quote": text[start:], "classification": "context"}]
        self.assert_status(intake, "READY_FOR_REVIEW")

    def test_classifications_require_exact_quotes_too(self):
        intake = fixture()
        ref = copy.deepcopy(intake["requirements"][0]["source_refs"][0])
        intake["classifications"] = [{**ref, "quote": "가짜 인용", "classification": "excluded"}]
        self.assert_status(intake, "HOLD", "source_reference_invalid")

    def test_overlapping_spans_are_counted_once(self):
        intake = fixture()
        ref = intake["requirements"][0]["source_refs"][0]
        intake["requirements"][0]["source_refs"].append(copy.deepcopy(ref))
        intake["classifications"] = [{**ref, "classification": "context"}]
        result = self.assert_status(intake, "READY_FOR_REVIEW")
        self.assertEqual(result["coverage"]["classified_characters"], len(ref["quote"]))

    def test_unicode_offsets_and_whitespace_preserve_source_verbatim(self):
        intake = fixture()
        text = "예약😀\n메모"
        intake["sources"][0]["text"] = text
        intake["requirements"][0]["source_refs"] = [
            {"source_id": "SRC-1", "start": 0, "end": 3, "quote": "예약😀"}]
        intake["classifications"] = [
            {"source_id": "SRC-1", "start": 3, "end": 4, "quote": "\n", "classification": "context"},
            {"source_id": "SRC-1", "start": 4, "end": 6, "quote": "메모", "classification": "context"}]
        result = self.assert_status(intake, "READY_FOR_REVIEW")
        self.assertEqual(result["coverage"]["total_characters"], 6)

    def test_open_blocker_holds_and_questions_do_not_classify_source(self):
        intake = fixture()
        intake["questions"] = [{"question_id": "Q-1", "text": "예약 취소가 필요한가요?",
                                "blocking": True, "status": "open", "source_refs": []}]
        self.assert_status(intake, "HOLD", "blocking_question_open")
        intake["questions"][0]["blocking"] = False
        self.assert_status(intake, "READY_FOR_REVIEW")
        intake["questions"][0].update(blocking=True, status="resolved")
        self.assert_status(intake, "READY_FOR_REVIEW")
        intake["questions"][0]["source_refs"] = intake["requirements"][0]["source_refs"]
        intake["requirements"][0]["source_refs"] = []
        self.assert_status(intake, "HOLD", "source_unclassified")

    def test_internal_proposal_cannot_be_required_without_current_customer_record(self):
        intake = fixture()
        requirement = internal_requirement()
        intake["requirements"].append(requirement)
        self.assert_status(intake, "HOLD", "internal_confirmation_missing")
        requirement["scope"] = "proposed"
        self.assert_status(intake, "READY_FOR_REVIEW")
        requirement["scope"] = "required"
        intake["customer_confirmations"] = [stored_decision(intake, requirement)]
        self.assert_status(intake, "READY_FOR_REVIEW")

    def test_internal_references_do_not_count_as_client_source_classification(self):
        intake = fixture()
        intake["requirements"][0].update(origin="internal", scope="proposed")
        self.assert_status(intake, "HOLD", "source_unclassified")

    def test_requirement_or_source_revision_invalidates_customer_confirmation(self):
        for revision in ("requirement", "source"):
            with self.subTest(revision=revision):
                intake = fixture()
                requirement = internal_requirement()
                intake["requirements"].append(requirement)
                intake["customer_confirmations"] = [stored_decision(intake, requirement)]
                if revision == "requirement":
                    requirement["text"] += " 추가 동작."
                else:
                    intake["sources"].append({"source_id": "SRC-2", "text": "추가 원문"})
                self.assert_status(intake, "HOLD", "internal_confirmation_missing")

    def test_rejected_or_revoked_decision_cannot_be_hidden(self):
        for status in ("rejected", "revoked"):
            with self.subTest(status=status):
                intake = fixture()
                requirement = internal_requirement()
                intake["requirements"].append(requirement)
                intake["customer_confirmations"] = [stored_decision(intake, requirement),
                    stored_decision(intake, requirement, status, "customer-2")]
                self.assert_status(intake, "HOLD", "customer_decision_blocked")

    def test_conflicting_decision_records_fail_closed(self):
        intake = fixture()
        requirement = internal_requirement()
        intake["requirements"].append(requirement)
        intake["customer_confirmations"] = [stored_decision(intake, requirement),
                                             stored_decision(intake, requirement, "rejected")]
        with self.assertRaisesRegex(ValueError, "^invalid_intake_input$"):
            evaluate_intake(intake)

    def test_hashes_bind_exact_id_text_and_requirement_fields(self):
        source = {"source_id": "SRC-1", "text": "원문"}
        digest = source_digest(source)
        self.assertEqual(digest, source_digest(dict(reversed(list(source.items())))))
        self.assertNotEqual(digest, source_digest({**source, "source_id": "SRC-2"}))
        self.assertNotEqual(digest, source_digest({**source, "text": "원문 "}))
        requirement = internal_requirement()
        self.assertNotEqual(requirement_digest(requirement),
                            requirement_digest({**requirement, "scope": "proposed"}))
        sources = [source, {"source_id": "SRC-2", "text": "추가"}]
        self.assertEqual(source_snapshot_digest(sources), source_snapshot_digest(sources[::-1]))

    def test_strict_bounded_schema_rejects_flags_and_malformed_shapes(self):
        cases = [((), {"approved": True}), (("sources", 0), {"registered": True}),
                 (("sources", 0), {"text": "a" * 16385}),
                 (("sources", 0), {"source_id": "x" * 65}),
                 (("sources", 0), {"text": "secret\x00text"}),
                 (("requirements", 0), {"customer_confirmed": True}),
                 (("requirements", 0), {"origin": "AI"}),
                 (("requirements", 0), {"scope": "approved"}),
                 (("requirements", 0, "source_refs", 0), {"start": False}),
                 (("requirements", 0, "source_refs", 0), {"end": -1})]
        for path, changes in cases:
            with self.subTest(path=path, changes=changes):
                intake = fixture()
                target = intake
                for key in path:
                    target = target[key]
                target.update(changes)
                with self.assertRaisesRegex(ValueError, "^invalid_intake_input$"):
                    evaluate_intake(intake)
        for value in (None, [], True):
            with self.assertRaisesRegex(ValueError, "^invalid_intake_input$"):
                evaluate_intake(value)

    def test_duplicate_source_and_requirement_ids_rejected(self):
        for kind in ("sources", "requirements"):
            with self.subTest(kind=kind):
                intake = fixture()
                intake[kind].append(copy.deepcopy(intake[kind][0]))
                with self.assertRaisesRegex(ValueError, "^invalid_intake_input$"):
                    evaluate_intake(intake)

    def test_aggregate_source_size_and_collection_limits(self):
        intake = fixture()
        intake["sources"] = [{"source_id": f"SRC-{i}", "text": "a" * 16384} for i in range(5)]
        with self.assertRaisesRegex(ValueError, "^invalid_intake_input$"):
            evaluate_intake(intake)
        intake = fixture()
        intake["requirements"] *= 65
        with self.assertRaisesRegex(ValueError, "^invalid_intake_input$"):
            evaluate_intake(intake)


if __name__ == "__main__":
    unittest.main()
