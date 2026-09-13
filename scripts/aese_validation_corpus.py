"""Build the held-out AESE validation corpus and conservative cost ledger.

Corpus evaluation is shadow-only.  The planner is never allowed to control
the legacy runner, and a cost claim is withheld until paired legacy and
selected-evidence timings are supplied for the same workload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Final, cast

if __package__:
    from scripts import aese_shadow_planner as _planner
else:  # Direct ``python scripts/aese_validation_corpus.py`` from repository root.
    import aese_shadow_planner as _planner

ROOT = _planner.ROOT
DEFAULT_CORPUS_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_validation_corpus.json"
DEFAULT_COST_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_cost_measurement.json"
SCHEMA: Final[str] = "aese-validation-corpus-v1"
COST_SCHEMA: Final[str] = "aese-cost-measurement-v1"
LABELS: Final[frozenset[str]] = frozenset({"KNOWN_GOOD", "KNOWN_BAD", "OOD", "AMBIGUOUS"})

DEVELOPMENT_CASES: Final[tuple[dict[str, object], ...]] = (
    {
        "case_id": "DEV-GT96-GOOD",
        "changed_paths": ["core/rust/src/gt96.rs"],
        "label": "KNOWN_GOOD",
        "critical": False,
        "target_paths": ["core/rust/src/gt96.rs"],
        "mutation": "NONE",
    },
    {
        "case_id": "DEV-UNKNOWN-BAD",
        "changed_paths": ["plugins/unknown_dynamic_loader.py"],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["core/rust/src/gt96.rs"],
        "mutation": "UNKNOWN_DEPENDENCY_MUTATION",
    },
    {
        "case_id": "DEV-CONFIG-BAD",
        "changed_paths": ["pyproject.toml"],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["tests/test_aese_primitives.py"],
        "mutation": "CONFIG_MUTATION",
    },
    {
        "case_id": "DEV-OOD-PLUGIN",
        "changed_paths": ["plugins/never_seen.py"],
        "label": "OOD",
        "critical": False,
        "target_paths": [],
        "mutation": "DYNAMIC_PLUGIN_EDGE",
    },
)

FINAL_CASES: Final[tuple[dict[str, object], ...]] = (
    {
        "case_id": "VAL-REPLAY-BAD",
        "changed_paths": ["core/rust/src/replay.rs"],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["core/rust/src/replay.rs"],
        "mutation": "SERIALIZATION_SCHEMA_MUTATION",
    },
    {
        "case_id": "VAL-FFI-BAD",
        "changed_paths": ["aegis_cognition/aese.py"],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["core/rust/src/ffi/eac.rs"],
        "mutation": "CROSS_LANGUAGE_FFI_FIELD_MUTATION",
    },
    {
        "case_id": "VAL-WORKFLOW-BAD",
        "changed_paths": [".github/workflows/ci.yml"],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["core/rust/src/gt96.rs"],
        "mutation": "WORKFLOW_MATRIX_MUTATION",
    },
    {
        "case_id": "VAL-AMBIGUOUS-DYNAMIC",
        "changed_paths": ["scripts/e2e_release_gate.py"],
        "label": "AMBIGUOUS",
        "critical": False,
        "target_paths": [],
        "mutation": "DYNAMIC_IMPORT_AMBIGUITY",
    },
    {
        "case_id": "VAL-KNOWN-UNMAPPED-BAD",
        "changed_paths": [],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["core/rust/src/gt96.rs"],
        "mutation": "KNOWN_UNMAPPED_SURFACE_MUTATION",
    },
    {
        "case_id": "VAL-PARTIAL-MAPPING-BAD",
        "changed_paths": [],
        "label": "KNOWN_BAD",
        "critical": True,
        "target_paths": ["core/rust/src/gt96.rs"],
        "mutation": "PARTIAL_MAPPING_MUTATION",
    },
)


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _declared_runtime_contract() -> dict[str, str]:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project")
    requires_python = project.get("requires-python") if isinstance(project, dict) else None
    if not isinstance(requires_python, str) or not requires_python.strip():
        raise ValueError("pyproject.toml must declare project.requires-python")
    return {"requires_python": requires_python.strip()}


def _artifact_provenance(subject: object) -> dict[str, object]:
    source_sha = str(_planner._inventory.build_inventory()["source_head"])
    # Reproducibility is keyed to the declared runtime contract, not to the
    # host or interpreter version that generated the artifact. OS/kernel
    # details, interpreter versions, and absolute paths remain useful
    # observations but would make an identical artifact from Windows, Linux,
    # macOS, and supported Python versions unverifiable.
    environment = _declared_runtime_contract()
    host_observation = {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "executable": sys.executable,
    }
    return {
        "artifact_source_sha": source_sha,
        "current_head": source_sha,
        "relevant_subject_digest": _stable_hash(subject),
        "protocol_hash": "NOT_APPLICABLE",
        "validator_hash": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "environment": environment,
        "environment_hash": _stable_hash(environment),
        "host_observation": host_observation,
        "reuse_status": "NOT_REUSED",
    }


def _inventory_path_index() -> dict[str, list[str]]:
    inventory = json.loads((_planner.ROOT / "quality" / "registry" / "current_inventory.json").read_text(encoding="utf-8"))
    by_path: dict[str, list[str]] = {}
    for raw_item in inventory.get("items", []):
        if isinstance(raw_item, dict) and raw_item.get("stable_id") and raw_item.get("path"):
            by_path.setdefault(str(raw_item["path"]).replace("\\", "/"), []).append(str(raw_item["stable_id"]))
    for values in by_path.values():
        values.sort()
    return by_path


def _unknown_path() -> str:
    mapping = json.loads((_planner.ROOT / "quality" / "registry" / "current_s2_mapping.json").read_text(encoding="utf-8"))
    unknown_ids = {str(value) for value in mapping.get("unknown_surface_ids", [])}
    for path, surface_ids in sorted(_inventory_path_index().items()):
        if unknown_ids.intersection(surface_ids):
            return path
    raise ValueError("validation corpus requires at least one current unmapped inventory path")


def _partial_mapping_fixture(directory: Path) -> tuple[Path, str]:
    mapping_path = _planner.ROOT / "quality" / "registry" / "current_s2_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    by_path = _inventory_path_index()
    path, surface_ids = next((item for item in sorted(by_path.items()) if len(item[1]) >= 2), ("", []))
    if not path:
        raise ValueError("validation corpus requires a multi-surface inventory path")
    unknown_ids = {str(value) for value in mapping.get("unknown_surface_ids", [])}
    mapped_surface_id = next((surface_id for surface_id in surface_ids if surface_id in unknown_ids), surface_ids[0])
    claims = mapping.get("records", [{}])[0].get("claim_ids", [])
    mapping.setdefault("records", []).append(
        {
            "surface_id": mapped_surface_id,
            "claim_ids": list(claims[:1]) or ["AESE-CLAIM-GT96-005"],
            "source_subjects": [f"{path}::partial_mapping_source"],
            "test_subjects": [f"{path}::partial_mapping_test"],
            "evidence_subjects": ["quality/registry/current_s2_mapping.json"],
            "risk": "LOW",
            "security_criticality": "LOW",
            "release_criticality": "LOW",
            "selection_relevant": False,
            "relationship_types": ["DIRECT_CONTRACT"],
            "unknown_dependency_policy": "WIDEN_CONSERVATIVELY",
            "rationale": "Deterministic test-only fixture to prove one mapped and one unmapped surface share a path.",
            "verification_status": "VERIFIED",
        }
    )
    mapping["unknown_surface_ids"] = sorted(unknown_ids - {mapped_surface_id})
    output = directory / "partial_mapping.json"
    output.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output, path


def _materialize_cases(cases: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    materialized: list[dict[str, object]] = []
    unknown_path = _unknown_path()
    partial_path = next((path for path, ids in sorted(_inventory_path_index().items()) if len(ids) >= 2), None)
    if partial_path is None:
        raise ValueError("validation corpus requires a multi-surface inventory path")
    for case in cases:
        if case["case_id"] == "VAL-KNOWN-UNMAPPED-BAD":
            materialized.append({**case, "changed_paths": [unknown_path]})
        elif case["case_id"] == "VAL-PARTIAL-MAPPING-BAD":
            materialized.append({**case, "changed_paths": [partial_path]})
        else:
            materialized.append(dict(case))
    return tuple(materialized)


def _evaluate_case(case: dict[str, object]) -> dict[str, object]:
    changed = cast(list[str], case["changed_paths"])
    if case["mutation"] == "PARTIAL_MAPPING_MUTATION":
        with tempfile.TemporaryDirectory(prefix="aese-partial-") as temporary:
            fixture, _ = _partial_mapping_fixture(Path(temporary))
            plan = _planner.build_shadow_plan(changed, mapping_path=fixture)
    else:
        plan = _planner.build_shadow_plan(changed)
    target_paths = set(cast(list[str], case["target_paths"]))
    reached = set(cast(list[str], plan["closure_paths"]))
    reached_target = bool(target_paths <= reached) if target_paths else None
    label = str(case["label"])
    return {
        **case,
        "plan_widened": plan["plan_widened"],
        "closure_status": plan["closure_status"],
        "structural_critical_reachability_status": plan["structural_critical_reachability_status"],
        "observed_critical_false_negative_status": plan["observed_critical_false_negative_status"],
        "path_dependency_states": plan["path_dependency_states"],
        "changed_unmapped_surface_paths": plan["changed_unmapped_surface_paths"],
        "target_paths_reached": sorted(target_paths & reached),
        "target_paths_missing": sorted(target_paths - reached),
        "planning_target_reached": reached_target if label == "KNOWN_BAD" else None,
        "decision_state_counts": {
            state: sum(1 for decision in cast(list[dict[str, object]], plan["decisions"]) if decision["state"] == state)
            for state in ("WOULD_RUN", "WOULD_REUSE", "WOULD_SKIP", "WIDENED_UNKNOWN")
        },
    }


def _validate_cases(cases: tuple[dict[str, object], ...], split: str) -> None:
    ids: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in ids:
            raise ValueError(f"duplicate or empty {split} case id")
        ids.add(case_id)
        if case.get("label") not in LABELS:
            raise ValueError(f"invalid {split} label for {case_id}")
        if not isinstance(case.get("changed_paths"), list) or not isinstance(case.get("target_paths"), list):
            raise ValueError(f"{split} paths must be lists for {case_id}")


def build_corpus() -> dict[str, object]:
    development_cases = _materialize_cases(DEVELOPMENT_CASES)
    final_cases = _materialize_cases(FINAL_CASES)
    _validate_cases(development_cases, "development")
    _validate_cases(final_cases, "final")
    development = [_evaluate_case(case) for case in development_cases]
    final = [_evaluate_case(case) for case in final_cases]
    if {str(case["case_id"]) for case in development_cases} & {str(case["case_id"]) for case in final_cases}:
        raise ValueError("development and final corpus IDs must be disjoint")
    final_known_bad = [case for case in final if case["label"] == "KNOWN_BAD"]
    final_critical = [case for case in final_known_bad if case["critical"] is True]
    critical_misses = [str(case["case_id"]) for case in final_critical if case["planning_target_reached"] is not True]
    noncritical = [case for case in final_known_bad if case["critical"] is not True]
    noncritical_misses = [str(case["case_id"]) for case in noncritical if case["planning_target_reached"] is not True]
    false_alarms = [
        str(case["case_id"])
        for case in final
        if case["label"] == "KNOWN_GOOD" and case["plan_widened"] is True
    ]
    corpus: dict[str, object] = {
        "schema": SCHEMA,
        "mode": "SHADOW",
        "status": "LOCAL_SHADOW_VALIDATION_ONLY" if not critical_misses else "INSUFFICIENT_EVIDENCE",
        "development_corpus": development,
        "final_validation_corpus": final,
        "split_policy": {
            "final_held_out": True,
            "planner_tuning_uses": "DEVELOPMENT_ONLY",
            "final_corpus_not_used_for_tuning": True,
            "ambiguous_not_forced_into_good_or_bad": True,
        },
        "metrics": {
            "synthetic_critical_planning_targets": len(final_critical),
            "synthetic_critical_targets_reached": len(final_critical) - len(critical_misses),
            "synthetic_critical_targets_missed": len(critical_misses),
            "synthetic_critical_miss_case_ids": critical_misses,
            "synthetic_noncritical_planning_targets": len(noncritical),
            "synthetic_noncritical_targets_reached": len(noncritical) - len(noncritical_misses),
            "synthetic_noncritical_targets_missed": len(noncritical_misses),
            "synthetic_noncritical_miss_case_ids": noncritical_misses,
            "false_alarm_case_ids": false_alarms,
            "widen_events": sum(1 for case in final if case["plan_widened"] is True),
            "ood_events": sum(1 for case in final if case["label"] == "OOD"),
        },
        "limitations": [
            "Corpus cases provide planning reachability evidence, not executed mutation-detection evidence.",
            "Critical planning-target tolerance is zero, while noncritical policy remains explicit and exploratory.",
            "A larger independently sourced corpus is required before any production non-inferiority claim.",
        ],
    }
    corpus["provenance"] = _artifact_provenance({"development": development, "final": final})
    corpus["artifact_hash"] = _stable_hash({key: value for key, value in corpus.items() if key != "artifact_hash"})
    return corpus


def validate_corpus(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    """Validate a retained corpus without rewriting historical provenance.

    The corpus decisions and validator/environment digests must remain stable,
    while ``artifact_source_sha`` and ``current_head`` intentionally retain the
    checkout in which the artifact was produced.  A later docs-only commit must
    therefore not make an otherwise valid historical measurement unverifiable.
    """

    if type(actual) is not dict or type(expected) is not dict:
        return ["validation corpora must be canonical dictionaries"]
    errors: list[str] = []
    if set(actual) != set(expected):
        errors.append("root schema keys differ")
    volatile = {"artifact_hash", "provenance"}
    errors.extend(
        f"{key} differs"
        for key in expected
        if key not in volatile and actual.get(key) != expected.get(key)
    )

    recorded_hash = actual.get("artifact_hash")
    if recorded_hash != _stable_hash({key: value for key, value in actual.items() if key != "artifact_hash"}):
        errors.append("artifact_hash is not self-consistent")

    actual_provenance = actual.get("provenance")
    expected_provenance = expected.get("provenance")
    if type(actual_provenance) is not dict or type(expected_provenance) is not dict:
        errors.append("provenance envelope is missing")
    else:
        required = {
            "artifact_source_sha",
            "current_head",
            "relevant_subject_digest",
            "protocol_hash",
            "validator_hash",
            "environment",
            "environment_hash",
            "host_observation",
            "reuse_status",
        }
        if not required.issubset(actual_provenance):
            errors.append("provenance envelope is incomplete")
        errors.extend(
            f"provenance.{key} differs"
            for key in ("relevant_subject_digest", "protocol_hash", "validator_hash", "environment_hash", "reuse_status")
            if actual_provenance.get(key) != expected_provenance.get(key)
        )
        environment = actual_provenance.get("environment")
        if type(environment) is not dict:
            errors.append("provenance.environment is not a runtime contract")
        elif actual_provenance.get("environment_hash") != _stable_hash(environment):
            errors.append("provenance.environment_hash is not self-consistent")
        if type(actual_provenance.get("host_observation")) is not dict:
            errors.append("provenance.host_observation is missing")
        for key in ("artifact_source_sha", "current_head"):
            value = actual_provenance.get(key)
            if not isinstance(value, str) or len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
                errors.append(f"provenance.{key} is not a commit SHA")

    return sorted(set(errors))


def build_cost_measurement(corpus: dict[str, object] | None = None) -> dict[str, object]:
    corpus = build_corpus() if corpus is None else corpus
    cases = cast(list[dict[str, object]], corpus["final_validation_corpus"])
    timings: list[float] = []
    for case in cases:
        start = time.perf_counter()
        _planner.build_shadow_plan(cast(list[str], case["changed_paths"]))
        timings.append(time.perf_counter() - start)
    planner_seconds = {
        "sample_count": len(timings),
        "samples": timings,
        "median": sorted(timings)[len(timings) // 2] if timings else None,
        "p95": sorted(timings)[max(0, int(len(timings) * 0.95) - 1)] if timings else None,
    }
    result: dict[str, object] = {
        "schema": COST_SCHEMA,
        "mode": "SHADOW",
        "status": "INSUFFICIENT_EVIDENCE",
        "planner_cost_seconds": planner_seconds,
        "legacy_wall_time_seconds": None,
        "selected_evidence_wall_time_seconds": None,
        "full_fallback_wall_time_seconds": None,
        "net_saving_seconds": None,
        "net_saving_formula": "legacy - (planner + selected_evidence)",
        "paired_workload": False,
        "reason": "legacy and selected evidence were not executed as a paired apples-to-apples workload while authority is shadow-only",
        "corpus_artifact_hash": corpus.get("artifact_hash"),
        "limitations": [
            "Planner timing alone cannot establish net savings.",
            "No 10x or scalability claim is made; median/p95 require paired measurements.",
        ],
    }
    result["provenance"] = _artifact_provenance({"corpus": corpus.get("artifact_hash"), "planner": planner_seconds})
    result["artifact_hash"] = _stable_hash({key: value for key, value in result.items() if key != "artifact_hash"})
    return result


def _load_retained_paired_cost(path: Path) -> dict[str, object] | None:
    """Keep a valid historical paired ledger when no new timings are supplied."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if type(raw) is not dict:
        return None
    if (
        raw.get("schema") != COST_SCHEMA
        or raw.get("status") != "MEASURED_EXPLORATORY_PAIRED"
        or raw.get("paired_workload") is not True
        or type(raw.get("sample_pair_count")) is not int
        or raw.get("sample_pair_count", 0) < 1
    ):
        return None
    recorded_hash = raw.get("artifact_hash")
    if recorded_hash != _stable_hash({key: value for key, value in raw.items() if key != "artifact_hash"}):
        return None
    return cast(dict[str, object], raw)


def _summary(samples: list[float]) -> dict[str, object]:
    ordered = sorted(samples)
    if not ordered:
        return {"sample_count": 0, "samples": [], "median": None, "p95": None, "best": None, "worst": None}
    result: dict[str, object] = {
        "sample_count": len(ordered),
        "samples": samples,
        "median": ordered[len(ordered) // 2],
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "best": min(ordered),
        "worst": max(ordered),
    }
    return result


def _saving_summary(samples: list[float]) -> dict[str, object]:
    ordered = sorted(samples)
    if not ordered:
        return {"sample_count": 0, "samples": [], "min_saving": None, "median_saving": None, "max_saving": None}
    return {
        "sample_count": len(ordered),
        "samples": samples,
        "min_saving": min(ordered),
        "median_saving": ordered[len(ordered) // 2],
        "max_saving": max(ordered),
    }


def build_paired_cost_measurement(
    legacy_seconds: list[float],
    selected_seconds: list[float],
    planner_seconds: list[float],
    *,
    legacy_test_count: int = 456,
    selected_test_count: int = 14,
    measurement_reused: bool = False,
    measurement_source_sha: str | None = None,
    relevant_subjects_unchanged: bool = False,
) -> dict[str, object]:
    """Record paired local timings without implying system-wide savings."""

    samples = (legacy_seconds, selected_seconds, planner_seconds)
    if not samples[0] or not (len(samples[0]) == len(samples[1]) == len(samples[2])):
        raise ValueError("paired timing arrays must be non-empty and equal length")
    if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0 for group in samples for value in group):
        raise ValueError("paired timings must be finite non-negative numbers")
    if measurement_reused and (
        not measurement_source_sha
        or len(measurement_source_sha) != 40
        or any(character not in "0123456789abcdef" for character in measurement_source_sha)
        or not relevant_subjects_unchanged
    ):
        raise ValueError("reused measurements require a valid source SHA and unchanged relevant subjects")
    legacy = [float(value) for value in legacy_seconds]
    selected = [float(value) for value in selected_seconds]
    planner = [float(value) for value in planner_seconds]
    net = [
        left - right - overhead
        for left, right, overhead in zip(legacy, selected, planner, strict=True)
    ]
    result: dict[str, object] = {
        "schema": COST_SCHEMA,
        "mode": "SHADOW",
        "status": "MEASURED_EXPLORATORY_PAIRED",
        "paired_workload": True,
        "workload": {
            "legacy_command": "cargo test --manifest-path core/rust/Cargo.toml --lib --no-default-features",
            "selected_command": "cargo test --manifest-path core/rust/Cargo.toml --lib --no-default-features gt96",
            "same_source_change": "core/rust/src/gt96.rs",
            "same_environment_class": True,
            "cache_policy": "warm incremental build; commands measured in the same checkout",
            "legacy_test_count": legacy_test_count,
            "selected_test_count": selected_test_count,
        },
        "legacy_wall_time_seconds": _summary(legacy),
        "selected_evidence_wall_time_seconds": _summary(selected),
        "planner_cost_seconds": _summary(planner),
        "net_saving_seconds": _saving_summary(net),
        "net_saving_formula": "legacy - (planner + selected_evidence)",
        "sample_pair_count": len(net),
        "savings_claim": "LOCAL_EXPLORATORY_ONLY",
        "measurement_reused": measurement_reused,
        "measurement_source_sha": measurement_source_sha,
        "relevant_subjects_unchanged": relevant_subjects_unchanged,
        "limitations": [
            "Three paired runs cover one Rust library change class and are exploratory, not a system-wide guarantee.",
            "No claim is made for Python, external anchors, cold builds, or other change classes.",
            "The planner remains shadow-only and cannot skip authoritative tests.",
        ],
    }
    result["provenance"] = _artifact_provenance(
        {"legacy": legacy, "selected": selected, "planner": planner, "source_change": "core/rust/src/gt96.rs"}
    )
    if measurement_reused and measurement_source_sha:
        result["provenance"]["artifact_source_sha"] = measurement_source_sha  # type: ignore[index]
        result["provenance"]["reuse_status"] = "REUSED_HISTORICAL_MEASUREMENT"  # type: ignore[index]
    result["artifact_hash"] = _stable_hash({key: value for key, value in result.items() if key != "artifact_hash"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-output", type=Path, default=DEFAULT_CORPUS_OUTPUT)
    parser.add_argument("--cost-output", type=Path, default=DEFAULT_COST_OUTPUT)
    parser.add_argument("--legacy-seconds", help="comma-separated paired legacy timings")
    parser.add_argument("--selected-seconds", help="comma-separated paired selected timings")
    parser.add_argument("--planner-seconds", help="comma-separated paired planner timings")
    parser.add_argument("--measurement-reused", action="store_true", help="retain an earlier paired measurement")
    parser.add_argument("--measurement-source-sha", help="source commit that produced a reused measurement")
    args = parser.parse_args()
    corpus_output = args.corpus_output if args.corpus_output.is_absolute() else ROOT / args.corpus_output
    cost_output = args.cost_output if args.cost_output.is_absolute() else ROOT / args.cost_output
    corpus = build_corpus()
    if bool(args.legacy_seconds) or bool(args.selected_seconds) or bool(args.planner_seconds):
        if not (args.legacy_seconds and args.selected_seconds and args.planner_seconds):
            raise ValueError("all paired timing options are required together")
        def parse(value: str) -> list[float]:
            return [float(part) for part in value.split(",") if part.strip()]

        cost = build_paired_cost_measurement(
            parse(args.legacy_seconds),
            parse(args.selected_seconds),
            parse(args.planner_seconds),
            measurement_reused=args.measurement_reused,
            measurement_source_sha=args.measurement_source_sha,
            relevant_subjects_unchanged=args.measurement_reused,
        )
    else:
        cost = _load_retained_paired_cost(cost_output) or build_cost_measurement(corpus)
    corpus_output.parent.mkdir(parents=True, exist_ok=True)
    corpus_output.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cost_output.write_text(json.dumps(cost, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"corpus_status": corpus["status"], "critical_misses": corpus["metrics"]["synthetic_critical_targets_missed"], "cost_status": cost["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
