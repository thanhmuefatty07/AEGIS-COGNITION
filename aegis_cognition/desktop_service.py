"""Local desktop sidecar for the bounded AEGIS command protocol.

The service owns transport and lifecycle only. Durable state and authorization
remain in the Rust-backed Python adapters; the desktop renderer never receives
direct database, filesystem, shell, or provider access.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, TextIO, cast
from urllib.parse import urlsplit

from .config import trust_policy_hash
from .application import AgentApplication
from .code_reuse import (
    CodeReuseError,
    assess_code_reuse,
    build_local_reuse_candidate,
    materialize_exact,
)
from .config import AgentConfig
from .runtime import (
    coordinated_runtime_task_sync,
    native_runtime_available,
    normalize_runtime_trust_level,
)
from .subagents import AgentCancellationError, AgentMessage, AgentMessageJournal
from .verification import VerificationFacade, VerificationSessionError

try:
    from core.python.aegis.code_intelligence import (
        BoundedSnapshotWatcher,
        CodeIntelligenceError,
        NativeSnapshotWatcher,
        SourceMapper,
        SourceSnapshot,
        build_repository_map,
    )
except ImportError:
    from aegis.code_intelligence import (  # type: ignore[import-not-found]
        BoundedSnapshotWatcher,
        CodeIntelligenceError,
        NativeSnapshotWatcher,
        SourceMapper,
        SourceSnapshot,
        build_repository_map,
    )

try:
    from core.python.aegis.connection_clients import OpenAICompatibleClient
    from core.python.aegis.connections import ConnectionCatalog
    from core.python.aegis.context_compiler import ContextCompiler
    from core.python.aegis.conversations import ConversationManager
    from core.python.aegis.desktop_projection import build_conversation_inspection, build_subagent_graph
    from core.python.aegis.learning import LearningManager
    from core.python.aegis.desktop_protocol import (
        ALLOWED_COMMANDS,
        MAX_FRAME_BYTES,
        PROTOCOL_VERSION,
        DesktopCommandRouter,
        DesktopProtocolError,
        encode_response,
    )
except ImportError:
    from aegis.connection_clients import OpenAICompatibleClient  # type: ignore[import-not-found]
    from aegis.connections import ConnectionCatalog  # type: ignore[import-not-found]
    from aegis.context_compiler import ContextCompiler  # type: ignore[import-not-found]
    from aegis.conversations import (  # type: ignore[import-not-found]
        ConversationManager,
    )
    from aegis.desktop_projection import build_conversation_inspection, build_subagent_graph  # type: ignore[import-not-found]
    from aegis.learning import LearningManager  # type: ignore[import-not-found]
    from aegis.desktop_protocol import (  # type: ignore[import-not-found]
        ALLOWED_COMMANDS,
        MAX_FRAME_BYTES,
        PROTOCOL_VERSION,
        DesktopCommandRouter,
        DesktopProtocolError,
        encode_response,
    )


READY_SCHEMA = "aegis-desktop-ready-v1"
SERVICE_VERSION_FALLBACK = "0.1.0"
MAX_PATH_LENGTH = 4096
MAX_CLONE_URL_LENGTH = 2048
MAX_CLONE_TIMEOUT_SECONDS = 120
MAX_PROFILE_ID_LENGTH = 128
MAX_MESSAGE_LENGTH = 256 * 1024
MAX_PROMPT_LENGTH = 64 * 1024
MAX_HISTORY_TURNS = 32
MAX_SOURCE_FILES_IN_PROMPT = 64
MAX_BASELINE_SOURCE_FILES_IN_PROMPT = 16
MAX_SUBAGENT_TASK_CHARS = 64 * 1024
MAX_SUBAGENT_RESULT_CHARS = 16 * 1024
MAX_SUBAGENT_TASKS = 64
MAX_SUBAGENT_CONCURRENCY = 32
MAX_SUBAGENT_EVENT_RUNS = 32
MAX_SUBAGENT_EVENT_MESSAGES = 256
MAX_CODE_REUSE_LINE = 1_000_000
MAX_CODE_REUSE_TOKENS = 10_000_000
_SCP_GIT_SOURCE = re.compile(r"^git@([A-Za-z0-9.-]+):([A-Za-z0-9._/-]+)$")
_CLONE_HOSTS = frozenset({"bitbucket.org", "codeberg.org", "github.com", "gitlab.com"})
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*$")


@dataclass(slots=True)
class _SubagentRunState:
    """Host-owned state for a bounded background subagent run."""

    run_id: str
    journal: AgentMessageJournal
    status: str = "RUNNING"
    result_payload: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    cancel_requested_at_ms: int | None = None
    cancel_supported: bool = False
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    finished_at_ms: int | None = None
    thread: threading.Thread | None = field(default=None, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class DesktopServiceError(DesktopProtocolError):
    """An expected service-level failure that is safe to expose to the UI."""


def _service_version() -> str:
    # The frozen sidecar has no package metadata at runtime.  Keep the
    # protocol version deterministic and allow packaging to stamp an explicit
    # value without importing the large ``importlib.metadata``/setuptools
    # graph during PyInstaller analysis.
    return os.environ.get("AEGIS_DESKTOP_SERVICE_VERSION", SERVICE_VERSION_FALLBACK)


def _default_profile_root(profile_id: str) -> Path:
    configured = os.environ.get("AEGIS_DESKTOP_PROFILE_ROOT")
    if configured:
        return Path(configured)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "AEGIS" / "profiles" / profile_id


def _require_text(payload: Mapping[str, Any], key: str, *, max_length: int = MAX_PATH_LENGTH) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or "\x00" in value:
        raise DesktopServiceError("INVALID_ARGUMENT", f"{key} must be a bounded non-empty string")
    return value


def _require_revision(payload: Mapping[str, Any], key: str = "expected_revision") -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DesktopServiceError("INVALID_ARGUMENT", f"{key} must be a non-negative integer")
    return value


def _require_top_k(payload: Mapping[str, Any]) -> int:
    value = payload.get("top_k", 8)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise DesktopServiceError("INVALID_ARGUMENT", "top_k must be an integer between 1 and 100")
    return value


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        mapping = cast(Mapping[Any, Any], value)
        return {str(key): _json_value(item) for key, item in mapping.items()}
    if isinstance(value, tuple | list):
        items = cast(tuple[Any, ...] | list[Any], value)
        return [_json_value(item) for item in items]
    return value


def _select_source_paths_for_prompt(file_records: list[dict[str, Any]], message: str) -> list[str]:
    """Keep a small stable workspace prefix and rank lexical path candidates."""

    paths = sorted(
        {
            str(item.get("relative_path", "")).strip()
            for item in file_records
            if str(item.get("relative_path", "")).strip()
        }
    )
    if len(paths) <= MAX_SOURCE_FILES_IN_PROMPT:
        return paths
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "project",
        "file",
        "code",
        "memory",
        "agent",
        "workspace",
        "please",
        "show",
        "what",
        "how",
    }
    terms = {term for term in re.findall(r"[a-z0-9_]{3,}", message.casefold()) if term not in stop_words}
    scored = sorted(
        (
            sum(1 for term in terms if term in path.casefold()),
            path,
        )
        for path in paths
    )
    relevant = [path for score, path in sorted(scored, key=lambda item: (-item[0], item[1])) if score > 0]
    baseline = paths[:MAX_BASELINE_SOURCE_FILES_IN_PROMPT]
    selected: list[str] = []
    for path in [*relevant, *baseline]:
        if path not in selected:
            selected.append(path)
        if len(selected) == MAX_SOURCE_FILES_IN_PROMPT:
            break
    return selected


class DesktopService:
    """Serve newline-delimited protocol frames over stdin/stdout."""

    def __init__(
        self,
        *,
        profile_root: Path | None = None,
        profile_id: object = "local-profile",
        manager_factory: Callable[[], Any] = ConversationManager,
        connection_catalog_factory: Callable[[], Any] = ConnectionCatalog,
        learning_factory: Callable[[], Any] = LearningManager,
        client_factory: Callable[..., Any] = OpenAICompatibleClient,
        subagent_application_factory: Callable[..., Any] = AgentApplication,
        trust_level: str | None = None,
    ) -> None:
        if (
            not isinstance(profile_id, str)
            or not profile_id.strip()
            or len(profile_id) > MAX_PROFILE_ID_LENGTH
            or profile_id in {".", ".."}
            or "/" in profile_id
            or "\\" in profile_id
            or "\x00" in profile_id
        ):
            raise ValueError("profile_id must be a bounded non-empty string")
        self.profile_id = profile_id
        configured_trust = os.environ.get("AEGIS_TRUST_LEVEL", "DEV") if trust_level is None else trust_level
        self.trust_level = normalize_runtime_trust_level(configured_trust)
        self.trust_policy_hash = trust_policy_hash(self.trust_level)
        self.profile_root = (profile_root or _default_profile_root(profile_id)).expanduser()
        self._manager_factory = manager_factory
        self._connection_catalog_factory = connection_catalog_factory
        self._learning_factory = learning_factory
        self._client_factory = client_factory
        self._subagent_application_factory = subagent_application_factory
        self._manager: Any | None = None
        self._connections: Any | None = None
        self._learning: Any | None = None
        self._opened_root: Path | None = None
        self._state_path: Path | None = None
        self._source_mapper: SourceMapper | None = None
        self._source_snapshot: SourceSnapshot | None = None
        self._source_watcher: NativeSnapshotWatcher | None = None
        self._stop_requested = False
        self._subagent_event_journals: OrderedDict[str, AgentMessageJournal] = OrderedDict()
        self._subagent_runs: OrderedDict[str, _SubagentRunState] = OrderedDict()
        self._subagent_event_lock = threading.RLock()
        self._verification_facade = VerificationFacade()
        self._router = DesktopCommandRouter(self._handlers())

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Mapping[str, Any]]]:
        return {
            "service.shutdown": self._shutdown,
            "workspace.open": self._open_workspace,
            "workspace.switch": self._switch_workspace,
            "workspace.clone": self._clone_workspace,
            "workspace.snapshot": self._workspace_snapshot,
            "workspace.source_snapshot": self._workspace_source_snapshot,
            "connections.list": self._list_connections,
            "connections.save": self._save_connection,
            "connections.discover": self._discover_connection_models,
            "connections.disable": self._disable_connection,
            "models.list": self._list_models,
            "conversations.create": self._create_conversation,
            "conversations.list": self._list_conversations,
            "conversations.read": self._read_conversation,
            "conversations.inspect": self._inspect_conversation,
            "conversations.send": self._send_message,
            "conversations.switch_model": self._switch_model,
            "subagents.run": self._run_subagents,
            "subagents.start": self._start_subagents,
            "subagents.cancel": self._cancel_subagents,
            "subagents.status": self._subagent_status,
            "subagents.events": self._read_subagent_events,
            "subagents.graph": self._subagent_graph,
            "code_reuse.assess": self._assess_code_reuse,
            "code_reuse.materialize": self._materialize_code_reuse,
            "memory.search": self._search_memories,
            "memory.inspect": self._inspect_memory,
            "memory.capture": self._capture_memory,
            "memory.correct": self._correct_memory,
            "memory.forget": self._forget_memory,
            "memory.restore": self._restore_memory,
            "memory.purge": self._purge_memory,
            "verification.inspect_project": self._verification_inspect_project,
            "verification.create_contract": self._verification_create_contract,
            "verification.start_session": self._verification_start_session,
            "verification.get_agent_packet": self._verification_get_agent_packet,
            "verification.observe_change": self._verification_observe_change,
            "verification.get_feedback": self._verification_get_feedback,
            "verification.propose_test_change": self._verification_propose_test_change,
            "verification.evaluate_test_change": self._verification_evaluate_test_change,
            "verification.apply_test_change": self._verification_apply_test_change,
            "verification.request_deep_run": self._verification_request_deep_run,
            "verification.inspect_run": self._verification_inspect_run,
            "verification.cancel_run": self._verification_cancel_run,
            "verification.resume_session": self._verification_resume_session,
            "verification.read_report": self._verification_read_report,
        }

    def ready_payload(self) -> dict[str, Any]:
        available_commands = sorted(self._handlers())
        return {
            "schema": READY_SCHEMA,
            "protocol_version": PROTOCOL_VERSION,
            "service_version": _service_version(),
            "pid": os.getpid(),
            "profile_id": self.profile_id,
            "native_runtime_available": native_runtime_available(),
            # The allowlist is a policy boundary; only registered handlers are
            # executable in this service instance. Advertising the latter
            # prevents a desktop client from treating reserved commands as
            # implemented and receiving COMMAND_UNAVAILABLE at runtime.
            "commands": available_commands,
            "allowed_commands": sorted(ALLOWED_COMMANDS),
        }

    def _shutdown(self, _payload: dict[str, Any]) -> Mapping[str, Any]:
        self._stop_requested = True
        self.close()
        return {"stopping": True}

    def close(self) -> None:
        """Release process-local source watching resources."""

        watcher = self._source_watcher
        self._source_watcher = None
        if watcher is not None:
            watcher.close()

    def _open_workspace(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        root = self._workspace_root(payload)
        if self._opened_root is not None and root != self._opened_root:
            raise DesktopServiceError("WORKSPACE_ALREADY_OPEN", "the service already owns another workspace")
        return self._bind_workspace(root, switch=False)

    def _switch_workspace(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        root = self._workspace_root(payload)
        if any(self._subagent_is_active(state) for state in self._subagent_states()):
            raise DesktopServiceError("WORKSPACE_BUSY", "stop active subagent runs before switching workspace")
        return self._bind_workspace(root, switch=True)

    def _workspace_root(self, payload: Mapping[str, Any]) -> Path:
        requested = payload.get("workspace_path")
        if requested is not None:
            if (
                not isinstance(requested, str)
                or not requested.strip()
                or len(requested) > MAX_PATH_LENGTH
                or "\x00" in requested
            ):
                raise DesktopServiceError("INVALID_ARGUMENT", "workspace_path must be a bounded path")
            return Path(requested).expanduser().resolve()
        return self.profile_root.resolve()

    def _subagent_states(self) -> tuple[_SubagentRunState, ...]:
        with self._subagent_event_lock:
            return tuple(self._subagent_runs.values())

    @staticmethod
    def _subagent_is_active(state: _SubagentRunState) -> bool:
        with state.lock:
            return state.status in {"RUNNING", "CANCELLING"}

    def _bind_workspace(self, root: Path, *, switch: bool) -> Mapping[str, Any]:
        if root.exists() and not root.is_dir():
            raise DesktopServiceError("INVALID_ARGUMENT", "workspace_path must identify a directory")
        root.mkdir(parents=True, exist_ok=True)
        aegis_dir = root / ".aegis"
        aegis_dir.mkdir(parents=True, exist_ok=True)
        state_path = (aegis_dir / "state.db").resolve()
        configured_profile = os.environ.get("AEGIS_PROFILE_ID")
        if configured_profile is not None and configured_profile != self.profile_id:
            raise DesktopServiceError(
                "PROFILE_ALREADY_BOUND",
                "the native profile is already bound to a different profile id",
            )
        if switch:
            os.environ["AEGIS_SESSION_DB_PATH"] = str(state_path)
        else:
            os.environ.setdefault("AEGIS_SESSION_DB_PATH", str(state_path))
        os.environ.setdefault("AEGIS_PROFILE_ID", self.profile_id)
        configured_state = Path(os.environ["AEGIS_SESSION_DB_PATH"]).expanduser().resolve()
        if configured_state != state_path:
            raise DesktopServiceError(
                "PROFILE_ALREADY_BOUND",
                "the native profile is already bound to a different state store",
            )
        self._opened_root = root
        self._state_path = state_path
        self._source_mapper = SourceMapper(root)
        self._source_snapshot = None
        self.close()
        self._connections = None
        self._learning = None
        self._manager = None
        return self._workspace_snapshot({})

    def _clone_workspace(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        if any(self._subagent_is_active(state) for state in self._subagent_states()):
            raise DesktopServiceError("WORKSPACE_BUSY", "stop active subagent runs before cloning a workspace")
        raw_url = payload.get("clone_url")
        url, name = self._clone_source(raw_url)
        destination_root = payload.get("destination_root")
        if destination_root is None:
            parent = (self.profile_root / "projects").resolve()
        elif isinstance(destination_root, str) and destination_root.strip() and len(destination_root) <= MAX_PATH_LENGTH and "\x00" not in destination_root:
            parent = Path(destination_root).expanduser().resolve()
        else:
            raise DesktopServiceError("INVALID_ARGUMENT", "destination_root must be a bounded directory path")
        parent.mkdir(parents=True, exist_ok=True)
        destination = (parent / name).resolve()
        if destination == self._opened_root:
            raise DesktopServiceError("INVALID_ARGUMENT", "clone destination must differ from the open workspace")
        if destination.exists():
            raise DesktopServiceError("CLONE_DESTINATION_EXISTS", "the clone destination already exists")
        try:
            completed = subprocess.run(
                ["git", "clone", "--", url, str(destination)],
                check=False,
                capture_output=True,
                text=True,
                timeout=MAX_CLONE_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as error:
            raise DesktopServiceError("GIT_UNAVAILABLE", "git is not available on this machine") from error
        except subprocess.TimeoutExpired as error:
            self._remove_clone_destination(destination)
            raise DesktopServiceError("CLONE_TIMEOUT", "git clone exceeded the time limit") from error
        if completed.returncode != 0:
            self._remove_clone_destination(destination)
            raise DesktopServiceError("CLONE_FAILED", "git could not clone the requested public repository")
        return {"workspace_path": str(destination), "name": name}

    @staticmethod
    def _remove_clone_destination(destination: Path) -> None:
        if destination.exists() and destination.is_dir() and (destination / ".git").exists():
            shutil.rmtree(destination, ignore_errors=True)

    @staticmethod
    def _clone_source(raw_url: object) -> tuple[str, str]:
        if not isinstance(raw_url, str) or not raw_url.strip() or len(raw_url) > MAX_CLONE_URL_LENGTH or any(char.isspace() for char in raw_url):
            raise DesktopServiceError("INVALID_ARGUMENT", "clone_url must be a bounded public repository URL")
        value = raw_url.strip()
        scp = _SCP_GIT_SOURCE.fullmatch(value)
        if scp:
            host, path = scp.groups()
        else:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            path = parsed.path
            if parsed.scheme not in {"git", "http", "https", "ssh"} or parsed.username not in {None, "git"} or parsed.password is not None or parsed.fragment:
                raise DesktopServiceError("INVALID_ARGUMENT", "clone_url must use a supported public git URL")
        if host.lower() not in _CLONE_HOSTS:
            raise DesktopServiceError("INVALID_ARGUMENT", "clone_url must target an approved public git host")
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if name.lower().endswith(".git"):
            name = name[:-4]
        if not name or not _REPOSITORY_NAME.fullmatch(name):
            raise DesktopServiceError("INVALID_ARGUMENT", "clone_url does not contain a safe repository name")
        return value, name

    def _workspace_snapshot(self, _payload: dict[str, Any]) -> Mapping[str, Any]:
        return {
            "open": self._opened_root is not None,
            "workspace_path": str(self._opened_root) if self._opened_root is not None else None,
            "state_path": str(self._state_path) if self._state_path is not None else None,
            "profile_id": self.profile_id,
            "native_runtime_available": native_runtime_available(),
        }

    def _require_open(self) -> None:
        if self._opened_root is None:
            raise DesktopServiceError("WORKSPACE_NOT_OPEN", "open a workspace before using it")

    def _workspace_source_snapshot(self, _payload: dict[str, Any]) -> Mapping[str, Any]:
        self._require_open()
        if self._source_mapper is None:
            raise DesktopServiceError("SOURCE_MAPPER_UNAVAILABLE", "the local source mapper is unavailable")
        watcher: BoundedSnapshotWatcher | None = None
        if self._source_watcher is None:
            try:
                self._source_watcher = NativeSnapshotWatcher(self._opened_root or self.profile_root)
            except CodeIntelligenceError, ImportError, OSError, RuntimeError, ValueError:
                self._source_watcher = None
        if self._source_watcher is not None:
            try:
                watcher = self._source_watcher.poll()
            except CodeIntelligenceError, ImportError, OSError, RuntimeError, ValueError:
                self._source_watcher.close()
                self._source_watcher = None
                watcher = BoundedSnapshotWatcher()
                watcher.mark_overflow()
        self._source_snapshot = self._source_mapper.snapshot(self._source_snapshot, watcher=watcher)
        return {"snapshot": _json_value(self._source_snapshot)}

    def _verification_session_id(self, payload: dict[str, Any]) -> str:
        return _require_text(payload, "session_id", max_length=256)

    def _verification_error(self, error: Exception) -> DesktopServiceError:
        return DesktopServiceError("VERIFICATION_ERROR", str(error))

    def _verification_inspect_project(self, _payload: dict[str, Any]) -> Mapping[str, Any]:
        self._require_open()
        try:
            profile = self._verification_facade.inspect_project(self._opened_root or self.profile_root)
        except (OSError, ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return {"profile": profile.as_dict()}

    def _verification_create_contract(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        self._require_open()
        task = _require_text(payload, "task", max_length=MAX_PROMPT_LENGTH)
        expected = payload.get("expected_behavior")
        if expected is not None and (not isinstance(expected, str) or len(expected) > MAX_PROMPT_LENGTH):
            raise DesktopServiceError("INVALID_ARGUMENT", "expected_behavior must be a bounded string")
        try:
            profile = self._verification_facade.inspect_project(self._opened_root or self.profile_root)
            requirements = self._verification_facade.create_contract(
                task,
                expected_behavior=expected,
                source_revision=profile.source_revision,
                policy_hash=self.trust_policy_hash,
            )
        except (OSError, ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return {
            "profile": profile.as_dict(),
            "requirements": tuple(requirement.as_dict() for requirement in requirements),
        }

    def _verification_start_session(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        self._require_open()
        task = _require_text(payload, "task", max_length=MAX_PROMPT_LENGTH)
        expected = payload.get("expected_behavior")
        if expected is not None and (not isinstance(expected, str) or len(expected) > MAX_PROMPT_LENGTH):
            raise DesktopServiceError("INVALID_ARGUMENT", "expected_behavior must be a bounded string")
        risk_level = payload.get("risk_level", "UNKNOWN")
        if not isinstance(risk_level, str) or not risk_level.strip():
            raise DesktopServiceError("INVALID_ARGUMENT", "risk_level must be a non-empty string")
        try:
            profile = self._verification_facade.inspect_project(self._opened_root or self.profile_root)
            requirements = self._verification_facade.create_contract(
                task,
                expected_behavior=expected,
                source_revision=profile.source_revision,
                policy_hash=self.trust_policy_hash,
            )
            session = self._verification_facade.start_session(profile, requirements, risk_level=risk_level)
            packet = self._verification_facade.get_agent_packet(session.session_id)
        except (OSError, ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return {"session": session.as_dict(), "packet": _json_value(packet)}

    def _verification_get_agent_packet(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            packet = self._verification_facade.get_agent_packet(self._verification_session_id(payload))
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return _json_value(packet)

    def _verification_observe_change(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        raw_paths = payload.get("changed_paths", [])
        raw_path_values = cast(list[object], raw_paths) if isinstance(raw_paths, list) else []
        if not isinstance(raw_paths, list) or any(not isinstance(path, str) for path in raw_path_values):
            raise DesktopServiceError("INVALID_ARGUMENT", "changed_paths must be a list of strings")
        typed_paths = cast(list[str], raw_paths)
        revision = payload.get("source_revision")
        if revision is not None and not isinstance(revision, str):
            raise DesktopServiceError("INVALID_ARGUMENT", "source_revision must be a string")
        try:
            return self._verification_facade.observe_change(
                self._verification_session_id(payload),
                typed_paths,
                source_revision=revision,
            )
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _verification_get_feedback(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            return self._verification_facade.get_feedback(self._verification_session_id(payload))
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _verification_propose_test_change(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        patch = payload.get("patch_text")
        paths = payload.get("changed_paths")
        if not isinstance(patch, str) or not patch.strip() or not isinstance(paths, list):
            raise DesktopServiceError("INVALID_ARGUMENT", "patch_text and changed_paths are required")
        path_values = cast(list[object], paths)
        if any(not isinstance(path, str) for path in path_values):
            raise DesktopServiceError("INVALID_ARGUMENT", "changed_paths must be a list of strings")
        typed_paths = cast(list[str], paths)
        requirement_ids = payload.get("requirement_ids", [])
        requirement_values = cast(list[object], requirement_ids) if isinstance(requirement_ids, list) else []
        if not isinstance(requirement_ids, list) or any(not isinstance(item, str) for item in requirement_values):
            raise DesktopServiceError("INVALID_ARGUMENT", "requirement_ids must be a list of strings")
        typed_requirement_ids = cast(list[str], requirement_ids)
        try:
            proposal = self._verification_facade.propose_test_change(
                self._verification_session_id(payload), patch, typed_paths, requirement_ids=typed_requirement_ids
            )
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return {"proposal": proposal.as_dict()}

    def _verification_evaluate_test_change(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            proposal = self._verification_facade.evaluate_test_change(
                self._verification_session_id(payload), _require_text(payload, "proposal_id", max_length=256)
            )
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return {"proposal": proposal.as_dict()}

    def _verification_apply_test_change(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            return self._verification_facade.apply_test_change(
                self._verification_session_id(payload), _require_text(payload, "proposal_id", max_length=256)
            )
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _verification_request_deep_run(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        reason = payload.get("reason", "desktop_requested")
        if not isinstance(reason, str) or not reason.strip():
            raise DesktopServiceError("INVALID_ARGUMENT", "reason must be a non-empty string")
        try:
            return self._verification_facade.request_deep_run(self._verification_session_id(payload), reason=reason)
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _verification_inspect_run(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            return self._verification_facade.inspect_run(
                self._verification_session_id(payload), _require_text(payload, "run_id", max_length=256)
            )
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _verification_cancel_run(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            return self._verification_facade.cancel_run(
                self._verification_session_id(payload), _require_text(payload, "run_id", max_length=256)
            )
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _verification_resume_session(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            session = self._verification_facade.resume_session(self._verification_session_id(payload))
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error
        return {"session": session.as_dict()}

    def _verification_read_report(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            return self._verification_facade.read_report(self._verification_session_id(payload))
        except (ValueError, VerificationSessionError) as error:
            raise self._verification_error(error) from error

    def _manager_for_request(self) -> Any:
        self._require_open()
        if self._manager_factory is ConversationManager and not native_runtime_available():
            raise DesktopServiceError(
                "NATIVE_UNAVAILABLE",
                "the bundled native runtime is unavailable",
            )
        if self._manager is None:
            self._manager = self._manager_factory()
        return self._manager

    def _connections_for_request(self) -> Any:
        self._require_open()
        if self._connections is None:
            if self._connection_catalog_factory is ConnectionCatalog and not native_runtime_available():
                raise DesktopServiceError("NATIVE_UNAVAILABLE", "the bundled native runtime is unavailable")
            self._connections = self._connection_catalog_factory()
        return self._connections

    def _learning_for_request(self) -> Any:
        self._require_open()
        if self._learning is None:
            if self._learning_factory is LearningManager and not native_runtime_available():
                raise DesktopServiceError("NATIVE_UNAVAILABLE", "the bundled native runtime is unavailable")
            self._learning = self._learning_factory()
        return self._learning

    def _owner(self, payload: Mapping[str, Any]) -> str:
        owner = payload.get("owner_id", self.profile_id)
        if not isinstance(owner, str) or not owner.strip() or len(owner) > MAX_PROFILE_ID_LENGTH:
            raise DesktopServiceError("INVALID_ARGUMENT", "owner_id must be a bounded non-empty string")
        return owner

    def _list_connections(self, _payload: dict[str, Any]) -> Mapping[str, Any]:
        catalog = self._connections_for_request()
        return {"records": _json_value(catalog.list_connections())}

    def _save_connection(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        catalog = self._connections_for_request()
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise DesktopServiceError("INVALID_ARGUMENT", "enabled must be a boolean")
        record = catalog.register_connection(
            _require_text(payload, "connection_id", max_length=256),
            provider_kind=_require_text(payload, "provider_kind", max_length=128),
            endpoint=_require_text(payload, "endpoint", max_length=2048),
            protocol=_require_text(payload, "protocol", max_length=128),
            secret_ref=(
                _require_text(payload, "secret_ref", max_length=512) if payload.get("secret_ref") is not None else None
            ),
            enabled=enabled,
            expected_revision=(_require_revision(payload) if payload.get("expected_revision") is not None else None),
        )
        model_id = payload.get("model_id")
        model = None
        if model_id is not None:
            model = catalog.register_model(
                record.connection_id,
                _require_text(payload, "model_id", max_length=256),
                family=(str(payload["family"]) if payload.get("family") is not None else None),
                capabilities=tuple(str(item) for item in payload.get("capabilities", [])),
                context_limit=(int(payload["context_limit"]) if payload.get("context_limit") is not None else None),
                output_limit=(int(payload["output_limit"]) if payload.get("output_limit") is not None else None),
                source="manual",
            )
        return {"record": _json_value(record), "model": _json_value(model) if model is not None else None}

    def _discover_connection_models(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        catalog = self._connections_for_request()
        models = catalog.discover_models(_require_text(payload, "connection_id", max_length=256))
        return {"models": _json_value(models)}

    def _disable_connection(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        catalog = self._connections_for_request()
        record = catalog.revoke_connection(
            _require_text(payload, "connection_id", max_length=256),
            expected_revision=_require_revision(payload),
        )
        return {"record": _json_value(record)}

    def _list_models(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        catalog = self._connections_for_request()
        return {"models": _json_value(catalog.list_models(_require_text(payload, "connection_id", max_length=256)))}

    def _create_conversation(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        manager = self._manager_for_request()
        conversation_id = _require_text(payload, "conversation_id", max_length=256)
        title = _require_text(payload, "title", max_length=512)
        connection_id = _require_text(payload, "connection_id", max_length=256)
        model_id = _require_text(payload, "model_id", max_length=256)
        record = manager.create(
            conversation_id,
            owner_id=self._owner(payload),
            title=title,
            connection_id=connection_id,
            model_id=model_id,
        )
        return {"record": _json_value(record)}

    def _list_conversations(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        manager = self._manager_for_request()
        return {"records": _json_value(manager.list(self._owner(payload)))}

    def _read_conversation(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        manager = self._manager_for_request()
        conversation_id = _require_text(payload, "conversation_id", max_length=256)
        snapshot = manager.read(conversation_id, owner_id=self._owner(payload))
        return {"snapshot": _json_value(snapshot)}

    def _inspect_conversation(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        """Expose a bounded context/timeline/approval projection for the UI."""

        manager = self._manager_for_request()
        conversation_id = _require_text(payload, "conversation_id", max_length=256)
        snapshot = manager.read(conversation_id, owner_id=self._owner(payload))
        source_revision: str | None = None
        if self._source_snapshot is not None:
            source_revision = self._source_snapshot.revision
        projection = build_conversation_inspection(snapshot, source_revision=source_revision)
        return {"inspection": projection}

    def _switch_model(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        manager = self._manager_for_request()
        record = manager.switch_model(
            _require_text(payload, "conversation_id", max_length=256),
            owner_id=self._owner(payload),
            connection_id=_require_text(payload, "connection_id", max_length=256),
            model_id=_require_text(payload, "model_id", max_length=256),
            expected_revision=_require_revision(payload),
        )
        return {"record": _json_value(record)}

    def _start_subagents(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        """Start a host-owned run and return before model work completes."""

        return self._run_subagents(payload, wait=False)

    def _run_subagents(self, payload: dict[str, Any], *, wait: bool = True) -> Mapping[str, Any]:
        """Run the canonical local subagent runtime through the desktop boundary.

        The desktop command deliberately accepts only a task and optional
        validated plan metadata.  Model callables, handler binding, resource
        admission, and the native graph authority remain owned by
        ``AgentApplication``; the renderer cannot provide executable code.
        """

        self._require_open()
        task = _require_text(payload, "task", max_length=MAX_SUBAGENT_TASK_CHARS)
        owner_id = self._owner(payload)
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None:
            conversation_id = _require_text(payload, "conversation_id", max_length=256)

        conversation_manager: Any = None
        conversation_snapshot: Any = None
        if conversation_id is not None:
            conversation_manager = self._manager_for_request()
            conversation_snapshot = conversation_manager.read(conversation_id, owner_id=owner_id)
            if conversation_snapshot.conversation.status != "ACTIVE":
                raise DesktopServiceError("CONVERSATION_NOT_ACTIVE", "conversation is not active")
            if any(
                turn.status in {"QUEUED", "RUNNING", "WAITING_APPROVAL"} for turn in conversation_snapshot.turns
            ) or any(call.status in {"REQUESTED", "AMBIGUOUS"} for call in conversation_snapshot.tool_calls):
                raise DesktopServiceError("CONVERSATION_BUSY", "conversation has unresolved execution state")

        raw_connection_id = payload.get("connection_id")
        if raw_connection_id is None and conversation_snapshot is not None:
            raw_connection_id = conversation_snapshot.conversation.connection_id
        connection_id = _require_text({"connection_id": raw_connection_id}, "connection_id", max_length=256)

        raw_model_id = payload.get("model_id")
        if raw_model_id is None and conversation_snapshot is not None:
            raw_model_id = conversation_snapshot.conversation.model_id
        if raw_model_id is None:
            raw_model_id = os.environ.get("AEGIS_DESKTOP_MODEL_ID", "")
        model_id = _require_text({"model_id": raw_model_id}, "model_id", max_length=256)

        raw_plan = payload.get("plan")
        if raw_plan is not None and not isinstance(raw_plan, Mapping):
            raise DesktopServiceError("INVALID_ARGUMENT", "plan must be a JSON object")

        def bounded_bool(key: str, default: bool) -> bool:
            value = payload.get(key, default)
            if type(value) is not bool:
                raise DesktopServiceError("INVALID_ARGUMENT", f"{key} must be a boolean")
            return value

        def bounded_int(key: str, default: int, maximum: int) -> int:
            value = payload.get(key, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise DesktopServiceError("INVALID_ARGUMENT", f"{key} must be an integer between 1 and {maximum}")
            return value

        allow_dynamic = bounded_bool("allow_dynamic_plans", True)
        require_native = bounded_bool("require_native_authority", True)
        max_concurrency = bounded_int("max_concurrency", 4, MAX_SUBAGENT_CONCURRENCY)
        max_tasks = bounded_int("max_tasks", 32, MAX_SUBAGENT_TASKS)
        max_dynamic_tasks = bounded_int("max_dynamic_tasks", min(128, max_tasks), MAX_SUBAGENT_TASKS)

        connection = self._connection_for_conversation(connection_id)
        client = self._provider_client(connection, model_id)
        config = AgentConfig.from_inputs(
            task,
            llm=client,
            trust_level=self.trust_level,
            browser=False,
            max_steps=1,
            subagents_allow_dynamic_plans=allow_dynamic,
            subagents_require_native_authority=require_native,
            subagent_max_concurrency=max_concurrency,
            subagent_max_tasks=max_tasks,
            subagent_max_dynamic_tasks=max_dynamic_tasks,
            subagent_public_research=True,
        )
        application = self._subagent_application_factory(config)
        runner = getattr(application, "run_subagents", None)
        if not callable(runner):
            raise DesktopServiceError("SUBAGENT_UNAVAILABLE", "the local subagent application is unavailable")

        execution: Any = None
        assistant_turn: Any = None
        if conversation_manager is not None and conversation_snapshot is not None and conversation_id is not None:
            user_turn = conversation_manager.append_turn(
                conversation_id,
                owner_id=owner_id,
                turn_id=f"turn-{uuid.uuid4().hex}",
                role="user",
                content=task,
                connection_id=connection.connection_id,
                model_id=model_id,
                status="COMPLETED",
                expected_revision=conversation_snapshot.conversation.revision,
            )
            assistant_turn = conversation_manager.append_turn(
                conversation_id,
                owner_id=owner_id,
                turn_id=f"turn-{uuid.uuid4().hex}",
                role="assistant",
                content="",
                connection_id=connection.connection_id,
                model_id=model_id,
                status="QUEUED",
                expected_revision=user_turn.revision,
            )
            execution = conversation_manager.start_execution(
                conversation_id,
                owner_id=owner_id,
                execution_id=f"exec-{uuid.uuid4().hex}",
                turn_id=assistant_turn.turn_id,
                provider_kind=connection.provider_kind,
                connection_id=connection.connection_id,
                model_id=model_id,
                expected_revision=assistant_turn.revision,
            )
        event_journal = AgentMessageJournal(max_messages=MAX_SUBAGENT_EVENT_MESSAGES)
        runner_kwargs: dict[str, object] = {
            "plan": (cast(Mapping[str, object], raw_plan) if raw_plan is not None else None),
            "require_native_authority": require_native,
            "max_concurrency": max_concurrency,
        }
        try:
            parameters = dict(cast(Mapping[str, inspect.Parameter], inspect.signature(runner).parameters))
        except TypeError, ValueError:
            parameters = {}
        if "message_journal" in parameters:
            runner_kwargs["message_journal"] = event_journal
        application_run_id = str(getattr(getattr(application, "correlation", None), "run_id", "")).strip()
        run_id = application_run_id or f"desktop-{uuid.uuid4().hex}"
        accepts_keyword_arguments = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        cancel_supported = "cancel_event" in parameters or accepts_keyword_arguments
        state = _SubagentRunState(
            run_id=run_id,
            journal=event_journal,
            cancel_supported=cancel_supported,
        )
        if cancel_supported:
            runner_kwargs["cancel_event"] = state.cancel_event
        try:
            self._remember_subagent_run(state)
        except DesktopServiceError:
            self._finish_subagent_execution(
                conversation_manager,
                conversation_id,
                owner_id,
                execution,
                status="FAILED",
            )
            raise

        def execute_and_finalize() -> Mapping[str, Any]:
            try:
                result = runner(**runner_kwargs)
            except AgentCancellationError as error:
                self._finish_subagent_execution(
                    conversation_manager,
                    conversation_id,
                    owner_id,
                    execution,
                    status="CANCELLED",
                )
                raise DesktopServiceError("SUBAGENT_CANCELLED", "subagent run was cancelled") from error
            except DesktopServiceError:
                self._finish_subagent_execution(
                    conversation_manager,
                    conversation_id,
                    owner_id,
                    execution,
                    status="FAILED",
                )
                raise
            except Exception as error:
                self._finish_subagent_execution(
                    conversation_manager,
                    conversation_id,
                    owner_id,
                    execution,
                    status="FAILED",
                )
                raise DesktopServiceError("SUBAGENT_ERROR", "local subagent execution failed") from error
            try:
                result_payload = self._subagent_result_payload(result)
            except DesktopServiceError:
                self._finish_subagent_execution(
                    conversation_manager,
                    conversation_id,
                    owner_id,
                    execution,
                    status="FAILED",
                )
                raise
            except Exception as error:
                self._finish_subagent_execution(
                    conversation_manager,
                    conversation_id,
                    owner_id,
                    execution,
                    status="FAILED",
                )
                raise DesktopServiceError("SUBAGENT_ERROR", "subagent result could not be projected") from error
            result_run_id = str(getattr(result, "run_id", "")).strip() or run_id
            self._remember_subagent_event_journal(result_run_id, event_journal)
            result_payload = {
                **result_payload,
                "event_cursor": event_journal.latest_cursor,
            }
            if (
                conversation_manager is not None
                and conversation_id is not None
                and execution is not None
                and assistant_turn is not None
            ):
                try:
                    output_turn = conversation_manager.append_part(
                        conversation_id,
                        owner_id=owner_id,
                        turn_id=assistant_turn.turn_id,
                        kind="TEXT",
                        content=str(result_payload["root_output"]),
                        expected_revision=execution.revision,
                    )
                    finished = conversation_manager.finish_execution(
                        conversation_id,
                        owner_id=owner_id,
                        execution_id=execution.execution_id,
                        status="COMPLETED",
                        expected_revision=output_turn.revision,
                    )
                except Exception as error:
                    self._finish_subagent_execution(
                        conversation_manager,
                        conversation_id,
                        owner_id,
                        execution,
                        status="FAILED",
                    )
                    raise DesktopServiceError(
                        "SUBAGENT_PERSISTENCE_FAILED", "subagent result could not be persisted"
                    ) from error
                result_payload = {
                    **result_payload,
                    "conversation_revision": int(getattr(finished, "revision", output_turn.revision)),
                }
            return result_payload

        if wait:
            try:
                result_payload = dict(execute_and_finalize())
            except DesktopServiceError as error:
                self._finish_subagent_state(state, error=error)
                raise
            except Exception as error:
                wrapped = DesktopServiceError("SUBAGENT_ERROR", "local subagent execution failed")
                self._finish_subagent_state(state, error=wrapped)
                raise wrapped from error
            self._finish_subagent_state(state, result=result_payload)
            return result_payload

        def background_worker() -> None:
            try:
                self._finish_subagent_state(state, result=dict(execute_and_finalize()))
            except DesktopServiceError as error:
                self._finish_subagent_state(state, error=error)
            except Exception:
                self._finish_subagent_state(
                    state,
                    error=DesktopServiceError("SUBAGENT_ERROR", "local subagent execution failed"),
                )

        thread = threading.Thread(target=background_worker, name=f"aegis-subagent-{run_id[:24]}", daemon=True)
        state.thread = thread
        thread.start()
        return {
            "schema": "aegis-desktop-subagents-start-v1",
            "run_id": run_id,
            "status": "RUNNING",
            "event_cursor": event_journal.latest_cursor,
        }

    def _remember_subagent_run(self, state: _SubagentRunState) -> None:
        with self._subagent_event_lock:
            self._subagent_runs[state.run_id] = state
            self._subagent_runs.move_to_end(state.run_id)
            self._subagent_event_journals[state.run_id] = state.journal
            self._subagent_event_journals.move_to_end(state.run_id)
            while len(self._subagent_runs) > MAX_SUBAGENT_EVENT_RUNS:
                terminal_id: str | None = None
                for candidate_id, candidate in self._subagent_runs.items():
                    with candidate.lock:
                        if candidate.status != "RUNNING":
                            terminal_id = candidate_id
                            break
                if terminal_id is None:
                    self._subagent_runs.pop(state.run_id, None)
                    self._subagent_event_journals.pop(state.run_id, None)
                    raise DesktopServiceError(
                        "SUBAGENT_CAPACITY",
                        "too many active subagent runs; wait for one to finish",
                    )
                run_id = terminal_id
                self._subagent_runs.pop(run_id)
                self._subagent_event_journals.pop(run_id, None)

    @staticmethod
    def _finish_subagent_state(
        state: _SubagentRunState,
        *,
        result: dict[str, Any] | None = None,
        error: DesktopServiceError | None = None,
    ) -> None:
        with state.lock:
            if result is not None:
                state.result_payload = dict(result)
                state.status = str(result.get("status", "COMPLETED"))
                state.error_code = None
                state.error_message = None
            elif error is not None:
                state.status = "CANCELLED" if error.code == "SUBAGENT_CANCELLED" else "FAILED"
                state.error_code = error.code
                state.error_message = str(error)
            state.finished_at_ms = int(time.time() * 1000)

    def _subagent_status(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        run_id = _require_text(payload, "run_id", max_length=128)
        with self._subagent_event_lock:
            state = self._subagent_runs.get(run_id)
        if state is None:
            raise DesktopServiceError("SUBAGENT_RUN_NOT_FOUND", "subagent run is unavailable")
        with state.lock:
            result = dict(state.result_payload) if state.result_payload is not None else None
            return {
                "schema": "aegis-desktop-subagents-status-v1",
                "run_id": state.run_id,
                "status": state.status,
                "event_cursor": state.journal.latest_cursor,
                "started_at_ms": state.started_at_ms,
                "finished_at_ms": state.finished_at_ms,
                "cancel_requested_at_ms": state.cancel_requested_at_ms,
                "cancel_supported": state.cancel_supported,
                "thread_alive": bool(state.thread is not None and state.thread.is_alive()),
                "result": result,
                "error": (
                    {"code": state.error_code, "message": state.error_message} if state.error_code is not None else None
                ),
            }

    def _cancel_subagents(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        run_id = _require_text(payload, "run_id", max_length=128)
        with self._subagent_event_lock:
            state = self._subagent_runs.get(run_id)
        if state is None:
            raise DesktopServiceError("SUBAGENT_RUN_NOT_FOUND", "subagent run is unavailable")
        with state.lock:
            if state.status not in {"RUNNING", "CANCELLING"}:
                return {
                    "schema": "aegis-desktop-subagents-cancel-v1",
                    "run_id": state.run_id,
                    "status": state.status,
                    "event_cursor": state.journal.latest_cursor,
                }
            if not state.cancel_supported:
                raise DesktopServiceError(
                    "SUBAGENT_CANCEL_UNSUPPORTED",
                    "the active subagent runner does not expose cooperative cancellation",
                )
            state.cancel_event.set()
            state.status = "CANCELLING"
            state.cancel_requested_at_ms = int(time.time() * 1000)
            return {
                "schema": "aegis-desktop-subagents-cancel-v1",
                "run_id": state.run_id,
                "status": state.status,
                "event_cursor": state.journal.latest_cursor,
            }

    def _reuse_snapshot(self) -> SourceSnapshot:
        self._require_open()
        if self._source_mapper is None:
            raise DesktopServiceError("SOURCE_MAPPER_UNAVAILABLE", "the local source mapper is unavailable")
        if self._source_snapshot is None:
            self._workspace_source_snapshot({})
        if self._source_snapshot is None:
            raise DesktopServiceError("SOURCE_SNAPSHOT_UNAVAILABLE", "the local source snapshot is unavailable")
        return self._source_snapshot

    @staticmethod
    def _reuse_int(payload: Mapping[str, Any], key: str, *, maximum: int = MAX_CODE_REUSE_TOKENS) -> int:
        value = payload.get(key)
        if type(value) is not int or not 0 <= value <= maximum:
            raise DesktopServiceError("INVALID_ARGUMENT", f"{key} must be an integer between 0 and {maximum}")
        return value

    def _reuse_candidate(self, payload: Mapping[str, Any]) -> Any:
        source_path = _require_text(payload, "source_path", max_length=MAX_PATH_LENGTH)
        start_line = self._reuse_int(payload, "start_line", maximum=MAX_CODE_REUSE_LINE)
        end_line = self._reuse_int(payload, "end_line", maximum=MAX_CODE_REUSE_LINE)
        if start_line < 1 or end_line < start_line:
            raise DesktopServiceError("INVALID_ARGUMENT", "source line range is invalid")
        license_id = str(payload.get("license_id", "INTERNAL"))
        provenance_uri = payload.get("provenance_uri")
        if provenance_uri is not None and not isinstance(provenance_uri, str):
            raise DesktopServiceError("INVALID_ARGUMENT", "provenance_uri must be text")
        try:
            candidate = build_local_reuse_candidate(
                self._reuse_snapshot(),
                source_path,
                start_line=start_line,
                end_line=end_line,
                license_id=license_id,
                provenance_uri=provenance_uri,
            )
        except CodeReuseError as error:
            raise DesktopServiceError("CODE_REUSE_REJECTED", str(error)) from error
        return candidate

    def _assess_code_reuse(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        candidate = self._reuse_candidate(payload)
        try:
            assessment = assess_code_reuse(
                candidate,
                generation_tokens=self._reuse_int(payload, "generation_tokens"),
                adaptation_tokens=self._reuse_int(payload, "adaptation_tokens"),
                verification_tokens=self._reuse_int(payload, "verification_tokens"),
            )
        except CodeReuseError as error:
            raise DesktopServiceError("CODE_REUSE_REJECTED", str(error)) from error
        return {"candidate": candidate.as_dict(), "assessment": assessment.as_dict()}

    def _materialize_code_reuse(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        candidate = self._reuse_candidate(payload)
        overwrite = payload.get("overwrite", False)
        if type(overwrite) is not bool:
            raise DesktopServiceError("INVALID_ARGUMENT", "overwrite must be a boolean")
        target_relative_path = _require_text(payload, "target_relative_path", max_length=MAX_PATH_LENGTH)
        root = self._opened_root
        if root is None:
            raise DesktopServiceError("WORKSPACE_NOT_OPEN", "open a workspace before reusing code")
        try:
            receipt = materialize_exact(
                candidate,
                target_root=root,
                target_relative_path=target_relative_path,
                overwrite=overwrite,
            )
        except CodeReuseError as error:
            raise DesktopServiceError("CODE_REUSE_REJECTED", str(error)) from error
        return {"candidate": candidate.as_dict(), "receipt": receipt.as_dict()}

    def _remember_subagent_event_journal(self, run_id: str, journal: AgentMessageJournal) -> None:
        if not run_id or type(journal) is not AgentMessageJournal:
            return
        with self._subagent_event_lock:
            self._subagent_event_journals[run_id] = journal
            self._subagent_event_journals.move_to_end(run_id)
            while len(self._subagent_event_journals) > MAX_SUBAGENT_EVENT_RUNS:
                self._subagent_event_journals.popitem(last=False)

    def _read_subagent_events(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        run_id = _require_text(payload, "run_id", max_length=128)
        cursor = _require_revision(payload, "cursor") if payload.get("cursor") is not None else 0
        raw_limit = payload.get("max_messages", 64)
        if type(raw_limit) is not int or not 1 <= raw_limit <= MAX_SUBAGENT_EVENT_MESSAGES:
            raise DesktopServiceError(
                "INVALID_ARGUMENT",
                f"max_messages must be an integer between 1 and {MAX_SUBAGENT_EVENT_MESSAGES}",
            )
        with self._subagent_event_lock:
            journal = self._subagent_event_journals.get(run_id)
        if journal is None:
            raise DesktopServiceError("SUBAGENT_RUN_NOT_FOUND", "subagent event history is unavailable")
        read = journal.read_since(cursor, max_messages=raw_limit)
        return {
            "schema": "aegis-desktop-subagent-events-v1",
            "run_id": run_id,
            "latest_cursor": read.latest_cursor,
            "oldest_cursor": read.oldest_cursor,
            "resync_required": read.resync_required,
            "events": [self._subagent_event_projection(entry.cursor, entry.message) for entry in read.entries],
        }

    def _subagent_graph(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        run_id = _require_text(payload, "run_id", max_length=128)
        with self._subagent_event_lock:
            journal = self._subagent_event_journals.get(run_id)
        if journal is None:
            raise DesktopServiceError("SUBAGENT_RUN_NOT_FOUND", "subagent event history is unavailable")
        read = journal.read_since(0, max_messages=MAX_SUBAGENT_EVENT_MESSAGES)
        events = [self._subagent_event_projection(entry.cursor, entry.message) for entry in read.entries]
        return {"graph": build_subagent_graph(run_id, events)}

    @staticmethod
    def _subagent_event_projection(cursor: int, message: AgentMessage) -> Mapping[str, Any]:
        """Expose event metadata without forwarding child prompts or raw payloads."""

        def list_value(key: str) -> list[object]:
            value = message.payload.get(key, [])
            return list(cast(list[object], value)) if isinstance(value, list) else []

        payload: dict[str, Any] = {}
        if message.message_kind == "TASK_REQUEST":
            payload = {
                "role": str(message.payload.get("role", "")),
                "dependency_ids": list_value("dependency_ids"),
                "capabilities": list_value("capabilities"),
                "side_effect_class": str(message.payload.get("side_effect_class", "")),
                "redacted": True,
            }
        elif message.message_kind == "TASK_RESULT":
            summary = str(message.payload.get("summary", ""))
            truncated = len(summary) > 512
            payload = {
                "status": str(message.payload.get("status", "")),
                "summary": summary[:512],
                "summary_truncated": truncated,
                "uncertainty": list_value("uncertainty"),
                "blockers": list_value("blockers"),
            }
        return {
            "cursor": cursor,
            "message_kind": message.message_kind,
            "run_id": message.run_id,
            "sender_id": message.sender_id,
            "recipient_id": message.recipient_id,
            "task_id": message.task_id,
            "parent_task_id": message.parent_task_id,
            "attempt_id": message.attempt_id,
            "message_hash": message.message_hash,
            "artifact_refs": [artifact.compact_dict() for artifact in message.artifact_refs],
            "payload": payload,
        }

    @staticmethod
    def _finish_subagent_execution(
        manager: Any | None,
        conversation_id: str | None,
        owner_id: str,
        execution: Any | None,
        *,
        status: str,
    ) -> None:
        if manager is None or conversation_id is None or execution is None:
            return
        try:
            manager.finish_execution(
                conversation_id,
                owner_id=owner_id,
                execution_id=execution.execution_id,
                status=status,
                expected_revision=execution.revision,
            )
        except Exception:
            # A concurrent revision may have advanced after the worker
            # returned. Re-read the canonical execution once and settle it
            # with that fence; never overwrite an already-terminal execution.
            try:
                snapshot = manager.read(conversation_id, owner_id=owner_id)
                current = next(item for item in snapshot.executions if item.execution_id == execution.execution_id)
                if current.status in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
                    return
                manager.finish_execution(
                    conversation_id,
                    owner_id=owner_id,
                    execution_id=execution.execution_id,
                    status=status,
                    expected_revision=current.revision,
                )
            except Exception as settle_error:
                raise DesktopServiceError(
                    "SUBAGENT_PERSISTENCE_FAILED", "subagent execution state could not be settled"
                ) from settle_error

    @staticmethod
    def _subagent_result_payload(result: Any) -> Mapping[str, Any]:
        """Expose a bounded renderer projection, never raw internal objects."""

        def text_value(value: object) -> tuple[str, bool]:
            if isinstance(value, str):
                text = value
            else:
                try:
                    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                except TypeError, ValueError:
                    text = str(value)
            if len(text) <= MAX_SUBAGENT_RESULT_CHARS:
                return text, False
            suffix = "\n[result truncated at the desktop boundary]"
            return f"{text[: MAX_SUBAGENT_RESULT_CHARS - len(suffix)]}{suffix}", True

        root_output, root_output_truncated = text_value(getattr(result, "root_output", ""))
        if not root_output.strip():
            raise DesktopServiceError("SUBAGENT_ERROR", "subagent root synthesis was empty")
        packets: list[Mapping[str, Any]] = []
        for packet in getattr(result, "child_results", ()):
            compact = getattr(packet, "compact_dict", None)
            if not callable(compact):
                raise DesktopServiceError("SUBAGENT_ERROR", "subagent result packet is invalid")
            value = compact()
            if not isinstance(value, Mapping):
                raise DesktopServiceError("SUBAGENT_ERROR", "subagent result packet is invalid")
            packets.append(dict(cast(Mapping[str, Any], value)))
        return {
            "schema": "aegis-desktop-subagents-result-v1",
            "run_id": str(getattr(result, "run_id", "")),
            "graph_hash": str(getattr(result, "graph_hash", "")),
            "graph_authority": str(getattr(result, "graph_authority", "")),
            "status": str(getattr(result, "status", "")),
            "root_output": root_output,
            "root_output_truncated": root_output_truncated,
            "child_results": packets,
            "failed_task_ids": [int(item) for item in getattr(result, "failed_task_ids", ())],
            "blocked_task_ids": [int(item) for item in getattr(result, "blocked_task_ids", ())],
            "coordination_hash": str(getattr(result, "coordination_hash", "")),
        }

    def _send_message(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        mode = payload.get("mode", "mock")
        if mode == "mock":
            return self._send_mock_message(payload)
        if mode in {"live", "real"}:
            return self._send_live_message(payload)
        raise DesktopServiceError("MODE_UNSUPPORTED", "mode must be mock or live")

    def _send_mock_message(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        """Run a deterministic smoke conversation through the real repository.

        This command is deliberately explicit. It is a packaging and restart
        smoke path, not a claim that a live provider is configured.
        """

        message = _require_text(payload, "message", max_length=MAX_MESSAGE_LENGTH)
        conversation_id = _require_text(payload, "conversation_id", max_length=256)
        owner_id = self._owner(payload)
        manager = self._manager_for_request()
        snapshot = manager.read(conversation_id, owner_id=owner_id)
        if snapshot.conversation.status != "ACTIVE":
            raise DesktopServiceError("CONVERSATION_NOT_ACTIVE", "conversation is not active")
        if any(turn.status in {"QUEUED", "RUNNING", "WAITING_APPROVAL"} for turn in snapshot.turns):
            raise DesktopServiceError("CONVERSATION_BUSY", "conversation has unresolved execution state")
        if any(call.status in {"REQUESTED", "AMBIGUOUS"} for call in snapshot.tool_calls):
            raise DesktopServiceError("CONVERSATION_BUSY", "conversation has unresolved tool state")
        connection_id = snapshot.conversation.connection_id
        model_id = snapshot.conversation.model_id
        user_turn_id = f"turn-{uuid.uuid4().hex}"
        user_turn = manager.append_turn(
            conversation_id,
            owner_id=owner_id,
            turn_id=user_turn_id,
            role="user",
            content=message,
            connection_id=connection_id,
            model_id=model_id,
            status="COMPLETED",
            expected_revision=snapshot.conversation.revision,
        )
        assistant_turn_id = f"turn-{uuid.uuid4().hex}"
        assistant_turn = manager.append_turn(
            conversation_id,
            owner_id=owner_id,
            turn_id=assistant_turn_id,
            role="assistant",
            content="",
            connection_id=connection_id,
            model_id=model_id,
            status="QUEUED",
            expected_revision=user_turn.revision,
        )
        execution = manager.start_execution(
            conversation_id,
            owner_id=owner_id,
            execution_id=f"exec-{uuid.uuid4().hex}",
            turn_id=assistant_turn.turn_id,
            provider_kind="mock",
            connection_id=connection_id,
            model_id=model_id,
            expected_revision=assistant_turn.revision,
        )
        output = f"[mock] {message}"
        output_turn = manager.append_part(
            conversation_id,
            owner_id=owner_id,
            turn_id=assistant_turn.turn_id,
            kind="TEXT",
            content=output,
            expected_revision=execution.revision,
        )
        finished = manager.finish_execution(
            conversation_id,
            owner_id=owner_id,
            execution_id=execution.execution_id,
            status="COMPLETED",
            expected_revision=output_turn.revision,
        )
        return {
            "mode": "mock",
            "output": output,
            "execution": _json_value(finished),
            "snapshot": _json_value(manager.read(conversation_id, owner_id=owner_id)),
        }

    def _send_live_message(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        """Execute one provider request through the canonical local records.

        The provider receives a bounded prompt assembled from the persisted
        conversation, authorized recalled sources and the current source-map
        revision.  The request is only started after the input transcript and
        execution checkpoint have been committed.
        """

        message = _require_text(payload, "message", max_length=MAX_MESSAGE_LENGTH)
        conversation_id = _require_text(payload, "conversation_id", max_length=256)
        owner_id = self._owner(payload)
        manager = self._manager_for_request()
        snapshot = manager.read(conversation_id, owner_id=owner_id)
        if snapshot.conversation.status != "ACTIVE":
            raise DesktopServiceError("CONVERSATION_NOT_ACTIVE", "conversation is not active")
        if any(turn.status in {"QUEUED", "RUNNING", "WAITING_APPROVAL"} for turn in snapshot.turns):
            raise DesktopServiceError("CONVERSATION_BUSY", "conversation has unresolved execution state")
        if any(call.status in {"REQUESTED", "AMBIGUOUS"} for call in snapshot.tool_calls):
            raise DesktopServiceError("CONVERSATION_BUSY", "conversation has unresolved tool state")

        source_result = self._workspace_source_snapshot({})
        source_snapshot = cast(dict[str, Any], source_result["snapshot"])
        source_revision = str(source_snapshot["revision"])
        learning = self._learning_for_request()
        transcript = self._transcript_text(snapshot, message)
        session_id = self._transcript_session_id(conversation_id, snapshot.conversation.revision + 1)
        try:
            learning.index_session_scoped(
                session_id,
                transcript,
                scope_kind="USER_PRIVATE",
                owner_id=owner_id,
            )
        except Exception as error:
            raise DesktopServiceError(
                "MEMORY_PERSISTENCE_FAILED", "conversation source could not be persisted"
            ) from error

        context = self._compile_recalled_context(learning, message, owner_id)
        prompt = self._render_provider_prompt(snapshot, message, source_snapshot, context)
        connection = self._connection_for_conversation(snapshot.conversation.connection_id)
        model_id = snapshot.conversation.model_id
        if model_id == "mock":
            model_id = os.environ.get("AEGIS_DESKTOP_MODEL_ID", "").strip()
        if not model_id:
            raise DesktopServiceError("MODEL_NOT_CONFIGURED", "configure a model before using live mode")
        client = self._provider_client(connection, model_id)

        user_turn = manager.append_turn(
            conversation_id,
            owner_id=owner_id,
            turn_id=f"turn-{uuid.uuid4().hex}",
            role="user",
            content=message,
            connection_id=connection.connection_id,
            model_id=model_id,
            status="COMPLETED",
            expected_revision=snapshot.conversation.revision,
        )
        assistant_turn = manager.append_turn(
            conversation_id,
            owner_id=owner_id,
            turn_id=f"turn-{uuid.uuid4().hex}",
            role="assistant",
            content="",
            connection_id=connection.connection_id,
            model_id=model_id,
            status="QUEUED",
            expected_revision=user_turn.revision,
        )
        execution = manager.start_execution(
            conversation_id,
            owner_id=owner_id,
            execution_id=f"exec-{uuid.uuid4().hex}",
            turn_id=assistant_turn.turn_id,
            provider_kind=connection.provider_kind,
            connection_id=connection.connection_id,
            model_id=model_id,
            expected_revision=assistant_turn.revision,
        )
        checkpoint_revision = int(getattr(execution, "revision", 0))
        checkpoint = getattr(manager, "checkpoint_execution", None)
        if callable(checkpoint):
            checkpoint_record = checkpoint(
                conversation_id,
                owner_id=owner_id,
                execution_id=execution.execution_id,
                sequence=1,
                state="REQUEST_STARTED",
                continuation_json=json.dumps(
                    {
                        "schema": "aegis-desktop-provider-continuation-v1",
                        "source_revision": source_revision,
                        "context_manifest_hash": context.manifest_hash,
                        "context_token_budget": context.token_budget,
                        "context_token_count": context.token_count,
                        "context_item_count": len(context.items),
                        "context_selection_backend": context.manifest.get("selection_backend"),
                        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                expected_revision=execution.revision,
            )
            checkpoint_revision = int(getattr(checkpoint_record, "revision", 0))

        try:
            task_id = (
                int.from_bytes(
                    hashlib.blake2b(execution.execution_id.encode("utf-8"), digest_size=16).digest(),
                    "big",
                )
                or 1
            )
            with coordinated_runtime_task_sync(
                task_id=task_id,
                work_kind="Agent",
                priority="Foreground",
                side_effect_class="ExternalSideEffect",
                trust_level=self.trust_level,
            ):
                output = client.invoke(prompt)
            if not isinstance(output, str) or not output.strip():
                raise RuntimeError("provider returned empty text")
        except Exception as error:
            manager.finish_execution(
                conversation_id,
                owner_id=owner_id,
                execution_id=execution.execution_id,
                status="FAILED",
                expected_revision=checkpoint_revision,
            )
            raise DesktopServiceError("PROVIDER_ERROR", "live provider request failed") from error

        output_turn = manager.append_part(
            conversation_id,
            owner_id=owner_id,
            turn_id=assistant_turn.turn_id,
            kind="TEXT",
            content=output,
            expected_revision=checkpoint_revision,
        )
        finished = manager.finish_execution(
            conversation_id,
            owner_id=owner_id,
            execution_id=execution.execution_id,
            status="COMPLETED",
            expected_revision=output_turn.revision,
        )
        final_source = cast(dict[str, Any], self._workspace_source_snapshot({})["snapshot"])
        post_persisted = True
        try:
            final_snapshot = manager.read(conversation_id, owner_id=owner_id)
            learning.index_session_scoped(
                self._transcript_session_id(conversation_id, final_snapshot.conversation.revision),
                self._transcript_text(final_snapshot, ""),
                scope_kind="USER_PRIVATE",
                owner_id=owner_id,
            )
        except Exception:
            post_persisted = False
        return {
            "mode": "live",
            "output": output,
            "execution": _json_value(finished),
            "snapshot": _json_value(manager.read(conversation_id, owner_id=owner_id)),
            "source_revision": source_revision,
            "source_changed_during_request": final_source["revision"] != source_revision,
            "context_manifest_hash": context.manifest_hash,
            "transcript_persisted": post_persisted,
        }

    def _connection_for_conversation(self, connection_id: str) -> Any:
        catalog = self._connections_for_request()
        records = catalog.list_connections()
        connection = next((item for item in records if item.connection_id == connection_id), None)
        if connection is not None:
            return connection
        endpoint = os.environ.get("AEGIS_DESKTOP_PROVIDER_ENDPOINT", "").strip()
        if connection_id != "local" or not endpoint:
            raise DesktopServiceError(
                "CONNECTION_NOT_CONFIGURED", "configure the conversation connection before live mode"
            )
        connection = catalog.register_connection(
            "local",
            provider_kind=os.environ.get("AEGIS_DESKTOP_PROVIDER_KIND", "openai-compatible"),
            endpoint=endpoint,
            protocol=os.environ.get("AEGIS_DESKTOP_PROVIDER_PROTOCOL", "chat-completions"),
            secret_ref=os.environ.get("AEGIS_DESKTOP_SECRET_REF") or None,
        )
        return connection

    def _provider_client(self, connection: Any, model_id: str) -> Any:
        parsed = urlsplit(str(connection.endpoint))
        egress_check: Callable[[str], bool] | None = None
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            catalog = self._connections_for_request()

            def check_egress(connection_key: str) -> bool:
                return bool(catalog.check_egress(connection_key, "PROJECT_TEXT", "foreground_inference"))

            egress_check = check_egress
        try:
            return self._client_factory(connection, model=model_id, egress_check=egress_check)
        except TypeError:
            return self._client_factory(connection, model=model_id)

    def _compile_recalled_context(self, learning: Any, query: str, owner_id: str) -> Any:
        candidates = learning.search_past(query, top_k=8).results
        return ContextCompiler(learning, token_budget=4096, candidate_cap=8).compile(
            query,
            candidates,
            scope_kind="USER_PRIVATE",
            owner_id=owner_id,
        )

    def _render_provider_prompt(
        self,
        snapshot: Any,
        message: str,
        source_snapshot: dict[str, Any],
        context: Any,
    ) -> str:
        history: list[str] = []
        for turn in snapshot.turns[-MAX_HISTORY_TURNS:]:
            content = turn.content.strip()
            if content:
                history.append(f"{turn.role}: {content[:8192]}")
        raw_files: object = source_snapshot.get("files", [])
        raw_file_items = cast(list[object], raw_files) if isinstance(raw_files, list) else []
        file_records: list[dict[str, Any]] = [
            cast(dict[str, Any], item) for item in raw_file_items if isinstance(item, dict)
        ]
        files = [str(item.get("relative_path", "")) for item in file_records]
        selected_files = _select_source_paths_for_prompt(file_records, message)
        repository_map = None
        if self._source_snapshot is not None:
            try:
                repository_map = build_repository_map(
                    self._source_snapshot,
                    query=message,
                    token_budget=2_048,
                    max_files=MAX_SOURCE_FILES_IN_PROMPT,
                )
            except CodeIntelligenceError:
                # The legacy bounded path list remains a safe compatibility
                # projection when the optional symbol map cannot fit or build.
                repository_map = None
        source_context = (
            repository_map.rendered
            if repository_map is not None
            else "\n".join(
                (
                    f"source_revision: {source_snapshot.get('revision', '')}",
                    f"source_files_total: {len([item for item in files if item])}",
                    f"source_files_selected: {len(selected_files)}",
                    f"source_files_omitted: {max(0, len([item for item in files if item]) - len(selected_files))}",
                    "source_file_selection: bounded lexical path candidates; not dependency proof",
                    f"source_files: {', '.join(selected_files)}",
                )
            )
        )
        prompt = "\n".join(
            [
                "[AEGIS LOCAL WORKSPACE CONTEXT]",
                source_context,
                "[PERSISTED CONVERSATION]",
                *history,
                "[RECALLED AUTHORIZED CONTEXT]",
                context.rendered or "(none)",
                "[CURRENT USER MESSAGE]",
                message,
            ]
        )
        return prompt[:MAX_PROMPT_LENGTH]

    def _transcript_text(self, snapshot: Any, pending_message: str) -> str:
        lines = [f"conversation: {snapshot.conversation.conversation_id}"]
        lines.extend(
            f"{turn.role}: {turn.content[:8192]}"
            for turn in snapshot.turns[-MAX_HISTORY_TURNS:]
            if turn.content.strip()
        )
        if pending_message.strip():
            lines.append(f"user: {pending_message[:8192]}")
        return "\n".join(lines)

    def _transcript_session_id(self, conversation_id: str, revision: int) -> int:
        digest = hashlib.sha256(
            f"aegis-desktop-transcript-v1:{self.profile_id}:{conversation_id}:{revision}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") or 1

    def _search_memories(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        learning = self._learning_for_request()
        records = learning.search_memories(
            _require_text(payload, "query", max_length=MAX_MESSAGE_LENGTH),
            _require_top_k(payload),
            scope_kind=str(payload.get("scope_kind", "USER_PRIVATE")),
            owner_id=self._owner(payload),
            subject_id=(str(payload["subject_id"]) if payload.get("subject_id") is not None else None),
        )
        return {"records": _json_value(records)}

    def _inspect_memory(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        record = self._learning_for_request().inspect_memory(
            _require_text(payload, "memory_id", max_length=128),
            scope_kind=str(payload.get("scope_kind", "USER_PRIVATE")),
            owner_id=self._owner(payload),
            subject_id=(str(payload["subject_id"]) if payload.get("subject_id") is not None else None),
        )
        return {"record": _json_value(record) if record is not None else None}

    def _capture_memory(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return {
            "result": self._learning_for_request().capture_memory(
                _require_text(payload, "memory_id", max_length=128),
                _require_text(payload, "content", max_length=MAX_MESSAGE_LENGTH),
                scope_kind=str(payload.get("scope_kind", "USER_PRIVATE")),
                owner_id=self._owner(payload),
                subject_id=(str(payload["subject_id"]) if payload.get("subject_id") is not None else None),
                expected_revision=(
                    _require_revision(payload) if payload.get("expected_revision") is not None else None
                ),
                memory_kind=(str(payload["memory_kind"]) if payload.get("memory_kind") is not None else None),
            )
        }

    def _correct_memory(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return {
            "result": self._learning_for_request().correct_memory(
                _require_text(payload, "memory_id", max_length=128),
                _require_text(payload, "content", max_length=MAX_MESSAGE_LENGTH),
                expected_revision=_require_revision(payload),
                scope_kind=str(payload.get("scope_kind", "USER_PRIVATE")),
                owner_id=self._owner(payload),
                subject_id=(str(payload["subject_id"]) if payload.get("subject_id") is not None else None),
            )
        }

    def _forget_memory(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return {"result": self._memory_transition("forget_memory", payload)}

    def _restore_memory(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return {"result": self._memory_transition("restore_memory", payload)}

    def _purge_memory(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return {"result": self._memory_transition("purge_memory", payload)}

    def _memory_transition(self, method_name: str, payload: dict[str, Any]) -> Any:
        method = getattr(self._learning_for_request(), method_name)
        return method(
            _require_text(payload, "memory_id", max_length=128),
            scope_kind=str(payload.get("scope_kind", "USER_PRIVATE")),
            owner_id=self._owner(payload),
            subject_id=(str(payload["subject_id"]) if payload.get("subject_id") is not None else None),
            expected_revision=_require_revision(payload),
        )

    def dispatch(self, frame: bytes | str) -> bytes:
        return self._router.dispatch(frame)

    def _ready_frame(self) -> bytes:
        return json.dumps(self.ready_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    def run(self, input_stream: Any = None, output_stream: Any = None, error_stream: TextIO | None = None) -> int:
        source = input_stream or sys.stdin.buffer
        sink = output_stream or sys.stdout.buffer
        errors = error_stream or sys.stderr
        sink.write(self._ready_frame() + b"\n")
        sink.flush()
        for frame in _iter_frames(source, max_frame_bytes=MAX_FRAME_BYTES):
            if not frame.strip():
                continue
            try:
                response = self.dispatch(frame)
            except DesktopProtocolError as error:
                response = encode_response("invalid", error=error)
            except Exception as error:
                print(f"desktop service request failed: {type(error).__name__}", file=errors)
                response = encode_response(
                    "invalid",
                    error=DesktopProtocolError("INTERNAL_ERROR", "desktop command failed"),
                )
            sink.write(response + b"\n")
            sink.flush()
            if self._stop_requested:
                break
        self.close()
        return 0


def _iter_frames(stream: Any, *, max_frame_bytes: int) -> Iterator[bytes]:
    while True:
        frame = stream.readline(max_frame_bytes + 2)
        if not frame:
            return
        if len(frame) > max_frame_bytes + 1 or (len(frame) == max_frame_bytes + 1 and not frame.endswith(b"\n")):
            yield b"{}" + b" " * (max_frame_bytes + 1)
            while frame and not frame.endswith(b"\n"):
                frame = stream.readline(max_frame_bytes + 2)
            continue
        yield frame.rstrip(b"\r\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AEGIS local desktop sidecar")
    parser.add_argument("--profile-root", type=Path, default=None)
    parser.add_argument("--profile-id", default="local-profile")
    args = parser.parse_args(argv)
    return DesktopService(profile_root=args.profile_root, profile_id=args.profile_id).run()


__all__ = ["READY_SCHEMA", "DesktopService", "DesktopServiceError", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
