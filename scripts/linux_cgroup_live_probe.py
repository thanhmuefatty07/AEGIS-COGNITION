"""Run a conservative Linux cgroup-v2 live enforcement probe.

This harness is deliberately fail-closed.  On a non-Linux host it records that
the live lane is unavailable.  On Linux it only reports kernel enforcement when
the probe can create an isolated cgroup, attach a child, observe accounting,
exercise a bounded memory-pressure attempt, and terminate the group through
``cgroup.kill``.  A fixture or capability lookup is never promoted to proof.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


SCHEMA = "aegis-linux-cgroup-v2-live-probe-v1"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _child_code() -> str:
    return (
        "import time\n"
        "blocks=[]\n"
        "while True:\n"
        "    block = bytearray(1024 * 1024)\n"
        "    block[:] = b'x' * len(block)\n"
        "    blocks.append(block)\n"
        "    time.sleep(0.02)\n"
    )


def _deadline_child_code() -> str:
    return "import time\ntime.sleep(30)\n"


def probe(output: Path | None = None, *, cgroup_root: Path = Path("/sys/fs/cgroup")) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": SCHEMA,
        "generated_at_utc": _now(),
        "platform": platform.platform(),
        "os": os.name,
        "implementation": "core/rust/src/resource_platform.rs::LinuxCgroupV2Controller",
        "status": "NOT VERIFIED",
        "checks": {},
        "limits": {
            "memory_max_bytes": 32 * 1024 * 1024,
            "memory_swap_max_bytes": 0,
            "cpu_max": "10000 100000",
            "pids_max": 4,
        },
        "verification_scope": {
            "verified": [],
            "not_verified": ["all Linux cgroup live checks until a Linux privileged runner executes this file"],
        },
    }
    if platform.system() != "Linux":
        report["reason"] = "Linux-only live cgroup probe executed on a non-Linux host"
        return _write(report, output)

    required = [
        "cgroup.controllers",
        "cgroup.procs",
        "memory.max",
        "memory.swap.max",
        "memory.current",
        "memory.events",
        "cpu.max",
        "pids.max",
        "cgroup.kill",
    ]
    if not cgroup_root.is_dir() or any(not (cgroup_root / name).exists() for name in required):
        report["reason"] = f"cgroup v2 root is missing required files: {cgroup_root}"
        return _write(report, output)
    if not os.access(cgroup_root, os.W_OK):
        report["reason"] = "cgroup root is not writable; privileged isolated probe is required"
        return _write(report, output)

    group = cgroup_root / f"aegis-live-probe-{os.getpid()}"
    child: subprocess.Popen[bytes] | None = None
    try:
        group.mkdir()
        _write_text(group / "memory.max", str(report["limits"]["memory_max_bytes"]))
        _write_text(group / "memory.swap.max", str(report["limits"]["memory_swap_max_bytes"]))
        _write_text(group / "cpu.max", str(report["limits"]["cpu_max"]))
        _write_text(group / "pids.max", str(report["limits"]["pids_max"]))
        report["checks"]["limits_configured"] = (
            _read(group / "memory.max") == str(report["limits"]["memory_max_bytes"])
            and _read(group / "cpu.max") == str(report["limits"]["cpu_max"])
            and _read(group / "pids.max") == str(report["limits"]["pids_max"])
        )
        report["checks"]["swap_limit_configured"] = _read(group / "memory.swap.max") == "0"

        child = subprocess.Popen([sys.executable, "-c", _child_code()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _write_text(group / "cgroup.procs", str(child.pid))
        report["checks"]["child_attached"] = _read(group / "cgroup.procs") == str(child.pid)
        before_memory = _read(group / "memory.current")
        before_events = _read(group / "memory.events") or ""
        deadline = time.monotonic() + 8.0
        pressure_seen = False
        oom_kill_seen = False
        while time.monotonic() < deadline and child.poll() is None:
            current = _read(group / "memory.current")
            events = _read(group / "memory.events") or ""
            pressure_seen = pressure_seen or (current is not None and current != before_memory)
            oom_kill_seen = oom_kill_seen or _event_count(events, "oom_kill") > _event_count(before_events, "oom_kill")
            if oom_kill_seen:
                break
            time.sleep(0.05)
        events = _read(group / "memory.events") or ""
        oom_kill_seen = oom_kill_seen or _event_count(events, "oom_kill") > _event_count(before_events, "oom_kill")
        report["checks"]["live_memory_accounting_observed"] = pressure_seen
        report["checks"]["memory_pressure_oom_kill_observed"] = oom_kill_seen

        if child.poll() is None:
            _write_text(group / "cgroup.kill", "1")
            child.wait(timeout=5)

        child = subprocess.Popen(
            [sys.executable, "-c", _deadline_child_code()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _write_text(group / "cgroup.procs", str(child.pid))
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        alive_at_deadline = child.poll() is None
        _write_text(group / "cgroup.kill", "1")
        child.wait(timeout=5)
        report["checks"]["deadline_cancellation_observed"] = alive_at_deadline and child.returncode is not None
        report["checks"]["cgroup_kill_termination_observed"] = child.returncode is not None
        checks = report["checks"]
        required_checks = [
            "limits_configured",
            "swap_limit_configured",
            "child_attached",
            "live_memory_accounting_observed",
            "deadline_cancellation_observed",
            "cgroup_kill_termination_observed",
        ]
        if all(checks.get(name) is True for name in required_checks):
            report["status"] = "LIVE VERIFIED" if checks.get("memory_pressure_oom_kill_observed") else "PARTIALLY LIVE VERIFIED"
            verified_checks = list(required_checks)
            if checks.get("memory_pressure_oom_kill_observed"):
                verified_checks.append("memory_pressure_oom_kill_observed")
            report["verification_scope"]["verified"] = verified_checks
            if checks.get("memory_pressure_oom_kill_observed"):
                report["verification_scope"]["not_verified"] = []
            else:
                report["verification_scope"]["not_verified"] = ["kernel OOM kill under allocation pressure"]
        else:
            report["reason"] = "one or more live cgroup checks did not pass"
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        report["reason"] = str(exc)
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        try:
            if group.exists():
                group.rmdir()
        except OSError as exc:
            report["cleanup_warning"] = str(exc)
    return _write(report, output)


def _event_count(payload: str, key: str) -> int:
    for line in payload.splitlines():
        name, _, value = line.partition(" ")
        if name == key:
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


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
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    args = parser.parse_args()
    report = probe(args.output, cgroup_root=args.cgroup_root)
    return 0 if report.get("status") in {"LIVE VERIFIED", "PARTIALLY LIVE VERIFIED", "NOT VERIFIED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
