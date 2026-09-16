import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from aegis_cognition.desktop_service import DesktopService, _select_source_paths_for_prompt
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
