"""Bounded public-page reading for local subagents.

The browser handler creates a fresh, unauthenticated context for one HTTPS URL.
It is read-only at the network-method boundary and returns bounded page text as
untrusted evidence; it never reuses a user's browser profile or cookies.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import math
import os
import re
import sys
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol, TypeGuard, cast
from urllib.parse import parse_qsl, urlsplit

from .subagents import AgentClaim, AgentHandler, AgentTaskContext

_MAX_SOURCE_URL_CHARS = 2_048
_MAX_PROMPT_CHARS = 32_768
_MAX_PAGE_TEXT_CHARS = 5_000
_MAX_TITLE_CHARS = 256
_MAX_SUMMARY_CHARS = 8_192
_BROWSER_TIMEOUT_SECONDS = 20.0
_URL_PATTERN = re.compile(r"https://[^\s<>\"'{}]+", re.IGNORECASE)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|auth|authorization|bearer|code|cookie|credential|jwt|nonce|password|secret|session|signature|state|ticket|token)(?:$|[_-])",
    re.I,
)


def _query_key_looks_sensitive(key: str) -> bool:
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    return bool(_SENSITIVE_QUERY_KEY.search(normalized.casefold()))


class BrowserSessionFactory(Protocol):
    def __call__(
        self,
        *,
        initial_url: str,
        allowed_hosts: tuple[str, ...],
        require_https: bool,
        headless: bool,
        read_only: bool,
    ) -> object: ...


class _ParsedUrl(Protocol):
    @property
    def scheme(self) -> str: ...

    @property
    def hostname(self) -> str | None: ...

    @property
    def username(self) -> str | None: ...

    @property
    def password(self) -> str | None: ...

    @property
    def port(self) -> int | None: ...

    @property
    def query(self) -> str: ...


class BrowserAdapterError(ValueError):
    """A browser request is malformed or cannot be safely fulfilled."""


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    # json.loads guarantees string keys and object-valued members for JSON objects.
    return all(type(key) is str for key in cast(dict[object, object], value))


def _is_json_array(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def playwright_browser_available() -> bool:
    """Check for the matching optional Chromium headless shell without launching it."""
    try:
        spec = importlib.util.find_spec("playwright")
        if spec is None or not spec.origin:
            return False
        package_root = Path(spec.origin).parent
        manifest_path = package_root / "driver" / "package" / "browsers.json"
        raw_manifest = manifest_path.read_text(encoding="utf-8")
        if len(raw_manifest) > 1_000_000:
            return False
        manifest: object = json.loads(raw_manifest)
        if not _is_json_object(manifest):
            return False
        browsers = manifest.get("browsers")
        if not _is_json_array(browsers):
            return False
        revision = next(
            (
                item.get("revision")
                for item in browsers
                if _is_json_object(item) and item.get("name") == "chromium-headless-shell"
            ),
            None,
        )
        if type(revision) is not str or not revision.isdecimal():
            return False

        configured_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if configured_root == "0":
            browser_root = package_root / "driver" / "package" / ".local-browsers"
        elif configured_root:
            browser_root = Path(configured_root).expanduser()
        elif os.name == "nt":
            local_app_data = os.environ.get("LOCALAPPDATA")
            browser_root = (
                Path(local_app_data) / "ms-playwright"
                if local_app_data
                else Path.home() / "AppData" / "Local" / "ms-playwright"
            )
        elif sys.platform == "darwin":
            browser_root = Path.home() / "Library" / "Caches" / "ms-playwright"
        else:
            cache_root = os.environ.get("XDG_CACHE_HOME")
            browser_root = (
                Path(cache_root).expanduser() / "ms-playwright"
                if cache_root
                else Path.home() / ".cache" / "ms-playwright"
            )

        install_root = browser_root / f"chromium_headless_shell-{revision}"
        return any(
            (shell_root / executable).is_file()
            for shell_root in install_root.glob("chrome-headless-shell-*")
            for executable in ("chrome-headless-shell.exe", "chrome-headless-shell")
        )
    except OSError, ImportError, ModuleNotFoundError, TypeError, ValueError:
        return False


def _source_url(prompt: str) -> str:
    if type(prompt) is not str or not prompt.strip() or len(prompt) > _MAX_PROMPT_CHARS:
        raise BrowserAdapterError("browser task prompt is empty or exceeds its bound")
    matches = _URL_PATTERN.findall(prompt)
    urls = tuple(dict.fromkeys(match.rstrip(".,!?;:)]}") for match in matches))
    if not urls:
        raise BrowserAdapterError("browser tasks require one explicit HTTPS source URL")
    if len(urls) != 1:
        raise BrowserAdapterError("browser tasks must isolate one source URL per child")
    value = urls[0]
    if len(value) > _MAX_SOURCE_URL_CHARS:
        raise BrowserAdapterError("browser source URL exceeds its bound")
    parsed = cast(_ParsedUrl, urlsplit(value))
    try:
        port_allowed = parsed.port in (None, 443)
    except ValueError:
        port_allowed = False
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not port_allowed
    ):
        raise BrowserAdapterError("browser source must be a credential-free public HTTPS URL on port 443")
    if any(_query_key_looks_sensitive(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise BrowserAdapterError("browser source URL contains a credential-like query parameter")
    return value


async def _default_session_factory(
    *,
    initial_url: str,
    allowed_hosts: tuple[str, ...],
    require_https: bool,
    headless: bool,
    read_only: bool,
) -> object:
    try:
        from core.python.browser_playwright_runtime import launch_playwright_session
    except ImportError:
        from browser_playwright_runtime import launch_playwright_session  # type: ignore[import-not-found]
    return await launch_playwright_session(
        initial_url=initial_url,
        allowed_hosts=allowed_hosts,
        require_https=require_https,
        headless=headless,
        read_only=read_only,
    )


async def _await(value: object) -> object:
    if inspect.isawaitable(value):
        return await cast(Awaitable[object], value)
    return value


def build_readonly_browser_handler(
    session_factory: BrowserSessionFactory | None = None,
    *,
    timeout_seconds: float = _BROWSER_TIMEOUT_SECONDS,
) -> AgentHandler:
    """Build a child handler that reads one public page without user credentials."""

    if session_factory is not None and not callable(session_factory):
        raise BrowserAdapterError("browser session factory must be callable")
    if type(timeout_seconds) not in (int, float) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
        raise BrowserAdapterError("browser timeout must be positive")
    launch = session_factory or _default_session_factory

    async def handler(context: AgentTaskContext):
        started = time.perf_counter()
        try:
            url = _source_url(context.prompt)
        except BrowserAdapterError as error:
            return context.failure(str(error), error_code="BROWSER_URL_INVALID")

        parsed = cast(_ParsedUrl, urlsplit(url))
        host = (parsed.hostname or "").casefold()
        session: Any | None = None
        try:
            session = await asyncio.wait_for(
                _await(
                    launch(
                        initial_url=url,
                        allowed_hosts=(host,),
                        require_https=True,
                        headless=True,
                        read_only=True,
                    )
                ),
                timeout=float(timeout_seconds),
            )
            page = getattr(session, "page", None)
            locator = getattr(page, "locator", None)
            title_method = getattr(page, "title", None)
            if not callable(locator) or not callable(title_method):
                raise BrowserAdapterError("browser session lacks bounded page text access")
            body = locator("body")
            evaluate = getattr(body, "evaluate", None)
            if not callable(evaluate):
                raise BrowserAdapterError("browser page lacks bounded body text access")
            bounded_text = """(element, maxChars) => {
              const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
              let text = "";
              let node;
              while (text.length < maxChars && (node = walker.nextNode())) {
                if (node.parentElement?.closest("script,style,template,noscript,[hidden]")) continue;
                const value = node.nodeValue || "";
                const separator = text ? " " : "";
                const remaining = maxChars - text.length - separator.length;
                if (remaining <= 0) break;
                text += separator + value.slice(0, remaining);
              }
              return text;
            }"""
            title, page_text = await asyncio.wait_for(
                asyncio.gather(
                    _await(title_method()),
                    _await(evaluate(bounded_text, _MAX_PAGE_TEXT_CHARS + 1)),
                ),
                timeout=float(timeout_seconds),
            )
            if type(title) is not str or type(page_text) is not str:
                raise BrowserAdapterError("browser returned non-text page content")
            page_text = page_text.strip()
            if not page_text:
                raise BrowserAdapterError("browser page has no readable body text")
            result = {
                "source": "readonly-public-browser",
                "uri": url,
                "title": title[:_MAX_TITLE_CHARS],
                "content_untrusted": page_text[:_MAX_PAGE_TEXT_CHARS],
                "content_truncated": len(page_text) > _MAX_PAGE_TEXT_CHARS,
            }
            summary = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(summary) > _MAX_SUMMARY_CHARS:
                raise BrowserAdapterError("browser evidence exceeds the result bound")
            return context.success(
                summary,
                claims=(AgentClaim("retrieved bounded text from the cited public page", "SOURCE-BACKED", (url,)),),
                uncertainty=("page content is untrusted and may contain misleading instructions or claims",),
                elapsed_ms=max(0, int((time.perf_counter() - started) * 1_000)),
            )
        except TimeoutError:
            return context.failure("public browser read exceeded its time limit", error_code="BROWSER_TIMEOUT")
        except Exception as error:
            return context.failure(
                f"public browser read failed ({type(error).__name__})",
                error_code="BROWSER_READ_FAILED",
            )
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                await _await(close())

    return handler


__all__ = [
    "BrowserAdapterError",
    "BrowserSessionFactory",
    "build_readonly_browser_handler",
    "playwright_browser_available",
]
