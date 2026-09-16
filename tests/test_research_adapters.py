from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping

import pytest

from aegis_cognition.lab import SearchProgram, SearchProgramExecutor
from aegis_cognition.research_adapters import (
    PublicResearchRouter,
    RedditRssQueryProvider,
    ResearchSourceUnavailable,
    XAppOnlyQueryProvider,
    build_public_research_handler,
)
from aegis_cognition.subagents import AgentMessage, AgentTaskContext


def _fetcher(payload: bytes, calls: list[tuple[str, Mapping[str, str]]]):
    def fetch(url: str, headers: Mapping[str, str], _: float, __: int) -> bytes:
        calls.append((url, headers))
        return payload

    return fetch


def _reddit_feed() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_one</id>
    <title>First public post</title>
    <updated>2026-09-16T00:00:00Z</updated>
    <author><name>author-one</name></author>
    <link rel="alternate" href="https://www.reddit.com/r/artificial/comments/one/first/" />
    <content type="html">Evidence item one</content>
  </entry>
  <entry>
    <id>t3_two</id>
    <title>Second public post</title>
    <link rel="alternate" href="https://www.reddit.com/r/artificial/comments/two/second/" />
    <summary>Evidence item two</summary>
  </entry>
</feed>"""


def _x_response() -> bytes:
    return json.dumps(
        {
            "data": [
                {
                    "id": "123456",
                    "text": "Public X observation",
                    "created_at": "2026-09-16T00:00:00.000Z",
                    "author_id": "42",
                    "public_metrics": {"like_count": 3},
                }
            ]
        }
    ).encode()


async def test_reddit_rss_is_credential_free_and_integrates_with_search_program() -> None:
    calls: list[tuple[str, Mapping[str, str]]] = []
    provider = RedditRssQueryProvider(fetcher=_fetcher(_reddit_feed(), calls), max_results=2)
    assert provider.capabilities["login_required"] is False
    assert provider.capabilities["user_cookies_used"] is False

    program = SearchProgram.from_mappings(
        [{"kind": "query", "text": "agent coordination"}],
        allowed_hosts=("www.reddit.com",),
        provider="reddit-rss-public",
        max_candidates=2,
    )
    result = await SearchProgramExecutor(query_provider=provider).execute(program)

    assert [item["source_id"] for item in result] == ["t3_one", "t3_two"]
    assert "q=agent+coordination" in calls[0][0]
    assert "Authorization" not in calls[0][1]


async def test_x_requires_system_app_token_and_does_not_fallback_to_login_or_cookies() -> None:
    calls: list[tuple[str, Mapping[str, str]]] = []
    missing = XAppOnlyQueryProvider(fetcher=_fetcher(_x_response(), calls))
    with pytest.raises(ResearchSourceUnavailable, match="system bearer token"):
        await missing("agent research")
    assert calls == []

    provider = XAppOnlyQueryProvider(
        bearer_token="system-token",
        fetcher=_fetcher(_x_response(), calls),
    )
    result = await provider("agent research")
    assert result[0]["source_id"] == "123456"
    assert result[0]["content"] == "Public X observation"
    assert calls[0][1]["Authorization"] == "Bearer system-token"
    assert provider.capabilities["user_context"] is False


async def test_router_routes_explicit_sources_and_runs_all_sources_concurrently() -> None:
    reddit_calls: list[tuple[str, Mapping[str, str]]] = []
    x_calls: list[tuple[str, Mapping[str, str]]] = []
    router = PublicResearchRouter(
        reddit=RedditRssQueryProvider(fetcher=_fetcher(_reddit_feed(), reddit_calls), max_results=1),
        x=XAppOnlyQueryProvider(bearer_token="system-token", fetcher=_fetcher(_x_response(), x_calls)),
    )

    reddit_result, x_result, all_result = await asyncio.gather(
        router("reddit:public agents"),
        router("x:public agents"),
        router("all:public agents"),
    )
    assert reddit_result[0]["provenance_cluster"] == "reddit"
    assert x_result[0]["provenance_cluster"] == "x"
    assert {item["provenance_cluster"] for item in all_result} == {"reddit", "x"}
    assert len(reddit_calls) == 2
    assert len(x_calls) == 2


async def test_router_ignores_unconfigured_x_for_unprefixed_public_queries() -> None:
    calls: list[tuple[str, Mapping[str, str]]] = []
    router = PublicResearchRouter(
        reddit=RedditRssQueryProvider(fetcher=_fetcher(_reddit_feed(), calls), max_results=1),
        x=XAppOnlyQueryProvider(fetcher=_fetcher(_x_response(), [])),
    )

    result = await router("public agents")

    assert result[0]["provenance_cluster"] == "reddit"
    assert len(calls) == 1


async def test_router_does_not_silently_downgrade_all_to_one_source() -> None:
    router = PublicResearchRouter(
        reddit=RedditRssQueryProvider(fetcher=_fetcher(_reddit_feed(), []), max_results=1),
        x=XAppOnlyQueryProvider(fetcher=_fetcher(_x_response(), [])),
    )

    with pytest.raises(ResearchSourceUnavailable, match="both Reddit and X"):
        await router("all:public agents")


async def test_public_research_handler_returns_bounded_claims_to_the_root() -> None:
    async def provider(_: str, **__: object) -> list[dict[str, object]]:
        return [
            {
                "uri": "https://www.reddit.com/r/artificial/comments/one/first/",
                "source_id": "t3_one",
                "content": "public evidence",
            }
        ]

    handler = build_public_research_handler(provider, max_candidates=1)
    request = AgentMessage(
        schema="aegis-agent-message-v1",
        message_kind="TASK_REQUEST",
        run_id="run-research-handler",
        sender_id="root",
        recipient_id="subagent:1",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        idempotency_key="run-research-handler:1:1:request",
        payload={"role": "researcher", "prompt": "reddit: agent coordination"},
        token_budget=None,
        deadline_ms=1_000,
    )
    result = await handler(AgentTaskContext(request=request, dependency_results=()))

    assert result.status == "SUCCEEDED"
    assert result.claims[0].evidence_class == "SOURCE-BACKED"
    assert "public evidence" in result.summary
    assert result.uncertainty


def test_public_research_adapters_reject_non_finite_or_oversized_limits() -> None:
    with pytest.raises(ValueError, match="timeout"):
        RedditRssQueryProvider(timeout_seconds=math.inf)
    with pytest.raises(ValueError, match="max_bytes"):
        RedditRssQueryProvider(max_bytes=2 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="timeout"):
        XAppOnlyQueryProvider(timeout_seconds=math.nan)


async def test_x_error_response_is_not_treated_as_empty_success() -> None:
    provider = XAppOnlyQueryProvider(
        bearer_token="system-token",
        fetcher=_fetcher(b'{"errors":[{"detail":"denied"}]}', []),
    )

    with pytest.raises(ResearchSourceUnavailable, match="error response"):
        await provider("agent research")
