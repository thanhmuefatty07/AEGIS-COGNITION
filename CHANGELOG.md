# Changelog

All notable changes to AEGIS-COGNITION will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`comprehensive_hardening_report.md`**: audit-grounded hardening record distinguishing
  premise-wrong prompt items, already-done prior-session items, and new-this-session work.

### Removed
- **Code hygiene (Phase 1A)**: 3 unused variables across 3 files, each removed with a
  dated inline comment so rationale is preserved:
  - `core/rust/src/eac/sandbox/runtime.rs:492` — `blocked_pattern` slot in `EacEvent::SecurityViolation`
    destructure pattern (only mention in file, confirmed ignore-only)
  - `core/rust/src/licensing.rs:480` — `validator` binding in `test_license_fingerprint_deterministic`
    (used only to verify `LicenseValidator::new(...).unwrap()` then dropped immediately)
  - `core/rust/src/tests.rs:4762` — `wasm` binding in admission+improvement regression context
    (a separate `improved_wasm` binding is what the test exercises)

### Changed
- **License expiry diagnostics**: `LicenseManager::check_feature` now preserves
  the actual `expired_at` timestamp instead of returning `0`; a Rust regression
  test covers the error payload.
- **License expiry boundary**: expiry grace-period checks now use saturating
  arithmetic, with a `u64::MAX` regression case preventing overflow.
- **Native Lab contract tests**: fixed a mutable/immutable borrow overlap in the
  goal-contract wire fixture so the formatted Rust source recompiles cleanly.
- **`core/rust/src/skill_registry.rs`**: introduced `SkillImprovementRecordInfo` struct with a
  `record_hash(&self) -> [u8; 32]` method, kept `pub fn skill_improvement_record_hash(...)` as a
  9-arg thin wrapper for backward compatibility. Clears `clippy::too_many_arguments` without
  forcing call-site churn. Two call sites in the same module continue to compile without edit.
- **Hardening evidence**: refreshed `artifacts/hardening/` with this-session (2026-06-16)
  timestamps for `cargo audit`, `cargo geiger`, `bandit`, `mypy`, `ruff`, and bench outputs.
  Differs from `audit_2026_06_15/` only in mtime and tool-drift counts (mypy 80 vs prior 83,
  ruff 2 vs prior 84 — both tool versions re-tightened upstream between runs).

### Verified Unchanged
- 381/381 Rust lib tests PASS (`cargo test --lib`, ~49s — faster than the 53s baseline after
  the unused-var cleanups removed redundant initialisation work).
- Hot-path benchmark `shadow_sealer_queue_depth/qd_4096` = **62.5 µs** median (matches the
  2026-06-15 MEMORY baseline of 60–68 µs at the post-fix queue depth).
- Gate-layer `skill_improve_usage_scaling/usage_10000` = **13.5 ns** median (no regression).
- 0 unused-import warnings / 0 dead-code warnings / 0 unused-variable warnings.
- `cargo clippy -W clippy::too_many_arguments` hits on `skill_improvement_record_hash` = **0**.
- Bandit: 0 Critical / 0 High (1 Medium, 660 Low — Medium is the documented pickle advisory).
- Historical closure claims are superseded: current `production_deployable` is
  derived from the deployment policy and local artifacts, and is **false** until
  the active blockers are independently closed.

### Deferred With Rationale (audit-completed, execution deferred)
- **27→29 bare `blake3::hash` calls (ISSUE-003 / R4)**: per-site migration plan lives at
  `artifacts/audit_2026_06_15/blake3_domain_prefix_plan.md`. Each production site needs its
  own migration that preserves historical hash compatibility for `LearningLedger`,
  `SessionSearchIndex`, and `cold_ledger` integrity chains. Three canonical helper sites
  (hot_engine:586, physical:774, evidence_index:3254) are intentionally kept bare because
  they export into the public hash API. Test sites (10) cannot migrate without updating
  fixture literals in lockstep. Migration is **high-risk if done wrong**, so it is sequenced
  per-site in subsequent PRs.
- **R6 — Refactor 88 `pub fn is_valid(&self) -> bool` impls**: 20-40h effort, would lose type
  safety at usage boundaries, scheduled for P3 in the master prompt itself.
- **Pre-commit hooks**: prior session deferred (Windows hook plumbing is brittle); CI gate
  in `.github/workflows/ci.yml` covers the same lint set at PR time.

### Note on prior-session items (Phase 1A from earlier cleanup, 2026-06-15)
- **5 unused imports** removed (recorded in the prior CHANGELOG entry): `CogniFoldStore` +
  `PhysicalWatchdog` in `memory/nudge.rs`; `LearningLedger` in `memory/session_search.rs`;
  `CandidateEvidenceRef` in `tests.rs`; `LicenseManager` in `benches/nerve_bench.rs`.

## [0.1.0] - 2026-06-15

Baseline established. See:
- `docs/archive/reports/project-overview.md` — system architecture and pillar split
- `docs/archive/reports/extreme-audit.md` — extreme testing, 0 critical / 0 high
- `docs/archive/reports/implementation-spec.md` — DX and CLI implementation spec
- `docs/archive/reports/dx-research.md`, `docs/archive/reports/dx-integration.md` — competitive DX analysis
