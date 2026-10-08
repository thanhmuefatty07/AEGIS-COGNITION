from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOTS = (
    ("benchmark_preflight.py", "ROOT"),
    ("benchmark_gate.py", "ROOT"),
    ("benchmark_wrapper.py", "ROOT"),
    ("constitution_audit.py", "ROOT"),
    ("smoke_check.py", "PROJECT_ROOT"),
)


@pytest.mark.parametrize(("script_name", "root_name"), SCRIPT_ROOTS)
def test_script_root_follows_relocated_checkout(
    tmp_path: Path, script_name: str, root_name: str
) -> None:
    relocated_root = tmp_path / "relocated checkout"
    relocated_scripts = relocated_root / "scripts"
    relocated_scripts.mkdir(parents=True)
    script_path = relocated_scripts / script_name
    shutil.copy2(REPOSITORY_ROOT / "scripts" / script_name, script_path)

    probe = (
        "import json, runpy, sys; from pathlib import Path; "
        "namespace = runpy.run_path(sys.argv[1], run_name='root_probe'); "
        f"print(json.dumps(str(Path(str(namespace[{root_name!r}])).resolve())))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(script_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert Path(json.loads(completed.stdout)) == relocated_root.resolve()
