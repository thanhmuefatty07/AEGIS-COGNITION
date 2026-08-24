# ADR-008: Admission before spawn

Status: Accepted (2026-08-14)

## Context and problem

Unbounded work creation can exhaust CPU, memory, descriptors, processes, or
provider budgets before a scheduler observes the load.

## Constraints and options

Admission must be deterministic, bounded, and reclaimable. Options were spawn
then monitor, a best-effort semaphore, or a multidimensional Rust admission
controller before execution.

## Decision and rationale

Validate a `ResourceRequest`, check checked arithmetic against capacity, issue a
Rust-held lease, and only then allow execution. Queues have finite limits and
unknown capacity is conservative.

Queued requests are re-attempted only by `AuthoritativeRuntime::drain_queued`,
which restores `TaskLedger` state before returning an opaque token. Deadline
reaping releases the lease and lane counters before moving a task to the
explicit `TimedOut` state. Resource samples may reduce future capacity but
cannot revoke an active lease.

## Trade-offs and consequences

Some work queues instead of starting immediately, and throughput can be lower
than optimistic overcommit. In exchange, accounting is auditable and release
can be fenced.

## Rejected alternatives

Optimistic overcommit and queue-until-memory-fails were rejected as unsafe
failure modes.

## Migration, security, performance, operations, rollback

Every new work kind maps to an explicit lane and request shape. Fuzz bounds and
test duplicate release. Record queue/rejection reasons, not secrets. Measure
admission latency and queue tail behavior. Rollback requires draining active
leases before changing capacity semantics.

## Evidence

`AdmissionController`, checked resource arithmetic, bounded queue tests, and
runtime integration tests.
