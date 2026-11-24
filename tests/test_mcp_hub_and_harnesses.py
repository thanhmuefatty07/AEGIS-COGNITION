"""End-to-end and adversarial tests for the Bidirectional AEGIS MCP Hub & 4-Harness Integration."""

from __future__ import annotations

import asyncio
import http.server
import io
import json
from pathlib import Path
import threading
import pytest

from aegis_cognition.cli import main as cli_main
from aegis_cognition.extensions import (
    COMPACT_MCP_TOOL_SCHEMA_SERIALIZED_BYTES,
    MCP_INTEROP_PROTOCOL_VERSIONS,
    ExtensionError,
    ExtensionManifest,
    ExtensionRegistry,
    McpHttpConfig,
    McpHttpProvider,
    ToolExecutionError,
    ToolSpec,
    compact_mcp_tool_schema,
    dump_cordis_yaml_preserving_js_tags,
    format_canonical_mcp_tool_id,
    load_cordis_patch_preserving_js_tags,
    parse_mcp_harness_config,
    resolve_mcp_harness_config_paths,
    validate_mcp_tool_schema_budget,
)
from aegis_cognition.mcp_server import (
    AegisMcpServer,
    HarnessDelegationAdapter,
    render_harness_mcp_config_snippet,
)


def test_mcp_compact_schema_budget_canonical_id_and_policy_gate() -> None:
    assert COMPACT_MCP_TOOL_SCHEMA_SERIALIZED_BYTES == 13_312
    assert MCP_INTEROP_PROTOCOL_VERSIONS[0] == "2025-11-25"
    assert "2024-11-05" in MCP_INTEROP_PROTOCOL_VERSIONS

    # Canonical tool ID scrubs embedded URL userinfo credentials & normalizes punctuation
    tid = format_canonical_mcp_tool_id("https://alice:supersecret@mcp.example.local:8443/v1", "Run.Query-Fast")
    assert "alice" not in tid and "supersecret" not in tid
    assert tid == "mcp__url__run_query-fast"

    # Compact schema strips verbose descriptions/examples to fit <= 13 KiB
    bloated_schema = {
        "type": "object",
        "description": "A" * 20_000,
        "examples": [{"query": "B" * 5_000}],
        "properties": {
            "query": {
                "type": "string",
                "description": "C" * 8_000,
            }
        },
        "required": ["query"],
    }
    with pytest.raises(ExtensionError, match="serialized"):
        validate_mcp_tool_schema_budget(bloated_schema, profile="compact")

    compacted = compact_mcp_tool_schema(bloated_schema, profile="compact")
    validate_mcp_tool_schema_budget(compacted, profile="compact")
    assert len(json.dumps(compacted, separators=(",", ":")).encode("utf-8")) <= COMPACT_MCP_TOOL_SCHEMA_SERIALIZED_BYTES

    # ExtensionRegistry policy_gate blocks unauthorized invocations fail-closed
    decisions: list[tuple[str, object]] = []

    def _deny_drop(spec: ToolSpec, arguments: object) -> None:
        decisions.append((spec.name, arguments))
        if isinstance(arguments, dict) and arguments.get("action") == "drop":
            raise ToolExecutionError("blocked by policy gate")

    reg = ExtensionRegistry(policy_gate=_deny_drop)
    spec = ToolSpec(
        name="db_admin",
        description="Admin tool",
        input_schema={
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
            "additionalProperties": False,
        },
        extension_id="ext.admin",
    )
    reg.register(
        ExtensionManifest(
            extension_id="ext.admin",
            version="1",
            description="Admin test extension",
            tool_names=("db_admin",),
            source="test",
            trusted=True,
        ),
        ((spec, lambda payload: {"executed": payload}),),
    )

    async def _exercise_gate() -> None:
        ok = await reg.invoke("db_admin", {"action": "status"})
        assert ok == {"executed": {"action": "status"}}
        with pytest.raises(ToolExecutionError, match="blocked by policy gate"):
            await reg.invoke("db_admin", {"action": "drop"})

    asyncio.run(_exercise_gate())
    assert len(decisions) == 2


def test_http_200_minus_32601_legacy_fallback_and_initialize_first_mode(tmp_path: Path) -> None:
    seen_methods: list[str] = []

    class _TrackingMcpServer(AegisMcpServer):
        def handle_jsonrpc_message(
            self,
            request: dict[str, object],
            *,
            session_id: str = "default",
        ) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
            seen_methods.append(str(request.get("method", "")))
            return super().handle_jsonrpc_message(request, session_id=session_id)

    hub = _TrackingMcpServer(workspace_root=tmp_path, catalog_mode="hot-cold")
    with hub.serve_loopback_http(host="127.0.0.1", port=0) as endpoint_url:
        async def _exercise_auto_and_init_first() -> None:
            # 1. `auto` mode sends `server/discover`, receives HTTP 200 JSON-RPC -32601,
            #    and automatically falls back to `initialize` + `notifications/initialized`!
            auto_cfg = McpHttpConfig(
                endpoint=endpoint_url,
                allowed_hosts=("127.0.0.1",),
                allow_local=True,
                handshake_mode="auto",
                protocol_version="2024-11-05",
                accepted_protocol_versions=MCP_INTEROP_PROTOCOL_VERSIONS,
            )
            async with McpHttpProvider(auto_cfg) as client:
                tools = await client.list_tools()
                assert any(t.name == "aegis_hypergraph_query" for t in tools)
            assert seen_methods[:3] == ["server/discover", "initialize", "notifications/initialized"]

            # 2. `initialize-first` mode skips `server/discover` completely!
            seen_methods.clear()
            init_first_cfg = McpHttpConfig(
                endpoint=endpoint_url,
                allowed_hosts=("127.0.0.1",),
                allow_local=True,
                handshake_mode="initialize-first",
                protocol_version="2025-03-26",
                accepted_protocol_versions=MCP_INTEROP_PROTOCOL_VERSIONS,
            )
            async with McpHttpProvider(init_first_cfg) as client2:
                tools2 = await client2.list_tools()
                assert any(t.name == "aegis_discover_tools" for t in tools2)
            assert seen_methods[:2] == ["initialize", "notifications/initialized"]
            assert "server/discover" not in seen_methods

        asyncio.run(_exercise_auto_and_init_first())

    # 3. Broken server returning HTTP 200 -32601 even on `initialize` MUST fail closed in `initialize-first` mode
    class _BrokenHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _fmt: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
            resp = {
                "jsonrpc": "2.0",
                "id": raw.get("id", 1),
                "error": {"code": -32601, "message": "initialize unsupported"},
            }
            encoded = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BrokenHandler)
    b_host, b_port = httpd.server_address[:2]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        broken_cfg = McpHttpConfig(
            endpoint=f"http://{b_host}:{b_port}/mcp",
            allowed_hosts=("127.0.0.1",),
            allow_local=True,
            handshake_mode="initialize-first",
            accepted_protocol_versions=MCP_INTEROP_PROTOCOL_VERSIONS,
        )

        async def _try_broken() -> None:
            async with McpHttpProvider(broken_cfg):
                pass

        with pytest.raises(ExtensionError):
            asyncio.run(_try_broken())
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2.0)


def test_four_harness_configs_cordis_sequence_patch_and_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Sequence-root cordis.patch.yml with !js/regexp and !js/function tags preserved
    cordis_patch_text = """
- insert:
    - plugin: '@deepseek-ai/dsh-mcp-client'
      config:
        allowPattern: !js/regexp /^aegis_/i
        transform: !js/function (x) => x
        mcpServers:
          aegis-hub:
            command: aegis
            args:
              - mcp-serve
"""
    parsed_patch = load_cordis_patch_preserving_js_tags(cordis_patch_text)
    assert isinstance(parsed_patch, list)
    insert_ops = parsed_patch[0]["insert"]
    assert isinstance(insert_ops, list)
    roundtripped_yaml = dump_cordis_yaml_preserving_js_tags(insert_ops[0]["config"])
    assert "js/regexp" in roundtripped_yaml
    assert "js/function" in roundtripped_yaml

    entries = parse_mcp_harness_config("dsh", cordis_patch_text)
    assert len(entries) == 1
    assert entries[0].server_id == "aegis-hub"
    assert entries[0].to_stdio_config().command == ("aegis", "mcp-serve")

    # 2. All 4 rendered snippets parse back cleanly through parse_mcp_harness_config
    for harness in ("claude_code", "codex", "antigravity", "dsh"):
        snippet = render_harness_mcp_config_snippet(harness, server_id="aegis-cognition")
        parsed_entries = parse_mcp_harness_config(harness, snippet)
        assert len(parsed_entries) == 1
        assert parsed_entries[0].server_id == "aegis-cognition"
        stdio_cfg = parsed_entries[0].to_stdio_config()
        assert stdio_cfg.command == ("aegis", "mcp-serve")

    # 3. Environment variable path overrides (CLAUDE_CONFIG_DIR, CODEX_HOME, DSH_HOME)
    claude_dir = tmp_path / "custom-claude"
    codex_dir = tmp_path / "custom-codex"
    dsh_dir = tmp_path / "custom-dsh"
    ws_dir = tmp_path / "workspace"
    for d in (claude_dir, codex_dir, dsh_dir, ws_dir):
        d.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setenv("CODEX_HOME", str(codex_dir))
    monkeypatch.setenv("DSH_HOME", str(dsh_dir))

    c_paths = resolve_mcp_harness_config_paths("claude_code", workspace=ws_dir, home=tmp_path)
    assert claude_dir / ".claude.json" in c_paths
    assert ws_dir / ".mcp.json" in c_paths

    x_paths = resolve_mcp_harness_config_paths("codex", workspace=ws_dir, home=tmp_path)
    assert codex_dir / "config.toml" in x_paths

    d_paths = resolve_mcp_harness_config_paths("dsh", workspace=ws_dir, home=tmp_path)
    assert dsh_dir / "cordis.patch.yml" in d_paths
    assert ws_dir / "cordis.patch.yml" in d_paths

    a_paths = resolve_mcp_harness_config_paths("antigravity", workspace=ws_dir, home=tmp_path)
    assert tmp_path / ".gemini" / "config" / "mcp_config.json" in a_paths


def test_bidirectional_aegis_mcp_server_loopback_http_stdio_and_cli(tmp_path: Path) -> None:
    # Non-loopback bind MUST fail closed
    hub = AegisMcpServer(workspace_root=tmp_path, catalog_mode="hot-cold")
    with pytest.raises(ExtensionError, match="loopback"), hub.serve_loopback_http(host="0.0.0.0", port=0):
        pass

    # Real loopback HTTP round-trip using McpHttpProvider -> AegisMcpServer!
    with hub.serve_loopback_http(host="127.0.0.1", port=0) as endpoint_url:
        async def _run_client_flow() -> None:
            cfg = McpHttpConfig(
                endpoint=endpoint_url,
                allowed_hosts=("127.0.0.1",),
                allow_local=True,
                handshake_mode="initialize-first",
                accepted_protocol_versions=MCP_INTEROP_PROTOCOL_VERSIONS,
            )
            async with McpHttpProvider(cfg) as client:
                tools_initial = await client.list_tools()
                initial_names = {t.name for t in tools_initial}
                # In hot-cold mode, only hot tools are exposed initially
                assert "aegis_discover_tools" in initial_names
                assert "aegis_hypergraph_query" in initial_names
                assert "aegis_hypergraph_ingest" not in initial_names

                # Progressively activate hypergraph cold tools via aegis_discover_tools
                disc = await client.call_tool("aegis_discover_tools", {"query": "hypergraph"})
                assert "aegis_hypergraph_ingest" in json.dumps(disc)
                tools_after = {t.name for t in await client.list_tools()}
                assert "aegis_hypergraph_ingest" in tools_after
                assert "aegis_hypergraph_blast_radius" in tools_after

                # Ingest -> Query -> Blast Radius over live loopback MCP HTTP!
                ingest_out = await client.call_tool(
                    "aegis_hypergraph_ingest",
                    {
                        "nodes": [
                            {
                                "node_id": "sym:seqlock_write",
                                "label": "seqlock_write_stripe",
                                "kind": "symbol",
                                "source_file": "core/rust/src/shm.rs",
                                "content": "Per-stripe seqlock writer with Acquire/Release fences",
                                "corroboration_count": 3,
                            },
                            {
                                "node_id": "sym:mmap_reader",
                                "label": "mmap_bridge_reader",
                                "kind": "symbol",
                                "source_file": "core/rust/src/bridge_mmap.rs",
                                "content": "Zero-copy mmap reader validating seqlock epochs",
                                "corroboration_count": 2,
                            },
                        ],
                        "edges": [
                            {
                                "source": "sym:mmap_reader",
                                "target": "sym:seqlock_write",
                                "relation": "calls",
                                "provenance": "EXTRACTED",
                            }
                        ],
                    },
                )
                assert "ingested" in json.dumps(ingest_out)

                query_out = await client.call_tool(
                    "aegis_hypergraph_query",
                    {"query": "seqlock stripe writer", "top_k": 3},
                )
                assert "sym:seqlock_write" in json.dumps(query_out)

                blast_out = await client.call_tool(
                    "aegis_hypergraph_blast_radius",
                    {"changed_files": ["core\\rust\\src\\shm.rs"], "max_hops": 2},
                )
                blast_str = json.dumps(blast_out)
                assert "sym:seqlock_write" in blast_str
                assert "sym:mmap_reader" in blast_str

        asyncio.run(_run_client_flow())

    # Stdio JSON-RPC transport + list_changed notification emission
    in_buf = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "aegis_discover_tools", "arguments": {"query": "audit"}},
                    }
                ),
            ]
        )
        + "\n"
    )
    out_buf = io.StringIO()
    stdio_hub = AegisMcpServer(workspace_root=tmp_path, catalog_mode="hot-cold")
    stdio_hub.serve_stdio(stdin=in_buf, stdout=out_buf)
    stdio_lines = [json.loads(line) for line in out_buf.getvalue().splitlines() if line.strip()]
    assert stdio_lines[0]["result"]["protocolVersion"] == "2025-03-26"
    assert any(msg.get("method") == "notifications/tools/list_changed" for msg in stdio_lines)

    # CLI dry-run and --print-config exit 0
    assert cli_main(["mcp-serve", "--dry-run", "--catalog", "hot-cold", "--workspace", str(tmp_path)]) == 0
    assert cli_main(["mcp-serve", "--print-config", "codex"]) == 0


def test_harness_delegation_adapter_isolates_and_cleans_disposable_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    primary_file = repo / "main.py"
    primary_file.write_text("print('untouched primary tree')\n", encoding="utf-8")

    seen_sandbox: list[Path] = []

    def _fake_runner(cmd: object, cwd: Path, timeout_s: float) -> tuple[int, str, str]:
        assert isinstance(cmd, tuple)
        assert timeout_s == 30.0
        seen_sandbox.append(cwd)
        # Mutate file ONLY inside disposable sandbox
        (cwd / "main.py").write_text("print('mutated inside disposable sandbox')\n", encoding="utf-8")
        return (0, '{"status":"ok"}', "")

    adapter = HarnessDelegationAdapter(repo, runner=_fake_runner)
    for harness in ("claude_code", "codex", "antigravity", "dsh"):
        outcome = adapter.delegate(harness, "Refactor main.py safely", timeout_seconds=30.0)
        assert outcome.exit_code == 0
        assert outcome.patch_handle.startswith("@patch:")
        assert outcome.isolated_worktree_cleaned is True

    # Primary working tree MUST remain 100% untouched and sandbox directories removed!
    assert primary_file.read_text(encoding="utf-8") == "print('untouched primary tree')\n"
    assert all(not p.exists() for p in seen_sandbox)
