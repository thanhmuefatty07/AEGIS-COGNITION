from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sanitize_fuzz_logs  # noqa: E402


def test_sanitize_fuzz_logs_redacts_runner_paths() -> None:
    raw = "Error at /home/runner/work/AEGIS-COGNITION/core/rust/src/parser.rs:42:10\n"
    sanitized = sanitize_fuzz_logs.sanitize_text(raw)
    assert "/home/runner" not in sanitized
    assert "[REDACTED_RUNNER_PATH]" in sanitized
    assert ":42:10" in sanitized


def test_sanitize_fuzz_logs_redacts_windows_paths() -> None:
    raw = r"Stack trace: Z:\runner\work\AEGIS-COGNITION\target\debug\fuzz.exe line 123"
    sanitized = sanitize_fuzz_logs.sanitize_text(raw)
    assert r"Z:\runner\work" not in sanitized
    assert "[REDACTED_WIN_PATH]" in sanitized
    assert "line 123" in sanitized


def test_sanitize_fuzz_logs_redacts_ip_addresses() -> None:
    raw = "Connected to 192.168.1.55:8080 and 10.0.0.12 (loopback 127.0.0.1 untouched)\n"
    sanitized = sanitize_fuzz_logs.sanitize_text(raw)
    assert "192.168.1.55" not in sanitized
    assert "10.0.0.12" not in sanitized
    assert "[REDACTED_IP]" in sanitized
    assert "127.0.0.1" in sanitized  # loopback preserved


def test_sanitize_fuzz_logs_redacts_tokens_and_credentials() -> None:
    gh = "gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789"
    aws = "AKIA" + "0123456789ABCDEF"
    fake_secret = "sk-live-" + "supersecrettokenvalue"
    raw = f'Authorization: Bearer {gh}, key="{aws}", api_' + f'key="{fake_secret}"\n'
    sanitized = sanitize_fuzz_logs.sanitize_text(raw)
    assert gh not in sanitized
    assert aws not in sanitized
    assert fake_secret not in sanitized
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized
    assert "[REDACTED_AWS_KEY]" in sanitized
    assert "[REDACTED_CREDENTIAL]" in sanitized


def test_sanitize_fuzz_logs_preserves_backtraces() -> None:
    backtrace = (
        "thread 'main' panicked at 'index out of bounds: the len is 4 but the index is 4', src/parser.rs:88:9\n"
        "stack backtrace:\n"
        "   0: rust_begin_unwind\n"
        "             at /rustc/abcdef/library/std/src/panicking.rs:593:5\n"
        "   1: core::panicking::panic_fmt\n"
        "             at /rustc/abcdef/library/core/src/panicking.rs:67:14\n"
        "   2: aegis_core::parser::parse_frame\n"
        "   3: LLVMFuzzerTestOneInput\n"
    )
    sanitized = sanitize_fuzz_logs.sanitize_text(backtrace)
    assert "thread 'main' panicked at 'index out of bounds" in sanitized
    assert "stack backtrace:" in sanitized
    assert "aegis_core::parser::parse_frame" in sanitized
    assert "LLVMFuzzerTestOneInput" in sanitized


def test_isolate_crash_reproducers(tmp_path: Path) -> None:
    search_dir = tmp_path / "artifacts" / "fuzz"
    search_dir.mkdir(parents=True)
    crash_file = search_dir / "crash-0123456789abcdef"
    crash_file.write_bytes(b"\x00\xff\xfe\x01malformed_fuzz_payload")

    dest_dir = tmp_path / ".local" / "tmp" / "fuzz"
    isolated = sanitize_fuzz_logs.isolate_crash_reproducers(
        search_dirs=[search_dir],
        destination=dest_dir,
    )

    assert len(isolated) == 1
    assert not crash_file.exists()
    assert (dest_dir / "crash-0123456789abcdef").exists()
    assert (dest_dir / "crash-0123456789abcdef").read_bytes() == b"\x00\xff\xfe\x01malformed_fuzz_payload"
