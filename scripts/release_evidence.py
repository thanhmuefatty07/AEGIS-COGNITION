"""Create deterministic release hashes and a minimal SPDX SBOM.

The release workflow deliberately produces evidence first. Publishing and
promotion remain separate permissions, while every wheel/binary can be
verified against the generated manifest and provenance attestation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision(root: Path) -> str:
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if status:
            return "WORKTREE_DIRTY"
        declared = os.environ.get("GITHUB_SHA", "").strip()
        if declared:
            return declared
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "NOT VERIFIED"


def creation_timestamp() -> str:
    declared_epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if declared_epoch:
        try:
            timestamp = datetime.fromtimestamp(int(declared_epoch), tz=UTC)
        except (OverflowError, ValueError):
            timestamp = datetime.now(UTC)
    else:
        timestamp = datetime.now(UTC)
    return timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def wheel_contract_ok(path: Path) -> bool:
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
    return (
        any(name.startswith("aegis_cognition/") for name in names)
        and any("aegis_nerve" in name for name in names)
        and any(name.startswith("core/python/") for name in names)
    )


def build_evidence(root: Path, artifacts: Path, output: Path) -> None:
    files = sorted(path for path in artifacts.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"no release artifacts found under {artifacts}")
    wheels = [path for path in files if path.suffix == ".whl"]
    if not wheels or not all(wheel_contract_ok(path) for path in wheels):
        raise SystemExit("every release wheel must contain Python, native, and bridge payloads")

    output.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "path": path.relative_to(artifacts).as_posix(),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in files
    ]
    manifest = {
        "schema": "aegis-release-evidence-v1",
        "revision": git_revision(root),
        "ref": os.environ.get("GITHUB_REF", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "artifacts": entries,
        "verification": (
            "PROVEN for generated hashes; revision is WORKTREE_DIRTY when source changes are uncommitted; "
            "external signature/promotion remains separate"
        ),
    }
    (output / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    packages = [
        {
            "SPDXID": f"SPDXRef-{index}",
            "name": entry["path"],
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "checksums": [{"algorithm": "SHA256", "checksumValue": entry["sha256"]}],
        }
        for index, entry in enumerate(entries, start=1)
    ]
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "aegis-cognition-release",
        "documentNamespace": f"https://aegis-cognition.ai/sbom/{manifest['revision']}",
        "creationInfo": {
            "created": creation_timestamp(),
            "creators": ["Tool: scripts/release_evidence.py"],
        },
        "packages": packages,
    }
    (output / "release.sbom.spdx.json").write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build_evidence(root, args.artifacts.resolve(), args.output.resolve())
    print(json.dumps({"status": "PROVEN", "output": str(args.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
