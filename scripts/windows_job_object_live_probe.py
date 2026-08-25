"""Run a conservative live Windows Job Object containment probe.

The probe is intentionally independent from the Rust unit fixture.  It reports
scoped live evidence when the OS accepts the job, the child is assigned, the
process-count rule is observed, and job termination actually stops the child.
Memory-limit configuration is recorded separately because observing a limit
configuration is weaker than observing an allocation-pressure kill.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_ALL_ACCESS_QUERY = 0x1000


class LargeInteger(ctypes.Structure):
    _fields_ = [("quad_part", ctypes.c_longlong)]


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", LargeInteger),
        ("per_job_user_time_limit", LargeInteger),
        ("limit_flags", ctypes.c_ulong),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_ulong),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_ulong),
        ("scheduling_class", ctypes.c_ulong),
    ]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", BasicLimitInformation),
        ("io_info", IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("total_user_time", LargeInteger),
        ("total_kernel_time", LargeInteger),
        ("this_period_total_user_time", LargeInteger),
        ("this_period_total_kernel_time", LargeInteger),
        ("total_page_fault_count", ctypes.c_ulong),
        ("total_processes", ctypes.c_ulong),
        ("active_processes", ctypes.c_ulong),
        ("total_terminated_processes", ctypes.c_ulong),
    ]


def child_command() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(60)"]


def pressure_child_command() -> list[str]:
    code = (
        "import time\n"
        "blocks=[]\n"
        "while True:\n"
        "    block=bytearray(4 * 1024 * 1024)\n"
        "    block[0]=1\n"
        "    blocks.append(block)\n"
        "    time.sleep(0.02)\n"
    )
    return [sys.executable, "-c", code]


def probe(output: Path | None = None) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "aegis-windows-job-object-live-probe-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": platform.platform(),
        "os": os.name,
        "implementation": "core/rust/src/resource_platform.rs::WindowsJobObjectController",
        "status": "NOT VERIFIED",
        "checks": {},
        "verification_scope": {
            "verified": [
                "job_object_limit_configuration",
                "process_assignment_and_containment",
                "active_process_limit",
                "job_termination_and_deadline_cancellation",
            ],
            "not_verified": ["memory_pressure_kill_observed"],
        },
        "limits": {
            "process_memory_bytes": 128 * 1024 * 1024,
            "active_process_limit": 1,
            "kill_on_job_close": True,
        },
    }
    if os.name != "nt":
        report["reason"] = "Windows-only probe executed on a non-Windows host"
        return _write(report, output)

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.IsProcessInJob.restype = ctypes.c_int
    kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        report["reason"] = f"CreateJobObjectW failed: {ctypes.get_last_error()}"
        return _write(report, output)

    child: subprocess.Popen[bytes] | None = None
    second: subprocess.Popen[bytes] | None = None
    pressure_child: subprocess.Popen[bytes] | None = None
    pressure_job = None
    try:
        limits = ExtendedLimitInformation()
        limits.basic_limit_information.limit_flags = (
            JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        limits.basic_limit_information.active_process_limit = 1
        limits.process_memory_limit = 128 * 1024 * 1024
        configured = bool(
            kernel32.SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
        )
        report["checks"]["limits_configured"] = configured
        if not configured:
            report["reason"] = f"SetInformationJobObject failed: {ctypes.get_last_error()}"
            return _write(report, output)

        child = subprocess.Popen(child_command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assigned = bool(kernel32.AssignProcessToJobObject(job, ctypes.c_void_p(child._handle)))
        report["checks"]["child_assigned"] = assigned
        if not assigned:
            report["reason"] = f"AssignProcessToJobObject failed: {ctypes.get_last_error()}"
            return _write(report, output)

        in_job = ctypes.c_int(0)
        process_in_job = bool(
            kernel32.IsProcessInJob(ctypes.c_void_p(child._handle), job, ctypes.byref(in_job))
        ) and bool(in_job.value)
        report["checks"]["process_containment_observed"] = process_in_job

        accounting = BasicAccountingInformation()
        returned = ctypes.c_ulong(0)
        accounting_ok = bool(
            kernel32.QueryInformationJobObject(
                job,
                JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(accounting),
                ctypes.sizeof(accounting),
                ctypes.byref(returned),
            )
        )
        report["checks"]["active_process_count_observed"] = accounting_ok and accounting.active_processes >= 1

        second = subprocess.Popen(child_command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second_assigned = bool(kernel32.AssignProcessToJobObject(job, ctypes.c_void_p(second._handle)))
        report["checks"]["second_process_rejected_by_active_limit"] = not second_assigned
        if second_assigned:
            second.terminate()
        else:
            second.terminate()
        second.wait(timeout=5)

        terminated = bool(kernel32.TerminateJobObject(job, 137))
        if terminated:
            child.wait(timeout=5)
        report["checks"]["termination_observed"] = terminated and child.poll() is not None
        report["checks"]["deadline_cancellation_observed"] = report["checks"]["termination_observed"]
        report["checks"]["memory_limit_configured"] = configured
        report["checks"]["memory_pressure_child_assigned"] = False
        report["checks"]["memory_pressure_kill_observed"] = False
        pressure_job = kernel32.CreateJobObjectW(None, None)
        if pressure_job:
            pressure_limits = ExtendedLimitInformation()
            pressure_limits.basic_limit_information.limit_flags = (
                JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            pressure_limits.process_memory_limit = 48 * 1024 * 1024
            pressure_configured = bool(
                kernel32.SetInformationJobObject(
                    pressure_job,
                    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(pressure_limits),
                    ctypes.sizeof(pressure_limits),
                )
            )
            report["checks"]["memory_pressure_limits_configured"] = pressure_configured
            if pressure_configured:
                pressure_child = subprocess.Popen(
                    pressure_child_command(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                assigned_pressure = bool(
                    kernel32.AssignProcessToJobObject(
                        pressure_job, ctypes.c_void_p(pressure_child._handle)
                    )
                )
                report["checks"]["memory_pressure_child_assigned"] = assigned_pressure
                if assigned_pressure:
                    baseline = BasicAccountingInformation()
                    returned = ctypes.c_ulong(0)
                    kernel32.QueryInformationJobObject(
                        pressure_job,
                        JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                        ctypes.byref(baseline),
                        ctypes.sizeof(baseline),
                        ctypes.byref(returned),
                    )
                    pressure_deadline = time.monotonic() + 8.0
                    while time.monotonic() < pressure_deadline and pressure_child.poll() is None:
                        time.sleep(0.05)
                    after = BasicAccountingInformation()
                    kernel32.QueryInformationJobObject(
                        pressure_job,
                        JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                        ctypes.byref(after),
                        ctypes.sizeof(after),
                        ctypes.byref(returned),
                    )
                    pressure_terminated = pressure_child.poll() is not None
                    report["checks"]["memory_pressure_process_terminated_by_job"] = (
                        pressure_terminated
                        and after.total_terminated_processes > baseline.total_terminated_processes
                    )
                    report["checks"]["memory_pressure_kill_observed"] = report["checks"][
                        "memory_pressure_process_terminated_by_job"
                    ]
        report["notes"] = (
            "Memory pressure uses a separate 48 MiB Job Object and is marked observed only when "
            "the child exits before the bounded wait and Job Object accounting records termination."
        )
        if report["checks"].get("memory_pressure_kill_observed") is True:
            report["verification_scope"]["verified"].append("memory_pressure_kill_observed")
            report["verification_scope"]["not_verified"] = []
        checks = report["checks"]
        required = [
            "limits_configured",
            "child_assigned",
            "process_containment_observed",
            "active_process_count_observed",
            "second_process_rejected_by_active_limit",
            "termination_observed",
            "deadline_cancellation_observed",
        ]
        report["status"] = (
            "PARTIALLY LIVE VERIFIED"
            if all(checks.get(name) is True for name in required)
            else "NOT VERIFIED"
        )
        if report["status"] != "PARTIALLY LIVE VERIFIED":
            report["reason"] = "one or more live Job Object checks did not pass"
    except (OSError, ctypes.ArgumentError, subprocess.SubprocessError, TimeoutError) as exc:
        report["reason"] = str(exc)
    finally:
        for process in (pressure_child, second, child):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        if pressure_job:
            kernel32.CloseHandle(pressure_job)
        kernel32.CloseHandle(job)
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
    return 0 if report.get("status") == "PARTIALLY LIVE VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
