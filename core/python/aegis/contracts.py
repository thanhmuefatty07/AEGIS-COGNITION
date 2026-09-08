"""Typed DTOs crossing the Python gateway boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HotCommitRecord:
    schema: str
    truth_claim: bool
    verifier: str
    byte_len: int
    artifact_hash: str
    storage_ref_hash: str
    trust_level: str
    admission: str
    physical_witness_required: bool
    fail_closed: bool
    handle_valid: bool
    degraded_reason: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> HotCommitRecord:
        return cls(
            schema=str(data["schema"]),
            truth_claim=bool(data["truth_claim"]),
            verifier=str(data["verifier"]),
            byte_len=int(data["byte_len"]),
            artifact_hash=str(data["artifact_hash"]),
            storage_ref_hash=str(data["storage_ref_hash"]),
            trust_level=str(data["trust_level"]),
            admission=str(data["admission"]),
            physical_witness_required=bool(data["physical_witness_required"]),
            fail_closed=bool(data["fail_closed"]),
            handle_valid=bool(data["handle_valid"]),
            degraded_reason=data.get("degraded_reason"),
        )


class ProviderRateLimitError(RuntimeError):
    """Transient provider throttling that may be retried or routed around."""


@dataclass(frozen=True)
class TrustPolicySnapshot:
    schema: str
    truth_claim: bool
    trust_level: str
    physical_witness_required: bool
    fail_closed: bool
    degraded_hot_evidence_allowed: bool
    rust_extension_required: bool
    missing_artifact_policy: str
    dual_approval_required: bool
    trust_policy_hash: str


@dataclass(frozen=True)
class ProviderBudgetRecord:
    schema: str
    truth_claim: bool
    provider: str
    remaining_requests: int
    remaining_tokens: int
    reset_epoch_ms: int
    admitted: bool
    rejection_reason: str
    budget_hash: str


@dataclass(frozen=True)
class ProviderBudgetEvidence:
    schema: str
    truth_claim: bool
    trust_level: str
    budgeted: bool
    required_tokens: int
    provider_count: int
    admitted_provider_count: int
    skipped_providers: tuple[str, ...]
    throttled_provider_count: int
    selected_provider: str | None
    feedback_kind: str
    budget_ledger_hash: str
    budget_evidence_hash: str
    budgets: tuple[ProviderBudgetRecord, ...]


@dataclass(frozen=True)
class ProviderRouteRecord:
    schema: str
    truth_claim: bool
    trust_level: str
    selected_provider: str | None
    attempted_providers: tuple[str, ...]
    throttled_providers: tuple[str, ...]
    fallback_used: bool
    downgraded_model: bool
    throttled_provider_count: int
    provider_budget_hash: str
    route_hash: str
    egress_denied_providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class AegisRunResult:
    schema: str
    task: str
    output: Any
    hot_commit: HotCommitRecord
    trust_level: str
    trust_policy: TrustPolicySnapshot
    provider: str | None
    provider_route: ProviderRouteRecord
    provider_budget: ProviderBudgetEvidence
    truth_claim: bool
    correlation: dict[str, Any] | None = None


@dataclass(frozen=True)
class AegisBrowserActionResult:
    schema: str
    capture: Any
    hot_evidence: Any
    trust_level: str
    trust_policy: TrustPolicySnapshot
    no_file_roundtrip_on_hot_path: bool
    browser_action_result_hash: str
    truth_claim: bool


@dataclass(frozen=True)
class AegisBrowserHotFirstActionResult:
    schema: str
    capture: Any
    hot_evidence: Any
    trust_level: str
    trust_policy: TrustPolicySnapshot
    no_file_roundtrip_on_hot_path: bool
    hot_returned_before_cold_publish: bool
    browser_action_result_hash: str
    truth_claim: bool


__all__ = [
    "AegisBrowserActionResult",
    "AegisBrowserHotFirstActionResult",
    "AegisRunResult",
    "HotCommitRecord",
    "ProviderBudgetEvidence",
    "ProviderBudgetRecord",
    "ProviderRateLimitError",
    "ProviderRouteRecord",
    "TrustPolicySnapshot",
]
