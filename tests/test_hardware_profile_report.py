from __future__ import annotations

from pathlib import Path

from scripts import hardware_profile_report as report


def test_hardware_profile_report_preserves_provenance_and_unknown_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(report.platform, "platform", lambda: "TestOS-1")
    monkeypatch.setattr(report.platform, "system", lambda: "TestOS")
    monkeypatch.setattr(report.platform, "release", lambda: "1")
    monkeypatch.setattr(report.platform, "version", lambda: "build")
    monkeypatch.setattr(report.platform, "machine", lambda: "test-machine")
    monkeypatch.setattr(report.platform, "processor", lambda: "test-processor")
    monkeypatch.setattr(report.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(report.os, "sched_getaffinity", lambda _pid: {0, 1}, raising=False)
    monkeypatch.setattr(
        report,
        "_memory_snapshot",
        lambda: {
            "capacity_bytes": 8 * 1024**3,
            "available_bytes": 3 * 1024**3,
            "provenance": "DETECTED",
            "source": "test memory probe",
            "limitations": ["test limitation"],
        },
    )
    monkeypatch.setattr(
        report,
        "_storage_snapshot",
        lambda path: {
            "value": [
                {
                    "id": str(path),
                    "kind": "filesystem",
                    "capacity_bytes": 1000,
                    "available_bytes": 400,
                }
            ],
            "provenance": "DETECTED",
            "source": "test storage probe",
            "limitations": ["no medium or bandwidth evidence"],
        },
    )
    monkeypatch.setattr(
        report,
        "_vulkan_snapshot",
        lambda: {
            "value": [],
            "provenance": "UNKNOWN",
            "source": "test Vulkan probe",
            "limitations": ["no Vulkan device in test fixture"],
        },
    )

    result = report.build_report(cwd=Path("C:/aegis-test"))

    assert result["status"] == "MEASURED_LOCAL_ONLY"
    assert result["os"]["provenance"] == "DETECTED"
    assert result["cpu"]["logical_processors"]["value"] == 4
    assert result["cpu"]["logical_processors"]["provenance"] == "DETECTED"
    assert result["cpu"]["affinity"]["value"] == 2
    assert result["memory"]["host_bytes"]["value"] == 8 * 1024**3
    assert result["memory"]["available_bytes"]["value"] == 3 * 1024**3
    assert result["memory"]["unified_memory"]["provenance"] == "UNKNOWN"
    assert result["accelerator_topology"]["provenance"] == "UNKNOWN"
    assert "empty value is not evidence" in " ".join(
        result["accelerator_topology"]["limitations"]
    ).lower()
    assert result["storage_topology"]["provenance"] == "DETECTED"
    assert result["storage_topology"]["value"][0]["available_bytes"] == 400
    assert result["storage_topology"]["limitations"] == ["no medium or bandwidth evidence"]


def test_vulkan_probe_records_identity_without_claiming_project_execution(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = """GPU0:\n    deviceName = Intel Test GPU\n    deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU\n    vendorID = 0x8086\n    driverName = Intel Test Driver\n    apiVersion = 1.3.1\n"""

    monkeypatch.setattr(report.shutil, "which", lambda name: "C:/vulkaninfo.exe")
    monkeypatch.setattr(report.subprocess, "run", lambda *args, **kwargs: Completed())

    result = report._vulkan_snapshot()

    assert result["provenance"] == "DETECTED"
    assert result["value"] == [
        {
            "backend": "Vulkan",
            "device_name": "Intel Test GPU",
            "device_type": "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU",
            "vendor_id": "0x8086",
            "driver_name": "Intel Test Driver",
            "api_version": "1.3.1",
        }
    ]
    assert "project Vulkan backend" in " ".join(result["limitations"])
