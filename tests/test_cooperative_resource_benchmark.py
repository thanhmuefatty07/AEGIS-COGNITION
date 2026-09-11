from __future__ import annotations

import pytest

from scripts.cooperative_resource_benchmark import run_benchmark


def test_benchmark_compares_paths_with_one_correctness_oracle() -> None:
    result = run_benchmark(mode="both", payload_bytes=4 * 1024, iterations=3)

    assert result["status"] == "MEASURED_LOCAL_ONLY"
    assert result["correctness"]["all_modes_match"] is True
    assert result["correctness"]["temporary_files_cleaned"] is True
    assert [item["mode"] for item in result["results"]] == ["ram", "spill"]
    assert result["results"][0]["io"]["bytes_written"] == 0
    assert result["results"][1]["io"]["bytes_written"] == 3 * 4 * 1024
    assert result["results"][1]["io"]["bytes_read"] == 3 * 4 * 1024


@pytest.mark.parametrize(
    ("mode", "payload_bytes", "iterations"),
    [
        ("unknown", 4 * 1024, 1),
        ("ram", 1, 1),
        ("spill", 4 * 1024, 0),
    ],
)
def test_benchmark_rejects_invalid_bounds(
    mode: str, payload_bytes: int, iterations: int
) -> None:
    with pytest.raises(ValueError):
        run_benchmark(mode=mode, payload_bytes=payload_bytes, iterations=iterations)  # type: ignore[arg-type]
