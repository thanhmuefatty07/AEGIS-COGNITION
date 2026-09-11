from __future__ import annotations

import json
from pathlib import Path

from scripts import document_consistency_gate as gate


def _fixture_tree(tmp_path: Path) -> None:
    architecture = tmp_path / "docs" / "architecture"
    architecture.mkdir(parents=True)
    (architecture / "AEGIS_LAB_RUNTIME_MASTER_PLAN.md").write_text(
        "---\n"
        "document_id: AEGIS-LAB-RUNTIME-MASTER-PLAN\n"
        "document_type: canonical_current_implementation_plan\n"
        "status: IN_EXECUTION\n"
        "authority: derived_from_checkout_and_evidence_manifest\n"
        "applies_to_commit: f9645caf6d17cee2023d52183990ffcf8317e456\n"
        "last_verified_at: 2026-08-27\n"
        "---\n\n[guide](guide.md)\n",
        encoding="utf-8",
    )
    (architecture / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (architecture / "not_verified_registry.json").write_text(
        json.dumps({"entries": [{"id": "NV-001", "title": "Linux", "status": "NOT VERIFIED"}]}),
        encoding="utf-8",
    )
    (architecture / "deployment_policy.json").write_text(
        json.dumps({"non_blocking_registry_ids": ["NV-001"], "blockers": []}),
        encoding="utf-8",
    )
    (architecture / "document_inventory.json").write_text(
        json.dumps(
            {
                "schema": "aegis-document-inventory-v1",
                "source_of_truth": "docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md",
                "generated_view": "docs/architecture/AEGIS_LAB_STATUS_GENERATED.md",
                "last_verified_at": "2026-08-27",
                "scopes": [
                    {
                        "glob": "docs/architecture/*.md",
                        "document_type": "architecture",
                        "authority": "architecture",
                        "status": "ACTIVE",
                        "id_prefix": "ARCH",
                    }
                ],
                "overrides": {
                    "docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md": {
                        "document_id": "AEGIS-LAB-RUNTIME-MASTER-PLAN",
                        "document_type": "canonical_current_implementation_plan",
                        "status": "IN_EXECUTION",
                        "authority": "derived_from_checkout_and_evidence_manifest",
                        "applies_to_commit": "f9645caf6d17cee2023d52183990ffcf8317e456",
                    }
                },
                "historical_paths": [],
            }
        ),
        encoding="utf-8",
    )


def test_generated_view_and_metadata_are_consistent(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    (tmp_path / "docs" / "architecture" / "AEGIS_LAB_STATUS_GENERATED.md").write_text(
        gate.generate_status_view(tmp_path), encoding="utf-8"
    )
    assert gate.validate_inventory(tmp_path) == []


def test_generated_view_drift_is_rejected(tmp_path: Path) -> None:
    _fixture_tree(tmp_path)
    generated = tmp_path / "docs" / "architecture" / "AEGIS_LAB_STATUS_GENERATED.md"
    generated.write_text("# hand edited\n", encoding="utf-8")
    errors = gate.validate_inventory(tmp_path)
    assert any("generated Lab status view is stale" in error for error in errors)


def test_broken_markdown_link_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.md"
    source.write_text("[missing](missing.md)\n", encoding="utf-8")
    assert gate._link_errors(tmp_path) == ["broken link: broken.md -> missing.md"]


def test_ephemeral_uv_environment_markdown_is_ignored(tmp_path: Path) -> None:
    package_docs = tmp_path / ".venv-3.14.7" / "Lib" / "site-packages"
    package_docs.mkdir(parents=True)
    (package_docs / "third_party.md").write_text(
        "[package-local](missing-package-doc.md)\n", encoding="utf-8"
    )
    assert gate._link_errors(tmp_path) == []


def test_generated_node_modules_markdown_is_ignored(tmp_path: Path) -> None:
    package_docs = tmp_path / "node_modules" / "third-party"
    package_docs.mkdir(parents=True)
    (package_docs / "README.md").write_text("[package-local](missing-package-doc.md)\n", encoding="utf-8")

    assert gate._link_errors(tmp_path) == []
