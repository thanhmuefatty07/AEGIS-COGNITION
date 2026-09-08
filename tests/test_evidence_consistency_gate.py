from __future__ import annotations

import json
from pathlib import Path

from scripts import evidence_consistency_gate as gate
from scripts.evidence_consistency_gate import validate_manifest


def manifest(commit: str) -> dict[str, object]:
    return {
        "schema": "aegis-evidence-manifest-v1",
        "branch": "main",
        "commit": commit,
        "evidence": [
            {
                "id": "LOCAL-001",
                "kind": "local",
                "status": "PROVEN",
                "head_sha": commit,
            }
        ],
        "requirements": [
            {"id": "TEST-001", "tests": "unit", "evidence_ids": ["LOCAL-001"]},
            *[
                {"id": f"GT96-{index:03d}", "tests": "traceability fixture", "evidence_ids": []}
                for index in range(1, 36)
            ],
        ],
        "remediation_requirements": [
            {
                "id": "TEST-REM-001",
                "priority": "P0",
                "implementation": "unit",
                "closure": "test",
                "evidence_ids": [],
                "evidence_class": "NOT VERIFIED",
                "status": "IMPLEMENTED / NOT VERIFIED",
                "final_sha": commit,
            }
        ],
        "suites": [
            {
                "name": "unit",
                "command": "pytest",
                "commit": commit,
                "timestamp_utc": "2026-01-01T00:00:00Z",
                "platform": "test",
                "toolchain": "test",
                "discovered": 1,
                "passed": 1,
                "failed": 0,
                "ignored": 0,
                "filtered": 0,
            }
        ],
    }


def test_manifest_rejects_stale_sha() -> None:
    errors = validate_manifest(manifest("a" * 40), "b" * 40)
    assert any("does not match" in error for error in errors)


def test_manifest_accepts_matching_sha_without_run_for_local() -> None:
    assert validate_manifest(manifest("a" * 40), "a" * 40, verification_index_text="a" * 40) == []


def test_registry_parity_rejects_dropped_and_policy_unknown_ids(monkeypatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "not_verified_registry.json"
    policy_path = tmp_path / "deployment_policy.json"
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {"id": "NV-001", "title": "one"},
                    {"id": "NV-002", "title": "two"},
                ]
            }
        ),
        encoding="utf-8",
    )
    policy_path.write_text(
        json.dumps({"blockers": [{"registry_ids": ["NV-002", "NV-999"]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "NOT_VERIFIED_REGISTRY", registry_path)
    monkeypatch.setattr(gate, "DEPLOYMENT_POLICY", policy_path)
    errors = gate.validate_registry_parity({"not_verified_ids": ["NV-001"]})
    assert any("do not match registry" in error for error in errors)
    assert any("unknown registry IDs" in error for error in errors)
    assert any("missing from evidence manifest" in error for error in errors)


def test_materializer_binds_retained_suite_artifact(monkeypatch, tmp_path) -> None:
    suite_dir = tmp_path / "artifacts" / "suites"
    suite_dir.mkdir(parents=True)
    (suite_dir / "rust.json").write_text(
        json.dumps(
            {
                "schema": "aegis-suite-evidence-v1",
                "name": "rust-full-workspace-final",
                "command": "cargo nextest run --workspace",
                "commit": "a" * 40,
                "timestamp_utc": "2026-01-01T00:00:00Z",
                "platform": "Windows",
                "toolchain": "rustc 1.98.1",
                "discovered": 2,
                "passed": 2,
                "failed": 0,
                "ignored": 0,
                "filtered": 0,
                "status": "PROVEN",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    template = {
        "commit": "CHECKOUT_HEAD",
        "generated_at_utc": "old",
        "evidence": [
            {
                "id": "LOCAL-RUST-001",
                "kind": "local",
                "source_artifact": "rust-full-workspace",
                "head_sha": "CHECKOUT_HEAD",
                "status": "NOT VERIFIED",
                "evidence_class": "NOT VERIFIED",
            }
        ],
        "suites": [
            {
                "name": "rust-full-workspace",
                "command": "old",
                "commit": "CHECKOUT_HEAD",
                "timestamp_utc": "old",
                "platform": "old",
                "toolchain": "old",
                "discovered": None,
                "passed": None,
                "failed": None,
                "ignored": None,
                "filtered": None,
            }
        ],
        "requirements": [
            {
                "id": "GT96-LOCAL",
                "tests": "retained suite",
                "evidence_ids": ["LOCAL-RUST-001"],
                "status": "IMPLEMENTED / NOT VERIFIED",
                "evidence_class": "NOT VERIFIED",
            }
        ],
    }

    materialized = gate.materialize_for_head(template, "a" * 40)
    assert materialized["evidence"][0]["status"] == "PROVEN"
    assert materialized["suites"][0]["passed"] == 2
    assert materialized["requirements"][0]["status"] == "PROVEN"


def test_materializer_sources_all_blocker_ids_from_registry(monkeypatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "not_verified_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {"id": "NV-001", "title": "one"},
                    {"id": "NV-019", "title": "nineteen"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "NOT_VERIFIED_REGISTRY", registry_path)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    generated = gate.materialize_for_head(
        {"commit": "CHECKOUT_HEAD", "evidence": [], "suites": []},
        "a" * 40,
    )
    assert generated["not_verified_ids"] == ["NV-001", "NV-019"]


def test_checkout_registry_inventory_has_three_way_parity() -> None:
    manifest = gate.load_manifest(gate.MANIFEST)
    assert gate.validate_registry_parity(manifest) == []


def test_registry_markdown_statuses_cannot_drift_from_machine_registry(monkeypatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "not_verified_registry.json"
    policy_path = tmp_path / "deployment_policy.json"
    markdown_path = tmp_path / "docs" / "architecture" / "NOT_VERIFIED_REGISTRY.md"
    markdown_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {"id": "NV-001", "title": "one", "status": "NOT VERIFIED"},
                ]
            }
        ),
        encoding="utf-8",
    )
    policy_path.write_text(json.dumps({"blockers": []}), encoding="utf-8")
    markdown_path.write_text(
        "| ID | Area | Reason | Risk | Required | Owner | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| NV-001 | Runtime | reason | risk | closure | owner | PROVEN |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "NOT_VERIFIED_REGISTRY", registry_path)
    monkeypatch.setattr(gate, "DEPLOYMENT_POLICY", policy_path)
    errors = gate.validate_registry_parity({"not_verified_ids": ["NV-001"]})
    assert any("statuses do not match" in error for error in errors)


def test_gt96_traceability_statuses_cannot_drift_from_current_evidence(monkeypatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "not_verified_registry.json"
    policy_path = tmp_path / "deployment_policy.json"
    evidence_path = tmp_path / "docs" / "architecture" / "evidence" / "current.json"
    blocker_markdown = tmp_path / "docs" / "architecture" / "NOT_VERIFIED_REGISTRY.md"
    traceability_path = tmp_path / "docs" / "architecture" / "GT96_TRACEABILITY.md"
    evidence_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({"entries": [{"id": "NV-001", "status": "NOT VERIFIED"}]}), encoding="utf-8")
    policy_path.write_text(json.dumps({"blockers": []}), encoding="utf-8")
    blocker_markdown.write_text(
        "| ID | Area | Reason | Risk | Required | Owner | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| NV-001 | Runtime | reason | risk | closure | owner | NOT VERIFIED |\n",
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps({"requirements": [{"id": "GT96-001", "status": "PROVEN"}]}),
        encoding="utf-8",
    )
    traceability_path.write_text(
        "| ID | Requirement | Status |\n"
        "|---|---|---|\n"
        "| GT96-001 | fixture | IMPLEMENTED / NOT VERIFIED |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "NOT_VERIFIED_REGISTRY", registry_path)
    monkeypatch.setattr(gate, "DEPLOYMENT_POLICY", policy_path)
    errors = gate.validate_registry_parity({"not_verified_ids": ["NV-001"]})
    assert any("GT96_TRACEABILITY.md statuses do not match" in error for error in errors)
