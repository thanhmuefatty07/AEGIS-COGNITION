from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from scripts import suite_evidence


def test_windows_kill_fallback_handles_taskkill_timeout(monkeypatch) -> None:
    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()

    def timeout_taskkill(command: list[str], **kwargs: object) -> None:
        assert command[:3] == ["taskkill", "/PID", str(process.pid)]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(suite_evidence, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(suite_evidence.subprocess, "run", timeout_taskkill)

    suite_evidence._terminate_process_tree(process)

    assert process.killed


def test_windows_job_child_fails_closed_when_assignment_fails(monkeypatch, capsys) -> None:
    launched_commands = []

    def deny_assignment(_job_name: str) -> None:
        raise PermissionError("assignment denied")

    def record_launch(command: list[str], check: bool) -> SimpleNamespace:
        launched_commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(suite_evidence, "_assign_current_process_to_windows_job", deny_assignment)
    monkeypatch.setattr(suite_evidence.subprocess, "run", record_launch)

    exit_code = suite_evidence._windows_job_child_entry(["job-name", "--", "target"])

    assert exit_code == 125
    assert launched_commands == []
    assert "Windows Job Object assignment failed" in capsys.readouterr().err


def test_run_command_does_not_launch_when_windows_job_creation_fails(monkeypatch) -> None:
    launches = []

    class FailedJob:
        def __init__(self) -> None:
            raise OSError("job creation denied")

    def record_launch(*args: object, **kwargs: object) -> None:
        launches.append((args, kwargs))

    monkeypatch.setattr(suite_evidence, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(suite_evidence, "_WindowsProcessJob", FailedJob)
    monkeypatch.setattr(suite_evidence.subprocess, "Popen", record_launch)

    result = suite_evidence.run_command(["target"], 2)

    assert result == ("", "job creation denied", 127, False)
    assert launches == []


def test_run_command_does_not_claim_success_when_windows_job_close_fails(monkeypatch) -> None:
    class FailedCloseJob:
        name = "job-name"

        def __init__(self) -> None:
            self.close_calls = 0
            self.terminate_calls = 0

        def close(self) -> bool:
            self.close_calls += 1
            return False

        def terminate(self) -> bool:
            self.terminate_calls += 1
            return False

    class CompletedProcess:
        pid = 123
        returncode = 0

        def communicate(self, timeout: int) -> tuple[str, str]:
            return "", ""

    job = FailedCloseJob()
    monkeypatch.setattr(suite_evidence, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(suite_evidence, "_WindowsProcessJob", lambda: job)
    monkeypatch.setattr(suite_evidence.subprocess, "CREATE_NEW_PROCESS_GROUP", 0, raising=False)
    monkeypatch.setattr(suite_evidence.subprocess, "Popen", lambda *args, **kwargs: CompletedProcess())

    result = suite_evidence.run_command(["target"], 2)

    assert result == (
        "",
        "Windows Job Object could not be closed cleanly; process cleanup is unverified",
        125,
        False,
    )
    assert job.close_calls == 2
    assert job.terminate_calls == 1


def test_run_command_terminates_descendant_when_leader_exits(tmp_path: Path) -> None:
    ready_file = tmp_path / "descendant.ready"
    late_file = tmp_path / "descendant.late"
    child_script = tmp_path / "descendant.py"
    parent_script = tmp_path / "parent.py"
    child_script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "target = Path(sys.argv[1])\n"
        "tmp = target.with_suffix('.tmp')\n"
        "tmp.write_text('ready', encoding='ascii')\n"
        "tmp.replace(target)\n"
        "time.sleep(2)\n"
        "Path(sys.argv[2]).write_text('late', encoding='ascii')\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "subprocess.Popen([sys.executable, sys.argv[2], sys.argv[3], sys.argv[4]], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "deadline = time.monotonic() + 2\n"
        "while not Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "if not Path(sys.argv[1]).exists():\n"
        "    raise SystemExit(5)\n",
        encoding="utf-8",
    )

    stdout, stderr, exit_code, timed_out = suite_evidence.run_command(
        [sys.executable, str(parent_script), str(ready_file), str(child_script), str(ready_file), str(late_file)],
        5,
    )[:4]
    deadline = time.monotonic() + 2.5
    while not late_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert stdout == ""
    assert stderr == ""
    assert exit_code == 0
    assert timed_out is False
    assert ready_file.read_text(encoding="ascii") == "ready"
    assert not late_file.exists(), "a descendant must not outlive a successfully completed command"


def test_run_command_terminates_descendant_when_command_times_out(tmp_path: Path) -> None:
    ready_file = tmp_path / "timeout-descendant.ready"
    late_file = tmp_path / "timeout-descendant.late"
    child_script = tmp_path / "timeout-descendant.py"
    parent_script = tmp_path / "timeout-parent.py"
    child_script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "target = Path(sys.argv[1])\n"
        "tmp = target.with_suffix('.tmp')\n"
        "tmp.write_text('ready', encoding='ascii')\n"
        "tmp.replace(target)\n"
        "time.sleep(5)\n"
        "Path(sys.argv[2]).write_text('late', encoding='ascii')\n",
        encoding="utf-8",
    )
    parent_script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "subprocess.Popen([sys.executable, sys.argv[2], sys.argv[3], sys.argv[4]], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "deadline = time.monotonic() + 4\n"
        "while not Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "if not Path(sys.argv[1]).exists():\n"
        "    raise SystemExit(5)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    stdout, stderr, exit_code, timed_out = suite_evidence.run_command(
        [sys.executable, str(parent_script), str(ready_file), str(child_script), str(ready_file), str(late_file)],
        3,
    )
    deadline = time.monotonic() + 3.5
    while not late_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert stdout == ""
    assert stderr == ""
    assert exit_code == 124
    assert timed_out is True
    assert ready_file.read_text(encoding="ascii") == "ready"
    assert not late_file.exists(), "a timed-out command must not leave a live descendant"
