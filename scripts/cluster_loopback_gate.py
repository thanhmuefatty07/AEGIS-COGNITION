import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
CLUSTER_LOOPBACK_REPORT_PATH = ARTIFACTS_DIR / "cluster_loopback_report.json"
REPORT_PATH = ARTIFACTS_DIR / "cluster_loopback_gate_report.json"


def evaluate_cluster_loopback_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifact_path = root_path / "artifacts" / "cluster_loopback_report.json"
    artifact = _read_json(artifact_path)
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    checks = (
        _check("artifact_present", bool(artifact)),
        _check("schema_version", artifact.get("schema_version") == 1),
        _check("report_schema", report.get("schema") == "aegis-cluster-loopback-report-v1"),
        _check("worker_count", report.get("worker_count") == 3),
        _check("work_item_count", report.get("work_item_count") == 32),
        _check("all_candidates_accepted", report.get("accepted_count") == report.get("work_item_count")),
        _check("duplicate_rejected_as_duplicate", report.get("duplicate_count") == 1),
        _check("replay_event_count_bound", report.get("replay_event_count") == 33),
        _check("replay_hash_chain_valid", report.get("replay_hash_chain_valid") is True),
        _check("direct_worker_commit_rejected", report.get("direct_worker_commit_rejected") is True),
        _check("side_effect_partition_paused", report.get("side_effect_partition_paused") is True),
        _check("logical_rtt_recorded", _positive_int(report.get("max_logical_rtt_ticks")) and _positive_int(report.get("total_logical_rtt_ticks"))),
        _check("candidate_result_hash_nonzero", _hash_array(report.get("candidate_result_hash"))),
        _check("replay_last_hash_nonzero", _hash_array(report.get("replay_last_hash"))),
        _check("report_hash_nonzero", _hash_array(report.get("report_hash"))),
        _check("multi_machine_gap_disclosed", report.get("multi_machine_real_cluster") is False),
    )
    payload = {
        "suite_name": "AEGIS Cluster Loopback Gate",
        "schema": "aegis-cluster-loopback-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-cluster-loopback-artifact-verifier",
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_hash(artifact_path),
        "loopback_cluster_evidence": {
            "worker_count": report.get("worker_count", 0),
            "work_item_count": report.get("work_item_count", 0),
            "accepted_count": report.get("accepted_count", 0),
            "max_logical_rtt_ticks": report.get("max_logical_rtt_ticks", 0),
            "total_logical_rtt_ticks": report.get("total_logical_rtt_ticks", 0),
            "multi_machine_real_cluster": report.get("multi_machine_real_cluster"),
        },
        "real_multi_node_cluster_test_present": False,
        "production_cluster_release_blocked_without_real_multi_node_soak": True,
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
    report = evaluate_cluster_loopback_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
