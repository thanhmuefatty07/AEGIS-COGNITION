"""Cross-language placement metadata and accelerator request tests."""

from __future__ import annotations

import pytest

from aegis_cognition import runtime


def _accelerator_plan() -> dict[str, object]:
    return {
        "schema": "aegis-placement-plan-v1",
        "task_id": 7,
        "decision": "selected",
        "executor": "gpu-0",
        "data_tier": "ram-0",
        "candidates": [
            {
                "id": "gpu-0",
                "domain": "Accelerator",
                "accelerator_kind": "Gpu",
                "backend": "Cuda",
                "vendor": "test-vendor",
                "capabilities": ["xor_u8"],
            },
            {"id": "ram-0", "domain": "HostMemory"},
        ],
    }


def test_selected_accelerator_metadata_becomes_a_typed_runtime_request() -> None:
    request = runtime.placement_resource_request(
        {
            "task_id": 7,
            "input_bytes": 1024,
            "output_bytes": 1024,
            "working_memory_bytes": 1024,
            "accelerator_work_units": 64,
            "accelerator_capabilities": ["xor_u8"],
        },
        _accelerator_plan(),
    )

    assert request["work_kind"] == "Accelerator"
    assert request["accelerator"] == {
        "kind": "Gpu",
        "backend": "Cuda",
        "required_capabilities": ["xor_u8"],
        "memory": {"bytes": 1024},
    }


def test_accelerator_task_cannot_request_an_unadvertised_capability() -> None:
    with pytest.raises(
        runtime.RuntimeCoordinationError,
        match="unadvertised capability",
    ):
        runtime.placement_resource_request(
            {
                "task_id": 7,
                "input_bytes": 1,
                "working_memory_bytes": 1,
                "accelerator_work_units": 1,
                "accelerator_capabilities": ["not-advertised"],
            },
            _accelerator_plan(),
        )
