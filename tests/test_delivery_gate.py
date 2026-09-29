"""Gate calculator behavior, binding and bounded-input regression tests."""
import copy
import unittest

from channelshift.delivery_gate import candidate_digest, evaluate_gate, policy_digest


def fixture():
    policy = {
        "stage_id": "release", "policy_version": "v1",
        "required_artifacts": ["site", "readme"], "required_checks": ["suite"],
        "required_tests": ["unit", "browser"], "independent_review": True,
        "required_approval_roles": ["owner"], "prerequisite_gate_ids": ["design"],
    }
    candidate = {
        "project_id": "project-a", "run_id": "run-a", "stage_id": "release",
        "author_id": "worker-a", "artifacts": {"site": "a" * 64, "readme": "b" * 64},
        "baseline_digest": "c" * 64, "test_suite_digest": "d" * 64,
        "policy_digest": policy_digest(policy), "environment_digest": "e" * 64,
        "prerequisites": {"design": "f" * 64},
    }
    binding = {key: candidate[key] for key in ("project_id", "run_id", "stage_id")}
    binding["candidate_digest"] = candidate_digest(candidate)
    evidence = {
        "checks": [{**binding, "check_id": "suite", "status": "PASS", "exit_code": 0,
                    "tests": [{"test_id": "unit", "status": "PASS"},
                              {"test_id": "browser", "status": "PASS"}]}],
        "reviews": [{**binding, "reviewer_id": "reviewer-b", "status": "PASS", "blocking_count": 0}],
        "approvals": [{**binding, "human_id": "human-c", "role": "owner", "status": "approved"}],
        "prerequisites": [{"gate_id": "design", "project_id": "project-a", "run_id": "run-a",
                           "stage_id": "design", "candidate_digest": "f" * 64, "status": "PASS"}],
    }
    return policy, candidate, evidence


def rebind(candidate, evidence):
    for kind in ("checks", "reviews", "approvals"):
        for record in evidence[kind]:
            record.update({key: candidate[key] for key in ("project_id", "run_id", "stage_id")})
            record["candidate_digest"] = candidate_digest(candidate)


class DeliveryGateTests(unittest.TestCase):
    def assert_status(self, args, status, reason=None):
        result = evaluate_gate(*args)
        self.assertEqual(result["status"], status)
        if reason:
            self.assertIn(reason, {item["code"] for item in result["reasons"]})
        return result

    def test_complete_candidate_passes_without_mutating_inputs(self):
        args = fixture()
        original = copy.deepcopy(args)
        result = self.assert_status(args, "PASS")
        self.assertEqual(result, {"status": "PASS", "reasons": [],
                                  "candidate_digest": candidate_digest(args[1])})
        self.assertEqual(args, original)
        self.assertEqual(evaluate_gate(*args), result)

    def test_digest_is_canonical_for_object_key_order(self):
        policy, candidate, _ = fixture()
        reordered = dict(reversed(list(candidate.items())))
        reordered["artifacts"] = dict(reversed(list(candidate["artifacts"].items())))
        self.assertEqual(candidate_digest(candidate), candidate_digest(reordered))
        self.assertEqual(policy_digest(policy), policy_digest(dict(reversed(list(policy.items())))))

    def test_every_manifest_revision_invalidates_old_evidence(self):
        fields = ["project_id", "run_id", "stage_id", "author_id", "artifacts",
                  "baseline_digest", "test_suite_digest", "policy_digest",
                  "environment_digest", "prerequisites"]
        for field in fields:
            with self.subTest(field=field):
                policy, candidate, evidence = fixture()
                previous = candidate_digest(candidate)
                if field == "artifacts":
                    candidate[field]["site"] = "1" * 64
                elif field == "prerequisites":
                    candidate[field]["design"] = "1" * 64
                elif field.endswith("_digest"):
                    candidate[field] = "1" * 64
                else:
                    candidate[field] += "-new"
                self.assertNotEqual(previous, candidate_digest(candidate))
                self.assert_status((policy, candidate, evidence), "HOLD", "check_missing")

    def test_scope_and_digest_must_match_for_each_evidence_type(self):
        reasons = {"checks": "check_missing", "reviews": "review_missing",
                   "approvals": "approval_missing", "prerequisites": "prerequisite_missing"}
        for kind, reason in reasons.items():
            for field in ("project_id", "run_id", "stage_id", "candidate_digest"):
                with self.subTest(kind=kind, field=field):
                    args = fixture()
                    args[2][kind][0][field] = "0" * 64 if field == "candidate_digest" else "another"
                    self.assert_status(args, "HOLD", reason)

    def test_missing_evidence_never_passes(self):
        for kind, reason in [("checks", "check_missing"), ("reviews", "review_missing"),
                             ("approvals", "approval_missing"), ("prerequisites", "prerequisite_missing")]:
            with self.subTest(kind=kind):
                args = fixture()
                args[2][kind] = []
                self.assert_status(args, "HOLD", reason)
        args = fixture()
        for records in args[2].values():
            records.clear()
        self.assert_status(args, "HOLD")

    def test_required_artifact_and_policy_are_checked(self):
        args = fixture()
        del args[1]["artifacts"]["readme"]
        rebind(args[1], args[2])
        self.assert_status(args, "HOLD", "artifact_missing")
        args = fixture()
        args[0]["policy_version"] = "v2"
        self.assert_status(args, "HOLD", "policy_mismatch")

    def test_prerequisite_revision_requires_new_predecessor_result(self):
        args = fixture()
        args[1]["prerequisites"]["design"] = "1" * 64
        rebind(args[1], args[2])
        self.assert_status(args, "HOLD", "prerequisite_missing")
        args[2]["prerequisites"][0]["candidate_digest"] = "1" * 64
        self.assert_status(args, "PASS")
        args[1]["prerequisites"].clear()
        rebind(args[1], args[2])
        self.assert_status(args, "HOLD", "prerequisite_missing")

    def test_predecessor_failure_and_hold_are_not_pass(self):
        for status in ("HOLD", "NEEDS_FIX"):
            with self.subTest(status=status):
                args = fixture()
                args[2]["prerequisites"][0]["status"] = status
                self.assert_status(args, status)

    def test_no_tests_and_missing_required_test(self):
        args = fixture()
        args[2]["checks"][0]["tests"] = []
        self.assert_status(args, "NEEDS_FIX", "tests_not_executed")
        args = fixture()
        args[2]["checks"][0]["tests"].pop()
        self.assert_status(args, "HOLD", "test_missing")

    def test_failed_skipped_or_abnormally_exited_checks_need_fix(self):
        for test_status in ("FAIL", "SKIP"):
            with self.subTest(test_status=test_status):
                args = fixture()
                args[2]["checks"][0]["tests"][0]["status"] = test_status
                self.assert_status(args, "NEEDS_FIX")
        for field, value in (("status", "FAIL"), ("exit_code", 1), ("exit_code", -9)):
            with self.subTest(field=field, value=value):
                args = fixture()
                args[2]["checks"][0][field] = value
                self.assert_status(args, "NEEDS_FIX", "check_failed")

    def test_required_tests_must_come_from_required_checks(self):
        args = fixture()
        args[2]["checks"][0]["check_id"] = "unrelated"
        self.assert_status(args, "NEEDS_FIX", "check_missing")

    def test_pass_cannot_hide_a_different_current_failure(self):
        args = fixture()
        extra = copy.deepcopy(args[2]["checks"][0])
        extra.update(check_id="extra", status="FAIL")
        args[2]["checks"].append(extra)
        self.assert_status(args, "NEEDS_FIX", "check_failed")
        extra["status"] = "PASS"
        extra["tests"][0]["status"] = "FAIL"
        self.assert_status(args, "NEEDS_FIX", "test_failed")

    def test_independent_review_and_blocking_findings(self):
        args = fixture()
        args[2]["reviews"][0]["reviewer_id"] = args[1]["author_id"]
        self.assert_status(args, "NEEDS_FIX", "self_review")
        for field, value in (("status", "CHANGES_REQUESTED"), ("blocking_count", 1)):
            with self.subTest(field=field):
                args = fixture()
                args[2]["reviews"][0][field] = value
                self.assert_status(args, "NEEDS_FIX", "review_changes_requested")

    def test_rejection_and_revocation_cannot_be_hidden_by_another_approval(self):
        for status, expected in (("rejected", "NEEDS_FIX"), ("revoked", "HOLD")):
            with self.subTest(status=status):
                args = fixture()
                extra = copy.deepcopy(args[2]["approvals"][0])
                extra.update(human_id="human-d", status=status)
                args[2]["approvals"].append(extra)
                self.assert_status(args, expected)

    def test_approval_role_must_match_and_author_is_not_implicitly_barred(self):
        args = fixture()
        args[2]["approvals"][0]["role"] = "observer"
        self.assert_status(args, "HOLD", "approval_missing")
        args = fixture()
        args[2]["approvals"][0]["human_id"] = args[1]["author_id"]
        self.assert_status(args, "PASS")

    def test_conflicting_canonical_records_fail_closed(self):
        for kind, status in (("checks", "FAIL"), ("reviews", "CHANGES_REQUESTED"),
                             ("approvals", "rejected"), ("approvals", "revoked"),
                             ("prerequisites", "NEEDS_FIX")):
            with self.subTest(kind=kind, status=status):
                args = fixture()
                conflict = copy.deepcopy(args[2][kind][0])
                conflict["status"] = status
                args[2][kind].append(conflict)
                with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
                    evaluate_gate(*args)

    def test_identical_duplicates_and_record_order_are_deterministic(self):
        args = fixture()
        for records in args[2].values():
            records.append(copy.deepcopy(records[0]))
        self.assert_status(args, "PASS")
        args[2]["checks"][0]["status"] = args[2]["checks"][1]["status"] = "FAIL"
        args[2]["approvals"][0]["status"] = args[2]["approvals"][1]["status"] = "revoked"
        result = evaluate_gate(*args)
        for records in args[2].values():
            records.reverse()
        self.assertEqual(result, evaluate_gate(*args))

    def test_changed_policy_can_remove_review_but_not_checks(self):
        args = fixture()
        args[0].update(independent_review=False, required_tests=[], required_approval_roles=[],
                       prerequisite_gate_ids=[])
        args[1]["policy_digest"] = policy_digest(args[0])
        args[2]["reviews"].clear()
        args[2]["approvals"].clear()
        args[2]["prerequisites"].clear()
        args[2]["checks"][0]["tests"].clear()
        rebind(args[1], args[2])
        self.assert_status(args, "PASS")
        args[0]["required_checks"].clear()
        with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
            evaluate_gate(*args)

    def test_invalid_types_flags_and_unbounded_values_are_rejected(self):
        cases = [
            (0, (), {"approved": True}),
            (1, (), {"approved": True}),
            (2, (), {"approved": True}),
            (0, (), {"independent_review": 1}),
            (0, (), {"required_checks": []}),
            (0, (), {"required_tests": ["unit", "unit"]}),
            (0, (), {"required_tests": ["test"] * 65}),
            (1, (), {"project_id": "secret\nvalue"}),
            (1, (), {"project_id": "a" * 65}),
            (1, (), {"environment_digest": "A" * 64}),
            (1, (), {"artifacts": {"site": True}}),
            (1, (), {"prerequisites": {"design": True}}),
            (2, ("checks", 0), {"approved": True}),
            (2, ("checks", 0), {"exit_code": False}),
            (2, ("checks", 0), {"exit_code": 0.0}),
            (2, ("checks", 0), {"status": "success"}),
            (2, ("checks", 0), {"tests": [True]}),
            (2, ("reviews", 0), {"blocking_count": False}),
            (2, ("reviews", 0), {"status": "APPROVED"}),
            (2, ("approvals", 0), {"authenticated": True}),
            (2, ("approvals", 0), {"role": ["owner"]}),
            (2, ("prerequisites", 0), {"status": True}),
        ]
        for index, path, changes in cases:
            with self.subTest(index=index, path=path, changes=changes):
                args = fixture()
                target = args[index]
                for part in path:
                    target = target[part]
                target.update(changes)
                with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
                    evaluate_gate(*args)

    def test_missing_top_level_fields_and_nonobjects_fail_closed(self):
        for index in range(3):
            args = list(fixture())
            args[index] = None
            with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
                evaluate_gate(*args)
            args = list(fixture())
            args[index].pop(next(iter(args[index])))
            with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
                evaluate_gate(*args)

    def test_nested_test_limit_and_duplicate_test_ids_fail_closed(self):
        args = fixture()
        args[2]["checks"][0]["tests"] *= 2
        with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
            evaluate_gate(*args)
        args = fixture()
        check = args[2]["checks"][0]
        args[2]["checks"] = [{**check, "check_id": f"check-{i}",
                              "tests": [{"test_id": f"test-{j}", "status": "PASS"} for j in range(64)]}
                             for i in range(9)]
        with self.assertRaisesRegex(ValueError, "^invalid_gate_input$"):
            evaluate_gate(*args)

    def test_failure_takes_precedence_over_missing_evidence(self):
        args = fixture()
        args[2]["approvals"].clear()
        args[2]["checks"][0]["status"] = "FAIL"
        result = self.assert_status(args, "NEEDS_FIX", "approval_missing")
        self.assertIn("check_failed", {reason["code"] for reason in result["reasons"]})


if __name__ == "__main__":
    unittest.main()
