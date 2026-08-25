"""Create and verify a local git bundle without contacting a remote."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(root: str | Path, output: str | Path, remote_ref: str = "origin/main") -> dict[str, object]:
    root_path = Path(root).resolve()
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    head = _run(root_path, "rev-parse", "HEAD").stdout.strip()
    remote_head_result = _run(root_path, "rev-parse", remote_ref, check=False)
    remote_head = remote_head_result.stdout.strip() if remote_head_result.returncode == 0 else ""
    revision_range = f"{remote_ref}..HEAD" if remote_head else "HEAD"
    _run(root_path, "bundle", "create", str(output_path), revision_range)
    verified = _run(root_path, "bundle", "verify", str(output_path), check=False)
    heads = _run(root_path, "bundle", "list-heads", str(output_path), check=False)
    commit_count = _run(root_path, "rev-list", "--count", revision_range).stdout.strip()
    report = {
        "schema": "aegis-local-git-backup-v1",
        "truth_claim": False,
        "claim_scope": "LOCAL_BACKUP_ONLY",
        "independent_verification": "NOT VERIFIED",
        "local_head": head,
        "remote_ref": remote_ref,
        "remote_ref_head": remote_head,
        "revision_range": revision_range,
        "commit_count": int(commit_count),
        "bundle_path": str(output_path),
        "bundle_sha256": _sha256(output_path),
        "bundle_verified": verified.returncode == 0,
        "bundle_verify_output": verified.stdout.strip() or verified.stderr.strip(),
        "bundle_heads": heads.stdout.strip().splitlines(),
    }
    manifest_path = output_path.with_suffix(".json")
    manifest_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--remote-ref", default="origin/main")
    args = parser.parse_args()
    report = create_backup(args.root, args.output, args.remote_ref)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["bundle_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
