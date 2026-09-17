# AEGIS in-process subagent runtime

Status: implemented as an opt-in coordination primitive; not the default `Agent` execution path yet.

## Decision

Use a manager/supervisor model. The root agent retains final synthesis. Child agents are bounded task handlers, not peer chatbots. A child receives one typed request and compact dependency result packets. Large observations, browser captures, source snapshots, and datasets are references to immutable artifacts rather than inline conversation history.

The implementation reuses AEGIS primitives:

- Rust validates the task graph before execution: positive identities, closed dependencies, cycle rejection, disjoint artifact namespaces, deterministic topological order, and a BLAKE3 graph hash. Python additionally validates bounded parent lineage; a nested child must list its parent as a dependency, so the existing DAG is the lifecycle gate.
- Python 3.14 uses `asyncio.TaskGroup` for structured lifecycle, a bounded semaphore for concurrency, and per-resource async locks for stateful resources such as one browser actor session.
- Each child is admitted through the existing Rust-authoritative runtime lease. The runtime submission now carries the same run-scoped hashed dependency IDs into Rust, so native admission cannot treat a dependent child as dependency-free; Python owns handler lifecycle while the parent result and the existing DAG control nested child readiness and failure propagation.
- The existing message transport remains unchanged. The semantic envelope is a versioned JSON payload that can be carried by the existing `MessageFrame` bytes.

## Protocol language

`TASK_REQUEST` contains role, prompt, dependency IDs, capabilities, artifact namespace, exclusive resource keys, side-effect class, token budget, deadline, and idempotency key.

`TASK_RESULT` contains status, a bounded summary, epistemic claims, uncertainty, blockers, token counters reported by the handler, elapsed time, and artifact references. `token_budget` is the child completion/output-token ceiling for the coordination contract; the supervisor rejects a reported `tokens_out` value above it, while provider-specific request-side truncation remains an adapter concern. An absent provider usage field is recorded as `0` but is not evidence that no tokens were consumed. The packet and envelope are hash-bound. The wire payload carries the result body once; envelope identity and artifact references are not duplicated. Dependency context exposes only the compact representation; raw child transcripts are not forwarded automatically.

The scheduler uses direct dependency edges for data flow. It does not create a general-purpose broadcast bus or send progress chatter by default. A future progress stream can use the same envelope with a separately versioned kind after a measured need is demonstrated.

For a desktop observer, the optional `AgentMessageJournal` retains only the
already-bounded request/result envelopes in a local cursor window. It can
signal `resync_required` after eviction, but it is observation-only. When a
process boundary or restart-recovery path is required, the optional
`AgentMailbox` persists the same canonical bytes in a small SQLite WAL file.
It deduplicates by idempotency key, leases one delivery to one consumer,
requeues expired leases, and dead-letters after a bounded retry count. This is
at-least-once delivery; the Rust task ledger still owns execution state and no
exactly-once side-effect claim is made. `AgentMailboxWorker` is the matching
host-owned delivery loop for restart-aware observers or routers: it claims one
envelope, invokes a registered handler, ACKs success, and NACKs handler
failure. It does not deserialize or execute a model-selected callable and it
does not duplicate `AgentSupervisor`'s dependency scheduler.

The current Tauri renderer follows the same boundary. It reads recent
conversations through the existing `conversations.list` command, keeps the
active transcript in the canonical conversation manager, and exposes the
revision-bound project map as an opt-in view. The PI-Desktop-inspired shell is
presentation only: it does not open the state database, start providers, or
create a second session store.

## Isolation rules

Every child must have a unique artifact namespace. A result containing an artifact from another namespace is rejected. A handler declaring an exclusive resource key is serialized with other handlers declaring the same key, including supervisors sharing one Python event loop. Browser actor work must use an exclusive session key; read-only observers should use independent browser contexts or no shared actor key. The scheduler does not make a browser profile or external service safe by itself.

Handlers that declare an exclusive resource must be async. A synchronous handler runs in a worker thread that Python cannot forcibly stop; allowing its timeout to release the resource lock would let the worker continue against a resource that another child has acquired. Synchronous handlers remain available for work that declares no exclusive resource.

## Authority and degraded mode

`AgentSupervisor` requires native graph authority by default. `require_native_authority=False` is an explicit development/test escape hatch and marks the graph as `python_proposal`; it is not a production assurance. A missing native extension is therefore visible rather than silently treated as authoritative. The child runtime guard remains the existing per-child capacity/lease boundary. Static nested plans and bounded dynamic expansion both use parent-as-dependency edges. A dynamic expansion is admitted only after the complete graph is revalidated and the new task ids, handlers, capabilities, resources, side-effect classes, budgets and deadlines pass the host policy.

The root synthesizer runs once after all child tasks settle. Failed or blocked children are passed to the root with explicit status so the root can decline to overclaim. A child never writes the root answer.

`AgentPlanProposal` is the model-facing planning boundary. A model may return a
strict `aegis-agent-plan-v1` document containing task metadata and a
`handler_key`; the host validates its DAG and binds that key to an already
registered callable. The model cannot select an arbitrary Python callable,
change the native graph authority, or bypass the child runtime guard.

An async handler may call `await context.spawn_plan(plan)` when the supervisor
was given a trusted dynamic-plan binder. The plan may reference the spawning
task as an external parent during parsing, but every bound child must set that
parent as `parent_task_id` and include it in `dependencies`. The supervisor
serializes expansion admission, validates the combined graph through the same
graph authority, and schedules the accepted children in the existing
TaskGroup. A child can therefore expand recursively within the global bound,
while the parent remains a lifecycle gate; a parent failure blocks its
expansion. No dynamic plan is persisted as executable code, and no model
output is invoked as a callable.

`Agent.arun_subagents()` and `Agent.run_subagents()` provide the application
boundary. With no plan supplied, the root performs one bounded planning call;
the host hashes an unsigned plan locally, applies task/capability/side-effect/
resource limits, binds the trusted `model` handler and the public `research`
handler, then runs the children and invokes root synthesis once. The public
research handler uses Reddit RSS by default and only uses X when an explicit
system app-only bearer token is configured. Existing `Agent.run()` remains
unchanged until a later measured policy decision enables automatic mode.

Browser and vision work is deliberately injected as a trusted handler backed by
the existing `BrowserCell`/Playwright runtime. `build_browser_vision_handler`
adapts an existing `BrowserRuntimeCapture`-shaped result to a provider-neutral
multimodal invoker, validates image bytes against SHA-256 artifact references,
and keeps those bytes out of result messages. An actor handler must declare an
exclusive browser-session resource key; observer handlers must not share that
actor key. The planner can request only metadata and a registered handler key,
so it cannot create a browser session, select a cookie profile, or grant itself
external-write authority. A text-only gateway is not silently advertised as a
vision provider.

The code-reuse lane is separate from child execution. `build_local_reuse_candidate`
binds an exact line range to the existing `SourceSnapshot`; `assess_code_reuse`
compares caller-supplied generation/adaptation/verification estimates; and
`materialize_exact` requires explicit license/ownership, fresh hashes, an
in-root target, and no implicit overwrite. This is an estimated token-cost
decision, not a claim that model training data can be copied verbatim.

The memory-nudge lane is also candidate-only. A normal structured model result
may carry the reserved `_aegis_memory_proposals` field; the application strips
it from the public output, accepts only bounded `{content, relevance_score}`
objects, and sends them through `LearningManager.sync_memory`. Rust filters
them by relevance, seals the nudge, stores each fact in the existing SQLite
repository with source-session and hash provenance, and makes repeated
`nudge_id` submissions idempotent. When callers omit that ID, the Python
bridge derives a stable bounded ID from the session, scope, owner, and
canonical candidate payload so a retry converges on the same operation. The
resulting record is always
`CANDIDATE/UNREVIEWED`; activation still requires the existing explicit
validation path. This uses the existing model call; it does not launch a
second extraction call. Plain-text results or malformed proposals produce no
candidate, and calling `sync_memory` without candidates remains a truthful
compatibility acknowledgment that performs no work.

Completed ordinary runs are indexed through the scoped session API with the
configured `memory_scope` and `memory_owner_id` (defaulting to the local
profile). This keeps episodic recall attached to its owner/workspace instead
of relying on an implicit native default. Candidate search uses the same
boundary when the application configures a scope, and source hydration still
requires an explicit authorized request. The transcript remains episodic
source material; it is not silently promoted to semantic active memory. A
record that has already received an explicit `ACCEPTED` validation is eligible
for the bounded read path: AEGIS filters `ACTIVE/ACCEPTED` records, re-inspects
the authoritative content and hash, and places them in the same Rust-selected
context pack as session sources. The rendered block is explicitly data-only;
it cannot raise its own instruction priority. `hydrate_memory=False` disables
this read path without changing candidate staging.

## Research evidence that shaped the core boundary

The following upstream designs were inspected before choosing this boundary:

- [PI agent SDK](https://github.com/pi-packages/earendil-works-pi/blob/main/packages/coding-agent/docs/sdk.md)
  exposes lifecycle events, bounded steering/follow-up queues, parallel tool
  execution with sequential overrides, and session branches. AEGIS should
  adopt the event vocabulary and context/compaction ideas, but keep task
  authority in its existing Rust/Python supervisor.
- [PI-Desktop host-core requirements](https://github.com/vastsa/PI-Desktop/blob/main/docs/spec/03-runtime/05-host-core-rust.md)
  make permission evaluation host-owned, require path containment and
  versioned handshakes, and interrupt pending work on restart without replay.
  These are future desktop acceptance criteria, not a reason to replace the
  current Tauri/Python/Rust stack with Electron/Node.
- [DeepSeek Harness agent-team documentation](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/agent-team.md)
  uses a durable queued message before acknowledging delivery. Its own
  [subagent limitations](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/subagent/subagent/README.md)
  still document process-local residency, loss of accepted-but-unlogged
  messages after a crash, and the need for a durable mailbox plus lease
  protocol for cross-process coordination. AEGIS now provides that narrow
  local mailbox as an opt-in delivery projection; it remains separate from the
  authoritative task ledger.
- Public reporting on the Claude Code package incident says a debugging
  source map was accidentally shipped in a routine package, exposing roughly
  2,000 files and more than 500,000 lines; the reported cause included a manual
  deployment step. The exact public ``512,000`` figure is not independently
  verified here, and no leaked source is used. The reusable engineering lesson
  is an automated publish gate: deny unexpected source maps, compare the
  package manifest with an allowlist, scan secrets, record provenance, and
  verify the published artifact before release.
- [Aider's repository map](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/repomap.md)
  demonstrates a low-context alternative to repeatedly reading whole files:
  rank symbols and signatures against a token budget, then let the agent open
  exact source ranges only when needed. AEGIS already has a hash-bound
  `SourceSnapshot`; the compatible next step is a deterministic map projection,
  not a second code index or an embedding service.
- The [Compaction Cliff study](https://arxiv.org/abs/2608.22752) reports that
  type-blind compaction can lose safety constraints over repeated rounds. Its
  numbers are not an AEGIS benchmark, but the failure mode is directly
  relevant: safety rules, task requirements, artifact hashes, and ordinary
  transcript prose must not share one undifferentiated summary bucket.
- Recent primary research makes automatic semantic-memory promotion a
  security boundary, not just a relevance threshold. [MPBench](https://arxiv.org/abs/2606.04329)
  catalogs memory-write attack channels and reports that more aggressive write
  and retrieval policies increase exposure. [GhostWriter](https://arxiv.org/abs/2607.06595)
  demonstrates injection followed by later activation against memory-backed
  agents. [TMA-NM](https://arxiv.org/abs/2606.24322) further argues that
  summaries, trusted-tool echoes, and repeated corroboration can launder an
  untrusted origin. Therefore repeated model-supplied candidates alone cannot
  authorize ACTIVE memory in AEGIS.

The resulting core priority is: extend the type-aware context budget plan into
sidechain/session evidence, benchmark quality and token cost on fixed tasks,
and run memory-poisoning negative probes before considering any automatic
promotion policy. A model-generated summary is a recovery aid, not permission
to discard authoritative requirements, hashes, leases, or failure states.

The first type-aware context slice is now implemented in the existing Rust
`ContextGovernor`. A context node defaults to `condensable` for compatibility;
callers may explicitly mark it `protected` or `ephemeral`. Protected nodes are
retained before utility selection, are included in the budget, and make an
over-budget request fail closed. The local-swap improvement step cannot replace
a higher-retention-class node with a lower-retention-class one. The Python
hydration compiler carries the class into its manifest and rejects a selector
that omits a protected source. This is retention protection, not a claim that a
model-generated summary preserves arbitrary semantics.

These observations are evidence for boundaries and tests, not evidence that
any upstream implementation should be copied into AEGIS.

## What is intentionally deferred

- No LangGraph, AutoGen, A2A, or broker dependency is added to the core. Their supervisor/subagent, structured message, and artifact ideas informed this boundary. The local SQLite mailbox is deliberately smaller and is not a distributed broker; a remote A2A adapter can be added if a real multi-machine requirement appears.
- No personal login, browser-cookie, or cookie-scraping handling is enabled. `RedditRssQueryProvider` exposes public RSS without credentials; `XAppOnlyQueryProvider` uses only an explicitly supplied/system app bearer token and rejects an unconfigured source. `PublicResearchRouter` composes `reddit:`, `x:`, and `all:` queries for the existing `SearchProgramExecutor`. Internal scraping/login libraries remain out of the core dependency set.
- No automatic code copying is enabled. The future reuse lane must bind source bytes, license/provenance, dependency changes, and verification results, then measure reuse versus generation before choosing a path. A model response is not treated as a source-code database or as proof that training data can be copied verbatim.
- Rust graph validation remains separate from runtime lease admission, while
  the native lease submission carries the validated run-scoped dependency IDs.
  Static and dynamically expanded parent/child plans use those dependency
  edges and are bounded by the existing supervisor. Durable replay of a plan
  created after admission remains intentionally unavailable: a mailbox can
  recover delivery envelopes, but it must not silently re-execute provider or
  external side effects after a crash.
