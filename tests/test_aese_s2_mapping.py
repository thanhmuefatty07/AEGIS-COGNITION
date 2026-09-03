from __future__ import annotations

import json
from pathlib import Path

from scripts.aese_s2_mapping import DEFAULT_INVENTORY, DEFAULT_OUTPUT, DEFAULT_GRAPH, DEFAULT_SEED, build_mapping


def test_s2_mapping_closes_critical_and_high_selection_surfaces() -> None:
    report = build_mapping()
    assert report["mapping_status"] == "COMPLETE"
    assert report["all_critical_mapped"] is True
    assert report["all_high_selection_relevant_mapped"] is True
    assert report["no_fake_mapping"] is True
    assert report["unknown_dependency_policy"] == "WIDEN_CONSERVATIVELY"
    assert report["unknown_surface_count"] == 112
    assert report["claim_graph_status"] == "SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED"


def test_recorded_s2_mapping_has_no_content_drift() -> None:
    actual = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    expected = build_mapping()
    for key in ("schema", "phase", "mode", "selection_authority", "mapping_status", "all_critical_mapped", "all_high_selection_relevant_mapped", "unknown_surface_ids", "records", "errors_by_surface"):
        assert actual[key] == expected[key]


def test_s2_mapping_rejects_file_name_only_test_subject(tmp_path: Path) -> None:
    seed = json.loads(DEFAULT_SEED.read_text(encoding="utf-8"))
    seed["records"][0]["test_subjects"][0] = "tests/test_aese_primitives.py"
    mutated = tmp_path / "mapping.json"
    mutated.write_text(json.dumps(seed), encoding="utf-8")
    report = build_mapping(mutated, DEFAULT_INVENTORY, DEFAULT_GRAPH)
    assert report["mapping_status"] == "INSUFFICIENT_EVIDENCE"
    assert report["no_fake_mapping"] is False
    assert report["errors_by_surface"]
