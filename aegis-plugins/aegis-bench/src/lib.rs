pub mod environment;
pub mod open_loop;
pub mod stats;

pub use environment::{ContaminationVerdict, EnvironmentCheckReport, EnvironmentFingerprintV3};
pub use open_loop::{
    HDR_MAX_VALUE_NS, HdrHistogramLog, LatencyPercentiles, OpenLoopCalibration, OpenLoopConfig,
    OpenLoopHarness, OpenLoopQueueSimulation, OpenLoopReport, OpenLoopUtilizationPoint,
    QueueRejectionReason, generate_poisson_arrivals_ns, simulate_open_loop_queue,
};
pub use stats::{
    BcaConfidenceInterval, CliffMagnitude, CliffsDeltaResult, DEFAULT_BOOTSTRAP_RESAMPLES,
    DistributionV3, EstimandKind, JACKKNIFE_BLOCK_COUNT, MIN_DEV_BOOTSTRAP_RESAMPLES,
    MannWhitneyResult, McNemarResult, NewcombeWilsonDiffCi, PairedComparisonV3,
    PairedEstimandComparisonV3, SensitivitySelfTestReport, SesoiPolicy, WilcoxonResult,
    WilsonScoreCi, apply_holm_bonferroni_to_comparisons, bca_confidence_interval, cliffs_delta,
    evaluate_paired_comparison_v3, exact_mcnemar_test, hodges_lehmann_pseudomedian,
    holm_bonferroni, lemire_bounded_index, mann_whitney_u_test, newcombe_wilson_diff_ci,
    normal_cdf, normal_inv_cdf, pass_at_k_with_bca_ci, quantile_type7_sorted,
    run_canary_sensitivity_self_test, splitmix64_next, unbiased_pass_at_k,
    wilcoxon_signed_rank_test, wilson_score_ci,
};

use serde::{Deserialize, Serialize};
use stats::bca_confidence_interval_with_min_resamples;
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum BenchError {
    #[error("metric missing: {0}")]
    MissingMetric(String),
    #[error("empty sample slice for metric: {0}")]
    EmptySamples(String),
    #[error("invalid sample value for metric: {0}")]
    InvalidSample(String),
    #[error("performance gate failed: {0}")]
    GateFailed(String),
    #[error("insufficient samples for {metric}: required {required}, got {actual}")]
    InsufficientSamples {
        metric: String,
        required: usize,
        actual: usize,
    },
    #[error("invalid benchmark parameter: {0}")]
    InvalidParameter(&'static str),
    #[error("benchmark environment contaminated: {0}")]
    Contaminated(String),
    #[error("benchmark environment observations unavailable: {0}")]
    IncompleteEnvironment(String),
    #[error("benchmark sensitivity check failed: {0}")]
    SensitivityCheckFailed(String),
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EmpiricalDistribution {
    pub sample_count: usize,
    pub mean_ns: f64,
    pub stddev_ns: f64,
    pub min_ns: f64,
    pub p50_ns: f64,
    pub p95_ns: f64,
    pub p99_ns: f64,
    pub max_ns: f64,
    pub bootstrap_p95_ci_low_ns: f64,
    pub bootstrap_p95_ci_high_ns: f64,
}

impl EmpiricalDistribution {
    /// Compute empirical quantiles (`p50`, `p95`, `p99`) and a deterministic
    /// 95% BCa bootstrap confidence interval for `p95_ns` (`resamples >= 200`,
    /// defaulting higher in V3 via `DistributionV3::from_samples`).
    pub fn from_samples(
        metric_name: &str,
        samples_ns: &[f64],
        bootstrap_resamples: usize,
    ) -> Result<Self, BenchError> {
        if samples_ns.is_empty() {
            return Err(BenchError::EmptySamples(metric_name.to_string()));
        }
        if samples_ns.iter().any(|v| !v.is_finite() || *v < 0.0) {
            return Err(BenchError::InvalidSample(metric_name.to_string()));
        }
        let mut sorted = samples_ns.to_vec();
        sorted.sort_by(|a, b| a.total_cmp(b));
        let n = sorted.len();
        let mean_ns = sorted.iter().sum::<f64>() / (n as f64);
        let variance = if n > 1 {
            sorted
                .iter()
                .map(|x| {
                    let d = *x - mean_ns;
                    d * d
                })
                .sum::<f64>()
                / ((n - 1) as f64)
        } else {
            0.0
        };
        let stddev_ns = variance.sqrt();
        let min_ns = sorted[0];
        let max_ns = sorted[n - 1];
        let p50_ns = quantile_type7_sorted(&sorted, 0.50);
        let p95_ns = quantile_type7_sorted(&sorted, 0.95);
        let p99_ns = quantile_type7_sorted(&sorted, 0.99);

        let (bootstrap_p95_ci_low_ns, bootstrap_p95_ci_high_ns) = if n < 2 {
            (p95_ns, p95_ns)
        } else {
            let resamples = bootstrap_resamples.max(200);
            let ci = bca_confidence_interval_with_min_resamples(
                metric_name,
                samples_ns,
                EstimandKind::P95,
                0.05,
                resamples,
                200,
                0xAE61_5BEE,
            )?;
            (ci.ci_low, ci.ci_high)
        };

        Ok(Self {
            sample_count: n,
            mean_ns,
            stddev_ns,
            min_ns,
            p50_ns,
            p95_ns,
            p99_ns,
            max_ns,
            bootstrap_p95_ci_low_ns,
            bootstrap_p95_ci_high_ns,
        })
    }
}

/// Measure a real closure over `warmup` + `iterations` trials using `Instant::now()`
/// and return its empirical latency distribution (`p50/p95/p99` + 95% BCa CI).
pub fn measure_closure_distribution<F>(
    metric_name: &str,
    warmup: usize,
    iterations: usize,
    mut op: F,
) -> Result<EmpiricalDistribution, BenchError>
where
    F: FnMut(),
{
    let trials = iterations.max(1);
    for _ in 0..warmup {
        std::hint::black_box(&op);
        op();
    }
    let mut samples = Vec::with_capacity(trials);
    for _ in 0..trials {
        let start = std::time::Instant::now();
        op();
        samples.push(start.elapsed().as_secs_f64() * 1_000_000_000.0);
    }
    EmpiricalDistribution::from_samples(metric_name, &samples, MIN_DEV_BOOTSTRAP_RESAMPLES)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BenchMetric {
    pub name: String,
    pub estimate_ns: f64,
    pub threshold_ns: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BenchReport {
    pub suite_name: String,
    pub metrics: Vec<BenchMetric>,
}

#[derive(Clone, Debug, Default)]
pub struct PerformanceGates {
    thresholds_ns: BTreeMap<String, f64>,
}

impl BenchMetric {
    pub fn passes(&self) -> bool {
        self.estimate_ns <= self.threshold_ns
    }
}

impl PerformanceGates {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_gate(mut self, name: impl Into<String>, threshold_ns: f64) -> Self {
        self.thresholds_ns.insert(name.into(), threshold_ns);
        self
    }

    pub fn plugin_defaults() -> Self {
        Self::new()
            .with_gate("search_single_operation_p95", 50_000_000.0)
            .with_gate("browser_dom_interaction_p95", 100_000_000.0)
            .with_gate("sandbox_startup_p95", 100_000_000.0)
            .with_gate("evidence_binding_verify_p95", 1_000_000.0)
            .with_gate("skill_injection_p95", 5_000_000.0)
    }

    pub fn evaluate(
        &self,
        measurements: &BTreeMap<String, f64>,
    ) -> Result<BenchReport, BenchError> {
        let mut metrics = Vec::new();
        for (name, threshold_ns) in &self.thresholds_ns {
            let estimate_ns = measurements
                .get(name)
                .copied()
                .ok_or_else(|| BenchError::MissingMetric(name.clone()))?;
            let metric = BenchMetric {
                name: name.clone(),
                estimate_ns,
                threshold_ns: *threshold_ns,
            };
            if !metric.passes() {
                return Err(BenchError::GateFailed(name.clone()));
            }
            metrics.push(metric);
        }
        Ok(BenchReport {
            suite_name: "AEGIS Plugin Gates".to_string(),
            metrics,
        })
    }

    /// Evaluate gates directly against raw trial sample vectors, gating on empirical
    /// `p95_ns` (and returning the full `EmpiricalDistribution` map alongside `BenchReport`).
    pub fn evaluate_sample_distributions(
        &self,
        raw_samples: &BTreeMap<String, Vec<f64>>,
        gate_on_ci_upper: bool,
    ) -> Result<(BenchReport, BTreeMap<String, EmpiricalDistribution>), BenchError> {
        let mut metrics = Vec::with_capacity(self.thresholds_ns.len());
        let mut distributions = BTreeMap::new();
        for (name, threshold_ns) in &self.thresholds_ns {
            let samples = raw_samples
                .get(name)
                .ok_or_else(|| BenchError::MissingMetric(name.clone()))?;
            let dist =
                EmpiricalDistribution::from_samples(name, samples, MIN_DEV_BOOTSTRAP_RESAMPLES)?;
            let estimate_ns = if gate_on_ci_upper {
                dist.bootstrap_p95_ci_high_ns
            } else {
                dist.p95_ns
            };
            let metric = BenchMetric {
                name: name.clone(),
                estimate_ns,
                threshold_ns: *threshold_ns,
            };
            if !metric.passes() {
                return Err(BenchError::GateFailed(name.clone()));
            }
            distributions.insert(name.clone(), dist);
            metrics.push(metric);
        }
        Ok((
            BenchReport {
                suite_name: "AEGIS Empirical Plugin Gates".to_string(),
                metrics,
            },
            distributions,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_gates_pass_with_bounded_measurements() {
        let gates = PerformanceGates::plugin_defaults();
        let measurements = BTreeMap::from([
            ("search_single_operation_p95".to_string(), 10_000_000.0),
            ("browser_dom_interaction_p95".to_string(), 10_000_000.0),
            ("sandbox_startup_p95".to_string(), 10_000_000.0),
            ("evidence_binding_verify_p95".to_string(), 500_000.0),
            ("skill_injection_p95".to_string(), 1_000_000.0),
        ]);
        let report = gates.evaluate(&measurements).unwrap();
        assert_eq!(report.metrics.len(), 5);
    }

    #[test]
    fn normal_cdf_and_inv_cdf_match_reference_precision() {
        assert!((normal_cdf(0.0) - 0.5).abs() < 1e-15);
        assert!((normal_cdf(1.959963984540054) - 0.975).abs() < 1e-12);
        assert!((normal_cdf(-2.5758293035489004) - 0.005).abs() < 1e-12);
        for p in [0.001, 0.025, 0.1, 0.5, 0.9, 0.975, 0.999] {
            let z = normal_inv_cdf(p);
            assert!((normal_cdf(z) - p).abs() < 1e-12, "p={p}, z={z}");
        }
    }

    #[test]
    fn lemire_unbiased_sampler_covers_range_uniformly() {
        let mut state = 0x1234_5678_9ABC_DEF0u64;
        let bound = 7usize;
        let mut counts = [0usize; 7];
        for _ in 0..70_000 {
            let idx = lemire_bounded_index(&mut state, bound);
            counts[idx] += 1;
        }
        for (i, &c) in counts.iter().enumerate() {
            assert!(
                c > 9_400 && c < 10_600,
                "bucket {i} deviated from uniform 10000: {c}"
            );
        }
    }

    #[test]
    fn bca_honors_alpha_and_detects_non_zero_variance() {
        let samples: Vec<f64> = (1..=250)
            .map(|i| {
                let x = i as f64;
                100.0 + 0.05 * x * x
            })
            .collect();
        let ci_99 = bca_confidence_interval(
            "skewed",
            &samples,
            EstimandKind::P95,
            0.01,
            MIN_DEV_BOOTSTRAP_RESAMPLES,
            7,
        )
        .unwrap();
        let ci_90 = bca_confidence_interval(
            "skewed",
            &samples,
            EstimandKind::P95,
            0.10,
            MIN_DEV_BOOTSTRAP_RESAMPLES,
            7,
        )
        .unwrap();
        assert_eq!(ci_99.ci_method, "bca");
        assert!(ci_99.ci_low < ci_99.estimate && ci_99.estimate < ci_99.ci_high);
        assert!(
            (ci_99.ci_high - ci_99.ci_low) > (ci_90.ci_high - ci_90.ci_low),
            "99% CI must be wider than 90% CI"
        );
    }

    #[test]
    fn bca_degenerate_zero_variance_vs_bc_zero_accel() {
        let const_samples = vec![42.0; 40];
        let zero_var = bca_confidence_interval(
            "const",
            &const_samples,
            EstimandKind::Mean,
            0.05,
            MIN_DEV_BOOTSTRAP_RESAMPLES,
            1,
        )
        .unwrap();
        assert_eq!(zero_var.ci_method, "degenerate_zero_variance");
        assert_eq!(zero_var.ci_low, 42.0);
        assert_eq!(zero_var.ci_high, 42.0);

        // Symmetric data gives a_hat == 0 => bc_zero_accel, non-degenerate interval
        let sym: Vec<f64> = (0..60).map(|i| (i % 10) as f64 + 1.0).collect();
        let sym_ci = bca_confidence_interval(
            "sym",
            &sym,
            EstimandKind::Mean,
            0.05,
            MIN_DEV_BOOTSTRAP_RESAMPLES,
            9,
        )
        .unwrap();
        assert!(sym_ci.ci_high > sym_ci.ci_low);
    }

    #[test]
    fn wilcoxon_exact_and_normal_plus_hodges_lehmann_match_analytical_values() {
        // Known paired differences: [1, 2, 3, 4, 5, 6] => W+ = 21, exact 2-sided p = 2*(1/64) = 0.03125
        let a = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0];
        let b = [11.0, 12.0, 13.0, 14.0, 15.0, 16.0];
        let res = wilcoxon_signed_rank_test(&a, &b).unwrap();
        assert!(res.exact);
        assert_eq!(res.w_plus, 21.0);
        assert!((res.p_value - 0.03125).abs() < 1e-12);
        assert!((res.hodges_lehmann_pseudomedian - 3.5).abs() < 1e-12);

        // Large n > 25 triggers tie/zero-corrected normal approx
        let a_big: Vec<f64> = (0..40).map(|i| i as f64).collect();
        let b_big: Vec<f64> = (0..40).map(|i| i as f64 + 2.0).collect();
        let res_big = wilcoxon_signed_rank_test(&a_big, &b_big).unwrap();
        assert!(!res_big.exact);
        assert!(res_big.p_value < 1e-6);
        assert!((res_big.hodges_lehmann_pseudomedian - 2.0).abs() < 1e-12);

        // Verify exact HL matches direct algorithm even on n = 600 > 512 branch
        let diffs_big: Vec<f64> = (1..=600).map(|i| (i as f64) * 0.5).collect();
        let hl_big = hodges_lehmann_pseudomedian(&diffs_big).unwrap();
        // Symmetric around median (0.5 + 300.0)/2 = 150.25
        assert!((hl_big - 150.25).abs() < 1e-9, "hl_big={hl_big}");
    }

    #[test]
    fn mann_whitney_mcnemar_wilson_newcombe_pass_at_k_cliffs_holm_verified() {
        let a = [1.0, 2.0, 3.0, 4.0, 5.0];
        let b = [6.0, 7.0, 8.0, 9.0, 10.0];
        let mw = mann_whitney_u_test(&a, &b).unwrap();
        assert_eq!(mw.u_statistic, 0.0);
        assert!(mw.p_value < 0.02);

        let w = wilson_score_ci(18, 20, 0.05).unwrap();
        assert!((w.proportion - 0.9).abs() < 1e-12);
        assert!(w.ci_low > 0.68 && w.ci_high < 0.99);

        // Exact McNemar: n01=12, n10=1 => 2 * (1 + 13) / 2^13 = 28 / 8192 = 0.00341796875
        let mc = exact_mcnemar_test(12, 1);
        assert!((mc.p_value - (28.0 / 8192.0)).abs() < 1e-12);

        let nw = newcombe_wilson_diff_ci(40, 2, 14, 44, 0.05).unwrap();
        assert!((nw.diff - 0.12).abs() < 1e-12);
        assert!(nw.ci_low > 0.0 && nw.ci_low < nw.diff && nw.diff < nw.ci_high);

        // unbiased pass@k: n=5, c=2, k=2 => 1 - C(3,2)/C(5,2) = 1 - 3/10 = 0.7
        let pk = unbiased_pass_at_k(5, 2, 2).unwrap();
        assert!((pk - 0.7).abs() < 1e-12);

        let cd = cliffs_delta(&a, &b).unwrap();
        assert!((cd.delta - 1.0).abs() < 1e-12);
        assert_eq!(cd.magnitude, CliffMagnitude::Large);

        let adj = holm_bonferroni(&[0.01, 0.04, 0.03]).unwrap();
        assert!((adj[0] - 0.03).abs() < 1e-12);
        assert!((adj[2] - 0.06).abs() < 1e-12);
        assert!((adj[1] - 0.06).abs() < 1e-12);
    }

    #[test]
    fn open_loop_queue_exposes_coordinated_omission_and_hdr_accuracy() {
        let mut hist = HdrHistogramLog::new();
        for v in 1..=10_000u64 {
            hist.record_ns(v * 1_000);
        }
        let s = hist.summary();
        // Relative error within < 0.2% across all quantiles
        assert!((s.p50_ns - 5_000_000.0).abs() / 5_000_000.0 < 0.002);
        assert!((s.p95_ns - 9_500_000.0).abs() / 9_500_000.0 < 0.002);
        assert!((s.p99_ns - 9_900_000.0).abs() / 9_900_000.0 < 0.002);

        // Periodic stall in open-loop service creates queueing pile-up visible in sojourn_p99 >> service_p99
        let mut svc = vec![50_000u64; 1_000];
        svc[200] = 20_000_000; // 20ms stall!
        let sim = simulate_open_loop_queue(&svc, 15_000.0, 1, 2_000, 5_000_000_000, 42).unwrap();
        assert_eq!(sim.admitted, 1_000);
        assert_eq!(sim.completed, 1_000);
        assert_eq!(sim.rejected_queue_full, 0);
        assert!(sim.queued > 50);
        assert!(sim.sojourn_hist.quantile(0.99) > sim.service_hist.quantile(0.99) * 10.0);

        // Full queue triggers explicit rejection counts instead of silent drops
        let sim2 = simulate_open_loop_queue(&svc, 50_000.0, 1, 4, 5_000_000_000, 42).unwrap();
        assert!(sim2.rejected_queue_full > 0 && sim2.admitted + sim2.rejected_queue_full == 1_000);
    }

    #[test]
    fn environment_contamination_gate_and_canary_self_test() {
        let mut env = EnvironmentFingerprintV3::capture_local_best_effort("offline");
        let incomplete = env.evaluate_contamination(false);
        assert_eq!(incomplete.verdict, ContaminationVerdict::Unknown);
        assert!(incomplete.cpu_frequency_drop_ratio.is_none());
        assert!(matches!(
            env.require_clean(false),
            Err(BenchError::IncompleteEnvironment(_))
        ));
        env.baseline_cpu_mhz = Some(3_600.0);
        env.measured_cpu_mhz = Some(3_500.0);
        env.pre_run_load_avg = Some(0.05);
        env.post_run_load_avg = Some(0.05);
        assert!(env.require_clean(false).is_ok());

        let mut throttled = env.clone();
        throttled.measured_cpu_mhz = Some(2_800.0);
        let err = throttled.require_clean(false).unwrap_err();
        assert!(matches!(err, BenchError::Contaminated(_)));

        let mut overloaded = env.clone();
        overloaded.post_run_load_avg = Some(env.logical_cpus as f64 * 0.5);
        assert!(matches!(
            overloaded.require_clean(false).unwrap_err(),
            BenchError::Contaminated(_)
        ));

        let sensitivity = run_canary_sensitivity_self_test().unwrap();
        assert!(sensitivity.passed);
        assert!(sensitivity.adjusted_p_value <= 0.05);
        assert!(sensitivity.cliffs_delta >= 0.33);
    }
}
