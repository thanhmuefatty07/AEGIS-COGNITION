# ADR-014: Future Agent work kind

Status: Accepted (2026-08-14)

## Context and problem

Agent loops combine model calls, tools, Python, and untrusted execution. Treating
them as a single unrestricted work kind would defeat lane and budget controls.

## Constraints and options

Every work kind needs explicit resource requests, side-effect class, deadlines,
and a lane mapping. Options were a generic catch-all, separate hard-coded
agents, or a future composite work kind.

## Decision and rationale

Keep `WorkKind::Agent` explicit and map it to the Python cognition lane until a
composite scheduler contract is designed. Agent expansion remains deferred.

## Trade-offs and consequences

Current agent execution is conservative and may serialize more than necessary,
but cannot silently bypass admission. A future composite must preserve attempt
and token fencing.

## Rejected alternatives

Mapping unknown agent work to unlimited CPU or untrusted execution was rejected.

## Migration, security, performance, operations, rollback

Define child-task ownership, budgets, cancellation propagation, and evidence
bindings before expanding. Test nested failures and side-effect retries; measure
latency per child lane. Roll back by treating the agent as a bounded Python task.

## Evidence

The explicit enum/lane mapping is implemented; composite-agent semantics remain
`NOT VERIFIED` and are intentionally outside this closure pass.
