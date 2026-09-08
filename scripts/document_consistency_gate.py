"""Validate document metadata, Markdown links, and generated Lab status views."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "architecture" / "document_inventory.json"
REGISTRY = ROOT / "docs" / "architecture" / "not_verified_registry.json"
POLICY = ROOT / "docs" / "architecture" / "deployment_policy.json"
GENERATED_VIEW = ROOT / "docs" / "architecture" / "AEGIS_LAB_STATUS_GENERATED.md"
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
REQUIRED_METADATA = {
    "document_id",
    "document_type",
    "status",
    "authority",
    "applies_to_commit",
    "last_verified_at",
}
SKIP_DIRS = {".git", ".serena", ".venv", "target", "artifacts", "__pycache__"}
SKIP_SCHEMES = ("http://", "https://", "mailto:", "file:")


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return cast(dict[str, Any], value)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _derive_document_id(path: str, prefix: str) -> str:
    stem = Path(path).stem.upper()
    normalized = re.sub(r"[^A-Z0-9]+", "-", stem).strip("-")
    return f"{prefix}-{normalized}" if normalized else prefix


def _scoped_documents(root: Path, inventory: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    documents: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    scopes = inventory.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        return {}, ["document inventory scopes must be a non-empty array"]
    typed_scopes = cast(list[Any], scopes)
    for index, raw_value in enumerate(typed_scopes):
        if not isinstance(raw_value, dict):
            errors.append(f"document inventory scope[{index}] must be an object")
            continue
        raw_scope: dict[str, Any] = cast(dict[str, Any], raw_value)
        pattern = raw_scope.get("glob")
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append(f"document inventory scope[{index}] glob is missing")
            continue
        matches = [path for path in root.glob(pattern) if path.is_file()]
        if not matches:
            errors.append(f"document inventory scope has no files: {pattern}")
        for path in matches:
            relative = _relative(path, root)
            if relative in documents:
                errors.append(f"document inventory path is covered more than once: {relative}")
                continue
            prefix = str(raw_scope.get("id_prefix", "DOC"))
            metadata: dict[str, Any] = {
                "document_id": _derive_document_id(relative, prefix),
                "document_type": raw_scope.get("document_type"),
                "status": raw_scope.get("status"),
                "authority": raw_scope.get("authority"),
                "applies_to_commit": "CHECKOUT_HEAD",
                "last_verified_at": inventory.get("last_verified_at"),
            }
            if not all(isinstance(metadata[key], str) and metadata[key].strip() for key in REQUIRED_METADATA):
                errors.append(f"document inventory scope metadata is incomplete: {relative}")
            documents[relative] = metadata
    overrides = inventory.get("overrides", {})
    if not isinstance(overrides, dict):
        errors.append("document inventory overrides must be an object")
        overrides = {}
    typed_overrides = cast(dict[Any, Any], overrides)
    for relative, raw_value in typed_overrides.items():
        if not isinstance(relative, str) or not isinstance(raw_value, dict):
            errors.append("document inventory override must map a path to an object")
            continue
        raw_override: dict[str, Any] = cast(dict[str, Any], raw_value)
        path = root / relative
        if path.suffix.lower() == ".json" and path.exists():
            # Machine evidence is checked as metadata even though link lint is Markdown-only.
            documents.setdefault(relative, {})
        if relative not in documents:
            if path.exists() and path.is_file():
                documents[relative] = {
                    "document_id": _derive_document_id(relative, "DOC"),
                    "document_type": "artifact",
                    "status": "ACTIVE",
                    "authority": "machine_source",
                    "applies_to_commit": "CHECKOUT_HEAD",
                    "last_verified_at": inventory.get("last_verified_at"),
                }
            else:
                errors.append(f"document inventory override path does not exist: {relative}")
                continue
        documents[relative].update(raw_override)
    ids: dict[str, str] = {}
    for relative, metadata in documents.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"document inventory path does not exist: {relative}")
        missing_keys = [
            key for key in REQUIRED_METADATA
            if not isinstance(metadata.get(key), str) or not str(metadata[key]).strip()
        ]
        errors.extend(f"document metadata {key} missing: {relative}" for key in missing_keys)
        identifier = str(metadata.get("document_id", ""))
        previous = ids.get(identifier)
        if previous is not None:
            errors.append(f"duplicate document_id {identifier}: {previous} and {relative}")
        ids[identifier] = relative
    historical = inventory.get("historical_paths", ())
    if not isinstance(historical, list):
        errors.append("historical_paths must be an array")
    else:
        for raw_value in cast(list[Any], historical):
            raw_path = raw_value
            if not isinstance(raw_path, str) or not raw_path.strip():
                errors.append("historical path must be a non-empty string")
                continue
            path = root / raw_path
            if not path.is_file():
                errors.append(f"historical path does not exist: {raw_path}")
            if raw_path in documents:
                errors.append(f"path is both current and historical: {raw_path}")
    return documents, errors


def _markdown_files(root: Path) -> Iterable[Path]:
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".venv-")
        ]
        for filename in filenames:
            if filename.lower().endswith(".md"):
                yield Path(directory) / filename


def _link_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for source in _markdown_files(root):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"cannot read Markdown {source}: {error}")
            continue
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split("#", 1)[0].split("?", 1)[0].strip("<>")
            if not target or target.startswith(("#", *SKIP_SCHEMES)):
                continue
            candidate = (source.parent / target).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                errors.append(f"link escapes repository: {_relative(source, root)} -> {target}")
                continue
            if not candidate.exists():
                errors.append(f"broken link: {_relative(source, root)} -> {target}")
    return errors


def generate_status_view(root: Path) -> str:
    registry = _load(root / REGISTRY.relative_to(ROOT))
    policy = _load(root / POLICY.relative_to(ROOT))
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("not-verified registry entries must be a non-empty array")
    policy_by_registry: dict[str, tuple[str, bool]] = {}
    raw_blockers = policy.get("blockers", [])
    for raw_value in cast(list[Any], raw_blockers) if isinstance(raw_blockers, list) else []:
        if not isinstance(raw_value, dict):
            continue
        raw_blocker: dict[str, Any] = cast(dict[str, Any], raw_value)
        policy_id = str(raw_blocker.get("id", "")).strip()
        registry_ids = raw_blocker.get("registry_ids", ())
        for registry_id in cast(list[Any], registry_ids) if isinstance(registry_ids, list) else []:
            if isinstance(registry_id, str) and registry_id.strip():
                policy_by_registry[registry_id] = (policy_id, bool(raw_blocker.get("blocks_production")))
    raw_non_blocking = policy.get("non_blocking_registry_ids", ())
    non_blocking: set[str] = {
        item for item in cast(list[Any], raw_non_blocking) if isinstance(item, str)
    } if isinstance(raw_non_blocking, list) else set()
    rows: list[str] = []
    typed_entries = cast(list[Any], entries)
    for raw_value in sorted(
        typed_entries,
        key=lambda item: str(cast(dict[str, Any], item).get("id", "")) if isinstance(item, dict) else "",
    ):
        if not isinstance(raw_value, dict):
            raise ValueError("not-verified registry entry must be an object")
        raw_entry: dict[str, Any] = cast(dict[str, Any], raw_value)
        identifier = str(raw_entry.get("id", "")).strip()
        title = str(raw_entry.get("title", "")).strip()
        status = str(raw_entry.get("status", "")).strip()
        if not identifier or not title or not status:
            raise ValueError("registry entry requires id, title and status")
        if identifier in policy_by_registry:
            policy_id, blocks = policy_by_registry[identifier]
            blocking = "YES" if blocks else "NO"
            policy_label = policy_id or "classified"
        elif identifier in non_blocking:
            blocking = "NO"
            policy_label = "non_blocking_registry_ids"
        else:
            raise ValueError(f"registry id has no deployment-policy classification: {identifier}")
        safe_title = title.replace("|", "\\|")
        safe_status = status.replace("|", "\\|")
        rows.append(f"| {identifier} | {safe_title} | {safe_status} | {blocking} | {policy_label} |")
    return "\n".join(
        [
            "<!-- GENERATED FILE: scripts/document_consistency_gate.py; do not edit. -->",
            "# AEGIS Lab blocker status (generated)",
            "",
            "This view is generated from `not_verified_registry.json` and",
            "`deployment_policy.json`. It is a human-readable view only; the",
            "machine-readable registry remains authoritative.",
            "",
            "| ID | Title | Registry status | Blocks production | Policy classification |",
            "|---|---|---|---|---|",
            *rows,
            "",
        ]
    )


def validate_inventory(root: Path = ROOT) -> list[str]:
    inventory = _load(root / INVENTORY.relative_to(ROOT))
    errors: list[str] = []
    if inventory.get("schema") != "aegis-document-inventory-v1":
        errors.append("document inventory schema is invalid")
    source = inventory.get("source_of_truth")
    if source != "docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md":
        errors.append("document inventory source_of_truth must be the canonical Lab plan")
    documents, metadata_errors = _scoped_documents(root, inventory)
    errors.extend(metadata_errors)
    generated_relative = inventory.get("generated_view")
    if not isinstance(generated_relative, str) or not generated_relative.strip():
        errors.append("document inventory generated_view is missing")
    else:
        generated_path = root / generated_relative
        if not generated_path.is_file():
            errors.append(f"generated document view is missing: {generated_relative}")
        else:
            try:
                expected = generate_status_view(root)
                actual = generated_path.read_text(encoding="utf-8")
                if actual != expected:
                    errors.append("generated Lab status view is stale or hand-edited")
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
                errors.append(f"generated Lab status view cannot be validated: {error}")
    errors.extend(_link_errors(root))
    # A canonical plan with front matter must agree with the sidecar inventory.
    plan = root / "docs" / "architecture" / "AEGIS_LAB_RUNTIME_MASTER_PLAN.md"
    if plan.is_file() and "docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md" in documents:
        front_matter = plan.read_text(encoding="utf-8").split("---", 2)
        if len(front_matter) >= 3:
            for line in front_matter[1].splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip().strip('"')
                if key in REQUIRED_METADATA and value and value != str(documents["docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md"].get(key, "")):
                    errors.append(f"canonical plan metadata drift for {key}: {value}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true", help="rewrite the generated status view before checking")
    args = parser.parse_args()
    if args.generate:
        GENERATED_VIEW.write_text(generate_status_view(ROOT), encoding="utf-8")
    errors = validate_inventory(ROOT)
    payload = {
        "schema": "aegis-document-consistency-report-v1",
        "status": "PASS" if not errors else "REJECTED",
        "errors": errors,
        "generated_view": str(GENERATED_VIEW),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
