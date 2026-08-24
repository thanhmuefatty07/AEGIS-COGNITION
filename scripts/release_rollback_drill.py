"""Run an isolated package rollback smoke when two wheels are available."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def install(python: Path, wheel: Path) -> None:
    subprocess.run(
        [str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )


def smoke(python: Path) -> None:
    subprocess.run(
        [str(python), "-c", "import aegis_cognition; from aegis_cognition import Agent; print(Agent.__name__)"],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--next", dest="next_wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report: dict[str, object] = {
        "schema": "aegis-release-rollback-drill-v1",
        "current": {"path": str(args.current), "sha256": digest(args.current)},
        "next": {"path": str(args.next_wheel), "sha256": digest(args.next_wheel)},
        "sequence": ["install N", "smoke N", "install N+1", "smoke N+1", "rollback N", "smoke N"],
        "status": "NOT VERIFIED",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-rollback-") as directory:
            environment = Path(directory) / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(environment)
            python = environment / ("Scripts" if sys.platform == "win32" else "bin") / "python"
            install(python, args.current)
            smoke(python)
            install(python, args.next_wheel)
            smoke(python)
            install(python, args.current)
            smoke(python)
        report["status"] = "PROVEN"
    except (OSError, subprocess.CalledProcessError) as error:
        report["status"] = "NOT VERIFIED"
        report["reason"] = str(error)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["status"] == "PROVEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
