from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _find_bash() -> str | None:
    if os.name == "nt":
        git = shutil.which("git")
        if git is not None:
            candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("bash")


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return path.as_posix()
    cygpath = shutil.which("cygpath")
    if cygpath is None:
        git = shutil.which("git")
        if git is not None:
            candidate = Path(git).resolve().parent.parent / "usr" / "bin" / "cygpath.exe"
            if candidate.is_file():
                cygpath = str(candidate)
    if cygpath is None:
        pytest.skip("Git Bash path conversion is unavailable")
    result = subprocess.run([cygpath, "-u", str(path)], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write_shell_command(directory: Path, name: str, body: str) -> None:
    command = directory / name
    command.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    command.chmod(0o755)


def test_unix_installer_success_builds_with_project_toolchain_from_external_cwd(tmp_path: Path) -> None:
    bash = _find_bash()
    if bash is None:
        pytest.skip("Bash is unavailable")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    install_root = tmp_path / "install root"
    command_log = tmp_path / "commands.log"
    source_toolchain = REPOSITORY_ROOT / "rust-toolchain.toml"

    _write_shell_command(fake_bin, "uname", 'printf "Linux\\n"')
    _write_shell_command(
        fake_bin,
        "git",
        'printf "git %s\\n" "$*" >> "$WP02_SHIM_LOG"\n'
        'if [[ "$1" == "clone" ]]; then\n'
        '    mkdir -p "$3"\n'
        '    cp "$WP02_SOURCE_TOOLCHAIN" "$3/rust-toolchain.toml"\n'
        '    mkdir -p "$3/.git"\n'
        "fi",
    )
    _write_shell_command(fake_bin, "rustup", 'printf "rustup %s\\n" "$*" >> "$WP02_SHIM_LOG"')
    _write_shell_command(
        fake_bin,
        "rustc",
        'printf "rustc %s\\n" "$*" >> "$WP02_SHIM_LOG"\nprintf "rustc 1.99.1\\n"',
    )
    _write_shell_command(
        fake_bin,
        "cargo",
        'toolchain="$(awk -F \'"\' \'/^[[:space:]]*channel[[:space:]]*=/ { print $2; exit }\' "$PWD/rust-toolchain.toml")"\n'
        'printf "cargo cwd=%s toolchain=%s args=%s\\n" "$PWD" "$toolchain" "$*" >> "$WP02_SHIM_LOG"\n'
        '[[ -f "$PWD/rust-toolchain.toml" ]] || { echo "cargo ran outside the project toolchain root" >&2; exit 92; }\n'
        'manifest=""\n'
        "while (($#)); do\n"
        '    if [[ "$1" == "--manifest-path" ]]; then manifest="$2"; shift 2; else shift; fi\n'
        "done\n"
        'repo="${manifest%/core/rust/Cargo.toml}"\n'
        'mkdir -p "$repo/target/release"\n'
        'printf "#!/usr/bin/env bash\\nexit 0\\n" > "$repo/target/release/aegis-nerve-cli"\n'
        'chmod +x "$repo/target/release/aegis-nerve-cli"',
    )
    _write_shell_command(
        fake_bin,
        "uv",
        'printf "uv %s\\n" "$*" >> "$WP02_SHIM_LOG"\n'
        'if [[ "$1" == "--version" ]]; then echo "uv 0.12.9 (test shim)"; exit 0; fi\n'
        '[[ "$1" == "sync" ]] || exit 91\n'
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        'printf "#!/usr/bin/env bash\\nexit 0\\n" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        "cat > \"$UV_PROJECT_ENVIRONMENT/bin/aegis\" <<'SH'\n"
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        '    version) echo "aegis-cognition v0.1.0"; exit 0 ;;\n'
        '    verify) [[ "$2" == "--help" ]] && exit 0 ;;\n'
        '    run) echo "Usage: aegis run"; exit 2 ;;\n'
        "esac\nexit 90\n"
        "SH\n"
        "cat > \"$UV_PROJECT_ENVIRONMENT/bin/aegis-desktop\" <<'SH'\n"
        "#!/usr/bin/env bash\n"
        '[[ "$1" == "--help" ]] && exit 0\n'
        "exit 90\n"
        "SH\n"
        'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python" "$UV_PROJECT_ENVIRONMENT/bin/aegis" "$UV_PROJECT_ENVIRONMENT/bin/aegis-desktop"',
    )
    _write_shell_command(fake_bin, "sudo", 'printf "sudo %s\\n" "$*" >> "$WP02_SHIM_LOG"')
    _write_shell_command(fake_bin, "curl", 'echo "unexpected curl invocation" >&2; exit 99')

    posix_fake_bin = _bash_path(fake_bin)
    posix_home = _bash_path(home)
    posix_install_root = _bash_path(install_root)
    posix_log = _bash_path(command_log)
    posix_toolchain = _bash_path(source_toolchain)
    posix_installer = _bash_path(REPOSITORY_ROOT / "install.sh")
    path_entries = [posix_fake_bin, "/usr/bin", "/bin", "/mingw64/bin", "/c/Windows/System32"]
    runner = tmp_path / "run-installer.sh"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"export HOME={shlex.quote(posix_home)}",
                f"export WP02_SHIM_LOG={shlex.quote(posix_log)}",
                f"export WP02_SOURCE_TOOLCHAIN={shlex.quote(posix_toolchain)}",
                f"export PATH={shlex.quote(':'.join(path_entries))}",
                f"exec {shlex.quote(posix_installer)} --install-dir {shlex.quote(posix_install_root)} --skip-browser",
                "",
            ]
        ),
        encoding="utf-8",
    )
    runner.chmod(0o755)

    result = subprocess.run(
        [bash, _bash_path(runner)], cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False
    )

    output = result.stdout + result.stderr
    log = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    expected_repo = _bash_path(install_root / "repo")
    assert result.returncode == 0, output
    assert "Installation complete" in output
    assert f"cargo cwd={expected_repo} " in log
    assert "toolchain=1.99.1" in log
    assert "unexpected curl invocation" not in output
    assert (home / ".local" / "bin" / "aegis").is_file()
