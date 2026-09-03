from __future__ import annotations

import json

import pytest

from scripts.aese_validation_corpus import (
    DEFAULT_CORPUS_OUTPUT,
    DEFAULT_COST_OUTPUT,
    build_cost_measurement,
    build_corpus,
    build_paired_cost_measurement,
    validate_corpus,
)


@pytest.fixture(scope="module")
def corpus() -> dict[str, object]:
    return build_corpus()


def test_validation_corpus_has_held_out_split_and_zero_critical_misses(corpus: dict[str, object]) -> None:
    assert corpus["status"] == "COMPLETE"
    assert corpus["split_policy"]["final_held_out"] is True
    assert corpus["split_policy"]["planner_tuning_uses"] == "DEVELOPMENT_ONLY"
    metrics = corpus["metrics"]
    assert metrics["critical_defects_missed"] == 0
    assert metrics["critical_defects_caught"] == metrics["critical_defects"]
    assert metrics["critical_defects"] >= 1


def test_validation_labels_are_explicit_and_ambiguous_is_preserved(corpus: dict[str, object]) -> None:
    development = corpus["development_corpus"]
    final = corpus["final_validation_corpus"]
    assert {case["label"] for case in development} >= {"KNOWN_GOOD", "KNOWN_BAD", "OOD"}
    assert any(case["label"] == "AMBIGUOUS" for case in final)
    assert {case["case_id"] for case in development}.isdisjoint({case["case_id"] for case in final})


def test_recorded_corpus_hash_is_reproducible(corpus: dict[str, object]) -> None:
    actual = json.loads(DEFAULT_CORPUS_OUTPUT.read_text(encoding="utf-8"))
    assert validate_corpus(actual, corpus) == []


def test_corpus_validator_rejects_stable_decision_drift(corpus: dict[str, object]) -> None:
    actual = json.loads(DEFAULT_CORPUS_OUTPUT.read_text(encoding="utf-8"))
    actual["metrics"]["widen_events"] += 1
    assert "metrics differs" in validate_corpus(actual, corpus)


def test_cost_ledger_withholds_net_savings_without_paired_measurement(corpus: dict[str, object]) -> None:
    cost = build_cost_measurement(corpus)
    assert cost["status"] == "INSUFFICIENT_EVIDENCE"
    assert cost["paired_workload"] is False
    assert cost["legacy_wall_time_seconds"] is None
    assert cost["selected_evidence_wall_time_seconds"] is None
    assert cost["net_saving_seconds"] is None
    assert cost["planner_cost_seconds"]["sample_count"] == 4


def test_recorded_cost_ledger_retains_paired_exploratory_measurement() -> None:
    cost = json.loads(DEFAULT_COST_OUTPUT.read_text(encoding="utf-8"))
    assert cost["status"] == "MEASURED_EXPLORATORY_PAIRED"
    assert cost["paired_workload"] is True
    assert cost["sample_pair_count"] == 3
    assert cost["net_saving_seconds"]["median"] > 0
    assert cost["savings_claim"] == "LOCAL_EXPLORATORY_ONLY"


def test_paired_cost_measurement_computes_net_saving_without_generalizing() -> None:
    cost = build_paired_cost_measurement(
        [98.718078, 57.168086, 71.778817],
        [3.203918, 1.444539, 1.521086],
        [8.139848, 6.937248, 6.625486],
    )
    assert cost["status"] == "MEASURED_EXPLORATORY_PAIRED"
    assert cost["paired_workload"] is True
    assert cost["workload"]["legacy_test_count"] == 456
    assert cost["workload"]["selected_test_count"] == 14
    assert cost["net_saving_seconds"]["median"] > 0
    assert cost["savings_claim"] == "LOCAL_EXPLORATORY_ONLY"
