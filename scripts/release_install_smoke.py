"""Install one release wheel into an isolated environment and smoke-import it."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import venv
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def smoke(wheel: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "aegis-release-install-smoke-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "wheel": str(wheel),
        "wheel_sha256": sha256(wheel),
        "platform": platform.platform(),
        "status": "NOT VERIFIED",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-release-install-") as directory:
            environment = Path(directory) / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(environment)
            python = environment / ("Scripts" if sys.platform == "win32" else "bin") / "python"
            # Install declared runtime dependencies as a real clean install;
            # ``--no-deps`` would make the package-import check meaningless
            # because ``aegis_cognition`` imports its required BLAKE3 boundary.
            install = subprocess.run(
                [str(python), "-m", "pip", "install", "--force-reinstall", str(wheel)],
                check=True,
                capture_output=True,
                text=True,
                cwd=directory,
            )
            imported = subprocess.run(
                [
                    str(python),
                    "-c",
                    "import aegis_cognition; from aegis_cognition import Agent; print(Agent.__name__)",
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=directory,
            )
            report.update(
                {
                    "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
                    "pip_install_returncode": install.returncode,
                    "import_stdout": imported.stdout.strip(),
                    "import_returncode": imported.returncode,
                    "status": "PROVEN",
                }
            )
    except (OSError, subprocess.CalledProcessError) as error:
        report["reason"] = str(error)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = smoke(args.wheel.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PROVEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
