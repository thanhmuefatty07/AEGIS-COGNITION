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


def test_adaptive_measurement_rejects_over_budget_input_even_when_precision_is_zero() -> None:
    spec = AdaptiveMeasurementSpec(metric="latency_ms", max_observations=30)
    result = evaluate_adaptive_measurement(spec, [1.0] * 31, warmups=[0.0] * 10)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert "observation_budget_exceeded" in result.failure_reasons
    assert result.interval_method == "student_t_cornish_fisher_bonferroni_peek_v1"


def test_adaptive_measurement_does_not_pass_with_a_trailing_partial_block() -> None:
    spec = AdaptiveMeasurementSpec(metric="latency_ms", block_size=5)
    result = evaluate_adaptive_measurement(spec, [1.0] * 31, warmups=[0.0] * 10)
    assert result.status == "CONTINUE"
    assert "incomplete_final_block" in result.failure_reasons


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


def test_adaptive_measurement_malformed_protocol_fails_closed_without_raising() -> None:
    malformed = AdaptiveMeasurementSpec(metric="latency_ms", min_observations="30")  # type: ignore[arg-type]
    result = evaluate_adaptive_measurement(malformed, [1.0] * 30, warmups=[0.0] * 10)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.metric == "latency_ms"
    assert any(reason.startswith("protocol_invalid:") for reason in result.failure_reasons)


def test_adaptive_measurement_non_dataclass_protocol_returns_typed_failure() -> None:
    result = evaluate_adaptive_measurement(None, [1.0] * 30)  # type: ignore[arg-type]
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.metric == ""
    assert result.estimate is None
    assert any(reason.startswith("protocol_invalid:") for reason in result.failure_reasons)


def test_adaptive_measurement_rejects_forged_spec_and_exploding_containers() -> None:
    spec = AdaptiveMeasurementSpec(metric="latency_ms")

    class SpecLike(AdaptiveMeasurementSpec):
        def validate(self) -> None:
            raise RuntimeError("forged spec was invoked")

    class ExplodingSequence:
        def __iter__(self):
            raise RuntimeError("container was iterated")

    forged = evaluate_adaptive_measurement(SpecLike(metric=spec.metric), [1.0] * 30)
    assert forged.status == "INSUFFICIENT_EVIDENCE"
    assert "protocol_invalid:TypeError" in forged.failure_reasons

    result = evaluate_adaptive_measurement(
        spec,
        ExplodingSequence(),  # type: ignore[arg-type]
        warmups=ExplodingSequence(),  # type: ignore[arg-type]
        contamination_flags=ExplodingSequence(),  # type: ignore[arg-type]
    )
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert "observations_invalid" in result.failure_reasons
    assert "warmups_invalid" in result.failure_reasons
    assert "contamination_flags_invalid" in result.failure_reasons


def test_adaptive_measurement_session_rejects_forged_append_containers() -> None:
    spec = AdaptiveMeasurementSpec(metric="latency_ms")
    session = AdaptiveMeasurementSession(spec)

    class ExplodingSequence:
        def __iter__(self):
            raise RuntimeError("session container was iterated")

    with pytest.raises(TypeError, match="canonical list or tuple"):
        session.append_warmups(ExplodingSequence())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="canonical list or tuple"):
        session.append_observations(ExplodingSequence())  # type: ignore[arg-type]


def test_adaptive_measurement_rejects_malformed_contamination_flags() -> None:
    result = evaluate_adaptive_measurement(
        AdaptiveMeasurementSpec(metric="latency_ms"),
        [1.0] * 30,
        warmups=[0.0] * 10,
        contamination_flags="not-a-sequence",  # type: ignore[arg-type]
    )
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert "contamination_flags_invalid" in result.failure_reasons
    assert result.estimate == 1.0


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


def test_hardware_vector_mapping_rejects_non_mapping_and_mixed_unknown_keys() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        HardwareCapabilityVector.from_mapping(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown hardware fields"):
        HardwareCapabilityVector.from_mapping({"unknown": 1, 2: "bad"})  # type: ignore[dict-item]


def test_hardware_vector_rejects_non_mapping_runtime_profile() -> None:
    with pytest.raises(ValueError, match="runtime profile must be a mapping"):
        HardwareCapabilityVector.from_runtime_profile(None)  # type: ignore[arg-type]


def test_hardware_vector_rejects_malformed_nested_runtime_profiles() -> None:
    with pytest.raises(ValueError, match="runtime profile cpu"):
        HardwareCapabilityVector.from_runtime_profile({"cpu": "unknown"})
    with pytest.raises(ValueError, match="runtime profile os"):
        HardwareCapabilityVector.from_runtime_profile({"os": []})


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
    assert result.anchor_ids == ("a1", "a2")
    assert tuple(anchor_id for anchor_id, _hash in result.anchor_evidence_hashes) == ("a1", "a2")
    reordered = predict_cross_hardware(model, hardware, workload, list(reversed(anchors)))
    assert reordered.artifact_hash == result.artifact_hash
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
    assert result.anchor_ids == ("a1",)
    assert tuple(anchor_id for anchor_id, _hash in result.anchor_evidence_hashes) == ("a1",)


def test_cross_hardware_prediction_does_not_count_duplicate_anchor_ids() -> None:
    model, hardware, workload, anchors = _prediction_fixture()
    result = predict_cross_hardware(model, hardware, workload, [anchors[0], anchors[0]])
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.ood_status == "UNKNOWN"
    assert result.nearest_anchor_distance == pytest.approx(4.0 / 15.0)
    assert result.failure_reasons == ("no_reconstructible_anchor", "duplicate_anchor_id")
    assert result.anchor_ids == ("a1",)
    assert tuple(anchor_id for anchor_id, _hash in result.anchor_evidence_hashes) == ("a1",)


def test_cross_hardware_prediction_hash_binds_anchor_observation_values() -> None:
    model, hardware, workload, anchors = _prediction_fixture()
    altered = AnchorObservation(
        "a2",
        anchors[1].hardware,
        anchors[1].workload,
        99.0,
    )
    original = predict_cross_hardware(model, hardware, workload, anchors)
    changed = predict_cross_hardware(model, hardware, workload, [anchors[0], altered])
    assert original.estimate == changed.estimate
    assert original.anchor_ids == changed.anchor_ids == ("a1", "a2")
    assert original.anchor_evidence_hashes != changed.anchor_evidence_hashes
    assert original.artifact_hash != changed.artifact_hash


def test_cross_hardware_prediction_fails_closed_on_invalid_model_or_hardware() -> None:
    model, _hardware, workload, anchors = _prediction_fixture()
    malformed_hardware = HardwareCapabilityVector(logical_cores="8")  # type: ignore[arg-type]
    result = predict_cross_hardware(model, malformed_hardware, workload, anchors)

    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.ood_status == "UNKNOWN"
    assert result.estimate is None
    assert result.failure_reasons == ("input_invalid:ValueError",)
    assert result.anchor_ids == ()

    malformed_model = AnalyticPredictionModel(
        model_id=123,  # type: ignore[arg-type]
        model_version="1",
        metric="latency_ms",
        intercept=0.0,
        coefficients=(("hardware.logical_cores",),),  # type: ignore[arg-type]
        validated_domain=(("hardware.logical_cores", 1.0, 16.0),),
        residual_half_width=1.0,
        residual_sample_count=30,
        residual_evidence_class="MEASURED",
    )
    malformed_result = predict_cross_hardware(
        malformed_model,
        _hardware,
        workload,
        anchors,
    )
    assert malformed_result.status == "INSUFFICIENT_EVIDENCE"
    assert malformed_result.model_id == ""
    assert malformed_result.failure_reasons == ("input_invalid:ValueError",)


def test_cross_hardware_prediction_does_not_silently_drop_invalid_anchor() -> None:
    model, hardware, workload, anchors = _prediction_fixture()
    result = predict_cross_hardware(model, hardware, workload, [anchors[0], object(), anchors[1]])  # type: ignore[list-item]

    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.ood_status == "UNKNOWN"
    assert result.failure_reasons == ("invalid_anchor_observation",)
    assert result.anchor_ids == ("a1", "a2")


def test_cross_hardware_prediction_rejects_non_sequence_anchors() -> None:
    model, hardware, workload, _anchors = _prediction_fixture()
    result = predict_cross_hardware(model, hardware, workload, 1)  # type: ignore[arg-type]

    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.ood_status == "UNKNOWN"
    assert result.failure_reasons == ("anchors_invalid:TypeError",)
    assert result.anchor_ids == ()


def test_cross_hardware_prediction_rejects_custom_anchor_objects() -> None:
    model, hardware, workload, anchors = _prediction_fixture()

    class AnchorLike:
        def features(self) -> dict[str, float]:
            return anchors[0].features()

    result = predict_cross_hardware(model, hardware, workload, [AnchorLike(), anchors[1]])  # type: ignore[list-item]

    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.failure_reasons == ("invalid_anchor_observation",)
    assert result.anchor_ids == ("a2",)


def test_cross_hardware_prediction_rejects_forged_protocol_objects() -> None:
    model, hardware, workload, anchors = _prediction_fixture()

    class ModelLike:
        model_id = model.model_id
        model_version = model.model_version
        metric = model.metric
        validated_domain = model.validated_domain

    class HardwareLike:
        pass

    class WorkloadLike:
        pass

    forged_model = predict_cross_hardware(ModelLike(), hardware, workload, anchors)  # type: ignore[arg-type]
    assert forged_model.status == "INSUFFICIENT_EVIDENCE"
    assert forged_model.failure_reasons == ("model_invalid:TypeError",)

    forged_hardware = predict_cross_hardware(model, HardwareLike(), workload, anchors)  # type: ignore[arg-type]
    assert forged_hardware.status == "INSUFFICIENT_EVIDENCE"
    assert forged_hardware.failure_reasons == ("hardware_invalid:TypeError",)

    forged_workload = predict_cross_hardware(model, hardware, WorkloadLike(), anchors)  # type: ignore[arg-type]
    assert forged_workload.status == "INSUFFICIENT_EVIDENCE"
    assert forged_workload.failure_reasons == ("workload_invalid:TypeError",)


def test_cross_hardware_prediction_rejects_forged_nested_anchor_vectors() -> None:
    model, hardware, workload, anchors = _prediction_fixture()

    class HardwareLike(HardwareCapabilityVector):
        pass

    forged_anchor = AnchorObservation(
        "forged",
        HardwareLike(logical_cores=4),
        workload,
        2.0,
    )
    result = predict_cross_hardware(model, hardware, workload, [forged_anchor, anchors[1]])

    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.failure_reasons == ("invalid_anchor_observation",)
    assert result.anchor_ids == ("a2",)


def test_aese_rejects_custom_sequence_containers_at_trust_boundaries() -> None:
    model, hardware, workload, anchors = _prediction_fixture()

    class SequenceLike:
        def __init__(self, values: object) -> None:
            self.values = values

        def __iter__(self):
            return iter(self.values)  # type: ignore[arg-type]

        def __len__(self) -> int:
            return len(self.values)  # type: ignore[arg-type]

        def __getitem__(self, index: int) -> object:
            return self.values[index]  # type: ignore[index]

    plan = select_anchor_plan(SequenceLike([]), budget_seconds=10.0)  # type: ignore[arg-type]
    assert plan.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert plan.selected_anchor_ids == ()
    assert plan.failure_reasons == ("candidates_invalid:TypeError",)

    result = predict_cross_hardware(model, hardware, workload, SequenceLike(anchors))  # type: ignore[arg-type]
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.anchor_ids == ()
    assert result.failure_reasons == ("anchors_invalid:TypeError",)

    malformed_model = AnalyticPredictionModel(
        model_id=model.model_id,
        model_version=model.model_version,
        metric=model.metric,
        intercept=model.intercept,
        coefficients=SequenceLike(model.coefficients),  # type: ignore[arg-type]
        validated_domain=model.validated_domain,
        residual_half_width=model.residual_half_width,
        residual_sample_count=model.residual_sample_count,
        residual_evidence_class=model.residual_evidence_class,
        distance_penalty_per_unit=model.distance_penalty_per_unit,
    )
    malformed_result = predict_cross_hardware(malformed_model, hardware, workload, anchors)
    assert malformed_result.status == "INSUFFICIENT_EVIDENCE"
    assert malformed_result.failure_reasons == ("input_invalid:TypeError",)
    assert malformed_result.validated_domain == model.validated_domain


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


def test_anchor_planner_fails_closed_on_non_sequence_candidates() -> None:
    plan = select_anchor_plan(None, budget_seconds=10.0)  # type: ignore[arg-type]
    assert plan.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert plan.selected_anchor_ids == ()
    assert "candidates_invalid:TypeError" in plan.failure_reasons


def test_anchor_planner_rejects_custom_candidate_objects() -> None:
    class CandidateLike:
        def validate(self) -> None:
            return None

    plan = select_anchor_plan([CandidateLike()], budget_seconds=10.0)  # type: ignore[list-item]

    assert plan.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert plan.selected_anchor_ids == ()
    assert plan.failure_reasons == ("candidate_invalid:TypeError",)


def test_anchor_planner_clears_selection_when_any_input_is_invalid() -> None:
    valid = AnchorCandidate("linux", "linux", 1.0)

    invalid_budget = select_anchor_plan([valid], budget_seconds=float("nan"))
    assert invalid_budget.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert invalid_budget.selected_anchor_ids == ()
    assert invalid_budget.planned_cost_seconds == 0.0
    assert "budget_invalid" in invalid_budget.failure_reasons

    invalid_candidate = select_anchor_plan(
        [valid, object()],  # type: ignore[list-item]
        budget_seconds=10.0,
    )
    assert invalid_candidate.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert invalid_candidate.selected_anchor_ids == ()
    assert invalid_candidate.planned_cost_seconds == 0.0
    assert "candidate_invalid:TypeError" in invalid_candidate.failure_reasons


def test_anchor_planner_rejects_planned_cost_overflow() -> None:
    plan = select_anchor_plan(
        [
            AnchorCandidate("a", "linux", 1.7e308, mandatory=True),
            AnchorCandidate("b", "linux", 1.7e308, mandatory=True),
        ],
        budget_seconds=1.0,
    )
    assert plan.status == "EXTERNAL_VERIFICATION_BLOCKED"
    assert plan.selected_anchor_ids == ()
    assert plan.planned_cost_seconds == 0.0
    assert "planned_cost_overflow" in plan.failure_reasons


def test_anchor_candidate_rejects_lossy_availability_flag() -> None:
    with pytest.raises(ValueError, match="priority flags"):
        AnchorCandidate("linux", "linux", 1.0, available="false").validate()  # type: ignore[arg-type]


def test_coverage_vector_has_independent_dimensions_and_no_aggregate() -> None:
    vector = CoverageVector(contract_coverage="COMPLETE", statistical_precision="NOT_VERIFIED")
    payload = vector.as_dict()
    assert payload["contract_coverage"] == "COMPLETE"
    assert payload["statistical_precision"] == "NOT_VERIFIED"
    assert payload["aggregate"] is None
    with pytest.raises(ValueError, match="coverage vector"):
        CoverageVector(contract_coverage="99%").validate()
