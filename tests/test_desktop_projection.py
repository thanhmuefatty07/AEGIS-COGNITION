import json
from dataclasses import replace

from core.python.aegis.conversations import (
    ConversationCheckpoint,
    ConversationExecution,
    ConversationPart,
    ConversationRecord,
    ConversationSnapshot,
    ConversationToolCall,
    ConversationTurn,
)
from core.python.aegis.desktop_projection import (
    _approval_argument_preview,
    build_conversation_inspection,
    build_subagent_graph,
)


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
        arguments_json=json.dumps({"path": "notes.txt", "target": "api_key=private-key", "token": "private-token"}),
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
    assert projection["approvals"][0]["argument_keys"] == ["path", "target", "token"]
    assert projection["approvals"][0]["argument_preview"] == [
        {"key": "path", "value": "notes.txt"},
        {"key": "target", "value": "REDACTED"},
        {"key": "token", "value": "REDACTED"},
    ]
    assert projection["approvals"][0]["can_resolve"] is False
    assert projection["approvals"][0]["can_reconcile"] is False
    assert projection["approvals"][0]["effect_class"] == "unknown"
    assert "private assistant content" not in encoded
    assert "private-token" not in encoded
    assert "private-key" not in encoded
    assert "private prompt" not in encoded
    assert "private part" not in encoded


def test_conversation_projection_exposes_inclusion_and_adapter_attempt_without_prompt_content():
    snapshot = _snapshot()
    inclusion = {
        "schema": "aegis-context-inclusion-manifest-v1",
        "status": "COMPOSED",
        "manifest_sha256": "b" * 64,
        "prompt_chars": 63380,
        "prompt_limit_chars": 65536,
        "prompt_hash": "prompt-final-hash",
        "items": [
            {
                "source_kind": "session",
                "content_hash": "source-hash",
                "status": "INCLUDED",
                "omitted_reason": None,
            },
            {
                "source_kind": "memory",
                "content_hash": "omitted-hash",
                "status": "OMITTED",
                "omitted_reason": "PROMPT_LIMIT",
            },
        ],
    }
    attempt = {
        "schema": "aegis-provider-request-attempt-v1",
        "attempt_id": "exec-1:provider:2",
        "ordinal": 1,
        "state": "DISPATCH_ATTEMPTED",
        "payload_sha256": "a" * 64,
        "payload_bytes": 64000,
    }
    checkpoints = (
        snapshot.checkpoints[0],
        ConversationCheckpoint(
            execution_id="exec-1",
            sequence=2,
            state="PROVIDER_DISPATCH_ATTEMPTED",
            continuation_json=json.dumps(
                {
                    "schema": "aegis-desktop-provider-continuation-v1",
                    "context_inclusion": inclusion,
                    "provider_request_attempt": attempt,
                }
            ),
            continuation_hash="continuation-2",
            created_at_ms=6,
        ),
        ConversationCheckpoint(
            execution_id="exec-1",
            sequence=3,
            state="TOOL_ROUND_COMPLETED",
            continuation_json=json.dumps({"completed_tool_rounds": 1}),
            continuation_hash="continuation-3",
            created_at_ms=7,
        ),
    )
    projected = build_conversation_inspection(replace(snapshot, checkpoints=checkpoints))
    encoded = json.dumps(projected, sort_keys=True)

    assert projected["context"]["inclusion"]["included_count"] == 1
    assert projected["context"]["inclusion"]["omitted_count"] == 1
    assert projected["context"]["inclusion"]["omitted_reasons"] == ["PROMPT_LIMIT"]
    assert projected["context"]["request_attempt"]["state"] == "DISPATCH_ATTEMPTED"
    assert projected["context"]["request_attempt"]["payload_sha256"] == "a" * 64
    assert "private assistant content" not in encoded
    assert "private prompt" not in encoded
    assert "private-token" not in encoded
    assert "source-hash" not in encoded
    assert "omitted-hash" not in encoded


def test_conversation_projection_does_not_reuse_context_from_a_prior_assistant_turn():
    snapshot = _snapshot()
    older_turn = replace(snapshot.turns[0], turn_id="turn-old", ordinal=2, execution_id="exec-a")
    user_turn = replace(snapshot.turns[0], turn_id="turn-user", ordinal=3, role="user", execution_id=None)
    latest_turn = replace(snapshot.turns[0], turn_id="turn-latest", ordinal=4, execution_id="exec-z")
    older_execution = replace(snapshot.executions[0], execution_id="exec-a", turn_id="turn-old", started_at_ms=20)
    latest_execution = replace(snapshot.executions[0], execution_id="exec-z", turn_id="turn-latest", started_at_ms=10)

    def checkpoint(
        execution_id: str,
        sequence: int,
        manifest: str,
        prompt: str,
        inclusion_hash: str,
        created_at_ms: int,
    ) -> ConversationCheckpoint:
        continuation = {
            "schema": "aegis-desktop-provider-continuation-v1",
            "context_manifest_hash": manifest,
            "prompt_hash": prompt,
            "context_inclusion": {
                "schema": "aegis-context-inclusion-manifest-v1",
                "status": "COMPOSED",
                "manifest_sha256": inclusion_hash,
                "prompt_chars": 100,
                "prompt_limit_chars": 65536,
                "items": [],
            },
        }
        return ConversationCheckpoint(
            execution_id=execution_id,
            sequence=sequence,
            state="PROMPT_PREPARED",
            continuation_json=json.dumps(continuation),
            continuation_hash=f"{execution_id}-{sequence}",
            created_at_ms=created_at_ms,
        )

    prior_context = checkpoint("exec-a", 1, "manifest-old", "prompt-old", "a" * 64, 5)
    latest_sparse_checkpoint = ConversationCheckpoint(
        execution_id="exec-z",
        sequence=1,
        state="LAB_STEP_COMPLETED",
        continuation_json=json.dumps({"step": "done"}),
        continuation_hash="exec-z-1",
        created_at_ms=30,
    )
    projected = build_conversation_inspection(
        replace(
            snapshot,
            turns=(older_turn, user_turn, latest_turn),
            executions=(older_execution, latest_execution),
            checkpoints=(latest_sparse_checkpoint, prior_context),
        )
    )

    assert projected["context"]["status"] == "NOT_CAPTURED"
    assert projected["context"]["context_manifest_hash"] is None
    assert projected["context"]["prompt_hash"] is None
    assert projected["context"]["inclusion"] is None


def test_conversation_projection_prepared_state_is_not_misreported_as_sent():
    snapshot = _snapshot()
    checkpoint = ConversationCheckpoint(
        execution_id="exec-1",
        sequence=1,
        state="PROMPT_PREPARED",
        continuation_json=json.dumps(
            {
                "schema": "aegis-desktop-provider-continuation-v1",
                "context_inclusion": {
                    "schema": "aegis-context-inclusion-manifest-v1",
                    "status": "COMPOSED",
                    "manifest_sha256": "c" * 64,
                    "prompt_chars": 120,
                    "prompt_limit_chars": 65536,
                    "prompt_hash": "prepared-prompt-hash",
                    "items": [],
                },
            }
        ),
        continuation_hash="prepared-checkpoint",
        created_at_ms=5,
    )
    projected = build_conversation_inspection(replace(snapshot, checkpoints=(checkpoint,)))

    assert projected["context"]["request_attempt"] is None
    assert projected["context"]["inclusion"]["status"] == "COMPOSED"


def test_approval_preview_shows_only_safe_code_copy_scope():
    preview = _approval_argument_preview(
        {
            "source_path": "src\\worker.py",
            "start_line": 4,
            "end_line": 9,
            "target_relative_path": "src\\worker_copy.py",
            "license_id": "MIT",
            "api_key": "private-token",
        }
    )
    assert preview == [
        {"key": "api_key", "value": "REDACTED"},
        {"key": "end_line", "value": "9"},
        {"key": "license_id", "value": "MIT (declared; unverified)"},
        {"key": "source_path", "value": "src/worker.py"},
        {"key": "start_line", "value": "4"},
        {"key": "target_relative_path", "value": "src/worker_copy.py"},
    ]
    assert _approval_argument_preview({"source_path": "C:\\private\\source.py"}) == [
        {"key": "source_path", "value": "REDACTED"}
    ]


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
