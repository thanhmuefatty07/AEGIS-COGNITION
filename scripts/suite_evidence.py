"""Run a test suite and retain normalized, scope-aware metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path


COUNT_PATTERNS = {
    "passed": re.compile(r"(?P<count>\d+)\s+passed\b"),
    "failed": re.compile(r"(?P<count>\d+)\s+failed\b"),
    "ignored": re.compile(r"(?P<count>\d+)\s+ignored\b"),
    "filtered": re.compile(r"(?P<count>\d+)\s+filtered(?:\s+out)?\b"),
    "skipped": re.compile(r"(?P<count>\d+)\s+skipped\b"),
}
DISCOVERED_RE = re.compile(r"(?P<count>\d+)\s+(?:tests?|cases?)\s+(?:run|collected)\b")


def parse_counts(output: str) -> dict[str, int | None]:
    counts: dict[str, int | None] = {name: 0 for name in COUNT_PATTERNS}
    for name, pattern in COUNT_PATTERNS.items():
        matches = [int(match.group("count")) for match in pattern.finditer(output)]
        if matches:
            counts[name] = max(matches)
    discovered_match = DISCOVERED_RE.search(output)
    if discovered_match:
        discovered: int | None = int(discovered_match.group("count"))
    else:
        discovered = sum(int(counts[name] or 0) for name in ("passed", "failed", "ignored", "skipped"))
    counts["discovered"] = discovered
    return counts


def toolchain(command: list[str]) -> str:
    executable = command[0].lower() if command else ""
    if "cargo" in executable:
        probe = ["rustc", "--version"]
    elif executable == "uv" and "python" in command:
        python_index = command.index("python")
        probe = [*command[: python_index + 1], "--version"]
    else:
        probe = ["python", "--version"]
    result = subprocess.run(probe, capture_output=True, text=True, check=False)
    return (result.stdout or result.stderr).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _git_remote_observation(revision: str) -> dict[str, object]:
    """Report only what the local git ref cache can establish.

    This deliberately does not call the network.  A cached origin ref is not
    an independent GitHub observation, so the scope is explicit in the JSON.
    """
    try:
        remote_head = subprocess.check_output(
            ["git", "rev-parse", "origin/main"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        contains = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "origin/main"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, subprocess.CalledProcessError):
        remote_head = ""
        contains = False
    return {
        "ref": "origin/main",
        "head": remote_head,
        "contains_commit": contains,
        "observation_scope": "LOCAL_GIT_REF_CACHE_ONLY",
        "independent_verification": "NOT VERIFIED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("a command is required after --")
    started = datetime.now(UTC)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    finished = datetime.now(UTC)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    output = stdout + stderr
    counts = parse_counts(output)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = {
        "schema": "aegis-suite-evidence-v1",
        "name": args.name,
        "command": shlex.join(command),
        "commit": revision,
        "timestamp_utc": finished.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "duration_seconds": (finished - started).total_seconds(),
        "platform": platform.platform(),
        "toolchain": toolchain(command),
        "discovered": counts["discovered"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "ignored": counts["ignored"],
        "filtered": counts["filtered"],
        "skipped": counts["skipped"],
        "exit_code": completed.returncode,
        "status": "PROVEN" if completed.returncode == 0 else "FAILED",
        "claim_scope": "LOCAL_CHECKOUT_ONLY",
        "claim_label": "LOCALLY PROVEN" if completed.returncode == 0 else "LOCAL FAILED",
        "independent_verification": "NOT VERIFIED",
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr),
        "combined_output_sha256": _sha256(output),
        "remote_observation": _git_remote_observation(revision),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output, end="")
    print(json.dumps(result, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
