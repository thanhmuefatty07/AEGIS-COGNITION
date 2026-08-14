# Architecture v1 traceability matrix

Status is evidence-based as of 2026-08-14 (UTC+7). `PROVEN` means a repository
artifact or deterministic test currently demonstrates the requirement;
`MEASURED` means a benchmark was run and its output is retained; `ASSUMED`
means an explicit default is in use but has not been frozen by H0/H1/H2 data;
`NOT VERIFIED` means the required external or platform evidence is still open.

| ID | Requirement/source | Implementation artifact | Evidence / current status |
|---|---|---|---|
| AUTH-001 | Rust is authoritative for task/resource state | `core/rust/src/runtime.rs`, `task_ledger.rs` | `PROVEN`: runtime integration tests and no lease grants in FFI token |
| FFI-001 | Python cannot return a mutable lease grant | `schemas/lease-token-v1.json`, `resource.rs`, `ffi.rs`, `runtime.py` | `PROVEN`: token contains schema/id/generation/attempt only; forged-token test |
| STATE-001 | Formal task lifecycle and attempt fencing | `task_ledger.rs` | `PROVEN`: legal transitions, `AttemptId`, retry/cancel/reconciliation tests |
| RES-001 | Bounded, fail-safe admission | `resource.rs` | `PROVEN`: finite unknown-memory cap, checked arithmetic, bounded queue tests |
| POL-001 | Policy numbers require provenance | `ResourcePolicy` | `ASSUMED`: defaults are labeled and H0/H1/H2 freeze is pending |
| OS-001 | Linux cgroup v2 enforcement adapter | `resource_platform.rs` | `PROVEN` code path and fixture test; privileged host enforcement `NOT VERIFIED` |
| OS-002 | Windows Job Object enforcement adapter | `resource_platform.rs` | `PROVEN` Windows-target compilation required; live process test `NOT VERIFIED` |
| LANE-001 | Actual bounded CPU/Python/I/O/untrusted primitives | `execution.rs` | `PROVEN` implementation and unit tests; stress fairness `NOT VERIFIED` |
| PY-001 | Production/forward Python lanes | `.python-version`, `.github/workflows/ci.yml` | `PROVEN` policy is 3.14.7 / 3.15.0rc1 with prereleases enabled |
| PKG-001 | Maturin owns the native wheel build | `pyproject.toml`, Cargo `python-extension` feature | `PROVEN` configuration; wheel build is CI/release evidence |
| RUST-001 | Rust 2024 migration | all workspace manifests and Windows FFI declarations | `PROVEN` workspace library check passes |
| WASM-001 | Wasmtime 47 migration is security-reviewed | `Cargo.toml`, `WASMTIME_MIGRATION.md`, sandbox tests | `PROVEN` configuration/static gates; fuzz and hostile-kernel evidence `NOT VERIFIED` |
| CI-001 | CI runs the resolved environment | `.github/workflows/ci.yml` | `PROVEN` Python uses `uv run --locked`; prior red run is superseded only after rerun |
| DOC-001 | Decisions and requirements are durable | `docs/adr/`, this matrix | `PROVEN` ADRs carry decision, security, performance, operations, rollback, and evidence fields |
| PERF-001 | Hardware policy is measurement-backed | `scripts/resource_policy_benchmark.py` | `MEASURED` only for explicitly recorded local runs; H0/H1/H2 freeze `NOT VERIFIED` |
| SCM-001 | Dependency/secret/release gates are reproducible | `scripts/supply_chain_gate.py`, CI | `PROVEN` gate definitions; final green CI/release run required |

## Evidence discipline

Claims in README and release notes must link to this matrix or a retained
benchmark artifact. A local run on one workstation cannot be relabeled as H0,
H1, or H2 without recording the hardware profile, OS, toolchain, workload,
sample count, and raw output.
