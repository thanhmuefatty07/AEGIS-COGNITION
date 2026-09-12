# AEGIS — Architecture Convergence Evidence Pack

**Collection date:** 2026-08-28  
**Repository:** `C:\Users\ADMIN\AEGIS-COGNITION`  
**Method:** fresh filesystem + Git + Python AST + Rust source + `cargo metadata --no-deps`; repository knowledge-graph index intentionally not used as truth.  
**Safety:** no refactor, delete, feature, wheel build, provider call, benchmark campaign, fuzz, soak, or stress workload was run.

This document is an evidence pack, not a redesign. It reports what the current working tree mechanically supports and labels uncertainty. Generated JSON artifacts in this directory are audit outputs only.

## 1. Current state snapshot

| Field | Value |
|---|---|
| HEAD | `06e2a00e6d18a1fb1ca0edf125aae596ecc6068c` |
| branch | `codex/aegis-ci-evidence-gates` |
| origin/main ref | `origin/main` |
| ahead/behind | `9	0` |
| tracked modified files | `139` |
| staged files | `81` |
| untracked files | `0` |
| exact total status entries | `139` |

### File classification

- `.dockerignore` → **UNKNOWN**
- `.github/workflows/ci.yml` → **UNKNOWN**
- `.gitignore` → **UNKNOWN**
- `.local/agent-config/cursor/agents/aegis-checkpoint-warden.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-decision-oracle.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-memory-curator.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-nerve-architect.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-path-prioritizer.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-planning-guardian.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-rust-architect.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-rust-benchmark-harness.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-rust-ffi-auditor.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-rust-schema-keeper.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-rust-security-auditor.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-sac-sentinel.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-speculative-orchestrator.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-vibecoding-conductor.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/agents/aegis-zero-trust-plugin-architect.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-audit.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-build.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-checkpoint.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-consensus.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-crystallize.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-decide.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-init.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-path-check.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-perf-check.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-plan-check.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-plugin-create.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-regression-check.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-resume.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-speculate.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-verify.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/commands/aegis-vibecode.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/rules/aegis-cognition-stack.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/aegis-performance-checkpoint.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/aegis-zero-trust-plugin-creator.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/defect-remediation.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/planning-architecture.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/ponytail.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/project.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/style.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/tool-maximization.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/rules/vibecoding.mdc` → **UNKNOWN**
- `.local/agent-config/cursor/skills/aegis-architecture/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-checkpoint-supervisor/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-codegen-command/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-cognifold/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-crystallization/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-decision-oracle/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-failure-provenance/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-implementation-guardian/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-invariant-registry/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-long-horizon-orchestrator/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-nerve/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-next-best-action/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-optimal/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-optimization/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-path-prioritizer/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-performance-compiler/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-planning-alignment/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-proof-checker/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-regression-sentinel/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-rust-benchmark/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-rust-ffi/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-rust-schema/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-rust-security-audit/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-sac/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-speculative-decoding/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-vibecoding-conductor/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursor/skills/aegis-zero-trust-plugin-creator/SKILL.md` → **DOCUMENTATION**
- `.local/agent-config/cursorrules` → **UNKNOWN**
- `.local/agent-state/serena/.gitignore` → **UNKNOWN**
- `.local/agent-state/serena/project.yml` → **UNKNOWN**
- `core/python/tests.py` → **TEST**
- `docs/README.md` → **DOCUMENTATION**
- `docs/architecture/AEGIS_CURRENT_ARCHITECTURE_TRUTH_AUDIT.md` → **DOCUMENTATION**
- `docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md` → **DOCUMENTATION**
- `docs/architecture/ARCHITECTURE_FREEZE.md` → **DOCUMENTATION**
- `docs/architecture/README.md` → **DOCUMENTATION**
- `docs/architecture/design-closure/AEGIS_FINAL_DESIGN_CLOSURE.md` → **DOCUMENTATION**
- `docs/architecture/design-closure/artifact_retention.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/cohesion_candidates.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/config_precedence.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/data_movement.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/delete_candidates.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/design_closure.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/dynamic_reachability.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/entrypoint_truth.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/error_semantics.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/external_boundary.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/ffi_actual_surface.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/invariant_matrix.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/packaging_truth.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/performance_baseline.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/retry_truth.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/rust_mirror_truth.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/rust_public_surface.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/schema_graph.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/side_effect_bypasses.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/side_effect_chains.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/state_divergence.json` → **DOCUMENTATION**
- `docs/architecture/design-closure/trust_truth.json` → **DOCUMENTATION**
- `docs/architecture/document_inventory.json` → **DOCUMENTATION**
- `docs/architecture/evidence-pack/architecture_concurrency.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_dependency_rent.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_duplicate_candidates.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_error_model.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_ffi_inventory.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_performance.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_public_api.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_python_import_graph.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_reachability.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_retry_graph.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_rust_module_graph.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_side_effect_graph.json` → **GENERATED**
- `docs/architecture/evidence-pack/architecture_trust_policy.json` → **GENERATED**
- `docs/archive/README.md` → **DOCUMENTATION**
- `docs/archive/planning/agent-harness-continuation-plan.md` → **DOCUMENTATION**
- `docs/archive/planning/architecture-optimization.md` → **DOCUMENTATION**
- `docs/archive/reports/browser-native-architecture.md` → **DOCUMENTATION**
- `docs/archive/reports/cluster-soak.md` → **DOCUMENTATION**
- `docs/integrations/aegis-plugins.md` → **DOCUMENTATION**
- `quality/registry/current_affected_closure.json` → **UNKNOWN**
- `quality/registry/current_claim_graph.json` → **UNKNOWN**
- `quality/registry/current_inventory.json` → **UNKNOWN**
- `quality/registry/current_s2_mapping.json` → **UNKNOWN**
- `quality/registry/current_shadow_plan.json` → **UNKNOWN**
- `quality/registry/current_validation_corpus.json` → **UNKNOWN**
- `scripts/_audit_regen_requirements.py` → **SCRIPT**
- `scripts/_debug_probe.py` → **SCRIPT**
- `scripts/constitution_audit.py` → **SCRIPT**
- `scripts/document_consistency_gate.py` → **SCRIPT**
- `scripts/research/comparators/external/fts5_baseline_gate.py` → **SCRIPT**
- `scripts/research/comparators/external/persistence_write_baseline_gate.py` → **SCRIPT**
- `scripts/research/comparators/external/rpc_context_baseline_gate.py` → **SCRIPT**
- `scripts/research/comparators/external/session_recovery_baseline_gate.py` → **SCRIPT**
- `scripts/run_checks.py` → **SCRIPT**
- `tests/test_constitution_audit_gates.py` → **TEST**

The generated evidence-pack files are included in the final status snapshot as `GENERATED`; the pre-generation snapshot is retained in `architecture_reachability.json` under `collection_state_before_generation` when available.

## 2. Fresh Python import graph

AST scan found **176 modules** and **259 resolved internal import edges**. Full adjacency is in `architecture_python_import_graph.json`.

- Cycles mechanically detected: `1`. Cycles are listed in the JSON; dynamic/decorator edges can be missing.
- Dynamic imports: `16` module records.
- `sys.path` manipulation records: `14`.
- Import-time side-effect candidates: `0` lexical candidates.
- Lazy import detection is conservative; imports nested in functions need parent-aware AST analysis before being classified as intentional.

Highest fan-in:
- `aegis_cognition` fan_in=16 fan_out=0 (aegis_cognition/__init__.py)
- `aegis_cognition.config` fan_in=8 fan_out=2 (aegis_cognition/config.py)
- `aegis_cognition.runtime` fan_in=7 fan_out=1 (aegis_cognition/runtime.py)
- `aegis_cognition.application` fan_in=6 fan_out=9 (aegis_cognition/application.py)
- `aegis_cognition.observability` fan_in=6 fan_out=2 (aegis_cognition/observability.py)
- `core.python` fan_in=6 fan_out=1 (core/python/__init__.py)
- `aegis_cognition.lab` fan_in=5 fan_out=7 (aegis_cognition/lab.py)
- `core.python.aegis.connections` fan_in=5 fan_out=2 (core/python/aegis/connections.py)
- `core.python.aegis.contracts` fan_in=5 fan_out=0 (core/python/aegis/contracts.py)
- `core.python.aegis.native` fan_in=5 fan_out=1 (core/python/aegis/native.py)
- `core.python.aegis_adapter` fan_in=5 fan_out=13 (core/python/aegis_adapter.py)
- `core.python.browser_live_collector` fan_in=5 fan_out=0 (core/python/browser_live_collector.py)

Highest fan-out:
- `core.python.tests` fan_in=0 fan_out=33 (core/python/tests.py)
- `scripts.run_checks` fan_in=1 fan_out=26 (scripts/run_checks.py)
- `core.python.aegis_adapter` fan_in=5 fan_out=13 (core/python/aegis_adapter.py)
- `tests.test_lab_runtime` fan_in=0 fan_out=12 (tests/test_lab_runtime.py)
- `scripts.e2e_release_gate` fan_in=3 fan_out=10 (scripts/e2e_release_gate.py)
- `aegis_cognition.application` fan_in=6 fan_out=9 (aegis_cognition/application.py)
- `aegis_cognition._public_exports` fan_in=0 fan_out=8 (aegis_cognition/_public_exports.py)
- `aegis_cognition.desktop_service` fan_in=2 fan_out=8 (aegis_cognition/desktop_service.py)
- `aegis_cognition.lab` fan_in=5 fan_out=7 (aegis_cognition/lab.py)
- `core.python.build.lib.aegis_adapter` fan_in=0 fan_out=7 (core/python/build/lib/aegis_adapter.py)
- `aegis_cognition.agent` fan_in=3 fan_out=6 (aegis_cognition/agent.py)
- `tests.test_connections` fan_in=0 fan_out=5 (tests/test_connections.py)

## 3. Rust module + crate graph

`cargo metadata --no-deps` status: **PROVEN**. Workspace packages, dependencies, features, targets, modules, visibility, public declarations, and target limitations are in `architecture_rust_module_graph.json`.

High-coupling Rust modules (source scan):
- `core/rust/benches/architecture_performance.rs` `aegis-nerve::benches::architecture_performance` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/benches/nerve_bench.rs` `aegis-nerve::benches::nerve_bench` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/benches/resource_runtime.rs` `aegis-nerve::benches::resource_runtime` fan_in=0 fan_out=0 pub=0 responsibility=resource admission/platform
- `core/rust/benches/shadow_sealer_throughput.rs` `aegis-nerve::benches::shadow_sealer_throughput` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/benches/skill_improvement.rs` `aegis-nerve::benches::skill_improvement` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/src/bridge_mmap.rs` `aegis-nerve::src::bridge_mmap` fan_in=0 fan_out=0 pub=21 responsibility=module-local responsibility; inspect source
- `core/rust/src/browser_witness.rs` `aegis-nerve::src::browser_witness` fan_in=0 fan_out=0 pub=94 responsibility=module-local responsibility; inspect source
- `core/rust/src/circuit_breaker.rs` `aegis-nerve::src::circuit_breaker` fan_in=0 fan_out=0 pub=14 responsibility=module-local responsibility; inspect source
- `core/rust/src/cli/mod.rs` `aegis-nerve::src::cli` fan_in=0 fan_out=0 pub=33 responsibility=CLI
- `core/rust/src/connections.rs` `aegis-nerve::src::connections` fan_in=0 fan_out=0 pub=16 responsibility=module-local responsibility; inspect source
- `core/rust/src/context.rs` `aegis-nerve::src::context` fan_in=0 fan_out=0 pub=41 responsibility=module-local responsibility; inspect source
- `core/rust/src/conversations.rs` `aegis-nerve::src::conversations` fan_in=0 fan_out=0 pub=23 responsibility=module-local responsibility; inspect source
- `core/rust/src/descriptor.rs` `aegis-nerve::src::descriptor` fan_in=0 fan_out=0 pub=4 responsibility=module-local responsibility; inspect source
- `core/rust/src/distributed.rs` `aegis-nerve::src::distributed` fan_in=0 fan_out=0 pub=29 responsibility=module-local responsibility; inspect source
- `core/rust/src/eac/cache/mod.rs` `aegis-nerve::src::eac::cache` fan_in=0 fan_out=0 pub=2 responsibility=module-local responsibility; inspect source

The graph does not claim macro-expanded, trait-dispatch, generated, or rust-analyzer-complete edges. `pub mod` and `pub use` are recorded conservatively; accidental-public status requires API review.

## 4. Production reachability graph

Root labels are separated as `PUBLIC_API`, `LAB`, `CLI`, `FFI`, `PLUGIN`, `TEST_ONLY`, `SCRIPT_ONLY`, and `POC_ONLY`. Reachability counts from the fresh static graph: `{'CLI': 35, 'FFI': 20, 'LAB': 32, 'PUBLIC_API': 34, 'TEST_ONLY': 119, 'SCRIPT_ONLY': 68, 'POC_ONLY': 1}`. Full per-file rows are in `architecture_reachability.json`.

The allowed final class is deliberately conservative: static absence yields `UNKNOWN`, not delete authorization. Dynamic imports, plugin discovery, packaging includes, console scripts, subprocesses, and documentation references are separately recorded.

## 5. Exact package / wheel graph

The current configuration declares two package/build surfaces and two `aegis` console-script owners. The source→wheel→import mappings and existing wheel search are in `architecture_wheel_graph.json`.

**Wheel build/listing was not run** to avoid a potentially heavy native build and source mutation. Source-checkout imports therefore remain insufficient to claim wheel correctness.

## 6. FFI contract inventory

Fresh PyO3 attribute scan found **151 exposed function/class candidates**. Full records include Python name, Rust symbol, callers, inputs/outputs, serialization/copying suspicion, side-effect class, authority relevance, frequency category, and flags.

- `aegis-plugins/aegis-search-sdk/src/lib.rs:475` `SearchQuery` → `PySearchQuery`; flags=none
- `aegis-plugins/aegis-search-sdk/src/lib.rs:494` `lexical_sync` → `lexical_sync`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:10` `aegis_llm_request` → `aegis_llm_request`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:25` `aegis_llm_route` → `aegis_llm_route`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:43` `aegis_llm_bridge_key` → `aegis_llm_bridge_key`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:48` `aegis_llm_normalize` → `aegis_llm_normalize`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:57` `aegis_llm_reject` → `aegis_llm_reject`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:67` `aegis_harness_generate_skeleton` → `aegis_harness_generate_skeleton`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:73` `aegis_harness_analyze_errors` → `aegis_harness_analyze_errors`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:79` `aegis_physical_metrics_prometheus` → `aegis_physical_metrics_prometheus`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:84` `aegis_runtime_telemetry_emit` → `aegis_runtime_telemetry_emit`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:92` `aegis_runtime_telemetry_snapshot` → `aegis_runtime_telemetry_snapshot`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:100` `aegis_trust_level` → `aegis_trust_level`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `core/rust/src/ffi/compat.rs:105` `aegis_hot_hash` → `aegis_hot_hash`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:47` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:93` `aegis_list_connections` → `aegis_list_connections`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:115` `aegis_revoke_connection` → `aegis_revoke_connection`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:146` `UNKNOWN` → `UNKNOWN`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:200` `aegis_list_model_descriptors` → `aegis_list_model_descriptors`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:228` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:270` `aegis_revoke_connection_egress` → `aegis_revoke_connection_egress`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/connections.rs:299` `aegis_check_connection_egress` → `aegis_check_connection_egress`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/context.rs:27` `aegis_select_context_items` → `aegis_select_context_items`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:98` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:136` `aegis_list_conversations` → `aegis_list_conversations`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:160` `aegis_read_conversation` → `aegis_read_conversation`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:184` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:230` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:267` `aegis_set_conversation_status` → `aegis_set_conversation_status`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:302` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:342` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:380` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:424` `UNKNOWN` → `UNKNOWN`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:466` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:504` `UNKNOWN` → `UNKNOWN`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:546` `UNKNOWN` → `UNKNOWN`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/conversations.rs:588` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/eac.rs:16` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/eac.rs:32` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/eac.rs:50` `aegis_eac_cache_invalidate` → `aegis_eac_cache_invalidate`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/eac.rs:67` `aegis_eac_batch` → `aegis_eac_batch`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/eac.rs:163` `UNKNOWN` → `UNKNOWN`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/eac.rs:199` `aegis_eac_load_state` → `aegis_eac_load_state`; flags=COPY_HEAVY,STRINGLY_TYPED
- `core/rust/src/ffi/hot.rs:9` `aegis_hot_commit` → `aegis_hot_commit`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/hot.rs:56` `aegis_hot_commit_batch` → `aegis_hot_commit_batch`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:14` `LabController` → `PyLabController`; flags=AUTHORITY_SENSITIVE
- `core/rust/src/ffi/lab.rs:237` `aegis_lab_verify_event_chain` → `aegis_lab_verify_event_chain`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:254` `aegis_lab_validate_transition` → `aegis_lab_validate_transition`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:282` `aegis_lab_validate_snapshot` → `aegis_lab_validate_snapshot`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:291` `aegis_lab_archive_events` → `aegis_lab_archive_events`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:325` `aegis_lab_verify_archive` → `aegis_lab_verify_archive`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:348` `aegis_lab_verify_archive_against_manifest` → `aegis_lab_verify_archive_against_manifest`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:381` `aegis_lab_verify_archive_against_events` → `aegis_lab_verify_archive_against_events`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/lab.rs:405` `aegis_lab_verify_archive_against_legacy_manifest` → `aegis_lab_verify_archive_against_legacy_manifest`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:62` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:146` `aegis_inspect_memory` → `aegis_inspect_memory`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:182` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:221` `aegis_backup_memory_store` → `aegis_backup_memory_store`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:253` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:304` `aegis_revoke_memory_access` → `aegis_revoke_memory_access`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:348` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:371` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:425` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:490` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:513` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:621` `aegis_get_learning_stats` → `aegis_get_learning_stats`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:739` `aegis_trigger_memory_nudge` → `aegis_trigger_memory_nudge`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:784` `aegis_index_session` → `aegis_index_session`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:797` `aegis_index_session_scoped` → `aegis_index_session_scoped`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:837` `aegis_read_session_content` → `aegis_read_session_content`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:846` `aegis_read_session_content_scoped` → `aegis_read_session_content_scoped`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:873` `aegis_read_session_record_scoped` → `aegis_read_session_record_scoped`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:930` `aegis_search_past_sessions` → `aegis_search_past_sessions`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/learning.rs:943` `aegis_search_past_sessions_scoped` → `aegis_search_past_sessions_scoped`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/mmap.rs:9` `aegis_mmap_bridge_header_bytes` → `aegis_mmap_bridge_header_bytes`; flags=LARGE_PAYLOAD
- `core/rust/src/ffi/mmap.rs:14` `aegis_mmap_bridge_payload_alignment` → `aegis_mmap_bridge_payload_alignment`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/mmap.rs:19` `aegis_write_mmap_bridge_pattern` → `aegis_write_mmap_bridge_pattern`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/mmap.rs:44` `aegis_validate_mmap_bridge_frame` → `aegis_validate_mmap_bridge_frame`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/mmap.rs:49` `aegis_execute_mmap_wasm_bridge_frame` → `aegis_execute_mmap_wasm_bridge_frame`; flags=STRINGLY_TYPED
- `core/rust/src/ffi/runtime.rs:192` `aegis_hardware_profile` → `aegis_hardware_profile`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED

Macro-generated or re-exported exposure may be missing; caller frequency is a category estimate based on static callsites, not runtime telemetry.

## 7. Canonical state ownership matrix

Full 24-entity matrix is in `architecture_authority_matrix.json`. The current tree mechanically indicates a split for Lab entities: Python constructs/mutates a mutable projection and Rust validates/adjudicates many typed admissions, budgets, replay, and finalization operations. `TrustLevel` and `RetryPolicy` have compatibility duplicates/split ownership. No solution is recommended in this pack.

## 8. Side-effect reachability

Fresh lexical/AST side-effect candidates:

- `aegis_cognition/aese.py:99` `aegis_cognition.aese:_is_finite` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:108` `aegis_cognition.aese:_is_digest` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/aese.py:118` `aegis_cognition.aese:_hash` → `EXTERNAL_WRITE`; roots=aegis_cognition.agent:run,aegis_cognition.application:arun,aegis_cognition.application:run,aegis_cognition.desktop_service:main,aegis_cognition.lab:run,scripts.run_checks:_blake3_hex_with_rust,scripts.run_checks:_cached_or_generate_cli_report,scripts.run_checks:ensure_fresh_benchmarks
- `aegis_cognition/aese.py:2121` `aegis_cognition.aese:as_dict` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:2126` `aegis_cognition.aese:from_dict` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/aese.py:2108` `aegis_cognition.aese:validate` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/aese.py:1848` `aegis_cognition.aese:_payload` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1091` `aegis_cognition.aese:vector_hash` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1095` `aegis_cognition.aese:from_runtime_profile` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1258` `aegis_cognition.aese:numeric_features` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1136` `aegis_cognition.aese:from_mapping` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.application:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/aese.py:1255` `aegis_cognition.aese:signature_hash` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1262` `aegis_cognition.aese:classify_regime` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1823` `aegis_cognition.aese:priority_key` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1973` `aegis_cognition.aese:_coerce_anchor_sequence` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:1981` `aegis_cognition.aese:select_anchor_plan` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/aese.py:2164` `aegis_cognition.aese:predict_cross_hardware` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `BROWSER`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `ENVIRONMENT_MUTATION`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:77` `aegis_cognition.agent:_validate` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:77` `aegis_cognition.agent:_validate` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:82` `aegis_cognition.agent:_prepare_rag_and_prompt` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:82` `aegis_cognition.agent:_prepare_rag_and_prompt` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:85` `aegis_cognition.agent:_index_completed_run` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/agent.py:85` `aegis_cognition.agent:_index_completed_run` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/agent.py:98` `aegis_cognition.agent:run` → `PROVIDER_CALL`; roots=aegis_cognition.agent:run
- `aegis_cognition/agent.py:91` `aegis_cognition.agent:arun` → `PROVIDER_CALL`; roots=aegis_cognition.agent:arun
- `aegis_cognition/agent.py:94` `aegis_cognition.agent:__repr__` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:32` `aegis_cognition.application:__init__` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:32` `aegis_cognition.application:__init__` → `SECRET_ACCESS`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:32` `aegis_cognition.application:__init__` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:44` `aegis_cognition.application:prepare` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:44` `aegis_cognition.application:prepare` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:44` `aegis_cognition.application:prepare` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:52` `aegis_cognition.application:_retrieve_context` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:52` `aegis_cognition.application:_retrieve_context` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:52` `aegis_cognition.application:_retrieve_context` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:91` `aegis_cognition.application:_build_system_context` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:91` `aegis_cognition.application:_build_system_context` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:91` `aegis_cognition.application:_build_system_context` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:113` `aegis_cognition.application:_gateway` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run
- `aegis_cognition/application.py:113` `aegis_cognition.application:_gateway` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run
- `aegis_cognition/application.py:113` `aegis_cognition.application:_gateway` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run
- `aegis_cognition/application.py:137` `aegis_cognition.application:_begin_conversation` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:137` `aegis_cognition.application:_begin_conversation` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:233` `aegis_cognition.application:_conversation_output` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:233` `aegis_cognition.application:_conversation_output` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:238` `aegis_cognition.application:_finish_conversation` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:238` `aegis_cognition.application:_finish_conversation` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:269` `aegis_cognition.application:arun` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:269` `aegis_cognition.application:arun` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:393` `aegis_cognition.application:_lab_enabled` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:407` `aegis_cognition.application:run` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:run
- `aegis_cognition/application.py:417` `aegis_cognition.application:_index_completed_run` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/benchmark.py:28` `aegis_cognition.benchmark:_is_finite_real` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/benchmark.py:39` `aegis_cognition.benchmark:_digest` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run,core.python.operator_api_server:main,scripts.run_checks:main
- `aegis_cognition/benchmark.py:186` `aegis_cognition.benchmark:validate` → `FILESYSTEM_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/benchmark.py:186` `aegis_cognition.benchmark:validate` → `PROCESS_SPAWN`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/benchmark.py:186` `aegis_cognition.benchmark:validate` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run,core.python.aegis_cli:get_masked_input,scripts.run_checks:_benchmark_inputs,scripts.run_checks:_cli_report_inputs,scripts.run_checks:_file_blake3_hex,scripts.run_checks:_file_sha256,scripts.run_checks:main
- `aegis_cognition/benchmark.py:105` `aegis_cognition.benchmark:protocol_hash` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:105` `aegis_cognition.benchmark:protocol_hash` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:105` `aegis_cognition.benchmark:protocol_hash` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:126` `aegis_cognition.benchmark:capture` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:126` `aegis_cognition.benchmark:capture` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:126` `aegis_cognition.benchmark:capture` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:169` `aegis_cognition.benchmark:environment_hash` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:169` `aegis_cognition.benchmark:environment_hash` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:169` `aegis_cognition.benchmark:environment_hash` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:210` `aegis_cognition.benchmark:key` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:210` `aegis_cognition.benchmark:key` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:210` `aegis_cognition.benchmark:key` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:237` `aegis_cognition.benchmark:_run_isolated_validator` → `FILESYSTEM_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:237` `aegis_cognition.benchmark:_run_isolated_validator` → `PROCESS_SPAWN`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:237` `aegis_cognition.benchmark:_run_isolated_validator` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:340` `aegis_cognition.benchmark:_normalize_trial_records` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:424` `aegis_cognition.benchmark:evaluate_benchmark` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/cli.py:32` `aegis_cognition.cli:main` → `FILESYSTEM_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:32` `aegis_cognition.cli:main` → `BROWSER`; roots=aegis_cognition.cli:main

Rust side-effect sites and all effect classes are in `architecture_side_effect_graph.json`. The requested chain `ENTRYPOINT → CALLERS → ADMISSION → CAPABILITY → BUDGET → LEASE → EFFECT → SETTLEMENT → EVIDENCE` cannot be mechanically proven complete from static source alone. Records therefore flag missing stages as **UNKNOWN/REQUIRES CONTRACT REVIEW**, not as a proven absence. Hidden adapter effects, descendants, macro calls, and external service effects remain outside static completeness.

## 9. Retry / timeout / cancellation graph

Fresh pattern inventory produced `3002` records. Representative locations:

- `aegis_cognition/_public_exports.py:50` `"ExternalSideEffectLease",`
- `aegis_cognition/_public_exports.py:71` `"ReplayWriterLease",`
- `aegis_cognition/_public_exports.py:99` `"finish_runtime_lease",`
- `aegis_cognition/_public_exports.py:107` `"release_cooperative_placement",`
- `aegis_cognition/_public_exports.py:169` `ExternalSideEffectLease,`
- `aegis_cognition/_public_exports.py:170` `ReplayWriterLease,`
- `aegis_cognition/_public_exports.py:209` `finish_runtime_lease,`
- `aegis_cognition/_public_exports.py:218` `release_cooperative_placement,`
- `aegis_cognition/application.py:355` `timeout_seconds=runtime_options.get("runtime_timeout_seconds", 60.0),`
- `aegis_cognition/application.py:377` `except asyncio.CancelledError:`
- `aegis_cognition/benchmark.py:19` `_TRIAL_STATUSES = frozenset({"SUCCESS", "FAIL", "TIMEOUT", "REFUSAL", "CANCELLED", "ERROR"})`
- `aegis_cognition/benchmark.py:115` `os_release: str`
- `aegis_cognition/benchmark.py:138` `os_release=platform.release() or "unknown",`
- `aegis_cognition/benchmark.py:152` `self.os_release,`
- `aegis_cognition/benchmark.py:243` `timeout_seconds: float,`
- `aegis_cognition/benchmark.py:262` `if not _is_finite_real(timeout_seconds) or timeout_seconds <= 0:`
- `aegis_cognition/benchmark.py:263` `raise ValueError("hidden validator timeout must be finite and positive")`
- `aegis_cognition/benchmark.py:295` `stdout, stderr = process.communicate(payload, timeout=timeout_seconds)`
- `aegis_cognition/benchmark.py:296` `except subprocess.TimeoutExpired as exc:`
- `aegis_cognition/benchmark.py:300` `with contextlib.suppress(subprocess.TimeoutExpired):`
- `aegis_cognition/benchmark.py:301` `process.communicate(timeout=1.0)`
- `aegis_cognition/benchmark.py:302` `raise TimeoutError("hidden validator timed out") from exc`
- `aegis_cognition/benchmark.py:436` `validator_timeout_seconds: float = 5.0,`
- `aegis_cognition/benchmark.py:537` `timeout_seconds=validator_timeout_seconds,`
- `aegis_cognition/benchmark.py:542` `except (RuntimeError, TimeoutError, TypeError, ValueError) as exc:`
- `aegis_cognition/desktop_service.py:228` `"""Release process-local source watching resources."""`
- `aegis_cognition/errors.py:43` `"""Provider failure with user-facing retry guidance."""`
- `aegis_cognition/errors.py:181` `If this persists, please file an issue:`
- `aegis_cognition/errors.py:234` `If it didn't, wait a few seconds and retry.`
- `aegis_cognition/errors.py:253` `1. Wait 30 seconds and retry`
- `aegis_cognition/errors.py:272` `Please file an issue with this error message:`
- `aegis_cognition/errors.py:288` `Please file an issue:`
- `aegis_cognition/goal_contract.py:490` `CANCELLED = "cancelled"`
- `aegis_cognition/goal_contract.py:505` `GoalLifecycle.CANCELLED,`
- `aegis_cognition/goal_contract.py:513` `GoalLifecycle.DRAFT: frozenset({GoalLifecycle.ADMITTED, GoalLifecycle.CANCELLED}),`
- `aegis_cognition/goal_contract.py:514` `GoalLifecycle.ADMITTED: frozenset({GoalLifecycle.ACTIVE, GoalLifecycle.CANCELLED, GoalLifecycle.FAILED}),`
- `aegis_cognition/goal_contract.py:521` `GoalLifecycle.CANCELLED,`
- `aegis_cognition/goal_contract.py:526` `GoalLifecycle.PAUSED: frozenset({GoalLifecycle.ACTIVE, GoalLifecycle.BLOCKED, GoalLifecycle.CANCELLED}),`
- `aegis_cognition/goal_contract.py:527` `GoalLifecycle.BLOCKED: frozenset({GoalLifecycle.ACTIVE, GoalLifecycle.CANCELLED, GoalLifecycle.FAILED}),`
- `aegis_cognition/goal_contract.py:531` `GoalLifecycle.CANCELLED: frozenset(),`
- `aegis_cognition/lab.py:250` `def _bounded_retry_attempts(value: Any, *, max_steps: int, label: str) -> int:`
- `aegis_cognition/lab.py:251` `"""Validate and cap a retry count without lossy type coercion.`
- `aegis_cognition/lab.py:253` `Retry policy is part of the execution contract.  Accepting booleans,`
- `aegis_cognition/lab.py:261` `raise ValueError("retry policy max_steps must be a positive integer")`
- `aegis_cognition/lab.py:263` `raise ValueError(f"{label} retry policy must be a positive integer")`
- `aegis_cognition/lab.py:292` `"cancellation_admitted",`
- `aegis_cognition/lab.py:293` `"cancellation_recorded",`
- `aegis_cognition/lab.py:316` `The envelope covers the bounded controller/action/retry nesting known to`
- `aegis_cognition/lab.py:656` `"cancellation_admitted": "CancellationAdmitted",`
- `aegis_cognition/lab.py:657` `"cancellation_recorded": "CancellationRecorded",`
- `aegis_cognition/lab.py:673` `"timeout_ms",`
- `aegis_cognition/lab.py:939` `timeout_seconds: float = 10.0,`
- `aegis_cognition/lab.py:944` `type(timeout_seconds) not in (int, float)`
- `aegis_cognition/lab.py:945` `or isinstance(timeout_seconds, bool)`
- `aegis_cognition/lab.py:946` `or timeout_seconds <= 0`
- `aegis_cognition/lab.py:947` `or not math.isfinite(timeout_seconds)`
- `aegis_cognition/lab.py:949` `raise ValueError("search executor timeout must be positive and finite")`
- `aegis_cognition/lab.py:955` `self.timeout_seconds = timeout_seconds`
- `aegis_cognition/lab.py:1066` `return await asyncio.wait_for(`
- `aegis_cognition/lab.py:1074` `timeout=self.timeout_seconds,`
- `aegis_cognition/lab.py:1088` `with urlopen(request, timeout=self.timeout_seconds) as response:`
- `aegis_cognition/lab.py:1951` `"""Invoke an edge adapter without accepting a swallowed cancellation.`
- `aegis_cognition/lab.py:1953` `Python tasks retain a positive ``cancelling()`` count even when an`
- `aegis_cognition/lab.py:1954` `adapter catches ``CancelledError`` and returns a value.  Checking before`
- `aegis_cognition/lab.py:1955` `and after the adapter turns that ambiguous return into a cancellation so`
- `aegis_cognition/lab.py:1960` `if task is not None and task.cancelling():`
- `aegis_cognition/lab.py:1961` `raise asyncio.CancelledError`
- `aegis_cognition/lab.py:1964` `if task is not None and task.cancelling():`
- `aegis_cognition/lab.py:1965` `raise asyncio.CancelledError`
- `aegis_cognition/lab.py:2546` `async def wait_for_timeout(self, milliseconds: int) -> None:`
- `aegis_cognition/lab.py:2547` `method = getattr(self._session, "wait_for_timeout", None)`
- `aegis_cognition/lab.py:2554` `_SKILL_STATUSES = frozenset({"SUCCESS", "REJECTED", "CANCELLED"})`
- `aegis_cognition/lab.py:2563` `_BROWSER_SETTLEMENT_STATUSES = frozenset({"SUCCESS", "REJECTED", "CANCELLED"})`
- `aegis_cognition/lab.py:2779` `"""Bounded Python registry; Rust remains the release authority."""`
- `aegis_cognition/lab.py:2935` `self._lease_id = 0`
- `aegis_cognition/lab.py:2939` `def lease_id(self) -> int:`
- `aegis_cognition/lab.py:2940` `return self._lease_id`
- `aegis_cognition/lab.py:2947` `"""Return a read-only view bound to the current actor lease."""`
- `aegis_cognition/lab.py:2972` `"""Open one browser session and bind it to this cell's lease."""`
- `aegis_cognition/lab.py:2989` `self._lease_id += 1`
- `aegis_cognition/lab.py:3003` `self._lease_id += 1`
- `aegis_cognition/lab.py:3006` `async def release(self) -> None:`
- `aegis_cognition/lab.py:3007` `"""Close the owned session; repeated release is idempotent."""`
- `aegis_cognition/lab.py:3021` `await self.release()`
- `aegis_cognition/lab.py:3033` `"""Mark the cell closed; async session cleanup still uses ``release``."""`
- `aegis_cognition/lab.py:3161` `method = getattr(browser_session, "wait_for_timeout", None)`
- `aegis_cognition/lab.py:3199` `wait_for_timeout = getattr(browser_session, "wait_for_timeout", None)`
- `aegis_cognition/lab.py:3200` `if wait_for_timeout is None:`
- `aegis_cognition/lab.py:3202` `return await _call(wait_for_timeout, milliseconds)`
- `aegis_cognition/lab.py:3372` `if run.state in {"completed", "blocked", "aborted"}:`
- `aegis_cognition/lab.py:3395` `if run.state in {"completed", "blocked", "aborted"}:`
- `aegis_cognition/lab.py:3892` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3969` `timeout_seconds: float | None = None,`
- `aegis_cognition/lab.py:3973` ```attempt`` is the replay-visible retry fence.  A later attempt must`
- `aegis_cognition/lab.py:3978` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3985` `or (timeout_seconds is not None and type(timeout_seconds) not in (int, float))`
- `aegis_cognition/lab.py:3986` `or isinstance(timeout_seconds, bool)`
- `aegis_cognition/lab.py:3990` `raw_timeout_seconds: object = timeout_seconds`
- `aegis_cognition/lab.py:4000` `raw_timeout_seconds is not None`
- `aegis_cognition/lab.py:4002` `type(raw_timeout_seconds) not in (int, float)`
- `aegis_cognition/lab.py:4003` `or isinstance(raw_timeout_seconds, bool)`
- `aegis_cognition/lab.py:4004` `or not math.isfinite(float(raw_timeout_seconds))`
- `aegis_cognition/lab.py:4005` `or float(raw_timeout_seconds) <= 0`
- `aegis_cognition/lab.py:4040` `if raw_timeout_seconds is not None:`
- `aegis_cognition/lab.py:4041` `payload["timeout_seconds"] = float(cast(float, raw_timeout_seconds))`
- `aegis_cognition/lab.py:4065` `timeout_seconds: float | None = None,`
- `aegis_cognition/lab.py:4069` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:4081` `or (timeout_seconds is not None and type(timeout_seconds) not in (int, float))`
- `aegis_cognition/lab.py:4082` `or isinstance(timeout_seconds, bool)`
- `aegis_cognition/lab.py:4095` `if normalized_status not in {"SUCCESS", "REJECTED", "TIMED_OUT", "CANCELLED"}:`
- `aegis_cognition/lab.py:4102` `raw_timeout_seconds: object = timeout_seconds`
- `aegis_cognition/lab.py:4111` `timeout_seconds=timeout_seconds,`
- `aegis_cognition/lab.py:4135` `if "timeout_seconds" in admission:`
- `aegis_cognition/lab.py:4137` `raw_timeout_seconds is None`
- `aegis_cognition/lab.py:4138` `or type(raw_timeout_seconds) not in (int, float)`
- `aegis_cognition/lab.py:4139` `or isinstance(raw_timeout_seconds, bool)`
- `aegis_cognition/lab.py:4140` `or not math.isfinite(float(raw_timeout_seconds))`
- `aegis_cognition/lab.py:4141` `or float(raw_timeout_seconds) <= 0`
- `aegis_cognition/lab.py:4142` `or float(raw_timeout_seconds) != admission["timeout_seconds"]`
- `aegis_cognition/lab.py:4144` `raise ValueError("experiment execution settlement timeout does not match admission")`

The JSON separates retry count/backoff/idempotency/budget/attempt/cancellation fields as unknown unless directly visible. Duplicated candidate locations include provider, adapter, Lab, replay, and execution paths; no exactly-once or system-wide retry-budget claim is made.

## 10. Trust / policy graph

Fresh policy scan found `4000` records and `1000` default/fallback candidates.

- `.local/agent-config/cursor/agents/aegis-speculative-orchestrator.md:12` `2. State acceptance and fallback invariants.`
- `.local/agent-config/cursor/commands/aegis-verify.md:8` `3. Flag any unsupported reasoning or hidden fallback.`
- `.local/agent-config/cursor/skills/aegis-implementation-guardian/SKILL.md:18` `5. Do not allow silent fallback behavior when correctness matters.`
- `.local/agent-config/cursor/skills/aegis-rust-security-audit/SKILL.md:40` `- Safe fallback defined`
- `.local/agent-config/cursor/skills/aegis-speculative-decoding/SKILL.md:23` `- Ensure fallback path exists when confidence is low.`
- `.local/agent-config/cursor/skills/aegis-speculative-decoding/SKILL.md:37` `- Fallback decode path present`
- `.local/agent-state/agents/teamwork_preview_explorer_m3_1/handoff.md:6` `- Inspected `c:\Users\ADMIN\AEGIS-COGNITION\core\python\aegis_adapter.py`. Found that `AegisAdapter` initialization takes `trust_level`, `llm`, `fallback_providers`, and `provider_budgets`. `_normalize_trust_level` checks the `AEGIS_TRUST_LEVEL` environment variable. Also found `ProviderRateLimitError`.`
- `.local/agent-state/agents/teamwork_preview_explorer_m3_1/handoff.md:15` `- The Rust integration involves mapping the loaded configuration to `ProviderConfig` and initializing `ProviderRuntimeBudget` instances to leverage Rust's robust throughput throttling and fallback routing seamlessly.`
- `.local/agent-state/agents/worker_m1_1/BRIEFING.md:39` `- Keep exact fallback string check in `lookup` to guard against hash collisions.`
- `.local/agent-state/agents/worker_m1_1/handoff.md:19` `- Configured production-grade loading/saving with atomic temp-file write-and-rename mechanism, parent directory creation (`std::fs::create_dir_all`), and fallback behavior for Windows/permission issues.`
- `.local/agent-state/agents/worker_m1_1/handoff.md:34` `2. To guard against hash collisions, the exact match lookup implements a fallback string comparison check (`record.prompt.trim() == prompt.trim()`), providing bulletproof correctness (Step 2).`
- `.local/agent-state/agents/worker_m1_1/handoff.md:36` `4. For file caching, simple file writes can cause cache corruption if aborted mid-write. The saving routine writes to a `.tmp` file and performs a rename (atomic on most OSes). A double rename/delete fallback is added specifically to handle Windows locked-file behaviors (Step 2).`
- `.local/agent-state/serena/project.yml:166` `# The first language server is the default language and the respective language server will be used as a fallback.`
- `aegis_cognition/application.py:117` `for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")`
- `aegis_cognition/desktop_service.py:78` `SERVICE_VERSION_FALLBACK = "0.1.0"`
- `aegis_cognition/desktop_service.py:96` `return os.environ.get("AEGIS_DESKTOP_SERVICE_VERSION", SERVICE_VERSION_FALLBACK)`
- `aegis_cognition/errors.py:233` `The task may have completed successfully using a fallback provider.`
- `aegis_cognition/errors.py:238` `2. Add more fallback providers:`
- `aegis_cognition/errors.py:239` `agent = Agent(task="...", fallback_providers=["anthropic", "openrouter"])`
- `aegis_cognition/lab.py:184` `if trust_level == "PROD" and authority_mode is AuthorityMode.PROJECTION_ONLY:`
- `aegis_cognition/lab.py:211` `def _authority_mode_from_options(options: Mapping[str, Any], *, default_trust_level: str) -> AuthorityMode:`
- `aegis_cognition/lab.py:224` `_normalize_lab_trust_level(default_trust_level) == "PROD",`
- `aegis_cognition/lab.py:359` `fallback_providers = options.get("fallback_providers")`
- `aegis_cognition/lab.py:360` `if fallback_providers is not None:`
- `aegis_cognition/lab.py:361` `if type(fallback_providers) not in (list, tuple):`
- `aegis_cognition/lab.py:362` `raise ValueError("native Lab fallback_providers must be a list or tuple")`
- `aegis_cognition/lab.py:363` `typed_fallbacks = cast(list[Any] | tuple[Any, ...], fallback_providers)`
- `aegis_cognition/lab.py:364` `for item in typed_fallbacks:`
- `aegis_cognition/lab.py:368` `raise ValueError("native Lab fallback provider entries must be 2-item pairs")`
- `aegis_cognition/lab.py:371` `raise ValueError("native Lab fallback provider names must be null or non-empty trimmed strings")`
- `aegis_cognition/lab.py:373` `_validate_native_provider_identity(typed_item[1], label="fallback")`
- `aegis_cognition/lab.py:375` `_validate_native_provider_identity(item, label="fallback")`
- `aegis_cognition/lab.py:3427` `trust_level: str = "DEV",`
- `aegis_cognition/lab.py:5339` `return self.require_native_authority or self.trust_level == "PROD"`
- `aegis_cognition/lab.py:8462` `trust_level: str = "DEV"`
- `aegis_cognition/lab.py:8517` `"""Require Rust admission by default for production trust levels."""`
- `aegis_cognition/lab.py:8521` `return _normalize_lab_trust_level(self.trust_level) == "PROD"`
- `aegis_cognition/lab.py:9075` `trust_level: str = "DEV",`
- `aegis_cognition/lab.py:9438` `authority_mode = _authority_mode_from_options(options, default_trust_level=self.config.trust_level)`
- `aegis_cognition/lab.py:9857` `for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")`
- `aegis_cognition/lab.py:9861` `authority_mode = _authority_mode_from_options(options, default_trust_level=self.config.trust_level)`
- `aegis_cognition/lab.py:9948` `logical model call, while these nested receipts make fallback routing`
- `aegis_cognition/lab.py:10041` `"configured_fallback_providers_hash": _hash(self.config.options.get("fallback_providers", ())),`
- `aegis_cognition/lab.py:10241` `fallback_used = getattr(route, "fallback_used", None)`
- `aegis_cognition/lab.py:10242` `if type(fallback_used) is not bool:`
- `aegis_cognition/lab.py:10243` `raise RuntimeError("native Lab authority requires a typed provider fallback flag")`
- `aegis_cognition/lab.py:11511` `"configured_fallback_providers_hash": _hash(self.config.options.get("fallback_providers", ())),`
- `aegis_cognition/lab.py:11666` `"provider_fallback_used": bool(getattr(route, "fallback_used", False)),`
- `aegis_cognition/lab.py:11872` `effect_required = raw_effect_required or (raw_trust_level.strip().upper() == "PROD")`
- `aegis_cognition/lab.py:12337` `authority_mode = _authority_mode_from_options(options, default_trust_level=self.config.trust_level)`
- `aegis_cognition/prompt.py:17` `trust_level: str = "DEV",`
- `aegis_cognition/prompt.py:25` `# Set intelligent default security level based on trust_level & task_type`
- `aegis_cognition/prompt.py:29` `if self.trust_level == "PROD":`
- `aegis_cognition/prompt.py:38` `if self.trust_level == "PROD" and "Yêu cầu bằng chứng vật lý" not in self.constraints:`
- `aegis_cognition/prompt.py:40` `if self.trust_level == "PROD" and "Xác thực qua browser witness" not in self.constraints:`
- `aegis_cognition/runtime.py:60` `"""A bounded runtime lease, or an explicitly non-authoritative fallback."""`
- `aegis_cognition/runtime.py:209` `"source": "python-fallback",`
- `aegis_cognition/runtime.py:225` `"os": {"backend": "python-fallback", "enforcement": "unsupported"},`
- `aegis_cognition/runtime.py:240` `"""Return Rust lane limits, or a conservative unverified fallback."""`
- `aegis_cognition/runtime.py:1129` `trust_level: str = "DEV",`
- `aegis_cognition/runtime.py:1148` `if normalized_trust_level == "PROD":`
- `aegis_cognition/runtime.py:1195` `trust_level: str = "DEV",`
- `aegis_cognition/runtime.py:1214` `if normalized_trust_level == "PROD":`
- `aegis_cognition/runtime.py:1258` `trust_level: str = "DEV",`
- `aegis_cognition/runtime.py:1300` `trust_level: str = "DEV",`
- `core/python/aegis/code_intelligence.py:282` `extraction_status="SYNTAX_ERROR_STALE_FALLBACK",`
- `core/python/aegis/code_intelligence.py:462` `return [], [], "FALLBACK_HASH_ONLY", None`
- `core/python/aegis/contracts.py:99` `fallback_used: bool`
- `core/python/aegis/evidence.py:51` `if level != "DEV":`
- `core/python/aegis/evidence.py:60` `verifier="python-dev-fallback",`
- `core/python/aegis/evidence.py:83` `if level != "DEV":`
- `core/python/aegis/evidence.py:107` `"verifier": "python-dev-batch-fallback",`
- `core/python/aegis/evidence.py:122` `"verifier": "python-dev-batch-fallback",`
- `core/python/aegis/native.py:13` `# exercise the no-extension fallback even when an editable maturin build`
- `core/python/aegis/provider.py:26` `def normalize_fallback_providers(fallback_providers: Any) -> tuple[tuple[str | None, Any], ...]:`
- `core/python/aegis/provider.py:27` `if not fallback_providers:`
- `core/python/aegis/provider.py:30` `for item in fallback_providers:`
- `core/python/aegis/provider.py:73` `def task_from_runnable_input(value: Any, fallback_task: str | None = None) -> str:`
- `core/python/aegis/provider.py:75` `if fallback_task:`
- `core/python/aegis/provider.py:76` `return fallback_task`
- `core/python/aegis/provider.py:86` `return task_from_runnable_input(nested, fallback_task)`
- `core/python/aegis/provider.py:179` `fallback_providers: tuple[tuple[str | None, Any], ...],`
- `core/python/aegis/provider.py:191` `*fallback_providers,`
- `core/python/aegis/provider.py:283` `fallback_used=index > 0,`
- `core/python/aegis/provider.py:337` `fallback_used: bool,`
- `core/python/aegis/provider.py:341` `downgraded_model = fallback_used`
- `core/python/aegis/provider.py:349` `"fallback_used": fallback_used,`
- `core/python/aegis/provider.py:362` `fallback_used=fallback_used,`
- `core/python/aegis/trust_policy.py:4` `friendly Agent path defaults to ``DEV`` while the standalone evidence bridge`
- `core/python/aegis/trust_policy.py:5` `defaults to ``PROD``).  This module owns the policy schema and digest so those`
- `core/python/aegis/trust_policy.py:21` `def normalize_trust_level(value: str | None, *, default: str = "PROD") -> str:`
- `core/python/aegis/trust_policy.py:29` `selected = (value or os.environ.get("AEGIS_TRUST_LEVEL") or default).strip().upper()`
- `core/python/aegis/trust_policy.py:44` `"physical_witness_required": level == "PROD",`
- `core/python/aegis/trust_policy.py:45` `"fail_closed": level == "PROD",`
- `core/python/aegis/trust_policy.py:46` `"degraded_hot_evidence_allowed": level == "DEV",`
- `core/python/aegis/trust_policy.py:47` `"rust_extension_required": level != "DEV",`
- `core/python/aegis/trust_policy.py:53` `"dual_approval_required": level == "PROD",`
- `core/python/aegis_adapter.py:66` `normalize_fallback_providers as _normalize_fallback_providers,`
- `core/python/aegis_adapter.py:112` `normalize_fallback_providers as _normalize_fallback_providers,`
- `core/python/aegis_adapter.py:132` `fallback_providers: Any = None,`

Previously suspected contradiction remains mechanically present: `AgentConfig` defaults trust to `DEV`, while core evidence normalization defaults to `PROD`. This is **UNRESOLVED_POLICY_AMBIGUITY** until one authoritative source and contract test exist.

## 11. Large module responsibility map

Size alone is not used as a split recommendation. Responsibility clusters inferred from names/calls are:

| Module | Responsibility clusters | Classification |
|---|---|---|
| `aegis_cognition/lab.py` | run lifecycle; Python projection/event append; execution-cell registry; search/fetch; browser; experiments/simulation; gateway/provider; benchmark; archive/recovery; memory context/index | `MIXED_RESPONSIBILITY` (large orchestration surface; split not authorized by size) |
| `core/rust/src/lab.rs` | typed domain records; event kinds/hash; controller admission; budget/finalization; snapshot/restore; archive/replay | `COHESIVE` with high contract density |
| `core/rust/src/replay.rs` | binary record validation; hash chain; snapshot/archive; recovery semantics | `COHESIVE` / high branching |
| `core/rust/src/cli/mod.rs` | command parsing; policy/config; dispatch; output/error translation | `MIXED_RESPONSIBILITY` |
| `core/rust/src/ffi.rs` | PyO3 conversion; runtime/resource/evidence bindings; LabController binding; panic/error boundary | `GOD_MODULE_CANDIDATE` by boundary breadth, not size alone |
| `scripts/evidence_consistency_gate.py` | manifest schema; commit binding; remediation parity; suite evidence; report output | `MIXED_RESPONSIBILITY` |

Exact definitions and call evidence are recoverable from the JSON graphs; no reorganization was performed.

## 12. Duplication map

`architecture_duplicate_candidates.json` records semantic domains and exact implementation hits. Candidates are labelled `UNKNOWN` until contract-level equivalence is demonstrated. Main domains with multiple implementations are hashing, ID generation, retry, budget, trust, capability/admission, serialization, event creation, evidence validation, provider routing, browser policy, replay validation, context handling, telemetry, and error conversion. Some are intentional projection/compatibility; static text overlap cannot distinguish all cases.

## 13. Error model

The error artifact separates Python broad catches/conversions from Rust `unwrap`/`expect`/`panic!`/`unsafe`/`Box::leak` hits and marks likely test context lexically. Important exact cases remain:

- `core/rust/src/ffi.rs:24`: `SessionSearchIndex::new(...).unwrap()` on a native initialization path;
- `core/rust/src/ffi.rs` around the LLM bridge: `Box::leak` is used for request/error strings; possible per-call leak requires ownership confirmation;
- broad Python catches in application/adapter/provider/Lab/browser paths can convert failures into recorded blockers, but broad catching is still a compatibility/error-surface risk.

Static counts are not production-only panic counts because Rust tests are embedded in source files. Full rows are in `architecture_error_model.json`.

## 14. Dependency rent data

`architecture_dependency_rent.json` maps direct Python/Rust declarations to usage-hit locations, role (production/test/build/POC/unknown), feature declaration, duplicate capability, and runtime relevance. It intentionally does not recommend removal. PyO3/maturin, Wasmtime, Arrow/mmap/IPC, Playwright, provider SDKs, plugins, and POC crates create material ABI, security, platform, build, and operational rent.

## 15. Actual performance profile

**`NOT_MEASURABLE_LOCALLY` for representative Lab/non-Lab runs in this pass.** No external credentials/provider call, native wheel build, Lab workload, benchmark, or browser session was invoked. Startup/FFI/event/hash/serialization/subprocess timings are therefore not invented. The profile status and reason are in `architecture_performance.json`.

## 16. Static performance suspects

The artifact records lexical suspects only: repeated JSON/Serde serialization, repeated hashing, clones/copies, lock use, filesystem scans, context reconstruction, subprocess startup, FFI crossings, and unbounded collection growth. All are `SUSPECT_ONLY_UNMEASURED`; no performance regression or optimization claim follows.

## 17. Concurrency ownership

Fresh pattern inventory covers Python asyncio/tasks/threads/processes/locks/global state and Rust Tokio/spawn/Rayon/Arc/Mutex/RwLock/atomics/TCP. Full rows are in `architecture_concurrency.json`. Flags are `MULTIPLE_WRITERS`, `LOCK_ACROSS_AWAIT`, `GLOBAL_MUTABLE`, `POTENTIAL_CONTENTION`, and `UNKNOWN`; no race is claimed without a reproducer or proof.

## 18. Public API inventory

`architecture_public_api.json` lists root Python facade symbols, compatibility package/CLI, Rust public modules/declarations, PyO3 exposure, schemas, plugins, and both `aegis` console-script definitions. Root `aegis_cognition` is the stable-public candidate; `core/python` is compatibility; many Rust modules are internal-but-public. Accidental public surface requires explicit API review.

## 19. Current architecture graph

```text
[PUBLIC/API]
  aegis_cognition.Agent [A]
      -> AgentApplication [A]
      -> core/python compatibility facade [C]
      -> CLI entrypoints [A/C]

[SEMANTIC]
  AgentApplication -> LabApplication [A when lab=True]
  LabApplication -> LabRun Python projection [A for current lifecycle]
  LabRun -> typed mission/source/claim/hypothesis/experiment/observation [P/A]

[STATE/AUTHORITY]
  LabRun Python state [A for projection]
      -> PyLabController/Rust LabRuntime [A for native admission/budget/replay]
      -> ExecutionCellRegistry [A for explicit cell policy]
  Rust controller -> GT96/resource/replay/evidence [A]

[SCHEDULING/RESOURCE]
  Agent/Lab -> resource/execution lanes [A/P]
  replay directory -> ReplayWriterLease [A local / C hosted gap]

[EXECUTION CELLS]
  cells -> search/urlopen [E]
  cells -> browser/Playwright [E]
  cells -> experiments/simulation [E]
  cells -> provider/gateway [E]
  cells -> benchmark subprocess [E]
  cells -> memory context/index [E]

[ADAPTERS]
  provider route -> candidates/retry/fallback [C/P]
  browser runtime -> network/process/browser [E]
  operator/cluster -> artifact/network surfaces [E]

[EVIDENCE/REPLAY]
  Python append -> hash/native admission/Arrow audit [P/A]
  Rust event chain -> snapshot/restore/archive verify [A]

[STORAGE]
  filesystem/replay/mmap/Arrow/memory [E/R]

[PACKAGING]
  root maturin -> aegis_cognition + aegis_cognition.aegis_nerve [A]
  core/python setuptools -> compatibility package + same `aegis` name [C]
```

This is the current graph only; it is not a target architecture. `[A]` means observed authority for that scope, `[P]` proposal/projection, `[R]` read, `[E]` effect, `[C]` compatibility.

## 20. Machine-readable output

Generated audit artifacts:

- `architecture_python_import_graph.json`
- `architecture_rust_module_graph.json`
- `architecture_reachability.json`
- `architecture_ffi_inventory.json`
- `architecture_authority_matrix.json`
- `architecture_side_effect_graph.json`
- `architecture_retry_graph.json`
- `architecture_public_api.json`
- `architecture_duplicate_candidates.json`
- `architecture_dependency_rent.json`
- `architecture_trust_policy.json`
- `architecture_error_model.json`
- `architecture_concurrency.json`
- `architecture_performance.json`
- `architecture_wheel_graph.json`

## Evidence boundary

This pack gives fresh machine-grounded design inputs, not proof of security, production readiness, benchmark superiority, scientific validity, complete reachability, or universal native authority. Missing dynamic/macro/hosted/platform evidence is enumerated rather than filled with assumptions.
