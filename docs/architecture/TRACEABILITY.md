# Architecture v1 traceability matrix

Status is evidence-based as of 2026-08-25 (UTC+7). `PROVEN` means a repository
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
| RES-002 | Hardware-adaptive feedback cannot revoke active grants | `resource.rs`, `runtime.rs` | `PROVEN`: pressure feedback test; cross-host policy freeze `NOT VERIFIED` |
| RES-003 | Queued work re-enters through TaskLedger, not a second scheduler | `runtime.rs` | `PROVEN`: FIFO queue drain integration test |
| RES-004 | Expired lease is reclaimed as an explicit timeout | `runtime.rs` | `PROVEN`: deadline reaper test |
| POL-001 | Policy numbers require provenance | `ResourcePolicy` | `ASSUMED`: defaults are labeled and H0/H1/H2 freeze is pending |
| OS-001 | Linux cgroup v2 enforcement adapter | `resource_platform.rs` | `PROVEN` code path and fixture test; privileged host enforcement `NOT VERIFIED` |
| OS-002 | Windows Job Object enforcement adapter | `resource_platform.rs` | `PROVEN` Windows-target compilation required; live process test `NOT VERIFIED` |
| LANE-001 | Actual bounded CPU/Python/I/O/untrusted primitives | `execution.rs` | `PROVEN` implementation and unit tests; stress fairness `NOT VERIFIED` |
| LANE-002 | Optional accelerator lane with CPU-only fallback | `resource.rs`, `execution.rs` | `PROVEN`: zero advertised devices reject accelerator work without affecting CPU lanes; vendor adapters `NOT VERIFIED` |
| LANE-003 | Native/untrusted work requires a process-controller attachment | `execution.rs`, `resource.rs`, `resource_platform.rs` | `PROVEN`: portable controller fail-closed test; privileged live process enforcement `NOT VERIFIED` |
| ACC-001 | Capability-driven accelerator seam without mandatory vendor dependency | `execution.rs`, `resource.rs` | `PROVEN`: capability contract and CPU-only fail-closed tests; CUDA/ROCm/Metal adapters `NOT VERIFIED` |
| PY-001 | Production/forward Python lanes | `.python-version`, `.github/workflows/ci.yml` | `PROVEN` policy is 3.14.7 / 3.15.0rc1 with prereleases enabled |
| PKG-001 | Maturin owns the native wheel build | `pyproject.toml`, Cargo `python-extension` feature | `PROVEN` pinned 1.14.1 configuration and local clean-wheel import; exact Tier-1 matrix remains `NOT VERIFIED` |
| RUST-001 | Rust 2024 migration | all workspace manifests and Windows FFI declarations | `PROVEN` workspace library check passes |
| WASM-001 | Wasmtime 47 migration is security-reviewed | `Cargo.toml`, `WASMTIME_MIGRATION.md`, sandbox tests, `fuzz/` | `PROVEN` exact 47.0.3 configuration/static gates and Rust behavior tests; fuzz and hostile-kernel evidence `NOT VERIFIED` |
| CI-001 | CI runs the resolved environment | `.github/workflows/ci.yml`, `.github/workflows/deep.yml` | `PROVEN`: workflow definitions use pinned actions, locked uv, strict checks, deep/fuzz paths; current commit `6e09dba1a1cf227476ef893a35bfd1c050f3a1e0` passed all 9 CI jobs in run [32770728338](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/32770728338) |
| DOC-001 | Decisions and requirements are durable | `docs/adr/`, this matrix | `PROVEN` ADRs carry decision, security, performance, operations, rollback, and evidence fields |
| TEL-001 | Bounded non-authoritative runtime telemetry with correlation IDs | `core/rust/src/telemetry.rs`, `schemas/runtime-telemetry-v1.json` | `PROVEN`: schema, correlation validation, bounded-drop behavior; OTel exporter `NOT VERIFIED` |
| TEL-003 | Resource sampling is observation-only and can feed deterministic capacity feedback | `resource.rs`, `ffi.rs`, `aegis_cognition/runtime.py` | `PROVEN`: native sample boundary and pressure-feedback tests; external exporter `NOT VERIFIED` |
| STD-001 | Standards applicability and test-process mapping | `docs/architecture/STANDARDS_APPLICABILITY.md`, `TESTING_AND_EVIDENCE.md` | `PROVEN`: durable matrix and evidence-label process |
| PERF-001 | Hardware policy is measurement-backed | `scripts/resource_policy_benchmark.py` | `MEASURED` only for explicitly recorded local runs; H0/H1/H2 freeze `NOT VERIFIED` |
| PERF-002 | Scheduler microbenchmarks have stable IDs | `core/rust/benches/resource_runtime.rs`, `RESOURCE_POLICY_BENCHMARKS.md` | `MEASURED`: local Windows sample-size-10 run retained in verification ledger; cross-tier comparison `NOT VERIFIED` |
| SCM-001 | Dependency/secret/release gates are reproducible | `scripts/supply_chain_gate.py`, `scripts/secret_scan.py`, CI | `PROVEN` gate definitions; external signed release attestation and final release run `NOT VERIFIED` |
| REL-001 | Release wheel hashes, SBOM, and provenance path | `scripts/release_evidence.py`, `.github/workflows/release.yml` | `PROVEN`: deterministic generator and workflow path; tag-run attestation `NOT VERIFIED` |
| PY-002 | Agent facade has explicit application/config/infrastructure boundaries | `aegis_cognition/agent.py`, `application.py`, `config.py`, `infrastructure.py` | `PROVEN`: architecture fitness, strict Pyright/Ruff, 89-test local regression |
| PY-003 | Gateway provider/evidence/learning responsibilities are split | `core/python/aegis/*.py`, `core/python/aegis_adapter.py` | `PROVEN`: adapter responsibility gate and bridge regression suite |
| TEL-002 | Python provider/tool/evidence events share Rust-compatible correlation IDs | `aegis_cognition/observability.py`, `core/python/aegis_adapter.py`, `core/rust/src/ffi.rs` | `PROVEN` local bounded-chain tests; native wheel FFI forwarding requires rebuild |
| TOOL-001 | Rust toolchain and CI derive from one exact source | `rust-toolchain.toml`, `scripts/rust_toolchain.py`, workflows | `PROVEN`: architecture fitness; beta forward-compat lane is experimental |
| TOOL-002 | Fast CI excludes POCs while deep evidence retains them | `Cargo.toml`, `ci.yml`, `deep.yml` | `PROVEN`: configuration and current remote CI result; plugin workflow also passed in run [32770728189](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/32770728189) |

## Evidence discipline

Claims in README and release notes must link to this matrix or a retained
benchmark artifact. A local run on one workstation cannot be relabeled as H0,
H1, or H2 without recording the hardware profile, OS, toolchain, workload,
sample count, and raw output.
