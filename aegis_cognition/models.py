"""Application-facing domain values for the canonical Python facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunResult:
    """Stable result returned by the public ``Agent`` facade."""

    task: str
    output: Any
    trust_level: str
    provider: str | None
    hot_commit: Any
    correlation: dict[str, Any] | None = None
    lab_manifest: dict[str, Any] | None = None
    lab_events: tuple[dict[str, Any], ...] = ()

    def __repr__(self) -> str:
        return f"RunResult(output={str(self.output)[:80]!r}, provider={self.provider!r})"
