import json

from core.python.aegis.conversations import (
    ConversationCheckpoint,
    ConversationExecution,
    ConversationPart,
    ConversationRecord,
    ConversationSnapshot,
    ConversationToolCall,
    ConversationTurn,
)
from core.python.aegis.desktop_projection import build_conversation_inspection, build_subagent_graph


def _snapshot() -> ConversationSnapshot:
    conversation = ConversationRecord(
        conversation_id="conv-1",
        owner_id="owner-1",
        title="Projection",
        connection_id="local",
        model_id="model-1",
        status="ACTIVE",
        revision=9,
        created_at_ms=1,
        updated_at_ms=9,
    )
    turn = ConversationTurn(
        conversation_id="conv-1",
        turn_id="turn-1",
        ordinal=1,
        role="assistant",
        status="WAITING_APPROVAL",
        content="private assistant content",
        connection_id="local",
        model_id="model-1",
        revision=3,
        created_at_ms=3,
        finished_at_ms=None,
    )
    execution = ConversationExecution(
        execution_id="exec-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        provider_kind="local",
        connection_id="local",
        model_id="model-1",
        status="RUNNING",
        checkpoint_seq=1,
        revision=4,
        started_at_ms=4,
        finished_at_ms=None,
    )
    checkpoint = ConversationCheckpoint(
        execution_id="exec-1",
        sequence=1,
        state="REQUEST_STARTED",
        continuation_json=json.dumps(
            {
                "source_revision": "source-1",
                "context_manifest_hash": "manifest-1",
                "prompt_hash": "prompt-1",
                "context_token_budget": 4096,
                "context_token_count": 320,
                "context_item_count": 2,
                "context_selection_backend": "rust-context-governor-v1",
                "private_prompt": "must never cross the observer boundary",
            }
        ),
        continuation_hash="continuation-1",
        created_at_ms=5,
    )
    call = ConversationToolCall(
        call_id="call-1",
        conversation_id="conv-1",
        request_turn_id="turn-1",
        result_turn_id=None,
        tool_name="filesystem.write",
        arguments_json=json.dumps({"path": "secret.txt", "token": "private-token"}),
        result_content="private tool result",
        status="REQUESTED",
        revision=8,
        created_at_ms=8,
        completed_at_ms=None,
    )
    return ConversationSnapshot(
        conversation=conversation,
        turns=(turn,),
        executions=(execution,),
        checkpoints=(checkpoint,),
        tool_calls=(call,),
        parts=(ConversationPart("conv-1", "turn-1", 0, "TEXT", "private part"),),
    )


def test_conversation_projection_is_bounded_and_redacts_payloads():
    projection = build_conversation_inspection(_snapshot(), source_revision="source-2")
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["schema"] == "aegis-desktop-conversation-inspection-v1"
    assert projection["context"]["source_revision"] == "source-1"
    assert projection["context"]["item_count"] == 2
    assert projection["approvals"][0]["argument_keys"] == ["path", "token"]
    assert "private assistant content" not in encoded
    assert "private-token" not in encoded
    assert "private prompt" not in encoded
    assert "private part" not in encoded


def test_subagent_graph_preserves_edges_and_redacts_prompt_bodies():
    graph = build_subagent_graph(
        "run-1",
        [
            {
                "message_kind": "TASK_REQUEST",
                "task_id": 1,
                "parent_task_id": None,
                "payload": {
                    "role": "research",
                    "dependency_ids": [],
                    "capabilities": ["network_read"],
                    "side_effect_class": "NetworkRead",
                    "prompt": "private prompt must not be forwarded",
                },
            },
            {
                "message_kind": "TASK_REQUEST",
                "task_id": 2,
                "parent_task_id": 1,
                "payload": {
                    "role": "verification",
                    "dependency_ids": [1],
                    "capabilities": ["filesystem_read"],
                    "side_effect_class": "ReadOnly",
                },
            },
            {
                "message_kind": "TASK_RESULT",
                "task_id": 1,
                "parent_task_id": None,
                "payload": {"status": "SUCCEEDED", "summary": "bounded evidence"},
            },
        ],
    )
    encoded = json.dumps(graph, sort_keys=True)

    assert graph["schema"] == "aegis-desktop-subagent-graph-v1"
    assert graph["nodes"][0]["status"] == "SUCCEEDED"
    assert graph["edges"] == [{"from": 1, "to": 2}]
    assert "private prompt" not in encoded
    assert all(node["redacted"] is True for node in graph["nodes"])
