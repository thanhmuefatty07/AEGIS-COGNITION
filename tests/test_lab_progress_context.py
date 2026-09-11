from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from aegis_cognition.lab import (
    AdaptiveDecision,
    ClaimRecord,
    ExperimentSpec,
    HypothesisRecord,
    LabApplication,
    LabRun,
    ObservationRecord,
    SourceRecord,
)


def _evidence_run() -> LabRun:
    run = LabRun(
        "validate a bounded research claim",
        scope=("AI agent lab",),
        non_goals=("absolute certainty",),
        max_steps=4,
        token_budget=400,
    )
    run.add_source(
        SourceRecord(
            "source-1",
            "https://example.test/paper",
            "content-hash",
            "snapshot-hash",
            1,
            trust_tier=1,
            relation="supports",
        )
    )
    run.add_claim(
        ClaimRecord(
            "claim-1",
            "A bounded claim with an explicit citation.",
            ("source-1",),
            7_500,
            "supported",
        )
    )
    run.add_hypothesis(
        HypothesisRecord(
            "hypothesis-1",
            "The bounded claim remains valid under the declared test.",
            5_000,
            ("a negative result",),
            ("claim-1",),
        )
    )
    run.add_experiment(
        ExperimentSpec(
            "experiment-1",
            "hypothesis-1",
            "paired comparison",
            ("controller context",),
            ("same mission",),
            (1, 2, 3, 4, 5),
            1,
        )
    )
    run.add_observation(
        ObservationRecord(
            "observation-1",
            "experiment-1",
            1,
            0.75,
            "score",
            "raw-artifact-hash",
            "environment-hash",
        )
    )
    return run


def test_controller_context_is_bounded_and_contains_epistemic_state() -> None:
    run = _evidence_run()

    context = run.controller_context()
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert context["schema"] == "aegis-lab-controller-context-v1"
    assert context["counts"] == {
        "sources": 1,
        "claims": 1,
        "hypotheses": 1,
        "experiments": 1,
        "observations": 1,
        "tool_executions": 0,
    }
    assert context["claims"][0]["status"] == "supported"
    assert context["experiments"][0]["observed_count"] == 1
    assert context["observations"][0]["epistemic_status"] == "OBSERVED"
    assert context["event_cursor"] == run.event_cursor()
    assert len(encoded) <= context["record_limits"]["max_chars"]


def test_controller_context_keeps_latest_evidence_frontier() -> None:
    run = LabRun("retain the latest evidence frontier", max_steps=2, token_budget=200)
    for index in range(1, 6):
        run.add_source(
            SourceRecord(
                f"source-{index}",
                f"https://example.test/paper-{index}",
                f"content-{index}",
                f"snapshot-{index}",
                index,
            )
        )

    context = run.controller_context()

    assert [item["source_id"] for item in context["sources"]] == [
        "source-1",
        "source-2",
        "source-4",
        "source-5",
    ]


def test_progress_checkpoint_is_admitted_and_settled() -> None:
    checkpoints: list[dict[str, object]] = []

    async def persist_checkpoint(*, checkpoint: dict[str, object], run: LabRun) -> dict[str, object]:
        checkpoints.append({"checkpoint": checkpoint, "mission_id": run.mission_id})
        return {"persisted": True}

    config = SimpleNamespace(trust_level="DEV", options={}, max_steps=4)
    application = LabApplication(
        config=config,
        gateway_factory=lambda **_: object(),
        telemetry=None,
        correlation=None,
        checkpoint_effect=persist_checkpoint,
    )
    run = LabRun("checkpoint a long mission", max_steps=4, token_budget=400)
    application._prepare_execution_cells(run, {})

    result = asyncio.run(
        application._run_progress_checkpoint(
            run,
            {},
            decision=AdaptiveDecision("research", 1, 100, "continue with the next bounded step"),
        )
    )

    assert result is True
    assert checkpoints[0]["checkpoint"]["schema"] == "aegis-lab-continuation-v1"
    assert checkpoints[0]["checkpoint"]["next_step"] == 2
    assert [item["status"] for item in run.tool_execution_admissions.values()] == ["ADMITTED"]
    assert len(run.tool_executions) == 1
    execution_id = next(iter(run.tool_executions))
    assert run.tool_execution_admissions[execution_id]["tool_name"] == "lab.progress_checkpoint"
    assert run.blockers == []


def test_progress_checkpoint_failure_is_not_treated_as_success() -> None:
    def fail_checkpoint(**_kwargs):
        raise OSError("checkpoint store unavailable")

    application = LabApplication(
        config=SimpleNamespace(trust_level="DEV", options={}, max_steps=2),
        gateway_factory=lambda **_: object(),
        telemetry=None,
        correlation=None,
        checkpoint_effect=fail_checkpoint,
    )
    run = LabRun("fail closed checkpoint", max_steps=2, token_budget=200)
    application._prepare_execution_cells(run, {})

    result = asyncio.run(
        application._run_progress_checkpoint(
            run,
            {},
            decision=AdaptiveDecision("research", 1, 100, "persist before continuing"),
        )
    )

    assert result is False
    assert "progress_checkpoint_failed:OSError" in run.blockers
    execution_id = next(iter(run.tool_execution_admissions))
    assert execution_id in run.tool_executions
    assert run.tool_execution_admissions[execution_id]["tool_name"] == "lab.progress_checkpoint"
    settlements = [
        event.payload for event in run.events if event.kind == "tool_execution_recorded"
    ]
    assert settlements[-1]["status"] == "REJECTED"


def test_agent_lab_binds_checkpoint_to_conversation_execution(monkeypatch) -> None:
    import aegis_cognition.application as application_module

    class FakeManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def read(self, *_args, **_kwargs):
            raise ValueError("conversation not found")

        def create(self, _conversation_id, **_kwargs):
            self.calls.append(("create", {}))
            return SimpleNamespace(revision=1)

        def append_turn(self, _conversation_id, **kwargs):
            self.calls.append(("append_turn", kwargs))
            revision = 2 if len([call for call in self.calls if call[0] == "append_turn"]) == 1 else 3
            return SimpleNamespace(revision=revision, turn_id=kwargs["turn_id"])

        def start_execution(self, _conversation_id, **kwargs):
            self.calls.append(("start_execution", kwargs))
            return SimpleNamespace(revision=4, execution_id=kwargs["execution_id"])

        def checkpoint_execution(self, _conversation_id, **kwargs):
            self.calls.append(("checkpoint_execution", kwargs))
            return SimpleNamespace(revision=5, checkpoint_seq=kwargs["sequence"])

        def append_part(self, _conversation_id, **kwargs):
            self.calls.append(("append_part", kwargs))
            return SimpleNamespace(revision=6)

        def finish_execution(self, _conversation_id, **kwargs):
            self.calls.append(("finish_execution", kwargs))
            return SimpleNamespace(revision=7)

    manager = FakeManager()

    class FakeLabApplication:
        def __init__(self, **kwargs) -> None:
            self.checkpoint_effect = kwargs["checkpoint_effect"]

        async def run(self):
            self.checkpoint_effect(
                checkpoint={
                    "schema": "aegis-lab-continuation-v1",
                    "step": 1,
                    "progress_context": {},
                },
                run=object(),
            )
            return (
                SimpleNamespace(
                    output="lab output",
                    trust_level="DEV",
                    provider="test",
                    hot_commit=None,
                    correlation=None,
                ),
                SimpleNamespace(manifest={}, events=()),
            )

    monkeypatch.setattr(application_module, "ConversationManager", lambda: manager)
    monkeypatch.setattr(application_module, "LabApplication", FakeLabApplication)
    monkeypatch.setattr(application_module.AgentApplication, "_index_completed_run", lambda *_, **__: None)

    config = SimpleNamespace(
        task="run a long research mission",
        llm=SimpleNamespace(requires_api_key=False),
        trust_level="DEV",
        trust_policy_hash="",
        options={
            "lab": True,
            "conversation_id": "conv-1",
            "conversation_connection_id": "local",
            "conversation_model_id": "model-a",
            "conversation_provider_kind": "local",
        },
    )
    result = asyncio.run(application_module.AgentApplication(config).arun())

    assert result.output == "lab output"
    checkpoint_calls = [call for call in manager.calls if call[0] == "checkpoint_execution"]
    assert len(checkpoint_calls) == 1
    assert checkpoint_calls[0][1]["sequence"] == 1
    assert checkpoint_calls[0][1]["state"] == "LAB_STEP_COMPLETED"
    assert [call[0] for call in manager.calls][-2:] == ["append_part", "finish_execution"]
