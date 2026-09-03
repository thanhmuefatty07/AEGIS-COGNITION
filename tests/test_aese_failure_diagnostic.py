from __future__ import annotations

from scripts.aese_failure_diagnostic import _distribution, _failure_class


def test_failure_diagnostic_distribution_is_null_aware() -> None:
    summary = _distribution([None, 1.0, 2.0, 3.0])
    assert summary["count"] == 3
    assert summary["null_count"] == 1
    assert summary["median"] == 2.0
    assert summary["p95"] == 3.0


def test_failure_diagnostic_classifies_known_reasons_without_guessing() -> None:
    assert _failure_class("autocorrelation_exceeds_bound") == "STABILITY_DETECTOR"
    assert _failure_class("precision_not_met") == "PRECISION_REQUIREMENT"
    assert _failure_class("confidence_interval_does_not_clear_baseline") == "BASELINE_SEMANTICS"
    assert _failure_class("unclassified_reason") == "UNKNOWN"
