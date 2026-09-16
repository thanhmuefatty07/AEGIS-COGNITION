"""Policy-bounded public research adapters.

These adapters implement the existing ``SearchProgramExecutor`` query-provider
contract. They do not own browser profiles, cookies, or user sessions:
Reddit uses public RSS and X uses an explicitly supplied app-only bearer token.
Returned text is untrusted evidence and is bounded before it reaches an agent.
"""

from __future__ import annotations

import asyncio
from http.client import HTTPMessage
import inspect
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Mapping
from typing import IO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .lab import SearchProgram, SearchProgramExecutor, _assert_research_host_egress
from .subagents import AgentClaim, AgentHandler, AgentTaskContext


RESEARCH_ADAPTER_SCHEMA_V1 = "aegis-research-adapters-v1"
REDDIT_RSS_HOST = "www.reddit.com"
X_API_HOST = "api.x.com"
_MAX_QUERY_CHARS = 512
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_CONTENT_CHARS = 32_768
_SUBREDDIT_PATTERN = re.compile(r"[A-Za-z0-9_+-]{1,64}\Z")
_X_ID_PATTERN = re.compile(r"[0-9]{1,64}\Z")


class ResearchAdapterError(ValueError):
    """Raised when a public-source response violates the adapter contract."""


class ResearchSourceUnavailable(RuntimeError):
    """Raised when a configured source cannot be used without hidden fallback."""


type ResearchHttpFetcher = Callable[[str, Mapping[str, str], float, int], bytes | Awaitable[bytes]]
type ResearchCandidate = dict[str, object]


def _bounded_query(value: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > _MAX_QUERY_CHARS:
        raise ResearchAdapterError("research query must be bounded non-empty text")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ResearchAdapterError("research query contains a control character")
    return value.strip()


def _validate_endpoint_shape(url: str, expected_host: str) -> None:
    if type(url) is not str or type(expected_host) is not str:
        raise ResearchAdapterError("research endpoint must be text")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ResearchAdapterError("research endpoint leaves the fixed HTTPS host boundary")


def _default_fetch(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> bytes:
    expected_host = urlparse(url).hostname
    if expected_host is None:
        raise ResearchAdapterError("research endpoint must include a host")
    _validate_endpoint_shape(url, expected_host)
    _assert_research_host_egress(expected_host, port=443)
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with build_opener(_NoRedirectHandler).open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            _validate_endpoint_shape(final_url, expected_host)
            _assert_research_host_egress(expected_host, port=443)
            payload = response.read(max_bytes + 1)
    except HTTPError as error:
        raise ResearchSourceUnavailable(f"public research source returned HTTP {error.code}") from error
    except (OSError, URLError, TimeoutError) as error:
        raise ResearchSourceUnavailable("public research source request failed") from error
    if len(payload) > max_bytes:
        raise ResearchAdapterError("public research response exceeds its byte quota")
    return payload


async def _fetch(
    fetcher: ResearchHttpFetcher,
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_bytes: int,
) -> bytes:
    try:
        result = fetcher(url, headers, timeout_seconds, max_bytes)
        if inspect.isawaitable(result):
            result = await result
    except ResearchAdapterError:
        raise
    except ResearchSourceUnavailable:
        raise
    except (HTTPError, OSError, URLError, TimeoutError) as error:
        raise ResearchSourceUnavailable("public research source request failed") from error
    if type(result) is not bytes:
        raise ResearchAdapterError("research HTTP fetcher must return bytes")
    if len(result) > max_bytes:
        raise ResearchAdapterError("public research response exceeds its byte quota")
    return result


def _candidate_base(
    *,
    uri: str,
    content: str,
    source_id: str,
    extractor: str,
    retrieved_at_ms: int,
    provenance_cluster: str,
) -> ResearchCandidate:
    if len(uri) > 2_048 or len(source_id) > 512:
        raise ResearchAdapterError("public research candidate identity exceeds its bound")
    return {
        "uri": uri,
        "content": content[:_MAX_CONTENT_CHARS],
        "source_id": source_id,
        "retrieved_at_ms": retrieved_at_ms,
        "extractor": extractor,
        "provenance_cluster": provenance_cluster,
        "trust_tier": 1,
        "content_truncated": len(content) > _MAX_CONTENT_CHARS,
    }


def _local_name(tag: object) -> str:
    if type(tag) is not str:
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if child is not element and _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _entry_uri(element: ET.Element) -> str:
    for child in element.iter():
        if child is element or _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if href:
            return href
    return ""


class RedditRssQueryProvider:
    """Credential-free Reddit RSS provider for public, bounded research."""

    def __init__(
        self,
        *,
        subreddit: str | None = None,
        max_results: int = 25,
        timeout_seconds: float = 10.0,
        max_bytes: int = _MAX_RESPONSE_BYTES,
        user_agent: str = "aegis-cognition-research/1",
        fetcher: ResearchHttpFetcher = _default_fetch,
    ) -> None:
        if subreddit is not None and not _SUBREDDIT_PATTERN.fullmatch(subreddit):
            raise ResearchAdapterError("subreddit must be a simple Reddit community name")
        if type(max_results) is not int or not 1 <= max_results <= 25:
            raise ResearchAdapterError("Reddit RSS max_results must be within [1, 25]")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ResearchAdapterError("research timeout must be positive")
        if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_RESPONSE_BYTES:
            raise ResearchAdapterError("research max_bytes must be positive")
        if type(user_agent) is not str or not user_agent.strip() or len(user_agent) > 512:
            raise ResearchAdapterError("research user_agent must be non-empty")
        if not callable(fetcher):
            raise ResearchAdapterError("research fetcher must be callable")
        self.subreddit = subreddit
        self.max_results = max_results
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._fetcher = fetcher

    @property
    def capabilities(self) -> dict[str, object]:
        return {
            "schema": RESEARCH_ADAPTER_SCHEMA_V1,
            "source": "reddit",
            "transport": "public_rss",
            "login_required": False,
            "user_cookies_used": False,
            "max_results": self.max_results,
        }

    async def __call__(self, query: str, **_: object) -> list[ResearchCandidate]:
        text = _bounded_query(query)
        path = "/search.rss" if self.subreddit is None else f"/r/{self.subreddit}/search.rss"
        params = {"q": text, "sort": "relevance", "t": "all", "limit": str(self.max_results)}
        if self.subreddit is not None:
            params["restrict_sr"] = "on"
        url = f"https://{REDDIT_RSS_HOST}{path}?{urlencode(params)}"
        _validate_endpoint_shape(url, REDDIT_RSS_HOST)
        payload = await _fetch(
            self._fetcher,
            url,
            {"Accept": "application/atom+xml,application/rss+xml", "User-Agent": self.user_agent},
            self.timeout_seconds,
            self.max_bytes,
        )
        lowered = payload.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise ResearchAdapterError("Reddit RSS document contains a disallowed DTD/entity declaration")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise ResearchAdapterError("Reddit RSS response is not valid XML") from error
        retrieved_at_ms = max(1, int(time.time() * 1000))
        candidates: list[ResearchCandidate] = []
        for entry in root.iter():
            if _local_name(entry.tag) != "entry":
                continue
            uri = _entry_uri(entry)
            parsed_uri = urlparse(uri)
            if parsed_uri.scheme != "https" or parsed_uri.hostname != REDDIT_RSS_HOST:
                continue
            source_id = _child_text(entry, "id") or uri
            title = _child_text(entry, "title")
            content = _child_text(entry, "content") or _child_text(entry, "summary")
            combined = "\n".join(part for part in (title, content) if part)
            if not combined or len(uri) > 2_048 or len(source_id) > 512:
                continue
            candidate = _candidate_base(
                uri=uri,
                content=combined,
                source_id=source_id,
                extractor="reddit-rss-public",
                retrieved_at_ms=retrieved_at_ms,
                provenance_cluster="reddit",
            )
            candidate["title"] = title[:4_096]
            candidate["published_at"] = (_child_text(entry, "published") or _child_text(entry, "updated"))[:128]
            candidate["author"] = _child_text(entry, "name")[:512]
            candidates.append(candidate)
            if len(candidates) >= self.max_results:
                break
        return candidates


class XAppOnlyQueryProvider:
    """X recent-search provider using app-only bearer auth, never user login."""

    def __init__(
        self,
        *,
        bearer_token: str | None = None,
        token_env: str = "AEGIS_X_API_BEARER_TOKEN",
        max_results: int = 10,
        timeout_seconds: float = 10.0,
        max_bytes: int = _MAX_RESPONSE_BYTES,
        fetcher: ResearchHttpFetcher = _default_fetch,
    ) -> None:
        if bearer_token is not None and (
            type(bearer_token) is not str
            or not bearer_token.strip()
            or any(ord(character) < 33 for character in bearer_token)
        ):
            raise ResearchAdapterError("X bearer token must be non-empty text without control characters")
        if type(token_env) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", token_env):
            raise ResearchAdapterError("X token_env must be a simple uppercase environment key")
        if type(max_results) is not int or not 10 <= max_results <= 100:
            raise ResearchAdapterError("X recent-search max_results must be within [10, 100]")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ResearchAdapterError("research timeout must be positive")
        if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_RESPONSE_BYTES:
            raise ResearchAdapterError("research max_bytes must be positive")
        if not callable(fetcher):
            raise ResearchAdapterError("research fetcher must be callable")
        self._bearer_token = bearer_token.strip() if bearer_token is not None else None
        self.token_env = token_env
        self.max_results = max_results
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = max_bytes
        self._fetcher = fetcher

    @property
    def configured(self) -> bool:
        return bool(self._bearer_token or os.environ.get(self.token_env, "").strip())

    @property
    def capabilities(self) -> dict[str, object]:
        return {
            "schema": RESEARCH_ADAPTER_SCHEMA_V1,
            "source": "x",
            "transport": "official_api_v2_recent_search",
            "login_required": False,
            "user_cookies_used": False,
            "user_context": False,
            "requires_system_app_bearer": True,
            "configured": self.configured,
            "max_results": self.max_results,
        }

    def _token(self) -> str:
        token = self._bearer_token or os.environ.get(self.token_env, "")
        if not token.strip():
            raise ResearchSourceUnavailable(
                "X app-only research requires a system bearer token; user login and cookies are unsupported"
            )
        return token.strip()

    async def __call__(self, query: str, **_: object) -> list[ResearchCandidate]:
        text = _bounded_query(query)
        params = {
            "query": text,
            "max_results": str(self.max_results),
            "tweet.fields": "created_at,author_id,public_metrics",
        }
        url = f"https://{X_API_HOST}/2/tweets/search/recent?{urlencode(params)}"
        _validate_endpoint_shape(url, X_API_HOST)
        payload = await _fetch(
            self._fetcher,
            url,
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token()}",
            },
            self.timeout_seconds,
            self.max_bytes,
        )
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResearchAdapterError("X API response is not valid JSON") from error
        if type(decoded) is not dict:
            raise ResearchAdapterError("X API response must be a JSON object")
        response: dict[str, object] = cast(dict[str, object], decoded)
        if response.get("errors"):
            raise ResearchSourceUnavailable("X API returned an error response")
        raw_data = response.get("data", [])
        if type(raw_data) is not list:
            raise ResearchAdapterError("X API response data must be a list")
        retrieved_at_ms = max(1, int(time.time() * 1000))
        candidates: list[ResearchCandidate] = []
        for raw_item in cast(list[object], raw_data):
            if type(raw_item) is not dict:
                continue
            item = cast(dict[str, object], raw_item)
            tweet_id = item.get("id")
            text_value = item.get("text")
            if (
                type(tweet_id) is not str
                or not _X_ID_PATTERN.fullmatch(tweet_id)
                or type(text_value) is not str
                or not text_value.strip()
            ):
                continue
            candidate = _candidate_base(
                uri=f"https://x.com/i/web/status/{tweet_id}",
                content=text_value,
                source_id=tweet_id,
                extractor="x-api-v2-app-only",
                retrieved_at_ms=retrieved_at_ms,
                provenance_cluster="x",
            )
            created_at = item.get("created_at")
            if type(created_at) is str and len(created_at) <= 128:
                candidate["created_at"] = created_at
            author_id = item.get("author_id")
            if type(author_id) is str and _X_ID_PATTERN.fullmatch(author_id):
                candidate["author_id"] = author_id
            metrics = item.get("public_metrics")
            if type(metrics) is dict:
                metrics_map = cast(dict[str, object], metrics)
                safe_metrics = {
                    key: value
                    for key in (
                        "bookmark_count",
                        "impression_count",
                        "like_count",
                        "quote_count",
                        "reply_count",
                        "retweet_count",
                    )
                    if type(value := metrics_map.get(key)) is int and value >= 0
                }
                if safe_metrics:
                    candidate["public_metrics"] = safe_metrics
            candidates.append(candidate)
            if len(candidates) >= self.max_results:
                break
        return candidates


class PublicResearchRouter:
    """Route ``reddit:``, ``x:``, or ``all:`` queries to explicit providers."""

    def __init__(
        self,
        *,
        reddit: RedditRssQueryProvider | None = None,
        x: XAppOnlyQueryProvider | None = None,
        default_source: str | None = None,
    ) -> None:
        if reddit is None and x is None:
            raise ResearchAdapterError("public research router needs at least one source")
        if default_source is not None and default_source not in {"reddit", "x", "all"}:
            raise ResearchAdapterError("default_source must be reddit, x, or all")
        self.reddit = reddit
        self.x = x
        self.default_source = default_source

    @property
    def capabilities(self) -> dict[str, object]:
        sources: dict[str, object] = {}
        if self.reddit is not None:
            sources["reddit"] = self.reddit.capabilities
        if self.x is not None:
            sources["x"] = self.x.capabilities
        return {"schema": RESEARCH_ADAPTER_SCHEMA_V1, "sources": sources, "default_source": self.default_source}

    def _select(self, query: str) -> tuple[str, str]:
        text = _bounded_query(query)
        prefix, separator, remainder = text.partition(":")
        if separator and prefix.lower() in {"reddit", "x", "all"}:
            return prefix.lower(), _bounded_query(remainder)
        if self.default_source is not None:
            return self.default_source, text
        configured = ["reddit"] if self.reddit is not None else []
        if self.x is not None and self.x.configured:
            configured.append("x")
        if len(configured) == 1:
            return configured[0], text
        raise ResearchAdapterError("multi-source research query requires reddit:, x:, or all: prefix")

    async def __call__(self, query: str, **context: object) -> list[ResearchCandidate]:
        source, text = self._select(query)
        if source == "all" and (self.reddit is None or self.x is None or not self.x.configured):
            raise ResearchSourceUnavailable("all-source research requires both Reddit and X providers to be configured")
        providers: list[Callable[..., Awaitable[list[ResearchCandidate]]]] = []
        if source in {"reddit", "all"} and self.reddit is not None:
            providers.append(self.reddit)
        x_provider = self.x
        if x_provider is not None and (source == "x" or (source == "all" and x_provider.configured)):
            providers.append(x_provider)
        if not providers:
            raise ResearchSourceUnavailable(f"requested public research source is not configured: {source}")
        groups = await asyncio.gather(*(provider(text, **context) for provider in providers))
        return [candidate for group in groups for candidate in group]


def build_public_research_handler(
    query_provider: Callable[..., object],
    *,
    max_candidates: int = 20,
    timeout_seconds: float = 10.0,
) -> AgentHandler:
    """Bind the public-source router to one bounded child-agent handler."""

    if not callable(query_provider):
        raise ResearchAdapterError("public research query_provider must be callable")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 1_000:
        raise ResearchAdapterError("public research max_candidates must be within [1, 1000]")
    if type(timeout_seconds) not in (int, float) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
        raise ResearchAdapterError("public research timeout must be positive")

    async def handler(context: AgentTaskContext):
        started = time.perf_counter()
        program = SearchProgram.from_mappings(
            [{"kind": "query", "text": context.prompt}],
            max_candidates=max_candidates,
            provider="public-research-adapter",
        )
        candidates = await SearchProgramExecutor(
            query_provider=query_provider,
            timeout_seconds=float(timeout_seconds),
        ).execute(program, task=context.prompt, run_id=context.request.run_id)
        compact_candidates: list[dict[str, object]] = []
        claims: list[AgentClaim] = []
        for candidate in candidates:
            uri = candidate.get("uri")
            if type(uri) is not str or not uri:
                continue
            compact: dict[str, object] = {"uri": uri}
            for key in ("source_id", "title", "published_at", "created_at", "author", "author_id"):
                value = candidate.get(key)
                if type(value) is str and value:
                    compact[key] = value[:512]
            content = candidate.get("content")
            if type(content) is str and content:
                compact["content"] = content[:2_048]
            compact_candidates.append(compact)
            if len(claims) < 64:
                claims.append(
                    AgentClaim(
                        f"public adapter observed a bounded candidate at {uri}",
                        "SOURCE-BACKED",
                        (uri,),
                    )
                )
        summary = json.dumps(
            {
                "source": "public-research-adapter",
                "candidate_count": len(candidates),
                "candidates": compact_candidates,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(summary) > 8_192:
            summary = f"{summary[:8_160]}...[summary truncated]"
        uncertainty = ("public source content is untrusted and requires root verification",)
        return context.success(
            summary,
            claims=claims,
            uncertainty=uncertainty,
            elapsed_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    return handler


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_301(self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage) -> None:
        raise ResearchSourceUnavailable("public research redirects are not allowed")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


__all__ = [
    "REDDIT_RSS_HOST",
    "RESEARCH_ADAPTER_SCHEMA_V1",
    "X_API_HOST",
    "PublicResearchRouter",
    "RedditRssQueryProvider",
    "ResearchAdapterError",
    "ResearchCandidate",
    "ResearchHttpFetcher",
    "ResearchSourceUnavailable",
    "XAppOnlyQueryProvider",
    "build_public_research_handler",
]
