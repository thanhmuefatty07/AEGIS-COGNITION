from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from aegis_cognition.subagents import AgentMessage, AgentTaskContext
from aegis_cognition.vision_adapters import (
    VisionAdapterError,
    VisionObservation,
    build_vision_subagent_handler,
    build_browser_vision_handler,
    make_vision_image,
)


def _context() -> AgentTaskContext:
    request = AgentMessage(
        schema="aegis-agent-message-v1",
        message_kind="TASK_REQUEST",
        run_id="run-vision",
        sender_id="root",
        recipient_id="subagent:1",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        idempotency_key="run-vision:1:1:request",
        payload={
            "role": "vision",
            "prompt": "inspect image",
            "artifact_namespace": "agent/1",
        },
    )
    return AgentTaskContext(request=request, dependency_results=())


async def test_vision_handler_hash_binds_image_and_keeps_bytes_out_of_result() -> None:
    image = make_vision_image(
        b"\x89PNG\r\n\x1a\nimage",
        namespace="agent/1",
        artifact_id="screen-after",
        uri="aegis://browser/screen-after",
    )
    observation = VisionObservation(images=(image,), text_context="untrusted page")
    calls: list[tuple[str, VisionObservation]] = []

    async def invoke(prompt: str, value: VisionObservation) -> str:
        calls.append((prompt, value))
        return "looked at the page"

    async def provide(_: AgentTaskContext) -> VisionObservation:
        return observation

    result = await build_vision_subagent_handler(invoke, provide)(_context())  # type: ignore[misc]

    assert result.summary == "looked at the page"
    assert result.artifacts == (image.artifact_ref,)
    assert result.tokens_in == 0
    assert calls[0][1].images[0].data == image.data


async def test_browser_capture_adapter_uses_existing_snapshot_shape() -> None:
    async def invoke(_: str, observation: VisionObservation) -> str:
        assert len(observation.images) == 1
        return "captured"

    async def capture(_: AgentTaskContext) -> object:
        return SimpleNamespace(
            after=SimpleNamespace(
                screenshot=b"\xff\xd8\xffjpeg",
                dom_snapshot="<main>page</main>",
                accessibility_tree={"role": "main"},
            ),
        )

    result = await (  # type: ignore[misc]
        build_browser_vision_handler(invoke, capture)(_context())
    )
    assert result.summary == "captured"
    assert result.artifacts[0].media_type == "image/jpeg"


def test_vision_observation_rejects_tampered_image_bytes() -> None:
    image = make_vision_image(
        b"GIF89aimage",
        namespace="agent/1",
        artifact_id="screen",
        uri="aegis://browser/screen",
    )
    tampered = replace(image, data=b"GIF89bimage")
    with pytest.raises(VisionAdapterError, match="digest"):
        VisionObservation(images=(tampered,)).validate()
