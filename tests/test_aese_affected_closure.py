from __future__ import annotations

import json
from collections import defaultdict

import pytest

from scripts import aese_affected_closure as closure_module
from scripts.aese_affected_closure import DEFAULT_MAPPING, DEFAULT_OUTPUT, build_closure, validate_closure


def test_s3_closure_is_shadow_only_and_audits_critical_false_negatives() -> None:
    plan = build_closure(["core/rust/src/gt96.rs"])
    assert plan["status"] == "AFFECTED_CLOSURE_PLAN_ONLY_SELECTION_DISABLED"
    assert plan["closure_status"] == "EXACT_CONTRACT_CLOSURE_SHADOW"
    assert plan["plan_widened"] is False
    audit = plan["critical_audit"]
    assert audit["structural_critical_reachability_status"] == "COMPLETE_ZERO_MAPPED_SCOPE"
    assert audit["checked_mapping_record_count"] == 15
    assert audit["scope"] == "DECLARED_CRITICAL_HIGH_RECORDS"
    assert audit["unknown_surfaces_fail_closed_by_widen"] is True
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


def test_known_but_unmapped_inventory_path_widens() -> None:
    mapping = json.loads(DEFAULT_MAPPING.read_text(encoding="utf-8"))
    inventory = json.loads(closure_module._inventory.DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    unknown_ids = set(mapping["unknown_surface_ids"])
    path = next(str(item["path"]) for item in inventory["items"] if item["stable_id"] in unknown_ids)
    plan = build_closure([path])
    assert plan["path_dependency_states"][path] in {"UNMAPPED", "PARTIALLY_MAPPED"}
    assert plan["changed_unmapped_surface_paths"] == [path]
    assert plan["plan_widened"] is True
    assert plan["closure_status"] == "WIDENED_ALL_RETAINED"
    assert plan["shadow_selection"]["would_skip_item_ids"] == []


def test_mixed_mapped_and_unmapped_changes_widen() -> None:
    plan = build_closure(["core/rust/src/gt96.rs", ".github/workflows/ci.yml"])
    assert plan["plan_widened"] is True
    assert plan["closure_status"] == "WIDENED_ALL_RETAINED"
    assert plan["path_dependency_states"]["core/rust/src/gt96.rs"] == "MAPPED"
    assert plan["path_dependency_states"][".github/workflows/ci.yml"] == "UNMAPPED"
    assert plan["shadow_selection"]["would_skip_item_ids"] == []


def _partial_mapping_fixture(tmp_path):
    mapping = json.loads(DEFAULT_MAPPING.read_text(encoding="utf-8"))
    inventory = json.loads(closure_module._inventory.DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    by_path = defaultdict(list)
    for item in inventory["items"]:
        by_path[item["path"]].append(item["stable_id"])
    path, surface_ids = next((item for item in sorted(by_path.items()) if len(item[1]) >= 2))
    unknown_ids = set(mapping["unknown_surface_ids"])
    mapped_id = next(surface_id for surface_id in surface_ids if surface_id in unknown_ids)
    mapping["records"].append(
        {
            "surface_id": mapped_id,
            "verification_status": "VERIFIED",
            "source_subjects": [f"{path}::fixture_source"],
            "test_subjects": [f"{path}::fixture_test"],
        }
    )
    mapping["unknown_surface_ids"] = sorted(unknown_ids - {mapped_id})
    fixture = tmp_path / "partial_mapping.json"
    fixture.write_text(json.dumps(mapping), encoding="utf-8")
    return fixture, path


def test_partial_mapping_at_one_path_widens(tmp_path) -> None:
    fixture, path = _partial_mapping_fixture(tmp_path)
    plan = build_closure([path], mapping_path=fixture)
    assert plan["path_dependency_states"][path] == "PARTIALLY_MAPPED"
    assert plan["changed_unmapped_surface_paths"] == [path]
    assert plan["plan_widened"] is True
    assert plan["closure_status"] == "WIDENED_ALL_RETAINED"
    assert plan["shadow_selection"]["would_skip_item_ids"] == []


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
    edges = closure_module._declared_edges(closure_module._load_json(DEFAULT_MAPPING))
    assert any(edge["from"] == changed and edge["to"] == expected and edge["type"] == edge_type for edge in edges)
    if plan["path_dependency_states"].get(changed) == "MAPPED":
        assert expected in plan["closure_paths"]
    else:
        assert plan["plan_widened"] is True


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


def test_closure_validator_rejects_unmapped_path_without_widening() -> None:
    plan = build_closure([".github/workflows/ci.yml"])
    plan["plan_widened"] = False
    assert "changed unmapped surfaces must widen" in validate_closure(plan, build_closure([".github/workflows/ci.yml"]))
