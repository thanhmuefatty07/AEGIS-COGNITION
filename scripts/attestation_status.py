"""Record provider attestation outcome without changing artifact evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    outcome = os.environ.get("ATTESTATION_OUTCOME", "unknown").strip().lower()
    status = "PROVEN" if outcome == "success" else "NOT VERIFIED"
    try:
        head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head_sha = "NOT VERIFIED"
    result = {
        "schema": "aegis-attestation-status-v1",
        "commit": head_sha,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "provider_outcome": outcome,
        "status": status,
        "reason": "provider attestation succeeded" if status == "PROVEN" else "provider capability unavailable or attestation persistence failed",
        "future_closure": "Run the same release workflow in a repository/provider where signed build provenance is supported, then verify subject digest.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
