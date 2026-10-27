from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aegis_cognition.lab import ProcessExecutionCell, _WindowsCellJob


def is_pid_alive_kernel32(pid: int) -> bool:
    """Check if process is alive using Win32 OpenProcess and GetExitCodeProcess."""
    if pid <= 0:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


def is_pid_alive_tasklist(pid: int) -> bool:
    """Check if process is alive using OS command tasklist."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        output = result.stdout.strip()
        # If process is not found, output starts with "INFO: No tasks are running"
        if not output or "No tasks are running" in output:
            return False
        # If found, CSV output contains the PID
        return str(pid) in output
    except Exception:
        return False


def is_pid_alive(pid: int) -> bool:
    return is_pid_alive_kernel32(pid) or is_pid_alive_tasklist(pid)


# Top-level functions for pickling under multiprocessing spawn on Windows:
def _worker_with_grandchild(pid_file_str: str, creation_flags: int = 0) -> None:
    """Worker that launches a detached long-lived grandchild and sleeps."""
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    proc = subprocess.Popen(cmd, creationflags=creation_flags)
    child_pid = os.getpid()
    grandchild_pid = proc.pid
    with open(pid_file_str, "w", encoding="utf-8") as f:
        f.write(f"{child_pid},{grandchild_pid}\n")
        f.flush()
    # Hang to force timeout or cancellation
    time.sleep(60)


def _worker_with_ping_grandchild(pid_file_str: str) -> None:
    """Worker that launches cmd /c ping 127.0.0.1 -n 45."""
    cmd = ["cmd.exe", "/c", "ping", "127.0.0.1", "-n", "45"]
    proc = subprocess.Popen(cmd)
    child_pid = os.getpid()
    grandchild_pid = proc.pid
    with open(pid_file_str, "w", encoding="utf-8") as f:
        f.write(f"{child_pid},{grandchild_pid}\n")
        f.flush()
    time.sleep(60)


def wait_for_pids(pid_file: Path, timeout: float = 10.0) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists():
            content = pid_file.read_text(encoding="utf-8").strip()
            if "," in content:
                parts = content.split(",")
                return int(parts[0]), int(parts[1])
        time.sleep(0.05)
    raise TimeoutError(f"Worker did not write pids to {pid_file} within {timeout}s")


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment test")
def test_adversarial_timeout_kills_child_and_grandchild(tmp_path: Path) -> None:
    """Test that ProcessExecutionCell timeout terminates both worker and its grandchild."""
    pid_file = tmp_path / "pids_timeout.txt"
    cell = ProcessExecutionCell(_worker_with_grandchild, timeout_seconds=6.0)

    with pytest.raises(TimeoutError):
        asyncio.run(cell(str(pid_file), 0))

    child_pid, grandchild_pid = wait_for_pids(pid_file)
    assert child_pid > 0 and grandchild_pid > 0

    # Allow brief window for OS kernel cleanup
    time.sleep(0.5)

    assert not is_pid_alive_kernel32(child_pid), f"Child worker {child_pid} is still alive!"
    assert not is_pid_alive_tasklist(child_pid), f"Child worker {child_pid} found in tasklist!"

    assert not is_pid_alive_kernel32(grandchild_pid), f"Grandchild {grandchild_pid} is still alive!"
    assert not is_pid_alive_tasklist(grandchild_pid), f"Grandchild {grandchild_pid} found in tasklist!"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment test")
def test_adversarial_cancellation_kills_child_and_grandchild(tmp_path: Path) -> None:
    """Test that asyncio task cancellation terminates both worker and its grandchild."""
    pid_file = tmp_path / "pids_cancel.txt"

    async def run_and_cancel() -> None:
        cell = ProcessExecutionCell(_worker_with_grandchild, timeout_seconds=30.0)
        task = asyncio.create_task(cell(str(pid_file), 0))
        # Wait until worker has started and spawned grandchild
        await asyncio.to_thread(wait_for_pids, pid_file)
        # Cancel cell execution
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())

    child_pid, grandchild_pid = wait_for_pids(pid_file)
    time.sleep(0.5)

    assert not is_pid_alive(child_pid), f"Child worker {child_pid} survived cancellation!"
    assert not is_pid_alive(grandchild_pid), f"Grandchild {grandchild_pid} survived cancellation!"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment test")
def test_adversarial_ping_grandchild_containment(tmp_path: Path) -> None:
    """Test that cmd/ping grandchild process tree is terminated on cell timeout."""
    pid_file = tmp_path / "pids_ping.txt"
    cell = ProcessExecutionCell(_worker_with_ping_grandchild, timeout_seconds=6.0)

    with pytest.raises(TimeoutError):
        asyncio.run(cell(str(pid_file)))

    child_pid, grandchild_pid = wait_for_pids(pid_file)
    time.sleep(0.5)

    assert not is_pid_alive(child_pid), f"Child worker {child_pid} is still alive!"
    assert not is_pid_alive(grandchild_pid), f"Ping grandchild {grandchild_pid} is still alive!"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment test")
def test_job_object_cleanup_without_taskkill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify Job Object handle close terminates process tree even when taskkill is disabled."""
    # Monkeypatch taskkill so shutil.which returns None
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    pid_file = tmp_path / "pids_no_taskkill.txt"
    # DETACHED_PROCESS = 0x00000008, CREATE_NEW_PROCESS_GROUP = 0x00000200
    detached_flags = 0x00000008 | 0x00000200
    cell = ProcessExecutionCell(_worker_with_grandchild, timeout_seconds=6.0)

    with pytest.raises(TimeoutError):
        asyncio.run(cell(str(pid_file), detached_flags))

    child_pid, grandchild_pid = wait_for_pids(pid_file)
    time.sleep(0.5)

    # Both child and detached grandchild must be dead purely via Job Object termination
    assert not is_pid_alive(child_pid), f"Child worker {child_pid} survived without taskkill!"
    assert not is_pid_alive(grandchild_pid), f"Grandchild {grandchild_pid} survived without taskkill!"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment test")
def test_job_object_close_handle_direct_guarantee() -> None:
    """Directly test that closing a _WindowsCellJob handle terminates attached child process trees."""
    job = _WindowsCellJob()
    # Spawn a sleeping python process
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    proc = subprocess.Popen(cmd)
    try:
        assigned = job.assign_process(proc.pid)
        assert assigned is True, "Failed to assign process to Job Object"
        assert is_pid_alive(proc.pid), "Process died before test"

        # Now close the job object handle directly without calling terminate() or taskkill
        closed = job.close()
        assert closed is True
        time.sleep(0.5)

        # Because JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE was set, Windows kernel MUST have terminated it
        assert not is_pid_alive(proc.pid), f"Process {proc.pid} was not terminated by Job Object close!"
    finally:
        # Failsafe cleanup if test failed
        if is_pid_alive(proc.pid):
            proc.kill()
            proc.wait()
