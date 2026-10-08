from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Barrier
from types import SimpleNamespace

from aegis_cognition.application import AgentApplication
from aegis_cognition.config import AgentConfig
from aegis_cognition.subagents import AgentSupervisor, AgentTaskSpec, build_model_subagent_handler
from aegis_cognition.subagents import AgentPlanProposal, AgentTaskBlueprint


@asynccontextmanager
async def _fake_runtime_guard(**_: object):
    yield None


def _spec(task_id: int, handler: object) -> AgentTaskSpec:
    return AgentTaskSpec(
        task_id=task_id,
        role=f"role-{task_id}",
        prompt=f"prompt-{task_id}",
        handler=handler,  # type: ignore[arg-type]
        artifact_namespace=f"agent/{task_id}",
        token_budget=8,
    )


async def test_independent_sync_model_invocations_do_not_block_each_other() -> None:
    arrivals = Barrier(2)

    def invoke(_prompt: str, *, max_output_tokens: int) -> dict[str, object]:
        assert max_output_tokens == 8
        arrivals.wait(timeout=2)
        return {"output_text": "bounded child result", "usage": {"input_tokens": 1, "output_tokens": 1}}

    handler = build_model_subagent_handler(invoke)
    supervisor = AgentSupervisor(
        run_id="sync-model-concurrency",
        max_concurrency=2,
        require_native_authority=False,
        runtime_guard_factory=_fake_runtime_guard,
    )

    result = await supervisor.run((_spec(1, handler), _spec(2, handler)), lambda children: len(children))

    assert result.status == "COMPLETED", result.child_results
    assert result.root_output == 2
    assert all(child.status == "SUCCEEDED" for child in result.child_results)


async def test_application_offloads_sync_gateway_fallback_for_parallel_children(monkeypatch) -> None:
    arrivals = Barrier(2)
    monkeypatch.setattr("aegis_cognition.runtime._native_module", lambda: None)

    class Gateway:
        def run(self, prompt: str, **options: object) -> object:
            assert options["max_output_tokens"] == 8
            arrivals.wait(timeout=2)
            return SimpleNamespace(output=f"evidence for {prompt}", usage={"input_tokens": 1, "output_tokens": 1})

    class NoKeyModel:
        requires_api_key = False

    config = AgentConfig.from_inputs(
        "run two independent evidence workers",
        llm=NoKeyModel(),
        subagents_allow_dynamic_plans=False,
    )
    application = AgentApplication(config, gateway_factory=lambda **_: Gateway())
    monkeypatch.setattr(application, "prepare", lambda _task: ("formatted", "system"))
    plan = AgentPlanProposal(
        tasks=tuple(
            AgentTaskBlueprint(
                task_id=task_id,
                handler_key="model",
                role=f"worker-{task_id}",
                prompt=f"prompt-{task_id}",
                artifact_namespace=f"agent/{task_id}",
                capabilities=("model_inference",),
                token_budget=8,
                side_effect_class="ModelInference",
            )
            for task_id in (1, 2)
        )
    )

    result = await application.arun_subagents(
        plan=plan,
        root_synthesizer=lambda children: len(children),
        require_native_authority=False,
    )

    assert result.status == "COMPLETED", result.child_results
    assert result.root_output == 2
    assert all(child.status == "SUCCEEDED" for child in result.child_results)
