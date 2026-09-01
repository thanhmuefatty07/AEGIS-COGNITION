from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import ipaddress
import inspect
from pathlib import Path
from collections.abc import Mapping
import socket
from typing import Any
from urllib.parse import urlparse

try:
    from .browser_live_collector import BrowserLiveCollectorProducer
    from .browser_runtime_adapter import (
        BrowserRuntimeCapture,
        BrowserRuntimeCollectorAdapter,
    )
except ImportError:
    from browser_live_collector import BrowserLiveCollectorProducer
    from browser_runtime_adapter import (
        BrowserRuntimeCapture,
        BrowserRuntimeCollectorAdapter,
    )


PlaywrightAction = Callable[["PlaywrightBrowserRuntimeSession"], Awaitable[Any] | Any]


def _is_disallowed_ip_literal(host: str) -> bool:
    """Reject unsafe IPv4/IPv6 literals, including legacy IPv4 spellings."""

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except (OSError, ValueError):
            return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolved_addresses(host: str) -> tuple[str, ...]:
    """Resolve a hostname once and return every stream address for policy checks."""

    if _is_disallowed_ip_literal(host):
        return (host,)
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("browser egress hostname resolution failed") from exc
    addresses = tuple(
        sorted(
            {
                str(record[4][0])
                for record in records
                if len(record) > 4 and isinstance(record[4], tuple) and record[4]
            }
        )
    )
    if not addresses:
        raise ValueError("browser egress hostname has no resolved address")
    return addresses


def _hostname_resolution_is_safe(host: str) -> bool:
    try:
        return all(not _is_disallowed_ip_literal(address) for address in _resolved_addresses(host))
    except (OSError, ValueError):
        return False


@dataclass
class PlaywrightBrowserRuntimeSession:
    page: Any
    context: Any | None = None
    browser: Any | None = None
    playwright: Any | None = None
    network_events: list[Mapping[str, Any]] = field(default_factory=list)
    _attached: bool = False

    def __post_init__(self) -> None:
        if self.page is None:
            raise ValueError("playwright runtime session requires a page")
        self.attach_network_listeners()

    def attach_network_listeners(self) -> None:
        if self._attached or not hasattr(self.page, "on"):
            return
        on = self.page.on
        on("request", self._record_request)
        on("response", self._record_response)
        on("requestfailed", self._record_request_failed)
        self._attached = True

    async def get_current_page(self) -> Any:
        return self.page

    @property
    def current_url(self) -> str:
        value = getattr(self.page, "url", "")
        return str(value() if callable(value) else value)

    async def close(self) -> None:
        """Close page resources in reverse ownership order; repeated calls safe."""

        for owner, method_name in (
            (self.context, "close"),
            (self.browser, "close"),
            (self.playwright, "stop"),
        ):
            method = getattr(owner, method_name, None)
            if callable(method):
                await _maybe_await(method())
        self.context = None
        self.browser = None
        self.playwright = None

    async def clear_network_log(self) -> None:
        self.network_events.clear()

    async def drain_network_log(self) -> list[Mapping[str, Any]]:
        drained = list(self.network_events)
        self.network_events.clear()
        return drained

    async def goto(self, url: str, **kwargs: Any) -> Any:
        return await _maybe_await(self.page.goto(url, **kwargs))

    async def click(self, selector: str, **kwargs: Any) -> Any:
        return await _maybe_await(self.page.click(selector, **kwargs))

    async def fill(self, selector: str, value: str, **kwargs: Any) -> Any:
        return await _maybe_await(self.page.fill(selector, value, **kwargs))

    async def type_text(self, selector: str, value: str, **kwargs: Any) -> Any:
        if hasattr(self.page, "type"):
            return await _maybe_await(self.page.type(selector, value, **kwargs))
        return await self.fill(selector, value, **kwargs)

    async def get_accessibility_tree(self) -> Mapping[str, Any]:
        if hasattr(self.page, "accessibility_snapshot"):
            raw = self.page.accessibility_snapshot
            value = await _maybe_await(raw() if callable(raw) else raw)
            return _mapping_or_unavailable(value)
        if self.context is not None and hasattr(self.context, "accessibility"):
            accessibility = self.context.accessibility
            if hasattr(accessibility, "snapshot"):
                value = await _maybe_await(accessibility.snapshot())
                return _mapping_or_unavailable(value)
        return {"source": "playwright-runtime-session", "unavailable": True}

    def _record_request(self, request: Any) -> None:
        self.network_events.append(
            {
                "phase": "request",
                "url": _value(request, "url"),
                "method": _value(request, "method"),
                "resource_type": _value(request, "resource_type"),
            }
        )

    def _record_response(self, response: Any) -> None:
        request = _value(response, "request")
        self.network_events.append(
            {
                "phase": "response",
                "url": _value(response, "url"),
                "status": _value(response, "status"),
                "method": _value(request, "method") if request is not None else "",
            }
        )

    def _record_request_failed(self, request: Any) -> None:
        failure = _value(request, "failure")
        self.network_events.append(
            {
                "phase": "requestfailed",
                "url": _value(request, "url"),
                "method": _value(request, "method"),
                "failure": _value(failure, "error_text") if failure is not None else "",
            }
        )


async def capture_playwright_action(
    *,
    page: Any,
    output_root: str | Path,
    run_id: int,
    action_id: int,
    sequence_number: int,
    action: PlaywrightAction,
    context: Any | None = None,
    max_bytes: int = 32 * 1024 * 1024,
) -> BrowserRuntimeCapture:
    session = PlaywrightBrowserRuntimeSession(page=page, context=context)
    adapter = BrowserRuntimeCollectorAdapter(
        BrowserLiveCollectorProducer(output_root, max_bytes=max_bytes)
    )
    return await adapter.capture_action(
        browser_session=session,
        run_id=run_id,
        action_id=action_id,
        sequence_number=sequence_number,
        action=lambda: action(session),
    )


async def launch_playwright_session(
    *,
    initial_url: str,
    allowed_hosts: tuple[str, ...] = (),
    require_https: bool = True,
    headless: bool = True,
    browser_type: str = "chromium",
) -> PlaywrightBrowserRuntimeSession:
    """Launch a managed Playwright session with an egress route guard.

    The function is intentionally optional: Playwright and its browser binary
    are loaded only when called. Missing runtime prerequisites raise a clear
    error and never degrade into an untracked or unbounded browser session.
    """

    parsed_initial = urlparse(initial_url)
    if (
        not initial_url.strip()
        or (require_https and parsed_initial.scheme != "https")
        or not parsed_initial.hostname
        or parsed_initial.username
        or parsed_initial.password
        or _is_disallowed_ip_literal(parsed_initial.hostname or "")
        or (allowed_hosts and parsed_initial.hostname.lower() not in allowed_hosts)
    ):
        raise ValueError("Playwright initial URL violates the browser egress policy")
    if not await asyncio.to_thread(_hostname_resolution_is_safe, parsed_initial.hostname):
        raise ValueError("Playwright initial URL violates the browser egress policy")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; install the browser extra") from exc

    manager = await async_playwright().start()
    browser = None
    context = None
    try:
        launcher = getattr(manager, browser_type, None)
        if launcher is None:
            raise ValueError(f"unsupported Playwright browser type: {browser_type}")
        browser = await launcher.launch(headless=headless)
        context = await browser.new_context()

        async def guard(route: Any) -> None:
            request_url = str(getattr(route.request, "url", ""))
            parsed = urlparse(request_url)
            resolved_safe = (
                await asyncio.to_thread(_hostname_resolution_is_safe, parsed.hostname)
                if parsed.hostname
                else False
            )
            allowed = (
                bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not _is_disallowed_ip_literal(parsed.hostname or "")
                and resolved_safe
                and (not require_https or parsed.scheme == "https")
                and (not allowed_hosts or parsed.hostname.lower() in allowed_hosts)
            )
            if allowed:
                await route.continue_()
            else:
                await route.abort()

        await context.route("**/*", guard)
        page = await context.new_page()
        session = PlaywrightBrowserRuntimeSession(
            page=page,
            context=context,
            browser=browser,
            playwright=manager,
        )
        await session.goto(initial_url)
        return session
    except BaseException:
        for owner, method_name in ((context, "close"), (browser, "close"), (manager, "stop")):
            method = getattr(owner, method_name, None)
            if callable(method):
                await _maybe_await(method())
        raise


def _value(owner: Any, name: str) -> Any:
    if isinstance(owner, Mapping):
        return owner.get(name, "")
    if owner is None or not hasattr(owner, name):
        return ""
    value = getattr(owner, name)
    return value() if callable(value) else value


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _mapping_or_unavailable(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {"source": "playwright-runtime-session", "unavailable": True}
