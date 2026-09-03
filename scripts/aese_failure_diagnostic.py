"""Produce a bounded, failure-focused AESE calibration diagnostic.

The diagnostic replays only retained deterministic seeds for currently failed
in-domain scenario-cells.  It emits aggregate distributions and failure
classes, never a second campaign artifact or a calibration PASS claim.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Final, cast

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_INPUT: Final[Path] = ROOT / "artifacts" / "evidence" / "aese-statistical-calibration.json"
DEFAULT_OUTPUT: Final[Path] = ROOT / "artifacts" / "evidence" / "aese-failure-diagnostic.json"
SCHEMA: Final[str] = "aese-failure-diagnostic-v1"
GENERATOR_VERSION: Final[str] = "aese-failure-diagnostic-generator-v1"
FAILURE_CLASSES: Final[tuple[str, ...]] = (
    "STATISTICAL_METHOD",
    "STABILITY_DETECTOR",
    "BLOCK_SIZE",
    "PRECISION_REQUIREMENT",
    "EFFECT_SIZE",
    "BASELINE_SEMANTICS",
    "PROTOCOL_BUDGET",
    "TRUE_WORKLOAD_PROPERTY",
    "GENERATOR_ARTIFACT",
    "IMPLEMENTATION_BUG",
    "UNKNOWN",
)


def _hash(value: object) -> str:
    import hashlib

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "NOT_AVAILABLE"


def _distribution(values: list[float | int | None]) -> dict[str, object]:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return {"count": 0, "null_count": len(values), "min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(finite)

    def percentile(fraction: float) -> float:
        rank = max(1, math.ceil(fraction * len(ordered)))
        return ordered[rank - 1]

    return {
        "count": len(ordered),
        "null_count": len(values) - len(ordered),
        "min": ordered[0],
        "median": percentile(0.50),
        "p95": percentile(0.95),
        "max": ordered[-1],
    }


def _failure_class(reason: str) -> str:
    if "autocorrelation" in reason or "drift" in reason:
        return "STABILITY_DETECTOR"
    if "precision" in reason:
        return "PRECISION_REQUIREMENT"
    if "baseline" in reason:
        return "BASELINE_SEMANTICS"
    if "block" in reason:
        return "BLOCK_SIZE"
    if "observation" in reason or "budget" in reason or "floor" in reason:
        return "PROTOCOL_BUDGET"
    if "contamin" in reason or "outlier" in reason:
        return "TRUE_WORKLOAD_PROPERTY"
    if "hash" in reason or "generated" in reason:
        return "GENERATOR_ARTIFACT"
    if "invalid" in reason or "exception" in reason:
        return "IMPLEMENTATION_BUG"
    if "estimand" in reason or "statistical" in reason:
        return "STATISTICAL_METHOD"
    return "UNKNOWN"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def _diagnose_cell(
    *,
    family: str,
    variant: str,
    checkpoint_policy: str,
    scenario: str,
    seeds: list[int],
) -> dict[str, object]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.aese_statistical_calibration import FAMILY_BY_NAME, VARIANT_BY_ID, _run_trial

    trials = [
        _run_trial(
            FAMILY_BY_NAME[family],
            scenario,
            VARIANT_BY_ID[variant],
            checkpoint_policy,
            seed,
        )
        for seed in seeds
    ]
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {failure_class: 0 for failure_class in FAILURE_CLASSES}
    for trial in trials:
        status = str(trial["final_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        for reason in cast(list[str], trial["failure_reasons"]):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            class_counts[_failure_class(reason)] += 1
    ordered_reasons = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    resolution_count = sum(bool(trial["correct_decision"]) or bool(trial["wrong_decision"]) for trial in trials)
    wrong_count = sum(bool(trial["wrong_decision"]) for trial in trials)
    inconclusive_count = sum(bool(trial["inconclusive_decision"]) for trial in trials)
    return {
        "family": family,
        "variant": variant,
        "checkpoint_policy": checkpoint_policy,
        "scenario": scenario,
        "replicate_count": len(trials),
        "final_status_distribution": dict(sorted(status_counts.items())),
        "lag1_distribution": _distribution([cast(float | int | None, trial["lag1_autocorrelation"]) for trial in trials]),
        "drift_distribution": _distribution([cast(float | int | None, trial["drift_ratio"]) for trial in trials]),
        "ci_width_distribution": _distribution([cast(float | int | None, trial["ci_width"]) for trial in trials]),
        "precision_ratio_distribution": _distribution(
            [cast(float | int | None, trial["precision_ratio"]) for trial in trials]
        ),
        "decision_resolution": {
            "resolved_count": resolution_count,
            "resolved_rate": resolution_count / len(trials),
            "wrong_count": wrong_count,
            "wrong_rate": wrong_count / len(trials),
            "inconclusive_count": inconclusive_count,
            "inconclusive_rate": inconclusive_count / len(trials),
        },
        "observations_consumed": _distribution([int(trial["observations_consumed"]) for trial in trials]),
        "failure_reason_counts": dict(ordered_reasons),
        "failure_class_counts": {key: value for key, value in class_counts.items() if value},
        "primary_failure_reason": ordered_reasons[0][0] if ordered_reasons else "NONE_RECORDED",
        "secondary_failure_reason": ordered_reasons[1][0] if len(ordered_reasons) > 1 else None,
        "primary_failure_class": _failure_class(ordered_reasons[0][0]) if ordered_reasons else "UNKNOWN",
        "seed_digest": _hash(seeds),
    }


def build_diagnostic(input_path: Path = DEFAULT_INPUT) -> dict[str, object]:
    source_artifact = _load(input_path)
    raw_trials_value = source_artifact.get("raw_trials")
    cells_value = source_artifact.get("cells")
    assessments_value = source_artifact.get("domain_assessments")
    if not isinstance(raw_trials_value, list) or not isinstance(cells_value, list) or not isinstance(assessments_value, dict):
        raise ValueError("calibration artifact lacks diagnostic inputs")
    raw_trials = [cast(dict[str, object], value) for value in raw_trials_value if isinstance(value, dict)]
    assessments = cast(dict[str, dict[str, object]], assessments_value)
    seeds_by_key: dict[tuple[str, str, str, str], list[int]] = {}
    for trial in raw_trials:
        key = (str(trial["family"]), str(trial["variant"]), str(trial["checkpoint_policy"]), str(trial["scenario"]))
        seeds_by_key.setdefault(key, []).append(int(trial["seed"]))
    diagnostics: list[dict[str, object]] = []
    for cell_value in cells_value:
        cell = cast(dict[str, object], cell_value)
        family = str(cell["family"])
        if str(assessments.get(family, {}).get("assessment")) != "INSUFFICIENT_EVIDENCE":
            continue
        variant = str(cell["variant"])
        checkpoint_policy = str(cell["checkpoint_policy"])
        for scenario in ("null", "improvement", "regression"):
            key = (family, variant, checkpoint_policy, scenario)
            seeds = seeds_by_key.get(key, [])
            if not seeds:
                raise ValueError(f"no retained seeds for failed scenario-cell: {key}")
            diagnostics.append(
                _diagnose_cell(
                    family=family,
                    variant=variant,
                    checkpoint_policy=checkpoint_policy,
                    scenario=scenario,
                    seeds=seeds,
                )
            )
    class_totals: dict[str, int] = {failure_class: 0 for failure_class in FAILURE_CLASSES}
    for diagnostic in diagnostics:
        for failure_class, count in cast(dict[str, int], diagnostic["failure_class_counts"]).items():
            class_totals[failure_class] += count
    source = cast(dict[str, object], source_artifact.get("source", {}))
    artifact_without_hash: dict[str, object] = {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "input_artifact_path": input_path.relative_to(ROOT).as_posix() if input_path.is_relative_to(ROOT) else str(input_path),
        "input_artifact_hash": source_artifact.get("artifact_hash"),
        "artifact_source_sha": source.get("source_sha"),
        "relevant_subject_digest": _hash([(item["family"], item["variant"], item["checkpoint_policy"], item["scenario"]) for item in diagnostics]),
        "current_head": _git_head(),
        "protocol_hash": source_artifact.get("protocol_hash"),
        "validator_hash": source_artifact.get("validator_hash"),
        "environment_hash": source.get("environment_hash"),
        "reuse_status": "RECOMPUTED_FROM_RETAINED_SEEDS",
        "scope": "FAILED_IN_DOMAIN_SCENARIO_CELLS_ONLY",
        "diagnostic_cell_count": len(diagnostics),
        "failure_class_totals": {key: value for key, value in class_totals.items() if value},
        "diagnostics": diagnostics,
        "limitations": [
            "This is a bounded diagnostic replay, not a new calibration campaign.",
            "Input measurements are synthetic and finite; they do not establish production representativeness.",
            "The input artifact predates S1.1a finite-look alpha control; this diagnostic does not retroactively promote it.",
        ],
    }
    return {**artifact_without_hash, "artifact_hash": _hash(artifact_without_hash)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    artifact = build_diagnostic(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"artifact_hash": artifact["artifact_hash"], "diagnostic_cell_count": artifact["diagnostic_cell_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
