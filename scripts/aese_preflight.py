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
import platform
import subprocess
import sys
from pathlib import Path
from typing import Final, cast

if __package__:
    from scripts import aese_claim_graph as _claim_graph
    from scripts import aese_inventory as _inventory
else:  # Direct ``python scripts/aese_preflight.py`` invocation from the repo root.
    import aese_claim_graph as _claim_graph
    import aese_inventory as _inventory

ROOT = _inventory.ROOT
build_inventory = _inventory.build_inventory
build_graph = _claim_graph.build_graph


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


def _git_ref(ref: str) -> str:
    """Return an optional ref without turning missing remote state into a claim."""

    try:
        return subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "NOT_AVAILABLE"


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
    # A source node can legitimately have multiple edges of the same
    # relation (for example one invariant verified by several suites).  Keep
    # a multimap; collapsing to one target would silently under-approximate
    # the affected closure and could later permit an unsafe selective skip.
    relation: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        key = (str(edge["from"]), str(edge["relation"]))
        relation.setdefault(key, set()).add(str(edge["to"]))
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
        for target in relation.get((contract_id, "GUARDS"), set())
    }
    affected_verifications = {
        target
        for invariant_id in affected_invariants
        for target in relation.get((invariant_id, "VERIFIED_BY_REFERENCE"), set())
    }
    affected_evidence = {
        target
        for verification_id in affected_verifications
        for target in relation.get((verification_id, "MATERIALIZES"), set())
    }
    affected_claims = {
        target
        for evidence_id in affected_evidence
        for target in relation.get((evidence_id, "SUPPORTS_OR_LEAVES_UNVERIFIED"), set())
    }
    # A changed verification surface is a direct seed in the closure.  The
    # graph records both the exact test/benchmark reference and the claim it
    # exercises; follow both edges without treating a surface as source code.
    for surface_id in matched_surfaces:
        for verification_id in relation.get((surface_id, "RESOLVES_REFERENCE"), set()):
            affected_verifications.add(verification_id)
        claims.update(relation.get((surface_id, "IMPLEMENTS_OR_EXERCISES"), set()))
    affected_evidence.update(
        target
        for verification_id in affected_verifications
        for target in relation.get((verification_id, "MATERIALIZES"), set())
    )
    affected_claims.update(
        target
        for evidence_id in affected_evidence
        for target in relation.get((evidence_id, "SUPPORTS_OR_LEAVES_UNVERIFIED"), set())
    )
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
    retained_item_ids = sorted(str(item["stable_id"]) for item in items)
    shadow_selection = {
        "mode": "SHADOW",
        "authority": "RETAINED_LEGACY_AUTHORITY",
        "legacy_would_run_item_ids": retained_item_ids,
        "would_reuse_item_ids": [],
        "would_skip_item_ids": [],
        "external_anchor_requests": [],
        "external_anchor_status": "NOT_EVALUATED_NO_CANDIDATES",
        "execution": "NOT_EXECUTED",
        "reason": (
            "UNKNOWN_OR_UNMAPPED_DEPENDENCY_WIDENS_TO_ALL_RETAINED"
            if widened
            else "SHADOW_COMPARISON_RETAINS_LEGACY_AUTHORITY"
        ),
    }
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
        "shadow_selection": shadow_selection,
        "provenance": {
            "source_sha": str(inventory["source_head"]),
            "origin_main_sha": _git_ref("origin/main"),
            "worktree_status": str(inventory["worktree_status"]),
            "worktree_epoch": str(inventory["worktree_epoch"]),
            "inventory_source_tree_sha256": str(inventory["source_tree_sha256"]),
            "graph_input_sha256": str(graph["source_tree_sha256"]),
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "os": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "executable": sys.executable,
            },
            "validator": "scripts/aese_preflight.py",
            "evidence_class": "PLANNING_ONLY",
            "claim_scope": "LOCAL_CHECKOUT_ONLY",
            "promotion": "DISABLED_IN_SHADOW",
        },
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
        "shadow_selection",
        "provenance",
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
