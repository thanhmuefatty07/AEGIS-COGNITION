"""Typed boundary helpers for the Rust-authoritative runtime.

The Python layer may use these helpers to inspect capability state and submit a
coarse resource proposal. It must not mutate task state or infer enforcement
that the native runtime did not report.
"""

from __future__ import annotations

import os
import platform
from typing import Any, cast

RESOURCE_CONTRACT_SCHEMA_V1 = "aegis-resource-contract-v1"


def _native_module() -> Any | None:
    try:
        import aegis_nerve  # type: ignore[import-not-found]
    except ImportError:
        try:
            from aegis_cognition import aegis_nerve  # type: ignore[import-not-found]
        except ImportError:
            return None
    return cast(Any, aegis_nerve)


def native_runtime_available() -> bool:
    """Return whether the PyO3 runtime extension is importable."""

    return _native_module() is not None


def hardware_profile() -> dict[str, Any]:
    """Return a normalized hardware profile without inventing memory data."""

    native = _native_module()
    if native is not None:
        import json

        return json.loads(native.aegis_hardware_profile())

    return {
        "schema": RESOURCE_CONTRACT_SCHEMA_V1,
        "source": "python-fallback",
        "authoritative": False,
        "cpu": {
            "architecture": platform.machine().lower() or "unknown",
            "usable_parallelism": max(1, os.cpu_count() or 1),
        },
        "memory_domains": [
            {
                "id": "host",
                "kind": "Unknown",
                "capacity_bytes": None,
                "available_bytes": None,
                "reserved_bytes": 0,
            }
        ],
        "accelerators": [],
        "os": {"backend": "python-fallback", "enforcement": "unsupported"},
        "verification": "NOT VERIFIED — native Rust runtime is not importable",
    }


def resource_contract_version() -> str:
    """Return the versioned contract identifier."""

    native = _native_module()
    if native is not None:
        return str(native.aegis_resource_contract_version())
    return RESOURCE_CONTRACT_SCHEMA_V1


def execution_lanes() -> dict[str, Any]:
    """Return Rust lane limits, or a conservative unverified fallback."""

    native = _native_module()
    if native is not None:
        import json

        return json.loads(native.aegis_execution_lanes())
    return {
        "schema": RESOURCE_CONTRACT_SCHEMA_V1,
        "authoritative": False,
        "lanes": {},
        "verification": "NOT VERIFIED — native Rust runtime is not importable",
    }


def admission_preview(request: dict[str, Any], now_ms: int | None = None) -> dict[str, Any]:
    """Ask Rust to validate one request without creating a Python scheduler.

    When the extension is unavailable the function returns an explicit
    unverified result rather than duplicating admission policy in Python.
    """

    native = _native_module()
    if native is None:
        return {
            "schema": RESOURCE_CONTRACT_SCHEMA_V1,
            "status": "native_unavailable",
            "authoritative": False,
            "verification": "NOT VERIFIED — native Rust runtime is not importable",
        }

    import json

    return json.loads(native.aegis_resource_admission_preview(json.dumps(request), now_ms))


def submit_runtime_task(
    task_id: int,
    request: dict[str, Any],
    dependency_ids: list[int] | None = None,
    now_ms: int = 0,
) -> dict[str, Any]:
    """Submit a coarse task proposal to Rust, if the native runtime is loaded."""

    native = _native_module()
    if native is None:
        return {
            "schema": "aegis-runtime-admission-v1",
            "status": "native_unavailable",
            "authoritative": False,
            "verification": "NOT VERIFIED — native Rust runtime is not importable",
        }
    import json

    return json.loads(
        native.aegis_runtime_submit(
            task_id,
            json.dumps(dependency_ids or []),
            json.dumps(request),
            now_ms,
        )
    )


def finish_runtime_lease(token: dict[str, Any], outcome: str) -> bool:
    """Finish an opaque Rust lease token with an explicit outcome."""

    native = _native_module()
    if native is None:
        return False
    import json

    return bool(native.aegis_runtime_finish(json.dumps(token), outcome))


def retry_runtime_task(task_id: int, request: dict[str, Any], now_ms: int = 0) -> dict[str, Any]:
    """Start a newer fenced attempt through the Rust-owned runtime."""

    native = _native_module()
    if native is None:
        return {
            "schema": "aegis-runtime-admission-v1",
            "status": "native_unavailable",
            "authoritative": False,
            "verification": "NOT VERIFIED — native Rust runtime is not importable",
        }
    import json

    return json.loads(native.aegis_runtime_retry(task_id, json.dumps(request), now_ms))
