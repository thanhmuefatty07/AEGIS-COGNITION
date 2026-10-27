"""Authorized source hydration and deterministic provider-neutral context rendering."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, replace
from typing import Any

from .hashing import stable_hash

MAX_SELECTOR_CONTEXT_ITEMS = 100
MAX_VERIFIED_FILE_RELATIONS = 8
# The Rust selector uses fixed-point relevance scores in millionths. One
# verified current file relation adds one bounded unit, regardless of link count.
FILE_RELATION_UTILITY_MICROS = 1_000_000
CONTEXT_ACCOUNTING = "estimated-rendered-context-utf8-bytes-v2"
_CONTEXT_RENDER_HEADER = "[AUTHORIZED HYDRATED CONTEXT — SOURCE BOUND]"
_CONTEXT_RENDER_FOOTER = "[/AUTHORIZED HYDRATED CONTEXT]"


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
    dependency_coverage: int = 0
    file_relations: tuple[tuple[str, str], ...] = ()


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
        verified_file_relations: tuple[Any, ...] | list[Any] = (),
    ) -> ContextCompilation:
        if not query or not query.strip():
            raise ContextCompilationError("INVALID_QUERY", "context query must be non-empty")
        if not scope_kind or not scope_kind.strip():
            raise ContextCompilationError("INVALID_SCOPE", "scope_kind must be non-empty")
        resolved_owner = owner_id or "local-profile"
        if not resolved_owner.strip():
            raise ContextCompilationError("INVALID_OWNER", "owner_id must be non-empty")

        relations_by_memory = self._file_relation_sources(verified_file_relations)
        selector_v2 = getattr(self.learning_manager, "select_context_items_v2", None)
        relation_graph = (
            self._file_relation_graph(verified_file_relations, scope_kind=scope_kind)
            if callable(selector_v2) and verified_file_relations
            else None
        )

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
                    token_cost=0,
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
            except TypeError, ValueError:
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
            relation_sources = relations_by_memory.get(memory_id, ())
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
                    token_cost=0,
                    mandatory=False,
                    retention_class="condensable",
                    node_id=node_id,
                    source_kind="memory",
                    source_id=memory_id_text,
                    # Explicit file provenance is a single bounded support
                    # signal; additional links do not multiply its utility.
                    dependency_coverage=(FILE_RELATION_UTILITY_MICROS if relation_sources else 0),
                    file_relations=relation_sources,
                )
            )

        hydrated = [
            replace(item, token_cost=self._estimate_tokens(f"{self._render_item(item)}\n")) for item in hydrated
        ]
        hydrated.sort(
            key=lambda item: (
                -item.mandatory,
                self._retention_rank(item.retention_class),
                -item.score,
                item.session_id,
                item.content_hash,
            )
        )
        protected_items = [item for item in hydrated if item.mandatory or item.retention_class == "protected"]
        if len(protected_items) > MAX_SELECTOR_CONTEXT_ITEMS:
            raise ContextCompilationError(
                "CONTEXT_ITEM_LIMIT_EXCEEDED",
                "mandatory or protected hydrated context exceeds the selector item limit",
            )
        render_overhead = self._render_overhead_tokens() if hydrated else 0
        protected_cost = sum(item.token_cost for item in protected_items)
        if protected_items:
            protected_cost += render_overhead
        if protected_cost > self.token_budget:
            raise ContextCompilationError(
                "CONTEXT_OVERFLOW",
                "protected hydrated context exceeds the configured token budget",
            )
        if len(hydrated) > MAX_SELECTOR_CONTEXT_ITEMS:
            protected_ids = {item.node_id or item.session_id for item in protected_items}
            optional_items = [item for item in hydrated if (item.node_id or item.session_id) not in protected_ids]
            hydrated = [
                *protected_items,
                *optional_items[: MAX_SELECTOR_CONTEXT_ITEMS - len(protected_items)],
            ]

        selection_budget = max(0, self.token_budget - render_overhead)
        selector = getattr(self.learning_manager, "select_context_items", None)
        backend = "python-compatibility-selector-v1"
        selection: dict[str, Any] | None = None
        selected_relation_ids: list[str] | None = None
        if relation_graph is not None:
            hydrated_memory_ids = {item.node_id or item.session_id for item in hydrated if item.source_kind == "memory"}
            graph_relations = [
                edge
                for edge in relation_graph["relations"]
                if self._parse_session_id(edge["memory_node_id"]) in hydrated_memory_ids
            ]
            relation_graph = {
                "schema": relation_graph["schema"],
                "active_file_node_ids": sorted(
                    {edge["file_node_id"] for edge in graph_relations},
                    key=self._parse_session_id,
                ),
                "relations": graph_relations,
            }
            if not graph_relations:
                relation_graph = None
        if callable(selector) and hydrated and selection_budget > 0:
            try:
                selector_items = [
                    {
                        "session_id": item.session_id,
                        "node_id": item.node_id or item.session_id,
                        "kind": item.source_kind,
                        "token_cost": item.token_cost,
                        "score": item.score,
                        "retention_class": item.retention_class,
                        "dependency_coverage": item.dependency_coverage,
                    }
                    for item in hydrated
                ]
                if relation_graph is not None and callable(selector_v2):
                    selection = selector_v2(
                        selector_items,
                        relation_graph,
                        selection_budget,
                        mandatory_session_ids=tuple(sorted(mandatory)),
                    )
                else:
                    selection = selector(
                        selector_items,
                        selection_budget,
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
            selected_ids = {self._parse_session_id(value) for value in selection.get("selected_node_ids", [])}
            if relation_graph is not None and selection.get("schema") == "aegis-context-selection-v2":
                available_ids = {item.node_id or item.session_id for item in hydrated}
                expected_related_ids = {
                    self._parse_session_id(edge["memory_node_id"]) for edge in relation_graph["relations"]
                }
                expected_selected_relations = sorted(
                    (
                        self._parse_session_id(edge["relation_id"]),
                        edge["relation_id"],
                    )
                    for edge in relation_graph["relations"]
                    if self._parse_session_id(edge["memory_node_id"]) in selected_ids
                )
                try:
                    actual_related_ids = {
                        self._parse_session_id(value) for value in selection["related_memory_node_ids"]
                    }
                    actual_selected_relations = [
                        self._parse_session_id(value) for value in selection["selected_relation_ids"]
                    ]
                except KeyError, TypeError, ValueError:
                    raise ContextCompilationError(
                        "CONTEXT_SELECTOR_INVALID",
                        "versioned context selector returned an invalid relation explanation",
                    ) from None
                if (
                    selected_ids.difference(available_ids)
                    or actual_related_ids != expected_related_ids.intersection(selected_ids)
                    or actual_selected_relations != [item[0] for item in expected_selected_relations]
                ):
                    raise ContextCompilationError(
                        "CONTEXT_SELECTOR_INVALID",
                        "versioned context selector returned relations outside the verified graph",
                    )
                selected_relation_ids = [item[1] for item in expected_selected_relations]
            protected_ids = {
                item.node_id or item.session_id for item in hydrated if item.retention_class == "protected"
            } | mandatory
            if not protected_ids.issubset(selected_ids):
                raise ContextCompilationError(
                    "CONTEXT_SELECTOR_INVALID",
                    "Rust context selector omitted a protected source",
                )
            selected = [item for item in hydrated if (item.node_id or item.session_id) in selected_ids]
            token_count = sum(item.token_cost for item in selected)
            if token_count > selection_budget:
                raise ContextCompilationError(
                    "CONTEXT_SELECTOR_INVALID",
                    "context selector returned a pack over the configured budget",
                )
        else:
            selected = []
            token_count = 0
            for item in hydrated:
                if (
                    item.mandatory
                    or item.retention_class == "protected"
                    or token_count + item.token_cost <= selection_budget
                ):
                    selected.append(item)
                    token_count += item.token_cost

        if selected:
            token_count += render_overhead
        if token_count > self.token_budget:
            raise ContextCompilationError(
                "CONTEXT_SELECTOR_INVALID",
                "rendered context exceeds the configured token budget",
            )

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
            "accounting": CONTEXT_ACCOUNTING,
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
                    **(
                        {
                            "relations": [
                                {
                                    "kind": "confirmed_file_memory",
                                    "relative_path": relative_path,
                                    "source_content_hash": source_content_hash,
                                }
                                for relative_path, source_content_hash in item.file_relations
                            ]
                        }
                        if item.file_relations
                        else {}
                    ),
                }
                for item in selected
            ],
        }
        if selected_relation_ids is not None:
            manifest["selected_relation_ids"] = selected_relation_ids
        return ContextCompilation(
            schema="aegis-context-compilation-v1",
            status="compiled" if selected else "empty",
            query=query,
            scope_kind=scope_kind,
            owner_id=resolved_owner,
            token_budget=self.token_budget,
            token_count=token_count,
            accounting=CONTEXT_ACCOUNTING,
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
        value = (
            candidate.get("evidence_ref_hash", "")
            if isinstance(candidate, dict)
            else getattr(candidate, "evidence_ref_hash", "")
        )
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

    @classmethod
    def _file_relation_sources(
        cls,
        relations: tuple[Any, ...] | list[Any],
    ) -> dict[int, tuple[tuple[str, str], ...]]:
        if not isinstance(relations, (tuple, list)):
            raise ContextCompilationError(
                "INVALID_FILE_RELATION",
                "verified file relations must be a bounded sequence",
            )
        if len(relations) > MAX_VERIFIED_FILE_RELATIONS:
            raise ContextCompilationError(
                "FILE_RELATION_LIMIT_EXCEEDED",
                "verified file relations exceed the bounded context limit",
            )
        by_memory: dict[int, set[tuple[str, str]]] = {}
        for relation in relations:
            memory_id_text = cls._value(relation, "memory_id", "")
            relative_path = cls._value(relation, "relative_path", "")
            source_hash = cls._value(relation, "source_content_hash", "")
            if (
                not isinstance(memory_id_text, str)
                or not isinstance(relative_path, str)
                or not isinstance(source_hash, str)
            ):
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation fields have invalid types",
                )
            parts = relative_path.split("/")
            if (
                not relative_path
                or relative_path.startswith("/")
                or "\\" in relative_path
                or any(part in {"", ".", "..", ".git", ".aegis"} for part in parts)
                or len(relative_path) > 4096
                or len(source_hash) != 64
                or any(character not in "0123456789abcdefABCDEF" for character in source_hash)
            ):
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation source identity is invalid",
                )
            try:
                memory_id = cls._parse_session_id(memory_id_text)
            except TypeError, ValueError:
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation memory identifier is invalid",
                ) from None
            if memory_id <= 0:
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation memory identifier is invalid",
                )
            by_memory.setdefault(memory_id, set()).add((relative_path, source_hash.lower()))
        return {memory_id: tuple(sorted(sources)) for memory_id, sources in by_memory.items()}

    @classmethod
    def _file_relation_graph(
        cls,
        relations: tuple[Any, ...] | list[Any],
        *,
        scope_kind: str,
    ) -> dict[str, Any]:
        cls._file_relation_sources(relations)
        roots: set[int] = set()
        edges: list[dict[str, str]] = []
        seen_relation_ids: set[int] = set()
        for relation in relations:
            relation_id_text = cls._value(relation, "relation_id", "")
            memory_id_text = cls._value(relation, "memory_id", "")
            project_id = cls._value(relation, "project_id", "")
            relative_path = cls._value(relation, "relative_path", "")
            source_hash = cls._value(relation, "source_content_hash", "")
            if (
                not isinstance(relation_id_text, str)
                or not isinstance(memory_id_text, str)
                or not isinstance(project_id, str)
                or not project_id.strip()
                or cls._value(relation, "scope_kind", None) != scope_kind
                or str(cls._value(relation, "memory_lifecycle", "")).upper() != "ACTIVE"
                or str(cls._value(relation, "memory_validation", "")).upper() != "ACCEPTED"
                or cls._value(relation, "memory_content_current", False) is not True
            ):
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation provenance is incomplete or stale",
                )
            try:
                relation_id = cls._parse_session_id(relation_id_text)
                memory_id = cls._parse_session_id(memory_id_text)
            except TypeError, ValueError:
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation identifier is invalid",
                ) from None
            if relation_id <= 0 or relation_id in seen_relation_ids:
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation identifiers must be unique and positive",
                )
            seen_relation_ids.add(relation_id)
            if memory_id <= 0:
                raise ContextCompilationError(
                    "INVALID_FILE_RELATION",
                    "verified file relation memory identifier is invalid",
                )
            file_node_id = cls._file_node_id(project_id, relative_path, source_hash)
            roots.add(file_node_id)
            edges.append(
                {
                    "relation_id": f"0x{relation_id:x}",
                    "kind": "confirmed_file_memory",
                    "file_node_id": f"0x{file_node_id:x}",
                    "memory_node_id": f"0x{cls._memory_node_id(memory_id):x}",
                }
            )
        if len(roots) > MAX_VERIFIED_FILE_RELATIONS:
            raise ContextCompilationError(
                "FILE_RELATION_LIMIT_EXCEEDED",
                "verified file relations exceed the bounded context limit",
            )
        edges.sort(key=lambda edge: cls._parse_session_id(edge["relation_id"]))
        return {
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": [f"0x{node_id:x}" for node_id in sorted(roots)],
            "relations": edges,
        }

    @staticmethod
    def _bounded_int(value: Any) -> int:
        if type(value) is bool:
            return 0
        try:
            parsed = int(value)
        except TypeError, ValueError:
            return 0
        return max(0, parsed)

    @staticmethod
    def _memory_node_id(memory_id: int) -> int:
        digest = hashlib.sha256(b"aegis-context-memory-node-v1\0" + str(memory_id).encode("ascii")).digest()
        node_id = int.from_bytes(digest[:16], "big")
        return node_id or 1

    @staticmethod
    def _file_node_id(project_id: str, relative_path: str, source_hash: str) -> int:
        identity = (project_id, relative_path, source_hash.lower())
        encoded = bytearray(b"aegis-context-file-node-v1\0")
        for value in identity:
            part = value.encode("utf-8")
            encoded.extend(len(part).to_bytes(4, "big"))
            encoded.extend(part)
        digest = hashlib.sha256(encoded).digest()
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
        return max(1, (len(content.encode("utf-8")) + 3) // 4)

    @staticmethod
    def _sanitize_rendered_content(content: str) -> str:
        return re.sub(
            r"\[(\/?(?:ACTIVE MEMORY DATA|SOURCE|AUTHORIZED HYDRATED CONTEXT|CONFIRMED FILE RELATION)\b)",
            lambda m: f"[\u200b{m.group(1)}",
            content,
            flags=re.IGNORECASE,
        )

    @classmethod
    def _render_item(cls, item: HydratedContextItem) -> str:
        safe_content = cls._sanitize_rendered_content(item.content)
        if item.source_kind == "memory":
            return "\n".join(
                [
                    f"[ACTIVE MEMORY DATA memory_id={item.source_id} hash={item.content_hash} "
                    f"scope={item.scope_kind} owner={item.owner_id}]",
                    *(
                        f"[CONFIRMED FILE RELATION path={relative_path}]"
                        for relative_path, _source_hash in item.file_relations
                    ),
                    safe_content,
                    "[/ACTIVE MEMORY DATA]",
                ]
            )
        return "\n".join(
            (
                f"[SOURCE session=0x{item.session_id:x} hash={item.content_hash} "
                f"scope={item.scope_kind} owner={item.owner_id}]",
                safe_content,
                "[/SOURCE]",
            )
        )

    @classmethod
    def _render_overhead_tokens(cls) -> int:
        return cls._estimate_tokens(f"{_CONTEXT_RENDER_HEADER}\n{_CONTEXT_RENDER_FOOTER}")

    @staticmethod
    def _render(items: list[HydratedContextItem]) -> str:
        if not items:
            return ""
        return "\n".join(
            (
                _CONTEXT_RENDER_HEADER,
                *(ContextCompiler._render_item(item) for item in items),
                _CONTEXT_RENDER_FOOTER,
            )
        )


__all__ = [
    "CONTEXT_ACCOUNTING",
    "ContextCompilation",
    "ContextCompilationError",
    "ContextCompiler",
    "HydratedContextItem",
]
