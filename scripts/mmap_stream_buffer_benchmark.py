"""Measure bounded mmap-bridge producer strategies on one host.

Each measurement runs in a fresh child process so the sampled RSS belongs to
one operation and a previous full-buffer allocation cannot contaminate the
next strategy. The Rust writer, frame validation, and BLAKE3 path are shared
by every strategy. This is evidence for the measured host, not a universal
performance claim.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHUNK_BYTES = 1024 * 1024
DEFAULT_FILE_STREAM_CHUNK_BYTES = CHUNK_BYTES
DEFAULT_PAYLOADS = (16 * 1024 * 1024, 64 * 1024 * 1024)
WORKER_READY = "AEGIS_MMAP_WORKER_READY"
MODES = (
    "full_buffer_native",
    "direct_view_native",
    "chunked_bytes_native",
    "reusable_buffer_native",
    "file_stream_python",
    "file_stream_native",
)
PATTERN_CHUNK = bytes(((index * 31 + 7) & 0xFF) for index in range(CHUNK_BYTES))
PATTERN_VIEW = memoryview(PATTERN_CHUNK)


def native_module() -> Any:
    try:
        import aegis_nerve
    except ImportError:
        from aegis_cognition import aegis_nerve

    return aegis_nerve


def _windows_rss_bytes(pid: int) -> int | None:
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
    process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.working_set_size)
    finally:
        kernel32.CloseHandle(process)


def rss_bytes(pid: int) -> int | None:
    """Return current RSS for a process using a platform-native observation."""

    if os.name == "nt":
        try:
            return _windows_rss_bytes(pid)
        except OSError:
            return None
        except AttributeError:
            return None
        except ctypes.ArgumentError:
            return None

    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
            if len(fields) < 2:
                return None
            return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except OSError:
            return None
        except ValueError:
            return None

    if sys.platform == "darwin":
        # ``ps`` is a system utility on supported macOS runners and reports
        # RSS in KiB. It avoids treating ru_maxrss (a process high-water mark)
        # as a current per-operation working-set measurement.
        try:
            completed = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=False,
                timeout=1,
            )
            value = completed.stdout.strip()
            return int(value) * 1024 if completed.returncode == 0 and value else None
        except OSError:
            return None
        except subprocess.SubprocessError:
            return None
        except ValueError:
            return None

    return None


def _full_buffer(path: Path, payload_len: int, _file_chunk_bytes: int) -> None:
    from core.python.bridge_mmap import MmapBridgeWriter

    full_chunks, remainder = divmod(payload_len, CHUNK_BYTES)
    payload = PATTERN_CHUNK * full_chunks + PATTERN_CHUNK[:remainder]
    with MmapBridgeWriter(path, 111, 222, payload_len) as writer:
        writer.write(payload)
        writer.finish()


def _direct_view(path: Path, payload_len: int, _file_chunk_bytes: int) -> None:
    from core.python.bridge_mmap import MmapBridgeWriter

    with MmapBridgeWriter(path, 111, 222, payload_len) as writer:
        for offset in range(0, payload_len, CHUNK_BYTES):
            length = min(CHUNK_BYTES, payload_len - offset)
            writer.write(PATTERN_VIEW[:length])
        writer.finish()


def _chunked_bytes(path: Path, payload_len: int, _file_chunk_bytes: int) -> None:
    from core.python.bridge_mmap import MmapBridgeWriter

    with MmapBridgeWriter(path, 111, 222, payload_len) as writer:
        for offset in range(0, payload_len, CHUNK_BYTES):
            length = min(CHUNK_BYTES, payload_len - offset)
            writer.write(bytes(PATTERN_VIEW[:length]))
        writer.finish()


def _reusable_buffer(path: Path, payload_len: int, _file_chunk_bytes: int) -> None:
    from core.python.bridge_mmap import MmapBridgeWriter

    reusable = bytearray(CHUNK_BYTES)
    reusable_view = memoryview(reusable)
    with MmapBridgeWriter(path, 111, 222, payload_len) as writer:
        for offset in range(0, payload_len, CHUNK_BYTES):
            length = min(CHUNK_BYTES, payload_len - offset)
            reusable_view[:length] = PATTERN_VIEW[:length]
            writer.write(reusable_view[:length])
        writer.finish()


def _prepare_file_source(path: Path, payload_len: int) -> None:
    with path.open("wb") as source:
        for offset in range(0, payload_len, CHUNK_BYTES):
            length = min(CHUNK_BYTES, payload_len - offset)
            source.write(PATTERN_VIEW[:length])


def _file_stream(path: Path, payload_len: int, file_chunk_bytes: int) -> None:
    from core.python.bridge_mmap import MmapBridgeWriter

    source = path.with_suffix(".source.bin")
    with MmapBridgeWriter(path, 111, 222, payload_len) as writer:
        writer.write_file(source, chunk_bytes=file_chunk_bytes)
        writer.finish()


def _file_stream_python(path: Path, payload_len: int, file_chunk_bytes: int) -> None:
    from core.python.bridge_mmap import MmapBridgeWriter

    source = path.with_suffix(".source.bin")
    reusable = bytearray(file_chunk_bytes)
    reusable_view = memoryview(reusable)
    remaining = payload_len
    try:
        with source.open("rb") as source_file, MmapBridgeWriter(path, 111, 222, payload_len) as writer:
            while remaining:
                read = source_file.readinto(reusable_view[: min(len(reusable), remaining)])
                if not read:
                    raise ValueError("benchmark source ended before declared payload length")
                writer.write(reusable_view[:read])
                remaining -= read
            if source_file.read(1):
                raise ValueError("benchmark source exceeds declared payload length")
            writer.finish()
    finally:
        reusable_view.release()


OPERATIONS: dict[str, Callable[[Path, int, int], None]] = {
    "full_buffer_native": _full_buffer,
    "direct_view_native": _direct_view,
    "chunked_bytes_native": _chunked_bytes,
    "reusable_buffer_native": _reusable_buffer,
    "file_stream_python": _file_stream_python,
    "file_stream_native": _file_stream,
}


def worker_main(
    mode: str,
    payload_len: int,
    path: Path,
    done_path: Path,
    file_chunk_bytes: int,
) -> int:
    operation = OPERATIONS[mode]
    # Load the extension and Python bridge before the measurement handshake;
    # import cost is a shared startup cost, not part of the buffer strategy.
    native_module()
    __import__("core.python.bridge_mmap", fromlist=["MmapBridgeWriter"])
    if mode in {"file_stream_python", "file_stream_native"}:
        _prepare_file_source(path.with_suffix(".source.bin"), payload_len)
    print(f"{WORKER_READY} {os.getpid()}", flush=True)
    sys.stdin.readline()
    started = time.perf_counter_ns()
    error: str | None = None
    try:
        operation(path, payload_len, file_chunk_bytes)
    except Exception as exc:  # report the failure to the parent as evidence
        error = f"{type(exc).__name__}: {exc}"
    producer_elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    # Publish the completion marker atomically.  The parent polls for the
    # marker while the worker is still alive; writing directly to the final
    # path would let it observe a partially written JSON document.
    done_tmp_path = done_path.with_name(f"{done_path.name}.tmp")
    done_tmp_path.write_text(
        json.dumps(
            {
                "elapsed_ms": producer_elapsed_ms,
                "error": error,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(done_tmp_path, done_path)
    # Let the parent stop RSS sampling before validation maps and hashes the
    # file. Validation remains mandatory, but it is a separate correctness
    # phase rather than producer-memory evidence.
    sys.stdin.readline()
    metadata: dict[str, object] | None = None
    if error is None:
        try:
            metadata = {
                "validated": bool(native_module().aegis_validate_mmap_bridge_frame(str(path))),
                "file_bytes": path.stat().st_size,
            }
            if not metadata["validated"]:
                error = "native validation returned false"
        except Exception as exc:  # report validation failures as evidence
            error = f"{type(exc).__name__}: {exc}"
    result = {
        "elapsed_ms": producer_elapsed_ms,
        "metadata": metadata,
        "error": error,
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if error is None else 1


def _read_ready(process: subprocess.Popen[str], ready: list[str], done: threading.Event) -> None:
    assert process.stdout is not None
    ready.append(process.stdout.readline().strip())
    done.set()


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        else:
            process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)


def measure_one(
    *,
    mode: str,
    payload_len: int,
    repetition: int,
    sequence_position: int,
    root: Path,
    file_chunk_bytes: int,
) -> dict[str, object]:
    path = root / f"frame-{payload_len}-{repetition}-{sequence_position}-{mode}.aegmmap"
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not existing_pythonpath else f"{ROOT}{os.pathsep}{existing_pythonpath}"
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--mode",
            mode,
            "--payload-bytes",
            str(payload_len),
            "--path",
            str(path),
            "--done-path",
            str(path.with_suffix(".done.json")),
            "--file-chunk-bytes",
            str(file_chunk_bytes),
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready: list[str] = []
    ready_done = threading.Event()
    ready_thread = threading.Thread(target=_read_ready, args=(process, ready, ready_done), daemon=True)
    ready_thread.start()
    if not ready_done.wait(timeout=60) or len(ready) != 1:
        _terminate(process)
        return {
            "repetition": repetition,
            "sequence_position": sequence_position,
            "elapsed_ms": None,
            "start_rss_bytes": None,
            "peak_rss_bytes": None,
            "peak_rss_delta_bytes": None,
            "metadata": None,
            "error": "worker did not reach ready state",
        }

    ready_parts = ready[0].split()
    try:
        worker_pid = int(ready_parts[1]) if ready_parts[0] == WORKER_READY else 0
    except IndexError:
        worker_pid = 0
    except ValueError:
        worker_pid = 0
    if worker_pid <= 0:
        _terminate(process)
        return {
            "repetition": repetition,
            "sequence_position": sequence_position,
            "elapsed_ms": None,
            "start_rss_bytes": None,
            "peak_rss_bytes": None,
            "peak_rss_delta_bytes": None,
            "metadata": None,
            "error": f"worker returned malformed ready message: {ready[0]!r}",
        }

    done_path = path.with_suffix(".done.json")
    start_rss = rss_bytes(worker_pid)
    samples: list[int] = []
    stop = threading.Event()

    def monitor() -> None:
        while not stop.is_set():
            value = rss_bytes(worker_pid)
            if value is not None:
                samples.append(value)
            stop.wait(0.01)

    monitor_thread = threading.Thread(target=monitor, name="aegis-mmap-rss", daemon=True)
    monitor_thread.start()
    error: str | None = None
    stdout = ""
    stderr = ""
    try:
        assert process.stdin is not None
        process.stdin.write("go\n")
        process.stdin.flush()
        deadline = time.monotonic() + 180.0
        while not done_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        if not done_path.exists():
            raise TimeoutError("worker did not finish producer phase within 180 seconds")
        stop.set()
        monitor_thread.join(timeout=2)
        done = json.loads(done_path.read_text(encoding="utf-8"))
        if isinstance(done, dict) and done.get("error") is not None:
            error = str(done["error"])
        process.stdin.write("validate\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=180)
    except TimeoutError as exc:
        error = f"{type(exc).__name__}: {exc}"
        _terminate(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except OSError:
            stdout, stderr = "", ""
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
        _terminate(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except OSError:
            stdout, stderr = "", ""
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    except subprocess.TimeoutExpired as exc:
        error = f"{type(exc).__name__}: {exc}"
        _terminate(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except OSError:
            stdout, stderr = "", ""
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    except json.JSONDecodeError as exc:
        error = f"{type(exc).__name__}: {exc}"
        _terminate(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except OSError:
            stdout, stderr = "", ""
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    finally:
        if not stop.is_set():
            stop.set()
            monitor_thread.join(timeout=2)

    worker_result: dict[str, object] | None = None
    for line in reversed(stdout.splitlines()):
        if line.strip():
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                break
            if isinstance(decoded, dict):
                worker_result = decoded
            break
    if worker_result is None and error is None:
        error = f"worker returned no JSON evidence: {stderr[-1000:]}"
    if worker_result is not None:
        if worker_result.get("error") is not None:
            error = str(worker_result["error"])
        elapsed = worker_result.get("elapsed_ms")
        metadata = worker_result.get("metadata")
    else:
        elapsed = None
        metadata = None
    if process.returncode not in (0, None) and error is None:
        error = f"worker exited with code {process.returncode}: {stderr[-1000:]}"

    observed = [value for value in (*samples, start_rss) if isinstance(value, int)]
    peak_rss = max(observed) if observed else None
    if start_rss is None or peak_rss is None:
        error = error or "platform RSS observation unavailable"
    if path.exists():
        path.unlink()
    source_path = path.with_suffix(".source.bin")
    if source_path.exists():
        source_path.unlink()
    if done_path.exists():
        done_path.unlink()
    return {
        "repetition": repetition,
        "sequence_position": sequence_position,
        "worker_pid": worker_pid,
        "elapsed_ms": elapsed,
        "start_rss_bytes": start_rss,
        "peak_rss_bytes": peak_rss,
        "peak_rss_delta_bytes": peak_rss - start_rss if start_rss is not None and peak_rss is not None else None,
        "metadata": metadata,
        "error": error,
    }


def summarize(mode: str, payload_len: int, rows: list[dict[str, object]]) -> dict[str, object]:
    elapsed = [float(row["elapsed_ms"]) for row in rows if isinstance(row.get("elapsed_ms"), (int, float))]
    deltas = [int(row["peak_rss_delta_bytes"]) for row in rows if isinstance(row.get("peak_rss_delta_bytes"), int)]
    return {
        "name": mode,
        "payload_bytes": payload_len,
        "repetitions": len(rows),
        "all_completed": all(row.get("error") is None for row in rows),
        "all_files_have_expected_size": all(
            isinstance(row.get("metadata"), dict) and row["metadata"].get("file_bytes") == payload_len + 128
            for row in rows
        ),
        "all_frames_validated": all(
            isinstance(row.get("metadata"), dict) and row["metadata"].get("validated") is True for row in rows
        ),
        "rss_observed_for_all_rows": len(deltas) == len(rows),
        "elapsed_ms_median": median(elapsed) if elapsed else None,
        "peak_rss_delta_bytes_max": max(deltas) if deltas else None,
        "rows": rows,
    }


def measure_payload(payload_len: int, repetitions: int, file_chunk_bytes: int) -> list[dict[str, object]]:
    rows_by_mode: dict[str, list[dict[str, object]]] = {mode: [] for mode in MODES}
    with tempfile.TemporaryDirectory(prefix="aegis-mmap-buffer-") as directory:
        root = Path(directory)
        for repetition in range(repetitions):
            for sequence_position in range(len(MODES)):
                mode = MODES[(repetition + sequence_position) % len(MODES)]
                rows_by_mode[mode].append(
                    measure_one(
                        mode=mode,
                        payload_len=payload_len,
                        repetition=repetition,
                        sequence_position=sequence_position,
                        root=root,
                        file_chunk_bytes=file_chunk_bytes,
                    )
                )
    return [summarize(mode, payload_len, rows_by_mode[mode]) for mode in MODES]


def add_comparison(results: list[dict[str, object]]) -> dict[str, object]:
    baseline = results[0]
    baseline_rss = baseline["peak_rss_delta_bytes_max"]
    baseline_elapsed = baseline["elapsed_ms_median"]
    comparison: dict[str, object] = {
        "same_native_writer": True,
        "same_frame_validation": True,
    }
    for label, item in (
        ("direct_view", results[1]),
        ("chunked_bytes", results[2]),
        ("reusable_buffer", results[3]),
        ("file_stream_python", results[4]),
        ("file_stream_native", results[5]),
    ):
        rss = item["peak_rss_delta_bytes_max"]
        elapsed = item["elapsed_ms_median"]
        comparison[f"{label}_rss_reduction_percent"] = (
            (1.0 - float(rss) / float(baseline_rss)) * 100.0
            if isinstance(baseline_rss, int) and isinstance(rss, int) and baseline_rss > 0
            else None
        )
        comparison[f"{label}_elapsed_delta_percent"] = (
            (float(elapsed) / float(baseline_elapsed) - 1.0) * 100.0
            if isinstance(baseline_elapsed, (int, float)) and isinstance(elapsed, (int, float)) and baseline_elapsed > 0
            else None
        )
    python_file = results[4]
    native_file = results[5]
    python_file_elapsed = python_file["elapsed_ms_median"]
    native_file_elapsed = native_file["elapsed_ms_median"]
    python_file_rss = python_file["peak_rss_delta_bytes_max"]
    native_file_rss = native_file["peak_rss_delta_bytes_max"]
    comparison["file_stream_native_vs_python_elapsed_delta_percent"] = (
        (float(native_file_elapsed) / float(python_file_elapsed) - 1.0) * 100.0
        if isinstance(python_file_elapsed, (int, float))
        and isinstance(native_file_elapsed, (int, float))
        and python_file_elapsed > 0
        else None
    )
    comparison["file_stream_native_vs_python_rss_delta_percent"] = (
        (float(native_file_rss) / float(python_file_rss) - 1.0) * 100.0
        if isinstance(python_file_rss, int) and isinstance(native_file_rss, int) and python_file_rss > 0
        else None
    )
    return comparison


def run_benchmark(payloads: tuple[int, ...], repetitions: int, file_chunk_bytes: int) -> dict[str, object]:
    measurements = []
    for payload_len in payloads:
        results = measure_payload(payload_len, repetitions, file_chunk_bytes)
        measurements.append(
            {
                "payload_bytes": payload_len,
                "comparison": add_comparison(results),
                "results": results,
            }
        )
    valid = all(
        result["all_completed"]
        and result["all_files_have_expected_size"]
        and result["all_frames_validated"]
        and result["rss_observed_for_all_rows"]
        for measurement in measurements
        for result in measurement["results"]
    )
    for measurement in measurements:
        baseline = measurement["results"][0]
        comparison = measurement["comparison"]
        valid = valid and isinstance(baseline["peak_rss_delta_bytes_max"], int)
        valid = valid and baseline["peak_rss_delta_bytes_max"] > 0
        valid = valid and all(
            isinstance(comparison.get(key), (int, float))
            for key in (
                "direct_view_rss_reduction_percent",
                "direct_view_elapsed_delta_percent",
                "chunked_bytes_rss_reduction_percent",
                "chunked_bytes_elapsed_delta_percent",
                "reusable_buffer_rss_reduction_percent",
                "reusable_buffer_elapsed_delta_percent",
                "file_stream_native_rss_reduction_percent",
                "file_stream_native_elapsed_delta_percent",
                "file_stream_python_rss_reduction_percent",
                "file_stream_python_elapsed_delta_percent",
                "file_stream_native_vs_python_elapsed_delta_percent",
                "file_stream_native_vs_python_rss_delta_percent",
            )
        )
    return {
        "schema": "aegis-mmap-stream-buffer-benchmark-v3",
        "status": "MEASURED_HOST_ONLY" if valid else "FAILED",
        "scope": "fresh child per strategy; same Rust writer/frame/hash/validation; counterbalanced order; file source prepared before producer measurement",
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "chunk_bytes": CHUNK_BYTES,
        "file_stream_chunk_bytes": file_chunk_bytes,
        "payloads": list(payloads),
        "repetitions": repetitions,
        "measurements": measurements,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload-bytes", type=int, action="append")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--done-path", type=Path)
    parser.add_argument("--file-chunk-bytes", type=int, default=DEFAULT_FILE_STREAM_CHUNK_BYTES)
    args = parser.parse_args()
    if args.worker:
        if (
            args.mode is None
            or args.path is None
            or args.payload_bytes is None
            or len(args.payload_bytes) != 1
            or args.done_path is None
            or args.file_chunk_bytes <= 0
        ):
            parser.error(
                "worker requires one --payload-bytes, --mode, --path, --done-path, and positive --file-chunk-bytes"
            )
        return worker_main(args.mode, args.payload_bytes[0], args.path, args.done_path, args.file_chunk_bytes)

    payloads = tuple(args.payload_bytes or DEFAULT_PAYLOADS)
    if not payloads or any(value <= 0 for value in payloads):
        parser.error("payload-bytes must be positive")
    if args.repetitions < 1 or args.repetitions > 20:
        parser.error("repetitions must be in 1..20")
    if args.file_chunk_bytes <= 0:
        parser.error("file-chunk-bytes must be positive")
    result = run_benchmark(payloads, args.repetitions, args.file_chunk_bytes)
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if result["status"] == "MEASURED_HOST_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
