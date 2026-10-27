#!/usr/bin/env python3
"""Export the locked runtime dependencies to the compatibility requirements file.

The project metadata and ``uv.lock`` are the dependency authorities. Run this
script after changing either one to refresh the top-level requirements export.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _resolve_uv() -> str | None:
    current_directory = Path.cwd().resolve()
    repository_root = ROOT.resolve()
    executable_name = "uv.exe" if os.name == "nt" else "uv"
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        try:
            resolved_directory = directory.resolve(strict=True)
        except OSError, RuntimeError:
            continue
        if resolved_directory.is_relative_to(current_directory) or resolved_directory.is_relative_to(repository_root):
            continue
        candidate = resolved_directory / executable_name
        resolved_executable = shutil.which(str(candidate))
        if resolved_executable is not None:
            return str(Path(resolved_executable).resolve())
    return None


def main() -> int:
    uv = _resolve_uv()
    if uv is None:
        print("uv is required to export the locked requirements.", file=sys.stderr)
        return 127

    result = subprocess.run(
        [
            uv,
            "export",
            "--locked",
            "--format",
            "requirements.txt",
            "--no-dev",
            "--no-default-groups",
            "--no-emit-project",
            "--no-editable",
            "--no-header",
            "--quiet",
            "--output-file",
            str(ROOT / "requirements.txt"),
        ],
        cwd=ROOT,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
