import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "production_readiness_report.json"


def stable_payload_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dict_list(value: object) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def string_attr(value: object, name: str) -> str:
    attr = getattr(value, name, "")
    return attr if isinstance(attr, str) else ""


def production_blocker_closure_packet(blocker: dict) -> dict:
    blocker_id = str(blocker.get("id", ""))
    evidence_artifact = str(blocker.get("evidence_artifact", ""))
    base = {
        "schema": "aegis-production-blocker-closure-packet-v1",
        "truth_claim": False,
        "blocker_id": blocker_id,
        "evidence_artifact": evidence_artifact,
        "blocks_production": blocker.get("blocks_production") is True,
        "active": blocker.get("active") is True,
    }
    specs = {
        "external_signed_attestation_missing": {
            "producer_command": [],
            "verifier_command": ["python", "scripts/supply_chain_gate.py"],
            "required_env": [],
            "required_artifacts": [],
            "expected_artifacts": [
                "artifacts/release_signed_attestation.json",
                "artifacts/release_signed_attestation_verification.json",
            ],
            "admission_gate": "supply_chain_gate_report.json",
            "closure_condition": (
                "supply_chain_gate_report.external_signed_attestation_present == true "
                "with externally verified nonzero signature_bundle_hash, "
                "transparency_log_entry_hash, and certificate_identity_hash"
            ),
            "operator_action": (
                "Obtain an external release signature and verification bundle for the "
                "current release attestation subject; then materialize both expected artifacts."
            ),
        },
        "real_multi_machine_cluster_soak_missing": {
            "producer_command": ["python", "scripts/tcp_cluster_soak_gate.py", "--write-capture"],
            "verifier_command": ["python", "scripts/tcp_cluster_soak_gate.py"],
            "required_env": [
                "AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON",
                "AEGIS_REAL_MULTI_MACHINE_CLUSTER_TIMEOUT_SECONDS",
            ],
            "required_artifacts": ["artifacts/tcp_cluster_soak_report.json"],
            "expected_artifacts": ["artifacts/real_multi_machine_cluster_soak_capture.json"],
            "admission_gate": "tcp_cluster_soak_gate_report.json",
            "closure_condition": (
                "tcp_cluster_soak_gate_report.real_multi_node_cluster_test_present == true "
                "and distinct non-loopback machine count equals required worker count"
            ),
            "operator_action": (
                "Run the capture producer against real non-loopback worker endpoints; "
                "the producer writes the capture only when all round trips satisfy the cluster contract."
            ),
        },
        "full_quickjs_interpreter_cold_start_missing": {
            "producer_command": ["python", "scripts/quickjs_cold_start_gate.py", "--write-capture"],
            "verifier_command": ["python", "scripts/quickjs_cold_start_gate.py"],
            "required_env": ["AEGIS_QUICKJS_FULL_INTERPRETER_RUNNER_JSON"],
            "required_artifacts": [
                "artifacts/quickjs_cold_start_report.json",
                "artifacts/quickjs_full_interpreter.wasm",
            ],
            "expected_artifacts": ["artifacts/quickjs_full_interpreter_cold_start_capture.json"],
            "admission_gate": "quickjs_cold_start_gate_report.json",
            "closure_condition": (
                "quickjs_cold_start_gate_report.real_quickjs_interpreter_cold_start_present == true "
                "with semantic corpus runs passing under the sandbox contract"
            ),
            "operator_action": (
                "Provide the full QuickJS interpreter WASM and runner JSON; the producer writes "
                "the capture only when all semantic corpus samples match."
            ),
        },
        "live_provider_429_soak_missing": {
            "producer_command": ["python", "scripts/dynamic_provider_fallback_gate.py", "--write-capture"],
            "verifier_command": ["python", "scripts/dynamic_provider_fallback_gate.py"],
            "required_env": [
                "AEGIS_LIVE_PROVIDER_429_SOAK_URL",
                "AEGIS_LIVE_PROVIDER_429_SOAK_METHOD",
                "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON",
                "AEGIS_LIVE_PROVIDER_429_SOAK_BODY",
                "AEGIS_LIVE_PROVIDER_429_SOAK_TIMEOUT_SECONDS",
            ],
            "required_artifacts": ["artifacts/dynamic_provider_fallback_report.json"],
            "expected_artifacts": ["artifacts/live_provider_429_soak_capture.json"],
            "admission_gate": "dynamic_provider_fallback_gate_report.json",
            "closure_condition": (
                "dynamic_provider_fallback_gate_report.live_provider_traffic_present == true "
                "with required sample count of real HTTP 429 responses and fallback latency under gate"
            ),
            "operator_action": (
                "Point the capture producer at a live provider endpoint that reliably returns "
                "429 for the configured request while local fallback proof stays under the latency gate."
            ),
        },
        "external_deployment_smoke_missing": {
            "producer_command": ["python", "scripts/external_deployment_smoke_gate.py", "--write-capture"],
            "verifier_command": ["python", "scripts/external_deployment_smoke_gate.py"],
            "required_env": ["AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL"],
            "required_artifacts": [
                "deploy/docker-compose.yml",
                "deploy/kubernetes.yaml",
                "Dockerfile",
            ],
            "expected_artifacts": ["artifacts/external_deployment_smoke_capture.json"],
            "admission_gate": "external_deployment_smoke_gate_report.json",
            "closure_condition": (
                "external_deployment_smoke_gate_report.external_deployment_smoke_present == true "
                "with HTTP 200 aegis-operator-health-v1 and truth_claim == false"
            ),
            "operator_action": (
                "Deploy the package to an external runtime and expose the operator health URL; "
                "the producer writes the capture only when the health response satisfies the contract."
            ),
        },
    }
    packet = {
        **base,
        **specs.get(
            blocker_id,
            {
                "producer_command": [],
                "verifier_command": [],
                "required_env": [],
                "required_artifacts": [],
                "expected_artifacts": [evidence_artifact] if evidence_artifact else [],
                "admission_gate": evidence_artifact,
                "closure_condition": "unknown production blocker requires a project-specific closure contract",
                "operator_action": "Define a fail-closed producer and verifier before clearing this blocker.",
            },
        ),
    }
    packet["closure_packet_hash"] = stable_payload_sha256(packet)
    return packet


def production_blocker_closure_packets(active_blockers: list[dict]) -> list[dict]:
    return [production_blocker_closure_packet(blocker) for blocker in active_blockers]


def build_production_readiness_report(deployment: object, e2e_release_gate: dict) -> dict:
    active_blockers = dict_list(getattr(deployment, "active_production_blockers", ()))
    active_blocker_ids = [
        str(blocker.get("id", ""))
        for blocker in active_blockers
        if isinstance(blocker.get("id", ""), str) and blocker.get("id", "")
    ]
    deployment_deployable = getattr(deployment, "production_deployable", False) is True
    e2e_deployable = e2e_release_gate.get("production_deployable") is True
    topology_hash = string_attr(deployment, "topology_hash")
    topology_contract_hash = string_attr(deployment, "topology_contract_hash")
    closure_packets = production_blocker_closure_packets(active_blockers)
    payload = {
        "schema": "aegis-run-checks-production-readiness-v1",
        "truth_claim": False,
        "production_deployable": deployment_deployable and e2e_deployable,
        "deployment_manifest_production_deployable": deployment_deployable,
        "e2e_release_gate_production_deployable": e2e_deployable,
        "production_gap_disclosed": (deployment_deployable and e2e_deployable) or len(active_blockers) > 0,
        "deployment_policy_source": string_attr(deployment, "deployment_policy_source"),
        "deployment_policy_hash": string_attr(deployment, "deployment_policy_hash"),
        "deployment_registry_source": string_attr(deployment, "deployment_registry_source"),
        "deployment_registry_hash": string_attr(deployment, "deployment_registry_hash"),
        "production_blocker_hash": string_attr(deployment, "production_blocker_hash"),
        "active_production_blocker_hash": string_attr(deployment, "active_production_blocker_hash"),
        "e2e_release_gate_active_production_blocker_hash": str(
            e2e_release_gate.get("active_production_blocker_hash", "")
        ),
        "release_attestation_hash": string_attr(deployment, "release_attestation_hash"),
        "external_signed_attestation_present": getattr(deployment, "external_signed_attestation_present", False)
        is True,
        "deployment_topology_defined": bool(topology_hash and topology_contract_hash),
        "topology_hash": topology_hash,
        "topology_contract_hash": topology_contract_hash,
        "active_production_blocker_count": len(active_blockers),
        "active_production_blocker_ids": active_blocker_ids,
        "active_production_blockers": active_blockers,
        "closure_packet_count": len(closure_packets),
        "closure_packet_set_hash": stable_payload_sha256({"closure_packets": closure_packets}),
        "closure_packets": closure_packets,
    }
    payload["readiness_hash"] = stable_payload_sha256(payload)
    return payload


def evaluate_production_readiness(root: str | Path = ROOT) -> dict:
    from scripts.deployment_manifest import build_deployment_manifest
    from scripts.e2e_release_gate import evaluate_e2e_release_gate

    root_path = Path(root)
    deployment = build_deployment_manifest(root_path)
    e2e_release_gate = evaluate_e2e_release_gate(root_path)
    return build_production_readiness_report(deployment, e2e_release_gate)


def write_production_readiness_report(root: str | Path = ROOT, report_path: Path | None = None) -> dict:
    root_path = Path(root)
    report = evaluate_production_readiness(root_path)
    target = report_path if report_path is not None else root_path / "artifacts" / "production_readiness_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    report = write_production_readiness_report(ROOT, REPORT_PATH)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["production_gap_disclosed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
