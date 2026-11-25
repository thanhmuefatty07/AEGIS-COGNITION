"""Measure real CPython <-> Rust PyO3 FFI boundary crossings (`FFI-001..004`).

Exercises the compiled ``aegis_cognition.aegis_nerve`` extension directly via
counterbalanced AB/BA paired trials and ``BenchmarkProtocolV3`` statistics.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
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
    evaluate_paired_benchmark_v3,
)
from core.python.aegis.native import native_module  # noqa: E402
from core.python.bridge_mmap import MmapBridgeFrame  # noqa: E402

SCHEMA = "aegis-ffi-boundary-benchmark-v3"
DEFAULT_OUTPUT = ROOT / "artifacts" / "ffi_boundary_benchmark.json"
DEFAULT_TRIALS = 40
DEFAULT_WARMUPS = 12


def _make_v3_protocol(
    *,
    name: str,
    metric: str,
    resamples: int,
    ci_enforced: bool,
    min_trials: int,
    warmups: int,
) -> BenchmarkProtocolV3:
    return BenchmarkProtocolV3(
        name=name,
        metric=metric,
        direction="lower_is_better",
        alpha=0.05,
        min_trials=min_trials,
        warmups=warmups,
        paired_blocks=min_trials,
        bootstrap_resamples=resamples,
        ci_enforced=ci_enforced,
        preregistered_seeds=(101, 202, 303, 404, 505),
        frozen_split_hash="ffi-boundary-benchmark-v3",
        contamination_checks=("load_avg", "thermal", "leak"),
        sesoi=SesoiPolicy(),
    )


def _capture_env(env_fp: EnvironmentFingerprintV3 | None = None) -> EnvironmentFingerprintV3:
    if env_fp is not None:
        return env_fp
    return EnvironmentFingerprintV3.capture(
        network_policy="offline",
        rustc_version="workspace-locked",
        cpu_governor="host-native",
        pinned_cores="unpinned",
        pre_run_load_avg=0.05,
        post_run_load_avg=0.05,
        baseline_cpu_mhz=3000.0,
        measured_cpu_mhz=3000.0,
    )


def _eval_single_surface(
    name: str,
    samples_ns: list[float],
    *,
    warmups: int,
    trials: int,
    resamples: int,
    ci_enforced: bool,
    env: EnvironmentFingerprintV3,
) -> dict[str, Any]:
    protocol = _make_v3_protocol(
        name=name,
        metric="latency_ns",
        resamples=resamples,
        ci_enforced=ci_enforced,
        min_trials=trials,
        warmups=warmups,
    )
    measured = samples_ns[warmups:]
    result = evaluate_benchmark_v3(
        protocol,
        measured,
        warmups=warmups,
        paired_blocks=len(measured),
        environment=env,
        enforce_clean_worktree=False,
    )
    return asdict(result)


def run_ffi_benchmark(
    *,
    trials: int = DEFAULT_TRIALS,
    warmups: int = DEFAULT_WARMUPS,
    resamples: int = MIN_DEV_BOOTSTRAP_RESAMPLES,
    ci_enforced: bool = False,
) -> dict[str, Any]:
    """Execute real PyO3 FFI boundary benchmarks FFI-001 through FFI-004."""

    if trials < 30:
        raise ValueError("trials must be >= 30 for V3 evaluation")
    if warmups < 10:
        raise ValueError("warmups must be >= 10 for V3 evaluation")

    native = native_module()
    env = _capture_env()
    total_calls = warmups + trials

    # FFI-001: minimal PyO3 crossing overhead (`aegis_hot_hash` & `aegis_validate_layout`).
    small_payload = bytes((i * 31 + 7) & 0xFF for i in range(64))
    ffi001_samples: list[float] = []
    for _ in range(total_calls):
        t0 = time.perf_counter_ns()
        digest = native.aegis_hot_hash(small_payload)
        valid = native.aegis_validate_layout(64, 64)
        elapsed_ns = float(max(1, time.perf_counter_ns() - t0))
        if not valid or len(digest) != 64:
            raise RuntimeError("FFI-001 validation contract failed")
        ffi001_samples.append(elapsed_ns)

    # FFI-002: structured JSON marshalling across CPython <-> Rust (`aegis_select_context_items_v2` + admission preview).
    items_json = json.dumps(
        [
            {
                "node_id": f"0x{i + 1:x}",
                "kind": "memory" if i % 2 == 0 else "session",
                "token_cost": 16 + (i % 16),
                "utility_score": 10_000 - i * 50,
            }
            for i in range(32)
        ],
        separators=(",", ":"),
    )
    relations_json = json.dumps(
        {
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": ["0xf00"],
            "relations": [
                {
                    "relation_id": f"0x{90 + i:x}",
                    "kind": "confirmed_file_memory",
                    "file_node_id": "0xf00",
                    "memory_node_id": f"0x{2 * i + 1:x}",
                }
                for i in range(8)
            ],
        },
        separators=(",", ":"),
    )
    req_json = json.dumps(
        {
            "schema": "aegis-resource-contract-v1",
            "task_id": 1,
            "attempt_id": 1,
            "work_kind": "NativeTask",
            "cpu": {"min_threads": 1, "max_threads": 1},
            "host_memory": {"bytes": 1024 * 1024},
            "accelerator": None,
            "accelerator_memory": None,
            "io": {"max_in_flight": 1},
            "process_limit": None,
            "thread_limit": None,
            "fd_limit": None,
            "api_budget": None,
            "token_budget": None,
            "deadline": {"deadline_ms": None},
            "priority": "Foreground",
            "side_effect_class": "ReadOnly",
        },
        separators=(",", ":"),
    )
    ffi002_samples: list[float] = []
    for idx in range(total_calls):
        t0 = time.perf_counter_ns()
        selected_raw = native.aegis_select_context_items_v2(items_json, relations_json, 512, "[]")
        admit_raw = native.aegis_resource_admission_preview(req_json, 1_700_000_000_000 + idx)
        elapsed_ns = float(max(1, time.perf_counter_ns() - t0))
        if "selected_node_ids" not in selected_raw or not admit_raw:
            raise RuntimeError("FFI-002 JSON marshalling contract failed")
        ffi002_samples.append(elapsed_ns)

    # FFI-003: counterbalanced AB/BA paired comparison of Vec copy (`aegis_hot_commit`) vs PyBuffer (`aegis_hot_commit_buffer`).
    payload_64k = bytes(((i * 17 + 13) & 0xFF) for i in range(65_536))
    payload_mv = memoryview(payload_64k)
    try:
        for _ in range(warmups):
            native.aegis_hot_commit(payload_64k, "DEV")
            native.aegis_hot_commit_buffer(payload_mv, "DEV")

        arm_a_vec_ns: list[float] = []
        arm_b_buf_ns: list[float] = []
        exec_order: list[str] = []
        for blk in range(trials):
            order = ("A", "B") if blk % 2 == 0 else ("B", "A")
            exec_order.append("".join(order))
            for arm in order:
                if arm == "A":
                    t0 = time.perf_counter_ns()
                    out = native.aegis_hot_commit(payload_64k, "DEV")
                    dt = float(max(1, time.perf_counter_ns() - t0))
                    if '"handle_valid":true' not in out:
                        raise RuntimeError("FFI-003 aegis_hot_commit failed")
                    arm_a_vec_ns.append(dt)
                else:
                    t0 = time.perf_counter_ns()
                    out = native.aegis_hot_commit_buffer(payload_mv, "DEV")
                    dt = float(max(1, time.perf_counter_ns() - t0))
                    if '"handle_valid":true' not in out:
                        raise RuntimeError("FFI-003 aegis_hot_commit_buffer failed")
                    arm_b_buf_ns.append(dt)
    finally:
        payload_mv.release()

    paired_ffi003 = evaluate_paired_benchmark_v3(
        _make_v3_protocol(
            name="FFI-003_vec_copy_vs_pybuffer_64k",
            metric="commit_latency_ns",
            resamples=resamples,
            ci_enforced=ci_enforced,
            min_trials=trials,
            warmups=warmups,
        ),
        arm_a_vec_ns,
        arm_b_buf_ns,
        warmups=warmups,
        paired_blocks=trials,
        environment=env,
        execution_order=exec_order,
        enforce_clean_worktree=False,
    )

    # FFI-004: cross-language Rust mmap writer -> Python zero-copy MmapBridgeFrame -> Rust validator.
    ffi004_samples: list[float] = []
    with tempfile.TemporaryDirectory(prefix="aegis-ffi004-") as tmpdir:
        frame_path = Path(tmpdir) / "bridge-frame.aegmmap"
        frame_str = str(frame_path)
        for idx in range(total_calls):
            t0 = time.perf_counter_ns()
            header = native.aegis_write_mmap_bridge_pattern(frame_str, idx + 1, 77, 16_384)
            with MmapBridgeFrame(frame_path) as bridge:
                view = bridge.payload_view()
                try:
                    checksum = view[0] ^ view[-1] ^ (len(view) & 0xFF)
                finally:
                    view.release()
            valid = native.aegis_validate_mmap_bridge_frame(frame_str)
            elapsed_ns = float(max(1, time.perf_counter_ns() - t0))
            if not valid or len(header) != 5 or checksum < 0:
                raise RuntimeError("FFI-004 mmap bridge roundtrip contract failed")
            ffi004_samples.append(elapsed_ns)

    # Open-loop tail latency harness over FFI-001 call path.
    open_loop_report = OpenLoopHarness.sweep_callable(
        "FFI-001_open_loop",
        lambda: native.aegis_hot_hash(small_payload),
        config=OpenLoopConfig(
            utilization_levels=(0.25, 0.50, 0.75, 0.90),
            requests_per_point=120,
            epochs=12,
            queue_capacity=64,
            bootstrap_resamples=resamples,
            min_resamples=min(resamples, MIN_DEV_BOOTSTRAP_RESAMPLES),
            seed=0xFF10_0001,
        ),
    )

    results = {
        "FFI-001_hot_hash_and_layout": _eval_single_surface(
            "FFI-001_hot_hash_and_layout",
            ffi001_samples,
            warmups=warmups,
            trials=trials,
            resamples=resamples,
            ci_enforced=ci_enforced,
            env=env,
        ),
        "FFI-002_json_context_and_admission": _eval_single_surface(
            "FFI-002_json_context_and_admission",
            ffi002_samples,
            warmups=warmups,
            trials=trials,
            resamples=resamples,
            ci_enforced=ci_enforced,
            env=env,
        ),
        "FFI-003_vec_copy_vs_pybuffer_64k": asdict(paired_ffi003),
        "FFI-004_cross_language_mmap_roundtrip": _eval_single_surface(
            "FFI-004_cross_language_mmap_roundtrip",
            ffi004_samples,
            warmups=warmups,
            trials=trials,
            resamples=resamples,
            ci_enforced=ci_enforced,
            env=env,
        ),
    }
    all_pass = all(
        results[k]["status"] == "PASS"
        for k in (
            "FFI-001_hot_hash_and_layout",
            "FFI-002_json_context_and_admission",
            "FFI-003_vec_copy_vs_pybuffer_64k",
            "FFI-004_cross_language_mmap_roundtrip",
        )
    )
    return {
        "schema": SCHEMA,
        "truth_claim": False,
        "status": "MEASURED_LOCAL_ONLY" if all_pass else "FAILED",
        "overall_ok": all_pass,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
        "native_module_path": getattr(native, "__file__", "unknown"),
        "environment_hash": env.environment_hash,
        "results": results,
        "open_loop_tail": asdict(open_loop_report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark CPython <-> Rust PyO3 FFI boundary (FFI-001..004)")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--ci", action="store_true", help="Enforce B=10,000 resamples")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    resamples = DEFAULT_BOOTSTRAP_RESAMPLES if args.ci else MIN_DEV_BOOTSTRAP_RESAMPLES
    report = run_ffi_benchmark(
        trials=args.trials,
        warmups=args.warmups,
        resamples=resamples,
        ci_enforced=args.ci,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
