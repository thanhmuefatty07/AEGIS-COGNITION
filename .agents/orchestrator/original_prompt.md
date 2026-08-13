## 2026-06-07T18:57:49Z

Build AEGIS-COGNITION into a production-grade, long-horizon AI agent harness that is deterministic, zero-trust, evidence-backed, and replayable, optimizing SFoT and MTC metrics.

Working directory: c:/Users/ADMIN/AEGIS-COGNITION
Integrity mode: development

## Requirements

### R1. Durable 100h Loop Coordination & Shift Manager
Implement the durable, multi-shift 100h loop coordination in the Shift Manager, integrating TaskLedger, Context Governor, and Replay Ledger to ensure crash-resilience and correct state transitions.

### R2. Host-Validated QuickJS/Javy Wasm Sandbox Bridge
Complete the host-validated linear memory QuickJS/Javy bridge in Wasmtime, verifying deterministic invocation ABI packets, checking fuel/memory limits, and running tests comparing Javy vs quickjs-wasm cold start and fuel.

### R3. mmap-Backed Arrow IPC Audit Stream
Build mmap-backed Arrow IPC streams with resumable, append-only write/read semantics for the Episodic Audit Log, providing zero-copy observability views for the Python bridge.

### R4. Security-Gated Tool Gateway & Witness Generation
Implement the execution proof/witness generation for browser and shell tool executions in the Tool Gateway, enforcing security policies (R3/R4 permissions) and preventing unverified memory commits.

### R5. Hot-Path Optimizations & Benchmark Gauntlet
Optimize hot paths (generational slab allocator, Wasmtime caching, zero-copy IPC layouts, and SIMD JSON) and run the benchmark gauntlet to prove >3x latency/throughput performance compared to SOTA JSON trajectory baselines.

## Acceptance Criteria

### Replay & Endurance Validation
- [ ] All 128 crash points in `ReplayChaosBench` recover successfully with correct BLAKE3 hash chain validations.
- [ ] `ReplayEnduranceBench` successfully runs a 100h simulated run with 2,400 synthetic cycles, 400 context folds, and 400 checkpoints, producing a valid endurance report.
- [ ] The PyCapsule FFI boundary handles repeated 1 MiB memoryview transfers under 5 us median latency.

### Sandbox & Security
- [ ] QuickJS Wasm execution validates ABI guest linear memory headers correctly, trapping out-of-budget, unsafe, or destructive scripts.
- [ ] High-risk tool calls (financial/outreach) fail-closed and block execution unless approved via the HITL multi-signature gate.

### IPC & Persistence
- [ ] Segmented Arrow audit logs are written in a single-writer append-only stream and verified through mmap-backed column scans without full in-memory event materialization.
- [ ] Recovery from partial or corrupted Arrow segments rolls back to the last valid checkpoint without model intervention.

### Performance & Evals
- [ ] Rust-side benchmark suite Criterion run passes all CPU-bound micro-path gates, with target cache-validated benchmarks under their respective thresholds.
- [ ] Python preflight checks and `run_checks.py` execute successfully and return status code 0.
