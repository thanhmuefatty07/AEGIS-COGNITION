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
_MAX_NETWORK_EVENTS = 512
_MAX_ALLOWED_BROWSER_HOSTS = 32


def _canonical_host(host: str) -> str:
    if type(host) is not str or not host:
        raise ValueError("browser egress host is invalid")
    try:
        return ipaddress.ip_address(host).compressed.casefold()
    except ValueError:
        try:
            ascii_host = host.rstrip(".").encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise ValueError("browser egress host is invalid") from exc
        labels = ascii_host.split(".")
        if len(ascii_host) > 253 or any(
            not label
            or len(label) > 63
            or label[0] == "-"
            or label[-1] == "-"
            or any(not (character.isascii() and (character.isalnum() or character == "-")) for character in label)
            for label in labels
        ):
            raise ValueError("browser egress host is invalid") from None
        return ascii_host


def _host_resolver_rules(pinned_hosts: Mapping[str, str]) -> str:
    rules = []
    for host, address in sorted(pinned_hosts.items()):
        parsed_address = ipaddress.ip_address(address)
        mapped_address = f"[{parsed_address.compressed}]" if parsed_address.version == 6 else str(parsed_address)
        rules.append(f"MAP {host} {mapped_address}")
    rules.append("MAP * ^NOTFOUND")
    return ", ".join(rules)


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
        or not address.is_global
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

        failures = await _close_owned_resources(
            (
                ("context", self.context, "close"),
                ("browser", self.browser, "close"),
                ("playwright", self.playwright, "stop"),
            )
        )
        failed_owners = {owner_name for owner_name, _ in failures}
        if "context" not in failed_owners:
            self.context = None
        if "browser" not in failed_owners:
            self.browser = None
        if "playwright" not in failed_owners:
            self.playwright = None
        if failures:
            primary_error = failures[0][1]
            for owner_name, error in failures[1:]:
                primary_error.add_note(f"additional {owner_name} cleanup failed ({type(error).__name__})")
            raise primary_error

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

    def _append_network_event(self, event: Mapping[str, Any]) -> None:
        if len(self.network_events) >= _MAX_NETWORK_EVENTS:
            del self.network_events[: len(self.network_events) - _MAX_NETWORK_EVENTS + 1]
        self.network_events.append(event)

    def _record_request(self, request: Any) -> None:
        self._append_network_event(
            {
                "phase": "request",
                "url": _value(request, "url"),
                "method": _value(request, "method"),
                "resource_type": _value(request, "resource_type"),
            }
        )

    def _record_response(self, response: Any) -> None:
        request = _value(response, "request")
        self._append_network_event(
            {
                "phase": "response",
                "url": _value(response, "url"),
                "status": _value(response, "status"),
                "method": _value(request, "method") if request is not None else "",
            }
        )

    def _record_request_failed(self, request: Any) -> None:
        failure = _value(request, "failure")
        self._append_network_event(
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
    read_only: bool = False,
    browser_type: str = "chromium",
) -> PlaywrightBrowserRuntimeSession:
    """Launch a managed Playwright session with an egress route guard.

    The function is intentionally optional: Playwright and its browser binary
    are loaded only when called. Missing runtime prerequisites raise a clear
    error and never degrade into an untracked or unbounded browser session.
    """

    if type(read_only) is not bool:
        raise ValueError("Playwright read_only must be a boolean")
    parsed_initial = urlparse(initial_url)
    try:
        initial_host = _canonical_host(parsed_initial.hostname or "")
        normalized_allowed_hosts = frozenset(_canonical_host(host) for host in allowed_hosts)
    except (TypeError, ValueError) as exc:
        raise ValueError("Playwright initial URL violates the browser egress policy") from exc
    if len(normalized_allowed_hosts) > _MAX_ALLOWED_BROWSER_HOSTS:
        raise ValueError("Playwright initial URL violates the browser egress policy")
    try:
        initial_port_allowed = not read_only or parsed_initial.port in (None, 443)
    except ValueError:
        initial_port_allowed = False
    if (
        not initial_url.strip()
        or (require_https and parsed_initial.scheme != "https")
        or not parsed_initial.hostname
        or parsed_initial.username
        or parsed_initial.password
        or not initial_port_allowed
        or _is_disallowed_ip_literal(parsed_initial.hostname or "")
        or (normalized_allowed_hosts and initial_host not in normalized_allowed_hosts)
    ):
        raise ValueError("Playwright initial URL violates the browser egress policy")
    pinned_hosts: dict[str, str] = {}
    if read_only:
        hosts_to_pin = normalized_allowed_hosts or frozenset({initial_host})
        for host in sorted(hosts_to_pin):
            try:
                addresses = await asyncio.to_thread(_resolved_addresses, host)
            except (OSError, ValueError):
                continue
            if any(_is_disallowed_ip_literal(address) for address in addresses):
                continue
            pinned_hosts[host] = addresses[0]
        if initial_host not in pinned_hosts:
            raise ValueError("Playwright initial URL violates the browser egress policy")
    elif not await asyncio.to_thread(_hostname_resolution_is_safe, parsed_initial.hostname):
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
        launch_options: dict[str, Any] = {"headless": headless}
        if read_only:
            launch_options["args"] = [f"--host-resolver-rules={_host_resolver_rules(pinned_hosts)}"]
        browser = await launcher.launch(**launch_options)
        context = (
            await browser.new_context(
                accept_downloads=False,
                java_script_enabled=False,
                service_workers="block",
            )
            if read_only
            else await browser.new_context()
        )

        async def guard(route: Any) -> None:
            request = getattr(route, "request", None)
            request_url = str(getattr(request, "url", ""))
            parsed = urlparse(request_url)
            try:
                port_allowed = not read_only or parsed.port in (None, 443)
            except ValueError:
                port_allowed = False
            method = getattr(request, "method", None)
            method_allowed = not read_only or (type(method) is str and method.upper() in {"GET", "HEAD"})
            try:
                request_host = _canonical_host(parsed.hostname or "")
            except ValueError:
                request_host = ""
            host_allowed = (
                request_host in pinned_hosts
                if read_only
                else bool(request_host) and (not normalized_allowed_hosts or request_host in normalized_allowed_hosts)
            )
            resolved_safe = (
                request_host in pinned_hosts
                if read_only
                else bool(parsed.hostname)
                and await asyncio.to_thread(_hostname_resolution_is_safe, parsed.hostname)
            )
            allowed = (
                bool(request_host)
                and not parsed.username
                and not parsed.password
                and port_allowed
                and method_allowed
                and not _is_disallowed_ip_literal(parsed.hostname or "")
                and host_allowed
                and resolved_safe
                and (not require_https or parsed.scheme == "https")
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
    except BaseException as error:
        cleanup_failures = await _close_owned_resources(
            (("context", context, "close"), ("browser", browser, "close"), ("playwright", manager, "stop"))
        )
        if cleanup_failures:
            cleanup_summary = ", ".join(
                f"{owner_name} ({type(cleanup_error).__name__})"
                for owner_name, cleanup_error in cleanup_failures
            )
            error.add_note(f"browser initialization cleanup also failed: {cleanup_summary}")
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


async def _close_owned_resources(
    resources: tuple[tuple[str, object | None, str], ...],
) -> tuple[tuple[str, BaseException], ...]:
    failures: list[tuple[str, BaseException]] = []
    for owner_name, owner, method_name in resources:
        try:
            method = getattr(owner, method_name, None)
            if callable(method):
                await _maybe_await(method())
        except BaseException as error:
            failures.append((owner_name, error))
    return tuple(failures)


def _mapping_or_unavailable(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {"source": "playwright-runtime-session", "unavailable": True}
