import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
import hashlib
import sys
from importlib import import_module

if TYPE_CHECKING:
    from scripts.cluster_loopback_gate import evaluate_cluster_loopback_gate
    from scripts.deployment_manifest import build_deployment_manifest
    from scripts.dynamic_provider_fallback_gate import evaluate_dynamic_provider_fallback_gate
    from scripts.external_deployment_smoke_gate import (
        evaluate_external_deployment_smoke_gate,
        write_external_deployment_smoke_capture,
    )
    from scripts.hot_browser_shadow_gate import evaluate_hot_browser_shadow_gate
    from scripts.production_packaging_smoke_gate import evaluate_production_packaging_smoke_gate
    from scripts.quickjs_cold_start_gate import evaluate_quickjs_cold_start_gate
    from scripts.shadow_sealer_soak_gate import evaluate_shadow_sealer_soak_gate
    from scripts.supply_chain_gate import evaluate_supply_chain_gate
    from scripts.tcp_cluster_soak_gate import evaluate_tcp_cluster_soak_gate

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_e2e_dependencies() -> None:
    modules = {
        name: import_module(name)
        for name in (
            "scripts.cluster_loopback_gate",
            "scripts.deployment_manifest",
            "scripts.dynamic_provider_fallback_gate",
            "scripts.external_deployment_smoke_gate",
            "scripts.hot_browser_shadow_gate",
            "scripts.production_packaging_smoke_gate",
            "scripts.quickjs_cold_start_gate",
            "scripts.shadow_sealer_soak_gate",
            "scripts.supply_chain_gate",
            "scripts.tcp_cluster_soak_gate",
        )
    }
    bindings = {
        "evaluate_cluster_loopback_gate": ("scripts.cluster_loopback_gate", "evaluate_cluster_loopback_gate"),
        "build_deployment_manifest": ("scripts.deployment_manifest", "build_deployment_manifest"),
        "evaluate_dynamic_provider_fallback_gate": ("scripts.dynamic_provider_fallback_gate", "evaluate_dynamic_provider_fallback_gate"),
        "evaluate_external_deployment_smoke_gate": ("scripts.external_deployment_smoke_gate", "evaluate_external_deployment_smoke_gate"),
        "write_external_deployment_smoke_capture": ("scripts.external_deployment_smoke_gate", "write_external_deployment_smoke_capture"),
        "evaluate_hot_browser_shadow_gate": ("scripts.hot_browser_shadow_gate", "evaluate_hot_browser_shadow_gate"),
        "evaluate_production_packaging_smoke_gate": ("scripts.production_packaging_smoke_gate", "evaluate_production_packaging_smoke_gate"),
        "evaluate_quickjs_cold_start_gate": ("scripts.quickjs_cold_start_gate", "evaluate_quickjs_cold_start_gate"),
        "evaluate_shadow_sealer_soak_gate": ("scripts.shadow_sealer_soak_gate", "evaluate_shadow_sealer_soak_gate"),
        "evaluate_supply_chain_gate": ("scripts.supply_chain_gate", "evaluate_supply_chain_gate"),
        "evaluate_tcp_cluster_soak_gate": ("scripts.tcp_cluster_soak_gate", "evaluate_tcp_cluster_soak_gate"),
    }
    globals().update({name: getattr(modules[module], attribute) for name, (module, attribute) in bindings.items()})


_load_e2e_dependencies()

ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "e2e_release_gate_report.json"
DEPLOYMENT_MANIFEST_REPORT_PATH = ARTIFACTS_DIR / "deployment_manifest_report.json"
SUPPLY_CHAIN_GATE_REPORT_PATH = ARTIFACTS_DIR / "supply_chain_gate_report.json"
CLUSTER_LOOPBACK_GATE_REPORT_PATH = ARTIFACTS_DIR / "cluster_loopback_gate_report.json"
QUICKJS_COLD_START_GATE_REPORT_PATH = ARTIFACTS_DIR / "quickjs_cold_start_gate_report.json"
TCP_CLUSTER_SOAK_GATE_REPORT_PATH = ARTIFACTS_DIR / "tcp_cluster_soak_gate_report.json"
HOT_BROWSER_SHADOW_GATE_REPORT_PATH = ARTIFACTS_DIR / "hot_browser_shadow_gate_report.json"
SHADOW_SEALER_SOAK_GATE_REPORT_PATH = ARTIFACTS_DIR / "shadow_sealer_soak_gate_report.json"
DYNAMIC_PROVIDER_FALLBACK_GATE_REPORT_PATH = ARTIFACTS_DIR / "dynamic_provider_fallback_gate_report.json"
PRODUCTION_PACKAGING_SMOKE_GATE_REPORT_PATH = ARTIFACTS_DIR / "production_packaging_smoke_gate_report.json"
EXTERNAL_DEPLOYMENT_SMOKE_GATE_REPORT_PATH = ARTIFACTS_DIR / "external_deployment_smoke_gate_report.json"

STAGES: tuple[str, ...] = (
    "goal_intake",
    "task_plan",
    "context_pack",
    "typed_tool_ir",
    "policy_proof",
    "dynamic_provider_fallback",
    "tool_execution",
    "hot_browser_shadow",
    "evidence_ingest",
    "replay_append",
    "checkpoint_seal",
    "memory_candidate",
    "next_action",
    "cluster_loopback",
    "tcp_cluster_soak",
    "quickjs_cold_start",
    "supply_chain_hardening",
    "production_packaging_smoke",
    "external_deployment_smoke",
    "deployment_topology",
)

STAGE_REQUIREMENTS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "goal_intake": (("governance_gate_report.json", ("overall_ok",)),),
    "task_plan": (
        ("agentic_sdk_context_report.json", ("report", "active_task_id")),
        ("benchmark_gate_report.json", ("checks", "task_ledger_ready_queue_10k", "ok")),
    ),
    "context_pack": (
        ("agentic_sdk_context_report.json", ("report", "context_pack_digest")),
        ("agentic_sdk_context_report.json", ("report", "context_pack_candidate_proof_hash")),
    ),
    "typed_tool_ir": (
        ("benchmark_gate_report.json", ("checks", "browser_tool_gateway_ingest", "ok")),
        ("benchmark_gate_report.json", ("checks", "wasmtime_execute_cached_module", "ok")),
    ),
    "policy_proof": (
        ("benchmark_gate_report.json", ("checks", "policy_datalog_closure_proof", "ok")),
        ("governance_gate_report.json", ("checks", "provider_rate_limit_mitigation", "ok")),
        ("provider_route_gate_report.json", ("overall_ok",)),
        ("dependency_audit_gate_report.json", ("overall_ok",)),
    ),
    "dynamic_provider_fallback": (
        ("dynamic_provider_fallback_gate_report.json", ("overall_ok",)),
        ("dynamic_provider_fallback_gate_report.json", ("artifact_sha256",)),
        ("dynamic_provider_fallback_gate_report.json", ("dynamic_provider_fallback_evidence", "fallback_used")),
        ("dynamic_provider_fallback_gate_report.json", ("dynamic_provider_fallback_evidence", "downgraded_model")),
        ("dynamic_provider_fallback_gate_report.json", ("dynamic_provider_fallback_evidence", "throttled_provider_count")),
        ("dynamic_provider_fallback_gate_report.json", ("dynamic_provider_fallback_evidence", "latency_under_gate")),
        ("dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_admission", "schema")),
        ("dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_admission", "expected_capture_path")),
        ("dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_admission_hash",)),
        ("dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_capture_missing_or_valid",)),
        ("dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_admission_blocker_id",)),
        ("dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_state_recorded",)),
    ),
    "tool_execution": (
        ("browser_ops_bench_verification_report.json", ("report", "proof_hash")),
        ("benchmark_gate_report.json", ("checks", "browser_action_plan_live_manifest_gateway_ingest", "ok")),
    ),
    "hot_browser_shadow": (
        ("hot_browser_shadow_gate_report.json", ("overall_ok",)),
        ("hot_browser_shadow_gate_report.json", ("artifact_sha256",)),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "artifact_count")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "hot_commit_count")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "cold_seal_receipt_count")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_seal_replay_recorded")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_arrow_archive_recovered")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_arrow_archive_replay_matches")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_arrow_archive_contains_shadow_seal")),
        ("hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "no_file_roundtrip_on_hot_path")),
        ("shadow_sealer_soak_gate_report.json", ("overall_ok",)),
        ("shadow_sealer_soak_gate_report.json", ("artifact_sha256",)),
        ("shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "sample_count")),
        ("shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "hot_submit_under_gate")),
        ("shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "hot_submissions_completed_before_receipts")),
        ("shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "cold_payload_files_materialized")),
        ("shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "cold_payload_file_hashes_match")),
        ("shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "cold_payload_sync_requested")),
    ),
    "evidence_ingest": (
        ("browser_ops_bench_verification_report.json", ("report", "verified_records_hash")),
        ("browser_ops_bench_verification_report.json", ("report", "scorecard_file_hash")),
    ),
    "replay_append": (
        ("agentic_sdk_context_report.json", ("report", "sdk_run_replay_binding_hash")),
        ("replay_endurance_report.json", ("report", "segmented_arrow_audit_proof_hash")),
    ),
    "checkpoint_seal": (
        ("replay_endurance_report.json", ("report", "run_checkpoint_hash")),
        ("replay_endurance_report.json", ("report", "replay_determinism_proof_hash")),
    ),
    "memory_candidate": (
        ("agentic_sdk_context_report.json", ("report", "candidate_list_hash")),
        ("benchmark_gate_report.json", ("checks", "pav_ast_distance_registered", "ok")),
    ),
    "next_action": (
        ("agentic_sdk_context_report.json", ("report", "next_action_packet_hash")),
        ("replay_endurance_report.json", ("report", "next_action_packet_hash")),
    ),
    "cluster_loopback": (
        ("cluster_loopback_gate_report.json", ("overall_ok",)),
        ("cluster_loopback_gate_report.json", ("artifact_sha256",)),
        ("cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "worker_count")),
        ("cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "work_item_count")),
        ("cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "accepted_count")),
        ("cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "max_logical_rtt_ticks")),
        ("cluster_loopback_gate_report.json", ("production_cluster_release_blocked_without_real_multi_node_soak",)),
    ),
    "tcp_cluster_soak": (
        ("tcp_cluster_soak_gate_report.json", ("overall_ok",)),
        ("tcp_cluster_soak_gate_report.json", ("artifact_sha256",)),
        ("tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "worker_count")),
        ("tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "work_item_count")),
        ("tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "tcp_worker_connections")),
        ("tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "tcp_round_trip_count")),
        ("tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_admission", "schema")),
        ("tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_admission", "expected_capture_path")),
        ("tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_admission_hash",)),
        ("tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_capture_missing_or_valid",)),
        ("tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_admission_blocker_id",)),
        ("tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_state_recorded",)),
    ),
    "quickjs_cold_start": (
        ("quickjs_cold_start_gate_report.json", ("overall_ok",)),
        ("quickjs_cold_start_gate_report.json", ("artifact_sha256",)),
        ("quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "sample_count")),
        ("quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "cold_start_total_ns")),
        ("quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "warm_cached_total_ns")),
        ("quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "bridge_probe_executed")),
        ("quickjs_cold_start_gate_report.json", ("full_interpreter_admission", "schema")),
        ("quickjs_cold_start_gate_report.json", ("full_interpreter_admission", "expected_runtime_path")),
        ("quickjs_cold_start_gate_report.json", ("full_interpreter_admission_hash",)),
        ("quickjs_cold_start_gate_report.json", ("full_interpreter_semantic_capture_missing_or_valid",)),
        ("quickjs_cold_start_gate_report.json", ("full_interpreter_admission_blocker_id",)),
        ("quickjs_cold_start_gate_report.json", ("full_interpreter_state_recorded",)),
    ),
    "supply_chain_hardening": (
        ("supply_chain_gate_report.json", ("overall_ok",)),
        ("supply_chain_gate_report.json", ("sbom", "sbom_index_hash")),
        ("supply_chain_gate_report.json", ("provenance", "provenance_hash")),
        ("supply_chain_gate_report.json", ("wasi_deny_by_default", "wasmtime_fuel_enabled")),
        ("supply_chain_gate_report.json", ("wasi_deny_by_default", "wasmtime_epoch_enabled")),
        ("supply_chain_gate_report.json", ("wasi_deny_by_default", "wasmtime_memory_limited")),
        ("supply_chain_gate_report.json", ("wasi_deny_by_default", "sandbox_network_isolated")),
        ("supply_chain_gate_report.json", ("release_attestation", "attestation_state_recorded")),
        ("supply_chain_gate_report.json", ("release_attestation", "expected_subject_hash")),
        ("supply_chain_gate_report.json", ("release_attestation", "release_attestation_evidence_hash")),
    ),
    "production_packaging_smoke": (
        ("production_packaging_smoke_gate_report.json", ("overall_ok",)),
        ("production_packaging_smoke_gate_report.json", ("production_packaging_smoke_present",)),
        ("production_packaging_smoke_gate_report.json", ("package_surface_hash",)),
        ("production_packaging_smoke_gate_report.json", ("smoke_evidence_hash",)),
        ("production_packaging_smoke_gate_report.json", ("smoke_evidence", "friendly_gateway_hash")),
        ("production_packaging_smoke_gate_report.json", ("smoke_evidence", "service_manifest_hash")),
        ("production_packaging_smoke_gate_report.json", ("smoke_evidence", "artifact_write_hash")),
    ),
    "external_deployment_smoke": (
        ("external_deployment_smoke_gate_report.json", ("overall_ok",)),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_evidence_hash",)),
        ("external_deployment_smoke_gate_report.json", ("packaging_smoke_hash",)),
        ("external_deployment_smoke_gate_report.json", ("descriptor_index_hash",)),
        ("external_deployment_smoke_gate_report.json", ("runtime_probe_hash",)),
        ("external_deployment_smoke_gate_report.json", ("local_operator_health_hash",)),
        ("external_deployment_smoke_gate_report.json", ("external_health_hash",)),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_admission", "schema")),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_admission", "expected_capture_path")),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_admission_hash",)),
        # The legacy ``external_deployment_smoke_admission_missing`` field
        # remains disclosure-only;
        # a passing gate must be witnessed by a valid smoke capture.
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_present",)),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_admission_blocker_id",)),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_capture_missing_or_valid",)),
        ("external_deployment_smoke_gate_report.json", ("external_deployment_smoke_state_recorded",)),
        ("external_deployment_smoke_gate_report.json", ("container_attestation_state_recorded",)),
    ),
    "deployment_topology": (
        ("deployment_manifest_report.json", ("overall_ok",)),
        ("deployment_manifest_report.json", ("topology", "schema")),
        ("deployment_manifest_report.json", ("topology", "contract_invariants", "single_writer_placement_enforced")),
        ("deployment_manifest_report.json", ("topology", "contract_invariants", "durable_storage_defined")),
        ("deployment_manifest_report.json", ("topology", "durable_volumes", "replay-ledger", "append_only")),
        ("deployment_manifest_report.json", ("topology", "durable_volumes", "shadow-seals", "append_only")),
        ("deployment_manifest_report.json", ("topology_hash",)),
        ("deployment_manifest_report.json", ("topology_contract_hash",)),
        ("deployment_manifest_report.json", ("service_boundary_hash",)),
        ("deployment_manifest_report.json", ("service_placement_hash",)),
        ("deployment_manifest_report.json", ("network_edge_hash",)),
        ("deployment_manifest_report.json", ("storage_volume_hash",)),
        ("deployment_manifest_report.json", ("rollback_plan_hash",)),
        ("deployment_manifest_report.json", ("operator_runbook_hash",)),
        ("deployment_manifest_report.json", ("active_production_blocker_hash",)),
        ("deployment_manifest_report.json", ("release_manifest_hash",)),
        ("deployment_manifest_report.json", ("deployment_manifest_hash",)),
        ("deployment_manifest_report.json", ("topology_cannot_weaken_policy",)),
        ("deployment_manifest_report.json", ("remote_workers_candidate_only",)),
        ("deployment_manifest_report.json", ("service_placements_cover_boundaries",)),
        ("deployment_manifest_report.json", ("single_writer_placement_enforced",)),
        ("deployment_manifest_report.json", ("network_edges_candidate_only",)),
        ("deployment_manifest_report.json", ("durable_storage_defined",)),
        ("deployment_manifest_report.json", ("topology_contract_materialized",)),
        ("deployment_manifest_report.json", ("durable_volumes_materialized",)),
        ("deployment_manifest_report.json", ("health_checks_defined",)),
        ("deployment_manifest_report.json", ("operator_rollback_defined",)),
        ("deployment_manifest_report.json", ("operator_runbook_defined",)),
        ("deployment_manifest_report.json", ("production_blockers_declared",)),
        ("deployment_manifest_report.json", ("release_attestation_hash",)),
        ("deployment_manifest_report.json", ("production_packaging_smoke_present",)),
        ("deployment_manifest_report.json", ("production_packaging_smoke_hash",)),
        ("deployment_manifest_report.json", ("external_deployment_smoke_hash",)),
    ),
}


@dataclass(frozen=True)
class E2EStageCheck:
    name: str
    ok: bool
    artifact_refs: tuple[str, ...]
    digest: str
    detail: str
    status: str
    not_verified_reason: str


def evaluate_e2e_release_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifacts_dir = root_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    supply_chain_gate = evaluate_supply_chain_gate(root_path)
    (artifacts_dir / "supply_chain_gate_report.json").write_text(
        json.dumps(supply_chain_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    cluster_loopback_gate = evaluate_cluster_loopback_gate(root_path)
    (artifacts_dir / "cluster_loopback_gate_report.json").write_text(
        json.dumps(cluster_loopback_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    quickjs_cold_start_gate = evaluate_quickjs_cold_start_gate(root_path)
    (artifacts_dir / "quickjs_cold_start_gate_report.json").write_text(
        json.dumps(quickjs_cold_start_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tcp_cluster_soak_gate = evaluate_tcp_cluster_soak_gate(root_path)
    (artifacts_dir / "tcp_cluster_soak_gate_report.json").write_text(
        json.dumps(tcp_cluster_soak_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    hot_browser_shadow_gate = evaluate_hot_browser_shadow_gate(root_path)
    (artifacts_dir / "hot_browser_shadow_gate_report.json").write_text(
        json.dumps(hot_browser_shadow_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    shadow_sealer_soak_gate = evaluate_shadow_sealer_soak_gate(root_path)
    (artifacts_dir / "shadow_sealer_soak_gate_report.json").write_text(
        json.dumps(shadow_sealer_soak_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    dynamic_provider_fallback_gate = evaluate_dynamic_provider_fallback_gate(root_path)
    (artifacts_dir / "dynamic_provider_fallback_gate_report.json").write_text(
        json.dumps(dynamic_provider_fallback_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    production_packaging_smoke_gate = evaluate_production_packaging_smoke_gate(root_path)
    (artifacts_dir / "production_packaging_smoke_gate_report.json").write_text(
        json.dumps(production_packaging_smoke_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    # Bind a local/external health capture after the e2e-owned artifacts have
    # settled so the health-body hash cannot go stale during this same gate.
    write_external_deployment_smoke_capture(root_path)
    external_deployment_smoke_gate = evaluate_external_deployment_smoke_gate(root_path)
    (artifacts_dir / "external_deployment_smoke_gate_report.json").write_text(
        json.dumps(external_deployment_smoke_gate, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    deployment_manifest = build_deployment_manifest(root_path).to_report()
    (artifacts_dir / "deployment_manifest_report.json").write_text(
        json.dumps(deployment_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    payloads = {
        name: _read_json_artifact(artifacts_dir / name)
        for name in sorted({artifact for checks in STAGE_REQUIREMENTS.values() for artifact, _ in checks})
    }
    stage_checks = tuple(
        _evaluate_stage(stage, payloads, artifacts_dir)
        for stage in STAGES
    )
    passed = sum(1 for check in stage_checks if check.status == "PASS")
    failed = sum(1 for check in stage_checks if check.status == "FAIL")
    not_verified = sum(1 for check in stage_checks if check.status == "NOT VERIFIED")
    production_blockers = _dict_list(deployment_manifest.get("production_blockers", []))
    active_production_blockers = _dict_list(deployment_manifest.get("active_production_blockers", []))
    production_deployable = deployment_manifest.get("production_deployable") is True
    unsigned_report = {
        "suite_name": "AEGIS E2E Release Gate",
        "schema": "aegis-e2e-release-gate-report-v2",
        "truth_claim": False,
        "claim_scope": "LOCAL_CHECKOUT_ONLY",
        "claim_label": "LOCAL E2E GATE; INDEPENDENT VERIFICATION NOT VERIFIED",
        "independent_verification": "NOT VERIFIED",
        "verifier": "rust-replay-and-artifact-gates",
        "digest_algorithm": "sha256-artifact-index",
        "stage_order": list(STAGES),
        "total": len(stage_checks),
        "passed": passed,
        "failed": failed,
        "not_verified": not_verified,
        "verification_complete": not_verified == 0,
        "gate_status": (
            "FAILED" if failed else "INCOMPLETE / NOT VERIFIED" if not_verified else "PASS"
        ),
        "overall_ok": failed == 0 and not_verified == 0,
        "production_deployable": production_deployable,
        "production_blockers": production_blockers,
        "active_production_blockers": active_production_blockers,
        "production_blocker_hash": str(deployment_manifest.get("production_blocker_hash", "")),
        "active_production_blocker_hash": str(deployment_manifest.get("active_production_blocker_hash", "")),
        "release_attestation_hash": str(deployment_manifest.get("release_attestation_hash", "")),
        "external_signed_attestation_present": deployment_manifest.get("external_signed_attestation_present") is True,
        "production_gap_disclosed": production_deployable or len(active_production_blockers) > 0,
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "artifact_refs": list(check.artifact_refs),
                "digest": check.digest,
                "detail": check.detail,
                "status": check.status,
                "not_verified_reason": check.not_verified_reason,
            }
            for check in stage_checks
        ],
    }
    unsigned_report["report_digest"] = _stable_hash(unsigned_report)
    return unsigned_report


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _evaluate_stage(
    stage: str,
    payloads: dict[str, dict[str, Any]],
    artifacts_dir: Path,
) -> E2EStageCheck:
    requirements = STAGE_REQUIREMENTS[stage]
    refs: list[str] = []
    witnesses: list[Any] = []
    missing: list[str] = []
    for artifact_name, selector in requirements:
        payload = payloads.get(artifact_name, {})
        value = _select(payload, selector)
        ref = f"{artifact_name}:{'.'.join(selector)}"
        refs.append(ref)
        if _value_ok(value):
            witnesses.append(value)
        else:
            missing.append(ref)
    artifact_digests = {
        artifact_name: _file_hash(artifacts_dir / artifact_name)
        for artifact_name, _ in requirements
    }
    digest = _stable_hash(
        {
            "stage": stage,
            "refs": refs,
            "witnesses": witnesses,
            "artifact_digests": artifact_digests,
        }
    )
    ok = not missing and all(digest for digest in artifact_digests.values())
    not_verified_reason = _known_unavailable_reason(stage, payloads)
    if ok:
        status = "PASS"
        detail = "pass"
    elif not_verified_reason:
        status = "NOT VERIFIED"
        detail = f"not_verified={not_verified_reason}; missing_or_invalid={missing}"
    else:
        status = "FAIL"
        detail = f"missing_or_invalid={missing}"
    return E2EStageCheck(stage, status == "PASS", tuple(refs), digest, detail, status, not_verified_reason)


def _known_unavailable_reason(stage: str, payloads: dict[str, dict[str, Any]]) -> str:
    """Classify an unavailable environment as unknown, not as a product failure."""
    payload = payloads.get(
        {
            "tcp_cluster_soak": "tcp_cluster_soak_gate_report.json",
            "quickjs_cold_start": "quickjs_cold_start_gate_report.json",
            "dynamic_provider_fallback": "dynamic_provider_fallback_gate_report.json",
            "external_deployment_smoke": "external_deployment_smoke_gate_report.json",
        }.get(stage, ""),
        {},
    )
    if not payload:
        return ""
    if stage == "tcp_cluster_soak" and (
        payload.get("real_multi_node_cluster_test_present") is False
        or payload.get("real_multi_machine_cluster_capture_missing_or_valid") is False
    ):
        return "requires a real non-loopback multi-machine cluster capture"
    if stage == "quickjs_cold_start" and payload.get("real_quickjs_interpreter_cold_start_present") is False:
        return "requires a full QuickJS interpreter cold-start capture"
    if stage == "dynamic_provider_fallback" and payload.get("live_provider_traffic_present") is False:
        return "requires live provider HTTP 429 traffic capture"
    if stage == "external_deployment_smoke" and (
        payload.get("external_deployment_smoke_present") is False
        or payload.get("external_deployment_smoke_capture_missing_or_valid") is False
    ):
        return "requires an externally deployed runtime capture"
    return ""


def _read_json_artifact(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _select(payload: Any, selector: tuple[str, ...]) -> Any:
    node = payload
    for key in selector:
        if isinstance(node, dict):
            node = node.get(key)
            continue
        if isinstance(node, list):
            node = next(
                (
                    item
                    for item in node
                    if isinstance(item, dict) and item.get("name") == key
                ),
                None,
            )
            continue
        return None
    return node


def _value_ok(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        return len(value) >= 16 and any(char != "0" for char in value)
    if isinstance(value, list):
        return len(value) > 0 and any(_value_ok(item) for item in value)
    return value is not None


def _file_hash(path: Path) -> str:
    if not path.exists():
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
    report = evaluate_e2e_release_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
