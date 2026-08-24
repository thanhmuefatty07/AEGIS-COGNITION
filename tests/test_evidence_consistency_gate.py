from __future__ import annotations

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
            {"id": "TEST-001", "tests": "unit", "evidence_ids": ["LOCAL-001"]}
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
