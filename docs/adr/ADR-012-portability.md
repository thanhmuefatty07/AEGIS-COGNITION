# ADR-012: CPU baseline and capability honesty

Status: Accepted (2026-08-14)

## Context and problem

Tier-1 platforms differ in quotas, topology, and OS controls. Advertising hard
limits that are only measurements creates unsafe expectations.

## Constraints and options

CPU-only paths must work everywhere; Linux cgroup and Windows Job Object
adapters are opt-in. Options were platform forks, a lowest-common-denominator
contract, or capability levels with adapters.

## Decision and rationale

Use the normalized profile and `EnforcementLevel`; provide Linux cgroup v2 and
Windows Job Object controllers, while keeping unsupported controls explicit.
macOS remains cooperative until an equivalent adapter exists.

## Trade-offs and consequences

Portable behavior is conservative and may leave performance unused. Platform
adapters can enforce more without changing callers.

## Rejected alternatives

Claiming universal kernel isolation was rejected. Silent fallback from a hard
control to measurement was rejected.

## Migration, security, performance, operations, rollback

Compile and test each target, require privilege-aware live tests, and publish
the active backend/capability. Monitor denied controller operations. Benchmark
each tier separately. Roll back to cooperative mode only with the status visibly
changed to `MeasurementOnly`.

## Evidence

`resource_platform.rs`, portability docs, Windows target check, and the
traceability matrix.
