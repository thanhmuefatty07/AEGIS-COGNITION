import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "hot_browser_shadow_gate_report.json"


def evaluate_hot_browser_shadow_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifact_path = root_path / "artifacts" / "hot_browser_shadow_report.json"
    seal_dir = root_path / "artifacts" / "hot_browser_shadow_seals"
    artifact = _read_json(artifact_path)
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    artifacts = report.get("artifact_reports", [])
    samples = report.get("hot_commit_count", 0)
    checks = (
        _check("artifact_present", bool(artifact)),
        _check("schema_version", artifact.get("schema_version") == 1),
        _check("report_schema", report.get("schema") == "aegis-hot-browser-shadow-report-v1"),
        _check("artifact_count", report.get("artifact_count") == 8),
        _check("hot_commit_count", report.get("hot_commit_count") == 8),
        _check("all_browser_artifact_kinds_present", _browser_artifact_kinds_present(artifacts)),
        _check("total_bytes_bound", _positive_int(report.get("total_artifact_bytes")) and report.get("total_artifact_bytes") == _artifact_total_bytes(artifacts)),
        _check("hot_timings_recorded", _timing_window(report, "hot_commit", samples)),
        _check("arena_live_bytes_after_hot", report.get("arena_live_bytes_after_hot") == report.get("total_artifact_bytes")),
        _check("arena_live_slots_after_hot", report.get("arena_live_slots_after_hot") == 8),
        _check("arena_total_commits", report.get("arena_total_commits") == 8),
        _check("hot_before_cold_receipt", report.get("hot_commit_completed_before_first_cold_receipt") is True),
        _check("shadow_sealer_nonblocking_admission", report.get("shadow_sealer_nonblocking_admission") is True),
        _check("shadow_sealer_queue_depth", _positive_int(report.get("shadow_sealer_queue_depth")) and report.get("shadow_sealer_queue_depth") >= 8),
        _check("shadow_sealer_queued_admissions", report.get("shadow_sealer_queued_admissions") == 8),
        _check("shadow_sealer_backpressure_rejections", report.get("shadow_sealer_backpressure_rejections") == 0),
        _check("cold_seal_receipt_count", report.get("cold_seal_receipt_count") == 8),
        _check("cold_seal_total_bytes", report.get("cold_seal_total_bytes") == report.get("total_artifact_bytes")),
        _check("cold_files_materialized_flag", report.get("cold_files_materialized") is True),
        _check("cold_files_exist_on_disk", _cold_files_exist(seal_dir, report.get("cold_seal_receipt_count"), report.get("cold_seal_total_bytes"))),
        _check("shadow_receipts_valid", report.get("shadow_receipts_valid") is True),
        _check("shadow_batch_hash_nonzero", _hash_array(report.get("shadow_batch_hash"))),
        _check("shadow_batch_valid", report.get("shadow_batch_valid") is True),
        _check("replay_event_count", report.get("replay_event_count") == 2),
        _check("replay_hash_chain_valid", report.get("replay_hash_chain_valid") is True),
        _check("shadow_seal_replay_recorded", report.get("shadow_seal_replay_recorded") is True),
        _check("replay_last_hash_nonzero", _hash_array(report.get("replay_last_hash"))),
        _check("shadow_arrow_archive_segment_count", report.get("shadow_arrow_archive_segment_count") == 2),
        _check("shadow_arrow_archive_event_count", report.get("shadow_arrow_archive_event_count") == report.get("replay_event_count")),
        _check("shadow_arrow_archive_manifest_hash_nonzero", _hash_array(report.get("shadow_arrow_archive_manifest_hash"))),
        _check("shadow_arrow_archive_audit_proof_hash_nonzero", _hash_array(report.get("shadow_arrow_archive_audit_proof_hash"))),
        _check("shadow_arrow_archive_segment_witness_hash_nonzero", _hash_array(report.get("shadow_arrow_archive_segment_witness_hash"))),
        _check("shadow_arrow_archive_mmap_evidence_hash_nonzero", _hash_array(report.get("shadow_arrow_archive_mmap_evidence_hash"))),
        _check("shadow_arrow_archive_determinism_proof_hash_nonzero", _hash_array(report.get("shadow_arrow_archive_determinism_proof_hash"))),
        _check("shadow_arrow_archive_recovered", report.get("shadow_arrow_archive_recovered") is True),
        _check("shadow_arrow_archive_replay_matches", report.get("shadow_arrow_archive_replay_matches") is True),
        _check("shadow_arrow_archive_contains_shadow_seal", report.get("shadow_arrow_archive_contains_shadow_seal") is True),
        _check("hot_artifact_batch_hash_nonzero", _hash_array(report.get("hot_artifact_batch_hash"))),
        _check("artifact_hashes_nonzero", _artifact_hashes_valid(artifacts)),
        _check("no_file_roundtrip_on_hot_path", report.get("no_file_roundtrip_on_hot_path") is True),
        _check("prod_trust_level", report.get("trust_level_prod") is True),
        _check("physical_witness_required", report.get("physical_witness_required") is True),
        _check("fail_closed", report.get("fail_closed") is True),
        _check("report_hash_nonzero", _hash_array(report.get("report_hash"))),
    )
    payload = {
        "suite_name": "AEGIS Hot Browser Shadow Gate",
        "schema": "aegis-hot-browser-shadow-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-hot-engine-shadow-sealer-artifact-verifier",
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_hash(artifact_path),
        "hot_browser_shadow_evidence": {
            "artifact_count": report.get("artifact_count", 0),
            "total_artifact_bytes": report.get("total_artifact_bytes", 0),
            "hot_commit_count": report.get("hot_commit_count", 0),
            "hot_commit_total_ns": report.get("hot_commit_total_ns", 0),
            "arena_live_bytes_after_hot": report.get("arena_live_bytes_after_hot", 0),
            "shadow_sealer_nonblocking_admission": report.get("shadow_sealer_nonblocking_admission"),
            "shadow_sealer_queue_depth": report.get("shadow_sealer_queue_depth", 0),
            "shadow_sealer_queued_admissions": report.get("shadow_sealer_queued_admissions", 0),
            "shadow_sealer_backpressure_rejections": report.get("shadow_sealer_backpressure_rejections", 0),
            "cold_seal_receipt_count": report.get("cold_seal_receipt_count", 0),
            "cold_seal_total_bytes": report.get("cold_seal_total_bytes", 0),
            "shadow_seal_replay_recorded": report.get("shadow_seal_replay_recorded"),
            "shadow_arrow_archive_segment_count": report.get("shadow_arrow_archive_segment_count", 0),
            "shadow_arrow_archive_event_count": report.get("shadow_arrow_archive_event_count", 0),
            "shadow_arrow_archive_recovered": report.get("shadow_arrow_archive_recovered"),
            "shadow_arrow_archive_replay_matches": report.get("shadow_arrow_archive_replay_matches"),
            "shadow_arrow_archive_contains_shadow_seal": report.get("shadow_arrow_archive_contains_shadow_seal"),
            "no_file_roundtrip_on_hot_path": report.get("no_file_roundtrip_on_hot_path"),
            "trust_level_prod": report.get("trust_level_prod"),
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


def _browser_artifact_kinds_present(artifacts: Any) -> bool:
    expected = {
        "url_before",
        "url_after",
        "dom_snapshot_before",
        "dom_snapshot_after",
        "screenshot_before",
        "screenshot_after",
        "accessibility_tree_after",
        "network_log",
    }
    if not isinstance(artifacts, list):
        return False
    observed = {artifact.get("kind") for artifact in artifacts if isinstance(artifact, dict)}
    return observed == expected


def _artifact_total_bytes(artifacts: Any) -> int:
    if not isinstance(artifacts, list):
        return -1
    total = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not _positive_int(artifact.get("byte_len")):
            return -1
        total += artifact["byte_len"]
    return total


def _artifact_hashes_valid(artifacts: Any) -> bool:
    if not isinstance(artifacts, list) or len(artifacts) != 8:
        return False
    return all(
        isinstance(artifact, dict)
        and _positive_int(artifact.get("byte_len"))
        and _positive_int(artifact.get("generation"))
        and _hash_array(artifact.get("artifact_hash"))
        and _hash_array(artifact.get("storage_ref_hash"))
        and _hash_array(artifact.get("seal_hash"))
        for artifact in artifacts
    )


def _cold_files_exist(seal_dir: Path, expected_count: Any, expected_bytes: Any) -> bool:
    if not isinstance(expected_count, int) or not isinstance(expected_bytes, int):
        return False
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
    report = evaluate_hot_browser_shadow_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
