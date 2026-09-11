"""Emit conservative, source-labelled hardware provenance.

The report is an inventory boundary, not a backend detector.  An absent
portable probe is represented as ``UNKNOWN``; an empty accelerator list never
means that a GPU is absent or executable.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _observation(
    value: Any,
    provenance: str,
    source: str,
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "value": value,
        "provenance": provenance,
        "source": source,
        "limitations": limitations,
    }


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory_snapshot() -> dict[str, Any]:
    if sys.platform == "win32":
        windll = getattr(ctypes, "windll", None)
        kernel32 = getattr(windll, "kernel32", None)
        function = getattr(kernel32, "GlobalMemoryStatusEx", None)
        if function is not None:
            status = _MemoryStatusEx(dwLength=ctypes.sizeof(_MemoryStatusEx))
            function.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
            function.restype = ctypes.c_int
            try:
                if function(ctypes.byref(status)):
                    return {
                        "capacity_bytes": int(status.ullTotalPhys),
                        "available_bytes": int(status.ullAvailPhys),
                        "provenance": "DETECTED",
                        "source": "Windows GlobalMemoryStatusEx",
                        "limitations": ["Does not prove unified GPU memory or accelerator usability."],
                    }
            except OSError:
                pass

    if sys.platform != "win32" and hasattr(os, "sysconf"):
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            total_pages = int(os.sysconf("SC_PHYS_PAGES"))
            available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
            if page_size > 0 and total_pages > 0 and available_pages >= 0:
                return {
                    "capacity_bytes": page_size * total_pages,
                    "available_bytes": page_size * available_pages,
                    "provenance": "DETECTED",
                    "source": "POSIX sysconf SC_PHYS_PAGES/SC_AVPHYS_PAGES",
                    "limitations": ["Does not prove unified GPU memory or accelerator usability."],
                }
        except (OSError, TypeError, ValueError):
            pass

    return {
        "capacity_bytes": None,
        "available_bytes": None,
        "provenance": "UNKNOWN",
        "source": "No supported stdlib/native memory probe on this target",
        "limitations": ["Host memory remains unmeasured on this target."],
    }


def _storage_snapshot(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError as error:
        return {
            "value": [],
            "provenance": "UNKNOWN",
            "source": f"shutil.disk_usage({path})",
            "limitations": [f"Filesystem usage probe failed: {error.__class__.__name__}."],
        }
    return {
        "value": [
            {
                "id": str(path),
                "kind": "filesystem",
                "capacity_bytes": int(usage.total),
                "available_bytes": int(usage.free),
            }
        ],
        "provenance": "DETECTED",
        "source": f"shutil.disk_usage({path})",
        "limitations": [
            "Does not identify physical medium, bandwidth, latency, queue depth, or wear.",
            "Filesystem capacity is not a promise that spill I/O improves performance.",
        ],
    }


def _affinity_observation() -> dict[str, Any]:
    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        try:
            return _observation(
                len(get_affinity(0)),
                "DETECTED",
                "os.sched_getaffinity(0)",
                ["Reports the process affinity mask, not physical-core topology."],
            )
        except OSError:
            pass
    return _observation(
        None,
        "UNKNOWN",
        "No portable process-affinity probe on this target",
        ["Affinity/quota was not measured."],
    )


def _vulkan_snapshot() -> dict[str, Any]:
    """Discover Vulkan physical-device identity without claiming execution."""

    executable = shutil.which("vulkaninfo")
    if executable is None:
        return {
            "value": [],
            "provenance": "UNKNOWN",
            "source": "vulkaninfo executable not found",
            "limitations": [
                "Adapter absence is not established without a supported probe.",
                "No project GPU backend or workload execution was tested.",
            ],
        }
    try:
        completed = subprocess.run(
            [executable, "--summary"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "value": [],
            "provenance": "UNKNOWN",
            "source": "vulkaninfo --summary",
            "limitations": [
                f"Vulkan identity probe failed: {error.__class__.__name__}.",
                "No project GPU backend or workload execution was tested.",
            ],
        }
    if completed.returncode != 0:
        return {
            "value": [],
            "provenance": "UNKNOWN",
            "source": "vulkaninfo --summary",
            "limitations": [
                f"Vulkan identity probe exited {completed.returncode}.",
                "No project GPU backend or workload execution was tested.",
            ],
        }

    fields = {
        "deviceName": "device_name",
        "deviceType": "device_type",
        "vendorID": "vendor_id",
        "deviceID": "device_id",
        "driverName": "driver_name",
        "driverInfo": "driver_info",
        "apiVersion": "api_version",
    }
    devices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("GPU") and line.endswith(":"):
            if current:
                devices.append(current)
            current = {"backend": "Vulkan"}
            continue
        if current is None or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        field = fields.get(key)
        if field and value:
            current[field] = value
    if current:
        devices.append(current)
    if not devices:
        return {
            "value": [],
            "provenance": "UNKNOWN",
            "source": "vulkaninfo --summary",
            "limitations": [
                "The probe succeeded but did not expose a parseable physical device.",
                "No project GPU backend or workload execution was tested.",
            ],
        }
    return {
        "value": devices,
        "provenance": "DETECTED",
        "source": "vulkaninfo --summary",
        "limitations": [
            "Physical-device identity does not prove a project Vulkan backend exists.",
            "No project kernel, operation support, correctness oracle, or transfer benchmark was run.",
            "Device memory type, queue limits, power state, and contention remain unmeasured.",
        ],
    }


def build_report(*, now: datetime | None = None, cwd: Path | None = None) -> dict[str, Any]:
    """Build a local inventory with field-level provenance and limitations."""
    path = cwd if cwd is not None else Path.cwd()
    memory = _memory_snapshot()
    storage = _storage_snapshot(path)
    logical_processors = os.cpu_count()
    logical_provenance = "DETECTED" if logical_processors is not None else "UNKNOWN"
    logical_source = "os.cpu_count()" if logical_processors is not None else "os.cpu_count() returned None"
    accelerator_limitations = [
        "An empty value is not evidence that GPU/iGPU hardware is absent.",
        "Device presence, VRAM and driver metadata do not prove a usable project backend.",
    ]
    accelerator_topology = _vulkan_snapshot()
    timestamp = now or datetime.now(UTC)
    return {
        "schema": "aegis-hardware-profile-provenance-v1",
        "generated_at_utc": timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platform": platform.platform(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": _observation(
                platform.machine() or None,
                "DETECTED" if platform.machine() else "UNKNOWN",
                "platform.machine()",
                ["This does not identify a usable accelerator backend."],
            ),
            "processor": _observation(
                platform.processor() or None,
                "DETECTED" if platform.processor() else "UNKNOWN",
                "platform.processor()",
                ["Processor branding is not a performance measurement."],
            ),
            "provenance": "DETECTED",
            "source": "Python platform stdlib",
            "limitations": ["OS identity is local inventory, not cross-platform enforcement proof."],
        },
        "cpu": {
            "logical_processors": _observation(
                logical_processors,
                logical_provenance,
                logical_source,
                ["Logical count does not prove physical cores, quota, frequency, or available performance."],
            ),
            "physical_cores": _observation(
                None,
                "UNKNOWN",
                "No portable physical-core probe in this artifact",
                ["Do not infer physical cores from logical processors."],
            ),
            "numa": _observation(None, "UNKNOWN", "No portable NUMA probe in this artifact", ["NUMA topology was not measured."]),
            "quota": _observation(None, "UNKNOWN", "No portable quota probe in this artifact", ["OS/container quota was not measured."]),
            "affinity": _affinity_observation(),
        },
        "memory": {
            "host_bytes": _observation(
                memory["capacity_bytes"],
                memory["provenance"],
                memory["source"],
                memory["limitations"],
            ),
            "available_bytes": _observation(
                memory["available_bytes"],
                memory["provenance"],
                memory["source"],
                memory["limitations"],
            ),
            "unified_memory": _observation(
                None,
                "UNKNOWN",
                "No verified unified-memory probe in this artifact",
                ["Shared RAM/iGPU memory must not be inferred from adapter presence."],
            ),
        },
        "accelerator_topology": {
            **accelerator_topology,
            "limitations": accelerator_limitations + accelerator_topology["limitations"],
        },
        "storage_topology": storage,
        "policy_defaults": {"provenance": "ASSUMED", "source": "ResourcePolicy::default"},
        "status": "MEASURED_LOCAL_ONLY",
        "warning": "This report is not H0/H1/H2 hardware proof and does not claim unified physical memory or executable accelerators.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
