from __future__ import annotations

import aegis_cognition.runtime as runtime
import pytest
from aegis_cognition.metrics import RuntimeMetrics
from aegis_cognition.observability import CorrelationContext, RuntimeTelemetry
from aegis_cognition.runtime import (
    RESOURCE_CONTRACT_SCHEMA_V1,
    admission_preview,
    hardware_profile,
    resource_contract_version,
    submit_runtime_task,
)


def test_python_runtime_exposes_versioned_contract_without_overclaiming() -> None:
    profile = hardware_profile()
    assert profile["schema"] == RESOURCE_CONTRACT_SCHEMA_V1
    assert profile["cpu"]["usable_parallelism"] >= 1
    assert resource_contract_version() == RESOURCE_CONTRACT_SCHEMA_V1


def test_python_does_not_duplicate_authoritative_admission_when_native_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)
    result = admission_preview({})
    assert result["status"] == "native_unavailable"
    assert result["authoritative"] is False
    assert result["verification"].startswith("NOT VERIFIED")


def test_python_task_submission_is_fail_closed_without_native_runtime(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)
    result = submit_runtime_task(1, {})
    assert result["status"] == "native_unavailable"
    assert result["authoritative"] is False


def test_python_runtime_telemetry_preserves_one_correlation_chain() -> None:
    telemetry = RuntimeTelemetry(capacity=2)
    correlation = CorrelationContext(
        mission_id="mission-test",
        task_id=1,
        run_id=2,
        attempt_id=3,
        lease_id=4,
    )

    telemetry.emit("agent", "run_started", correlation=correlation)
    telemetry.emit("provider", "request_succeeded", correlation=correlation)
    telemetry.emit("evidence", "commit_completed", correlation=correlation)

    snapshot = telemetry.snapshot()
    assert snapshot["schema"] == "aegis-runtime-telemetry-v1"
    assert snapshot["dropped"] == 1
    assert len(snapshot["events"]) == 2
    assert all(event["correlation"] == correlation.as_mapping() for event in snapshot["events"])


def test_python_runtime_telemetry_rejects_invalid_correlation() -> None:
    telemetry = RuntimeTelemetry()
    with pytest.raises(ValueError, match="task_id"):
        telemetry.emit(
            "agent",
            "run_started",
            correlation={"mission_id": "mission", "task_id": 0, "run_id": 1, "attempt_id": 1},
        )


def test_runtime_metrics_provide_prometheus_surface() -> None:
    metrics = RuntimeMetrics()
    metrics.increment("runtime.agent.runs")
    metrics.observe_ms("runtime.agent.duration", 1.5)
    output = metrics.prometheus()
    assert "aegis_runtime_agent_runs_total 1" in output
    assert "aegis_runtime_agent_duration_count 1" in output
