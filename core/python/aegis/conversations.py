"""Typed Python adapter for canonical, Rust-owned conversations."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .native import native_module


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    owner_id: str
    title: str
    connection_id: str
    model_id: str
    status: str
    revision: int
    created_at_ms: int
    updated_at_ms: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationRecord:
        return cls(
            conversation_id=str(data["conversation_id"]),
            owner_id=str(data["owner_id"]),
            title=str(data["title"]),
            connection_id=str(data["connection_id"]),
            model_id=str(data["model_id"]),
            status=str(data["status"]),
            revision=int(data["revision"]),
            created_at_ms=int(data["created_at_ms"]),
            updated_at_ms=int(data["updated_at_ms"]),
        )


@dataclass(frozen=True)
class ConversationTurn:
    conversation_id: str
    turn_id: str
    ordinal: int
    role: str
    status: str
    content: str
    connection_id: str | None
    model_id: str | None
    revision: int
    created_at_ms: int
    finished_at_ms: int | None
    execution_id: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationTurn:
        return cls(
            conversation_id=str(data["conversation_id"]),
            turn_id=str(data["turn_id"]),
            ordinal=int(data["ordinal"]),
            role=str(data["role"]),
            status=str(data["status"]),
            content=str(data["content"]),
            connection_id=(str(data["connection_id"]) if data.get("connection_id") is not None else None),
            model_id=(str(data["model_id"]) if data.get("model_id") is not None else None),
            revision=int(data["revision"]),
            created_at_ms=int(data["created_at_ms"]),
            finished_at_ms=(int(data["finished_at_ms"]) if data.get("finished_at_ms") is not None else None),
            execution_id=(str(data["execution_id"]) if data.get("execution_id") is not None else None),
        )


@dataclass(frozen=True)
class ConversationPart:
    conversation_id: str
    turn_id: str
    part_index: int
    kind: str
    content: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationPart:
        return cls(
            conversation_id=str(data["conversation_id"]),
            turn_id=str(data["turn_id"]),
            part_index=int(data["part_index"]),
            kind=str(data["kind"]),
            content=str(data["content"]),
        )


@dataclass(frozen=True)
class ConversationExecution:
    execution_id: str
    conversation_id: str
    turn_id: str
    provider_kind: str
    connection_id: str
    model_id: str
    status: str
    checkpoint_seq: int
    revision: int
    started_at_ms: int
    finished_at_ms: int | None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationExecution:
        return cls(
            execution_id=str(data["execution_id"]),
            conversation_id=str(data["conversation_id"]),
            turn_id=str(data["turn_id"]),
            provider_kind=str(data["provider_kind"]),
            connection_id=str(data["connection_id"]),
            model_id=str(data["model_id"]),
            status=str(data["status"]),
            checkpoint_seq=int(data["checkpoint_seq"]),
            revision=int(data["revision"]),
            started_at_ms=int(data["started_at_ms"]),
            finished_at_ms=(
                int(data["finished_at_ms"])
                if data.get("finished_at_ms") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ConversationCheckpoint:
    execution_id: str
    sequence: int
    state: str
    continuation_json: str
    continuation_hash: str
    created_at_ms: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationCheckpoint:
        return cls(
            execution_id=str(data["execution_id"]),
            sequence=int(data["sequence"]),
            state=str(data["state"]),
            continuation_json=str(data["continuation_json"]),
            continuation_hash=str(data["continuation_hash"]),
            created_at_ms=int(data["created_at_ms"]),
        )


@dataclass(frozen=True)
class ConversationToolCall:
    call_id: str
    conversation_id: str
    request_turn_id: str
    result_turn_id: str | None
    tool_name: str
    arguments_json: str
    result_content: str | None
    status: str
    revision: int
    created_at_ms: int
    completed_at_ms: int | None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationToolCall:
        return cls(
            call_id=str(data["call_id"]),
            conversation_id=str(data["conversation_id"]),
            request_turn_id=str(data["request_turn_id"]),
            result_turn_id=(
                str(data["result_turn_id"])
                if data.get("result_turn_id") is not None
                else None
            ),
            tool_name=str(data["tool_name"]),
            arguments_json=str(data["arguments_json"]),
            result_content=(
                str(data["result_content"])
                if data.get("result_content") is not None
                else None
            ),
            status=str(data["status"]),
            revision=int(data["revision"]),
            created_at_ms=int(data["created_at_ms"]),
            completed_at_ms=(
                int(data["completed_at_ms"])
                if data.get("completed_at_ms") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ConversationSnapshot:
    conversation: ConversationRecord
    turns: tuple[ConversationTurn, ...]
    executions: tuple[ConversationExecution, ...] = field(default_factory=tuple)
    checkpoints: tuple[ConversationCheckpoint, ...] = field(default_factory=tuple)
    tool_calls: tuple[ConversationToolCall, ...] = field(default_factory=tuple)
    parts: tuple[ConversationPart, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConversationSnapshot:
        return cls(
            conversation=ConversationRecord.from_mapping(data["conversation"]),
            turns=tuple(
                ConversationTurn.from_mapping(item)
                for item in data.get("turns", [])
                if isinstance(item, dict)
            ),
            parts=tuple(
                ConversationPart.from_mapping(item)
                for item in data.get("parts", [])
                if isinstance(item, dict)
            ),
            executions=tuple(
                ConversationExecution.from_mapping(item)
                for item in data.get("executions", [])
                if isinstance(item, dict)
            ),
            checkpoints=tuple(
                ConversationCheckpoint.from_mapping(item)
                for item in data.get("checkpoints", [])
                if isinstance(item, dict)
            ),
            tool_calls=tuple(
                ConversationToolCall.from_mapping(item)
                for item in data.get("tool_calls", [])
                if isinstance(item, dict)
            ),
        )


class ConversationManager:
    """Typed Python boundary; canonical state remains in the Rust repository."""

    def __init__(self, rust_bridge: Any = None) -> None:
        self._bridge = rust_bridge

    @property
    def _native(self) -> Any:
        if self._bridge is None:
            self._bridge = native_module()
        return self._bridge

    def create(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        title: str,
        connection_id: str,
        model_id: str,
        timestamp: int | None = None,
    ) -> ConversationRecord:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (title, "title"),
            (connection_id, "connection_id"),
            (model_id, "model_id"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_create_conversation(
            conversation_id,
            owner_id,
            title,
            connection_id,
            model_id,
            event_time,
        )
        return ConversationRecord.from_mapping(json.loads(raw)["record"])

    def list(self, owner_id: str) -> tuple[ConversationRecord, ...]:
        self._require_text(owner_id, "owner_id")
        raw = json.loads(self._native.aegis_list_conversations(owner_id))
        return tuple(
            ConversationRecord.from_mapping(item)
            for item in raw.get("records", [])
            if isinstance(item, dict)
        )

    def read(self, conversation_id: str, *, owner_id: str) -> ConversationSnapshot:
        self._require_text(conversation_id, "conversation_id")
        self._require_text(owner_id, "owner_id")
        raw = json.loads(self._native.aegis_read_conversation(conversation_id, owner_id))
        return ConversationSnapshot.from_mapping(raw["snapshot"])

    def append_turn(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        turn_id: str,
        role: str,
        content: str,
        expected_revision: int,
        connection_id: str | None = None,
        model_id: str | None = None,
        status: str = "COMPLETED",
        timestamp: int | None = None,
    ) -> ConversationTurn:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (turn_id, "turn_id"),
            (role, "role"),
            (status, "status"),
        ):
            self._require_text(value, name)
        if not isinstance(content, str) or (
            not content.strip() and status not in {"QUEUED", "RUNNING", "WAITING_APPROVAL"}
        ):
            raise ValueError("content must be non-empty for a terminal turn")
        for value, name in ((connection_id, "connection_id"), (model_id, "model_id")):
            if value is not None:
                self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_append_conversation_turn(
            conversation_id,
            owner_id,
            turn_id,
            role,
            content,
            connection_id,
            model_id,
            status,
            int(expected_revision),
            event_time,
        )
        return ConversationTurn.from_mapping(json.loads(raw)["record"])

    def switch_model(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        connection_id: str,
        model_id: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationRecord:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (connection_id, "connection_id"),
            (model_id, "model_id"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_switch_conversation_model(
            conversation_id,
            owner_id,
            connection_id,
            model_id,
            int(expected_revision),
            event_time,
        )
        return ConversationRecord.from_mapping(json.loads(raw)["record"])

    def set_status(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        status: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationRecord:
        self._require_text(conversation_id, "conversation_id")
        self._require_text(owner_id, "owner_id")
        self._require_text(status, "status")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_set_conversation_status(
            conversation_id,
            owner_id,
            status,
            int(expected_revision),
            event_time,
        )
        return ConversationRecord.from_mapping(json.loads(raw)["record"])

    def append_part(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        turn_id: str,
        kind: str,
        content: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationTurn:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (turn_id, "turn_id"),
            (kind, "kind"),
            (content, "content"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_append_conversation_part(
            conversation_id,
            owner_id,
            turn_id,
            kind,
            content,
            int(expected_revision),
            event_time,
        )
        return ConversationTurn.from_mapping(json.loads(raw)["record"])

    def transition_turn(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        turn_id: str,
        status: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationTurn:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (turn_id, "turn_id"),
            (status, "status"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_transition_conversation_turn(
            conversation_id,
            owner_id,
            turn_id,
            status,
            int(expected_revision),
            event_time,
        )
        return ConversationTurn.from_mapping(json.loads(raw)["record"])

    def start_execution(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        execution_id: str,
        turn_id: str,
        provider_kind: str,
        connection_id: str,
        model_id: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationExecution:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (execution_id, "execution_id"),
            (turn_id, "turn_id"),
            (provider_kind, "provider_kind"),
            (connection_id, "connection_id"),
            (model_id, "model_id"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_start_conversation_execution(
            conversation_id,
            owner_id,
            execution_id,
            turn_id,
            provider_kind,
            connection_id,
            model_id,
            int(expected_revision),
            event_time,
        )
        return ConversationExecution.from_mapping(json.loads(raw)["record"])

    def checkpoint_execution(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        execution_id: str,
        sequence: int,
        state: str,
        continuation_json: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationExecution:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (execution_id, "execution_id"),
            (state, "state"),
            (continuation_json, "continuation_json"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_checkpoint_conversation_execution(
            conversation_id,
            owner_id,
            execution_id,
            int(sequence),
            state,
            continuation_json,
            int(expected_revision),
            event_time,
        )
        return ConversationExecution.from_mapping(json.loads(raw)["record"])

    def finish_execution(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        execution_id: str,
        status: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationExecution:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (execution_id, "execution_id"),
            (status, "status"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_finish_conversation_execution(
            conversation_id,
            owner_id,
            execution_id,
            status,
            int(expected_revision),
            event_time,
        )
        return ConversationExecution.from_mapping(json.loads(raw)["record"])

    def record_tool_call(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        call_id: str,
        request_turn_id: str,
        tool_name: str,
        arguments_json: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationToolCall:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (call_id, "call_id"),
            (request_turn_id, "request_turn_id"),
            (tool_name, "tool_name"),
            (arguments_json, "arguments_json"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_record_conversation_tool_call(
            conversation_id,
            owner_id,
            call_id,
            request_turn_id,
            tool_name,
            arguments_json,
            int(expected_revision),
            event_time,
        )
        return ConversationToolCall.from_mapping(json.loads(raw)["record"])

    def record_tool_result(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        call_id: str,
        result_turn_id: str,
        result_content: str,
        status: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationToolCall:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (call_id, "call_id"),
            (result_turn_id, "result_turn_id"),
            (result_content, "result_content"),
            (status, "status"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_record_conversation_tool_result(
            conversation_id,
            owner_id,
            call_id,
            result_turn_id,
            result_content,
            status,
            int(expected_revision),
            event_time,
        )
        return ConversationToolCall.from_mapping(json.loads(raw)["record"])

    def reconcile_tool_call(
        self,
        conversation_id: str,
        *,
        owner_id: str,
        call_id: str,
        status: str,
        note: str,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConversationToolCall:
        for value, name in (
            (conversation_id, "conversation_id"),
            (owner_id, "owner_id"),
            (call_id, "call_id"),
            (status, "status"),
            (note, "note"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_reconcile_conversation_tool_call(
            conversation_id,
            owner_id,
            call_id,
            status,
            note,
            int(expected_revision),
            event_time,
        )
        return ConversationToolCall.from_mapping(json.loads(raw)["record"])

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be non-empty")


__all__ = [
    "ConversationCheckpoint",
    "ConversationExecution",
    "ConversationManager",
    "ConversationPart",
    "ConversationRecord",
    "ConversationSnapshot",
    "ConversationToolCall",
    "ConversationTurn",
]
