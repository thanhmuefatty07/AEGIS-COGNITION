from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import re
import sysconfig
import tomllib
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_sidecar_build_reinstalls_current_project_before_freezing() -> None:
    runner = (REPOSITORY_ROOT / "desktop" / "packaging" / "run_sidecar_build.mjs").read_text(encoding="utf-8")
    sync_call = re.search(
        r"const syncStatus = runUv\(\s*\[(?P<arguments>.*?)\]\s*,",
        runner,
        re.DOTALL,
    )
    assert sync_call is not None, "the sidecar runner must have a distinct uv sync step"
    arguments = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', sync_call.group("arguments"))
    project_metadata = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_name = project_metadata["project"]["name"]
    assert "--reinstall-package" in arguments, "same-version project wheels must be refreshed before freezing"
    reinstall_index = arguments.index("--reinstall-package")
    assert arguments[reinstall_index + 1] == package_name


def _load_sidecar_builder():
    path = REPOSITORY_ROOT / "desktop" / "packaging" / "build_sidecar.py"
    spec = importlib.util.spec_from_file_location("aegis_sidecar_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _set_builder_runtime(monkeypatch, builder, version: tuple[int, int, int]) -> None:
    version_text = ".".join(str(part) for part in version)
    get_config_var = builder.sysconfig.get_config_var
    monkeypatch.setattr(
        builder.sysconfig,
        "get_config_var",
        lambda name: None if name == "Py_GIL_DISABLED" else get_config_var(name),
    )
    monkeypatch.setattr(
        builder,
        "sys",
        SimpleNamespace(
            implementation=SimpleNamespace(name="cpython"),
            version_info=(*version, "final", 0),
            version=version_text,
            executable="python.exe",
            stderr=io.StringIO(),
        ),
    )


def test_sidecar_builder_orders_installed_site_packages_before_checkout_sources(
    monkeypatch,
    tmp_path: Path,
) -> None:
    builder = _load_sidecar_builder()
    expected_version = tuple(int(part) for part in builder.PYTHON_VERSION.split("."))
    _set_builder_runtime(monkeypatch, builder, expected_version)
    repository = tmp_path / "project"
    output = tmp_path / "sidecar-output"
    monkeypatch.setattr(builder, "ROOT", repository)
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setenv("PYTHONPATH", "existing-python-path")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def capture_build(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", capture_build)

    assert builder._build_sidecar() == 0
    assert len(calls) == 1
    command, options = calls[0]
    assert options["cwd"] == repository / "desktop" / "packaging"
    assert "--paths" not in command
    assert "--clean" not in command, "building the sidecar must retain the machine-wide PyInstaller cache"
    search_paths = options["env"]["PYTHONPATH"].split(os.pathsep)
    assert search_paths == [
        sysconfig.get_path("platlib"),
        str(repository),
        str(repository / "core" / "python"),
        "existing-python-path",
    ]


def test_sidecar_builder_rejects_free_threaded_python(monkeypatch, tmp_path: Path) -> None:
    builder = _load_sidecar_builder()
    expected_version = tuple(int(part) for part in builder.PYTHON_VERSION.split("."))
    _set_builder_runtime(monkeypatch, builder, expected_version)
    monkeypatch.setattr(builder, "OUTPUT", tmp_path / "must-not-be-created")
    monkeypatch.setattr(builder.sysconfig, "get_config_var", lambda name: 1 if name == "Py_GIL_DISABLED" else None)

    assert builder._build_sidecar() == 1
    assert not (tmp_path / "must-not-be-created").exists()


def test_sidecar_builder_rejects_unpinned_runtime(monkeypatch, tmp_path: Path) -> None:
    builder = _load_sidecar_builder()
    _set_builder_runtime(monkeypatch, builder, (3, 15, 0))
    output = tmp_path / "must-not-be-created"
    monkeypatch.setattr(builder, "OUTPUT", output)

    assert builder._build_sidecar() == 1
    assert not output.exists()
