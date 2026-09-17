"""Local desktop sidecar for the bounded AEGIS command protocol.

The service owns transport and lifecycle only. Durable state and authorization
remain in the Rust-backed Python adapters; the desktop renderer never receives
direct database, filesystem, shell, or provider access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, TextIO, cast
from urllib.parse import urlsplit

from .config import trust_policy_hash
from .application import AgentApplication
from .config import AgentConfig
from .runtime import (
    coordinated_runtime_task_sync,
    native_runtime_available,
    normalize_runtime_trust_level,
)
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
        self._verification_facade = VerificationFacade()
        self._router = DesktopCommandRouter(self._handlers())

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Mapping[str, Any]]]:
        return {
            "service.shutdown": self._shutdown,
            "workspace.open": self._open_workspace,
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
            "conversations.send": self._send_message,
            "conversations.switch_model": self._switch_model,
            "subagents.run": self._run_subagents,
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
        requested = payload.get("workspace_path")
        if requested is not None:
            if (
                not isinstance(requested, str)
                or not requested.strip()
                or len(requested) > MAX_PATH_LENGTH
                or "\x00" in requested
            ):
                raise DesktopServiceError("INVALID_ARGUMENT", "workspace_path must be a bounded path")
            root = Path(requested).expanduser().resolve()
        else:
            root = self.profile_root.resolve()
        if self._opened_root is not None and root != self._opened_root:
            raise DesktopServiceError("WORKSPACE_ALREADY_OPEN", "the service already owns another workspace")
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
        return self._workspace_snapshot({})

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

    def _run_subagents(self, payload: dict[str, Any]) -> Mapping[str, Any]:
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
                turn.status in {"QUEUED", "RUNNING", "WAITING_APPROVAL"}
                for turn in conversation_snapshot.turns
            ) or any(call.status in {"REQUESTED", "AMBIGUOUS"} for call in conversation_snapshot.tool_calls):
                raise DesktopServiceError("CONVERSATION_BUSY", "conversation has unresolved execution state")

        raw_connection_id = payload.get("connection_id")
        if raw_connection_id is None and conversation_snapshot is not None:
            raw_connection_id = conversation_snapshot.conversation.connection_id
        connection_id = _require_text(
            {"connection_id": raw_connection_id}, "connection_id", max_length=256
        )

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
        try:
            result = runner(
                plan=(cast(Mapping[str, object], raw_plan) if raw_plan is not None else None),
                require_native_authority=require_native,
                max_concurrency=max_concurrency,
            )
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
        if conversation_manager is not None and conversation_id is not None and execution is not None and assistant_turn is not None:
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
                current = next(
                    item
                    for item in snapshot.executions
                    if item.execution_id == execution.execution_id
                )
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
                except (TypeError, ValueError):
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
