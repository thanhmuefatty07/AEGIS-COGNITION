# Memory Agent and Living Workspace research

Status: research decision input; implementation changes must preserve the
contracts in this document and rebind every claim to the commit that is built.

Research capture: 2026-09-14

Repository commit inspected: `ec995b998fd990529bb1459ec2c0701d9a8d389e`

## Purpose

AEGIS is being developed around a local-first Memory Agent and a Living
Workspace. The Memory Agent must keep durable context available without
replaying an ever-growing transcript on every turn. The Workspace must make a
project understandable as a trustworthy, explorable map. A later collaboration
mode may allow several people or agents to work on one workspace, but the first
implementation remains local and single-owner.

This document records what was learned from Archify, relevant memory systems,
and the current AEGIS implementation. It is a design input, not permission to
copy another project or to claim benchmark superiority before measurement.

## Scope and non-goals

In scope:

- durable episodic, semantic, project, and task memory;
- bounded context selection that reduces repeated prompt material;
- a read-only project graph derived from a hash-bound source snapshot;
- evidence, revision, recovery, and access boundaries;
- a versioned seam that can support collaboration later;
- measurable evaluation of answer quality, token use, latency, and failure cases.

Out of scope for the first slice:

- importing Archify or reusing its source, viewer, styles, examples, or assets;
- a hosted diagram editor or hosted memory service;
- a vector database or graph database before a measured requirement exists;
- real-time multi-user transport, CRDTs, or conflict merging;
- claims about GPU acceleration, production availability, or cross-machine scale.

## What Archify demonstrates

Archify describes itself as an agent-oriented renderer that accepts typed JSON
intermediate representation (IR), validates it, and delivers a self-contained
HTML/SVG artifact. Its README and skill contract provide several patterns that
are relevant to AEGIS, while its MIT license and repository remain separate
from this project:

1. **A typed, small source model.** The authoring input is structured data with
   stable IDs, semantic node kinds, explicit relationships, and a bounded main
   path. The visual output is a projection of that source rather than a second
   hand-edited truth.
2. **Fail-closed delivery.** Schema, layout, artifact, route, and label checks
   run before a candidate replaces the last known good artifact. A failed
   candidate cannot silently replace a trusted one.
3. **Deterministic receipts.** Delivery records specification and artifact
   hashes and byte counts. This is useful for binding a diagram to one source
   revision and for detecting stale or tampered output.
4. **Progressive disclosure.** The primary map stays readable while search,
   focus, upstream/downstream reach, route inspection, semantic lenses, and
   guided stories expose detail only when requested.
5. **Evidence is opt-in and revision-pinned.** Source beacons open authored
   files and line ranges tied to a verified public commit. Ordinary diagrams do
   not imply source proof.
6. **Last-good preview.** A watcher may refresh only after a candidate passes
   validation; a broken or incomplete save keeps the previous verified view.
7. **Semantic visual language.** Color, type, and motion have fixed meaning.
   Dark/light themes preserve category identity. Motion is finite and never the
   only carrier of meaning. The supplied reference image follows the same
   principle: one dominant canvas, quiet controls, a restrained grid, and
   semantic colors for boundaries and components.

These are design and verification patterns, not implementation instructions
for importing Archify. Archify explicitly states that it is not a general
purpose editor and that hosted sharing is outside its current scope. AEGIS
should likewise keep the graph view subordinate to memory and evidence rather
than turning it into an unrelated drawing product.

Reference: [Archify repository](https://github.com/tt-a1i/archify), especially
its [product](https://github.com/tt-a1i/archify/blob/main/PRODUCT.md),
[design system](https://github.com/tt-a1i/archify/blob/main/DESIGN.md), and
[skill contract](https://github.com/tt-a1i/archify/blob/main/archify/SKILL.md).

## Relevant market patterns

The systems below were used as comparison points. Their reported results are
not AEGIS evidence and no external benchmark number is adopted as a target
without reproducing the protocol.

### Virtual context and memory tiers

MemGPT frames long-context operation as virtual context management: move useful
information between fast in-context memory and slower external memory, with the
agent controlling transfers. This supports a tiered AEGIS model, but tool-driven
transfers can add calls and prompt overhead if every turn performs them.

Source: [MemGPT paper](https://arxiv.org/abs/2310.08560).

Letta exposes bounded core memory blocks plus recall and archival memory. Its
git-backed memory filesystem makes durable memory inspectable and versioned.
This is a useful product idea for human review, but editable memory must still
be treated as a proposal until AEGIS validates scope, provenance, and conflict.

Sources: [Letta memory schema](https://github.com/letta-ai/letta/blob/main/letta/schemas/memory.py),
[Letta memory filesystem](https://github.com/letta-ai/letta-docs-md/blob/main/configuration/memory/index.md).

### Extraction and hybrid retrieval

Mem0 separates memory writing from reading: it extracts durable facts, performs
deduplication, and combines semantic, keyword, entity, and temporal signals at
search time. It scopes records by user, agent, run, and metadata. The pattern
is valuable, but LLM extraction and embedding on every write are not free and
an extracted fact is not automatically evidence.

Source: [Mem0 design](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx)
and the [Mem0 paper](https://arxiv.org/abs/2504.19413).

### Checkpoints and stores

LangGraph distinguishes thread-scoped short-term checkpoints from long-term
stores. It recommends trimming, deleting, summarizing, and retaining
checkpoints deliberately; in-memory savers are for tests, not restart safety.
This confirms that conversation continuity and durable semantic memory should
be separate AEGIS contracts.

Sources: [LangGraph persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/),
[LangGraph memory guide](https://langchain-ai.github.io/langgraph/how-tos/persistence/).

### Temporal and graph memory

Graphiti stores episodes in a bi-temporal model: ingestion time and the time the
described event occurred. It processes episodes for the same group sequentially
to avoid races. This is a good model for temporal updates and contradiction
analysis, but it adds entity resolution, graph maintenance, and consistency
costs. AEGIS should first keep a compact revisioned record and add temporal
edges only where a measured query needs them.

Source: [Graphiti MCP implementation](https://github.com/getzep/graphiti/blob/main/mcp_server/src/graphiti_mcp_server.py).

Microsoft GraphRAG separates local entity-grounded search from expensive global
map-reduce search and a broader DRIFT mode. The separation is useful: local
queries should be the normal path, while global synthesis should be explicit or
offline because it consumes substantially more resources.

Sources: [GraphRAG query overview](https://github.com/microsoft/graphrag/blob/main/docs/query/overview.md),
[GraphRAG local search](https://github.com/microsoft/graphrag/blob/main/docs/query/local_search.md).

### Codebase maps and token budgets

Aider builds a dependency graph of files, ranks important symbols, and selects
the most useful portions under a configurable token budget. It expands the map
when no files are already in context. This is the closest direct reference for
the code-graph side of the Living Workspace: send a compact map first, then
hydrate exact files or symbols only on demand.

Source: [Aider repository map](https://aider.chat/docs/repomap.html).

## Evaluation that matters

LongMemEval tests information extraction, multi-session reasoning, temporal
reasoning, knowledge updates, and abstention across 500 questions. Its design
shows why a single “retrieval accuracy” number is inadequate: an agent must
also update stale facts and abstain when evidence is insufficient.

Source: [LongMemEval](https://arxiv.org/abs/2410.10813).

LoCoMo focuses on very long conversations, including question answering,
event summarization, and multimodal dialogue generation. It is useful for
long-horizon recall, but it does not by itself prove safe tool execution or
correct project-graph changes.

Source: [LoCoMo data and code](https://github.com/snap-research/locomo).

The first AEGIS evaluation slice should therefore measure, with the same corpus
and answer model for every comparator:

- recall of required facts and evidence at `k` candidates;
- multi-hop and temporal answer accuracy;
- knowledge-update correctness, stale-fact rejection, and abstention;
- input tokens, output tokens, number of retrieval/LLM calls, and p50/p95
  latency;
- deterministic replay of the same query and memory revision;
- scope isolation, deletion/restore behavior, and contradiction handling;
- source-map usefulness: time to locate the right file/symbol and rate of
  candidate edges that require correction.

No target percentage is set in this document. A target becomes meaningful only
after a reproducible baseline run is recorded.

## Current AEGIS capabilities

The current repository already contains substantial primitives that align with
the above direction:

- `core/python/aegis/code_intelligence.py` emits a bounded, hash-bound
  `SourceSnapshot`. It caps files, per-file bytes, total bytes, symbols, imports,
  and watcher events; records Git identity, file hashes, symbols, imports,
  lineage candidates, stale/deleted paths, and overflow status.
- `core/rust/src/context.rs` provides an activation graph, a two-hop maximum,
  an activation cap, required evidence nodes, deterministic utility ordering,
  bounded token selection, local swaps, and context-pack digests.
- `core/python/aegis/context_compiler.py` hydrates only authorized scoped
  sources, preserves mandatory items, applies a hard token budget, records a
  manifest, and hashes that manifest. Its byte-based token estimate is a
  bounded compatibility heuristic, not a model-token measurement.
- `core/rust/src/memory/repository.rs` owns a SQLite memory record lifecycle
  with scopes, grants, revisions, content/request hashes, idempotent capture,
  validation, correction, forget/restore/purge transitions, an FTS projection,
  dirty-index recovery, and an online backup path.
- `core/rust/src/memory/fold.rs`, `frame.rs`, `fidelity.rs`, `nudge.rs`, and
  `session_search.rs` provide the existing CogniFold, semantic-pointer,
  fidelity, candidate, and session-search primitives.
- `aegis_cognition/desktop_service.py` already exposes versioned workspace,
  source-snapshot, conversation, and memory commands. It refreshes a bounded
  source snapshot before provider prompt construction.
- `desktop/src/protocol.ts` validates the command/response envelope and already
  reserves memory operations (`search`, `inspect`, `capture`, `correct`,
  `forget`, `restore`, and `purge`).

The current desktop `App.tsx` is still a conversation/status surface. It does
not yet render a project graph, memory graph, source-map legend, focused proof
panel, or revision comparison. That is a product gap, not a reason to weaken
the existing source and evidence contracts.

## Proposed AEGIS model

### Memory tiers

Keep one source of truth per fact and expose separate projections:

1. **Active context:** a small task prefix, current constraints, mandatory
   evidence references, and the latest user turn.
2. **Working session:** checkpointed task state, decisions, open questions,
   tool results, and replay metadata for the current run.
3. **Episodic memory:** append-only raw turns and observations, retained for
   audit and later extraction rather than injected wholesale.
4. **Semantic memory:** atomic facts, decisions, preferences, and lessons with
   scope, owner, provenance, validity interval, revision, and confidence.
5. **Project graph:** a rebuildable projection of files, symbols, imports,
   authored relationships, and memory/evidence links for the open workspace.
6. **Cold evidence/archive:** large artifacts and sealed run segments referenced
   by digest, never copied into every prompt.

The active context is a cache and view. Durable records remain authoritative;
compaction or summarization must never silently destroy them.

### Read path

Use a staged, mostly local path:

1. filter by owner, scope, revision, lifecycle, and time when applicable;
2. query the cheap lexical/metadata index;
3. expand only a bounded neighborhood around the active task or matched
   entities (normally one or two hops);
4. optionally consult a cold vector or graph index when the cheap path is
   insufficient;
5. score candidates deterministically using relevance, dependency coverage,
   freshness, evidence quality, contradiction risk, and token cost;
6. let the Rust Context Governor select a pack under a hard budget;
7. hydrate exact authorized content once and emit a manifest/digest.

The normal turn must not ask an LLM to rediscover the entire graph or replay the
whole transcript. A global graph summary is an explicit, cacheable operation.

### Write path

Every write should follow this order:

```text
capture raw observation
→ assign owner/scope/source/revision
→ deduplicate by stable request and content hashes
→ create a candidate fact or event
→ validate identity, size, policy, and provenance
→ detect conflict/update/forget semantics
→ commit an immutable revision
→ rebuild or mark the projection dirty
→ emit replay/evidence metadata
```

LLM extraction may propose a semantic fact, but it must not silently rewrite a
previous fact or become execution authority. Corrections create a new revision;
forget and purge remain explicit and auditable. A stale candidate must fail
closed when it is mandatory and be discarded or marked stale when optional.

### Workspace graph

The graph view should consume one `SourceSnapshot` revision and render a
read-only `WorkspaceGraph` projection with stable IDs:

- project and package boundaries;
- files and symbols;
- imports/calls/lineage candidates, each with relationship kind and certainty;
- memory and evidence references attached to the relevant file, symbol, or
  task;
- changed, stale, deleted, overflowed, or unverified states;
- optional directory/deployment boundaries only when source evidence supports
  them.

Candidate lineage must be visibly different from compiler-proven relationships.
The UI should offer a primary path first, then progressive actions such as
search, focus, upstream/downstream reach, exact source location, and revision
delta. The graph never writes memory or source files merely because a node was
focused.

Adopt the visual vocabulary suggested by the reference image: a dominant
canvas, restrained controls, semantic category colors, quiet cards, readable
mono-compatible labels, and finite interaction feedback. Do not copy Archify's
CSS, SVG, generated assets, wording, or layout source. If any Archify code or
asset is ever reused, stop and record the required MIT notice and provenance
first; the default plan reimplements the concepts independently.

### Collaboration seam for later

Do not build a real-time service in the local-first slice. Preserve a future
protocol around immutable workspace revisions and append-only operations:

```text
workspace_id
actor_id
operation_id
base_revision
operation_kind
payload_digest
created_at
```

Every mutation should be idempotent by `operation_id` and reject an incompatible
`base_revision` rather than silently merging. A later sync service can add
leases, ordered delivery, or a CRDT after real collaboration requirements are
known. The local implementation should already be able to export a revision
and replay operations deterministically, so a future network layer does not
become the source of truth.

## Decision and implementation order

1. **Freeze the contracts.** Define `WorkspaceGraph` and memory manifest
   schemas, stable IDs, certainty labels, revision binding, and the evidence
   class for every field. Do not add a database or dependency at this step.
2. **Build one local vertical slice.** Convert the existing `SourceSnapshot` to
   a bounded graph projection, render it in the Tauri desktop surface, and add
   focus/search/source-proof interactions. Keep the projection read-only.
3. **Connect memory progressively.** Show selected semantic memories and
   evidence beside a focused file/symbol; use the existing Context Compiler and
   Rust Governor rather than a second selector.
4. **Measure token economy.** Run fixed corpora and compare full transcript,
   lexical/metadata, graph-bounded, and optional vector variants. Record
   accuracy, abstention, token counts, calls, latency, and storage. Choose a
   richer index only when it wins a stated workload after its write and runtime
   cost.
5. **Harden persistence and recovery.** Test revision conflicts, stale
   candidates, crash-boundary recovery, backup/restore, dirty-index rebuild,
   scope isolation, forget/restore/purge, and deterministic replay.
6. **Add visual quality gates.** Validate the exact delivered artifact, run
   browser interaction checks at the supported desktop sizes, verify keyboard
   and reduced-motion behavior, and retain last-good output on invalid source.
7. **Only then design collaboration.** Use exported immutable revisions and
   operation replay as the compatibility contract before choosing a sync
   mechanism.

## Acceptance gates

The first Memory Agent + Workspace release slice is acceptable only when all
applicable gates have evidence tied to one commit:

- **Memory correctness:** scoped reads/writes, revision and hash checks,
  idempotency, correction, forget/restore/purge, conflict rejection, and
  recovery pass with positive and negative cases.
- **Context economy:** every pack stays within the hard budget; mandatory
  overflow is explicit; repeated-turn cost and answer quality are measured
  against a fixed baseline; no token-saving claim is made from a heuristic
  alone.
- **Project truth:** the graph revision matches the source snapshot; file and
  symbol identities are stable; candidate relationships are labeled as such;
  missing, stale, deleted, and overflowed scans cannot appear complete.
- **Evidence:** manifests contain source/revision IDs, selected records, token
  accounting, artifact digests, and environment identity. A stale artifact
  cannot replace the last known good artifact.
- **Viewer quality:** search/focus/reach/source actions are deterministic,
  keyboard reachable, readable in dark and light modes, contained at supported
  desktop sizes, and understandable without animation.
- **Security/privacy:** owner and scope checks are enforced server-side,
  secrets and unredacted sensitive data are excluded, external content is
  treated as untrusted, and memory poisoning/cross-workspace leakage tests
  fail closed.
- **Future seam:** local operation replay and revision export are deterministic;
  real-time collaboration remains explicitly `NOT IMPLEMENTED` until its
  requirements and failure model are approved.

## Current research conclusion

The direction is sound: AEGIS already has a stronger evidence and resource
boundary than a typical memory plugin, and Archify supplies a useful model for
turning a bounded source projection into a compelling, inspectable artifact.
The next semantic change should be the local read-only `SourceSnapshot →
WorkspaceGraph → desktop viewer` slice, integrated with the existing bounded
context compiler. It should be implemented only after the schemas and
benchmark/evidence contract are frozen. No external system is being adopted as
the memory authority, and no claim of reduced tokens or superior retrieval is
made until the paired baseline measurements exist.

\n