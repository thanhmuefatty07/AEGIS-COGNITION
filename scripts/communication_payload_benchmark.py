"""Measure bounded local payload paths without making a universal zero-copy claim."""

from __future__ import annotations

import argparse
import json
import mmap
import platform
import socket
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles


PAYLOAD_BYTES = (64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 16777216)


def _rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        return None


def _copy(payload: bytes) -> None:
    target = bytearray(len(payload))
    target[:] = payload


def _memoryview(payload: bytes) -> None:
    view = memoryview(payload)
    _ = view[:]


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
        try:
            left.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        left.close()
        right.close()
        if reader.is_alive():
            reader.join(timeout=1)


METHODS = {
    "python_bytearray_copy": ("copy", _copy),
    "python_memoryview_candidate": ("candidate_only", _memoryview),
    "anonymous_mmap_candidate": ("candidate_only", _mmap_view),
    "local_socketpair": ("copying_ipc", _socket_roundtrip),
}


def _samples(method: str, payload: bytes, iterations: int) -> dict[str, object]:
    semantic, function = METHODS[method]
    durations: list[int] = []
    allocations: list[int] = []
    rss_values: list[int] = []
    cpu_ns = 0
    tracemalloc.start()
    try:
        for _ in range(iterations):
            tracemalloc.reset_peak()
            before_cpu = time.process_time_ns()
            start = time.perf_counter_ns()
            function(payload)
            durations.append(time.perf_counter_ns() - start)
            cpu_ns += time.process_time_ns() - before_cpu
            _, peak = tracemalloc.get_traced_memory()
            allocations.append(peak)
            rss = _rss_bytes()
            if rss is not None:
                rss_values.append(rss)
    finally:
        tracemalloc.stop()
    ordered = sorted(durations)
    p95 = quantiles(ordered, n=20, method="inclusive")[18] if len(ordered) >= 2 else float(ordered[0])
    total_seconds = sum(durations) / 1_000_000_000
    return {
        "method": method,
        "semantic": semantic,
        "supported": True,
        "iterations": iterations,
        "p50_ns": ordered[len(ordered) // 2],
        "p95_ns": p95,
        "throughput_bytes_per_second": (len(payload) * iterations / total_seconds) if total_seconds else 0.0,
        "allocation_peak_bytes": max(allocations, default=0),
        "copy_operations_declared": 1 if semantic in {"copy", "copying_ipc"} else 0,
        "rss_bytes_peak": max(rss_values, default=None),
        "cpu_time_ns": cpu_ns,
        "cpu_measurement_status": "MEASURED" if cpu_ns > 0 else "CLOCK_RESOLUTION_TOO_COARSE",
    }


def run(output: Path | None, iterations: int) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "aegis-communication-payload-benchmark-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "payload_classes_bytes": list(PAYLOAD_BYTES),
        "zero_copy_claim": False,
        "zero_copy_scope": "No universal zero-copy claim; memoryview/mmap rows are candidate-only observations.",
        "unsupported_methods": {},
        "results": [],
    }
    for size in PAYLOAD_BYTES:
        payload = b"aegis" * (size // 5) + b"a" * (size % 5)
        for method in METHODS:
            try:
                result = _samples(method, payload, max(1, iterations if size < 1 << 20 else min(iterations, 3)))
            except (OSError, ValueError, BufferError) as exc:
                report["unsupported_methods"].setdefault(method, str(exc))
                result = {"method": method, "supported": False, "reason": str(exc)}
            result["payload_bytes"] = size
            report["results"].append(result)
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
