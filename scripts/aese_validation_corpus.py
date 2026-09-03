"""Build the held-out AESE validation corpus and conservative cost ledger.

Corpus evaluation is shadow-only.  The planner is never allowed to control
the legacy runner, and a cost claim is withheld until paired legacy and
selected-evidence timings are supplied for the same workload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
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
)


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _evaluate_case(case: dict[str, object]) -> dict[str, object]:
    changed = cast(list[str], case["changed_paths"])
    plan = _planner.build_shadow_plan(changed)
    target_paths = set(cast(list[str], case["target_paths"]))
    reached = set(cast(list[str], plan["closure_paths"]))
    caught = bool(target_paths <= reached) if target_paths else None
    label = str(case["label"])
    return {
        **case,
        "plan_widened": plan["plan_widened"],
        "closure_status": plan["closure_status"],
        "critical_false_negative_status": plan["critical_false_negative_status"],
        "target_paths_reached": sorted(target_paths & reached),
        "target_paths_missing": sorted(target_paths - reached),
        "defect_caught": caught if label == "KNOWN_BAD" else None,
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
    _validate_cases(DEVELOPMENT_CASES, "development")
    _validate_cases(FINAL_CASES, "final")
    development = [_evaluate_case(case) for case in DEVELOPMENT_CASES]
    final = [_evaluate_case(case) for case in FINAL_CASES]
    if {str(case["case_id"]) for case in DEVELOPMENT_CASES} & {str(case["case_id"]) for case in FINAL_CASES}:
        raise ValueError("development and final corpus IDs must be disjoint")
    final_known_bad = [case for case in final if case["label"] == "KNOWN_BAD"]
    final_critical = [case for case in final_known_bad if case["critical"] is True]
    critical_misses = [str(case["case_id"]) for case in final_critical if case["defect_caught"] is not True]
    noncritical = [case for case in final_known_bad if case["critical"] is not True]
    noncritical_misses = [str(case["case_id"]) for case in noncritical if case["defect_caught"] is not True]
    false_alarms = [
        str(case["case_id"])
        for case in final
        if case["label"] == "KNOWN_GOOD" and case["plan_widened"] is True
    ]
    corpus: dict[str, object] = {
        "schema": SCHEMA,
        "mode": "SHADOW",
        "status": "COMPLETE" if not critical_misses else "INSUFFICIENT_EVIDENCE",
        "development_corpus": development,
        "final_validation_corpus": final,
        "split_policy": {
            "final_held_out": True,
            "planner_tuning_uses": "DEVELOPMENT_ONLY",
            "final_corpus_not_used_for_tuning": True,
            "ambiguous_not_forced_into_good_or_bad": True,
        },
        "metrics": {
            "critical_defects": len(final_critical),
            "critical_defects_caught": len(final_critical) - len(critical_misses),
            "critical_defects_missed": len(critical_misses),
            "critical_miss_case_ids": critical_misses,
            "noncritical_defects": len(noncritical),
            "noncritical_defects_caught": len(noncritical) - len(noncritical_misses),
            "noncritical_defects_missed": len(noncritical_misses),
            "noncritical_miss_case_ids": noncritical_misses,
            "false_alarm_case_ids": false_alarms,
            "widen_events": sum(1 for case in final if case["plan_widened"] is True),
            "ood_events": sum(1 for case in final if case["label"] == "OOD"),
        },
        "limitations": [
            "Corpus cases are deterministic planning mutations; no production or external tests are inferred.",
            "Critical-miss tolerance is zero, while noncritical policy remains explicit and exploratory.",
            "A larger independently sourced corpus is required before any production non-inferiority claim.",
        ],
    }
    corpus["artifact_hash"] = _stable_hash({key: value for key, value in corpus.items() if key != "artifact_hash"})
    return corpus


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
    return {
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-output", type=Path, default=DEFAULT_CORPUS_OUTPUT)
    parser.add_argument("--cost-output", type=Path, default=DEFAULT_COST_OUTPUT)
    args = parser.parse_args()
    corpus = build_corpus()
    cost = build_cost_measurement(corpus)
    corpus_output = args.corpus_output if args.corpus_output.is_absolute() else ROOT / args.corpus_output
    cost_output = args.cost_output if args.cost_output.is_absolute() else ROOT / args.cost_output
    corpus_output.parent.mkdir(parents=True, exist_ok=True)
    corpus_output.write_text(json.dumps(corpus, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cost_output.write_text(json.dumps(cost, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"corpus_status": corpus["status"], "critical_misses": corpus["metrics"]["critical_defects_missed"], "cost_status": cost["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
