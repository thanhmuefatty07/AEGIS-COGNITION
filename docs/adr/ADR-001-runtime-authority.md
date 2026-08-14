# ADR-001: Rust owns runtime authority

Status: Accepted (2026-08-14)

## Context and problem

Task status, attempts, resource grants, cancellation, and evidence validity
must remain correct when Python crashes, hangs, retries, or sends malformed
data. A Python-returned lease cannot be treated as proof of authority.

## Constraints and options

Constraints are a single-node deterministic core, a PyO3 boundary, bounded
queues, and future alternate Python hosting. Options were Python authority,
split authority, or Rust authority with Python proposals.

## Decision and rationale

Rust owns `TaskLedger`, admission, lease lookup, lifecycle transitions,
cancellation, and reconciliation. Python submits typed proposals and receives
opaque tokens. This leaves one state machine and one source of truth.

## Trade-offs and consequences

The boundary is less flexible and requires explicit Rust contracts, but replay,
fencing, and failure handling are auditable. Python integrations remain easy to
replace, while native build/import becomes a release requirement.

## Rejected alternatives

Returning the full `ResourceLease` to Python was rejected because callers could
return altered task, lane, or grant fields. A dual-writer ledger was rejected
because conflict resolution would be another authority problem.

## Migration, security, performance, operations, rollback

Migration uses `ResourceLeaseToken { schema, lease_id, generation, attempt_id }`
and Rust lookup. Security review must test forged, stale, duplicate, and wrong-
attempt tokens. Lookup is O(log n) in the active lease map; no network hop is
introduced. Operations expose reconciliation rather than silently repairing a
counter. Rollback is a reviewed contract-version change, never a trust-boundary
reversion without fencing tests.

## Evidence

`core/rust/src/runtime.rs` integration tests and `docs/architecture/TRACEABILITY.md`.
