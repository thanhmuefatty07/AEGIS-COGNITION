"""Fuzz log sanitizer and crash reproducer isolation tool."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Patterns to redact
RUNNER_PATH_PATTERN = re.compile(r"/home/runner(?:/[^\s\"':]+)+")
WIN_PATH_PATTERN = re.compile(r"[A-Za-z]:(?:[\\/][^\s\"':]+)+")
IPV4_PATTERN = re.compile(r"\b(?!(?:127\.0\.0\.1|0\.0\.0\.0)\b)(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
GH_TOKEN_PATTERN = re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}\b")
AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
CREDENTIAL_PATTERN = re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*([:=])\s*['\"]([^'\"]{8,})['\"]")


def sanitize_text(text: str) -> str:
    """Redact machine paths, IPs, and credentials from log text while preserving backtraces."""
    text = GH_TOKEN_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", text)
    text = AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", text)
    text = CREDENTIAL_PATTERN.sub(r'\1\2"[REDACTED_CREDENTIAL]"', text)
    text = RUNNER_PATH_PATTERN.sub("[REDACTED_RUNNER_PATH]", text)
    text = WIN_PATH_PATTERN.sub("[REDACTED_WIN_PATH]", text)
    text = IPV4_PATTERN.sub("[REDACTED_IP]", text)
    return text


def sanitize_file(path: Path) -> None:
    if not path.is_file():
        return
    raw = path.read_text(encoding="utf-8", errors="replace")
    sanitized = sanitize_text(raw)
    path.write_text(sanitized, encoding="utf-8")


def isolate_crash_reproducers(
    search_dirs: list[Path] | None = None,
    destination: Path | None = None,
) -> list[Path]:
    """Find crash/leak/oom/timeout artifacts and isolate them to .local/tmp/fuzz/."""
    if search_dirs is None:
        search_dirs = [ROOT, ROOT / "fuzz", ROOT / "artifacts" / "fuzz"]
    if destination is None:
        destination = ROOT / ".local" / "tmp" / "fuzz"

    destination.mkdir(parents=True, exist_ok=True)
    isolated: list[Path] = []
    reproducer_patterns = ("crash-*", "leak-*", "timeout-*", "oom-*")

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for pattern in reproducer_patterns:
            for item in directory.glob(pattern):
                if item.is_file() and not item.resolve().is_relative_to(destination.resolve()):
                    target = destination / item.name
                    shutil.move(str(item), str(target))
                    isolated.append(target)

    return isolated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sanitize fuzz logs and isolate reproducers")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "artifacts" / "fuzz", help="Directory containing logs")
    parser.add_argument("--isolate-crashes", action="store_true", help="Isolate crash reproducers to .local/tmp/fuzz/")
    parser.add_argument("files", nargs="*", type=Path, help="Explicit log files to sanitize")

    args = parser.parse_args(argv)

    files_to_sanitize: list[Path] = []
    if args.files:
        files_to_sanitize.extend(args.files)
    elif args.log_dir.is_dir():
        files_to_sanitize.extend(args.log_dir.glob("*.log"))
        files_to_sanitize.extend(args.log_dir.glob("*.stdout"))
        files_to_sanitize.extend(args.log_dir.glob("*.stderr"))

    for log_path in files_to_sanitize:
        sanitize_file(log_path)

    if args.isolate_crashes:
        isolated = isolate_crash_reproducers()
        if isolated:
            print(f"Isolated {len(isolated)} crash reproducer(s) to .local/tmp/fuzz/")

    print(f"Sanitized {len(files_to_sanitize)} log file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
