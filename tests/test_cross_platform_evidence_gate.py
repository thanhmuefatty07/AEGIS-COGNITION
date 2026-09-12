from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.cross_platform_evidence_gate import validate_directory


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
        ({"commit": "b" * 40}, "commit does not match"),
        ({"filtered": 1}, "filtered or ignored"),
        ({"skipped": 1}, "skipped tests are not permitted"),
        ({"command": COMMAND + " --maxfail=1"}, "commands differ"),
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
