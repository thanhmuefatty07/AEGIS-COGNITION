"""Fail-closed anti-leak gate for tracked files and GitHub Actions artifacts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
_ARCHIVE_SUFFIX = ".local-" + "private"

LEAK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("windows_user_path", re.compile(r"(?i)[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+")),
    (
        "private_archive_path",
        re.compile(
            r"(?i)(?:[A-Za-z]:[\\/]+AEGIS-COGNITION"
            + re.escape(_ARCHIVE_SUFFIX)
            + r"|"
            + re.escape(_ARCHIVE_SUFFIX)
            + r")\b"
        ),
    ),
    ("unix_user_path", re.compile(r"/(?:Users/|home/(?!runner\b))[A-Za-z0-9._-]+")),
    ("antigravity_brain_path", re.compile(r"(?i)\.gemini[\\/]+antigravity")),
    (
        "host_machine_name",
        re.compile(r"\bDESKTOP-(?!SERVICE\b)[A-Z0-9]{6,}\b|[A-Za-z0-9._-]+-MacBook-Pro\b"),
    ),
    (
        "tailscale_cgnat_ip",
        re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
    ),
    ("rfc1918_192_ip", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")),
)

SYNTHETIC_ALLOWLIST_PATHS: frozenset[str] = frozenset(
    {
        "tests/test_anti_leak_gate.py",
        "tests/test_browser_adapters.py",
        "tests/test_mcp_http_network_policy.py",
        "tests/test_sanitize_fuzz_logs.py",
    }
)

SYNTHETIC_EXPLICIT_MARKERS: tuple[str, ...] = (
    "192.168." + "1.55",
    "100.64." + "0.1",
    "synthetic-test-only",
)

FORBIDDEN_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "artifacts/private",
    "artifacts/benchmarks",
    "artifacts/suites",
    "artifacts/evidence",
    "target/criterion",
    "fuzz/corpus",
    "fuzz/artifacts",
)

FORBIDDEN_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|/)\*\.console\.log$"),
    re.compile(r"\.console\.log$"),
    re.compile(r"\.tar\.gz$"),
    re.compile(r"(?:^|/)memory-context-.*\.json$"),
    re.compile(r"(?:^|/)mmap-stream-buffer-.*\.json$"),
    re.compile(r"(?:^|/)hot-buffer-ffi-.*\.json$"),
    re.compile(r"(?:^|/)artifacts/rust-coverage\.lcov$"),
)


def _normalize_relative_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _is_synthetic_fixture_match(relative_path: str | None, line: str) -> bool:
    if relative_path is None:
        return False
    normalized = _normalize_relative_path(relative_path)
    if normalized not in SYNTHETIC_ALLOWLIST_PATHS:
        return False
    return any(marker in line for marker in SYNTHETIC_EXPLICIT_MARKERS)


def scan_bytes(data: bytes, relative_path: str | None = None) -> list[tuple[str, int]]:
    """Return rule-name and 1-based line findings without echoing matched text."""

    if len(data) > MAX_TEXT_BYTES:
        return [("file_too_large", 0)]
    if b"\x00" in data[:4096]:
        return []
    text = data.decode("utf-8", errors="replace")
    findings: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_name, pattern in LEAK_RULES:
            finding = (rule_name, line_number)
            if finding in seen:
                continue
            if pattern.search(line):
                if _is_synthetic_fixture_match(relative_path, line):
                    continue
                findings.append(finding)
                seen.add(finding)
    return findings


def _repository_paths(root: Path, extra_args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", *extra_args, "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [raw for raw in result.stdout.decode("utf-8", errors="replace").split("\0") if raw]


def _scan_workflow_uploads(root: Path) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return violations
    workflow_files = sorted(
        (*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")),
        key=lambda item: item.name,
    )
    for workflow_path in workflow_files:
        doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for job_id, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                uses = str(step.get("uses", ""))
                if not uses.startswith("actions/upload-artifact@"):
                    continue
                raw_paths = str((step.get("with") or {}).get("path", ""))
                for raw_entry in raw_paths.splitlines():
                    normalized = raw_entry.strip().replace("\\", "/")
                    if not normalized:
                        continue
                    rel_wf = workflow_path.relative_to(root).as_posix()
                    if any(
                        normalized == prefix or normalized.startswith(f"{prefix}/")
                        for prefix in FORBIDDEN_ARTIFACT_PREFIXES
                    ) or any(pattern.search(normalized) for pattern in FORBIDDEN_ARTIFACT_PATTERNS):
                        violations.append((f"{rel_wf}:{job_id}", f"forbidden_upload_artifact:{normalized}"))
    return violations


def run_gate(root: Path = ROOT) -> tuple[int, list[str]]:
    """Run tracked/untracked path leak and workflow upload-artifact checks."""

    repo_root = root.resolve()
    tracked = _repository_paths(repo_root, [])
    untracked = _repository_paths(repo_root, ["--others", "--exclude-standard"])
    candidate_paths = list(dict.fromkeys([*tracked, *untracked]))

    errors: list[str] = []
    for rel_path in candidate_paths:
        path = repo_root / Path(rel_path)
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(repo_root)
        except OSError, ValueError:
            errors.append(f"unresolvable_path at {rel_path}:0")
            continue
        try:
            data = resolved.read_bytes()
        except OSError:
            errors.append(f"unreadable_file at {rel_path}:0")
            continue
        for rule_name, line_number in scan_bytes(data, relative_path=rel_path):
            errors.append(f"{rule_name} at {rel_path}:{line_number}")

    for location, reason in _scan_workflow_uploads(repo_root):
        errors.append(f"{reason} at {location}")

    return (1 if errors else 0), errors


def main() -> int:
    code, errors = run_gate(ROOT)
    if code != 0:
        for entry in errors:
            print(f"anti-leak gate failed: {entry}")
        return 1
    print("anti-leak gate passed: 0 leaked local/LAN/Tailscale paths or forbidden workflow artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
