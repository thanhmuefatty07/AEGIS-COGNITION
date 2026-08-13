from __future__ import annotations

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


def test_python_does_not_duplicate_authoritative_admission_when_native_is_missing() -> None:
    result = admission_preview({})
    if result.get("status") == "native_unavailable":
        assert result["authoritative"] is False
        assert result["verification"].startswith("NOT VERIFIED")


def test_python_task_submission_is_fail_closed_without_native_runtime() -> None:
    result = submit_runtime_task(1, {})
    if result.get("status") == "native_unavailable":
        assert result["authoritative"] is False
