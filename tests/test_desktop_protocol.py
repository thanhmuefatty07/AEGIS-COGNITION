import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
import time
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from aegis_cognition.desktop_service import DesktopService, _select_source_paths_for_prompt
from aegis_cognition.subagents import (
    AGENT_MESSAGE_SCHEMA_V1,
    AgentCancellationError,
    AgentMessage,
    AgentMessageJournal,
    AgentResultPacket,
    AgentSupervisorResult,
)
from core.python.aegis.desktop_protocol import (
    DesktopCommandRouter,
    DesktopProtocolError,
    decode_request,
)


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
class _FakeSnapshot:
    conversation: _FakeConversation
    turns: tuple[_FakeTurn, ...] = ()
    executions: tuple[_FakeExecution, ...] = ()
    checkpoints: tuple = ()
    tool_calls: tuple = ()


class _FakeConversationManager:
    def __init__(self) -> None:
        self.snapshot = _FakeSnapshot(
            _FakeConversation("conv-1", "local-profile", "Smoke", "local", "mock-model")
        )

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
            tuple(updated if item.execution_id == execution.execution_id else item for item in self.snapshot.executions),
        )
        return updated


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


def test_desktop_protocol_accepts_allowlisted_command_and_routes_typed_payload():
    observed: list[dict] = []
    router = DesktopCommandRouter(
        {"workspace.snapshot": lambda payload: observed.append(payload) or {"revision": 3}}
    )

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
    frames = b"\n".join(
        [
            _request("workspace.open"),
            _request("workspace.snapshot"),
            _request("service.shutdown"),
        ]
    ) + b"\n"
    output = io.BytesIO()

    assert service.run(io.BytesIO(frames), output) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]

    assert responses[0]["schema"] == "aegis-desktop-ready-v1"
    assert responses[0]["protocol_version"] == 1
    assert responses[0]["commands"] == sorted(service._handlers())
    assert "runs.start" in responses[0]["allowed_commands"]
    assert "runs.start" not in responses[0]["commands"]
    assert responses[1]["status"] == "ok"
    assert responses[1]["result"]["open"] is True
    assert Path(responses[1]["result"]["state_path"]).parts[-2:] == (".aegis", "state.db")
    assert responses[2]["result"]["open"] is True
    assert responses[3]["result"] == {"stopping": True}
    assert service._stop_requested is True


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


def test_desktop_service_lists_canonical_conversations_for_the_renderer(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile", profile_id="local-profile")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"
    assert json.loads(
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
    )["status"] == "ok"

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


def test_desktop_service_runs_subagents_through_the_canonical_application(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=_FakeConnectionCatalog,
        client_factory=lambda *_args, **_kwargs: _FakeSubagentClient(),
        subagent_application_factory=_FakeSubagentApplication,
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
    events = json.loads(
        service.dispatch(_request("subagents.events", {"run_id": start["run_id"], "max_messages": 4}))
    )
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


def test_desktop_service_exposes_hash_bound_code_reuse_workflow(tmp_path: Path, monkeypatch):
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
                    "adaptation_tokens": 4,
                    "verification_tokens": 3,
                },
            )
        )
    )
    assert assessment["status"] == "ok"
    assert assessment["result"]["assessment"]["decision"] == "REUSE_WITH_PATCH"
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
    assert materialized["status"] == "ok"
    assert (root / "copied.py").read_text(encoding="utf-8") == "def answer():\n    return 42\n"
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
    assert duplicate["error"]["code"] == "CODE_REUSE_REJECTED"


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
    second = json.loads(
        service.dispatch(
            _request("workspace.open", {"workspace_path": str(tmp_path / "two")})
        )
    )
    assert second["status"] == "error"
    assert second["error"]["code"] == "WORKSPACE_ALREADY_OPEN"


def test_desktop_service_switches_workspace_without_changing_open_contract(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "one")
    assert json.loads(service.dispatch(_request("workspace.open")))["status"] == "ok"

    target = tmp_path / "two"
    switched = json.loads(
        service.dispatch(_request("workspace.switch", {"workspace_path": str(target)}))
    )

    assert switched["status"] == "ok"
    assert Path(switched["result"]["workspace_path"]) == target.resolve()
    assert Path(service._state_path or "") == (target / ".aegis" / "state.db").resolve()
    assert Path(os.environ["AEGIS_SESSION_DB_PATH"]) == (target / ".aegis" / "state.db").resolve()


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


def test_source_prompt_path_selection_is_bounded_and_query_ranked():
    records = [{"relative_path": f"src/{index:03d}-module.py"} for index in range(70)]
    records.append({"relative_path": "src/repository.py"})

    selected = _select_source_paths_for_prompt(records, "inspect the repository")

    assert len(selected) == 17
    assert selected[0] == "src/repository.py"
    assert "src/069-module.py" not in selected
    assert _select_source_paths_for_prompt(records, "show the project files")[:16] == sorted(
        item["relative_path"] for item in records
    )[:16]


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
        assert json.loads(
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
        )["status"] == "ok"
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
        assert json.loads(
            reopened.dispatch(_request("conversations.read", {"conversation_id": "local-live"}))
        )["status"] == "ok"
        assert reopened._learning_for_request().search_past("hello endpoint", 8).results
    finally:
        server.shutdown()
        server.server_close()
