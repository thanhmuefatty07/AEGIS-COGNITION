# AEGIS Empirical Research Report

## A. Identity
- HEAD: `f9645caf6d17cee2023d52183990ffcf8317e456`
- WORKTREE_EPOCH: `549114d28117c7374ab5e1e1b1c0706876887eb55365684c7ec896fe2c779b2d`
- Branch: `main`; ahead/behind: `10	0`
- Source state: current working tree; epoch stable: `True`
- Environment: `environment.json`; local-only bounded execution.

## B. Experiments completed
- 22 experiment records: COMPLETE=13, PARTIAL=9; index: `research_index.json`.
- `EXP-PKG-001` — COMPLETE; MEASURED; `package_layout.json`; root=PASS; core=BROKEN; combined=PASS_WITH_COLLISION.
- `EXP-IMP-001` — COMPLETE; MEASURED; `import_costs.json`; Import self/cumulative timings are recorded without introducing lazy imports.
- `EXP-RPL-001` — COMPLETE; MEASURED; `replay_decomposition.json`; The components are separable under explicitly different durability labels.
- `EXP-FFI-001` — COMPLETE; MEASURED; `ffi_runtime_profile.json`; Representative scalar FFI calls completed or were explicitly recorded.
- `EXP-DATA-001` — PARTIAL; INFERRED; `data_movement.json`; Source shows repeated JSON/bytes conversion and artifact writes; exact bytes moved remain unmeasured.
- `EXP-API-001` — COMPLETE; SOURCE_BACKED; `rust_public_surface.json`; Cargo/source graph separates FFI, crate-internal, compatibility, test and accidental-suspect surfaces; no visibility changes.
- `EXP-CLEAN-001` — COMPLETE; INFERRED; `cleanup_reachability.json`; No strong delete candidate; nested mirror is archive/history and core/python requires migration.
- `EXP-DEP-001` — PARTIAL; INFERRED; `dependency_rent.json`; Manifest and lock roles are mixed; no dependency removal candidate is proven.
- `EXP-COH-001` — COMPLETE; INFERRED; `cohesion.json`; ffi.rs is strongest split candidate; lab.py/CLI/evidence gate are candidates; adapter should reorganize internally; no split performed.
- `EXP-ERR-001` — PARTIAL; INFERRED; `error_semantics.json`; Concrete broad-catch/string-conversion suspects exist; complete terminal mapping is partial.
- `EXP-TRUST-001` — COMPLETE; MEASURED; `trust.json`; Local no-provider constructors expose DEV while evidence normalization defaults PROD; Agent is credential-gated.
- `EXP-RETRY-001` — COMPLETE; INFERRED; `retry.json`; Potential bound is symbolic; SDK attempts and idempotency remain unknown.
- `EXP-EFFECT-001` — COMPLETE; INFERRED; `effect_paths.json`; Provider/network paths are bypassable; browser/process are Lab-fenced only; filesystem includes direct operator effects.
- `EXP-SCHEMA-001` — PARTIAL; INFERRED; `schema_lifecycle.json`; Resource contract is explicit; snapshots, receipts, lease/telemetry and provider dictionaries remain unversioned or external.
- `EXP-DOC-001` — PARTIAL; INFERRED; `documentation_authority.json`; Architecture/audit/master-plan documents overlap; current manifest retains CHECKOUT_HEAD placeholders.
- `EXP-BENCH-001` — COMPLETE; SOURCE_BACKED; `measurement_contract.json`; Provided contract requires identity, rival hypothesis, raw artifacts, limitations and evidence class; measurement units are explicit when populated.
- `EXP-QUALITY-001` — PARTIAL; INFERRED; `test_invariant_map.json`; Taxonomy finds unit/integration/recovery/gate surfaces, but direct coverage and current-SHA artifacts remain partial.
- `EXP-GRADER-001` — PARTIAL; INFERRED; `grader_validation.json`; Grader validation is partial; no untrusted candidate code was executed.
- `EXP-PROV-001` — PARTIAL; INFERRED; `provenance.json`; CHECKOUT_HEAD placeholders and missing retained artifacts break a complete current-SHA chain.
- `EXP-HW-002` — PARTIAL; INFERRED; `scaling_model_feasibility.json`; Regimes can be named, but status is MODEL_ESTIMATE_UNVALIDATED and other hosts are OUT_OF_VALIDATED_DOMAIN.
- `EXP-BENCH-002` — COMPLETE; MEASURED; `benchmark_noise.json`; Noise and raw samples are recorded; no robust tail claim.
- `EXP-HW-001` — COMPLETE; MEASURED; `hardware_calibration.json`; HardwareCapabilityVectorPrototype is local only; cross-machine and energy are unvalidated.

## C. Falsified hypotheses
- One canonical `aegis` owner is falsified by combined-install collision.
- Complete current-SHA provenance is falsified by `CHECKOUT_HEAD` and missing retained records.
- One-host data does not prove a cross-machine scaling model.

## D. Surviving hypotheses
- Rust owns selected typed mechanics; Python retains mutable projection/orchestration.
- Explicit Lab paths are stronger than compatibility/provider/operator paths.
- Replay durability classes must remain separate from encode/hash cost.

## E. Architecture-relevant facts
- Package/CLI, reducer, trust, retry and schema ownership remain unresolved design inputs.
- Static FFI/public-surface breadth exceeds observed scalar runtime calls.

## F. Cleanup-relevant facts
- No strong delete candidate; nested Rust mirror is archive/history; core/python is migration-required.

## G. Performance-relevant facts
- Bounded encode/hash/write/flush/fsync, FFI, noise and local hardware observations include raw values and units.
- Energy, cross-machine and production-tail performance are NOT_MEASURED.

## H. Benchmark/eval-relevant facts
- Measurement contract requires raw artifacts, units, hypotheses, environment and evidence class.
- Grader validation remains PARTIAL without positive/negative oracle runs.

## I. Security/effect facts
- No live provider/browser/hostile binary/network, stress, fuzz, soak or multi-machine operation.
- Compatibility effect sinks remain potential admission bypasses.

## J. External-only unknowns
- Linux/macOS/Windows containment; hosted cluster; multi-machine ordering; provider/browser receipts; hardware energy; independent replication; signed release provenance.

## K. Local unresolved unknowns
- Final SHA/provenance owner; package/CLI owner; canonical reducer/projection; trust/retry owners; plugin discovery; lifecycle schema migration.

## L. Evidence suitable for master-plan decisions
- Measured clean package behavior, CLI collision, bounded local cost decomposition, FFI probe, static reachability and local calibration.

## M. Evidence NOT suitable for master-plan decisions
- Cross-machine performance/security/energy/scalability; live provider/browser behavior; production readiness; strong deletion; exact SDK retry bounds.

EMPIRICAL_RESEARCH_STATUS = PARTIAL_LOCAL
SAFE_TO_CREATE_FINAL_MASTER_PLAN = NO
SAFE_TO_BEGIN_ARCHITECTURE_CONVERGENCE = NO

generated_at: 2026-08-31T08:45:23.079168+00:00
method: aegis-empirical-research-execution-v1; bounded local probes and direct source inspection; no production mutation
limitations: audit-only bounded pass; see JSON artifacts and raw files.
