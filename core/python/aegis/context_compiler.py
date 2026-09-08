"""Authorized source hydration and deterministic provider-neutral context rendering."""

from __future__ import annotations

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
                )
            )

        hydrated.sort(key=lambda item: (-item.mandatory, -item.score, item.session_id, item.content_hash))
        mandatory_items = [item for item in hydrated if item.mandatory]
        mandatory_cost = sum(item.token_cost for item in mandatory_items)
        if mandatory_cost > self.token_budget:
            raise ContextCompilationError(
                "CONTEXT_OVERFLOW",
                "mandatory hydrated context exceeds the configured token budget",
            )

        selector = getattr(self.learning_manager, "select_context_items", None)
        backend = "python-compatibility-selector-v1"
        selection: dict[str, Any] | None = None
        if callable(selector):
            try:
                selection = selector(
                    [
                        {
                            "session_id": item.session_id,
                            "token_cost": item.token_cost,
                            "score": item.score,
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
            if not mandatory.issubset(selected_ids):
                raise ContextCompilationError(
                    "CONTEXT_SELECTOR_INVALID",
                    "Rust context selector omitted a mandatory source",
                )
            selected = [item for item in hydrated if item.session_id in selected_ids]
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
                if item.mandatory or token_count + item.token_cost <= self.token_budget:
                    selected.append(item)
                    token_count += item.token_cost

        selected.sort(key=lambda item: (-item.mandatory, -item.score, item.session_id, item.content_hash))
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
                    "content_hash": item.content_hash,
                    "timestamp": item.timestamp,
                    "token_cost": item.token_cost,
                    "mandatory": item.mandatory,
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
