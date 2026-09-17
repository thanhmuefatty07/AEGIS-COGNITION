from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import aegis_cognition.application as application_module
from aegis_cognition.application import AgentApplication
from aegis_cognition.observability import RuntimeTelemetry
from aegis_cognition.rag import RAGManager


def test_candidate_report_never_claims_quality_or_hydrated_context() -> None:
    candidate = SimpleNamespace(
        evidence_ref_hash="aabbcc",
        segment_id=101,
        score=0.985,
        tier="ColdVectorExpansion",
    )
    manager = Mock()
    manager.search_past.return_value = SimpleNamespace(results=(candidate,))

    report = RAGManager(learning_manager=manager).retrieve_and_format("test query")

    assert "CANDIDATE REFERENCES ONLY" in report
    assert "NOT MODEL CONTEXT" in report
    assert "aabbcc" in report
    assert "QUALITY GUARANTEES" not in report
    assert "Độ tin cậy" not in report


def test_candidate_search_is_exposed_without_promoting_references() -> None:
    candidate = object()
    manager = Mock()
    manager.search_past.return_value = SimpleNamespace(results=(candidate,))

    candidates = RAGManager(learning_manager=manager).retrieve_candidates("query")

    assert candidates == (candidate,)
    manager.search_past.assert_called_once_with("query", top_k=3)


def test_expected_retrieval_errors_degrade_to_no_candidates() -> None:
    manager = Mock()
    manager.search_past.side_effect = RuntimeError("native index unavailable")

    assert RAGManager(learning_manager=manager).retrieve_candidates("query") == ()


def test_missing_native_bridge_emits_degraded_signal_without_fabricating_context(monkeypatch) -> None:
    manager = Mock()
    manager.search_past.side_effect = ImportError("native bridge unavailable")
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)
    telemetry = RuntimeTelemetry()
    application = AgentApplication(SimpleNamespace(options={}), telemetry=telemetry)

    assert application._retrieve_context("query") == ""
    outcomes = {(event["kind"], event["outcome"]) for event in telemetry.snapshot()["events"]}
    assert ("memory", "retrieval_degraded") in outcomes


def test_application_does_not_append_candidate_metadata_to_model_task(monkeypatch) -> None:
    candidate = SimpleNamespace(evidence_ref_hash="secret-source", segment_id=7, score=1.0, tier="Candidate")
    manager = Mock()
    manager.search_past.return_value = SimpleNamespace(results=(candidate,))
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)

    application = AgentApplication(
        SimpleNamespace(options={"top_k": 3}),
        telemetry=RuntimeTelemetry(),
    )

    assert application._retrieve_context("query") == ""


def test_application_hydrates_only_when_explicitly_enabled(monkeypatch) -> None:
    candidate = SimpleNamespace(evidence_ref_hash="source-hash", segment_id=7, score=1.0, tier="Candidate")
    manager = Mock()
    manager.search_past.return_value = SimpleNamespace(results=(candidate,))
    manager.read_session_record_scoped.return_value = SimpleNamespace(
        session_id=7,
        content_hash="source-hash",
        timestamp=1_700_000_000_000,
        scope_kind="USER_PRIVATE",
        owner_id="local-profile",
        content="authorized source text",
    )
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)

    application = AgentApplication(
        SimpleNamespace(options={"top_k": 3, "hydrate_context": True}),
        telemetry=RuntimeTelemetry(),
    )

    context = application._retrieve_context("query")
    assert "authorized source text" in context
    assert "CANDIDATE REFERENCES ONLY" not in context


def test_application_searches_memory_inside_configured_owner_scope(monkeypatch) -> None:
    manager = Mock()
    manager.search_past_scoped.return_value = SimpleNamespace(results=())
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)

    application = AgentApplication(
        SimpleNamespace(
            options={
                "top_k": 3,
                "memory_scope": "PROJECT_PRIVATE",
                "memory_owner_id": "workspace-owner",
            }
        ),
        telemetry=RuntimeTelemetry(),
    )

    assert application._retrieve_context("query") == ""
    manager.search_past_scoped.assert_called_once_with(
        "query",
        top_k=3,
        scope_kind="PROJECT_PRIVATE",
        owner_id="workspace-owner",
    )
    manager.search_past.assert_not_called()


def test_active_memory_is_hydrated_only_after_authorized_inspect(monkeypatch) -> None:
    candidate = SimpleNamespace(
        memory_id="0x9",
        owner_id="workspace-owner",
        scope_kind="PROJECT_PRIVATE",
        lifecycle="ACTIVE",
        validation="ACCEPTED",
        content_hash="memory-hash",
    )
    record = SimpleNamespace(
        memory_id="0x9",
        owner_id="workspace-owner",
        scope_kind="PROJECT_PRIVATE",
        lifecycle="ACTIVE",
        validation="ACCEPTED",
        content_hash="memory-hash",
        content="user prefers concise reports",
    )
    manager = Mock()
    manager.search_past_scoped.return_value = SimpleNamespace(results=())
    manager.search_memories.return_value = (candidate,)
    manager.inspect_memory.return_value = record
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)

    application = AgentApplication(
        SimpleNamespace(
            options={
                "top_k": 3,
                "memory_scope": "PROJECT_PRIVATE",
                "memory_owner_id": "workspace-owner",
            }
        ),
        telemetry=RuntimeTelemetry(),
    )

    context = application._retrieve_context("report preference")

    assert "user prefers concise reports" in context
    assert "ACTIVE MEMORY DATA" in context
    manager.inspect_memory.assert_called_once_with(
        "0x9",
        scope_kind="PROJECT_PRIVATE",
        owner_id="workspace-owner",
    )


def test_active_memory_search_does_not_hydrate_candidate_content():
    candidate = SimpleNamespace(
        memory_id="0xa",
        owner_id="local-profile",
        scope_kind="USER_PRIVATE",
        lifecycle="CANDIDATE",
        validation="UNREVIEWED",
        content_hash="candidate-hash",
    )
    manager = Mock()
    manager.search_memories.return_value = (candidate,)
    rag = RAGManager(learning_manager=manager, top_k=3)

    assert rag.retrieve_active_memories("preference") == ()
    manager.inspect_memory.assert_not_called()


def test_internal_memory_proposals_are_removed_and_bounded() -> None:
    public_output, candidates = AgentApplication._split_memory_proposals(
        {
            "answer": "done",
            "_aegis_memory_proposals": [
                {"content": "user prefers concise reports", "relevance_score": 0.9},
                {"content": "", "relevance_score": 0.9},
                {"content": "unsupported source", "relevance_score": 0.9, "source_session_id": "0x1"},
            ],
        },
        enabled=True,
    )

    assert public_output == {"answer": "done"}
    assert candidates == ({"content": "user prefers concise reports", "relevance_score": 0.9},)


def test_internal_memory_proposals_can_be_disabled_without_leaking_metadata() -> None:
    public_output, candidates = AgentApplication._split_memory_proposals(
        {"answer": "done", "_aegis_memory_proposals": [{"content": "x", "relevance_score": 1.0}]},
        enabled=False,
    )

    assert public_output == {"answer": "done"}
    assert candidates == ()


def test_system_context_describes_the_internal_candidate_contract() -> None:
    default_application = AgentApplication(
        SimpleNamespace(options={}, trust_level="DEV"),
        telemetry=RuntimeTelemetry(),
    )
    assert "_aegis_memory_proposals" in default_application._build_system_context("bounded task")

    disabled_application = AgentApplication(
        SimpleNamespace(options={"memory_learning": False}, trust_level="DEV"),
        telemetry=RuntimeTelemetry(),
    )
    assert "_aegis_memory_proposals" not in disabled_application._build_system_context("bounded task")


def test_completed_run_indexes_transcript_under_explicit_memory_owner_and_scope(monkeypatch) -> None:
    manager = Mock()
    manager.index_session_scoped.return_value = "content-hash"
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)

    application = AgentApplication(
        SimpleNamespace(
            options={
                "memory_scope": "PROJECT_PRIVATE",
                "memory_owner_id": "workspace-owner",
            }
        ),
        telemetry=RuntimeTelemetry(),
    )
    result = SimpleNamespace(hot_commit=SimpleNamespace(artifact_hash="a" * 64))

    assert application._index_completed_run("task", "output", result) == "content-hash"
    manager.index_session_scoped.assert_called_once_with(
        session_id=int("a" * 16, 16),
        content="Task: task\nOutput: output",
        scope_kind="PROJECT_PRIVATE",
        owner_id="workspace-owner",
    )


def test_completed_run_stages_internal_memory_candidates(monkeypatch) -> None:
    manager = Mock()
    manager.index_session_scoped.return_value = "content-hash"
    manager.sync_memory.return_value = {"durable_candidate_commit": True}
    monkeypatch.setattr("aegis_cognition.application.build_learning_manager", lambda: manager)

    application = AgentApplication(
        SimpleNamespace(
            options={
                "memory_scope": "USER_PRIVATE",
                "memory_owner_id": "local-profile",
            }
        ),
        telemetry=RuntimeTelemetry(),
    )
    result = SimpleNamespace(hot_commit=SimpleNamespace(artifact_hash="b" * 64))
    candidates = ({"content": "prefers concise reports", "relevance_score": 0.9},)

    assert (
        application._index_completed_run(
            "task",
            "output",
            result,
            memory_candidates=candidates,
        )
        == "content-hash"
    )
    manager.sync_memory.assert_called_once_with(
        session_id=f"0x{int('b' * 16, 16):x}",
        candidates=list(candidates),
        relevance_threshold=0.7,
        scope_kind="USER_PRIVATE",
        owner_id="local-profile",
    )


def test_completed_run_hides_and_stages_model_memory_proposals(monkeypatch) -> None:
    manager = Mock()
    manager.index_session_scoped.return_value = "content-hash"
    manager.sync_memory.return_value = {"durable_candidate_commit": True}
    monkeypatch.setattr(application_module, "build_learning_manager", lambda: manager)

    @asynccontextmanager
    async def fake_runtime(**_: object):
        yield None

    monkeypatch.setattr(application_module, "coordinated_runtime_task", fake_runtime)

    class Gateway:
        async def run(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                task="remember task",
                output={
                    "answer": "done",
                    "_aegis_memory_proposals": [
                        {"content": "user prefers concise reports", "relevance_score": 0.92}
                    ],
                },
                trust_level="DEV",
                provider="test",
                hot_commit=SimpleNamespace(artifact_hash="c" * 64),
                correlation=None,
            )

    config = SimpleNamespace(
        task="remember task",
        llm=SimpleNamespace(requires_api_key=False),
        trust_level="DEV",
        trust_policy_hash="",
        options={},
    )
    application = AgentApplication(config, gateway_factory=lambda **_: Gateway())
    monkeypatch.setattr(application, "prepare", lambda _task: ("formatted", "context"))

    result = asyncio.run(application.arun())

    assert result.output == {"answer": "done"}
    manager.sync_memory.assert_called_once()
