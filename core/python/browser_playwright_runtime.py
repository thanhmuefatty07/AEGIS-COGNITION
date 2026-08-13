from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import inspect
from pathlib import Path
from typing import Any, Mapping

from .browser_live_collector import BrowserLiveCollectorProducer
from .browser_runtime_adapter import (
    BrowserRuntimeCapture,
    BrowserRuntimeCollectorAdapter,
)


PlaywrightAction = Callable[["PlaywrightBrowserRuntimeSession"], Awaitable[Any] | Any]


@dataclass
class PlaywrightBrowserRuntimeSession:
    page: Any
    context: Any | None = None
    network_events: list[Mapping[str, Any]] = field(default_factory=list)
    _attached: bool = False

    def __post_init__(self) -> None:
        if self.page is None:
            raise ValueError("playwright runtime session requires a page")
        self.attach_network_listeners()

    def attach_network_listeners(self) -> None:
        if self._attached or not hasattr(self.page, "on"):
            return
        on = getattr(self.page, "on")
        on("request", self._record_request)
        on("response", self._record_response)
        on("requestfailed", self._record_request_failed)
        self._attached = True

    async def get_current_page(self) -> Any:
        return self.page

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
            raw = getattr(self.page, "accessibility_snapshot")
            value = await _maybe_await(raw() if callable(raw) else raw)
            return _mapping_or_unavailable(value)
        if self.context is not None and hasattr(self.context, "accessibility"):
            accessibility = getattr(self.context, "accessibility")
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
