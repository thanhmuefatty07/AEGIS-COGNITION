"""Bidirectional `aegis-mcp` Hub Server & Isolated Multi-Harness Delegation Adapters.

Exposes AEGIS-COGNITION's Memory Hypergraph, `AIP-v2` Multi-Agent Team Bus,
`<goal_round>` anti-drift driver, `AdversarialAuditGate`, and isolated
`DisposableWorktree` harness delegation over standard MCP `stdio` and
loopback-only `streamable-http` (`127.0.0.1` / `::1`).
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import http.server
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any, Literal, TextIO, cast
import uuid

from .agent_efficiency import DisposableWorktree
from .extensions import (
    COMPACT_MCP_TOOL_SCHEMA_SERIALIZED_BYTES,
    MCP_INTEROP_PROTOCOL_VERSIONS,
    ExtensionError,
    attach_untrusted_target_provenance,
    compact_mcp_tool_schema,
    dump_cordis_yaml_preserving_js_tags,
    validate_mcp_tool_schema_budget,
)
from .hypergraph import (
    HypergraphEdgeSpec,
    HypergraphEngine,
    HypergraphNodeSpec,
    NodeKindLiteral,
    ProvenanceKind,
)
from .team_protocol import (
    AdversarialAuditGate,
    AuditVote,
    ChallengeFinding,
    GoalRoundDriver,
    ReconstructionObligationLedger,
    SharedTaskDeltaBoard,
    ToolResultPruner,
    make_compact_handle,
)


AEGIS_MCP_SERVER_NAME = "aegis-cognition-mcp"
AEGIS_MCP_SERVER_VERSION = "0.2.0"

_HOT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "aegis_discover_tools",
        "aegis_hypergraph_query",
        "aegis_team_delta_sync",
        "aegis_goal_round_status",
    }
)


@dataclass(frozen=True, slots=True)
class HubToolDefinition:
    """Validated outbound MCP tool with compact (`<= 13 KiB`) JSON Schema."""

    name: str
    description: str
    input_schema: dict[str, Any]
    hot: bool
    read_only: bool
    handler: Callable[[Mapping[str, Any], _McpSessionState], dict[str, Any]]

    def mcp_descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }


@dataclass(slots=True)
class _McpSessionState:
    session_id: str
    negotiated_version: str = "2025-11-25"
    activated_cold_tools: set[str] = field(default_factory=cast(Callable[[], set[str]], set))
    pending_notifications: list[dict[str, Any]] = field(default_factory=cast(Callable[[], list[dict[str, Any]]], list))


class ProgressiveToolCatalog:
    """Session-aware progressive disclosure (`full` vs `hot-cold` MCP tool catalog)."""

    def __init__(self, mode: Literal["full", "hot-cold"] = "full") -> None:
        if mode not in {"full", "hot-cold"}:
            raise ValueError("catalog mode must be 'full' or 'hot-cold'")
        self.mode = mode
        self._tools: dict[str, HubToolDefinition] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        input_schema: Mapping[str, Any],
        handler: Callable[[Mapping[str, Any], _McpSessionState], dict[str, Any]],
        hot: bool = False,
        read_only: bool = True,
    ) -> None:
        compacted = compact_mcp_tool_schema(
            input_schema,
            max_serialized_bytes=COMPACT_MCP_TOOL_SCHEMA_SERIALIZED_BYTES,
            profile="compact",
        )
        validate_mcp_tool_schema_budget(
            compacted,
            max_serialized_bytes=COMPACT_MCP_TOOL_SCHEMA_SERIALIZED_BYTES,
            profile="compact",
        )
        self._tools[name] = HubToolDefinition(
            name=name,
            description=description.strip()[:512],
            input_schema=compacted,
            hot=hot or (name in _HOT_TOOL_NAMES),
            read_only=read_only,
            handler=handler,
        )

    @property
    def all_tools(self) -> tuple[HubToolDefinition, ...]:
        return tuple(self._tools[k] for k in sorted(self._tools))

    def visible_descriptors(self, session: _McpSessionState) -> list[dict[str, Any]]:
        if self.mode == "full":
            return [tool.mcp_descriptor() for tool in self.all_tools]
        return [
            tool.mcp_descriptor() for tool in self.all_tools if tool.hot or tool.name in session.activated_cold_tools
        ]

    def discover_and_activate(self, query: str, session: _McpSessionState) -> list[str]:
        tokens = [tok for tok in query.casefold().split() if tok]
        newly_activated: list[str] = []
        matched: list[str] = []
        for tool in self.all_tools:
            haystack = f"{tool.name} {tool.description}".casefold()
            if not tokens or any(tok in haystack for tok in tokens):
                matched.append(tool.name)
                if not tool.hot and tool.name not in session.activated_cold_tools:
                    session.activated_cold_tools.add(tool.name)
                    newly_activated.append(tool.name)
        if newly_activated and self.mode == "hot-cold":
            session.pending_notifications.append(
                {"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}}
            )
        return matched

    def get(self, name: str) -> HubToolDefinition | None:
        return self._tools.get(name)


class SlimMcpResponse:
    """Compact JSON formatter + UTF-8 head/tail handle pruner for MCP tool results."""

    def __init__(self, *, max_inline_bytes: int = 8_192) -> None:
        self._pruner = ToolResultPruner(
            max_inline_bytes=max_inline_bytes,
            head_bytes=max(256, max_inline_bytes // 2),
            tail_bytes=max(128, max_inline_bytes // 4),
        )

    def format(self, payload: Mapping[str, Any], *, untrusted: bool = False) -> dict[str, Any]:
        body: Mapping[str, Any] = (
            attach_untrusted_target_provenance(payload, untrusted_content=True, source="aegis_mcp_hub")
            if untrusted
            else dict(payload)
        )
        compact_json = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        pruned = self._pruner.prune(compact_json)
        structured: dict[str, Any] = dict(body)
        if pruned.pruned:
            structured = {
                "pruned": True,
                "artifact_handle": pruned.handle,
                "original_bytes": pruned.original_bytes,
            }
        return {
            "content": [{"type": "text", "text": pruned.compact_text}],
            "structuredContent": structured,
            "isError": False,
        }


@dataclass(frozen=True, slots=True)
class HarnessDelegationResult:
    """Outcome of running an external CLI harness inside an isolated disposable worktree."""

    harness: str
    command: tuple[str, ...]
    exit_code: int
    patch_handle: str
    diff_bytes: int
    changed_files: tuple[str, ...]
    stdout_excerpt: str
    isolated_worktree_cleaned: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "patch_handle": self.patch_handle,
            "diff_bytes": self.diff_bytes,
            "changed_files": list(self.changed_files),
            "stdout_excerpt": self.stdout_excerpt,
            "isolated_worktree_cleaned": self.isolated_worktree_cleaned,
        }


class HarnessDelegationAdapter:
    """Builds non-interactive CLI invocations and executes harnesses inside `DisposableWorktree`."""

    SUPPORTED: tuple[str, ...] = ("claude_code", "codex", "antigravity", "dsh")

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: Callable[[Sequence[str], Path, float], tuple[int, str, str]] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self._runner = runner or self._default_subprocess_runner

    @staticmethod
    def build_cli_command(harness: str, prompt: str, *, executable_override: str | None = None) -> tuple[str, ...]:
        norm = harness.strip().casefold()
        if norm not in HarnessDelegationAdapter.SUPPORTED:
            raise ExtensionError(f"unsupported delegation harness: {harness!r}")
        if not prompt.strip() or "\x00" in prompt or len(prompt) > 16_384:
            raise ExtensionError("delegation prompt must be bounded non-empty text")
        if norm == "claude_code":
            exe = executable_override or "claude"
            return (exe, "-p", prompt, "--output-format", "json")
        if norm == "codex":
            exe = executable_override or "codex"
            return (exe, "exec", "--json", "--ephemeral", prompt)
        if norm == "antigravity":
            exe = executable_override or "agy"
            return (exe, "run", "--headless", "--output", "json", prompt)
        exe = executable_override or "dsh"
        return (exe, "run", "--non-interactive", prompt)

    @staticmethod
    def _default_subprocess_runner(
        command: Sequence[str],
        cwd: Path,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        resolved_exe = shutil.which(command[0])
        if resolved_exe is None:
            return (127, "", f"executable not found on PATH: {command[0]}")
        proc = subprocess.run(
            [resolved_exe, *command[1:]],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return (proc.returncode, proc.stdout, proc.stderr)

    def delegate(
        self,
        harness: str,
        prompt: str,
        *,
        timeout_seconds: float = 60.0,
        executable_override: str | None = None,
    ) -> HarnessDelegationResult:
        if not 1.0 <= timeout_seconds <= 600.0:
            raise ExtensionError("timeout_seconds must be within [1.0, 600.0]")
        cmd = self.build_cli_command(harness, prompt, executable_override=executable_override)
        with DisposableWorktree(self.repo_root) as sandbox:
            exit_code, stdout, stderr = self._runner(cmd, sandbox, timeout_seconds)
            diff_proc = subprocess.run(
                ["git", "diff", "--binary", "HEAD"],
                cwd=str(sandbox),
                capture_output=True,
                check=False,
                timeout=15,
            )
            diff_bytes_raw = diff_proc.stdout if diff_proc.returncode == 0 else b""
            status_proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            changed_files = tuple(
                sorted(
                    line[3:].strip()
                    for line in (status_proc.stdout.splitlines() if status_proc.returncode == 0 else ())
                    if len(line) > 3
                )
            )
        cleaned = not sandbox.exists()
        patch_handle = make_compact_handle("patch", diff_bytes_raw or b"empty")
        combined_out = (stdout or stderr).strip()[:1_024]
        return HarnessDelegationResult(
            harness=harness.strip().casefold(),
            command=cmd,
            exit_code=exit_code,
            patch_handle=patch_handle,
            diff_bytes=len(diff_bytes_raw),
            changed_files=changed_files,
            stdout_excerpt=combined_out,
            isolated_worktree_cleaned=cleaned,
        )


def render_harness_mcp_config_snippet(
    harness: str,
    *,
    server_id: str = "aegis-cognition",
    python_executable: str | None = None,
) -> str:
    """Generate an exact, valid MCP client configuration snippet for any of the 4 target harnesses."""

    norm = harness.strip().casefold()
    py_cmd = python_executable or "aegis"
    args = ["mcp-serve"] if py_cmd == "aegis" else ["-m", "aegis_cognition.cli", "mcp-serve"]
    if norm in {"claude_code", "antigravity"}:
        return json.dumps(
            {
                "mcpServers": {
                    server_id: {
                        "command": py_cmd,
                        "args": args,
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
    if norm == "codex":
        escaped_cmd = json.dumps(py_cmd)
        escaped_args = json.dumps(args)
        return f"[mcp_servers.{server_id}]\ncommand = {escaped_cmd}\nargs = {escaped_args}\n"
    if norm == "dsh":
        cfg_doc: dict[str, object] = {
            "mcpServers": {
                server_id: {
                    "command": py_cmd,
                    "args": args,
                }
            }
        }
        # Serialize sequence-root Cordis patch YAML cleanly
        lines = ["- insert:", "    - plugin: '@deepseek-ai/dsh-mcp-client'", "      config:"]
        cfg_yaml = dump_cordis_yaml_preserving_js_tags(cfg_doc)
        lines.extend(f"        {line}" for line in cfg_yaml.splitlines())
        return "\n".join(lines) + "\n"
    raise ExtensionError(f"unsupported harness for snippet generation: {harness!r}")


class AegisMcpServer:
    """Bidirectional MCP Hub Server supporting `stdio` and loopback `streamable-http`."""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        hypergraph: HypergraphEngine | None = None,
        task_board: SharedTaskDeltaBoard | None = None,
        goal_driver: GoalRoundDriver | None = None,
        audit_ledger: ReconstructionObligationLedger | None = None,
        delegation_adapter: HarnessDelegationAdapter | None = None,
        catalog_mode: Literal["full", "hot-cold"] = "full",
    ) -> None:
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.hypergraph = hypergraph or HypergraphEngine()
        self.task_board = task_board or SharedTaskDeltaBoard()
        self.goal_driver = goal_driver or GoalRoundDriver(
            "aegis-root-goal",
            "Maintain coherent multi-agent verification and hypergraph memory invariants.",
        )
        self.audit_ledger = audit_ledger or ReconstructionObligationLedger()
        self.delegation_adapter = delegation_adapter or HarnessDelegationAdapter(self.workspace_root)
        self.catalog = ProgressiveToolCatalog(mode=catalog_mode)
        self.slim = SlimMcpResponse()
        self._sessions: dict[str, _McpSessionState] = {}
        self._external_to_int_id: dict[str, int] = {}
        self._int_to_external_id: dict[int, str] = {}
        self._next_node_id = max(self.hypergraph.nodes.keys(), default=0) + 1
        self._next_edge_id = max(self.hypergraph.hyperedges.keys(), default=0) + 1
        self._lock = threading.RLock()
        self._register_builtin_tools()

    def _resolve_or_assign_node_id(self, ext_id: str) -> int:
        key = ext_id.strip()
        if not key:
            raise ExtensionError("node_id must be non-empty")
        existing = self._external_to_int_id.get(key)
        if existing is not None:
            return existing
        if key.isdigit() and int(key) > 0 and int(key) not in self._int_to_external_id:
            assigned = int(key)
            self._next_node_id = max(self._next_node_id, assigned + 1)
        else:
            while self._next_node_id in self.hypergraph.nodes or self._next_node_id in self._int_to_external_id:
                self._next_node_id += 1
            assigned = self._next_node_id
            self._next_node_id += 1
        self._external_to_int_id[key] = assigned
        self._int_to_external_id[assigned] = key
        return assigned

    def _get_or_create_session(self, session_id: str | None = None) -> _McpSessionState:
        with self._lock:
            sid = session_id or "default"
            session = self._sessions.get(sid)
            if session is None:
                session = _McpSessionState(session_id=sid)
                self._sessions[sid] = session
            return session

    def _register_builtin_tools(self) -> None:
        self.catalog.register(
            name="aegis_discover_tools",
            description="Search and progressively activate cold tools on this AEGIS MCP session.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 256}},
                "required": ["query"],
                "additionalProperties": False,
            },
            hot=True,
            read_only=True,
            handler=self._tool_discover,
        )
        self.catalog.register(
            name="aegis_hypergraph_ingest",
            description="Ingest nodes and edges into the shared AEGIS Memory Hypergraph with 10-stage deduplication.",
            input_schema={
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "node_id": {"type": "string"},
                                "label": {"type": "string"},
                                "kind": {"type": "string"},
                                "source_file": {"type": "string"},
                                "content": {"type": "string"},
                                "corroboration_count": {"type": "integer", "minimum": 1},
                            },
                            "required": ["node_id", "label"],
                        },
                    },
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                                "relation": {"type": "string"},
                                "provenance": {"type": "string", "enum": ["EXTRACTED", "INFERRED", "AMBIGUOUS"]},
                            },
                            "required": ["source", "target", "relation"],
                        },
                    },
                },
                "required": ["nodes"],
                "additionalProperties": False,
            },
            hot=False,
            read_only=False,
            handler=self._tool_hypergraph_ingest,
        )
        self.catalog.register(
            name="aegis_hypergraph_query",
            description="Run hybrid BM25 + quantized cosine + Personalized PageRank + reflect query on the Memory Hypergraph.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            hot=True,
            read_only=True,
            handler=self._tool_hypergraph_query,
        )
        self.catalog.register(
            name="aegis_hypergraph_blast_radius",
            description="Compute repo-relative affected_nodes blast radius, god_nodes, and surprising_connections.",
            input_schema={
                "type": "object",
                "properties": {
                    "changed_files": {"type": "array", "items": {"type": "string"}},
                    "max_hops": {"type": "integer", "minimum": 1, "maximum": 6},
                },
                "required": ["changed_files"],
                "additionalProperties": False,
            },
            hot=False,
            read_only=True,
            handler=self._tool_hypergraph_blast_radius,
        )
        self.catalog.register(
            name="aegis_team_task_upsert",
            description="Claim or update a shared multi-agent task with hierarchical path/file/symbol lease conflict checks.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "minimum": 1},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "blocked", "completed", "failed"],
                    },
                    "owner_agent": {"type": "string"},
                    "blocked_by": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                    "exclusive_resources": {"type": "array", "items": {"type": "string"}},
                    "compact_handles": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["task_id", "status"],
                "additionalProperties": False,
            },
            hot=False,
            read_only=False,
            handler=self._tool_team_task_upsert,
        )
        self.catalog.register(
            name="aegis_team_delta_sync",
            description="Fetch compact incremental multi-agent task/lease deltas since `since_seq`.",
            input_schema={
                "type": "object",
                "properties": {"since_seq": {"type": "integer", "minimum": 0}},
                "additionalProperties": False,
            },
            hot=True,
            read_only=True,
            handler=self._tool_team_delta_sync,
        )
        self.catalog.register(
            name="aegis_goal_round_status",
            description="Read or validate the active `<goal_round>` anti-drift anchor `(goal_id, revision, round)`.",
            input_schema={
                "type": "object",
                "properties": {
                    "expected_revision": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
            hot=True,
            read_only=True,
            handler=self._tool_goal_round_status,
        )
        self.catalog.register(
            name="aegis_audit_evaluate",
            description="Run blinded adversarial audit consensus with single-counterexample veto and floor(2N/3)+1 quorum.",
            input_schema={
                "type": "object",
                "properties": {
                    "spec_text": {"type": "string"},
                    "diff_hunks": {"type": "array", "items": {"type": "string"}},
                    "evidence_handles": {"type": "array", "items": {"type": "string"}},
                    "raw_claims": {"type": "array", "items": {"type": "string"}},
                    "author_agent_id": {"type": "string"},
                    "total_auditors": {"type": "integer", "minimum": 1},
                    "votes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "auditor_id": {"type": "string"},
                                "approved": {"type": "boolean"},
                                "verified_receipt_ids": {"type": "array", "items": {"type": "string"}},
                                "fatal_counterexample": {"type": "string"},
                                "reproducible_check": {"type": "string"},
                            },
                            "required": ["auditor_id", "approved"],
                        },
                    },
                },
                "required": ["spec_text", "total_auditors", "votes"],
                "additionalProperties": False,
            },
            hot=False,
            read_only=True,
            handler=self._tool_audit_evaluate,
        )
        self.catalog.register(
            name="aegis_harness_config_snippet",
            description="Render MCP configuration snippet for claude_code, codex, antigravity, or dsh.",
            input_schema={
                "type": "object",
                "properties": {
                    "harness": {
                        "type": "string",
                        "enum": ["claude_code", "codex", "antigravity", "dsh"],
                    },
                    "server_id": {"type": "string"},
                },
                "required": ["harness"],
                "additionalProperties": False,
            },
            hot=False,
            read_only=True,
            handler=self._tool_harness_snippet,
        )
        self.catalog.register(
            name="aegis_delegate_harness",
            description="Delegate a bounded task to claude_code, codex, antigravity, or dsh in a DisposableWorktree.",
            input_schema={
                "type": "object",
                "properties": {
                    "harness": {
                        "type": "string",
                        "enum": ["claude_code", "codex", "antigravity", "dsh"],
                    },
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 8192},
                    "timeout_seconds": {"type": "number", "minimum": 1.0, "maximum": 600.0},
                },
                "required": ["harness", "prompt"],
                "additionalProperties": False,
            },
            hot=False,
            read_only=False,
            handler=self._tool_delegate_harness,
        )

    def _tool_discover(self, args: Mapping[str, Any], session: _McpSessionState) -> dict[str, Any]:
        query = str(args.get("query", ""))
        matched = self.catalog.discover_and_activate(query, session)
        return {"matched_tools": matched, "visible_count": len(self.catalog.visible_descriptors(session))}

    def _tool_hypergraph_ingest(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        raw_nodes = cast(Sequence[object], args.get("nodes", []))
        raw_edges = cast(Sequence[object], args.get("edges", []))
        valid_kinds = {"symbol", "file", "module", "concept", "memory", "session", "task", "evidence"}
        with self._lock:
            ingested_nodes = 0
            for item_raw in raw_nodes:
                if not isinstance(item_raw, Mapping) or "node_id" not in item_raw or "label" not in item_raw:
                    continue
                item = cast(Mapping[str, Any], item_raw)
                nid = self._resolve_or_assign_node_id(str(item["node_id"]))
                raw_kind = str(item.get("kind", "concept")).strip().casefold()
                kind = cast(NodeKindLiteral, raw_kind if raw_kind in valid_kinds else "concept")
                src_file = str(item.get("source_file", "")).strip() or None
                self.hypergraph.upsert_node(
                    HypergraphNodeSpec(
                        node_id=nid,
                        label=str(item["label"]),
                        kind=kind,
                        summary=str(item.get("content", item["label"])),
                        source_file=src_file,
                    )
                )
                corr = int(cast(int | float | str, item.get("corroboration_count", 0)))
                for step in range(max(0, min(corr, 8))):
                    self.hypergraph.record_reflect(nid, supporting=True, observed_at_ms=1_700_000_000_000 + step)
                ingested_nodes += 1

            ingested_edges = 0
            for edge_raw in raw_edges:
                if (
                    not isinstance(edge_raw, Mapping)
                    or "source" not in edge_raw
                    or "target" not in edge_raw
                    or "relation" not in edge_raw
                ):
                    continue
                edge = cast(Mapping[str, Any], edge_raw)
                src_id = self._resolve_or_assign_node_id(str(edge["source"]))
                tgt_id = self._resolve_or_assign_node_id(str(edge["target"]))
                if src_id == tgt_id or src_id not in self.hypergraph.nodes or tgt_id not in self.hypergraph.nodes:
                    continue
                raw_prov = str(edge.get("provenance", "EXTRACTED")).strip().upper()
                prov = cast(
                    ProvenanceKind, raw_prov if raw_prov in {"EXTRACTED", "INFERRED", "AMBIGUOUS"} else "EXTRACTED"
                )
                eid = self._next_edge_id
                self._next_edge_id += 1
                self.hypergraph.upsert_edge(
                    HypergraphEdgeSpec(
                        hyperedge_id=eid,
                        relation=str(edge["relation"]),
                        sources=(src_id,),
                        targets=(tgt_id,),
                        provenance=prov,
                    )
                )
                ingested_edges += 1

            report = self.hypergraph.analyze(deduplicate=True, cluster_leiden=True)
            return {
                "ingested": True,
                "ingested_nodes": ingested_nodes,
                "ingested_edges": ingested_edges,
                "active_node_count": report["node_count"],
                "active_hyperedge_count": report["hyperedge_count"],
                "authority": report.get("authority", "python_fallback"),
            }

    def _tool_hypergraph_query(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        query = str(args["query"])
        top_k = int(args.get("top_k", 8))
        with self._lock:
            report = self.hypergraph.analyze(
                deduplicate=True,
                cluster_leiden=True,
                query=query,
                top_k=top_k,
            )
            hits_raw = report.get("retrieval_hits", [])
            return {
                "authority": report.get("authority", "python_fallback"),
                "hits": [
                    {
                        "node_id": self._int_to_external_id.get(int(h["node_id"]), str(h["node_id"])),
                        "label": h["label"],
                        "score": round(float(h.get("fused_score", 0.0)), 6),
                        "origin_tier": h.get("origin_tier", "HybridFused"),
                        "reflect_disposition": h.get("reflect_disposition", "NEUTRAL_UNVERIFIED"),
                    }
                    for h in hits_raw
                ],
            }

    def _tool_hypergraph_blast_radius(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        changed = [str(p) for p in args.get("changed_files", [])]
        max_hops = int(args.get("max_hops", 2))
        with self._lock:
            report = self.hypergraph.analyze(
                deduplicate=True,
                cluster_leiden=True,
                changed_paths=changed,
                blast_max_hops=max_hops,
                top_k=5,
            )
            return {
                "affected_nodes": [
                    self._int_to_external_id.get(int(a["node_id"]), str(a["node_id"]))
                    for a in report.get("affected_nodes", [])
                ],
                "god_nodes": [
                    self._int_to_external_id.get(int(g["node_id"]), str(g["node_id"]))
                    for g in report.get("god_nodes", [])
                ],
                "surprising_count": len(report.get("surprising_connections", [])),
            }

    def _tool_team_task_upsert(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        rec = self.task_board.upsert_task(
            int(args["task_id"]),
            status=str(args["status"]),
            owner_agent=cast(str | None, args.get("owner_agent")),
            blocked_by=[int(x) for x in args.get("blocked_by", [])],
            exclusive_resources=[str(x) for x in args.get("exclusive_resources", [])],
            compact_handles=[str(x) for x in args.get("compact_handles", [])],
        )
        return rec.as_dict()

    def _tool_team_delta_sync(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        since_seq = int(args.get("since_seq", 0))
        delta = self.task_board.delta_since(since_seq)
        return {
            "latest_seq": delta.latest_seq,
            "resync_required": delta.resync_required,
            "changed_tasks": [t.as_dict() for t in delta.changed_tasks],
        }

    def _tool_goal_round_status(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        anchor = self.goal_driver.current_anchor
        expected_rev = args.get("expected_revision")
        if expected_rev is not None:
            self.goal_driver.validate_agent_turn(
                goal_id=anchor.goal_id,
                revision=int(expected_rev),
                round_index=anchor.round_index,
            )
        return {
            "goal_id": anchor.goal_id,
            "revision": anchor.revision,
            "round": anchor.round_index,
            "objective_digest": anchor.objective_digest,
            "tag": anchor.render_tag(),
        }

    def _tool_audit_evaluate(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        packet = AdversarialAuditGate.blind_submission(
            spec_text=str(args["spec_text"]),
            diff_hunks=[str(x) for x in args.get("diff_hunks", [])],
            evidence_handles=[str(x) for x in args.get("evidence_handles", [])],
            raw_claims=[str(x) for x in cast(Sequence[object], args.get("raw_claims", []))],
            author_agent_id=cast(str | None, args.get("author_agent_id")),
        )
        votes: list[AuditVote] = []
        for raw_vote in cast(Sequence[object], args.get("votes", [])):
            if not isinstance(raw_vote, Mapping):
                continue
            vote_map = cast(Mapping[str, Any], raw_vote)
            findings: list[ChallengeFinding] = []
            fatal_ce = str(vote_map.get("fatal_counterexample", "")).strip()
            if fatal_ce:
                findings.append(
                    ChallengeFinding(
                        severity="FATAL_COUNTEREXAMPLE",
                        description=fatal_ce,
                        reproducible_check=str(vote_map.get("reproducible_check", "pytest")),
                    )
                )
            votes.append(
                AuditVote(
                    auditor_id=str(vote_map["auditor_id"]),
                    submission_digest=packet.submission_digest,
                    approved=bool(vote_map["approved"]),
                    verified_receipt_ids=tuple(
                        str(r) for r in cast(Sequence[object], vote_map.get("verified_receipt_ids", []))
                    ),
                    findings=tuple(findings),
                )
            )
        verdict = self.audit_ledger.evaluate_consensus(
            packet=packet,
            votes=votes,
            total_auditors=int(args["total_auditors"]),
        )
        return {
            "status": verdict.status,
            "admitted": verdict.admitted,
            "required_quorum": verdict.required_quorum,
            "approved_votes": verdict.approved_votes,
            "missing_obligations": list(verdict.missing_obligation_ids),
            "ledger_digest": verdict.ledger_digest,
        }

    def _tool_harness_snippet(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        harness = str(args["harness"])
        server_id = str(args.get("server_id", "aegis-cognition"))
        return {
            "harness": harness,
            "config_snippet": render_harness_mcp_config_snippet(harness, server_id=server_id),
        }

    def _tool_delegate_harness(self, args: Mapping[str, Any], _session: _McpSessionState) -> dict[str, Any]:
        outcome = self.delegation_adapter.delegate(
            str(args["harness"]),
            str(args["prompt"]),
            timeout_seconds=float(args.get("timeout_seconds", 60.0)),
        )
        return outcome.as_dict()

    def handle_jsonrpc_message(
        self,
        request: Mapping[str, Any],
        *,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Process one JSON-RPC 2.0 request/notification and return `(response_or_none, notifications)`."""

        session = self._get_or_create_session(session_id)
        req_id = request.get("id")
        method = request.get("method")
        raw_params = request.get("params")
        params: Mapping[str, Any] = cast(Mapping[str, Any], raw_params) if isinstance(raw_params, Mapping) else {}
        if type(method) is not str:
            if req_id is None:
                return (None, [])
            return (
                {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "invalid JSON-RPC method"}},
                [],
            )

        # Notifications (no `id`)
        if req_id is None:
            return (None, [])

        if method == "initialize":
            requested_version = str(params.get("protocolVersion", "2025-11-25"))
            selected_version = (
                requested_version
                if requested_version in MCP_INTEROP_PROTOCOL_VERSIONS
                else MCP_INTEROP_PROTOCOL_VERSIONS[0]
            )
            session.negotiated_version = selected_version
            return (
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": selected_version,
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {
                            "name": AEGIS_MCP_SERVER_NAME,
                            "version": AEGIS_MCP_SERVER_VERSION,
                        },
                    },
                },
                [],
            )

        if method == "ping":
            return ({"jsonrpc": "2.0", "id": req_id, "result": {}}, [])

        if method == "tools/list":
            return (
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": self.catalog.visible_descriptors(session)},
                },
                [],
            )

        if method == "tools/call":
            tool_name = params.get("name")
            raw_args = params.get("arguments", {})
            if type(tool_name) is not str or not isinstance(raw_args, Mapping):
                return (
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "invalid tools/call parameters"},
                    },
                    [],
                )
            tool_def = self.catalog.get(tool_name)
            if tool_def is None:
                return (
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"unknown tool: {tool_name}"},
                    },
                    [],
                )
            try:
                output = tool_def.handler(cast(Mapping[str, Any], raw_args), session)
                formatted = self.slim.format(output)
            except Exception as error:
                formatted = {
                    "content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}],
                    "isError": True,
                }
            notifications = list(session.pending_notifications)
            session.pending_notifications.clear()
            return ({"jsonrpc": "2.0", "id": req_id, "result": formatted}, notifications)

        return (
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            },
            [],
        )

    def serve_stdio(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        """Run synchronous newline-delimited JSON-RPC MCP loop over `stdio`."""

        in_stream = stdin if stdin is not None else sys.stdin
        out_stream = stdout if stdout is not None else sys.stdout
        session_id = f"stdio-{uuid.uuid4().hex[:12]}"
        for raw_line in in_stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                out_stream.write(json.dumps(err, separators=(",", ":")) + "\n")
                out_stream.flush()
                continue
            if not isinstance(decoded, Mapping):
                continue
            response, notifications = self.handle_jsonrpc_message(
                cast(Mapping[str, Any], decoded), session_id=session_id
            )
            for note in notifications:
                out_stream.write(json.dumps(note, ensure_ascii=False, separators=(",", ":")) + "\n")
            if response is not None:
                out_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            out_stream.flush()

    @contextmanager
    def serve_loopback_http(self, *, host: str = "127.0.0.1", port: int = 0) -> Generator[str]:
        """Start a bounded loopback-only Streamable HTTP MCP endpoint, yielding its URL."""

        try:
            addr = ipaddress.ip_address(host)
        except ValueError as error:
            raise ExtensionError("MCP HTTP server host must be an explicit IP literal") from error
        if not addr.is_loopback:
            raise ExtensionError("AegisMcpServer HTTP transport is restricted to loopback addresses")

        hub = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_DELETE(self) -> None:
                self.send_response(204)
                self.end_headers()

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4 * 1024 * 1024:
                    self.send_response(400)
                    self.end_headers()
                    return
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except UnicodeDecodeError, json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                if not isinstance(payload, Mapping):
                    self.send_response(400)
                    self.end_headers()
                    return
                sid = self.headers.get("Mcp-Session-Id") or f"http-{uuid.uuid4().hex[:12]}"
                response, _notes = hub.handle_jsonrpc_message(cast(Mapping[str, Any], payload), session_id=sid)
                if response is None:
                    self.send_response(202)
                    self.send_header("Mcp-Session-Id", sid)
                    self.end_headers()
                    return
                encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Mcp-Session-Id", sid)
                self.end_headers()
                self.wfile.write(encoded)

        httpd = http.server.ThreadingHTTPServer((host, port), _Handler)
        bound_host, bound_port = httpd.server_address[:2]
        thread = threading.Thread(target=httpd.serve_forever, name="aegis-mcp-http", daemon=True)
        thread.start()
        try:
            yield f"http://{bound_host}:{bound_port}/mcp"
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2.0)


__all__ = [
    "AEGIS_MCP_SERVER_NAME",
    "AEGIS_MCP_SERVER_VERSION",
    "AegisMcpServer",
    "HarnessDelegationAdapter",
    "HarnessDelegationResult",
    "HubToolDefinition",
    "ProgressiveToolCatalog",
    "SlimMcpResponse",
    "render_harness_mcp_config_snippet",
]
