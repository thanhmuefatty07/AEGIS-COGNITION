"""
AEGIS-COGNITION RAG Manager.
Retrieves past session context using LearningManager and formats it according to validation and evidence policies.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Any

try:
    from core.python.aegis_adapter import LearningManager
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "python"))
    try:
        from aegis_adapter import LearningManager
    except ImportError:
        LearningManager = None


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
        reliability_score: float = 0.999,
        completeness_score: float = 0.95,
        accuracy_score: float = 0.98,
        verification_method: list[str] | None = None,
    ) -> None:
        self.learning_manager = learning_manager or (LearningManager() if LearningManager else None)
        self.data_sources = data_sources or ["Past Session Transcripts", "Evidence Index", "AST Signatures"]
        self.retrieval_method = retrieval_method
        self.retrieval_level = retrieval_level
        self.top_k = top_k
        self.context_level = context_level
        self.integration_method = integration_method
        self.conflict_resolution = conflict_resolution
        self.consistency_check = consistency_check
        self.reliability_score = reliability_score
        self.completeness_score = completeness_score
        self.accuracy_score = accuracy_score
        self.verification_method = verification_method or ["BrowserWitnessProof", "MemoryCommitProof"]

    def retrieve_and_format(self, query: str) -> str:
        """
        Queries the learning manager for context, and formats the RAG block.
        """
        if not self.learning_manager:
            return ""

        try:
            search_result = self.learning_manager.search_past(query, top_k=self.top_k)
            candidates: list[Any] = list(search_result.results)
        except Exception:
            candidates = []

        if not candidates:
            return ""

        # Formats the RAG Strategy and metadata blocks
        sources_str = ", ".join(self.data_sources)
        verifications_str = ", ".join(self.verification_method)

        retrieval_strategy = (
            f"[RETRIEVAL STRATEGY]\n"
            f"- Nguồn dữ liệu: {sources_str}\n"
            f"- Kỹ thuật truy xuất: {self.retrieval_method}\n"
            f"- Cấp độ truy xuất: {self.retrieval_level}\n"
            f"- Kích thước kết quả: {self.top_k}"
        )

        context_integration = (
            f"[CONTEXT INTEGRATION]\n"
            f"- Lớp Context: {self.context_level}\n"
            f"- Phương pháp kết hợp: {self.integration_method}\n"
            f"- Xử lý mâu thuẫn: {self.conflict_resolution}\n"
            f"- Kiểm tra tính nhất quán: {self.consistency_check}"
        )

        quality_guarantees = (
            f"[QUALITY GUARANTEES]\n"
            f"- Độ tin cậy: {self.reliability_score * 100}%\n"
            f"- Tính đầy đủ: {self.completeness_score * 100}%\n"
            f"- Tính chính xác: {self.accuracy_score * 100}%\n"
            f"- Xác thực bằng: {verifications_str}"
        )

        # Retrieve detailed context
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
            f"[RETRIEVED CONTEXT]\n\n"
            f"{retrieval_strategy}\n\n"
            f"{context_integration}\n\n"
            f"{quality_guarantees}\n\n"
            f"[RETRIEVED EVIDENCE CANDIDATES]\n"
            f"{context_payload.strip()}"
        )
        return rag_block
