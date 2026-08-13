import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "shadow_sealer_soak_gate_report.json"

EXPECTED_SAMPLE_COUNT = 64
EXPECTED_PAYLOAD_BYTES = 4 * 1024
EXPECTED_QUEUE_DEPTH = 128
EXPECTED_HOT_SUBMIT_GATE_NS = 1_000_000


def evaluate_shadow_sealer_soak_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifact_path = root_path / "artifacts" / "shadow_sealer_soak_report.json"
    seal_dir = root_path / "artifacts" / "shadow_sealer_soak_seals"
    artifact = _read_json(artifact_path)
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    samples = report.get("sample_count", 0)
    total_bytes = EXPECTED_SAMPLE_COUNT * EXPECTED_PAYLOAD_BYTES
    checks = (
        _check("artifact_present", bool(artifact)),
        _check("schema_version", artifact.get("schema_version") == 1),
        _check("report_schema", report.get("schema") == "aegis-shadow-sealer-soak-report-v1"),
        _check("sample_count", report.get("sample_count") == EXPECTED_SAMPLE_COUNT),
        _check("payload_bytes_per_sample", report.get("payload_bytes_per_sample") == EXPECTED_PAYLOAD_BYTES),
        _check("total_payload_bytes", report.get("total_payload_bytes") == total_bytes),
        _check("queue_depth", report.get("queue_depth") == EXPECTED_QUEUE_DEPTH),
        _check("hot_commit_timings", _timing_window(report, "hot_commit", samples)),
        _check("hot_submit_timings", _timing_window(report, "hot_submit", samples)),
        _check("hot_submit_gate", report.get("hot_submit_gate_ns") == EXPECTED_HOT_SUBMIT_GATE_NS),
        _check("hot_submit_under_gate", report.get("hot_submit_under_gate") is True),
        _check("hot_submit_max_under_gate", _positive_int(report.get("hot_submit_max_ns")) and report.get("hot_submit_max_ns") < EXPECTED_HOT_SUBMIT_GATE_NS),
        _check("hot_before_cold_receipts", report.get("hot_submissions_completed_before_receipts") is True),
        _check("queued_admissions", report.get("queued_admissions") == EXPECTED_SAMPLE_COUNT),
        _check("backpressure_rejections", report.get("backpressure_rejections") == 0),
        _check("cold_seal_receipt_count", report.get("cold_seal_receipt_count") == EXPECTED_SAMPLE_COUNT),
        _check("cold_seal_total_bytes", report.get("cold_seal_total_bytes") == total_bytes),
        _check("cold_seal_wait_recorded", _positive_int(report.get("cold_seal_wait_total_ns"))),
        _check("cold_payload_files_materialized", report.get("cold_payload_files_materialized") is True),
        _check("cold_files_exist_on_disk", _cold_files_exist(seal_dir, EXPECTED_SAMPLE_COUNT, total_bytes)),
        _check("cold_payload_file_hashes_match", report.get("cold_payload_file_hashes_match") is True),
        _check("cold_payload_sync_requested", report.get("cold_payload_sync_requested") is True),
        _check("shadow_receipts_valid", report.get("shadow_receipts_valid") is True),
        _check("shadow_batch_hash_nonzero", _hash_array(report.get("shadow_batch_hash"))),
        _check("shadow_batch_valid", report.get("shadow_batch_valid") is True),
        _check("payload_set_hash_nonzero", _hash_array(report.get("payload_set_hash"))),
        _check("receipt_set_hash_nonzero", _hash_array(report.get("receipt_set_hash"))),
        _check("cold_file_evidence_hash_nonzero", _hash_array(report.get("cold_file_evidence_hash"))),
        _check("prod_trust_level", report.get("trust_level_prod") is True),
        _check("physical_witness_required", report.get("physical_witness_required") is True),
        _check("fail_closed", report.get("fail_closed") is True),
        _check("report_hash_nonzero", _hash_array(report.get("report_hash"))),
    )
    payload = {
        "suite_name": "AEGIS Shadow Sealer Soak Gate",
        "schema": "aegis-shadow-sealer-soak-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-async-shadow-sealer-soak-artifact-verifier",
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_hash(artifact_path),
        "shadow_sealer_soak_evidence": {
            "sample_count": report.get("sample_count", 0),
            "payload_bytes_per_sample": report.get("payload_bytes_per_sample", 0),
            "total_payload_bytes": report.get("total_payload_bytes", 0),
            "queue_depth": report.get("queue_depth", 0),
            "hot_submit_max_ns": report.get("hot_submit_max_ns", 0),
            "hot_submit_gate_ns": report.get("hot_submit_gate_ns", 0),
            "hot_submit_under_gate": report.get("hot_submit_under_gate"),
            "hot_submissions_completed_before_receipts": report.get("hot_submissions_completed_before_receipts"),
            "queued_admissions": report.get("queued_admissions", 0),
            "backpressure_rejections": report.get("backpressure_rejections", 0),
            "cold_seal_receipt_count": report.get("cold_seal_receipt_count", 0),
            "cold_seal_total_bytes": report.get("cold_seal_total_bytes", 0),
            "cold_payload_files_materialized": report.get("cold_payload_files_materialized"),
            "cold_payload_file_hashes_match": report.get("cold_payload_file_hashes_match"),
            "cold_payload_sync_requested": report.get("cold_payload_sync_requested"),
        },
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
    }
    payload["overall_ok"] = payload["failed"] == 0
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _timing_window(report: dict[str, Any], prefix: str, samples: Any) -> bool:
    if not isinstance(samples, int) or isinstance(samples, bool) or samples <= 0:
        return False
    minimum = report.get(f"{prefix}_min_ns")
    maximum = report.get(f"{prefix}_max_ns")
    total = report.get(f"{prefix}_total_ns")
    return (
        _positive_int(minimum)
        and _positive_int(maximum)
        and _positive_int(total)
        and minimum <= maximum
        and minimum * samples <= total <= maximum * samples
    )


def _cold_files_exist(seal_dir: Path, expected_count: int, expected_bytes: int) -> bool:
    try:
        files = [path for path in seal_dir.iterdir() if path.is_file()]
    except OSError:
        return False
    return len(files) >= expected_count and sum(path.stat().st_size for path in files) >= expected_bytes


def _hash_array(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(item, int) and 0 <= item <= 255 for item in value)
        and any(item != 0 for item in value)
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    report = evaluate_shadow_sealer_soak_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
