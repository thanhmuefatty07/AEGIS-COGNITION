from __future__ import annotations

import json

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
        "requirements": [{"id": "TEST-001", "tests": "unit", "evidence_ids": ["LOCAL-001"]}],
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
                "toolchain": "rustc 1.97.1",
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
