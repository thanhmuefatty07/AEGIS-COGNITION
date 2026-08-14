# ADR-007: Normalized hardware profile

Status: Accepted (2026-08-14)

## Context and problem

Scheduling decisions need CPU, memory, accelerator, storage, and OS-control
signals without pretending every platform exposes exact topology.

## Constraints and options

The model must serialize across the FFI, preserve unknown values, and remain
CPU-first. Options were platform-specific structs, guessed totals, or a
normalized profile with optional measurements.

## Decision and rationale

`HardwareProfile` contains explicit memory domains, CPU capabilities,
accelerators, storage, control capabilities, profile epoch, and a
`ResourcePolicy` provenance record. Unknown memory is conservative, finite, and
never represented as an unbounded grant.

## Trade-offs and consequences

The model is verbose and may underutilize unknown hosts, but it prevents unsafe
over-admission. Platform adapters can add precision without changing contracts.

## Rejected alternatives

`u64::MAX / 4` for unknown RAM was rejected because it fails open. Mandatory
physical-core/NUMA detection was rejected because portable probes cannot prove it.

## Migration, security, performance, operations, rollback

Add fields with serde defaults and increment the contract only for incompatible
meaning. Treat probe data as untrusted input and validate arithmetic. Cache
profiles for an epoch, not forever. Roll back by retaining the prior schema
decoder and selecting a conservative profile.

## Evidence

`core/rust/src/resource.rs`, unknown-memory test, and policy provenance in the
traceability matrix.
