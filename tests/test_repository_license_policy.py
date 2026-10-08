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
        ROOT / "docs" / "architecture" / "README.md",
    )

    for path in document_paths:
        content = path.read_text(encoding="utf-8")
        assert "BUSL-1.1" not in content

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "No permission is" in readme
    assert "LICENSE.txt" in readme


def test_desktop_bundle_includes_regex_license_notice() -> None:
    import json

    bundle = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.bundle.conf.json").read_text(encoding="utf-8"))
    resources = bundle["bundle"]["resources"]
    assert resources["resources/aegis-desktop-service*"] == "resources/"
    assert resources["../THIRD_PARTY_NOTICES.md"] == "THIRD_PARTY_NOTICES.md"

    notices = (ROOT / "desktop" / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "regex==2026.9.29" in notices
    assert "Apache-2.0 AND CNRI-Python" in notices
    assert "Apache License" in notices
    assert "licensed under CNRI's Python 1.6" in notices
