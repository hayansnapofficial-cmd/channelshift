"""Bounded, pure delivery-gate calculation; NOT authorization or enforcement.

The calling service must load canonical policy, candidate, stored check results,
reviews, human decisions and prerequisite outcomes from protected authenticated
stores. Worker JSON and caller-supplied identity/role strings are never proof of
authentication. This module cannot verify identities, provenance, artifact bytes
or execution; its hashes provide binding, not a cryptographic trust guarantee.
Supply the current canonical record set, not a history to sort by timestamps.
Do not expose this function as an HTTP/MCP authorization or execution endpoint.

All objects have exact fields; IDs are 1..64 ASCII characters, digests are
lowercase SHA-256 hex. Collections contain at most 64 entries, with at most 512
test records in all checks. Prerequisite gate IDs are predecessor stage IDs.
Prerequisite records bind that stage's candidate digest and the same project/run.
Other records bind all four fields in _BINDING to the current candidate.
"""
from __future__ import annotations

import hashlib
import json
import re

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_BINDING = {"project_id", "run_id", "stage_id", "candidate_digest"}
_POLICY = {"stage_id", "policy_version", "required_artifacts", "required_checks",
           "required_tests", "independent_review", "required_approval_roles",
           "prerequisite_gate_ids"}
_CANDIDATE = {"project_id", "run_id", "stage_id", "author_id", "artifacts",
              "baseline_digest", "test_suite_digest", "policy_digest",
              "environment_digest", "prerequisites"}
_EVIDENCE = {"checks", "reviews", "approvals", "prerequisites"}


def _require(condition):
    if not condition:
        raise ValueError("invalid_gate_input")


def _object(value, fields):
    _require(type(value) is dict and len(value) == len(fields))
    _require(all(type(key) is str for key in value) and value.keys() == fields)


def _string(value, pattern=_ID):
    _require(type(value) is str and len(value) <= 64 and pattern.fullmatch(value) is not None)


def _list(value):
    _require(type(value) is list and len(value) <= 64)


def _ids(value):
    _list(value)
    for item in value:
        _string(item)
    _require(len(set(value)) == len(value))


def _hashes(value):
    _require(type(value) is dict and len(value) <= 64)
    for key, digest in value.items():
        _string(key)
        _string(digest, _HASH)


def _enum(value, choices):
    _require(type(value) is str and len(value) <= 64 and value in choices)


def _integer(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)


def _validate_policy(policy):
    _object(policy, _POLICY)
    for field in ("stage_id", "policy_version"):
        _string(policy[field])
    for field in ("required_artifacts", "required_checks", "required_tests",
                  "required_approval_roles", "prerequisite_gate_ids"):
        _ids(policy[field])
    _require(bool(policy["required_checks"]))
    _require(type(policy["independent_review"]) is bool)
    _require(policy["stage_id"] not in policy["prerequisite_gate_ids"])


def _validate_candidate(candidate):
    _object(candidate, _CANDIDATE)
    for field in ("project_id", "run_id", "stage_id", "author_id"):
        _string(candidate[field])
    for field in ("baseline_digest", "test_suite_digest", "policy_digest", "environment_digest"):
        _string(candidate[field], _HASH)
    for field in ("artifacts", "prerequisites"):
        _hashes(candidate[field])
    _require(candidate["stage_id"] not in candidate["prerequisites"])


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def policy_digest(policy: dict) -> str:
    """Hash validated policy JSON (sorted object keys, array order preserved)."""
    _validate_policy(policy)
    return _digest(policy)


def candidate_digest(manifest: dict) -> str:
    """Hash the complete validated manifest without changing its contents."""
    _validate_candidate(manifest)
    return _digest(manifest)


def _validate_evidence(evidence):
    _object(evidence, _EVIDENCE)
    extra = {"checks": {"check_id", "status", "exit_code", "tests"},
             "reviews": {"reviewer_id", "status", "blocking_count"},
             "approvals": {"human_id", "role", "status"},
             "prerequisites": {"gate_id", "status"}}
    identities = {"checks": ("check_id",), "reviews": ("reviewer_id",),
                  "approvals": ("human_id", "role"), "prerequisites": ("gate_id",)}
    test_count = 0
    for kind in ("checks", "reviews", "approvals", "prerequisites"):
        _list(evidence[kind])
        seen = {}
        for record in evidence[kind]:
            _object(record, _BINDING | extra[kind])
            for field in ("project_id", "run_id", "stage_id", *identities[kind]):
                _string(record[field])
            _string(record["candidate_digest"], _HASH)
            if kind == "checks":
                _enum(record["status"], {"PASS", "FAIL"})
                _integer(record["exit_code"], -2147483648, 2147483647)
                _list(record["tests"])
                test_count += len(record["tests"])
                _require(test_count <= 512)
                test_ids = set()
                for test in record["tests"]:
                    _object(test, {"test_id", "status"})
                    _string(test["test_id"])
                    _enum(test["status"], {"PASS", "FAIL", "SKIP"})
                    _require(test["test_id"] not in test_ids)
                    test_ids.add(test["test_id"])
            elif kind == "reviews":
                _enum(record["status"], {"PASS", "CHANGES_REQUESTED"})
                _integer(record["blocking_count"], 0, 1000000)
            elif kind == "approvals":
                _enum(record["status"], {"approved", "rejected", "revoked"})
            else:
                _enum(record["status"], {"PASS", "HOLD", "NEEDS_FIX"})
            key = tuple(record[field] for field in
                        ("project_id", "run_id", "stage_id", "candidate_digest", *identities[kind]))
            _require(key not in seen or seen[key] == record)
            seen[key] = record


def evaluate_gate(policy: dict, candidate: dict, evidence: dict) -> dict:
    """Return PASS/HOLD/NEEDS_FIX, safe Korean reasons and candidate_digest.

    Missing/stale evidence means HOLD; a current failure means NEEDS_FIX and
    takes precedence. Malformed or conflicting records fail closed with
    ValueError('invalid_gate_input'). An approved flag is never accepted.
    """
    expected_policy = policy_digest(policy)
    digest = candidate_digest(candidate)
    _validate_evidence(evidence)
    reasons = {}
    failures = set()

    def add(code, message, failure=False):
        reasons[code] = {"code": code, "message": message}
        if failure:
            failures.add(code)

    if candidate["stage_id"] != policy["stage_id"]:
        add("stage_mismatch", "현재 단계에 맞는 정책이 필요합니다.")
    if candidate["policy_digest"] != expected_policy:
        add("policy_mismatch", "정책이 변경되어 현재 정책에 따른 새 증거가 필요합니다.")
    if not set(policy["required_artifacts"]) <= candidate["artifacts"].keys():
        add("artifact_missing", "이 단계에 필요한 산출물이 빠져 있습니다.")

    def current(kind):
        return [record for record in evidence[kind]
                if record["candidate_digest"] == digest
                and all(record[field] == candidate[field]
                        for field in ("project_id", "run_id", "stage_id"))]

    checks = current("checks")
    required_checks = set(policy["required_checks"])
    if not required_checks <= {record["check_id"] for record in checks}:
        add("check_missing", "현재 후보에 대한 필수 검사 결과가 필요합니다.")
    for record in checks:
        if record["status"] != "PASS" or record["exit_code"] != 0:
            add("check_failed", "검사가 실패했거나 정상 종료되지 않았습니다.", True)
    tests = [test for record in checks if record["check_id"] in required_checks
             for test in record["tests"]]
    if policy["required_tests"] and checks and not tests:
        add("tests_not_executed", "필수 테스트가 실행되지 않았습니다.", True)
    for record in checks:
        for test in record["tests"]:
            if test["status"] == "FAIL":
                add("test_failed", "실패한 테스트를 수정해야 합니다.", True)
            if test["status"] == "SKIP":
                add("test_skipped", "건너뛴 테스트를 실행해야 합니다.", True)
    if not set(policy["required_tests"]) <= {test["test_id"] for test in tests
                                            if test["status"] == "PASS"}:
        add("test_missing", "현재 후보에 대한 필수 테스트 통과 결과가 필요합니다.")

    reviews = current("reviews")
    if policy["independent_review"]:
        independent = [record for record in reviews
                       if record["reviewer_id"] != candidate["author_id"]]
        if not independent:
            add("review_missing", "작성자와 다른 검토자의 검토가 필요합니다.")
        if any(record["reviewer_id"] == candidate["author_id"] for record in reviews):
            add("self_review", "작성자 본인의 검토는 독립 검토로 인정되지 않습니다.", True)
    for record in reviews:
        if record["status"] != "PASS" or record["blocking_count"] != 0:
            add("review_changes_requested", "검토에서 지적된 차단 문제를 수정해야 합니다.", True)

    approvals = current("approvals")
    for record in approvals:
        if record["status"] == "rejected":
            add("approval_rejected", "현재 후보에 대한 승인이 거절되었습니다.", True)
        if record["status"] == "revoked":
            add("approval_revoked", "승인이 철회되어 새 승인 결정이 필요합니다.")
    approved_roles = {record["role"] for record in approvals if record["status"] == "approved"}
    if not set(policy["required_approval_roles"]) <= approved_roles:
        add("approval_missing", "필요한 역할의 사람이 현재 후보를 승인해야 합니다.")

    for gate_id in policy["prerequisite_gate_ids"]:
        expected = candidate["prerequisites"].get(gate_id)
        records = [record for record in evidence["prerequisites"]
                   if record["gate_id"] == gate_id and record["stage_id"] == gate_id
                   and record["candidate_digest"] == expected
                   and all(record[field] == candidate[field] for field in ("project_id", "run_id"))]
        if not records:
            add("prerequisite_missing", "현재 후보와 연결된 이전 단계의 통과 결과가 필요합니다.")
        for record in records:
            if record["status"] == "HOLD":
                add("prerequisite_hold", "이전 단계가 아직 통과하지 못했습니다.")
            if record["status"] == "NEEDS_FIX":
                add("prerequisite_failed", "이전 단계의 문제를 먼저 수정해야 합니다.", True)

    return {"status": "NEEDS_FIX" if failures else "HOLD" if reasons else "PASS",
            "reasons": [reasons[code] for code in sorted(reasons)],
            "candidate_digest": digest}
