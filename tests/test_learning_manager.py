from __future__ import annotations

import json
from typing import Any

from core.python.aegis.learning import LearningManager, MemoryRecord


class _NudgeBridge:
    def __init__(self) -> None:
        self.args: tuple[Any, ...] | None = None

    def aegis_trigger_memory_nudge(self, *args: Any) -> str:
        self.args = args
        return json.dumps(
            {
                "schema": "aegis-memory-nudge-status-v3",
                "status": "committed",
                "candidate_only": True,
                "durable_commit": False,
                "durable_candidate_commit": True,
                "activation": "requires_explicit_validation",
                "staged_count": 1,
                "replayed_count": 0,
            }
        )


class _ScopedSearchBridge:
    def __init__(self) -> None:
        self.args: tuple[Any, ...] | None = None

    def aegis_search_past_sessions_scoped(self, *args: Any) -> str:
        self.args = args
        return json.dumps(
            {
                "schema": "aegis-session-search-result-v1",
                "query": args[0],
                "top_k": args[1],
                "count": 0,
                "results": [],
                "tier": "ColdVectorExpansion",
                "gate": "CandidateOnly",
            }
        )


class _SelectionBridge:
    def __init__(self) -> None:
        self.payload: str | None = None

    def aegis_select_context_items(self, payload: str, token_budget: int, mandatory: str) -> str:
        del token_budget, mandatory
        self.payload = payload
        return json.dumps(
            {
                "schema": "aegis-context-selection-v1",
                "selected_node_ids": ["0x2"],
                "token_count": 8,
                "utility_score": 1,
                "activation_node_count": 1,
                "digest": "aa",
                "backend": "rust-context-governor-v1",
            }
        )


def test_sync_memory_sends_bounded_candidate_payload_to_native_bridge() -> None:
    bridge = _NudgeBridge()
    result = LearningManager(bridge).sync_memory(
        "0x2",
        candidates=[
            {
                "content": "user prefers a bounded local workflow",
                "relevance_score": 0.91,
                "source_session_id": "0x2",
            }
        ],
        nudge_id="0x4d",
    )

    assert result["candidate_only"] is True
    assert result["durable_candidate_commit"] is True
    assert bridge.args is not None
    assert bridge.args[0:3] == ("0x2", 77, 1)
    assert json.loads(bridge.args[3]) == [
        {
            "content": "user prefers a bounded local workflow",
            "relevance_score": 0.91,
            "source_session_id": "0x2",
        }
    ]


def test_sync_memory_derives_a_stable_id_for_retries() -> None:
    first_bridge = _NudgeBridge()
    second_bridge = _NudgeBridge()
    manager_args = {
        "session_id": "0x2",
        "candidates": [{"content": "prefers concise reports", "relevance_score": 0.9}],
        "scope_kind": "USER_PRIVATE",
        "owner_id": "owner-1",
    }

    LearningManager(first_bridge).sync_memory(**manager_args)
    LearningManager(second_bridge).sync_memory(**manager_args)

    assert first_bridge.args is not None
    assert second_bridge.args is not None
    assert first_bridge.args[1] == second_bridge.args[1]
    assert first_bridge.args[1] > 0


def test_search_past_scoped_forwards_explicit_memory_boundary() -> None:
    bridge = _ScopedSearchBridge()

    result = LearningManager(bridge).search_past_scoped(
        "provider preference",
        top_k=4,
        scope_kind="PROJECT_PRIVATE",
        owner_id="workspace-owner",
    )

    assert result.results == ()
    assert bridge.args == ("provider preference", 4, "PROJECT_PRIVATE", "workspace-owner")


def test_context_selection_accepts_namespace_neutral_node_id() -> None:
    bridge = _SelectionBridge()

    result = LearningManager(bridge).select_context_items(
        [
            {
                "session_id": 1,
                "node_id": 2,
                "token_cost": 8,
                "score": 1.0,
            }
        ],
        token_budget=16,
    )

    assert result["selected_node_ids"] == ["0x2"]
    assert bridge.payload is not None
    assert json.loads(bridge.payload)[0]["node_id"] == "0x2"


def test_memory_record_reads_nudge_provenance_without_changing_candidate_gate() -> None:
    record = MemoryRecord.from_mapping(
        {
            "memory_id": "0x1",
            "owner_id": "owner",
            "scope_kind": "USER_PRIVATE",
            "lifecycle": "CANDIDATE",
            "validation": "UNREVIEWED",
            "revision": 1,
            "observed_at_ms": 1_700_000_000_000,
            "content_hash": "aa",
            "content": "bounded local workflow",
            "source_session_id": "0x2",
            "nudge_id": "0x4d",
            "nudge_hash": "bb",
            "candidate_hash": "cc",
            "relevance_score": 0.91,
        }
    )

    assert record.lifecycle == "CANDIDATE"
    assert record.validation == "UNREVIEWED"
    assert record.source_session_id == "0x2"
    assert record.nudge_id == "0x4d"
    assert record.relevance_score == 0.91
