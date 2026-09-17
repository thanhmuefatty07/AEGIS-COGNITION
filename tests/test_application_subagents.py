from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace

import pytest

from aegis_cognition.application import AgentApplication
from aegis_cognition.config import AgentConfig
from aegis_cognition.subagents import AgentCoordinationError, AgentPlanProposal, AgentTaskBlueprint, AgentTaskContext


class _NoKeyModel:
    requires_api_key = False


class _Gateway:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str, **_: object) -> object:
        self.prompts.append(prompt)
        return self.responses.pop(0)


def _unsigned_plan() -> str:
    return json.dumps(
        {
            "schema": "aegis-agent-plan-v1",
            "tasks": [
                {
                    "task_id": 1,
                    "handler_key": "model",
                    "role": "evidence worker",
                    "prompt": "collect bounded evidence",
                    "artifact_namespace": "agent/1",
                    "dependencies": [],
                    "capabilities": ["model_inference"],
                    "exclusive_resources": [],
                    "token_budget": 100,
                    "timeout_seconds": 2.0,
                    "memory_bytes": 1_024,
                    "side_effect_class": "ReadOnly",
                    "parent_task_id": None,
                    "attempt_id": 1,
                }
            ],
        }
    )


async def test_application_can_plan_children_and_synthesize_once_at_root(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _Gateway([_unsigned_plan(), "child evidence", "root synthesis"])

    def factory(**_: object) -> _Gateway:
        return gateway

    config = AgentConfig.from_inputs("research a bounded topic", llm=_NoKeyModel())
    application = AgentApplication(config, gateway_factory=factory)
    monkeypatch.setattr(application, "prepare", lambda _task: ("formatted", "system"))

    result = await application.arun_subagents(require_native_authority=False)

    assert result.root_output == "root synthesis"
    assert result.status == "COMPLETED"
    assert [child.summary for child in result.child_results] == ["child evidence"]
    assert len(gateway.prompts) == 3
    assert "HANDLER_ALLOWLIST" in gateway.prompts[0]
    assert "research" in gateway.prompts[0]
    assert "RESEARCH_X_APP_ONLY_CONFIGURED: false" in gateway.prompts[0]
    assert "CHILD_RESULT_PACKETS" in gateway.prompts[2]


async def test_application_native_authority_runs_parallel_children_and_root_synthesis() -> None:
    blueprint_one = AgentTaskBlueprint(
        task_id=1,
        handler_key="trusted",
        role="worker one",
        prompt="bounded work one",
        artifact_namespace="agent/1",
    )
    blueprint_two = AgentTaskBlueprint(
        task_id=2,
        handler_key="trusted",
        role="worker two",
        prompt="bounded work two",
        artifact_namespace="agent/2",
    )
    config = AgentConfig.from_inputs(
        "native subagent integration",
        llm=_NoKeyModel(),
    )
    application = AgentApplication(config)
    plan = {"schema": "aegis-agent-plan-v1", "tasks": [blueprint_one.as_dict(), blueprint_two.as_dict()]}

    async def handler(context: AgentTaskContext) -> object:
        await asyncio.sleep(0)
        return context.success(f"done-{context.task_id}")

    result = await application.arun_subagents(
        plan=plan,
        handlers={"trusted": handler},
        root_synthesizer=lambda children: tuple(child.summary for child in children),
        require_native_authority=True,
    )

    assert result.status == "COMPLETED"
    assert result.graph_authority == "native_runtime"
    assert result.root_output == ("done-1", "done-2")
    assert len(result.child_results) == 2


async def test_application_binds_public_research_handler_into_the_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway_calls = 0

    async def provider(_: str, **__: object) -> list[dict[str, object]]:
        return [{"uri": "https://www.reddit.com/r/artificial/comments/one/", "content": "public result"}]

    def gateway_factory(**_: object) -> object:
        nonlocal gateway_calls
        gateway_calls += 1
        return object()

    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="research",
        role="public researcher",
        prompt="reddit: bounded agent evidence",
        artifact_namespace="agent/1",
        capabilities=("network_read",),
        side_effect_class="ReadOnly",
    )
    plan = {"schema": "aegis-agent-plan-v1", "tasks": [blueprint.as_dict()]}
    config = AgentConfig.from_inputs(
        "public research",
        llm=_NoKeyModel(),
        subagent_research_router=provider,
    )
    application = AgentApplication(config, gateway_factory=gateway_factory)
    monkeypatch.setattr(application, "prepare", lambda _task: ("formatted", "system"))

    async def synthesize(results: tuple[object, ...]) -> str:
        return str(results[0])

    result = await application.arun_subagents(
        plan=plan,
        root_synthesizer=synthesize,  # type: ignore[arg-type]
        require_native_authority=False,
    )

    assert result.status == "COMPLETED"
    assert "public result" in result.child_results[0].summary
    assert gateway_calls == 0


async def test_application_binds_host_owned_browser_vision_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, object]] = []

    async def capture(_: object) -> object:
        return SimpleNamespace(
            after=SimpleNamespace(
                screenshot=b"\x89PNG\r\n\x1a\nimage",
                dom_snapshot="<button>untrusted</button>",
                accessibility_tree=b'{"role":"button"}',
            )
        )

    async def invoke(prompt: str, observation: object) -> object:
        captured.append((prompt, observation))
        return {"output_text": "visual interpretation", "prompt_tokens": 4, "completion_tokens": 2}

    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="vision",
        role="visual researcher",
        prompt="inspect the captured page",
        artifact_namespace="agent/1",
        capabilities=("vision",),
        exclusive_resources=("browser:observer:one",),
    )
    config = AgentConfig.from_inputs(
        "visual research",
        llm=_NoKeyModel(),
        subagent_browser_capture=capture,
        subagent_vision_invoker=invoke,
    )
    application = AgentApplication(config, gateway_factory=lambda **_: object())
    monkeypatch.setattr(application, "prepare", lambda _task: ("formatted", "system"))

    result = await application.arun_subagents(
        plan={"schema": "aegis-agent-plan-v1", "tasks": [blueprint.as_dict()]},
        root_synthesizer=lambda children: children[0].summary,
        require_native_authority=False,
    )

    assert result.root_output == "visual interpretation"
    assert len(captured) == 1
    assert result.child_results[0].tokens_in == 4
    assert result.child_results[0].tokens_out == 2
    assert result.child_results[0].claims[0].evidence_class == "INFERRED"


def test_application_rejects_untrusted_plan_capability_and_nested_parent() -> None:
    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="trusted",
        role="worker",
        prompt="bounded work",
        artifact_namespace="agent/1",
        capabilities=("external_write",),
        side_effect_class="ExternalSideEffect",
        parent_task_id=None,
    )
    proposal = AgentPlanProposal(tasks=(blueprint,))
    config = AgentConfig.from_inputs("bounded work", llm=_NoKeyModel())
    application = AgentApplication(config)

    async def handler(_context: object) -> str:
        return "done"

    with pytest.raises(ValueError, match="capability outside"):
        application._validate_subagent_plan(proposal, {"trusted": handler})  # type: ignore[arg-type]

    nested = AgentPlanProposal(
        tasks=(
            AgentTaskBlueprint(
                task_id=1,
                handler_key="trusted",
                role="worker",
                prompt="bounded work",
                artifact_namespace="agent/1",
                parent_task_id=99,
            ),
        )
    )
    with pytest.raises(AgentCoordinationError, match="parent_task_id"):
        nested.validate()


def test_application_rejects_non_finite_subagent_timeout_policy() -> None:
    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="trusted",
        role="worker",
        prompt="bounded work",
        artifact_namespace="agent/1",
    )
    proposal = AgentPlanProposal(tasks=(blueprint,))
    config = AgentConfig.from_inputs(
        "bounded work",
        llm=_NoKeyModel(),
        subagent_max_timeout_seconds=math.inf,
    )
    application = AgentApplication(config)

    async def handler(_: object) -> str:
        return "ok"

    with pytest.raises(ValueError, match="subagent_max_timeout_seconds"):
        application._validate_subagent_plan(proposal, {"trusted": handler})  # type: ignore[arg-type]


def test_sync_facade_rejects_running_event_loop() -> None:
    config = AgentConfig.from_inputs("bounded work", llm=_NoKeyModel())
    application = AgentApplication(config)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="async event loop"):
            application.run_subagents()  # type: ignore[attr-defined]

    asyncio.run(exercise())
