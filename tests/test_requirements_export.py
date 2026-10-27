from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UV_EXPORT_ARGS = [
    "export",
    "--locked",
    "--format",
    "requirements.txt",
    "--no-dev",
    "--no-default-groups",
    "--no-emit-project",
    "--no-editable",
]


def _load_export_script():
    script = REPOSITORY_ROOT / "scripts" / "_audit_regen_requirements.py"
    spec = importlib.util.spec_from_file_location("aegis_audit_regen_requirements", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_ignores_uv_from_the_current_checkout(monkeypatch, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    current_dir = repository_root / "checkout"
    trusted_dir = tmp_path / "trusted-tools"
    current_dir.mkdir(parents=True)
    trusted_dir.mkdir()
    executable_name = "uv.exe" if os.name == "nt" else "uv"
    checkout_uv = current_dir / executable_name
    repository_uv = repository_root / executable_name
    trusted_uv = trusted_dir / executable_name
    checkout_uv.write_bytes(b"synthetic checkout executable")
    repository_uv.write_bytes(b"synthetic repository executable")
    trusted_uv.write_bytes(b"synthetic trusted executable")
    if os.name != "nt":
        checkout_uv.chmod(0o755)
        trusted_uv.chmod(0o755)

    monkeypatch.chdir(current_dir)
    monkeypatch.setenv("PATH", os.pathsep.join((str(current_dir), str(repository_root), str(trusted_dir))))
    calls: list[list[str]] = []

    def capture_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(arguments)
        return SimpleNamespace(returncode=0)

    module = _load_export_script()
    monkeypatch.setattr(module, "ROOT", repository_root)
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=capture_run))

    assert module.main() == 0
    assert len(calls) == 1
    assert Path(calls[0][0]).resolve() == trusted_uv.resolve()


def test_compatibility_requirements_is_a_locked_uv_export(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to verify the project's lock export"

    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for relative_path in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(REPOSITORY_ROOT / relative_path, project / relative_path)
    shutil.copyfile(
        REPOSITORY_ROOT / "scripts" / "_audit_regen_requirements.py",
        scripts / "_audit_regen_requirements.py",
    )

    environment = os.environ.copy()
    environment["PATH"] = f"{Path(uv).parent}{os.pathsep}{environment.get('PATH', '')}"
    generated = subprocess.run(
        [sys.executable, str(scripts / "_audit_regen_requirements.py")],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr

    expected = subprocess.run(
        [uv, *UV_EXPORT_ARGS, "--no-header"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert expected.returncode == 0, expected.stdout + expected.stderr

    generated_lines = (project / "requirements.txt").read_text(encoding="utf-8").splitlines()
    generated_requirements = [line for line in generated_lines if line.strip() and not line.startswith("#")]
    expected_requirements = [line for line in expected.stdout.splitlines() if line.strip() and not line.startswith("#")]
    assert generated_requirements == expected_requirements

    checked_in_lines = (REPOSITORY_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    checked_in_requirements = [line for line in checked_in_lines if line.strip() and not line.startswith("#")]
    assert checked_in_requirements == expected_requirements, (
        "checked-in requirements.txt is stale; regenerate it with scripts/_audit_regen_requirements.py"
    )
