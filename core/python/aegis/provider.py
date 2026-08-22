"""Provider routing, budgets, and runnable-input normalization."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any

from .contracts import (
    ProviderBudgetEvidence,
    ProviderBudgetRecord,
    ProviderRateLimitError,
    ProviderRouteRecord,
)
from .hashing import stable_hash


def provider_name(llm: Any) -> str | None:
    if llm is None:
        return None
    return getattr(llm, "model", None) or getattr(llm, "model_name", None) or type(llm).__name__


def normalize_fallback_providers(fallback_providers: Any) -> tuple[tuple[str | None, Any], ...]:
    if not fallback_providers:
        return ()
    normalized: list[tuple[str | None, Any]] = []
    for item in fallback_providers:
        if isinstance(item, tuple) and len(item) == 2:
            name, llm = item
            normalized.append((None if name is None else str(name), llm))
        else:
            normalized.append((provider_name(item), item))
    return tuple(normalized)


def normalize_provider_budgets(
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
            records.append(normalize_provider_budget_record(str(provider), raw, required_tokens=required_tokens))
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
            records.append(normalize_provider_budget_record(str(provider), raw, required_tokens=required_tokens))
    providers = [record.provider for record in records]
    if len(set(providers)) != len(providers):
        raise ValueError("provider budgets require unique provider names")
    return tuple(sorted(records, key=lambda record: record.provider))


def task_from_runnable_input(value: Any, fallback_task: str | None = None) -> str:
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
                return task_from_runnable_input(nested, fallback_task)
        messages = value.get("messages")
        if messages is not None:
            return task_from_messages(messages)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def task_from_messages(messages: Any) -> str:
    if isinstance(messages, (str, bytes)):
        return task_from_runnable_input(messages)
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("Agent.invoke messages input must be a non-empty list")
    last = messages[-1]
    if isinstance(last, Mapping):
        content = last.get("content")
        if content is not None:
            return task_from_runnable_input(content)
    return task_from_runnable_input(last)


def normalize_provider_budget_record(
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
    remaining_requests = normalize_nonnegative_int(remaining_requests, f"{provider}.remaining_requests")
    remaining_tokens = normalize_nonnegative_int(remaining_tokens, f"{provider}.remaining_tokens")
    reset_epoch_ms = normalize_nonnegative_int(reset_epoch_ms, f"{provider}.reset_epoch_ms")
    admitted = remaining_requests > 0 and remaining_tokens >= required_tokens
    rejection_reason = (
        "remaining_requests_exhausted"
        if remaining_requests <= 0
        else "remaining_tokens_insufficient"
        if remaining_tokens < required_tokens
        else ""
    )
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
        budget_hash=stable_hash(payload),
    )


def normalize_positive_int(value: Any, name: str) -> int:
    normalized = normalize_nonnegative_int(value, name)
    if normalized <= 0:
        raise ValueError(f"{name} must be positive")
    return normalized


def normalize_nonnegative_int(value: Any, name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if normalized < 0:
        raise ValueError(f"{name} must be non-negative")
    return normalized


async def invoke_with_provider_route(
    primary_llm: Any,
    primary_provider: str | None,
    fallback_providers: tuple[tuple[str | None, Any], ...],
    trust_level: str,
    task: str,
    provider_budgets: tuple[ProviderBudgetRecord, ...],
    required_tokens: int,
    **kwargs: Any,
) -> tuple[Any, str | None, ProviderRouteRecord, ProviderBudgetEvidence]:
    candidates = [
        (primary_provider or provider_name(primary_llm), primary_llm),
        *fallback_providers,
    ]
    budget_lookup = {record.provider: record for record in provider_budgets}
    attempted: list[str] = []
    throttled: list[str] = []
    skipped: list[str] = []
    last_rate_limit: Exception | None = None

    for index, (provider, llm) in enumerate(candidates):
        selected_name = provider or provider_name(llm) or "default"
        if provider_budgets:
            budget = budget_lookup.get(selected_name)
            if budget is None or not budget.admitted:
                skipped.append(selected_name)
                continue
        attempted.append(selected_name)
        try:
            output = await invoke_llm(llm, task, **kwargs)
        except Exception as exc:
            if not is_rate_limit_error(exc):
                raise
            throttled.append(selected_name)
            last_rate_limit = exc
            continue
        budget_evidence = provider_budget_evidence(
            trust_level=trust_level,
            provider_budgets=provider_budgets,
            required_tokens=required_tokens,
            selected_provider=selected_name,
            skipped_providers=tuple(skipped),
            throttled_providers=tuple(throttled),
        )
        route = provider_route_record(
            trust_level=trust_level,
            selected_provider=selected_name,
            attempted_providers=tuple(attempted),
            throttled_providers=tuple(throttled),
            fallback_used=index > 0,
            provider_budget_hash=budget_evidence.budget_evidence_hash,
        )
        return output, selected_name, route, budget_evidence

    message = "all providers throttled or budget-blocked"
    if last_rate_limit is not None:
        message = f"{message}: {last_rate_limit}"
    if skipped:
        message = f"{message}; skipped={','.join(skipped)}"
    raise ProviderRateLimitError(message)


async def invoke_llm(llm: Any, task: str, **kwargs: Any) -> Any:
    if llm is None:
        return {"status": "accepted", "task": task}
    if hasattr(llm, "ainvoke"):
        return await llm.ainvoke(task, **kwargs)
    if hasattr(llm, "invoke"):
        result = llm.invoke(task, **kwargs)
        return await result if inspect.isawaitable(result) else result
    if callable(llm):
        result = llm(task, **kwargs)
        return await result if inspect.isawaitable(result) else result
    raise TypeError("llm must be callable or expose invoke/ainvoke")


def is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, ProviderRateLimitError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429 or "429" in str(exc)


def provider_route_record(
    *,
    trust_level: str,
    selected_provider: str | None,
    attempted_providers: tuple[str, ...],
    throttled_providers: tuple[str, ...],
    fallback_used: bool,
    provider_budget_hash: str,
) -> ProviderRouteRecord:
    downgraded_model = fallback_used
    route_hash = stable_hash(
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


def provider_budget_evidence(
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
        "budgets": [provider_budget_record_payload(record) for record in provider_budgets],
        "truth_claim": False,
    }
    budget_ledger_hash = stable_hash(budget_payload)
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
        budget_evidence_hash=stable_hash(payload),
        budgets=provider_budgets,
    )


def provider_budget_record_payload(record: ProviderBudgetRecord) -> dict[str, Any]:
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
