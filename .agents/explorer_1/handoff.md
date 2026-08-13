# Handoff Report — POC Graduation Investigation

## 1. Observation
The following file locations, structures, and systems have been directly observed in the `AEGIS-COGNITION` workspace:

### 1.1 Core Ledger & Event System
- **No EventBus Component**: There is no dedicated class or module named `EventBus`. The system uses `RunEventLedger` (`core/rust/src/replay.rs:120-138`) and `SegmentedArrowAuditStream` (`replay.rs:146-170`) to handle event serialization, hash-chain auditing, and replay determinism.
- **RunEvent Struct**: Chained via BLAKE3 hashes (`replay.rs:68-77`):
  ```rust
  pub struct RunEvent {
      pub event_id: EventId,
      pub run_id: RunId,
      pub kind: RunEventKind,
      pub subject_id: SubjectId,
      pub primary_hash: [u8; 32],
      pub secondary_hash: Option<[u8; 32]>,
      pub previous_event_hash: [u8; 32],
      pub event_hash: [u8; 32],
  }
  ```
- **Serialization**: Record batches are constructed column-by-column in `batch_from_events` (`replay.rs:2201-2250`) using Arrow data types (e.g. `UInt64`, `UInt8`, `Binary`).

### 1.2 PyO3 Bindings
- **FFI Registration**: Functions are registered inside `aegis_nerve` module in `core/rust/src/ffi.rs` (lines 230-262):
  ```rust
  #[pymodule]
  pub fn aegis_nerve(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
      m.add_function(wrap_pyfunction!(aegis_status, m)?)?;
      ...
  }
  ```
  No PyO3 classes are currently registered in `ffi.rs`.

### 1.3 Sandboxing & Execution Paths
- **Wasmtime Sandbox**: Exposes `execute_wasm_binary`, `execute_mmap_wasm_bridge_frame`, and `execute_quickjs_invocation_bridge` in `core/rust/src/sandbox.rs:278-590` with caching for modules (`module_cache`) and pre-instantiations (`wasm_pre_cache`).

### 1.4 Workspace Verification Utilities
- **Tests**: Located in `core/rust/src/tests.rs` (Cargo suite) and `core/python/tests.py` (PyTest suite).
- **Benchmarks**: Benchmarking is handled via Criterion in `core/rust/benches/nerve_bench.rs` and check gates in `scripts/run_checks.py`.

---

## 2. Logic Chain
1. **Target Graduation Paths**: Since the `core/rust/src/eac/` directory does not yet exist, we must introduce:
   - `core/rust/src/eac/cache/semantic.rs` (For R1: Semantic Cache)
   - `core/rust/src/eac/sandbox/batch.rs` (For R2: Tool Batching)
   - `core/rust/src/eac/sandbox/runtime.rs` (For R3: Sandbox runtime & FilesystemSerde)
2. **PyO3 Exposing (R4)**: To allow the python bridge/client to access these, `ffi.rs` must register the `SemanticCache` class via `m.add_class::<SemanticCache>()?` and expose `batch` and `persist_state` functions, wrapping the EAC Rust primitives.
3. **Sandbox Integration (C3)**:
   - **Batching**: The Skill Blueprint DSL compiler/gateway must replace isolated wasm executes with `ToolBatcher` calls, executing sequential halt-on-failure or parallel continue-on-failure workflows.
   - **Sandbox Runtime**: Command streams must be checked against traversing inputs (`../etc/passwd`) and command executions (`rm -rf`) in `runtime.rs` prior to spawning isolated guest runs consuming `fuel_limit` budgets.
   - **FilesystemSerde**: Atomic persistence must be utilized by serializing to `.tmp` files, syncing, and renaming to prevent half-write corruptions.
4. **Trajectory Replay Determinism (R5)**: Since `RunEventKind` gates trajectory validation, we must add `SemanticCacheHit`, `ToolBatchExecuted`, and `TurnStatePersisted` to the enum and emit them to the `SegmentedArrowAuditStream` whenever cache hits, batch runs, or persistence cycles execute.

---

## 3. Caveats
- **Read-Only Restrictions**: This investigation was strictly read-only. No directories were created under `core/rust/src/eac/` and no code files were modified.
- **Wasmtime/Arrow Assumptions**: Benchmark gates assume that Arrow segments are written in a single-writer append-only configuration and verified through mmap-backed column scans. Any concurrent write access could break integrity.

---

## 4. Conclusion
The codebase is structurally prepared for graduating the POCs. The integration boundaries have been clearly mapped out to the new `core/rust/src/eac/` layout, FFI registration protocols have been detailed, and a robust verification strategy has been established.

---

## 5. Verification Method
Verify the core workspace build and tests by running:
1. **Rust Cargo Tests**:
   ```powershell
   cargo test --manifest-path core/rust/Cargo.toml
   ```
2. **Python Bridge Tests**:
   ```powershell
   pytest core/python/tests.py
   ```
3. **Criterion Benchmarks & Gates**:
   ```powershell
   python scripts/run_checks.py
   ```
   *Verify performance estimates satisfy:*
   - `semantic_cache_lookup_p95` < 50 us
   - `batch_executor_overhead_p95` < 200 us
   - `filesystem_serde_persist_p95` < 10 ms
