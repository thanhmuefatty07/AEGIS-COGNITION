from __future__ import annotations

import json

import pytest

from scripts.aese_shadow_planner import DEFAULT_OUTPUT, build_shadow_plan, validate_shadow_plan


def test_shadow_plan_is_explainable_and_never_executes_or_skips_authority() -> None:
    plan = build_shadow_plan(["core/rust/src/gt96.rs"])
    assert plan["status"] == "SHADOW_PLAN_ONLY_SELECTION_DISABLED"
    assert plan["closure_status"] == "EXACT_CONTRACT_CLOSURE_SHADOW"
    assert plan["structural_critical_reachability_status"] == "COMPLETE_ZERO_MAPPED_SCOPE"
    assert plan["observed_critical_false_negative_status"] == "NOT_MEASURED"
    assert plan["critical_false_negative_status"] == "NOT_MEASURED"
    assert plan["execution"] == "NOT_EXECUTED"
    assert plan["would_reuse_item_ids"] == []
    assert plan["would_skip_item_ids"]
    assert all(decision["reason"] and decision["evidence"] for decision in plan["decisions"])


def test_unknown_closure_widens_every_item_and_has_no_skip() -> None:
    plan = build_shadow_plan(["plugins/unknown_dynamic_loader.py"])
    assert plan["plan_widened"] is True
    assert plan["decision_states"] == ["WIDENED_UNKNOWN"]
    assert plan["would_skip_item_ids"] == []
    assert len(plan["would_run_item_ids"]) == 140


def test_known_but_unmapped_inventory_path_widens_every_item() -> None:
    mapping = json.loads(DEFAULT_OUTPUT.parent.joinpath("current_s2_mapping.json").read_text(encoding="utf-8"))
    inventory = json.loads(DEFAULT_OUTPUT.parent.joinpath("current_inventory.json").read_text(encoding="utf-8"))
    unknown_ids = set(mapping["unknown_surface_ids"])
    path = next(str(item["path"]) for item in inventory["items"] if item["stable_id"] in unknown_ids)
    plan = build_shadow_plan([path])
    assert plan["plan_widened"] is True
    assert plan["path_dependency_states"][path] in {"UNMAPPED", "PARTIALLY_MAPPED"}
    assert plan["would_skip_item_ids"] == []
    assert len(plan["would_run_item_ids"]) == 140


def test_mixed_mapped_and_unmapped_changes_select_nothing_to_skip() -> None:
    plan = build_shadow_plan(["core/rust/src/gt96.rs", ".github/workflows/ci.yml"])
    assert plan["plan_widened"] is True
    assert plan["would_skip_item_ids"] == []
    assert len(plan["would_run_item_ids"]) == 140


def test_partial_mapping_path_widens_every_item(tmp_path) -> None:
    mapping_path = DEFAULT_OUTPUT.parent.joinpath("current_s2_mapping.json")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    inventory = json.loads(DEFAULT_OUTPUT.parent.joinpath("current_inventory.json").read_text(encoding="utf-8"))
    grouped: dict[str, list[str]] = {}
    for item in inventory["items"]:
        grouped.setdefault(str(item["path"]), []).append(str(item["stable_id"]))
    path, surface_ids = next(entry for entry in sorted(grouped.items()) if len(entry[1]) >= 2)
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
    plan = build_shadow_plan([path], mapping_path=fixture)
    assert plan["path_dependency_states"][path] == "PARTIALLY_MAPPED"
    assert plan["plan_widened"] is True
    assert plan["would_skip_item_ids"] == []
    assert len(plan["would_run_item_ids"]) == 140


def test_external_workflow_anchor_is_deferred_without_authority_change() -> None:
    plan = build_shadow_plan([".github/workflows/ci.yml"])
    assert plan["external_deferred"] == [
        {
            "state": "EXTERNAL_DEFERRED",
            "path": ".github/workflows/ci.yml",
            "reason": "hosted_external_anchor_unavailable; local legacy authority remains retained",
        }
    ]
    assert plan["execution"] == "NOT_EXECUTED"


def test_confusion_matrix_is_only_measured_from_explicit_legacy_results() -> None:
    baseline = build_shadow_plan(["core/rust/src/gt96.rs"])
    selected = baseline["would_run_item_ids"]
    assert isinstance(selected, list)
    results = {str(selected[0]): "FAIL"}
    measured = build_shadow_plan(["core/rust/src/gt96.rs"], results)
    matrix = measured["confusion_matrix"]
    assert matrix["status"] == "MEASURED_FROM_EXPLICIT_LEGACY_RESULTS"
    assert matrix["counts"]["AESE_SELECTED_FAILED"] == 1
    assert matrix["critical_false_negatives"] == []
    assert measured["observed_critical_false_negative_status"] == "NOT_MEASURED_INCOMPLETE"


def test_shadow_plan_hash_and_recorded_artifact_are_stable() -> None:
    first = build_shadow_plan(["core/rust/src/gt96.rs", "aegis_cognition/aese.py"])
    second = build_shadow_plan(["aegis_cognition/aese.py", "core/rust/src/gt96.rs"])
    assert first["artifact_hash"] == second["artifact_hash"]
    actual = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert validate_shadow_plan(actual, build_shadow_plan(["core/rust/src/gt96.rs"])) == []


def test_no_legacy_results_cannot_be_reported_as_observed_zero() -> None:
    plan = build_shadow_plan(["core/rust/src/gt96.rs"])
    assert plan["confusion_matrix"]["status"] == "NOT_MEASURED"
    assert plan["observed_critical_false_negative_status"] == "NOT_MEASURED"
    assert plan["observed_critical_false_negative_status"] != "OBSERVED_COMPLETE_ZERO"


@pytest.mark.parametrize("bad_state", ["RUN", "SKIP", "opaque_score"])
def test_validator_rejects_unauthorized_or_opaque_decisions(bad_state: str) -> None:
    plan = build_shadow_plan(["core/rust/src/gt96.rs"])
    plan["decision_states"] = [bad_state]
    assert validate_shadow_plan(plan, build_shadow_plan(["core/rust/src/gt96.rs"]))
