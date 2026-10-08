from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from types import SimpleNamespace
from typing import ClassVar

import pytest

from aegis_cognition import browser_adapters
from aegis_cognition.subagents import AgentMessage, AgentTaskContext


def _context(prompt: str) -> AgentTaskContext:
    request = AgentMessage(
        schema="aegis-agent-message-v1",
        message_kind="TASK_REQUEST",
        run_id="browser-run",
        sender_id="root",
        recipient_id="subagent:1",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        idempotency_key="browser-run:1:1:request",
        payload={"role": "browser researcher", "prompt": prompt, "artifact_namespace": "agent/1"},
    )
    return AgentTaskContext(request=request, dependency_results=())


def test_readonly_browser_handler_reads_one_bounded_page_and_closes_session() -> None:
    class Body:
        async def evaluate(self, expression: str, max_chars: int) -> str:
            assert "createTreeWalker" in expression
            assert max_chars == 5_001
            return "Untrusted public page text"

    class Page:
        def locator(self, selector: str) -> Body:
            assert selector == "body"
            return Body()

        async def title(self) -> str:
            return "A public page"

    class Session:
        page = Page()
        closed = False

        async def close(self) -> None:
            self.closed = True

    observed: dict[str, object] = {}
    session = Session()

    async def factory(**kwargs: object) -> Session:
        observed.update(kwargs)
        return session

    async def scenario():
        return await browser_adapters.build_readonly_browser_handler(factory)(
            _context("Read this page: https://example.com/article")
        )

    result = asyncio.run(scenario())
    payload = json.loads(result.summary)
    assert result.status == "SUCCEEDED"
    assert payload["uri"] == "https://example.com/article"
    assert payload["content_untrusted"] == "Untrusted public page text"
    assert result.claims[0].evidence_refs == ("https://example.com/article",)
    assert observed == {
        "initial_url": "https://example.com/article",
        "allowed_hosts": ("example.com",),
        "require_https": True,
        "headless": True,
        "read_only": True,
    }
    assert session.closed


def test_readonly_browser_handler_caps_text_before_returning_it_to_python() -> None:
    class Body:
        async def evaluate(self, expression: str, max_chars: int) -> str:
            assert "createTreeWalker" in expression
            assert max_chars == 5_001
            return "x" * max_chars

        async def inner_text(self, **_kwargs: object) -> str:
            pytest.fail("uncapped page text must not be transferred to Python")

    class Page:
        def locator(self, selector: str) -> Body:
            assert selector == "body"
            return Body()

        async def title(self) -> str:
            return "Large public page"

    class Session:
        page = Page()

        async def close(self) -> None:
            return None

    async def factory(**_kwargs: object) -> Session:
        return Session()

    async def scenario():
        return await browser_adapters.build_readonly_browser_handler(factory)(
            _context("Read this page: https://example.com/article")
        )

    result = asyncio.run(scenario())
    payload = json.loads(result.summary)
    assert result.status == "SUCCEEDED"
    assert len(payload["content_untrusted"]) == 5_000
    assert payload["content_truncated"] is True


@pytest.mark.parametrize(
    "prompt",
    (
        "Search for this topic without a URL",
        "https://example.com/a https://example.org/b",
        "http://example.com/",
        "https://user:password@example.com/",
        "https://example.com:444/private-service",
        "https://example.com/page?access_token=do-not-send",
        "https://example.com/page?accessToken=do-not-send",
        "https://example.com/page?clientSecret=do-not-send",
        "https://example.com/page?APIKey=do-not-send",
    ),
)
def test_readonly_browser_handler_rejects_unbounded_or_credential_urls(prompt: str) -> None:
    called = False

    async def factory(**_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("invalid browser prompt must not launch a browser")

    async def scenario():
        return await browser_adapters.build_readonly_browser_handler(factory)(_context(prompt))

    result = asyncio.run(scenario())
    assert result.status == "FAILED"
    assert result.error_code == "BROWSER_URL_INVALID"
    assert not called


def test_public_browser_egress_rejects_shared_ip_literal_before_launch(monkeypatch) -> None:
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["launch_playwright_session"])
    monkeypatch.setattr(runtime, "_hostname_resolution_is_safe", lambda _host: True)
    monkeypatch.setitem(__import__("sys").modules, "playwright", SimpleNamespace())
    monkeypatch.setitem(
        __import__("sys").modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: pytest.fail("non-public address reached browser launch")),
    )

    async def scenario() -> None:
        with pytest.raises(ValueError, match="egress policy"):
            await runtime.launch_playwright_session(initial_url="https://100.64.0.1/", read_only=True)

    asyncio.run(scenario())


def test_public_browser_egress_rejects_shared_address_returned_by_dns(monkeypatch) -> None:
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["launch_playwright_session"])
    address = (runtime.socket.AF_INET, runtime.socket.SOCK_STREAM, runtime.socket.IPPROTO_TCP, "", ("100.64.0.1", 0))
    monkeypatch.setattr(runtime.socket, "getaddrinfo", lambda *_args, **_kwargs: [address])

    assert not runtime._hostname_resolution_is_safe("public.example")


def test_playwright_readonly_mode_blocks_non_get_requests_and_external_hosts(monkeypatch) -> None:
    class Route:
        def __init__(self, url: str, method: str) -> None:
            self.request = SimpleNamespace(url=url, method=method)
            self.continued = False
            self.aborted = False

        async def continue_(self) -> None:
            self.continued = True

        async def abort(self) -> None:
            self.aborted = True

    class Page:
        url = "https://example.com/"

        def on(self, _event: str, _callback: object) -> None:
            return None

        async def goto(self, url: str, **_kwargs: object) -> None:
            self.url = url

    class Context:
        def __init__(self) -> None:
            self.options: dict[str, object] = {}
            self.route_handler = None

        async def route(self, _pattern: str, handler: object) -> None:
            self.route_handler = handler

        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            return None

    class Browser:
        def __init__(self) -> None:
            self.context = Context()

        async def new_context(self, **kwargs: object) -> Context:
            self.context.options.update(kwargs)
            return self.context

        async def close(self) -> None:
            return None

    class Launcher:
        launch_args: ClassVar[dict[str, object]] = {}

        async def launch(self, **_kwargs: object) -> Browser:
            self.launch_args.update(_kwargs)
            return Browser()

    class Manager:
        chromium = Launcher()

        async def stop(self) -> None:
            return None

    class Factory:
        async def start(self) -> Manager:
            return Manager()

    monkeypatch.setattr(browser_adapters, "_default_session_factory", lambda **_: None)
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["launch_playwright_session"])
    monkeypatch.setattr(runtime, "_hostname_resolution_is_safe", lambda _host: True)
    resolved_hosts: list[str] = []
    address = (runtime.socket.AF_INET, runtime.socket.SOCK_STREAM, runtime.socket.IPPROTO_TCP, "", ("93.184.216.34", 0))

    def resolve(host: str, *_args: object, **_kwargs: object) -> list[object]:
        resolved_hosts.append(host)
        return [address]

    monkeypatch.setattr(runtime.socket, "getaddrinfo", resolve)
    monkeypatch.setitem(__import__("sys").modules, "playwright", SimpleNamespace())
    monkeypatch.setitem(
        __import__("sys").modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: Factory()),
    )

    async def scenario():
        session = await runtime.launch_playwright_session(
            initial_url="https://example.com/",
            allowed_hosts=("example.com",),
            read_only=True,
        )
        context = session.context
        assert context is not None
        assert context.options == {
            "accept_downloads": False,
            "java_script_enabled": False,
            "service_workers": "block",
        }
        assert Launcher.launch_args["args"] == ["--host-resolver-rules=MAP example.com 93.184.216.34, MAP * ^NOTFOUND"]
        post = Route("https://example.com/submit", "POST")
        await context.route_handler(post)
        assert post.aborted and not post.continued
        external = Route("https://other.example/track", "GET")
        await context.route_handler(external)
        assert external.aborted and not external.continued
        read = Route("https://example.com/read", "GET")
        await context.route_handler(read)
        assert read.continued and not read.aborted
        assert resolved_hosts == ["example.com"]
        await session.close()

    asyncio.run(scenario())


def test_playwright_readonly_mode_rejects_nonstandard_port_before_launch(monkeypatch) -> None:
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["launch_playwright_session"])
    monkeypatch.setattr(runtime, "_hostname_resolution_is_safe", lambda _host: True)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="egress policy"):
            await runtime.launch_playwright_session(
                initial_url="https://example.com:8443/",
                read_only=True,
            )

    asyncio.run(scenario())


def test_playwright_close_attempts_all_owned_resources_and_can_retry_failed_owner() -> None:
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["PlaywrightBrowserRuntimeSession"])
    events: list[str] = []

    class Context:
        attempts = 0

        async def close(self) -> None:
            self.attempts += 1
            events.append("context")
            if self.attempts == 1:
                raise RuntimeError("context close failed")

    class Browser:
        async def close(self) -> None:
            events.append("browser")

    class Manager:
        async def stop(self) -> None:
            events.append("playwright")

    context = Context()
    session = runtime.PlaywrightBrowserRuntimeSession(
        page=object(),
        context=context,
        browser=Browser(),
        playwright=Manager(),
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="context close failed"):
            await session.close()
        assert events == ["context", "browser", "playwright"]
        assert session.context is context
        assert session.browser is None
        assert session.playwright is None

        await session.close()
        assert events == ["context", "browser", "playwright", "context"]
        assert session.context is None

    asyncio.run(scenario())


def test_playwright_runtime_network_log_is_bounded() -> None:
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["PlaywrightBrowserRuntimeSession"])

    class Page:
        def on(self, _event: str, _callback: object) -> None:
            return None

    session = runtime.PlaywrightBrowserRuntimeSession(page=Page())
    for index in range(513):
        session._record_request(
            SimpleNamespace(
                url=f"https://example.com/{index}",
                method="GET",
                resource_type="document",
            )
        )

    assert len(session.network_events) == 512
    assert session.network_events[0]["url"] == "https://example.com/1"
    assert session.network_events[-1]["url"] == "https://example.com/512"


def test_playwright_launch_failure_preserves_primary_error_and_cleans_every_owner(monkeypatch) -> None:
    runtime = __import__("core.python.browser_playwright_runtime", fromlist=["launch_playwright_session"])
    events: list[str] = []
    monkeypatch.setattr(runtime, "_hostname_resolution_is_safe", lambda _host: True)
    monkeypatch.setitem(__import__("sys").modules, "playwright", SimpleNamespace())

    class Page:
        async def goto(self, _url: str) -> None:
            raise RuntimeError("navigation failed")

        def on(self, _event: str, _callback: object) -> None:
            return None

    class Context:
        async def route(self, _pattern: str, _handler: object) -> None:
            return None

        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            events.append("context")
            raise RuntimeError("context cleanup failed")

    class Browser:
        async def new_context(self, **_kwargs: object) -> Context:
            return Context()

        async def close(self) -> None:
            events.append("browser")

    class Launcher:
        async def launch(self, **_kwargs: object) -> Browser:
            return Browser()

    class Manager:
        chromium = Launcher()

        async def stop(self) -> None:
            events.append("playwright")

    class Factory:
        async def start(self) -> Manager:
            return Manager()

    monkeypatch.setitem(
        __import__("sys").modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: Factory()),
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="navigation failed") as caught:
            await runtime.launch_playwright_session(initial_url="https://example.com/")
        assert events == ["context", "browser", "playwright"]
        assert any("context (RuntimeError)" in note for note in caught.value.__notes__)

    asyncio.run(scenario())


def test_playwright_browser_availability_requires_matching_headless_binary(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "site-packages" / "playwright"
    browser_package = package_root / "driver" / "package"
    browser_package.mkdir(parents=True)
    origin = package_root / "__init__.py"
    origin.touch()
    (browser_package / "browsers.json").write_text(
        json.dumps({"browsers": [{"name": "chromium-headless-shell", "revision": "2468"}]}),
        encoding="utf-8",
    )
    browser_root = tmp_path / "browsers"
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: SimpleNamespace(origin=str(origin)))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browser_root))
    assert not browser_adapters.playwright_browser_available()

    executable_name = "chrome-headless-shell.exe" if os.name == "nt" else "chrome-headless-shell"
    executable = browser_root / "chromium_headless_shell-2468" / "chrome-headless-shell-test" / executable_name
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert browser_adapters.playwright_browser_available()
