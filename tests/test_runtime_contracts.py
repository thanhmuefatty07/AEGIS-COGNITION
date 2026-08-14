from __future__ import annotations

import aegis_cognition.runtime as runtime
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
