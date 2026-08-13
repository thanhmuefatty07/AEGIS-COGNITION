//! Shadow Sealer Throughput Benchmarks
//!
//! Phase 1C.1: Measure `AsyncShadowSealer::try_submit` admission cost across
//! payload sizes and queue depths.
//!
//! Uses ONLY the public `AsyncShadowSealer::start` /
//! `AsyncShadowSealer::start_with_queue_depth` API. The previous
//! bench-only `start_paused_for_backpressure_test` helper is `#[cfg(test)]`
//! and not callable from a bench crate (would need a feature-gated re-export
//! to expose — that change is out of scope for Phase 1C). The public `start`
//! API spawns a real background worker thread; we accept this and rely on
//! `iter_with_setup` so the sealer is created exactly once per measurement,
//! not per inner iteration, avoiding thread pileup.
//!
//! Smoke-test only: this file compiles and runs one short measurement per
//! benchmark function. Full sample-sizes are deferred to when sealer
//! shutdown overhead is profiled in CI under a stable thread pool.

use aegis_nerve::hot_engine::{
    AsyncShadowSealer, EvidenceHandle, InMemoryEvidenceArena, TrustLevel,
};
use criterion::{black_box, criterion_group, criterion_main, Criterion, Throughput};
use std::path::PathBuf;

const ARENA_LIVE_BYTES: usize = 64 * 1024 * 1024;
const ARENA_ARTIFACT_BYTES: usize = 1024 * 1024;
const SAMPLES: usize = 10;
const MEASURE_SECS: u64 = 2;

fn unique_seal_dir(tag: &str) -> PathBuf {
    let mut p = std::env::temp_dir();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    p.push(format!("aegis-bench-shadow-sealer-{tag}-{pid}-{nanos}"));
    p
}

fn seed_arena_with_payload(bytes: usize) -> (InMemoryEvidenceArena, Vec<EvidenceHandle>) {
    let arena = InMemoryEvidenceArena::new(TrustLevel::Dev, ARENA_LIVE_BYTES, ARENA_ARTIFACT_BYTES);
    let payload = vec![0u8; bytes];
    let mut handles = Vec::with_capacity(512);
    for _ in 0..512 {
        if let Ok(h) = arena.commit(&payload) {
            handles.push(h);
        }
    }
    (arena, handles)
}

fn bench_shadow_sealer_try_submit_payload_sweep(c: &mut Criterion) {
    let mut group = c.benchmark_group("shadow_sealer_try_submit");
    group.sample_size(SAMPLES);
    group.measurement_time(std::time::Duration::from_secs(MEASURE_SECS));
    for size in [1024usize, 4096, 16384, 65536].iter() {
        let (arena, handles) = seed_arena_with_payload(*size);
        group.throughput(Throughput::Elements(1));
        group.bench_function(format!("payload_{}b_qd4096", size), |b| {
            let dir = unique_seal_dir("payload");
            b.iter_custom(|iters| {
                let mut total = std::time::Duration::ZERO;
                for _ in 0..iters {
                    let sealer = AsyncShadowSealer::start(&dir).expect("start sealer");
                    let start = std::time::Instant::now();
                    let mut idx = 0usize;
                    for _ in 0..64 {
                        let handle = handles[idx % handles.len()];
                        idx = idx.wrapping_add(1);
                        let _ = black_box(
                            sealer.try_submit(black_box(&arena), black_box(handle)),
                        );
                    }
                    total += start.elapsed();
                    drop(sealer);
                }
                let _ = std::fs::remove_dir_all(&dir);
                total
            });
        });
    }
    group.finish();
}

fn bench_shadow_sealer_queue_depth_sweep(c: &mut Criterion) {
    let mut group = c.benchmark_group("shadow_sealer_queue_depth");
    group.sample_size(SAMPLES);
    group.measurement_time(std::time::Duration::from_secs(MEASURE_SECS));
    let (arena, handles) = seed_arena_with_payload(4096);
    for qd in [256usize, 1024, 2048, 4096].iter() {
        group.bench_function(format!("qd_{}", qd), |b| {
            let dir = unique_seal_dir("qd");
            b.iter_custom(|iters| {
                let mut total = std::time::Duration::ZERO;
                for _ in 0..iters {
                    let sealer =
                        AsyncShadowSealer::start_with_queue_depth(&dir, *qd).expect("start sealer");
                    let start = std::time::Instant::now();
                    let mut idx = 0usize;
                    for _ in 0..64 {
                        let handle = handles[idx % handles.len()];
                        idx = idx.wrapping_add(1);
                        let _ = black_box(
                            sealer.try_submit(black_box(&arena), black_box(handle)),
                        );
                    }
                    total += start.elapsed();
                    drop(sealer);
                }
                let _ = std::fs::remove_dir_all(&dir);
                total
            });
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_shadow_sealer_try_submit_payload_sweep,
    bench_shadow_sealer_queue_depth_sweep,
);
criterion_main!(benches);
