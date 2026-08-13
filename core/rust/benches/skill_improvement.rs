//! Skill Improvement Benchmarks (Phase 1C.3)
//!
//! Measures the hot-path cost of `SkillRegistry::improve_skill_from_usage`
//! at the gate-check layer. The full happy-path (admit → regression →
//! domain_hash → learning ledger append) requires a real Wasmtime sandbox
//! and a populated admitted-skill registry, which is heavy for a smoke
//! bench; that path is exercised by the `*_skill_improve_*` tests in
//! `src/tests.rs` instead.
//!
//! What this bench DOES measure:
//!   1. Gate 1 (insufficient usage) — early rejection on usage_count<10
//!   2. License-gate + Gates 1+2 (passing gates) + BTreeMap lookup →
//!      MissingAdmission rejection on an unknown skill
//!
//! Both paths are fast, deterministic rejections that happen constantly
//! in production: brand-new skills that haven't yet collected 10 uses,
//! and skills that were never admitted to the registry.

use aegis_nerve::learning::LearningLedger;
use aegis_nerve::licensing::LicenseManager;
use aegis_nerve::skill_registry::{
    SkillRegistry, SkillRegressionCase, SkillRegressionReport, SkillUsageStats,
};
use aegis_nerve::skill_registry::SkillAdmissionError;
use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};

const SAMPLE_SIZE: usize = 60;

/// Build a clean regression report that passes Gate 2 (`passed_count > 0`).
fn clean_report() -> SkillRegressionReport {
    let case = SkillRegressionCase::new(1, [0x11u8; 32], [0x22u8; 32], [0x22u8; 32], true)
        .expect("clean regression case must construct");
    SkillRegressionReport::new(std::slice::from_ref(&case))
        .expect("clean regression report must construct")
}

fn bench_gate_insufficient_usage(c: &mut Criterion) {
    let mut group = c.benchmark_group("skill_improve_gates");
    group.sample_size(SAMPLE_SIZE);
    group.throughput(Throughput::Elements(1));

    group.bench_function("gate1_insufficient_usage_no_license", |b| {
        let mut registry = SkillRegistry::new();
        let mut ledger = LearningLedger::new();
        let stats = SkillUsageStats::new(5, 0.95); // < 10 ⇒ Gate 1 rejects
        let report = clean_report();
        let unknown_skill_id: u128 = 0xDEAD_BEEF;

        b.iter(|| {
            let r = registry.improve_skill_from_usage(
                black_box(unknown_skill_id),
                black_box(&stats),
                black_box(b"improved-wasm"),
                black_box(&report),
                black_box(&mut ledger),
                None,
            );
            assert_eq!(r.unwrap_err(), SkillAdmissionError::InsufficientUsage);
        });
    });

    group.bench_function("gate1_insufficient_usage_with_license", |b| {
        let mut registry = SkillRegistry::new();
        let mut ledger = LearningLedger::new();
        let license = LicenseManager::new();
        let stats = SkillUsageStats::new(5, 0.95); // < 10 ⇒ Gate 1 rejects
        let report = clean_report();
        let unknown_skill_id: u128 = 0xDEAD_BEEF;

        b.iter(|| {
            let r = registry.improve_skill_from_usage(
                black_box(unknown_skill_id),
                black_box(&stats),
                black_box(b"improved-wasm"),
                black_box(&report),
                black_box(&mut ledger),
                black_box(Some(&license)),
            );
            // Commercial gate fires first: community license can't unlock SelfImprovingSkills,
            // so we get LicenseRequired before Gate 1 ever sees the low usage count.
            assert!(
                matches!(r, Err(SkillAdmissionError::LicenseRequired(_))),
                "expected LicenseRequired from community license, got {:?}",
                r
            );
        });
    });

    group.bench_function("gates12_lookup_missing_admission_no_license", |b| {
        let mut registry = SkillRegistry::new();
        let mut ledger = LearningLedger::new();
        let stats = SkillUsageStats::new(100, 0.95); // passes Gate 1
        let report = clean_report(); // passes Gate 2
        let unknown_skill_id: u128 = 0xDEAD_BEEF; // not admitted ⇒ MissingAdmission

        b.iter(|| {
            let r = registry.improve_skill_from_usage(
                black_box(unknown_skill_id),
                black_box(&stats),
                black_box(b"improved-wasm"),
                black_box(&report),
                black_box(&mut ledger),
                None,
            );
            assert_eq!(r.unwrap_err(), SkillAdmissionError::MissingAdmission);
        });
    });

    group.bench_function("gates12_lookup_with_license_gate", |b| {
        let mut registry = SkillRegistry::new();
        let mut ledger = LearningLedger::new();
        let license = LicenseManager::new();
        let stats = SkillUsageStats::new(100, 0.95);
        let report = clean_report();
        let unknown_skill_id: u128 = 0xDEAD_BEEF;

        b.iter(|| {
            let r = registry.improve_skill_from_usage(
                black_box(unknown_skill_id),
                black_box(&stats),
                black_box(b"improved-wasm"),
                black_box(&report),
                black_box(&mut ledger),
                black_box(Some(&license)),
            );
            // Commercial gate fires first; community license blocks before
            // we ever reach the BTreeMap lookup.
            assert!(
                matches!(r, Err(SkillAdmissionError::LicenseRequired(_))),
                "expected LicenseRequired, got {:?}",
                r
            );
        });
    });

    group.finish();
}

fn bench_usage_count_scaling(c: &mut Criterion) {
    // Sweep usage_count to confirm gate 1 is O(1) (no input-size scaling).
    let mut group = c.benchmark_group("skill_improve_usage_scaling");
    group.sample_size(SAMPLE_SIZE);

    for count in [10u32, 100, 1000, 10000].iter() {
        group.bench_with_input(
            BenchmarkId::from_parameter(format!("usage_{}", count)),
            count,
            |b, &count| {
                let mut registry = SkillRegistry::new();
                let mut ledger = LearningLedger::new();
                let stats = SkillUsageStats::new(count, 0.95);
                let report = clean_report();
                let unknown_skill_id: u128 = 0xDEAD_BEEF;

                b.iter(|| {
                    let r = registry.improve_skill_from_usage(
                        black_box(unknown_skill_id),
                        black_box(&stats),
                        black_box(b"improved-wasm"),
                        black_box(&report),
                        black_box(&mut ledger),
                        None,
                    );
                    // All these usage_counts pass Gate 1; we hit MissingAdmission.
                    assert_eq!(r.unwrap_err(), SkillAdmissionError::MissingAdmission);
                });
            },
        );
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_gate_insufficient_usage,
    bench_usage_count_scaling
);
criterion_main!(benches);
