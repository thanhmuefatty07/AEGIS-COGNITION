"""Session state and deterministic requirement compilation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Iterable

from .contracts import (
    VerificationRequirement,
    canonical_hash,
)


class VerificationSessionError(RuntimeError):
    """Raised when a session transition would violate verification policy."""


class SessionState(StrEnum):
    CREATED = "CREATED"
    CONTRACT_READY = "CONTRACT_READY"
    PACKET_READY = "PACKET_READY"
    ACTIVE = "ACTIVE"
    FEEDBACK_READY = "FEEDBACK_READY"
    DEEP_RUN_REQUESTED = "DEEP_RUN_REQUESTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


@dataclass(frozen=True)
class CompiledRequirements:
    requirements: tuple[VerificationRequirement, ...]
    compilation_status: str
    warnings: tuple[str, ...]


def compile_requirements(
    task_description: str,
    *,
    expected_behavior: str | None = None,
    session_id: str = "",
    source_revision: str = "UNKNOWN",
    policy_hash: str = "UNKNOWN",
) -> CompiledRequirements:
    """Compile only user-supplied facts; never invent acceptance criteria."""

    task = task_description.strip()
    expected = expected_behavior.strip() if expected_behavior is not None else ""
    if not task:
        return CompiledRequirements((), "INCOMPLETE", ("task_description_missing",))
    if not expected:
        requirement = VerificationRequirement(
            session_id=session_id,
            source_revision=source_revision,
            policy_hash=policy_hash,
            requirement_id=f"REQ-{canonical_hash({'task': task})[:16]}",
            text=task,
            category="functional",
            criticality="unknown",
            acceptance_conditions=(),
            required_test_classes=("regression",),
            compilation_status="INCOMPLETE",
        )
        return CompiledRequirements((requirement,), "INCOMPLETE", ("expected_behavior_missing",))
    requirement = VerificationRequirement(
        session_id=session_id,
        source_revision=source_revision,
        policy_hash=policy_hash,
        requirement_id=f"REQ-{canonical_hash({'task': task, 'expected': expected})[:16]}",
        text=expected,
        category="functional",
        criticality="normal",
        acceptance_conditions=(expected,),
        required_test_classes=("regression", "negative_control"),
        compilation_status="READY",
    )
    return CompiledRequirements((requirement,), "READY", ())


def validate_change_paths(paths: Iterable[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in paths:
        if type(raw) is not str or not raw.strip():
            raise VerificationSessionError("changed path must be a non-empty string")
        value = raw.replace("\\", "/").strip()
        if value.startswith("/") or value.startswith("../") or "/../" in f"/{value}/" or value == "..":
            raise VerificationSessionError("changed path escapes the workspace")
        normalized.add(value)
    return tuple(sorted(normalized))


__all__ = [
    "CompiledRequirements",
    "SessionState",
    "VerificationSessionError",
    "compile_requirements",
    "validate_change_paths",
]
