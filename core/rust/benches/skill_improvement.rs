//! Skill Improvement Benchmarks
//!
//! Benchmarks both Gate-1/admission rejection paths and full admitted skill
//! improvements across realistic registry sizes (`1`, `100`, `1_000` admitted skills)
//! using non-trivial Wasmtime-verified modules and explicit Criterion sample counts.

use aegis_nerve::learning::LearningLedger;
use aegis_nerve::licensing::LicenseManager;
use aegis_nerve::physical::{PhysicalWatchdog, blake3_digest};
use aegis_nerve::sandbox::WasmtimeSandbox;
use aegis_nerve::skill_registry::{
    SkillAdmissionError, SkillAdmissionRecord, SkillPackageManifest, SkillRegistry,
    SkillRegressionCase, SkillRegressionReport, SkillUsageStats,
};
use criterion::{BenchmarkId, Criterion, Throughput, black_box, criterion_group, criterion_main};
use std::time::Duration;

const SAMPLE_SIZE: usize = 200;
const WARMUP_SECS: u64 = 3;
const MEASURE_SECS: u64 = 10;

fn bench_wasm_bytes(salt: u8) -> Vec<u8> {
    let wat = format!(
        r#"(module
            (memory (export "memory") 1)
            (func (export "_start")
                (local $i i32)
                (local.set $i (i32.const 0))
                (loop $l
                    (i32.store (i32.mul (local.get $i) (i32.const 4)) (i32.add (local.get $i) (i32.const {salt})))
                    (local.set $i (i32.add (local.get $i) (i32.const 1)))
                    (br_if $l (i32.lt_u (local.get $i) (i32.const 64))))))"#
    );
    wat::parse_str(&wat).expect("valid skill WAT module")
}

fn clean_report() -> SkillRegressionReport {
    let case = SkillRegressionCase::new(1, [0x11u8; 32], [0x22u8; 32], [0x22u8; 32], true)
        .expect("clean regression case must construct");
    SkillRegressionReport::new(std::slice::from_ref(&case))
        .expect("clean regression report must construct")
}

fn populated_registry(
    skill_count: usize,
) -> (SkillRegistry, Vec<u128>, SkillRegressionReport, Vec<u8>) {
    let wasm = bench_wasm_bytes(1);
    let improved_wasm = bench_wasm_bytes(9);
    let sandbox = WasmtimeSandbox::new();
    let sandbox_result = sandbox
        .execute_wasm_binary(&wasm, 50_000)
        .expect("execute baseline skill wasm");
    let report = clean_report();
    let watchdog = PhysicalWatchdog { epsilon: 0.0 };

    let mut registry = SkillRegistry::new();
    let mut ids = Vec::with_capacity(skill_count);
    for idx in 1..=skill_count {
        let skill_id = 10_000u128 + idx as u128;
        let name_hash = blake3_digest(format!("bench-skill-name-{idx}").as_bytes());
        let ver_hash = blake3_digest(format!("bench-skill-ver-{idx}").as_bytes());
        let md_hash = blake3_digest(format!("bench-skill-md-{idx}").as_bytes());
        let pol_hash = blake3_digest(format!("bench-skill-pol-{idx}").as_bytes());
        let manifest = SkillPackageManifest::new(
            skill_id,
            name_hash,
            ver_hash,
            md_hash,
            sandbox_result.artifact.artifact_hash,
            pol_hash,
            report.case_list_hash,
        )
        .expect("bench manifest");
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &report,
            &watchdog,
        )
        .expect("bench admission");
        registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .expect("commit admitted skill");
        ids.push(skill_id);
    }
    (registry, ids, report, improved_wasm)
}

fn phase0_criterion_config() -> Criterion {
    Criterion::default()
        .warm_up_time(Duration::from_secs(WARMUP_SECS))
        .measurement_time(Duration::from_secs(MEASURE_SECS))
        .sample_size(SAMPLE_SIZE)
}

fn bench_admitted_skill_improvements(c: &mut Criterion) {
    let mut group = c.benchmark_group("skill_improve_admitted");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);
    group.throughput(Throughput::Elements(1));

    let adequate = SkillUsageStats::new(50, 0.95);
    for &registry_size in &[1usize, 100, 1_000] {
        let (base_registry, ids, report, improved_wasm) = populated_registry(registry_size);
        let target_id = ids[ids.len() / 2];
        group.bench_with_input(
            BenchmarkId::from_parameter(registry_size),
            &registry_size,
            |b, _| {
                b.iter_batched(
                    || (base_registry.clone(), LearningLedger::new()),
                    |(mut registry, mut ledger)| {
                        let rec = registry
                            .improve_skill_from_usage(
                                black_box(target_id),
                                black_box(&adequate),
                                black_box(&improved_wasm),
                                black_box(&report),
                                black_box(&mut ledger),
                                black_box(None),
                            )
                            .expect("admitted skill improvement must succeed");
                        black_box((rec.improvement_hash, ledger.len()))
                    },
                    criterion::BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();
}

fn bench_skill_improvement_rejection_gates(c: &mut Criterion) {
    let mut group = c.benchmark_group("skill_improve_gates");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);
    group.throughput(Throughput::Elements(1));

    let low_stats = SkillUsageStats::new(5, 0.95);
    let good_stats = SkillUsageStats::new(100, 0.95);
    let report = clean_report();
    let (base_registry, ids, _, improved_wasm) = populated_registry(64);
    let admitted_id = ids[0];
    let unknown_id = 0xDEAD_BEEFu128;
    let license = LicenseManager::new();

    group.bench_function("gate1_insufficient_usage_admitted", |b| {
        b.iter_batched(
            || (base_registry.clone(), LearningLedger::new()),
            |(mut registry, mut ledger)| {
                let outcome = registry.improve_skill_from_usage(
                    black_box(admitted_id),
                    black_box(&low_stats),
                    black_box(&improved_wasm),
                    black_box(&report),
                    black_box(&mut ledger),
                    black_box(None),
                );
                let is_expected = matches!(outcome, Err(SkillAdmissionError::InsufficientUsage));
                black_box(is_expected)
            },
            criterion::BatchSize::SmallInput,
        );
    });

    group.bench_function("gates12_lookup_missing_admission", |b| {
        b.iter_batched(
            || (base_registry.clone(), LearningLedger::new()),
            |(mut registry, mut ledger)| {
                let outcome = registry.improve_skill_from_usage(
                    black_box(unknown_id),
                    black_box(&good_stats),
                    black_box(&improved_wasm),
                    black_box(&report),
                    black_box(&mut ledger),
                    black_box(None),
                );
                let is_expected = matches!(outcome, Err(SkillAdmissionError::MissingAdmission));
                black_box(is_expected)
            },
            criterion::BatchSize::SmallInput,
        );
    });

    group.bench_function("license_gate_community_blocked", |b| {
        b.iter_batched(
            || (base_registry.clone(), LearningLedger::new()),
            |(mut registry, mut ledger)| {
                let outcome = registry.improve_skill_from_usage(
                    black_box(admitted_id),
                    black_box(&good_stats),
                    black_box(&improved_wasm),
                    black_box(&report),
                    black_box(&mut ledger),
                    black_box(Some(&license)),
                );
                let is_expected = matches!(outcome, Err(SkillAdmissionError::LicenseRequired(_)));
                black_box(is_expected)
            },
            criterion::BatchSize::SmallInput,
        );
    });

    group.finish();
}

criterion_group! {
    name = benches;
    config = phase0_criterion_config();
    targets = bench_admitted_skill_improvements, bench_skill_improvement_rejection_gates
}
criterion_main!(benches);
