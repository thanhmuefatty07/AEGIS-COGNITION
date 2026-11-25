use crate::BenchError;
use serde::{Deserialize, Serialize};

pub const DEFAULT_BOOTSTRAP_RESAMPLES: usize = 10_000;
pub const MIN_DEV_BOOTSTRAP_RESAMPLES: usize = 2_000;
pub const JACKKNIFE_BLOCK_COUNT: usize = 50;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EstimandKind {
    Mean,
    P50,
    P90,
    P95,
    P99,
}

impl EstimandKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Mean => "mean",
            Self::P50 => "p50",
            Self::P90 => "p90",
            Self::P95 => "p95",
            Self::P99 => "p99",
        }
    }

    pub fn quantile(self) -> Option<f64> {
        match self {
            Self::Mean => None,
            Self::P50 => Some(0.50),
            Self::P90 => Some(0.90),
            Self::P95 => Some(0.95),
            Self::P99 => Some(0.99),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BcaConfidenceInterval {
    pub estimand: String,
    pub estimate: f64,
    pub ci_low: f64,
    pub ci_high: f64,
    pub alpha: f64,
    pub resamples: usize,
    pub z0_hat: f64,
    pub a_hat: f64,
    pub ci_method: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DistributionV3 {
    pub metric_name: String,
    pub sample_count: usize,
    pub mean_ns: f64,
    pub stddev_ns: f64,
    pub min_ns: f64,
    pub p50_ns: f64,
    pub p90_ns: f64,
    pub p95_ns: f64,
    pub p99_ns: f64,
    pub max_ns: f64,
    pub mean_ci: BcaConfidenceInterval,
    pub p50_ci: BcaConfidenceInterval,
    pub p90_ci: BcaConfidenceInterval,
    pub p95_ci: BcaConfidenceInterval,
    pub p99_ci: BcaConfidenceInterval,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PairedEstimandComparisonV3 {
    pub estimand: String,
    pub a_estimate: f64,
    pub b_estimate: f64,
    pub delta_estimate: f64,
    pub delta_ci: BcaConfidenceInterval,
    pub ratio_estimate: Option<f64>,
    pub ratio_ci: Option<BcaConfidenceInterval>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct WilcoxonResult {
    pub n_total: usize,
    pub n_nonzero: usize,
    pub w_plus: f64,
    pub w_minus: f64,
    pub statistic: f64,
    pub p_value: f64,
    pub hodges_lehmann_pseudomedian: f64,
    pub exact: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MannWhitneyResult {
    pub n_a: usize,
    pub n_b: usize,
    pub u_a: f64,
    pub u_b: f64,
    pub u_statistic: f64,
    pub z_score: f64,
    pub p_value: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct WilsonScoreCi {
    pub successes: usize,
    pub total: usize,
    pub proportion: f64,
    pub ci_low: f64,
    pub ci_high: f64,
    pub alpha: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct McNemarResult {
    pub n01: usize,
    pub n10: usize,
    pub discordant_total: usize,
    pub p_value: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NewcombeWilsonDiffCi {
    pub diff: f64,
    pub ci_low: f64,
    pub ci_high: f64,
    pub p_a: f64,
    pub p_b: f64,
    pub alpha: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CliffMagnitude {
    Negligible,
    Small,
    Medium,
    Large,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CliffsDeltaResult {
    pub delta: f64,
    pub abs_delta: f64,
    pub magnitude: CliffMagnitude,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SesoiPolicy {
    pub min_abs_cliffs_delta: f64,
    pub min_relative_delta: f64,
}

impl Default for SesoiPolicy {
    fn default() -> Self {
        Self {
            min_abs_cliffs_delta: 0.147,
            min_relative_delta: 0.05,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PairedComparisonV3 {
    pub metric_name: String,
    pub alpha: f64,
    pub resamples: usize,
    pub paired_blocks: usize,
    pub arm_a: DistributionV3,
    pub arm_b: DistributionV3,
    pub wilcoxon: WilcoxonResult,
    pub cliffs_delta: CliffsDeltaResult,
    pub estimands: Vec<PairedEstimandComparisonV3>,
    pub raw_p_value: f64,
    pub adjusted_p_value: f64,
    pub sesoi_cleared: bool,
    pub statistically_significant: bool,
    pub practical_change_proven: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SensitivitySelfTestReport {
    pub passed: bool,
    pub injected_slowdown_factor: f64,
    pub observed_ratio: f64,
    pub adjusted_p_value: f64,
    pub cliffs_delta: f64,
    pub mean_delta_ci_low: f64,
    pub quarantine_status: String,
}

#[inline]
pub fn splitmix64_next(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

#[inline]
pub fn lemire_bounded_index(state: &mut u64, bound: usize) -> usize {
    if bound <= 1 {
        return 0;
    }
    let s = bound as u64;
    let mut x = splitmix64_next(state);
    let mut m = (x as u128) * (s as u128);
    let mut l = m as u64;
    if l < s {
        let t = s.wrapping_neg() % s;
        while l < t {
            x = splitmix64_next(state);
            m = (x as u128) * (s as u128);
            l = m as u64;
        }
    }
    (m >> 64) as usize
}

#[inline]
pub fn uniform_open0_closed1(state: &mut u64) -> f64 {
    // 53-bit strictly positive uniform in (0, 1] so -ln(U) is always finite and >= 0.
    let top53 = (splitmix64_next(state) >> 11) + 1;
    (top53 as f64) / ((1u64 << 53) as f64)
}

pub fn seed_from_label(label: &str, len: usize, base_seed: u64) -> u64 {
    let mut state = base_seed ^ 0xAE61_5BEE_F00D_CAFEu64 ^ (len as u64).wrapping_mul(0x9E37_79B1);
    for &b in label.as_bytes() {
        state = state.rotate_left(7) ^ u64::from(b);
        state = state.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    }
    state
}

#[inline]
pub fn quantile_type7_sorted(sorted: &[f64], q: f64) -> f64 {
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

fn erf_small_positive_terms(x: f64) -> f64 {
    let x2 = x * x;
    let mut term = x;
    let mut sum = term;
    for k in 1usize..=80 {
        term *= (2.0 * x2) / ((2 * k + 1) as f64);
        sum += term;
        if term.abs() <= sum.abs() * 1e-16 {
            break;
        }
    }
    (std::f64::consts::FRAC_2_SQRT_PI * (-x2).exp() * sum).clamp(0.0, 1.0)
}

fn erfc_continued_fraction(x: f64) -> f64 {
    if x > 27.5 {
        return 0.0;
    }
    let one_over_sqrt_pi = 0.564_189_583_547_756_3f64;
    let tiny = 1e-300f64;
    let mut f = x;
    let mut c = f;
    let mut d = 0.0f64;
    for n in 1usize..=200 {
        let a = 0.5 * (n as f64);
        let b = x;
        d = b + a * d;
        if d.abs() < tiny {
            d = tiny;
        }
        c = b + a / c;
        if c.abs() < tiny {
            c = tiny;
        }
        d = 1.0 / d;
        let delta = c * d;
        f *= delta;
        if (delta - 1.0).abs() < 1e-15 {
            break;
        }
    }
    (one_over_sqrt_pi * (-(x * x)).exp() / f).clamp(0.0, 1.0)
}

pub fn erf_exact(x: f64) -> f64 {
    if !x.is_finite() {
        return if x.is_nan() { f64::NAN } else { x.signum() };
    }
    let ax = x.abs();
    if ax == 0.0 {
        return 0.0;
    }
    let mag = if ax < 1.8 {
        erf_small_positive_terms(ax)
    } else {
        1.0 - erfc_continued_fraction(ax)
    };
    if x < 0.0 { -mag } else { mag }
}

pub fn normal_cdf(x: f64) -> f64 {
    if x < -1.8 * std::f64::consts::SQRT_2 {
        return 0.5 * erfc_continued_fraction(-x * std::f64::consts::FRAC_1_SQRT_2);
    }
    if x > 1.8 * std::f64::consts::SQRT_2 {
        return 1.0 - 0.5 * erfc_continued_fraction(x * std::f64::consts::FRAC_1_SQRT_2);
    }
    0.5 * (1.0 + erf_exact(x * std::f64::consts::FRAC_1_SQRT_2))
}

pub fn normal_inv_cdf(p: f64) -> f64 {
    if p <= 0.0 {
        return f64::NEG_INFINITY;
    }
    if p >= 1.0 {
        return f64::INFINITY;
    }
    if (p - 0.5).abs() == 0.0 {
        return 0.0;
    }
    // Acklam rational approximation
    const A: [f64; 6] = [
        -3.969_683_028_665_376e+01,
        2.209_460_984_245_205e+02,
        -2.759_285_104_469_687e+02,
        1.383_577_518_672_69e+02,
        -3.066_479_806_614_716e+01,
        2.506_628_277_459_239e+00,
    ];
    const B: [f64; 5] = [
        -5.447_609_879_822_406e+01,
        1.615_858_368_580_409e+02,
        -1.556_989_798_598_866e+02,
        6.680_131_188_771_972e+01,
        -1.328_068_155_288_572e+01,
    ];
    const C: [f64; 6] = [
        -7.784_894_002_430_293e-03,
        -3.223_964_580_411_365e-01,
        -2.400_758_277_161_838e+00,
        -2.549_732_539_343_734e+00,
        4.374_664_141_464_968e+00,
        2.938_163_982_698_783e+00,
    ];
    const D: [f64; 4] = [
        7.784_695_709_041_462e-03,
        3.224_671_290_700_398e-01,
        2.445_134_137_142_996e+00,
        3.754_408_661_907_416e+00,
    ];
    let p_low = 0.02425;
    let p_high = 1.0 - p_low;
    let mut z = if p < p_low {
        let q = (-2.0 * p.ln()).sqrt();
        (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    } else if p <= p_high {
        let q = p - 0.5;
        let r = q * q;
        (((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q
            / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1.0)
    } else {
        let q = (-2.0 * (1.0 - p).ln()).sqrt();
        -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    };

    // Two Newton-Raphson refinement steps to approach full IEEE precision
    let inv_sqrt_2pi = 0.398_942_280_401_432_7f64;
    for _ in 0..2 {
        let err = normal_cdf(z) - p;
        let pdf = inv_sqrt_2pi * (-0.5 * z * z).exp();
        if pdf > 1e-300 {
            z -= err / pdf;
        }
    }
    z
}

fn validate_samples(metric_name: &str, samples: &[f64], min_len: usize) -> Result<(), BenchError> {
    if samples.is_empty() {
        return Err(BenchError::EmptySamples(metric_name.to_string()));
    }
    if samples.len() < min_len {
        return Err(BenchError::InsufficientSamples {
            metric: metric_name.to_string(),
            required: min_len,
            actual: samples.len(),
        });
    }
    if samples.iter().any(|v| !v.is_finite()) {
        return Err(BenchError::InvalidSample(metric_name.to_string()));
    }
    Ok(())
}

fn eval_estimand_unsorted(samples: &[f64], kind: EstimandKind, scratch: &mut Vec<f64>) -> f64 {
    match kind.quantile() {
        None => samples.iter().sum::<f64>() / (samples.len() as f64),
        Some(q) => {
            scratch.clear();
            scratch.extend_from_slice(samples);
            scratch.sort_by(|a, b| a.total_cmp(b));
            quantile_type7_sorted(scratch, q)
        }
    }
}

fn compute_acceleration_from_jackknife(jack_vals: &[f64]) -> (f64, bool) {
    if jack_vals.len() < 2 {
        return (0.0, true);
    }
    let k = jack_vals.len() as f64;
    let jack_mean = jack_vals.iter().sum::<f64>() / k;
    let mut sum_sq = 0.0f64;
    let mut sum_cube = 0.0f64;
    for &v in jack_vals {
        let d = jack_mean - v;
        let d2 = d * d;
        sum_sq += d2;
        sum_cube += d2 * d;
    }
    if sum_sq <= 1e-30 {
        (0.0, true)
    } else {
        let denom = 6.0 * sum_sq.powf(1.5);
        if denom <= 1e-30 {
            (0.0, true)
        } else {
            (sum_cube / denom, false)
        }
    }
}

fn jackknife_single_arm(samples: &[f64], kind: EstimandKind) -> Vec<f64> {
    let n = samples.len();
    let mut scratch = Vec::with_capacity(n);
    if matches!(kind, EstimandKind::Mean) {
        if n < 200 {
            let total: f64 = samples.iter().sum();
            let denom = (n - 1) as f64;
            return samples.iter().map(|&x| (total - x) / denom).collect();
        }
    } else if n < 200 {
        let mut out = Vec::with_capacity(n);
        let mut fold = Vec::with_capacity(n - 1);
        for skip in 0..n {
            fold.clear();
            fold.extend_from_slice(&samples[..skip]);
            fold.extend_from_slice(&samples[skip + 1..]);
            out.push(eval_estimand_unsorted(&fold, kind, &mut scratch));
        }
        return out;
    }
    // Delete-d block jackknife with K = 50 equal blocks
    let k_blocks = JACKKNIFE_BLOCK_COUNT.min(n);
    let mut out = Vec::with_capacity(k_blocks);
    let mut fold = Vec::with_capacity(n);
    for block_idx in 0..k_blocks {
        let start = (block_idx * n) / k_blocks;
        let end = ((block_idx + 1) * n) / k_blocks;
        fold.clear();
        fold.extend_from_slice(&samples[..start]);
        fold.extend_from_slice(&samples[end..]);
        if !fold.is_empty() {
            out.push(eval_estimand_unsorted(&fold, kind, &mut scratch));
        }
    }
    out
}

fn finalize_bca_interval(
    estimand: &str,
    estimate: f64,
    mut boot_replicates: Vec<f64>,
    jack_vals: &[f64],
    alpha: f64,
    zero_variance_input: bool,
) -> BcaConfidenceInterval {
    let resamples = boot_replicates.len();
    if zero_variance_input {
        return BcaConfidenceInterval {
            estimand: estimand.to_string(),
            estimate,
            ci_low: estimate,
            ci_high: estimate,
            alpha,
            resamples,
            z0_hat: 0.0,
            a_hat: 0.0,
            ci_method: "degenerate_zero_variance".to_string(),
        };
    }

    let mut less = 0usize;
    let mut eq = 0usize;
    for &b in &boot_replicates {
        if b < estimate {
            less += 1;
        } else if (b - estimate).abs() == 0.0 {
            eq += 1;
        }
    }
    let p0 = ((less as f64) + 0.5 * (eq as f64)) / (resamples as f64);
    let eps = 0.5 / ((resamples + 1) as f64);
    let z0_hat = normal_inv_cdf(p0.clamp(eps, 1.0 - eps));

    let (mut a_hat, mut zero_accel) = compute_acceleration_from_jackknife(jack_vals);
    let z_lo = normal_inv_cdf(alpha * 0.5);
    let z_hi = normal_inv_cdf(1.0 - alpha * 0.5);

    let mut denom_lo = 1.0 - a_hat * (z0_hat + z_lo);
    let mut denom_hi = 1.0 - a_hat * (z0_hat + z_hi);
    if denom_lo <= 1e-9 || denom_hi <= 1e-9 {
        a_hat = 0.0;
        zero_accel = true;
        denom_lo = 1.0;
        denom_hi = 1.0;
    }

    let alpha_lo = normal_cdf(z0_hat + (z0_hat + z_lo) / denom_lo).clamp(eps, 1.0 - eps);
    let alpha_hi = normal_cdf(z0_hat + (z0_hat + z_hi) / denom_hi).clamp(eps, 1.0 - eps);
    let (a_lo, a_hi) = if alpha_lo <= alpha_hi {
        (alpha_lo, alpha_hi)
    } else {
        (alpha_hi, alpha_lo)
    };

    boot_replicates.sort_by(|a, b| a.total_cmp(b));
    let mut ci_low = quantile_type7_sorted(&boot_replicates, a_lo);
    let mut ci_high = quantile_type7_sorted(&boot_replicates, a_hi);
    let boot_min = boot_replicates[0];
    let boot_max = boot_replicates[resamples - 1];
    // Never emit collapsed zero-width bounds when bootstrap replicates themselves vary.
    if (ci_high - ci_low).abs() == 0.0 && boot_max > boot_min {
        let fallback_lo = quantile_type7_sorted(&boot_replicates, alpha * 0.5);
        let fallback_hi = quantile_type7_sorted(&boot_replicates, 1.0 - alpha * 0.5);
        if fallback_hi > fallback_lo {
            ci_low = fallback_lo;
            ci_high = fallback_hi;
        } else {
            ci_low = boot_min;
            ci_high = boot_max;
        }
    }

    BcaConfidenceInterval {
        estimand: estimand.to_string(),
        estimate,
        ci_low: ci_low.min(ci_high),
        ci_high: ci_low.max(ci_high),
        alpha,
        resamples,
        z0_hat,
        a_hat,
        ci_method: if zero_accel {
            "bc_zero_accel".to_string()
        } else {
            "bca".to_string()
        },
    }
}

pub fn bca_confidence_interval(
    label: &str,
    samples: &[f64],
    kind: EstimandKind,
    alpha: f64,
    resamples: usize,
    seed: u64,
) -> Result<BcaConfidenceInterval, BenchError> {
    bca_confidence_interval_with_min_resamples(
        label,
        samples,
        kind,
        alpha,
        resamples,
        MIN_DEV_BOOTSTRAP_RESAMPLES,
        seed,
    )
}

pub(crate) fn bca_confidence_interval_with_min_resamples(
    label: &str,
    samples: &[f64],
    kind: EstimandKind,
    alpha: f64,
    resamples: usize,
    min_resamples: usize,
    seed: u64,
) -> Result<BcaConfidenceInterval, BenchError> {
    validate_samples(label, samples, 2)?;
    if !alpha.is_finite() || alpha <= 0.0 || alpha >= 0.5 {
        return Err(BenchError::InvalidParameter("alpha must be in (0, 0.5)"));
    }
    if resamples < min_resamples {
        return Err(BenchError::InvalidParameter(
            "resamples below required minimum",
        ));
    }
    let n = samples.len();
    let zero_var = samples.iter().all(|&v| (v - samples[0]).abs() == 0.0);
    let mut scratch = Vec::with_capacity(n);
    let estimate = eval_estimand_unsorted(samples, kind, &mut scratch);
    if zero_var {
        return Ok(finalize_bca_interval(
            kind.as_str(),
            estimate,
            vec![estimate; resamples],
            &[],
            alpha,
            true,
        ));
    }

    let mut rng_state = seed_from_label(label, n, seed) ^ seed_from_label(kind.as_str(), n, 17);
    let mut sample_buf = vec![0.0f64; n];
    let mut boot = Vec::with_capacity(resamples);
    for _ in 0..resamples {
        for slot in &mut sample_buf {
            let idx = lemire_bounded_index(&mut rng_state, n);
            *slot = samples[idx];
        }
        let val = match kind.quantile() {
            None => sample_buf.iter().sum::<f64>() / (n as f64),
            Some(q) => {
                sample_buf.sort_by(|a, b| a.total_cmp(b));
                quantile_type7_sorted(&sample_buf, q)
            }
        };
        boot.push(val);
    }

    let jack_vals = jackknife_single_arm(samples, kind);
    Ok(finalize_bca_interval(
        kind.as_str(),
        estimate,
        boot,
        &jack_vals,
        alpha,
        false,
    ))
}

impl DistributionV3 {
    pub fn from_samples(
        metric_name: &str,
        samples_ns: &[f64],
        alpha: f64,
        resamples: usize,
        seed: u64,
    ) -> Result<Self, BenchError> {
        validate_samples(metric_name, samples_ns, 2)?;
        if samples_ns.iter().any(|&v| v < 0.0) {
            return Err(BenchError::InvalidSample(metric_name.to_string()));
        }
        if !alpha.is_finite() || alpha <= 0.0 || alpha >= 0.5 {
            return Err(BenchError::InvalidParameter("alpha must be in (0, 0.5)"));
        }
        if resamples < MIN_DEV_BOOTSTRAP_RESAMPLES {
            return Err(BenchError::InvalidParameter(
                "resamples must be >= 2000 (10000 for CI)",
            ));
        }
        let n = samples_ns.len();
        let mut sorted = samples_ns.to_vec();
        sorted.sort_by(|a, b| a.total_cmp(b));
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
        let zero_var = samples_ns
            .iter()
            .all(|&v| (v - samples_ns[0]).abs() == 0.0);
        let p50_ns = quantile_type7_sorted(&sorted, 0.50);
        let p90_ns = quantile_type7_sorted(&sorted, 0.90);
        let p95_ns = quantile_type7_sorted(&sorted, 0.95);
        let p99_ns = quantile_type7_sorted(&sorted, 0.99);

        // Single pass paired index draw across all 5 estimands to reuse one sort per resample
        let mut boot_mean = Vec::with_capacity(resamples);
        let mut boot_p50 = Vec::with_capacity(resamples);
        let mut boot_p90 = Vec::with_capacity(resamples);
        let mut boot_p95 = Vec::with_capacity(resamples);
        let mut boot_p99 = Vec::with_capacity(resamples);

        if zero_var {
            boot_mean.resize(resamples, mean_ns);
            boot_p50.resize(resamples, p50_ns);
            boot_p90.resize(resamples, p90_ns);
            boot_p95.resize(resamples, p95_ns);
            boot_p99.resize(resamples, p99_ns);
        } else {
            let mut rng_state = seed_from_label(metric_name, n, seed);
            let mut buf = vec![0.0f64; n];
            for _ in 0..resamples {
                let mut sum = 0.0f64;
                for slot in &mut buf {
                    let v = samples_ns[lemire_bounded_index(&mut rng_state, n)];
                    *slot = v;
                    sum += v;
                }
                boot_mean.push(sum / (n as f64));
                buf.sort_by(|a, b| a.total_cmp(b));
                boot_p50.push(quantile_type7_sorted(&buf, 0.50));
                boot_p90.push(quantile_type7_sorted(&buf, 0.90));
                boot_p95.push(quantile_type7_sorted(&buf, 0.95));
                boot_p99.push(quantile_type7_sorted(&buf, 0.99));
            }
        }

        let mean_ci = finalize_bca_interval(
            "mean",
            mean_ns,
            boot_mean,
            &if zero_var {
                Vec::new()
            } else {
                jackknife_single_arm(samples_ns, EstimandKind::Mean)
            },
            alpha,
            zero_var,
        );
        let p50_ci = finalize_bca_interval(
            "p50",
            p50_ns,
            boot_p50,
            &if zero_var {
                Vec::new()
            } else {
                jackknife_single_arm(samples_ns, EstimandKind::P50)
            },
            alpha,
            zero_var,
        );
        let p90_ci = finalize_bca_interval(
            "p90",
            p90_ns,
            boot_p90,
            &if zero_var {
                Vec::new()
            } else {
                jackknife_single_arm(samples_ns, EstimandKind::P90)
            },
            alpha,
            zero_var,
        );
        let p95_ci = finalize_bca_interval(
            "p95",
            p95_ns,
            boot_p95,
            &if zero_var {
                Vec::new()
            } else {
                jackknife_single_arm(samples_ns, EstimandKind::P95)
            },
            alpha,
            zero_var,
        );
        let p99_ci = finalize_bca_interval(
            "p99",
            p99_ns,
            boot_p99,
            &if zero_var {
                Vec::new()
            } else {
                jackknife_single_arm(samples_ns, EstimandKind::P99)
            },
            alpha,
            zero_var,
        );

        Ok(Self {
            metric_name: metric_name.to_string(),
            sample_count: n,
            mean_ns,
            stddev_ns: variance.sqrt(),
            min_ns: sorted[0],
            p50_ns,
            p90_ns,
            p95_ns,
            p99_ns,
            max_ns: sorted[n - 1],
            mean_ci,
            p50_ci,
            p90_ci,
            p95_ci,
            p99_ci,
        })
    }
}

pub fn hodges_lehmann_pseudomedian(diffs: &[f64]) -> Result<f64, BenchError> {
    validate_samples("hodges_lehmann", diffs, 1)?;
    let n = diffs.len();
    if n == 1 {
        return Ok(diffs[0]);
    }
    let mut sorted = diffs.to_vec();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let total_pairs = n * (n + 1) / 2;

    if n <= 512 {
        let mut walsh = Vec::with_capacity(total_pairs);
        for i in 0..n {
            for j in i..n {
                walsh.push(0.5 * (sorted[i] + sorted[j]));
            }
        }
        walsh.sort_by(|a, b| a.total_cmp(b));
        if total_pairs % 2 == 1 {
            return Ok(walsh[total_pairs / 2]);
        }
        return Ok(0.5 * (walsh[total_pairs / 2 - 1] + walsh[total_pairs / 2]));
    }

    // Exact k-th order statistic on sorted upper-triangular matrix A[i,j] = d[i] + d[j] (i <= j)
    let select_kth = |target_k: usize| -> f64 {
        let mut left: Vec<usize> = (0..n).collect();
        let mut right: Vec<usize> = vec![n - 1; n];
        let mut rng = 0x517C_C1B7_2722_0A95u64 ^ (n as u64);
        loop {
            let mut active = 0usize;
            for i in 0..n {
                if left[i] <= right[i] {
                    active += right[i] - left[i] + 1;
                }
            }
            if active == 0 {
                return sorted[0];
            }
            let pick = lemire_bounded_index(&mut rng, active);
            let mut acc = 0usize;
            let mut pivot = 0.0f64;
            for i in 0..n {
                if left[i] <= right[i] {
                    let count_row = right[i] - left[i] + 1;
                    if pick < acc + count_row {
                        let col = left[i] + (pick - acc);
                        pivot = sorted[i] + sorted[col];
                        break;
                    }
                    acc += count_row;
                }
            }

            // Count strictly < pivot and <= pivot across full upper triangle i <= j
            let mut count_lt = 0usize;
            let mut count_le = 0usize;
            let mut j_lt = n as isize - 1;
            let mut j_le = n as isize - 1;
            for i in 0..n {
                while j_lt >= i as isize && sorted[i] + sorted[j_lt as usize] >= pivot {
                    j_lt -= 1;
                }
                if j_lt >= i as isize {
                    count_lt += (j_lt as usize) - i + 1;
                }
                while j_le >= i as isize && sorted[i] + sorted[j_le as usize] > pivot {
                    j_le -= 1;
                }
                if j_le >= i as isize {
                    count_le += (j_le as usize) - i + 1;
                }
            }

            if target_k < count_lt {
                let mut col = n as isize - 1;
                for i in 0..n {
                    while col >= left[i] as isize && sorted[i] + sorted[col as usize] >= pivot {
                        col -= 1;
                    }
                    right[i] = if col < i as isize { 0 } else { col as usize };
                    if col < i as isize {
                        left[i] = 1;
                    }
                }
            } else if target_k >= count_le {
                let mut col = 0usize;
                for i in 0..n {
                    if col < i {
                        col = i;
                    }
                    while col < n && sorted[i] + sorted[col] <= pivot {
                        col += 1;
                    }
                    left[i] = left[i].max(col);
                }
            } else {
                return 0.5 * pivot;
            }
        }
    };

    if total_pairs % 2 == 1 {
        Ok(select_kth(total_pairs / 2))
    } else {
        let a = select_kth(total_pairs / 2 - 1);
        let b = select_kth(total_pairs / 2);
        Ok(0.5 * (a + b))
    }
}

pub fn wilcoxon_signed_rank_test(arm_a: &[f64], arm_b: &[f64]) -> Result<WilcoxonResult, BenchError> {
    if arm_a.len() != arm_b.len() {
        return Err(BenchError::InvalidParameter(
            "paired Wilcoxon requires equal-length arms",
        ));
    }
    validate_samples("wilcoxon_a", arm_a, 1)?;
    validate_samples("wilcoxon_b", arm_b, 1)?;
    let n_total = arm_a.len();
    let diffs: Vec<f64> = arm_b.iter().zip(arm_a.iter()).map(|(b, a)| b - a).collect();
    let hl = hodges_lehmann_pseudomedian(&diffs)?;

    let mut nonzero: Vec<(f64, bool)> = diffs
        .iter()
        .filter_map(|&d| {
            if d.abs() == 0.0 {
                None
            } else {
                Some((d.abs(), d > 0.0))
            }
        })
        .collect();
    let n_nonzero = nonzero.len();
    if n_nonzero == 0 {
        return Ok(WilcoxonResult {
            n_total,
            n_nonzero: 0,
            w_plus: 0.0,
            w_minus: 0.0,
            statistic: 0.0,
            p_value: 1.0,
            hodges_lehmann_pseudomedian: hl,
            exact: true,
        });
    }

    nonzero.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut ranks = vec![0.0f64; n_nonzero];
    let mut tie_cube_sum = 0.0f64;
    let mut i = 0usize;
    while i < n_nonzero {
        let mut j = i + 1;
        while j < n_nonzero && (nonzero[j].0 - nonzero[i].0).abs() == 0.0 {
            j += 1;
        }
        let avg_rank = 0.5 * ((i + 1 + j) as f64);
        for slot in &mut ranks[i..j] {
            *slot = avg_rank;
        }
        let t = (j - i) as f64;
        if t > 1.0 {
            tie_cube_sum += t * t * t - t;
        }
        i = j;
    }

    let mut w_plus = 0.0f64;
    let total_rank_sum = 0.5 * ((n_nonzero * (n_nonzero + 1)) as f64);
    for (idx, &(_, pos)) in nonzero.iter().enumerate() {
        if pos {
            w_plus += ranks[idx];
        }
    }
    let w_minus = total_rank_sum - w_plus;
    let statistic = w_plus.min(w_minus);

    let (p_value, exact) = if n_nonzero <= 25 {
        let total2 = n_nonzero * (n_nonzero + 1);
        let twice_ranks: Vec<usize> = ranks.iter().map(|r| (r * 2.0).round() as usize).collect();
        let w_plus2: usize = twice_ranks
            .iter()
            .zip(nonzero.iter())
            .filter_map(|(&r2, &(_, pos))| if pos { Some(r2) } else { None })
            .sum();
        let mut ways = vec![0u128; total2 + 1];
        ways[0] = 1;
        for &r2 in &twice_ranks {
            for s in (r2..=total2).rev() {
                ways[s] += ways[s - r2];
            }
        }
        let min_s = w_plus2.min(total2 - w_plus2);
        let tail_count: u128 = ways[..=min_s].iter().sum();
        let total_ways = 1u128 << n_nonzero;
        let p = ((2.0 * (tail_count as f64)) / (total_ways as f64)).min(1.0);
        (p, true)
    } else {
        let m = n_nonzero as f64;
        let mean_w = 0.25 * m * (m + 1.0);
        let var_w = (m * (m + 1.0) * (2.0 * m + 1.0)) / 24.0 - tie_cube_sum / 48.0;
        if var_w <= 1e-30 {
            (1.0, false)
        } else {
            let z = ((w_plus - mean_w).abs() - 0.5).max(0.0) / var_w.sqrt();
            ((2.0 * (1.0 - normal_cdf(z))).clamp(0.0, 1.0), false)
        }
    };

    Ok(WilcoxonResult {
        n_total,
        n_nonzero,
        w_plus,
        w_minus,
        statistic,
        p_value,
        hodges_lehmann_pseudomedian: hl,
        exact,
    })
}

pub fn mann_whitney_u_test(arm_a: &[f64], arm_b: &[f64]) -> Result<MannWhitneyResult, BenchError> {
    validate_samples("mann_whitney_a", arm_a, 1)?;
    validate_samples("mann_whitney_b", arm_b, 1)?;
    let n_a = arm_a.len();
    let n_b = arm_b.len();
    let total_n = n_a + n_b;

    let mut tagged: Vec<(f64, bool)> = Vec::with_capacity(total_n);
    for &v in arm_a {
        tagged.push((v, false));
    }
    for &v in arm_b {
        tagged.push((v, true));
    }
    tagged.sort_by(|x, y| x.0.total_cmp(&y.0));

    let mut rank_sum_b = 0.0f64;
    let mut tie_cube_sum = 0.0f64;
    let mut i = 0usize;
    while i < total_n {
        let mut j = i + 1;
        while j < total_n && (tagged[j].0 - tagged[i].0).abs() == 0.0 {
            j += 1;
        }
        let avg_rank = 0.5 * ((i + 1 + j) as f64);
        let mut b_count = 0usize;
        for &(_, is_b) in &tagged[i..j] {
            if is_b {
                b_count += 1;
            }
        }
        rank_sum_b += (b_count as f64) * avg_rank;
        let t = (j - i) as f64;
        if t > 1.0 {
            tie_cube_sum += t * t * t - t;
        }
        i = j;
    }

    let nf_a = n_a as f64;
    let nf_b = n_b as f64;
    let u_b = rank_sum_b - 0.5 * nf_b * (nf_b + 1.0);
    let u_a = nf_a * nf_b - u_b;
    let u_statistic = u_a.min(u_b);

    let mu = 0.5 * nf_a * nf_b;
    let n_tot_f = total_n as f64;
    let tie_term = if total_n > 1 {
        tie_cube_sum / (n_tot_f * (n_tot_f - 1.0))
    } else {
        0.0
    };
    let sigma_sq = (nf_a * nf_b / 12.0) * ((n_tot_f + 1.0) - tie_term);
    let (z_score, p_value) = if sigma_sq <= 1e-30 {
        (0.0, 1.0)
    } else {
        let z = ((u_b - mu).abs() - 0.5).max(0.0) / sigma_sq.sqrt();
        let signed_z = (u_b - mu) / sigma_sq.sqrt();
        (signed_z, (2.0 * (1.0 - normal_cdf(z))).clamp(0.0, 1.0))
    };

    Ok(MannWhitneyResult {
        n_a,
        n_b,
        u_a,
        u_b,
        u_statistic,
        z_score,
        p_value,
    })
}

pub fn wilson_score_ci(
    successes: usize,
    total: usize,
    alpha: f64,
) -> Result<WilsonScoreCi, BenchError> {
    if total == 0 || successes > total {
        return Err(BenchError::InvalidParameter(
            "total must be positive and >= successes",
        ));
    }
    if !alpha.is_finite() || alpha <= 0.0 || alpha >= 0.5 {
        return Err(BenchError::InvalidParameter("alpha must be in (0, 0.5)"));
    }
    let n = total as f64;
    let p_hat = (successes as f64) / n;
    let z = normal_inv_cdf(1.0 - alpha * 0.5);
    let z2 = z * z;
    let denom = 1.0 + z2 / n;
    let center = (p_hat + z2 / (2.0 * n)) / denom;
    let half = (z * ((p_hat * (1.0 - p_hat)) / n + z2 / (4.0 * n * n)).sqrt()) / denom;
    Ok(WilsonScoreCi {
        successes,
        total,
        proportion: p_hat,
        ci_low: (center - half).clamp(0.0, 1.0),
        ci_high: (center + half).clamp(0.0, 1.0),
        alpha,
    })
}

fn lgamma_int(n_plus_1: usize) -> f64 {
    // Exact log((n_plus_1 - 1)!) via direct sum for moderate n and Stirling for large n
    let n = n_plus_1.saturating_sub(1);
    if n <= 1 {
        return 0.0;
    }
    if n <= 2048 {
        let mut s = 0.0f64;
        for k in 2..=n {
            s += (k as f64).ln();
        }
        return s;
    }
    let nf = n as f64;
    nf * nf.ln() - nf + 0.5 * (2.0 * std::f64::consts::PI * nf).ln() + 1.0 / (12.0 * nf)
}

pub fn exact_mcnemar_test(n01: usize, n10: usize) -> McNemarResult {
    let discordant_total = n01 + n10;
    if discordant_total == 0 {
        return McNemarResult {
            n01,
            n10,
            discordant_total: 0,
            p_value: 1.0,
        };
    }
    let m = discordant_total;
    let k_max = n01.min(n10);
    let log_n_fact = lgamma_int(m + 1);
    let log_half_pow = -(m as f64) * std::f64::consts::LN_2;
    let mut one_sided = 0.0f64;
    for k in 0..=k_max {
        let log_comb = log_n_fact - lgamma_int(k + 1) - lgamma_int(m - k + 1);
        one_sided += (log_comb + log_half_pow).exp();
    }
    McNemarResult {
        n01,
        n10,
        discordant_total,
        p_value: (2.0 * one_sided).min(1.0),
    }
}

pub fn newcombe_wilson_diff_ci(
    n11: usize,
    n10: usize,
    n01: usize,
    n00: usize,
    alpha: f64,
) -> Result<NewcombeWilsonDiffCi, BenchError> {
    let n = n11 + n10 + n01 + n00;
    if n == 0 {
        return Err(BenchError::InvalidParameter("paired counts cannot be empty"));
    }
    let s1 = n11 + n10;
    let s2 = n11 + n01;
    let w1 = wilson_score_ci(s1, n, alpha)?;
    let w2 = wilson_score_ci(s2, n, alpha)?;
    let p_a = w1.proportion;
    let p_b = w2.proportion;
    let diff = p_b - p_a;

    let e = (n11 + n10) as f64;
    let f = (n01 + n00) as f64;
    let g = (n11 + n01) as f64;
    let h = (n10 + n00) as f64;
    let denom = (e * f * g * h).sqrt();
    let phi = if denom <= 0.0 {
        0.0
    } else {
        let psi = (n11 as f64) * (n00 as f64) - (n10 as f64) * (n01 as f64);
        if psi > 0.0 {
            let half_n = 0.5 * (n as f64);
            if psi > half_n {
                (psi - half_n) / denom
            } else {
                0.0
            }
        } else {
            psi / denom
        }
    };

    let d_lo_sq = (p_b - w2.ci_low).powi(2)
        - 2.0 * phi * (p_b - w2.ci_low) * (w1.ci_high - p_a)
        + (w1.ci_high - p_a).powi(2);
    let d_hi_sq = (w2.ci_high - p_b).powi(2)
        - 2.0 * phi * (w2.ci_high - p_b) * (p_a - w1.ci_low)
        + (p_a - w1.ci_low).powi(2);

    Ok(NewcombeWilsonDiffCi {
        diff,
        ci_low: (diff - d_lo_sq.max(0.0).sqrt()).clamp(-1.0, 1.0),
        ci_high: (diff + d_hi_sq.max(0.0).sqrt()).clamp(-1.0, 1.0),
        p_a,
        p_b,
        alpha,
    })
}

pub fn unbiased_pass_at_k(n: usize, c: usize, k: usize) -> Result<f64, BenchError> {
    if n == 0 || k == 0 || k > n || c > n {
        return Err(BenchError::InvalidParameter("invalid pass@k arguments"));
    }
    if n - c < k {
        return Ok(1.0);
    }
    let mut prod = 1.0f64;
    for i in 0..k {
        prod *= ((n - c - i) as f64) / ((n - i) as f64);
    }
    Ok((1.0 - prod).clamp(0.0, 1.0))
}

pub fn pass_at_k_with_bca_ci(
    tasks: &[(usize, usize)],
    k: usize,
    alpha: f64,
    resamples: usize,
    seed: u64,
) -> Result<BcaConfidenceInterval, BenchError> {
    if tasks.len() < 2 {
        return Err(BenchError::InsufficientSamples {
            metric: "pass@k".to_string(),
            required: 2,
            actual: tasks.len(),
        });
    }
    let scores: Vec<f64> = tasks
        .iter()
        .map(|&(n, c)| unbiased_pass_at_k(n, c, k))
        .collect::<Result<Vec<_>, _>>()?;
    bca_confidence_interval("pass@k", &scores, EstimandKind::Mean, alpha, resamples, seed)
}

pub fn cliffs_delta(arm_a: &[f64], arm_b: &[f64]) -> Result<CliffsDeltaResult, BenchError> {
    validate_samples("cliffs_delta_a", arm_a, 1)?;
    validate_samples("cliffs_delta_b", arm_b, 1)?;
    let mut sorted_a = arm_a.to_vec();
    sorted_a.sort_by(|x, y| x.total_cmp(y));

    let n_a = sorted_a.len();
    let n_b = arm_b.len();
    let mut net = 0i128;
    for &b in arm_b {
        let lt = sorted_a.partition_point(|&a| a < b);
        let le = sorted_a.partition_point(|&a| a <= b);
        let gt = n_a - le;
        net += (lt as i128) - (gt as i128);
    }
    let delta = (net as f64) / ((n_a as f64) * (n_b as f64));
    let abs_delta = delta.abs();
    let magnitude = if abs_delta < 0.147 {
        CliffMagnitude::Negligible
    } else if abs_delta < 0.33 {
        CliffMagnitude::Small
    } else if abs_delta < 0.474 {
        CliffMagnitude::Medium
    } else {
        CliffMagnitude::Large
    };
    Ok(CliffsDeltaResult {
        delta,
        abs_delta,
        magnitude,
    })
}

pub fn holm_bonferroni(p_values: &[f64]) -> Result<Vec<f64>, BenchError> {
    if p_values.is_empty() {
        return Ok(Vec::new());
    }
    if p_values
        .iter()
        .any(|p| !p.is_finite() || *p < 0.0 || *p > 1.0)
    {
        return Err(BenchError::InvalidParameter("p-values must be in [0, 1]"));
    }
    let m = p_values.len();
    let mut indexed: Vec<(usize, f64)> = p_values.iter().copied().enumerate().collect();
    indexed.sort_by(|a, b| a.1.total_cmp(&b.1));

    let mut adjusted = vec![1.0f64; m];
    let mut running_max = 0.0f64;
    for (rank, &(orig_idx, p)) in indexed.iter().enumerate() {
        let factor = (m - rank) as f64;
        let candidate = (p * factor).min(1.0);
        running_max = running_max.max(candidate);
        adjusted[orig_idx] = running_max;
    }
    Ok(adjusted)
}

pub fn evaluate_paired_comparison_v3(
    metric_name: &str,
    arm_a_samples: &[f64],
    arm_b_samples: &[f64],
    alpha: f64,
    resamples: usize,
    sesoi: &SesoiPolicy,
    seed: u64,
) -> Result<PairedComparisonV3, BenchError> {
    if arm_a_samples.len() != arm_b_samples.len() {
        return Err(BenchError::InvalidParameter(
            "paired comparison requires equal-length arms",
        ));
    }
    validate_samples(metric_name, arm_a_samples, 2)?;
    validate_samples(metric_name, arm_b_samples, 2)?;
    if !alpha.is_finite() || alpha <= 0.0 || alpha >= 0.5 {
        return Err(BenchError::InvalidParameter("alpha must be in (0, 0.5)"));
    }
    if resamples < MIN_DEV_BOOTSTRAP_RESAMPLES {
        return Err(BenchError::InvalidParameter("resamples must be >= 2000"));
    }

    let n = arm_a_samples.len();
    let arm_a = DistributionV3::from_samples(
        &format!("{metric_name}::arm_a"),
        arm_a_samples,
        alpha,
        resamples,
        seed ^ 0x11,
    )?;
    let arm_b = DistributionV3::from_samples(
        &format!("{metric_name}::arm_b"),
        arm_b_samples,
        alpha,
        resamples,
        seed ^ 0x22,
    )?;
    let wilcoxon = wilcoxon_signed_rank_test(arm_a_samples, arm_b_samples)?;
    let cliff = cliffs_delta(arm_a_samples, arm_b_samples)?;

    const ESTIMANDS: [EstimandKind; 5] = [
        EstimandKind::Mean,
        EstimandKind::P50,
        EstimandKind::P90,
        EstimandKind::P95,
        EstimandKind::P99,
    ];
    let mut a_scratch = Vec::with_capacity(n);
    let mut b_scratch = Vec::with_capacity(n);
    let a_ests: [f64; 5] = ESTIMANDS
        .map(|k| eval_estimand_unsorted(arm_a_samples, k, &mut a_scratch));
    let b_ests: [f64; 5] = ESTIMANDS
        .map(|k| eval_estimand_unsorted(arm_b_samples, k, &mut b_scratch));
    let delta_ests: [f64; 5] = [
        b_ests[0] - a_ests[0],
        b_ests[1] - a_ests[1],
        b_ests[2] - a_ests[2],
        b_ests[3] - a_ests[3],
        b_ests[4] - a_ests[4],
    ];
    let ratio_ests: [Option<f64>; 5] = [
        if a_ests[0] > 0.0 { Some(b_ests[0] / a_ests[0]) } else { None },
        if a_ests[1] > 0.0 { Some(b_ests[1] / a_ests[1]) } else { None },
        if a_ests[2] > 0.0 { Some(b_ests[2] / a_ests[2]) } else { None },
        if a_ests[3] > 0.0 { Some(b_ests[3] / a_ests[3]) } else { None },
        if a_ests[4] > 0.0 { Some(b_ests[4] / a_ests[4]) } else { None },
    ];

    let zero_paired_diff = arm_a_samples
        .iter()
        .zip(arm_b_samples.iter())
        .all(|(a, b)| ((b - a) - (arm_b_samples[0] - arm_a_samples[0])).abs() == 0.0)
        && arm_a_samples
            .iter()
            .all(|a| (a - arm_a_samples[0]).abs() == 0.0);

    let mut boot_deltas: [Vec<f64>; 5] = std::array::from_fn(|_| Vec::with_capacity(resamples));
    let mut boot_ratios: [Vec<f64>; 5] = std::array::from_fn(|_| Vec::with_capacity(resamples));

    if zero_paired_diff {
        for idx in 0..5 {
            boot_deltas[idx].resize(resamples, delta_ests[idx]);
            if let Some(r) = ratio_ests[idx] {
                boot_ratios[idx].resize(resamples, r);
            }
        }
    } else {
        let mut rng_state = seed_from_label(metric_name, n, seed ^ 0xA5A5);
        let mut buf_a = vec![0.0f64; n];
        let mut buf_b = vec![0.0f64; n];
        for _ in 0..resamples {
            let mut sum_a = 0.0f64;
            let mut sum_b = 0.0f64;
            for pos in 0..n {
                let idx = lemire_bounded_index(&mut rng_state, n);
                let va = arm_a_samples[idx];
                let vb = arm_b_samples[idx];
                buf_a[pos] = va;
                buf_b[pos] = vb;
                sum_a += va;
                sum_b += vb;
            }
            let ma = sum_a / (n as f64);
            let mb = sum_b / (n as f64);
            boot_deltas[0].push(mb - ma);
            if ratio_ests[0].is_some() {
                boot_ratios[0].push(if ma > 0.0 { mb / ma } else { 1.0 });
            }
            buf_a.sort_by(|x, y| x.total_cmp(y));
            buf_b.sort_by(|x, y| x.total_cmp(y));
            for (slot, q) in [(1usize, 0.50), (2, 0.90), (3, 0.95), (4, 0.99)] {
                let qa = quantile_type7_sorted(&buf_a, q);
                let qb = quantile_type7_sorted(&buf_b, q);
                boot_deltas[slot].push(qb - qa);
                if ratio_ests[slot].is_some() {
                    boot_ratios[slot].push(if qa > 0.0 { qb / qa } else { 1.0 });
                }
            }
        }
    }

    // Paired jackknife (LOO for n < 200 & mean; K=50 blocks otherwise)
    let k_blocks = if n < 200 { n } else { JACKKNIFE_BLOCK_COUNT };
    let mut jack_deltas: [Vec<f64>; 5] = std::array::from_fn(|_| Vec::with_capacity(k_blocks));
    let mut jack_ratios: [Vec<f64>; 5] = std::array::from_fn(|_| Vec::with_capacity(k_blocks));
    if !zero_paired_diff {
        let mut fold_a = Vec::with_capacity(n);
        let mut fold_b = Vec::with_capacity(n);
        for blk in 0..k_blocks {
            let start = (blk * n) / k_blocks;
            let end = ((blk + 1) * n) / k_blocks;
            fold_a.clear();
            fold_b.clear();
            fold_a.extend_from_slice(&arm_a_samples[..start]);
            fold_a.extend_from_slice(&arm_a_samples[end..]);
            fold_b.extend_from_slice(&arm_b_samples[..start]);
            fold_b.extend_from_slice(&arm_b_samples[end..]);
            if fold_a.is_empty() {
                continue;
            }
            let ma = fold_a.iter().sum::<f64>() / (fold_a.len() as f64);
            let mb = fold_b.iter().sum::<f64>() / (fold_b.len() as f64);
            jack_deltas[0].push(mb - ma);
            if ratio_ests[0].is_some() {
                jack_ratios[0].push(if ma > 0.0 { mb / ma } else { 1.0 });
            }
            fold_a.sort_by(|x, y| x.total_cmp(y));
            fold_b.sort_by(|x, y| x.total_cmp(y));
            for (slot, q) in [(1usize, 0.50), (2, 0.90), (3, 0.95), (4, 0.99)] {
                let qa = quantile_type7_sorted(&fold_a, q);
                let qb = quantile_type7_sorted(&fold_b, q);
                jack_deltas[slot].push(qb - qa);
                if ratio_ests[slot].is_some() {
                    jack_ratios[slot].push(if qa > 0.0 { qb / qa } else { 1.0 });
                }
            }
        }
    }

    let mut estimands = Vec::with_capacity(5);
    for (idx, kind) in ESTIMANDS.into_iter().enumerate() {
        let deltas = std::mem::take(&mut boot_deltas[idx]);
        let ratios = std::mem::take(&mut boot_ratios[idx]);
        let delta_ci = finalize_bca_interval(
            &format!("delta_{}", kind.as_str()),
            delta_ests[idx],
            deltas,
            &jack_deltas[idx],
            alpha,
            zero_paired_diff,
        );
        let ratio_ci = ratio_ests[idx].map(|r| {
            finalize_bca_interval(
                &format!("ratio_{}", kind.as_str()),
                r,
                ratios,
                &jack_ratios[idx],
                alpha,
                zero_paired_diff,
            )
        });
        estimands.push(PairedEstimandComparisonV3 {
            estimand: kind.as_str().to_string(),
            a_estimate: a_ests[idx],
            b_estimate: b_ests[idx],
            delta_estimate: delta_ests[idx],
            delta_ci,
            ratio_estimate: ratio_ests[idx],
            ratio_ci,
        });
    }

    let raw_p = wilcoxon.p_value;
    let adjusted_p = raw_p;
    let p50_rel = if a_ests[1].abs() > 1e-12 {
        (delta_ests[1] / a_ests[1]).abs()
    } else {
        0.0
    };
    let p95_rel = if a_ests[3].abs() > 1e-12 {
        (delta_ests[3] / a_ests[3]).abs()
    } else {
        0.0
    };
    let sesoi_cleared = cliff.abs_delta >= sesoi.min_abs_cliffs_delta
        || p50_rel.max(p95_rel) >= sesoi.min_relative_delta;
    let median_ci_excludes_zero = estimands[1].delta_ci.ci_low > 0.0
        || estimands[1].delta_ci.ci_high < 0.0
        || estimands[0].delta_ci.ci_low > 0.0
        || estimands[0].delta_ci.ci_high < 0.0;
    let statistically_significant = adjusted_p <= alpha && median_ci_excludes_zero;
    let practical_change_proven = statistically_significant && sesoi_cleared;

    Ok(PairedComparisonV3 {
        metric_name: metric_name.to_string(),
        alpha,
        resamples,
        paired_blocks: n,
        arm_a,
        arm_b,
        wilcoxon,
        cliffs_delta: cliff,
        estimands,
        raw_p_value: raw_p,
        adjusted_p_value: adjusted_p,
        sesoi_cleared,
        statistically_significant,
        practical_change_proven,
    })
}

pub fn apply_holm_bonferroni_to_comparisons(
    comparisons: &mut [PairedComparisonV3],
) -> Result<(), BenchError> {
    let raw: Vec<f64> = comparisons.iter().map(|c| c.raw_p_value).collect();
    let adjusted = holm_bonferroni(&raw)?;
    for (comp, adj_p) in comparisons.iter_mut().zip(adjusted) {
        comp.adjusted_p_value = adj_p;
        let ci_excludes_zero = comp.estimands[1].delta_ci.ci_low > 0.0
            || comp.estimands[1].delta_ci.ci_high < 0.0
            || comp.estimands[0].delta_ci.ci_low > 0.0
            || comp.estimands[0].delta_ci.ci_high < 0.0;
        comp.statistically_significant = adj_p <= comp.alpha && ci_excludes_zero;
        comp.practical_change_proven = comp.statistically_significant && comp.sesoi_cleared;
    }
    Ok(())
}

pub fn run_canary_sensitivity_self_test() -> Result<SensitivitySelfTestReport, BenchError> {
    // Realistic right-skewed positive baseline with +25% regression injection
    let n = 60usize;
    let mut rng = 0xAE61_5000_CA9A_0001u64;
    let mut arm_a = Vec::with_capacity(n);
    let mut arm_b = Vec::with_capacity(n);
    for _ in 0..n {
        let u1 = uniform_open0_closed1(&mut rng);
        let u2 = uniform_open0_closed1(&mut rng);
        let base_ns = 1_000.0 + (-u1.ln()) * 120.0;
        let noise_b = 1.0 + (u2 - 0.5) * 0.06;
        arm_a.push(base_ns);
        arm_b.push(base_ns * 1.25 * noise_b);
    }
    let mut comp = evaluate_paired_comparison_v3(
        "canary_sensitivity_plus_25pct",
        &arm_a,
        &arm_b,
        0.05,
        MIN_DEV_BOOTSTRAP_RESAMPLES,
        &SesoiPolicy::default(),
        0xC4A7_0001,
    )?;
    let slice = std::slice::from_mut(&mut comp);
    apply_holm_bonferroni_to_comparisons(slice)?;
    let comp = &slice[0];
    let mean_ci = &comp.estimands[0].delta_ci;
    let passed = comp.adjusted_p_value <= 0.05
        && comp.cliffs_delta.abs_delta >= 0.33
        && comp.practical_change_proven
        && mean_ci.ci_low > 0.0;
    let report = SensitivitySelfTestReport {
        passed,
        injected_slowdown_factor: 1.25,
        observed_ratio: comp.estimands[0].ratio_estimate.unwrap_or(0.0),
        adjusted_p_value: comp.adjusted_p_value,
        cliffs_delta: comp.cliffs_delta.delta,
        mean_delta_ci_low: mean_ci.ci_low,
        quarantine_status: if passed {
            "SENSITIVE_OK".to_string()
        } else {
            "INSENSITIVE_TOY".to_string()
        },
    };
    if !passed {
        return Err(BenchError::SensitivityCheckFailed(format!(
            "p_adj={}, delta={}",
            report.adjusted_p_value, report.cliffs_delta
        )));
    }
    Ok(report)
}
