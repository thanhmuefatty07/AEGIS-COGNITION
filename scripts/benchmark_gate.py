import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(r"c:\Users\ADMIN\AEGIS-COGNITION")
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "benchmark_gate_report.json"
BENCHMARK_SUITE_NAME = "Micro-Path Benchmarks (Core Primitives)"
BENCHMARK_PROFILE_RELEASE = "release"
BENCHMARK_PROFILE_INNOVATION = "innovation"
BENCHMARK_PROFILE_MULTIPLIERS = {
    BENCHMARK_PROFILE_RELEASE: 1.0,
    BENCHMARK_PROFILE_INNOVATION: 3.0,
}
ADVISORY_BENCHMARK_SURFACES = (
    "provider_route_admission_proof",
)
BENCHMARK_SCOPE_WARNING = (
    "These estimates cover isolated CPU-bound primitives, bounded TaskLedger ready "
    "queue cache-hit and recompute paths, bounded context-governor activation, QuickJS ABI packet "
    "preparation, linear-memory write, normal Wasmtime cached-module execution, "
    "and Wasmtime host bridge validation, "
    "hash-bound goal intake risk classification and initial TypedToolIR proof construction, "
    "PAV registered AST edit-distance measurement, "
    "bounded LLM loop circuit-breaker observation, "
    "hash-bound context fold record construction, "
    "budgeted generational-slab handle lookup and O(1) layout accounting, "
    "mmap-backed Rust/Python payload view access, mmap-verified Wasmtime bridge execution, "
    "Wasmtime/PAV-gated skill admission record validation, registry commit binding, "
    "and replay-sealed skill-admission handoff proof validation, "
    "bounded replay mmap materialized readback, fixed binary replay readback, "
    "segmented Arrow audit append/flush, proof scan, mmap column proof scan, physical manifest recovery, "
    "two-pass determinism proof, "
    "shift-manager next-action binding, and crash-safe compaction, "
    "hash-bound browser witness proof validation, collector artifact-envelope "
    "minting into browser observation packets, file-backed collector artifact "
    "envelope hashing into browser observation packets, collector-bound browser "
    "observation packet validation, Browser-Use-style browser action plan binding "
    "into observation packets, Browser-Use-style zero-LLM page-search candidate "
    "record minting, and file-backed gateway ingestion, "
    "browser packet gateway ingestion into replay-bound tool evidence, "
    "deterministic Datalog-style policy closure proof binding, "
    "non-materializing binary replay verification/recovery, "
    "accelerated 100h replay-endurance "
    "report self-verification, schema-only hot lexical "
    "candidate retrieval, artifact-bound Aho-Corasick hot evidence retrieval, "
    "artifact-bound AST-signature hot evidence lookup, "
    "RAM-direct Hot Engine SIMD BLAKE3 hashing and in-memory arena commit/release, "
    "bounded Search-as-Code evidence primitive orchestration, "
    "SaC SDK-run replay binding, append, and sealed handoff proof construction, "
    "deterministic artifact-bound cold-vector retrieval, "
    "deterministic cluster work-envelope hashing, worker lease acquisition, "
    "and single-writer distributed candidate admission, "
    "index-epoch replay record binding, cold-vector expansion "
    "replay record binding, sorted-Vec hot bitmap filter intersections, and sorted-Vec "
    "hot term prefix expansion only; they do not "
    "measure end-to-end platform latency including mutable Arrow writer "
    "semantics, full QuickJS interpreter execution/cold-start, browser evidence, or "
    "LLM network RTT; zero-copy Arrow observability is proven only for the "
    "bounded segmented mmap column proof scan."
)


THRESHOLDS_NS = {
    "descriptor_validation": 5.0,
    "goal_intake_classify_packet": 25_000.0,
    "layout_validation": 2.0,
    "message_to_zero_copy": 100.0,
    "zero_copy_roundtrip": 250.0,
    "zero_copy_validation": 150.0,
    "runtime_ingest": 1_000.0,
    "runtime_ingest_batch": 20_000.0,
    "runtime_latest_summary": 1_000.0,
    "runtime_is_ready": 1_000.0,
    "generational_slab_get_hot": 25.0,
    "generational_slab_len": 10.0,
    "llm_route": 100.0,
    "llm_runtime_checkpoint": 3_000.0,
    "inference_route_contract_proof": 25_000.0,
    "prefix_cache_broker_hit_proof": 25_000.0,
    "structured_output_proof": 50_000.0,
    "continuous_batch_fairness_proof": 25_000.0,
    "speculative_decode_equivalence_proof": 25_000.0,
    "llm_loop_circuit_breaker_observe": 2_000.0,
    "quickjs_invocation_abi_prepare": 10_000.0,
    "quickjs_linear_memory_write": 10_000.0,
    "wasmtime_execute_cached_module": 25_000.0,
    "skill_admission_record_validate": 10_000.0,
    "skill_registry_commit_admitted_skill": 15_000.0,
    "skill_admission_replay_binding_hash": 5_000.0,
    "skill_admission_replay_append": 20_000.0,
    "skill_admission_handoff_proof_hash": 5_000.0,
    "skill_admission_handoff_validate": 15_000.0,
    "skill_registry_activate_replay_sealed_skill": 20_000.0,
    "skill_active_wasm_execution_proof": 50_000.0,
    "skill_execution_handoff_seal": 25_000_000.0,
    "quickjs_wasmtime_bridge_validate": 400_000.0,
    "pav_ast_distance_registered": 100.0,
    "mmap_bridge_payload_view": 25.0,
    "mmap_wasm_bridge_execute": 250_000.0,
    "replay_io_mmap_materialized": 12_000_000.0,
    "replay_segmented_arrow_append": 35_000_000.0,
    "replay_segmented_arrow_proof": 20_000_000.0,
    "replay_segmented_arrow_column_scan_proof": 20_000_000.0,
    "replay_segmented_arrow_manifest_recover": 20_000_000.0,
    "replay_determinism_proof": 25_000_000.0,
    "replay_segmented_arrow_compact": 100_000_000.0,
    "replay_shift_manager_next_action": 10_000.0,
    "browser_witness_proof_validate": 5_000.0,
    "browser_witness_packet_validate": 15_000.0,
    "browser_collector_envelope_mint_packet": 20_000.0,
    "browser_file_artifact_envelope_mint_packet": 1_000_000.0,
    "browser_live_collector_run_write_manifest": 100_000_000.0,
    "browser_tool_gateway_ingest": 25_000.0,
    "browser_file_artifact_gateway_ingest": 1_500_000.0,
    "browser_live_collector_manifest_gateway_ingest": 1_500_000.0,
    "browser_action_plan_live_manifest_gateway_ingest": 1_500_000.0,
    "browser_action_plan_file_artifact_gateway_ingest": 1_500_000.0,
    "browser_action_plan_bind_packet": 25_000.0,
    "browser_page_search_candidate_record": 25_000.0,
    "policy_datalog_closure_proof": 10_000.0,
    "replay_io_binary_mmap_fixed": 5_000_000.0,
    "replay_io_binary_mmap_verify": 2_000_000.0,
    "replay_io_binary_mmap_recover_prefix": 5_000_000.0,
    "replay_endurance_report_verify_100h": 5_000.0,
    "context_governor_activated_pack": 100_000.0,
    "context_fold_record": 150_000.0,
    "task_ledger_ready_queue_10k": 500.0,
    "task_ledger_ready_queue_10k_recompute": 250_000.0,
    "hot_lexical_index_top_k": 200_000.0,
    "hot_evidence_index_exact_aho": 3_000_000.0,
    "hot_evidence_index_ast_signature_lookup": 50_000.0,
    "hot_engine_simd_blake3_hash": 10_000.0,
    "hot_engine_arena_commit": 25_000.0,
    "agentic_evidence_program_execute": 3_500_000.0,
    "agentic_evidence_ast_signature_program_execute": 750_000.0,
    "agentic_evidence_sdk_run": 3_750_000.0,
    "agentic_evidence_sdk_run_replay_binding_hash": 10_000.0,
    "agentic_evidence_sdk_run_replay_append": 25_000.0,
    "agentic_evidence_sdk_run_handoff_seal": 25_000_000.0,
    "agentic_evidence_sdk_run_handoff_context_pack": 250_000.0,
    "candidate_state_capsule_roundtrip": 250_000.0,
    "index_epoch_replay_record": 10_000.0,
    "cold_vector_expansion_replay_record": 15_000.0,
    "cold_vector_index_query": 2_500_000.0,
    "hot_bitmap_filter_intersection": 150_000.0,
    "hot_term_dictionary_prefix_expand": 25_000.0,
    "cluster_work_envelope": 25_000.0,
    "work_lease_acquire": 15_000.0,
    "single_writer_cluster_commit": 75_000.0,
}


@dataclass(frozen=True)
class BenchmarkGateCheck:
    name: str
    ok: bool
    estimate_ns: float | None
    threshold_ns: float
    detail: str


@dataclass(frozen=True)
class BenchmarkGateReport:
    suite_name: str
    scope_warning: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[BenchmarkGateCheck]


def _estimate_path(root: Path, name: str) -> Path:
    return root / "target" / "criterion" / name / "new" / "estimates.json"


def _read_estimate_ns(path: Path) -> float:
    data = json.loads(path.read_text(encoding="utf-8"))
    estimate = data.get("slope") or data.get("mean") or data.get("median")
    if not estimate or "point_estimate" not in estimate:
        raise ValueError("missing point_estimate")
    return float(estimate["point_estimate"])


def thresholds_for_profile(profile: str) -> dict[str, float]:
    if profile not in BENCHMARK_PROFILE_MULTIPLIERS:
        raise ValueError(f"unknown benchmark profile: {profile}")
    multiplier = BENCHMARK_PROFILE_MULTIPLIERS[profile]
    return {name: threshold * multiplier for name, threshold in THRESHOLDS_NS.items()}


def evaluate_benchmarks(
    root: str | Path,
    thresholds: dict[str, float] | None = None,
    profile: str = BENCHMARK_PROFILE_RELEASE,
) -> BenchmarkGateReport:
    root_path = Path(root)
    active_thresholds = thresholds or thresholds_for_profile(profile)
    checks: list[BenchmarkGateCheck] = []

    for name, threshold_ns in active_thresholds.items():
        path = _estimate_path(root_path, name)
        if not path.exists():
            checks.append(BenchmarkGateCheck(name, False, None, threshold_ns, f"missing {path}"))
            continue
        try:
            estimate_ns = _read_estimate_ns(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks.append(BenchmarkGateCheck(name, False, None, threshold_ns, str(exc)))
            continue
        checks.append(
            BenchmarkGateCheck(
                name=name,
                ok=estimate_ns <= threshold_ns,
                estimate_ns=estimate_ns,
                threshold_ns=threshold_ns,
                detail=f"{estimate_ns:.3f} ns <= {threshold_ns:.3f} ns",
            )
        )

    passed = sum(1 for check in checks if check.ok)
    failed = len(checks) - passed
    return BenchmarkGateReport(
        BENCHMARK_SUITE_NAME,
        BENCHMARK_SCOPE_WARNING,
        failed == 0,
        passed,
        failed,
        checks,
    )


def report_to_dict(report: BenchmarkGateReport) -> dict:
    return {
        "suite_name": report.suite_name,
        "scope_warning": report.scope_warning,
        "overall_ok": report.overall_ok,
        "passed": report.passed,
        "failed": report.failed,
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "estimate_ns": check.estimate_ns,
                "threshold_ns": check.threshold_ns,
                "detail": check.detail,
            }
            for check in report.checks
        ],
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=sorted(BENCHMARK_PROFILE_MULTIPLIERS),
        default=BENCHMARK_PROFILE_RELEASE,
    )
    args = parser.parse_args()
    report = evaluate_benchmarks(ROOT, profile=args.profile)
    payload = report_to_dict(report)
    payload["profile"] = args.profile
    payload["threshold_multiplier"] = BENCHMARK_PROFILE_MULTIPLIERS[args.profile]
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
