"""Tests for the repository secret scanner."""

from __future__ import annotations

import subprocess
import tracemalloc
from pathlib import Path

import pytest

from scripts import secret_scan


@pytest.mark.parametrize(
    "synthetic_fixture",
    (
        b'API_KEY = "test-sk-test-key-abcdefghijklmnopqrstuvwxyz"',
        b'API_TOKEN = "test-only-api-token-placeholder"',
        b'OPENAI_API_KEY = "test-only-openai-api-key-placeholder"',
    ),
)
def test_secret_scan_accepts_known_synthetic_credentials(synthetic_fixture: bytes):
    assert secret_scan.scan_bytes(synthetic_fixture) == []


def test_secret_scan_reports_real_shaped_values_without_echoing_untracked_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    gitignore = repository / ".gitignore"
    gitignore.write_text(".env\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", ".gitignore"], check=True, capture_output=True)
    canary = "candidate" + "X" * 24
    untracked_source = repository / "pending_source.py"
    untracked_source.write_text(f'API_KEY = "{canary}"\n', encoding="utf-8")
    ignored_env = repository / ".env"
    ignored_env.write_text(f'API_KEY = "{canary}"\n', encoding="utf-8")
    monkeypatch.setattr(secret_scan, "ROOT", repository)

    exit_code = secret_scan.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "credential_assignment at pending_source.py:1" in output
    assert "credential_assignment at .env" not in output
    assert canary not in output


def test_secret_scanner_positive_controls_still_match_rule_shapes():
    gh_token = "gh" + "p_" + "A" * 32
    api_assignment = b'API_KEY = "' + b"A" * 24 + b'"'

    assert secret_scan.scan_bytes(gh_token.encode("ascii")) == [("github_token", 1)]
    assert secret_scan.scan_bytes(api_assignment) == [("credential_assignment", 1)]


def test_secret_scan_checks_binary_content_for_secret_shapes() -> None:
    gh_token = b"ghp_" + b"A" * 32

    assert secret_scan.scan_bytes(b"\0" + gh_token) == [("github_token", 1)]


@pytest.mark.parametrize("private_root", (".local", ".LOCAL", ".Agents", ".Aegis"))
def test_secret_scan_rejects_private_worktree_paths_case_insensitively(private_root: str) -> None:
    path = f"{private_root}/private-evidence.json"

    assert secret_scan.private_worktree_paths([path]) == [path]


@pytest.mark.parametrize("encoding", ("utf-16-le", "utf-16-be"))
@pytest.mark.parametrize("with_bom", (False, True))
def test_secret_scan_checks_utf16_content_for_secret_shapes(encoding: str, with_bom: bool) -> None:
    token = "ghp_" + "A" * 32
    payload = token.encode(encoding)
    if with_bom:
        payload = (b"\xff\xfe" if encoding == "utf-16-le" else b"\xfe\xff") + payload

    assert secret_scan.scan_bytes(payload) == [("github_token", 1)]


def test_secret_scan_covers_force_added_ignored_private_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    (repository / ".gitignore").write_text(".local/\n", encoding="utf-8")
    private_directory = repository / ".local"
    private_directory.mkdir()
    private_file = private_directory / "private.bin"
    private_file.write_bytes(b"\0ghp_" + b"A" * 32)
    (private_directory / "private-evidence.json").write_text('{"status":"local"}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", ".gitignore"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "add", "-f", ".local"], check=True, capture_output=True)
    monkeypatch.setattr(secret_scan, "ROOT", repository)

    exit_code = secret_scan.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "github_token at .local/private.bin:1" in output.replace("\\", "/")
    assert "private_local_path at .local/private-evidence.json:0" in output.replace("\\", "/")
    assert "A" * 32 not in output


@pytest.mark.parametrize(
    "private_path",
    (
        ".codex/session.json",
        ".cursorrules",
        "core/python/.agents/session.json",
    ),
)
def test_secret_scan_rejects_force_added_local_agent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private_path: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    (repository / ".gitignore").write_text(".codex/\n.cursorrules\n.agents/\n", encoding="utf-8")
    target = repository / private_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"synthetic local agent state\n")
    subprocess.run(["git", "-C", str(repository), "add", ".gitignore"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "add", "-f", private_path], check=True, capture_output=True)
    monkeypatch.setattr(secret_scan, "ROOT", repository)

    exit_code = secret_scan.main()
    output = capsys.readouterr().out.replace("\\", "/")

    assert exit_code == 1
    assert f"private_local_path at {private_path}:0" in output
    assert "synthetic local agent state" not in output


def test_secret_scan_fails_closed_within_memory_limit_for_large_file(tmp_path: Path) -> None:
    path = tmp_path / "large-untracked.bin"
    path.write_bytes(b"x" * (secret_scan.MAX_TEXT_BYTES * 4))

    tracemalloc.start()
    try:
        findings = secret_scan.scan_file(path)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert findings == [("file_too_large", 0)]
    assert peak_bytes < secret_scan.MAX_TEXT_BYTES * 2


def test_secret_scan_fails_closed_for_large_staged_blob(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    path = repository / "large.bin"
    path.write_bytes(b"x" * (secret_scan.MAX_TEXT_BYTES * 2))
    subprocess.run(["git", "-C", str(repository), "add", "large.bin"], check=True, capture_output=True)

    assert secret_scan.scan_staged_file("large.bin", repository) == [("file_too_large", 0)]


def test_secret_scan_fails_closed_when_repository_file_cannot_be_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    source = repository / "unreadable.py"
    source.write_text("VALUE = 'safe fixture'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "unreadable.py"], check=True, capture_output=True)
    resolve = Path.resolve

    def reject_path(candidate: Path, *args, **kwargs):
        if candidate == source:
            raise PermissionError("fixture access denied")
        return resolve(candidate, *args, **kwargs)

    monkeypatch.setattr(secret_scan, "ROOT", repository)
    monkeypatch.setattr(Path, "resolve", reject_path)

    exit_code = secret_scan.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "repository path could not be safely resolved" in output


def test_secret_scan_allows_synthetic_offline_tokens_in_exempt_fixture_paths() -> None:
    fixture_path = "tests/test_provider_contract_matrix.py"
    aws_mock = b'AWS_KEY = "' + b"AKIA" + b'IOSFODNN7EXAMPLE"\n'
    ant_mock = b'API_KEY = "sk-ant-offline-test-123456789012345"\n'
    or_mock = b'API_KEY = "sk-or-offline-test-123456789012345"\n'

    assert secret_scan.scan_bytes(aws_mock, relative_path=fixture_path) == []
    assert secret_scan.scan_bytes(ant_mock, relative_path=fixture_path) == []
    assert secret_scan.scan_bytes(or_mock, relative_path=fixture_path) == []


def test_secret_scan_negative_regression_blocks_real_secrets_in_fixture_paths() -> None:
    fixture_path = "tests/test_provider_contract_matrix.py"
    real_aws = b'AWS_KEY = "' + b"AKIA" + b'1234567890ABCDEF"\n'
    real_gh = b"gh" + b"p_123456789012345678901234567890\n"
    real_cred = b"API_" + b'KEY = "sk-live-12345678901234567890"\n'

    assert secret_scan.scan_bytes(real_aws, relative_path=fixture_path) == [("aws_access_key", 1)]
    assert secret_scan.scan_bytes(real_gh, relative_path=fixture_path) == [("github_token", 1)]
    assert secret_scan.scan_bytes(real_cred, relative_path=fixture_path) == [("credential_assignment", 1)]


def test_secret_scan_blocks_synthetic_tokens_in_production_source() -> None:
    prod_path = "aegis_cognition/agent.py"
    aws_mock = b'AWS_KEY = "' + b"AKIA" + b'IOSFODNN7EXAMPLE"\n'
    ant_mock = b"API_" + b'KEY = "sk-ant-offline-test-123456789012345"\n'

    assert secret_scan.scan_bytes(aws_mock, relative_path=prod_path) == [("aws_access_key", 1)]
    assert secret_scan.scan_bytes(ant_mock, relative_path=prod_path) == [("credential_assignment", 1)]


def test_secret_scan_tamper_resistance_cannot_be_disabled_by_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Verify that ambient env vars cannot disable the scanner or suppress findings
    monkeypatch.setenv("SKIP_SECRET_SCAN", "1")
    monkeypatch.setenv("DISABLE_SECRET_SCAN", "1")
    monkeypatch.setenv("AEGIS_DISABLE_SECRET_SCAN", "true")
    monkeypatch.setenv("SECRET_SCAN_OVERRIDE", "all")

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    source = repository / "leaked.py"
    secret_val = "real-production-" + "secret-abcdef123456"
    source.write_text(f'API_KEY = "{secret_val}"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "leaked.py"], check=True, capture_output=True)
    monkeypatch.setattr(secret_scan, "ROOT", repository)

    exit_code = secret_scan.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "credential_assignment at leaked.py:1" in output


def test_secret_scan_detects_real_key_with_marker_comment_on_same_line() -> None:
    fixture_path = "tests/test_provider_contract_matrix.py"
    # Real-format key followed by a synthetic marker word in a comment on the same line
    real_key_with_comment = b"API_" + b'KEY = "sk-live-12345678901234567890"  # mock_test sk-ant-offline-test\n'
    assert secret_scan.scan_bytes(real_key_with_comment, relative_path=fixture_path) == [("credential_assignment", 1)]


def test_secret_scan_detects_real_key_when_sharing_line_with_synthetic_key() -> None:
    fixture_path = "tests/test_provider_contract_matrix.py"
    # Two keys on one line: first is synthetic mock, second is real-format key
    line = b'MOCK = "' + b"AKIA" + b'IOSFODNN7EXAMPLE"  REAL = "' + b"AKIA" + b'1234567890ABCDEF"\n'
    assert secret_scan.scan_bytes(line, relative_path=fixture_path) == [("aws_access_key", 1)]


def test_secret_scan_detects_synthetic_marker_in_unallowed_file() -> None:
    unallowed_path = "aegis_cognition/providers.py"
    synthetic_key = b"API_" + b'KEY = "sk-ant-offline-test-123456789012345"\n'
    assert secret_scan.scan_bytes(synthetic_key, relative_path=unallowed_path) == [("credential_assignment", 1)]


def test_secret_scan_staged_file_respects_exempt_fixture_allowlist(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    tests_dir = repository / "tests"
    tests_dir.mkdir()
    exempt_fixture = tests_dir / "test_provider_contract_matrix.py"
    exempt_fixture.write_bytes(b'API_KEY = "sk-ant-offline-test-123456789012345"\n')
    non_exempt = repository / "production.py"
    non_exempt.write_bytes(b'API_KEY = "sk-ant-offline-test-123456789012345"\n')
    subprocess.run(
        ["git", "-C", str(repository), "add", "tests/test_provider_contract_matrix.py", "production.py"],
        check=True,
        capture_output=True,
    )

    assert secret_scan.scan_staged_file("tests/test_provider_contract_matrix.py", repository) == []
    assert secret_scan.scan_staged_file("production.py", repository) == [("credential_assignment", 1)]
