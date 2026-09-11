"""Measure bounded CPU/RAM and CPU/RAM/SSD execution paths.

This is a local measurement harness, not a scheduler and not evidence that
SSD spill improves every workload.  Both modes compute the same deterministic
result; the spill mode keeps the intermediate data in a bounded chunk while a
temporary file is flushed and read back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import tempfile
import time
from contextlib import suppress
import tracemalloc
from pathlib import Path
from typing import Any, Literal

Mode = Literal["ram", "spill"]
MIN_PAYLOAD_BYTES = 4 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MIN_ITERATIONS = 1
MAX_ITERATIONS = 500
CHUNK_BYTES = 64 * 1024


def _validate(payload_bytes: int, iterations: int) -> None:
    if type(payload_bytes) is not int or not MIN_PAYLOAD_BYTES <= payload_bytes <= MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"payload_bytes must be an integer in [{MIN_PAYLOAD_BYTES}, {MAX_PAYLOAD_BYTES}]"
        )
    if type(iterations) is not int or not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError(
            f"iterations must be an integer in [{MIN_ITERATIONS}, {MAX_ITERATIONS}]"
        )


def _payload(size: int) -> bytes:
    return bytes((index * 17 + 29) & 0xFF for index in range(size))


def _transform(chunk: bytes, salt: int) -> bytes:
    return bytes(value ^ salt for value in chunk)


def _ram_path(payload: bytes) -> bytes:
    intermediate = _transform(payload, 0x5A)
    output = _transform(intermediate, 0xA5)
    return hashlib.blake2b(output, digest_size=32).digest()


def _spill_path(payload: bytes, directory: str) -> tuple[bytes, int, int, int]:
    written = 0
    read = 0
    fsyncs = 0
    path = Path(directory) / f"aegis-cooperative-{os.getpid()}-{time.time_ns()}.bin"
    try:
        with path.open("wb") as handle:
            for start in range(0, len(payload), CHUNK_BYTES):
                chunk = _transform(payload[start : start + CHUNK_BYTES], 0x5A)
                handle.write(chunk)
                written += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
            fsyncs = 1

        digest = hashlib.blake2b(digest_size=32)
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(_transform(chunk, 0xA5))
                read += len(chunk)
        return digest.digest(), written, read, fsyncs
    finally:
        with suppress(FileNotFoundError):
            path.unlink()


def _rss_bytes() -> int | None:
    """Return a process RSS observation when the host exposes one."""

    if os.name == "nt":
        import ctypes

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
        function = psapi.GetProcessMemoryInfo
        function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        function.restype = ctypes.c_int
        if function(ctypes.c_void_p(-1), ctypes.byref(counters), ctypes.sizeof(counters)):
            return int(counters.working_set_size)
        return None

    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, ImportError, OSError, ValueError):
        return None
    return value if platform.system() == "Darwin" else value * 1024


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _measure_mode(mode: Mode, payload: bytes, iterations: int, directory: str | None) -> dict[str, Any]:
    samples_ms: list[float] = []
    digest: bytes | None = None
    io_written = 0
    io_read = 0
    fsyncs = 0
    rss_before = _rss_bytes()
    tracemalloc.start()
    wall_start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    try:
        for _ in range(iterations):
            started = time.perf_counter_ns()
            if mode == "ram":
                observed = _ram_path(payload)
            else:
                if directory is None:
                    raise ValueError("spill mode requires a temporary directory")
                observed, written, read, flushes = _spill_path(payload, directory)
                io_written += written
                io_read += read
                fsyncs += flushes
            if digest is None:
                digest = observed
            elif observed != digest:
                raise AssertionError("cooperative path produced inconsistent digest")
            samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    finally:
        _current, peak_allocated = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    wall_ns = time.perf_counter_ns() - wall_start
    cpu_ns = time.process_time_ns() - cpu_start
    return {
        "mode": mode,
        "iterations": iterations,
        "correctness_digest": digest.hex() if digest is not None else None,
        "latency_ms": {
            "mean": statistics.fmean(samples_ms),
            "p50": _percentile(samples_ms, 0.50),
            "p95": _percentile(samples_ms, 0.95),
        },
        "resource_metrics": {
            "cpu_time_ns": cpu_ns,
            "wall_time_ns": wall_ns,
            "cpu_utilization_ratio": cpu_ns / wall_ns if wall_ns else None,
            "rss_before_bytes": rss_before,
            "rss_after_bytes": _rss_bytes(),
            "peak_tracemalloc_bytes": peak_allocated,
        },
        "io": {
            "bytes_written": io_written,
            "bytes_read": io_read,
            "fsync_operations": fsyncs,
        },
        "raw_samples_ms": samples_ms,
    }


def run_benchmark(
    *,
    mode: Literal["ram", "spill", "both"] = "both",
    payload_bytes: int = 1 * 1024 * 1024,
    iterations: int = 5,
) -> dict[str, Any]:
    """Run a correctness-checked, bounded local comparison."""

    _validate(payload_bytes, iterations)
    if mode not in {"ram", "spill", "both"}:
        raise ValueError("mode must be ram, spill, or both")
    payload = _payload(payload_bytes)
    expected = _ram_path(payload).hex()
    modes: list[Mode] = ["ram", "spill"] if mode == "both" else [mode]
    with tempfile.TemporaryDirectory(prefix="aegis-cooperative-") as directory:
        results = [_measure_mode(item, payload, iterations, directory) for item in modes]
        temp_files_remaining = any(Path(directory).iterdir())
    all_correct = all(item["correctness_digest"] == expected for item in results)
    return {
        "schema": "aegis-cooperative-resource-benchmark-v1",
        "status": "MEASURED_LOCAL_ONLY" if all_correct and not temp_files_remaining else "FAILED",
        "measurement_scope": "one host; no cross-device or universal-performance claim",
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "workload": {
            "payload_bytes": payload_bytes,
            "chunk_bytes": CHUNK_BYTES,
            "deterministic": True,
        },
        "correctness": {
            "expected_digest": expected,
            "all_modes_match": all_correct,
            "temporary_files_cleaned": not temp_files_remaining,
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("ram", "spill", "both"), default="both")
    parser.add_argument("--payload-bytes", type=int, default=1 * 1024 * 1024)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(
        mode=args.mode,
        payload_bytes=args.payload_bytes,
        iterations=args.iterations,
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if result["status"] != "MEASURED_LOCAL_ONLY":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
