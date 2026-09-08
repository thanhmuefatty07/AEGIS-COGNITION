from types import SimpleNamespace

import pytest

from core.python.aegis.context_compiler import ContextCompilationError, ContextCompiler


def _candidate(session_id: int, content_hash: str = "hash-a", score: float = 0.9):
    return SimpleNamespace(segment_id=session_id, evidence_ref_hash=content_hash, score=score)


def _record(session_id: int, content_hash: str = "hash-a", content: str = "authorized source"):
    return SimpleNamespace(
        session_id=session_id,
        content_hash=content_hash,
        timestamp=1_700_000_000_000,
        scope_kind="USER_PRIVATE",
        owner_id="local-profile",
        content=content,
    )


class _Learning:
    def __init__(self, record):
        self.record = record

    def read_session_record_scoped(self, session_id, *, scope_kind, owner_id):
        del scope_kind, owner_id
        return self.record if self.record and self.record.session_id == session_id else None


class _RustSelectingLearning(_Learning):
    def select_context_items(self, items, token_budget, *, mandatory_session_ids=()):
        del token_budget, mandatory_session_ids
        return {
            "backend": "rust-context-governor-v1",
            "selected_node_ids": [f"0x{int(item['session_id']):x}" for item in items],
        }


def test_context_compiler_hydrates_authorized_source_and_emits_manifest():
    result = ContextCompiler(_Learning(_record(42))).compile(
        "query",
        [_candidate(42)],
    )

    assert result.status == "compiled"
    assert "authorized source" in result.rendered
    assert "CANDIDATE REFERENCES ONLY" not in result.rendered
    assert result.manifest_hash
    assert result.accounting == "estimated-byte-heuristic-v1"


def test_context_compiler_rejects_stale_mandatory_candidate():
    with pytest.raises(ContextCompilationError, match="changed"):
        ContextCompiler(_Learning(_record(42, content_hash="current"))).compile(
            "query",
            [_candidate(42, content_hash="old")],
            mandatory_session_ids=(42,),
        )


def test_context_compiler_records_rust_selection_backend():
    result = ContextCompiler(_RustSelectingLearning(_record(42))).compile(
        "query",
        [_candidate(42)],
    )

    assert result.manifest["selection_backend"] == "rust-context-governor-v1"


def test_context_compiler_fails_when_mandatory_content_exceeds_budget():
    with pytest.raises(ContextCompilationError, match="token budget"):
        ContextCompiler(_Learning(_record(42, content="x" * 100)), token_budget=8).compile(
            "query",
            [_candidate(42)],
            mandatory_session_ids=(42,),
        )
