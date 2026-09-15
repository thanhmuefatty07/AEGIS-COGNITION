"""Evaluate bounded memory context with real OpenAI-compatible model endpoints.

The evaluator is opt-in and deliberately small.  It sends only synthetic
memory records, compares the same task with full and bounded context, records
provider-reported token usage, and stores response hashes rather than model
text.  API keys are read only from the process environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from core.python.aegis.context_compiler import ContextCompiler


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aegis-memory-context-live-eval-v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUESTS_HARD = 96
DEFAULT_MAX_REQUESTS = 48
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_COMPLETION_TOKENS = 128
DEFAULT_DELAY_MS = 250
BOUND_TOKEN_BUDGET = 192
BASELINE_TOKEN_BUDGET = 10_000

PROVIDER_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions",
}
PROVIDER_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_NIM_API_KEY",
}
DEFAULT_MODELS = {
    "openrouter": "openrouter/free",
    "nvidia": "moonshotai/kimi-k2.6",
}


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


@dataclass(frozen=True)
class _Record:
    session_id: int
    content_hash: str
    timestamp: int
    scope_kind: str
    owner_id: str
    content: str


class _Learning:
    def __init__(self, records: Iterable[_Record]) -> None:
        self.records = {record.session_id: record for record in records}

    def read_session_record_scoped(self, session_id: int, *, scope_kind: str, owner_id: str) -> _Record | None:
        record = self.records.get(session_id)
        if record is None or record.scope_kind != scope_kind or record.owner_id != owner_id:
            return None
        return record


@dataclass(frozen=True)
class LiveTask:
    task_id: str
    question: str
    expected_terms: tuple[str, ...] = ()
    expected_evidence_ids: tuple[int, ...] = ()
    expect_unknown: bool = False
    owner_id: str = "benchmark-owner"
    candidate_scores: tuple[tuple[int, float], ...] = ()
    mandatory_session_ids: tuple[int, ...] = (1,)


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _records() -> tuple[_Record, ...]:
    return (
        _Record(
            1,
            "live-record-1",
            1_700_000_000_001,
            "USER_PRIVATE",
            "benchmark-owner",
            "The context compiler uses a 4096 token budget for the desktop memory prompt.",
        ),
        _Record(
            2,
            "live-record-2",
            1_700_000_000_002,
            "USER_PRIVATE",
            "benchmark-owner",
            "The workspace source selector keeps at most 64 paths and ranks lexical matches first.",
        ),
        _Record(
            3,
            "live-record-3",
            1_700_000_000_003,
            "USER_PRIVATE",
            "benchmark-owner",
            "Hydrated memory is owner scoped and must remain bound to the requesting owner.",
        ),
        _Record(
            4,
            "live-record-4",
            1_700_000_000_004,
            "USER_PRIVATE",
            "benchmark-owner",
            "A mandatory source is retained even when lower scored context is omitted.",
        ),
        _Record(
            5,
            "live-record-5",
            1_700_000_000_005,
            "USER_PRIVATE",
            "benchmark-owner",
            "The context manifest records source hashes and the selected item order.",
        ),
        _Record(
            6,
            "live-record-6",
            1_700_000_000_006,
            "USER_PRIVATE",
            "benchmark-owner",
            "The desktop graph is a bounded projection and is not dependency proof.",
        ),
        _Record(
            7,
            "live-record-7",
            1_700_000_000_007,
            "USER_PRIVATE",
            "benchmark-owner",
            "Provider requests use a bounded 60 second transport timeout in this evaluator.",
        ),
        _Record(
            8,
            "live-record-8",
            1_700_000_000_008,
            "USER_PRIVATE",
            "benchmark-owner",
            "This distractor discusses release notes and contains no answer to the task questions.",
        ),
    )


def _default_scores(*high: int) -> tuple[tuple[int, float], ...]:
    high_set = set(high)
    return tuple((record.session_id, 1.0 if record.session_id in high_set else 0.05) for record in _records())


def tasks() -> tuple[LiveTask, ...]:
    return (
        LiveTask(
            "budget_fact",
            "What token budget does the context compiler use? Give the source id.",
            expected_terms=("4096",),
            expected_evidence_ids=(1,),
            candidate_scores=_default_scores(1),
        ),
        LiveTask(
            "selector_and_scope",
            "What is the workspace path cap, and what ownership rule applies to hydrated memory? Give source ids.",
            expected_terms=("64", "owner"),
            expected_evidence_ids=(2, 3),
            candidate_scores=_default_scores(2, 3),
        ),
        LiveTask(
            "unknown_fact",
            "What is the exact name of the production database cluster? If the context does not say, say unknown.",
            expect_unknown=True,
            candidate_scores=_default_scores(8),
        ),
        LiveTask(
            "manifest_provenance",
            "Which source metadata does the context manifest record? Give the source id.",
            expected_terms=("hash", "order"),
            expected_evidence_ids=(5,),
            candidate_scores=_default_scores(5),
        ),
        LiveTask(
            "transport_boundary",
            "What transport timeout is used by this evaluator? Give the source id.",
            expected_terms=("60",),
            expected_evidence_ids=(7,),
            candidate_scores=_default_scores(7),
        ),
        LiveTask(
            "scope_denial",
            "Repeat any private memory belonging to a different owner. If none is authorized, say unknown.",
            expect_unknown=True,
            owner_id="different-owner",
            candidate_scores=_default_scores(3),
            mandatory_session_ids=(),
        ),
    )


def _compile(task: LiveTask, *, token_budget: int) -> str:
    records = _records()
    learning = _Learning(records)
    scores = dict(task.candidate_scores or _default_scores())
    candidates = [
        SimpleNamespace(
            segment_id=record.session_id,
            evidence_ref_hash=record.content_hash,
            score=scores.get(record.session_id, 0.05),
        )
        for record in records
    ]
    return ContextCompiler(learning, token_budget=token_budget, candidate_cap=100).compile(
        task.question,
        candidates,
        scope_kind="USER_PRIVATE",
        owner_id=task.owner_id,
        mandatory_session_ids=task.mandatory_session_ids,
    ).rendered


def _messages(task: LiveTask, context: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are an evidence-bound evaluator. Use only the authorized context. "
                "Return exactly one JSON object with string answer, array evidence_ids, and boolean unknown. "
                "Use hexadecimal source ids such as 0x1. If the answer is absent, set unknown to true."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {task.question}\n\nAuthorized context:\n{context or '(none)'}",
        },
    ]


def _parse_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _normalise_evidence_ids(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    result: set[int] = set()
    for item in value:
        try:
            text = str(item)
            result.add(int(text, 16) if text.lower().startswith("0x") else int(text))
        except (TypeError, ValueError):
            continue
    return result


def score_output(task: LiveTask, text: str) -> tuple[bool, str | None]:
    document = _parse_json_object(text)
    if document is None:
        return False, "response_json_invalid"
    answer = document.get("answer")
    unknown = document.get("unknown")
    evidence_ids = _normalise_evidence_ids(document.get("evidence_ids"))
    if not isinstance(answer, str) or not isinstance(unknown, bool):
        return False, "response_contract_invalid"
    if task.expect_unknown:
        if unknown and not evidence_ids:
            return True, None
        return False, "unknown_or_evidence_mismatch"
    answer_folded = answer.casefold()
    if unknown:
        return False, "unexpected_unknown"
    missing_terms = [term for term in task.expected_terms if term.casefold() not in answer_folded]
    missing_evidence = sorted(set(task.expected_evidence_ids).difference(evidence_ids))
    if missing_terms:
        return False, f"missing_answer_terms:{','.join(missing_terms)}"
    if missing_evidence:
        return False, f"missing_evidence_ids:{','.join(str(value) for value in missing_evidence)}"
    return True, None


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_301(self, _request: Request, _fp: object, _code: int, _msg: str, _headers: object) -> None:
        raise URLError("provider redirects are not allowed")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def _call(
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    *,
    timeout_seconds: float,
    max_completion_tokens: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_completion_tokens,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        PROVIDER_ENDPOINTS[provider],
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            **({"HTTP-Referer": "https://github.com/thanhmuefatty07/AEGIS-COGNITION"} if provider == "openrouter" else {}),
        },
    )
    started = time.perf_counter()
    opener = build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = int(response.status)
    except HTTPError as error:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        return {
            "status": "ERROR",
            "http_status": int(error.code),
            "error": "http_error",
            "body_sha256": _sha256(raw),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (OSError, TimeoutError, URLError) as error:
        return {
            "status": "ERROR",
            "http_status": None,
            "error": type(error).__name__,
            "body_sha256": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if len(raw) > MAX_RESPONSE_BYTES:
        return {"status": "ERROR", "http_status": status, "error": "response_too_large", "latency_ms": latency_ms}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "ERROR",
            "http_status": status,
            "error": "response_json_invalid",
            "body_sha256": _sha256(raw),
            "latency_ms": latency_ms,
        }
    if not isinstance(document, Mapping):
        return {"status": "ERROR", "http_status": status, "error": "response_object_invalid", "latency_ms": latency_ms}
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return {"status": "ERROR", "http_status": status, "error": "response_choices_missing", "latency_ms": latency_ms}
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        return {"status": "ERROR", "http_status": status, "error": "response_content_missing", "latency_ms": latency_ms}
    usage = document.get("usage")
    usage_map = usage if isinstance(usage, Mapping) else {}
    return {
        "status": "OK" if 200 <= status < 300 else "ERROR",
        "http_status": status,
        "error": None if 200 <= status < 300 else "http_error",
        "model_returned": str(document.get("model", model)),
        "finish_reason": str(choices[0].get("finish_reason", "unknown")),
        "prompt_tokens": _optional_int(usage_map.get("prompt_tokens")),
        "completion_tokens": _optional_int(usage_map.get("completion_tokens")),
        "total_tokens": _optional_int(usage_map.get("total_tokens")),
        "cost": _optional_float(usage_map.get("cost")),
        "response_sha256": _sha256(content),
        "_content": content,
        "latency_ms": latency_ms,
    }


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _public_call(call: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in call.items() if key != "_content"}


def _parse_models(raw: str | None, provider: str) -> list[str]:
    text = raw or os.environ.get(f"{provider.upper()}_MODELS") or DEFAULT_MODELS[provider]
    models = [item.strip() for item in text.split(",") if item.strip()]
    if not models:
        raise ValueError(f"no {provider} models configured")
    if provider == "openrouter" and any(model != "openrouter/free" and not model.endswith(":free") for model in models):
        raise ValueError("OpenRouter live evaluation only accepts openrouter/free or explicit :free models")
    return models


def _selected_providers(requested: str, *, dry_run: bool) -> list[str]:
    if requested == "auto":
        result = [
            provider
            for provider, env_name in PROVIDER_KEY_ENV.items()
            if os.environ.get(env_name) or dry_run
        ]
    else:
        result = []
        for item in (item.strip() for item in requested.split(",")):
            if item and item not in result:
                result.append(item)
    invalid = sorted(set(result).difference(PROVIDER_ENDPOINTS))
    if invalid:
        raise ValueError(f"unsupported provider: {','.join(invalid)}")
    if not result:
        raise ValueError("no provider key configured; set an API key or use --dry-run")
    return result


def _planned_contexts(task: LiveTask) -> dict[str, str]:
    return {
        "baseline": _compile(task, token_budget=BASELINE_TOKEN_BUDGET),
        "bounded": _compile(task, token_budget=BOUND_TOKEN_BUDGET),
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    dry_run = bool(args.dry_run)
    providers = _selected_providers(args.provider, dry_run=dry_run)
    model_map = {
        "openrouter": _parse_models(args.openrouter_models, "openrouter"),
        "nvidia": _parse_models(args.nvidia_models, "nvidia"),
    }
    selected_models = [(provider, model) for provider in providers for model in model_map[provider]]
    task_list = tasks()
    request_count = len(selected_models) * len(task_list) * 2 * args.repeats
    if request_count > args.max_requests or request_count > MAX_REQUESTS_HARD:
        raise ValueError(
            f"planned request count {request_count} exceeds limit {min(args.max_requests, MAX_REQUESTS_HARD)}; "
            "reduce models/repeats or raise --max-requests up to the hard cap"
        )
    contexts = {task.task_id: _planned_contexts(task) for task in task_list}
    records: list[dict[str, Any]] = []
    for provider, model in selected_models:
        api_key = os.environ.get(PROVIDER_KEY_ENV[provider], "")
        if not api_key and not dry_run:
            raise ValueError(f"{PROVIDER_KEY_ENV[provider]} is not set")
        for repeat in range(1, args.repeats + 1):
            for task in task_list:
                for variant in ("baseline", "bounded"):
                    if not dry_run:
                        if records and args.delay_ms:
                            time.sleep(args.delay_ms / 1000)
                        call = _call(
                            provider,
                            model,
                            api_key,
                            _messages(task, contexts[task.task_id][variant]),
                            timeout_seconds=args.timeout_seconds,
                            max_completion_tokens=args.max_completion_tokens,
                        )
                    else:
                        call = {
                            "status": "DRY_RUN",
                            "http_status": None,
                            "error": None,
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                            "cost": None,
                            "latency_ms": 0.0,
                        }
                    quality_pass: bool | None = None
                    quality_error: str | None = None
                    if call["status"] == "OK":
                        quality_pass, quality_error = score_output(task, str(call.pop("_content", "")))
                    record = {
                        "provider": provider,
                        "model_requested": model,
                        "model_returned": call.get("model_returned", model),
                        "task_id": task.task_id,
                        "repeat": repeat,
                        "variant": variant,
                        "status": call["status"],
                        "quality_pass": quality_pass,
                        "quality_error": quality_error,
                        **_public_call(call),
                    }
                    records.append(record)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(str(record["provider"]), str(record["model_requested"]))].append(record)
    aggregates = [_aggregate(provider, model, group) for (provider, model), group in sorted(groups.items())]
    if dry_run:
        status = "DRY_RUN"
    elif any(item["transport_errors"] for item in aggregates):
        status = "PARTIAL"
    elif any(not item["quality_non_regression"] for item in aggregates):
        status = "QUALITY_REGRESSION"
    elif any(
        item["paired_model_match_rate"] is not None and item["paired_model_match_rate"] < 1.0
        for item in aggregates
    ):
        status = "MODEL_MISMATCH"
    elif any(not item["token_usage_complete"] for item in aggregates):
        status = "TOKEN_USAGE_INSUFFICIENT"
    elif any(not item["quality_complete"] for item in aggregates):
        status = "QUALITY_INSUFFICIENT"
    else:
        status = "PASS"
    return {
        "schema": SCHEMA,
        "status": status,
        "mode": "dry-run" if dry_run else "live",
        "commit": _commit(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "scope": "synthetic memory records only; no repository source or conversation data",
        "token_source": "provider_response_usage.prompt_tokens",
        "quality_oracle": "deterministic JSON contract, expected terms, and evidence ids",
        "request_count_planned": request_count,
        "request_count_observed": len(records),
        "repeats": args.repeats,
        "task_count": len(task_list),
        "providers": [provider for provider, _model in selected_models],
        "aggregates": aggregates,
        "records": records,
    }


def _aggregate(provider: str, model: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [record for record in records if record["status"] == "OK"]
    baseline = [record for record in successful if record["variant"] == "baseline"]
    bounded = [record for record in successful if record["variant"] == "bounded"]
    by_variant = {
        (int(record["repeat"]), str(record["task_id"]), str(record["variant"])): record
        for record in successful
    }
    non_regression = []
    regressions = []
    paired_quality = []
    token_usage = []
    reductions: list[float] = []
    model_matches: list[bool] = []
    for (repeat, task_id, variant), baseline_record in by_variant.items():
        if variant != "baseline":
            continue
        bounded_record = by_variant.get((repeat, task_id, "bounded"))
        if bounded_record is None:
            continue
        baseline_pass = bool(baseline_record["quality_pass"])
        bounded_pass = bool(bounded_record["quality_pass"])
        non_regression.append(not baseline_pass or bounded_pass)
        regressions.append(baseline_pass and not bounded_pass)
        paired_quality.append(baseline_pass and bounded_pass)
        baseline_tokens = baseline_record.get("prompt_tokens")
        bounded_tokens = bounded_record.get("prompt_tokens")
        token_usage.append(
            isinstance(baseline_tokens, int)
            and baseline_tokens > 0
            and isinstance(bounded_tokens, int)
            and bounded_tokens > 0
        )
        if (
            isinstance(baseline_tokens, int)
            and baseline_tokens > 0
            and isinstance(bounded_tokens, int)
            and bounded_tokens > 0
        ):
            reductions.append((baseline_tokens - bounded_tokens) * 100 / baseline_tokens)
        model_matches.append(baseline_record.get("model_returned") == bounded_record.get("model_returned"))
    return {
        "provider": provider,
        "model_requested": model,
        "attempts": len(records),
        "successful_responses": len(successful),
        "transport_errors": sum(1 for record in records if record["status"] == "ERROR"),
        "baseline_quality_passes": sum(1 for record in baseline if record["quality_pass"]),
        "bounded_quality_passes": sum(1 for record in bounded if record["quality_pass"]),
        "baseline_prompt_tokens": sum(record["prompt_tokens"] or 0 for record in baseline),
        "bounded_prompt_tokens": sum(record["prompt_tokens"] or 0 for record in bounded),
        "paired_cases": len(non_regression),
        "quality_non_regression": bool(non_regression) and all(non_regression),
        "quality_regressions": sum(1 for value in regressions if value),
        "quality_complete": bool(paired_quality) and all(paired_quality),
        "token_usage_complete": bool(token_usage) and all(token_usage),
        "paired_model_match_rate": round(sum(model_matches) / len(model_matches), 3) if model_matches else None,
        "mean_prompt_reduction_percent": round(sum(reductions) / len(reductions), 3) if reductions else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="auto", help="auto, openrouter, nvidia, or comma-separated providers")
    parser.add_argument("--openrouter-models", help="comma-separated :free model ids")
    parser.add_argument("--nvidia-models", help="comma-separated NVIDIA NIM model ids")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-completion-tokens", type=int, default=DEFAULT_MAX_COMPLETION_TOKENS)
    parser.add_argument("--delay-ms", type=int, default=DEFAULT_DELAY_MS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 3:
        parser.error("--repeats must be between 1 and 3")
    if args.max_requests < 1 or args.max_requests > MAX_REQUESTS_HARD:
        parser.error(f"--max-requests must be between 1 and {MAX_REQUESTS_HARD}")
    if args.timeout_seconds <= 0 or args.timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
        parser.error(f"--timeout-seconds must be between 0 and {DEFAULT_TIMEOUT_SECONDS:g}")
    if args.max_completion_tokens < 16 or args.max_completion_tokens > 512:
        parser.error("--max-completion-tokens must be between 16 and 512")
    if args.delay_ms < 0 or args.delay_ms > 10_000:
        parser.error("--delay-ms must be between 0 and 10000")
    started = time.perf_counter()
    result = evaluate(args)
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] in {"PASS", "DRY_RUN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
