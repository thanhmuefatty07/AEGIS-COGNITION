use crate::BenchError;
use crate::stats::{
    BcaConfidenceInterval, EstimandKind, bca_confidence_interval_with_min_resamples,
    seed_from_label, uniform_open0_closed1,
};
use serde::{Deserialize, Serialize};
use std::sync::mpsc::{TrySendError, sync_channel};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Instant;

pub const HDR_MAX_VALUE_NS: u64 = 60_000_000_000;
const HDR_SUB_BUCKET_HALF_COUNT: usize = 1024;
const HDR_SUB_BUCKET_COUNT: usize = 2048;
const HDR_BUCKET_COUNT: usize = 26;
const HDR_TOTAL_SLOTS: usize = HDR_SUB_BUCKET_COUNT + HDR_BUCKET_COUNT * HDR_SUB_BUCKET_HALF_COUNT;

/// Logarithmic histogram tracking `1 ns .. 60 s` at 3 decimal significant digits (`< 0.1%` bucket width).
#[derive(Clone, Debug)]
pub struct HdrHistogramLog {
    counts: Vec<u64>,
    total_count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LatencyPercentiles {
    pub count: u64,
    pub mean_ns: f64,
    pub p50_ns: f64,
    pub p90_ns: f64,
    pub p95_ns: f64,
    pub p99_ns: f64,
    pub p99_9_ns: f64,
    pub max_ns: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum QueueRejectionReason {
    QueueFull,
    TimedOut,
    Disconnected,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OpenLoopConfig {
    pub utilization_levels: Vec<f64>,
    pub requests_per_point: usize,
    pub epochs: usize,
    pub queue_capacity: usize,
    pub worker_threads: usize,
    pub request_timeout_ns: u64,
    pub bootstrap_resamples: usize,
    pub alpha: f64,
    pub seed: u64,
}

impl Default for OpenLoopConfig {
    fn default() -> Self {
        Self {
            utilization_levels: vec![0.25, 0.50, 0.75, 0.90, 0.95],
            requests_per_point: 10_000,
            epochs: 30,
            queue_capacity: 4096,
            worker_threads: 1,
            request_timeout_ns: 5_000_000_000,
            bootstrap_resamples: 10_000,
            alpha: 0.05,
            seed: 0xAE61_500E_0001,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OpenLoopCalibration {
    pub mu_max_ops_per_sec: f64,
    pub probe_requests: usize,
    pub elapsed_secs: f64,
    pub peak_queue_depth: usize,
    pub stable: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OpenLoopUtilizationPoint {
    pub utilization_rho: f64,
    pub target_rate_ops_per_sec: f64,
    pub offered: usize,
    pub admitted: usize,
    pub queued: usize,
    pub completed: usize,
    pub rejected_queue_full: usize,
    pub timed_out: usize,
    pub service_latency: LatencyPercentiles,
    pub sojourn_latency: LatencyPercentiles,
    pub service_p99_epoch_ci: Option<BcaConfidenceInterval>,
    pub sojourn_p99_epoch_ci: Option<BcaConfidenceInterval>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OpenLoopReport {
    pub label: String,
    pub calibration: OpenLoopCalibration,
    pub points: Vec<OpenLoopUtilizationPoint>,
    pub coordinated_omission_ratio_p99: f64,
}

impl Default for HdrHistogramLog {
    fn default() -> Self {
        Self::new()
    }
}

impl HdrHistogramLog {
    pub fn new() -> Self {
        Self {
            counts: vec![0u64; HDR_TOTAL_SLOTS],
            total_count: 0,
            sum_ns: 0,
            min_ns: u64::MAX,
            max_ns: 0,
        }
    }

    #[inline]
    fn slot_index(value_ns: u64) -> usize {
        let clamped = value_ns.clamp(1, HDR_MAX_VALUE_NS);
        if clamped < HDR_SUB_BUCKET_COUNT as u64 {
            return clamped as usize;
        }
        let msb = 63 - clamped.leading_zeros();
        let bucket = (msb as usize) - 10;
        if bucket >= HDR_BUCKET_COUNT {
            return HDR_TOTAL_SLOTS - 1;
        }
        let sub = (clamped >> bucket) as usize;
        HDR_SUB_BUCKET_COUNT
            + (bucket - 1) * HDR_SUB_BUCKET_HALF_COUNT
            + (sub - HDR_SUB_BUCKET_HALF_COUNT)
    }

    #[inline]
    fn slot_value_ns(idx: usize) -> f64 {
        if idx < HDR_SUB_BUCKET_COUNT {
            return idx as f64;
        }
        let rem = idx - HDR_SUB_BUCKET_COUNT;
        let bucket = 1 + rem / HDR_SUB_BUCKET_HALF_COUNT;
        let sub = HDR_SUB_BUCKET_HALF_COUNT + (rem % HDR_SUB_BUCKET_HALF_COUNT);
        let low = (sub as u64) << bucket;
        let mid = low + (1u64 << (bucket - 1));
        mid as f64
    }

    pub fn record_ns(&mut self, value_ns: u64) {
        let clamped = value_ns.min(HDR_MAX_VALUE_NS);
        let idx = Self::slot_index(clamped.max(1));
        self.counts[idx] += 1;
        self.total_count += 1;
        self.sum_ns += u128::from(clamped);
        if clamped < self.min_ns {
            self.min_ns = clamped;
        }
        if clamped > self.max_ns {
            self.max_ns = clamped;
        }
    }

    pub fn quantile(&self, q: f64) -> f64 {
        if self.total_count == 0 {
            return 0.0;
        }
        let clamped_q = q.clamp(0.0, 1.0);
        if clamped_q == 0.0 {
            return self.min_ns as f64;
        }
        if clamped_q >= 1.0 {
            return self.max_ns as f64;
        }
        let target_rank = ((clamped_q * (self.total_count as f64)).ceil() as u64).max(1);
        let mut cumulative = 0u64;
        for (idx, &count) in self.counts.iter().enumerate() {
            if count == 0 {
                continue;
            }
            cumulative += count;
            if cumulative >= target_rank {
                return Self::slot_value_ns(idx).clamp(self.min_ns as f64, self.max_ns as f64);
            }
        }
        self.max_ns as f64
    }

    pub fn summary(&self) -> LatencyPercentiles {
        let mean_ns = if self.total_count == 0 {
            0.0
        } else {
            (self.sum_ns as f64) / (self.total_count as f64)
        };
        LatencyPercentiles {
            count: self.total_count,
            mean_ns,
            p50_ns: self.quantile(0.50),
            p90_ns: self.quantile(0.90),
            p95_ns: self.quantile(0.95),
            p99_ns: self.quantile(0.99),
            p99_9_ns: self.quantile(0.999),
            max_ns: self.max_ns as f64,
        }
    }
}

pub fn generate_poisson_arrivals_ns(
    count: usize,
    rate_ops_per_sec: f64,
    seed: u64,
) -> Result<Vec<u64>, BenchError> {
    if count == 0 {
        return Err(BenchError::InvalidParameter(
            "arrival count must be positive",
        ));
    }
    if !rate_ops_per_sec.is_finite() || rate_ops_per_sec <= 0.0 {
        return Err(BenchError::InvalidParameter(
            "arrival rate must be finite and positive",
        ));
    }
    let mut state = seed_from_label("poisson_arrivals", count, seed);
    let mut out = Vec::with_capacity(count);
    let mut t_ns = 0.0f64;
    let scale_ns = 1_000_000_000.0 / rate_ops_per_sec;
    for _ in 0..count {
        let u = uniform_open0_closed1(&mut state);
        let delta_ns = (-u.ln()) * scale_ns;
        t_ns += delta_ns;
        out.push(t_ns.round() as u64);
    }
    Ok(out)
}

#[derive(Clone, Debug)]
pub struct OpenLoopQueueSimulation {
    pub service_hist: HdrHistogramLog,
    pub sojourn_hist: HdrHistogramLog,
    pub admitted: usize,
    pub queued: usize,
    pub completed: usize,
    pub rejected_queue_full: usize,
    pub timed_out: usize,
}

/// Deterministic queued execution model checking service vs sojourn times without coordinated omission.
pub fn simulate_open_loop_queue(
    service_times_ns: &[u64],
    rate_ops_per_sec: f64,
    workers: usize,
    queue_capacity: usize,
    timeout_ns: u64,
    seed: u64,
) -> Result<OpenLoopQueueSimulation, BenchError> {
    if service_times_ns.is_empty() {
        return Err(BenchError::EmptySamples("open_loop_service".to_string()));
    }
    if workers == 0 || queue_capacity == 0 {
        return Err(BenchError::InvalidParameter(
            "workers and queue_capacity must be >= 1",
        ));
    }
    let n = service_times_ns.len();
    let arrivals = generate_poisson_arrivals_ns(n, rate_ops_per_sec, seed)?;
    let mut worker_free_at = vec![0u64; workers];
    let mut in_flight_departures: Vec<u64> = Vec::with_capacity(queue_capacity + workers);
    let mut service_hist = HdrHistogramLog::new();
    let mut sojourn_hist = HdrHistogramLog::new();

    let mut admitted = 0usize;
    let mut queued = 0usize;
    let mut completed = 0usize;
    let mut rejected_queue_full = 0usize;
    let mut timed_out = 0usize;

    for (&sched_ns, &svc_ns) in arrivals.iter().zip(service_times_ns.iter()) {
        in_flight_departures.retain(|&dep| dep > sched_ns);
        let queued_now = in_flight_departures.len().saturating_sub(workers);
        if queued_now >= queue_capacity {
            rejected_queue_full += 1;
            continue;
        }
        admitted += 1;
        // Find earliest available worker
        let (worker_idx, &earliest_free) = worker_free_at
            .iter()
            .enumerate()
            .min_by_key(|&(_, t)| *t)
            .ok_or(BenchError::InvalidParameter("no workers"))?;
        let start_ns = sched_ns.max(earliest_free);
        let queue_wait = start_ns - sched_ns;
        if queue_wait > 0 {
            queued += 1;
        }
        if queue_wait + svc_ns > timeout_ns {
            timed_out += 1;
            continue;
        }
        let end_ns = start_ns + svc_ns;
        worker_free_at[worker_idx] = end_ns;
        in_flight_departures.push(end_ns);
        completed += 1;
        service_hist.record_ns(svc_ns.max(1));
        sojourn_hist.record_ns((end_ns - sched_ns).max(1));
    }

    Ok(OpenLoopQueueSimulation {
        service_hist,
        sojourn_hist,
        admitted,
        queued,
        completed,
        rejected_queue_full,
        timed_out,
    })
}

#[derive(Clone, Debug)]
struct QueuedRequest {
    scheduled_ns: u64,
}

/// Real concurrent open-loop harness pacing Poisson arrivals onto worker threads.
pub struct OpenLoopHarness;

impl OpenLoopHarness {
    pub fn calibrate_capacity<F>(
        probe_requests: usize,
        mut op: F,
    ) -> Result<OpenLoopCalibration, BenchError>
    where
        F: FnMut(),
    {
        if probe_requests == 0 {
            return Err(BenchError::InvalidParameter(
                "calibration probe_requests must be >= 1",
            ));
        }
        let warmup = probe_requests.clamp(16, 256);
        for _ in 0..warmup {
            op();
        }
        let start = Instant::now();
        for _ in 0..probe_requests {
            op();
        }
        let elapsed_secs = start.elapsed().as_secs_f64().max(1e-9);
        let mu_max = (probe_requests as f64) / elapsed_secs;
        Ok(OpenLoopCalibration {
            mu_max_ops_per_sec: mu_max,
            probe_requests,
            elapsed_secs,
            peak_queue_depth: 1,
            stable: mu_max.is_finite() && mu_max > 0.0,
        })
    }

    pub fn sweep_closure<F, Factory>(
        label: &str,
        config: &OpenLoopConfig,
        factory: Factory,
    ) -> Result<OpenLoopReport, BenchError>
    where
        Factory: Fn() -> F + Send + Sync,
        F: FnMut() + Send,
    {
        if config.utilization_levels.is_empty()
            || config
                .utilization_levels
                .iter()
                .any(|&u| !u.is_finite() || u <= 0.0 || u >= 1.5)
        {
            return Err(BenchError::InvalidParameter(
                "utilization levels must be finite and in (0, 1.5)",
            ));
        }
        if config.requests_per_point < 30 || config.epochs == 0 || config.queue_capacity == 0 {
            return Err(BenchError::InvalidParameter(
                "requests_per_point >= 30, epochs >= 1, queue_capacity >= 1 required",
            ));
        }
        let mut cal_op = factory();
        let calibration =
            Self::calibrate_capacity(config.requests_per_point.clamp(64, 512), &mut cal_op)?;
        let total_workers = config.worker_threads.max(1);
        let capacity_total = calibration.mu_max_ops_per_sec * (total_workers as f64);

        let mut points = Vec::with_capacity(config.utilization_levels.len());
        let mut max_co_ratio = 1.0f64;

        for (level_idx, &rho) in config.utilization_levels.iter().enumerate() {
            let target_rate = (capacity_total * rho).max(1.0);
            let arrivals = generate_poisson_arrivals_ns(
                config.requests_per_point,
                target_rate,
                config.seed ^ ((level_idx as u64 + 1) * 0x9E37_79B9),
            )?;

            let service_hist = Arc::new(Mutex::new(HdrHistogramLog::new()));
            let sojourn_hist = Arc::new(Mutex::new(HdrHistogramLog::new()));
            let completed_vec = Arc::new(Mutex::new(Vec::<(usize, u64, u64)>::with_capacity(
                config.requests_per_point,
            )));
            let timed_out_count = Arc::new(Mutex::new(0usize));

            let (tx, rx) = sync_channel::<QueuedRequest>(config.queue_capacity);
            let shared_rx = Arc::new(Mutex::new(rx));
            let epoch_count = config.epochs.max(1);
            let per_epoch = (config.requests_per_point / epoch_count).max(1);

            let mut admitted = 0usize;
            let mut queued = 0usize;
            let mut rejected_queue_full = 0usize;
            let mut disconnected = 0usize;
            let mut worker_panicked = false;

            let run_start = Instant::now();
            thread::scope(|scope| {
                let mut handles = Vec::with_capacity(total_workers);
                for _ in 0..total_workers {
                    let rx_clone = Arc::clone(&shared_rx);
                    let svc_h = Arc::clone(&service_hist);
                    let soj_h = Arc::clone(&sojourn_hist);
                    let comp_v = Arc::clone(&completed_vec);
                    let to_cnt = Arc::clone(&timed_out_count);
                    let timeout_ns = config.request_timeout_ns;
                    let mut worker_op = factory();

                    handles.push(scope.spawn(move || {
                        loop {
                            let req = {
                                let Ok(guard) = rx_clone.lock() else {
                                    break;
                                };
                                match guard.recv() {
                                    Ok(item) => item,
                                    Err(_) => break,
                                }
                            };
                            let start = Instant::now();
                            worker_op();
                            let end = Instant::now();
                            let svc_ns = end.duration_since(start).as_nanos() as u64;
                            let end_rel_ns = end.duration_since(run_start).as_nanos() as u64;
                            let soj_ns = end_rel_ns.saturating_sub(req.scheduled_ns).max(svc_ns);
                            if soj_ns > timeout_ns {
                                if let Ok(mut guard) = to_cnt.lock() {
                                    *guard += 1;
                                }
                                continue;
                            }
                            if let Ok(mut g) = svc_h.lock() {
                                g.record_ns(svc_ns.max(1));
                            }
                            if let Ok(mut g) = soj_h.lock() {
                                g.record_ns(soj_ns.max(1));
                            }
                            if let Ok(mut g) = comp_v.lock() {
                                let epoch_idx = g.len() / per_epoch;
                                g.push((epoch_idx, svc_ns.max(1), soj_ns.max(1)));
                            }
                        }
                    }));
                }

                for sched_ns in arrivals {
                    loop {
                        let elapsed = run_start.elapsed().as_nanos() as u64;
                        if elapsed >= sched_ns {
                            break;
                        }
                        let remaining = sched_ns - elapsed;
                        if remaining > 250_000 {
                            thread::sleep(std::time::Duration::from_nanos(remaining - 100_000));
                        } else {
                            std::hint::spin_loop();
                        }
                    }
                    if (run_start.elapsed().as_nanos() as u64) > sched_ns + 1_000 {
                        queued += 1;
                    }
                    match tx.try_send(QueuedRequest {
                        scheduled_ns: sched_ns,
                    }) {
                        Ok(()) => admitted += 1,
                        Err(TrySendError::Full(_)) => rejected_queue_full += 1,
                        Err(TrySendError::Disconnected(_)) => disconnected += 1,
                    }
                }
                drop(tx);
                for handle in handles {
                    if handle.join().is_err() {
                        worker_panicked = true;
                    }
                }
            });
            if worker_panicked {
                return Err(BenchError::GateFailed(
                    "open-loop worker thread panicked".to_string(),
                ));
            }

            let svc_summary = service_hist
                .lock()
                .map_err(|_| BenchError::GateFailed("service histogram lock poisoned".to_string()))?
                .summary();
            let soj_summary = sojourn_hist
                .lock()
                .map_err(|_| BenchError::GateFailed("sojourn histogram lock poisoned".to_string()))?
                .summary();
            let timed_out = *timed_out_count
                .lock()
                .map_err(|_| BenchError::GateFailed("timeout lock poisoned".to_string()))?
                + disconnected;

            let samples_by_epoch = completed_vec
                .lock()
                .map_err(|_| BenchError::GateFailed("epoch samples lock poisoned".to_string()))?;
            let mut epoch_svc_hists = vec![HdrHistogramLog::new(); epoch_count];
            let mut epoch_soj_hists = vec![HdrHistogramLog::new(); epoch_count];
            for &(ep, svc, soj) in samples_by_epoch.iter() {
                let idx = ep.min(epoch_count - 1);
                epoch_svc_hists[idx].record_ns(svc);
                epoch_soj_hists[idx].record_ns(soj);
            }
            let epoch_svc_p99: Vec<f64> = epoch_svc_hists
                .iter()
                .filter(|h| h.total_count > 0)
                .map(|h| h.quantile(0.99))
                .collect();
            let epoch_soj_p99: Vec<f64> = epoch_soj_hists
                .iter()
                .filter(|h| h.total_count > 0)
                .map(|h| h.quantile(0.99))
                .collect();
            let min_boot = config.bootstrap_resamples.min(200);
            let service_p99_epoch_ci = if epoch_svc_p99.len() >= 2 {
                Some(bca_confidence_interval_with_min_resamples(
                    &format!("{label}::rho_{rho:.2}::service_p99"),
                    &epoch_svc_p99,
                    EstimandKind::Mean,
                    config.alpha,
                    config.bootstrap_resamples,
                    min_boot,
                    config.seed,
                )?)
            } else {
                None
            };
            let sojourn_p99_epoch_ci = if epoch_soj_p99.len() >= 2 {
                Some(bca_confidence_interval_with_min_resamples(
                    &format!("{label}::rho_{rho:.2}::sojourn_p99"),
                    &epoch_soj_p99,
                    EstimandKind::Mean,
                    config.alpha,
                    config.bootstrap_resamples,
                    min_boot,
                    config.seed ^ 0x77,
                )?)
            } else {
                None
            };

            if svc_summary.p99_ns > 0.0 {
                max_co_ratio = max_co_ratio.max(soj_summary.p99_ns / svc_summary.p99_ns);
            }
            points.push(OpenLoopUtilizationPoint {
                utilization_rho: rho,
                target_rate_ops_per_sec: target_rate,
                offered: config.requests_per_point,
                admitted,
                queued,
                completed: samples_by_epoch.len(),
                rejected_queue_full,
                timed_out,
                service_latency: svc_summary,
                sojourn_latency: soj_summary,
                service_p99_epoch_ci,
                sojourn_p99_epoch_ci,
            });
        }

        Ok(OpenLoopReport {
            label: label.to_string(),
            calibration,
            points,
            coordinated_omission_ratio_p99: max_co_ratio,
        })
    }
}
