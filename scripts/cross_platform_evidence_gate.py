"""Fail closed unless one identical suite passes on every Tier-1 OS.

The native test runner remains authoritative.  This gate only validates the
runner evidence artifacts emitted by ``suite_evidence.py`` on the
Ubuntu, Windows, and macOS hosted runners.  It never merges partial results,
silences a failed lane, or treats a missing runner as success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import re
import shlex
import sys
from pathlib import Path
from typing import Any, cast

if __package__:
    from .suite_evidence import suite_execution_valid
else:
    from suite_evidence import suite_execution_valid


SUITE_SCHEMA = "aegis-suite-evidence-v1"
REPORT_SCHEMA = "aegis-cross-platform-suite-evidence-v1"
LANE_ATTESTATION_SCHEMA = "aegis-cross-platform-lane-status-v1"
STATUS_REPORT_SCHEMA = "aegis-cross-platform-status-v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_FAMILY = {
    "ubuntu-latest": "linux",
    "windows-latest": "windows",
    "macos-14": "macos",
}
COUNT_FIELDS = ("discovered", "passed", "failed", "ignored", "filtered", "skipped")
ATTESTATION_BASE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "commit",
        "platform_id",
        "platform_family",
        "failure_codes",
        "independent_verification",
    }
)
ATTESTATION_DIGEST_FIELDS = frozenset({"command_sha256", "toolchain_sha256", "count_vector_sha256"})
ATTESTATION_FAILURE_CODES = frozenset(
    {"INVALID_OR_UNEXPECTED_SUITE", "LANE_VALIDATION_FAILED", "MISSING_OR_DUPLICATE_SUITE"}
)


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


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _claim_scope() -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted":
        return "GITHUB_HOSTED_RUNNERS"
    return "LOCAL_CHECKOUT_ONLY"


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
    if type(candidate.get("discovered")) is not int or candidate["discovered"] <= 0:
        errors.append("suite discovered zero tests")
    if candidate.get("timed_out") is not False or candidate.get("termination") != "EXITED":
        errors.append("suite timed out or did not terminate normally")
    if candidate.get("worktree_status") != "CLEAN":
        errors.append("suite was not run from a clean worktree")
    try:
        expected_argv = shlex.split(command_fragment, comments=False)
    except ValueError:
        expected_argv = []
    if not expected_argv or candidate.get("command_argv") != expected_argv or not suite_execution_valid(candidate):
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
    if all(type(candidate.get(field)) is int and candidate[field] >= 0 for field in COUNT_FIELDS):
        if candidate["passed"] <= 0:
            errors.append("suite has no passing tests")
        if candidate["discovered"] != sum(candidate[field] for field in ("passed", "failed", "ignored", "skipped")):
            errors.append("suite test counts are inconsistent")
    if candidate.get("filtered") != 0 or candidate.get("ignored") != 0:
        errors.append("filtered or ignored tests are not permitted")
    if candidate.get("skipped") != 0:
        errors.append("skipped tests are not permitted")
    return errors


def create_lane_attestation(
    candidate: dict[str, Any], expected_commit: str, expected_platform: str, command_fragment: str
) -> dict[str, Any]:
    """Reduce one private suite report to the fields needed by the cross-OS gate.

    Comparison digests preserve parity checks; they are not confidentiality controls.
    """

    errors = _valid_suite(candidate, expected_commit, command_fragment)
    expected_family = EXPECTED_FAMILY.get(expected_platform)
    observed_family = _family(candidate.get("platform"))
    if candidate.get("platform_id") != expected_platform:
        errors.append("platform id does not match the requested lane")
    if expected_family is None or observed_family != expected_family:
        errors.append("observed platform does not match the requested lane")

    attestation: dict[str, Any] = {
        "schema": LANE_ATTESTATION_SCHEMA,
        "status": "PROVEN" if not errors else "NOT_VERIFIED",
        "commit": expected_commit,
        "platform_id": expected_platform,
        "platform_family": expected_family or "unknown",
        "failure_codes": [] if not errors else ["LANE_VALIDATION_FAILED"],
        "independent_verification": "NOT VERIFIED",
    }
    if not errors:
        expected_argv = shlex.split(command_fragment, comments=False)
        attestation.update(
            {
                "command_sha256": _digest(expected_argv),
                "toolchain_sha256": hashlib.sha256(candidate["toolchain"].encode("utf-8")).hexdigest(),
                "count_vector_sha256": _digest([candidate[field] for field in COUNT_FIELDS]),
            }
        )
    return attestation


def _attest_directory(
    directory: Path, expected_commit: str, expected_platform: str, command_fragment: str
) -> dict[str, Any]:
    if not directory.is_dir():
        return create_lane_attestation({}, expected_commit, expected_platform, command_fragment)
    candidates: list[dict[str, Any]] = []
    invalid_inputs = False
    for path in sorted(directory.rglob("*.json")):
        try:
            candidate = _load(path)
        except OSError, ValueError, json.JSONDecodeError:
            invalid_inputs = True
            continue
        if candidate.get("schema") != SUITE_SCHEMA or candidate.get("platform_id") != expected_platform:
            invalid_inputs = True
            continue
        candidates.append(candidate)
    if invalid_inputs:
        return {
            "schema": LANE_ATTESTATION_SCHEMA,
            "status": "NOT_VERIFIED",
            "commit": expected_commit,
            "platform_id": expected_platform,
            "platform_family": EXPECTED_FAMILY.get(expected_platform, "unknown"),
            "failure_codes": ["INVALID_OR_UNEXPECTED_SUITE"],
            "independent_verification": "NOT VERIFIED",
        }
    if len(candidates) != 1:
        return {
            "schema": LANE_ATTESTATION_SCHEMA,
            "status": "NOT_VERIFIED",
            "commit": expected_commit,
            "platform_id": expected_platform,
            "platform_family": EXPECTED_FAMILY.get(expected_platform, "unknown"),
            "failure_codes": ["MISSING_OR_DUPLICATE_SUITE"],
            "independent_verification": "NOT VERIFIED",
        }
    return create_lane_attestation(candidates[0], expected_commit, expected_platform, command_fragment)


def _validate_attestations(
    directory: Path,
    expected_commit: str,
    expected_platforms: tuple[str, ...],
    command_fragment: str,
) -> dict[str, Any]:
    expected = tuple(dict.fromkeys(item.strip() for item in expected_platforms if item.strip()))
    if not SHA_RE.fullmatch(expected_commit) or not expected:
        return {
            "schema": STATUS_REPORT_SCHEMA,
            "status": "FAILED",
            "expected_platforms": list(expected),
            "verified_platforms": [],
            "failure_codes": ["INVALID_GATE_INPUT"],
            "independent_verification": "NOT VERIFIED",
        }
    try:
        expected_argv = shlex.split(command_fragment, comments=False)
    except ValueError:
        expected_argv = []
    if not expected_argv:
        return {
            "schema": STATUS_REPORT_SCHEMA,
            "status": "FAILED",
            "commit": expected_commit,
            "expected_platforms": list(expected),
            "verified_platforms": [],
            "failure_codes": ["INVALID_GATE_INPUT"],
            "independent_verification": "NOT VERIFIED",
        }
    paths = sorted(directory.rglob("*.json"))
    candidates: dict[str, dict[str, Any]] = {}
    failure_codes: set[str] = set()
    for path in paths:
        try:
            candidate = _load(path)
        except OSError, ValueError, json.JSONDecodeError:
            failure_codes.add("INVALID_ATTESTATION")
            continue
        if candidate.get("schema") != LANE_ATTESTATION_SCHEMA:
            failure_codes.add("MIXED_OR_INVALID_EVIDENCE")
            continue
        candidate_fields = set(candidate)
        if (
            not ATTESTATION_BASE_FIELDS.issubset(candidate_fields)
            or candidate_fields - ATTESTATION_BASE_FIELDS - ATTESTATION_DIGEST_FIELDS
        ):
            failure_codes.add("INVALID_ATTESTATION_SCHEMA")
            continue
        expected_fields = (
            ATTESTATION_BASE_FIELDS | ATTESTATION_DIGEST_FIELDS
            if candidate.get("status") == "PROVEN"
            else ATTESTATION_BASE_FIELDS
        )
        if candidate_fields != expected_fields:
            failure_codes.add("INVALID_ATTESTATION_SCHEMA")
            continue
        if candidate.get("independent_verification") != "NOT VERIFIED":
            failure_codes.add("INVALID_ATTESTATION_SCHEMA")
            continue
        codes = candidate.get("failure_codes")
        if not isinstance(codes, list) or any(
            not isinstance(code, str) or code not in ATTESTATION_FAILURE_CODES for code in codes
        ):
            failure_codes.add("INVALID_ATTESTATION_SCHEMA")
            continue
        platform_id = candidate.get("platform_id")
        if not isinstance(platform_id, str) or platform_id not in expected:
            failure_codes.add("UNEXPECTED_PLATFORM")
            continue
        if platform_id in candidates:
            failure_codes.add("DUPLICATE_PLATFORM")
            continue
        candidates[platform_id] = candidate

    if set(candidates) != set(expected):
        failure_codes.add("PLATFORM_SET_MISMATCH")

    expected_command_hash = _digest(expected_argv)
    accepted: dict[str, dict[str, Any]] = {}
    for platform_id in expected:
        candidate = candidates.get(platform_id)
        if candidate is None:
            continue
        lane_valid = True
        if candidate.get("status") != "PROVEN" or candidate.get("failure_codes") != []:
            failure_codes.add("LANE_NOT_PROVEN")
            lane_valid = False
        if candidate.get("commit") != expected_commit:
            failure_codes.add("COMMIT_MISMATCH")
            lane_valid = False
        expected_family = EXPECTED_FAMILY.get(platform_id)
        if expected_family is None or candidate.get("platform_family") != expected_family:
            failure_codes.add("PLATFORM_MISMATCH")
            lane_valid = False
        if candidate.get("command_sha256") != expected_command_hash:
            failure_codes.add("COMMAND_MISMATCH")
            lane_valid = False
        for field in ("toolchain_sha256", "count_vector_sha256"):
            if not isinstance(candidate.get(field), str) or not SHA256_RE.fullmatch(candidate[field]):
                failure_codes.add("INVALID_COMPARISON_DIGEST")
                lane_valid = False
        if lane_valid:
            accepted[platform_id] = candidate

    for field, code in (
        ("toolchain_sha256", "TOOLCHAIN_MISMATCH"),
        ("count_vector_sha256", "TEST_RESULT_PARITY_MISMATCH"),
    ):
        values = {candidate[field] for candidate in accepted.values() if isinstance(candidate.get(field), str)}
        if values and len(values) != 1:
            failure_codes.add(code)

    verified = sorted(platform_id for platform_id in accepted if not failure_codes)
    status = "PROVEN" if not failure_codes and set(accepted) == set(expected) else "FAILED"
    return {
        "schema": STATUS_REPORT_SCHEMA,
        "status": status,
        "commit": expected_commit,
        "expected_platforms": list(expected),
        "verified_platforms": verified,
        "failure_codes": sorted(failure_codes),
        "independent_verification": "NOT VERIFIED",
    }


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema") == STATUS_REPORT_SCHEMA:
        return report
    errors = report.get("errors")
    failure_codes = ["CROSS_PLATFORM_VALIDATION_FAILED"] if isinstance(errors, list) and errors else []
    lanes = report.get("lanes")
    verified = (
        sorted(
            lane["platform_id"]
            for lane in lanes
            if isinstance(lane, dict) and lane.get("status") == "PROVEN" and not lane.get("errors")
        )
        if isinstance(lanes, list)
        else []
    )
    return {
        "schema": STATUS_REPORT_SCHEMA,
        "status": report.get("status", "FAILED"),
        "commit": report.get("commit"),
        "expected_platforms": report.get("expected_platforms", []),
        "verified_platforms": verified,
        "failure_codes": failure_codes,
        "independent_verification": "NOT VERIFIED",
    }


def _public_attestation_status(attestation: dict[str, Any]) -> dict[str, Any]:
    platform_id = attestation.get("platform_id")
    status = attestation.get("status") == "PROVEN" and attestation.get("failure_codes") == []
    failure_codes = attestation.get("failure_codes")
    return {
        "schema": STATUS_REPORT_SCHEMA,
        "status": "PROVEN" if status else "FAILED",
        "commit": attestation.get("commit"),
        "expected_platforms": [platform_id] if isinstance(platform_id, str) else [],
        "verified_platforms": [platform_id] if status and isinstance(platform_id, str) else [],
        "failure_codes": failure_codes if isinstance(failure_codes, list) else ["LANE_VALIDATION_FAILED"],
        "independent_verification": "NOT VERIFIED",
    }


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
    has_attestations = False
    for path in paths:
        try:
            if _load(path).get("schema") == LANE_ATTESTATION_SCHEMA:
                has_attestations = True
                break
        except OSError, ValueError, json.JSONDecodeError:
            continue
    if has_attestations:
        return _validate_attestations(directory, expected_commit, expected_platforms, command_fragment)
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
            lane_errors.append(f"platform observation {candidate.get('platform')!r} does not match {platform_id}")
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
                "command_argv": candidate.get("command_argv"),
                "count_source": candidate.get("count_source"),
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
        "claim_scope": _claim_scope(),
        "independent_verification": "NOT VERIFIED",
        "commit": expected_commit,
        "expected_platforms": list(expected),
        "command_fragment": command_fragment,
        "expected_command_argv": shlex.split(command_fragment, comments=False),
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
    parser.add_argument("--command-fragment", required=True, help="exact required command, including all arguments")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--attestation-output", type=Path, help="write a sanitized single-lane attestation")
    parser.add_argument("--require-attestations", action="store_true")
    args = parser.parse_args()
    input_directory = args.directory.resolve()
    for option, output_path in (
        ("--output", args.output),
        ("--attestation-output", args.attestation_output),
    ):
        if output_path is not None and output_path.resolve().is_relative_to(input_directory):
            parser.error(f"{option} must be outside --directory so it cannot be read as input")
    if (
        args.output is not None
        and args.attestation_output is not None
        and args.output.resolve() == args.attestation_output.resolve()
    ):
        parser.error("--output and --attestation-output must use different files")
    if args.attestation_output is not None and len(args.expected_platforms) != 1:
        parser.error("--attestation-output requires exactly one --expected-platform")
    if args.require_attestations:
        if args.attestation_output is not None:
            parser.error("--require-attestations cannot be combined with --attestation-output")
        report = _validate_attestations(
            args.directory,
            args.commit,
            tuple(args.expected_platforms),
            args.command_fragment,
        )
    else:
        report = validate_directory(
            args.directory,
            args.commit,
            tuple(args.expected_platforms),
            args.command_fragment,
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    attestation = None
    if args.attestation_output is not None:
        attestation = _attest_directory(
            args.directory,
            args.commit,
            args.expected_platforms[0],
            args.command_fragment,
        )
        if report.get("status") != "PROVEN" and attestation.get("status") == "PROVEN":
            attestation = {
                **attestation,
                "status": "NOT_VERIFIED",
                "failure_codes": ["LANE_VALIDATION_FAILED"],
            }
        args.attestation_output.parent.mkdir(parents=True, exist_ok=True)
        args.attestation_output.write_text(json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        printable = _public_attestation_status(attestation) if attestation is not None else _public_report(report)
    else:
        printable = report
    print(json.dumps(printable, indent=2, sort_keys=True))
    status = attestation.get("status") if attestation is not None else report.get("status")
    if status != "PROVEN":
        print("cross-platform evidence gate: FAILED", file=sys.stderr)
        return 1
    print("cross-platform evidence gate: PROVEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
