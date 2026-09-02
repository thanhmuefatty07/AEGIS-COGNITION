"""Build an AESE-2 shadow evidence preflight plan.

The planner classifies a supplied change set and records the evidence order
that AESE would propose.  Legacy suites remain authoritative: this command
never skips, runs, promotes, or deletes evidence.  An unmapped path widens the
plan instead of narrowing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Final, cast

from scripts.aese_claim_graph import build_graph
from scripts.aese_inventory import ROOT, build_inventory


SCHEMA: Final[str] = "aese-shadow-preflight-plan-v1"
PHASE: Final[str] = "PHASE_2_CHEAP_PREFLIGHT"
MODE: Final[str] = "SHADOW"
DEFAULT_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("STATIC_METADATA", "inventory_and_claim_graph_drift"),
    ("DOCUMENT_ARCHITECTURE", "document_consistency_and_architecture_fitness"),
    ("SECURITY_STATIC", "secret_and_dependency_policy"),
    ("PYTHON_QUALITY", "ruff_pyright_and_python_regression"),
    ("RUST_QUALITY", "fmt_check_clippy_and_rust_regression"),
    ("PLATFORM_ANCHORS", "cross_platform_or_hosted_evidence_when_required"),
    ("PERFORMANCE", "benchmark_or_simulation_only_with_declared_estimand"),
    ("RELEASE", "signed_provenance_and_release_certification"),
)


def _normalize(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _git_paths(base: str, target: str) -> list[str]:
    output = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{target}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted({_normalize(path) for path in output.splitlines() if path.strip()})


def _digest(*parts: str) -> str:
    material = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def build_preflight(changed_paths: list[str] | tuple[str, ...] = ()) -> dict[str, object]:
    inventory = build_inventory()
    graph = build_graph()
    paths = sorted({_normalize(path) for path in changed_paths if path.strip()})
    items = [cast(dict[str, object], value) for value in inventory["items"] if isinstance(value, dict)]
    surfaces = [cast(dict[str, object], value) for value in graph["surfaces"] if isinstance(value, dict)]
    code_nodes = [cast(dict[str, object], value) for value in graph["code"] if isinstance(value, dict)]
    item_ids_by_path: dict[str, list[str]] = {}
    for item in items:
        item_ids_by_path.setdefault(str(item["path"]), []).append(str(item["stable_id"]))
    surface_ids_by_path: dict[str, list[str]] = {}
    for surface in surfaces:
        surface_ids_by_path.setdefault(str(surface["path"]), []).append(str(surface["id"]))
    code_ids_by_path = {str(node["path"]): str(node["id"]) for node in code_nodes}
    matched_items = sorted({item_id for path in paths for item_id in item_ids_by_path.get(path, [])})
    matched_surfaces = sorted({surface_id for path in paths for surface_id in surface_ids_by_path.get(path, [])})
    matched_code = sorted({code_ids_by_path[path] for path in paths if path in code_ids_by_path})
    known_paths = set(item_ids_by_path) | set(code_ids_by_path)
    unknown_paths = sorted(path for path in paths if path not in known_paths)
    unmapped_known_paths = sorted(
        path
        for path in paths
        if path in known_paths
        and any(str(surface["path"]) == path and surface["mapping_status"] == "NOT_MAPPED" for surface in surfaces)
    )
    claims: set[str] = set()
    for contract in graph["contracts"]:
        if not isinstance(contract, dict):
            continue
        source_paths = contract.get("source_paths", ())
        if isinstance(source_paths, list) and any(str(path) in paths for path in source_paths):
            claims.add(f"AESE-CLAIM-{contract['source_id']}")
    widened = bool(unknown_paths or unmapped_known_paths) or not paths
    edges = [cast(dict[str, object], value) for value in graph["edges"] if isinstance(value, dict)]
    relation = {(str(edge["from"]), str(edge["relation"])): str(edge["to"]) for edge in edges}
    affected_contracts = {
        str(contract["id"])
        for contract in graph["contracts"]
        if isinstance(contract, dict)
        and isinstance(contract.get("source_paths"), list)
        and any(str(source_path) in paths for source_path in contract["source_paths"])
    }
    affected_invariants = {
        target
        for contract_id in affected_contracts
        for (source, edge_relation), target in relation.items()
        if source == contract_id and edge_relation == "GUARDS"
    }
    affected_verifications = {
        target
        for invariant_id in affected_invariants
        for (source, edge_relation), target in relation.items()
        if source == invariant_id and edge_relation == "VERIFIED_BY_REFERENCE"
    }
    affected_evidence = {
        target
        for verification_id in affected_verifications
        for (source, edge_relation), target in relation.items()
        if source == verification_id and edge_relation == "MATERIALIZES"
    }
    affected_claims = {
        target
        for evidence_id in affected_evidence
        for (source, edge_relation), target in relation.items()
        if source == evidence_id and edge_relation == "SUPPORTS_OR_LEAVES_UNVERIFIED"
    }
    if widened:
        affected_contracts = {str(contract["id"]) for contract in graph["contracts"] if isinstance(contract, dict)}
        affected_invariants = {str(invariant["id"]) for invariant in graph["invariants"] if isinstance(invariant, dict)}
        affected_verifications = {
            str(verification["id"]) for verification in graph["verifications"] if isinstance(verification, dict)
        }
        affected_evidence = {
            str(evidence["id"]) for evidence in graph["evidence_obligations"] if isinstance(evidence, dict)
        }
        affected_claims = {str(claim["id"]) for claim in graph["claims"] if isinstance(claim, dict)}
    claims.update(affected_claims)
    selection = "RUN_ALL_RETAINED" if widened else "RUN_ALL_RETAINED_SHADOW_COMPARISON"
    order = [
        {
            "stage": stage,
            "capability": capability,
            "selection": "RETAINED_LEGACY_AUTHORITY",
            "may_skip": False,
        }
        for stage, capability in DEFAULT_ORDER
    ]
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "mode": MODE,
        "status": "PREFLIGHT_PLAN_ONLY_SELECTION_DISABLED",
        "direct_cutover": "PROHIBITED",
        "source_head": str(inventory["source_head"]),
        "source_tree_sha256": str(graph["source_tree_sha256"]),
        "worktree_epoch": str(inventory["worktree_epoch"]),
        "worktree_status": str(inventory["worktree_status"]),
        "changed_paths": paths,
        "matched_inventory_ids": matched_items,
        "matched_surface_ids": matched_surfaces,
        "matched_code_ids": matched_code,
        "affected_claim_ids": sorted(claims),
        "affected_contract_ids": sorted(affected_contracts),
        "affected_invariant_ids": sorted(affected_invariants),
        "affected_verification_ids": sorted(affected_verifications),
        "affected_evidence_ids": sorted(affected_evidence),
        "unknown_paths": unknown_paths,
        "unmapped_known_paths": unmapped_known_paths,
        "closure_status": "WIDENED_ALL_RETAINED" if widened else "EXACT_SOURCE_CLOSURE_SHADOW",
        "unknown_dependency_policy": "WIDEN_TO_RETAINED_SUITE",
        "plan_widened": widened,
        "legacy_selection": selection,
        "evidence_order": order,
        "external_anchor_policy": "REQUEST_ONLY_WHEN_LOCAL_EVIDENCE_IS_INSUFFICIENT",
        "measurement": {
            "cost_status": "NOT_MEASURED",
            "savings_claim": "PROHIBITED_UNTIL_PAIRED_MEASUREMENT",
            "statistical_status": "NOT_STARTED",
        },
        "input_digest": _digest(
            str(graph["source_tree_sha256"]),
            *paths,
            json.dumps(matched_items, separators=(",", ":")),
            json.dumps(matched_surfaces, separators=(",", ":")),
            json.dumps(matched_code, separators=(",", ":")),
        ),
        "limitations": [
            "The plan is advisory shadow output; legacy runners remain authoritative.",
            "Empty or unmapped changes widen to the retained suite and never become a skip.",
            "Cost, runtime savings, statistical precision, OOD status and external validation are not inferred.",
        ],
    }


def validate_preflight(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    keys = (
        "schema",
        "phase",
        "mode",
        "status",
        "direct_cutover",
        "source_tree_sha256",
        "changed_paths",
        "matched_inventory_ids",
        "matched_surface_ids",
        "matched_code_ids",
        "affected_claim_ids",
        "affected_contract_ids",
        "affected_invariant_ids",
        "affected_verification_ids",
        "affected_evidence_ids",
        "unknown_paths",
        "unmapped_known_paths",
        "closure_status",
        "unknown_dependency_policy",
        "plan_widened",
        "legacy_selection",
        "evidence_order",
        "external_anchor_policy",
        "measurement",
        "input_digest",
        "limitations",
    )
    return [f"{key} differs" for key in keys if actual.get(key) != expected.get(key)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", default=[], help="changed repository path (repeatable)")
    parser.add_argument("--from", dest="base", help="git base revision for changed-path discovery")
    parser.add_argument("--to", dest="target", default="HEAD", help="git target revision")
    parser.add_argument("--output", type=Path, help="write the plan JSON to this path")
    args = parser.parse_args()
    changed = list(args.path)
    if args.base:
        changed.extend(_git_paths(args.base, args.target))
    plan = build_preflight(changed)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
