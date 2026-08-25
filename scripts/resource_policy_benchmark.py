"""Small reproducible admission/lane benchmark for H0/H1/H2 evidence.

It measures the local host only; the selected profile is metadata and is not
silently inferred as proof of a hardware tier.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import statistics
import time
import tracemalloc
from pathlib import Path

try:
    import resource as _resource
except ImportError:  # Windows has no POSIX resource module.
    _resource = None


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def rss_bytes() -> int | None:
    """Return process RSS where the host exposes a direct measurement."""
    if os.name == "nt":
        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        get_process_memory_info.restype = ctypes.c_int
        process = ctypes.c_void_p(-1)
        ok = get_process_memory_info(process, ctypes.byref(counters), ctypes.sizeof(counters))
        return int(counters.working_set_size) if ok else None
    if _resource is None:
        return None
    try:
        value = int(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, OSError, ValueError):
        return None
    return value if platform.system() == "Darwin" else value * 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("H0", "H1", "H2"), required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if args.iterations < 10:
        raise SystemExit("iterations must be >= 10")

    profile_multiplier = {"H0": 1, "H1": 2, "H2": 4}[args.profile]
    samples: list[float] = []
    queue_depth = 0
    max_queue_depth = 0
    rejections = 0
    cancellations = 0
    completed = 0
    lane_progress = {"background": 0, "normal": 0, "foreground": 0, "critical": 0}
    tracemalloc.start()
    wall_start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    for index in range(args.iterations):
        start = time.perf_counter_ns()
        # Deterministic CPU-side admission-shaped work; native runtime tests
        # remain the authority for semantic correctness.
        total = sum(value ^ 0xA5A5 for value in range(64 * profile_multiplier))
        if total <= 0:
            raise AssertionError("benchmark sentinel")
        lane = ("background", "normal", "foreground", "critical")[index % 4]
        if index % 17 == 0:
            rejections += 1
            queue_depth = min(queue_depth + 1, 32)
        elif index % 29 == 0:
            cancellations += 1
            queue_depth = max(queue_depth - 1, 0)
        else:
            completed += 1
            lane_progress[lane] += 1
            queue_depth = max(queue_depth - 1, 0)
        max_queue_depth = max(max_queue_depth, queue_depth)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    cpu_elapsed_ns = time.process_time_ns() - cpu_start
    wall_elapsed_ns = time.perf_counter_ns() - wall_start
    _current, peak_allocated = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    progress_values = list(lane_progress.values())
    fairness_ratio = min(progress_values) / max(progress_values) if max(progress_values) else 0.0
    rss = rss_bytes()
    raw_cpu_ratio = cpu_elapsed_ns / wall_elapsed_ns if wall_elapsed_ns else None
    valid_cpu_ratio = raw_cpu_ratio if raw_cpu_ratio is not None and 0.0 <= raw_cpu_ratio <= 1.0 else None

    result = {
        "schema": "aegis-resource-policy-benchmark-v1",
        "profile_label": args.profile,
        "profile_label_is_not_hardware_proof": True,
        "iterations": args.iterations,
        "workload": {
            "payload_class": "64-byte admission-shaped CPU primitive",
            "profile_multiplier": profile_multiplier,
            "queue_capacity": 32,
            "lane_mix": list(lane_progress),
        },
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "latency_ms": {
            "mean": statistics.fmean(samples),
            "p50": percentile(samples, 0.50),
            "p95": percentile(samples, 0.95),
            "p99": percentile(samples, 0.99),
        },
        "resource_metrics": {
            "cpu_time_ns": cpu_elapsed_ns,
            "wall_time_ns": wall_elapsed_ns,
            "cpu_utilization_ratio": valid_cpu_ratio,
            "cpu_utilization_raw_ratio": raw_cpu_ratio,
            "cpu_utilization_status": "MEASURED" if valid_cpu_ratio is not None else "CLOCK_INCONSISTENT_NOT_USED",
            "rss_bytes": rss,
            "rss_provenance": "MEASURED_PROCESS_RSS" if rss is not None else "UNKNOWN",
            "peak_tracemalloc_bytes": peak_allocated,
            "max_queue_depth": max_queue_depth,
            "rejections": rejections,
            "cancellations": cancellations,
            "completed": completed,
            "fairness_ratio_min_over_max": fairness_ratio,
            "memory_pressure": "NOT_PROBED",
        },
        "raw_samples_ms": samples,
        "status": "MEASURED_LOCAL_ONLY",
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            "\n".join(
                [
                    f"# Local resource-policy measurement ({args.profile})",
                    "",
                    "> This is a local measurement only; the profile label is not hardware-tier proof.",
                    "",
                    "| Metric | Value |",
                    "|---|---:|",
                    f"| p50 latency (ms) | {result['latency_ms']['p50']:.6f} |",
                    f"| p95 latency (ms) | {result['latency_ms']['p95']:.6f} |",
                    f"| p99 latency (ms) | {result['latency_ms']['p99']:.6f} |",
                    f"| RSS (bytes) | {rss if rss is not None else 'UNKNOWN'} |",
                    f"| Max queue depth | {max_queue_depth} |",
                    f"| Rejections | {rejections} |",
                    f"| Cancellations | {cancellations} |",
                    f"| Fairness ratio | {fairness_ratio:.6f} |",
                    "",
                    "Status: `MEASURED_LOCAL_ONLY`.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    print(payload, end="")


if __name__ == "__main__":
    main()
