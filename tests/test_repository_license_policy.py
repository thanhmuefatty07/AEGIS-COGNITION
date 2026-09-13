"""Regression checks for the repository's proprietary licensing boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_proprietary_license_is_explicit_and_permission_gated() -> None:
    license_text = (ROOT / "LICENSE.txt").read_text(encoding="utf-8")

    assert "AEGIS-COGNITION PROPRIETARY LICENSE" in license_text
    assert "All rights reserved" in license_text
    assert "no license or other permission is granted" in license_text
    assert "prior written permission" in license_text
    assert "noncommercial" in license_text


def test_active_metadata_uses_proprietary_policy() -> None:
    metadata_paths = (
        ROOT / "pyproject.toml",
        ROOT / "core" / "python" / "pyproject.toml",
        ROOT / "core" / "rust" / "Cargo.toml",
    )

    for path in metadata_paths:
        content = path.read_text(encoding="utf-8")
        assert "BUSL-1.1" not in content
        assert "Proprietary" in content or "license-file" in content


def test_active_project_docs_do_not_grant_default_use() -> None:
    document_paths = (
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs" / "architecture" / "AEGIS_CURRENT_ARCHITECTURE_TRUTH_AUDIT.md",
    )

    for path in document_paths:
        content = path.read_text(encoding="utf-8")
        assert "BUSL-1.1" not in content

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "No permission is" in readme
    assert "LICENSE.txt" in readme
