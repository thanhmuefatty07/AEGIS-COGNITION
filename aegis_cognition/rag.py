"""Candidate retrieval compatibility adapter for AEGIS-COGNITION.

The learning bridge currently returns candidate references, not hydrated source
content.  This module must therefore never describe candidates as verified
context or attach invented retrieval-quality percentages to them.
"""

from __future__ import annotations
from typing import Any

from core.python.aegis_adapter import LearningManager
from core.python.aegis.context_compiler import ContextCompilation, ContextCompilationError, ContextCompiler


class RAGManager:
    """
    Manages Retrieval-Augmented Generation context for AEGIS-COGNITION.
    Searches past session transcripts and formats context with v3.1 metadata.
    """

    def __init__(
        self,
        learning_manager: Any = None,
        data_sources: list[str] | None = None,
        retrieval_method: str = "hybrid (lexical + vector)",
        retrieval_level: str = "global",
        top_k: int = 3,
        context_level: str = "2-hop",
        integration_method: str = "CandidateOnlyGate validation",
        conflict_resolution: str = "Prioritize latest physical evidence",
        consistency_check: str = "Compare across ContextFoldRecord",
        # Deprecated compatibility inputs.  They are retained so existing
        # callers do not break, but are deliberately not stored or rendered.
        reliability_score: float | None = None,
        completeness_score: float | None = None,
        accuracy_score: float | None = None,
        verification_method: list[str] | None = None,
    ) -> None:
        self.learning_manager = learning_manager or LearningManager()
        self.data_sources = data_sources or ["Past Session Transcripts", "Evidence Index", "AST Signatures"]
        self.retrieval_method = retrieval_method
        self.retrieval_level = retrieval_level
        self.top_k = top_k
        self.context_level = context_level
        self.integration_method = integration_method
        self.conflict_resolution = conflict_resolution
        self.consistency_check = consistency_check
        self.retrieval_error: str | None = None
        del reliability_score, completeness_score, accuracy_score, verification_method

    def retrieve_candidates(self, query: str) -> tuple[Any, ...]:
        """Return candidate references without upgrading them to context."""

        if not self.learning_manager:
            return ()

        self.retrieval_error = None
        try:
            search_result = self.learning_manager.search_past(query, top_k=self.top_k)
        except (ImportError, RuntimeError, ValueError) as error:
            # A missing optional native bridge or an unavailable index is a
            # degraded read path.  Preserve the truthful empty result and let
            # the application emit the degraded signal; never fabricate text.
            self.retrieval_error = type(error).__name__
            return ()
        return tuple(search_result.results)

    def retrieve_and_format(self, query: str) -> str:
        """
        Format a diagnostic candidate report for compatibility callers.

        This output is intentionally not suitable for an LLM prompt.  A
        hydrator must resolve source references and authorize actual content
        before context compilation is introduced.
        """
        candidates = self.retrieve_candidates(query)

        if not candidates:
            return ""

        # Formats the RAG Strategy and metadata blocks
        sources_str = ", ".join(self.data_sources)
        retrieval_strategy = (
            f"[RETRIEVAL STRATEGY]\n"
            f"- Nguồn dữ liệu: {sources_str}\n"
            f"- Kỹ thuật truy xuất: {self.retrieval_method}\n"
            f"- Cấp độ truy xuất: {self.retrieval_level}\n"
            f"- Kích thước kết quả: {self.top_k}"
        )

        # Candidate references intentionally contain no source text.
        context_payload = ""
        for i, candidate in enumerate(candidates):
            context_payload += (
                f"--- Candidate {i + 1} ---\n"
                f"- Hash: {candidate.evidence_ref_hash}\n"
                f"- Session Segment: {candidate.segment_id}\n"
                f"- Match Tier: {candidate.tier}\n"
                f"- Score: {candidate.score:.4f}\n"
            )

        rag_block = (
            f"[CANDIDATE REFERENCES ONLY — NOT MODEL CONTEXT]\n\n"
            f"{retrieval_strategy}\n\n"
            f"[CANDIDATE METADATA — SOURCE HYDRATION REQUIRED]\n"
            f"{context_payload.strip()}"
        )
        return rag_block

    def compile_context(
        self,
        query: str,
        *,
        scope_kind: str = "USER_PRIVATE",
        owner_id: str | None = None,
        token_budget: int = 2048,
        mandatory_session_ids: tuple[int, ...] = (),
    ) -> ContextCompilation:
        """Hydrate authorized sources and render bounded model context."""

        candidates = self.retrieve_candidates(query)
        if self.retrieval_error is not None:
            raise ContextCompilationError(
                "RETRIEVAL_UNAVAILABLE",
                f"context retrieval unavailable: {self.retrieval_error}",
            )
        return ContextCompiler(
            self.learning_manager,
            token_budget=token_budget,
            candidate_cap=min(100, max(self.top_k, 1)),
        ).compile(
            query,
            candidates,
            scope_kind=scope_kind,
            owner_id=owner_id,
            mandatory_session_ids=mandatory_session_ids,
        )
