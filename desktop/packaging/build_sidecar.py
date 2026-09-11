"""Build the bundled desktop service with the project Python environment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "desktop" / "src-tauri" / "resources"


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "aegis-desktop-service",
        "--distpath",
        str(OUTPUT),
        "--workpath",
        str(ROOT / "desktop" / ".pyinstaller-build"),
        "--specpath",
        str(ROOT / "desktop" / ".pyinstaller-build"),
        "--paths",
        str(ROOT),
        "--paths",
        str(ROOT / "core" / "python"),
        "--collect-binaries",
        "aegis_cognition",
        str(ROOT / "desktop" / "packaging" / "sidecar_entry.py"),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
