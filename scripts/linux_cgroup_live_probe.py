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
import signal
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path


SCHEMA = "aegis-linux-cgroup-v2-live-probe-v1"
_REQUIRED_CONTROLLERS = {"cpu", "memory", "pids"}
_SIGKILL_RETURN_CODE = -getattr(signal, "SIGKILL", 9)
_CHECK_LABELS = {
    "limits_configured": "CPU, memory, and process limits configured",
    "swap_limit_configured": "swap limit configured",
    "child_attached": "memory-pressure child attached to the cgroup",
    "live_memory_accounting_observed": "live memory accounting",
    "memory_pressure_oom_kill_observed": "memory-limit OOM denial",
    "cpu_child_attached": "CPU-pressure child attached to the cgroup",
    "cpu_quota_enforcement_observed": "CPU quota enforcement",
    "pids_child_attached": "process-limit child attached to the cgroup",
    "process_count_denial_observed": "process-count denial",
    "descendant_containment_observed": "descendant containment",
    "cgroup_kill_termination_observed": "cgroup.kill termination",
    "deadline_child_attached": "deadline child attached to the cgroup",
    "deadline_cancellation_observed": "deadline cancellation",
    "scope_cleanup_observed": "temporary cgroup cleanup",
}
_FULL_LIVE_CHECKS = tuple(_CHECK_LABELS)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _memory_child_code() -> str:
    return (
        "import sys, time\n"
        "sys.stdin.buffer.readline()\n"
        "blocks=[]\n"
        "while True:\n"
        "    block = bytearray(1024 * 1024)\n"
        "    block[:] = b'x' * len(block)\n"
        "    blocks.append(block)\n"
        "    time.sleep(0.02)\n"
    )


def _cpu_child_code() -> str:
    return (
        "import sys, time\n"
        "sys.stdin.buffer.readline()\n"
        "deadline = time.monotonic() + 1.0\n"
        "while time.monotonic() < deadline:\n"
        "    pass\n"
    )


def _pids_child_code() -> str:
    return (
        "import errno, os, sys, time\n"
        "sys.stdin.buffer.readline()\n"
        "denied = False\n"
        "for _ in range(8):\n"
        "    try:\n"
        "        pid = os.fork()\n"
        "    except OSError as exc:\n"
        "        if exc.errno != errno.EAGAIN:\n"
        "            raise\n"
        "        denied = True\n"
        "        break\n"
        "    if pid == 0:\n"
        "        while True:\n"
        "            time.sleep(1)\n"
        "if denied:\n"
        "    while True:\n"
        "        time.sleep(1)\n"
    )


def _deadline_child_code() -> str:
    return "import sys, time\nsys.stdin.buffer.readline()\ntime.sleep(30)\n"


def _integer_field(payload: str | None, key: str) -> int | None:
    if payload is None:
        return None
    found: int | None = None
    for line in payload.splitlines():
        fields = line.split()
        if not fields or fields[0] != key:
            continue
        if len(fields) != 2 or found is not None:
            return None
        try:
            found = int(fields[1])
        except ValueError:
            return None
        if found < 0:
            return None
    return found


def _scalar_integer(payload: str | None) -> int | None:
    if payload is None:
        return None
    try:
        value = int(payload)
    except ValueError:
        return None
    return value if value >= 0 else None


def _cpu_quota_enforced(before: str | None, after: str | None) -> bool:
    before_usage = _integer_field(before, "usage_usec")
    after_usage = _integer_field(after, "usage_usec")
    before_periods = _integer_field(before, "nr_periods")
    after_periods = _integer_field(after, "nr_periods")
    before_throttled = _integer_field(before, "nr_throttled")
    after_throttled = _integer_field(after, "nr_throttled")
    before_throttled_usec = _integer_field(before, "throttled_usec")
    after_throttled_usec = _integer_field(after, "throttled_usec")
    return (
        before_usage is not None
        and after_usage is not None
        and after_usage > before_usage
        and before_periods is not None
        and after_periods is not None
        and after_periods > before_periods
        and before_throttled is not None
        and after_throttled is not None
        and after_throttled > before_throttled
        and before_throttled_usec is not None
        and after_throttled_usec is not None
        and after_throttled_usec > before_throttled_usec
    )


def _pids_limit_denial_observed(
    before_events: str | None,
    after_events: str | None,
    current: str | None,
    limit: int,
    child_alive: bool,
) -> bool:
    before_max = _integer_field(before_events, "max")
    after_max = _integer_field(after_events, "max")
    current_count = _scalar_integer(current)
    return (
        limit > 0
        and child_alive
        and before_max is not None
        and after_max is not None
        and after_max > before_max
        and current_count is not None
        and current_count >= limit
    )


def _descendant_termination_observed(
    before_events: str | None,
    after_events: str | None,
    before_current: str | None,
    after_current: str | None,
) -> bool:
    return (
        _integer_field(before_events, "populated") == 1
        and _integer_field(after_events, "populated") == 0
        and (_scalar_integer(before_current) or 0) > 1
        and _scalar_integer(after_current) == 0
    )


def _attach_child(group: Path, child: subprocess.Popen[bytes]) -> bool:
    _write_text(group / "cgroup.procs", str(child.pid))
    return str(child.pid) in (_read(group / "cgroup.procs") or "").splitlines()


def _release_child(child: subprocess.Popen[bytes]) -> None:
    if child.stdin is None:
        raise OSError("probe child stdin pipe was not created")
    child.stdin.write(b"start\n")
    child.stdin.flush()
    child.stdin.close()


def _wait_for_empty(group: Path, timeout: float = 5.0) -> tuple[bool, str | None, str | None]:
    deadline = time.monotonic() + timeout
    events: str | None = None
    current: str | None = None
    while True:
        events = _read(group / "cgroup.events")
        current = _read(group / "pids.current")
        if _integer_field(events, "populated") == 0 and _scalar_integer(current) == 0:
            return True, events, current
        if time.monotonic() >= deadline:
            return False, events, current
        time.sleep(0.02)


def _event_delta(before: str | None, after: str | None, key: str) -> bool:
    before_value = _integer_field(before, key)
    after_value = _integer_field(after, key)
    return before_value is not None and after_value is not None and after_value > before_value


def _terminate_group(group: Path, child: subprocess.Popen[bytes] | None = None) -> tuple[bool, str | None, str | None]:
    _write_text(group / "cgroup.kill", "1")
    if child is not None:
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=2)
    return _wait_for_empty(group)


def _start_gated_child(code: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _probe_memory(group: Path, children: list[subprocess.Popen[bytes]], checks: dict[str, object]) -> None:
    child = _start_gated_child(_memory_child_code())
    children.append(child)
    attached = _attach_child(group, child)
    checks["child_attached"] = attached
    before_memory = _scalar_integer(_read(group / "memory.current"))
    before_events = _read(group / "memory.events")
    if attached:
        _release_child(child)
        deadline = time.monotonic() + 8.0
        pressure_seen = False
        oom_kill_seen = False
        while time.monotonic() < deadline and child.poll() is None:
            current = _scalar_integer(_read(group / "memory.current"))
            pressure_seen |= before_memory is not None and current is not None and current > before_memory
            oom_kill_seen |= _event_delta(before_events, _read(group / "memory.events"), "oom_kill")
            if oom_kill_seen:
                break
            time.sleep(0.05)
        final_events = _read(group / "memory.events")
        oom_kill_seen |= _event_delta(before_events, final_events, "oom_kill")
        checks["live_memory_accounting_observed"] = pressure_seen
        if oom_kill_seen and child.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                child.wait(timeout=2)
        checks["memory_pressure_oom_kill_observed"] = oom_kill_seen and child.returncode == _SIGKILL_RETURN_CODE
    empty, _, _ = _terminate_group(group, child)
    if child.poll() is None:
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=2)
    if not empty:
        raise TimeoutError("memory-pressure cgroup did not become empty after cgroup.kill")


def _probe_cpu(group: Path, children: list[subprocess.Popen[bytes]], checks: dict[str, object]) -> None:
    child = _start_gated_child(_cpu_child_code())
    children.append(child)
    attached = _attach_child(group, child)
    checks["cpu_child_attached"] = attached
    before_stats = _read(group / "cpu.stat")
    if attached:
        _release_child(child)
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=4)
        after_stats = _read(group / "cpu.stat")
        checks["cpu_quota_enforcement_observed"] = child.returncode == 0 and _cpu_quota_enforced(
            before_stats, after_stats
        )
    empty, _, _ = _terminate_group(group, child)
    if child.poll() is None:
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=2)
    if not empty:
        raise TimeoutError("CPU-probe cgroup did not become empty after cgroup.kill")


def _probe_pids(group: Path, children: list[subprocess.Popen[bytes]], checks: dict[str, object]) -> None:
    child = _start_gated_child(_pids_child_code())
    children.append(child)
    attached = _attach_child(group, child)
    checks["pids_child_attached"] = attached
    before_events = _read(group / "pids.events")
    if attached:
        _release_child(child)
        deadline = time.monotonic() + 3.0
        current: str | None = None
        after_events = _read(group / "pids.events")
        while time.monotonic() < deadline and child.poll() is None:
            current = _read(group / "pids.current")
            after_events = _read(group / "pids.events")
            if _pids_limit_denial_observed(before_events, after_events, current, 4, True):
                break
            time.sleep(0.02)
        checks["process_count_denial_observed"] = _pids_limit_denial_observed(
            before_events, after_events, current, 4, child.poll() is None
        )
        before_group_events = _read(group / "cgroup.events")
        before_current = _read(group / "pids.current")
        empty, after_group_events, after_current = _terminate_group(group, child)
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=2)
        checks["descendant_containment_observed"] = (
            empty
            and child.returncode == _SIGKILL_RETURN_CODE
            and _descendant_termination_observed(before_group_events, after_group_events, before_current, after_current)
        )
        checks["cgroup_kill_termination_observed"] |= empty and child.returncode == _SIGKILL_RETURN_CODE
        if not empty:
            raise TimeoutError("pids-probe cgroup tree did not become empty after cgroup.kill")
    else:
        empty, _, _ = _terminate_group(group, child)
        if not empty:
            raise TimeoutError("unattached pids-probe cgroup did not become empty")


def _probe_deadline(group: Path, children: list[subprocess.Popen[bytes]], checks: dict[str, object]) -> None:
    child = _start_gated_child(_deadline_child_code())
    children.append(child)
    attached = _attach_child(group, child)
    checks["deadline_child_attached"] = attached
    if attached:
        _release_child(child)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.02)
        alive_at_deadline = child.poll() is None
        empty, _, _ = _terminate_group(group, child)
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=2)
        killed = empty and child.returncode == _SIGKILL_RETURN_CODE
        checks["deadline_cancellation_observed"] = alive_at_deadline and killed
        checks["cgroup_kill_termination_observed"] |= killed
        if not empty:
            raise TimeoutError("deadline-probe cgroup did not become empty after cgroup.kill")
    else:
        empty, _, _ = _terminate_group(group)
        if not empty:
            raise TimeoutError("unattached deadline-probe cgroup did not become empty")


def probe(output: Path | None = None, *, cgroup_root: Path = Path("/sys/fs/cgroup")) -> dict[str, object]:
    limits = {
        "memory_max_bytes": 32 * 1024 * 1024,
        "memory_swap_max_bytes": 0,
        "cpu_max": "10000 100000",
        "pids_max": 4,
    }
    report: dict[str, object] = {
        "schema": SCHEMA,
        "generated_at_utc": _now(),
        "platform": platform.platform(),
        "os": os.name,
        "implementation": "core/rust/src/resource_platform.rs::LinuxCgroupV2Controller",
        "status": "NOT VERIFIED",
        "checks": {name: False for name in _CHECK_LABELS},
        "check_labels": _CHECK_LABELS,
        "limits": limits,
        "verification_scope": {"verified": [], "not_verified": list(_CHECK_LABELS.values())},
    }
    if platform.system() != "Linux":
        report["reason"] = "Linux-only live cgroup probe executed on a non-Linux host"
        return _write(report, output)

    root_files = ("cgroup.controllers", "cgroup.procs", "cgroup.subtree_control")
    if not cgroup_root.is_dir() or any(not (cgroup_root / name).exists() for name in root_files):
        report["reason"] = "cgroup v2 root is missing required delegation files"
        return _write(report, output)

    available = set((_read(cgroup_root / "cgroup.controllers") or "").split())
    enabled = {item.lstrip("+") for item in (_read(cgroup_root / "cgroup.subtree_control") or "").split()}
    report["kernel"] = {
        "release": platform.release(),
        "controllers_available": sorted(available),
        "controllers_enabled_for_children": sorted(enabled),
    }
    missing = sorted(_REQUIRED_CONTROLLERS - available)
    disabled = sorted(_REQUIRED_CONTROLLERS - enabled)
    if missing or disabled:
        report["reason"] = f"required cgroup controllers unavailable={missing}, not enabled for children={disabled}"
        return _write(report, output)
    if not os.access(cgroup_root, os.W_OK):
        report["reason"] = "cgroup root is not writable; delegated cgroup-v2 access is required"
        return _write(report, output)

    group = cgroup_root / f"aegis-live-probe-{os.getpid()}-{time.time_ns()}"
    group_created = False
    group_empty = False
    group_removed = False
    children: list[subprocess.Popen[bytes]] = []
    checks = report["checks"]
    assert isinstance(checks, dict)
    try:
        group.mkdir()
        group_created = True
        group_files = (
            "cgroup.procs",
            "cgroup.events",
            "cgroup.kill",
            "memory.max",
            "memory.swap.max",
            "memory.current",
            "memory.events",
            "cpu.max",
            "cpu.stat",
            "pids.max",
            "pids.current",
            "pids.events",
        )
        missing_group_files = [name for name in group_files if not (group / name).exists()]
        if missing_group_files:
            raise OSError(f"created cgroup is missing controls: {missing_group_files}")

        _write_text(group / "memory.max", str(limits["memory_max_bytes"]))
        _write_text(group / "memory.swap.max", str(limits["memory_swap_max_bytes"]))
        _write_text(group / "cpu.max", str(limits["cpu_max"]))
        _write_text(group / "pids.max", str(limits["pids_max"]))
        checks["limits_configured"] = (
            _read(group / "memory.max") == str(limits["memory_max_bytes"])
            and _read(group / "cpu.max") == limits["cpu_max"]
            and _read(group / "pids.max") == str(limits["pids_max"])
        )
        checks["swap_limit_configured"] = _read(group / "memory.swap.max") == "0"
        if not checks["limits_configured"] or not checks["swap_limit_configured"]:
            raise OSError("kernel did not retain one or more requested cgroup limits")

        _probe_memory(group, children, checks)
        _probe_cpu(group, children, checks)
        _probe_pids(group, children, checks)
        _probe_deadline(group, children, checks)
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        report["reason"] = str(exc)
    finally:
        cleanup_errors: list[str] = []
        if group_created and group.exists():
            try:
                group_empty, _, _ = _terminate_group(group)
            except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
                cleanup_errors.append(f"cgroup termination/empty observation: {exc}")
            for child in children:
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
                    cleanup_errors.append(f"child cleanup: {exc}")
            if group_empty:
                try:
                    group.rmdir()
                except OSError as exc:
                    cleanup_errors.append(f"cgroup removal: {exc}")
            group_removed = not group.exists()

        children_stopped = all(child.poll() is not None for child in children)
        cleanup_observed = group_created and group_empty and group_removed and children_stopped
        checks["scope_cleanup_observed"] = cleanup_observed
        if not cleanup_observed:
            cleanup_errors.append("temporary cgroup cleanup was not fully observed")
            report["cleanup_warning"] = "; ".join(cleanup_errors)

    passed = [name for name in _FULL_LIVE_CHECKS if checks.get(name) is True]
    report["verification_scope"] = {
        "verified": passed,
        "not_verified": [_CHECK_LABELS[name] for name in _FULL_LIVE_CHECKS if name not in passed],
    }
    if len(passed) == len(_FULL_LIVE_CHECKS):
        report["status"] = "LIVE VERIFIED"
    elif checks.get("scope_cleanup_observed") is True and passed:
        report["status"] = "PARTIALLY LIVE VERIFIED"
        report["reason"] = report.get("reason") or "one or more live kernel enforcement checks did not pass"
    else:
        report["status"] = "NOT VERIFIED"
        report["reason"] = report.get("reason") or "no live kernel enforcement check passed"
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
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="return failure unless every live cgroup enforcement check is verified",
    )
    args = parser.parse_args()
    report = probe(args.output, cgroup_root=args.cgroup_root)
    if args.require_live and report.get("status") != "LIVE VERIFIED":
        return 1
    return 0 if report.get("status") in {"LIVE VERIFIED", "PARTIALLY LIVE VERIFIED", "NOT VERIFIED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
