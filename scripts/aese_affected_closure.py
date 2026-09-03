"""Build a deterministic, fail-closed AESE affected-test closure.

The closure is an evidence/planning artifact only.  It does not execute a
test, skip a test, promote evidence, or replace the retained legacy runner.
Edges are deliberately explicit and typed; an unknown path or dynamic edge
widens to every retained inventory item.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Final, cast

if __package__:
    from scripts import aese_claim_graph as _claim_graph
    from scripts import aese_inventory as _inventory
else:  # Direct ``python scripts/aese_affected_closure.py`` from repository root.
    import aese_claim_graph as _claim_graph
    import aese_inventory as _inventory

ROOT = _inventory.ROOT
DEFAULT_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_affected_closure.json"
DEFAULT_MAPPING: Final[Path] = ROOT / "quality" / "registry" / "current_s2_mapping.json"
SCHEMA: Final[str] = "aese-affected-closure-v1"
PHASE: Final[str] = "PHASE_3_DETERMINISTIC_AFFECTED_CLOSURE"
MODE: Final[str] = "SHADOW"
UNKNOWN_POLICY: Final[str] = "WIDEN_TO_RETAINED_SUITE"
CRITICAL_VALUES: Final[frozenset[str]] = frozenset({"CRITICAL", "HIGH", "CRITICAL_NOW", "RELEASE_CRITICAL"})
EDGE_TYPES: Final[tuple[str, ...]] = (
    "IMPORT",
    "CALL",
    "FFI",
    "SERIALIZATION",
    "CONFIG",
    "SCHEMA",
    "PACKAGE",
    "ENTRY_POINT",
    "CARGO_FEATURE",
    "WORKFLOW",
    "GENERATOR",
    "VALIDATOR",
    "CLAIM",
    "TEST",
)
_DYNAMIC_MARKERS: Final[tuple[str, ...]] = (
    "importlib.import_module",
    "importlib.util",
    "from importlib import",
    "import_module(",
    "__import__(",
    "dlopen(",
    "libloading::",
    "eval(",
    "exec(",
)


def _normalize(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _subject_path(subject: str) -> str:
    return _normalize(subject.split("::", 1)[0])


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _git_ref(ref: str) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "NOT_AVAILABLE"


def _load_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if type(raw) is not dict:
        raise ValueError(f"{path} must contain a canonical object")
    return cast(dict[str, object], raw)


def _edge(from_path: str, to_path: str, edge_type: str, evidence: str) -> dict[str, str]:
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"unsupported edge type: {edge_type}")
    return {
        "from": _normalize(from_path),
        "to": _normalize(to_path),
        "type": edge_type,
        "evidence": evidence,
    }


def _mapping_edges(mapping: dict[str, object]) -> list[dict[str, str]]:
    records = mapping.get("records")
    if type(records) is not list:
        raise ValueError("S2 mapping records must be a list")
    edges: list[dict[str, str]] = []
    for raw_record in records:
        if type(raw_record) is not dict:
            raise ValueError("S2 mapping records must be objects")
        record = cast(dict[str, object], raw_record)
        sources = record.get("source_subjects")
        tests = record.get("test_subjects")
        surface_id = str(record.get("surface_id", "UNKNOWN_SURFACE"))
        if type(sources) is not list or type(tests) is not list or not sources or not tests:
            raise ValueError(f"mapping record {surface_id} lacks concrete source/test subjects")
        source_paths = sorted({_subject_path(str(item)) for item in sources})
        test_paths = sorted({_subject_path(str(item)) for item in tests})
        for source_path in source_paths:
            for test_path in test_paths:
                edges.append(_edge(source_path, test_path, "TEST", f"S2:{surface_id}"))
                if source_path != test_path:
                    edges.append(_edge(test_path, source_path, "CLAIM", f"S2:{surface_id}:reverse"))
    return edges


def _declared_edges(mapping: dict[str, object]) -> list[dict[str, str]]:
    """Return only edges backed by concrete repository paths or S2 subjects."""

    edges = _mapping_edges(mapping)
    # These are intentionally small, path-level seams.  Their evidence anchors
    # are kept human-auditable so a filename-only coincidence cannot create a
    # closure edge.
    edges.extend(
        [
            _edge("aegis_cognition/__init__.py", "aegis_cognition/aese.py", "IMPORT", "from .aese import"),
            _edge("aegis_cognition/aese.py", "core/rust/src/ffi/eac.rs", "FFI", "ffi_latency_ns"),
            _edge("aegis_cognition/aese.py", "core/rust/src/ffi/runtime.rs", "FFI", "aegis_resource_usage_sample"),
            _edge("core/rust/src/ffi.rs", "core/rust/src/ffi/eac.rs", "IMPORT", "pub mod eac"),
            _edge("core/rust/src/ffi.rs", "core/rust/src/ffi/runtime.rs", "IMPORT", "pub mod runtime"),
            _edge("core/rust/src/replay.rs", "core/rust/src/schema.rs", "SERIALIZATION", "serde::"),
            _edge("core/rust/src/schema.rs", "core/rust/src/replay.rs", "SCHEMA", "serde::Serialize"),
            _edge("core/rust/Cargo.toml", "core/rust/src/lib.rs", "CARGO_FEATURE", "[features]"),
            _edge("Cargo.toml", "core/rust/Cargo.toml", "PACKAGE", "core/rust"),
            _edge("pyproject.toml", "aegis_cognition/aese.py", "PACKAGE", "aegis-cognition"),
            _edge("pyproject.toml", "tests/test_aese_primitives.py", "CONFIG", "[tool.pytest"),
            _edge("core/rust/src/main.rs", "core/rust/src/cli/mod.rs", "ENTRY_POINT", "fn main"),
            _edge("scripts/aese_inventory.py", "quality/registry/current_inventory.json", "GENERATOR", "current_inventory.json"),
            _edge("scripts/aese_claim_graph.py", "quality/registry/current_claim_graph.json", "GENERATOR", "current_claim_graph.json"),
            _edge("scripts/aese_s2_mapping.py", "quality/registry/current_s2_mapping.json", "GENERATOR", "current_s2_mapping.json"),
            _edge("scripts/aese_inventory.py", "quality/registry/current_inventory.json", "VALIDATOR", "validate_inventory"),
            _edge("scripts/aese_claim_graph.py", "quality/registry/current_claim_graph.json", "VALIDATOR", "validate_graph"),
            _edge("scripts/aese_preflight.py", "quality/registry/current_inventory.json", "VALIDATOR", "build_inventory"),
            _edge("scripts/aese_preflight.py", "quality/registry/current_claim_graph.json", "VALIDATOR", "build_graph"),
            _edge("scripts/evidence_consistency_gate.py", "quality/registry/current_inventory.json", "VALIDATOR", "registry"),
            _edge("scripts/aese_statistical_calibration.py", "tests/test_aese_statistical_calibration.py", "TEST", "run_calibration"),
            _edge(".github/workflows/ci.yml", "tests/test_aese_primitives.py", "WORKFLOW", "pytest tests"),
            _edge(".github/workflows/ci.yml", "core/rust/src/gt96.rs", "WORKFLOW", "cargo test"),
            _edge("scripts/aese_preflight.py", "scripts/aese_claim_graph.py", "CALL", "build_graph()"),
            _edge("scripts/aese_preflight.py", "scripts/aese_inventory.py", "CALL", "build_inventory()"),
        ]
    )
    deduplicated = {(edge["from"], edge["to"], edge["type"], edge["evidence"]): edge for edge in edges}
    return [deduplicated[key] for key in sorted(deduplicated)]


def _path_set(inventory: dict[str, object], graph: dict[str, object], edges: list[dict[str, str]]) -> tuple[set[str], set[str]]:
    known: set[str] = set()
    retained: set[str] = set()
    items = inventory.get("items", [])
    if type(items) is list:
        for raw_item in items:
            if type(raw_item) is dict:
                item = cast(dict[str, object], raw_item)
                path = _normalize(str(item.get("path", "")))
                if path:
                    known.add(path)
                    retained.add(path)
    for section in ("code", "surfaces"):
        values = graph.get(section, [])
        if type(values) is list:
            for raw_value in values:
                if type(raw_value) is dict:
                    path = _normalize(str(cast(dict[str, object], raw_value).get("path", "")))
                    if path:
                        known.add(path)
    for edge in edges:
        for key in ("from", "to"):
            path = _normalize(edge[key])
            if (ROOT / path).exists():
                known.add(path)
    return known, retained


def _dynamic_edge_paths(paths: list[str]) -> list[str]:
    dynamic: list[str] = []
    for path in paths:
        absolute = ROOT / path
        if not absolute.is_file():
            continue
        try:
            text = absolute.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            dynamic.append(path)
            continue
        if any(marker in text for marker in _DYNAMIC_MARKERS):
            dynamic.append(path)
    return sorted(dynamic)


def _adjacency(edges: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    adjacency: dict[str, list[dict[str, str]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge)
    return {path: sorted(values, key=lambda item: (item["to"], item["type"], item["evidence"])) for path, values in adjacency.items()}


def _reach(seed_paths: list[str], adjacency: dict[str, list[dict[str, str]]]) -> tuple[list[str], list[dict[str, str]]]:
    visited = set(seed_paths)
    queue: deque[str] = deque(seed_paths)
    traversed: list[dict[str, str]] = []
    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, []):
            traversed.append(edge)
            target = edge["to"]
            if target not in visited:
                visited.add(target)
                queue.append(target)
    return sorted(visited), sorted(traversed, key=lambda item: (item["from"], item["to"], item["type"], item["evidence"]))


def _critical_audit(mapping: dict[str, object], adjacency: dict[str, list[dict[str, str]]]) -> dict[str, object]:
    records = mapping.get("records", [])
    checked: list[str] = []
    false_negatives: list[dict[str, object]] = []
    if type(records) is not list:
        return {"status": "NOT_EVALUATED_INVALID_MAPPING", "checked_records": [], "false_negatives": []}
    for raw_record in records:
        if type(raw_record) is not dict:
            continue
        record = cast(dict[str, object], raw_record)
        values = {str(record.get(key, "UNKNOWN")).upper() for key in ("risk", "security_criticality", "release_criticality")}
        if not values.intersection(CRITICAL_VALUES):
            continue
        surface_id = str(record.get("surface_id", "UNKNOWN_SURFACE"))
        checked.append(surface_id)
        sources = record.get("source_subjects", [])
        tests = record.get("test_subjects", [])
        source_paths = sorted({_subject_path(str(item)) for item in sources}) if isinstance(sources, list) else []
        test_paths = sorted({_subject_path(str(item)) for item in tests}) if isinstance(tests, list) else []
        reachable: set[str] = set()
        for source_path in source_paths:
            reached, _ = _reach([source_path], adjacency)
            reachable.update(reached)
        missing = sorted(set(test_paths) - reachable)
        if missing:
            false_negatives.append({"surface_id": surface_id, "missing_test_paths": missing})
    return {
        "status": "COMPLETE_ZERO" if not false_negatives else "CRITICAL_FALSE_NEGATIVE",
        "checked_records": sorted(checked),
        "false_negatives": false_negatives,
    }


def build_closure(changed_paths: list[str] | tuple[str, ...] = (), mapping_path: Path = DEFAULT_MAPPING) -> dict[str, object]:
    inventory = _inventory.build_inventory()
    graph = _claim_graph.build_graph()
    mapping = _load_json(mapping_path)
    edges = _declared_edges(mapping)
    known_paths, retained_paths = _path_set(inventory, graph, edges)
    paths = sorted({_normalize(path) for path in changed_paths if path.strip()})
    unknown_paths = sorted(path for path in paths if path not in known_paths)
    dynamic_paths = _dynamic_edge_paths(paths)
    adjacency = _adjacency(edges)
    reached_paths, traversed_edges = _reach(paths, adjacency)
    widened = bool(unknown_paths or dynamic_paths or not paths)
    if widened:
        closure_paths = sorted(retained_paths)
        traversed_edges = []
        closure_status = "WIDENED_ALL_RETAINED"
    else:
        closure_paths = sorted(set(reached_paths) & known_paths)
        closure_status = "EXACT_CONTRACT_CLOSURE_SHADOW"
    critical_audit = _critical_audit(mapping, adjacency)
    test_paths = sorted(
        path
        for path in closure_paths
        if path.startswith("tests/") or "/tests/" in path or "/benches/" in path or path.startswith("core/rust/src/")
    )
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "phase": PHASE,
        "mode": MODE,
        "status": "AFFECTED_CLOSURE_PLAN_ONLY_SELECTION_DISABLED",
        "direct_cutover": "PROHIBITED",
        "source_head": str(inventory["source_head"]),
        "source_tree_sha256": str(graph["source_tree_sha256"]),
        "changed_paths": paths,
        "known_paths": sorted(known_paths),
        "unknown_paths": unknown_paths,
        "dynamic_edge_paths": dynamic_paths,
        "closure_status": closure_status,
        "unknown_dependency_policy": UNKNOWN_POLICY,
        "plan_widened": widened,
        "closure_paths": closure_paths,
        "affected_test_paths": test_paths,
        "traversed_edges": traversed_edges,
        "edge_types": sorted({edge["type"] for edge in edges}),
        "critical_audit": critical_audit,
        "shadow_selection": {
            "would_run_item_ids": sorted(
                str(cast(dict[str, object], item)["stable_id"])
                for item in inventory["items"]
                if isinstance(item, dict) and _normalize(str(cast(dict[str, object], item)["path"])) in closure_paths
            ),
            "would_reuse_item_ids": [],
            "would_skip_item_ids": [],
            "execution": "NOT_EXECUTED",
            "authority": "RETAINED_LEGACY_AUTHORITY",
        },
        "provenance": {
            "source_sha": str(inventory["source_head"]),
            "origin_main_sha": _git_ref("origin/main"),
            "inventory_source_tree_sha256": str(inventory["source_tree_sha256"]),
            "claim_graph_source_tree_sha256": str(graph["source_tree_sha256"]),
            "mapping_artifact_hash": str(mapping.get("artifact_hash", "NOT_AVAILABLE")),
            "edge_digest": _stable_hash(edges),
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "os": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "executable": sys.executable,
            },
            "validator": "scripts/aese_affected_closure.py",
            "evidence_class": "PLANNING_ONLY",
            "claim_scope": "LOCAL_CHECKOUT_ONLY",
            "promotion": "DISABLED_IN_SHADOW",
        },
        "limitations": [
            "This is a deterministic shadow closure; it never controls execution.",
            "Unknown paths and dynamic edges widen to every retained inventory item.",
            "Critical false-negative audit covers only explicitly mapped S2 critical records; unknown surfaces remain widened.",
        ],
    }
    payload["reproducible_hash"] = _stable_hash(
        {
            "source_tree_sha256": payload["source_tree_sha256"],
            "mapping_artifact_hash": mapping.get("artifact_hash", "NOT_AVAILABLE"),
            "changed_paths": paths,
            "closure_status": closure_status,
            "closure_paths": closure_paths,
            "traversed_edges": traversed_edges,
            "critical_audit": critical_audit,
        }
    )
    payload["artifact_hash"] = _stable_hash({key: value for key, value in payload.items() if key != "artifact_hash"})
    return payload


def validate_closure(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    if type(actual) is not dict or type(expected) is not dict:
        return ["closure plans must be canonical dictionaries"]
    errors: list[str] = []
    if set(actual) != set(expected):
        errors.append("root schema keys differ")
    for key in expected:
        if key != "artifact_hash" and actual.get(key) != expected.get(key):
            errors.append(f"{key} differs")
    if actual.get("critical_audit", {}).get("status") != "COMPLETE_ZERO":  # type: ignore[union-attr]
        errors.append("critical false-negative audit is not complete")
    if actual.get("plan_widened") and actual.get("closure_status") != "WIDENED_ALL_RETAINED":
        errors.append("widened plan is not explicitly widened")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", default=[], help="changed repository path (repeatable)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="write the closure plan JSON")
    args = parser.parse_args()
    plan = build_closure(args.path)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": plan["status"],
                "closure_status": plan["closure_status"],
                "plan_widened": plan["plan_widened"],
                "critical_audit": cast(dict[str, object], plan["critical_audit"])["status"],
                "reproducible_hash": plan["reproducible_hash"],
                "artifact_hash": plan["artifact_hash"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
