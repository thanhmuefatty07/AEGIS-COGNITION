"""Build the AESE-1 shadow claim/evidence graph.

This graph is an observation and planning artifact.  It does not select or
skip a test, promote evidence, or replace an existing runner.  References that
cannot be resolved from tracked source remain explicit ``NOT_MAPPED`` values;
the later affected-closure phase must invalidate conservatively in that case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Final, cast

if __package__:
    from scripts import aese_inventory as _inventory
else:  # Direct ``python scripts/aese_claim_graph.py`` invocation from the repo root.
    import aese_inventory as _inventory

ROOT = _inventory.ROOT
build_inventory = _inventory.build_inventory


DEFAULT_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_claim_graph.json"
TRACEABILITY: Final[Path] = ROOT / "docs" / "architecture" / "GT96_TRACEABILITY.md"
NOT_VERIFIED: Final[Path] = ROOT / "docs" / "architecture" / "not_verified_registry.json"
SCHEMA: Final[str] = "aese-shadow-claim-evidence-graph-v1"
PHASE: Final[str] = "PHASE_1_CLAIM_EVIDENCE_GRAPH"
MODE: Final[str] = "SHADOW"
TRACEABILITY_RE: Final[re.Pattern[str]] = re.compile(r"^(?:GT96|AESE)-(\d{3})$")
_CRITICAL_VALUES: Final[frozenset[str]] = frozenset({"CRITICAL", "HIGH", "CRITICAL_NOW", "RELEASE_CRITICAL"})


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _code_source_material(code_nodes: list[dict[str, object]]) -> str:
    """Bind the graph input to every resolved implementation source file."""

    material: list[str] = []
    for node in sorted(code_nodes, key=lambda item: str(item["path"])):
        path = str(node["path"])
        source = ROOT / path
        digest = _file_sha256(source) if source.is_file() else "MISSING"
        material.append(f"{path}\0{digest}")
    return "\n".join(material)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _implementation_paths(value: str) -> list[str]:
    paths: list[str] = []
    for reference in re.findall(r"`([^`]+)`", value):
        path = reference.split("::", 1)[0].strip().replace("\\", "/")
        if "/" in path or path.endswith((".py", ".rs")):
            paths.append(path)
    return sorted(set(paths))


def _reference_paths(value: str) -> list[str]:
    """Return only references that are unambiguously repository paths."""

    paths: list[str] = []
    for token in re.findall(r"`([^`]+)`", value):
        normalized = token.split("::", 1)[0].strip().replace("\\", "/")
        if normalized.endswith((".py", ".rs", ".yml", ".yaml", ".json")):
            paths.append(normalized)
    return sorted(set(paths))


def _verification_symbols(value: str) -> list[str]:
    return sorted(
        set(symbol for symbol in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", value) if symbol not in {"tests", "test"})
    )


def _resolve_verification_paths(value: str, sources: dict[str, str]) -> list[str]:
    resolved: set[str] = set(_reference_paths(value))
    for symbol in _verification_symbols(value):
        definition = re.compile(rf"\b(?:def|fn)\s+{re.escape(symbol)}\b")
        resolved.update(path for path, text in sources.items() if definition.search(text))
    return sorted(resolved)


def _tracked_paths() -> set[str]:
    output = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return {item.decode("utf-8") for item in output.split(b"\0") if item}


def _canonical_source_path(reference: str, tracked: set[str]) -> str | None:
    normalized = reference.replace("\\", "/")
    if normalized in tracked or (ROOT / normalized).is_file():
        return normalized
    rust_candidate = f"core/rust/src/{normalized}"
    if rust_candidate in tracked:
        return rust_candidate
    rust_directory = f"core/rust/src/{normalized.rstrip('/')}"
    if (ROOT / rust_directory).is_dir() and any(path.startswith(f"{rust_directory}/") for path in tracked):
        return rust_directory
    basename_matches = sorted(path for path in tracked if Path(path).name == Path(normalized).name)
    if len(basename_matches) == 1:
        return basename_matches[0]
    if (ROOT / normalized).is_dir() and any(path.startswith(f"{normalized.rstrip('/')}/") for path in tracked):
        return normalized.rstrip("/")
    return None


def _parse_gt96() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in TRACEABILITY.read_text(encoding="utf-8").splitlines():
        if not line.startswith(("| GT96-", "| AESE-")):
            continue
        cells = _cells(line)
        if len(cells) != 10:
            raise ValueError(f"GT96 row must have 10 cells: {line}")
        identifier = cells[0]
        if TRACEABILITY_RE.fullmatch(identifier) is None:
            raise ValueError(f"invalid traceability identifier: {identifier}")
        rows.append(
            {
                "id": identifier,
                "requirement": cells[1],
                "owner": cells[2],
                "implementation": cells[3],
                "invariant": cells[4],
                "tests": cells[5],
                "benchmark": cells[6],
                "security_relevance": cells[7],
                "evidence_class": cells[8],
                "status": cells[9],
            }
        )
    if not rows:
        raise ValueError("GT96 traceability contains no rows")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("GT96 traceability contains duplicate identifiers")
    return rows


def _surface_nodes(
    inventory: dict[str, object], claim_rows: list[dict[str, str]], sources: dict[str, str]
) -> list[dict[str, object]]:
    raw_items = inventory.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("inventory items must be an array")
    items = [cast(dict[str, object], value) for value in raw_items if isinstance(value, dict)]
    paths_to_claims: dict[str, set[str]] = {}
    for row in claim_rows:
        claim_id = f"AESE-CLAIM-{row['id']}"
        for path in _implementation_paths(row["implementation"]):
            paths_to_claims.setdefault(path, set()).add(claim_id)
        # Verification paths are claim-bearing surfaces too.  Resolve only
        # explicit repository paths or exact symbols; unresolved prose stays
        # unmapped so preflight continues to widen conservatively.
        for field in ("tests", "benchmark"):
            for path in _resolve_verification_paths(row[field], sources):
                paths_to_claims.setdefault(path, set()).add(claim_id)
    nodes: list[dict[str, object]] = []
    for item in items:
        path = str(item["path"])
        claim_refs = sorted(paths_to_claims.get(path, set()))
        nodes.append(
            {
                "id": str(item["stable_id"]),
                "path": path,
                "target": item.get("target"),
                "kind": str(item["kind"]),
                "risk": item.get("risk"),
                "security_criticality": item.get("security_criticality"),
                "release_criticality": item.get("release_criticality"),
                "release_critical": item.get("release_critical"),
                "claim_refs": claim_refs,
                "mapping_status": "MAPPED_EXACT" if claim_refs else "NOT_MAPPED",
                "unknown_dependency_policy": "INVALIDATE_CONSERVATIVELY" if not claim_refs else None,
            }
        )
    return sorted(nodes, key=lambda node: str(node["id"]))


def _mapping_state(surfaces: list[dict[str, object]]) -> tuple[str, dict[str, object]]:
    """Derive graph status from mapping facts; unknown criticality is conservative."""

    unmapped_count = sum(1 for surface in surfaces if surface["mapping_status"] != "MAPPED_EXACT")
    criticality_keys = ("risk", "security_criticality", "release_criticality", "release_critical")
    criticality_known = all(
        any(str(surface.get(key, "UNKNOWN")).strip().upper() not in {"", "UNKNOWN", "NONE", "NULL"} for key in criticality_keys)
        for surface in surfaces
    )
    high_risk = [
        surface
        for surface in surfaces
        if any(str(surface.get(key, "")).strip().upper() in _CRITICAL_VALUES for key in criticality_keys)
    ]
    critical_high_risk_mapped = criticality_known and all(
        surface["mapping_status"] == "MAPPED_EXACT" for surface in high_risk
    )
    all_surfaces_mapped = unmapped_count == 0
    if all_surfaces_mapped:
        status = "SHADOW_GRAPH_MAPPING_COMPLETE_SELECTION_DISABLED"
    elif critical_high_risk_mapped:
        status = "SHADOW_GRAPH_CRITICAL_MAPPING_COMPLETE_SELECTION_DISABLED"
    else:
        status = "SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED"
    return status, {
        "all_surfaces_mapped": all_surfaces_mapped,
        "critical_high_risk_mapped": critical_high_risk_mapped,
        "criticality_known": criticality_known,
        "unmapped_surface_count": unmapped_count,
    }


def _claim_nodes(
    claim_rows: list[dict[str, str]], tracked: set[str], sources: dict[str, str]
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, str]],
]:
    claims: list[dict[str, object]] = []
    contracts: list[dict[str, object]] = []
    invariants: list[dict[str, object]] = []
    verifications: list[dict[str, object]] = []
    code_nodes_by_path: dict[str, dict[str, object]] = {}
    edges: list[dict[str, str]] = []
    for row in claim_rows:
        claim_id = f"AESE-CLAIM-{row['id']}"
        contract_id = f"AESE-CONTRACT-{row['id']}"
        invariant_id = f"AESE-INVARIANT-{_sha256(row['invariant'].encode())[:16].upper()}"
        evidence_id = f"AESE-EVIDENCE-{row['id']}"
        claims.append(
            {
                "id": claim_id,
                "source_id": row["id"],
                "requirement": row["requirement"],
                "owner": row["owner"],
                "security_relevance": row["security_relevance"],
                "evidence_class": row["evidence_class"],
                "status": row["status"],
            }
        )
        contracts.append(
            {
                "id": contract_id,
                "source_id": row["id"],
                "implementation": row["implementation"],
                "source_paths": [],
                "owner": row["owner"],
                "mapping_status": "SOURCE_BACKED",
            }
        )
        source_paths: list[str] = []
        for reference in _implementation_paths(row["implementation"]):
            canonical = _canonical_source_path(reference, tracked)
            if canonical is not None:
                source_paths.append(canonical)
                code_nodes_by_path.setdefault(
                    canonical,
                    {
                        "id": f"AESE-CODE-{_sha256(canonical.encode())[:16].upper()}",
                        "path": canonical,
                        "tracked": canonical in tracked,
                        "exists": (ROOT / canonical).exists(),
                        "mapping_status": "MAPPED_EXACT",
                    },
                )
        contracts[-1]["source_paths"] = sorted(set(source_paths))
        edges.extend(
            {
                "from": str(code_nodes_by_path[path]["id"]),
                "to": contract_id,
                "relation": "IMPLEMENTS",
            }
            for path in sorted(set(source_paths))
        )
        invariants.append(
            {
                "id": invariant_id,
                "source_id": row["id"],
                "description": row["invariant"],
                "mapping_status": "SOURCE_BACKED",
            }
        )
        for kind, text in (("TEST_OR_PROOF", row["tests"]), ("BENCHMARK", row["benchmark"])):
            verification_id = f"AESE-{kind}-{row['id']}"
            resolved_paths = _resolve_verification_paths(text, sources)
            verifications.append(
                {
                    "id": verification_id,
                    "source_id": row["id"],
                    "kind": kind,
                    "reference": text,
                    "resolved_paths": resolved_paths,
                    "mapping_status": "MAPPED_EXACT" if resolved_paths else "NOT_MAPPED",
                }
            )
            edges.append({"from": invariant_id, "to": verification_id, "relation": "VERIFIED_BY_REFERENCE"})
            edges.append({"from": verification_id, "to": evidence_id, "relation": "MATERIALIZES"})
        edges.extend(
            [
                {"from": contract_id, "to": invariant_id, "relation": "GUARDS"},
                {"from": evidence_id, "to": claim_id, "relation": "SUPPORTS_OR_LEAVES_UNVERIFIED"},
            ]
        )
    return (
        claims,
        contracts,
        invariants,
        verifications,
        sorted(code_nodes_by_path.values(), key=lambda node: str(node["id"])),
        edges,
    )


def _blocker_nodes() -> list[dict[str, object]]:
    raw_entries = _load_json(NOT_VERIFIED).get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("not-verified registry entries must be an array")
    nodes: list[dict[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise ValueError("not-verified registry entry must be an object")
        identifier = str(entry.get("id", "")).strip()
        title = str(entry.get("title", "")).strip()
        status = str(entry.get("status", "")).strip()
        if not identifier or not title or not status:
            raise ValueError("not-verified registry entries require id, title and status")
        nodes.append(
            {
                "id": f"AESE-BLOCKER-{identifier}",
                "source_id": identifier,
                "title": title,
                "status": status,
                "classification": "FUTURE_ANCHOR_OR_RELEASE_OBLIGATION",
            }
        )
    return sorted(nodes, key=lambda node: str(node["id"]))


def build_graph() -> dict[str, object]:
    inventory = build_inventory()
    claim_rows = _parse_gt96()
    tracked = _tracked_paths()
    sources = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in tracked
        if Path(path).suffix.lower() in {".py", ".rs"}
    }
    surfaces = _surface_nodes(inventory, claim_rows, sources)
    claims, contracts, invariants, verifications, code_nodes, edges = _claim_nodes(claim_rows, tracked, sources)
    blockers = _blocker_nodes()
    for surface in surfaces:
        for claim_id in cast(list[object], surface["claim_refs"]):
            edges.append({"from": str(surface["id"]), "to": str(claim_id), "relation": "IMPLEMENTS_OR_EXERCISES"})
    surface_ids_by_path: dict[str, list[str]] = {}
    for surface in surfaces:
        surface_ids_by_path.setdefault(str(surface["path"]), []).append(str(surface["id"]))
    for verification in verifications:
        for path in cast(list[object], verification["resolved_paths"]):
            for surface_id in surface_ids_by_path.get(str(path), []):
                edges.append(
                    {
                        "from": surface_id,
                        "to": str(verification["id"]),
                        "relation": "RESOLVES_REFERENCE",
                    }
                )
    edges = sorted(
        {(edge["from"], edge["to"], edge["relation"]): edge for edge in edges}.values(),
        key=lambda edge: (edge["from"], edge["to"], edge["relation"]),
    )
    mapped_surfaces = sum(1 for surface in surfaces if surface["mapping_status"] == "MAPPED_EXACT")
    status, mapping_condition = _mapping_state(surfaces)
    unresolved_verifications = sum(
        1 for verification in verifications if verification["mapping_status"] == "NOT_MAPPED"
    )
    input_material = "\n".join(
        [
            str(inventory["source_tree_sha256"]),
            _code_source_material(code_nodes),
            _file_sha256(TRACEABILITY),
            _file_sha256(NOT_VERIFIED),
        ]
    ).encode()
    head = str(inventory["source_head"])
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "mode": MODE,
        "status": status,
        "mapping_condition": mapping_condition,
        "direct_cutover": "PROHIBITED",
        "source_head": head,
        "source_tree_sha256": _sha256(input_material),
        "worktree_epoch": str(inventory["worktree_epoch"]),
        "worktree_status": str(inventory["worktree_status"]),
        "inputs": {
            "inventory": "quality/registry/current_inventory.json",
            "traceability": TRACEABILITY.relative_to(ROOT).as_posix(),
            "not_verified_registry": NOT_VERIFIED.relative_to(ROOT).as_posix(),
        },
        "counts": {
            "surfaces": len(surfaces),
            "mapped_surfaces": mapped_surfaces,
            "unmapped_surfaces": len(surfaces) - mapped_surfaces,
            "claims": len(claims),
            "contracts": len(contracts),
            "invariants": len(invariants),
            "verifications": len(verifications),
            "unmapped_verifications": unresolved_verifications,
            "code_nodes": len(code_nodes),
            "unresolved_code_references": sum(
                1
                for row in claim_rows
                for reference in _implementation_paths(row["implementation"])
                if _canonical_source_path(reference, tracked) is None
            ),
            "future_obligations": len(blockers),
            "edges": len(edges),
        },
        "surfaces": surfaces,
        "code": code_nodes,
        "contracts": contracts,
        "invariants": invariants,
        "verifications": verifications,
        "evidence_obligations": [
            {
                "id": f"AESE-EVIDENCE-{row['id']}",
                "source_id": row["id"],
                "evidence_class": row["evidence_class"],
                "status": row["status"],
                "promotion": "DISABLED_IN_SHADOW",
            }
            for row in claim_rows
        ],
        "claims": claims,
        "future_obligations": blockers,
        "edges": edges,
        "limitations": [
            "GT96 rows are source-backed requirement records; they are not proof that the referenced tests or benchmarks ran.",
            "Symbol-only test and benchmark references remain NOT_MAPPED until resolved to concrete tracked files or runner jobs.",
            "Unmapped surfaces and dependencies require conservative invalidation in the future affected-closure phase.",
            "The graph cannot select, skip, promote or delete evidence while AESE_MODE is SHADOW.",
        ],
    }


def validate_graph(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    errors: list[str] = []
    keys = (
        "schema",
        "phase",
        "mode",
        "status",
        "mapping_condition",
        "direct_cutover",
        "source_tree_sha256",
        "counts",
        "inputs",
        "surfaces",
        "code",
        "contracts",
        "invariants",
        "verifications",
        "evidence_obligations",
        "claims",
        "future_obligations",
        "edges",
        "limitations",
    )
    errors.extend([f"{key} differs" for key in keys if actual.get(key) != expected.get(key)])
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the shadow graph drifts")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    expected = build_graph()
    if args.check:
        try:
            actual = _load_json(output)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"claim graph check failed: {exc}")
            return 1
        errors = validate_graph(actual, expected)
        if errors:
            print(json.dumps({"status": "DRIFT", "errors": errors}, sort_keys=True))
            return 1
        print(json.dumps({"status": "PASS", "counts": expected["counts"]}, sort_keys=True))
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": expected["status"], "counts": expected["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
