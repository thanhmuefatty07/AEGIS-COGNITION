"""Exact provider reasoning support for model catalogs and request adapters.

Unknown model IDs stay Auto-only. Extend these profiles only when the
provider's current model-specific API documentation and wire behavior agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


AnthropicThinkingMode = Literal["adaptive", "effort", "budgeted"]
_REASONING_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_REASONING_VALUE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z", re.ASCII)


def is_reasoning_effort_value(value: object) -> bool:
    """Accept only bounded provider enum identifiers, never arbitrary payload text."""

    return (
        isinstance(value, str)
        and _REASONING_VALUE_PATTERN.fullmatch(value) is not None
        and value.casefold() not in {"auto", "declared", "inferred"}
    )


def reasoning_efforts_from_capabilities(capabilities: object) -> tuple[str, ...]:
    """Extract exact advertised enum values from bounded capability markers."""

    if not isinstance(capabilities, tuple | list):
        return ()
    declared: dict[str, str] = {}
    for capability in capabilities:
        if not isinstance(capability, str) or not capability.casefold().startswith("reasoning:"):
            continue
        value = capability[len("reasoning:") :]
        if is_reasoning_effort_value(value):
            declared.setdefault(value.casefold(), value)
    ordered = [declared.pop(level) for level in _REASONING_ORDER if level in declared]
    ordered.extend(sorted(declared.values(), key=lambda value: (value.casefold(), value)))
    return tuple(ordered)


@dataclass(frozen=True)
class ProviderReasoningProfile:
    efforts: tuple[str, ...]
    anthropic_thinking_mode: AnthropicThinkingMode | None = None


_OPENAI_BASIC = ("low", "medium", "high")
_OPENAI_WITH_NONE = ("none", *_OPENAI_BASIC)
_OPENAI_EXTENDED = (*_OPENAI_WITH_NONE, "xhigh")
_OPENAI_MAX = (*_OPENAI_EXTENDED, "max")
_OPENAI_GPT5 = ("minimal", "low", "medium", "high")

_OPENAI_PROFILES = {
    "gpt-5": ProviderReasoningProfile(_OPENAI_GPT5),
    "gpt-5.1": ProviderReasoningProfile(_OPENAI_WITH_NONE),
    "gpt-5.2": ProviderReasoningProfile(_OPENAI_EXTENDED),
    "gpt-5.3-codex": ProviderReasoningProfile(("low", "medium", "high", "xhigh")),
    "gpt-5.4": ProviderReasoningProfile(_OPENAI_EXTENDED),
    "gpt-5.4-mini": ProviderReasoningProfile(_OPENAI_EXTENDED),
    "gpt-5.5": ProviderReasoningProfile(_OPENAI_EXTENDED),
    "gpt-5.6": ProviderReasoningProfile(_OPENAI_MAX),
    "gpt-5.6-sol": ProviderReasoningProfile(_OPENAI_MAX),
    "gpt-5.6-terra": ProviderReasoningProfile(_OPENAI_MAX),
    "gpt-5.6-luna": ProviderReasoningProfile(_OPENAI_MAX),
}

_GOOGLE_PROFILES = {
    "gemini-2.5-flash": ProviderReasoningProfile(("none", "minimal", "low", "medium", "high")),
    "gemini-2.5-flash-lite": ProviderReasoningProfile(("none", "minimal", "low", "medium", "high")),
    "gemini-2.5-pro": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3.1-pro": ProviderReasoningProfile(("low", "medium", "high")),
    "gemini-3.1-pro-preview": ProviderReasoningProfile(("low", "medium", "high")),
    "gemini-3.1-flash-lite": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3.1-flash-lite-image": ProviderReasoningProfile(("minimal", "high")),
    "gemini-3.5-flash": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3.5-flash-lite": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3.6-flash": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3.7-flash": ProviderReasoningProfile(_OPENAI_BASIC),
    "gemini-3.8-flash": ProviderReasoningProfile(_OPENAI_BASIC),
    "gemini-3-flash": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3-flash-preview": ProviderReasoningProfile(("minimal", "low", "medium", "high")),
    "gemini-3-pro-preview": ProviderReasoningProfile(("low", "high")),
}

_GROQ_PROFILES = {
    "openai/gpt-oss-20b": ProviderReasoningProfile(("low", "medium", "high")),
    "openai/gpt-oss-120b": ProviderReasoningProfile(("low", "medium", "high")),
    "qwen/qwen3.8-27b": ProviderReasoningProfile(("none", "low", "medium", "high")),
}

_XAI_PROFILES = {
    "grok-4.5": ProviderReasoningProfile(("low", "medium", "high")),
    "grok-4.6": ProviderReasoningProfile(("low", "medium", "high", "xhigh")),
    "grok-4.7": ProviderReasoningProfile(("low", "medium", "high", "xhigh")),
}

_DEEPSEEK_PROFILES = {
    "deepseek-flash": ProviderReasoningProfile(("none", "low", "high", "max")),
    "deepseek-v4-flash": ProviderReasoningProfile(("none", "low", "high", "max")),
    "deepseek-v4-flash-vision-exp": ProviderReasoningProfile(("none", "low", "high", "max")),
    "deepseek-v4-pro": ProviderReasoningProfile(("none", "low", "high", "max")),
}

# The hosted NIM catalog lists model IDs but not each model's effort enum.
# Keep only model-specific values from NVIDIA's inference schemas; unknown IDs
# remain Auto-only instead of inheriting a guessed family-wide configuration.
_NVIDIA_NIM_PROFILES = {
    # https://docs.api.nvidia.com/nim/reference/openai-gpt-oss-20b-infer
    "openai/gpt-oss-20b": ProviderReasoningProfile(("low", "medium", "high")),
    # https://docs.api.nvidia.com/nim/reference/openai-gpt-oss-120b-infer
    "openai/gpt-oss-120b": ProviderReasoningProfile(("low", "medium", "high")),
    # https://docs.api.nvidia.com/nim/reference/meta-muse-glimmer-30b-infer
    "meta/muse-glimmer-30b": ProviderReasoningProfile(("none", "minimal", "low", "medium", "high", "max")),
    # https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k3-infer
    "moonshotai/kimi-k3": ProviderReasoningProfile(("low", "high", "max")),
    # https://docs.api.nvidia.com/nim/reference/mistralai-mistral-small-4-119b-2603-infer
    "mistralai/mistral-small-4-119b-2603": ProviderReasoningProfile(("none", "high")),
    # https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer
    "nvidia/nemotron-3-super-120b-a12b": ProviderReasoningProfile(("none", "low", "high")),
    # https://docs.api.nvidia.com/nim/reference/z-ai-glm-5-3
    "z-ai/glm-5.3": ProviderReasoningProfile(("low", "high", "max")),
    # https://docs.api.nvidia.com/nim/reference/z-ai-glm-5-3-flash
    "z-ai/glm-5.3-flash": ProviderReasoningProfile(("low", "high", "max")),
    # https://docs.api.nvidia.com/nim/reference/deepseek-ai-deepseek-v4-flash-infer
    "deepseek-ai/deepseek-v4-flash": ProviderReasoningProfile(("none", "high", "max")),
}

_CLAUDE_BASE = ("low", "medium", "high")
_CLAUDE_ALL = (*_CLAUDE_BASE, "xhigh", "max")
_CLAUDE_MAX = (*_CLAUDE_BASE, "max")
_CLAUDE_EXTENDED = (*_CLAUDE_BASE, "xhigh", "max")

_ANTHROPIC_PROFILES = {
    "claude-opus-4-5": ProviderReasoningProfile(_CLAUDE_BASE, "budgeted"),
    "claude-opus-4-6": ProviderReasoningProfile(_CLAUDE_MAX, "adaptive"),
    "claude-opus-4-7": ProviderReasoningProfile(_CLAUDE_EXTENDED, "adaptive"),
    "claude-opus-4-8": ProviderReasoningProfile(_CLAUDE_EXTENDED, "adaptive"),
    "claude-sonnet-4-6": ProviderReasoningProfile(_CLAUDE_MAX, "adaptive"),
    "claude-opus-5": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-opus-5-5": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-sonnet-5": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-fable-5": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-fable-5-1": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-mythos-5": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-mythos-5-1": ProviderReasoningProfile(_CLAUDE_ALL, "effort"),
    "claude-mythos-preview": ProviderReasoningProfile(_CLAUDE_MAX, "effort"),
}


def provider_reasoning_profile(
    provider_kind: str,
    model_id: str,
    endpoint: str | None,
    capabilities: tuple[str, ...] = (),
) -> ProviderReasoningProfile | None:
    """Return a source-backed profile only for known first-party model IDs."""

    if not isinstance(provider_kind, str) or not isinstance(model_id, str):
        return None
    host = (urlsplit(endpoint).hostname or "").casefold() if isinstance(endpoint, str) else ""
    provider = provider_kind.casefold()
    lowered_model = model_id.casefold()
    normalized_capabilities = {item.casefold() for item in capabilities if isinstance(item, str)}
    if "reasoning:declared" in normalized_capabilities:
        declared_efforts = reasoning_efforts_from_capabilities(capabilities)
        if provider == "anthropic" and host == "api.anthropic.com":
            profile = _lookup_profile(_ANTHROPIC_PROFILES, lowered_model, r"-\d{8}")
            mode = (
                "adaptive"
                if "thinking:adaptive" in normalized_capabilities
                else "budgeted"
                if "thinking:enabled" in normalized_capabilities
                else "effort"
                if "thinking:effort" in normalized_capabilities
                else profile.anthropic_thinking_mode
                if profile is not None
                else None
            )
            if mode == "budgeted":
                declared_efforts = tuple(level for level in declared_efforts if level in _CLAUDE_BASE)
            return ProviderReasoningProfile(declared_efforts, mode)
        if provider == "openai" and host == "api.openai.com":
            return ProviderReasoningProfile(declared_efforts)
        if provider == "xai" and host == "api.x.ai":
            return ProviderReasoningProfile(declared_efforts)
        if provider == "deepseek" and host == "api.deepseek.com":
            return ProviderReasoningProfile(declared_efforts)
    if provider == "openai" and host == "api.openai.com":
        return _lookup_profile(_OPENAI_PROFILES, lowered_model, r"-\d{4}-\d{2}-\d{2}")
    if provider == "google-gemini" and host == "generativelanguage.googleapis.com":
        canonical = re.sub(r"-preview-\d{2}-(?:\d{2}|\d{4})$", "-preview", lowered_model)
        return _GOOGLE_PROFILES.get(canonical)
    if provider == "groq" and host == "api.groq.com":
        return _GROQ_PROFILES.get(lowered_model)
    if provider == "xai" and host == "api.x.ai":
        return _XAI_PROFILES.get(lowered_model)
    if provider == "deepseek" and host == "api.deepseek.com":
        return _DEEPSEEK_PROFILES.get(lowered_model)
    if provider == "nvidia-nim" and host == "integrate.api.nvidia.com":
        return _NVIDIA_NIM_PROFILES.get(lowered_model)
    if provider == "anthropic" and host == "api.anthropic.com":
        profile = _lookup_profile(_ANTHROPIC_PROFILES, lowered_model, r"-\d{8}")
        markers = normalized_capabilities
        mode = (
            "adaptive"
            if "thinking:adaptive" in markers
            else "budgeted"
            if "thinking:enabled" in markers
            else "effort"
            if "thinking:effort" in markers
            else None
        )
        if "reasoning:declared" in markers:
            efforts = reasoning_efforts_from_capabilities(capabilities)
            if mode == "budgeted":
                efforts = tuple(level for level in efforts if level in _CLAUDE_BASE)
            return ProviderReasoningProfile(efforts, mode)
        if "thinking:declared" in markers:
            efforts = profile.efforts if profile is not None and mode is not None else ()
            return ProviderReasoningProfile(efforts, mode)
        return profile
    return None


def _lookup_profile(
    profiles: dict[str, ProviderReasoningProfile],
    model_id: str,
    snapshot_suffix: str,
) -> ProviderReasoningProfile | None:
    exact = profiles.get(model_id)
    if exact is not None:
        return exact
    for alias in sorted(profiles, key=len, reverse=True):
        if model_id.startswith(f"{alias}-") and re.fullmatch(snapshot_suffix, model_id[len(alias) :]):
            return profiles[alias]
    return None


__all__ = [
    "ProviderReasoningProfile",
    "is_reasoning_effort_value",
    "provider_reasoning_profile",
    "reasoning_efforts_from_capabilities",
]
