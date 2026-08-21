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
- The Rust 2024 workspace library targets compile with the separated PyO3
  extension feature disabled for test/linker correctness.
- Runtime admission returns an opaque lease token; Rust retains the authoritative
  grant and fences completion by lease generation and attempt.
- Resource policy thresholds carry explicit `ASSUMED DEFAULTS` provenance and
  unknown host memory maps to a finite conservative cap.
- Linux cgroup v2 and Windows Job Object adapters exist as opt-in controllers;
  a compiled adapter is not evidence that the current process has permission to
  enforce it.
- The root wheel is configured through pinned maturin 1.14.1, includes the
  `core/python` bridge package explicitly, and the native extension uses the
  `python-extension` Cargo feature only for packaging.

## Deliberately not claimed

- `cargo test --workspace` is not a proof of production readiness.
- Wasmtime sandbox escape is not “impossible”; engine vulnerabilities and OS isolation
  remain residual risks.
- Hardware memory capacity is only populated where the portable probe can read an
  authoritative source; unknown is represented as `null`.
- Python fallback capability data is informational and not an admission authority.

## Required follow-up evidence

1. Build/import the maturin PyO3 extension on Tier-1 platforms.
2. Run live cgroup v2 and Windows Job Object process tests with the required privileges.
3. Establish retained H0/H1/H2 workload benchmarks before freezing policy values.
4. Complete Wasmtime fuzz/adversarial/replay-parity evidence.
5. Run dependency, secret, full workspace, and release packaging gates in CI/release environments.

The canonical distribution is the root `aegis-cognition` wheel. The
`core/python/pyproject.toml` file remains a compatibility manifest for bridge
only deployments and tests; it is not a second root build path. The root
maturin wheel is the only package published by the release workflow.
