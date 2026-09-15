from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from scripts import memory_context_live_eval as live_eval


def _args(**overrides: Any) -> argparse.Namespace:
    values = {
        "provider": "openrouter",
        "openrouter_models": "test/model:free",
        "nvidia_models": "",
        "repeats": 1,
        "max_requests": 48,
        "timeout_seconds": 60.0,
        "max_completion_tokens": 128,
        "delay_ms": 0,
        "dry_run": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_score_output_enforces_evidence_contract() -> None:
    known = '{"answer":"4096 tokens","evidence_ids":["0x1"],"unknown":false}'
    unknown = '{"answer":"unknown","evidence_ids":[],"unknown":true}'

    assert live_eval.score_output(live_eval.tasks()[0], known) == (True, None)
    assert live_eval.score_output(live_eval.tasks()[2], unknown) == (True, None)
    assert live_eval.score_output(live_eval.tasks()[0], "not json")[0] is False
    assert live_eval.score_output(live_eval.tasks()[0], known.replace("0x1", "0x2"))[0] is False


def test_call_parses_provider_usage_and_keeps_endpoint_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {
            "model": "test/model:free",
            "choices": [{"message": {"content": '{"answer":"4096","evidence_ids":[1],"unknown":false}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 9, "total_tokens": 132, "cost": 0},
        }
    ).encode()
    captured: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == live_eval.MAX_RESPONSE_BYTES + 1
            return payload

    class FakeOpener:
        def open(self, request: Any, *, timeout: float) -> FakeResponse:
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(live_eval, "build_opener", lambda _handler: FakeOpener())
    result = live_eval._call(
        "openrouter",
        "test/model:free",
        "test-key",
        [{"role": "user", "content": "synthetic"}],
        timeout_seconds=7.0,
        max_completion_tokens=16,
    )

    assert captured == {
        "url": live_eval.PROVIDER_ENDPOINTS["openrouter"],
        "authorization": "Bearer test-key",
        "timeout": 7.0,
    }
    assert result["status"] == "OK"
    assert result["prompt_tokens"] == 123
    assert result["completion_tokens"] == 9
    assert result["total_tokens"] == 132
    assert result["_content"]
    assert "test-key" not in json.dumps(live_eval._public_call(result))


def test_dry_run_is_network_free_and_bounded() -> None:
    result = live_eval.evaluate(_args(dry_run=True))

    assert result["status"] == "DRY_RUN"
    assert result["mode"] == "dry-run"
    assert result["request_count_planned"] == 12
    assert result["request_count_observed"] == 12
    assert len(result["records"]) == 12
    assert all("_content" not in record for record in result["records"])
    assert all(record["status"] == "DRY_RUN" for record in result["records"])


def test_aggregate_marks_missing_prompt_usage_as_insufficient() -> None:
    records = [
        {
            "repeat": 1,
            "task_id": task.task_id,
            "variant": variant,
            "status": "OK",
            "quality_pass": True,
            "prompt_tokens": None,
            "model_returned": "test/model:free",
        }
        for task in live_eval.tasks()
        for variant in ("baseline", "bounded")
    ]

    aggregate = live_eval._aggregate("openrouter", "test/model:free", records)

    assert aggregate["paired_cases"] == 6
    assert aggregate["quality_complete"] is True
    assert aggregate["token_usage_complete"] is False
    assert aggregate["mean_prompt_reduction_percent"] is None


def test_live_eval_uses_provider_usage_and_redacts_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "fresh-test-key")
    calls = 0

    def fake_call(
        provider: str,
        model: str,
        api_key: str,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        max_completion_tokens: int,
    ) -> dict[str, Any]:
        nonlocal calls
        assert provider == "openrouter"
        assert model == "test/model:free"
        assert api_key == "fresh-test-key"
        assert timeout_seconds == 60.0
        assert max_completion_tokens == 128
        question = messages[1]["content"].split("\n\n", 1)[0]
        if "production database" in question or "different owner" in question:
            answer = {"answer": "unknown", "evidence_ids": [], "unknown": True}
        elif "token budget" in question:
            answer = {"answer": "4096 tokens", "evidence_ids": ["0x1"], "unknown": False}
        elif "path cap" in question:
            answer = {"answer": "64 paths; owner scoped", "evidence_ids": [2, 3], "unknown": False}
        elif "manifest" in question:
            answer = {"answer": "hash and order", "evidence_ids": [5], "unknown": False}
        else:
            answer = {"answer": "60 seconds", "evidence_ids": [7], "unknown": False}
        calls += 1
        content = json.dumps(answer)
        return {
            "status": "OK",
            "http_status": 200,
            "error": None,
            "model_returned": "test/model:free",
            "finish_reason": "stop",
            "prompt_tokens": 200 if calls % 2 else 100,
            "completion_tokens": 12,
            "total_tokens": 212 if calls % 2 else 112,
            "cost": 0.0,
            "response_sha256": live_eval._sha256(content),
            "_content": content,
            "latency_ms": 1.0,
        }

    monkeypatch.setattr(live_eval, "_call", fake_call)
    result = live_eval.evaluate(_args())

    assert result["status"] == "PASS"
    assert calls == 12
    aggregate = result["aggregates"][0]
    assert aggregate["paired_cases"] == 6
    assert aggregate["quality_complete"] is True
    assert aggregate["quality_non_regression"] is True
    assert aggregate["quality_regressions"] == 0
    assert aggregate["token_usage_complete"] is True
    assert aggregate["baseline_prompt_tokens"] == 1200
    assert aggregate["bounded_prompt_tokens"] == 600
    assert aggregate["mean_prompt_reduction_percent"] == 50.0
    rendered = json.dumps(result, sort_keys=True)
    assert "fresh-test-key" not in rendered
    assert '"_content"' not in rendered
