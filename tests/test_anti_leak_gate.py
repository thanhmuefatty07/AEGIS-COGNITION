"""Tests for the anti-leak path and workflow artifact fast gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import anti_leak_gate


def test_anti_leak_gate_detects_local_paths_and_private_ips() -> None:
    win_sample = (r"C:" + r"\Users" + r"\TESTUSER\secret" + "\n").encode("utf-8")
    mac_sample = b"/Users/" + b"sampleuser/repo\n"
    home_sample = b"/home/" + b"alice/repo\n"
    archive_sample = (r"D:" + r"\AEGIS-COGNITION" + ".local-" + "private" + r"\snap\n").encode("utf-8")
    brain_sample = (".gemini" + "/antigravity" + "/brain/id\n").encode("utf-8")
    tailscale_sample = b"100.64." + b"0.42:2222\n"
    lan_sample = b"192.168." + b"50.10:8080\n"

    assert anti_leak_gate.scan_bytes(win_sample, relative_path="aegis_cognition/agent.py") == [("windows_user_path", 1)]
    assert anti_leak_gate.scan_bytes(mac_sample, relative_path="aegis_cognition/agent.py") == [("unix_user_path", 1)]
    assert anti_leak_gate.scan_bytes(home_sample, relative_path="aegis_cognition/agent.py") == [("unix_user_path", 1)]
    assert anti_leak_gate.scan_bytes(archive_sample, relative_path="aegis_cognition/agent.py") == [
        ("private_archive_path", 1)
    ]
    assert anti_leak_gate.scan_bytes(brain_sample, relative_path="aegis_cognition/agent.py") == [
        ("antigravity_brain_path", 1)
    ]
    assert anti_leak_gate.scan_bytes(tailscale_sample, relative_path="aegis_cognition/agent.py") == [
        ("tailscale_cgnat_ip", 1)
    ]
    assert anti_leak_gate.scan_bytes(lan_sample, relative_path="aegis_cognition/agent.py") == [("rfc1918_192_ip", 1)]


def test_anti_leak_gate_allows_github_runner_path_and_exempt_fixture() -> None:
    runner_sample = b"/home/runner/work/AEGIS-COGNITION/core/rust/src/lib.rs\n"
    fixture_sample = b'raw = "' + b"192.168." + b'1.55:8080"\n'

    assert anti_leak_gate.scan_bytes(runner_sample, relative_path="scripts/sanitize_fuzz_logs.py") == []
    assert anti_leak_gate.scan_bytes(fixture_sample, relative_path="tests/test_sanitize_fuzz_logs.py") == []


def test_anti_leak_gate_rejects_forbidden_upload_artifact_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True, capture_output=True)
    wf = repo / ".github" / "workflows" / "bad.yml"
    wf.write_text(
        """name: Bad
jobs:
  bench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: raw-bench
          path: artifacts/private/raw.json
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", ".github/workflows/bad.yml"], check=True, capture_output=True)

    code, errors = anti_leak_gate.run_gate(repo)

    assert code == 1
    assert any("forbidden_upload_artifact:artifacts/private/raw.json" in err for err in errors)
