"""Fail closed unless one identical suite passes on every Tier-1 OS.

The native test runner remains authoritative.  This gate only validates the
independent evidence artifacts emitted by ``suite_evidence.py`` on the
Ubuntu, Windows, and macOS hosted runners.  It never merges partial results,
silences a failed lane, or treats a missing runner as success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform as host_platform
import re
import sys
from pathlib import Path
from typing import Any, cast


SUITE_SCHEMA = "aegis-suite-evidence-v1"
REPORT_SCHEMA = "aegis-cross-platform-suite-evidence-v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_FAMILY = {
    "ubuntu-latest": "linux",
    "windows-latest": "windows",
    "macos-14": "macos",
}
COUNT_FIELDS = ("discovered", "passed", "failed", "ignored", "filtered", "skipped")


def _load(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], value)


def _family(value: object) -> str:
    text = str(value).lower()
    if "windows" in text:
        return "windows"
    if "darwin" in text or "macos" in text or "mac os" in text:
        return "macos"
    if "linux" in text:
        return "linux"
    return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_suite(candidate: dict[str, Any], expected_commit: str, command_fragment: str) -> list[str]:
    errors: list[str] = []
    if candidate.get("schema") != SUITE_SCHEMA:
        errors.append("schema is not aegis-suite-evidence-v1")
    if candidate.get("commit") != expected_commit:
        errors.append("commit does not match the checked-out commit")
    if candidate.get("status") != "PROVEN" or candidate.get("release_eligible") is not True:
        errors.append("suite is not PROVEN and release-eligible")
    if candidate.get("exit_code") != 0 or candidate.get("failed") != 0:
        errors.append("suite has a non-zero exit or failed tests")
    if candidate.get("timed_out") is not False or candidate.get("termination") != "EXITED":
        errors.append("suite timed out or did not terminate normally")
    if candidate.get("worktree_status") != "CLEAN":
        errors.append("suite was not run from a clean worktree")
    command = candidate.get("command")
    if not isinstance(command, str) or command_fragment not in command:
        errors.append("suite command does not match the required full-suite command")
    if not isinstance(candidate.get("toolchain"), str) or not candidate["toolchain"].strip():
        errors.append("toolchain is missing")
    platform_id = candidate.get("platform_id")
    if not isinstance(platform_id, str) or not platform_id.strip():
        errors.append("platform_id is missing")
    errors.extend(
        f"{field} is not a non-negative integer"
        for field in COUNT_FIELDS
        if type(candidate.get(field)) is not int or candidate[field] < 0
    )
    if candidate.get("filtered") != 0 or candidate.get("ignored") != 0:
        errors.append("filtered or ignored tests are not permitted")
    return errors


def validate_directory(
    directory: Path,
    expected_commit: str,
    expected_platforms: tuple[str, ...],
    command_fragment: str,
) -> dict[str, Any]:
    """Validate exactly one eligible artifact per expected hosted runner."""

    if not SHA_RE.fullmatch(expected_commit):
        raise ValueError("expected commit must be a 40-character lowercase git SHA")
    if not directory.is_dir():
        raise ValueError(f"evidence directory does not exist: {directory}")
    paths = sorted(directory.rglob("*.json"))
    candidates: dict[str, tuple[Path, dict[str, Any]]] = {}
    errors: list[str] = []
    for path in paths:
        try:
            candidate = _load(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{path.name}: unreadable JSON: {error}")
            continue
        if candidate.get("schema") != SUITE_SCHEMA:
            continue
        platform_id = candidate.get("platform_id")
        if not isinstance(platform_id, str) or not platform_id.strip():
            errors.append(f"{path.name}: missing platform_id")
            continue
        platform_id = platform_id.strip()
        if platform_id in candidates:
            errors.append(f"duplicate suite evidence for {platform_id}")
            continue
        candidates[platform_id] = (path, candidate)

    expected = tuple(dict.fromkeys(item.strip() for item in expected_platforms if item.strip()))
    if set(candidates) != set(expected):
        errors.append(
            "platform set mismatch: "
            f"missing={sorted(set(expected) - set(candidates))}, "
            f"unexpected={sorted(set(candidates) - set(expected))}"
        )

    count_vectors: dict[str, tuple[int, ...]] = {}
    commands: dict[str, str] = {}
    toolchains: dict[str, str] = {}
    lane_reports: list[dict[str, Any]] = []
    for platform_id in expected:
        pair = candidates.get(platform_id)
        if pair is None:
            continue
        path, candidate = pair
        lane_errors = _valid_suite(candidate, expected_commit, command_fragment)
        expected_family = EXPECTED_FAMILY.get(platform_id)
        observed_family = _family(candidate.get("platform"))
        if expected_family is None:
            lane_errors.append(f"unsupported expected platform label: {platform_id}")
        elif observed_family != expected_family:
            lane_errors.append(
                f"platform observation {candidate.get('platform')!r} does not match {platform_id}"
            )
        if not lane_errors:
            count_vectors[platform_id] = tuple(int(candidate[field]) for field in COUNT_FIELDS)
            commands[platform_id] = str(candidate["command"])
            toolchains[platform_id] = str(candidate["toolchain"])
        errors.extend(f"{platform_id}: {error}" for error in lane_errors)
        lane_reports.append(
            {
                "platform_id": platform_id,
                "source": path.name,
                "source_sha256": _sha256(path),
                "platform": candidate.get("platform"),
                "toolchain": candidate.get("toolchain"),
                "command": candidate.get("command"),
                "counts": {field: candidate.get(field) for field in COUNT_FIELDS},
                "status": candidate.get("status"),
                "errors": lane_errors,
            }
        )

    if count_vectors:
        unique_vectors = {vector for vector in count_vectors.values()}
        if len(unique_vectors) != 1:
            errors.append("suite count vectors differ between operating systems")
    if commands and len(set(commands.values())) != 1:
        errors.append("suite commands differ between operating systems")
    if toolchains and len(set(toolchains.values())) != 1:
        errors.append("toolchains differ between operating systems")

    return {
        "schema": REPORT_SCHEMA,
        "status": "PROVEN" if not errors else "FAILED",
        "claim_scope": "GITHUB_HOSTED_RUNNERS",
        "independent_verification": "NOT VERIFIED",
        "commit": expected_commit,
        "expected_platforms": list(expected),
        "command_fragment": command_fragment,
        "lanes": lane_reports,
        "errors": sorted(set(errors)),
        "validator": "scripts/cross_platform_evidence_gate.py",
        "validator_platform": host_platform.platform(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--expected-platform", action="append", dest="expected_platforms", required=True)
    parser.add_argument("--command-fragment", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_directory(
        args.directory,
        args.commit,
        tuple(args.expected_platforms),
        args.command_fragment,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["errors"]:
        print("cross-platform evidence gate: FAILED", file=sys.stderr)
        return 1
    print("cross-platform evidence gate: PROVEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
