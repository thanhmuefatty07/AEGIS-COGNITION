"""Provider-neutral prompt-cache accounting and session guidance.

The provider cache is an optimization owned by the remote model service.  It
is not treated as durable memory, an authoritative result, or a guarantee of
reuse.  This module only records usage returned by a provider and makes
bounded economic decisions from an explicitly supplied price table.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast


CACHE_ECONOMICS_SCHEMA_V1 = "aegis-cache-economics-v1"
CACHE_PROMPT_SCHEMA_V1 = "aegis-cache-prompt-v1"
_VALID_CACHE_RETENTIONS = frozenset({"30m", "24h", "in_memory"})


def _nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _nonnegative_float(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


@dataclass(frozen=True, slots=True)
class CachePricing:
    """Price inputs for one provider/model route.

    Prices are USD per one million tokens.  They are deliberately supplied by
    the caller instead of hard-coded: provider prices, promotions, regions,
    service tiers, and model aliases change independently of this package.
    """

    provider: str
    model: str
    input_usd_per_million: float
    cached_input_usd_per_million: float | None
    cache_write_usd_per_million: float | None = None
    storage_usd_per_million_hour: float = 0.0
    ttl_hours: float | None = None
    minimum_prefix_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("cache pricing provider and model must be non-empty")
        _nonnegative_float(self.input_usd_per_million, "input_usd_per_million")
        if self.cached_input_usd_per_million is not None:
            _nonnegative_float(self.cached_input_usd_per_million, "cached_input_usd_per_million")
        if self.cache_write_usd_per_million is not None:
            _nonnegative_float(self.cache_write_usd_per_million, "cache_write_usd_per_million")
        _nonnegative_float(self.storage_usd_per_million_hour, "storage_usd_per_million_hour")
        if self.ttl_hours is not None:
            _nonnegative_float(self.ttl_hours, "ttl_hours")
        _nonnegative_int(self.minimum_prefix_tokens, "minimum_prefix_tokens")

    @property
    def supports_prompt_cache(self) -> bool:
        return self.cached_input_usd_per_million is not None


@dataclass(frozen=True, slots=True)
class CacheUsage:
    """Provider-reported token counters, normalized across SDKs."""

    input_tokens: int = 0
    cached_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "cached_read_tokens", "cache_write_tokens", "output_tokens"):
            _nonnegative_int(getattr(self, name), name)

    @property
    def cache_status(self) -> str:
        if self.cached_read_tokens:
            return "HIT"
        if self.cache_write_tokens:
            return "WRITE"
        return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CacheObservation:
    """Non-authoritative observation attached to a single model response."""

    provider: str
    model: str | None
    prefix_hash: str | None
    usage: CacheUsage
    observed_at_ms: int

    @classmethod
    def now(
        cls,
        *,
        provider: str,
        model: str | None,
        prefix_hash: str | None,
        usage: CacheUsage,
    ) -> CacheObservation:
        if not provider.strip():
            raise ValueError("cache observation provider must be non-empty")
        if prefix_hash is not None and (len(prefix_hash) != 64 or any(c not in "0123456789abcdef" for c in prefix_hash)):
            raise ValueError("cache observation prefix_hash must be a lowercase SHA-256 digest")
        return cls(provider, model, prefix_hash, usage, int(time.time() * 1000))


def _canonical_prompt_text(value: str, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    # Preserve prompt meaning while removing platform-specific line-ending
    # churn, which would otherwise create needless cache misses.
    return value.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class CachePromptPlan:
    """A cache-first prompt split into stable prefix and changing suffix.

    The stable prefix is the only part used to derive the cache identity.  The
    dynamic suffix is retained for diagnostics and is never mixed into that
    identity.  Provider controls are emitted only for an adapter that opts in;
    stable prompt ordering remains the provider-neutral fallback.
    """

    schema: str
    provider: str
    model: str
    namespace: str
    stable_prefix: str
    dynamic_suffix: str
    prefix_hash: str
    cache_key: str
    prompt_cache_ttl: str | None = None

    def __post_init__(self) -> None:
        if self.schema != CACHE_PROMPT_SCHEMA_V1:
            raise ValueError("unsupported cache prompt schema")
        for name in ("provider", "model", "namespace"):
            if not getattr(self, name).strip():
                raise ValueError(f"cache prompt {name} must be non-empty")
        if len(self.namespace) > 256:
            raise ValueError("cache prompt namespace is too long")
        if len(self.prefix_hash) != 64 or any(c not in "0123456789abcdef" for c in self.prefix_hash):
            raise ValueError("cache prompt prefix_hash must be a lowercase SHA-256 digest")
        if not self.cache_key.startswith("aegis-cache-"):
            raise ValueError("cache prompt cache_key is invalid")
        if self.prompt_cache_ttl is not None and self.prompt_cache_ttl not in _VALID_CACHE_RETENTIONS:
            raise ValueError("cache prompt TTL must be 30m, 24h, or in_memory")

    @classmethod
    def from_parts(
        cls,
        *,
        provider: str,
        model: str | None,
        namespace: str,
        stable_prefix: str,
        dynamic_suffix: str = "",
        prompt_cache_ttl: str | None = None,
    ) -> CachePromptPlan:
        normalized_provider = _canonical_prompt_text(provider, "provider").strip().lower()
        normalized_model = _canonical_prompt_text(model or "unknown", "model").strip()
        normalized_namespace = _canonical_prompt_text(namespace, "namespace").strip()
        normalized_prefix = _canonical_prompt_text(stable_prefix, "stable_prefix")
        normalized_suffix = _canonical_prompt_text(dynamic_suffix, "dynamic_suffix")
        prefix_hash = stable_prefix_hash(
            normalized_provider,
            normalized_model,
            normalized_namespace,
            normalized_prefix,
        )
        return cls(
            schema=CACHE_PROMPT_SCHEMA_V1,
            provider=normalized_provider,
            model=normalized_model,
            namespace=normalized_namespace,
            stable_prefix=normalized_prefix,
            dynamic_suffix=normalized_suffix,
            prefix_hash=prefix_hash,
            cache_key=f"aegis-cache-{prefix_hash[:40]}",
            prompt_cache_ttl=prompt_cache_ttl,
        )

    def for_provider(self, provider: str, model: str | None = None) -> CachePromptPlan:
        """Bind the same safe prefix to the provider actually selected."""

        return CachePromptPlan.from_parts(
            provider=provider,
            model=model or self.model,
            namespace=self.namespace,
            stable_prefix=self.stable_prefix,
            dynamic_suffix=self.dynamic_suffix,
            prompt_cache_ttl=self.prompt_cache_ttl,
        )

    def request_options(self, *, provider: str, model: str | None, supports_controls: bool) -> dict[str, object]:
        """Return safe provider hints, or nothing for opaque clients.

        OpenAI-compatible clients can use the stable key for cache routing.
        Retention is sent only when explicitly configured; provider defaults are
        safer than guessing a model-specific TTL.  Anthropic/Gemini adapters
        can consume this plan through their own structured message hooks.
        """

        if not supports_controls:
            return {}
        normalized_provider = provider.strip().lower()
        if normalized_provider not in {"openai", "openai-compatible", "chatgpt-web", "openrouter"}:
            return {}
        bound = self.for_provider(normalized_provider, model)
        options: dict[str, object] = {"prompt_cache_key": bound.cache_key}
        if normalized_provider == "openrouter" or bound.prompt_cache_ttl is None:
            return options
        normalized_model = bound.model.casefold()
        if normalized_model.startswith(("gpt-5.6", "gpt-6")):
            if bound.prompt_cache_ttl != "30m":
                raise ValueError("GPT-5.6+ prompt cache TTL must be 30m")
            options["prompt_cache_options"] = {"mode": "implicit", "ttl": bound.prompt_cache_ttl}
        elif normalized_model.startswith(("gpt-5.5", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-4.1")):
            if bound.prompt_cache_ttl not in {"24h", "in_memory"}:
                raise ValueError("legacy OpenAI prompt cache retention must be 24h or in_memory")
            options["prompt_cache_retention"] = bound.prompt_cache_ttl
        return options


@dataclass(frozen=True, slots=True)
class CacheCostEstimate:
    """Separate cost arithmetic; it intentionally contains no capability score."""

    schema: str
    prefix_tokens: int
    expected_requests: int
    expected_cached_reads: int
    expected_cache_writes: int
    uncached_usd: float
    cached_usd: float
    savings_usd: float
    break_even_requests: int | None
    cache_worthwhile: bool
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "prefix_tokens": self.prefix_tokens,
            "expected_requests": self.expected_requests,
            "expected_cached_reads": self.expected_cached_reads,
            "expected_cache_writes": self.expected_cache_writes,
            "uncached_usd": self.uncached_usd,
            "cached_usd": self.cached_usd,
            "savings_usd": self.savings_usd,
            "break_even_requests": self.break_even_requests,
            "cache_worthwhile": self.cache_worthwhile,
            "rationale": self.rationale,
        }


def estimate_cache_cost(
    pricing: CachePricing,
    *,
    prefix_tokens: int,
    expected_requests: int,
    expected_cached_reads: int | None = None,
    expected_cache_writes: int = 1,
    storage_hours: float | None = None,
) -> CacheCostEstimate:
    """Compare the same stable prefix sent uncached vs provider-cache billed.

    The caller supplies the expected hit count because a remote cache hit is
    never guaranteed.  Dynamic suffix/output costs are intentionally excluded:
    they are equal in both scenarios and would hide the cache decision.
    """

    _nonnegative_int(prefix_tokens, "prefix_tokens")
    if expected_requests < 1:
        raise ValueError("expected_requests must be positive")
    _nonnegative_int(expected_cache_writes, "expected_cache_writes")
    if expected_cache_writes > expected_requests:
        raise ValueError("expected_cache_writes cannot exceed expected_requests")
    reads = expected_requests - expected_cache_writes if expected_cached_reads is None else expected_cached_reads
    _nonnegative_int(reads, "expected_cached_reads")
    if reads > expected_requests - expected_cache_writes:
        raise ValueError("expected_cached_reads exceeds requests remaining after writes")
    misses = expected_requests - expected_cache_writes - reads
    hours = pricing.ttl_hours if storage_hours is None else storage_hours
    if hours is None:
        hours = 0.0
    _nonnegative_float(hours, "storage_hours")

    uncached = expected_requests * prefix_tokens * pricing.input_usd_per_million / 1_000_000
    write_rate = pricing.cache_write_usd_per_million
    if write_rate is None:
        write_rate = pricing.input_usd_per_million
    cached_rate = pricing.cached_input_usd_per_million
    if cached_rate is None:
        cached = uncached
        rationale = "provider cache pricing is unavailable; preserve the session but do not assume a saving"
    else:
        cached = (
            expected_cache_writes * prefix_tokens * write_rate
            + reads * prefix_tokens * cached_rate
            + misses * prefix_tokens * pricing.input_usd_per_million
            + prefix_tokens * pricing.storage_usd_per_million_hour * hours
        ) / 1_000_000
        rationale = "stable-prefix cache is worthwhile only when observed or forecast reads cover the write/storage cost"
    savings = uncached - cached
    break_even = _break_even_requests(pricing, prefix_tokens, hours)
    return CacheCostEstimate(
        schema=CACHE_ECONOMICS_SCHEMA_V1,
        prefix_tokens=prefix_tokens,
        expected_requests=expected_requests,
        expected_cached_reads=reads,
        expected_cache_writes=expected_cache_writes,
        uncached_usd=uncached,
        cached_usd=cached,
        savings_usd=savings,
        break_even_requests=break_even,
        cache_worthwhile=bool(pricing.supports_prompt_cache and savings > 0),
        rationale=rationale,
    )


def _break_even_requests(pricing: CachePricing, prefix_tokens: int, storage_hours: float) -> int | None:
    if not pricing.supports_prompt_cache or prefix_tokens <= 0:
        return None
    write_rate = pricing.cache_write_usd_per_million
    if write_rate is None:
        write_rate = pricing.input_usd_per_million
    for requests in range(1, 100_001):
        if requests == 1:
            continue
        uncached = requests * prefix_tokens * pricing.input_usd_per_million / 1_000_000
        cached = (
            prefix_tokens * write_rate
            + (requests - 1) * prefix_tokens * cast(float, pricing.cached_input_usd_per_million)
            + prefix_tokens * pricing.storage_usd_per_million_hour * storage_hours
        ) / 1_000_000
        if uncached - cached > 0:
            return requests
    return None


@dataclass(frozen=True, slots=True)
class ContextCompactionDecision:
    compact: bool
    preserve_session: bool
    reason: str
    replay_cost_usd: float
    compaction_cost_usd: float


class EconomicContextCompaction:
    """Choose compaction from measured economics while keeping the UI session."""

    def __init__(self, pricing: CachePricing, *, safety_margin: float = 0.10) -> None:
        self.pricing = pricing
        self.safety_margin = _nonnegative_float(safety_margin, "safety_margin")

    def decide(
        self,
        *,
        current_context_tokens: int,
        future_requests: int,
        compaction_input_tokens: int,
        compaction_output_tokens: int,
        cache_reads_after_compaction: int = 0,
    ) -> ContextCompactionDecision:
        _nonnegative_int(current_context_tokens, "current_context_tokens")
        if future_requests < 1:
            raise ValueError("future_requests must be positive")
        _nonnegative_int(compaction_input_tokens, "compaction_input_tokens")
        _nonnegative_int(compaction_output_tokens, "compaction_output_tokens")
        _nonnegative_int(cache_reads_after_compaction, "cache_reads_after_compaction")
        if cache_reads_after_compaction > future_requests:
            raise ValueError("cache_reads_after_compaction cannot exceed future_requests")
        replay = future_requests * current_context_tokens * self.pricing.input_usd_per_million / 1_000_000
        compact = (
            compaction_input_tokens * self.pricing.input_usd_per_million
            + compaction_output_tokens * self.pricing.input_usd_per_million
        ) / 1_000_000
        if self.pricing.cached_input_usd_per_million is not None:
            replay -= cache_reads_after_compaction * current_context_tokens * (
                self.pricing.input_usd_per_million - self.pricing.cached_input_usd_per_million
            ) / 1_000_000
        should_compact = replay > compact * (1.0 + self.safety_margin) and current_context_tokens > compaction_output_tokens
        reason = "compact economically; keep the same user-visible session" if should_compact else "retain context; compaction cost is not justified yet"
        return ContextCompactionDecision(should_compact, True, reason, replay, compact)


def stable_prefix_hash(*parts: str) -> str:
    """Hash the stable rendered prefix, not the changing user suffix."""

    if any(type(part) is not str for part in parts):
        raise TypeError("stable prefix parts must be strings")
    encoded = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"aegis-stable-prefix-v1\0" + encoded).hexdigest()


def extract_cache_usage(value: object) -> CacheUsage:
    """Extract common OpenAI, Anthropic, and Gemini usage fields safely."""

    layers = _usage_layers(value)
    input_tokens = _first_counter(layers, ("input_tokens", "prompt_tokens", "prompt_token_count"))
    cached_read = _first_counter(
        layers,
        ("cached_tokens", "cache_read_input_tokens", "cached_content_token_count", "cache_read_tokens"),
    )
    cache_write = _first_counter(
        layers,
        ("cache_creation_input_tokens", "cache_write_input_tokens", "cache_creation_tokens", "cache_write_tokens"),
    )
    output_tokens = _first_counter(layers, ("output_tokens", "completion_tokens", "candidates_token_count"))
    return CacheUsage(input_tokens, cached_read, cache_write, output_tokens)


def _usage_layers(value: object, *, max_depth: int = 3, max_layers: int = 32) -> tuple[Mapping[str, object], ...]:
    queue: list[tuple[object, int]] = [(value, 0)]
    seen: set[int] = set()
    layers: list[Mapping[str, object]] = []
    while queue and len(layers) < max_layers:
        candidate, depth = queue.pop(0)
        marker = id(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, object], candidate)
            layers.append(mapping)
            if depth < max_depth:
                for key in (
                    "usage",
                    "usage_metadata",
                    "response_metadata",
                    "metadata",
                    "prompt_tokens_details",
                    "input_token_details",
                    "input_tokens_details",
                ):
                    nested = mapping.get(key)
                    if isinstance(nested, Mapping) or nested is not None:
                        queue.append((nested, depth + 1))
        elif depth < max_depth:
            direct: dict[str, object] = {}
            for key in (
                "input_tokens",
                "prompt_tokens",
                "prompt_token_count",
                "cached_tokens",
                "cache_read_input_tokens",
                "cached_content_token_count",
                "cache_creation_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "completion_tokens",
                "candidates_token_count",
            ):
                nested_value = getattr(candidate, key, None)
                if nested_value is not None:
                    direct[key] = nested_value
            if direct:
                layers.append(direct)
            for key in (
                "usage",
                "usage_metadata",
                "response_metadata",
                "metadata",
                "prompt_tokens_details",
                "input_token_details",
                "input_tokens_details",
            ):
                nested = getattr(candidate, key, None)
                if isinstance(nested, Mapping) or nested is not None:
                    queue.append((nested, depth + 1))
    return tuple(layers)


def _first_counter(layers: Sequence[Mapping[str, object]], names: tuple[str, ...]) -> int:
    for layer in layers:
        for name in names:
            value = layer.get(name)
            if type(value) is int and value >= 0:
                return value
    return 0


__all__ = [
    "CACHE_ECONOMICS_SCHEMA_V1",
    "CACHE_PROMPT_SCHEMA_V1",
    "CacheCostEstimate",
    "CacheObservation",
    "CachePricing",
    "CachePromptPlan",
    "CacheUsage",
    "ContextCompactionDecision",
    "EconomicContextCompaction",
    "estimate_cache_cost",
    "extract_cache_usage",
    "stable_prefix_hash",
]
