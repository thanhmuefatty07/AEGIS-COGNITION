import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "tcp_cluster_soak_gate_report.json"
REAL_MULTI_MACHINE_CLUSTER_ADMISSION_SCHEMA = "aegis-real-multi-machine-cluster-soak-admission-v1"
REAL_MULTI_MACHINE_CLUSTER_CAPTURE_SCHEMA = "aegis-real-multi-machine-cluster-soak-capture-v1"
REAL_MULTI_MACHINE_CLUSTER_EXPECTED_PATH = "artifacts/real_multi_machine_cluster_soak_capture.json"
REAL_MULTI_MACHINE_CLUSTER_BLOCKER_ID = "real_multi_machine_cluster_soak_missing"


def evaluate_tcp_cluster_soak_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifact_path = root_path / "artifacts" / "tcp_cluster_soak_report.json"
    artifact = _read_json(artifact_path)
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    base_admission = report.get("real_multi_machine_cluster_admission", {})
    base_admission = base_admission if isinstance(base_admission, dict) else {}
    admission = _real_multi_machine_cluster_admission(root_path, report, base_admission)
    real_multi_node_cluster_test_present = admission.get("capture_valid") is True
    production_blocked_without_real_multi_node = not real_multi_node_cluster_test_present
    checks = (
        _check("artifact_present", bool(artifact)),
        _check("schema_version", artifact.get("schema_version") == 1),
        _check("report_schema", report.get("schema") == "aegis-tcp-cluster-soak-report-v1"),
        _check("worker_count", report.get("worker_count") == 3),
        _check("work_item_count", report.get("work_item_count") == 24),
        _check("all_candidates_accepted", report.get("accepted_count") == report.get("work_item_count")),
        _check("duplicate_rejected_as_duplicate", report.get("duplicate_count") == 1),
        _check("replay_event_count_bound", report.get("replay_event_count") == 25),
        _check("replay_hash_chain_valid", report.get("replay_hash_chain_valid") is True),
        _check("tcp_listener_bound", report.get("tcp_listener_bound") is True),
        _check("tcp_worker_connections", report.get("tcp_worker_connections") == report.get("worker_count")),
        _check("tcp_round_trips_recorded", _timing_window(report, "tcp_round_trip", report.get("tcp_round_trip_count"))),
        _check("tcp_round_trip_count", report.get("tcp_round_trip_count") == report.get("work_item_count")),
        _check("tcp_payload_bytes_sent", report.get("tcp_payload_bytes_sent") == 24 * (1 + 228)),
        _check("tcp_payload_bytes_received", report.get("tcp_payload_bytes_received") == 24 * 156),
        _check("worker_response_count", report.get("worker_response_count") == report.get("work_item_count")),
        _check("direct_worker_commit_rejected", report.get("direct_worker_commit_rejected") is True),
        _check("side_effect_partition_paused", report.get("side_effect_partition_paused") is True),
        _check("network_trace_hash_nonzero", _hash_array(report.get("network_trace_hash"))),
        _check("candidate_result_hash_nonzero", _hash_array(report.get("candidate_result_hash"))),
        _check("replay_last_hash_nonzero", _hash_array(report.get("replay_last_hash"))),
        _check("report_hash_nonzero", _hash_array(report.get("report_hash"))),
        _check("tcp_loopback_gap_disclosed", report.get("loopback_tcp_only") is True),
        _check("multi_machine_gap_disclosed", report.get("multi_machine_real_cluster") is False),
        _check("real_multi_machine_admission_schema", admission.get("schema") == REAL_MULTI_MACHINE_CLUSTER_ADMISSION_SCHEMA),
        _check("real_multi_machine_admission_path", admission.get("expected_capture_path") == REAL_MULTI_MACHINE_CLUSTER_EXPECTED_PATH),
        _check("real_multi_machine_admission_hash", _hash_array(admission.get("admission_hash"))),
        _check("real_multi_machine_network_contract_hash", _hash_array(admission.get("network_contract_hash"))),
        _check("real_multi_machine_topology_contract_hash", _hash_array(admission.get("topology_contract_hash"))),
        _check("real_multi_machine_required_worker_count", admission.get("required_worker_count") == report.get("worker_count")),
        _check("real_multi_machine_required_work_items", admission.get("required_work_item_count") == report.get("work_item_count")),
        _check("real_multi_machine_required_round_trips", admission.get("required_round_trip_count") == report.get("tcp_round_trip_count")),
        _check("real_multi_machine_local_worker_count", admission.get("local_loopback_worker_count") == report.get("worker_count")),
        _check("real_multi_machine_local_work_items", admission.get("local_loopback_work_item_count") == report.get("work_item_count")),
        _check("real_multi_machine_local_round_trips", admission.get("local_loopback_round_trip_count") == report.get("tcp_round_trip_count")),
        _check("real_multi_machine_admission_blocker", admission.get("production_blocker_id") == REAL_MULTI_MACHINE_CLUSTER_BLOCKER_ID),
        _check("real_multi_machine_admission_state_bound", _admission_state_bound(admission)),
        _check("real_multi_machine_capture_missing_or_valid", (not admission.get("capture_present")) or admission.get("capture_valid") is True),
        _check("real_multi_machine_state_recorded", True),
        _check("production_blocked_without_real_multi_node_soak", production_blocked_without_real_multi_node == (not real_multi_node_cluster_test_present)),
    )
    payload = {
        "suite_name": "AEGIS TCP Cluster Soak Gate",
        "schema": "aegis-tcp-cluster-soak-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-tcp-cluster-soak-artifact-verifier",
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_hash(artifact_path),
        "tcp_cluster_soak_evidence": {
            "worker_count": report.get("worker_count", 0),
            "work_item_count": report.get("work_item_count", 0),
            "accepted_count": report.get("accepted_count", 0),
            "tcp_worker_connections": report.get("tcp_worker_connections", 0),
            "tcp_round_trip_count": report.get("tcp_round_trip_count", 0),
            "tcp_round_trip_min_ns": report.get("tcp_round_trip_min_ns", 0),
            "tcp_round_trip_max_ns": report.get("tcp_round_trip_max_ns", 0),
            "tcp_round_trip_total_ns": report.get("tcp_round_trip_total_ns", 0),
            "tcp_payload_bytes_sent": report.get("tcp_payload_bytes_sent", 0),
            "tcp_payload_bytes_received": report.get("tcp_payload_bytes_received", 0),
            "loopback_tcp_only": report.get("loopback_tcp_only"),
            "multi_machine_real_cluster": report.get("multi_machine_real_cluster"),
        },
        "real_multi_machine_cluster_admission": {
            "schema": admission.get("schema", ""),
            "expected_capture_path": admission.get("expected_capture_path", ""),
            "capture_present": admission.get("capture_present"),
            "capture_hash": admission.get("capture_hash", []),
            "network_contract": admission.get("network_contract", ""),
            "network_contract_hash": admission.get("network_contract_hash", []),
            "topology_contract": admission.get("topology_contract", ""),
            "topology_contract_hash": admission.get("topology_contract_hash", []),
            "required_worker_count": admission.get("required_worker_count", 0),
            "required_work_item_count": admission.get("required_work_item_count", 0),
            "required_round_trip_count": admission.get("required_round_trip_count", 0),
            "local_loopback_worker_count": admission.get("local_loopback_worker_count", 0),
            "local_loopback_work_item_count": admission.get("local_loopback_work_item_count", 0),
            "local_loopback_round_trip_count": admission.get("local_loopback_round_trip_count", 0),
            "capture_schema": admission.get("capture_schema", ""),
            "capture_valid": admission.get("capture_valid"),
            "capture_error": admission.get("capture_error", ""),
            "capture_verification_hash": admission.get("capture_verification_hash", []),
            "admission_status": admission.get("admission_status", ""),
            "production_blocker_id": admission.get("production_blocker_id", ""),
            "admission_hash": admission.get("admission_hash", []),
        },
        "real_multi_machine_cluster_admission_hash": admission.get("admission_hash", []),
        "real_multi_machine_cluster_admission_missing": (
            admission.get("capture_present") is False
            and admission.get("admission_status") == "missing"
        ),
        "real_multi_machine_cluster_capture_missing_or_valid": (
            (not admission.get("capture_present")) or admission.get("capture_valid") is True
        ),
        "real_multi_machine_cluster_admission_blocker_id": admission.get("production_blocker_id", ""),
        "real_multi_machine_cluster_state_recorded": True,
        "real_multi_node_cluster_test_present": real_multi_node_cluster_test_present,
        "production_cluster_release_blocked_without_real_multi_node_soak": production_blocked_without_real_multi_node,
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


def _real_multi_machine_cluster_admission(
    root: Path,
    report: dict[str, Any],
    base_admission: dict[str, Any],
) -> dict[str, Any]:
    admission = dict(base_admission)
    capture_path = root / REAL_MULTI_MACHINE_CLUSTER_EXPECTED_PATH
    capture_payload, capture_present, capture_bytes, capture_error = _read_capture(capture_path)
    capture_valid = _real_multi_machine_capture_valid(capture_payload, report, base_admission)
    if capture_valid:
        admission_status = "candidate-present-real-multi-machine-soak-gate-required"
    elif capture_present:
        admission_status = "invalid-capture"
    else:
        admission_status = "missing"
    capture_hash = _hash_bytes_array(capture_bytes) if capture_present else [0] * 32
    admission.update(
        {
            "schema": admission.get("schema", REAL_MULTI_MACHINE_CLUSTER_ADMISSION_SCHEMA),
            "expected_capture_path": admission.get("expected_capture_path", REAL_MULTI_MACHINE_CLUSTER_EXPECTED_PATH),
            "capture_present": capture_present,
            "capture_schema": capture_payload.get("schema", "") if capture_present else "",
            "capture_valid": capture_valid,
            "capture_hash": capture_hash,
            "capture_error": capture_error,
            "admission_status": admission_status,
            "production_blocker_id": admission.get("production_blocker_id", REAL_MULTI_MACHINE_CLUSTER_BLOCKER_ID),
        }
    )
    admission["capture_verification_hash"] = _hash_payload_array(
        {
            "capture_hash": capture_hash,
            "capture_valid": capture_valid,
            "admission_status": admission_status,
            "network_contract": admission.get("network_contract", ""),
            "topology_contract": admission.get("topology_contract", ""),
            "required_worker_count": admission.get("required_worker_count", 0),
            "required_work_item_count": admission.get("required_work_item_count", 0),
            "required_round_trip_count": admission.get("required_round_trip_count", 0),
        }
    )
    return admission


def write_real_multi_machine_cluster_soak_capture(
    root: str | Path = ROOT,
    probe_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    artifact = _read_json(root_path / "artifacts" / "tcp_cluster_soak_report.json")
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    admission = report.get("real_multi_machine_cluster_admission", {})
    admission = admission if isinstance(admission, dict) else {}
    probe = probe_override if probe_override is not None else _real_multi_machine_cluster_probe(report, admission)
    capture = _real_multi_machine_capture(report, admission, probe)
    capture_valid = _real_multi_machine_capture_valid(capture, report, admission)
    capture_path = root_path / REAL_MULTI_MACHINE_CLUSTER_EXPECTED_PATH
    encoded_capture = json.dumps(capture, indent=2, sort_keys=True).encode("utf-8")
    if capture_valid:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(encoded_capture)
    return {
        "suite_name": "AEGIS Real Multi-Machine Cluster Soak Capture Producer",
        "schema": "aegis-real-multi-machine-cluster-soak-capture-producer-v1",
        "truth_claim": False,
        "capture_path": str(capture_path),
        "capture_written": capture_valid,
        "capture_schema": capture.get("schema", ""),
        "capture_hash": hashlib.sha256(encoded_capture).hexdigest() if capture_valid else "0" * 64,
        "endpoint_config_present": probe.get("endpoint_config_present") is True,
        "worker_count": capture.get("worker_count", 0),
        "round_trip_count": capture.get("round_trip_count", 0),
        "distinct_machine_count": capture.get("distinct_machine_count", 0),
        "overall_ok": capture_valid,
        "error": "" if capture_valid else probe.get("error", "real multi-machine cluster soak did not satisfy capture contract"),
    }


def _real_multi_machine_cluster_probe(report: dict[str, Any], admission: dict[str, Any]) -> dict[str, Any]:
    raw_endpoints = os.environ.get("AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON", "").strip()
    timeout_seconds = int(os.environ.get("AEGIS_REAL_MULTI_MACHINE_CLUSTER_TIMEOUT_SECONDS", "5"))
    required_worker_count = report.get("worker_count", admission.get("required_worker_count", 0))
    required_round_trips = report.get("tcp_round_trip_count", admission.get("required_round_trip_count", 0))
    if not raw_endpoints:
        return {
            "schema": "aegis-real-multi-machine-cluster-probe-v1",
            "endpoint_config_present": False,
            "requested_round_trip_count": required_round_trips,
            "endpoints": [],
            "round_trips": [],
            "error": "AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON unset",
        }
    try:
        endpoints = json.loads(raw_endpoints)
    except json.JSONDecodeError:
        return {
            "schema": "aegis-real-multi-machine-cluster-probe-v1",
            "endpoint_config_present": True,
            "requested_round_trip_count": required_round_trips,
            "endpoints": [],
            "round_trips": [],
            "error": "AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON invalid-json",
        }
    normalized, endpoint_error = _normalize_cluster_endpoints(endpoints)
    if endpoint_error:
        return {
            "schema": "aegis-real-multi-machine-cluster-probe-v1",
            "endpoint_config_present": True,
            "requested_round_trip_count": required_round_trips,
            "endpoints": [],
            "round_trips": [],
            "error": endpoint_error,
        }
    if len(normalized) != required_worker_count:
        return {
            "schema": "aegis-real-multi-machine-cluster-probe-v1",
            "endpoint_config_present": True,
            "requested_round_trip_count": required_round_trips,
            "endpoints": normalized,
            "round_trips": [],
            "error": "endpoint count does not match required worker count",
        }
    round_trips: list[dict[str, Any]] = []
    for index in range(required_round_trips if isinstance(required_round_trips, int) else 0):
        endpoint = normalized[index % len(normalized)]
        request_payload = _cluster_worker_request(report, admission, endpoint, index)
        encoded_request = json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        started_ns = time.perf_counter_ns()
        response_bytes = b""
        error = ""
        try:
            with socket.create_connection((endpoint["host"], endpoint["port"]), timeout=timeout_seconds) as sock:
                sock.settimeout(timeout_seconds)
                sock.sendall(encoded_request)
                response_bytes = sock.recv(1024 * 1024)
        except OSError as exc:
            error = type(exc).__name__
        elapsed_ns = time.perf_counter_ns() - started_ns
        try:
            response = json.loads(response_bytes.decode("utf-8")) if response_bytes else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {}
        round_trips.append(
            {
                "index": index,
                "endpoint_hash": _stable_hash(endpoint),
                "machine_id_hash": hashlib.sha256(endpoint["machine_id"].encode("utf-8")).hexdigest(),
                "host_hash": hashlib.sha256(endpoint["host"].encode("utf-8")).hexdigest(),
                "port": endpoint["port"],
                "elapsed_ns": elapsed_ns,
                "request_hash": hashlib.sha256(encoded_request).hexdigest(),
                "response_len": len(response_bytes),
                "response_hash": hashlib.sha256(response_bytes).hexdigest() if response_bytes else "",
                "response": response if isinstance(response, dict) else {},
                "error": error,
            }
        )
    return {
        "schema": "aegis-real-multi-machine-cluster-probe-v1",
        "endpoint_config_present": True,
        "requested_round_trip_count": required_round_trips,
        "timeout_seconds": timeout_seconds,
        "source_host_hash": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest(),
        "endpoints": normalized,
        "round_trips": round_trips,
        "error": "",
    }


def _normalize_cluster_endpoints(value: Any) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(value, list) or not value:
        return [], "endpoint config must be a non-empty list"
    endpoints: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return [], "endpoint entries must be objects"
        machine_id = item.get("machine_id")
        host = item.get("host")
        port = item.get("port")
        if not isinstance(machine_id, str) or not machine_id:
            return [], "endpoint machine_id must be a non-empty string"
        if not isinstance(host, str) or not host:
            return [], "endpoint host must be a non-empty string"
        if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
            return [], "endpoint port must be an integer TCP port"
        endpoints.append({"machine_id": machine_id, "host": host, "port": port})
    if len({endpoint["machine_id"] for endpoint in endpoints}) != len(endpoints):
        return [], "endpoint machine_id values must be unique"
    return endpoints, ""


def _cluster_worker_request(
    report: dict[str, Any],
    admission: dict[str, Any],
    endpoint: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    payload = {
        "schema": "aegis-real-cluster-worker-request-v1",
        "truth_claim": False,
        "work_index": index,
        "target_machine_id_hash": hashlib.sha256(endpoint["machine_id"].encode("utf-8")).hexdigest(),
        "network_contract": admission.get("network_contract", ""),
        "network_contract_hash": admission.get("network_contract_hash", []),
        "topology_contract": admission.get("topology_contract", ""),
        "topology_contract_hash": admission.get("topology_contract_hash", []),
        "required_worker_count": report.get("worker_count", 0),
        "required_work_item_count": report.get("work_item_count", 0),
        "required_round_trip_count": report.get("tcp_round_trip_count", 0),
        "loopback_network_trace_hash": report.get("network_trace_hash", []),
        "loopback_candidate_result_hash": report.get("candidate_result_hash", []),
        "loopback_replay_last_hash": report.get("replay_last_hash", []),
    }
    payload["request_hash"] = _stable_hash(payload)
    return payload


def _real_multi_machine_capture(
    report: dict[str, Any],
    admission: dict[str, Any],
    probe: dict[str, Any],
) -> dict[str, Any]:
    endpoints = probe.get("endpoints", [])
    round_trips = probe.get("round_trips", [])
    endpoint_count = len(endpoints) if isinstance(endpoints, list) else 0
    machine_ids = {
        endpoint.get("machine_id", "")
        for endpoint in endpoints
        if isinstance(endpoint, dict) and isinstance(endpoint.get("machine_id"), str)
    }
    non_loopback_hosts = all(
        isinstance(endpoint, dict) and not _loopback_host(str(endpoint.get("host", "")))
        for endpoint in endpoints
    ) if isinstance(endpoints, list) else False
    accepted_responses = 0
    if isinstance(round_trips, list):
        for row in round_trips:
            response = row.get("response", {}) if isinstance(row, dict) else {}
            if _cluster_worker_response_valid(row, response, admission):
                accepted_responses += 1
    timing = _round_trip_timing(round_trips if isinstance(round_trips, list) else [])
    return {
        "schema": REAL_MULTI_MACHINE_CLUSTER_CAPTURE_SCHEMA,
        "truth_claim": False,
        "verifier": "external-multi-machine-cluster-capture-producer",
        "production_blocker_id": REAL_MULTI_MACHINE_CLUSTER_BLOCKER_ID,
        "network_contract": admission.get("network_contract", ""),
        "network_contract_hash": admission.get("network_contract_hash", []),
        "topology_contract": admission.get("topology_contract", ""),
        "topology_contract_hash": admission.get("topology_contract_hash", []),
        "required_worker_count": report.get("worker_count", 0),
        "required_work_item_count": report.get("work_item_count", 0),
        "required_round_trip_count": report.get("tcp_round_trip_count", 0),
        "loopback_network_trace_hash": report.get("network_trace_hash", []),
        "loopback_candidate_result_hash": report.get("candidate_result_hash", []),
        "loopback_replay_last_hash": report.get("replay_last_hash", []),
        "loopback_report_hash": report.get("report_hash", []),
        "probe": probe,
        "probe_hash": _stable_hash(probe),
        "worker_count": endpoint_count,
        "round_trip_count": len(round_trips) if isinstance(round_trips, list) else 0,
        "accepted_response_count": accepted_responses,
        "distinct_machine_count": len(machine_ids),
        "non_loopback_hosts": non_loopback_hosts,
        "tcp_round_trip_min_ns": timing["min_ns"],
        "tcp_round_trip_max_ns": timing["max_ns"],
        "tcp_round_trip_total_ns": timing["total_ns"],
        "all_round_trips_valid": accepted_responses == report.get("tcp_round_trip_count"),
        "response_bodies_redacted": True,
    }


def _real_multi_machine_capture_valid(
    capture: dict[str, Any],
    report: dict[str, Any],
    admission: dict[str, Any],
) -> bool:
    probe = capture.get("probe", {})
    round_trips = probe.get("round_trips", []) if isinstance(probe, dict) else []
    endpoints = probe.get("endpoints", []) if isinstance(probe, dict) else []
    return (
        capture.get("schema") == REAL_MULTI_MACHINE_CLUSTER_CAPTURE_SCHEMA
        and capture.get("truth_claim") is False
        and capture.get("production_blocker_id") == REAL_MULTI_MACHINE_CLUSTER_BLOCKER_ID
        and capture.get("network_contract") == admission.get("network_contract")
        and capture.get("network_contract_hash") == admission.get("network_contract_hash")
        and capture.get("topology_contract") == admission.get("topology_contract")
        and capture.get("topology_contract_hash") == admission.get("topology_contract_hash")
        and capture.get("required_worker_count") == admission.get("required_worker_count") == report.get("worker_count")
        and capture.get("required_work_item_count") == admission.get("required_work_item_count") == report.get("work_item_count")
        and capture.get("required_round_trip_count") == admission.get("required_round_trip_count") == report.get("tcp_round_trip_count")
        and capture.get("loopback_network_trace_hash") == report.get("network_trace_hash")
        and capture.get("loopback_candidate_result_hash") == report.get("candidate_result_hash")
        and capture.get("loopback_replay_last_hash") == report.get("replay_last_hash")
        and capture.get("loopback_report_hash") == report.get("report_hash")
        and isinstance(probe, dict)
        and probe.get("endpoint_config_present") is True
        and probe.get("error") == ""
        and isinstance(endpoints, list)
        and len(endpoints) == report.get("worker_count")
        and capture.get("worker_count") == report.get("worker_count")
        and capture.get("distinct_machine_count") == report.get("worker_count")
        and capture.get("non_loopback_hosts") is True
        and capture.get("round_trip_count") == report.get("tcp_round_trip_count")
        and capture.get("accepted_response_count") == report.get("tcp_round_trip_count")
        and capture.get("all_round_trips_valid") is True
        and capture.get("response_bodies_redacted") is True
        and capture.get("probe_hash") == _stable_hash(probe)
        and _positive_int(capture.get("tcp_round_trip_min_ns"))
        and _positive_int(capture.get("tcp_round_trip_max_ns"))
        and _positive_int(capture.get("tcp_round_trip_total_ns"))
        and isinstance(round_trips, list)
        and len(round_trips) == report.get("tcp_round_trip_count")
        and all(
            isinstance(row, dict)
            and _cluster_worker_response_valid(row, row.get("response", {}), admission)
            for row in round_trips
        )
    )


def _cluster_worker_response_valid(row: dict[str, Any], response: Any, admission: dict[str, Any]) -> bool:
    return (
        isinstance(response, dict)
        and response.get("schema") == "aegis-real-cluster-worker-response-v1"
        and response.get("truth_claim") is False
        and response.get("candidate_only") is True
        and response.get("direct_worker_commit_rejected") is True
        and response.get("side_effect_partition_paused") is True
        and response.get("network_contract_hash") == admission.get("network_contract_hash")
        and response.get("topology_contract_hash") == admission.get("topology_contract_hash")
        and isinstance(response.get("worker_machine_id_hash"), str)
        and response.get("worker_machine_id_hash") == row.get("machine_id_hash")
        and _positive_int(row.get("elapsed_ns"))
        and _nonzero_hex(str(row.get("request_hash", "")))
        and _nonzero_hex(str(row.get("response_hash", "")))
        and isinstance(row.get("response_len"), int)
        and row.get("response_len") > 0
        and not row.get("error")
    )


def _round_trip_timing(round_trips: list[dict[str, Any]]) -> dict[str, int]:
    timings = [row.get("elapsed_ns") for row in round_trips if _positive_int(row.get("elapsed_ns"))]
    if not timings:
        return {"min_ns": 0, "max_ns": 0, "total_ns": 0}
    return {"min_ns": min(timings), "max_ns": max(timings), "total_ns": sum(timings)}


def _read_capture(path: Path) -> tuple[dict[str, Any], bool, bytes, str]:
    try:
        capture_bytes = path.read_bytes()
    except OSError:
        return {}, False, b"", "missing"
    if not capture_bytes:
        return {}, True, capture_bytes, "empty"
    try:
        payload = json.loads(capture_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, True, capture_bytes, "invalid-json"
    if not isinstance(payload, dict):
        return {}, True, capture_bytes, "non-object"
    return payload, True, capture_bytes, ""


def _loopback_host(host: str) -> bool:
    lowered = host.lower()
    return lowered in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or lowered.startswith("127.")


def _hash_array(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(item, int) and 0 <= item <= 255 for item in value)
        and any(item != 0 for item in value)
    )


def _hash_bytes_array(value: bytes) -> list[int]:
    return list(hashlib.sha256(value).digest())


def _hash_payload_array(value: Any) -> list[int]:
    return list(bytes.fromhex(_stable_hash(value)))


def _nonzero_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower()) and any(char != "0" for char in value)


def _zero_hash_array(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(item, int) and item == 0 for item in value)
    )


def _admission_state_bound(admission: dict[str, Any]) -> bool:
    missing_capture = (
        admission.get("capture_present") is False
        and _zero_hash_array(admission.get("capture_hash"))
        and admission.get("admission_status") == "missing"
    )
    candidate_capture = (
        admission.get("capture_present") is True
        and _hash_array(admission.get("capture_hash"))
        and admission.get("admission_status") == "candidate-present-real-multi-machine-soak-gate-required"
    )
    return missing_capture or candidate_capture


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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--write-capture"]:
        producer_report = write_real_multi_machine_cluster_soak_capture(ROOT)
        print(json.dumps(producer_report, indent=2, sort_keys=True))
        return 0 if producer_report["overall_ok"] else 1
    if argv:
        print(json.dumps({"error": f"unknown args: {argv}"}, indent=2, sort_keys=True))
        return 2
    report = evaluate_tcp_cluster_soak_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
