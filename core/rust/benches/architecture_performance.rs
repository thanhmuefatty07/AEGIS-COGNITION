//! Pure-Rust Subsystem Architecture Performance Microbenchmarks
//!
//! Covers hot-path hashing, mmap bridge, DAG scheduler with real dependency edges,
//! concurrent multi-thread IPC MPMC ring and shm seqlock contention, connected CSR graph
//! construction, multi-file AST & JSON batches, and non-trivial Wasmtime sandboxing.
//! True CPython <-> Rust PyO3 FFI boundary crossings (`FFI-001..004`) are measured
//! via `scripts/ffi_boundary_benchmark.py`.

use aegis_nerve::bridge_mmap::{open_mmap_bridge_view, write_mmap_bridge_frame};
use aegis_nerve::ffi::aegis_hot_hash;
use aegis_nerve::ipc::MpmcFrameRing;
use aegis_nerve::memory::frame::{MemoryEdge, MemoryGraph, SemanticNode};
use aegis_nerve::message::MessageFrame;
use aegis_nerve::sandbox::WasmtimeSandbox;
use aegis_nerve::shm::{create_region, read_seqlock_frame, write_seqlock_frame};
use aegis_nerve::task_ledger::{TaskCard, TaskLedger};
use criterion::{BatchSize, Criterion, Throughput, black_box, criterion_group, criterion_main};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

const SAMPLE_SIZE: usize = 200;
const WARMUP_SECS: u64 = 3;
const MEASURE_SECS: u64 = 10;

fn deterministic_bytes(len: usize, salt: u8) -> Vec<u8> {
    (0..len)
        .map(|i| (((i.wrapping_mul(131)) ^ (i >> 4)) as u8).wrapping_add(salt))
        .collect()
}

fn task_ledger_with_dag_edges(count: u128) -> TaskLedger {
    let mut ledger = TaskLedger::new(60_000);
    for task_id in 1..=count {
        let mut deps = Vec::new();
        if task_id > 4 {
            deps.push((task_id / 2).max(1));
            let secondary = (task_id / 3).max(1);
            if secondary != deps[0] {
                deps.push(secondary);
            }
        }
        ledger
            .insert_task(TaskCard::new(
                task_id,
                deps,
                (task_id % 7) as u32,
                Some(120_000 + ((task_id * 37) % 10_000) as u64),
                Some((task_id % 53) as i64),
            ))
            .expect("DAG benchmark task insertion");
    }
    ledger.complete_task(1).expect("complete root task 1");
    if count >= 2 {
        ledger.complete_task(2).expect("complete root task 2");
    }
    ledger
}

fn build_connected_memory_graph(node_count: u64) -> MemoryGraph {
    let mut graph = MemoryGraph::new();
    for i in 1..=node_count {
        graph
            .add_node(SemanticNode {
                node_id: i as u128,
                session_id: 1,
                artifact_hash: [((i % 251) as u8).max(1); 32],
                ast_fingerprint: i.wrapping_mul(0x9E37_79B9_7F4A_7C15),
                fidelity: 0.9,
            })
            .expect("graph node");
    }
    let mut edge_id = 1u128;
    for i in 2..=node_count {
        for parent in [
            (i - 1).max(1),
            (i / 2).max(1),
            ((i * 7 + 3) % (i - 1)).max(1),
        ] {
            if parent != i
                && graph
                    .add_edge(MemoryEdge {
                        edge_id,
                        from_node: parent as u128,
                        to_node: i as u128,
                        weight: 0.8,
                        time_since_last_access: (i % 100) as f32,
                    })
                    .is_ok()
            {
                edge_id += 1;
            }
        }
    }
    graph
}

fn phase0_criterion_config() -> Criterion {
    Criterion::default()
        .warm_up_time(Duration::from_secs(WARMUP_SECS))
        .measurement_time(Duration::from_secs(MEASURE_SECS))
        .sample_size(SAMPLE_SIZE)
}

fn benchmark_ffi_and_hot_buffers(c: &mut Criterion) {
    let mut group = c.benchmark_group("rust_hot_buffer");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);

    for &(id, bytes) in &[
        ("rust_hot_hash_1KiB", 1024usize),
        ("rust_hot_hash_64KiB", 64 * 1024),
        ("rust_hot_hash_1MiB", 1024 * 1024),
    ] {
        let payload = deterministic_bytes(bytes, 0xA5);
        group.throughput(Throughput::Bytes(bytes as u64));
        group.bench_function(id, |bencher| {
            bencher.iter_batched(
                || payload.clone(),
                |buf| black_box(aegis_hot_hash(black_box(buf)).expect("hash")),
                BatchSize::SmallInput,
            );
        });
    }

    let frame = tempfile::NamedTempFile::new().expect("mmap frame");
    let mmap_payload = deterministic_bytes(64 * 1024, 0x3C);
    write_mmap_bridge_frame(frame.path(), 1, 2, &mmap_payload).expect("write frame");
    group.throughput(Throughput::Bytes((64 * 1024) as u64));
    group.bench_function("FFI-005_mmap_shared_memory_handle", |bencher| {
        bencher.iter(|| {
            let view = open_mmap_bridge_view(black_box(frame.path())).expect("open frame");
            let ok = view.payload_hash_matches().expect("hash check");
            black_box((view.payload_ptr().expect("payload pointer"), ok))
        });
    });
    group.finish();
}

fn benchmark_scheduler(c: &mut Criterion) {
    let mut group = c.benchmark_group("scheduler_dag");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);

    group.throughput(Throughput::Elements(1));
    group.bench_function("SCH-001_task_insert", |bencher| {
        bencher.iter_batched(
            || TaskLedger::new(60_000),
            |mut ledger| black_box(ledger.insert_task(TaskCard::new(1, Vec::new(), 0, None, None))),
            BatchSize::SmallInput,
        );
    });

    for &count in &[100_u128, 1_000, 10_000] {
        let base_ledger = task_ledger_with_dag_edges(count);
        group.throughput(Throughput::Elements(count as u64));
        group.bench_function(format!("SCH-002_dag_validation/{count}"), |bencher| {
            let mut tick = 100u64;
            bencher.iter_batched(
                || {
                    tick = tick.wrapping_add(1);
                    (base_ledger.clone(), tick)
                },
                |(mut ledger, now_ms)| {
                    black_box(ledger.ordered_ready_tasks(black_box(now_ms)).expect("DAG"))
                },
                BatchSize::SmallInput,
            );
        });
    }

    let select_base = task_ledger_with_dag_edges(500);
    group.throughput(Throughput::Elements(1));
    group.bench_function("SCH-003_ready_queue_selection", |bencher| {
        let mut tick = 200u64;
        bencher.iter_batched(
            || {
                tick = tick.wrapping_add(1);
                (select_base.clone(), tick)
            },
            |(mut ledger, now_ms)| {
                black_box(
                    ledger
                        .select_next_task(black_box(now_ms))
                        .expect("selection"),
                )
            },
            BatchSize::SmallInput,
        );
    });
    group.finish();
}

fn benchmark_communication_payloads(c: &mut Criterion) {
    let mut group = c.benchmark_group("ipc_and_memory");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);

    for &(id, bytes) in &[
        ("IPC-001_payload_64B", 64usize),
        ("IPC-002_payload_1KiB", 1024),
        ("IPC-003_payload_64KiB", 64 * 1024),
        ("IPC-004_payload_1MiB", 1024 * 1024),
        ("IPC-005_payload_16MiB", 16 * 1024 * 1024),
    ] {
        let payload = deterministic_bytes(bytes, 0x5A);
        group.throughput(Throughput::Bytes(bytes as u64));
        group.bench_function(id, |bencher| {
            bencher.iter_batched(
                || payload.clone(),
                |buf| {
                    let frame = MessageFrame::new(black_box(buf));
                    black_box((frame.is_valid(), frame.payload.len()))
                },
                BatchSize::SmallInput,
            );
        });
    }

    // Concurrent multi-thread producer/consumer contention on MpmcFrameRing
    let ring = Arc::new(MpmcFrameRing::with_capacity(256).expect("mpmc ring"));
    let stop_flag = Arc::new(AtomicBool::new(false));
    let bg_ring = Arc::clone(&ring);
    let bg_stop = Arc::clone(&stop_flag);
    let bg_payload = deterministic_bytes(1024, 0x77);
    let bg_handle = std::thread::spawn(move || {
        let mut seq = 10u128;
        while !bg_stop.load(Ordering::Relaxed) {
            let f = MessageFrame::with_identity(seq, 2, bg_payload.clone());
            if bg_ring.try_send(f).is_err() {
                std::hint::spin_loop();
            } else {
                seq += 1;
            }
            if bg_ring.try_recv().is_none() {
                std::thread::yield_now();
            }
        }
    });

    let ring_payload = deterministic_bytes(1024, 0x5A);
    group.throughput(Throughput::Bytes(1024));
    group.bench_function("IPC-006_mpmc_ring_send_recv_1KiB", |bencher| {
        bencher.iter_batched(
            || MessageFrame::with_identity(1, 1, ring_payload.clone()),
            |frame| {
                let sent = ring.try_send(black_box(frame));
                let recvd = ring.try_recv();
                black_box((sent.is_ok(), recvd.is_some()))
            },
            BatchSize::SmallInput,
        );
    });
    stop_flag.store(true, Ordering::Relaxed);
    bg_handle.join().expect("join MPMC contention worker");

    let shm_file = tempfile::NamedTempFile::new().expect("shm file");
    let (_mmap, region) =
        create_region(shm_file.path().to_str().expect("utf8 path"), 8192, 64).expect("region");
    let seqlock_payload = deterministic_bytes(1024, 0x3C);
    group.throughput(Throughput::Bytes(1024));
    group.bench_function("IPC-007_shm_seqlock_roundtrip_1KiB", |bencher| {
        bencher.iter(|| unsafe {
            write_seqlock_frame(&region, black_box(&seqlock_payload)).expect("seqlock write");
            black_box(read_seqlock_frame(&region, 16).expect("seqlock read"));
        });
    });

    let connected_graph = build_connected_memory_graph(1_000);
    group.throughput(Throughput::Elements(1_000));
    group.bench_function("MEM-001_memory_graph_1000_nodes_csr", |bencher| {
        bencher.iter(|| black_box(black_box(&connected_graph).build_csr_snapshot(None)));
    });
    group.finish();
}

fn benchmark_cpu(c: &mut Criterion) {
    let mut group = c.benchmark_group("cpu_kernels");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);

    let payload = deterministic_bytes(64 * 1024, 0x5A);
    group.throughput(Throughput::Bytes((64 * 1024) as u64));
    group.bench_function("CPU-001_hashing_batch", |bencher| {
        bencher.iter(|| {
            black_box(aegis_nerve::hot_engine::simd_blake3_hash(black_box(
                &payload,
            )))
        });
    });

    let rust_sources: Vec<String> = (0..16)
        .map(|idx| {
            format!(
                "pub struct Record{idx} {{ pub id: u64, pub tags: Vec<String> }}\n\
                 pub fn score_{idx}(x: u64, y: u64) -> u64 {{ (x * 31 + y).wrapping_add({idx}) }}\n"
            )
        })
        .collect();
    let ast_bytes: usize = rust_sources.iter().map(String::len).sum();
    group.throughput(Throughput::Bytes(ast_bytes as u64));
    group.bench_function("CPU-002_parser_ast_batch", |bencher| {
        bencher.iter(|| {
            let mut item_count = 0usize;
            for src in black_box(&rust_sources) {
                let parsed = syn::parse_file(black_box(src)).expect("Rust AST");
                item_count += parsed.items.len();
            }
            black_box(item_count)
        });
    });

    let json_batch: Vec<serde_json::Value> = (0..32)
        .map(|idx| {
            serde_json::json!({
                "task_id": idx,
                "status": "ready",
                "metrics": [idx * 2, idx * 3, idx * 5],
                "nested": {"owner": format!("agent-{idx}"), "verified": idx % 2 == 0}
            })
        })
        .collect();
    group.throughput(Throughput::Elements(json_batch.len() as u64));
    group.bench_function("CPU-003_serialization_batch", |bencher| {
        bencher.iter(|| {
            let mut total_bytes = 0usize;
            for item in black_box(&json_batch) {
                let buf = serde_json::to_vec(black_box(item)).expect("JSON");
                total_bytes += buf.len();
            }
            black_box(total_bytes)
        });
    });
    group.finish();
}

fn benchmark_sandbox(c: &mut Criterion) {
    let mut group = c.benchmark_group("wasmtime_sandbox");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);
    group.throughput(Throughput::Elements(1));

    let compute_module = wat::parse_str(
        r#"(module
            (memory (export "memory") 1)
            (func (export "_start")
                (local $i i32)
                (local.set $i (i32.const 0))
                (loop $l
                    (i32.store (i32.mul (local.get $i) (i32.const 4)) (i32.mul (local.get $i) (i32.const 7)))
                    (local.set $i (i32.add (local.get $i) (i32.const 1)))
                    (br_if $l (i32.lt_u (local.get $i) (i32.const 64))))))"#,
    )
    .expect("compute Wasm");
    let looping_module = wat::parse_str(
        r#"(module
            (func (export "_start")
                (local $i i32)
                (loop $l
                    (local.set $i (i32.add (local.get $i) (i32.const 1)))
                    (br $l))))"#,
    )
    .expect("looping Wasm");

    group.bench_function("SBX-001_wasmtime_cold_compile", |bencher| {
        bencher.iter_batched(
            WasmtimeSandbox::new,
            |sandbox| {
                black_box(
                    sandbox.execute_wasm_binary(black_box(&compute_module), black_box(50_000)),
                )
            },
            BatchSize::SmallInput,
        );
    });

    let sandbox = WasmtimeSandbox::new();
    group.bench_function("SBX-002_cached_module_execution", |bencher| {
        bencher.iter(|| {
            black_box(sandbox.execute_wasm_binary(black_box(&compute_module), black_box(50_000)))
        });
    });

    group.bench_function("SBX-003_fuel_trap", |bencher| {
        bencher.iter(|| {
            black_box(sandbox.execute_wasm_binary(black_box(&looping_module), black_box(500)))
        });
    });

    let mut epoch_sandbox = WasmtimeSandbox::new();
    epoch_sandbox.timeout_ms = 1;
    group.bench_function("SBX-004_epoch_interruption", |bencher| {
        bencher.iter(|| {
            let result =
                epoch_sandbox.execute_wasm_binary(black_box(&looping_module), black_box(u64::MAX));
            black_box(result.is_err())
        });
    });
    group.finish();
}

criterion_group! {
    name = benches;
    config = phase0_criterion_config();
    targets = benchmark_ffi_and_hot_buffers, benchmark_scheduler, benchmark_communication_payloads, benchmark_cpu, benchmark_sandbox
}
criterion_main!(benches);
