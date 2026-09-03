from __future__ import annotations

import re

from scripts.aese_preflight import build_preflight, validate_preflight


def test_empty_preflight_widens_and_never_skips() -> None:
    plan = build_preflight()
    assert plan["mode"] == "SHADOW"
    assert plan["plan_widened"] is True
    assert plan["legacy_selection"] == "RUN_ALL_RETAINED"
    assert all(stage["may_skip"] is False for stage in plan["evidence_order"])


def test_known_code_change_records_affected_claims_without_cutover() -> None:
    plan = build_preflight(["core/rust/src/gt96.rs"])
    assert plan["plan_widened"] is False
    assert plan["closure_status"] == "EXACT_SOURCE_CLOSURE_SHADOW"
    assert plan["matched_code_ids"]
    assert plan["affected_claim_ids"]
    assert plan["affected_contract_ids"]
    assert plan["affected_invariant_ids"]
    assert plan["affected_verification_ids"]
    assert len(plan["affected_verification_ids"]) == 22
    assert plan["affected_evidence_ids"]
    assert plan["direct_cutover"] == "PROHIBITED"


def test_unknown_change_widens_conservatively() -> None:
    plan = build_preflight(["new/unmapped/input.txt"])
    assert plan["unknown_paths"] == ["new/unmapped/input.txt"]
    assert plan["plan_widened"] is True
    assert plan["closure_status"] == "WIDENED_ALL_RETAINED"
    assert len(plan["affected_verification_ids"]) == 92
    assert plan["unknown_dependency_policy"] == "WIDEN_TO_RETAINED_SUITE"
    selection = plan["shadow_selection"]
    assert isinstance(selection, dict)
    assert len(selection["legacy_would_run_item_ids"]) == 127
    assert selection["would_reuse_item_ids"] == []
    assert selection["would_skip_item_ids"] == []
    assert selection["external_anchor_requests"] == []
    assert selection["external_anchor_status"] == "NOT_EVALUATED_NO_CANDIDATES"
    assert selection["execution"] == "NOT_EXECUTED"


def test_known_but_unmapped_surface_also_widens() -> None:
    plan = build_preflight(["scripts/run_checks.py"])
    assert plan["unknown_paths"] == []
    assert plan["unmapped_known_paths"] == ["scripts/run_checks.py"]
    assert plan["plan_widened"] is True


def test_mapped_test_surface_has_exact_shadow_closure() -> None:
    plan = build_preflight(["tests/test_aese_primitives.py"])

    assert plan["unknown_paths"] == []
    assert plan["unmapped_known_paths"] == []
    assert plan["plan_widened"] is False
    assert plan["closure_status"] == "EXACT_SOURCE_CLOSURE_SHADOW"
    assert len(plan["affected_claim_ids"]) == 8
    assert len(plan["affected_verification_ids"]) == 8
    assert plan["shadow_selection"]["would_skip_item_ids"] == []


def test_dot_github_path_is_not_corrupted_by_normalization() -> None:
    plan = build_preflight([".github/workflows/ci.yml"])
    assert plan["unknown_paths"] == []
    assert plan["matched_inventory_ids"]
    assert plan["plan_widened"] is True


def test_preflight_digest_is_deterministic() -> None:
    first = build_preflight(["core/rust/src/gt96.rs", "README.md"])
    second = build_preflight(["README.md", "core/rust/src/gt96.rs"])
    assert first == second


def test_preflight_provenance_is_explicit_and_non_promotable() -> None:
    plan = build_preflight(["core/rust/src/gt96.rs"])
    provenance = plan["provenance"]
    assert isinstance(provenance, dict)
    assert re.fullmatch(r"[0-9a-f]{40}", str(provenance["source_sha"]))
    assert provenance["worktree_status"] in {"CLEAN", "DIRTY"}
    assert re.fullmatch(r"[0-9a-f]{64}", str(provenance["worktree_epoch"]))
    assert re.fullmatch(r"[0-9a-f]{64}", str(provenance["inventory_source_tree_sha256"]))
    assert re.fullmatch(r"[0-9a-f]{64}", str(provenance["graph_input_sha256"]))
    assert provenance["validator"] == "scripts/aese_preflight.py"
    assert provenance["evidence_class"] == "PLANNING_ONLY"
    assert provenance["claim_scope"] == "LOCAL_CHECKOUT_ONLY"
    assert provenance["promotion"] == "DISABLED_IN_SHADOW"


def test_preflight_validator_rejects_open_root_schema() -> None:
    expected = build_preflight(["aegis_cognition/aese.py"])
    actual = {**expected, "unsafe_skip_item_ids": ["legacy-test"]}

    assert validate_preflight(actual, expected) == ["root schema keys differ"]
    assert validate_preflight([], expected) == ["preflight plans must be canonical dictionaries"]  # type: ignore[arg-type]
