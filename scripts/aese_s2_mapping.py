"""Validate the evidence-derived S2 critical/high-risk mapping.

The seed is deliberately small and explicit.  Unlisted inventory surfaces are
not guessed: they remain UNKNOWN and the report instructs the shadow planner
to widen conservatively.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Final, cast

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_SEED: Final[Path] = ROOT / "quality" / "registry" / "aese_s2_mapping.json"
DEFAULT_INVENTORY: Final[Path] = ROOT / "quality" / "registry" / "current_inventory.json"
DEFAULT_GRAPH: Final[Path] = ROOT / "quality" / "registry" / "current_claim_graph.json"
DEFAULT_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_s2_mapping.json"
SCHEMA: Final[str] = "aese-s2-mapping-v1"
ALLOWED_CRITICALITY: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM", "LOW", "NOT_APPLICABLE", "UNKNOWN"}
)
ALLOWED_RELATIONSHIPS: Final[frozenset[str]] = frozenset(
    {
        "DIRECT_IMPLEMENTATION",
        "DIRECT_CONTRACT",
        "DIRECT_TEST",
        "INTEGRATION_COVERAGE",
        "TRANSITIVE_DEPENDENCY",
        "GENERATES",
        "VALIDATES",
        "META_EVIDENCE",
        "UNKNOWN",
    }
)


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def _tracked_paths() -> set[str]:
    output = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return {item.decode("utf-8") for item in output.split(b"\0") if item}


def _subject_path(subject: str) -> tuple[str, str | None]:
    path, separator, symbol = subject.partition("::")
    normalized = path.strip().replace("\\", "/")
    return normalized, symbol.strip() if separator and symbol.strip() else None


def _verify_subjects(subjects: object, tracked: set[str], *, require_symbol: bool) -> list[str]:
    if not isinstance(subjects, list) or not subjects:
        return ["missing_subjects"]
    errors: list[str] = []
    for subject_value in subjects:
        if not isinstance(subject_value, str) or not subject_value.strip():
            errors.append("subject_not_string")
            continue
        path, symbol = _subject_path(subject_value)
        if path not in tracked or not (ROOT / path).is_file():
            errors.append(f"subject_path_not_tracked:{path}")
            continue
        if require_symbol and symbol is None:
            errors.append(f"subject_symbol_missing:{subject_value}")
            continue
        if symbol is None:
            continue
        text = (ROOT / path).read_text(encoding="utf-8")
        definition = re.compile(rf"\b(?:def|class|fn|struct|enum|trait|type)\s+{re.escape(symbol.lstrip('_'))}\b")
        if not definition.search(text) and not re.search(rf"\b{re.escape(symbol)}\b", text):
            errors.append(f"subject_symbol_not_found:{subject_value}")
    return errors


def _record_errors(
    record: dict[str, object],
    *,
    inventory_ids: set[str],
    claim_ids: set[str],
    tracked: set[str],
) -> list[str]:
    errors: list[str] = []
    surface_id = record.get("surface_id")
    if not isinstance(surface_id, str) or surface_id not in inventory_ids:
        errors.append("surface_id_not_in_inventory")
    raw_claim_ids = record.get("claim_ids")
    if not isinstance(raw_claim_ids, list) or not raw_claim_ids:
        errors.append("claim_ids_missing")
    else:
        errors.extend(f"claim_id_not_in_graph:{claim_id}" for claim_id in raw_claim_ids if claim_id not in claim_ids)
    errors.extend(
        f"invalid_{field}"
        for field in ("risk", "security_criticality", "release_criticality")
        if record.get(field) not in ALLOWED_CRITICALITY
    )
    if not isinstance(record.get("selection_relevant"), bool):
        errors.append("selection_relevant_missing")
    relationships = record.get("relationship_types")
    if not isinstance(relationships, list) or not relationships or any(value not in ALLOWED_RELATIONSHIPS for value in relationships):
        errors.append("invalid_relationship_types")
    if record.get("unknown_dependency_policy") != "WIDEN_CONSERVATIVELY":
        errors.append("unknown_dependency_policy_not_conservative")
    errors.extend(_verify_subjects(record.get("source_subjects"), tracked, require_symbol=True))
    errors.extend(_verify_subjects(record.get("test_subjects"), tracked, require_symbol=True))
    errors.extend(_verify_subjects(record.get("evidence_subjects"), tracked, require_symbol=False))
    rationale = record.get("rationale")
    if not isinstance(rationale, str) or len(rationale.strip()) < 40:
        errors.append("rationale_insufficient")
    return sorted(set(errors))


def build_mapping(
    seed_path: Path = DEFAULT_SEED,
    inventory_path: Path = DEFAULT_INVENTORY,
    graph_path: Path = DEFAULT_GRAPH,
) -> dict[str, object]:
    seed = _load(seed_path)
    inventory = _load(inventory_path)
    graph = _load(graph_path)
    inventory_values = inventory.get("items")
    graph_claim_values = graph.get("claims")
    raw_records = seed.get("records")
    if not isinstance(inventory_values, list) or not isinstance(graph_claim_values, list) or not isinstance(raw_records, list):
        raise ValueError("S2 mapping inputs have invalid arrays")
    inventory_ids = {str(item["stable_id"]) for item in inventory_values if isinstance(item, dict) and "stable_id" in item}
    claim_ids = {str(item["id"]) for item in graph_claim_values if isinstance(item, dict) and "id" in item}
    tracked = _tracked_paths()
    records: list[dict[str, object]] = []
    errors_by_surface: dict[str, list[str]] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            errors_by_surface.setdefault("<invalid>", []).append("record_not_object")
            continue
        record = cast(dict[str, object], raw_record)
        surface_id = str(record.get("surface_id", "<missing>"))
        errors = _record_errors(record, inventory_ids=inventory_ids, claim_ids=claim_ids, tracked=tracked)
        if surface_id in errors_by_surface:
            errors.append("duplicate_surface_mapping")
        errors_by_surface[surface_id] = errors
        records.append(
            {
                **record,
                "mapping_status": "MAPPED_MULTI" if len(cast(list[object], record.get("claim_ids", []))) > 1 else "MAPPED_EXACT",
                "verification_status": "VERIFIED" if not errors else "INVALID",
                "verification_errors": errors,
            }
        )
    mapped_ids = {
        str(record.get("surface_id"))
        for record in records
        if record.get("surface_id") in inventory_ids and record.get("verification_status") == "VERIFIED"
    }
    unknown_surface_ids = sorted(inventory_ids - mapped_ids)
    inventory_paths_by_id = {
        str(item["stable_id"]): str(item["path"]).replace("\\", "/")
        for item in inventory_values
        if isinstance(item, dict) and item.get("stable_id") and item.get("path")
    }
    unknown_surface_paths = sorted({inventory_paths_by_id[surface_id] for surface_id in unknown_surface_ids if surface_id in inventory_paths_by_id})
    critical_records = [record for record in records if record.get("risk") == "CRITICAL" or record.get("security_criticality") == "CRITICAL"]
    high_selection_records = [
        record
        for record in records
        if record.get("selection_relevant") is True and record.get("risk") in {"CRITICAL", "HIGH"}
    ]
    critical_complete = bool(critical_records) and all(record["verification_status"] == "VERIFIED" for record in critical_records)
    high_selection_complete = bool(high_selection_records) and all(
        record["verification_status"] == "VERIFIED" for record in high_selection_records
    )
    output_without_hash: dict[str, object] = {
        "schema": SCHEMA,
        "phase": "PHASE_2_CRITICAL_HIGH_RISK_MAPPING",
        "mode": "SHADOW",
        "selection_authority": "DISABLED",
        "source_head": str(graph.get("source_head", "UNKNOWN")),
        "current_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
        "inventory_source_tree_sha256": inventory.get("source_tree_sha256"),
        "claim_graph_source_tree_sha256": graph.get("source_tree_sha256"),
        "mapping_seed_hash": _hash(seed),
        "mapping_status": (
            "S2_FAIL_CLOSED_MAPPING_COMPLETE"
            if critical_complete and high_selection_complete
            else "INSUFFICIENT_EVIDENCE"
        ),
        "all_declared_critical_mapped": critical_complete,
        "all_declared_high_selection_relevant_mapped": high_selection_complete,
        "critical_mapping_scope": "DECLARED_MAPPED_RECORDS_ONLY",
        "unknown_surfaces_may_contain_unclassified_criticality": True,
        "all_critical_mapped": critical_complete,
        "all_high_selection_relevant_mapped": high_selection_complete,
        "unknown_dependency_policy": "WIDEN_CONSERVATIVELY",
        "unknown_surface_count": len(unknown_surface_ids),
        "unknown_surface_ids": unknown_surface_ids,
        "unknown_surface_paths": unknown_surface_paths,
        "critical_mapping_count": len(critical_records),
        "high_selection_mapping_count": len(high_selection_records),
        "claim_graph_status": graph.get("status"),
        "no_fake_mapping": not any(errors_by_surface.values()),
        "records": records,
        "errors_by_surface": {key: value for key, value in sorted(errors_by_surface.items()) if value},
        "critical_false_negative_status": "NOT_EVALUATED_S3",
        "limitations": [
            "Unlisted surfaces remain UNKNOWN; shadow planners must widen conservatively.",
            "Mapping evidence does not authorize test skipping or promotion.",
            "Critical false-negative closure is evaluated in S3, not inferred from S2 mapping completeness.",
        ],
    }
    return {**output_without_hash, "artifact_hash": _hash(output_without_hash)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else ROOT / path

    report = build_mapping(resolve(args.seed), resolve(args.inventory), resolve(args.graph))
    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["mapping_status"], "artifact_hash": report["artifact_hash"], "unknown_surface_count": report["unknown_surface_count"]}))
    return 0 if report["mapping_status"] == "S2_FAIL_CLOSED_MAPPING_COMPLETE" and report["no_fake_mapping"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
