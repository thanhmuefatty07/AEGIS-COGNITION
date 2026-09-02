"""Agent laboratory orchestration with provenance-first records.

This module is deliberately an adapter-level coordinator. The Rust ``lab``
kernel owns the same invariants for authoritative consumers; Python keeps the
developer-facing hooks for search-as-code, browser actions and experiments.
Missing hooks become explicit blockers in the dossier instead of silently
turning a one-shot answer into a research claim.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import ipaddress
import json
import math
import multiprocessing
import os
import re
import socket
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import blake2b
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, cast
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from blake3 import blake3

from .benchmark import BenchmarkProtocolV2, EnvironmentFingerprint, evaluate_benchmark
from .config import trust_policy_hash as _canonical_trust_policy_hash


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return blake2b(payload, digest_size=32).hexdigest()


def _is_finite_number(value: Any) -> bool:
    if type(value) not in (int, float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


_LAB_TRUST_LEVELS = frozenset({"DEV", "STAGING", "PROD"})


class AuthorityMode(StrEnum):
    """Explicitly label whether a Lab run has native authority.

    ``PROJECTION_ONLY`` is the compatibility/development path, while
    ``NATIVE_ADMITTED`` uses the native controller when available without
    turning its absence into a production claim.  ``NATIVE_REQUIRED`` is the
    fail-closed path used by production policy.
    """

    PROJECTION_ONLY = "projection_only"
    NATIVE_ADMITTED = "native_admitted"
    NATIVE_REQUIRED = "native_required"


def _normalize_lab_trust_level(value: str) -> str:
    if type(value) is not str:
        raise ValueError("lab trust level must be a string")
    level = str(value).strip().upper()
    if level not in _LAB_TRUST_LEVELS:
        raise ValueError("lab trust level must be DEV, STAGING, or PROD")
    return level


def _normalize_authority_mode(value: AuthorityMode | str) -> AuthorityMode:
    if isinstance(value, AuthorityMode):
        return value
    if type(value) is not str:
        raise ValueError(
            "lab authority mode must be projection_only, native_admitted, or native_required"
        )
    normalized = str(value).strip().upper().replace("-", "_")
    try:
        return AuthorityMode[normalized]
    except KeyError:
        try:
            return AuthorityMode(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError(
                "lab authority mode must be projection_only, native_admitted, or native_required"
            ) from exc


def _browser_policy_matches(value: Any, expected: Mapping[str, Any]) -> bool:
    """Compare a caller-supplied policy without allowing structural drift."""

    if not isinstance(value, Mapping):
        return False
    typed_value = cast(Mapping[str, Any], value)
    if set(typed_value) != set(expected):
        return False
    raw_allowed_hosts = typed_value.get("allowed_hosts")
    if isinstance(raw_allowed_hosts, str) or not isinstance(raw_allowed_hosts, (list, tuple)):
        return False
    allowed_hosts = cast(list[Any] | tuple[Any, ...], raw_allowed_hosts)
    if any(type(host) is not str for host in allowed_hosts):
        return False
    normalized_hosts = tuple(host for host in allowed_hosts)
    return (
        normalized_hosts == tuple(cast(tuple[str, ...], expected["allowed_hosts"]))
        and typed_value.get("require_https") == expected["require_https"]
        and typed_value.get("max_actions") == expected["max_actions"]
        and typed_value.get("max_observations") == expected["max_observations"]
    )


def _authority_mode_from_options(
    options: Mapping[str, Any], *, default_trust_level: str
) -> AuthorityMode:
    raw_mode = options.get("lab_authority_mode")
    if raw_mode is not None:
        normalized_mode = _normalize_authority_mode(cast(AuthorityMode | str, raw_mode))
        if "lab_require_native_authority" in options:
            legacy_flag = options["lab_require_native_authority"]
            if type(legacy_flag) is not bool:
                raise ValueError("lab native-authority flag must be boolean")
            if legacy_flag != (normalized_mode is AuthorityMode.NATIVE_REQUIRED):
                raise ValueError("legacy native-authority flag conflicts with Lab authority mode")
        return normalized_mode
    raw_required = options.get(
        "lab_require_native_authority",
        _normalize_lab_trust_level(default_trust_level) == "PROD",
    )
    if type(raw_required) is not bool:
        raise ValueError("lab native-authority flag must be boolean")
    return AuthorityMode.NATIVE_REQUIRED if raw_required else AuthorityMode.PROJECTION_ONLY


def _trust_policy_hash(trust_level: str) -> str:
    """Return the canonical mission-bound subject hash."""

    return _canonical_trust_policy_hash(trust_level)


def _bounded_retry_attempts(value: Any, *, max_steps: int, label: str) -> int:
    """Validate and cap a retry count without lossy type coercion.

    Retry policy is part of the execution contract.  Accepting booleans,
    floats, or numeric strings through ``int(...)`` would make the requested
    bound differ from the admitted bound and would hide operator mistakes.
    Values above the mission step quota remain valid but are deterministically
    capped by that quota, matching the documented finite-bound policy.
    """

    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("retry policy max_steps must be a positive integer")
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} retry policy must be a positive integer")
    return min(value, max_steps)


_EXTERNAL_ADMISSION_EVENT_KINDS = frozenset(
    {
        "experiment_execution_admitted",
        "tool_execution_admitted",
        "research_program_admitted",
        "browser_action_admitted",
        "browser_observation_admitted",
        "skill_admission_recorded",
    }
)


def _default_external_attempt_budget(max_steps: int) -> int:
    """Return a finite envelope for Lab-owned admission attempts.

    The envelope covers the bounded controller/action/retry nesting known to
    this compatibility facade.  It is deliberately conservative and counts
    *admissions*, not opaque provider SDK requests, browser subrequests, or
    descendant-process effects.  Those remain separately unverified.
    """

    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("external attempt budget max_steps must be a positive integer")
    return 8 * (max_steps + 1) ** 3


def _strict_string_sequence(value: Any, *, label: str) -> tuple[str, ...]:
    """Copy a bounded string sequence without normalizing malformed values."""

    if type(value) not in (list, tuple):
        raise ValueError(f"{label} must be a sequence")
    typed_value = cast(list[Any] | tuple[Any, ...], value)
    if any(type(item) is not str for item in typed_value):
        raise ValueError(f"{label} entries must be strings")
    return tuple(typed_value)


def _validate_native_provider_options(options: Mapping[str, Any], *, llm: Any = None) -> None:
    """Reject provider metadata that a compatibility adapter would coerce.

    The friendly gateway intentionally accepts a broad compatibility surface,
    including numeric strings in its legacy budget normalizer.  A
    native-required Lab cannot let that adapter rewrite an input before the
    provider-attempt fence sees it: the admitted policy would no longer be the
    exact policy the caller supplied.  Keep this check at the Lab boundary so
    the compatibility ``Agent`` path remains unchanged.
    """

    required_tokens = options.get("required_tokens", 1)
    if type(required_tokens) is not int or required_tokens < 1:
        raise ValueError("native Lab provider required_tokens must be a positive integer")

    provider = options.get("provider")
    if provider is not None and (
        type(provider) is not str or not provider.strip() or provider != provider.strip()
    ):
        raise ValueError("native Lab provider name must be a non-empty trimmed string")
    if provider is None:
        _validate_native_provider_identity(llm, label="primary")

    fallback_providers = options.get("fallback_providers")
    if fallback_providers is not None:
        if type(fallback_providers) not in (list, tuple):
            raise ValueError("native Lab fallback_providers must be a list or tuple")
        typed_fallbacks = cast(list[Any] | tuple[Any, ...], fallback_providers)
        for item in typed_fallbacks:
            if isinstance(item, tuple):
                typed_item = cast(tuple[Any, ...], item)
                if len(typed_item) != 2:
                    raise ValueError("native Lab fallback provider entries must be 2-item pairs")
                name = typed_item[0]
                if name is not None and (
                    type(name) is not str or not name.strip() or name != name.strip()
                ):
                    raise ValueError(
                        "native Lab fallback provider names must be null or non-empty trimmed strings"
                    )
                if name is None:
                    _validate_native_provider_identity(typed_item[1], label="fallback")
            else:
                _validate_native_provider_identity(item, label="fallback")

    provider_budgets = options.get("provider_budgets")
    if provider_budgets is None:
        return
    if isinstance(provider_budgets, Mapping):
        typed_provider_budgets = cast(Mapping[str, Any], provider_budgets)
        for provider_name, raw_budget in typed_provider_budgets.items():
            _validate_native_provider_budget_name(provider_name)
            _validate_native_provider_budget_value(
                raw_budget,
                label=str(provider_name),
                required_tokens=required_tokens,
            )
        return
    if type(provider_budgets) not in (list, tuple):
        raise ValueError("native Lab provider_budgets must be a mapping, list, or tuple")
    typed_budgets = cast(list[Any] | tuple[Any, ...], provider_budgets)
    for index, raw_budget in enumerate(typed_budgets):
        if _is_provider_budget_record(raw_budget):
            _validate_native_provider_budget_record(
                raw_budget,
                label=f"entry {index}",
                required_tokens=required_tokens,
            )
            continue
        if not isinstance(raw_budget, Mapping):
            raise ValueError("native Lab provider budget entries must be mappings")
        typed_raw_budget = cast(Mapping[str, Any], raw_budget)
        raw_keys = tuple(typed_raw_budget)
        if any(type(key) is not str for key in raw_keys):
            raise ValueError("native Lab provider budget keys must be strings")
        provider_name = typed_raw_budget.get("provider") or typed_raw_budget.get("provider_name")
        if provider_name is None:
            provider_id = typed_raw_budget.get("provider_id")
            model_name = typed_raw_budget.get("model") or typed_raw_budget.get("model_name")
            if provider_id is not None and model_name is not None:
                if type(provider_id) is not str or type(model_name) is not str:
                    raise ValueError("native Lab provider identifiers must be strings")
                provider_name = f"{provider_id}/{model_name}"
        _validate_native_provider_budget_name(provider_name)
        _validate_native_provider_budget_value(
            typed_raw_budget,
            label=f"entry {index}",
            required_tokens=required_tokens,
        )


def _validate_native_provider_budget_name(value: Any) -> None:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError("native Lab provider budget names must be non-empty trimmed strings")


def _validate_native_provider_identity(value: Any, *, label: str) -> None:
    """Prevent falsy/non-text model identifiers from becoming ``default``."""

    if value is None:
        return
    for field in ("model", "model_name"):
        candidate = getattr(value, field, None)
        if candidate is None:
            continue
        if type(candidate) is not str or not candidate.strip() or candidate != candidate.strip():
            raise ValueError(f"native Lab {label} provider identity must be a non-empty trimmed string")
        return


def _validate_native_provider_budget_value(
    value: Any,
    *,
    label: str,
    required_tokens: int,
) -> None:
    if _is_provider_budget_record(value):
        _validate_native_provider_budget_record(
            value,
            label=label,
            required_tokens=required_tokens,
        )
        return
    if isinstance(value, Mapping):
        typed_value = cast(Mapping[str, Any], value)
        keys = tuple(typed_value)
        if any(type(key) is not str for key in keys):
            raise ValueError("native Lab provider budget mapping keys must be strings")
        for field in (
            "remaining_requests",
            "requests",
            "remaining_tokens",
            "tokens",
            "reset_epoch_ms",
        ):
            if field in typed_value:
                raw = typed_value[field]
                if type(raw) is not int or raw < 0:
                    raise ValueError(f"native Lab {label} budget fields must be non-negative integers")
        return
    if isinstance(value, (tuple, list)):
        typed_value = cast(tuple[Any, ...] | list[Any], value)
        if len(typed_value) not in (2, 3):
            raise ValueError("native Lab provider budget values must be mappings or 2/3-item sequences")
        for raw in typed_value:
            if type(raw) is not int or raw < 0:
                raise ValueError(f"native Lab {label} budget fields must be non-negative integers")
        return
    raise ValueError("native Lab provider budget values must be mappings or 2/3-item sequences")


def _is_provider_budget_record(value: Any) -> bool:
    try:
        from core.python.aegis.contracts import ProviderBudgetRecord
    except ImportError:
        from aegis.contracts import ProviderBudgetRecord  # type: ignore[import-not-found]
    return isinstance(value, ProviderBudgetRecord)


def _validate_native_provider_budget_record(
    value: Any,
    *,
    label: str,
    required_tokens: int,
) -> None:
    if (
        type(getattr(value, "schema", None)) is not str
        or value.schema != "aegis-friendly-provider-budget-record-v1"
        or type(getattr(value, "truth_claim", None)) is not bool
        or value.truth_claim is not False
        or type(getattr(value, "admitted", None)) is not bool
        or type(getattr(value, "rejection_reason", None)) is not str
        or type(getattr(value, "budget_hash", None)) is not str
        or not _is_digest(value.budget_hash)
    ):
        raise ValueError(f"native Lab {label} provider budget record is invalid")
    _validate_native_provider_budget_name(getattr(value, "provider", None))
    for field in ("remaining_requests", "remaining_tokens", "reset_epoch_ms"):
        raw = getattr(value, field, None)
        if type(raw) is not int or raw < 0:
            raise ValueError(f"native Lab {label} budget fields must be non-negative integers")
    remaining_requests = value.remaining_requests
    remaining_tokens = value.remaining_tokens
    expected_admitted = remaining_requests > 0 and remaining_tokens >= required_tokens
    expected_reason = (
        ""
        if expected_admitted
        else "remaining_requests_exhausted"
        if remaining_requests <= 0
        else "remaining_tokens_insufficient"
    )
    if (
        value.admitted is not expected_admitted
        or value.rejection_reason != expected_reason
    ):
        raise ValueError(f"native Lab {label} provider budget admission is inconsistent")


def _is_disallowed_ip_literal(host: str) -> bool:
    """Reject address literals that cannot be safe browser egress targets."""

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # ``inet_aton`` accepts legacy decimal/octal/hex IPv4 spellings that
        # ``ipaddress`` intentionally rejects.  Normalize those spellings so
        # an SSRF policy cannot be bypassed by an obfuscated loopback/private
        # destination.  Hostnames still return ``False`` here and require the
        # separate DNS/egress enforcement boundary.
        try:
            address = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except (OSError, ValueError):
            return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _assert_research_host_egress(host: str, *, port: int = 443) -> None:
    """Reject literal or currently resolved private destinations before fetch.

    This is a preflight check, not a claim of complete DNS-rebinding
    protection: the socket can still race a later DNS answer and requires an
    OS/network egress boundary for a stronger guarantee.
    """

    normalized_host = host.strip().lower().rstrip(".")
    if not normalized_host:
        raise ValueError("research fetch host is empty")
    if _is_disallowed_ip_literal(normalized_host):
        raise ValueError("research fetch rejects private IP destinations")
    try:
        addresses = {
            str(info[4][0])
            for info in socket.getaddrinfo(
                normalized_host,
                port,
                type=socket.SOCK_STREAM,
            )
            if len(info) >= 5 and info[4]
        }
    except OSError as exc:
        raise ValueError("research fetch hostname resolution failed") from exc
    if not addresses:
        raise ValueError("research fetch hostname resolution returned no addresses")
    if any(_is_disallowed_ip_literal(address) for address in addresses):
        raise ValueError("research fetch hostname resolves to private IP destinations")


def _rust_canonical_hash(value: Any) -> str:
    """Match the Rust Lab canonical hash for serde-compatible values."""

    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    return blake3(b"aegis-lab-canonical-v1\0" + payload).hexdigest()


def _native_projection_payload_hash(value: Any) -> str:
    """Hash the canonical JSON payload accepted by native projection checks."""

    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return blake3(b"aegis-lab-projection-payload-v1\0" + payload).hexdigest()


def _execution_cell_manifest_hash(manifest: Any) -> str:
    """Hash the canonical manifest value embedded in its binding event."""

    return _native_projection_payload_hash(manifest)


_BUDGET_FIELDS = (
    "tokens",
    "money_minor_units",
    "time_ms",
    "tool_calls",
    "api_calls",
    "cpu_ms",
    "risk_units",
)


def _budget_vector_payload(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("budget payload must be a mapping")
    mapping = cast(dict[str, Any], value)
    result: dict[str, int] = {}
    for field in _BUDGET_FIELDS:
        raw = mapping.get(field, 0)
        if type(raw) is not int or raw < 0:
            raise ValueError("budget payload values must be non-negative integers")
        result[field] = raw
    return result


_RUST_EVENT_KINDS = {
    "mission_created": "MissionCreated",
    "execution_cell_manifest_recorded": "ExecutionCellManifestRecorded",
    "state_changed": "StateChanged",
    "source_captured": "SourceCaptured",
    "claim_recorded": "ClaimRecorded",
    "hypothesis_recorded": "HypothesisRecorded",
    "experiment_scheduled": "ExperimentScheduled",
    "experiment_execution_admitted": "ExperimentExecutionAdmitted",
    "experiment_execution_recorded": "ExperimentExecutionRecorded",
    "tool_execution_admitted": "ToolExecutionAdmitted",
    "tool_execution_recorded": "ToolExecutionRecorded",
    "observation_recorded": "ObservationRecorded",
    "browser_action_admitted": "BrowserActionAdmitted",
    "browser_action_recorded": "BrowserActionRecorded",
    "browser_observation_admitted": "BrowserObservationAdmitted",
    "browser_observation_recorded": "BrowserObservationRecorded",
    "benchmark_evaluated": "BenchmarkEvaluated",
    "controller_step": "ReviewRecorded",
    "synthesis_committed": "ReviewRecorded",
    "research_program_admitted": "ResearchProgramAdmitted",
    "research_program_executed": "ResearchProgramExecuted",
    "security_event_recorded": "SecurityEventRecorded",
    "blocker_recorded": "BlockerRecorded",
    "blocker_resolved": "BlockerResolved",
    "skill_admission_recorded": "SkillAdmissionRecorded",
    "skill_execution_recorded": "SkillExecutionRecorded",
    "cancellation_admitted": "CancellationAdmitted",
    "cancellation_recorded": "CancellationRecorded",
    "budget_admitted": "BudgetAdmitted",
    "finalization_started": "FinalizationStarted",
    "finalization_reopened": "FinalizationReopened",
}

_EXECUTION_CELL_MANIFEST_SCHEMA = "aegis-execution-cell-manifest-v1"

_REPLAY_ARCHIVE_MANIFEST_SCHEMA = "aegis-run-event-segment-manifest-v1"
_REPLAY_ARCHIVE_MANIFEST_VERSION = 1


_SEARCH_OPERATION_KINDS = frozenset(
    {
        "query",
        "fetch",
        "render",
        "extract",
        "cross_check",
        "contradiction_search",
        "rank",
        "dedupe",
    }
)
_CONTROLLER_ACTION_PLAN_SCHEMA = "aegis-lab-action-plan-v1"
_CONTROLLER_ACTION_KINDS = frozenset(
    {
        "search_program",
        "tool_call",
        "browser_action",
        "experiment_action",
        "simulation_action",
    }
)
_EXECUTION_CELL_ACTION_KINDS = _CONTROLLER_ACTION_KINDS | frozenset(
    {
        "skill_execution",
        "post_completion_effect",
        "context_retrieval",
        "benchmark_validation",
    }
)
_CONTROLLER_SAFE_TOOL_EFFECTS = frozenset({"read_only", "network_read", "compute", "model_inference"})
_PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "reveal the system prompt",
    "reveal your system message",
    "send the api key",
    "exfiltrate credentials",
    "developer message above",
)
_EPISTEMIC_STATUSES = frozenset(
    {"OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "UNKNOWN", "REJECTED"}
)


@dataclass(frozen=True)
class SearchOperation:
    """A deterministic, serializable search-as-code operation."""

    kind: str
    arguments: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SearchOperation:
        if type(value) is not dict:
            raise TypeError("search operation must be a mapping")
        raw_kind = value.get("kind", "")
        if type(raw_kind) is not str:
            raise ValueError("search operation kind must be a string")
        kind = raw_kind.strip().lower()
        if any(type(key) is not str for key in value if key != "kind"):
            raise ValueError("search operation argument keys must be strings")
        if any(type(item) is not str for key, item in value.items() if key != "kind"):
            raise ValueError("search operation arguments must be strings")
        arguments = tuple(
            sorted(
                (key, item)
                for key, item in value.items()
                if key != "kind"
            )
        )
        operation = cls(kind=kind, arguments=arguments)
        operation.validate()
        return operation

    def validate(self) -> None:
        if type(self.kind) is not str or self.kind not in _SEARCH_OPERATION_KINDS:
            raise ValueError(f"unsupported search operation kind: {self.kind}")
        if type(self.arguments) not in (list, tuple):
            raise ValueError("search operation arguments must be a sequence")
        if any(
            type(argument) not in (list, tuple)
            or len(argument) != 2
            or type(argument[0]) is not str
            or type(argument[1]) is not str
            for argument in self.arguments
        ):
            raise ValueError("search operation arguments must be string pairs")
        keys = [key for key, _ in self.arguments]
        if len(keys) != len(set(keys)):
            raise ValueError("search operation arguments must have unique keys")
        argument_map = dict(self.arguments)
        if self.kind in {"query", "cross_check", "contradiction_search", "rank"} and not argument_map.get("text", "").strip():
            raise ValueError(f"{self.kind} operation requires non-empty text")
        if self.kind == "fetch":
            target = argument_map.get("url", "")
            parsed = urlparse(target)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("fetch operation requires a credential-free HTTPS URL")
        if self.kind == "extract" and not argument_map.get("selector", "").strip():
            raise ValueError("extract operation requires a selector")


@dataclass(frozen=True)
class SearchProgram:
    """Typed research program; providers remain replaceable edge adapters."""

    operations: tuple[SearchOperation, ...]
    max_candidates: int = 20
    allowed_hosts: tuple[str, ...] = ()
    provider: str = "unspecified"
    freshness_max_age_seconds: int | None = None
    min_independent_contradiction_clusters: int = 0

    @classmethod
    def from_mappings(
        cls,
        operations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        max_candidates: int = 20,
        allowed_hosts: tuple[str, ...] = (),
        provider: str = "unspecified",
        freshness_max_age_seconds: int | None = None,
        min_independent_contradiction_clusters: int = 0,
    ) -> SearchProgram:
        if type(operations) not in (list, tuple):
            raise TypeError("search program operations must be a sequence")
        if any(type(item) is not dict for item in operations):
            raise TypeError("search program operations must be mappings")
        program = cls(
            operations=tuple(SearchOperation.from_mapping(item) for item in operations),
            max_candidates=max_candidates,
            allowed_hosts=allowed_hosts,
            provider=provider,
            freshness_max_age_seconds=freshness_max_age_seconds,
            min_independent_contradiction_clusters=min_independent_contradiction_clusters,
        )
        program.validate()
        return program

    def validate(self) -> None:
        if type(self.operations) not in (list, tuple):
            raise ValueError("search program operations must be a sequence")
        if any(type(operation) is not SearchOperation for operation in self.operations):
            raise ValueError("search program operations must be typed")
        if not self.operations or len(self.operations) > 64:
            raise ValueError("search program requires 1..64 operations")
        if type(self.max_candidates) is not int or self.max_candidates < 1 or self.max_candidates > 10_000:
            raise ValueError("search program max_candidates must be within [1, 10000]")
        if type(self.provider) is not str or not self.provider.strip():
            raise ValueError("search program provider is required")
        if type(self.freshness_max_age_seconds) not in (int, type(None)):
            raise ValueError("search program freshness bound must be an integer or unset")
        if self.freshness_max_age_seconds is not None and self.freshness_max_age_seconds < 1:
            raise ValueError("search program freshness bound must be positive")
        if type(self.min_independent_contradiction_clusters) is not int:
            raise ValueError("search program contradiction-cluster bound must be an integer")
        if self.min_independent_contradiction_clusters < 0:
            raise ValueError("search program contradiction-cluster bound cannot be negative")
        if self.min_independent_contradiction_clusters > self.max_candidates:
            raise ValueError("search contradiction-cluster bound exceeds candidate quota")
        if type(self.allowed_hosts) not in (list, tuple):
            raise ValueError("search program allowlist hosts must be a sequence")
        if any(type(host) is not str for host in self.allowed_hosts):
            raise ValueError("search program allowlist hosts must be strings")
        normalized_hosts = tuple(host.strip().lower() for host in self.allowed_hosts)
        if any(
            not host or host != original
            for host, original in zip(normalized_hosts, self.allowed_hosts, strict=True)
        ):
            raise ValueError("search program allowlist hosts must be lowercase and trimmed")
        for operation in self.operations:
            operation.validate()
            if operation.kind == "fetch":
                target = dict(operation.arguments).get("url", "")
                host = (urlparse(target).hostname or "").lower()
                if self.allowed_hosts and host not in self.allowed_hosts:
                    raise ValueError("search fetch host is not allowlisted")

    @property
    def program_hash(self) -> str:
        self.validate()
        return _hash(asdict(self))


class _VisibleTextParser(HTMLParser):
    """Small deterministic HTML extractor used by the research boundary."""

    def __init__(self, selector: str) -> None:
        super().__init__(convert_charrefs=True)
        self.selector = selector.lower()
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == self.selector:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth > 0 and tag.lower() == self.selector:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth > 0 and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return " ".join(self._parts)


class SearchProgramExecutor:
    """Execute a typed search program against explicit provider/fetch bounds.

    The executor owns no credentials and has no implicit search engine. Query
    and contradiction operations require a caller-supplied provider adapter;
    HTTPS fetches use a bounded standard-library client. All returned content is
    untrusted data and carries content/snapshot hashes plus citation spans.
    """

    def __init__(
        self,
        *,
        query_provider: Any = None,
        timeout_seconds: float = 10.0,
        max_bytes: int = 8 * 1024 * 1024,
        user_agent: str = "aegis-lab-research/1",
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("search executor timeout must be positive and finite")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("search executor max_bytes must be positive")
        if type(user_agent) is not str or not user_agent.strip():
            raise ValueError("search executor user agent must be non-empty")
        self.query_provider = query_provider
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.last_trace: tuple[dict[str, Any], ...] = ()

    async def __call__(self, program: SearchProgram, **context: Any) -> list[dict[str, Any]]:
        return await self.execute(program, **context)

    async def execute(
        self,
        program: SearchProgram,
        *,
        task: str = "",
        run_id: str = "",
    ) -> list[dict[str, Any]]:
        program.validate()
        candidates: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        for operation in program.operations:
            arguments = dict(operation.arguments)
            trace_item: dict[str, Any] = {"kind": operation.kind, "arguments": arguments}
            if operation.kind in {"query", "cross_check", "contradiction_search"}:
                text_query = arguments.get("text", "").strip()
                raw = await self._query(text_query, task=task, run_id=run_id, operation=operation.kind)
                additions = self._normalize_candidates(raw, relation=operation.kind)
                self._admit_candidates(additions, program)
                self._enforce_freshness(additions, program)
                if operation.kind == "contradiction_search":
                    self._enforce_independent_contradictions(additions, program)
                candidates.extend(additions)
                trace_item["candidate_count"] = len(additions)
            elif operation.kind == "fetch":
                url = arguments.get("url", "")
                content, headers = await self._fetch(url, program.allowed_hosts)
                candidate = self._candidate_from_content(url, content, headers, relation="fetch")
                candidates.append(candidate)
                trace_item["candidate_count"] = 1
            elif operation.kind == "render":
                rendered: list[dict[str, Any]] = []
                for candidate in candidates:
                    if not candidate.get("content") and candidate.get("uri"):
                        raw_uri = candidate["uri"]
                        if type(raw_uri) is not str:
                            raise ValueError("render candidate URI must be a string")
                        content, headers = await self._fetch(raw_uri, program.allowed_hosts)
                        rendered.append(self._candidate_from_content(raw_uri, content, headers, relation="render"))
                    else:
                        rendered.append(candidate)
                candidates = rendered
                trace_item["candidate_count"] = len(candidates)
            elif operation.kind == "extract":
                selector = arguments.get("selector", "").strip().lower()
                if not re.fullmatch(r"[a-z][a-z0-9_-]*", selector):
                    raise ValueError("extract selector must be a simple HTML tag")
                extracted: list[dict[str, Any]] = []
                for candidate in candidates:
                    content = candidate.get("content", "")
                    if type(content) is not str:
                        raise ValueError("extract candidate content must be a string")
                    parser = _VisibleTextParser(selector)
                    parser.feed(content)
                    parser.close()
                    text = parser.text
                    if text:
                        enriched = dict(candidate)
                        enriched["extracted_text"] = text
                        enriched["citation_spans"] = ({
                            "selector": selector,
                            "start": 0,
                            "end": len(text),
                            "text_hash": _hash(text),
                        },)
                        extracted.append(enriched)
                candidates = extracted
                trace_item["candidate_count"] = len(candidates)
            elif operation.kind == "dedupe":
                unique: dict[tuple[str, str], dict[str, Any]] = {}
                for candidate in candidates:
                    uri = candidate.get("uri", "")
                    content_hash = candidate.get("content_hash", "")
                    if type(uri) is not str or type(content_hash) is not str:
                        raise ValueError("dedupe candidate identity metadata is invalid")
                    key = (uri, content_hash)
                    unique.setdefault(key, candidate)
                candidates = list(unique.values())
                trace_item["candidate_count"] = len(candidates)
            elif operation.kind == "rank":
                for candidate in candidates:
                    score = candidate.get("score", 0.0)
                    if not _is_finite_number(score):
                        raise ValueError("rank candidate score must be finite and numeric")
                candidates.sort(
                    key=lambda item: (-item.get("score", 0.0), item.get("uri", "")),
                )
                trace_item["candidate_count"] = len(candidates)
            trace.append(trace_item)
        trace_hash = _hash(trace)
        self.last_trace = tuple(trace)
        result: list[dict[str, Any]] = []
        for candidate in candidates[: program.max_candidates]:
            enriched = dict(candidate)
            enriched["program_hash"] = program.program_hash
            enriched["execution_trace_hash"] = trace_hash
            result.append(enriched)
        return result

    async def _query(self, text: str, *, task: str, run_id: str, operation: str) -> Any:
        if not callable(self.query_provider):
            raise RuntimeError(f"{operation} requires an explicit query provider adapter")
        return await asyncio.wait_for(
            _call_fenced(
                self.query_provider,
                text,
                task=task,
                run_id=run_id,
                operation=operation,
            ),
            timeout=self.timeout_seconds,
        )

    async def _fetch(self, url: str, allowed_hosts: tuple[str, ...]) -> tuple[str, dict[str, str]]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("research fetch requires a credential-free HTTPS URL")
        host = parsed.hostname.lower()
        if allowed_hosts and host not in allowed_hosts:
            raise ValueError("research fetch host is not allowlisted")
        _assert_research_host_egress(host, port=parsed.port or 443)
        request = Request(url, headers={"User-Agent": self.user_agent}, method="GET")

        def read_response() -> tuple[str, dict[str, str]]:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                final_url_getter = getattr(response, "geturl", None)
                final_url = final_url_getter() if callable(final_url_getter) else url
                if type(final_url) is not str:
                    raise ValueError("research redirect returned an invalid URL")
                final_parsed = urlparse(final_url)
                final_host = (final_parsed.hostname or "").lower()
                if (
                    final_parsed.scheme != "https"
                    or not final_host
                    or final_parsed.username
                    or final_parsed.password
                    or (allowed_hosts and final_host not in allowed_hosts)
                ):
                    raise ValueError("research redirect leaves the HTTPS host policy")
                _assert_research_host_egress(final_host, port=final_parsed.port or 443)
                payload = response.read(self.max_bytes + 1)
                if len(payload) > self.max_bytes:
                    raise ValueError("research response exceeds byte quota")
                charset = response.headers.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace"), {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }

        return await asyncio.to_thread(read_response)

    @staticmethod
    def _normalize_candidates(raw: Any, *, relation: str) -> list[dict[str, Any]]:
        if type(raw) is dict:
            raw_mapping = cast(dict[str, Any], raw)
            if "results" not in raw_mapping and "candidates" not in raw_mapping:
                raise TypeError("query provider result must contain results or candidates")
            if (
                "results" in raw_mapping
                and "candidates" in raw_mapping
                and raw_mapping["results"] != raw_mapping["candidates"]
            ):
                raise ValueError("query provider result aliases disagree")
            raw = raw_mapping.get("results", raw_mapping.get("candidates"))
        if raw is None:
            return []
        if type(raw) is str:
            raw = [raw]
        if type(raw) not in (list, tuple):
            raise TypeError("query provider must return a sequence")
        result: list[dict[str, Any]] = []
        typed_raw = cast(list[Any] | tuple[Any, ...], raw)
        for item in typed_raw:
            if type(item) is str:
                item = {"uri": item, "content": ""}
            if type(item) is not dict:
                raise TypeError("query provider candidates must be mappings or URLs")
            mapping = cast(dict[str, Any], item)
            raw_uri = mapping.get("uri", mapping.get("url"))
            if type(raw_uri) is not str or not raw_uri.strip():
                raise ValueError("query provider candidate URI must be a non-empty string")
            if "uri" in mapping and "url" in mapping and mapping["uri"] != mapping["url"]:
                raise ValueError("query provider candidate URI aliases disagree")
            content_values = [mapping[name] for name in ("content", "body", "snippet") if name in mapping]
            if any(type(value) is not str for value in content_values):
                raise ValueError("query provider candidate content must be a string")
            if content_values and any(value != content_values[0] for value in content_values[1:]):
                raise ValueError("query provider candidate content aliases disagree")
            raw_content = content_values[0] if content_values else ""
            uri = raw_uri
            content = raw_content
            content_hash = mapping.get("content_hash", _hash(content))
            snapshot_hash = mapping.get(
                "snapshot_hash", _hash({"uri": uri, "content": content})
            )
            retrieved_at_ms = mapping.get("retrieved_at_ms", int(time.time() * 1000))
            trust_tier = mapping.get("trust_tier", 1)
            extractor = mapping.get("extractor", "search-program-provider")
            source_id = mapping.get("source_id", mapping.get("id"))
            if "source_id" in mapping and "id" in mapping and mapping["source_id"] != mapping["id"]:
                raise ValueError("query provider candidate source aliases disagree")
            provenance_cluster = mapping.get("provenance_cluster", "")
            citation_spans = mapping.get("citation_spans", ())
            score = mapping.get("score")
            if (
                type(content_hash) is not str
                or content_hash != _hash(content)
                or type(snapshot_hash) is not str
                or not snapshot_hash.strip()
                or not _is_digest(snapshot_hash)
                or type(retrieved_at_ms) is not int
                or retrieved_at_ms <= 0
                or type(trust_tier) is not int
                or trust_tier < 1
                or type(extractor) is not str
                or not extractor.strip()
                or type(provenance_cluster) is not str
                or type(citation_spans) not in (list, tuple)
                or any(not _valid_citation_span(span) for span in citation_spans)
                or (source_id is not None and (type(source_id) is not str or not source_id.strip()))
                or (
                    score is not None
                    and not _is_finite_number(score)
                )
            ):
                raise ValueError("query provider candidate metadata is invalid")
            normalized = {
                **mapping,
                "uri": uri,
                "content": content,
                "relation": relation,
                "content_hash": content_hash,
                "snapshot_hash": snapshot_hash,
                "retrieved_at_ms": retrieved_at_ms,
                "trust_tier": trust_tier,
                "extractor": extractor,
                "provenance_cluster": provenance_cluster,
                "citation_spans": citation_spans,
            }
            if source_id is not None:
                normalized["source_id"] = source_id
            if score is not None:
                normalized["score"] = score
            result.append(normalized)
        return result

    @staticmethod
    def _candidate_from_content(
        url: str,
        content: str,
        headers: dict[str, str],
        *,
        relation: str,
    ) -> dict[str, Any]:
        content_hash = _hash(content)
        return {
            "uri": url,
            "content": content,
            "content_hash": content_hash,
            "snapshot_hash": _hash({"uri": url, "content": content}),
            "retrieved_at_ms": int(time.time() * 1000),
            "trust_tier": 1,
            "extractor": "https-fetch",
            "headers": headers,
            "relation": relation,
        }

    @staticmethod
    def _admit_candidates(candidates: list[dict[str, Any]], program: SearchProgram) -> None:
        for candidate in candidates:
            uri = candidate.get("uri", "")
            if type(uri) is not str:
                raise ValueError("provider candidate URI must be a string")
            parsed = urlparse(uri)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("provider candidate must use an HTTPS URI")
            if parsed.username or parsed.password:
                raise ValueError("provider candidate cannot contain credentials")
            if program.allowed_hosts and parsed.hostname.lower() not in program.allowed_hosts:
                raise ValueError("provider candidate host is not allowlisted")
            if _is_disallowed_ip_literal(parsed.hostname):
                raise ValueError("provider candidate rejects private IP destinations")

    @staticmethod
    def _enforce_freshness(candidates: list[dict[str, Any]], program: SearchProgram) -> None:
        if program.freshness_max_age_seconds is None:
            return
        now_ms = int(time.time() * 1000)
        max_age_ms = program.freshness_max_age_seconds * 1000
        for candidate in candidates:
            retrieved_at_ms = candidate.get("retrieved_at_ms", 0)
            if type(retrieved_at_ms) is not int or retrieved_at_ms <= 0:
                raise ValueError("provider candidate retrieval time is invalid")
            age_ms = now_ms - retrieved_at_ms
            if age_ms < 0 or age_ms > max_age_ms:
                raise ValueError("provider candidate is outside the freshness window")

    @staticmethod
    def _enforce_independent_contradictions(
        candidates: list[dict[str, Any]], program: SearchProgram
    ) -> None:
        required = program.min_independent_contradiction_clusters
        if required == 0:
            return
        clusters: set[str] = set()
        for candidate in candidates:
            cluster = candidate.get("provenance_cluster", "")
            if type(cluster) is not str:
                raise ValueError("provider candidate provenance cluster is invalid")
            if cluster != cluster.strip():
                raise ValueError("provider candidate provenance cluster is not canonical")
            if cluster.strip():
                clusters.add(cluster.strip())
        if len(clusters) < required:
            raise ValueError("contradiction search lacks independent provenance clusters")


@dataclass(frozen=True)
class UnitDefinition:
    symbol: str
    dimension: str
    scale_to_base: float


class UnitRegistry:
    """Explicit SI registry with dimensional algebra for compound units."""

    _DEFAULTS: ClassVar[dict[str, UnitDefinition]] = {
        "1": UnitDefinition("1", "dimensionless", 1.0),
        "ratio": UnitDefinition("ratio", "dimensionless", 1.0),
        "score": UnitDefinition("score", "dimensionless", 1.0),
        "m": UnitDefinition("m", "length", 1.0),
        "cm": UnitDefinition("cm", "length", 0.01),
        "mm": UnitDefinition("mm", "length", 0.001),
        "s": UnitDefinition("s", "time", 1.0),
        "ms": UnitDefinition("ms", "time", 0.001),
        "kg": UnitDefinition("kg", "mass", 1.0),
        "g": UnitDefinition("g", "mass", 0.001),
        "A": UnitDefinition("A", "current", 1.0),
        "mA": UnitDefinition("mA", "current", 0.001),
        "uA": UnitDefinition("uA", "current", 0.000001),
        "V": UnitDefinition("V", "voltage", 1.0),
        "mV": UnitDefinition("mV", "voltage", 0.001),
        "W": UnitDefinition("W", "power", 1.0),
        "mW": UnitDefinition("mW", "power", 0.001),
        "uW": UnitDefinition("uW", "power", 0.000001),
        "J": UnitDefinition("J", "energy", 1.0),
        "mJ": UnitDefinition("mJ", "energy", 0.001),
        "K": UnitDefinition("K", "temperature", 1.0),
        "Hz": UnitDefinition("Hz", "frequency", 1.0),
        "N": UnitDefinition("N", "force", 1.0),
        "Pa": UnitDefinition("Pa", "pressure", 1.0),
        "C": UnitDefinition("C", "charge", 1.0),
        "Ohm": UnitDefinition("Ohm", "resistance", 1.0),
    }

    _DIMENSION_VECTORS: ClassVar[dict[str, tuple[int, ...]]] = {
        "dimensionless": (0, 0, 0, 0, 0, 0),
        "length": (0, 1, 0, 0, 0, 0),
        "time": (0, 0, 1, 0, 0, 0),
        "mass": (1, 0, 0, 0, 0, 0),
        "current": (0, 0, 0, 1, 0, 0),
        "temperature": (0, 0, 0, 0, 1, 0),
        "amount": (0, 0, 0, 0, 0, 1),
        "voltage": (1, 2, -3, -1, 0, 0),
        "power": (1, 2, -3, 0, 0, 0),
        "energy": (1, 2, -2, 0, 0, 0),
        "frequency": (0, 0, -1, 0, 0, 0),
        "force": (1, 1, -2, 0, 0, 0),
        "pressure": (1, -1, -2, 0, 0, 0),
        "charge": (0, 0, 1, 1, 0, 0),
        "resistance": (1, 2, -3, -2, 0, 0),
    }

    def __init__(self, definitions: dict[str, UnitDefinition] | None = None) -> None:
        self._definitions = dict(definitions or self._DEFAULTS)

    def require(self, symbol: str) -> UnitDefinition:
        value = self._definitions.get(self._canonical_symbol(symbol))
        if value is None:
            raise ValueError(f"unknown measurement unit: {symbol}")
        return value

    @staticmethod
    def _canonical_symbol(symbol: str) -> str:
        normalized = symbol.strip().replace("μ", "u").replace("Ω", "Ohm")
        return normalized

    def signature(self, expression: str) -> tuple[int, ...]:
        """Return the base-dimension exponent vector for an SI expression."""

        expression = expression.strip()
        if not expression:
            raise ValueError("measurement unit must be non-empty")
        vector = [0, 0, 0, 0, 0, 0]
        operator = 1
        for token in re.split(r"([*/])", expression):
            token = token.strip()
            if not token:
                continue
            if token == "*":
                operator = 1
                continue
            if token == "/":
                operator = -1
                continue
            if token == "1":
                continue
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)?(?:\^(-?\d+))?", token)
            if match is None or match.group(1) is None:
                raise ValueError(f"unknown measurement unit expression: {expression}")
            symbol = self._canonical_symbol(match.group(1))
            exponent = int(match.group(2) or "1") * operator
            definition = self.require(symbol)
            base = self._DIMENSION_VECTORS.get(definition.dimension)
            if base is None:
                raise ValueError(f"unit dimension is not registered: {definition.dimension}")
            for index, component in enumerate(base):
                vector[index] += component * exponent
        return tuple(vector)

    def scale(self, expression: str) -> float:
        """Return the multiplicative scale from the expression to SI base."""

        expression = expression.strip()
        if not expression:
            raise ValueError("measurement unit must be non-empty")
        factor = 1.0
        operator = 1
        for token in re.split(r"([*/])", expression):
            token = token.strip()
            if not token:
                continue
            if token == "*":
                operator = 1
                continue
            if token == "/":
                operator = -1
                continue
            if token == "1":
                continue
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)?(?:\^(-?\d+))?", token)
            if match is None or match.group(1) is None:
                raise ValueError(f"unknown measurement unit expression: {expression}")
            definition = self.require(match.group(1))
            exponent = int(match.group(2) or "1") * operator
            factor *= definition.scale_to_base**exponent
        return factor

    def dimension_name(self, expression: str) -> str:
        signature = self.signature(expression)
        for name, vector in self._DIMENSION_VECTORS.items():
            if vector == signature:
                return name
        return "compound[" + ",".join(str(value) for value in signature) + "]"

    def compatible(self, left: str, right: str) -> bool:
        return self.signature(left) == self.signature(right)

    def convert(self, value: float, source: str, target: str) -> float:
        if not math.isfinite(value):
            raise ValueError("measurement must be finite")
        if self.signature(source) != self.signature(target):
            raise ValueError("measurement units have incompatible dimensions")
        return value * self.scale(source) / self.scale(target)


DEFAULT_UNIT_REGISTRY = UnitRegistry()


@dataclass(frozen=True)
class PhysicalConstraint:
    """A scalar conservation/invariant constraint for a simulation cell."""

    name: str
    tolerance: float
    unit: str = "1"

    def validate(self) -> None:
        if (
            type(self.name) is not str
            or type(self.unit) is not str
            or not self.name.strip()
            or not _is_finite_number(self.tolerance)
            or self.tolerance < 0
        ):
            raise ValueError("physical constraint requires a finite non-negative tolerance")
        DEFAULT_UNIT_REGISTRY.signature(self.unit)


@dataclass(frozen=True)
class SimulationSpec:
    """Preregistered deterministic simulation contract."""

    simulation_id: str
    experiment_id: str
    model_hash: str
    seeds: tuple[int, ...]
    max_steps: int = 10_000
    constraints: tuple[PhysicalConstraint, ...] = ()
    discrepancy_model: str = "y_real = y_sim + delta(x) + epsilon"
    calibration_hash: str = ""
    calibration_observations: int = 0
    calibration_rmse: float | None = None
    calibration_tolerance: float | None = None
    calibration_holdout_hash: str = ""
    numerical_method: str = "deterministic-runner"
    floating_point_mode: str = "IEEE754"
    convergence_tolerance: float | None = None
    minimum_convergence_steps: int = 0

    def validate(self) -> None:
        if (
            type(self.simulation_id) is not str
            or type(self.experiment_id) is not str
            or type(self.model_hash) is not str
            or type(self.discrepancy_model) is not str
            or type(self.calibration_hash) is not str
            or type(self.calibration_holdout_hash) is not str
            or type(self.numerical_method) is not str
            or type(self.floating_point_mode) is not str
            or not self.simulation_id.strip()
            or not self.experiment_id.strip()
            or not _is_digest(self.model_hash)
            or type(self.seeds) not in (list, tuple)
            or not self.seeds
            or any(type(seed) is not int for seed in self.seeds)
            or len(set(self.seeds)) != len(self.seeds)
            or type(self.max_steps) is not int
            or self.max_steps < 1
            or not self.discrepancy_model.strip()
            or type(self.constraints) not in (list, tuple)
            or any(type(constraint) is not PhysicalConstraint for constraint in self.constraints)
            or type(self.calibration_observations) is not int
            or self.calibration_observations < 0
            or (self.calibration_hash and not _is_digest(self.calibration_hash))
            or not self.numerical_method.strip()
            or not self.floating_point_mode.strip()
            or type(self.minimum_convergence_steps) is not int
            or self.minimum_convergence_steps < 0
        ):
            raise ValueError("simulation spec identity, model hash, seeds and step bound are required")
        if self.convergence_tolerance is not None and (
            not _is_finite_number(self.convergence_tolerance)
            or self.convergence_tolerance < 0
            or self.minimum_convergence_steps < 1
        ):
            raise ValueError("simulation convergence contract is invalid")
        if self.convergence_tolerance is None and self.minimum_convergence_steps != 0:
            raise ValueError("simulation convergence steps require a tolerance")
        if self.calibration_observations > 0 and (
            not self.calibration_holdout_hash
            or not _is_digest(self.calibration_holdout_hash)
            or self.calibration_rmse is None
            or self.calibration_tolerance is None
            or not _is_finite_number(self.calibration_rmse)
            or not _is_finite_number(self.calibration_tolerance)
            or self.calibration_rmse < 0
            or self.calibration_tolerance < 0
            or self.calibration_rmse > self.calibration_tolerance
        ):
            raise ValueError("simulation calibration must pass held-out RMSE tolerance")
        for constraint in self.constraints:
            constraint.validate()


@dataclass(frozen=True)
class CalibrationResult:
    """Held-out calibration evidence required before simulation inference."""

    calibration_hash: str
    holdout_hash: str
    holdout_observations: int
    rmse: float
    max_abs_error: float
    tolerance: float
    passed: bool


@dataclass(frozen=True)
class ODEIntegrationResult:
    """Auditable result of a bounded ordinary-differential-equation solve.

    The solver intentionally returns every accepted state so a caller can bind
    the trace to an observation artifact.  ``convergence_status`` is explicit:
    no tolerance means ``NOT_REQUESTED`` and never masquerades as a proof of
    numerical convergence.  An invariant callback is useful for energy,
    charge, or other conserved quantities, but it remains a declared gate,
    not a claim that the model itself is physically correct.
    """

    method: str
    initial_time: float
    step_size: float
    steps: int
    times: tuple[float, ...]
    states: tuple[tuple[float, ...], ...]
    max_local_error: float
    convergence_status: str
    invariant_initial: float | None
    max_invariant_drift: float | None
    invariant_status: str

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe trace for content-addressed evidence storage."""

        return {
            "schema": "aegis-ode-integration-v1",
            "method": self.method,
            "initial_time": self.initial_time,
            "step_size": self.step_size,
            "steps": self.steps,
            "times": list(self.times),
            "states": [list(state) for state in self.states],
            "max_local_error": self.max_local_error,
            "convergence_status": self.convergence_status,
            "invariant_initial": self.invariant_initial,
            "max_invariant_drift": self.max_invariant_drift,
            "invariant_status": self.invariant_status,
        }


def calibrate_simulation(
    train_pairs: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    holdout_pairs: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    *,
    tolerance: float,
) -> CalibrationResult:
    """Compute deterministic RMSE evidence on a sealed holdout set.

    Pairs are ``(simulated, observed)`` and are never silently filtered. A
    caller may bind the returned hashes into ``SimulationSpec``; only a passed
    held-out result can justify the ``INFERRED`` epistemic label.
    """

    if (
        type(train_pairs) not in (list, tuple)
        or type(holdout_pairs) not in (list, tuple)
        or not train_pairs
        or not holdout_pairs
        or not _is_finite_number(tolerance)
        or tolerance < 0
    ):
        raise ValueError("calibration requires non-empty train/holdout pairs and finite tolerance")
    for pair in (*train_pairs, *holdout_pairs):
        if (
            type(pair) not in (list, tuple)
            or len(pair) != 2
            or any(not _is_finite_number(value) for value in pair)
        ):
            raise ValueError("calibration pairs must contain finite numeric values")
    errors = [simulated - observed for simulated, observed in holdout_pairs]
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    max_abs_error = max(abs(error) for error in errors)
    return CalibrationResult(
        calibration_hash=_hash({"train_pairs": train_pairs}),
        holdout_hash=_hash({"holdout_pairs": holdout_pairs}),
        holdout_observations=len(holdout_pairs),
        rmse=rmse,
        max_abs_error=max_abs_error,
        tolerance=tolerance,
        passed=rmse <= tolerance,
    )


class SimulationCell:
    """Fail-closed adapter for deterministic mathematical/physical simulation."""

    def __init__(self, *, max_steps: int = 10_000) -> None:
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("simulation cell max_steps must be positive")
        self.max_steps = max_steps

    def integrate_ode(
        self,
        derivative: Callable[[float, tuple[float, ...]], Any],
        *,
        initial_time: float = 0.0,
        initial_state: tuple[float, ...] | list[float],
        step_size: float,
        steps: int,
        method: str = "rk4",
        convergence_tolerance: float | None = None,
        invariant: Callable[[float, tuple[float, ...]], float] | None = None,
        invariant_tolerance: float | None = None,
    ) -> ODEIntegrationResult:
        """Integrate a bounded ODE with deterministic numerical evidence.

        Every requested step is checked by step-doubling: one full step is
        compared with two half steps.  When a convergence tolerance is
        declared, the finer state is accepted only if the local error is within
        that tolerance; otherwise the run fails closed.  Euler and classical
        RK4 are supported explicitly so the method is part of the artifact
        identity rather than an implicit implementation detail.
        """

        if not callable(derivative):
            raise TypeError("ODE derivative must be callable")
        if not _is_finite_number(initial_time):
            raise ValueError("ODE initial time must be finite")
        if not _is_finite_number(step_size) or step_size <= 0:
            raise ValueError("ODE step size must be finite and positive")
        if type(steps) is not int or steps < 1 or steps > self.max_steps:
            raise ValueError("ODE steps exceed the simulation cell bound")
        if type(method) is not str:
            raise TypeError("ODE method must be a string")
        normalized_method = method.strip().lower()
        if normalized_method not in {"euler", "rk4"}:
            raise ValueError("ODE method must be Euler or RK4")
        if convergence_tolerance is not None and (
            not _is_finite_number(convergence_tolerance) or convergence_tolerance < 0
        ):
            raise ValueError("ODE convergence tolerance must be finite and non-negative")
        if invariant_tolerance is not None and (
            invariant is None
            or not _is_finite_number(invariant_tolerance)
            or invariant_tolerance < 0
        ):
            raise ValueError("ODE invariant tolerance requires a finite invariant callback")
        if type(initial_state) not in (list, tuple) or not initial_state:
            raise ValueError("ODE initial state must be non-empty")
        if any(not _is_finite_number(value) for value in initial_state):
            raise ValueError("ODE initial state must contain finite numeric values")
        state = tuple(float(value) for value in initial_state)
        if any(not math.isfinite(value) for value in state):
            raise ValueError("ODE initial state must contain finite values")
        dimension = len(state)

        def evaluate(time_value: float, candidate: tuple[float, ...]) -> tuple[float, ...]:
            raw = derivative(time_value, candidate)
            if inspect.isawaitable(raw):
                raise TypeError("ODE derivative must be synchronous")
            if type(raw) not in (list, tuple):
                raise ValueError("ODE derivative dimension does not match the state")
            raw_sequence = cast(list[Any] | tuple[Any, ...], raw)
            if len(raw_sequence) != dimension:
                raise ValueError("ODE derivative dimension does not match the state")
            if any(not _is_finite_number(value) for value in raw_sequence):
                raise ValueError("ODE derivative returned invalid numeric values")
            values = tuple(float(value) for value in raw_sequence)
            if any(not math.isfinite(value) for value in values):
                raise ValueError("ODE derivative returned a non-finite value")
            return values

        def add_scaled(base: tuple[float, ...], scale: float, direction: tuple[float, ...]) -> tuple[float, ...]:
            values = tuple(base[index] + scale * direction[index] for index in range(dimension))
            if any(not math.isfinite(value) for value in values):
                raise ValueError("ODE state overflowed or became non-finite")
            return values

        def step(time_value: float, candidate: tuple[float, ...], delta: float) -> tuple[float, ...]:
            k1 = evaluate(time_value, candidate)
            if normalized_method == "euler":
                return add_scaled(candidate, delta, k1)
            k2 = evaluate(time_value + delta / 2, add_scaled(candidate, delta / 2, k1))
            k3 = evaluate(time_value + delta / 2, add_scaled(candidate, delta / 2, k2))
            k4 = evaluate(time_value + delta, add_scaled(candidate, delta, k3))
            combined = tuple(
                candidate[index]
                + delta * (k1[index] + 2 * k2[index] + 2 * k3[index] + k4[index]) / 6
                for index in range(dimension)
            )
            if any(not math.isfinite(value) for value in combined):
                raise ValueError("ODE state overflowed or became non-finite")
            return combined

        invariant_initial: float | None = None
        max_invariant_drift: float | None = None
        if invariant is not None:
            raw_invariant = invariant(initial_time, state)
            if inspect.isawaitable(raw_invariant):
                raise TypeError("ODE invariant must be synchronous")
            if not _is_finite_number(raw_invariant):
                raise ValueError("ODE invariant returned an invalid numeric value")
            invariant_initial = float(raw_invariant)
            if not math.isfinite(invariant_initial):
                raise ValueError("ODE invariant returned a non-finite value")
            max_invariant_drift = 0.0

        times = [initial_time]
        states = [state]
        max_local_error = 0.0
        time_value = initial_time
        for _ in range(steps):
            coarse = step(time_value, state, step_size)
            half = step(time_value, state, step_size / 2)
            fine = step(time_value + step_size / 2, half, step_size / 2)
            local_error = max(abs(fine[index] - coarse[index]) for index in range(dimension))
            max_local_error = max(max_local_error, local_error)
            if convergence_tolerance is not None and local_error > convergence_tolerance:
                raise ValueError("ODE convergence gate failed")
            state = fine if convergence_tolerance is not None else coarse
            time_value += step_size
            if not math.isfinite(time_value):
                raise ValueError("ODE time became non-finite")
            if invariant is not None:
                raw_invariant = invariant(time_value, state)
                if inspect.isawaitable(raw_invariant):
                    raise TypeError("ODE invariant must be synchronous")
                if not _is_finite_number(raw_invariant):
                    raise ValueError("ODE invariant returned an invalid numeric value")
                current_invariant = float(raw_invariant)
                if not math.isfinite(current_invariant):
                    raise ValueError("ODE invariant returned a non-finite value")
                assert invariant_initial is not None
                drift = abs(current_invariant - invariant_initial)
                assert max_invariant_drift is not None
                max_invariant_drift = max(max_invariant_drift, drift)
                if invariant_tolerance is not None and drift > invariant_tolerance:
                    raise ValueError("ODE invariant gate failed")
            times.append(time_value)
            states.append(state)

        return ODEIntegrationResult(
            method=normalized_method,
            initial_time=initial_time,
            step_size=step_size,
            steps=steps,
            times=tuple(times),
            states=tuple(states),
            max_local_error=max_local_error,
            convergence_status=(
                "PASS" if convergence_tolerance is not None else "NOT_REQUESTED"
            ),
            invariant_initial=invariant_initial,
            max_invariant_drift=max_invariant_drift,
            invariant_status=(
                "PASS" if invariant_tolerance is not None else "NOT_REQUESTED"
            ),
        )

    async def run(self, spec: SimulationSpec, runner: Any) -> list[dict[str, Any]]:
        spec.validate()
        if spec.max_steps > self.max_steps:
            raise ValueError("simulation exceeds cell step budget")
        if not callable(runner):
            raise TypeError("simulation runner must be callable")
        result = await _call_fenced(runner, spec, seeds=spec.seeds, max_steps=spec.max_steps)
        if type(result) is dict:
            typed_result: list[Any] | tuple[Any, ...] = [result]
        elif type(result) in (list, tuple):
            typed_result = cast(list[Any] | tuple[Any, ...], result)
        else:
            raise TypeError("simulation runner must return a sequence of observations")
        if not typed_result:
            raise ValueError("simulation produced no observations")
        constraints = {constraint.name: constraint for constraint in spec.constraints}
        out: list[dict[str, Any]] = []
        for raw in typed_result:
            if type(raw) is not dict:
                raise ValueError("simulation observation must be a mapping")
            raw_map = cast(dict[str, Any], raw)
            raw_measurement = raw_map.get("measurement")
            if not _is_finite_number(raw_measurement):
                raise ValueError("simulation observation requires a measurement")
            measurement = float(cast(float, raw_measurement))
            unit = raw_map.get("unit", "score")
            if type(unit) is not str or not unit.strip():
                raise ValueError("simulation observation unit must be a string")
            DEFAULT_UNIT_REGISTRY.signature(unit)
            if not math.isfinite(measurement):
                raise ValueError("simulation measurement must be finite")
            if spec.convergence_tolerance is not None:
                convergence_error = raw_map.get("convergence_error")
                convergence_steps = raw_map.get("convergence_steps")
                if (
                    not _is_finite_number(convergence_error)
                    or type(convergence_steps) is not int
                ):
                    raise ValueError("simulation convergence evidence is missing")
                convergence_error = float(cast(float, convergence_error))
                if (
                    not math.isfinite(convergence_error)
                    or convergence_error < 0
                    or convergence_error > spec.convergence_tolerance
                    or convergence_steps < spec.minimum_convergence_steps
                ):
                    raise ValueError("simulation convergence gate failed")
            residuals = raw_map.get("constraint_residuals", {})
            residual_units = raw_map.get("constraint_residual_units", {})
            if type(residuals) is not dict or type(residual_units) is not dict:
                raise ValueError("simulation constraint residual metadata must be mappings")
            if not constraints and (residuals or residual_units):
                raise ValueError("simulation reported undeclared constraint residuals")
            if constraints:
                residual_map = cast(dict[str, Any], residuals)
                residual_units_map = cast(dict[str, Any], residual_units)
                for name, constraint in constraints.items():
                    if name not in residual_map:
                        raise ValueError(f"simulation constraint residual missing: {name}")
                    raw_residual = residual_map[name]
                    if not _is_finite_number(raw_residual):
                        raise ValueError(f"simulation constraint residual is invalid: {name}")
                    residual = float(cast(float, raw_residual))
                    supplied_unit = residual_units_map.get(name, "1")
                    if type(supplied_unit) is not str or not supplied_unit.strip():
                        raise ValueError(f"simulation constraint unit is invalid: {name}")
                    try:
                        residual = DEFAULT_UNIT_REGISTRY.convert(
                            residual, supplied_unit, constraint.unit
                        )
                    except ValueError as exc:
                        raise ValueError(f"simulation constraint unit mismatch: {name}") from exc
                    if not math.isfinite(residual) or abs(residual) > constraint.tolerance:
                        raise ValueError(f"simulation constraint violated: {name}")
            epistemic_status = (
                "INFERRED"
                if (
                    spec.calibration_hash
                    and spec.calibration_observations > 0
                    and spec.calibration_holdout_hash
                    and spec.calibration_rmse is not None
                    and spec.calibration_tolerance is not None
                    and spec.calibration_rmse <= spec.calibration_tolerance
                )
                else "SIMULATED"
            )
            supplied_status = raw_map.get("epistemic_status", epistemic_status)
            if type(supplied_status) is not str:
                raise ValueError("simulation epistemic status must be a string")
            supplied_status = supplied_status.upper()
            if supplied_status != epistemic_status:
                raise ValueError("simulation epistemic status is not justified by calibration")
            out.append({**raw_map, "epistemic_status": epistemic_status})
        return out


def _rust_event_hash(
    sequence: int,
    state_epoch: int,
    kind: str,
    payload_hash: str,
    previous_event_hash: str,
) -> str:
    """Match Rust ``LabEvent::is_valid`` canonical bytes exactly."""

    value = {
        "sequence": sequence,
        "state_epoch": state_epoch,
        "kind": _RUST_EVENT_KINDS.get(kind, "ReviewRecorded"),
        "payload_hash": list(bytes.fromhex(payload_hash)),
        "previous_event_hash": list(bytes.fromhex(previous_event_hash)),
    }
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    return blake3(b"aegis-lab-canonical-v1\0" + encoded).hexdigest()


def _native_lab_module() -> Any:
    """Load the top-level or maturin-packaged Rust extension."""

    try:
        import aegis_nerve  # type: ignore[import-not-found]

        return cast(Any, aegis_nerve)
    except ImportError:
        try:
            from aegis_cognition import aegis_nerve  # type: ignore[import-not-found]

            return cast(Any, aegis_nerve)
        except ImportError as exc:
            raise ImportError("Rust aegis_nerve extension is unavailable") from exc


def _manifest_hash_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Decode the native manifest identity without accepting a partial hash."""

    raw_hash = manifest.get("manifest_hash")
    if type(raw_hash) is str and _is_digest(raw_hash):
        return bytes.fromhex(raw_hash)
    if not isinstance(raw_hash, list):
        raise ValueError("native replay manifest has no valid 32-byte manifest hash")
    typed_hash = cast(list[Any], raw_hash)
    if len(typed_hash) != 32:
        raise ValueError("native replay manifest has no valid 32-byte manifest hash")
    if any(type(value) is not int or not 0 <= value <= 255 for value in typed_hash):
        raise ValueError("native replay manifest hash contains invalid bytes")
    return bytes(typed_hash)


def _validate_replay_archive_manifest_metadata(
    manifest: Mapping[str, Any], *, require_current: bool
) -> None:
    """Validate the additive archive schema marker without rewriting legacy data.

    A pre-marker snapshot may still be read for rollback/diagnosis when both
    metadata fields are absent.  Any partial marker or non-current value is
    rejected: accepting one field while ignoring the other would make a
    future archive look compatible with the current verifier.
    """

    schema_present = "schema" in manifest
    version_present = "version" in manifest
    raw_schema = manifest.get("schema")
    raw_version = manifest.get("version")
    if not schema_present and not version_present:
        if require_current:
            raise ValueError("native replay manifest schema metadata is missing")
        return
    if (
        type(raw_schema) is not str
        or raw_schema != _REPLAY_ARCHIVE_MANIFEST_SCHEMA
        or type(raw_version) is not int
        or raw_version != _REPLAY_ARCHIVE_MANIFEST_VERSION
    ):
        raise ValueError("native replay manifest schema metadata is invalid")


async def _call(hook: Any, *args: Any, **kwargs: Any) -> Any:
    value = hook(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


async def _call_fenced(hook: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke an edge adapter without accepting a swallowed cancellation.

    Python tasks retain a positive ``cancelling()`` count even when an
    adapter catches ``CancelledError`` and returns a value.  Checking before
    and after the adapter turns that ambiguous return into a cancellation so
    the caller's admission is settled non-successfully.
    """

    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    value = hook(*args, **kwargs)
    result = await value if inspect.isawaitable(value) else value
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    return result


def _observation_count(value: Any) -> int:
    """Count runner outputs without silently accepting an untyped scalar."""

    if value is None:
        return 0
    if isinstance(value, dict):
        return 1
    if isinstance(value, (list, tuple)):
        typed_value = cast(list[Any] | tuple[Any, ...], value)
        return len(typed_value)
    raise TypeError("experiment runner must return a sequence")


def _prompt_injection_marker(content: str) -> str | None:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    for marker in _PROMPT_INJECTION_MARKERS:
        if marker in normalized:
            return marker
    return None


def _prompt_injection_marker_in_value(value: Any) -> str | None:
    """Inspect an untrusted browser projection without retaining its content."""

    if isinstance(value, str):
        content = value
    else:
        # Browser projections are untrusted JSON-like values. Serialize them
        # only in memory so nested accessibility/network text is covered; the
        # event ledger stores a digest and marker, never this raw projection.
        content = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return _prompt_injection_marker(content)


def _missing_benchmark_validator(_: tuple[float, ...]) -> bool:
    """Fail closed when a requested validator is outside the trusted registry."""

    raise RuntimeError("benchmark validation cell is not registered")


def _valid_citation_span(span: Any) -> bool:
    if type(span) is not dict:
        return False
    span_mapping = cast(dict[str, Any], span)
    start = span_mapping.get("start", -1)
    end = span_mapping.get("end", -1)
    text_hash = span_mapping.get("text_hash", "")
    if type(start) is not int or type(end) is not int or type(text_hash) is not str:
        return False
    return start >= 0 and end >= start and _is_digest(text_hash)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


@dataclass(frozen=True)
class ElectricalSignalSpec:
    """Preregistered contract for bounded voltage/current sampling.

    The cell computes an explicit sampled electrical model.  It does not
    infer hardware energy from CPU time, wall time or process utilization.
    """

    signal_id: str
    sample_rate_hz: float
    duration_s: float
    resistance_ohm: float
    max_expected_frequency_hz: float = 0.0
    anti_alias_cutoff_hz: float | None = None
    voltage_tolerance_v: float = 1e-9
    sensor_calibration_hash: str = ""
    voltage_sensor_gain: float = 1.0
    voltage_sensor_offset_v: float = 0.0
    current_sensor_gain: float = 1.0
    current_sensor_offset_a: float = 0.0
    max_samples: int = 1_000_000

    def validate(self) -> None:
        if (
            type(self.signal_id) is not str
            or type(self.sensor_calibration_hash) is not str
            or not self.signal_id.strip()
            or not _is_finite_number(self.sample_rate_hz)
            or self.sample_rate_hz <= 0
            or not _is_finite_number(self.duration_s)
            or self.duration_s <= 0
            or not _is_finite_number(self.resistance_ohm)
            or self.resistance_ohm < 0
            or not _is_finite_number(self.max_expected_frequency_hz)
            or self.max_expected_frequency_hz < 0
            or not _is_finite_number(self.voltage_tolerance_v)
            or self.voltage_tolerance_v < 0
            or type(self.max_samples) is not int
            or self.max_samples < 2
        ):
            raise ValueError("electrical signal contract has invalid bounds")
        nyquist_hz = self.sample_rate_hz / 2.0
        if self.max_expected_frequency_hz > nyquist_hz:
            raise ValueError("sample rate violates declared Nyquist bound")
        if self.anti_alias_cutoff_hz is not None and (
            not _is_finite_number(self.anti_alias_cutoff_hz)
            or self.anti_alias_cutoff_hz <= 0
            or self.anti_alias_cutoff_hz >= nyquist_hz
        ):
            raise ValueError("anti-alias cutoff must be strictly below Nyquist")
        if (
            not _is_finite_number(self.voltage_sensor_gain)
            or self.voltage_sensor_gain <= 0
            or not _is_finite_number(self.current_sensor_gain)
            or self.current_sensor_gain <= 0
            or not _is_finite_number(self.voltage_sensor_offset_v)
            or not _is_finite_number(self.current_sensor_offset_a)
        ):
            raise ValueError("sensor calibration parameters must be finite and positive")
        if self.sensor_calibration_hash and not _is_digest(self.sensor_calibration_hash):
            raise ValueError("sensor calibration hash must be a lowercase digest")
        expected_samples = round(self.sample_rate_hz * self.duration_s) + 1
        if expected_samples < 2 or expected_samples > self.max_samples:
            raise ValueError("electrical signal sample count exceeds the cell bound")


@dataclass(frozen=True)
class ElectricalSignalResult:
    """Auditable sampled electrical quantities with explicit epistemic labels."""

    signal_id: str
    sample_rate_hz: float
    duration_s: float
    sample_count: int
    times_s: tuple[float, ...]
    voltage_v: tuple[float, ...]
    current_a: tuple[float, ...]
    power_w: tuple[float, ...]
    energy_j: float
    rms_voltage_v: float
    rms_current_a: float
    max_ohms_residual_v: float
    max_voltage_sensor_error_v: float | None
    max_current_sensor_error_a: float | None
    nyquist_status: str
    sensor_calibration_status: str
    energy_status: str
    epistemic_status: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "aegis-electrical-signal-v1",
            "signal_id": self.signal_id,
            "sample_rate_hz": self.sample_rate_hz,
            "duration_s": self.duration_s,
            "sample_count": self.sample_count,
            "times_s": list(self.times_s),
            "voltage_v": list(self.voltage_v),
            "current_a": list(self.current_a),
            "power_w": list(self.power_w),
            "energy_j": self.energy_j,
            "rms_voltage_v": self.rms_voltage_v,
            "rms_current_a": self.rms_current_a,
            "max_ohms_residual_v": self.max_ohms_residual_v,
            "max_voltage_sensor_error_v": self.max_voltage_sensor_error_v,
            "max_current_sensor_error_a": self.max_current_sensor_error_a,
            "nyquist_status": self.nyquist_status,
            "sensor_calibration_status": self.sensor_calibration_status,
            "energy_status": self.energy_status,
            "epistemic_status": self.epistemic_status,
        }


class ElectricalSignalCell:
    """Bounded V/I measurement model with Ohm, sampling and energy gates."""

    def __init__(self, *, max_samples: int = 1_000_000) -> None:
        if type(max_samples) is not int or max_samples < 2:
            raise ValueError("electrical cell max_samples must be at least two")
        self.max_samples = max_samples

    def run(
        self,
        spec: ElectricalSignalSpec,
        *,
        voltage_samples: list[float] | tuple[float, ...],
        current_samples: list[float] | tuple[float, ...],
        reference_voltage_samples: list[float] | tuple[float, ...] | None = None,
        reference_current_samples: list[float] | tuple[float, ...] | None = None,
    ) -> ElectricalSignalResult:
        spec.validate()
        expected_samples = round(spec.sample_rate_hz * spec.duration_s) + 1
        if expected_samples > self.max_samples:
            raise ValueError("electrical signal exceeds cell sample bound")
        for name, samples in (
            ("voltage", voltage_samples),
            ("current", current_samples),
            ("reference voltage", reference_voltage_samples),
            ("reference current", reference_current_samples),
        ):
            if samples is not None and (
                type(samples) not in (list, tuple)
                or any(not _is_finite_number(value) for value in samples)
            ):
                raise ValueError(f"{name} samples must be finite numeric sequences")
        if len(voltage_samples) != expected_samples or len(current_samples) != expected_samples:
            raise ValueError("voltage/current sample count does not match the preregistered contract")
        if reference_voltage_samples is not None and len(reference_voltage_samples) != expected_samples:
            raise ValueError("reference voltage sample count does not match the contract")
        if reference_current_samples is not None and len(reference_current_samples) != expected_samples:
            raise ValueError("reference current sample count does not match the contract")

        voltage = tuple(
            (float(value) - spec.voltage_sensor_offset_v) / spec.voltage_sensor_gain
            for value in voltage_samples
        )
        current = tuple(
            (float(value) - spec.current_sensor_offset_a) / spec.current_sensor_gain
            for value in current_samples
        )
        if any(not math.isfinite(value) for value in (*voltage, *current)):
            raise ValueError("electrical samples must be finite")
        reference_voltage = (
            tuple(float(value) for value in reference_voltage_samples)
            if reference_voltage_samples is not None
            else None
        )
        reference_current = (
            tuple(float(value) for value in reference_current_samples)
            if reference_current_samples is not None
            else None
        )
        if (
            reference_voltage is not None
            and any(not math.isfinite(value) for value in reference_voltage)
        ) or (
            reference_current is not None
            and any(not math.isfinite(value) for value in reference_current)
        ):
            raise ValueError("reference electrical samples must be finite")
        dt = 1.0 / spec.sample_rate_hz
        times = tuple(index * dt for index in range(expected_samples))
        power = tuple(v * i for v, i in zip(voltage, current, strict=True))
        ohms_residual = tuple(
            v - i * spec.resistance_ohm for v, i in zip(voltage, current, strict=True)
        )
        max_residual = max(abs(value) for value in ohms_residual)
        if max_residual > spec.voltage_tolerance_v:
            raise ValueError("Ohm/Kirchhoff residual exceeds declared tolerance")
        energy = sum(
            (power[index - 1] + power[index]) * dt / 2.0
            for index in range(1, expected_samples)
        )
        if not math.isfinite(energy):
            raise ValueError("electrical energy integral is non-finite")
        voltage_error = (
            max(abs(v - reference) for v, reference in zip(voltage, reference_voltage, strict=True))
            if reference_voltage is not None
            else None
        )
        current_error = (
            max(abs(i - reference) for i, reference in zip(current, reference_current, strict=True))
            if reference_current is not None
            else None
        )
        if voltage_error is not None and not math.isfinite(voltage_error):
            raise ValueError("reference voltage error is non-finite")
        if current_error is not None and not math.isfinite(current_error):
            raise ValueError("reference current error is non-finite")
        return ElectricalSignalResult(
            signal_id=spec.signal_id,
            sample_rate_hz=spec.sample_rate_hz,
            duration_s=spec.duration_s,
            sample_count=expected_samples,
            times_s=times,
            voltage_v=voltage,
            current_a=current,
            power_w=power,
            energy_j=energy,
            rms_voltage_v=math.sqrt(sum(value * value for value in voltage) / expected_samples),
            rms_current_a=math.sqrt(sum(value * value for value in current) / expected_samples),
            max_ohms_residual_v=max_residual,
            max_voltage_sensor_error_v=voltage_error,
            max_current_sensor_error_a=current_error,
            nyquist_status=(
                "PASS_DECLARED_BAND"
                if spec.max_expected_frequency_hz > 0
                else "NOT_DECLARED"
            ),
            sensor_calibration_status=(
                "DECLARED"
                if spec.sensor_calibration_hash
                else "NOT_DECLARED"
            ),
            energy_status="NUMERICAL_TRAPEZOID_FROM_V_I_SAMPLES",
            epistemic_status="MEASURED_INPUTS_ONLY",
        )


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    uri: str
    content_hash: str
    snapshot_hash: str
    retrieved_at_ms: int
    trust_tier: int = 1
    extractor: str = "search-as-code"
    relation: str = "unknown"
    citation_spans: tuple[dict[str, Any], ...] = ()
    provenance_cluster: str = ""

    def validate(self) -> None:
        if (
            type(self.source_id) is not str
            or type(self.uri) is not str
            or type(self.content_hash) is not str
            or type(self.snapshot_hash) is not str
            or type(self.extractor) is not str
            or type(self.relation) is not str
            or type(self.provenance_cluster) is not str
            or type(self.retrieved_at_ms) is not int
            or type(self.trust_tier) is not int
            or type(self.citation_spans) not in (list, tuple)
            or not self.source_id.strip()
            or not self.uri.strip()
            or not self.content_hash.strip()
            or not self.snapshot_hash.strip()
            or not self.relation.strip()
            or self.retrieved_at_ms <= 0
            or self.trust_tier < 1
            or (self.provenance_cluster and not self.provenance_cluster.strip())
            or any(not _valid_citation_span(span) for span in self.citation_spans)
        ):
            raise ValueError("source identity, provenance and citation metadata are invalid")
        parsed_uri = urlparse(self.uri)
        if (
            parsed_uri.scheme != "https"
            or not parsed_uri.hostname
            or parsed_uri.username
            or parsed_uri.password
        ):
            raise ValueError("source URI must be a credential-free HTTPS URL")


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: str
    statement: str
    source_ids: tuple[str, ...]
    confidence_bps: int
    status: str = "unresolved"

    def validate(self) -> None:
        if (
            type(self.claim_id) is not str
            or type(self.statement) is not str
            or type(self.status) is not str
            or type(self.source_ids) not in (list, tuple)
            or type(self.confidence_bps) is not int
            or not self.claim_id.strip()
            or not self.statement.strip()
            or not self.source_ids
            or any(type(source_id) is not str or not source_id.strip() for source_id in self.source_ids)
            or not 0 <= self.confidence_bps <= 10_000
        ):
            raise ValueError("claim identity, citations and confidence metadata are invalid")


@dataclass(frozen=True)
class HypothesisRecord:
    hypothesis_id: str
    statement: str
    prior_bps: int
    falsifiers: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...] = ()
    contradicting_claim_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if (
            type(self.hypothesis_id) is not str
            or type(self.statement) is not str
            or type(self.falsifiers) not in (list, tuple)
            or type(self.supporting_claim_ids) not in (list, tuple)
            or type(self.contradicting_claim_ids) not in (list, tuple)
            or type(self.prior_bps) is not int
            or not self.hypothesis_id.strip()
            or not self.statement.strip()
            or not self.falsifiers
            or any(type(item) is not str or not item.strip() for item in self.falsifiers)
            or any(
                type(claim_id) is not str or not claim_id.strip()
                for claim_id in (*self.supporting_claim_ids, *self.contradicting_claim_ids)
            )
            or not 0 <= self.prior_bps <= 10_000
        ):
            raise ValueError("hypothesis identity, falsifiers and prior metadata are invalid")


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    hypothesis_id: str
    design: str
    variables: tuple[str, ...]
    controls: tuple[str, ...]
    preregistered_seeds: tuple[int, ...]
    expected_observations: int
    measurement_unit: str = "score"
    uncertainty_required: bool = False
    min_clean_replicates: int = 0

    def validate(self) -> None:
        if (
            type(self.experiment_id) is not str
            or type(self.hypothesis_id) is not str
            or type(self.design) is not str
            or type(self.measurement_unit) is not str
            or not self.experiment_id.strip()
            or not self.hypothesis_id.strip()
            or not self.design.strip()
            or not self.measurement_unit.strip()
            or type(self.variables) not in (list, tuple)
            or type(self.controls) not in (list, tuple)
            or type(self.preregistered_seeds) not in (list, tuple)
            or not self.variables
            or not self.controls
            or any(type(item) is not str or not item.strip() for item in self.variables)
            or any(type(item) is not str or not item.strip() for item in self.controls)
            or len(self.preregistered_seeds) < 5
            or any(type(seed) is not int for seed in self.preregistered_seeds)
            or len(set(self.preregistered_seeds)) != len(self.preregistered_seeds)
            or type(self.expected_observations) is not int
            or self.expected_observations < 1
            or type(self.uncertainty_required) is not bool
            or type(self.min_clean_replicates) is not int
            or self.min_clean_replicates < 0
            or self.min_clean_replicates > self.expected_observations
        ):
            raise ValueError(
                "experiment requires unique integer seeds, controls and a positive observation quota"
            )


@dataclass(frozen=True)
class ObservationRecord:
    observation_id: str
    experiment_id: str
    seed: int
    measurement: float
    unit: str
    raw_artifact_hash: str
    environment_hash: str
    valid: bool = True
    uncertainty: float | None = None
    replication_of: str | None = None
    operator_id: str = ""
    clean: bool = True
    epistemic_status: str = "OBSERVED"
    reported_unit: str = ""

    def validate(self) -> None:
        if (
            type(self.observation_id) is not str
            or type(self.experiment_id) is not str
            or type(self.unit) is not str
            or type(self.raw_artifact_hash) is not str
            or type(self.environment_hash) is not str
            or type(self.operator_id) is not str
            or type(self.epistemic_status) is not str
            or type(self.reported_unit) is not str
            or not self.observation_id.strip()
            or not self.experiment_id.strip()
            or type(self.seed) is not int
            or type(self.measurement) not in (int, float)
            or isinstance(self.measurement, bool)
            or not math.isfinite(float(self.measurement))
            or not self.unit.strip()
            or not self.raw_artifact_hash.strip()
            or not self.environment_hash.strip()
            or type(self.valid) is not bool
            or type(self.clean) is not bool
            or self.epistemic_status.upper() not in _EPISTEMIC_STATUSES
            or (self.replication_of is not None and type(self.replication_of) is not str)
        ):
            raise ValueError("observation identity, measurement and epistemic metadata are invalid")
        if self.uncertainty is not None and (
            type(self.uncertainty) not in (int, float)
            or isinstance(self.uncertainty, bool)
            or not math.isfinite(float(self.uncertainty))
            or float(self.uncertainty) < 0
        ):
            raise ValueError("observation uncertainty must be finite and non-negative")


@dataclass(frozen=True)
class LabEvent:
    sequence: int
    state_epoch: int
    kind: str
    payload_hash: str
    previous_event_hash: str
    event_hash: str
    payload: Any


@dataclass(frozen=True)
class LabDossier:
    schema: str
    mission_id: str
    objective: str
    status: str
    blockers: tuple[str, ...]
    sources: tuple[SourceRecord, ...]
    claims: tuple[ClaimRecord, ...]
    hypotheses: tuple[HypothesisRecord, ...]
    experiments: tuple[ExperimentSpec, ...]
    observations: tuple[ObservationRecord, ...]
    events: tuple[LabEvent, ...]
    manifest: dict[str, Any]
    benchmark: dict[str, Any] | None = None
    truth_claim: bool = False
    security_events: tuple[dict[str, Any], ...] = ()
    scope: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    skill_admissions: tuple[SkillAdmission, ...] = ()


@dataclass(frozen=True)
class AdaptiveDecision:
    phase: str
    step: int
    token_budget: int
    reason: str
    potential: tuple[int, ...] = ()


@dataclass(frozen=True)
class BrowserCellPolicy:
    allowed_hosts: tuple[str, ...] = ()
    require_https: bool = True
    max_actions: int = 100
    max_observations: int = 1_000

    def validate(self) -> None:
        if (
            type(self.allowed_hosts) not in (list, tuple)
            or type(self.require_https) is not bool
            or type(self.max_actions) is not int
            or type(self.max_observations) is not int
            or self.max_actions < 1
            or self.max_observations < 1
            or any(
                type(host) is not str
                or not host.strip()
                or host != host.strip().lower()
                for host in self.allowed_hosts
            )
        ):
            raise ValueError("browser policy requires positive quotas and valid hosts")


@dataclass(frozen=True)
class BrowserObserverView:
    """Read-only projection of a BrowserCell's actor-owned session.

    The observer intentionally exposes no navigation or DOM mutation methods.
    It is a local capability boundary; process isolation and hostile-browser
    enforcement still require a hosted/runtime proof recorded in the blocker
    ledger.
    """

    _session: Any

    @property
    def current_url(self) -> str:
        value = getattr(self._session, "current_url", "")
        value = value() if callable(value) else value
        if type(value) is not str:
            raise RuntimeError("browser observer returned a non-string URL")
        return value

    async def get_accessibility_tree(self) -> Any:
        method = getattr(self._session, "get_accessibility_tree", None)
        if not callable(method):
            return {"source": "browser-observer", "unavailable": True}
        return await _call(method)

    async def drain_network_log(self) -> list[Any]:
        method = getattr(self._session, "drain_network_log", None)
        if not callable(method):
            raise RuntimeError("browser observer lacks network projection")
        result = await _call(method)
        if type(result) is not list:
            raise ValueError("browser observer network log must be a list")
        return cast(list[Any], result)

    async def wait_for_timeout(self, milliseconds: int) -> None:
        method = getattr(self._session, "wait_for_timeout", None)
        if not callable(method):
            raise RuntimeError("browser observer lacks wait projection")
        await _call(method, milliseconds)


_SKILL_RISK_CLASSES = frozenset(
    {"read", "compute", "network_read", "external_write", "destructive"}
)
_SKILL_STATUSES = frozenset({"SUCCESS", "REJECTED", "CANCELLED"})


def _is_digest(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


_BROWSER_ACTION_KINDS = frozenset({"launch", "goto", "click", "fill", "type", "wait"})
_BROWSER_OBSERVATION_KINDS = frozenset({"read_url", "accessibility", "network_log", "wait"})
_BROWSER_SETTLEMENT_STATUSES = frozenset({"SUCCESS", "REJECTED", "CANCELLED"})


class SkillAdmissionError(ValueError):
    """Raised when a skill cannot be admitted or its result is untrusted."""


@dataclass(frozen=True)
class SkillManifest:
    """Replayable declaration of one bounded skill package."""

    skill_id: str
    version: str
    capabilities: tuple[str, ...]
    preconditions: tuple[str, ...]
    validator_version: str
    implementation_hash: str
    policy_hash: str
    cost_units: int = 1
    risk_class: str = "compute"

    def validate(self) -> None:
        if (
            type(self.skill_id) is not str
            or type(self.version) is not str
            or type(self.capabilities) not in (list, tuple)
            or type(self.preconditions) not in (list, tuple)
            or type(self.validator_version) is not str
            or type(self.implementation_hash) is not str
            or type(self.policy_hash) is not str
            or type(self.cost_units) is not int
            or type(self.risk_class) is not str
        ):
            raise SkillAdmissionError("skill manifest metadata types are invalid")
        if not self.skill_id.strip() or not self.version.strip():
            raise SkillAdmissionError("skill identity and version are required")
        if not self.validator_version.strip():
            raise SkillAdmissionError("skill validator version is required")
        if self.cost_units < 1:
            raise SkillAdmissionError("skill cost must be positive")
        if self.risk_class not in _SKILL_RISK_CLASSES:
            raise SkillAdmissionError("skill risk class is invalid")
        for values, label in (
            (self.capabilities, "capability"),
            (self.preconditions, "precondition"),
        ):
            if any(not item.strip() for item in values):
                raise SkillAdmissionError(f"skill {label} names must be non-empty")
            if tuple(sorted(set(values))) != values:
                raise SkillAdmissionError(f"skill {label} names must be sorted and unique")
        if not _is_digest(self.implementation_hash) or not _is_digest(self.policy_hash):
            raise SkillAdmissionError("skill implementation and policy hashes are required")

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return _hash({"schema": "aegis-skill-manifest-v1", **asdict(self)})


@dataclass(frozen=True)
class SkillAdmission:
    """Capability/precondition proof bound to one mission replay position."""

    skill_id: str
    version: str
    manifest_hash: str
    mission_id: str
    mission_epoch: int
    granted_capabilities: tuple[str, ...]
    precondition_results: tuple[tuple[str, bool], ...]
    replay_parent_hash: str
    admission_hash: str
    admission_event_hash: str = ""

    def _expected_hash(self) -> str:
        return _hash(
            {
                "schema": "aegis-skill-admission-v1",
                "skill_id": self.skill_id,
                "version": self.version,
                "manifest_hash": self.manifest_hash,
                "mission_id": self.mission_id,
                "mission_epoch": self.mission_epoch,
                "granted_capabilities": self.granted_capabilities,
                "precondition_results": self.precondition_results,
                "replay_parent_hash": self.replay_parent_hash,
            }
        )

    def validate_hash(self) -> None:
        if (
            type(self.skill_id) is not str
            or type(self.version) is not str
            or type(self.manifest_hash) is not str
            or type(self.mission_id) is not str
            or type(self.mission_epoch) is not int
            or type(self.granted_capabilities) not in (list, tuple)
            or type(self.precondition_results) not in (list, tuple)
            or type(self.replay_parent_hash) is not str
            or type(self.admission_hash) is not str
            or type(self.admission_event_hash) is not str
            or not self.skill_id.strip()
            or not self.version.strip()
            or not self.mission_id.strip()
            or any(type(capability) is not str or not capability.strip() for capability in self.granted_capabilities)
            or any(
                type(pair) not in (list, tuple)
                or len(pair) != 2
                or type(pair[0]) is not str
                or not pair[0].strip()
                or type(pair[1]) is not bool
                for pair in self.precondition_results
            )
        ):
            raise SkillAdmissionError("skill admission identity is invalid")
        if self.mission_epoch < 0 or not _is_digest(self.manifest_hash):
            raise SkillAdmissionError("skill admission metadata is invalid")
        if not _is_digest(self.replay_parent_hash) or not _is_digest(self.admission_hash):
            raise SkillAdmissionError("skill admission replay hashes are invalid")
        if self.admission_event_hash and not _is_digest(self.admission_event_hash):
            raise SkillAdmissionError("skill admission event hash is invalid")
        if tuple(sorted(set(self.granted_capabilities))) != self.granted_capabilities:
            raise SkillAdmissionError("granted capabilities must be sorted and unique")
        if tuple(name for name, _ in self.precondition_results) != tuple(
            sorted(name for name, _ in self.precondition_results)
        ):
            raise SkillAdmissionError("skill precondition result names must be sorted")
        if self.admission_hash != self._expected_hash():
            raise SkillAdmissionError("skill admission hash mismatch")

    def validate(self, manifest: SkillManifest) -> None:
        manifest.validate()
        if self.skill_id != manifest.skill_id or self.version != manifest.version:
            raise SkillAdmissionError("skill admission identity mismatch")
        if self.manifest_hash != manifest.manifest_hash:
            raise SkillAdmissionError("skill admission manifest hash mismatch")
        if not self.mission_id.strip() or self.mission_epoch < 0:
            raise SkillAdmissionError("skill admission mission binding is invalid")
        if not _is_digest(self.replay_parent_hash) or not _is_digest(self.admission_hash):
            raise SkillAdmissionError("skill admission replay hashes are invalid")
        if tuple(sorted(set(self.granted_capabilities))) != self.granted_capabilities:
            raise SkillAdmissionError("granted capabilities must be sorted and unique")
        if tuple(name for name, _ in self.precondition_results) != manifest.preconditions:
            raise SkillAdmissionError("skill precondition result order does not match manifest")
        self.validate_hash()


@dataclass(frozen=True)
class SkillExecutionReceipt:
    """Validator-backed output binding for one admitted skill execution."""

    skill_id: str
    version: str
    admission_hash: str
    mission_id: str
    replay_parent_hash: str
    input_hash: str
    result_hash: str
    artifact_hash: str
    validator_version: str
    status: str
    execution_hash: str

    def _expected_hash(self) -> str:
        return _hash(
            {
                "schema": "aegis-skill-execution-v1",
                "skill_id": self.skill_id,
                "version": self.version,
                "admission_hash": self.admission_hash,
                "mission_id": self.mission_id,
                "replay_parent_hash": self.replay_parent_hash,
                "input_hash": self.input_hash,
                "result_hash": self.result_hash,
                "artifact_hash": self.artifact_hash,
                "validator_version": self.validator_version,
                "status": self.status,
            }
        )

    def validate(self) -> None:
        if (
            type(self.skill_id) is not str
            or type(self.version) is not str
            or type(self.admission_hash) is not str
            or type(self.mission_id) is not str
            or type(self.replay_parent_hash) is not str
            or type(self.input_hash) is not str
            or type(self.result_hash) is not str
            or type(self.artifact_hash) is not str
            or type(self.validator_version) is not str
            or type(self.status) is not str
            or type(self.execution_hash) is not str
        ):
            raise SkillAdmissionError("skill execution metadata types are invalid")
        if not self.skill_id.strip() or not self.version.strip() or not self.mission_id.strip():
            raise SkillAdmissionError("skill execution identity is invalid")
        if self.status not in _SKILL_STATUSES:
            raise SkillAdmissionError("skill execution status is invalid")
        if not self.validator_version.strip() or any(
            not _is_digest(value)
            for value in (
                self.admission_hash,
                self.replay_parent_hash,
                self.input_hash,
                self.result_hash,
                self.artifact_hash,
                self.execution_hash,
            )
        ):
            raise SkillAdmissionError("skill execution hashes are invalid")
        if self.execution_hash != self._expected_hash():
            raise SkillAdmissionError("skill execution hash mismatch")


class SkillRegistry:
    """Bounded Python registry; Rust remains the release authority."""

    def __init__(self) -> None:
        self._manifests: dict[tuple[str, str], SkillManifest] = {}
        self._validators: dict[tuple[str, str], Callable[[Any], Any]] = {}

    def register(self, manifest: SkillManifest, validator: Callable[[Any], Any]) -> None:
        if type(manifest) is not SkillManifest:
            raise SkillAdmissionError("skill manifest type is invalid")
        manifest.validate()
        if not callable(validator):
            raise TypeError("skill validator must be callable")
        key = (manifest.skill_id, manifest.version)
        if key in self._manifests:
            raise SkillAdmissionError("skill manifest is already registered")
        self._manifests[key] = manifest
        self._validators[key] = validator

    def get(self, skill_id: str, version: str) -> SkillManifest:
        if type(skill_id) is not str or type(version) is not str:
            raise SkillAdmissionError("skill manifest identity is invalid")
        try:
            return self._manifests[(skill_id, version)]
        except KeyError as exc:
            raise SkillAdmissionError("skill manifest is not registered") from exc

    def admit(
        self,
        skill_id: str,
        version: str,
        *,
        mission_id: str,
        mission_epoch: int,
        available_capabilities: tuple[str, ...] | list[str] | set[str],
        preconditions: dict[str, bool],
        replay_parent_hash: str,
    ) -> SkillAdmission:
        if (
            type(skill_id) is not str
            or type(version) is not str
            or type(mission_id) is not str
            or type(mission_epoch) is not int
            or type(replay_parent_hash) is not str
            or type(available_capabilities) not in (list, tuple, set)
            or type(preconditions) is not dict
            or any(type(item) is not str for item in available_capabilities)
            or any(type(key) is not str or type(value) is not bool for key, value in preconditions.items())
        ):
            raise SkillAdmissionError("skill admission contract is invalid")
        manifest = self.get(skill_id, version)
        capabilities = tuple(sorted(set(available_capabilities)))
        if any(capability not in capabilities for capability in manifest.capabilities):
            raise SkillAdmissionError("skill capability is not granted")
        if tuple(preconditions) != manifest.preconditions:
            raise SkillAdmissionError("skill preconditions must match manifest order")
        results = tuple((name, preconditions[name]) for name in manifest.preconditions)
        if not all(value for _, value in results):
            raise SkillAdmissionError("skill precondition is false")
        if not mission_id.strip() or mission_epoch < 0 or not _is_digest(replay_parent_hash):
            raise SkillAdmissionError("skill mission/replay binding is invalid")
        admission_without_hash = {
            "schema": "aegis-skill-admission-v1",
            "skill_id": manifest.skill_id,
            "version": manifest.version,
            "manifest_hash": manifest.manifest_hash,
            "mission_id": mission_id,
            "mission_epoch": mission_epoch,
            "granted_capabilities": capabilities,
            "precondition_results": results,
            "replay_parent_hash": replay_parent_hash,
        }
        admission = SkillAdmission(
            skill_id=manifest.skill_id,
            version=manifest.version,
            manifest_hash=manifest.manifest_hash,
            mission_id=mission_id,
            mission_epoch=mission_epoch,
            granted_capabilities=capabilities,
            precondition_results=results,
            replay_parent_hash=replay_parent_hash,
            admission_hash=_hash(admission_without_hash),
        )
        admission.validate(manifest)
        return admission

    async def execute(
        self,
        admission: SkillAdmission,
        executor: Callable[[Any], Any],
        *,
        mission_id: str,
        replay_parent_hash: str,
        input_payload: Any,
    ) -> tuple[Any, SkillExecutionReceipt]:
        manifest = self.get(admission.skill_id, admission.version)
        admission.validate(manifest)
        expected_parent = admission.admission_event_hash or admission.replay_parent_hash
        if admission.mission_id != mission_id or expected_parent != replay_parent_hash:
            raise SkillAdmissionError("skill execution replay binding mismatch")
        if not callable(executor):
            raise TypeError("skill executor must be callable")
        result = await _call_fenced(executor, input_payload)
        validator = self._validators[(manifest.skill_id, manifest.version)]
        accepted = await _call_fenced(validator, result)
        if type(accepted) is not bool or not accepted:
            raise SkillAdmissionError("skill validator rejected result")
        input_hash = _hash(input_payload)
        result_hash = _hash(result)
        artifact_hash = _hash(
            {
                "schema": "aegis-skill-artifact-v1",
                "skill_id": manifest.skill_id,
                "version": manifest.version,
                "result_hash": result_hash,
            }
        )
        execution_without_hash = {
            "schema": "aegis-skill-execution-v1",
            "skill_id": manifest.skill_id,
            "version": manifest.version,
            "admission_hash": admission.admission_hash,
            "mission_id": mission_id,
            "replay_parent_hash": replay_parent_hash,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "artifact_hash": artifact_hash,
            "validator_version": manifest.validator_version,
            "status": "SUCCESS",
        }
        receipt = SkillExecutionReceipt(
            skill_id=manifest.skill_id,
            version=manifest.version,
            admission_hash=admission.admission_hash,
            mission_id=mission_id,
            replay_parent_hash=replay_parent_hash,
            input_hash=input_hash,
            result_hash=result_hash,
            artifact_hash=artifact_hash,
            validator_version=manifest.validator_version,
            status="SUCCESS",
            execution_hash=_hash(execution_without_hash),
        )
        receipt.validate()
        return result, receipt


class BrowserCell:
    """Small actor/observer boundary around an externally-owned browser page."""

    def __init__(self, policy: BrowserCellPolicy | None = None) -> None:
        self.policy = policy or BrowserCellPolicy()
        self.policy.validate()
        self.action_count = 0
        self.observation_count = 0
        self.recovery_count = 0
        self._session: Any | None = None
        self._lease_id = 0
        self._closed = False

    @property
    def lease_id(self) -> int:
        return self._lease_id

    @property
    def session(self) -> Any | None:
        return self._session

    def observer_view(self) -> BrowserObserverView:
        """Return a read-only view bound to the current actor lease."""

        if self._closed:
            raise RuntimeError("browser cell is closed")
        if self._session is None:
            raise RuntimeError("browser cell has no active session")
        return BrowserObserverView(self._session)

    async def observe(self, action: Any) -> Any:
        """Run one read-only observation without consuming actor quota."""

        if self._closed:
            raise RuntimeError("browser cell is closed")
        if self._session is None:
            raise RuntimeError("browser cell has no active session")
        if self.observation_count >= self.policy.max_observations:
            raise RuntimeError("browser observation quota exhausted")
        # Observer reads remain subject to the same HTTPS/host boundary as
        # actor actions, but consume only the independent observation quota.
        self.validate_url(await self._url(self._session))
        result = await _execute_browser_action(
            self.observer_view(), action, self, role="observer"
        )
        self.observation_count += 1
        return result

    async def acquire(self, launcher: Any) -> Any:
        """Open one browser session and bind it to this cell's lease."""

        if self._closed:
            raise RuntimeError("browser cell is closed")
        if self._session is not None:
            raise RuntimeError("browser cell already has an active session")
        if not callable(launcher):
            raise TypeError("browser launcher must be callable")
        candidate = await _call_fenced(launcher)
        if candidate is None:
            raise RuntimeError("browser launcher returned no session")
        try:
            await self.admit(candidate)
        except BaseException:
            await self._close_candidate(candidate)
            raise
        self._session = candidate
        self._lease_id += 1
        return candidate

    async def bind(self, browser_session: Any) -> Any:
        """Bind an externally-owned session without taking close ownership."""

        if self._closed:
            raise RuntimeError("browser cell is closed")
        if self._session is not None:
            raise RuntimeError("browser cell already has an active session")
        if browser_session is None:
            raise ValueError("browser session is required")
        await self.admit(browser_session)
        self._session = browser_session
        self._lease_id += 1
        return browser_session

    async def release(self) -> None:
        """Close the owned session; repeated release is idempotent."""

        session = self._session
        self._session = None
        if session is not None:
            await self._close_candidate(session)

    async def recover(self, launcher: Any, *, max_attempts: int = 1) -> Any:
        """Restart a crashed/expired session without resetting its quota."""

        if self._closed:
            raise RuntimeError("browser cell is closed")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("browser recovery attempts must be positive")
        await self.release()
        last_error: BaseException | None = None
        for _ in range(max_attempts):
            try:
                session = await self.acquire(launcher)
                self.recovery_count += 1
                return session
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                last_error = exc
        raise RuntimeError("browser cell recovery exhausted") from last_error

    def close(self) -> None:
        """Mark the cell closed; async session cleanup still uses ``release``."""

        self._closed = True

    @staticmethod
    async def _close_candidate(candidate: Any) -> None:
        for name in ("close", "shutdown", "dispose"):
            method = getattr(candidate, name, None)
            if callable(method):
                await _call(method)
                return

    async def admit(self, browser_session: Any) -> None:
        if self.action_count >= self.policy.max_actions:
            raise RuntimeError("browser action quota exhausted")
        url = await self._url(browser_session)
        self.validate_url(url)

    async def admit_observation(self, browser_session: Any, action: Any) -> None:
        """Validate one observer intent before reading from the page."""

        if self._closed:
            raise RuntimeError("browser cell is closed")
        if self._session is None:
            raise RuntimeError("browser cell has no active session")
        if self._session is not browser_session:
            raise RuntimeError("browser session is not bound to this cell")
        if self.observation_count >= self.policy.max_observations:
            raise RuntimeError("browser observation quota exhausted")
        if type(action) is not dict:
            raise TypeError("browser observer actions must be typed mappings")
        action_map = cast(dict[str, Any], action)
        raw_kind = action_map.get("kind", "")
        if type(raw_kind) is not str:
            raise TypeError("browser observer action kind must be a string")
        kind = raw_kind.strip().lower()
        if kind not in {"read_url", "accessibility", "network_log", "wait"}:
            raise RuntimeError("browser observer cannot perform actor action")
        self.validate_url(await self._url(browser_session))

    def validate_url(self, url: str) -> None:
        """Validate both current and requested navigation URLs."""

        if type(url) is not str or not url.strip() or url != url.strip():
            raise RuntimeError("browser egress URL must be a canonical string")
        parsed = urlparse(url)
        if self.policy.require_https and parsed.scheme != "https":
            raise RuntimeError("browser egress policy requires HTTPS")
        if not parsed.hostname:
            raise RuntimeError("browser egress URL must include a host")
        if parsed.username or parsed.password:
            raise RuntimeError("browser egress rejects credential-bearing URLs")
        host = (parsed.hostname or "").lower()
        if _is_disallowed_ip_literal(host):
            raise RuntimeError("browser egress rejects private IP destinations")
        if self.policy.allowed_hosts and host not in self.policy.allowed_hosts:
            raise RuntimeError("browser egress host is not allowlisted")

    def record(self) -> None:
        self.action_count += 1

    @staticmethod
    async def _url(browser_session: Any) -> str:
        for name in ("get_current_page_url", "current_url", "url"):
            if not hasattr(browser_session, name):
                continue
            value = getattr(browser_session, name)
            value = await _call(value) if callable(value) else value
            if type(value) is str and value.strip():
                return value.strip()
        raise RuntimeError("browser session did not expose a URL")


async def _execute_browser_action(
    browser_session: Any,
    action: Any,
    cell: BrowserCell,
    *,
    role: str = "actor",
) -> Any:
    """Execute only the typed, allowlisted browser action vocabulary."""

    if role not in {"actor", "observer"}:
        raise ValueError("browser action role must be actor or observer")
    if callable(action):
        if role == "observer":
            raise RuntimeError("browser observer cannot execute arbitrary callables")
        try:
            signature = inspect.signature(action)
            accepts_argument = any(
                parameter.kind
                in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.VAR_POSITIONAL}
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_argument = True
        return await _call(action, browser_session) if accepts_argument else await _call(action)
    if type(action) is not dict:
        raise TypeError("browser action must be a callable or typed mapping")
    action_map = cast(dict[str, Any], action)
    raw_kind = action_map.get("kind", "")
    if type(raw_kind) is not str:
        raise TypeError("browser action kind must be a string")
    kind = raw_kind.strip().lower()
    if role == "observer":
        if kind == "read_url":
            return await BrowserCell._url(browser_session)
        if kind == "accessibility":
            method = getattr(browser_session, "get_accessibility_tree", None)
            if not callable(method):
                raise RuntimeError("browser observer lacks accessibility projection")
            return await _call(method)
        if kind == "network_log":
            method = getattr(browser_session, "drain_network_log", None)
            if not callable(method):
                raise RuntimeError("browser observer lacks network projection")
            result = await _call(method)
            if not isinstance(result, list):
                raise ValueError("browser observer network log must be a list")
            return cast(list[Any], result)
        if kind == "wait":
            milliseconds = action_map.get("milliseconds", 0)
            if type(milliseconds) is not int or milliseconds < 0 or milliseconds > 60_000:
                raise ValueError("wait action milliseconds must be within [0, 60000]")
            method = getattr(browser_session, "wait_for_timeout", None)
            if not callable(method):
                raise RuntimeError("browser observer does not support typed wait action")
            return await _call(method, milliseconds)
        raise RuntimeError("browser observer cannot perform actor action")
    if kind == "goto":
        target = action_map.get("url", "")
        if type(target) is not str or not target or target != target.strip():
            raise ValueError("goto action requires a URL")
        cell.validate_url(target)
        method = getattr(browser_session, "goto", None)
        if method is None:
            raise RuntimeError("browser session does not support typed goto action")
        return await _call(method, target)
    if kind == "click":
        selector = action_map.get("selector", "")
        if type(selector) is not str or not selector or selector != selector.strip():
            raise ValueError("click action requires a selector")
        method = getattr(browser_session, "click", None)
        if method is None:
            raise RuntimeError("browser session does not support typed click action")
        return await _call(method, selector)
    if kind in {"fill", "type"}:
        selector = action_map.get("selector", "")
        value = action_map.get("value", "")
        if type(selector) is not str or not selector or selector != selector.strip():
            raise ValueError(f"{kind} action requires a selector")
        if type(value) is not str:
            raise ValueError(f"{kind} action requires a string value")
        method_name = "fill" if kind == "fill" else "type_text"
        method = getattr(browser_session, method_name, None)
        if method is None:
            raise RuntimeError(f"browser session does not support typed {kind} action")
        return await _call(method, selector, value)
    if kind == "wait":
        milliseconds = action_map.get("milliseconds", 0)
        if type(milliseconds) is not int or milliseconds < 0 or milliseconds > 60_000:
            raise ValueError("wait action milliseconds must be within [0, 60000]")
        wait_for_timeout = getattr(browser_session, "wait_for_timeout", None)
        if wait_for_timeout is None:
            raise RuntimeError("browser session does not support typed wait action")
        return await _call(wait_for_timeout, milliseconds)
    raise ValueError(f"unsupported browser action kind: {kind}")


def _runtime_browser_action(
    browser_session: Any, action: Any, cell: BrowserCell
) -> Callable[[], Any]:
    async def invoke() -> Any:
        return await _execute_browser_action(browser_session, action, cell)

    return invoke


def _gateway_browser_action(action: Any, cell: BrowserCell) -> Callable[[Any], Any]:
    async def invoke(session: Any) -> Any:
        return await _execute_browser_action(session, action, cell)

    return invoke


class AdaptiveController:
    """Deterministic gap-driven controller with disjoint token reserves."""

    def __init__(self, *, max_steps: int, token_budget: int, finalization_reserve: int, recovery_reserve: int) -> None:
        if (
            type(max_steps) is not int
            or type(token_budget) is not int
            or type(finalization_reserve) is not int
            or type(recovery_reserve) is not int
            or max_steps < 1
            or token_budget < 1
            or finalization_reserve < 0
            or recovery_reserve < 0
        ):
            raise ValueError("adaptive controller bounds must be positive")
        if finalization_reserve + recovery_reserve >= token_budget:
            raise ValueError("adaptive reserves must leave exploration headroom")
        self.max_steps = max_steps
        self.exploration_remaining = token_budget - finalization_reserve - recovery_reserve
        self.finalization_remaining = finalization_reserve
        self.recovery_remaining = recovery_reserve
        self.step = 0
        self._last_signature: str | None = None
        self._repeated = 0

    @staticmethod
    def progress_potential(run: LabRun) -> tuple[int, ...]:
        """Return an unweighted, verifier-owned gap tuple for one run.

        The tuple is intentionally qualitative rather than a fabricated
        weighted score: each component counts an unresolved evidence obligation
        and lower is better.  It is safe to persist in a controller-decision
        event and deterministic across processes for the same projection.
        """

        unreplicated = 0
        uncertainty_gaps = 0
        for experiment in run.experiments.values():
            clean_replicates = sum(
                1
                for observation in run.observations.values()
                if observation.experiment_id == experiment.experiment_id
                and observation.replication_of is not None
                and observation.clean
            )
            unreplicated += max(0, experiment.min_clean_replicates - clean_replicates)
            if experiment.uncertainty_required:
                uncertainty_gaps += sum(
                    1
                    for observation in run.observations.values()
                    if observation.experiment_id == experiment.experiment_id
                    and observation.uncertainty is None
                )
        return (
            int(not bool(run.sources)),
            int(not bool(run.claims)),
            int(not bool(run.hypotheses)),
            int(not bool(run.experiments)),
            int(not bool(run.observations)),
            unreplicated,
            uncertainty_gaps,
            len(run.blockers),
        )

    @staticmethod
    def _state_signature(run: LabRun) -> str:
        """Hash evidence content, not only collection sizes, for plateau detection."""

        return _hash(
            {
                "state": run.state,
                "sources": tuple(
                    sorted(
                        (
                            source.source_id,
                            source.uri,
                            source.content_hash,
                            source.snapshot_hash,
                            source.retrieved_at_ms,
                            source.trust_tier,
                            source.relation,
                        )
                        for source in run.sources.values()
                    )
                ),
                "claims": tuple(
                    sorted(
                        (
                            claim.claim_id,
                            claim.statement,
                            claim.source_ids,
                            claim.confidence_bps,
                            claim.status,
                        )
                        for claim in run.claims.values()
                    )
                ),
                "hypotheses": tuple(
                    sorted(
                        (
                            hypothesis.hypothesis_id,
                            hypothesis.statement,
                            hypothesis.prior_bps,
                            hypothesis.falsifiers,
                            hypothesis.supporting_claim_ids,
                            hypothesis.contradicting_claim_ids,
                        )
                        for hypothesis in run.hypotheses.values()
                    )
                ),
                "experiments": tuple(
                    sorted(
                        (
                            experiment.experiment_id,
                            experiment.hypothesis_id,
                            experiment.design,
                            experiment.variables,
                            experiment.controls,
                            experiment.preregistered_seeds,
                            experiment.expected_observations,
                            experiment.measurement_unit,
                            experiment.uncertainty_required,
                            experiment.min_clean_replicates,
                        )
                        for experiment in run.experiments.values()
                    )
                ),
                "observations": tuple(
                    sorted(
                        (
                            observation.observation_id,
                            observation.experiment_id,
                            observation.seed,
                            observation.measurement,
                            observation.unit,
                            observation.raw_artifact_hash,
                            observation.environment_hash,
                            observation.valid,
                            observation.uncertainty,
                            observation.replication_of,
                            observation.clean,
                            observation.epistemic_status,
                        )
                        for observation in run.observations.values()
                    )
                ),
                "blockers": tuple(sorted(run.blockers)),
            }
        )

    def next(self, run: LabRun) -> AdaptiveDecision | None:
        if self.step >= self.max_steps or self.exploration_remaining <= 0:
            return None
        potential = self.progress_potential(run)
        if not run.sources:
            phase, reason = "research", "source provenance is missing"
        elif not run.claims or not run.hypotheses:
            phase, reason = "synthesis", "claims or falsifiable hypotheses are missing"
        elif not run.experiments:
            phase, reason = "experiment", "no preregistered experiment exists"
        elif not run.observations:
            phase, reason = "experiment", "experiment has no observations"
        else:
            phase, reason = "review", "all evidence planes have a candidate record"
        self.step += 1
        token_budget = max(1, self.exploration_remaining // max(1, self.max_steps - self.step + 1))
        self.exploration_remaining -= token_budget
        return AdaptiveDecision(phase, self.step, token_budget, reason, potential)

    def observe(self, run: LabRun) -> bool:
        signature = self._state_signature(run)
        if signature == self._last_signature:
            self._repeated += 1
        else:
            self._last_signature = signature
            self._repeated = 0
        return self._repeated < 2

    def reserve_snapshot(self) -> dict[str, int]:
        return {
            "exploration_remaining": self.exploration_remaining,
            "finalization_remaining": self.finalization_remaining,
            "recovery_remaining": self.recovery_remaining,
        }


class LabRun:
    """In-memory run reducer; every mutation emits an auditable event."""

    def __init__(
        self,
        task: str,
        *,
        max_steps: int = 100,
        external_attempt_budget: int | None = None,
        scope: tuple[str, ...] = (),
        non_goals: tuple[str, ...] = (),
        require_native_authority: bool = False,
        authority_mode: AuthorityMode | str | None = None,
        trust_level: str = "DEV",
        trust_policy_hash: str | None = None,
        token_budget: int | None = None,
        finalization_reserve: int | None = None,
        recovery_reserve: int | None = None,
    ) -> None:
        if type(task) is not str or not task.strip():
            raise ValueError("lab task must be non-empty")
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("lab max_steps must be positive")
        if type(require_native_authority) is not bool:
            raise ValueError("lab native-authority flag must be boolean")
        if type(scope) not in (list, tuple) or type(non_goals) not in (list, tuple):
            raise ValueError("lab scope and non-goals must be sequences")
        if any(type(item) is not str or not item.strip() for item in (*scope, *non_goals)):
            raise ValueError("lab scope and non-goals must be non-empty strings")
        self.scope = tuple(scope)
        self.non_goals = tuple(non_goals)
        if authority_mode is None:
            normalized_authority_mode = (
                AuthorityMode.NATIVE_REQUIRED
                if require_native_authority
                else AuthorityMode.PROJECTION_ONLY
            )
        else:
            normalized_authority_mode = _normalize_authority_mode(authority_mode)
            if require_native_authority and normalized_authority_mode is not AuthorityMode.NATIVE_REQUIRED:
                raise ValueError("legacy native-authority flag conflicts with Lab authority mode")
        self.authority_mode = normalized_authority_mode
        self.require_native_authority = self.authority_mode is AuthorityMode.NATIVE_REQUIRED
        self.trust_level = _normalize_lab_trust_level(trust_level)
        expected_trust_policy_hash = _trust_policy_hash(self.trust_level)
        if trust_policy_hash is not None and trust_policy_hash != expected_trust_policy_hash:
            raise ValueError("lab trust policy hash does not match trust level")
        # ``None`` preserves legacy direct LabRun/native snapshots.  The public
        # Lab facade supplies the hash, making policy binding explicit there.
        self.trust_policy_hash = trust_policy_hash
        if token_budget is not None and type(token_budget) is not int:
            raise ValueError("lab token budget must be an integer")
        self.token_budget = token_budget if token_budget is not None else max_steps * 1000
        if self.token_budget < 1:
            raise ValueError("lab token budget must be positive")
        if finalization_reserve is not None and type(finalization_reserve) is not int:
            raise ValueError("lab finalization reserve must be an integer")
        self.finalization_reserve = (
            finalization_reserve
            if finalization_reserve is not None
            else min(max(1, self.token_budget // 5), max(0, self.token_budget - 1))
        )
        if recovery_reserve is not None and type(recovery_reserve) is not int:
            raise ValueError("lab recovery reserve must be an integer")
        self.recovery_reserve = (
            recovery_reserve
            if recovery_reserve is not None
            else min(
                max(0, self.token_budget // 10),
                max(0, self.token_budget - self.finalization_reserve - 1),
            )
        )
        if (
            self.finalization_reserve < 0
            or self.recovery_reserve < 0
            or self.finalization_reserve + self.recovery_reserve >= self.token_budget
        ):
            raise ValueError("lab budget reserves must leave exploration headroom")
        self.mission_id = _hash(
            {
                "task": task,
                "scope": self.scope,
                "non_goals": self.non_goals,
                "created_at_ms": int(time.time() * 1000),
            }
        )[:32]
        self.objective = task
        self.max_steps = max_steps
        if external_attempt_budget is not None and (
            type(external_attempt_budget) is not int or external_attempt_budget < 1
        ):
            raise ValueError("lab external attempt budget must be a positive integer or unset")
        self.external_attempt_budget = (
            external_attempt_budget
            if external_attempt_budget is not None
            else _default_external_attempt_budget(max_steps)
        )
        self.external_attempt_count = 0
        self.state = "planned"
        self.state_epoch = 0
        self.sources: dict[str, SourceRecord] = {}
        self.claims: dict[str, ClaimRecord] = {}
        self.hypotheses: dict[str, HypothesisRecord] = {}
        self.experiments: dict[str, ExperimentSpec] = {}
        self.observations: dict[str, ObservationRecord] = {}
        self.skill_admissions: dict[str, SkillAdmission] = {}
        self.tool_execution_admissions: dict[str, dict[str, Any]] = {}
        self.tool_executions: set[str] = set()
        self.events: list[LabEvent] = []
        self.blockers: list[str] = []
        self.security_events: list[dict[str, Any]] = []
        self.replay_archive: dict[str, Any] | None = None
        self.execution_cell_manifest: tuple[dict[str, Any], ...] = ()
        self._event_listeners: list[Callable[[LabEvent], Any]] = []
        self._native_controller: Any | None = None
        self._native_mission: dict[str, Any] | None = None
        self._native_mission_hash: str | None = None
        self._native_finalization_started = False
        self._initialize_native_controller(task)
        self._append(
            "mission_created",
            {
                "mission_id": self.mission_id,
                "objective": task,
                "scope": self.scope,
                "non_goals": self.non_goals,
            },
        )

    def _initialize_native_controller(self, task: str) -> None:
        """Create the Rust controller when the selected policy requires it.

        The Python reducer remains a compatibility projection.  When the
        compiled extension exposes the controller, every subsequent event is
        admitted by that native single-writer chain; a missing extension still
        fails closed for production policy but preserves the legacy verifier
        seam used by development/test doubles.
        """

        if self.authority_mode is AuthorityMode.PROJECTION_ONLY:
            return
        try:
            native_module = _native_lab_module()
        except ImportError as exc:
            if self.require_native_authority:
                raise RuntimeError("native Lab authority is required but unavailable") from exc
            return
        controller_type = getattr(native_module, "LabController", None)
        if not callable(controller_type):
            return

        contract_hash = bytes.fromhex(
            _hash(
                {
                    "objective": task,
                    "scope": self.scope,
                    "non_goals": self.non_goals,
                    **(
                        {
                            "trust_level": self.trust_level,
                            "trust_policy_hash": self.trust_policy_hash,
                        }
                        if self.trust_policy_hash is not None
                        else {}
                    ),
                }
            )
        )

        def budget_vector(tokens: int) -> dict[str, int]:
            return {
                "tokens": tokens,
                "money_minor_units": 0,
                "time_ms": 0,
                "tool_calls": 0,
                "api_calls": 0,
                "cpu_ms": 0,
                "risk_units": 0,
            }

        mission: dict[str, Any] = {
            "mission_id": self.mission_id,
            "objective": task,
            "scope": " | ".join(self.scope) or "unspecified",
            "contract_hash": list(contract_hash),
            "budget_policy": {
                "total": budget_vector(self.token_budget),
                "finalization_reserve": budget_vector(self.finalization_reserve),
                "recovery_reserve": budget_vector(self.recovery_reserve),
            },
            "required_evidence": True,
            "max_steps": self.max_steps,
            "max_external_attempts": self.external_attempt_budget,
            "schema": "aegis-lab-runtime-v1",
        }
        try:
            self._native_controller = controller_type(
                json.dumps(mission, separators=(",", ":"), ensure_ascii=False)
            )
        except Exception as exc:
            if self.require_native_authority:
                raise RuntimeError("native Lab controller initialization failed") from exc
            return
        self._native_mission = mission
        self._native_mission_hash = _rust_canonical_hash(mission)

    def subscribe(self, listener: Callable[[LabEvent], Any]) -> None:
        """Register a bounded evidence-stream listener.

        Listeners receive immutable ``LabEvent`` objects after they are appended
        to the reducer. They are projections only: a listener cannot alter the
        event, state transition, budget or evidence admission decision.
        """

        if not callable(listener):
            raise TypeError("lab event listener must be callable")
        if listener not in self._event_listeners:
            self._event_listeners.append(listener)

    def unsubscribe(self, listener: Callable[[LabEvent], Any]) -> None:
        """Remove a listener without changing the authoritative event log."""

        if listener in self._event_listeners:
            self._event_listeners.remove(listener)

    def record_security_event(self, reason: str, *, artifact_hash: str = "", detail: str = "") -> None:
        """Retain a non-authoritative security observation for later review."""

        if (
            type(reason) is not str
            or type(artifact_hash) is not str
            or type(detail) is not str
            or (artifact_hash and not _is_digest(artifact_hash))
        ):
            raise ValueError("security event contract is invalid")
        reason = reason.strip()
        if not reason:
            raise ValueError("security event reason must be non-empty")
        snapshot = self._projection_snapshot()
        event = {
            "reason": reason,
            "artifact_hash": artifact_hash,
            "detail": detail,
        }

        def apply_projection() -> None:
            self.security_events.append(event)

        self._append(
            "security_event_recorded",
            event,
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def record_blocker(self, reason: str, *, detail: str = "") -> None:
        """Record a deduplicated blocker as an auditable evidence event."""

        if type(reason) is not str or type(detail) is not str:
            raise ValueError("lab blocker contract is invalid")
        reason = reason.strip()
        if not reason:
            raise ValueError("lab blocker reason must be non-empty")
        if reason in self.blockers:
            return
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.blockers.append(reason)

        self._append(
            "blocker_recorded",
            {"reason": reason, "detail": detail},
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def resolve_blocker(self, reason: str, *, detail: str = "") -> None:
        """Resolve one operator/action blocker through the event ledger.

        Resolution is deliberately explicit: deleting a blocker from the
        projection without an event would make replay unable to explain why a
        previously blocked run became admissible again.
        """

        if type(reason) is not str or type(detail) is not str:
            raise ValueError("lab blocker contract is invalid")
        reason = reason.strip()
        if not reason:
            raise ValueError("lab blocker reason must be non-empty")
        if reason not in self.blockers:
            return
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.blockers.remove(reason)

        self._append(
            "blocker_resolved",
            {"reason": reason, "detail": detail},
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def _require_open_admission(
        self,
        *,
        admission_kind: str,
        record_kind: str,
        identity_key: str,
        identity: str,
        admission_id: str,
        expected: Mapping[str, Any],
        label: str,
    ) -> dict[str, Any]:
        """Bind a settlement to exactly one still-open immutable admission.

        The typed projection maps only cover some execution lanes. This
        event-ledger check keeps the compatibility projection fail-closed as
        well: a missing/duplicated admission, a stale identity, or a second
        settlement cannot be turned into a new side-effect receipt.
        """

        if type(identity) is not str or type(admission_id) is not str:
            raise ValueError(f"{label} settlement identity is invalid")
        normalized_identity = identity.strip()
        normalized_admission_id = admission_id.strip()
        if not normalized_identity or not normalized_admission_id:
            raise ValueError(f"{label} settlement identity is invalid")
        admissions: list[dict[str, Any]] = []
        settled = False
        for event in self.events:
            payload = event.payload
            if event.kind not in {admission_kind, record_kind}:
                continue
            if type(payload) is not dict:
                raise ValueError(f"{label} admission payload is invalid")
            raw_payload = cast(dict[Any, Any], payload)
            if any(type(key) is not str for key in raw_payload):
                raise ValueError(f"{label} admission payload is invalid")
            typed_payload = cast(dict[str, Any], raw_payload)
            raw_event_identity = typed_payload.get(identity_key, "")
            if type(raw_event_identity) is not str:
                raise ValueError(f"{label} admission identity metadata is invalid")
            event_identity = raw_event_identity.strip()
            if event_identity != normalized_identity:
                continue
            if event.kind == admission_kind:
                admissions.append(typed_payload)
            else:
                settled = True
        if not admissions:
            raise ValueError(f"{label} settlement references unknown admission")
        if len(admissions) != 1:
            raise ValueError(f"{label} admission identity is duplicated")
        if settled:
            raise ValueError(f"{label} settlement is duplicated")
        admission = admissions[0]
        stored_admission_id = admission.get("admission_id")
        if stored_admission_id is None and admission_kind == "skill_admission_recorded":
            stored_admission_id = admission.get("admission_hash")
        if type(stored_admission_id) is not str:
            raise ValueError(f"{label} admission identity metadata is invalid")
        if stored_admission_id != normalized_admission_id:
            raise ValueError(f"{label} settlement admission binding mismatch")
        if any(admission.get(key) != value for key, value in expected.items()):
            raise ValueError(f"{label} settlement does not match admission")
        return admission

    def _assert_admission_identity_available(
        self,
        *,
        admission_kind: str,
        record_kind: str,
        identity_key: str,
        identity: str,
        label: str,
    ) -> None:
        """Reject an explicit identity already present in the event ledger."""

        if type(identity) is not str:
            raise ValueError(f"{label} identity must be non-empty")
        normalized_identity = identity.strip()
        if not normalized_identity:
            raise ValueError(f"{label} identity must be non-empty")
        for event in self.events:
            if event.kind not in {admission_kind, record_kind}:
                continue
            payload = event.payload
            if type(payload) is not dict:
                raise ValueError(f"{label} admission payload is invalid")
            raw_payload = cast(dict[Any, Any], payload)
            if any(type(key) is not str for key in raw_payload):
                raise ValueError(f"{label} admission payload is invalid")
            typed_payload = cast(dict[str, object], raw_payload)
            event_identity = typed_payload.get(identity_key, "")
            if type(event_identity) is not str:
                raise ValueError(f"{label} admission identity metadata is invalid")
            if event_identity.strip() == normalized_identity:
                raise ValueError(f"{label} identity is duplicated")

    def admit_skill(
        self,
        registry: SkillRegistry,
        skill_id: str,
        version: str,
        *,
        available_capabilities: tuple[str, ...] | list[str] | set[str],
        preconditions: dict[str, bool],
    ) -> SkillAdmission:
        """Admit a registered skill at the current replay position."""

        if self.state in {"completed", "aborted"}:
            raise SkillAdmissionError(f"cannot admit skill in state {self.state}")
        parent_hash = self.events[-1].event_hash if self.events else "0" * 64
        admission = registry.admit(
            skill_id,
            version,
            mission_id=self.mission_id,
            mission_epoch=self.state_epoch,
            available_capabilities=available_capabilities,
            preconditions=preconditions,
            replay_parent_hash=parent_hash,
        )
        if admission.admission_hash in self.skill_admissions:
            raise SkillAdmissionError("skill admission is duplicated in this run")
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.skill_admissions[admission.admission_hash] = admission

        self._append(
            "skill_admission_recorded",
            asdict(admission),
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )
        bound_admission = replace(admission, admission_event_hash=self.events[-1].event_hash)
        self.skill_admissions[admission.admission_hash] = bound_admission
        return bound_admission

    def record_skill_execution(self, receipt: SkillExecutionReceipt) -> None:
        """Commit a validator-backed skill receipt to the current replay head."""

        receipt.validate()
        admission = self.skill_admissions.get(receipt.admission_hash)
        if admission is None:
            raise SkillAdmissionError("skill execution references unknown admission")
        if (
            receipt.skill_id != admission.skill_id
            or receipt.version != admission.version
            or receipt.mission_id != self.mission_id
            or receipt.replay_parent_hash != (self.events[-1].event_hash if self.events else "0" * 64)
        ):
            raise SkillAdmissionError("skill execution replay binding mismatch")
        try:
            self._require_open_admission(
                admission_kind="skill_admission_recorded",
                record_kind="skill_execution_recorded",
                identity_key="admission_hash",
                identity=receipt.admission_hash,
                admission_id=receipt.admission_hash,
                expected={
                    "skill_id": receipt.skill_id,
                    "version": receipt.version,
                    "mission_id": receipt.mission_id,
                },
                label="skill execution",
            )
        except ValueError as exc:
            raise SkillAdmissionError(str(exc)) from exc
        snapshot = self._projection_snapshot()
        self._append(
            "skill_execution_recorded",
            asdict(receipt),
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_experiment_execution(
        self,
        *,
        experiment_id: str,
        attempt: int,
        input_payload: Any,
        policy_payload: Any,
        execution_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[str, str]:
        """Admit one experiment-cell invocation before running its side effect.

        ``attempt`` is the replay-visible retry fence.  A later attempt must
        use a distinct execution identity; the native controller rejects a
        duplicate settlement or a settlement bound to another admission.
        """

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot admit experiment execution in state {self.state}")
        if (
            type(experiment_id) is not str
            or type(attempt) is not int
            or (execution_id is not None and type(execution_id) is not str)
            or (idempotency_key is not None and type(idempotency_key) is not str)
            or (timeout_seconds is not None and type(timeout_seconds) not in (int, float))
            or isinstance(timeout_seconds, bool)
        ):
            raise ValueError("experiment execution admission contract is invalid")
        raw_idempotency_key: object = idempotency_key
        raw_timeout_seconds: object = timeout_seconds
        if (
            experiment_id not in self.experiments
            or type(attempt) is not int
            or attempt < 1
            or (
                raw_idempotency_key is not None
                and (type(raw_idempotency_key) is not str or not _is_digest(raw_idempotency_key))
            )
            or (
                raw_timeout_seconds is not None
                and (
                    type(raw_timeout_seconds) not in (int, float)
                    or isinstance(raw_timeout_seconds, bool)
                    or not math.isfinite(float(raw_timeout_seconds))
                    or float(raw_timeout_seconds) <= 0
                )
            )
        ):
            raise ValueError("experiment execution requires a known experiment and positive attempt")
        ordinal = sum(event.kind == "experiment_execution_admitted" for event in self.events) + 1
        normalized_execution_id = (
            f"{experiment_id}-attempt-{attempt}-{ordinal}" if execution_id is None else execution_id
        )
        if not normalized_execution_id.strip():
            raise ValueError("experiment execution identity must be non-empty")
        self._assert_admission_identity_available(
            admission_kind="experiment_execution_admitted",
            record_kind="experiment_execution_recorded",
            identity_key="execution_id",
            identity=normalized_execution_id,
            label="experiment execution",
        )
        input_hash = _hash(input_payload)
        policy_hash = _hash(policy_payload)
        admission_id = f"{normalized_execution_id}-admission"
        snapshot = self._projection_snapshot()
        payload: dict[str, Any] = {
            "admission_id": admission_id,
            "execution_id": normalized_execution_id,
            "experiment_id": experiment_id,
            "attempt": attempt,
            "mission_id": self.mission_id,
            "replay_parent_hash": self.events[-1].event_hash if self.events else "0" * 64,
            "input_hash": input_hash,
            "policy_hash": policy_hash,
            "status": "ADMITTED",
        }
        if isinstance(raw_idempotency_key, str):
            payload["idempotency_key"] = raw_idempotency_key
        if raw_timeout_seconds is not None:
            payload["timeout_seconds"] = float(cast(float, raw_timeout_seconds))
        self._append(
            "experiment_execution_admitted",
            payload,
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )
        return normalized_execution_id, admission_id

    def record_experiment_execution(
        self,
        *,
        experiment_id: str,
        attempt: int,
        execution_id: str,
        admission_id: str | None,
        input_payload: Any,
        policy_payload: Any,
        result: Any,
        observation_count: int,
        status: str = "SUCCESS",
        input_hash: str | None = None,
        policy_hash: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        """Settle one admitted experiment-cell invocation exactly once."""

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot record experiment execution in state {self.state}")
        if (
            type(experiment_id) is not str
            or type(attempt) is not int
            or type(execution_id) is not str
            or (admission_id is not None and type(admission_id) is not str)
            or type(observation_count) is not int
            or type(status) is not str
            or (input_hash is not None and type(input_hash) is not str)
            or (policy_hash is not None and type(policy_hash) is not str)
            or (idempotency_key is not None and type(idempotency_key) is not str)
            or (timeout_seconds is not None and type(timeout_seconds) not in (int, float))
            or isinstance(timeout_seconds, bool)
        ):
            raise ValueError("experiment execution settlement contract is invalid")
        if (
            experiment_id not in self.experiments
            or type(attempt) is not int
            or attempt < 1
            or not execution_id.strip()
            or type(observation_count) is not int
            or observation_count < 0
        ):
            raise ValueError("invalid experiment execution settlement")
        normalized_status = status.strip().upper()
        if normalized_status not in {"SUCCESS", "REJECTED", "TIMED_OUT", "CANCELLED"}:
            raise ValueError("invalid experiment execution status")
        normalized_input_hash = _hash(input_payload) if input_hash is None else input_hash
        normalized_policy_hash = _hash(policy_payload) if policy_hash is None else policy_hash
        if not _is_digest(normalized_input_hash) or not _is_digest(normalized_policy_hash):
            raise ValueError("experiment execution settlement hashes are invalid")
        raw_idempotency_key: object = idempotency_key
        raw_timeout_seconds: object = timeout_seconds
        if admission_id is None:
            _, admission_id = self.admit_experiment_execution(
                experiment_id=experiment_id,
                attempt=attempt,
                input_payload=input_payload,
                policy_payload=policy_payload,
                execution_id=execution_id,
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )
        admission = self._require_open_admission(
            admission_kind="experiment_execution_admitted",
            record_kind="experiment_execution_recorded",
            identity_key="execution_id",
            identity=execution_id,
            admission_id=admission_id,
            expected={
                "execution_id": execution_id,
                "experiment_id": experiment_id,
                "attempt": attempt,
                "mission_id": self.mission_id,
                "input_hash": normalized_input_hash,
                "policy_hash": normalized_policy_hash,
                "status": "ADMITTED",
            },
            label="experiment execution",
        )
        if "idempotency_key" in admission:
            if raw_idempotency_key != admission["idempotency_key"]:
                raise ValueError("experiment execution settlement idempotency key does not match admission")
        elif raw_idempotency_key is not None:
            raise ValueError("experiment execution settlement supplied an unexpected idempotency key")
        if "timeout_seconds" in admission:
            if (
                raw_timeout_seconds is None
                or type(raw_timeout_seconds) not in (int, float)
                or isinstance(raw_timeout_seconds, bool)
                or not math.isfinite(float(raw_timeout_seconds))
                or float(raw_timeout_seconds) <= 0
                or float(raw_timeout_seconds) != admission["timeout_seconds"]
            ):
                raise ValueError("experiment execution settlement timeout does not match admission")
        elif raw_timeout_seconds is not None:
            raise ValueError("experiment execution settlement supplied an unexpected timeout")
        snapshot = self._projection_snapshot()
        payload: dict[str, Any] = {
            "admission_id": admission_id,
            "execution_id": execution_id,
            "experiment_id": experiment_id,
            "attempt": attempt,
            "observation_count": observation_count,
            "mission_id": self.mission_id,
            "replay_parent_hash": self.events[-1].event_hash if self.events else "0" * 64,
            "input_hash": normalized_input_hash,
            "result_hash": _hash(
                {
                    "schema": "aegis-experiment-execution-result-v1",
                    "execution_id": execution_id,
                    "observation_count": observation_count,
                    "result": result,
                }
            ),
            "policy_hash": normalized_policy_hash,
            "status": normalized_status,
        }
        if isinstance(raw_idempotency_key, str):
            payload["idempotency_key"] = raw_idempotency_key
        if raw_timeout_seconds is not None:
            payload["timeout_seconds"] = float(cast(float, raw_timeout_seconds))
        self._append(
            "experiment_execution_recorded",
            payload,
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_tool_execution(
        self,
        *,
        tool_name: str,
        input_payload: Any,
        policy_payload: Any,
        effect_class: str,
        actor_role: str = "actor",
        expected_observation_schema: str = "opaque",
        stop_rule: str = "single_call",
        lease_id: int = 1,
        attempt: int = 1,
        execution_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[str, str]:
        """Admit one generic tool side effect before invoking its adapter.

        The generic seam is deliberately typed and replay-visible.  It does
        not execute tools itself; an edge adapter must provide the side effect
        only after this admission succeeds and must settle it exactly once.
        """

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot admit tool execution in state {self.state}")
        if (
            type(tool_name) is not str
            or type(effect_class) is not str
            or type(actor_role) is not str
            or type(expected_observation_schema) is not str
            or type(stop_rule) is not str
            or type(lease_id) is not int
            or type(attempt) is not int
            or (execution_id is not None and type(execution_id) is not str)
            or (idempotency_key is not None and type(idempotency_key) is not str)
            or (timeout_seconds is not None and type(timeout_seconds) not in (int, float))
            or isinstance(timeout_seconds, bool)
        ):
            raise ValueError("tool execution admission contract is invalid")
        normalized_tool = tool_name.strip()
        normalized_effect = effect_class.strip()
        normalized_role = actor_role.strip().lower()
        normalized_schema = expected_observation_schema.strip()
        normalized_stop = stop_rule.strip()
        raw_idempotency_key: object = idempotency_key
        raw_timeout_seconds: object = timeout_seconds
        if (
            not normalized_tool
            or not normalized_effect
            or normalized_role not in {"actor", "observer"}
            or not normalized_schema
            or not normalized_stop
            or type(lease_id) is not int
            or lease_id < 1
            or type(attempt) is not int
            or attempt < 1
            or (
                raw_idempotency_key is not None
                and (type(raw_idempotency_key) is not str or not _is_digest(raw_idempotency_key))
            )
            or (
                raw_timeout_seconds is not None
                and (
                    isinstance(raw_timeout_seconds, bool)
                    or type(raw_timeout_seconds) not in (int, float)
                    or not math.isfinite(float(raw_timeout_seconds))
                    or float(raw_timeout_seconds) <= 0
                )
            )
        ):
            raise ValueError("tool execution admission contract is invalid")
        ordinal = sum(event.kind == "tool_execution_admitted" for event in self.events) + 1
        normalized_execution_id = (
            f"tool-{normalized_tool}-{attempt}-{ordinal}" if execution_id is None else execution_id
        )
        if not normalized_execution_id.strip() or normalized_execution_id in self.tool_execution_admissions:
            raise ValueError("tool execution identity is duplicated or empty")
        admission_id = f"{normalized_execution_id}-admission"
        if any(value["admission_id"] == admission_id for value in self.tool_execution_admissions.values()):
            raise ValueError("tool execution admission identity is duplicated")
        payload: dict[str, Any] = {
            "admission_id": admission_id,
            "execution_id": normalized_execution_id,
            "tool_name": normalized_tool,
            "attempt": attempt,
            "lease_id": lease_id,
            "effect_class": normalized_effect,
            "actor_role": normalized_role,
            "expected_observation_schema": normalized_schema,
            "stop_rule": normalized_stop,
            "mission_id": self.mission_id,
            "replay_parent_hash": self.events[-1].event_hash if self.events else "0" * 64,
            "input_hash": _hash(input_payload),
            "policy_hash": _hash(policy_payload),
            "status": "ADMITTED",
        }
        if isinstance(raw_idempotency_key, str):
            payload["idempotency_key"] = raw_idempotency_key
        if isinstance(raw_timeout_seconds, (int, float)) and not isinstance(raw_timeout_seconds, bool):
            payload["timeout_seconds"] = float(raw_timeout_seconds)
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.tool_execution_admissions[normalized_execution_id] = dict(payload)

        self._append(
            "tool_execution_admitted",
            payload,
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )
        return normalized_execution_id, admission_id

    def record_tool_execution(
        self,
        *,
        tool_name: str,
        execution_id: str,
        admission_id: str,
        input_payload: Any,
        policy_payload: Any,
        result: Any,
        effect_class: str,
        actor_role: str = "actor",
        expected_observation_schema: str = "opaque",
        stop_rule: str = "single_call",
        lease_id: int = 1,
        attempt: int = 1,
        status: str = "SUCCESS",
        input_hash: str | None = None,
        policy_hash: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        """Settle one admitted generic tool invocation exactly once.

        Recovery may only have the hashes retained in the admission (raw
        inputs are intentionally not persisted).  Callers may provide those
        exact hashes for reconciliation; the admission comparison below still
        prevents forged identities or policy changes.
        """

        if (
            type(tool_name) is not str
            or type(execution_id) is not str
            or type(admission_id) is not str
            or type(effect_class) is not str
            or type(actor_role) is not str
            or type(expected_observation_schema) is not str
            or type(stop_rule) is not str
            or type(lease_id) is not int
            or type(attempt) is not int
            or type(status) is not str
            or (input_hash is not None and type(input_hash) is not str)
            or (policy_hash is not None and type(policy_hash) is not str)
            or (idempotency_key is not None and type(idempotency_key) is not str)
            or (timeout_seconds is not None and type(timeout_seconds) not in (int, float))
            or isinstance(timeout_seconds, bool)
        ):
            raise ValueError("tool execution settlement contract is invalid")
        normalized_status = status.strip().upper()
        if self.state == "completed" or (self.state == "aborted" and normalized_status != "CANCELLED"):
            raise ValueError(f"cannot record tool execution in state {self.state}")
        if normalized_status not in {"SUCCESS", "REJECTED", "TIMED_OUT", "CANCELLED"}:
            raise ValueError("invalid tool execution status")
        admission = self.tool_execution_admissions.get(execution_id)
        if admission is None:
            raise ValueError("tool execution references unknown admission")
        if execution_id in self.tool_executions:
            raise ValueError("tool execution settlement is duplicated")
        normalized_input_hash = _hash(input_payload) if input_hash is None else input_hash
        normalized_policy_hash = _hash(policy_payload) if policy_hash is None else policy_hash
        if not _is_digest(normalized_input_hash) or not _is_digest(normalized_policy_hash):
            raise ValueError("tool execution settlement hashes are invalid")
        expected: dict[str, Any] = {
            "admission_id": admission_id,
            "tool_name": tool_name.strip(),
            "attempt": attempt,
            "lease_id": lease_id,
            "effect_class": effect_class.strip(),
            "actor_role": actor_role.strip().lower(),
            "expected_observation_schema": expected_observation_schema.strip(),
            "stop_rule": stop_rule.strip(),
            "input_hash": normalized_input_hash,
            "policy_hash": normalized_policy_hash,
        }
        raw_idempotency_key: object = idempotency_key
        raw_timeout_seconds: object = timeout_seconds
        if "idempotency_key" in admission:
            if raw_idempotency_key != admission["idempotency_key"]:
                raise ValueError("tool execution settlement idempotency key does not match admission")
            expected["idempotency_key"] = cast(str, raw_idempotency_key)
        elif raw_idempotency_key is not None:
            raise ValueError("tool execution settlement supplied an unexpected idempotency key")
        if "timeout_seconds" in admission:
            if (
                raw_timeout_seconds is None
                or isinstance(raw_timeout_seconds, bool)
                or type(raw_timeout_seconds) not in (int, float)
                or not math.isfinite(float(raw_timeout_seconds))
                or float(raw_timeout_seconds) <= 0
                or float(raw_timeout_seconds) != admission["timeout_seconds"]
            ):
                raise ValueError("tool execution settlement timeout does not match admission")
            expected["timeout_seconds"] = float(raw_timeout_seconds)
        elif raw_timeout_seconds is not None:
            raise ValueError("tool execution settlement supplied an unexpected timeout")
        if any(admission.get(key) != value for key, value in expected.items()):
            raise ValueError("tool execution settlement does not match admission")
        payload = {
            **expected,
            "execution_id": execution_id,
            "mission_id": self.mission_id,
            "replay_parent_hash": self.events[-1].event_hash if self.events else "0" * 64,
            "result_hash": _hash(
                {
                    "schema": "aegis-tool-execution-result-v1",
                    "execution_id": execution_id,
                    "result": result,
                }
            ),
            "status": normalized_status,
        }
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.tool_executions.add(execution_id)

        self._append(
            "tool_execution_recorded",
            payload,
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_cancellation(self, *, reason: str, request_id: str | None = None) -> tuple[str, str]:
        """Admit a cancellation request before changing run state."""

        if type(reason) is not str or (request_id is not None and type(request_id) is not str):
            raise ValueError("cancellation admission contract is invalid")
        if self.state in {"completed", "aborted"}:
            return (request_id if request_id is not None else "", "")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("cancellation reason must be non-empty")
        ordinal = sum(event.kind == "cancellation_admitted" for event in self.events) + 1
        normalized_request_id = f"cancel-{ordinal}" if request_id is None else request_id
        if not normalized_request_id.strip():
            raise ValueError("cancellation request identity must be non-empty")
        self._assert_admission_identity_available(
            admission_kind="cancellation_admitted",
            record_kind="cancellation_recorded",
            identity_key="request_id",
            identity=normalized_request_id,
            label="cancellation request",
        )
        admission_id = f"{normalized_request_id}-admission"
        snapshot = self._projection_snapshot()
        self._append(
            "cancellation_admitted",
            {
                "request_id": normalized_request_id,
                "admission_id": admission_id,
                "mission_id": self.mission_id,
                "reason": normalized_reason,
                "replay_parent_hash": self.events[-1].event_hash if self.events else "0" * 64,
                "input_hash": _hash({"request_id": normalized_request_id, "reason": normalized_reason}),
                "policy_hash": _hash({"schema": "aegis-cancellation-policy-v1"}),
                "status": "ADMITTED",
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )
        return normalized_request_id, admission_id

    def record_cancellation(
        self,
        *,
        request_id: str,
        admission_id: str,
        reason: str,
        result: Any,
        status: str = "SUCCESS",
    ) -> None:
        """Settle a cancellation request after the state transition attempt."""

        if (
            type(request_id) is not str
            or type(admission_id) is not str
            or type(reason) is not str
            or type(status) is not str
        ):
            raise ValueError("cancellation settlement contract is invalid")
        if not request_id.strip() or not admission_id.strip() or not reason.strip():
            raise ValueError("cancellation settlement identity is invalid")
        normalized_status = status.strip().upper()
        if normalized_status not in {"SUCCESS", "REJECTED"}:
            raise ValueError("invalid cancellation status")
        self._require_open_admission(
            admission_kind="cancellation_admitted",
            record_kind="cancellation_recorded",
            identity_key="request_id",
            identity=request_id,
            admission_id=admission_id,
            expected={
                "request_id": request_id,
                "mission_id": self.mission_id,
                "reason": reason,
                "input_hash": _hash({"request_id": request_id, "reason": reason}),
                "policy_hash": _hash({"schema": "aegis-cancellation-policy-v1"}),
                "status": "ADMITTED",
            },
            label="cancellation",
        )
        snapshot = self._projection_snapshot()
        self._append(
            "cancellation_recorded",
            {
                "request_id": request_id,
                "admission_id": admission_id,
                "mission_id": self.mission_id,
                "reason": reason,
                "replay_parent_hash": self.events[-1].event_hash if self.events else "0" * 64,
                "result_hash": _hash(
                    {
                        "schema": "aegis-cancellation-result-v1",
                        "request_id": request_id,
                        "result": result,
                    }
                ),
                "status": normalized_status,
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_research_program(
        self,
        *,
        program_hash: str,
        operation_count: int,
        provider: str,
    ) -> str:
        """Admit one research program before invoking its provider."""

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot admit research program in state {self.state}")
        if (
            type(program_hash) is not str
            or type(operation_count) is not int
            or type(provider) is not str
        ):
            raise ValueError("research program admission contract is invalid")
        if not _is_digest(program_hash) or operation_count < 1 or not provider.strip():
            raise ValueError("invalid research program admission")
        self._assert_admission_identity_available(
            admission_kind="research_program_admitted",
            record_kind="research_program_executed",
            identity_key="program_hash",
            identity=program_hash,
            label="research program",
        )
        admission_id = (
            f"research-{program_hash[:16]}-"
            f"{sum(event.kind == 'research_program_admitted' for event in self.events) + 1}"
        )
        snapshot = self._projection_snapshot()
        self._append(
            "research_program_admitted",
            {
                "admission_id": admission_id,
                "program_hash": program_hash,
                "operation_count": operation_count,
                "provider": provider,
                "input_hash": _hash(
                    {
                        "schema": "aegis-research-program-input-v1",
                        "program_hash": program_hash,
                        "provider": provider,
                    }
                ),
                "policy_hash": _hash({"schema": "aegis-research-program-policy-v1"}),
                "status": "ADMITTED",
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )
        return admission_id

    def record_research_program(
        self,
        *,
        program_hash: str,
        operation_count: int,
        candidate_count: int,
        provider: str,
        admission_id: str | None = None,
        status: str = "SUCCESS",
        input_hash: str | None = None,
        policy_hash: str | None = None,
    ) -> None:
        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot record research program in state {self.state}")
        if (
            type(program_hash) is not str
            or type(operation_count) is not int
            or type(candidate_count) is not int
            or type(provider) is not str
            or (admission_id is not None and type(admission_id) is not str)
            or type(status) is not str
            or (input_hash is not None and type(input_hash) is not str)
            or (policy_hash is not None and type(policy_hash) is not str)
        ):
            raise ValueError("research program settlement contract is invalid")
        if (
            not _is_digest(program_hash)
            or operation_count < 1
            or candidate_count < 0
            or not provider.strip()
        ):
            raise ValueError("invalid research program receipt")
        normalized_status = status.strip().upper()
        if normalized_status not in {"SUCCESS", "REJECTED", "CANCELLED"}:
            raise ValueError("invalid research program status")
        if admission_id is None:
            admission_id = self.admit_research_program(
                program_hash=program_hash,
                operation_count=operation_count,
                provider=provider,
            )
        normalized_input_hash = _hash(
            {
                "schema": "aegis-research-program-input-v1",
                "program_hash": program_hash,
                "provider": provider,
            }
        ) if input_hash is None else input_hash
        result_hash = _hash(
            {
                "schema": "aegis-research-program-result-v1",
                "program_hash": program_hash,
                "candidate_count": candidate_count,
            }
        )
        normalized_policy_hash = (
            _hash({"schema": "aegis-research-program-policy-v1"})
            if policy_hash is None
            else policy_hash
        )
        if not _is_digest(normalized_input_hash) or not _is_digest(normalized_policy_hash):
            raise ValueError("research program settlement hashes are invalid")
        self._require_open_admission(
            admission_kind="research_program_admitted",
            record_kind="research_program_executed",
            identity_key="program_hash",
            identity=program_hash,
            admission_id=admission_id,
            expected={
                "program_hash": program_hash,
                "operation_count": operation_count,
                "provider": provider,
                "input_hash": normalized_input_hash,
                "policy_hash": normalized_policy_hash,
                "status": "ADMITTED",
            },
            label="research program",
        )
        snapshot = self._projection_snapshot()
        self._append(
            "research_program_executed",
            {
                "admission_id": admission_id,
                "program_hash": program_hash,
                "operation_count": operation_count,
                "candidate_count": candidate_count,
                "provider": provider,
                "input_hash": normalized_input_hash,
                "result_hash": result_hash,
                "policy_hash": normalized_policy_hash,
                "status": normalized_status,
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_browser_action(
        self,
        *,
        action_kind: str,
        action: Any,
        policy: BrowserCellPolicy,
        lease_id: int,
        action_id: str,
    ) -> str:
        """Admit a browser actor intent before executing its side effect."""

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot admit browser action in state {self.state}")
        if (
            type(action_kind) is not str
            or type(action) is not dict
            or type(policy) is not BrowserCellPolicy
            or type(lease_id) is not int
            or type(action_id) is not str
        ):
            raise ValueError("browser action admission contract is invalid")
        policy.validate()
        normalized_kind = action_kind.strip().lower()
        if normalized_kind not in _BROWSER_ACTION_KINDS:
            raise ValueError("invalid browser action kind")
        if lease_id < 1 or not action_id.strip():
            raise ValueError("browser action lease and identity must be valid")
        self._assert_admission_identity_available(
            admission_kind="browser_action_admitted",
            record_kind="browser_action_recorded",
            identity_key="action_id",
            identity=action_id,
            label="browser action",
        )
        admission_id = f"{action_id}-admission"
        snapshot = self._projection_snapshot()
        self._append(
            "browser_action_admitted",
            {
                "admission_id": admission_id,
                "action_id": action_id,
                "lease_id": lease_id,
                "actor_role": "actor",
                "action_kind": normalized_kind,
                "input_hash": _hash(action),
                "policy_hash": _hash(
                    {"schema": "aegis-browser-cell-policy-v1", **asdict(policy)}
                ),
                "status": "ADMITTED",
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )
        return admission_id

    def record_browser_action(
        self,
        *,
        action_id: str,
        admission_id: str,
        action_kind: str,
        action: Any,
        result: Any,
        policy: BrowserCellPolicy,
        lease_id: int,
        status: str = "SUCCESS",
        input_hash: str | None = None,
        policy_hash: str | None = None,
    ) -> None:
        """Settle a previously admitted browser actor intent."""

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot record browser action in state {self.state}")
        if (
            type(action_id) is not str
            or type(admission_id) is not str
            or type(action_kind) is not str
            or type(action) is not dict
            or type(policy) is not BrowserCellPolicy
            or type(lease_id) is not int
            or type(status) is not str
            or (input_hash is not None and type(input_hash) is not str)
            or (policy_hash is not None and type(policy_hash) is not str)
        ):
            raise ValueError("browser action settlement contract is invalid")
        policy.validate()
        normalized_kind = action_kind.strip().lower()
        normalized_status = status.strip().upper()
        if normalized_kind not in _BROWSER_ACTION_KINDS:
            raise ValueError("invalid browser action kind")
        if normalized_status not in _BROWSER_SETTLEMENT_STATUSES:
            raise ValueError("invalid browser action status")
        if lease_id < 1 or not action_id.strip() or not admission_id.strip():
            raise ValueError("browser action settlement identity is invalid")
        normalized_input_hash = _hash(action) if input_hash is None else input_hash
        normalized_policy_hash = _hash(
            {"schema": "aegis-browser-cell-policy-v1", **asdict(policy)}
        ) if policy_hash is None else policy_hash
        if not _is_digest(normalized_input_hash) or not _is_digest(normalized_policy_hash):
            raise ValueError("browser action settlement hashes are invalid")
        self._require_open_admission(
            admission_kind="browser_action_admitted",
            record_kind="browser_action_recorded",
            identity_key="action_id",
            identity=action_id,
            admission_id=admission_id,
            expected={
                "action_id": action_id,
                "lease_id": lease_id,
                "action_kind": normalized_kind,
                "input_hash": normalized_input_hash,
                "policy_hash": normalized_policy_hash,
                "status": "ADMITTED",
            },
            label="browser action",
        )
        snapshot = self._projection_snapshot()
        self._append(
            "browser_action_recorded",
            {
                "action_id": action_id,
                "admission_id": admission_id,
                "lease_id": lease_id,
                "actor_role": "actor",
                "action_kind": normalized_kind,
                "input_hash": normalized_input_hash,
                "result_hash": _hash(
                    {
                        "schema": "aegis-browser-action-result-v1",
                        "action_kind": normalized_kind,
                        "result": result,
                    }
                ),
                "policy_hash": normalized_policy_hash,
                "status": normalized_status,
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_browser_observation(
        self,
        *,
        observation_kind: str,
        action: Any,
        policy: BrowserCellPolicy,
        lease_id: int,
        observation_count: int,
    ) -> tuple[str, str]:
        """Admit a browser observer intent before reading page state."""

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot admit browser observation in state {self.state}")
        if (
            type(observation_kind) is not str
            or type(action) is not dict
            or type(policy) is not BrowserCellPolicy
            or type(lease_id) is not int
            or type(observation_count) is not int
        ):
            raise ValueError("browser observation admission contract is invalid")
        policy.validate()
        normalized_kind = observation_kind.strip().lower()
        if normalized_kind not in _BROWSER_OBSERVATION_KINDS:
            raise ValueError("invalid browser observation kind")
        if lease_id < 1 or observation_count < 1:
            raise ValueError("browser observation lease and count must be positive")
        observation_id = (
            f"lease-{lease_id}-observation-"
            f"{sum(event.kind == 'browser_observation_admitted' for event in self.events) + 1}"
        )
        self._assert_admission_identity_available(
            admission_kind="browser_observation_admitted",
            record_kind="browser_observation_recorded",
            identity_key="observation_id",
            identity=observation_id,
            label="browser observation",
        )
        admission_id = f"{observation_id}-admission"
        snapshot = self._projection_snapshot()
        self._append(
            "browser_observation_admitted",
            {
                "admission_id": admission_id,
                "observation_id": observation_id,
                "lease_id": lease_id,
                "observer_role": "observer",
                "observation_kind": normalized_kind,
                "observation_count": observation_count,
                "input_hash": _hash(action),
                "policy_hash": _hash(
                    {"schema": "aegis-browser-cell-policy-v1", **asdict(policy)}
                ),
                "status": "ADMITTED",
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )
        return observation_id, admission_id

    def record_browser_observation(
        self,
        *,
        observation_kind: str,
        action: Any,
        result: Any,
        policy: BrowserCellPolicy,
        lease_id: int,
        observation_count: int,
        admission_id: str | None = None,
        observation_id: str | None = None,
        status: str = "SUCCESS",
        input_hash: str | None = None,
        policy_hash: str | None = None,
    ) -> None:
        """Commit one read-only browser observation as replay-visible evidence."""

        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot record browser observation in state {self.state}")
        if (
            type(observation_kind) is not str
            or type(action) is not dict
            or type(policy) is not BrowserCellPolicy
            or type(lease_id) is not int
            or type(observation_count) is not int
            or type(status) is not str
            or (admission_id is not None and type(admission_id) is not str)
            or (observation_id is not None and type(observation_id) is not str)
            or (input_hash is not None and type(input_hash) is not str)
            or (policy_hash is not None and type(policy_hash) is not str)
        ):
            raise ValueError("browser observation settlement contract is invalid")
        policy.validate()
        normalized_kind = observation_kind.strip().lower()
        if normalized_kind not in _BROWSER_OBSERVATION_KINDS:
            raise ValueError("invalid browser observation kind")
        if lease_id < 1 or observation_count < 1:
            raise ValueError("browser observation lease and count must be positive")
        normalized_status = status.strip().upper()
        if normalized_status not in _BROWSER_SETTLEMENT_STATUSES:
            raise ValueError("invalid browser observation status")
        if admission_id is None and observation_id is not None:
            raise ValueError("browser observation settlement identity is invalid")
        if admission_id is None:
            observation_id, admission_id = self.admit_browser_observation(
                observation_kind=normalized_kind,
                action=action,
                policy=policy,
                lease_id=lease_id,
                observation_count=observation_count,
            )
        if not observation_id or not admission_id.strip():
            raise ValueError("browser observation settlement identity is invalid")
        normalized_input_hash = _hash(action) if input_hash is None else input_hash
        normalized_policy_hash = _hash(
            {"schema": "aegis-browser-cell-policy-v1", **asdict(policy)}
        ) if policy_hash is None else policy_hash
        if not _is_digest(normalized_input_hash) or not _is_digest(normalized_policy_hash):
            raise ValueError("browser observation settlement hashes are invalid")
        self._require_open_admission(
            admission_kind="browser_observation_admitted",
            record_kind="browser_observation_recorded",
            identity_key="observation_id",
            identity=observation_id,
            admission_id=admission_id,
            expected={
                "observation_id": observation_id,
                "lease_id": lease_id,
                "observation_kind": normalized_kind,
                "observation_count": observation_count,
                "input_hash": normalized_input_hash,
                "policy_hash": normalized_policy_hash,
                "status": "ADMITTED",
            },
            label="browser observation",
        )
        snapshot = self._projection_snapshot()
        self._append(
            "browser_observation_recorded",
            {
                "admission_id": admission_id,
                "observation_id": observation_id,
                "lease_id": lease_id,
                "observer_role": "observer",
                "observation_kind": normalized_kind,
                "observation_count": observation_count,
                "input_hash": normalized_input_hash,
                "result_hash": _hash(
                    {
                        "schema": "aegis-browser-observation-result-v1",
                        "observation_kind": normalized_kind,
                        "result": result,
                    }
                ),
                "policy_hash": normalized_policy_hash,
                "status": normalized_status,
            },
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def admit_exploration(self, tokens: int) -> None:
        """Commit one bounded exploration allocation through the native fence."""

        if type(tokens) is not int or tokens < 1:
            raise ValueError("exploration token allocation must be a positive integer")
        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot admit exploration in state {self.state}")
        spend = {
            "tokens": tokens,
            "money_minor_units": 0,
            "time_ms": 0,
            "tool_calls": 0,
            "api_calls": 0,
            "cpu_ms": 0,
            "risk_units": 0,
        }
        snapshot = self._projection_snapshot()
        self._append(
            "budget_admitted",
            spend,
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def begin_finalization(self) -> None:
        """Open the finalization boundary exactly once for this run."""

        if self._native_finalization_started:
            return
        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot begin finalization in state {self.state}")
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self._native_finalization_started = True

        self._append(
            "finalization_started",
            {"reason": "dossier_review"},
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def reopen_for_critical_gap(self, *, detail: str = "") -> None:
        """Reopen finalization only when an explicit critical gap is recorded."""

        if not self._native_finalization_started:
            raise ValueError("finalization has not started")
        if self.state in {"completed", "aborted"}:
            raise ValueError(f"cannot reopen finalization in state {self.state}")
        snapshot = self._projection_snapshot()
        self._append(
            "finalization_reopened",
            {"reason": detail.strip() or "critical_gap"},
            rollback_snapshot=snapshot,
            event_state_epoch=self.state_epoch + 1,
        )

    def _append(
        self,
        kind: str,
        payload: Any,
        *,
        rollback_snapshot: tuple[Any, ...] | None = None,
        projection_apply: Callable[[], None] | None = None,
        event_state_epoch: int | None = None,
    ) -> None:
        is_external_admission = kind in _EXTERNAL_ADMISSION_EVENT_KINDS
        if is_external_admission and self.external_attempt_count >= self.external_attempt_budget:
            raise RuntimeError("lab external attempt budget exhausted")
        # Store the canonical payload alongside its digest.  A digest-only
        # event stream can detect tampering but cannot reconstruct a reducer
        # after restart; keeping the immutable payload makes replay auditable.
        if self.trust_policy_hash is not None and isinstance(payload, Mapping):
            typed_payload = cast(Mapping[str, Any], payload)
            existing_trust_hash = typed_payload.get("trust_policy_hash")
            if existing_trust_hash is not None and existing_trust_hash != self.trust_policy_hash:
                raise ValueError("event trust policy hash does not match Lab policy")
            payload = {**typed_payload, "trust_policy_hash": self.trust_policy_hash}
        canonical_payload = json.loads(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        )
        if kind == "mission_created" and self._native_mission_hash is not None:
            payload_hash = self._native_mission_hash
        elif kind == "budget_admitted":
            payload_hash = _rust_canonical_hash(_budget_vector_payload(canonical_payload))
        elif self._native_controller is not None:
            payload_hash = _native_projection_payload_hash(canonical_payload)
        else:
            payload_hash = _hash(canonical_payload)
        previous = self.events[-1].event_hash if self.events else "0" * 64
        sequence = len(self.events) + 1
        candidate_state_epoch = (
            self.state_epoch if event_state_epoch is None else event_state_epoch
        )
        event_hash = _rust_event_hash(
            sequence,
            candidate_state_epoch,
            kind,
            payload_hash,
            previous,
        )
        candidate = LabEvent(
            sequence,
            candidate_state_epoch,
            kind,
            payload_hash,
            previous,
            event_hash,
            canonical_payload,
        )
        # Existing callers may perform their projection update immediately
        # before ``_append``; the optional callback provides the native-first
        # two-phase form for migrated lanes. Native admission and the callback
        # are atomic from the caller's point of view: if either rejects, every
        # reducer field that could have changed is restored before the error is
        # exposed. The copies are small bounded projections; durable replay
        # remains the source of truth for large artifacts.
        snapshot = rollback_snapshot or self._projection_snapshot()
        try:
            if not self._admit_native_controller_event(candidate):
                self._validate_native_event_chain((*self.events, candidate))
            if projection_apply is not None:
                projection_apply()
            self.state_epoch = candidate_state_epoch
            if is_external_admission:
                self.external_attempt_count += 1
        except BaseException:
            self._restore_projection(snapshot)
            raise
        self.events.append(candidate)
        event = self.events[-1]
        for listener in tuple(self._event_listeners):
            listener(event)

    def _projection_snapshot(self) -> tuple[Any, ...]:
        """Capture mutable reducer fields for atomic native-admission rollback."""

        native_snapshot: str | None = None
        native_controller = self._native_controller
        snapshot_reader = getattr(native_controller, "snapshot_json", None)
        if callable(snapshot_reader):
            try:
                raw_native_snapshot = snapshot_reader()
            except Exception as exc:
                raise RuntimeError("native Lab controller snapshot is unavailable") from exc
            if not isinstance(raw_native_snapshot, str) or not raw_native_snapshot:
                raise RuntimeError("native Lab controller snapshot is invalid")
            native_snapshot = raw_native_snapshot

        return (
            list(self.events),
            self.state,
            self.state_epoch,
            dict(self.sources),
            dict(self.claims),
            dict(self.hypotheses),
            dict(self.experiments),
            dict(self.observations),
            dict(self.skill_admissions),
            dict(self.tool_execution_admissions),
            set(self.tool_executions),
            list(self.blockers),
            list(self.security_events),
            self.replay_archive,
            self.execution_cell_manifest,
            self.external_attempt_count,
            self._native_finalization_started,
            native_snapshot,
        )

    def _restore_projection(self, snapshot: tuple[Any, ...]) -> None:
        (
            events,
            self.state,
            self.state_epoch,
            sources,
            claims,
            hypotheses,
            experiments,
            observations,
            skill_admissions,
            tool_execution_admissions,
            tool_executions,
            blockers,
            security_events,
            self.replay_archive,
            self.execution_cell_manifest,
            external_attempt_count,
            native_finalization_started,
            native_snapshot,
        ) = snapshot
        self.events = events
        self.sources = sources
        self.claims = claims
        self.hypotheses = hypotheses
        self.experiments = experiments
        self.observations = observations
        self.skill_admissions = skill_admissions
        self.tool_execution_admissions = tool_execution_admissions
        self.tool_executions = tool_executions
        self.blockers = blockers
        self.security_events = security_events
        self.external_attempt_count = external_attempt_count
        self._native_finalization_started = native_finalization_started
        if native_snapshot is None:
            return
        native_controller = self._native_controller
        snapshot_restorer = getattr(native_controller, "restore_snapshot_json", None)
        if not callable(snapshot_restorer):
            raise RuntimeError("native Lab controller snapshot restore is unavailable")
        try:
            snapshot_restorer(native_snapshot)
        except Exception as exc:
            raise RuntimeError("native Lab controller snapshot restore failed") from exc
        # A compatibility adapter can expose the restore method but silently
        # ignore the payload.  Treat that as a failed rollback rather than
        # allowing a Python view and native reducer with different prefixes to
        # continue under one run identity.
        snapshot_reader = getattr(native_controller, "snapshot_json", None)
        if not callable(snapshot_reader):
            raise RuntimeError("native Lab controller snapshot verification is unavailable")
        try:
            restored_native_snapshot = snapshot_reader()
            if not isinstance(restored_native_snapshot, (str, bytes, bytearray)):
                raise TypeError("native Lab controller snapshot is not JSON text")
            expected_native_state = json.loads(native_snapshot)
            actual_native_state = json.loads(restored_native_snapshot)
        except Exception as exc:
            raise RuntimeError("native Lab controller snapshot restore verification failed") from exc
        if expected_native_state != actual_native_state:
            raise RuntimeError("native Lab controller snapshot restore mismatch")

    @staticmethod
    def _native_event_wire(events: tuple[LabEvent, ...]) -> str:
        return json.dumps(
            [
                {
                    "sequence": event.sequence,
                    "state_epoch": event.state_epoch,
                    "kind": _RUST_EVENT_KINDS.get(event.kind, "ReviewRecorded"),
                    "payload_hash": list(bytes.fromhex(event.payload_hash)),
                    "previous_event_hash": list(bytes.fromhex(event.previous_event_hash)),
                    "event_hash": list(bytes.fromhex(event.event_hash)),
                }
                for event in events
            ]
        )

    @staticmethod
    def _native_event_json(event: LabEvent) -> str:
        """Serialize one projection event using Rust ``LabEvent`` serde wire."""

        return json.dumps(
            {
                "sequence": event.sequence,
                "state_epoch": event.state_epoch,
                "kind": _RUST_EVENT_KINDS.get(event.kind, "ReviewRecorded"),
                "payload_hash": list(bytes.fromhex(event.payload_hash)),
                "previous_event_hash": list(bytes.fromhex(event.previous_event_hash)),
                "event_hash": list(bytes.fromhex(event.event_hash)),
            },
            separators=(",", ":"),
        )

    def bind_execution_cell_manifest(
        self,
        manifest: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> None:
        """Seal the execution-cell inventory into the Lab event ledger.

        The registry is an authority boundary, so its snapshot metadata must
        not exist only as a mutable top-level field.  This event records the
        canonical manifest and its native projection-payload digest before any
        controller-selected cell can execute.
        """

        if type(manifest) not in (list, tuple):
            raise TypeError("execution cell manifest must be a sequence")
        if any(type(item) is not dict for item in manifest):
            raise TypeError("execution cell manifest entries must be mappings")
        if any(event.kind == "execution_cell_manifest_recorded" for event in self.events):
            raise RuntimeError("execution cell manifest is already bound")
        if self.execution_cell_manifest:
            raise RuntimeError("execution cell manifest is already configured")

        normalized_manifest: list[dict[str, Any]] = []
        for raw_item in manifest:
            item = raw_item
            if any(type(key) is not str for key in item):
                raise ValueError("execution cell manifest keys must be strings")
            normalized = dict(item)
            for key in ("action_kinds", "capabilities", "effect_classes", "trust_levels"):
                raw_values = normalized.get(key)
                if type(raw_values) not in (list, tuple):
                    raise ValueError("execution cell manifest policy fields must be sequences")
                normalized[key] = tuple(cast(list[Any] | tuple[Any, ...], raw_values))
            normalized_manifest.append(normalized)

        manifest_value = [dict(item) for item in normalized_manifest]
        manifest_hash = _execution_cell_manifest_hash(manifest_value)
        payload = {
            "schema": _EXECUTION_CELL_MANIFEST_SCHEMA,
            "cell_count": len(manifest_value),
            "manifest": manifest_value,
            "manifest_hash": manifest_hash,
        }
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.execution_cell_manifest = tuple(normalized_manifest)

        self._append(
            "execution_cell_manifest_recorded",
            payload,
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def _admit_native_controller_event(self, event: LabEvent) -> bool:
        """Admit one projection event into the native controller, if present."""

        native_controller = self._native_controller
        if native_controller is None:
            return False
        admit_event = getattr(native_controller, "admit_event_json", None)
        if not callable(admit_event):
            if self.require_native_authority:
                raise RuntimeError("native Lab controller event admission is unavailable")
            return False
        try:
            projection_state: str | None = None
            if event.kind == "state_changed":
                raw_payload = event.payload
                if isinstance(raw_payload, dict):
                    state_payload = cast(dict[str, Any], raw_payload)
                    state_value = str(state_payload.get("state", ""))
                else:
                    state_value = ""
                projection_state = state_value if state_value.strip() else self.state
            event_json = self._native_event_json(event)
            record_admitter = getattr(native_controller, "admit_record_json", None)
            budget_admitter = getattr(native_controller, "admit_exploration_event_json", None)
            if event.kind == "budget_admitted" and callable(budget_admitter):
                if event.state_epoch < 1:
                    raise ValueError("budget admission event epoch must be positive")
                spend = _budget_vector_payload(event.payload)
                budget_admitter(
                    event_json,
                    event.state_epoch - 1,
                    json.dumps(spend, separators=(",", ":")),
                )
            elif (
                event.kind != "mission_created"
                and callable(record_admitter)
            ):
                payload_json = json.dumps(
                    event.payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                record_admitter(event_json, payload_json, projection_state)
            elif projection_state is None:
                admit_event(event_json)
            else:
                try:
                    admit_event(event_json, projection_state)
                except TypeError:
                    # Keep compatibility with development doubles exposing the
                    # original one-argument verifier surface.
                    admit_event(event_json)
        except Exception as exc:
            raise RuntimeError("native Lab controller rejected event append") from exc
        return True

    def _validate_native_event_chain(self, events: tuple[LabEvent, ...]) -> None:
        """Ask Rust to admit each append when its authority is available.

        Development mode may run the projection without a compiled extension,
        but a loaded native verifier is never advisory: a rejection aborts the
        mutation before the Python event becomes visible.  Production policy
        can additionally require the extension to be present.
        """

        try:
            aegis_nerve = _native_lab_module()
        except ImportError as exc:
            if self.require_native_authority:
                raise RuntimeError("native Lab authority is required but unavailable") from exc
            return
        verifier = getattr(aegis_nerve, "aegis_lab_verify_event_chain", None)
        if not callable(verifier):
            if self.require_native_authority:
                raise RuntimeError("native Lab authority verifier is unavailable")
            return
        try:
            accepted = verifier(self._native_event_wire(events))
            if type(accepted) is not bool:
                raise TypeError("native Lab authority verifier must return a boolean")
        except Exception as exc:
            raise RuntimeError("native Lab authority verification failed") from exc
        if not accepted:
            raise RuntimeError("native Lab authority rejected event append")

    def _validate_native_transition(self, current: str, next_state: str) -> None:
        """Ask Rust to admit a state transition before projection mutation."""

        try:
            aegis_nerve = _native_lab_module()
        except ImportError as exc:
            if self.require_native_authority:
                raise RuntimeError("native Lab authority is required but unavailable") from exc
            return
        validator = getattr(aegis_nerve, "aegis_lab_validate_transition", None)
        if not callable(validator):
            if self.require_native_authority:
                raise RuntimeError("native Lab transition validator is unavailable")
            return
        try:
            accepted = validator(current, next_state)
            if type(accepted) is not bool:
                raise TypeError("native Lab transition validator must return a boolean")
        except Exception as exc:
            raise RuntimeError("native Lab transition validation failed") from exc
        if not accepted:
            raise RuntimeError("native Lab authority rejected state transition")

    def transition(self, state: str) -> None:
        allowed: dict[str, set[str]] = {
            "planned": {"researching", "blocked", "aborted"},
            "researching": {"experimenting", "reviewing", "blocked", "aborted"},
            "experimenting": {"researching", "reviewing", "blocked", "aborted"},
            "reviewing": {"completed", "researching", "blocked", "aborted"},
            "blocked": {"researching", "aborted"},
            # A post-completion release gate (for example replay archive
            # publication) may invalidate the dossier without hiding the
            # original completion event.
            "completed": {"blocked", "aborted"},
        }
        if state == self.state:
            return
        if state not in allowed.get(self.state, set[str]()):
            raise ValueError(f"invalid lab transition {self.state}->{state}")
        snapshot = self._projection_snapshot()
        self._validate_native_transition(self.state, state)

        def apply_projection() -> None:
            self.state = state

        self._append(
            "state_changed",
            {"state": state},
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def pause(self) -> None:
        if self.state not in {"planned", "researching", "experimenting", "reviewing"}:
            raise ValueError(f"cannot pause lab in state {self.state}")
        self.transition("blocked")
        self.record_blocker("paused_by_operator")

    def resume(self) -> None:
        if self.state != "blocked":
            raise ValueError(f"cannot resume lab in state {self.state}")
        self.resolve_blocker("paused_by_operator", detail="operator_resume")
        self.transition("researching")

    def abort(self) -> None:
        if self.state in {"completed", "aborted"}:
            return
        request_id, admission_id = self.admit_cancellation(reason="abort")
        try:
            self.transition("aborted")
        except (RuntimeError, TypeError, ValueError):
            # The failed transition is itself a replay-visible cancellation
            # settlement; the run remains in its previous non-terminal state.
            self.record_cancellation(
                request_id=request_id,
                admission_id=admission_id,
                reason="abort",
                result={"state": self.state},
                status="REJECTED",
            )
            raise
        self.record_cancellation(
            request_id=request_id,
            admission_id=admission_id,
            reason="abort",
            result={"state": self.state},
            status="SUCCESS",
        )

    def add_source(self, source: SourceRecord) -> None:
        if self.state not in {"planned", "researching"}:
            raise ValueError(f"cannot add source in state {self.state}")
        if type(source) is not SourceRecord:
            raise ValueError("invalid or duplicate source record")
        try:
            source.validate()
        except ValueError as exc:
            raise ValueError("invalid or duplicate source record") from exc
        if source.source_id in self.sources:
            raise ValueError("invalid or duplicate source record")
        snapshot = self._projection_snapshot()
        if self.state == "planned":
            self.transition("researching")

        def apply_projection() -> None:
            self.sources[source.source_id] = source

        self._append(
            "source_captured",
            asdict(source),
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def add_claim(self, claim: ClaimRecord) -> None:
        if self.state not in {"researching", "experimenting", "reviewing"}:
            raise ValueError(f"cannot add claim in state {self.state}")
        if type(claim) is not ClaimRecord:
            raise ValueError("claim must cite known sources and have bounded confidence")
        try:
            claim.validate()
        except ValueError as exc:
            raise ValueError("claim must cite known sources and have bounded confidence") from exc
        if (
            any(source_id not in self.sources for source_id in claim.source_ids)
            or claim.claim_id in self.claims
        ):
            raise ValueError("claim must cite known sources and have bounded confidence")
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.claims[claim.claim_id] = claim

        self._append(
            "claim_recorded",
            asdict(claim),
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def add_hypothesis(self, hypothesis: HypothesisRecord) -> None:
        if self.state not in {"researching", "experimenting", "reviewing"}:
            raise ValueError(f"cannot add hypothesis in state {self.state}")
        if type(hypothesis) is not HypothesisRecord:
            raise ValueError("hypothesis must include falsifiers and known claim links")
        try:
            hypothesis.validate()
        except ValueError as exc:
            raise ValueError("hypothesis must include falsifiers and known claim links") from exc
        if (
            any(claim_id not in self.claims for claim_id in (*hypothesis.supporting_claim_ids, *hypothesis.contradicting_claim_ids))
            or hypothesis.hypothesis_id in self.hypotheses
        ):
            raise ValueError("hypothesis must include falsifiers and known claim links")
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.hypotheses[hypothesis.hypothesis_id] = hypothesis

        self._append(
            "hypothesis_recorded",
            asdict(hypothesis),
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def add_experiment(self, experiment: ExperimentSpec) -> None:
        if self.state not in {"researching", "experimenting", "reviewing"}:
            raise ValueError(f"cannot add experiment in state {self.state}")
        experiment.validate()
        if experiment.hypothesis_id not in self.hypotheses or experiment.experiment_id in self.experiments:
            raise ValueError("experiment requires controls and at least five preregistered seeds")
        DEFAULT_UNIT_REGISTRY.signature(experiment.measurement_unit)
        snapshot = self._projection_snapshot()
        if self.state == "researching":
            self.transition("experimenting")

        def apply_projection() -> None:
            self.experiments[experiment.experiment_id] = experiment

        self._append(
            "experiment_scheduled",
            asdict(experiment),
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def add_observation(self, observation: ObservationRecord) -> None:
        if self.state not in {"experimenting", "reviewing"}:
            raise ValueError(f"cannot add observation in state {self.state}")
        observation.validate()
        experiment = self.experiments.get(observation.experiment_id)
        if (
            experiment is None
            or observation.observation_id in self.observations
        ):
            raise ValueError("invalid observation or unknown experiment")
        try:
            if not DEFAULT_UNIT_REGISTRY.compatible(observation.unit, experiment.measurement_unit):
                raise ValueError("observation unit is incompatible with experiment unit")
        except ValueError as exc:
            raise ValueError("invalid observation unit") from exc
        if observation.seed not in experiment.preregistered_seeds:
            raise ValueError("observation seed was not preregistered")
        if experiment.uncertainty_required and observation.uncertainty is None:
            raise ValueError("experiment requires an uncertainty estimate")
        if observation.replication_of is not None:
            parent = self.observations.get(observation.replication_of)
            if (
                parent is None
                or parent.experiment_id != observation.experiment_id
                or not observation.operator_id.strip()
                or observation.environment_hash == parent.environment_hash
                or not observation.clean
            ):
                raise ValueError("clean replication must be independent and explicitly identified")
        try:
            normalized_measurement = DEFAULT_UNIT_REGISTRY.convert(
                observation.measurement,
                observation.unit,
                experiment.measurement_unit,
            )
            normalized_uncertainty = (
                DEFAULT_UNIT_REGISTRY.convert(
                    observation.uncertainty,
                    observation.unit,
                    experiment.measurement_unit,
                )
                if observation.uncertainty is not None
                else None
            )
        except ValueError as exc:
            raise ValueError("observation unit conversion failed") from exc
        normalized = replace(
            observation,
            measurement=normalized_measurement,
            unit=experiment.measurement_unit,
            uncertainty=normalized_uncertainty,
            reported_unit=observation.reported_unit or observation.unit,
        )
        snapshot = self._projection_snapshot()

        def apply_projection() -> None:
            self.observations[normalized.observation_id] = normalized

        self._append(
            "observation_recorded",
            asdict(normalized),
            rollback_snapshot=snapshot,
            projection_apply=apply_projection,
            event_state_epoch=self.state_epoch + 1,
        )

    def _assert_projection_admission_consistency(self) -> None:
        """Keep mutable admission projections bound to immutable events.

        Native snapshots may expose admission identities, but older/native
        compatibility reducers do not necessarily retain every admission
        field.  The Python projection therefore needs its own binding check:
        a same-ID metadata mutation must not be exportable as if it were the
        admission that was actually appended to the event ledger.
        """

        def canonical(value: Any) -> Any:
            try:
                normalized: Any = json.loads(
                    json.dumps(value, sort_keys=True, separators=(",", ":"))
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    "native projection diverged: admission is not canonical JSON"
                ) from exc
            if isinstance(normalized, dict):
                cast(dict[Any, Any], normalized).pop("trust_policy_hash", None)
            return cast(Any, normalized)

        def event_admissions(
            kind: str,
            identity_key: str,
        ) -> dict[str, tuple[LabEvent, dict[str, Any]]]:
            result: dict[str, tuple[LabEvent, dict[str, Any]]] = {}
            for event in self.events:
                if event.kind != kind:
                    continue
                payload = event.payload
                if type(payload) is not dict or any(
                    type(key) is not str for key in cast(dict[Any, Any], payload)
                ):
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=invalid_admission_payload"
                    )
                typed_payload = cast(dict[str, Any], payload)
                identity = typed_payload.get(identity_key)
                if type(identity) is not str or not identity.strip():
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=invalid_admission_identity"
                    )
                if identity in result:
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=duplicate_admission_identity"
                    )
                result[identity] = (event, typed_payload)
            return result

        tool_events = event_admissions("tool_execution_admitted", "execution_id")
        if set(self.tool_execution_admissions) != set(tool_events):
            raise RuntimeError("native projection diverged: tool_execution_admissions=identity_set")
        for execution_id, admission in self.tool_execution_admissions.items():
            if type(execution_id) is not str or type(admission) is not dict:
                raise RuntimeError("native projection diverged: tool_execution_admissions=invalid")
            _, event_payload = tool_events[execution_id]
            if canonical(admission) != canonical(event_payload):
                raise RuntimeError(
                    f"native projection diverged: tool_execution_admission:{execution_id}=different"
                )

        skill_events = event_admissions("skill_admission_recorded", "admission_hash")
        if set(self.skill_admissions) != set(skill_events):
            raise RuntimeError("native projection diverged: skill_admissions=identity_set")
        for admission_hash, admission in self.skill_admissions.items():
            if type(admission_hash) is not str or type(admission) is not SkillAdmission:
                raise RuntimeError("native projection diverged: skill_admissions=invalid")
            event, event_payload = skill_events[admission_hash]
            raw_event_binding = event_payload.get("admission_event_hash", "")
            if type(raw_event_binding) is not str or raw_event_binding:
                raise RuntimeError(
                    f"native projection diverged: event_{event.sequence}=skill_event_binding"
                )
            expected = dict(event_payload)
            expected["admission_event_hash"] = event.event_hash
            if canonical(asdict(admission)) != canonical(expected):
                raise RuntimeError(
                    f"native projection diverged: skill_admission:{admission_hash}=different"
                )

        tool_settlement_events = event_admissions("tool_execution_recorded", "execution_id")
        if self.tool_executions != set(tool_settlement_events):
            raise RuntimeError("native projection diverged: tool_executions=identity_set")

    def _assert_projection_record_consistency(self) -> None:
        """Bind typed scientific records to the events that admitted them."""

        mismatches: list[str] = []

        def canonical(value: Any) -> Any:
            try:
                normalized: Any = json.loads(
                    json.dumps(value, sort_keys=True, separators=(",", ":"))
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    "native projection diverged: record is not canonical JSON"
                ) from exc
            if isinstance(normalized, dict):
                cast(dict[Any, Any], normalized).pop("trust_policy_hash", None)
            return cast(Any, normalized)

        record_specs: tuple[tuple[str, str, Mapping[str, Any]], ...] = (
            ("source_captured", "source_id", cast(Mapping[str, Any], self.sources)),
            ("claim_recorded", "claim_id", cast(Mapping[str, Any], self.claims)),
            ("hypothesis_recorded", "hypothesis_id", cast(Mapping[str, Any], self.hypotheses)),
            ("experiment_scheduled", "experiment_id", cast(Mapping[str, Any], self.experiments)),
            ("observation_recorded", "observation_id", cast(Mapping[str, Any], self.observations)),
        )
        for kind, identifier, records in record_specs:
            admitted: dict[str, Any] = {}
            for event in self.events:
                if event.kind != kind:
                    continue
                raw_payload = event.payload
                if type(raw_payload) is not dict or any(
                    type(key) is not str for key in cast(dict[Any, Any], raw_payload)
                ):
                    mismatches.append(f"{kind}:event_{event.sequence}=invalid")
                    continue
                payload = cast(dict[str, Any], raw_payload)
                record_id = payload.get(identifier)
                if type(record_id) is not str or not record_id.strip():
                    mismatches.append(f"{kind}:event_{event.sequence}=identity")
                    continue
                if record_id in admitted:
                    mismatches.append(f"{kind}:{record_id}=duplicate")
                    continue
                admitted[record_id] = canonical(payload)
            current: dict[str, Any] = {}
            for record_id, record in records.items():
                if type(record_id) is not str:
                    mismatches.append(f"{kind}=non_string_identity")
                    continue
                try:
                    current[record_id] = canonical(asdict(record))
                except (TypeError, ValueError, AttributeError) as exc:
                    raise RuntimeError(
                        f"native projection diverged: {kind}:{record_id}=invalid"
                    ) from exc
            record_ids: set[str] = set(admitted.keys()) | set(current.keys())
            mismatches.extend(
                f"{kind}:{record_id}=different"
                for record_id in record_ids
                if admitted.get(record_id) != current.get(record_id)
            )
            if mismatches:
                # Continue collecting the other record classes only when the
                # current class is clean; one bounded detail is sufficient for
                # the operator while preserving deterministic failure.
                raise RuntimeError(
                    "native projection diverged: " + mismatches[0]
                )

    def _assert_projection_operator_consistency(self) -> None:
        """Bind blocker/security projections to their append events."""

        blockers: list[str] = []
        security_events: list[dict[str, str]] = []
        for event in self.events:
            if event.kind == "blocker_recorded" or event.kind == "blocker_resolved":
                raw_payload = event.payload
                if type(raw_payload) is not dict or any(
                    type(key) is not str for key in cast(dict[Any, Any], raw_payload)
                ):
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=blocker_metadata"
                    )
                payload = cast(dict[str, Any], raw_payload)
                if type(payload.get("reason")) is not str or type(payload.get("detail", "")) is not str:
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=blocker_metadata"
                    )
                reason = cast(str, payload["reason"])
                if not reason.strip():
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=blocker_identity"
                    )
                if event.kind == "blocker_recorded":
                    if reason in blockers:
                        raise RuntimeError(
                            f"native projection diverged: event_{event.sequence}=duplicate_blocker"
                        )
                    blockers.append(reason)
                else:
                    if reason not in blockers:
                        raise RuntimeError(
                            f"native projection diverged: event_{event.sequence}=unknown_blocker"
                        )
                    blockers.remove(reason)
            elif event.kind == "security_event_recorded":
                payload = event.payload
                if type(payload) is not dict or any(
                    type(key) is not str for key in cast(dict[Any, Any], payload)
                ):
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=security_metadata"
                    )
                typed_payload = cast(dict[str, Any], payload)
                if (
                    type(typed_payload.get("reason")) is not str
                    or type(typed_payload.get("artifact_hash", "")) is not str
                    or type(typed_payload.get("detail", "")) is not str
                    or not cast(str, typed_payload["reason"]).strip()
                    or (
                        cast(str, typed_payload.get("artifact_hash", ""))
                        and not _is_digest(cast(str, typed_payload["artifact_hash"]))
                    )
                ):
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=security_metadata"
                    )
                security_events.append(
                    {
                        "reason": cast(str, typed_payload["reason"]),
                        "artifact_hash": cast(str, typed_payload.get("artifact_hash", "")),
                        "detail": cast(str, typed_payload.get("detail", "")),
                    }
                )
        if self.blockers != blockers:
            raise RuntimeError("native projection diverged: blockers=different")
        if self.security_events != security_events:
            raise RuntimeError("native projection diverged: security_events=different")

    def _assert_execution_cell_manifest_consistency(self) -> None:
        """Keep the mutable manifest projection bound to one immutable event."""

        manifest_events = [
            event
            for event in self.events
            if event.kind == "execution_cell_manifest_recorded"
        ]
        if len(manifest_events) > 1:
            raise RuntimeError("native projection diverged: execution_cell_manifest=duplicate")
        if not manifest_events:
            if self.execution_cell_manifest:
                raise RuntimeError("native projection diverged: execution_cell_manifest=unbound")
            return
        event_payload = manifest_events[0].payload
        if type(event_payload) is not dict:
            raise RuntimeError("native projection diverged: execution_cell_manifest=invalid")
        payload = cast(dict[str, Any], event_payload)
        raw_manifest: Any = payload.get("manifest")
        if type(raw_manifest) is not list:
            raise RuntimeError("native projection diverged: execution_cell_manifest=invalid")
        raw_manifest = cast(list[Any], raw_manifest)
        if payload.get("schema") != _EXECUTION_CELL_MANIFEST_SCHEMA:
            raise RuntimeError("native projection diverged: execution_cell_manifest=schema")
        if payload.get("cell_count") != len(raw_manifest):
            raise RuntimeError("native projection diverged: execution_cell_manifest=count")
        if payload.get("manifest_hash") != _execution_cell_manifest_hash(raw_manifest):
            raise RuntimeError("native projection diverged: execution_cell_manifest=hash")
        current_manifest = json.loads(
            json.dumps(
                list(self.execution_cell_manifest),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if raw_manifest != current_manifest:
            raise RuntimeError("native projection diverged: execution_cell_manifest=different")

    def _assert_native_projection_consistency(self) -> None:
        """Fail closed when the Python view no longer matches native state.

        The Python facade remains mutable for compatibility, but a mutated
        projection must never be serialized or exported as if it were the
        native reducer.  Older development doubles may not expose
        ``snapshot_json``; those doubles retain their existing compatibility
        behavior and are covered by the event-chain tests instead.
        """

        self._assert_projection_admission_consistency()
        self._assert_projection_record_consistency()
        self._assert_projection_operator_consistency()
        self._assert_execution_cell_manifest_consistency()
        native_controller = self._native_controller
        for event in self.events:
            event_payload = event.payload
            if not isinstance(event_payload, dict):
                if self.trust_policy_hash is not None:
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=trust_policy_mismatch"
                    )
                continue
            raw_event_trust_hash = cast(dict[str, Any], event_payload).get("trust_policy_hash")
            if self.trust_policy_hash is None:
                if raw_event_trust_hash is not None:
                    raise RuntimeError(
                        f"native projection diverged: event_{event.sequence}=unexpected_trust_policy"
                    )
            elif raw_event_trust_hash != self.trust_policy_hash:
                raise RuntimeError(
                    f"native projection diverged: event_{event.sequence}=trust_policy_mismatch"
                )
        snapshot_reader = getattr(native_controller, "snapshot_json", None)
        if native_controller is None or not callable(snapshot_reader):
            return
        try:
            raw_snapshot = snapshot_reader()
            if not isinstance(raw_snapshot, (str, bytes, bytearray)):
                raise TypeError("native snapshot reader must return JSON text")
            snapshot = json.loads(raw_snapshot)
        except Exception as exc:
            raise RuntimeError("native projection snapshot is unavailable") from exc
        if not isinstance(snapshot, dict):
            raise RuntimeError("native projection snapshot is invalid")
        snapshot_map = cast(dict[str, Any], snapshot)
        runtime_raw = snapshot_map.get("runtime", {})
        runtime = cast(dict[str, Any], runtime_raw) if isinstance(runtime_raw, dict) else {}
        mismatches: list[str] = []

        native_events = runtime.get("events")
        if isinstance(native_events, list):
            native_event_list = cast(list[Any], native_events)
            if len(native_event_list) != len(self.events):
                mismatches.append(
                    f"event_count python={len(self.events)} native={len(native_event_list)}"
                )
            else:
                for expected, actual in zip(self.events, native_event_list, strict=True):
                    if not isinstance(actual, dict):
                        mismatches.append(f"event_{expected.sequence}=invalid")
                        continue
                    actual_map = cast(dict[str, Any], actual)
                    expected_kind = _RUST_EVENT_KINDS.get(expected.kind, "ReviewRecorded")
                    expected_wire = {
                        "sequence": expected.sequence,
                        "state_epoch": expected.state_epoch,
                        "kind": expected_kind,
                        "payload_hash": list(bytes.fromhex(expected.payload_hash)),
                        "previous_event_hash": list(bytes.fromhex(expected.previous_event_hash)),
                        "event_hash": list(bytes.fromhex(expected.event_hash)),
                    }
                    if any(actual_map.get(key) != value for key, value in expected_wire.items()):
                        mismatches.append(f"event_{expected.sequence}=different")
                        break

        raw_native_state = runtime.get("state", "")
        if type(raw_native_state) is not str:
            mismatches.append("state=invalid")
        else:
            native_state = raw_native_state.strip().lower()
            if native_state and native_state != self.state:
                mismatches.append(f"state python={self.state} native={native_state}")
        native_epoch = runtime.get("state_epoch")
        if native_epoch is not None and (
            type(native_epoch) is not int or native_epoch != self.state_epoch
        ):
            mismatches.append(
                f"state_epoch python={self.state_epoch} native={native_epoch}"
            )

        def native_ids(field: str) -> set[str] | None:
            value = snapshot_map.get(field)
            if isinstance(value, dict):
                typed_value = cast(dict[Any, Any], value)
                if any(type(key) is not str for key in typed_value):
                    raise RuntimeError(f"native projection diverged: {field}=invalid")
                return set(cast(dict[str, Any], typed_value))
            if isinstance(value, list):
                typed_value = cast(list[Any], value)
                if any(type(item) is not str for item in typed_value):
                    raise RuntimeError(f"native projection diverged: {field}=invalid")
                return set(typed_value)
            return None

        expected_ids = {
            "projection_sources": set(self.sources),
            "projection_claims": set(self.claims),
            "projection_hypotheses": set(self.hypotheses),
            "projection_experiments": set(self.experiments),
            "projection_observations": set(self.observations),
            "projection_skill_admissions": set(self.skill_admissions),
            "projection_tool_execution_admissions": set(self.tool_execution_admissions),
            "projection_tool_executions": set(self.tool_executions),
        }
        for field, expected in expected_ids.items():
            actual = native_ids(field)
            if actual is not None and actual != expected:
                mismatches.append(
                    f"{field} python={sorted(expected)!r} native={sorted(actual)!r}"
                )

        actual_payloads = snapshot_map.get("projection_payloads")
        if isinstance(actual_payloads, dict) and actual_payloads:
            typed_actual_payloads = cast(dict[Any, Any], actual_payloads)
            if any(type(key) is not str for key in typed_actual_payloads):
                raise RuntimeError("native projection diverged: projection_payloads=invalid")
            expected_payloads = {
                str(event.sequence): event.payload
                for event in self.events
                if event.kind not in {"mission_created", "budget_admitted"}
            }
            normalized_payloads = dict(cast(dict[str, Any], typed_actual_payloads))
            if normalized_payloads != expected_payloads:
                mismatches.append("projection_payloads=different")

        if mismatches:
            detail = "; ".join(mismatches[:8])
            if len(mismatches) > 8:
                detail += f"; +{len(mismatches) - 8} more"
            raise RuntimeError(f"native projection diverged: {detail}")

    def dossier(
        self,
        *,
        benchmark: dict[str, Any] | None = None,
        finalize: bool = True,
    ) -> LabDossier:
        """Build a dossier, optionally postponing terminal finalization.

        A pre-synthesis dossier is a read-only planning snapshot.  Keeping
        finalization explicit lets the synthesis gateway call pass through its
        native admission/settlement fence before the run becomes terminal.
        The default remains the historical finalized-dossier behavior.
        """

        if type(finalize) is not bool:
            raise TypeError("dossier finalize flag must be boolean")
        self._assert_native_projection_consistency()
        derived_blockers: list[str] = []
        if not self.sources:
            derived_blockers.append("research_plane_missing_or_empty")
        if not self.hypotheses:
            derived_blockers.append("hypothesis_not_registered")
        if not self.experiments:
            derived_blockers.append("experiment_not_preregistered")
        for experiment in self.experiments.values():
            observed = sum(
                observation.experiment_id == experiment.experiment_id
                for observation in self.observations.values()
            )
            if observed < experiment.expected_observations:
                derived_blockers.append(
                    f"experiment_observation_quota_missing:{experiment.experiment_id}"
                )
            clean_replicates = sum(
                observation.experiment_id == experiment.experiment_id
                and observation.replication_of is not None
                and observation.clean
                for observation in self.observations.values()
            )
            if clean_replicates < experiment.min_clean_replicates:
                derived_blockers.append(
                    f"clean_replication_quota_missing:{experiment.experiment_id}"
                )
            if experiment.uncertainty_required and any(
                observation.experiment_id == experiment.experiment_id
                and observation.uncertainty is None
                for observation in self.observations.values()
            ):
                derived_blockers.append(f"uncertainty_missing:{experiment.experiment_id}")
        if not self.observations or any(not observation.valid for observation in self.observations.values()):
            derived_blockers.append("valid_observation_missing")
        for blocker in derived_blockers:
            self.record_blocker(blocker)
        combined_blockers = list(dict.fromkeys(self.blockers))
        if finalize:
            if combined_blockers and self.state not in {"blocked", "aborted"}:
                self.transition("blocked")
            elif not combined_blockers and self.state != "completed":
                self.begin_finalization()
                if self.state != "reviewing":
                    self.transition("reviewing")
                self.transition("completed")
        manifest_base: dict[str, Any] = {
            "mission_id": self.mission_id,
            "authority_mode": self.authority_mode.value,
            "trust_level": self.trust_level,
            "trust_policy_hash": self.trust_policy_hash,
            "scope": self.scope,
            "non_goals": self.non_goals,
            "state": self.state,
            "state_epoch": self.state_epoch,
            "event_count": len(self.events),
            "event_root_hash": self.events[-1].event_hash if self.events else "0" * 64,
            "source_count": len(self.sources),
            "provenance_cluster_count": len({
                source.provenance_cluster
                for source in self.sources.values()
                if source.provenance_cluster.strip()
            }),
            "claim_count": len(self.claims),
            "hypothesis_count": len(self.hypotheses),
            "experiment_count": len(self.experiments),
            "observation_count": len(self.observations),
            "skill_admission_count": len(self.skill_admissions),
            "tool_execution_count": len(self.tool_executions),
            "max_external_attempts": self.external_attempt_budget,
            "external_attempt_count": self.external_attempt_count,
            "execution_cell_manifest": self.execution_cell_manifest,
            # Keep operator-visible blockers in the exported manifest.  The
            # dossier also carries the typed tuple, but ``RunResult`` exposes
            # the manifest as its stable compatibility surface.
            "blockers": tuple(combined_blockers),
        }
        if benchmark is not None:
            manifest_base["benchmark_hash"] = benchmark.get("raw_trial_hash", "")
            manifest_base["benchmark_status"] = benchmark.get("status", "REJECTED")
        manifest_base["event_chain_authority"] = self._event_chain_authority()
        manifest_base["security_event_count"] = len(self.security_events)
        manifest_base["experiment_statistics"] = self._experiment_statistics()
        if self.replay_archive is not None:
            manifest_base["replay_archive"] = self.replay_archive
        manifest = {**manifest_base, "manifest_hash": _hash(manifest_base)}
        return LabDossier(
            schema="aegis-lab-dossier-v1",
            mission_id=self.mission_id,
            objective=self.objective,
            status=self.state,
            blockers=tuple(combined_blockers),
            sources=tuple(self.sources.values()),
            claims=tuple(self.claims.values()),
            hypotheses=tuple(self.hypotheses.values()),
            experiments=tuple(self.experiments.values()),
            observations=tuple(self.observations.values()),
            events=tuple(self.events),
            manifest=manifest,
            benchmark=benchmark,
            truth_claim=False,
            security_events=tuple(self.security_events),
            scope=self.scope,
            non_goals=self.non_goals,
            skill_admissions=tuple(self.skill_admissions.values()),
        )

    def verify_event_chain(self) -> bool:
        previous = "0" * 64
        previous_epoch = 0
        for index, event in enumerate(self.events, start=1):
            expected = _rust_event_hash(
                event.sequence,
                event.state_epoch,
                event.kind,
                event.payload_hash,
                event.previous_event_hash,
            )
            if event.kind == "mission_created" and self._native_mission_hash is not None:
                expected_payload_hash = self._native_mission_hash
            elif event.kind == "budget_admitted":
                expected_payload_hash = _rust_canonical_hash(_budget_vector_payload(event.payload))
            elif self._native_controller is not None:
                expected_payload_hash = _native_projection_payload_hash(event.payload)
            else:
                expected_payload_hash = _hash(event.payload)
            if (
                event.sequence != index
                or event.previous_event_hash != previous
                or event.state_epoch < previous_epoch
                or event.payload_hash != expected_payload_hash
                or event.event_hash != expected
            ):
                return False
            previous = event.event_hash
            previous_epoch = event.state_epoch
        return bool(self.events)

    def event_cursor(self) -> int:
        """Return the monotonic cursor for an event-stream consumer."""

        return self.events[-1].sequence if self.events else 0

    def events_since(self, cursor: int = 0) -> tuple[LabEvent, ...]:
        """Read evidence deltas after ``cursor`` without exposing model traces."""

        if cursor < 0:
            raise ValueError("event cursor must be non-negative")
        return tuple(event for event in self.events if event.sequence > cursor)

    def _validate_execution_admission_payload(
        self,
        admission_kind: str,
        payload: dict[str, Any],
    ) -> None:
        """Validate recovery inputs before any lossy field access.

        Recovery is an ambiguity boundary: admission payloads may be the only
        durable description left after a crash.  Do not let ``str``/``int``
        coercion turn a malformed prefix into a seemingly valid settlement.
        The trust-policy field is an event-envelope concern and is therefore
        intentionally allowed as an extra key.
        """

        if type(payload) is not dict or any(type(key) is not str for key in payload):
            raise ValueError("execution admission payload metadata is invalid")

        def required_strings(*fields: str) -> None:
            if any(type(payload.get(field)) is not str or not payload[field].strip() for field in fields):
                raise ValueError("execution admission string metadata is invalid")

        def required_digest(*fields: str) -> None:
            required_strings(*fields)
            if any(not _is_digest(cast(str, payload[field])) for field in fields):
                raise ValueError("execution admission digest metadata is invalid")

        def positive_int(*fields: str) -> None:
            if any(
                type(payload.get(field)) is not int or payload[field] < 1
                for field in fields
            ):
                raise ValueError("execution admission integer metadata is invalid")

        def optional_digest(field: str) -> None:
            if field in payload and (
                type(payload[field]) is not str or not _is_digest(cast(str, payload[field]))
            ):
                raise ValueError("execution admission optional digest metadata is invalid")

        def optional_timeout() -> None:
            if "timeout_seconds" not in payload:
                return
            value = payload["timeout_seconds"]
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise ValueError("execution admission deadline metadata is invalid")

        if admission_kind == "tool_execution_admitted":
            required_strings(
                "admission_id",
                "execution_id",
                "tool_name",
                "effect_class",
                "actor_role",
                "expected_observation_schema",
                "stop_rule",
                "mission_id",
                "replay_parent_hash",
                "input_hash",
                "policy_hash",
                "status",
            )
            if payload["status"].upper() != "ADMITTED" or payload["actor_role"] not in {
                "actor",
                "observer",
            }:
                raise ValueError("execution admission status or role metadata is invalid")
            if payload["mission_id"] != self.mission_id:
                raise ValueError("execution admission mission metadata is invalid")
            required_digest("replay_parent_hash", "input_hash", "policy_hash")
            positive_int("lease_id", "attempt")
            optional_digest("idempotency_key")
            optional_timeout()
            return

        if admission_kind == "experiment_execution_admitted":
            required_strings(
                "admission_id",
                "execution_id",
                "experiment_id",
                "mission_id",
                "replay_parent_hash",
                "input_hash",
                "policy_hash",
                "status",
            )
            if payload["status"].upper() != "ADMITTED" or payload["mission_id"] != self.mission_id:
                raise ValueError("experiment execution admission metadata is invalid")
            required_digest("replay_parent_hash", "input_hash", "policy_hash")
            positive_int("attempt")
            optional_digest("idempotency_key")
            optional_timeout()
            return

        if admission_kind == "research_program_admitted":
            required_strings("admission_id", "program_hash", "provider", "input_hash", "policy_hash", "status")
            if payload["status"].upper() != "ADMITTED":
                raise ValueError("research program admission status metadata is invalid")
            required_digest("program_hash", "input_hash", "policy_hash")
            positive_int("operation_count")
            return

        if admission_kind == "browser_action_admitted":
            required_strings(
                "admission_id",
                "action_id",
                "actor_role",
                "action_kind",
                "input_hash",
                "policy_hash",
                "status",
            )
            if (
                payload["status"].upper() != "ADMITTED"
                or payload["actor_role"] != "actor"
                or payload["action_kind"].lower() not in _BROWSER_ACTION_KINDS
            ):
                raise ValueError("browser action admission metadata is invalid")
            required_digest("input_hash", "policy_hash")
            positive_int("lease_id")
            return

        if admission_kind == "browser_observation_admitted":
            required_strings(
                "admission_id",
                "observation_id",
                "observer_role",
                "observation_kind",
                "input_hash",
                "policy_hash",
                "status",
            )
            if (
                payload["status"].upper() != "ADMITTED"
                or payload["observer_role"] != "observer"
                or payload["observation_kind"].lower() not in _BROWSER_OBSERVATION_KINDS
            ):
                raise ValueError("browser observation admission metadata is invalid")
            required_digest("input_hash", "policy_hash")
            positive_int("lease_id", "observation_count")
            return

        if admission_kind == "skill_admission_recorded":
            required_strings(
                "skill_id",
                "version",
                "manifest_hash",
                "mission_id",
                "replay_parent_hash",
                "admission_hash",
            )
            if payload["mission_id"] != self.mission_id:
                raise ValueError("skill admission mission metadata is invalid")
            raw_capabilities = payload.get("granted_capabilities")
            raw_preconditions = payload.get("precondition_results")
            if type(raw_capabilities) not in (list, tuple) or type(raw_preconditions) not in (list, tuple):
                raise ValueError("skill admission collection metadata is invalid")
            capabilities = cast(list[Any] | tuple[Any, ...], raw_capabilities)
            preconditions = cast(list[Any] | tuple[Any, ...], raw_preconditions)
            if any(type(value) is not str or not value.strip() for value in capabilities) or any(
                type(pair) not in (list, tuple)
                or len(pair) != 2
                or type(pair[0]) is not str
                or not pair[0].strip()
                or type(pair[1]) is not bool
                for pair in preconditions
            ):
                raise ValueError("skill admission collection metadata is invalid")
            if type(payload.get("mission_epoch")) is not int or payload["mission_epoch"] < 0:
                raise ValueError("skill admission epoch metadata is invalid")
            required_digest("manifest_hash", "replay_parent_hash", "admission_hash")
            raw_event_hash = payload.get("admission_event_hash", "")
            if type(raw_event_hash) is not str or raw_event_hash:
                raise ValueError("skill admission event binding metadata is invalid")
            try:
                SkillAdmission(
                    skill_id=cast(str, payload["skill_id"]),
                    version=cast(str, payload["version"]),
                    manifest_hash=cast(str, payload["manifest_hash"]),
                    mission_id=cast(str, payload["mission_id"]),
                    mission_epoch=cast(int, payload["mission_epoch"]),
                    granted_capabilities=tuple(cast(str, value) for value in capabilities),
                    precondition_results=tuple(
                        (cast(str, pair[0]), cast(bool, pair[1])) for pair in preconditions
                    ),
                    replay_parent_hash=cast(str, payload["replay_parent_hash"]),
                    admission_hash=cast(str, payload["admission_hash"]),
                    admission_event_hash="",
                ).validate_hash()
            except (SkillAdmissionError, TypeError, ValueError) as exc:
                raise ValueError("skill admission hash metadata is invalid") from exc
            return

        raise ValueError(f"unsupported execution admission lane: {admission_kind}")

    def unsettled_tool_execution_ids(self) -> tuple[str, ...]:
        """Return side-effect admissions that have no durable settlement.

        An ungraceful process stop can occur after admission and before the
        adapter returns.  Recovery must surface that ambiguity explicitly;
        treating an open admission as success would overclaim what happened.
        """

        return tuple(
            sorted(
                execution_id
                for execution_id in self.tool_execution_admissions
                if execution_id not in self.tool_executions
            )
        )

    def unsettled_execution_admissions(
        self,
    ) -> tuple[tuple[str, str, dict[str, Any]], ...]:
        """Return every admitted edge execution that lacks a settlement.

        The event log is authoritative here rather than an in-memory index:
        this method is intentionally usable after restoring a crash prefix.
        The first tuple item is the lane name, the second is its stable
        identity, and the final item is the immutable admission payload.
        """

        admission_specs = {
            "experiment_execution_admitted": ("experiment_execution_recorded", "execution_id"),
            "tool_execution_admitted": ("tool_execution_recorded", "execution_id"),
            "research_program_admitted": ("research_program_executed", "program_hash"),
            "browser_action_admitted": ("browser_action_recorded", "action_id"),
            "browser_observation_admitted": ("browser_observation_recorded", "observation_id"),
            "skill_admission_recorded": ("skill_execution_recorded", "admission_hash"),
        }
        admissions: dict[tuple[str, str], dict[str, Any]] = {}
        settled: set[tuple[str, str]] = set()
        known_event_kinds = {
            kind
            for admission_kind, (record_kind, _identity_key) in admission_specs.items()
            for kind in (admission_kind, record_kind)
        }
        for event in self.events:
            if event.kind not in known_event_kinds:
                continue
            payload = event.payload
            if type(payload) is not dict:
                raise ValueError("execution admission payload is invalid")
            raw_payload = cast(dict[Any, Any], payload)
            if any(type(key) is not str for key in raw_payload):
                raise ValueError("execution admission payload is invalid")
            payload = cast(dict[str, Any], raw_payload)
            for admission_kind, (record_kind, identity_key) in admission_specs.items():
                if event.kind == admission_kind:
                    raw_identity = payload.get(identity_key, "")
                    if type(raw_identity) is not str:
                        raise ValueError("execution admission identity metadata is invalid")
                    identity = raw_identity.strip()
                    if not identity:
                        raise ValueError("execution admission identity metadata is invalid")
                    raw_status = payload.get("status", "ADMITTED")
                    if type(raw_status) is not str:
                        raise ValueError("execution admission status metadata is invalid")
                    if (
                        admission_kind == "skill_admission_recorded"
                        or raw_status.upper() == "ADMITTED"
                    ):
                        self._validate_execution_admission_payload(admission_kind, payload)
                        admissions[(admission_kind, identity)] = dict(payload)
                    else:
                        raise ValueError("execution admission status metadata is invalid")
                    break
                if event.kind == record_kind:
                    raw_identity = payload.get(identity_key, "")
                    if type(raw_identity) is not str:
                        raise ValueError("execution settlement identity metadata is invalid")
                    identity = raw_identity.strip()
                    if identity:
                        settled.add((admission_kind, identity))
                    break
        return tuple(
            (admission_kind, identity, dict(payload))
            for (admission_kind, identity), payload in sorted(admissions.items())
            if (admission_kind, identity) not in settled
        )

    def reconcile_unsettled_executions(
        self,
        *,
        operator_id: str,
        reason: str = "process_interruption",
    ) -> tuple[tuple[str, str], ...]:
        """Close all open execution admissions as explicit non-success.

        A crash can happen after any lane's admission and before its adapter
        returns.  Recovery retains the admission's hashes, emits a typed
        non-success settlement, blocks the run, and never guesses that a
        side effect succeeded.  Recovery must happen before an ``aborted``
        terminal transition because some lane contracts cannot append after
        that transition; this is deliberate fail-closed behavior.
        """

        if type(operator_id) is not str or type(reason) is not str:
            raise ValueError("reconciliation contract is invalid")
        normalized_operator = operator_id.strip()
        normalized_reason = reason.strip()
        if not normalized_operator or not normalized_reason:
            raise ValueError("reconciliation operator and reason are required")
        if self.state == "aborted":
            raise ValueError("reconcile unsettled executions before aborting the run")
        unsettled = self.unsettled_execution_admissions()
        if not unsettled:
            return ()
        if self.state == "completed":
            self.transition("blocked")
        operator_hash = _hash(normalized_operator)
        recovered: list[tuple[str, str]] = []
        recovery_result = {
            "schema": "aegis-execution-recovery-result-v1",
            "status": "UNKNOWN_SIDE_EFFECT",
            "reason": normalized_reason,
            "operator_hash": operator_hash,
        }
        for admission_kind, identity, admission in unsettled:
            try:
                if admission_kind == "tool_execution_admitted":
                    self.record_tool_execution(
                        tool_name=cast(str, admission["tool_name"]),
                        execution_id=identity,
                        admission_id=cast(str, admission["admission_id"]),
                        input_payload={},
                        policy_payload={},
                        result=recovery_result,
                        effect_class=cast(str, admission["effect_class"]),
                        actor_role=cast(str, admission["actor_role"]),
                        expected_observation_schema=cast(
                            str, admission["expected_observation_schema"]
                        ),
                        stop_rule=cast(str, admission["stop_rule"]),
                        lease_id=cast(int, admission["lease_id"]),
                        attempt=cast(int, admission["attempt"]),
                        status="REJECTED",
                        input_hash=cast(str, admission["input_hash"]),
                        policy_hash=cast(str, admission["policy_hash"]),
                        idempotency_key=cast(str | None, admission.get("idempotency_key")),
                        timeout_seconds=cast(float | None, admission.get("timeout_seconds")),
                    )
                elif admission_kind == "experiment_execution_admitted":
                    self.record_experiment_execution(
                        experiment_id=cast(str, admission["experiment_id"]),
                        attempt=cast(int, admission["attempt"]),
                        execution_id=identity,
                        admission_id=cast(str, admission["admission_id"]),
                        input_payload={},
                        policy_payload={},
                        result=recovery_result,
                        observation_count=0,
                        status="REJECTED",
                        input_hash=cast(str, admission["input_hash"]),
                        policy_hash=cast(str, admission["policy_hash"]),
                        idempotency_key=cast(str | None, admission.get("idempotency_key")),
                        timeout_seconds=cast(float | None, admission.get("timeout_seconds")),
                    )
                elif admission_kind == "research_program_admitted":
                    self.record_research_program(
                        program_hash=cast(str, admission["program_hash"]),
                        operation_count=cast(int, admission["operation_count"]),
                        candidate_count=0,
                        provider=cast(str, admission["provider"]),
                        admission_id=cast(str, admission["admission_id"]),
                        status="REJECTED",
                        input_hash=cast(str, admission["input_hash"]),
                        policy_hash=cast(str, admission["policy_hash"]),
                    )
                elif admission_kind == "browser_action_admitted":
                    self.record_browser_action(
                        action_id=identity,
                        admission_id=cast(str, admission["admission_id"]),
                        action_kind=cast(str, admission["action_kind"]),
                        action={},
                        result=recovery_result,
                        policy=BrowserCellPolicy(),
                        lease_id=cast(int, admission["lease_id"]),
                        status="REJECTED",
                        input_hash=cast(str, admission["input_hash"]),
                        policy_hash=cast(str, admission["policy_hash"]),
                    )
                elif admission_kind == "browser_observation_admitted":
                    self.record_browser_observation(
                        observation_kind=cast(str, admission["observation_kind"]),
                        action={},
                        result=recovery_result,
                        policy=BrowserCellPolicy(),
                        lease_id=cast(int, admission["lease_id"]),
                        observation_count=cast(int, admission["observation_count"]),
                        admission_id=cast(str, admission["admission_id"]),
                        observation_id=identity,
                        status="REJECTED",
                        input_hash=cast(str, admission["input_hash"]),
                        policy_hash=cast(str, admission["policy_hash"]),
                    )
                elif admission_kind == "skill_admission_recorded":
                    skill_admission = self.skill_admissions.get(identity)
                    if skill_admission is None:
                        raise ValueError("skill admission is absent from the restored projection")
                    input_hash = _hash(
                        {
                            "schema": "aegis-skill-recovery-input-v1",
                            "admission_hash": identity,
                        }
                    )
                    result_hash = _hash(recovery_result)
                    artifact_hash = _hash(
                        {
                            "schema": "aegis-skill-artifact-v1",
                            "skill_id": skill_admission.skill_id,
                            "version": skill_admission.version,
                            "result_hash": result_hash,
                        }
                    )
                    replay_parent_hash = self.events[-1].event_hash if self.events else "0" * 64
                    execution_without_hash = {
                        "schema": "aegis-skill-execution-v1",
                        "skill_id": skill_admission.skill_id,
                        "version": skill_admission.version,
                        "admission_hash": identity,
                        "mission_id": self.mission_id,
                        "replay_parent_hash": replay_parent_hash,
                        "input_hash": input_hash,
                        "result_hash": result_hash,
                        "artifact_hash": artifact_hash,
                        "validator_version": "recovery-unverified",
                        "status": "REJECTED",
                    }
                    self.record_skill_execution(
                        SkillExecutionReceipt(
                            skill_id=skill_admission.skill_id,
                            version=skill_admission.version,
                            admission_hash=identity,
                            mission_id=self.mission_id,
                            replay_parent_hash=replay_parent_hash,
                            input_hash=input_hash,
                            result_hash=result_hash,
                            artifact_hash=artifact_hash,
                            validator_version="recovery-unverified",
                            status="REJECTED",
                            execution_hash=_hash(execution_without_hash),
                        )
                    )
                else:
                    raise ValueError(f"unsupported unsettled execution lane: {admission_kind}")
            except (KeyError, TypeError, ValueError, RuntimeError, SkillAdmissionError) as exc:
                self.record_blocker(
                    f"execution_reconciliation_failed:{admission_kind}:{type(exc).__name__}",
                    detail=f"identity={identity};operator={operator_hash}",
                )
                continue
            recovered.append((admission_kind, identity))
        if self.state not in {"blocked", "aborted"}:
            self.transition("blocked")
        if self.state != "aborted":
            self.record_blocker(
                "unsettled_execution_requires_reconciliation",
                detail=f"operator={operator_hash};reason={normalized_reason}",
            )
        return tuple(recovered)

    def reconcile_unsettled_tool_executions(
        self,
        *,
        operator_id: str,
        reason: str = "process_interruption",
    ) -> tuple[str, ...]:
        """Close ambiguous admissions as non-success and block the run.

        The adapter cannot prove whether an external side effect happened after
        a crash.  This operation therefore records ``REJECTED`` (or
        ``CANCELLED`` for an already-aborted run), hashes the operator identity,
        and moves the dossier to an explicit blocked state.  It never promotes
        an unknown effect to a successful result.
        """

        if type(operator_id) is not str or type(reason) is not str:
            raise ValueError("reconciliation contract is invalid")
        normalized_operator = operator_id.strip()
        normalized_reason = reason.strip()
        if not normalized_operator or not normalized_reason:
            raise ValueError("reconciliation operator and reason are required")
        unsettled = self.unsettled_tool_execution_ids()
        if not unsettled:
            return ()
        if self.state == "completed":
            self.transition("blocked")
        for execution_id in unsettled:
            admission = self.tool_execution_admissions[execution_id]
            self._validate_execution_admission_payload("tool_execution_admitted", admission)
            result = {
                "schema": "aegis-tool-recovery-result-v1",
                "status": "UNKNOWN_SIDE_EFFECT",
                "reason": normalized_reason,
                "operator_hash": _hash(normalized_operator),
            }
            self.record_tool_execution(
                tool_name=cast(str, admission["tool_name"]),
                execution_id=execution_id,
                admission_id=cast(str, admission["admission_id"]),
                input_payload={},
                policy_payload={},
                result=result,
                effect_class=cast(str, admission["effect_class"]),
                actor_role=cast(str, admission["actor_role"]),
                expected_observation_schema=cast(
                    str, admission["expected_observation_schema"]
                ),
                stop_rule=cast(str, admission["stop_rule"]),
                lease_id=cast(int, admission["lease_id"]),
                attempt=cast(int, admission["attempt"]),
                status="CANCELLED" if self.state == "aborted" else "REJECTED",
                input_hash=cast(str, admission["input_hash"]),
                policy_hash=cast(str, admission["policy_hash"]),
                idempotency_key=cast(str | None, admission.get("idempotency_key")),
                timeout_seconds=cast(float | None, admission.get("timeout_seconds")),
            )
        if self.state not in {"blocked", "aborted"}:
            self.transition("blocked")
        if self.state != "aborted":
            self.record_blocker(
                "unsettled_tool_execution_requires_reconciliation",
                detail=f"operator={_hash(normalized_operator)};reason={normalized_reason}",
            )
        return unsettled

    def _experiment_statistics(self) -> dict[str, dict[str, Any]]:
        statistics: dict[str, dict[str, Any]] = {}
        for experiment in self.experiments.values():
            observations = [
                observation
                for observation in self.observations.values()
                if observation.experiment_id == experiment.experiment_id and observation.valid
            ]
            if not observations:
                continue
            values = [
                DEFAULT_UNIT_REGISTRY.convert(
                    observation.measurement,
                    observation.unit,
                    experiment.measurement_unit,
                )
                for observation in observations
            ]
            mean = sum(values) / len(values)
            if len(values) > 1:
                variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
                standard_error = math.sqrt(variance / len(values))
                margin = 1.96 * standard_error
                ci95_low: float | None = mean - margin
                ci95_high: float | None = mean + margin
                interval_status = "NORMAL_APPROXIMATION"
            else:
                # A single observation has no empirical sampling variance. Do
                # not emit a degenerate interval that looks like confidence.
                ci95_low = None
                ci95_high = None
                interval_status = "INSUFFICIENT_REPLICATION"
            reported_uncertainties = [
                observation.uncertainty
                for observation in observations
                if observation.uncertainty is not None
            ]
            statistics[experiment.experiment_id] = {
                "unit": experiment.measurement_unit,
                "dimension": DEFAULT_UNIT_REGISTRY.dimension_name(experiment.measurement_unit),
                "n": len(values),
                "mean": mean,
                "ci95_low": ci95_low,
                "ci95_high": ci95_high,
                "interval_status": interval_status,
                "reported_uncertainty_count": len(reported_uncertainties),
                "clean_replication_count": sum(
                    observation.replication_of is not None and observation.clean
                    for observation in observations
                ),
                "epistemic_status_counts": {
                    status: sum(
                        observation.epistemic_status.upper() == status
                        for observation in observations
                    )
                    for status in sorted(_EPISTEMIC_STATUSES)
                    if any(observation.epistemic_status.upper() == status for observation in observations)
                },
            }
        return statistics

    def to_payload(self) -> dict[str, Any]:
        """Return a restart-safe, JSON-serializable reducer snapshot."""

        self._assert_native_projection_consistency()

        return {
            "schema": "aegis-lab-run-v1",
            "mission_id": self.mission_id,
            "objective": self.objective,
            "scope": self.scope,
            "non_goals": self.non_goals,
            "require_native_authority": self.require_native_authority,
            "authority_mode": self.authority_mode.value,
            "trust_level": self.trust_level,
            "trust_policy_hash": self.trust_policy_hash,
            "max_steps": self.max_steps,
            "max_external_attempts": self.external_attempt_budget,
            "external_attempt_count": self.external_attempt_count,
            "token_budget": self.token_budget,
            "finalization_reserve": self.finalization_reserve,
            "recovery_reserve": self.recovery_reserve,
            "state": self.state,
            "state_epoch": self.state_epoch,
            "sources": [asdict(item) for item in self.sources.values()],
            "claims": [asdict(item) for item in self.claims.values()],
            "hypotheses": [asdict(item) for item in self.hypotheses.values()],
            "experiments": [asdict(item) for item in self.experiments.values()],
            "observations": [asdict(item) for item in self.observations.values()],
            "skill_admissions": [asdict(item) for item in self.skill_admissions.values()],
            "tool_execution_admissions": list(self.tool_execution_admissions.values()),
            "tool_executions": sorted(self.tool_executions),
            "events": [asdict(item) for item in self.events],
            "blockers": list(dict.fromkeys(self.blockers)),
            "security_events": list(self.security_events),
            "replay_archive": self.replay_archive,
            "execution_cell_manifest": self.execution_cell_manifest,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LabRun:
        """Restore a run only when its snapshot and event chain are valid."""

        if type(payload) is not dict or payload.get("schema") != "aegis-lab-run-v1":
            raise ValueError("invalid lab snapshot schema")
        objective = payload.get("objective", "")
        mission_id = payload.get("mission_id", "")
        state = payload.get("state", "")
        if (
            type(objective) is not str
            or type(mission_id) is not str
            or type(state) is not str
            or not objective.strip()
            or not mission_id.strip()
            or state not in {
                "planned",
                "researching",
                "experimenting",
                "reviewing",
                "completed",
                "blocked",
                "aborted",
            }
        ):
            raise ValueError("invalid lab snapshot identity or state")
        restored = cls.__new__(cls)
        restored.mission_id = mission_id
        restored.objective = objective
        raw_scope = payload.get("scope", ())
        raw_non_goals = payload.get("non_goals", ())
        if not isinstance(raw_scope, (list, tuple)) or not isinstance(raw_non_goals, (list, tuple)):
            raise ValueError("invalid lab snapshot scope or non-goals")
        typed_scope = cast(list[Any] | tuple[Any, ...], raw_scope)
        typed_non_goals = cast(list[Any] | tuple[Any, ...], raw_non_goals)
        if any(type(item) is not str for item in (*typed_scope, *typed_non_goals)):
            raise ValueError("invalid lab snapshot scope or non-goals")
        restored.scope = tuple(typed_scope)
        restored.non_goals = tuple(typed_non_goals)
        if any(not item.strip() for item in (*restored.scope, *restored.non_goals)):
            raise ValueError("invalid lab snapshot scope or non-goals")
        raw_require_native = payload.get("require_native_authority", False)
        if not isinstance(raw_require_native, bool):
            raise ValueError("invalid native-authority flag in lab snapshot")
        raw_authority_mode = payload.get("authority_mode")
        if raw_authority_mode is None:
            restored.authority_mode = (
                AuthorityMode.NATIVE_REQUIRED
                if raw_require_native
                else AuthorityMode.PROJECTION_ONLY
            )
        else:
            restored.authority_mode = _normalize_authority_mode(
                cast(AuthorityMode | str, raw_authority_mode)
            )
            if raw_require_native and restored.authority_mode is not AuthorityMode.NATIVE_REQUIRED:
                raise ValueError("legacy native-authority flag conflicts with Lab authority mode")
        restored.require_native_authority = restored.authority_mode is AuthorityMode.NATIVE_REQUIRED
        raw_trust_level = payload.get("trust_level", "DEV")
        if type(raw_trust_level) is not str:
            raise ValueError("invalid lab trust level in snapshot")
        restored.trust_level = _normalize_lab_trust_level(raw_trust_level)
        raw_trust_policy_hash = payload.get("trust_policy_hash")
        if raw_trust_policy_hash is not None and (
            not isinstance(raw_trust_policy_hash, str)
            or raw_trust_policy_hash != _trust_policy_hash(restored.trust_level)
        ):
            raise ValueError("invalid lab trust policy binding in snapshot")
        restored.trust_policy_hash = raw_trust_policy_hash
        raw_max_steps = payload.get("max_steps", 0)
        if type(raw_max_steps) is not int:
            raise ValueError("invalid lab snapshot max_steps")
        restored.max_steps = raw_max_steps
        if restored.max_steps < 1:
            raise ValueError("invalid lab snapshot max_steps")
        raw_external_attempt_budget = payload.get(
            "max_external_attempts",
            _default_external_attempt_budget(restored.max_steps),
        )
        if (
            type(raw_external_attempt_budget) is not int
            or raw_external_attempt_budget < 1
        ):
            raise ValueError("invalid lab snapshot external attempt budget")
        restored.external_attempt_budget = raw_external_attempt_budget
        has_external_attempt_count = "external_attempt_count" in payload
        raw_external_attempt_count = payload.get("external_attempt_count", 0)
        if (
            type(raw_external_attempt_count) is not int
            or raw_external_attempt_count < 0
            or raw_external_attempt_count > restored.external_attempt_budget
        ):
            raise ValueError("invalid lab snapshot external attempt count")
        restored.external_attempt_count = raw_external_attempt_count
        raw_token_budget = payload.get("token_budget", max(1, restored.max_steps * 1000))
        if type(raw_token_budget) is not int:
            raise ValueError("invalid lab snapshot token budget")
        restored.token_budget = raw_token_budget
        default_finalization = min(
            max(1, restored.token_budget // 5), max(0, restored.token_budget - 1)
        )
        raw_finalization = payload.get("finalization_reserve", default_finalization)
        if type(raw_finalization) is not int:
            raise ValueError("invalid lab snapshot finalization reserve")
        restored.finalization_reserve = raw_finalization
        default_recovery = min(
            max(0, restored.token_budget // 10),
            max(0, restored.token_budget - restored.finalization_reserve - 1),
        )
        raw_recovery = payload.get("recovery_reserve", default_recovery)
        if type(raw_recovery) is not int:
            raise ValueError("invalid lab snapshot recovery reserve")
        restored.recovery_reserve = raw_recovery
        if (
            restored.token_budget < 1
            or restored.finalization_reserve < 0
            or restored.recovery_reserve < 0
            or restored.finalization_reserve + restored.recovery_reserve >= restored.token_budget
        ):
            raise ValueError("invalid lab snapshot budget")
        restored.state = state
        raw_state_epoch = payload.get("state_epoch", 0)
        if type(raw_state_epoch) is not int or raw_state_epoch < 0:
            raise ValueError("invalid lab snapshot state epoch")
        restored.state_epoch = raw_state_epoch
        restored._native_controller = None
        restored._native_mission = None
        restored._native_mission_hash = None
        restored._native_finalization_started = False
        restored._initialize_native_controller(objective)
        def snapshot_records(name: str) -> list[dict[str, Any]]:
            raw_value = payload.get(name, ())
            if type(raw_value) not in (list, tuple):
                raise ValueError(f"invalid {name} in lab snapshot")
            typed_value = cast(list[Any] | tuple[Any, ...], raw_value)
            if any(type(item) is not dict for item in typed_value):
                raise ValueError(f"invalid {name} in lab snapshot")
            return [cast(dict[str, Any], item) for item in typed_value]

        raw_sources = snapshot_records("sources")
        raw_claims = snapshot_records("claims")
        raw_hypotheses = snapshot_records("hypotheses")
        raw_experiments = snapshot_records("experiments")
        raw_observations = snapshot_records("observations")
        raw_skill_admissions = snapshot_records("skill_admissions")
        raw_tool_admissions = snapshot_records("tool_execution_admissions")
        raw_tool_executions_value = payload.get("tool_executions", ())
        if type(raw_tool_executions_value) not in (list, tuple):
            raise ValueError("invalid tool executions in lab snapshot")
        raw_tool_executions = list(cast(list[Any] | tuple[Any, ...], raw_tool_executions_value))

        def require_unique_ids(items: list[Any], key: str) -> None:
            values: list[str] = []
            for item in items:
                if type(item) is not dict or key not in item:
                    raise ValueError(f"invalid {key} in lab snapshot")
                item_map = cast(dict[str, Any], item)
                value = item_map[key]
                if type(value) is not str or not value.strip():
                    raise ValueError(f"invalid {key} in lab snapshot")
                values.append(value)
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {key} in lab snapshot")

        require_unique_ids(raw_sources, "source_id")
        require_unique_ids(raw_claims, "claim_id")
        require_unique_ids(raw_hypotheses, "hypothesis_id")
        require_unique_ids(raw_experiments, "experiment_id")
        require_unique_ids(raw_observations, "observation_id")
        require_unique_ids(raw_skill_admissions, "admission_hash")
        require_unique_ids(raw_tool_admissions, "execution_id")
        if any(type(item) is not str or not item.strip() for item in raw_tool_executions):
            raise ValueError("invalid tool execution identity in lab snapshot")
        if len(raw_tool_executions) != len(set(raw_tool_executions)):
            raise ValueError("duplicate tool execution identity in lab snapshot")

        raw_events = snapshot_records("events")
        for item in raw_events:
            if (
                type(item.get("sequence")) is not int
                or type(item.get("state_epoch")) is not int
                or item.get("sequence", 0) < 1
                or item.get("state_epoch", -1) < 0
                or type(item.get("kind")) is not str
                or not item["kind"].strip()
                or type(item.get("payload_hash")) is not str
                or type(item.get("previous_event_hash")) is not str
                or type(item.get("event_hash")) is not str
                or not _is_digest(item["payload_hash"])
                or not _is_digest(item["previous_event_hash"])
                or not _is_digest(item["event_hash"])
                or type(item.get("payload")) is not dict
            ):
                raise ValueError("invalid event metadata in lab snapshot")

        def validate_tool_admission(item: dict[str, Any]) -> None:
            required_strings = (
                "admission_id",
                "execution_id",
                "tool_name",
                "effect_class",
                "actor_role",
                "expected_observation_schema",
                "stop_rule",
                "mission_id",
                "replay_parent_hash",
                "input_hash",
                "policy_hash",
                "status",
            )
            if any(
                type(item.get(field)) is not str or not item[field].strip()
                for field in required_strings
            ):
                raise ValueError("invalid tool execution admission metadata in lab snapshot")
            if (
                item["status"] != "ADMITTED"
                or item["actor_role"] not in {"actor", "observer"}
                or item["mission_id"] != restored.mission_id
                or not _is_digest(item["replay_parent_hash"])
                or not _is_digest(item["input_hash"])
                or not _is_digest(item["policy_hash"])
                or type(item.get("attempt")) is not int
                or item["attempt"] < 1
                or type(item.get("lease_id")) is not int
                or item["lease_id"] < 1
            ):
                raise ValueError("invalid tool execution admission metadata in lab snapshot")
            if "idempotency_key" in item and (
                type(item["idempotency_key"]) is not str
                or not _is_digest(item["idempotency_key"])
            ):
                raise ValueError("invalid tool execution idempotency metadata in lab snapshot")
            if "timeout_seconds" in item and (
                type(item["timeout_seconds"]) not in (int, float)
                or isinstance(item["timeout_seconds"], bool)
                or not math.isfinite(float(item["timeout_seconds"]))
                or float(item["timeout_seconds"]) <= 0
            ):
                raise ValueError("invalid tool execution deadline metadata in lab snapshot")

        for item in raw_tool_admissions:
            validate_tool_admission(item)

        def snapshot_string_sequence(item: dict[str, Any], key: str) -> tuple[str, ...]:
            raw_value = item.get(key, ())
            if type(raw_value) not in (list, tuple):
                raise ValueError(f"invalid {key} in lab snapshot")
            typed_value = cast(list[Any] | tuple[Any, ...], raw_value)
            if any(type(value) is not str for value in typed_value):
                raise ValueError(f"invalid {key} in lab snapshot")
            return tuple(typed_value)

        for item in raw_skill_admissions:
            snapshot_string_sequence(item, "granted_capabilities")
            raw_pairs = item.get("precondition_results")
            if type(raw_pairs) not in (list, tuple):
                raise ValueError("invalid skill precondition metadata in lab snapshot")
            typed_pairs = cast(list[Any] | tuple[Any, ...], raw_pairs)
            if any(
                type(pair) not in (list, tuple)
                or len(pair) != 2
                or type(pair[0]) is not str
                or not pair[0].strip()
                or type(pair[1]) is not bool
                for pair in typed_pairs
            ):
                raise ValueError("invalid skill precondition metadata in lab snapshot")

        restored.sources = {
            record.source_id: record
            for record in (
                SourceRecord(**item)
                for item in raw_sources
            )
        }
        restored.claims = {
            record.claim_id: record
            for record in (
                ClaimRecord(
                    claim_id=item["claim_id"],
                    statement=item["statement"],
                    source_ids=snapshot_string_sequence(item, "source_ids"),
                    confidence_bps=item["confidence_bps"],
                    status=item.get("status", "unresolved"),
                )
                for item in raw_claims
            )
        }
        restored.hypotheses = {
            record.hypothesis_id: record
            for record in (
                HypothesisRecord(
                    hypothesis_id=item["hypothesis_id"],
                    statement=item["statement"],
                    prior_bps=item["prior_bps"],
                    falsifiers=snapshot_string_sequence(item, "falsifiers"),
                    supporting_claim_ids=snapshot_string_sequence(item, "supporting_claim_ids"),
                    contradicting_claim_ids=snapshot_string_sequence(item, "contradicting_claim_ids"),
                )
                for item in raw_hypotheses
            )
        }
        for item in raw_experiments:
            for field in ("variables", "controls", "preregistered_seeds"):
                raw_values = item.get(field)
                if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, (list, tuple)):
                    raise ValueError(f"invalid experiment {field} in lab snapshot")
        restored.experiments = {
            record.experiment_id: record
            for record in (
                ExperimentSpec(
                    experiment_id=item["experiment_id"],
                    hypothesis_id=item["hypothesis_id"],
                    design=item["design"],
                    variables=tuple(item["variables"]),
                    controls=tuple(item["controls"]),
                    preregistered_seeds=tuple(item["preregistered_seeds"]),
                    expected_observations=item["expected_observations"],
                    measurement_unit=item.get("measurement_unit", "score"),
                    uncertainty_required=item.get("uncertainty_required", False),
                    min_clean_replicates=item.get("min_clean_replicates", 0),
                )
                for item in raw_experiments
            )
        }
        restored.observations = {
            record.observation_id: record
            for record in (
                ObservationRecord(
                    observation_id=item["observation_id"],
                    experiment_id=item["experiment_id"],
                    seed=item["seed"],
                    measurement=item["measurement"],
                    unit=item["unit"],
                    raw_artifact_hash=item["raw_artifact_hash"],
                    environment_hash=item["environment_hash"],
                    valid=item.get("valid", True),
                    uncertainty=(
                        item["uncertainty"]
                        if item.get("uncertainty") is not None
                        else None
                    ),
                    replication_of=(
                        item["replication_of"]
                        if item.get("replication_of") is not None
                        else None
                    ),
                    operator_id=item.get("operator_id", ""),
                    clean=item.get("clean", True),
                    epistemic_status=item.get("epistemic_status", "OBSERVED"),
                    reported_unit=item.get("reported_unit", ""),
                )
                for item in raw_observations
            )
        }
        restored.skill_admissions = {
            admission.admission_hash: admission
            for admission in (
                SkillAdmission(
                    skill_id=item["skill_id"],
                    version=item["version"],
                    manifest_hash=item["manifest_hash"],
                    mission_id=item["mission_id"],
                    mission_epoch=item["mission_epoch"],
                    granted_capabilities=snapshot_string_sequence(item, "granted_capabilities"),
                    precondition_results=tuple(
                        (pair[0], pair[1])
                        for pair in cast(list[Any] | tuple[Any, ...], item["precondition_results"])
                    ),
                    replay_parent_hash=item["replay_parent_hash"],
                    admission_hash=item["admission_hash"],
                    admission_event_hash=item.get("admission_event_hash", ""),
                )
                for item in raw_skill_admissions
            )
        }
        restored.tool_execution_admissions = {
            item["execution_id"]: dict(item) for item in raw_tool_admissions
        }
        restored.tool_executions = set(raw_tool_executions)
        restored.events = [
            LabEvent(
                sequence=item["sequence"],
                state_epoch=item["state_epoch"],
                kind=item["kind"],
                payload_hash=item["payload_hash"],
                previous_event_hash=item["previous_event_hash"],
                event_hash=item["event_hash"],
                payload=item.get("payload"),
            )
            for item in raw_events
        ]
        observed_external_attempt_count = sum(
            event.kind in _EXTERNAL_ADMISSION_EVENT_KINDS for event in restored.events
        )
        if observed_external_attempt_count != restored.external_attempt_count:
            if has_external_attempt_count:
                raise ValueError("lab snapshot external attempt count is not bound to the event log")
            # Pre-budget snapshots did not persist this derived counter.  Read
            # them by reconstructing it from the authoritative event log; new
            # snapshots always carry the explicit count and are strict.
            if observed_external_attempt_count > restored.external_attempt_budget:
                raise ValueError("legacy lab snapshot exceeds external attempt budget")
            restored.external_attempt_count = observed_external_attempt_count
        restored._native_finalization_started = any(
            event.kind == "finalization_started" for event in restored.events
        )
        raw_blockers = payload.get("blockers", ())
        if type(raw_blockers) not in (list, tuple) or any(
            type(item) is not str or not item.strip() for item in raw_blockers
        ):
            raise ValueError("invalid blockers in lab snapshot")
        restored.blockers = list(cast(list[str] | tuple[str, ...], raw_blockers))
        restored._event_listeners = []
        raw_security_events = payload.get("security_events", ())
        if not isinstance(raw_security_events, (list, tuple)):
            raise ValueError("invalid security events in lab snapshot")
        restored.security_events = []
        raw_security_events_any = cast(list[Any] | tuple[Any, ...], raw_security_events)
        if any(not isinstance(item, dict) for item in raw_security_events_any):
            raise ValueError("invalid security events in lab snapshot")
        typed_security_events = cast(
            list[dict[str, Any]] | tuple[dict[str, Any], ...],
            raw_security_events_any,
        )
        for item in typed_security_events:
            if (
                type(item.get("reason")) is not str
                or type(item.get("artifact_hash", "")) is not str
                or type(item.get("detail", "")) is not str
                or not item["reason"].strip()
            ):
                raise ValueError("invalid security event in lab snapshot")
            restored.security_events.append(
                {
                    "reason": item["reason"],
                    "artifact_hash": item.get("artifact_hash", ""),
                    "detail": item.get("detail", ""),
                }
            )
        raw_archive = payload.get("replay_archive")
        if raw_archive is None:
            restored.replay_archive = None
        elif type(raw_archive) is not dict:
            raise ValueError("invalid replay archive manifest in lab snapshot")
        else:
            archive_map = cast(dict[str, Any], raw_archive)
            _validate_replay_archive_manifest_metadata(
                archive_map,
                require_current=False,
            )
            raw_manifest_hash = archive_map.get("manifest_hash")
            valid_hex_manifest = type(raw_manifest_hash) is str and _is_digest(raw_manifest_hash)
            if type(raw_manifest_hash) is list:
                typed_manifest_hash = cast(list[Any], raw_manifest_hash)
                valid_byte_manifest = len(typed_manifest_hash) == 32 and all(
                    type(value) is int and 0 <= value <= 255
                    for value in typed_manifest_hash
                )
            else:
                valid_byte_manifest = False
            if not (valid_hex_manifest or valid_byte_manifest):
                raise ValueError("invalid replay archive manifest hash in lab snapshot")
            raw_run_id = archive_map.get("run_id")
            try:
                expected_run_id = int(restored.mission_id, 16)
            except ValueError as exc:
                raise ValueError("invalid replay archive mission identity in lab snapshot") from exc
            if type(raw_run_id) is not int or raw_run_id != expected_run_id:
                raise ValueError("invalid replay archive mission identity in lab snapshot")
            if type(archive_map.get("snapshot_path")) is not str or not archive_map["snapshot_path"].strip():
                raise ValueError("invalid replay archive snapshot path in lab snapshot")
            if "snapshot_hash" in archive_map and (
                type(archive_map["snapshot_hash"]) is not str
                or not _is_digest(archive_map["snapshot_hash"])
            ):
                raise ValueError("invalid replay archive snapshot hash in lab snapshot")
            restored.replay_archive = dict(archive_map)
        raw_cells = payload.get("execution_cell_manifest", ())
        if not isinstance(raw_cells, (list, tuple)):
            raise ValueError("invalid execution cell manifest in lab snapshot")
        typed_cells = cast(list[Any] | tuple[Any, ...], raw_cells)
        if any(type(item) is not dict for item in typed_cells):
            raise ValueError("invalid execution cell manifest entry")
        normalized_cells: list[dict[str, Any]] = []
        seen_cell_actions: set[tuple[str, tuple[str, ...]]] = set()
        for raw_cell in typed_cells:
            cell = cast(dict[str, Any], raw_cell)
            raw_cell_id = cell.get("cell_id")
            if (
                type(raw_cell_id) is not str
                or not raw_cell_id.strip()
                or raw_cell_id != raw_cell_id.strip()
            ):
                raise ValueError("invalid execution cell id in lab snapshot")
            raw_action_kinds = cell.get("action_kinds")
            raw_capabilities = cell.get("capabilities")
            raw_effects = cell.get("effect_classes")
            raw_trust_levels = cell.get("trust_levels")
            if any(
                type(value) not in (list, tuple)
                for value in (raw_action_kinds, raw_capabilities, raw_effects, raw_trust_levels)
            ):
                raise ValueError("invalid execution cell policy sequence in lab snapshot")
            action_kinds = tuple(cast(list[Any] | tuple[Any, ...], raw_action_kinds))
            capabilities = tuple(cast(list[Any] | tuple[Any, ...], raw_capabilities))
            effects = tuple(cast(list[Any] | tuple[Any, ...], raw_effects))
            trust_levels = tuple(cast(list[Any] | tuple[Any, ...], raw_trust_levels))
            if (
                not action_kinds
                or any(
                    type(kind) is not str
                    or not kind.strip()
                    or kind != kind.strip().lower()
                    for kind in action_kinds
                )
                or any(kind.strip().lower() not in _EXECUTION_CELL_ACTION_KINDS for kind in action_kinds)
                or len({kind.strip().lower() for kind in action_kinds}) != len(action_kinds)
                or any(
                    type(capability) is not str
                    or not capability.strip()
                    or capability != capability.strip()
                    for capability in capabilities
                )
                or any(
                    type(effect) is not str
                    or not effect.strip()
                    or effect != effect.strip()
                    for effect in effects
                )
                or not trust_levels
                or any(type(level) is not str for level in trust_levels)
                or any(level != level.strip().upper() for level in trust_levels)
                or any(level.strip().upper() not in {"DEV", "STAGING", "PROD"} for level in trust_levels)
            ):
                raise ValueError("invalid execution cell policy metadata in lab snapshot")
            normalized_actions = tuple(kind.strip().lower() for kind in action_kinds)
            cell_identity = (raw_cell_id, normalized_actions)
            if cell_identity in seen_cell_actions:
                raise ValueError("duplicate execution cell manifest entry in lab snapshot")
            seen_cell_actions.add(cell_identity)
            raw_cell_policy_hash = cell.get("trust_policy_hash")
            if raw_cell_policy_hash is not None and (
                type(raw_cell_policy_hash) is not str or not _is_digest(raw_cell_policy_hash)
            ):
                raise ValueError("invalid execution cell trust policy hash in lab snapshot")
            normalized_cells.append(dict(cell))
        restored.execution_cell_manifest = tuple(normalized_cells)
        if restored.trust_policy_hash is not None and any(
            item.get("trust_policy_hash") != restored.trust_policy_hash
            for item in restored.execution_cell_manifest
        ):
            raise ValueError("invalid execution cell trust policy binding in snapshot")
        if not restored.verify_event_chain():
            raise ValueError("lab snapshot event chain is invalid")
        if not restored.events or restored.state_epoch != restored.events[-1].state_epoch:
            raise ValueError("lab snapshot epoch is not bound to the event chain")
        first_event = restored.events[0]
        first_payload = cast(dict[str, Any], first_event.payload)
        if (
            first_event.kind != "mission_created"
            or not isinstance(first_event.payload, dict)
            or first_payload.get("mission_id") != restored.mission_id
            or first_payload.get("objective") != restored.objective
            or tuple(first_payload.get("scope", ())) != restored.scope
            or tuple(first_payload.get("non_goals", ())) != restored.non_goals
        ):
            raise ValueError("lab snapshot mission event is not bound to identity")
        restored._validate_snapshot_records()
        restored._validate_execution_event_semantics()
        native_event_admitter = getattr(restored._native_controller, "admit_event_json", None)
        if callable(native_event_admitter):
            try:
                for event in restored.events:
                    restored._admit_native_controller_event(event)
            except RuntimeError as exc:
                raise ValueError("lab snapshot is rejected by native controller") from exc
        else:
            restored._validate_native_event_chain(tuple(restored.events))
        try:
            restored._assert_native_projection_consistency()
        except RuntimeError as exc:
            raise ValueError("lab snapshot projection is inconsistent") from exc
        if restored.state == "completed":
            if not restored._completion_requirements_met():
                raise ValueError("completed lab snapshot does not satisfy evidence quotas")
            completion_event_bound = False
            for event in restored.events:
                raw_payload: Any = event.payload
                if event.kind == "state_changed" and isinstance(raw_payload, dict):
                    event_payload = cast(dict[str, Any], raw_payload)
                    if event_payload.get("state") == "completed":
                        completion_event_bound = True
                        break
            if not completion_event_bound:
                raise ValueError("completed lab snapshot is not bound to a completion event")
        return restored

    def _completion_requirements_met(self) -> bool:
        if not self.hypotheses or not self.experiments or not self.observations:
            return False
        if any(not observation.valid for observation in self.observations.values()):
            return False
        for experiment in self.experiments.values():
            observations = [
                observation
                for observation in self.observations.values()
                if observation.experiment_id == experiment.experiment_id
            ]
            if len(observations) < experiment.expected_observations:
                return False
            clean_replicates = sum(
                observation.replication_of is not None and observation.clean
                for observation in observations
            )
            if clean_replicates < experiment.min_clean_replicates:
                return False
            if experiment.uncertainty_required and any(
                observation.uncertainty is None for observation in observations
            ):
                return False
        return True

    def archive_to_native(self, directory: str, *, max_events_per_segment: int = 64) -> dict[str, Any]:
        """Commit this validated run to Rust's segmented replay archive."""

        if type(directory) is not str:
            raise TypeError("native replay archive directory must be a string")
        if type(max_events_per_segment) is not int or max_events_per_segment < 1:
            raise ValueError("native replay archive requires a directory and positive segment size")
        if not directory.strip():
            raise ValueError("native replay archive requires a directory and positive segment size")
        aegis_nerve = _native_lab_module()
        archive = getattr(aegis_nerve, "aegis_lab_archive_events", None)
        if archive is None:
            raise RuntimeError("Rust Lab replay archive extension is unavailable")
        if not self.verify_event_chain():
            raise ValueError("cannot archive an invalid Lab event chain")

        events = [
            {
                "sequence": event.sequence,
                "state_epoch": event.state_epoch,
                "kind": _RUST_EVENT_KINDS.get(event.kind, "ReviewRecorded"),
                "payload_hash": list(bytes.fromhex(event.payload_hash)),
                "previous_event_hash": list(bytes.fromhex(event.previous_event_hash)),
                "event_hash": list(bytes.fromhex(event.event_hash)),
            }
            for event in self.events
        ]
        raw_manifest = archive(
            json.dumps(events),
            directory,
            int(self.mission_id, 16),
            max_events_per_segment,
        )
        decoded_manifest = json.loads(raw_manifest)
        if not isinstance(decoded_manifest, dict):
            raise RuntimeError("Rust Lab replay archive returned an invalid manifest")
        manifest = cast(dict[str, Any], decoded_manifest)
        try:
            _validate_replay_archive_manifest_metadata(manifest, require_current=True)
            _manifest_hash_bytes(manifest)
        except ValueError as exc:
            raise RuntimeError("Rust Lab replay archive returned an invalid versioned manifest") from exc
        raw_run_id = manifest.get("run_id")
        if type(raw_run_id) is not int or raw_run_id != int(self.mission_id, 16):
            raise RuntimeError("Rust Lab replay archive returned an invalid manifest identity")
        if not manifest.get("manifest_hash"):
            raise RuntimeError("Rust Lab replay archive returned an invalid manifest")
        strict_verifier = getattr(
            aegis_nerve, "aegis_lab_verify_archive_against_manifest", None
        )
        verifier = getattr(aegis_nerve, "aegis_lab_verify_archive", None)
        if strict_verifier is not None:
            verified = strict_verifier(
                directory,
                int(self.mission_id, 16),
                _manifest_hash_bytes(manifest),
            )
        elif verifier is not None:
            verified = verifier(directory, int(self.mission_id, 16))
        else:
            verified = True
        if type(verified) is not bool or not verified:
            raise RuntimeError("Rust Lab replay archive failed recovery verification")
        event_verifier = getattr(
            aegis_nerve, "aegis_lab_verify_archive_against_events", None
        )
        if callable(event_verifier):
            try:
                events_verified = event_verifier(
                    directory,
                    int(self.mission_id, 16),
                    self._native_event_wire(tuple(self.events)),
                )
            except Exception as exc:
                raise RuntimeError(
                    "Rust Lab replay archive event identity verification failed"
                ) from exc
            if type(events_verified) is not bool or not events_verified:
                raise RuntimeError(
                    "Rust Lab replay archive event identity verification failed"
                )
        snapshot_path = Path(directory) / f"lab-{self.mission_id}.snapshot.json"
        manifest["snapshot_path"] = str(snapshot_path)
        self.replay_archive = manifest
        snapshot_without_archive = self.to_payload()
        snapshot_without_archive["replay_archive"] = None
        manifest["snapshot_hash"] = _hash(snapshot_without_archive)
        self.replay_archive = manifest
        _atomic_write_json(snapshot_path, self.to_payload())
        return self.replay_archive

    @classmethod
    def recover_from_archive(cls, snapshot_path: str) -> LabRun:
        """Restore a run from the durable JSON snapshot next to its archive."""

        path = Path(snapshot_path)
        if not path.is_file():
            raise ValueError("lab replay snapshot does not exist")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("lab replay snapshot is unreadable") from exc
        if not isinstance(payload, dict):
            raise ValueError("lab replay snapshot must be an object")
        typed_payload = cast(dict[str, Any], payload)
        run = cls.from_payload(typed_payload)
        archive = run.replay_archive
        if not isinstance(archive, dict):
            raise ValueError("lab replay snapshot has no archive manifest")
        snapshot_without_archive: dict[str, Any] = dict(typed_payload)
        snapshot_without_archive["replay_archive"] = None
        expected_hash = archive.get("snapshot_hash")
        if type(expected_hash) is not str or not _is_digest(expected_hash):
            raise ValueError("lab replay snapshot has an invalid archive snapshot hash")
        if expected_hash != _hash(snapshot_without_archive):
            raise ValueError("lab replay snapshot hash mismatch")
        try:
            native_module = _native_lab_module()
        except ImportError:
            native_module = None
        legacy_manifest = "schema" not in archive and "version" not in archive
        legacy_verifier = getattr(
            native_module, "aegis_lab_verify_archive_against_legacy_manifest", None
        )
        strict_verifier = getattr(
            native_module, "aegis_lab_verify_archive_against_manifest", None
        )
        verifier = getattr(native_module, "aegis_lab_verify_archive", None)
        if legacy_manifest:
            if legacy_verifier is None:
                raise ValueError("legacy native lab replay verifier is unavailable")
            verified = legacy_verifier(
                str(path.parent),
                int(run.mission_id, 16),
                _manifest_hash_bytes(archive),
            )
        elif strict_verifier is not None:
            verified = strict_verifier(
                str(path.parent),
                int(run.mission_id, 16),
                _manifest_hash_bytes(archive),
            )
        elif verifier is not None:
            verified = verifier(str(path.parent), int(run.mission_id, 16))
        else:
            verified = True
        if type(verified) is not bool or not verified:
            raise ValueError("native lab replay archive recovery failed")
        event_verifier = getattr(
            native_module, "aegis_lab_verify_archive_against_events", None
        )
        if callable(event_verifier):
            try:
                events_verified = event_verifier(
                    str(path.parent),
                    int(run.mission_id, 16),
                    run._native_event_wire(tuple(run.events)),
                )
            except Exception as exc:
                raise ValueError(
                    "native lab replay archive event identity verification failed"
                ) from exc
            if type(events_verified) is not bool or not events_verified:
                raise ValueError(
                    "native lab replay archive event identity verification failed"
                )
        return run

    def _validate_execution_event_semantics(self) -> None:
        """Reject duplicate or orphaned execution events during restore.

        Hash-chain integrity alone does not prove that a validly hashed event
        was admitted exactly once.  Restore therefore applies the same
        identity fence used by live settlement methods to durable snapshots.
        """

        admission_specs = {
            "experiment_execution_admitted": ("experiment_execution_recorded", "execution_id", "experiment execution"),
            "tool_execution_admitted": ("tool_execution_recorded", "execution_id", "tool execution"),
            "research_program_admitted": ("research_program_executed", "program_hash", "research program"),
            "browser_action_admitted": ("browser_action_recorded", "action_id", "browser action"),
            "browser_observation_admitted": ("browser_observation_recorded", "observation_id", "browser observation"),
            "skill_admission_recorded": ("skill_execution_recorded", "admission_hash", "skill execution"),
            "cancellation_admitted": ("cancellation_recorded", "request_id", "cancellation"),
        }
        admissions: dict[tuple[str, str], str] = {}
        settled: set[tuple[str, str]] = set()
        record_specs = {
            record_kind: (admission_kind, identity_key, label)
            for admission_kind, (record_kind, identity_key, label) in admission_specs.items()
        }
        for event in self.events:
            admission_kind = event.kind
            spec = admission_specs.get(event.kind)
            is_admission = spec is not None
            if spec is None:
                record_spec = record_specs.get(event.kind)
                if record_spec is None:
                    continue
                admission_kind, identity_key, label = record_spec
            else:
                _, identity_key, label = spec
            payload = event.payload
            if not isinstance(payload, dict):
                raise ValueError(f"invalid {label} event payload in lab snapshot")
            typed_payload = cast(dict[str, Any], cast(Any, payload))
            if is_admission and admission_kind != "cancellation_admitted":
                self._validate_execution_admission_payload(admission_kind, typed_payload)
            raw_identity = typed_payload.get(identity_key)
            if type(raw_identity) is not str:
                raise ValueError(f"invalid {label} event identity in lab snapshot")
            identity = raw_identity.strip()
            if not identity:
                raise ValueError(f"invalid {label} event identity in lab snapshot")
            admission_key = (admission_kind, identity)
            if not is_admission:
                if admission_key not in admissions:
                    raise ValueError(f"{label} settlement references unknown admission in lab snapshot")
                if admission_key in settled:
                    raise ValueError(f"duplicate {label} settlement in lab snapshot")
                raw_admission_id = typed_payload.get("admission_id")
                if identity_key == "admission_hash":
                    stored_admission_id = identity
                elif type(raw_admission_id) is str:
                    stored_admission_id = raw_admission_id
                else:
                    raise ValueError(f"invalid {label} admission identity in lab snapshot")
                if stored_admission_id != admissions[admission_key]:
                    raise ValueError(f"{label} settlement admission binding mismatch in lab snapshot")
                settled.add(admission_key)
                continue
            if admission_key in admissions:
                raise ValueError(f"duplicate {label} admission in lab snapshot")
            raw_admission_id = typed_payload.get("admission_id")
            if identity_key == "admission_hash":
                stored_admission_id = identity
            elif type(raw_admission_id) is str:
                stored_admission_id = raw_admission_id
            else:
                raise ValueError(f"invalid {label} admission identity in lab snapshot")
            if not stored_admission_id.strip():
                raise ValueError(f"invalid {label} admission identity in lab snapshot")
            admissions[admission_key] = stored_admission_id

    def _validate_snapshot_records(self) -> None:
        for source in self.sources.values():
            source.validate()
            parsed_uri = urlparse(source.uri)
            if (
                not source.source_id.strip()
                or not source.uri.strip()
                or parsed_uri.scheme != "https"
                or not parsed_uri.hostname
                or parsed_uri.username
                or parsed_uri.password
                or not source.content_hash
                or not source.snapshot_hash
                or source.retrieved_at_ms <= 0
                or source.trust_tier < 1
                or not source.relation.strip()
                or (source.provenance_cluster and not source.provenance_cluster.strip())
                or any(not _valid_citation_span(span) for span in source.citation_spans)
            ):
                raise ValueError("invalid source in lab snapshot")
        for claim in self.claims.values():
            claim.validate()
            if (
                not claim.claim_id.strip()
                or not claim.statement.strip()
                or not claim.source_ids
                or any(source_id not in self.sources for source_id in claim.source_ids)
                or not 0 <= claim.confidence_bps <= 10_000
            ):
                raise ValueError("invalid claim in lab snapshot")
        for hypothesis in self.hypotheses.values():
            hypothesis.validate()
            if (
                not hypothesis.hypothesis_id.strip()
                or not hypothesis.statement.strip()
                or not hypothesis.falsifiers
                or any(claim_id not in self.claims for claim_id in (*hypothesis.supporting_claim_ids, *hypothesis.contradicting_claim_ids))
                or not 0 <= hypothesis.prior_bps <= 10_000
            ):
                raise ValueError("invalid hypothesis in lab snapshot")
        for experiment in self.experiments.values():
            experiment.validate()
            if experiment.hypothesis_id not in self.hypotheses:
                raise ValueError("invalid experiment in lab snapshot")
            DEFAULT_UNIT_REGISTRY.signature(experiment.measurement_unit)
        for observation in self.observations.values():
            observation.validate()
            if (
                observation.experiment_id not in self.experiments
            ):
                raise ValueError("invalid observation in lab snapshot")
            experiment = self.experiments[observation.experiment_id]
            if not DEFAULT_UNIT_REGISTRY.compatible(observation.unit, experiment.measurement_unit):
                raise ValueError("observation unit is incompatible with experiment in lab snapshot")
            if observation.reported_unit:
                DEFAULT_UNIT_REGISTRY.signature(observation.reported_unit)
            if observation.seed not in experiment.preregistered_seeds:
                raise ValueError("observation seed was not preregistered in lab snapshot")
            if experiment.uncertainty_required and observation.uncertainty is None:
                raise ValueError("missing required observation uncertainty in lab snapshot")
            if observation.replication_of is not None:
                parent = self.observations.get(observation.replication_of)
                if (
                    parent is None
                    or parent.experiment_id != observation.experiment_id
                    or not observation.operator_id.strip()
                    or parent.environment_hash == observation.environment_hash
                    or not observation.clean
                ):
                    raise ValueError("invalid clean replication in lab snapshot")
        for admission in self.skill_admissions.values():
            try:
                admission.validate_hash()
            except SkillAdmissionError as exc:
                raise ValueError("invalid skill admission in lab snapshot") from exc
            if admission.mission_id != self.mission_id:
                raise ValueError("skill admission mission mismatch in lab snapshot")

    def _event_chain_authority(self) -> str:
        """Report native verification without making it a hidden dependency."""

        try:
            aegis_nerve = _native_lab_module()
            verifier = getattr(aegis_nerve, "aegis_lab_verify_event_chain", None)
            if verifier is None:
                return "python_projection_only"

            def digest_bytes(value: str) -> list[int]:
                return list(bytes.fromhex(value))

            events: list[dict[str, Any]] = [
                {
                    "sequence": event.sequence,
                    "state_epoch": event.state_epoch,
                    "kind": _RUST_EVENT_KINDS.get(event.kind, "ReviewRecorded"),
                    "payload_hash": digest_bytes(event.payload_hash),
                    "previous_event_hash": digest_bytes(event.previous_event_hash),
                    "event_hash": digest_bytes(event.event_hash),
                }
                for event in self.events
            ]
            accepted = verifier(json.dumps(events))
            if type(accepted) is not bool:
                return "python_projection_only"
            return "rust_native_verified" if accepted else "rust_native_rejected"
        except Exception:
            return "python_projection_only"


@dataclass(frozen=True)
class LabPolicy:
    """Explicit authority and side-effect policy for a Lab session."""

    trust_level: str = "DEV"
    allowed_hosts: tuple[str, ...] = ()
    require_https: bool = True
    max_browser_actions: int = 100
    replay_directory: str | None = None
    replay_archive: bool = True
    allow_external_writes: bool = False
    require_native_authority: bool | None = None

    @classmethod
    def max_within_policy(
        cls,
        *,
        allowed_hosts: tuple[str, ...] = (),
        replay_directory: str | None = None,
    ) -> LabPolicy:
        """Return the broadest read/compute policy without external writes."""

        return cls(
            trust_level="DEV",
            allowed_hosts=allowed_hosts,
            replay_directory=replay_directory,
            allow_external_writes=False,
        )

    def validate(self) -> None:
        if type(self.trust_level) is not str:
            raise ValueError("lab policy trust level must be a string")
        _normalize_lab_trust_level(self.trust_level)
        if type(self.allowed_hosts) not in (list, tuple):
            raise ValueError("lab policy hosts must be a sequence")
        if type(self.require_https) is not bool:
            raise ValueError("lab policy HTTPS requirement must be boolean")
        if type(self.max_browser_actions) is not int or self.max_browser_actions < 1:
            raise ValueError("lab policy browser quota must be positive")
        if type(self.replay_directory) not in (str, type(None)):
            raise ValueError("lab policy replay directory must be a string or unset")
        if any(type(host) is not str for host in self.allowed_hosts):
            raise ValueError("lab policy hosts must be strings")
        normalized_hosts = tuple(host.strip().lower() for host in self.allowed_hosts)
        if any(not host or host != original for host, original in zip(normalized_hosts, self.allowed_hosts, strict=True)):
            raise ValueError("lab policy hosts must be lowercase and trimmed")
        if self.replay_directory is not None and not self.replay_directory.strip():
            raise ValueError("lab policy replay directory must be non-empty")
        if type(self.replay_archive) is not bool:
            raise ValueError("lab policy replay archive flag must be boolean")
        if type(self.allow_external_writes) is not bool:
            raise ValueError("lab policy external-write flag must be boolean")
        if self.require_native_authority is not None and type(self.require_native_authority) is not bool:
            raise ValueError("lab policy native-authority requirement must be boolean or unset")

    @property
    def native_authority_required(self) -> bool:
        """Require Rust admission by default for production trust levels."""

        if self.require_native_authority is not None:
            return self.require_native_authority
        return _normalize_lab_trust_level(self.trust_level) == "PROD"

    @property
    def authority_mode(self) -> AuthorityMode:
        """Expose the effective authority mode instead of an implicit bool."""

        return (
            AuthorityMode.NATIVE_REQUIRED
            if self.native_authority_required
            else AuthorityMode.PROJECTION_ONLY
        )

    @property
    def trust_policy_hash(self) -> str:
        """Return the policy subject that must accompany this Lab session."""

        return _trust_policy_hash(self.trust_level)


@dataclass(frozen=True)
class LabBudget:
    """Disjoint exploration/finalization/recovery budget for one mission."""

    max_steps: int = 100
    max_external_attempts: int | None = None
    token_budget: int = 100_000
    finalization_reserve: int | None = None
    recovery_reserve: int | None = None

    def validate(self) -> None:
        if type(self.max_steps) is not int or type(self.token_budget) is not int:
            raise ValueError("lab budget bounds must be integers")
        if self.max_steps < 1 or self.token_budget < 1:
            raise ValueError("lab budget bounds must be positive")
        if self.max_external_attempts is not None and (
            type(self.max_external_attempts) is not int
            or self.max_external_attempts < 1
        ):
            raise ValueError("lab external attempt budget must be positive or unset")
        if self.finalization_reserve is not None and type(self.finalization_reserve) is not int:
            raise ValueError("lab finalization reserve must be an integer or unset")
        if self.recovery_reserve is not None and type(self.recovery_reserve) is not int:
            raise ValueError("lab recovery reserve must be an integer or unset")
        finalization = self.finalization_reserve
        recovery = self.recovery_reserve
        if finalization is None:
            finalization = min(max(1, self.token_budget // 5), max(0, self.token_budget - 1))
        if recovery is None:
            recovery = min(max(0, self.token_budget // 10), max(0, self.token_budget - finalization - 1))
        if finalization < 0 or recovery < 0 or finalization + recovery >= self.token_budget:
            raise ValueError("lab budget reserves must leave exploration headroom")

    def as_options(self) -> dict[str, int]:
        self.validate()
        finalization = self.finalization_reserve
        recovery = self.recovery_reserve
        if finalization is None:
            finalization = min(max(1, self.token_budget // 5), max(0, self.token_budget - 1))
        if recovery is None:
            recovery = min(max(0, self.token_budget // 10), max(0, self.token_budget - finalization - 1))
        options = {
            "lab_token_budget": self.token_budget,
            "lab_finalization_reserve": finalization,
            "lab_recovery_reserve": recovery,
        }
        if self.max_external_attempts is not None:
            options["lab_max_external_attempts"] = self.max_external_attempts
        return options


@dataclass(frozen=True)
class LabMissionSpec:
    """User-visible mission contract used by the long-horizon ``Lab`` API."""

    objective: str
    scope: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()

    def validate(self) -> None:
        if type(self.objective) is not str or not self.objective.strip():
            raise ValueError("lab mission objective must be non-empty")
        if type(self.scope) not in (list, tuple) or type(self.non_goals) not in (list, tuple):
            raise ValueError("lab mission scope and non-goals must be sequences")
        if any(type(item) is not str or not item.strip() for item in (*self.scope, *self.non_goals)):
            raise ValueError("lab mission scope and non-goals must be non-empty strings")


def _process_execution_worker(
    connection: Any,
    runner: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Execute one picklable edge runner outside the Lab event-loop process."""

    try:
        result = runner(*args, **kwargs)
        if inspect.isawaitable(result):
            result = asyncio.run(cast(Coroutine[Any, Any, Any], result))
        connection.send(("SUCCESS", result))
    except BaseException as exc:  # pragma: no cover - exercised in child process
        with contextlib.suppress(BaseException):
            connection.send(("ERROR", type(exc).__name__, str(exc)))
            # A non-picklable exception must not leave the parent waiting for a
            # result; the parent will observe the child exit and fail closed.
    finally:
        with contextlib.suppress(BaseException):
            connection.close()


class ProcessExecutionCell:
    """Run one picklable adapter in a killable child process.

    This is an opt-in local isolation cell. It bounds wall time and ensures a
    Python adapter that ignores cancellation cannot keep the Lab event loop
    alive. OS-level resource enforcement, descendant-process cleanup and
    cross-platform Job Object/cgroup guarantees still require the external
    platform evidence recorded in the plan.
    """

    def __init__(
        self,
        runner: Any,
        *,
        timeout_seconds: float = 30.0,
        start_method: str = "spawn",
    ) -> None:
        if not callable(runner):
            raise TypeError("process execution runner must be callable")
        if (
            type(timeout_seconds) not in (int, float)
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(float(timeout_seconds))
            or float(timeout_seconds) <= 0
        ):
            raise ValueError("process execution timeout must be finite and positive")
        available_methods = multiprocessing.get_all_start_methods()
        if type(start_method) is not str or start_method not in available_methods:
            raise ValueError("process execution start method is unavailable")
        self.runner = runner
        self.timeout_seconds = float(timeout_seconds)
        self.start_method = start_method
        self._active_process: Any | None = None

    @property
    def active_pid(self) -> int | None:
        process = self._active_process
        pid = getattr(process, "pid", None) if process is not None else None
        return pid if type(pid) is int and pid > 0 else None

    @staticmethod
    def _terminate_process(process: Any) -> None:
        if process is None:
            return
        try:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1.0)
        except (AssertionError, OSError):
            # A process that failed before start or already exited is already
            # fail-closed; cleanup must not mask the original error.
            return

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._active_process is not None:
            raise RuntimeError("process execution cell is busy")
        context: Any = multiprocessing.get_context(self.start_method)
        parent, child = context.Pipe(duplex=False)
        process: Any = context.Process(
            target=_process_execution_worker,
            args=(child, self.runner, tuple(args), dict(kwargs)),
            name="aegis-lab-process-cell",
        )
        process.daemon = True
        started = False
        self._active_process = process
        try:
            process.start()
            started = True
            child.close()
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if parent.poll():
                    raw_message: Any = parent.recv()
                    if not isinstance(raw_message, tuple):
                        raise RuntimeError("process execution runner returned an invalid message")
                    message = cast(tuple[Any, ...], raw_message)
                    if message and message[0] == "SUCCESS":
                        return message[1]
                    if len(message) >= 3 and message[0] == "ERROR":
                        raise RuntimeError(
                            f"process execution runner failed: {message[1]}: {message[2]}"
                        )
                    raise RuntimeError("process execution runner returned an invalid message")
                if not process.is_alive():
                    process.join()
                    raise RuntimeError("process execution runner exited without a result")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_process(process)
                    raise TimeoutError("process execution cell timed out")
                await asyncio.sleep(min(0.02, max(0.001, remaining)))
        except asyncio.CancelledError:
            if started:
                self._terminate_process(process)
            raise
        finally:
            if started:
                self._terminate_process(process)
                with contextlib.suppress(AssertionError):
                    process.join(timeout=1.0)
            with contextlib.suppress(OSError, ValueError):
                child.close()
            with contextlib.suppress(OSError, ValueError):
                parent.close()
            self._active_process = None


class ReplayWriterLease:
    """Advisory cross-process lease for one durable replay directory.

    The lock is held by an open file descriptor, so normal process exit releases
    it without relying on a stale PID marker.  This serializes local writers on
    POSIX and Windows; it does not replace the native event authority, a hosted
    lock service, or proof that an external side effect was rolled back.
    """

    _LOCK_FILE = ".lab-writer.lock"

    def __init__(self, directory: object) -> None:
        if type(directory) is not str and not hasattr(directory, "__fspath__"):
            raise TypeError("replay writer directory must be a string or path-like value")
        raw_directory = (
            directory
            if type(directory) is str
            else os.fspath(cast(os.PathLike[str], directory))
        )
        if type(raw_directory) is not str:
            raise TypeError("replay writer directory must resolve to text")
        normalized = raw_directory.strip()
        if not normalized:
            raise ValueError("replay writer directory must be non-empty")
        self.directory = Path(normalized)
        self.path = self.directory / self._LOCK_FILE
        self._handle: Any | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("replay writer lease is already acquired")
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError, ValueError) as exc:
            with contextlib.suppress(OSError):
                handle.close()
            raise RuntimeError("replay writer directory is already leased") from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError, ValueError):
            # Closing the descriptor is still sufficient for the OS to release
            # the advisory lock; cleanup must not mask the original run result.
            pass
        finally:
            with contextlib.suppress(OSError):
                handle.close()

    def __enter__(self) -> ReplayWriterLease:
        self.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


@dataclass(frozen=True)
class ExecutionCellBinding:
    """Trusted registration for one executable Lab edge adapter.

    Controller output can select a ``cell_id`` but can never provide the
    callable.  The callable is injected at the operator/configuration
    boundary and the binding records the capability/effect/trust envelope
    that must hold before the runner is returned to the dispatcher.
    """

    cell_id: str
    action_kinds: tuple[str, ...]
    runner: Any
    capabilities: tuple[str, ...] = ()
    effect_classes: tuple[str, ...] = (
        "read_only",
        "network_read",
        "compute",
        "model_inference",
    )
    trust_levels: tuple[str, ...] = ("DEV", "STAGING", "PROD")
    trust_policy_hash: str | None = None

    def validate(self) -> None:
        if type(self.cell_id) is not str or not self.cell_id.strip():
            raise ValueError("execution cell id must be non-empty")
        if type(self.action_kinds) not in (list, tuple):
            raise ValueError("execution cell action kinds must be a sequence")
        if any(type(kind) is not str for kind in self.action_kinds):
            raise ValueError("execution cell action kinds must be strings")
        kinds = tuple(kind.strip().lower() for kind in self.action_kinds)
        if not kinds or any(kind not in _EXECUTION_CELL_ACTION_KINDS for kind in kinds):
            raise ValueError("execution cell action kind is unsupported")
        if len(kinds) != len(set(kinds)):
            raise ValueError("execution cell action kinds must be unique")
        browser_adapter = "browser_action" in kinds and hasattr(self.runner, "capture_action")
        if "browser_action" in kinds and not browser_adapter:
            raise TypeError("browser execution cell must expose capture_action")
        if not callable(self.runner) and not browser_adapter:
            raise TypeError("execution cell runner must be callable")
        if type(self.capabilities) not in (list, tuple):
            raise ValueError("execution cell capabilities must be a sequence")
        if any(type(capability) is not str for capability in self.capabilities):
            raise ValueError("execution cell capabilities must be strings")
        if any(not capability.strip() for capability in self.capabilities):
            raise ValueError("execution cell capabilities must be non-empty")
        if type(self.effect_classes) not in (list, tuple):
            raise ValueError("execution cell effect classes must be a sequence")
        if any(type(effect) is not str for effect in self.effect_classes):
            raise ValueError("execution cell effect classes must be strings")
        if any(not effect.strip() for effect in self.effect_classes):
            raise ValueError("execution cell effect classes must be non-empty")
        if type(self.trust_levels) not in (list, tuple):
            raise ValueError("execution cell trust levels must be a sequence")
        if any(type(level) is not str for level in self.trust_levels):
            raise ValueError("execution cell trust levels must be strings")
        normalized_trust = tuple(level.strip().upper() for level in self.trust_levels)
        if not normalized_trust or any(level not in {"DEV", "STAGING", "PROD"} for level in normalized_trust):
            raise ValueError("execution cell trust level is unsupported")
        if type(self.trust_policy_hash) not in (str, type(None)):
            raise ValueError("execution cell trust policy hash must be a string or unset")
        if self.trust_policy_hash is not None and not _is_digest(self.trust_policy_hash):
            raise ValueError("execution cell trust policy hash is invalid")


class ExecutionCellRegistry:
    """Single trusted lookup table for controller-selected edge adapters."""

    def __init__(
        self,
        bindings: tuple[ExecutionCellBinding, ...] = (),
        *,
        trust_policy_hash: str | None = None,
    ) -> None:
        if type(bindings) not in (list, tuple):
            raise TypeError("execution cell registry bindings must be a sequence")
        if type(trust_policy_hash) not in (str, type(None)):
            raise ValueError("execution cell registry trust policy hash must be a string or unset")
        if trust_policy_hash is not None and not _is_digest(trust_policy_hash):
            raise ValueError("execution cell registry trust policy hash is invalid")
        self._trust_policy_hash = trust_policy_hash
        self._bindings: dict[str, ExecutionCellBinding] = {}
        # Keep a sealed snapshot separate from the construction-time table.
        # The public API already rejects late ``register`` calls; the
        # snapshot also makes authoritative lookup/manifest generation
        # insensitive to accidental or reflective mutation of the private
        # construction table after sealing.
        self._sealed_bindings: Mapping[str, ExecutionCellBinding] | None = None
        self._sealed = False
        for binding in bindings:
            self.register(binding)

    def register(self, binding: ExecutionCellBinding) -> None:
        if self._sealed:
            raise RuntimeError("execution cell registry is sealed")
        binding.validate()
        if self._trust_policy_hash is not None and binding.trust_policy_hash not in {
            None,
            self._trust_policy_hash,
        }:
            raise ValueError("execution cell trust policy hash does not match Lab policy")
        normalized_binding = replace(
            binding,
            action_kinds=tuple(kind.strip().lower() for kind in binding.action_kinds),
            capabilities=tuple(capability.strip() for capability in binding.capabilities),
            effect_classes=tuple(effect.strip() for effect in binding.effect_classes),
            trust_levels=tuple(level.strip().upper() for level in binding.trust_levels),
            trust_policy_hash=binding.trust_policy_hash or self._trust_policy_hash,
        )
        for kind in normalized_binding.action_kinds:
            normalized_kind = kind.strip().lower()
            if normalized_kind in self._bindings:
                raise ValueError(f"execution cell action kind already registered: {normalized_kind}")
            self._bindings[normalized_kind] = normalized_binding

    @property
    def sealed(self) -> bool:
        return self._sealed

    def seal(self) -> None:
        """Freeze trusted bindings after their manifest is replay-bound."""

        if self._sealed:
            return
        self._sealed_bindings = MappingProxyType(dict(self._bindings))
        self._sealed = True

    def resolve(
        self,
        action_kind: str,
        *,
        cell_id: str | None = None,
        trust_level: str = "DEV",
        capability: str | None = None,
        effect_class: str | None = None,
        trust_policy_hash: str | None = None,
    ) -> Any:
        if type(action_kind) is not str or not action_kind.strip():
            raise ValueError("execution cell action kind must be a non-empty string")
        if type(trust_level) is not str or not trust_level.strip():
            raise ValueError("execution cell trust level must be a non-empty string")
        if type(cell_id) not in (str, type(None)):
            raise ValueError("execution cell id must be a string or unset")
        if cell_id is not None and (
            not cell_id.strip() or cell_id != cell_id.strip()
        ):
            raise ValueError("execution cell id must be a non-empty trimmed string")
        if type(capability) not in (str, type(None)):
            raise ValueError("execution cell capability must be a string or unset")
        if type(effect_class) not in (str, type(None)):
            raise ValueError("execution cell effect must be a string or unset")
        if type(trust_policy_hash) not in (str, type(None)):
            raise ValueError("execution cell trust policy hash must be a string or unset")
        normalized_kind = action_kind.strip().lower()
        bindings = self._sealed_bindings if self._sealed_bindings is not None else self._bindings
        binding = bindings.get(normalized_kind)
        if binding is None:
            raise LookupError(f"execution cell is not registered: {normalized_kind}")
        if cell_id is not None and cell_id != binding.cell_id:
            raise PermissionError("controller requested an unregistered execution cell")
        normalized_trust = trust_level.strip().upper()
        if normalized_trust not in binding.trust_levels:
            raise PermissionError("execution cell trust level is not permitted")
        if self._trust_policy_hash is not None and trust_policy_hash != self._trust_policy_hash:
            raise PermissionError("execution cell trust policy hash is not permitted")
        if binding.trust_policy_hash is not None and trust_policy_hash != binding.trust_policy_hash:
            raise PermissionError("execution cell trust policy hash is not permitted")
        if capability is not None and capability.strip() not in binding.capabilities:
            raise PermissionError("execution cell capability is not permitted")
        if effect_class is not None and effect_class.strip() not in binding.effect_classes:
            raise PermissionError("execution cell effect is not permitted")
        return binding.runner

    def manifest(self) -> tuple[dict[str, Any], ...]:
        """Return bounded metadata suitable for a replay/policy hash."""

        bindings = self._sealed_bindings if self._sealed_bindings is not None else self._bindings
        unique_bindings = {
            (binding.cell_id, tuple(binding.action_kinds)): binding
            for binding in bindings.values()
        }
        return tuple(
            {
                "cell_id": binding.cell_id,
                "action_kinds": tuple(binding.action_kinds),
                "capabilities": tuple(binding.capabilities),
                "effect_classes": tuple(binding.effect_classes),
                "trust_levels": tuple(binding.trust_levels),
                **(
                    {"trust_policy_hash": binding.trust_policy_hash}
                    if binding.trust_policy_hash is not None
                    else {}
                ),
            }
            for binding in sorted(unique_bindings.values(), key=lambda item: item.cell_id)
        )


class LabSession:
    """Long-horizon handle with live evidence events and operator controls."""

    def __init__(
        self,
        *,
        config: Any,
        gateway_factory: Any,
        telemetry: Any,
        correlation: Any,
    ) -> None:
        self.config = config
        self.gateway_factory = gateway_factory
        self.telemetry = telemetry
        self.correlation = correlation
        self._run: LabRun | None = None
        self._task: asyncio.Task[tuple[Any, LabDossier]] | None = None
        self._events: list[LabEvent] = []
        self._event_signal: asyncio.Event | None = None
        self._finished = False
        self._stream_started = False
        self._cancel_requested = False

    @property
    def run(self) -> LabRun | None:
        return self._run

    @property
    def status(self) -> str:
        return self._run.state if self._run is not None else "planned"

    def _attach_run(self, run: LabRun) -> None:
        self._run = run
        for event in run.events:
            self._on_event(event)
        run.subscribe(self._on_event)

    def _on_event(self, event: LabEvent) -> None:
        self._events.append(event)
        if self._event_signal is not None:
            self._event_signal.set()

    def _ensure_started(self) -> asyncio.Task[tuple[Any, LabDossier]]:
        if self._task is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError as exc:
                raise RuntimeError("LabSession requires a running event loop") from exc
            self._task = asyncio.create_task(self._execute(), name="aegis-lab-session")
            if self._cancel_requested:
                asyncio.get_running_loop().call_soon(self._task.cancel)
        return self._task

    async def _execute(self) -> tuple[Any, LabDossier]:
        try:
            return await LabApplication(
                config=self.config,
                gateway_factory=self.gateway_factory,
                telemetry=self.telemetry,
                correlation=self.correlation,
                run_sink=self._attach_run,
            ).run()
        except asyncio.CancelledError:
            # The application settles the currently admitted lane before this
            # task-level cancellation reaches the session.  If cancellation
            # arrives between lanes, close the run through the same native
            # cancellation admission rather than leaving it non-terminal.
            if self._run is not None and self._run.state not in {"completed", "aborted"}:
                self._run.abort()
            raise
        finally:
            self._finished = True
            if self._event_signal is not None:
                self._event_signal.set()

    async def events(self):
        """Yield evidence deltas as they are committed, without model traces."""

        if self._stream_started:
            raise RuntimeError("LabSession event stream already has a consumer")
        self._stream_started = True
        self._ensure_started()
        self._event_signal = asyncio.Event()
        index = 0
        while True:
            while index < len(self._events):
                event = self._events[index]
                index += 1
                yield event
            if self._finished:
                return
            await self._event_signal.wait()
            self._event_signal.clear()

    async def result(self) -> tuple[Any, LabDossier]:
        """Wait for the synthesis output and final dossier."""

        return await self._ensure_started()

    async def export_dossier(self) -> LabDossier:
        return (await self.result())[1]

    def pause(self) -> None:
        if self._run is None:
            raise RuntimeError("LabSession has not started")
        self._run.pause()

    def resume(self) -> None:
        if self._run is None:
            raise RuntimeError("LabSession has not started")
        self._run.resume()

    def cancel(self) -> None:
        self._cancel_requested = True
        if self._task is not None and not self._task.done():
            self._task.cancel()


class Lab:
    """Public Lab facade; ``Agent.run()`` remains the compatibility path."""

    def __init__(
        self,
        *,
        policy: LabPolicy | None = None,
        budget: LabBudget | None = None,
        llm: Any = None,
        gateway_factory: Any = None,
        telemetry: Any = None,
    ) -> None:
        from .infrastructure import build_gateway
        from .observability import RuntimeTelemetry

        self.policy = policy or LabPolicy()
        self.budget = budget or LabBudget()
        self.policy.validate()
        self.budget.validate()
        self.llm = llm
        self.gateway_factory = gateway_factory or build_gateway
        self.telemetry = telemetry or RuntimeTelemetry()

    def start(self, mission: LabMissionSpec | str, **options: Any) -> LabSession:
        from .config import AgentConfig
        from .observability import CorrelationContext

        spec = mission if isinstance(mission, LabMissionSpec) else LabMissionSpec(mission)
        spec.validate()
        merged = dict(options)
        merged.update(self.budget.as_options())
        merged["lab"] = True
        browser_policy = asdict(BrowserCellPolicy(
            allowed_hosts=self.policy.allowed_hosts,
            require_https=self.policy.require_https,
            max_actions=self.policy.max_browser_actions,
            max_observations=max(1, self.policy.max_browser_actions * 10),
        ))
        if "browser_policy" in merged and not _browser_policy_matches(merged["browser_policy"], browser_policy):
            raise ValueError("LabPolicy owns browser_policy; conflicting compatibility options are rejected")
        merged["browser_policy"] = browser_policy
        if self.policy.replay_directory is not None:
            merged.setdefault("lab_replay_directory", self.policy.replay_directory)
        if "non_goals" in merged:
            merged["non_goals"] = _strict_string_sequence(
                merged["non_goals"], label="lab non-goals"
            )
        else:
            merged["non_goals"] = spec.non_goals
        if "scope" in merged:
            merged["scope"] = _strict_string_sequence(merged["scope"], label="lab scope")
        else:
            merged["scope"] = spec.scope
        # Authority is owned by the policy object at the mission boundary;
        # compatibility options cannot silently downgrade a production run.
        merged["lab_require_native_authority"] = self.policy.native_authority_required
        merged["lab_authority_mode"] = self.policy.authority_mode.value
        # Trust policy is owned by LabPolicy; callers cannot replace its hash
        # through compatibility options without creating a new policy object.
        merged["lab_trust_policy_hash"] = self.policy.trust_policy_hash
        if "lab_allow_external_writes" in merged and (
            type(merged["lab_allow_external_writes"]) is not bool
            or merged["lab_allow_external_writes"] != self.policy.allow_external_writes
        ):
            raise ValueError(
                "LabPolicy owns lab_allow_external_writes; conflicting compatibility options are rejected"
            )
        merged["lab_allow_external_writes"] = self.policy.allow_external_writes
        if self.policy.replay_archive:
            default_replay_directory = self.policy.replay_directory or os.environ.get(
                "AEGIS_LAB_REPLAY_DIR",
                str(Path.cwd() / ".aegis" / "lab-replay"),
            )
            merged.setdefault("lab_replay_directory", default_replay_directory)
        raw_browser = merged.pop("browser", False)
        if type(raw_browser) is not bool:
            raise ValueError("Lab browser option must be boolean")
        browser_enabled = raw_browser
        config = AgentConfig.from_inputs(
            spec.objective,
            llm=self.llm,
            trust_level=self.policy.trust_level,
            browser=browser_enabled,
            max_steps=self.budget.max_steps,
            **merged,
        )
        return LabSession(
            config=config,
            gateway_factory=self.gateway_factory,
            telemetry=self.telemetry,
            correlation=CorrelationContext.new(),
        )


class LabApplication:
    """Run bounded research/actions/experiments, then synthesize once.

    Controller responses may contain a strictly typed action plan.  Only the
    five action kinds backed by configured edge adapters are executable here;
    arbitrary model-generated callables or provider names are never accepted.
    Each accepted action is sent through the same admission/settlement fence as
    an explicitly configured request.
    """

    def __init__(
        self,
        *,
        config: Any,
        gateway_factory: Any,
        telemetry: Any,
        correlation: Any,
        run_sink: Callable[[LabRun], Any] | None = None,
        event_sink: Callable[[LabEvent], Any] | None = None,
        context_retriever: Callable[..., Any] | None = None,
        system_context: str | None = None,
        post_completion_effect: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.gateway_factory = gateway_factory
        self.telemetry = telemetry
        self.correlation = correlation
        self.run_sink = run_sink
        self.event_sink = event_sink
        # Compatibility preparation may retrieve prior evidence.  When the
        # Lab owns that callback, the read is admitted/settled as a typed
        # execution cell before any controller/provider call instead of
        # happening invisibly in the outer Agent facade.
        self.context_retriever = context_retriever
        self.system_context = system_context
        # Compatibility callers may persist a completed result (for example
        # the learning index).  Keep that side effect inside the Lab ledger
        # rather than allowing an implicit write after the run has returned.
        self.post_completion_effect = post_completion_effect
        # One gateway instance is shared by all admitted model calls in this
        # Lab run.  This keeps provider routing/budget state at one explicit
        # adapter boundary instead of creating an untracked route per step.
        self._gateway_instance: Any | None = None
        self._active_run: LabRun | None = None
        # LabApplication executes gateway calls serially.  Keeping the
        # short-lived context here avoids adding private kwargs to arbitrary
        # compatibility gateway doubles while still binding each provider
        # attempt to its enclosing controller call.
        self._provider_attempt_context: dict[str, Any] = {}
        self._provider_attempt_receipts: dict[tuple[str, str, int], list[tuple[str, str]]] = {}
        # Keep one callback object so the post-construction handshake can
        # prove that a compatibility gateway installed this exact Lab fence,
        # rather than merely accepting and discarding an unknown keyword.
        self._provider_attempt_hook_ref = self._provider_attempt_hook
        self.execution_cells = ExecutionCellRegistry()
        # ``None`` means legacy options are converted into trusted default
        # bindings. Supplying an explicit registry is an authority decision:
        # a missing lane must fail closed instead of falling back to a raw
        # callable hidden in compatibility options.
        self._execution_cells_strict = False

    def _prepare_execution_cells(self, run: LabRun, options: dict[str, Any]) -> None:
        """Build one trusted cell registry for the complete Lab run.

        Legacy per-option runners are converted into explicit bindings so the
        dispatcher has one lookup path.  An operator may provide
        ``lab_execution_cells`` to replace these defaults with stricter
        capability/effect/trust envelopes.
        """

        raw_registry = options.get("lab_execution_cells")
        authority_mode = _authority_mode_from_options(
            options, default_trust_level=self.config.trust_level
        )
        native_required = authority_mode is AuthorityMode.NATIVE_REQUIRED
        # Native-required runs cannot use an unregistered compatibility edge
        # adapter.  DEV/STAGING retain the additive compatibility conversion;
        # an explicitly supplied registry is strict at every trust level.
        self._execution_cells_strict = raw_registry is not None or native_required
        if raw_registry is None and native_required:
            # A native-required mission must make its edge ownership explicit.
            # Auto-converting compatibility options here would make a callable
            # appear registry-bound without an operator-supplied manifest and
            # would leave hidden planner/lease ownership ambiguous.  Keep the
            # empty manifest replayable, then fail the affected lane closed.
            legacy_edge_keys = (
                "search_program_executor",
                "researcher",
                "search_as_code",
                "search_query_provider",
                "browser_runtime_adapter",
                "experiment_runner",
                "simulation_runner",
                "tool_runner",
                "benchmark_validator",
                "skill_executor",
            )
            if any(key in options for key in legacy_edge_keys):
                self.execution_cells = ExecutionCellRegistry(
                    trust_policy_hash=run.trust_policy_hash
                )
                run.bind_execution_cell_manifest(self.execution_cells.manifest())
                self.execution_cells.seal()
                run.record_blocker("execution_cell_registry_required:native_authority")
                return
        bindings: list[ExecutionCellBinding] = []
        try:
            search_runner = (
                options.get("search_program_executor")
                or options.get("researcher")
                or options.get("search_as_code")
            )
            if search_runner is None and callable(options.get("search_query_provider")):
                raw_search_timeout = options.get("search_timeout_seconds", 10.0)
                raw_search_max_bytes = options.get("search_max_bytes", 8 * 1024 * 1024)
                if (
                    not _is_finite_number(raw_search_timeout)
                    or float(raw_search_timeout) <= 0
                    or type(raw_search_max_bytes) is not int
                ):
                    raise TypeError("search executor metadata types are invalid")
                search_runner = SearchProgramExecutor(
                    query_provider=options.get("search_query_provider"),
                    timeout_seconds=raw_search_timeout,
                    max_bytes=raw_search_max_bytes,
                )
            if raw_registry is None:
                candidates: tuple[tuple[str, str, Any, tuple[str, ...], tuple[str, ...]], ...] = (
                    (
                        "context-retrieval",
                        "context_retrieval",
                        self.context_retriever,
                        ("read_only",),
                        ("read_only",),
                    ),
                    (
                        "search-program",
                        "search_program",
                        search_runner,
                        ("network_read",),
                        ("network_read", "read_only", "compute"),
                    ),
                    (
                        "browser-runtime",
                        "browser_action",
                        options.get("browser_runtime_adapter"),
                        ("network_read",),
                        ("network_read",),
                    ),
                    (
                        "python-experiment",
                        "experiment_action",
                        options.get("experiment_runner"),
                        ("compute",),
                        ("compute",),
                    ),
                    (
                        "simulation-cell",
                        "simulation_action",
                        options.get("simulation_runner"),
                        ("compute",),
                        ("compute",),
                    ),
                    (
                        "generic-tool",
                        "tool_call",
                        options.get("tool_runner"),
                        ("compute", "network_read", "read_only", "model_inference"),
                        tuple(_CONTROLLER_SAFE_TOOL_EFFECTS),
                    ),
                    (
                        "benchmark-validator",
                        "benchmark_validation",
                        options.get("benchmark_validator"),
                        ("compute",),
                        ("compute",),
                    ),
                    (
                        "skill-runtime",
                        "skill_execution",
                        options.get("skill_executor"),
                        ("compute", "network_read", "read_only"),
                        tuple(_CONTROLLER_SAFE_TOOL_EFFECTS),
                    ),
                    (
                        "post-completion-effect",
                        "post_completion_effect",
                        self.post_completion_effect,
                        ("memory_write",),
                        ("memory_write",),
                    ),
                )
                for cell_id, action_kind, runner, capabilities, effects in candidates:
                    if callable(runner) or (
                        action_kind == "browser_action" and hasattr(runner, "capture_action")
                    ):
                        bindings.append(
                            ExecutionCellBinding(
                                cell_id=cell_id,
                                action_kinds=(action_kind,),
                                runner=runner,
                                capabilities=capabilities,
                                effect_classes=effects,
                            )
                        )
            elif isinstance(raw_registry, Mapping):
                typed_registry = cast(Mapping[str, Any], raw_registry)
                for key, raw_binding in typed_registry.items():
                    if type(key) is not str or not key.strip():
                        raise TypeError("lab execution cell registry keys must be non-empty strings")
                    if isinstance(raw_binding, ExecutionCellBinding):
                        bindings.append(raw_binding)
                        continue
                    if not isinstance(raw_binding, Mapping):
                        raise TypeError("lab execution cell binding must be a mapping")
                    binding_map = cast(Mapping[str, Any], raw_binding)
                    raw_kinds = binding_map.get("action_kinds", (str(key),))
                    if isinstance(raw_kinds, str):
                        raw_kinds = (raw_kinds,)
                    if not isinstance(raw_kinds, (list, tuple)):
                        raise TypeError("lab execution cell action_kinds must be a sequence")
                    typed_kinds = cast(list[Any] | tuple[Any, ...], raw_kinds)
                    raw_capabilities = binding_map.get(
                        "capabilities",
                        ("compute", "network_read", "model_inference", "read_only"),
                    )
                    raw_effects = binding_map.get(
                        "effect_classes", tuple(_CONTROLLER_SAFE_TOOL_EFFECTS)
                    )
                    raw_trust_levels = binding_map.get(
                        "trust_levels", ("DEV", "STAGING", "PROD")
                    )
                    if not isinstance(raw_capabilities, (list, tuple)) or not isinstance(raw_effects, (list, tuple)) or not isinstance(raw_trust_levels, (list, tuple)):
                        raise TypeError("lab execution cell policy fields must be sequences")
                    typed_capabilities = cast(list[Any] | tuple[Any, ...], raw_capabilities)
                    typed_effects = cast(list[Any] | tuple[Any, ...], raw_effects)
                    typed_trust_levels = cast(list[Any] | tuple[Any, ...], raw_trust_levels)
                    bindings.append(
                        ExecutionCellBinding(
                            cell_id=cast(str, binding_map.get("cell_id", key)),
                            action_kinds=cast(tuple[str, ...], tuple(typed_kinds)),
                            runner=binding_map.get("runner"),
                            capabilities=cast(tuple[str, ...], tuple(typed_capabilities)),
                            effect_classes=cast(tuple[str, ...], tuple(typed_effects)),
                            trust_levels=cast(tuple[str, ...], tuple(typed_trust_levels)),
                        )
                    )
            elif isinstance(raw_registry, (list, tuple)):
                typed_bindings = cast(list[Any] | tuple[Any, ...], raw_registry)
                for raw_binding in typed_bindings:
                    if not isinstance(raw_binding, ExecutionCellBinding):
                        raise TypeError("lab execution cell sequence must contain bindings")
                    bindings.append(raw_binding)
            else:
                raise TypeError("lab_execution_cells must be a mapping or sequence")
            self.execution_cells = ExecutionCellRegistry(
                tuple(bindings), trust_policy_hash=run.trust_policy_hash
            )
            run.bind_execution_cell_manifest(self.execution_cells.manifest())
            self.execution_cells.seal()
        except (TypeError, ValueError) as exc:
            self.execution_cells = ExecutionCellRegistry()
            self.execution_cells.seal()
            run.bind_execution_cell_manifest(())
            run.record_blocker(f"execution_cell_registry_invalid:{type(exc).__name__}")

    def _resolve_execution_cell(
        self,
        run: LabRun,
        *,
        action_kind: str,
        options: dict[str, Any],
        cell_id: Any = None,
        capability: str | None = None,
        effect_class: str | None = None,
    ) -> Any | None:
        try:
            return self.execution_cells.resolve(
                action_kind,
                cell_id=cell_id,
                trust_level=self.config.trust_level,
                capability=capability,
                effect_class=effect_class,
                trust_policy_hash=run.trust_policy_hash,
            )
        except LookupError:
            return None
        except (PermissionError, TypeError, ValueError) as exc:
            run.record_blocker(f"execution_cell_not_permitted:{type(exc).__name__}")
            return None

    async def _run_context_retrieval(self, run: LabRun, options: dict[str, Any]) -> str:
        """Admit the compatibility RAG read before controller/provider calls.

        The retrieval result is untrusted prompt material.  Only its digest and
        bounded size enter the event ledger; a bounded injection marker rejects
        the context before it can be presented to the controller.
        """

        runner = self._resolve_execution_cell(
            run,
            action_kind="context_retrieval",
            options=options,
            capability="read_only",
            effect_class="read_only",
        )
        if not callable(runner):
            if self._execution_cells_strict and self.context_retriever is not None:
                run.record_blocker("execution_cell_not_registered:context_retrieval")
            return ""
        query = run.objective
        top_k = options.get("top_k", 3)
        max_chars = options.get("lab_context_max_chars", 16_384)
        raw_timeout = options.get("context_retrieval_timeout_seconds", 10.0)
        if (
            type(top_k) is not int
            or type(max_chars) is not int
            or top_k < 1
            or max_chars < 1
            or not _is_finite_number(raw_timeout)
            or float(raw_timeout) <= 0
        ):
            run.record_blocker("context_retrieval_policy_invalid:ValueError")
            return ""
        timeout_seconds = float(raw_timeout)
        input_payload = {
            "schema": "aegis-context-retrieval-input-v1",
            "query_hash": _hash(query),
            "top_k": top_k,
        }
        policy_payload = {
            "schema": "aegis-context-retrieval-policy-v1",
            "effect_class": "read_only",
            "trust_level": self.config.trust_level,
            "max_chars": max_chars,
            "timeout_seconds": timeout_seconds,
        }
        lease_id = max(1, len(run.tool_execution_admissions) + 1)
        execution_id = f"context-retrieval-{run.mission_id}"
        try:
            execution_id, admission_id = run.admit_tool_execution(
                tool_name="memory.search_past",
                input_payload=input_payload,
                policy_payload=policy_payload,
                effect_class="read_only",
                actor_role="observer",
                expected_observation_schema="aegis-context-retrieval-result-v1",
                stop_rule="single_context_retrieval",
                lease_id=lease_id,
                attempt=1,
                execution_id=execution_id,
                timeout_seconds=timeout_seconds,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            run.record_blocker(f"context_retrieval_admission_failed:{type(exc).__name__}")
            return ""

        async def settle(status: str, result: Any) -> None:
            run.record_tool_execution(
                tool_name="memory.search_past",
                execution_id=execution_id,
                admission_id=admission_id,
                input_payload=input_payload,
                policy_payload=policy_payload,
                result=result,
                effect_class="read_only",
                actor_role="observer",
                expected_observation_schema="aegis-context-retrieval-result-v1",
                stop_rule="single_context_retrieval",
                lease_id=lease_id,
                attempt=1,
                status=status,
                timeout_seconds=timeout_seconds,
            )

        try:
            raw_context = await asyncio.wait_for(
                _call_fenced(runner, query=query, task=query, run=run),
                timeout=timeout_seconds,
            )
            if raw_context is None:
                context = ""
            elif isinstance(raw_context, str):
                context = raw_context
            else:
                raise TypeError("context retriever must return text or None")
            if len(context) > max_chars:
                raise ValueError("context retriever returned oversized text")
            if _prompt_injection_marker_in_value(context):
                digest = _hash(context)
                run.record_security_event(
                    "context_prompt_injection_detected",
                    artifact_hash=digest,
                    detail="bounded_marker",
                )
                await settle("REJECTED", {"error": "prompt_injection_marker"})
                run.record_blocker("context_prompt_injection_detected")
                return ""
            await settle(
                "SUCCESS",
                {
                    "schema": "aegis-context-retrieval-result-v1",
                    "context_hash": _hash(context),
                    "character_count": len(context),
                },
            )
            return context
        except asyncio.CancelledError:
            try:
                await settle("CANCELLED", {"error": "CancelledError"})
            except (RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"context_retrieval_settlement_failed:{type(exc).__name__}")
            raise
        except Exception as exc:
            try:
                await settle(
                    "TIMED_OUT" if isinstance(exc, TimeoutError) else "REJECTED",
                    {"error": type(exc).__name__},
                )
            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                run.record_blocker(
                    f"context_retrieval_settlement_failed:{type(settlement_exc).__name__}"
                )
            run.record_blocker(f"context_retrieval_failed:{type(exc).__name__}")
            return ""

    def _gateway(self, task: str) -> Any:
        if self._gateway_instance is not None:
            return self._gateway_instance
        options = self.config.options
        gateway_options = {
            key: options[key]
            for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")
            if key in options
        }
        trust_policy_hash = options.get("lab_trust_policy_hash")
        if trust_policy_hash is not None and self._factory_accepts_keyword("trust_policy_hash"):
            gateway_options["trust_policy_hash"] = trust_policy_hash
        authority_mode = _authority_mode_from_options(
            options, default_trust_level=self.config.trust_level
        )
        require_native = authority_mode is AuthorityMode.NATIVE_REQUIRED
        if require_native:
            _validate_native_provider_options(options, llm=self.config.llm)
        if self._factory_accepts_keyword("provider_attempt_hook"):
            gateway_options["provider_attempt_hook"] = self._provider_attempt_hook_ref
        elif require_native:
            raise RuntimeError("native Lab authority requires a gateway provider-attempt fence")
        if trust_policy_hash is not None and require_native and "trust_policy_hash" not in gateway_options:
            raise RuntimeError("native Lab authority requires a gateway trust-policy fence")
        gateway_instance = self.gateway_factory(
            task=task,
            llm=self.config.llm,
            trust_level=self.config.trust_level,
            correlation=self.correlation,
            telemetry=self.telemetry,
            **gateway_options,
        )
        if require_native and getattr(gateway_instance, "provider_attempt_hook", None) is not self._provider_attempt_hook_ref:
            # A permissive ``**kwargs`` compatibility factory may accept the
            # hook but drop it.  Native-required mode must reject that
            # ambiguity before any model/provider side effect can occur.
            raise RuntimeError("native Lab authority requires the gateway to install its provider-attempt fence")
        if trust_policy_hash is not None and require_native and getattr(
            gateway_instance, "trust_policy_hash", None
        ) != trust_policy_hash:
            raise RuntimeError("native Lab authority requires a matching gateway trust-policy hash")
        # Native-required Lab retries are owned by the enclosing execution
        # cell.  A compatibility gateway that exposes its own multi-attempt
        # loop (for example ``AegisAgent.max_retries``) would otherwise create
        # an unaccounted retry layer and multiply external effects.  A
        # one-attempt adapter is compatible with the Lab-owned retry fence;
        # opaque SDK-internal retry behavior remains an external unknown.
        adapter_max_retries = getattr(gateway_instance, "max_retries", None)
        if require_native and adapter_max_retries is not None:
            if type(adapter_max_retries) is not int or adapter_max_retries < 1:
                raise RuntimeError("native Lab authority requires a valid gateway retry policy")
            if adapter_max_retries > 1:
                raise RuntimeError("native Lab authority requires Lab-owned gateway retries")
        self._gateway_instance = gateway_instance
        return self._gateway_instance

    def _factory_accepts_keyword(self, keyword: str) -> bool:
        """Detect whether a compatibility gateway can install a Lab hook."""

        try:
            signature = inspect.signature(self.gateway_factory)
        except (TypeError, ValueError):
            # A callable implemented in an extension may not expose a
            # signature; passing the keyword lets it fail closed at the
            # factory boundary instead of silently dropping the fence.
            return True
        return keyword in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    def _provider_attempt_hook(self, event: str, payload: Any) -> Any:
        """Native-fence every provider candidate attempted by a gateway.

        The outer ``gateway.*`` execution receipt remains useful for the
        logical model call, while these nested receipts make fallback routing
        auditable per provider.  Only hashes and bounded metadata are persisted;
        provider output and prompts never enter the event payload.
        """

        if type(event) is not str or not event.strip():
            raise ValueError("provider attempt fence event must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise TypeError("provider attempt fence payload must be a mapping")
        raw_payload = cast(Mapping[Any, Any], payload)
        if any(type(key) is not str for key in raw_payload):
            raise TypeError("provider attempt fence payload keys must be strings")
        run = self._active_run
        if run is None:
            raise RuntimeError("provider attempt fence is unavailable outside a Lab run")
        typed_payload = cast(Mapping[str, Any], raw_payload)
        normalized_payload: dict[str, Any] = {
            **self._provider_attempt_context,
            **dict(typed_payload),
        }
        if event == "admit":
            return self._admit_provider_attempt(run, normalized_payload)
        if event == "settle":
            self._settle_provider_attempt(run, normalized_payload)
            return None
        raise ValueError("unknown provider attempt fence event")

    def _admit_provider_attempt(self, run: LabRun, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_phase = payload.get("phase", "")
        raw_call_id = payload.get("call_id", "")
        raw_provider = payload.get("provider", "")
        candidate_index = payload.get("candidate_index")
        candidate_count = payload.get("candidate_count", candidate_index)
        gateway_attempt = payload.get("gateway_attempt", 1)
        gateway_max_attempts = payload.get("gateway_max_attempts")
        timeout_seconds = payload.get("timeout_seconds")
        idempotency_key = payload.get("idempotency_key")
        raw_task = payload.get("task", "")
        if (
            type(raw_phase) is not str
            or not raw_phase.strip()
            or raw_phase != raw_phase.strip()
            or raw_phase != raw_phase.lower()
            or type(raw_call_id) is not str
            or not raw_call_id.strip()
            or raw_call_id != raw_call_id.strip()
            or type(raw_provider) is not str
            or not raw_provider.strip()
            or raw_provider != raw_provider.strip()
            or type(candidate_index) is not int
            or candidate_index < 1
            or type(candidate_count) is not int
            or candidate_count < candidate_index
            or candidate_count > run.max_steps + 1
            or candidate_index > run.max_steps + 1
            or type(gateway_attempt) is not int
            or gateway_attempt < 1
            or (
                gateway_max_attempts is not None
                and (
                    type(gateway_max_attempts) is not int
                    or gateway_max_attempts < gateway_attempt
                )
            )
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or float(timeout_seconds) <= 0
            or type(idempotency_key) is not str
            or not _is_digest(idempotency_key)
            or type(raw_task) is not str
            or not raw_task.strip()
        ):
            raise ValueError("provider attempt fence identity/deadline is invalid")
        phase = raw_phase.lower()
        call_id = raw_call_id
        provider = raw_provider
        input_payload = {
            "schema": "aegis-provider-attempt-input-v1",
            "phase": phase,
            "call_id": call_id,
            "provider": provider,
            "gateway_attempt": gateway_attempt,
            "candidate_index": candidate_index,
            "candidate_count": candidate_count,
            "task_hash": _hash(raw_task),
        }
        policy_payload = {
            "schema": "aegis-provider-attempt-policy-v1",
            "phase": phase,
            "trust_level": self.config.trust_level,
            "gateway_attempt": gateway_attempt,
            "candidate_index": candidate_index,
            "timeout_seconds": float(timeout_seconds),
            "idempotency_key": idempotency_key,
            "configured_provider": str(self.config.options.get("provider", "")),
            "configured_fallback_providers_hash": _hash(
                self.config.options.get("fallback_providers", ())
            ),
            "configured_provider_budgets_hash": _hash(
                self.config.options.get("provider_budgets", ())
            ),
        }
        execution_id = "provider-attempt-" + _hash(
            {
                "mission_id": run.mission_id,
                "phase": phase,
                "call_id": call_id,
                "provider": provider,
                "gateway_attempt": gateway_attempt,
                "candidate_index": candidate_index,
            }
        )[:48]
        lease_id = max(1, len(run.tool_execution_admissions) + 1)
        execution_id, admission_id = run.admit_tool_execution(
            tool_name=f"provider.{provider}",
            input_payload=input_payload,
            policy_payload=policy_payload,
            effect_class="model_inference",
            actor_role="actor",
            expected_observation_schema="aegis-provider-attempt-result-v1",
            stop_rule=f"provider_route_{phase}_{call_id}",
            lease_id=lease_id,
            attempt=candidate_index,
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            timeout_seconds=float(timeout_seconds),
        )
        return {
            "tool_name": f"provider.{provider}",
            "execution_id": execution_id,
            "admission_id": admission_id,
            "input_payload": input_payload,
            "policy_payload": policy_payload,
            "effect_class": "model_inference",
            "actor_role": "actor",
            "expected_observation_schema": "aegis-provider-attempt-result-v1",
            "stop_rule": f"provider_route_{phase}_{call_id}",
            "lease_id": lease_id,
            "attempt": candidate_index,
            "idempotency_key": idempotency_key,
            "timeout_seconds": float(timeout_seconds),
        }

    def _settle_provider_attempt(self, run: LabRun, payload: Mapping[str, Any]) -> None:
        fence = payload.get("fence")
        if not isinstance(fence, Mapping):
            raise ValueError("provider attempt settlement fence is missing")
        fence_map = cast(dict[str, Any], fence)
        raw_status = payload.get("status", "")
        raw_provider = payload.get("provider", "")
        raw_phase = payload.get("phase", "")
        raw_call_id = payload.get("call_id", "")
        raw_gateway_attempt = payload.get("gateway_attempt", 1)
        raw_candidate_index = payload.get("candidate_index", 0)
        raw_error = payload.get("error", "provider_failure")
        if (
            type(raw_status) is not str
            or raw_status != raw_status.strip()
            or type(raw_provider) is not str
            or not raw_provider.strip()
            or raw_provider != raw_provider.strip()
            or type(raw_phase) is not str
            or not raw_phase.strip()
            or raw_phase != raw_phase.strip()
            or raw_phase != raw_phase.lower()
            or type(raw_call_id) is not str
            or not raw_call_id.strip()
            or raw_call_id != raw_call_id.strip()
            or type(raw_gateway_attempt) is not int
            or raw_gateway_attempt < 1
            or type(raw_candidate_index) is not int
            or raw_candidate_index < 1
            or (raw_status != "SUCCESS" and (type(raw_error) is not str or not raw_error.strip()))
            or (raw_status == "SUCCESS" and "result" not in payload)
        ):
            raise ValueError("provider attempt settlement metadata is invalid")
        for field in (
            "tool_name",
            "execution_id",
            "admission_id",
            "effect_class",
            "actor_role",
            "expected_observation_schema",
            "stop_rule",
            "idempotency_key",
        ):
            value = fence_map.get(field)
            if type(value) is not str or not value.strip():
                raise ValueError("provider attempt settlement fence metadata is invalid")
        for field in ("lease_id", "attempt"):
            value = fence_map.get(field)
            if type(value) is not int or value < 1:
                raise ValueError("provider attempt settlement fence counters are invalid")
        timeout_seconds = fence_map.get("timeout_seconds")
        if not _is_finite_number(timeout_seconds):
            raise ValueError("provider attempt settlement fence deadline is invalid")
        timeout_value = cast(int | float, timeout_seconds)
        if timeout_value <= 0:
            raise ValueError("provider attempt settlement fence deadline is invalid")
        if not _is_digest(cast(str, fence_map["idempotency_key"])):
            raise ValueError("provider attempt settlement fence idempotency key is invalid")
        status = raw_status
        if status not in {"SUCCESS", "REJECTED", "TIMED_OUT", "CANCELLED"}:
            raise ValueError("provider attempt settlement status is invalid")
        result_payload: dict[str, Any] = {
            "schema": "aegis-provider-attempt-result-v1",
            "provider": raw_provider,
            "phase": raw_phase,
            "gateway_attempt": raw_gateway_attempt,
            "candidate_index": raw_candidate_index,
            "status": status,
        }
        if status == "SUCCESS":
            result_payload["output_hash"] = _hash(payload["result"])
        else:
            result_payload["error"] = raw_error
        run.record_tool_execution(
            tool_name=fence_map["tool_name"],
            execution_id=fence_map["execution_id"],
            admission_id=fence_map["admission_id"],
            input_payload=fence_map["input_payload"],
            policy_payload=fence_map["policy_payload"],
            result=result_payload,
            effect_class=fence_map["effect_class"],
            actor_role=fence_map["actor_role"],
            expected_observation_schema=fence_map["expected_observation_schema"],
            stop_rule=fence_map["stop_rule"],
            lease_id=fence_map["lease_id"],
            attempt=fence_map["attempt"],
            status=status,
            idempotency_key=fence_map["idempotency_key"],
            timeout_seconds=float(timeout_value),
        )
        key = (
            raw_phase.lower(),
            raw_call_id,
            raw_gateway_attempt,
        )
        self._provider_attempt_receipts.setdefault(key, []).append(
            (raw_provider, status)
        )

    def _assert_provider_attempt_fence(
        self,
        run: LabRun,
        *,
        phase: str,
        call_id: str,
        gateway_attempt: int,
        route: Any,
    ) -> None:
        """Require a native-required gateway to account for every provider attempt.

        A compatibility adapter can accept ``provider_attempt_hook`` as a
        constructor keyword and still omit the callback around its real
        provider call.  The outer gateway receipt cannot detect that omission
        by itself.  Native-required mode therefore reconciles the returned
        route with the nested provider receipts before the outer call is
        settled successfully.  Projection/DEV lanes retain their historical
        adapter compatibility and do not impose this stricter handshake.
        """

        if not run.require_native_authority:
            return
        if route is None:
            raise RuntimeError("native Lab authority requires a provider route receipt")
        route_hash = getattr(route, "route_hash", None)
        if type(route_hash) is not str or not _is_digest(route_hash):
            raise RuntimeError("native Lab authority requires a canonical provider route hash")
        raw_attempted = getattr(route, "attempted_providers", None)
        if not isinstance(raw_attempted, (list, tuple)):
            raise RuntimeError("native Lab authority requires typed provider attempts")
        typed_attempted = cast(list[Any] | tuple[Any, ...], raw_attempted)
        if any(
            type(provider) is not str
            or not provider.strip()
            or provider != provider.strip()
            for provider in typed_attempted
        ):
            raise RuntimeError("native Lab authority requires canonical provider names")
        attempted = tuple(typed_attempted)
        if not attempted:
            raise RuntimeError("native Lab authority requires at least one provider attempt")
        selected = getattr(route, "selected_provider", None)
        if (
            type(selected) is not str
            or not selected.strip()
            or selected != selected.strip()
            or selected not in attempted
        ):
            raise RuntimeError("native Lab authority provider selection is inconsistent")
        raw_throttled = getattr(route, "throttled_providers", None)
        if not isinstance(raw_throttled, (list, tuple)):
            raise RuntimeError("native Lab authority requires typed throttled providers")
        typed_throttled = cast(list[Any] | tuple[Any, ...], raw_throttled)
        if any(
            type(provider) is not str
            or not provider.strip()
            or provider != provider.strip()
            for provider in typed_throttled
        ):
            raise RuntimeError("native Lab authority requires canonical throttled providers")
        fallback_used = getattr(route, "fallback_used", None)
        if type(fallback_used) is not bool:
            raise RuntimeError("native Lab authority requires a typed provider fallback flag")
        optional_route_fields = {
            "schema": "aegis-friendly-provider-route-v1",
            "trust_level": run.trust_level,
            "throttled_provider_count": len(typed_throttled),
            "provider_budget_hash": None,
        }
        for field, expected in optional_route_fields.items():
            if not hasattr(route, field):
                continue
            value = getattr(route, field)
            if field == "provider_budget_hash":
                if type(value) is not str or not _is_digest(value):
                    raise RuntimeError("native Lab authority provider budget hash is invalid")
            elif field == "throttled_provider_count":
                if type(value) is not int or value != expected:
                    raise RuntimeError("native Lab authority throttled count is inconsistent")
            elif type(value) is not str or value != expected:
                raise RuntimeError(f"native Lab authority provider route {field} is invalid")

        observed = tuple(
            self._provider_attempt_receipts.get((phase, call_id, gateway_attempt), ())
        )
        if tuple(provider for provider, _ in observed) != attempted:
            raise RuntimeError("native Lab authority provider-attempt receipts are incomplete")
        if any(status not in {"SUCCESS", "REJECTED", "TIMED_OUT", "CANCELLED"} for _, status in observed):
            raise RuntimeError("native Lab authority provider-attempt status is invalid")
        if not any(provider == selected and status == "SUCCESS" for provider, status in observed):
            raise RuntimeError("native Lab authority selected provider has no successful receipt")

    async def _run_search_program(
        self,
        program: SearchProgram,
        executor: Any,
        *,
        task: str,
        run_id: str,
        timeout_seconds: float = 10.0,
    ) -> list[Any]:
        program.validate()
        if not callable(executor):
            raise TypeError("search program executor must be callable")
        if not _is_finite_number(timeout_seconds) or float(timeout_seconds) <= 0:
            raise ValueError("search executor timeout must be finite and positive")
        result = await asyncio.wait_for(
            _call_fenced(executor, program, task=task, run_id=run_id),
            timeout=float(timeout_seconds),
        )
        if type(result) is dict:
            result_map = cast(dict[str, Any], result)
            has_results = "results" in result_map
            has_candidates = "candidates" in result_map
            if has_results and has_candidates and result_map["results"] != result_map["candidates"]:
                raise ValueError("search program executor result aliases disagree")
            if has_results or has_candidates:
                result = result_map.get("results", result_map.get("candidates"))
            elif any(key in result_map for key in ("uri", "url", "content", "body", "snippet")):
                result = [result_map]
            else:
                raise TypeError("search program executor result must contain results or a source record")
        if result is None:
            return []
        if type(result) is str:
            return [result]
        if type(result) not in (list, tuple):
            raise TypeError("search program executor must return a sequence")
        typed_result = cast(list[Any] | tuple[Any, ...], result)
        return list(typed_result)[: program.max_candidates]

    @staticmethod
    def _coerce_experiment_spec(value: Any) -> ExperimentSpec:
        if isinstance(value, ExperimentSpec):
            return value
        if not isinstance(value, dict):
            raise TypeError("experiment spec must be a mapping")
        payload = cast(dict[str, Any], value)
        raw_variables = payload["variables"]
        raw_controls = payload["controls"]
        raw_seeds = payload["preregistered_seeds"]
        if any(
            isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple))
            for raw in (raw_variables, raw_controls, raw_seeds)
        ):
            raise TypeError("experiment variables, controls and seeds must be sequences")
        return ExperimentSpec(
            experiment_id=payload["experiment_id"],
            hypothesis_id=payload["hypothesis_id"],
            design=payload["design"],
            variables=tuple(raw_variables),
            controls=tuple(raw_controls),
            preregistered_seeds=tuple(raw_seeds),
            expected_observations=payload["expected_observations"],
            measurement_unit=payload.get("measurement_unit", "score"),
            uncertainty_required=payload.get("uncertainty_required", False),
            min_clean_replicates=payload.get("min_clean_replicates", 0),
        )

    @staticmethod
    def _coerce_simulation_spec(value: Any) -> SimulationSpec:
        if isinstance(value, SimulationSpec):
            return value
        if not isinstance(value, dict):
            raise TypeError("simulation spec must be a mapping")
        payload = cast(dict[str, Any], value)
        raw_seeds = payload["seeds"]
        if not isinstance(raw_seeds, (list, tuple)):
            raise TypeError("simulation seeds must be a sequence")
        typed_seeds = cast(list[Any] | tuple[Any, ...], raw_seeds)
        raw_constraints = payload.get("constraints", ())
        if not isinstance(raw_constraints, (list, tuple)):
            raise TypeError("simulation constraints must be a sequence")
        constraints: list[PhysicalConstraint] = []
        for raw_constraint in cast(list[Any] | tuple[Any, ...], raw_constraints):
            if not isinstance(raw_constraint, dict):
                raise TypeError("simulation constraint must be a mapping")
            constraint = cast(dict[str, Any], raw_constraint)
            constraints.append(
                PhysicalConstraint(
                    name=constraint["name"],
                    tolerance=constraint["tolerance"],
                    unit=constraint.get("unit", "1"),
                )
            )

        def optional_float(name: str) -> float | None:
            raw = payload.get(name)
            return raw if raw is not None else None

        return SimulationSpec(
            simulation_id=payload["simulation_id"],
            experiment_id=payload["experiment_id"],
            model_hash=payload["model_hash"],
            seeds=tuple(item for item in typed_seeds),
            max_steps=payload.get("max_steps", 10_000),
            constraints=tuple(constraints),
            discrepancy_model=payload.get(
                "discrepancy_model", "y_real = y_sim + delta(x) + epsilon"
            ),
            calibration_hash=payload.get("calibration_hash", ""),
            calibration_observations=payload.get("calibration_observations", 0),
            calibration_rmse=optional_float("calibration_rmse"),
            calibration_tolerance=optional_float("calibration_tolerance"),
            calibration_holdout_hash=payload.get("calibration_holdout_hash", ""),
            numerical_method=payload.get("numerical_method", "deterministic-runner"),
            floating_point_mode=payload.get("floating_point_mode", "IEEE754"),
            convergence_tolerance=optional_float("convergence_tolerance"),
            minimum_convergence_steps=payload.get("minimum_convergence_steps", 0),
        )

    async def _run_skill_requests(self, run: LabRun, options: dict[str, Any]) -> None:
        """Admit and execute explicitly requested skills inside the run fence."""

        raw_requests = options.get("skill_requests")
        if raw_requests is None:
            return
        registry = options.get("skill_registry")
        if not isinstance(registry, SkillRegistry):
            run.record_blocker("skill_registry_missing")
            return
        if not isinstance(raw_requests, (list, tuple)):
            run.record_blocker("skill_requests_not_sequence")
            return
        requests = cast(list[Any] | tuple[Any, ...], raw_requests)
        for raw in list(requests)[: run.max_steps]:
            if not isinstance(raw, dict):
                run.record_blocker("skill_request_invalid")
                continue
            request = cast(dict[str, Any], raw)
            admission: SkillAdmission | None = None
            input_payload = request.get("input")
            try:
                skill_id = request["skill_id"]
                version = request["version"]
                if type(skill_id) is not str or type(version) is not str:
                    raise SkillAdmissionError("skill identity and version must be strings")
                capabilities = _strict_string_sequence(
                    request.get("available_capabilities", ()),
                    label="skill capabilities",
                )
                preconditions_raw = request.get("preconditions", {})
                if not isinstance(preconditions_raw, dict):
                    raise SkillAdmissionError("skill preconditions must be a mapping")
                typed_preconditions = cast(dict[Any, Any], preconditions_raw)
                if any(
                    type(name) is not str or type(value) is not bool
                    for name, value in typed_preconditions.items()
                ):
                    raise SkillAdmissionError("skill preconditions must use string/boolean values")
                preconditions = cast(dict[str, bool], dict(typed_preconditions))
                configured_executor = request.get("executor")
                registered_executor = self._resolve_execution_cell(
                    run,
                    action_kind="skill_execution",
                    options=options,
                    cell_id=request.get("cell_id"),
                )
                if self._execution_cells_strict:
                    if registered_executor is None:
                        raise SkillAdmissionError("skill execution cell is not registered")
                    executor = registered_executor
                else:
                    executor = registered_executor or configured_executor
                if not callable(executor):
                    raise TypeError("skill request executor must be callable")
                admission = run.admit_skill(
                    registry,
                    skill_id,
                    version,
                    available_capabilities=capabilities,
                    preconditions=preconditions,
                )
                _, receipt = await registry.execute(
                    admission,
                    executor,
                    mission_id=run.mission_id,
                    replay_parent_hash=admission.admission_event_hash,
                    input_payload=input_payload,
                )
                run.record_skill_execution(receipt)
            except asyncio.CancelledError:
                if admission is not None:
                    try:
                        manifest = registry.get(admission.skill_id, admission.version)
                        cancelled_result = {"error": "CancelledError"}
                        input_hash = _hash(input_payload)
                        result_hash = _hash(cancelled_result)
                        artifact_hash = _hash(
                            {
                                "schema": "aegis-skill-artifact-v1",
                                "skill_id": manifest.skill_id,
                                "version": manifest.version,
                                "result_hash": result_hash,
                            }
                        )
                        replay_parent_hash = admission.admission_event_hash
                        execution_without_hash = {
                            "schema": "aegis-skill-execution-v1",
                            "skill_id": manifest.skill_id,
                            "version": manifest.version,
                            "admission_hash": admission.admission_hash,
                            "mission_id": run.mission_id,
                            "replay_parent_hash": replay_parent_hash,
                            "input_hash": input_hash,
                            "result_hash": result_hash,
                            "artifact_hash": artifact_hash,
                            "validator_version": manifest.validator_version,
                            "status": "CANCELLED",
                        }
                        run.record_skill_execution(
                            SkillExecutionReceipt(
                                skill_id=manifest.skill_id,
                                version=manifest.version,
                                admission_hash=admission.admission_hash,
                                mission_id=run.mission_id,
                                replay_parent_hash=replay_parent_hash,
                                input_hash=input_hash,
                                result_hash=result_hash,
                                artifact_hash=artifact_hash,
                                validator_version=manifest.validator_version,
                                status="CANCELLED",
                                execution_hash=_hash(execution_without_hash),
                            )
                        )
                    except (KeyError, TypeError, ValueError, RuntimeError) as settlement_exc:
                        run.record_blocker(f"skill_settlement_failed:{type(settlement_exc).__name__}")
                raise
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                run.record_blocker(f"skill_execution_failed:{type(exc).__name__}")

    async def _run_tool_calls(self, run: LabRun, options: dict[str, Any]) -> None:
        """Execute explicitly declared generic tools behind admit/settle fences."""

        raw_calls = options.get("tool_calls")
        if raw_calls is None:
            return
        if not isinstance(raw_calls, (list, tuple)):
            run.record_blocker("tool_calls_not_sequence")
            return
        try:
            max_attempts = _bounded_retry_attempts(
                options.get("tool_max_attempts", 1),
                max_steps=self.config.max_steps,
                label="tool",
            )
        except ValueError:
            run.record_blocker("tool_retry_policy_invalid")
            return
        raw_timeout = options.get("tool_timeout_seconds", 30.0)
        if not _is_finite_number(raw_timeout) or float(raw_timeout) <= 0:
            run.record_blocker("tool_timeout_policy_invalid")
            return
        timeout_seconds = float(raw_timeout)
        allow_external_writes = options.get("lab_allow_external_writes", False)
        if type(allow_external_writes) is not bool:
            run.record_blocker("tool_effect_policy_invalid")
            return
        typed_calls = cast(list[Any] | tuple[Any, ...], raw_calls)
        for index, raw_call in enumerate(list(typed_calls)[: run.max_steps], start=1):
            if not isinstance(raw_call, dict):
                run.record_blocker("tool_call_invalid")
                continue
            request = cast(dict[str, Any], raw_call)
            raw_tool_name = request.get("tool_name", request.get("name", ""))
            raw_call_id = request.get("call_id", f"call-{index}")
            raw_effect_class = request.get("effect_class", "read_only")
            raw_actor_role = request.get("actor_role", "actor")
            raw_expected_schema = request.get("expected_observation_schema", "opaque")
            raw_stop_rule = request.get("stop_rule", "single_call")
            lease_id = request.get("lease_id", 1)
            if any(
                type(value) is not str
                for value in (
                    raw_tool_name,
                    raw_call_id,
                    raw_effect_class,
                    raw_actor_role,
                    raw_expected_schema,
                    raw_stop_rule,
                )
            ) or type(lease_id) is not int:
                run.record_blocker("tool_call_invalid")
                continue
            tool_name = raw_tool_name.strip()
            call_id = raw_call_id.strip()
            effect_class = raw_effect_class.strip()
            actor_role = raw_actor_role.strip().lower()
            expected_schema = raw_expected_schema.strip()
            stop_rule = raw_stop_rule.strip()
            if lease_id < 1:
                run.record_blocker("tool_lease_invalid")
                continue
            if (
                effect_class not in _CONTROLLER_SAFE_TOOL_EFFECTS
                and not allow_external_writes
            ):
                run.record_blocker("tool_external_effect_not_approved")
                continue
            runner = self._resolve_execution_cell(
                run,
                action_kind="tool_call",
                options=options,
                cell_id=request.get("cell_id"),
                capability=(
                    "network_read"
                    if effect_class == "network_read"
                    else effect_class
                    if effect_class in {"read_only", "model_inference"}
                    else "compute"
                ),
                effect_class=effect_class,
            )
            if not callable(runner):
                run.record_blocker("tool_runner_missing")
                continue
            input_payload = request.get("input", request.get("arguments", {}))
            policy_payload = request.get(
                "policy",
                {
                    "schema": "aegis-tool-execution-policy-v1",
                    "effect_class": effect_class,
                    "actor_role": actor_role,
                },
            )
            idempotency_key_base = _hash(
                {
                    "schema": "aegis-tool-idempotency-key-v1",
                    "mission_id": run.mission_id,
                    "call_id": call_id,
                    "input_hash": _hash(input_payload),
                    "policy_hash": _hash(policy_payload),
                }
            )
            for attempt in range(1, max_attempts + 1):
                execution_id = f"{call_id}-attempt-{attempt}"
                idempotency_key = _hash(
                    {
                        "base": idempotency_key_base,
                        "execution_id": execution_id,
                    }
                )
                admission_id = ""
                try:
                    execution_id, admission_id = run.admit_tool_execution(
                        tool_name=tool_name,
                        input_payload=input_payload,
                        policy_payload=policy_payload,
                        effect_class=effect_class,
                        actor_role=actor_role,
                        expected_observation_schema=expected_schema,
                        stop_rule=stop_rule,
                        lease_id=lease_id,
                        attempt=attempt,
                        execution_id=execution_id,
                        idempotency_key=idempotency_key,
                        timeout_seconds=timeout_seconds,
                    )
                except (RuntimeError, TypeError, ValueError) as exc:
                    run.record_blocker(f"tool_admission_failed:{type(exc).__name__}")
                    break
                try:
                    result = await asyncio.wait_for(
                        _call_fenced(
                            runner,
                            request,
                            task=self.config.task,
                            run=run,
                            attempt=attempt,
                        ),
                        timeout=timeout_seconds,
                    )
                except asyncio.CancelledError:
                    try:
                        run.record_tool_execution(
                            tool_name=tool_name,
                            execution_id=execution_id,
                            admission_id=admission_id,
                            input_payload=input_payload,
                            policy_payload=policy_payload,
                            result={"error": "CancelledError"},
                            effect_class=effect_class,
                            actor_role=actor_role,
                            expected_observation_schema=expected_schema,
                            stop_rule=stop_rule,
                            lease_id=lease_id,
                            attempt=attempt,
                            status="CANCELLED",
                            idempotency_key=idempotency_key,
                            timeout_seconds=timeout_seconds,
                        )
                    except (RuntimeError, TypeError, ValueError) as settlement_exc:
                        run.record_blocker(f"tool_settlement_failed:{type(settlement_exc).__name__}")
                    raise
                except Exception as exc:
                    status = "TIMED_OUT" if isinstance(exc, TimeoutError) else "REJECTED"
                    try:
                        run.record_tool_execution(
                            tool_name=tool_name,
                            execution_id=execution_id,
                            admission_id=admission_id,
                            input_payload=input_payload,
                            policy_payload=policy_payload,
                            result={"error": type(exc).__name__},
                            effect_class=effect_class,
                            actor_role=actor_role,
                            expected_observation_schema=expected_schema,
                            stop_rule=stop_rule,
                            lease_id=lease_id,
                            attempt=attempt,
                            status=status,
                            idempotency_key=idempotency_key,
                            timeout_seconds=timeout_seconds,
                        )
                    except (RuntimeError, TypeError, ValueError) as settlement_exc:
                        run.record_blocker(f"tool_settlement_failed:{type(settlement_exc).__name__}")
                        break
                    if attempt == max_attempts:
                        run.record_blocker(f"tool_execution_failed:{type(exc).__name__}")
                    continue
                try:
                    run.record_tool_execution(
                        tool_name=tool_name,
                        execution_id=execution_id,
                        admission_id=admission_id,
                        input_payload=input_payload,
                        policy_payload=policy_payload,
                        result=result,
                        effect_class=effect_class,
                        actor_role=actor_role,
                        expected_observation_schema=expected_schema,
                        stop_rule=stop_rule,
                        lease_id=lease_id,
                        attempt=attempt,
                        status="SUCCESS",
                        idempotency_key=idempotency_key,
                        timeout_seconds=timeout_seconds,
                    )
                except (RuntimeError, TypeError, ValueError) as exc:
                    run.record_blocker(f"tool_settlement_failed:{type(exc).__name__}")
                break

    @staticmethod
    def _decode_controller_action_plan(output: Any) -> tuple[dict[str, Any], ...] | None:
        """Decode an optional typed action plan without executing model text.

        Plain-language or unstructured model output is intentionally ignored.
        A payload that declares ``actions`` must carry the exact schema marker
        and a bounded list of mappings, otherwise the caller records a blocker.
        """

        payload: Any = output
        if isinstance(output, str):
            candidate = output.strip()
            if not candidate.startswith("{"):
                return None
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise ValueError("controller action plan is not valid JSON") from exc
        if not isinstance(payload, dict) or "actions" not in payload:
            return None
        mapping = cast(dict[str, Any], payload)
        if mapping.get("schema") != _CONTROLLER_ACTION_PLAN_SCHEMA:
            raise ValueError("controller action plan schema is invalid")
        raw_actions = mapping.get("actions")
        if not isinstance(raw_actions, (list, tuple)):
            raise ValueError("controller action plan actions must be a sequence")
        actions: list[dict[str, Any]] = []
        typed_actions = cast(list[Any] | tuple[Any, ...], raw_actions)
        for raw_action in typed_actions:
            if not isinstance(raw_action, dict):
                raise ValueError("controller action plan item must be a mapping")
            actions.append(cast(dict[str, Any], raw_action))
        return tuple(actions)

    async def _execute_controller_action_plan(
        self,
        run: LabRun,
        output: Any,
        options: dict[str, Any],
        *,
        run_id: str,
        browser_cell: BrowserCell | None = None,
        browser_session: Any | None = None,
        action_kinds: set[str] | None = None,
    ) -> set[str]:
        """Execute only typed, configured actions returned by the controller.

        This is deliberately an adapter boundary: the model can propose a
        search program, browser action, experiment-cell invocation, or generic
        tool request, but it cannot
        provide a Python callable, widen policy, or bypass the native LabRun
        reducer.
        """

        executed_experiment_ids: set[str] = set()

        try:
            actions = self._decode_controller_action_plan(output)
        except ValueError as exc:
            run.record_blocker(f"controller_action_plan_invalid:{type(exc).__name__}")
            return executed_experiment_ids
        if actions is None:
            return executed_experiment_ids
        if len(actions) > run.max_steps:
            run.record_blocker("controller_action_plan_exceeds_step_quota")
            return executed_experiment_ids
        for raw_action in actions:
            raw_kind = raw_action.get("kind", "")
            if type(raw_kind) is not str:
                run.record_blocker("controller_action_kind_invalid")
                continue
            kind = raw_kind.strip().lower()
            if kind not in _CONTROLLER_ACTION_KINDS:
                run.record_blocker("controller_action_kind_unsupported")
                continue
            if action_kinds is not None and kind not in action_kinds:
                continue
            if kind == "tool_call":
                request = raw_action.get("request", raw_action.get("call"))
                if not isinstance(request, dict):
                    run.record_blocker("controller_tool_call_invalid")
                    continue
                request_map = cast(dict[str, Any], request)
                raw_effect_class = request_map.get("effect_class", "read_only")
                if type(raw_effect_class) is not str:
                    run.record_blocker("controller_tool_effect_invalid")
                    continue
                effect_class = raw_effect_class.strip()
                if (
                    effect_class not in _CONTROLLER_SAFE_TOOL_EFFECTS
                    and options.get("lab_allow_external_writes", False) is not True
                ):
                    run.record_blocker("controller_tool_effect_not_approved")
                    continue
                tool_options = dict(options)
                tool_options["tool_calls"] = [request_map]
                if "max_attempts" in raw_action:
                    tool_options["tool_max_attempts"] = raw_action["max_attempts"]
                await self._run_tool_calls(run, tool_options)
                continue

            if kind == "experiment_action":
                raw_experiment_id = raw_action.get("experiment_id", raw_action.get("id", ""))
                if type(raw_experiment_id) is not str:
                    run.record_blocker("controller_experiment_invalid:TypeError")
                    continue
                experiment_id = raw_experiment_id.strip()
                raw_spec = run.experiments.get(experiment_id) if experiment_id else None
                if raw_spec is None:
                    candidate = raw_action.get("experiment_spec")
                    if candidate is None:
                        candidate = options.get("experiment_spec")
                    try:
                        raw_spec = self._coerce_experiment_spec(candidate)
                        if experiment_id and raw_spec.experiment_id != experiment_id:
                            raise ValueError("controller experiment id does not match spec")
                        if raw_spec.experiment_id not in run.experiments:
                            run.add_experiment(raw_spec)
                    except (KeyError, TypeError, ValueError) as exc:
                        run.record_blocker(f"controller_experiment_invalid:{type(exc).__name__}")
                        continue
                runner = self._resolve_execution_cell(
                    run,
                    action_kind="experiment_action",
                    options=options,
                    cell_id=raw_action.get("cell_id"),
                    capability="compute",
                    effect_class="compute",
                )
                if not callable(runner):
                    run.record_blocker("controller_experiment_runner_missing")
                    continue
                executed_experiment_ids.add(raw_spec.experiment_id)
                try:
                    await self._run_admitted_experiment(
                        run,
                        raw_spec,
                        runner,
                        options,
                        run_id=run_id,
                        action=raw_action,
                    )
                except asyncio.CancelledError:
                    raise
                continue

            if kind == "simulation_action":
                simulation: SimulationSpec | None = None
                try:
                    candidate_simulation = raw_action.get("simulation_spec")
                    if candidate_simulation is None:
                        candidate_simulation = options.get("simulation_spec")
                    simulation = self._coerce_simulation_spec(candidate_simulation)
                    raw_experiment_id = raw_action.get("experiment_id", simulation.experiment_id)
                    if type(raw_experiment_id) is not str:
                        raise TypeError("controller simulation experiment id must be a string")
                    experiment_id = raw_experiment_id.strip()
                    if experiment_id != simulation.experiment_id:
                        raise ValueError("controller simulation experiment id does not match spec")
                    experiment = run.experiments.get(experiment_id)
                    if experiment is None:
                        candidate_experiment = raw_action.get("experiment_spec")
                        if candidate_experiment is None:
                            candidate_experiment = options.get("experiment_spec")
                        experiment = self._coerce_experiment_spec(candidate_experiment)
                        if experiment.experiment_id != experiment_id:
                            raise ValueError("controller simulation experiment is not registered")
                        run.add_experiment(experiment)
                    if experiment.experiment_id != simulation.experiment_id:
                        raise ValueError("controller simulation binding mismatch")
                except (KeyError, TypeError, ValueError) as exc:
                    run.record_blocker(f"controller_simulation_invalid:{type(exc).__name__}")
                    continue
                runner = self._resolve_execution_cell(
                    run,
                    action_kind="simulation_action",
                    options=options,
                    cell_id=raw_action.get("cell_id"),
                    capability="compute",
                    effect_class="compute",
                )
                if not callable(runner):
                    run.record_blocker("controller_simulation_runner_missing")
                    continue
                executed_experiment_ids.add(experiment.experiment_id)
                try:
                    await self._run_admitted_simulation(
                        run,
                        experiment,
                        simulation,
                        runner,
                        options,
                        run_id=run_id,
                        action=raw_action,
                    )
                except asyncio.CancelledError:
                    raise
                continue

            if kind == "browser_action":
                action = raw_action.get("action")
                if (
                    browser_cell is None
                    or browser_session is None
                    or browser_cell.session is not browser_session
                    or not isinstance(action, dict)
                ):
                    run.record_blocker("controller_browser_action_requires_bound_session")
                    continue
                action_map = cast(dict[str, Any], action)
                raw_action_kind = action_map.get("kind", "")
                if type(raw_action_kind) is not str:
                    run.record_blocker("controller_browser_action_invalid")
                    continue
                action_kind = raw_action_kind.strip().lower()
                action_id = (
                    f"lease-{max(1, browser_cell.lease_id)}-"
                    f"action-{browser_cell.action_count + 1}"
                )
                admission_id = ""
                counted = False
                try:
                    await browser_cell.admit(browser_session)
                    admission_id = run.admit_browser_action(
                        action_kind=action_kind,
                        action=action_map,
                        policy=browser_cell.policy,
                        lease_id=max(1, browser_cell.lease_id),
                        action_id=action_id,
                    )
                    runtime_adapter = self._resolve_execution_cell(
                        run,
                        action_kind="browser_action",
                        options=options,
                        cell_id=action_map.get("cell_id"),
                        capability="network_read",
                        effect_class="network_read",
                    )
                    if runtime_adapter is None and self._execution_cells_strict:
                        run.record_blocker("execution_cell_not_registered:browser_action")
                        raise RuntimeError("browser execution cell is not registered")
                    if runtime_adapter is None and action_map.get("cell_id") is None:
                        runtime_adapter = options.get("browser_runtime_adapter")
                    if runtime_adapter is not None and hasattr(runtime_adapter, "capture_action"):
                        capture = await _call_fenced(
                            runtime_adapter.capture_action,
                            browser_session=browser_session,
                            run_id=int(run_id[:16], 16),
                            action_id=browser_cell.action_count + 1,
                            sequence_number=browser_cell.action_count + 1,
                            action=_runtime_browser_action(browser_session, action_map, browser_cell),
                        )
                    else:
                        capture = await _call_fenced(
                            self._gateway(self.config.task).capture_browser_action,
                            browser_session=browser_session,
                            action=_gateway_browser_action(action_map, browser_cell),
                            run_id=int(run_id[:16], 16),
                        )
                    browser_cell.record()
                    counted = True
                    run.record_browser_action(
                        action_id=action_id,
                        admission_id=admission_id,
                        action_kind=action_kind,
                        action=action_map,
                        result={
                            "capture_result_hash": str(
                                getattr(capture, "browser_action_result_hash", "")
                            )
                        },
                        policy=browser_cell.policy,
                        lease_id=max(1, browser_cell.lease_id),
                    )
                    run.resolve_blocker(
                        "browser_session_or_actions_missing", detail="controller_action_plan"
                    )
                except asyncio.CancelledError:
                    if admission_id:
                        if not counted:
                            # An admitted actor action may have reached the
                            # external session before cancellation.  Consume
                            # the quota just as the rejection path does so a
                            # relaunch cannot turn an unknown side effect into
                            # an unbounded retry stream.
                            browser_cell.record()
                        run.record_browser_action(
                            action_id=action_id,
                            admission_id=admission_id,
                            action_kind=action_kind,
                            action=action_map,
                            result={"error": "CancelledError"},
                            policy=browser_cell.policy,
                            lease_id=max(1, browser_cell.lease_id),
                            status="CANCELLED",
                        )
                    raise
                except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
                    if admission_id:
                        if not counted:
                            browser_cell.record()
                        run.record_browser_action(
                            action_id=action_id,
                            admission_id=admission_id,
                            action_kind=action_kind,
                            action=action_map,
                            result={"error": type(exc).__name__},
                            policy=browser_cell.policy,
                            lease_id=max(1, browser_cell.lease_id),
                            status="REJECTED",
                        )
                    run.record_blocker(f"controller_browser_action_failed:{type(exc).__name__}")
                continue

            raw_program = raw_action.get("program")
            try:
                if isinstance(raw_program, SearchProgram):
                    program = raw_program
                elif isinstance(raw_program, dict):
                    program_data = cast(dict[str, Any], raw_program)
                    operations = program_data.get("operations")
                    if not isinstance(operations, (list, tuple)):
                        raise TypeError("controller search program operations must be a sequence")
                    max_candidates = program_data.get("max_candidates", 20)
                    provider = program_data.get("provider", "unspecified")
                    freshness_max_age = program_data.get("freshness_max_age_seconds")
                    contradiction_clusters = program_data.get(
                        "min_independent_contradiction_clusters", 0
                    )
                    if (
                        type(max_candidates) is not int
                        or type(provider) is not str
                        or (freshness_max_age is not None and type(freshness_max_age) is not int)
                        or type(contradiction_clusters) is not int
                    ):
                        raise TypeError("controller search program metadata types are invalid")
                    program = SearchProgram.from_mappings(
                        tuple(cast(list[dict[str, Any]], operations)),
                        max_candidates=max_candidates,
                        allowed_hosts=_strict_string_sequence(
                            program_data.get("allowed_hosts", ()),
                            label="controller search program allowlist",
                        ),
                        provider=provider,
                        freshness_max_age_seconds=freshness_max_age,
                        min_independent_contradiction_clusters=contradiction_clusters,
                    )
                else:
                    raise TypeError("controller search program must be a mapping")
                executor = self._resolve_execution_cell(
                    run,
                    action_kind="search_program",
                    options=options,
                    cell_id=raw_action.get("cell_id"),
                    capability="network_read",
                    effect_class="network_read",
                )
                if raw_action.get("cell_id") is not None and executor is None:
                    continue
                if executor is None and self._execution_cells_strict:
                    run.record_blocker("execution_cell_not_registered:search_program")
                    continue
                if executor is None:
                    executor = options.get("search_program_executor")
                if executor is None:
                    executor = options.get("researcher") or options.get("search_as_code")
                raw_search_timeout = options.get("search_timeout_seconds", 10.0)
                if not _is_finite_number(raw_search_timeout) or float(raw_search_timeout) <= 0:
                    raise TypeError("controller search executor timeout must be finite and positive")
                search_timeout_seconds = float(raw_search_timeout)
                if executor is None:
                    raw_search_max_bytes = options.get("search_max_bytes", 8 * 1024 * 1024)
                    if type(raw_search_max_bytes) is not int:
                        raise TypeError("controller search executor metadata types are invalid")
                    executor = SearchProgramExecutor(
                        query_provider=options.get("search_query_provider"),
                        timeout_seconds=search_timeout_seconds,
                        max_bytes=raw_search_max_bytes,
                    )
                if not callable(executor):
                    raise TypeError("controller search program executor is missing")
                admission_id = run.admit_research_program(
                    program_hash=program.program_hash,
                    operation_count=len(program.operations),
                    provider=program.provider,
                )
            except (TypeError, ValueError, RuntimeError) as exc:
                run.record_blocker(f"controller_search_program_invalid:{type(exc).__name__}")
                continue
            try:
                results = await self._run_search_program(
                    program,
                    executor,
                    task=self.config.task,
                    run_id=run_id,
                    timeout_seconds=search_timeout_seconds,
                )
            except asyncio.CancelledError:
                run.record_research_program(
                    program_hash=program.program_hash,
                    operation_count=len(program.operations),
                    provider=program.provider,
                    candidate_count=0,
                    admission_id=admission_id,
                    status="CANCELLED",
                )
                raise
            except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
                run.record_research_program(
                    program_hash=program.program_hash,
                    operation_count=len(program.operations),
                    provider=program.provider,
                    candidate_count=0,
                    admission_id=admission_id,
                    status="REJECTED",
                )
                run.record_blocker(f"controller_search_failed:{type(exc).__name__}")
                continue
            run.record_research_program(
                program_hash=program.program_hash,
                operation_count=len(program.operations),
                provider=program.provider,
                candidate_count=len(results),
                admission_id=admission_id,
            )
            self._ingest_search_candidates(run, results)
            run.resolve_blocker("search_as_code_hook_missing", detail="controller_action_plan")
        return executed_experiment_ids

    async def _run_admitted_experiment(
        self,
        run: LabRun,
        spec: ExperimentSpec,
        runner: Any,
        options: dict[str, Any],
        *,
        run_id: str,
        action: dict[str, Any] | None = None,
    ) -> None:
        """Run a configured experiment cell behind one native retry fence.

        A controller action can select a registered experiment, but it can
        never supply an executable callable.  The edge runner is injected by
        the operator/configuration boundary and every attempt is admitted and
        settled before observations are ingested.
        """

        try:
            if action is not None and "max_attempts" in action:
                requested_attempts: Any = action["max_attempts"]
            else:
                requested_attempts = options.get("experiment_max_attempts", 1)
            if requested_attempts is None:
                raise TypeError("experiment retry count must be an integer")
            max_attempts = _bounded_retry_attempts(
                requested_attempts,
                max_steps=run.max_steps,
                label="experiment",
            )
        except (TypeError, ValueError, KeyError) as exc:
            run.record_blocker(f"experiment_retry_policy_invalid:{type(exc).__name__}")
            return
        raw_timeout = options.get("experiment_timeout_seconds", 30.0)
        if not _is_finite_number(raw_timeout) or float(raw_timeout) <= 0:
            run.record_blocker("experiment_timeout_policy_invalid")
            return
        timeout_seconds = float(raw_timeout)

        controller_action_hash = _hash(action) if action is not None else ""
        for attempt in range(1, max_attempts + 1):
            input_payload: dict[str, Any] = {
                "schema": "aegis-experiment-execution-input-v1",
                "experiment": asdict(spec),
                "attempt": attempt,
            }
            if controller_action_hash:
                input_payload["controller_action_hash"] = controller_action_hash
            policy_payload: dict[str, Any] = {
                "schema": "aegis-experiment-execution-policy-v1",
                "cell": "python-experiment-adapter",
                "controller_selected": bool(action is not None),
                "timeout_seconds": timeout_seconds,
            }
            execution_id = f"experiment-{spec.experiment_id}-attempt-{attempt}"
            idempotency_key = _hash(
                {
                    "schema": "aegis-experiment-idempotency-key-v1",
                    "mission_id": run.mission_id,
                    "execution_id": execution_id,
                    "input_hash": _hash(input_payload),
                    "policy_hash": _hash(policy_payload),
                }
            )
            policy_payload["idempotency_key"] = idempotency_key
            admission_id = ""
            try:
                execution_id, admission_id = run.admit_experiment_execution(
                    experiment_id=spec.experiment_id,
                    attempt=attempt,
                    input_payload=input_payload,
                    policy_payload=policy_payload,
                    execution_id=execution_id,
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"experiment_admission_failed:{type(exc).__name__}")
                return
            try:
                results = await asyncio.wait_for(
                    _call_fenced(runner, spec, run=run),
                    timeout=timeout_seconds,
                )
                observation_count = _observation_count(results)
            except asyncio.CancelledError:
                try:
                    run.record_experiment_execution(
                        experiment_id=spec.experiment_id,
                        attempt=attempt,
                        execution_id=execution_id,
                        admission_id=admission_id,
                        input_payload=input_payload,
                        policy_payload=policy_payload,
                        result={"error": "CancelledError"},
                        observation_count=0,
                        status="CANCELLED",
                        idempotency_key=idempotency_key,
                        timeout_seconds=timeout_seconds,
                    )
                except (RuntimeError, TypeError, ValueError) as exc:
                    run.record_blocker(f"experiment_settlement_failed:{type(exc).__name__}")
                raise
            except (OSError, TimeoutError, RuntimeError, TypeError, ValueError, KeyError) as exc:
                try:
                    run.record_experiment_execution(
                        experiment_id=spec.experiment_id,
                        attempt=attempt,
                        execution_id=execution_id,
                        admission_id=admission_id,
                        input_payload=input_payload,
                        policy_payload=policy_payload,
                        result={"error": type(exc).__name__},
                        observation_count=0,
                        status="TIMED_OUT" if isinstance(exc, TimeoutError) else "REJECTED",
                        idempotency_key=idempotency_key,
                        timeout_seconds=timeout_seconds,
                    )
                except (RuntimeError, TypeError, ValueError) as settlement_exc:
                    run.record_blocker(f"experiment_settlement_failed:{type(settlement_exc).__name__}")
                    return
                if attempt == max_attempts:
                    run.record_blocker(f"experiment_failed:{type(exc).__name__}")
                continue
            try:
                run.record_experiment_execution(
                    experiment_id=spec.experiment_id,
                    attempt=attempt,
                    execution_id=execution_id,
                    admission_id=admission_id,
                    input_payload=input_payload,
                    policy_payload=policy_payload,
                    result=results,
                    observation_count=observation_count,
                    status="SUCCESS",
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"experiment_settlement_failed:{type(exc).__name__}")
                return
            try:
                self._ingest_observations(run, spec, results, run_id=run_id)
            except (TypeError, ValueError, KeyError) as exc:
                run.record_blocker(f"experiment_observation_ingest_failed:{type(exc).__name__}")
            return

    async def _run_admitted_simulation(
        self,
        run: LabRun,
        experiment: ExperimentSpec,
        simulation: SimulationSpec,
        runner: Any,
        options: dict[str, Any],
        *,
        run_id: str,
        action: dict[str, Any] | None = None,
    ) -> None:
        """Run a bounded simulation cell with explicit numerical evidence."""

        try:
            requested_attempts = (
                action["max_attempts"]
                if action is not None and "max_attempts" in action
                else options.get("simulation_max_attempts", 1)
            )
            if requested_attempts is None:
                raise TypeError("simulation retry count must be an integer")
            max_attempts = _bounded_retry_attempts(
                requested_attempts,
                max_steps=run.max_steps,
                label="simulation",
            )
            cell_max_steps = options.get("simulation_cell_max_steps", 10_000)
            if type(cell_max_steps) is not int or cell_max_steps < 1:
                raise TypeError("simulation cell step quota must be a positive integer")
        except (TypeError, ValueError, KeyError) as exc:
            run.record_blocker(f"simulation_retry_policy_invalid:{type(exc).__name__}")
            return
        raw_timeout = options.get("simulation_timeout_seconds", 30.0)
        if not _is_finite_number(raw_timeout) or float(raw_timeout) <= 0:
            run.record_blocker("simulation_timeout_policy_invalid")
            return
        timeout_seconds = float(raw_timeout)

        controller_action_hash = _hash(action) if action is not None else ""
        for attempt in range(1, max_attempts + 1):
            input_payload: dict[str, Any] = {
                "schema": "aegis-experiment-execution-input-v1",
                "simulation": asdict(simulation),
                "experiment": asdict(experiment),
                "attempt": attempt,
            }
            if controller_action_hash:
                input_payload["controller_action_hash"] = controller_action_hash
            policy_payload: dict[str, Any] = {
                "schema": "aegis-experiment-execution-policy-v1",
                "cell": "simulation-cell",
                "controller_selected": bool(action is not None),
                "timeout_seconds": timeout_seconds,
            }
            execution_id = f"simulation-{simulation.simulation_id}-attempt-{attempt}"
            idempotency_key = _hash(
                {
                    "schema": "aegis-simulation-idempotency-key-v1",
                    "mission_id": run.mission_id,
                    "execution_id": execution_id,
                    "input_hash": _hash(input_payload),
                    "policy_hash": _hash(policy_payload),
                }
            )
            policy_payload["idempotency_key"] = idempotency_key
            admission_id = ""
            try:
                execution_id, admission_id = run.admit_experiment_execution(
                    experiment_id=experiment.experiment_id,
                    attempt=attempt,
                    input_payload=input_payload,
                    policy_payload=policy_payload,
                    execution_id=execution_id,
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"simulation_admission_failed:{type(exc).__name__}")
                return
            try:
                simulation_results = await asyncio.wait_for(
                    SimulationCell(max_steps=cell_max_steps).run(simulation, runner),
                    timeout=timeout_seconds,
                )
                observation_count = _observation_count(simulation_results)
            except asyncio.CancelledError:
                try:
                    run.record_experiment_execution(
                        experiment_id=experiment.experiment_id,
                        attempt=attempt,
                        execution_id=execution_id,
                        admission_id=admission_id,
                        input_payload=input_payload,
                        policy_payload=policy_payload,
                        result={"error": "CancelledError"},
                        observation_count=0,
                        status="CANCELLED",
                        idempotency_key=idempotency_key,
                        timeout_seconds=timeout_seconds,
                    )
                except (RuntimeError, TypeError, ValueError) as exc:
                    run.record_blocker(f"simulation_settlement_failed:{type(exc).__name__}")
                raise
            except (OSError, TimeoutError, RuntimeError, TypeError, ValueError, KeyError) as exc:
                try:
                    run.record_experiment_execution(
                        experiment_id=experiment.experiment_id,
                        attempt=attempt,
                        execution_id=execution_id,
                        admission_id=admission_id,
                        input_payload=input_payload,
                        policy_payload=policy_payload,
                        result={"error": type(exc).__name__},
                        observation_count=0,
                        status="TIMED_OUT" if isinstance(exc, TimeoutError) else "REJECTED",
                        idempotency_key=idempotency_key,
                        timeout_seconds=timeout_seconds,
                    )
                except (RuntimeError, TypeError, ValueError) as settlement_exc:
                    run.record_blocker(f"simulation_settlement_failed:{type(settlement_exc).__name__}")
                    return
                if attempt == max_attempts:
                    run.record_blocker(f"simulation_failed:{type(exc).__name__}")
                continue
            try:
                run.record_experiment_execution(
                    experiment_id=experiment.experiment_id,
                    attempt=attempt,
                    execution_id=execution_id,
                    admission_id=admission_id,
                    input_payload=input_payload,
                    policy_payload=policy_payload,
                    result=simulation_results,
                    observation_count=observation_count,
                    status="SUCCESS",
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_seconds,
                )
                self._ingest_observations(run, experiment, simulation_results, run_id=run_id)
            except (TypeError, ValueError, KeyError, RuntimeError) as exc:
                run.record_blocker(f"simulation_failed:{type(exc).__name__}")
            return

    async def _run_admitted_gateway(
        self,
        run: LabRun,
        *,
        task: str,
        system_context: str,
        phase: str,
        call_id: str,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> Any:
        """Run one model/gateway call behind the native tool fence.

        Gateway calls are external effects from the Lab controller's point of
        view: they consume provider quota, may timeout, and can be cancelled.
        The prompt itself is never persisted in the event payload; only its
        content hash is bound to the admission and settlement receipts.
        """

        normalized_phase = phase.strip().lower()
        normalized_call_id = call_id.strip()
        if not normalized_phase or not normalized_call_id:
            raise ValueError("gateway execution phase and identity are required")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("gateway execution attempt must be positive")
        if type(max_attempts) is not int or max_attempts < attempt:
            raise ValueError("gateway retry policy must allow the current attempt")
        max_attempts = min(max_attempts, self.config.max_steps)
        raw_timeout = self.config.options.get("gateway_timeout_seconds", 60.0)
        if not _is_finite_number(raw_timeout) or float(raw_timeout) <= 0:
            raise ValueError("gateway timeout policy must be finite and positive")
        timeout_seconds = float(raw_timeout)
        input_payload = {
            "schema": "aegis-gateway-execution-input-v1",
            "phase": normalized_phase,
            "call_id": normalized_call_id,
            "task": task,
            "system_context": system_context,
        }
        policy_payload = {
            "schema": "aegis-gateway-execution-policy-v1",
            "phase": normalized_phase,
            "trust_level": self.config.trust_level,
            "max_steps": self.config.max_steps,
            "timeout_seconds": timeout_seconds,
            "configured_provider": str(self.config.options.get("provider", "")),
            "configured_fallback_providers_hash": _hash(
                self.config.options.get("fallback_providers", ())
            ),
            "configured_provider_budgets_hash": _hash(
                self.config.options.get("provider_budgets", ())
            ),
        }
        execution_id = f"gateway-{normalized_phase}-{normalized_call_id}-attempt-{attempt}"
        idempotency_key = _hash(
            {
                "schema": "aegis-gateway-idempotency-key-v1",
                "mission_id": run.mission_id,
                "execution_id": execution_id,
                "input_hash": _hash(input_payload),
                "policy_hash": _hash(policy_payload),
            }
        )
        policy_payload["idempotency_key"] = idempotency_key
        lease_id = max(1, len(run.tool_execution_admissions) + 1)
        execution_id, admission_id = run.admit_tool_execution(
            tool_name=f"gateway.{normalized_phase}",
            input_payload=input_payload,
            policy_payload=policy_payload,
            effect_class="model_inference",
            actor_role="actor",
            expected_observation_schema="aegis-gateway-run-result-v1",
            stop_rule=f"single_{normalized_phase}",
            lease_id=lease_id,
            attempt=attempt,
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
        )
        previous_provider_context = self._provider_attempt_context
        self._provider_attempt_context = {
            "phase": normalized_phase,
            "call_id": normalized_call_id,
            "gateway_attempt": attempt,
            "gateway_max_attempts": max_attempts,
            "timeout_seconds": timeout_seconds,
            "idempotency_key": idempotency_key,
        }
        try:
            result = await asyncio.wait_for(
                _call_fenced(
                    self._gateway(task).run,
                    task,
                    system_context=system_context,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.CancelledError:
            run.record_tool_execution(
                tool_name=f"gateway.{normalized_phase}",
                execution_id=execution_id,
                admission_id=admission_id,
                input_payload=input_payload,
                policy_payload=policy_payload,
                result={"error": "CancelledError"},
                effect_class="model_inference",
                actor_role="actor",
                expected_observation_schema="aegis-gateway-run-result-v1",
                stop_rule=f"single_{normalized_phase}",
                lease_id=lease_id,
                attempt=attempt,
                status="CANCELLED",
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )
            raise

        except Exception as exc:
            run.record_tool_execution(
                tool_name=f"gateway.{normalized_phase}",
                execution_id=execution_id,
                admission_id=admission_id,
                input_payload=input_payload,
                policy_payload=policy_payload,
                result={"error": type(exc).__name__},
                effect_class="model_inference",
                actor_role="actor",
                expected_observation_schema="aegis-gateway-run-result-v1",
                stop_rule=f"single_{normalized_phase}",
                lease_id=lease_id,
                attempt=attempt,
                status="TIMED_OUT" if isinstance(exc, TimeoutError) else "REJECTED",
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )
            if attempt < max_attempts:
                return await self._run_admitted_gateway(
                    run,
                    task=task,
                    system_context=system_context,
                    phase=normalized_phase,
                    call_id=normalized_call_id,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                )
            raise
        finally:
            self._provider_attempt_context = previous_provider_context
        result_payload: dict[str, Any] = {
            "schema": "aegis-gateway-run-result-v1",
            "output_hash": _hash(getattr(result, "output", result)),
            "provider": str(getattr(result, "provider", "")),
            "trust_level": str(getattr(result, "trust_level", "")),
        }
        route = getattr(result, "provider_route", None)
        budget = getattr(result, "provider_budget", None)
        try:
            if run.require_native_authority:
                result_provider = getattr(result, "provider", None)
                result_trust_level = getattr(result, "trust_level", None)
                if (
                    type(result_provider) is not str
                    or not result_provider.strip()
                    or result_provider != result_provider.strip()
                    or type(result_trust_level) is not str
                    or result_trust_level != run.trust_level
                ):
                    raise RuntimeError("native Lab authority gateway result identity is invalid")
                if budget is not None:
                    budget_hash = getattr(budget, "budget_evidence_hash", None)
                    if type(budget_hash) is not str or not _is_digest(budget_hash):
                        raise RuntimeError("native Lab authority provider budget evidence is invalid")
            self._assert_provider_attempt_fence(
                run,
                phase=normalized_phase,
                call_id=normalized_call_id,
                gateway_attempt=attempt,
                route=route,
            )
        except RuntimeError as exc:
            run.record_tool_execution(
                tool_name=f"gateway.{normalized_phase}",
                execution_id=execution_id,
                admission_id=admission_id,
                input_payload=input_payload,
                policy_payload=policy_payload,
                result={"error": type(exc).__name__, "reason": str(exc)},
                effect_class="model_inference",
                actor_role="actor",
                expected_observation_schema="aegis-gateway-run-result-v1",
                stop_rule=f"single_{normalized_phase}",
                lease_id=lease_id,
                attempt=attempt,
                status="REJECTED",
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )
            raise
        if route is not None:
            result_payload.update(
                {
                    "provider_route_hash": str(getattr(route, "route_hash", "")),
                    "provider_selected": str(getattr(route, "selected_provider", "")),
                    "provider_attempted": tuple(getattr(route, "attempted_providers", ())),
                    "provider_throttled": tuple(getattr(route, "throttled_providers", ())),
                    "provider_fallback_used": bool(getattr(route, "fallback_used", False)),
                }
            )
        if budget is not None:
            result_payload["provider_budget_evidence_hash"] = str(
                getattr(budget, "budget_evidence_hash", "")
            )
        run.record_tool_execution(
            tool_name=f"gateway.{normalized_phase}",
            execution_id=execution_id,
            admission_id=admission_id,
            input_payload=input_payload,
            policy_payload=policy_payload,
            result=result_payload,
            effect_class="model_inference",
            actor_role="actor",
            expected_observation_schema="aegis-gateway-run-result-v1",
            stop_rule=f"single_{normalized_phase}",
            lease_id=lease_id,
            attempt=attempt,
            status="SUCCESS",
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
        )
        return result

    async def _run_post_completion_effect(self, run: LabRun, result: Any) -> None:
        """Fence a trusted persistence hook before the dossier becomes terminal."""

        raw_effect_required = self.config.options.get("lab_require_post_completion_effect", False)
        if type(raw_effect_required) is not bool:
            run.record_blocker("post_completion_effect_policy_invalid")
            return
        raw_trust_level = self.config.trust_level
        if type(raw_trust_level) is not str:
            run.record_blocker("trust_level_policy_invalid")
            return
        effect_required = raw_effect_required or (
            raw_trust_level.strip().upper() == "PROD"
        )
        effect = self._resolve_execution_cell(
            run,
            action_kind="post_completion_effect",
            options=self.config.options,
            capability="memory_write",
            effect_class="memory_write",
        )
        if effect is None:
            if self._execution_cells_strict and self.post_completion_effect is not None:
                run.record_blocker("execution_cell_not_registered:post_completion_effect")
            elif effect_required:
                run.record_blocker("post_completion_effect_missing")
            return
        input_payload = {
            "schema": "aegis-post-completion-effect-input-v1",
            "effect": "memory.index_session",
            "mission_id": run.mission_id,
            "output_hash": _hash(getattr(result, "output", None)),
        }
        policy_payload = {
            "schema": "aegis-post-completion-effect-policy-v1",
            "effect": "memory.index_session",
            "trust_level": self.config.trust_level,
            "failure_mode": "block_dossier",
        }
        try:
            execution_id, admission_id = run.admit_tool_execution(
                tool_name="memory.index_session",
                input_payload=input_payload,
                policy_payload=policy_payload,
                effect_class="memory_write",
                actor_role="actor",
                expected_observation_schema="aegis-post-completion-effect-result-v1",
                stop_rule="single_post_completion_effect",
                lease_id=max(1, len(run.tool_execution_admissions) + 1),
                attempt=1,
                execution_id=f"post-completion-{run.mission_id}",
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            run.record_blocker(f"post_completion_effect_admission_failed:{type(exc).__name__}")
            return
        try:
            effect_result = await _call_fenced(effect, run=run, result=result)
        except asyncio.CancelledError:
            try:
                run.record_tool_execution(
                    tool_name="memory.index_session",
                    execution_id=execution_id,
                    admission_id=admission_id,
                    input_payload=input_payload,
                    policy_payload=policy_payload,
                    result={"error": "CancelledError"},
                    effect_class="memory_write",
                    actor_role="actor",
                    expected_observation_schema="aegis-post-completion-effect-result-v1",
                    stop_rule="single_post_completion_effect",
                    lease_id=max(1, len(run.tool_execution_admissions)),
                    attempt=1,
                    status="CANCELLED",
                )
            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                run.record_blocker(f"post_completion_effect_settlement_failed:{type(settlement_exc).__name__}")
            raise
        except Exception as exc:
            try:
                run.record_tool_execution(
                    tool_name="memory.index_session",
                    execution_id=execution_id,
                    admission_id=admission_id,
                    input_payload=input_payload,
                    policy_payload=policy_payload,
                    result={"error": type(exc).__name__},
                    effect_class="memory_write",
                    actor_role="actor",
                    expected_observation_schema="aegis-post-completion-effect-result-v1",
                    stop_rule="single_post_completion_effect",
                    lease_id=max(1, len(run.tool_execution_admissions)),
                    attempt=1,
                    status="REJECTED",
                )
            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                run.record_blocker(f"post_completion_effect_settlement_failed:{type(settlement_exc).__name__}")
            if effect_required:
                run.record_blocker(f"post_completion_effect_failed:{type(exc).__name__}")
            return
        try:
            run.record_tool_execution(
                tool_name="memory.index_session",
                execution_id=execution_id,
                admission_id=admission_id,
                input_payload=input_payload,
                policy_payload=policy_payload,
                result={
                    "schema": "aegis-post-completion-effect-result-v1",
                    "result_hash": _hash(effect_result),
                },
                effect_class="memory_write",
                actor_role="actor",
                expected_observation_schema="aegis-post-completion-effect-result-v1",
                stop_rule="single_post_completion_effect",
                lease_id=max(1, len(run.tool_execution_admissions)),
                attempt=1,
                status="SUCCESS",
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            run.record_blocker(f"post_completion_effect_settlement_failed:{type(exc).__name__}")

    @staticmethod
    def _ingest_observations(
        run: LabRun,
        spec: ExperimentSpec,
        results: Any,
        *,
        run_id: str,
    ) -> None:
        if results is None:
            return
        if isinstance(results, dict):
            results = [results]
        if not isinstance(results, (list, tuple)):
            raise TypeError("experiment runner must return a sequence")
        typed_results = cast(list[Any] | tuple[Any, ...], results)
        for index, raw in enumerate(typed_results):
            if not isinstance(raw, dict):
                continue
            raw_map = cast(dict[str, Any], raw)
            run.add_observation(
                ObservationRecord(
                    observation_id=raw_map.get("observation_id", f"observation-{index+1}"),
                    experiment_id=spec.experiment_id,
                    seed=raw_map.get("seed", spec.preregistered_seeds[index % len(spec.preregistered_seeds)]),
                    measurement=raw_map["measurement"],
                    unit=raw_map.get("unit", spec.measurement_unit),
                    raw_artifact_hash=raw_map.get("raw_artifact_hash", _hash(raw_map)),
                    environment_hash=raw_map.get("environment_hash", _hash({"run_id": run_id})),
                    valid=raw_map.get("valid", True),
                    uncertainty=(
                        raw_map["uncertainty"]
                        if raw_map.get("uncertainty") is not None
                        else None
                    ),
                    replication_of=(
                        raw_map["replication_of"]
                        if raw_map.get("replication_of") is not None
                        else None
                    ),
                    operator_id=raw_map.get("operator_id", ""),
                    clean=raw_map.get("clean", True),
                    epistemic_status=raw_map.get("epistemic_status", "OBSERVED"),
                )
            )

    @staticmethod
    def _candidate_fields(raw: Any) -> dict[str, Any] | None:
        if type(raw) is dict:
            return cast(dict[str, Any], raw)
        if raw is None or type(raw) is str:
            return None
        return None

    @staticmethod
    def _ingest_search_candidates(run: LabRun, candidates: Any) -> None:
        if candidates is None:
            return
        if type(candidates) in (str, dict):
            candidates = [candidates]
        if type(candidates) not in (list, tuple):
            run.record_blocker("search_results_not_sequence")
            return
        typed_candidates = cast(list[Any] | tuple[Any, ...], candidates)
        for index, candidate in enumerate(typed_candidates):
            if type(candidate) is str:
                candidate = {"uri": candidate, "content": candidate}
            raw = LabApplication._candidate_fields(candidate)
            if raw is None:
                run.record_blocker("invalid_source_record", detail="candidate type")
                continue
            uri_values = [raw[name] for name in ("uri", "url") if name in raw]
            content_values = [raw[name] for name in ("content", "body", "snippet") if name in raw]
            source_id_values = [raw[name] for name in ("source_id", "id") if name in raw]
            if (
                any(type(value) is not str for value in uri_values)
                or any(type(value) is not str for value in content_values)
                or any(type(value) is not str for value in source_id_values)
                or (uri_values and any(value != uri_values[0] for value in uri_values[1:]))
                or (content_values and any(value != content_values[0] for value in content_values[1:]))
                or (source_id_values and any(value != source_id_values[0] for value in source_id_values[1:]))
            ):
                run.record_blocker("invalid_source_record", detail="ambiguous alias metadata")
                continue
            content = content_values[0] if content_values else ""
            uri = uri_values[0] if uri_values else ""
            if (
                type(uri) is not str
                or type(content) is not str
                or not uri.strip()
                or not content
            ):
                run.record_blocker("invalid_source_record", detail="uri/content type")
                continue
            content_text = content
            marker = _prompt_injection_marker(content_text)
            digest = _hash(content_text)
            if marker is not None:
                run.record_blocker("source_prompt_injection_detected", detail="prompt-injection marker")
                run.record_security_event(
                    "source_prompt_injection_detected",
                    artifact_hash=digest,
                    detail=marker,
                )
                continue
            supplied_content_hash = raw.get("content_hash", digest)
            supplied_snapshot_hash = raw.get("snapshot_hash", digest)
            retrieved_at_ms = raw.get("retrieved_at_ms", int(time.time() * 1000))
            trust_tier = raw.get("trust_tier", 1)
            extractor = raw.get("extractor", "search-as-code")
            relation = raw.get("relation", "unknown")
            provenance_cluster = raw.get("provenance_cluster", "")
            citation_spans = raw.get("citation_spans", ())
            source_id = source_id_values[0] if source_id_values else f"source-{len(run.sources)+index+1}"
            if (
                type(supplied_content_hash) is not str
                or supplied_content_hash != digest
            ):
                run.record_blocker("source_content_hash_mismatch")
                run.record_security_event(
                    "source_content_hash_mismatch",
                    artifact_hash=digest,
                    detail=(
                        supplied_content_hash
                        if type(supplied_content_hash) is str
                        else type(supplied_content_hash).__name__
                    ),
                )
                continue
            if (
                type(supplied_snapshot_hash) is not str
                or not _is_digest(supplied_snapshot_hash)
                or type(retrieved_at_ms) is not int
                or retrieved_at_ms <= 0
                or type(trust_tier) is not int
                or trust_tier < 1
                or type(extractor) is not str
                or not extractor.strip()
                or type(relation) is not str
                or not relation.strip()
                or type(provenance_cluster) is not str
                or (provenance_cluster and not provenance_cluster.strip())
                or type(citation_spans) not in (list, tuple)
                or any(not _valid_citation_span(span) for span in citation_spans)
                or type(source_id) is not str
                or not source_id.strip()
            ):
                run.record_blocker("invalid_source_record", detail="provenance metadata")
                run.record_security_event(
                    "invalid_source_record",
                    artifact_hash=digest,
                    detail="provenance metadata",
                )
                continue
            try:
                run.add_source(
                    SourceRecord(
                        source_id=source_id,
                        uri=uri,
                        content_hash=supplied_content_hash,
                        snapshot_hash=supplied_snapshot_hash,
                        retrieved_at_ms=retrieved_at_ms,
                        trust_tier=trust_tier,
                        extractor=extractor,
                        relation=relation,
                        citation_spans=citation_spans,
                        provenance_cluster=provenance_cluster,
                    )
                )
            except (TypeError, ValueError) as exc:
                run.record_blocker("invalid_source_record", detail=type(exc).__name__)
                run.record_security_event(
                    "invalid_source_record",
                    artifact_hash=digest,
                    detail=type(exc).__name__,
                )

    async def run(self) -> tuple[Any, LabDossier]:
        """Run one Lab application under an exclusive replay-writer lease."""

        options = cast(dict[str, Any], self.config.options)
        replay_directory = options.get("lab_replay_directory")
        lease = ReplayWriterLease(replay_directory) if replay_directory is not None else None
        if lease is not None:
            lease.acquire()
        try:
            return await self._run_unleased()
        finally:
            if lease is not None:
                lease.release()

    async def _run_unleased(self) -> tuple[Any, LabDossier]:
        active_run = self._active_run
        if active_run is not None and active_run.state not in {"completed", "blocked", "aborted"}:
            raise RuntimeError("LabApplication already has an active run")
        self._provider_attempt_receipts = {}
        options = cast(dict[str, Any], self.config.options)
        raw_token_budget = options.get("lab_token_budget", max(1, self.config.max_steps * 1000))
        if type(raw_token_budget) is not int:
            raise ValueError("lab token budget must be an integer")
        token_budget = raw_token_budget
        raw_external_attempt_budget = options.get(
            "lab_max_external_attempts",
            _default_external_attempt_budget(self.config.max_steps),
        )
        if type(raw_external_attempt_budget) is not int or raw_external_attempt_budget < 1:
            raise ValueError("lab external attempt budget must be a positive integer")
        default_finalization = min(max(1, token_budget // 5), max(0, token_budget - 1))
        default_recovery = min(
            max(0, token_budget // 10),
            max(0, token_budget - default_finalization - 1),
        )
        raw_scope = options.get("scope", ())
        raw_non_goals = options.get("non_goals", ())
        scope = _strict_string_sequence(raw_scope, label="lab scope")
        non_goals = _strict_string_sequence(raw_non_goals, label="lab non-goals")
        raw_finalization = options.get("lab_finalization_reserve", default_finalization)
        raw_recovery = options.get("lab_recovery_reserve", default_recovery)
        if type(raw_finalization) is not int or type(raw_recovery) is not int:
            raise ValueError("lab budget reserves must be integers")
        authority_mode = _authority_mode_from_options(
            options, default_trust_level=self.config.trust_level
        )
        raw_trust_policy_hash = options.get("lab_trust_policy_hash")
        if raw_trust_policy_hash is not None and type(raw_trust_policy_hash) is not str:
            raise ValueError("lab trust policy hash must be a string or unset")
        run = LabRun(
            self.config.task,
            max_steps=self.config.max_steps,
            external_attempt_budget=raw_external_attempt_budget,
            scope=scope,
            non_goals=non_goals,
            require_native_authority=authority_mode is AuthorityMode.NATIVE_REQUIRED,
            authority_mode=authority_mode,
            trust_level=self.config.trust_level,
            trust_policy_hash=raw_trust_policy_hash,
            token_budget=token_budget,
            finalization_reserve=raw_finalization,
            recovery_reserve=raw_recovery,
        )
        self._active_run = run
        if self.run_sink is not None:
            self.run_sink(run)
        if self.event_sink is not None:
            run.subscribe(self.event_sink)
        self._prepare_execution_cells(run, options)
        retrieved_context = await self._run_context_retrieval(run, options)
        context_block = (
            "\nLAB RETRIEVED CONTEXT (untrusted, hash-bound metadata):\n"
            f"{retrieved_context}\nEND LAB RETRIEVED CONTEXT\n"
            if retrieved_context
            else ""
        )
        run_id = run.mission_id
        await self._run_skill_requests(run, options)
        await self._run_tool_calls(run, options)
        controller = AdaptiveController(
            max_steps=self.config.max_steps,
            token_budget=token_budget,
            finalization_reserve=raw_finalization,
            recovery_reserve=raw_recovery,
        )
        search = options.get("search_as_code") or options.get("researcher")
        search_program = options.get("search_program")
        search_executor = self._resolve_execution_cell(
            run,
            action_kind="search_program",
            options=options,
            capability="network_read",
            effect_class="network_read",
        )
        if search_program is not None:
            program: SearchProgram | None = None
            research_admission_id = ""
            research_settled = False
            try:
                if isinstance(search_program, SearchProgram):
                    program = search_program
                elif isinstance(search_program, dict):
                    program_data = cast(dict[str, Any], search_program)
                    max_candidates = program_data.get("max_candidates", 20)
                    provider = program_data.get("provider", "unspecified")
                    freshness_max_age = program_data.get("freshness_max_age_seconds")
                    contradiction_clusters = program_data.get(
                        "min_independent_contradiction_clusters", 0
                    )
                    if (
                        type(max_candidates) is not int
                        or type(provider) is not str
                        or (freshness_max_age is not None and type(freshness_max_age) is not int)
                        or type(contradiction_clusters) is not int
                    ):
                        raise TypeError("search program metadata types are invalid")
                    program = SearchProgram.from_mappings(
                        tuple(cast(list[dict[str, Any]], program_data["operations"])),
                        max_candidates=max_candidates,
                        allowed_hosts=_strict_string_sequence(
                            program_data.get("allowed_hosts", ()),
                            label="search program allowlist",
                        ),
                        provider=provider,
                        freshness_max_age_seconds=freshness_max_age,
                        min_independent_contradiction_clusters=contradiction_clusters,
                    )
                else:
                    raise TypeError("search_program must be SearchProgram or mapping")
                if search_executor is None and self._execution_cells_strict:
                    run.record_blocker("execution_cell_not_registered:search_program")
                    raise RuntimeError("search program execution cell is not registered")
                executor = search_executor or options.get("search_program_executor") or search
                raw_search_timeout = options.get("search_timeout_seconds", 10.0)
                if not _is_finite_number(raw_search_timeout) or float(raw_search_timeout) <= 0:
                    raise TypeError("search executor timeout must be finite and positive")
                search_timeout_seconds = float(raw_search_timeout)
                if executor is None and not self._execution_cells_strict:
                    raw_search_max_bytes = options.get("search_max_bytes", 8 * 1024 * 1024)
                    if type(raw_search_max_bytes) is not int:
                        raise TypeError("search executor metadata types are invalid")
                    executor = SearchProgramExecutor(
                        query_provider=options.get("search_query_provider"),
                        timeout_seconds=search_timeout_seconds,
                        max_bytes=raw_search_max_bytes,
                    )
                if not callable(executor):
                    raise TypeError("search program executor is missing")
                research_admission_id = run.admit_research_program(
                    program_hash=program.program_hash,
                    operation_count=len(program.operations),
                    provider=program.provider,
                )
                try:
                    results = await self._run_search_program(
                        program,
                        executor,
                        task=self.config.task,
                        run_id=run_id,
                        timeout_seconds=search_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    try:
                        run.record_research_program(
                            program_hash=program.program_hash,
                            operation_count=len(program.operations),
                            provider=program.provider,
                            candidate_count=0,
                            admission_id=research_admission_id,
                            status="CANCELLED",
                        )
                        research_settled = True
                    except (RuntimeError, TypeError, ValueError) as settlement_exc:
                        run.record_blocker(f"research_admission_settlement_failed:{type(settlement_exc).__name__}")
                    raise
                except (OSError, TimeoutError, RuntimeError, TypeError, ValueError):
                    run.record_research_program(
                        program_hash=program.program_hash,
                        operation_count=len(program.operations),
                        provider=program.provider,
                        candidate_count=0,
                        admission_id=research_admission_id,
                        status="REJECTED",
                    )
                    research_settled = True
                    raise
                run.record_research_program(
                    program_hash=program.program_hash,
                    operation_count=len(program.operations),
                    provider=program.provider,
                    candidate_count=len(results),
                    admission_id=research_admission_id,
                )
                research_settled = True
                self._ingest_search_candidates(run, results)
                search = None
            except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
                if research_admission_id and not research_settled and program is not None:
                    try:
                        run.record_research_program(
                            program_hash=program.program_hash,
                            operation_count=len(program.operations),
                            provider=program.provider,
                            candidate_count=0,
                            admission_id=research_admission_id,
                            status="REJECTED",
                        )
                    except (RuntimeError, TypeError, ValueError):
                        run.record_blocker("research_admission_settlement_failed")
                run.record_blocker(f"search_program_invalid:{type(exc).__name__}")
                search = None
        if callable(search) and self._execution_cells_strict and search_executor is None:
            run.record_blocker("execution_cell_not_registered:search_program")
            search = None
        elif callable(search) and self._execution_cells_strict:
            # Use only the trusted binding; do not execute the compatibility
            # callable even if it happens to be present in the options map.
            search = search_executor
        if callable(search):
            queries = options.get("research_queries") or [self.config.task]
            for query in list(queries)[: self.config.max_steps]:
                research_admission_id = ""
                research_settled = False
                research_program_hash = _hash({"query": query})
                try:
                    research_admission_id = run.admit_research_program(
                        program_hash=research_program_hash,
                        operation_count=1,
                        provider="callback",
                    )
                    try:
                        results = await _call_fenced(search, query, task=self.config.task, run_id=run_id)
                    except asyncio.CancelledError:
                        try:
                            run.record_research_program(
                                program_hash=research_program_hash,
                                operation_count=1,
                                provider="callback",
                                candidate_count=0,
                                admission_id=research_admission_id,
                                status="CANCELLED",
                            )
                            research_settled = True
                        except (RuntimeError, TypeError, ValueError) as settlement_exc:
                            run.record_blocker(f"research_admission_settlement_failed:{type(settlement_exc).__name__}")
                        raise
                    except (OSError, TimeoutError, RuntimeError, TypeError, ValueError):
                        run.record_research_program(
                            program_hash=research_program_hash,
                            operation_count=1,
                            provider="callback",
                            candidate_count=0,
                            admission_id=research_admission_id,
                            status="REJECTED",
                        )
                        research_settled = True
                        raise
                    run.record_research_program(
                        program_hash=research_program_hash,
                        operation_count=1,
                        provider="callback",
                        candidate_count=len(results or []),
                        admission_id=research_admission_id,
                    )
                    research_settled = True
                except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
                    if research_admission_id and not research_settled:
                        try:
                            run.record_research_program(
                                program_hash=research_program_hash,
                                operation_count=1,
                                provider="callback",
                                candidate_count=0,
                                admission_id=research_admission_id,
                                status="REJECTED",
                            )
                        except (RuntimeError, TypeError, ValueError):
                            run.record_blocker("research_admission_settlement_failed")
                    run.record_blocker(f"search_failed:{type(exc).__name__}")
                    break
                self._ingest_search_candidates(run, results)
        elif search is None and search_program is None and not self._execution_cells_strict:
            run.record_blocker("search_as_code_hook_missing")

        for raw in options.get("claim_records", options.get("claims", ())) or ():
            try:
                claim = raw if isinstance(raw, ClaimRecord) else ClaimRecord(
                    claim_id=raw["claim_id"],
                    statement=raw["statement"],
                    source_ids=raw["source_ids"],
                    confidence_bps=raw.get("confidence_bps", 5_000),
                    status=raw.get("status", "unresolved"),
                )
                run.add_claim(claim)
            except (KeyError, TypeError, ValueError):
                run.record_blocker("invalid_claim_record")

        for raw in options.get("hypothesis_records", options.get("hypotheses", ())) or ():
            try:
                hypothesis = raw if isinstance(raw, HypothesisRecord) else HypothesisRecord(
                    hypothesis_id=raw["hypothesis_id"],
                    statement=raw["statement"],
                    prior_bps=raw.get("prior_bps", 5_000),
                    falsifiers=raw["falsifiers"],
                    supporting_claim_ids=raw.get("supporting_claim_ids", ()),
                    contradicting_claim_ids=raw.get("contradicting_claim_ids", ()),
                )
                run.add_hypothesis(hypothesis)
            except (KeyError, TypeError, ValueError):
                run.record_blocker("invalid_hypothesis_record")

        browser_session = options.get("browser_session")
        browser_launcher = options.get("browser_launcher")
        browser_actions = options.get("browser_actions")
        browser_observations = options.get("browser_observations")
        browser_prompt_injection_detected = False
        raw_policy = options.get("browser_policy")
        if isinstance(raw_policy, dict):
            policy_data = cast(dict[str, Any], raw_policy)
            raw_policy = BrowserCellPolicy(
                allowed_hosts=policy_data.get("allowed_hosts", ()),
                require_https=policy_data.get("require_https", True),
                max_actions=policy_data.get("max_actions", 100),
                max_observations=policy_data.get("max_observations", 1_000),
            )
        browser_cell = options.get("browser_cell")
        try:
            if not isinstance(browser_cell, BrowserCell):
                browser_cell = BrowserCell(raw_policy if isinstance(raw_policy, BrowserCellPolicy) else None)
        except (TypeError, ValueError) as exc:
            run.record_blocker(f"browser_cell_policy_invalid:{type(exc).__name__}")
            browser_cell = None
        owns_browser_session = False
        browser_launch_admission_id = ""
        browser_launch_action_id = ""
        browser_launch_action: dict[str, str] = {"kind": "launch", "launcher": "provided"}
        browser_launch_lease_id = 0
        browser_launch_settled = False
        if browser_cell is not None and browser_session is None and callable(browser_launcher):
            try:
                # Browser creation is itself an external side effect.  Admit
                # and replay-bind that intent before invoking an untrusted
                # launcher, so no launcher can create a process before the
                # native receipt exists.
                browser_launch_lease_id = max(1, browser_cell.lease_id + 1)
                browser_launch_action_id = (
                    f"lease-{browser_launch_lease_id}-launch-"
                    f"{browser_cell.action_count + 1}"
                )
                browser_launch_admission_id = run.admit_browser_action(
                    action_kind="launch",
                    action=browser_launch_action,
                    policy=browser_cell.policy,
                    lease_id=browser_launch_lease_id,
                    action_id=browser_launch_action_id,
                )
                browser_session = await browser_cell.acquire(browser_launcher)
                owns_browser_session = True
                run.record_browser_action(
                    action_id=browser_launch_action_id,
                    admission_id=browser_launch_admission_id,
                    action_kind="launch",
                    action=browser_launch_action,
                    result={"session_bound": True},
                    policy=browser_cell.policy,
                    lease_id=browser_launch_lease_id,
                    status="SUCCESS",
                )
                browser_launch_settled = True
            except asyncio.CancelledError:
                if browser_launch_admission_id and not browser_launch_settled:
                    try:
                        run.record_browser_action(
                            action_id=browser_launch_action_id,
                            admission_id=browser_launch_admission_id,
                            action_kind="launch",
                            action=browser_launch_action,
                            result={"error": "CancelledError"},
                            policy=browser_cell.policy,
                            lease_id=browser_launch_lease_id,
                            status="CANCELLED",
                        )
                        browser_launch_settled = True
                    except (RuntimeError, TypeError, ValueError) as settlement_exc:
                        run.record_blocker(
                            f"browser_launch_settlement_failed:{type(settlement_exc).__name__}"
                        )
                raise
            except (RuntimeError, TypeError, ValueError) as exc:
                if browser_launch_admission_id and not browser_launch_settled:
                    try:
                        run.record_browser_action(
                            action_id=browser_launch_action_id,
                            admission_id=browser_launch_admission_id,
                            action_kind="launch",
                            action=browser_launch_action,
                            result={"error": type(exc).__name__},
                            policy=browser_cell.policy,
                            lease_id=browser_launch_lease_id,
                            status="REJECTED",
                        )
                        browser_launch_settled = True
                    except (RuntimeError, TypeError, ValueError) as settlement_exc:
                        run.record_blocker(
                            f"browser_launch_settlement_failed:{type(settlement_exc).__name__}"
                        )
                run.record_blocker(f"browser_cell_launch_blocked:{type(exc).__name__}")
        if browser_cell is not None and browser_session is not None and browser_cell.session is None:
            try:
                await browser_cell.bind(browser_session)
            except (RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"browser_cell_bind_blocked:{type(exc).__name__}")
                browser_session = None
        # A launcher-owned session stays bound through the controller loop so
        # the model can perform multiple typed browser actions across steps.
        # Explicitly supplied sessions are never closed by this application.
        keep_browser_for_controller = owns_browser_session and browser_session is not None

        async def release_owned_browser_session() -> None:
            nonlocal owns_browser_session
            if owns_browser_session and browser_cell is not None:
                await browser_cell.release()
                browser_cell.close()
                owns_browser_session = False

        if browser_actions and not isinstance(browser_actions, (list, tuple)):
            run.record_blocker("browser_actions_not_sequence")
            browser_actions = ()
        if browser_observations and not isinstance(browser_observations, (list, tuple)):
            run.record_blocker("browser_observations_not_sequence")
            browser_observations = ()
        if browser_cell is not None and browser_session is not None and browser_actions:
            action_specs = cast(list[Any] | tuple[Any, ...], browser_actions)
            runtime_adapter = self._resolve_execution_cell(
                run,
                action_kind="browser_action",
                options=options,
                capability="network_read",
                effect_class="network_read",
            )
            if runtime_adapter is None and not self._execution_cells_strict:
                runtime_adapter = options.get("browser_runtime_adapter")
            try:
                for action in list(action_specs)[: self.config.max_steps]:
                    action_id = ""
                    admission_id = ""
                    action_kind = ""
                    action_counted = False
                    try:
                        # The Lab API accepts only serializable typed actions.
                        # Callable actions remain available on the legacy
                        # ``AegisAdapter`` compatibility surface, but allowing
                        # them here would let a caller bypass the replayable
                        # browser vocabulary and its policy audit.
                        if not isinstance(action, dict):
                            raise TypeError("lab browser actions must be typed mappings")
                        await browser_cell.admit(browser_session)
                        action_map = cast(dict[str, Any], action)
                        action_kind = str(action_map.get("kind", "")).strip().lower()
                        action_id = (
                            f"lease-{max(1, browser_cell.lease_id)}-"
                            f"action-{browser_cell.action_count + 1}"
                        )
                        admission_id = run.admit_browser_action(
                            action_kind=action_kind,
                            action=action,
                            policy=browser_cell.policy,
                            lease_id=max(1, browser_cell.lease_id),
                            action_id=action_id,
                        )
                        if runtime_adapter is None and self._execution_cells_strict:
                            run.record_blocker("execution_cell_not_registered:browser_action")
                            raise RuntimeError("browser execution cell is not registered")
                        if runtime_adapter is not None and hasattr(runtime_adapter, "capture_action"):
                            capture = await _call_fenced(
                                runtime_adapter.capture_action,
                                browser_session=browser_session,
                                run_id=int(run_id[:16], 16),
                                action_id=browser_cell.action_count + 1,
                                sequence_number=browser_cell.action_count + 1,
                                action=_runtime_browser_action(browser_session, action, browser_cell),
                            )
                        else:
                            # Construct the compatibility gateway only after
                            # browser admission succeeds.  A custom factory
                            # is untrusted and must not perform an implicit
                            # side effect before the native receipt exists.
                            gateway = self._gateway(self.config.task)
                            capture = await _call_fenced(
                                gateway.capture_browser_action,
                                browser_session=browser_session,
                                action=_gateway_browser_action(action, browser_cell),
                                run_id=int(run_id[:16], 16),
                            )
                        await browser_cell.admit(browser_session)
                        browser_cell.record()
                        action_counted = True
                        raw_result_hash = str(getattr(capture, "browser_action_result_hash", ""))
                        run.record_browser_action(
                            action_id=action_id,
                            admission_id=admission_id,
                            action_kind=action_kind,
                            action=action,
                            result={"capture_result_hash": raw_result_hash},
                            policy=browser_cell.policy,
                            lease_id=max(1, browser_cell.lease_id),
                        )
                    except asyncio.CancelledError:
                        if admission_id:
                            if not action_counted:
                                # Cancellation is not evidence that the
                                # already-admitted actor action had no effect.
                                browser_cell.record()
                            try:
                                run.record_browser_action(
                                    action_id=action_id,
                                    admission_id=admission_id,
                                    action_kind=action_kind,
                                    action=action,
                                    result={"error": "CancelledError"},
                                    policy=browser_cell.policy,
                                    lease_id=max(1, browser_cell.lease_id),
                                    status="CANCELLED",
                                )
                            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                                run.record_blocker(
                                    f"browser_action_settlement_failed:{type(settlement_exc).__name__}"
                                )
                        await release_owned_browser_session()
                        raise
                    except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
                        if admission_id:
                            if not action_counted:
                                browser_cell.record()
                            try:
                                run.record_browser_action(
                                    action_id=action_id,
                                    admission_id=admission_id,
                                    action_kind=action_kind,
                                    action=action,
                                    result={"error": type(exc).__name__},
                                    policy=browser_cell.policy,
                                    lease_id=max(1, browser_cell.lease_id),
                                    status="REJECTED",
                                )
                            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                                run.record_blocker(
                                    f"browser_action_settlement_failed:{type(settlement_exc).__name__}"
                                )
                        run.record_blocker(f"browser_cell_blocked:{type(exc).__name__}")
                        break
            finally:
                if owns_browser_session and not browser_observations and not keep_browser_for_controller:
                    await release_owned_browser_session()
        if browser_cell is not None and browser_session is not None and browser_observations:
            observation_specs = cast(list[Any] | tuple[Any, ...], browser_observations)
            try:
                for observation in list(observation_specs)[: self.config.max_steps]:
                    observation_id = ""
                    admission_id = ""
                    observation_kind = ""
                    try:
                        if not isinstance(observation, dict):
                            raise TypeError("lab browser observations must be typed mappings")
                        observation_map = cast(dict[str, Any], observation)
                        observation_kind = str(observation_map.get("kind", "")).strip().lower()
                        await browser_cell.admit_observation(browser_session, observation)
                        observation_id, admission_id = run.admit_browser_observation(
                            observation_kind=observation_kind,
                            action=observation,
                            policy=browser_cell.policy,
                            lease_id=max(1, browser_cell.lease_id),
                            observation_count=max(1, browser_cell.observation_count + 1),
                        )
                        result = await browser_cell.observe(observation)
                        marker = _prompt_injection_marker_in_value(result)
                        if marker is not None:
                            browser_prompt_injection_detected = True
                            artifact_hash = _hash(
                                {
                                    "schema": "aegis-browser-observation-security-v1",
                                    "result": result,
                                }
                            )
                            run.record_security_event(
                                "browser_prompt_injection_detected",
                                artifact_hash=artifact_hash,
                                detail=marker,
                            )
                            run.record_blocker(
                                "browser_prompt_injection_detected",
                                detail="prompt-injection marker",
                            )
                            run.record_browser_observation(
                                observation_kind=observation_kind,
                                action=observation,
                                result={
                                    "security": "prompt_injection_detected",
                                    "artifact_hash": artifact_hash,
                                },
                                policy=browser_cell.policy,
                                lease_id=max(1, browser_cell.lease_id),
                                observation_count=browser_cell.observation_count,
                                admission_id=admission_id,
                                observation_id=observation_id,
                                status="REJECTED",
                            )
                            break
                        run.record_browser_observation(
                            observation_kind=observation_kind,
                            action=observation,
                            result=result,
                            policy=browser_cell.policy,
                            lease_id=max(1, browser_cell.lease_id),
                            observation_count=browser_cell.observation_count,
                            admission_id=admission_id,
                            observation_id=observation_id,
                        )
                    except asyncio.CancelledError:
                        if admission_id:
                            try:
                                run.record_browser_observation(
                                    observation_kind=observation_kind,
                                    action=observation,
                                    result={"error": "CancelledError"},
                                    policy=browser_cell.policy,
                                    lease_id=max(1, browser_cell.lease_id),
                                    observation_count=max(1, browser_cell.observation_count + 1),
                                    admission_id=admission_id,
                                    observation_id=observation_id,
                                    status="CANCELLED",
                                )
                            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                                run.record_blocker(
                                    f"browser_observation_settlement_failed:{type(settlement_exc).__name__}"
                                )
                        await release_owned_browser_session()
                        raise
                    except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as exc:
                        if admission_id:
                            try:
                                run.record_browser_observation(
                                    observation_kind=observation_kind,
                                    action=observation,
                                    result={"error": type(exc).__name__},
                                    policy=browser_cell.policy,
                                    lease_id=max(1, browser_cell.lease_id),
                                    observation_count=max(1, browser_cell.observation_count + 1),
                                    admission_id=admission_id,
                                    observation_id=observation_id,
                                    status="REJECTED",
                                )
                            except (RuntimeError, TypeError, ValueError) as settlement_exc:
                                run.record_blocker(
                                    f"browser_observation_settlement_failed:{type(settlement_exc).__name__}"
                                )
                        run.record_blocker(f"browser_observation_blocked:{type(exc).__name__}")
                        break
            finally:
                if owns_browser_session and not keep_browser_for_controller:
                    await release_owned_browser_session()
        else:
            if owns_browser_session and not keep_browser_for_controller:
                await release_owned_browser_session()
            if self.config.browser and browser_session is None and not browser_actions and not browser_observations:
                run.record_blocker("browser_session_or_actions_missing")

        # A lab mission gets a bounded controller loop. Each response is an
        # observation of the controller, never silently promoted to a claim;
        # structured records are admitted only after the reducer validates
        # their provenance and links.
        try:
            gateway_max_attempts = _bounded_retry_attempts(
                options.get("gateway_max_attempts", 1),
                max_steps=self.config.max_steps,
                label="gateway",
            )
        except ValueError:
            run.record_blocker("gateway_retry_policy_invalid")
            gateway_max_attempts = 1
        controller_executed_experiment_ids: set[str] = set()
        raw_iterations = options.get("lab_iterations", 3 if self._has_lab_hooks(options) else 1)
        if type(raw_iterations) is not int or raw_iterations < 1:
            run.record_blocker("lab_iterations_policy_invalid")
            iterations = 0
        else:
            iterations = min(self.config.max_steps, raw_iterations)
        for _ in range(iterations):
            if browser_prompt_injection_detected:
                # Do not ask the model to continue after untrusted page content
                # crossed the marker gate; preserve the blocker for finalization.
                break
            decision = controller.next(run)
            if decision is None:
                run.record_blocker("adaptive_exploration_budget_exhausted")
                break
            try:
                run.admit_exploration(decision.token_budget)
            except (RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"native_exploration_admission_failed:{type(exc).__name__}")
                break
            context = {
                "step": decision.step,
                "max_steps": iterations,
                "phase": decision.phase,
                "reason": decision.reason,
                "progress_potential": decision.potential,
                "token_budget": decision.token_budget,
                "source_count": len(run.sources),
                "claim_count": len(run.claims),
                "hypothesis_count": len(run.hypotheses),
                "experiment_count": len(run.experiments),
                "observation_count": len(run.observations),
                "blockers": list(dict.fromkeys(run.blockers)),
                "execution_cells": run.execution_cell_manifest,
            }
            step_task = (
                f"{self.config.task}\n\nAEGIS LAB CONTROLLER STEP {decision.step}/{iterations}\n"
                f"{json.dumps(context, sort_keys=True)}\n"
                f"{context_block}"
                "Return only evidence-bounded progress. If structured records are available, "
                "include them under claims, hypotheses, or experiment_spec; if a configured "
                "research/tool capability is required, you may emit exactly one JSON object "
                "with schema=aegis-lab-action-plan-v1 and a bounded actions list. Allowed "
                "action kinds are search_program, browser_action, experiment_action, "
                "simulation_action, and tool_call; experiment_action and simulation_action "
                "must select a configured ExperimentSpec/SimulationSpec and "
                "trusted experiment cell (use the advertised cell_id); tool_call "
                "must be read_only/network_read/compute unless an explicit policy grants "
                "external writes; never emit code or callables. "
                "Otherwise state the gap."
            )
            try:
                step_result = await self._run_admitted_gateway(
                    run,
                    task=step_task,
                    system_context=(
                        f"{self.system_context}\n\nYou are a bounded controller inside AEGIS Lab Runtime."
                        if self.system_context
                        else "You are a bounded controller inside AEGIS Lab Runtime."
                    ),
                    phase="controller_step",
                    call_id=str(decision.step),
                    max_attempts=gateway_max_attempts,
                )
                event_snapshot = run._projection_snapshot()
                run._append(
                    "controller_step",
                    {
                        "step": decision.step,
                        "phase": decision.phase,
                        "output_hash": _hash(step_result.output),
                        "reserve": controller.reserve_snapshot(),
                    },
                    rollback_snapshot=event_snapshot,
                    event_state_epoch=run.state_epoch + 1,
                )
                # Run search actions before preregistration.  Capturing a
                # source is legal in the research phase, while registering an
                # experiment advances the reducer to ``experimenting``.  A
                # two-pass dispatch keeps a single model response able to
                # request both without making source ingestion order-dependent.
                await self._execute_controller_action_plan(
                    run,
                    step_result.output,
                    options,
                    run_id=run_id,
                    browser_cell=browser_cell,
                    browser_session=browser_session,
                    action_kinds={"search_program"},
                )
                self._ingest_structured_step(run, step_result.output)
                controller_executed_experiment_ids.update(
                    await self._execute_controller_action_plan(
                        run,
                        step_result.output,
                        options,
                        run_id=run_id,
                        browser_cell=browser_cell,
                        browser_session=browser_session,
                        action_kinds={
                            "browser_action",
                            "experiment_action",
                            "simulation_action",
                            "tool_call",
                        },
                    )
                )
                if not controller.observe(run):
                    run.record_blocker("adaptive_no_progress_detected")
                    break
            except asyncio.CancelledError:
                await release_owned_browser_session()
                raise
            except Exception as exc:
                run.record_blocker(f"controller_step_failed:{type(exc).__name__}")
                break

        await release_owned_browser_session()
        experiment_runner = self._resolve_execution_cell(
            run,
            action_kind="experiment_action",
            options=options,
            capability="compute",
            effect_class="compute",
        )
        if experiment_runner is None and not self._execution_cells_strict:
            experiment_runner = options.get("experiment_runner")
        if callable(experiment_runner):
            raw_spec = options.get("experiment_spec")
            if raw_spec is None and run.experiments:
                raw_spec = run.experiments[next(reversed(run.experiments))]
            if (
                isinstance(raw_spec, ExperimentSpec)
                and raw_spec.experiment_id in controller_executed_experiment_ids
            ):
                # A controller-selected action already consumed this trusted
                # cell.  Do not silently execute the compatibility lane a
                # second time after the controller loop.
                raw_spec = None
            if isinstance(raw_spec, ExperimentSpec):
                try:
                    if raw_spec.experiment_id not in run.experiments:
                        run.add_experiment(raw_spec)
                except (TypeError, ValueError, KeyError) as exc:
                    run.record_blocker(f"experiment_spec_invalid:{type(exc).__name__}")
                else:
                    await self._run_admitted_experiment(
                        run,
                        raw_spec,
                        experiment_runner,
                        options,
                        run_id=run_id,
                    )
        else:
            simulation_runner = self._resolve_execution_cell(
                run,
                action_kind="simulation_action",
                options=options,
                capability="compute",
                effect_class="compute",
            )
            if simulation_runner is None and not self._execution_cells_strict:
                simulation_runner = options.get("simulation_runner")
            if callable(simulation_runner):
                raw_spec = options.get("experiment_spec")
                raw_simulation = options.get("simulation_spec")
                controller_already_selected = bool(
                    controller_executed_experiment_ids
                    and (
                        raw_spec is None
                        or (
                            isinstance(raw_spec, ExperimentSpec)
                            and raw_spec.experiment_id in controller_executed_experiment_ids
                        )
                    )
                )
                if controller_already_selected:
                    # A controller-selected experiment or simulation action has
                    # already consumed the configured cell.
                    raw_spec = None
                    raw_simulation = None
                if controller_already_selected:
                    pass
                elif not isinstance(raw_spec, ExperimentSpec) or not isinstance(raw_simulation, SimulationSpec):
                    run.record_blocker("simulation_contract_missing")
                elif raw_simulation.experiment_id != raw_spec.experiment_id:
                    run.record_blocker("simulation_experiment_binding_mismatch")
                else:
                    try:
                        if raw_spec.experiment_id not in run.experiments:
                            run.add_experiment(raw_spec)
                    except (TypeError, ValueError, KeyError) as exc:
                        run.record_blocker(f"simulation_spec_invalid:{type(exc).__name__}")
                    else:
                        await self._run_admitted_simulation(
                            run,
                            raw_spec,
                            raw_simulation,
                            simulation_runner,
                            options,
                            run_id=run_id,
                        )
            else:
                run.record_blocker("experiment_runner_missing")

        benchmark_payload: dict[str, Any] | None = None
        raw_protocol = options.get("benchmark_protocol")
        raw_trials = options.get("benchmark_trials")
        if raw_protocol is not None and raw_trials is not None:
            validator_execution_id: str | None = None
            validator_admission_id: str | None = None
            configured_validator = options.get("benchmark_validator")
            validator_runner = (
                self._resolve_execution_cell(
                    run,
                    action_kind="benchmark_validation",
                    options=options,
                    cell_id=options.get("benchmark_validator_cell_id"),
                    capability="compute",
                    effect_class="compute",
                )
                if configured_validator is not None
                else None
            )
            validator_input_payload = {
                "schema": "aegis-benchmark-validator-invocation-v1",
                "protocol": raw_protocol,
                "trials": raw_trials,
            }
            validator_policy_payload = {
                "schema": "aegis-benchmark-validator-policy-v1",
                "mode": "isolated_process"
                if options.get("benchmark_validator_command") is not None
                else "in_process_callback",
                "timeout_seconds": options.get("benchmark_validator_timeout_seconds", 5.0),
            }
            validator_requested = (
                configured_validator is not None
                or options.get("benchmark_validator_command") is not None
            )
            validator_for_evaluation = (
                validator_runner if validator_runner is not None else configured_validator
            )
            if configured_validator is not None and validator_runner is None and self._execution_cells_strict:
                # Keep the admission/settlement receipt, but make the actual
                # evaluation fail closed without invoking an unregistered
                # compatibility callback.
                run.record_blocker("execution_cell_not_registered:benchmark_validation")
                validator_for_evaluation = _missing_benchmark_validator
            try:
                protocol = raw_protocol if isinstance(raw_protocol, BenchmarkProtocolV2) else BenchmarkProtocolV2(**raw_protocol)
                if type(raw_trials) not in (list, tuple):
                    raise TypeError("benchmark trials must be a list or tuple")
                if validator_requested:
                    validator_input_payload["trials"] = raw_trials
                    validator_execution_id, validator_admission_id = run.admit_tool_execution(
                        tool_name="benchmark.hidden_validator",
                        input_payload=validator_input_payload,
                        policy_payload=validator_policy_payload,
                        effect_class="compute",
                        actor_role="observer",
                        expected_observation_schema="aegis-benchmark-result-v2",
                        stop_rule="single_call",
                        execution_id="benchmark-validator-1",
                    )
                raw_environment = options.get("benchmark_environment")
                if isinstance(raw_environment, EnvironmentFingerprint):
                    environment = raw_environment
                elif isinstance(raw_environment, dict):
                    environment_data = cast(dict[str, Any], raw_environment)
                    environment = EnvironmentFingerprint(
                        os_name=environment_data.get("os_name", ""),
                        os_release=environment_data.get("os_release", ""),
                        architecture=environment_data.get("architecture", ""),
                        python_version=environment_data.get("python_version", ""),
                        cpu_model=environment_data.get("cpu_model", ""),
                        logical_cpus=environment_data.get("logical_cpus", 0),
                        container_image=environment_data.get("container_image", ""),
                        gpu_driver=environment_data.get("gpu_driver", ""),
                        locale_name=environment_data.get("locale_name", ""),
                        network_policy=environment_data.get("network_policy", ""),
                    )
                else:
                    environment = None
                benchmark_result = evaluate_benchmark(
                    protocol,
                    raw_trials,
                    baseline=options.get("benchmark_baseline"),
                    contamination_flags=options.get("contamination_flags", ()),
                    paired_blocks=options.get("benchmark_paired_blocks"),
                    warmups=options.get("benchmark_warmups"),
                    environment_hash=options.get("benchmark_environment_hash", ""),
                    environment=environment,
                    validator=validator_for_evaluation,
                    validator_command=options.get("benchmark_validator_command"),
                    validator_timeout_seconds=options.get("benchmark_validator_timeout_seconds", 5.0),
                    validator_max_output_bytes=options.get("benchmark_validator_max_output_bytes", 65_536),
                )
                benchmark_payload = asdict(benchmark_result)
                if validator_execution_id is not None and validator_admission_id is not None:
                    run.record_tool_execution(
                        tool_name="benchmark.hidden_validator",
                        execution_id=validator_execution_id,
                        admission_id=validator_admission_id,
                        input_payload=validator_input_payload,
                        policy_payload=validator_policy_payload,
                        result=benchmark_payload,
                        effect_class="compute",
                        actor_role="observer",
                        expected_observation_schema="aegis-benchmark-result-v2",
                        stop_rule="single_call",
                        status="SUCCESS" if benchmark_result.status == "PASS" else "REJECTED",
                    )
                    validator_execution_id = None
                    validator_admission_id = None
                event_snapshot = run._projection_snapshot()
                run._append(
                    "benchmark_evaluated",
                    benchmark_payload,
                    rollback_snapshot=event_snapshot,
                    event_state_epoch=run.state_epoch + 1,
                )
                if benchmark_result.status != "PASS":
                    run.record_blocker("benchmark_rejected")
            except (TypeError, ValueError) as exc:
                if validator_execution_id is not None and validator_admission_id is not None:
                    with contextlib.suppress(RuntimeError, TypeError, ValueError):
                        run.record_tool_execution(
                            tool_name="benchmark.hidden_validator",
                            execution_id=validator_execution_id,
                            admission_id=validator_admission_id,
                            input_payload=validator_input_payload,
                            policy_payload=validator_policy_payload,
                            result={"error": type(exc).__name__},
                            effect_class="compute",
                            actor_role="observer",
                            expected_observation_schema="aegis-benchmark-result-v2",
                            stop_rule="single_call",
                            status="REJECTED",
                        )
                run.record_blocker(f"benchmark_invalid:{type(exc).__name__}")
        # Keep the run non-terminal while the final model call is admitted and
        # settled.  A completed run must not be a bypass around the native
        # gateway fence.
        dossier = run.dossier(benchmark=benchmark_payload, finalize=False)
        synthesis_task = (
            f"{self.config.task}\n\nLAB DOSSIER (do not claim completion unless status=completed):\n"
            f"{json.dumps(dossier.manifest, sort_keys=True)}\n"
            f"blockers={list(dossier.blockers)}\n"
            f"{context_block}"
            "Return a concise evidence-bounded synthesis and identify the next falsifiable step."
        )
        result = await self._run_admitted_gateway(
            run,
            task=synthesis_task,
            system_context=(
                f"{self.system_context}\n\nYou are operating inside AEGIS Lab Runtime."
                if self.system_context
                else "You are operating inside AEGIS Lab Runtime."
            ),
            phase="synthesis",
            call_id="final",
            max_attempts=gateway_max_attempts,
        )
        event_snapshot = run._projection_snapshot()
        run._append(
            "synthesis_committed",
            {"output_hash": _hash(result.output)},
            rollback_snapshot=event_snapshot,
            event_state_epoch=run.state_epoch + 1,
        )
        replay_directory = options.get("lab_replay_directory")
        if replay_directory is not None:
            try:
                run.archive_to_native(
                    replay_directory,
                    max_events_per_segment=options.get("lab_replay_segment_size", 64),
                )
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                run.record_blocker(f"lab_replay_archive_failed:{type(exc).__name__}")
                if run.state == "completed":
                    run.transition("blocked")
        await self._run_post_completion_effect(run, result)
        dossier = run.dossier(benchmark=benchmark_payload)
        return result, dossier

    @staticmethod
    def _has_lab_hooks(options: dict[str, Any]) -> bool:
        return any(
            callable(options.get(name))
            for name in (
                "search_as_code",
                "researcher",
                "experiment_runner",
                "simulation_runner",
                "search_query_provider",
                "tool_runner",
                "browser_launcher",
            )
        ) or bool(options.get("browser_actions")) or options.get("search_program") is not None or bool(
            options.get("skill_requests")
        ) or bool(options.get("tool_calls")) or options.get("browser_session") is not None

    @staticmethod
    def _ingest_structured_step(run: LabRun, output: Any) -> None:
        if not isinstance(output, dict):
            return
        payload = cast(dict[str, Any], output)
        # A controller may preregister a hypothesis/experiment in the same
        # response that first requests a research action.  The static research
        # compatibility lane normally moves ``planned`` to ``researching``
        # before this reducer runs, but an action-only lane has not captured a
        # source yet.  Enter the research phase explicitly so structured
        # records are not rejected solely because of response ordering.
        if run.state == "planned" and any(
            payload.get(name)
            for name in ("claims", "hypotheses", "experiment_spec")
        ):
            try:
                run.transition("researching")
            except (RuntimeError, TypeError, ValueError):
                run.record_blocker("controller_structured_phase_transition_failed")
                return
        for raw in payload.get("claims", ()) or ():
            if not isinstance(raw, dict):
                continue
            raw = cast(dict[str, Any], raw)
            try:
                run.add_claim(
                    ClaimRecord(
                        claim_id=raw["claim_id"],
                        statement=raw["statement"],
                        source_ids=raw["source_ids"],
                        confidence_bps=raw.get("confidence_bps", 5_000),
                        status=raw.get("status", "unresolved"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                run.record_blocker("invalid_controller_claim")
        for raw in payload.get("hypotheses", ()) or ():
            if not isinstance(raw, dict):
                continue
            raw = cast(dict[str, Any], raw)
            try:
                run.add_hypothesis(
                    HypothesisRecord(
                        hypothesis_id=raw["hypothesis_id"],
                        statement=raw["statement"],
                        prior_bps=raw.get("prior_bps", 5_000),
                        falsifiers=raw["falsifiers"],
                        supporting_claim_ids=raw.get("supporting_claim_ids", ()),
                        contradicting_claim_ids=raw.get("contradicting_claim_ids", ()),
                    )
                )
            except (KeyError, TypeError, ValueError):
                run.record_blocker("invalid_controller_hypothesis")
        raw_experiment = payload.get("experiment_spec")
        if isinstance(raw_experiment, dict):
            raw_experiment = cast(dict[str, Any], raw_experiment)
            try:
                run.add_experiment(
                    LabApplication._coerce_experiment_spec(raw_experiment)
                )
            except (KeyError, TypeError, ValueError):
                run.record_blocker("invalid_controller_experiment")


__all__ = [
    "DEFAULT_UNIT_REGISTRY",
    "AdaptiveController",
    "AdaptiveDecision",
    "AuthorityMode",
    "BrowserCell",
    "BrowserCellPolicy",
    "BrowserObserverView",
    "CalibrationResult",
    "ClaimRecord",
    "ElectricalSignalCell",
    "ElectricalSignalResult",
    "ElectricalSignalSpec",
    "ExperimentSpec",
    "HypothesisRecord",
    "Lab",
    "LabApplication",
    "LabBudget",
    "LabDossier",
    "LabEvent",
    "LabMissionSpec",
    "LabPolicy",
    "LabRun",
    "LabSession",
    "ODEIntegrationResult",
    "ObservationRecord",
    "PhysicalConstraint",
    "ProcessExecutionCell",
    "ReplayWriterLease",
    "SearchOperation",
    "SearchProgram",
    "SearchProgramExecutor",
    "SimulationCell",
    "SimulationSpec",
    "SourceRecord",
    "UnitDefinition",
    "UnitRegistry",
    "calibrate_simulation",
]
