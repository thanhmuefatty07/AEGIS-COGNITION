from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_CASES = {
    "test_disposable_worktree_rejects_scratch_root_junction_swap_before_cleanup",
    "test_junctioned_directory_is_not_traversed",
    "test_windows_installer_parses_without_errors",
    "test_native_build_uses_system_command_interpreter_not_comspec",
    "test_windows_installer_stops_at_native_exit_seven[uv]",
    "test_windows_installer_stops_at_native_exit_seven[rustup]",
    "test_windows_installer_stops_at_native_exit_seven[cargo]",
    "test_windows_installer_stops_at_native_exit_seven[git]",
    "test_cmd_environment_indirection_preserves_percent_and_metacharacters_in_paths",
}


def _collect_node_ids(arguments: list[str], temporary_root: Path) -> list[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *(argument for argument in arguments if argument not in {"-v", "-q"}),
            "--collect-only",
            "-q",
            "--basetemp",
            str(temporary_root / "fixtures"),
            "-o",
            f"cache_dir={temporary_root / 'cache'}",
        ],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    nodes = [
        line.strip() for line in result.stdout.splitlines() if line.partition("::")[0].endswith(".py") and "::" in line
    ]
    assert nodes
    return nodes


@pytest.fixture(scope="module")
def portable_nodes(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    return _collect_node_ids(["tests", "core/python/tests.py"], tmp_path_factory.mktemp("portable_scope"))


def _windows_steps() -> tuple[dict, dict, dict]:
    job = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"][
        "cross-platform-full-suite"
    ]
    suites = [step for step in job["steps"] if step.get("name") == "Windows platform contracts"]
    validators = [step for step in job["steps"] if step.get("name") == "Validate Windows platform evidence"]
    uploads = [step for step in job["steps"] if step.get("name") == "Upload Windows platform evidence"]
    assert len(suites) == len(validators) == len(uploads) == 1, "Windows cases need a closed CI evidence gate"
    return suites[0], validators[0], uploads[0]


def test_portable_and_windows_collection_retains_each_platform_case_once(
    portable_nodes: list[str], tmp_path: Path
) -> None:
    suite, _, _ = _windows_steps()
    command = shlex.split(suite["run"].replace("${{ github.run_id }}-${{ github.run_attempt }}", "test"))
    native_command = command[command.index("--") + 1 :]
    assert native_command == [
        "uv",
        "run",
        "--locked",
        "--extra",
        "all",
        "--extra",
        "dev",
        "python",
        "-m",
        "pytest",
        "tests/platform/windows.py",
        "-v",
        "-W",
        "error::DeprecationWarning",
    ]
    windows_nodes = _collect_node_ids(native_command[native_command.index("pytest") + 1 :], tmp_path)
    assert set(windows_nodes) == {f"tests/platform/windows.py::{case}" for case in WINDOWS_CASES}
    combined = portable_nodes + windows_nodes
    assert len(combined) == len(set(combined)), "suite roots must not collect duplicate node IDs"


@pytest.mark.parametrize("condition", ("skipped", "empty", "failed", "passed"))
def test_windows_evidence_validation_fails_closed(condition: str, tmp_path: Path) -> None:
    suite, validator, upload = _windows_steps()
    assert suite.get("if") == "runner.os == 'Windows'"
    assert validator.get("if") == upload.get("if") == "always() && runner.os == 'Windows'"
    assert upload["with"]["name"] == "windows-platform-suite-${{ github.sha }}-${{ github.run_attempt }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "artifacts/suite-status/windows-platform.json"
    suite_arguments = shlex.split(suite["run"].replace("${{ github.run_id }}-${{ github.run_attempt }}", "test"))
    suite_command = suite_arguments[suite_arguments.index("--") + 1 :]
    validator_command = shlex.split(validator["run"].replace("${{ github.sha }}", "a" * 40))
    assert validator_command[validator_command.index("--expected-platform") + 1] == "windows-latest"
    command_fragment = validator_command[validator_command.index("--command-fragment") + 1]
    assert shlex.split(command_fragment) == suite_command
    directory = tmp_path / "suites"
    directory.mkdir()
    report = tmp_path / "report.json"
    validator_command[0] = sys.executable
    validator_command[validator_command.index("--directory") + 1] = str(directory)
    validator_command[validator_command.index("--output") + 1] = str(report)
    validator_command[validator_command.index("--attestation-output") + 1] = str(tmp_path / "status.json")
    candidate = {
        "schema": "aegis-suite-evidence-v1",
        "commit": "a" * 40,
        "status": "PROVEN",
        "release_eligible": True,
        "exit_code": 0,
        "failed": 0,
        "timed_out": False,
        "termination": "EXITED",
        "worktree_status": "CLEAN",
        "command": shlex.join(suite_command),
        "command_argv": suite_command,
        "executed_command_argv": [
            *suite_command,
            "-o",
            "junit_family=xunit2",
            "-o",
            "junit_logging=no",
            "--junitxml",
            str(tmp_path / "windows-platform.xml"),
        ],
        "count_source": "PYTEST_JUNIT_XML",
        "runner_report_sha256": "c" * 64,
        "runner_report_bytes": 256,
        "platform_id": "windows-latest",
        "platform": "Windows-11",
        "toolchain": "Python 3.14.7",
        "discovered": 8,
        "passed": 8,
        "ignored": 0,
        "filtered": 0,
        "skipped": 0,
    }
    if condition == "skipped":
        candidate.update(skipped=1, passed=7)
    elif condition == "empty":
        candidate.update(discovered=0, passed=0)
    elif condition == "failed":
        candidate.update(failed=1, passed=7, exit_code=1, status="FAILED", release_eligible=False)
    (directory / "windows.json").write_text(json.dumps(candidate), encoding="utf-8")
    result = subprocess.run(
        validator_command,
        cwd=ROOT,
        env=dict(os.environ, GITHUB_ACTIONS="true"),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == (0 if condition == "passed" else 1), result.stdout + result.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == ("PROVEN" if condition == "passed" else "FAILED")
    attestation = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert attestation["schema"] == "aegis-cross-platform-lane-status-v1"
    assert attestation["status"] == ("PROVEN" if condition == "passed" else "NOT_VERIFIED")
    assert not {"command", "toolchain", "passed", "discovered", "runner_report_sha256"}.intersection(attestation)
    assert "command" not in result.stdout and "toolchain" not in result.stdout
