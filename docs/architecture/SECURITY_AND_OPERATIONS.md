# Security and operations applicability

Rust remains the authority for task state, resource admission, lease fencing,
cancellation, sandbox policy, and replay/evidence transitions. Python and
telemetry are proposal/observation surfaces and cannot mint authoritative
grants or Done state.

## Security controls

- Versioned, coarse FFI contracts use opaque lease tokens; forged, stale, and
  duplicate completion attempts are rejected.
- Unknown memory is bounded by a finite conservative cap; queue admission and
  arithmetic fail closed.
- Wasmtime fuel, epoch interruption, memory limits, and deny-by-default network
  policy are statically checked and behavior-tested. The host process remains
  the OS security boundary.
- Linux cgroup v2 and Windows Job Object controls are opt-in adapters. macOS is
  explicitly measurement/cooperative-only until a native enforcement adapter and
  live evidence exist.
- Dependency locks, internal SBOM/provenance hashes, dependency audit, and secret
  scan are required gates. An internal report never substitutes for an external
  signed release attestation.

## Operations controls

Runtime metrics must identify admission, queue depth, lane saturation, resource
sampling, cancellation, timeout, sandbox, provider, and replay outcomes without
leaking prompts, credentials, or provider payloads. Telemetry is non-authoritative
and may be disabled without changing the state machine.

SLOs and capacity limits are intentionally not frozen until retained H0/H1/H2
workload data exists. Rollback is a reviewed version/feature change that keeps
persisted schema and FFI compatibility checks in the release gate.

## Open evidence

GitHub branch protection/ruleset state, signed commits/tags, privileged live OS
tests, web/control-plane ASVS/WSTG coverage, full OTel export, and production
restore/rollback drills remain `NOT VERIFIED` unless a retained external or
runner artifact proves them.
