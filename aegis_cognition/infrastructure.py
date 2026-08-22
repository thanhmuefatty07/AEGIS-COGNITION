"""Infrastructure factories kept outside the application/domain layers."""

from __future__ import annotations

from typing import Any

from core.python.aegis_adapter import AegisAdapter, LearningManager


def build_gateway(**kwargs: Any) -> AegisAdapter:
    """Construct the Rust-authoritative friendly gateway at one boundary."""

    return AegisAdapter(**kwargs)


def build_learning_manager() -> LearningManager:
    """Construct the learning bridge lazily at the infrastructure boundary."""

    return LearningManager()
