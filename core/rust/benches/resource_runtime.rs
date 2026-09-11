use aegis_nerve::resource::{
    AdmissionController, AdmissionDecision, CpuRequest, HardwareProfile, MemoryRequest,
    ResourceRequest, ResourceUsageSample, WorkKind,
};
use aegis_nerve::runtime::{AuthoritativeRuntime, RuntimeOutcome};
use aegis_nerve::task_ledger::TaskCard;
use criterion::{BenchmarkId, Criterion, Throughput, black_box, criterion_group, criterion_main};

fn request(task_id: u128) -> ResourceRequest {
    let mut request = ResourceRequest::minimal(task_id, WorkKind::NativeTask);
    request.cpu = CpuRequest {
        min_threads: 1,
        max_threads: 1,
    };
    request.host_memory = MemoryRequest { bytes: 1 };
    request
}

fn resource_runtime_benchmarks(c: &mut Criterion) {
    let profile = HardwareProfile::probe();

    c.bench_function("SCH-004_resource_admission", |bencher| {
        bencher.iter_batched(
            || AdmissionController::from_hardware(&profile),
            |mut controller| black_box(controller.admit(request(1), 1)),
            criterion::BatchSize::SmallInput,
        );
    });

    c.bench_function("SCH-005_lease_acquire_release", |bencher| {
        bencher.iter_batched(
            || AdmissionController::from_hardware(&profile),
            |mut controller| {
                let lease = match controller.admit(request(1), 1) {
                    AdmissionDecision::Admitted(lease) => lease,
                    decision => panic!("unexpected admission result: {decision:?}"),
                };
                black_box(controller.release(lease.lease_id))
            },
            criterion::BatchSize::SmallInput,
        );
    });

    c.bench_function("SCH-006_capacity_feedback", |bencher| {
        bencher.iter_batched(
            || AdmissionController::from_hardware(&profile),
            |mut controller| {
                let sample = ResourceUsageSample {
                    schema: "aegis-resource-contract-v1".to_string(),
                    sampled_at_ms: 1,
                    cpu_threads_active: 1,
                    host_memory_bytes: Some(1),
                    host_memory_available_bytes: Some(1),
                    queue_depth: 1,
                    memory_pressure: true,
                };
                black_box(controller.capacity_feedback(&sample))
            },
            criterion::BatchSize::SmallInput,
        );
    });

    let mut group = c.benchmark_group("SCH-007_bounded_queue");
    for queue_limit in [1_u32, 8, 32] {
        group.throughput(Throughput::Elements(u64::from(queue_limit)));
        group.bench_with_input(
            BenchmarkId::from_parameter(queue_limit),
            &queue_limit,
            |bencher, queue_limit| {
                bencher.iter_batched(
                    || {
                        AdmissionController::new(
                            aegis_nerve::resource::GrantedResources {
                                cpu_threads: 1,
                                host_memory_bytes: 1,
                                accelerator_memory_bytes: 0,
                                io_in_flight: 1,
                                process_limit: 1,
                                thread_limit: 1,
                                fd_limit: 1,
                            },
                            *queue_limit as usize,
                        )
                    },
                    |mut controller| {
                        for task_id in 1..=*queue_limit as u128 + 1 {
                            let _ = controller.admit(request(task_id), task_id as u64);
                        }
                        black_box(controller.queued())
                    },
                    criterion::BatchSize::SmallInput,
                );
            },
        );
    }
    group.finish();

    c.bench_function("SCH-008_runtime_submit_finish", |bencher| {
        bencher.iter_batched(
            || AuthoritativeRuntime::new(60_000, &profile),
            |mut runtime| {
                let task = TaskCard::new(1, vec![], 0, None, None);
                let token = match runtime.submit(task, request(1), 1).unwrap() {
                    aegis_nerve::runtime::RuntimeAdmission::Admitted(token) => token,
                    admission => panic!("unexpected runtime admission: {admission:?}"),
                };
                black_box(runtime.finish(token, RuntimeOutcome::Done))
            },
            criterion::BatchSize::SmallInput,
        );
    });
}

criterion_group!(benches, resource_runtime_benchmarks);
criterion_main!(benches);
