"""Lazy public exports for the split Python gateway modules."""

from __future__ import annotations

from typing import Any


_ADAPTER_EXPORTS = {
    "Agent",
    "AegisAdapter",
    "AegisBrowserActionResult",
    "AegisBrowserHotFirstActionResult",
    "AegisRunResult",
    "HotCommitRecord",
    "ProviderBudgetEvidence",
    "ProviderBudgetRecord",
    "ProviderRateLimitError",
    "ProviderRouteRecord",
    "TrustPolicySnapshot",
    "commit_hot_evidence",
    "commit_hot_evidence_batch",
    "trust_policy_snapshot",
}


def __getattr__(name: str) -> Any:
    if name not in _ADAPTER_EXPORTS:
        raise AttributeError(name)
    try:
        from .. import aegis_adapter
    except ImportError:
        import aegis_adapter  # type: ignore[import-not-found]

    return getattr(aegis_adapter, name)


__all__ = sorted(_ADAPTER_EXPORTS)
