"""Measure bounded local and cross-FFI payload transfer paths without universal zero-copy claims."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import mmap
import os
import platform
import socket
import sys
import tempfile
import threading
import time
import tracemalloc
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis_cognition.benchmark import (  # noqa: E402
    MIN_DEV_BOOTSTRAP_RESAMPLES,
    BenchmarkProtocolV3,
    BenchmarkHostSample,
    EnvironmentFingerprintV3,
    SesoiPolicy,
    bca_confidence_interval,
    evaluate_paired_benchmark_v3,
    paired_ab_ba_schedule,
    quantile_type7_sorted,
)
from core.python.aegis.native import native_module  # noqa: E402
from core.python.bridge_mmap import MmapBridgeFrame  # noqa: E402

PAYLOAD_BYTES = (64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 16777216)


def _rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except ImportError, OSError:
        return None


def _copy(payload: bytes) -> None:
    target = bytearray(len(payload))
    target[:] = payload


def _memoryview(payload: bytes) -> None:
    view = memoryview(payload)
    try:
        _ = view[:]
    finally:
        view.release()


def _mmap_view(payload: bytes) -> None:
    region = mmap.mmap(-1, len(payload))
    try:
        region[:] = payload
        view = memoryview(region)
        try:
            _ = view[0]
        finally:
            view.release()
    finally:
        region.close()


def _socket_roundtrip(payload: bytes) -> None:
    left, right = socket.socketpair()
    received = 0
    reader_error: list[BaseException] = []

    def drain() -> None:
        nonlocal received
        try:
            while received < len(payload):
                chunk = right.recv(min(1024 * 1024, len(payload) - received))
                if not chunk:
                    raise ConnectionError("socket peer closed before the payload was drained")
                received += len(chunk)
        except BaseException as exc:  # propagate the reader failure to the caller
            reader_error.append(exc)

    reader = threading.Thread(target=drain, name="aegis-payload-drain")
    reader.start()
    try:
        view = memoryview(payload)
        while view:
            sent = left.send(view)
            if sent <= 0:
                raise ConnectionError("socket peer closed before the payload was sent")
            view = view[sent:]
        reader.join(timeout=10)
        if reader.is_alive():
            raise TimeoutError("socket payload drain exceeded the bounded timeout")
        if reader_error:
            raise reader_error[0]
        if received != len(payload):
            raise ValueError("socket payload roundtrip received an incomplete payload")
    finally:
        with contextlib.suppress(OSError):
            left.shutdown(socket.SHUT_WR)
        left.close()
        right.close()
        if reader.is_alive():
            reader.join(timeout=1)


def _aegis_hot_commit_vec(payload: bytes) -> None:
    native = native_module()
    raw = native.aegis_hot_commit(payload, "DEV")
    if '"handle_valid":true' not in raw:
        raise RuntimeError("aegis_hot_commit rejected payload")


def _aegis_hot_commit_buffer(payload: bytes) -> None:
    native = native_module()
    view = memoryview(payload)
    try:
        raw = native.aegis_hot_commit_buffer(view, "DEV")
        if '"handle_valid":true' not in raw:
            raise RuntimeError("aegis_hot_commit_buffer rejected payload")
    finally:
        view.release()


def _aegis_mmap_bridge_ipc(payload: bytes, bridge_dir: Path) -> None:
    native = native_module()
    frame_path = bridge_dir / f"payload-{len(payload)}.aegmmap"
    frame_str = str(frame_path)
    native.aegis_write_mmap_bridge_pattern(frame_str, 101, 202, len(payload))
    with MmapBridgeFrame(frame_path) as bridge:
        view = bridge.payload_view()
        try:
            _ = view[0] ^ view[-1]
        finally:
            view.release()
    if not native.aegis_validate_mmap_bridge_frame(frame_str):
        raise RuntimeError("aegis_validate_mmap_bridge_frame failed")


METHODS = {
    "python_bytearray_copy": ("copy", _copy),
    "python_memoryview_candidate": ("candidate_only", _memoryview),
    "anonymous_mmap_candidate": ("candidate_only", _mmap_view),
    "local_socketpair": ("copying_ipc", _socket_roundtrip),
    "aegis_hot_commit_vec": ("copying_ffi", _aegis_hot_commit_vec),
    "aegis_hot_commit_buffer": ("pybuffer_ffi", _aegis_hot_commit_buffer),
}


def _measure_peak_alloc_bytes(function: Any, payload: bytes) -> int:
    """Measure peak Python heap allocation outside latency timing loops."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        function(payload)
        _current, peak = tracemalloc.get_traced_memory()
        return int(peak)
    finally:
        tracemalloc.stop()


def _samples(method: str, payload: bytes, iterations: int, *, function: Any, semantic: str) -> dict[str, Any]:
    durations: list[int] = []
    cpu_ns = 0
    # Warmup once prior to measurement.
    function(payload)
    for _ in range(iterations):
        before_cpu = time.process_time_ns()
        start = time.perf_counter_ns()
        function(payload)
        durations.append(max(1, time.perf_counter_ns() - start))
        cpu_ns += max(0, time.process_time_ns() - before_cpu)

    # Measure allocation peak and process RSS cleanly outside the timed latency loop.
    alloc_peak = _measure_peak_alloc_bytes(function, payload)
    rss_peak = _rss_bytes()

    ordered = sorted(float(d) for d in durations)
    p50 = quantile_type7_sorted(ordered, 0.50)
    p95 = quantile_type7_sorted(ordered, 0.95)
    mean_ci = None
    if len(ordered) >= 2:
        ci = bca_confidence_interval(
            f"{method}_{len(payload)}",
            [float(d) for d in durations],
            estimand="p50",
            alpha=0.05,
            resamples=MIN_DEV_BOOTSTRAP_RESAMPLES,
            seed=0xAE61_0014 ^ len(payload),
        )
        mean_ci = {"ci_low": ci.ci_low, "ci_high": ci.ci_high}

    total_seconds = sum(durations) / 1_000_000_000.0
    return {
        "method": method,
        "semantic": semantic,
        "supported": True,
        "iterations": iterations,
        "p50_ns": p50,
        "p95_ns": p95,
        "p50_bca_95_ci_ns": mean_ci,
        "throughput_bytes_per_second": (len(payload) * iterations / total_seconds) if total_seconds else 0.0,
        "allocation_peak_bytes": alloc_peak,
        "copy_operations_declared": 1 if semantic in {"copy", "copying_ipc", "copying_ffi"} else 0,
        "rss_bytes_peak": rss_peak,
        "cpu_time_ns": cpu_ns,
        "cpu_measurement_status": "MEASURED" if cpu_ns > 0 else "CLOCK_RESOLUTION_TOO_COARSE",
    }


def _paired_ab_ba_v3_comparison(payload: bytes) -> dict[str, Any]:
    """Run paired AB/BA evaluation of `aegis_hot_commit` (Vec copy) vs `aegis_hot_commit_buffer` (PyBuffer)."""
    native = native_module()
    mv = memoryview(payload)
    warmups = 10
    trials = 30
    arm_a: list[float] = []
    arm_b: list[float] = []
    exec_order: list[str] = []
    host_before = BenchmarkHostSample.capture()
    try:
        for _ in range(warmups):
            native.aegis_hot_commit(payload, "DEV")
            native.aegis_hot_commit_buffer(mv, "DEV")
        for order in paired_ab_ba_schedule(trials):
            exec_order.append(order)
            for arm in order:
                if arm == "A":
                    t0 = time.perf_counter_ns()
                    native.aegis_hot_commit(payload, "DEV")
                    arm_a.append(float(max(1, time.perf_counter_ns() - t0)))
                else:
                    t0 = time.perf_counter_ns()
                    native.aegis_hot_commit_buffer(mv, "DEV")
                    arm_b.append(float(max(1, time.perf_counter_ns() - t0)))
    finally:
        mv.release()

    env = EnvironmentFingerprintV3.capture_host(host_before, network_policy="offline", workspace_root=ROOT)
    protocol = BenchmarkProtocolV3(
        name="nv014_vec_copy_vs_pybuffer_64k",
        metric="latency_ns",
        direction="lower_is_better",
        alpha=0.05,
        min_trials=trials,
        warmups=warmups,
        paired_blocks=trials,
        bootstrap_resamples=MIN_DEV_BOOTSTRAP_RESAMPLES,
        ci_enforced=False,
        preregistered_seeds=(1, 2, 3, 4, 5),
        frozen_split_hash=hashlib.sha256(payload).hexdigest(),
        contamination_checks=("load_avg", "thermal", "leak"),
        sesoi=SesoiPolicy(),
    )
    comp = evaluate_paired_benchmark_v3(
        protocol,
        arm_a,
        arm_b,
        warmups=warmups,
        paired_blocks=trials,
        environment=env,
        execution_order=exec_order,
        enforce_clean_worktree=False,
    )
    return asdict(comp)


def run(output: Path | None, iterations: int) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "aegis-communication-payload-benchmark-v2",
        "protocol_version": "aegis-bench-protocol-v3",
        "truth_claim": False,
        "status": "MEASURED_LOCAL_ONLY",
        "verified": False,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
        "payload_classes_bytes": list(PAYLOAD_BYTES),
        "zero_copy_claim": False,
        "zero_copy_scope": (
            "No universal zero-copy claim; cross-boundary paths include live Rust FFI PyBuffer and MmapBridgeFrame."
        ),
        "unsupported_methods": {},
        "results": [],
    }
    with tempfile.TemporaryDirectory(prefix="aegis-nv014-") as tmpdir:
        bridge_dir = Path(tmpdir)
        for size in PAYLOAD_BYTES:
            payload = b"aegis" * (size // 5) + b"a" * (size % 5)
            local_iterations = max(1, iterations if size < 1 << 20 else min(iterations, 3))
            for method, (semantic, fn) in METHODS.items():
                try:
                    result = _samples(method, payload, local_iterations, function=fn, semantic=semantic)
                except (OSError, ValueError, BufferError, ImportError, RuntimeError) as exc:
                    report["unsupported_methods"].setdefault(method, str(exc))
                    result = {"method": method, "supported": False, "reason": str(exc)}
                result["payload_bytes"] = size
                report["results"].append(result)
            try:
                mmap_res = _samples(
                    "aegis_mmap_bridge_ipc",
                    payload,
                    local_iterations,
                    function=lambda p: _aegis_mmap_bridge_ipc(p, bridge_dir),
                    semantic="mmap_bridge_ipc",
                )
            except (OSError, ValueError, BufferError, ImportError, RuntimeError) as exc:
                report["unsupported_methods"].setdefault("aegis_mmap_bridge_ipc", str(exc))
                mmap_res = {"method": "aegis_mmap_bridge_ipc", "supported": False, "reason": str(exc)}
            mmap_res["payload_bytes"] = size
            report["results"].append(mmap_res)

    try:
        report["paired_v3_ffi_comparison_64k"] = _paired_ab_ba_v3_comparison(b"aegis" * (65_536 // 5) + b"a")
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        report["paired_v3_ffi_comparison_64k"] = {"status": "NOT VERIFIED", "reason": str(exc)}

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    run(args.output, args.iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
