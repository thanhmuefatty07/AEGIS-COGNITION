"""Enforce the repository's proprietary licensing boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CHANGELOG.md",
    ROOT / "docs" / "architecture" / "AEGIS_CURRENT_ARCHITECTURE_TRUTH_AUDIT.md",
)
METADATA_FILES = (
    ROOT / "pyproject.toml",
    ROOT / "core" / "python" / "pyproject.toml",
    ROOT / "core" / "rust" / "Cargo.toml",
)


def main() -> None:
    license_path = ROOT / "LICENSE.txt"
    license_text = license_path.read_text(encoding="utf-8")
    required_license_markers = (
        "AEGIS-COGNITION PROPRIETARY LICENSE",
        "All rights reserved",
        "no license or other permission is granted",
        "prior written permission",
        "noncommercial",
    )
    for marker in required_license_markers:
        if marker not in license_text:
            raise SystemExit(f"license policy missing required marker: {marker}")

    for path in METADATA_FILES:
        content = path.read_text(encoding="utf-8")
        if "BUSL-1.1" in content:
            raise SystemExit(f"stale BUSL-1.1 declaration: {path.relative_to(ROOT)}")
        if "Proprietary" not in content and "license-file" not in content:
            raise SystemExit(f"metadata is not proprietary: {path.relative_to(ROOT)}")

    for path in ACTIVE_DOCUMENTS:
        content = path.read_text(encoding="utf-8")
        if "BUSL-1.1" in content or "Business Source License" in content:
            raise SystemExit(f"stale source-available license wording: {path.relative_to(ROOT)}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "LICENSE.txt" not in readme or "No permission is" not in readme:
        raise SystemExit("README does not expose the current permission boundary")

    print("license policy passed")


if __name__ == "__main__":
    main()
