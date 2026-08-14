"""Small reproducible admission/lane benchmark for H0/H1/H2 evidence.

It measures the local host only; the selected profile is metadata and is not
silently inferred as proof of a hardware tier.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("H0", "H1", "H2"), required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 10:
        raise SystemExit("iterations must be >= 10")

    samples: list[float] = []
    for _ in range(args.iterations):
        start = time.perf_counter_ns()
        # Deterministic CPU-side admission-shaped work; native runtime tests
        # remain the authority for semantic correctness.
        total = sum(index ^ 0xA5A5 for index in range(64))
        if total <= 0:
            raise AssertionError("benchmark sentinel")
        samples.append((time.perf_counter_ns() - start) / 1_000_000)

    result = {
        "schema": "aegis-resource-policy-benchmark-v1",
        "profile_label": args.profile,
        "profile_label_is_not_hardware_proof": True,
        "iterations": args.iterations,
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "latency_ms": {
            "mean": statistics.fmean(samples),
            "p50": percentile(samples, 0.50),
            "p95": percentile(samples, 0.95),
        },
        "status": "MEASURED_LOCAL_ONLY",
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
