from __future__ import annotations

import json

import pytest

from scripts.aese_affected_closure import DEFAULT_OUTPUT, build_closure, validate_closure


def test_s3_closure_is_shadow_only_and_audits_critical_false_negatives() -> None:
    plan = build_closure(["core/rust/src/gt96.rs"])
    assert plan["status"] == "AFFECTED_CLOSURE_PLAN_ONLY_SELECTION_DISABLED"
    assert plan["closure_status"] == "EXACT_CONTRACT_CLOSURE_SHADOW"
    assert plan["plan_widened"] is False
    assert plan["critical_audit"]["status"] == "COMPLETE_ZERO"
    assert plan["shadow_selection"]["would_skip_item_ids"] == []
    assert plan["shadow_selection"]["execution"] == "NOT_EXECUTED"


def test_unknown_and_dynamic_dependencies_widen_to_retained_suite() -> None:
    unknown = build_closure(["plugins/unknown_dynamic_loader.py"])
    assert unknown["unknown_paths"] == ["plugins/unknown_dynamic_loader.py"]
    assert unknown["closure_status"] == "WIDENED_ALL_RETAINED"
    assert unknown["plan_widened"] is True
    assert len(unknown["closure_paths"]) == len(set(unknown["known_paths"]) & set(unknown["closure_paths"]))

    dynamic = build_closure(["scripts/e2e_release_gate.py"])
    assert dynamic["dynamic_edge_paths"] == ["scripts/e2e_release_gate.py"]
    assert dynamic["closure_status"] == "WIDENED_ALL_RETAINED"
    assert dynamic["shadow_selection"]["would_skip_item_ids"] == []


def test_closure_hash_and_order_are_deterministic() -> None:
    first = build_closure(["core/rust/src/gt96.rs", "aegis_cognition/aese.py"])
    second = build_closure(["aegis_cognition/aese.py", "core/rust/src/gt96.rs"])
    assert first == second
    assert len(str(first["reproducible_hash"])) == 64


@pytest.mark.parametrize(
    ("changed", "expected", "edge_type"),
    [
        ("aegis_cognition/__init__.py", "aegis_cognition/aese.py", "IMPORT"),
        ("aegis_cognition/aese.py", "core/rust/src/ffi/eac.rs", "FFI"),
        ("core/rust/src/replay.rs", "core/rust/src/schema.rs", "SERIALIZATION"),
        ("core/rust/Cargo.toml", "core/rust/src/lib.rs", "CARGO_FEATURE"),
        ("pyproject.toml", "tests/test_aese_primitives.py", "CONFIG"),
        ("core/rust/src/main.rs", "core/rust/src/cli/mod.rs", "ENTRY_POINT"),
        ("scripts/aese_inventory.py", "quality/registry/current_inventory.json", "GENERATOR"),
        ("scripts/aese_preflight.py", "scripts/aese_claim_graph.py", "CALL"),
        (".github/workflows/ci.yml", "tests/test_aese_primitives.py", "WORKFLOW"),
    ],
)
def test_contract_edges_beat_naive_filename_matching(changed: str, expected: str, edge_type: str) -> None:
    plan = build_closure([changed])
    assert expected in plan["closure_paths"]
    assert edge_type in plan["edge_types"]


def test_all_required_edge_types_are_declared() -> None:
    plan = build_closure(["core/rust/src/gt96.rs"])
    assert set(plan["edge_types"]) == {
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
    }


def test_recorded_s3_artifact_has_no_drift() -> None:
    actual = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert validate_closure(actual, build_closure(["core/rust/src/gt96.rs"])) == []
