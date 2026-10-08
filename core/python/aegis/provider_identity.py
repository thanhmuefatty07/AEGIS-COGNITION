"""Key-only provider hints used by the desktop connection onboarding flow.

The prefix is only a routing hint, never proof that a key is valid.  The host
still validates the key by performing bounded model discovery after the user
explicitly connects it.  Raw key material is intentionally absent from the
returned value and from this module's data model.
"""

from __future__ import annotations

from dataclasses import dataclass

from .provider_registry import ProviderSpec, key_prefix_specs


@dataclass(frozen=True)
class ProviderIdentity:
    provider_kind: str
    provider_label: str
    endpoint: str | None
    protocol: str
    confidence: str
    hints: tuple[str, ...]
    requires_confirmation: bool = False

    @property
    def requires_endpoint(self) -> bool:
        return self.endpoint is None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_kind": self.provider_kind,
            "provider_label": self.provider_label,
            "endpoint": self.endpoint,
            "protocol": self.protocol,
            "confidence": self.confidence,
            "hints": list(self.hints),
            "requires_endpoint": self.requires_endpoint,
            "requires_confirmation": self.requires_confirmation,
        }


_KNOWN_PREFIXES: tuple[tuple[str, ProviderSpec], ...] = key_prefix_specs()


def identify_api_key(api_key: str) -> ProviderIdentity:
    """Return a non-secret provider hint for a bounded, non-empty key."""

    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 8192:
        raise ValueError("api_key must be a bounded non-empty string")
    normalized = api_key.strip()
    for prefix, spec in _KNOWN_PREFIXES:
        if normalized.startswith(prefix):
            return ProviderIdentity(
                provider_kind=spec.provider_kind,
                provider_label=spec.label,
                endpoint=spec.endpoint,
                protocol=spec.protocol,
                confidence=spec.confidence,
                hints=spec.hints,
                requires_confirmation=spec.requires_confirmation,
            )
    return ProviderIdentity(
        "openai-compatible",
        "Compatible provider",
        None,
        "chat-completions",
        "unknown",
        ("No safe provider prefix matched", "Enter an HTTPS-compatible endpoint to continue"),
    )


__all__ = ["ProviderIdentity", "identify_api_key"]
