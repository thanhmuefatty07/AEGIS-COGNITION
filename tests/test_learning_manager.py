from __future__ import annotations

import json
from typing import Any

import pytest

from core.python.aegis.learning import LearningManager, MemoryFileRelationCursor, MemoryRecord


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
                "utility_score": 1_000_000,
                "activation_node_count": 1,
                "digest": "a" * 64,
                "backend": "rust-context-governor-v1",
            }
        )


class _BoundedSelectionBridge:
    def __init__(self) -> None:
        self.payload: str | None = None
        self.mandatory: str | None = None

    def aegis_select_context_items(self, payload: str, token_budget: int, mandatory: str) -> str:
        del token_budget
        self.payload = payload
        self.mandatory = mandatory
        return json.dumps(
            {
                "schema": "aegis-context-selection-v1",
                "selected_node_ids": ["0x1"],
                "token_count": 1,
                "utility_score": 500_000,
                "activation_node_count": 1,
                "digest": "a" * 64,
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


@pytest.mark.parametrize("count", [99, 100])
def test_context_selection_forwards_inputs_at_the_rust_item_limit(count: int) -> None:
    bridge = _BoundedSelectionBridge()
    items = [{"node_id": index + 1, "token_cost": 1, "score": 0.5} for index in range(count)]

    LearningManager(bridge).select_context_items(items, token_budget=count)

    assert bridge.payload is not None
    assert len(json.loads(bridge.payload)) == count


@pytest.mark.parametrize(
    ("items", "message"),
    [([], "between 1 and 100"), ([{"node_id": i + 1, "token_cost": 1} for i in range(101)], "between 1 and 100")],
)
def test_context_selection_rejects_item_count_before_native_call(items, message: str) -> None:
    bridge = _BoundedSelectionBridge()

    with pytest.raises(ValueError, match=message):
        LearningManager(bridge).select_context_items(items, token_budget=1024)

    assert bridge.payload is None


@pytest.mark.parametrize("oversize_input", ["items", "mandatory"])
def test_context_selection_rejects_json_over_two_mib_before_native_call(oversize_input: str) -> None:
    bridge = _BoundedSelectionBridge()
    huge_node_id = 1 << (4 * (2 * 1024 * 1024))
    items = [{"node_id": 1, "token_cost": 1}]
    mandatory_ids = ()
    if oversize_input == "items":
        items[0]["node_id"] = huge_node_id
    else:
        mandatory_ids = (huge_node_id,)

    with pytest.raises(ValueError, match="bounded size"):
        LearningManager(bridge).select_context_items(
            items,
            token_budget=1024,
            mandatory_session_ids=mandatory_ids,
        )

    assert bridge.payload is None


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


def test_file_relation_bridge_preserves_provenance_and_scoped_owner() -> None:
    relation = {
        "relation_id": "0x7b",
        "memory_id": "0x2a",
        "scope_kind": "USER_PRIVATE",
        "project_id": "project:project-a",
        "relative_path": "src/agent.py",
        "source_revision": "revision-a",
        "source_content_hash": "a" * 64,
        "memory_revision": 4,
        "memory_content_hash": "b" * 64,
        "created_at_ms": 10,
        "memory_lifecycle": "ACTIVE",
        "memory_validation": "ACCEPTED",
        "memory_content_current": True,
    }

    class RelationBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def aegis_link_memory_file_relation(self, *args: Any) -> str:
            self.calls.append(("link", args))
            return json.dumps({"relation": relation})

        def aegis_list_memory_file_relations(
            self,
            project_id: str,
            relative_path: str,
            scope_kind: str | None = None,
            owner_id: str | None = None,
            subject_id: str | None = None,
        ) -> str:
            self.calls.append(("list", (project_id, relative_path, scope_kind, owner_id, subject_id)))
            second_relation = {**relation, "memory_id": "0x2b"}
            return json.dumps({"relations": [relation, second_relation]})

        def aegis_unlink_memory_file_relation(self, *args: Any) -> str:
            self.calls.append(("unlink", args))
            return json.dumps({"removed": True})

    bridge = RelationBridge()
    manager = LearningManager(bridge)
    linked = manager.link_file_relation(
        123,
        "0x2a",
        project_id="project:project-a",
        relative_path="src/agent.py",
        source_revision="revision-a",
        source_content_hash="a" * 64,
        memory_revision=4,
        memory_content_hash="b" * 64,
        owner_id="local-profile",
    )
    listed = manager.list_file_relations(
        "project:project-a",
        "src/agent.py",
        owner_id="local-profile",
        limit=1,
    )
    removed = manager.unlink_file_relation(
        "0x7b",
        project_id="project:project-a",
        relative_path="src/agent.py",
        owner_id="local-profile",
    )

    assert linked == listed[0]
    assert len(listed) == 1
    assert linked.memory_content_current is True
    assert linked.project_id == "project:project-a"
    assert removed is True
    assert [kind for kind, _args in bridge.calls] == ["link", "list", "unlink"]
    assert bridge.calls[0][1][0:2] == (123, 42)
    assert bridge.calls[0][1][2:5] == ("project:project-a", "src/agent.py", "revision-a")
    assert bridge.calls[1][1][0:2] == ("project:project-a", "src/agent.py")
    assert bridge.calls[1][1] == (
        "project:project-a",
        "src/agent.py",
        "USER_PRIVATE",
        "local-profile",
        None,
    )
    assert bridge.calls[2][1][0:3] == (123, "project:project-a", "src/agent.py")


def test_file_relation_history_page_forwards_limit_and_keyset_cursor_to_native() -> None:
    relation = {
        "relation_id": "0x7d",
        "memory_id": "0x2a",
        "scope_kind": "USER_PRIVATE",
        "project_id": "project:project-a",
        "relative_path": "src/agent.py",
        "source_revision": "revision-a",
        "source_content_hash": "a" * 64,
        "memory_revision": 4,
        "memory_content_hash": "b" * 64,
        "created_at_ms": 11,
        "memory_lifecycle": "ACTIVE",
        "memory_validation": "ACCEPTED",
        "memory_content_current": True,
    }

    class RelationPageBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def aegis_list_memory_file_relations_page(self, *args: Any) -> str:
            self.calls.append(args)
            return json.dumps(
                {
                    "schema": "aegis-memory-file-relation-page-v1",
                    "relations": [relation],
                    "next_cursor": {"created_at_ms": 11, "relation_id": "0x7d"},
                }
            )

    bridge = RelationPageBridge()
    manager = LearningManager(bridge)
    page = manager.list_file_relations_page(
        "project:project-a",
        "src/agent.py",
        limit=2,
        cursor=MemoryFileRelationCursor(created_at_ms=10, relation_id="0x7c"),
        owner_id="local-profile",
    )

    assert len(page.relations) == 1
    assert page.relations[0].relation_id == "0x7d"
    assert page.next_cursor == MemoryFileRelationCursor(created_at_ms=11, relation_id="0x7d")
    assert bridge.calls == [
        (
            "project:project-a",
            "src/agent.py",
            2,
            10,
            124,
            "USER_PRIVATE",
            "local-profile",
            None,
        )
    ]

    with pytest.raises(ValueError, match="limit"):
        manager.list_file_relations_page("project:project-a", "src/agent.py", limit=101)


def test_current_file_relation_bridge_bounds_native_results_by_source_and_limit() -> None:
    relation = {
        "relation_id": "0x7b",
        "memory_id": "0x2a",
        "scope_kind": "USER_PRIVATE",
        "project_id": "project:project-a",
        "relative_path": "src/agent.py",
        "source_revision": "revision-a",
        "source_content_hash": "a" * 64,
        "memory_revision": 4,
        "memory_content_hash": "b" * 64,
        "created_at_ms": 10,
        "memory_lifecycle": "ACTIVE",
        "memory_validation": "ACCEPTED",
        "memory_content_current": True,
    }

    class RelationBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def aegis_list_current_memory_file_relations(self, *args: Any) -> str:
            self.calls.append(args)
            return json.dumps({"relations": [relation]})

    bridge = RelationBridge()
    manager = LearningManager(bridge)
    listed = manager.list_current_file_relations(
        "project:project-a",
        "src/agent.py",
        source_content_hash="a" * 64,
        limit=2,
        owner_id="local-profile",
    )

    assert len(listed) == 1
    assert listed[0].memory_id == "0x2a"
    assert bridge.calls == [
        (
            "project:project-a",
            "src/agent.py",
            "a" * 64,
            2,
            "USER_PRIVATE",
            "local-profile",
            None,
        )
    ]

    with pytest.raises(ValueError, match="limit"):
        manager.list_current_file_relations(
            "project:project-a",
            "src/agent.py",
            source_content_hash="a" * 64,
            limit=9,
            owner_id="local-profile",
        )
    with pytest.raises(ValueError, match="64-character hexadecimal digest"):
        manager.list_current_file_relations(
            "project:project-a",
            "src/agent.py",
            source_content_hash="not-a-digest",
            limit=2,
            owner_id="local-profile",
        )
    assert len(bridge.calls) == 1


def test_current_file_relation_bridge_rejects_native_limit_violations() -> None:
    relation = {
        "relation_id": "0x7b",
        "memory_id": "0x2a",
        "scope_kind": "USER_PRIVATE",
        "project_id": "project:project-a",
        "relative_path": "src/agent.py",
        "source_revision": "revision-a",
        "source_content_hash": "a" * 64,
        "memory_revision": 4,
        "memory_content_hash": "b" * 64,
        "created_at_ms": 10,
        "memory_lifecycle": "ACTIVE",
        "memory_validation": "ACCEPTED",
        "memory_content_current": True,
    }

    class RelationBridge:
        def aegis_list_current_memory_file_relations(self, *_args: Any) -> str:
            return json.dumps({"relations": [relation, relation, relation]})

    manager = LearningManager(RelationBridge())
    with pytest.raises(ValueError, match="exceeded its limit"):
        manager.list_current_file_relations(
            "project:project-a",
            "src/agent.py",
            source_content_hash="a" * 64,
            limit=2,
            owner_id="local-profile",
        )


def test_file_relation_bridge_rejects_unsafe_source_identity_before_native_call() -> None:
    class RelationBridge:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"native call must not occur: {name}")

    manager = LearningManager(RelationBridge())

    with pytest.raises(ValueError, match="relative_path"):
        manager.list_file_relations("project:project-a", "../secrets.txt", owner_id="local-profile")

    with pytest.raises(ValueError, match="relation_id"):
        manager.unlink_file_relation(
            True,
            project_id="project:project-a",
            relative_path="src/agent.py",
            owner_id="local-profile",
        )


def test_context_selector_v2_round_trips_typed_file_memory_graph_without_owner_data() -> None:
    class RelationSelectorBridge:
        def __init__(self) -> None:
            self.call: tuple[str, str, int, str] | None = None

        def aegis_select_context_items_v2(
            self,
            items_json: str,
            relation_graph_json: str,
            token_budget: int,
            mandatory_ids_json: str,
        ) -> str:
            self.call = (items_json, relation_graph_json, token_budget, mandatory_ids_json)
            return json.dumps(
                {
                    "schema": "aegis-context-selection-v2",
                    "selected_node_ids": ["0x2"],
                    "related_memory_node_ids": ["0x2"],
                    "selected_relation_ids": ["0x91"],
                    "token_count": 5,
                    "utility_score": 1_000_000,
                    "activation_node_count": 1,
                    "digest": "d" * 64,
                    "backend": "rust-context-governor-v2",
                }
            )

    bridge = RelationSelectorBridge()
    manager = LearningManager(bridge)
    graph = {
        "schema": "aegis-context-relation-graph-v1",
        "active_file_node_ids": ["0xf00"],
        "relations": [
            {
                "relation_id": "0x91",
                "kind": "confirmed_file_memory",
                "file_node_id": "0xf00",
                "memory_node_id": "0x2",
            }
        ],
    }

    result = manager.select_context_items_v2(
        [
            {
                "session_id": 2,
                "node_id": 2,
                "kind": "memory",
                "token_cost": 5,
                "score": 0.5,
            }
        ],
        graph,
        5,
    )

    assert bridge.call is not None
    items_json, relation_graph_json, token_budget, mandatory_ids_json = bridge.call
    assert json.loads(items_json) == [
        {
            "node_id": "0x2",
            "kind": "memory",
            "token_cost": 5,
            "utility_score": 500_000,
            "contradiction_risk": 0,
        }
    ]
    assert json.loads(relation_graph_json) == graph
    assert token_budget == 5
    assert json.loads(mandatory_ids_json) == []
    assert result["schema"] == "aegis-context-selection-v2"
    assert result["selected_relation_ids"] == ["0x91"]


def test_context_selector_v2_keeps_legacy_native_extension_on_v1() -> None:
    class LegacySelectorBridge:
        def aegis_select_context_items(
            self,
            items_json: str,
            token_budget: int,
            mandatory_ids_json: str,
        ) -> str:
            assert json.loads(items_json)[0]["dependency_coverage"] == 1_000_000
            assert token_budget == 5
            assert json.loads(mandatory_ids_json) == []
            return json.dumps({"schema": "aegis-context-selection-v1", "selected_node_ids": ["0x2"]})

    manager = LearningManager(LegacySelectorBridge())
    result = manager.select_context_items_v2(
        [
            {
                "session_id": 2,
                "node_id": 2,
                "kind": "memory",
                "token_cost": 5,
                "score": 0.5,
                "dependency_coverage": 1_000_000,
            }
        ],
        {
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": ["0xf00"],
            "relations": [
                {
                    "relation_id": "0x91",
                    "kind": "confirmed_file_memory",
                    "file_node_id": "0xf00",
                    "memory_node_id": "0x2",
                }
            ],
        },
        5,
    )

    assert result["schema"] == "aegis-context-selection-v1"


def test_select_adaptive_user_memories_ranks_validated_preferences_and_excludes_inactive() -> None:
    base_item = {
        "owner_id": "local-profile",
        "scope_kind": "USER_PRIVATE",
        "revision": 1,
        "observed_at_ms": 1_700_000_000_000,
        "content_hash": "a" * 64,
        "source_session_id": "0x2",
    }

    class AdaptiveListBridge:
        def aegis_list_memories(self, scope_kind: str, owner_id: str, subject_id: Any, lifecycle: str | None) -> str:
            assert scope_kind == "USER_PRIVATE"
            assert owner_id == "local-profile"
            assert subject_id is None
            assert lifecycle == "ACTIVE"
            return json.dumps(
                {
                    "memories": [
                        {
                            **base_item,
                            "memory_id": "0x1",
                            "category": "SEMANTIC_FACT",
                            "lifecycle": "ACTIVE",
                            "validation": "UNREVIEWED",
                            "relevance_score": 0.95,
                            "content": "uses dark mode",
                        },
                        {
                            **base_item,
                            "memory_id": "0x2",
                            "category": "USER_PREFERENCE",
                            "lifecycle": "ACTIVE",
                            "validation": "ACCEPTED",
                            "relevance_score": 0.80,
                            "content": "prefers concise verification summaries",
                        },
                        {
                            **base_item,
                            "memory_id": "0x3",
                            "category": "USER_WORKFLOW",
                            "lifecycle": "ACTIVE",
                            "validation": "REJECTED",
                            "relevance_score": 0.99,
                            "content": "rejected workflow entry",
                        },
                        {
                            **base_item,
                            "memory_id": "0x4",
                            "category": "PROCEDURAL_SKILL",
                            "lifecycle": "ACTIVE",
                            "validation": "ACCEPTED",
                            "relevance_score": 0.90,
                            "content": "runs pytest before rust gates",
                        },
                    ]
                }
            )

    manager = LearningManager(AdaptiveListBridge())
    selected = manager.select_adaptive_user_memories(owner_id="local-profile", limit=5)

    assert [record.memory_id for record in selected] == ["0x2", "0x4", "0x1"]


def test_learning_manager_select_adaptive_user_memories_hydrates_candidate_only_search() -> None:
    base_item = {
        "owner_id": "local-profile",
        "scope_kind": "USER_PRIVATE",
        "revision": 1,
        "observed_at_ms": 1_700_000_000_000,
        "content_hash": "b" * 64,
        "source_session_id": "0x2",
    }
    inspected_ids: list[int] = []

    class CandidateOnlySearchBridge:
        def aegis_search_memories(
            self,
            query: str,
            top_k: int,
            scope_kind: str,
            owner_id: str | None,
        ) -> str:
            assert scope_kind == "USER_PRIVATE"
            assert owner_id == "local-profile"
            # CandidateOnly search returns content: None, matching Rust MemoryRepository::search_candidates_as
            return json.dumps(
                {
                    "schema": "aegis.learning.memory.search.v1",
                    "gate": "CandidateOnly",
                    "results": [
                        {
                            **base_item,
                            "memory_id": "0x10",
                            "memory_kind": "SEMANTIC_FACT",
                            "lifecycle": "ACTIVE",
                            "validation": "UNREVIEWED",
                            "relevance_score": 0.99,
                            "content": None,
                        },
                        {
                            **base_item,
                            "memory_id": "0x20",
                            "memory_kind": "USER_PREFERENCE",
                            "lifecycle": "ACTIVE",
                            "validation": "ACCEPTED",
                            "relevance_score": 0.85,
                            "content": None,
                        },
                        {
                            **base_item,
                            "memory_id": "0x30",
                            "memory_kind": "USER_WORKFLOW",
                            "lifecycle": "CANDIDATE",
                            "validation": "UNREVIEWED",
                            "relevance_score": 0.95,
                            "content": None,
                        },
                    ],
                }
            )

        def aegis_inspect_memory(
            self,
            memory_id: int,
            scope_kind: str,
            owner_id: str | None,
        ) -> str:
            inspected_ids.append(memory_id)
            contents = {
                0x10: ("SEMANTIC_FACT", "ACCEPTED", "dark terminal theme"),
                0x20: ("USER_PREFERENCE", "ACCEPTED", "prefers deterministic pytest runs"),
            }
            kind, validation, content = contents[memory_id]
            return json.dumps(
                {
                    "schema": "aegis.learning.memory.inspect.v1",
                    "found": True,
                    "record": {
                        **base_item,
                        "memory_id": hex(memory_id),
                        "memory_kind": kind,
                        "lifecycle": "ACTIVE",
                        "validation": validation,
                        "content": content,
                    },
                }
            )

    manager = LearningManager(CandidateOnlySearchBridge())
    selected = manager.select_adaptive_user_memories(
        query="preferences",
        owner_id="local-profile",
        top_k=1,
    )
    assert [record.memory_id for record in selected] == ["0x20"]
    assert selected[0].content == "prefers deterministic pytest runs"
    assert selected[0].relevance_score == 0.85
    # Only the top-ranked ACTIVE candidate (0x20) needed hydration to satisfy top_k=1
    assert inspected_ids == [0x20]


