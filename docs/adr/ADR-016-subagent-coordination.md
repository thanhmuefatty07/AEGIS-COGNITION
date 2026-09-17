# ADR-016: In-process subagent coordination with root-owned synthesis

Status: Accepted (2026-09-16)

## Context and problem

AEGIS needs local subagents that can research, use browser/vision adapters, and
work concurrently on Windows, macOS, and Linux while one root agent retains
responsibility for the final answer. Peer-to-peer transcript sharing would
increase token cost and make ownership, cancellation, and conflicting writes
ambiguous. Model-generated plans are also untrusted input and must not become
arbitrary Python execution or external-write authority.

## Decision

- Keep coordination inside the existing Python/Rust modular monolith. Rust
  validates the bounded DAG and computes the graph hash; Python 3.14
  `TaskGroup`, a semaphore, and sorted resource locks run independent children
  on the same machine.
- Forward each child’s run-scoped hashed dependency IDs into the existing Rust
  runtime admission call. Python waits for dependency results for data flow,
  while Rust also sees the same edges for admission ordering; this does not
  introduce a second scheduler or a composite parent/child ledger.
- Use versioned `TASK_REQUEST`/`TASK_RESULT` envelopes with hash-bound packets,
  compact dependency summaries, explicit evidence classes, and immutable
  artifact references. No progress broadcast or raw transcript forwarding is
  enabled by default.
- Provide an optional bounded in-process `AgentMessageJournal` for a desktop
  observer. Cursor reads and `resync_required` handle slow consumers. For a
  process boundary or restart-recovery path, provide an optional SQLite
  `AgentMailbox` that persists the same canonical envelopes, deduplicates by
  idempotency key, and uses bounded leases/retries. It is at-least-once
  delivery, not a second task authority or an exactly-once side-effect claim.
- Let the root optionally produce an unsigned plan. The host hashes it, checks
  task/capability/side-effect/resource limits, binds only registered handlers,
  and invokes root synthesis once after children settle. Direct parent/child
  composite lifecycle remains deferred until the Rust ledger can persist it.
- Reuse the existing `SearchProgramExecutor` for the default public research
  handler. Reddit uses public RSS without user credentials; X is app-only and
  requires an explicitly configured system bearer token. Browser/vision work is
  injected through trusted handlers backed by the existing BrowserCell and
  Playwright evidence collector; an actor session gets an exclusive resource
  key and observers remain read-only.
- Keep exact code reuse separate from planning. Reuse requires local snapshot,
  SHA-256, license/ownership, provenance, fresh source bytes, and explicit
  materialization; model weights are not treated as a code database.

## Options considered

1. Add LangGraph, AutoGen, A2A, or a broker as the core scheduler. Rejected for
   this phase: their useful supervisor/message ideas are compatible references,
   but a new runtime would add dependency, deployment, and failure surfaces
   without a current multi-process requirement.
2. Let children chat freely or all share full context. Rejected: larger token
   payloads, unclear authority, prompt-injection amplification, and difficult
   deterministic replay.
3. Make subagents silently replace `Agent.run()` immediately. Rejected: this
   is a behavior and cost change without workload measurements. The additive
   `run_subagents` API is the reversible boundary; automatic defaulting requires
   a later measured policy decision.

## Migration, security, performance, operations, rollback

Migration is additive: callers opt into `run_subagents`; the existing
`Agent.run()` path and persisted schemas remain unchanged. Removing the new
modules and methods is the rollback path because no data migration is needed.

The planner cannot select an arbitrary callable, use personal cookies, or grant
itself `ExternalSideEffect` under the default application policy. Public-source
content remains untrusted and is bounded before it reaches the root. Native
authority is required by default; Python degraded mode is explicit for tests and
development only. The design assumes duplicate/reordered messages and uses
idempotency keys, task identity checks, namespace isolation, deadlines, and
bounded memory/token/task limits.

The implementation enables measured overlap for independent tasks and removes
duplicate envelope/artifact bytes from result messages. It does not claim a
universal speedup or token reduction; workload benchmarks, browser visual-model
quality, multi-process execution, and production SLOs remain `NOT VERIFIED`.

## Evidence

- [`AGENT_SUBAGENT_RUNTIME_DESIGN.md`](../architecture/AGENT_SUBAGENT_RUNTIME_DESIGN.md)
  records the protocol and ownership boundary.
- `tests/test_runtime_contracts.py`, `tests/test_runtime_coordination.py`,
  `tests/test_subagents.py`, `tests/test_application_subagents.py`,
  `tests/test_research_adapters.py`, and `tests/test_code_reuse.py` provide the
  local regression closure for this slice.
- `test_runtime_submission_preserves_dependency_ids` and
  `test_runtime_admission_receives_the_same_hashed_dependency_dag` verify that
  the Python and native admission boundaries receive the same dependency
  contract.
- Rust graph validation is in `core/rust/src/agent_coordination.rs` and is
  exposed through `core/rust/src/ffi/agent_coordination.rs`; native smoke
  execution reports `authority=native_runtime`.
