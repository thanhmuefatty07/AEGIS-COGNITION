from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.cross_platform_evidence_gate import (
    LANE_ATTESTATION_SCHEMA,
    _validate_attestations,
    create_lane_attestation,
    validate_directory,
)


COMMIT = "a" * 40
COMMAND = "uv run --locked --extra all --extra dev python -m pytest tests core/python/tests.py -v -W error::DeprecationWarning"


def _write_lane(root: Path, platform_id: str, observed_platform: str, **overrides: object) -> None:
    lane = {
        "schema": "aegis-suite-evidence-v1",
        "commit": COMMIT,
        "status": "PROVEN",
        "release_eligible": True,
        "exit_code": 0,
        "failed": 0,
        "timed_out": False,
        "termination": "EXITED",
        "worktree_status": "CLEAN",
        "command": COMMAND,
        "command_argv": shlex.split(COMMAND),
        "executed_command_argv": [
            *shlex.split(COMMAND),
            "-o",
            "junit_family=xunit2",
            "-o",
            "junit_logging=no",
            "--junitxml",
            "artifacts/suites/pytest-fixture/junit.xml",
        ],
        "count_source": "PYTEST_JUNIT_XML",
        "runner_report_sha256": "c" * 64,
        "runner_report_bytes": 256,
        "platform_id": platform_id,
        "platform": observed_platform,
        "toolchain": "Python 3.14.7",
        "discovered": 10,
        "passed": 10,
        "ignored": 0,
        "filtered": 0,
        "skipped": 0,
    }
    lane.update(overrides)
    (root / f"{platform_id}.json").write_text(json.dumps(lane), encoding="utf-8")


def _write_all(root: Path) -> None:
    _write_lane(root, "ubuntu-latest", "Linux-6.8")
    _write_lane(root, "windows-latest", "Windows-11")
    _write_lane(root, "macos-14", "macOS-14.0")


def test_validate_directory_requires_all_three_platforms(tmp_path: Path) -> None:
    _write_all(tmp_path)
    report = validate_directory(tmp_path, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)
    assert report["status"] == "PROVEN"
    assert report["errors"] == []


def test_lane_attestation_contains_only_cross_platform_comparison_status(tmp_path: Path) -> None:
    _write_lane(tmp_path, "windows-latest", "Windows-11")
    candidate = json.loads((tmp_path / "windows-latest.json").read_text(encoding="utf-8"))

    attestation = create_lane_attestation(candidate, COMMIT, "windows-latest", COMMAND)

    assert attestation["schema"] == LANE_ATTESTATION_SCHEMA
    assert attestation["status"] == "PROVEN"
    assert attestation["commit"] == COMMIT
    assert attestation["platform_id"] == "windows-latest"
    assert attestation["failure_codes"] == []
    assert set(attestation) == {
        "schema",
        "status",
        "commit",
        "platform_id",
        "platform_family",
        "failure_codes",
        "independent_verification",
        "command_sha256",
        "toolchain_sha256",
        "count_vector_sha256",
    }
    assert not {"command", "toolchain", "discovered", "passed", "runner_report_sha256"}.intersection(attestation)


@pytest.mark.parametrize(
    ("invalid_sibling", "failure_code"),
    [
        ("malformed-json", "INVALID_OR_UNEXPECTED_SUITE"),
        ("unexpected-schema", "INVALID_OR_UNEXPECTED_SUITE"),
        ("wrong-platform", "INVALID_OR_UNEXPECTED_SUITE"),
        ("duplicate-suite", "MISSING_OR_DUPLICATE_SUITE"),
    ],
)
def test_lane_attestation_rejects_invalid_json_siblings(
    tmp_path: Path, invalid_sibling: str, failure_code: str
) -> None:
    _write_lane(tmp_path, "windows-latest", "Windows-11")
    sibling = tmp_path / "additional.json"
    if invalid_sibling == "malformed-json":
        sibling.write_text("{not-json", encoding="utf-8")
    elif invalid_sibling == "duplicate-suite":
        candidate = json.loads((tmp_path / "windows-latest.json").read_text(encoding="utf-8"))
        sibling.write_text(json.dumps(candidate), encoding="utf-8")
    else:
        candidate = json.loads((tmp_path / "windows-latest.json").read_text(encoding="utf-8"))
        if invalid_sibling == "unexpected-schema":
            candidate["schema"] = "unrecognized-evidence-v1"
        else:
            candidate["platform_id"] = "ubuntu-latest"
        sibling.write_text(json.dumps(candidate), encoding="utf-8")

    status_path = tmp_path.parent / f"{tmp_path.name}-status.json"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "cross_platform_evidence_gate.py"),
            "--directory",
            str(tmp_path),
            "--commit",
            COMMIT,
            "--expected-platform",
            "windows-latest",
            "--command-fragment",
            COMMAND,
            "--attestation-output",
            str(status_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    attestation = json.loads(status_path.read_text(encoding="utf-8"))
    assert attestation["status"] == "NOT_VERIFIED"
    assert attestation["failure_codes"] == [failure_code]

    aggregate_dir = tmp_path / "aggregate"
    aggregate_dir.mkdir()
    (aggregate_dir / "windows-latest.json").write_text(json.dumps(attestation), encoding="utf-8")
    aggregate = validate_directory(aggregate_dir, COMMIT, ("windows-latest",), COMMAND)
    assert aggregate["status"] == "FAILED"
    assert "INVALID_ATTESTATION_SCHEMA" not in aggregate["failure_codes"]


def test_attested_cross_platform_gate_preserves_parity_checks_without_raw_reports(tmp_path: Path) -> None:
    private_reports = tmp_path / "private"
    public_status = tmp_path / "status"
    private_reports.mkdir()
    public_status.mkdir()
    _write_all(private_reports)
    for platform_id in ("ubuntu-latest", "windows-latest", "macos-14"):
        candidate = json.loads((private_reports / f"{platform_id}.json").read_text(encoding="utf-8"))
        attestation = create_lane_attestation(candidate, COMMIT, platform_id, COMMAND)
        (public_status / f"{platform_id}.json").write_text(json.dumps(attestation), encoding="utf-8")

    report = validate_directory(public_status, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)

    assert report["status"] == "PROVEN"
    assert report["verified_platforms"] == ["macos-14", "ubuntu-latest", "windows-latest"]
    assert report["failure_codes"] == []
    assert "lanes" not in report
    assert "errors" not in report

    cli_output = tmp_path / "public-status.json"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "cross_platform_evidence_gate.py"),
            "--directory",
            str(public_status),
            "--commit",
            COMMIT,
            "--expected-platform",
            "ubuntu-latest",
            "--expected-platform",
            "windows-latest",
            "--expected-platform",
            "macos-14",
            "--command-fragment",
            COMMAND,
            "--require-attestations",
            "--output",
            str(cli_output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, GITHUB_ACTIONS="true"),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cli_status = json.loads(cli_output.read_text(encoding="utf-8"))
    assert cli_status["status"] == "PROVEN"
    assert "lanes" not in cli_status and "errors" not in cli_status
    assert "toolchain_sha256" not in result.stdout and "count_vector_sha256" not in result.stdout


@pytest.mark.parametrize("mismatch", ["toolchain", "count-vector"], ids=["toolchain-parity", "test-result-parity"])
def test_attested_cross_platform_gate_rejects_mismatched_comparators(tmp_path: Path, mismatch: str) -> None:
    _write_all(tmp_path)
    attestations = tmp_path / "attestations"
    attestations.mkdir()
    for platform_id in ("ubuntu-latest", "windows-latest", "macos-14"):
        candidate = json.loads((tmp_path / f"{platform_id}.json").read_text(encoding="utf-8"))
        if platform_id == "windows-latest" and mismatch == "toolchain":
            candidate["toolchain"] = "Python 3.14.8"
        if platform_id == "windows-latest" and mismatch == "count-vector":
            candidate["discovered"] = candidate["passed"] = 9
        attestation = create_lane_attestation(candidate, COMMIT, platform_id, COMMAND)
        (attestations / f"{platform_id}.json").write_text(json.dumps(attestation), encoding="utf-8")

    report = validate_directory(attestations, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)

    assert report["status"] == "FAILED"
    expected_code = "TOOLCHAIN_MISMATCH" if mismatch == "toolchain" else "TEST_RESULT_PARITY_MISMATCH"
    assert expected_code in report["failure_codes"]


def test_ci_attestation_mode_rejects_raw_suite_reports(tmp_path: Path) -> None:
    raw_reports = tmp_path / "raw-reports"
    raw_reports.mkdir()
    _write_all(raw_reports)
    output = tmp_path / "status.json"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "cross_platform_evidence_gate.py"),
            "--directory",
            str(raw_reports),
            "--commit",
            COMMIT,
            "--expected-platform",
            "ubuntu-latest",
            "--expected-platform",
            "windows-latest",
            "--expected-platform",
            "macos-14",
            "--command-fragment",
            COMMAND,
            "--require-attestations",
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, GITHUB_ACTIONS="true"),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 1
    status = json.loads(output.read_text(encoding="utf-8"))
    assert status["schema"] == "aegis-cross-platform-status-v1"
    assert status["status"] == "FAILED"
    assert "MIXED_OR_INVALID_EVIDENCE" in status["failure_codes"]
    assert "lanes" not in status and "errors" not in status
    assert "toolchain" not in result.stdout and "runner_report_sha256" not in result.stdout


def test_attestation_schema_rejects_added_raw_fields(tmp_path: Path) -> None:
    attestations = tmp_path / "attestations"
    attestations.mkdir()
    for platform_id, observed_platform in (
        ("ubuntu-latest", "Linux-6.8"),
        ("windows-latest", "Windows-11"),
        ("macos-14", "macOS-14.0"),
    ):
        _write_lane(tmp_path, platform_id, observed_platform)
        candidate = json.loads((tmp_path / f"{platform_id}.json").read_text(encoding="utf-8"))
        attestation = create_lane_attestation(candidate, COMMIT, platform_id, COMMAND)
        if platform_id == "windows-latest":
            attestation["command"] = candidate["command"]
        (attestations / f"{platform_id}.json").write_text(json.dumps(attestation), encoding="utf-8")

    report = validate_directory(attestations, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)

    assert report["status"] == "FAILED"
    assert "INVALID_ATTESTATION_SCHEMA" in report["failure_codes"]


@pytest.mark.parametrize(
    "expected_commit,command_fragment",
    [("invalid", COMMAND), (COMMIT, "")],
    ids=["invalid-commit", "empty-command"],
)
def test_attestation_gate_fails_closed_on_invalid_gate_input(
    tmp_path: Path, expected_commit: str, command_fragment: str
) -> None:
    report = _validate_attestations(
        tmp_path,
        expected_commit,
        ("ubuntu-latest", "windows-latest", "macos-14"),
        command_fragment,
    )

    assert report["schema"] == "aegis-cross-platform-status-v1"
    assert report["status"] == "FAILED"
    assert report["failure_codes"] == ["INVALID_GATE_INPUT"]


@pytest.mark.parametrize("invalid_code", [["nested"], {"nested": "code"}], ids=["array", "object"])
def test_attestation_gate_rejects_non_string_failure_codes(tmp_path: Path, invalid_code: object) -> None:
    source = tmp_path / "private"
    attestations = tmp_path / "attestations"
    source.mkdir()
    attestations.mkdir()
    _write_all(source)
    for platform_id in ("ubuntu-latest", "windows-latest", "macos-14"):
        candidate = json.loads((source / f"{platform_id}.json").read_text(encoding="utf-8"))
        attestation = create_lane_attestation(candidate, COMMIT, platform_id, COMMAND)
        if platform_id == "windows-latest":
            attestation["failure_codes"] = [invalid_code]
        (attestations / f"{platform_id}.json").write_text(json.dumps(attestation), encoding="utf-8")

    report = _validate_attestations(attestations, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)

    assert report["status"] == "FAILED"
    assert "INVALID_ATTESTATION_SCHEMA" in report["failure_codes"]


def test_cli_rejects_output_inside_its_input_directory(tmp_path: Path) -> None:
    input_directory = tmp_path / "attestations"
    input_directory.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "cross_platform_evidence_gate.py"),
            "--directory",
            str(input_directory),
            "--commit",
            COMMIT,
            "--expected-platform",
            "windows-latest",
            "--command-fragment",
            COMMAND,
            "--require-attestations",
            "--output",
            str(input_directory / "evidence.json"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 2
    assert "must be outside --directory" in result.stderr


def test_cli_rejects_identical_report_and_attestation_outputs(tmp_path: Path) -> None:
    input_directory = tmp_path / "attestations"
    input_directory.mkdir()
    output_path = tmp_path / "status.json"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "cross_platform_evidence_gate.py"),
            "--directory",
            str(input_directory),
            "--commit",
            COMMIT,
            "--expected-platform",
            "windows-latest",
            "--command-fragment",
            COMMAND,
            "--output",
            str(output_path),
            "--attestation-output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 2
    assert "must use different files" in result.stderr


@pytest.mark.parametrize(
    "platforms,display_only",
    [
        (("ubuntu-latest", "windows-latest", "macos-14"), False),
        (("windows-latest",), False),
        (("ubuntu-latest", "windows-latest", "macos-14"), True),
    ],
    ids=["all-lanes-noop", "windows-noop", "disagreeing-argv"],
)
def test_full_suite_command_cannot_be_an_inert_argument(
    tmp_path: Path, platforms: tuple[str, ...], display_only: bool
) -> None:
    noop = ["python", "-c", "print('10 passed')", COMMAND]
    observed = {"ubuntu-latest": "Linux-6.8", "windows-latest": "Windows-11", "macos-14": "macOS-14.0"}
    for platform_id in platforms:
        _write_lane(
            tmp_path,
            platform_id,
            observed[platform_id],
            command=COMMAND if display_only else shlex.join(noop),
            command_argv=noop,
        )

    report = validate_directory(tmp_path, COMMIT, platforms, COMMAND)

    assert report["status"] == "FAILED"
    assert any("required full-suite command" in error for error in report["errors"])


def test_validate_directory_rejects_missing_platform(tmp_path: Path) -> None:
    _write_all(tmp_path)
    (tmp_path / "macos-14.json").unlink()
    report = validate_directory(tmp_path, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)
    assert report["status"] == "FAILED"
    assert any("platform set mismatch" in error for error in report["errors"])


@pytest.mark.parametrize(
    "overrides,needle",
    [
        ({"failed": 1, "exit_code": 1, "status": "FAILED", "release_eligible": False}, "non-zero exit"),
        ({"discovered": 0, "passed": 0}, "zero tests"),
        ({"passed": 0}, "suite has no passing tests"),
        ({"discovered": 11}, "suite test counts are inconsistent"),
        ({"commit": "b" * 40}, "commit does not match"),
        ({"filtered": 1}, "filtered or ignored"),
        ({"skipped": 1}, "skipped tests are not permitted"),
        ({"command": COMMAND + " --maxfail=1"}, "required full-suite command"),
        ({"toolchain": "Python 3.15.0"}, "toolchains differ"),
    ],
)
def test_validate_directory_rejects_unproven_or_inconsistent_lane(
    tmp_path: Path, overrides: dict[str, object], needle: str
) -> None:
    _write_all(tmp_path)
    _write_lane(tmp_path, "windows-latest", "Windows-11", **overrides)
    report = validate_directory(tmp_path, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)
    assert report["status"] == "FAILED"
    assert any(needle in error for error in report["errors"])


def test_validate_directory_rejects_mislabeled_host(tmp_path: Path) -> None:
    _write_all(tmp_path)
    _write_lane(tmp_path, "macos-14", "Linux-6.8")
    report = validate_directory(tmp_path, COMMIT, ("ubuntu-latest", "windows-latest", "macos-14"), COMMAND)
    assert report["status"] == "FAILED"
    assert any("does not match macos-14" in error for error in report["errors"])
