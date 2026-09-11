"""Learning-loop bridge isolated from the run/provider gateway."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .native import native_module


@dataclass(frozen=True)
class LearningStats:
    """Snapshot of the AEGIS learning loop state."""

    schema: str
    ledger_events: int
    improved_skills: int
    nudged_memories: int
    indexed_sessions: int
    user_models: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> LearningStats:
        return cls(
            schema=str(data["schema"]),
            ledger_events=int(data["ledger_events"]),
            improved_skills=int(data["improved_skills"]),
            nudged_memories=int(data["nudged_memories"]),
            indexed_sessions=int(data["indexed_sessions"]),
            user_models=int(data["user_models"]),
        )


@dataclass(frozen=True)
class SessionSearchCandidate:
    """A single cross-session recall candidate (CandidateOnlyGate enforced)."""

    evidence_ref_hash: str
    segment_id: int
    tier: str
    score: float
    epoch_hash: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SessionSearchCandidate:
        return cls(
            evidence_ref_hash=str(data.get("evidence_ref_hash", "")),
            segment_id=int(data.get("segment_id", 0)),
            tier=str(data.get("tier", "ColdVectorExpansion")),
            score=float(data.get("score", 0.0)),
            epoch_hash=str(data.get("epoch_hash", "")),
        )


@dataclass(frozen=True)
class SessionSearchResult:
    """Container for cross-session search results."""

    schema: str
    query: str
    top_k: int
    count: int
    results: tuple[SessionSearchCandidate, ...]
    tier: str
    gate: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SessionSearchResult:
        raw_results = data.get("results", [])
        candidates = tuple(SessionSearchCandidate.from_mapping(item) for item in raw_results if isinstance(item, dict))
        return cls(
            schema=str(data["schema"]),
            query=str(data.get("query", "")),
            top_k=int(data.get("top_k", 0)),
            count=len(candidates),
            results=candidates,
            tier=str(data.get("tier", "ColdVectorExpansion")),
            gate=str(data.get("gate", "CandidateOnly")),
        )


@dataclass(frozen=True)
class SessionSourceRecord:
    """Authorized immutable source payload used by context hydration."""

    session_id: int
    content_hash: str
    timestamp: int
    scope_kind: str
    owner_id: str
    content: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SessionSourceRecord:
        session_id_raw = str(data["session_id"])
        session_id = int(session_id_raw, 16) if session_id_raw.startswith("0x") else int(session_id_raw)
        return cls(
            session_id=session_id,
            content_hash=str(data["content_hash"]),
            timestamp=int(data["timestamp"]),
            scope_kind=str(data["scope_kind"]),
            owner_id=str(data["owner_id"]),
            content=str(data["content"]),
        )


@dataclass(frozen=True)
class MemoryRecord:
    """Durable memory metadata and source content returned by explicit inspect."""

    memory_id: str
    owner_id: str
    scope_kind: str
    lifecycle: str
    validation: str
    revision: int
    observed_at_ms: int
    content_hash: str | None
    content: str | None
    validation_basis: str | None = None
    validation_reason: str | None = None
    memory_kind: str = "SEMANTIC_FACT"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> MemoryRecord:
        return cls(
            memory_id=str(data["memory_id"]),
            owner_id=str(data["owner_id"]),
            scope_kind=str(data["scope_kind"]),
            lifecycle=str(data["lifecycle"]),
            validation=str(data["validation"]),
            revision=int(data["revision"]),
            observed_at_ms=int(data["observed_at_ms"]),
            content_hash=(str(data["content_hash"]) if data.get("content_hash") else None),
            content=(str(data["content"]) if data.get("content") is not None else None),
            validation_basis=(str(data["validation_basis"]) if data.get("validation_basis") else None),
            validation_reason=(str(data["validation_reason"]) if data.get("validation_reason") else None),
            memory_kind=str(data.get("memory_kind", "SEMANTIC_FACT")),
        )


class LearningManager:
    """Rust-backed learning bridge; all search results remain candidates."""

    def __init__(self, rust_bridge: Any = None) -> None:
        self._bridge = rust_bridge

    @property
    def _native(self) -> Any:
        if self._bridge is None:
            self._bridge = native_module()
        return self._bridge

    def get_stats(self, *, ledger_json: str = "{}") -> LearningStats:
        raw = self._native.aegis_get_learning_stats(ledger_json)
        return LearningStats.from_mapping(json.loads(raw))

    def search_past(self, query: str, top_k: int = 5) -> SessionSearchResult:
        if not query or not query.strip():
            raise ValueError("search_past requires a non-empty query")
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100")
        raw = self._native.aegis_search_past_sessions(query, top_k)
        return SessionSearchResult.from_mapping(json.loads(raw))

    def index_session(self, session_id: int | str, content: str, timestamp: int | None = None) -> str:
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        if isinstance(session_id, str):
            sid = int(session_id, 16) if session_id.startswith("0x") else int(session_id)
        else:
            sid = int(session_id)
        event_time = int(time.time() * 1000) if timestamp is None else timestamp
        return self._native.aegis_index_session(sid, content, event_time)

    def index_session_scoped(
        self,
        session_id: int | str,
        content: str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str = "local-profile",
        timestamp: int | None = None,
    ) -> str:
        """Persist a transcript under an explicit local scope owner."""
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        if not scope_kind or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        if not owner_id or not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        if isinstance(session_id, str):
            sid = int(session_id, 16) if session_id.startswith("0x") else int(session_id)
        else:
            sid = int(session_id)
        event_time = int(time.time() * 1000) if timestamp is None else timestamp
        return self._native.aegis_index_session_scoped(
            sid,
            content,
            event_time,
            scope_kind,
            owner_id,
        )

    def read_session_content(self, session_id: int | str, *, owner_id: str | None = None) -> str | None:
        """Hydrate authoritative source text after candidate search."""
        if isinstance(session_id, str):
            sid = int(session_id, 16) if session_id.startswith("0x") else int(session_id)
        else:
            sid = int(session_id)
        if owner_id is not None and not owner_id.strip():
            raise ValueError("owner_id must be non-empty when provided")
        return self._native.aegis_read_session_content(sid, owner_id)

    def read_session_content_scoped(
        self,
        session_id: int | str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
    ) -> str | None:
        """Hydrate source text only inside the requested owner/scope."""
        if not scope_kind or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        if owner_id is not None and not owner_id.strip():
            raise ValueError("owner_id must be non-empty when provided")
        if isinstance(session_id, str):
            sid = int(session_id, 16) if session_id.startswith("0x") else int(session_id)
        else:
            sid = int(session_id)
        return self._native.aegis_read_session_content_scoped(sid, scope_kind, owner_id)

    def read_session_record_scoped(
        self,
        session_id: int | str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
    ) -> SessionSourceRecord | None:
        """Hydrate one source with its Rust-verified revision metadata."""
        if not scope_kind or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        if owner_id is not None and not owner_id.strip():
            raise ValueError("owner_id must be non-empty when provided")
        if isinstance(session_id, str):
            sid = int(session_id, 16) if session_id.startswith("0x") else int(session_id)
        else:
            sid = int(session_id)
        raw = self._native.aegis_read_session_record_scoped(sid, scope_kind, owner_id)
        if raw is None:
            return None
        return SessionSourceRecord.from_mapping(json.loads(raw))

    def select_context_items(
        self,
        items: list[dict[str, Any]],
        token_budget: int,
        *,
        mandatory_session_ids: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        """Delegate deterministic hydrated-item selection to Rust ContextGovernor."""
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        payload = []
        for item in items:
            session_id = int(item["session_id"])
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
            payload.append(
                {
                    "node_id": f"0x{session_id:x}",
                    "token_cost": int(item["token_cost"]),
                    "utility_score": int(score * 1_000_000),
                    "dependency_coverage": int(item.get("dependency_coverage", 0)),
                    "contradiction_risk": int(item.get("contradiction_risk", 0)),
                }
            )
        raw = self._native.aegis_select_context_items(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            int(token_budget),
            json.dumps([f"0x{int(session_id):x}" for session_id in mandatory_session_ids]),
        )
        return json.loads(raw)

    def capture_memory(
        self,
        memory_id: int | str,
        content: str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        expected_revision: int | None = None,
        timestamp: int | None = None,
        request_id: int | str | None = None,
        memory_kind: str | None = None,
    ) -> dict[str, Any]:
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        mid = int(memory_id, 16) if isinstance(memory_id, str) and memory_id.startswith("0x") else int(memory_id)
        rid_value = uuid.uuid4().int if request_id is None else request_id
        rid = int(rid_value, 16) if isinstance(rid_value, str) and rid_value.startswith("0x") else int(rid_value)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        if memory_kind is not None and (
            not isinstance(memory_kind, str) or not memory_kind.strip() or len(memory_kind) > 64
        ):
            raise ValueError("memory_kind must be non-empty and bounded when provided")
        args = (mid, content, event_time, rid, scope_kind, owner_id)
        if memory_kind is None:
            raw = self._native.aegis_capture_memory(*args, subject_id) if subject_id is not None else self._native.aegis_capture_memory(*args)
        else:
            raw = self._native.aegis_capture_memory(
                *args,
                subject_id,
                memory_kind,
            )
        return json.loads(raw)

    def inspect_memory(
        self,
        memory_id: int | str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> MemoryRecord | None:
        mid = int(memory_id, 16) if isinstance(memory_id, str) and memory_id.startswith("0x") else int(memory_id)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        args = (mid, scope_kind, owner_id)
        raw = json.loads(self._native.aegis_inspect_memory(*args, subject_id) if subject_id is not None else self._native.aegis_inspect_memory(*args))
        record = raw.get("record")
        return MemoryRecord.from_mapping(record) if isinstance(record, dict) else None

    def search_memories(
        self,
        query: str,
        top_k: int = 5,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100")
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        args = (query, top_k, scope_kind, owner_id)
        raw = json.loads(self._native.aegis_search_memories(*args, subject_id) if subject_id is not None else self._native.aegis_search_memories(*args))
        return tuple(
            MemoryRecord.from_mapping(item)
            for item in raw.get("results", [])
            if isinstance(item, dict)
        )

    def forget_memory(
        self,
        memory_id: int | str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        expected_revision: int | None = None,
        timestamp: int | None = None,
        request_id: int | str | None = None,
    ) -> dict[str, Any]:
        return self._memory_transition(
            "aegis_forget_memory", memory_id, scope_kind, owner_id, subject_id, expected_revision, timestamp, request_id
        )

    def validate_memory(
        self,
        memory_id: int | str,
        validation: str,
        *,
        basis: str | None = None,
        reason: str | None = None,
        expected_revision: int,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        timestamp: int | None = None,
        request_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Commit an explicit validation decision without invoking a model."""
        decision = str(validation).strip().upper()
        if decision not in {"ACCEPTED", "REJECTED", "QUARANTINED"}:
            raise ValueError("validation must be ACCEPTED, REJECTED, or QUARANTINED")
        if basis is not None and not basis.strip():
            basis = None
        if reason is not None and not reason.strip():
            reason = None
        if decision == "ACCEPTED" and (basis is None or reason is not None):
            raise ValueError("accepted validation requires basis and no reason")
        if decision in {"REJECTED", "QUARANTINED"} and (reason is None or basis is not None):
            raise ValueError("rejected or quarantined validation requires reason and no basis")
        mid = int(memory_id, 16) if isinstance(memory_id, str) and memory_id.startswith("0x") else int(memory_id)
        rid_value = uuid.uuid4().int if request_id is None else request_id
        rid = int(rid_value, 16) if isinstance(rid_value, str) and rid_value.startswith("0x") else int(rid_value)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        args = (mid, decision, basis, reason, int(expected_revision), rid, event_time, scope_kind, owner_id)
        raw = self._native.aegis_validate_memory(*args, subject_id) if subject_id is not None else self._native.aegis_validate_memory(*args)
        return json.loads(raw)

    def correct_memory(
        self,
        memory_id: int | str,
        content: str,
        *,
        expected_revision: int,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        timestamp: int | None = None,
        request_id: int | str | None = None,
    ) -> dict[str, Any]:
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        mid = int(memory_id, 16) if isinstance(memory_id, str) and memory_id.startswith("0x") else int(memory_id)
        rid_value = uuid.uuid4().int if request_id is None else request_id
        rid = int(rid_value, 16) if isinstance(rid_value, str) and rid_value.startswith("0x") else int(rid_value)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        args = (mid, content, int(expected_revision), rid, event_time, scope_kind, owner_id)
        raw = self._native.aegis_correct_memory(*args, subject_id) if subject_id is not None else self._native.aegis_correct_memory(*args)
        return json.loads(raw)

    def restore_memory(
        self,
        memory_id: int | str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        expected_revision: int | None = None,
        timestamp: int | None = None,
        request_id: int | str | None = None,
    ) -> dict[str, Any]:
        return self._memory_transition(
            "aegis_restore_memory", memory_id, scope_kind, owner_id, subject_id, expected_revision, timestamp, request_id
        )

    def purge_memory(
        self,
        memory_id: int | str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        expected_revision: int | None = None,
        timestamp: int | None = None,
        request_id: int | str | None = None,
    ) -> dict[str, Any]:
        return self._memory_transition(
            "aegis_purge_memory", memory_id, scope_kind, owner_id, subject_id, expected_revision, timestamp, request_id
        )

    def grant_memory_access(
        self,
        subject_id: str,
        *,
        owner_id: str,
        scope_kind: str = "USER_PRIVATE",
        can_read: bool = True,
        can_write: bool = False,
        expires_at_ms: int | None = None,
        expected_revision: int | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        if not subject_id or not subject_id.strip():
            raise ValueError("subject_id must be non-empty")
        if not owner_id or not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        if not scope_kind or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_grant_memory_access(
            subject_id,
            owner_id,
            scope_kind,
            bool(can_read),
            bool(can_write),
            expires_at_ms,
            expected_revision,
            event_time,
        )
        return json.loads(raw)

    def revoke_memory_access(
        self,
        subject_id: str,
        *,
        owner_id: str,
        scope_kind: str = "USER_PRIVATE",
        expected_revision: int,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        if not subject_id or not subject_id.strip():
            raise ValueError("subject_id must be non-empty")
        if not owner_id or not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        if not scope_kind or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_revoke_memory_access(
            subject_id,
            owner_id,
            scope_kind,
            int(expected_revision),
            event_time,
        )
        return json.loads(raw)

    def backup_memory_store(self, destination: str) -> dict[str, Any]:
        if not destination or not destination.strip():
            raise ValueError("destination must be non-empty")
        return json.loads(self._native.aegis_backup_memory_store(destination))

    def _memory_transition(
        self,
        function_name: str,
        memory_id: int | str,
        scope_kind: str,
        owner_id: str | None,
        subject_id: str | None,
        expected_revision: int | None,
        timestamp: int | None,
        request_id: int | str | None,
    ) -> dict[str, Any]:
        mid = int(memory_id, 16) if isinstance(memory_id, str) and memory_id.startswith("0x") else int(memory_id)
        rid_value = uuid.uuid4().int if request_id is None else request_id
        rid = int(rid_value, 16) if isinstance(rid_value, str) and rid_value.startswith("0x") else int(rid_value)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        args = (mid, rid, event_time, scope_kind, owner_id)
        if subject_id is not None:
            args += (subject_id,)
        if expected_revision is not None:
            if subject_id is None:
                args += (None,)
            args += (int(expected_revision),)
        raw = getattr(self._native, function_name)(*args)
        return json.loads(raw)

    def sync_memory(self, session_id: str = "0x1") -> dict[str, Any]:
        raw = self._native.aegis_trigger_memory_nudge(session_id)
        return json.loads(raw)


__all__ = [
    "LearningManager",
    "LearningStats",
    "MemoryRecord",
    "SessionSearchCandidate",
    "SessionSearchResult",
    "SessionSourceRecord",
]
