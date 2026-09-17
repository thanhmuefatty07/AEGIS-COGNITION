from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import aegis_cognition.application as application_module
from aegis_cognition.desktop_service import DesktopService
import aegis_cognition.runtime as runtime


def _token() -> dict[str, object]:
    return {
        "schema": "aegis-runtime-lease-token-v1",
        "lease_id": 11,
        "generation": 3,
        "attempt_id": 1,
    }


class _Native:
    def __init__(self, submit: list[dict[str, object]], polls: list[dict[str, object]]) -> None:
        self.submit = submit
        self.polls = polls
        self.finished: list[tuple[str, str]] = []
        self.cancelled: list[tuple[int, int]] = []
        self.requests: list[tuple[int, str, str, int]] = []
        self.dependencies: list[tuple[int, str]] = []

    def aegis_runtime_submit(self, task_id: int, dependencies: str, request: str, now_ms: int) -> str:
        self.dependencies.append((task_id, dependencies))
        self.requests.append((task_id, request, "submit", now_ms))
        return json.dumps(self.submit.pop(0))

    def aegis_runtime_poll(self, task_id: int, attempt_id: int, now_ms: int) -> str:
        self.requests.append((task_id, str(attempt_id), "poll", now_ms))
        response = (
            self.polls.pop(0)
            if self.polls
            else {
                "schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1,
                "status": "pending",
            }
        )
        return json.dumps(response)

    def aegis_runtime_cancel(self, task_id: int, attempt_id: int) -> bool:
        self.cancelled.append((task_id, attempt_id))
        return True

    def aegis_runtime_finish(self, token: str, outcome: str) -> bool:
        self.finished.append((token, outcome))
        return True


def test_no_native_runtime_is_explicitly_non_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    lease = asyncio.run(runtime.acquire_runtime_task_async(task_id=1))
    assert lease.authoritative is False
    lease.finish()
    lease.finish()


def test_prod_runtime_rejects_non_authoritative_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    with pytest.raises(runtime.RuntimeCoordinationError, match=r"PROD.*authoritative"):
        asyncio.run(runtime.acquire_runtime_task_async(task_id=6, trust_level="PROD"))

    with pytest.raises(runtime.RuntimeCoordinationError, match=r"PROD.*authoritative"):
        runtime.acquire_runtime_task(task_id=7, trust_level="PROD")

    dev_lease = runtime.acquire_runtime_task(task_id=8, trust_level="DEV")
    assert dev_lease.authoritative is False


def test_agent_application_propagates_trust_to_runtime_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_runtime(**kwargs: object):
        captured.update(kwargs)
        yield object()

    class Gateway:
        async def run(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                task="task",
                output="output",
                trust_level="PROD",
                provider="test",
                hot_commit=None,
                correlation=None,
            )

    config = SimpleNamespace(
        task="task",
        llm=SimpleNamespace(requires_api_key=False),
        trust_level="PROD",
        trust_policy_hash="",
        options={},
    )
    application = application_module.AgentApplication(
        config,
        gateway_factory=lambda **_: Gateway(),
    )
    monkeypatch.setattr(application_module, "coordinated_runtime_task", fake_runtime)
    monkeypatch.setattr(application, "prepare", lambda task: (task, "context"))
    monkeypatch.setattr(application, "_gateway", lambda _task: Gateway())
    monkeypatch.setattr(application, "_index_completed_run", lambda *_, **__: None)

    result = asyncio.run(application.arun())

    assert result.output == "output"
    assert captured["trust_level"] == "PROD"


def test_desktop_runtime_policy_defaults_to_dev_and_supports_explicit_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("AEGIS_TRUST_LEVEL", raising=False)

    default_service = DesktopService(profile_root=tmp_path / "default")
    strict_service = DesktopService(profile_root=tmp_path / "strict", trust_level="PROD")

    assert default_service.trust_level == "DEV"
    assert strict_service.trust_level == "PROD"


def test_admitted_lease_finishes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _Native(
        submit=[
            {
                "schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1,
                "status": "admitted",
                "lease_token": _token(),
            }
        ],
        polls=[],
    )
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    lease = asyncio.run(runtime.acquire_runtime_task_async(task_id=2))
    assert lease.authoritative is True
    lease.finish()
    lease.finish()
    assert native.finished == [(json.dumps(_token()), "done")]
    request = json.loads(native.requests[0][1])
    assert request["task_id"] == 2
    assert request["work_kind"] == "Agent"
    assert request["cpu"] == {"min_threads": 1, "max_threads": 1}


def test_runtime_submission_preserves_dependency_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _Native(
        submit=[
            {
                "schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1,
                "status": "admitted",
                "lease_token": _token(),
            }
        ],
        polls=[],
    )
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    lease = asyncio.run(runtime.acquire_runtime_task_async(task_id=12, dependency_ids=[3, 5]))
    lease.finish()

    assert native.dependencies == [(12, "[3, 5]")]


def test_queued_task_polls_until_admitted_and_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _Native(
        submit=[
            {
                "schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1,
                "status": "queued",
                "position": 1,
            }
        ],
        polls=[
            {"schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1, "status": "pending"},
            {
                "schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1,
                "status": "admitted",
                "lease_token": _token(),
            },
        ],
    )
    monkeypatch.setattr(runtime, "_native_module", lambda: native)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(runtime.asyncio, "sleep", lambda _seconds: real_sleep(0))

    lease = asyncio.run(runtime.acquire_runtime_task_async(task_id=3, timeout_seconds=1))
    lease.finish()
    assert len(native.requests) == 3
    assert native.cancelled == []


def test_queue_timeout_cancels_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _Native(
        submit=[
            {
                "schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1,
                "status": "queued",
                "position": 1,
            }
        ],
        polls=[{"schema": runtime.RUNTIME_ADMISSION_SCHEMA_V1, "status": "pending"}],
    )
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    with pytest.raises(runtime.RuntimeCoordinationError, match="timeout"):
        asyncio.run(runtime.acquire_runtime_task_async(task_id=4, timeout_seconds=0.001))
    assert native.cancelled == [(4, 1)]


def test_malformed_native_admission_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _Native(submit=[{"status": "admitted"}], polls=[])
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    with pytest.raises(runtime.RuntimeCoordinationError, match="schema"):
        asyncio.run(runtime.acquire_runtime_task_async(task_id=5))


def test_runtime_finish_rejects_invalid_outcome_and_native_truthiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        runtime.finish_runtime_lease(_token(), "maybe")

    class MalformedNative:
        def aegis_runtime_finish(self, token: str, outcome: str) -> object:
            del token, outcome
            return "false"

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())
    with pytest.raises(runtime.RuntimeCoordinationError, match="non-boolean"):
        runtime.finish_runtime_lease(_token(), "done")


def test_runtime_cancellation_rejects_malformed_native_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedNative:
        def aegis_runtime_cancel(self, task_id: int, attempt_id: int) -> object:
            del task_id, attempt_id
            return 1

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())
    with pytest.raises(runtime.RuntimeCoordinationError, match="non-boolean"):
        runtime.cancel_runtime_task(1)


def test_cooperative_release_rejects_malformed_native_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedNative:
        def aegis_cooperative_placement_release(self, lease: str) -> object:
            del lease
            return "false"

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())
    with pytest.raises(runtime.RuntimeCoordinationError, match="non-boolean"):
        runtime.release_cooperative_placement({"lease_id": 1})


def test_placement_planner_fallback_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    result = runtime.placement_plan({"task_id": 1}, [])
    assert result["schema"] == "aegis-placement-plan-v1"
    assert result["decision"] == "native_unavailable"
    assert result["selected"] is None


def test_cooperative_placement_preview_fallback_is_non_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    result = runtime.cooperative_placement_preview({"task_id": 1}, [], [])
    assert result["schema"] == runtime.COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1
    assert result["authority"] == "planner_only"
    assert result["executable"] is False
    assert result["reservation_status"] == "NOT_ADMITTED"
    assert result["plan"]["decision"] == "native_unavailable"


def test_cooperative_placement_preview_forwards_only_to_native_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PreviewNative:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, int]] = []

        def aegis_cooperative_placement_preview(self, task: str, capabilities: str, paths: str, now_ms: int) -> str:
            self.calls.append((task, capabilities, paths, now_ms))
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1,
                    "authority": "planner_only",
                    "executable": False,
                    "reservation_status": "NOT_ADMITTED",
                    "plan": {
                        "schema": "aegis-cooperative-placement-plan-v1",
                        "decision": "selected",
                    },
                }
            )

    native = PreviewNative()
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    result = runtime.cooperative_placement_preview({"task_id": 1}, [{"id": "cpu"}], [{"id": "path"}], now_ms=123)
    assert result["executable"] is False
    assert len(native.calls) == 1
    task, capabilities, paths, now_ms = native.calls[0]
    assert json.loads(task) == {"task_id": 1}
    assert json.loads(capabilities) == [{"id": "cpu"}]
    assert json.loads(paths) == [{"id": "path"}]
    assert now_ms == 123


def test_cooperative_preview_with_calibration_merges_only_measured_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CalibratingNative:
        def __init__(self) -> None:
            self.preview_capabilities: list[dict[str, object]] = []

        def aegis_placement_capabilities(self) -> str:
            return json.dumps(
                [
                    {
                        "id": "cpu",
                        "domain": "Cpu",
                        "compute_units_per_us": None,
                        "confidence": "Unknown",
                    },
                    {
                        "id": "host",
                        "domain": "HostMemory",
                        "compute_units_per_us": None,
                        "confidence": "Estimated",
                    },
                ]
            )

        def aegis_placement_calibrate(self, cpu_iterations: int, storage_bytes: int | None) -> str:
            assert cpu_iterations == 123
            assert storage_bytes == 4096
            return json.dumps(
                {
                    "schema": "aegis-placement-measurement-v1",
                    "measurements": [
                        {
                            "schema": "aegis-placement-measurement-v1",
                            "resource_id": "cpu",
                            "domain": "Cpu",
                            "workload": "test-cpu-v1",
                            "bytes": 0,
                            "work_units": 123,
                            "elapsed_us": 1,
                            "compute_units_per_us": 17,
                            "bandwidth_bytes_per_s": None,
                            "latency_us": 1,
                            "confidence": "Measured",
                        },
                        {
                            "schema": "aegis-placement-measurement-v1",
                            "resource_id": "host",
                            "domain": "HostMemory",
                            "workload": "test-host-v1",
                            "bytes": 0,
                            "work_units": 0,
                            "elapsed_us": 1,
                            "compute_units_per_us": 999,
                            "bandwidth_bytes_per_s": None,
                            "latency_us": 0,
                            "confidence": "Unknown",
                        },
                    ],
                }
            )

        def aegis_cooperative_placement_preview(self, task: str, capabilities: str, paths: str, now_ms: int) -> str:
            del task, paths, now_ms
            self.preview_capabilities = json.loads(capabilities)
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1,
                    "authority": "planner_only",
                    "executable": False,
                    "reservation_status": "NOT_ADMITTED",
                    "plan": {
                        "schema": "aegis-cooperative-placement-plan-v1",
                        "decision": "deferred",
                    },
                }
            )

    native = CalibratingNative()
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    result = runtime.cooperative_placement_preview_with_calibration(
        {"task_id": 1}, cpu_iterations=123, storage_bytes=4096, now_ms=456
    )

    assert result["schema"] == runtime.COOPERATIVE_PLACEMENT_RUN_SCHEMA_V1
    assert result["preview"]["executable"] is False
    assert native.preview_capabilities[0]["compute_units_per_us"] == 17
    assert native.preview_capabilities[0]["confidence"] == "Measured"
    assert native.preview_capabilities[1]["compute_units_per_us"] is None
    assert native.preview_capabilities[1]["confidence"] == "Estimated"


def test_cooperative_placement_preview_rejects_executable_native_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PreviewNative:
        def aegis_cooperative_placement_preview(self, task: str, capabilities: str, paths: str, now_ms: int) -> str:
            del task, capabilities, paths, now_ms
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1,
                    "authority": "runtime",
                    "executable": True,
                    "reservation_status": "ADMITTED",
                    "plan": {},
                }
            )

    monkeypatch.setattr(runtime, "_native_module", lambda: PreviewNative())

    with pytest.raises(runtime.RuntimeCoordinationError, match="non-executable"):
        runtime.cooperative_placement_preview({"task_id": 1}, [], [])


def test_cooperative_preview_with_calibration_validates_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NativeThatMustNotBeCalled:
        def aegis_placement_capabilities(self) -> str:
            raise AssertionError("capability discovery must not run for invalid input")

        def aegis_placement_calibrate(self, cpu_iterations: int, storage_bytes: int | None) -> str:
            del cpu_iterations, storage_bytes
            raise AssertionError("calibration must not run for invalid input")

    monkeypatch.setattr(runtime, "_native_module", lambda: NativeThatMustNotBeCalled())

    with pytest.raises(TypeError, match="task must be an object"):
        runtime.cooperative_placement_preview_with_calibration(  # type: ignore[arg-type]
            [], paths=[]
        )


def test_calibration_rejects_measurement_schema_and_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedNative:
        def aegis_placement_calibrate(self, cpu_iterations: int, storage_bytes: int | None) -> str:
            del cpu_iterations, storage_bytes
            return json.dumps(
                {
                    "schema": "aegis-placement-measurement-v1",
                    "measurements": [
                        {
                            "schema": "wrong-schema",
                            "resource_id": "cpu",
                            "domain": "Cpu",
                            "workload": "test-v1",
                            "bytes": 0,
                            "work_units": 1,
                            "elapsed_us": 1,
                            "compute_units_per_us": 1,
                            "bandwidth_bytes_per_s": None,
                            "latency_us": 1,
                            "confidence": "Measured",
                        }
                    ],
                }
            )

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())

    with pytest.raises(runtime.RuntimeCoordinationError, match="unsupported schema"):
        runtime.calibrate_placement(cpu_iterations=1, storage_bytes=None)


def test_calibration_rejects_non_positive_measured_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedNative:
        def aegis_placement_calibrate(self, cpu_iterations: int, storage_bytes: int | None) -> str:
            del cpu_iterations, storage_bytes
            return json.dumps(
                {
                    "schema": "aegis-placement-measurement-v1",
                    "measurements": [
                        {
                            "schema": "aegis-placement-measurement-v1",
                            "resource_id": "cpu",
                            "domain": "Cpu",
                            "workload": "test-v1",
                            "bytes": 0,
                            "work_units": 1,
                            "elapsed_us": 1,
                            "compute_units_per_us": 0,
                            "bandwidth_bytes_per_s": None,
                            "latency_us": 1,
                            "confidence": "Measured",
                        }
                    ],
                }
            )

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())

    with pytest.raises(runtime.RuntimeCoordinationError, match="compute_units_per_us"):
        runtime.calibrate_placement(cpu_iterations=1, storage_bytes=None)


def test_cooperative_placement_admission_fallback_is_non_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    result = runtime.admit_cooperative_placement({"task_id": 1}, [], [])
    assert result["schema"] == runtime.COOPERATIVE_ADMISSION_SCHEMA_V1
    assert result["authority"] == "unverified"
    assert result["status"] == "native_unavailable"
    assert result["executable"] is False


def test_cooperative_placement_admission_requires_native_lease_and_releases_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AdmissionNative:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, int, int]] = []
            self.released: list[str] = []

        def aegis_cooperative_placement_admit(
            self,
            task: str,
            capabilities: str,
            paths: str,
            attempt_id: int,
            now_ms: int,
        ) -> str:
            self.calls.append((task, capabilities, paths, attempt_id, now_ms))
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_ADMISSION_SCHEMA_V1,
                    "authority": "native_runtime",
                    "status": "admitted",
                    "executable": True,
                    "reservation_status": "ADMITTED",
                    "inventory_hash": "a" * 64,
                    "plan": {"decision": "selected"},
                    "lease": {"lease_id": 9, "generation": 1},
                }
            )

        def aegis_cooperative_placement_release(self, lease: str) -> bool:
            self.released.append(lease)
            return True

    native = AdmissionNative()
    monkeypatch.setattr(runtime, "_native_module", lambda: native)
    result = runtime.admit_cooperative_placement({"task_id": 7}, [{"id": "cpu"}], [], attempt_id=2, now_ms=123)
    assert result["status"] == "admitted"
    assert result["lease"] == {"lease_id": 9, "generation": 1}
    assert runtime.release_cooperative_placement(result["lease"])
    assert len(native.calls) == 1
    task, capabilities, paths, attempt_id, now_ms = native.calls[0]
    assert json.loads(task) == {"task_id": 7}
    assert json.loads(capabilities) == [{"id": "cpu"}]
    assert json.loads(paths) == []
    assert attempt_id == 2
    assert now_ms == 123
    assert native.released == [json.dumps(result["lease"])]


def test_cooperative_placement_admission_rejects_missing_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AdmissionNative:
        def aegis_cooperative_placement_admit(
            self, task: str, capabilities: str, paths: str, attempt_id: int, now_ms: int
        ) -> str:
            del task, capabilities, paths, attempt_id, now_ms
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_ADMISSION_SCHEMA_V1,
                    "authority": "native_runtime",
                    "status": "admitted",
                    "executable": True,
                }
            )

    monkeypatch.setattr(runtime, "_native_module", lambda: AdmissionNative())
    with pytest.raises(runtime.RuntimeCoordinationError, match="omitted its lease"):
        runtime.admit_cooperative_placement({"task_id": 7}, [], [])


class _CooperativeRuntimeNative:
    def __init__(self, *, mode: str, status: str = "admitted") -> None:
        self.mode = mode
        self.status = status
        self.configured: list[tuple[str, str, str]] = []
        self.submitted: list[tuple[str, int]] = []
        self.finished: list[tuple[int, str]] = []
        self.cancelled: list[int] = []

    def aegis_runtime_cooperative_configure(self, mode: str, capabilities: str, paths: str) -> str:
        self.configured.append((mode, capabilities, paths))
        return json.dumps(
            {
                "schema": runtime.COOPERATIVE_RUNTIME_SCHEMA_V1,
                "authority": "authoritative_runtime",
                "status": "configured",
                "mode": mode,
                "executable": mode == "enforced",
                "inventory_hash": "b" * 64,
            }
        )

    def aegis_runtime_cooperative_submit(self, request: str, now_ms: int) -> str:
        self.submitted.append((request, now_ms))
        if self.status == "shadow":
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_RUNTIME_SCHEMA_V1,
                    "authority": "authoritative_runtime",
                    "status": "shadow",
                    "executable": False,
                    "reservation_status": "VALIDATED_NOT_ADMITTED",
                    "handle": None,
                }
            )
        return json.dumps(
            {
                "schema": runtime.COOPERATIVE_RUNTIME_SCHEMA_V1,
                "authority": "authoritative_runtime",
                "status": "admitted",
                "executable": True,
                "reservation_status": "ADMITTED",
                "handle": 17,
            }
        )

    def aegis_runtime_cooperative_finish(self, handle: int, outcome: str) -> bool:
        self.finished.append((handle, outcome))
        return True

    def aegis_runtime_cooperative_cancel(self, handle: int) -> bool:
        self.cancelled.append(handle)
        return True


def test_cooperative_runtime_unavailable_is_explicitly_non_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    configured = runtime.configure_cooperative_runtime("enforced", [])
    submitted = runtime.submit_cooperative_runtime({}, now_ms=123)

    assert configured["status"] == "native_unavailable"
    assert configured["authority"] == "unverified"
    assert configured["executable"] is False
    assert submitted["status"] == "native_unavailable"
    assert submitted["reservation_status"] == "NOT_ADMITTED"
    assert submitted["executable"] is False


def test_cooperative_runtime_shadow_is_non_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _CooperativeRuntimeNative(mode="shadow", status="shadow")
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    result = runtime.configure_cooperative_runtime("shadow", [{"id": "cpu-0"}], [])
    submission = runtime.submit_cooperative_runtime({"task_id": 1}, now_ms=123)

    assert result["status"] == "configured"
    assert result["executable"] is False
    assert submission["status"] == "shadow"
    assert submission["executable"] is False
    assert submission["handle"] is None
    assert json.loads(native.configured[0][1]) == [{"id": "cpu-0"}]


def test_cooperative_runtime_enforced_uses_only_an_opaque_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _CooperativeRuntimeNative(mode="enforced")
    monkeypatch.setattr(runtime, "_native_module", lambda: native)

    runtime.configure_cooperative_runtime("enforced", [], [])
    first = runtime.submit_cooperative_runtime({"task_id": 2}, now_ms=456)
    second = runtime.submit_cooperative_runtime({"task_id": 2}, now_ms=456)

    assert first["handle"] == 17
    assert second["handle"] == first["handle"]
    assert "lease" not in first
    assert runtime.finish_cooperative_runtime(first["handle"], "done")
    assert native.finished == [(17, "done")]


def test_cooperative_runtime_rejects_serialized_lease_instead_of_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedNative:
        def aegis_runtime_cooperative_submit(self, request: str, now_ms: int) -> str:
            del request, now_ms
            return json.dumps(
                {
                    "schema": runtime.COOPERATIVE_RUNTIME_SCHEMA_V1,
                    "authority": "authoritative_runtime",
                    "status": "admitted",
                    "executable": True,
                    "reservation_status": "ADMITTED",
                    "lease": {"lease_id": 1},
                }
            )

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())

    with pytest.raises(runtime.RuntimeCoordinationError, match="omitted its handle"):
        runtime.submit_cooperative_runtime({}, now_ms=789)


def test_cooperative_runtime_finish_rejects_non_boolean_native_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedNative:
        def aegis_runtime_cooperative_finish(self, handle: int, outcome: str) -> object:
            del handle, outcome
            return 1

    monkeypatch.setattr(runtime, "_native_module", lambda: MalformedNative())

    with pytest.raises(runtime.RuntimeCoordinationError, match="non-boolean"):
        runtime.finish_cooperative_runtime(17)


def test_placement_capabilities_fallback_keeps_unknown_costs_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    capabilities = runtime.placement_capabilities()
    assert capabilities[0]["domain"] == "Cpu"
    assert capabilities[0]["compute_units_per_us"] is None
    assert capabilities[0]["confidence"] == "Unknown"


def test_calibration_fallback_is_explicit_and_does_not_claim_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    result = runtime.placement_plan_with_calibration({"task_id": 1}, cpu_iterations=100, storage_bytes=None)
    assert result["calibration"]["measurements"] == []
    assert result["plan"]["decision"] == "native_unavailable"


def test_resource_observation_fallback_is_explicit_and_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_native_module", lambda: None)

    result = runtime.observe_runtime_resources()
    assert result["authoritative"] is False
    assert result["feedback"] is None
    assert result["sample"]["host_memory_available_bytes"] is None


def _selected_placement_plan(*, data_domain: str = "Storage") -> dict[str, object]:
    return {
        "schema": "aegis-placement-plan-v1",
        "task_id": 9,
        "decision": "selected",
        "executor": "cpu",
        "data_tier": "data",
        "candidates": [
            {"id": "cpu", "domain": "Cpu"},
            {"id": "data", "domain": data_domain},
        ],
    }


def test_placement_request_keeps_storage_data_out_of_the_full_ram_reservation() -> None:
    request = runtime.placement_resource_request(
        {
            "task_id": 9,
            "input_bytes": 100,
            "output_bytes": 20,
            "working_memory_bytes": 64,
            "cpu_work_units": 10,
        },
        _selected_placement_plan(),
    )
    assert request["host_memory"] == {"bytes": 184}
    assert request["io"] == {"max_in_flight": 1}
    assert request["accelerator"] is None


def test_placement_request_fails_closed_for_deferred_plan() -> None:
    plan = _selected_placement_plan()
    plan["decision"] = "deferred"
    with pytest.raises(runtime.RuntimeCoordinationError, match="not executable"):
        runtime.placement_resource_request({"task_id": 9, "working_memory_bytes": 1}, plan)


def test_placement_request_requires_real_accelerator_metadata() -> None:
    plan = _selected_placement_plan()
    plan["executor"] = "gpu"
    plan["candidates"] = [
        {"id": "gpu", "domain": "Accelerator"},
        {"id": "data", "domain": "HostMemory"},
    ]
    with pytest.raises(runtime.RuntimeCoordinationError, match="metadata"):
        runtime.placement_resource_request(
            {
                "task_id": 9,
                "input_bytes": 1,
                "working_memory_bytes": 1,
                "accelerator_work_units": 1,
            },
            plan,
        )
