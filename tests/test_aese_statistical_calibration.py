from __future__ import annotations

import pytest

from scripts.aese_statistical_calibration import PROTOCOL_VARIANTS, run_calibration


def test_protocol_campaign_preregisters_required_parameter_variation() -> None:
    assert {variant.min_observations for variant in PROTOCOL_VARIANTS} == {30, 36, 40}
    assert {variant.max_observations for variant in PROTOCOL_VARIANTS} == {45, 60, 90}
    assert {variant.block_size for variant in PROTOCOL_VARIANTS} == {3, 5, 10}
    assert {variant.alpha for variant in PROTOCOL_VARIANTS} == {0.01, 0.05, 0.10}
    assert {variant.relative_precision for variant in PROTOCOL_VARIANTS} == {0.02, 0.05, 0.10}


def test_calibration_campaign_is_deterministic_and_shadow_only() -> None:
    kwargs = {
        "replicates": 2,
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
    ):
        assert key in summary


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
