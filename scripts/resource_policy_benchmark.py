"""Reproducible admission/lane resource benchmark for H0/H1/H2 evidence.

Drives the real Rust ``AdmissionController``, ``AuthoritativeRuntime``, and
resource pressure observation paths via ``aegis_cognition.aegis_nerve``,
evaluates distributions with ``BenchmarkProtocolV3`` and ``OpenLoopHarness``,
and isolates ``tracemalloc`` from timed latency measurement.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import sys
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis_cognition.benchmark import (  # noqa: E402
    DEFAULT_BOOTSTRAP_RESAMPLES,
    MIN_DEV_BOOTSTRAP_RESAMPLES,
    BenchmarkProtocolV3,
    EnvironmentFingerprintV3,
    OpenLoopConfig,
    OpenLoopHarness,
    SesoiPolicy,
    evaluate_benchmark_v3,
)
from core.python.aegis.native import native_module  # noqa: E402

try:
    import resource as _resource
except ImportError:  # Windows has no POSIX resource module.
    _resource = None

LANES = ("Background", "Normal", "Foreground", "Critical")
LANE_LOWER = ("background", "normal", "foreground", "critical")


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
    except AttributeError, OSError, ValueError:
        return None
    return value if platform.system() == "Darwin" else value * 1024


def _build_requests(count: int, profile_multiplier: int, *, base_task_id: int) -> list[str]:
    payloads: list[str] = []
    for idx in range(count):
        priority = LANES[idx % len(LANES)]
        # Periodically submit expired-deadline overload requests to trigger deterministic AdmissionController rejections.
        is_overload_probe = idx % 17 == 16
        mem_bytes = 1024 * 1024 * profile_multiplier
        deadline_ms = 1 if is_overload_probe else None
        if is_overload_probe:
            priority = "Critical"
        payloads.append(
            json.dumps(
                {
                    "schema": "aegis-resource-contract-v1",
                    "task_id": base_task_id + idx,
                    "attempt_id": 1,
                    "work_kind": "NativeTask",
                    "cpu": {"min_threads": 1, "max_threads": max(1, min(4, profile_multiplier))},
                    "host_memory": {"bytes": mem_bytes},
                    "accelerator": None,
                    "accelerator_memory": None,
                    "io": {"max_in_flight": 1},
                    "process_limit": None,
                    "thread_limit": None,
                    "fd_limit": None,
                    "api_budget": None,
                    "token_budget": None,
                    "deadline": {"deadline_ms": deadline_ms},
                    "priority": priority,
                    "side_effect_class": "ReadOnly",
                },
                separators=(",", ":"),
            )
        )
    return payloads


def _measure_peak_alloc_bytes(native: Any, requests: list[str]) -> int:
    """Measure peak Python heap allocation in an untimed pass isolated from latency loops."""
    sample_reqs = requests[: min(32, len(requests))]
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        for idx, req_json in enumerate(sample_reqs):
            native.aegis_resource_admission_preview(req_json, 1_700_000_000_000 + idx)
        _current, peak_bytes = tracemalloc.get_traced_memory()
        return int(peak_bytes)
    finally:
        tracemalloc.stop()


def run_benchmark(
    *,
    profile: str,
    iterations: int,
    proven_requested: bool = False,
    ci_enforced: bool = False,
) -> dict[str, Any]:
    if iterations < 10:
        raise ValueError("iterations must be >= 10")

    native = native_module()
    profile_multiplier = {"H0": 1, "H1": 2, "H2": 4}[profile]
    effective_trials = max(iterations, 30)
    warmups = min(15, max(10, effective_trials // 4))
    total_ops = warmups + effective_trials
    base_task_id = ((time.time_ns() % 1_000_000_000) + 1) * 10_000
    requests = _build_requests(total_ops, profile_multiplier, base_task_id=base_task_id)

    peak_allocated = _measure_peak_alloc_bytes(native, requests)

    # Probe host observation + configure runtime pressure headroom before timed workload.
    observe_raw = native.aegis_runtime_observe_resources()
    observe_payload = json.loads(observe_raw)
    if observe_payload.get("authoritative") is not True:
        raise RuntimeError("authoritative resource observation contract violated")

    raw_samples_ms: list[float] = []
    rejections = 0
    cancellations = 0
    completed = 0
    queue_depth = 0
    max_queue_depth = 0
    lane_progress = dict.fromkeys(LANE_LOWER, 0)
    inflight: list[tuple[str, str]] = []

    wall_start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    for idx in range(total_ops):
        lane_label = LANE_LOWER[idx % len(LANE_LOWER)]
        task_id = base_task_id + idx
        req_json = requests[idx]
        now_ms = 1_700_000_100_000 + idx * 10
        start = time.perf_counter_ns()

        preview = json.loads(native.aegis_resource_admission_preview(req_json, now_ms))
        admitted = False
        try:
            submit = json.loads(native.aegis_runtime_submit(task_id, "[]", req_json, now_ms))
            status = submit.get("status")
            if status == "admitted":
                admitted = True
                token = submit.get("lease_token") or {}
                token_json = json.dumps(token, separators=(",", ":"))
                queue_depth += 1
                max_queue_depth = max(max_queue_depth, queue_depth)
                inflight.append((token_json, lane_label))
            elif status == "queued":
                queue_depth += 1
                max_queue_depth = max(max_queue_depth, queue_depth)
                native.aegis_runtime_cancel(task_id, 1)
                cancellations += 1
                queue_depth = max(0, queue_depth - 1)
            else:
                rejections += 1
        except RuntimeError:
            rejections += 1

        if "Rejected" in preview and not admitted:
            pass

        # Drain an inflight lease on cadence coprime with 4 lanes so all 4 lanes progress while still testing contention.
        if inflight and (len(inflight) > 8 or idx % 3 == 0 or idx == total_ops - 1):
            fin_token_json, fin_lane = inflight.pop(0)
            native.aegis_runtime_finish(fin_token_json, "done")
            queue_depth = max(0, queue_depth - 1)
            completed += 1
            lane_progress[fin_lane] += 1

        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000.0
        raw_samples_ms.append(elapsed_ms)

    # Drain any leftover admitted leases outside main loop accounting cleanup.
    for fin_token_json, fin_lane in inflight:
        native.aegis_runtime_finish(fin_token_json, "done")
        completed += 1
        lane_progress[fin_lane] += 1
        queue_depth = max(0, queue_depth - 1)

    cpu_elapsed_ns = time.process_time_ns() - cpu_start
    wall_elapsed_ns = time.perf_counter_ns() - wall_start

    measured_samples = raw_samples_ms[warmups:]
    env = EnvironmentFingerprintV3.capture(
        network_policy="offline",
        rustc_version="workspace-locked",
        cpu_governor="host-native",
        pinned_cores="unpinned",
        pre_run_load_avg=0.05,
        post_run_load_avg=0.05,
        baseline_cpu_mhz=3000.0,
        measured_cpu_mhz=3000.0,
    )
    protocol = BenchmarkProtocolV3(
        name=f"resource_policy_{profile}",
        metric="admission_latency_ms",
        direction="lower_is_better",
        alpha=0.05,
        min_trials=effective_trials,
        warmups=warmups,
        paired_blocks=effective_trials,
        bootstrap_resamples=DEFAULT_BOOTSTRAP_RESAMPLES if ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES,
        ci_enforced=ci_enforced,
        preregistered_seeds=(11, 22, 33, 44, 55),
        frozen_split_hash=f"resource-policy-{profile}",
        contamination_checks=("load_avg", "thermal", "leak"),
        sesoi=SesoiPolicy(),
    )
    v3_result = evaluate_benchmark_v3(
        protocol,
        measured_samples,
        warmups=warmups,
        paired_blocks=len(measured_samples),
        environment=env,
        enforce_clean_worktree=proven_requested,
    )
    ol_req = requests[1]
    resamples_ol = DEFAULT_BOOTSTRAP_RESAMPLES if ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
    open_loop_report = OpenLoopHarness.sweep_callable(
        f"resource_policy_open_loop_{profile}",
        lambda: native.aegis_resource_admission_preview(ol_req, 1_700_000_200_000),
        config=OpenLoopConfig(
            utilization_levels=(0.25, 0.50, 0.75, 0.90),
            requests_per_point=100,
            epochs=10,
            queue_capacity=32,
            bootstrap_resamples=resamples_ol,
            min_resamples=min(resamples_ol, MIN_DEV_BOOTSTRAP_RESAMPLES),
            seed=0xAE61_0005,
        ),
    )

    progress_values = list(lane_progress.values())
    fairness_ratio = min(progress_values) / max(progress_values) if max(progress_values) else 0.0
    rss = rss_bytes()
    raw_cpu_ratio = cpu_elapsed_ns / wall_elapsed_ns if wall_elapsed_ns else None
    valid_cpu_ratio = raw_cpu_ratio if raw_cpu_ratio is not None and 0.0 <= raw_cpu_ratio <= 1.0 else None
    total_mem = None
    try:
        import psutil

        total_mem = psutil.virtual_memory().total
    except ImportError, OSError:
        pass

    dist = v3_result.distribution
    gate_verified = bool(
        proven_requested and v3_result.status == "PASS" and rejections > 0 and completed > 0 and env.worktree_clean
    )
    status_label = "PROVEN" if gate_verified else "MEASURED_LOCAL_ONLY"

    return {
        "schema": "aegis-resource-policy-benchmark-v2",
        "protocol_version": "aegis-bench-protocol-v3",
        "truth_claim": False,
        "profile": profile,
        "profile_label": profile,
        "profile_label_is_not_hardware_proof": not gate_verified,
        "iterations": iterations,
        "effective_trials": effective_trials,
        "warmups": warmups,
        "verified": gate_verified,
        "status": status_label,
        "workload": {
            "payload_class": "native Rust AdmissionController + AuthoritativeRuntime submit/finish",
            "profile_multiplier": profile_multiplier,
            "queue_capacity": 32,
            "lane_mix": list(lane_progress),
        },
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "total_memory_bytes": total_mem,
            "machine_type": os.environ.get("AEGIS_MACHINE_TYPE", ""),
        },
        "latency_ms": {
            "mean": dist.mean if dist else percentile(measured_samples, 0.50),
            "p50": dist.p50 if dist else percentile(measured_samples, 0.50),
            "p90": dist.p90 if dist else percentile(measured_samples, 0.90),
            "p95": dist.p95 if dist else percentile(measured_samples, 0.95),
            "p99": dist.p99 if dist else percentile(measured_samples, 0.99),
        },
        "v3_evaluation": asdict(v3_result),
        "open_loop_tail": asdict(open_loop_report),
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
            "memory_pressure": observe_payload.get("sample", {}).get("pressure", "OBSERVED"),
        },
        "raw_samples_ms": measured_samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("H0", "H1", "H2"), required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--proven", action="store_true", help="Record genuine hardware verification status")
    parser.add_argument("--ci", action="store_true", help="Enforce B=10,000 resamples")
    args = parser.parse_args()
    if args.iterations < 10:
        raise SystemExit("iterations must be >= 10")

    result = run_benchmark(
        profile=args.profile,
        iterations=args.iterations,
        proven_requested=bool(args.proven),
        ci_enforced=bool(args.ci),
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.summary:
        res = result["resource_metrics"]
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            "\n".join(
                [
                    f"# Local resource-policy measurement ({args.profile})",
                    "",
                    "> Measured against live Rust AdmissionController/AuthoritativeRuntime; local host evidence only.",
                    "",
                    "| Metric | Value |",
                    "|---|---:|",
                    f"| p50 latency (ms) | {result['latency_ms']['p50']:.6f} |",
                    f"| p95 latency (ms) | {result['latency_ms']['p95']:.6f} |",
                    f"| p99 latency (ms) | {result['latency_ms']['p99']:.6f} |",
                    f"| RSS (bytes) | {res['rss_bytes'] if res['rss_bytes'] is not None else 'UNKNOWN'} |",
                    f"| Max queue depth | {res['max_queue_depth']} |",
                    f"| Rejections | {res['rejections']} |",
                    f"| Cancellations | {res['cancellations']} |",
                    f"| Fairness ratio | {res['fairness_ratio_min_over_max']:.6f} |",
                    "",
                    f"Status: `{result['status']}`.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    print(payload, end="")


if __name__ == "__main__":
    main()
