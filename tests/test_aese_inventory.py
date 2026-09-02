from __future__ import annotations

import ntpath
import json
import subprocess
from pathlib import Path

from scripts.aese_inventory import (
    DEFAULT_OUTPUT,
    DISPOSITIONS,
    ROOT,
    RUST_TEST_RE,
    build_inventory,
    validate_inventory,
)


def test_inventory_covers_every_scoped_tracked_file() -> None:
    inventory = build_inventory()
    items = inventory["items"]
    assert isinstance(items, list)
    paths = {str(item["path"]) for item in items}
    identities = {(str(item["kind"]), str(item["path"]), str(item["target"])) for item in items}
    assert inventory["scope_counts"]["inventoried_items"] == len(identities)
    assert inventory["scope_counts"]["workflow_jobs"] == 11
    assert inventory["disposition_counts"] == {"RETAIN_UNCHANGED": len(items)}
    assert inventory["missing_scopes"] == []
    for prefix, _kind, suffixes in (
        ("tests", "PYTHON_TEST", (".py",)),
        ("core/rust/tests", "RUST_INTEGRATION_TEST", (".rs",)),
        ("core/rust/benches", "RUST_BENCHMARK", (".rs",)),
        ("fuzz/fuzz_targets", "FUZZ_TARGET", (".rs",)),
        ("core/rust/src", "RUST_UNIT_TEST", (".rs",)),
        ("scripts", "SCRIPT_OR_GATE", (".py",)),
        (".github/workflows", "HOSTED_WORKFLOW", (".yml", ".yaml")),
    ):
        expected = {
            path.replace("\\", "/")
            for path in subprocess.run(
                ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
            ).stdout.splitlines()
            if (path == prefix or path.startswith(f"{prefix}/"))
            and Path(path).suffix.lower() in suffixes
            and (prefix != "core/rust/src" or RUST_TEST_RE.search((ROOT / path).read_text(encoding="utf-8")))
        }
        assert expected <= paths


def test_inventory_assigns_one_safe_disposition_and_no_absolute_path() -> None:
    items = build_inventory()["items"]
    assert isinstance(items, list) and items
    for item in items:
        assert item["migration_disposition"] in DISPOSITIONS
        assert item["mapping_status"] == "NOT_MAPPED"
        assert not ntpath.isabs(str(item["path"]))
        assert item["replacement_id"] is None
        assert item["current_status"] == "NOT_VERIFIED"
        assert item["risk"] == "UNKNOWN"
        assert item["cost"] == "UNKNOWN"
        assert item["platform"] == "UNKNOWN"
        assert item["release_critical"] == "UNKNOWN"


def test_inventory_stable_ids_are_path_bound() -> None:
    items = build_inventory()["items"]
    assert isinstance(items, list)
    identities = {(str(item["kind"]), str(item["path"]), str(item["target"])) for item in items}
    assert len(identities) == len(items)
    assert len({str(item["stable_id"]) for item in items}) == len(items)


def test_recorded_inventory_has_no_scoped_file_drift() -> None:
    actual = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    expected = build_inventory()
    assert isinstance(actual, dict)
    assert validate_inventory(actual, expected) == []
