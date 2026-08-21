# AEGIS architecture

This directory records the implementation baseline for the modular-monolith
architecture described in `AEGIS_ARCHITECTURE_VERSION_MASTER_PLAN_2026-08-13.md`.

The current runtime direction is intentionally conservative:

- Rust owns authoritative task, resource, capability, cancellation, and evidence transitions.
- Python owns intent interpretation, provider integrations, and semantic proposal generation.
- Cross-language calls use coarse, versioned contracts rather than per-field scheduler mutations.
- CPU-only execution is mandatory; accelerator adapters are optional.
- OS enforcement is reported by capability level. Unsupported controls are never advertised as hard limits.

The current closure slice includes the Rust resource contract, fenced runtime,
bounded execution primitives, and opt-in Linux/Windows OS adapters. It still
does not certify production readiness, cross-host benchmark superiority, or
that a platform adapter is active merely because it is compiled.

## Current implementation map

| Concern | Implementation | Evidence status |
|---|---|---|
| Hardware profile | `core/rust/src/resource.rs` | Rust unit tests + local probe |
| Resource request/lease | `core/rust/src/resource.rs` | Rust unit tests |
| Bounded admission | `core/rust/src/resource.rs` | Rust unit tests |
| Execution lane limits | `core/rust/src/resource.rs`, `core/rust/src/execution.rs` | bounded executor unit tests; stress fairness NOT VERIFIED |
| Accelerator seam | `core/rust/src/resource.rs`, `core/rust/src/execution.rs` | optional lane fails closed when no device is advertised; vendor backend NOT VERIFIED |
| Python boundary | `core/rust/src/ffi.rs`, `aegis_cognition/runtime.py` | opaque token tests; native wheel build required |
| OS resource control | `core/rust/src/resource_platform.rs` | Linux fixture + Windows target compile; macOS cooperative adapter; live privileged tests NOT VERIFIED |
| End-to-end scheduler/TaskLedger integration | `core/rust/src/runtime.rs`, `task_ledger.rs` | lease/state/attempt integration tests |
| Policy provenance | `ResourcePolicy`, benchmark protocol | ASSUMED until H0/H1/H2 retained outputs |
| Wasmtime migration | `docs/architecture/WASMTIME_MIGRATION.md` | static/security gate + Rust tests required |
| Runtime telemetry facade | `core/rust/src/telemetry.rs`, `schemas/runtime-telemetry-v1.json` | bounded, correlated, non-authoritative sink tests; OTel export NOT VERIFIED |

The durable standards mapping is in
[`STANDARDS_APPLICABILITY.md`](STANDARDS_APPLICABILITY.md). The testing tiers
and evidence-label rules are in
[`TESTING_AND_EVIDENCE.md`](TESTING_AND_EVIDENCE.md).
