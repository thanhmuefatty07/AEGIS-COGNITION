"""Emit conservative hardware provenance for benchmark evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "schema": "aegis-hardware-profile-provenance-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": platform.platform(),
        "cpu": {
            "logical_processors": {"value": os.cpu_count(), "provenance": "DETECTED"},
            "physical_cores": {"value": None, "provenance": "UNKNOWN"},
            "numa": {"value": None, "provenance": "UNKNOWN"},
            "quota": {"value": None, "provenance": "UNKNOWN"},
            "affinity": {"value": None, "provenance": "UNKNOWN"},
        },
        "memory": {
            "host_bytes": {"value": None, "provenance": "UNKNOWN"},
            "unified_memory": {"value": None, "provenance": "UNKNOWN"},
        },
        "accelerator_topology": {"value": [], "provenance": "UNKNOWN"},
        "storage_topology": {"value": [], "provenance": "UNKNOWN"},
        "policy_defaults": {"provenance": "ASSUMED", "source": "ResourcePolicy::default"},
        "status": "MEASURED_LOCAL_ONLY",
        "warning": "This report is not H0/H1/H2 hardware proof and does not claim unified physical memory.",
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
