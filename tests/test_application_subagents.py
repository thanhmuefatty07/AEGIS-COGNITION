from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace

import pytest

from aegis_cognition.application import AgentApplication
from aegis_cognition.browser_adapters import build_readonly_browser_handler
from aegis_cognition.config import AgentConfig
import aegis_cognition.desktop_service as desktop_service_module
from aegis_cognition.desktop_service import DesktopService
from aegis_cognition.extensions import ExtensionManifest, ToolSpec
from aegis_cognition.subagents import (
    AgentCoordinationError,
    AgentHandlerRegistration,
    AgentPlanProposal,
    AgentTaskBlueprint,
    AgentTaskContext,
)
from core.python.aegis.connection_clients import ConnectionResponse, OpenAIResponsesClient
from core.python.aegis.connections import ConnectionRecord
from core.python.aegis_adapter import AegisAdapter


class _NoKeyModel:
    requires_api_key = False


class _Gateway:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.prompts: list[str] = []
        self.call_options: list[dict[str, object]] = []

    async def ainvoke(self, prompt: str, **options: object) -> object:
        self.prompts.append(prompt)
        self.call_options.append(options)
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
                    "side_effect_class": "ModelInference",
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
    assert gateway.call_options[1]["max_output_tokens"] == 100
    assert "max_output_tokens" not in gateway.call_options[0]
    assert "max_output_tokens" not in gateway.call_options[2]
    assert "HANDLER_ALLOWLIST" in gateway.prompts[0]
    assert "research" in gateway.prompts[0]
    assert "RESEARCH_X_APP_ONLY_CONFIGURED: false" in gateway.prompts[0]
    assert "CHILD_RESULT_PACKETS" in gateway.prompts[2]


async def test_application_preserves_per_call_provider_usage_for_bounded_workers() -> None:
    calls: list[dict[str, object]] = []

    class Gateway:
        async def ainvoke_with_usage(self, prompt: str, **options: object) -> object:
            calls.append({"prompt": prompt, **options})
            return SimpleNamespace(
                output="bounded child evidence",
                usage={"input_tokens": 11, "output_tokens": 6},
            )

    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="model",
        role="evidence worker",
        prompt="collect evidence",
        artifact_namespace="agent/1",
        capabilities=("model_inference",),
        side_effect_class="ModelInference",
        token_budget=17,
    )
    config = AgentConfig.from_inputs(
        "bounded model worker",
        llm=_NoKeyModel(),
        subagents_allow_dynamic_plans=False,
    )
    application = AgentApplication(config, gateway_factory=lambda **_: Gateway())
    result = await application.arun_subagents(
        plan=AgentPlanProposal(tasks=(blueprint,)),
        root_synthesizer=lambda children: children[0].summary,
        require_native_authority=False,
    )

    assert result.root_output == "bounded child evidence"
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == 17
    assert (result.child_results[0].tokens_in, result.child_results[0].tokens_out) == (11, 6)


async def test_model_worker_budget_reaches_provider_request_without_api_key() -> None:
    observed: dict[str, object] = {}
    connection = ConnectionRecord(
        connection_id="openai-offline",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="openai-responses",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update(json.loads(body))
        return ConnectionResponse(
            200,
            b'{"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"offline child evidence"}]}],"usage":{"input_tokens":11,"output_tokens":6}}',
        )

    client = OpenAIResponsesClient(
        connection,
        model="gpt-test",
        secret_resolver=lambda _secret_ref: pytest.fail("keyless test must not resolve a secret"),
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="model",
        role="evidence worker",
        prompt="collect evidence",
        artifact_namespace="agent/1",
        capabilities=("model_inference",),
        side_effect_class="ModelInference",
        token_budget=17,
    )
    config = AgentConfig.from_inputs(
        "bounded model worker",
        llm=client,
        subagents_allow_dynamic_plans=False,
        subagent_public_research=False,
    )

    def gateway_factory(**kwargs: object) -> AegisAdapter:
        kwargs["provider"] = "openai"
        kwargs["model"] = "gpt-test"
        return AegisAdapter(**kwargs)

    application = AgentApplication(config, gateway_factory=gateway_factory)
    result = await application.arun_subagents(
        plan=AgentPlanProposal(tasks=(blueprint,)),
        root_synthesizer=lambda children: children[0].summary,
        require_native_authority=False,
    )

    assert result.root_output == "offline child evidence"
    assert observed["max_output_tokens"] == 17
    assert (result.child_results[0].tokens_in, result.child_results[0].tokens_out) == (11, 6)


async def test_application_model_child_can_spawn_a_bounded_grandchild(monkeypatch: pytest.MonkeyPatch) -> None:
    dynamic_child = {
        "task_id": 2,
        "handler_key": "model",
        "role": "dynamic evidence worker",
        "prompt": "refine the parent evidence",
        "artifact_namespace": "agent/2",
        "dependencies": [1],
        "capabilities": ["model_inference"],
        "exclusive_resources": [],
        "token_budget": 100,
        "timeout_seconds": 2.0,
        "memory_bytes": 1_024,
        "side_effect_class": "ModelInference",
        "parent_task_id": 1,
        "attempt_id": 1,
    }
    gateway = _Gateway(
        [
            _unsigned_plan(),
            json.dumps({"schema": "aegis-agent-plan-v1", "tasks": [dynamic_child]}),
            "grandchild evidence",
            "root synthesis",
        ]
    )

    config = AgentConfig.from_inputs("research with bounded delegation", llm=_NoKeyModel())
    application = AgentApplication(config, gateway_factory=lambda **_: gateway)
    monkeypatch.setattr(application, "prepare", lambda _task: ("formatted", "system"))

    result = await application.arun_subagents(require_native_authority=False)

    assert result.status == "COMPLETED"
    assert result.child_results[0].summary == "spawned 1 bounded child task(s): 2"
    assert result.child_results[1].summary == "grandchild evidence"
    assert len(gateway.prompts) == 4
    assert "more bounded workers" in gateway.prompts[1]
    assert 'DYNAMIC_HANDLER_ALLOWLIST: ["model", "research"]' in gateway.prompts[1]


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
        handlers={"trusted": AgentHandlerRegistration(handler, capabilities=(), side_effect_class="ReadOnly")},
        root_synthesizer=lambda children: tuple(child.summary for child in children),
        require_native_authority=True,
    )

    assert result.status == "COMPLETED"
    assert result.graph_authority == "native_runtime"
    assert result.root_output == ("done-1", "done-2")
    assert len(result.child_results) == 2


def test_desktop_subagent_bridge_binds_host_handlers_into_real_application(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DesktopService(profile_root=tmp_path)
    service._opened_root = tmp_path
    connection = SimpleNamespace(connection_id="local")
    monkeypatch.setattr(service, "_parallel_workers_enabled", lambda: True)
    monkeypatch.setattr(service, "_connection_for_conversation", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(service, "_verified_model_descriptor", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_provider_client", lambda *_args, **_kwargs: _NoKeyModel())
    executed: list[int] = []

    async def host_handler(context: AgentTaskContext) -> object:
        executed.append(context.task_id)
        return context.success(f"host-result-{context.task_id}")

    plan = AgentPlanProposal(
        tasks=tuple(
            AgentTaskBlueprint(
                task_id=task_id,
                handler_key="host",
                role=f"worker-{task_id}",
                prompt=f"bounded task {task_id}",
                artifact_namespace=f"desktop-bridge/{task_id}",
                capabilities=("read_only",),
                timeout_seconds=5.0,
                memory_bytes=1_024,
            )
            for task_id in (1, 2)
        )
    )
    try:
        result = service._run_subagents(
            {
                "task": "Run two independent host-owned tasks.",
                "owner_id": "local-profile",
                "connection_id": "local",
                "model_id": "test-model",
                "plan": plan.as_dict(),
                "allow_dynamic_plans": False,
                "require_native_authority": False,
                "max_concurrency": 2,
                "max_tasks": 2,
                "max_dynamic_tasks": 2,
            },
            root_synthesizer=lambda packets: " | ".join(packet.summary for packet in packets),
            trusted_handlers={
                "host": AgentHandlerRegistration(
                    host_handler,
                    capabilities=("read_only",),
                    side_effect_class="ReadOnly",
                )
            },
        )

        assert result["status"] == "COMPLETED"
        assert set(executed) == {1, 2}
        assert "host-result-1" in result["root_output"]
        assert "host-result-2" in result["root_output"]
        assert result["failed_task_ids"] == []
    finally:
        service.close()


def test_provider_delegation_runs_host_readonly_tools_through_real_runtime(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DesktopService(profile_root=tmp_path)
    service._opened_root = tmp_path
    connection = SimpleNamespace(connection_id="local")
    monkeypatch.setattr(service, "_parallel_workers_enabled", lambda: True)
    monkeypatch.setattr(service, "_connection_for_conversation", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(service, "_verified_model_descriptor", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_provider_client", lambda *_args, **_kwargs: _NoKeyModel())
    calls: list[dict[str, object]] = []
    specs = tuple(
        ToolSpec(
            name=f"fixture.lookup-{task_id}",
            description="Read one bounded test record",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
            effect_class="read_only",
            capabilities=("read_only",),
            extension_id="fixture-provider-lookups",
        )
        for task_id in (1, 2)
    )
    service._extension_registry.register(
        ExtensionManifest(
            extension_id="fixture-provider-lookups",
            version="1",
            description="Read-only provider delegation integration test",
            capabilities=("read_only",),
            tool_names=tuple(spec.name for spec in specs),
        ),
        tuple(
            (
                spec,
                lambda arguments, selected=spec: (
                    calls.append({"tool": selected.name, **dict(arguments)})
                    or {"key": arguments["key"], "status": "verified-fixture"}
                ),
            )
            for spec in specs
        ),
    )
    scope = desktop_service_module._ProviderToolTurnScope(
        connection_id="local",
        model_id="test-model",
        owner_id="local-profile",
        catalog_revision=service._extension_registry.catalog_revision,
        conversation_id="test-conversation",
        execution_id="test-execution",
        visible_tool_descriptor_hashes=tuple((spec.name, spec.descriptor_hash) for spec in specs),
    )
    token = desktop_service_module._ACTIVE_PROVIDER_TOOL_SCOPE.set(scope)
    try:
        try:
            result = asyncio.run(
                service._delegate_provider_subagents(
                    {
                        "task": "Read two independent test records.",
                        "workers": [
                            {
                                "handler_key": "tool",
                                "role": f"lookup-{task_id}",
                                "prompt": f"Read record {task_id}.",
                                "tool_name": specs[task_id - 1].name,
                                "tool_input": {"key": f"record-{task_id}"},
                            }
                            for task_id in (1, 2)
                        ],
                    }
                )
            )
        finally:
            desktop_service_module._ACTIVE_PROVIDER_TOOL_SCOPE.reset(token)
        assert result["status"] == "COMPLETED"
        assert result["worker_count"] == 2
        assert result["failed_task_ids"] == []
        assert {call["tool"] for call in calls} == {spec.name for spec in specs}
        events = service._read_subagent_events({"run_id": result["run_id"], "max_messages": 4})
        requests = [event for event in events["events"] if event["message_kind"] == "TASK_REQUEST"]
        results = [event for event in events["events"] if event["message_kind"] == "TASK_RESULT"]
        assert len(requests) == len(results) == 2
        assert all(event["payload"].get("redacted") is True for event in requests)
        assert "Read record" not in json.dumps(events)
        assert "verified-fixture" in result["evidence"]
    finally:
        service.close()


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
        side_effect_class="NetworkRead",
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
        side_effect_class="ModelInference",
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


async def test_application_binds_readonly_browser_handler_from_host_options() -> None:
    class Body:
        async def evaluate(self, expression: str, max_chars: int) -> str:
            assert "createTreeWalker" in expression
            assert max_chars == 5_001
            return "public evidence"

    class Page:
        def locator(self, _selector: str) -> Body:
            return Body()

        async def title(self) -> str:
            return "Source"

    class Session:
        page = Page()

        async def close(self) -> None:
            return None

    async def factory(**_kwargs: object) -> Session:
        return Session()

    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="browser",
        role="source reader",
        prompt="Read https://example.com/source",
        artifact_namespace="agent/1",
        capabilities=("browser", "network_read"),
        side_effect_class="NetworkRead",
    )
    config = AgentConfig.from_inputs(
        "read one public source",
        llm=_NoKeyModel(),
        subagent_browser_handler=build_readonly_browser_handler(factory),
    )
    application = AgentApplication(config, gateway_factory=lambda **_: object())
    result = await application.arun_subagents(
        plan={"schema": "aegis-agent-plan-v1", "tasks": [blueprint.as_dict()]},
        root_synthesizer=lambda children: children[0].summary,
        require_native_authority=False,
    )

    assert result.child_results[0].status == "SUCCEEDED"
    assert "public evidence" in result.root_output
    assert result.child_results[0].claims[0].evidence_class == "SOURCE-BACKED"


async def test_application_executes_static_nested_parent_plan() -> None:
    calls: list[int] = []

    async def handler(context: AgentTaskContext) -> str:
        calls.append(context.task_id)
        return f"evidence-{context.task_id}"

    parent = AgentTaskBlueprint(
        task_id=1,
        handler_key="trusted",
        role="parent",
        prompt="prepare evidence",
        artifact_namespace="agent/1",
    )
    child = AgentTaskBlueprint(
        task_id=2,
        handler_key="trusted",
        role="child",
        prompt="refine parent evidence",
        artifact_namespace="agent/2",
        dependencies=(1,),
        parent_task_id=1,
    )
    application = AgentApplication(AgentConfig.from_inputs("nested plan", llm=_NoKeyModel()))

    result = await application.arun_subagents(
        plan=AgentPlanProposal(tasks=(parent, child)),
        handlers={"trusted": AgentHandlerRegistration(handler, capabilities=(), side_effect_class="ReadOnly")},
        root_synthesizer=lambda results: tuple(item.summary for item in results),
        require_native_authority=False,
    )

    assert calls == [1, 2]
    assert result.root_output == ("evidence-1", "evidence-2")
    assert result.child_results[1].parent_task_id == 1


async def test_application_rejects_custom_handler_without_host_effect_policy() -> None:
    invoked: list[int] = []
    application = AgentApplication(AgentConfig.from_inputs("bounded work", llm=_NoKeyModel()))
    plan = AgentPlanProposal(
        tasks=(
            AgentTaskBlueprint(
                task_id=1,
                handler_key="custom",
                role="worker",
                prompt="perform host-registered work",
                artifact_namespace="agent/1",
                capabilities=("read_only",),
                side_effect_class="ReadOnly",
            ),
        )
    )

    async def handler(context: AgentTaskContext) -> object:
        invoked.append(context.task_id)
        return context.success("completed")

    with pytest.raises(TypeError, match="AgentHandlerRegistration"):
        await application.arun_subagents(
            plan=plan,
            handlers={"custom": handler},
            root_synthesizer=lambda children: len(children),
            require_native_authority=False,
        )

    assert invoked == []


async def test_application_rejects_custom_handler_plan_policy_mismatch_before_execution() -> None:
    invoked: list[int] = []
    application = AgentApplication(AgentConfig.from_inputs("bounded work", llm=_NoKeyModel()))
    plan = AgentPlanProposal(
        tasks=(
            AgentTaskBlueprint(
                task_id=1,
                handler_key="custom",
                role="worker",
                prompt="perform host-registered work",
                artifact_namespace="agent/1",
                capabilities=("read_only",),
                side_effect_class="ReadOnly",
            ),
        )
    )

    async def handler(context: AgentTaskContext) -> object:
        invoked.append(context.task_id)
        return context.success("completed")

    registration = AgentHandlerRegistration(
        handler=handler,
        capabilities=("network_read",),
        side_effect_class="NetworkRead",
    )
    with pytest.raises(ValueError, match="capabilities do not match the host handler registration"):
        await application.arun_subagents(
            plan=plan,
            handlers={"custom": registration},
            root_synthesizer=lambda children: len(children),
            require_native_authority=False,
        )

    assert invoked == []


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
        application._validate_subagent_plan(
            proposal,
            {"trusted": handler},
            handler_registrations={
                "trusted": AgentHandlerRegistration(
                    handler,
                    capabilities=("read_only",),
                    side_effect_class="ReadOnly",
                )
            },
        )

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


def test_application_rejects_model_worker_without_output_budget() -> None:
    blueprint = AgentTaskBlueprint(
        task_id=1,
        handler_key="model",
        role="worker",
        prompt="bounded work",
        artifact_namespace="agent/1",
    )
    proposal = AgentPlanProposal(tasks=(blueprint,))
    application = AgentApplication(AgentConfig.from_inputs("bounded work", llm=_NoKeyModel()))

    async def handler(_: object) -> str:
        return "not used"

    with pytest.raises(ValueError, match="requires a positive token_budget"):
        application._validate_subagent_plan(proposal, {"model": handler})


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


@pytest.mark.parametrize(
    ("allowed_capabilities", "allowed_effects", "claimed_capabilities", "claimed_effect", "expected_error"),
    [
        (("read_only",), ("ReadOnly",), ("read_only",), "ReadOnly", "outside the host policy"),
        (
            ("read_only", "network_read"),
            ("ReadOnly", "NetworkRead"),
            ("read_only",),
            "ReadOnly",
            "must declare network_read",
        ),
        (
            ("read_only", "network_read"),
            ("ReadOnly", "NetworkRead"),
            ("network_read",),
            "ReadOnly",
            "must use NetworkRead",
        ),
    ],
)
async def test_research_handler_cannot_understate_host_capability_policy(
    allowed_capabilities: tuple[str, ...],
    allowed_effects: tuple[str, ...],
    claimed_capabilities: tuple[str, ...],
    claimed_effect: str,
    expected_error: str,
) -> None:
    provider_calls: list[str] = []

    async def query_provider(query: str, **_context: object) -> list[object]:
        provider_calls.append(query)
        return []

    config = AgentConfig.from_inputs(
        "verify subagent capability binding",
        llm=_NoKeyModel(),
        subagent_allowed_capabilities=allowed_capabilities,
        subagent_allowed_side_effect_classes=allowed_effects,
        subagent_research_router=query_provider,
    )
    application = AgentApplication(config)
    plan = AgentPlanProposal(
        tasks=(
            AgentTaskBlueprint(
                task_id=1,
                handler_key="research",
                role="researcher",
                prompt="bounded query",
                artifact_namespace="agent/1",
                capabilities=claimed_capabilities,
                side_effect_class=claimed_effect,
            ),
        )
    )

    with pytest.raises(ValueError, match=expected_error):
        await application.arun_subagents(
            plan=plan,
            root_synthesizer=lambda children: len(children),
            require_native_authority=False,
        )

    assert provider_calls == []
