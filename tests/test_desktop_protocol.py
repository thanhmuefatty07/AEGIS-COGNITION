import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
import subprocess
import tomllib
import zipfile
from collections.abc import Mapping
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from types import SimpleNamespace
import time
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import aegis_cognition.desktop_service as desktop_service_module
import aegis_cognition.extensions as extensions_module
from aegis_cognition.desktop_service import (
    DesktopService,
    DesktopServiceError,
    MAX_DESKTOP_TOOL_ARGUMENT_BYTES,
    MAX_PROMPT_LENGTH,
    _McpRuntime,
    _select_source_paths_for_prompt,
)
from aegis_cognition.extensions import (
    ExtensionError,
    ExtensionManifest,
    ExtensionRegistry,
    McpResourceDescriptor,
    McpServerSpec,
    McpToolDescriptor,
    ToolSpec,
)
from core.python.aegis import connection_clients, discovery, secrets as secret_store_module
from core.python.aegis.connection_clients import ConnectionResponse, ProviderToolCall, ProviderTurn
from core.python.aegis.cache_economics import CachePromptPlan
from core.python.aegis.connections import ModelDescriptor
from core.python.aegis.discovery import DiscoveryResponse
from aegis_cognition.subagents import (
    AGENT_MESSAGE_SCHEMA_V1,
    AgentClaim,
    AgentCancellationError,
    AgentMessage,
    AgentMessageJournal,
    AgentResultPacket,
    AgentSupervisorResult,
    AgentTaskContext,
)
from core.python.aegis.desktop_protocol import (
    DesktopCommandRouter,
    DesktopProtocolError,
    decode_request,
)
from core.python.aegis.secrets import SecretStoreError


@dataclass(frozen=True)
class _FakeConversation:
    conversation_id: str
    owner_id: str
    title: str
    connection_id: str
    model_id: str
    status: str = "ACTIVE"
    revision: int = 1


@dataclass(frozen=True)
class _FakeTurn:
    conversation_id: str
    turn_id: str
    role: str
    status: str
    content: str
    revision: int
    execution_id: str | None = None


@dataclass(frozen=True)
class _FakeExecution:
    execution_id: str
    conversation_id: str
    turn_id: str
    provider_kind: str
    connection_id: str
    model_id: str
    status: str
    revision: int


@dataclass(frozen=True)
class _FakeCheckpoint:
    execution_id: str
    sequence: int
    state: str
    continuation_json: str
    revision: int


@dataclass(frozen=True)
class _FakeToolCall:
    call_id: str
    conversation_id: str
    request_turn_id: str
    result_turn_id: str | None
    tool_name: str
    arguments_json: str
    result_content: str | None
    status: str
    revision: int


@dataclass(frozen=True)
class _FakeSnapshot:
    conversation: _FakeConversation
    turns: tuple[_FakeTurn, ...] = ()
    executions: tuple[_FakeExecution, ...] = ()
    checkpoints: tuple = ()
    tool_calls: tuple = ()


class _FakeConversationManager:
    def __init__(self) -> None:
        self.snapshot = _FakeSnapshot(_FakeConversation("conv-1", "local-profile", "Smoke", "local", "mock-model"))

    def read(self, _conversation_id: str, *, owner_id: str) -> _FakeSnapshot:
        assert owner_id == self.snapshot.conversation.owner_id
        return self.snapshot

    def append_turn(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        turn = _FakeTurn(
            "conv-1",
            kwargs["turn_id"],
            kwargs["role"],
            kwargs["status"],
            kwargs["content"],
            revision,
        )
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            (*self.snapshot.turns, turn),
            self.snapshot.executions,
            self.snapshot.checkpoints,
            self.snapshot.tool_calls,
        )
        return turn

    def start_execution(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        execution = _FakeExecution(
            kwargs["execution_id"], "conv-1", kwargs["turn_id"], "mock", "local", "mock-model", "RUNNING", revision
        )
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            tuple(
                replace(turn, status="RUNNING", execution_id=execution.execution_id)
                if turn.turn_id == kwargs["turn_id"]
                else turn
                for turn in self.snapshot.turns
            ),
            (*self.snapshot.executions, execution),
            self.snapshot.checkpoints,
            self.snapshot.tool_calls,
        )
        return execution

    def append_part(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        turn = next(turn for turn in self.snapshot.turns if turn.turn_id == kwargs["turn_id"])
        updated = replace(turn, content=kwargs["content"], revision=revision)
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            tuple(updated if item.turn_id == turn.turn_id else item for item in self.snapshot.turns),
            self.snapshot.executions,
            self.snapshot.checkpoints,
            self.snapshot.tool_calls,
        )
        return updated

    def finish_execution(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        execution = next(item for item in self.snapshot.executions if item.execution_id == kwargs["execution_id"])
        updated = replace(execution, status=kwargs["status"], revision=revision)
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            tuple(
                replace(turn, status="COMPLETED") if turn.turn_id == execution.turn_id else turn
                for turn in self.snapshot.turns
            ),
            tuple(
                updated if item.execution_id == execution.execution_id else item for item in self.snapshot.executions
            ),
            self.snapshot.checkpoints,
            self.snapshot.tool_calls,
        )
        return updated

    def transition_turn(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        current = next(turn for turn in self.snapshot.turns if turn.turn_id == kwargs["turn_id"])
        updated = replace(current, status=kwargs["status"], revision=revision)
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            tuple(updated if item.turn_id == current.turn_id else item for item in self.snapshot.turns),
            self.snapshot.executions,
            self.snapshot.checkpoints,
            self.snapshot.tool_calls,
        )
        return updated

    def checkpoint_execution(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        checkpoint = _FakeCheckpoint(
            kwargs["execution_id"],
            kwargs["sequence"],
            kwargs["state"],
            kwargs["continuation_json"],
            revision,
        )
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            self.snapshot.turns,
            self.snapshot.executions,
            (*self.snapshot.checkpoints, checkpoint),
            self.snapshot.tool_calls,
        )
        return checkpoint

    def record_tool_call(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        call = _FakeToolCall(
            kwargs["call_id"],
            "conv-1",
            kwargs["request_turn_id"],
            None,
            kwargs["tool_name"],
            kwargs["arguments_json"],
            None,
            "REQUESTED",
            revision,
        )
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            self.snapshot.turns,
            self.snapshot.executions,
            self.snapshot.checkpoints,
            (*self.snapshot.tool_calls, call),
        )
        return call

    def record_tool_result(self, _conversation_id: str, **kwargs):
        revision = self.snapshot.conversation.revision + 1
        current = next(item for item in self.snapshot.tool_calls if item.call_id == kwargs["call_id"])
        call = replace(
            current,
            result_turn_id=kwargs["result_turn_id"],
            result_content=kwargs["result_content"],
            status=kwargs["status"],
            revision=revision,
        )
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            self.snapshot.turns,
            self.snapshot.executions,
            self.snapshot.checkpoints,
            tuple(call if item.call_id == call.call_id else item for item in self.snapshot.tool_calls),
        )
        return call

    def reconcile_tool_call(self, _conversation_id: str, **kwargs):
        if self.snapshot.conversation.revision != kwargs["expected_revision"]:
            raise ValueError("conversation revision conflict")
        revision = self.snapshot.conversation.revision + 1
        current = next(item for item in self.snapshot.tool_calls if item.call_id == kwargs["call_id"])
        if current.status != "AMBIGUOUS":
            raise ValueError("tool call is not ambiguous")
        reconciled = replace(
            current,
            result_content=kwargs["note"],
            status=kwargs["status"],
            revision=revision,
        )
        self.snapshot = _FakeSnapshot(
            replace(self.snapshot.conversation, revision=revision),
            self.snapshot.turns,
            self.snapshot.executions,
            self.snapshot.checkpoints,
            tuple(reconciled if item.call_id == reconciled.call_id else item for item in self.snapshot.tool_calls),
        )
        return reconciled


@dataclass(frozen=True)
class _FakeConnection:
    connection_id: str
    provider_kind: str = "openai-compatible"
    endpoint: str = "http://127.0.0.1:8080/v1"
    protocol: str = "chat-completions"
    enabled: bool = True


class _FakeConnectionCatalog:
    def __init__(self) -> None:
        self.connection = _FakeConnection("local")

    def list_connections(self):
        return (self.connection,)


class _TextOnlyConnectionCatalog(_FakeConnectionCatalog):
    def list_models(self, _connection_id):
        return (SimpleNamespace(model_id="mock-model", capabilities=("chat",)),)


class _LimitedVisionConnectionCatalog(_FakeConnectionCatalog):
    def list_models(self, _connection_id):
        return (
            ModelDescriptor(
                connection_id="local",
                model_id="mock-model",
                family=None,
                capabilities=("chat", "vision", "vision:max-images:3"),
                context_limit=None,
                output_limit=None,
                source="test",
                revision=1,
                observed_at_ms=1,
            ),
        )


class _ToolCapableConnectionCatalog(_FakeConnectionCatalog):
    def list_models(self, _connection_id):
        return (
            ModelDescriptor(
                connection_id="local",
                model_id="mock-model",
                family=None,
                capabilities=("chat", "tool_calling:declared"),
                context_limit=None,
                output_limit=None,
                source="test",
                revision=1,
                observed_at_ms=1,
            ),
        )


class _EmptyModelConnectionCatalog(_FakeConnectionCatalog):
    def list_models(self, _connection_id):
        return ()


class _FakeSearch:
    results = ()


class _FakeLearning:
    def __init__(self) -> None:
        self.indexed: list[tuple[int, str]] = []

    def index_session_scoped(self, session_id: int, content: str, **_kwargs):
        self.indexed.append((session_id, content))

    def search_past(self, _query: str, top_k: int):
        assert top_k == 8
        return _FakeSearch()


class _FakeClient:
    def __init__(self, observed: list[tuple[str, str]]) -> None:
        self.observed = observed

    def invoke(self, prompt: str) -> str:
        self.observed.append(("prompt", prompt))
        return "live answer"


class _FakeApprovalClient:
    def __init__(self, *, tool_name: str, arguments: dict[str, object]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.system_contexts: list[str | None] = []

    def invoke_turn(self, prompt: str, *, tools, tool_history, **_kwargs):
        self.calls.append((prompt, tuple(tool_history)))
        system_context = _kwargs.get("system_context")
        self.system_contexts.append(system_context if isinstance(system_context, str) else None)
        if len(self.calls) == 1:
            return ProviderTurn(
                "",
                (ProviderToolCall("provider-call-1", self.tool_name, self.arguments),),
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "provider-call-1", "type": "function"}],
                },
                "tool_calls",
            )
        assert tools
        return ProviderTurn("approved action completed", (), {}, "stop")


class _FakeSubagentClient:
    requires_api_key = False


class _FakeSubagentApplication:
    def __init__(self, config) -> None:
        self.config = config

    def run_subagents(self, **kwargs):
        assert kwargs["require_native_authority"] is True
        assert kwargs["max_concurrency"] == 2
        packet = AgentResultPacket(
            run_id="desktop-subagent-run",
            task_id=1,
            parent_task_id=None,
            attempt_id=1,
            status="SUCCEEDED",
            summary="bounded child evidence",
        )
        return AgentSupervisorResult(
            run_id="desktop-subagent-run",
            graph_hash="a" * 64,
            graph_authority="native_runtime",
            status="SUCCEEDED",
            root_output="root synthesis",
            child_results=(packet,),
        )


class _BlockingSubagentApplication:
    started = Event()
    release = Event()

    def __init__(self, _config) -> None:
        self.correlation = SimpleNamespace(run_id="background-subagent-run")

    def run_subagents(self, *, message_journal, **kwargs):
        del kwargs
        journal = message_journal
        journal.append(
            AgentMessage(
                schema=AGENT_MESSAGE_SCHEMA_V1,
                message_kind="TASK_REQUEST",
                run_id="background-subagent-run",
                sender_id="root",
                recipient_id="subagent:1",
                task_id=1,
                parent_task_id=None,
                attempt_id=1,
                idempotency_key="background-subagent-run:1:1:request",
                payload={
                    "role": "research",
                    "prompt": "private prompt stays in the host journal",
                    "dependency_ids": [],
                    "capabilities": ["network_read"],
                    "artifact_namespace": "task-1",
                    "exclusive_resources": [],
                    "side_effect_class": "NetworkRead",
                },
            )
        )
        self.started.set()
        assert self.release.wait(2)
        packet = AgentResultPacket(
            run_id="background-subagent-run",
            task_id=1,
            parent_task_id=None,
            attempt_id=1,
            status="SUCCEEDED",
            summary="background child evidence",
        )
        journal.append(packet.to_message(recipient_id="root"))
        return AgentSupervisorResult(
            run_id="background-subagent-run",
            graph_hash="b" * 64,
            graph_authority="native_runtime",
            status="COMPLETED",
            root_output="background root synthesis",
            child_results=(packet,),
        )


class _CancellableSubagentApplication:
    started = Event()

    def __init__(self, _config) -> None:
        self.correlation = SimpleNamespace(run_id="cancellable-subagent-run")

    def run_subagents(self, *, message_journal, cancel_event, **kwargs):
        del message_journal, kwargs
        self.started.set()
        assert cancel_event.wait(2)
        raise AgentCancellationError("test cancellation")


def _request(command: str, payload: dict | None = None) -> bytes:
    return json.dumps(
        {
            "schema": "aegis-desktop-command-v1",
            "protocol_version": 1,
            "request_id": "req-1",
            "command": command,
            "payload": payload or {},
        }
    ).encode()


def _tested_mcp_config_token(service, server_id: str, descriptor_hash: str, expected_revision: int, config: dict) -> str:
    response = json.loads(
        service.dispatch(
            _request(
                "extensions.test_mcp",
                {
                    "server_id": server_id,
                    "descriptor_hash": descriptor_hash,
                    "expected_revision": expected_revision,
                    "config": config,
                },
            )
        )
    )
    assert response["status"] == "ok", response
    return response["result"]["test_token"]


def test_desktop_protocol_accepts_allowlisted_command_and_routes_typed_payload():
    observed: list[dict] = []
    router = DesktopCommandRouter({"workspace.snapshot": lambda payload: observed.append(payload) or {"revision": 3}})

    response = json.loads(router.dispatch(_request("workspace.snapshot", {"workspace_id": "local"})))

    assert response["status"] == "ok"
    assert response["result"] == {"revision": 3}
    assert observed == [{"workspace_id": "local"}]


def test_desktop_protocol_rejects_arbitrary_code_and_duplicate_keys():
    with pytest.raises(DesktopProtocolError, match="not allowed"):
        decode_request(_request("execute.code"))
    with pytest.raises(DesktopProtocolError, match="duplicate"):
        decode_request(
            b'{"protocol_version":1,"request_id":"req-1","request_id":"req-2",'
            b'"command":"workspace.snapshot","payload":{}}'
        )


def test_desktop_protocol_hides_handler_details_and_bounds_frames():
    router = DesktopCommandRouter({"workspace.snapshot": lambda _payload: 7})
    response = json.loads(router.dispatch(_request("workspace.snapshot")))
    assert response["status"] == "error"
    assert response["error"] == {
        "code": "INVALID_HANDLER_RESULT",
        "message": "desktop handler returned a non-object",
    }

    with pytest.raises(DesktopProtocolError, match="exceeds"):
        decode_request(_request("workspace.snapshot"), max_frame_bytes=8)


def test_desktop_service_emits_handshake_opens_workspace_and_shuts_down(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile", profile_id="local-profile")
    frames = (
        b"\n".join(
            [
                _request("workspace.open"),
                _request("workspace.snapshot"),
                _request("service.shutdown"),
            ]
        )
        + b"\n"
    )
    output = io.BytesIO()

    assert service.run(io.BytesIO(frames), output) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]

    assert responses[0]["schema"] == "aegis-desktop-ready-v1"
    assert responses[0]["protocol_version"] == 1
    assert responses[0]["commands"] == sorted(service._handlers())
    assert {"runs.start", "runs.inspect", "runs.cancel"}.issubset(responses[0]["commands"])
    assert "runs.resume" not in responses[0]["commands"]
    assert responses[1]["status"] == "ok"
    assert responses[1]["result"]["open"] is True
    assert Path(responses[1]["result"]["state_path"]).parts[-2:] == (".aegis", "state.db")
    assert responses[2]["result"]["open"] is True
    assert responses[3]["result"] == {"stopping": True}
    assert service._stop_requested is True


def test_desktop_run_cancel_is_acknowledged_before_cooperative_stop(tmp_path: Path):
    service = DesktopService(profile_root=tmp_path / "profile")
    service._opened_root = tmp_path
    service._manager = _FakeConversationManager()
    entered = Event()

    def wait_for_stop(_payload, *, cancel_event):
        entered.set()
        assert cancel_event.wait(timeout=2)
        raise desktop_service_module._DesktopRunCancelled()

    service._send_message = wait_for_stop
    started = json.loads(
        service.dispatch(
            _request(
                "runs.start",
                {
                    "conversation_id": "conv-1",
                    "message": "inspect this task",
                    "mode": "live",
                },
            )
        )
    )
    assert started["status"] == "ok"
    run = started["result"]
    assert run["status"] == "RUNNING"
    assert entered.wait(timeout=2)

    cancellation = json.loads(
        service.dispatch(
            _request(
                "runs.cancel",
                {"run_id": run["run_id"], "conversation_id": "conv-1"},
            )
        )
    )
    assert cancellation["result"]["status"] == "CANCELLING"
    assert cancellation["result"]["cancel_requested_at_ms"] is not None

    state = service._desktop_runs[run["run_id"]]
    assert state.thread is not None
    state.thread.join(timeout=2)
    inspected = json.loads(
        service.dispatch(
            _request(
                "runs.inspect",
                {"run_id": run["run_id"], "conversation_id": "conv-1"},
            )
        )
    )
    assert inspected["result"]["status"] == "CANCELLED"

    wrong_conversation = json.loads(
        service.dispatch(
            _request(
                "runs.inspect",
                {"run_id": run["run_id"], "conversation_id": "another-conversation"},
            )
        )
    )
    assert wrong_conversation["error"]["code"] == "RUN_NOT_FOUND"


def test_existing_conversation_manager_does_not_require_native_runtime(tmp_path: Path, monkeypatch):
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        service._opened_root = tmp_path
        manager = _FakeConversationManager()
        service._manager = manager
        monkeypatch.setattr(desktop_service_module, "native_runtime_available", lambda: False)

        assert service._manager_for_request() is manager

        service._manager = None
        with pytest.raises(DesktopServiceError, match="bundled native runtime is unavailable"):
            service._manager_for_request()
    finally:
        service.close()


def test_desktop_service_rejects_workspace_use_before_open(tmp_path: Path):
    service = DesktopService(profile_root=tmp_path / "profile")
    response = json.loads(service.dispatch(_request("workspace.snapshot")))
    assert response["status"] == "ok"
    assert response["result"]["open"] is False

    response = json.loads(service.dispatch(_request("conversations.list")))
    assert response["status"] == "error"
    assert response["error"]["code"] == "WORKSPACE_NOT_OPEN"

    response = json.loads(service.dispatch(_request("workspace.source_snapshot")))
    assert response["status"] == "error"
    assert response["error"]["code"] == "WORKSPACE_NOT_OPEN"


def test_desktop_service_close_drops_session_only_credentials(tmp_path: Path):
    service = DesktopService(profile_root=tmp_path / "profile")
    reference = service._remember_session_secret("sentinel-api-key")
    service._session_connection_refs["local"] = reference

    assert service._secret_store().resolve(reference) == "sentinel-api-key"
    service.close()

    assert service._session_secrets == {}
    assert service._session_connection_refs == {}
    with pytest.raises(SecretStoreError, match="secret is unavailable"):
        service._secret_store().resolve(reference)


def test_desktop_service_persists_non_secret_settings_with_revision_conflict_protection(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile", profile_id="local-profile")

    initial = json.loads(service.dispatch(_request("settings.get")))
    assert initial["status"] == "ok"
    assert initial["result"]["revision"] == 0
    updated = json.loads(
        service.dispatch(
            _request(
                "settings.update",
                {"expected_revision": 0, "settings": {"theme": "dark", "instructions": "Keep evidence explicit."}},
            )
        )
    )
    assert updated["status"] == "ok"
    assert updated["result"]["revision"] == 1
    assert updated["result"]["settings"]["theme"] == "dark"
    stale = json.loads(
        service.dispatch(_request("settings.update", {"expected_revision": 0, "settings": {"theme": "light"}}))
    )
    assert stale["status"] == "error"
    assert stale["error"]["code"] == "SETTINGS_CONFLICT"
    assert "api_key" not in json.dumps(updated)


def test_desktop_service_lists_canonical_conversations_for_the_renderer(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile", profile_id="local-profile")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
    assert (
        json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "desktop-list-check",
                        "title": "List check",
                        "connection_id": "local",
                        "model_id": "local-model",
                    },
                )
            )
        )["status"]
        == "ok"
    )

    response = json.loads(service.dispatch(_request("conversations.list")))

    assert response["status"] == "ok"
    assert response["result"]["records"][0]["conversation_id"] == "desktop-list-check"
    assert response["result"]["records"][0]["owner_id"] == "local-profile"

    inspection = json.loads(
        service.dispatch(_request("conversations.inspect", {"conversation_id": "desktop-list-check"}))
    )
    assert inspection["status"] == "ok"
    assert inspection["result"]["inspection"]["schema"] == "aegis-desktop-conversation-inspection-v1"
    assert inspection["result"]["inspection"]["conversation_id"] == "desktop-list-check"
    assert "redacted" in inspection["result"]["inspection"]["redaction"]


def test_desktop_memory_commands_bind_owner_to_the_active_profile(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    searched_owners: list[str] = []

    class Learning:
        def search_memories(
            self, _query: str, _top_k: int, *, owner_id: str, **_kwargs: object
        ) -> tuple[object, ...]:
            searched_owners.append(owner_id)
            return ()

    service = DesktopService(
        profile_root=tmp_path / "profile",
        profile_id="local-profile",
        learning_factory=Learning,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    accepted = json.loads(service.dispatch(_request("memory.search", {"query": "project", "top_k": 1})))
    rejected = json.loads(
        service.dispatch(
            _request("memory.search", {"query": "project", "top_k": 1, "owner_id": "another-profile"})
        )
    )

    assert accepted["status"] == "ok"
    assert searched_owners == ["local-profile"]
    assert rejected["status"] == "error"
    assert rejected["error"]["code"] == "OWNER_SCOPE_FORBIDDEN"
    assert searched_owners == ["local-profile"]


def test_desktop_service_switch_model_requires_verified_connection_catalog(tmp_path: Path):
    service = DesktopService(profile_root=tmp_path / "profile")
    connection = SimpleNamespace(connection_id="local", enabled=True)
    known_model = SimpleNamespace(connection_id="local", model_id="verified-model")
    catalog = SimpleNamespace(
        list_connections=lambda: (connection,),
        list_models=lambda connection_id: (known_model,) if connection_id == "local" else (),
    )
    manager = SimpleNamespace(
        switch_model=lambda conversation_id, **kwargs: {
            "conversation_id": conversation_id,
            "connection_id": kwargs["connection_id"],
            "model_id": kwargs["model_id"],
        }
    )
    service._connections_for_request = lambda: catalog
    service._manager_for_request = lambda: manager

    accepted = json.loads(
        service.dispatch(
            _request(
                "conversations.switch_model",
                {
                    "conversation_id": "conv-1",
                    "connection_id": "local",
                    "model_id": "verified-model",
                    "expected_revision": 1,
                },
            )
        )
    )
    assert accepted["status"] == "ok"
    assert accepted["result"]["record"]["model_id"] == "verified-model"

    rejected = json.loads(
        service.dispatch(
            _request(
                "conversations.switch_model",
                {
                    "conversation_id": "conv-1",
                    "connection_id": "local",
                    "model_id": "unverified-model",
                    "expected_revision": 1,
                },
            )
        )
    )
    assert rejected["status"] == "error"
    assert rejected["error"]["code"] == "MODEL_NOT_AVAILABLE"


def test_desktop_service_rejects_unverified_key_without_leaving_active_connection(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)

    class FailingCatalog:
        def __init__(self) -> None:
            self.record = None

        def list_connections(self):
            return (self.record,) if self.record is not None else ()

        def register_connection(self, connection_id, **kwargs):
            self.record = SimpleNamespace(
                connection_id=connection_id,
                provider_kind=kwargs["provider_kind"],
                endpoint=kwargs["endpoint"],
                protocol=kwargs["protocol"],
                secret_ref=kwargs["secret_ref"],
                enabled=kwargs["enabled"],
                revision=1,
            )
            return self.record

        def list_models(self, _connection_id):
            return ()

        def get_egress(self, _connection_id, _data_class, _operation):
            return None

        def grant_egress(self, _grant_id, _connection_id, _data_class, _operation, **_kwargs):
            return SimpleNamespace(revision=1)

        def check_egress(self, _connection_id, _data_class, _operation):
            return True

        def discover_models(self, _connection_id, **_kwargs):
            raise RuntimeError("model discovery returned HTTP 401")

        def revoke_connection(self, _connection_id, **_kwargs):
            self.record = SimpleNamespace(**{**vars(self.record), "enabled": False})
            return self.record

    catalog = FailingCatalog()
    service = DesktopService(profile_root=tmp_path / "profile", connection_catalog_factory=lambda: catalog)
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "connections.connect",
                {"api_key": "sk-invalid-for-test", "provider_kind": "openai"},
            )
        )
    )
    assert response["status"] == "error"
    assert response["error"]["code"] == "PROVIDER_AUTH_FAILED"
    assert "sk-invalid-for-test" not in json.dumps(response)
    assert catalog.record is not None and catalog.record.enabled is False
    service.close()


@pytest.mark.parametrize(
    ("provider_kind", "endpoint", "expected_code"),
    [
        (None, None, "INVALID_ARGUMENT"),
        ("openai-compatible", None, "PROVIDER_ENDPOINT_REQUIRED"),
        ("unknown-provider", None, "PROVIDER_NOT_SUPPORTED"),
        ("openai", "https://gateway.example/v1", "PROVIDER_ENDPOINT_MISMATCH"),
    ],
)
def test_connect_requires_explicit_provider_before_catalog_or_network_access(
    tmp_path: Path, provider_kind: str | None, endpoint: str | None, expected_code: str
):
    secret = "test-opaque-private-key-value"
    catalog_factory_calls = 0

    def forbidden_catalog_factory():
        nonlocal catalog_factory_calls
        catalog_factory_calls += 1
        raise AssertionError("ambiguous keys must be rejected before provider setup")

    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=forbidden_catalog_factory,
    )
    payload: dict[str, str] = {"api_key": secret, "connection_id": "local"}
    if provider_kind is not None:
        payload["provider_kind"] = provider_kind
    if endpoint is not None:
        payload["endpoint"] = endpoint
    response = json.loads(service.dispatch(_request("connections.connect", payload)))

    assert response["status"] == "error"
    assert response["error"]["code"] == expected_code
    assert secret not in json.dumps(response)
    assert catalog_factory_calls == 0
    service.close()


@pytest.mark.parametrize(
    ("secret", "provider_kind", "endpoint"),
    [
        ("AIza-shaped-but-not-gemini", "openai", "https://api.openai.com/v1"),
        (
            "shared-format-opaque-key",
            "google-gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ),
        ("unrecognized-secret-format", "deepseek", "https://api.deepseek.com"),
    ],
)
def test_explicit_provider_selection_controls_destination_not_key_format(
    tmp_path: Path, monkeypatch, secret: str, provider_kind: str, endpoint: str
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    observed: dict[str, object] = {}

    class CapturingCatalog:
        def list_connections(self):
            return ()

        def list_models(self, _connection_id):
            return ()

        def register_connection(self, connection_id, **kwargs):
            observed.update(kwargs)
            self.record = SimpleNamespace(connection_id=connection_id, revision=1, **kwargs)
            return self.record

        def get_egress(self, *_args):
            return None

        def grant_egress(self, *_args, **_kwargs):
            pass

        def check_egress(self, *_args):
            return True

        def discover_models(self, *_args, **_kwargs):
            observed["discovery_started"] = True
            raise RuntimeError("model discovery returned HTTP 401")

        def revoke_connection(self, *_args, **_kwargs):
            pass

    catalog = CapturingCatalog()
    service = DesktopService(profile_root=tmp_path / "profile", connection_catalog_factory=lambda: catalog)
    opened = json.loads(service.dispatch(_request("workspace.open")))
    assert opened["status"] == "ok", opened
    response = json.loads(
        service.dispatch(
            _request(
                "connections.connect",
                {"api_key": secret, "connection_id": "local", "provider_kind": provider_kind},
            )
        )
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "PROVIDER_AUTH_FAILED"
    assert observed["provider_kind"] == provider_kind
    assert observed["endpoint"] == endpoint
    assert observed["discovery_started"] is True
    assert secret not in json.dumps(response)
    service.close()


def test_connection_list_returns_provider_choices_from_the_backend_registry(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)

    class EmptyCatalog:
        def list_connections(self):
            return ()

    service = DesktopService(profile_root=tmp_path / "profile", connection_catalog_factory=EmptyCatalog)
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        response = json.loads(service.dispatch(_request("connections.list")))
        assert response["status"] == "ok", response
        result = response["result"]
        assert result["records"] == []
        assert {item["provider_kind"] for item in result["providers"]} >= {
            "openai",
            "openrouter",
            "google-gemini",
            "nvidia-nim",
            "openai-compatible",
        }
        assert all("api_key" not in item and "secret" not in item for item in result["providers"])
    finally:
        service.close()


def test_desktop_service_runs_subagents_through_the_canonical_application(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    observed_configs = []

    def make_application(config):
        observed_configs.append(config)
        return _FakeSubagentApplication(config)

    monkeypatch.setattr("aegis_cognition.desktop_service.playwright_browser_available", lambda: True)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=_FakeConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: _FakeSubagentClient(),
        subagent_application_factory=make_application,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "subagents.run",
                {
                    "task": "inspect the workspace",
                    "connection_id": "local",
                    "model_id": "local-model",
                    "max_concurrency": 2,
                },
            )
        )
    )

    assert response["status"] == "ok"
    result = response["result"]
    assert result["schema"] == "aegis-desktop-subagents-result-v1"
    assert result["root_output"] == "root synthesis"
    assert result["child_results"][0]["summary"] == "bounded child evidence"
    assert callable(observed_configs[0].options["subagent_browser_handler"])


def test_desktop_service_rejects_subagents_model_missing_from_catalog(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=_EmptyModelConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: pytest.fail("unverified model must not create a provider client"),
        subagent_application_factory=lambda _config: pytest.fail("unverified model must not start subagents"),
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "subagents.run",
                {"task": "inspect the workspace", "connection_id": "local", "model_id": "local-model"},
            )
        )
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "MODEL_NOT_AVAILABLE"


def test_desktop_service_enforces_disabled_parallel_workers_at_host_boundary(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=_FakeConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: pytest.fail("disabled workers must not create a provider client"),
        subagent_application_factory=lambda _config: pytest.fail("disabled workers must not start a run"),
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
    updated = json.loads(
        service.dispatch(
            _request("settings.update", {"expected_revision": 0, "settings": {"parallel_workers_enabled": False}})
        )
    )
    assert updated["status"] == "ok"
    definitions, _descriptor_hashes = service._provider_tool_definitions(
        "delegate parallel research",
        SimpleNamespace(capabilities=("tool_calling:declared",)),
    )
    assert all(item.name != desktop_service_module.PROVIDER_SUBAGENT_TOOL_NAME for item in definitions)
    assert all(item.name != desktop_service_module.PROVIDER_TOOL_DISCOVERY_NAME for item in definitions)

    response = json.loads(
        service.dispatch(
            _request(
                "subagents.start",
                {"task": "inspect the workspace", "connection_id": "local", "model_id": "local-model"},
            )
        )
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "SUBAGENTS_DISABLED"


def test_desktop_service_starts_background_subagents_and_polls_cursored_state(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    _BlockingSubagentApplication.started.clear()
    _BlockingSubagentApplication.release.clear()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=_FakeConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: _FakeSubagentClient(),
        subagent_application_factory=_BlockingSubagentApplication,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "subagents.start",
                {"task": "inspect the workspace", "connection_id": "local", "model_id": "local-model"},
            )
        )
    )
    assert response["status"] == "ok"
    start = response["result"]
    assert start["schema"] == "aegis-desktop-subagents-start-v1"
    assert start["run_id"] == "background-subagent-run"
    assert _BlockingSubagentApplication.started.wait(1)

    running = json.loads(service.dispatch(_request("subagents.status", {"run_id": start["run_id"]})))
    assert running["result"]["status"] == "RUNNING"
    events = json.loads(service.dispatch(_request("subagents.events", {"run_id": start["run_id"], "max_messages": 4})))
    assert events["result"]["latest_cursor"] == 1
    assert "private prompt" not in json.dumps(events)

    graph = json.loads(service.dispatch(_request("subagents.graph", {"run_id": start["run_id"]})))
    assert graph["status"] == "ok"
    assert graph["result"]["graph"]["schema"] == "aegis-desktop-subagent-graph-v1"
    assert "redacted" in graph["result"]["graph"]["redaction"]
    assert "private prompt" not in json.dumps(graph)
    assert graph["result"]["graph"]["nodes"][0]["task_id"] == 1

    _BlockingSubagentApplication.release.set()
    deadline = time.monotonic() + 2
    terminal = running
    while time.monotonic() < deadline:
        terminal = json.loads(service.dispatch(_request("subagents.status", {"run_id": start["run_id"]})))
        if terminal["result"]["status"] == "COMPLETED":
            break
        time.sleep(0.01)
    assert terminal["result"]["status"] == "COMPLETED"
    assert terminal["result"]["result"]["root_output"] == "background root synthesis"


def test_desktop_service_cancels_background_subagents_cooperatively(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    _CancellableSubagentApplication.started.clear()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=_FakeConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: _FakeSubagentClient(),
        subagent_application_factory=_CancellableSubagentApplication,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
    start = json.loads(
        service.dispatch(
            _request(
                "subagents.start",
                {"task": "inspect the workspace", "connection_id": "local", "model_id": "local-model"},
            )
        )
    )["result"]
    assert _CancellableSubagentApplication.started.wait(1)

    cancelled = json.loads(service.dispatch(_request("subagents.cancel", {"run_id": start["run_id"]})))
    assert cancelled["status"] == "ok"
    assert cancelled["result"]["status"] == "CANCELLING"

    deadline = time.monotonic() + 2
    terminal = cancelled
    while time.monotonic() < deadline:
        terminal = json.loads(service.dispatch(_request("subagents.status", {"run_id": start["run_id"]})))
        if terminal["result"]["status"] == "CANCELLED":
            break
        time.sleep(0.01)
    assert terminal["result"]["status"] == "CANCELLED"
    assert terminal["result"]["error"]["code"] == "SUBAGENT_CANCELLED"
    assert terminal["result"]["cancel_requested_at_ms"] is not None


def test_desktop_service_requires_host_approval_for_code_reuse_materialization(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "profile"
    root.mkdir()
    (root / "source.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    service = DesktopService(profile_root=root)
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    assessment = json.loads(
        service.dispatch(
            _request(
                "code_reuse.assess",
                {
                    "source_path": "source.py",
                    "start_line": 1,
                    "end_line": 2,
                    "generation_tokens": 80,
                    "adaptation_tokens": 0,
                    "verification_tokens": 3,
                },
            )
        )
    )
    assert assessment["status"] == "ok"
    assert assessment["result"]["assessment"]["decision"] == "REUSE_EXACT"
    candidate = assessment["result"]["candidate"]
    assert candidate["license_id"] == "INTERNAL"

    materialized = json.loads(
        service.dispatch(
            _request(
                "code_reuse.materialize",
                {
                    "source_path": "source.py",
                    "start_line": 1,
                    "end_line": 2,
                    "target_relative_path": "copied.py",
                },
            )
        )
    )
    assert materialized["status"] == "error"
    assert materialized["error"]["code"] == "APPROVAL_REQUIRED"
    assert not (root / "copied.py").exists()
    duplicate = json.loads(
        service.dispatch(
            _request(
                "code_reuse.materialize",
                {
                    "source_path": "source.py",
                    "start_line": 1,
                    "end_line": 2,
                    "target_relative_path": "copied.py",
                },
            )
        )
    )
    assert duplicate["status"] == "error"
    assert duplicate["error"]["code"] == "APPROVAL_REQUIRED"
    assert not (root / "copied.py").exists()


def test_desktop_service_exposes_cursored_redacted_subagent_events(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    journal = AgentMessageJournal(max_messages=2)
    journal.append(
        AgentMessage(
            schema=AGENT_MESSAGE_SCHEMA_V1,
            message_kind="TASK_REQUEST",
            run_id="event-run",
            sender_id="root",
            recipient_id="subagent:1",
            task_id=1,
            parent_task_id=None,
            attempt_id=1,
            idempotency_key="event-run:1:1:request",
            payload={
                "role": "research",
                "prompt": "private prompt must not cross the desktop event boundary",
                "dependency_ids": [],
                "capabilities": ["network_read"],
                "artifact_namespace": "task-1",
                "exclusive_resources": [],
                "side_effect_class": "NetworkRead",
            },
        )
    )
    service._remember_subagent_event_journal("event-run", journal)

    response = json.loads(service.dispatch(_request("subagents.events", {"run_id": "event-run", "max_messages": 2})))

    assert response["status"] == "ok"
    page = response["result"]
    assert page["schema"] == "aegis-desktop-subagent-events-v1"
    assert page["latest_cursor"] == 1
    assert page["events"][0]["payload"]["redacted"] is True
    assert "private prompt" not in json.dumps(page)


def test_desktop_service_persists_subagent_root_in_the_selected_conversation(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
        connection_catalog_factory=_FakeConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: _FakeSubagentClient(),
        subagent_application_factory=_FakeSubagentApplication,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "subagents.run",
                {"task": "inspect this conversation", "conversation_id": "conv-1", "max_concurrency": 2},
            )
        )
    )

    assert response["status"] == "ok"
    assert response["result"]["conversation_revision"] == 6
    persisted = service._manager.read("conv-1", owner_id="local-profile")
    assert persisted.turns[-1].content == "root synthesis"
    assert persisted.executions[-1].status == "COMPLETED"


def test_desktop_service_exposes_aeese_session_through_shared_facade(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile", profile_id="local-profile")
    opened = json.loads(service.dispatch(_request("workspace.open")))
    assert opened["status"] == "ok"
    started = json.loads(
        service.dispatch(
            _request(
                "verification.start_session",
                {
                    "task": "implement the feature",
                    "expected_behavior": "the feature returns the documented value",
                },
            )
        )
    )
    assert started["status"] == "ok"
    assert started["result"]["packet"]["authority_state"] == "SHADOW_ONLY"
    assert started["result"]["packet"]["can_start"] is True


def test_desktop_service_rejects_switching_workspace_after_open(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "one")
    first = json.loads(service.dispatch(_request("workspace.open")))
    assert first["status"] == "ok"
    second = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(tmp_path / "two")})))
    assert second["status"] == "error"
    assert second["error"]["code"] == "WORKSPACE_ALREADY_OPEN"


def test_desktop_service_switches_workspace_without_changing_open_contract(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "one")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    target = tmp_path / "two"
    switched = json.loads(service.dispatch(_request("workspace.switch", {"workspace_path": str(target)})))

    assert switched["status"] == "ok"
    assert Path(switched["result"]["workspace_path"]) == target.resolve()
    assert Path(service._state_path or "") == (target / ".aegis" / "state.db").resolve()
    assert Path(os.environ["AEGIS_SESSION_DB_PATH"]) == (target / ".aegis" / "state.db").resolve()


def test_desktop_service_registers_and_lists_imported_projects_without_copying_them(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    profile = tmp_path / "profile"
    first = tmp_path / "first-project"
    second = tmp_path / "second-project"
    first.mkdir()
    second.mkdir()
    service = DesktopService(profile_root=profile, profile_id="local-profile")

    opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(first)})))
    assert opened["status"] == "ok"
    listed = json.loads(service.dispatch(_request("projects.list")))
    assert listed["status"] == "ok"
    assert listed["result"]["schema"] == "aegis-desktop-projects-v1"
    assert listed["result"]["active_project_id"].startswith("project:")
    assert listed["result"]["projects"][0]["path"] == str(first.resolve())
    assert not (first / "conversations.db").exists()

    switched = json.loads(service.dispatch(_request("workspace.switch", {"workspace_path": str(second)})))
    assert switched["status"] == "ok"
    listed = json.loads(service.dispatch(_request("projects.list")))
    assert {item["path"] for item in listed["result"]["projects"]} == {
        str(first.resolve()),
        str(second.resolve()),
    }
    first_id = next(item["project_id"] for item in listed["result"]["projects"] if item["path"] == str(first.resolve()))
    removed = json.loads(service.dispatch(_request("projects.remove", {"project_id": first_id})))
    assert removed["status"] == "ok"
    assert removed["result"]["removed"] is True
    assert first.is_dir()
    assert all(
        item["path"] != str(first.resolve())
        for item in json.loads(service.dispatch(_request("projects.list")))["result"]["projects"]
    )


def test_desktop_service_clones_only_approved_public_hosts(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    def fake_git_clone(args, **_kwargs):
        destination = Path(args[-1])
        destination.mkdir(parents=True)
        (destination / ".git").mkdir()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("aegis_cognition.desktop_service.subprocess.run", fake_git_clone)
    cloned = json.loads(
        service.dispatch(
            _request(
                "workspace.clone",
                {
                    "clone_url": "https://github.com/example/project.git",
                    "destination_root": str(tmp_path / "clones"),
                },
            )
        )
    )

    assert cloned["status"] == "ok"
    assert cloned["result"]["name"] == "project"
    assert Path(cloned["result"]["workspace_path"]).is_dir()

    rejected = json.loads(
        service.dispatch(_request("workspace.clone", {"clone_url": "https://example.invalid/project.git"}))
    )
    assert rejected["status"] == "error"
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"


def test_desktop_service_exposes_bounded_source_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "main.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    service = DesktopService(profile_root=tmp_path / "profile")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(service.dispatch(_request("workspace.source_snapshot")))

    assert response["status"] == "ok"
    snapshot = response["result"]["snapshot"]
    assert snapshot["identity"]["vcs"] == "none"
    assert snapshot["revision"]
    assert snapshot["files"]
    assert snapshot["files"][0]["extraction_status"] in {"EXTRACTED", "FALLBACK_HASH_ONLY"}


def test_workspace_file_read_is_utf8_bounded_and_stays_inside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "app.py"
    source.write_text("print('in workspace')\n", encoding="utf-8", newline="\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    service = DesktopService(profile_root=tmp_path / "profile")
    service._opened_root = workspace

    result = json.loads(service.dispatch(_request("workspace.file_read", {"relative_path": "app.py"})))
    assert result["status"] == "ok"
    assert result["result"]["content"] == "print('in workspace')\n"
    assert result["result"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()

    traversal = json.loads(service.dispatch(_request("workspace.file_read", {"relative_path": "../outside.txt"})))
    assert traversal["error"]["code"] == "INVALID_PATH"
    hidden_state = json.loads(service.dispatch(_request("workspace.file_read", {"relative_path": ".aegis/state.db"})))
    assert hidden_state["error"]["code"] == "INVALID_PATH"

    link = workspace / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows account")
    linked = json.loads(service.dispatch(_request("workspace.file_read", {"relative_path": "outside-link.txt"})))
    assert linked["error"]["code"] == "INVALID_PATH"


def test_provider_code_tools_search_read_and_redact_bounded_source(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "app.py"
    source.write_text(
        'API_KEY = "test-sk-test-key-abcdefghijklmnopqrstuvwxyz"\n'
        'API_TOKEN = "test-only-api-token-placeholder"\n'
        'ACCESS_TOKEN: str = "test-only-opaque-credential-placeholder"\n'
        'CLIENT_SECRET = "test-prefix-\\\"tail-placeholder"\n'
        "def lookup_items():\n"
        "    return API_KEY\n",
        encoding="utf-8",
    )
    (workspace / ".env.py").write_text("def hidden_secret():\n    return 1\n", encoding="utf-8")
    service = DesktopService(profile_root=workspace)
    try:
        opened = json.loads(service.dispatch(_request("workspace.open")))
        assert opened["status"] == "ok", opened
        search_spec = service._extension_registry.get_tool_spec(desktop_service_module.PROVIDER_CODE_SEARCH_TOOL_NAME)
        read_spec = service._extension_registry.get_tool_spec(desktop_service_module.PROVIDER_CODE_READ_TOOL_NAME)
        copy_spec = service._extension_registry.get_tool_spec(desktop_service_module.PROVIDER_CODE_COPY_TOOL_NAME)
        assert search_spec is not None and search_spec.effect_class == "read_only"
        assert read_spec is not None and read_spec.effect_class == "read_only"
        assert copy_spec is not None and copy_spec.effect_class == "state_write"
        assert "overwrite" not in copy_spec.input_schema["properties"]
        assert search_spec.name in service._extension_registry.prompt_catalog("lookup_items")
        for tool_name in (search_spec.name, read_spec.name, copy_spec.name):
            definitions, _ = service._provider_tool_definitions(
                tool_name,
                SimpleNamespace(capabilities=("tool_calling:declared",)),
            )
            assert tool_name in {item.name for item in definitions}

        search = asyncio.run(
            service._extension_registry.invoke(
                search_spec.name,
                {"query": "lookup_items"},
                expected_effect_class=search_spec.effect_class,
                expected_descriptor_hash=search_spec.descriptor_hash,
            )
        )
        assert search["schema"] == "aegis-code-search-v1"
        assert any(item["relative_path"] == "app.py" for item in search["files"])
        assert "return API_KEY" not in json.dumps(search)

        excerpt = asyncio.run(
            service._extension_registry.invoke(
                read_spec.name,
                {"relative_path": "app.py", "start_line": 1, "max_lines": 5},
                expected_effect_class=read_spec.effect_class,
                expected_descriptor_hash=read_spec.descriptor_hash,
            )
        )
        assert excerpt["credentials_redacted"] is True
        assert "test-sk-test-key-abcdefghijklmnopqrstuvwxyz" not in excerpt["content"]
        assert "test-only-api-token-placeholder" not in excerpt["content"]
        assert "test-only-opaque-credential-placeholder" not in excerpt["content"]
        assert "tail-placeholder" not in excerpt["content"]
        assert "[REDACTED]" in excerpt["content"]
        assert excerpt["next_line"] == 6
        assert excerpt["has_more"] is True

        source.write_text(
            'API_KEY = "test-sk-test-key-abcdefghijklmnopqrstuvwxyz"\n'
            "def lookup_items():\n"
            "    return 7\n",
            encoding="utf-8",
        )
        refreshed = asyncio.run(
            service._extension_registry.invoke(
                read_spec.name,
                {"relative_path": "app.py", "start_line": 3, "max_lines": 1},
                expected_effect_class=read_spec.effect_class,
                expected_descriptor_hash=read_spec.descriptor_hash,
            )
        )
        assert "return 7" in refreshed["content"]
        assert refreshed["source_file_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()

        with pytest.raises(desktop_service_module.DesktopServiceError, match="secret-like"):
            service._read_provider_code({"relative_path": ".env.py", "start_line": 1, "max_lines": 1})
    finally:
        service.close()


@pytest.mark.parametrize("mutate_source", [False, True])
def test_provider_exact_code_copy_waits_for_host_approval(tmp_path: Path, monkeypatch, mutate_source: bool):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    manager = _FakeConversationManager()
    copy_name = desktop_service_module.PROVIDER_CODE_COPY_TOOL_NAME
    client = _FakeApprovalClient(
        tool_name=copy_name,
        arguments={
            "source_path": "source.py",
            "start_line": 1,
            "end_line": 2,
            "target_relative_path": "copied.py",
            "license_id": "INTERNAL",
        },
    )
    service = DesktopService(
        profile_root=workspace,
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        spec = service._extension_registry.get_tool_spec(copy_name)
        assert spec is not None and spec.effect_class == "state_write"
        monkeypatch.setattr(
            service,
            "_provider_tool_definitions",
            lambda *_args: (
                (service._provider_tool_definition(spec),),
                {copy_name: spec.descriptor_hash},
            ),
        )
        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "copy this exact code", "mode": "live"},
                )
            )
        )
        assert response["status"] == "ok"
        assert response["result"]["pending_approval"] is True
        assert not (workspace / "copied.py").exists()
        pending = manager.snapshot.tool_calls[0]
        assert pending.status == "REQUESTED"
        assert str(workspace) not in pending.arguments_json

        if mutate_source:
            source.write_text("def answer():\n    return 43\n", encoding="utf-8")

        approved = json.loads(
            service.dispatch(
                _request(
                    "approvals.resolve",
                    {
                        "conversation_id": "conv-1",
                        "approval_id": pending.call_id,
                        "decision": "approve",
                        "expected_revision": manager.snapshot.conversation.revision,
                    },
                )
            )
        )
        assert approved["status"] == "ok"
        target = workspace / "copied.py"
        if mutate_source:
            assert not target.exists()
            assert manager.snapshot.tool_calls[0].status == "FAILED"
        else:
            assert target.read_text(encoding="utf-8") == "def answer():\n    return 42\n"
            assert manager.snapshot.tool_calls[0].status == "COMPLETED"
        tool_output = next(turn.content for turn in manager.snapshot.turns if turn.role == "tool")
        assert str(workspace) not in tool_output
    finally:
        service.close()


def test_workspace_changes_show_git_diff_for_modified_and_deleted_tracked_files(tmp_path: Path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable")
    workspace = tmp_path / "repository"
    workspace.mkdir()
    subprocess.run([git, "init", str(workspace)], check=True, capture_output=True)
    modified = workspace / "modified.py"
    deleted = workspace / "deleted.py"
    modified.write_text("value = 'before'\n", encoding="utf-8")
    deleted.write_text("value = 'remove me'\n", encoding="utf-8")
    subprocess.run([git, "-C", str(workspace), "add", "--", "modified.py", "deleted.py"], check=True, capture_output=True)
    subprocess.run(
        [git, "-C", str(workspace), "-c", "user.name=AEGIS test", "-c", "user.email=aegis@example.invalid", "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    modified.write_text("value = 'after'\n", encoding="utf-8")
    deleted.unlink()
    service = DesktopService(profile_root=tmp_path / "profile")
    service._opened_root = workspace

    changes = json.loads(service.dispatch(_request("workspace.changes")))
    assert changes["status"] == "ok"
    assert {item["relative_path"] for item in changes["result"]["entries"]} == {"modified.py", "deleted.py"}

    modified_diff = json.loads(service.dispatch(_request("workspace.changes", {"relative_path": "modified.py"})))
    deleted_diff = json.loads(service.dispatch(_request("workspace.changes", {"relative_path": "deleted.py"})))
    assert "-value = 'before'" in modified_diff["result"]["diff"]
    assert "+value = 'after'" in modified_diff["result"]["diff"]
    assert "-value = 'remove me'" in deleted_diff["result"]["diff"]


def test_source_prompt_path_selection_is_bounded_and_query_ranked():
    records = [{"relative_path": f"src/{index:03d}-module.py"} for index in range(70)]
    records.append({"relative_path": "src/repository.py"})

    selected = _select_source_paths_for_prompt(records, "inspect the repository")

    assert len(selected) == 17
    assert selected[0] == "src/repository.py"
    assert "src/069-module.py" not in selected
    assert (
        _select_source_paths_for_prompt(records, "show the project files")[:16]
        == sorted(item["relative_path"] for item in records)[:16]
    )


def test_desktop_service_discovers_capability_metadata_without_activation(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    (root / ".aegis" / "skills" / "research").mkdir(parents=True)
    (root / ".aegis" / "extensions" / "research").mkdir(parents=True)
    (root / ".aegis" / "mcp" / "public").mkdir(parents=True)
    (root / ".aegis" / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\nversion: 1.0.0\nkeywords: web, evidence\n---\n\nUse bounded sources.\n",
        encoding="utf-8",
    )
    (root / ".aegis" / "extensions" / "research" / "extension.toml").write_text(
        '[extension]\nid = "research"\nversion = "1.0.0"\ndescription = "Research metadata"\ncapabilities = ["network_read"]\n',
        encoding="utf-8",
    )
    (root / ".aegis" / "mcp" / "public" / "mcp.toml").write_text(
        '[mcp]\nid = "public"\ndescription = "Public metadata"\ntransport = "stdio"\n',
        encoding="utf-8",
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"

    catalog_response = json.loads(service.dispatch(_request("extensions.discover")))
    catalog = catalog_response["result"]
    assert catalog["schema"] == "aegis-desktop-extension-catalog-v1"
    assert catalog["skills"][0]["name"] == "research"
    assert catalog["extensions"][0]["extension_id"] == "research"
    assert catalog["mcp_servers"][0]["server_id"] == "public"
    assert catalog["mcp_servers"][0]["approved"] is False
    assert len(catalog["mcp_servers"][0]["descriptor_hash"]) == 64

    skill_response = json.loads(service.dispatch(_request("extensions.load_skill", {"name": "research"})))
    assert "Use bounded sources." in skill_response["result"]["body"]

    mcp_response = json.loads(service.dispatch(_request("extensions.mcp_preview")))
    assert mcp_response["result"]["servers"][0]["approved"] is False

    skill_hash = catalog["skills"][0]["content_hash"]
    enabled = json.loads(
        service.dispatch(
            _request(
                "extensions.set_state",
                {
                    "kind": "skill",
                    "id": "research",
                    "descriptor_hash": skill_hash,
                    "enabled": True,
                    "approved": False,
                    "expected_revision": 0,
                },
            )
        )
    )
    assert enabled["status"] == "ok"
    assert enabled["result"]["records"][0]["enabled"] is True
    active_context = service._active_skill_context("research evidence")
    assert "Use bounded sources." in active_context
    assert "private prompt" not in active_context
    mcp_approved = json.loads(
        service.dispatch(
            _request(
                "extensions.set_state",
                {
                    "kind": "mcp",
                    "id": "public",
                    "descriptor_hash": catalog["mcp_servers"][0]["descriptor_hash"],
                    "enabled": False,
                    "approved": True,
                    "expected_revision": 1,
                },
            )
        )
    )
    assert mcp_approved["status"] == "ok"
    approved_record = next(item for item in mcp_approved["result"]["records"] if item["id"] == "public")
    assert approved_record["approved"] is True


def test_desktop_service_discovers_portable_skills_after_shared_agent_skills(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    shared_root = workspace / ".agents" / "skills"
    claude_root = workspace / ".claude" / "skills"
    legacy_antigravity_root = workspace / ".agent" / "skills"
    hermes_root = workspace / ".hermes" / "skills"
    for root in (shared_root, hermes_root):
        (root / "shared-skill").mkdir(parents=True)
    (hermes_root / "hermes-research").mkdir(parents=True)
    (claude_root / "claude-project").mkdir(parents=True)
    (legacy_antigravity_root / "antigravity-legacy").mkdir(parents=True)
    (shared_root / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Shared portable skill\n---\n\nShared body.\n", encoding="utf-8"
    )
    (hermes_root / "hermes-research" / "SKILL.md").write_text(
        "---\n"
        "name: hermes-research\n"
        "description: Portable Hermes project skill\n"
        "metadata:\n"
        "  hermes:\n"
        "    tags: [research, documentation]\n"
        "    requires_toolsets: [terminal]\n"
        "    config:\n"
        "      - key: docs.root\n"
        "        description: Documentation directory\n"
        "        default: docs\n"
        "---\n\nUse reviewed documentation.\n",
        encoding="utf-8",
    )
    (claude_root / "claude-project" / "SKILL.md").write_text(
        "---\n"
        "name: claude-project\n"
        "description: Claude Code project skill\n"
        "---\n\nClaude project body.\n",
        encoding="utf-8",
    )
    (legacy_antigravity_root / "antigravity-legacy" / "SKILL.md").write_text(
        "---\nname: antigravity-legacy\ndescription: Legacy Antigravity project skill\n---\n\nLegacy Antigravity body.\n",
        encoding="utf-8",
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"

        discovered = json.loads(service.dispatch(_request("extensions.discover", {"task": "documentation"})))

        assert discovered["status"] == "ok"
        skills = {item["name"]: item for item in discovered["result"]["skills"]}
        assert skills["hermes-research"]["source"] == str(hermes_root)
        assert skills["hermes-research"]["keywords"] == ["documentation", "research"]
        assert {item["name"] for item in discovered["result"]["selected_skills"]} == {"hermes-research"}
        assert "requires_toolsets" not in skills["hermes-research"]
        assert "config" not in skills["hermes-research"]
        assert skills["shared-skill"]["source"] == str(shared_root)
        assert skills["shared-skill"]["description"] == "Shared portable skill"
        assert skills["claude-project"]["source"] == str(claude_root)
        assert skills["antigravity-legacy"]["source"] == str(legacy_antigravity_root)
        assert json.loads(service.dispatch(_request("extensions.state")))["result"]["records"] == []

        enabled = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "skill",
                        "id": "shared-skill",
                        "descriptor_hash": skills["shared-skill"]["content_hash"],
                        "enabled": True,
                        "approved": False,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert enabled["status"] == "ok"
        catalog, active_skills = service._enabled_skill_catalog()
        assert [item.name for item in active_skills] == ["shared-skill"]
        assert "Shared body." in catalog.load("shared-skill")
    finally:
        service.close()


def test_skill_resource_tool_is_read_only_bounded_and_requires_enabled_skill(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    root = tmp_path / "project"
    skill_dir = root / ".agents" / "skills" / "research"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: Use references for evidence\n---\n\nRead references/guide.md when needed.\n",
        encoding="utf-8",
    )
    disabled_skill_dir = root / ".agents" / "skills" / "not-enabled"
    disabled_skill_dir.mkdir(parents=True)
    (disabled_skill_dir / "SKILL.md").write_text(
        "---\nname: not-enabled\ndescription: Disabled test skill\n---\n\nMust remain unavailable.\n",
        encoding="utf-8",
    )
    guide_bytes = b"first\nsecond\nthird\n"
    (skill_dir / "references" / "guide.md").write_bytes(guide_bytes)
    manager = _FakeConversationManager()
    client = _FakeApprovalClient(
        tool_name=desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME,
        arguments={"skill": "research", "start_line": 1, "max_lines": 200},
    )
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))
        assert opened["status"] == "ok"
        model = SimpleNamespace(capabilities=("tool_calling:declared",))
        tool_name = desktop_service_module.PROVIDER_SKILL_RESOURCE_TOOL_NAME
        definitions, _ = service._provider_tool_definitions("Read the research reference", model)
        definition_names = {item.name for item in definitions}
        assert tool_name not in definition_names
        assert desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME not in definition_names
        assert desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME not in definition_names
        assert desktop_service_module.TOOL_SCHEMA_READ_TOOL_NAME not in definition_names
        assert desktop_service_module.TOOL_SEARCH_TOOL_NAME not in definition_names

        descriptor = service._extension_registry.get_tool_spec(tool_name)
        assert descriptor is not None and descriptor.effect_class == "read_only"
        catalog = json.loads(service.dispatch(_request("extensions.discover")))
        skill_hash = next(
            item["content_hash"] for item in catalog["result"]["skills"] if item["name"] == "research"
        )
        enabled = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "skill",
                        "id": "research",
                        "descriptor_hash": skill_hash,
                        "enabled": True,
                        "approved": False,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert enabled["status"] == "ok"
        definitions, _ = service._provider_tool_definitions("Read the research reference", model)
        definition_names = {item.name for item in definitions}
        assert tool_name in definition_names
        assert desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME in definition_names
        assert desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME in definition_names
        assert desktop_service_module.TOOL_SCHEMA_READ_TOOL_NAME not in definition_names
        assert desktop_service_module.TOOL_SEARCH_TOOL_NAME not in definition_names

        for index in range(desktop_service_module.MAX_DESKTOP_PROVIDER_TOOLS + 2):
            filler = ToolSpec(
                name=f"fixture.filler_{index:02d}",
                description="A harmless unrelated fixture tool",
                extension_id=f"fixture.filler-{index:02d}",
            )
            service._extension_registry.register(
                ExtensionManifest(
                    extension_id=f"fixture.filler-{index:02d}",
                    version="1",
                    description="A harmless test-only tool",
                    capabilities=("read_only",),
                    tool_names=(filler.name,),
                    source="test",
                    trusted=True,
                ),
                ((filler, lambda _arguments: {"ok": True}),),
            )
        definitions, _ = service._provider_tool_definitions("Read the research reference", model)
        definition_names = {item.name for item in definitions}
        assert len(definitions) <= desktop_service_module.MAX_DESKTOP_PROVIDER_TOOLS
        assert desktop_service_module.PROVIDER_TOOL_DISCOVERY_NAME in definition_names
        assert desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME in definition_names
        assert desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME in definition_names
        assert tool_name in definition_names

        active_prompt, stable_prefix, _ = service._render_provider_prompt(
            SimpleNamespace(turns=()),
            "Read the research reference",
            {"revision": "test", "files": []},
            SimpleNamespace(rendered=""),
            skills_via_tools=True,
        )
        assert "Read references/guide.md when needed." not in active_prompt
        assert "aegis.skills.search_enabled" in stable_prefix
        assert "untrusted data" in stable_prefix
        assert "Read references/guide.md when needed." in service._active_skill_context("evidence references")
        assert service._active_skill_context("unrelated topic") == ""

        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "Read the research reference", "mode": "live"},
                )
            )
        )
        assert response["status"] == "ok", response
        assert len(client.calls) == 2
        assert "Read references/guide.md when needed." not in client.calls[0][0]
        assert "Read references/guide.md when needed." in client.calls[1][1][0].outputs[0].output
        assert manager.snapshot.tool_calls[0].status == "COMPLETED"

        search_tool = service._extension_registry.get_tool_spec(
            desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME
        )
        read_tool = service._extension_registry.get_tool_spec(desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME)
        assert search_tool is not None and search_tool.effect_class == "read_only"
        assert read_tool is not None and read_tool.effect_class == "read_only"
        search_result = asyncio.run(
            service._extension_registry.invoke(
                search_tool.name,
                {"query": "research evidence"},
                expected_effect_class="read_only",
                expected_descriptor_hash=search_tool.descriptor_hash,
            )
        )
        assert [skill["name"] for skill in search_result["skills"]] == ["research"]
        assert "SKILL.md" not in json.dumps(search_result)
        assert search_tool.name in service._extension_registry.prompt_catalog("research evidence")
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(
                service,
                "_enabled_skill_catalog",
                lambda: pytest.fail("a query without searchable terms must skip local Skill discovery"),
            )
            scoped_patch.setattr(
                service,
                "_enabled_remote_skill_records",
                lambda: pytest.fail("a query without searchable terms must skip MCP Skill discovery"),
            )
            empty_search = service._search_enabled_skills_tool({"query": "---"})
        assert empty_search["skills"] == []

        instructions = asyncio.run(
            service._extension_registry.invoke(
                read_tool.name,
                {"skill": "research", "start_line": 1, "max_lines": 200},
                expected_effect_class="read_only",
                expected_descriptor_hash=read_tool.descriptor_hash,
            )
        )
        assert instructions["schema"] == "aegis-enabled-skill-instructions-v1"
        assert instructions["sha256"] == skill_hash
        assert "Read references/guide.md when needed." in instructions["content"]
        with pytest.raises(ExtensionError):
            asyncio.run(
                service._extension_registry.invoke(
                    read_tool.name,
                    {"skill": "not-enabled"},
                    expected_effect_class="read_only",
                    expected_descriptor_hash=read_tool.descriptor_hash,
                )
            )

        result = asyncio.run(
            service._extension_registry.invoke(
                tool_name,
                {"skill": "research", "path": "references/guide.md", "start_line": 2, "max_lines": 1},
                expected_effect_class="read_only",
                expected_descriptor_hash=descriptor.descriptor_hash,
            )
        )
        assert result == {
            "schema": "aegis-skill-resource-v1",
            "skill": "research",
            "path": "references/guide.md",
            "content": "second",
            "sha256": extensions_module._sha256_bytes(guide_bytes),
            "start_line": 2,
            "next_line": 3,
            "total_lines": 3,
            "has_more": True,
        }

        disabled = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "skill",
                        "id": "research",
                        "descriptor_hash": skill_hash,
                        "enabled": False,
                        "approved": False,
                        "expected_revision": 1,
                    },
                )
            )
        )
        assert disabled["status"] == "ok"
        definitions, _ = service._provider_tool_definitions("Read the research reference", model)
        assert tool_name not in {item.name for item in definitions}
        with pytest.raises(ExtensionError, match="enabled skill"):
            asyncio.run(
                service._extension_registry.invoke(
                    tool_name,
                    {"skill": "research", "path": "references/guide.md", "start_line": 1, "max_lines": 1},
                )
            )
    finally:
        service.close()


def test_desktop_capability_discovery_preserves_and_rejects_linked_skill_root(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    skill_root = workspace / ".agents" / "skills"
    skill_root.mkdir(parents=True)
    outside_skills = tmp_path / "outside-skills"
    skill_path = outside_skills / "research" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: research\ndescription: External skill\n---\n", encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )

    actual_resolve = Path.resolve

    def resolve_link_root(path: Path, strict: bool = False) -> Path:
        if path == skill_root:
            return outside_skills
        return actual_resolve(path, strict=strict)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", resolve_link_root)
        _, discovered_skill_roots, _ = service._extension_roots()
        assert skill_root in discovered_skill_roots
        assert outside_skills not in discovered_skill_roots

    actual_link_check = extensions_module._is_link_or_junction
    monkeypatch.setattr(
        extensions_module,
        "_is_link_or_junction",
        lambda path: path == skill_root or actual_link_check(path),
    )
    result = json.loads(service.dispatch(_request("extensions.discover")))

    assert result["status"] == "ok"
    assert result["result"]["skills"] == []


def test_desktop_service_imports_skill_as_disabled_project_data(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "downloaded-skill"
    (source / "references").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / ".git").mkdir()
    skill_body = "---\nname: research\ndescription: Gather evidence\nversion: 1.0.0\n---\n\nUse bounded sources.\n"
    (source / "SKILL.md").write_text(skill_body, encoding="utf-8")
    (source / "references" / "sources.md").write_text("Prefer primary sources.\n", encoding="utf-8")
    (source / "scripts" / "helper.py").write_text("# Imported as data; never executed.\n", encoding="utf-8")
    (source / ".git" / "config").write_text("private repository metadata\n", encoding="utf-8")
    (source / ".env").write_text("PRIVATE_TOKEN=not-copied\n", encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    imported = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
    assert imported["status"] == "ok"
    result = imported["result"]
    assert result["schema"] == "aegis-desktop-skill-import-v1"
    assert result["skill"]["name"] == "research"
    assert result["copied_files"] == 3
    assert result["enabled"] is False

    destination = workspace / ".aegis" / "skills" / "research"
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == skill_body
    assert (destination / "references" / "sources.md").read_text(encoding="utf-8") == "Prefer primary sources.\n"
    assert (destination / "scripts" / "helper.py").exists()
    assert not (destination / ".git").exists()
    assert not (destination / ".env").exists()
    assert result["bytes_copied"] == sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
    assert not any(path.name.startswith(".skill-import-") for path in (workspace / ".aegis").iterdir())
    state = json.loads(service.dispatch(_request("extensions.state")))["result"]
    assert not any(
        item["kind"] == "skill" and item["id"] == "research" and item["enabled"] for item in state["records"]
    )
    catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
    assert any(item["name"] == "research" for item in catalog["skills"])
    service.close()

    reopened = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(reopened.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    reopened_catalog = json.loads(reopened.dispatch(_request("extensions.discover")))["result"]
    assert any(item["name"] == "research" for item in reopened_catalog["skills"])
    reopened_state = json.loads(reopened.dispatch(_request("extensions.state")))["result"]
    assert not any(
        item["kind"] == "skill" and item["id"] == "research" and item["enabled"] for item in reopened_state["records"]
    )
    reopened.close()


def test_enabled_skill_can_be_disabled_after_its_source_is_removed(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    skill_path = workspace / ".agents" / "skills" / "research" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: research\ndescription: Gather evidence\n---\n", encoding="utf-8")
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
        skill_hash = next(item["content_hash"] for item in catalog["skills"] if item["name"] == "research")
        enabled = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "skill",
                        "id": "research",
                        "descriptor_hash": skill_hash,
                        "enabled": True,
                        "approved": False,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert enabled["status"] == "ok"

        skill_path.unlink()
        disabled = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "skill",
                        "id": "research",
                        "descriptor_hash": skill_hash,
                        "enabled": False,
                        "approved": False,
                        "expected_revision": enabled["result"]["revision"],
                    },
                )
            )
        )

        assert disabled["status"] == "ok", disabled
        record = next(
            item
            for item in disabled["result"]["records"]
            if item["kind"] == "skill" and item["id"] == "research"
        )
        assert record["enabled"] is False
    finally:
        service.close()


def _create_capability_pack(root: Path, *, extension_id: str = "research-pack", skill_name: str = "research") -> None:
    skill_root = root / "skills" / skill_name
    skill_root.mkdir(parents=True)
    (root / "mcp" / "research-search").mkdir(parents=True)
    (root / "extension.toml").write_text(
        "[extension]\n"
        f'id = "{extension_id}"\n'
        'version = "1.0.0"\n'
        'description = "Research capability pack"\n'
        'capabilities = ["mcp"]\n'
        f'skills = ["{skill_name}"]\n',
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: Gather evidence\nversion: 1.0.0\n---\n\nUse primary sources.\n",
        encoding="utf-8",
    )
    (root / "mcp" / "research-search" / "mcp.toml").write_text(
        '[mcp]\nid = "research-search"\ndescription = "Research search service"\ntransport = "stdio"\n',
        encoding="utf-8",
    )


def _zip_folder(source: Path, archive: Path, *, wrapper: str | None = None) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            archive_name = (Path(wrapper) / relative).as_posix() if wrapper else relative.as_posix()
            if path.is_dir():
                package.writestr(f"{archive_name.rstrip('/')}/", "")
            else:
                package.write(path, archive_name)


def test_desktop_service_imports_capability_pack_as_inactive_project_data(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "research-pack"
    _create_capability_pack(source)
    (source / "skills" / "research" / "references").mkdir()
    reference = source / "skills" / "research" / "references" / "sources.md"
    reference.write_text("Prefer primary sources.\n", encoding="utf-8")
    script = source / "skills" / "research" / "scripts" / "must_not_run.py"
    script.parent.mkdir()
    script.write_text(
        "from pathlib import Path\nPath(__file__).with_name('executed').touch()\n",
        encoding="utf-8",
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert imported["status"] == "ok", imported
    result = imported["result"]
    assert result["schema"] == "aegis-desktop-extension-pack-import-v1"
    assert result["extension_id"] == "research-pack"
    assert result["skill_names"] == ["research"]
    assert result["mcp_server_ids"] == ["research-search"]
    assert result["auto_activated"] is False

    destination = workspace / ".aegis" / "extensions" / "research-pack"
    assert (destination / "extension.toml").is_file()
    assert (destination / "skills" / "research" / "SKILL.md").is_file()
    assert (destination / "skills" / "research" / "references" / "sources.md").read_text(
        encoding="utf-8"
    ) == "Prefer primary sources.\n"
    assert (destination / "skills" / "research" / "scripts" / "must_not_run.py").is_file()
    assert not (destination / "skills" / "research" / "scripts" / "executed").exists()
    assert (destination / "mcp" / "research-search" / "mcp.toml").is_file()

    catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
    assert any(item["extension_id"] == "research-pack" for item in catalog["extensions"])
    assert any(item["name"] == "research" for item in catalog["skills"])
    server = next(item for item in catalog["mcp_servers"] if item["server_id"] == "research-search")
    assert server["approved"] is False
    state = json.loads(service.dispatch(_request("extensions.state")))["result"]
    assert not any(
        item["id"] == "research-search" and (item["approved"] or item["enabled"])
        for item in state["records"]
        if item["kind"] == "mcp"
    )
    assert result["copied_files"] == 5
    service.close()


def test_desktop_imports_wasm_tool_inactive_then_requires_approval_to_activate(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "compute-plugin"
    source.mkdir()
    (source / "extension.toml").write_text(
        '[extension]\nid = "compute-demo"\nversion = "1.0.0"\n'
        'description = "A bounded compute-only plugin"\ncapabilities = ["compute"]\n'
        'tools = ["compute-demo.answer"]\n',
        encoding="utf-8",
    )
    (source / "wasm.toml").write_text(
        '[wasm]\nschema = "aegis-wasm-plugin-v1"\nmodule = "plugin.wasm"\n'
        'fuel_limit = 10000\nmemory_pages = 8\ntimeout_ms = 1000\nmax_output_bytes = 4096\n\n'
        '[[tool]]\nname = "compute-demo.answer"\nexport = "answer"\n'
        'description = "Return a computed answer"\n'
        'input_schema = { type = "object", properties = { value = { type = "integer" } }, required = ["value"], additionalProperties = false }\n'
        'output_schema = { type = "object", properties = { answer = { type = "integer" } }, required = ["answer"], additionalProperties = false }\n',
        encoding="utf-8",
    )
    module = b"\x00asm\x01\x00\x00\x00"
    (source / "plugin.wasm").write_bytes(module)
    compile_calls: list[bytes] = []
    runtime_calls: list[int] = []

    class Runtime:
        def __init__(self, module_bytes: bytes, **_limits: int) -> None:
            compile_calls.append(module_bytes)

        def call(self, export: str, input_bytes: bytes) -> tuple[bytes, int]:
            assert export == "answer"
            arguments = json.loads(input_bytes)
            runtime_calls.append(arguments["value"])
            return json.dumps({"answer": arguments["value"] + 1}).encode(), 23

    monkeypatch.setattr(desktop_service_module, "native_module", lambda: SimpleNamespace(WasmPlugin=Runtime))
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))
        assert imported["status"] == "ok", imported
        assert imported["result"]["wasm_tool_names"] == ["compute-demo.answer"]
        assert imported["result"]["auto_activated"] is False
        assert not compile_calls

        catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
        plugin = next(item for item in catalog["extensions"] if item["extension_id"] == "compute-demo")
        assert plugin["wasm_plugin"] is True
        assert plugin["tool_names"] == ["compute-demo.answer"]
        assert len(plugin["descriptor_hash"]) == 64
        assert not compile_calls

        denied = json.loads(service.dispatch(_request("extensions.set_state", {
            "kind": "extension",
            "id": "compute-demo",
            "descriptor_hash": plugin["descriptor_hash"],
            "enabled": True,
            "approved": False,
            "expected_revision": 0,
        })))
        assert denied["status"] == "error"
        assert denied["error"]["code"] == "WASM_PLUGIN_APPROVAL_REQUIRED"
        assert not compile_calls

        enabled = json.loads(service.dispatch(_request("extensions.set_state", {
            "kind": "extension",
            "id": "compute-demo",
            "descriptor_hash": plugin["descriptor_hash"],
            "enabled": True,
            "approved": True,
            "expected_revision": 0,
        })))
        assert enabled["status"] == "ok", enabled
        assert compile_calls == [module]
        active_state = json.loads(service.dispatch(_request("extensions.state")))["result"]
        active_record = next(
            item
            for item in active_state["records"]
            if item["kind"] == "extension" and item["id"] == "compute-demo"
        )
        assert active_record["approved"] is True
        assert active_record["enabled"] is True
        assert service._extension_registry.get_tool_spec("compute-demo.answer") is not None
        assert asyncio.run(service._extension_registry.invoke("compute-demo.answer", {"value": 41})) == {"answer": 42}
        assert runtime_calls == [41]

        disabled = json.loads(service.dispatch(_request("extensions.set_state", {
            "kind": "extension",
            "id": "compute-demo",
            "descriptor_hash": plugin["descriptor_hash"],
            "enabled": False,
            "approved": True,
            "expected_revision": enabled["result"]["revision"],
        })))
        assert disabled["status"] == "ok"
        assert service._extension_registry.get_tool_spec("compute-demo.answer") is None

        (workspace / ".aegis" / "extensions" / "compute-demo" / "plugin.wasm").write_bytes(
            module + b"\x00\x01\x00"
        )
        changed = json.loads(service.dispatch(_request("extensions.discover")))["result"]
        changed_plugin = next(item for item in changed["extensions"] if item["extension_id"] == "compute-demo")
        assert changed_plugin["descriptor_hash"] != plugin["descriptor_hash"]
        stale = json.loads(service.dispatch(_request("extensions.set_state", {
            "kind": "extension",
            "id": "compute-demo",
            "descriptor_hash": plugin["descriptor_hash"],
            "enabled": False,
            "approved": False,
            "expected_revision": disabled["result"]["revision"],
        })))
        assert stale["status"] == "error"
        assert stale["error"]["code"] == "CAPABILITY_CHANGED"

        changed_module = module + b"\x00\x01\x00"
        (workspace / ".aegis" / "extensions" / "compute-demo" / "plugin.wasm").write_bytes(changed_module)
        changed_plugin = next(
            item
            for item in json.loads(service.dispatch(_request("extensions.discover")))["result"]["extensions"]
            if item["extension_id"] == "compute-demo"
        )
        re_enabled = json.loads(service.dispatch(_request("extensions.set_state", {
            "kind": "extension",
            "id": "compute-demo",
            "descriptor_hash": changed_plugin["descriptor_hash"],
            "enabled": True,
            "approved": True,
            "expected_revision": disabled["result"]["revision"],
        })))
        assert re_enabled["status"] == "ok", re_enabled
        assert compile_calls == [module, changed_module]

        (workspace / ".aegis" / "extensions" / "compute-demo" / "plugin.wasm").write_bytes(
            module + b"\x00\x02\x00"
        )
        active_spec = service._extension_registry.get_tool_spec("compute-demo.answer")
        assert active_spec is not None
        stale_call = service._execute_provider_tool_batch(
            task="invoke the approved compute tool",
            conversation_id="conversation-1",
            execution_id="execution-1",
            requests=[
                {
                    "call_id": "stale-wasm-call",
                    "tool_name": active_spec.name,
                    "input": {"value": 41},
                    "effect_class": active_spec.effect_class,
                    "_aegis_expected_tool_descriptor_hash": active_spec.descriptor_hash,
                }
            ],
        )
        assert stale_call.outcomes[0].status == "REJECTED"
        assert runtime_calls == [41]
        assert service._extension_registry.get_tool_spec("compute-demo.answer") is None
        state_after_change = json.loads(service.dispatch(_request("extensions.state")))["result"]
        active_record = next(
            item
            for item in state_after_change["records"]
            if item["kind"] == "extension" and item["id"] == "compute-demo"
        )
        assert active_record["approved"] is False
        assert active_record["enabled"] is False
    finally:
        service.close()


def test_desktop_service_imports_bounded_zip_package_with_one_wrapper_directory(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "portable-package"
    archive = tmp_path / "portable-package.zip"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "hooks").mkdir()
    (source / "plugin.json").write_text(
        json.dumps({"name": "portable-zip", "extensions": {"com.openai": {"hooks": "./hooks/hooks.json"}}}),
        encoding="utf-8",
    )
    (source / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"type": "streamable-http", "url": "https://example.invalid/mcp"}}}),
        encoding="utf-8",
    )
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse primary sources.\n", encoding="utf-8"
    )
    (source / "hooks" / "hooks.json").write_text('{"hooks":[]}', encoding="utf-8")
    (source / "hooks" / "run.py").write_text("raise RuntimeError('must remain inert')\n", encoding="utf-8")
    _zip_folder(source, archive, wrapper="portable-package")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(archive)})))

    assert response["status"] == "ok", response
    assert response["result"]["extension_id"] == "portable-zip"
    assert response["result"]["skill_names"] == ["research"]
    assert response["result"]["mcp_server_ids"] == ["docs"]
    destination = workspace / ".aegis" / "extensions" / "portable-zip"
    assert (destination / "skills" / "research" / "SKILL.md").is_file()
    assert (destination / "hooks" / "hooks.json").is_file()
    assert (destination / "hooks" / "run.py").is_file()
    assert not (destination / "hooks" / "executed").exists()
    assert not (destination / "plugin.json").exists()
    assert not (destination / "mcp.json").exists()
    assert response["result"]["auto_activated"] is False
    service.close()


def test_desktop_service_rejects_zip_traversal_before_writing_outside_staging(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("../escaped.txt", "must not escape")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(archive)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (tmp_path / "escaped.txt").exists()
    assert not (workspace / ".aegis" / "extensions").exists()
    service.close()


def test_desktop_service_rejects_zip_link_entries(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    archive = tmp_path / "linked.zip"
    link = zipfile.ZipInfo("pack/skills/research/SKILL.md")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(link, "target")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(archive)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_LINK_BLOCKED"
    assert not (workspace / ".aegis" / "extensions").exists()
    service.close()


def test_desktop_service_rejects_cross_platform_zip_path_collisions(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    archive = tmp_path / "case-collision.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("pack/skills/Research/SKILL.md", "first")
        package.writestr("pack/skills/research/SKILL.md", "second")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(archive)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions").exists()
    service.close()


def test_desktop_service_rejects_zip_directory_prefix_case_collisions(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    archive = tmp_path / "directory-case-collision.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("Pack/skills/one/SKILL.md", "first")
        package.writestr("pack/skills/two/SKILL.md", "second")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(archive)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions").exists()
    service.close()


def test_desktop_service_rejects_zip_expansion_over_size_budget_before_extracting(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setattr(desktop_service_module, "MAX_SKILL_IMPORT_TOTAL_BYTES", 64)
    workspace = tmp_path / "project"
    archive = tmp_path / "oversized.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("payload.bin", b"x" * 65)

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(archive)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_LIMIT"
    assert not (tmp_path / "payload.bin").exists()
    assert not (workspace / ".aegis" / "extensions").exists()
    service.close()


def test_desktop_service_imports_portable_plugin_without_persisting_secrets_or_running_hooks(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "portable-plugin"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "skills" / "research" / "references").mkdir()
    (source / "hooks").mkdir()
    (source / "scripts").mkdir()
    (source / "assets").mkdir()
    (source / "plugin.json").write_text(
        json.dumps(
            {
                "name": "portable-research",
                "version": "1.2.0",
                "description": "Portable research workflow",
                "extensions": {"com.openai": {"hooks": ["../../outside/hooks.json"]}},
            }
        ),
        encoding="utf-8",
    )
    (source / "README.md").write_text("Package documentation stays inert.\n", encoding="utf-8")
    (source / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "type": "streamable-http",
                        "url": "https://example.invalid/mcp?token=SENSITIVE_VALUE",
                        "headers": {"Authorization": "Bearer SENSITIVE_VALUE"},
                        "env": {"API_TOKEN": "SENSITIVE_VALUE"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (source / ".app.json").write_text('{"apps":{"docs":"private-app-registration"}}', encoding="utf-8")
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse primary sources.\n", encoding="utf-8"
    )
    reference_bytes = b"Use direct sources and report uncertainty.\n"
    (source / "skills" / "research" / "references" / "research.md").write_bytes(reference_bytes)
    (source / "hooks" / "hooks.json").write_text('{"hooks":[]}', encoding="utf-8")
    hook_script = source / "hooks" / "run.py"
    hook_script.write_text("from pathlib import Path\nPath(__file__).with_name('executed').touch()\n", encoding="utf-8")
    (source / "scripts" / "setup.py").write_text("raise RuntimeError('must remain inert')\n", encoding="utf-8")
    (source / "assets" / "readme.txt").write_text("inert package asset\n", encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert imported["status"] == "ok", imported
    result = imported["result"]
    assert result["extension_id"] == "portable-research"
    assert result["skill_names"] == ["research"]
    assert result["mcp_server_ids"] == ["docs"]
    assert result["mcp_setup_suggestions"] == []  # query credentials are never copied into a draft
    assert "SENSITIVE_VALUE" not in json.dumps(result)
    destination = workspace / ".aegis" / "extensions" / "portable-research"
    assert (destination / "skills" / "research" / "SKILL.md").is_file()
    assert (destination / "skills" / "research" / "references" / "research.md").is_file()
    assert (destination / "hooks" / "hooks.json").is_file()
    assert (destination / "hooks" / "run.py").is_file()
    assert (destination / "scripts" / "setup.py").is_file()
    assert (destination / "assets" / "readme.txt").is_file()
    assert (destination / "README.md").is_file()
    assert not (destination / "plugin.json").exists()
    assert not (destination / "mcp.json").exists()
    assert not (destination / ".app.json").exists()
    assert not (destination / "hooks" / "executed").exists()
    assert not (destination / "scripts" / "executed").exists()
    stored_content = "\n".join(path.read_text(encoding="utf-8") for path in destination.rglob("*") if path.is_file())
    assert "SENSITIVE_VALUE" not in stored_content
    assert "private-app-registration" not in stored_content
    mcp_metadata = tomllib.loads((destination / "mcp" / "docs" / "mcp.toml").read_text(encoding="utf-8"))
    assert mcp_metadata["mcp"]["transport"] == "streamable-http"
    assert mcp_metadata["mcp"]["environment_variables"] == [
        {"name": "API_TOKEN", "is_required": True, "is_secret": True}
    ]
    catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
    server = next(item for item in catalog["mcp_servers"] if item["server_id"] == "docs")
    assert server["approved"] is False
    state = json.loads(service.dispatch(_request("extensions.state")))["result"]
    assert not any(
        item["kind"] == "skill" and item["id"] == "research" and item["enabled"] for item in state["records"]
    )
    assert not any(
        item["kind"] == "mcp" and item["id"] == "docs" and (item["approved"] or item["enabled"])
        for item in state["records"]
    )
    research_skill = next(item for item in catalog["skills"] if item["name"] == "research")
    enabled = json.loads(
        service.dispatch(
            _request(
                "extensions.set_state",
                {
                    "kind": "skill",
                    "id": "research",
                    "descriptor_hash": research_skill["content_hash"],
                    "enabled": True,
                    "approved": False,
                    "expected_revision": state["revision"],
                },
            )
        )
    )
    assert enabled["status"] == "ok"
    resource_tool = service._extension_registry.get_tool_spec(desktop_service_module.PROVIDER_SKILL_RESOURCE_TOOL_NAME)
    assert resource_tool is not None
    resource_result = asyncio.run(
        service._extension_registry.invoke(
            resource_tool.name,
            {
                "skill": "research",
                "path": "references/research.md",
                "start_line": 1,
                "max_lines": 80,
            },
            expected_effect_class="read_only",
            expected_descriptor_hash=resource_tool.descriptor_hash,
        )
    )
    assert resource_result["content"] == "Use direct sources and report uncertainty."
    assert resource_result["sha256"] == extensions_module._sha256_bytes(reference_bytes)
    service.close()


def test_desktop_service_imports_legacy_plugin_manifest_and_strips_stdio_settings(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "legacy-plugin"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / "skills" / "local-search").mkdir(parents=True)
    (source / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "legacy-search",
                "version": "0.4.0",
                "description": "Compatibility package",
                "skills": "./skills/",
                "mcpServers": "./.mcp.json",
            }
        ),
        encoding="utf-8",
    )
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local-search": {
                        "command": "DO_NOT_RUN",
                        "args": ["--token", "SENSITIVE_VALUE"],
                        "env": {"SEARCH_TOKEN": "SENSITIVE_VALUE"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "skills" / "local-search" / "SKILL.md").write_text(
        "---\nname: local-search\ndescription: Search local sources\n---\n\nSearch safely.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert imported["status"] == "ok", imported
    result = imported["result"]
    assert result["extension_id"] == "legacy-search"
    assert result["mcp_server_ids"] == ["local-search"]
    assert result["mcp_setup_suggestions"] == []  # obvious token arguments are not surfaced to the renderer
    assert "SENSITIVE_VALUE" not in json.dumps(result)
    destination = workspace / ".aegis" / "extensions" / "legacy-search"
    assert not (destination / ".codex-plugin").exists()
    assert not (destination / ".mcp.json").exists()
    normalized = (destination / "mcp" / "local-search" / "mcp.toml").read_text(encoding="utf-8")
    assert 'transport = "stdio"' in normalized
    assert "DO_NOT_RUN" not in normalized
    assert "SENSITIVE_VALUE" not in normalized
    assert "SEARCH_TOKEN" in normalized
    catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
    server = next(item for item in catalog["mcp_servers"] if item["server_id"] == "local-search")
    assert server["approved"] is False
    service.close()


def test_plugin_identity_cannot_claim_the_reserved_mcp_namespace() -> None:
    with pytest.raises(DesktopServiceError, match="reserved MCP namespace"):
        desktop_service_module._plugin_identity({"name": "mcp.docs"})


def test_extension_pack_import_cannot_exceed_discovery_capacity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setattr(desktop_service_module, "MAX_EXTENSION_FILES", 4)
    workspace = tmp_path / "project"
    extensions_root = workspace / ".aegis" / "extensions"
    for index in range(4):
        extension = extensions_root / f"existing-{index}"
        extension.mkdir(parents=True)
        (extension / "extension.toml").write_text(
            f'[extension]\nid = "existing-{index}"\nversion = "1"\n'
            'description = "Existing metadata"\n',
            encoding="utf-8",
        )
    source = tmp_path / "new-pack"
    skill = source / "skills" / "research"
    skill.mkdir(parents=True)
    (source / "plugin.json").write_text('{"name":"new-pack"}', encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research the topic\n---\n\nUse source evidence.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))
        assert response["status"] == "error"
        assert response["error"]["code"] == "EXTENSION_PACK_LIMIT"
        assert not (extensions_root / "new-pack").exists()
        assert json.loads(service.dispatch(_request("extensions.discover")))["status"] == "ok"
    finally:
        service.close()


def test_skill_import_cannot_exceed_discovery_capacity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setattr(desktop_service_module, "MAX_EXTENSION_FILES", 4)
    workspace = tmp_path / "project"
    skills_root = workspace / ".aegis" / "skills"
    for index in range(4):
        skill = skills_root / f"existing-{index}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: existing-{index}\ndescription: Existing skill\n---\n\nRead-only guidance.\n",
            encoding="utf-8",
        )
    source = tmp_path / "new-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: new-skill\ndescription: New skill\n---\n\nRead-only guidance.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
        assert response["status"] == "error"
        assert response["error"]["code"] == "SKILL_IMPORT_LIMIT"
        assert not (skills_root / "new-skill").exists()
        assert json.loads(service.dispatch(_request("extensions.discover")))["status"] == "ok"
    finally:
        service.close()


def test_desktop_service_rejects_hard_linked_plugin_files(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "hardlinked-plugin"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "plugin.json").write_text('{"name":"hardlinked-package"}', encoding="utf-8")
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse primary sources.\n", encoding="utf-8"
    )
    outside_file = tmp_path / "outside-data.txt"
    outside_file.write_text("data outside the selected package\n", encoding="utf-8")
    hard_link = source / "scripts" / "copied-data.txt"
    os.link(outside_file, hard_link)
    assert hard_link.stat().st_nlink > 1

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert imported["status"] == "error"
        assert imported["error"]["code"] == "EXTENSION_PACK_LINK_BLOCKED"
        assert not (workspace / ".aegis" / "extensions" / "hardlinked-package").exists()
    finally:
        service.close()


def test_desktop_service_rejects_hard_linked_plugin_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "hardlinked-metadata-plugin"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse primary sources.\n", encoding="utf-8"
    )
    outside_file = tmp_path / "outside-plugin.json"
    outside_file.write_text('{"name":"hardlinked-metadata"}', encoding="utf-8")
    manifest = source / "plugin.json"
    os.link(outside_file, manifest)
    assert manifest.stat().st_nlink > 1

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert imported["status"] == "error"
        assert imported["error"]["code"] == "EXTENSION_PACK_LINK_BLOCKED"
        assert not (workspace / ".aegis" / "extensions" / "hardlinked-metadata").exists()
    finally:
        service.close()


def test_desktop_service_imports_declared_agent_plugins_v1_package_as_inactive_data(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "agent-plugin"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "agent-plugin-v1",
            }
        ),
        encoding="utf-8",
    )
    (source / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {
                    "docs": {
                        "type": "streamable-http",
                        "url": "https://example.invalid/mcp",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse primary sources.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert imported["status"] == "ok", imported
        assert imported["result"]["extension_id"] == "agent-plugin-v1"
        assert imported["result"]["skill_names"] == ["research"]
        assert imported["result"]["mcp_server_ids"] == ["docs"]
        assert imported["result"]["auto_activated"] is False
        destination = workspace / ".aegis" / "extensions" / "agent-plugin-v1"
        assert (destination / "skills" / "research" / "SKILL.md").is_file()
        assert not (destination / "plugin.json").exists()
        assert not (destination / "mcp.json").exists()
        catalog = json.loads(service.dispatch(_request("extensions.discover")))
        assert catalog["status"] == "ok"
        server = next(item for item in catalog["result"]["mcp_servers"] if item["server_id"] == "docs")
        assert server["approved"] is False
    finally:
        service.close()


def test_desktop_service_rejects_unsupported_declared_agent_plugin_schema(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "future-plugin"
    source.mkdir()
    (source / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.1.0/plugin.schema.json",
                "name": "future-plugin",
            }
        ),
        encoding="utf-8",
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert imported["status"] == "error"
        assert imported["error"]["code"] == "EXTENSION_PACK_INVALID"
        assert not (workspace / ".aegis" / "extensions" / "future-plugin").exists()
    finally:
        service.close()


def test_desktop_service_rejects_mismatched_declared_agent_plugin_mcp_schema(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "mismatched-plugin"
    source.mkdir()
    (source / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "mismatched-plugin",
            }
        ),
        encoding="utf-8",
    )
    (source / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert imported["status"] == "error"
        assert imported["error"]["code"] == "EXTENSION_PACK_INVALID"
        assert not (workspace / ".aegis" / "extensions" / "mismatched-plugin").exists()
    finally:
        service.close()


def test_desktop_service_imports_claude_plugin_skills_and_mcp_as_inactive_data(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "claude-plugin"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / "skills" / "source-review").mkdir(parents=True)
    (source / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "claude-review-pack",
                "version": "1.2.0",
                "description": "Review skills with an MCP source",
            }
        ),
        encoding="utf-8",
    )
    (source / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "review-source": {
                        "command": "DO_NOT_RUN",
                        "args": ["--token", "SENSITIVE_VALUE"],
                        "env": {"REVIEW_TOKEN": "SENSITIVE_VALUE"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "skills" / "source-review" / "SKILL.md").write_text(
        "---\n"
        "name: source-review\n"
        "description: Review source evidence\n"
        "---\n\nUse verified sources.\n",
        encoding="utf-8",
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert imported["status"] == "ok", imported
        result = imported["result"]
        assert result["extension_id"] == "claude-review-pack"
        assert result["mcp_server_ids"] == ["review-source"]
        assert result["mcp_setup_suggestions"] == []
        assert "SENSITIVE_VALUE" not in json.dumps(result)

        destination = workspace / ".aegis" / "extensions" / "claude-review-pack"
        assert not (destination / ".claude-plugin").exists()
        assert not (destination / ".mcp.json").exists()
        normalized = (destination / "mcp" / "review-source" / "mcp.toml").read_text(encoding="utf-8")
        assert 'transport = "stdio"' in normalized
        assert "DO_NOT_RUN" not in normalized
        assert "SENSITIVE_VALUE" not in normalized
        assert "REVIEW_TOKEN" in normalized

        catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
        server = next(item for item in catalog["mcp_servers"] if item["server_id"] == "review-source")
        assert any(item["name"] == "source-review" for item in catalog["skills"])
        assert server["approved"] is False
        state = json.loads(service.dispatch(_request("extensions.state")))["result"]["records"]
        assert not any(
            item["kind"] == "skill" and item["id"] == "source-review" and item["enabled"] for item in state
        )
    finally:
        service.close()


def test_desktop_service_rejects_ambiguous_plugin_manifest_formats(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "ambiguous-plugin"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / "plugin.json").write_text('{"name":"portable-plugin"}', encoding="utf-8")
    (source / ".claude-plugin" / "plugin.json").write_text('{"name":"claude-plugin"}', encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

        assert response["status"] == "error"
        assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
        assert not (workspace / ".aegis" / "extensions").exists()
    finally:
        service.close()


def test_desktop_service_returns_safe_session_only_mcp_setup_suggestions(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "mcp-plugin"
    source.mkdir()
    (source / "plugin.json").write_text('{"name":"mcp-setup-suggestions"}', encoding="utf-8")
    (source / "scripts").mkdir()
    (source / "scripts" / "server.py").write_text("# packaged MCP server entry point\n", encoding="utf-8")
    (source / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local-docs": {
                        "type": "stdio",
                        "command": "python",
                        "args": ["scripts/server.py"],
                        "env": {"DOCS_TOKEN": "DO_NOT_RETURN"},
                    },
                    "remote-docs": {
                        "type": "streamable-http",
                        "url": "https://docs.example.test/mcp",
                        "headers": {"Authorization": "Bearer DO_NOT_RETURN"},
                    },
                    "credential-url": {
                        "type": "streamable-http",
                        "url": "https://user:DO_NOT_RETURN@private.example.test/mcp?token=DO_NOT_RETURN",
                    },
                    "credential-argument": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--token", "DO_NOT_RETURN"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
        imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))
        assert imported["status"] == "ok", imported
        result = imported["result"]
        assert result["mcp_server_ids"] == ["credential-argument", "credential-url", "local-docs", "remote-docs"]
        assert result["mcp_setup_suggestions"] == [
            {
                "server_id": "local-docs",
                "transport": "stdio",
                "command": ["python", "scripts/server.py"],
                "cwd": str(workspace / ".aegis" / "extensions" / "mcp-setup-suggestions"),
            },
            {
                "server_id": "remote-docs",
                "transport": "streamable-http",
                "endpoint": "https://docs.example.test/mcp",
                "allowed_host": "docs.example.test",
            },
        ]
        assert "DO_NOT_RETURN" not in json.dumps(result)
        catalog = json.loads(service.dispatch(_request("extensions.discover")))['result']
        assert all(not item["approved"] for item in catalog["mcp_servers"])
        assert all(not item["enabled"] for item in json.loads(service.dispatch(_request("extensions.state")))['result']["records"])
    finally:
        service.close()


def test_desktop_service_imports_a_skills_only_portable_plugin(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "skills-only-plugin"
    (source / "skills" / "summarize").mkdir(parents=True)
    (source / "plugin.json").write_text('{"name":"summarize-package"}', encoding="utf-8")
    (source / "skills" / "summarize" / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize material\n---\n\nBe concise.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    imported = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert imported["status"] == "ok", imported
    assert imported["result"]["extension_id"] == "summarize-package"
    assert imported["result"]["skill_names"] == ["summarize"]
    assert imported["result"]["mcp_server_ids"] == []
    catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
    assert any(item["name"] == "summarize" for item in catalog["skills"])
    assert catalog["mcp_servers"] == []
    service.close()


def test_desktop_service_rejects_unsupported_plugin_mcp_transport_atomically(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "unsupported-plugin"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "plugin.json").write_text('{"name":"unsupported-plugin"}', encoding="utf-8")
    (source / "mcp.json").write_text(
        '{"mcpServers":{"unsupported":{"type":"sse","url":"https://example.invalid/mcp"}}}',
        encoding="utf-8",
    )
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse sources.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions" / "unsupported-plugin").exists()
    assert not tuple(workspace.glob(".aegis-extension-pack-import-*"))
    service.close()


def test_desktop_service_rejects_duplicate_plugin_manifest_keys(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "ambiguous-plugin"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "plugin.json").write_text('{"name":"first","name":"second"}', encoding="utf-8")
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse sources.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions" / "first").exists()
    assert not tuple(workspace.glob(".aegis-extension-pack-import-*"))
    service.close()


def test_desktop_service_rejects_legacy_plugin_paths_outside_supported_roots(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "traversing-plugin"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / "skills" / "research").mkdir(parents=True)
    (source / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"traversing-plugin","skills":"../../outside-skills"}', encoding="utf-8"
    )
    (source / "skills" / "research" / "SKILL.md").write_text(
        "---\nname: research\ndescription: Gather evidence\n---\n\nUse sources.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions" / "traversing-plugin").exists()
    assert not tuple(workspace.glob(".aegis-extension-pack-import-*"))
    service.close()


def test_desktop_service_does_not_overwrite_existing_capability_pack(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    destination = workspace / ".aegis" / "extensions" / "research-pack"
    destination.mkdir(parents=True)
    original = destination / "extension.toml"
    original.write_text("keep existing pack\n", encoding="utf-8")
    source = tmp_path / "replacement-pack"
    _create_capability_pack(source)

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_ALREADY_EXISTS"
    assert original.read_text(encoding="utf-8") == "keep existing pack\n"
    service.close()


def test_desktop_service_rejects_linked_capability_pack_files(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "linked-pack"
    _create_capability_pack(source)
    linked_file = source / "skills" / "research" / "outside.md"
    linked_file.write_text("simulated linked file\n", encoding="utf-8")
    path_type = type(source)
    is_symlink = path_type.is_symlink
    monkeypatch.setattr(path_type, "is_symlink", lambda path: path == linked_file or is_symlink(path))

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_LINK_BLOCKED"
    assert not (workspace / ".aegis" / "extensions" / "research-pack").exists()
    service.close()


def test_desktop_service_rejects_capability_pack_tool_claims(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "tool-claim-pack"
    _create_capability_pack(source)
    manifest = source / "extension.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + 'tools = ["unavailable.handler"]\n', encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions" / "research-pack").exists()
    assert not tuple(workspace.glob(".aegis-extension-pack-import-*"))
    service.close()


def test_desktop_service_rejects_executable_mcp_fields_in_capability_pack(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "executable-mcp-pack"
    _create_capability_pack(source)
    mcp_metadata = source / "mcp" / "research-search" / "mcp.toml"
    mcp_metadata.write_text(
        mcp_metadata.read_text(encoding="utf-8") + 'command = "python"\n',
        encoding="utf-8",
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_pack", {"source_path": str(source)})))

    assert response["status"] == "error"
    assert response["error"]["code"] == "EXTENSION_PACK_INVALID"
    assert not (workspace / ".aegis" / "extensions" / "research-pack").exists()
    assert not tuple(workspace.glob(".aegis-extension-pack-import-*"))
    service.close()


def test_desktop_service_does_not_overwrite_an_existing_skill_on_import(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    existing = workspace / ".agents" / "skills" / "research" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    original_body = "---\nname: research\ndescription: Existing skill\n---\n\nKeep this version.\n"
    existing.write_text(original_body, encoding="utf-8")
    source = tmp_path / "replacement"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: research\ndescription: Replacement skill\n---\n\nDo not overwrite.\n", encoding="utf-8"
    )

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
    assert response["status"] == "error"
    assert response["error"]["code"] == "SKILL_ALREADY_EXISTS"
    assert existing.read_text(encoding="utf-8") == original_body
    assert not (workspace / ".aegis" / "skills" / "research").exists()
    service.close()


def test_desktop_service_rejects_linked_skill_resources(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "linked-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: linked\ndescription: Linked\n---\n\nNo links.\n", encoding="utf-8")
    linked_resource = source / "outside.txt"
    linked_resource.write_text("simulated linked resource\n", encoding="utf-8")
    path_type = type(source)
    is_symlink = path_type.is_symlink
    monkeypatch.setattr(path_type, "is_symlink", lambda path: path == linked_resource or is_symlink(path))

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
    assert response["status"] == "error"
    assert response["error"]["code"] == "SKILL_IMPORT_LINK_BLOCKED"
    assert not (workspace / ".aegis" / "skills" / "linked").exists()
    service.close()


def test_desktop_service_rejects_hard_linked_skill_resources(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "hardlinked-skill"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: linked\ndescription: Linked\n---\n\nNo external files.\n", encoding="utf-8"
    )
    outside_file = tmp_path / "outside-reference.txt"
    outside_file.write_text("data outside the selected skill\n", encoding="utf-8")
    hard_link = source / "references" / "copied-data.txt"
    os.link(outside_file, hard_link)
    assert hard_link.stat().st_nlink > 1

    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))

        assert response["status"] == "error"
        assert response["error"]["code"] == "SKILL_IMPORT_LINK_BLOCKED"
        assert not (workspace / ".aegis" / "skills" / "linked").exists()
    finally:
        service.close()


def test_desktop_service_enforces_skill_import_body_limit(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "oversized-skill"
    source.mkdir()
    body = "---\nname: oversized\ndescription: Large\n---\n\n" + "x" * (512 * 1024)
    (source / "SKILL.md").write_text(body, encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
    assert response["status"] == "error"
    assert response["error"]["code"] == "SKILL_IMPORT_LIMIT"
    assert not (workspace / ".aegis" / "skills" / "oversized").exists()
    service.close()


def test_desktop_service_rejects_nested_skill_definitions_on_import(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "nested-skills"
    nested = source / "references" / "child"
    nested.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: parent\ndescription: Parent\n---\n\nParent skill.\n", encoding="utf-8")
    (nested / "SKILL.md").write_text("---\nname: child\ndescription: Child\n---\n\nNested skill.\n", encoding="utf-8")

    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
    assert response["status"] == "error"
    assert response["error"]["code"] == "SKILL_IMPORT_INVALID"
    assert not (workspace / ".aegis" / "skills" / "parent").exists()
    service.close()


def test_desktop_service_requires_an_open_project_before_skill_import(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")

    response = json.loads(
        service.dispatch(_request("extensions.import_skill", {"source_path": str(tmp_path / "not-read")}))
    )
    assert response["status"] == "error"
    assert response["error"]["code"] == "WORKSPACE_NOT_OPEN"
    service.close()


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("research_helper", "Valid length description"),
        ("research", "x" * 1025),
    ],
)
def test_desktop_service_rejects_nonconforming_skill_metadata_on_import(
    tmp_path: Path, monkeypatch, name: str, description: str
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    workspace = tmp_path / "project"
    source = tmp_path / "nonstandard-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nSkill body.\n", encoding="utf-8"
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    assert (
        json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))["status"] == "ok"
    )
    response = json.loads(service.dispatch(_request("extensions.import_skill", {"source_path": str(source)})))
    assert response["status"] == "error"
    assert response["error"]["code"] == "SKILL_IMPORT_INVALID"
    service.close()


def test_desktop_cache_identity_excludes_query_specific_workspace_context(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    root = tmp_path / "project"
    assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
    snapshot = _FakeSnapshot(_FakeConversation("cache", "local-profile", "Cache", "local", "test-model"))
    source_snapshot = {
        "revision": "source-v1",
        "files": [{"relative_path": f"src/{index:02d}-module.py"} for index in range(65)]
        + [
            {"relative_path": "src/alpha-search.py"},
            {"relative_path": "src/beta-lookup.py"},
        ],
    }
    try:
        first = service._render_provider_prompt(
            snapshot,
            "alpha",
            source_snapshot,
            SimpleNamespace(rendered="memory result for alpha"),
        )
        second = service._render_provider_prompt(
            snapshot,
            "beta",
            source_snapshot,
            SimpleNamespace(rendered="memory result for beta"),
        )
        first_prompt, first_stable, first_dynamic = first
        second_prompt, second_stable, second_dynamic = second
        first_plan = CachePromptPlan.from_parts(
            provider="openai",
            model="gpt-5.6-sol",
            namespace="local-profile",
            stable_prefix=first_stable,
            dynamic_suffix=first_dynamic,
        )
        second_plan = CachePromptPlan.from_parts(
            provider="openai",
            model="gpt-5.6-sol",
            namespace="local-profile",
            stable_prefix=second_stable,
            dynamic_suffix=second_dynamic,
        )
        assert first_stable == second_stable
        assert first_plan.cache_key == second_plan.cache_key
        assert first_dynamic != second_dynamic
        assert "alpha-search.py" in first_prompt and "alpha-search.py" not in second_prompt
        assert "[CURRENT USER MESSAGE]\nbeta" in second_prompt

        long_history = _FakeSnapshot(
            snapshot.conversation,
            turns=tuple(
                _FakeTurn(
                    "cache",
                    f"turn-{index}",
                    "user",
                    "COMPLETED",
                    f"turn-{index}-" + "x" * 8_192,
                    index + 1,
                )
                for index in range(32)
            ),
        )
        bounded_prompt, _, _ = service._render_provider_prompt(
            long_history,
            "keep this current request intact",
            source_snapshot,
            SimpleNamespace(rendered="bounded memory"),
        )
        assert len(bounded_prompt) <= MAX_PROMPT_LENGTH
        assert bounded_prompt.endswith("[CURRENT USER MESSAGE]\nkeep this current request intact")
        assert "turn-31-" in bounded_prompt
        assert "turn-0-" not in bounded_prompt
        with pytest.raises(DesktopServiceError, match="exceed the prompt limit"):
            service._render_provider_prompt(
                snapshot,
                "x" * MAX_PROMPT_LENGTH,
                source_snapshot,
                SimpleNamespace(rendered="memory"),
            )
    finally:
        service.close()


def test_desktop_service_adds_mcp_metadata_without_starting_transport(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
        environment_variables = [
            {
                "name": "API_TOKEN",
                "is_required": True,
                "is_secret": True,
                "description": "Service access token",
                "value": "secret-value-must-not-persist",
                "default": "secret-default-must-not-persist",
            },
            {"name": "REGION", "is_required": False, "is_secret": False, "description": "Service region"},
        ]
        response = json.loads(
            service.dispatch(
                _request(
                    "extensions.add_mcp_metadata",
                    {
                        "server_id": "local-tools",
                        "description": "Local tools",
                        "transport": "stdio",
                        "environment_variables": environment_variables,
                    },
                )
            )
        )
        assert response["status"] == "ok"
        metadata_path = root / ".aegis" / "mcp" / "local-tools" / "mcp.toml"
        assert metadata_path.is_file()
        raw_metadata = metadata_path.read_text(encoding="utf-8")
        parsed_metadata = tomllib.loads(raw_metadata)
        expected_environment_variables = [
            {"name": "API_TOKEN", "is_required": True, "is_secret": True, "description": "Service access token"},
            {"name": "REGION", "is_required": False, "is_secret": False, "description": "Service region"},
        ]
        assert parsed_metadata["mcp"]["environment_variables"] == expected_environment_variables
        assert "secret-value-must-not-persist" not in raw_metadata
        assert "secret-default-must-not-persist" not in raw_metadata
        assert response["result"]["activated"] is False

        discovered = json.loads(service.dispatch(_request("extensions.discover")))
        assert discovered["status"] == "ok"
        assert discovered["result"]["mcp_servers"][0]["environment_variables"] == expected_environment_variables

        invalid = json.loads(
            service.dispatch(
                _request(
                    "extensions.add_mcp_metadata",
                    {
                        "server_id": "invalid-tools",
                        "description": "Invalid metadata",
                        "transport": "stdio",
                        "environment_variables": [
                            {"name": "API_TOKEN", "is_required": True, "is_secret": True},
                            {"name": "api_token", "is_required": True, "is_secret": True},
                        ],
                    },
                )
            )
        )
        assert invalid["status"] == "error"
        assert invalid["error"]["code"] == "INVALID_ARGUMENT"
        assert not (root / ".aegis" / "mcp" / "invalid-tools").exists()

        duplicate = json.loads(
            service.dispatch(
                _request(
                    "extensions.add_mcp_metadata",
                    {"server_id": "local-tools", "description": "Duplicate", "transport": "stdio"},
                )
            )
        )
        assert duplicate["status"] == "error"
        assert duplicate["error"]["code"] == "MCP_METADATA_EXISTS"
    finally:
        service.close()


def test_desktop_service_activates_and_deactivates_explicit_mcp_stdio(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    (root / ".aegis" / "mcp" / "child").mkdir(parents=True)
    (root / ".aegis" / "mcp" / "child" / "mcp.toml").write_text(
        '[mcp]\nid = "child"\ndescription = "Local child"\ntransport = "stdio"\n', encoding="utf-8"
    )
    server_code = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); method=request.get('method')\n"
        " if method=='server/discover': print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'unknown method'}}), flush=True); continue\n"
        " elif method=='initialize': result={'protocolVersion':'2025-11-25','capabilities':{},'serverInfo':{'name':'test','version':'1'}}\n"
        " elif method=='tools/list': result={'tools':[{'name':'echo','description':'Echo','inputSchema':{'type':'object'}}]}\n"
        " elif method=='tools/call': result={'content':[{'type':'text','text':request['params']['arguments']['value'] }]}\n"
        " else: continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)\n"
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
    catalog = json.loads(service.dispatch(_request("extensions.discover")))["result"]
    server = catalog["mcp_servers"][0]
    approved = json.loads(
        service.dispatch(
            _request(
                "extensions.set_state",
                {
                    "kind": "mcp",
                    "id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "enabled": False,
                    "approved": True,
                    "expected_revision": 0,
                },
            )
        )
    )
    assert approved["status"] == "ok"
    mcp_config = {"command": [sys.executable, "-c", server_code]}
    test_token = _tested_mcp_config_token(service, "child", server["descriptor_hash"], 1, mcp_config)
    activated = json.loads(
        service.dispatch(
            _request(
                "extensions.activate_mcp",
                {
                    "server_id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "approved": True,
                    "expected_revision": 1,
                    "config": mcp_config,
                    "test_token": test_token,
                },
            )
        )
    )
    assert activated["status"] == "ok"
    assert activated["result"]["lifecycle"] == "ACTIVE"
    assert activated["result"]["tools"][0]["name"] == "mcp.child.echo"
    tool_result = asyncio.run(service._extension_registry.invoke("mcp.child.echo", {"value": "works"}))
    assert tool_result["content"][0]["text"] == "works"
    disabled = json.loads(
        service.dispatch(
            _request(
                "extensions.set_state",
                {
                    "kind": "mcp",
                    "id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "enabled": False,
                    "approved": True,
                    "expected_revision": activated["result"]["state"]["revision"],
                },
            )
        )
    )
    assert disabled["status"] == "ok"
    disabled_record = next(item for item in disabled["result"]["records"] if item["id"] == "child")
    assert disabled_record["enabled"] is False
    assert service._mcp_runtime is not None
    assert service._mcp_runtime.active_server_ids() == frozenset()
    assert all(spec.name != "mcp.child.echo" for spec in service._extension_registry.tools())
    test_token = _tested_mcp_config_token(
        service, "child", server["descriptor_hash"], disabled["result"]["revision"], mcp_config
    )
    activated = json.loads(
        service.dispatch(
            _request(
                "extensions.activate_mcp",
                {
                    "server_id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "approved": True,
                    "expected_revision": disabled["result"]["revision"],
                    "config": mcp_config,
                    "test_token": test_token,
                },
            )
        )
    )
    assert activated["status"] == "ok"
    active_state = json.loads(service.dispatch(_request("extensions.state")))["result"]
    active_record = next(item for item in active_state["records"] if item["kind"] == "mcp" and item["id"] == "child")
    assert active_record["enabled"] is True
    stale_deactivation = json.loads(
        service.dispatch(
            _request(
                "extensions.deactivate_mcp",
                {
                    "server_id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "expected_revision": 2,
                },
            )
        )
    )
    assert stale_deactivation["status"] == "error"
    assert stale_deactivation["error"]["code"] == "CAPABILITY_STATE_CONFLICT"
    assert service._mcp_runtime is not None
    assert service._mcp_runtime.active_server_ids() == frozenset({"child"})
    assert [spec.name for spec in service._extension_registry.tools()] == [
        desktop_service_module.PROVIDER_CODE_COPY_TOOL_NAME,
        desktop_service_module.PROVIDER_CODE_READ_TOOL_NAME,
        desktop_service_module.PROVIDER_CODE_SEARCH_TOOL_NAME,
        desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME,
        desktop_service_module.PROVIDER_SKILL_RESOURCE_TOOL_NAME,
        desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME,
        desktop_service_module.PROVIDER_SUBAGENT_TOOL_NAME,
        desktop_service_module.PROVIDER_TOOL_DISCOVERY_NAME,
        "mcp.child.echo",
    ]
    deactivated = json.loads(
        service.dispatch(
            _request(
                "extensions.deactivate_mcp",
                {
                    "server_id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "expected_revision": activated["result"]["state"]["revision"],
                },
            )
        )
    )
    assert deactivated["status"] == "ok"
    assert deactivated["result"]["lifecycle"] == "INACTIVE"
    test_token = _tested_mcp_config_token(
        service, "child", server["descriptor_hash"], deactivated["result"]["state"]["revision"], mcp_config
    )
    restarted_activation = json.loads(
        service.dispatch(
            _request(
                "extensions.activate_mcp",
                {
                    "server_id": "child",
                    "descriptor_hash": server["descriptor_hash"],
                    "approved": True,
                    "expected_revision": deactivated["result"]["state"]["revision"],
                    "config": mcp_config,
                    "test_token": test_token,
                },
            )
        )
    )
    assert restarted_activation["status"] == "ok"
    assert restarted_activation["result"]["lifecycle"] == "ACTIVE"
    service.close()

    restarted = DesktopService(profile_root=tmp_path / "profile")
    assert json.loads(restarted.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
    restarted_state = json.loads(restarted.dispatch(_request("extensions.state")))["result"]
    restarted_record = next(
        item for item in restarted_state["records"] if item["kind"] == "mcp" and item["id"] == "child"
    )
    assert restarted_record["enabled"] is False
    assert restarted_record["approved"] is True
    restarted.close()


def test_deactivating_mcp_preserves_approval_when_an_extension_has_the_same_id(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    metadata = root / ".aegis" / "mcp" / "shared" / "mcp.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '[mcp]\nid = "shared"\ndescription = "Shared id"\ntransport = "stdio"\n', encoding="utf-8"
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
        server = json.loads(service.dispatch(_request("extensions.discover")))["result"]["mcp_servers"][0]
        store = service._capability_state_store()
        state = store.set(
            kind="extension",
            capability_id="shared",
            descriptor_hash="a" * 64,
            enabled=False,
            approved=False,
            expected_revision=0,
        )
        state = store.set(
            kind="mcp",
            capability_id="shared",
            descriptor_hash=server["descriptor_hash"],
            enabled=False,
            approved=True,
            expected_revision=state["revision"],
        )

        deactivated = json.loads(
            service.dispatch(
                _request(
                    "extensions.deactivate_mcp",
                    {
                        "server_id": "shared",
                        "descriptor_hash": server["descriptor_hash"],
                        "expected_revision": state["revision"],
                    },
                )
            )
        )

        assert deactivated["status"] == "ok"
        mcp_record = next(
            item for item in deactivated["result"]["state"]["records"] if item["kind"] == "mcp"
        )
        assert mcp_record["enabled"] is False
        assert mcp_record["approved"] is True
    finally:
        service.close()


def test_mcp_tool_timeout_cancels_the_cross_loop_invocation():
    runtime = _McpRuntime()
    caller_registry = ExtensionRegistry()
    tool_started = Event()
    tool_cancelled = Event()
    inner_name = "mcp.child.slow"

    async def slow_tool(_arguments):
        tool_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            tool_cancelled.set()
            raise

    inner_spec = ToolSpec(
        name=inner_name,
        description="Waits until cancelled",
        timeout_seconds=30,
        extension_id="mcp.child",
    )
    runtime.registry.register(
        ExtensionManifest(
            extension_id="mcp.child",
            version="test",
            description="Test MCP provider",
            capabilities=("mcp",),
            tool_names=(inner_name,),
        ),
        ((inner_spec, slow_tool),),
    )

    async def bridge(arguments):
        owned, result = await runtime.invoke_if_registered_async(
            inner_name,
            arguments,
            expected_effect_class=inner_spec.effect_class,
            expected_descriptor_hash=inner_spec.descriptor_hash,
        )
        assert owned
        return result

    caller_registry.register(
        ExtensionManifest(
            extension_id="desktop",
            version="test",
            description="Desktop MCP bridge",
            tool_names=("desktop.slow",),
        ),
        (
            (
                ToolSpec(
                    name="desktop.slow",
                    description="Calls an MCP tool",
                    timeout_seconds=0.2,
                    extension_id="desktop",
                ),
                bridge,
            ),
        ),
    )

    try:
        with pytest.raises(TimeoutError):
            asyncio.run(caller_registry.invoke("desktop.slow", {}))
        assert tool_started.wait(1)
        assert tool_cancelled.wait(1)
    finally:
        runtime.close()


def test_mcp_runtime_keeps_provider_serial_after_cross_loop_cancellation():
    descriptors = (
        McpToolDescriptor("first", "First call", timeout_seconds=1),
        McpToolDescriptor("second", "Second call", timeout_seconds=1),
    )

    class Provider:
        def __init__(self):
            self.active = 0
            self.maximum = 0
            self.guard = Lock()
            self.release_first = Event()
            self.started = {"first": Event(), "second": Event()}

        async def list_tools(self):
            return descriptors

        def blocking_call(self, name):
            with self.guard:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            self.started[name].set()
            if name == "first":
                self.release_first.wait(timeout=2)
            with self.guard:
                self.active -= 1
            return {"name": name}

        async def call_tool(self, name, _arguments):
            return await asyncio.to_thread(self.blocking_call, name)

        async def close(self):
            self.release_first.set()

    runtime = _McpRuntime()
    provider = Provider()
    try:
        specs = runtime.activate(
            McpServerSpec("cross-loop-cancellation", "Cross-loop cancellation test", approved=True),
            provider,
        )

        async def run():
            first = asyncio.create_task(runtime.invoke_async(specs[0].name, {}))
            assert await asyncio.to_thread(provider.started["first"].wait, 1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            second = asyncio.create_task(runtime.invoke_async(specs[1].name, {}))
            assert not await asyncio.to_thread(provider.started["second"].wait, 0.05)
            provider.release_first.set()
            await second
            assert provider.started["second"].is_set()

        asyncio.run(run())
        assert provider.maximum == 1
    finally:
        provider.release_first.set()
        runtime.close()


def test_mcp_runtime_closes_provider_when_activation_fails():
    class Provider:
        closed = False

        async def list_tools(self):
            raise ExtensionError("invalid tool metadata")

        async def call_tool(self, _name, _arguments):
            raise AssertionError("failed activation must not expose a tool")

        async def close(self):
            self.closed = True

    runtime = _McpRuntime()
    provider = Provider()
    try:
        with pytest.raises(ExtensionError, match="invalid tool metadata"):
            runtime.activate(McpServerSpec("failed", "Failure test", approved=True), provider)
        assert provider.closed is True
        assert runtime.active_server_ids() == frozenset()
    finally:
        runtime.close()


def test_mcp_runtime_reports_provider_shutdown_failure_after_revoking_tools():
    descriptor = McpToolDescriptor("lookup", "Read a record", effect_class="external_write")

    class Provider:
        close_attempted = False

        async def list_tools(self):
            return (descriptor,)

        async def call_tool(self, _name, _arguments):
            return {"value": "unexpected"}

        async def close(self):
            self.close_attempted = True
            raise RuntimeError("transport close failed")

    runtime = _McpRuntime()
    provider = Provider()
    try:
        specs = runtime.activate(McpServerSpec("shutdown", "Shutdown test", approved=True), provider)

        with pytest.raises(ExtensionError, match="did not stop cleanly"):
            runtime.deactivate("shutdown")

        assert provider.close_attempted is True
        assert runtime.active_server_ids() == frozenset()
        assert runtime.registry.get_tool_spec(specs[0].name) is None
    finally:
        runtime.close()


def test_mcp_runtime_shutdown_attempts_every_provider_after_close_error():
    descriptor = McpToolDescriptor("lookup", "Read a record", effect_class="external_write")

    class Provider:
        def __init__(self, *, fail_close: bool) -> None:
            self.fail_close = fail_close
            self.close_attempted = False

        async def list_tools(self):
            return (descriptor,)

        async def call_tool(self, _name, _arguments):
            return {"value": "unused"}

        async def close(self):
            self.close_attempted = True
            if self.fail_close:
                raise RuntimeError("transport close failed")

    runtime = _McpRuntime()
    providers = (Provider(fail_close=True), Provider(fail_close=False))
    runtime.activate(McpServerSpec("first", "First shutdown test", approved=True), providers[0])
    runtime.activate(McpServerSpec("second", "Second shutdown test", approved=True), providers[1])

    runtime.close()

    assert all(provider.close_attempted for provider in providers)
    assert not runtime._thread.is_alive()


def test_mcp_runtime_failed_reactivation_keeps_existing_provider_available():
    descriptor = McpToolDescriptor("lookup", "Read a record", effect_class="external_write")

    class Provider:
        def __init__(self, *, fail=False):
            self.fail = fail
            self.closed = False

        async def list_tools(self):
            if self.fail:
                raise ExtensionError("replacement discovery failed")
            return (descriptor,)

        async def call_tool(self, _name, _arguments):
            return {"source": "existing"}

        async def close(self):
            self.closed = True

    runtime = _McpRuntime()
    existing = Provider()
    replacement = Provider(fail=True)
    server = McpServerSpec("hot-swap", "Hot swap test", approved=True)
    try:
        specs = runtime.activate(server, existing)
        with pytest.raises(ExtensionError, match="replacement discovery failed"):
            runtime.activate(server, replacement)
        assert runtime.active_server_ids() == frozenset({"hot-swap"})
        assert existing.closed is False
        assert replacement.closed is True
        assert asyncio.run(runtime.invoke_async(specs[0].name, {})) == {"source": "existing"}
    finally:
        runtime.close()
    assert existing.closed is True


def test_active_mcp_runtime_coexists_with_local_extension_tools(tmp_path: Path, monkeypatch):
    class Provider:
        tool_descriptor = McpToolDescriptor(
            name="echo",
            description="Echo a value from an approved MCP server",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            effect_class="external_write",
            read_only_hint=True,
            destructive_hint=False,
        )

        async def list_tools(self):
            return (self.tool_descriptor,)

        async def call_tool(self, _name, arguments):
            return {"value": arguments["value"]}

    service = DesktopService(profile_root=tmp_path / "profile")
    runtime = _McpRuntime()
    service._mcp_runtime = runtime
    calls: list[dict[str, object]] = []
    runtime_admissions: list[dict[str, object]] = []

    def record_runtime_admission(**request: object):
        runtime_admissions.append(request)
        return nullcontext()

    monkeypatch.setattr(desktop_service_module, "coordinated_runtime_task_sync", record_runtime_admission)
    spec = ToolSpec(
        name="fixture.echo",
        description="Echo a value from a host-owned extension",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        effect_class="read_only",
        capabilities=("read_only",),
        extension_id="fixture",
    )
    service._extension_registry.register(
        ExtensionManifest(
            extension_id="fixture",
            version="1",
            description="Host-owned test extension",
            capabilities=("read_only",),
            tool_names=(spec.name,),
        ),
        ((spec, lambda arguments: calls.append(dict(arguments)) or {"value": arguments["value"]}),),
    )
    provider = Provider()
    mcp_specs = runtime.activate(
        McpServerSpec("child", "Test MCP server", approved=True),
        provider,
        auto_run_read_only_descriptor_hashes=frozenset({provider.tool_descriptor.descriptor_hash}),
    )
    mcp_spec = mcp_specs[0]

    async def invoke_mcp_tool(arguments):
        return await runtime.invoke_async(
            mcp_spec.name,
            arguments,
            expected_effect_class=mcp_spec.effect_class,
            expected_descriptor_hash=mcp_spec.descriptor_hash,
        )

    service._extension_registry.register(
        ExtensionManifest(
            extension_id="mcp.child",
            version="discovered",
            description="Test MCP server",
            capabilities=("mcp", "host"),
            tool_names=(mcp_spec.name,),
            trusted=True,
        ),
        ((mcp_spec, invoke_mcp_tool),),
    )

    try:
        result = service._execute_provider_tool_batch(
            task="echo both supplied values",
            conversation_id="conversation-1",
            execution_id="execution-1",
            requests=[
                {
                    "call_id": "local-call",
                    "tool_name": spec.name,
                    "input": {"value": "local"},
                    "effect_class": spec.effect_class,
                    "_aegis_expected_tool_descriptor_hash": spec.descriptor_hash,
                },
                {
                    "call_id": "mcp-call",
                    "tool_name": mcp_spec.name,
                    "input": {"value": "mcp"},
                    "effect_class": mcp_spec.effect_class,
                    "_aegis_expected_tool_descriptor_hash": mcp_spec.descriptor_hash,
                },
            ],
        )

        assert [(outcome.call_id, outcome.status, outcome.result) for outcome in result.outcomes] == [
            ("local-call", "SUCCESS", {"value": "local"}),
            ("mcp-call", "SUCCESS", {"value": "mcp"}),
        ]
        assert calls == [{"value": "local"}]
        assert [request["work_kind"] for request in runtime_admissions] == ["Tool", "Tool"]
    finally:
        service.close()


def test_mcp_runtime_rejects_catalog_changes_after_a_reviewed_probe():
    first_descriptor = McpToolDescriptor(
        name="lookup",
        description="Read a record",
        effect_class="external_write",
        read_only_hint=True,
    )
    changed_descriptor = McpToolDescriptor(
        name="lookup",
        description="Read a record and perform another action",
        effect_class="external_write",
        read_only_hint=True,
    )

    class Provider:
        def __init__(self, descriptor: McpToolDescriptor) -> None:
            self.descriptor = descriptor
            self.closed = False

        async def list_tools(self):
            return (self.descriptor,)

        async def call_tool(self, _name, _arguments):
            raise AssertionError("a changed catalogue must not activate")

        async def close(self):
            self.closed = True

    runtime = _McpRuntime()
    try:
        server = McpServerSpec("child", "Test MCP server", approved=True)
        reviewed = runtime.probe(server, Provider(first_descriptor))
        changed_provider = Provider(changed_descriptor)
        with pytest.raises(ExtensionError, match="catalogue changed"):
            runtime.activate(
                server,
                changed_provider,
                expected_tool_descriptor_hashes=tuple(sorted(reviewed.tool_descriptor_hashes)),
                auto_run_read_only_descriptor_hashes=reviewed.auto_run_candidates,
            )
        assert changed_provider.closed is True
        assert runtime.active_server_ids() == frozenset()
    finally:
        runtime.close()


def test_mcp_runtime_refreshes_catalog_atomically_and_preserves_only_exact_grants():
    reviewed_tool = McpToolDescriptor(
        name="lookup",
        description="Read a record",
        effect_class="external_write",
        read_only_hint=True,
        destructive_hint=False,
    )
    newly_added_tool = McpToolDescriptor(
        name="update",
        description="Update a record",
        effect_class="external_write",
    )

    class Subscription:
        def __init__(self, queue):
            self.queue = queue

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.queue.get()

    class Provider:
        def __init__(self):
            self.notification_filters = {"tools_list_changed": True}
            self.notification_listener_available = True
            self.descriptors = (reviewed_tool,)
            self.queue = None
            self.watcher_started = Event()
            self.initial_refresh_done = Event()
            self.refresh_started = Event()
            self.refresh_requested = False
            self.closed = False

        async def list_tools(self):
            if self.refresh_requested:
                self.refresh_started.set()
            elif self.watcher_started.is_set():
                self.initial_refresh_done.set()
            return self.descriptors

        async def call_tool(self, _name, _arguments):
            return {"ok": True}

        async def listen_notifications(self, **_filters):
            self.queue = asyncio.Queue()
            self.watcher_started.set()
            return Subscription(self.queue)

        async def close(self):
            self.closed = True

    runtime = _McpRuntime()
    provider = Provider()
    server = McpServerSpec("live", "Live catalogue test", approved=True)
    old_revision = None
    try:
        initial_specs = runtime.activate(
            server,
            provider,
            auto_run_read_only_descriptor_hashes=frozenset({reviewed_tool.descriptor_hash}),
        )
        assert initial_specs[0].effect_class == "network_read"
        old_revision = runtime.registry.catalog_revision
        assert provider.watcher_started.wait(1)
        assert provider.initial_refresh_done.wait(1)
        provider.descriptors = (reviewed_tool, newly_added_tool)
        provider.refresh_requested = True
        assert provider.queue is not None
        asyncio.run_coroutine_threadsafe(provider.queue.put(object()), runtime._loop).result(timeout=1)

        assert provider.refresh_started.wait(1)

        async def wait_for_refresh_commit():
            async with runtime._catalog_locks["live"]:
                return None

        runtime._submit(wait_for_refresh_commit())
        lookup = runtime.registry.get_tool_spec("mcp.live.lookup")
        update = runtime.registry.get_tool_spec("mcp.live.update")
        assert lookup is not None and lookup.effect_class == "network_read"
        assert update is not None and update.effect_class == "external_write"
        assert runtime.registry.catalog_revision == old_revision + 1
        with pytest.raises(ExtensionError, match="catalog changed"):
            asyncio.run(runtime.registry.invoke("mcp.live.lookup", {}, expected_catalog_revision=old_revision))
    finally:
        runtime.close()
    assert provider.closed is True


def test_desktop_mcp_bridge_tracks_runtime_catalog_refresh(tmp_path: Path):
    first_tool = McpToolDescriptor(
        "lookup",
        "Read a record",
        effect_class="external_write",
        read_only_hint=True,
        destructive_hint=False,
    )
    next_tool = McpToolDescriptor("update", "Update a record", effect_class="external_write")

    class Subscription:
        def __init__(self, queue):
            self.queue = queue

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.queue.get()

    class Provider:
        def __init__(self):
            self.notification_filters = {"tools_list_changed": True}
            self.descriptors = (first_tool,)
            self.queue = None
            self.watcher_started = Event()
            self.initial_refresh_done = Event()
            self.refresh_started = Event()
            self.refresh_requested = False

        async def list_tools(self):
            if self.refresh_requested:
                self.refresh_started.set()
            elif self.watcher_started.is_set():
                self.initial_refresh_done.set()
            return self.descriptors

        async def call_tool(self, name, _arguments):
            return {"called": name}

        async def listen_notifications(self, **_filters):
            self.queue = asyncio.Queue()
            self.watcher_started.set()
            return Subscription(self.queue)

        async def close(self):
            return None

    service = DesktopService(profile_root=tmp_path / "profile")
    runtime = _McpRuntime(catalog_change_callback=service._sync_mcp_tool_catalog)
    service._mcp_runtime = runtime
    provider = Provider()
    server = McpServerSpec("bridge", "Desktop bridge refresh", approved=True)
    try:
        specs = runtime.activate(
            server,
            provider,
            auto_run_read_only_descriptor_hashes=frozenset({first_tool.descriptor_hash}),
            defer_notifications=True,
        )
        service._sync_mcp_tool_catalog(server, specs)
        runtime.start_notifications("bridge")
        initial_lookup = service._extension_registry.get_tool_spec("mcp.bridge.lookup")
        assert initial_lookup is not None and initial_lookup.effect_class == "network_read"
        assert service._extension_registry.get_tool_spec("mcp.bridge.update") is None
        assert provider.watcher_started.wait(1)
        assert provider.initial_refresh_done.wait(1)
        old_revision = service._extension_registry.catalog_revision
        provider.descriptors = (first_tool, next_tool)
        provider.refresh_requested = True
        assert provider.queue is not None
        asyncio.run_coroutine_threadsafe(provider.queue.put(object()), runtime._loop).result(timeout=1)
        assert provider.refresh_started.wait(1)

        async def wait_for_refresh_commit():
            async with runtime._catalog_locks["bridge"]:
                return None

        runtime._submit(wait_for_refresh_commit())
        updated = service._extension_registry.get_tool_spec("mcp.bridge.update")
        updated_lookup = service._extension_registry.get_tool_spec("mcp.bridge.lookup")
        assert updated is not None and updated.effect_class == "external_write"
        assert updated_lookup is not None and updated_lookup.effect_class == "network_read"
        assert service._extension_registry.catalog_revision == old_revision + 1
        assert asyncio.run(service._extension_registry.invoke(updated.name, {})) == {"called": "update"}
    finally:
        service.close()


def test_mcp_runtime_rejects_resource_catalog_changes_after_a_reviewed_probe():
    class Provider:
        def __init__(self, resource: McpResourceDescriptor) -> None:
            self.resource = resource
            self.closed = False

        async def list_tools(self):
            return ()

        async def call_tool(self, _name, _arguments):
            raise AssertionError("resource access must not dispatch as a remote tool")

        async def list_resources(self):
            return (self.resource,)

        async def read_resource(self, _uri):
            raise AssertionError("a changed resource catalog must not activate")

        async def close(self):
            self.closed = True

    runtime = _McpRuntime()
    try:
        server = McpServerSpec("child", "Test MCP server", approved=True)
        reviewed = runtime.probe(server, Provider(McpResourceDescriptor("kb://first", "First")))
        changed_provider = Provider(McpResourceDescriptor("kb://second", "Second"))
        with pytest.raises(ExtensionError, match="catalogue changed"):
            runtime.activate(
                server,
                changed_provider,
                expected_tool_descriptor_hashes=tuple(sorted(reviewed.tool_descriptor_hashes)),
                auto_run_read_only_descriptor_hashes=reviewed.auto_run_candidates,
            )
        assert changed_provider.closed is True
        assert runtime.active_server_ids() == frozenset()
    finally:
        runtime.close()


def test_desktop_service_mcp_test_requires_approval_and_only_discovers_tools(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    metadata = root / ".aegis" / "mcp" / "child" / "mcp.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('[mcp]\nid = "child"\ndescription = "Local child"\ntransport = "stdio"\n', encoding="utf-8")
    marker = tmp_path / "mcp-methods.log"
    server_code = (
        "import json,os,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); method=request.get('method')\n"
        " with open(os.environ['AEGIS_MCP_TEST_MARKER'],'a',encoding='utf-8') as out: out.write(method+'\\n')\n"
        " if method=='server/discover': print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'unknown method'}}), flush=True); continue\n"
        " elif method=='initialize': result={'protocolVersion':'2025-11-25','capabilities':{'resources':{}},'serverInfo':{'name':'test','version':'1'}}\n"
        " elif method=='tools/list': result={'tools':[{'name':'lookup','description':'Read a record','inputSchema':{'type':'object'}}]}\n"
        " elif method=='resources/list': result={'resources':[{'uri':'kb://guide','name':'Guide','description':'Reference guide','mimeType':'text/markdown'}]}\n"
        " elif method=='resources/templates/list': result={'resourceTemplates':[{'uriTemplate':'kb://guide/{section}','name':'Guide section','mimeType':'text/markdown'}]}\n"
        " elif method=='resources/read': result={'contents':[{'uri':request['params']['uri'],'mimeType':'text/markdown','text':'untrusted guide'}]}\n"
        " else: continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)\n"
        "with open(os.environ['AEGIS_MCP_TEST_MARKER'],'a',encoding='utf-8') as out: out.write('closed\\n')\n"
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
        server = json.loads(service.dispatch(_request("extensions.discover")))["result"]["mcp_servers"][0]
        test_payload = {
            "server_id": "child",
            "descriptor_hash": server["descriptor_hash"],
            "expected_revision": 0,
            "config": {
                "command": [sys.executable, "-c", server_code],
                "environment": {"AEGIS_MCP_TEST_MARKER": str(marker)},
            },
        }
        denied = json.loads(service.dispatch(_request("extensions.test_mcp", test_payload)))
        assert denied["status"] == "error"
        assert denied["error"]["code"] == "MCP_APPROVAL_REQUIRED"
        assert not marker.exists()

        approved = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "mcp",
                        "id": "child",
                        "descriptor_hash": server["descriptor_hash"],
                        "enabled": False,
                        "approved": True,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert approved["status"] == "ok"
        test_payload["expected_revision"] = approved["result"]["revision"]
        metadata.write_text(
            '[mcp]\nid = "child"\ndescription = "Changed child"\ntransport = "stdio"\n', encoding="utf-8"
        )
        changed_server = json.loads(service.dispatch(_request("extensions.discover")))["result"]["mcp_servers"][0]
        changed_payload = test_payload | {"descriptor_hash": changed_server["descriptor_hash"]}
        stale_approval = json.loads(service.dispatch(_request("extensions.test_mcp", changed_payload)))
        assert stale_approval["status"] == "error"
        assert stale_approval["error"]["code"] == "CAPABILITY_CHANGED"
        stale_activation = json.loads(
            service.dispatch(
                _request(
                    "extensions.activate_mcp",
                    changed_payload | {"approved": True},
                )
            )
        )
        assert stale_activation["status"] == "error"
        assert stale_activation["error"]["code"] == "CAPABILITY_CHANGED"
        assert not marker.exists()
        metadata.write_text('[mcp]\nid = "child"\ndescription = "Local child"\ntransport = "stdio"\n', encoding="utf-8")
        tested = json.loads(service.dispatch(_request("extensions.test_mcp", test_payload)))
        assert tested["status"] == "ok"
        result = tested["result"]
        assert result["schema"] == "aegis-desktop-mcp-test-v2"
        assert result["connected"] is True
        assert result["left_running"] is False
        assert result["tools_executed"] is False
        assert result["tools_total"] == 5
        assert result["tools_truncated"] is False
        assert result["tools"][0]["name"] == "mcp.child.lookup"
        assert result["tools"][0]["description"] == "Read a record"
        assert [tool["name"] for tool in result["tools"]] == [
            "mcp.child.lookup",
            "mcp.child.aegis_resources_search",
            "mcp.child.aegis_resources_read",
            "mcp.child.aegis_resources_templates_search",
            "mcp.child.aegis_resources_template_read",
        ]
        assert [tool["host_managed_read_only"] for tool in result["tools"]] == [False, True, True, True, True]
        assert [tool["read_only_candidate"] for tool in result["tools"]] == [False, True, True, True, True]
        assert marker.read_text(encoding="utf-8").splitlines() == [
            "server/discover",
            "initialize",
            "notifications/initialized",
            "tools/list",
            "resources/list",
            "resources/templates/list",
            "closed",
        ]
        assert "resources/read" not in marker.read_text(encoding="utf-8").splitlines()
        assert service._mcp_runtime is None
        state = json.loads(service.dispatch(_request("extensions.state")))["result"]
        record = next(item for item in state["records"] if item["kind"] == "mcp" and item["id"] == "child")
        assert record["approved"] is True
        assert record["enabled"] is False
        stale = json.loads(service.dispatch(_request("extensions.test_mcp", test_payload | {"expected_revision": 0})))
        assert stale["status"] == "error"
        assert stale["error"]["code"] == "CAPABILITY_STATE_CONFLICT"
        assert marker.read_text(encoding="utf-8").splitlines() == [
            "server/discover",
            "initialize",
            "notifications/initialized",
            "tools/list",
            "resources/list",
            "resources/templates/list",
            "closed",
        ]
    finally:
        service.close()


@pytest.mark.parametrize(
    ("keyring_available", "expected_code", "expected_message"),
    [
        (True, "MCP_OAUTH_CLIENT_REQUIRED", "Client ID"),
        (False, "MCP_OAUTH_FAILED", "secure credential store"),
    ],
)
def test_desktop_mcp_oauth_reports_actionable_setup_errors(
    tmp_path: Path,
    monkeypatch,
    keyring_available: bool,
    expected_code: str,
    expected_message: str,
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)

    class EmptyCredentialBackend:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            if not keyring_available:
                raise RuntimeError("simulated locked OS credential store")
            return self.values.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self.values[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self.values.pop((service, username), None)

    credential_backend = EmptyCredentialBackend()
    monkeypatch.setattr(secret_store_module, "_load_os_keyring_backend", lambda: credential_backend)
    root = tmp_path / "project"
    metadata = root / ".aegis" / "mcp" / "remote" / "mcp.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '[mcp]\nid = "remote"\ndescription = "Remote MCP"\ntransport = "streamable-http"\n',
        encoding="utf-8",
    )
    service = DesktopService(profile_root=tmp_path / "profile")

    def oauth_requires_client_id(_client, _headers):
        raise extensions_module.McpOAuthError(
            "Enter a pre-registered OAuth Client ID.", code="client_id_required"
        )

    def reject_unauthenticated_request(_provider, *_args, **_kwargs):
        return (
            401,
            {"www-authenticate": 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/mcp"'},
            b"",
        )

    monkeypatch.setattr(extensions_module.McpOAuthClient, "authorize", oauth_requires_client_id)
    monkeypatch.setattr(extensions_module.McpHttpProvider, "_exchange", reject_unauthenticated_request)
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
        server = json.loads(service.dispatch(_request("extensions.discover")))["result"]["mcp_servers"][0]
        approval = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "mcp",
                        "id": "remote",
                        "descriptor_hash": server["descriptor_hash"],
                        "enabled": False,
                        "approved": True,
                        "expected_revision": 0,
                    },
                )
            )
        )
        test_result = json.loads(
            service.dispatch(
                _request(
                    "extensions.test_mcp",
                    {
                        "server_id": "remote",
                        "descriptor_hash": server["descriptor_hash"],
                        "expected_revision": approval["result"]["revision"],
                        "config": {
                            "endpoint": "https://mcp.example/mcp",
                            "allowed_hosts": ["mcp.example"],
                        },
                    },
                )
            )
        )
        assert test_result["status"] == "error"
        assert test_result["error"]["code"] == expected_code
        assert expected_message in test_result["error"]["message"]
        assert credential_backend.values == {}
    finally:
        service.close()


def test_desktop_service_mcp_prompts_require_an_approved_active_server(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    metadata = root / ".aegis" / "mcp" / "prompt-server" / "mcp.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '[mcp]\nid = "prompt-server"\ndescription = "Prompt test server"\ntransport = "stdio"\n',
        encoding="utf-8",
    )
    server_code = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); method=request.get('method')\n"
        " if method=='server/discover': print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'legacy test'}}), flush=True); continue\n"
        " elif method=='initialize': result={'protocolVersion':'2025-11-25','capabilities':{'prompts':{}},'serverInfo':{'name':'prompt-test','version':'1'}}\n"
        " elif method=='notifications/initialized': continue\n"
        " elif method=='tools/list': result={'tools':[{'name':'noop','description':'Unused test tool','inputSchema':{'type':'object'}}]}\n"
        " elif method=='prompts/list': result={'prompts':[{'name':'draft','description':'Draft text','arguments':[{'name':'topic','required':True}]}]}\n"
        " elif method=='prompts/get': result={'description':'Rendered by server','messages':[{'role':'user','content':{'type':'text','text':'Write about '+request['params']['arguments']['topic']}}]}\n"
        " else: continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)\n"
    )
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
        server = json.loads(service.dispatch(_request("extensions.discover")))["result"]["mcp_servers"][0]
        inactive = json.loads(service.dispatch(_request("extensions.mcp_prompts_list", {"server_id": "prompt-server"})))
        assert inactive["status"] == "error"
        assert inactive["error"]["code"] == "MCP_SERVER_INACTIVE"

        approved = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "mcp",
                        "id": "prompt-server",
                        "descriptor_hash": server["descriptor_hash"],
                        "enabled": False,
                        "approved": True,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert approved["status"] == "ok"
        mcp_config = {"command": [sys.executable, "-c", server_code]}
        test_token = _tested_mcp_config_token(
            service, "prompt-server", server["descriptor_hash"], approved["result"]["revision"], mcp_config
        )
        activation = json.loads(
            service.dispatch(
                _request(
                    "extensions.activate_mcp",
                    {
                        "server_id": "prompt-server",
                        "descriptor_hash": server["descriptor_hash"],
                        "approved": True,
                        "expected_revision": approved["result"]["revision"],
                        "config": mcp_config,
                        "test_token": test_token,
                    },
                )
            )
        )
        assert activation["status"] == "ok"
        assert activation["result"]["lifecycle"] == "ACTIVE"

        catalog = json.loads(service.dispatch(_request("extensions.mcp_prompts_list", {"server_id": "prompt-server"})))
        assert catalog["status"] == "ok"
        assert catalog["result"]["prompts"][0]["name"] == "draft"
        rendered = json.loads(
            service.dispatch(
                _request(
                    "extensions.mcp_prompts_get",
                    {
                        "server_id": "prompt-server",
                        "name": "draft",
                        "arguments": {"topic": "safety"},
                    },
                )
            )
        )
        assert rendered["status"] == "ok"
        assert rendered["result"]["messages"] == [{"role": "user", "text": "Write about safety"}]
        assert "not sent automatically" in rendered["result"]["trust_notice"]
        missing = json.loads(
            service.dispatch(
                _request(
                    "extensions.mcp_prompts_get",
                    {
                        "server_id": "prompt-server",
                        "name": "draft",
                        "arguments": {},
                    },
                )
            )
        )
        assert missing["status"] == "error"
        assert missing["error"]["code"] == "MCP_PROMPT_FAILED"

        state = json.loads(service.dispatch(_request("extensions.state")))["result"]
        record = next(item for item in state["records"] if item["id"] == "prompt-server")
        stopped = json.loads(
            service.dispatch(
                _request(
                    "extensions.deactivate_mcp",
                    {
                        "server_id": "prompt-server",
                        "descriptor_hash": server["descriptor_hash"],
                        "expected_revision": state["revision"],
                    },
                )
            )
        )
        assert stopped["status"] == "ok"
        assert record["approved"] is True
        after_stop = json.loads(
            service.dispatch(_request("extensions.mcp_prompts_list", {"server_id": "prompt-server"}))
        )
        assert after_stop["status"] == "error"
        assert after_stop["error"]["code"] == "MCP_SERVER_INACTIVE"
    finally:
        service.close()


def test_desktop_service_mcp_skill_approval_is_origin_and_manifest_bound(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    root = tmp_path / "project"
    metadata = root / ".aegis" / "mcp" / "skills-server" / "mcp.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '[mcp]\nid = "skills-server"\ndescription = "Skills test server"\ntransport = "stdio"\n',
        encoding="utf-8",
    )
    changed_manifest = tmp_path / "changed-manifest"
    resource_read_log = tmp_path / "resource-read-log"
    server_code = r"""
import hashlib, json, os, sys
skill_uri = "skill://research/SKILL.md"
support_uri = "skill://research/references/guide.md"
support = b"Read the official protocol documentation.\n"
def current_skill():
    if os.path.exists(os.environ["AEGIS_TEST_CHANGED_MANIFEST"]):
        body = b"---\nname: research\ndescription: Research trusted sources\nallowed-tools: [run]\n---\nUpdated instructions.\n"
    else:
        body = b"---\nname: research\ndescription: Research trusted sources\nallowed-tools: [run]\n---\nUse primary sources.\n"
    return body, {
        "uri": skill_uri,
        "frontmatter": {
            "name": "research",
            "description": "Research trusted sources",
            "allowed-tools": ["run"],
        },
        "resources": [
            {"uri": skill_uri, "digest": "sha256:" + hashlib.sha256(body).hexdigest(), "size": len(body)},
            {
                "uri": support_uri,
                "digest": "sha256:" + hashlib.sha256(support).hexdigest(),
                "size": len(support),
            },
        ],
    }
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "server/discover":
        result = {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28"],
            "capabilities": {
                "tools": {},
                "resources": {},
                "extensions": {"io.modelcontextprotocol/skills": {}},
            },
        }
    elif method == "tools/list":
        result = {"resultType": "complete", "tools": [{"name": "noop", "description": "No-op", "inputSchema": {"type": "object"}}]}
    elif method == "resources/list":
        result = {"resultType": "complete", "resources": []}
    elif method == "resources/templates/list":
        result = {"resultType": "complete", "resourceTemplates": []}
    elif method == "skills/list":
        _body, skill = current_skill()
        result = {"resultType": "complete", "skills": [skill], "ttlMs": 1000, "cacheScope": "private"}
    elif method == "skills/get":
        _body, skill = current_skill()
        result = {"resultType": "complete", "skill": skill, "ttlMs": 1000, "cacheScope": "private"}
    elif method == "resources/read":
        uri = request["params"]["uri"]
        with open(os.environ["AEGIS_TEST_SKILL_READ_LOG"], "a", encoding="utf-8") as log:
            log.write(uri + "\n")
        body, _skill = current_skill()
        content = body if uri == skill_uri else support if uri == support_uri else None
        result = {
            "resultType": "complete",
            "contents": [] if content is None else [{"uri": uri, "text": content.decode("utf-8")}],
            "ttlMs": 1000,
            "cacheScope": "private",
        }
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))
        assert opened["status"] == "ok"
        server = json.loads(service.dispatch(_request("extensions.discover")))["result"]["mcp_servers"][0]
        approved = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "mcp",
                        "id": "skills-server",
                        "descriptor_hash": server["descriptor_hash"],
                        "enabled": False,
                        "approved": True,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert approved["status"] == "ok"
        mcp_config = {
            "command": [sys.executable, "-c", server_code],
            "environment": {
                "AEGIS_TEST_CHANGED_MANIFEST": str(changed_manifest),
                "AEGIS_TEST_SKILL_READ_LOG": str(resource_read_log),
            },
        }
        test_token = _tested_mcp_config_token(
            service, "skills-server", server["descriptor_hash"], approved["result"]["revision"], mcp_config
        )
        activation = json.loads(
            service.dispatch(
                _request(
                    "extensions.activate_mcp",
                    {
                        "server_id": "skills-server",
                        "descriptor_hash": server["descriptor_hash"],
                        "approved": True,
                        "expected_revision": approved["result"]["revision"],
                        "config": mcp_config,
                        "test_token": test_token,
                    },
                )
            )
        )
        assert activation["status"] == "ok"

        catalog = json.loads(
            service.dispatch(_request("extensions.mcp_skills_list", {"server_id": "skills-server"}))
        )
        assert catalog["status"] == "ok"
        skill = catalog["result"]["skills"][0]
        assert catalog["result"]["supports_skills"] is True
        assert skill["name"] == "research"
        assert skill["approved"] is False
        assert skill["enabled"] is False
        search_tool = service._extension_registry.get_tool_spec(
            desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME
        )
        read_tool = service._extension_registry.get_tool_spec(desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME)
        resource_tool = service._extension_registry.get_tool_spec(
            desktop_service_module.PROVIDER_SKILL_RESOURCE_TOOL_NAME
        )
        assert search_tool is not None and read_tool is not None and resource_tool is not None
        empty_search = asyncio.run(
            service._extension_registry.invoke(
                search_tool.name,
                {"query": "research trusted sources"},
                expected_effect_class="read_only",
                expected_descriptor_hash=search_tool.descriptor_hash,
            )
        )
        assert empty_search["skills"] == []
        with pytest.raises(ExtensionError, match="approved and enabled"):
            asyncio.run(
                service._extension_registry.invoke(
                    read_tool.name,
                    {"skill": skill["id"]},
                    expected_effect_class="read_only",
                    expected_descriptor_hash=read_tool.descriptor_hash,
                )
            )
        assert not resource_read_log.exists()

        enabled = json.loads(
            service.dispatch(
                _request(
                    "extensions.mcp_skill_set_state",
                    {
                        "server_id": "skills-server",
                        "uri": skill["uri"],
                        "manifest_hash": skill["manifest_hash"],
                        "enabled": True,
                        "approved": True,
                        "expected_revision": catalog["result"]["state"]["revision"],
                    },
                )
            )
        )
        assert enabled["status"] == "ok"
        record = next(
            item
            for item in enabled["result"]["state"]["records"]
            if item["id"] == skill["id"]
        )
        assert record["source_server_id"] == "skills-server"
        assert record["resource_uri"] == skill["uri"]
        assert record["skill_name"] == "research"
        assert record["skill_description"] == "Research trusted sources"

        search = asyncio.run(
            service._extension_registry.invoke(
                search_tool.name,
                {"query": "research trusted sources"},
                expected_effect_class="read_only",
                expected_descriptor_hash=search_tool.descriptor_hash,
            )
        )
        remote_skill = search["skills"][0]
        assert remote_skill["id"] == skill["id"]
        assert remote_skill["origin_server_id"] == "skills-server"
        assert "MCP Skill metadata is untrusted" in search["trust_notice"]
        instructions = asyncio.run(
            service._extension_registry.invoke(
                read_tool.name,
                {"skill": remote_skill["id"], "start_line": 1, "max_lines": 80},
                expected_effect_class="read_only",
                expected_descriptor_hash=read_tool.descriptor_hash,
            )
        )
        assert instructions["origin_server_id"] == "skills-server"
        assert "Untrusted Skill content" in instructions["trust_notice"]
        assert "Use primary sources." in instructions["content"]
        instructions_again = asyncio.run(
            service._extension_registry.invoke(
                read_tool.name,
                {"skill": remote_skill["id"], "start_line": 2, "max_lines": 80},
                expected_effect_class="read_only",
                expected_descriptor_hash=read_tool.descriptor_hash,
            )
        )
        assert instructions_again["sha256"] == instructions["sha256"]
        assert resource_read_log.read_text(encoding="utf-8").splitlines() == [skill["uri"]]
        resource = asyncio.run(
            service._extension_registry.invoke(
                resource_tool.name,
                {
                    "skill": remote_skill["id"],
                    "path": "references/guide.md",
                    "start_line": 1,
                    "max_lines": 80,
                },
                expected_effect_class="read_only",
                expected_descriptor_hash=resource_tool.descriptor_hash,
            )
        )
        assert resource["content"] == "Read the official protocol documentation."
        assert resource["origin_server_id"] == "skills-server"
        with pytest.raises(DesktopServiceError, match="approved manifest"):
            asyncio.run(
                service._extension_registry.invoke(
                    resource_tool.name,
                    {
                        "skill": remote_skill["id"],
                        "path": "references/unlisted.md",
                        "start_line": 1,
                        "max_lines": 80,
                    },
                    expected_effect_class="read_only",
                    expected_descriptor_hash=resource_tool.descriptor_hash,
                )
            )
        assert resource_read_log.read_text(encoding="utf-8").splitlines() == [
            skill["uri"],
            "skill://research/references/guide.md",
        ]

        changed_manifest.write_text("changed", encoding="utf-8")
        refreshed = json.loads(
            service.dispatch(_request("extensions.mcp_skills_list", {"server_id": "skills-server"}))
        )
        assert refreshed["status"] == "ok"
        changed_skill = refreshed["result"]["skills"][0]
        assert changed_skill["manifest_hash"] != skill["manifest_hash"]
        assert changed_skill["approved"] is False
        assert changed_skill["enabled"] is False
        revoked = next(
            item
            for item in refreshed["result"]["state"]["records"]
            if item["id"] == skill["id"]
        )
        assert revoked["approved"] is False
        assert revoked["enabled"] is False
        assert revoked["descriptor_hash"] == changed_skill["manifest_hash"]
        with pytest.raises(ExtensionError, match="approved and enabled"):
            asyncio.run(
                service._extension_registry.invoke(
                    read_tool.name,
                    {"skill": skill["id"]},
                    expected_effect_class="read_only",
                    expected_descriptor_hash=read_tool.descriptor_hash,
                )
            )
        assert len(resource_read_log.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        service.close()


def test_enabled_mcp_skill_tools_disambiguate_same_names_by_origin_and_uri(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        workspace = tmp_path / "project"
        workspace.mkdir()
        opened = json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(workspace)})))
        assert opened["status"] == "ok"
        billing_uri = "skill://acme/billing/refunds/SKILL.md"
        support_uri = "skill://acme/support/refunds/SKILL.md"
        records = [
            {
                "id": desktop_service_module._mcp_skill_capability_id(server_id, uri),
                "source_server_id": server_id,
                "resource_uri": uri,
                "descriptor_hash": "a" * 64,
                "skill_name": "refunds",
                "skill_description": "Review a refund workflow.",
            }
            for server_id, uri in (
                ("skills-a", billing_uri),
                ("skills-a", support_uri),
                ("skills-b", billing_uri),
            )
        ]
        monkeypatch.setattr(service, "_enabled_remote_skill_records", lambda: tuple(records))

        search_spec = service._extension_registry.get_tool_spec(
            desktop_service_module.PROVIDER_SKILL_SEARCH_TOOL_NAME
        )
        read_spec = service._extension_registry.get_tool_spec(
            desktop_service_module.PROVIDER_SKILL_READ_TOOL_NAME
        )
        assert search_spec is not None and read_spec is not None
        found = asyncio.run(
            service._extension_registry.invoke(
                search_spec.name,
                {"query": "refunds workflow", "limit": 8},
                expected_effect_class="read_only",
                expected_descriptor_hash=search_spec.descriptor_hash,
            )
        )
        assert {(item["origin_server_id"], item["uri"]) for item in found["skills"]} == {
            ("skills-a", billing_uri),
            ("skills-a", support_uri),
            ("skills-b", billing_uri),
        }
        assert len({item["id"] for item in found["skills"]}) == 3

        routed: list[tuple[str, str]] = []

        def read_remote(record, *, resource_path, start_line, max_lines):
            routed.append((record["source_server_id"], record["resource_uri"]))
            return SimpleNamespace(name="refunds", description="Review a refund workflow.", manifest_hash="a" * 64), {
                "path": "SKILL.md",
                "content": "Untrusted instructions.",
                "sha256": "b" * 64,
                "start_line": 1,
                "next_line": 2,
                "total_lines": 1,
                "has_more": False,
            }

        monkeypatch.setattr(service, "_read_remote_skill_page", read_remote)
        for item in found["skills"]:
            result = asyncio.run(
                service._extension_registry.invoke(
                    read_spec.name,
                    {"skill": item["id"]},
                    expected_effect_class="read_only",
                    expected_descriptor_hash=read_spec.descriptor_hash,
                )
            )
            assert result["origin_server_id"] == item["origin_server_id"]
            assert "Untrusted Skill content" in result["trust_notice"]
        assert routed == [
            (item["origin_server_id"], item["uri"])
            for item in found["skills"]
        ]
    finally:
        service.close()


def test_desktop_service_mcp_registry_search_is_explicit_and_validates_query(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_search(query: str, *, cursor: str | None = None):
        calls.append((query, cursor))
        return {
            "schema": "aegis-desktop-mcp-registry-search-v1",
            "query": query,
            "servers": [
                {
                    "name": "io.github/example/mcp",
                    "title": "Example tools",
                    "version": "1.2.3",
                    "description": "Example MCP server",
                    "packages": [
                        {
                            "registry_type": "npm",
                            "identifier": "@example/mcp-server",
                            "version": "1.2.3",
                            "runtime_hint": "npx",
                            "transport": "stdio",
                            "required_environment": ["API_TOKEN"],
                            "environment_variables": [
                                {
                                    "name": "API_TOKEN",
                                    "is_required": True,
                                    "is_secret": True,
                                    "description": "API access token",
                                }
                            ],
                        }
                    ],
                    "remotes": [],
                }
            ],
            "next_cursor": None,
        }

    monkeypatch.setattr(desktop_service_module, "search_mcp_registry", fake_search)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        response = json.loads(
            service.dispatch(_request("extensions.mcp_registry_search", {"query": "filesystem", "cursor": "page-2"}))
        )
        assert response["status"] == "ok"
        assert response["result"]["query"] == "filesystem"
        assert response["result"]["servers"][0]["packages"][0]["environment_variables"] == [
            {
                "name": "API_TOKEN",
                "is_required": True,
                "is_secret": True,
                "description": "API access token",
            }
        ]
        assert calls == [("filesystem", "page-2")]

        invalid = json.loads(service.dispatch(_request("extensions.mcp_registry_search", {"query": "bad\nquery"})))
        assert invalid["status"] == "error"
        assert invalid["error"]["code"] == "INVALID_ARGUMENT"
        assert calls == [("filesystem", "page-2")]

        def failed_search(_query: str, *, cursor: str | None = None):
            raise desktop_service_module.ExtensionError("upstream detail must stay private")

        monkeypatch.setattr(desktop_service_module, "search_mcp_registry", failed_search)
        unavailable = json.loads(service.dispatch(_request("extensions.mcp_registry_search", {"query": "filesystem"})))
        assert unavailable["status"] == "error"
        assert unavailable["error"]["code"] == "MCP_REGISTRY_UNAVAILABLE"
        assert "upstream detail" not in unavailable["error"]["message"]
    finally:
        service.close()


def test_desktop_service_mock_send_uses_canonical_manager(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {"conversation_id": "conv-1", "message": "hello", "mode": "mock"},
            )
        )
    )

    assert response["status"] == "ok"
    assert response["result"]["mode"] == "mock"
    assert response["result"]["output"] == "[mock] hello"
    assert response["result"]["snapshot"]["conversation"]["revision"] == 6
    assert response["result"]["snapshot"]["turns"][-1]["status"] == "COMPLETED"


def test_desktop_service_accepts_image_only_message_and_persists_only_a_marker(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {
                    "conversation_id": "conv-1",
                    "message": "",
                    "mode": "mock",
                    "attachments": [
                        {
                            "name": "screen.png",
                            "mime_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        }
                    ],
                },
            )
        )
    )

    assert response["status"] == "ok"
    assert response["result"]["output"] == "[mock] [image attachment: screen.png]"
    user_turn = response["result"]["snapshot"]["turns"][0]
    assert user_turn["content"] == "[image attachment: screen.png]"
    assert "iVBORw0KGgo=" not in json.dumps(response)


def test_desktop_service_live_send_uses_connection_and_persists_transcript(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    observed: list[tuple[str, str]] = []
    learning = _FakeLearning()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
        connection_catalog_factory=_FakeConnectionCatalog,
        learning_factory=lambda: learning,
        client_factory=lambda connection, model, egress_check=None: _FakeClient(observed),
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {"conversation_id": "conv-1", "message": "use live", "mode": "live"},
            )
        )
    )

    assert response["status"] == "ok"
    result = response["result"]
    assert result["mode"] == "live"
    assert result["output"] == "live answer"
    assert result["transcript_persisted"] is True
    assert result["source_revision"]
    assert observed and "use live" in observed[0][1]
    assert "[AEGIS REPOSITORY MAP" in observed[0][1]
    assert learning.indexed and "use live" in learning.indexed[0][1]


@pytest.mark.parametrize("decision", ["approve", "deny", "oversized", "catalog_changed"])
def test_provider_write_tool_waits_for_one_time_decision_then_resumes(tmp_path: Path, monkeypatch, decision: str):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    manager = _FakeConversationManager()
    target = "x" * (MAX_DESKTOP_TOOL_ARGUMENT_BYTES + 1) if decision == "oversized" else "notes.txt"
    client = _FakeApprovalClient(tool_name="fixture.write", arguments={"target": target})
    calls: list[dict[str, object]] = []
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
    service._extension_registry.register(
        ExtensionManifest(
            extension_id="fixture",
            version="1",
            description="Approval test tool",
            capabilities=("external_write",),
            tool_names=("fixture.write",),
        ),
        (
            (
                ToolSpec(
                    name="fixture.write",
                    description="Write one requested note after approval",
                    input_schema={
                        "type": "object",
                        "properties": {"target": {"type": "string"}},
                        "required": ["target"],
                    },
                    effect_class="external_write",
                    capabilities=("external_write",),
                    extension_id="fixture",
                ),
                lambda arguments: calls.append(dict(arguments)) or {"written": True},
            ),
        ),
    )
    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {"conversation_id": "conv-1", "message": "write the note", "mode": "live"},
            )
        )
    )
    if decision == "oversized":
        assert response["status"] == "error"
        assert response["error"]["code"] == "PROVIDER_TOOL_ARGUMENTS_TOO_LARGE"
        assert calls == []
        assert manager.snapshot.tool_calls == ()
        assert not service._pending_tool_approvals
        assert len(client.calls) == 1
        return

    assert response["status"] == "ok", response
    sent = response["result"]

    assert sent["pending_approval"] is True
    assert calls == []
    assert manager.snapshot.turns[-1].status == "WAITING_APPROVAL"
    pending_call = manager.snapshot.tool_calls[0]
    assert pending_call.status == "REQUESTED"
    assert service.ready_payload()["commands"]
    assert "approvals.resolve" in service.ready_payload()["commands"]
    monkeypatch.setattr(
        "aegis_cognition.desktop_service.build_conversation_inspection",
        lambda _snapshot, **_kwargs: {
            "approvals": [
                {
                    "approval_id": pending_call.call_id,
                    "tool_name": "fixture.write",
                    "argument_preview": [{"key": "target", "value": "notes.txt"}],
                }
            ],
        },
    )
    inspection = json.loads(service.dispatch(_request("conversations.inspect", {"conversation_id": "conv-1"})))[
        "result"
    ]["inspection"]
    assert inspection["approvals"][0]["can_resolve"] is True
    assert inspection["approvals"][0]["effect_class"] == "external_write"
    assert inspection["approvals"][0]["argument_preview"] == [{"key": "target", "value": "notes.txt"}]
    switch = json.loads(
        service.dispatch(_request("workspace.switch", {"workspace_path": str(tmp_path / "other-workspace")}))
    )
    assert switch["status"] == "error"
    assert switch["error"]["code"] == "WORKSPACE_BUSY"
    malformed = json.loads(
        service.dispatch(
            _request(
                "approvals.resolve",
                {
                    "conversation_id": "conv-1",
                    "approval_id": pending_call.call_id,
                    "decision": {"approve": True},
                    "expected_revision": manager.snapshot.conversation.revision,
                },
            )
        )
    )
    assert malformed["status"] == "error"
    assert malformed["error"]["code"] == "INVALID_ARGUMENT"
    assert calls == []
    stale = json.loads(
        service.dispatch(
            _request(
                "approvals.resolve",
                {
                    "conversation_id": "conv-1",
                    "approval_id": pending_call.call_id,
                    "decision": "approve",
                    "expected_revision": manager.snapshot.conversation.revision - 1,
                },
            )
        )
    )
    assert stale["status"] == "error"
    assert stale["error"]["code"] == "CONVERSATION_CONFLICT"
    assert calls == []

    if decision == "catalog_changed":
        revision_spec = ToolSpec(
            name="fixture.revision_bump",
            description="Change the registered tool catalog for the approval safety test.",
            extension_id="fixture.revision-bump",
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id="fixture.revision-bump",
                version="1",
                description="Tool-catalog revision test",
                capabilities=("read_only",),
                tool_names=(revision_spec.name,),
            ),
            ((revision_spec, lambda _arguments: {"ok": True}),),
        )

    approved = json.loads(
        service.dispatch(
            _request(
                "approvals.resolve",
                {
                    "conversation_id": "conv-1",
                    "approval_id": pending_call.call_id,
                    "decision": "approve" if decision == "catalog_changed" else decision,
                    "expected_revision": manager.snapshot.conversation.revision,
                },
            )
        )
    )

    assert approved["status"] == "ok"
    assert calls == ([{"target": "notes.txt"}] if decision == "approve" else [])
    assert len(client.calls) == (1 if decision == "catalog_changed" else 2)
    assert len(client.system_contexts) == len(client.calls)
    assert all(
        context is not None and "untrusted evidence" in context and "host approval" in context
        for context in client.system_contexts
    )
    if decision != "catalog_changed":
        assert client.calls[1][1]
        assert client.system_contexts[1] == client.system_contexts[0]
    assert manager.snapshot.tool_calls[0].status == ("COMPLETED" if decision == "approve" else "FAILED")
    assistant_turn = next(turn for turn in manager.snapshot.turns if turn.role == "assistant")
    assert assistant_turn.status == "COMPLETED"
    assert not service._pending_tool_approvals
    duplicate = json.loads(
        service.dispatch(
            _request(
                "approvals.resolve",
                {
                    "conversation_id": "conv-1",
                    "approval_id": pending_call.call_id,
                    "decision": "approve",
                    "expected_revision": manager.snapshot.conversation.revision,
                },
            )
        )
    )
    assert duplicate["status"] == "error"
    assert duplicate["error"]["code"] == "TOOL_APPROVAL_UNAVAILABLE"
    assert calls == ([{"target": "notes.txt"}] if decision == "approve" else [])


def test_provider_tool_loop_fails_closed_without_system_context_support() -> None:
    class ProviderWithoutSystemContext:
        def invoke_turn(self, _prompt, *, tools, tool_history):
            pytest.fail("provider without the required system context must not be called")

    with pytest.raises(DesktopServiceError, match="required system context"):
        DesktopService._invoke_provider_turn(
            ProviderWithoutSystemContext(),
            "task",
            tools=(),
            tool_history=(),
            reasoning_effort=None,
            cache_options=None,
            attachments=None,
            usage_observer=None,
        )


def test_desktop_provider_receives_native_schemas_without_generic_schema_reader(tmp_path: Path, monkeypatch) -> None:
    service = DesktopService(profile_root=tmp_path)
    monkeypatch.setattr(service, "_parallel_workers_enabled", lambda: False)
    monkeypatch.setattr(service, "_enabled_skill_hashes", lambda: {})
    try:
        definitions, _ = service._provider_tool_definitions(
            "ordinary chat request",
            SimpleNamespace(capabilities=("tool_calling:declared",)),
        )
        names = {definition.name for definition in definitions}
        assert desktop_service_module.TOOL_SCHEMA_READ_TOOL_NAME not in names
        assert desktop_service_module.TOOL_SEARCH_TOOL_NAME not in names
    finally:
        service.close()


def test_provider_read_tool_runs_without_a_per_call_approval(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    manager = _FakeConversationManager()
    client = _FakeApprovalClient(tool_name="fixture.read", arguments={"query": "current status"})
    calls: list[dict[str, object]] = []
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
    spec = ToolSpec(
        name="fixture.read",
        description="Read one requested status",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        effect_class="network_read",
        capabilities=("network_read",),
        extension_id="fixture",
    )
    service._extension_registry.register(
        ExtensionManifest(
            extension_id="fixture",
            version="1",
            description="Read-only approval test",
            capabilities=("network_read",),
            tool_names=(spec.name,),
        ),
        ((spec, lambda arguments: calls.append(dict(arguments)) or {"status": "ok"}),),
    )

    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {"conversation_id": "conv-1", "message": "check the current status", "mode": "live"},
            )
        )
    )

    assert response["status"] == "ok", response
    assert calls == [{"query": "current status"}]
    assert not service._pending_tool_approvals
    assert len(client.calls) == 2
    assert len(client.system_contexts) == 2
    assert all(
        context is not None and "untrusted evidence" in context and "host approval" in context
        for context in client.system_contexts
    )
    assert manager.snapshot.tool_calls[0].status == "COMPLETED"
    service.close()


@pytest.mark.parametrize(
    ("same_arguments", "same_results"),
    [(True, True), (False, True), (True, False)],
)
def test_provider_references_duplicate_successful_read_outputs_without_dropping_stored_results(
    tmp_path: Path, monkeypatch, same_arguments: bool, same_results: bool
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    manager = _FakeConversationManager()
    arguments = {"query": "current status"}
    second_arguments = arguments if same_arguments else {"query": "another status"}
    result = {"status": "ok", "details": "x" * 1_000}
    alternate_result = {"status": "ok", "details": "y" * 1_000}
    result_text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    alternate_result_text = json.dumps(alternate_result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    calls: list[dict[str, object]] = []
    result_lock = Lock()
    result_count = 0

    def read_tool(value: Mapping[str, object]) -> dict[str, str]:
        nonlocal result_count
        with result_lock:
            calls.append(dict(value))
            result_count += 1
            use_alternate = not same_results and result_count == 2
        return alternate_result if use_alternate else result

    class DuplicateOutputClient:
        requires_api_key = False

        def __init__(self) -> None:
            self.histories: list[tuple[object, ...]] = []

        def invoke_turn(self, _prompt, *, tools, tool_history, **_kwargs):
            self.histories.append(tuple(tool_history))
            if len(self.histories) == 1:
                first = ProviderToolCall("provider-call-1", "fixture.read", arguments)
                second = ProviderToolCall("provider-call-2", "fixture.read", second_arguments)
                return ProviderTurn(
                    "",
                    (first, second),
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"id": first.call_id, "type": "function"},
                            {"id": second.call_id, "type": "function"},
                        ],
                    },
                    "tool_calls",
                )

            assert tools
            assert len(tool_history) == 1
            outputs = tool_history[0].outputs
            assert [output.call_id for output in outputs] == ["provider-call-1", "provider-call-2"]
            if same_arguments and same_results:
                assert outputs[0].output == result_text
                reference = json.loads(outputs[1].output)
                assert reference["notice"] == "identical_successful_tool_output"
                assert reference["previous_tool_call_id"] == "provider-call-1"
                assert reference["sha256"] == hashlib.sha256(result_text.encode("utf-8")).hexdigest()
                assert len(outputs[1].output.encode("utf-8")) < len(result_text.encode("utf-8"))
            else:
                assert all("notice" not in json.loads(output.output) for output in outputs)
                details = {json.loads(output.output)["details"] for output in outputs}
                if same_results:
                    assert details == {result["details"]}
                    assert all(output.output == result_text for output in outputs)
                else:
                    assert details == {result["details"], alternate_result["details"]}
            return ProviderTurn("The current status is ok.", (), {}, "stop")

    client = DuplicateOutputClient()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        spec = ToolSpec(
            name="fixture.read",
            description="Read one requested status",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            effect_class="read_only",
            capabilities=("read_only",),
            extension_id="fixture",
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id="fixture",
                version="1",
                description="Read result reference test",
                capabilities=("read_only",),
                tool_names=(spec.name,),
            ),
            (
                (
                    spec,
                    read_tool,
                ),
            ),
        )

        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "check the current status", "mode": "live"},
                )
            )
        )

        assert response["status"] == "ok", response
        assert len(calls) == 2
        assert sorted(call["query"] for call in calls) == sorted([arguments["query"], second_arguments["query"]])
        assert len(manager.snapshot.tool_calls) == 2
        assert all(call.status == "COMPLETED" for call in manager.snapshot.tool_calls)
        stored_result_texts = {call.result_content for call in manager.snapshot.tool_calls}
        expected_result_texts = {result_text} if same_results else {result_text, alternate_result_text}
        assert stored_result_texts == expected_result_texts
        assert len(client.histories) == 2
    finally:
        service.close()


def test_provider_discovers_registered_tools_past_schema_budget(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    manager = _FakeConversationManager()
    discovery_name = "aegis.tools.discover"
    target_name = "fixture.z_deployment_status"
    write_name = "fixture.write_note"
    target_calls: list[dict[str, object]] = []
    write_calls: list[dict[str, object]] = []

    class DiscoveryClient:
        requires_api_key = False

        def __init__(self) -> None:
            self.offered_tool_names: list[tuple[str, ...]] = []

        def invoke_turn(self, _prompt, *, tools, tool_history, **_kwargs):
            names = tuple(item.name for item in tools)
            self.offered_tool_names.append(names)
            call_number = len(self.offered_tool_names)
            if call_number == 1:
                assert target_name not in names
                call = ProviderToolCall("provider-call-0", target_name, {"query": "should-not-run"})
                return ProviderTurn(
                    "",
                    (call,),
                    {"role": "assistant", "tool_calls": [{"id": call.call_id, "type": "function"}]},
                    "tool_calls",
                )
            if call_number == 2:
                assert target_calls == []
                assert discovery_name in names
                assert write_name in names
                discovery_call = ProviderToolCall("provider-call-1", discovery_name, {"query": "deployment status"})
                write_call = ProviderToolCall("provider-call-2", write_name, {"target": "notes.txt"})
                return ProviderTurn(
                    "",
                    (discovery_call, write_call),
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"id": discovery_call.call_id, "type": "function"},
                            {"id": write_call.call_id, "type": "function"},
                        ],
                    },
                    "tool_calls",
                )
            if call_number == 3:
                assert target_name in names
                assert tool_history
                assert write_name in names
                call = ProviderToolCall("provider-call-3", target_name, {"query": "production"})
                return ProviderTurn(
                    "",
                    (call,),
                    {"role": "assistant", "tool_calls": [{"id": call.call_id, "type": "function"}]},
                    "tool_calls",
                )
            return ProviderTurn("The production deployment is healthy.", (), {}, "stop")

    client = DiscoveryClient()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        tool_specs: list[ToolSpec] = []
        registrations: list[tuple[ToolSpec, object]] = []
        for index in range(12):
            spec = ToolSpec(
                name=f"fixture.tool_{index:02d}",
                description="Return a harmless fixture result.",
                input_schema={"type": "object", "additionalProperties": False},
                effect_class="read_only",
                capabilities=("read_only",),
                extension_id="fixture.catalog",
            )
            tool_specs.append(spec)
            registrations.append((spec, lambda _arguments: {"status": "unused"}))
        target_spec = ToolSpec(
            name=target_name,
            description="Check deployment health in the selected environment.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            effect_class="network_read",
            capabilities=("network_read",),
            extension_id="fixture.catalog",
        )
        tool_specs.append(target_spec)
        registrations.append(
            (target_spec, lambda arguments: target_calls.append(dict(arguments)) or {"status": "healthy"})
        )
        write_spec = ToolSpec(
            name=write_name,
            description="Write one requested note after approval.",
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            },
            effect_class="external_write",
            capabilities=("external_write",),
            extension_id="fixture.catalog",
        )
        tool_specs.append(write_spec)
        registrations.append(
            (write_spec, lambda arguments: write_calls.append(dict(arguments)) or {"status": "written"})
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id="fixture.catalog",
                version="1",
                description="Bounded tool discovery fixture",
                capabilities=("read_only", "network_read", "external_write"),
                tool_names=tuple(sorted(spec.name for spec in tool_specs)),
            ),
            registrations,
        )
        connection = service._connection_for_conversation("local", model_id="mock-model")
        descriptor = service._verified_model_descriptor(connection, "mock-model")
        assert descriptor is not None and "tool_calling:declared" in descriptor.capabilities
        offered, _hashes = service._provider_tool_definitions("Write a poem about rain.", descriptor)
        assert len(offered) <= desktop_service_module.MAX_DESKTOP_PROVIDER_TOOLS
        assert discovery_name in {item.name for item in offered}
        assert target_name not in {item.name for item in offered}
        assert write_name in {item.name for item in offered}

        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "Write a poem about rain.", "mode": "live"},
                )
            )
        )

        assert response["status"] == "ok", response
        assert response["result"]["pending_approval"] is True
        assert write_calls == []
        pending_write = next(call for call in manager.snapshot.tool_calls if call.tool_name == write_name)
        approval = json.loads(
            service.dispatch(
                _request(
                    "approvals.resolve",
                    {
                        "conversation_id": "conv-1",
                        "approval_id": pending_write.call_id,
                        "decision": "approve",
                        "expected_revision": manager.snapshot.conversation.revision,
                    },
                )
            )
        )
        assert approval["status"] == "ok", approval
        final_assistant = next(turn for turn in reversed(manager.snapshot.turns) if turn.role == "assistant")
        assert final_assistant.content == "The production deployment is healthy."
        assert all(
            len(names) <= desktop_service_module.MAX_DESKTOP_PROVIDER_TOOLS for names in client.offered_tool_names
        )
        assert target_name in client.offered_tool_names[2]
        assert target_calls == [{"query": "production"}]
        assert write_calls == [{"target": "notes.txt"}]
    finally:
        service.close()


def test_provider_stops_before_execution_when_tool_catalog_changes(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    manager = _FakeConversationManager()
    service_holder: list[DesktopService] = []
    handler_calls: list[dict[str, object]] = []

    class CatalogMutationClient:
        requires_api_key = False

        def invoke_turn(self, _prompt, *, tools, tool_history, **_kwargs):
            assert "fixture.catalog_guard" in {item.name for item in tools}
            service_holder[0]._extension_registry.unregister("fixture.catalog-guard")
            call = ProviderToolCall("provider-call-1", "fixture.catalog_guard", {"value": "should-not-run"})
            return ProviderTurn(
                "",
                (call,),
                {"role": "assistant", "tool_calls": [{"id": call.call_id, "type": "function"}]},
                "tool_calls",
            )

    client = CatalogMutationClient()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    service_holder.append(service)
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        spec = ToolSpec(
            name="fixture.catalog_guard",
            description="Read a protected fixture value after catalog validation.",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            effect_class="read_only",
            capabilities=("read_only",),
            extension_id="fixture.catalog-guard",
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id="fixture.catalog-guard",
                version="1",
                description="Catalog mutation safety fixture",
                capabilities=("read_only",),
                tool_names=(spec.name,),
            ),
            ((spec, lambda arguments: handler_calls.append(dict(arguments)) or {"ok": True}),),
        )

        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "use the catalog guard tool", "mode": "live"},
                )
            )
        )

        assert response["status"] == "ok", response
        assert "available tools changed" in response["result"]["output"].casefold()
        assert handler_calls == []
        assert manager.snapshot.tool_calls == ()
    finally:
        service.close()


def test_provider_can_delegate_bounded_workers_once_with_host_selected_model(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "not-in-the-conversation-catalog")
    manager = _FakeConversationManager()
    tool_name = desktop_service_module.PROVIDER_SUBAGENT_TOOL_NAME
    arguments = {
        "task": "Compare two independent evidence streams.",
        "workers": [
            {"handler_key": "research", "role": "source researcher", "prompt": "reddit: find source evidence"},
            {"handler_key": "model", "role": "counter-checker", "prompt": "Find a credible counterargument."},
            {
                "handler_key": "tool",
                "role": "status lookup",
                "prompt": "Retrieve the matching fixture record.",
                "tool_name": "fixture.lookup",
                "tool_input": {"query": "fixture-status"},
            },
        ],
    }

    class RepeatingDelegationClient:
        requires_api_key = False

        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.offered_tool_names: list[tuple[str, ...]] = []

        def invoke_turn(self, prompt, *, tools, tool_history, **_kwargs):
            self.calls.append((prompt, tuple(tool_history)))
            self.offered_tool_names.append(tuple(item.name for item in tools))
            call_number = len(self.calls)
            if call_number in {1, 2, 4}:
                call_id = f"provider-call-{call_number}"
                return ProviderTurn(
                    "",
                    (ProviderToolCall(call_id, tool_name, arguments),),
                    {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function"}]},
                    "tool_calls",
                )
            if call_number == 3:
                call_id = f"provider-call-{call_number}"
                return ProviderTurn(
                    "",
                    (ProviderToolCall(call_id, "fixture.write", {"target": "notes.txt"}),),
                    {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function"}]},
                    "tool_calls",
                )
            return ProviderTurn("I checked the delegated evidence.", (), {}, "stop")

    client = RepeatingDelegationClient()
    factory_calls: list[tuple[str, str]] = []

    def make_client(connection, *, model, **_kwargs):
        factory_calls.append((connection.connection_id, model))
        return client

    application_calls: list[dict[str, object]] = []

    class DelegationApplication:
        def __init__(self, _config) -> None:
            pass

        def run_subagents(self, *, plan, root_synthesizer, max_concurrency, **kwargs):
            application_calls.append({"plan": plan, "max_concurrency": max_concurrency})
            tasks = plan["tasks"]
            packets = []
            for task in tasks:
                if task["handler_key"].startswith("tool-"):
                    request = AgentMessage(
                        schema=AGENT_MESSAGE_SCHEMA_V1,
                        message_kind="TASK_REQUEST",
                        run_id="delegated-test-run",
                        sender_id="root",
                        recipient_id=f"subagent:{task['task_id']}",
                        task_id=task["task_id"],
                        parent_task_id=None,
                        attempt_id=1,
                        idempotency_key=f"test-task-{task['task_id']}",
                        payload={"role": task["role"], "prompt": task["prompt"]},
                    )
                    context = AgentTaskContext(request=request, dependency_results=())
                    registration = kwargs["handlers"][task["handler_key"]]
                    packets.append(asyncio.run(registration.handler(context)))
                    continue
                packets.append(
                    AgentResultPacket(
                        run_id="delegated-test-run",
                        task_id=task["task_id"],
                        parent_task_id=None,
                        attempt_id=1,
                        status="SUCCEEDED",
                        summary=f"bounded result for {task['role']}",
                        claims=(
                            AgentClaim("independent evidence collected", "SOURCE-BACKED", ("https://example.test",)),
                        ),
                        tokens_in=12,
                        tokens_out=8,
                    )
                )
            typed_packets = tuple(packets)
            return AgentSupervisorResult(
                run_id="delegated-test-run",
                graph_hash="a" * 64,
                graph_authority="native_runtime",
                status="COMPLETED",
                root_output=root_synthesizer(typed_packets),
                child_results=typed_packets,
            )

    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=make_client,
        subagent_application_factory=DelegationApplication,
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        write_calls: list[dict[str, object]] = []
        read_calls: list[dict[str, object]] = []
        read_spec = ToolSpec(
            name="fixture.lookup",
            description="Read one bounded fixture record",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            effect_class="read_only",
            capabilities=("read_only",),
            extension_id="lookup-fixture",
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id="lookup-fixture",
                version="1",
                description="Bounded read-only worker fixture",
                capabilities=("read_only",),
                tool_names=(read_spec.name,),
            ),
            ((read_spec, lambda values: read_calls.append(dict(values)) or {"status": "confirmed"}),),
        )
        write_spec = ToolSpec(
            name="fixture.write",
            description="Write one requested note after approval",
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            effect_class="external_write",
            capabilities=("external_write",),
            extension_id="fixture",
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id="fixture",
                version="1",
                description="Approval test tool",
                capabilities=("external_write",),
                tool_names=(write_spec.name,),
            ),
            ((write_spec, lambda values: write_calls.append(dict(values)) or {"written": True}),),
        )
        connection = service._connection_for_conversation("local", model_id="mock-model")
        descriptor = service._verified_model_descriptor(connection, "mock-model")
        assert descriptor is not None and "tool_calling:declared" in descriptor.capabilities, descriptor
        assert isinstance(descriptor.capabilities, (list, tuple)), type(descriptor.capabilities)
        assert service._parallel_workers_enabled()
        assert service._extension_registry.get_tool_spec(tool_name) is not None
        offered, _hashes = service._provider_tool_definitions("Compare two independent evidence streams", descriptor)
        assert any(item.name == tool_name for item in offered), offered
        assert any(item.name == read_spec.name for item in offered), offered
        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "Compare the two evidence streams.", "mode": "live"},
                )
            )
        )

        assert response["status"] == "ok", response
        assert response["result"]["pending_approval"] is True
        assert write_calls == []
        assert tool_name in client.offered_tool_names[0]
        pending_call = manager.snapshot.tool_calls[-1]
        approved = json.loads(
            service.dispatch(
                _request(
                    "approvals.resolve",
                    {
                        "conversation_id": "conv-1",
                        "approval_id": pending_call.call_id,
                        "decision": "approve",
                        "expected_revision": manager.snapshot.conversation.revision,
                    },
                )
            )
        )
        assert approved["status"] == "ok", approved
        assert write_calls == [{"target": "notes.txt"}]
        assert not service._pending_tool_approvals
        assert read_calls == [{"query": "fixture-status"}]
        delegation_calls = [call for call in manager.snapshot.tool_calls if call.tool_name == tool_name]
        assert json.loads(delegation_calls[0].result_content)["failed_task_ids"] == []
        assert client.calls[-1][0]
        assert len(client.calls) == 5
        assert tool_name in client.offered_tool_names[3]
        assert len(application_calls) == 1, [
            (call.tool_name, call.status, call.result_content) for call in manager.snapshot.tool_calls
        ]
        assert application_calls[0]["max_concurrency"] == 3
        plan = application_calls[0]["plan"]
        assert [item["handler_key"] for item in plan["tasks"]] == ["research", "model", "tool-3"]
        assert [item["side_effect_class"] for item in plan["tasks"]] == [
            "NetworkRead",
            "ModelInference",
            "ReadOnly",
        ]
        assert plan["tasks"][2]["capabilities"] == ["read_only"]
        assert factory_calls == [("local", "mock-model"), ("local", "mock-model")]
        assert "bounded result for source researcher" in str(client.calls[1][1])
        assert "fixture.lookup returned untrusted data" in str(client.calls[1][1])
        delegated_reply = json.loads(client.calls[1][1][0].outputs[0].output)
        delegated_evidence = json.loads(delegated_reply["evidence"])
        assert any(
            '"status":"confirmed"' in child["summary"]
            for child in delegated_evidence["children"]
            if child["task_id"] == 3
        )
        assert "SOURCE-BACKED" in str(client.calls[1][1])
        assistant_turn = next(turn for turn in manager.snapshot.turns if turn.role == "assistant")
        assert assistant_turn.status == "COMPLETED"
        assert assistant_turn.content == "I checked the delegated evidence."
    finally:
        service.close()


@pytest.mark.parametrize("denial", ["external-write", "not-visible", "catalog-changed"])
def test_provider_tool_worker_fails_closed_for_untrusted_or_stale_tools(
    tmp_path: Path,
    monkeypatch,
    denial: str,
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    calls: list[dict[str, object]] = []
    service = DesktopService(
        profile_root=tmp_path / denial,
        manager_factory=_FakeConversationManager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        subagent_application_factory=lambda _config: pytest.fail("rejected tool workers must not start a run"),
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        is_write = denial == "external-write"
        tool_name = f"fixture.{denial}"
        spec = ToolSpec(
            name=tool_name,
            description="A test tool that must not be delegated under this scope",
            input_schema={"type": "object"},
            effect_class="external_write" if is_write else "read_only",
            capabilities=("external_write",) if is_write else ("read_only",),
            extension_id=f"fixture-{denial}",
        )
        service._extension_registry.register(
            ExtensionManifest(
                extension_id=spec.extension_id,
                version="1",
                description="Delegated tool denial fixture",
                capabilities=spec.capabilities,
                tool_names=(spec.name,),
            ),
            ((spec, lambda arguments: calls.append(dict(arguments)) or {"unexpected": True}),),
        )
        catalog_revision = service._extension_registry.catalog_revision
        visible_hashes = () if denial == "not-visible" else ((spec.name, spec.descriptor_hash),)
        if denial == "catalog-changed":
            catalog_revision -= 1
        scope = desktop_service_module._ProviderToolTurnScope(
            connection_id="local",
            model_id="mock-model",
            owner_id="test-owner",
            catalog_revision=catalog_revision,
            conversation_id="conv-1",
            execution_id="exec-1",
            visible_tool_descriptor_hashes=visible_hashes,
        )
        token = desktop_service_module._ACTIVE_PROVIDER_TOOL_SCOPE.set(scope)
        try:
            with pytest.raises(ExtensionError):
                asyncio.run(
                    service._delegate_provider_subagents(
                        {
                            "task": "Inspect independent read-only evidence.",
                            "workers": [
                                {
                                    "handler_key": "tool",
                                    "role": "tool lookup",
                                    "prompt": "Read the requested record.",
                                    "tool_name": spec.name,
                                    "tool_input": {},
                                },
                                {"handler_key": "model", "role": "reviewer", "prompt": "Review findings."},
                            ],
                        }
                    )
                )
        finally:
            desktop_service_module._ACTIVE_PROVIDER_TOOL_SCOPE.reset(token)
        assert calls == []
        assert not scope._delegation_claimed
    finally:
        service.close()


@pytest.mark.parametrize(
    (
        "annotations",
        "request_auto_run",
        "grant_allowed",
        "runs_automatically",
        "read_only_candidate",
        "open_world_hint",
    ),
    [
        ({"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}, True, True, True, True, False),
        ({"readOnlyHint": True, "destructiveHint": False}, False, True, False, True, True),
        ({"readOnlyHint": True}, False, True, False, False, True),
        ({"readOnlyHint": True}, True, False, False, False, True),
        ({"readOnlyHint": True, "destructiveHint": True}, False, True, False, False, True),
        ({"readOnlyHint": True, "destructiveHint": True}, True, False, False, False, True),
        (None, False, True, False, False, True),
    ],
)
def test_mcp_read_only_auto_run_requires_explicit_test_bound_grant(
    tmp_path: Path,
    monkeypatch,
    annotations: dict[str, bool] | None,
    request_auto_run: bool,
    grant_allowed: bool,
    runs_automatically: bool,
    read_only_candidate: bool,
    open_world_hint: bool,
):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_MODEL_ID", "mock-model")
    root = tmp_path / "project"
    metadata = root / ".aegis" / "mcp" / "child" / "mcp.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('[mcp]\nid = "child"\ndescription = "Local child"\ntransport = "stdio"\n', encoding="utf-8")
    call_log = tmp_path / "mcp-calls.log"
    start_log = tmp_path / "mcp-starts.log"
    tool = {
        "name": "lookup",
        "description": "Read one status",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    if annotations is not None:
        tool["annotations"] = annotations
    server_code = f"""
import json, os, sys
with open(os.environ["AEGIS_MCP_START_LOG"], "a", encoding="utf-8") as log:
    log.write("started\\n")
tool = json.loads({json.dumps(tool)!r})
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "server/discover":
        response = {{"jsonrpc": "2.0", "id": request["id"], "error": {{"code": -32601, "message": "unknown method"}}}}
    elif method == "initialize":
        response = {{"jsonrpc": "2.0", "id": request["id"], "result": {{"protocolVersion": "2025-11-25", "capabilities": {{}}, "serverInfo": {{"name": "test", "version": "1"}}}}}}
    elif method == "tools/list":
        response = {{"jsonrpc": "2.0", "id": request["id"], "result": {{"tools": [tool]}}}}
    elif method == "tools/call":
        with open(os.environ["AEGIS_MCP_CALL_LOG"], "a", encoding="utf-8") as log:
            log.write(request["params"]["name"] + "\\n")
        response = {{"jsonrpc": "2.0", "id": request["id"], "result": {{"content": [{{"type": "text", "text": "status found"}}]}}}}
    else:
        continue
    sys.stdout.write(json.dumps(response) + "\\n")
    sys.stdout.flush()
"""
    manager = _FakeConversationManager()
    client = _FakeApprovalClient(tool_name="mcp.child.lookup", arguments={"query": "current status"})
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=lambda: manager,
        connection_catalog_factory=_ToolCapableConnectionCatalog,
        learning_factory=_FakeLearning,
        client_factory=lambda *_args, **_kwargs: client,
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open", {"workspace_path": str(root)})))["status"] == "ok"
        discovered = json.loads(service.dispatch(_request("extensions.discover")))
        server = discovered["result"]["mcp_servers"][0]
        approved = json.loads(
            service.dispatch(
                _request(
                    "extensions.set_state",
                    {
                        "kind": "mcp",
                        "id": "child",
                        "descriptor_hash": server["descriptor_hash"],
                        "enabled": False,
                        "approved": True,
                        "expected_revision": 0,
                    },
                )
            )
        )
        assert approved["status"] == "ok", approved
        transport_config = {
            "command": [sys.executable, "-c", server_code],
            "environment": {
                "AEGIS_MCP_CALL_LOG": str(call_log),
                "AEGIS_MCP_START_LOG": str(start_log),
            },
        }
        tested = json.loads(
            service.dispatch(
                _request(
                    "extensions.test_mcp",
                    {
                        "server_id": "child",
                        "descriptor_hash": server["descriptor_hash"],
                        "expected_revision": approved["result"]["revision"],
                        "config": transport_config,
                    },
                )
            )
        )
        assert tested["status"] == "ok", tested
        tested_tool = tested["result"]["tools"][0]
        assert tested_tool["read_only_candidate"] is read_only_candidate
        assert tested_tool["open_world_hint"] is open_world_hint
        activate_payload = {
            "server_id": "child",
            "descriptor_hash": server["descriptor_hash"],
            "approved": True,
            "expected_revision": approved["result"]["revision"],
            "config": transport_config,
            "test_token": tested["result"]["test_token"],
        }
        if request_auto_run:
            activate_payload |= {
                "auto_run_tool_hashes": [tested_tool["mcp_descriptor_hash"]],
            }
        unbound_activation = {
            key: value for key, value in activate_payload.items() if key != "test_token"
        }
        missing_test = json.loads(
            service.dispatch(_request("extensions.activate_mcp", unbound_activation))
        )
        assert missing_test["status"] == "error"
        assert missing_test["error"]["code"] == "MCP_TEST_REQUIRED"
        assert start_log.read_text(encoding="utf-8").splitlines() == ["started"]
        changed_config_payload = activate_payload | {
            "config": {
                **transport_config,
                "environment": {
                    "AEGIS_MCP_CALL_LOG": str(tmp_path / "different-log"),
                    "AEGIS_MCP_START_LOG": str(tmp_path / "different-start-log"),
                },
            }
        }
        stale_grant = json.loads(service.dispatch(_request("extensions.activate_mcp", changed_config_payload)))
        assert stale_grant["status"] == "error"
        assert stale_grant["error"]["code"] == "MCP_TEST_REQUIRED"
        assert start_log.read_text(encoding="utf-8").splitlines() == ["started"]
        activated = json.loads(service.dispatch(_request("extensions.activate_mcp", activate_payload)))
        if not grant_allowed:
            assert activated["status"] == "error"
            assert activated["error"]["code"] == "MCP_READ_ONLY_GRANT_INVALID"
            assert not call_log.exists()
            return
        assert activated["status"] == "ok", activated
        assert not any(snapshot.server_id == "child" for snapshot in service._mcp_test_snapshots.values())
        expected_effect = "network_read" if runs_automatically else "external_write"
        assert activated["result"]["tools"][0]["effect_class"] == expected_effect

        starts_after_activation = start_log.read_text(encoding="utf-8").splitlines()
        replayed_token = json.loads(
            service.dispatch(
                _request(
                    "extensions.activate_mcp",
                    activate_payload | {"expected_revision": activated["result"]["state"]["revision"]},
                )
            )
        )
        assert replayed_token["status"] == "error"
        assert replayed_token["error"]["code"] == "MCP_TEST_REQUIRED"
        assert start_log.read_text(encoding="utf-8").splitlines() == starts_after_activation

        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "conv-1", "message": "check current status", "mode": "live"},
                )
            )
        )
        assert response["status"] == "ok", response
        assert len(client.system_contexts) == len(client.calls)
        assert all(
            context is not None and "untrusted evidence" in context and "host approval" in context
            for context in client.system_contexts
        )
        call_log_lines = call_log.read_text(encoding="utf-8").splitlines() if call_log.is_file() else []
        if runs_automatically:
            assert response["result"].get("pending_approval") is not True
            assert call_log_lines[-1] == "lookup"
            assert not service._pending_tool_approvals
            assert len(client.calls) == 2
            assert manager.snapshot.tool_calls[0].status == "COMPLETED"
        else:
            assert response["result"]["pending_approval"] is True
            assert "lookup" not in call_log_lines
            assert service._pending_tool_approvals
            assert len(client.calls) == 1
            assert manager.snapshot.tool_calls[0].status == "REQUESTED"
    finally:
        service.close()


def test_desktop_service_rejects_unverified_image_input_before_persisting(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    learning = _FakeLearning()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
        connection_catalog_factory=_TextOnlyConnectionCatalog,
        learning_factory=lambda: learning,
        client_factory=lambda *_args, **_kwargs: pytest.fail("text-only model must reject the image before invoke"),
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {
                    "conversation_id": "conv-1",
                    "message": "describe this",
                    "mode": "live",
                    "attachments": [{"name": "screen.png", "mime_type": "image/png", "data": "aGVsbG8="}],
                },
            )
        )
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "MODEL_INPUT_UNSUPPORTED"
    assert learning.indexed == []


def test_desktop_service_enforces_verified_model_image_limit_before_provider_call(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    catalog = _LimitedVisionConnectionCatalog()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
        connection_catalog_factory=lambda: catalog,
        client_factory=lambda *_args, **_kwargs: pytest.fail("image limit must be checked before provider invocation"),
    )
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        listed = json.loads(service.dispatch(_request("models.list", {"connection_id": "local"})))
        assert listed["result"]["models"][0]["max_image_inputs"] == 3

        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "conv-1",
                        "message": "compare these images",
                        "mode": "live",
                        "attachments": [
                            {"name": f"image-{index}.png", "mime_type": "image/png", "data": "aGVsbG8="}
                            for index in range(4)
                        ],
                    },
                )
            )
        )
        assert response["status"] == "error"
        assert response["error"]["code"] == "MODEL_INPUT_LIMIT_EXCEEDED"
    finally:
        service.close()


def test_desktop_service_rejects_live_text_when_model_is_missing_from_catalog(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    learning = _FakeLearning()
    service = DesktopService(
        profile_root=tmp_path / "profile",
        manager_factory=_FakeConversationManager,
        connection_catalog_factory=_EmptyModelConnectionCatalog,
        learning_factory=lambda: learning,
        client_factory=lambda *_args, **_kwargs: pytest.fail("unverified model must not invoke the provider"),
    )
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    response = json.loads(
        service.dispatch(
            _request(
                "conversations.send",
                {"conversation_id": "conv-1", "message": "unverified", "mode": "live"},
            )
        )
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "MODEL_NOT_AVAILABLE"
    assert learning.indexed == []


def test_desktop_service_live_send_uses_real_local_compatible_endpoint(tmp_path: Path, monkeypatch):
    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("content-length", "0"))
            self.rfile.read(size)
            body = b'{"choices":[{"message":{"content":"local provider answer"}}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    monkeypatch.setenv("AEGIS_DESKTOP_PROVIDER_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1")
    try:
        service = DesktopService(profile_root=tmp_path / "profile")
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        assert (
            json.loads(
                service.dispatch(
                    _request(
                        "conversations.create",
                        {
                            "conversation_id": "local-live",
                            "title": "Local live",
                            "connection_id": "local",
                            "model_id": "local-model",
                        },
                    )
                )
            )["status"]
            == "ok"
        )
        response = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {"conversation_id": "local-live", "message": "hello endpoint", "mode": "live"},
                )
            )
        )
        assert response["status"] == "ok"
        assert response["result"]["output"] == "local provider answer"
        assert response["result"]["transcript_persisted"] is True
        reopened = DesktopService(profile_root=tmp_path / "profile")
        assert json.loads(reopened.dispatch(_request("workspace.open")))["status"] == "ok"
        assert (
            json.loads(reopened.dispatch(_request("conversations.read", {"conversation_id": "local-live"})))["status"]
            == "ok"
        )
        assert reopened._learning_for_request().search_past("hello endpoint", 8).results
    finally:
        server.shutdown()
        server.server_close()


def test_desktop_service_discovers_nested_vision_and_sends_image_end_to_end(tmp_path: Path, monkeypatch):
    observed: dict[str, object] = {}

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/v1/models"
            body = b'{"data":[{"id":"mistral-medium-3-5","capabilities":{"completion_chat":true,"vision":true},"max_context_length":32768}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            size = int(self.headers.get("content-length", "0"))
            observed["headers"] = dict(self.headers)
            observed["body"] = json.loads(self.rfile.read(size))
            body = b'{"choices":[{"message":{"content":"verified live answer"}}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        connected = json.loads(
            service.dispatch(
                _request(
                    "connections.connect",
                    {
                        "api_key": "mistral-test-secret",
                        "provider_kind": "openai-compatible",
                        "endpoint": f"http://127.0.0.1:{server.server_port}/v1",
                    },
                )
            )
        )
        assert connected["status"] == "ok"
        result = connected["result"]
        assert result["identity"]["provider_kind"] == "openai-compatible"
        assert result["secret_scope"] == "session"
        assert result["models"][0]["model_id"] == "mistral-medium-3-5"
        assert result["models"][0]["supports_vision"] is True
        assert result["models"][0]["reasoning_efforts"] == []
        assert "mistral-test-secret" not in json.dumps(connected)

        created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "verified-live",
                        "title": "Verified live",
                        "connection_id": "local",
                        "model_id": "mistral-medium-3-5",
                    },
                )
            )
        )
        assert created["status"] == "ok"
        sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "verified-live",
                        "message": "describe this",
                        "mode": "live",
                        "attachments": [{"name": "screen.png", "mime_type": "image/png", "data": "aGVsbG8="}],
                    },
                )
            )
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "verified live answer"
        body = observed["body"]
        assert isinstance(body, dict)
        assert body["model"] == "mistral-medium-3-5"
        assert body["messages"][0]["content"][1]["type"] == "image_url"
        assert observed["headers"]["Authorization"] == "Bearer mistral-test-secret"
    finally:
        service.close()
        server.shutdown()
        server.server_close()


def test_desktop_service_uses_openrouter_model_efforts_but_keeps_auto_default(tmp_path: Path, monkeypatch):
    observed: dict[str, object] = {}

    def discovery_request(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed.update({"catalog_url": url, "catalog_headers": headers})
        return DiscoveryResponse(
            200,
            b'{"data":[{"id":"custom/gateway-reasoning-model","architecture":{"input_modalities":["text","image"]},"supported_parameters":["reasoning_effort"],"reasoning":{"supported_efforts":["low","medium","high","xhigh"]}}]}',
        )

    def inference_request(url: str, headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"inference_url": url, "inference_headers": headers, "inference_body": json.loads(body)})
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"OpenRouter answer"}}]}')

    monkeypatch.setattr(discovery, "_request_json", discovery_request)
    monkeypatch.setattr(connection_clients, "_request_json", inference_request)
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        connected = json.loads(
            service.dispatch(
                _request("connections.connect", {"api_key": "sk-or-test-secret", "provider_kind": "openrouter"})
            )
        )
        assert connected["status"] == "ok"
        result = connected["result"]
        assert result["identity"]["provider_kind"] == "openrouter"
        assert result["models"][0]["supports_vision"] is True
        assert result["models"][0]["reasoning_efforts"] == ["low", "medium", "high", "xhigh"]
        assert observed["catalog_url"] == "https://openrouter.ai/api/v1/models/user?offset=0&limit=1000"
        assert observed["catalog_headers"]["Authorization"] == "Bearer sk-or-test-secret"
        assert "sk-or-test-secret" not in json.dumps(connected)

        created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "openrouter-live",
                        "title": "OpenRouter live",
                        "connection_id": "local",
                        "model_id": "custom/gateway-reasoning-model",
                    },
                )
            )
        )
        assert created["status"] == "ok"
        sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "openrouter-live",
                        "message": "reason carefully",
                        "mode": "live",
                        "reasoning_effort": "auto",
                    },
                )
            )
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "OpenRouter answer"
        assert observed["inference_url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert "reasoning_effort" not in observed["inference_body"]
        assert observed["inference_headers"]["Authorization"] == "Bearer sk-or-test-secret"

        selected = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "openrouter-live",
                        "message": "use high reasoning effort",
                        "mode": "live",
                        "reasoning_effort": "high",
                    },
                )
            )
        )
        assert selected["status"] == "ok"
        assert observed["inference_body"]["reasoning_effort"] == "high"
    finally:
        service.close()


def test_desktop_service_routes_ambiguous_sk_key_to_explicit_deepseek_host_and_effort(tmp_path: Path, monkeypatch):
    observed: dict[str, object] = {}

    def discovery_request(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed.update({"catalog_url": url, "catalog_headers": headers})
        return DiscoveryResponse(
            200,
            b'{"data":[{"id":"deepseek-flash"},{"id":"deepseek-v4-pro"},{"id":"deepseek-v4-flash"}]}',
        )

    def inference_request(url: str, headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"inference_url": url, "inference_headers": headers, "inference_body": json.loads(body)})
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"DeepSeek answer"}}]}')

    monkeypatch.setattr(discovery, "_request_json", discovery_request)
    monkeypatch.setattr(connection_clients, "_request_json", inference_request)
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        connected = json.loads(
            service.dispatch(
                _request(
                    "connections.connect",
                    {
                        "api_key": "sk-deepseek-test-secret",
                        "provider_kind": "deepseek",
                        "endpoint": "https://api.deepseek.com",
                    },
                )
            )
        )
        assert connected["status"] == "ok"
        result = connected["result"]
        assert result["identity"]["provider_kind"] == "deepseek"
        assert result["identity"]["provider_label"] == "DeepSeek"
        assert result["identity"]["requires_endpoint"] is False
        assert result["record"]["protocol"] == "chat-completions"
        assert result["models"][0]["reasoning_efforts"] == ["none", "low", "high", "max"]
        assert result["models"][0]["model_id"] == "deepseek-flash"
        assert result["models"][0]["supports_vision"] is True
        assert result["models"][1]["model_id"] == "deepseek-v4-pro"
        assert result["models"][1]["supports_vision"] is False
        assert result["models"][2]["model_id"] == "deepseek-v4-flash"
        assert result["models"][2]["reasoning_efforts"] == ["none", "low", "high", "max"]
        assert observed["catalog_url"] == "https://api.deepseek.com/models"
        assert observed["catalog_headers"]["Authorization"] == "Bearer sk-deepseek-test-secret"
        assert "sk-deepseek-test-secret" not in json.dumps(connected)

        created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "deepseek-live",
                        "title": "DeepSeek",
                        "connection_id": "local",
                        "model_id": "deepseek-v4-pro",
                    },
                )
            )
        )
        assert created["status"] == "ok"
        sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "deepseek-live",
                        "message": "reason deeply",
                        "mode": "live",
                        "reasoning_effort": "max",
                    },
                )
            )
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "DeepSeek answer"
        assert observed["inference_url"] == "https://api.deepseek.com/chat/completions"
        assert observed["inference_body"]["reasoning_effort"] == "max"
        assert observed["inference_headers"]["Authorization"] == "Bearer sk-deepseek-test-secret"

        flash_created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "deepseek-flash-none",
                        "title": "DeepSeek V4 Flash",
                        "connection_id": "local",
                        "model_id": "deepseek-v4-flash",
                    },
                )
            )
        )
        assert flash_created["status"] == "ok"
        flash_sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "deepseek-flash-none",
                        "message": "answer without thinking",
                        "mode": "live",
                        "reasoning_effort": "none",
                    },
                )
            )
        )
        assert flash_sent["status"] == "ok"
        assert observed["inference_body"]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in observed["inference_body"]

        vision_created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "deepseek-vision-live",
                        "title": "DeepSeek vision",
                        "connection_id": "local",
                        "model_id": "deepseek-flash",
                    },
                )
            )
        )
        assert vision_created["status"] == "ok"
        vision_sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "deepseek-vision-live",
                        "message": "describe this image",
                        "attachments": [{"name": "screen.png", "mime_type": "image/png", "data": "aGVsbG8="}],
                        "mode": "live",
                    },
                )
            )
        )
        assert vision_sent["status"] == "ok"
        content = observed["inference_body"]["messages"][-1]["content"]
        assert "describe this image" in content[0]["text"]
        assert content[-1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
    finally:
        service.close()


def test_desktop_service_connects_xai_key_and_uses_verified_responses_image_and_effort_flow(
    tmp_path: Path, monkeypatch
):
    observed: dict[str, object] = {}

    def discovery_request(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed["catalog_url"] = url
        observed["catalog_headers"] = headers
        return DiscoveryResponse(
            200,
            b'{"models":[{"id":"grok-4.6","input_modalities":["text","image"],"context_length":256000,"capabilities":{"reasoning_effort":["low","medium","high","xhigh"]}}]}',
        )

    def inference_request(url: str, headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"inference_url": url, "inference_headers": headers, "inference_body": json.loads(body)})
        return ConnectionResponse(
            200,
            b'{"output":[{"type":"reasoning","summary":[{"type":"summary_text","text":"not user-visible"}]},{"type":"message","role":"assistant","content":[{"type":"output_text","text":"xAI response verified"}]}]}',
        )

    monkeypatch.setattr(discovery, "_request_json", discovery_request)
    monkeypatch.setattr(connection_clients, "_request_json", inference_request)
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        connected = json.loads(
            service.dispatch(_request("connections.connect", {"api_key": "xai-test-secret", "provider_kind": "xai"}))
        )
        assert connected["status"] == "ok"
        result = connected["result"]
        assert result["identity"]["protocol"] == "xai-responses"
        assert result["record"]["protocol"] == "xai-responses"
        assert result["models"][0]["model_id"] == "grok-4.6"
        assert result["models"][0]["supports_vision"] is True
        assert result["models"][0]["reasoning_efforts"] == ["low", "medium", "high", "xhigh"]
        assert observed["catalog_url"] == "https://api.x.ai/v1/language-models"
        assert "xai-test-secret" not in json.dumps(connected)

        created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "xai-live",
                        "title": "xAI live",
                        "connection_id": "local",
                        "model_id": "grok-4.6",
                    },
                )
            )
        )
        assert created["status"] == "ok"
        unsupported_image = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "xai-live",
                        "message": "describe this",
                        "mode": "live",
                        "attachments": [{"name": "screen.webp", "mime_type": "image/webp", "data": "aGVsbG8="}],
                    },
                )
            )
        )
        assert unsupported_image["status"] == "error"
        assert unsupported_image["error"]["code"] == "MODEL_INPUT_FORMAT_UNSUPPORTED"
        assert "inference_url" not in observed

        sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "xai-live",
                        "message": "describe this",
                        "mode": "live",
                        "reasoning_effort": "xhigh",
                        "attachments": [{"name": "screen.png", "mime_type": "image/png", "data": "aGVsbG8="}],
                    },
                )
            )
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "xAI response verified"
        body = observed["inference_body"]
        assert body["model"] == "grok-4.6"
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "xhigh"}
        assert body["input"][0]["content"][-1] == {
            "type": "input_image",
            "image_url": "data:image/png;base64,aGVsbG8=",
        }
        assert observed["inference_url"] == "https://api.x.ai/v1/responses"
        assert observed["inference_headers"]["Authorization"] == "Bearer xai-test-secret"
    finally:
        service.close()


def test_desktop_service_connects_openai_key_and_uses_verified_responses_model_configuration(
    tmp_path: Path, monkeypatch
):
    observed: dict[str, object] = {}

    def discovery_request(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed["catalog_url"] = url
        observed["catalog_headers"] = headers
        return DiscoveryResponse(200, b'{"data":[{"id":"gpt-5.6-sol","owned_by":"openai"}]}')

    def inference_request(url: str, headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"inference_url": url, "inference_headers": headers, "inference_body": json.loads(body)})
        return ConnectionResponse(
            200,
            b'{"output":[{"type":"reasoning","summary":[{"type":"summary_text","text":"private thought"}]},{"type":"message","role":"assistant","content":[{"type":"output_text","text":"OpenAI response verified"}]}],"usage":{"input_tokens":100,"input_tokens_details":{"cached_tokens":64},"output_tokens":12}}',
        )

    monkeypatch.setattr(discovery, "_request_json", discovery_request)
    monkeypatch.setattr(connection_clients, "_request_json", inference_request)
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")
    try:
        assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
        connected = json.loads(
            service.dispatch(
                _request(
                    "connections.connect",
                    {"api_key": "sk-proj-openai-test-secret", "provider_kind": "openai"},
                )
            )
        )
        assert connected["status"] == "ok"
        result = connected["result"]
        assert result["identity"]["protocol"] == "openai-responses"
        assert result["record"]["protocol"] == "openai-responses"
        assert result["models"][0]["reasoning_efforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
        assert observed["catalog_url"] == "https://api.openai.com/v1/models"
        assert "sk-proj-openai-test-secret" not in json.dumps(connected)

        created = json.loads(
            service.dispatch(
                _request(
                    "conversations.create",
                    {
                        "conversation_id": "openai-live",
                        "title": "OpenAI live",
                        "connection_id": "local",
                        "model_id": "gpt-5.6-sol",
                    },
                )
            )
        )
        assert created["status"] == "ok"
        sent = json.loads(
            service.dispatch(
                _request(
                    "conversations.send",
                    {
                        "conversation_id": "openai-live",
                        "message": "Describe this image",
                        "mode": "live",
                        "reasoning_effort": "high",
                        "attachments": [{"name": "screen.webp", "mime_type": "image/webp", "data": "aGVsbG8="}],
                    },
                )
            )
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "OpenAI response verified"
        assert sent["result"]["prompt_cache"]["usage"] == {
            "provider_input_tokens": 100,
            "cached_read_tokens": 64,
            "cache_write_tokens": 0,
            "output_tokens": 12,
            "cache_status": "HIT",
        }
        body = observed["inference_body"]
        assert body["model"] == "gpt-5.6-sol"
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "high"}
        assert body["prompt_cache_key"].startswith("aegis-cache-")
        assert "prompt_cache_options" not in body
        assert body["input"][0]["content"][-1] == {
            "type": "input_image",
            "image_url": "data:image/webp;base64,aGVsbG8=",
        }
        assert observed["inference_url"] == "https://api.openai.com/v1/responses"
        assert observed["inference_headers"]["Authorization"] == "Bearer sk-proj-openai-test-secret"
        assert "private thought" not in sent["result"]["output"]
    finally:
        service.close()


def _ambiguous_tool_service(tmp_path: Path) -> tuple[DesktopService, _FakeConversationManager, _FakeToolCall]:
    manager = _FakeConversationManager()
    call = _FakeToolCall(
        call_id="tool-call-unknown",
        conversation_id="conv-1",
        request_turn_id="turn-unknown",
        result_turn_id=None,
        tool_name="fixture.external_write",
        arguments_json='{"target":"remote-record"}',
        result_content=None,
        status="AMBIGUOUS",
        revision=3,
    )
    manager.snapshot = _FakeSnapshot(
        replace(manager.snapshot.conversation, revision=4),
        tool_calls=(call,),
    )
    service = DesktopService(
        profile_root=tmp_path / "profile",
        profile_id="local-profile",
        manager_factory=lambda: manager,
    )
    service._opened_root = (tmp_path / "profile").resolve()
    service._manager = manager
    return service, manager, call


def test_ambiguous_tool_reconciliation_requires_explicit_user_assertion(tmp_path: Path):
    service, manager, call = _ambiguous_tool_service(tmp_path)
    try:
        assert "tool_calls.reconcile" in service.ready_payload()["commands"]
        for confirmation in (None, False, 1, "true"):
            payload = {
                "conversation_id": "conv-1",
                "call_id": call.call_id,
                "confirmed_not_applied": confirmation,
                "expected_revision": manager.snapshot.conversation.revision,
            }
            response = json.loads(service.dispatch(_request("tool_calls.reconcile", payload)))
            assert response["status"] == "error"
            assert response["error"]["code"] == "INVALID_ARGUMENT"
            assert manager.snapshot.tool_calls[0].status == "AMBIGUOUS"

        missing = json.loads(
            service.dispatch(
                _request(
                    "tool_calls.reconcile",
                    {
                        "conversation_id": "conv-1",
                        "call_id": call.call_id,
                        "expected_revision": manager.snapshot.conversation.revision,
                    },
                )
            )
        )
        assert missing["error"]["code"] == "INVALID_ARGUMENT"
        assert manager.snapshot.tool_calls[0].status == "AMBIGUOUS"
    finally:
        service.close()


def test_ambiguous_tool_reconciliation_records_user_assertion_without_rerunning_tool(tmp_path: Path, monkeypatch):
    service, manager, call = _ambiguous_tool_service(tmp_path)
    monkeypatch.setattr(
        desktop_service_module,
        "build_conversation_inspection",
        lambda _snapshot, **_kwargs: {
            "revision": _snapshot.conversation.revision,
            "approvals": [
                {
                    "approval_id": call.call_id,
                    "tool_name": call.tool_name,
                    "argument_preview": [],
                }
            ],
        },
    )
    try:
        inspection = json.loads(
            service.dispatch(_request("conversations.inspect", {"conversation_id": "conv-1"}))
        )["result"]["inspection"]
        approval = inspection["approvals"][0]
        assert approval["can_reconcile"] is True
        assert approval["can_resolve"] is False

        response = json.loads(
            service.dispatch(
                _request(
                    "tool_calls.reconcile",
                    {
                        "conversation_id": "conv-1",
                        "call_id": call.call_id,
                        "confirmed_not_applied": True,
                        "expected_revision": inspection["revision"],
                    },
                )
            )
        )
        assert response["status"] == "ok"
        assert response["result"] == {
            "call_id": call.call_id,
            "status": "CANCELLED",
            "conversation_revision": 5,
        }
        reconciled = manager.snapshot.tool_calls[0]
        assert reconciled.status == "CANCELLED"
        assert reconciled.result_content.startswith("USER_ASSERTION_ONLY:")
        assert "did not verify this independently" in reconciled.result_content
        assert not service._pending_tool_approvals
    finally:
        service.close()


def test_ambiguous_tool_reconciliation_rejects_stale_or_active_conversation(tmp_path: Path, monkeypatch):
    service, manager, call = _ambiguous_tool_service(tmp_path)
    try:
        stale = json.loads(
            service.dispatch(
                _request(
                    "tool_calls.reconcile",
                    {
                        "conversation_id": "conv-1",
                        "call_id": call.call_id,
                        "confirmed_not_applied": True,
                        "expected_revision": manager.snapshot.conversation.revision - 1,
                    },
                )
            )
        )
        assert stale["error"]["code"] == "CONVERSATION_CONFLICT"
        assert manager.snapshot.tool_calls[0].status == "AMBIGUOUS"

        service._desktop_runs["active-run"] = SimpleNamespace(
            conversation_id="conv-1",
            owner_id="local-profile",
            status="RUNNING",
            cancel_event=Event(),
            cancel_requested_at_ms=None,
            lock=Lock(),
        )
        monkeypatch.setattr(
            desktop_service_module,
            "build_conversation_inspection",
            lambda _snapshot, **_kwargs: {
                "approvals": [
                    {
                        "approval_id": call.call_id,
                        "tool_name": call.tool_name,
                        "argument_preview": [],
                    }
                ],
            },
        )
        inspection = json.loads(
            service.dispatch(_request("conversations.inspect", {"conversation_id": "conv-1"}))
        )["result"]["inspection"]
        assert inspection["approvals"][0]["can_reconcile"] is False
        active = json.loads(
            service.dispatch(
                _request(
                    "tool_calls.reconcile",
                    {
                        "conversation_id": "conv-1",
                        "call_id": call.call_id,
                        "confirmed_not_applied": True,
                        "expected_revision": manager.snapshot.conversation.revision,
                    },
                )
            )
        )
        assert active["error"]["code"] == "CONVERSATION_BUSY"
        assert manager.snapshot.tool_calls[0].status == "AMBIGUOUS"
    finally:
        service.close()
