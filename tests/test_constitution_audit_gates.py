from __future__ import annotations

import json
from pathlib import Path

from scripts.constitution_audit import _no_overclaim_gate, _truth_schema_gate, evaluate_constitution


def _write_truth_fixture(root: Path) -> None:
    for relative_path, (schema_id, schema_name) in {
        Path("schemas/resource-contract-v1.json"): (
            "https://aegis-cognition.ai/schemas/resource-contract-v1.json",
            "aegis-resource-contract-v1",
        ),
        Path("schemas/lease-token-v1.json"): (
            "https://aegis-cognition.ai/schemas/lease-token-v1.json",
            "aegis-resource-lease-token-v1",
        ),
        Path("schemas/runtime-telemetry-v1.json"): (
            "https://aegis-cognition.ai/schemas/runtime-telemetry-v1.json",
            "aegis-runtime-telemetry-v1",
        ),
    }.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "$id": schema_id,
                    "type": "object",
                    "properties": {"schema": {"const": schema_name}},
                }
            ),
            encoding="utf-8",
        )
    for relative_path, markers in {
        Path("core/rust/src/policy.rs"): ("TypedToolIR", "PolicyProofTrace", "HarnessBenchScorecard"),
        Path("core/rust/src/replay.rs"): ("RunEvent", "event_hash", "schema_hash"),
    }.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(markers), encoding="utf-8")


def _write_freeze_fixture(root: Path, body: str) -> None:
    path = root / "docs" / "ARCHITECTURE_FREEZE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Status: DESIGN_FREEZE_INPUT\n"
        "This does not assert that the checkout already satisfies the target.\n"
        "This is not a surgical-convergence authorization.\n"
        "BASELINE_PRESENT; benchmark status remains local.\n"
        f"{body}\n",
        encoding="utf-8",
    )


def test_truth_schema_gate_requires_versioned_ids_and_owner_markers(tmp_path: Path) -> None:
    _write_truth_fixture(tmp_path)

    assert _truth_schema_gate(tmp_path) == (
        True,
        "versioned truth schemas and Rust ownership markers are present; "
        "compile/replay-hash stability remains runtime evidence",
    )

    payload_path = tmp_path / "schemas" / "lease-token-v1.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["properties"]["schema"]["const"] = "drifted"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    ok, detail = _truth_schema_gate(tmp_path)
    assert not ok
    assert "lease-token-v1.json.properties.schema.const drift" in detail


def test_no_overclaim_gate_accepts_qualified_and_rejects_unqualified_claims(tmp_path: Path) -> None:
    _write_freeze_fixture(tmp_path, "The target is not production-ready until external evidence exists.")
    assert _no_overclaim_gate(tmp_path) == (True, "readiness claims are qualified and freeze status is explicit")

    _write_freeze_fixture(tmp_path, "The system is production-ready.")
    ok, detail = _no_overclaim_gate(tmp_path)
    assert not ok
    assert "production-ready" in detail


def test_evaluate_constitution_exposes_named_freeze_gates() -> None:
    report = evaluate_constitution(Path(__file__).resolve().parents[1])
    checks = {check["name"]: check for check in report["checks"]}

    assert checks["TruthSchemaGate"]["ok"] is True
    assert checks["NoOverclaimGate"]["ok"] is True
