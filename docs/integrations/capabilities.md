# Capability extensions

This document describes the local extension boundary for tools, skills, and
MCP-backed capabilities. It is an adapter around the existing Lab runtime; it
is not a second execution engine.

## What is implemented

`aegis_cognition.extensions.ExtensionRegistry` provides four bounded pieces:

1. `ToolSpec` describes a callable capability for the model: name, input
   schema, effect class, timeout, concurrency mode, and result-size limit.
2. `SkillCatalog` discovers `SKILL.md` metadata without importing code. Skill
   bodies are loaded only when explicitly requested and are hash-checked
   against the discovery snapshot.
3. `discover_extension_manifests()` reads `extension.toml` metadata only. It
   never imports or executes an extension.
4. `McpStdioProvider` implements the local MCP stdio request/response subset:
   it starts an explicitly supplied child command, performs the initialize
   handshake, discovers `tools/list`, and calls `tools/call`. Activation still
   requires an explicitly approved server. The command, environment, and
   process lifetime remain host-owned.
5. `McpHttpProvider` implements bounded MCP Streamable HTTP request/response:
   it supports JSON and finite SSE responses, protocol negotiation, session
   identifiers, session reinitialization after `404`, and explicit session
   cleanup. Remote hosts require an exact host allowlist; local/plain-HTTP
   endpoints require explicit opt-in and resolve only to loopback addresses.

Tool calls from `AgentApplication` are bound to the existing generic tool cell,
so Lab admission, AESE checks, telemetry, and settlement remain the execution
boundary. The registry only performs metadata selection, lookup, timeout
handling, serialization bounds, and per-tool concurrency control.

## Minimal example

```python
from aegis_cognition.extensions import ExtensionManifest, ExtensionRegistry, ToolSpec

registry = ExtensionRegistry()
spec = ToolSpec(
    name="local.search",
    description="Search the approved local index",
    input_schema={"type": "object"},
    effect_class="read_only",
    extension_id="local-search",
)
registry.register(
    ExtensionManifest(
        extension_id="local-search",
        version="1.0.0",
        description="Approved local search capability",
        capabilities=("read_only",),
        tool_names=(spec.name,),
    ),
    ((spec, search_handler),),
)
```

Pass the registry as `extension_registry` in `AgentConfig.from_inputs(...)`.
The application adds only a compact capability catalog to the model context;
it does not inject full skill files or every tool schema into every turn.

## Activation and safety rules

- Discovery is metadata-only and bounded by file count, depth, and byte size.
- Untrusted manifests are not executable registrations.
- Tool arguments and results must be JSON-compatible and remain within their
  declared byte limits.
- Serial tools are serialized per event loop; parallel tools may use the
  existing Lab scheduling boundary.
- Live MCP transports are not contacted before approval: a pre-approval
  registration is a strict no-op after provider-shape validation. Activation
  connects only after the host marks the server approved.
- The stdio transport never uses a shell, rejects non-JSON stdout, bounds each
  message, rejects unsupported server-initiated requests, and drains bounded
  stderr so a noisy child cannot block the protocol pipe.
- The HTTP transport does not follow redirects, does not read ambient browser
  cookies, pins the request to an address resolved after allowlist validation,
  rejects private/reserved DNS results by default, bounds headers and bodies,
  and accepts credentials only through explicit configuration headers.
- This layer does not claim OS-level isolation, secret management, remote
  authentication, or full server-initiated MCP request handling. Those controls
  belong to the host/sandbox integration and must be verified separately.

## Current boundary

The registry is intentionally a small compatibility seam. It does not provide
a general dynamic Python plugin loader, long-lived MCP notification streams, or
server-initiated MCP requests. Adding those would expand the trust boundary and
requires separate review of process isolation, credentials, cancellation,
retries, DNS-rebinding protection, sessions, and audit logging.

The HTTP boundary follows the stable MCP transport/lifecycle requirements:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle

The relevant focused verification is:

```text
uv run --locked --extra all --extra dev pytest tests/test_extensions.py -q
```
