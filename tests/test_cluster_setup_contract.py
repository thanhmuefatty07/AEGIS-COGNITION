from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SETUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_cluster_test.sh"
READINESS_PROBE = "import socket;s=socket.socket();s.connect(('127.0.0.1',9000));s.close()"


def _bash_executable() -> str | None:
    candidates: list[Path] = []
    discovered = shutil.which("bash")
    if discovered is not None:
        candidates.append(Path(discovered))
    git = shutil.which("git")
    if git is not None:
        git_root = Path(git).resolve().parents[1]
        candidates.extend((git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            result = subprocess.run(
                (str(candidate), "--version"),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            continue
        if result.returncode == 0 and result.stdout.startswith("GNU bash"):
            return str(candidate)
    return None


def _run_setup(
    tmp_path: Path,
    *,
    failed_worker: str | None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bash = _bash_executable()
    if bash is None:
        pytest.skip("GNU Bash is unavailable")

    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    docker = bin_directory / "docker"
    docker_log = tmp_path / "docker-cli.log"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  --version) echo 'Docker synthetic'; exit 0 ;;\n"
        "  compose)\n"
        "    if [[ \"$2\" == version ]]; then echo 'Docker Compose synthetic'; exit 0; fi\n"
        "    exit 0\n"
        "    ;;\n"
        '  exec) printf \'exec\\t%s\\t%s\\t%s\\t%s\\n\' "$2" "$3" "$4" "$5" >> "$FAKE_DOCKER_LOG"; '
        '[[ "${FAKE_FAILED_WORKER:-}" != "$2" ]] ;;\n'
        "  network) echo '{\"Containers\":{}}'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    os.chmod(docker, 0o755)
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(bin_directory), environment.get("PATH", "")))
    environment["FAKE_DOCKER_LOG"] = str(docker_log)
    if failed_worker is not None:
        environment["FAKE_FAILED_WORKER"] = failed_worker
    result = subprocess.run(
        (
            bash,
            "-c",
            'seq() { printf "1\\n"; }; sleep() { :; }; source "$1"',
            "cluster-setup-test",
            str(SETUP_SCRIPT),
        ),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )
    return result, docker_log.read_text(encoding="utf-8") if docker_log.exists() else ""


def test_cluster_setup_stops_when_fake_docker_readiness_probe_fails(tmp_path: Path) -> None:
    result, docker_log = _run_setup(tmp_path, failed_worker="aegis-worker-1")

    assert result.returncode != 0
    assert "worker-1" in result.stderr
    assert "single-host bridge smoke test is ready" not in result.stdout
    expected_probe = "\t".join(("exec", "aegis-worker-1", "python", "-c", READINESS_PROBE))
    assert expected_probe in docker_log


def test_cluster_setup_reports_ready_when_fake_docker_probes_succeed(tmp_path: Path) -> None:
    result, docker_log = _run_setup(tmp_path, failed_worker=None)

    assert result.returncode == 0, result.stderr
    assert "worker-1: listening" in result.stdout
    assert "worker-2: listening" in result.stdout
    assert "worker-3: listening" in result.stdout
    assert "single-host bridge smoke test is ready" in result.stdout
    assert {line.split("\t", maxsplit=2)[1] for line in docker_log.splitlines() if line.startswith("exec\t")} == {
        "aegis-worker-1",
        "aegis-worker-2",
        "aegis-worker-3",
    }
