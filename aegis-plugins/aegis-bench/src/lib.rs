use serde::{Deserialize, Serialize};
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

fn quantile_sorted(sorted: &[f64], q: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    if sorted.len() == 1 {
        return sorted[0];
    }
    let clamped = q.clamp(0.0, 1.0);
    let pos = clamped * ((sorted.len() - 1) as f64);
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        sorted[lo]
    } else {
        let frac = pos - (lo as f64);
        sorted[lo] * (1.0 - frac) + sorted[hi] * frac
    }
}

fn splitmix64_next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

impl EmpiricalDistribution {
    /// Compute exact empirical quantiles (`p50`, `p95`, `p99`) and a deterministic
    /// 95% nonparametric bootstrap confidence interval for `p95_ns` (`resamples >= 200`).
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
        let p50_ns = quantile_sorted(&sorted, 0.50);
        let p95_ns = quantile_sorted(&sorted, 0.95);
        let p99_ns = quantile_sorted(&sorted, 0.99);

        let resamples = bootstrap_resamples.max(200);
        let mut rng_state = 0xAE61_5BEE_F00D_CAFEu64 ^ (n as u64);
        for b in metric_name.as_bytes() {
            rng_state = rng_state.rotate_left(7) ^ u64::from(*b);
        }
        let mut boot_p95 = Vec::with_capacity(resamples);
        let mut scratch = vec![0.0; n];
        for _ in 0..resamples {
            for slot in &mut scratch {
                let idx = (splitmix64_next(&mut rng_state) as usize) % n;
                *slot = sorted[idx];
            }
            scratch.sort_by(|a, b| a.total_cmp(b));
            boot_p95.push(quantile_sorted(&scratch, 0.95));
        }
        boot_p95.sort_by(|a, b| a.total_cmp(b));
        let bootstrap_p95_ci_low_ns = quantile_sorted(&boot_p95, 0.025);
        let bootstrap_p95_ci_high_ns = quantile_sorted(&boot_p95, 0.975);

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
/// and return its empirical latency distribution (`p50/p95/p99` + 95% bootstrap CI).
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
        op();
    }
    let mut samples = Vec::with_capacity(trials);
    for _ in 0..trials {
        let start = std::time::Instant::now();
        op();
        samples.push(start.elapsed().as_secs_f64() * 1_000_000_000.0);
    }
    EmpiricalDistribution::from_samples(metric_name, &samples, 500)
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
            let dist = EmpiricalDistribution::from_samples(name, samples, 500)?;
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
}
