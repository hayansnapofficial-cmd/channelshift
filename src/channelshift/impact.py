"""Pure, revision-bound impact traversal over explicitly registered links.

Native entity and attribute names are their identifiers; the native model has
no UUID table/field IDs. Graph edges point from a source to its dependent.
``covered_kinds`` is the graph supplier's coverage declaration, not proof that
all application code was inspected. Undeclared category totals remain unknown;
``observed_counts`` reports only the links actually present in a current graph.

Cycles are allowed. Each reachable artifact appears once with a deterministic
shortest path. STALE is only a suggested downstream status; this module changes
no records, approvals, files, or services.
"""
from __future__ import annotations

from collections import deque
import copy
import hashlib
import json
import re

from .core import IDENTIFIER, validate_schema

GRAPH_FORMAT = "channelshift.traceability/v1"
IMPACT_FORMAT = "channelshift.impact/v1"
IMPACT_KINDS = ("api", "backend", "screen", "test")
MAX_GRAPH_BYTES = 131072
MAX_NODES = 512
MAX_EDGES = 2048
MAX_PATH_STEPS = 8192
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\Z", re.ASCII)
_HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)


def schema_digest(model):
    """SHA-256 of validated canonical native JSON, matching ProjectStore IDs."""
    if type(model) is not dict or not validate_schema(model)["valid"]:
        raise ValueError("invalid_schema")
    try:
        encoded = json.dumps(model, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid_schema") from None
    return hashlib.sha256(encoded).hexdigest()


def field_node_id(table_id, field_id):
    """Map a native entity/attribute name pair to its unambiguous graph ID."""
    if any(type(value) is not str or IDENTIFIER.fullmatch(value) is None
           for value in (table_id, field_id)):
        raise ValueError("invalid_impact_field")
    return "field:" + table_id + ":" + field_id


def _require(condition):
    if not condition:
        raise ValueError("invalid_impact_graph")


def _node_id(value):
    return type(value) is str and len(value) <= 160 and _ID.fullmatch(value) is not None


def _label(value):
    if type(value) is not str or not 1 <= len(value) <= 300 or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return not any(ord(char) < 32 for char in value)


def validate_graph(graph):
    """Return a copy of a bounded exact-shape graph, or a constant ValueError.

    Structural validity is independent of revision binding. Live field
    references are checked by analyze_impact only after the digest matches.
    """
    required = {"format", "schema_digest", "nodes", "edges"}
    _require(type(graph) is dict and required <= graph.keys() <= required | {"covered_kinds"})
    _require(type(graph["format"]) is str and graph["format"] == GRAPH_FORMAT)
    _require(type(graph["schema_digest"]) is str and _HASH.fullmatch(graph["schema_digest"]) is not None)
    _require(type(graph["nodes"]) is list and len(graph["nodes"]) <= MAX_NODES)
    _require(type(graph["edges"]) is list and len(graph["edges"]) <= MAX_EDGES)
    covered = graph.get("covered_kinds", [])
    _require(type(covered) is list and len(covered) <= len(IMPACT_KINDS))
    _require(all(type(kind) is str and kind in IMPACT_KINDS for kind in covered))
    _require(len(set(covered)) == len(covered))
    ids = set()
    for node in graph["nodes"]:
        _require(type(node) is dict and node.keys() == {"id", "kind", "label"})
        _require(_node_id(node["id"]) and node["id"] not in ids)
        _require(type(node["kind"]) is str and node["kind"] in (*IMPACT_KINDS, "field"))
        _require(_label(node["label"]))
        if node["kind"] == "field":
            parts = node["id"].split(":")
            _require(len(parts) == 3 and parts[0] == "field"
                     and all(IDENTIFIER.fullmatch(part) is not None for part in parts[1:]))
        else:
            _require(not node["id"].startswith("field:"))
        ids.add(node["id"])
    edges = set()
    for edge in graph["edges"]:
        _require(type(edge) is dict and edge.keys() == {"from", "to"})
        _require(_node_id(edge["from"]) and _node_id(edge["to"]))
        pair = (edge["from"], edge["to"])
        _require(edge["from"] in ids and edge["to"] in ids and pair not in edges)
        edges.add(pair)
    encoded = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _require(len(encoded) <= MAX_GRAPH_BYTES)
    return copy.deepcopy(graph)


def analyze_impact(schema, table_id, field_id, graph=None):
    """Inspect a field's dependents in this exact schema revision.

    UNKNOWN: missing graph, obsolete revision, or unregistered selected field.
    PARTIAL: a current graph without all four coverage declarations.
    LINKED: a current graph with all four declared categories, including any
    declared zero counts. Neither LINKED nor a zero certifies code completeness.
    Invalid native fields/graphs raise a safe constant ValueError instead.
    """
    digest = schema_digest(schema)
    source = field_node_id(table_id, field_id)
    native_fields = {field_node_id(entity["name"], attribute["name"])
                     for entity in schema["entities"] for attribute in entity["attributes"]}
    if source not in native_fields:
        raise ValueError("invalid_impact_field")
    result = {
        "format": IMPACT_FORMAT, "status": "UNKNOWN", "reason": "graph_missing",
        "schema_digest": digest, "source_field_id": source,
        "counts": {kind: None for kind in IMPACT_KINDS},
        "observed_counts": {kind: None for kind in IMPACT_KINDS},
        "covered_kinds": [], "direct": [], "indirect": [], "impacted_ids": [],
        "suggested_status": None, "basis": "registered_traceability",
    }
    if graph is None:
        return result
    current = validate_graph(graph)
    if current["schema_digest"] != digest:
        result["reason"] = "schema_revision_mismatch"
        return result
    nodes = {node["id"]: node for node in current["nodes"]}
    _require(all(node["id"] in native_fields for node in current["nodes"] if node["kind"] == "field"))
    if source not in nodes:
        result["reason"] = "field_node_missing"
        return result
    adjacency = {node_id: [] for node_id in nodes}
    for edge in current["edges"]:
        adjacency[edge["from"]].append(edge["to"])
    for dependents in adjacency.values():
        dependents.sort()
    predecessors = {source: None}
    queue = deque([source])
    while queue:
        node_id = queue.popleft()
        for dependent in adjacency[node_id]:
            if dependent not in predecessors:
                predecessors[dependent] = node_id
                queue.append(dependent)
    counts = {kind: 0 for kind in IMPACT_KINDS}
    path_steps = 0
    for node_id in sorted(predecessors):
        node = nodes[node_id]
        if node["kind"] == "field":
            continue
        path = []
        cursor = node_id
        while cursor is not None:
            path.append(cursor)
            cursor = predecessors[cursor]
        path.reverse()
        path_steps += len(path)
        if path_steps > MAX_PATH_STEPS:
            raise ValueError("impact_graph_too_complex")
        item = {**node, "path": path, "suggested_status": "STALE"}
        result["direct" if len(path) == 2 else "indirect"].append(item)
        result["impacted_ids"].append(node_id)
        counts[node["kind"]] += 1
    covered = set(current.get("covered_kinds", []))
    result["covered_kinds"] = [kind for kind in IMPACT_KINDS if kind in covered]
    result["observed_counts"] = counts
    result["counts"] = {kind: counts[kind] if kind in covered else None for kind in IMPACT_KINDS}
    result["status"] = "LINKED" if len(covered) == len(IMPACT_KINDS) else "PARTIAL"
    result["reason"] = "registered_graph" if result["status"] == "LINKED" else "coverage_incomplete"
    result["suggested_status"] = "STALE" if result["impacted_ids"] else None
    return result
