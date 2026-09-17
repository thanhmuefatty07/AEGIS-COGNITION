"""Authorized source hydration and deterministic provider-neutral context rendering."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from .hashing import stable_hash


class ContextCompilationError(ValueError):
    """A context request cannot be compiled without weakening its contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HydratedContextItem:
    session_id: int
    content_hash: str
    timestamp: int
    scope_kind: str
    owner_id: str
    content: str
    score: float
    token_cost: int
    mandatory: bool
    retention_class: str = "condensable"
    node_id: int | None = None
    source_kind: str = "session"
    source_id: str | None = None


@dataclass(frozen=True)
class ContextCompilation:
    schema: str
    status: str
    query: str
    scope_kind: str
    owner_id: str
    token_budget: int
    token_count: int
    accounting: str
    items: tuple[HydratedContextItem, ...]
    rendered: str
    manifest: dict[str, Any]
    manifest_hash: str


class ContextCompiler:
    """Hydrate only authorized Rust-owned sources and render a bounded context."""

    def __init__(
        self,
        learning_manager: Any,
        *,
        token_budget: int = 2048,
        candidate_cap: int = 100,
    ) -> None:
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        if candidate_cap < 1 or candidate_cap > 100:
            raise ValueError("candidate_cap must be between 1 and 100")
        self.learning_manager = learning_manager
        self.token_budget = token_budget
        self.candidate_cap = candidate_cap

    def compile(
        self,
        query: str,
        candidates: tuple[Any, ...] | list[Any],
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        mandatory_session_ids: tuple[int, ...] = (),
        active_memories: tuple[Any, ...] | list[Any] = (),
    ) -> ContextCompilation:
        if not query or not query.strip():
            raise ContextCompilationError("INVALID_QUERY", "context query must be non-empty")
        if not scope_kind or not scope_kind.strip():
            raise ContextCompilationError("INVALID_SCOPE", "scope_kind must be non-empty")
        resolved_owner = owner_id or "local-profile"
        if not resolved_owner.strip():
            raise ContextCompilationError("INVALID_OWNER", "owner_id must be non-empty")

        mandatory = {int(session_id) for session_id in mandatory_session_ids}
        unique_candidates: dict[int, Any] = {}
        for candidate in list(candidates)[: self.candidate_cap]:
            session_id = self._candidate_session_id(candidate)
            if session_id > 0:
                unique_candidates.setdefault(session_id, candidate)
        missing_mandatory = mandatory.difference(unique_candidates)
        if missing_mandatory:
            raise ContextCompilationError(
                "MANDATORY_SOURCE_UNAVAILABLE",
                "mandatory source was not present in the bounded candidate set",
            )

        hydrated: list[HydratedContextItem] = []
        for session_id, candidate in unique_candidates.items():
            record = self.learning_manager.read_session_record_scoped(
                session_id,
                scope_kind=scope_kind,
                owner_id=resolved_owner,
            )
            if record is None:
                if session_id in mandatory:
                    raise ContextCompilationError(
                        "MANDATORY_SOURCE_UNAVAILABLE",
                        f"mandatory session {session_id} is unavailable in the requested scope",
                    )
                continue
            candidate_hash = self._candidate_hash(candidate)
            if candidate_hash and candidate_hash.lower() != record.content_hash.lower():
                if session_id in mandatory:
                    raise ContextCompilationError(
                        "STALE_SOURCE",
                        f"mandatory session {session_id} changed after candidate generation",
                    )
                continue
            content = record.content
            if not content.strip():
                continue
            hydrated.append(
                HydratedContextItem(
                    session_id=record.session_id,
                    content_hash=record.content_hash,
                    timestamp=record.timestamp,
                    scope_kind=record.scope_kind,
                    owner_id=record.owner_id,
                    content=content,
                    score=self._candidate_score(candidate),
                    token_cost=self._estimate_tokens(content),
                    mandatory=session_id in mandatory,
                    retention_class=self._candidate_retention_class(candidate),
                    node_id=session_id,
                    source_kind="session",
                    source_id=f"0x{session_id:x}",
                )
            )

        used_node_ids = {item.node_id or item.session_id for item in hydrated}
        for memory in list(active_memories)[: self.candidate_cap]:
            if not self._is_active_memory(memory, scope_kind=scope_kind, owner_id=resolved_owner):
                continue
            memory_id_text = str(self._value(memory, "memory_id", ""))
            try:
                memory_id = self._parse_session_id(memory_id_text)
            except (TypeError, ValueError):
                continue
            if memory_id <= 0:
                continue
            content = self._value(memory, "content", None)
            content_hash = str(self._value(memory, "content_hash", ""))
            if not isinstance(content, str) or not content.strip() or not content_hash.strip():
                continue
            node_id = self._memory_node_id(memory_id)
            if node_id in used_node_ids:
                raise ContextCompilationError(
                    "CONTEXT_NODE_COLLISION",
                    "active memory node collided with another context source",
                )
            used_node_ids.add(node_id)
            observed_at_ms = self._bounded_int(self._value(memory, "observed_at_ms", 0))
            hydrated.append(
                HydratedContextItem(
                    # Kept populated for compatibility with the existing
                    # selector contract; node_id/source_id are authoritative.
                    session_id=node_id,
                    content_hash=content_hash,
                    timestamp=observed_at_ms,
                    scope_kind=str(self._value(memory, "scope_kind", scope_kind)),
                    owner_id=str(self._value(memory, "owner_id", resolved_owner)),
                    content=content,
                    score=self._memory_score(memory),
                    token_cost=self._estimate_tokens(content),
                    mandatory=False,
                    retention_class="condensable",
                    node_id=node_id,
                    source_kind="memory",
                    source_id=memory_id_text,
                )
            )

        hydrated.sort(
            key=lambda item: (
                -item.mandatory,
                self._retention_rank(item.retention_class),
                -item.score,
                item.session_id,
                item.content_hash,
            )
        )
        protected_items = [
            item for item in hydrated if item.mandatory or item.retention_class == "protected"
        ]
        protected_cost = sum(item.token_cost for item in protected_items)
        if protected_cost > self.token_budget:
            raise ContextCompilationError(
                "CONTEXT_OVERFLOW",
                "protected hydrated context exceeds the configured token budget",
            )

        selector = getattr(self.learning_manager, "select_context_items", None)
        backend = "python-compatibility-selector-v1"
        selection: dict[str, Any] | None = None
        if callable(selector) and hydrated:
            try:
                selection = selector(
                    [
                        {
                            "session_id": item.session_id,
                            "node_id": item.node_id or item.session_id,
                            "token_cost": item.token_cost,
                            "score": item.score,
                            "retention_class": item.retention_class,
                        }
                        for item in hydrated
                    ],
                    self.token_budget,
                    mandatory_session_ids=tuple(sorted(mandatory)),
                )
                if not isinstance(selection, dict):
                    selection = None
                    backend = "python-compatibility-selector-v1"
                else:
                    backend = str(selection.get("backend", "rust-context-governor-v1"))
            except AttributeError:
                # An older extension remains usable through the explicit
                # compatibility selector; it must be labeled in the manifest.
                selection = None

        if selection is not None:
            selected_ids = {
                self._parse_session_id(value)
                for value in selection.get("selected_node_ids", [])
            }
            protected_ids = {
                item.node_id or item.session_id
                for item in hydrated
                if item.retention_class == "protected"
            } | mandatory
            if not protected_ids.issubset(selected_ids):
                raise ContextCompilationError(
                    "CONTEXT_SELECTOR_INVALID",
                    "Rust context selector omitted a protected source",
                )
            selected = [
                item
                for item in hydrated
                if (item.node_id or item.session_id) in selected_ids
            ]
            token_count = sum(item.token_cost for item in selected)
            if token_count > self.token_budget:
                raise ContextCompilationError(
                    "CONTEXT_SELECTOR_INVALID",
                    "context selector returned a pack over the configured budget",
                )
        else:
            selected = []
            token_count = 0
            for item in hydrated:
                if item.mandatory or item.retention_class == "protected" or token_count + item.token_cost <= self.token_budget:
                    selected.append(item)
                    token_count += item.token_cost

        selected.sort(
            key=lambda item: (
                -item.mandatory,
                -item.score,
                item.node_id or item.session_id,
                item.content_hash,
            )
        )
        rendered = self._render(selected)
        manifest = {
            "schema": "aegis-context-manifest-v1",
            "query": query,
            "scope_kind": scope_kind,
            "owner_id": resolved_owner,
            "token_budget": self.token_budget,
            "token_count": token_count,
            "accounting": "estimated-byte-heuristic-v1",
            "renderer": "provider-neutral-context-v1",
            "selection_backend": backend,
            "items": [
                {
                    "session_id": item.session_id,
                    "node_id": item.node_id or item.session_id,
                    "source_kind": item.source_kind,
                    "source_id": item.source_id,
                    "content_hash": item.content_hash,
                    "timestamp": item.timestamp,
                    "token_cost": item.token_cost,
                    "mandatory": item.mandatory,
                    "retention_class": item.retention_class,
                }
                for item in selected
            ],
        }
        return ContextCompilation(
            schema="aegis-context-compilation-v1",
            status="compiled" if selected else "empty",
            query=query,
            scope_kind=scope_kind,
            owner_id=resolved_owner,
            token_budget=self.token_budget,
            token_count=token_count,
            accounting="estimated-byte-heuristic-v1",
            items=tuple(selected),
            rendered=rendered,
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
        )

    @staticmethod
    def _candidate_session_id(candidate: Any) -> int:
        value = candidate.get("segment_id", 0) if isinstance(candidate, dict) else getattr(candidate, "segment_id", 0)
        return int(value)

    @staticmethod
    def _candidate_hash(candidate: Any) -> str:
        value = candidate.get("evidence_ref_hash", "") if isinstance(candidate, dict) else getattr(candidate, "evidence_ref_hash", "")
        return str(value)

    @staticmethod
    def _candidate_score(candidate: Any) -> float:
        value = candidate.get("score", 0.0) if isinstance(candidate, dict) else getattr(candidate, "score", 0.0)
        return float(value)

    @staticmethod
    def _candidate_retention_class(candidate: Any) -> str:
        value = (
            candidate.get("retention_class", "condensable")
            if isinstance(candidate, dict)
            else getattr(candidate, "retention_class", "condensable")
        )
        if value is None:
            value = "condensable"
        retention_class = str(value).strip().lower()
        if retention_class not in {"protected", "condensable", "ephemeral"}:
            raise ContextCompilationError(
                "INVALID_RETENTION_CLASS",
                "context retention_class must be protected, condensable, or ephemeral",
            )
        return retention_class

    @staticmethod
    def _value(value: Any, name: str, default: Any) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @classmethod
    def _is_active_memory(cls, memory: Any, *, scope_kind: str, owner_id: str) -> bool:
        return (
            str(cls._value(memory, "lifecycle", "")).upper() == "ACTIVE"
            and str(cls._value(memory, "validation", "")).upper() == "ACCEPTED"
            and str(cls._value(memory, "scope_kind", "")) == scope_kind
            and str(cls._value(memory, "owner_id", "")) == owner_id
        )

    @classmethod
    def _memory_score(cls, memory: Any) -> float:
        raw_score = cls._value(memory, "relevance_score", None)
        if type(raw_score) in (int, float) and math.isfinite(float(raw_score)):
            return max(0.0, min(1.0, float(raw_score)))
        # Search ordering is already bounded and deterministic.  This
        # neutral fallback avoids pretending that FTS order is a calibrated
        # relevance probability.
        return 0.5

    @staticmethod
    def _bounded_int(value: Any) -> int:
        if type(value) is bool:
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    @staticmethod
    def _memory_node_id(memory_id: int) -> int:
        digest = hashlib.sha256(
            b"aegis-context-memory-node-v1\0" + str(memory_id).encode("ascii")
        ).digest()
        node_id = int.from_bytes(digest[:16], "big")
        return node_id or 1

    @staticmethod
    def _retention_rank(retention_class: str) -> int:
        return {"protected": 0, "condensable": 1, "ephemeral": 2}[retention_class]

    @staticmethod
    def _parse_session_id(value: Any) -> int:
        text = str(value)
        return int(text, 16) if text.startswith("0x") else int(text)

    @staticmethod
    def _estimate_tokens(content: str) -> int:
        return max(1, (len(content.encode("utf-8")) + 3) // 4) + 8

    @staticmethod
    def _render(items: list[HydratedContextItem]) -> str:
        if not items:
            return ""
        blocks = ["[AUTHORIZED HYDRATED CONTEXT — SOURCE BOUND]"]
        for item in items:
            if item.source_kind == "memory":
                blocks.extend(
                    (
                        f"[ACTIVE MEMORY DATA memory_id={item.source_id} hash={item.content_hash} "
                        f"scope={item.scope_kind} owner={item.owner_id}]",
                        item.content,
                        "[/ACTIVE MEMORY DATA]",
                    )
                )
                continue
            blocks.extend(
                (
                    f"[SOURCE session=0x{item.session_id:x} hash={item.content_hash} "
                    f"scope={item.scope_kind} owner={item.owner_id}]",
                    item.content,
                    "[/SOURCE]",
                )
            )
        blocks.append("[/AUTHORIZED HYDRATED CONTEXT]")
        return "\n".join(blocks)


__all__ = [
    "ContextCompilation",
    "ContextCompilationError",
    "ContextCompiler",
    "HydratedContextItem",
]
