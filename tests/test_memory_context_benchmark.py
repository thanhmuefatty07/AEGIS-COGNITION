from __future__ import annotations

from scripts.memory_context_benchmark import run


def test_memory_context_benchmark_is_self_consistent() -> None:
    result = run()

    assert result["status"] == "PASS"
    assert result["quantitative_case_count"] == 8
    assert result["oracle_case_count"] == 8
    assert result["aggregate"]["bounded_tokens"] <= result["aggregate"]["baseline_tokens"]
    assert all(case.get("deterministic", False) for case in result["cases"])
