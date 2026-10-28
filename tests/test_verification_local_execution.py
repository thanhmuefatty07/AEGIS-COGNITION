from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
from multiprocessing.connection import wait as wait_processes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import aegis_cognition.lab as lab_module
from aegis_cognition.application import AgentApplication
from aegis_cognition.config import AgentConfig
from aegis_cognition.lab import ProcessExecutionCell
from aegis_cognition.verification import (
    LocalVerificationCommand,
    VerificationFacade,
    VerificationSessionError,
    run_local_verification_command,
)
from aegis_cognition.verification.discovery import inspect_project
from aegis_cognition.verification import execution as execution_module


def _session(tmp_path: Path) -> tuple[VerificationFacade, str, str]:
    facade = VerificationFacade()
    profile = inspect_project(tmp_path)
    requirements = facade.create_contract(
        "implement the bounded verification lane",
        expected_behavior="the lane records a structured receipt",
        source_revision=profile.source_revision,
        policy_hash="test-policy",
    )
    session = facade.start_session(profile, requirements)
    run = facade.request_deep_run(session.session_id)
    return facade, session.session_id, str(run["run_id"])


def _result(source_revision: str, run_id: str) -> dict[str, object]:
    return {
        "source_revision": source_revision,
        "command_id": "test-command",
        "adapter": "custom",
        "framework": "fixture",
        "toolchain": "test",
        "platform": "test",
        "executable": sys.executable,
        "argv": ("-c", "print('1 passed')"),
        "working_directory": str(Path.cwd()),
        "environment_fingerprint": {},
        "timeout_seconds": 5.0,
        "cancellation_requested": False,
        "exit_code": 0,
        "exit_semantics": "PROCESS_EXIT",
        "status": "PASS",
        "discovered": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "filtered": 0,
        "ignored": 0,
        "stdout_hash": "a" * 64,
        "stderr_hash": "b" * 64,
        "stdout_size": 12,
        "stderr_size": 0,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
    }


def test_local_command_rejects_missing_executable_and_shell_payload(tmp_path: Path) -> None:
    command = LocalVerificationCommand(
        command_id="fixture",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", "import sys; print('1 passed'); print(sys.argv[1])", "&& whoami"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    command.validate()
    result = asyncio.run(ProcessExecutionCell(run_local_verification_command, timeout_seconds=60.0)(command.as_dict()))
    assert result["status"] == "ERROR"
    assert result["failure_class"] == "UNSUPPORTED_RESULT_FORMAT"
    assert result["discovered"] == 0
    assert result["passed"] == 0
    assert result["detail"] == "&& whoami"

    invalid = LocalVerificationCommand(
        command_id="invalid",
        adapter="custom",
        framework="fixture",
        executable=str(tmp_path / "missing-executable"),
        argv=("-c", "print('1 passed')"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    with pytest.raises(ValueError, match="executable"):
        invalid.validate()


def test_standalone_local_verification_runs_in_its_own_session(tmp_path: Path) -> None:
    probe_expr = (
        "import os; print('1 passed', os.getpid(), os.getpid(), os.getpid())"
        if os.name == "nt"
        else "import os; print('1 passed', os.getpid(), os.getpgrp(), os.getsid(0))"
    )
    command = LocalVerificationCommand(
        command_id="standalone-session",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", probe_expr),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )

    result = run_local_verification_command(command.as_dict())

    output = str(result["detail"]).split()
    assert output[:2] == ["1", "passed"]
    _, _, pid, group_id, session_id = output
    assert pid == group_id == session_id
    assert int(pid) != os.getpid()


def test_tool_resolution_uses_rustup_which_paths_instead_of_path_shims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rustup = tmp_path / "rustup.exe"
    shim = tmp_path / "cargo.exe"
    real_cargo = tmp_path / "toolchain" / "cargo.exe"
    real_rustc = tmp_path / "toolchain" / "rustc.exe"
    rustup.write_bytes(b"rustup")
    shim.write_bytes(b"shim")
    real_cargo.parent.mkdir()
    real_cargo.write_bytes(b"cargo")
    real_rustc.write_bytes(b"rustc")

    discovered = {"cargo": str(shim), "rustc": str(shim), "rustup": str(rustup)}
    monkeypatch.setattr(execution_module.shutil, "which", discovered.get)

    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        command = args[0]
        tool = command[2]
        toolchain_path = {"cargo": real_cargo, "rustc": real_rustc}[tool]
        return type("Completed", (), {"stdout": str(toolchain_path)})()

    monkeypatch.setattr(execution_module.subprocess, "run", fake_run)

    assert execution_module._resolve_cargo_executable(cwd=tmp_path) == str(real_cargo)
    assert execution_module._resolve_rustup_tool("rustc", cwd=tmp_path) == str(real_rustc)
    assert [call[0][0] for call in calls] == [
        (str(rustup), "which", "cargo"),
        (str(rustup), "which", "rustc"),
    ]
    assert all(call[1]["cwd"] == tmp_path for call in calls)
    assert all(call[1]["shell"] is False for call in calls)


def test_process_cell_does_not_trust_a_custom_stdout_summary(tmp_path: Path) -> None:
    command = LocalVerificationCommand(
        command_id="process-cell",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", "print('1 passed')"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    result = asyncio.run(ProcessExecutionCell(run_local_verification_command, timeout_seconds=10.0)(command.as_dict()))
    assert result["status"] == "ERROR"
    assert result["failure_class"] == "UNSUPPORTED_RESULT_FORMAT"
    assert result["passed"] == 0
    assert result["resource_class"] == "LOCAL_PROCESS_CELL_BOUNDED_WALL_TIME"


def test_process_cell_deadline_expires_during_worker_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner_marker = tmp_path / "runner-started.txt"
    command = LocalVerificationCommand(
        command_id="process-cell-startup-deadline",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=(
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started', encoding='ascii')",
            str(runner_marker),
        ),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    command.validate()

    clock_offset = [0.0]
    original_monotonic = time.monotonic
    context = multiprocessing.get_context("spawn")
    original_start = context.Process.start

    def elapsed_worker_start(process: Any) -> None:
        original_start(process)
        clock_offset[0] = 20.0

    monkeypatch.setattr(
        lab_module,
        "time",
        SimpleNamespace(monotonic=lambda: original_monotonic() + clock_offset[0], sleep=time.sleep),
    )
    monkeypatch.setattr(context.Process, "start", elapsed_worker_start)
    cell = ProcessExecutionCell(run_local_verification_command, timeout_seconds=10.0)

    with pytest.raises(TimeoutError, match="process execution cell timed out"):
        asyncio.run(cell(command.as_dict()))

    assert not runner_marker.exists(), "runner executed after the process-cell deadline had elapsed"


def _observed_popen_verification_runner(
    payload: object,
    launch_marker_path: str,
    *,
    popen_blocked_marker_path: str | None = None,
    **runner_options: Any,
) -> dict[str, object]:
    original_popen = execution_module.subprocess.Popen

    def observed_popen(*args: Any, **kwargs: Any) -> Any:
        process = original_popen(*args, **kwargs)
        marker = Path(launch_marker_path)
        temporary_marker = marker.with_suffix(".tmp")
        try:
            temporary_marker.write_text(str(process.pid), encoding="ascii")
            temporary_marker.replace(marker)
        except BaseException:
            process.kill()
            process.wait()
            raise
        if popen_blocked_marker_path is not None:
            blocked_marker = Path(popen_blocked_marker_path)
            temporary_blocked_marker = blocked_marker.with_suffix(".tmp")
            temporary_blocked_marker.write_text(str(process.pid), encoding="ascii")
            temporary_blocked_marker.replace(blocked_marker)
            release_marker = blocked_marker.with_suffix(".release")
            blocked_deadline = time.monotonic() + 10.0
            while time.monotonic() < blocked_deadline and not release_marker.exists():
                time.sleep(0.01)
            if not release_marker.exists():
                raise TimeoutError("test did not release the blocked Popen wrapper")
        return process

    execution_module.subprocess.Popen = observed_popen
    try:
        return run_local_verification_command(payload, **runner_options)
    finally:
        execution_module.subprocess.Popen = original_popen


def test_process_cell_does_not_signal_reaped_process_group_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale child PID must not be used to signal a recycled process group."""

    pid = 43129
    group_signals: list[tuple[int, int]] = []
    direct_signals: list[str] = []
    waitid_calls = 0

    def waitid(*_args: int) -> None:
        nonlocal waitid_calls
        waitid_calls += 1
        raise ChildProcessError("the child was already reaped")

    fake_os = SimpleNamespace(
        name="posix",
        P_PID=1,
        WEXITED=4,
        WNOHANG=1,
        WNOWAIT=8,
        getpgid=lambda target_pid: target_pid,
        getsid=lambda target_pid: target_pid,
        killpg=lambda group_id, signum: group_signals.append((group_id, signum)),
        waitid=waitid,
    )
    fake_signal = SimpleNamespace(SIGTERM=15, SIGKILL=9)

    class ReapedProcess:
        def __init__(self) -> None:
            self.pid = pid

        def is_alive(self) -> bool:
            return False

        def terminate(self) -> None:
            direct_signals.append("terminate")

        def kill(self) -> None:
            direct_signals.append("kill")

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(lab_module, "os", fake_os)
    monkeypatch.setattr(lab_module, "signal", fake_signal)

    ProcessExecutionCell._terminate_process(ReapedProcess())

    assert waitid_calls == 1
    assert group_signals == [], "cleanup signaled a numeric process-group ID without a waitable child"
    assert direct_signals == [], "cleanup signaled a numeric process ID after the child was reaped"


def test_process_cell_stops_group_signals_after_waitability_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second group signal requires the child PID to remain waitable."""

    pid = 43129
    group_signals: list[tuple[int, int]] = []
    direct_signals: list[str] = []
    waitid_results = [
        SimpleNamespace(si_pid=0),
        ChildProcessError("reaped during termination wait"),
        ChildProcessError("reaped during cleanup"),
    ]

    def waitid(*_args: int) -> object:
        result = waitid_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    fake_os = SimpleNamespace(
        name="posix",
        P_PID=1,
        WEXITED=4,
        WNOHANG=1,
        WNOWAIT=8,
        getpgid=lambda target_pid: target_pid,
        getsid=lambda target_pid: target_pid,
        killpg=lambda group_id, signum: group_signals.append((group_id, signum)),
        waitid=waitid,
    )
    fake_signal = SimpleNamespace(SIGTERM=15, SIGKILL=9)

    class ProcessReapedDuringCleanup:
        def __init__(self) -> None:
            self.pid = pid
            self.sentinel = 1

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            direct_signals.append("terminate")

        def kill(self) -> None:
            direct_signals.append("kill")

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(lab_module, "os", fake_os)
    monkeypatch.setattr(lab_module, "signal", fake_signal)

    ProcessExecutionCell._terminate_process(ProcessReapedDuringCleanup())

    assert waitid_results == []
    assert group_signals == [(pid, fake_signal.SIGTERM)]
    assert direct_signals == []


def test_process_execution_cell_rejects_forkserver_cleanup_method() -> None:
    with pytest.raises(ValueError, match=r"forkserver.*cleanup"):
        ProcessExecutionCell(_observed_popen_verification_runner, start_method="forkserver")


@pytest.mark.parametrize(
    "termination",
    (
        "task-cancel",
        "cell-timeout",
        "command-timeout",
        "popen-cancel",
        "competing-process-start",
        "competing-process-start-no-waitid",
        "competing-process-join",
    ),
)
def test_process_cell_cleans_up_verification_tree_on_termination(
    tmp_path: Path,
    termination: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_file = tmp_path / "verification-processes.txt"
    launch_marker = tmp_path / "verification-launch.txt"
    popen_blocked_marker = tmp_path / "verification-popen-blocked.txt"
    execution_started_marker = tmp_path / "verification-started.txt"
    grandchild_ready_marker = tmp_path / "verification-grandchild-ready.txt"
    grandchild_code = "\n".join(
        (
            "import os, signal, sys, time",
            "from pathlib import Path",
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            "ready = Path(sys.argv[1])",
            "ready_tmp = Path(sys.argv[1] + '.tmp')",
            "ready_tmp.write_text(str(os.getpid()), encoding='ascii')",
            "ready_tmp.replace(ready)",
            "time.sleep(60)",
        )
    )
    child_code = "\n".join(
        (
            "import os, subprocess, sys, time",
            "from pathlib import Path",
            "pid_path, started_path, ready_path = sys.argv[1:4]",
            "started = Path(started_path)",
            "started_tmp = Path(started_path + '.tmp')",
            "started_tmp.write_text(str(os.getpid()), encoding='ascii')",
            "started_tmp.replace(started)",
            f"grandchild = subprocess.Popen([sys.executable, '-c', {grandchild_code!r}, ready_path])",
            "ready_deadline = time.monotonic() + 5.0",
            "while time.monotonic() < ready_deadline and not Path(ready_path).is_file():",
            "    time.sleep(0.01)",
            "if not Path(ready_path).is_file():",
            "    raise SystemExit('grandchild did not report signal readiness')",
            "temporary_pid_path = Path(pid_path + '.tmp')",
            "temporary_pid_path.write_text(f'{os.getpid()} {grandchild.pid}', encoding='ascii')",
            "temporary_pid_path.replace(pid_path)",
            "print('1 passed', flush=True)",
            "time.sleep(60)",
        )
    )
    command = LocalVerificationCommand(
        command_id=f"process-cell-{termination}",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", child_code, str(pid_file), str(execution_started_marker), str(grandchild_ready_marker)),
        working_directory=str(tmp_path),
        timeout_seconds=1.5 if termination == "command-timeout" else 60.0,
        source_revision="fixture-revision",
    )
    command.validate()
    cell_timeout = 2.0 if termination == "cell-timeout" else 15.0
    cell = ProcessExecutionCell(_observed_popen_verification_runner, timeout_seconds=cell_timeout)
    if termination == "competing-process-start-no-waitid":
        monkeypatch.setattr(lab_module.os, "waitid", None, raising=False)
    if os.name != "nt" and termination in (
        "competing-process-start",
        "competing-process-start-no-waitid",
        "competing-process-join",
    ):
        original_wait = lab_module._wait_for_process_exit_without_reaping

        def wait_after_competing_process_activity(process: Any, timeout: float) -> None:
            worker = cell._active_process
            assert worker is not None and worker is process
            assert wait_processes((worker.sentinel,), timeout=5.0), "worker did not exit after group SIGTERM"
            if termination == "competing-process-join":
                worker.join(timeout=5.0)
            else:
                probe = multiprocessing.get_context("spawn").Process(target=time.sleep, args=(0.01,))
                probe.start()
                probe.join(timeout=5.0)
                assert probe.exitcode == 0, "competing process did not complete its bounded cleanup trigger"
            original_wait(process, timeout)

        monkeypatch.setattr(lab_module, "_wait_for_process_exit_without_reaping", wait_after_competing_process_activity)
    process_ids: tuple[int, ...] = ()
    process_start_times: tuple[str, ...] = ()
    launch_pid: int | None = None
    launch_start_time: str | None = None
    worker_pid: int | None = None
    worker_start_time: str | None = None

    def process_record(pid: int) -> tuple[str, int, int, str] | None:
        if Path("/proc").is_dir():
            stat_path = Path("/proc") / str(pid) / "stat"
            try:
                stat = stat_path.read_text(encoding="ascii")
            except FileNotFoundError:
                return None
            fields = stat[stat.rfind(")") + 2 :].split()
            if len(fields) <= 19:
                raise AssertionError(f"incomplete /proc identity for process {pid}")
            return fields[0], int(fields[2]), int(fields[3]), fields[19]
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                exit_code = ctypes.c_ulong(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                    return None
                create_time = ctypes.c_uint64(0)
                exit_time = ctypes.c_uint64(0)
                kernel_time = ctypes.c_uint64(0)
                user_time = ctypes.c_uint64(0)
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(create_time),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                ):
                    return None
                group_leader = worker_pid if worker_pid is not None else pid
                return "R", group_leader, group_leader, str(create_time.value)
            finally:
                kernel32.CloseHandle(handle)
        try:
            pgid = os.getpgid(pid)
            sid = os.getsid(pid)
        except OSError:
            return None
        completed = subprocess.run(
            ["ps", "-o", "state=,lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        line = completed.stdout.strip()
        if completed.returncode != 0 or not line:
            return None
        parts = line.split(None, 1)
        if len(parts) < 2:
            return None
        state = "Z" if parts[0].startswith("Z") else parts[0][0]
        return state, pgid, sid, parts[1]

    def process_identity_exists(pid: int, start_time: str) -> bool:
        record = process_record(pid)
        return record is not None and record[0] != "Z" and record[3] == start_time

    def clean_up_owned_process_group() -> None:
        owned_identities = tuple(zip(process_ids, process_start_times, strict=True))
        if launch_pid is not None and launch_start_time is not None:
            owned_identities += ((launch_pid, launch_start_time),)
        if worker_pid is not None and worker_start_time is not None:
            owned_identities += ((worker_pid, worker_start_time),)
        live_owned: list[int] = []
        for pid, start_time in owned_identities:
            record = process_record(pid)
            if record is None or record[0] == "Z" or record[3] != start_time:
                continue
            live_owned.append(pid)
        if not live_owned:
            return
        if os.name == "nt":
            for pid in live_owned:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2.0,
                    )
            return
        worker_record = process_record(worker_pid) if worker_pid is not None and worker_start_time is not None else None
        isolated_worker_group = (
            worker_record is not None
            and worker_record[0] != "Z"
            and worker_record[3] == worker_start_time
            and worker_record[1:3] == (worker_pid, worker_pid)
        )
        if isolated_worker_group and worker_pid is not None:
            try:
                os.killpg(worker_pid, signal.SIGKILL)
                return
            except OSError:
                pass
        for pid in live_owned:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def capture_owned_processes() -> None:
        nonlocal process_ids
        nonlocal process_start_times
        if not pid_file.is_file():
            return
        values = tuple(int(value) for value in pid_file.read_text(encoding="ascii").split())
        if len(values) != 2:
            raise AssertionError(f"verification command published an invalid PID list: {values}")
        captured_start_times = tuple(process_record(pid) for pid in values)
        if any(record is None for record in captured_start_times):
            raise AssertionError(f"verification process identity disappeared during setup: {values}")
        process_ids = values
        process_start_times = tuple(record[3] for record in captured_start_times if record is not None)

    async def exercise() -> None:
        nonlocal process_ids
        nonlocal process_start_times
        nonlocal launch_pid
        nonlocal launch_start_time
        nonlocal worker_pid
        nonlocal worker_start_time
        task = asyncio.create_task(
            cell(
                command.as_dict(),
                str(launch_marker),
                popen_blocked_marker_path=(str(popen_blocked_marker) if termination == "popen-cancel" else None),
            )
        )
        try:
            setup_deadline = time.monotonic() + 10.0
            while time.monotonic() < setup_deadline:
                worker_pid = cell.active_pid
                if worker_pid is not None:
                    worker_record = process_record(worker_pid)
                    worker_start_time = worker_record[3] if worker_record is not None else None
                if launch_marker.is_file():
                    launch_pid = int(launch_marker.read_text(encoding="ascii"))
                    launch_record = process_record(launch_pid)
                    launch_start_time = launch_record[3] if launch_record is not None else None
                capture_owned_processes()
                if (
                    worker_pid is not None
                    and worker_start_time is not None
                    and launch_pid is not None
                    and launch_start_time is not None
                    and execution_started_marker.is_file()
                    and grandchild_ready_marker.is_file()
                    and len(process_ids) == 2
                    and (termination != "popen-cancel" or popen_blocked_marker.is_file())
                ):
                    break
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            assert worker_pid is not None and worker_start_time is not None, (
                "process-cell worker identity could not be established"
            )
            assert launch_pid is not None and launch_start_time is not None, (
                "verification command launch identity could not be established"
            )
            worker_record = process_record(worker_pid)
            assert worker_record is not None and worker_record[1:3] == (worker_pid, worker_pid), (
                "process-cell worker did not own an isolated session and process group"
            )
            assert execution_started_marker.is_file(), "verification command did not start"
            assert grandchild_ready_marker.is_file(), "SIGTERM-ignoring descendant was not ready"
            capture_owned_processes()
            assert len(process_ids) == 2 and len(process_start_times) == 2, (
                "verification subprocess did not publish both process identities"
            )
            assert all(process_start_times), "verification process start-time identity was empty"
            command_record, grandchild_record = tuple(process_record(pid) for pid in process_ids)
            assert command_record is not None and command_record[1:3] == (worker_pid, worker_pid), (
                "verification command escaped the process-cell worker group or session"
            )
            assert grandchild_record is not None and grandchild_record[1:3] == (worker_pid, worker_pid), (
                "verification descendant escaped the process-cell worker group or session"
            )
            assert execution_started_marker.read_text(encoding="ascii") == str(process_ids[0]), (
                "execution marker did not identify the recorded command process"
            )

            if termination in (
                "task-cancel",
                "competing-process-start",
                "competing-process-start-no-waitid",
                "competing-process-join",
            ):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif termination == "cell-timeout":
                with pytest.raises(TimeoutError, match="process execution cell timed out"):
                    await task
            elif termination == "popen-cancel":
                assert popen_blocked_marker.read_text(encoding="ascii") in {
                    str(process_ids[0]),
                    str(launch_pid),
                }, "Popen wrapper had not started its bounded pre-return wait"
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3.0)
            else:
                result = await task
                assert result["status"] == "TIMEOUT"
                assert result["failure_class"] == "TIMEOUT"
                assert result["cancellation_requested"] is False

            assert cell.active_pid is None
            cleanup_deadline = time.monotonic() + 5.0
            while time.monotonic() < cleanup_deadline:
                if not any(
                    process_identity_exists(pid, start_time)
                    for pid, start_time in zip(process_ids, process_start_times, strict=True)
                ):
                    break
                await asyncio.sleep(0.01)
            remaining = tuple(
                (pid, process_record(pid))
                for pid, start_time in zip(process_ids, process_start_times, strict=True)
                if process_identity_exists(pid, start_time)
            )
            assert not remaining, f"verification process identities remained after {termination}: {remaining}"
        finally:
            try:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await asyncio.wait_for(task, timeout=3.0)
            finally:
                if launch_pid is None and launch_marker.is_file():
                    launch_pid = int(launch_marker.read_text(encoding="ascii"))
                    launch_record = process_record(launch_pid)
                    launch_start_time = launch_record[3] if launch_record is not None else None
                if not process_ids:
                    with contextlib.suppress(AssertionError, OSError, ValueError):
                        capture_owned_processes()
                clean_up_owned_process_group()
                cleanup_deadline = time.monotonic() + 5.0
                while time.monotonic() < cleanup_deadline:
                    remaining = any(
                        process_identity_exists(pid, start_time)
                        for pid, start_time in zip(process_ids, process_start_times, strict=True)
                    )
                    launch_remains = (
                        launch_pid is not None
                        and launch_start_time is not None
                        and process_identity_exists(launch_pid, launch_start_time)
                    )
                    if not remaining and not launch_remains:
                        break
                    clean_up_owned_process_group()
                    await asyncio.sleep(0.01)
                remaining = tuple(
                    pid
                    for pid, start_time in zip(process_ids, process_start_times, strict=True)
                    if process_identity_exists(pid, start_time)
                )
                launch_remains = (
                    launch_pid is not None
                    and launch_start_time is not None
                    and process_identity_exists(launch_pid, launch_start_time)
                )
                assert not remaining and not launch_remains, (
                    f"cleanup could not reap all owned processes after {termination}: {remaining}"
                )

    try:
        asyncio.run(exercise())
    finally:
        pid_file.unlink(missing_ok=True)
        launch_marker.unlink(missing_ok=True)
        popen_blocked_marker.unlink(missing_ok=True)
        execution_started_marker.unlink(missing_ok=True)
        grandchild_ready_marker.unlink(missing_ok=True)


def test_cargo_adapter_rejects_non_cargo_executable_before_launch(tmp_path: Path) -> None:
    if execution_module._resolve_cargo_executable(cwd=tmp_path) is None:
        pytest.skip("the pinned Rust toolchain is unavailable")
    marker = tmp_path / "fake-cargo-was-launched"
    command = LocalVerificationCommand(
        command_id="cargo-fake-executable",
        adapter="rust-cargo",
        framework="cargo",
        executable=sys.executable,
        argv=("-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('launched')"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )

    with pytest.raises(ValueError, match="active toolchain binary"):
        run_local_verification_command(command.as_dict())
    assert not marker.exists()


def test_pytest_junit_report_binds_collection_and_outcomes(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "def test_first():\n    assert 2 == 2\n\n"
        "def test_second():\n    assert 3 == 3\n\n"
        "def test_skipped():\n    import pytest\n    pytest.skip('not in this lane')\n",
        encoding="utf-8",
    )
    command = LocalVerificationCommand(
        command_id="pytest-structured",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "-q", str(test_file)),
        working_directory=str(tmp_path),
        timeout_seconds=30.0,
        source_revision="fixture-revision",
    )

    result = asyncio.run(ProcessExecutionCell(run_local_verification_command, timeout_seconds=60.0)(command.as_dict()))

    assert result["status"] == "PASS"
    assert result["discovered"] == 3
    assert result["passed"] == 2
    assert result["skipped"] == 1
    assert result["collector"] == "pytest-junitxml-xunit2"
    assert len(cast(str, result["collection_report_sha256"])) == 64
    assert cast(int, result["collection_report_size"]) > 0
    test_ids = cast(tuple[str, ...], result["collected_test_ids"])
    assert tuple(test_id.rsplit("::", 1)[1] for test_id in test_ids) == (
        "test_first",
        "test_second",
        "test_skipped",
    )
    assert all("test_sample" in test_id for test_id in test_ids)


def test_python_stdout_summary_without_junit_report_is_not_a_pass(tmp_path: Path) -> None:
    command = LocalVerificationCommand(
        command_id="pytest-fake-summary",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-c", "print('99 passed')"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )

    result = run_local_verification_command(command.as_dict())

    assert result["status"] == "MALFORMED"
    assert result["failure_class"] == "MALFORMED_RESULT"
    assert result["discovered"] == 0
    assert result["passed"] == 0


def test_pytest_with_only_skipped_cases_is_not_a_pass(tmp_path: Path) -> None:
    test_file = tmp_path / "test_skipped.py"
    test_file.write_text(
        "def test_skipped():\n    import pytest\n    pytest.skip('not in this lane')\n",
        encoding="utf-8",
    )
    command = LocalVerificationCommand(
        command_id="pytest-skipped",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "-q", str(test_file)),
        working_directory=str(tmp_path),
        timeout_seconds=30.0,
        source_revision="fixture-revision",
    )

    result = run_local_verification_command(command.as_dict())

    assert result["status"] == "ZERO_TESTS"
    assert result["discovered"] == 1
    assert result["passed"] == 0
    assert result["skipped"] == 1


def test_inherited_pytest_addopts_cannot_hide_a_failing_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_file = tmp_path / "test_runner_input.py"
    test_file.write_text(
        "def test_selected_success():\n    assert True\n\n"
        "def test_filtered_failure():\n    assert False, 'intentional runner regression'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_selected_success")
    command = LocalVerificationCommand(
        command_id="pytest-addopts-untrusted-environment",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "-q", str(test_file)),
        working_directory=str(tmp_path),
        timeout_seconds=30.0,
        source_revision="fixture-revision",
    )

    result = run_local_verification_command(command.as_dict())

    assert result["status"] == "FAIL"
    assert result["discovered"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["filtered"] == 0
    assert result["environment_fingerprint"]["PYTEST_ADDOPTS"] == ""


def test_pytest_project_addopts_cannot_hide_a_failing_case(tmp_path: Path) -> None:
    test_file = tmp_path / "test_project_config.py"
    test_file.write_text(
        "def test_selected_success():\n    assert True\n\n"
        "def test_filtered_failure():\n    assert False, 'intentional project-config regression'\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -k test_selected_success\n",
        encoding="utf-8",
    )
    command = LocalVerificationCommand(
        command_id="pytest-project-config-addopts-untrusted",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "-q", str(test_file)),
        working_directory=str(tmp_path),
        timeout_seconds=30.0,
        source_revision="fixture-revision",
    )

    result = run_local_verification_command(command.as_dict())

    assert result["status"] == "FAIL"
    assert result["discovered"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1


@pytest.mark.parametrize("runner", ("sync", "async"))
def test_verification_output_capture_stops_at_its_byte_limit(tmp_path: Path, runner: str) -> None:
    test_file = tmp_path / "test_noisy.py"
    test_file.write_text(
        "def test_noisy_output():\n    print('x' * (9 * 1024 * 1024))\n",
        encoding="utf-8",
    )
    command = LocalVerificationCommand(
        command_id="pytest-output-limit",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "-q", "-s", str(test_file)),
        working_directory=str(tmp_path),
        timeout_seconds=30.0,
        source_revision="fixture-revision",
    )

    result = (
        run_local_verification_command(command.as_dict())
        if runner == "sync"
        else asyncio.run(execution_module.run_local_verification_command_async(command.as_dict()))
    )

    assert result["status"] == "ERROR"
    assert result["failure_class"] == "OUTPUT_LIMIT"
    assert result["output_truncated"] is True
    assert result["stdout_size"] + result["stderr_size"] <= result["output_limit_bytes"]


def test_cargo_summary_reports_real_pass_and_failure_counts(tmp_path: Path) -> None:
    cargo = execution_module._resolve_cargo_executable(cwd=tmp_path)
    rustc = execution_module._resolve_rustup_tool("rustc", cwd=tmp_path)
    rustdoc = execution_module._resolve_rustup_tool("rustdoc", cwd=tmp_path)
    if cargo is None or rustc is None or rustdoc is None:
        pytest.skip("the pinned Rust toolchain is unavailable")

    source = tmp_path / "src"
    source.mkdir()
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[package]\nname = "aese-cargo-result-fixture"\nversion = "0.1.0"\nedition = "2024"\n\n[workspace]\n',
        encoding="utf-8",
    )
    (source / "lib.rs").write_text(
        "#[test]\nfn passes_alpha() {}\n\n"
        "#[test]\nfn can_fail_on_request() {\n"
        '    assert!(std::env::var_os("AEGIS_WP12_FORCE_CARGO_FAILURE").is_none());\n'
        "}\n\n"
        "#[test]\n#[ignore]\nfn ignored_case() {}\n",
        encoding="utf-8",
    )

    def cargo_command(command_id: str, *, force_failure: bool = False) -> LocalVerificationCommand:
        environment = {
            "CARGO_HOME": str(tmp_path / "cargo-home"),
            "CARGO_TARGET_DIR": str(tmp_path / "cargo-target"),
            "RUSTC": rustc,
            "RUSTDOC": rustdoc,
        }
        if force_failure:
            environment["AEGIS_WP12_FORCE_CARGO_FAILURE"] = "1"
        return LocalVerificationCommand(
            command_id=command_id,
            adapter="rust-cargo",
            framework="cargo",
            executable=cargo,
            argv=("test", "--offline", "--manifest-path", str(manifest)),
            working_directory=str(tmp_path),
            timeout_seconds=90.0,
            source_revision="fixture-revision",
            environment=environment,
        )

    passed = run_local_verification_command(cargo_command("cargo-pass").as_dict())
    assert passed["status"] == "PASS"
    assert passed["discovered"] == 3
    assert passed["passed"] == 2
    assert passed["failed"] == 0
    assert passed["ignored"] == 1
    assert passed["collector"] == "cargo-libtest-summary"
    assert cast(str, passed["toolchain"]).startswith("rustc ")
    assert cast(str, passed["detail"]).startswith("test result: ok.")
    assert len(cast(str, passed["collection_report_sha256"])) == 64
    assert cast(int, passed["collection_report_size"]) > 0

    failed = run_local_verification_command(cargo_command("cargo-failure", force_failure=True).as_dict())
    assert failed["status"] == "FAIL"
    assert failed["failure_class"] == "TEST_FAILURE"
    assert failed["discovered"] == 3
    assert failed["passed"] == 1
    assert failed["failed"] == 1
    assert failed["ignored"] == 1
    assert cast(str, failed["detail"]).startswith("test result: FAILED.")


def test_lab_bound_receipt_is_provisional_even_when_command_passes(tmp_path: Path) -> None:
    facade, session_id, run_id = _session(tmp_path)
    profile = facade.inspect_project(tmp_path)
    receipt = facade.record_execution_result(session_id, run_id, _result(profile.source_revision, run_id))
    assert receipt.status == "PASS"
    report = facade.read_report(session_id)
    assert report["execution"] == "LAB_BOUND"
    assert report["final_assurance"] is False
    assert report["assessment"]["failure_reasons"] == ["evidence_promotion_disabled_shadow_mode"]
    assert len(report["receipts"]) == 1
    assert len(report["artifacts"]) == 2


def test_stale_receipt_is_rejected_and_cannot_become_pass(tmp_path: Path) -> None:
    facade, session_id, run_id = _session(tmp_path)
    with pytest.raises(VerificationSessionError, match="stale"):
        facade.record_execution_result(session_id, run_id, _result("different-revision", run_id))
    report = facade.read_report(session_id)
    assert report["execution"] == "NOT_EXECUTED"
    assert report["final_assurance"] is False


def test_cancelled_aese_failure_receipt_marks_cancellation_requested(tmp_path: Path) -> None:
    facade, session_id, run_id = _session(tmp_path)
    profile = facade.inspect_project(tmp_path)
    command = LocalVerificationCommand(
        command_id="cancelled-receipt",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", "pass"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision=profile.source_revision,
    )
    app = AgentApplication(
        AgentConfig(
            task="record cancellation truthfully",
            llm=None,
            trust_level="DEV",
            browser=False,
            max_steps=1,
            options={},
            config={},
            api_key=None,
        )
    )
    app._verification_facade = facade
    app._verification_session_id = session_id
    app._aese_run_id = run_id

    app._record_aese_runner_failure(command, status="CANCELLED", lab_run=None)

    receipt = facade.read_report(session_id)["receipts"][-1]
    assert receipt["status"] == "CANCELLED"
    assert receipt["cancellation_requested"] is True


def _linux_process_state(pid: int) -> str | None:
    if Path("/proc").is_dir():
        stat_path = Path("/proc") / str(pid) / "stat"
        try:
            raw = stat_path.read_text(encoding="ascii")
        except FileNotFoundError:
            return None
        fields = raw[raw.rfind(")") + 2 :].split()
        return fields[0] if fields else None
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                return None
            return "R"
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    completed = subprocess.run(
        ["ps", "-o", "state=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=2.0,
    )
    state = completed.stdout.strip()
    if completed.returncode != 0 or not state:
        return None
    return "Z" if state.startswith("Z") else state[0]


def _is_owned_probe_process(pid: int, pid_file: Path) -> bool:
    if Path("/proc").is_dir():
        try:
            command_line = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        except FileNotFoundError:
            return False
        return str(pid_file).encode() in command_line
    if os.name == "nt":
        return _linux_process_state(pid) is not None
    completed = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
        timeout=2.0,
    )
    return completed.returncode == 0 and str(pid_file) in completed.stdout


def _kill_owned_probe_process(pid: int, pid_file: Path) -> None:
    if not _is_owned_probe_process(pid, pid_file):
        return
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGKILL)


def test_cancelled_aese_runner_stops_detached_verification_child(tmp_path: Path) -> None:
    probe_root = tmp_path.parent / f"{tmp_path.name}-aese-cancel-probe"
    probe_root.mkdir()
    pid_file = probe_root / "child.pid"
    heartbeat = probe_root / "heartbeat"
    facade, session_id, run_id = _session(tmp_path)
    profile = facade.inspect_project(tmp_path)
    command = LocalVerificationCommand(
        command_id="cancel-detached-child",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=(
            "-c",
            "import os,pathlib,signal,sys,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "f=open(sys.argv[2],'w',encoding='ascii');f.write('x\\n');f.flush();"
            "p=pathlib.Path(sys.argv[1]);t=pathlib.Path(sys.argv[1]+'.tmp');"
            "t.write_text(str(os.getpid()),encoding='ascii');t.replace(p);"
            "\nwhile True:\n f.write('x\\n');f.flush();time.sleep(0.02)",
            str(pid_file),
            str(heartbeat),
        ),
        working_directory=str(tmp_path),
        timeout_seconds=30.0,
        source_revision=profile.source_revision,
    )
    app = AgentApplication(
        AgentConfig(
            task="cancel the local verification command",
            llm=None,
            trust_level="DEV",
            browser=False,
            max_steps=1,
            options={},
            config={},
            api_key=None,
        )
    )
    app._verification_facade = facade
    app._verification_session_id = session_id
    app._aese_run_id = run_id
    app._aese_commands = {command.command_id: command}

    async def run_and_cancel() -> None:
        task: asyncio.Task[dict[str, object]] | None = asyncio.create_task(
            app._aese_tool_runner({"tool_name": "aese.local_verification", "input": command.as_dict()})
        )
        pid: int | None = None
        loop = asyncio.get_running_loop()
        try:
            deadline = loop.time() + 10.0
            # A separate process writes this marker; there is no asyncio event for the filesystem change.
            while not pid_file.exists():
                if task.done():
                    await task
                if loop.time() >= deadline:
                    pytest.fail("verification child did not publish its PID")
                await asyncio.sleep(0.02)
            pid = int(pid_file.read_text(encoding="ascii"))
            assert _linux_process_state(pid) not in (None, "Z", "X")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            stop_deadline = loop.time() + 2.0
            # /proc state is owned by the kernel, so bounded polling is the independent process oracle.
            while _linux_process_state(pid) not in (None, "Z", "X") and loop.time() < stop_deadline:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
            assert _linux_process_state(pid) in (None, "Z", "X")
            size_before = heartbeat.stat().st_size
            await asyncio.sleep(0.15)
            assert heartbeat.stat().st_size == size_before
        finally:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if pid is None:
                publish_deadline = loop.time() + 1.0
                # Cleanup waits briefly for a just-started child to publish its owned PID.
                while not pid_file.exists() and loop.time() < publish_deadline:  # noqa: ASYNC110
                    await asyncio.sleep(0.02)
                if pid_file.exists():
                    pid = int(pid_file.read_text(encoding="ascii"))
            if pid is not None:
                _kill_owned_probe_process(pid, pid_file)

    try:
        asyncio.run(run_and_cancel())
    finally:
        with contextlib.suppress(OSError):
            pid_file.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            heartbeat.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            probe_root.rmdir()


def test_successful_pytest_run_reaps_background_descendants(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    heartbeat = tmp_path / "heartbeat"
    test_file = tmp_path / "test_background_child.py"
    child_code = (
        "import signal,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "f=open(sys.argv[1],'a',encoding='ascii');"
        "\nwhile True:\n f.write('x');f.flush();time.sleep(0.02)"
    )
    test_file.write_text(
        "import pathlib,subprocess,sys\n"
        "def test_parent_finishes_with_child_running():\n"
        f"    child = subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(heartbeat)!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"    pid_path = pathlib.Path({str(pid_file)!r})\n"
        f"    tmp_path = pathlib.Path({str(pid_file) + '.tmp'!r})\n"
        "    tmp_path.write_text(str(child.pid), encoding='ascii')\n"
        "    tmp_path.replace(pid_path)\n"
        "    assert child.pid > 0\n",
        encoding="utf-8",
    )
    command = LocalVerificationCommand(
        command_id="successful-command-leaves-descendant",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "-q", str(test_file)),
        working_directory=str(tmp_path),
        timeout_seconds=10.0,
        source_revision="fixture-revision",
    )
    pid: int | None = None
    try:
        result = asyncio.run(execution_module.run_local_verification_command_async(command.as_dict()))
        pid = int(pid_file.read_text(encoding="ascii"))
        stop_deadline = time.monotonic() + 2.0
        while _linux_process_state(pid) not in (None, "Z", "X") and time.monotonic() < stop_deadline:
            time.sleep(0.02)
        assert _linux_process_state(pid) in (None, "Z", "X")
        assert result["status"] == "ERROR"
        assert result["failure_class"] == "PROCESS_TREE_REMAINS"
    finally:
        if pid is None and pid_file.exists():
            pid = int(pid_file.read_text(encoding="ascii"))
        if pid is not None and _is_owned_probe_process(pid, heartbeat):
            _kill_owned_probe_process(pid, heartbeat)
        with contextlib.suppress(OSError):
            pid_file.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            heartbeat.unlink(missing_ok=True)


def test_verification_timeout_kills_term_ignoring_child(tmp_path: Path) -> None:
    probe_root = tmp_path.parent / f"{tmp_path.name}-aese-timeout-probe"
    probe_root.mkdir()
    pid_file = probe_root / "child.pid"
    heartbeat = probe_root / "heartbeat"
    command = LocalVerificationCommand(
        command_id="timeout-term-ignoring-child",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=(
            "-c",
            "import os,pathlib,signal,sys,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "f=open(sys.argv[2],'w',encoding='ascii');f.write('x\\n');f.flush();"
            "p=pathlib.Path(sys.argv[1]);t=pathlib.Path(sys.argv[1]+'.tmp');"
            "t.write_text(str(os.getpid()),encoding='ascii');t.replace(p);"
            "\nwhile True:\n f.write('x\\n');f.flush();time.sleep(0.02)",
            str(pid_file),
            str(heartbeat),
        ),
        working_directory=str(tmp_path),
        timeout_seconds=1.0,
        source_revision="fixture-revision",
    )
    pid: int | None = None
    try:
        result = asyncio.run(execution_module.run_local_verification_command_async(command.as_dict()))
        assert result["status"] == "TIMEOUT"
        assert result["failure_class"] == "TIMEOUT"
        pid = int(pid_file.read_text(encoding="ascii"))
        stop_deadline = time.monotonic() + 2.0
        while _linux_process_state(pid) not in (None, "Z", "X") and time.monotonic() < stop_deadline:
            time.sleep(0.02)
        assert _linux_process_state(pid) in (None, "Z", "X")
        size_before = heartbeat.stat().st_size
        time.sleep(0.15)
        assert heartbeat.stat().st_size == size_before
    finally:
        if pid is None and pid_file.exists():
            pid = int(pid_file.read_text(encoding="ascii"))
        if pid is not None:
            _kill_owned_probe_process(pid, pid_file)
        with contextlib.suppress(OSError):
            pid_file.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            heartbeat.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            probe_root.rmdir()


def test_pytest_plugins_and_addopts_stripped_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("pytest_addopts", "--override-ini=cache_dir=/tmp/bad")
    monkeypatch.setenv("PyTest_AddOpts", "-o fake=1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore=tests")
    monkeypatch.setenv("pytest_plugins", "bad_plugin")
    monkeypatch.setenv("Pytest_Plugins", "another_bad_plugin")
    monkeypatch.setenv("PYTEST_PLUGINS", "malicious_entry")

    command = LocalVerificationCommand(
        command_id="test-env-sanitize",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-rev",
    )

    env = execution_module._verification_environment(command, is_pytest=True)
    for key in env:
        assert key.casefold() not in {"pytest_addopts", "pytest_plugins"}


def test_pytest_addopts_override_appended_even_without_report_directory(tmp_path: Path) -> None:
    import itertools

    command = LocalVerificationCommand(
        command_id="test-argv-addopts",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest", "tests/"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-rev",
    )
    argv, report_path = execution_module._verification_argv(command, is_pytest=True, report_directory=None)
    assert report_path is None
    assert ("-o", "addopts=") in itertools.pairwise(argv)


def test_environment_fingerprint_records_pytest_plugins(tmp_path: Path) -> None:
    command = LocalVerificationCommand(
        command_id="test-fingerprint-plugins",
        adapter="python-pytest",
        framework="pytest",
        executable=sys.executable,
        argv=("-m", "pytest"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-rev",
    )
    env = {
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTEST_ADDOPTS": "",
        "PYTEST_PLUGINS": "test_plugin_a,test_plugin_b",
    }
    import tempfile

    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        res = execution_module._build_local_verification_result(
            command,
            root=tmp_path,
            is_pytest=True,
            environment=env,
            started_at="2026-10-27T00:00:00Z",
            timed_out=False,
            output_limited=False,
            output_capture_failed=False,
            return_code=0,
            execution_argv=("-m", "pytest"),
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            report_path=None,
        )
    fingerprint = res["environment_fingerprint"]
    assert "PYTEST_PLUGINS" in fingerprint
    assert fingerprint["PYTEST_PLUGINS"] == "test_plugin_a,test_plugin_b"


def test_process_cell_windows_job_object_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class MockJob:
        def __init__(self) -> None:
            calls.append("init")

        def assign_process(self, pid: int) -> bool:
            calls.append(f"assign_{pid}")
            return True

        def terminate(self) -> bool:
            calls.append("terminate")
            return True

        def close(self) -> bool:
            calls.append("close")
            return True

    fake_os = SimpleNamespace(name="nt")
    monkeypatch.setattr(lab_module, "os", fake_os)

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 9999
            self._aegis_job = MockJob()

        def is_alive(self) -> bool:
            return False

        def terminate(self) -> None:
            calls.append("proc_terminate")

        def join(self, timeout: float | None = None) -> None:
            calls.append(f"proc_join_{timeout}")

    fake_proc = FakeProcess()
    ProcessExecutionCell._terminate_process(fake_proc)

    assert "terminate" in calls
    assert "close" in calls
    assert fake_proc._aegis_job is None


def _sample_cell_worker() -> int:
    return 42


def _sample_hanging_worker() -> None:
    time.sleep(10.0)


def test_process_execution_cell_job_object_lifecycle_on_windows() -> None:
    async def run_cell() -> None:
        cell = ProcessExecutionCell(
            _sample_cell_worker,
            timeout_seconds=5.0,
        )
        result = await cell()
        assert result == 42

    asyncio.run(run_cell())


def test_process_execution_cell_job_object_kills_child_on_timeout() -> None:
    async def run_timeout_cell() -> None:
        cell = ProcessExecutionCell(
            _sample_hanging_worker,
            timeout_seconds=0.3,
        )
        with pytest.raises(TimeoutError):
            await cell()

    asyncio.run(run_timeout_cell())
