"""Retain macOS capability-level and cooperative cancellation evidence."""

from __future__ import annotations

import argparse
import json
import platform
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def probe(output: Path | None = None) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "aegis-macos-capability-probe-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": platform.platform(),
        "implementation": "core/rust/src/resource_platform.rs::MacosCooperativeController",
        "capability_level": {
            "cpu": "MEASUREMENT_ONLY",
            "memory": "MEASUREMENT_ONLY",
            "process_count": "MEASUREMENT_ONLY",
            "thread_count": "MEASUREMENT_ONLY",
            "io": "MEASUREMENT_ONLY",
            "termination": "COOPERATIVE_ONLY",
        },
        "status": "NOT VERIFIED",
        "checks": {},
        "kernel_enforcement_claim": False,
    }
    if platform.system() != "Darwin":
        report["reason"] = "macOS-only capability probe executed on a non-macOS host"
        return _write(report, output)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.1)
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=5)
        report["checks"]["cooperative_cancellation_observed"] = child.returncode is not None
        report["checks"]["cooperative_termination_observed"] = child.returncode == -signal.SIGTERM
        if all(report["checks"].values()):
            report["status"] = "COOPERATIVE LIVE VERIFIED"
            report["reason"] = "macOS lane verifies cooperative cancellation only; kernel-equivalent limits remain unclaimed"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    return _write(report, output)


def _write(report: dict[str, object], output: Path | None) -> dict[str, object]:
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = probe(args.output)
    return 0 if report.get("status") in {"COOPERATIVE LIVE VERIFIED", "NOT VERIFIED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
