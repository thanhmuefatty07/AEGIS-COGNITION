# ADR-009: Separate execution lanes

Status: Accepted (2026-08-14)

## Context and problem

Python cognition, native CPU work, I/O, accelerators, and untrusted work have
different blocking and isolation behavior. One executor permits interference.

## Constraints and options

CPU-only operation is mandatory; lane limits must be bounded and policy-driven.
Options were one global pool, advisory labels, or registry-backed executors.

## Decision and rationale

`ExecutionLaneRegistry` accounts lanes and `ExecutionLanes` provides concrete
Rayon CPU, current-thread Tokio I/O, Python-blocking, and untrusted primitives.
All primitives acquire and release the corresponding lane.

The untrusted native-process path uses an argv-only `Command` invocation and
requires `ResourceController::apply_to_process` before the child is allowed to
run. Cooperative closures remain available only for trusted adapters and unit
tests. `AcceleratorExecutor` is a capability seam; vendor SDKs are not core
dependencies.

## Trade-offs and consequences

Extra pools and synchronization cost resources, but prevent a blocking class
from consuming every slot. Accelerator lane capacity is zero when no device is
advertised.

## Rejected alternatives

Labels without enforcement were rejected because the audit found accounting-only
lanes. A single unbounded Tokio pool was rejected for CPU and untrusted work.

## Migration, security, performance, operations, rollback

Map every `WorkKind` in one function, catch panics at the boundary, and run
stress tests for release mismatch. Record active/limit/queue metrics. Benchmark
H0/H1/H2 before freezing limits. Roll back by disabling a lane or reducing
capacity, never by bypassing admission.

## Evidence

`core/rust/src/execution.rs`, `resource.rs`, and `ExecutionLanes` unit tests.
