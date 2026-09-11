import subprocess
import sys
from pathlib import Path

from scripts import suite_evidence as suite
from scripts.suite_evidence import (
    exclusive_suite_lock,
    execution_run_key,
    parse_counts,
    run_command,
)


def test_parse_counts_keeps_scope_fields() -> None:
    counts = parse_counts("418 tests run: 418 passed, 0 failed, 0 skipped\n0 ignored; 2 filtered out")
    assert counts == {
        "discovered": 418,
        "passed": 418,
        "failed": 0,
        "ignored": 0,
        "filtered": 2,
        "skipped": 0,
    }


def test_execution_run_key_is_stable_for_duplicate_detection() -> None:
    args = {
        "gate_id": "python-cross-language",
        "commit": "a" * 40,
        "command": ["uv", "run", "pytest"],
        "platform_name": "Windows",
        "toolchain_name": "CPython 3.14.7",
        "claim_scope": "LOCAL_CHECKOUT_ONLY",
    }

    other_gate = {**args, "gate_id": "other-gate"}
    assert execution_run_key(**args) == execution_run_key(**args)
    assert execution_run_key(**args) != execution_run_key(**other_gate)


def test_exclusive_suite_lock_rejects_a_second_process(tmp_path: Path) -> None:
    lock_path = tmp_path / ".suite-evidence.lock"
    code = (
        "from pathlib import Path; import sys; "
        "from scripts.suite_evidence import exclusive_suite_lock; "
        "\ntry:\n"
        "    with exclusive_suite_lock(Path(sys.argv[1])): pass\n"
        "except RuntimeError:\n"
        "    raise SystemExit(3)\n"
    )

    with exclusive_suite_lock(lock_path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(lock_path)],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 3


def test_exclusive_suite_lock_recovers_after_lane_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / ".suite-evidence.lock"

    try:
        with exclusive_suite_lock(lock_path):
            raise RuntimeError("lane failed")
    except RuntimeError as exc:
        assert str(exc) == "lane failed"

    with exclusive_suite_lock(lock_path):
        pass


def test_run_command_reports_timeout_without_claiming_success(monkeypatch) -> None:
    class FakeProcess:
        pid = 123
        returncode = 124

        def communicate(self, timeout=None):
            if timeout == 2:
                raise subprocess.TimeoutExpired(["example"], 2, output=b"partial", stderr=b"late")
            return "partial", "late"

        def kill(self):
            return None

        def wait(self, timeout=None):
            return None

    def timeout(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", timeout)
    monkeypatch.setattr(suite, "_terminate_process_tree", lambda _process: None)

    assert run_command(["example"], 2) == ("partial", "late", 124, True)


def test_run_command_records_missing_executable_as_failure(monkeypatch) -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("missing executable")

    monkeypatch.setattr(subprocess, "Popen", missing)

    assert run_command(["missing"], 2) == ("", "missing executable", 127, False)
