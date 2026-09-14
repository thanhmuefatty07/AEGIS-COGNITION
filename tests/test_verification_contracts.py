from __future__ import annotations

import math

import pytest

from aegis_cognition.verification import (
    ContractValidationError,
    ExecutionReceipt,
    ProjectProfile,
    VerificationRequirement,
)


def test_contract_round_trip_preserves_hash_and_rejects_tampering() -> None:
    requirement = VerificationRequirement(
        session_id="session-1",
        source_revision="abc123",
        policy_hash="policy-1",
        requirement_id="REQ-1",
        text="the feature returns the documented value",
        acceptance_conditions=("result equals 3",),
        required_test_classes=("regression",),
        compilation_status="READY",
    )
    payload = requirement.as_dict()
    restored = VerificationRequirement.from_dict(payload)
    assert restored == requirement
    tampered = {**payload, "text": "different requirement"}
    with pytest.raises(ContractValidationError, match="hash mismatch"):
        VerificationRequirement.from_dict(tampered)


def test_contract_rejects_nonfinite_values() -> None:
    profile = ProjectProfile(
        project_id="project-1",
        root_path="C:/workspace",
        source_revision="abc123",
        policy_hash="policy-1",
    )
    payload = profile.as_dict()
    payload["root_path"] = math.nan
    with pytest.raises(ContractValidationError):
        ProjectProfile.from_dict(payload)


def test_plan_selection_is_shadow_only() -> None:
    from aegis_cognition.verification.contracts import VerificationPlan

    plan = VerificationPlan(
        session_id="session-1",
        source_revision="abc123",
        policy_hash="policy-1",
        plan_id="plan-1",
    )
    assert plan.selection_mode == "SHADOW_ONLY"
    with pytest.raises(ContractValidationError, match="hash mismatch"):
        VerificationPlan.from_dict({**plan.as_dict(), "selection_mode": "AUTHORIZED"})
    unauthorized = VerificationPlan(
        session_id="session-1",
        source_revision="abc123",
        policy_hash="policy-1",
        plan_id="plan-1",
        selection_mode="AUTHORIZED",
    )
    with pytest.raises(ContractValidationError, match="selection authority"):
        unauthorized.validate()


def test_execution_receipt_rejects_zero_tests_and_requires_structured_metadata() -> None:
    receipt = ExecutionReceipt(
        session_id="session-1",
        source_revision="abc123",
        policy_hash="policy-1",
        status="PASS",
        run_id="run-1",
        adapter="python-pytest",
        framework="pytest",
        executable="python",
        argv=("-m", "pytest"),
        working_directory="C:/workspace",
        environment_fingerprint={"PYTHONHASHSEED": "0"},
        timeout_seconds=30.0,
        discovered=0,
        stdout_reference="artifact://stdout",
        stderr_reference="artifact://stderr",
        started_at="2026-09-14T00:00:00Z",
        finished_at="2026-09-14T00:00:01Z",
    )
    with pytest.raises(ContractValidationError, match="zero tests"):
        receipt.validate()
