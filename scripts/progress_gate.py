import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "progress_gate_report.json"


@dataclass(frozen=True)
class Domain:
    name: str
    weight: int
    checks: tuple[tuple[str, str, tuple[str, ...]], ...]


DOMAINS: tuple[Domain, ...] = (
    Domain(
        "goal_plan_context",
        10,
        (
            ("goal_intake_gate", "governance_gate_report.json", ("overall_ok",)),
            ("context_pack_artifact", "agentic_sdk_context_report.json", ("report", "context_pack_digest")),
            ("task_ledger_bench", "benchmark_gate_report.json", ("checks", "task_ledger_ready_queue_10k", "ok")),
        ),
    ),
    Domain(
        "typed_tool_policy_execution",
        12,
        (
            ("policy_gate", "governance_gate_report.json", ("overall_ok",)),
            ("wasmtime_tool_bench", "benchmark_gate_report.json", ("checks", "wasmtime_execute_cached_module", "ok")),
            ("browser_gateway_bench", "benchmark_gate_report.json", ("checks", "browser_tool_gateway_ingest", "ok")),
        ),
    ),
    Domain(
        "replay_checkpoint_shift",
        14,
        (
            ("e2e_replay_stage", "e2e_release_gate_report.json", ("overall_ok",)),
            ("replay_endurance", "run_checks_report.json", ("replay_endurance_report", "overall_ok")),
            ("replay_chaos", "run_checks_report.json", ("replay_chaos_scorecard", "overall_ok")),
        ),
    ),
    Domain(
        "evidence_retrieval_sac",
        10,
        (
            ("agentic_sdk_context", "run_checks_report.json", ("agentic_sdk_context_report", "overall_ok")),
            ("hot_lexical_bench", "benchmark_gate_report.json", ("checks", "hot_lexical_index_top_k", "ok")),
            ("candidate_proof", "agentic_sdk_context_report.json", ("report", "context_pack_candidate_proof_hash")),
        ),
    ),
    Domain(
        "browser_automation",
        8,
        (
            ("browser_ops_report", "run_checks_report.json", ("browser_ops_bench_verification_report", "overall_ok")),
            ("browser_live_bench", "benchmark_gate_report.json", ("checks", "browser_action_plan_live_manifest_gateway_ingest", "ok")),
        ),
    ),
    Domain(
        "hot_engine_cold_ledger",
        10,
        (
            ("hot_browser_shadow_gate", "hot_browser_shadow_gate_report.json", ("overall_ok",)),
            ("hot_artifact_count", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "artifact_count")),
            ("hot_commit_count", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "hot_commit_count")),
            ("cold_seal_receipts", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "cold_seal_receipt_count")),
            ("shadow_replay_recorded", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_seal_replay_recorded")),
            ("shadow_arrow_archive_recovered", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_arrow_archive_recovered")),
            ("shadow_arrow_archive_replay_matches", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_arrow_archive_replay_matches")),
            ("shadow_arrow_archive_contains_shadow_seal", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "shadow_arrow_archive_contains_shadow_seal")),
            ("hot_no_file_roundtrip", "hot_browser_shadow_gate_report.json", ("hot_browser_shadow_evidence", "no_file_roundtrip_on_hot_path")),
            ("shadow_sealer_soak_gate", "shadow_sealer_soak_gate_report.json", ("overall_ok",)),
            ("shadow_sealer_soak_samples", "shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "sample_count")),
            ("shadow_sealer_hot_submit_under_gate", "shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "hot_submit_under_gate")),
            ("shadow_sealer_hot_before_cold", "shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "hot_submissions_completed_before_receipts")),
            ("shadow_sealer_cold_files_materialized", "shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "cold_payload_files_materialized")),
            ("shadow_sealer_cold_hashes_match", "shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "cold_payload_file_hashes_match")),
            ("shadow_sealer_payload_sync_requested", "shadow_sealer_soak_gate_report.json", ("shadow_sealer_soak_evidence", "cold_payload_sync_requested")),
        ),
    ),
    Domain(
        "llm_runtime_budget_replay",
        12,
        (
            ("provider_route_gate", "provider_route_gate_report.json", ("overall_ok",)),
            ("provider_route_checks", "provider_route_gate_report.json", ("passed",)),
            ("dynamic_provider_fallback_gate", "dynamic_provider_fallback_gate_report.json", ("overall_ok",)),
            ("dynamic_provider_latency", "dynamic_provider_fallback_gate_report.json", ("dynamic_provider_fallback_evidence", "latency_under_gate")),
            (
                "dynamic_provider_live_429_admission_schema",
                "dynamic_provider_fallback_gate_report.json",
                ("live_provider_429_soak_admission", "schema"),
            ),
            (
                "dynamic_provider_live_429_admission_hash",
                "dynamic_provider_fallback_gate_report.json",
                ("live_provider_429_soak_admission_hash",),
            ),
            (
                "dynamic_provider_live_429_capture_missing_or_valid",
                "dynamic_provider_fallback_gate_report.json",
                ("live_provider_429_soak_capture_missing_or_valid",),
            ),
            ("dynamic_provider_live_state", "dynamic_provider_fallback_gate_report.json", ("live_provider_429_soak_state_recorded",)),
            ("constitution_runtime", "constitution_audit_report.json", ("overall_ok",)),
        ),
    ),
    Domain(
        "memory_safety_skill",
        8,
        (
            ("pav_memory_bench", "benchmark_gate_report.json", ("checks", "pav_ast_distance_registered", "ok")),
            ("skill_admission_bench", "benchmark_gate_report.json", ("checks", "skill_admission_record_validate", "ok")),
        ),
    ),
    Domain(
        "security_supply_chain",
        8,
        (
            ("dependency_audit", "dependency_audit_gate_report.json", ("overall_ok",)),
            ("supply_chain_gate", "supply_chain_gate_report.json", ("overall_ok",)),
            ("sbom_index", "supply_chain_gate_report.json", ("sbom", "sbom_index_hash")),
            ("provenance_hash", "supply_chain_gate_report.json", ("provenance", "provenance_hash")),
            ("wasi_deny_fuel", "supply_chain_gate_report.json", ("wasi_deny_by_default", "wasmtime_fuel_enabled")),
            ("wasi_deny_memory", "supply_chain_gate_report.json", ("wasi_deny_by_default", "wasmtime_memory_limited")),
            (
                "release_attestation_state",
                "supply_chain_gate_report.json",
                ("release_attestation", "attestation_state_recorded"),
            ),
            (
                "release_attestation_subject_hash",
                "supply_chain_gate_report.json",
                ("release_attestation", "expected_subject_hash"),
            ),
            (
                "release_attestation_evidence_hash",
                "supply_chain_gate_report.json",
                ("release_attestation", "release_attestation_evidence_hash"),
            ),
            ("sandbox_bench", "benchmark_gate_report.json", ("checks", "quickjs_wasmtime_bridge_validate", "ok")),
        ),
    ),
    Domain(
        "operator_product_surface",
        8,
        (
            ("operator_api_contract", "constitution_audit_report.json", ("checks", "python_operator_evidence_api", "ok")),
            ("e2e_gate", "e2e_release_gate_report.json", ("overall_ok",)),
        ),
    ),
    Domain(
        "deployment_topology",
        8,
        (
            ("deployment_manifest", "deployment_manifest_report.json", ("overall_ok",)),
            ("topology_hash", "deployment_manifest_report.json", ("topology_hash",)),
            ("topology_contract_hash", "deployment_manifest_report.json", ("topology_contract_hash",)),
            ("topology_contract_schema", "deployment_manifest_report.json", ("topology", "schema")),
            (
                "topology_contract_materialized",
                "deployment_manifest_report.json",
                ("topology_contract_materialized",),
            ),
            ("release_manifest_hash", "deployment_manifest_report.json", ("release_manifest_hash",)),
            (
                "release_attestation_hash",
                "deployment_manifest_report.json",
                ("release_attestation_hash",),
            ),
            (
                "active_production_blocker_hash",
                "deployment_manifest_report.json",
                ("active_production_blocker_hash",),
            ),
            ("topology_no_policy_weaken", "deployment_manifest_report.json", ("topology_cannot_weaken_policy",)),
            ("remote_workers_candidate_only", "deployment_manifest_report.json", ("remote_workers_candidate_only",)),
            ("service_placements_cover_boundaries", "deployment_manifest_report.json", ("service_placements_cover_boundaries",)),
            ("single_writer_placement_enforced", "deployment_manifest_report.json", ("single_writer_placement_enforced",)),
            ("network_edges_candidate_only", "deployment_manifest_report.json", ("network_edges_candidate_only",)),
            ("durable_storage_defined", "deployment_manifest_report.json", ("durable_storage_defined",)),
            (
                "durable_volumes_materialized",
                "deployment_manifest_report.json",
                ("durable_volumes_materialized",),
            ),
            (
                "topology_replay_ledger_append_only",
                "deployment_manifest_report.json",
                ("topology", "durable_volumes", "replay-ledger", "append_only"),
            ),
            (
                "topology_shadow_seals_append_only",
                "deployment_manifest_report.json",
                ("topology", "durable_volumes", "shadow-seals", "append_only"),
            ),
            ("health_checks_defined", "deployment_manifest_report.json", ("health_checks_defined",)),
            ("operator_rollback_defined", "deployment_manifest_report.json", ("operator_rollback_defined",)),
            ("operator_runbook_defined", "deployment_manifest_report.json", ("operator_runbook_defined",)),
            ("production_blockers_declared", "deployment_manifest_report.json", ("production_blockers_declared",)),
            ("production_packaging_smoke_gate", "production_packaging_smoke_gate_report.json", ("overall_ok",)),
            (
                "production_packaging_smoke_present",
                "production_packaging_smoke_gate_report.json",
                ("production_packaging_smoke_present",),
            ),
            ("production_packaging_smoke_hash", "production_packaging_smoke_gate_report.json", ("smoke_evidence_hash",)),
            ("external_deployment_smoke_gate", "external_deployment_smoke_gate_report.json", ("overall_ok",)),
            (
                "external_deployment_smoke_evidence_hash",
                "external_deployment_smoke_gate_report.json",
                ("external_deployment_smoke_evidence_hash",),
            ),
            (
                "external_deployment_smoke_admission_schema",
                "external_deployment_smoke_gate_report.json",
                ("external_deployment_smoke_admission", "schema"),
            ),
            (
                "external_deployment_smoke_admission_hash",
                "external_deployment_smoke_gate_report.json",
                ("external_deployment_smoke_admission_hash",),
            ),
            (
                "external_deployment_smoke_admission_missing",
                "external_deployment_smoke_gate_report.json",
                ("external_deployment_smoke_admission_missing",),
            ),
            (
                "external_deployment_smoke_capture_missing_or_valid",
                "external_deployment_smoke_gate_report.json",
                ("external_deployment_smoke_capture_missing_or_valid",),
            ),
            (
                "external_deployment_smoke_gap",
                "external_deployment_smoke_gate_report.json",
                ("external_deployment_smoke_state_recorded",),
            ),
            (
                "container_attestation_gap",
                "external_deployment_smoke_gate_report.json",
                ("container_attestation_state_recorded",),
            ),
            (
                "deployment_packaging_smoke_present",
                "deployment_manifest_report.json",
                ("production_packaging_smoke_present",),
            ),
            (
                "deployment_external_smoke_hash",
                "deployment_manifest_report.json",
                ("external_deployment_smoke_hash",),
            ),
        ),
    ),
    Domain(
        "distributed_loopback_cluster",
        8,
        (
            ("cluster_loopback_gate", "cluster_loopback_gate_report.json", ("overall_ok",)),
            ("cluster_worker_count", "cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "worker_count")),
            ("cluster_work_items", "cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "work_item_count")),
            ("cluster_rtt", "cluster_loopback_gate_report.json", ("loopback_cluster_evidence", "max_logical_rtt_ticks")),
            ("multi_node_gap", "cluster_loopback_gate_report.json", ("production_cluster_release_blocked_without_real_multi_node_soak",)),
        ),
    ),
    Domain(
        "tcp_cluster_soak",
        8,
        (
            ("tcp_cluster_soak_gate", "tcp_cluster_soak_gate_report.json", ("overall_ok",)),
            ("tcp_worker_count", "tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "worker_count")),
            ("tcp_work_items", "tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "work_item_count")),
            ("tcp_worker_connections", "tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "tcp_worker_connections")),
            ("tcp_round_trips", "tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "tcp_round_trip_count")),
            ("tcp_payload_sent", "tcp_cluster_soak_gate_report.json", ("tcp_cluster_soak_evidence", "tcp_payload_bytes_sent")),
            ("real_multi_machine_admission_schema", "tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_admission", "schema")),
            ("real_multi_machine_admission_hash", "tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_admission_hash",)),
            (
                "real_multi_machine_capture_missing_or_valid",
                "tcp_cluster_soak_gate_report.json",
                ("real_multi_machine_cluster_capture_missing_or_valid",),
            ),
            ("real_cluster_state", "tcp_cluster_soak_gate_report.json", ("real_multi_machine_cluster_state_recorded",)),
        ),
    ),
    Domain(
        "quickjs_cold_start_bridge",
        8,
        (
            ("quickjs_cold_start_gate", "quickjs_cold_start_gate_report.json", ("overall_ok",)),
            ("quickjs_sample_count", "quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "sample_count")),
            ("quickjs_cold_total", "quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "cold_start_total_ns")),
            ("quickjs_warm_total", "quickjs_cold_start_gate_report.json", ("quickjs_cold_start_evidence", "warm_cached_total_ns")),
            ("quickjs_full_interpreter_admission_schema", "quickjs_cold_start_gate_report.json", ("full_interpreter_admission", "schema")),
            ("quickjs_full_interpreter_admission_hash", "quickjs_cold_start_gate_report.json", ("full_interpreter_admission_hash",)),
            (
                "quickjs_full_interpreter_semantic_capture_missing_or_valid",
                "quickjs_cold_start_gate_report.json",
                ("full_interpreter_semantic_capture_missing_or_valid",),
            ),
            ("quickjs_full_interpreter_state", "quickjs_cold_start_gate_report.json", ("full_interpreter_state_recorded",)),
        ),
    ),
    Domain(
        "competitor_gauntlet",
        10,
        (
            ("sota_gate", "run_checks_report.json", ("sota_baseline_gate", "overall_ok")),
            ("hermes_search", "run_checks_report.json", ("hermes_baseline_gate", "overall_ok")),
            ("hermes_rpc", "run_checks_report.json", ("hermes_rpc_baseline_gate", "overall_ok")),
            ("hermes_recovery", "run_checks_report.json", ("hermes_session_recovery_baseline_gate", "overall_ok")),
            ("hermes_persistence", "run_checks_report.json", ("hermes_persistence_baseline_gate", "overall_ok")),
        ),
    ),
)


def evaluate_progress_gate(
    root: str | Path = ROOT,
    run_checks_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    artifacts_dir = root_path / "artifacts"
    payloads = {
        artifact: _read_json(artifacts_dir / artifact)
        for domain in DOMAINS
        for _, artifact, _ in domain.checks
    }
    if run_checks_payload is not None:
        payloads["run_checks_report.json"] = run_checks_payload
    domain_reports = []
    total_weight = sum(domain.weight for domain in DOMAINS)
    weighted_score = 0
    for domain in DOMAINS:
        checks = []
        for name, artifact, path in domain.checks:
            value = _get_path(payloads.get(artifact, {}), path)
            ok = _truthy_evidence(value)
            checks.append(
                {
                    "name": name,
                    "artifact": artifact,
                    "path": list(path),
                    "ok": ok,
                    "observed": _stable_observed(value),
                }
            )
        passed = sum(1 for check in checks if check["ok"])
        readiness_ppm = 0 if not checks else (passed * 1_000_000) // len(checks)
        weighted_score += domain.weight * readiness_ppm
        domain_reports.append(
            {
                "name": domain.name,
                "weight": domain.weight,
                "passed": passed,
                "total": len(checks),
                "readiness_ppm": readiness_ppm,
                "checks": checks,
            }
        )
    overall_readiness_ppm = 0 if total_weight == 0 else weighted_score // total_weight
    payload = {
        "suite_name": "AEGIS Progress Gate",
        "schema": "aegis-progress-gate-report-v1",
        "truth_claim": False,
        "verifier": "artifact-weighted-readiness-index",
        "overall_ok": True,
        "overall_readiness_ppm": overall_readiness_ppm,
        "overall_readiness_percent": round(overall_readiness_ppm / 10_000, 2),
        "domain_count": len(DOMAINS),
        "domains": domain_reports,
    }
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _truthy_evidence(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, int):
        return value > 0
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        return bool(value) and value not in {"0", "false", "False"}
    return False


def _stable_observed(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value if len(value) <= 16 else value[:16]
    if isinstance(value, list):
        return f"list:{len(value)}"
    if value is None:
        return "missing"
    return type(value).__name__


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for part in path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            current = next(
                (item for item in current if isinstance(item, dict) and item.get("name") == part),
                None,
            )
        else:
            return None
    return current


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report = evaluate_progress_gate(ROOT)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
