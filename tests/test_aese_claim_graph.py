from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import scripts.aese_claim_graph as claim_graph
from scripts.aese_inventory import build_inventory
from scripts.aese_claim_graph import DEFAULT_OUTPUT, build_graph, validate_graph


def test_shadow_graph_preserves_inventory_and_disables_selection() -> None:
    graph = build_graph()
    surfaces = graph["counts"]["surfaces"]
    mapped_surfaces = graph["counts"]["mapped_surfaces"]
    unmapped_surfaces = graph["counts"]["unmapped_surfaces"]
    assert graph["mode"] == "SHADOW"
    assert graph["status"] == "SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED"
    assert graph["mapping_condition"] == {
        "all_surfaces_mapped": False,
        "critical_high_risk_mapped": False,
        "criticality_known": False,
        "unmapped_surface_count": unmapped_surfaces,
    }
    assert graph["direct_cutover"] == "PROHIBITED"
    assert graph["counts"]["surfaces"] == graph["counts"]["mapped_surfaces"] + graph["counts"]["unmapped_surfaces"]
    assert graph["counts"]["surfaces"] == surfaces
    assert graph["counts"]["mapped_surfaces"] == mapped_surfaces
    assert graph["counts"]["unmapped_surfaces"] == unmapped_surfaces
    assert graph["counts"]["unmapped_verifications"] == 35
    assert graph["counts"]["claims"] == 46
    assert graph["counts"]["code_nodes"] == 31
    assert graph["counts"]["unresolved_code_references"] == 0
    assert graph["counts"]["future_obligations"] == 20

    verifications = {str(item["id"]): item for item in graph["verifications"]}
    expected_test_paths = {
        "AESE-TEST_OR_PROOF-GT96-001": {"core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-002": {"core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-003": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-004": {
            "core/rust/src/gt96.rs",
            "core/rust/src/lab.rs",
            "tests/test_goal_contract.py",
        },
        "AESE-TEST_OR_PROOF-GT96-005": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-006": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-008": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-009": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-011": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-012": {"core/rust/src/resource.rs", "core/rust/src/runtime.rs"},
        "AESE-TEST_OR_PROOF-GT96-013": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-014": {"core/rust/src/resource.rs"},
        "AESE-TEST_OR_PROOF-GT96-015": {"core/rust/src/resource.rs", "core/rust/src/runtime.rs"},
        "AESE-TEST_OR_PROOF-GT96-016": {"core/rust/src/runtime.rs", "core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-017": {"core/rust/src/resource.rs"},
        "AESE-TEST_OR_PROOF-GT96-018": {"core/rust/src/runtime.rs"},
        "AESE-TEST_OR_PROOF-GT96-010": {
            "core/rust/src/lab.rs",
            "tests/test_goal_contract.py",
        },
        "AESE-TEST_OR_PROOF-GT96-022": {"core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-023": {
            "core/rust/src/context.rs",
            "core/rust/src/memory/repository.rs",
        },
        "AESE-TEST_OR_PROOF-GT96-024": {"core/rust/src/telemetry.rs", "core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-025": {"core/rust/src/eac/cache/semantic.rs"},
        "AESE-TEST_OR_PROOF-GT96-026": {"core/rust/src/memory/repository.rs"},
        "AESE-TEST_OR_PROOF-GT96-027": {"core/rust/src/telemetry.rs", "core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-029": {"core/rust/src/resource.rs", "core/rust/src/runtime.rs"},
        "AESE-TEST_OR_PROOF-GT96-031": {"core/rust/src/replay.rs"},
        "AESE-TEST_OR_PROOF-GT96-032": {"core/rust/src/replay.rs", "core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-033": {"core/python/tests.py"},
        "AESE-TEST_OR_PROOF-GT96-034": {"tests/test_connections.py"},
        "AESE-TEST_OR_PROOF-GT96-019": {
            "core/rust/src/lab.rs",
            "tests/test_goal_contract.py",
        },
        "AESE-TEST_OR_PROOF-GT96-020": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-021": {"core/rust/src/tests.rs"},
        "AESE-TEST_OR_PROOF-GT96-030": {"core/rust/src/gt96.rs"},
        "AESE-TEST_OR_PROOF-GT96-035": {"core/rust/src/eac/cache/semantic.rs"},
    }
    for verification_id, expected_paths in expected_test_paths.items():
        verification = verifications[verification_id]
        assert verification["mapping_status"] == "MAPPED_EXACT"
        assert set(verification["resolved_paths"]) == expected_paths

    for requirement_id in ("GT96-007", "GT96-028"):
        verification = verifications[f"AESE-TEST_OR_PROOF-{requirement_id}"]
        assert verification["mapping_status"] == "NOT_MAPPED"
        assert verification["resolved_paths"] == []

    expected_benchmark_paths = {
        "AESE-BENCHMARK-GT96-002": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-012": {"core/rust/benches/resource_runtime.rs"},
        "AESE-BENCHMARK-GT96-013": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-030": {"core/rust/benches/resource_runtime.rs"},
        "AESE-BENCHMARK-GT96-017": {"core/rust/benches/resource_runtime.rs"},
        "AESE-BENCHMARK-GT96-018": {"core/rust/benches/resource_runtime.rs"},
        "AESE-BENCHMARK-GT96-022": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-001": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-005": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-011": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-024": {"core/rust/benches/architecture_performance.rs"},
        "AESE-BENCHMARK-GT96-032": {"core/rust/benches/nerve_bench.rs"},
        "AESE-BENCHMARK-GT96-033": {
            "core/rust/benches/nerve_bench.rs",
            "scripts/communication_payload_benchmark.py",
        },
    }
    for verification_id, expected_paths in expected_benchmark_paths.items():
        benchmark = verifications[verification_id]
        assert benchmark["mapping_status"] == "MAPPED_EXACT"
        assert set(benchmark["resolved_paths"]) == expected_paths

    mapped_benchmarks = {
        verification_id
        for verification_id, benchmark in verifications.items()
        if verification_id.startswith("AESE-BENCHMARK-GT96-") and benchmark["mapping_status"] == "MAPPED_EXACT"
    }
    assert mapped_benchmarks == set(expected_benchmark_paths)

    for verification_id, benchmark in verifications.items():
        if verification_id.startswith("AESE-BENCHMARK-GT96-") and verification_id not in expected_benchmark_paths:
            assert benchmark["mapping_status"] == "NOT_MAPPED"
            assert benchmark["resolved_paths"] == []


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


def test_precomputed_inventory_preserves_direct_graph_semantics() -> None:
    precomputed = build_graph(inventory=build_inventory())
    direct = build_graph()
    assert precomputed == direct


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


def test_verification_symbol_resolution_scans_each_source_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = {
        "src/alpha.py": "def alpha():\n    pass\n",
        "src/beta.rs": "fn beta() {}\n",
        "src/gamma.py": "def gamma():\n    pass\n",
        "src/unrelated.py": "def unrelated():\n    pass\n",
    }
    original_compile = claim_graph.re.compile
    definition_searches = 0

    class SearchCountingPattern:
        def __init__(self, pattern: str) -> None:
            self._pattern = original_compile(pattern)

        def search(self, value: str):
            nonlocal definition_searches
            definition_searches += 1
            return self._pattern.search(value)

    def compile_with_search_count(pattern: str, *args, **kwargs):
        compiled = original_compile(pattern, *args, **kwargs)
        if pattern.startswith(r"\b(?:def|fn)\s+"):
            return SearchCountingPattern(pattern)
        return compiled

    monkeypatch.setattr(claim_graph.re, "compile", compile_with_search_count)
    resolved = claim_graph._resolve_verification_paths(
        "`alpha` and `beta` and `gamma` and `missing`",
        sources,
    )

    assert resolved == ["src/alpha.py", "src/beta.rs", "src/gamma.py"]
    assert definition_searches <= len(sources)
