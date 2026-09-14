"""Bounded command protocol for the future desktop shell.

The shell may request typed application operations through this boundary. It
cannot submit arbitrary Python, SQL, filesystem or subprocess instructions.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

MAX_FRAME_BYTES = 1 * 1024 * 1024
PROTOCOL_VERSION = 1
REQUEST_SCHEMA = "aegis-desktop-command-v1"
ALLOWED_COMMANDS = frozenset(
    {
        "service.shutdown",
        "workspace.open",
        "workspace.snapshot",
        "workspace.source_snapshot",
        "connections.list",
        "connections.save",
        "connections.discover",
        "connections.disable",
        "models.list",
        "conversations.create",
        "conversations.list",
        "conversations.read",
        "conversations.send",
        "conversations.switch_model",
        "memory.search",
        "memory.inspect",
        "memory.capture",
        "memory.correct",
        "memory.forget",
        "memory.restore",
        "memory.purge",
        "runs.start",
        "runs.inspect",
        "runs.cancel",
        "runs.resume",
        "approvals.resolve",
        "objects.inspect",
        "objects.explain",
        "maintenance.backup",
        "maintenance.restore",
        "maintenance.rebuild",
        "verification.inspect_project",
        "verification.create_contract",
        "verification.start_session",
        "verification.get_agent_packet",
        "verification.observe_change",
        "verification.get_feedback",
        "verification.propose_test_change",
        "verification.evaluate_test_change",
        "verification.apply_test_change",
        "verification.request_deep_run",
        "verification.inspect_run",
        "verification.cancel_run",
        "verification.resume_session",
        "verification.read_report",
    }
)


class DesktopProtocolError(ValueError):
    """A frame failed a deterministic protocol or command policy check."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DesktopRequest:
    request_id: str
    command: str
    payload: dict[str, Any]
    protocol_version: int


def decode_request(frame: bytes | str, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> DesktopRequest:
    if not isinstance(max_frame_bytes, int) or isinstance(max_frame_bytes, bool) or max_frame_bytes < 1:
        raise ValueError("max_frame_bytes must be a positive integer")
    if isinstance(frame, bytes):
        if len(frame) > max_frame_bytes:
            raise DesktopProtocolError("FRAME_TOO_LARGE", "desktop frame exceeds the configured limit")
        try:
            text = frame.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DesktopProtocolError("INVALID_ENCODING", "desktop frame must be UTF-8") from exc
    elif isinstance(frame, str):
        if len(frame.encode("utf-8")) > max_frame_bytes:
            raise DesktopProtocolError("FRAME_TOO_LARGE", "desktop frame exceeds the configured limit")
        text = frame
    else:
        raise DesktopProtocolError("INVALID_FRAME", "desktop frame must be bytes or text")
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except DesktopProtocolError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DesktopProtocolError("INVALID_JSON", "desktop frame is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise DesktopProtocolError("INVALID_FRAME", "desktop request must be an object")
    version = raw.get("protocol_version")
    schema = raw.get("schema")
    request_id = raw.get("request_id")
    command = raw.get("command")
    payload = raw.get("payload")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != PROTOCOL_VERSION
    ):
        raise DesktopProtocolError("PROTOCOL_VERSION_UNSUPPORTED", "desktop protocol version is unsupported")
    if schema != REQUEST_SCHEMA:
        raise DesktopProtocolError("INVALID_SCHEMA", "desktop request schema is unsupported")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise DesktopProtocolError("INVALID_REQUEST_ID", "request_id must be a bounded non-empty string")
    if not isinstance(command, str) or not command.strip() or len(command) > 128:
        raise DesktopProtocolError("INVALID_COMMAND", "command must be a bounded non-empty string")
    if command not in ALLOWED_COMMANDS:
        raise DesktopProtocolError("COMMAND_NOT_ALLOWED", "desktop command is not allowed")
    if not isinstance(payload, dict):
        raise DesktopProtocolError("INVALID_PAYLOAD", "desktop payload must be an object")
    return DesktopRequest(request_id, command, payload, version)


def encode_response(
    request_id: str,
    *,
    result: Mapping[str, Any] | None = None,
    error: DesktopProtocolError | None = None,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> bytes:
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError("request_id must be a bounded non-empty string")
    if (result is None) == (error is None):
        raise ValueError("exactly one of result or error must be provided")
    if error is None:
        body: dict[str, Any] = {
            "schema": "aegis-desktop-response-v1",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "status": "ok",
            "result": dict(result or {}),
        }
    else:
        body = {
            "schema": "aegis-desktop-response-v1",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "status": "error",
            "error": {"code": error.code, "message": str(error)},
        }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_frame_bytes:
        raise DesktopProtocolError("FRAME_TOO_LARGE", "desktop response exceeds the configured limit")
    return encoded


Handler = Callable[[dict[str, Any]], Mapping[str, Any]]


class DesktopCommandRouter:
    """Dispatch only allowlisted commands to typed application handlers."""

    def __init__(self, handlers: Mapping[str, Handler], *, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if not isinstance(max_frame_bytes, int) or isinstance(max_frame_bytes, bool) or max_frame_bytes < 1:
            raise ValueError("max_frame_bytes must be a positive integer")
        unknown = set(handlers).difference(ALLOWED_COMMANDS)
        if unknown:
            raise ValueError("handlers contain commands outside the desktop allowlist")
        self._handlers = dict(handlers)
        self._max_frame_bytes = max_frame_bytes

    def dispatch(self, frame: bytes | str) -> bytes:
        request = decode_request(frame, max_frame_bytes=self._max_frame_bytes)
        handler = self._handlers.get(request.command)
        if handler is None:
            return encode_response(
                request.request_id,
                error=DesktopProtocolError("COMMAND_UNAVAILABLE", "desktop command is not available"),
                max_frame_bytes=self._max_frame_bytes,
            )
        try:
            result = handler(request.payload)
        except DesktopProtocolError as error:
            return encode_response(request.request_id, error=error, max_frame_bytes=self._max_frame_bytes)
        except Exception as exc:
            del exc
            return encode_response(
                request.request_id,
                error=DesktopProtocolError("INTERNAL_ERROR", "desktop command failed"),
                max_frame_bytes=self._max_frame_bytes,
            )
        if not isinstance(result, Mapping):
            return encode_response(
                request.request_id,
                error=DesktopProtocolError("INVALID_HANDLER_RESULT", "desktop handler returned a non-object"),
                max_frame_bytes=self._max_frame_bytes,
            )
        return encode_response(request.request_id, result=result, max_frame_bytes=self._max_frame_bytes)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DesktopProtocolError("DUPLICATE_KEY", "desktop JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    raise DesktopProtocolError("INVALID_NUMBER", f"desktop JSON contains unsupported numeric value {value}")


__all__ = [
    "ALLOWED_COMMANDS",
    "MAX_FRAME_BYTES",
    "PROTOCOL_VERSION",
    "REQUEST_SCHEMA",
    "DesktopCommandRouter",
    "DesktopProtocolError",
    "DesktopRequest",
    "decode_request",
    "encode_response",
]
