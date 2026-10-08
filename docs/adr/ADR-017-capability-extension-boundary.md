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
- Generic prompt catalogs stay ranked and bounded. If tools overflow the
  visible list and capacity remains without consuming the final user-tool
  slot, a read-only `aegis.tools.search` returns paginated metadata pinned to
  one catalog revision; `aegis.tools.schema` remains the source of exact
  argument contracts. Neither lookup authorizes the selected tool. Desktop
  continues to use provider-native discovery rather than exposing duplicate
  generic lookup tools.
- `SkillCatalog` discovers Markdown metadata lazily; the existing Lab
  `SkillRegistry` remains the execution and evidence authority.
- The host exposes a bounded `aegis.skills.read_resource` tool only when a
  skill is enabled. It reads UTF-8 text relative to that skill's directory,
  verifies the enabled `SKILL.md` hash for each call, returns line-paginated
  excerpts and the current resource hash, and rejects traversal, hidden files,
  links/junctions, binary content, and oversized reads. It is read-only and
  never executes packaged scripts. Reference contents remain untrusted; this
  is not general workspace file access.
- Local extension packs are bounded folders or ZIP archives using
  `extension.toml`, `SKILL.md`, and `mcp.toml` readers. A native AEGIS pack may
  additionally declare a compute-only WASM module in `wasm.toml` plus the
  fixed `plugin.wasm` file. ZIP entries
  are streamed into a temporary directory only after path/type/duplicate checks
  and strict compressed, expanded-byte, and entry-count bounds; the existing
  folder importer then performs its normal validation and atomic install. The
  importer also
  accepts a bounded subset of portable plugin packages (`plugin.json`,
  `mcp.json`) and their `.codex-plugin/plugin.json` / `.mcp.json`
  compatibility layout, translating only identity, skills, and MCP metadata
  into the same catalogs. This includes the supported portable subset of Hermes'
  Agent Plugins package shape (`plugin.json`, `skills/`, and `mcp.json`), not its
  separate native `plugin.yaml` plus Python `register(ctx)` runtime. Native plugin
  code and declared dependencies are not imported or executed by this path.
  Persisted configuration retains only server identity,
  transport, description, and environment-variable names; command/arguments,
  endpoint, headers, literal environment values, raw plugin configuration, and
  `.app.json` mappings are never persisted. For a bounded stdio command without
  recognizable credential markers, or an HTTPS endpoint without URL
  credentials, query, or fragment, import may return a session-only connection
  draft to the renderer. That draft is untrusted, is not activation, and must be
  reviewed before the host's existing connection test and approval flow; this
  conservative filter is not a general secret detector. Package assets,
  scripts, hooks, and approved package documents may be retained as inert files,
  but the source platform's package code and hooks are not executed. Import
  stages and validates the complete pack before an atomic project-local install;
  skills remain disabled, MCP servers remain unapproved and inactive, and WASM
  modules remain uncompiled and unregistered. A separate explicit approval and
  enable action compiles an unchanged WASM module and registers its declared
  exports as `compute` tools. Approval binds the extension manifest, module
  SHA-256, tool schemas/exports, and resource limits. The WASM host disables
  WASI, registers no AEGIS-provided guest host functions, denies all network
  hosts, disables plugin variables, and bounds fuel, memory, wall time, and
  module/input/output bytes. Only Extism's data/input/output ABI functions
  remain available.
  Each tool call receives a fresh guest instance. This intentionally provides
  no filesystem, network, cookie, subprocess, or secret capability; those
  integrations must use separately governed host tools/MCP. It is not claimed
  to be a complete OS-level sandbox or protection from runtime/OS defects.
- MCP stdio and Streamable HTTP are host-owned adapters implementing the same
  `McpToolProvider` seam. They become registry tools only after explicit
  approval and activation. The adapters negotiate current stateless MCP
  requests and fall back to the legacy initialization/session flow only when
  the transport-specific compatibility rules identify a legacy server.
- The MCP adapter implements bounded tool discovery/invocation and paginated
  `tools/list`, `resources/list`, `resources/templates/list`, and
  `resources/read`, plus current per-request metadata and HTTP `Mcp-Method`,
  `Mcp-Name`, and validated `x-mcp-header` parameter mirroring. Resource access
  is represented by host-managed search/read tools for listed resources and
  templates, restricted to the catalog observed at test/activation; catalog
  changes invalidate the tool descriptors. Reads accept listed URIs or URIs
  expanded from listed templates using bounded text variables. Text output is
  capped at 64 KiB across at most eight content blocks; search results are
  capped at 32 KiB with descriptions truncated at 512 characters. Binary
  payloads are omitted, and returned content is labeled untrusted.
- MCP Prompts supports bounded listing and rendering only for an approved,
  active server. Rendered text is untrusted and preview-only: original message
  roles are preserved, and only one user text message may be inserted into the
  composer; the user must still review and send it. Multimodal prompt content
  and user-mediated elicitation remain unsupported. State-only
  `input_required` retries are bounded and do not collect user input. Current
  protocol subscriptions are limited to acknowledged list-change filters and
  resource URIs explicitly requested by the host; server-initiated requests
  remain unsupported and fail visibly rather than execute implicitly.
- Advertised current-protocol tool/resource list-change notifications refresh
  the validated tool/resource catalog in the background. The registry
  replaces a complete server tool set atomically and advances one catalog
  revision; a failed refresh preserves the last-known-good catalog. An automatic read-only grant
  survives only for an unchanged descriptor hash that remains eligible. New or
  changed descriptors do not inherit that grant and remain on per-call
  approval. Provider turns pinned to the previous catalog revision are
  rejected.
- The HTTP adapter requires an exact host allowlist, rejects non-global DNS
  results by default (loopback requires explicit opt-in), does not follow
  redirects or read ambient cookies,
  and bounds headers, bodies, tool counts, and timeouts. For HTTPS servers that
  challenge with OAuth, it supports public-client registration first and
  Authorization Code + PKCE S256 through a loopback callback. If registration
  is unavailable, the user may supply a pre-registered Client ID. Access and
  refresh tokens are stored behind opaque references in the native OS
  credential store; no browser cookies or tool-call replay are used.
- MCP-provided annotations are untrusted server hints and never approve a
  server or activate its transport. Server approval alone does not grant
  automatic tool execution. After a connection test, the user may opt in to
  automatic use of the exact displayed tools with strict boolean
  `readOnlyHint: true` and explicit `destructiveHint: false` (the MCP default
  when omitted is `true`); the default is off.
  That one-time grant is held in memory, bound to the workspace, server
  metadata, transport configuration, and full tested tool catalogue, then
  consumed on activation. After activation, only unchanged descriptor hashes
  retain their exact grants across a catalog refresh; a new or changed tool
  requires review before it can receive an automatic grant. Every
  non-selected tool remains `external_write` and requires per-call approval.
  This explicit opt-in trusts the selected server's claim; it is not proof
  that the server cannot modify data or an OS sandbox. Invalid HTTP
  parameter-header annotations exclude that tool from discovery.
- The application composes the registry into the existing generic tool cell:
  registry-owned names use registry validation and dispatch, while other names
  fall back to an already-configured host runner. Lab admission and settlement
  remain the single execution path; the registry does not create a parallel
  tool lifecycle.
- Dynamic in-process Python imports, native binaries, JavaScript handlers, and
  source-platform hooks remain unsupported. Explicit WASM is currently the only
  guest-code path; external integrations continue through separately governed
  MCP/host tools.

## Consequences

This keeps the common path small and cross-platform while allowing local
subprocess and remote MCP integrations plus pure-compute WASM tools. Current MCP requests carry their own
version and client metadata and do not depend on a protocol session; legacy
servers continue to use initialization and, for HTTP, session handling.
Discovery is bounded, but live transports are not probed before approval.
Current-protocol list-change notification streams are available only when the
server advertises the capability; user-mediated elicitation and
server-initiated requests remain explicit deferred work rather than being
silently treated as supported.

## Migration, security, performance, operations, rollback

Migration is additive: existing tool runners, skill registries, desktop
commands, and persisted records remain authoritative. Callers can construct an
`ExtensionRegistry` without changing the legacy `Agent.run()` path. Removing
the registry wiring and its focused tests is the rollback path; no persisted
schema migration is required.

Security is fail-closed at the trust boundary. Discovery never compiles or
executes extension code; WASM compilation/registration requires explicit
approval and its guest receives no host capabilities. Live MCP connections require explicit approval, stdio commands
are argument-vector based, HTTP hosts are allowlisted, non-global DNS results
and redirects are rejected by default (loopback requires explicit opt-in),
ambient cookies are not read, and payloads,
headers, tools, and timeouts are bounded. Modern HTTP parameter values are
derived only from statically reachable, validated schema properties and are
encoded before entering headers. OAuth is limited to the documented HTTPS
Bearer flow; unsupported authentication schemes fail closed. Token references
resolve only through the native OS credential store, and a missing or
unavailable store fails closed. This layer does not claim OS-level isolation or
complete MCP server request handling; those remain host/sandbox
responsibilities.
Package ingestion does not follow plugin-declared paths; it accepts only the
fixed resource roots, bounds traversal and bytes, rejects links/special files,
and never persists raw MCP connection values. This is a safe-subset importer,
not full compatibility with every plugin runtime feature.

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

- DeepSeek Harness labels the current project a developer preview with breaking
  changes; its package map and subagent documentation are useful ownership and
  provider-seam references, not evidence of production maturity:
  https://github.com/deepseek-ai/deepseek-harness
  https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/README.md
  https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/subagent.md
- Codex documents plugin packaging/hook trust and skills that expose metadata
  before loading full instructions on demand. AEGIS follows that disclosure
  model but keeps skill content separate from tool authorization:
  https://developers.openai.com/plugins/build/plugins
  https://developers.openai.com/codex/skills
- Antigravity documents `Ask` as the default for unconfigured MCP tools;
  Claude Code documents per-invocation skill tool grants and warns that a
  project skill can grant itself broad access. AEGIS keeps approval in the host
  policy and never interprets skill metadata as an authorization grant:
  https://antigravity.google/docs/mcp
  https://code.claude.com/docs/en/skills
- Hermes documents plugin-scoped MCP allowlists and timeouts. Pi documents
  that executable extensions inherit the agent process's OS permissions and
  routes MCP/codemode tool calls through the same tool hooks. These are
  capability and lifecycle references, not proof of code isolation:
  https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins/
  https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-search
  https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md
  https://github.com/earendil-works/pi/blob/main/packages/agent/README.md
- Current MCP versioning and legacy compatibility rules:
  https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- Current MCP Streamable HTTP metadata headers, stateless requests, and
  compatibility detection:
  https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- Current MCP tool schemas, pagination, result types, and untrusted-annotation
  requirements:
  https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- MCP maintainers' explanation of why `readOnlyHint` is advisory and cannot
  replace host authorization:
  https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/
- Local verification: `tests/test_extensions.py`, including real stdio and
  local Streamable HTTP servers, modern metadata, pagination, and legacy
  fallback.
- The upstream product documentation above supports qualitative capability
  comparisons, not a controlled cross-project benchmark; this ADR makes no
  relative quality, speed, security, or token-savings claim.
