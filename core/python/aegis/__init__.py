"""Lazy public exports for the split Python gateway modules."""

from __future__ import annotations

from typing import Any


_ADAPTER_EXPORTS = {
    "Agent",
    "AegisAdapter",
    "AegisBrowserActionResult",
    "AegisBrowserHotFirstActionResult",
    "AegisRunResult",
    "ConnectionCatalog",
    "ConnectionClientError",
    "ConnectionRecord",
    "ConnectionResponse",
    "ConversationManager",
    "ConversationCheckpoint",
    "ConversationExecution",
    "ConversationRecord",
    "ConversationSnapshot",
    "ConversationToolCall",
    "ConversationTurn",
    "DesktopCommandRouter",
    "DesktopProtocolError",
    "DesktopRequest",
    "DiscoveryError",
    "DiscoveryResponse",
    "EgressGrant",
    "HotCommitRecord",
    "ModelDescriptor",
    "OpenAICompatibleClient",
    "PlatformSecretStore",
    "ProviderBudgetEvidence",
    "ProviderBudgetRecord",
    "ProviderRateLimitError",
    "ProviderRouteRecord",
    "SecretStoreError",
    "TrustPolicySnapshot",
    "decode_request",
    "encode_response",
    "commit_hot_evidence",
    "commit_hot_evidence_batch",
    "trust_policy_snapshot",
}

_CHATGPT_WEB_EXPORTS = {
    "ChatGPTWebClient",
    "ChatGPTWebConfig",
    "ChatGPTWebUnavailableError",
}


def __getattr__(name: str) -> Any:
    if name in _CHATGPT_WEB_EXPORTS:
        try:
            from .. import chatgpt_web_client
        except ImportError:
            import chatgpt_web_client  # type: ignore[import-not-found]

        return getattr(chatgpt_web_client, name)
    if name not in _ADAPTER_EXPORTS:
        raise AttributeError(name)
    try:
        from .. import aegis_adapter
    except ImportError:
        import aegis_adapter  # type: ignore[import-not-found]

    return getattr(aegis_adapter, name)


__all__ = sorted(_ADAPTER_EXPORTS | _CHATGPT_WEB_EXPORTS)
