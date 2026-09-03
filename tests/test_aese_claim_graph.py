from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import scripts.aese_claim_graph as claim_graph
from scripts.aese_claim_graph import DEFAULT_OUTPUT, build_graph, validate_graph


def test_shadow_graph_preserves_inventory_and_disables_selection() -> None:
    graph = build_graph()
    assert graph["mode"] == "SHADOW"
    assert graph["status"] == "SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED"
    assert graph["mapping_condition"] == {
        "all_surfaces_mapped": False,
        "critical_high_risk_mapped": False,
        "criticality_known": False,
        "unmapped_surface_count": 120,
    }
    assert graph["direct_cutover"] == "PROHIBITED"
    assert graph["counts"]["surfaces"] == graph["counts"]["mapped_surfaces"] + graph["counts"]["unmapped_surfaces"]
    assert graph["counts"]["surfaces"] == 129
    assert graph["counts"]["mapped_surfaces"] == 9
    assert graph["counts"]["unmapped_surfaces"] == 120
    assert graph["counts"]["unmapped_verifications"] == 70
    assert graph["counts"]["claims"] == 46
    assert graph["counts"]["code_nodes"] == 27
    assert graph["counts"]["unresolved_code_references"] == 0
    assert graph["counts"]["future_obligations"] == 20


def test_claim_graph_complete_status_requires_mapping_condition() -> None:
    graph = build_graph()
    if "MAPPING_COMPLETE" in str(graph["status"]):
        condition = graph["mapping_condition"]
        assert condition["all_surfaces_mapped"] or condition["critical_high_risk_mapped"]


def test_shadow_graph_uses_stable_ids_and_conservative_unknowns() -> None:
    graph = build_graph()
    surfaces = graph["surfaces"]
    assert isinstance(surfaces, list)
    assert len({str(item["id"]) for item in surfaces}) == len(surfaces)
    assert all(item["mapping_status"] in {"MAPPED_EXACT", "NOT_MAPPED"} for item in surfaces)
    assert all(
        item["unknown_dependency_policy"] == "INVALIDATE_CONSERVATIVELY"
        for item in surfaces
        if item["mapping_status"] == "NOT_MAPPED"
    )


def test_graph_digest_binds_resolved_implementation_source(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = build_graph()
    original_file_sha256 = claim_graph._file_sha256

    def altered_digest(path: Path) -> str:
        if path.as_posix().endswith("aegis_cognition/aese.py"):
            return "f" * 64
        return original_file_sha256(path)

    monkeypatch.setattr(claim_graph, "_file_sha256", altered_digest)
    altered = build_graph()

    assert altered["source_tree_sha256"] != baseline["source_tree_sha256"]


def test_recorded_shadow_graph_has_no_drift() -> None:
    actual = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert validate_graph(actual, build_graph()) == []


def test_claim_graph_direct_entrypoint_works_like_ci_invocation() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/aese_claim_graph.py", "--check"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
