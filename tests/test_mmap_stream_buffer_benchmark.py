"""Unit tests for mmap stream buffer benchmark RSS counters, retries, and 0-delta handling."""

from __future__ import annotations

import ctypes
import json
import sys
import types
from pathlib import Path
from typing import ClassVar

import pytest

from scripts import mmap_stream_buffer_benchmark as bench


def _fake_results(
    *,
    baseline_rss: int | None = 16 * 1024 * 1024,
    python_file_rss: int | None = 1024 * 1024,
    native_file_rss: int | None = 512 * 1024,
    rss_observed: bool = True,
) -> list[dict[str, object]]:
    rss_map = {
        "full_buffer_native": baseline_rss,
        "direct_view_native": 1024 * 1024,
        "chunked_bytes_native": 1024 * 1024,
        "reusable_buffer_native": 1024 * 1024,
        "file_stream_python": python_file_rss,
        "file_stream_native": native_file_rss,
    }
    return [
        {
            "name": mode,
            "payload_bytes": 16 * 1024 * 1024,
            "repetitions": 1,
            "all_completed": True,
            "all_files_have_expected_size": True,
            "all_frames_validated": True,
            "rss_observed_for_all_rows": rss_observed,
            "elapsed_ms_median": 10.0,
            "peak_rss_delta_bytes_max": rss_map[mode],
            "rows": [],
        }
        for mode in bench.MODES
    ]


def test_windows_peak_rss_uses_peak_working_set_size_not_current_wss(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKernel32:
        class OpenProcess:
            argtypes: ClassVar[list[object]] = []
            restype: ClassVar[object] = None

            def __new__(cls, *_args: object) -> int:
                return 0x1234

        class CloseHandle:
            argtypes: ClassVar[list[object]] = []
            restype: ClassVar[object] = None

            def __new__(cls, _handle: object) -> int:
                return 1

    class FakePsapi:
        class GetProcessMemoryInfo:
            argtypes: ClassVar[list[object]] = []
            restype: ClassVar[object] = None

            def __new__(cls, _handle: object, counters_ptr: object, _cb: int) -> int:
                # Write directly into the ctypes MemoryCounters buffer passed byref.
                raw_ptr = ctypes.cast(counters_ptr, ctypes.POINTER(ctypes.c_size_t))
                # Layout: cb(DWORD)+page_fault_count(DWORD) = 8 bytes (1 c_size_t on 64-bit),
                # then peak_working_set_size, then working_set_size.
                if ctypes.sizeof(ctypes.c_size_t) == 8:
                    raw_ptr[1] = 64 * 1024 * 1024
                    raw_ptr[2] = 4 * 1024
                else:
                    raw_ptr[2] = 64 * 1024 * 1024
                    raw_ptr[3] = 4 * 1024
                return 1

    def fake_windll(name: str, **_kwargs: object) -> object:
        return FakeKernel32 if name == "kernel32" else FakePsapi

    fake_wintypes = types.ModuleType("ctypes.wintypes")
    monkeypatch.setattr(fake_wintypes, "DWORD", ctypes.c_uint32, raising=False)
    monkeypatch.setattr(fake_wintypes, "BOOL", ctypes.c_int, raising=False)
    monkeypatch.setattr(fake_wintypes, "HANDLE", ctypes.c_void_p, raising=False)

    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)
    monkeypatch.setattr(ctypes, "wintypes", fake_wintypes, raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    monkeypatch.setattr(bench.os, "name", "nt")

    assert bench._windows_peak_rss_bytes(4242) == 64 * 1024 * 1024
    assert bench._windows_rss_bytes(4242) == 64 * 1024 * 1024
    assert bench.process_peak_rss_bytes() == 64 * 1024 * 1024


def test_parse_linux_vmhwm_bytes_handles_kb_and_malformed_lines() -> None:
    status_sample = "Name:\tpython\nVmHWM:\t   49152 kB\nVmRSS:\t   1024 kB\n"
    assert bench._parse_linux_vmhwm_bytes(status_sample) == 49152 * 1024
    assert bench._parse_linux_vmhwm_bytes("Name:\tpython\nVmRSS:\t1024 kB\n") is None
    assert bench._parse_linux_vmhwm_bytes("VmHWM:\tnotanint kB\n") is None


def test_read_done_json_with_retry_recovers_after_transient_ntfs_and_json_errors(tmp_path: Path) -> None:
    done_path = tmp_path / "worker.done.json"
    done_path.write_text(json.dumps({"elapsed_ms": 1.5, "error": None}), encoding="utf-8")

    original_read_text = Path.read_text
    attempts = 0
    delays: list[float] = []

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        nonlocal attempts
        if self == done_path:
            attempts += 1
            if attempts == 1:
                raise PermissionError(32, "sharing violation")
            if attempts == 2:
                return "{partial"
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(Path, "read_text", flaky_read_text)
        decoded = bench._read_done_json_with_retry(
            done_path,
            max_attempts=5,
            initial_delay_s=0.002,
            max_delay_s=0.01,
            sleep_fn=delays.append,
        )
    finally:
        monkeypatch.undo()

    assert decoded == {"elapsed_ms": 1.5, "error": None}
    assert attempts == 3
    assert delays == [0.002, 0.004]


def test_unlink_with_retry_recovers_from_sharing_violation_and_ignores_missing(tmp_path: Path) -> None:
    target = tmp_path / "frame.aegmmap"
    target.write_bytes(b"payload")

    original_unlink = Path.unlink
    attempts = 0
    delays: list[float] = []

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        nonlocal attempts
        if self == target:
            attempts += 1
            if attempts <= 2:
                raise PermissionError(32, "file in use")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        bench._unlink_with_retry(
            target,
            max_attempts=5,
            initial_delay_s=0.002,
            max_delay_s=0.01,
            sleep_fn=delays.append,
        )
    finally:
        monkeypatch.undo()

    assert not target.exists()
    assert attempts == 3
    assert delays == [0.002, 0.004]
    # Missing files succeed without sleeping.
    bench._unlink_with_retry(target, sleep_fn=delays.append)
    assert delays == [0.002, 0.004]


def test_zero_delta_rss_comparison_and_validation_succeed_while_none_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Case 1: both python and native file stream have 0 peak delta -> 0.0% and MEASURED_HOST_ONLY
    monkeypatch.setattr(
        bench,
        "measure_payload",
        lambda *_args: _fake_results(python_file_rss=0, native_file_rss=0),
    )
    both_zero = bench.run_benchmark((16 * 1024 * 1024,), 1, bench.CHUNK_BYTES)
    assert both_zero["status"] == "MEASURED_HOST_ONLY"
    assert both_zero["measurements"][0]["comparison"]["file_stream_native_vs_python_rss_delta_percent"] == 0.0

    # Case 2: python_file_rss == 0 and native_file_rss > 0 -> None delta (undefined 0-denom), still valid
    monkeypatch.setattr(
        bench,
        "measure_payload",
        lambda *_args: _fake_results(python_file_rss=0, native_file_rss=65536),
    )
    py_zero = bench.run_benchmark((16 * 1024 * 1024,), 1, bench.CHUNK_BYTES)
    assert py_zero["status"] == "MEASURED_HOST_ONLY"
    assert py_zero["measurements"][0]["comparison"]["file_stream_native_vs_python_rss_delta_percent"] is None

    # Case 3: RSS observation actually failed (None) -> must fail closed!
    monkeypatch.setattr(
        bench,
        "measure_payload",
        lambda *_args: _fake_results(python_file_rss=None, rss_observed=False),
    )
    missing_obs = bench.run_benchmark((16 * 1024 * 1024,), 1, bench.CHUNK_BYTES)
    assert missing_obs["status"] == "FAILED"
