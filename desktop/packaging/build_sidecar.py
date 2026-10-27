"""Build the bundled desktop service with the project Python environment."""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "desktop" / "src-tauri" / "resources"
PYTHON_VERSION = (ROOT / ".python-version").read_text(encoding="utf-8").strip()


def _build_sidecar() -> int:
    expected_version = tuple(int(part) for part in PYTHON_VERSION.split("."))
    gil_disabled = sysconfig.get_config_var("Py_GIL_DISABLED")
    if sys.implementation.name != "cpython" or sys.version_info[:3] != expected_version or bool(gil_disabled):
        print(
            f"Sidecar requires standard CPython {PYTHON_VERSION}; "
            f"observed {sys.implementation.name} {sys.version.split()[0]} "
            f"(Py_GIL_DISABLED={gil_disabled}).",
            file=sys.stderr,
        )
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    package_path = sysconfig.get_path("platlib")
    if not package_path:
        raise RuntimeError("The active Python environment has no platform package directory")
    # Keep the installed wheel ahead of checkout source, which can contain a stale ignored native extension.
    python_path = [package_path, str(ROOT), str(ROOT / "core" / "python")]
    inherited_python_path = os.environ.get("PYTHONPATH")
    if inherited_python_path:
        python_path.append(inherited_python_path)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        "aegis-desktop-service",
        "--distpath",
        str(OUTPUT),
        "--workpath",
        str(ROOT / "desktop" / ".pyinstaller-build"),
        "--specpath",
        str(ROOT / "desktop" / ".pyinstaller-build"),
        "--collect-binaries",
        "aegis_cognition",
        str(ROOT / "desktop" / "packaging" / "sidecar_entry.py"),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    completed = subprocess.run(
        command,
        cwd=ROOT / "desktop" / "packaging",
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    if sys.argv[1:] != ["--build-only"]:
        raise SystemExit("run this script through `npm run sidecar:build`")
    raise SystemExit(_build_sidecar())
