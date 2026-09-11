"""Typed boundary helpers for the Rust-authoritative runtime.

The Python layer may use these helpers to inspect capability state and submit a
coarse resource proposal. It must not mutate task state or infer enforcement
that the native runtime did not report.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from collections.abc import AsyncGenerator, Generator
from typing import Any, cast

RESOURCE_CONTRACT_SCHEMA_V1 = "aegis-resource-contract-v1"
RUNTIME_ADMISSION_SCHEMA_V1 = "aegis-runtime-admission-v1"
COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1 = "aegis-cooperative-placement-preview-v1"
COOPERATIVE_PLACEMENT_RUN_SCHEMA_V1 = "aegis-cooperative-placement-run-v1"
COOPERATIVE_ADMISSION_SCHEMA_V1 = "aegis-cooperative-admission-v1"
COOPERATIVE_RUNTIME_SCHEMA_V1 = "aegis-cooperative-runtime-v1"
DEFAULT_RUNTIME_MEMORY_BYTES = 64 * 1024 * 1024
_RUNTIME_TRUST_LEVELS = frozenset({"DEV", "STAGING", "PROD"})
_RUNTIME_OUTCOMES = frozenset(
    {
        "done",
        "failed",
        "retry_wait",
        "retry-wait",
        "cancelled",
        "canceled",
        "timed_out",
        "timed-out",
        "timeout",
    }
)


class RuntimeCoordinationError(RuntimeError):
    """The native runtime could not authoritatively coordinate one task."""


def normalize_runtime_trust_level(value: object) -> str:
    """Normalize the caller policy used by runtime authority boundaries."""

    if type(value) is not str:
        raise ValueError("runtime trust level must be a string")
    normalized = value.strip().upper()
    if normalized not in _RUNTIME_TRUST_LEVELS:
        raise ValueError("runtime trust level must be DEV, STAGING, or PROD")
    return normalized


@dataclass
class RuntimeLease:
    """A bounded runtime lease, or an explicitly non-authoritative fallback."""

    task_id: int
    attempt_id: int
    token: dict[str, Any] | None
    authoritative: bool
    _finished: bool = False

    def finish(self, outcome: str = "done") -> None:
        if self._finished:
            return
        if not self.authoritative:
            self._finished = True
            return
        if self.token is None:
            raise RuntimeCoordinationError("authoritative runtime lease has no token")
        try:
            finished = finish_runtime_lease(self.token, outcome)
        except Exception as error:
            raise RuntimeCoordinationError("native runtime lease finish failed") from error
        if not finished:
            raise RuntimeCoordinationError("native runtime lease was not released")
        self._finished = True


def _native_module() -> Any | None:
    try:
        import aegis_nerve  # type: ignore[import-not-found]
    except ImportError:
        try:
            from aegis_cognition import aegis_nerve  # type: ignore[import-not-found]
        except ImportError:
            return None
    return cast(Any, aegis_nerve)


def _unix_time_ms() -> int:
    return max(1, int(time.time() * 1000))


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _positive_timeout(value: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("timeout_seconds must be a positive finite number")
    timeout = float(value)
    if timeout <= 0 or timeout == float("inf") or timeout != timeout:
        raise ValueError("timeout_seconds must be a positive finite number")
    return timeout


def _strict_native_bool(value: object, operation: str) -> bool:
    """Reject a malformed native boolean instead of truthiness-coercing it."""

    if type(value) is not bool:
        raise RuntimeCoordinationError(f"{operation} returned a non-boolean result")
    return value


def _runtime_request(
    *,
    task_id: int,
    work_kind: str,
    attempt_id: int,
    timeout_seconds: float,
    memory_bytes: int,
    priority: str,
    side_effect_class: str,
    now_ms: int,
) -> dict[str, Any]:
    _positive_int(task_id, "task_id")
    _positive_int(attempt_id, "attempt_id")
    _positive_int(memory_bytes, "memory_bytes")
    timeout = _positive_timeout(timeout_seconds)
    if type(work_kind) is not str or not work_kind.strip():
        raise ValueError("work_kind must be a non-empty string")
    if type(priority) is not str or not priority.strip():
        raise ValueError("priority must be a non-empty string")
    if type(side_effect_class) is not str or not side_effect_class.strip():
        raise ValueError("side_effect_class must be a non-empty string")
    if type(now_ms) is not int or now_ms < 1:
        raise ValueError("now_ms must be a positive integer")
    deadline_ms = now_ms + max(1, int(timeout * 1000))
    return {
        "schema": RESOURCE_CONTRACT_SCHEMA_V1,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "work_kind": work_kind,
        "cpu": {"min_threads": 1, "max_threads": 1},
        "host_memory": {"bytes": memory_bytes},
        "accelerator": None,
        "accelerator_memory": None,
        "io": {"max_in_flight": 1},
        "process_limit": 1,
        "thread_limit": 1,
        "fd_limit": 256,
        "api_budget": None,
        "token_budget": None,
        "deadline": {"deadline_ms": deadline_ms},
        "priority": priority,
        "side_effect_class": side_effect_class,
    }


def _parse_admission(response: object, *, source: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(response, dict):
        raise RuntimeCoordinationError(f"{source} returned a non-object response")
    payload = cast(dict[str, Any], response)
    if payload.get("schema") != RUNTIME_ADMISSION_SCHEMA_V1:
        raise RuntimeCoordinationError(f"{source} returned an unsupported admission schema")
    status = payload.get("status")
    if status not in {"admitted", "queued", "pending", "rejected"}:
        raise RuntimeCoordinationError(f"{source} returned an unknown admission status")
    return cast(str, status), payload


def _lease_from_response(response: dict[str, Any], *, task_id: int, attempt_id: int) -> RuntimeLease:
    token = response.get("lease_token")
    if not isinstance(token, dict) or not token:
        raise RuntimeCoordinationError("admitted runtime response has no lease token")
    return RuntimeLease(
        task_id=task_id,
        attempt_id=attempt_id,
        token=cast(dict[str, Any], token),
        authoritative=True,
    )


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


def placement_capabilities() -> list[dict[str, Any]]:
    """Return the native inventory used as input to the placement planner."""

    native = _native_module()
    if native is None:
        return [
            {
                "id": "cpu",
                "domain": "Cpu",
                "usable_bytes": None,
                "compute_units_per_us": None,
                "bandwidth_bytes_per_s": None,
                "latency_us": None,
                "queue_depth": 0,
                "queue_capacity": max(1, os.cpu_count() or 1),
                "pressure": False,
                "local_only": True,
                "confidence": "Unknown",
            }
        ]
    return cast(list[dict[str, Any]], json.loads(native.aegis_placement_capabilities()))


def _validate_cooperative_placement_inputs(task: object, paths: object) -> None:
    if type(task) is not dict:
        raise TypeError("task must be an object")
    if paths is not None and type(paths) is not list:
        raise TypeError("paths must be a list when provided")


def calibrate_placement(
    cpu_iterations: int = 1_000_000,
    storage_bytes: int | None = 1 * 1024 * 1024,
) -> dict[str, Any]:
    """Run explicit bounded CPU/filesystem calibration for placement costs."""

    _positive_int(cpu_iterations, "cpu_iterations")
    if storage_bytes is not None:
        _positive_int(storage_bytes, "storage_bytes")
    native = _native_module()
    if native is None:
        return {
            "schema": "aegis-placement-measurement-v1",
            "measurements": [],
            "authoritative": False,
            "verification": "NOT VERIFIED — native Rust planner is not importable",
        }
    result = cast(
        dict[str, Any],
        json.loads(native.aegis_placement_calibrate(cpu_iterations, storage_bytes)),
    )
    _validated_placement_measurements(result)
    return result


def _validated_placement_measurements(
    calibration: object,
) -> list[dict[str, Any]]:
    if not isinstance(calibration, dict):
        raise RuntimeCoordinationError("placement calibration is not an object")
    payload = cast(dict[str, Any], calibration)
    if payload.get("schema") != "aegis-placement-measurement-v1":
        raise RuntimeCoordinationError("placement calibration has an unsupported schema")
    measurements = payload.get("measurements")
    if not isinstance(measurements, list):
        raise RuntimeCoordinationError("placement calibration measurements are not a list")

    valid: list[dict[str, Any]] = []
    domains = {"Cpu", "HostMemory", "Storage", "Accelerator"}
    confidences = {"Measured", "Estimated", "Unknown"}
    for index, raw_measurement in enumerate(cast(list[object], measurements)):
        if not isinstance(raw_measurement, dict):
            raise RuntimeCoordinationError(f"placement calibration measurement {index} is not an object")
        measurement = cast(dict[str, Any], raw_measurement)
        if measurement.get("schema") != "aegis-placement-measurement-v1":
            raise RuntimeCoordinationError(f"placement calibration measurement {index} has an unsupported schema")
        for field in ("resource_id", "workload"):
            value = measurement.get(field)
            if type(value) is not str or not value.strip():
                raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid {field}")
        if measurement.get("domain") not in domains:
            raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid domain")
        if measurement.get("confidence") not in confidences:
            raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid confidence")
        for field in ("bytes", "work_units"):
            value = measurement.get(field)
            if type(value) is not int or value < 0:
                raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid {field}")
        elapsed_us = measurement.get("elapsed_us")
        if type(elapsed_us) is not int or elapsed_us <= 0:
            raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid elapsed_us")
        for field in ("compute_units_per_us", "bandwidth_bytes_per_s"):
            value = measurement.get(field)
            if value is not None and (type(value) is not int or value <= 0):
                raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid {field}")
        latency_us = measurement.get("latency_us")
        if latency_us is not None and (type(latency_us) is not int or latency_us < 0):
            raise RuntimeCoordinationError(f"placement calibration measurement {index} has invalid latency_us")
        if measurement.get("confidence") == "Measured" and all(
            measurement.get(field) is None
            for field in (
                "compute_units_per_us",
                "bandwidth_bytes_per_s",
                "latency_us",
            )
        ):
            raise RuntimeCoordinationError(f"placement calibration measurement {index} has no measured cost")
        valid.append(measurement)
    return valid


def _merge_placement_calibration(capabilities: list[dict[str, Any]], calibration: dict[str, Any]) -> None:
    by_id = {
        str(capability.get("id")): capability for capability in capabilities if isinstance(capability.get("id"), str)
    }
    measurements = _validated_placement_measurements(calibration)
    for measurement in measurements:
        target = by_id.get(str(measurement.get("resource_id")))
        if target is None:
            continue
        if measurement.get("domain") != target.get("domain"):
            raise RuntimeCoordinationError("placement calibration measurement domain does not match capability")
        if measurement.get("confidence") != "Measured":
            continue
        for field in (
            "compute_units_per_us",
            "bandwidth_bytes_per_s",
            "latency_us",
        ):
            if measurement.get(field) is not None:
                target[field] = measurement[field]
        target["confidence"] = "Measured"


def placement_plan_with_calibration(
    task: dict[str, Any],
    *,
    cpu_iterations: int = 1_000_000,
    storage_bytes: int | None = 1 * 1024 * 1024,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Calibrate locally once, merge measured costs, and build one plan.

    Calibration is opt-in because filesystem calibration writes a bounded
    temporary file and synthetic CPU work is not a universal application
    benchmark.  The returned envelope preserves calibration and capabilities
    so the decision can be audited instead of silently cached.
    """

    capabilities = placement_capabilities()
    calibration = calibrate_placement(cpu_iterations, storage_bytes)
    _merge_placement_calibration(capabilities, calibration)
    return {
        "schema": "aegis-placement-run-v1",
        "calibration": calibration,
        "capabilities": capabilities,
        "plan": placement_plan(task, capabilities, now_ms=now_ms),
    }


def placement_plan(
    task: dict[str, Any],
    capabilities: list[dict[str, Any]],
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Ask Rust to choose a resource using measured transfer and queue costs."""

    native = _native_module()
    if native is None:
        return {
            "schema": "aegis-placement-plan-v1",
            "decision": "native_unavailable",
            "selected": None,
            "executor": None,
            "data_tier": None,
            "authoritative": False,
            "verification": "NOT VERIFIED — native Rust planner is not importable",
        }
    current_ms = _unix_time_ms() if now_ms is None else now_ms
    if type(current_ms) is not int or current_ms < 1:
        raise ValueError("now_ms must be a positive integer")
    return json.loads(
        native.aegis_placement_plan(
            json.dumps(task),
            json.dumps(capabilities),
            current_ms,
        )
    )


def cooperative_placement_preview(
    task: dict[str, Any],
    capabilities: list[dict[str, Any]],
    paths: list[dict[str, Any]] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Return a planner-only cooperative placement preview.

    This boundary intentionally cannot submit work or create a resource
    lease.  A missing/older native extension returns an explicit
    non-authoritative envelope, while a malformed native envelope fails
    closed rather than being treated as executable.
    """

    _validate_cooperative_placement_inputs(task, paths)
    if type(capabilities) is not list:
        raise TypeError("capabilities must be a list")

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "schema": COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1,
            "authority": "planner_only",
            "executable": False,
            "reservation_status": "NOT_ADMITTED",
            "plan": {
                "schema": "aegis-cooperative-placement-plan-v1",
                "decision": "native_unavailable",
            },
            "verification": reason,
        }

    native = _native_module()
    if native is None:
        return unavailable("NOT VERIFIED — native Rust planner is not importable")
    preview = getattr(native, "aegis_cooperative_placement_preview", None)
    if not callable(preview):
        return unavailable("NOT VERIFIED — native extension lacks cooperative placement preview")

    current_ms = _unix_time_ms() if now_ms is None else now_ms
    if type(current_ms) is not int or current_ms < 1:
        raise ValueError("now_ms must be a positive integer")
    raw_result = cast(
        str,
        preview(
            json.dumps(task),
            json.dumps(capabilities),
            json.dumps([] if paths is None else paths),
            current_ms,
        ),
    )
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeCoordinationError("cooperative placement preview returned invalid JSON") from error
    if type(result) is not dict:
        raise RuntimeCoordinationError("cooperative placement preview is not an object")
    result_object = cast(dict[str, Any], result)
    if (
        result_object.get("schema") != COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1
        or result_object.get("authority") != "planner_only"
        or result_object.get("executable") is not False
        or result_object.get("reservation_status") != "NOT_ADMITTED"
        or type(result_object.get("plan")) is not dict
    ):
        raise RuntimeCoordinationError("cooperative placement preview violated its non-executable contract")
    return result_object


def cooperative_placement_preview_with_calibration(
    task: dict[str, Any],
    paths: list[dict[str, Any]] | None = None,
    *,
    cpu_iterations: int = 1_000_000,
    storage_bytes: int | None = 1 * 1024 * 1024,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Calibrate known local resources, then build one cooperative preview.

    Calibration only fills matching capabilities with explicitly measured
    fields.  It does not invent accelerator metadata, transfer-token limits,
    or a CPU-to-storage path from filesystem capacity alone.
    """

    _validate_cooperative_placement_inputs(task, paths)
    capabilities = placement_capabilities()
    calibration = calibrate_placement(cpu_iterations, storage_bytes)
    _merge_placement_calibration(capabilities, calibration)
    return {
        "schema": COOPERATIVE_PLACEMENT_RUN_SCHEMA_V1,
        "calibration": calibration,
        "capabilities": capabilities,
        "preview": cooperative_placement_preview(task, capabilities, paths, now_ms=now_ms),
    }


def admit_cooperative_placement(
    task: dict[str, Any],
    capabilities: list[dict[str, Any]],
    paths: list[dict[str, Any]] | None = None,
    *,
    attempt_id: int = 1,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Build and admit a cooperative placement through Rust.

    The native call rebuilds the plan and binds the admission to the sorted
    inventory digest.  A deferred plan is returned as non-executable; only an
    ``admitted`` or ``already_admitted`` response carries a usable lease.
    """

    if type(task) is not dict or type(capabilities) is not list:
        raise TypeError("task and capabilities must be an object/list")
    if paths is not None and type(paths) is not list:
        raise TypeError("paths must be a list when provided")
    _positive_int(attempt_id, "attempt_id")
    current_ms = _unix_time_ms() if now_ms is None else now_ms
    if type(current_ms) is not int or current_ms < 1:
        raise ValueError("now_ms must be a positive integer")

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "schema": COOPERATIVE_ADMISSION_SCHEMA_V1,
            "authority": "unverified",
            "status": "native_unavailable",
            "executable": False,
            "reservation_status": "NOT_ADMITTED",
            "verification": reason,
        }

    native = _native_module()
    if native is None:
        return unavailable("NOT VERIFIED — native Rust runtime is not importable")
    admit = getattr(native, "aegis_cooperative_placement_admit", None)
    if not callable(admit):
        return unavailable("NOT VERIFIED — native extension lacks cooperative admission")
    raw_result = cast(
        str,
        admit(
            json.dumps(task),
            json.dumps(capabilities),
            json.dumps([] if paths is None else paths),
            attempt_id,
            current_ms,
        ),
    )
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeCoordinationError("cooperative admission returned invalid JSON") from error
    if type(result) is not dict:
        raise RuntimeCoordinationError("cooperative admission is not an object")
    result_object = cast(dict[str, Any], result)
    status = result_object.get("status")
    if (
        result_object.get("schema") != COOPERATIVE_ADMISSION_SCHEMA_V1
        or result_object.get("authority") != "native_runtime"
        or status not in {"admitted", "already_admitted", "deferred"}
        or type(result_object.get("executable")) is not bool
    ):
        raise RuntimeCoordinationError("cooperative admission violated its contract")
    if status in {"admitted", "already_admitted"}:
        if result_object.get("executable") is not True or type(result_object.get("lease")) is not dict:
            raise RuntimeCoordinationError("cooperative admission omitted its lease")
    elif result_object.get("executable") is not False:
        raise RuntimeCoordinationError("deferred cooperative placement was executable")
    return result_object


def release_cooperative_placement(lease: dict[str, Any]) -> bool:
    """Release one exact native cooperative admission lease."""

    if type(lease) is not dict or not lease:
        raise TypeError("cooperative lease must be a non-empty object")
    native = _native_module()
    if native is None:
        return False
    release = getattr(native, "aegis_cooperative_placement_release", None)
    if not callable(release):
        return False
    return _strict_native_bool(release(json.dumps(lease)), "cooperative placement release")


def configure_cooperative_runtime(
    mode: str,
    capabilities: list[dict[str, Any]],
    paths: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Configure the H2c-B runtime lifecycle from one observed inventory."""

    if type(mode) is not str:
        raise TypeError("cooperative runtime mode must be a string")
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"disabled", "shadow", "enforced"}:
        raise ValueError("cooperative runtime mode must be disabled, shadow, or enforced")
    if type(capabilities) is not list:
        raise TypeError("capabilities must be a list")
    if paths is not None and type(paths) is not list:
        raise TypeError("paths must be a list when provided")

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "schema": COOPERATIVE_RUNTIME_SCHEMA_V1,
            "authority": "unverified",
            "status": "native_unavailable",
            "mode": normalized_mode,
            "executable": False,
            "verification": reason,
        }

    native = _native_module()
    if native is None:
        return unavailable("NOT VERIFIED — native Rust runtime is not importable")
    configure = getattr(native, "aegis_runtime_cooperative_configure", None)
    if not callable(configure):
        return unavailable("NOT VERIFIED — native extension lacks cooperative runtime")
    raw_result = cast(
        str,
        configure(
            normalized_mode,
            json.dumps(capabilities),
            json.dumps([] if paths is None else paths),
        ),
    )
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeCoordinationError("cooperative runtime configuration returned invalid JSON") from error
    if type(result) is not dict:
        raise RuntimeCoordinationError("cooperative runtime configuration is not an object")
    result_object = cast(dict[str, Any], result)
    if (
        result_object.get("schema") != COOPERATIVE_RUNTIME_SCHEMA_V1
        or result_object.get("authority") != "authoritative_runtime"
        or result_object.get("status") != "configured"
        or result_object.get("mode") != normalized_mode
        or type(result_object.get("executable")) is not bool
        or type(result_object.get("inventory_hash")) is not str
    ):
        raise RuntimeCoordinationError("cooperative runtime configuration violated its contract")
    return result_object


def submit_cooperative_runtime(
    request: dict[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Submit one planned cooperative request and return an opaque handle."""

    if type(request) is not dict:
        raise TypeError("cooperative runtime request must be an object")
    current_ms = _unix_time_ms() if now_ms is None else now_ms
    if type(current_ms) is not int or current_ms < 1:
        raise ValueError("now_ms must be a positive integer")

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "schema": COOPERATIVE_RUNTIME_SCHEMA_V1,
            "authority": "unverified",
            "status": "native_unavailable",
            "executable": False,
            "reservation_status": "NOT_ADMITTED",
            "verification": reason,
        }

    native = _native_module()
    if native is None:
        return unavailable("NOT VERIFIED — native Rust runtime is not importable")
    submit = getattr(native, "aegis_runtime_cooperative_submit", None)
    if not callable(submit):
        return unavailable("NOT VERIFIED — native extension lacks cooperative runtime")
    raw_result = cast(str, submit(json.dumps(request), current_ms))
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeCoordinationError("cooperative runtime submit returned invalid JSON") from error
    if type(result) is not dict:
        raise RuntimeCoordinationError("cooperative runtime submit is not an object")
    result_object = cast(dict[str, Any], result)
    status = result_object.get("status")
    if (
        result_object.get("schema") != COOPERATIVE_RUNTIME_SCHEMA_V1
        or result_object.get("authority") != "authoritative_runtime"
        or status not in {"admitted", "shadow"}
        or type(result_object.get("executable")) is not bool
    ):
        raise RuntimeCoordinationError("cooperative runtime submit violated its contract")
    if status == "admitted":
        if (
            result_object.get("executable") is not True
            or result_object.get("reservation_status") != "ADMITTED"
            or type(result_object.get("handle")) is not int
            or isinstance(result_object.get("handle"), bool)
            or result_object.get("handle", 0) <= 0
        ):
            raise RuntimeCoordinationError("cooperative runtime admission omitted its handle")
    elif (
        result_object.get("executable") is not False
        or result_object.get("reservation_status") != "VALIDATED_NOT_ADMITTED"
        or result_object.get("handle") is not None
    ):
        raise RuntimeCoordinationError("cooperative runtime shadow result was executable")
    return result_object


def finish_cooperative_runtime(handle: int, outcome: str = "done") -> bool:
    """Release one opaque cooperative runtime handle."""

    _positive_int(handle, "cooperative runtime handle")
    if type(outcome) is not str or outcome.strip().lower() not in _RUNTIME_OUTCOMES:
        raise ValueError("invalid cooperative runtime outcome")
    native = _native_module()
    if native is None:
        return False
    finish = getattr(native, "aegis_runtime_cooperative_finish", None)
    if not callable(finish):
        return False
    return _strict_native_bool(finish(handle, outcome), "cooperative runtime finish")


def cancel_cooperative_runtime(handle: int) -> bool:
    """Cancel and release one opaque cooperative runtime handle."""

    _positive_int(handle, "cooperative runtime handle")
    native = _native_module()
    if native is None:
        return False
    cancel = getattr(native, "aegis_runtime_cooperative_cancel", None)
    if not callable(cancel):
        return False
    return _strict_native_bool(cancel(handle), "cooperative runtime cancel")


def placement_resource_request(
    task: dict[str, Any],
    plan: dict[str, Any],
    *,
    attempt_id: int = 1,
    timeout_seconds: float = 60.0,
    work_kind: str = "NativeTask",
    priority: str = "Normal",
    side_effect_class: str = "ReadOnly",
) -> dict[str, Any]:
    """Translate a selected placement into the typed runtime request.

    The planner chooses placement; Rust still owns admission and validates the
    resulting request.  A storage tier contributes bounded I/O and only a
    window-sized host-memory reservation, while a host-memory tier reserves
    the complete requested data residency.  Accelerator plans require the
    capability metadata emitted by a real adapter; they are never guessed.
    """

    if type(task) is not dict or type(plan) is not dict:
        raise TypeError("placement task and plan must be objects")
    if plan.get("schema") != "aegis-placement-plan-v1":
        raise RuntimeCoordinationError("placement plan schema is unsupported")
    if plan.get("decision") != "selected":
        raise RuntimeCoordinationError(f"placement plan is not executable: {plan.get('decision')}")
    task_id = _positive_int(task.get("task_id"), "task_id")
    if plan.get("task_id") not in (None, task_id):
        raise RuntimeCoordinationError("placement plan task identity does not match request")
    _positive_int(attempt_id, "attempt_id")
    timeout = _positive_timeout(timeout_seconds)
    if type(work_kind) is not str or not work_kind.strip():
        raise ValueError("work_kind must be a non-empty string")
    if type(priority) is not str or not priority.strip():
        raise ValueError("priority must be a non-empty string")
    if type(side_effect_class) is not str or not side_effect_class.strip():
        raise ValueError("side_effect_class must be a non-empty string")

    def nonnegative_int(value: object, field: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return value

    input_bytes = nonnegative_int(task.get("input_bytes", 0), "input_bytes")
    output_bytes = nonnegative_int(task.get("output_bytes", 0), "output_bytes")
    working_memory = nonnegative_int(task.get("working_memory_bytes", 0), "working_memory_bytes")
    cpu_work = nonnegative_int(task.get("cpu_work_units", 0), "cpu_work_units")
    accelerator_work = nonnegative_int(task.get("accelerator_work_units", 0), "accelerator_work_units")
    data_bytes = input_bytes + output_bytes
    if working_memory == 0 and data_bytes == 0:
        raise ValueError("placement task must request memory or data")

    candidates = plan.get("candidates")
    if type(candidates) is not list:
        raise RuntimeCoordinationError("selected placement has no candidate list")
    raw_candidates = cast(list[object], candidates)

    def candidate_for(resource_id: object) -> dict[str, Any] | None:
        if type(resource_id) is not str or not resource_id.strip():
            return None
        for raw_candidate in raw_candidates:
            if isinstance(raw_candidate, dict):
                candidate = cast(dict[str, Any], raw_candidate)
                if candidate.get("id") == resource_id:
                    return candidate
        return None

    executor_id = plan.get("executor")
    data_tier_id = plan.get("data_tier")
    executor_candidate = candidate_for(executor_id)
    data_candidate = candidate_for(data_tier_id)
    if executor_id is not None and executor_candidate is None:
        raise RuntimeCoordinationError("placement executor is missing from candidates")
    if data_bytes > 0 and data_candidate is None:
        raise RuntimeCoordinationError("placement data tier is missing from candidates")

    executor_mapping: dict[str, Any] = executor_candidate or {}
    data_mapping: dict[str, Any] = data_candidate or {}
    executor_domain = executor_mapping.get("domain")
    data_domain = data_mapping.get("domain")
    if cpu_work + accelerator_work > 0 and executor_candidate is None:
        raise RuntimeCoordinationError("compute placement is missing from selected plan")
    if accelerator_work > 0 and executor_domain != "Accelerator":
        raise RuntimeCoordinationError("accelerator work requires an accelerator executor")
    if executor_domain not in {None, "Cpu", "Accelerator"}:
        raise RuntimeCoordinationError("placement executor is not a compute domain")
    if data_domain not in {None, "HostMemory", "Storage"}:
        raise RuntimeCoordinationError("placement data tier is not a residency domain")

    cpu_threads = task.get("cpu_threads", 1)
    cpu_threads = _positive_int(cpu_threads, "cpu_threads")
    data_window = task.get("data_window_bytes", min(max(data_bytes, 1), 64 * 1024))
    data_window = _positive_int(data_window, "data_window_bytes")
    host_memory_bytes = working_memory
    if data_domain == "HostMemory":
        host_memory_bytes += data_bytes
    elif data_domain == "Storage":
        host_memory_bytes += min(data_bytes, data_window)
    host_memory_bytes = max(1, host_memory_bytes)

    accelerator_request: dict[str, Any] | None = None
    if executor_domain == "Accelerator":
        kind = executor_mapping.get("accelerator_kind")
        backend = executor_mapping.get("backend")
        raw_capabilities = executor_mapping.get("capabilities", [])
        if type(raw_capabilities) is not list:
            raise RuntimeCoordinationError("accelerator placement has invalid capability metadata")
        raw_required_capabilities = cast(list[object], raw_capabilities)
        if any(type(capability) is not str or not capability.strip() for capability in raw_required_capabilities):
            raise RuntimeCoordinationError("accelerator placement has invalid capability metadata")
        required_capabilities = cast(list[str], raw_required_capabilities)
        if type(kind) is not str or not kind.strip():
            raise RuntimeCoordinationError("accelerator placement lacks adapter kind metadata")
        if type(backend) is not str or not backend.strip():
            raise RuntimeCoordinationError("accelerator placement lacks adapter backend metadata")
        accelerator_memory = _positive_int(
            task.get("accelerator_memory_bytes", max(1, working_memory)),
            "accelerator_memory_bytes",
        )
        accelerator_request = {
            "kind": kind,
            "backend": backend,
            "required_capabilities": required_capabilities,
            "memory": {"bytes": accelerator_memory},
        }
        work_kind = "Accelerator"

    deadline_ms = task.get("deadline_ms")
    if deadline_ms is not None:
        _positive_int(deadline_ms, "deadline_ms")
    now_ms = _unix_time_ms()
    derived_deadline = now_ms + max(1, int(timeout * 1000))
    return {
        "schema": RESOURCE_CONTRACT_SCHEMA_V1,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "work_kind": work_kind,
        "cpu": {"min_threads": cpu_threads, "max_threads": cpu_threads},
        "host_memory": {"bytes": host_memory_bytes},
        "accelerator": accelerator_request,
        "accelerator_memory": None,
        "io": {"max_in_flight": _positive_int(task.get("io_in_flight", 1), "io_in_flight")},
        "process_limit": 1,
        "thread_limit": cpu_threads,
        "fd_limit": 256,
        "api_budget": None,
        "token_budget": None,
        "deadline": {"deadline_ms": deadline_ms or derived_deadline},
        "priority": priority,
        "side_effect_class": side_effect_class,
    }


def submit_placement_task(
    task: dict[str, Any],
    plan: dict[str, Any],
    *,
    dependency_ids: list[int] | None = None,
    now_ms: int = 0,
    attempt_id: int = 1,
    timeout_seconds: float = 60.0,
    work_kind: str = "NativeTask",
    priority: str = "Normal",
    side_effect_class: str = "ReadOnly",
) -> dict[str, Any]:
    """Submit one selected placement through the Rust admission authority."""

    task_id = _positive_int(task.get("task_id"), "task_id")
    request = placement_resource_request(
        task,
        plan,
        attempt_id=attempt_id,
        timeout_seconds=timeout_seconds,
        work_kind=work_kind,
        priority=priority,
        side_effect_class=side_effect_class,
    )
    return submit_runtime_task(task_id, request, dependency_ids, now_ms)


def resource_usage_sample() -> dict[str, Any]:
    """Return an observation-only native resource sample."""

    native = _native_module()
    if native is not None:
        import json

        return json.loads(native.aegis_resource_usage_sample())
    return {
        "schema": RESOURCE_CONTRACT_SCHEMA_V1,
        "authoritative": False,
        "sampled_at_ms": 0,
        "cpu_threads_active": 0,
        "host_memory_bytes": None,
        "host_memory_available_bytes": None,
        "queue_depth": 0,
        "memory_pressure": False,
        "verification": "NOT VERIFIED — native Rust runtime is not importable",
    }


def observe_runtime_resources() -> dict[str, Any]:
    """Sample host pressure and feed it into native admission headroom."""

    native = _native_module()
    if native is None:
        return {
            "schema": RESOURCE_CONTRACT_SCHEMA_V1,
            "authoritative": False,
            "sample": resource_usage_sample(),
            "feedback": None,
            "verification": "NOT VERIFIED — native Rust runtime is not importable",
        }
    return cast(dict[str, Any], json.loads(native.aegis_runtime_observe_resources()))


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


def poll_runtime_task(task_id: int, attempt_id: int = 1, now_ms: int | None = None) -> dict[str, Any]:
    """Poll one queued task through the native runtime authority."""

    native = _native_module()
    if native is None:
        return {
            "schema": RUNTIME_ADMISSION_SCHEMA_V1,
            "status": "native_unavailable",
            "authoritative": False,
            "verification": "NOT VERIFIED — native Rust runtime is not importable",
        }
    _positive_int(task_id, "task_id")
    _positive_int(attempt_id, "attempt_id")
    current_ms = _unix_time_ms() if now_ms is None else now_ms
    if type(current_ms) is not int or current_ms < 1:
        raise ValueError("now_ms must be a positive integer")
    return json.loads(native.aegis_runtime_poll(task_id, attempt_id, current_ms))


def cancel_runtime_task(task_id: int, attempt_id: int = 1) -> bool:
    """Cancel one queued task; admitted work must be finished by its lease."""

    _positive_int(task_id, "task_id")
    _positive_int(attempt_id, "attempt_id")
    native = _native_module()
    if native is None:
        return False
    return _strict_native_bool(native.aegis_runtime_cancel(task_id, attempt_id), "runtime cancellation")


def finish_runtime_lease(token: dict[str, Any], outcome: str) -> bool:
    """Finish an opaque Rust lease token with an explicit outcome."""

    if type(token) is not dict or not token:
        raise TypeError("runtime lease token must be a non-empty object")
    if type(outcome) is not str or not outcome.strip():
        raise ValueError("runtime lease outcome must be non-empty text")
    normalized_outcome = outcome.strip().lower()
    if normalized_outcome not in _RUNTIME_OUTCOMES:
        raise ValueError("runtime lease outcome is unsupported")
    native = _native_module()
    if native is None:
        return False
    import json

    return _strict_native_bool(
        native.aegis_runtime_finish(json.dumps(token), normalized_outcome),
        "runtime lease finish",
    )


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


def _cancel_queued_after_abort(task_id: int, attempt_id: int) -> None:
    """Cancel a queued task, reclaiming a lease if admission won a race."""

    if cancel_runtime_task(task_id, attempt_id):
        return
    status, response = _parse_admission(poll_runtime_task(task_id, attempt_id), source="runtime abort poll")
    if status == "admitted":
        lease = _lease_from_response(response, task_id=task_id, attempt_id=attempt_id)
        lease.finish("cancelled")


async def acquire_runtime_task_async(
    *,
    task_id: int,
    work_kind: str = "Agent",
    attempt_id: int = 1,
    timeout_seconds: float = 60.0,
    memory_bytes: int = DEFAULT_RUNTIME_MEMORY_BYTES,
    priority: str = "Foreground",
    side_effect_class: str = "ExternalSideEffect",
    trust_level: str = "DEV",
) -> RuntimeLease:
    """Acquire a bounded native lease, waiting only for a finite deadline."""

    normalized_trust_level = normalize_runtime_trust_level(trust_level)
    timeout = _positive_timeout(timeout_seconds)
    now_ms = _unix_time_ms()
    request = _runtime_request(
        task_id=task_id,
        work_kind=work_kind,
        attempt_id=attempt_id,
        timeout_seconds=timeout,
        memory_bytes=memory_bytes,
        priority=priority,
        side_effect_class=side_effect_class,
        now_ms=now_ms,
    )
    native = _native_module()
    if native is None:
        if normalized_trust_level == "PROD":
            raise RuntimeCoordinationError("PROD runtime requires authoritative native coordination")
        return RuntimeLease(task_id, attempt_id, None, authoritative=False)
    try:
        status, response = _parse_admission(
            submit_runtime_task(task_id, request, now_ms=now_ms), source="runtime submit"
        )
    except RuntimeCoordinationError:
        raise
    except Exception as error:
        raise RuntimeCoordinationError("native runtime submit failed") from error
    if status == "admitted":
        return _lease_from_response(response, task_id=task_id, attempt_id=attempt_id)
    if status == "rejected":
        raise RuntimeCoordinationError(f"runtime admission rejected: {response.get('reason')}")
    if status not in {"queued", "pending"}:
        raise RuntimeCoordinationError(f"runtime submit returned unexpected status: {status}")

    deadline = time.monotonic() + timeout
    try:
        while True:
            status, response = _parse_admission(poll_runtime_task(task_id, attempt_id), source="runtime poll")
            if status == "admitted":
                return _lease_from_response(response, task_id=task_id, attempt_id=attempt_id)
            if status == "rejected":
                raise RuntimeCoordinationError(f"runtime admission rejected: {response.get('reason')}")
            if status not in {"queued", "pending"}:
                raise RuntimeCoordinationError(f"runtime poll returned unexpected status: {status}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _cancel_queued_after_abort(task_id, attempt_id)
                raise RuntimeCoordinationError("runtime queue wait exceeded timeout")
            await asyncio.sleep(min(0.05, remaining))
    except asyncio.CancelledError:
        _cancel_queued_after_abort(task_id, attempt_id)
        raise


def acquire_runtime_task(
    *,
    task_id: int,
    work_kind: str = "Agent",
    attempt_id: int = 1,
    timeout_seconds: float = 60.0,
    memory_bytes: int = DEFAULT_RUNTIME_MEMORY_BYTES,
    priority: str = "Foreground",
    side_effect_class: str = "ExternalSideEffect",
    trust_level: str = "DEV",
) -> RuntimeLease:
    """Synchronous counterpart of :func:`acquire_runtime_task_async`."""

    normalized_trust_level = normalize_runtime_trust_level(trust_level)
    timeout = _positive_timeout(timeout_seconds)
    now_ms = _unix_time_ms()
    request = _runtime_request(
        task_id=task_id,
        work_kind=work_kind,
        attempt_id=attempt_id,
        timeout_seconds=timeout,
        memory_bytes=memory_bytes,
        priority=priority,
        side_effect_class=side_effect_class,
        now_ms=now_ms,
    )
    native = _native_module()
    if native is None:
        if normalized_trust_level == "PROD":
            raise RuntimeCoordinationError("PROD runtime requires authoritative native coordination")
        return RuntimeLease(task_id, attempt_id, None, authoritative=False)
    try:
        status, response = _parse_admission(
            submit_runtime_task(task_id, request, now_ms=now_ms), source="runtime submit"
        )
    except RuntimeCoordinationError:
        raise
    except Exception as error:
        raise RuntimeCoordinationError("native runtime submit failed") from error
    if status == "admitted":
        return _lease_from_response(response, task_id=task_id, attempt_id=attempt_id)
    if status == "rejected":
        raise RuntimeCoordinationError(f"runtime admission rejected: {response.get('reason')}")
    if status not in {"queued", "pending"}:
        raise RuntimeCoordinationError(f"runtime submit returned unexpected status: {status}")

    deadline = time.monotonic() + timeout
    while True:
        status, response = _parse_admission(poll_runtime_task(task_id, attempt_id), source="runtime poll")
        if status == "admitted":
            return _lease_from_response(response, task_id=task_id, attempt_id=attempt_id)
        if status == "rejected":
            raise RuntimeCoordinationError(f"runtime admission rejected: {response.get('reason')}")
        if status not in {"queued", "pending"}:
            raise RuntimeCoordinationError(f"runtime poll returned unexpected status: {status}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _cancel_queued_after_abort(task_id, attempt_id)
            raise RuntimeCoordinationError("runtime queue wait exceeded timeout")
        time.sleep(min(0.05, remaining))


@asynccontextmanager
async def coordinated_runtime_task(
    *,
    task_id: int,
    work_kind: str = "Agent",
    attempt_id: int = 1,
    timeout_seconds: float = 60.0,
    memory_bytes: int = DEFAULT_RUNTIME_MEMORY_BYTES,
    priority: str = "Foreground",
    side_effect_class: str = "ExternalSideEffect",
    trust_level: str = "DEV",
) -> AsyncGenerator[RuntimeLease]:
    """Run one async operation under a bounded authoritative lease."""

    lease = await acquire_runtime_task_async(
        task_id=task_id,
        work_kind=work_kind,
        attempt_id=attempt_id,
        timeout_seconds=timeout_seconds,
        memory_bytes=memory_bytes,
        priority=priority,
        side_effect_class=side_effect_class,
        trust_level=trust_level,
    )
    try:
        yield lease
    except asyncio.CancelledError:
        try:
            lease.finish("cancelled")
        except BaseException as cleanup_error:
            raise RuntimeCoordinationError("runtime cancellation cleanup failed") from cleanup_error
        raise
    except BaseException as error:
        try:
            lease.finish("failed")
        except BaseException as cleanup_error:
            error.add_note(f"runtime lease cleanup failed: {cleanup_error}")
        raise
    else:
        lease.finish("done")


@contextmanager
def coordinated_runtime_task_sync(
    *,
    task_id: int,
    work_kind: str = "Agent",
    attempt_id: int = 1,
    timeout_seconds: float = 60.0,
    memory_bytes: int = DEFAULT_RUNTIME_MEMORY_BYTES,
    priority: str = "Foreground",
    side_effect_class: str = "ExternalSideEffect",
    trust_level: str = "DEV",
) -> Generator[RuntimeLease]:
    """Run one synchronous operation under a bounded authoritative lease."""

    lease = acquire_runtime_task(
        task_id=task_id,
        work_kind=work_kind,
        attempt_id=attempt_id,
        timeout_seconds=timeout_seconds,
        memory_bytes=memory_bytes,
        priority=priority,
        side_effect_class=side_effect_class,
        trust_level=trust_level,
    )
    try:
        yield lease
    except BaseException as error:
        try:
            lease.finish("failed")
        except BaseException as cleanup_error:
            error.add_note(f"runtime lease cleanup failed: {cleanup_error}")
        raise
    else:
        lease.finish("done")
