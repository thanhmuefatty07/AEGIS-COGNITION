from __future__ import annotations

import pytest

from core.python.aegis.cache_economics import (
    CachePromptPlan,
    CachePricing,
    EconomicContextCompaction,
    estimate_cache_cost,
    extract_cache_usage,
    stable_prefix_hash,
)


def test_cache_usage_normalizes_openai_anthropic_and_gemini_shapes() -> None:
    openai = extract_cache_usage(
        {"usage": {"prompt_tokens": 120, "prompt_tokens_details": {"cached_tokens": 80}, "completion_tokens": 10}}
    )
    anthropic = extract_cache_usage(
        {"usage": {"input_tokens": 120, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 120}}
    )
    gemini = extract_cache_usage({"usage_metadata": {"prompt_token_count": 120, "cached_content_token_count": 80}})
    assert (openai.input_tokens, openai.cached_read_tokens, openai.output_tokens) == (120, 80, 10)
    assert (anthropic.cached_read_tokens, anthropic.cache_write_tokens) == (80, 120)
    assert gemini.cached_read_tokens == 80

    class Usage:
        prompt_tokens = 120
        cached_tokens = 80
        completion_tokens = 10

    assert extract_cache_usage({"usage": Usage()}).cached_read_tokens == 80


def test_cache_economics_exposes_break_even_and_separates_uncached_cost() -> None:
    pricing = CachePricing(
        provider="test",
        model="model",
        input_usd_per_million=10.0,
        cached_input_usd_per_million=1.0,
        cache_write_usd_per_million=12.0,
        storage_usd_per_million_hour=0.5,
        ttl_hours=1.0,
    )
    estimate = estimate_cache_cost(pricing, prefix_tokens=1_000, expected_requests=3, expected_cached_reads=2)
    assert estimate.uncached_usd == 0.03
    assert estimate.cached_usd == 0.0145
    assert estimate.savings_usd == pytest.approx(0.0155)
    assert estimate.break_even_requests == 2
    assert estimate.cache_worthwhile


def test_unknown_provider_pricing_does_not_claim_savings() -> None:
    estimate = estimate_cache_cost(
        CachePricing("unknown", "model", 1.0, None),
        prefix_tokens=10_000,
        expected_requests=4,
    )
    assert estimate.cached_usd == estimate.uncached_usd
    assert not estimate.cache_worthwhile
    assert estimate.break_even_requests is None


def test_economic_compaction_keeps_user_session() -> None:
    decision = EconomicContextCompaction(
        CachePricing("provider", "model", 10.0, 1.0, output_usd_per_million=30.0),
    ).decide(
        current_context_tokens=100_000,
        future_requests=4,
        compaction_input_tokens=2_000,
        compaction_output_tokens=1_000,
        cache_reads_after_compaction=2,
    )
    assert decision.compact
    assert decision.preserve_session


def test_economic_compaction_accounts_for_replaying_the_compacted_context() -> None:
    decision = EconomicContextCompaction(
        CachePricing("provider", "model", 1.0, None, output_usd_per_million=1.0)
    ).decide(
        current_context_tokens=100_000,
        future_requests=2,
        compaction_input_tokens=100_000,
        compaction_output_tokens=50_000,
    )
    assert not decision.compact
    assert decision.replay_cost_usd == pytest.approx(0.20)
    assert decision.compaction_cost_usd == pytest.approx(0.25)


def test_economic_compaction_includes_cache_write_and_storage_costs() -> None:
    decision = EconomicContextCompaction(
        CachePricing(
            "provider",
            "model",
            1.0,
            0.0,
            cache_write_usd_per_million=100.0,
            storage_usd_per_million_hour=100.0,
            ttl_hours=1.0,
            output_usd_per_million=0.0,
        )
    ).decide(
        current_context_tokens=100_000,
        future_requests=2,
        compaction_input_tokens=100_000,
        compaction_output_tokens=50_000,
        cache_reads_after_compaction=1,
    )
    assert not decision.compact
    assert decision.replay_cost_usd == pytest.approx(0.20)
    assert decision.compaction_cost_usd == pytest.approx(10.10)


def test_economic_compaction_requires_output_price_and_integer_request_count() -> None:
    compactor = EconomicContextCompaction(CachePricing("provider", "model", 1.0, None))
    decision = compactor.decide(
        current_context_tokens=100_000,
        future_requests=2,
        compaction_input_tokens=100_000,
        compaction_output_tokens=50_000,
    )
    assert not decision.compact
    assert decision.compaction_cost_usd is None
    assert "output-token pricing" in decision.reason
    no_cache_price = EconomicContextCompaction(
        CachePricing("provider", "model", 1.0, None, output_usd_per_million=1.0)
    ).decide(
        current_context_tokens=100_000,
        future_requests=2,
        compaction_input_tokens=100_000,
        compaction_output_tokens=50_000,
        cache_reads_after_compaction=1,
    )
    assert not no_cache_price.compact
    assert no_cache_price.compaction_cost_usd is None
    assert "cache-read pricing" in no_cache_price.reason
    with pytest.raises(ValueError, match="future_requests must be a non-negative integer"):
        compactor.decide(
            current_context_tokens=100_000,
            future_requests=1.5,
            compaction_input_tokens=100_000,
            compaction_output_tokens=50_000,
        )
    with pytest.raises(ValueError, match="requests after the initial write"):
        EconomicContextCompaction(CachePricing("provider", "model", 1.0, 0.0, output_usd_per_million=1.0)).decide(
            current_context_tokens=100_000,
            future_requests=2,
            compaction_input_tokens=100_000,
            compaction_output_tokens=50_000,
            cache_reads_after_compaction=2,
        )


def test_stable_prefix_hash_changes_when_model_or_prefix_changes() -> None:
    first = stable_prefix_hash("provider", "model-a", "stable system")
    assert first == stable_prefix_hash("provider", "model-a", "stable system")
    assert first != stable_prefix_hash("provider", "model-b", "stable system")
    assert first != stable_prefix_hash("provider", "model-a", "changed system")


def test_cache_prompt_plan_keeps_key_stable_across_dynamic_tasks() -> None:
    first = CachePromptPlan.from_parts(
        provider="openai",
        model="gpt-5.6",
        namespace="user-1",
        stable_prefix="rules\r\n\r\n tools",
        dynamic_suffix="task one",
        prompt_cache_ttl="30m",
    )
    second = CachePromptPlan.from_parts(
        provider="openai",
        model="gpt-5.6",
        namespace="user-1",
        stable_prefix="rules\n\n tools",
        dynamic_suffix="task two",
        prompt_cache_ttl="30m",
    )
    assert first.stable_prefix == second.stable_prefix
    assert first.prefix_hash == second.prefix_hash
    assert first.cache_key == second.cache_key
    assert first.request_options(provider="openai", model="gpt-5.6", supports_controls=True) == {
        "prompt_cache_key": first.cache_key,
        "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
    }
    openrouter_options = first.request_options(
        provider="openrouter",
        model="openai/gpt-5.6",
        supports_controls=True,
    )
    assert openrouter_options == {
        "prompt_cache_key": CachePromptPlan.from_parts(
            provider="openrouter",
            model="openai/gpt-5.6",
            namespace="user-1",
            stable_prefix="rules\n\n tools",
            dynamic_suffix="different task",
        ).cache_key,
    }
    assert first.request_options(provider="anthropic", model="claude", supports_controls=True) == {}
    assert first.request_options(provider="openai", model="gpt-5.6", supports_controls=False) == {}


def test_cache_prompt_plan_isolated_by_namespace() -> None:
    first = CachePromptPlan.from_parts(
        provider="openai",
        model="model",
        namespace="user-1",
        stable_prefix="shared rules",
    )
    second = CachePromptPlan.from_parts(
        provider="openai",
        model="model",
        namespace="user-2",
        stable_prefix="shared rules",
    )
    assert first.prefix_hash != second.prefix_hash
    assert first.cache_key != second.cache_key
