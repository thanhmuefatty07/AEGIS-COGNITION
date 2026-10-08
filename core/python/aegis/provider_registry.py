"""Declarative metadata shared by safe provider identity and model discovery.

Provider identity, catalog transport, and model inference are separate concerns:
the key prefix is only a hint, discovery is enabled only for the provider's
documented host, and inference continues to use the stored protocol contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CatalogStrategy = Literal["compatible", "anthropic", "google", "openrouter"]
DiscoveryAuth = Literal["bearer", "anthropic-key", "google-key"]
CUSTOM_PROVIDER_KIND = "openai-compatible"


@dataclass(frozen=True)
class ProviderSpec:
    provider_kind: str
    label: str
    endpoint: str
    protocol: str
    key_prefixes: tuple[str, ...]
    confidence: str
    hints: tuple[str, ...]
    requires_confirmation: bool
    native_hosts: tuple[str, ...]
    catalog_strategy: CatalogStrategy
    discovery_auth: DiscoveryAuth
    models_resource: str = "models"


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "anthropic",
        "Anthropic",
        "https://api.anthropic.com/v1",
        "anthropic-messages",
        ("sk-ant-",),
        "high",
        ("Anthropic key prefix",),
        False,
        ("api.anthropic.com",),
        "anthropic",
        "anthropic-key",
    ),
    ProviderSpec(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "chat-completions",
        ("sk-or-",),
        "high",
        ("OpenRouter key prefix",),
        False,
        ("openrouter.ai", "eu.openrouter.ai"),
        "openrouter",
        "bearer",
    ),
    ProviderSpec(
        "google-gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "chat-completions",
        ("AIza",),
        "medium",
        (
            "AIza is used by Google Cloud APIs and does not uniquely identify Gemini",
            "Connecting sends the key to the Google Gemini endpoint",
        ),
        True,
        ("generativelanguage.googleapis.com",),
        "google",
        "google-key",
    ),
    ProviderSpec(
        "groq",
        "Groq",
        "https://api.groq.com/openai/v1",
        "chat-completions",
        ("gsk_",),
        "high",
        ("Groq key prefix",),
        False,
        ("api.groq.com",),
        "compatible",
        "bearer",
    ),
    ProviderSpec(
        "xai",
        "xAI",
        "https://api.x.ai/v1",
        "xai-responses",
        ("xai-",),
        "high",
        ("xAI key prefix",),
        False,
        ("api.x.ai",),
        "compatible",
        "bearer",
        "language-models",
    ),
    ProviderSpec(
        "perplexity",
        "Perplexity",
        "https://api.perplexity.ai/router/v1",
        "chat-completions",
        ("pplx-",),
        "high",
        ("Perplexity key prefix",),
        False,
        ("api.perplexity.ai",),
        "compatible",
        "bearer",
    ),
    ProviderSpec(
        "nvidia-nim",
        "NVIDIA NIM",
        "https://integrate.api.nvidia.com/v1",
        "chat-completions",
        ("nvapi-",),
        "high",
        ("NVIDIA NIM key prefix", "OpenAI-compatible NIM endpoint"),
        False,
        ("integrate.api.nvidia.com",),
        "compatible",
        "bearer",
    ),
    ProviderSpec(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        "chat-completions",
        (),
        "unknown",
        (
            "DeepSeek keys use the shared sk- prefix and cannot be distinguished safely from OpenAI by key alone",
            "Connecting sends the key to api.deepseek.com",
        ),
        True,
        ("api.deepseek.com",),
        "compatible",
        "bearer",
    ),
    ProviderSpec(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "openai-responses",
        ("sk-",),
        "medium",
        (
            "This key prefix may also be used by compatible providers",
            "Connecting sends the key to api.openai.com",
        ),
        True,
        ("api.openai.com",),
        "compatible",
        "bearer",
    ),
)

_PROVIDER_BY_KIND = {spec.provider_kind.casefold(): spec for spec in PROVIDER_SPECS}
_PROVIDER_BY_HOST = {hostname.casefold(): spec for spec in PROVIDER_SPECS for hostname in spec.native_hosts}


def provider_spec(provider_kind: str) -> ProviderSpec | None:
    """Return a built-in provider definition by its stable identifier."""

    if not isinstance(provider_kind, str):
        return None
    return _PROVIDER_BY_KIND.get(provider_kind.casefold())


def provider_choices() -> tuple[dict[str, object], ...]:
    """Return safe, non-secret provider options for the desktop connection UI."""

    built_in = tuple(
        {
            "provider_kind": spec.provider_kind,
            "provider_label": spec.label,
            "endpoint": spec.endpoint,
            "protocol": spec.protocol,
            "requires_endpoint": False,
        }
        for spec in PROVIDER_SPECS
    )
    custom = {
        "provider_kind": CUSTOM_PROVIDER_KIND,
        "provider_label": "Custom OpenAI-compatible",
        "endpoint": None,
        "protocol": "chat-completions",
        "requires_endpoint": True,
    }
    return (*built_in, custom)


def provider_spec_for_endpoint(provider_kind: str, hostname: str) -> ProviderSpec | None:
    """Return provider-native behavior only for an explicitly recognized host."""

    spec = provider_spec(provider_kind)
    if spec is None or not isinstance(hostname, str):
        return None
    return spec if hostname.casefold() in spec.native_hosts else None


def provider_spec_for_host(hostname: str) -> ProviderSpec | None:
    """Identify a registered provider only from an explicitly selected exact host."""

    if not isinstance(hostname, str):
        return None
    return _PROVIDER_BY_HOST.get(hostname.casefold())


def key_prefix_specs() -> tuple[tuple[str, ProviderSpec], ...]:
    """Return key hints longest-first so broad prefixes cannot shadow specific ones."""

    entries = [(prefix, spec) for spec in PROVIDER_SPECS for prefix in spec.key_prefixes]
    return tuple(sorted(entries, key=lambda entry: len(entry[0]), reverse=True))


__all__ = [
    "CUSTOM_PROVIDER_KIND",
    "PROVIDER_SPECS",
    "CatalogStrategy",
    "DiscoveryAuth",
    "ProviderSpec",
    "key_prefix_specs",
    "provider_choices",
    "provider_spec",
    "provider_spec_for_endpoint",
    "provider_spec_for_host",
]
