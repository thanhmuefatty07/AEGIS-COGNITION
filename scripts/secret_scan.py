"""Small fail-closed secret scan for tracked text files.

This is a repository hygiene gate, not a substitute for provider-side secret
scanning or incident response. It intentionally reports only the rule, path,
and line number; matched credential values are never printed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*"
            r"['\"](?!your[-_]|test[-_]|dummy|example|placeholder|<|\$\{)"
            r"[A-Za-z0-9_./+=-]{20,}['\"]"
        ),
    ),
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    # A worktree can contain deleted or renamed index entries before staging.
    # There are no bytes to inspect for those paths; the staged scan below
    # validates the final index contents before a commit.
    return [
        ROOT / Path(raw)
        for raw in result.stdout.decode().split("\0")
        if raw and (ROOT / Path(raw)).is_file()
    ]


def staged_files() -> list[str]:
    """Return changed paths whose staged blobs need an index-level scan."""

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [raw for raw in result.stdout.decode().split("\0") if raw]


def scan_file(path: Path) -> list[tuple[str, int]]:
    try:
        data = path.read_bytes()
    except OSError:
        return [("unreadable_file", 0)]
    return scan_bytes(data)


def scan_bytes(data: bytes) -> list[tuple[str, int]]:
    if len(data) > MAX_TEXT_BYTES or b"\0" in data:
        return []
    text = data.decode("utf-8", errors="replace")
    findings: list[tuple[str, int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_name, pattern in RULES:
            if pattern.search(line):
                findings.append((rule_name, line_number))
    return findings


def scan_staged_file(relative_path: str) -> list[tuple[str, int]]:
    """Scan the blob currently in the Git index, without printing its value."""

    try:
        result = subprocess.run(
            ["git", "show", f":{relative_path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [("unreadable_staged_file", 0)]
    return scan_bytes(result.stdout)


def main() -> int:
    live_files = tracked_files()
    staged = staged_files()
    findings = [
        (path, rule, line)
        for path in live_files
        for rule, line in scan_file(path)
    ]
    findings.extend(
        (ROOT / relative_path, rule, line)
        for relative_path in staged
        for rule, line in scan_staged_file(relative_path)
    )
    if findings:
        for path, rule, line in findings:
            print(f"secret scan failed: {rule} at {path.relative_to(ROOT)}:{line}")
        return 1
    print(
        f"secret scan passed: {len(live_files)} tracked worktree files and "
        f"{len(staged)} staged blobs inspected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
