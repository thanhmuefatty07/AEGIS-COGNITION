# ADR-023: Execution lanes vs local verification command boundary

Status: Accepted (recorded 2026-11-03)

## Context

The repository contains two modules named `execution`:
1. `core/rust/src/execution.rs` in the Rust core (`L0_core_rust`).
2. `aegis_cognition/verification/execution.py` in the Python verification
   package (`L2_python_sdk`).

Without an explicit architectural decision record, contributors and automated
audits can mistake these two modules for duplicate schedulers or competing
process sandboxes.

## Decision

- `core/rust/src/execution.rs` is the canonical owner of runtime execution lanes
  (`ExecutionLanes`: CPU Rayon thread pool, Tokio async I/O runtime,
  `AcceleratorExecutor` trait seam) and OS-isolated untrusted process execution
  (`run_untrusted_process`, backed by `core/rust/src/resource_platform.rs` and
  `core/rust/src/sandbox.rs`).
- `aegis_cognition/verification/execution.py` is strictly a bounded command-argv
  builder and test-report parser (`LocalVerificationCommand`,
  `build_local_verification_commands`, `run_local_verification_command`) scoped
  to `DevelopmentVerificationSession` and invoked within `ProcessExecutionCell`
  (`process_execution_cell_scope`). It never acts as a general shell or a
  competing runtime lane scheduler.

## Options considered

1. Merge `aegis_cognition/verification/execution.py` into `core/rust/src/execution.rs`.
   Rejected: JUnit XML parsing, pytest/cargo argv discovery, and Python virtual
   environment resolution belong to the high-level developer verification
   session workflow rather than the low-level Rust resource scheduler.
2. Rename `aegis_cognition/verification/execution.py` and break existing imports.
   Rejected: `aegis_cognition/verification/__init__.py` and verification tests
   depend on the module path; documenting and enforcing the boundary preserves
   compatibility without churn.
3. Keep both modules in their respective layers (`L0_core_rust` vs
   `L2_python_sdk`) and enforce their non-overlapping responsibilities in
   `docs/architecture/capability_ownership.json` and
   `scripts/architecture_fitness.py`. Chosen.

## Migration, security, performance, operations, rollback

- Migration requires zero breaking API changes; both modules retain their
  existing public symbols and import paths.
- Security invariants remain fail-closed: `verification/execution.py` rejects
  shell interpolation, caps argv length (`<= 512`), bounds stdout/stderr capture
  to 8 MiB, and requires `process_execution_cell_scope` when gated by Lab
  authority; `core/rust/src/execution.rs` enforces OS resource limits and
  environment scrubbing.
- Performance budgets are orthogonal: Rust `ExecutionLanes` govern sub-millisecond
  task admission, while `verification/execution.py` governs bounded local test
  suite runs (up to `3600 s` timeout cap).
- Operations and CI verify both boundaries via `scripts/architecture_fitness.py`
  and `scripts/generate_capability_ownership.py --check`.
- Rollback is a single commit revert with zero schema or wire-format impact.

## Evidence

- `docs/architecture/capability_ownership.json` maps `CAP-RS-EXECUTION` to
  `core/rust/src/execution.rs` and `CAP-PY-VERIFICATION` to
  `aegis_cognition/verification/__init__.py`.
- `tests/test_resource_runtime.py` and `tests/test_verification_session.py`
  verify the Rust execution lanes and Python verification command runner
  independently.
