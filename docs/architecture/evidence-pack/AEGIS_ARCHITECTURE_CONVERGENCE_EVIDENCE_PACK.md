# AEGIS — Architecture Convergence Evidence Pack

**Collection date:** 2026-08-28  
**Repository:** `C:\Users\ADMIN\AEGIS-COGNITION`  
**Method:** fresh filesystem + Git + Python AST + Rust source + `cargo metadata --no-deps`; repository knowledge-graph index intentionally not used as truth.  
**Safety:** no refactor, delete, feature, wheel build, provider call, benchmark campaign, fuzz, soak, or stress workload was run.

This document is an evidence pack, not a redesign. It reports what the current working tree mechanically supports and labels uncertainty. Generated JSON artifacts in this directory are audit outputs only.

## 1. Current state snapshot

| Field | Value |
|---|---|
| HEAD | `f9645caf6d17cee2023d52183990ffcf8317e456` |
| branch | `main` |
| origin/main ref | `origin/main` |
| ahead/behind | `10	0` |
| tracked modified files | `58` |
| staged files | `0` |
| untracked files | `19` |
| exact total status entries | `77` |

### File classification

- `.github/workflows/ci.yml` → **UNKNOWN**
- `.github/workflows/deep.yml` → **UNKNOWN**
- `.github/workflows/release.yml` → **UNKNOWN**
- `.gitignore` → **UNKNOWN**
- `aegis_cognition/__init__.py` → **PRODUCT**
- `aegis_cognition/agent.py` → **PRODUCT**
- `aegis_cognition/application.py` → **PRODUCT**
- `aegis_cognition/models.py` → **PRODUCT**
- `core/python/aegis/provider.py` → **COMPATIBILITY**
- `core/python/aegis_adapter.py` → **COMPATIBILITY**
- `core/python/aegis_cli.py` → **COMPATIBILITY**
- `core/python/bridge.py` → **COMPATIBILITY**
- `core/python/bridge_mmap.py` → **COMPATIBILITY**
- `core/python/browser_live_collector.py` → **COMPATIBILITY**
- `core/python/browser_ops_bench.py` → **COMPATIBILITY**
- `core/python/browser_playwright_runtime.py` → **COMPATIBILITY**
- `core/python/browser_runtime_adapter.py` → **COMPATIBILITY**
- `core/python/nim_client.py` → **COMPATIBILITY**
- `core/python/operator_api.py` → **COMPATIBILITY**
- `core/python/orchestrator.py` → **COMPATIBILITY**
- `core/python/tests.py` → **TEST**
- `core/rust/benches/nerve_bench.rs` → **PRODUCT**
- `core/rust/src/ffi.rs` → **PRODUCT**
- `core/rust/src/gt96.rs` → **PRODUCT**
- `core/rust/src/lib.rs` → **PRODUCT**
- `core/rust/src/memory/fold.rs` → **PRODUCT**
- `core/rust/src/physical.rs` → **PRODUCT**
- `core/rust/src/replay.rs` → **PRODUCT**
- `core/rust/src/task_ledger.rs` → **PRODUCT**
- `core/rust/src/tests.rs` → **PRODUCT**
- `core/rust/src/tool_gateway.rs` → **PRODUCT**
- `docs/adr/ADR-012-portability.md` → **DOCUMENTATION**
- `docs/architecture/CONTRACT_INVENTORY.md` → **DOCUMENTATION**
- `docs/architecture/GT96_TRACEABILITY.md` → **DOCUMENTATION**
- `docs/architecture/NOT_VERIFIED_REGISTRY.md` → **DOCUMENTATION**
- `docs/architecture/deployment_policy.json` → **DOCUMENTATION**
- `docs/architecture/evidence/current.json` → **DOCUMENTATION**
- `pyproject.toml` → **UNKNOWN**
- `scripts/_audit_regen_requirements.py` → **SCRIPT**
- `scripts/_debug_probe.py` → **SCRIPT**
- `scripts/constitution_audit.py` → **SCRIPT**
- `scripts/dynamic_provider_fallback_gate.py` → **SCRIPT**
- `scripts/e2e_release_gate.py` → **SCRIPT**
- `scripts/evidence_consistency_gate.py` → **SCRIPT**
- `scripts/external_deployment_smoke_gate.py` → **SCRIPT**
- `scripts/extreme_testing_suite.py` → **SCRIPT**
- `scripts/hermes_rpc_baseline_gate.py` → **SCRIPT**
- `scripts/production_closure.py` → **SCRIPT**
- `scripts/production_readiness.py` → **SCRIPT**
- `scripts/python_hotpath_gate.py` → **SCRIPT**
- `scripts/release_install_smoke.py` → **SCRIPT**
- `scripts/release_rollback_drill.py` → **SCRIPT**
- `scripts/run_checks.py` → **SCRIPT**
- `scripts/smoke_check.py` → **SCRIPT**
- `scripts/sota_baseline_gate.py` → **SCRIPT**
- `scripts/supply_chain_gate.py` → **SCRIPT**
- `tests/test_evidence_consistency_gate.py` → **TEST**
- `uv.lock` → **UNKNOWN**
- `.serena/` → **UNKNOWN**
- `aegis_cognition/benchmark.py` → **PRODUCT**
- `aegis_cognition/lab.py` → **LAB**
- `core/rust/src/lab.rs` → **LAB**
- `docs/adr/ADR-015-portability.md` → **DOCUMENTATION**
- `docs/api/` → **DOCUMENTATION**
- `docs/architecture.md` → **DOCUMENTATION**
- `docs/architecture/AEGIS_CURRENT_ARCHITECTURE_TRUTH_AUDIT.md` → **DOCUMENTATION**
- `docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md` → **DOCUMENTATION**
- `docs/architecture/AEGIS_LAB_STATUS_GENERATED.md` → **DOCUMENTATION**
- `docs/architecture/document_inventory.json` → **DOCUMENTATION**
- `docs/architecture/evidence-pack/` → **GENERATED**
- `docs/troubleshooting/` → **DOCUMENTATION**
- `docs/tutorials/` → **DOCUMENTATION**
- `scripts/document_consistency_gate.py` → **SCRIPT**
- `scripts/release_controller_action_smoke.py` → **SCRIPT**
- `scripts/release_recovery_smoke.py` → **SCRIPT**
- `tests/test_document_consistency_gate.py` → **TEST**
- `tests/test_lab_runtime.py` → **TEST**

The generated evidence-pack files are included in the final status snapshot as `GENERATED`; the pre-generation snapshot is retained in `architecture_reachability.json` under `collection_state_before_generation` when available.

## 2. Fresh Python import graph

AST scan found **110 modules** and **146 resolved internal import edges**. Full adjacency is in `architecture_python_import_graph.json`.

- Cycles mechanically detected: `0`. Cycles are listed in the JSON; dynamic/decorator edges can be missing.
- Dynamic imports: `15` module records.
- `sys.path` manipulation records: `13`.
- Import-time side-effect candidates: `0` lexical candidates.
- Lazy import detection is conservative; imports nested in functions need parent-aware AST analysis before being classified as intentional.

Highest fan-in:
- `aegis_cognition` fan_in=13 fan_out=0 (aegis_cognition/__init__.py)
- `core.python` fan_in=5 fan_out=1 (core/python/__init__.py)
- `core.python.browser_live_collector` fan_in=5 fan_out=0 (core/python/browser_live_collector.py)
- `aegis_cognition.observability` fan_in=4 fan_out=2 (aegis_cognition/observability.py)
- `core.python.browser_runtime_adapter` fan_in=4 fan_out=1 (core/python/browser_runtime_adapter.py)
- `core.python.operator_api` fan_in=4 fan_out=0 (core/python/operator_api.py)
- `scripts.deployment_manifest` fan_in=4 fan_out=0 (scripts/deployment_manifest.py)
- `aegis_cognition.config` fan_in=3 fan_out=1 (aegis_cognition/config.py)
- `aegis_cognition.runtime` fan_in=3 fan_out=1 (aegis_cognition/runtime.py)
- `core.python.aegis.contracts` fan_in=3 fan_out=0 (core/python/aegis/contracts.py)
- `core.python.aegis.hashing` fan_in=3 fan_out=0 (core/python/aegis/hashing.py)
- `core.python.aegis_adapter` fan_in=3 fan_out=7 (core/python/aegis_adapter.py)

Highest fan-out:
- `core.python.tests` fan_in=0 fan_out=31 (core/python/tests.py)
- `scripts.run_checks` fan_in=1 fan_out=26 (scripts/run_checks.py)
- `scripts.e2e_release_gate` fan_in=3 fan_out=10 (scripts/e2e_release_gate.py)
- `aegis_cognition.application` fan_in=1 fan_out=7 (aegis_cognition/application.py)
- `core.python.aegis_adapter` fan_in=3 fan_out=7 (core/python/aegis_adapter.py)
- `aegis_cognition.agent` fan_in=1 fan_out=6 (aegis_cognition/agent.py)
- `aegis_cognition.lab` fan_in=2 fan_out=5 (aegis_cognition/lab.py)
- `tests.test_lab_runtime` fan_in=0 fan_out=5 (tests/test_lab_runtime.py)
- `core.python.aegis.evidence` fan_in=1 fan_out=3 (core/python/aegis/evidence.py)
- `tests.test_runtime_contracts` fan_in=0 fan_out=3 (tests/test_runtime_contracts.py)
- `aegis_cognition.observability` fan_in=4 fan_out=2 (aegis_cognition/observability.py)
- `core.python.aegis.provider` fan_in=1 fan_out=2 (core/python/aegis/provider.py)

## 3. Rust module + crate graph

`cargo metadata --no-deps` status: **PROVEN**. Workspace packages, dependencies, features, targets, modules, visibility, public declarations, and target limitations are in `architecture_rust_module_graph.json`.

High-coupling Rust modules (source scan):
- `core/rust/AEGIS-COGNITION/core/rust/src/licensing.rs` `aegis-nerve::AEGIS-COGNITION::core::rust::src::licensing` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/AEGIS-COGNITION/core/rust/src/rbac.rs` `aegis-nerve::AEGIS-COGNITION::core::rust::src::rbac` fan_in=0 fan_out=0 pub=22 responsibility=module-local responsibility; inspect source
- `core/rust/benches/architecture_performance.rs` `aegis-nerve::benches::architecture_performance` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/benches/nerve_bench.rs` `aegis-nerve::benches::nerve_bench` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/benches/resource_runtime.rs` `aegis-nerve::benches::resource_runtime` fan_in=0 fan_out=0 pub=0 responsibility=resource admission/platform
- `core/rust/benches/shadow_sealer_throughput.rs` `aegis-nerve::benches::shadow_sealer_throughput` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/benches/skill_improvement.rs` `aegis-nerve::benches::skill_improvement` fan_in=0 fan_out=0 pub=0 responsibility=module-local responsibility; inspect source
- `core/rust/src/bridge_mmap.rs` `aegis-nerve::src::bridge_mmap` fan_in=0 fan_out=0 pub=21 responsibility=module-local responsibility; inspect source
- `core/rust/src/browser_witness.rs` `aegis-nerve::src::browser_witness` fan_in=0 fan_out=0 pub=94 responsibility=module-local responsibility; inspect source
- `core/rust/src/circuit_breaker.rs` `aegis-nerve::src::circuit_breaker` fan_in=0 fan_out=0 pub=14 responsibility=module-local responsibility; inspect source
- `core/rust/src/cli/mod.rs` `aegis-nerve::src::cli` fan_in=0 fan_out=0 pub=33 responsibility=CLI
- `core/rust/src/context.rs` `aegis-nerve::src::context` fan_in=0 fan_out=0 pub=40 responsibility=module-local responsibility; inspect source
- `core/rust/src/descriptor.rs` `aegis-nerve::src::descriptor` fan_in=0 fan_out=0 pub=4 responsibility=module-local responsibility; inspect source
- `core/rust/src/distributed.rs` `aegis-nerve::src::distributed` fan_in=0 fan_out=0 pub=29 responsibility=module-local responsibility; inspect source
- `core/rust/src/eac/cache/mod.rs` `aegis-nerve::src::eac::cache` fan_in=0 fan_out=0 pub=2 responsibility=module-local responsibility; inspect source

The graph does not claim macro-expanded, trait-dispatch, generated, or rust-analyzer-complete edges. `pub mod` and `pub use` are recorded conservatively; accidental-public status requires API review.

The current tree also contains a nested, non-workspace mirror at `core/rust/AEGIS-COGNITION/` (two Rust files plus a website). It is not represented as a Cargo workspace member in the root metadata. Its ownership is therefore `UNKNOWN`/historical-or-experimental until explicitly classified; it must not be silently treated as production Rust.

## 4. Production reachability graph

Root labels are separated as `PUBLIC_API`, `LAB`, `CLI`, `FFI`, `PLUGIN`, `TEST_ONLY`, `SCRIPT_ONLY`, and `POC_ONLY`. Reachability counts from the fresh static graph: `{'CLI': 3, 'FFI': 13, 'LAB': 23, 'PUBLIC_API': 24, 'TEST_ONLY': 68, 'SCRIPT_ONLY': 58, 'POC_ONLY': 1}`. Full per-file rows are in `architecture_reachability.json`.

The allowed final class is deliberately conservative: static absence yields `UNKNOWN`, not delete authorization. Dynamic imports, plugin discovery, packaging includes, console scripts, subprocesses, and documentation references are separately recorded.

## 5. Exact package / wheel graph

The current configuration declares two package/build surfaces and two `aegis` console-script owners. The source→wheel→import mappings and existing wheel search are in `architecture_wheel_graph.json`.

**Wheel build/listing was not run** to avoid a potentially heavy native build and source mutation. Source-checkout imports therefore remain insufficient to claim wheel correctness.

## 6. FFI contract inventory

Fresh PyO3 scan found **93 exposed function/class/method candidates**, including methods inside every detected `#[pymethods]` implementation. Full records include Python name, Rust symbol/type/receiver, callers, inputs/outputs, serialization/copying suspicion, side-effect class, authority relevance, frequency category, and flags.

- `aegis-plugins/aegis-search-sdk/src/lib.rs:475` `SearchQuery` → `PySearchQuery`; flags=none
- `aegis-plugins/aegis-search-sdk/src/lib.rs:494` `lexical_sync` → `lexical_sync`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:45` `aegis_status` → `aegis_status`; flags=none
- `core/rust/src/ffi.rs:50` `aegis_validate_schema` → `aegis_validate_schema`; flags=LARGE_PAYLOAD
- `core/rust/src/ffi.rs:55` `aegis_validate_layout` → `aegis_validate_layout`; flags=LARGE_PAYLOAD
- `core/rust/src/ffi.rs:60` `aegis_memory_alignment` → `aegis_memory_alignment`; flags=none
- `core/rust/src/ffi.rs:65` `aegis_nerve_schema_id` → `aegis_nerve_schema_id`; flags=none
- `core/rust/src/ffi.rs:70` `aegis_frame_is_valid` → `aegis_frame_is_valid`; flags=none
- `core/rust/src/ffi.rs:75` `aegis_message_frame_valid` → `aegis_message_frame_valid`; flags=none
- `core/rust/src/ffi.rs:84` `aegis_zero_copy_ready` → `aegis_zero_copy_ready`; flags=LARGE_PAYLOAD
- `core/rust/src/ffi.rs:89` `aegis_mmap_bridge_header_bytes` → `aegis_mmap_bridge_header_bytes`; flags=LARGE_PAYLOAD
- `core/rust/src/ffi.rs:94` `aegis_mmap_bridge_payload_alignment` → `aegis_mmap_bridge_payload_alignment`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:99` `aegis_write_mmap_bridge_pattern` → `aegis_write_mmap_bridge_pattern`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:124` `aegis_validate_mmap_bridge_frame` → `aegis_validate_mmap_bridge_frame`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:129` `aegis_execute_mmap_wasm_bridge_frame` → `aegis_execute_mmap_wasm_bridge_frame`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:143` `aegis_new_message_identity` → `aegis_new_message_identity`; flags=none
- `core/rust/src/ffi.rs:148` `aegis_can_bridge_python` → `aegis_can_bridge_python`; flags=none
- `core/rust/src/ffi.rs:157` `aegis_layout_header_bytes` → `aegis_layout_header_bytes`; flags=LARGE_PAYLOAD
- `core/rust/src/ffi.rs:162` `aegis_layout_payload_alignment` → `aegis_layout_payload_alignment`; flags=none
- `core/rust/src/ffi.rs:167` `aegis_descriptor_valid` → `aegis_descriptor_valid`; flags=none
- `core/rust/src/ffi.rs:172` `aegis_cli_status` → `aegis_cli_status`; flags=none
- `core/rust/src/ffi.rs:177` `aegis_cli_schema` → `aegis_cli_schema`; flags=none
- `core/rust/src/ffi.rs:182` `aegis_release_ready` → `aegis_release_ready`; flags=none
- `core/rust/src/ffi.rs:190` `aegis_hardware_profile` → `aegis_hardware_profile`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:201` `aegis_resource_contract_version` → `aegis_resource_contract_version`; flags=none
- `core/rust/src/ffi.rs:211` `aegis_resource_admission_preview` → `aegis_resource_admission_preview`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:235` `aegis_execution_lanes` → `aegis_execution_lanes`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:253` `aegis_runtime_submit` → `aegis_runtime_submit`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:305` `aegis_runtime_retry` → `aegis_runtime_retry`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:344` `aegis_runtime_finish` → `aegis_runtime_finish`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:376` `aegis_llm_request` → `aegis_llm_request`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:394` `aegis_resource_usage_sample` → `aegis_resource_usage_sample`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:406` `aegis_llm_route` → `aegis_llm_route`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:424` `aegis_llm_bridge_key` → `aegis_llm_bridge_key`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:429` `aegis_llm_normalize` → `aegis_llm_normalize`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:438` `aegis_llm_reject` → `aegis_llm_reject`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:448` `aegis_harness_generate_skeleton` → `aegis_harness_generate_skeleton`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:454` `aegis_harness_analyze_errors` → `aegis_harness_analyze_errors`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:460` `aegis_physical_metrics_prometheus` → `aegis_physical_metrics_prometheus`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:465` `aegis_runtime_telemetry_emit` → `aegis_runtime_telemetry_emit`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:473` `aegis_runtime_telemetry_snapshot` → `aegis_runtime_telemetry_snapshot`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:481` `aegis_trust_level` → `aegis_trust_level`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `core/rust/src/ffi.rs:486` `aegis_hot_hash` → `aegis_hot_hash`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:491` `aegis_hot_commit` → `aegis_hot_commit`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:538` `aegis_hot_commit_batch` → `aegis_hot_commit_batch`; flags=LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:620` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:636` `UNKNOWN` → `UNKNOWN`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:654` `aegis_eac_cache_invalidate` → `aegis_eac_cache_invalidate`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:671` `aegis_eac_batch` → `aegis_eac_batch`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:707` `UNKNOWN` → `UNKNOWN`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:738` `aegis_eac_load_state` → `aegis_eac_load_state`; flags=COPY_HEAVY,STRINGLY_TYPED
- `core/rust/src/ffi.rs:764` `aegis_get_learning_stats` → `aegis_get_learning_stats`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:832` `aegis_trigger_memory_nudge` → `aegis_trigger_memory_nudge`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:875` `aegis_index_session` → `aegis_index_session`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:903` `aegis_search_past_sessions` → `aegis_search_past_sessions`; flags=STRINGLY_TYPED
- `core/rust/src/ffi.rs:956` `LabController` → `PyLabController`; flags=AUTHORITY_SENSITIVE
- `core/rust/src/ffi.rs:1166` `aegis_lab_verify_event_chain` → `aegis_lab_verify_event_chain`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:1183` `aegis_lab_validate_transition` → `aegis_lab_validate_transition`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `core/rust/src/ffi.rs:1211` `aegis_lab_validate_snapshot` → `aegis_lab_validate_snapshot`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:1220` `aegis_lab_archive_events` → `aegis_lab_archive_events`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED
- `core/rust/src/ffi.rs:1254` `aegis_lab_verify_archive` → `aegis_lab_verify_archive`; flags=AUTHORITY_SENSITIVE,COPY_HEAVY,STRINGLY_TYPED
- `core/rust/src/ffi.rs:1277` `aegis_lab_verify_archive_against_manifest` → `aegis_lab_verify_archive_against_manifest`; flags=AUTHORITY_SENSITIVE,STRINGLY_TYPED
- `pocs/semantic_cache_poc/src/lib.rs:16` `SemanticCache` → `SemanticCache`; flags=COPY_HEAVY,LARGE_PAYLOAD,STRINGLY_TYPED

Macro-generated or re-exported exposure may be missing; caller frequency is a category estimate based on static callsites, not runtime telemetry.

## 7. Canonical state ownership matrix

Full 24-entity matrix is in `architecture_authority_matrix.json`. The current tree mechanically indicates a split for Lab entities: Python constructs/mutates a mutable projection and Rust validates/adjudicates many typed admissions, budgets, replay, and finalization operations. `TrustLevel` and `RetryPolicy` have compatibility duplicates/split ownership. No solution is recommended in this pack.

## 8. Side-effect reachability

Fresh lexical/AST side-effect candidates:

- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `BROWSER`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `ENVIRONMENT_MUTATION`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:24` `aegis_cognition.agent:__init__` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:71` `aegis_cognition.agent:_validate` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:71` `aegis_cognition.agent:_validate` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:76` `aegis_cognition.agent:_prepare_rag_and_prompt` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:76` `aegis_cognition.agent:_prepare_rag_and_prompt` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/agent.py:79` `aegis_cognition.agent:_index_completed_run` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/agent.py:79` `aegis_cognition.agent:_index_completed_run` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/agent.py:92` `aegis_cognition.agent:run` → `PROVIDER_CALL`; roots=aegis_cognition.agent:run
- `aegis_cognition/agent.py:85` `aegis_cognition.agent:arun` → `PROVIDER_CALL`; roots=aegis_cognition.agent:arun
- `aegis_cognition/agent.py:88` `aegis_cognition.agent:__repr__` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:23` `aegis_cognition.application:__init__` → `PROVIDER_CALL`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:23` `aegis_cognition.application:__init__` → `SECRET_ACCESS`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:23` `aegis_cognition.application:__init__` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/application.py:35` `aegis_cognition.application:prepare` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:35` `aegis_cognition.application:prepare` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:35` `aegis_cognition.application:prepare` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:43` `aegis_cognition.application:_retrieve_context` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:43` `aegis_cognition.application:_retrieve_context` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:43` `aegis_cognition.application:_retrieve_context` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:51` `aegis_cognition.application:_build_system_context` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:51` `aegis_cognition.application:_build_system_context` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:51` `aegis_cognition.application:_build_system_context` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:73` `aegis_cognition.application:_gateway` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run
- `aegis_cognition/application.py:73` `aegis_cognition.application:_gateway` → `SECRET_ACCESS`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run
- `aegis_cognition/application.py:73` `aegis_cognition.application:_gateway` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun,aegis_cognition.lab:run
- `aegis_cognition/application.py:89` `aegis_cognition.application:arun` → `PROVIDER_CALL`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:89` `aegis_cognition.application:arun` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:159` `aegis_cognition.application:_lab_enabled` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/application.py:173` `aegis_cognition.application:run` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:run
- `aegis_cognition/application.py:183` `aegis_cognition.application:_index_completed_run` → `EXTERNAL_WRITE`; roots=aegis_cognition.application:arun
- `aegis_cognition/benchmark.py:22` `aegis_cognition.benchmark:_digest` → `FILESYSTEM_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:22` `aegis_cognition.benchmark:_digest` → `PROCESS_SPAWN`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:22` `aegis_cognition.benchmark:_digest` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:140` `aegis_cognition.benchmark:validate` → `FILESYSTEM_WRITE`; roots=aegis_cognition.agent:run,aegis_cognition.application:arun,aegis_cognition.application:run,aegis_cognition.lab:run,core.python.aegis_cli:run_agent,scripts.run_checks:_blake3_hex_with_rust,scripts.run_checks:_cached_or_generate_cli_report,scripts.run_checks:ensure_fresh_benchmarks
- `aegis_cognition/benchmark.py:140` `aegis_cognition.benchmark:validate` → `PROCESS_SPAWN`; roots=aegis_cognition.agent:run,aegis_cognition.application:arun,aegis_cognition.application:run,aegis_cognition.lab:run,core.python.aegis_cli:run_agent,scripts.run_checks:_blake3_hex_with_rust,scripts.run_checks:_cached_or_generate_cli_report,scripts.run_checks:ensure_fresh_benchmarks
- `aegis_cognition/benchmark.py:140` `aegis_cognition.benchmark:validate` → `EXTERNAL_WRITE`; roots=aegis_cognition.agent:run,aegis_cognition.application:arun,aegis_cognition.application:run,aegis_cognition.lab:run,core.python.aegis_cli:run_agent,scripts.run_checks:_blake3_hex_with_rust,scripts.run_checks:_cached_or_generate_cli_report,scripts.run_checks:ensure_fresh_benchmarks
- `aegis_cognition/benchmark.py:63` `aegis_cognition.benchmark:protocol_hash` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:63` `aegis_cognition.benchmark:protocol_hash` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:63` `aegis_cognition.benchmark:protocol_hash` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:84` `aegis_cognition.benchmark:capture` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:84` `aegis_cognition.benchmark:capture` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:84` `aegis_cognition.benchmark:capture` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:123` `aegis_cognition.benchmark:environment_hash` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:123` `aegis_cognition.benchmark:environment_hash` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:123` `aegis_cognition.benchmark:environment_hash` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:152` `aegis_cognition.benchmark:key` → `FILESYSTEM_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:152` `aegis_cognition.benchmark:key` → `PROCESS_SPAWN`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:152` `aegis_cognition.benchmark:key` → `EXTERNAL_WRITE`; roots=UNKNOWN_DYNAMIC
- `aegis_cognition/benchmark.py:179` `aegis_cognition.benchmark:_run_isolated_validator` → `FILESYSTEM_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:179` `aegis_cognition.benchmark:_run_isolated_validator` → `PROCESS_SPAWN`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:179` `aegis_cognition.benchmark:_run_isolated_validator` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:282` `aegis_cognition.benchmark:_normalize_trial_records` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/benchmark.py:344` `aegis_cognition.benchmark:evaluate_benchmark` → `EXTERNAL_WRITE`; roots=aegis_cognition.lab:run
- `aegis_cognition/cli.py:30` `aegis_cognition.cli:main` → `FILESYSTEM_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:30` `aegis_cognition.cli:main` → `BROWSER`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:30` `aegis_cognition.cli:main` → `PROVIDER_CALL`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:30` `aegis_cognition.cli:main` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:30` `aegis_cognition.cli:main` → `EXTERNAL_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:55` `aegis_cognition.cli:_print_help` → `FILESYSTEM_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:55` `aegis_cognition.cli:_print_help` → `BROWSER`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:55` `aegis_cognition.cli:_print_help` → `PROVIDER_CALL`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:55` `aegis_cognition.cli:_print_help` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:55` `aegis_cognition.cli:_print_help` → `EXTERNAL_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:82` `aegis_cognition.cli:_cmd_init` → `FILESYSTEM_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:82` `aegis_cognition.cli:_cmd_init` → `BROWSER`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:82` `aegis_cognition.cli:_cmd_init` → `PROVIDER_CALL`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:82` `aegis_cognition.cli:_cmd_init` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:82` `aegis_cognition.cli:_cmd_init` → `EXTERNAL_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:154` `aegis_cognition.cli:_cmd_run` → `BROWSER`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:154` `aegis_cognition.cli:_cmd_run` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:154` `aegis_cognition.cli:_cmd_run` → `EXTERNAL_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:188` `aegis_cognition.cli:_cmd_examples` → `BROWSER`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:188` `aegis_cognition.cli:_cmd_examples` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:188` `aegis_cognition.cli:_cmd_examples` → `EXTERNAL_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:213` `aegis_cognition.cli:_cmd_version` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:213` `aegis_cognition.cli:_cmd_version` → `EXTERNAL_WRITE`; roots=aegis_cognition.cli:main
- `aegis_cognition/cli.py:220` `aegis_cognition.cli:_cmd_config` → `SECRET_ACCESS`; roots=aegis_cognition.cli:main

Rust side-effect sites and all effect classes are in `architecture_side_effect_graph.json`. The requested chain `ENTRYPOINT → CALLERS → ADMISSION → CAPABILITY → BUDGET → LEASE → EFFECT → SETTLEMENT → EVIDENCE` cannot be mechanically proven complete from static source alone. Records therefore flag missing stages as **UNKNOWN/REQUIRES CONTRACT REVIEW**, not as a proven absence. Hidden adapter effects, descendants, macro calls, and external service effects remain outside static completeness.

## 9. Retry / timeout / cancellation graph

Fresh pattern inventory produced `1720` records. Representative locations:

- `aegis_cognition/__init__.py:44` `"ReplayWriterLease",`
- `aegis_cognition/__init__.py:61` `"finish_runtime_lease",`
- `aegis_cognition/__init__.py:99` `ReplayWriterLease,`
- `aegis_cognition/__init__.py:117` `finish_runtime_lease,`
- `aegis_cognition/benchmark.py:19` `_TRIAL_STATUSES = frozenset({"SUCCESS", "FAIL", "TIMEOUT", "REFUSAL", "CANCELLED", "ERROR"})`
- `aegis_cognition/benchmark.py:73` `os_release: str`
- `aegis_cognition/benchmark.py:96` `os_release=platform.release() or "unknown",`
- `aegis_cognition/benchmark.py:110` `self.os_release,`
- `aegis_cognition/benchmark.py:185` `timeout_seconds: float,`
- `aegis_cognition/benchmark.py:204` `if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:`
- `aegis_cognition/benchmark.py:205` `raise ValueError("hidden validator timeout must be finite and positive")`
- `aegis_cognition/benchmark.py:237` `stdout, stderr = process.communicate(payload, timeout=float(timeout_seconds))`
- `aegis_cognition/benchmark.py:238` `except subprocess.TimeoutExpired as exc:`
- `aegis_cognition/benchmark.py:242` `with contextlib.suppress(subprocess.TimeoutExpired):`
- `aegis_cognition/benchmark.py:243` `process.communicate(timeout=1.0)`
- `aegis_cognition/benchmark.py:244` `raise TimeoutError("hidden validator timed out") from exc`
- `aegis_cognition/benchmark.py:356` `validator_timeout_seconds: float = 5.0,`
- `aegis_cognition/benchmark.py:425` `timeout_seconds=validator_timeout_seconds,`
- `aegis_cognition/benchmark.py:430` `except (RuntimeError, TimeoutError, ValueError) as exc:`
- `aegis_cognition/errors.py:43` `"""Provider failure with user-facing retry guidance."""`
- `aegis_cognition/errors.py:181` `If this persists, please file an issue:`
- `aegis_cognition/errors.py:234` `If it didn't, wait a few seconds and retry.`
- `aegis_cognition/errors.py:253` `1. Wait 30 seconds and retry`
- `aegis_cognition/errors.py:272` `Please file an issue with this error message:`
- `aegis_cognition/errors.py:288` `Please file an issue:`
- `aegis_cognition/lab.py:170` `"cancellation_admitted": "CancellationAdmitted",`
- `aegis_cognition/lab.py:171` `"cancellation_recorded": "CancellationRecorded",`
- `aegis_cognition/lab.py:368` `timeout_seconds: float = 10.0,`
- `aegis_cognition/lab.py:372` `if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):`
- `aegis_cognition/lab.py:373` `raise ValueError("search executor timeout must be positive and finite")`
- `aegis_cognition/lab.py:379` `self.timeout_seconds = timeout_seconds`
- `aegis_cognition/lab.py:494` `with urlopen(request, timeout=self.timeout_seconds) as response:`
- `aegis_cognition/lab.py:1231` `"""Invoke an edge adapter without accepting a swallowed cancellation.`
- `aegis_cognition/lab.py:1233` `Python tasks retain a positive ``cancelling()`` count even when an`
- `aegis_cognition/lab.py:1234` `adapter catches ``CancelledError`` and returns a value.  Checking before`
- `aegis_cognition/lab.py:1235` `and after the adapter turns that ambiguous return into a cancellation so`
- `aegis_cognition/lab.py:1240` `if task is not None and task.cancelling():`
- `aegis_cognition/lab.py:1241` `raise asyncio.CancelledError`
- `aegis_cognition/lab.py:1244` `if task is not None and task.cancelling():`
- `aegis_cognition/lab.py:1245` `raise asyncio.CancelledError`
- `aegis_cognition/lab.py:1697` `async def wait_for_timeout(self, milliseconds: int) -> None:`
- `aegis_cognition/lab.py:1698` `method = getattr(self._session, "wait_for_timeout", None)`
- `aegis_cognition/lab.py:1707` `_SKILL_STATUSES = frozenset({"SUCCESS", "REJECTED", "CANCELLED"})`
- `aegis_cognition/lab.py:1878` `"""Bounded Python registry; Rust remains the release authority."""`
- `aegis_cognition/lab.py:2018` `self._lease_id = 0`
- `aegis_cognition/lab.py:2022` `def lease_id(self) -> int:`
- `aegis_cognition/lab.py:2023` `return self._lease_id`
- `aegis_cognition/lab.py:2030` `"""Return a read-only view bound to the current actor lease."""`
- `aegis_cognition/lab.py:2057` `"""Open one browser session and bind it to this cell's lease."""`
- `aegis_cognition/lab.py:2074` `self._lease_id += 1`
- `aegis_cognition/lab.py:2088` `self._lease_id += 1`
- `aegis_cognition/lab.py:2091` `async def release(self) -> None:`
- `aegis_cognition/lab.py:2092` `"""Close the owned session; repeated release is idempotent."""`
- `aegis_cognition/lab.py:2106` `await self.release()`
- `aegis_cognition/lab.py:2118` `"""Mark the cell closed; async session cleanup still uses ``release``."""`
- `aegis_cognition/lab.py:2234` `method = getattr(browser_session, "wait_for_timeout", None)`
- `aegis_cognition/lab.py:2270` `wait_for_timeout = getattr(browser_session, "wait_for_timeout", None)`
- `aegis_cognition/lab.py:2271` `if wait_for_timeout is None:`
- `aegis_cognition/lab.py:2273` `return await _call(wait_for_timeout, milliseconds)`
- `aegis_cognition/lab.py:2591` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:2650` ```attempt`` is the replay-visible retry fence.  A later attempt must`
- `aegis_cognition/lab.py:2655` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:2702` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:2714` `if normalized_status not in {"SUCCESS", "REJECTED", "CANCELLED"}:`
- `aegis_cognition/lab.py:2767` `lease_id: int = 1,`
- `aegis_cognition/lab.py:2778` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:2791` `or type(lease_id) is not int`
- `aegis_cognition/lab.py:2792` `or lease_id < 1`
- `aegis_cognition/lab.py:2809` `"lease_id": lease_id,`
- `aegis_cognition/lab.py:2839` `lease_id: int = 1,`
- `aegis_cognition/lab.py:2854` `if self.state == "completed" or (self.state == "aborted" and normalized_status != "CANCELLED"):`
- `aegis_cognition/lab.py:2856` `if normalized_status not in {"SUCCESS", "REJECTED", "TIMED_OUT", "CANCELLED"}:`
- `aegis_cognition/lab.py:2871` `"lease_id": lease_id,`
- `aegis_cognition/lab.py:2900` `def admit_cancellation(self, *, reason: str, request_id: str | None = None) -> tuple[str, str]:`
- `aegis_cognition/lab.py:2901` `"""Admit a cancellation request before changing run state."""`
- `aegis_cognition/lab.py:2903` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:2907` `raise ValueError("cancellation reason must be non-empty")`
- `aegis_cognition/lab.py:2908` `ordinal = sum(event.kind == "cancellation_admitted" for event in self.events) + 1`
- `aegis_cognition/lab.py:2909` `normalized_request_id = request_id or f"cancel-{ordinal}"`
- `aegis_cognition/lab.py:2911` `raise ValueError("cancellation request identity must be non-empty")`
- `aegis_cognition/lab.py:2916` `"cancellation_admitted",`
- `aegis_cognition/lab.py:2924` `"policy_hash": _hash({"schema": "aegis-cancellation-policy-v1"}),`
- `aegis_cognition/lab.py:2931` `def record_cancellation(`
- `aegis_cognition/lab.py:2940` `"""Settle a cancellation request after the state transition attempt."""`
- `aegis_cognition/lab.py:2943` `raise ValueError("cancellation settlement identity is invalid")`
- `aegis_cognition/lab.py:2946` `raise ValueError("invalid cancellation status")`
- `aegis_cognition/lab.py:2950` `"cancellation_recorded",`
- `aegis_cognition/lab.py:2959` `"schema": "aegis-cancellation-result-v1",`
- `aegis_cognition/lab.py:2978` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3021` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3031` `if normalized_status not in {"SUCCESS", "REJECTED", "CANCELLED"}:`
- `aegis_cognition/lab.py:3080` `lease_id: int,`
- `aegis_cognition/lab.py:3085` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3090` `if lease_id < 1 or not action_id.strip():`
- `aegis_cognition/lab.py:3091` `raise ValueError("browser action lease and identity must be valid")`
- `aegis_cognition/lab.py:3100` `"lease_id": lease_id,`
- `aegis_cognition/lab.py:3122` `lease_id: int,`
- `aegis_cognition/lab.py:3129` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3135` `if normalized_status not in {"SUCCESS", "REJECTED", "CANCELLED"}:`
- `aegis_cognition/lab.py:3137` `if lease_id < 1 or not action_id.strip() or not admission_id.strip():`
- `aegis_cognition/lab.py:3152` `"lease_id": lease_id,`
- `aegis_cognition/lab.py:3175` `lease_id: int,`
- `aegis_cognition/lab.py:3180` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3185` `if lease_id < 1 or observation_count < 1:`
- `aegis_cognition/lab.py:3186` `raise ValueError("browser observation lease and count must be positive")`
- `aegis_cognition/lab.py:3188` `f"lease-{lease_id}-observation-"`
- `aegis_cognition/lab.py:3199` `"lease_id": lease_id,`
- `aegis_cognition/lab.py:3220` `lease_id: int,`
- `aegis_cognition/lab.py:3230` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3235` `if lease_id < 1 or observation_count < 1:`
- `aegis_cognition/lab.py:3236` `raise ValueError("browser observation lease and count must be positive")`
- `aegis_cognition/lab.py:3238` `if normalized_status not in {"SUCCESS", "REJECTED", "CANCELLED"}:`
- `aegis_cognition/lab.py:3245` `lease_id=lease_id,`
- `aegis_cognition/lab.py:3263` `"lease_id": lease_id,`
- `aegis_cognition/lab.py:3286` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3306` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3322` `if self.state in {"completed", "aborted"}:`
- `aegis_cognition/lab.py:3533` `but a loaded native verifier is never advisory: a rejection aborts the`
- `aegis_cognition/lab.py:3579` `"planned": {"researching", "blocked", "aborted"},`
- `aegis_cognition/lab.py:3580` `"researching": {"experimenting", "reviewing", "blocked", "aborted"},`

The JSON separates retry count/backoff/idempotency/budget/attempt/cancellation fields as unknown unless directly visible. Duplicated candidate locations include provider, adapter, Lab, replay, and execution paths; no exactly-once or system-wide retry-budget claim is made.

## 10. Trust / policy graph

Fresh policy scan found `1492` records and `480` default/fallback candidates.

- `aegis_cognition/application.py:77` `for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")`
- `aegis_cognition/errors.py:233` `The task may have completed successfully using a fallback provider.`
- `aegis_cognition/errors.py:238` `2. Add more fallback providers:`
- `aegis_cognition/errors.py:239` `agent = Agent(task="...", fallback_providers=["anthropic", "openrouter"])`
- `aegis_cognition/lab.py:4888` `trust_level: str = "DEV"`
- `aegis_cognition/lab.py:4928` `"""Require Rust admission by default for production trust levels."""`
- `aegis_cognition/lab.py:4932` `return self.trust_level.strip().upper() == "PROD"`
- `aegis_cognition/lab.py:5287` `trust_level: str = "DEV",`
- `aegis_cognition/lab.py:5585` `str(self.config.trust_level).strip().upper() == "PROD",`
- `aegis_cognition/lab.py:5900` `for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")`
- `aegis_cognition/lab.py:5906` `str(self.config.trust_level).strip().upper() == "PROD",`
- `aegis_cognition/lab.py:5948` `logical model call, while these nested receipts make fallback routing`
- `aegis_cognition/lab.py:5997` `"configured_fallback_providers_hash": _hash(`
- `aegis_cognition/lab.py:5998` `self.config.options.get("fallback_providers", ())`
- `aegis_cognition/lab.py:7099` `"configured_fallback_providers_hash": _hash(`
- `aegis_cognition/lab.py:7100` `self.config.options.get("fallback_providers", ())`
- `aegis_cognition/lab.py:7195` `"provider_fallback_used": bool(getattr(route, "fallback_used", False)),`
- `aegis_cognition/lab.py:7223` `str(self.config.trust_level).strip().upper() == "PROD"`
- `aegis_cognition/lab.py:7489` `str(self.config.trust_level).strip().upper() == "PROD",`
- `aegis_cognition/prompt.py:17` `trust_level: str = "DEV",`
- `aegis_cognition/prompt.py:25` `# Set intelligent default security level based on trust_level & task_type`
- `aegis_cognition/prompt.py:29` `if self.trust_level == "PROD":`
- `aegis_cognition/prompt.py:38` `if self.trust_level == "PROD" and "Yêu cầu bằng chứng vật lý" not in self.constraints:`
- `aegis_cognition/prompt.py:40` `if self.trust_level == "PROD" and "Xác thực qua browser witness" not in self.constraints:`
- `aegis_cognition/runtime.py:45` `"source": "python-fallback",`
- `aegis_cognition/runtime.py:61` `"os": {"backend": "python-fallback", "enforcement": "unsupported"},`
- `aegis_cognition/runtime.py:76` `"""Return Rust lane limits, or a conservative unverified fallback."""`
- `AEGIS_DX_RESEARCH_REPORT.md:13` `- **Fallback**: If the terminal is not interactive (`sys.stdin.isatty() == False`), silently fall back to `getpass.getpass()`.`
- `AEGIS_DX_RESEARCH_REPORT.md:46` `- **Rate Limit Handling**: Utilize the `aegis_adapter.py` token bucket and `ProviderRateLimitError` to automatically trigger the fallback chain.`
- `core/python/aegis/contracts.py:99` `fallback_used: bool`
- `core/python/aegis/evidence.py:27` `physical_witness_required = level == "PROD"`
- `core/python/aegis/evidence.py:28` `fail_closed = level == "PROD"`
- `core/python/aegis/evidence.py:29` `degraded_hot_evidence_allowed = level == "DEV"`
- `core/python/aegis/evidence.py:30` `rust_extension_required = level != "DEV"`
- `core/python/aegis/evidence.py:31` `dual_approval_required = level == "PROD"`
- `core/python/aegis/evidence.py:67` `if level != "DEV":`
- `core/python/aegis/evidence.py:76` `verifier="python-dev-fallback",`
- `core/python/aegis/evidence.py:99` `if level != "DEV":`
- `core/python/aegis/evidence.py:123` `"verifier": "python-dev-batch-fallback",`
- `core/python/aegis/evidence.py:138` `"verifier": "python-dev-batch-fallback",`
- `core/python/aegis/native.py:13` `# exercise the no-extension fallback even when an editable maturin build`
- `core/python/aegis/provider.py:26` `def normalize_fallback_providers(fallback_providers: Any) -> tuple[tuple[str | None, Any], ...]:`
- `core/python/aegis/provider.py:27` `if not fallback_providers:`
- `core/python/aegis/provider.py:30` `for item in fallback_providers:`
- `core/python/aegis/provider.py:73` `def task_from_runnable_input(value: Any, fallback_task: str | None = None) -> str:`
- `core/python/aegis/provider.py:75` `if fallback_task:`
- `core/python/aegis/provider.py:76` `return fallback_task`
- `core/python/aegis/provider.py:86` `return task_from_runnable_input(nested, fallback_task)`
- `core/python/aegis/provider.py:179` `fallback_providers: tuple[tuple[str | None, Any], ...],`
- `core/python/aegis/provider.py:190` `*fallback_providers,`
- `core/python/aegis/provider.py:271` `fallback_used=index > 0,`
- `core/python/aegis/provider.py:324` `fallback_used: bool,`
- `core/python/aegis/provider.py:327` `downgraded_model = fallback_used`
- `core/python/aegis/provider.py:335` `"fallback_used": fallback_used,`
- `core/python/aegis/provider.py:347` `fallback_used=fallback_used,`
- `core/python/aegis_adapter.py:47` `normalize_fallback_providers as _normalize_fallback_providers,`
- `core/python/aegis_adapter.py:74` `normalize_fallback_providers as _normalize_fallback_providers,`
- `core/python/aegis_adapter.py:93` `fallback_providers: Any = None,`
- `core/python/aegis_adapter.py:107` `self.fallback_providers = _normalize_fallback_providers(fallback_providers)`
- `core/python/aegis_adapter.py:147` `self.fallback_providers,`
- `core/python/aegis_adapter.py:158` `"fallback_succeeded" if provider_route.fallback_used else "request_succeeded",`
- `core/python/aegis_adapter.py:480` `"provider_fallback_used": provider_route.fallback_used,`
- `core/python/aegis_cli.py:113` `trust_level = "PROD"`
- `core/python/aegis_cli.py:114` `print("Defaulting to PROD.")`
- `core/python/browser_runtime_adapter.py:293` `verifier="rust-hot-engine-or-python-dev-fallback",`
- `core/python/orchestrator.py:61` `# Simple regex fallback to strip implementation bodies`
- `core/python/tests.py:41` `from scripts.dynamic_provider_fallback_gate import evaluate_dynamic_provider_fallback_gate, write_live_provider_429_soak_capture`
- `core/python/tests.py:534` `patch("scripts.e2e_release_gate.evaluate_dynamic_provider_fallback_gate", return_value={`
- `core/python/tests.py:537` `"dynamic_provider_fallback_evidence": {`
- `core/python/tests.py:538` `"fallback_used": True,`
- `core/python/tests.py:814` `assert result.hot_commit.verifier == "python-dev-fallback"`
- `core/python/tests.py:816` `assert result.hot_commit.trust_level == "DEV"`
- `core/python/tests.py:820` `assert result.trust_policy.trust_level == "DEV"`
- `core/python/tests.py:862` `assert agent.last_result.hot_commit.verifier == "python-dev-fallback"`
- `core/python/tests.py:923` `def _write_dynamic_provider_fallback_fixture(root: Path) -> None:`
- `core/python/tests.py:940` `"fallback_provider": "nim",`
- `core/python/tests.py:941` `"fallback_model": "llama-3-70b",`
- `core/python/tests.py:946` `"schema": "aegis-dynamic-provider-fallback-report-v1",`
- `core/python/tests.py:954` `"fallback_used": True,`
- `core/python/tests.py:960` `"fallback_proof_hash": _sha256_array("fallback-proof"),`
- `core/python/tests.py:971` `(artifacts / "dynamic_provider_fallback_report.json").write_text(`
- `core/python/tests.py:1408` `def test_aegis_adapter_dev_provider_fallback_records_route_evidence():`
- `core/python/tests.py:1426` `fallback_providers=[("nim/llama-3-70b", reserve)],`
- `core/python/tests.py:1437` `assert result.provider_route.fallback_used is True`
- `core/python/tests.py:1443` `assert result.hot_commit.trust_level == "DEV"`
- `core/python/tests.py:1449` `_write_dynamic_provider_fallback_fixture(root)`
- `core/python/tests.py:1475` `gate = evaluate_dynamic_provider_fallback_gate(root)`
- `core/python/tests.py:1502` `_write_dynamic_provider_fallback_fixture(root)`
- `core/python/tests.py:1514` `gate = evaluate_dynamic_provider_fallback_gate(root)`
- `core/python/tests.py:1546` `fallback_providers=[("nim/llama-3-70b", reserve)],`
- `core/python/tests.py:1584` `assert result.provider_route.fallback_used is True`
- `core/python/tests.py:1602` `fallback_providers=[("nim/llama-3-70b", throttled)],`
- `core/python/tests.py:1626` `"verifier": "python-dev-fallback",`
- `core/python/tests.py:1645` `assert result.trust_level == "DEV"`
- `core/python/tests.py:1658` `assert all(commit.trust_level == "DEV" for commit in result.hot_evidence.commits)`
- `core/python/tests.py:1680` `assert result.hot_evidence.verifier == "python-dev-batch-fallback"`
- `core/python/tests.py:1686` `assert all(commit.verifier == "python-dev-batch-fallback" for commit in result.hot_evidence.commits)`
- `core/python/tests.py:1745` `assert result.trust_level == "DEV"`
- `core/python/tests.py:1751` `assert result.hot_evidence.verifier == "python-dev-batch-fallback"`
- `core/python/tests.py:2138` `"evidence_artifact": "dynamic_provider_fallback_gate_report.json",`

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

The nested `core/rust/AEGIS-COGNITION` mirror is an additional concrete duplicate/ownership candidate, distinct from normal Python/Rust projection duplication.

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
