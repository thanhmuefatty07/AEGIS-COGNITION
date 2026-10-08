"""Minimal HTTP client used by the upstream MCP conformance runner."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

async def _run(server_url: str, scenario: str) -> None:
    endpoint = urlsplit(server_url)
    if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("conformance server must use a loopback HTTP URL")
    from aegis_cognition.extensions import McpHttpConfig, McpHttpProvider

    protocol_version = os.environ.get("MCP_CONFORMANCE_PROTOCOL_VERSION", "2026-07-28")
    config = McpHttpConfig(
        endpoint=server_url,
        allowed_hosts=(endpoint.hostname,),
        allow_local=True,
        protocol_version=protocol_version,
        client_name="aegis-conformance-client",
        client_version="0.1.0",
    )
    async with McpHttpProvider(config) as provider:
        tools = await provider.list_tools()
        if scenario == "tools_call":
            if not any(tool.name == "add_numbers" for tool in tools):
                raise RuntimeError("conformance server did not advertise add_numbers")
            result = await provider.call_tool("add_numbers", {"a": 2, "b": 3})
            if isinstance(result, Mapping) and result.get("isError") is True:
                raise RuntimeError("conformance tool call returned an error")
        elif scenario != "initialize":
            raise RuntimeError(f"unsupported conformance scenario: {scenario}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("the MCP conformance runner must pass the server URL")
    scenario = os.environ.get("MCP_CONFORMANCE_SCENARIO", "")
    try:
        asyncio.run(_run(sys.argv[-1], scenario))
    except Exception as error:
        print(f"MCP conformance client failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
