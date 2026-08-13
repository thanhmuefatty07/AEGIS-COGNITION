# Execution Plan — AEGIS-COGNITION POC Graduation

## Goal
Graduate `semantic_cache_poc`, `tool_batching_poc`, and `code_orchestration_poc` into production-grade modules under `core/rust/src/eac/` in the `aegis-nerve` crate, integrate them with PyO3 bindings, and hook them up to the `EventBus` for replay determinism.

## Milestones & Status
1. **Decomposition & Setup** [In Progress]
   - Define codebase layout, modules, and cross-module interface contracts.
   - Initialize `PROJECT.md` at the root.
2. **Exploration & Research** [Pending]
   - Spawn explorer(s) to verify details of the three POCs, existing sandbox mechanisms, PyO3 bindings structure, and EventBus/Replay systems.
3. **Milestone 1: Semantic Cache Integration** [Pending]
   - Implement semantic caching in `core/rust/src/eac/cache/semantic.rs`.
   - Incorporate L1 BLAKE3 exact match and L2 Cosine Similarity.
   - Implement configurable threshold (default 0.95) and namespace invalidation linked to changes in the active `evidence_binding_hash`.
4. **Milestone 2: Tool Call Batching & Coalescing** [Pending]
   - Implement batch request handler and transactional recovery policies (`HaltOnFailure`, `ContinueOnFailure`) in `core/rust/src/eac/sandbox/batch.rs`.
   - Integrate with the compiler and Skill Blueprint DSL.
5. **Milestone 3: Code-First Sandbox & Filesystem Serde** [Pending]
   - Implement execution sandbox for search/retrieval code in `core/rust/src/eac/sandbox/runtime.rs`.
   - Implement `FilesystemSerde` API for atomic filesystem writes and BLAKE3-hashed turn state tracking.
   - Add path traversal and command injection blocks to prevent sandbox escape.
6. **Milestone 4: PyO3 Bindings & Client Integration** [Pending]
   - Export `SemanticCache`, `batch()`, and `persist_state()` from PyO3 module.
7. **Milestone 5: Event Sourcing & Determinism** [Pending]
   - Emit cache hits, batch executions, and state serialization events to the `EventBus` to preserve trajectory replay determinism under the `ReplayValidator`.
8. **Milestone 6: Verification & Hardening** [Pending]
   - Verify performance requirements via Criterion benchmarks:
     - `semantic_cache_lookup_p95` < 50 us
     - `batch_executor_overhead_p95` < 200 us
     - `filesystem_serde_persist_p95` < 10 ms
   - Verify security rules and integration tests (at least 3 tests, path traversal blocks).
   - Perform integrity audit check using `Forensic Auditor`.
