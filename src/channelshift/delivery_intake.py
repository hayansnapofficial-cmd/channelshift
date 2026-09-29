"""Pure B0 source-first intake checks, not final approval or authentication.

The caller must obtain original sources, classifications, question resolutions
and customer decisions from canonical protected stores. Identity strings and
decision fields supplied by a worker are not evidence of customer authorization.
No store, append-only enforcement, source registration, runtime or I/O is
implemented here. Persisting original source revisions without overwriting them
is the future service's responsibility. Hashes bind content, not provenance.

Coverage counts exact source character positions covered by client requirement
citations or explicit context/excluded classifications. It cannot prove semantic
completeness, correct interpretation or that an excluded passage is expendable.
READY_FOR_REVIEW only means these mechanical prerequisites are present; it is
never final customer approval. Indices are Python Unicode code-point offsets,
start inclusive and end exclusive. Quotes must match without normalization.
"""
from __future__ import annotations

import hashlib
import json
import re

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_REF = {"source_id", "start", "end", "quote"}
_REQ = {"requirement_id", "text", "origin", "scope", "source_refs"}
_INPUT = {"sources", "requirements", "classifications", "questions", "customer_confirmations"}


def _require(condition):
    if not condition:
        raise ValueError("invalid_intake_input")


def _object(value, fields):
    _require(type(value) is dict and len(value) == len(fields))
    _require(all(type(key) is str for key in value) and value.keys() == fields)


def _identifier(value, pattern=_ID):
    _require(type(value) is str and len(value) <= 64 and pattern.fullmatch(value) is not None)


def _text(value, maximum):
    _require(type(value) is str and 0 < len(value) <= maximum)
    _require(bool(value.strip()) and "\x00" not in value
             and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def _list(value, maximum=64):
    _require(type(value) is list and len(value) <= maximum)


def _enum(value, choices):
    _require(type(value) is str and len(value) <= 32 and value in choices)


def _source(source):
    _object(source, {"source_id", "text"})
    _identifier(source["source_id"])
    _text(source["text"], 16384)


def _sources(sources):
    _list(sources, 32)
    seen = set()
    total = 0
    for source in sources:
        _source(source)
        _require(source["source_id"] not in seen)
        seen.add(source["source_id"])
        total += len(source["text"])
        _require(total <= 65536)


def _reference(ref, classification=False):
    _object(ref, _REF | ({"classification"} if classification else set()))
    _identifier(ref["source_id"])
    for field in ("start", "end"):
        _require(type(ref[field]) is int and 0 <= ref[field] <= 16384)
    _require(ref["start"] < ref["end"])
    # Whitespace is meaningful source content and may be explicitly classified.
    quote = ref["quote"]
    _require(type(quote) is str and 0 < len(quote) <= 16384 and "\x00" not in quote
             and not any(0xD800 <= ord(char) <= 0xDFFF for char in quote))
    if classification:
        _enum(ref["classification"], {"context", "excluded"})


def _references(refs):
    _list(refs, 32)
    for ref in refs:
        _reference(ref)


def _requirement(requirement):
    _object(requirement, _REQ)
    _identifier(requirement["requirement_id"])
    _text(requirement["text"], 4096)
    _enum(requirement["origin"], {"client", "internal"})
    _enum(requirement["scope"], {"proposed", "required", "excluded"})
    _references(requirement["source_refs"])


def _digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def source_digest(source: dict) -> str:
    """Hash an exact source ID and its unnormalized text; no source is saved."""
    _source(source)
    return _digest(source)


def source_snapshot_digest(sources: list) -> str:
    """Hash validated source content, independent of source list ordering."""
    _sources(sources)
    return _digest(sorted(sources, key=lambda source: source["source_id"]))


def requirement_digest(requirement: dict) -> str:
    """Bind a customer decision to one complete requirement candidate."""
    _requirement(requirement)
    return _digest(requirement)


def _validate(intake):
    _object(intake, _INPUT)
    _sources(intake["sources"])
    for kind in ("requirements", "classifications", "questions", "customer_confirmations"):
        _list(intake[kind])
    seen = set()
    refs_count = len(intake["classifications"])
    for requirement in intake["requirements"]:
        _requirement(requirement)
        _require(requirement["requirement_id"] not in seen)
        seen.add(requirement["requirement_id"])
        refs_count += len(requirement["source_refs"])
    for classification in intake["classifications"]:
        _reference(classification, classification=True)
    seen = set()
    for question in intake["questions"]:
        _object(question, {"question_id", "text", "blocking", "status", "source_refs"})
        _identifier(question["question_id"])
        _text(question["text"], 2048)
        _require(type(question["blocking"]) is bool)
        _enum(question["status"], {"open", "resolved"})
        _references(question["source_refs"])
        _require(question["question_id"] not in seen)
        seen.add(question["question_id"])
        refs_count += len(question["source_refs"])
    _require(refs_count <= 512)
    seen = {}
    for decision in intake["customer_confirmations"]:
        _object(decision, {"requirement_id", "requirement_digest", "source_snapshot_digest",
                           "customer_id", "status"})
        for field in ("requirement_id", "customer_id"):
            _identifier(decision[field])
        for field in ("requirement_digest", "source_snapshot_digest"):
            _identifier(decision[field], _HASH)
        _enum(decision["status"], {"confirmed", "rejected", "revoked"})
        key = tuple(decision[field] for field in ("requirement_id", "requirement_digest",
                                                  "source_snapshot_digest", "customer_id"))
        _require(key not in seen or seen[key] == decision)
        seen[key] = decision


def evaluate_intake(intake: dict) -> dict:
    """Check source-first planning prerequisites without approving any plan."""
    _validate(intake)
    snapshot = source_snapshot_digest(intake["sources"])
    sources = {source["source_id"]: source["text"] for source in intake["sources"]}
    coverage = {source_id: bytearray(len(text)) for source_id, text in sources.items()}
    reasons = {}

    def add(code, message):
        reasons[code] = {"code": code, "message": message}

    def reference(ref, classify=False):
        text = sources.get(ref["source_id"])
        valid = (text is not None and ref["end"] <= len(text)
                 and text[ref["start"]:ref["end"]] == ref["quote"])
        if not valid:
            add("source_reference_invalid", "원문 인용의 문서와 위치, 문구가 일치해야 합니다.")
        elif classify:
            coverage[ref["source_id"]][ref["start"]:ref["end"]] = b"\x01" * (ref["end"] - ref["start"])

    if not sources:
        add("source_missing", "고객이 전달한 원문을 먼저 등록해야 합니다.")
    if not intake["requirements"]:
        add("requirement_missing", "원문에 근거한 요구사항 후보를 작성해야 합니다.")
    for requirement in intake["requirements"]:
        client = requirement["origin"] == "client"
        if client and not requirement["source_refs"]:
            add("client_source_missing", "고객 요구사항에는 정확한 원문 인용이 필요합니다.")
        for ref in requirement["source_refs"]:
            reference(ref, classify=client)
        digest = requirement_digest(requirement)
        decisions = [decision for decision in intake["customer_confirmations"]
                     if decision["requirement_id"] == requirement["requirement_id"]
                     and decision["requirement_digest"] == digest
                     and decision["source_snapshot_digest"] == snapshot]
        if any(decision["status"] != "confirmed" for decision in decisions):
            add("customer_decision_blocked", "거절되거나 철회된 고객 결정을 다시 확인해야 합니다.")
        if not client and requirement["scope"] == "required" and not decisions:
            add("internal_confirmation_missing", "내부 제안을 필수 요구사항으로 삼으려면 고객 확인이 필요합니다.")
    for classification in intake["classifications"]:
        reference(classification, classify=True)
    open_blocking = 0
    for question in intake["questions"]:
        for ref in question["source_refs"]:
            reference(ref)
        if question["blocking"] and question["status"] == "open":
            open_blocking += 1
    if open_blocking:
        add("blocking_question_open", "진행을 막는 미해결 질문을 먼저 확인해야 합니다.")
    total = sum(len(text) for text in sources.values())
    classified = sum(sum(positions) for positions in coverage.values())
    if classified < total:
        add("source_unclassified", "요구사항이나 참고·제외 항목으로 분류하지 않은 원문 구간이 있습니다.")
    return {"status": "HOLD" if reasons else "READY_FOR_REVIEW",
            "reasons": [reasons[code] for code in sorted(reasons)],
            "source_snapshot_digest": snapshot,
            "coverage": {"total_characters": total, "classified_characters": classified,
                         "unclassified_characters": total - classified},
            "counts": {"sources": len(sources), "requirements": len(intake["requirements"]),
                       "open_blocking_questions": open_blocking}}
