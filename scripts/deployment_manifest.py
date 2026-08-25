import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "deployment_manifest_report.json"
DEPLOYMENT_POLICY_RELATIVE_PATH = "docs/architecture/deployment_policy.json"
NOT_VERIFIED_REGISTRY_RELATIVE_PATH = "docs/architecture/not_verified_registry.json"

RELEASE_PROFILE = "minimal-prod-single-writer"
DEPLOYMENT_TOPOLOGY = "single-node-core-with-candidate-workers"
REQUIRED_RELEASE_ARTIFACTS: tuple[str, ...] = (
    "benchmark_gate_report.json",
    "constitution_audit_report.json",
    "governance_gate_report.json",
    "provider_route_gate_report.json",
    "dependency_audit_gate_report.json",
    "supply_chain_gate_report.json",
    "cluster_loopback_gate_report.json",
    "quickjs_cold_start_gate_report.json",
    "tcp_cluster_soak_gate_report.json",
    "hot_browser_shadow_gate_report.json",
    "shadow_sealer_soak_gate_report.json",
    "dynamic_provider_fallback_gate_report.json",
    "production_packaging_smoke_gate_report.json",
    "external_deployment_smoke_gate_report.json",
)

SERVICE_BOUNDARIES: tuple[dict[str, Any], ...] = (
    {
        "name": "aegis-core",
        "role": "single_writer_core",
        "can_commit_replay": True,
        "can_approve_hitl": False,
        "can_execute_side_effects": True,
        "policy_window_required": True,
        "policy_bypass_allowed": False,
        "candidate_only": False,
        "read_only": False,
        "artifact_producer": False,
    },
    {
        "name": "python-friendly-gateway",
        "role": "developer_api",
        "can_commit_replay": False,
        "can_approve_hitl": False,
        "can_execute_side_effects": False,
        "policy_window_required": True,
        "policy_bypass_allowed": False,
        "candidate_only": True,
        "read_only": False,
        "artifact_producer": False,
    },
    {
        "name": "browser-collector",
        "role": "artifact_producer",
        "can_commit_replay": False,
        "can_approve_hitl": False,
        "can_execute_side_effects": False,
        "policy_window_required": True,
        "policy_bypass_allowed": False,
        "candidate_only": True,
        "read_only": False,
        "artifact_producer": True,
    },
    {
        "name": "remote-worker",
        "role": "remote_worker",
        "can_commit_replay": False,
        "can_approve_hitl": False,
        "can_execute_side_effects": False,
        "policy_window_required": True,
        "policy_bypass_allowed": False,
        "candidate_only": True,
        "read_only": False,
        "artifact_producer": True,
    },
    {
        "name": "operator-api",
        "role": "operator_surface",
        "can_commit_replay": False,
        "can_approve_hitl": False,
        "can_execute_side_effects": False,
        "policy_window_required": True,
        "policy_bypass_allowed": False,
        "candidate_only": False,
        "read_only": True,
        "artifact_producer": False,
    },
)

TOPOLOGY_NODES: tuple[dict[str, Any], ...] = (
    {
        "node_id": "core-node",
        "role": "single_writer_runtime",
        "trust_zone": "prod-core",
        "deployment_unit": "stateful-vm-or-statefulset",
        "public_ingress": False,
        "persistent_state": True,
        "commit_authority": True,
    },
    {
        "node_id": "gateway-node",
        "role": "developer_gateway",
        "trust_zone": "prod-api",
        "deployment_unit": "stateless-service",
        "public_ingress": False,
        "persistent_state": False,
        "commit_authority": False,
    },
    {
        "node_id": "collector-node",
        "role": "browser_artifact_collector",
        "trust_zone": "artifact-ingest",
        "deployment_unit": "sandboxed-worker",
        "public_ingress": False,
        "persistent_state": False,
        "commit_authority": False,
    },
    {
        "node_id": "worker-pool",
        "role": "candidate_remote_workers",
        "trust_zone": "untrusted-compute",
        "deployment_unit": "horizontally-scaled-workers",
        "public_ingress": False,
        "persistent_state": False,
        "commit_authority": False,
    },
    {
        "node_id": "operator-node",
        "role": "read_only_operator_surface",
        "trust_zone": "operator-control",
        "deployment_unit": "internal-service",
        "public_ingress": False,
        "persistent_state": False,
        "commit_authority": False,
    },
)

SERVICE_PLACEMENTS: tuple[dict[str, Any], ...] = (
    {
        "service": "aegis-core",
        "node_id": "core-node",
        "process": "aegis-nerve-core",
        "replicas": 1,
        "state_writer": True,
        "can_commit_replay": True,
    },
    {
        "service": "python-friendly-gateway",
        "node_id": "gateway-node",
        "process": "aegis-python-gateway",
        "replicas": 1,
        "state_writer": False,
        "can_commit_replay": False,
    },
    {
        "service": "browser-collector",
        "node_id": "collector-node",
        "process": "aegis-browser-collector",
        "replicas": 1,
        "state_writer": False,
        "can_commit_replay": False,
    },
    {
        "service": "remote-worker",
        "node_id": "worker-pool",
        "process": "aegis-remote-worker",
        "replicas": 3,
        "state_writer": False,
        "can_commit_replay": False,
    },
    {
        "service": "operator-api",
        "node_id": "operator-node",
        "process": "aegis-operator-api",
        "replicas": 1,
        "state_writer": False,
        "can_commit_replay": False,
    },
)

NETWORK_EDGES: tuple[dict[str, Any], ...] = (
    {
        "from": "python-friendly-gateway",
        "to": "aegis-core",
        "protocol": "loopback-or-mtls-http",
        "allowed_payload": "candidate_tool_ir",
        "can_commit_replay": False,
        "policy_window_required": True,
    },
    {
        "from": "browser-collector",
        "to": "aegis-core",
        "protocol": "local-ipc-or-mtls-http",
        "allowed_payload": "artifact_candidate_ref",
        "can_commit_replay": False,
        "policy_window_required": True,
    },
    {
        "from": "remote-worker",
        "to": "aegis-core",
        "protocol": "mtls-http",
        "allowed_payload": "candidate_execution_result",
        "can_commit_replay": False,
        "policy_window_required": True,
    },
    {
        "from": "operator-api",
        "to": "aegis-core",
        "protocol": "loopback-or-mtls-http",
        "allowed_payload": "read_only_status_and_review_packet",
        "can_commit_replay": False,
        "policy_window_required": True,
    },
    {
        "from": "aegis-core",
        "to": "provider-egress",
        "protocol": "https",
        "allowed_payload": "policy_approved_llm_request",
        "can_commit_replay": False,
        "policy_window_required": True,
    },
)

STORAGE_VOLUMES: tuple[dict[str, Any], ...] = (
    {
        "name": "replay-ledger",
        "owner_service": "aegis-core",
        "mount": "/var/lib/aegis/replay",
        "kind": "segmented-arrow-ipc-plus-commit-sidecars",
        "append_only": True,
        "fsync_required": True,
        "encrypted_at_rest_required": True,
    },
    {
        "name": "shadow-seals",
        "owner_service": "aegis-core",
        "mount": "/var/lib/aegis/shadow-seals",
        "kind": "async-shadow-payload-receipts",
        "append_only": True,
        "fsync_required": True,
        "encrypted_at_rest_required": True,
    },
    {
        "name": "evidence-artifacts",
        "owner_service": "aegis-core",
        "mount": "/var/lib/aegis/artifacts",
        "kind": "hash-bound-release-and-runtime-artifacts",
        "append_only": False,
        "fsync_required": True,
        "encrypted_at_rest_required": True,
    },
    {
        "name": "config",
        "owner_service": "aegis-core",
        "mount": "/etc/aegis",
        "kind": "read-only-config-layer",
        "append_only": False,
        "fsync_required": False,
        "encrypted_at_rest_required": True,
    },
)

PROCESS_MODEL: dict[str, Any] = {
    "supervisor": "systemd-or-kubernetes-statefulset",
    "max_core_replicas": 1,
    "startup_order": (
        "aegis-core",
        "python-friendly-gateway",
        "browser-collector",
        "remote-worker",
        "operator-api",
    ),
    "shutdown_order": (
        "operator-api",
        "remote-worker",
        "browser-collector",
        "python-friendly-gateway",
        "aegis-core",
    ),
    "restart_policy": "on-failure-with-replay-prefix-recovery",
    "remote_worker_scaling": "horizontal-candidate-only",
}

HEALTH_CHECKS: tuple[dict[str, Any], ...] = (
    {
        "service": "aegis-core",
        "liveness": "process-plus-metrics-port",
        "readiness": "replay-prefix-recovery-and-policy-window-self-check",
        "fail_closed": True,
    },
    {
        "service": "python-friendly-gateway",
        "liveness": "http-health",
        "readiness": "rust-ffi-import-and-dev-adapter-self-check",
        "fail_closed": False,
    },
    {
        "service": "browser-collector",
        "liveness": "collector-process-health",
        "readiness": "artifact-candidate-submit-probe",
        "fail_closed": False,
    },
    {
        "service": "remote-worker",
        "liveness": "worker-loop-health",
        "readiness": "candidate-only-admission-probe",
        "fail_closed": False,
    },
    {
        "service": "operator-api",
        "liveness": "http-health",
        "readiness": "read-only-review-packet-probe",
        "fail_closed": True,
    },
)

ROLLBACK_PLAN: dict[str, Any] = {
    "operator_rollback_defined": True,
    "rollback_requires_dual_operator_approval": True,
    "preserve_replay_ledger": True,
    "preserve_shadow_seals": True,
    "artifact_source": "required_release_artifact_hashes",
    "steps": (
        "freeze-non-core-ingress",
        "seal-current-replay-prefix",
        "verify-target-release-manifest-hash",
        "stop-candidate-workers",
        "restart-single-writer-core-on-target-release",
        "run-replay-prefix-recovery-gate",
        "resume-candidate-workers",
    ),
}

OPERATOR_RUNBOOK: dict[str, Any] = {
    "runbook_defined": True,
    "preflight": (
        "run scripts/run_checks.py",
        "verify deployment_manifest_report.production_deployable is false until blockers clear",
        "verify release_manifest_hash and required_artifact_hashes",
    ),
    "runtime_monitors": (
        "replay hash-chain continuity",
        "shadow sealer queue depth",
        "provider 429 fallback latency",
        "candidate-only remote worker admission",
    ),
    "emergency_stop": (
        "freeze gateway ingress",
        "pause remote workers",
        "preserve replay and shadow-seal volumes",
    ),
}

PRODUCTION_BLOCKERS: tuple[dict[str, Any], ...] = (
    {
        "id": "external_signed_attestation_missing",
        "blocks_production": True,
        "evidence_artifact": "supply_chain_gate_report.json",
        "registry_ids": ["NV-004"],
    },
    {
        "id": "real_multi_machine_cluster_soak_missing",
        "blocks_production": True,
        "evidence_artifact": "tcp_cluster_soak_gate_report.json",
        "registry_ids": ["NV-016"],
    },
    {
        "id": "full_quickjs_interpreter_cold_start_missing",
        "blocks_production": True,
        "evidence_artifact": "quickjs_cold_start_gate_report.json",
        "registry_ids": ["NV-017"],
    },
    {
        "id": "live_provider_429_soak_missing",
        "blocks_production": True,
        "evidence_artifact": "dynamic_provider_fallback_gate_report.json",
        "registry_ids": ["NV-018"],
    },
    {
        "id": "external_deployment_smoke_missing",
        "blocks_production": True,
        "evidence_artifact": "external_deployment_smoke_gate_report.json",
        "registry_ids": ["NV-019"],
    },
)


@dataclass(frozen=True)
class DeploymentManifest:
    root: str
    python_bridge_ready: bool
    rust_core_ready: bool
    packaging_ready: bool
    service_surface_ready: bool
    cargo_manifest_ready: bool
    docs_ready: bool
    artifacts_dir_ready: bool
    operator_api_ready: bool
    release_profile: str
    topology_id: str
    topology_contract: dict[str, Any]
    service_boundaries: tuple[dict[str, Any], ...]
    topology_nodes: tuple[dict[str, Any], ...]
    service_placements: tuple[dict[str, Any], ...]
    network_edges: tuple[dict[str, Any], ...]
    storage_volumes: tuple[dict[str, Any], ...]
    process_model: dict[str, Any]
    health_checks: tuple[dict[str, Any], ...]
    rollback_plan: dict[str, Any]
    operator_runbook: dict[str, Any]
    production_blockers: tuple[dict[str, Any], ...]
    active_production_blockers: tuple[dict[str, Any], ...]
    deployment_policy_source: str
    deployment_policy_hash: str
    deployment_registry_source: str
    deployment_registry_hash: str
    production_packaging_smoke_present: bool
    production_packaging_smoke_hash: str
    external_signed_attestation_present: bool
    release_attestation_hash: str
    external_deployment_smoke_hash: str
    container_image_attestation_present: bool
    external_deployment_smoke_present: bool
    config_layers: tuple[dict[str, str], ...]
    required_artifact_hashes: dict[str, str]
    topology_hash: str
    topology_contract_hash: str
    service_boundary_hash: str
    service_placement_hash: str
    network_edge_hash: str
    storage_volume_hash: str
    process_model_hash: str
    health_check_hash: str
    rollback_plan_hash: str
    operator_runbook_hash: str
    production_blocker_hash: str
    active_production_blocker_hash: str
    config_layer_hash: str
    artifact_index_hash: str
    supply_chain_policy_hash: str
    release_manifest_hash: str
    deployment_manifest_hash: str
    non_core_commit_authority_count: int
    topology_cannot_weaken_policy: bool
    remote_workers_candidate_only: bool
    service_placements_cover_boundaries: bool
    single_writer_placement_enforced: bool
    network_edges_candidate_only: bool
    durable_storage_defined: bool
    topology_contract_materialized: bool
    durable_volumes_materialized: bool
    health_checks_defined: bool
    operator_rollback_defined: bool
    operator_runbook_defined: bool
    production_blockers_declared: bool
    production_deployable: bool
    overall_ok: bool
    checks: tuple[dict[str, Any], ...]

    def to_report(self) -> dict[str, Any]:
        return {
            "suite_name": "AEGIS Deployment Manifest",
            "schema": "aegis-deployment-manifest-v1",
            "truth_claim": False,
            "verifier": "hash-bound-release-topology-contract",
            "root": self.root,
            "release_profile": self.release_profile,
            "topology_id": self.topology_id,
            "topology": self.topology_contract,
            "topology_contract": self.topology_contract,
            "python_bridge_ready": self.python_bridge_ready,
            "rust_core_ready": self.rust_core_ready,
            "packaging_ready": self.packaging_ready,
            "service_surface_ready": self.service_surface_ready,
            "cargo_manifest_ready": self.cargo_manifest_ready,
            "docs_ready": self.docs_ready,
            "artifacts_dir_ready": self.artifacts_dir_ready,
            "operator_api_ready": self.operator_api_ready,
            "service_boundaries": list(self.service_boundaries),
            "topology_nodes": list(self.topology_nodes),
            "service_placements": list(self.service_placements),
            "network_edges": list(self.network_edges),
            "storage_volumes": list(self.storage_volumes),
            "durable_volumes": list(self.storage_volumes),
            "process_model": self.process_model,
            "health_checks": list(self.health_checks),
            "rollback_plan": self.rollback_plan,
            "operator_runbook": self.operator_runbook,
            "production_blockers": list(self.production_blockers),
            "active_production_blockers": list(self.active_production_blockers),
            "deployment_policy_source": self.deployment_policy_source,
            "deployment_policy_hash": self.deployment_policy_hash,
            "deployment_registry_source": self.deployment_registry_source,
            "deployment_registry_hash": self.deployment_registry_hash,
            "production_packaging_smoke_present": self.production_packaging_smoke_present,
            "production_packaging_smoke_hash": self.production_packaging_smoke_hash,
            "external_signed_attestation_present": self.external_signed_attestation_present,
            "release_attestation_hash": self.release_attestation_hash,
            "external_deployment_smoke_hash": self.external_deployment_smoke_hash,
            "container_image_attestation_present": self.container_image_attestation_present,
            "external_deployment_smoke_present": self.external_deployment_smoke_present,
            "config_layers": list(self.config_layers),
            "required_artifact_hashes": self.required_artifact_hashes,
            "topology_hash": self.topology_hash,
            "topology_contract_hash": self.topology_contract_hash,
            "service_boundary_hash": self.service_boundary_hash,
            "service_placement_hash": self.service_placement_hash,
            "network_edge_hash": self.network_edge_hash,
            "storage_volume_hash": self.storage_volume_hash,
            "process_model_hash": self.process_model_hash,
            "health_check_hash": self.health_check_hash,
            "rollback_plan_hash": self.rollback_plan_hash,
            "operator_runbook_hash": self.operator_runbook_hash,
            "production_blocker_hash": self.production_blocker_hash,
            "active_production_blocker_hash": self.active_production_blocker_hash,
            "config_layer_hash": self.config_layer_hash,
            "artifact_index_hash": self.artifact_index_hash,
            "supply_chain_policy_hash": self.supply_chain_policy_hash,
            "release_manifest_hash": self.release_manifest_hash,
            "deployment_manifest_hash": self.deployment_manifest_hash,
            "non_core_commit_authority_count": self.non_core_commit_authority_count,
            "topology_cannot_weaken_policy": self.topology_cannot_weaken_policy,
            "remote_workers_candidate_only": self.remote_workers_candidate_only,
            "service_placements_cover_boundaries": self.service_placements_cover_boundaries,
            "single_writer_placement_enforced": self.single_writer_placement_enforced,
            "network_edges_candidate_only": self.network_edges_candidate_only,
            "durable_storage_defined": self.durable_storage_defined,
            "topology_contract_materialized": self.topology_contract_materialized,
            "durable_volumes_materialized": self.durable_volumes_materialized,
            "health_checks_defined": self.health_checks_defined,
            "operator_rollback_defined": self.operator_rollback_defined,
            "operator_runbook_defined": self.operator_runbook_defined,
            "production_blockers_declared": self.production_blockers_declared,
            "supply_chain_sbom_required_before_prod": True,
            "supply_chain_provenance_required_before_prod": True,
            "wasi_deny_by_default_required_before_prod": True,
            "external_signed_attestation_required_before_prod": True,
            "production_deployable": self.production_deployable,
            "checks": list(self.checks),
            "passed": sum(1 for check in self.checks if check["ok"]),
            "failed": sum(1 for check in self.checks if not check["ok"]),
            "overall_ok": self.overall_ok,
        }


def build_deployment_manifest(root: str | Path) -> DeploymentManifest:
    root_path = Path(root)
    artifacts_dir = root_path / "artifacts"
    production_blockers, policy_hash, registry_info = _load_deployment_policy(root_path)
    python_bridge_ready = (root_path / "core" / "python" / "bridge.py").exists()
    rust_core_ready = (root_path / "core" / "rust" / "src" / "lib.rs").exists()
    packaging_ready = (root_path / "pyproject.toml").exists() and python_bridge_ready and rust_core_ready
    service_surface_ready = (
        (root_path / "scripts" / "run_checks.py").exists()
        and (root_path / "core" / "python" / "service.py").exists()
    )
    operator_api_ready = (root_path / "core" / "python" / "operator_api.py").exists()
    cargo_manifest_ready = (
        (root_path / "Cargo.toml").exists()
        and (root_path / "core" / "rust" / "Cargo.toml").exists()
    )
    docs_ready = (root_path / "PROJECT_OVERVIEW_DETAILED.md").exists()
    artifacts_dir_ready = artifacts_dir.is_dir()

    artifact_hashes = {
        artifact_name: _file_hash(artifacts_dir / artifact_name)
        for artifact_name in REQUIRED_RELEASE_ARTIFACTS
    }
    packaging_smoke_gate = _read_json(artifacts_dir / "production_packaging_smoke_gate_report.json")
    production_packaging_smoke_present = (
        packaging_smoke_gate.get("overall_ok") is True
        and packaging_smoke_gate.get("production_packaging_smoke_present") is True
        and _nonzero_hex(str(packaging_smoke_gate.get("smoke_evidence_hash", "")))
    )
    production_packaging_smoke_hash = str(packaging_smoke_gate.get("smoke_evidence_hash", ""))
    external_deployment_smoke_gate = _read_json(artifacts_dir / "external_deployment_smoke_gate_report.json")
    external_deployment_smoke_hash = str(
        external_deployment_smoke_gate.get("external_deployment_smoke_evidence_hash", "")
    )
    supply_chain_gate = _read_json(artifacts_dir / "supply_chain_gate_report.json")
    release_attestation = supply_chain_gate.get("release_attestation", {})
    release_attestation_hash = str(release_attestation.get("release_attestation_evidence_hash", ""))
    external_signed_attestation_present = (
        supply_chain_gate.get("external_signed_attestation_present") is True
        and release_attestation.get("status") == "verified"
        and _nonzero_hex(release_attestation_hash)
    )
    container_image_attestation_present = (
        external_deployment_smoke_gate.get("container_image_attestation_present") is True
    )
    external_deployment_smoke_present = (
        external_deployment_smoke_gate.get("external_deployment_smoke_present") is True
    )
    non_core_commit_authority_count = sum(
        1
        for boundary in SERVICE_BOUNDARIES
        if boundary["name"] != "aegis-core" and boundary["can_commit_replay"]
    )
    topology_cannot_weaken_policy = all(
        boundary["policy_window_required"] and not boundary["policy_bypass_allowed"]
        for boundary in SERVICE_BOUNDARIES
    )
    remote_workers_candidate_only = all(
        boundary["candidate_only"] and not boundary["can_commit_replay"]
        for boundary in SERVICE_BOUNDARIES
        if boundary["role"] == "remote_worker"
    )
    service_placements_cover_boundaries = _service_placements_cover_boundaries(
        SERVICE_BOUNDARIES,
        SERVICE_PLACEMENTS,
        TOPOLOGY_NODES,
    )
    single_writer_placement_enforced = _single_writer_placement_enforced(
        SERVICE_PLACEMENTS,
        TOPOLOGY_NODES,
    )
    network_edges_candidate_only = _network_edges_candidate_only(NETWORK_EDGES)
    durable_storage_defined = _durable_storage_defined(STORAGE_VOLUMES)
    health_checks_defined = _health_checks_defined(SERVICE_BOUNDARIES, HEALTH_CHECKS)
    operator_rollback_defined = _operator_rollback_defined(ROLLBACK_PLAN)
    operator_runbook_defined = _operator_runbook_defined(OPERATOR_RUNBOOK)
    production_blockers_declared = _production_blockers_declared(
        production_blockers,
        REQUIRED_RELEASE_ARTIFACTS,
        registry_info["registry_ids"],
    )
    quickjs_cold_start_gate = _read_json(artifacts_dir / "quickjs_cold_start_gate_report.json")
    tcp_cluster_soak_gate = _read_json(artifacts_dir / "tcp_cluster_soak_gate_report.json")
    dynamic_provider_fallback_gate = _read_json(artifacts_dir / "dynamic_provider_fallback_gate_report.json")
    active_production_blockers = _active_production_blockers(
        blockers=production_blockers,
        external_signed_attestation_present=external_signed_attestation_present,
        real_multi_node_cluster_present=tcp_cluster_soak_gate.get("real_multi_node_cluster_test_present") is True,
        full_quickjs_interpreter_present=(
            quickjs_cold_start_gate.get("real_quickjs_interpreter_cold_start_present") is True
            or quickjs_cold_start_gate.get("quickjs_cold_start_evidence", {}).get("full_quickjs_interpreter_present") is True
        ),
        live_provider_traffic_present=dynamic_provider_fallback_gate.get("live_provider_traffic_present") is True,
        external_deployment_smoke_present=external_deployment_smoke_present,
    )
    production_deployable = len(active_production_blockers) == 0
    config_layers = _config_layers(root_path)
    topology_contract = {
        "schema": "aegis-deployment-topology-contract-v1",
        "truth_claim": False,
        "hash_algorithm": "sha256-canonical-json",
        "release_profile": RELEASE_PROFILE,
        "topology_id": DEPLOYMENT_TOPOLOGY,
        "nodes": TOPOLOGY_NODES,
        "service_boundaries": SERVICE_BOUNDARIES,
        "service_placements": SERVICE_PLACEMENTS,
        "network_edges": NETWORK_EDGES,
        "durable_volumes": STORAGE_VOLUMES,
        "storage_volumes": STORAGE_VOLUMES,
        "process_model": PROCESS_MODEL,
        "health_checks": HEALTH_CHECKS,
        "rollback_plan": ROLLBACK_PLAN,
        "operator_runbook": OPERATOR_RUNBOOK,
        "contract_invariants": {
            "topology_cannot_weaken_policy": topology_cannot_weaken_policy,
            "remote_workers_candidate_only": remote_workers_candidate_only,
            "service_placements_cover_boundaries": service_placements_cover_boundaries,
            "single_writer_placement_enforced": single_writer_placement_enforced,
            "network_edges_candidate_only": network_edges_candidate_only,
            "durable_storage_defined": durable_storage_defined,
            "health_checks_defined": health_checks_defined,
            "operator_rollback_defined": operator_rollback_defined,
            "operator_runbook_defined": operator_runbook_defined,
            "non_core_commit_authority_count": non_core_commit_authority_count,
        },
        "production_blockers": production_blockers,
        "active_production_blockers": active_production_blockers,
        "production_deployable": production_deployable,
        "production_packaging_smoke_hash": production_packaging_smoke_hash,
        "release_attestation_hash": release_attestation_hash,
        "external_signed_attestation_present": external_signed_attestation_present,
        "external_deployment_smoke_hash": external_deployment_smoke_hash,
        "container_image_attestation_present": container_image_attestation_present,
        "external_deployment_smoke_present": external_deployment_smoke_present,
    }
    topology_hash = _stable_hash(
        {
            "release_profile": RELEASE_PROFILE,
            "topology_id": DEPLOYMENT_TOPOLOGY,
            "service_names": [boundary["name"] for boundary in SERVICE_BOUNDARIES],
        }
    )
    topology_contract_hash = _stable_hash(topology_contract)
    topology_contract_materialized = _topology_contract_materialized(topology_contract)
    durable_volumes_materialized = _durable_volumes_materialized(topology_contract)
    service_boundary_hash = _stable_hash(SERVICE_BOUNDARIES)
    service_placement_hash = _stable_hash(SERVICE_PLACEMENTS)
    network_edge_hash = _stable_hash(NETWORK_EDGES)
    storage_volume_hash = _stable_hash(STORAGE_VOLUMES)
    process_model_hash = _stable_hash(PROCESS_MODEL)
    health_check_hash = _stable_hash(HEALTH_CHECKS)
    rollback_plan_hash = _stable_hash(ROLLBACK_PLAN)
    operator_runbook_hash = _stable_hash(OPERATOR_RUNBOOK)
    production_blocker_hash = _stable_hash(production_blockers)
    active_production_blocker_hash = _stable_hash(active_production_blockers)
    config_layer_hash = _stable_hash(config_layers)
    artifact_index_hash = _stable_hash(artifact_hashes)
    supply_chain_policy_hash = _stable_hash(
        {
            "sbom_required_before_prod": True,
            "provenance_required_before_prod": True,
            "wasi_deny_by_default_required_before_prod": True,
            "external_signed_attestation_required_before_prod": True,
            "current_slice": "topology-contract-plus-internal-sbom-provenance-wasi-deny",
            "release_attestation_hash": release_attestation_hash,
        }
    )
    release_manifest_hash = _stable_hash(
        {
            "topology_hash": topology_hash,
            "topology_contract_hash": topology_contract_hash,
            "service_boundary_hash": service_boundary_hash,
            "service_placement_hash": service_placement_hash,
            "network_edge_hash": network_edge_hash,
            "storage_volume_hash": storage_volume_hash,
            "process_model_hash": process_model_hash,
            "health_check_hash": health_check_hash,
            "rollback_plan_hash": rollback_plan_hash,
            "operator_runbook_hash": operator_runbook_hash,
            "production_blocker_hash": production_blocker_hash,
            "active_production_blocker_hash": active_production_blocker_hash,
            "production_packaging_smoke_hash": production_packaging_smoke_hash,
            "release_attestation_hash": release_attestation_hash,
            "external_deployment_smoke_hash": external_deployment_smoke_hash,
            "config_layer_hash": config_layer_hash,
            "artifact_index_hash": artifact_index_hash,
            "supply_chain_policy_hash": supply_chain_policy_hash,
        }
    )
    check_rows = (
        _check("packaging_surface_ready", packaging_ready),
        _check("service_surface_ready", service_surface_ready),
        _check("cargo_manifest_ready", cargo_manifest_ready),
        _check("docs_ready", docs_ready),
        _check("artifacts_dir_ready", artifacts_dir_ready),
        _check("operator_api_ready", operator_api_ready),
        _check("single_writer_core_present", _single_writer_core_present(SERVICE_BOUNDARIES)),
        _check("non_core_services_cannot_commit", non_core_commit_authority_count == 0),
        _check("topology_cannot_weaken_policy", topology_cannot_weaken_policy),
        _check("remote_workers_candidate_only", remote_workers_candidate_only),
        _check("service_placements_cover_boundaries", service_placements_cover_boundaries),
        _check("single_writer_placement_enforced", single_writer_placement_enforced),
        _check("network_edges_candidate_only", network_edges_candidate_only),
        _check("durable_storage_defined", durable_storage_defined),
        _check("topology_contract_materialized", topology_contract_materialized),
        _check("durable_volumes_materialized", durable_volumes_materialized),
        _check("health_checks_defined", health_checks_defined),
        _check("operator_rollback_defined", operator_rollback_defined),
        _check("operator_runbook_defined", operator_runbook_defined),
        _check("production_blockers_declared", production_blockers_declared),
        _check("production_blockers_derived_from_policy", bool(policy_hash)),
        _check("production_blockers_reference_registry", production_blockers_declared),
        _check("active_production_blocker_hash_bound", _nonzero_hex(active_production_blocker_hash)),
        _check("release_attestation_hash_bound", _nonzero_hex(release_attestation_hash)),
        _check("production_packaging_smoke_present", production_packaging_smoke_present),
        _check("external_deployment_smoke_gate_bound", _nonzero_hex(external_deployment_smoke_hash)),
        _check("topology_contract_hash_bound", _nonzero_hex(topology_contract_hash)),
        _check("topology_contract_schema_bound", topology_contract.get("schema") == "aegis-deployment-topology-contract-v1"),
        _check("required_release_artifacts_hashed", all(artifact_hashes.values())),
        _check("release_manifest_hash_bound", _nonzero_hex(release_manifest_hash)),
        _check("production_status_consistent", production_deployable == (len(active_production_blockers) == 0)),
        _check("production_gap_disclosed", production_deployable or len(active_production_blockers) > 0),
    )
    unsigned = {
        "release_profile": RELEASE_PROFILE,
        "topology_id": DEPLOYMENT_TOPOLOGY,
        "topology_hash": topology_hash,
        "topology_contract_hash": topology_contract_hash,
        "service_boundary_hash": service_boundary_hash,
        "service_placement_hash": service_placement_hash,
        "network_edge_hash": network_edge_hash,
        "storage_volume_hash": storage_volume_hash,
        "process_model_hash": process_model_hash,
        "health_check_hash": health_check_hash,
        "rollback_plan_hash": rollback_plan_hash,
        "operator_runbook_hash": operator_runbook_hash,
        "production_blocker_hash": production_blocker_hash,
        "active_production_blocker_hash": active_production_blocker_hash,
        "production_packaging_smoke_hash": production_packaging_smoke_hash,
        "release_attestation_hash": release_attestation_hash,
        "external_deployment_smoke_hash": external_deployment_smoke_hash,
        "config_layer_hash": config_layer_hash,
        "artifact_index_hash": artifact_index_hash,
        "release_manifest_hash": release_manifest_hash,
        "checks": check_rows,
    }
    deployment_manifest_hash = _stable_hash(unsigned)
    overall_ok = all(check["ok"] for check in check_rows)
    return DeploymentManifest(
        root=str(root_path),
        python_bridge_ready=python_bridge_ready,
        rust_core_ready=rust_core_ready,
        packaging_ready=packaging_ready,
        service_surface_ready=service_surface_ready,
        cargo_manifest_ready=cargo_manifest_ready,
        docs_ready=docs_ready,
        artifacts_dir_ready=artifacts_dir_ready,
        operator_api_ready=operator_api_ready,
        release_profile=RELEASE_PROFILE,
        topology_id=DEPLOYMENT_TOPOLOGY,
        topology_contract=topology_contract,
        service_boundaries=SERVICE_BOUNDARIES,
        topology_nodes=TOPOLOGY_NODES,
        service_placements=SERVICE_PLACEMENTS,
        network_edges=NETWORK_EDGES,
        storage_volumes=STORAGE_VOLUMES,
        process_model=PROCESS_MODEL,
        health_checks=HEALTH_CHECKS,
        rollback_plan=ROLLBACK_PLAN,
        operator_runbook=OPERATOR_RUNBOOK,
        production_blockers=production_blockers,
        active_production_blockers=active_production_blockers,
        deployment_policy_source=registry_info["policy_source"],
        deployment_policy_hash=policy_hash,
        deployment_registry_source=registry_info["registry_source"],
        deployment_registry_hash=registry_info["registry_hash"],
        production_packaging_smoke_present=production_packaging_smoke_present,
        production_packaging_smoke_hash=production_packaging_smoke_hash,
        external_signed_attestation_present=external_signed_attestation_present,
        release_attestation_hash=release_attestation_hash,
        external_deployment_smoke_hash=external_deployment_smoke_hash,
        container_image_attestation_present=container_image_attestation_present,
        external_deployment_smoke_present=external_deployment_smoke_present,
        config_layers=config_layers,
        required_artifact_hashes=artifact_hashes,
        topology_hash=topology_hash,
        topology_contract_hash=topology_contract_hash,
        service_boundary_hash=service_boundary_hash,
        service_placement_hash=service_placement_hash,
        network_edge_hash=network_edge_hash,
        storage_volume_hash=storage_volume_hash,
        process_model_hash=process_model_hash,
        health_check_hash=health_check_hash,
        rollback_plan_hash=rollback_plan_hash,
        operator_runbook_hash=operator_runbook_hash,
        production_blocker_hash=production_blocker_hash,
        active_production_blocker_hash=active_production_blocker_hash,
        config_layer_hash=config_layer_hash,
        artifact_index_hash=artifact_index_hash,
        supply_chain_policy_hash=supply_chain_policy_hash,
        release_manifest_hash=release_manifest_hash,
        deployment_manifest_hash=deployment_manifest_hash,
        non_core_commit_authority_count=non_core_commit_authority_count,
        topology_cannot_weaken_policy=topology_cannot_weaken_policy,
        remote_workers_candidate_only=remote_workers_candidate_only,
        service_placements_cover_boundaries=service_placements_cover_boundaries,
        single_writer_placement_enforced=single_writer_placement_enforced,
        network_edges_candidate_only=network_edges_candidate_only,
        durable_storage_defined=durable_storage_defined,
        topology_contract_materialized=topology_contract_materialized,
        durable_volumes_materialized=durable_volumes_materialized,
        health_checks_defined=health_checks_defined,
        operator_rollback_defined=operator_rollback_defined,
        operator_runbook_defined=operator_runbook_defined,
        production_blockers_declared=production_blockers_declared,
        production_deployable=production_deployable,
        overall_ok=overall_ok,
        checks=check_rows,
    )


def _single_writer_core_present(boundaries: tuple[dict[str, Any], ...]) -> bool:
    writers = [boundary for boundary in boundaries if boundary["can_commit_replay"]]
    return len(writers) == 1 and writers[0]["name"] == "aegis-core"


def _service_placements_cover_boundaries(
    boundaries: tuple[dict[str, Any], ...],
    placements: tuple[dict[str, Any], ...],
    nodes: tuple[dict[str, Any], ...],
) -> bool:
    boundary_names = {boundary["name"] for boundary in boundaries}
    placement_services = {placement["service"] for placement in placements}
    node_ids = {node["node_id"] for node in nodes}
    return (
        boundary_names == placement_services
        and all(placement["node_id"] in node_ids for placement in placements)
        and all(placement["replicas"] >= 1 for placement in placements)
    )


def _single_writer_placement_enforced(
    placements: tuple[dict[str, Any], ...],
    nodes: tuple[dict[str, Any], ...],
) -> bool:
    writer_placements = [
        placement
        for placement in placements
        if placement["state_writer"] or placement["can_commit_replay"]
    ]
    writer_nodes = [node for node in nodes if node["commit_authority"]]
    return (
        len(writer_placements) == 1
        and writer_placements[0]["service"] == "aegis-core"
        and writer_placements[0]["replicas"] == 1
        and len(writer_nodes) == 1
        and writer_nodes[0]["node_id"] == writer_placements[0]["node_id"]
    )


def _network_edges_candidate_only(edges: tuple[dict[str, Any], ...]) -> bool:
    return all(
        edge["policy_window_required"]
        and not edge["can_commit_replay"]
        and edge["allowed_payload"]
        in {
            "candidate_tool_ir",
            "artifact_candidate_ref",
            "candidate_execution_result",
            "read_only_status_and_review_packet",
            "policy_approved_llm_request",
        }
        for edge in edges
    )


def _durable_storage_defined(volumes: tuple[dict[str, Any], ...]) -> bool:
    by_name = {volume["name"]: volume for volume in volumes}
    required = ("replay-ledger", "shadow-seals", "evidence-artifacts")
    return all(
        name in by_name
        and by_name[name]["owner_service"] == "aegis-core"
        and by_name[name]["fsync_required"]
        and by_name[name]["encrypted_at_rest_required"]
        for name in required
    ) and by_name["replay-ledger"]["append_only"] and by_name["shadow-seals"]["append_only"]


def _topology_contract_materialized(contract: dict[str, Any]) -> bool:
    invariants = contract.get("contract_invariants", {})
    required_top_level = (
        "schema",
        "release_profile",
        "topology_id",
        "nodes",
        "service_boundaries",
        "service_placements",
        "network_edges",
        "durable_volumes",
        "process_model",
        "health_checks",
        "rollback_plan",
        "operator_runbook",
        "production_blockers",
        "active_production_blockers",
        "production_deployable",
    )
    required_invariants = (
        "topology_cannot_weaken_policy",
        "remote_workers_candidate_only",
        "service_placements_cover_boundaries",
        "single_writer_placement_enforced",
        "network_edges_candidate_only",
        "durable_storage_defined",
        "health_checks_defined",
        "operator_rollback_defined",
        "operator_runbook_defined",
        "non_core_commit_authority_count",
    )
    return (
        contract.get("schema") == "aegis-deployment-topology-contract-v1"
        and contract.get("truth_claim") is False
        and contract.get("hash_algorithm") == "sha256-canonical-json"
        and all(key in contract for key in required_top_level)
        and all(key in invariants for key in required_invariants)
        and invariants.get("non_core_commit_authority_count") == 0
        and all(
            invariants.get(key) is True
            for key in required_invariants
            if key != "non_core_commit_authority_count"
        )
    )


def _durable_volumes_materialized(contract: dict[str, Any]) -> bool:
    volumes = contract.get("durable_volumes")
    if not isinstance(volumes, tuple):
        return False
    return _durable_storage_defined(volumes)


def _health_checks_defined(
    boundaries: tuple[dict[str, Any], ...],
    health_checks: tuple[dict[str, Any], ...],
) -> bool:
    service_names = {boundary["name"] for boundary in boundaries}
    checked_services = {check["service"] for check in health_checks}
    return service_names == checked_services and all(
        check["liveness"] and check["readiness"] for check in health_checks
    )


def _operator_rollback_defined(plan: dict[str, Any]) -> bool:
    steps = plan.get("steps", ())
    return (
        plan.get("operator_rollback_defined") is True
        and plan.get("rollback_requires_dual_operator_approval") is True
        and plan.get("preserve_replay_ledger") is True
        and plan.get("preserve_shadow_seals") is True
        and isinstance(steps, tuple)
        and len(steps) >= 5
        and "run-replay-prefix-recovery-gate" in steps
    )


def _operator_runbook_defined(runbook: dict[str, Any]) -> bool:
    return (
        runbook.get("runbook_defined") is True
        and len(runbook.get("preflight", ())) >= 2
        and len(runbook.get("runtime_monitors", ())) >= 3
        and len(runbook.get("emergency_stop", ())) >= 2
    )


def _load_deployment_policy(root_path: Path) -> tuple[tuple[dict[str, Any], ...], str, dict[str, Any]]:
    policy_path = root_path / DEPLOYMENT_POLICY_RELATIVE_PATH
    registry_path = root_path / NOT_VERIFIED_REGISTRY_RELATIVE_PATH
    policy = _read_json(policy_path)
    blockers_value = policy.get("blockers")
    blockers = tuple(item for item in blockers_value if isinstance(item, dict)) if isinstance(blockers_value, list) else ()
    if not blockers:
        blockers = PRODUCTION_BLOCKERS
    registry = _read_json(registry_path)
    registry_entries = registry.get("entries", [])
    registry_ids = {
        str(entry.get("id"))
        for entry in registry_entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if not registry_ids:
        registry_ids = {
            registry_id
            for blocker in blockers
            for registry_id in blocker.get("registry_ids", [])
            if isinstance(registry_id, str)
        }
    return (
        blockers,
        _file_hash(policy_path),
        {
            "policy_source": DEPLOYMENT_POLICY_RELATIVE_PATH if policy_path.is_file() else "embedded-fallback",
            "registry_source": NOT_VERIFIED_REGISTRY_RELATIVE_PATH if registry_path.is_file() else "embedded-fallback",
            "registry_hash": _file_hash(registry_path),
            "registry_ids": registry_ids,
        },
    )


def _production_blockers_declared(
    blockers: tuple[dict[str, Any], ...],
    required_artifacts: tuple[str, ...],
    registry_ids: set[str],
) -> bool:
    artifact_names = set(required_artifacts) | {"deployment_manifest_report.json"}
    observed_ids = {blocker["id"] for blocker in blockers}
    referenced_registry_ids = {
        registry_id
        for blocker in blockers
        for registry_id in blocker.get("registry_ids", [])
        if isinstance(registry_id, str)
    }
    return bool(observed_ids) and len(observed_ids) == len(blockers) and all(
        blocker["blocks_production"] is True
        and blocker["evidence_artifact"] in artifact_names
        and referenced_registry_ids
        and set(blocker.get("registry_ids", [])) <= registry_ids
        for blocker in blockers
    )


def _active_production_blockers(
    *,
    blockers: tuple[dict[str, Any], ...],
    external_signed_attestation_present: bool,
    real_multi_node_cluster_present: bool,
    full_quickjs_interpreter_present: bool,
    live_provider_traffic_present: bool,
    external_deployment_smoke_present: bool,
) -> tuple[dict[str, Any], ...]:
    blocker_state = {
        "external_signed_attestation_missing": not external_signed_attestation_present,
        "real_multi_machine_cluster_soak_missing": not real_multi_node_cluster_present,
        "full_quickjs_interpreter_cold_start_missing": not full_quickjs_interpreter_present,
        "live_provider_429_soak_missing": not live_provider_traffic_present,
        "external_deployment_smoke_missing": not external_deployment_smoke_present,
    }
    return tuple(
        {
            **blocker,
            "active": True,
        }
        for blocker in blockers
        if blocker_state.get(str(blocker["id"]), False)
    )


def _config_layers(root_path: Path) -> tuple[dict[str, str], ...]:
    return (
        {
            "name": "python-package",
            "path": "pyproject.toml",
            "hash": _file_hash(root_path / "pyproject.toml"),
        },
        {
            "name": "rust-workspace",
            "path": "Cargo.toml",
            "hash": _file_hash(root_path / "Cargo.toml"),
        },
        {
            "name": "rust-core-crate",
            "path": "core/rust/Cargo.toml",
            "hash": _file_hash(root_path / "core" / "rust" / "Cargo.toml"),
        },
        {
            "name": "operator-env-template",
            "path": ".env.example",
            "hash": _file_hash(root_path / ".env.example"),
        },
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


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


def _nonzero_hex(value: str) -> bool:
    return len(value) == 64 and any(char != "0" for char in value)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    manifest = build_deployment_manifest(ROOT)
    report = manifest.to_report()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
