"""Learning-loop bridge isolated from the run/provider gateway."""

from __future__ import annotations

import json
import hashlib
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from .native import native_module

MAX_CONTEXT_ITEMS = 100
MAX_CONTEXT_SELECTOR_JSON_BYTES = 2 * 1024 * 1024
MAX_FILE_RELATION_CONTEXT_RESULTS = 8
DEFAULT_FILE_RELATION_PAGE_SIZE = 24
MAX_FILE_RELATION_PAGE_SIZE = 100


def _memory_id_value(value: int | str, name: str) -> int:
    if type(value) is int:
        parsed = value
    elif type(value) is str:
        if value.startswith("0x"):
            digits = value[2:]
            if not digits or any(character not in "0123456789abcdefABCDEF" for character in digits):
                raise ValueError(f"{name} must be a positive decimal or hexadecimal integer")
            parsed = int(digits, 16)
        elif value and all("0" <= character <= "9" for character in value):
            parsed = int(value, 10)
        else:
            raise ValueError(f"{name} must be a positive decimal or hexadecimal integer")
    else:
        raise ValueError(f"{name} must be a positive decimal or hexadecimal integer")
    if type(parsed) is not int or not 0 < parsed < 1 << 128:
        raise ValueError(f"{name} must fit a non-zero unsigned 128-bit integer")
    return parsed


def _validate_project_path(project_id: str, relative_path: str) -> None:
    if not isinstance(project_id, str) or not project_id.startswith("project:") or len(project_id) > 128:
        raise ValueError("project_id must be a bounded project identity")
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path.encode("utf-8")) > 8192
        or "\\" in relative_path
        or relative_path.startswith("/")
        or "\x00" in relative_path
    ):
        raise ValueError("relative_path must be a bounded normalized project-relative path")
    parts = relative_path.split("/")
    if any(part in {"", ".", "..", ".git", ".aegis"} for part in parts):
        raise ValueError("relative_path must be a normalized project-relative path")


def _validate_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a 64-character hexadecimal digest")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a 64-character hexadecimal digest") from error
    if len(decoded) != 32:
        raise ValueError(f"{name} must be a 64-character hexadecimal digest")


def _validate_file_relation_source(
    project_id: str,
    relative_path: str,
    source_revision: str,
    source_content_hash: str,
) -> None:
    _validate_project_path(project_id, relative_path)
    if (
        not isinstance(source_revision, str)
        or not source_revision
        or len(source_revision) > 256
        or "\x00" in source_revision
    ):
        raise ValueError("source_revision must be a bounded non-empty value")
    _validate_hash(source_content_hash, "source content hash")


def _require_relation_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if type(value) is not bool:
        raise ValueError(f"native relation field {key} must be a boolean")
    return value


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
    source_session_id: str | None = None
    nudge_id: str | None = None
    nudge_hash: str | None = None
    candidate_hash: str | None = None
    relevance_score: float | None = None

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
            source_session_id=(str(data["source_session_id"]) if data.get("source_session_id") else None),
            nudge_id=(str(data["nudge_id"]) if data.get("nudge_id") else None),
            nudge_hash=(str(data["nudge_hash"]) if data.get("nudge_hash") else None),
            candidate_hash=(str(data["candidate_hash"]) if data.get("candidate_hash") else None),
            relevance_score=(float(data["relevance_score"]) if data.get("relevance_score") is not None else None),
        )


@dataclass(frozen=True)
class MemoryFileRelation:
    """User-confirmed link between one memory and one source file snapshot."""

    relation_id: str
    memory_id: str
    scope_kind: str
    project_id: str
    relative_path: str
    source_revision: str
    source_content_hash: str
    memory_revision: int
    memory_content_hash: str
    created_at_ms: int
    memory_lifecycle: str
    memory_validation: str
    memory_content_current: bool

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> MemoryFileRelation:
        return cls(
            relation_id=str(data["relation_id"]),
            memory_id=str(data["memory_id"]),
            scope_kind=str(data["scope_kind"]),
            project_id=str(data["project_id"]),
            relative_path=str(data["relative_path"]),
            source_revision=str(data["source_revision"]),
            source_content_hash=str(data["source_content_hash"]),
            memory_revision=int(data["memory_revision"]),
            memory_content_hash=str(data["memory_content_hash"]),
            created_at_ms=int(data["created_at_ms"]),
            memory_lifecycle=str(data["memory_lifecycle"]),
            memory_validation=str(data["memory_validation"]),
            memory_content_current=_require_relation_bool(data, "memory_content_current"),
        )


@dataclass(frozen=True)
class MemoryFileRelationCursor:
    created_at_ms: int
    relation_id: str

    def __post_init__(self) -> None:
        if type(self.created_at_ms) is not int or not 1 <= self.created_at_ms <= 0x7FFFFFFFFFFFFFFF:
            raise ValueError("created_at_ms must be a positive SQLite integer")
        relation_id = _memory_id_value(self.relation_id, "relation_id")
        if relation_id == 0:
            raise ValueError("relation_id must be positive")
        object.__setattr__(self, "relation_id", f"0x{relation_id:x}")


@dataclass(frozen=True)
class MemoryFileRelationPage:
    relations: tuple[MemoryFileRelation, ...]
    next_cursor: MemoryFileRelationCursor | None


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

    def search_past_scoped(
        self,
        query: str,
        top_k: int = 5,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
    ) -> SessionSearchResult:
        """Search candidate references inside one explicit owner scope."""
        if not query or not query.strip():
            raise ValueError("search_past_scoped requires a non-empty query")
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100")
        if not scope_kind or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        if owner_id is not None and not owner_id.strip():
            raise ValueError("owner_id must be non-empty when provided")
        raw = self._native.aegis_search_past_sessions_scoped(query, top_k, scope_kind, owner_id)
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
        if not items or len(items) > MAX_CONTEXT_ITEMS:
            raise ValueError("context items must contain between 1 and 100 entries")
        payload = []
        for item in items:
            # ``session_id`` is the legacy field.  New context producers may
            # provide a namespace-neutral node_id (for example an ACTIVE
            # semantic memory) while keeping session_id for compatibility
            # with older selectors.
            raw_node_id = item.get("node_id")
            if raw_node_id is None:
                raw_node_id = item["session_id"]
            node_id = int(raw_node_id)
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
            payload.append(
                {
                    "node_id": f"0x{node_id:x}",
                    "token_cost": int(item["token_cost"]),
                    "utility_score": int(score * 1_000_000),
                    "dependency_coverage": int(item.get("dependency_coverage", 0)),
                    "contradiction_risk": int(item.get("contradiction_risk", 0)),
                }
            )
            retention_class = item.get("retention_class")
            if retention_class is not None:
                normalized_retention = str(retention_class).strip().lower()
                if normalized_retention not in {"protected", "condensable", "ephemeral"}:
                    raise ValueError("retention_class must be protected, condensable, or ephemeral")
                if normalized_retention != "condensable":
                    payload[-1]["retention_class"] = normalized_retention
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        mandatory_ids_json = json.dumps([f"0x{int(session_id):x}" for session_id in mandatory_session_ids])
        if (
            len(payload_json.encode("utf-8")) > MAX_CONTEXT_SELECTOR_JSON_BYTES
            or len(mandatory_ids_json.encode("utf-8")) > MAX_CONTEXT_SELECTOR_JSON_BYTES
        ):
            raise ValueError("context selector input exceeds the bounded size")
        raw = self._native.aegis_select_context_items(
            payload_json,
            int(token_budget),
            mandatory_ids_json,
        )
        return json.loads(raw)

    def select_context_items_v2(
        self,
        items: list[dict[str, Any]],
        relation_graph: dict[str, Any],
        token_budget: int,
        *,
        mandatory_session_ids: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        """Select hydrated items with a bounded, typed verified-relation graph."""
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        if not items or len(items) > MAX_CONTEXT_ITEMS:
            raise ValueError("context items must contain between 1 and 100 entries")
        if not isinstance(relation_graph, dict):
            raise ValueError("relation_graph must be an object")

        payload = []
        for item in items:
            raw_node_id = item.get("node_id")
            if raw_node_id is None:
                raw_node_id = item["session_id"]
            node_id = int(raw_node_id)
            kind = item.get("kind")
            if kind not in {"session", "memory"}:
                raise ValueError("context item kind must be session or memory")
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
            payload_item = {
                "node_id": f"0x{node_id:x}",
                "kind": kind,
                "token_cost": int(item["token_cost"]),
                "utility_score": int(score * 1_000_000),
                "contradiction_risk": int(item.get("contradiction_risk", 0)),
            }
            retention_class = item.get("retention_class")
            if retention_class is not None:
                normalized_retention = str(retention_class).strip().lower()
                if normalized_retention not in {"protected", "condensable", "ephemeral"}:
                    raise ValueError("retention_class must be protected, condensable, or ephemeral")
                if normalized_retention != "condensable":
                    payload_item["retention_class"] = normalized_retention
            payload.append(payload_item)

        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        relation_graph_json = json.dumps(relation_graph, sort_keys=True, separators=(",", ":"))
        mandatory_ids_json = json.dumps([f"0x{int(session_id):x}" for session_id in mandatory_session_ids])
        if any(
            len(value.encode("utf-8")) > MAX_CONTEXT_SELECTOR_JSON_BYTES
            for value in (payload_json, relation_graph_json, mandatory_ids_json)
        ):
            raise ValueError("context selector input exceeds the bounded size")

        native_selector = getattr(self._native, "aegis_select_context_items_v2", None)
        if not callable(native_selector):
            # Older native extensions remain usable, but their response stays
            # explicitly v1 and therefore cannot claim typed relation support.
            return self.select_context_items(
                items,
                token_budget,
                mandatory_session_ids=mandatory_session_ids,
            )
        raw = native_selector(
            payload_json,
            relation_graph_json,
            int(token_budget),
            mandatory_ids_json,
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
            raw = (
                self._native.aegis_capture_memory(*args, subject_id)
                if subject_id is not None
                else self._native.aegis_capture_memory(*args)
            )
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
        raw = json.loads(
            self._native.aegis_inspect_memory(*args, subject_id)
            if subject_id is not None
            else self._native.aegis_inspect_memory(*args)
        )
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
        raw = json.loads(
            self._native.aegis_search_memories(*args, subject_id)
            if subject_id is not None
            else self._native.aegis_search_memories(*args)
        )
        return tuple(MemoryRecord.from_mapping(item) for item in raw.get("results", []) if isinstance(item, dict))

    def select_adaptive_user_memories(
        self,
        query: str = "user preferences workflow",
        top_k: int = 5,
        *,
        limit: int | None = None,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Retrieve and rank active, validated user-adaptation memories for personalization.

        Strictly excludes non-ACTIVE records and REJECTED/QUARANTINED records,
        hydrates CandidateOnly search results via inspect_memory, and prioritizes
        ACCEPTED and USER_PREFERENCE/USER_WORKFLOW/PROCEDURAL_SKILL/USER_ASSERTION
        memories ahead of unreviewed general facts.
        """
        effective_limit = top_k if limit is None else limit
        if effective_limit < 1 or effective_limit > 100:
            raise ValueError("top_k must be between 1 and 100")
        candidate_pool_size = min(100, max(effective_limit * 3, 15))
        records_by_id: dict[str, MemoryRecord] = {}
        if hasattr(self._native, "aegis_search_memories"):
            for rec in self.search_memories(
                query,
                top_k=candidate_pool_size,
                scope_kind=scope_kind,
                owner_id=owner_id,
                subject_id=subject_id,
            ):
                records_by_id.setdefault(rec.memory_id, rec)
            tokens = [tok for tok in query.split() if tok]
            if len(records_by_id) < effective_limit and len(tokens) > 1:
                for token in dict.fromkeys(tokens[:4]):
                    for rec in self.search_memories(
                        token,
                        top_k=candidate_pool_size,
                        scope_kind=scope_kind,
                        owner_id=owner_id,
                        subject_id=subject_id,
                    ):
                        records_by_id.setdefault(rec.memory_id, rec)
        if not records_by_id and hasattr(self._native, "aegis_list_memories"):
            raw = json.loads(
                self._native.aegis_list_memories(scope_kind, owner_id, subject_id, "ACTIVE")
            )
            items = raw.get("memories", raw.get("results", []))
            for item in items:
                if isinstance(item, dict):
                    rec = MemoryRecord.from_mapping(
                        {
                            **item,
                            "memory_kind": item.get(
                                "memory_kind", item.get("category", "SEMANTIC_FACT")
                            ),
                        }
                    )
                    records_by_id.setdefault(rec.memory_id, rec)
        priority_kinds = {
            "USER_PREFERENCE": 3,
            "USER_WORKFLOW": 2,
            "PROCEDURAL_SKILL": 2,
            "USER_ASSERTION": 2,
            "SEMANTIC_FACT": 1,
        }
        eligible: list[MemoryRecord] = [
            rec
            for rec in records_by_id.values()
            if rec.lifecycle == "ACTIVE"
            and rec.validation in {"ACCEPTED", "UNREVIEWED"}
        ]
        eligible.sort(
            key=lambda rec: (
                -(2 if rec.validation == "ACCEPTED" else 1),
                -priority_kinds.get(rec.memory_kind, 0),
                -(rec.relevance_score if rec.relevance_score is not None else 0.0),
                -rec.observed_at_ms,
                rec.memory_id,
            )
        )
        can_inspect = hasattr(self._native, "aegis_inspect_memory")
        selected: list[MemoryRecord] = []
        for rec in eligible:
            if rec.content:
                selected.append(rec)
            elif rec.content is None and can_inspect:
                inspected = self.inspect_memory(
                    rec.memory_id,
                    scope_kind=scope_kind,
                    owner_id=owner_id,
                    subject_id=subject_id,
                )
                if (
                    inspected is not None
                    and inspected.lifecycle == "ACTIVE"
                    and inspected.validation in {"ACCEPTED", "UNREVIEWED"}
                    and inspected.content
                ):
                    if inspected.relevance_score is None and rec.relevance_score is not None:
                        inspected = replace(inspected, relevance_score=rec.relevance_score)
                    selected.append(inspected)
            if len(selected) >= effective_limit:
                break
        return tuple(selected)

    def link_file_relation(
        self,
        relation_id: int | str,
        memory_id: int | str,
        *,
        project_id: str,
        relative_path: str,
        source_revision: str,
        source_content_hash: str,
        memory_revision: int,
        memory_content_hash: str,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> MemoryFileRelation:
        relation_value = _memory_id_value(relation_id, "relation_id")
        memory_value = _memory_id_value(memory_id, "memory_id")
        _validate_file_relation_source(project_id, relative_path, source_revision, source_content_hash)
        _validate_hash(memory_content_hash, "memory content hash")
        if type(memory_revision) is not int or not 1 <= memory_revision < 1 << 64:
            raise ValueError("memory_revision must be a positive integer")
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        raw = self._native.aegis_link_memory_file_relation(
            relation_value,
            memory_value,
            project_id,
            relative_path,
            source_revision,
            source_content_hash.lower(),
            memory_revision,
            memory_content_hash.lower(),
            int(time.time_ns() // 1_000_000),
            scope_kind,
            owner_id,
            subject_id,
        )
        value = json.loads(raw).get("relation")
        if not isinstance(value, dict):
            raise ValueError("native relation link returned no relation")
        return MemoryFileRelation.from_mapping(value)

    def list_file_relations(
        self,
        project_id: str,
        relative_path: str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryFileRelation, ...]:
        _validate_project_path(project_id, relative_path)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        if limit is not None and (type(limit) is not int or not 0 <= limit <= 0xFFFFFFFF):
            raise ValueError("limit must be an integer between 0 and 4294967295")
        raw = json.loads(
            self._native.aegis_list_memory_file_relations(
                project_id,
                relative_path,
                scope_kind,
                owner_id,
                subject_id,
            )
        )
        relations = raw.get("relations", [])
        if not isinstance(relations, list):
            raise ValueError("native relation list returned an invalid value")
        if limit is not None:
            relations = relations[:limit]
        return tuple(MemoryFileRelation.from_mapping(item) for item in relations if isinstance(item, dict))

    def list_file_relations_page(
        self,
        project_id: str,
        relative_path: str,
        *,
        limit: int = DEFAULT_FILE_RELATION_PAGE_SIZE,
        cursor: MemoryFileRelationCursor | None = None,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> MemoryFileRelationPage:
        _validate_project_path(project_id, relative_path)
        if type(limit) is not int or not 1 <= limit <= MAX_FILE_RELATION_PAGE_SIZE:
            raise ValueError(f"limit must be an integer between 1 and {MAX_FILE_RELATION_PAGE_SIZE}")
        if cursor is not None and not isinstance(cursor, MemoryFileRelationCursor):
            raise ValueError("cursor must be a validated MemoryFileRelationCursor")
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        raw = json.loads(
            self._native.aegis_list_memory_file_relations_page(
                project_id,
                relative_path,
                limit,
                cursor.created_at_ms if cursor is not None else None,
                _memory_id_value(cursor.relation_id, "relation_id") if cursor is not None else None,
                scope_kind,
                owner_id,
                subject_id,
            )
        )
        if not isinstance(raw, dict) or raw.get("schema") != "aegis-memory-file-relation-page-v1":
            raise ValueError("native relation page returned an invalid schema")
        raw_relations = raw.get("relations")
        if (
            not isinstance(raw_relations, list)
            or len(raw_relations) > limit
            or not all(isinstance(item, dict) for item in raw_relations)
        ):
            raise ValueError("native relation page exceeded its limit or returned an invalid value")
        relations = tuple(MemoryFileRelation.from_mapping(item) for item in raw_relations)
        raw_cursor = raw.get("next_cursor")
        if raw_cursor is None:
            next_cursor = None
        elif isinstance(raw_cursor, dict) and set(raw_cursor) == {"created_at_ms", "relation_id"}:
            next_cursor = MemoryFileRelationCursor(
                created_at_ms=raw_cursor["created_at_ms"],
                relation_id=raw_cursor["relation_id"],
            )
            if not relations or next_cursor != MemoryFileRelationCursor(
                created_at_ms=relations[-1].created_at_ms,
                relation_id=relations[-1].relation_id,
            ):
                raise ValueError("native relation page returned an invalid continuation cursor")
            if cursor is not None and (
                next_cursor.created_at_ms,
                _memory_id_value(next_cursor.relation_id, "relation_id"),
            ) <= (cursor.created_at_ms, _memory_id_value(cursor.relation_id, "relation_id")):
                raise ValueError("native relation page cursor did not advance")
        else:
            raise ValueError("native relation page returned an invalid continuation cursor")
        return MemoryFileRelationPage(relations=relations, next_cursor=next_cursor)

    def list_current_file_relations(
        self,
        project_id: str,
        relative_path: str,
        *,
        source_content_hash: str,
        limit: int,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> tuple[MemoryFileRelation, ...]:
        _validate_project_path(project_id, relative_path)
        _validate_hash(source_content_hash, "source content hash")
        if type(limit) is not int or not 0 <= limit <= MAX_FILE_RELATION_CONTEXT_RESULTS:
            raise ValueError(f"limit must be an integer between 0 and {MAX_FILE_RELATION_CONTEXT_RESULTS}")
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        raw = json.loads(
            self._native.aegis_list_current_memory_file_relations(
                project_id,
                relative_path,
                source_content_hash,
                limit,
                scope_kind,
                owner_id,
                subject_id,
            )
        )
        relations = raw.get("relations", [])
        if not isinstance(relations, list):
            raise ValueError("native current relation list returned an invalid value")
        if len(relations) > limit:
            raise ValueError("native current relation list exceeded its limit")
        return tuple(MemoryFileRelation.from_mapping(item) for item in relations if isinstance(item, dict))

    def unlink_file_relation(
        self,
        relation_id: int | str,
        *,
        project_id: str,
        relative_path: str,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        subject_id: str | None = None,
    ) -> bool:
        relation_value = _memory_id_value(relation_id, "relation_id")
        _validate_project_path(project_id, relative_path)
        if subject_id is not None and not subject_id.strip():
            raise ValueError("subject_id must be non-empty when provided")
        raw = json.loads(
            self._native.aegis_unlink_memory_file_relation(
                relation_value,
                project_id,
                relative_path,
                int(time.time_ns() // 1_000_000),
                scope_kind,
                owner_id,
                subject_id,
            )
        )
        return raw.get("removed") is True

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
        raw = (
            self._native.aegis_validate_memory(*args, subject_id)
            if subject_id is not None
            else self._native.aegis_validate_memory(*args)
        )
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
        raw = (
            self._native.aegis_correct_memory(*args, subject_id)
            if subject_id is not None
            else self._native.aegis_correct_memory(*args)
        )
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
            "aegis_restore_memory",
            memory_id,
            scope_kind,
            owner_id,
            subject_id,
            expected_revision,
            timestamp,
            request_id,
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

    def sync_memory(
        self,
        session_id: str = "0x1",
        *,
        candidates: list[dict[str, Any]] | None = None,
        nudge_id: int | str | None = None,
        relevance_threshold: float = 0.7,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Stage agent-proposed facts without activating them.

        The native boundary seals and persists only candidates. A later
        explicit validation is required before a candidate can enter active
        memory or model context.
        """
        if candidates is None:
            # Preserve the compatibility acknowledgement for callers that do
            # not yet have an extractor or candidate payload.
            raw = self._native.aegis_trigger_memory_nudge(session_id)
            return json.loads(raw)
        if type(candidates) is not list or not candidates:
            raise ValueError("candidates must be a non-empty list")
        if not isinstance(scope_kind, str) or not scope_kind.strip():
            raise ValueError("scope_kind must be non-empty")
        normalized: list[dict[str, Any]] = []
        for candidate in candidates:
            if type(candidate) is not dict:
                raise ValueError("each memory candidate must be a mapping")
            if set(candidate) - {"content", "relevance_score", "source_session_id"}:
                raise ValueError("memory candidate contains an unsupported field")
            content = candidate.get("content")
            score = candidate.get("relevance_score")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("memory candidate content must be non-empty")
            if type(score) not in (int, float) or not 0.0 <= float(score) <= 1.0:
                raise ValueError("memory candidate relevance_score must be between 0 and 1")
            normalized_candidate: dict[str, Any] = {
                "content": content,
                "relevance_score": float(score),
            }
            if "source_session_id" in candidate:
                normalized_candidate["source_session_id"] = candidate["source_session_id"]
            normalized.append(normalized_candidate)
        if nudge_id is None:
            # Retries of the same bounded proposal should converge on the
            # native idempotency key instead of creating duplicate candidates.
            identity = json.dumps(
                {
                    "session_id": session_id,
                    "scope_kind": scope_kind,
                    "owner_id": owner_id or "native-profile",
                    "candidates": normalized,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest = hashlib.sha256(b"aegis-memory-nudge-v1\0" + identity).digest()
            nudge_value = int.from_bytes(digest[:16], "big") or 1
        else:
            nudge_value = nudge_id
        if isinstance(nudge_value, str):
            nudge_value = int(nudge_value, 16) if nudge_value.startswith("0x") else int(nudge_value)
        if type(nudge_value) is not int or nudge_value <= 0:
            raise ValueError("nudge_id must be a positive integer")
        if type(relevance_threshold) not in (int, float) or not 0.0 <= float(relevance_threshold) <= 1.0:
            raise ValueError("relevance_threshold must be between 0 and 1")
        raw = self._native.aegis_trigger_memory_nudge(
            session_id,
            int(nudge_value),
            len(normalized),
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            float(relevance_threshold),
            scope_kind,
            owner_id,
        )
        return json.loads(raw)


__all__ = [
    "LearningManager",
    "LearningStats",
    "MemoryRecord",
    "SessionSearchCandidate",
    "SessionSearchResult",
    "SessionSourceRecord",
]
