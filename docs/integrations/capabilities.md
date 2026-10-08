# Capability extensions

This document describes the local extension boundary for tools, skills, and
MCP-backed capabilities. It is an adapter around the existing Lab runtime; it
is not a second execution engine.

## Terms and access boundary

- The AEGIS harness owns discovery, validation, approval, dispatch, execution,
  and result handling.
- A tool is a callable function with a declared input schema and effect policy.
- A skill is reusable instruction text loaded when relevant; it is not code
  and does not run its bundled scripts.
- A plugin is a package that can distribute supported metadata, skills, MCP
  connection descriptions, and—when explicitly declared—bounded WASM tools.
  Importing a package does not approve or activate its contents.
- MCP is a protocol for connecting AEGIS to another capability server. It is
  not itself a tool, an agent, a project-data request, or a sandbox. A remote
  server receives only the protocol messages AEGIS sends, including any
  configured authentication and tool arguments; the protocol does not
  automatically upload the open project. The current
  host rejects server-initiated requests. A local stdio server is different:
  its process runs as the desktop user's OS account and is not sandboxed, so
  it may access whatever that account can access independently of MCP. Review
  its command before starting it. Tool/resource/prompt results from any MCP
  server remain untrusted and follow the existing approval and insertion
  rules. An imported plugin's MCP command follows this same local-server rule:
  import only suggests a session draft and never launches it. After server
  approval, a user-started test or activation launches the command under the
  current OS account, without an OS sandbox. Direct Python/Node plugin
  execution is separate and remains unsupported by this importer.

## What is implemented

`aegis_cognition.extensions.ExtensionRegistry` provides these bounded pieces:

1. `ToolSpec` describes a callable capability for the model: name, input
   schema, effect class, timeout, concurrency mode, and result-size limit.
2. `SkillCatalog` discovers `SKILL.md` metadata without importing code. A
   registry constructed with that catalog exposes `aegis.skills.read` as a
   read-only, hash-checked, paginated way to load a skill body only when needed.
   Supporting text files use a separate bounded reader; neither path executes
   skill or plugin files.
3. `discover_extension_manifests()` reads `extension.toml` metadata only. The
   separate WASM descriptor scanner validates `wasm.toml` and hashes a bounded
   `plugin.wasm`; neither discovery path compiles or executes guest code.
4. `McpStdioProvider` starts an explicitly supplied child command and
   negotiates current stateless discovery or the legacy initialize handshake.
   It supports bounded request/response calls and multiplexes current-protocol
   change notifications with tool responses on one stdout reader. Activation
   still requires an explicitly approved server. The command, environment,
   and process lifetime remain host-owned.
5. `McpHttpProvider` implements bounded MCP Streamable HTTP request/response
   and explicit change-notification SSE streams. Each stream uses a separate,
   DNS-pinned connection and bounded worker; ordinary tool requests continue
   independently. It supports the current stateless request format and bounded
   legacy session flow, finite SSE responses, session reinitialization after
   `404`, and explicit session cleanup. Hosts require an exact allowlist;
   remote HTTPS endpoints must resolve only to globally routable addresses;
   loopback/plain-HTTP endpoints require explicit opt-in and remain loopback-
   only. For HTTPS servers that challenge with OAuth,
   the user-triggered connection test can use public-client registration,
   Authorization Code + PKCE S256, and a loopback callback. If dynamic client
   registration is unavailable, the user can provide a pre-registered Client
   ID; AEGIS does not publish client metadata or use browser cookies. Access and
   refresh tokens are stored in the native OS credential vault and sent only
   as Bearer authorization headers. A tool call is never replayed automatically
   after sign-in or token refresh.
6. `WasmPluginDescriptor` plus `ExtensionRegistry.register_wasm_plugin()` bind
   approved WASM exports to ordinary `compute` tools. Discovery only reads
   metadata and hashes the module; compile/registration occurs only after an
   explicit desktop approval.

Tool calls from `AgentApplication` use the existing generic tool cell, so Lab
admission, AESE checks, telemetry, and settlement remain the execution
boundary. Registry-owned tool names pass through registry validation and
dispatch; when a host runner already exists, other names fall back to that
runner. This keeps legacy tools available without bypassing registry checks.
The registry also owns metadata selection, lookup, timeout handling,
serialization bounds, and concurrency control.

Within one MCP server, calls without an exact read-only auto-run grant are
serialized across tool names. Exact, explicitly granted read-only tools may run
in parallel, and separate MCP servers remain independent. This is a host-side
concurrency policy, not proof about server internals: after a timeout or lost
connection, AEGIS cannot prove that the remote operation stopped or rolled back,
so the external outcome may be unknown.

The default registry reserves space for its host-owned helper tools so one MCP
server can still register the full bounded catalog of 256 tools. A caller that
sets a smaller `max_tools` value intentionally applies a smaller aggregate
registry limit.

If a dispatched, non-granted MCP call times out or its caller is cancelled, the
caller receives the timeout/cancellation while AEGIS keeps that server's lock
until the provider call itself settles. Calls still waiting for the lock are
cancelled before dispatch when their own deadline expires; they are not run
later as stale work. A provider that never settles can therefore block later
calls for that server. The built-in transports have their own bounded request
timeouts; custom providers must also bound their calls.

A timeout does not forcibly stop a synchronous Python handler already running
in a worker thread. More generally, a runner exception does not prove that an
external side effect did not commit before its response was lost. When another
attempt is configured, AEGIS therefore suppresses automatic retries after any
error for effects outside `read_only`, `network_read`, `compute`, and
`model_inference`. Timeouts record
`tool_timeout_outcome_unknown_retry_suppressed`; other errors record
`tool_external_effect_retry_suppressed_after_error`. The caller must inspect
external state before manually retrying. A separately configured initial
research/search operation remains eligible under its normal `network_read`
policy; it does not receive or verify the uncertain write result. The uncertain
result stops the remainder of that tool batch, the current controller action
plan, later controller turns, and automatic experiment/simulation continuation;
the action-plan contract does not prove that later actions are independent.
Settlement failure after an external call is treated as an unknown outcome as
well. For tools declared `serial`, AEGIS rejects another synchronous call until
the timed-out handler actually exits, preventing overlapping retries. Async
handlers must cooperate with cancellation.

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
The application adds only a compact capability catalog to the model context.
The catalog helpers' legacy `token_budget` argument caps whitespace-delimited
words, not provider-specific model tokens. When a `SkillCatalog` is supplied,
the application advertises `aegis.skills.read` and loads
selected skill instructions only when the model requests them. It does not send
every tool schema on every turn. Desktop chat has a separate explicit enable
state: enabled skill bodies are included in its stable prompt prefix, while
referenced text files remain available through the bounded reader. The desktop
exposes its built-in skill-resource reader only while at least one skill is
enabled. For tool-capable providers, desktop chat exposes bounded Skill search
and read tools and loads instruction pages only when requested; it does not
copy every enabled body into the cacheable prompt prefix. Providers without a
working tool-call interface retain a bounded fallback that loads only
task-relevant local Skills.

In the generic `AgentApplication` path, `aegis.tools.schema` retrieves one
registered tool's exact JSON Schema on demand. The result includes the
descriptor hash and catalog revision so a later call can reject a changed
registration; reading a schema does not authorize or execute the tool. When
the registered catalog exceeds the prompt's visible-tool limit and the
registry has room while preserving a user-tool slot, the application also
advertises `aegis.tools.search`. It performs a bounded, deterministic lexical
search over registered names and descriptions, returns at most 16 metadata
matches per page, and does not return schemas or invoke a match. A caller may
pin later pages to the returned catalog revision; a changed catalog requires
starting the search again. Search results are untrusted metadata, not an
authorization grant. The model must inspect the exact schema and the existing
Lab admission/effect policy still decides whether the selected tool may run.
The desktop keeps its separate provider-native discovery path and does not
expose either generic lookup helper as a duplicate model tool. After a
successful safe tool call, the next Lab controller step receives up to three
recent results, bounded to 36 KiB of serialized item data and labelled
untrusted. These results exist only in the current run's memory; the durable
ledger continues to store hashes, not raw tool content. Tool search is hidden
when the visible list is sufficient or a constrained registry cannot reserve
its helper capacity. Any token or latency savings are workload-dependent and
have not been benchmarked for AEGIS.

The skill-body reader accepts a skill name and optional `start_line`/
`max_lines`, returns at most 8 Ki characters and 200 lines per call, and reports
`next_line`/`has_more` for continuation. It revalidates the skill path and
content hash on each read. Skill text is user-provided task guidance, not
permission to override host safeguards; the reader is read-only and never
executes the file. `SKILL.md` remains disallowed as a path to the separate
reference-file reader, keeping the two contracts distinct.

When a skill is enabled, the model may call `aegis.skills.read_resource` to
read a referenced UTF-8 text file from that skill's own folder. Each call is
limited to a 512 KiB source file, 200 lines, and 8 Ki characters of returned
text; larger line ranges continue from `next_line` while `has_more` is true.
Absolute paths, traversal, hidden files, links/junctions, binary content, and
files outside the skill folder are rejected. The file is never executed. Its
contents remain untrusted prompt input, and each result includes the relative
path and SHA-256 of the current file contents. This is progressive loading of
skill references, not general workspace file access.

Desktop chat and delegated workers can use `aegis.skills.search_enabled` to
find relevant enabled metadata, then `aegis.skills.read_enabled` to read a
bounded `SKILL.md` page. Each read revalidates the enabled skill hash and is
read-only; the body is untrusted task guidance and is never executed. Direct
desktop chat uses this on-demand path only when the selected provider actually
supports tool calls; otherwise it retains the bounded task-relevant local
fallback. Tool-call-capable provider catalogs reserve the Skill search/read
tools when the rest of the tool list exceeds the provider's schema limit.

## Import a local capability pack

Utilities can import one local folder, or a `.zip` package with the same
layout. The desktop uses a native picker for either source. A ZIP is validated
and streamed into a temporary staging directory before the existing atomic
folder importer sees it; traversal paths, links/special files, duplicate or
platform-colliding names, encrypted entries, and archives over the bounds are
rejected: 20 MiB compressed, 16 MiB expanded, and 4,096 entries. The normal
importer then applies its 256-file, depth, metadata, and atomic-install checks.
A single enclosing directory is accepted. Import never runs code. Native
AEGIS packs may include one bounded WASM module, but it remains inactive until
the user approves and enables it.

Local folder layout:

```text
extension.toml
skills/<skill-name>/SKILL.md
skills/<skill-name>/...          # optional reference files
mcp/<server-id>/mcp.toml         # optional; one metadata file per server
wasm.toml                        # optional; describes one compute-only WASM plugin
plugin.wasm                      # required with wasm.toml; compiled only after approval
```

`wasm.toml` uses `[wasm]` (`schema = "aegis-wasm-plugin-v1"`,
`module = "plugin.wasm"`, optional `fuel_limit`, `memory_pages`, `timeout_ms`,
and `max_output_bytes`) plus one or more `[[tool]]` tables. Each tool declares
`name`, `description`, an exported function name, and an input JSON Schema;
output JSON Schema is optional. Tool names must match `extension.toml`'s
`tools`, and the extension must declare the `compute` capability. Example:

```toml
[wasm]
schema = "aegis-wasm-plugin-v1"
module = "plugin.wasm"
fuel_limit = 2000000
memory_pages = 64
timeout_ms = 5000
max_output_bytes = 65536

[[tool]]
name = "example.calculate"
export = "calculate"
description = "Calculate a bounded result from the supplied input."
input_schema = { type = "object", properties = { value = { type = "integer" } }, required = ["value"], additionalProperties = false }
output_schema = { type = "object", properties = { result = { type = "integer" } }, required = ["result"], additionalProperties = false }
```

The current host accepts modules up to 16 MiB, at most 32 tools per module,
fuel up to 100 million, memory up to 256 WebAssembly pages, execution timeout
up to 30 seconds, and result output up to 64 KiB. Desktop inputs are separately
bounded by the existing tool-call boundary. These are enforcement caps, not
performance promises.

Each module is limited to one linear memory and one table. The declared guest
memory limit may be 1–256 pages; growth beyond that limit fails the invocation.
Tables are capped at 65,536 elements, including when the module declares no
table maximum.

Extism's [upstream PDK catalog](https://extism.org/docs/quickstart/plugin-quickstart/)
is not an AEGIS language-compatibility promise.
The AEGIS host disables WASI and accepts only modules whose imports work with
the Extism data ABI alone. For example, Extism's current quickstart says its
JavaScript and Go examples require WASI, so those examples do not run under
this policy unchanged. A language/toolchain is supported here only after an
actual no-WASI module passes the AEGIS runtime tests.

The importer also accepts a supported, data-only subset of portable, Codex,
and Claude Code plugin package layouts:

```text
# Portable package
plugin.json
mcp.json                       # optional
skills/<skill-name>/SKILL.md
assets/                        # optional; copied as inert files
hooks/                         # optional; copied but never executed
scripts/                       # optional; copied but never executed

# Compatibility package (used when root plugin.json is absent)
.codex-plugin/plugin.json
.mcp.json                      # optional
skills/<skill-name>/SKILL.md

# Claude Code compatibility package (manifest is metadata only)
.claude-plugin/plugin.json
.mcp.json                      # optional
skills/<skill-name>/SKILL.md
```

The plugin `name`, optional `version`/`description`, skills, and supported MCP
server metadata are translated into AEGIS's existing extension/skill/MCP
catalogs. This is package-format compatibility, not full runtime
compatibility: Claude agent definitions and commands are unsupported; if a
package contains components outside the fixed allowlist, import rejects the
package instead of silently dropping or executing them. Plugin hooks and
scripts within the accepted layout remain inert files. Root MCP configuration
is normalized for the project: only the server ID, transport, description,
and environment-variable names are retained. Commands, arguments,
URLs/endpoints, headers, literal environment
values, raw plugin/MCP manifests, and `.app.json` mappings are never written
into the project. When a command is bounded and contains no recognizable
credential marker, or an endpoint is HTTPS without URL credentials/query/
fragment, the import result may send that connection suggestion to the
renderer’s session memory. The renderer labels it untrusted and keeps the
existing server approval and explicit connection test/activation steps.
The `mcp.*` extension-ID namespace is reserved for MCP server registrations;
imported plugin IDs cannot claim it.
When a suggested stdio command references a file copied with the package, the
session-only draft also points its working directory at that imported package
folder so the relative script path resolves; the user can review or change it.
Obvious credential-bearing command arguments and unsafe URLs are omitted;
this is a conservative filter, not a general secret detector.
Environment/header values are never transferred. Import does not install or
start anything. If both `mcp.json` and `.mcp.json` are present, import stops and
asks for one unambiguous configuration.

For plugin packages, `assets/`, `hooks/`, `scripts/`, and the root package
documents `README.md`, `LICENSE`, `NOTICE`, and `CHANGELOG.md` are retained as
inert files. AEGIS does not install packages, run hook/script code, enable
skills, approve servers, or start MCP transports during import. Skills remain
disabled and MCP servers remain unapproved and stopped. The importer accepts
only the fixed root skill/config locations described above; it does not follow
manifest-supplied paths. This compatibility importer does not execute the
source platform's JavaScript/Python, arbitrary tool handlers, hooks, or external
app registrations. Only a native AEGIS pack using the explicit WASM format
above can expose executable tools; unsupported MCP transports and alternate
resource roots remain unavailable.

Both layouts use the same bounded atomic import path: included links and
special files, mixed native/plugin manifests, unsupported files/layouts,
conflicting IDs, malformed metadata, oversized content, and excess traversal
are rejected. Native AEGIS manifests require the `skills` list to match the
included skills; any non-empty `tools` list must match a valid sibling
`wasm.toml` declaration and module.

## WASM activation and limits

Activation is an explicit user action (“Approve & enable”), not a result of
import or scanning. Approval is bound to the extension metadata, module
SHA-256, each tool's schemas/export, and runtime limits. A changed file or
contract invalidates the stored approval. Disabling removes the tools from the
live registry; a previously approved unchanged module can be re-enabled, or
its approval can be revoked. On restart, only unchanged, approved, enabled
modules are re-registered.
Before each desktop-dispatched WASM tool call, and whenever capability state is
read, AEGIS rechecks the current descriptor and saved approval. A changed or
missing module/contract, or a revoked/disabled state, unregisters the active
tools and rejects the pending call. This check is event-driven by those host
operations, not a continuous filesystem watcher; it cannot stop a guest call
that was already admitted before a change was observed. Direct low-level
registry calls remain outside this desktop authorization boundary.

Guest calls run through Extism with WASI disabled, no AEGIS-provided host
functions registered, all network hosts denied, and plugin-variable access
disabled. Only Extism's data/input/output ABI functions remain available. Every invocation
gets a fresh plugin instance; guest state is not persisted between calls. The
host caps module bytes, input/output, fuel, linear memory, and wall time. Calls
are registered only as `compute`; this plugin ABI cannot access project files,
network, cookies, subprocesses, or secrets. Integrations needing those
capabilities must use separately configured AEGIS tools or MCP and their own
approval gates. This design reduces exposed authority; it is not a claim of
complete security against runtime, compiler, or OS vulnerabilities, nor does
it provide a process-level sandbox.

Wasmtime reserves at least 1 MiB for Extism's internal memory. This allocator
floor does not raise the plugin's declared guest-memory budget. The allocator
also caps each compiled plugin engine at 32 instances; this is a per-engine
limit, not a process-wide allocator limit across multiple enabled plugins.

The supported desktop tool dispatcher also obtains a lease from the process-
local `AuthoritativeRuntime` for each registered tool call. `WorkKind::Tool`
uses its shared `Untrusted` lane, so that host path limits active calls across
compiled plugin engines in one AEGIS process. The default subagent path uses
the same process-local runtime through its `Agent` lane. This is not shared
between separate AEGIS processes. Desktop discovery currently limits one scan
to 256 extension manifests; that is a discovery bound, not an aggregate memory
budget or a process-wide guarantee on the number of live compiled engines as
plugin directories or service instances change. Runtime admission also does
not protect direct low-level registry calls made outside the host dispatcher.
The engine-pool and host-admission limits are distinct and neither currently
accounts for cumulative compiled-engine reservations.

When WASM plugins are enabled, AEGIS pins Extism to upstream commit
`d5da29759bba88645f886d9e12d3f4e4376df7b3`; both AEGIS and Extism resolve to
Wasmtime `48.0.4`, avoiding the second, older Wasmtime engine previously
pulled in by Extism `1.30.0`. This is an exact upstream Git revision, not a
stable Extism release. It reduces version divergence but adds a Git source and
passed 12/12 focused WASM runtime tests in debug and release and 579/579
`aegis-nerve` library tests in debug on Ubuntu 24.04 x86_64 with Rust 1.98.1.
Windows/macOS runtime parity and the Python 3.14 native-extension packaging
path remain unverified. Replace this pin with a stable Extism release only
after it targets the patched Wasmtime line and passes the same checks.
Build-time and binary-size effects have not been measured. The Cargo feature
remains optional for builds that do not package desktop WASM plugins.

Desktop live chat sends at most eight tool schemas to a provider. If more than
eight tools are active, it exposes the built-in `aegis.tools.discover` metadata
search; matching schemas are offered on the next provider round. Discovery does
not activate an extension or execute a candidate. Each provider turn is pinned
to the registry revision used to build its schemas: a registration change stops
later calls, and a pending side-effect approval is rejected if that revision
changed while waiting.
Every live tool turn also sends fixed system-level guidance that treats tool,
MCP, browser/research, workspace, and recalled-memory content as untrusted
evidence; MCP safety annotations are claims, not permissions. This guidance is
defense in depth, not enforcement: host-side effect checks and approval gates
remain authoritative. A provider client that cannot accept the system context
is rejected for the tool loop.
Within one provider tool batch, successful safe-effect calls with the same tool,
canonical arguments, and byte-identical result still execute independently; a
repeated result of at least 512 bytes is replaced only in the model-facing
history by a SHA-256 reference to the earlier call. The full result remains in
the conversation ledger. Failed and side-effecting calls are never compressed.

The built-in subagent delegation can assign a worker one tool call from the
schemas exposed in that exact provider round. The host checks the current
catalog revision and descriptor hash, then admits only registered
`read_only`, `network_read`, or `compute` tools through the existing Lab and
extension-registry path. The worker cannot call a hidden tool, change the
registered effect, or execute a state-changing tool; those calls remain on the
normal approval flow. A delegated tool call is one-shot, and a tool declared
`serial` reserves its resource in the subagent plan. Results are bounded and
returned to the root as untrusted evidence, not accepted as verified claims.

## Activation and safety rules

- Desktop project skill discovery checks `.aegis/skills`, `.aegis/extensions`,
  `.agents/skills`, `.claude/skills`, the legacy Antigravity `.agent/skills`,
  `.hermes/skills`, and `skills` in that order; profile skills are checked
  last. These are project-root locations only; other agents' machine-global
  skill directories are not scanned automatically. If names collide, the
  first valid skill wins. Portable skill directories containing `SKILL.md` are recognized; a
  limited optional tag field is used only for matching. Other external metadata
  is inert, and scripts/extensions are not executed.
- Discovery returns metadata rather than skill instructions, but it reads each
  `SKILL.md` within the per-file byte cap to parse frontmatter and bind a content
  hash; instruction text stays out of model context until explicitly loaded.
  It does not descend into symlink/junction, generated, or known
  package-metadata directories such as `.hub`, and is bounded across
  all roots by 16,384 filesystem entries, six nested
  directory levels, and a 64 KiB limit per extension/MCP manifest by default.
  Skill files keep their 512 KiB per-file limit. Exceeding the entry or metadata
  file-count cap fails discovery rather than presenting a silently truncated
  catalog; oversized metadata files are skipped. Callers may tune limits within
  hard maxima.
- `SKILL.md` frontmatter is parsed as safe YAML with duplicate mapping keys
  rejected, and checked against the
  [Agent Skills specification](https://agentskills.io/specification) name and
  description limits; nested catalog entries must use a directory matching
  `name`. AEGIS also accepts its legacy top-level `version` and comma-separated
  `keywords` fields, plus keyword lists and `metadata.version`. `allowed-tools` is
  descriptive metadata only; it does not grant tool permissions or bypass host
  approval. Importing a selected skill installs it under the validated metadata name.
- Portable plugin import accepts a bounded, data-only subset of the Agent
  Plugins v1.0 package layout (`plugin.json`, `skills/`, and `mcp.json`), as
  also used by Hermes' compatibility adapter. When a package declares
  `$schema`, AEGIS recognizes only the versioned v1.0.0 manifest and MCP schema
  identifiers and rejects other declared versions. Legacy packages without
  `$schema` remain accepted through the older compatibility path. This is not
  full Agent Plugins conformance: AEGIS normalizes only fields it uses and does
  not validate every optional manifest/MCP constraint. It does not load Hermes'
  separate native `plugin.yaml`/Python `register(ctx)` plugins, install their
  Python dependencies, or execute packaged code and hooks. Imported MCP
  connections remain inactive until the normal review and approval flow. See
  the [Agent Plugins v1.0 specification](https://github.com/agentplugins/agent-plugins-spec/blob/main/spec/1.0.0.md)
  and [Hermes plugin compatibility boundary](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins).
- `aegis.skills.read_resource` is available to model tool-calling only when a
  skill is explicitly enabled. It rechecks the skill's activation hash for
  every call and permits bounded text reads below that skill's directory only;
  it cannot read general project paths or execute files. A resource file's
  current hash is returned because only `SKILL.md` is part of the activation
  hash. Resource text is untrusted and does not change tool permissions.
- Untrusted manifests are not executable registrations.
- Tool arguments and results must be JSON-compatible and remain within their
  declared byte limits.
- JSON Schema `pattern` and `patternProperties` expressions are limited to 256
  distinct patterns, 4 KiB per pattern, and 16 KiB total UTF-8 pattern data.
  Each input/output schema validation has a shared 250 ms regex-work budget to
  prevent pathological patterns from stalling the agent runtime. Input-schema
  failure stops dispatch before the tool handler runs.
- Serial tools are serialized per event loop; parallel tools may use the
  existing Lab scheduling boundary.
- Live MCP transports are not contacted before approval: a pre-approval
  registration is a strict no-op after provider-shape validation. Activation
  connects only after the host marks the server approved.
- Advertised current-protocol tool/resource `listChanged` notifications trigger a bounded catalog
  refresh. The registry swaps the validated tool set in one revision; if a
  refresh fails, the last-known-good set remains active. A prior automatic
  read-only grant survives only for a descriptor with the identical hash that
  still meets the read-only eligibility rules. New or changed tools remain
  `external_write` and use the normal per-call approval path. The changed
  registry revision invalidates tool calls already prepared against an older
  catalog. The runtime subscribes only to tool/resource list-change
  capabilities advertised by the server; it does not subscribe to every
  resource URI. Prompt catalogs are fetched when requested, not cached.
- Server approval alone never makes an MCP tool automatically executable.
  After the user tests the connection, the settings page may offer a one-time
  opt-in for the exact listed tools that advertise strict `readOnlyHint: true`
  and explicitly set `destructiveHint: false` (MCP defaults an omitted
  `destructiveHint` to `true`). The default is off. The grant is bound in
  memory to the workspace, approved server metadata, exact transport
  configuration, and complete tested tool catalogue; it is consumed on
  activation and is not persisted. The settings preview shows at most 64 tools;
  unlisted tools are never included in that grant. After activation, a refresh
  preserves grants only for unchanged descriptor hashes that remain eligible;
  new or changed tools do not inherit them and require per-call approval unless
  the user tests and explicitly grants the updated descriptor.
  Tools without that explicit grant—including missing, false, destructive, or
  contradictory hints—remain `external_write` and require host approval for
  each call. MCP treats annotations as untrusted unless they come from
  trusted servers ([Tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools));
  the opt-in trusts the selected server's claim, not proof of read-only
  behavior or an OS sandbox ([maintainer discussion](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)).
  The test preview also surfaces `openWorldHint`; if omitted, it defaults to
  “may interact outside its domain.” This is an unverified server claim for
  user context only: it neither changes auto-run eligibility nor proves actual
  behavior. A changed hint changes the tool descriptor hash and invalidates a
  prior test-bound grant.
- When an approved server declares MCP's `resources` capability, the adapter
  exposes two additional host-managed tools: search the advertised resource
  catalog and read one advertised URI. If the server implements
  `resources/templates/list`, it also exposes bounded template search and a
  read tool that expands only variables from a currently advertised template
  using [RFC 6570 URI Template rules](https://www.rfc-editor.org/rfc/rfc6570).
  Variables are optional text values (up to 32 names, 4 KiB each, 16 KiB
  total); URI-template arrays and objects are not accepted. A server that
  returns method-not-found for template listing keeps ordinary resource access
  available. The resource/template catalog is rechecked on each call; a changed
  catalog fails closed until a supported list-change refresh commits the new
  descriptor set or the user tests the server again.
  Resource text is capped at 64 KiB total and eight content blocks; binary
  blobs are omitted. Search results cap each description at 512 characters and
  the complete serialized result at 32 KiB. Search metadata and returned text
  are untrusted model input.
  This host-managed path is read-only, but it can still disclose resource
  contents to the configured model, and server annotations are not proof of
  behavior. The user must opt in to automatic use just like other read-only
  candidates. The adapter does not fetch `https://` resource URIs directly.
  See the [MCP Resources specification](https://modelcontextprotocol.io/specification/2026-07-28/server/resources).
- For an approved, active server, the user can explicitly list and render MCP
  prompts. Prompt arguments are sent to that server only after the user asks
  for a preview. Returned text is untrusted and shown with its original
  `user`/`assistant` role; nothing is sent to the model automatically. The
  user may insert a preview only when it is exactly one user text message, and
  then still reviews and sends it through the normal composer. Multi-message
  prompts remain preview-only so AEGIS does not flatten roles; non-text prompt
  content and user-mediated elicitation are rejected explicitly. Bounded
  state-only `input_required` retries do not collect user input. Catalogs are
  bounded to 128 prompts, 32 arguments per prompt, 32 KiB of arguments, and
  64 KiB of rendered text. Advertised current-protocol tool/resource
  list-change notifications are handled in the background; server-initiated
  requests remain unsupported.
  See the [MCP
  Prompts specification](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts).
- The stdio transport passes an argument vector with `shell=False`, rejects
  non-JSON stdout, bounds each message, rejects unsupported server-initiated
  requests, and drains bounded stderr so a noisy child cannot block the
  protocol pipe. Its child receives a small platform-specific allowlist of
  runtime path/temp variables plus explicitly configured session values; it
  does not inherit ambient API keys, cookies, or proxy settings. Windows may
  still dispatch `.bat`/`.cmd` files through a system shell; prefer a native
  executable and review every configured command argument before activating a
  server. This environment filtering is not OS isolation: the child still runs
  as the desktop user's account with that account's normal file and network
  permissions. Before enabling tools, the host requires a one-use test bound
  to the exact transport configuration, workspace, and approval revision; a
  changed configuration or tool catalog must be tested again before any child
  process starts.
- The HTTP transport does not follow redirects, does not read ambient browser
  cookies, pins the request to an address resolved after allowlist validation,
  rejects non-global DNS results by default (loopback requires explicit
  opt-in), bounds headers and bodies,
  and accepts credentials only through explicit configuration headers or the
  MCP OAuth flow. OAuth metadata and token endpoints must use HTTPS, are
  contacted through the same bounded direct/pinned transport, and do not follow
  redirects. OAuth uses the system browser, an ephemeral `127.0.0.1` callback,
  PKCE S256, random state, and issuer checking when the authorization server
  advertises it. AEGIS tries Dynamic Client Registration when available; if
  registration is unavailable, the user supplies a Client ID. AEGIS does not
  host client metadata on an AEGIS domain. The secure-store adapter selects only the operating system's
  native credential vault (Windows Credential Manager, macOS Keychain, or
  Linux Secret Service); it has no plaintext-file fallback and fails closed if
  the native vault is unavailable. For HTTP 403 `insufficient_scope`, the
  rejected tool call is not replayed. Its requested scopes are kept in that
  vault; the user must stop the MCP server and explicitly test the connection
  to review/grant the added access in the browser, then retry the tool.
- The MCP Settings page can search the official public registry only after the
  user submits a query. It makes a bounded HTTPS `GET` to the fixed registry
  host, sends no provider key, MCP credential, or browser cookie, and never
  follows redirects. Returned package and server metadata are untrusted: the
  UI can prepare a local metadata draft, but it never installs a package,
  launches a command, supplies credentials, or approves/starts a server. Any
  endpoint is only a hint; the existing MCP host validation still applies if
  the user later configures and tests it. Registry access uses a pinned direct
  outbound HTTPS connection; it does not use system proxy settings, so proxy-
  only or restricted networks may block lookup. See the [official Registry API](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/official-registry-api.md)
  and [OpenAPI contract](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/openapi.yaml).
- This layer does not claim OS-level isolation or full server-initiated MCP
  request handling. OAuth supports public clients using dynamic registration
  when advertised, or a user-provided Client ID. This keeps the desktop flow
  independent of an AEGIS-hosted metadata domain; DCR is retained for server
  compatibility even though the current MCP revision prefers Client ID
  Metadata Documents. It does not use client secrets, host CIMD metadata,
  support non-S256 PKCE, accept OAuth for MCP URLs with query parameters, or
  implement MCP user-mediated elicitation. Automated tests use a local fake
  authorization server and fake vault; real-provider interoperability and
  native-vault persistence still require per-OS integration testing.

## Current boundary

The registry is intentionally a small compatibility seam. It does not provide
a general dynamic Python plugin loader or server-initiated MCP requests.
Notification streams are limited to advertised change events, at most 16
subscriptions per provider, a bounded 1,024-event queue, and bounded SSE event
size. Cancellation closes the HTTP stream or sends the stdio cancellation
notification. Server-initiated requests remain rejected rather than implicitly
executed.

Desktop memory operations are bound to the active profile; a request cannot
select another `owner_id`. If the app restarts while a tool is still waiting for
approval, recovery records it as cancelled-without-execution and interrupts the
turn. A call that had already left the approval state is marked ambiguous and
is never replayed automatically.

On desktop exit, Tauri requests an orderly service shutdown and waits up to
12 seconds before force-terminating the sidecar. This covers the MCP runtime's
bounded five-second close attempt plus its five-second event-loop join. If the
service still cannot exit, forced termination remains the final fallback; any
in-flight remote tool outcome must be treated as unknown.

The HTTP boundary follows the current MCP Streamable HTTP and versioning
requirements:

- https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery
- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration

Run local verification with standard CPython `3.14.7` (GIL enabled), not the
free-threaded `3.14t` build. If the existing project `.venv` uses another
interpreter, isolate the verification environment under the ignored `.local`
directory so `uv` does not replace it. In PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".local/tmp/cp314-standard"
$env:UV_PYTHON = (uv python find --system 3.14.7)
uv run --python $env:UV_PYTHON --locked --extra all --extra dev python -c "import sys; assert sys.version_info[:3] == (3, 14, 7) and sys._is_gil_enabled()"
uv run --python $env:UV_PYTHON --locked --extra all --extra dev pytest tests/test_extensions.py -q
```

On macOS/Linux, set the same variables before running the commands:

```sh
export UV_PROJECT_ENVIRONMENT=.local/tmp/cp314-standard
export UV_PYTHON="$(uv python find --system 3.14.7)"
uv run --python "$UV_PYTHON" --locked --extra all --extra dev python -c "import sys; assert sys.version_info[:3] == (3, 14, 7) and sys._is_gil_enabled()"
uv run --python "$UV_PYTHON" --locked --extra all --extra dev pytest tests/test_extensions.py -q
```

Keep them set for the conformance commands below so their nested
`uv run --no-sync` also uses this isolated standard-interpreter environment.
Remove them from the shell after verification if you do not want them to affect
later `uv` commands.

For an additional wire-level check against the upstream conformance runner, the
small client adapter in `tests/mcp_conformance_client.py` supports the
`tools_call` scenario over loopback HTTP. The runner release below is a
pre-release; these two scenarios are targeted evidence, not proof of full MCP
conformance:

```text
npx --yes @modelcontextprotocol/conformance@0.2.0-alpha.11 client --command "uv run --no-sync python tests/mcp_conformance_client.py" --scenario tools_call --spec-version 2026-07-28
npx --yes @modelcontextprotocol/conformance@0.2.0-alpha.11 client --command "uv run --no-sync python tests/mcp_conformance_client.py" --scenario tools_call --spec-version 2025-11-25
```

A previous revision of this document claimed 2/2 checks for each command. The
runner output was not retained, and its bare `uv run` did not establish which
interpreter build it selected. Treat that claim as unverified for standard
CPython `3.14.7`; rerun with the isolated environment above before relying on
it. These targeted scenarios do not establish full MCP or cross-platform
conformance.
