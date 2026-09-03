"""Run the AESE adaptive-measurement calibration campaign in SHADOW.

This harness evaluates the existing stopping rule against deterministic,
synthetic data-generating processes.  It never changes test selection,
promotes evidence, or labels synthetic data as a production measurement.
Results are reproducible from the source revision, generator version, campaign
seed, protocol variants, and replicate seeds retained in the artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Final, cast


ROOT: Final[Path] = Path(__file__).resolve().parents[1]
SCHEMA: Final[str] = "aese-statistical-calibration-v1"
GENERATOR_VERSION: Final[str] = "aese-statistical-calibration-generator-v2"
VALIDATOR_ID: Final[str] = f"{SCHEMA}:{GENERATOR_VERSION}"
DEFAULT_SEED: Final[int] = 2_026_0903
DEFAULT_MIN_REPLICATES: Final[int] = 30
DEFAULT_MAX_REPLICATES: Final[int] = 120
DEFAULT_REPLICATE_BATCH: Final[int] = 30
DEFAULT_REPLICATES: Final[int] = DEFAULT_MIN_REPLICATES
META_NOMINAL_ALPHA: Final[float] = 0.05
MIN_DECISION_RESOLUTION: Final[float] = 0.50
SCENARIOS: Final[tuple[str, ...]] = ("null", "improvement", "regression")
CHECKPOINT_POLICIES: Final[tuple[str, ...]] = ("every_observation", "every_block")
META_METRICS: Final[tuple[str, ...]] = ("coverage", "false_pass", "false_fail", "decision_resolution")
PREREGISTERED_LOOK_POINTS: Final[tuple[int, ...]] = (30, 60, 90, 120)


@dataclass(frozen=True)
class FamilyDefinition:
    name: str
    domain: str
    kind: str
    base: float
    scale: float
    notes: str
    unstable_expected: bool = False
    contaminated_expected: bool = False


FAMILIES: Final[tuple[FamilyDefinition, ...]] = (
    FamilyDefinition("iid_gaussian", "CANDIDATE_IN_DOMAIN", "gaussian", 1.0, 0.5, "independent finite-variance normal"),
    FamilyDefinition("log_normal", "CANDIDATE_IN_DOMAIN", "lognormal", 0.0, 0.4, "positive skew with finite mean"),
    FamilyDefinition("gamma_skewed_positive", "CANDIDATE_IN_DOMAIN", "gamma", 2.0, 0.75, "positive skew with shape two"),
    FamilyDefinition("heavy_tail", "OUT_OF_DOMAIN", "student_t", 0.0, 1.0, "Student-t with one-and-a-half degrees of freedom; variance is not finite"),
    FamilyDefinition("mixture_bimodal", "CANDIDATE_IN_DOMAIN", "mixture", 0.0, 1.0, "two finite-variance Gaussian modes"),
    FamilyDefinition("heteroscedastic", "CANDIDATE_IN_DOMAIN", "heteroscedastic", 1.0, 0.4, "independent observations with changing variance"),
    FamilyDefinition("ar1_positive", "OUT_OF_DOMAIN", "ar1_positive", 1.0, 0.3, "AR(1) positive serial dependence", unstable_expected=True),
    FamilyDefinition("ar1_negative", "OUT_OF_DOMAIN", "ar1_negative", 1.0, 0.3, "AR(1) negative serial dependence", unstable_expected=True),
    FamilyDefinition("slow_drift", "OUT_OF_DOMAIN", "slow_drift", 1.0, 0.4, "slow deterministic drift", unstable_expected=True),
    FamilyDefinition("linear_trend", "OUT_OF_DOMAIN", "linear_trend", 1.0, 0.5, "linear deterministic trend", unstable_expected=True),
    FamilyDefinition("step_change", "OUT_OF_DOMAIN", "step_change", 1.0, 0.5, "mid-run level step", unstable_expected=True),
    FamilyDefinition("change_point", "OUT_OF_DOMAIN", "change_point", 1.0, 0.5, "late level change point", unstable_expected=True),
    FamilyDefinition("bursty_contamination", "OUT_OF_DOMAIN", "bursty_contamination", 1.0, 0.5, "short bursts with an explicit contamination flag", contaminated_expected=True),
    FamilyDefinition("rare_extreme_outlier", "OUT_OF_DOMAIN", "rare_extreme_outlier", 1.0, 0.5, "one latent extreme outlier without a flag", contaminated_expected=True),
    FamilyDefinition("near_zero_mean", "CANDIDATE_IN_DOMAIN", "gaussian", 0.0, 1e-5, "small finite-variance mean near zero"),
    FamilyDefinition("large_magnitude", "CANDIDATE_IN_DOMAIN", "gaussian", 1e9, 1e6, "large finite-magnitude values"),
    FamilyDefinition("small_variance", "CANDIDATE_IN_DOMAIN", "gaussian", 10.0, 1e-3, "small but non-zero variance"),
    FamilyDefinition("high_variance", "CANDIDATE_IN_DOMAIN", "gaussian", 10.0, 10.0, "high finite variance"),
)

FAMILY_BY_NAME: Final[dict[str, FamilyDefinition]] = {family.name: family for family in FAMILIES}


@dataclass(frozen=True)
class ProtocolVariant:
    variant_id: str
    warmup_count: int
    min_observations: int
    block_size: int
    max_observations: int
    alpha: float
    relative_precision: float


PROTOCOL_VARIANTS: Final[tuple[ProtocolVariant, ...]] = (
    ProtocolVariant("default", 5, 30, 5, 60, 0.05, 0.05),
    ProtocolVariant("strict_alpha", 5, 36, 3, 45, 0.01, 0.10),
    ProtocolVariant("wide_budget", 2, 40, 10, 90, 0.10, 0.02),
)
VARIANT_BY_ID: Final[dict[str, ProtocolVariant]] = {
    variant.variant_id: variant for variant in PROTOCOL_VARIANTS
}


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_value(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "NOT_AVAILABLE"
    # An empty stdout is a valid result for ``git status --porcelain`` and
    # therefore means a clean worktree, not an unavailable Git invocation.
    return result.stdout.strip()


def _source_metadata() -> dict[str, object]:
    status = _git_value("status", "--porcelain=v1")
    environment = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
    }
    return {
        "source_sha": _git_value("rev-parse", "HEAD"),
        "worktree_status": "CLEAN" if not status else "DIRTY",
        "environment": environment,
        "environment_hash": _hash(environment),
    }


def _adaptive_primitives() -> tuple[object, object]:
    # Keep imports local so direct ``python scripts/...`` invocation remains
    # independent of the caller's module search path and script bootstrap.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aegis_cognition.aese import AdaptiveMeasurementSpec, evaluate_adaptive_measurement

    return AdaptiveMeasurementSpec, evaluate_adaptive_measurement


def _variant_spec(variant: ProtocolVariant) -> object:
    spec_type, _ = _adaptive_primitives()
    return spec_type(
        metric="synthetic_metric",
        warmup_count=variant.warmup_count,
        min_observations=variant.min_observations,
        block_size=variant.block_size,
        max_observations=variant.max_observations,
        alpha=variant.alpha,
        relative_precision=variant.relative_precision,
    )


def _effect_size(family: FamilyDefinition) -> float:
    if family.name == "large_magnitude":
        return 50_000_000.0
    if family.name == "high_variance":
        return 5.0
    if family.name == "near_zero_mean":
        return 0.25
    if family.name in {"small_variance", "heavy_tail", "ar1_positive", "ar1_negative"}:
        return 0.25
    return max(abs(family.scale) * 0.5, 0.25)


def _scenario_delta(family: FamilyDefinition, scenario: str) -> float:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown calibration scenario: {scenario}")
    effect = _effect_size(family)
    return {"null": 0.0, "improvement": -effect, "regression": effect}[scenario]


def _family_center(family: FamilyDefinition, index: int, count: int) -> float:
    fraction = index / max(count - 1, 1)
    if family.kind == "lognormal":
        return math.exp(family.base + (family.scale**2) / 2.0)
    if family.kind == "gamma":
        return family.base * family.scale
    if family.kind == "mixture":
        return 0.65 * (-family.scale) + 0.35 * (2.0 * family.scale)
    if family.kind == "linear_trend":
        return family.base + family.scale * (fraction - 0.5) * 2.0
    if family.kind == "slow_drift":
        return family.base + family.scale * math.sin(2.0 * math.pi * fraction)
    if family.kind == "step_change":
        return family.base + (family.scale * 2.0 if fraction >= 0.5 else 0.0)
    if family.kind == "change_point":
        return family.base + (family.scale * 3.0 if fraction >= 0.6 else 0.0)
    return family.base


def _generate_series(
    family: FamilyDefinition,
    scenario: str,
    seed: int,
    count: int,
) -> tuple[list[float], list[float], tuple[str, ...]]:
    rng = random.Random(seed)
    delta = _scenario_delta(family, scenario)
    values: list[float] = []
    centers = [_family_center(family, index, count) + delta for index in range(count)]
    previous = family.base
    for index, center in enumerate(centers):
        kind = family.kind
        if kind in {"gaussian", "near_zero_mean"}:
            value = rng.gauss(center, family.scale)
        elif kind == "lognormal":
            value = rng.lognormvariate(family.base, family.scale) + delta
        elif kind == "gamma":
            value = rng.gammavariate(family.base, family.scale) + delta
        elif kind == "student_t":
            degrees = 1.5
            numerator = rng.gauss(0.0, 1.0)
            denominator = math.sqrt(rng.gammavariate(degrees / 2.0, 2.0) / degrees)
            value = center + family.scale * numerator / denominator
        elif kind == "mixture":
            location = -family.scale if rng.random() < 0.65 else 2.0 * family.scale
            value = center + rng.gauss(location - (0.65 * (-family.scale) + 0.35 * (2.0 * family.scale)), family.scale * 0.25)
        elif kind == "heteroscedastic":
            changing_scale = family.scale * (0.25 + 1.75 * index / max(count - 1, 1))
            value = rng.gauss(center, changing_scale)
        elif kind in {"ar1_positive", "ar1_negative"}:
            rho = 0.85 if kind == "ar1_positive" else -0.85
            innovation = rng.gauss(0.0, family.scale * math.sqrt(1.0 - rho * rho))
            previous = center + rho * (previous - center) + innovation
            value = previous
        elif kind in {"slow_drift", "linear_trend", "step_change", "change_point"}:
            value = rng.gauss(center, family.scale * 0.25)
        elif kind == "bursty_contamination":
            value = rng.gauss(center, family.scale)
            if index % 15 in {0, 1, 2}:
                value += 8.0 * family.scale
        elif kind == "rare_extreme_outlier":
            value = rng.gauss(center, family.scale)
            if index == count // 2:
                value += 100.0 * family.scale
        else:  # pragma: no cover - family registry is validated before dispatch.
            raise ValueError(f"unsupported calibration family: {family.name}")
        if not math.isfinite(value):
            raise OverflowError(f"non-finite generated value for {family.name}")
        values.append(value)
    flags = ("synthetic_contamination",) if family.name == "bursty_contamination" else ()
    return values, centers, flags


def _trial_seed(
    campaign_seed: int,
    family_index: int,
    scenario_index: int,
    variant_index: int,
    policy_index: int,
    replicate: int,
) -> int:
    return int(
        _hash(
            {
                "campaign_seed": campaign_seed,
                "family_index": family_index,
                "scenario_index": scenario_index,
                "variant_index": variant_index,
                "policy_index": policy_index,
                "replicate": replicate,
            }
        )[:16],
        16,
    )


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _finite_look_schedule(minimum: int, maximum: int, batch: int) -> tuple[int, ...]:
    """Return a preregistered schedule with no more than four finite looks."""

    if type(minimum) is not int or type(maximum) is not int or type(batch) is not int:
        raise ValueError("calibration look schedule values must be integers")
    if not 1 <= minimum <= maximum <= 5_000 or batch < 1:
        raise ValueError("calibration look schedule bounds are invalid")
    if minimum == DEFAULT_MIN_REPLICATES and maximum == DEFAULT_MAX_REPLICATES and batch == DEFAULT_REPLICATE_BATCH:
        return PREREGISTERED_LOOK_POINTS
    points = [minimum]
    while len(points) < 3 and points[-1] + batch < maximum:
        points.append(points[-1] + batch)
    if points[-1] != maximum:
        points.append(maximum)
    return tuple(sorted(set(points)))


def _alpha_allocation(
    look_points: tuple[int, ...],
    *,
    alpha_total: float = META_NOMINAL_ALPHA,
    metric_names: tuple[str, ...] = META_METRICS,
    scenario_names: tuple[str, ...] = SCENARIOS,
) -> dict[str, object]:
    """Allocate one preregistered alpha budget across finite looks and metrics."""

    if not look_points or len(look_points) > len(PREREGISTERED_LOOK_POINTS):
        raise ValueError("calibration look count exceeds preregistration")
    if any(type(point) is not int for point in look_points) or tuple(sorted(set(look_points))) != look_points:
        raise ValueError("calibration look points must be unique and sorted")
    if type(alpha_total) not in (int, float) or not 0.0 < float(alpha_total) < 1.0:
        raise ValueError("calibration alpha_total is invalid")
    if not metric_names or len(set(metric_names)) != len(metric_names):
        raise ValueError("calibration metric family is invalid")
    if not scenario_names or len(set(scenario_names)) != len(scenario_names):
        raise ValueError("calibration scenario family is invalid")
    look_count = len(look_points)
    metric_count = len(metric_names)
    scenario_count = len(scenario_names)
    per_look = float(alpha_total) / look_count
    per_bound = per_look / (metric_count * scenario_count)
    return {
        "error_control_scope": "PER_CONFIGURATION_CELL",
        "look_points": list(look_points),
        "look_count": look_count,
        "metric_count": metric_count,
        "scenario_count": scenario_count,
        "family_size": look_count * metric_count * scenario_count,
        "family_unit": "look_metric_scenario_bounds",
        "alpha_total": float(alpha_total),
        "alpha_allocation_method": "BONFERRONI_EQUAL_OVER_FINITE_LOOKS_METRICS_SCENARIOS",
        "look_alpha_allocations": [per_look] * look_count,
        "per_bound_alpha": per_bound,
    }


def _wilson_bounds(
    successes: int,
    trials: int,
    *,
    nominal_alpha: float = META_NOMINAL_ALPHA,
    tail: str = "one_sided_upper",
) -> dict[str, object]:
    """Return an auditable Wilson score interval for a Bernoulli rate.

    This is an uncertainty estimate for a finite calibration campaign, not a
    claim that a synthetic generator is representative of production.
    """

    if type(successes) is not int or type(trials) is not int or not 0 <= successes <= trials:
        raise ValueError("calibration Bernoulli counts are invalid")
    if type(nominal_alpha) not in (int, float) or not 0.0 < float(nominal_alpha) < 1.0:
        raise ValueError("calibration nominal alpha is invalid")
    if tail not in {"one_sided_lower", "one_sided_upper", "two_sided"}:
        raise ValueError("calibration Bernoulli tail is invalid")
    if trials == 0:
        lower: float | None = None
        upper: float | None = None
    else:
        alpha = float(nominal_alpha)
        tail_alpha = alpha / 2.0 if tail == "two_sided" else alpha
        z = NormalDist().inv_cdf(1.0 - tail_alpha)
        proportion = successes / trials
        z_squared = z * z
        denominator = 1.0 + z_squared / trials
        center = (proportion + z_squared / (2.0 * trials)) / denominator
        half_width = (
            z
            * math.sqrt(
                proportion * (1.0 - proportion) / trials
                + z_squared / (4.0 * trials * trials)
            )
            / denominator
        )
        lower = max(0.0, center - half_width)
        upper = min(1.0, center + half_width)
        if successes == 0:
            lower = 0.0
        if successes == trials:
            upper = 1.0
    return {
        "method_id": "wilson_score_v1",
        "nominal_alpha": float(nominal_alpha),
        "one_sided_or_two_sided": tail,
        "successes": successes,
        "trials": trials,
        "lower_bound": lower,
        "upper_bound": upper,
    }


def _bound_decision(
    bounds: dict[str, object],
    *,
    threshold: float,
    relation: str,
) -> str:
    """Classify a bound without treating an undecidable sample as failure."""

    if relation not in {"minimum", "maximum"}:
        return "INCONCLUSIVE"
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("calibration threshold is invalid")
    lower = bounds["lower_bound"]
    upper = bounds["upper_bound"]
    if lower is None or upper is None:
        return "INCONCLUSIVE"
    if relation == "minimum":
        if float(lower) >= threshold:
            return "VALIDATED"
        if float(upper) < threshold:
            return "INVALIDATED"
    else:
        if float(upper) <= threshold:
            return "VALIDATED"
        if float(lower) > threshold:
            return "INVALIDATED"
    return "INCONCLUSIVE"


def _rate_metric(
    successes: int,
    trials: int,
    *,
    threshold: float,
    relation: str,
    tail: str,
    nominal_alpha: float = META_NOMINAL_ALPHA,
) -> dict[str, object]:
    bounds = _wilson_bounds(successes, trials, nominal_alpha=nominal_alpha, tail=tail)
    bounds["threshold"] = threshold
    bounds["threshold_relation"] = relation
    bounds["decision"] = _bound_decision(bounds, threshold=threshold, relation=relation)
    return bounds


def _run_trial(
    family: FamilyDefinition,
    scenario: str,
    variant: ProtocolVariant,
    checkpoint_policy: str,
    seed: int,
) -> dict[str, object]:
    if checkpoint_policy not in CHECKPOINT_POLICIES:
        raise ValueError(f"unknown checkpoint policy: {checkpoint_policy}")
    _, evaluator = _adaptive_primitives()
    spec = _variant_spec(variant)
    total_count = variant.warmup_count + variant.max_observations
    generated, centers, contamination_flags = _generate_series(family, scenario, seed, total_count)
    warmups = tuple(generated[: variant.warmup_count])
    observations = generated[variant.warmup_count :]
    planned_centers = centers[variant.warmup_count :]
    null_centers = [
        _family_center(family, index, total_count)
        for index in range(variant.warmup_count, total_count)
    ]
    true_estimand = sum(planned_centers) / len(planned_centers)
    baseline = sum(null_centers) / len(null_centers)
    consumed = 0
    result: object | None = None
    if checkpoint_policy == "every_observation":
        chunk_sizes = [1] * len(observations)
    else:
        chunk_sizes = [variant.block_size] * (len(observations) // variant.block_size)
        if len(observations) % variant.block_size:
            chunk_sizes.append(len(observations) % variant.block_size)
    for chunk_size in chunk_sizes:
        consumed += chunk_size
        result = evaluator(
            spec,
            observations[:consumed],
            warmups=warmups,
            baseline=baseline,
            contamination_flags=contamination_flags,
        )
        if result.status != "CONTINUE":
            break
    if result is None:
        raise RuntimeError("calibration trial produced no checkpoint")
    result.validate()
    relation = "NULL" if scenario == "null" else scenario.upper()
    expected_decision = "PASS" if scenario == "improvement" else "NOT_PASS"
    decision = result.status if result.status in {"PASS", "FAIL"} else None
    correct_decision = (decision == "PASS" and expected_decision == "PASS") or (
        decision == "FAIL" and expected_decision == "NOT_PASS"
    )
    has_interval = result.ci_low is not None and result.ci_high is not None
    covered = bool(has_interval and result.ci_low <= true_estimand <= result.ci_high)
    false_pass = result.status == "PASS" and relation != "IMPROVEMENT"
    false_fail = result.status == "FAIL" and relation == "IMPROVEMENT"
    early_stop = consumed < variant.max_observations and result.status in {
        "PASS",
        "FAIL",
        "UNSTABLE",
        "CONTAMINATED",
    }
    premature_terminal = consumed < variant.min_observations and result.status in {
        "PASS",
        "FAIL",
        "UNSTABLE",
        "CONTAMINATED",
    }
    budget_exhausted = consumed >= variant.max_observations and result.status == "INSUFFICIENT_EVIDENCE"
    return {
        "seed": seed,
        "scenario": scenario,
        "relation": relation,
        "expected_decision": expected_decision,
        "decision": decision,
        "correct_decision": correct_decision,
        "wrong_decision": decision is not None and not correct_decision,
        "inconclusive_decision": decision is None,
        "true_estimand": true_estimand,
        "final_status": result.status,
        "observations_consumed": consumed,
        "raw_observation_count": result.raw_observation_count,
        "observation_count": result.observation_count,
        "block_count": result.block_count,
        "coverage_eligible": has_interval,
        "coverage": covered if has_interval else None,
        "false_pass": false_pass,
        "false_fail": false_fail,
        "unstable_detected": result.status == "UNSTABLE",
        "contamination_detected": result.status == "CONTAMINATED",
        "early_stop": early_stop,
        "premature_terminal": premature_terminal,
        "budget_exhausted": budget_exhausted,
        "lag1_autocorrelation": result.lag1_autocorrelation,
        "drift_ratio": result.drift_ratio,
        "ci_width": result.ci_high - result.ci_low if has_interval else None,
        "precision_ratio": result.precision_ratio,
        "failure_reasons": list(result.failure_reasons),
        "result_artifact_hash": result.artifact_hash,
        "raw_observation_hash": result.raw_observation_hash,
    }


def _summarize_trials(
    trials: list[dict[str, object]],
    *,
    nominal_alpha: float = META_NOMINAL_ALPHA,
) -> dict[str, object]:
    if not trials:
        raise ValueError("calibration cell has no trials")
    consumed = [int(trial["observations_consumed"]) for trial in trials]
    eligible = [trial for trial in trials if trial["coverage_eligible"]]
    covered = sum(bool(trial["coverage"]) for trial in eligible)
    false_pass_count = sum(bool(trial["false_pass"]) for trial in trials)
    false_fail_count = sum(bool(trial["false_fail"]) for trial in trials)
    correct_decision_count = sum(bool(trial["correct_decision"]) for trial in trials)
    wrong_decision_count = sum(bool(trial["wrong_decision"]) for trial in trials)
    inconclusive_count = sum(bool(trial["inconclusive_decision"]) for trial in trials)
    decision_resolution_count = correct_decision_count + wrong_decision_count
    confidence_bounds = {
        "coverage": _rate_metric(
            covered,
            len(eligible),
            threshold=0.90,
            relation="minimum",
            tail="one_sided_lower",
            nominal_alpha=nominal_alpha,
        ),
        "false_pass": _rate_metric(
            false_pass_count,
            len(trials),
            threshold=0.05,
            relation="maximum",
            tail="one_sided_upper",
            nominal_alpha=nominal_alpha,
        ),
        "false_fail": _rate_metric(
            false_fail_count,
            len(trials),
            threshold=0.10,
            relation="maximum",
            tail="one_sided_upper",
            nominal_alpha=nominal_alpha,
        ),
        "decision_resolution": _rate_metric(
            decision_resolution_count,
            len(trials),
            threshold=MIN_DECISION_RESOLUTION,
            relation="minimum",
            tail="one_sided_lower",
            nominal_alpha=nominal_alpha,
        ),
    }
    return {
        "replicates": len(trials),
        "status_counts": {
            status: sum(trial["final_status"] == status for trial in trials)
            for status in ("PASS", "FAIL", "CONTINUE", "UNSTABLE", "CONTAMINATED", "INSUFFICIENT_EVIDENCE")
            if any(trial["final_status"] == status for trial in trials)
        },
        "empirical_ci_coverage": covered / len(eligible) if eligible else None,
        "coverage_success_count": covered,
        "coverage_trial_count": len(eligible),
        "coverage_denominator": len(eligible),
        "false_pass_count": false_pass_count,
        "false_pass_trial_count": len(trials),
        "false_pass_rate": false_pass_count / len(trials),
        "false_fail_count": false_fail_count,
        "false_fail_trial_count": len(trials),
        "false_fail_rate": false_fail_count / len(trials),
        "correct_decision_count": correct_decision_count,
        "correct_decision_rate": correct_decision_count / len(trials),
        "wrong_decision_count": wrong_decision_count,
        "wrong_decision_rate": wrong_decision_count / len(trials),
        "inconclusive_count": inconclusive_count,
        "inconclusive_rate": inconclusive_count / len(trials),
        "decision_resolution_count": decision_resolution_count,
        "decision_resolution_rate": decision_resolution_count / len(trials),
        "confidence_bounds": confidence_bounds,
        "early_stop_frequency": sum(bool(trial["early_stop"]) for trial in trials) / len(trials),
        "premature_terminal_frequency": sum(bool(trial["premature_terminal"]) for trial in trials) / len(trials),
        "average_observations_consumed": sum(consumed) / len(consumed),
        "p95_observations_consumed": _percentile(consumed, 0.95),
        "unstable_detection_rate": sum(bool(trial["unstable_detected"]) for trial in trials) / len(trials),
        "contamination_detection_rate": sum(bool(trial["contamination_detected"]) for trial in trials) / len(trials),
        "budget_exhaustion_rate": sum(bool(trial["budget_exhausted"]) for trial in trials) / len(trials),
    }


def _cell_criteria(summary: dict[str, object], minimum_replicates: int) -> list[str]:
    failures: list[str] = []
    if int(summary["replicates"]) < minimum_replicates:
        failures.append("replicate_count_below_preregistered_minimum")
    confidence_bounds = cast(dict[str, dict[str, object]], summary["confidence_bounds"])
    coverage = summary["empirical_ci_coverage"]
    coverage_metric = confidence_bounds["coverage"]
    if coverage is None or int(summary["coverage_trial_count"]) < minimum_replicates:
        failures.append("coverage_evidence_insufficient")
    elif coverage_metric["decision"] != "VALIDATED":
        failures.append(f"coverage_meta_{str(coverage_metric['decision']).lower()}")
    false_pass_metric = confidence_bounds["false_pass"]
    if false_pass_metric["decision"] != "VALIDATED":
        failures.append(f"false_pass_meta_{str(false_pass_metric['decision']).lower()}")
    false_fail_metric = confidence_bounds["false_fail"]
    if false_fail_metric["decision"] != "VALIDATED":
        failures.append(f"false_fail_meta_{str(false_fail_metric['decision']).lower()}")
    resolution_count = int(summary["decision_resolution_count"])
    if resolution_count == 0:
        failures.append("always_inconclusive_protocol")
    resolution_metric = confidence_bounds["decision_resolution"]
    if resolution_metric["decision"] != "VALIDATED":
        failures.append(f"decision_resolution_{str(resolution_metric['decision']).lower()}")
    unstable_count = int(cast(dict[str, int], summary["status_counts"]).get("UNSTABLE", 0))
    if unstable_count:
        failures.append("unexpected_unstable_status")
    if float(summary["premature_terminal_frequency"]) > 0.0:
        failures.append("terminal_status_before_minimum_observation_floor")
    return failures


def _cell_meta_decidable(summaries: dict[str, object]) -> bool:
    """Return true only when every scenario's preregistered bounds resolve."""

    for scenario in SCENARIOS:
        summary = summaries.get(scenario)
        if not isinstance(summary, dict):
            return False
        confidence_bounds = summary.get("confidence_bounds")
        if not isinstance(confidence_bounds, dict):
            return False
        for metric_name in ("coverage", "false_pass", "false_fail", "decision_resolution"):
            metric = confidence_bounds.get(metric_name)
            if not isinstance(metric, dict) or metric.get("decision") == "INCONCLUSIVE":
                return False
    return True


def run_calibration(
    *,
    replicates: int = DEFAULT_REPLICATES,
    max_replicates: int | None = None,
    replicate_batch: int | None = None,
    campaign_seed: int = DEFAULT_SEED,
    families: tuple[str, ...] | None = None,
    variants: tuple[str, ...] | None = None,
    checkpoint_policies: tuple[str, ...] = CHECKPOINT_POLICIES,
) -> dict[str, object]:
    """Execute one deterministic campaign and return a hash-bound artifact."""

    if type(replicates) is not int or not 1 <= replicates <= 5_000:
        raise ValueError("calibration replicates must be an integer in [1, 5000]")
    resolved_max_replicates = DEFAULT_MAX_REPLICATES if max_replicates is None else max_replicates
    resolved_replicate_batch = DEFAULT_REPLICATE_BATCH if replicate_batch is None else replicate_batch
    if type(resolved_max_replicates) is not int or not 1 <= resolved_max_replicates <= 5_000:
        raise ValueError("calibration max_replicates must be an integer in [1, 5000]")
    if resolved_max_replicates < replicates:
        raise ValueError("calibration max_replicates must be >= replicates")
    if type(resolved_replicate_batch) is not int or not 1 <= resolved_replicate_batch <= 5_000:
        raise ValueError("calibration replicate_batch must be an integer in [1, 5000]")
    if resolved_replicate_batch > resolved_max_replicates:
        raise ValueError("calibration replicate_batch must be <= max_replicates")
    if type(campaign_seed) is not int or campaign_seed < 0:
        raise ValueError("calibration campaign seed must be a non-negative integer")
    selected_family_names = tuple(families or tuple(FAMILY_BY_NAME))
    selected_variant_ids = tuple(variants or tuple(VARIANT_BY_ID))
    if not selected_family_names or any(name not in FAMILY_BY_NAME for name in selected_family_names):
        raise ValueError("calibration families must be known and non-empty")
    if len(set(selected_family_names)) != len(selected_family_names):
        raise ValueError("calibration families must be unique")
    if not selected_variant_ids or any(identifier not in VARIANT_BY_ID for identifier in selected_variant_ids):
        raise ValueError("calibration variants must be known and non-empty")
    if len(set(selected_variant_ids)) != len(selected_variant_ids):
        raise ValueError("calibration variants must be unique")
    if not checkpoint_policies or any(policy not in CHECKPOINT_POLICIES for policy in checkpoint_policies):
        raise ValueError("calibration checkpoint policies are invalid")
    if len(set(checkpoint_policies)) != len(checkpoint_policies):
        raise ValueError("calibration checkpoint policies must be unique")
    look_points = _finite_look_schedule(replicates, resolved_max_replicates, resolved_replicate_batch)
    alpha_allocation = _alpha_allocation(look_points)
    configuration_cell_count = len(selected_family_names) * len(selected_variant_ids) * len(checkpoint_policies)

    source = _source_metadata()
    protocol_payload = {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "campaign_seed": campaign_seed,
        "families": [FAMILY_BY_NAME[name].__dict__ for name in selected_family_names],
        "scenarios": SCENARIOS,
        "variants": [VARIANT_BY_ID[identifier].__dict__ for identifier in selected_variant_ids],
        "checkpoint_policies": checkpoint_policies,
        "replicates": replicates,
        "maximum_replicates": resolved_max_replicates,
        "replicate_batch": resolved_replicate_batch,
        "look_points": look_points,
        "meta_nominal_alpha": META_NOMINAL_ALPHA,
        "alpha_allocation": alpha_allocation,
        "min_decision_resolution": MIN_DECISION_RESOLUTION,
        "criteria": {
            "minimum_replicates_per_cell": replicates,
            "maximum_replicates_per_cell": resolved_max_replicates,
            "replicate_batch": resolved_replicate_batch,
            "meta_nominal_alpha": META_NOMINAL_ALPHA,
            "error_control_scope": alpha_allocation["error_control_scope"],
            "family_size": alpha_allocation["family_size"],
            "family_unit": alpha_allocation["family_unit"],
            "configuration_cell_count": configuration_cell_count,
            "look_count": alpha_allocation["look_count"],
            "metric_count": alpha_allocation["metric_count"],
            "scenario_count": alpha_allocation["scenario_count"],
            "alpha_total": alpha_allocation["alpha_total"],
            "alpha_allocation_method": alpha_allocation["alpha_allocation_method"],
            "per_bound_alpha": alpha_allocation["per_bound_alpha"],
            "min_decision_resolution": MIN_DECISION_RESOLUTION,
            "coverage_interval": [0.90, 1.0],
            "max_false_pass_rate": 0.05,
            "max_false_fail_rate": 0.10,
        },
    }
    protocol_hash = _hash(protocol_payload)
    cells: list[dict[str, object]] = []
    domain_cell_failures: dict[str, list[str]] = {name: [] for name in selected_family_names}
    all_trials: list[dict[str, object]] = []
    for family_index, family_name in enumerate(selected_family_names):
        family = FAMILY_BY_NAME[family_name]
        for variant_index, variant_id in enumerate(selected_variant_ids):
            variant = VARIANT_BY_ID[variant_id]
            for policy_index, policy in enumerate(checkpoint_policies):
                scenario_trials: dict[str, list[dict[str, object]]] = {scenario: [] for scenario in SCENARIOS}
                scenario_summaries: dict[str, object] = {}
                allocated = 0
                allocation_batches: list[int] = []
                allocation_stop_reason = ""
                for look_index, target in enumerate(look_points):
                    for scenario_index, scenario in enumerate(SCENARIOS):
                        scenario_trials[scenario].extend(
                            _run_trial(
                                family,
                                scenario,
                                variant,
                                policy,
                                _trial_seed(
                                    campaign_seed,
                                    family_index,
                                    scenario_index,
                                    variant_index,
                                    policy_index,
                                    replicate,
                                ),
                            )
                            for replicate in range(allocated, target)
                        )
                    allocated = target
                    allocation_batches.append(allocated)
                    scenario_summaries = {
                        scenario: _summarize_trials(
                            trials,
                            nominal_alpha=float(alpha_allocation["per_bound_alpha"]),
                        )
                        for scenario, trials in scenario_trials.items()
                    }
                    if _cell_meta_decidable(scenario_summaries):
                        allocation_stop_reason = "META_BOUNDS_DECIDABLE"
                        break
                    if look_index == len(look_points) - 1:
                        allocation_stop_reason = "MAXIMUM_REPLICATES_REACHED"
                        break
                if not allocation_stop_reason:  # pragma: no cover - schedule always includes maximum.
                    raise RuntimeError("calibration finite look schedule did not terminate")
                for scenario in SCENARIOS:
                    all_trials.extend(
                        {
                            "family": family_name,
                            "variant": variant_id,
                            "checkpoint_policy": policy,
                            **trial,
                        }
                        for trial in scenario_trials[scenario]
                    )
                cell = {
                    "family": family_name,
                    "domain_declaration": family.domain,
                    "variant": variant_id,
                    "checkpoint_policy": policy,
                    "replicates_allocated": allocated,
                    "allocation_batches": allocation_batches,
                    "preregistered_look_points": list(look_points),
                    "look_count": len(look_points),
                    "per_bound_alpha": alpha_allocation["per_bound_alpha"],
                    "allocation_stop_reason": allocation_stop_reason,
                    "scenarios": scenario_summaries,
                }
                cells.append(cell)
                if family.domain == "CANDIDATE_IN_DOMAIN":
                    domain_cell_failures[family_name].extend(
                        f"{variant_id}/{policy}/{scenario}:{failure}"
                        for scenario, summary in scenario_summaries.items()
                        for failure in _cell_criteria(cast(dict[str, object], summary), replicates)
                    )

    domain_assessments: dict[str, dict[str, object]] = {}
    for family_name in selected_family_names:
        family = FAMILY_BY_NAME[family_name]
        failures = domain_cell_failures[family_name]
        if family.domain == "OUT_OF_DOMAIN":
            status = "OUT_OF_DOMAIN"
        elif failures:
            status = "INSUFFICIENT_EVIDENCE"
        else:
            status = "VALIDATED_DOMAIN"
        domain_assessments[family_name] = {
            "declaration": family.domain,
            "assessment": status,
            "criteria_failures": failures,
            "notes": family.notes,
        }

    contamination_trials = [trial for trial in all_trials if FAMILY_BY_NAME[str(trial["family"])].contaminated_expected]
    unstable_trials = [trial for trial in all_trials if FAMILY_BY_NAME[str(trial["family"])].unstable_expected]
    candidate_families = [name for name in selected_family_names if FAMILY_BY_NAME[name].domain == "CANDIDATE_IN_DOMAIN"]
    overall_status = (
        "LOCALLY_CALIBRATED_WITHIN_DECLARED_DOMAIN"
        if candidate_families
        and all(domain_assessments[name]["assessment"] == "VALIDATED_DOMAIN" for name in candidate_families)
        else "INSUFFICIENT_EVIDENCE"
    )
    allocated_values = [int(cell["replicates_allocated"]) for cell in cells]
    stop_reason_counts = {
        reason: sum(cell["allocation_stop_reason"] == reason for cell in cells)
        for reason in sorted({str(cell["allocation_stop_reason"]) for cell in cells})
    }
    artifact_without_hash: dict[str, object] = {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "protocol_hash": protocol_hash,
        "source": source,
        "validator_id": VALIDATOR_ID,
        "validator_hash": _hash({"validator_id": VALIDATOR_ID, "generator_version": GENERATOR_VERSION}),
        "campaign": {
            "seed": campaign_seed,
            "replicates_per_cell": replicates,
            "minimum_replicates_per_cell": replicates,
            "maximum_replicates_per_cell": resolved_max_replicates,
            "replicate_batch": resolved_replicate_batch,
            "allocation_policy": "ADAPTIVE_BOUNDS_UNTIL_DECIDABLE",
            "look_points": list(look_points),
            "configuration_cell_count": configuration_cell_count,
            "scenario_cell_count": configuration_cell_count * len(SCENARIOS),
            "families": selected_family_names,
            "scenarios": SCENARIOS,
            "variants": selected_variant_ids,
            "checkpoint_policies": checkpoint_policies,
        },
        "meta_calibration": {
            "method_id": "wilson_score_v1",
            "nominal_alpha": float(alpha_allocation["per_bound_alpha"]),
            "interval_type": "one_sided",
            "decision_labels": ["VALIDATED", "INVALIDATED", "INCONCLUSIVE"],
            "scenario_expected_decision": {"improvement": "PASS", "null": "NOT_PASS", "regression": "NOT_PASS"},
            "error_control_scope": alpha_allocation["error_control_scope"],
            "family_size": alpha_allocation["family_size"],
            "family_unit": "configuration_cells",
            "look_count": alpha_allocation["look_count"],
            "metric_count": alpha_allocation["metric_count"],
            "scenario_count": alpha_allocation["scenario_count"],
            "alpha_total": alpha_allocation["alpha_total"],
            "alpha_allocation_method": alpha_allocation["alpha_allocation_method"],
            "look_alpha_allocations": alpha_allocation["look_alpha_allocations"],
            "per_bound_alpha": alpha_allocation["per_bound_alpha"],
            "preregistered_look_points": list(look_points),
            "metrics": {
                "coverage": {"threshold": 0.90, "relation": "minimum"},
                "false_pass": {"threshold": 0.05, "relation": "maximum"},
                "false_fail": {"threshold": 0.10, "relation": "maximum"},
                "decision_resolution": {"threshold": MIN_DECISION_RESOLUTION, "relation": "minimum"},
            },
            "minimum_replicates_per_cell": replicates,
            "maximum_replicates_per_cell": resolved_max_replicates,
            "replicate_batch": resolved_replicate_batch,
        },
        "adaptive_allocation_summary": {
            "cell_count": len(cells),
            "minimum_allocated_replicates": min(allocated_values),
            "maximum_allocated_replicates": max(allocated_values),
            "average_allocated_replicates": sum(allocated_values) / len(allocated_values),
            "stop_reason_counts": stop_reason_counts,
        },
        "criteria": protocol_payload["criteria"],
        "domain_assessments": domain_assessments,
        "cells": cells,
        "raw_trials_retained": "DIGESTS_AND_REPRODUCIBLE_SEEDS",
        "raw_trials": all_trials,
        "contamination_summary": {
            "contaminated_trial_count": len(contamination_trials),
            "detected_count": sum(bool(trial["contamination_detected"]) for trial in contamination_trials),
            "detection_rate": (
                sum(bool(trial["contamination_detected"]) for trial in contamination_trials) / len(contamination_trials)
                if contamination_trials
                else None
            ),
            "latent_outliers_are_not_automatically_detected": True,
        },
        "unstable_summary": {
            "unstable_trial_count": len(unstable_trials),
            "detected_count": sum(bool(trial["unstable_detected"]) for trial in unstable_trials),
            "detection_rate": (
                sum(bool(trial["unstable_detected"]) for trial in unstable_trials) / len(unstable_trials)
                if unstable_trials
                else None
            ),
        },
        "mode": "SHADOW",
        "legacy_full_suite_authority": "RETAINED",
        "selection_authority": "DISABLED",
        "claimable_as_observed": False,
        "promotion_status": "DISABLED_IN_SHADOW",
        "status": overall_status,
        "limitations": [
            "Synthetic data-generating processes are not production or hardware measurements.",
            "Coverage is empirical for the declared finite campaign and cannot establish universal validity.",
            "OUT_OF_DOMAIN and INSUFFICIENT_EVIDENCE results must not be reclassified as PASS by threshold tuning.",
            "The harness retains hashes and deterministic seeds; raw values are regenerated, not treated as external observations.",
        ],
    }
    artifact = {**artifact_without_hash, "artifact_hash": _hash(artifact_without_hash)}
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    parser.add_argument("--max-replicates", type=int, default=DEFAULT_MAX_REPLICATES)
    parser.add_argument("--replicate-batch", type=int, default=DEFAULT_REPLICATE_BATCH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--families", nargs="+", choices=tuple(FAMILY_BY_NAME))
    parser.add_argument("--variants", nargs="+", choices=tuple(VARIANT_BY_ID))
    parser.add_argument("--checkpoint-policies", nargs="+", choices=CHECKPOINT_POLICIES, default=CHECKPOINT_POLICIES)
    parser.add_argument("--require-calibrated", action="store_true")
    args = parser.parse_args()
    artifact = run_calibration(
        replicates=args.replicates,
        max_replicates=args.max_replicates,
        replicate_batch=args.replicate_batch,
        campaign_seed=args.seed,
        families=tuple(args.families) if args.families else None,
        variants=tuple(args.variants) if args.variants else None,
        checkpoint_policies=tuple(args.checkpoint_policies),
    )
    payload = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if not args.require_calibrated or artifact["status"] == "LOCALLY_CALIBRATED_WITHIN_DECLARED_DOMAIN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
