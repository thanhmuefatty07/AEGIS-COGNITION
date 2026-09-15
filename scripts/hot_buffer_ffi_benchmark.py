"""Measure the Python-to-Rust hot-evidence buffer boundary.

The two modes use the same payload, arena limits, hashing, and JSON response.
Only the input conversion differs: ``legacy_vec`` asks PyO3 to materialize a
``Vec<u8>``, while ``buffer_view`` consumes the Python buffer directly and
copies once into the Rust-owned arena. Each row runs in a fresh child process
so the peak working-set observation belongs to one operation.

This is host evidence, not a universal performance claim.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from statistics import median
from typing import Any

import blake3

from scripts.mmap_stream_buffer_benchmark import rss_bytes

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None


WORKER_READY = "AEGIS_HOT_BUFFER_WORKER_READY"
MODES = ("legacy_vec", "buffer_view")
DEFAULT_PAYLOADS = (4 * 1024 * 1024, 16 * 1024 * 1024)
DEFAULT_REPETITIONS = 7
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _payload(payload_len: int, seed: int = 0) -> bytes:
    chunk_len = 1024 * 1024
    chunk = bytes(((index * 31 + 7 + seed * 13) & 0xFF) for index in range(chunk_len))
    full_chunks, remainder = divmod(payload_len, chunk_len)
    return chunk * full_chunks + chunk[:remainder]


def _process_peak_rss_bytes() -> int | None:
    """Return this worker's process high-water RSS in bytes."""

    if os.name == "nt":
        import ctypes.wintypes as wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(MemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.OpenProcess(0x1000, False, os.getpid())  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return None
        try:
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.peak_working_set_size)
        finally:
            kernel32.CloseHandle(process)

    if resource is None:
        return None
    try:
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except AttributeError, OSError, ValueError:
        return None
    # Linux reports KiB; macOS and the other BSDs report bytes.
    return value * 1024 if sys.platform.startswith("linux") else value


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
    else:
        with suppress(OSError):
            process.terminate()
    with suppress(subprocess.TimeoutExpired, OSError):
        process.wait(timeout=5)


def _validate_response(
    response: dict[str, Any] | None,
    payload: bytes,
) -> str | None:
    if not isinstance(response, dict):
        return "native response was not an object"
    expected_artifact_hash = blake3.blake3(payload).hexdigest()
    if (
        response.get("schema") != "aegis-hot-arena-commit-v1"
        or response.get("truth_claim") is not False
        or response.get("byte_len") != len(payload)
        or response.get("handle_valid") is not True
        or not isinstance(response.get("artifact_hash"), str)
        or not HEX_DIGEST.fullmatch(response["artifact_hash"])
        or response["artifact_hash"] != expected_artifact_hash
    ):
        return "hot commit response failed the correctness contract"
    return None


def worker_main(mode: str, payload_len: int, done_path: Path) -> int:
    try:
        import aegis_nerve
    except ImportError:
        from aegis_cognition import aegis_nerve

    payload = _payload(payload_len)
    baseline_peak_rss = _process_peak_rss_bytes()
    function = aegis_nerve.aegis_hot_commit if mode == "legacy_vec" else aegis_nerve.aegis_hot_commit_buffer
    print(f"{WORKER_READY} {os.getpid()}", flush=True)
    sys.stdin.readline()
    started = time.perf_counter_ns()
    error: str | None = None
    response: dict[str, Any] | None = None
    try:
        response = json.loads(function(payload, "PROD"))
        error = _validate_response(response, payload)
    except Exception as exc:  # report failures as evidence instead of hiding them
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    operation_peak_rss = _process_peak_rss_bytes()
    staged_path = done_path.with_suffix(".tmp")
    staged_path.write_text(
        json.dumps(
            {
                "elapsed_ms": elapsed_ms,
                "error": error,
                "response": response,
                "baseline_peak_rss_bytes": baseline_peak_rss,
                "operation_peak_rss_bytes": operation_peak_rss,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(staged_path, done_path)
    sys.stdin.readline()
    print(json.dumps({"elapsed_ms": elapsed_ms, "error": error}, sort_keys=True), flush=True)
    return 0 if error is None else 1


def measure_one(mode: str, payload_len: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"aegis-hot-buffer-{mode}-") as directory:
        done_path = Path(directory) / "done.json"
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--mode",
                mode,
                "--payload-bytes",
                str(payload_len),
                "--done-path",
                str(done_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        try:
            assert process.stdout is not None
            ready = process.stdout.readline().strip()
            if not ready.startswith(f"{WORKER_READY} "):
                raise RuntimeError(f"worker handshake failed: {ready!r}")
            start_rss = rss_bytes(process.pid)
            assert process.stdin is not None
            process.stdin.write("\n")
            process.stdin.flush()
            samples: list[int] = []
            deadline = time.monotonic() + 60.0
            while not done_path.exists() and process.poll() is None:
                sample = rss_bytes(process.pid)
                if sample is not None:
                    samples.append(sample)
                if time.monotonic() >= deadline:
                    raise TimeoutError("hot buffer benchmark worker exceeded 60 seconds")
                time.sleep(0.001)
            if not done_path.is_file():
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise RuntimeError(f"worker exited before evidence: {stderr.strip()}")
            evidence: dict[str, Any] | None = None
            for _ in range(1000):
                try:
                    parsed = json.loads(done_path.read_text(encoding="utf-8"))
                except OSError, json.JSONDecodeError:
                    time.sleep(0.001)
                    continue
                if isinstance(parsed, dict):
                    evidence = parsed
                    break
            if evidence is None:
                raise RuntimeError("worker wrote no readable hot-buffer evidence")
            process.stdin.write("\n")
            process.stdin.flush()
            process.wait(timeout=10)
            peak_rss = max(samples) if samples else None
            return {
                "mode": mode,
                "payload_bytes": payload_len,
                "elapsed_ms": evidence.get("elapsed_ms"),
                "error": evidence.get("error"),
                "response": evidence.get("response"),
                "start_rss_bytes": start_rss,
                "sampled_peak_rss_bytes": peak_rss,
                "peak_rss_bytes": evidence.get("operation_peak_rss_bytes"),
                "baseline_peak_rss_bytes": evidence.get("baseline_peak_rss_bytes"),
                "peak_rss_delta_bytes": (
                    evidence["operation_peak_rss_bytes"] - evidence["baseline_peak_rss_bytes"]
                    if isinstance(evidence.get("operation_peak_rss_bytes"), int)
                    and isinstance(evidence.get("baseline_peak_rss_bytes"), int)
                    else None
                ),
                "rss_samples": len(samples),
            }
        except BaseException:
            _terminate(process)
            raise


def summarize(rows: list[dict[str, Any]], payload_len: int) -> dict[str, Any]:
    by_mode = {mode: [row for row in rows if row["mode"] == mode] for mode in MODES}
    valid = all(
        row.get("error") is None
        and isinstance(row.get("elapsed_ms"), int | float)
        and isinstance(row.get("peak_rss_delta_bytes"), int)
        for row in rows
    )
    legacy_name, buffered_name = MODES
    legacy = by_mode[legacy_name]
    buffered = by_mode[buffered_name]
    legacy_time = median(float(row["elapsed_ms"]) for row in legacy) if legacy else None
    buffer_time = median(float(row["elapsed_ms"]) for row in buffered) if buffered else None
    legacy_rss = max((int(row["peak_rss_delta_bytes"]) for row in legacy), default=None)
    buffer_rss = max((int(row["peak_rss_delta_bytes"]) for row in buffered), default=None)
    artifact_hashes: set[str] = set()
    for row in rows:
        response = row.get("response")
        if not isinstance(response, dict):
            continue
        if isinstance(response.get("artifact_hash"), str):
            artifact_hashes.add(response["artifact_hash"])
    return {
        "payload_bytes": payload_len,
        "repetitions": len(rows) // len(MODES),
        "valid": valid,
        "legacy_mode": legacy_name,
        "buffer_mode": buffered_name,
        "legacy_elapsed_median_ms": legacy_time,
        "buffer_elapsed_median_ms": buffer_time,
        "buffer_elapsed_delta_percent": (
            ((buffer_time / legacy_time) - 1.0) * 100.0 if legacy_time and buffer_time else None
        ),
        "legacy_peak_rss_delta_max_bytes": legacy_rss,
        "buffer_peak_rss_delta_max_bytes": buffer_rss,
        "buffer_peak_rss_reduction_percent": (
            (1.0 - (buffer_rss / legacy_rss)) * 100.0 if legacy_rss and buffer_rss is not None else None
        ),
        "artifact_hashes": sorted(artifact_hashes),
    }


def run_benchmark(payloads: tuple[int, ...], repetitions: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for payload_len in payloads:
        for repetition in range(repetitions):
            for mode in MODES:
                row = measure_one(mode, payload_len)
                row["repetition"] = repetition
                rows.append(row)
    summaries = [
        summarize(
            [row for row in rows if row["payload_bytes"] == size],
            size,
        )
        for size in payloads
    ]
    status = "MEASURED_HOST_ONLY" if all(item["valid"] for item in summaries) else "FAILED"
    return {
        "schema": "aegis-hot-buffer-ffi-benchmark-v1",
        "status": status,
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pid_isolated": True,
        },
        "configuration": {
            "payload_bytes": list(payloads),
            "repetitions": repetitions,
            "modes": list(MODES),
            "same_payload_and_native_contract": True,
        },
        "summaries": summaries,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--payload-bytes", type=int, action="append")
    parser.add_argument("--done-path", type=Path)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.mode is None or args.payload_bytes is None or len(args.payload_bytes) != 1 or args.done_path is None:
            raise SystemExit("worker requires --mode, one --payload-bytes, and --done-path")
        return worker_main(args.mode, args.payload_bytes[0], args.done_path)
    payloads = tuple(args.payload_bytes or DEFAULT_PAYLOADS)
    if any(size <= 0 or size > 16 * 1024 * 1024 for size in payloads):
        raise SystemExit("payload sizes must be between 1 and 16 MiB")
    if args.repetitions < 3:
        raise SystemExit("repetitions must be >= 3")
    result = run_benchmark(payloads, args.repetitions)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "MEASURED_HOST_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
