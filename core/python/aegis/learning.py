"""Learning-loop bridge isolated from the run/provider gateway."""

from __future__ import annotations

import json
import time
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

    def sync_memory(self, session_id: str = "0x1") -> dict[str, Any]:
        raw = self._native.aegis_trigger_memory_nudge(session_id)
        return json.loads(raw)


__all__ = ["LearningManager", "LearningStats", "SessionSearchCandidate", "SessionSearchResult"]
