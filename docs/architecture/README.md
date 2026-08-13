# AEGIS architecture

This directory records the implementation baseline for the modular-monolith
architecture described in `AEGIS_ARCHITECTURE_VERSION_MASTER_PLAN_2026-08-13.md`.

The current runtime direction is intentionally conservative:

- Rust owns authoritative task, resource, capability, cancellation, and evidence transitions.
- Python owns intent interpretation, provider integrations, and semantic proposal generation.
- Cross-language calls use coarse, versioned contracts rather than per-field scheduler mutations.
- CPU-only execution is mandatory; accelerator adapters are optional.
- OS enforcement is reported by capability level. Unsupported controls are never advertised as hard limits.

The first implementation slice is the Rust resource contract and bounded admission core.
It does not certify production readiness, kernel-level sandboxing, or benchmark superiority.

## Current implementation map

| Concern | Implementation | Evidence status |
|---|---|---|
| Hardware profile | `core/rust/src/resource.rs` | Rust unit tests + local probe |
| Resource request/lease | `core/rust/src/resource.rs` | Rust unit tests |
| Bounded admission | `core/rust/src/resource.rs` | Rust unit tests |
| Execution lane limits | `core/rust/src/resource.rs` | Rust unit tests |
| Python boundary | `core/rust/src/ffi.rs`, `aegis_cognition/runtime.py` | Python fallback tests; native import requires built extension |
| OS hard enforcement | platform adapters not yet implemented | NOT VERIFIED |
| End-to-end scheduler/TaskLedger integration | not yet wired | NOT VERIFIED |
