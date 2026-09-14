"""Versioned, hash-bound AESE verification contracts.

The contracts are intentionally independent of a model, UI, subprocess or
cloud provider.  They are the compatibility boundary shared by agent
runtime, desktop, CLI and a future API.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Self, cast
from collections.abc import Mapping


SCHEMA_VERSION = "aese.verification.v1"
UNKNOWN = "UNKNOWN"


def _empty_string_map() -> dict[str, str]:
    return {}


class ContractValidationError(ValueError):
    """Raised when a verification contract cannot be trusted."""


def _canonicalize(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractValidationError("non-finite numbers are not valid contract values")
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): _canonicalize(item) for key, item in sorted(mapping.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        items = cast(tuple[object, ...] | list[object], value)
        return [_canonicalize(item) for item in items]
    if hasattr(value, "as_dict"):
        return _canonicalize(cast(Any, value).as_dict())
    raise ContractValidationError(f"unsupported contract value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _non_empty(value: object, name: str) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ContractValidationError(f"{name} must be a bounded non-empty string")
    return value


def _tuple_strings(value: object, name: str) -> tuple[str, ...]:
    if type(value) not in (tuple, list):
        raise ContractValidationError(f"{name} must be a canonical sequence")
    typed = tuple(cast(tuple[object, ...] | list[object], value))
    if any(type(item) is not str or not item.strip() or "\x00" in item for item in typed):
        raise ContractValidationError(f"{name} must contain non-empty strings")
    return cast(tuple[str, ...], typed)


def _non_negative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractValidationError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, kw_only=True)
class ContractMetadata:
    schema_version: str = SCHEMA_VERSION
    session_id: str = ""
    source_revision: str = UNKNOWN
    contract_hash: str = ""
    plan_hash: str = ""
    policy_hash: str = UNKNOWN
    attempt: int = 0
    generation: int = 0
    status: str = "DRAFT"
    error_code: str | None = None
    error_codes: tuple[str, ...] = ()

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes",)

    def __post_init__(self) -> None:
        if not self.contract_hash:
            payload = asdict(self)
            payload.pop("contract_hash", None)
            if not self.plan_hash:
                plan_payload = dict(payload)
                plan_payload.pop("plan_hash", None)
                object.__setattr__(self, "plan_hash", canonical_hash(plan_payload))
                payload["plan_hash"] = self.plan_hash
            object.__setattr__(self, "contract_hash", canonical_hash(payload))

    def _payload(self) -> dict[str, object]:
        payload = cast(dict[str, object], asdict(self))
        payload.pop("contract_hash", None)
        return payload

    def _validate_common(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ContractValidationError("unknown contract schema version")
        if type(self.session_id) is not str or "\x00" in self.session_id:
            raise ContractValidationError("session_id is invalid")
        _non_empty(self.source_revision, "source_revision")
        _non_empty(self.plan_hash, "plan_hash")
        _non_empty(self.policy_hash, "policy_hash")
        _non_negative_int(self.attempt, "attempt")
        _non_negative_int(self.generation, "generation")
        _non_empty(self.status, "status")
        if self.error_code is not None:
            _non_empty(self.error_code, "error_code")
        _tuple_strings(self.error_codes, "error_codes")
        if type(self.contract_hash) is not str or self.contract_hash != canonical_hash(self._payload()):
            raise ContractValidationError("contract hash mismatch")

    def validate(self) -> None:
        self._validate_common()

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return cast(dict[str, object], _canonicalize(asdict(self)))

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if type(value) is not dict:
            raise ContractValidationError("contract must be a dictionary")
        payload = dict(cast(dict[str, object], value))
        expected = {field.name for field in fields(cls)}
        if set(payload) != expected:
            raise ContractValidationError("contract schema keys are invalid")
        for name in cls._tuple_fields:
            raw = payload.get(name)
            if type(raw) is not list:
                raise ContractValidationError(f"{name} must be a canonical list on the wire")
            payload[name] = tuple(cast(list[object], raw))
        item = cls(**cast(Any, payload))
        item.validate()
        return item


@dataclass(frozen=True, kw_only=True)
class ProjectProfile(ContractMetadata):
    project_id: str = ""
    root_path: str = ""
    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    test_roots: tuple[str, ...] = ()
    build_roots: tuple[str, ...] = ()
    generated_sources: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()
    ci_files: tuple[str, ...] = ()
    ffi_boundaries: tuple[str, ...] = ()
    dirty_workspace: bool = False
    dynamic_dependencies: bool = False
    discovery_status: str = "PASSIVE_COMPLETE"

    _tuple_fields: ClassVar[tuple[str, ...]] = (
        "error_codes",
        "languages",
        "frameworks",
        "manifests",
        "test_roots",
        "build_roots",
        "generated_sources",
        "lockfiles",
        "ci_files",
        "ffi_boundaries",
    )

    def validate(self) -> None:
        self._validate_common()
        _non_empty(self.project_id, "project_id")
        _non_empty(self.root_path, "root_path")
        for name in self._tuple_fields[1:]:
            _tuple_strings(getattr(self, name), name)
        if type(self.dirty_workspace) is not bool or type(self.dynamic_dependencies) is not bool:
            raise ContractValidationError("project discovery flags are invalid")
        _non_empty(self.discovery_status, "discovery_status")


@dataclass(frozen=True, kw_only=True)
class VerificationRequirement(ContractMetadata):
    requirement_id: str = ""
    text: str = ""
    category: str = "functional"
    criticality: str = "normal"
    acceptance_conditions: tuple[str, ...] = ()
    required_test_classes: tuple[str, ...] = ()
    compilation_status: str = "INCOMPLETE"

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes", "acceptance_conditions", "required_test_classes")

    def validate(self) -> None:
        self._validate_common()
        for name in ("requirement_id", "text", "category", "criticality", "compilation_status"):
            _non_empty(getattr(self, name), name)
        _tuple_strings(self.acceptance_conditions, "acceptance_conditions")
        _tuple_strings(self.required_test_classes, "required_test_classes")


@dataclass(frozen=True, kw_only=True)
class TestDescriptor(ContractMetadata):
    test_id: str = ""
    path: str = ""
    framework: str = ""
    adapter: str = ""
    kind: str = "unit"
    executable: str = ""
    argv: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    critical: bool = False
    discovery_status: str = "DISCOVERED"

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes", "argv", "requirement_ids")

    def validate(self) -> None:
        self._validate_common()
        for name in ("test_id", "path", "framework", "adapter", "kind", "executable", "discovery_status"):
            _non_empty(getattr(self, name), name)
        _tuple_strings(self.argv, "argv")
        _tuple_strings(self.requirement_ids, "requirement_ids")
        if type(self.critical) is not bool:
            raise ContractValidationError("critical must be a boolean")


@dataclass(frozen=True, kw_only=True)
class VerificationPlan(ContractMetadata):
    plan_id: str = ""
    requirement_ids: tuple[str, ...] = ()
    test_ids: tuple[str, ...] = ()
    required_test_classes: tuple[str, ...] = ()
    selection_mode: str = "SHADOW_ONLY"
    widened: bool = True
    widening_reasons: tuple[str, ...] = ()
    risk_level: str = "UNKNOWN"
    execution_policy: str = "LOCAL_DEFAULT_LAB_AUTHORITY"

    _tuple_fields: ClassVar[tuple[str, ...]] = (
        "error_codes",
        "requirement_ids",
        "test_ids",
        "required_test_classes",
        "widening_reasons",
    )

    def validate(self) -> None:
        self._validate_common()
        _non_empty(self.plan_id, "plan_id")
        for name in ("requirement_ids", "test_ids", "required_test_classes", "widening_reasons"):
            _tuple_strings(getattr(self, name), name)
        if self.selection_mode != "SHADOW_ONLY":
            raise ContractValidationError("selection authority is disabled in the initial AESE slice")
        if type(self.widened) is not bool:
            raise ContractValidationError("widened must be a boolean")
        _non_empty(self.risk_level, "risk_level")
        _non_empty(self.execution_policy, "execution_policy")


@dataclass(frozen=True, kw_only=True)
class ArtifactReference(ContractMetadata):
    artifact_id: str = ""
    kind: str = ""
    uri: str = ""
    artifact_hash: str = ""
    size_bytes: int = 0
    media_type: str = "application/octet-stream"
    redacted: bool = True

    def validate(self) -> None:
        self._validate_common()
        for name in ("artifact_id", "kind", "uri", "artifact_hash", "media_type"):
            _non_empty(getattr(self, name), name)
        if len(self.artifact_hash) != 64 or any(char not in "0123456789abcdef" for char in self.artifact_hash):
            raise ContractValidationError("artifact_hash must be a lowercase SHA-256 digest")
        _non_negative_int(self.size_bytes, "size_bytes")
        if type(self.redacted) is not bool:
            raise ContractValidationError("redacted must be a boolean")


@dataclass(frozen=True, kw_only=True)
class ExecutionReceipt(ContractMetadata):
    run_id: str = ""
    adapter: str = ""
    framework: str = ""
    toolchain: str = UNKNOWN
    platform: str = UNKNOWN
    executable: str = ""
    argv: tuple[str, ...] = ()
    working_directory: str = ""
    environment_fingerprint: Mapping[str, str] = field(default_factory=_empty_string_map)
    resource_class: str = "LOCAL_BOUNDED"
    timeout_seconds: float = 0.0
    cancellation_requested: bool = False
    exit_code: int | None = None
    exit_semantics: str = "UNKNOWN"
    discovered: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    filtered: int = 0
    ignored: int = 0
    artifact_ids: tuple[str, ...] = ()
    stdout_reference: str = ""
    stderr_reference: str = ""
    started_at: str = ""
    finished_at: str = ""

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes", "argv", "artifact_ids")

    def validate(self) -> None:
        self._validate_common()
        for name in (
            "run_id",
            "adapter",
            "framework",
            "toolchain",
            "platform",
            "executable",
            "working_directory",
            "resource_class",
            "exit_semantics",
            "stdout_reference",
            "stderr_reference",
            "started_at",
            "finished_at",
        ):
            _non_empty(getattr(self, name), name)
        _tuple_strings(self.argv, "argv")
        _tuple_strings(self.artifact_ids, "artifact_ids")
        if not isinstance(cast(object, self.environment_fingerprint), Mapping):
            raise ContractValidationError("environment_fingerprint must be a mapping")
        if any(type(key) is not str or type(value) is not str for key, value in self.environment_fingerprint.items()):
            raise ContractValidationError("environment_fingerprint must contain strings")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ContractValidationError("timeout_seconds must be finite and positive")
        if type(self.cancellation_requested) is not bool:
            raise ContractValidationError("cancellation_requested must be a boolean")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ContractValidationError("exit_code must be an integer or null")
        for name in ("discovered", "passed", "failed", "skipped", "filtered", "ignored"):
            _non_negative_int(getattr(self, name), name)
        if self.passed + self.failed + self.skipped + self.filtered + self.ignored > self.discovered:
            raise ContractValidationError("receipt result counts exceed discovered tests")
        if self.status not in {"PENDING", "PASS", "FAIL", "ZERO_TESTS", "MALFORMED", "TIMEOUT", "CANCELLED", "ERROR"}:
            raise ContractValidationError("execution receipt status is invalid")
        if self.status == "PASS" and self.discovered == 0:
            raise ContractValidationError("zero tests cannot produce a PASS receipt")


@dataclass(frozen=True, kw_only=True)
class TestChangeProposal(ContractMetadata):
    proposal_id: str = ""
    base_revision: str = UNKNOWN
    patch_text: str = ""
    changed_paths: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    operation: str = "ADD_OR_MODIFY_TEST"
    oracle_present: bool = False
    weakening_detected: bool = False
    status: str = "CANDIDATE"
    author: str = "AESE"

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes", "changed_paths", "requirement_ids")

    def validate(self) -> None:
        self._validate_common()
        for name in ("proposal_id", "base_revision", "operation", "author"):
            _non_empty(getattr(self, name), name)
        if type(self.patch_text) is not str or not self.patch_text.strip():
            raise ContractValidationError("patch_text must be non-empty")
        _tuple_strings(self.changed_paths, "changed_paths")
        _tuple_strings(self.requirement_ids, "requirement_ids")
        if type(self.oracle_present) is not bool or type(self.weakening_detected) is not bool:
            raise ContractValidationError("test proposal safety flags are invalid")
        if self.status not in {"CANDIDATE", "REJECTED", "APPLIED"}:
            raise ContractValidationError("test proposal status is invalid")


@dataclass(frozen=True, kw_only=True)
class VerificationAssessment(ContractMetadata):
    assessment_id: str = ""
    run_id: str = ""
    requirement_status: Mapping[str, str] = field(default_factory=_empty_string_map)
    receipt_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    final_assurance: bool = False
    provisional: bool = True
    failure_reasons: tuple[str, ...] = ()

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes", "receipt_ids", "artifact_ids", "failure_reasons")

    def validate(self) -> None:
        self._validate_common()
        for name in ("assessment_id", "run_id"):
            _non_empty(getattr(self, name), name)
        if not isinstance(cast(object, self.requirement_status), Mapping):
            raise ContractValidationError("requirement_status must be a mapping")
        if any(type(key) is not str or type(value) is not str for key, value in self.requirement_status.items()):
            raise ContractValidationError("requirement_status must contain strings")
        _tuple_strings(self.receipt_ids, "receipt_ids")
        _tuple_strings(self.artifact_ids, "artifact_ids")
        _tuple_strings(self.failure_reasons, "failure_reasons")
        if type(self.final_assurance) is not bool or type(self.provisional) is not bool:
            raise ContractValidationError("assessment assurance flags are invalid")
        if self.final_assurance and self.provisional:
            raise ContractValidationError("final assurance cannot be provisional")
        if self.final_assurance and not self.receipt_ids:
            raise ContractValidationError("final assurance requires at least one receipt")


@dataclass(frozen=True, kw_only=True)
class DevelopmentVerificationSession(ContractMetadata):
    session_id: str = ""
    project_id: str = ""
    baseline_revision: str = UNKNOWN
    current_revision: str = UNKNOWN
    requirement_ids: tuple[str, ...] = ()
    plan_id: str = ""
    state: str = "CREATED"
    event_cursor: int = 0
    feedback_ids: tuple[str, ...] = ()
    latest_assessment_id: str | None = None

    _tuple_fields: ClassVar[tuple[str, ...]] = ("error_codes", "requirement_ids", "feedback_ids")

    def validate(self) -> None:
        self._validate_common()
        for name in ("session_id", "project_id", "baseline_revision", "current_revision", "plan_id"):
            _non_empty(getattr(self, name), name)
        _tuple_strings(self.requirement_ids, "requirement_ids")
        _tuple_strings(self.feedback_ids, "feedback_ids")
        _non_negative_int(self.event_cursor, "event_cursor")
        if self.state not in {
            "CREATED",
            "CONTRACT_READY",
            "PACKET_READY",
            "ACTIVE",
            "FEEDBACK_READY",
            "DEEP_RUN_REQUESTED",
            "COMPLETED",
            "CANCELLED",
            "BLOCKED",
            "STALE",
        }:
            raise ContractValidationError("session state is invalid")
        if self.latest_assessment_id is not None:
            _non_empty(self.latest_assessment_id, "latest_assessment_id")


@dataclass(frozen=True, kw_only=True)
class VerificationEvent(ContractMetadata):
    event_id: str = ""
    cursor: int = 0
    event_type: str = ""
    payload_hash: str = ""
    detail: str = ""

    def validate(self) -> None:
        self._validate_common()
        _non_empty(self.event_id, "event_id")
        _non_negative_int(self.cursor, "cursor")
        _non_empty(self.event_type, "event_type")
        _non_empty(self.payload_hash, "payload_hash")
        _non_empty(self.detail, "detail")


def rebind(contract: ContractMetadata, **changes: object) -> ContractMetadata:
    """Return a changed contract with its self-hash recomputed."""

    from dataclasses import replace

    return replace(contract, **changes, contract_hash="", plan_hash=changes.get("plan_hash", ""))


__all__ = [
    "SCHEMA_VERSION",
    "ArtifactReference",
    "ContractMetadata",
    "ContractValidationError",
    "DevelopmentVerificationSession",
    "ExecutionReceipt",
    "ProjectProfile",
    "TestChangeProposal",
    "TestDescriptor",
    "VerificationAssessment",
    "VerificationEvent",
    "VerificationPlan",
    "VerificationRequirement",
    "canonical_hash",
    "canonical_json",
    "rebind",
]
