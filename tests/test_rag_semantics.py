from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

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
