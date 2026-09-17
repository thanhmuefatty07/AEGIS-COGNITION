from types import SimpleNamespace
from unittest.mock import MagicMock

from core.python.aegis.conversations import ConversationManager, ConversationSnapshot


def test_conversation_manager_preserves_canonical_revision_contract():
    bridge = MagicMock()
    bridge.aegis_create_conversation.return_value = (
        '{"record": {"conversation_id": "conv-1", "owner_id": "local-user", '
        '"title": "Test", "connection_id": "local", "model_id": "model-a", '
        '"status": "ACTIVE", "revision": 1, "created_at_ms": 1700000000000, '
        '"updated_at_ms": 1700000000000}}'
    )
    bridge.aegis_append_conversation_turn.return_value = (
        '{"record": {"conversation_id": "conv-1", "turn_id": "turn-1", '
        '"ordinal": 1, "role": "user", "status": "COMPLETED", "content": "hello", '
        '"connection_id": "local", "model_id": "model-a", "revision": 2, '
        '"created_at_ms": 1700000000001, "finished_at_ms": null, "execution_id": null}}'
    )
    bridge.aegis_switch_conversation_model.return_value = (
        '{"record": {"conversation_id": "conv-1", "owner_id": "local-user", '
        '"title": "Test", "connection_id": "remote", "model_id": "model-b", '
        '"status": "ACTIVE", "revision": 3, "created_at_ms": 1700000000000, '
        '"updated_at_ms": 1700000000002}}'
    )
    bridge.aegis_read_conversation.return_value = (
        '{"snapshot": {"conversation": {"conversation_id": "conv-1", '
        '"owner_id": "local-user", "title": "Test", "connection_id": "remote", '
        '"model_id": "model-b", "status": "ACTIVE", "revision": 3, '
        '"created_at_ms": 1700000000000, "updated_at_ms": 1700000000002}, '
        '"turns": [{"conversation_id": "conv-1", "turn_id": "turn-1", '
        '"ordinal": 1, "role": "user", "status": "COMPLETED", "content": "hello", '
        '"connection_id": "local", "model_id": "model-a", "revision": 2, '
        '"created_at_ms": 1700000000001, "finished_at_ms": null, "execution_id": null}]}}'
    )
    manager = ConversationManager(bridge)

    conversation = manager.create(
        "conv-1",
        owner_id="local-user",
        title="Test",
        connection_id="local",
        model_id="model-a",
        timestamp=1_700_000_000_000,
    )
    turn = manager.append_turn(
        "conv-1",
        owner_id="local-user",
        turn_id="turn-1",
        role="user",
        content="hello",
        connection_id="local",
        model_id="model-a",
        expected_revision=1,
        timestamp=1_700_000_000_001,
    )
    switched = manager.switch_model(
        "conv-1",
        owner_id="local-user",
        connection_id="remote",
        model_id="model-b",
        expected_revision=2,
        timestamp=1_700_000_000_002,
    )
    snapshot = manager.read("conv-1", owner_id="local-user")

    assert conversation.revision == 1
    assert turn.revision == 2
    assert switched.revision == 3
    assert snapshot.turns[0].content == "hello"
    assert snapshot.parts == ()
    bridge.aegis_append_conversation_turn.assert_called_once_with(
        "conv-1",
        "local-user",
        "turn-1",
        "user",
        "hello",
        "local",
        "model-a",
        "COMPLETED",
        1,
        1_700_000_000_001,
    )


def test_conversation_manager_rejects_empty_identity_before_native_call():
    bridge = MagicMock()
    manager = ConversationManager(bridge)
    try:
        manager.create(
            "",
            owner_id="local-user",
            title="Test",
            connection_id="local",
            model_id="model-a",
        )
    except ValueError as error:
        assert "conversation_id" in str(error)
    else:
        raise AssertionError("empty conversation id must be rejected")
    bridge.aegis_create_conversation.assert_not_called()


def test_conversation_snapshot_parses_typed_parts_and_keeps_legacy_defaults():
    snapshot = ConversationSnapshot.from_mapping(
        {
            "conversation": {
                "conversation_id": "conv-1",
                "owner_id": "local-user",
                "title": "Test",
                "connection_id": "local",
                "model_id": "model-a",
                "status": "ACTIVE",
                "revision": 2,
                "created_at_ms": 1,
                "updated_at_ms": 2,
            },
            "turns": [],
            "parts": [
                {
                    "conversation_id": "conv-1",
                    "turn_id": "assistant-1",
                    "part_index": 0,
                    "kind": "TEXT",
                    "content": "hello",
                }
            ],
        }
    )

    assert snapshot.parts[0].turn_id == "assistant-1"
    assert snapshot.parts[0].kind == "TEXT"
    assert snapshot.parts[0].content == "hello"


def test_conversation_manager_exposes_streaming_execution_and_tool_pairing():
    bridge = MagicMock()
    bridge.aegis_append_conversation_part.return_value = (
        '{"record": {"conversation_id": "conv-1", "turn_id": "turn-1", '
        '"ordinal": 1, "role": "assistant", "status": "RUNNING", '
        '"content": "hello world", "connection_id": "local", '
        '"model_id": "model-a", "revision": 3, "created_at_ms": 3, '
        '"finished_at_ms": null, "execution_id": null}}'
    )
    bridge.aegis_transition_conversation_turn.return_value = (
        '{"record": {"conversation_id": "conv-1", "turn_id": "turn-1", '
        '"ordinal": 1, "role": "assistant", "status": "RUNNING", '
        '"content": "hello world", "connection_id": "local", '
        '"model_id": "model-a", "revision": 4, "created_at_ms": 2, '
        '"finished_at_ms": null, "execution_id": null}}'
    )
    execution_json = (
        '{"record": {"execution_id": "exec-1", "conversation_id": "conv-1", '
        '"turn_id": "turn-1", "provider_kind": "local", '
        '"connection_id": "local", "model_id": "model-a", '
        '"status": "RUNNING", "checkpoint_seq": 1, "revision": 5, '
        '"started_at_ms": 5, "finished_at_ms": null}}'
    )
    bridge.aegis_start_conversation_execution.return_value = execution_json
    bridge.aegis_checkpoint_conversation_execution.return_value = execution_json.replace(
        '"status": "RUNNING"', '"status": "RUNNING"'
    )
    bridge.aegis_finish_conversation_execution.return_value = execution_json.replace(
        '"status": "RUNNING", "checkpoint_seq": 1, "revision": 5, '
        '"started_at_ms": 5, "finished_at_ms": null',
        '"status": "COMPLETED", "checkpoint_seq": 1, "revision": 7, '
        '"started_at_ms": 5, "finished_at_ms": 7',
    )
    call_json = (
        '{"record": {"call_id": "call-1", "conversation_id": "conv-1", '
        '"request_turn_id": "turn-1", "result_turn_id": null, '
        '"tool_name": "search", "arguments_json": "{\\"q\\":\\"rust\\"}", '
        '"result_content": null, "status": "REQUESTED", "revision": 6, '
        '"created_at_ms": 6, "completed_at_ms": null}}'
    )
    bridge.aegis_record_conversation_tool_call.return_value = call_json
    bridge.aegis_record_conversation_tool_result.return_value = call_json.replace(
        '"result_turn_id": null, ',
        '"result_turn_id": "tool-1", ',
    ).replace(
        '"result_content": null, "status": "REQUESTED", "revision": 6, '
        '"created_at_ms": 6, "completed_at_ms": null',
        '"result_content": "ok", "status": "COMPLETED", "revision": 8, '
        '"created_at_ms": 6, "completed_at_ms": 8',
    )
    manager = ConversationManager(bridge)

    part = manager.append_part(
        "conv-1",
        owner_id="local-user",
        turn_id="turn-1",
        kind="TEXT",
        content=" world",
        expected_revision=2,
        timestamp=3,
    )
    transitioned = manager.transition_turn(
        "conv-1",
        owner_id="local-user",
        turn_id="turn-1",
        status="RUNNING",
        expected_revision=3,
        timestamp=4,
    )
    execution = manager.start_execution(
        "conv-1",
        owner_id="local-user",
        execution_id="exec-1",
        turn_id="turn-1",
        provider_kind="local",
        connection_id="local",
        model_id="model-a",
        expected_revision=4,
        timestamp=5,
    )
    checkpoint = manager.checkpoint_execution(
        "conv-1",
        owner_id="local-user",
        execution_id="exec-1",
        sequence=1,
        state="streaming",
        continuation_json="{}",
        expected_revision=5,
        timestamp=6,
    )
    finished = manager.finish_execution(
        "conv-1",
        owner_id="local-user",
        execution_id="exec-1",
        status="COMPLETED",
        expected_revision=6,
        timestamp=7,
    )
    call = manager.record_tool_call(
        "conv-1",
        owner_id="local-user",
        call_id="call-1",
        request_turn_id="turn-1",
        tool_name="search",
        arguments_json='{"q":"rust"}',
        expected_revision=7,
        timestamp=6,
    )
    settled = manager.record_tool_result(
        "conv-1",
        owner_id="local-user",
        call_id="call-1",
        result_turn_id="tool-1",
        result_content="ok",
        status="COMPLETED",
        expected_revision=8,
        timestamp=8,
    )

    assert part.content == "hello world"
    assert transitioned.status == "RUNNING"
    assert execution.execution_id == checkpoint.execution_id == "exec-1"
    assert finished.status == "COMPLETED"
    assert call.status == "REQUESTED"
    assert settled.result_turn_id == "tool-1"
    bridge.aegis_checkpoint_conversation_execution.assert_called_once()
    bridge.aegis_record_conversation_tool_result.assert_called_once()


def test_conversation_manager_reconciles_ambiguous_tool_call():
    bridge = MagicMock()
    bridge.aegis_reconcile_conversation_tool_call.return_value = (
        '{"record": {"call_id": "call-1", "conversation_id": "conv-1", '
        '"request_turn_id": "turn-1", "result_turn_id": null, '
        '"tool_name": "search", "arguments_json": "{}", '
        '"result_content": "confirmed not applied", "status": "CANCELLED", '
        '"revision": 9, "created_at_ms": 6, "completed_at_ms": 9}}'
    )
    call = ConversationManager(bridge).reconcile_tool_call(
        "conv-1",
        owner_id="local-user",
        call_id="call-1",
        status="CANCELLED",
        note="confirmed not applied",
        expected_revision=8,
        timestamp=9,
    )
    assert call.status == "CANCELLED"
    bridge.aegis_reconcile_conversation_tool_call.assert_called_once_with(
        "conv-1",
        "local-user",
        "call-1",
        "CANCELLED",
        "confirmed not applied",
        8,
        9,
    )


def test_agent_application_opt_in_records_a_canonical_execution(monkeypatch):
    import aegis_cognition.application as application_module

    class FakeManager:
        def __init__(self):
            self.calls = []

        def read(self, *_args, **_kwargs):
            raise ValueError("conversation not found")

        def create(self, conversation_id, **_kwargs):
            self.calls.append(("create", conversation_id))
            return SimpleNamespace(revision=1)

        def append_turn(self, conversation_id, **kwargs):
            self.calls.append(("append_turn", conversation_id, kwargs))
            revision = 2 if len([item for item in self.calls if item[0] == "append_turn"]) == 1 else 3
            return SimpleNamespace(revision=revision, turn_id=kwargs["turn_id"])

        def start_execution(self, conversation_id, **kwargs):
            self.calls.append(("start_execution", conversation_id, kwargs))
            return SimpleNamespace(revision=4, execution_id=kwargs["execution_id"])

        def append_part(self, conversation_id, **kwargs):
            self.calls.append(("append_part", conversation_id, kwargs))
            return SimpleNamespace(revision=5)

        def finish_execution(self, conversation_id, **kwargs):
            self.calls.append(("finish_execution", conversation_id, kwargs))
            return SimpleNamespace(revision=6)

    monkeypatch.setattr(application_module, "ConversationManager", FakeManager)
    config = SimpleNamespace(
        options={
            "conversation_id": "conv-1",
            "conversation_connection_id": "local",
            "conversation_model_id": "model-a",
            "conversation_provider_kind": "local",
        }
    )
    app = application_module.AgentApplication(config)
    run = app._begin_conversation("hello")
    app._finish_conversation(run, output="world", status="COMPLETED")

    assert [item[0] for item in run["manager"].calls] == [
        "create",
        "append_turn",
        "append_turn",
        "start_execution",
        "append_part",
        "finish_execution",
    ]
    assert run["manager"].calls[4][2]["kind"] == "TEXT"
    assert run["manager"].calls[5][2]["status"] == "COMPLETED"
