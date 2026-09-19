from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aegis_cognition.extensions import (
    ExtensionError,
    ExtensionManifest,
    ExtensionRegistry,
    McpHttpConfig,
    McpHttpProvider,
    McpServerSpec,
    McpStdioConfig,
    McpStdioProvider,
    McpToolDescriptor,
    SkillCatalog,
    ToolSpec,
    discover_extension_manifests,
)
from aegis_cognition.application import AgentApplication
from aegis_cognition.config import AgentConfig


def _manifest(*tool_names: str) -> ExtensionManifest:
    return ExtensionManifest(
        extension_id="demo",
        version="1.0.0",
        description="test extension",
        capabilities=("compute",),
        tool_names=tuple(sorted(tool_names)),
    )


def test_registry_keeps_metadata_small_and_dispatches_sync_tools() -> None:
    registry = ExtensionRegistry()
    spec = ToolSpec(
        name="demo.echo",
        description="Return the supplied value",
        effect_class="read_only",
        extension_id="demo",
    )
    registry.register(_manifest(spec.name), ((spec, lambda args: {"value": args["value"]}),))

    catalog = registry.prompt_catalog("return a value", token_budget=64)
    assert "demo.echo" in catalog
    assert "input_schema" not in catalog
    result = asyncio.run(registry.invoke("demo.echo", {"value": "ok"}))
    assert result == {"value": "ok"}


def test_serial_tools_are_not_run_concurrently() -> None:
    registry = ExtensionRegistry()
    active = 0
    maximum = 0

    async def handler(_: dict[str, Any]) -> dict[str, int]:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"ok": 1}

    spec = ToolSpec(
        name="demo.serial",
        description="A serialized test tool",
        effect_class="compute",
        capabilities=("compute",),
        concurrency="serial",
        extension_id="demo",
    )
    registry.register(_manifest(spec.name), ((spec, handler),))

    async def run() -> None:
        await asyncio.gather(registry.invoke(spec.name, {}), registry.invoke(spec.name, {}))

    asyncio.run(run())
    assert maximum == 1

    async def run_again() -> None:
        assert await registry.invoke(spec.name, {}) == {"ok": 1}

    asyncio.run(run_again())


def test_skill_catalog_discovers_metadata_and_detects_stale_body(tmp_path: Path) -> None:
    skill_path = tmp_path / ".agents" / "skills" / "research" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: research\ndescription: Research public sources\nversion: 1.0.0\nkeywords: web, sources\n---\n\nUse sources.\n",
        encoding="utf-8",
    )
    catalog = SkillCatalog.discover((tmp_path,))
    assert [item.name for item in catalog.select_for_task("research web")] == ["research"]
    assert "Use sources." in catalog.load("research")
    skill_path.write_text(skill_path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    with pytest.raises(ExtensionError, match="changed after discovery"):
        catalog.load("research")


def test_mcp_provider_requires_approval_before_activation() -> None:
    class Provider:
        list_calls = 0

        async def list_tools(self) -> tuple[McpToolDescriptor, ...]:
            self.list_calls += 1
            return (McpToolDescriptor("lookup", "Read a value"),)

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> object:
            return {"name": name, "arguments": arguments}

    async def run() -> None:
        registry = ExtensionRegistry()
        provider = Provider()
        server = McpServerSpec("public", "Public provider", approved=False)
        discovered = await registry.register_mcp_provider(server, provider, activate=False)
        assert discovered == ()
        assert provider.list_calls == 0
        with pytest.raises(ExtensionError, match="not approved"):
            await registry.register_mcp_provider(server, provider, activate=True)
        approved = McpServerSpec("public", "Public provider", approved=True)
        await registry.register_mcp_provider(approved, provider, activate=True)
        assert await registry.invoke("mcp.public.lookup", {"q": "aegis"}) == {
            "name": "lookup",
            "arguments": {"q": "aegis"},
        }

    asyncio.run(run())


def test_mcp_stdio_provider_is_not_started_by_preapproval_registration() -> None:
    server_code = "import sys\nfor _line in sys.stdin:\n    pass\n"

    async def run() -> None:
        provider = McpStdioProvider(McpStdioConfig(command=(sys.executable, "-c", server_code)))
        try:
            result = await ExtensionRegistry().register_mcp_provider(
                McpServerSpec("child", "Unapproved child", transport="stdio", approved=False),
                provider,
                activate=False,
            )
            assert result == ()
            assert provider._process is None
        finally:
            await provider.close()

    asyncio.run(run())


def test_mcp_stdio_provider_discovers_and_calls_a_real_child_process() -> None:
    server_code = """
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        response = {"jsonrpc": "2.0", "id": request["id"], "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"}}}
    elif method == "tools/list":
        response = {"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [{"name": "echo", "description": "Echo a value", "inputSchema": {"type": "object"}}]}}
    elif method == "tools/call":
        response = {"jsonrpc": "2.0", "id": request["id"], "result": {"content": [{"type": "text", "text": request["params"]["arguments"]["value"]}]}}
    else:
        continue
    sys.stdout.write(json.dumps(response) + "\\n")
    sys.stdout.flush()
"""

    async def run() -> None:
        provider = McpStdioProvider(McpStdioConfig(command=(sys.executable, "-c", server_code)))
        try:
            registry = ExtensionRegistry()
            specs = await registry.register_mcp_provider(
                McpServerSpec("child", "Local test server", transport="stdio", approved=True),
                provider,
                activate=True,
            )
            assert specs[0].name == "mcp.child.echo"
            result = await registry.invoke(specs[0].name, {"value": "hello"})
            assert result == {"content": [{"text": "hello", "type": "text"}]}
        finally:
            await provider.close()

    asyncio.run(run())


def test_mcp_http_provider_uses_allowlisted_streamable_http_and_recovers_sessions() -> None:
    state = {"session": "", "generation": 0, "force_reinitialize": True, "deleted": False}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send(self, status: int, payload: bytes = b"", *, content_type: str = "application/json", session: str | None = None) -> None:
            self.send_response(status)
            if payload:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
            if session is not None:
                self.send_header("MCP-Session-Id", session)
            self.send_header("Connection", "close")
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def do_DELETE(self) -> None:
            state["deleted"] = self.headers.get("MCP-Session-Id") == state["session"]
            self._send(204)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            method = request.get("method")
            if method == "initialize":
                state["generation"] += 1
                state["session"] = f"session-{state['generation']}"
                result = {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test-http", "version": "1"},
                }
                payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                self._send(200, payload, session=state["session"])
                return
            if method == "notifications/initialized":
                self._send(202)
                return
            if self.headers.get("MCP-Session-Id") != state["session"]:
                self._send(400)
                return
            if method == "tools/list" and state["force_reinitialize"]:
                state["force_reinitialize"] = False
                self._send(404)
                return
            if method == "tools/list":
                result = {"tools": [{"name": "lookup", "description": "Read a value", "inputSchema": {"type": "object"}}]}
                payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                self._send(200, payload, session=state["session"])
                return
            if method == "tools/call":
                result = {"content": [{"type": "text", "text": request["params"]["arguments"]["value"]}]}
                payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                sse = b"data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\n\n"
                sse += b"event: message\ndata: " + payload + b"\n\n"
                self._send(200, sse, content_type="text/event-stream", session=state["session"])
                return
            self._send(400)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/mcp?source=test"

        async def run() -> None:
            provider = McpHttpProvider(
                McpHttpConfig(endpoint=endpoint, allowed_hosts=("127.0.0.1",), allow_local=True)
            )
            registry = ExtensionRegistry()
            try:
                specs = await registry.register_mcp_provider(
                    McpServerSpec("http", "Local HTTP test server", transport="streamable-http", approved=True),
                    provider,
                    activate=True,
                )
                assert specs[0].name == "mcp.http.lookup"
                assert await registry.invoke(specs[0].name, {"value": "hello"}) == {
                    "content": [{"text": "hello", "type": "text"}]
                }
            finally:
                await provider.close()

        asyncio.run(run())
        assert state["generation"] == 2
        assert state["deleted"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mcp_http_config_requires_explicit_host_allowlist_and_local_opt_in() -> None:
    with pytest.raises(ExtensionError, match="allowlisted"):
        McpHttpConfig(endpoint="https://example.com/mcp").validate()
    with pytest.raises(ExtensionError, match="allow_local"):
        McpHttpConfig(endpoint="http://127.0.0.1:8000/mcp", allowed_hosts=("127.0.0.1",)).validate()


def test_extension_manifest_discovery_does_not_activate_code(tmp_path: Path) -> None:
    extension = tmp_path / "extension.toml"
    extension.write_text(
        "[extension]\nid = 'local-search'\nversion = '0.1.0'\ndescription = 'Local search metadata'\ntools = ['local-search.query']\n",
        encoding="utf-8",
    )
    manifests = discover_extension_manifests((tmp_path,))
    assert len(manifests) == 1
    assert manifests[0].trusted is False
    assert manifests[0].tool_names == ("local-search.query",)


def test_agent_application_binds_registry_without_replacing_existing_runner() -> None:
    class LocalModel:
        requires_api_key = False

    registry = ExtensionRegistry()
    spec = ToolSpec(
        name="demo.context",
        description="Read bounded context",
        extension_id="demo",
    )
    registry.register(_manifest(spec.name), ((spec, lambda _: {"ok": True}),))
    config = AgentConfig.from_inputs("find context", llm=LocalModel(), extension_registry=registry)
    application = AgentApplication(config)
    application._prepare_extension_runtime()
    assert config.options["tool_runner"] == registry.tool_runner
    assert "demo.context" in application._build_system_context("find context")

    def existing_runner(_request: object) -> dict[str, bool]:
        return {"existing": True}
    second_config = AgentConfig.from_inputs(
        "find context",
        llm=LocalModel(),
        extension_registry=registry,
        tool_runner=existing_runner,
    )
    second_application = AgentApplication(second_config)
    second_application._prepare_extension_runtime()
    assert second_config.options["tool_runner"] is existing_runner
