"""Report live OS enforcement evidence without confusing fixtures with proof."""

from __future__ import annotations

import argparse
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path


def capability_matrix(system: str) -> dict[str, str]:
    if system == "Linux":
        return {
            "cpu": "KERNEL_ENFORCED if privileged cgroup v2 live test passes",
            "memory": "KERNEL_ENFORCED if privileged cgroup v2 live test passes",
            "process_count": "KERNEL_ENFORCED if privileged cgroup v2 live test passes",
            "thread_count": "BEST_EFFORT",
            "io": "MEASUREMENT_ONLY",
            "termination": "KERNEL_ENFORCED if cgroup.kill is available and tested",
        }
    if system == "Windows":
        return {
            "cpu": "BEST_EFFORT until live Job Object test",
            "memory": "KERNEL_ENFORCED if live Job Object test passes",
            "process_count": "BEST_EFFORT until live count test",
            "thread_count": "BEST_EFFORT",
            "io": "MEASUREMENT_ONLY",
            "termination": "KERNEL_ENFORCED if live termination test passes",
        }
    if system == "Darwin":
        return {
            "cpu": "MEASUREMENT_ONLY",
            "memory": "MEASUREMENT_ONLY",
            "process_count": "MEASUREMENT_ONLY",
            "thread_count": "MEASUREMENT_ONLY",
            "io": "MEASUREMENT_ONLY",
            "termination": "COOPERATIVE_ONLY",
        }
    return {name: "UNKNOWN" for name in ("cpu", "memory", "process_count", "thread_count", "io", "termination")}


def build_report() -> dict[str, object]:
    system = platform.system()
    privileged_requested = os.environ.get("AEGIS_RUN_PRIVILEGED_PROBES") == "1"
    return {
        "schema": "aegis-platform-enforcement-probe-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": system,
        "platform_version": platform.platform(),
        "implementation": {
            "linux": "core/rust/src/resource_platform.rs::LinuxCgroupV2Controller",
            "windows": "core/rust/src/resource_platform.rs::WindowsJobObjectController",
            "macos": "core/rust/src/resource_platform.rs::MacosCooperativeController",
        },
        "capability_level": capability_matrix(system),
        "privileged_probe_requested": privileged_requested,
        "status": "IMPLEMENTED / NOT VERIFIED",
        "reason": "runner must execute and retain the platform-specific child-process harness",
        "closure": "Run a real child-process limit/termination/deadline test with required OS privileges and retain logs plus this JSON.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    report = build_report()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if args.require_live:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
