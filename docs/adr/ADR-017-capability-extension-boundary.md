# ADR-017: Capability extension boundary

## Status

Accepted for the current local-first runtime.

## Context

The project needs tools, skills, MCP servers, browser/vision adapters, and
subagents without creating a second execution engine or allowing an arbitrary
plugin import to bypass Lab admission. The current runtime already owns
generic tool execution, resource accounting, browser evidence, and skill
settlement. An extension layer therefore needs to describe capabilities and
bind them to those existing owners.

DeepSeek Harness groups capabilities by family and separates service
definitions, providers, and consumers. Its package map explicitly separates
web, computer use, browser use, skills, subagents, sandbox, extensions, and
MCP. We adopt that ownership principle, not its JavaScript runtime or package
layout: AEGIS keeps Python/Rust authority and uses one registry as the
compatibility seam.

## Decision

- `ExtensionManifest` and `ToolSpec` own bounded metadata and model-facing
  descriptions.
- `SkillCatalog` discovers Markdown metadata lazily; the existing Lab
  `SkillRegistry` remains the execution and evidence authority.
- MCP stdio and Streamable HTTP are transport adapters implementing the same
  `McpToolProvider` seam. They become registry tools only after explicit
  approval and activation.
- The HTTP adapter requires an exact host allowlist, blocks private/reserved
  DNS results by default, does not follow redirects or read ambient cookies,
  and bounds headers, bodies, tool counts, and timeouts.
- The application binds the registry to the existing generic tool cell. It
  never replaces an already-owned runner, and it does not create a parallel
  admission or settlement path.
- Dynamic in-process Python imports are intentionally not supported. A future
  plugin process must use a bounded protocol (MCP or an equivalent signed
  worker boundary) and must pass the same approval, resource, and audit gates.

## Consequences

This keeps the common path small and cross-platform while allowing local
subprocess and remote MCP integrations. Discovery is cheap and token-bounded,
but live transports are not probed before approval. Long-lived MCP notification
streams and server-initiated requests remain explicit deferred work rather than
being silently treated as supported.

## Migration, security, performance, operations, rollback

Migration is additive: existing tool runners, skill registries, desktop
commands, and persisted records remain authoritative. Callers can construct an
`ExtensionRegistry` without changing the legacy `Agent.run()` path. Removing
the registry wiring and its focused tests is the rollback path; no persisted
schema migration is required.

Security is fail-closed at the trust boundary. Discovery never executes
extension code, live MCP connections require explicit approval, stdio commands
are argument-vector based, HTTP hosts are allowlisted, private DNS results and
redirects are rejected by default, ambient cookies are not read, and payloads,
headers, tools, and timeouts are bounded. This layer does not claim OS-level
isolation, remote authentication, secret storage, or complete MCP server
request handling; those remain host/sandbox responsibilities.

Performance is bounded rather than claimed universally: metadata discovery and
prompt catalogs avoid loading full skill bodies, tool results have size limits,
and serial/parallel behavior is explicit per tool. No speedup or token saving
claim is made without workload measurement. Operations must record the host
approval decision and transport failure; noisy or malformed providers fail
closed instead of silently entering the model context.

The change is reversible because it adds an adapter seam rather than replacing
the existing execution engine. If a provider misbehaves, unregistering or
disabling that provider leaves the existing runtime, skill settlement, and
desktop protocol available. Long-lived streams, signed package installation,
and automatic updates remain out of scope until their separate recovery,
license, approval, and audit contracts are specified.

## Evidence

- DeepSeek Harness package map and service/provider/consumer guidance:
  https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/README.md
- MCP transport requirements, session handling, and DNS-rebinding warning:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- MCP initialization, negotiation, and timeout requirements:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- Local verification: `tests/test_extensions.py`, including real stdio and
  local Streamable HTTP servers.
