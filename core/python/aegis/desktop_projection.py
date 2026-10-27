"""Bounded, redacted projections for the desktop observer.

The renderer needs to explain execution without receiving provider prompts,
full tool arguments, or continuation payloads. This module derives a small
read-only view from canonical conversation records and already-redacted
subagent events; it does not own state, scheduling, or authority.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from .conversations import ConversationSnapshot


CONVERSATION_INSPECTION_SCHEMA_V1 = "aegis-desktop-conversation-inspection-v1"
SUBAGENT_GRAPH_SCHEMA_V1 = "aegis-desktop-subagent-graph-v1"
_PENDING_APPROVAL_STATUSES = frozenset({"REQUESTED", "AMBIGUOUS"})
_MAX_TIMELINE_ITEMS = 128
_MAX_APPROVALS = 32
_MAX_APPROVAL_ARGUMENTS = 12
_MAX_APPROVAL_VALUE_CHARS = 160
_MAX_GRAPH_NODES = 128
_SAFE_APPROVAL_VALUE_KEYS = frozenset(
    {
        "action",
        "directory",
        "end_line",
        "file",
        "method",
        "name",
        "operation",
        "path",
        "resource",
        "start_line",
        "target",
    }
)
_SAFE_APPROVAL_LICENSE_IDS = frozenset({"Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "INTERNAL", "ISC", "MIT"})
_APPROVAL_SENSITIVE_MARKERS = (
    "api_key",
    "api-key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


def _text(value: object, *, limit: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _positive_int(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _approval_relative_path(value: object) -> str:
    if type(value) is not str or not value or len(value) > _MAX_APPROVAL_VALUE_CHARS or "\x00" in value:
        return "REDACTED"
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold().startswith(".env") for part in parts)
        or any(marker in normalized.casefold() for marker in _APPROVAL_SENSITIVE_MARKERS)
    ):
        return "REDACTED"
    return normalized


def _approval_argument_preview(arguments: Mapping[str, object]) -> list[dict[str, str]]:
    preview: list[dict[str, str]] = []
    for key in sorted(arguments)[:_MAX_APPROVAL_ARGUMENTS]:
        value = arguments[key]
        normalized_key = key.casefold().replace("-", "_")
        if normalized_key in {"source_path", "target_relative_path"}:
            display_value = _approval_relative_path(value)
        elif normalized_key == "license_id":
            display_value = (
                f"{value} (declared; unverified)"
                if type(value) is str and value in _SAFE_APPROVAL_LICENSE_IDS
                else "REDACTED"
            )
        elif normalized_key not in _SAFE_APPROVAL_VALUE_KEYS:
            display_value = "REDACTED"
        elif type(value) is str:
            if (
                len(value) > _MAX_APPROVAL_VALUE_CHARS
                or "://" in value
                or "@" in value
                or any(marker in value.casefold() for marker in _APPROVAL_SENSITIVE_MARKERS)
            ):
                display_value = "REDACTED"
            else:
                display_value = value
        elif value is None or type(value) in (bool, int, float):
            try:
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            except TypeError, ValueError:
                display_value = "REDACTED"
            else:
                display_value = encoded if len(encoded) <= _MAX_APPROVAL_VALUE_CHARS else "REDACTED"
        else:
            display_value = "REDACTED"
        preview.append({"key": key[:128], "value": display_value})
    if len(arguments) > _MAX_APPROVAL_ARGUMENTS:
        preview.append({"key": "…", "value": f"{len(arguments) - _MAX_APPROVAL_ARGUMENTS} more fields"})
    return preview


def _project_context_inclusion(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or value.get("schema") != "aegis-context-inclusion-manifest-v1":
        return None
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > 100:
        return None
    included = 0
    omitted = 0
    reasons: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            return None
        status = item.get("status")
        if status == "INCLUDED":
            included += 1
        elif status == "OMITTED":
            omitted += 1
            reason = item.get("omitted_reason")
            if reason not in {"PROMPT_LIMIT"}:
                return None
            reasons.add(str(reason))
        else:
            return None
    prompt_chars = _positive_int(value.get("prompt_chars"))
    prompt_limit_chars = _positive_int(value.get("prompt_limit_chars"))
    manifest_hash = _text(value.get("manifest_sha256"), limit=64).lower()
    if (
        value.get("status") != "COMPOSED"
        or prompt_chars is None
        or prompt_limit_chars is None
        or len(manifest_hash) != 64
        or any(character not in "0123456789abcdef" for character in manifest_hash)
    ):
        return None
    return {
        "schema": "aegis-context-inclusion-inspection-v1",
        "status": "COMPOSED",
        "manifest_sha256": manifest_hash,
        "prompt_chars": prompt_chars,
        "prompt_limit_chars": prompt_limit_chars,
        "selected_count": len(raw_items),
        "included_count": included,
        "omitted_count": omitted,
        "omitted_reasons": sorted(reasons),
        "deduplicated_turn_count": _positive_int(value.get("deduplicated_turn_count")) or 0,
    }


def _project_provider_attempt(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or value.get("schema") != "aegis-provider-request-attempt-v1":
        return None
    ordinal = _positive_int(value.get("ordinal"))
    payload_bytes = _positive_int(value.get("payload_bytes"))
    payload_hash = _text(value.get("payload_sha256"), limit=64).lower()
    if (
        value.get("state") != "DISPATCH_ATTEMPTED"
        or ordinal is None
        or ordinal < 1
        or payload_bytes is None
        or payload_bytes < 1
        or len(payload_hash) != 64
        or any(character not in "0123456789abcdef" for character in payload_hash)
    ):
        return None
    return {
        "schema": "aegis-provider-request-attempt-inspection-v1",
        "ordinal": ordinal,
        "state": "DISPATCH_ATTEMPTED",
        "payload_sha256": payload_hash,
        "payload_bytes": payload_bytes,
    }


def _context_from_snapshot(snapshot: ConversationSnapshot, source_revision: str | None) -> dict[str, object]:
    latest: dict[str, object] = {}
    latest_inclusion: dict[str, object] | None = None
    latest_attempt: dict[str, object] | None = None
    request_attempt_count = 0
    latest_execution_id: str | None = None
    latest_assistant_turn = max(
        (turn for turn in snapshot.turns if turn.role == "assistant"),
        key=lambda turn: (turn.ordinal, turn.turn_id),
        default=None,
    )
    if latest_assistant_turn is not None:
        execution_by_id = {execution.execution_id: execution for execution in snapshot.executions}
        selected_execution = execution_by_id.get(latest_assistant_turn.execution_id or "")
        if selected_execution is not None and selected_execution.turn_id == latest_assistant_turn.turn_id:
            latest_execution_id = selected_execution.execution_id
        elif latest_assistant_turn.execution_id is None:
            executions_for_turn = [
                execution for execution in snapshot.executions if execution.turn_id == latest_assistant_turn.turn_id
            ]
            if len(executions_for_turn) == 1:
                latest_execution_id = executions_for_turn[0].execution_id
    for checkpoint in sorted(snapshot.checkpoints, key=lambda item: item.sequence):
        if latest_execution_id is None or checkpoint.execution_id != latest_execution_id:
            continue
        try:
            decoded = json.loads(checkpoint.continuation_json)
        except TypeError, ValueError:
            continue
        if isinstance(decoded, dict):
            latest.update({key: item for key, item in decoded.items() if item is not None})
            inclusion = _project_context_inclusion(decoded.get("context_inclusion"))
            if inclusion is not None:
                latest_inclusion = inclusion
            attempt = _project_provider_attempt(decoded.get("provider_request_attempt"))
            if attempt is not None:
                latest_attempt = attempt
                request_attempt_count += 1

    def value(key: str) -> object:
        candidate = latest.get(key)
        return candidate if candidate is not None else None

    manifest_hash = _text(value("context_manifest_hash"), limit=256)
    prompt_hash = _text(value("prompt_hash"), limit=256)
    captured_revision = _text(value("source_revision"), limit=256) or source_revision
    return {
        "schema": "aegis-desktop-context-inspection-v1",
        "status": "COMPOSED" if latest_inclusion is not None else "AVAILABLE" if manifest_hash else "NOT_CAPTURED",
        "source_revision": captured_revision,
        "context_manifest_hash": manifest_hash or None,
        "prompt_hash": prompt_hash or None,
        "token_budget": _positive_int(value("context_token_budget")),
        "token_count": _positive_int(value("context_token_count")),
        "item_count": _positive_int(value("context_item_count")),
        "selection_backend": _text(value("context_selection_backend"), limit=128) or None,
        "inclusion": latest_inclusion,
        "request_attempt": latest_attempt,
        "request_attempt_count": request_attempt_count,
        "history_turn_count": len(snapshot.turns),
        "sensitive_content": "REDACTED",
    }


def _timeline(snapshot: ConversationSnapshot) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []

    for turn in snapshot.turns:
        timestamp = getattr(turn, "created_at_ms", 0)
        if type(timestamp) is not int or timestamp < 0:
            timestamp = 0
        items.append(
            {
                "event_id": f"turn:{turn.turn_id}",
                "kind": "TURN",
                "status": _text(turn.status, limit=64),
                "title": f"{_text(turn.role, limit=32).capitalize()} turn",
                "detail": "Turn content is hidden from the observer projection.",
                "timestamp_ms": timestamp,
                "reference": _text(turn.turn_id, limit=256),
            }
        )

    items.extend(
        {
            "event_id": f"execution:{execution.execution_id}",
            "kind": "EXECUTION",
            "status": _text(execution.status, limit=64),
            "title": f"Provider execution {execution.status.lower()}",
            "detail": f"{_text(execution.model_id, limit=128)} · checkpoint {execution.checkpoint_seq}",
            "timestamp_ms": execution.finished_at_ms or execution.started_at_ms,
            "reference": _text(execution.execution_id, limit=256),
        }
        for execution in snapshot.executions
    )

    items.extend(
        {
            "event_id": f"checkpoint:{checkpoint.execution_id}:{checkpoint.sequence}",
            "kind": "CHECKPOINT",
            "status": _text(checkpoint.state, limit=64),
            "title": f"Checkpoint {checkpoint.state.lower()}",
            "detail": f"Sequence {checkpoint.sequence}; continuation body redacted.",
            "timestamp_ms": checkpoint.created_at_ms,
            "reference": _text(checkpoint.continuation_hash, limit=256),
        }
        for checkpoint in snapshot.checkpoints
    )

    items.extend(
        {
            "event_id": f"tool:{call.call_id}",
            "kind": "TOOL_CALL",
            "status": _text(call.status, limit=64),
            "title": f"Tool {call.status.lower()}",
            "detail": f"{_text(call.tool_name, limit=128)}; arguments and result redacted.",
            "timestamp_ms": call.completed_at_ms or call.created_at_ms,
            "reference": _text(call.call_id, limit=256),
        }
        for call in snapshot.tool_calls
    )

    return sorted(
        items,
        key=lambda item: (int(item.get("timestamp_ms", 0)), str(item.get("event_id", ""))),
    )[-_MAX_TIMELINE_ITEMS:]


def _pending_approvals(snapshot: ConversationSnapshot) -> list[dict[str, object]]:
    approvals: list[dict[str, object]] = []
    for call in snapshot.tool_calls:
        if call.status not in _PENDING_APPROVAL_STATUSES:
            continue
        argument_keys: list[str] = []
        argument_preview: list[dict[str, str]] = []
        try:
            arguments = json.loads(call.arguments_json)
        except TypeError, ValueError:
            arguments = None
        if isinstance(arguments, dict):
            argument_keys = sorted(str(key)[:128] for key in arguments)[:32]
            argument_preview = _approval_argument_preview(arguments)
        approvals.append(
            {
                "approval_id": _text(call.call_id, limit=256),
                "tool_name": _text(call.tool_name, limit=128),
                "status": _text(call.status, limit=64),
                "risk": "REVIEW_REQUIRED",
                "argument_keys": argument_keys,
                "argument_preview": argument_preview,
                "arguments": "REDACTED",
                "result": "REDACTED",
                "can_resolve": False,
                "can_reconcile": False,
                "effect_class": "unknown",
            }
        )
        if len(approvals) >= _MAX_APPROVALS:
            break
    return approvals


def build_conversation_inspection(
    snapshot: ConversationSnapshot,
    *,
    source_revision: str | None = None,
) -> dict[str, object]:
    """Return a bounded observer view without exposing sensitive payloads."""

    if type(snapshot) is not ConversationSnapshot:
        raise TypeError("snapshot must be a ConversationSnapshot")
    return {
        "schema": CONVERSATION_INSPECTION_SCHEMA_V1,
        "conversation_id": snapshot.conversation.conversation_id,
        "revision": snapshot.conversation.revision,
        "context": _context_from_snapshot(snapshot, source_revision),
        "timeline": _timeline(snapshot),
        "approvals": _pending_approvals(snapshot),
        "redaction": (
            "provider prompts, sensitive tool values, full results, and continuation bodies are redacted; "
            "only bounded allowlisted tool-argument values may appear in approval previews"
        ),
    }


def build_subagent_graph(
    run_id: str,
    events: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Build a task graph from already-redacted observer events."""

    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 128:
        raise ValueError("run_id must be a bounded non-empty string")
    nodes: dict[int, dict[str, object]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        task_id = event.get("task_id")
        if type(task_id) is not int or task_id <= 0:
            continue
        if task_id not in nodes and len(nodes) >= _MAX_GRAPH_NODES:
            break
        parent = event.get("parent_task_id")
        parent_id = parent if type(parent) is int and parent >= 0 else None
        node = nodes.setdefault(
            task_id,
            {
                "task_id": task_id,
                "parent_task_id": parent_id,
                "status": "QUEUED",
                "role": "",
                "dependencies": [],
                "capabilities": [],
                "side_effect_class": "",
                "summary": "",
                "uncertainty": [],
                "blockers": [],
                "redacted": True,
            },
        )
        if parent_id is not None:
            node["parent_task_id"] = parent_id
        payload = event.get("payload")
        payload_map = payload if isinstance(payload, Mapping) else {}
        kind = event.get("message_kind")
        if kind == "TASK_REQUEST":
            node["status"] = "RUNNING"
            node["role"] = _text(payload_map.get("role"), limit=128)
            node["dependencies"] = [
                item for item in payload_map.get("dependency_ids", []) if type(item) is int and item > 0
            ][:64]
            node["capabilities"] = [
                _text(item, limit=128) for item in payload_map.get("capabilities", []) if isinstance(item, str)
            ][:64]
            node["side_effect_class"] = _text(payload_map.get("side_effect_class"), limit=128)
        elif kind == "TASK_RESULT":
            node["status"] = _text(payload_map.get("status"), limit=64) or "COMPLETED"
            node["summary"] = _text(payload_map.get("summary"), limit=512)
            node["uncertainty"] = [
                _text(item, limit=256) for item in payload_map.get("uncertainty", []) if isinstance(item, str)
            ][:16]
            node["blockers"] = [
                _text(item, limit=256) for item in payload_map.get("blockers", []) if isinstance(item, str)
            ][:16]

    ordered = sorted(nodes.values(), key=lambda node: int(node["task_id"]))
    edges = [
        {
            "from": int(dependency),
            "to": int(node["task_id"]),
        }
        for node in ordered
        for dependency in node["dependencies"]
    ]
    return {
        "schema": SUBAGENT_GRAPH_SCHEMA_V1,
        "run_id": run_id,
        "nodes": ordered,
        "edges": edges[:256],
        "redaction": "task prompts and raw result bodies are redacted",
    }


__all__ = [
    "CONVERSATION_INSPECTION_SCHEMA_V1",
    "SUBAGENT_GRAPH_SCHEMA_V1",
    "build_conversation_inspection",
    "build_subagent_graph",
]
