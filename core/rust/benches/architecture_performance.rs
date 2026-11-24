//! Stable benchmark IDs from the architecture master plan.
//!
//! These measurements are intentionally small, deterministic microbenchmarks.
//! They are evidence of the operation under test, not H0/H1/H2 workload proof.

use aegis_nerve::bridge_mmap::{open_mmap_bridge_view, write_mmap_bridge_frame};
use aegis_nerve::ffi::{aegis_hot_hash, aegis_status};
use aegis_nerve::ipc::MpmcFrameRing;
use aegis_nerve::memory::frame::{MemoryEdge, MemoryGraph, SemanticNode};
use aegis_nerve::message::MessageFrame;
use aegis_nerve::sandbox::WasmtimeSandbox;
use aegis_nerve::shm::{create_region, read_seqlock_frame, write_seqlock_frame};
use aegis_nerve::task_ledger::{TaskCard, TaskLedger};
use criterion::{BatchSize, Criterion, black_box, criterion_group, criterion_main};
use std::time::Duration;

fn task_ledger_with_ready_tasks(count: u128) -> TaskLedger {
    let mut ledger = TaskLedger::new(60_000);
    for task_id in 1..=count {
        ledger
            .insert_task(TaskCard::new(task_id, Vec::new(), 0, None, None))
            .expect("benchmark task insertion");
    }
    ledger
}

fn benchmark_ffi(c: &mut Criterion) {
    c.bench_function("FFI-001_empty_python_rust_call", |bencher| {
        bencher.iter(|| black_box(aegis_status().expect("status")));
    });

    for (id, bytes) in [
        ("FFI-002_typed_1KiB_payload", 1024usize),
        ("FFI-003_typed_64KiB_payload", 64 * 1024),
        ("FFI-004_typed_1MiB_payload", 1024 * 1024),
    ] {
        let payload = vec![0xA5; bytes];
        c.bench_function(id, |bencher| {
            bencher.iter(|| black_box(aegis_hot_hash(black_box(payload.clone())).expect("hash")));
        });
    }

    let frame = tempfile::NamedTempFile::new().expect("mmap frame");
    write_mmap_bridge_frame(frame.path(), 1, 2, &[0xA5; 4096]).expect("write frame");
    c.bench_function("FFI-005_mmap_shared_memory_handle", |bencher| {
        bencher.iter(|| {
            let view = open_mmap_bridge_view(black_box(frame.path())).expect("open frame");
            black_box(view.payload_ptr().expect("payload pointer"));
        });
    });
}

fn benchmark_scheduler(c: &mut Criterion) {
    c.bench_function("SCH-001_task_insert", |bencher| {
        bencher.iter_batched(
            || TaskLedger::new(60_000),
            |mut ledger| black_box(ledger.insert_task(TaskCard::new(1, Vec::new(), 0, None, None))),
            BatchSize::SmallInput,
        );
    });

    for count in [100_u128, 1_000, 10_000] {
        let id = format!("SCH-002_dag_validation/{count}");
        c.bench_function(&id, |bencher| {
            bencher.iter_batched(
                || task_ledger_with_ready_tasks(count),
                |mut ledger| black_box(ledger.ordered_ready_tasks(1).expect("DAG")),
                BatchSize::SmallInput,
            );
        });
    }

    c.bench_function("SCH-003_ready_queue_selection", |bencher| {
        bencher.iter_batched(
            || task_ledger_with_ready_tasks(100),
            |mut ledger| black_box(ledger.select_next_task(1).expect("selection")),
            BatchSize::SmallInput,
        );
    });
}

fn benchmark_communication_payloads(c: &mut Criterion) {
    for (id, bytes) in [
        ("IPC-001_payload_64B", 64usize),
        ("IPC-002_payload_1KiB", 1024),
        ("IPC-003_payload_64KiB", 64 * 1024),
        ("IPC-004_payload_1MiB", 1024 * 1024),
        ("IPC-005_payload_16MiB", 16 * 1024 * 1024),
    ] {
        let payload = vec![0xA5; bytes];
        c.bench_function(id, |bencher| {
            bencher.iter(|| {
                let frame = MessageFrame::new(black_box(payload.clone()));
                black_box(frame.is_valid())
            });
        });
    }

    let ring = MpmcFrameRing::with_capacity(64).expect("mpmc ring");
    c.bench_function("IPC-006_mpmc_ring_send_recv_1KiB", |bencher| {
        bencher.iter(|| {
            let frame = MessageFrame::with_identity(1, 1, vec![0x5A; 1024]);
            ring.try_send(black_box(frame)).expect("ring send");
            black_box(ring.try_recv().expect("ring recv"));
        });
    });

    let shm_file = tempfile::NamedTempFile::new().expect("shm file");
    let (_mmap, region) =
        create_region(shm_file.path().to_str().expect("utf8 path"), 8192, 64).expect("region");
    let seqlock_payload = vec![0x3C; 1024];
    c.bench_function("IPC-007_shm_seqlock_roundtrip_1KiB", |bencher| {
        bencher.iter(|| unsafe {
            write_seqlock_frame(&region, black_box(&seqlock_payload)).expect("seqlock write");
            black_box(read_seqlock_frame(&region, 16).expect("seqlock read"));
        });
    });

    c.bench_function("MEM-001_memory_graph_1000_nodes_csr", |bencher| {
        bencher.iter(|| {
            let mut graph = MemoryGraph::new();
            for i in 1..=1_000u64 {
                graph
                    .add_node(SemanticNode {
                        node_id: i as u128,
                        session_id: 1,
                        artifact_hash: [(i & 0xFF) as u8; 32],
                        ast_fingerprint: i,
                        fidelity: 1.0,
                    })
                    .expect("node");
                if i > 1 {
                    graph
                        .add_edge(MemoryEdge {
                            edge_id: i as u128,
                            from_node: (i - 1) as u128,
                            to_node: i as u128,
                            weight: 0.9,
                            time_since_last_access: 0.0,
                        })
                        .expect("edge");
                }
            }
            black_box(graph.build_csr_snapshot(None))
        });
    });
}

fn benchmark_cpu(c: &mut Criterion) {
    let payload = vec![0x5A; 64 * 1024];
    c.bench_function("CPU-001_hashing_batch", |bencher| {
        bencher.iter(|| {
            black_box(aegis_nerve::hot_engine::simd_blake3_hash(black_box(
                &payload,
            )))
        });
    });

    let source = "fn main() { let value = 42; println!(\"{value}\"); }";
    c.bench_function("CPU-002_parser_ast_batch", |bencher| {
        bencher.iter(|| black_box(syn::parse_file(black_box(source)).expect("Rust AST")));
    });

    let value = serde_json::json!({"task": 1, "status": "ready", "payload": [1, 2, 3]});
    c.bench_function("CPU-003_serialization_batch", |bencher| {
        bencher.iter(|| black_box(serde_json::to_vec(black_box(&value)).expect("JSON")));
    });
}

fn benchmark_sandbox(c: &mut Criterion) {
    let module = wat::parse_str("(module (func (export \"_start\")))").expect("Wasm");
    let looping_module =
        wat::parse_str("(module (func (export \"_start\") (loop br 0)))").expect("looping Wasm");

    c.bench_function("SBX-001_wasmtime_cold_compile", |bencher| {
        bencher.iter_batched(
            WasmtimeSandbox::new,
            |sandbox| black_box(sandbox.execute_wasm_binary(&module, 10_000)),
            BatchSize::SmallInput,
        );
    });

    let sandbox = WasmtimeSandbox::new();
    c.bench_function("SBX-002_cached_module_execution", |bencher| {
        bencher.iter(|| black_box(sandbox.execute_wasm_binary(&module, 10_000)));
    });

    c.bench_function("SBX-003_fuel_trap", |bencher| {
        bencher.iter(|| black_box(sandbox.execute_wasm_binary(&looping_module, 1)));
    });

    let mut epoch_sandbox = WasmtimeSandbox::new();
    epoch_sandbox.timeout_ms = 1;
    c.bench_function("SBX-004_epoch_interruption", |bencher| {
        bencher.iter(|| {
            let result = epoch_sandbox.execute_wasm_binary(&looping_module, u64::MAX);
            black_box(result.is_err())
        });
    });
}

criterion_group! {
    name = benches;
    config = Criterion::default().measurement_time(Duration::from_secs(1)).sample_size(10);
    targets = benchmark_ffi, benchmark_scheduler, benchmark_communication_payloads, benchmark_cpu, benchmark_sandbox
}
criterion_main!(benches);
