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
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from statistics import median
from typing import Any

from aegis_cognition.agent_efficiency import managed_scratch_dir

try:
    import resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    resource = None


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


def _windows_peak_rss_bytes(pid: int | None = None) -> int | None:
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

    target_pid = os.getpid() if pid is None else pid
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
    process = kernel32.OpenProcess(0x1000, False, target_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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


def _windows_rss_bytes(pid: int) -> int | None:
    return _windows_peak_rss_bytes(pid)


def _parse_linux_vmhwm_bytes(status_text: str) -> int | None:
    for line in status_text.splitlines():
        if line.startswith("VmHWM:"):
            parts = line.split()
            if len(parts) < 2:
                return None
            try:
                value = int(parts[1])
            except ValueError:
                return None
            unit = parts[2].lower() if len(parts) >= 3 else "kb"
            if unit == "kb":
                return value * 1024
            if unit == "b":
                return value
            return None
    return None


def _linux_peak_rss_bytes(pid: int | None = None) -> int | None:
    status_path = Path("/proc/self/status") if pid is None else Path(f"/proc/{pid}/status")
    try:
        return _parse_linux_vmhwm_bytes(status_path.read_text(encoding="ascii"))
    except OSError:
        return None


def _darwin_peak_rss_bytes() -> int | None:
    import ctypes.util

    # Mirrors macOS `struct task_vm_info` through rev3 (`TASK_VM_INFO_REV3_COUNT = 84`);
    # all fields after the initial two int32 counts are 8-byte aligned without `_pack_`.
    class TaskVmInfoRev3(ctypes.Structure):
        _fields_ = [
            ("virtual_size", ctypes.c_uint64),
            ("region_count", ctypes.c_int32),
            ("page_size", ctypes.c_int32),
            ("resident_size", ctypes.c_uint64),
            ("resident_size_peak", ctypes.c_uint64),
            ("device", ctypes.c_uint64),
            ("device_peak", ctypes.c_uint64),
            ("internal", ctypes.c_uint64),
            ("internal_peak", ctypes.c_uint64),
            ("external", ctypes.c_uint64),
            ("external_peak", ctypes.c_uint64),
            ("reusable", ctypes.c_uint64),
            ("reusable_peak", ctypes.c_uint64),
            ("purgeable_volatile_pmap", ctypes.c_uint64),
            ("purgeable_volatile_resident", ctypes.c_uint64),
            ("purgeable_volatile_virtual", ctypes.c_uint64),
            ("compressed", ctypes.c_uint64),
            ("compressed_peak", ctypes.c_uint64),
            ("compressed_lifetime", ctypes.c_uint64),
            ("phys_footprint", ctypes.c_uint64),
            ("min_address", ctypes.c_uint64),
            ("max_address", ctypes.c_uint64),
            ("ledger_phys_footprint_peak", ctypes.c_int64),
            *[(f"_ledger_{index}", ctypes.c_int64) for index in range(20)],
        ]

    system_lib = ctypes.util.find_library("System")
    if system_lib:
        try:
            libc = ctypes.CDLL(system_lib)
            libc.mach_task_self.restype = ctypes.c_uint32
            libc.task_info.argtypes = [
                ctypes.c_uint32,
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            libc.task_info.restype = ctypes.c_int32
            info = TaskVmInfoRev3()
            count = ctypes.c_uint32(ctypes.sizeof(info) // 4)
            task_vm_info_flavor = 22
            if libc.task_info(libc.mach_task_self(), task_vm_info_flavor, ctypes.byref(info), ctypes.byref(count)) == 0:
                if count.value >= 46 and info.ledger_phys_footprint_peak > 0:
                    return int(info.ledger_phys_footprint_peak)
                if count.value >= 40 and info.phys_footprint > 0:
                    return int(info.phys_footprint)
        except OSError, AttributeError, ctypes.ArgumentError:
            pass

    if resource is not None:
        try:
            value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return value if value > 0 else None
        except AttributeError, OSError, ValueError:
            return None
    return None


def process_peak_rss_bytes() -> int | None:
    """Return the current process high-water RSS in bytes using OS peak counters."""

    if os.name == "nt":
        try:
            return _windows_peak_rss_bytes()
        except OSError, AttributeError, ctypes.ArgumentError:
            return None

    if sys.platform.startswith("linux"):
        return _linux_peak_rss_bytes()

    if sys.platform == "darwin":
        return _darwin_peak_rss_bytes()

    return None


def rss_bytes(pid: int) -> int | None:
    """Return kernel high-water or platform RSS for a process in bytes."""

    if os.name == "nt":
        try:
            return _windows_peak_rss_bytes(pid)
        except OSError, AttributeError, ctypes.ArgumentError:
            return None

    if sys.platform.startswith("linux"):
        return _linux_peak_rss_bytes(pid)

    if sys.platform == "darwin":
        if pid == os.getpid():
            return _darwin_peak_rss_bytes()
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
        except OSError, subprocess.SubprocessError, ValueError:
            return None

    return None


def _retry_bounded_backoff[T](
    action: Callable[[], T],
    *,
    retry_exceptions: tuple[type[BaseException], ...],
    max_attempts: int = 14,
    initial_delay_s: float = 0.005,
    max_delay_s: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(max_attempts):
        try:
            return action()
        except retry_exceptions:
            if attempt >= max_attempts - 1:
                raise
            delay = min(max_delay_s, initial_delay_s * (2**attempt))
            sleep_fn(delay)
    raise RuntimeError("unreachable retry loop")


def _read_done_json_with_retry(
    done_path: Path,
    *,
    max_attempts: int = 14,
    initial_delay_s: float = 0.005,
    max_delay_s: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    def read_once() -> dict[str, Any]:
        payload = json.loads(done_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("done marker must decode to JSON object", "", 0)
        return payload

    return _retry_bounded_backoff(
        read_once,
        retry_exceptions=(OSError, json.JSONDecodeError),
        max_attempts=max_attempts,
        initial_delay_s=initial_delay_s,
        max_delay_s=max_delay_s,
        sleep_fn=sleep_fn,
    )


def _unlink_with_retry(
    target: Path,
    *,
    max_attempts: int = 14,
    initial_delay_s: float = 0.005,
    max_delay_s: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    def unlink_once() -> None:
        try:
            target.unlink()
        except FileNotFoundError:
            return

    _retry_bounded_backoff(
        unlink_once,
        retry_exceptions=(PermissionError, OSError),
        max_attempts=max_attempts,
        initial_delay_s=initial_delay_s,
        max_delay_s=max_delay_s,
        sleep_fn=sleep_fn,
    )


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
    baseline_peak_rss = process_peak_rss_bytes()
    print(f"{WORKER_READY} {os.getpid()}", flush=True)
    sys.stdin.readline()
    started = time.perf_counter_ns()
    error: str | None = None
    try:
        operation(path, payload_len, file_chunk_bytes)
    except Exception as exc:  # report the failure to the parent as evidence
        error = f"{type(exc).__name__}: {exc}"
    producer_elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    operation_peak_rss = process_peak_rss_bytes()
    # Publish the completion marker atomically.  The parent polls for the
    # marker while the worker is still alive; writing directly to the final
    # path would let it observe a partially written JSON document.
    done_tmp_path = done_path.with_name(f"{done_path.name}.tmp")
    done_tmp_path.write_text(
        json.dumps(
            {
                "elapsed_ms": producer_elapsed_ms,
                "error": error,
                "baseline_peak_rss_bytes": baseline_peak_rss,
                "operation_peak_rss_bytes": operation_peak_rss,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _retry_bounded_backoff(
        lambda: os.replace(done_tmp_path, done_path),
        retry_exceptions=(PermissionError, OSError),
    )
    # Wait for parent acknowledgement before validation maps and hashes the
    # file so validation stays separated from producer-memory evidence.
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


def _as_int(value: object) -> int | None:
    return value if type(value) is int else None


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
    except IndexError, ValueError:
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
    source_path = path.with_suffix(".source.bin")
    start_rss: int | None = None
    peak_rss: int | None = None
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
            exit_code = process.poll()
            if exit_code is not None:
                raise RuntimeError(f"worker exited with code {exit_code} before completion marker")
            raise TimeoutError("worker did not finish producer phase within 180 seconds")
        done = _read_done_json_with_retry(done_path)
        start_rss = _as_int(done.get("baseline_peak_rss_bytes"))
        peak_rss = _as_int(done.get("operation_peak_rss_bytes"))
        if done.get("error") is not None:
            error = str(done["error"])
        process.stdin.write("validate\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=180)
    except (TimeoutError, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        _terminate(process)
        with suppress(OSError, subprocess.TimeoutExpired):
            stdout, stderr = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            _terminate(process)
        for target in (path, source_path, done_path):
            with suppress(OSError):
                _unlink_with_retry(target)

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

    if start_rss is None or peak_rss is None:
        error = error or "platform RSS observation unavailable"
        peak_rss_delta = None
    else:
        peak_rss_delta = max(0, peak_rss - start_rss)
    return {
        "repetition": repetition,
        "sequence_position": sequence_position,
        "worker_pid": worker_pid,
        "elapsed_ms": elapsed,
        "start_rss_bytes": start_rss,
        "peak_rss_bytes": peak_rss,
        "peak_rss_delta_bytes": peak_rss_delta,
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
    with managed_scratch_dir("benchmarks", prefix="aegis-mmap-buffer-") as directory:
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


def _rss_reduction_percent(candidate_rss: int | None, baseline_rss: int | None) -> float | None:
    if type(baseline_rss) is not int or type(candidate_rss) is not int or baseline_rss < 0 or candidate_rss < 0:
        return None
    if baseline_rss == 0:
        return 0.0 if candidate_rss == 0 else None
    return (1.0 - float(candidate_rss) / float(baseline_rss)) * 100.0


def _relative_delta_percent(candidate_rss: int | None, reference_rss: int | None) -> float | None:
    if type(reference_rss) is not int or type(candidate_rss) is not int or reference_rss < 0 or candidate_rss < 0:
        return None
    if reference_rss == 0:
        return 0.0 if candidate_rss == 0 else None
    return (float(candidate_rss) / float(reference_rss) - 1.0) * 100.0


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
        comparison[f"{label}_rss_reduction_percent"] = _rss_reduction_percent(
            rss if type(rss) is int else None,
            baseline_rss if type(baseline_rss) is int else None,
        )
        comparison[f"{label}_elapsed_delta_percent"] = (
            (float(elapsed) / float(baseline_elapsed) - 1.0) * 100.0
            if isinstance(baseline_elapsed, (int, float))
            and not isinstance(baseline_elapsed, bool)
            and isinstance(elapsed, (int, float))
            and not isinstance(elapsed, bool)
            and baseline_elapsed > 0
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
        and not isinstance(python_file_elapsed, bool)
        and isinstance(native_file_elapsed, (int, float))
        and not isinstance(native_file_elapsed, bool)
        and python_file_elapsed > 0
        else None
    )
    comparison["file_stream_native_vs_python_rss_delta_percent"] = _relative_delta_percent(
        native_file_rss if type(native_file_rss) is int else None,
        python_file_rss if type(python_file_rss) is int else None,
    )
    return comparison


def _measurement_is_valid(measurement: dict[str, Any]) -> bool:
    results = measurement["results"]
    baseline = results[0]
    comparison = measurement["comparison"]
    baseline_rss = baseline["peak_rss_delta_bytes_max"]
    if type(baseline_rss) is not int or baseline_rss < 0:
        return False
    elapsed_keys = (
        "direct_view_elapsed_delta_percent",
        "chunked_bytes_elapsed_delta_percent",
        "reusable_buffer_elapsed_delta_percent",
        "file_stream_native_elapsed_delta_percent",
        "file_stream_python_elapsed_delta_percent",
        "file_stream_native_vs_python_elapsed_delta_percent",
    )
    if not all(
        isinstance(comparison.get(key), (int, float)) and not isinstance(comparison.get(key), bool)
        for key in elapsed_keys
    ):
        return False
    for label, item in (
        ("direct_view", results[1]),
        ("chunked_bytes", results[2]),
        ("reusable_buffer", results[3]),
        ("file_stream_python", results[4]),
        ("file_stream_native", results[5]),
    ):
        rss = item["peak_rss_delta_bytes_max"]
        if type(rss) is not int or rss < 0:
            return False
        metric = comparison.get(f"{label}_rss_reduction_percent")
        if baseline_rss > 0 and (not isinstance(metric, (int, float)) or isinstance(metric, bool)):
            return False
        if baseline_rss == 0 and rss == 0 and (not isinstance(metric, (int, float)) or isinstance(metric, bool)):
            return False
    python_file_rss = results[4]["peak_rss_delta_bytes_max"]
    native_file_rss = results[5]["peak_rss_delta_bytes_max"]
    file_delta = comparison.get("file_stream_native_vs_python_rss_delta_percent")
    if python_file_rss > 0 and (not isinstance(file_delta, (int, float)) or isinstance(file_delta, bool)):
        return False
    return not (
        python_file_rss == 0
        and native_file_rss == 0
        and (not isinstance(file_delta, (int, float)) or isinstance(file_delta, bool))
    )


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
        valid = valid and _measurement_is_valid(measurement)
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
