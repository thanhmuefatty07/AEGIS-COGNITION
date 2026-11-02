# ADR-024: Lab deterministic state authority vs Python SDK cell facade

Status: Accepted (recorded 2026-11-03)

## Context

The Lab capability spans three files across three architectural layers:
1. `core/rust/src/lab.rs` (`L0_core_rust`)
2. `core/rust/src/ffi/lab.rs` (`L1_ffi_bridge`)
3. `aegis_cognition/lab.py` (`L2_python_sdk`)

Because both `core/rust/src/lab.rs` and `aegis_cognition/lab.py` are large
modules defining Lab types, their exact authority boundary and internal module
structure must be explicitly governed to prevent state drift or duplicate
verification logic.

## Decision

- `core/rust/src/lab.rs` is the single canonical authority for the deterministic
  `LabController` state machine, cryptographic event hash-chain, side-effect
  lease tokens, and skill manifest admission invariants.
- `core/rust/src/ffi/lab.rs` is the thin PyO3 bridge (`PyLabController`) that
  exposes `core/rust/src/lab.rs` to Python without adding business logic.
- `aegis_cognition/lab.py` is the Python developer SDK façade (`Lab`,
  `LabSession`, `LabApplication`, `SimulationCell`, `ElectricalSignalCell`,
  `ProcessExecutionCell`, `AdaptiveController`, `UnitRegistry`) that provides
  scientific/engineering cell ergonomics while delegating authoritative state
  transitions and lease verification to the Rust core when native authority is
  active.

## Options considered

1. Reimplement all scientific simulation cells (`SimulationCell`,
   `ElectricalSignalCell`) purely in Rust and remove `aegis_cognition/lab.py`.
   Rejected: Python scientific workflows and custom user callables require a
   first-class Python SDK surface (`aegis_cognition._public_exports`).
2. Allow `aegis_cognition/lab.py` to maintain an independent, unverified state
   machine in PROD trust mode. Rejected: violates ADR-001 (`Rust runtime
   authority`).
3. Maintain the strict `L0` (`core/rust/src/lab.rs`) -> `L1`
   (`core/rust/src/ffi/lab.rs`) -> `L2` (`aegis_cognition/lab.py`) hierarchy and
   modularize internal helpers behind stable public exports. Chosen.

## Migration, security, performance, operations, rollback

- Migration preserves 100% of the 36 public Lab symbols exported in
  `aegis_cognition/_public_exports.py` and all Rust `pub` items in
  `core/rust/src/lab.rs`.
- Security and trust-tier enforcement remain fail-closed: external side effects
  and replay writes require `ExternalSideEffectLease` and `ReplayWriterLease`
  tokens bound to the Lab session.
- Performance overhead of the Python cell dispatch layer remains `<= 2 ms` per
  cell invocation, with hash-chain and lease checks executing in native Rust.
- Operations and packaging (`pyproject.toml` maturin wheel build) continue to
  ship both the compiled `aegis_nerve` extension and `aegis_cognition` Python
  package.
- Rollback is a single commit revert with zero persisted state migration.

## Evidence

- `scripts/generate_capability_ownership.py --check` verifies the mapping of
  `CAP-RS-LAB` (`core/rust/src/lab.rs`) and `CAP-PY-LAB-SDK`
  (`aegis_cognition/lab.py`).
- `tests/test_lab_platform.py` and `tests/test_lab_scientific_Computing.py`
  verify Python cell ergonomics, unit dimensional checks, and Rust controller
  integration.
