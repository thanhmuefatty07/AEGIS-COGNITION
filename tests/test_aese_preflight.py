from __future__ import annotations

from scripts.aese_preflight import build_preflight


def test_empty_preflight_widens_and_never_skips() -> None:
    plan = build_preflight()
    assert plan["mode"] == "SHADOW"
    assert plan["plan_widened"] is True
    assert plan["legacy_selection"] == "RUN_ALL_RETAINED"
    assert all(stage["may_skip"] is False for stage in plan["evidence_order"])


def test_known_code_change_records_affected_claims_without_cutover() -> None:
    plan = build_preflight(["core/rust/src/gt96.rs"])
    assert plan["plan_widened"] is False
    assert plan["matched_code_ids"]
    assert plan["affected_claim_ids"]
    assert plan["direct_cutover"] == "PROHIBITED"


def test_unknown_change_widens_conservatively() -> None:
    plan = build_preflight(["new/unmapped/input.txt"])
    assert plan["unknown_paths"] == ["new/unmapped/input.txt"]
    assert plan["plan_widened"] is True
    assert plan["unknown_dependency_policy"] == "WIDEN_TO_RETAINED_SUITE"


def test_known_but_unmapped_surface_also_widens() -> None:
    plan = build_preflight(["scripts/aese_preflight.py"])
    assert plan["unknown_paths"] == []
    assert plan["unmapped_known_paths"] == ["scripts/aese_preflight.py"]
    assert plan["plan_widened"] is True


def test_dot_github_path_is_not_corrupted_by_normalization() -> None:
    plan = build_preflight([".github/workflows/ci.yml"])
    assert plan["unknown_paths"] == []
    assert plan["matched_inventory_ids"]
    assert plan["plan_widened"] is True


def test_preflight_digest_is_deterministic() -> None:
    first = build_preflight(["core/rust/src/gt96.rs", "README.md"])
    second = build_preflight(["README.md", "core/rust/src/gt96.rs"])
    assert first == second
