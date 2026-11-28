from __future__ import annotations

import math
from dataclasses import replace

import pytest

from aegis_cognition.benchmark import (
    DEFAULT_BOOTSTRAP_RESAMPLES,
    MIN_DEV_BOOTSTRAP_RESAMPLES,
    BenchmarkProtocolV2,
    BenchmarkProtocolV3,
    DistributionV3,
    EnvironmentFingerprintV3,
    HdrHistogramLog,
    OpenLoopHarness,
    PairedBenchmarkComparisonV3,
    SesoiPolicy,
    apply_holm_bonferroni_v3,
    bca_confidence_interval,
    cliffs_delta,
    evaluate_benchmark,
    evaluate_benchmark_v3,
    evaluate_paired_benchmark_v3,
    exact_mcnemar_test,
    hodges_lehmann_pseudomedian,
    holm_bonferroni,
    lemire_bounded_index,
    mann_whitney_u_test,
    newcombe_wilson_diff_ci,
    normal_cdf,
    normal_inv_cdf,
    pass_at_k_with_bca_ci,
    quantile_type7_sorted,
    run_canary_sensitivity_self_test,
    simulate_open_loop_queue,
    unbiased_pass_at_k,
    wilcoxon_signed_rank_test,
    wilson_score_ci,
)


def _clean_env() -> EnvironmentFingerprintV3:
    return EnvironmentFingerprintV3.capture(
        network_policy="offline",
        rustc_version="1.99.1",
        cpu_governor="performance",
        pinned_cores="0-3",
        pre_run_load_avg=0.05,
        post_run_load_avg=0.05,
        baseline_cpu_mhz=3600.0,
        measured_cpu_mhz=3550.0,
    )


def _v3_protocol(
    *,
    name: str = "suite",
    metric: str = "latency_ns",
    direction: str = "lower_is_better",
    alpha: float = 0.05,
    min_trials: int = 30,
    warmups: int = 10,
    paired_blocks: int = 30,
    bootstrap_resamples: int = MIN_DEV_BOOTSTRAP_RESAMPLES,
    ci_enforced: bool = False,
    percentile: int | None = None,
) -> BenchmarkProtocolV3:
    return BenchmarkProtocolV3(
        name=name,
        metric=metric,
        direction=direction,
        alpha=alpha,
        min_trials=min_trials,
        warmups=warmups,
        paired_blocks=paired_blocks,
        bootstrap_resamples=bootstrap_resamples,
        ci_enforced=ci_enforced,
        preregistered_seeds=(101, 202, 303, 404, 505),
        frozen_split_hash="split-v3-sha",
        contamination_checks=("load_avg", "thermal", "leak"),
        percentile=percentile,
        sesoi=SesoiPolicy(),
    )


def test_normal_cdf_inv_cdf_and_lemire_bounded_sampling() -> None:
    assert abs(normal_cdf(0.0) - 0.5) < 1e-15
    assert abs(normal_cdf(1.959963984540054) - 0.975) < 1e-12
    for p in (0.001, 0.025, 0.1, 0.5, 0.9, 0.975, 0.999):
        z = normal_inv_cdf(p)
        assert abs(normal_cdf(z) - p) < 1e-12

    state = 0x1234_5678_9ABC_DEF0
    counts = [0] * 7
    for _ in range(35_000):
        state, idx = lemire_bounded_index(state, 7)
        counts[idx] += 1
    for cnt in counts:
        assert 4_600 < cnt < 5_400


def test_bca_bootstrap_honors_alpha_and_block_jackknife_and_zero_variance_distinction() -> None:
    samples = [100.0 + 0.05 * (i * i) for i in range(1, 251)]
    ci_99 = bca_confidence_interval("skew", samples, estimand="p95", alpha=0.01, resamples=MIN_DEV_BOOTSTRAP_RESAMPLES)
    ci_90 = bca_confidence_interval("skew", samples, estimand="p95", alpha=0.10, resamples=MIN_DEV_BOOTSTRAP_RESAMPLES)
    assert ci_99.ci_method == "bca"
    assert ci_99.ci_low < ci_99.estimate < ci_99.ci_high
    assert (ci_99.ci_high - ci_99.ci_low) > (ci_90.ci_high - ci_90.ci_low)

    dist = DistributionV3.from_samples(
        "skew",
        samples,
        alpha=0.05,
        resamples=MIN_DEV_BOOTSTRAP_RESAMPLES,
    )
    assert dist.p50 == quantile_type7_sorted(sorted(samples), 0.50)
    assert dist.p90 == quantile_type7_sorted(sorted(samples), 0.90)
    assert dist.p95 == quantile_type7_sorted(sorted(samples), 0.95)
    assert dist.p99 == quantile_type7_sorted(sorted(samples), 0.99)
    assert dist.mean_ci.ci_low < dist.mean < dist.mean_ci.ci_high
    assert dist.p95_ci.ci_low < dist.p95 < dist.p95_ci.ci_high

    const_ci = bca_confidence_interval("const", [42.0] * 40, estimand="mean", resamples=MIN_DEV_BOOTSTRAP_RESAMPLES)
    assert const_ci.ci_method == "degenerate_zero_variance"
    assert const_ci.ci_low == 42.0 and const_ci.ci_high == 42.0

    sym = [float((i % 10) + 1) for i in range(60)]
    sym_ci = bca_confidence_interval("sym", sym, estimand="mean", resamples=MIN_DEV_BOOTSTRAP_RESAMPLES)
    assert sym_ci.ci_high > sym_ci.ci_low


def test_v2_evaluate_benchmark_honors_alpha_and_percentile_cis() -> None:
    proto_p95_strict = BenchmarkProtocolV2(
        name="p95-alpha-test",
        metric="throughput",
        direction="higher_is_better",
        alpha=0.01,
        min_trials=30,
        warmups=10,
        paired_blocks=30,
        preregistered_seeds=(1, 2, 3, 4, 5),
        frozen_split_hash="sha-v2",
        contamination_checks=("seed",),
        percentile=95,
    )
    proto_p95_loose = BenchmarkProtocolV2(
        name="p95-alpha-test",
        metric="throughput",
        direction="higher_is_better",
        alpha=0.10,
        min_trials=30,
        warmups=10,
        paired_blocks=30,
        preregistered_seeds=(1, 2, 3, 4, 5),
        frozen_split_hash="sha-v2",
        contamination_checks=("seed",),
        percentile=95,
    )
    trials = [100.0 + float(i * i) for i in range(1, 31)]
    r_strict = evaluate_benchmark(proto_p95_strict, trials, environment_hash="env-1")
    r_loose = evaluate_benchmark(proto_p95_loose, trials, environment_hash="env-1")
    assert r_strict.status == "PASS"
    assert r_strict.ci_low is not None and r_strict.ci_high is not None
    assert r_strict.ci_high > r_strict.ci_low
    assert r_loose.ci_low is not None and r_loose.ci_high is not None
    assert (r_strict.ci_high - r_strict.ci_low) > (r_loose.ci_high - r_loose.ci_low)


def test_nonparametric_tests_binary_outcomes_pass_at_k_cliffs_and_holm() -> None:
    # Exact Wilcoxon signed-rank on [1, 2, 3, 4, 5, 6] => W+=21, exact p = 2/64 = 0.03125
    w_res = wilcoxon_signed_rank_test([10.0] * 6, [11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
    assert w_res.exact is True
    assert w_res.w_plus == 21.0
    assert abs(w_res.p_value - 0.03125) < 1e-12
    assert abs(w_res.hodges_lehmann_pseudomedian - 3.5) < 1e-12

    # Ties + zeros exact Wilcoxon (n_nonzero = 6, ranks: two 1s -> 1.5, 2 -> 3, etc.)
    w_ties = wilcoxon_signed_rank_test([5.0, 5.0, 5.0, 5.0, 5.0, 5.0], [5.0, 6.0, 7.0, 8.0, 6.0, 9.0])
    assert w_ties.exact is True
    assert w_ties.n_nonzero == 5
    assert 0.0 < w_ties.p_value <= 0.125

    # Large-n > 25 normal approx + tie/large HL selector (> 400 elements)
    hl_big = hodges_lehmann_pseudomedian([0.5 * i for i in range(1, 501)])
    assert abs(hl_big - 125.25) < 1e-9
    w_big = wilcoxon_signed_rank_test([float(i) for i in range(40)], [float(i) + 2.0 for i in range(40)])
    assert w_big.exact is False
    assert w_big.p_value < 1e-6

    mw = mann_whitney_u_test([1.0, 2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0, 10.0])
    assert mw.u_statistic == 0.0
    assert mw.p_value < 0.02

    ws = wilson_score_ci(18, 20, alpha=0.05)
    assert abs(ws.proportion - 0.9) < 1e-12
    assert 0.68 < ws.ci_low < ws.proportion < ws.ci_high < 0.99

    mc = exact_mcnemar_test(12, 1)
    assert abs(mc.p_value - (28.0 / 8192.0)) < 1e-12

    nw = newcombe_wilson_diff_ci(40, 2, 14, 44, alpha=0.05)
    assert abs(nw.diff - 0.12) < 1e-12
    assert 0.0 < nw.ci_low < nw.diff < nw.ci_high

    assert abs(unbiased_pass_at_k(5, 2, 2) - 0.7) < 1e-12
    pk_ci = pass_at_k_with_bca_ci([(5, 2), (5, 3), (5, 4), (5, 1)], k=2, resamples=MIN_DEV_BOOTSTRAP_RESAMPLES)
    assert pk_ci.ci_low < pk_ci.estimate < pk_ci.ci_high

    cd = cliffs_delta([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert cd.delta == 1.0 and cd.magnitude == "large"

    adj = holm_bonferroni([0.01, 0.04, 0.03])
    assert abs(adj[0] - 0.03) < 1e-12
    assert abs(adj[2] - 0.06) < 1e-12
    assert abs(adj[1] - 0.06) < 1e-12


def test_v3_rejects_implicit_warmups_contamination_and_low_ci_resamples() -> None:
    proto_ci = _v3_protocol(ci_enforced=True, bootstrap_resamples=2000)
    with pytest.raises(ValueError, match="bootstrap_resamples"):
        proto_ci.validate()

    proto_default = _v3_protocol(ci_enforced=True, bootstrap_resamples=DEFAULT_BOOTSTRAP_RESAMPLES)
    proto_default.validate()

    # Insufficient warmups/paired_blocks must be rejected (no fabrication)
    bad = evaluate_benchmark_v3(
        _v3_protocol(),
        [100.0 + float(i) for i in range(30)],
        warmups=0,
        paired_blocks=10,
        environment=_clean_env(),
    )
    assert bad.status == "REJECTED"
    assert "insufficient_warmups" in bad.failure_reasons
    assert "insufficient_paired_blocks" in bad.failure_reasons

    # Thermal drop > 10% marks CONTAMINATED fail-closed
    throttled_env = EnvironmentFingerprintV3.capture(
        network_policy="offline",
        rustc_version="1.99.1",
        pre_run_load_avg=0.05,
        post_run_load_avg=0.05,
        baseline_cpu_mhz=4000.0,
        measured_cpu_mhz=3400.0,
    )
    contam = evaluate_benchmark_v3(
        _v3_protocol(),
        [100.0 + float(i) for i in range(30)],
        warmups=10,
        paired_blocks=30,
        environment=throttled_env,
    )
    assert contam.status == "CONTAMINATED"
    assert contam.contamination_verdict == "CONTAMINATED"
    assert "CONTAMINATED:cpu_frequency_drop_gt_10pct" in contam.failure_reasons


def test_v3_rejects_invalid_contamination_checks_when_host_load_is_missing() -> None:
    protocol = replace(_v3_protocol(), contamination_checks=None)
    samples = [100.0 + index for index in range(30)]
    environment = replace(_clean_env(), pre_run_load_avg=None)
    single = evaluate_benchmark_v3(protocol, samples, warmups=10, paired_blocks=30, environment=environment)
    paired = evaluate_paired_benchmark_v3(
        protocol,
        samples,
        samples,
        warmups=10,
        paired_blocks=30,
        environment=environment,
        execution_order=["AB", "BA"] * 15,
    )

    for comparison in (single, paired):
        assert comparison.status == "REJECTED"
        assert "protocol_invalid:ValueError" in comparison.failure_reasons


def test_paired_v3_preserves_correlation_and_checks_counterbalance_and_canary() -> None:
    # Shared block variance cancels out when paired block resampling is used vs unpaired noise
    arm_a = [500.0 + 40.0 * math.sin(i * 0.7) for i in range(40)]
    arm_b = [v + 25.0 for v in arm_a]
    order = ["AB" if i % 2 == 0 else "BA" for i in range(40)]

    comp = evaluate_paired_benchmark_v3(
        _v3_protocol(min_trials=40, paired_blocks=40),
        arm_a,
        arm_b,
        warmups=10,
        paired_blocks=40,
        environment=_clean_env(),
        execution_order=order,
    )
    assert comp.status == "PASS"
    assert abs(comp.estimands[0].delta_estimate - 25.0) < 1e-9
    assert comp.estimands[0].delta_ci.ci_low > 20.0
    assert comp.statistically_significant is True

    # Family-wise Holm-Bonferroni correction updates adjusted_p_value
    adjusted_family = apply_holm_bonferroni_v3([comp, comp])
    assert all(item.adjusted_p_value == min(1.0, (comp.raw_p_value or 0.0) * 2.0) for item in adjusted_family)

    # Non-counterbalanced order fails closed
    bad_order = evaluate_paired_benchmark_v3(
        _v3_protocol(min_trials=40, paired_blocks=40),
        arm_a,
        arm_b,
        warmups=10,
        paired_blocks=40,
        environment=_clean_env(),
        execution_order=["AB"] * 40,
    )
    assert bad_order.status == "REJECTED"
    assert "non_counterbalanced_ab_ba_order" in bad_order.failure_reasons

    # Canary +25% regression sensitivity self-test passes with required thresholds
    canary = run_canary_sensitivity_self_test(resamples=MIN_DEV_BOOTSTRAP_RESAMPLES, ci_enforced=False)
    assert canary.passed is True
    assert canary.quarantine_status == "SENSITIVE_OK"
    assert canary.adjusted_p_value <= 0.05
    assert abs(canary.cliffs_delta) >= 0.33


@pytest.mark.parametrize(
    "execution_order",
    [None, ["AB"] * 29 + ["BA"]],
    ids=("missing", "imbalanced"),
)
def test_paired_v3_rejects_missing_or_imbalanced_execution_order(execution_order: list[str] | None) -> None:
    arm_a = [100.0 + float(index) for index in range(30)]
    arm_b = [value + 25.0 for value in arm_a]

    comparison = evaluate_paired_benchmark_v3(
        _v3_protocol(),
        arm_a,
        arm_b,
        warmups=10,
        paired_blocks=30,
        environment=_clean_env(),
        execution_order=execution_order,
    )

    assert comparison.status == "REJECTED"
    assert "non_counterbalanced_ab_ba_order" in comparison.failure_reasons


def test_paired_v3_artifact_hash_binds_observed_samples() -> None:
    arm_a = [100.0 + index for index in range(30)]
    environment = _clean_env()

    def evaluate(delta: float) -> PairedBenchmarkComparisonV3:
        return evaluate_paired_benchmark_v3(
            _v3_protocol(),
            arm_a,
            [value + delta for value in arm_a],
            warmups=10,
            paired_blocks=30,
            environment=environment,
            execution_order=["AB", "BA"] * 15,
        )

    lower_delta = evaluate(20.0)
    higher_delta = evaluate(40.0)
    assert lower_delta.status == higher_delta.status == "PASS"
    assert lower_delta.estimands[0].delta_estimate == 20.0
    assert higher_delta.estimands[0].delta_estimate == 40.0
    assert lower_delta.raw_p_value == higher_delta.raw_p_value
    assert lower_delta.artifact_hash != higher_delta.artifact_hash
    assert lower_delta.artifact_hash == evaluate(20.0).artifact_hash


def test_paired_v3_artifact_hash_binds_contaminated_host_measurements() -> None:
    samples = [100.0 + index for index in range(30)]
    environment = replace(_clean_env(), logical_cpus=4)

    def evaluate(load_avg: float) -> PairedBenchmarkComparisonV3:
        return evaluate_paired_benchmark_v3(
            _v3_protocol(),
            samples,
            samples,
            warmups=10,
            paired_blocks=30,
            environment=replace(environment, pre_run_load_avg=load_avg),
            execution_order=["AB", "BA"] * 15,
        )

    overloaded = evaluate(2.0)
    heavily_overloaded = evaluate(3.0)
    assert overloaded.status == heavily_overloaded.status == "CONTAMINATED"
    assert overloaded.contamination_flags == heavily_overloaded.contamination_flags == ("pre_run_load_avg_exceeded",)
    assert overloaded.observations_hash == heavily_overloaded.observations_hash
    assert overloaded.environment_hash != heavily_overloaded.environment_hash
    assert overloaded.artifact_hash != heavily_overloaded.artifact_hash


def test_paired_v3_rejects_declared_block_count_different_from_observations() -> None:
    arm_a = [100.0 + float(index) for index in range(30)]
    comparison = evaluate_paired_benchmark_v3(
        _v3_protocol(),
        arm_a,
        [value + 25.0 for value in arm_a],
        warmups=10,
        paired_blocks=31,
        environment=_clean_env(),
        execution_order=["AB" if index % 2 == 0 else "BA" for index in range(30)],
    )

    assert comparison.status == "REJECTED"
    assert "paired_block_observation_mismatch" in comparison.failure_reasons


def test_paired_v3_rejects_additional_blocks_beyond_the_registered_count() -> None:
    comparison = evaluate_paired_benchmark_v3(
        _v3_protocol(),
        [100.0] * 32,
        [125.0] * 32,
        warmups=10,
        paired_blocks=32,
        environment=_clean_env(),
        execution_order=["AB", "BA"] * 16,
    )

    assert comparison.status == "REJECTED"
    assert "paired_blocks_exceed_preregistered_count" in comparison.failure_reasons


def test_paired_v3_rejects_balanced_order_that_differs_from_registered_schedule() -> None:
    comparison = evaluate_paired_benchmark_v3(
        _v3_protocol(),
        [100.0] * 30,
        [125.0] * 30,
        warmups=10,
        paired_blocks=30,
        environment=_clean_env(),
        execution_order=["AB"] * 15 + ["BA"] * 15,
    )

    assert comparison.status == "REJECTED"
    assert "execution_order_mismatch" in comparison.failure_reasons


def test_paired_v3_identical_arms_do_not_prove_an_effect() -> None:
    arm = [100.0 + index for index in range(30)]
    comparison = evaluate_paired_benchmark_v3(
        _v3_protocol(),
        arm,
        list(arm),
        warmups=10,
        paired_blocks=30,
        environment=_clean_env(),
        execution_order=["AB", "BA"] * 15,
    )

    assert comparison.status == "PASS"
    assert comparison.estimands[0].delta_ci.ci_low == comparison.estimands[0].delta_ci.ci_high == 0
    assert not comparison.practical_change_proven


def test_v3_missing_required_host_observations_stays_exploratory() -> None:
    protocol = _v3_protocol()
    env = replace(_clean_env(), measured_cpu_mhz=None, pre_run_load_avg=None)
    arm_a = [100.0] * 30
    arm_b = [150.0] * 30

    single = evaluate_benchmark_v3(protocol, arm_a, warmups=10, paired_blocks=30, environment=env)
    paired = evaluate_paired_benchmark_v3(
        protocol,
        arm_a,
        arm_b,
        warmups=10,
        paired_blocks=30,
        environment=env,
        execution_order=["AB", "BA"] * 15,
    )

    assert single.status == paired.status == "EXPLORATORY"
    assert single.contamination_verdict == paired.contamination_verdict == "UNKNOWN"
    assert single.distribution is not None
    assert paired.estimands[0].delta_estimate == 50.0
    assert {"load_avg_unavailable", "cpu_frequency_unavailable"} <= set(paired.contamination_flags)
    assert not apply_holm_bonferroni_v3([paired])[0].practical_change_proven


def test_open_loop_hdrhistogram_and_coordinated_omission_detection() -> None:
    hist = HdrHistogramLog()
    for val in range(1, 10_001):
        hist.record_ns(val * 1_000)
    summary = hist.summary()
    assert abs(summary.p50_ns - 5_000_000.0) / 5_000_000.0 < 0.002
    assert abs(summary.p95_ns - 9_500_000.0) / 9_500_000.0 < 0.002
    assert abs(summary.p99_ns - 9_900_000.0) / 9_900_000.0 < 0.002

    svc_times = [50_000] * 1_000
    svc_times[200] = 20_000_000  # 20ms pause causing queueing collapse
    svc_h, soj_h, admitted, queued, completed, rejected, _timed_out = simulate_open_loop_queue(
        svc_times,
        15_000.0,
        workers=1,
        queue_capacity=2_000,
    )
    assert admitted == 1_000 and completed == 1_000 and rejected == 0
    assert queued > 50
    assert soj_h.quantile(0.99) > svc_h.quantile(0.99) * 10.0

    # Calibration probe runs real callable
    calls = 0

    def _work() -> None:
        nonlocal calls
        calls += 1

    mu_max = OpenLoopHarness.calibrate_capacity(64, _work)
    assert mu_max > 0.0 and calls >= 80
