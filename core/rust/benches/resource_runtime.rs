use aegis_nerve::resource::{
    AdmissionController, AdmissionDecision, CpuRequest, GrantedResources, HardwareProfile,
    MemoryRequest, ResourceRequest, ResourceUsageSample, WorkKind,
};
use aegis_nerve::runtime::{AuthoritativeRuntime, RuntimeAdmission, RuntimeOutcome};
use aegis_nerve::task_ledger::TaskCard;
use criterion::{BenchmarkId, Criterion, Throughput, black_box, criterion_group, criterion_main};
use std::time::Duration;

const SAMPLE_SIZE: usize = 200;
const WARMUP_SECS: u64 = 3;
const MEASURE_SECS: u64 = 10;

fn build_request(task_id: u128, memory_bytes: u64, deps: Vec<u128>) -> (TaskCard, ResourceRequest) {
    let mut request = ResourceRequest::minimal(task_id, WorkKind::NativeTask);
    request.cpu = CpuRequest {
        min_threads: 1,
        max_threads: 2,
    };
    request.host_memory = MemoryRequest {
        bytes: memory_bytes,
    };
    let card = TaskCard::new(task_id, deps, 1, None, Some((task_id % 19) as i64));
    (card, request)
}

fn phase0_criterion_config() -> Criterion {
    Criterion::default()
        .warm_up_time(Duration::from_secs(WARMUP_SECS))
        .measurement_time(Duration::from_secs(MEASURE_SECS))
        .sample_size(SAMPLE_SIZE)
}

fn resource_runtime_benchmarks(c: &mut Criterion) {
    let profile = HardwareProfile::probe();
    let admit_req = build_request(1, 64 * 1024 * 1024, Vec::new()).1;
    let feedback_sample = ResourceUsageSample {
        schema: "aegis-resource-contract-v1".to_string(),
        sampled_at_ms: 100,
        cpu_threads_active: 4,
        host_memory_bytes: Some(4 * 1024 * 1024 * 1024),
        host_memory_available_bytes: Some(512 * 1024 * 1024),
        queue_depth: 16,
        memory_pressure: true,
    };

    let mut group = c.benchmark_group("resource_runtime");
    group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    group.measurement_time(Duration::from_secs(MEASURE_SECS));
    group.sample_size(SAMPLE_SIZE);
    group.throughput(Throughput::Elements(1));

    group.bench_function("SCH-004_resource_admission", |bencher| {
        bencher.iter_batched(
            || {
                let controller = AdmissionController::from_hardware(&profile);
                (controller, admit_req.clone())
            },
            |(mut controller, req)| black_box(controller.admit(black_box(req), black_box(1))),
            criterion::BatchSize::SmallInput,
        );
    });

    let fixed_capacity = GrantedResources {
        cpu_threads: 32,
        host_memory_bytes: 4 * 1024 * 1024 * 1024,
        accelerator_memory_bytes: 0,
        io_in_flight: 32,
        process_limit: 32,
        thread_limit: 64,
        fd_limit: 4096,
    };

    group.bench_function("SCH-005_lease_acquire_release", |bencher| {
        bencher.iter_batched(
            || {
                let mut controller = AdmissionController::new(fixed_capacity, 64);
                let warmup_decision =
                    controller.admit(build_request(99, 1024, Vec::new()).1, 0);
                assert!(matches!(warmup_decision, AdmissionDecision::Admitted(_)));
                (controller, admit_req.clone())
            },
            |(mut controller, req)| {
                let lease = match controller.admit(black_box(req), black_box(1)) {
                    AdmissionDecision::Admitted(lease) => lease,
                    decision => panic!("unexpected admission result: {decision:?}"),
                };
                black_box(controller.release(black_box(lease.lease_id)))
            },
            criterion::BatchSize::SmallInput,
        );
    });

    group.bench_function("SCH-006_capacity_feedback", |bencher| {
        bencher.iter_batched(
            || {
                let mut controller = AdmissionController::new(fixed_capacity, 64);
                for seed in 1..=8u128 {
                    let decision = controller.admit(build_request(seed, 4096, Vec::new()).1, seed as u64);
                    assert!(matches!(decision, AdmissionDecision::Admitted(_)));
                }
                controller
            },
            |mut controller| {
                black_box(controller.capacity_feedback(black_box(&feedback_sample)))
            },
            criterion::BatchSize::SmallInput,
        );
    });

    group.bench_function("SCH-008_runtime_submit_finish", |bencher| {
        bencher.iter_batched(
            || {
                let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
                for seed in 1..=32u128 {
                    let deps = if seed > 1 { vec![seed / 2] } else { Vec::new() };
                    let (card, req) = build_request(seed, 4096, deps);
                    if let Ok(RuntimeAdmission::Admitted(tok)) = runtime.submit(card, req, seed as u64) {
                        let _res = runtime.finish(tok, RuntimeOutcome::Done);
                    }
                }
                let (target_card, target_req) = build_request(1000, 64 * 1024, vec![1]);
                (runtime, target_card, target_req)
            },
            |(mut runtime, card, req)| {
                let admission = runtime
                    .submit(black_box(card), black_box(req), black_box(100))
                    .expect("submit runtime task");
                match admission {
                    RuntimeAdmission::Admitted(token) => {
                        black_box(runtime.finish(black_box(token), RuntimeOutcome::Done))
                    }
                    other => panic!("unexpected runtime admission: {other:?}"),
                }
            },
            criterion::BatchSize::SmallInput,
        );
    });
    group.finish();

    let mut q_group = c.benchmark_group("SCH-007_bounded_queue");
    q_group.warm_up_time(Duration::from_secs(WARMUP_SECS));
    q_group.measurement_time(Duration::from_secs(MEASURE_SECS));
    q_group.sample_size(SAMPLE_SIZE);
    for queue_limit in [8_u32, 32, 128, 512] {
        let total_submits = queue_limit as usize * 2;
        q_group.throughput(Throughput::Elements(total_submits as u64));
        q_group.bench_with_input(
            BenchmarkId::from_parameter(queue_limit),
            &queue_limit,
            |bencher, &limit| {
                let prebuilt_reqs: Vec<ResourceRequest> = (1..=total_submits as u128)
                    .map(|tid| build_request(tid, 1024, Vec::new()).1)
                    .collect();
                bencher.iter_batched(
                    || {
                        let controller = AdmissionController::new(
                            GrantedResources {
                                cpu_threads: 1,
                                host_memory_bytes: 1024,
                                accelerator_memory_bytes: 0,
                                io_in_flight: 1,
                                process_limit: 1,
                                thread_limit: 1,
                                fd_limit: 1,
                            },
                            limit as usize,
                        );
                        (controller, prebuilt_reqs.clone())
                    },
                    |(mut controller, reqs)| {
                        let mut admitted = 0usize;
                        let mut queued = 0usize;
                        let mut rejected = 0usize;
                        for (idx, req) in reqs.into_iter().enumerate() {
                            match controller.admit(black_box(req), (idx as u64) + 1) {
                                AdmissionDecision::Admitted(_) => admitted += 1,
                                AdmissionDecision::Queued { .. } => queued += 1,
                                AdmissionDecision::Rejected { .. } => rejected += 1,
                            }
                        }
                        black_box((admitted, queued, rejected, controller.queued()))
                    },
                    criterion::BatchSize::SmallInput,
                );
            },
        );
    }
    q_group.finish();
}

criterion_group! {
    name = benches;
    config = phase0_criterion_config();
    targets = resource_runtime_benchmarks
}
criterion_main!(benches);
