//! Shadow Sealer Throughput & Backpressure Benchmarks
//!
//! Measures `AsyncShadowSealer::try_submit` and full backpressure bursts using a
//! shared long-lived sealer outside timed loops, calibrated batch Throughput,
//! non-zero deterministic payloads, explicit Result inspection (zero `let _ =`),
//! and RAII temp directory cleanup outside measured regions.

use aegis_nerve::hot_engine::{
    AsyncShadowSealer, EvidenceHandle, InMemoryEvidenceArena, ShadowSealAdmission, TrustLevel,
};
use criterion::{Criterion, Throughput, black_box, criterion_group, criterion_main};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

const ARENA_LIVE_BYTES: usize = 64 * 1024 * 1024;
const ARENA_ARTIFACT_BYTES: usize = 1024 * 1024;
const BATCH_ITEMS: usize = 64;
const SAMPLE_SIZE: usize = 200;
const WARMUP_SECS: u64 = 3;
const MEASURE_SECS: u64 = 10;

static TEMP_SEQ: AtomicU64 = AtomicU64::new(1);

struct BenchTempDir {
    path: PathBuf,
}

impl BenchTempDir {
    fn new(tag: &str) -> Self {
        let seq = TEMP_SEQ.fetch_add(1, Ordering::Relaxed);
        let pid = std::process::id();
        let dir = std::env::temp_dir().join(format!("aegis-bench-shadow-sealer-{tag}-{pid}-{seq}"));
        std::fs::create_dir_all(&dir).expect("create shadow sealer bench temp dir");
        Self { path: dir }
    }
}

impl Drop for BenchTempDir {
    fn drop(&mut self) {
        match std::fs::remove_dir_all(&self.path) {
            Ok(()) => {}
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            Err(err) => eprintln!(
                "warning: failed to remove shadow sealer bench dir {}: {err}",
                self.path.display()
            ),
        }
    }
}

fn deterministic_payload(bytes: usize, salt: u8) -> Vec<u8> {
    (0..bytes)
        .map(|i| (((i.wrapping_mul(131)) ^ (i >> 5)) as u8).wrapping_add(salt))
        .collect()
}

fn seed_arena_with_payload(bytes: usize) -> (InMemoryEvidenceArena, Vec<EvidenceHandle>) {
    let arena = InMemoryEvidenceArena::new(TrustLevel::Dev, ARENA_LIVE_BYTES, ARENA_ARTIFACT_BYTES);
    let mut handles = Vec::with_capacity(512);
    for idx in 0..512u32 {
        let payload = deterministic_payload(bytes, (idx & 0xFF) as u8);
        let handle = arena.commit(&payload).expect("seed arena payload");
        handles.push(handle);
    }
    (arena, handles)
}

fn phase0_criterion_config() -> Criterion {
    Criterion::default()
        .warm_up_time(Duration::from_secs(WARMUP_SECS))
        .measurement_time(Duration::from_secs(MEASURE_SECS))
        .sample_size(SAMPLE_SIZE)
}

fn bench_shadow_sealer_try_submit_payload_sweep(c: &mut Criterion) {
    let mut group = c.benchmark_group("shadow_sealer_try_submit");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);

    for &size in &[1024usize, 4096, 16384, 65536] {
        let (arena, handles) = seed_arena_with_payload(size);
        let dir = BenchTempDir::new("payload");
        let sealer = AsyncShadowSealer::start_with_queue_depth(&dir.path, 4096)
            .expect("start shared payload sealer");
        let mut cursor = 0usize;

        group.throughput(Throughput::Bytes((size * BATCH_ITEMS) as u64));
        group.bench_function(format!("payload_{size}b_qd4096"), |b| {
            b.iter(|| {
                let mut queued = 0usize;
                let mut backpressured = 0usize;
                for _ in 0..BATCH_ITEMS {
                    let handle = handles[cursor % handles.len()];
                    cursor = cursor.wrapping_add(1);
                    match sealer.try_submit(black_box(&arena), black_box(handle)) {
                        Ok(ShadowSealAdmission::Queued(_)) => queued += 1,
                        Ok(ShadowSealAdmission::Backpressured(_)) => backpressured += 1,
                        Err(err) => panic!("unexpected shadow sealer try_submit error: {err:?}"),
                    }
                }
                black_box((queued, backpressured))
            });
        });

        sealer
            .flush_blocking()
            .expect("drain shadow sealer before cleanup");
        drop(sealer);
    }
    group.finish();
}

fn bench_shadow_sealer_queue_depth_sweep(c: &mut Criterion) {
    let mut group = c.benchmark_group("shadow_sealer_queue_depth");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);

    let (arena, handles) = seed_arena_with_payload(4096);
    for &qd in &[32usize, 64, 256, 1024] {
        // Submit burst exceeding qd so shallow depths genuinely trigger Backpressured.
        let burst = (qd * 2).max(128);
        let dir = BenchTempDir::new("qd");
        let sealer = AsyncShadowSealer::start_with_queue_depth(&dir.path, qd)
            .expect("start shared qd sealer");
        let mut cursor = 0usize;

        group.throughput(Throughput::Elements(burst as u64));
        group.bench_function(format!("qd_{qd}_burst_{burst}"), |b| {
            b.iter(|| {
                let mut queued = 0usize;
                let mut backpressured = 0usize;
                for _ in 0..burst {
                    let handle = handles[cursor % handles.len()];
                    cursor = cursor.wrapping_add(1);
                    match sealer.try_submit(black_box(&arena), black_box(handle)) {
                        Ok(ShadowSealAdmission::Queued(_)) => queued += 1,
                        Ok(ShadowSealAdmission::Backpressured(_)) => backpressured += 1,
                        Err(err) => panic!("unexpected shadow sealer try_submit error: {err:?}"),
                    }
                }
                black_box((queued, backpressured))
            });
        });

        sealer
            .flush_blocking()
            .expect("drain shadow sealer before cleanup");
        drop(sealer);
    }
    group.finish();
}

criterion_group! {
    name = benches;
    config = phase0_criterion_config();
    targets = bench_shadow_sealer_try_submit_payload_sweep, bench_shadow_sealer_queue_depth_sweep
}
criterion_main!(benches);
