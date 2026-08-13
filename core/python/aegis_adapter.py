from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .browser_live_collector import BrowserLiveCollectorArtifact, BrowserLiveCollectorProducer
    from .browser_runtime_adapter import (
        BrowserRuntimeCapture,
        BrowserRuntimeCollectorAdapter,
        BrowserRuntimeHotFirstCapture,
        HotBrowserEvidenceBatch,
    )
except ImportError:
    from browser_live_collector import BrowserLiveCollectorArtifact, BrowserLiveCollectorProducer
    from browser_runtime_adapter import (
        BrowserRuntimeCapture,
        BrowserRuntimeCollectorAdapter,
        BrowserRuntimeHotFirstCapture,
        HotBrowserEvidenceBatch,
    )


VALID_TRUST_LEVELS = {"DEV", "STAGING", "PROD"}


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
    def from_mapping(cls, data: dict[str, Any]) -> "HotCommitRecord":
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
    pass


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


@dataclass(frozen=True)
class AegisBrowserActionResult:
    schema: str
    capture: BrowserRuntimeCapture
    hot_evidence: HotBrowserEvidenceBatch
    trust_level: str
    trust_policy: TrustPolicySnapshot
    no_file_roundtrip_on_hot_path: bool
    browser_action_result_hash: str
    truth_claim: bool


@dataclass(frozen=True)
class AegisBrowserHotFirstActionResult:
    schema: str
    capture: BrowserRuntimeHotFirstCapture
    hot_evidence: HotBrowserEvidenceBatch
    trust_level: str
    trust_policy: TrustPolicySnapshot
    no_file_roundtrip_on_hot_path: bool
    hot_returned_before_cold_publish: bool
    browser_action_result_hash: str
    truth_claim: bool


class AegisAdapter:
    """Friendly Gateway adapter with Browser-Use/LangGraph-style run/invoke/batch entrypoints."""

    def __init__(
        self,
        task: str | None = None,
        *,
        llm: Any = None,
        model: str | None = None,
        trust_level: str | None = None,
        provider: str | None = None,
        fallback_providers: Any = None,
        provider_budgets: Any = None,
        required_tokens: int = 1,
        **_: Any,
    ) -> None:
        self.task = task
        self.llm = llm
        self.model = model
        self.trust_level = _normalize_trust_level(trust_level)
        self.trust_policy = trust_policy_snapshot(self.trust_level)
        self.provider = provider or _provider_name(llm)
        self.fallback_providers = _normalize_fallback_providers(fallback_providers)
        self.required_tokens = _normalize_positive_int(required_tokens, "required_tokens")
        self.provider_budgets = _normalize_provider_budgets(
            provider_budgets,
            required_tokens=self.required_tokens,
        )
        self._browser_sequence_number = 0
        self.last_result: AegisRunResult | None = None
        self.last_results: tuple[AegisRunResult, ...] = ()
        self.last_batch_results: tuple[AegisRunResult, ...] = ()

    async def run(self, task: str | None = None, **kwargs: Any) -> AegisRunResult:
        effective_task = task or self.task
        if not effective_task:
            raise ValueError("Agent.run requires a task")

        output, provider, provider_route, provider_budget = await _invoke_with_provider_route(
            self.llm,
            self.provider,
            self.fallback_providers,
            self.trust_level,
            effective_task,
            self.provider_budgets,
            self.required_tokens,
            **kwargs,
        )
        payload = _evidence_payload(
            effective_task,
            output,
            self.model,
            provider,
            self.trust_policy,
            provider_route,
            provider_budget,
        )
        hot_commit = commit_hot_evidence(payload, self.trust_level)
        return AegisRunResult(
            schema="aegis-friendly-gateway-run-v1",
            task=effective_task,
            output=output,
            hot_commit=hot_commit,
            trust_level=self.trust_level,
            trust_policy=self.trust_policy,
            provider=provider,
            provider_route=provider_route,
            provider_budget=provider_budget,
            truth_claim=False,
        )

    def run_sync(self, task: str | None = None, **kwargs: Any) -> AegisRunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(task, **kwargs))
        raise RuntimeError("run_sync cannot be called while an event loop is already running")

    async def ainvoke(self, input: Any = None, **kwargs: Any) -> Any:
        task = _task_from_runnable_input(input, self.task)
        result = await self.run(task, **kwargs)
        self.last_result = result
        self.last_results = (*self.last_results, result)
        return result.output

    def invoke(self, input: Any = None, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.ainvoke(input, **kwargs))
        raise RuntimeError("invoke cannot be called while an event loop is already running")

    async def abatch(self, inputs: Any, **kwargs: Any) -> list[Any]:
        if isinstance(inputs, (str, bytes)) or not hasattr(inputs, "__iter__"):
            raise TypeError("Agent.abatch requires an iterable of runnable inputs")
        start = len(self.last_results)
        outputs: list[Any] = []
        for item in inputs:
            outputs.append(await self.ainvoke(item, **kwargs))
        self.last_batch_results = self.last_results[start:]
        return outputs

    def batch(self, inputs: Any, **kwargs: Any) -> list[Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.abatch(inputs, **kwargs))
        raise RuntimeError("batch cannot be called while an event loop is already running")

    def __call__(self, input: Any = None, **kwargs: Any) -> Any:
        return self.invoke(input, **kwargs)

    async def capture_browser_action(
        self,
        *,
        browser_session: Any,
        action: Any,
        output_root: str | Path | None = None,
        run_id: int | None = None,
        action_id: int | None = None,
        sequence_number: int | None = None,
        producer: BrowserLiveCollectorProducer | None = None,
        hot_evidence_publisher: Any = None,
        hot_evidence_batch_publisher: Any = None,
    ) -> AegisBrowserActionResult:
        if sequence_number is None:
            self._browser_sequence_number += 1
            sequence_number = self._browser_sequence_number
        run_id = run_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-run-v1",
                "task": self.task,
                "model": self.model,
                "provider": self.provider,
            }
        )
        action_id = action_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-action-v1",
                "task": self.task,
                "sequence_number": sequence_number,
            }
        )
        producer = producer or BrowserLiveCollectorProducer(
            output_root or Path("artifacts") / "browser-runtime-captures"
        )
        publisher = hot_evidence_publisher
        batch_publisher = None
        if publisher is None:
            batch_publisher = hot_evidence_batch_publisher or (
                lambda artifacts: commit_hot_evidence_batch(artifacts, self.trust_level)
            )
        adapter = BrowserRuntimeCollectorAdapter(
            producer,
            hot_evidence_publisher=publisher,
            hot_evidence_batch_publisher=batch_publisher,
        )
        capture = await adapter.capture_action(
            browser_session=browser_session,
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            action=_bind_browser_action(action, browser_session),
        )
        return _browser_action_result(capture, self.trust_level, self.trust_policy)

    def capture_browser_action_sync(self, **kwargs: Any) -> AegisBrowserActionResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.capture_browser_action(**kwargs))
        raise RuntimeError("capture_browser_action_sync cannot be called while an event loop is already running")

    async def capture_browser_action_hot_first(
        self,
        *,
        browser_session: Any,
        action: Any,
        output_root: str | Path | None = None,
        run_id: int | None = None,
        action_id: int | None = None,
        sequence_number: int | None = None,
        producer: BrowserLiveCollectorProducer | None = None,
        hot_evidence_publisher: Any = None,
        hot_evidence_batch_publisher: Any = None,
    ) -> AegisBrowserHotFirstActionResult:
        if sequence_number is None:
            self._browser_sequence_number += 1
            sequence_number = self._browser_sequence_number
        run_id = run_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-run-v1",
                "task": self.task,
                "model": self.model,
                "provider": self.provider,
            }
        )
        action_id = action_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-action-v1",
                "task": self.task,
                "sequence_number": sequence_number,
            }
        )
        producer = producer or BrowserLiveCollectorProducer(
            output_root or Path("artifacts") / "browser-runtime-captures"
        )
        publisher = hot_evidence_publisher
        batch_publisher = None
        if publisher is None:
            batch_publisher = hot_evidence_batch_publisher or (
                lambda artifacts: commit_hot_evidence_batch(artifacts, self.trust_level)
            )
        adapter = BrowserRuntimeCollectorAdapter(
            producer,
            hot_evidence_publisher=publisher,
            hot_evidence_batch_publisher=batch_publisher,
        )
        capture = await adapter.capture_action_hot_first(
            browser_session=browser_session,
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            action=_bind_browser_action(action, browser_session),
        )
        return _browser_hot_first_action_result(capture, self.trust_level, self.trust_policy)

    def capture_browser_action_hot_first_sync(self, **kwargs: Any) -> AegisBrowserHotFirstActionResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.capture_browser_action_hot_first(**kwargs))
        raise RuntimeError("capture_browser_action_hot_first_sync cannot be called while an event loop is already running")


Agent = AegisAdapter


def trust_policy_snapshot(trust_level: str | None = None) -> TrustPolicySnapshot:
    level = _normalize_trust_level(trust_level)
    physical_witness_required = level == "PROD"
    fail_closed = level == "PROD"
    degraded_hot_evidence_allowed = level == "DEV"
    rust_extension_required = level != "DEV"
    dual_approval_required = level == "PROD"
    missing_artifact_policy = {
        "DEV": "warn_continue",
        "STAGING": "record_gap_without_truth_claim",
        "PROD": "fail_closed",
    }[level]
    payload = {
        "schema": "aegis-friendly-trust-policy-v1",
        "trust_level": level,
        "physical_witness_required": physical_witness_required,
        "fail_closed": fail_closed,
        "degraded_hot_evidence_allowed": degraded_hot_evidence_allowed,
        "rust_extension_required": rust_extension_required,
        "missing_artifact_policy": missing_artifact_policy,
        "dual_approval_required": dual_approval_required,
        "truth_claim": False,
    }
    return TrustPolicySnapshot(
        schema="aegis-friendly-trust-policy-v1",
        truth_claim=False,
        trust_level=level,
        physical_witness_required=physical_witness_required,
        fail_closed=fail_closed,
        degraded_hot_evidence_allowed=degraded_hot_evidence_allowed,
        rust_extension_required=rust_extension_required,
        missing_artifact_policy=missing_artifact_policy,
        dual_approval_required=dual_approval_required,
        trust_policy_hash=_stable_hash(payload),
    )


def commit_hot_evidence(payload: bytes, trust_level: str | None = None) -> HotCommitRecord:
    level = _normalize_trust_level(trust_level)
    try:
        import aegis_nerve  # type: ignore
    except Exception as exc:
        if level != "DEV":
            raise RuntimeError("Rust aegis_nerve extension is required outside DEV trust level") from exc
        digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
        storage_ref = hashlib.blake2b(
            b"aegis-dev-hot-storage-ref-v1" + digest.encode("ascii"),
            digest_size=32,
        ).hexdigest()
        return HotCommitRecord(
            schema="aegis-hot-arena-commit-v1",
            truth_claim=False,
            verifier="python-dev-fallback",
            byte_len=len(payload),
            artifact_hash=digest,
            storage_ref_hash=storage_ref,
            trust_level=level,
            admission="degraded_warning",
            physical_witness_required=False,
            fail_closed=False,
            handle_valid=True,
            degraded_reason="missing-aegis-nerve-extension",
        )

    raw = aegis_nerve.aegis_hot_commit(payload, level)
    return HotCommitRecord.from_mapping(json.loads(raw))


def commit_hot_evidence_batch(
    artifacts: list[BrowserLiveCollectorArtifact],
    trust_level: str | None = None,
) -> dict[str, Any] | str:
    level = _normalize_trust_level(trust_level)
    payloads = [artifact.bytes for artifact in artifacts]
    if not payloads or any(not payload for payload in payloads):
        raise ValueError("hot evidence batch requires non-empty browser artifacts")
    try:
        import aegis_nerve  # type: ignore
    except Exception as exc:
        if level != "DEV":
            raise RuntimeError("Rust aegis_nerve extension is required outside DEV trust level") from exc
        commits: list[dict[str, Any]] = []
        total_bytes = 0
        batch_hasher = hashlib.blake2b(digest_size=32)
        batch_hasher.update(b"aegis-hot-arena-commit-batch-v1")
        batch_hasher.update(len(payloads).to_bytes(8, "little"))
        for index, payload in enumerate(payloads):
            digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
            storage_ref = hashlib.blake2b(
                b"aegis-dev-hot-arena-batch-storage-ref-v1"
                + index.to_bytes(8, "little")
                + digest.encode("ascii"),
                digest_size=32,
            ).hexdigest()
            total_bytes += len(payload)
            batch_hasher.update(index.to_bytes(8, "little"))
            batch_hasher.update(len(payload).to_bytes(8, "little"))
            batch_hasher.update(digest.encode("ascii"))
            batch_hasher.update(storage_ref.encode("ascii"))
            commits.append(
                {
                    "schema": "aegis-hot-arena-commit-v1",
                    "truth_claim": False,
                    "verifier": "python-dev-batch-fallback",
                    "index": index,
                    "byte_len": len(payload),
                    "artifact_hash": digest,
                    "storage_ref_hash": storage_ref,
                    "trust_level": level,
                    "admission": "degraded_warning",
                    "physical_witness_required": False,
                    "fail_closed": False,
                    "handle_valid": True,
                }
            )
        return {
            "schema": "aegis-hot-arena-commit-batch-v1",
            "truth_claim": False,
            "verifier": "python-dev-batch-fallback",
            "trust_level": level,
            "artifact_count": len(commits),
            "total_bytes": total_bytes,
            "no_file_roundtrip_on_hot_path": True,
            "batch_digest": batch_hasher.hexdigest(),
            "arena_live_bytes": total_bytes,
            "arena_live_slots": len(commits),
            "arena_total_commits": len(commits),
            "arena_total_degraded_commits": len(commits),
            "commits": commits,
        }

    return aegis_nerve.aegis_hot_commit_batch(payloads, level)


def _normalize_trust_level(value: str | None) -> str:
    level = (value or os.environ.get("AEGIS_TRUST_LEVEL") or "PROD").strip().upper()
    if level not in VALID_TRUST_LEVELS:
        raise ValueError("AEGIS_TRUST_LEVEL must be DEV, STAGING, or PROD")
    return level


def _provider_name(llm: Any) -> str | None:
    if llm is None:
        return None
    return getattr(llm, "model", None) or getattr(llm, "model_name", None) or type(llm).__name__


def _normalize_fallback_providers(fallback_providers: Any) -> tuple[tuple[str | None, Any], ...]:
    if not fallback_providers:
        return ()
    normalized = []
    for item in fallback_providers:
        if isinstance(item, tuple) and len(item) == 2:
            name, llm = item
            normalized.append((None if name is None else str(name), llm))
        else:
            normalized.append((_provider_name(item), item))
    return tuple(normalized)


def _normalize_provider_budgets(
    provider_budgets: Any,
    *,
    required_tokens: int,
) -> tuple[ProviderBudgetRecord, ...]:
    if not provider_budgets:
        return ()
    records: list[ProviderBudgetRecord] = []
    if isinstance(provider_budgets, Mapping):
        items = sorted(provider_budgets.items(), key=lambda item: str(item[0]))
        for provider, raw in items:
            records.append(
                _provider_budget_record_from_raw(
                    str(provider),
                    raw,
                    required_tokens=required_tokens,
                )
            )
    else:
        for raw in provider_budgets:
            if isinstance(raw, ProviderBudgetRecord):
                records.append(raw)
                continue
            if not isinstance(raw, Mapping):
                raise TypeError("provider budget entries must be mappings or ProviderBudgetRecord")
            provider = raw.get("provider") or raw.get("provider_name")
            if provider is None:
                provider_id = raw.get("provider_id")
                model_name = raw.get("model") or raw.get("model_name")
                if provider_id is not None and model_name is not None:
                    provider = f"{provider_id}/{model_name}"
            if provider is None:
                raise ValueError("provider budget entry requires provider")
            records.append(
                _provider_budget_record_from_raw(
                    str(provider),
                    raw,
                    required_tokens=required_tokens,
                )
            )
    providers = [record.provider for record in records]
    if len(set(providers)) != len(providers):
        raise ValueError("provider budgets require unique provider names")
    return tuple(sorted(records, key=lambda record: record.provider))


def _task_from_runnable_input(value: Any, fallback_task: str | None = None) -> str:
    if value is None:
        if fallback_task:
            return fallback_task
        raise ValueError("Agent.invoke requires an input or configured task")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("input", "task", "query", "prompt", "content"):
            nested = value.get(key)
            if nested is not None:
                return _task_from_runnable_input(nested, fallback_task)
        messages = value.get("messages")
        if messages is not None:
            return _task_from_messages(messages)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _task_from_messages(messages: Any) -> str:
    if isinstance(messages, (str, bytes)):
        return _task_from_runnable_input(messages)
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("Agent.invoke messages input must be a non-empty list")
    last = messages[-1]
    if isinstance(last, Mapping):
        content = last.get("content")
        if content is not None:
            return _task_from_runnable_input(content)
    return _task_from_runnable_input(last)


def _provider_budget_record_from_raw(
    provider: str,
    raw: Any,
    *,
    required_tokens: int,
) -> ProviderBudgetRecord:
    if isinstance(raw, ProviderBudgetRecord):
        return raw
    if isinstance(raw, Mapping):
        remaining_requests = raw.get("remaining_requests", raw.get("requests", 0))
        remaining_tokens = raw.get("remaining_tokens", raw.get("tokens", 0))
        reset_epoch_ms = raw.get("reset_epoch_ms", 0)
    elif isinstance(raw, (tuple, list)) and len(raw) in (2, 3):
        remaining_requests = raw[0]
        remaining_tokens = raw[1]
        reset_epoch_ms = raw[2] if len(raw) == 3 else 0
    else:
        raise TypeError("provider budget value must be a mapping or tuple")
    remaining_requests = _normalize_nonnegative_int(
        remaining_requests,
        f"{provider}.remaining_requests",
    )
    remaining_tokens = _normalize_nonnegative_int(
        remaining_tokens,
        f"{provider}.remaining_tokens",
    )
    reset_epoch_ms = _normalize_nonnegative_int(reset_epoch_ms, f"{provider}.reset_epoch_ms")
    admitted = remaining_requests > 0 and remaining_tokens >= required_tokens
    if remaining_requests <= 0:
        rejection_reason = "remaining_requests_exhausted"
    elif remaining_tokens < required_tokens:
        rejection_reason = "remaining_tokens_insufficient"
    else:
        rejection_reason = ""
    payload = {
        "schema": "aegis-friendly-provider-budget-record-v1",
        "provider": provider,
        "remaining_requests": remaining_requests,
        "remaining_tokens": remaining_tokens,
        "reset_epoch_ms": reset_epoch_ms,
        "required_tokens": required_tokens,
        "admitted": admitted,
        "rejection_reason": rejection_reason,
        "truth_claim": False,
    }
    return ProviderBudgetRecord(
        schema="aegis-friendly-provider-budget-record-v1",
        truth_claim=False,
        provider=provider,
        remaining_requests=remaining_requests,
        remaining_tokens=remaining_tokens,
        reset_epoch_ms=reset_epoch_ms,
        admitted=admitted,
        rejection_reason=rejection_reason,
        budget_hash=_stable_hash(payload),
    )


def _normalize_positive_int(value: Any, name: str) -> int:
    normalized = _normalize_nonnegative_int(value, name)
    if normalized <= 0:
        raise ValueError(f"{name} must be positive")
    return normalized


def _normalize_nonnegative_int(value: Any, name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if normalized < 0:
        raise ValueError(f"{name} must be non-negative")
    return normalized


async def _invoke_with_provider_route(
    primary_llm: Any,
    primary_provider: str | None,
    fallback_providers: tuple[tuple[str | None, Any], ...],
    trust_level: str,
    task: str,
    provider_budgets: tuple[ProviderBudgetRecord, ...],
    required_tokens: int,
    **kwargs: Any,
) -> tuple[Any, str | None, ProviderRouteRecord, ProviderBudgetEvidence]:
    candidates = ((primary_provider or _provider_name(primary_llm), primary_llm),) + fallback_providers
    budget_lookup = {record.provider: record for record in provider_budgets}
    attempted: list[str] = []
    throttled: list[str] = []
    skipped: list[str] = []
    last_rate_limit: Exception | None = None

    for index, (provider, llm) in enumerate(candidates):
        provider_name = provider or _provider_name(llm) or "default"
        if provider_budgets:
            budget = budget_lookup.get(provider_name)
            if budget is None or not budget.admitted:
                skipped.append(provider_name)
                continue
        attempted.append(provider_name)
        try:
            output = await _invoke_llm(llm, task, **kwargs)
        except Exception as exc:
            if not _is_rate_limit_error(exc):
                raise
            throttled.append(provider_name)
            last_rate_limit = exc
            continue

        budget_evidence = _provider_budget_evidence(
            trust_level=trust_level,
            provider_budgets=provider_budgets,
            required_tokens=required_tokens,
            selected_provider=provider_name,
            skipped_providers=tuple(skipped),
            throttled_providers=tuple(throttled),
        )
        route = _provider_route_record(
            trust_level=trust_level,
            selected_provider=provider_name,
            attempted_providers=tuple(attempted),
            throttled_providers=tuple(throttled),
            fallback_used=index > 0,
            provider_budget_hash=budget_evidence.budget_evidence_hash,
        )
        return output, provider_name, route, budget_evidence

    message = "all providers throttled or budget-blocked"
    if last_rate_limit is not None:
        message = f"{message}: {last_rate_limit}"
    if skipped:
        message = f"{message}; skipped={','.join(skipped)}"
    raise ProviderRateLimitError(message)


async def _invoke_llm(llm: Any, task: str, **kwargs: Any) -> Any:
    if llm is None:
        return {"status": "accepted", "task": task}
    if hasattr(llm, "ainvoke"):
        return await llm.ainvoke(task, **kwargs)
    if hasattr(llm, "invoke"):
        result = llm.invoke(task, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    if callable(llm):
        result = llm(task, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    raise TypeError("llm must be callable or expose invoke/ainvoke")


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, ProviderRateLimitError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    return "429" in str(exc)


def _provider_route_record(
    *,
    trust_level: str,
    selected_provider: str | None,
    attempted_providers: tuple[str, ...],
    throttled_providers: tuple[str, ...],
    fallback_used: bool,
    provider_budget_hash: str,
) -> ProviderRouteRecord:
    downgraded_model = fallback_used
    route_hash = _stable_route_hash(
        {
            "schema": "aegis-friendly-provider-route-v1",
            "trust_level": trust_level,
            "selected_provider": selected_provider,
            "attempted_providers": attempted_providers,
            "throttled_providers": throttled_providers,
            "fallback_used": fallback_used,
            "downgraded_model": downgraded_model,
            "provider_budget_hash": provider_budget_hash,
        }
    )
    return ProviderRouteRecord(
        schema="aegis-friendly-provider-route-v1",
        truth_claim=False,
        trust_level=trust_level,
        selected_provider=selected_provider,
        attempted_providers=attempted_providers,
        throttled_providers=throttled_providers,
        fallback_used=fallback_used,
        downgraded_model=downgraded_model,
        throttled_provider_count=len(throttled_providers),
        provider_budget_hash=provider_budget_hash,
        route_hash=route_hash,
    )


def _stable_route_hash(payload: dict[str, Any]) -> str:
    return _stable_hash(payload)


def _provider_budget_evidence(
    *,
    trust_level: str,
    provider_budgets: tuple[ProviderBudgetRecord, ...],
    required_tokens: int,
    selected_provider: str | None,
    skipped_providers: tuple[str, ...],
    throttled_providers: tuple[str, ...],
) -> ProviderBudgetEvidence:
    budget_payload = {
        "schema": "aegis-friendly-provider-budget-ledger-v1",
        "budgets": [_provider_budget_record_payload(record) for record in provider_budgets],
        "truth_claim": False,
    }
    budget_ledger_hash = _stable_hash(budget_payload)
    feedback_kind = "http_429" if throttled_providers else "none"
    payload = {
        "schema": "aegis-friendly-provider-budget-evidence-v1",
        "trust_level": trust_level,
        "budgeted": bool(provider_budgets),
        "required_tokens": required_tokens,
        "provider_count": len(provider_budgets),
        "admitted_provider_count": sum(1 for record in provider_budgets if record.admitted),
        "skipped_providers": skipped_providers,
        "throttled_provider_count": len(throttled_providers),
        "selected_provider": selected_provider,
        "feedback_kind": feedback_kind,
        "budget_ledger_hash": budget_ledger_hash,
        "truth_claim": False,
    }
    return ProviderBudgetEvidence(
        schema="aegis-friendly-provider-budget-evidence-v1",
        truth_claim=False,
        trust_level=trust_level,
        budgeted=bool(provider_budgets),
        required_tokens=required_tokens,
        provider_count=len(provider_budgets),
        admitted_provider_count=sum(1 for record in provider_budgets if record.admitted),
        skipped_providers=skipped_providers,
        throttled_provider_count=len(throttled_providers),
        selected_provider=selected_provider,
        feedback_kind=feedback_kind,
        budget_ledger_hash=budget_ledger_hash,
        budget_evidence_hash=_stable_hash(payload),
        budgets=provider_budgets,
    )


def _provider_budget_record_payload(record: ProviderBudgetRecord) -> dict[str, Any]:
    return {
        "schema": record.schema,
        "truth_claim": record.truth_claim,
        "provider": record.provider,
        "remaining_requests": record.remaining_requests,
        "remaining_tokens": record.remaining_tokens,
        "reset_epoch_ms": record.reset_epoch_ms,
        "admitted": record.admitted,
        "rejection_reason": record.rejection_reason,
        "budget_hash": record.budget_hash,
    }


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=32).hexdigest()


def _stable_u128(payload: dict[str, Any]) -> int:
    value = int(_stable_hash(payload)[:32], 16)
    return value if value > 0 else 1


def _bind_browser_action(action: Any, browser_session: Any) -> Any:
    if not callable(action):
        raise TypeError("browser action must be callable")
    if _accepts_browser_session(action):
        return lambda: action(browser_session)
    return action


def _accepts_browser_session(action: Any) -> bool:
    try:
        signature = inspect.signature(action)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
    return False


def _browser_action_result(
    capture: BrowserRuntimeCapture,
    trust_level: str,
    trust_policy: TrustPolicySnapshot,
) -> AegisBrowserActionResult:
    hot_evidence = capture.hot_evidence
    if hot_evidence is None:
        raise RuntimeError("friendly browser action capture requires hot evidence")
    payload = {
        "schema": "aegis-friendly-browser-action-result-v1",
        "run_id": hot_evidence.run_id,
        "action_id": hot_evidence.action_id,
        "sequence_number": hot_evidence.sequence_number,
        "hot_evidence_batch_digest": hot_evidence.batch_digest,
        "producer_metadata_path": str(capture.producer_run.metadata_path),
        "trust_level": trust_level,
        "trust_policy_hash": trust_policy.trust_policy_hash,
        "no_file_roundtrip_on_hot_path": hot_evidence.no_file_roundtrip_on_hot_path,
        "truth_claim": False,
    }
    return AegisBrowserActionResult(
        schema="aegis-friendly-browser-action-result-v1",
        capture=capture,
        hot_evidence=hot_evidence,
        trust_level=trust_level,
        trust_policy=trust_policy,
        no_file_roundtrip_on_hot_path=hot_evidence.no_file_roundtrip_on_hot_path,
        browser_action_result_hash=_stable_hash(payload),
        truth_claim=False,
    )


def _browser_hot_first_action_result(
    capture: BrowserRuntimeHotFirstCapture,
    trust_level: str,
    trust_policy: TrustPolicySnapshot,
) -> AegisBrowserHotFirstActionResult:
    payload = {
        "schema": "aegis-friendly-browser-hot-first-action-result-v1",
        "trust_level": trust_level,
        "trust_policy_hash": trust_policy.trust_policy_hash,
        "hot_evidence_batch_digest": capture.hot_evidence.batch_digest,
        "cold_publish_handle_hash": capture.cold_publish.cold_publish_handle_hash,
        "hot_returned_before_cold_publish": capture.cold_publish.hot_returned_before_cold_publish,
        "run_id": capture.hot_evidence.run_id,
        "action_id": capture.hot_evidence.action_id,
        "sequence_number": capture.hot_evidence.sequence_number,
        "truth_claim": False,
    }
    return AegisBrowserHotFirstActionResult(
        schema="aegis-friendly-browser-hot-first-action-result-v1",
        capture=capture,
        hot_evidence=capture.hot_evidence,
        trust_level=trust_level,
        trust_policy=trust_policy,
        no_file_roundtrip_on_hot_path=capture.hot_evidence.no_file_roundtrip_on_hot_path,
        hot_returned_before_cold_publish=capture.cold_publish.hot_returned_before_cold_publish,
        browser_action_result_hash=_stable_hash(payload),
        truth_claim=False,
    )


def _evidence_payload(
    task: str,
    output: Any,
    model: str | None,
    provider: str | None,
    trust_policy: TrustPolicySnapshot,
    provider_route: ProviderRouteRecord,
    provider_budget: ProviderBudgetEvidence,
) -> bytes:
    return json.dumps(
        {
            "schema": "aegis-friendly-gateway-evidence-v1",
            "task": task,
            "output": output,
            "model": model,
            "provider": provider,
            "trust_policy_hash": trust_policy.trust_policy_hash,
            "trust_missing_artifact_policy": trust_policy.missing_artifact_policy,
            "provider_route_hash": provider_route.route_hash,
            "provider_fallback_used": provider_route.fallback_used,
            "provider_throttled_count": provider_route.throttled_provider_count,
            "provider_budget_hash": provider_budget.budget_evidence_hash,
            "provider_budgeted": provider_budget.budgeted,
            "provider_budget_skipped_count": len(provider_budget.skipped_providers),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class AegisAgent(AegisAdapter):
    """
    Friendly DX wrapper around AegisAdapter.
    """
    def __init__(self, *args: Any, max_retries: int = 3, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries

    async def run(self, task: str | None = None, **kwargs: Any) -> AegisRunResult:
        retries = 0
        while True:
            try:
                return await super().run(task, **kwargs)
            except ProviderRateLimitError as exc:
                print("\u26a0\ufe0f Provider bi gioi han, AEGIS dang tu dong chuyen sang du phong...")
                retries += 1
                if retries >= self.max_retries:
                    raise exc
                await asyncio.sleep(2 ** retries)


# ─────────────────────────────────────────────────────────────────────────
# LearningManager — Python DX Bridge for the AEGIS Learning Loop (Sprint 5)
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LearningStats:
    """Snapshot of the AEGIS learning loop state."""
    schema: str
    ledger_events: int
    improved_skills: int
    nudged_memories: int
    indexed_sessions: int
    user_models: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "LearningStats":
        return cls(
            schema=str(data["schema"]),
            ledger_events=int(data["ledger_events"]),
            improved_skills=int(data["improved_skills"]),
            nudged_memories=int(data["nudged_memories"]),
            indexed_sessions=int(data["indexed_sessions"]),
            user_models=int(data["user_models"]),
        )


@dataclass(frozen=True)
class SessionSearchCandidate:
    """A single cross-session recall candidate (CandidateOnlyGate enforced)."""
    evidence_ref_hash: str
    segment_id: int
    tier: str
    score: float
    epoch_hash: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SessionSearchCandidate":
        return cls(
            evidence_ref_hash=str(data.get("evidence_ref_hash", "")),
            segment_id=int(data.get("segment_id", 0)),
            tier=str(data.get("tier", "ColdVectorExpansion")),
            score=float(data.get("score", 0.0)),
            epoch_hash=str(data.get("epoch_hash", "")),
        )


@dataclass(frozen=True)
class SessionSearchResult:
    """Container for cross-session search results."""
    schema: str
    query: str
    top_k: int
    count: int
    results: tuple[SessionSearchCandidate, ...]
    tier: str
    gate: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SessionSearchResult":
        raw_results = data.get("results", [])
        candidates = tuple(
            SessionSearchCandidate.from_mapping(r)
            for r in raw_results
            if isinstance(r, dict)
        )
        return cls(
            schema=str(data["schema"]),
            query=str(data.get("query", "")),
            top_k=int(data.get("top_k", 0)),
            count=len(candidates),
            results=candidates,
            tier=str(data.get("tier", "ColdVectorExpansion")),
            gate=str(data.get("gate", "CandidateOnly")),
        )


class LearningManager:
    """Friendly Gateway to the AEGIS self-improving learning loop.

    Delegates to the Rust PyO3 bindings exposed in ``aegis_nerve``.
    All results are cryptographically-bound candidates — never truth claims.

    Usage::

        mgr = LearningManager()
        stats = mgr.get_stats()
        results = mgr.search_past("BLAKE3 hashing", top_k=3)
        mgr.sync_memory(session_id="0xabcd")
    """

    def __init__(self, rust_bridge: Any = None) -> None:
        """Create a LearningManager.

        Args:
            rust_bridge: Optional pre-imported aegis_nerve module.
                         If None, the manager attempts a lazy import.
        """
        self._bridge = rust_bridge

    @property
    def _native(self) -> Any:
        """Lazy-load the Rust PyO3 bridge (``import aegis_nerve``)."""
        if self._bridge is None:
            import aegis_nerve as bridge  # type: ignore[import-untyped]
            self._bridge = bridge
        return self._bridge

    def get_stats(self, *, ledger_json: str = "{}") -> LearningStats:
        """Get current learning loop statistics.

        Args:
            ledger_json: Optional serialised LearningLedger JSON.
                         Default ``"{}"`` yields zero counts.

        Returns:
            A ``LearningStats`` snapshot with event counts.
        """
        raw = self._native.aegis_get_learning_stats(ledger_json)
        data: dict[str, Any] = json.loads(raw)
        return LearningStats.from_mapping(data)

    def search_past(self, query: str, top_k: int = 5) -> SessionSearchResult:
        """Search past sessions.

        Returns cryptographically-bound candidate refs — **not truth**.
        Results are ``CandidateOnlyGate``-tiered and cannot be used as
        ``PhysicalWitness`` or ``PolicyApproval``.

        Args:
            query: Search query string (non-empty).
            top_k: Maximum number of results (1-100, default 5).

        Returns:
            ``SessionSearchResult`` with a ``.results`` tuple of
            ``SessionSearchCandidate`` objects.
        """
        if not query or not query.strip():
            raise ValueError("search_past requires a non-empty query")
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100")

        raw = self._native.aegis_search_past_sessions(query, top_k)
        data: dict[str, Any] = json.loads(raw)
        return SessionSearchResult.from_mapping(data)

    def index_session(
        self,
        session_id: int | str,
        content: str,
        timestamp: int | None = None,
    ) -> str:
        """Index a session transcript for cross-session recall.

        Args:
            session_id: Session ID (integer or hex string)
            content: Session content or transcript to index
            timestamp: Milliseconds since epoch (default: current time)

        Returns:
            The content hash of the indexed session as a hex string.
        """
        if not content or not content.strip():
            raise ValueError("content must be non-empty")

        # Parse session_id
        if isinstance(session_id, str):
            if session_id.startswith("0x"):
                sid = int(session_id, 16)
            else:
                sid = int(session_id)
        else:
            sid = int(session_id)

        if timestamp is None:
            import time
            timestamp = int(time.time() * 1000)

        raw = self._native.aegis_index_session(sid, content, timestamp)
        return raw

    def sync_memory(self, session_id: str = "0x1") -> dict[str, Any]:
        """Trigger a background memory nudge and PAV crystallisation.

        The actual processing happens async in the Rust engine.
        This method returns a JSON acknowledgment immediately.

        Args:
            session_id: Hex (``0x...``) or decimal session identifier.

        Returns:
            A dict with ``nudge_accepted`` and metadata.
        """
        raw = self._native.aegis_trigger_memory_nudge(session_id)
        return json.loads(raw)
