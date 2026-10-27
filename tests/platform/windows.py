from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from aegis_cognition.agent_efficiency import DisposableWorktree
from core.python.aegis.code_intelligence import SourceMapper


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_disposable_worktree_rejects_scratch_root_junction_swap_before_cleanup(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions are only available on Windows")

    repository = tmp_path / "project"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    context = DisposableWorktree(repository)
    temporary = context.__enter__()
    scratch_root = context._scratch_root
    assert scratch_root is not None
    outside_leaf = outside / temporary.name
    outside_leaf.mkdir()
    sentinel = outside_leaf / "preserve.txt"
    sentinel.write_text("outside data", encoding="utf-8")
    original_root = scratch_root.with_name("worktrees-original")
    moved_original = False
    created_junction = False

    try:
        scratch_root.rename(original_root)
        moved_original = True
        created = subprocess.run(
            ("cmd.exe", "/c", "mklink", "/J", str(scratch_root), str(outside)),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("Windows junction creation is unavailable")
        created_junction = True

        with pytest.raises(RuntimeError, match="scratch root changed after allocation"):
            context._assert_owned_temporary(temporary, scratch_root)
        assert sentinel.read_text(encoding="utf-8") == "outside data"
    finally:
        if created_junction and scratch_root.is_junction():
            scratch_root.rmdir()
        if moved_original and original_root.exists():
            original_root.rename(scratch_root)
        context.__exit__(None, None, None)

    assert sentinel.read_text(encoding="utf-8") == "outside data"


def test_junctioned_directory_is_not_traversed(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows junctions are only available on Windows")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    (tmp_path / "visible.py").write_text("visible = True\n", encoding="utf-8")
    outside = tmp_path.parent / "junction-target"
    outside.mkdir()
    junction = tmp_path / "linked"
    try:
        (outside / "outside.py").write_text("secret = True\n", encoding="utf-8")
        created = subprocess.run(
            ("cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)),
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if created.returncode != 0:
            pytest.skip("Windows junction creation is unavailable")
        try:
            assert junction.is_junction()
            snapshot = SourceMapper(tmp_path).snapshot()
            assert {record.relative_path for record in snapshot.files} == {"visible.py"}
        finally:
            if junction.is_junction() or junction.exists():
                junction.rmdir()
    finally:
        outside_entry = outside / "outside.py"
        if outside_entry.exists():
            outside_entry.unlink()
        if outside.exists():
            outside.rmdir()


def _write_command(directory: Path, name: str, body: str) -> None:
    (directory / f"{name}.cmd").write_text(f"@echo off\n{body}\n", encoding="ascii")


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer contract runs on Windows")
def test_windows_installer_parses_without_errors() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    environment = os.environ.copy()
    environment["AEGIS_INSTALL_SCRIPT"] = str(REPOSITORY_ROOT / "install.ps1")
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile($env:AEGIS_INSTALL_SCRIPT, "
            "[ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer contract runs on Windows")
def test_native_build_uses_system_command_interpreter_not_comspec(tmp_path: Path) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    fake_bin = tmp_path / "commands"
    fake_bin.mkdir()
    _write_command(fake_bin, "cl", "exit /b 0")
    _write_command(fake_bin, "link", "exit /b 0")
    marker = tmp_path / "trusted-command-ran.txt"
    _write_command(
        fake_bin,
        "probe",
        '>>"%AEGIS_PROBE_MARKER%" echo trusted-command-ran',
    )
    probe = tmp_path / "invoke-vs-native-checked.ps1"
    probe.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        "$env:AEGIS_INSTALL_SCRIPT, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { throw 'installer parser rejected source' }\n"
        "$nativeFunction = $ast.Find({ param($node) $node -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Invoke-NativeChecked' }, $true)\n"
        "$function = $ast.Find({ param($node) $node -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Invoke-VsNativeChecked' }, $true)\n"
        "if ($null -eq $nativeFunction) { throw 'native command helper not found' }\n"
        "if ($null -eq $function) { throw 'native build function not found' }\n"
        ". ([scriptblock]::Create($nativeFunction.Extent.Text))\n"
        ". ([scriptblock]::Create($function.Extent.Text))\n"
        '$env:PATH = "$env:AEGIS_TEST_COMMANDS;$env:SystemRoot\\System32"\n'
        "$env:WindowsSDKVersion = '10.0.26100.0\\'\n"
        "Invoke-VsNativeChecked 'system interpreter probe' "
        "$env:AEGIS_TEST_COMMAND '' $env:TEMP $null '3.14.7'\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment["AEGIS_INSTALL_SCRIPT"] = os.environ.get("AEGIS_INSTALL_SCRIPT", str(REPOSITORY_ROOT / "install.ps1"))
    environment["AEGIS_TEST_COMMANDS"] = str(fake_bin)
    environment["AEGIS_TEST_COMMAND"] = str(fake_bin / "probe.cmd")
    environment["AEGIS_PROBE_MARKER"] = str(marker)
    environment["COMSPEC"] = str(tmp_path / "untrusted-command-interpreter.exe")
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8").strip() == "trusted-command-ran"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer contract runs on Windows")
@pytest.mark.parametrize("failed_stage", ["uv", "rustup", "cargo", "git"])
def test_windows_installer_stops_at_native_exit_seven(tmp_path: Path, failed_stage: str) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    install_root = tmp_path / "install root"
    repository = install_root / "repo"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    for relative_path in ("pyproject.toml", "uv.lock", "rust-toolchain.toml"):
        (repository / relative_path).write_bytes((REPOSITORY_ROOT / relative_path).read_bytes())
    (repository / "requirements.txt").write_text("", encoding="utf-8")

    fake_bin = tmp_path / "commands"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    _write_command(
        fake_bin,
        "uv",
        '>>"%AEGIS_FAKE_COMMAND_LOG%" echo uv %*\n'
        'if /I "%1"=="sync" if /I "%AEGIS_FAIL_STAGE%"=="uv" exit /b 7\n'
        "exit /b 0",
    )
    _write_command(
        fake_bin,
        "git",
        '>>"%AEGIS_FAKE_COMMAND_LOG%" echo git %*\nif /I "%AEGIS_FAIL_STAGE%"=="git" exit /b 7\nexit /b 0',
    )
    _write_command(fake_bin, "winget", '>>"%AEGIS_FAKE_COMMAND_LOG%" echo winget %*\nexit /b 0')
    _write_command(fake_bin, "cl", "exit /b 0")
    _write_command(fake_bin, "link", "exit /b 0")
    _write_command(
        fake_bin,
        "rustup",
        '>>"%AEGIS_FAKE_COMMAND_LOG%" echo rustup %*\nif /I "%AEGIS_FAIL_STAGE%"=="rustup" exit /b 7\nexit /b 0',
    )
    _write_command(fake_bin, "rustc", '>>"%AEGIS_FAKE_COMMAND_LOG%" echo rustc %*\necho rustc 1.99.1\nexit /b 0')
    _write_command(
        fake_bin,
        "cargo",
        '>>"%AEGIS_FAKE_COMMAND_LOG%" echo cargo %*\n'
        'if /I "%AEGIS_FAIL_STAGE%"=="cargo" exit /b 7\n'
        'mkdir "%AEGIS_FAKE_INSTALL_ROOT%\\repo\\target\\release" 2>nul\n'
        'type nul > "%AEGIS_FAKE_INSTALL_ROOT%\\repo\\target\\release\\aegis-nerve-cli.exe"\n'
        "exit /b 0",
    )

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin};{environment.get('PATH', '')}"
    environment["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
    environment["AEGIS_FAKE_COMMAND_LOG"] = str(log_path)
    environment["AEGIS_FAIL_STAGE"] = failed_stage
    environment["AEGIS_FAKE_INSTALL_ROOT"] = str(install_root)
    environment["WindowsSDKVersion"] = "10.0.26100.0\\"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "install.ps1"),
            "-SkipBrowser",
            "-InstallRoot",
            str(install_root),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    output = result.stdout + result.stderr
    command_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert "exit code 7" in output.lower(), output
    assert result.returncode != 0
    assert "Installation complete" not in output
    assert "Locked project wheel and dependencies installed" not in output
    assert not (tmp_path / "local-app-data" / "Microsoft" / "WindowsApps" / "aegis.cmd").exists()

    if failed_stage == "git":
        assert "git pull" in command_log or "git clone" in command_log
        assert "Repo updated" not in output
        assert "Repo cloned" not in output
        assert "rustup toolchain install" not in command_log
        assert "cargo build" not in command_log
        assert "uv sync" not in command_log
    elif failed_stage == "uv":
        assert "uv sync" in command_log
        assert "cargo build" in command_log
        assert "aegis and aegis-desktop entry points passed" not in output
    elif failed_stage == "rustup":
        assert "rustup toolchain install" in command_log
        assert "cargo build" not in command_log
        assert "uv sync" not in command_log
    else:
        assert "rustup toolchain install" in command_log
        assert "cargo build" in command_log
        assert "uv sync" not in command_log


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe path expansion is Windows-specific")
def test_cmd_environment_indirection_preserves_percent_and_metacharacters_in_paths() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the runner's Windows argument mode")

    runner_path = r"C:\workspace\%UNSET_WP02_TOKEN%&echo INJECTED\runner.mjs"
    environment = os.environ.copy()
    environment["AEGIS_SIDECAR_RUNNER"] = runner_path
    probe = r"""
const { spawnSync } = require("node:child_process");
const result = spawnSync(process.env.ComSpec, ["/d", "/s", "/c", 'echo "%AEGIS_SIDECAR_RUNNER%"'], {
  encoding: "utf8",
  windowsVerbatimArguments: true,
});
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
"""
    result = subprocess.run(
        [node, "-e", probe],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f'"{runner_path}"'
