import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import suite_evidence as suite
from scripts.suite_evidence import (
    canonical_receipt_digest,
    classify_result,
    exclusive_suite_lock,
    execution_run_key,
    parse_counts,
    run_command,
    verify_receipt,
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


def test_parse_counts_strips_terminal_formatting() -> None:
    counts = parse_counts(
        "\x1b[32m566\x1b[0m tests run: \x1b[32m566\x1b[0m passed, \x1b[31m0\x1b[0m failed, \x1b[33m0\x1b[0m skipped"
    )
    assert counts == {
        "discovered": 566,
        "passed": 566,
        "failed": 0,
        "ignored": 0,
        "filtered": 0,
        "skipped": 0,
    }


def test_zero_test_success_is_not_verifiable() -> None:
    assert classify_result(
        exit_code=0,
        timed_out=False,
        discovered=0,
        passed=0,
        failed=0,
        ignored=0,
        skipped=0,
    ) == (
        "NOT_VERIFIED",
        "LOCAL ZERO TESTS",
        False,
        "ZERO_TESTS",
    )


def test_successful_nonempty_suite_is_proven() -> None:
    assert classify_result(
        exit_code=0,
        timed_out=False,
        discovered=1,
        passed=1,
        failed=0,
        ignored=0,
        skipped=0,
    ) == (
        "PROVEN",
        "LOCALLY PROVEN",
        True,
        None,
    )


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


def test_execution_run_key_separates_dirty_worktree_fingerprints() -> None:
    args = {
        "gate_id": "python-cross-language",
        "commit": "a" * 40,
        "command": ["uv", "run", "pytest"],
        "platform_name": "Windows",
        "toolchain_name": "CPython 3.14.7",
        "claim_scope": "LOCAL_CHECKOUT_ONLY",
    }
    assert execution_run_key(**args, worktree_fingerprint="0" * 64) != execution_run_key(
        **args,
        worktree_fingerprint="1" * 64,
    )


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


def test_main_suppresses_captured_child_output_in_github_actions(monkeypatch, tmp_path, capsys) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        'print("TEST_ONLY_CHILD_OUTPUT")\nprint("1 tests run: 1 passed, 0 failed, 0 skipped")\n',
        encoding="utf-8",
    )
    output = tmp_path / "suite.json"
    status_output = tmp_path / "suite-status.json"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "test-suite",
            "--output",
            str(output),
            "--status-output",
            str(status_output),
            "--owner-id",
            "test-owner",
            "--gate-id",
            "test-gate",
            "--timeout-seconds",
            "10",
            "--",
            sys.executable,
            str(child),
        ],
    )

    assert suite.main() == 0

    captured = capsys.readouterr()
    assert "TEST_ONLY_CHILD_OUTPUT" not in captured.out
    public_status = json.loads(captured.out)
    assert public_status == {
        "schema": "aegis-suite-status-v1",
        "gate_id": "test-gate",
        "status": "NOT_VERIFIED",
        "failure_reason": "UNSUPPORTED_TEST_RUNNER",
    }
    assert "command" not in captured.out
    assert "duration_seconds" not in captured.out
    assert "runner_report_sha256" not in captured.out
    assert json.loads(status_output.read_text(encoding="utf-8")) == public_status

    private_evidence = json.loads(output.read_text(encoding="utf-8"))
    assert private_evidence["status"] == "NOT_VERIFIED"
    assert private_evidence["command_argv"][-1] == str(child)
    assert "duration_seconds" in private_evidence


def test_count_printing_noop_cannot_become_suite_evidence(monkeypatch, tmp_path: Path) -> None:
    from scripts.evidence_consistency_gate import suite_artifact_release_eligible

    output = tmp_path / "suite.json"
    expected_command = (
        "uv run --locked --extra all --extra dev python -m pytest "
        "tests core/python/tests.py -v -W error::DeprecationWarning"
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "cross-platform-full-suite-windows-latest",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "cross-platform-full-suite-windows-latest",
            "--timeout-seconds",
            "10",
            "--",
            sys.executable,
            "-c",
            "print('1 passed')",
            expected_command,
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["release_eligible"] is False
    assert evidence["status"] == "NOT_VERIFIED"
    assert not suite_artifact_release_eligible(evidence)


def test_pytest_counts_ignore_misleading_child_stdout(monkeypatch, tmp_path: Path) -> None:
    test_file = tmp_path / "test_fixture.py"
    test_file.write_text("def test_fixture():\n    print('999 passed')\n    assert 2 + 2 == 4\n", encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    output = tmp_path / "suite.json"
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "pytest-fixture",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "pytest-fixture",
            "--timeout-seconds",
            "20",
            "--",
            sys.executable,
            "-m",
            "pytest",
            "--noconftest",
            "-c",
            str(config),
            str(test_file),
            "-q",
            "-s",
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["passed"] == evidence["discovered"] == 1
    assert evidence["release_eligible"] is True
    assert {path.name for path in tmp_path.iterdir()} <= {
        "test_fixture.py",
        "pytest.ini",
        "suite.json",
        ".suite-evidence.lock",
        "__pycache__",
        ".pytest_cache",
    }


@pytest.mark.parametrize("report_content", [None, "<broken"], ids=["missing", "malformed"])
def test_pytest_report_failure_never_falls_back_to_stdout(
    monkeypatch, tmp_path: Path, report_content: str | None
) -> None:
    output = tmp_path / "suite.json"

    def report_failure(command: list[str], _timeout: int) -> tuple[str, str, int, bool]:
        if report_content is not None and "--junitxml" in command:
            Path(command[command.index("--junitxml") + 1]).write_text(report_content, encoding="utf-8")
        return "1 passed", "", 0, False

    monkeypatch.setattr(suite, "run_command", report_failure)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "pytest-fixture",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "pytest-fixture",
            "--timeout-seconds",
            "10",
            "--",
            sys.executable,
            "-m",
            "pytest",
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["release_eligible"] is False
    assert evidence["status"] == "NOT_VERIFIED"
    assert {path.name for path in tmp_path.iterdir()} == {"suite.json", ".suite-evidence.lock"}


@pytest.mark.parametrize(
    "runner,child_output,expected_passed,expected_skipped",
    [
        (
            ["cargo", "test"],
            "999 passed\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0s\n"
            "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0s\n",
            3,
            0,
        ),
        (["cargo", "nextest", "run"], "999 passed\nSummary [1s] 3 tests run: 3 passed, 0 skipped\n", 3, 0),
        (["cargo", "test"], "1 passed\n", 0, 0),
        (["cargo", "nextest", "run"], "1 passed\n", 0, 0),
        (["cargo", "nextest", "run"], "Summary [0.620s] 1 test run: 1 passed, 1 skipped\n", 1, 1),
    ],
    ids=["cargo-harnesses", "nextest-summary", "cargo-no-summary", "nextest-no-summary", "nextest-with-skips"],
)
def test_rust_evidence_requires_native_runner_summaries(
    monkeypatch, tmp_path: Path, runner: list[str], child_output: str, expected_passed: int, expected_skipped: int
) -> None:
    output = tmp_path / "suite.json"
    monkeypatch.setattr(suite, "run_command", lambda _command, _timeout: (child_output, "", 0, False))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "rust-fixture",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "rust-fixture",
            "--timeout-seconds",
            "10",
            "--",
            *runner,
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["passed"] == expected_passed
    assert evidence["skipped"] == expected_skipped
    assert evidence["discovered"] == expected_passed + expected_skipped
    assert evidence["release_eligible"] is (expected_passed > 0)


@pytest.mark.parametrize("attached", [False, True], ids=["separate-config", "attached-config"])
def test_cargo_runner_configuration_cannot_attest_test_execution(monkeypatch, tmp_path: Path, attached: bool) -> None:
    from scripts.evidence_consistency_gate import suite_artifact_release_eligible

    output = tmp_path / "suite.json"
    setting = 'target.fixture.runner=["python","counterfeit_runner.py"]'
    override = [f"--config={setting}"] if attached else ["--config", setting]
    monkeypatch.setattr(
        suite,
        "run_command",
        lambda _command, _timeout: (
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n",
            "",
            0,
            False,
        ),
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "rust-runner-override",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "rust-fixture",
            "--timeout-seconds",
            "10",
            "--",
            "cargo",
            "test",
            "--target",
            "fixture",
            *override,
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["status"] == "NOT_VERIFIED"
    assert evidence["release_eligible"] is False
    assert not suite_artifact_release_eligible(evidence)


@pytest.mark.parametrize("source", ["environment", "project-config", "cargo-home-config", "included-project-config"])
@pytest.mark.parametrize(
    "runner,child_output",
    [
        (
            ["cargo", "test", "--target", "fixture"],
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n",
        ),
        (["cargo", "nextest", "run", "--target", "fixture"], "Summary [1s] 1 test run: 1 passed, 0 skipped\n"),
    ],
    ids=["cargo-test", "cargo-nextest"],
)
def test_configured_cargo_target_runner_cannot_attest_test_execution(
    monkeypatch, tmp_path: Path, source: str, runner: list[str], child_output: str
) -> None:
    from scripts.evidence_consistency_gate import suite_artifact_release_eligible

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cargo_home = tmp_path / "cargo-home"
    cargo_home.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    for name in tuple(os.environ):
        if name.upper().startswith("CARGO_TARGET_") and name.upper().endswith("_RUNNER"):
            monkeypatch.delenv(name, raising=False)

    setting = '[target.fixture]\nrunner = ["counterfeit-runner"]\n'
    if source == "environment":
        monkeypatch.setenv("CARGO_TARGET_FIXTURE_RUNNER", "counterfeit-runner")
    elif source == "project-config":
        project_config = workspace / ".cargo" / "config.toml"
        project_config.parent.mkdir()
        project_config.write_text(setting, encoding="utf-8")
    elif source == "included-project-config":
        project_config = workspace / ".cargo" / "config.toml"
        project_config.parent.mkdir()
        project_config.write_text('include = ["runner.toml"]\n', encoding="utf-8")
        (project_config.parent / "runner.toml").write_text(setting, encoding="utf-8")
    else:
        (cargo_home / "config.toml").write_text(setting, encoding="utf-8")

    output = tmp_path / "suite.json"
    monkeypatch.setattr(suite, "run_command", lambda _command, _timeout: (child_output, "", 0, False))
    monkeypatch.setattr(suite, "toolchain", lambda _command: "rustc 1.99.1")
    monkeypatch.setattr(suite, "_git_worktree_fingerprint", lambda: ("0" * 64, "CLEAN"))
    monkeypatch.setattr(suite, "_git_remote_observation", lambda _revision: {})
    monkeypatch.setattr(suite.subprocess, "check_output", lambda *_args, **_kwargs: "0" * 40)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "rust-runner-override",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "rust-fixture",
            "--timeout-seconds",
            "10",
            "--",
            *runner,
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["status"] == "NOT_VERIFIED"
    assert evidence["release_eligible"] is False
    assert evidence["failure_reason"] == "UNSUPPORTED_TEST_RUNNER"
    assert not suite_artifact_release_eligible(evidence)


def test_nextest_cross_target_checks_host_runner(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "suite.json"
    monkeypatch.setattr(
        suite,
        "run_command",
        lambda _command, _timeout: ("Summary [1s] 1 test run: 1 passed, 0 skipped\n", "", 0, False),
    )
    monkeypatch.setattr(suite, "toolchain", lambda _command: "rustc 1.99.1")
    monkeypatch.setattr(suite, "_git_worktree_fingerprint", lambda: ("0" * 64, "CLEAN"))
    monkeypatch.setattr(suite, "_git_remote_observation", lambda _revision: {})
    monkeypatch.setattr(suite.subprocess, "check_output", lambda *_args, **_kwargs: "0" * 40)
    monkeypatch.setattr(
        suite.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="host: x86_64-unknown-linux-gnu\n", stderr=""
        ),
    )
    monkeypatch.setenv("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER", "counterfeit-runner")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "nextest-cross-target-host-runner",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "rust-fixture",
            "--timeout-seconds",
            "10",
            "--",
            "cargo",
            "nextest",
            "run",
            "--target",
            "aarch64-unknown-linux-gnu",
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["status"] == "NOT_VERIFIED"
    assert evidence["release_eligible"] is False
    assert evidence["failure_reason"] == "UNSUPPORTED_TEST_RUNNER"


def test_custom_cargo_test_harness_cannot_attest_test_execution(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "suite.json"
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[package]\nname = "counterfeit"\nversion = "0.0.0"\nedition = "2024"\n'
        '[[test]]\nname = "counterfeit"\nharness = false\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        suite,
        "run_command",
        lambda _command, _timeout: (
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n",
            "",
            0,
            False,
        ),
    )
    monkeypatch.setattr(suite, "toolchain", lambda _command: "rustc 1.99.1")
    monkeypatch.setattr(suite, "_git_worktree_fingerprint", lambda: ("0" * 64, "CLEAN"))
    monkeypatch.setattr(suite, "_git_remote_observation", lambda _revision: {})
    monkeypatch.setattr(suite.subprocess, "check_output", lambda *_args, **_kwargs: "0" * 40)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "custom-harness",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "rust-fixture",
            "--timeout-seconds",
            "10",
            "--",
            "cargo",
            "test",
            "--manifest-path",
            str(manifest),
            "--test",
            "counterfeit",
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["status"] == "NOT_VERIFIED"
    assert evidence["release_eligible"] is False
    assert evidence["failure_reason"] == "UNSUPPORTED_TEST_HARNESS"


def test_unrelated_cargo_target_runner_does_not_block_selected_target(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cargo_home = tmp_path / "cargo-home"
    cargo_home.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    (cargo_home / "config.toml").write_text('[target.unrelated]\nrunner = ["counterfeit-runner"]\n', encoding="utf-8")
    output = tmp_path / "suite.json"
    monkeypatch.setattr(
        suite,
        "run_command",
        lambda _command, _timeout: (
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n",
            "",
            0,
            False,
        ),
    )
    monkeypatch.setattr(suite, "toolchain", lambda _command: "rustc 1.99.1")
    monkeypatch.setattr(suite, "_git_worktree_fingerprint", lambda: ("0" * 64, "CLEAN"))
    monkeypatch.setattr(suite, "_git_remote_observation", lambda _revision: {})
    monkeypatch.setattr(suite.subprocess, "check_output", lambda *_args, **_kwargs: "0" * 40)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "rust-runner-unrelated-target",
            "--output",
            str(output),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "rust-fixture",
            "--timeout-seconds",
            "10",
            "--",
            "cargo",
            "test",
            "--target",
            "fixture",
        ],
    )

    assert suite.main() == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))

    assert evidence["status"] == "PROVEN"
    assert evidence["release_eligible"] is True


@pytest.mark.parametrize(
    (
        "child_output",
        "discovered",
        "passed",
        "failed",
        "expected_status",
        "reason",
        "expected_exit_code",
    ),
    (
        (
            "1 test run",
            1,
            0,
            0,
            "NOT_VERIFIED",
            "NO_PASSED_TESTS",
            0,
        ),
        (
            "2 tests run: 1 passed, 1 failed, 0 skipped",
            2,
            1,
            1,
            "FAILED",
            "FAILED_TESTS",
            1,
        ),
        (
            "1 test run: 0 passed, 1 failed, 0 skipped",
            1,
            0,
            1,
            "FAILED",
            "FAILED_TESTS",
            1,
        ),
    ),
    ids=("zero-passes", "mixed-pass-fail", "all-fail"),
)
def test_nonpassing_process_cannot_emit_proven_cli_evidence(
    tmp_path: Path,
    child_output: str,
    discovered: int,
    passed: int,
    failed: int,
    expected_status: str,
    reason: str,
    expected_exit_code: int,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    child = tmp_path / "child.py"
    child.write_text(f"print({child_output!r})\n", encoding="utf-8")
    output = tmp_path / "suite.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(repository_root / "scripts" / "suite_evidence.py"),
            "--name",
            "nonpassing-suite",
            "--output",
            str(output),
            "--owner-id",
            "test-owner",
            "--gate-id",
            "nonpassing-suite",
            "--attempt-id",
            "test-attempt",
            "--timeout-seconds",
            "10",
            "--",
            sys.executable,
            "-B",
            str(child),
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_exit_code
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["discovered"] == discovered
    assert evidence["passed"] == passed
    assert evidence["failed"] == failed
    assert evidence["status"] == expected_status
    assert evidence["release_eligible"] is False
    assert evidence["failure_reason"] == reason


def test_canonical_receipt_digest_excludes_receipt_digest_field() -> None:
    base_payload = {
        "gate_id": "test-gate",
        "passed": 42,
        "discovered": 42,
        "status": "PROVEN",
    }
    digest_without = canonical_receipt_digest(base_payload)
    digest_with = canonical_receipt_digest({**base_payload, "receipt_digest": "dummy_digest_value"})
    digest_with_different = canonical_receipt_digest(
        {**base_payload, "receipt_digest": "0123456789abcdef0123456789abcdef"}
    )
    assert digest_without == digest_with == digest_with_different
    expected_bytes = json.dumps(base_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert digest_without == hashlib.sha256(expected_bytes).hexdigest()


def test_canonical_receipt_digest_detects_field_tampering() -> None:
    base = {
        "attempt_id": "att-1",
        "discovered": 10,
        "gate_id": "gate-1",
        "owner_id": "ci",
        "passed": 10,
        "status": "PROVEN",
    }
    base_digest = canonical_receipt_digest(base)

    mutations = [
        ("passed", 9),
        ("passed", 11),
        ("discovered", 11),
        ("status", "FAILED"),
        ("status", "NOT_VERIFIED"),
        ("gate_id", "gate-2"),
        ("owner_id", "rogue"),
        ("attempt_id", "att-2"),
        ("extra_field", "injected_value"),
    ]
    for key, value in mutations:
        tampered = dict(base)
        tampered[key] = value
        tampered_digest = canonical_receipt_digest(tampered)
        assert tampered_digest != base_digest, f"Digest did not change when tampering with {key}={value}"

    deleted_field_receipt = {k: v for k, v in base.items() if k != "owner_id"}
    assert canonical_receipt_digest(deleted_field_receipt) != base_digest


def test_canonical_receipt_digest_canonical_key_ordering_and_invariant_formatting() -> None:
    dict_a = {
        "z_field": 100,
        "a_field": "hello",
        "m_field": {"y_nested": "val", "b_nested": 10},
    }
    dict_b = {
        "a_field": "hello",
        "m_field": {"b_nested": 10, "y_nested": "val"},
        "z_field": 100,
    }
    assert canonical_receipt_digest(dict_a) == canonical_receipt_digest(dict_b)


def test_verify_receipt_rejects_non_canonical_digest(tmp_path: Path) -> None:
    payload = {
        "gate_id": "gate-test",
        "passed": 5,
        "discovered": 5,
        "status": "PROVEN",
    }
    non_canonical_bytes = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    non_canonical_digest = hashlib.sha256(non_canonical_bytes).hexdigest()

    receipt_file = tmp_path / "non_canonical.json"
    receipt_file.write_text(
        json.dumps({**payload, "receipt_digest": non_canonical_digest}, indent=2),
        encoding="utf-8",
    )

    ok, msg = verify_receipt(receipt_file)
    assert ok is False
    assert "tamper-evidence digest mismatch" in msg


def test_verify_receipt_accepts_authentic_receipt(tmp_path: Path) -> None:
    receipt = {
        "attempt_id": "att-42",
        "command": ["cargo", "test"],
        "discovered": 42,
        "failed": 0,
        "gate_id": "rust-native-gate",
        "name": "rust-suite",
        "owner_id": "ci-builder",
        "passed": 42,
        "status": "PROVEN",
    }
    receipt["receipt_digest"] = canonical_receipt_digest(receipt)
    receipt_path = tmp_path / "authentic_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    ok, msg = verify_receipt(receipt_path)
    assert ok is True
    assert "receipt verified successfully" in msg
    assert "42/42 passed" in msg
    assert receipt["receipt_digest"][:16] in msg


def test_verify_receipt_rejects_tampered_receipt(tmp_path: Path) -> None:
    authentic = {
        "attempt_id": "att-1",
        "discovered": 20,
        "gate_id": "unit-test-gate",
        "name": "unit-suite",
        "owner_id": "tester",
        "passed": 20,
        "status": "PROVEN",
    }
    authentic["receipt_digest"] = canonical_receipt_digest(authentic)

    tampered_cases = [
        ({"passed": 21}, "tampered passed count"),
        ({"discovered": 19}, "tampered discovered count"),
        ({"status": "FAILED"}, "tampered status"),
        ({"gate_id": "different-gate"}, "tampered gate_id"),
        ({"receipt_digest": "0" * 64}, "tampered receipt_digest"),
    ]
    for overrides, label in tampered_cases:
        receipt_data = dict(authentic, **overrides)
        receipt_file = tmp_path / f"tampered_{label.replace(' ', '_')}.json"
        receipt_file.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")

        ok, msg = verify_receipt(receipt_file)
        assert ok is False, f"Expected verification failure for {label}"
        assert "tamper-evidence digest mismatch" in msg


def test_zero_assertion_rejection_for_zero_discovered_tests(tmp_path: Path) -> None:
    receipt = {
        "gate_id": "empty-gate",
        "status": "PROVEN",
        "discovered": 0,
        "passed": 0,
    }
    receipt["receipt_digest"] = canonical_receipt_digest(receipt)
    receipt_file = tmp_path / "zero_discovered.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    ok, msg = verify_receipt(receipt_file)
    assert ok is False
    assert "zero-assertion rejection" in msg
    assert "discovered=0" in msg


def test_zero_assertion_rejection_for_zero_passed_tests(tmp_path: Path) -> None:
    receipt = {
        "gate_id": "no-pass-gate",
        "status": "PROVEN",
        "discovered": 10,
        "passed": 0,
    }
    receipt["receipt_digest"] = canonical_receipt_digest(receipt)
    receipt_file = tmp_path / "zero_passed.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    ok, msg = verify_receipt(receipt_file)
    assert ok is False
    assert "zero-assertion rejection" in msg
    assert "passed=0" in msg


def test_verify_receipt_rejects_non_proven_status(tmp_path: Path) -> None:
    for non_proven_status in ("FAILED", "NOT_VERIFIED", "ERROR", "TIMEOUT"):
        receipt = {
            "gate_id": "non-proven-gate",
            "status": non_proven_status,
            "discovered": 10,
            "passed": 10,
        }
        receipt["receipt_digest"] = canonical_receipt_digest(receipt)
        receipt_file = tmp_path / f"receipt_{non_proven_status}.json"
        receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

        ok, msg = verify_receipt(receipt_file)
        assert ok is False
        assert "zero-assertion rejection" in msg
        assert f"gate status is {non_proven_status}" in msg


def test_verify_receipt_invalid_and_missing_file_handling(tmp_path: Path) -> None:
    missing_file = tmp_path / "does_not_exist.json"
    ok, msg = verify_receipt(missing_file)
    assert ok is False
    assert "receipt file not found" in msg

    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{ unclosed json", encoding="utf-8")
    ok, msg = verify_receipt(corrupt_file)
    assert ok is False
    assert "failed to read receipt JSON" in msg

    array_file = tmp_path / "array.json"
    array_file.write_text("[1, 2, 3]", encoding="utf-8")
    ok, msg = verify_receipt(array_file)
    assert ok is False
    assert "receipt root is not a JSON object" in msg

    missing_digest_file = tmp_path / "no_digest.json"
    missing_digest_file.write_text(
        json.dumps({"status": "PROVEN", "passed": 10, "discovered": 10}),
        encoding="utf-8",
    )
    ok, msg = verify_receipt(missing_digest_file)
    assert ok is False
    assert "receipt is missing receipt_digest field" in msg

    invalid_digest_file = tmp_path / "invalid_digest_type.json"
    invalid_digest_file.write_text(
        json.dumps({"status": "PROVEN", "passed": 10, "discovered": 10, "receipt_digest": 12345}),
        encoding="utf-8",
    )
    ok, msg = verify_receipt(invalid_digest_file)
    assert ok is False
    assert "receipt is missing receipt_digest field" in msg


def test_suite_evidence_cli_verify_subcommand(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    receipt = {
        "gate_id": "cli-test-gate",
        "passed": 15,
        "discovered": 15,
        "status": "PROVEN",
    }
    receipt["receipt_digest"] = canonical_receipt_digest(receipt)
    valid_file = tmp_path / "valid_cli_receipt.json"
    valid_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    # 1. Success on authentic receipt (exit code 0)
    monkeypatch.setattr(sys, "argv", ["suite_evidence.py", "verify", str(valid_file)])
    assert suite.main() == 0
    captured = capsys.readouterr()
    assert "VERIFIED: receipt verified successfully" in captured.out
    assert captured.err == ""

    # 2. Also check --verify variant (exit code 0)
    monkeypatch.setattr(sys, "argv", ["suite_evidence.py", "--verify", str(valid_file)])
    assert suite.main() == 0
    captured = capsys.readouterr()
    assert "VERIFIED: receipt verified successfully" in captured.out

    # 3. Failure on tampered receipt (exit code 1)
    tampered_file = tmp_path / "tampered_cli_receipt.json"
    tampered_file.write_text(json.dumps({**receipt, "passed": 14}, indent=2), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["suite_evidence.py", "verify", str(tampered_file)])
    assert suite.main() == 1
    captured = capsys.readouterr()
    assert "VERIFICATION FAILED: tamper-evidence digest mismatch" in captured.err

    # 4. Missing argument (exit code 2)
    monkeypatch.setattr(sys, "argv", ["suite_evidence.py", "verify"])
    assert suite.main() == 2
    captured = capsys.readouterr()
    assert "Usage: suite_evidence.py verify <receipt_path>" in captured.err


def test_suite_evidence_cli_verify_subcommand_subprocess(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    suite_evidence_script = repository_root / "scripts" / "suite_evidence.py"

    receipt = {
        "gate_id": "subprocess-gate",
        "passed": 8,
        "discovered": 8,
        "status": "PROVEN",
    }
    receipt["receipt_digest"] = canonical_receipt_digest(receipt)
    valid_file = tmp_path / "subp_valid.json"
    valid_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    # Authentic receipt
    result_valid = subprocess.run(
        [sys.executable, "-B", str(suite_evidence_script), "verify", str(valid_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result_valid.returncode == 0
    assert result_valid.stdout.startswith("VERIFIED:")

    # Tampered receipt
    tampered_file = tmp_path / "subp_tampered.json"
    tampered_file.write_text(json.dumps({**receipt, "passed": 99}, indent=2), encoding="utf-8")
    result_tampered = subprocess.run(
        [sys.executable, "-B", str(suite_evidence_script), "verify", str(tampered_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result_tampered.returncode == 1
    assert "VERIFICATION FAILED:" in result_tampered.stderr

    # Missing arguments
    result_noargs = subprocess.run(
        [sys.executable, "-B", str(suite_evidence_script), "verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result_noargs.returncode == 2
    assert "Usage: suite_evidence.py verify <receipt_path>" in result_noargs.stderr


def test_ambient_injected_options_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-o fake_ini=1 --override-ini=fake=true")
    monkeypatch.setenv("PYTEST_PLUGINS", "fake_plugin,another_plugin")

    probe_code = (
        "import os\n"
        "print('OPT=' + str(os.environ.get('PYTEST_ADDOPTS')))\n"
        "print('PLUG=' + str(os.environ.get('PYTEST_PLUGINS')))\n"
    )
    stdout, _stderr, exit_code, timed_out = run_command(
        [sys.executable, "-c", probe_code],
        timeout_seconds=10,
    )
    assert exit_code == 0
    assert timed_out is False
    assert "OPT=None" in stdout
    assert "PLUG=None" in stdout


def test_run_command_sanitizes_ambient_environment_windows_and_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[dict[str, str]] = []
    real_popen = subprocess.Popen

    class CapturedPopen:
        def __init__(self, *args, **kwargs) -> None:
            if "env" in kwargs and kwargs["env"] is not None:
                captured_envs.append(dict(kwargs["env"]))
            self._real = real_popen(*args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._real, name)

    monkeypatch.setenv("PYTEST_ADDOPTS", "--override-ini=cache_dir=/tmp/bad")
    monkeypatch.setenv("PYTEST_PLUGINS", "malicious_override")
    monkeypatch.setattr(subprocess, "Popen", CapturedPopen)

    _stdout, _stderr, exit_code, _timed_out = run_command(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        timeout_seconds=10,
    )
    assert exit_code == 0
    assert len(captured_envs) >= 1
    for env in captured_envs:
        assert "PYTEST_ADDOPTS" not in env
        assert "PYTEST_PLUGINS" not in env


def test_ambient_injected_options_stripped_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[dict[str, str]] = []
    real_popen = subprocess.Popen

    class CapturedPopen:
        def __init__(self, *args, **kwargs) -> None:
            if "env" in kwargs and kwargs["env"] is not None:
                captured_envs.append(dict(kwargs["env"]))
            self._real = real_popen(*args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._real, name)

    monkeypatch.setenv("pytest_addopts", "--override-ini=cache_dir=/tmp/bad")
    monkeypatch.setenv("PyTest_AddOpts", "-o fake=1")
    monkeypatch.setenv("pytest_plugins", "bad_plugin")
    monkeypatch.setenv("Pytest_Plugins", "another_bad_plugin")
    monkeypatch.setattr(subprocess, "Popen", CapturedPopen)

    _stdout, _stderr, exit_code, _timed_out = run_command(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        timeout_seconds=10,
    )
    assert exit_code == 0
    assert len(captured_envs) >= 1
    for env in captured_envs:
        for key in env:
            assert key.lower() not in {"pytest_addopts", "pytest_plugins"}


def test_suite_evidence_main_pytest_runner_appends_addopts_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import itertools

    executed_commands: list[list[str]] = []

    def fake_run_command(command: list[str], timeout_seconds: float):
        executed_commands.append(list(command))
        return "", "", 0, False

    out_file = tmp_path / "receipt.json"

    def fake_pytest_report(path: Path):
        return {"discovered": 1, "passed": 1, "failed": 0, "ignored": 0, "filtered": 0, "skipped": 0}, "hash", 100

    monkeypatch.setattr(suite, "run_command", fake_run_command)
    monkeypatch.setattr(suite, "_pytest_report", fake_pytest_report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "test-pytest-run",
            "--gate-id",
            "pytest-gate",
            "--owner-id",
            "local-profile",
            "--timeout-seconds",
            "10",
            "--output",
            str(out_file),
            "--",
            sys.executable,
            "-m",
            "pytest",
            "tests/",
        ],
    )

    exit_code = suite.main()
    assert exit_code == 0
    assert len(executed_commands) == 1
    cmd = executed_commands[0]
    assert ("-o", "addopts=") in itertools.pairwise(cmd)
