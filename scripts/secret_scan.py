"""Small fail-closed secret scan for tracked and publishable worktree files.

This is a repository hygiene gate, not a substitute for provider-side secret
scanning or incident response. It intentionally reports only the rule, path,
and line number; matched credential values are never printed. Oversized files and
force-added local agent/runtime state fail closed instead of being skipped.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
PRIVATE_WORKTREE_ROOTS = frozenset(
    {".agents", ".aegis", ".codex", ".cursorrules", ".cursor", ".local", ".serena", ".sixth"}
)

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


SYNTHETIC_ALLOWLIST_PATHS: frozenset[str] = frozenset(
    {
        "tests/test_provider_contract_matrix.py",
        "tests/test_secret_scan.py",
    }
)

SYNTHETIC_EXPLICIT_MARKERS: tuple[str, ...] = (
    "AKIA" + "IOSFODNN7EXAMPLE",
    "sk-ant-offline-test",
    "sk-or-offline-test",
    "nvapi-offline-test",
)


def _normalize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized.removeprefix("./")


def _is_synthetic_fixture_match(relative_path: str | None, matched_text: str) -> bool:
    """Verify that a credential pattern match is strictly inside an authorized fixture path with an explicit synthetic marker."""
    if relative_path is None:
        return False
    normalized = _normalize_relative_path(relative_path)
    if normalized not in SYNTHETIC_ALLOWLIST_PATHS:
        return False
    return any(marker in matched_text for marker in SYNTHETIC_EXPLICIT_MARKERS)


def _repository_paths(root: Path, arguments: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", *arguments, "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [raw for raw in result.stdout.decode().split("\0") if raw]


def _repository_files(root: Path, arguments: list[str]) -> list[Path]:
    names = _repository_paths(root, arguments)
    repository_root = root.resolve()
    files: list[Path] = []
    for raw in names:
        path = repository_root / Path(raw)
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise OSError("repository path could not be safely resolved") from error
        try:
            resolved.relative_to(repository_root)
        except ValueError:
            continue
        if path.is_file():
            files.append(path)
    return files


def tracked_files(root: Path = ROOT) -> list[Path]:
    """Return tracked worktree files that resolve inside the repository."""

    return _repository_files(root, [])


def untracked_files(root: Path = ROOT) -> list[Path]:
    """Return non-ignored untracked files intended for a possible Git add."""

    return _repository_files(root, ["--others", "--exclude-standard"])


def staged_files(root: Path = ROOT) -> list[str]:
    """Return changed paths whose staged blobs need an index-level scan."""

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [raw for raw in result.stdout.decode().split("\0") if raw]


def scan_file(path: Path, root: Path = ROOT) -> list[tuple[str, int]]:
    try:
        relative_path = path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        relative_path = path.as_posix()
    try:
        with path.open("rb") as source:
            data = source.read(MAX_TEXT_BYTES + 1)
    except OSError:
        return [("unreadable_file", 0)]
    return scan_bytes(data, relative_path=relative_path)


def scan_bytes(data: bytes, relative_path: str | None = None) -> list[tuple[str, int]]:
    if len(data) > MAX_TEXT_BYTES:
        return [("file_too_large", 0)]
    texts = [data.decode("latin-1")]
    encoding: str | None = None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif b"\0" in data:
        even_nuls = sum(byte == 0 for byte in data[::2])
        odd_nuls = sum(byte == 0 for byte in data[1::2])
        pair_count = len(data) // 2
        threshold = max(1, pair_count // 4)
        if even_nuls >= threshold and even_nuls > odd_nuls * 2:
            encoding = "utf-16-be"
        elif odd_nuls >= threshold and odd_nuls > even_nuls * 2:
            encoding = "utf-16-le"
    if encoding is not None:
        texts.append(data.decode(encoding, errors="replace"))

    findings: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for text in texts:
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule_name, pattern in RULES:
                finding = (rule_name, line_number)
                if finding in seen:
                    continue
                for match in pattern.finditer(line):
                    matched_text = match.group(0)
                    if _is_synthetic_fixture_match(relative_path, matched_text):
                        continue
                    findings.append(finding)
                    seen.add(finding)
                    break
    return findings


def scan_staged_file(relative_path: str, root: Path = ROOT) -> list[tuple[str, int]]:
    """Scan the blob currently in the Git index, without printing its value."""

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["git", "show", f":{relative_path}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if process.stdout is None:
            process.kill()
            process.wait()
            return [("unreadable_staged_file", 0)]
        data = process.stdout.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()
            return [("file_too_large", 0)]
        process.stdout.close()
        return_code = process.wait()
    except OSError:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()
        return [("unreadable_staged_file", 0)]
    if return_code != 0:
        return [("unreadable_staged_file", 0)]
    return scan_bytes(data, relative_path=relative_path)


def private_worktree_paths(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        normalized = path.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if any(part.casefold() in PRIVATE_WORKTREE_ROOTS for part in parts):
            findings.append(normalized)
    return list(dict.fromkeys(findings))


def main() -> int:
    try:
        live_files = tracked_files(ROOT)
        untracked = untracked_files(ROOT)
        tracked_paths = _repository_paths(ROOT, [])
    except OSError:
        print("secret scan failed: repository path could not be safely resolved")
        return 1
    staged = staged_files(ROOT)
    findings = [(path, rule, line) for path in (*live_files, *untracked) for rule, line in scan_file(path)]
    findings.extend(
        (ROOT / Path(relative_path), rule, line)
        for relative_path in staged
        for rule, line in scan_staged_file(relative_path, ROOT)
    )
    private_path_names = private_worktree_paths(
        [
            *tracked_paths,
            *(path.relative_to(ROOT.resolve()).as_posix() for path in untracked),
            *staged,
        ]
    )
    findings.extend((ROOT / Path(path), "private_local_path", 0) for path in private_path_names)
    if findings:
        for path, rule, line in findings:
            print(f"secret scan failed: {rule} at {path.relative_to(ROOT)}:{line}")
        return 1
    print(
        f"secret scan passed: {len(live_files)} tracked and {len(untracked)} non-ignored "
        f"untracked worktree files, plus {len(staged)} staged blobs inspected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
