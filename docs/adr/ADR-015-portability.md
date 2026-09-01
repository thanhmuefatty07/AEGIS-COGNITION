# ADR-015: CPU baseline and capability honesty

Status: Accepted (renumbered from ADR-012, 2026-08-27)

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
macOS remains cooperative until an equivalent adapter exists. Capability
absence is a visible result, never a silent downgrade of a hard requirement.

## Trade-offs and consequences

Portable behavior is conservative and may leave performance unused. Platform
adapters can enforce more without changing callers. A capability claim is
scoped to the measured host and enforcement tier.

## Rejected alternatives

Claiming universal kernel isolation was rejected. Silent fallback from a hard
control to measurement was rejected.

## Migration, security, performance, operations, rollback

The renumbering is documentation-only: ADR-012 remains as a compatibility
stub, while new references use ADR-015. Compile and test each target, require
privilege-aware live tests, and publish the active backend/capability. Monitor
denied controller operations. Benchmark each tier separately. Roll back to
cooperative mode only with the status visibly changed to `MeasurementOnly` and
the missing enforcement recorded as a blocker.

## Evidence

`resource_platform.rs`, portability docs, Windows target check, the traceability
matrix, and the canonical Lab plan's `LAB-BROWSER-002`/`LAB-OPS-007` disposition.
Hosted privileged OS enforcement and cross-platform parity remain
`NOT VERIFIED` until the corresponding evidence gates pass.
