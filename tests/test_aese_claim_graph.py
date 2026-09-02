from __future__ import annotations

import json

from scripts.aese_claim_graph import DEFAULT_OUTPUT, build_graph, validate_graph


def test_shadow_graph_preserves_inventory_and_disables_selection() -> None:
    graph = build_graph()
    assert graph["mode"] == "SHADOW"
    assert graph["direct_cutover"] == "PROHIBITED"
    assert graph["counts"]["surfaces"] == graph["counts"]["mapped_surfaces"] + graph["counts"]["unmapped_surfaces"]
    assert graph["counts"]["surfaces"] == 115
    assert graph["counts"]["mapped_surfaces"] == 1
    assert graph["counts"]["unmapped_verifications"] == 59
    assert graph["counts"]["claims"] == 35
    assert graph["counts"]["code_nodes"] == 23
    assert graph["counts"]["unresolved_code_references"] == 0
    assert graph["counts"]["future_obligations"] == 20


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


def test_recorded_shadow_graph_has_no_drift() -> None:
    actual = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert validate_graph(actual, build_graph()) == []
