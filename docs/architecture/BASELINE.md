# Phase 0 truth baseline

Snapshot captured during implementation on 2026-08-14 (UTC+7).

## Verified repository facts

- Repository default branch is `main`.
- Rust workspace contains the existing `aegis-nerve` crate and plugin/POC members.
- The existing Rust core is a large modular crate with task ledger, replay, evidence,
  sandbox, telemetry, and FFI modules.
- Root Python metadata and `core/python/pyproject.toml` previously declared different
  Python floors; this change aligns both to the supported policy documented in the
  repository ADRs.
- The new resource contracts compile with the existing Rust dependency set.

## Deliberately not claimed

- `cargo test --workspace` is not a proof of production readiness.
- Wasmtime sandbox escape is not “impossible”; engine vulnerabilities and OS isolation
  remain residual risks.
- Hardware memory capacity is only populated where the portable probe can read an
  authoritative source; unknown is represented as `null`.
- Python fallback capability data is informational and not an admission authority.

## Required follow-up evidence

1. Build/import the PyO3 extension on Tier-1 platforms.
2. Add TaskLedger state-transition integration tests around resource leases.
3. Add Linux cgroup v2 and Windows Job Object adapters with platform-specific tests.
4. Establish AEGIS workload benchmarks before enabling Rayon, NUMA, affinity, or accelerator specialization.
5. Run dependency, secret, fuzz, and packaging gates in CI/release environments.
