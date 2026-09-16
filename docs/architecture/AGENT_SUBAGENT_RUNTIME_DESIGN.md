# AEGIS in-process subagent runtime — first vertical slice

Status: implemented as an opt-in coordination primitive; not the default `Agent` execution path yet.

## Decision

Use a manager/supervisor model. The root agent retains final synthesis. Child agents are bounded task handlers, not peer chatbots. A child receives one typed request and compact dependency result packets. Large observations, browser captures, source snapshots, and datasets are references to immutable artifacts rather than inline conversation history.

The implementation reuses AEGIS primitives:

- Rust validates the task graph before execution: positive identities, closed dependencies, cycle rejection, disjoint artifact namespaces, deterministic topological order, and a BLAKE3 graph hash.
- Python 3.14 uses `asyncio.TaskGroup` for structured lifecycle, a bounded semaphore for concurrency, and per-resource async locks for stateful resources such as one browser actor session.
- Each child is admitted through the existing Rust-authoritative runtime lease. The current runtime lease API remains a per-child capacity gate; this slice does not claim that the existing runtime admission path enforces semantic dependencies.
- The existing message transport remains unchanged. The semantic envelope is a versioned JSON payload that can be carried by the existing `MessageFrame` bytes.

## Protocol language

`TASK_REQUEST` contains role, prompt, dependency IDs, capabilities, artifact namespace, exclusive resource keys, side-effect class, token budget, deadline, and idempotency key.

`TASK_RESULT` contains status, a bounded summary, epistemic claims, uncertainty, blockers, token counters reported by the handler, elapsed time, and artifact references. `token_budget` is the child completion/output-token ceiling for the coordination contract; the supervisor rejects a reported `tokens_out` value above it, while provider-specific request-side truncation remains an adapter concern. An absent provider usage field is recorded as `0` but is not evidence that no tokens were consumed. The packet and envelope are hash-bound. The wire payload carries the result body once; envelope identity and artifact references are not duplicated. Dependency context exposes only the compact representation; raw child transcripts are not forwarded automatically.

The scheduler uses direct dependency edges for data flow. It does not create a general-purpose broadcast bus or send progress chatter by default. A future progress stream can use the same envelope with a separately versioned kind after a measured need is demonstrated.

## Isolation rules

Every child must have a unique artifact namespace. A result containing an artifact from another namespace is rejected. A handler declaring an exclusive resource key is serialized with other handlers declaring the same key, including supervisors sharing one Python event loop. Browser actor work must use an exclusive session key; read-only observers should use independent browser contexts or no shared actor key. The scheduler does not make a browser profile or external service safe by itself.

Handlers that declare an exclusive resource must be async. A synchronous handler runs in a worker thread that Python cannot forcibly stop; allowing its timeout to release the resource lock would let the worker continue against a resource that another child has acquired. Synchronous handlers remain available for work that declares no exclusive resource.

## Authority and degraded mode

`AgentSupervisor` requires native graph authority by default. `require_native_authority=False` is an explicit development/test escape hatch and marks the graph as `python_proposal`; it is not a production assurance. A missing native extension is therefore visible rather than silently treated as authoritative. The child runtime guard remains the existing per-child capacity/lease boundary; this slice does not claim a composite parent/child ledger.

The root synthesizer runs once after all child tasks settle. Failed or blocked children are passed to the root with explicit status so the root can decline to overclaim. A child never writes the root answer.

`AgentPlanProposal` is the model-facing planning boundary. A model may return a
strict `aegis-agent-plan-v1` document containing task metadata and a
`handler_key`; the host validates its DAG and binds that key to an already
registered callable. The model cannot select an arbitrary Python callable,
change the native graph authority, or bypass the child runtime guard.

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

## What is intentionally deferred

- No LangGraph, AutoGen, A2A, or broker dependency is added to the core. Their supervisor/subagent, structured message, and artifact ideas informed this boundary; a remote A2A adapter can be added if a real multi-process or multi-machine requirement appears.
- No personal login, browser-cookie, or cookie-scraping handling is enabled. `RedditRssQueryProvider` exposes public RSS without credentials; `XAppOnlyQueryProvider` uses only an explicitly supplied/system app bearer token and rejects an unconfigured source. `PublicResearchRouter` composes `reddit:`, `x:`, and `all:` queries for the existing `SearchProgramExecutor`. Internal scraping/login libraries remain out of the core dependency set.
- No automatic code copying is enabled. The future reuse lane must bind source bytes, license/provenance, dependency changes, and verification results, then measure reuse versus generation before choosing a path. A model response is not treated as a source-code database or as proof that training data can be copied verbatim.
- Rust graph validation is separate from runtime lease admission. Extending the Rust ledger to retain a composite parent/child lifecycle requires a compatibility and replay design; it is not hidden behind this first API.
