from __future__ import annotations

import math

import pytest

from aegis_cognition import (
    AdaptiveMeasurementSpec,
    AdaptiveMeasurementSession,
    AnchorCandidate,
    AnchorObservation,
    AnalyticPredictionModel,
    HardwareCapabilityVector,
    SimulationEvidence,
    WorkloadSignature,
    CoverageVector,
    evaluate_adaptive_measurement,
    predict_cross_hardware,
    select_anchor_plan,
)


def test_adaptive_measurement_passes_only_after_warmup_and_precision_floor() -> None:
    spec = AdaptiveMeasurementSpec(metric="latency_ms", warmup_count=2, min_observations=30, block_size=5)
    result = evaluate_adaptive_measurement(spec, [10.0] * 30, warmups=[0.0, 0.0])
    assert result.status == "PASS"
    assert result.block_count == 6
    assert result.ci_low == result.ci_high == 10.0
    assert result.failure_reasons == ()


def test_adaptive_measurement_continues_before_floor_and_stops_at_budget() -> None:
    spec = AdaptiveMeasurementSpec(
        metric="latency_ms",
        min_observations=30,
        max_observations=30,
        max_lag1_autocorrelation=0.99,
        max_drift_ratio=0.99,
        relative_precision=0.0001,
    )
    early = evaluate_adaptive_measurement(spec, [1.0] * 29, warmups=[0.0] * 10)
    exhausted = evaluate_adaptive_measurement(
        spec,
        [0.7, 1.3, 0.9, 1.1, 0.8, 1.2] * 5,
        warmups=[0.0] * 10,
    )
    assert early.status == "CONTINUE"
    assert exhausted.status == "INSUFFICIENT_EVIDENCE"
    assert "precision_not_met" in exhausted.failure_reasons


def test_adaptive_measurement_marks_nonfinite_and_drift_without_silent_filtering() -> None:
    spec = AdaptiveMeasurementSpec(metric="throughput", block_size=1)
    contaminated = evaluate_adaptive_measurement(spec, [1.0] * 29 + [math.nan], warmups=[0.0] * 10)
    unstable = evaluate_adaptive_measurement(spec, list(range(1, 31)), warmups=[0.0] * 10)
    assert contaminated.status == "CONTAMINATED"
    assert "non_finite_observation" in contaminated.failure_reasons
    assert unstable.status == "UNSTABLE"
    assert "drift_exceeds_bound" in unstable.failure_reasons


def test_adaptive_measurement_baseline_failure_is_not_a_pass() -> None:
    spec = AdaptiveMeasurementSpec(metric="throughput", direction="higher_is_better")
    result = evaluate_adaptive_measurement(spec, [0.9] * 30, warmups=[0.0] * 10, baseline=0.95)
    assert result.status == "FAIL"
    assert "confidence_interval_does_not_clear_baseline" in result.failure_reasons


def test_adaptive_measurement_session_is_append_only_and_stops_at_terminal_status() -> None:
    spec = AdaptiveMeasurementSpec(metric="latency_ms", warmup_count=2)
    session = AdaptiveMeasurementSession(spec).append_warmups([0.0, 0.0])
    session = session.append_observations([10.0] * 10)
    assert session.checkpoint().status == "CONTINUE"
    session = session.append_observations([10.0] * 20)
    assert session.checkpoint().status == "PASS"
    with pytest.raises(RuntimeError, match="terminal"):
        session.append_observations([10.0])


def test_hardware_vector_runtime_projection_does_not_infer_missing_capabilities() -> None:
    vector = HardwareCapabilityVector.from_runtime_profile(
        {
            "cpu": {"architecture": "x86_64", "usable_parallelism": 8},
            "os": {"backend": "windows", "enforcement": "job_object"},
        }
    )
    assert vector.architecture == "x86_64"
    assert vector.logical_cores == 8
    assert vector.memory_capacity_bytes is None
    assert vector.as_dict()["values"]["memory_capacity_bytes"] == "UNKNOWN"


def test_hardware_vector_preserves_unknowns_and_rejects_impossible_core_counts() -> None:
    vector = HardwareCapabilityVector(architecture="x86_64", logical_cores=8)
    payload = vector.as_dict()
    assert payload["values"]["memory_capacity_bytes"] == "UNKNOWN"
    assert "memory_capacity_bytes" in payload["unknown_fields"]
    assert "hardware.logical_cores" in vector.numeric_features()
    with pytest.raises(ValueError, match="logical cores"):
        HardwareCapabilityVector(physical_cores=8, logical_cores=4).validate()


def test_workload_regime_requires_known_dimensions_and_uses_hardware_capacity() -> None:
    assert WorkloadSignature(compute_intensity=0.9, memory_intensity=0.1).classify_regime() == "COMPUTE_BOUND"
    assert WorkloadSignature(network_intensity=0.9, compute_intensity=0.1).classify_regime() == "NETWORK_BOUND"
    workload = WorkloadSignature(memory_intensity=0.9, working_set_bytes=2_000)
    hardware = HardwareCapabilityVector(memory_capacity_bytes=1_000)
    assert workload.classify_regime(hardware) == "MEMORY_CAPACITY_BOUND"
    assert WorkloadSignature(compute_intensity=0.5).classify_regime() == "UNKNOWN"


def test_simulation_evidence_cannot_be_relabelled_as_observation() -> None:
    simulation = SimulationEvidence(
        simulation_id="fault-1",
        simulation_class="FAULT_SIMULATOR",
        question="Does retry fencing contain duplicates?",
        inputs=("retry schedule",),
        outputs=("duplicate count",),
    )
    assert simulation.as_dict()["claimable_as_observed"] is False
    with pytest.raises(ValueError, match="cannot be promoted"):
        SimulationEvidence(
            simulation_id="bad",
            simulation_class="FAULT_SIMULATOR",
            question="bad",
            inputs=("x",),
            outputs=("y",),
            status="OBSERVED",
        ).validate()


def _prediction_fixture() -> tuple[
    AnalyticPredictionModel, HardwareCapabilityVector, WorkloadSignature, list[AnchorObservation]
]:
    model = AnalyticPredictionModel(
        model_id="latency-analytic",
        model_version="1",
        metric="latency_ms",
        intercept=1.0,
        coefficients=(("hardware.logical_cores", 0.1), ("workload.compute_intensity", 2.0)),
        validated_domain=(("hardware.logical_cores", 1.0, 16.0), ("workload.compute_intensity", 0.0, 1.0)),
        residual_half_width=0.1,
        residual_sample_count=30,
        residual_evidence_class="MEASURED",
        distance_penalty_per_unit=0.2,
    )
    anchors = [
        AnchorObservation(
            "a1", HardwareCapabilityVector(logical_cores=4), WorkloadSignature(compute_intensity=0.5), 2.0
        ),
        AnchorObservation(
            "a2", HardwareCapabilityVector(logical_cores=8), WorkloadSignature(compute_intensity=0.5), 2.4
        ),
    ]
    return model, HardwareCapabilityVector(logical_cores=8), WorkloadSignature(compute_intensity=0.5), anchors


def test_cross_hardware_prediction_requires_domain_and_residual_evidence() -> None:
    model, hardware, workload, anchors = _prediction_fixture()
    result = predict_cross_hardware(model, hardware, workload, anchors)
    assert result.status == "PREDICTED_IN_DOMAIN"
    assert result.ood_status == "IN_DOMAIN"
    assert result.estimate == pytest.approx(2.8)
    assert result.prediction_interval is not None
    ood = predict_cross_hardware(model, HardwareCapabilityVector(logical_cores=32), workload, anchors)
    assert ood.status == "REJECTED_OOD"
    assert ood.ood_status == "OUT_OF_DOMAIN"
    unvalidated = AnalyticPredictionModel(
        model_id=model.model_id,
        model_version=model.model_version,
        metric=model.metric,
        intercept=model.intercept,
        coefficients=model.coefficients,
        validated_domain=model.validated_domain,
        residual_half_width=None,
        residual_sample_count=0,
        residual_evidence_class="NOT_VERIFIED",
    )
    insufficient = predict_cross_hardware(unvalidated, hardware, workload, anchors)
    assert insufficient.status == "INSUFFICIENT_EVIDENCE"
    assert insufficient.ood_status == "IN_DOMAIN"


def test_cross_hardware_prediction_refuses_missing_features() -> None:
    model = AnalyticPredictionModel(
        model_id="memory",
        model_version="1",
        metric="latency_ms",
        intercept=0.0,
        coefficients=(("hardware.memory_capacity_bytes", 1.0),),
        validated_domain=(("hardware.memory_capacity_bytes", 1.0, 10_000.0),),
        residual_half_width=1.0,
        residual_sample_count=30,
    )
    result = predict_cross_hardware(
        model,
        HardwareCapabilityVector(),
        WorkloadSignature(compute_intensity=0.5, memory_intensity=0.5),
        [],
    )
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.ood_status == "UNKNOWN"
    assert result.missing_features == ("hardware.memory_capacity_bytes",)


def test_cross_hardware_prediction_does_not_count_out_of_domain_anchors() -> None:
    model, hardware, workload, anchors = _prediction_fixture()
    out_of_domain = AnchorObservation(
        "outside",
        HardwareCapabilityVector(logical_cores=32),
        WorkloadSignature(compute_intensity=0.5),
        4.0,
    )
    result = predict_cross_hardware(model, hardware, workload, [anchors[0], out_of_domain])
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.ood_status == "UNKNOWN"
    assert result.nearest_anchor_distance == pytest.approx(4.0 / 15.0)
    assert result.failure_reasons == ("no_reconstructible_anchor", "anchor_outside_validated_domain")


def test_anchor_planner_prioritizes_mandatory_boundary_and_ood_with_budget() -> None:
    candidates = [
        AnchorCandidate("periodic", "linux", 3.0, periodic_sentinel_due=True),
        AnchorCandidate("ood", "windows", 2.0, ood_status="OUT_OF_DOMAIN", model_uncertainty=0.2),
        AnchorCandidate("boundary", "macos", 2.0, changed_platform_boundary=True),
        AnchorCandidate("mandatory", "linux", 2.0, mandatory=True),
    ]
    plan = select_anchor_plan(candidates, budget_seconds=6.0)
    assert plan.status == "PARTIAL_PLAN"
    assert plan.selected_anchor_ids == ("mandatory", "boundary", "ood")
    assert plan.skipped_anchor_ids == ("periodic",)
    assert plan.execution == "PLANNED_NOT_EXECUTED"


def test_anchor_planner_does_not_hide_unavailable_mandatory_anchor() -> None:
    plan = select_anchor_plan(
        [AnchorCandidate("linux", "linux", 1.0, mandatory=True, available=False)],
        budget_seconds=10.0,
    )
    assert plan.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert plan.unavailable_anchor_ids == ("linux",)
    assert "mandatory_anchor_unavailable" in plan.failure_reasons


def test_coverage_vector_has_independent_dimensions_and_no_aggregate() -> None:
    vector = CoverageVector(contract_coverage="COMPLETE", statistical_precision="NOT_VERIFIED")
    payload = vector.as_dict()
    assert payload["contract_coverage"] == "COMPLETE"
    assert payload["statistical_precision"] == "NOT_VERIFIED"
    assert payload["aggregate"] is None
    with pytest.raises(ValueError, match="coverage vector"):
        CoverageVector(contract_coverage="99%").validate()
