from __future__ import annotations

import pytest

from scripts.aese_statistical_calibration import (
    PROTOCOL_VARIANTS,
    _bound_decision,
    _cell_criteria,
    _summarize_trials,
    _wilson_bounds,
    run_calibration,
)


def test_protocol_campaign_preregisters_required_parameter_variation() -> None:
    assert {variant.min_observations for variant in PROTOCOL_VARIANTS} == {30, 36, 40}
    assert {variant.max_observations for variant in PROTOCOL_VARIANTS} == {45, 60, 90}
    assert {variant.block_size for variant in PROTOCOL_VARIANTS} == {3, 5, 10}
    assert {variant.alpha for variant in PROTOCOL_VARIANTS} == {0.01, 0.05, 0.10}
    assert {variant.relative_precision for variant in PROTOCOL_VARIANTS} == {0.02, 0.05, 0.10}


def test_calibration_campaign_is_deterministic_and_shadow_only() -> None:
    kwargs = {
        "replicates": 2,
        "max_replicates": 2,
        "replicate_batch": 2,
        "campaign_seed": 20260903,
        "families": ("iid_gaussian", "bursty_contamination"),
        "variants": ("default",),
        "checkpoint_policies": ("every_observation",),
    }
    first = run_calibration(**kwargs)
    second = run_calibration(**kwargs)

    assert first == second
    assert first["artifact_hash"]
    assert first["mode"] == "SHADOW"
    assert first["legacy_full_suite_authority"] == "RETAINED"
    assert first["selection_authority"] == "DISABLED"
    assert first["claimable_as_observed"] is False
    assert first["promotion_status"] == "DISABLED_IN_SHADOW"
    assert first["source"]["source_sha"]
    assert first["source"]["worktree_status"] in {"CLEAN", "DIRTY"}


def test_calibration_reports_required_metrics_and_declared_domains() -> None:
    artifact = run_calibration(
        replicates=2,
        max_replicates=2,
        replicate_batch=2,
        families=("iid_gaussian", "ar1_positive", "rare_extreme_outlier"),
        variants=("default",),
        checkpoint_policies=("every_block",),
    )

    assert artifact["status"] == "INSUFFICIENT_EVIDENCE"
    assessments = artifact["domain_assessments"]
    assert assessments["ar1_positive"]["assessment"] == "OUT_OF_DOMAIN"
    assert assessments["iid_gaussian"]["assessment"] == "INSUFFICIENT_EVIDENCE"
    assert "coverage_score" not in artifact
    assert "detection_rate" in artifact["contamination_summary"]
    assert "detection_rate" in artifact["unstable_summary"]
    summary = artifact["cells"][0]["scenarios"]["null"]
    for key in (
        "empirical_ci_coverage",
        "false_pass_rate",
        "false_fail_rate",
        "early_stop_frequency",
        "average_observations_consumed",
        "p95_observations_consumed",
        "unstable_detection_rate",
        "contamination_detection_rate",
        "budget_exhaustion_rate",
        "correct_decision_count",
        "wrong_decision_count",
        "inconclusive_count",
        "decision_resolution_rate",
        "confidence_bounds",
    ):
        assert key in summary


def test_wilson_bounds_and_decision_labels_cover_boundary_counts() -> None:
    for successes in (0, 1, 29, 30):
        bounds = _wilson_bounds(successes, 30)
        assert bounds["method_id"] == "wilson_score_v1"
        assert 0.0 <= float(bounds["lower_bound"]) <= float(bounds["upper_bound"]) <= 1.0
    assert _wilson_bounds(0, 30)["lower_bound"] == 0.0
    assert _wilson_bounds(30, 30)["upper_bound"] == 1.0

    def interval(lower: float, upper: float) -> dict[str, object]:
        return {"lower_bound": lower, "upper_bound": upper}

    assert _bound_decision(interval(0.90, 0.99), threshold=0.90, relation="minimum") == "VALIDATED"
    assert _bound_decision(interval(0.10, 0.89), threshold=0.90, relation="minimum") == "INVALIDATED"
    assert _bound_decision(interval(0.01, 0.05), threshold=0.05, relation="maximum") == "VALIDATED"
    assert _bound_decision(interval(0.06, 0.90), threshold=0.05, relation="maximum") == "INVALIDATED"
    assert _bound_decision({"lower_bound": None, "upper_bound": None}, threshold=0.90, relation="minimum") == "INCONCLUSIVE"


def test_always_inconclusive_protocol_cannot_validate() -> None:
    trial = {
        "observations_consumed": 30,
        "coverage_eligible": True,
        "coverage": True,
        "false_pass": False,
        "false_fail": False,
        "correct_decision": False,
        "wrong_decision": False,
        "inconclusive_decision": True,
        "unstable_detected": False,
        "contamination_detected": False,
        "early_stop": False,
        "premature_terminal": False,
        "budget_exhausted": True,
        "final_status": "INSUFFICIENT_EVIDENCE",
    }
    summary = _summarize_trials([trial.copy() for _ in range(30)])
    assert summary["correct_decision_count"] == 0
    assert summary["wrong_decision_count"] == 0
    assert summary["inconclusive_count"] == 30
    assert summary["decision_resolution_rate"] == 0.0
    assert "always_inconclusive_protocol" in _cell_criteria(summary, 30)


def test_adaptive_replicate_allocation_is_recorded() -> None:
    artifact = run_calibration(
        replicates=2,
        max_replicates=4,
        replicate_batch=2,
        families=("iid_gaussian",),
        variants=("default",),
        checkpoint_policies=("every_block",),
    )
    cell = artifact["cells"][0]
    assert cell["replicates_allocated"] in {2, 4}
    assert cell["allocation_batches"][0] == 2
    assert cell["allocation_stop_reason"] in {"META_BOUNDS_DECIDABLE", "MAXIMUM_REPLICATES_REACHED"}
    assert artifact["campaign"]["allocation_policy"] == "ADAPTIVE_BOUNDS_UNTIL_DECIDABLE"


@pytest.mark.parametrize(
    ("field", "value"),
    (("replicates", 0), ("campaign_seed", -1), ("families", ("unknown",))),
)
def test_calibration_rejects_invalid_campaign_configuration(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "replicates": 1,
        "campaign_seed": 1,
        "families": ("iid_gaussian",),
        "variants": ("default",),
        "checkpoint_policies": ("every_observation",),
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="calibration"):
        run_calibration(**kwargs)
