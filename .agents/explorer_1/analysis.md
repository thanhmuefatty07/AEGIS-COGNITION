# Technical Analysis Report — POC Graduation to Core

## Executive Summary
This report analyzes the integration requirements for graduating the three prototype-of-concept components (`semantic_cache_poc`, `tool_batching_poc`, and `code_orchestration_poc`) into production-grade implementations under the core Rust library of the `aegis-nerve` crate at `core/rust/src/eac/`. It outlines the existing Event Sourcing/Replay mechanisms, FFI PyO3 registration processes, Wasmtime sandboxing execution paths, detailed mappings for both baseline and follow-up technical requirements (R1-R5), and a validation and verification strategy.

---

## 1. Event Sourcing & Trajectory Replay Determinism (C1)

### 1.1 The Ledger Architecture
There is no traditional in-memory broadcast `EventBus` class. Trajectory replay determinism is enforced via a hash-chained, event-sourcing ledger defined in `core/rust/src/replay.rs`.

*   **`RunEvent`**: The fundamental unit of the ledger (`replay.rs:68-77`).
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
*   **`RunEventLedger`**: Tracks sequential event transition checks (`replay.rs:120-138`).
*   **`SegmentedArrowAuditStream`**: The persistence layer (`replay.rs:146-170`), writing events into memory-mapped, append-only Apache Arrow IPC streams (`.arrow` segments) with parallel sidecar verification files (`.commit`).

### 1.2 Hash Chain & Event Serialization
Every event chains itself to the preceding event:
$$\text{event\_hash} = \text{BLAKE3}(\text{event\_id} \mathbin{\Vert} \text{run\_id} \mathbin{\Vert} \text{kind} \mathbin{\Vert} \text{subject\_id} \mathbin{\Vert} \text{primary\_hash} \mathbin{\Vert} \text{secondary\_hash} \mathbin{\Vert} \text{previous\_event\_hash})$$
This is calculated via `compute_run_event_hash` in `replay.rs:1315-1323`. 
The serialization into Arrow columns occurs in `batch_from_events` (`replay.rs:2201-2250`):
- `event_id` $\to$ `DataType::UInt64`
- `run_id` $\to$ Split into `run_id_hi` (`UInt64`) and `run_id_lo` (`UInt64`)
- `kind` $\to$ `DataType::UInt8` (representing `RunEventKind` variants as integers)
- `subject_id` $\to$ Split into `subject_id_hi` (`UInt64`) and `subject_id_lo` (`UInt64`)
- `primary_hash` $\to$ `DataType::Binary`
- `secondary_hash` $\to$ `DataType::Binary` (nullable)
- `previous_event_hash` $\to$ `DataType::Binary`
- `event_hash` $\to$ `DataType::Binary`

### 1.3 Graduation Requirement for Event Sourcing
To satisfy **R5 (Event Sourcing & Determinism)**, three new variants must be appended to the `RunEventKind` enum in `core/rust/src/replay.rs:43-65` and its FFI decoder helper:
1.  `SemanticCacheHit`
2.  `ToolBatchExecuted`
3.  `TurnStatePersisted`

Caches, batchers, and serialization modules must query the ledger or append corresponding events at runtime to keep trajectories deterministic under `ReplayValidator` audits.

---

## 2. PyO3 Module `aegis_nerve` Binding & FFI Registrations (C2)

The `aegis_nerve` module is registered inside `core/rust/src/ffi.rs` using the `#[pymodule]` macro:
```rust
#[pymodule]
pub fn aegis_nerve(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(aegis_status, m)?)?;
    ...
    Ok(())
}
```
Currently, the module exposes global utility functions (e.g., `aegis_status`, `aegis_validate_schema`, `aegis_llm_request`, `aegis_execute_mmap_wasm_bridge_frame`) but registers no classes.

### 2.2 Proposed Classes and Functions for Graduation
To implement **R4 (PyO3 Bindings & Client Integration)**, the PyO3 layer must be modified to register classes and functions bridging the Rust-level EAC implementation:

1.  **`SemanticCache` Class**:
    ```rust
    #[pyclass]
    pub struct PySemanticCache {
        pub inner: Arc<Mutex<crate::eac::cache::SemanticCache>>,
    }

    #[pymethods]
    impl PySemanticCache {
        #[new]
        #[pyo3(signature = (threshold = 0.95, file_path = None))]
        fn new(threshold: f32, file_path: Option<String>) -> Self { ... }

        fn lookup(&self, prompt: &str, embedding: Vec<f32>, namespace: &str) -> PyResult<Option<String>> { ... }
        fn insert(&mut self, prompt: String, response: String, embedding: Vec<f32>, namespace: String) -> PyResult<()> { ... }
        fn invalidate(&mut self, namespace: &str) -> PyResult<()> { ... }
    }
    ```
2.  **`batch()` Function**:
    ```rust
    #[pyclass]
    #[derive(Clone)]
    pub struct PyToolCall {
        #[pyo3(get, set)]
        pub call_id: u32,
        #[pyo3(get, set)]
        pub tool_name: String,
        #[pyo3(get, set)]
        pub arguments: String, // JSON string
    }

    #[pyfunction]
    pub fn aegis_batch_tools(calls: Vec<PyToolCall>, policy: String) -> PyResult<String> { ... }
    ```
3.  **`persist_state()` Function**:
    ```rust
    #[pyfunction]
    pub fn aegis_persist_state(directory: String, turn_id: u64, state_json: String) -> PyResult<String> { ... }
    ```

---

## 3. Sandbox Execution Paths & Integration Design (C3)

### 3.1 Existing Execution Paths in `sandbox.rs`
1.  **Deterministic Sandbox**: Executes mock tests via static guardrail evaluations (`FirstOrderGuardrail`).
2.  **Wasmtime Sandbox**: Uses pre-cached compiled `wasmtime::Module` objects and instantiated environments (`InstancePre`) mapped by BLAKE3 hashes to eliminate cold starts.
3.  **QuickJS Invocation Bridge**: Uses raw guest linear memory layouts to pass QuickJS scripts into a wasmtime interpreter wrapper.

### 3.2 Integration of Batch execution (`batch.rs`)
The `ToolBatcher` executes a set of `ToolCall` objects based on a transactional policy:
*   `HaltOnFailure`: Runs tools sequentially in a loop. If a tool fails, it halts immediately, discards remaining executions, and logs a rollback/backtrack event.
*   `ContinueOnFailure`: Runs tools concurrently using `tokio::spawn` and awaits all futures, collecting results regardless of individual failures.
*   **Integration Path**: When a tool block in a Skill Blueprint contains multiple requests, `ToolExecutionGateway::execute_wasm_with_replay` (or a batch-specific gateway helper) will hand off control to `ToolBatcher`, forwarding execution parameters to `WasmtimeSandbox::execute_wasm_binary`.

### 3.3 Integration of Sandbox Runtime & Filesystem Serde (`runtime.rs`)
For secure, code-first execution:
*   **Security Scanning**: Before loading any user-generated or model-generated script, the script parameters are scanned against standard path traversal targets (`..`, `/etc/passwd`, etc.) and process injection targets (`rm -rf`, `sh`, `bash`).
*   **Wasm Sandbox Isolation**: Scripts must be evaluated in isolated guest runs with pre-defined `fuel_limit` restrictions. Each command consumes a set amount of fuel (e.g. 10 units), preventing infinite execution loops.
*   **`FilesystemSerde` Atomic Persistence**: replaces internal memory state dumps with atomic write transactions:
    1.  Serialize the execution turn state to a temporary file (`.tmp`).
    2.  Write and sync the file contents.
    3.  Atomically rename/publish the file to the final destination to prevent partial write corruptions.
    4.  Update the state hash chain with a BLAKE3 trace of the current turn's inputs/outputs.

---

## 4. Requirement Specifications Mapping (C4)

The project includes two sets of R1-R5 specifications. The maps below outline where each specification currently resides or will reside during graduation.

### 4.1 Set A: Baseline Core System Requirements
| Requirement | Description | Core Implementation Files |
|---|---|---|
| **R1** | Durable 100h Loop Coordination & Shift Manager | `core/rust/src/orchestrator.rs` (tracks run loop coordination), `core/rust/src/task_ledger.rs` (task selection & state), `core/rust/src/replay.rs` (replay checking). |
| **R2** | Host-Validated QuickJS/Javy Wasm Sandbox Bridge | `core/rust/src/sandbox.rs` (QuickJS linear memory header decoding, validation, Wasmtime linkers). |
| **R3** | mmap-Backed Arrow IPC Audit Stream | `core/rust/src/replay.rs` (implements single-writer `SegmentedArrowAuditStream` & `ArrowRunEventStream` for Arrow IPC). |
| **R4** | Security-Gated Tool Gateway & Witness Generation | `core/rust/src/tool_gateway.rs` (evaluates datalog policy decision envelopes), `core/rust/src/browser_witness.rs` (captures DOM/screenshot hashes), `core/rust/src/policy.rs` (R4 datalog gates). |
| **R5** | Hot-Path Optimizations & Benchmark Gauntlet | `core/rust/src/memory/fold.rs` (GenerationalSlab allocator), `core/rust/src/sandbox.rs` (pre-instantiated wasm caching), `core/rust/src/bridge_mmap.rs` (zero-copy mmap layouts). |

### 4.2 Set B: Graduation POC Requirements
| Requirement | Description | Target Module Location |
|---|---|---|
| **R1** | Semantic Cache Integration | `core/rust/src/eac/cache/semantic.rs` (checks L1/L2 hits; threshold=0.95; namespace invalidation). |
| **R2** | Tool Call Batching & Coalescing | `core/rust/src/eac/sandbox/batch.rs` (HaltOnFailure/ContinueOnFailure execution loop; maps to Skill Blueprint DSL). |
| **R3** | Code-First Sandbox & Filesystem Serde | `core/rust/src/eac/sandbox/runtime.rs` (command runner with path traversal guards, fuel limiting, atomic persistence). |
| **R4** | PyO3 Bindings & Client Integration | `core/rust/src/ffi.rs` (exposes SemanticCache class, batch/persist methods). |
| **R5** | Event Sourcing & Determinism | `core/rust/src/replay.rs` (registers event kinds and emits traces to preservation ledger). |

---

## 5. Verification Strategy & Commands (C5)

Verification should follow a multi-stage approach, starting with native Rust unit testing, moving to Python FFI check suites, and terminating with Criterion micro-path benchmark gates.

### 5.1 Cargo Tests (Rust Core verification)
Execute all core module unit tests:
```powershell
cargo test --manifest-path core/rust/Cargo.toml
```
To run tests specifically targeting memory layouts and sandboxing:
```powershell
cargo test --manifest-path core/rust/Cargo.toml -- sandbox::
cargo test --manifest-path core/rust/Cargo.toml -- replay::
```

### 5.2 PyTest Suites (Python Bridge verification)
Verify FFI integration, PyO3 bindings, and zero-copy mmap performance:
```powershell
pytest core/python/tests.py
```

### 5.3 Benchmark Gates & Performance Thresholds
The graduation code must run the benchmark gauntlet to prove it meets the strict micro-path thresholds:
```powershell
python scripts/run_checks.py
```
This script coordinates benchmark evaluation gates:
1.  **Semantic Cache Lookup Latency**:
    *   Benchmark: `semantic_cache_lookup_p95` (measured in Criterion).
    *   Threshold: **< 50 microseconds**.
2.  **Batch Execution Overhead**:
    *   Benchmark: `batch_executor_overhead_p95`.
    *   Threshold: **< 200 microseconds**.
3.  **Filesystem Serialization Write Latency**:
    *   Benchmark: `filesystem_serde_persist_p95`.
    *   Threshold: **< 10 milliseconds**.
4.  **SOTA Speedup Gate**:
    *   `scripts/sota_baseline_gate.py` must measure a trajectory speedup over the JSON baseline of **> 3.0x** (target: **> 10.0x**, current baseline achieved: **12.58x**).

### 5.4 Robustness & Security Validation
- **Path Traversal Gate**: Integrate unit tests checking that passing a string like `../../etc/passwd` or `c:\windows` to the `runtime.rs` executor returns a `Security Violation` error.
- **Integration Runs**: Test at least three complete scenarios verifying:
  1.  A cache lookup producing a hit under the same `evidence_binding_hash` namespace.
  2.  A transactional tool batch execution halting upon a simulated tool failure.
  3.  An atomic state save and subsequent recovery using `FilesystemSerde`.
