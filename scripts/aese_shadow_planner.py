"""Produce an explainable AESE shadow selection plan.

This module compares a hypothetical AESE subset with the retained legacy
authority.  It never runs, skips, reuses, or promotes an authoritative test.
Unknown closure inputs widen to the retained suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Final, cast

if __package__:
    from scripts import aese_affected_closure as _closure
    from scripts import aese_inventory as _inventory
else:  # Direct ``python scripts/aese_shadow_planner.py`` from repository root.
    import aese_affected_closure as _closure
    import aese_inventory as _inventory

ROOT = _inventory.ROOT
DEFAULT_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_shadow_plan.json"
SCHEMA: Final[str] = "aese-shadow-planner-v1"
PHASE: Final[str] = "PHASE_4_SHADOW_SELECTIVE_PLANNER"
MODE: Final[str] = "SHADOW"
DECISION_STATES: Final[frozenset[str]] = frozenset(
    {"WOULD_RUN", "WOULD_REUSE", "WOULD_SKIP", "WIDENED_UNKNOWN", "EXTERNAL_DEFERRED"}
)


def _normalize(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validator_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _items() -> list[dict[str, object]]:
    inventory = _inventory.build_inventory()
    values = inventory.get("items", [])
    return [cast(dict[str, object], value) for value in values if isinstance(value, dict)]


def _critical_item_ids(items: list[dict[str, object]], mapping_path: Path = _closure.DEFAULT_MAPPING) -> set[str]:
    """Resolve criticality from the evidence-derived S2 mapping, not names."""

    mapping = _closure._load_json(mapping_path)
    critical_paths: set[str] = set()
    records = mapping.get("records", [])
    if isinstance(records, list):
        for raw_record in records:
            if not isinstance(raw_record, dict):
                continue
            record = cast(dict[str, object], raw_record)
            values = {str(record.get(key, "UNKNOWN")).upper() for key in ("risk", "security_criticality", "release_criticality")}
            if values.intersection(_closure.CRITICAL_VALUES):
                subjects = record.get("test_subjects", [])
                if isinstance(subjects, list):
                    critical_paths.update(_closure._subject_path(str(subject)) for subject in subjects)
    return {
        str(item["stable_id"])
        for item in items
        if _normalize(str(item["path"])) in critical_paths
    }


def _confusion_matrix(selected_ids: set[str], legacy_results: dict[str, str] | None, critical_ids: set[str]) -> dict[str, object]:
    if legacy_results is None:
        return {
            "status": "NOT_MEASURED",
            "counts": {
                "AESE_SELECTED_FAILED": 0,
                "AESE_SELECTED_PASSED": 0,
                "AESE_SKIPPED_PASSED": 0,
                "AESE_SKIPPED_FAILED": 0,
            },
            "critical_false_negatives": [],
            "unobserved_item_count": len(_items()),
        }
    counts = {
        "AESE_SELECTED_FAILED": 0,
        "AESE_SELECTED_PASSED": 0,
        "AESE_SKIPPED_PASSED": 0,
        "AESE_SKIPPED_FAILED": 0,
    }
    critical_false_negatives: list[str] = []
    for item_id, outcome in sorted(legacy_results.items()):
        selected = item_id in selected_ids
        normalized = outcome.strip().upper()
        if normalized not in {"PASS", "FAIL"}:
            continue
        key = f"AESE_{'SELECTED' if selected else 'SKIPPED'}_{'PASSED' if normalized == 'PASS' else 'FAILED'}"
        counts[key] += 1
        if key == "AESE_SKIPPED_FAILED" and item_id in critical_ids:
            critical_false_negatives.append(item_id)
    return {
        "status": "MEASURED_FROM_EXPLICIT_LEGACY_RESULTS",
        "counts": counts,
        "critical_false_negatives": sorted(critical_false_negatives),
        "unobserved_item_count": len(set(item["stable_id"] for item in _items()) - set(legacy_results)),
    }


def _observed_critical_false_negative_status(
    legacy_results: dict[str, str] | None, critical_ids: set[str], critical_false_negatives: list[str]
) -> str:
    if legacy_results is None:
        return "NOT_MEASURED"
    if any(
        type(outcome) is not str or outcome.strip().upper() not in {"PASS", "FAIL"}
        for outcome in legacy_results.values()
    ):
        return "NOT_MEASURED_INCOMPLETE"
    if critical_false_negatives:
        return "OBSERVED_CRITICAL_FALSE_NEGATIVE"
    if not critical_ids.issubset(set(legacy_results)):
        return "NOT_MEASURED_INCOMPLETE"
    return "OBSERVED_COMPLETE_ZERO"


def build_shadow_plan(
    changed_paths: list[str] | tuple[str, ...] = (),
    legacy_results: dict[str, str] | None = None,
    mapping_path: Path = _closure.DEFAULT_MAPPING,
) -> dict[str, object]:
    normalized_paths = sorted({_normalize(path) for path in changed_paths if path.strip()})
    closure = _closure.build_closure(normalized_paths, mapping_path)
    items = _items()
    closure_paths = set(cast(list[str], closure["closure_paths"]))
    widened = bool(closure["plan_widened"])
    critical_audit = cast(dict[str, object], closure["critical_audit"])
    critical_ids = _critical_item_ids(items, mapping_path)
    decisions: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    for item in sorted(items, key=lambda value: (str(value["path"]), str(value["stable_id"]))):
        item_id = str(item["stable_id"])
        path = _normalize(str(item["path"]))
        if widened:
            state = "WIDENED_UNKNOWN"
            reason = "unknown_or_dynamic_dependency_widens_to_all_retained_items"
            selected_ids.add(item_id)
        elif path in closure_paths:
            state = "WOULD_RUN"
            reason = "path_is_in_exact_contract_closure"
            selected_ids.add(item_id)
        else:
            state = "WOULD_SKIP"
            reason = "path_is_outside_exact_contract_closure_shadow_only"
        decisions.append(
            {
                "item_id": item_id,
                "path": path,
                "state": state,
                "reason": reason,
                "evidence": {
                    "changed_paths": normalized_paths,
                    "closure_hash": closure["reproducible_hash"],
                    "critical_audit_status": critical_audit["status"],
                    "authority": "RETAINED_LEGACY_AUTHORITY",
                },
            }
        )
    external_paths = sorted(path for path in normalized_paths if path.startswith(".github/workflows/"))
    external_deferred = [
        {
            "state": "EXTERNAL_DEFERRED",
            "path": path,
            "reason": "hosted_external_anchor_unavailable; local legacy authority remains retained",
        }
        for path in external_paths
    ]
    confusion = _confusion_matrix(selected_ids, legacy_results, critical_ids)
    critical_false_negatives = cast(list[str], confusion["critical_false_negatives"])
    observed_status = _observed_critical_false_negative_status(
        legacy_results, critical_ids, critical_false_negatives
    )
    structural_status = str(critical_audit.get("structural_critical_reachability_status", "UNKNOWN"))
    plan: dict[str, object] = {
        "schema": SCHEMA,
        "phase": PHASE,
        "mode": MODE,
        "status": "SHADOW_PLAN_ONLY_SELECTION_DISABLED",
        "direct_cutover": "PROHIBITED",
        "changed_paths": normalized_paths,
        "closure_status": closure["closure_status"],
        "closure_hash": closure["reproducible_hash"],
        "closure_paths": closure["closure_paths"],
        "plan_widened": widened,
        "unknown_repository_paths": closure["unknown_repository_paths"],
        "unknown_surface_ids": closure["unknown_surface_ids"],
        "unknown_surface_paths": closure["unknown_surface_paths"],
        "changed_unmapped_surface_ids": closure["changed_unmapped_surface_ids"],
        "changed_unmapped_surface_paths": closure["changed_unmapped_surface_paths"],
        "path_dependency_states": closure["path_dependency_states"],
        "unknown_dependency_policy": _closure.UNKNOWN_POLICY,
        "decisions": decisions,
        "decision_states": sorted({str(decision["state"]) for decision in decisions}),
        "external_deferred": external_deferred,
        "would_run_item_ids": sorted(selected_ids),
        "would_reuse_item_ids": [],
        "would_skip_item_ids": sorted(
            str(decision["item_id"]) for decision in decisions if decision["state"] == "WOULD_SKIP"
        ),
        "reuse_policy": {
            "status": "NO_REUSE_CANDIDATES",
            "required_match": [
                "relevant_source_digest",
                "protocol_hash",
                "validator_hash",
                "environment_class",
                "claim_domain",
            ],
            "artifact_age_alone_is_not_sufficient": True,
        },
        "confusion_matrix": confusion,
        "structural_critical_reachability_status": structural_status,
        "observed_critical_false_negative_status": observed_status,
        # Compatibility alias; unlike the historical value it never reports
        # zero before explicit legacy outcomes exist.
        "critical_false_negative_status": observed_status,
        "execution": "NOT_EXECUTED",
        "provenance": {
            "source_sha": str(_inventory.build_inventory()["source_head"]),
            "closure_artifact_hash": closure["artifact_hash"],
            "validator_hash": _validator_hash(),
            "environment_hash": _stable_hash(
                {
                    "python": platform.python_version(),
                    "implementation": platform.python_implementation(),
                    "os": platform.system(),
                    "release": platform.release(),
                    "machine": platform.machine(),
                    "executable": sys.executable,
                }
            ),
            "validator": "scripts/aese_shadow_planner.py",
            "evidence_class": "PLANNING_ONLY",
            "claim_scope": "LOCAL_CHECKOUT_ONLY",
            "promotion": "DISABLED_IN_SHADOW",
        },
        "limitations": [
            "Decisions are predictions; legacy execution remains authoritative.",
            "No reuse is granted without all declared digest and environment matches.",
            "Confusion matrix is NOT_MEASURED unless explicit legacy results are supplied.",
        ],
    }
    plan["artifact_hash"] = _stable_hash({key: value for key, value in plan.items() if key != "artifact_hash"})
    return plan


def validate_shadow_plan(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    if type(actual) is not dict or type(expected) is not dict:
        return ["shadow plans must be canonical dictionaries"]
    errors: list[str] = []
    if set(actual) != set(expected):
        errors.append("root schema keys differ")
    errors.extend(
        f"{key} differs"
        for key in expected
        if key not in {"artifact_hash", "provenance"} and actual.get(key) != expected.get(key)
    )
    if actual.get("artifact_hash") != _stable_hash({key: value for key, value in actual.items() if key != "artifact_hash"}):
        errors.append("artifact_hash is not self-consistent")
    if actual.get("execution") != "NOT_EXECUTED":
        errors.append("shadow planner execution is not disabled")
    if any(state not in DECISION_STATES for state in cast(list[str], actual.get("decision_states", []))):
        errors.append("unknown decision state")
    if actual.get("observed_critical_false_negative_status") == "COMPLETE_ZERO":
        errors.append("observed false-negative status uses an unscoped zero")
    if actual.get("observed_critical_false_negative_status") == "NOT_MEASURED" and actual.get("confusion_matrix", {}).get("status") != "NOT_MEASURED":  # type: ignore[union-attr]
        errors.append("observed status is inconsistent with confusion matrix")
    if actual.get("structural_critical_reachability_status") != "COMPLETE_ZERO_MAPPED_SCOPE":
        errors.append("structural critical reachability scope is not complete")
    if actual.get("changed_unmapped_surface_paths") and actual.get("plan_widened") is not True:
        errors.append("changed unmapped surfaces must widen")
    if actual.get("unknown_repository_paths") and actual.get("plan_widened") is not True:
        errors.append("unknown repository paths must widen")
    if actual.get("plan_widened") and actual.get("would_skip_item_ids"):
        errors.append("widened planner cannot skip retained items")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", default=[], help="changed repository path (repeatable)")
    parser.add_argument("--legacy-results", type=Path, help="JSON object mapping inventory item IDs to PASS/FAIL")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="write the shadow plan JSON")
    args = parser.parse_args()
    legacy_results: dict[str, str] | None = None
    if args.legacy_results:
        raw = json.loads(args.legacy_results.read_text(encoding="utf-8"))
        if type(raw) is not dict or any(type(key) is not str or type(value) is not str for key, value in raw.items()):
            raise ValueError("legacy results must be a JSON object of item ID to PASS/FAIL")
        legacy_results = cast(dict[str, str], raw)
    plan = build_shadow_plan(args.path, legacy_results)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": plan["status"],
                "closure_status": plan["closure_status"],
                "plan_widened": plan["plan_widened"],
                "critical_false_negative_status": plan["critical_false_negative_status"],
                "artifact_hash": plan["artifact_hash"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
