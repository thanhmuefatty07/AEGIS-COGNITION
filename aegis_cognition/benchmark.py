"""Strict, contamination-aware benchmark primitives (`V2` compat + `aegis-bench-protocol-v3`)."""

from __future__ import annotations

import contextlib
import ctypes
import json
import locale
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from hashlib import blake2b
from typing import Any, Literal, cast


DEFAULT_BOOTSTRAP_RESAMPLES: int = 10_000
MIN_DEV_BOOTSTRAP_RESAMPLES: int = 2_000
JACKKNIFE_BLOCK_COUNT: int = 50
HDR_MAX_VALUE_NS: int = 60_000_000_000
_HDR_SUB_BUCKET_HALF_COUNT: int = 1024
_HDR_SUB_BUCKET_COUNT: int = 2048
_HDR_BUCKET_COUNT: int = 26
_HDR_TOTAL_SLOTS: int = _HDR_SUB_BUCKET_COUNT + _HDR_BUCKET_COUNT * _HDR_SUB_BUCKET_HALF_COUNT

_U64_MASK: int = 0xFFFF_FFFF_FFFF_FFFF
_U64_MOD: int = 1 << 64
_TWO_POW_53: float = float(1 << 53)
_TRIAL_STATUSES = frozenset({"SUCCESS", "FAIL", "TIMEOUT", "REFUSAL", "CANCELLED", "ERROR"})


def _is_exact_int(value: Any) -> bool:
    """Accept integer metadata without allowing bool-as-int coercion."""

    return type(value) is int


def _is_finite_real(value: Any) -> bool:
    """Accept only finite JSON-like numeric values, never bool or strings."""

    if type(value) not in (int, float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError, TypeError, ValueError:
        return False


def _digest(value: Any) -> str:
    payload = asdict(cast(Any, value)) if is_dataclass(value) else value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return blake2b(encoded, digest_size=32).hexdigest()


# ---------------------------------------------------------------------------
# Deterministic PRNG, Quantiles, Normal CDF / Inverse CDF
# ---------------------------------------------------------------------------


def splitmix64_next(state: int) -> tuple[int, int]:
    """Advance SplitMix64 state and return ``(next_state, u64_value)``."""

    next_state = (state + 0x9E37_79B9_7F4A_7C15) & _U64_MASK
    z = next_state
    z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & _U64_MASK
    z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & _U64_MASK
    z = (z ^ (z >> 31)) & _U64_MASK
    return next_state, z


def lemire_bounded_index(state: int, bound: int) -> tuple[int, int]:
    """Sample unbiased integer in ``[0, bound)`` using Lemire's method."""

    if bound <= 1:
        return state, 0
    s = bound
    next_state, x = splitmix64_next(state)
    m = x * s
    low = m & _U64_MASK
    if low < s:
        t = (_U64_MOD - s) % s
        while low < t:
            next_state, x = splitmix64_next(next_state)
            m = x * s
            low = m & _U64_MASK
    return next_state, m >> 64


def uniform_open0_closed1(state: int) -> tuple[int, float]:
    """Return ``(next_state, U)`` with ``U`` strictly in ``(0, 1]``."""

    next_state, val = splitmix64_next(state)
    top53 = (val >> 11) + 1
    return next_state, top53 / _TWO_POW_53


def seed_from_label(label: str, length: int, base_seed: int = 0xAE61_5BEE) -> int:
    """Derive a deterministic 64-bit seed from an estimand label and length."""

    state = (base_seed ^ 0xAE61_5BEE_F00D_CAFE ^ ((length * 0x9E37_79B1) & _U64_MASK)) & _U64_MASK
    for byte in label.encode("utf-8"):
        state = (((state << 7) | (state >> 57)) & _U64_MASK) ^ byte
        state = (state * 0xBF58_476D_1CE4_E5B9) & _U64_MASK
    return state


def quantile_type7_sorted(sorted_vals: Sequence[float], q: float) -> float:
    """Hyndman-Fan Type 7 linear interpolation quantile over ascending values."""

    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    clamped = 0.0 if q < 0.0 else (1.0 if q > 1.0 else q)
    pos = clamped * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def normal_cdf(x: float) -> float:
    """Exact standard normal CDF using ``math.erfc`` without left-tail cancellation."""

    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_inv_cdf(p: float) -> float:
    """High-precision standard normal quantile (Acklam + 2 Newton-Raphson steps)."""

    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    if p == 0.5:
        return 0.0

    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        z = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        z = (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        z = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )

    inv_sqrt_2pi = 0.3989422804014327
    for _ in range(2):
        err = normal_cdf(z) - p
        pdf = inv_sqrt_2pi * math.exp(-0.5 * z * z)
        if pdf > 1e-300:
            z -= err / pdf
    return z


# ---------------------------------------------------------------------------
# Phase 0 V3 Statistical Types & Engine
# ---------------------------------------------------------------------------

EstimandName = Literal["mean", "p50", "p90", "p95", "p99"]
_ESTIMAND_QUANTILES: dict[EstimandName, float | None] = {
    "mean": None,
    "p50": 0.50,
    "p90": 0.90,
    "p95": 0.95,
    "p99": 0.99,
}


@dataclass(frozen=True)
class BcaConfidenceInterval:
    estimand: str
    estimate: float
    ci_low: float
    ci_high: float
    alpha: float
    resamples: int
    z0_hat: float
    a_hat: float
    ci_method: str


@dataclass(frozen=True)
class DistributionV3:
    metric_name: str
    sample_count: int
    mean: float
    stddev: float
    minimum: float
    p50: float
    p90: float
    p95: float
    p99: float
    maximum: float
    mean_ci: BcaConfidenceInterval
    p50_ci: BcaConfidenceInterval
    p90_ci: BcaConfidenceInterval
    p95_ci: BcaConfidenceInterval
    p99_ci: BcaConfidenceInterval

    @classmethod
    def from_samples(
        cls,
        metric_name: str,
        samples: Sequence[float],
        *,
        alpha: float = 0.05,
        resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
        min_resamples: int = MIN_DEV_BOOTSTRAP_RESAMPLES,
        seed: int = 0xAE61_5BEE,
    ) -> DistributionV3:
        vals = _validate_numeric_samples(metric_name, samples, min_len=2)
        if not _is_finite_real(alpha) or not 0.0 < alpha < 0.5:
            raise ValueError("alpha must be in (0, 0.5)")
        if not _is_exact_int(resamples) or resamples < min_resamples:
            raise ValueError(f"resamples must be an exact integer >= {min_resamples}")
        n = len(vals)
        sorted_vals = sorted(vals)
        mean_val = statistics.fmean(sorted_vals)
        stddev_val = statistics.stdev(sorted_vals) if n > 1 else 0.0
        p50_val = quantile_type7_sorted(sorted_vals, 0.50)
        p90_val = quantile_type7_sorted(sorted_vals, 0.90)
        p95_val = quantile_type7_sorted(sorted_vals, 0.95)
        p99_val = quantile_type7_sorted(sorted_vals, 0.99)
        first = vals[0]
        zero_var = all(v == first for v in vals)

        boot_mean: list[float] = []
        boot_p50: list[float] = []
        boot_p90: list[float] = []
        boot_p95: list[float] = []
        boot_p99: list[float] = []
        if zero_var:
            boot_mean = [mean_val] * resamples
            boot_p50 = [p50_val] * resamples
            boot_p90 = [p90_val] * resamples
            boot_p95 = [p95_val] * resamples
            boot_p99 = [p99_val] * resamples
        else:
            state = seed_from_label(metric_name, n, seed)
            mask = _U64_MASK
            mod = _U64_MOD
            t = (mod - n) % n
            n_float = float(n)
            sorted_fn = sorted
            for _ in range(resamples):
                draw: list[float] = []
                total = 0.0
                for _i in range(n):
                    state = (state + 0x9E37_79B9_7F4A_7C15) & mask
                    z = state
                    z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & mask
                    z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & mask
                    x = (z ^ (z >> 31)) & mask
                    m = x * n
                    low = m & mask
                    if low < n:
                        while low < t:
                            state = (state + 0x9E37_79B9_7F4A_7C15) & mask
                            z = state
                            z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & mask
                            z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & mask
                            x = (z ^ (z >> 31)) & mask
                            m = x * n
                            low = m & mask
                    v = vals[m >> 64]
                    draw.append(v)
                    total += v
                boot_mean.append(total / n_float)
                draw_sorted = sorted_fn(draw)
                boot_p50.append(quantile_type7_sorted(draw_sorted, 0.50))
                boot_p90.append(quantile_type7_sorted(draw_sorted, 0.90))
                boot_p95.append(quantile_type7_sorted(draw_sorted, 0.95))
                boot_p99.append(quantile_type7_sorted(draw_sorted, 0.99))

        return cls(
            metric_name=metric_name,
            sample_count=n,
            mean=mean_val,
            stddev=stddev_val,
            minimum=sorted_vals[0],
            p50=p50_val,
            p90=p90_val,
            p95=p95_val,
            p99=p99_val,
            maximum=sorted_vals[-1],
            mean_ci=_finalize_bca_interval(
                "mean",
                mean_val,
                boot_mean,
                () if zero_var else _jackknife_single_arm(vals, "mean"),
                alpha,
                zero_var,
            ),
            p50_ci=_finalize_bca_interval(
                "p50",
                p50_val,
                boot_p50,
                () if zero_var else _jackknife_single_arm(vals, "p50"),
                alpha,
                zero_var,
            ),
            p90_ci=_finalize_bca_interval(
                "p90",
                p90_val,
                boot_p90,
                () if zero_var else _jackknife_single_arm(vals, "p90"),
                alpha,
                zero_var,
            ),
            p95_ci=_finalize_bca_interval(
                "p95",
                p95_val,
                boot_p95,
                () if zero_var else _jackknife_single_arm(vals, "p95"),
                alpha,
                zero_var,
            ),
            p99_ci=_finalize_bca_interval(
                "p99",
                p99_val,
                boot_p99,
                () if zero_var else _jackknife_single_arm(vals, "p99"),
                alpha,
                zero_var,
            ),
        )


def _validate_numeric_samples(label: str, samples: Sequence[float], *, min_len: int = 1) -> tuple[float, ...]:
    if not samples:
        raise ValueError(f"{label}: samples must not be empty")
    if len(samples) < min_len:
        raise ValueError(f"{label}: expected at least {min_len} samples, got {len(samples)}")
    out: list[float] = []
    for item in samples:
        if not _is_finite_real(item):
            raise ValueError(f"{label}: samples must all be finite real numbers")
        out.append(float(item))
    return tuple(out)


def _eval_estimand(vals: Sequence[float], estimand: EstimandName) -> float:
    q = _ESTIMAND_QUANTILES[estimand]
    if q is None:
        return statistics.fmean(vals)
    return quantile_type7_sorted(sorted(vals), q)


def _compute_acceleration(jack_vals: Sequence[float]) -> tuple[float, bool]:
    if len(jack_vals) < 2:
        return 0.0, True
    mean_jack = statistics.fmean(jack_vals)
    sum_sq = 0.0
    sum_cube = 0.0
    for val in jack_vals:
        diff = mean_jack - val
        sq = diff * diff
        sum_sq += sq
        sum_cube += sq * diff
    if sum_sq <= 1e-30:
        return 0.0, True
    denom = 6.0 * (sum_sq**1.5)
    if denom <= 1e-30:
        return 0.0, True
    return sum_cube / denom, False


def _jackknife_single_arm(vals: tuple[float, ...], estimand: EstimandName) -> tuple[float, ...]:
    n = len(vals)
    q = _ESTIMAND_QUANTILES[estimand]
    if q is None and n < 200:
        total = math.fsum(vals)
        denom = float(n - 1)
        return tuple((total - val) / denom for val in vals)
    if n < 200:
        return tuple(_eval_estimand(vals[:i] + vals[i + 1 :], estimand) for i in range(n))
    k_blocks = min(JACKKNIFE_BLOCK_COUNT, n)
    out: list[float] = []
    for block_idx in range(k_blocks):
        start = (block_idx * n) // k_blocks
        end = ((block_idx + 1) * n) // k_blocks
        fold = vals[:start] + vals[end:]
        if fold:
            out.append(_eval_estimand(fold, estimand))
    return tuple(out)


def _finalize_bca_interval(
    estimand: str,
    estimate: float,
    boot_replicates: list[float],
    jack_vals: Sequence[float],
    alpha: float,
    zero_variance_input: bool,
) -> BcaConfidenceInterval:
    resamples = len(boot_replicates)
    if zero_variance_input:
        return BcaConfidenceInterval(
            estimand=estimand,
            estimate=estimate,
            ci_low=estimate,
            ci_high=estimate,
            alpha=alpha,
            resamples=resamples,
            z0_hat=0.0,
            a_hat=0.0,
            ci_method="degenerate_zero_variance",
        )

    less = 0
    eq = 0
    for rep in boot_replicates:
        if rep < estimate:
            less += 1
        elif rep == estimate:
            eq += 1
    p0 = (less + 0.5 * eq) / resamples
    eps = 0.5 / (resamples + 1)
    p0_clipped = eps if p0 < eps else (1.0 - eps if p0 > 1.0 - eps else p0)
    z0_hat = normal_inv_cdf(p0_clipped)

    a_hat, zero_accel = _compute_acceleration(jack_vals)
    z_lo = normal_inv_cdf(0.5 * alpha)
    z_hi = normal_inv_cdf(1.0 - 0.5 * alpha)
    denom_lo = 1.0 - a_hat * (z0_hat + z_lo)
    denom_hi = 1.0 - a_hat * (z0_hat + z_hi)
    if denom_lo <= 1e-9 or denom_hi <= 1e-9:
        a_hat = 0.0
        zero_accel = True
        denom_lo = 1.0
        denom_hi = 1.0

    alpha_lo = normal_cdf(z0_hat + (z0_hat + z_lo) / denom_lo)
    alpha_hi = normal_cdf(z0_hat + (z0_hat + z_hi) / denom_hi)
    alpha_lo = eps if alpha_lo < eps else (1.0 - eps if alpha_lo > 1.0 - eps else alpha_lo)
    alpha_hi = eps if alpha_hi < eps else (1.0 - eps if alpha_hi > 1.0 - eps else alpha_hi)
    if alpha_lo > alpha_hi:
        alpha_lo, alpha_hi = alpha_hi, alpha_lo

    boot_sorted = sorted(boot_replicates)
    ci_low = quantile_type7_sorted(boot_sorted, alpha_lo)
    ci_high = quantile_type7_sorted(boot_sorted, alpha_hi)
    if ci_high == ci_low and boot_sorted[-1] > boot_sorted[0]:
        fallback_lo = quantile_type7_sorted(boot_sorted, 0.5 * alpha)
        fallback_hi = quantile_type7_sorted(boot_sorted, 1.0 - 0.5 * alpha)
        if fallback_hi > fallback_lo:
            ci_low, ci_high = fallback_lo, fallback_hi
        else:
            ci_low, ci_high = boot_sorted[0], boot_sorted[-1]

    return BcaConfidenceInterval(
        estimand=estimand,
        estimate=estimate,
        ci_low=min(ci_low, ci_high),
        ci_high=max(ci_low, ci_high),
        alpha=alpha,
        resamples=resamples,
        z0_hat=z0_hat,
        a_hat=a_hat,
        ci_method="bc_zero_accel" if zero_accel else "bca",
    )


def bca_confidence_interval(
    label: str,
    samples: Sequence[float],
    *,
    estimand: EstimandName = "mean",
    alpha: float = 0.05,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    min_resamples: int = MIN_DEV_BOOTSTRAP_RESAMPLES,
    seed: int = 0xAE61_5BEE,
) -> BcaConfidenceInterval:
    """Compute a deterministic BCa confidence interval with Lemire sampling and block jackknife."""

    vals = _validate_numeric_samples(label, samples, min_len=2)
    if estimand not in _ESTIMAND_QUANTILES:
        raise ValueError(f"unsupported estimand: {estimand}")
    if not _is_finite_real(alpha) or not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    if not _is_exact_int(resamples) or resamples < min_resamples:
        raise ValueError(f"resamples must be an exact integer >= {min_resamples}")

    n = len(vals)
    estimate = _eval_estimand(vals, estimand)
    first = vals[0]
    zero_var = all(v == first for v in vals)
    if zero_var:
        return _finalize_bca_interval(estimand, estimate, [estimate] * resamples, (), alpha, True)

    state = seed_from_label(label, n, seed) ^ seed_from_label(estimand, n, 17)
    q = _ESTIMAND_QUANTILES[estimand]
    boot: list[float] = []
    if q is None:
        n_float = float(n)
        for _ in range(resamples):
            total = 0.0
            for _i in range(n):
                state, idx = lemire_bounded_index(state, n)
                total += vals[idx]
            boot.append(total / n_float)
    else:
        sorted_fn = sorted
        for _ in range(resamples):
            draw: list[float] = []
            for _i in range(n):
                state, idx = lemire_bounded_index(state, n)
                draw.append(vals[idx])
            boot.append(quantile_type7_sorted(sorted_fn(draw), q))

    jack_vals = _jackknife_single_arm(vals, estimand)
    return _finalize_bca_interval(estimand, estimate, boot, jack_vals, alpha, False)


# ---------------------------------------------------------------------------
# Non-Parametric Tests, Binary Outcomes, Effect Sizes & Multiple Comparisons
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WilcoxonResult:
    n_total: int
    n_nonzero: int
    w_plus: float
    w_minus: float
    statistic: float
    p_value: float
    hodges_lehmann_pseudomedian: float
    exact: bool


@dataclass(frozen=True)
class MannWhitneyResult:
    n_a: int
    n_b: int
    u_a: float
    u_b: float
    u_statistic: float
    z_score: float
    p_value: float


@dataclass(frozen=True)
class WilsonScoreCi:
    successes: int
    total: int
    proportion: float
    ci_low: float
    ci_high: float
    alpha: float


@dataclass(frozen=True)
class McNemarResult:
    n01: int
    n10: int
    discordant_total: int
    p_value: float


@dataclass(frozen=True)
class NewcombeWilsonDiffCi:
    diff: float
    ci_low: float
    ci_high: float
    p_a: float
    p_b: float
    alpha: float


@dataclass(frozen=True)
class CliffsDeltaResult:
    delta: float
    abs_delta: float
    magnitude: str


@dataclass(frozen=True)
class SesoiPolicy:
    min_abs_cliffs_delta: float = 0.147
    min_relative_delta: float = 0.05
    binary_margin_pp: float = 0.02
    non_inferiority_margin_pp: float = -0.01

    def validate(self) -> None:
        for field_val in (self.min_abs_cliffs_delta, self.min_relative_delta, self.binary_margin_pp):
            if not _is_finite_real(field_val) or field_val < 0.0:
                raise ValueError("SESOI positive margins must be finite and non-negative")
        if not _is_finite_real(self.non_inferiority_margin_pp):
            raise ValueError("non_inferiority_margin_pp must be finite")


def hodges_lehmann_pseudomedian(diffs: Sequence[float]) -> float:
    """Compute exact Hodges-Lehmann pseudomedian ``median_{i <= j} (d_i + d_j)/2``."""

    vals = _validate_numeric_samples("hodges_lehmann", diffs, min_len=1)
    n = len(vals)
    if n == 1:
        return vals[0]
    s = sorted(vals)
    total_pairs = (n * (n + 1)) // 2
    if n <= 400:
        walsh = sorted(0.5 * (s[i] + s[j]) for i in range(n) for j in range(i, n))
        mid = total_pairs // 2
        if total_pairs % 2 == 1:
            return walsh[mid]
        return 0.5 * (walsh[mid - 1] + walsh[mid])

    def _select_kth(target_k: int) -> float:
        left = list(range(n))
        right = [n - 1] * n
        state = 0x517C_C1B7_2722_0A95 ^ n
        while True:
            active = sum(right[i] - left[i] + 1 for i in range(n) if left[i] <= right[i])
            if active == 0:
                return s[0]
            state, pick = lemire_bounded_index(state, active)
            acc = 0
            pivot = 0.0
            for i in range(n):
                if left[i] <= right[i]:
                    row_cnt = right[i] - left[i] + 1
                    if pick < acc + row_cnt:
                        pivot = s[i] + s[left[i] + (pick - acc)]
                        break
                    acc += row_cnt

            count_lt = 0
            count_le = 0
            j_lt = n - 1
            j_le = n - 1
            for i in range(n):
                while j_lt >= i and s[i] + s[j_lt] >= pivot:
                    j_lt -= 1
                if j_lt >= i:
                    count_lt += j_lt - i + 1
                while j_le >= i and s[i] + s[j_le] > pivot:
                    j_le -= 1
                if j_le >= i:
                    count_le += j_le - i + 1

            if target_k < count_lt:
                col = n - 1
                for i in range(n):
                    while col >= left[i] and s[i] + s[col] >= pivot:
                        col -= 1
                    if col < i:
                        left[i] = 1
                        right[i] = 0
                    else:
                        right[i] = col
            elif target_k >= count_le:
                col = 0
                for i in range(n):
                    if col < i:
                        col = i
                    while col < n and s[i] + s[col] <= pivot:
                        col += 1
                    if col > left[i]:
                        left[i] = col
            else:
                return 0.5 * pivot

    mid = total_pairs // 2
    if total_pairs % 2 == 1:
        return _select_kth(mid)
    return 0.5 * (_select_kth(mid - 1) + _select_kth(mid))


def wilcoxon_signed_rank_test(arm_a: Sequence[float], arm_b: Sequence[float]) -> WilcoxonResult:
    """Two-sided paired Wilcoxon signed-rank test on ``d_i = B_i - A_i`` + Hodges-Lehmann."""

    a = _validate_numeric_samples("wilcoxon_a", arm_a, min_len=1)
    b = _validate_numeric_samples("wilcoxon_b", arm_b, min_len=1)
    if len(a) != len(b):
        raise ValueError("paired Wilcoxon requires equal-length arms")
    diffs = tuple(vb - va for va, vb in zip(a, b, strict=True))
    hl = hodges_lehmann_pseudomedian(diffs)

    nonzero = sorted(((abs(d), d > 0.0) for d in diffs if d != 0.0), key=lambda pair: pair[0])
    m = len(nonzero)
    if m == 0:
        return WilcoxonResult(
            n_total=len(a),
            n_nonzero=0,
            w_plus=0.0,
            w_minus=0.0,
            statistic=0.0,
            p_value=1.0,
            hodges_lehmann_pseudomedian=hl,
            exact=True,
        )

    ranks = [0.0] * m
    tie_cube_sum = 0.0
    i = 0
    while i < m:
        j = i + 1
        while j < m and nonzero[j][0] == nonzero[i][0]:
            j += 1
        avg_rank = 0.5 * (i + 1 + j)
        for idx in range(i, j):
            ranks[idx] = avg_rank
        t = float(j - i)
        if t > 1.0:
            tie_cube_sum += t * t * t - t
        i = j

    w_plus = sum(rank for rank, (_, pos) in zip(ranks, nonzero, strict=True) if pos)
    total_rank_sum = 0.5 * m * (m + 1)
    w_minus = total_rank_sum - w_plus
    statistic = min(w_plus, w_minus)

    if m <= 25:
        total2 = m * (m + 1)
        twice_ranks = [round(r * 2.0) for r in ranks]
        w_plus2 = sum(r2 for r2, (_, pos) in zip(twice_ranks, nonzero, strict=True) if pos)
        ways = [0] * (total2 + 1)
        ways[0] = 1
        for r2 in twice_ranks:
            for s_idx in range(total2, r2 - 1, -1):
                ways[s_idx] += ways[s_idx - r2]
        min_s = min(w_plus2, total2 - w_plus2)
        tail = sum(ways[: min_s + 1])
        p_val = min(1.0, (2.0 * tail) / float(1 << m))
        exact = True
    else:
        mean_w = 0.25 * m * (m + 1)
        var_w = (m * (m + 1) * (2 * m + 1)) / 24.0 - tie_cube_sum / 48.0
        if var_w <= 1e-30:
            p_val = 1.0
        else:
            z = max(0.0, abs(w_plus - mean_w) - 0.5) / math.sqrt(var_w)
            p_val = min(1.0, max(0.0, 2.0 * (1.0 - normal_cdf(z))))
        exact = False

    return WilcoxonResult(
        n_total=len(a),
        n_nonzero=m,
        w_plus=w_plus,
        w_minus=w_minus,
        statistic=statistic,
        p_value=p_val,
        hodges_lehmann_pseudomedian=hl,
        exact=exact,
    )


def mann_whitney_u_test(arm_a: Sequence[float], arm_b: Sequence[float]) -> MannWhitneyResult:
    """Two-sided unpaired Mann-Whitney U test with tie correction."""

    a = _validate_numeric_samples("mann_whitney_a", arm_a, min_len=1)
    b = _validate_numeric_samples("mann_whitney_b", arm_b, min_len=1)
    n_a = len(a)
    n_b = len(b)
    total_n = n_a + n_b
    tagged = sorted(((v, False) for v in a), key=lambda pair: pair[0])
    tagged.extend((v, True) for v in b)
    tagged.sort(key=lambda pair: pair[0])

    rank_sum_b = 0.0
    tie_cube_sum = 0.0
    i = 0
    while i < total_n:
        j = i + 1
        while j < total_n and tagged[j][0] == tagged[i][0]:
            j += 1
        avg_rank = 0.5 * (i + 1 + j)
        b_cnt = sum(1 for _, is_b in tagged[i:j] if is_b)
        rank_sum_b += b_cnt * avg_rank
        t = float(j - i)
        if t > 1.0:
            tie_cube_sum += t * t * t - t
        i = j

    u_b = rank_sum_b - 0.5 * n_b * (n_b + 1)
    u_a = n_a * n_b - u_b
    u_stat = min(u_a, u_b)
    mu = 0.5 * n_a * n_b
    tie_term = tie_cube_sum / (total_n * (total_n - 1)) if total_n > 1 else 0.0
    sigma_sq = (n_a * n_b / 12.0) * ((total_n + 1.0) - tie_term)
    if sigma_sq <= 1e-30:
        z_score = 0.0
        p_value = 1.0
    else:
        sigma = math.sqrt(sigma_sq)
        z_score = (u_b - mu) / sigma
        z_abs = max(0.0, abs(u_b - mu) - 0.5) / sigma
        p_value = min(1.0, max(0.0, 2.0 * (1.0 - normal_cdf(z_abs))))

    return MannWhitneyResult(
        n_a=n_a,
        n_b=n_b,
        u_a=u_a,
        u_b=u_b,
        u_statistic=u_stat,
        z_score=z_score,
        p_value=p_value,
    )


def wilson_score_ci(successes: int, total: int, *, alpha: float = 0.05) -> WilsonScoreCi:
    """Compute Wilson score binomial confidence interval."""

    if not _is_exact_int(successes) or not _is_exact_int(total) or total <= 0 or not 0 <= successes <= total:
        raise ValueError("total must be positive integer and 0 <= successes <= total")
    if not _is_finite_real(alpha) or not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    p_hat = successes / total
    z = normal_inv_cdf(1.0 - 0.5 * alpha)
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p_hat + z2 / (2.0 * total)) / denom
    half = (z * math.sqrt((p_hat * (1.0 - p_hat)) / total + z2 / (4.0 * total * total))) / denom
    return WilsonScoreCi(
        successes=successes,
        total=total,
        proportion=p_hat,
        ci_low=max(0.0, min(1.0, center - half)),
        ci_high=max(0.0, min(1.0, center + half)),
        alpha=alpha,
    )


def exact_mcnemar_test(n01: int, n10: int) -> McNemarResult:
    """Exact two-sided McNemar binomial test over discordant pairs ``(n01, n10)``."""

    if not _is_exact_int(n01) or not _is_exact_int(n10) or n01 < 0 or n10 < 0:
        raise ValueError("n01 and n10 must be non-negative integers")
    m = n01 + n10
    if m == 0:
        return McNemarResult(n01=0, n10=0, discordant_total=0, p_value=1.0)
    k_max = min(n01, n10)
    log_n_fact = math.lgamma(m + 1)
    log_half_pow = -m * math.log(2.0)
    one_sided = math.fsum(
        math.exp(log_n_fact - math.lgamma(k + 1) - math.lgamma(m - k + 1) + log_half_pow) for k in range(k_max + 1)
    )
    return McNemarResult(n01=n01, n10=n10, discordant_total=m, p_value=min(1.0, 2.0 * one_sided))


def newcombe_wilson_diff_ci(
    n11: int,
    n10: int,
    n01: int,
    n00: int,
    *,
    alpha: float = 0.05,
) -> NewcombeWilsonDiffCi:
    """Newcombe (1998) Method 10 paired difference CI for ``p_B - p_A``."""

    for count in (n11, n10, n01, n00):
        if not _is_exact_int(count) or count < 0:
            raise ValueError("contingency table counts must be non-negative integers")
    n = n11 + n10 + n01 + n00
    if n == 0:
        raise ValueError("contingency table cannot be empty")
    w1 = wilson_score_ci(n11 + n10, n, alpha=alpha)
    w2 = wilson_score_ci(n11 + n01, n, alpha=alpha)
    p_a = w1.proportion
    p_b = w2.proportion
    diff = p_b - p_a

    e = float(n11 + n10)
    f = float(n01 + n00)
    g = float(n11 + n01)
    h = float(n10 + n00)
    denom = math.sqrt(e * f * g * h)
    if denom <= 0.0:
        phi = 0.0
    else:
        psi = float(n11 * n00 - n10 * n01)
        if psi > 0.0:
            half_n = 0.5 * n
            phi = (psi - half_n) / denom if psi > half_n else 0.0
        else:
            phi = psi / denom

    d_lo_sq = (p_b - w2.ci_low) ** 2 - 2.0 * phi * (p_b - w2.ci_low) * (w1.ci_high - p_a) + (w1.ci_high - p_a) ** 2
    d_hi_sq = (w2.ci_high - p_b) ** 2 - 2.0 * phi * (w2.ci_high - p_b) * (p_a - w1.ci_low) + (p_a - w1.ci_low) ** 2
    return NewcombeWilsonDiffCi(
        diff=diff,
        ci_low=max(-1.0, min(1.0, diff - math.sqrt(max(0.0, d_lo_sq)))),
        ci_high=max(-1.0, min(1.0, diff + math.sqrt(max(0.0, d_hi_sq)))),
        p_a=p_a,
        p_b=p_b,
        alpha=alpha,
    )


def unbiased_pass_at_k(n: int, c: int, k: int) -> float:
    """Compute unbiased estimator ``pass@k = 1 - C(n-c, k)/C(n, k)``."""

    if not (_is_exact_int(n) and _is_exact_int(c) and _is_exact_int(k)) or n <= 0 or k <= 0 or k > n or not 0 <= c <= n:
        raise ValueError("invalid pass@k arguments")
    if n - c < k:
        return 1.0
    prod = 1.0
    for i in range(k):
        prod *= (n - c - i) / (n - i)
    return max(0.0, min(1.0, 1.0 - prod))


def pass_at_k_with_bca_ci(
    tasks: Sequence[tuple[int, int]],
    *,
    k: int,
    alpha: float = 0.05,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    min_resamples: int = MIN_DEV_BOOTSTRAP_RESAMPLES,
    seed: int = 0xAE61_5BEE,
) -> BcaConfidenceInterval:
    """Compute mean ``pass@k`` across tasks with BCa confidence interval."""

    if len(tasks) < 2:
        raise ValueError("pass@k BCa CI requires at least 2 task instances")
    scores = [unbiased_pass_at_k(n, c, k) for n, c in tasks]
    return bca_confidence_interval(
        f"pass@{k}",
        scores,
        estimand="mean",
        alpha=alpha,
        resamples=resamples,
        min_resamples=min_resamples,
        seed=seed,
    )


def cliffs_delta(arm_a: Sequence[float], arm_b: Sequence[float]) -> CliffsDeltaResult:
    """Compute Cliff's delta ``(1 / (n_a * n_b)) * sum sign(b_j - a_i)`` with Romano magnitude."""

    a = sorted(_validate_numeric_samples("cliffs_delta_a", arm_a, min_len=1))
    b = _validate_numeric_samples("cliffs_delta_b", arm_b, min_len=1)
    n_a = len(a)
    n_b = len(b)
    net = 0
    for vb in b:
        lt = bisect_left(a, vb)
        le = bisect_right(a, vb)
        gt = n_a - le
        net += lt - gt
    delta = net / float(n_a * n_b)
    abs_d = abs(delta)
    if abs_d < 0.147:
        mag = "negligible"
    elif abs_d < 0.33:
        mag = "small"
    elif abs_d < 0.474:
        mag = "medium"
    else:
        mag = "large"
    return CliffsDeltaResult(delta=delta, abs_delta=abs_d, magnitude=mag)


def holm_bonferroni(p_values: Sequence[float]) -> tuple[float, ...]:
    """Holm-Bonferroni step-down adjusted p-values preserving original index order."""

    if not p_values:
        return ()
    for p in p_values:
        if not _is_finite_real(p) or not 0.0 <= p <= 1.0:
            raise ValueError("p-values must be finite and in [0, 1]")
    m = len(p_values)
    indexed = sorted(enumerate(float(p) for p in p_values), key=lambda pair: pair[1])
    adjusted = [1.0] * m
    running_max = 0.0
    for rank, (orig_idx, raw_p) in enumerate(indexed):
        candidate = min(1.0, raw_p * float(m - rank))
        if candidate > running_max:
            running_max = candidate
        adjusted[orig_idx] = running_max
    return tuple(adjusted)


# ---------------------------------------------------------------------------
# Environment Fingerprint V3 & Contamination Gate
# ---------------------------------------------------------------------------


def _detect_ram_bytes() -> int:
    if sys.platform == "win32":
        win_fields: list[tuple[str, Any]] = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]
        memory_status_cls: Any = type("_MemoryStatusEx", (ctypes.Structure,), {"_fields_": win_fields})
        stat = memory_status_cls()
        stat.dwLength = ctypes.sizeof(memory_status_cls)
        try:
            kernel32: Any = getattr(ctypes, "windll", None)
            if kernel32 is not None and kernel32.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except OSError, AttributeError:
            return 0
    try:
        sysconf_fn: Any = getattr(os, "sysconf", None)
        if callable(sysconf_fn):
            raw_pages = sysconf_fn("SC_PHYS_PAGES")
            raw_page_size = sysconf_fn("SC_PAGE_SIZE")
            if isinstance(raw_pages, int) and isinstance(raw_page_size, int) and raw_pages > 0 and raw_page_size > 0:
                return raw_pages * raw_page_size
    except OSError, ValueError, TypeError:
        return 0
    return 0


def _best_effort_git_state() -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        return (sha or "unknown", porcelain == "")
    except OSError, subprocess.SubprocessError:
        return "unknown", False


def _best_effort_load_avg() -> float:
    try:
        getloadavg_fn: Any = getattr(os, "getloadavg", None)
        if callable(getloadavg_fn):
            load_vals = cast(tuple[float, float, float], getloadavg_fn())
            return float(load_vals[0])
    except OSError, TypeError, ValueError:
        return 0.0
    return 0.0


@dataclass(frozen=True)
class EnvironmentFingerprintV3:
    git_sha: str
    worktree_clean: bool
    cpu_model: str
    cpu_governor: str
    pinned_cores: str
    logical_cpus: int
    physical_cpus: int
    ram_bytes: int
    os_kernel: str
    rustc_version: str
    python_version: str
    allocator: str
    locale_name: str
    network_policy: str
    pre_run_load_avg: float = 0.0
    post_run_load_avg: float = 0.0
    baseline_cpu_mhz: float = 0.0
    measured_cpu_mhz: float = 0.0
    fs_leak_detected: bool = False
    sandbox_leak_detected: bool = False

    @classmethod
    def capture(
        cls,
        *,
        network_policy: str,
        rustc_version: str = "unknown",
        cpu_governor: str = "unknown",
        pinned_cores: str = "unpinned",
        allocator: str = "pymalloc+system",
        baseline_cpu_mhz: float = 0.0,
        measured_cpu_mhz: float = 0.0,
        pre_run_load_avg: float | None = None,
        post_run_load_avg: float | None = None,
        fs_leak_detected: bool = False,
        sandbox_leak_detected: bool = False,
    ) -> EnvironmentFingerprintV3:
        git_sha, worktree_clean = _best_effort_git_state()
        logical = os.cpu_count() or 1
        physical = max(1, logical // 2)
        load_now = _best_effort_load_avg()
        return cls(
            git_sha=git_sha,
            worktree_clean=worktree_clean,
            cpu_model=platform.processor() or platform.machine() or "unknown",
            cpu_governor=cpu_governor,
            pinned_cores=pinned_cores,
            logical_cpus=logical,
            physical_cpus=physical,
            ram_bytes=_detect_ram_bytes(),
            os_kernel=f"{platform.system() or 'unknown'}-{platform.release() or 'unknown'}",
            rustc_version=rustc_version,
            python_version=platform.python_version(),
            allocator=allocator,
            locale_name=locale.getpreferredencoding(False) or "unknown",
            network_policy=network_policy,
            pre_run_load_avg=load_now if pre_run_load_avg is None else pre_run_load_avg,
            post_run_load_avg=load_now if post_run_load_avg is None else post_run_load_avg,
            baseline_cpu_mhz=baseline_cpu_mhz,
            measured_cpu_mhz=measured_cpu_mhz,
            fs_leak_detected=fs_leak_detected,
            sandbox_leak_detected=sandbox_leak_detected,
        )

    def evaluate_contamination(self, *, enforce_clean_worktree: bool = False) -> tuple[str, tuple[str, ...]]:
        reasons: list[str] = []
        if not _is_exact_int(self.logical_cpus) or self.logical_cpus < 1:
            reasons.append("logical_cpus_invalid")
        if not _is_exact_int(self.physical_cpus) or self.physical_cpus < 1:
            reasons.append("physical_cpus_invalid")
        if not _is_exact_int(self.ram_bytes) or self.ram_bytes < 0:
            reasons.append("ram_bytes_invalid")
        if type(self.worktree_clean) is not bool:
            reasons.append("worktree_clean_invalid")
        if type(self.fs_leak_detected) is not bool or type(self.sandbox_leak_detected) is not bool:
            reasons.append("leak_flags_invalid")

        load_limit = 0.25 * max(1, self.physical_cpus if _is_exact_int(self.physical_cpus) else 1)
        if (
            not _is_finite_real(self.pre_run_load_avg)
            or self.pre_run_load_avg < 0.0
            or self.pre_run_load_avg > load_limit
        ):
            reasons.append("pre_run_load_avg_exceeded")
        if (
            not _is_finite_real(self.post_run_load_avg)
            or self.post_run_load_avg < 0.0
            or self.post_run_load_avg > load_limit
        ):
            reasons.append("post_run_load_avg_exceeded")

        if (
            _is_finite_real(self.baseline_cpu_mhz)
            and _is_finite_real(self.measured_cpu_mhz)
            and self.baseline_cpu_mhz > 0.0
        ):
            drop_ratio = max(0.0, (self.baseline_cpu_mhz - self.measured_cpu_mhz) / self.baseline_cpu_mhz)
            if drop_ratio > 0.10:
                reasons.append("cpu_thermal_throttling_gt_10pct")
        elif not (_is_finite_real(self.baseline_cpu_mhz) and _is_finite_real(self.measured_cpu_mhz)):
            reasons.append("cpu_mhz_invalid")

        if self.fs_leak_detected is True:
            reasons.append("fs_leak_detected")
        if self.sandbox_leak_detected is True:
            reasons.append("sandbox_leak_detected")
        if enforce_clean_worktree and self.worktree_clean is False:
            reasons.append("dirty_worktree_in_strict_run")

        for key, raw in (
            ("git_sha", self.git_sha),
            ("cpu_model", self.cpu_model),
            ("cpu_governor", self.cpu_governor),
            ("pinned_cores", self.pinned_cores),
            ("os_kernel", self.os_kernel),
            ("rustc_version", self.rustc_version),
            ("python_version", self.python_version),
            ("allocator", self.allocator),
            ("locale_name", self.locale_name),
            ("network_policy", self.network_policy),
        ):
            if type(raw) is not str or not raw.strip():
                reasons.append(f"missing_{key}")

        return ("CONTAMINATED" if reasons else "CLEAN", tuple(dict.fromkeys(reasons)))

    @property
    def environment_hash(self) -> str:
        verdict, reasons = self.evaluate_contamination()
        if verdict == "CONTAMINATED" and any(r.startswith("missing_") or r.endswith("_invalid") for r in reasons):
            raise ValueError(f"incomplete benchmark environment fingerprint v3: {','.join(reasons)}")
        return _digest({"schema": "aegis-environment-fingerprint-v3", **asdict(self)})


# ---------------------------------------------------------------------------
# Protocol V3 & Paired AB/BA Evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkProtocolV3:
    name: str
    metric: str
    direction: str = "higher_is_better"
    alpha: float = 0.05
    min_trials: int = 30
    warmups: int = 10
    paired_blocks: int = 30
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES
    ci_enforced: bool = True
    preregistered_seeds: tuple[int, ...] = ()
    frozen_split_hash: str = ""
    contamination_checks: tuple[str, ...] = ()
    percentile: int | None = None
    sesoi: SesoiPolicy = field(default_factory=SesoiPolicy)

    def validate(self) -> None:
        if (
            type(self.name) is not str
            or type(self.metric) is not str
            or type(self.direction) is not str
            or type(self.frozen_split_hash) is not str
            or not self.name.strip()
            or not self.metric.strip()
            or not self.frozen_split_hash.strip()
        ):
            raise ValueError("benchmark name, metric and frozen_split_hash are required")
        if self.direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("benchmark direction must be higher_is_better or lower_is_better")
        if not _is_finite_real(self.alpha) or not 0.0 < self.alpha < 0.5:
            raise ValueError("alpha must be in (0, 0.5)")
        if (
            not _is_exact_int(self.min_trials)
            or not _is_exact_int(self.warmups)
            or not _is_exact_int(self.paired_blocks)
            or self.min_trials < 30
            or self.warmups < 10
            or self.paired_blocks < 30
        ):
            raise ValueError("V3 protocol requires >=30 trials, >=10 warmups, >=30 paired blocks")
        min_boot = DEFAULT_BOOTSTRAP_RESAMPLES if self.ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
        if not _is_exact_int(self.bootstrap_resamples) or self.bootstrap_resamples < min_boot:
            raise ValueError(f"bootstrap_resamples must be an exact int >= {min_boot}")
        if type(self.preregistered_seeds) not in (list, tuple) or len(self.preregistered_seeds) < 5:
            raise ValueError("V3 protocol requires at least 5 unique preregistered integer seeds")
        if any(not _is_exact_int(seed) for seed in self.preregistered_seeds):
            raise ValueError("preregistered_seeds must contain exact integers")
        if len(set(self.preregistered_seeds)) != len(self.preregistered_seeds):
            raise ValueError("preregistered_seeds must be unique")
        if (
            type(self.contamination_checks) not in (list, tuple)
            or not self.contamination_checks
            or any(type(item) is not str or not item.strip() for item in self.contamination_checks)
        ):
            raise ValueError("contamination_checks must be non-empty strings")
        if self.percentile is not None and (
            not _is_exact_int(self.percentile) or self.percentile not in {50, 90, 95, 99}
        ):
            raise ValueError("percentile must be None or one of {50, 90, 95, 99}")
        if self.percentile == 95 and self.min_trials < 100:
            raise ValueError("p95 requires at least 100 observations")
        if self.percentile == 99 and self.min_trials < 1000:
            raise ValueError("p99 requires at least 1000 observations")
        if type(self.sesoi) is not SesoiPolicy:
            raise ValueError("sesoi must be SesoiPolicy")
        self.sesoi.validate()

    @property
    def protocol_hash(self) -> str:
        self.validate()
        return _digest({"schema": "aegis-bench-protocol-v3", **asdict(self)})


@dataclass(frozen=True)
class BenchmarkResultV3:
    protocol_hash: str
    status: str
    distribution: DistributionV3 | None
    primary_ci: BcaConfidenceInterval | None
    trial_count: int
    valid_trial_count: int
    warmup_count: int
    paired_block_count: int
    environment_hash: str
    contamination_verdict: str
    contamination_flags: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    artifact_hash: str
    truth_claim: bool = False


@dataclass(frozen=True)
class PairedEstimandComparisonV3:
    estimand: EstimandName
    a_estimate: float
    b_estimate: float
    delta_estimate: float
    delta_ci: BcaConfidenceInterval
    ratio_estimate: float | None
    ratio_ci: BcaConfidenceInterval | None


@dataclass(frozen=True)
class PairedBenchmarkComparisonV3:
    protocol_hash: str
    metric_name: str
    status: str
    alpha: float
    resamples: int
    paired_blocks: int
    warmup_count: int
    arm_a: DistributionV3 | None
    arm_b: DistributionV3 | None
    wilcoxon: WilcoxonResult | None
    cliffs_delta: CliffsDeltaResult | None
    estimands: tuple[PairedEstimandComparisonV3, ...]
    raw_p_value: float | None
    adjusted_p_value: float | None
    sesoi_cleared: bool
    statistically_significant: bool
    practical_change_proven: bool
    environment_hash: str
    contamination_flags: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    artifact_hash: str


def evaluate_benchmark_v3(
    protocol: BenchmarkProtocolV3,
    trials: Sequence[Any],
    *,
    warmups: int,
    paired_blocks: int,
    environment: EnvironmentFingerprintV3,
    baseline: float | None = None,
    contamination_flags: Sequence[str] = (),
    enforce_clean_worktree: bool = False,
) -> BenchmarkResultV3:
    """Strict V3 single-arm benchmark evaluation without implicit warmup/block fabrication."""

    if type(protocol) is not BenchmarkProtocolV3:
        raise TypeError("protocol must be BenchmarkProtocolV3")
    failures: list[str] = []
    try:
        protocol.validate()
    except (TypeError, ValueError, AttributeError) as exc:
        failures.append(f"protocol_invalid:{type(exc).__name__}")

    if type(environment) is not EnvironmentFingerprintV3:
        raise TypeError("environment must be EnvironmentFingerprintV3")
    contam_verdict, env_reasons = environment.evaluate_contamination(enforce_clean_worktree=enforce_clean_worktree)
    env_hash = ""
    if contam_verdict == "CONTAMINATED":
        failures.extend(f"CONTAMINATED:{reason}" for reason in env_reasons)
    else:
        env_hash = environment.environment_hash

    if not _is_exact_int(warmups):
        failures.append("warmups_missing_or_invalid")
        actual_warmups = 0
    else:
        actual_warmups = warmups
        if actual_warmups < protocol.warmups:
            failures.append("insufficient_warmups")

    if not _is_exact_int(paired_blocks):
        failures.append("paired_blocks_missing_or_invalid")
        actual_pairs = 0
    else:
        actual_pairs = paired_blocks
        if actual_pairs < protocol.paired_blocks:
            failures.append("insufficient_paired_blocks")

    if type(contamination_flags) not in (list, tuple) or any(
        type(flag) is not str or not flag.strip() for flag in contamination_flags
    ):
        failures.append("contamination_flags_invalid")
        norm_contam: tuple[str, ...] = ()
    else:
        norm_contam = tuple(contamination_flags)
        if norm_contam:
            failures.append("CONTAMINATED:external_flags")

    v2_adapter = BenchmarkProtocolV2(
        name=protocol.name,
        metric=protocol.metric,
        direction=protocol.direction,
        alpha=protocol.alpha,
        min_trials=max(30, protocol.min_trials),
        warmups=max(10, protocol.warmups),
        paired_blocks=max(30, protocol.paired_blocks),
        preregistered_seeds=protocol.preregistered_seeds,
        frozen_split_hash=protocol.frozen_split_hash,
        contamination_checks=protocol.contamination_checks,
        percentile=protocol.percentile,
    )
    records: tuple[BenchmarkTrialRecord, ...] = ()
    try:
        records = _normalize_trial_records(v2_adapter, list(trials))
    except (TypeError, ValueError, AttributeError) as exc:
        failures.append(f"raw_trial_records_invalid:{type(exc).__name__}")
    values = tuple(float(r.value) for r in records if r.status == "SUCCESS" and r.value is not None)
    if len(values) < protocol.min_trials:
        failures.append("insufficient_trials")
    if records and any(r.status != "SUCCESS" for r in records):
        failures.append("trial_failures_present")

    distribution: DistributionV3 | None = None
    primary_ci: BcaConfidenceInterval | None = None
    if len(values) >= 2 and not any(r.startswith("protocol_invalid:") for r in failures):
        distribution = DistributionV3.from_samples(
            protocol.metric,
            values,
            alpha=protocol.alpha,
            resamples=protocol.bootstrap_resamples,
            min_resamples=DEFAULT_BOOTSTRAP_RESAMPLES if protocol.ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES,
            seed=protocol.preregistered_seeds[0] if protocol.preregistered_seeds else 0xAE61_5BEE,
        )
        primary_map = {
            None: distribution.mean_ci,
            50: distribution.p50_ci,
            90: distribution.p90_ci,
            95: distribution.p95_ci,
            99: distribution.p99_ci,
        }
        primary_ci = primary_map[protocol.percentile]
        if baseline is not None:
            if not _is_finite_real(baseline):
                failures.append("baseline_invalid")
            elif (protocol.direction == "higher_is_better" and primary_ci.ci_low <= baseline) or (
                protocol.direction == "lower_is_better" and primary_ci.ci_high >= baseline
            ):
                failures.append("confidence_interval_does_not_clear_baseline")

    status = "CONTAMINATED" if contam_verdict == "CONTAMINATED" or norm_contam else ("REJECTED" if failures else "PASS")
    protocol_hash = _digest(asdict(protocol))
    artifact_hash = _digest(
        {
            "schema": "aegis-benchmark-artifact-v3",
            "protocol_hash": protocol_hash,
            "environment_hash": env_hash,
            "status": status,
            "primary_ci": asdict(primary_ci) if primary_ci is not None else None,
            "failures": failures,
        }
    )
    return BenchmarkResultV3(
        protocol_hash=protocol_hash,
        status=status,
        distribution=distribution,
        primary_ci=primary_ci,
        trial_count=len(records),
        valid_trial_count=len(values),
        warmup_count=actual_warmups,
        paired_block_count=actual_pairs,
        environment_hash=env_hash,
        contamination_verdict=contam_verdict,
        contamination_flags=norm_contam + env_reasons,
        failure_reasons=tuple(dict.fromkeys(failures)),
        artifact_hash=artifact_hash,
    )


def evaluate_paired_benchmark_v3(
    protocol: BenchmarkProtocolV3,
    arm_a_samples: Sequence[float],
    arm_b_samples: Sequence[float],
    *,
    warmups: int,
    paired_blocks: int,
    environment: EnvironmentFingerprintV3,
    execution_order: Sequence[str] | None = None,
    enforce_clean_worktree: bool = False,
) -> PairedBenchmarkComparisonV3:
    """Evaluate paired AB/BA benchmark samples with joint BCa CIs, Wilcoxon, Cliff's delta & SESOI."""

    if type(protocol) is not BenchmarkProtocolV3:
        raise TypeError("protocol must be BenchmarkProtocolV3")
    failures: list[str] = []
    try:
        protocol.validate()
    except (TypeError, ValueError, AttributeError) as exc:
        failures.append(f"protocol_invalid:{type(exc).__name__}")

    if type(environment) is not EnvironmentFingerprintV3:
        raise TypeError("environment must be EnvironmentFingerprintV3")
    contam_verdict, env_reasons = environment.evaluate_contamination(enforce_clean_worktree=enforce_clean_worktree)
    env_hash = ""
    if contam_verdict == "CONTAMINATED":
        failures.extend(f"CONTAMINATED:{reason}" for reason in env_reasons)
    else:
        env_hash = environment.environment_hash

    if not _is_exact_int(warmups) or warmups < protocol.warmups:
        failures.append("insufficient_warmups")
    if not _is_exact_int(paired_blocks) or paired_blocks < protocol.paired_blocks:
        failures.append("insufficient_paired_blocks")

    vals_a_buf: list[float] = []
    vals_b_buf: list[float] = []
    try:
        vals_a_buf = list(_validate_numeric_samples(f"{protocol.metric}::arm_a", arm_a_samples, min_len=2))
        vals_b_buf = list(_validate_numeric_samples(f"{protocol.metric}::arm_b", arm_b_samples, min_len=2))
        if len(vals_a_buf) != len(vals_b_buf):
            failures.append("unequal_paired_arms")
        elif len(vals_a_buf) < protocol.min_trials:
            failures.append("insufficient_paired_observations")
    except ValueError as exc:
        failures.append(f"invalid_samples:{exc}")

    vals_a = tuple(vals_a_buf)
    vals_b = tuple(vals_b_buf)
    if execution_order is not None:
        normalized_order = [str(item).upper() for item in execution_order]
        if len(normalized_order) != len(vals_a) or set(normalized_order) != {"AB", "BA"}:
            failures.append("non_counterbalanced_ab_ba_order")

    if failures or not vals_a or not vals_b:
        p_hash = _digest(asdict(protocol))
        return PairedBenchmarkComparisonV3(
            protocol_hash=p_hash,
            metric_name=protocol.metric,
            status="CONTAMINATED" if contam_verdict == "CONTAMINATED" else "REJECTED",
            alpha=protocol.alpha,
            resamples=protocol.bootstrap_resamples,
            paired_blocks=paired_blocks if _is_exact_int(paired_blocks) else 0,
            warmup_count=warmups if _is_exact_int(warmups) else 0,
            arm_a=None,
            arm_b=None,
            wilcoxon=None,
            cliffs_delta=None,
            estimands=(),
            raw_p_value=None,
            adjusted_p_value=None,
            sesoi_cleared=False,
            statistically_significant=False,
            practical_change_proven=False,
            environment_hash=env_hash,
            contamination_flags=env_reasons,
            failure_reasons=tuple(dict.fromkeys(failures)),
            artifact_hash=_digest({"protocol": p_hash, "failures": failures}),
        )

    seed = protocol.preregistered_seeds[0]
    min_boot = DEFAULT_BOOTSTRAP_RESAMPLES if protocol.ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
    resamples = protocol.bootstrap_resamples
    dist_a = DistributionV3.from_samples(
        f"{protocol.metric}::arm_a",
        vals_a,
        alpha=protocol.alpha,
        resamples=resamples,
        min_resamples=min_boot,
        seed=seed ^ 0x11,
    )
    dist_b = DistributionV3.from_samples(
        f"{protocol.metric}::arm_b",
        vals_b,
        alpha=protocol.alpha,
        resamples=resamples,
        min_resamples=min_boot,
        seed=seed ^ 0x22,
    )
    wilcoxon = wilcoxon_signed_rank_test(vals_a, vals_b)
    cliff = cliffs_delta(vals_a, vals_b)

    estimand_names: tuple[EstimandName, ...] = ("mean", "p50", "p90", "p95", "p99")
    a_ests = tuple(_eval_estimand(vals_a, k) for k in estimand_names)
    b_ests = tuple(_eval_estimand(vals_b, k) for k in estimand_names)
    delta_ests = tuple(b_v - a_v for a_v, b_v in zip(a_ests, b_ests, strict=True))
    ratio_ests = tuple((b_v / a_v) if a_v > 0.0 else None for a_v, b_v in zip(a_ests, b_ests, strict=True))

    first_a = vals_a[0]
    first_b = vals_b[0]
    zero_paired_diff = all(v == first_a for v in vals_a) and all(v == first_b for v in vals_b)
    n = len(vals_a)

    boot_deltas: list[list[float]] = [[] for _ in range(5)]
    boot_ratios: list[list[float]] = [[] for _ in range(5)]
    if zero_paired_diff:
        for slot in range(5):
            boot_deltas[slot] = [delta_ests[slot]] * resamples
            r_val = ratio_ests[slot]
            if r_val is not None:
                boot_ratios[slot] = [float(r_val)] * resamples
    else:
        state = seed_from_label(protocol.metric, n, seed ^ 0xA5A5)
        n_float = float(n)
        sorted_fn = sorted
        for _ in range(resamples):
            draw_a: list[float] = []
            draw_b: list[float] = []
            sum_a = 0.0
            sum_b = 0.0
            for _i in range(n):
                state, idx = lemire_bounded_index(state, n)
                va = vals_a[idx]
                vb = vals_b[idx]
                draw_a.append(va)
                draw_b.append(vb)
                sum_a += va
                sum_b += vb
            ma = sum_a / n_float
            mb = sum_b / n_float
            boot_deltas[0].append(mb - ma)
            if ratio_ests[0] is not None:
                boot_ratios[0].append((mb / ma) if ma > 0.0 else 1.0)
            sa = sorted_fn(draw_a)
            sb = sorted_fn(draw_b)
            for slot, q in ((1, 0.50), (2, 0.90), (3, 0.95), (4, 0.99)):
                qa = quantile_type7_sorted(sa, q)
                qb = quantile_type7_sorted(sb, q)
                boot_deltas[slot].append(qb - qa)
                if ratio_ests[slot] is not None:
                    boot_ratios[slot].append((qb / qa) if qa > 0.0 else 1.0)

    k_blocks = n if n < 200 else JACKKNIFE_BLOCK_COUNT
    jack_deltas: list[list[float]] = [[] for _ in range(5)]
    jack_ratios: list[list[float]] = [[] for _ in range(5)]
    if not zero_paired_diff:
        for blk in range(k_blocks):
            start = (blk * n) // k_blocks
            end = ((blk + 1) * n) // k_blocks
            fold_a = vals_a[:start] + vals_a[end:]
            fold_b = vals_b[:start] + vals_b[end:]
            if not fold_a:
                continue
            ma = statistics.fmean(fold_a)
            mb = statistics.fmean(fold_b)
            jack_deltas[0].append(mb - ma)
            if ratio_ests[0] is not None:
                jack_ratios[0].append((mb / ma) if ma > 0.0 else 1.0)
            s_fa = sorted(fold_a)
            s_fb = sorted(fold_b)
            for slot, q in ((1, 0.50), (2, 0.90), (3, 0.95), (4, 0.99)):
                qa = quantile_type7_sorted(s_fa, q)
                qb = quantile_type7_sorted(s_fb, q)
                jack_deltas[slot].append(qb - qa)
                if ratio_ests[slot] is not None:
                    jack_ratios[slot].append((qb / qa) if qa > 0.0 else 1.0)

    paired_estimands: list[PairedEstimandComparisonV3] = []
    for slot, est_name in enumerate(estimand_names):
        delta_ci = _finalize_bca_interval(
            f"delta_{est_name}",
            delta_ests[slot],
            boot_deltas[slot],
            jack_deltas[slot],
            protocol.alpha,
            zero_paired_diff,
        )
        r_est = ratio_ests[slot]
        ratio_ci = (
            _finalize_bca_interval(
                f"ratio_{est_name}",
                r_est,
                boot_ratios[slot],
                jack_ratios[slot],
                protocol.alpha,
                zero_paired_diff,
            )
            if r_est is not None
            else None
        )
        paired_estimands.append(
            PairedEstimandComparisonV3(
                estimand=est_name,
                a_estimate=a_ests[slot],
                b_estimate=b_ests[slot],
                delta_estimate=delta_ests[slot],
                delta_ci=delta_ci,
                ratio_estimate=r_est,
                ratio_ci=ratio_ci,
            )
        )

    p50_rel = abs(delta_ests[1] / a_ests[1]) if abs(a_ests[1]) > 1e-12 else 0.0
    p95_rel = abs(delta_ests[3] / a_ests[3]) if abs(a_ests[3]) > 1e-12 else 0.0
    sesoi_cleared = (
        cliff.abs_delta >= protocol.sesoi.min_abs_cliffs_delta
        or max(p50_rel, p95_rel) >= protocol.sesoi.min_relative_delta
    )
    raw_p = wilcoxon.p_value
    adj_p = raw_p
    ci_excludes_zero = (
        paired_estimands[1].delta_ci.ci_low > 0.0
        or paired_estimands[1].delta_ci.ci_high < 0.0
        or paired_estimands[0].delta_ci.ci_low > 0.0
        or paired_estimands[0].delta_ci.ci_high < 0.0
    )
    statistically_significant = adj_p <= protocol.alpha and ci_excludes_zero
    practical_proven = statistically_significant and sesoi_cleared
    p_hash = protocol.protocol_hash
    return PairedBenchmarkComparisonV3(
        protocol_hash=p_hash,
        metric_name=protocol.metric,
        status="PASS",
        alpha=protocol.alpha,
        resamples=resamples,
        paired_blocks=paired_blocks,
        warmup_count=warmups,
        arm_a=dist_a,
        arm_b=dist_b,
        wilcoxon=wilcoxon,
        cliffs_delta=cliff,
        estimands=tuple(paired_estimands),
        raw_p_value=raw_p,
        adjusted_p_value=adj_p,
        sesoi_cleared=sesoi_cleared,
        statistically_significant=statistically_significant,
        practical_change_proven=practical_proven,
        environment_hash=env_hash,
        contamination_flags=env_reasons,
        failure_reasons=(),
        artifact_hash=_digest({"schema": "aegis-paired-v3", "protocol": p_hash, "env": env_hash, "p": raw_p}),
    )


def apply_holm_bonferroni_v3(
    comparisons: Sequence[PairedBenchmarkComparisonV3],
) -> tuple[PairedBenchmarkComparisonV3, ...]:
    """Adjust raw p-values across a family of V3 paired comparisons using Holm-Bonferroni."""

    valid_indices = [idx for idx, comp in enumerate(comparisons) if comp.raw_p_value is not None]
    raw_ps = [float(cast(float, comparisons[idx].raw_p_value)) for idx in valid_indices]
    adjusted = holm_bonferroni(raw_ps)
    adj_map = {orig_idx: adj_p for orig_idx, adj_p in zip(valid_indices, adjusted, strict=True)}
    out: list[PairedBenchmarkComparisonV3] = []
    for idx, comp in enumerate(comparisons):
        if idx not in adj_map or len(comp.estimands) < 2:
            out.append(comp)
            continue
        adj_p = adj_map[idx]
        ci_excludes_zero = (
            comp.estimands[1].delta_ci.ci_low > 0.0
            or comp.estimands[1].delta_ci.ci_high < 0.0
            or comp.estimands[0].delta_ci.ci_low > 0.0
            or comp.estimands[0].delta_ci.ci_high < 0.0
        )
        stat_sig = adj_p <= comp.alpha and ci_excludes_zero
        out.append(
            PairedBenchmarkComparisonV3(
                protocol_hash=comp.protocol_hash,
                metric_name=comp.metric_name,
                status=comp.status,
                alpha=comp.alpha,
                resamples=comp.resamples,
                paired_blocks=comp.paired_blocks,
                warmup_count=comp.warmup_count,
                arm_a=comp.arm_a,
                arm_b=comp.arm_b,
                wilcoxon=comp.wilcoxon,
                cliffs_delta=comp.cliffs_delta,
                estimands=comp.estimands,
                raw_p_value=comp.raw_p_value,
                adjusted_p_value=adj_p,
                sesoi_cleared=comp.sesoi_cleared,
                statistically_significant=stat_sig,
                practical_change_proven=stat_sig and comp.sesoi_cleared,
                environment_hash=comp.environment_hash,
                contamination_flags=comp.contamination_flags,
                failure_reasons=comp.failure_reasons,
                artifact_hash=comp.artifact_hash,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Open-Loop Tail Latency Harness (Zero Coordinated Omission)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyPercentiles:
    count: int
    mean_ns: float
    p50_ns: float
    p90_ns: float
    p95_ns: float
    p99_ns: float
    p99_9_ns: float
    max_ns: float


class HdrHistogramLog:
    """Logarithmic HdrHistogram spanning ``1 ns .. 60 s`` at 3 decimal significant digits."""

    def __init__(self) -> None:
        self._counts: list[int] = [0] * _HDR_TOTAL_SLOTS
        self.total_count: int = 0
        self._sum_ns: int = 0
        self._min_ns: int = 1 << 63
        self._max_ns: int = 0

    @staticmethod
    def _slot_index(value_ns: int) -> int:
        clamped = 1 if value_ns < 1 else (HDR_MAX_VALUE_NS if value_ns > HDR_MAX_VALUE_NS else value_ns)
        if clamped < _HDR_SUB_BUCKET_COUNT:
            return clamped
        msb = clamped.bit_length() - 1
        bucket = msb - 10
        if bucket >= _HDR_BUCKET_COUNT:
            return _HDR_TOTAL_SLOTS - 1
        sub = clamped >> bucket
        return _HDR_SUB_BUCKET_COUNT + (bucket - 1) * _HDR_SUB_BUCKET_HALF_COUNT + (sub - _HDR_SUB_BUCKET_HALF_COUNT)

    @staticmethod
    def _slot_value_ns(idx: int) -> float:
        if idx < _HDR_SUB_BUCKET_COUNT:
            return float(idx)
        rem = idx - _HDR_SUB_BUCKET_COUNT
        bucket = 1 + rem // _HDR_SUB_BUCKET_HALF_COUNT
        sub = _HDR_SUB_BUCKET_HALF_COUNT + (rem % _HDR_SUB_BUCKET_HALF_COUNT)
        low = sub << bucket
        mid = low + (1 << (bucket - 1))
        return float(mid)

    def record_ns(self, value_ns: int) -> None:
        clamped = min(HDR_MAX_VALUE_NS, max(0, int(value_ns)))
        idx = self._slot_index(max(1, clamped))
        self._counts[idx] += 1
        self.total_count += 1
        self._sum_ns += clamped
        if clamped < self._min_ns:
            self._min_ns = clamped
        if clamped > self._max_ns:
            self._max_ns = clamped

    def quantile(self, q: float) -> float:
        if self.total_count == 0:
            return 0.0
        clamped_q = max(0.0, min(1.0, q))
        if clamped_q == 0.0:
            return float(self._min_ns)
        if clamped_q >= 1.0:
            return float(self._max_ns)
        target_rank = max(1, math.ceil(clamped_q * self.total_count))
        cumulative = 0
        for idx, cnt in enumerate(self._counts):
            if cnt == 0:
                continue
            cumulative += cnt
            if cumulative >= target_rank:
                return max(float(self._min_ns), min(float(self._max_ns), self._slot_value_ns(idx)))
        return float(self._max_ns)

    def summary(self) -> LatencyPercentiles:
        mean_ns = self._sum_ns / float(self.total_count) if self.total_count > 0 else 0.0
        return LatencyPercentiles(
            count=self.total_count,
            mean_ns=mean_ns,
            p50_ns=self.quantile(0.50),
            p90_ns=self.quantile(0.90),
            p95_ns=self.quantile(0.95),
            p99_ns=self.quantile(0.99),
            p99_9_ns=self.quantile(0.999),
            max_ns=float(self._max_ns),
        )


@dataclass(frozen=True)
class OpenLoopConfig:
    utilization_levels: tuple[float, ...] = (0.25, 0.50, 0.75, 0.90, 0.95)
    requests_per_point: int = 10_000
    epochs: int = 30
    queue_capacity: int = 4096
    request_timeout_ns: int = 5_000_000_000
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES
    min_resamples: int = MIN_DEV_BOOTSTRAP_RESAMPLES
    alpha: float = 0.05
    seed: int = 0xAE61_500E_0001


@dataclass(frozen=True)
class OpenLoopUtilizationPoint:
    utilization_rho: float
    target_rate_ops_per_sec: float
    offered: int
    admitted: int
    queued: int
    completed: int
    rejected_queue_full: int
    timed_out: int
    service_latency: LatencyPercentiles
    sojourn_latency: LatencyPercentiles
    service_p99_epoch_ci: BcaConfidenceInterval | None
    sojourn_p99_epoch_ci: BcaConfidenceInterval | None


@dataclass(frozen=True)
class OpenLoopReport:
    label: str
    mu_max_ops_per_sec: float
    points: tuple[OpenLoopUtilizationPoint, ...]
    coordinated_omission_ratio_p99: float


def generate_poisson_arrivals_ns(count: int, rate_ops_per_sec: float, *, seed: int = 0xAE61_500E) -> tuple[int, ...]:
    """Generate deterministic Poisson scheduled arrival timestamps in nanoseconds."""

    if not _is_exact_int(count) or count <= 0:
        raise ValueError("arrival count must be positive integer")
    if not _is_finite_real(rate_ops_per_sec) or rate_ops_per_sec <= 0.0:
        raise ValueError("rate_ops_per_sec must be finite and positive")
    state = seed_from_label("poisson_arrivals", count, seed)
    scale_ns = 1_000_000_000.0 / rate_ops_per_sec
    t_ns = 0.0
    out: list[int] = []
    for _ in range(count):
        state, u = uniform_open0_closed1(state)
        t_ns += -math.log(u) * scale_ns
        out.append(round(t_ns))
    return tuple(out)


def simulate_open_loop_queue(
    service_times_ns: Sequence[int],
    rate_ops_per_sec: float,
    *,
    workers: int = 1,
    queue_capacity: int = 4096,
    timeout_ns: int = 5_000_000_000,
    seed: int = 0xAE61_500E,
) -> tuple[HdrHistogramLog, HdrHistogramLog, int, int, int, int, int]:
    """Model a bounded FIFO queue under Poisson arrivals, tracking service and sojourn times."""

    if not service_times_ns:
        raise ValueError("service_times_ns must not be empty")
    if workers < 1 or queue_capacity < 1:
        raise ValueError("workers and queue_capacity must be >= 1")
    arrivals = generate_poisson_arrivals_ns(len(service_times_ns), rate_ops_per_sec, seed=seed)
    worker_free = [0] * workers
    in_flight: list[int] = []
    service_hist = HdrHistogramLog()
    sojourn_hist = HdrHistogramLog()
    admitted = 0
    queued = 0
    completed = 0
    rejected_queue_full = 0
    timed_out = 0

    for sched_ns, raw_svc_ns in zip(arrivals, service_times_ns, strict=True):
        svc_ns = max(1, int(raw_svc_ns))
        in_flight = [dep for dep in in_flight if dep > sched_ns]
        if max(0, len(in_flight) - workers) >= queue_capacity:
            rejected_queue_full += 1
            continue
        admitted += 1
        worker_idx = min(range(workers), key=lambda idx: worker_free[idx])
        start_ns = max(sched_ns, worker_free[worker_idx])
        wait_ns = start_ns - sched_ns
        if wait_ns > 0:
            queued += 1
        if wait_ns + svc_ns > timeout_ns:
            timed_out += 1
            continue
        end_ns = start_ns + svc_ns
        worker_free[worker_idx] = end_ns
        in_flight.append(end_ns)
        completed += 1
        service_hist.record_ns(svc_ns)
        sojourn_hist.record_ns(end_ns - sched_ns)

    return service_hist, sojourn_hist, admitted, queued, completed, rejected_queue_full, timed_out


class OpenLoopHarness:
    """Open-loop tail-latency harness avoiding coordinated omission."""

    @staticmethod
    def calibrate_capacity(probe_requests: int, op: Callable[[], Any]) -> float:
        if probe_requests <= 0:
            raise ValueError("probe_requests must be > 0")
        warm = min(256, max(16, probe_requests))
        for _ in range(warm):
            op()
        start = time.perf_counter_ns()
        for _ in range(probe_requests):
            op()
        elapsed_secs = max(1e-9, (time.perf_counter_ns() - start) / 1_000_000_000.0)
        return float(probe_requests) / elapsed_secs

    @classmethod
    def sweep_callable(
        cls,
        label: str,
        op: Callable[[], Any],
        *,
        config: OpenLoopConfig | None = None,
    ) -> OpenLoopReport:
        cfg = config or OpenLoopConfig()
        mu_max = cls.calibrate_capacity(min(512, max(64, cfg.requests_per_point)), op)
        points: list[OpenLoopUtilizationPoint] = []
        max_co_ratio = 1.0
        per_epoch = max(1, cfg.requests_per_point // max(1, cfg.epochs))

        for level_idx, rho in enumerate(cfg.utilization_levels):
            target_rate = max(1.0, mu_max * rho)
            arrivals = generate_poisson_arrivals_ns(
                cfg.requests_per_point,
                target_rate,
                seed=cfg.seed ^ ((level_idx + 1) * 0x9E37_79B9),
            )
            svc_hist = HdrHistogramLog()
            soj_hist = HdrHistogramLog()
            epoch_svc_hists = [HdrHistogramLog() for _ in range(cfg.epochs)]
            epoch_soj_hists = [HdrHistogramLog() for _ in range(cfg.epochs)]
            next_free_ns = 0
            admitted = 0
            queued = 0
            completed = 0
            rejected_queue_full = 0
            timed_out = 0
            in_flight: list[int] = []

            for req_idx, sched_ns in enumerate(arrivals):
                in_flight = [dep for dep in in_flight if dep > sched_ns]
                if max(0, len(in_flight) - 1) >= cfg.queue_capacity:
                    rejected_queue_full += 1
                    continue
                admitted += 1
                start_offset = max(sched_ns, next_free_ns)
                wait_ns = start_offset - sched_ns
                if wait_ns > 0:
                    queued += 1
                t0 = time.perf_counter_ns()
                op()
                svc_ns = max(1, time.perf_counter_ns() - t0)
                soj_ns = wait_ns + svc_ns
                if soj_ns > cfg.request_timeout_ns:
                    timed_out += 1
                    continue
                end_ns = start_offset + svc_ns
                next_free_ns = end_ns
                in_flight.append(end_ns)
                completed += 1
                svc_hist.record_ns(svc_ns)
                soj_hist.record_ns(soj_ns)
                ep = min(cfg.epochs - 1, req_idx // per_epoch)
                epoch_svc_hists[ep].record_ns(svc_ns)
                epoch_soj_hists[ep].record_ns(soj_ns)

            svc_summary = svc_hist.summary()
            soj_summary = soj_hist.summary()
            epoch_svc_p99 = [h.quantile(0.99) for h in epoch_svc_hists if h.total_count > 0]
            epoch_soj_p99 = [h.quantile(0.99) for h in epoch_soj_hists if h.total_count > 0]
            svc_ci = (
                bca_confidence_interval(
                    f"{label}::rho_{rho:.2f}::svc_p99",
                    epoch_svc_p99,
                    estimand="mean",
                    alpha=cfg.alpha,
                    resamples=cfg.bootstrap_resamples,
                    min_resamples=cfg.min_resamples,
                    seed=cfg.seed,
                )
                if len(epoch_svc_p99) >= 2
                else None
            )
            soj_ci = (
                bca_confidence_interval(
                    f"{label}::rho_{rho:.2f}::soj_p99",
                    epoch_soj_p99,
                    estimand="mean",
                    alpha=cfg.alpha,
                    resamples=cfg.bootstrap_resamples,
                    min_resamples=cfg.min_resamples,
                    seed=cfg.seed ^ 0x77,
                )
                if len(epoch_soj_p99) >= 2
                else None
            )
            if svc_summary.p99_ns > 0.0:
                max_co_ratio = max(max_co_ratio, soj_summary.p99_ns / svc_summary.p99_ns)
            points.append(
                OpenLoopUtilizationPoint(
                    utilization_rho=rho,
                    target_rate_ops_per_sec=target_rate,
                    offered=cfg.requests_per_point,
                    admitted=admitted,
                    queued=queued,
                    completed=completed,
                    rejected_queue_full=rejected_queue_full,
                    timed_out=timed_out,
                    service_latency=svc_summary,
                    sojourn_latency=soj_summary,
                    service_p99_epoch_ci=svc_ci,
                    sojourn_p99_epoch_ci=soj_ci,
                )
            )

        return OpenLoopReport(
            label=label,
            mu_max_ops_per_sec=mu_max,
            points=tuple(points),
            coordinated_omission_ratio_p99=max_co_ratio,
        )


# ---------------------------------------------------------------------------
# Canary +25% Regression Sensitivity Self-Test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SensitivitySelfTestReport:
    passed: bool
    injected_slowdown_factor: float
    observed_ratio: float
    adjusted_p_value: float
    cliffs_delta: float
    mean_delta_ci_low: float
    quarantine_status: str


def run_canary_sensitivity_self_test(
    *,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    ci_enforced: bool = False,
) -> SensitivitySelfTestReport:
    """Inject a +25% regression and prove the V3 engine flags it with Holm p_adj<=0.05 & |delta|>=0.33."""

    n = 60
    state = 0xAE61_5000_CA9A_0001
    arm_a: list[float] = []
    arm_b: list[float] = []
    for _ in range(n):
        state, u1 = uniform_open0_closed1(state)
        state, u2 = uniform_open0_closed1(state)
        base = 1000.0 + (-math.log(u1)) * 120.0
        noise_b = 1.0 + (u2 - 0.5) * 0.06
        arm_a.append(base)
        arm_b.append(base * 1.25 * noise_b)

    seeds = tuple(100 + i for i in range(5))
    proto = BenchmarkProtocolV3(
        name="canary_sensitivity_self_test",
        metric="latency_ns",
        direction="lower_is_better",
        alpha=0.05,
        min_trials=n,
        warmups=10,
        paired_blocks=n,
        bootstrap_resamples=resamples,
        ci_enforced=ci_enforced,
        preregistered_seeds=seeds,
        frozen_split_hash="canary-split-v3",
        contamination_checks=("load_avg", "thermal", "leak"),
    )
    env = EnvironmentFingerprintV3.capture(
        network_policy="offline",
        rustc_version="self-test",
        pre_run_load_avg=0.05,
        post_run_load_avg=0.05,
    )
    comp = evaluate_paired_benchmark_v3(
        proto,
        arm_a,
        arm_b,
        warmups=10,
        paired_blocks=n,
        environment=env,
        execution_order=["AB" if i % 2 == 0 else "BA" for i in range(n)],
    )
    adjusted = apply_holm_bonferroni_v3([comp])[0]
    assert adjusted.adjusted_p_value is not None
    assert adjusted.cliffs_delta is not None
    mean_est = adjusted.estimands[0]
    passed = (
        adjusted.status == "PASS"
        and adjusted.adjusted_p_value <= 0.05
        and adjusted.cliffs_delta.abs_delta >= 0.33
        and adjusted.practical_change_proven
        and mean_est.delta_ci.ci_low > 0.0
    )
    return SensitivitySelfTestReport(
        passed=passed,
        injected_slowdown_factor=1.25,
        observed_ratio=mean_est.ratio_estimate or 0.0,
        adjusted_p_value=adjusted.adjusted_p_value,
        cliffs_delta=adjusted.cliffs_delta.delta,
        mean_delta_ci_low=mean_est.delta_ci.ci_low,
        quarantine_status="SENSITIVE_OK" if passed else "INSENSITIVE_TOY",
    )


# ---------------------------------------------------------------------------
# Preserved Legacy V2 Primitives (Upgraded CI Engine honoring alpha & quantiles)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkProtocolV2:
    name: str
    metric: str
    direction: str = "higher_is_better"
    alpha: float = 0.05
    min_trials: int = 30
    warmups: int = 10
    paired_blocks: int = 30
    preregistered_seeds: tuple[int, ...] = ()
    frozen_split_hash: str = ""
    contamination_checks: tuple[str, ...] = ()
    percentile: int | None = None

    def validate(self) -> None:
        if (
            type(self.name) is not str
            or type(self.metric) is not str
            or type(self.direction) is not str
            or type(self.frozen_split_hash) is not str
            or not self.name.strip()
            or not self.metric.strip()
            or not self.frozen_split_hash.strip()
        ):
            raise ValueError("benchmark name and metric are required")
        if self.direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("benchmark direction must be explicit")
        if not _is_finite_real(self.alpha) or not 0 < self.alpha < 0.5:
            raise ValueError("alpha must be in (0, 0.5)")
        if (
            not _is_exact_int(self.min_trials)
            or not _is_exact_int(self.warmups)
            or not _is_exact_int(self.paired_blocks)
            or self.min_trials < 30
            or self.warmups < 10
            or self.paired_blocks < 30
        ):
            raise ValueError("strict benchmark requires >=30 trials, >=10 warmups and >=30 paired blocks")
        if type(self.preregistered_seeds) not in (list, tuple):
            raise ValueError("preregistered benchmark seeds must be an integer sequence")
        if len(self.preregistered_seeds) < 5:
            raise ValueError("stochastic benchmark requires at least five preregistered seeds")
        if any(not _is_exact_int(seed) for seed in self.preregistered_seeds):
            raise ValueError("preregistered benchmark seeds must be integers")
        if len(set(self.preregistered_seeds)) != len(self.preregistered_seeds):
            raise ValueError("preregistered benchmark seeds must be unique")
        if type(self.contamination_checks) not in (list, tuple):
            raise ValueError("contamination checks must be a string sequence")
        if not self.contamination_checks or any(
            type(item) is not str or not item.strip() for item in self.contamination_checks
        ):
            raise ValueError("contamination checks must be declared")
        if self.percentile is not None and (
            not _is_exact_int(self.percentile) or self.percentile not in {50, 90, 95, 99}
        ):
            raise ValueError("only declared percentile metrics are supported")
        if self.percentile == 99 and self.min_trials < 1000:
            raise ValueError("p99 requires at least 1000 valid observations")

    @property
    def protocol_hash(self) -> str:
        self.validate()
        return _digest(self)


@dataclass(frozen=True)
class EnvironmentFingerprint:
    """Stable, explicit environment binding for benchmark evidence."""

    os_name: str
    os_release: str
    architecture: str
    python_version: str
    cpu_model: str
    logical_cpus: int
    container_image: str
    gpu_driver: str
    locale_name: str
    network_policy: str

    @classmethod
    def capture(
        cls,
        *,
        network_policy: str,
        container_image: str | None = None,
        gpu_driver: str | None = None,
    ) -> EnvironmentFingerprint:
        """Capture deterministic host metadata; no wall-clock value is included."""

        detected_locale = locale.getpreferredencoding(False) or "unknown"
        return cls(
            os_name=platform.system() or "unknown",
            os_release=platform.release() or "unknown",
            architecture=platform.machine() or "unknown",
            python_version=platform.python_version(),
            cpu_model=platform.processor() or "unknown",
            logical_cpus=os.cpu_count() or 1,
            container_image=container_image or os.environ.get("AEGIS_CONTAINER_IMAGE", "unknown"),
            gpu_driver=gpu_driver or os.environ.get("AEGIS_GPU_DRIVER", "unknown"),
            locale_name=detected_locale,
            network_policy=network_policy,
        )

    def validate(self) -> None:
        fields = (
            self.os_name,
            self.os_release,
            self.architecture,
            self.python_version,
            self.cpu_model,
            self.container_image,
            self.gpu_driver,
            self.locale_name,
            self.network_policy,
        )
        if (
            any(type(field) is not str or not field.strip() for field in fields)
            or not _is_exact_int(self.logical_cpus)
            or self.logical_cpus < 1
        ):
            raise ValueError("benchmark environment fingerprint is incomplete")

    @property
    def environment_hash(self) -> str:
        self.validate()
        return _digest({"schema": "aegis-environment-fingerprint-v1", **asdict(self)})


@dataclass(frozen=True)
class BenchmarkTrialRecord:
    """Immutable raw record for one item/trial/seed attempt."""

    item_id: str
    trial_index: int
    seed: int
    status: str = "SUCCESS"
    value: float | None = None
    error_class: str = ""
    artifact_hash: str = ""

    def validate(self) -> None:
        if (
            type(self.item_id) is not str
            or type(self.status) is not str
            or type(self.error_class) is not str
            or type(self.artifact_hash) is not str
            or not self.item_id.strip()
            or type(self.trial_index) is not int
            or self.trial_index < 0
            or type(self.seed) is not int
            or self.status not in _TRIAL_STATUSES
        ):
            raise ValueError("benchmark trial identity or status is invalid")
        if self.status == "SUCCESS":
            if self.value is None or not _is_finite_real(self.value):
                raise ValueError("successful benchmark trial requires a finite value")
        elif self.value is not None:
            if not _is_finite_real(self.value):
                raise ValueError("failed benchmark trial cannot carry a non-numeric value")
            raise ValueError("failed benchmark trial cannot carry a metric value")
        if self.status != "SUCCESS" and not self.error_class.strip():
            raise ValueError("failed benchmark trial requires an error class")

    @property
    def key(self) -> tuple[str, int, int]:
        return self.item_id, self.trial_index, self.seed


@dataclass(frozen=True)
class BenchmarkResultV2:
    protocol_hash: str
    status: str
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    trial_count: int
    warmup_count: int
    paired_block_count: int
    raw_trial_hash: str
    contamination_flags: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    truth_claim: bool = False
    raw_trials: tuple[float, ...] = ()
    environment_hash: str = ""
    validator_hash: str = ""
    artifact_hash: str = ""
    valid_trial_count: int = 0
    raw_records: tuple[BenchmarkTrialRecord, ...] = ()
    validator_version: str = ""


def _run_isolated_validator(
    command: Sequence[str] | str | bytes,
    *,
    values: tuple[float, ...],
    protocol_hash: str,
    raw_trial_hash: str,
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[bool, str, str]:
    """Run a harness-owned validator outside the candidate process."""

    if type(command) not in (list, tuple):
        raise ValueError("hidden validator command must be a non-empty argv sequence")
    raw_command = tuple(cast(Sequence[Any], command))
    if not raw_command or any(type(part) is not str or not part.strip() for part in raw_command):
        raise ValueError("hidden validator command must be a non-empty argv sequence")
    argv = tuple(cast(str, part) for part in raw_command)
    if not _is_finite_real(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("hidden validator timeout must be finite and positive")
    raw_output_limit: Any = max_output_bytes
    if not _is_exact_int(raw_output_limit) or not 1 <= raw_output_limit <= 1_048_576:
        raise ValueError("hidden validator output limit is invalid")

    input_hash = _digest(values)
    payload = json.dumps(
        {
            "schema": "aegis-hidden-validator-input-v1",
            "protocol_hash": protocol_hash,
            "raw_trial_hash": raw_trial_hash,
            "values": values,
            "input_hash": input_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        stdout, stderr = process.communicate(payload, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        assert process is not None
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=1.0)
        raise TimeoutError("hidden validator timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"hidden validator execution failed: {type(exc).__name__}") from exc
    assert process is not None
    if len(stdout.encode("utf-8")) > max_output_bytes or len(stderr.encode("utf-8")) > max_output_bytes:
        raise RuntimeError("hidden validator output exceeded limit")
    if process.returncode != 0:
        raise RuntimeError(f"hidden validator exited with status {process.returncode}")
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("hidden validator returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("hidden validator result must be an object")
    result_mapping = cast(dict[str, Any], result)
    if result_mapping.get("schema") != "aegis-hidden-validator-result-v1":
        raise ValueError("hidden validator result schema is invalid")
    if not isinstance(result_mapping.get("valid"), bool):
        raise ValueError("hidden validator result verdict must be boolean")
    if result_mapping.get("input_hash") != input_hash:
        raise ValueError("hidden validator input hash mismatch")
    validator_version = result_mapping.get("validator_version")
    if not isinstance(validator_version, str) or not validator_version.strip():
        raise ValueError("hidden validator version is required")
    return (
        cast(bool, result_mapping["valid"]),
        validator_version,
        _digest(
            {
                "kind": "isolated-process",
                "command": list(argv),
                "validator_version": validator_version,
                "input_hash": input_hash,
            }
        ),
    )


def _normalize_trial_records(
    protocol: BenchmarkProtocolV2,
    trials: list[Any] | tuple[Any, ...],
) -> tuple[BenchmarkTrialRecord, ...]:
    """Normalize scalar compatibility inputs without dropping failed attempts."""

    if type(protocol) is not BenchmarkProtocolV2:
        raise TypeError("benchmark protocol must be BenchmarkProtocolV2")
    if type(trials) not in (list, tuple):
        raise TypeError("benchmark trials must be a list or tuple")
    if type(protocol.preregistered_seeds) not in (list, tuple):
        raise ValueError("benchmark protocol seeds must be a sequence")
    records: list[BenchmarkTrialRecord] = []
    seeds = protocol.preregistered_seeds or (0,)
    for index, raw in enumerate(trials):
        if isinstance(raw, BenchmarkTrialRecord):
            record = raw
        elif type(raw) is dict:
            mapping = cast(dict[str, Any], raw)
            raw_status = mapping.get("status", "SUCCESS")
            if type(raw_status) is not str or raw_status not in _TRIAL_STATUSES:
                raise ValueError("benchmark trial status must be an uppercase known status")
            status = raw_status
            raw_value = mapping.get("value", mapping.get("metric"))
            value: float | None
            error_class = mapping.get("error_class", "")
            if type(error_class) is not str:
                raise ValueError("benchmark trial error class must be a string")
            item_id = mapping.get("item_id", f"item-{index}")
            trial_index = mapping.get("trial_index", index)
            seed = mapping.get("seed", seeds[index % len(seeds)])
            artifact_hash = mapping.get("artifact_hash", "")
            if (
                type(item_id) is not str
                or type(trial_index) is not int
                or type(seed) is not int
                or type(artifact_hash) is not str
            ):
                raise ValueError("benchmark trial identity and artifact metadata types are invalid")
            if status == "SUCCESS":
                if _is_finite_real(raw_value):
                    value = raw_value
                else:
                    value = None
                    status = "ERROR"
                    error_class = error_class or "non_numeric_value"
            else:
                value = raw_value
            record = BenchmarkTrialRecord(
                item_id=item_id,
                trial_index=trial_index,
                seed=seed,
                status=status,
                value=value,
                error_class=error_class,
                artifact_hash=artifact_hash,
            )
        else:
            if _is_finite_real(raw):
                value = raw
                status = "SUCCESS"
                error_class = ""
            else:
                value = None
                status = "ERROR"
                error_class = "non_numeric_value"
            record = BenchmarkTrialRecord(
                item_id=f"item-{index}",
                trial_index=index,
                seed=seeds[index % len(seeds)],
                status=status,
                value=value,
                error_class=error_class,
            )
        record.validate()
        if record.seed not in protocol.preregistered_seeds:
            raise ValueError("benchmark trial seed is not preregistered")
        records.append(record)
    keys = [record.key for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError("benchmark trial identity is duplicated")
    return tuple(records)


def evaluate_benchmark(
    protocol: BenchmarkProtocolV2,
    trials: list[Any] | tuple[Any, ...],
    *,
    baseline: float | None = None,
    contamination_flags: tuple[str, ...] = (),
    paired_blocks: int | None = None,
    warmups: int | None = None,
    environment_hash: str = "",
    environment: EnvironmentFingerprint | None = None,
    validator: Any | None = None,
    validator_command: Sequence[str] | None = None,
    validator_timeout_seconds: float = 5.0,
    validator_max_output_bytes: int = 65_536,
) -> BenchmarkResultV2:
    """Evaluate raw trials using exact BCa bootstrap honoring ``protocol.alpha`` and declared percentiles."""

    failures: list[str] = []
    if type(protocol) is not BenchmarkProtocolV2:
        raise TypeError("benchmark protocol must be BenchmarkProtocolV2")
    try:
        protocol.validate()
    except (TypeError, ValueError, AttributeError) as exc:
        failures.append(f"protocol_invalid:{type(exc).__name__}")
    min_trials = protocol.min_trials if _is_exact_int(protocol.min_trials) else 30
    required_warmups = protocol.warmups if _is_exact_int(protocol.warmups) else 10
    required_pairs = protocol.paired_blocks if _is_exact_int(protocol.paired_blocks) else 30
    direction = protocol.direction if type(protocol.direction) is str else "higher_is_better"
    valid_alpha = protocol.alpha if _is_finite_real(protocol.alpha) and 0.0 < protocol.alpha < 0.5 else 0.05

    normalized_environment_hash = environment_hash if type(environment_hash) is str else ""
    if type(environment_hash) is not str:
        failures.append("environment_hash_invalid")

    if environment is not None:
        if type(environment) is not EnvironmentFingerprint:
            failures.append("environment_invalid")
        else:
            try:
                captured_hash = environment.environment_hash
                if normalized_environment_hash and normalized_environment_hash != captured_hash:
                    failures.append("environment_hash_mismatch")
                normalized_environment_hash = captured_hash
            except (TypeError, ValueError, AttributeError) as exc:
                failures.append(f"environment_invalid:{type(exc).__name__}")

    records: tuple[BenchmarkTrialRecord, ...]
    try:
        records = _normalize_trial_records(protocol, trials)
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        records = ()
        failures.append(f"raw_trial_records_invalid:{type(exc).__name__}")
    values = tuple(float(record.value) for record in records if record.status == "SUCCESS" and record.value is not None)
    if not values:
        failures.append("raw_trials_empty_or_non_finite")
    if len(values) < min_trials:
        failures.append("insufficient_trials")
    if records and any(record.status != "SUCCESS" for record in records):
        failures.append("trial_failures_present")
    if warmups is not None and not _is_exact_int(warmups):
        failures.append("warmups_invalid")
    if paired_blocks is not None and not _is_exact_int(paired_blocks):
        failures.append("paired_blocks_invalid")
    actual_warmups = required_warmups if warmups is None or not _is_exact_int(warmups) else warmups
    actual_pairs = required_pairs if paired_blocks is None or not _is_exact_int(paired_blocks) else paired_blocks
    if actual_warmups < required_warmups:
        failures.append("insufficient_warmups")
    if actual_pairs < required_pairs:
        failures.append("insufficient_paired_blocks")
    if type(contamination_flags) not in (list, tuple) or any(
        type(flag) is not str or not flag.strip() for flag in contamination_flags
    ):
        failures.append("contamination_flags_invalid")
        normalized_contamination_flags: tuple[str, ...] = ()
    else:
        normalized_contamination_flags = tuple(contamination_flags)
    if normalized_contamination_flags:
        failures.append("contamination_detected")
    if baseline is not None and not _is_finite_real(baseline):
        failures.append("baseline_invalid")
        normalized_baseline: float | None = None
    else:
        normalized_baseline = baseline
    if not normalized_environment_hash.strip():
        failures.append("environment_hash_missing")

    validator_hash = ""
    validator_version = ""
    if validator is not None and validator_command is not None:
        failures.append("multiple_validator_modes")
    if validator is not None:
        if not callable(validator):
            failures.append("hidden_validator_not_callable")
        else:
            validator_hash = _digest({"validator": getattr(validator, "__qualname__", repr(validator))})
            try:
                verdict = validator(values)
                if type(verdict) is not bool:
                    failures.append("hidden_validator_verdict_invalid")
                elif not verdict:
                    failures.append("hidden_validator_rejected")
            except Exception as exc:
                failures.append(f"hidden_validator_failed:{type(exc).__name__}")
    elif validator_command is not None:
        try:
            validated, validator_version, validator_hash = _run_isolated_validator(
                validator_command,
                values=values,
                protocol_hash=protocol.protocol_hash,
                raw_trial_hash=_digest(records),
                timeout_seconds=validator_timeout_seconds,
                max_output_bytes=validator_max_output_bytes,
            )
            if not validated:
                failures.append("hidden_validator_rejected")
        except (RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            failures.append(f"hidden_validator_failed:{type(exc).__name__}")

    estimate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    percentile = (
        protocol.percentile if _is_exact_int(protocol.percentile) and protocol.percentile in {50, 90, 95, 99} else None
    )
    percentile_estimand_map: dict[int | None, EstimandName] = {
        None: "mean",
        50: "p50",
        90: "p90",
        95: "p95",
        99: "p99",
    }
    estimand = percentile_estimand_map[percentile]
    if values:
        if len(values) > 1:
            bca = bca_confidence_interval(
                protocol.metric if type(protocol.metric) is str and protocol.metric.strip() else "metric",
                values,
                estimand=estimand,
                alpha=valid_alpha,
                resamples=MIN_DEV_BOOTSTRAP_RESAMPLES,
                min_resamples=MIN_DEV_BOOTSTRAP_RESAMPLES,
                seed=protocol.preregistered_seeds[0]
                if type(protocol.preregistered_seeds) in (list, tuple)
                and protocol.preregistered_seeds
                and _is_exact_int(protocol.preregistered_seeds[0])
                else 0xAE61_5BEE,
            )
            estimate = bca.estimate
            ci_low = bca.ci_low
            ci_high = bca.ci_high
        else:
            estimate = _eval_estimand(values, estimand)
            ci_low = ci_high = estimate

    if normalized_baseline is not None and estimate is not None:
        if direction == "higher_is_better" and ci_low is not None and ci_low <= normalized_baseline:
            failures.append("confidence_interval_does_not_clear_baseline")
        if direction == "lower_is_better" and ci_high is not None and ci_high >= normalized_baseline:
            failures.append("confidence_interval_does_not_clear_baseline")
    status = "REJECTED" if failures else "PASS"
    try:
        protocol_hash = protocol.protocol_hash
    except TypeError, ValueError, AttributeError:
        protocol_hash = _digest(protocol)
    raw_trial_hash = _digest(records)
    artifact_hash = _digest(
        {
            "schema": "aegis-benchmark-artifact-v2",
            "protocol_hash": protocol_hash,
            "raw_records": records,
            "baseline": normalized_baseline,
            "environment_hash": normalized_environment_hash,
            "validator_hash": validator_hash,
            "validator_version": validator_version,
            "contamination_flags": normalized_contamination_flags,
            "status": status,
        }
    )
    return BenchmarkResultV2(
        protocol_hash=protocol_hash,
        status=status,
        estimate=estimate,
        ci_low=ci_low,
        ci_high=ci_high,
        trial_count=len(records),
        warmup_count=actual_warmups,
        paired_block_count=actual_pairs,
        raw_trial_hash=raw_trial_hash,
        contamination_flags=normalized_contamination_flags,
        failure_reasons=tuple(dict.fromkeys(failures)),
        raw_trials=values,
        environment_hash=normalized_environment_hash,
        validator_hash=validator_hash,
        artifact_hash=artifact_hash,
        valid_trial_count=len(values),
        raw_records=records,
        validator_version=validator_version,
    )


__all__ = [
    "DEFAULT_BOOTSTRAP_RESAMPLES",
    "HDR_MAX_VALUE_NS",
    "JACKKNIFE_BLOCK_COUNT",
    "MIN_DEV_BOOTSTRAP_RESAMPLES",
    "BcaConfidenceInterval",
    "BenchmarkProtocolV2",
    "BenchmarkProtocolV3",
    "BenchmarkResultV2",
    "BenchmarkResultV3",
    "BenchmarkTrialRecord",
    "CliffsDeltaResult",
    "DistributionV3",
    "EnvironmentFingerprint",
    "EnvironmentFingerprintV3",
    "HdrHistogramLog",
    "LatencyPercentiles",
    "MannWhitneyResult",
    "McNemarResult",
    "NewcombeWilsonDiffCi",
    "OpenLoopConfig",
    "OpenLoopHarness",
    "OpenLoopReport",
    "OpenLoopUtilizationPoint",
    "PairedBenchmarkComparisonV3",
    "PairedEstimandComparisonV3",
    "SensitivitySelfTestReport",
    "SesoiPolicy",
    "WilcoxonResult",
    "WilsonScoreCi",
    "apply_holm_bonferroni_v3",
    "bca_confidence_interval",
    "cliffs_delta",
    "evaluate_benchmark",
    "evaluate_benchmark_v3",
    "evaluate_paired_benchmark_v3",
    "exact_mcnemar_test",
    "generate_poisson_arrivals_ns",
    "hodges_lehmann_pseudomedian",
    "holm_bonferroni",
    "lemire_bounded_index",
    "mann_whitney_u_test",
    "newcombe_wilson_diff_ci",
    "normal_cdf",
    "normal_inv_cdf",
    "pass_at_k_with_bca_ci",
    "quantile_type7_sorted",
    "run_canary_sensitivity_self_test",
    "seed_from_label",
    "simulate_open_loop_queue",
    "splitmix64_next",
    "unbiased_pass_at_k",
    "uniform_open0_closed1",
    "wilcoxon_signed_rank_test",
    "wilson_score_ci",
]
