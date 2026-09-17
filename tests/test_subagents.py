from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from aegis_cognition.subagents import (
    AGENT_GRAPH_SCHEMA_V1,
    AGENT_MESSAGE_SCHEMA_V1,
    AGENT_PLAN_SCHEMA_V1,
    AgentArtifactRef,
    AgentClaim,
    AgentCoordinationError,
    AgentMessage,
    AgentMessageJournal,
    AgentPlanProposal,
    AgentResultPacket,
    AgentSupervisor,
    AgentTaskBlueprint,
    AgentTaskContext,
    AgentTaskSpec,
    build_model_subagent_handler,
    build_root_synthesizer,
    parse_agent_plan,
    validate_agent_graph_locally,
)


@asynccontextmanager
async def _fake_runtime_guard(**_: object) -> AsyncIterator[None]:
    yield None


def _native_graph_validator(graph: dict[str, object]) -> dict[str, object]:
    local = validate_agent_graph_locally(graph)
    return {
        **local,
        "authority": "native_runtime",
        "status": "validated",
        "executable": True,
        "graph_hash": "a" * 64,
    }


def _spec(
    task_id: int,
    handler: object,
    *,
    dependencies: tuple[int, ...] = (),
    namespace: str | None = None,
    exclusive_resources: tuple[str, ...] = (),
    token_budget: int | None = None,
    timeout_seconds: float = 60.0,
) -> AgentTaskSpec:
    return AgentTaskSpec(
        task_id=task_id,
        role=f"role-{task_id}",
        prompt=f"prompt-{task_id}",
        handler=handler,  # type: ignore[arg-type]
        artifact_namespace=namespace or f"agent/{task_id}",
        dependencies=dependencies,
        exclusive_resources=exclusive_resources,
        token_budget=token_budget,
        timeout_seconds=timeout_seconds,
    )


async def test_independent_children_overlap_and_root_is_called_after_all_children() -> None:
    started = {1: asyncio.Event(), 2: asyncio.Event()}
    release = asyncio.Event()
    finished: list[int] = []

    async def handler(context: AgentTaskContext) -> str:
        started[context.task_id].set()
        await release.wait()
        finished.append(context.task_id)
        return f"summary-{context.task_id}"

    specs = (_spec(1, handler), _spec(2, handler))
    supervisor = AgentSupervisor(
        run_id="run-overlap",
        max_concurrency=2,
        require_native_authority=True,
        graph_validator=_native_graph_validator,
        runtime_guard_factory=_fake_runtime_guard,
    )
    run_task = asyncio.create_task(supervisor.run(specs, lambda results: [item.task_id for item in results]))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), timeout=1)
    assert finished == []
    release.set()
    result = await run_task
    assert result.root_output == [1, 2]
    assert result.status == "COMPLETED"
    assert result.coordination_hash


async def test_runtime_admission_receives_the_same_hashed_dependency_dag() -> None:
    observed: list[dict[str, object]] = []

    @asynccontextmanager
    async def observing_runtime(**kwargs: object) -> AsyncIterator[None]:
        observed.append(kwargs)
        yield None

    async def handler(context: AgentTaskContext) -> str:
        return f"done-{context.task_id}"

    supervisor = AgentSupervisor(
        run_id="run-runtime-dag",
        require_native_authority=False,
        runtime_guard_factory=observing_runtime,
    )
    result = await supervisor.run(
        (_spec(1, handler), _spec(2, handler, dependencies=(1,))),
        lambda results: tuple(item.task_id for item in results),
    )

    assert result.status == "COMPLETED"
    assert observed[0]["dependency_ids"] == []
    assert observed[1]["dependency_ids"] == [supervisor._runtime_task_id(1)]


def test_message_journal_is_bounded_and_reports_cursor_resync() -> None:
    async def handler(_: AgentTaskContext) -> str:
        return "ok"

    journal = AgentMessageJournal(max_messages=2)
    messages = tuple(
        _spec(task_id, handler).request_message(run_id="journal-run", now_ms=task_id) for task_id in (1, 2, 3)
    )
    assert journal.append(messages[0]) == 1
    assert journal.append(messages[1]) == 2
    assert journal.read_since(0).resync_required is False

    assert journal.append(messages[2]) == 3
    stale = journal.read_since(0)
    assert stale.resync_required is True
    assert [entry.cursor for entry in stale.entries] == [2, 3]
    assert journal.read_since(1).resync_required is False
    assert journal.latest_cursor == 3


async def test_supervisor_can_publish_bounded_messages_to_the_observation_journal() -> None:
    async def handler(_: AgentTaskContext) -> str:
        return "ok"

    journal = AgentMessageJournal(max_messages=8)
    result = await AgentSupervisor(
        run_id="journal-supervisor",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
        message_journal=journal,
    ).run(
        (_spec(1, handler),),
        lambda results: results[0].summary,
    )

    assert result.status == "COMPLETED"
    read = journal.read_since(0)
    assert [entry.message.message_kind for entry in read.entries] == ["TASK_REQUEST", "TASK_RESULT"]
    assert read.resync_required is False


async def test_dependency_context_is_compact_and_dependency_failure_blocks_child() -> None:
    called: list[int] = []

    async def first(context: AgentTaskContext):
        called.append(context.task_id)
        return context.success(
            "x" * 3_000,
            claims=(AgentClaim("source was observed", "SOURCE-BACKED", ("source-1",)),),
        )

    async def second(context: AgentTaskContext):
        called.append(context.task_id)
        compact = context.compact_dependency_context()
        assert compact[0]["summary_truncated"] is True
        assert "source-1" in str(compact[0])
        return "dependent complete"

    supervisor = AgentSupervisor(
        run_id="run-dependency",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    )
    result = await supervisor.run(
        (_spec(1, first), _spec(2, second, dependencies=(1,))),
        lambda results: tuple(item.status for item in results),
    )
    assert called == [1, 2]
    assert result.root_output == ("SUCCEEDED", "SUCCEEDED")

    async def failed(_: AgentTaskContext):
        raise ValueError("secret-looking detail must not be serialized")

    called.clear()
    blocked = await AgentSupervisor(
        run_id="run-blocked",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    ).run(
        (_spec(3, failed), _spec(4, second, dependencies=(3,))),
        lambda results: tuple(item.status for item in results),
    )
    assert blocked.root_output == ("FAILED", "BLOCKED")
    assert called == []
    assert blocked.child_results[0].error_code == "ValueError"
    assert "secret-looking" not in str(blocked.wire_summary())


async def test_exclusive_resource_serializes_stateful_handlers() -> None:
    active = 0
    maximum = 0

    async def handler(context: AgentTaskContext) -> str:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return str(context.task_id)

    result = await AgentSupervisor(
        run_id="run-exclusive",
        max_concurrency=2,
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    ).run(
        (
            _spec(1, handler, exclusive_resources=("browser:actor:one",)),
            _spec(2, handler, exclusive_resources=("browser:actor:one",)),
        ),
        lambda results: results,
    )
    assert maximum == 1
    assert result.status == "COMPLETED"


async def test_exclusive_resource_serializes_across_supervisors_in_one_event_loop() -> None:
    active = 0
    maximum = 0

    async def handler(_: AgentTaskContext) -> str:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "ok"

    first = AgentSupervisor(
        run_id="run-exclusive-one",
        max_concurrency=1,
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    )
    second = AgentSupervisor(
        run_id="run-exclusive-two",
        max_concurrency=1,
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    )
    results = await asyncio.gather(
        first.run((_spec(1, handler, exclusive_resources=("browser:actor:shared",)),), lambda values: values),
        second.run((_spec(2, handler, exclusive_resources=("browser:actor:shared",)),), lambda values: values),
    )

    assert all(item.status == "COMPLETED" for item in results)
    assert maximum == 1


async def test_exclusive_resource_rejects_sync_handlers_before_execution() -> None:
    called = False

    def sync_handler(_: AgentTaskContext) -> str:
        nonlocal called
        called = True
        return "unsafe"

    supervisor = AgentSupervisor(
        run_id="run-sync-exclusive",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    )
    with pytest.raises(AgentCoordinationError, match="exclusive resources must be async"):
        await supervisor.run(
            (_spec(1, sync_handler, exclusive_resources=("browser:actor:one",)),),
            lambda results: results,
        )
    assert called is False


async def test_child_timeout_includes_concurrency_queue_wait() -> None:
    async def slow(_: AgentTaskContext) -> str:
        await asyncio.sleep(0.05)
        return "slow"

    async def quick(_: AgentTaskContext) -> str:
        return "quick"

    result = await AgentSupervisor(
        run_id="run-queue-timeout",
        max_concurrency=1,
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    ).run(
        (
            _spec(1, slow),
            _spec(2, quick, timeout_seconds=0.01),
        ),
        lambda results: tuple(item.status for item in results),
    )

    assert result.root_output == ("SUCCEEDED", "TIMED_OUT")
    assert result.child_results[1].error_code == "TIMEOUT"


async def test_invalid_child_result_is_recorded_as_failure_and_async_sink_is_awaited() -> None:
    messages: list[AgentMessage] = []

    async def sink(message: AgentMessage) -> None:
        await asyncio.sleep(0)
        messages.append(message)

    async def malformed(context: AgentTaskContext) -> AgentResultPacket:
        return AgentResultPacket(
            run_id=context.request.run_id,
            task_id=999,
            parent_task_id=None,
            attempt_id=1,
            status="SUCCEEDED",
            summary="wrong identity",
        )

    result = await AgentSupervisor(
        run_id="run-invalid-result",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
        message_sink=sink,
    ).run((_spec(1, malformed),), lambda results: results[0].status)

    assert result.root_output == "FAILED"
    assert result.child_results[0].error_code == "AgentCoordinationError"
    assert [message.message_kind for message in messages] == ["TASK_REQUEST", "TASK_RESULT"]


async def test_model_usage_is_reported_and_output_budget_is_enforced() -> None:
    async def invoke(_: str) -> dict[str, object]:
        return {
            "output_text": "bounded answer",
            "usage": {"input_tokens": 4, "output_tokens": 7},
        }

    handler = build_model_subagent_handler(invoke)
    observed = await handler(  # type: ignore[misc]
        AgentTaskContext(
            request=_spec(1, handler, token_budget=7).request_message(run_id="run-usage", now_ms=1),
            dependency_results=(),
        )
    )
    assert isinstance(observed, AgentResultPacket)
    assert (observed.tokens_in, observed.tokens_out) == (4, 7)

    result = await AgentSupervisor(
        run_id="run-budget",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    ).run(
        (_spec(1, handler, token_budget=6),),
        lambda results: results[0].status,
    )
    assert result.root_output == "FAILED"
    assert result.child_results[0].error_code == "AgentCoordinationError"


async def test_empty_supervisor_graph_is_rejected_before_root_synthesis() -> None:
    with pytest.raises(AgentCoordinationError, match="at least one child task"):
        await AgentSupervisor(
            run_id="run-empty",
            require_native_authority=False,
            runtime_guard_factory=_fake_runtime_guard,
        ).run((), lambda results: results)


def test_graph_validation_rejects_namespace_collision_and_cycle() -> None:
    with pytest.raises(AgentCoordinationError, match="disjoint"):
        validate_agent_graph_locally(
            {
                "schema": AGENT_GRAPH_SCHEMA_V1,
                "tasks": [
                    {"task_id": 1, "dependency_ids": [], "artifact_namespace": "same", "exclusive_resource_keys": []},
                    {"task_id": 2, "dependency_ids": [], "artifact_namespace": "same", "exclusive_resource_keys": []},
                ],
            }
        )


async def test_model_plan_binds_only_trusted_handlers_and_root_synthesizes_once() -> None:
    model_prompts: list[str] = []

    async def invoker(prompt: str) -> str:
        model_prompts.append(prompt)
        return f"model-output-{len(model_prompts)}"

    blueprint = {
        "task_id": 1,
        "handler_key": "research",
        "role": "researcher",
        "prompt": "collect bounded evidence",
        "artifact_namespace": "agent/1",
        "dependencies": [],
        "capabilities": ["network_read"],
        "exclusive_resources": [],
        "token_budget": 100,
        "timeout_seconds": 2.0,
        "memory_bytes": 1_024,
        "side_effect_class": "ReadOnly",
        "parent_task_id": None,
        "attempt_id": 1,
    }
    unsigned = {"schema": AGENT_PLAN_SCHEMA_V1, "tasks": [blueprint]}
    typed_blueprint = AgentTaskBlueprint.from_dict(blueprint)
    parsed = AgentPlanProposal.from_dict(
        {**unsigned, "proposal_hash": AgentPlanProposal(tasks=(typed_blueprint,)).proposal_hash}
    )
    handler = build_model_subagent_handler(invoker)
    specs = parsed.bind_handlers({"research": handler})
    result = await AgentSupervisor(
        run_id="run-model-plan",
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    ).run(specs, build_root_synthesizer(invoker))

    assert result.root_output == "model-output-2"
    assert len(model_prompts) == 2
    assert "CHILD_RESULT_PACKETS" not in model_prompts[0]
    with pytest.raises(AgentCoordinationError, match="no trusted handler"):
        parsed.bind_handlers({})

    with pytest.raises(AgentCoordinationError, match="keys"):
        validate_agent_graph_locally(
            {
                "schema": AGENT_GRAPH_SCHEMA_V1,
                "tasks": [
                    {
                        "task_id": 1,
                        "dependency_ids": [],
                        "artifact_namespace": "one",
                        "exclusive_resource_keys": [],
                        "unexpected": True,
                    }
                ],
            }
        )
    with pytest.raises(AgentCoordinationError, match="cycle"):
        validate_agent_graph_locally(
            {
                "schema": AGENT_GRAPH_SCHEMA_V1,
                "tasks": [
                    {"task_id": 1, "dependency_ids": [2], "artifact_namespace": "one", "exclusive_resource_keys": []},
                    {"task_id": 2, "dependency_ids": [1], "artifact_namespace": "two", "exclusive_resource_keys": []},
                ],
            }
        )


def test_message_and_artifact_are_hash_bound_and_tamper_evident() -> None:
    artifact = AgentArtifactRef(
        namespace="agent/1",
        artifact_id="source-1",
        uri="aegis://artifact/source-1",
        digest="b" * 64,
        size_bytes=12,
        media_type="text/plain",
    )
    message = AgentMessage(
        schema=AGENT_MESSAGE_SCHEMA_V1,
        message_kind="TASK_REQUEST",
        run_id="run-message",
        sender_id="root",
        recipient_id="subagent:1",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        idempotency_key="run-message:1:1:request",
        payload={"role": "researcher", "prompt": "find evidence"},
        artifact_refs=(artifact,),
        token_budget=100,
        deadline_ms=1_000,
    )
    wire = message.as_dict()
    assert AgentMessage.from_dict(wire).message_hash == message.message_hash
    assert AgentMessage.from_bytes(message.to_bytes()) == message
    wire["payload"] = {"role": "researcher", "prompt": "tampered"}
    with pytest.raises(AgentCoordinationError, match="hash mismatch"):
        AgentMessage.from_dict(wire)

    result = AgentResultPacket(
        run_id="run-message",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        status="SUCCEEDED",
        summary="observed source",
        claims=(AgentClaim("source was observed", "SOURCE-BACKED", ("source-1",)),),
        artifacts=(artifact,),
    )
    result_message = result.to_message(recipient_id="root")
    assert "artifacts" not in result_message.payload
    assert AgentResultPacket.from_message(AgentMessage.from_dict(result_message.as_dict())) == result
    tampered_sender = replace(result_message, sender_id="subagent:2")
    with pytest.raises(AgentCoordinationError, match="sender"):
        AgentResultPacket.from_message(tampered_sender)

    with pytest.raises(AgentCoordinationError, match="UTF-8 JSON"):
        AgentMessage.from_bytes(b"not-json")
    with pytest.raises(AgentCoordinationError, match="canonical JSON"):
        AgentMessage.from_bytes(b" " + message.to_bytes())


def test_unsigned_and_fenced_model_plans_are_host_hashed_without_substring_extraction() -> None:
    blueprint = {
        "task_id": 1,
        "handler_key": "model",
        "role": "researcher",
        "prompt": "collect evidence",
        "artifact_namespace": "agent/1",
        "dependencies": [],
        "capabilities": ["read_only"],
        "exclusive_resources": [],
        "token_budget": 100,
        "timeout_seconds": 2.0,
        "memory_bytes": 1_024,
        "side_effect_class": "ReadOnly",
        "parent_task_id": None,
        "attempt_id": 1,
    }
    unsigned = {"schema": AGENT_PLAN_SCHEMA_V1, "tasks": [blueprint]}
    parsed = parse_agent_plan(json.dumps(unsigned, separators=(",", ":")))
    fenced = parse_agent_plan(f"```json\n{json.dumps(unsigned)}\n```")
    assert parsed.proposal_hash == fenced.proposal_hash
    with pytest.raises(AgentCoordinationError, match="valid JSON"):
        parse_agent_plan(f"prefix {json.dumps(unsigned)}")


def test_result_wire_does_not_duplicate_envelope_identity_or_artifacts() -> None:
    artifact = AgentArtifactRef(
        namespace="agent/1",
        artifact_id="source-1",
        uri="aegis://artifact/" + "x" * 512,
        digest="b" * 64,
        size_bytes=512,
        media_type="text/plain",
    )
    packet = AgentResultPacket(
        run_id="run-wire-size",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        status="SUCCEEDED",
        summary="bounded result",
        artifacts=(artifact,),
    )
    message = packet.to_message(recipient_id="root")
    wire = message.as_dict()
    naive_wire = {**wire, "payload": packet.as_dict()}
    actual_bytes = len(json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    naive_bytes = len(json.dumps(naive_wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert actual_bytes < naive_bytes
