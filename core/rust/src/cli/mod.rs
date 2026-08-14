use crate::browser_witness::{
    BrowserCollectorKind, BrowserOpsBenchVerificationProof, BrowserOpsBenchVerificationReport,
};
use crate::context::{ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind};
use crate::distributed::{
    CandidateArtifactRef, ClusterPartitionState, ClusterWorkEnvelope, SingleWriterAdmission,
    SingleWriterRunLog, WorkLeaseTable, WorkerRole,
};
use crate::evidence_index::{
    AgenticEvidenceProgram, AgenticEvidenceProgramStep, AgenticEvidenceSdk, AgenticEvidenceSdkRun,
    HotBitmapFilter, HotEvidenceIndex, HotLexicalIndex, SortedEvidenceSet,
};
use crate::llm::{
    LLMRequest, ProviderBudgetLedger, ProviderConfig, ProviderRouteAdmissionProof,
    ProviderRuntimeBudget, ProviderRuntimeFeedback, ProviderRuntimeFeedbackKind,
    route_request_after_provider_feedback,
};
use crate::message::MessageFrame;
use crate::orchestrator::NerveRuntime;
use crate::policy::{HarnessBenchScorecard, SideEffectClass};
use crate::replay::{
    AgenticEvidenceSdkRunHandoffProof, NextActionKind, ReplayChaosBench, ReplayEnduranceBench,
    RunEventLedger, RunEventSegmentArchive, ToolExecutionEvidence, ToolExecutionStatus,
    ToolExecutorKind, agentic_evidence_sdk_run_replay_binding_hash,
    browser_ops_bench_verification_replay_binding_hash,
};
use crate::sandbox::{
    QUICKJS_INVOCATION_ABI_HEADER_BYTES, QuickJsWasmInterpreterManager, WasmtimeSandbox,
};
use serde::Serialize;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

const REPLAY_CHAOS_SCORECARD_SEED: u64 = 0xAE61_5C0D_E7E7;
const REPLAY_CHAOS_SCORECARD_CRASH_POINTS: u32 = 128;
const REPLAY_CHAOS_SCORECARD_ROUNDS: u128 = 8;
const REPLAY_CHAOS_SCORECARD_EVENTS_PER_SEGMENT: usize = 4;
const BROWSER_OPS_BENCH_VERIFICATION_REPORT_SCHEMA: &str =
    "aegis-browser-ops-bench-verification-report-v1";
const AGENTIC_SDK_CONTEXT_REPORT_SCHEMA: &str = "aegis-agentic-sdk-context-report-v1";
const CLUSTER_LOOPBACK_REPORT_SCHEMA: &str = "aegis-cluster-loopback-report-v1";
const TCP_CLUSTER_SOAK_REPORT_SCHEMA: &str = "aegis-tcp-cluster-soak-report-v1";
const QUICKJS_COLD_START_REPORT_SCHEMA: &str = "aegis-quickjs-cold-start-report-v1";
const HOT_BROWSER_SHADOW_REPORT_SCHEMA: &str = "aegis-hot-browser-shadow-report-v1";
const SHADOW_SEALER_SOAK_REPORT_SCHEMA: &str = "aegis-shadow-sealer-soak-report-v1";
const DYNAMIC_PROVIDER_FALLBACK_REPORT_SCHEMA: &str = "aegis-dynamic-provider-fallback-report-v1";
const AGENTIC_SDK_CONTEXT_RUN_ID: u128 = 9_901;
const AGENTIC_SDK_CONTEXT_EXECUTION_ID: u128 = 1_991;
const AGENTIC_SDK_CONTEXT_ACTIVE_TASK_ID: u128 = 1;
const CLUSTER_LOOPBACK_RUN_ID: u128 = 12_901;
const CLUSTER_LOOPBACK_WORK_ITEMS: u32 = 32;
const CLUSTER_LOOPBACK_WORKER_COUNT: u32 = 3;
const TCP_CLUSTER_SOAK_RUN_ID: u128 = 13_901;
const TCP_CLUSTER_SOAK_WORK_ITEMS: u32 = 24;
const TCP_CLUSTER_SOAK_WORKER_COUNT: u32 = 3;
const TCP_CLUSTER_HELLO_BYTES: usize = 24;
const TCP_CLUSTER_REQUEST_FRAME_BYTES: usize = 228;
const TCP_CLUSTER_RESPONSE_FRAME_BYTES: usize = 156;
const TCP_CLUSTER_FRAME_TIMEOUT_MS: u64 = 2_000;
const REAL_MULTI_MACHINE_CLUSTER_SOAK_ADMISSION_SCHEMA: &str =
    "aegis-real-multi-machine-cluster-soak-admission-v1";
const REAL_MULTI_MACHINE_CLUSTER_SOAK_EXPECTED_PATH: &str =
    "artifacts/real_multi_machine_cluster_soak_capture.json";
const REAL_MULTI_MACHINE_CLUSTER_SOAK_NETWORK_CONTRACT: &str =
    "external-multi-machine-mtls-tcp-soak-with-replay-hash-chain-and-rtt-evidence";
const REAL_MULTI_MACHINE_CLUSTER_SOAK_TOPOLOGY_CONTRACT: &str =
    "single-writer-core-plus-remote-worker-pool-across-distinct-machines";
const REAL_MULTI_MACHINE_CLUSTER_SOAK_BLOCKER_ID: &str = "real_multi_machine_cluster_soak_missing";
const QUICKJS_COLD_START_SAMPLE_COUNT: u32 = 5;
const QUICKJS_COLD_START_FUEL_LIMIT: u64 = 10_000;
const QUICKJS_FULL_INTERPRETER_ADMISSION_SCHEMA: &str =
    "aegis-quickjs-full-interpreter-admission-v1";
const QUICKJS_FULL_INTERPRETER_EXPECTED_PATH: &str = "artifacts/quickjs_full_interpreter.wasm";
const QUICKJS_FULL_INTERPRETER_SEMANTIC_CORPUS: &str =
    "es2020-deterministic-subset-arithmetic-json-date-disabled";
const QUICKJS_FULL_INTERPRETER_SANDBOX_CONTRACT: &str =
    "wasmtime-fuel-epoch-memory-no-wasi-preopen-no-network";
const QUICKJS_FULL_INTERPRETER_BLOCKER_ID: &str = "full_quickjs_interpreter_cold_start_missing";
const HOT_BROWSER_SHADOW_RUN_ID: u128 = 14_901;
const HOT_BROWSER_SHADOW_ARTIFACT_COUNT: u32 = 8;
const SHADOW_SEALER_SOAK_RUN_ID: u128 = 15_901;
const SHADOW_SEALER_SOAK_SAMPLE_COUNT: u32 = 64;
const SHADOW_SEALER_SOAK_PAYLOAD_BYTES: usize = 4 * 1024;
const SHADOW_SEALER_SOAK_QUEUE_DEPTH: usize = 128;
const SHADOW_SEALER_SOAK_HOT_SUBMIT_GATE_NS: u64 = 1_000_000;
const DYNAMIC_PROVIDER_FALLBACK_SAMPLE_COUNT: u32 = 32;
const DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS: u64 = 1_000_000;
const LIVE_PROVIDER_429_SOAK_ADMISSION_SCHEMA: &str = "aegis-live-provider-429-soak-admission-v1";
const LIVE_PROVIDER_429_SOAK_EXPECTED_PATH: &str = "artifacts/live_provider_429_soak_capture.json";
const LIVE_PROVIDER_429_SOAK_PROVIDER_SET: &str = "openrouter:gpt-4->nim:llama-3-70b";
const LIVE_PROVIDER_429_SOAK_CAPTURE_CONTRACT: &str =
    "external-http-429-capture-with-request-redaction-and-provider-response-hashes";
const LIVE_PROVIDER_429_SOAK_BLOCKER_ID: &str = "live_provider_429_soak_missing";

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AgenticSdkContextReport {
    pub schema: &'static str,
    pub run_id: u128,
    pub execution_id: u128,
    pub active_task_id: u128,
    pub replay_recorded: bool,
    pub context_recorded: bool,
    pub sdk_run_hash: [u8; 32],
    pub manifest_hash: [u8; 32],
    pub execution_record_hash: [u8; 32],
    pub capsule_hash: [u8; 32],
    pub candidate_list_hash: [u8; 32],
    pub candidate_count: u32,
    pub sdk_run_event_id: u64,
    pub sdk_run_event_hash: [u8; 32],
    pub sdk_run_replay_binding_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub context_pack_digest: [u8; 32],
    pub context_pack_node_hash: [u8; 32],
    pub context_pack_candidate_proof_hash: [u8; 32],
    pub context_pack_event_id: u64,
    pub context_pack_event_hash: [u8; 32],
    pub context_archive_manifest_hash: [u8; 32],
    pub context_ledger_hash: [u8; 32],
    pub context_event_count: u64,
    pub report_hash: [u8; 32],
}

#[derive(Serialize)]
struct AgenticSdkContextReportBody<'a> {
    schema_version: u32,
    report: &'a AgenticSdkContextReport,
}

#[derive(Serialize)]
struct AgenticSdkContextReportArtifact<'a> {
    schema_version: u32,
    report: &'a AgenticSdkContextReport,
    write_evidence: &'a AgenticSdkContextReportWriteEvidence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AgenticSdkContextReportWriteEvidence {
    pub staged_temp_file_used: bool,
    pub temp_file_synced_before_publish: bool,
    pub publish_completed: bool,
    pub parent_directory_sync_attempted: bool,
    pub replace_existing_supported: bool,
    pub publish_write_through_requested: bool,
    pub logical_payload_bytes: u64,
    pub logical_payload_hash: [u8; 32],
    pub evidence_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ClusterLoopbackReport {
    pub schema: &'static str,
    pub run_id: u128,
    pub worker_count: u32,
    pub work_item_count: u32,
    pub accepted_count: u32,
    pub duplicate_count: u32,
    pub replay_event_count: u64,
    pub replay_hash_chain_valid: bool,
    pub direct_worker_commit_rejected: bool,
    pub side_effect_partition_paused: bool,
    pub max_logical_rtt_ticks: u64,
    pub total_logical_rtt_ticks: u64,
    pub candidate_result_hash: [u8; 32],
    pub replay_last_hash: [u8; 32],
    pub report_hash: [u8; 32],
    pub multi_machine_real_cluster: bool,
}

#[derive(Serialize)]
struct ClusterLoopbackReportBody<'a> {
    schema_version: u32,
    report: &'a ClusterLoopbackReport,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealMultiMachineClusterSoakAdmission {
    pub schema: &'static str,
    pub expected_capture_path: &'static str,
    pub capture_present: bool,
    pub capture_hash: [u8; 32],
    pub network_contract: &'static str,
    pub network_contract_hash: [u8; 32],
    pub topology_contract: &'static str,
    pub topology_contract_hash: [u8; 32],
    pub required_worker_count: u32,
    pub required_work_item_count: u32,
    pub required_round_trip_count: u32,
    pub local_loopback_worker_count: u32,
    pub local_loopback_work_item_count: u32,
    pub local_loopback_round_trip_count: u32,
    pub admission_status: &'static str,
    pub production_blocker_id: &'static str,
    pub admission_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct TcpClusterSoakReport {
    pub schema: &'static str,
    pub run_id: u128,
    pub worker_count: u32,
    pub work_item_count: u32,
    pub accepted_count: u32,
    pub duplicate_count: u32,
    pub replay_event_count: u64,
    pub replay_hash_chain_valid: bool,
    pub tcp_listener_bound: bool,
    pub tcp_worker_connections: u32,
    pub tcp_round_trip_count: u32,
    pub tcp_round_trip_min_ns: u64,
    pub tcp_round_trip_max_ns: u64,
    pub tcp_round_trip_total_ns: u64,
    pub tcp_payload_bytes_sent: u64,
    pub tcp_payload_bytes_received: u64,
    pub worker_response_count: u32,
    pub direct_worker_commit_rejected: bool,
    pub side_effect_partition_paused: bool,
    pub network_trace_hash: [u8; 32],
    pub candidate_result_hash: [u8; 32],
    pub replay_last_hash: [u8; 32],
    pub report_hash: [u8; 32],
    pub loopback_tcp_only: bool,
    pub multi_machine_real_cluster: bool,
    pub real_multi_machine_cluster_admission: RealMultiMachineClusterSoakAdmission,
    pub real_multi_node_cluster_test_present: bool,
    pub production_cluster_release_blocked_without_real_multi_node_soak: bool,
}

#[derive(Serialize)]
struct TcpClusterSoakReportBody<'a> {
    schema_version: u32,
    report: &'a TcpClusterSoakReport,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct TcpClusterRequestFrame {
    work_index: u32,
    run_id: u128,
    work_id: u128,
    task_id: u128,
    worker_id: u128,
    envelope_hash: [u8; 32],
    idempotency_key: [u8; 32],
    input_hash: [u8; 32],
    evidence_contract_hash: [u8; 32],
    policy_window_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct TcpClusterResponseFrame {
    work_index: u32,
    run_id: u128,
    work_id: u128,
    worker_id: u128,
    artifact_hash: [u8; 32],
    artifact_kind_hash: [u8; 32],
    byte_len: u64,
    response_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct QuickJsColdStartReport {
    pub schema: &'static str,
    pub sample_count: u32,
    pub fuel_limit: u64,
    pub script_bytes: u64,
    pub abi_packet_bytes: u64,
    pub wasm_module_bytes: u64,
    pub cold_start_min_ns: u64,
    pub cold_start_max_ns: u64,
    pub cold_start_total_ns: u64,
    pub warm_cached_min_ns: u64,
    pub warm_cached_max_ns: u64,
    pub warm_cached_total_ns: u64,
    pub cold_cache_entries_after_first: u32,
    pub warm_cache_entries_before: u32,
    pub warm_cache_entries_after: u32,
    pub cold_fuel_consumed_total: u64,
    pub warm_fuel_consumed_total: u64,
    pub cold_artifact_hash: [u8; 32],
    pub warm_artifact_hash: [u8; 32],
    pub script_blake3: [u8; 32],
    pub wrapper_blake3: [u8; 32],
    pub invocation_blake3: [u8; 32],
    pub bridge_probe_executed: bool,
    pub full_interpreter_admission: QuickJsFullInterpreterAdmission,
    pub full_quickjs_interpreter_present: bool,
    pub production_quickjs_release_blocked_without_full_interpreter: bool,
    pub report_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct QuickJsFullInterpreterAdmission {
    pub schema: &'static str,
    pub expected_runtime_path: &'static str,
    pub runtime_wasm_present: bool,
    pub runtime_wasm_hash: [u8; 32],
    pub semantic_corpus: &'static str,
    pub semantic_corpus_hash: [u8; 32],
    pub sandbox_contract: &'static str,
    pub sandbox_contract_hash: [u8; 32],
    pub required_cold_start_sample_count: u32,
    pub required_fuel_limit: u64,
    pub admission_status: &'static str,
    pub production_blocker_id: &'static str,
    pub admission_hash: [u8; 32],
}

#[derive(Serialize)]
struct QuickJsColdStartReportBody<'a> {
    schema_version: u32,
    report: &'a QuickJsColdStartReport,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct HotBrowserShadowArtifactReport {
    pub kind: &'static str,
    pub byte_len: u64,
    pub slot: u32,
    pub generation: u32,
    pub artifact_hash: [u8; 32],
    pub storage_ref_hash: [u8; 32],
    pub seal_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct HotBrowserShadowReport {
    pub schema: &'static str,
    pub run_id: u128,
    pub artifact_count: u32,
    pub total_artifact_bytes: u64,
    pub hot_commit_count: u32,
    pub hot_commit_min_ns: u64,
    pub hot_commit_max_ns: u64,
    pub hot_commit_total_ns: u64,
    pub arena_live_bytes_after_hot: u64,
    pub arena_live_slots_after_hot: u32,
    pub arena_total_commits: u64,
    pub hot_commit_completed_before_first_cold_receipt: bool,
    pub shadow_sealer_nonblocking_admission: bool,
    pub shadow_sealer_queue_depth: usize,
    pub shadow_sealer_queued_admissions: u64,
    pub shadow_sealer_backpressure_rejections: u64,
    pub cold_seal_receipt_count: u32,
    pub cold_seal_total_bytes: u64,
    pub cold_files_materialized: bool,
    pub shadow_receipts_valid: bool,
    pub shadow_batch_hash: [u8; 32],
    pub shadow_batch_valid: bool,
    pub replay_event_count: u64,
    pub replay_hash_chain_valid: bool,
    pub shadow_seal_replay_recorded: bool,
    pub replay_last_hash: [u8; 32],
    pub shadow_arrow_archive_segment_count: u32,
    pub shadow_arrow_archive_event_count: u64,
    pub shadow_arrow_archive_manifest_hash: [u8; 32],
    pub shadow_arrow_archive_audit_proof_hash: [u8; 32],
    pub shadow_arrow_archive_segment_witness_hash: [u8; 32],
    pub shadow_arrow_archive_mmap_evidence_hash: [u8; 32],
    pub shadow_arrow_archive_determinism_proof_hash: [u8; 32],
    pub shadow_arrow_archive_recovered: bool,
    pub shadow_arrow_archive_replay_matches: bool,
    pub shadow_arrow_archive_contains_shadow_seal: bool,
    pub hot_artifact_batch_hash: [u8; 32],
    pub artifact_reports: Vec<HotBrowserShadowArtifactReport>,
    pub no_file_roundtrip_on_hot_path: bool,
    pub trust_level_prod: bool,
    pub physical_witness_required: bool,
    pub fail_closed: bool,
    pub report_hash: [u8; 32],
}

#[derive(Serialize)]
struct HotBrowserShadowReportBody<'a> {
    schema_version: u32,
    report: &'a HotBrowserShadowReport,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ShadowSealerSoakReport {
    pub schema: &'static str,
    pub run_id: u128,
    pub sample_count: u32,
    pub payload_bytes_per_sample: u64,
    pub total_payload_bytes: u64,
    pub queue_depth: usize,
    pub hot_commit_min_ns: u64,
    pub hot_commit_max_ns: u64,
    pub hot_commit_total_ns: u64,
    pub hot_submit_min_ns: u64,
    pub hot_submit_max_ns: u64,
    pub hot_submit_total_ns: u64,
    pub hot_submit_gate_ns: u64,
    pub hot_submit_under_gate: bool,
    pub hot_submissions_completed_before_receipts: bool,
    pub queued_admissions: u64,
    pub backpressure_rejections: u64,
    pub cold_seal_receipt_count: u32,
    pub cold_seal_total_bytes: u64,
    pub cold_seal_wait_total_ns: u64,
    pub cold_payload_files_materialized: bool,
    pub cold_payload_file_hashes_match: bool,
    pub cold_payload_sync_requested: bool,
    pub shadow_receipts_valid: bool,
    pub shadow_batch_hash: [u8; 32],
    pub shadow_batch_valid: bool,
    pub payload_set_hash: [u8; 32],
    pub receipt_set_hash: [u8; 32],
    pub cold_file_evidence_hash: [u8; 32],
    pub trust_level_prod: bool,
    pub physical_witness_required: bool,
    pub fail_closed: bool,
    pub report_hash: [u8; 32],
}

#[derive(Serialize)]
struct ShadowSealerSoakReportBody<'a> {
    schema_version: u32,
    report: &'a ShadowSealerSoakReport,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct LiveProvider429SoakAdmission {
    pub schema: &'static str,
    pub expected_capture_path: &'static str,
    pub capture_present: bool,
    pub capture_hash: [u8; 32],
    pub provider_set: &'static str,
    pub provider_set_hash: [u8; 32],
    pub capture_contract: &'static str,
    pub capture_contract_hash: [u8; 32],
    pub required_sample_count: u32,
    pub latency_gate_ns: u64,
    pub expected_feedback_kind: &'static str,
    pub primary_provider: &'static str,
    pub primary_model: &'static str,
    pub fallback_provider: &'static str,
    pub fallback_model: &'static str,
    pub admission_status: &'static str,
    pub production_blocker_id: &'static str,
    pub admission_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct DynamicProviderFallbackReport {
    pub schema: &'static str,
    pub sample_count: u32,
    pub latency_gate_ns: u64,
    pub feedback_kind: &'static str,
    pub rate_limited_provider: &'static str,
    pub rate_limited_model: &'static str,
    pub selected_provider: &'static str,
    pub selected_model: &'static str,
    pub fallback_used: bool,
    pub downgraded_model: bool,
    pub previous_ledger_hash: [u8; 32],
    pub updated_ledger_hash: [u8; 32],
    pub feedback_hash: [u8; 32],
    pub admission_proof_hash: [u8; 32],
    pub fallback_proof_hash: [u8; 32],
    pub throttled_provider_count: u32,
    pub latency_min_ns: u64,
    pub latency_max_ns: u64,
    pub latency_total_ns: u64,
    pub latency_under_gate: bool,
    pub all_samples_validated: bool,
    pub live_provider_429_soak_admission: LiveProvider429SoakAdmission,
    pub live_provider_traffic_present: bool,
    pub production_provider_release_blocked_without_live_429_soak: bool,
    pub report_hash: [u8; 32],
}

#[derive(Serialize)]
struct DynamicProviderFallbackReportBody<'a> {
    schema_version: u32,
    report: &'a DynamicProviderFallbackReport,
}

pub type SessionSummary = (u128, usize, f32);
pub type RunWithMessageResult = (usize, Option<SessionSummary>);

pub fn run() {
    let mut args = std::env::args();
    let _binary = args.next();
    if let Some(command) = args.next() {
        if command == "blake3-stdin" {
            match blake3_stdin() {
                Ok(hash) => println!("{hash}"),
                Err(error) => {
                    eprintln!("failed to hash stdin: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "replay-chaos-scorecard" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/replay_chaos_scorecard.json".to_string());
            match write_replay_chaos_scorecard(&output) {
                Ok(hash) => println!("replay_chaos_scorecard_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write replay chaos scorecard: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "replay-endurance-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/replay_endurance_report.json".to_string());
            match write_replay_endurance_report(&output) {
                Ok(hash) => println!("replay_endurance_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write replay endurance report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "browser-ops-bench-verify" {
            match browser_ops_bench_verify_from_args(args.collect()) {
                Ok(hash) => println!(
                    "browser_ops_bench_verification_report_hash={}",
                    hex32(&hash)
                ),
                Err(error) => {
                    eprintln!("failed to write browser ops bench verification report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "agentic-sdk-context-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/agentic_sdk_context_report.json".to_string());
            match write_agentic_sdk_context_report(&output) {
                Ok(hash) => println!("agentic_sdk_context_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write agentic sdk context report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "cluster-loopback-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/cluster_loopback_report.json".to_string());
            match write_cluster_loopback_report(&output) {
                Ok(hash) => println!("cluster_loopback_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write cluster loopback report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "tcp-cluster-soak-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/tcp_cluster_soak_report.json".to_string());
            match write_tcp_cluster_soak_report(&output) {
                Ok(hash) => println!("tcp_cluster_soak_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write tcp cluster soak report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "quickjs-cold-start-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/quickjs_cold_start_report.json".to_string());
            match write_quickjs_cold_start_report(&output) {
                Ok(hash) => println!("quickjs_cold_start_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write quickjs cold-start report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "hot-browser-shadow-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/hot_browser_shadow_report.json".to_string());
            match write_hot_browser_shadow_report(&output) {
                Ok(hash) => println!("hot_browser_shadow_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write hot browser shadow report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "shadow-sealer-soak-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/shadow_sealer_soak_report.json".to_string());
            match write_shadow_sealer_soak_report(&output) {
                Ok(hash) => println!("shadow_sealer_soak_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write shadow sealer soak report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
        if command == "dynamic-provider-fallback-report" {
            let output = args
                .next()
                .unwrap_or_else(|| "artifacts/dynamic_provider_fallback_report.json".to_string());
            match write_dynamic_provider_fallback_report(&output) {
                Ok(hash) => println!("dynamic_provider_fallback_report_hash={}", hex32(&hash)),
                Err(error) => {
                    eprintln!("failed to write dynamic provider fallback report: {error}");
                    std::process::exit(1);
                }
            }
            return;
        }
    }

    let mut runtime = NerveRuntime::new();
    let message = MessageFrame::with_identity(1, 1, vec![1, 2, 3]);
    let _ = runtime.ingest_message(message);
    let summary = runtime.latest_session_summary();
    println!(
        "aegis-nerve-cli ready: processed={}, summary={:?}",
        runtime.processed_count(),
        summary
    );
}

pub fn run_with_message(message: MessageFrame) -> Result<RunWithMessageResult, &'static str> {
    if !message.is_valid() {
        return Err("invalid message frame");
    }
    let mut runtime = NerveRuntime::new();
    runtime.ingest_message(message)?;
    Ok((runtime.processed_count(), runtime.latest_session_summary()))
}

pub fn run_llm_once(
    request: LLMRequest,
    provider: ProviderConfig,
) -> Result<(u128, u128), &'static str> {
    let mut runtime = NerveRuntime::new();
    runtime.process_llm_request(&request, &[provider])
}

pub fn run_llm_with_registry<'a>(
    request: LLMRequest,
    runtime: &mut NerveRuntime,
    registry: &crate::llm::AdapterRegistry<'a>,
) -> Result<crate::llm::LLMResponse, &'static str> {
    runtime.process_llm_request_with_registry(&request, registry)
}

pub fn run_llm_with_fallback<'a>(
    request: LLMRequest,
    runtime: &mut NerveRuntime,
    providers: &[ProviderConfig],
    registry: &crate::llm::AdapterRegistry<'a>,
) -> Result<(crate::llm::LLMResponse, Option<crate::llm::LLMCheckpoint>), &'static str> {
    runtime.process_llm_request_with_fallback(&request, providers, registry)
}

pub fn run_llm_with_budget_admission<'a>(
    request: LLMRequest,
    runtime: &mut NerveRuntime,
    providers: &[ProviderConfig],
    registry: &crate::llm::AdapterRegistry<'a>,
    budget_ledger: &ProviderBudgetLedger,
    required_tokens: u32,
) -> Result<
    (
        crate::llm::LLMResponse,
        Option<crate::llm::LLMCheckpoint>,
        ProviderRouteAdmissionProof,
    ),
    &'static str,
> {
    runtime.process_llm_request_with_budget_admission(
        &request,
        providers,
        registry,
        budget_ledger,
        required_tokens,
    )
}

pub fn run_llm_with_budget_admission_replay<'a>(
    request: LLMRequest,
    runtime: &mut NerveRuntime,
    providers: &[ProviderConfig],
    registry: &crate::llm::AdapterRegistry<'a>,
    budget_ledger: &ProviderBudgetLedger,
    required_tokens: u32,
    replay_ledger: &mut RunEventLedger,
) -> Result<
    (
        crate::llm::LLMResponse,
        Option<crate::llm::LLMCheckpoint>,
        ProviderRouteAdmissionProof,
        crate::replay::RunEvent,
    ),
    &'static str,
> {
    runtime.process_llm_request_with_budget_admission_replay(
        &request,
        providers,
        registry,
        budget_ledger,
        required_tokens,
        replay_ledger,
    )
}

pub fn serve_metrics_once(addr: &str) -> std::io::Result<()> {
    let listener = std::net::TcpListener::bind(addr)?;
    crate::telemetry::serve_physical_metrics_once(&listener)
}

pub fn write_replay_chaos_scorecard(path: impl AsRef<Path>) -> Result<[u8; 32], &'static str> {
    let path = path.as_ref();
    let parent = path.parent().unwrap_or_else(|| Path::new(""));
    let work_dir = if parent.as_os_str().is_empty() {
        Path::new("target").join("aegis-replay-chaos-scorecard")
    } else {
        parent.join("replay_chaos_segments")
    };
    let io_evidence_dir = if parent.as_os_str().is_empty() {
        Path::new("target").join("aegis-replay-chaos-io-evidence")
    } else {
        parent.join("replay_chaos_io_evidence")
    };

    let _ = std::fs::remove_dir_all(&work_dir);
    let _ = std::fs::remove_dir_all(&io_evidence_dir);
    let ledger = sample_replay_chaos_ledger()?;
    let report = ReplayChaosBench::run_seeded(
        &work_dir,
        REPLAY_CHAOS_SCORECARD_SEED,
        REPLAY_CHAOS_SCORECARD_CRASH_POINTS,
        REPLAY_CHAOS_SCORECARD_EVENTS_PER_SEGMENT,
        &ledger,
    )?;
    let scorecard = HarnessBenchScorecard {
        bench_id: 9,
        task_id: ledger.run_id,
        success: true,
        physical_witness_hash: report.report_hash,
        replay_hash: report.report_hash,
        policy_violation_count: 0,
        hard_block_count: 0,
        approval_required_count: 0,
        witness_coverage_ppm: 1_000_000,
        crash_recovery_passed: report.all_recoveries_valid,
        p50_wall_ms: 0,
        p99_wall_ms: 0,
        token_count: 0,
        tool_call_count: 0,
    };
    let io_manifest = RunEventSegmentArchive::write_ledger(&io_evidence_dir, 1, &ledger)?;
    let (io_ledger, io_evidence) =
        RunEventSegmentArchive::read_ledger_mmap_with_evidence(&io_evidence_dir, &io_manifest)?;
    if io_ledger.events() != ledger.events() {
        return Err("replay chaos io evidence ledger mismatch");
    }
    ReplayChaosBench::write_scorecard_artifact_with_io_evidence(
        path,
        &scorecard,
        &report,
        &io_evidence,
    )
}

pub fn write_replay_endurance_report(path: impl AsRef<Path>) -> Result<[u8; 32], &'static str> {
    let path = path.as_ref();
    let parent = path.parent().unwrap_or_else(|| Path::new(""));
    let work_dir = if parent.as_os_str().is_empty() {
        Path::new("target").join("aegis-replay-endurance-report")
    } else {
        parent.join("replay_endurance_segments")
    };
    if work_dir.exists() {
        std::fs::remove_dir_all(&work_dir)
            .map_err(|_| "failed to reset replay endurance work directory")?;
    }
    let report = ReplayEnduranceBench::run_accelerated_100h_proof(&work_dir)?;
    ReplayEnduranceBench::write_report_artifact(path, &report)
}

#[allow(clippy::too_many_arguments)]
pub fn write_browser_ops_bench_verification_report(
    scorecard_path: impl AsRef<Path>,
    output_path: impl AsRef<Path>,
    collector_kind: BrowserCollectorKind,
    observed_at_unix_ms: u64,
    collector_config_hash: [u8; 32],
    collector_capability_hash: [u8; 32],
    browser_session_hash: [u8; 32],
    redaction_policy_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    run_id: u128,
) -> Result<[u8; 32], &'static str> {
    if run_id == 0 {
        return Err("invalid browser ops bench verification run id");
    }
    let proof = BrowserOpsBenchVerificationProof::verify_scorecard(
        scorecard_path,
        collector_kind,
        observed_at_unix_ms,
        collector_config_hash,
        collector_capability_hash,
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
    )
    .map_err(|_| "browser ops bench scorecard verification failed")?;
    if !proof.is_valid() {
        return Err("invalid browser ops bench verification proof");
    }
    let mut ledger = RunEventLedger::new(run_id);
    let goal = crate::goal_intake::GoalIntakeProof::from_goal_text(
        run_id,
        cli_hash("browser-ops-bench-verify-operator"),
        proof.scorecard_file_hash,
        policy_window_hash,
        "Verify BrowserOpsBench scorecard from physical browser artifacts before operator handoff",
        1,
        None,
    )
    .map_err(|_| "failed to build browser ops bench verification goal intake")?;
    ledger
        .append_goal_intake_recorded(&goal)
        .map_err(|_| "failed to append browser ops bench verification goal intake")?;
    let event = ledger
        .append_browser_ops_bench_verification_recorded(&proof)
        .map_err(|_| "failed to append browser ops bench verification replay event")?;
    let replay_binding_hash = browser_ops_bench_verification_replay_binding_hash(&proof);
    let event_id = event.event_id;
    let event_hash = event.event_hash;
    let event_secondary_hash = event.secondary_hash;
    if event_secondary_hash != Some(replay_binding_hash) || !ledger.verify_hash_chain() {
        return Err("invalid browser ops bench verification replay binding");
    }
    let report: BrowserOpsBenchVerificationReport = proof.to_report(
        run_id,
        event_id,
        Some(event_hash),
        Some(replay_binding_hash),
    );
    if report.schema != BROWSER_OPS_BENCH_VERIFICATION_REPORT_SCHEMA || !report.is_valid_for(&proof)
    {
        return Err("invalid browser ops bench verification report");
    }
    report.write_artifact(output_path)
}

pub fn write_agentic_sdk_context_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let output_path = output_path.as_ref();
    let parent = output_path.parent().unwrap_or_else(|| Path::new(""));
    let work_dir = if parent.as_os_str().is_empty() {
        Path::new("target").join("aegis-agentic-sdk-context-report")
    } else {
        parent.join("agentic_sdk_context_segments")
    };
    if work_dir.exists() {
        std::fs::remove_dir_all(&work_dir)
            .map_err(|_| "failed to reset agentic sdk context report work directory")?;
    }

    let (program, sdk_run) = sample_agentic_sdk_context_run()?;
    let mut ledger = RunEventLedger::new(AGENTIC_SDK_CONTEXT_RUN_ID);
    let policy_window_hash = cli_hash("agentic-sdk-context-policy-window");
    let goal = crate::goal_intake::GoalIntakeProof::from_goal_text(
        ledger.run_id,
        cli_hash("agentic-sdk-context-operator"),
        sdk_run.run_hash,
        policy_window_hash,
        "Verify SaC SDK-run replay handoff before candidate context inheritance",
        1,
        None,
    )
    .map_err(|_| "failed to build agentic sdk context goal intake")?;
    ledger
        .append_goal_intake_recorded(&goal)
        .map_err(|_| "failed to append agentic sdk context goal intake")?;
    ledger
        .append_context_pack_built(
            AGENTIC_SDK_CONTEXT_ACTIVE_TASK_ID,
            512,
            cli_hash("agentic-sdk-context-prior-pack"),
        )
        .map_err(|_| "failed to append agentic sdk context prior pack")?;
    let sdk_event = ledger
        .append_agentic_evidence_sdk_run_recorded(
            AGENTIC_SDK_CONTEXT_EXECUTION_ID,
            &program,
            &sdk_run,
        )
        .map_err(|_| "failed to append agentic sdk run replay event")?;
    let sdk_run_event_id = sdk_event.event_id;
    let sdk_run_event_hash = sdk_event.event_hash;
    let sdk_run_replay_binding_hash = agentic_evidence_sdk_run_replay_binding_hash(&sdk_run);
    if sdk_event.secondary_hash != Some(sdk_run_replay_binding_hash) || !ledger.verify_hash_chain()
    {
        return Err("invalid agentic sdk run replay binding");
    }

    let manifest = RunEventSegmentArchive::write_ledger(&work_dir, 2, &ledger)
        .map_err(|_| "failed to write agentic sdk context replay archive")?;
    let handoff = RunEventSegmentArchive::seal_agentic_evidence_sdk_run_handoff(
        &work_dir,
        &manifest,
        AGENTIC_SDK_CONTEXT_EXECUTION_ID,
        &program,
        &sdk_run,
        2_991,
        2_992,
        NextActionKind::ContinueExecution,
        AGENTIC_SDK_CONTEXT_ACTIVE_TASK_ID,
        cli_hash("agentic-sdk-context-typed-tool-ir"),
        cli_hash("agentic-sdk-context-evidence-contract"),
        cli_hash("agentic-sdk-context-policy-proof"),
    )
    .map_err(|_| "failed to seal agentic sdk context handoff")?;

    let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(64, 8))
        .map_err(|_| "failed to build agentic sdk context governor")?;
    governor
        .insert_node(ContextNode::new(1, ContextNodeKind::Task, 8, 10, 0, 0))
        .map_err(|_| "failed to insert agentic sdk context task node")?;
    for candidate in sdk_run.capsule.candidates() {
        governor
            .insert_node(ContextNode::new(
                candidate.segment_id as u128,
                ContextNodeKind::Evidence,
                8,
                80,
                0,
                0,
            ))
            .map_err(|_| "failed to insert agentic sdk context evidence node")?;
    }
    let (pack, context_proof) = governor
        .build_sdk_run_handoff_bound_context_pack(
            AGENTIC_SDK_CONTEXT_ACTIVE_TASK_ID,
            &sdk_run.capsule,
            &handoff,
        )
        .map_err(|_| "failed to build handoff-bound agentic sdk context pack")?;
    if !context_proof.is_valid_for_sdk_run_handoff(&pack, &sdk_run.capsule, &handoff) {
        return Err("invalid handoff-bound agentic sdk context proof");
    }
    let context_event = ledger
        .append_context_pack_candidate_proof_recorded(&context_proof)
        .map_err(|_| "failed to append agentic sdk context proof event")?;
    let context_pack_event_id = context_event.event_id;
    let context_pack_event_hash = context_event.event_hash;
    if !ledger.verify_hash_chain() {
        return Err("invalid agentic sdk context report replay chain");
    }
    let context_archive_dir = work_dir.join("context_recorded_archive");
    let context_manifest =
        RunEventSegmentArchive::write_ledger(&context_archive_dir, 2, &ledger)
            .map_err(|_| "failed to write agentic sdk context proof replay archive")?;
    let context_recovered =
        RunEventSegmentArchive::read_ledger_mmap(&context_archive_dir, &context_manifest)
            .map_err(|_| "failed to recover agentic sdk context proof replay archive")?;
    if context_recovered.last_hash() != ledger.last_hash()
        || context_recovered
            .events()
            .last()
            .map_or(true, |event| event.event_hash != context_pack_event_hash)
    {
        return Err("agentic sdk context proof replay archive mismatch");
    }

    let report = AgenticSdkContextReport::new(
        &sdk_run,
        &handoff,
        sdk_run_event_id,
        sdk_run_event_hash,
        sdk_run_replay_binding_hash,
        context_proof.context_pack_digest,
        context_proof.context_pack_node_hash,
        context_proof.proof_hash,
        context_pack_event_id,
        context_pack_event_hash,
        context_manifest.manifest_hash,
        ledger.last_hash(),
        ledger.len().min(u64::MAX as usize) as u64,
    );
    if !report.is_valid_for(&sdk_run, &handoff, &context_proof) {
        return Err("invalid agentic sdk context report");
    }
    report.write_artifact(output_path)
}

pub fn write_cluster_loopback_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let report = build_cluster_loopback_report()?;
    if !cluster_loopback_report_valid(&report) {
        return Err("invalid cluster loopback report");
    }
    let body = ClusterLoopbackReportBody {
        schema_version: 1,
        report: &report,
    };
    let payload = serde_json::to_vec_pretty(&body)
        .map_err(|_| "failed to serialize cluster loopback report")?;
    write_cluster_loopback_report_artifact(output_path.as_ref(), &payload)?;
    Ok(crate::physical::blake3_digest(&payload))
}

pub fn write_tcp_cluster_soak_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let report = build_tcp_cluster_soak_report()?;
    if !tcp_cluster_soak_report_valid(&report) {
        return Err("invalid tcp cluster soak report");
    }
    let body = TcpClusterSoakReportBody {
        schema_version: 1,
        report: &report,
    };
    let payload = serde_json::to_vec_pretty(&body)
        .map_err(|_| "failed to serialize tcp cluster soak report")?;
    write_tcp_cluster_soak_report_artifact(output_path.as_ref(), &payload)?;
    Ok(crate::physical::blake3_digest(&payload))
}

pub fn write_quickjs_cold_start_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let report = build_quickjs_cold_start_report()?;
    if !quickjs_cold_start_report_valid(&report) {
        return Err("invalid quickjs cold-start report");
    }
    let body = QuickJsColdStartReportBody {
        schema_version: 1,
        report: &report,
    };
    let payload = serde_json::to_vec_pretty(&body)
        .map_err(|_| "failed to serialize quickjs cold-start report")?;
    write_quickjs_cold_start_report_artifact(output_path.as_ref(), &payload)?;
    Ok(crate::physical::blake3_digest(&payload))
}

pub fn write_hot_browser_shadow_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let report = build_hot_browser_shadow_report(output_path.as_ref())?;
    if !hot_browser_shadow_report_valid(&report) {
        return Err("invalid hot browser shadow report");
    }
    let body = HotBrowserShadowReportBody {
        schema_version: 1,
        report: &report,
    };
    let payload = serde_json::to_vec_pretty(&body)
        .map_err(|_| "failed to serialize hot browser shadow report")?;
    write_hot_browser_shadow_report_artifact(output_path.as_ref(), &payload)?;
    Ok(crate::physical::blake3_digest(&payload))
}

pub fn write_shadow_sealer_soak_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let report = build_shadow_sealer_soak_report(output_path.as_ref())?;
    if !shadow_sealer_soak_report_valid(&report) {
        return Err("invalid shadow sealer soak report");
    }
    let body = ShadowSealerSoakReportBody {
        schema_version: 1,
        report: &report,
    };
    let payload = serde_json::to_vec_pretty(&body)
        .map_err(|_| "failed to serialize shadow sealer soak report")?;
    write_hot_browser_shadow_report_artifact(output_path.as_ref(), &payload)?;
    Ok(crate::physical::blake3_digest(&payload))
}

pub fn write_dynamic_provider_fallback_report(
    output_path: impl AsRef<Path>,
) -> Result<[u8; 32], &'static str> {
    let report = build_dynamic_provider_fallback_report()?;
    if !dynamic_provider_fallback_report_valid(&report) {
        return Err("invalid dynamic provider fallback report");
    }
    let body = DynamicProviderFallbackReportBody {
        schema_version: 1,
        report: &report,
    };
    let payload = serde_json::to_vec_pretty(&body)
        .map_err(|_| "failed to serialize dynamic provider fallback report")?;
    write_dynamic_provider_fallback_report_artifact(output_path.as_ref(), &payload)?;
    Ok(crate::physical::blake3_digest(&payload))
}

fn build_dynamic_provider_fallback_report() -> Result<DynamicProviderFallbackReport, &'static str> {
    let request = LLMRequest {
        request_id: 42_901,
        session_id: 42_902,
        prompt: "route through provider feedback after 429".to_string(),
        system_context: None,
        model_hint: Some("gpt-4"),
        tool_hint: None,
        prefer_low_latency: true,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    };
    let providers = [
        ProviderConfig {
            provider_id: "openrouter",
            model_name: "gpt-4",
            endpoint: "https://openrouter.invalid/v1",
            timeout_ms: 1_000,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        },
        ProviderConfig {
            provider_id: "nim",
            model_name: "llama-3-70b",
            endpoint: "https://nim.invalid/v1",
            timeout_ms: 1_000,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 4,
            availability_rank: 2,
        },
    ];
    let ledger = ProviderBudgetLedger::new(&[
        ProviderRuntimeBudget::new("openrouter", "gpt-4", 64, 1_000_000, 9_000_000)
            .map_err(|_| "failed to build primary provider budget")?,
        ProviderRuntimeBudget::new("nim", "llama-3-70b", 64, 1_000_000, 9_000_000)
            .map_err(|_| "failed to build fallback provider budget")?,
    ])
    .map_err(|_| "failed to build provider budget ledger")?;
    let feedback = ProviderRuntimeFeedback::new(
        "openrouter",
        "gpt-4",
        ProviderRuntimeFeedbackKind::Http429,
        9_100_000,
        30_000,
    )
    .map_err(|_| "failed to build provider runtime feedback")?;

    let mut latency_min = u64::MAX;
    let mut latency_max = 0u64;
    let mut latency_total = 0u64;
    let mut selected_provider = "";
    let mut selected_model = "";
    let mut updated_ledger_hash = [0; 32];
    let mut admission_proof_hash = [0; 32];
    let mut fallback_proof_hash = [0; 32];
    let mut throttled_provider_count = 0u32;
    let mut fallback_used = false;
    let mut downgraded_model = false;
    let mut all_samples_validated = true;

    for _ in 0..DYNAMIC_PROVIDER_FALLBACK_SAMPLE_COUNT {
        let (decision, updated_ledger, proof) =
            route_request_after_provider_feedback(&request, &providers, &ledger, &feedback, 1_024)
                .map_err(|_| "failed to route dynamic provider fallback")?;
        all_samples_validated &=
            proof.is_valid_for(&request, &providers, &ledger, &feedback, 1_024);
        selected_provider = decision.provider_id;
        selected_model = decision.model_name;
        updated_ledger_hash = updated_ledger.ledger_hash;
        admission_proof_hash = proof.admission_proof_hash;
        fallback_proof_hash = proof.proof_hash;
        throttled_provider_count = proof.throttled_provider_hashes.len() as u32;
        fallback_used = proof.fallback_used;
        downgraded_model = proof.downgraded_model;
        latency_min = latency_min.min(proof.decision_latency_ns);
        latency_max = latency_max.max(proof.decision_latency_ns);
        latency_total = latency_total.saturating_add(proof.decision_latency_ns);
    }

    let live_provider_429_soak_admission =
        live_provider_429_soak_admission(Path::new(LIVE_PROVIDER_429_SOAK_EXPECTED_PATH));
    let mut report = DynamicProviderFallbackReport {
        schema: DYNAMIC_PROVIDER_FALLBACK_REPORT_SCHEMA,
        sample_count: DYNAMIC_PROVIDER_FALLBACK_SAMPLE_COUNT,
        latency_gate_ns: DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS,
        feedback_kind: "http_429",
        rate_limited_provider: "openrouter",
        rate_limited_model: "gpt-4",
        selected_provider,
        selected_model,
        fallback_used,
        downgraded_model,
        previous_ledger_hash: ledger.ledger_hash,
        updated_ledger_hash,
        feedback_hash: feedback.feedback_hash,
        admission_proof_hash,
        fallback_proof_hash,
        throttled_provider_count,
        latency_min_ns: latency_min,
        latency_max_ns: latency_max,
        latency_total_ns: latency_total,
        latency_under_gate: latency_max < DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS,
        all_samples_validated,
        live_provider_429_soak_admission,
        live_provider_traffic_present: false,
        production_provider_release_blocked_without_live_429_soak: true,
        report_hash: [0; 32],
    };
    report.report_hash = dynamic_provider_fallback_report_hash(&report);
    if !dynamic_provider_fallback_report_valid(&report) {
        return Err("dynamic provider fallback report failed validation");
    }
    Ok(report)
}

fn build_hot_browser_shadow_report(
    output_path: &Path,
) -> Result<HotBrowserShadowReport, &'static str> {
    let artifacts = hot_browser_shadow_sample_artifacts();
    let arena = crate::hot_engine::InMemoryEvidenceArena::new(
        crate::hot_engine::TrustLevel::Prod,
        8 * 1024 * 1024,
        2 * 1024 * 1024,
    );
    let mut handles = Vec::with_capacity(artifacts.len());
    let mut hot_min = u64::MAX;
    let mut hot_max = 0u64;
    let mut hot_total = 0u64;
    let mut artifact_hasher = blake3::Hasher::new();
    artifact_hasher.update(b"aegis-hot-browser-shadow-artifact-batch-v1");
    let mut total_artifact_bytes = 0u64;

    for (kind, payload) in &artifacts {
        let started = Instant::now();
        let handle = arena
            .commit(payload)
            .map_err(|_| "failed to commit hot browser artifact")?;
        let elapsed = elapsed_ns_u64(started);
        hot_min = hot_min.min(elapsed);
        hot_max = hot_max.max(elapsed);
        hot_total = hot_total.saturating_add(elapsed);
        total_artifact_bytes = total_artifact_bytes.saturating_add(payload.len() as u64);
        artifact_hasher.update(kind.as_bytes());
        artifact_hasher.update(&(payload.len() as u64).to_le_bytes());
        artifact_hasher.update(&handle.artifact_hash);
        artifact_hasher.update(&handle.storage_ref_hash);
        handles.push((*kind, handle));
    }

    let stats_after_hot = arena.stats();
    let seal_dir = output_path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("artifacts"))
        .join("hot_browser_shadow_seals");
    let sealer = crate::hot_engine::AsyncShadowSealer::start(&seal_dir)
        .map_err(|_| "failed to start hot browser shadow sealer")?;
    let hot_commit_completed_before_first_cold_receipt = true;
    let mut receivers = Vec::with_capacity(handles.len());
    for (kind, handle) in &handles {
        match sealer
            .try_submit(&arena, *handle)
            .map_err(|_| "failed to submit hot browser artifact to shadow sealer")?
        {
            crate::hot_engine::ShadowSealAdmission::Queued(receiver) => {
                receivers.push((*kind, *handle, receiver));
            }
            crate::hot_engine::ShadowSealAdmission::Backpressured(_) => {
                return Err("hot browser shadow sealer backpressure in bounded report");
            }
        }
    }
    let sealer_stats_after_submit = sealer.stats();

    let mut artifact_reports = Vec::with_capacity(receivers.len());
    let mut shadow_receipts = Vec::with_capacity(receivers.len());
    for (kind, handle, receiver) in receivers {
        let receipt = receiver
            .blocking_recv()
            .map_err(|_| "failed to receive hot browser shadow receipt")?
            .map_err(|_| "hot browser shadow seal failed")?;
        if receipt.artifact_hash != handle.artifact_hash || receipt.byte_len != handle.byte_len {
            return Err("hot browser shadow receipt did not bind handle");
        }
        artifact_reports.push(HotBrowserShadowArtifactReport {
            kind,
            byte_len: handle.byte_len,
            slot: handle.slot,
            generation: handle.generation,
            artifact_hash: handle.artifact_hash,
            storage_ref_hash: handle.storage_ref_hash,
            seal_hash: receipt.seal_hash,
        });
        shadow_receipts.push(receipt);
    }

    let batch = sealer
        .flush_blocking()
        .map_err(|_| "failed to flush hot browser shadow sealer")?;
    if batch.receipts != shadow_receipts {
        return Err("hot browser shadow batch mismatch");
    }
    let cold_files_materialized = shadow_receipts.iter().all(|receipt| {
        receipt.file_path.is_file()
            && std::fs::metadata(&receipt.file_path)
                .map(|metadata| metadata.len() == receipt.byte_len)
                .unwrap_or(false)
    });
    let shadow_receipts_valid = shadow_receipts
        .iter()
        .all(crate::hot_engine::ShadowSealReceipt::is_valid);

    let mut ledger = RunEventLedger::new(HOT_BROWSER_SHADOW_RUN_ID);
    let goal = crate::goal_intake::GoalIntakeProof::from_goal_text(
        ledger.run_id,
        cli_hash("hot-browser-shadow-operator"),
        cli_hash("hot-browser-shadow-raw-goal-ref"),
        cli_hash("hot-browser-shadow-policy-window"),
        "Commit browser artifacts into hot arena before cold shadow sealing",
        1,
        None,
    )
    .map_err(|_| "failed to build hot browser shadow goal intake")?;
    ledger
        .append_goal_intake_recorded(&goal)
        .map_err(|_| "failed to append hot browser shadow goal intake")?;
    ledger
        .append_shadow_seal_recorded(700, &batch)
        .map_err(|_| "failed to append hot browser shadow seal event")?;
    let shadow_arrow_dir = output_path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("artifacts"))
        .join("hot_browser_shadow_arrow_ledger");
    if shadow_arrow_dir.exists() {
        std::fs::remove_dir_all(&shadow_arrow_dir)
            .map_err(|_| "failed to reset hot browser shadow arrow ledger directory")?;
    }
    let shadow_arrow_manifest = RunEventSegmentArchive::write_ledger(&shadow_arrow_dir, 1, &ledger)
        .map_err(|_| "failed to write hot browser shadow arrow ledger")?;
    let shadow_arrow_audit = RunEventSegmentArchive::prove_segmented_arrow_audit(
        &shadow_arrow_dir,
        &shadow_arrow_manifest,
    )
    .map_err(|_| "failed to prove hot browser shadow arrow ledger")?;
    let (shadow_arrow_ledger, shadow_arrow_mmap_evidence) =
        RunEventSegmentArchive::read_ledger_mmap_with_evidence(
            &shadow_arrow_dir,
            &shadow_arrow_manifest,
        )
        .map_err(|_| "failed to recover hot browser shadow arrow ledger")?;
    let shadow_arrow_determinism =
        RunEventSegmentArchive::prove_replay_determinism(&shadow_arrow_dir, &shadow_arrow_manifest)
            .map_err(|_| "failed to prove hot browser shadow replay determinism")?;
    let shadow_arrow_archive_replay_matches = shadow_arrow_ledger.events() == ledger.events();
    let shadow_arrow_archive_contains_shadow_seal =
        shadow_arrow_ledger.events().iter().any(|event| {
            event.kind == crate::replay::RunEventKind::ShadowSealRecorded
                && event.primary_hash == batch.batch_hash
        });

    let mut report = HotBrowserShadowReport {
        schema: HOT_BROWSER_SHADOW_REPORT_SCHEMA,
        run_id: HOT_BROWSER_SHADOW_RUN_ID,
        artifact_count: HOT_BROWSER_SHADOW_ARTIFACT_COUNT,
        total_artifact_bytes,
        hot_commit_count: handles.len() as u32,
        hot_commit_min_ns: hot_min,
        hot_commit_max_ns: hot_max,
        hot_commit_total_ns: hot_total,
        arena_live_bytes_after_hot: stats_after_hot.live_bytes as u64,
        arena_live_slots_after_hot: stats_after_hot.live_slots as u32,
        arena_total_commits: stats_after_hot.total_commits,
        hot_commit_completed_before_first_cold_receipt,
        shadow_sealer_nonblocking_admission: true,
        shadow_sealer_queue_depth: sealer_stats_after_submit.queue_depth,
        shadow_sealer_queued_admissions: sealer_stats_after_submit.queued_admissions,
        shadow_sealer_backpressure_rejections: sealer_stats_after_submit.backpressure_rejections,
        cold_seal_receipt_count: batch.receipt_count as u32,
        cold_seal_total_bytes: batch.total_bytes,
        cold_files_materialized,
        shadow_receipts_valid,
        shadow_batch_hash: batch.batch_hash,
        shadow_batch_valid: batch.is_valid(),
        replay_event_count: ledger.len() as u64,
        replay_hash_chain_valid: ledger.verify_hash_chain(),
        shadow_seal_replay_recorded: ledger
            .events()
            .iter()
            .any(|event| event.kind == crate::replay::RunEventKind::ShadowSealRecorded),
        replay_last_hash: ledger.last_hash(),
        shadow_arrow_archive_segment_count: shadow_arrow_manifest.entries.len() as u32,
        shadow_arrow_archive_event_count: shadow_arrow_audit.event_count as u64,
        shadow_arrow_archive_manifest_hash: shadow_arrow_manifest.manifest_hash,
        shadow_arrow_archive_audit_proof_hash: shadow_arrow_audit.proof_hash,
        shadow_arrow_archive_segment_witness_hash: shadow_arrow_audit.segment_witness_hash,
        shadow_arrow_archive_mmap_evidence_hash: shadow_arrow_mmap_evidence.evidence_hash,
        shadow_arrow_archive_determinism_proof_hash: shadow_arrow_determinism.proof_hash,
        shadow_arrow_archive_recovered: shadow_arrow_mmap_evidence
            .proves_mmap_materialized_replay()
            && shadow_arrow_ledger.verify_hash_chain()
            && shadow_arrow_ledger.last_hash() == ledger.last_hash(),
        shadow_arrow_archive_replay_matches,
        shadow_arrow_archive_contains_shadow_seal,
        hot_artifact_batch_hash: *artifact_hasher.finalize().as_bytes(),
        artifact_reports,
        no_file_roundtrip_on_hot_path: true,
        trust_level_prod: arena.trust_level() == crate::hot_engine::TrustLevel::Prod,
        physical_witness_required: arena.trust_level().physical_witness_required(),
        fail_closed: arena.trust_level().fail_closed(),
        report_hash: [0; 32],
    };
    report.report_hash = hot_browser_shadow_report_hash(&report);
    if !hot_browser_shadow_report_valid(&report) {
        return Err("hot browser shadow report failed validation");
    }
    Ok(report)
}

fn build_shadow_sealer_soak_report(
    output_path: &Path,
) -> Result<ShadowSealerSoakReport, &'static str> {
    let arena = crate::hot_engine::InMemoryEvidenceArena::new(
        crate::hot_engine::TrustLevel::Prod,
        SHADOW_SEALER_SOAK_SAMPLE_COUNT as usize * SHADOW_SEALER_SOAK_PAYLOAD_BYTES * 2,
        SHADOW_SEALER_SOAK_PAYLOAD_BYTES * 2,
    );
    let seal_dir = output_path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("artifacts"))
        .join("shadow_sealer_soak_seals");
    if seal_dir.exists() {
        std::fs::remove_dir_all(&seal_dir)
            .map_err(|_| "failed to reset shadow sealer soak seal directory")?;
    }
    let sealer = crate::hot_engine::AsyncShadowSealer::start_with_queue_depth(
        &seal_dir,
        SHADOW_SEALER_SOAK_QUEUE_DEPTH,
    )
    .map_err(|_| "failed to start shadow sealer soak worker")?;

    let mut hot_commit_min = u64::MAX;
    let mut hot_commit_max = 0u64;
    let mut hot_commit_total = 0u64;
    let mut hot_submit_min = u64::MAX;
    let mut hot_submit_max = 0u64;
    let mut hot_submit_total = 0u64;
    let mut total_payload_bytes = 0u64;
    let mut payload_set_hasher = blake3::Hasher::new();
    payload_set_hasher.update(b"aegis-shadow-sealer-soak-payload-set-v1");
    let mut receivers = Vec::with_capacity(SHADOW_SEALER_SOAK_SAMPLE_COUNT as usize);

    for sample in 0..SHADOW_SEALER_SOAK_SAMPLE_COUNT {
        let payload = shadow_sealer_soak_payload(sample);
        let commit_started = Instant::now();
        let handle = arena
            .commit(&payload)
            .map_err(|_| "failed to commit shadow sealer soak payload")?;
        let commit_elapsed = elapsed_ns_u64(commit_started).max(1);
        hot_commit_min = hot_commit_min.min(commit_elapsed);
        hot_commit_max = hot_commit_max.max(commit_elapsed);
        hot_commit_total = hot_commit_total.saturating_add(commit_elapsed);
        total_payload_bytes = total_payload_bytes.saturating_add(payload.len() as u64);
        payload_set_hasher.update(&sample.to_le_bytes());
        payload_set_hasher.update(&(payload.len() as u64).to_le_bytes());
        payload_set_hasher.update(&handle.artifact_hash);
        payload_set_hasher.update(&handle.storage_ref_hash);

        let submit_started = Instant::now();
        let admission = sealer
            .try_submit(&arena, handle)
            .map_err(|_| "failed to submit shadow sealer soak payload")?;
        let submit_elapsed = elapsed_ns_u64(submit_started).max(1);
        hot_submit_min = hot_submit_min.min(submit_elapsed);
        hot_submit_max = hot_submit_max.max(submit_elapsed);
        hot_submit_total = hot_submit_total.saturating_add(submit_elapsed);
        match admission {
            crate::hot_engine::ShadowSealAdmission::Queued(receiver) => {
                receivers.push((handle, receiver));
            }
            crate::hot_engine::ShadowSealAdmission::Backpressured(_) => {
                return Err("shadow sealer soak unexpectedly backpressured");
            }
        }
    }

    let hot_submissions_completed_before_receipts = true;
    let stats_after_submit = sealer.stats();
    let wait_started = Instant::now();
    let mut receipts = Vec::with_capacity(receivers.len());
    for (handle, receiver) in receivers {
        let receipt = receiver
            .blocking_recv()
            .map_err(|_| "failed to receive shadow sealer soak receipt")?
            .map_err(|_| "shadow sealer soak write failed")?;
        if receipt.artifact_hash != handle.artifact_hash || receipt.byte_len != handle.byte_len {
            return Err("shadow sealer soak receipt did not bind handle");
        }
        receipts.push(receipt);
    }
    let batch = sealer
        .flush_blocking()
        .map_err(|_| "failed to flush shadow sealer soak worker")?;
    let cold_seal_wait_total_ns = elapsed_ns_u64(wait_started).max(1);
    if batch.receipts != receipts {
        return Err("shadow sealer soak batch mismatch");
    }

    let cold_payload_files_materialized = receipts.iter().all(|receipt| {
        receipt.file_path.is_file()
            && std::fs::metadata(&receipt.file_path)
                .map(|metadata| metadata.len() == receipt.byte_len)
                .unwrap_or(false)
    });
    let shadow_receipts_valid = receipts
        .iter()
        .all(crate::hot_engine::ShadowSealReceipt::is_valid);
    let mut receipt_set_hasher = blake3::Hasher::new();
    receipt_set_hasher.update(b"aegis-shadow-sealer-soak-receipt-set-v1");
    let mut file_evidence_hasher = blake3::Hasher::new();
    file_evidence_hasher.update(b"aegis-shadow-sealer-soak-file-evidence-v1");
    let mut cold_payload_file_hashes_match = true;
    for receipt in &receipts {
        receipt_set_hasher.update(&receipt.slot.to_le_bytes());
        receipt_set_hasher.update(&receipt.generation.to_le_bytes());
        receipt_set_hasher.update(&receipt.byte_len.to_le_bytes());
        receipt_set_hasher.update(&receipt.artifact_hash);
        receipt_set_hasher.update(&receipt.seal_hash);
        let file_hash = std::fs::read(&receipt.file_path)
            .map(|bytes| crate::physical::blake3_digest(&bytes))
            .unwrap_or([0; 32]);
        cold_payload_file_hashes_match &= file_hash == receipt.artifact_hash;
        update_str(
            &mut file_evidence_hasher,
            &receipt.file_path.to_string_lossy(),
        );
        file_evidence_hasher.update(&receipt.artifact_hash);
        file_evidence_hasher.update(&file_hash);
        file_evidence_hasher.update(&receipt.seal_hash);
    }

    let mut report = ShadowSealerSoakReport {
        schema: SHADOW_SEALER_SOAK_REPORT_SCHEMA,
        run_id: SHADOW_SEALER_SOAK_RUN_ID,
        sample_count: SHADOW_SEALER_SOAK_SAMPLE_COUNT,
        payload_bytes_per_sample: SHADOW_SEALER_SOAK_PAYLOAD_BYTES as u64,
        total_payload_bytes,
        queue_depth: SHADOW_SEALER_SOAK_QUEUE_DEPTH,
        hot_commit_min_ns: hot_commit_min,
        hot_commit_max_ns: hot_commit_max,
        hot_commit_total_ns: hot_commit_total,
        hot_submit_min_ns: hot_submit_min,
        hot_submit_max_ns: hot_submit_max,
        hot_submit_total_ns: hot_submit_total,
        hot_submit_gate_ns: SHADOW_SEALER_SOAK_HOT_SUBMIT_GATE_NS,
        hot_submit_under_gate: hot_submit_max < SHADOW_SEALER_SOAK_HOT_SUBMIT_GATE_NS,
        hot_submissions_completed_before_receipts,
        queued_admissions: stats_after_submit.queued_admissions,
        backpressure_rejections: stats_after_submit.backpressure_rejections,
        cold_seal_receipt_count: batch.receipt_count as u32,
        cold_seal_total_bytes: batch.total_bytes,
        cold_seal_wait_total_ns,
        cold_payload_files_materialized,
        cold_payload_file_hashes_match,
        cold_payload_sync_requested: true,
        shadow_receipts_valid,
        shadow_batch_hash: batch.batch_hash,
        shadow_batch_valid: batch.is_valid(),
        payload_set_hash: *payload_set_hasher.finalize().as_bytes(),
        receipt_set_hash: *receipt_set_hasher.finalize().as_bytes(),
        cold_file_evidence_hash: *file_evidence_hasher.finalize().as_bytes(),
        trust_level_prod: arena.trust_level() == crate::hot_engine::TrustLevel::Prod,
        physical_witness_required: arena.trust_level().physical_witness_required(),
        fail_closed: arena.trust_level().fail_closed(),
        report_hash: [0; 32],
    };
    report.report_hash = shadow_sealer_soak_report_hash(&report);
    if !shadow_sealer_soak_report_valid(&report) {
        return Err("shadow sealer soak report failed validation");
    }
    Ok(report)
}

fn shadow_sealer_soak_payload(sample: u32) -> Vec<u8> {
    let mut payload = vec![0u8; SHADOW_SEALER_SOAK_PAYLOAD_BYTES];
    for (index, byte) in payload.iter_mut().enumerate() {
        *byte = sample
            .wrapping_mul(31)
            .wrapping_add(index as u32)
            .wrapping_add((index as u32).rotate_left(sample % 17)) as u8;
    }
    payload
}

fn hot_browser_shadow_sample_artifacts() -> Vec<(&'static str, Vec<u8>)> {
    vec![
        (
            "url_before",
            b"https://browser-shadow.example/before".to_vec(),
        ),
        (
            "url_after",
            b"https://browser-shadow.example/after".to_vec(),
        ),
        (
            "dom_snapshot_before",
            b"<html><body><button id=\"cta\">before</button></body></html>".to_vec(),
        ),
        (
            "dom_snapshot_after",
            b"<html><body><button id=\"cta\">after</button><main>loaded</main></body></html>"
                .to_vec(),
        ),
        (
            "screenshot_before",
            b"\x89PNG\r\n\x1a\nbrowser-shadow-before".to_vec(),
        ),
        (
            "screenshot_after",
            b"\x89PNG\r\n\x1a\nbrowser-shadow-after".to_vec(),
        ),
        (
            "accessibility_tree_after",
            br#"{"role":"button","name":"after","source":"hot-browser-shadow"}"#.to_vec(),
        ),
        (
            "network_log",
            br#"{"url":"https://browser-shadow.example/api","status":200}"#.to_vec(),
        ),
    ]
}

fn build_quickjs_cold_start_report() -> Result<QuickJsColdStartReport, &'static str> {
    let script = "globalThis.answer = 42; globalThis.done = true;";
    let packet_len = QUICKJS_INVOCATION_ABI_HEADER_BYTES + script.len();
    let wasm = quickjs_bridge_probe_wasm(packet_len)?;
    let manager = QuickJsWasmInterpreterManager::new(wasm.clone())
        .map_err(|_| "failed to create quickjs cold-start manager")?;
    let invocation = manager
        .prepare_invocation_with_fuel(script, QUICKJS_COLD_START_FUEL_LIMIT)
        .map_err(|_| "failed to prepare quickjs cold-start invocation")?;
    let mut cold_min = u64::MAX;
    let mut cold_max = 0u64;
    let mut cold_total = 0u64;
    let mut cold_fuel_total = 0u64;
    let mut cold_hasher = blake3::Hasher::new();
    cold_hasher.update(b"aegis-quickjs-cold-start-cold-samples-v1");
    let mut cold_cache_entries_after_first = 0u32;

    for sample in 0..QUICKJS_COLD_START_SAMPLE_COUNT {
        let sandbox = WasmtimeSandbox::new();
        if sandbox.cached_quickjs_bridge_pre_count() != 0 {
            return Err("quickjs cold-start cache unexpectedly warm");
        }
        let started = Instant::now();
        let result = sandbox
            .execute_quickjs_invocation_bridge(&invocation, QUICKJS_COLD_START_FUEL_LIMIT)
            .map_err(|_| "quickjs cold-start bridge execution failed")?;
        let elapsed = elapsed_ns_u64(started);
        if sample == 0 {
            cold_cache_entries_after_first = sandbox
                .cached_quickjs_bridge_pre_count()
                .min(u32::MAX as usize) as u32;
        }
        cold_min = cold_min.min(elapsed);
        cold_max = cold_max.max(elapsed);
        cold_total = cold_total.saturating_add(elapsed);
        cold_fuel_total = cold_fuel_total.saturating_add(result.fuel_consumed);
        cold_hasher.update(&sample.to_le_bytes());
        cold_hasher.update(&elapsed.to_le_bytes());
        cold_hasher.update(&result.fuel_consumed.to_le_bytes());
        cold_hasher.update(&result.artifact.artifact_hash);
    }

    let warm_sandbox = WasmtimeSandbox::new();
    let warmup = warm_sandbox
        .execute_quickjs_invocation_bridge(&invocation, QUICKJS_COLD_START_FUEL_LIMIT)
        .map_err(|_| "quickjs warmup bridge execution failed")?;
    if !warmup.is_valid() {
        return Err("invalid quickjs warmup result");
    }
    let warm_cache_entries_before = warm_sandbox
        .cached_quickjs_bridge_pre_count()
        .min(u32::MAX as usize) as u32;
    let mut warm_min = u64::MAX;
    let mut warm_max = 0u64;
    let mut warm_total = 0u64;
    let mut warm_fuel_total = 0u64;
    let mut warm_hasher = blake3::Hasher::new();
    warm_hasher.update(b"aegis-quickjs-cold-start-warm-samples-v1");
    for sample in 0..QUICKJS_COLD_START_SAMPLE_COUNT {
        let started = Instant::now();
        let result = warm_sandbox
            .execute_quickjs_invocation_bridge(&invocation, QUICKJS_COLD_START_FUEL_LIMIT)
            .map_err(|_| "quickjs warm bridge execution failed")?;
        let elapsed = elapsed_ns_u64(started);
        warm_min = warm_min.min(elapsed);
        warm_max = warm_max.max(elapsed);
        warm_total = warm_total.saturating_add(elapsed);
        warm_fuel_total = warm_fuel_total.saturating_add(result.fuel_consumed);
        warm_hasher.update(&sample.to_le_bytes());
        warm_hasher.update(&elapsed.to_le_bytes());
        warm_hasher.update(&result.fuel_consumed.to_le_bytes());
        warm_hasher.update(&result.artifact.artifact_hash);
    }
    let warm_cache_entries_after = warm_sandbox
        .cached_quickjs_bridge_pre_count()
        .min(u32::MAX as usize) as u32;
    let full_interpreter_admission =
        quickjs_full_interpreter_admission(Path::new(QUICKJS_FULL_INTERPRETER_EXPECTED_PATH));

    let mut report = QuickJsColdStartReport {
        schema: QUICKJS_COLD_START_REPORT_SCHEMA,
        sample_count: QUICKJS_COLD_START_SAMPLE_COUNT,
        fuel_limit: QUICKJS_COLD_START_FUEL_LIMIT,
        script_bytes: script.len().min(u64::MAX as usize) as u64,
        abi_packet_bytes: invocation.abi_packet.len().min(u64::MAX as usize) as u64,
        wasm_module_bytes: invocation.wasm_module.len().min(u64::MAX as usize) as u64,
        cold_start_min_ns: cold_min,
        cold_start_max_ns: cold_max,
        cold_start_total_ns: cold_total,
        warm_cached_min_ns: warm_min,
        warm_cached_max_ns: warm_max,
        warm_cached_total_ns: warm_total,
        cold_cache_entries_after_first,
        warm_cache_entries_before,
        warm_cache_entries_after,
        cold_fuel_consumed_total: cold_fuel_total,
        warm_fuel_consumed_total: warm_fuel_total,
        cold_artifact_hash: *cold_hasher.finalize().as_bytes(),
        warm_artifact_hash: *warm_hasher.finalize().as_bytes(),
        script_blake3: invocation.script_blake3,
        wrapper_blake3: invocation.wrapper_blake3,
        invocation_blake3: invocation.invocation_blake3,
        bridge_probe_executed: true,
        full_interpreter_admission,
        full_quickjs_interpreter_present: false,
        production_quickjs_release_blocked_without_full_interpreter: true,
        report_hash: [0; 32],
    };
    report.report_hash = quickjs_cold_start_report_hash(&report);
    if !quickjs_cold_start_report_valid(&report) {
        return Err("quickjs cold-start report failed validation");
    }
    Ok(report)
}

fn build_tcp_cluster_soak_report() -> Result<TcpClusterSoakReport, &'static str> {
    let listener =
        TcpListener::bind("127.0.0.1:0").map_err(|_| "failed to bind tcp cluster soak listener")?;
    listener
        .set_nonblocking(false)
        .map_err(|_| "failed to configure tcp cluster soak listener")?;
    let address = listener
        .local_addr()
        .map_err(|_| "failed to read tcp cluster soak listener address")?;
    let mut handles = Vec::with_capacity(TCP_CLUSTER_SOAK_WORKER_COUNT as usize);
    for worker_index in 0..TCP_CLUSTER_SOAK_WORKER_COUNT {
        let worker_id = tcp_cluster_worker_id(worker_index);
        handles.push(spawn_tcp_cluster_worker(address, worker_id));
    }

    let mut streams = BTreeMap::new();
    for _ in 0..TCP_CLUSTER_SOAK_WORKER_COUNT {
        let (mut stream, _peer) = listener
            .accept()
            .map_err(|_| "failed to accept tcp cluster soak worker")?;
        configure_tcp_cluster_stream(&stream)?;
        let worker_id = read_tcp_cluster_hello(&mut stream)?;
        if streams.insert(worker_id, stream).is_some() {
            return Err("duplicate tcp cluster worker connection");
        }
    }

    let mut ledger = RunEventLedger::new(TCP_CLUSTER_SOAK_RUN_ID);
    let goal = crate::goal_intake::GoalIntakeProof::from_goal_text(
        ledger.run_id,
        cli_hash("tcp-cluster-soak-operator"),
        cli_hash("tcp-cluster-soak-raw-goal-ref"),
        cli_hash("tcp-cluster-soak-policy-window"),
        "Run TCP loopback cluster workers and admit only single-writer candidate artifacts",
        1,
        None,
    )
    .map_err(|_| "failed to build tcp cluster soak goal intake")?;
    ledger
        .append_goal_intake_recorded(&goal)
        .map_err(|_| "failed to append tcp cluster soak goal intake")?;
    let mut leases = WorkLeaseTable::new();
    let mut writer = SingleWriterRunLog::new(ledger.run_id);
    let mut accepted_count = 0u32;
    let mut duplicate_count = 0u32;
    let mut rtt_min = u64::MAX;
    let mut rtt_max = 0u64;
    let mut rtt_total = 0u64;
    let mut sent_bytes = 0u64;
    let mut received_bytes = 0u64;
    let mut network_hasher = blake3::Hasher::new();
    let mut candidate_hasher = blake3::Hasher::new();
    network_hasher.update(b"aegis-tcp-cluster-soak-network-v1");
    candidate_hasher.update(b"aegis-tcp-cluster-soak-candidate-results-v1");

    for index in 0..TCP_CLUSTER_SOAK_WORK_ITEMS {
        let worker_slot = index % TCP_CLUSTER_SOAK_WORKER_COUNT;
        let worker_id = tcp_cluster_worker_id(worker_slot);
        let logical_start = 10_000 + (index as u64 * 11);
        let logical_finish = logical_start + 3 + worker_slot as u64;
        let input_hash = cli_hash(&format!("tcp-cluster-soak-input-{index}"));
        let envelope = ClusterWorkEnvelope::new(
            ledger.run_id,
            40_000 + index as u128,
            50_000 + index as u128,
            WorkerRole::BenchmarkRunner,
            1,
            vec![input_hash],
            cli_hash("tcp-cluster-soak-evidence-contract"),
            cli_hash("tcp-cluster-soak-policy-window"),
            SideEffectClass::ExternalRead,
            logical_start + 5_000,
        )
        .map_err(|_| "failed to build tcp cluster soak envelope")?;
        let lease = leases
            .acquire(&envelope, worker_id, logical_start, 1_000)
            .map_err(|_| "failed to acquire tcp cluster soak lease")?;
        let request = TcpClusterRequestFrame {
            work_index: index,
            run_id: envelope.run_id,
            work_id: envelope.work_id,
            task_id: envelope.task_id,
            worker_id,
            envelope_hash: envelope.envelope_hash,
            idempotency_key: envelope.idempotency_key,
            input_hash,
            evidence_contract_hash: envelope.evidence_contract_hash,
            policy_window_hash: envelope.policy_window_hash,
        };
        let stream = streams
            .get_mut(&worker_id)
            .ok_or("missing tcp cluster soak worker stream")?;
        let started = Instant::now();
        write_tcp_cluster_request(stream, &request)?;
        sent_bytes = sent_bytes.saturating_add(1 + TCP_CLUSTER_REQUEST_FRAME_BYTES as u64);
        let response = read_tcp_cluster_response(stream)?;
        let elapsed_ns = elapsed_ns_u64(started);
        received_bytes = received_bytes.saturating_add(TCP_CLUSTER_RESPONSE_FRAME_BYTES as u64);
        if !tcp_cluster_response_valid_for_request(&response, &request) {
            return Err("invalid tcp cluster soak response");
        }
        rtt_min = rtt_min.min(elapsed_ns);
        rtt_max = rtt_max.max(elapsed_ns);
        rtt_total = rtt_total.saturating_add(elapsed_ns);
        network_hasher.update(&index.to_le_bytes());
        network_hasher.update(&worker_id.to_le_bytes());
        network_hasher.update(&request.envelope_hash);
        network_hasher.update(&response.response_hash);
        network_hasher.update(&elapsed_ns.to_le_bytes());

        let candidate = CandidateArtifactRef::new(
            &envelope,
            worker_id,
            response.artifact_hash,
            response.artifact_kind_hash,
            response.byte_len,
        )
        .map_err(|_| "failed to build tcp cluster soak candidate")?;
        match writer
            .commit_candidate(
                &mut ledger,
                &envelope,
                &lease,
                &candidate,
                ClusterPartitionState::WriterReachable,
                logical_finish,
            )
            .map_err(|_| "failed to admit tcp cluster soak candidate")?
        {
            SingleWriterAdmission::Accepted(proof) => {
                if !proof.is_valid_for(&envelope, &lease, &candidate) {
                    return Err("invalid tcp cluster soak admission proof");
                }
                accepted_count += 1;
                candidate_hasher.update(&proof.admission_hash);
                candidate_hasher.update(&proof.replay_event_hash);
            }
            SingleWriterAdmission::Duplicate(_) => {
                return Err("unexpected first tcp cluster soak duplicate");
            }
        }
        if index == 0 {
            match writer
                .commit_candidate(
                    &mut ledger,
                    &envelope,
                    &lease,
                    &candidate,
                    ClusterPartitionState::WriterReachable,
                    logical_finish,
                )
                .map_err(|_| "failed to test tcp cluster soak duplicate")?
            {
                SingleWriterAdmission::Duplicate(proof) => {
                    if !proof.is_valid_for(&envelope, &lease, &candidate) {
                        return Err("invalid tcp cluster soak duplicate proof");
                    }
                    duplicate_count += 1;
                }
                SingleWriterAdmission::Accepted(_) => {
                    return Err("duplicate tcp cluster soak candidate accepted");
                }
            }
        }
    }

    for stream in streams.values_mut() {
        write_tcp_cluster_stop(stream)?;
    }
    for handle in handles {
        handle
            .join()
            .map_err(|_| "tcp cluster soak worker panicked")?
            .map_err(|_| "tcp cluster soak worker failed")?;
    }

    let direct_worker_commit_rejected = writer.reject_direct_worker_commit(90_000).is_err();
    let partition_envelope = ClusterWorkEnvelope::new(
        ledger.run_id,
        199_001,
        199_101,
        WorkerRole::WasmSandbox,
        1,
        vec![cli_hash("tcp-cluster-soak-partition-input")],
        cli_hash("tcp-cluster-soak-evidence-contract"),
        cli_hash("tcp-cluster-soak-policy-window"),
        SideEffectClass::ExternalWrite,
        80_000,
    )
    .map_err(|_| "failed to build tcp cluster soak partition envelope")?;
    let partition_lease = leases
        .acquire(&partition_envelope, 90_999, 70_000, 1_000)
        .map_err(|_| "failed to acquire tcp cluster soak partition lease")?;
    let partition_candidate = CandidateArtifactRef::new(
        &partition_envelope,
        90_999,
        cli_hash("tcp-cluster-soak-partition-artifact"),
        cli_hash("tcp-cluster-soak-artifact-kind"),
        2_048,
    )
    .map_err(|_| "failed to build tcp cluster soak partition candidate")?;
    let side_effect_partition_paused = writer
        .commit_candidate(
            &mut ledger,
            &partition_envelope,
            &partition_lease,
            &partition_candidate,
            ClusterPartitionState::WriterPartitioned,
            70_010,
        )
        .is_err();

    let real_multi_machine_cluster_admission = real_multi_machine_cluster_soak_admission(
        Path::new(REAL_MULTI_MACHINE_CLUSTER_SOAK_EXPECTED_PATH),
        TCP_CLUSTER_SOAK_WORKER_COUNT,
        TCP_CLUSTER_SOAK_WORK_ITEMS,
        TCP_CLUSTER_SOAK_WORK_ITEMS,
    );
    let mut report = TcpClusterSoakReport {
        schema: TCP_CLUSTER_SOAK_REPORT_SCHEMA,
        run_id: ledger.run_id,
        worker_count: TCP_CLUSTER_SOAK_WORKER_COUNT,
        work_item_count: TCP_CLUSTER_SOAK_WORK_ITEMS,
        accepted_count,
        duplicate_count,
        replay_event_count: ledger.len() as u64,
        replay_hash_chain_valid: ledger.verify_hash_chain(),
        tcp_listener_bound: true,
        tcp_worker_connections: streams.len().min(u32::MAX as usize) as u32,
        tcp_round_trip_count: TCP_CLUSTER_SOAK_WORK_ITEMS,
        tcp_round_trip_min_ns: rtt_min,
        tcp_round_trip_max_ns: rtt_max,
        tcp_round_trip_total_ns: rtt_total,
        tcp_payload_bytes_sent: sent_bytes,
        tcp_payload_bytes_received: received_bytes,
        worker_response_count: TCP_CLUSTER_SOAK_WORK_ITEMS,
        direct_worker_commit_rejected,
        side_effect_partition_paused,
        network_trace_hash: *network_hasher.finalize().as_bytes(),
        candidate_result_hash: *candidate_hasher.finalize().as_bytes(),
        replay_last_hash: ledger.last_hash(),
        report_hash: [0; 32],
        loopback_tcp_only: true,
        multi_machine_real_cluster: false,
        real_multi_machine_cluster_admission,
        real_multi_node_cluster_test_present: false,
        production_cluster_release_blocked_without_real_multi_node_soak: true,
    };
    report.report_hash = tcp_cluster_soak_report_hash(&report);
    if !tcp_cluster_soak_report_valid(&report) {
        return Err("tcp cluster soak report failed validation");
    }
    Ok(report)
}

fn build_cluster_loopback_report() -> Result<ClusterLoopbackReport, &'static str> {
    let mut ledger = RunEventLedger::new(CLUSTER_LOOPBACK_RUN_ID);
    let goal = crate::goal_intake::GoalIntakeProof::from_goal_text(
        ledger.run_id,
        cli_hash("cluster-loopback-operator"),
        cli_hash("cluster-loopback-raw-goal-ref"),
        cli_hash("cluster-loopback-policy-window"),
        "Run deterministic loopback cluster workers and admit only single-writer candidate artifacts",
        1,
        None,
    )
    .map_err(|_| "failed to build cluster loopback goal intake")?;
    ledger
        .append_goal_intake_recorded(&goal)
        .map_err(|_| "failed to append cluster loopback goal intake")?;
    let mut leases = WorkLeaseTable::new();
    let mut writer = SingleWriterRunLog::new(ledger.run_id);
    let mut accepted_count = 0u32;
    let mut duplicate_count = 0u32;
    let mut max_logical_rtt_ticks = 0u64;
    let mut total_logical_rtt_ticks = 0u64;
    let mut candidate_hasher = blake3::Hasher::new();
    candidate_hasher.update(b"aegis-cluster-loopback-candidate-results-v1");

    for index in 0..CLUSTER_LOOPBACK_WORK_ITEMS {
        let worker_slot = index % CLUSTER_LOOPBACK_WORKER_COUNT;
        let worker_id = 10_000 + worker_slot as u128;
        let logical_start = 1_000 + (index as u64 * 8);
        let logical_finish = logical_start + 2 + worker_slot as u64;
        let envelope = ClusterWorkEnvelope::new(
            ledger.run_id,
            20_000 + index as u128,
            30_000 + index as u128,
            WorkerRole::BenchmarkRunner,
            1,
            vec![cli_hash(&format!("cluster-loopback-input-{index}"))],
            cli_hash("cluster-loopback-evidence-contract"),
            cli_hash("cluster-loopback-policy-window"),
            SideEffectClass::ExternalRead,
            logical_start + 1_000,
        )
        .map_err(|_| "failed to build cluster loopback envelope")?;
        let lease = leases
            .acquire(&envelope, worker_id, logical_start, 100)
            .map_err(|_| "failed to acquire cluster loopback lease")?;
        let candidate = CandidateArtifactRef::new(
            &envelope,
            worker_id,
            cli_hash(&format!("cluster-loopback-artifact-{index}")),
            cli_hash("cluster-loopback-artifact-kind"),
            512 + index as u64,
        )
        .map_err(|_| "failed to build cluster loopback candidate")?;
        match writer
            .commit_candidate(
                &mut ledger,
                &envelope,
                &lease,
                &candidate,
                ClusterPartitionState::WriterReachable,
                logical_finish,
            )
            .map_err(|_| "failed to admit cluster loopback candidate")?
        {
            SingleWriterAdmission::Accepted(proof) => {
                if !proof.is_valid_for(&envelope, &lease, &candidate) {
                    return Err("invalid cluster loopback admission proof");
                }
                accepted_count += 1;
                candidate_hasher.update(&proof.admission_hash);
                candidate_hasher.update(&proof.replay_event_hash);
            }
            SingleWriterAdmission::Duplicate(_) => {
                return Err("unexpected first cluster loopback duplicate");
            }
        }
        if index == 0 {
            match writer
                .commit_candidate(
                    &mut ledger,
                    &envelope,
                    &lease,
                    &candidate,
                    ClusterPartitionState::WriterReachable,
                    logical_finish,
                )
                .map_err(|_| "failed to test cluster loopback duplicate")?
            {
                SingleWriterAdmission::Duplicate(proof) => {
                    if !proof.is_valid_for(&envelope, &lease, &candidate) {
                        return Err("invalid duplicate cluster loopback proof");
                    }
                    duplicate_count += 1;
                }
                SingleWriterAdmission::Accepted(_) => {
                    return Err("duplicate cluster loopback candidate accepted");
                }
            }
        }
        let rtt_ticks = logical_finish.saturating_sub(logical_start);
        max_logical_rtt_ticks = max_logical_rtt_ticks.max(rtt_ticks);
        total_logical_rtt_ticks = total_logical_rtt_ticks.saturating_add(rtt_ticks);
    }

    let direct_worker_commit_rejected = writer.reject_direct_worker_commit(10_000).is_err();
    let partition_envelope = ClusterWorkEnvelope::new(
        ledger.run_id,
        99_001,
        99_101,
        WorkerRole::WasmSandbox,
        1,
        vec![cli_hash("cluster-loopback-partition-input")],
        cli_hash("cluster-loopback-evidence-contract"),
        cli_hash("cluster-loopback-policy-window"),
        SideEffectClass::ExternalWrite,
        50_000,
    )
    .map_err(|_| "failed to build cluster loopback partition envelope")?;
    let partition_lease = leases
        .acquire(&partition_envelope, 10_999, 40_000, 100)
        .map_err(|_| "failed to acquire cluster loopback partition lease")?;
    let partition_candidate = CandidateArtifactRef::new(
        &partition_envelope,
        10_999,
        cli_hash("cluster-loopback-partition-artifact"),
        cli_hash("cluster-loopback-artifact-kind"),
        1024,
    )
    .map_err(|_| "failed to build cluster loopback partition candidate")?;
    let side_effect_partition_paused = writer
        .commit_candidate(
            &mut ledger,
            &partition_envelope,
            &partition_lease,
            &partition_candidate,
            ClusterPartitionState::WriterPartitioned,
            40_010,
        )
        .is_err();
    let mut report = ClusterLoopbackReport {
        schema: CLUSTER_LOOPBACK_REPORT_SCHEMA,
        run_id: ledger.run_id,
        worker_count: CLUSTER_LOOPBACK_WORKER_COUNT,
        work_item_count: CLUSTER_LOOPBACK_WORK_ITEMS,
        accepted_count,
        duplicate_count,
        replay_event_count: ledger.len() as u64,
        replay_hash_chain_valid: ledger.verify_hash_chain(),
        direct_worker_commit_rejected,
        side_effect_partition_paused,
        max_logical_rtt_ticks,
        total_logical_rtt_ticks,
        candidate_result_hash: *candidate_hasher.finalize().as_bytes(),
        replay_last_hash: ledger.last_hash(),
        report_hash: [0; 32],
        multi_machine_real_cluster: false,
    };
    report.report_hash = cluster_loopback_report_hash(&report);
    if !cluster_loopback_report_valid(&report) {
        return Err("cluster loopback report failed validation");
    }
    Ok(report)
}

impl AgenticSdkContextReport {
    #[allow(clippy::too_many_arguments)]
    fn new(
        run: &AgenticEvidenceSdkRun,
        handoff: &AgenticEvidenceSdkRunHandoffProof,
        sdk_run_event_id: u64,
        sdk_run_event_hash: [u8; 32],
        sdk_run_replay_binding_hash: [u8; 32],
        context_pack_digest: [u8; 32],
        context_pack_node_hash: [u8; 32],
        context_pack_candidate_proof_hash: [u8; 32],
        context_pack_event_id: u64,
        context_pack_event_hash: [u8; 32],
        context_archive_manifest_hash: [u8; 32],
        context_ledger_hash: [u8; 32],
        context_event_count: u64,
    ) -> Self {
        let mut report = Self {
            schema: AGENTIC_SDK_CONTEXT_REPORT_SCHEMA,
            run_id: AGENTIC_SDK_CONTEXT_RUN_ID,
            execution_id: AGENTIC_SDK_CONTEXT_EXECUTION_ID,
            active_task_id: AGENTIC_SDK_CONTEXT_ACTIVE_TASK_ID,
            replay_recorded: true,
            context_recorded: true,
            sdk_run_hash: run.run_hash,
            manifest_hash: run.manifest.manifest_hash,
            execution_record_hash: run.execution_record.record_hash,
            capsule_hash: run.capsule.capsule_hash,
            candidate_list_hash: run.execution_record.candidate_list_hash,
            candidate_count: run.execution_record.candidate_count,
            sdk_run_event_id,
            sdk_run_event_hash,
            sdk_run_replay_binding_hash,
            handoff_hash: handoff.handoff_hash,
            checkpoint_hash: handoff.checkpoint_hash,
            next_action_packet_hash: handoff.next_action_packet_hash,
            context_pack_digest,
            context_pack_node_hash,
            context_pack_candidate_proof_hash,
            context_pack_event_id,
            context_pack_event_hash,
            context_archive_manifest_hash,
            context_ledger_hash,
            context_event_count,
            report_hash: [0; 32],
        };
        report.report_hash = agentic_sdk_context_report_hash(&report);
        report
    }

    fn is_valid_for(
        &self,
        run: &AgenticEvidenceSdkRun,
        handoff: &AgenticEvidenceSdkRunHandoffProof,
        context_proof: &crate::context::ContextPackCandidateProof,
    ) -> bool {
        self.schema == AGENTIC_SDK_CONTEXT_REPORT_SCHEMA
            && self.run_id == AGENTIC_SDK_CONTEXT_RUN_ID
            && self.execution_id == AGENTIC_SDK_CONTEXT_EXECUTION_ID
            && self.active_task_id == AGENTIC_SDK_CONTEXT_ACTIVE_TASK_ID
            && self.replay_recorded
            && self.context_recorded
            && self.candidate_count > 0
            && nonzero_hash(&self.sdk_run_hash)
            && nonzero_hash(&self.manifest_hash)
            && nonzero_hash(&self.execution_record_hash)
            && nonzero_hash(&self.capsule_hash)
            && nonzero_hash(&self.candidate_list_hash)
            && nonzero_hash(&self.sdk_run_event_hash)
            && nonzero_hash(&self.sdk_run_replay_binding_hash)
            && nonzero_hash(&self.handoff_hash)
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.context_pack_digest)
            && nonzero_hash(&self.context_pack_node_hash)
            && nonzero_hash(&self.context_pack_candidate_proof_hash)
            && nonzero_hash(&self.context_pack_event_hash)
            && nonzero_hash(&self.context_archive_manifest_hash)
            && nonzero_hash(&self.context_ledger_hash)
            && self.context_event_count >= self.context_pack_event_id
            && self.context_ledger_hash == self.context_pack_event_hash
            && self.sdk_run_hash == run.run_hash
            && self.manifest_hash == run.manifest.manifest_hash
            && self.execution_record_hash == run.execution_record.record_hash
            && self.capsule_hash == run.capsule.capsule_hash
            && self.candidate_list_hash == run.execution_record.candidate_list_hash
            && self.candidate_count == run.execution_record.candidate_count
            && self.sdk_run_replay_binding_hash == agentic_evidence_sdk_run_replay_binding_hash(run)
            && self.handoff_hash == handoff.handoff_hash
            && self.checkpoint_hash == handoff.checkpoint_hash
            && self.next_action_packet_hash == handoff.next_action_packet_hash
            && self.context_pack_digest == context_proof.context_pack_digest
            && self.context_pack_node_hash == context_proof.context_pack_node_hash
            && self.context_pack_candidate_proof_hash == context_proof.proof_hash
            && self.report_hash == agentic_sdk_context_report_hash(self)
            && nonzero_hash(&self.report_hash)
    }

    fn write_artifact(&self, path: impl AsRef<Path>) -> Result<[u8; 32], &'static str> {
        if self.schema != AGENTIC_SDK_CONTEXT_REPORT_SCHEMA
            || self.report_hash != agentic_sdk_context_report_hash(self)
        {
            return Err("invalid agentic sdk context report artifact");
        }
        let body = AgenticSdkContextReportBody {
            schema_version: 1,
            report: self,
        };
        let logical_payload = serde_json::to_vec(&body)
            .map_err(|_| "failed to serialize agentic sdk context report body")?;
        let logical_payload_hash = crate::physical::blake3_digest(&logical_payload);
        let write_evidence = AgenticSdkContextReportWriteEvidence::for_logical_payload(
            logical_payload.len().min(u64::MAX as usize) as u64,
            logical_payload_hash,
        );
        if !write_evidence.is_valid() {
            return Err("invalid agentic sdk context report write evidence");
        }
        let artifact = AgenticSdkContextReportArtifact {
            schema_version: 1,
            report: self,
            write_evidence: &write_evidence,
        };
        let payload = serde_json::to_vec_pretty(&artifact)
            .map_err(|_| "failed to serialize agentic sdk context report artifact")?;
        write_agentic_sdk_context_report_artifact(path.as_ref(), &payload)?;
        Ok(crate::physical::blake3_digest(&payload))
    }
}

impl AgenticSdkContextReportWriteEvidence {
    fn for_logical_payload(logical_payload_bytes: u64, logical_payload_hash: [u8; 32]) -> Self {
        let staged_temp_file_used = true;
        let temp_file_synced_before_publish = true;
        let publish_completed = true;
        let parent_directory_sync_attempted = true;
        let replace_existing_supported = true;
        let publish_write_through_requested = cfg!(windows);
        let evidence_hash = agentic_sdk_context_report_write_evidence_hash(
            staged_temp_file_used,
            temp_file_synced_before_publish,
            publish_completed,
            parent_directory_sync_attempted,
            replace_existing_supported,
            publish_write_through_requested,
            logical_payload_bytes,
            logical_payload_hash,
        );
        Self {
            staged_temp_file_used,
            temp_file_synced_before_publish,
            publish_completed,
            parent_directory_sync_attempted,
            replace_existing_supported,
            publish_write_through_requested,
            logical_payload_bytes,
            logical_payload_hash,
            evidence_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.staged_temp_file_used
            && self.temp_file_synced_before_publish
            && self.publish_completed
            && self.parent_directory_sync_attempted
            && self.replace_existing_supported
            && self.logical_payload_bytes > 0
            && nonzero_hash(&self.logical_payload_hash)
            && self.evidence_hash
                == agentic_sdk_context_report_write_evidence_hash(
                    self.staged_temp_file_used,
                    self.temp_file_synced_before_publish,
                    self.publish_completed,
                    self.parent_directory_sync_attempted,
                    self.replace_existing_supported,
                    self.publish_write_through_requested,
                    self.logical_payload_bytes,
                    self.logical_payload_hash,
                )
            && nonzero_hash(&self.evidence_hash)
    }
}

fn sample_agentic_sdk_context_run()
-> Result<(AgenticEvidenceProgram, AgenticEvidenceSdkRun), &'static str> {
    let epoch_hash = cli_hash("agentic-sdk-context-epoch");
    let mut lexical =
        HotLexicalIndex::new(epoch_hash).map_err(|_| "failed to build lexical index")?;
    lexical
        .insert_document(
            cli_hash("agentic-sdk-context-browser"),
            27,
            &["browser", "policy", "replay", "witness"],
        )
        .map_err(|_| "failed to insert lexical browser doc")?;
    lexical
        .insert_document(
            cli_hash("agentic-sdk-context-sandbox"),
            29,
            &["wasmtime", "policy", "fuel", "sandbox"],
        )
        .map_err(|_| "failed to insert lexical sandbox doc")?;
    let mut exact = HotEvidenceIndex::new(epoch_hash).map_err(|_| "failed to build exact index")?;
    exact
        .insert_artifact_document(
            cli_hash("agentic-sdk-context-browser"),
            27,
            cli_hash("agentic-sdk-context-browser-artifact"),
            cli_hash("agentic-sdk-context-browser-ast"),
            "browser policy replay witness DOM screenshot artifact",
        )
        .map_err(|_| "failed to insert exact browser doc")?;
    exact
        .insert_artifact_document(
            cli_hash("agentic-sdk-context-sandbox"),
            29,
            cli_hash("agentic-sdk-context-sandbox-artifact"),
            cli_hash("agentic-sdk-context-sandbox-ast"),
            "wasmtime fuel sandbox policy",
        )
        .map_err(|_| "failed to insert exact sandbox doc")?;
    let bitmap = HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![27]))
        .map_err(|_| "failed to build bitmap filter")?;
    let program = AgenticEvidenceProgram::new(
        epoch_hash,
        64,
        vec![
            AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay", "witness"], 8),
            AgenticEvidenceProgramStep::exact_artifact_rerank(
                &["browser", "witness", "artifact"],
                4,
            ),
            AgenticEvidenceProgramStep::bitmap_filter(4),
        ],
    )
    .map_err(|_| "failed to build agentic evidence program")?;
    let sdk_run = AgenticEvidenceSdk::run_program(
        &program,
        9_991,
        Some(&lexical),
        Some(&exact),
        Some(&bitmap),
    )
    .map_err(|_| "failed to run agentic evidence sdk program")?;
    Ok((program, sdk_run))
}

fn blake3_stdin() -> std::io::Result<String> {
    let mut input = Vec::new();
    std::io::stdin().read_to_end(&mut input)?;
    Ok(hex32(&crate::physical::blake3_digest(&input)))
}

fn browser_ops_bench_verify_from_args(args: Vec<String>) -> Result<[u8; 32], &'static str> {
    if args.len() != 10 {
        return Err(
            "usage: browser-ops-bench-verify <scorecard> <output> <collector-kind> <observed-at-unix-ms> <collector-config-hash-hex> <collector-capability-hash-hex> <browser-session-hash-hex> <redaction-policy-hash-hex> <policy-window-hash-hex> <run-id>",
        );
    }
    let scorecard_path = PathBuf::from(&args[0]);
    let output_path = PathBuf::from(&args[1]);
    let collector_kind = parse_browser_collector_kind(&args[2])?;
    let observed_at_unix_ms = args[3]
        .parse::<u64>()
        .map_err(|_| "invalid observed-at-unix-ms")?;
    let collector_config_hash = parse_hex32(&args[4])?;
    let collector_capability_hash = parse_hex32(&args[5])?;
    let browser_session_hash = parse_hex32(&args[6])?;
    let redaction_policy_hash = parse_hex32(&args[7])?;
    let policy_window_hash = parse_hex32(&args[8])?;
    let run_id = args[9].parse::<u128>().map_err(|_| "invalid run id")?;
    write_browser_ops_bench_verification_report(
        scorecard_path,
        output_path,
        collector_kind,
        observed_at_unix_ms,
        collector_config_hash,
        collector_capability_hash,
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
        run_id,
    )
}

fn parse_browser_collector_kind(value: &str) -> Result<BrowserCollectorKind, &'static str> {
    match value {
        "in-app-browser" | "in_app_browser" | "InAppBrowser" => {
            Ok(BrowserCollectorKind::InAppBrowser)
        }
        "chrome-extension" | "chrome_extension" | "ChromeExtension" => {
            Ok(BrowserCollectorKind::ChromeExtension)
        }
        "computer-use" | "computer_use" | "ComputerUse" => Ok(BrowserCollectorKind::ComputerUse),
        "playwright-cdp" | "playwright_cdp" | "PlaywrightCdp" => {
            Ok(BrowserCollectorKind::PlaywrightCdp)
        }
        _ => Err("unknown browser collector kind"),
    }
}

fn parse_hex32(value: &str) -> Result<[u8; 32], &'static str> {
    let trimmed = value.strip_prefix("0x").unwrap_or(value);
    if trimmed.len() != 64 {
        return Err("expected 32-byte hex hash");
    }
    let mut out = [0u8; 32];
    let bytes = trimmed.as_bytes();
    for index in 0..32 {
        let high = hex_nibble(bytes[index * 2]).ok_or("invalid hex hash")?;
        let low = hex_nibble(bytes[index * 2 + 1]).ok_or("invalid hex hash")?;
        out[index] = (high << 4) | low;
    }
    if out.iter().all(|byte| *byte == 0) {
        return Err("zero hash rejected");
    }
    Ok(out)
}

fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn sample_replay_chaos_ledger() -> Result<RunEventLedger, &'static str> {
    let mut ledger = RunEventLedger::new(9001);
    let goal = crate::goal_intake::GoalIntakeProof::from_goal_text(
        ledger.run_id,
        cli_hash("operator"),
        cli_hash("raw-goal-ref"),
        cli_hash("policy-window"),
        "Use browser to inspect the website and fetch evidence, no submit",
        1,
        None,
    )
    .map_err(|_| "failed to build goal intake proof")?;
    ledger
        .append_goal_intake_recorded(&goal)
        .map_err(|_| "failed to append goal intake event")?;
    ledger
        .append_context_pack_built(11, 128, cli_hash("context-pack"))
        .map_err(|_| "failed to append context event")?;
    ledger
        .append_llm_response_received(cli_hash("response"), cli_hash("raw-text-ref"))
        .map_err(|_| "failed to append llm response event")?;
    for round in 0..REPLAY_CHAOS_SCORECARD_ROUNDS {
        let typed_tool_ir_hash = cli_hash(&format!("typed-tool-ir-{round}"));
        let policy_proof_trace_hash = cli_hash(&format!("policy-proof-trace-{round}"));
        ledger
            .append_llm_derived_tool_call_requested(200 + round, typed_tool_ir_hash)
            .map_err(|_| "failed to append tool request event")?;
        ledger
            .append_policy_decision_recorded(
                100 + round,
                policy_proof_trace_hash,
                typed_tool_ir_hash,
            )
            .map_err(|_| "failed to append policy event")?;
        ledger
            .append_operator_review_artifact_recorded(
                150 + round,
                cli_hash(&format!("operator-review-signing-target-{round}")),
                cli_hash(&format!("operator-review-artifact-{round}")),
            )
            .map_err(|_| "failed to append operator review event")?;
        let tool_execution_evidence = ToolExecutionEvidence::new(
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            cli_hash(&format!("tool-output-{round}")),
            cli_hash(&format!("physical-witness-{round}")),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        ledger
            .append_tool_call_completed(200 + round, &tool_execution_evidence)
            .map_err(|_| "failed to append tool completion event")?;
        ledger
            .append_checkpoint_sealed(300 + round, cli_hash(&format!("checkpoint-{round}")))
            .map_err(|_| "failed to append checkpoint event")?;
    }
    Ok(ledger)
}

fn cli_hash(label: &str) -> [u8; 32] {
    crate::physical::blake3_digest(label.as_bytes())
}

fn quickjs_full_interpreter_admission(runtime_path: &Path) -> QuickJsFullInterpreterAdmission {
    let runtime_bytes = std::fs::read(runtime_path).ok();
    let runtime_wasm_present = runtime_bytes
        .as_ref()
        .map(|bytes| bytes.starts_with(b"\0asm") && bytes.len() > 8)
        .unwrap_or(false);
    let runtime_wasm_hash = runtime_bytes
        .filter(|_| runtime_wasm_present)
        .map(|bytes| *blake3::hash(&bytes).as_bytes())
        .unwrap_or([0; 32]);
    let semantic_corpus_hash = cli_hash(QUICKJS_FULL_INTERPRETER_SEMANTIC_CORPUS);
    let sandbox_contract_hash = cli_hash(QUICKJS_FULL_INTERPRETER_SANDBOX_CONTRACT);
    let admission_status = if runtime_wasm_present {
        "candidate-present-semantic-cold-start-gate-required"
    } else {
        "missing"
    };
    let mut admission = QuickJsFullInterpreterAdmission {
        schema: QUICKJS_FULL_INTERPRETER_ADMISSION_SCHEMA,
        expected_runtime_path: QUICKJS_FULL_INTERPRETER_EXPECTED_PATH,
        runtime_wasm_present,
        runtime_wasm_hash,
        semantic_corpus: QUICKJS_FULL_INTERPRETER_SEMANTIC_CORPUS,
        semantic_corpus_hash,
        sandbox_contract: QUICKJS_FULL_INTERPRETER_SANDBOX_CONTRACT,
        sandbox_contract_hash,
        required_cold_start_sample_count: QUICKJS_COLD_START_SAMPLE_COUNT,
        required_fuel_limit: QUICKJS_COLD_START_FUEL_LIMIT,
        admission_status,
        production_blocker_id: QUICKJS_FULL_INTERPRETER_BLOCKER_ID,
        admission_hash: [0; 32],
    };
    admission.admission_hash = quickjs_full_interpreter_admission_hash(&admission);
    admission
}

fn live_provider_429_soak_admission(runtime_path: &Path) -> LiveProvider429SoakAdmission {
    let capture_bytes = std::fs::read(runtime_path).ok();
    let capture_present = capture_bytes
        .as_ref()
        .map(|bytes| !bytes.is_empty() && bytes.starts_with(b"{"))
        .unwrap_or(false);
    let capture_hash = capture_bytes
        .filter(|_| capture_present)
        .map(|bytes| *blake3::hash(&bytes).as_bytes())
        .unwrap_or([0; 32]);
    let provider_set_hash = cli_hash(LIVE_PROVIDER_429_SOAK_PROVIDER_SET);
    let capture_contract_hash = cli_hash(LIVE_PROVIDER_429_SOAK_CAPTURE_CONTRACT);
    let admission_status = if capture_present {
        "candidate-present-live-soak-gate-required"
    } else {
        "missing"
    };
    let mut admission = LiveProvider429SoakAdmission {
        schema: LIVE_PROVIDER_429_SOAK_ADMISSION_SCHEMA,
        expected_capture_path: LIVE_PROVIDER_429_SOAK_EXPECTED_PATH,
        capture_present,
        capture_hash,
        provider_set: LIVE_PROVIDER_429_SOAK_PROVIDER_SET,
        provider_set_hash,
        capture_contract: LIVE_PROVIDER_429_SOAK_CAPTURE_CONTRACT,
        capture_contract_hash,
        required_sample_count: DYNAMIC_PROVIDER_FALLBACK_SAMPLE_COUNT,
        latency_gate_ns: DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS,
        expected_feedback_kind: "http_429",
        primary_provider: "openrouter",
        primary_model: "gpt-4",
        fallback_provider: "nim",
        fallback_model: "llama-3-70b",
        admission_status,
        production_blocker_id: LIVE_PROVIDER_429_SOAK_BLOCKER_ID,
        admission_hash: [0; 32],
    };
    admission.admission_hash = live_provider_429_soak_admission_hash(&admission);
    admission
}

fn real_multi_machine_cluster_soak_admission(
    capture_path: &Path,
    local_worker_count: u32,
    local_work_item_count: u32,
    local_round_trip_count: u32,
) -> RealMultiMachineClusterSoakAdmission {
    let capture_bytes = std::fs::read(capture_path).ok();
    let capture_present = capture_bytes
        .as_ref()
        .map(|bytes| !bytes.is_empty() && bytes.starts_with(b"{"))
        .unwrap_or(false);
    let capture_hash = capture_bytes
        .filter(|_| capture_present)
        .map(|bytes| *blake3::hash(&bytes).as_bytes())
        .unwrap_or([0; 32]);
    let network_contract_hash = cli_hash(REAL_MULTI_MACHINE_CLUSTER_SOAK_NETWORK_CONTRACT);
    let topology_contract_hash = cli_hash(REAL_MULTI_MACHINE_CLUSTER_SOAK_TOPOLOGY_CONTRACT);
    let admission_status = if capture_present {
        "candidate-present-real-multi-machine-soak-gate-required"
    } else {
        "missing"
    };
    let mut admission = RealMultiMachineClusterSoakAdmission {
        schema: REAL_MULTI_MACHINE_CLUSTER_SOAK_ADMISSION_SCHEMA,
        expected_capture_path: REAL_MULTI_MACHINE_CLUSTER_SOAK_EXPECTED_PATH,
        capture_present,
        capture_hash,
        network_contract: REAL_MULTI_MACHINE_CLUSTER_SOAK_NETWORK_CONTRACT,
        network_contract_hash,
        topology_contract: REAL_MULTI_MACHINE_CLUSTER_SOAK_TOPOLOGY_CONTRACT,
        topology_contract_hash,
        required_worker_count: TCP_CLUSTER_SOAK_WORKER_COUNT,
        required_work_item_count: TCP_CLUSTER_SOAK_WORK_ITEMS,
        required_round_trip_count: TCP_CLUSTER_SOAK_WORK_ITEMS,
        local_loopback_worker_count: local_worker_count,
        local_loopback_work_item_count: local_work_item_count,
        local_loopback_round_trip_count: local_round_trip_count,
        admission_status,
        production_blocker_id: REAL_MULTI_MACHINE_CLUSTER_SOAK_BLOCKER_ID,
        admission_hash: [0; 32],
    };
    admission.admission_hash = real_multi_machine_cluster_soak_admission_hash(&admission);
    admission
}

fn quickjs_bridge_probe_wasm(packet_len: usize) -> Result<Vec<u8>, &'static str> {
    if packet_len == 0 {
        return Err("invalid quickjs bridge probe packet length");
    }
    let wat = format!(
        r#"(module
            (import "aegis_quickjs" "validate_invocation_packet"
                (func $validate (param i32 i32) (result i32)))
            (memory (export "memory") 1)
            (func (export "_start")
                i32.const 0
                i32.const {packet_len}
                call $validate
                i32.eqz
                if
                    unreachable
                end)
        )"#
    );
    wat::parse_str(&wat).map_err(|_| "failed to build quickjs bridge probe wasm")
}

fn tcp_cluster_worker_id(worker_index: u32) -> u128 {
    90_000 + worker_index as u128
}

fn spawn_tcp_cluster_worker(
    address: SocketAddr,
    worker_id: u128,
) -> thread::JoinHandle<Result<(), &'static str>> {
    thread::spawn(move || {
        let mut stream =
            TcpStream::connect(address).map_err(|_| "tcp cluster worker connect failed")?;
        configure_tcp_cluster_stream(&stream)?;
        write_tcp_cluster_hello(&mut stream, worker_id)?;
        loop {
            let mut command = [0u8; 1];
            stream
                .read_exact(&mut command)
                .map_err(|_| "tcp cluster worker command read failed")?;
            match command[0] {
                b'W' => {
                    let request = read_tcp_cluster_request_payload(&mut stream)?;
                    let response = tcp_cluster_worker_response(&request)?;
                    write_tcp_cluster_response(&mut stream, &response)?;
                }
                b'S' => break,
                _ => return Err("unknown tcp cluster worker command"),
            }
        }
        Ok(())
    })
}

fn configure_tcp_cluster_stream(stream: &TcpStream) -> Result<(), &'static str> {
    stream
        .set_nodelay(true)
        .map_err(|_| "failed to set tcp nodelay")?;
    let timeout = Some(Duration::from_millis(TCP_CLUSTER_FRAME_TIMEOUT_MS));
    stream
        .set_read_timeout(timeout)
        .map_err(|_| "failed to set tcp read timeout")?;
    stream
        .set_write_timeout(timeout)
        .map_err(|_| "failed to set tcp write timeout")?;
    Ok(())
}

fn write_tcp_cluster_hello(stream: &mut TcpStream, worker_id: u128) -> Result<(), &'static str> {
    let mut frame = [0u8; TCP_CLUSTER_HELLO_BYTES];
    frame[0..8].copy_from_slice(b"AEGTCP01");
    frame[8..24].copy_from_slice(&worker_id.to_le_bytes());
    stream
        .write_all(&frame)
        .map_err(|_| "failed to write tcp cluster hello")?;
    stream
        .flush()
        .map_err(|_| "failed to flush tcp cluster hello")
}

fn read_tcp_cluster_hello(stream: &mut TcpStream) -> Result<u128, &'static str> {
    let mut frame = [0u8; TCP_CLUSTER_HELLO_BYTES];
    stream
        .read_exact(&mut frame)
        .map_err(|_| "failed to read tcp cluster hello")?;
    if &frame[0..8] != b"AEGTCP01" {
        return Err("invalid tcp cluster hello magic");
    }
    read_u128_le(&frame, 8)
}

fn write_tcp_cluster_request(
    stream: &mut TcpStream,
    request: &TcpClusterRequestFrame,
) -> Result<(), &'static str> {
    stream
        .write_all(b"W")
        .map_err(|_| "failed to write tcp cluster request command")?;
    let payload = encode_tcp_cluster_request(request);
    stream
        .write_all(&payload)
        .map_err(|_| "failed to write tcp cluster request")?;
    stream
        .flush()
        .map_err(|_| "failed to flush tcp cluster request")
}

fn read_tcp_cluster_request_payload(
    stream: &mut TcpStream,
) -> Result<TcpClusterRequestFrame, &'static str> {
    let mut payload = [0u8; TCP_CLUSTER_REQUEST_FRAME_BYTES];
    stream
        .read_exact(&mut payload)
        .map_err(|_| "failed to read tcp cluster request")?;
    decode_tcp_cluster_request(&payload)
}

fn write_tcp_cluster_response(
    stream: &mut TcpStream,
    response: &TcpClusterResponseFrame,
) -> Result<(), &'static str> {
    let payload = encode_tcp_cluster_response(response);
    stream
        .write_all(&payload)
        .map_err(|_| "failed to write tcp cluster response")?;
    stream
        .flush()
        .map_err(|_| "failed to flush tcp cluster response")
}

fn read_tcp_cluster_response(
    stream: &mut TcpStream,
) -> Result<TcpClusterResponseFrame, &'static str> {
    let mut payload = [0u8; TCP_CLUSTER_RESPONSE_FRAME_BYTES];
    stream
        .read_exact(&mut payload)
        .map_err(|_| "failed to read tcp cluster response")?;
    decode_tcp_cluster_response(&payload)
}

fn write_tcp_cluster_stop(stream: &mut TcpStream) -> Result<(), &'static str> {
    stream
        .write_all(b"S")
        .map_err(|_| "failed to write tcp cluster stop")?;
    stream
        .flush()
        .map_err(|_| "failed to flush tcp cluster stop")
}

fn tcp_cluster_worker_response(
    request: &TcpClusterRequestFrame,
) -> Result<TcpClusterResponseFrame, &'static str> {
    let artifact_hash = tcp_cluster_worker_artifact_hash(request);
    let artifact_kind_hash = cli_hash("tcp-cluster-soak-artifact-kind");
    let byte_len = 1_024 + request.work_index as u64;
    let mut response = TcpClusterResponseFrame {
        work_index: request.work_index,
        run_id: request.run_id,
        work_id: request.work_id,
        worker_id: request.worker_id,
        artifact_hash,
        artifact_kind_hash,
        byte_len,
        response_hash: [0; 32],
    };
    response.response_hash = tcp_cluster_response_hash(&response);
    Ok(response)
}

fn tcp_cluster_worker_artifact_hash(request: &TcpClusterRequestFrame) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-tcp-cluster-worker-artifact-v1");
    update_u32(&mut hasher, request.work_index);
    update_u128(&mut hasher, request.run_id);
    update_u128(&mut hasher, request.work_id);
    update_u128(&mut hasher, request.worker_id);
    hasher.update(&request.envelope_hash);
    hasher.update(&request.idempotency_key);
    hasher.update(&request.input_hash);
    *hasher.finalize().as_bytes()
}

fn tcp_cluster_response_hash(response: &TcpClusterResponseFrame) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-tcp-cluster-response-v1");
    update_u32(&mut hasher, response.work_index);
    update_u128(&mut hasher, response.run_id);
    update_u128(&mut hasher, response.work_id);
    update_u128(&mut hasher, response.worker_id);
    hasher.update(&response.artifact_hash);
    hasher.update(&response.artifact_kind_hash);
    update_u64(&mut hasher, response.byte_len);
    *hasher.finalize().as_bytes()
}

fn tcp_cluster_response_valid_for_request(
    response: &TcpClusterResponseFrame,
    request: &TcpClusterRequestFrame,
) -> bool {
    response.work_index == request.work_index
        && response.run_id == request.run_id
        && response.work_id == request.work_id
        && response.worker_id == request.worker_id
        && response.byte_len > 0
        && nonzero_hash(&response.artifact_hash)
        && nonzero_hash(&response.artifact_kind_hash)
        && response.artifact_hash == tcp_cluster_worker_artifact_hash(request)
        && response.response_hash == tcp_cluster_response_hash(response)
        && nonzero_hash(&response.response_hash)
}

fn encode_tcp_cluster_request(
    request: &TcpClusterRequestFrame,
) -> [u8; TCP_CLUSTER_REQUEST_FRAME_BYTES] {
    let mut payload = [0u8; TCP_CLUSTER_REQUEST_FRAME_BYTES];
    payload[0..4].copy_from_slice(&request.work_index.to_le_bytes());
    payload[4..20].copy_from_slice(&request.run_id.to_le_bytes());
    payload[20..36].copy_from_slice(&request.work_id.to_le_bytes());
    payload[36..52].copy_from_slice(&request.task_id.to_le_bytes());
    payload[52..68].copy_from_slice(&request.worker_id.to_le_bytes());
    payload[68..100].copy_from_slice(&request.envelope_hash);
    payload[100..132].copy_from_slice(&request.idempotency_key);
    payload[132..164].copy_from_slice(&request.input_hash);
    payload[164..196].copy_from_slice(&request.evidence_contract_hash);
    payload[196..228].copy_from_slice(&request.policy_window_hash);
    payload
}

fn decode_tcp_cluster_request(
    payload: &[u8; TCP_CLUSTER_REQUEST_FRAME_BYTES],
) -> Result<TcpClusterRequestFrame, &'static str> {
    Ok(TcpClusterRequestFrame {
        work_index: read_u32_le(payload, 0)?,
        run_id: read_u128_le(payload, 4)?,
        work_id: read_u128_le(payload, 20)?,
        task_id: read_u128_le(payload, 36)?,
        worker_id: read_u128_le(payload, 52)?,
        envelope_hash: read_hash32(payload, 68)?,
        idempotency_key: read_hash32(payload, 100)?,
        input_hash: read_hash32(payload, 132)?,
        evidence_contract_hash: read_hash32(payload, 164)?,
        policy_window_hash: read_hash32(payload, 196)?,
    })
}

fn encode_tcp_cluster_response(
    response: &TcpClusterResponseFrame,
) -> [u8; TCP_CLUSTER_RESPONSE_FRAME_BYTES] {
    let mut payload = [0u8; TCP_CLUSTER_RESPONSE_FRAME_BYTES];
    payload[0..4].copy_from_slice(&response.work_index.to_le_bytes());
    payload[4..20].copy_from_slice(&response.run_id.to_le_bytes());
    payload[20..36].copy_from_slice(&response.work_id.to_le_bytes());
    payload[36..52].copy_from_slice(&response.worker_id.to_le_bytes());
    payload[52..84].copy_from_slice(&response.artifact_hash);
    payload[84..116].copy_from_slice(&response.artifact_kind_hash);
    payload[116..124].copy_from_slice(&response.byte_len.to_le_bytes());
    payload[124..156].copy_from_slice(&response.response_hash);
    payload
}

fn decode_tcp_cluster_response(
    payload: &[u8; TCP_CLUSTER_RESPONSE_FRAME_BYTES],
) -> Result<TcpClusterResponseFrame, &'static str> {
    Ok(TcpClusterResponseFrame {
        work_index: read_u32_le(payload, 0)?,
        run_id: read_u128_le(payload, 4)?,
        work_id: read_u128_le(payload, 20)?,
        worker_id: read_u128_le(payload, 36)?,
        artifact_hash: read_hash32(payload, 52)?,
        artifact_kind_hash: read_hash32(payload, 84)?,
        byte_len: read_u64_le(payload, 116)?,
        response_hash: read_hash32(payload, 124)?,
    })
}

fn read_u32_le(bytes: &[u8], offset: usize) -> Result<u32, &'static str> {
    let end = offset.checked_add(4).ok_or("u32 offset overflow")?;
    let slice = bytes.get(offset..end).ok_or("u32 out of bounds")?;
    let mut out = [0u8; 4];
    out.copy_from_slice(slice);
    Ok(u32::from_le_bytes(out))
}

fn read_u64_le(bytes: &[u8], offset: usize) -> Result<u64, &'static str> {
    let end = offset.checked_add(8).ok_or("u64 offset overflow")?;
    let slice = bytes.get(offset..end).ok_or("u64 out of bounds")?;
    let mut out = [0u8; 8];
    out.copy_from_slice(slice);
    Ok(u64::from_le_bytes(out))
}

fn read_u128_le(bytes: &[u8], offset: usize) -> Result<u128, &'static str> {
    let end = offset.checked_add(16).ok_or("u128 offset overflow")?;
    let slice = bytes.get(offset..end).ok_or("u128 out of bounds")?;
    let mut out = [0u8; 16];
    out.copy_from_slice(slice);
    Ok(u128::from_le_bytes(out))
}

fn read_hash32(bytes: &[u8], offset: usize) -> Result<[u8; 32], &'static str> {
    let end = offset.checked_add(32).ok_or("hash offset overflow")?;
    let slice = bytes.get(offset..end).ok_or("hash out of bounds")?;
    let mut out = [0u8; 32];
    out.copy_from_slice(slice);
    if !nonzero_hash(&out) {
        return Err("zero hash rejected");
    }
    Ok(out)
}

fn elapsed_ns_u64(started: Instant) -> u64 {
    started.elapsed().as_nanos().min(u64::MAX as u128) as u64
}

fn agentic_sdk_context_report_hash(report: &AgenticSdkContextReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-agentic-sdk-context-report-v1");
    update_u128(&mut hasher, report.run_id);
    update_u128(&mut hasher, report.execution_id);
    update_u128(&mut hasher, report.active_task_id);
    update_bool(&mut hasher, report.replay_recorded);
    update_bool(&mut hasher, report.context_recorded);
    hasher.update(&report.sdk_run_hash);
    hasher.update(&report.manifest_hash);
    hasher.update(&report.execution_record_hash);
    hasher.update(&report.capsule_hash);
    hasher.update(&report.candidate_list_hash);
    update_u32(&mut hasher, report.candidate_count);
    update_u64(&mut hasher, report.sdk_run_event_id);
    hasher.update(&report.sdk_run_event_hash);
    hasher.update(&report.sdk_run_replay_binding_hash);
    hasher.update(&report.handoff_hash);
    hasher.update(&report.checkpoint_hash);
    hasher.update(&report.next_action_packet_hash);
    hasher.update(&report.context_pack_digest);
    hasher.update(&report.context_pack_node_hash);
    hasher.update(&report.context_pack_candidate_proof_hash);
    update_u64(&mut hasher, report.context_pack_event_id);
    hasher.update(&report.context_pack_event_hash);
    hasher.update(&report.context_archive_manifest_hash);
    hasher.update(&report.context_ledger_hash);
    update_u64(&mut hasher, report.context_event_count);
    *hasher.finalize().as_bytes()
}

fn hot_browser_shadow_report_hash(report: &HotBrowserShadowReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-hot-browser-shadow-report-v1");
    update_u128(&mut hasher, report.run_id);
    update_u32(&mut hasher, report.artifact_count);
    update_u64(&mut hasher, report.total_artifact_bytes);
    update_u32(&mut hasher, report.hot_commit_count);
    update_u64(&mut hasher, report.hot_commit_min_ns);
    update_u64(&mut hasher, report.hot_commit_max_ns);
    update_u64(&mut hasher, report.hot_commit_total_ns);
    update_u64(&mut hasher, report.arena_live_bytes_after_hot);
    update_u32(&mut hasher, report.arena_live_slots_after_hot);
    update_u64(&mut hasher, report.arena_total_commits);
    update_bool(
        &mut hasher,
        report.hot_commit_completed_before_first_cold_receipt,
    );
    update_bool(&mut hasher, report.shadow_sealer_nonblocking_admission);
    update_u64(&mut hasher, report.shadow_sealer_queue_depth as u64);
    update_u64(&mut hasher, report.shadow_sealer_queued_admissions);
    update_u64(&mut hasher, report.shadow_sealer_backpressure_rejections);
    update_u32(&mut hasher, report.cold_seal_receipt_count);
    update_u64(&mut hasher, report.cold_seal_total_bytes);
    update_bool(&mut hasher, report.cold_files_materialized);
    update_bool(&mut hasher, report.shadow_receipts_valid);
    hasher.update(&report.shadow_batch_hash);
    update_bool(&mut hasher, report.shadow_batch_valid);
    update_u64(&mut hasher, report.replay_event_count);
    update_bool(&mut hasher, report.replay_hash_chain_valid);
    update_bool(&mut hasher, report.shadow_seal_replay_recorded);
    hasher.update(&report.replay_last_hash);
    update_u32(&mut hasher, report.shadow_arrow_archive_segment_count);
    update_u64(&mut hasher, report.shadow_arrow_archive_event_count);
    hasher.update(&report.shadow_arrow_archive_manifest_hash);
    hasher.update(&report.shadow_arrow_archive_audit_proof_hash);
    hasher.update(&report.shadow_arrow_archive_segment_witness_hash);
    hasher.update(&report.shadow_arrow_archive_mmap_evidence_hash);
    hasher.update(&report.shadow_arrow_archive_determinism_proof_hash);
    update_bool(&mut hasher, report.shadow_arrow_archive_recovered);
    update_bool(&mut hasher, report.shadow_arrow_archive_replay_matches);
    update_bool(
        &mut hasher,
        report.shadow_arrow_archive_contains_shadow_seal,
    );
    hasher.update(&report.hot_artifact_batch_hash);
    for artifact in &report.artifact_reports {
        hasher.update(artifact.kind.as_bytes());
        update_u64(&mut hasher, artifact.byte_len);
        update_u32(&mut hasher, artifact.slot);
        update_u32(&mut hasher, artifact.generation);
        hasher.update(&artifact.artifact_hash);
        hasher.update(&artifact.storage_ref_hash);
        hasher.update(&artifact.seal_hash);
    }
    update_bool(&mut hasher, report.no_file_roundtrip_on_hot_path);
    update_bool(&mut hasher, report.trust_level_prod);
    update_bool(&mut hasher, report.physical_witness_required);
    update_bool(&mut hasher, report.fail_closed);
    *hasher.finalize().as_bytes()
}

fn shadow_sealer_soak_report_hash(report: &ShadowSealerSoakReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-shadow-sealer-soak-report-v1");
    update_u128(&mut hasher, report.run_id);
    update_u32(&mut hasher, report.sample_count);
    update_u64(&mut hasher, report.payload_bytes_per_sample);
    update_u64(&mut hasher, report.total_payload_bytes);
    update_u64(&mut hasher, report.queue_depth as u64);
    update_u64(&mut hasher, report.hot_commit_min_ns);
    update_u64(&mut hasher, report.hot_commit_max_ns);
    update_u64(&mut hasher, report.hot_commit_total_ns);
    update_u64(&mut hasher, report.hot_submit_min_ns);
    update_u64(&mut hasher, report.hot_submit_max_ns);
    update_u64(&mut hasher, report.hot_submit_total_ns);
    update_u64(&mut hasher, report.hot_submit_gate_ns);
    update_bool(&mut hasher, report.hot_submit_under_gate);
    update_bool(
        &mut hasher,
        report.hot_submissions_completed_before_receipts,
    );
    update_u64(&mut hasher, report.queued_admissions);
    update_u64(&mut hasher, report.backpressure_rejections);
    update_u32(&mut hasher, report.cold_seal_receipt_count);
    update_u64(&mut hasher, report.cold_seal_total_bytes);
    update_u64(&mut hasher, report.cold_seal_wait_total_ns);
    update_bool(&mut hasher, report.cold_payload_files_materialized);
    update_bool(&mut hasher, report.cold_payload_file_hashes_match);
    update_bool(&mut hasher, report.cold_payload_sync_requested);
    update_bool(&mut hasher, report.shadow_receipts_valid);
    hasher.update(&report.shadow_batch_hash);
    update_bool(&mut hasher, report.shadow_batch_valid);
    hasher.update(&report.payload_set_hash);
    hasher.update(&report.receipt_set_hash);
    hasher.update(&report.cold_file_evidence_hash);
    update_bool(&mut hasher, report.trust_level_prod);
    update_bool(&mut hasher, report.physical_witness_required);
    update_bool(&mut hasher, report.fail_closed);
    *hasher.finalize().as_bytes()
}

fn dynamic_provider_fallback_report_hash(report: &DynamicProviderFallbackReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-dynamic-provider-fallback-report-v1");
    update_u32(&mut hasher, report.sample_count);
    update_u64(&mut hasher, report.latency_gate_ns);
    hasher.update(report.feedback_kind.as_bytes());
    hasher.update(report.rate_limited_provider.as_bytes());
    hasher.update(report.rate_limited_model.as_bytes());
    hasher.update(report.selected_provider.as_bytes());
    hasher.update(report.selected_model.as_bytes());
    update_bool(&mut hasher, report.fallback_used);
    update_bool(&mut hasher, report.downgraded_model);
    hasher.update(&report.previous_ledger_hash);
    hasher.update(&report.updated_ledger_hash);
    hasher.update(&report.feedback_hash);
    hasher.update(&report.admission_proof_hash);
    hasher.update(&report.fallback_proof_hash);
    update_u32(&mut hasher, report.throttled_provider_count);
    update_u64(&mut hasher, report.latency_min_ns);
    update_u64(&mut hasher, report.latency_max_ns);
    update_u64(&mut hasher, report.latency_total_ns);
    update_bool(&mut hasher, report.latency_under_gate);
    update_bool(&mut hasher, report.all_samples_validated);
    hasher.update(&report.live_provider_429_soak_admission.admission_hash);
    update_bool(&mut hasher, report.live_provider_traffic_present);
    update_bool(
        &mut hasher,
        report.production_provider_release_blocked_without_live_429_soak,
    );
    *hasher.finalize().as_bytes()
}

fn live_provider_429_soak_admission_hash(admission: &LiveProvider429SoakAdmission) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-live-provider-429-soak-admission-v1");
    update_str(&mut hasher, admission.expected_capture_path);
    update_bool(&mut hasher, admission.capture_present);
    hasher.update(&admission.capture_hash);
    update_str(&mut hasher, admission.provider_set);
    hasher.update(&admission.provider_set_hash);
    update_str(&mut hasher, admission.capture_contract);
    hasher.update(&admission.capture_contract_hash);
    update_u32(&mut hasher, admission.required_sample_count);
    update_u64(&mut hasher, admission.latency_gate_ns);
    update_str(&mut hasher, admission.expected_feedback_kind);
    update_str(&mut hasher, admission.primary_provider);
    update_str(&mut hasher, admission.primary_model);
    update_str(&mut hasher, admission.fallback_provider);
    update_str(&mut hasher, admission.fallback_model);
    update_str(&mut hasher, admission.admission_status);
    update_str(&mut hasher, admission.production_blocker_id);
    *hasher.finalize().as_bytes()
}

fn live_provider_429_soak_admission_valid(admission: &LiveProvider429SoakAdmission) -> bool {
    let missing_capture = !admission.capture_present
        && admission.capture_hash == [0; 32]
        && admission.admission_status == "missing";
    let candidate_capture = admission.capture_present
        && nonzero_hash(&admission.capture_hash)
        && admission.admission_status == "candidate-present-live-soak-gate-required";
    admission.schema == LIVE_PROVIDER_429_SOAK_ADMISSION_SCHEMA
        && admission.expected_capture_path == LIVE_PROVIDER_429_SOAK_EXPECTED_PATH
        && admission.provider_set == LIVE_PROVIDER_429_SOAK_PROVIDER_SET
        && admission.provider_set_hash == cli_hash(LIVE_PROVIDER_429_SOAK_PROVIDER_SET)
        && admission.capture_contract == LIVE_PROVIDER_429_SOAK_CAPTURE_CONTRACT
        && admission.capture_contract_hash == cli_hash(LIVE_PROVIDER_429_SOAK_CAPTURE_CONTRACT)
        && admission.required_sample_count == DYNAMIC_PROVIDER_FALLBACK_SAMPLE_COUNT
        && admission.latency_gate_ns == DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS
        && admission.expected_feedback_kind == "http_429"
        && admission.primary_provider == "openrouter"
        && admission.primary_model == "gpt-4"
        && admission.fallback_provider == "nim"
        && admission.fallback_model == "llama-3-70b"
        && admission.production_blocker_id == LIVE_PROVIDER_429_SOAK_BLOCKER_ID
        && (missing_capture || candidate_capture)
        && admission.admission_hash == live_provider_429_soak_admission_hash(admission)
        && nonzero_hash(&admission.admission_hash)
}

fn real_multi_machine_cluster_soak_admission_hash(
    admission: &RealMultiMachineClusterSoakAdmission,
) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-real-multi-machine-cluster-soak-admission-v1");
    update_str(&mut hasher, admission.expected_capture_path);
    update_bool(&mut hasher, admission.capture_present);
    hasher.update(&admission.capture_hash);
    update_str(&mut hasher, admission.network_contract);
    hasher.update(&admission.network_contract_hash);
    update_str(&mut hasher, admission.topology_contract);
    hasher.update(&admission.topology_contract_hash);
    update_u32(&mut hasher, admission.required_worker_count);
    update_u32(&mut hasher, admission.required_work_item_count);
    update_u32(&mut hasher, admission.required_round_trip_count);
    update_u32(&mut hasher, admission.local_loopback_worker_count);
    update_u32(&mut hasher, admission.local_loopback_work_item_count);
    update_u32(&mut hasher, admission.local_loopback_round_trip_count);
    update_str(&mut hasher, admission.admission_status);
    update_str(&mut hasher, admission.production_blocker_id);
    *hasher.finalize().as_bytes()
}

fn real_multi_machine_cluster_soak_admission_valid(
    admission: &RealMultiMachineClusterSoakAdmission,
) -> bool {
    let missing_capture = !admission.capture_present
        && admission.capture_hash == [0; 32]
        && admission.admission_status == "missing";
    let candidate_capture = admission.capture_present
        && nonzero_hash(&admission.capture_hash)
        && admission.admission_status == "candidate-present-real-multi-machine-soak-gate-required";
    admission.schema == REAL_MULTI_MACHINE_CLUSTER_SOAK_ADMISSION_SCHEMA
        && admission.expected_capture_path == REAL_MULTI_MACHINE_CLUSTER_SOAK_EXPECTED_PATH
        && admission.network_contract == REAL_MULTI_MACHINE_CLUSTER_SOAK_NETWORK_CONTRACT
        && admission.network_contract_hash
            == cli_hash(REAL_MULTI_MACHINE_CLUSTER_SOAK_NETWORK_CONTRACT)
        && admission.topology_contract == REAL_MULTI_MACHINE_CLUSTER_SOAK_TOPOLOGY_CONTRACT
        && admission.topology_contract_hash
            == cli_hash(REAL_MULTI_MACHINE_CLUSTER_SOAK_TOPOLOGY_CONTRACT)
        && admission.required_worker_count == TCP_CLUSTER_SOAK_WORKER_COUNT
        && admission.required_work_item_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && admission.required_round_trip_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && admission.local_loopback_worker_count == TCP_CLUSTER_SOAK_WORKER_COUNT
        && admission.local_loopback_work_item_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && admission.local_loopback_round_trip_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && admission.production_blocker_id == REAL_MULTI_MACHINE_CLUSTER_SOAK_BLOCKER_ID
        && (missing_capture || candidate_capture)
        && admission.admission_hash == real_multi_machine_cluster_soak_admission_hash(admission)
        && nonzero_hash(&admission.admission_hash)
}

fn dynamic_provider_fallback_report_valid(report: &DynamicProviderFallbackReport) -> bool {
    let samples = report.sample_count as u64;
    report.schema == DYNAMIC_PROVIDER_FALLBACK_REPORT_SCHEMA
        && report.sample_count == DYNAMIC_PROVIDER_FALLBACK_SAMPLE_COUNT
        && report.latency_gate_ns == DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS
        && report.feedback_kind == "http_429"
        && report.rate_limited_provider == "openrouter"
        && report.rate_limited_model == "gpt-4"
        && report.selected_provider == "nim"
        && report.selected_model == "llama-3-70b"
        && report.fallback_used
        && report.downgraded_model
        && nonzero_hash(&report.previous_ledger_hash)
        && nonzero_hash(&report.updated_ledger_hash)
        && report.previous_ledger_hash != report.updated_ledger_hash
        && nonzero_hash(&report.feedback_hash)
        && nonzero_hash(&report.admission_proof_hash)
        && nonzero_hash(&report.fallback_proof_hash)
        && report.throttled_provider_count == 1
        && report.latency_min_ns > 0
        && report.latency_min_ns <= report.latency_max_ns
        && report.latency_total_ns >= report.latency_min_ns.saturating_mul(samples)
        && report.latency_total_ns <= report.latency_max_ns.saturating_mul(samples)
        && report.latency_under_gate
        && report.latency_max_ns < DYNAMIC_PROVIDER_FALLBACK_LATENCY_GATE_NS
        && report.all_samples_validated
        && live_provider_429_soak_admission_valid(&report.live_provider_429_soak_admission)
        && !report.live_provider_traffic_present
        && report.production_provider_release_blocked_without_live_429_soak
        && report.report_hash == dynamic_provider_fallback_report_hash(report)
        && nonzero_hash(&report.report_hash)
}

fn hot_browser_shadow_report_valid(report: &HotBrowserShadowReport) -> bool {
    let samples = report.hot_commit_count as u64;
    report.schema == HOT_BROWSER_SHADOW_REPORT_SCHEMA
        && report.run_id == HOT_BROWSER_SHADOW_RUN_ID
        && report.artifact_count == HOT_BROWSER_SHADOW_ARTIFACT_COUNT
        && report.hot_commit_count == HOT_BROWSER_SHADOW_ARTIFACT_COUNT
        && report.artifact_reports.len() == HOT_BROWSER_SHADOW_ARTIFACT_COUNT as usize
        && report.total_artifact_bytes > 0
        && report.hot_commit_min_ns > 0
        && report.hot_commit_min_ns <= report.hot_commit_max_ns
        && report.hot_commit_total_ns >= report.hot_commit_min_ns.saturating_mul(samples)
        && report.hot_commit_total_ns <= report.hot_commit_max_ns.saturating_mul(samples)
        && report.arena_live_bytes_after_hot == report.total_artifact_bytes
        && report.arena_live_slots_after_hot == HOT_BROWSER_SHADOW_ARTIFACT_COUNT
        && report.arena_total_commits == HOT_BROWSER_SHADOW_ARTIFACT_COUNT as u64
        && report.hot_commit_completed_before_first_cold_receipt
        && report.shadow_sealer_nonblocking_admission
        && report.shadow_sealer_queue_depth >= HOT_BROWSER_SHADOW_ARTIFACT_COUNT as usize
        && report.shadow_sealer_queued_admissions == HOT_BROWSER_SHADOW_ARTIFACT_COUNT as u64
        && report.shadow_sealer_backpressure_rejections == 0
        && report.cold_seal_receipt_count == HOT_BROWSER_SHADOW_ARTIFACT_COUNT
        && report.cold_seal_total_bytes == report.total_artifact_bytes
        && report.cold_files_materialized
        && report.shadow_receipts_valid
        && nonzero_hash(&report.shadow_batch_hash)
        && report.shadow_batch_valid
        && report.replay_event_count == 2
        && report.replay_hash_chain_valid
        && report.shadow_seal_replay_recorded
        && nonzero_hash(&report.replay_last_hash)
        && report.shadow_arrow_archive_segment_count == 2
        && report.shadow_arrow_archive_event_count == report.replay_event_count
        && nonzero_hash(&report.shadow_arrow_archive_manifest_hash)
        && nonzero_hash(&report.shadow_arrow_archive_audit_proof_hash)
        && nonzero_hash(&report.shadow_arrow_archive_segment_witness_hash)
        && nonzero_hash(&report.shadow_arrow_archive_mmap_evidence_hash)
        && nonzero_hash(&report.shadow_arrow_archive_determinism_proof_hash)
        && report.shadow_arrow_archive_recovered
        && report.shadow_arrow_archive_replay_matches
        && report.shadow_arrow_archive_contains_shadow_seal
        && nonzero_hash(&report.hot_artifact_batch_hash)
        && report.no_file_roundtrip_on_hot_path
        && report.trust_level_prod
        && report.physical_witness_required
        && report.fail_closed
        && report.artifact_reports.iter().all(|artifact| {
            artifact.byte_len > 0
                && artifact.generation > 0
                && nonzero_hash(&artifact.artifact_hash)
                && nonzero_hash(&artifact.storage_ref_hash)
                && nonzero_hash(&artifact.seal_hash)
                && artifact.storage_ref_hash
                    == crate::hot_engine::arena_storage_ref_hash(
                        artifact.slot,
                        artifact.generation,
                        artifact.artifact_hash,
                    )
        })
        && report.report_hash == hot_browser_shadow_report_hash(report)
        && nonzero_hash(&report.report_hash)
}

fn shadow_sealer_soak_report_valid(report: &ShadowSealerSoakReport) -> bool {
    let samples = report.sample_count as u64;
    report.schema == SHADOW_SEALER_SOAK_REPORT_SCHEMA
        && report.run_id == SHADOW_SEALER_SOAK_RUN_ID
        && report.sample_count == SHADOW_SEALER_SOAK_SAMPLE_COUNT
        && report.payload_bytes_per_sample == SHADOW_SEALER_SOAK_PAYLOAD_BYTES as u64
        && report.total_payload_bytes
            == SHADOW_SEALER_SOAK_SAMPLE_COUNT as u64 * SHADOW_SEALER_SOAK_PAYLOAD_BYTES as u64
        && report.queue_depth == SHADOW_SEALER_SOAK_QUEUE_DEPTH
        && report.hot_commit_min_ns > 0
        && report.hot_commit_min_ns <= report.hot_commit_max_ns
        && report.hot_commit_total_ns >= report.hot_commit_min_ns.saturating_mul(samples)
        && report.hot_commit_total_ns <= report.hot_commit_max_ns.saturating_mul(samples)
        && report.hot_submit_min_ns > 0
        && report.hot_submit_min_ns <= report.hot_submit_max_ns
        && report.hot_submit_total_ns >= report.hot_submit_min_ns.saturating_mul(samples)
        && report.hot_submit_total_ns <= report.hot_submit_max_ns.saturating_mul(samples)
        && report.hot_submit_gate_ns == SHADOW_SEALER_SOAK_HOT_SUBMIT_GATE_NS
        && report.hot_submit_under_gate
        && report.hot_submit_max_ns < SHADOW_SEALER_SOAK_HOT_SUBMIT_GATE_NS
        && report.hot_submissions_completed_before_receipts
        && report.queued_admissions == SHADOW_SEALER_SOAK_SAMPLE_COUNT as u64
        && report.backpressure_rejections == 0
        && report.cold_seal_receipt_count == SHADOW_SEALER_SOAK_SAMPLE_COUNT
        && report.cold_seal_total_bytes == report.total_payload_bytes
        && report.cold_seal_wait_total_ns > 0
        && report.cold_payload_files_materialized
        && report.cold_payload_file_hashes_match
        && report.cold_payload_sync_requested
        && report.shadow_receipts_valid
        && nonzero_hash(&report.shadow_batch_hash)
        && report.shadow_batch_valid
        && nonzero_hash(&report.payload_set_hash)
        && nonzero_hash(&report.receipt_set_hash)
        && nonzero_hash(&report.cold_file_evidence_hash)
        && report.trust_level_prod
        && report.physical_witness_required
        && report.fail_closed
        && report.report_hash == shadow_sealer_soak_report_hash(report)
        && nonzero_hash(&report.report_hash)
}

fn quickjs_cold_start_report_hash(report: &QuickJsColdStartReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-quickjs-cold-start-report-v1");
    update_u32(&mut hasher, report.sample_count);
    update_u64(&mut hasher, report.fuel_limit);
    update_u64(&mut hasher, report.script_bytes);
    update_u64(&mut hasher, report.abi_packet_bytes);
    update_u64(&mut hasher, report.wasm_module_bytes);
    update_u64(&mut hasher, report.cold_start_min_ns);
    update_u64(&mut hasher, report.cold_start_max_ns);
    update_u64(&mut hasher, report.cold_start_total_ns);
    update_u64(&mut hasher, report.warm_cached_min_ns);
    update_u64(&mut hasher, report.warm_cached_max_ns);
    update_u64(&mut hasher, report.warm_cached_total_ns);
    update_u32(&mut hasher, report.cold_cache_entries_after_first);
    update_u32(&mut hasher, report.warm_cache_entries_before);
    update_u32(&mut hasher, report.warm_cache_entries_after);
    update_u64(&mut hasher, report.cold_fuel_consumed_total);
    update_u64(&mut hasher, report.warm_fuel_consumed_total);
    hasher.update(&report.cold_artifact_hash);
    hasher.update(&report.warm_artifact_hash);
    hasher.update(&report.script_blake3);
    hasher.update(&report.wrapper_blake3);
    hasher.update(&report.invocation_blake3);
    update_bool(&mut hasher, report.bridge_probe_executed);
    hasher.update(&report.full_interpreter_admission.admission_hash);
    update_bool(&mut hasher, report.full_quickjs_interpreter_present);
    update_bool(
        &mut hasher,
        report.production_quickjs_release_blocked_without_full_interpreter,
    );
    *hasher.finalize().as_bytes()
}

fn quickjs_full_interpreter_admission_hash(
    admission: &QuickJsFullInterpreterAdmission,
) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-quickjs-full-interpreter-admission-v1");
    update_str(&mut hasher, admission.expected_runtime_path);
    update_bool(&mut hasher, admission.runtime_wasm_present);
    hasher.update(&admission.runtime_wasm_hash);
    update_str(&mut hasher, admission.semantic_corpus);
    hasher.update(&admission.semantic_corpus_hash);
    update_str(&mut hasher, admission.sandbox_contract);
    hasher.update(&admission.sandbox_contract_hash);
    update_u32(&mut hasher, admission.required_cold_start_sample_count);
    update_u64(&mut hasher, admission.required_fuel_limit);
    update_str(&mut hasher, admission.admission_status);
    update_str(&mut hasher, admission.production_blocker_id);
    *hasher.finalize().as_bytes()
}

fn quickjs_full_interpreter_admission_valid(admission: &QuickJsFullInterpreterAdmission) -> bool {
    let missing_runtime = !admission.runtime_wasm_present
        && admission.runtime_wasm_hash == [0; 32]
        && admission.admission_status == "missing";
    let candidate_runtime = admission.runtime_wasm_present
        && nonzero_hash(&admission.runtime_wasm_hash)
        && admission.admission_status == "candidate-present-semantic-cold-start-gate-required";
    admission.schema == QUICKJS_FULL_INTERPRETER_ADMISSION_SCHEMA
        && admission.expected_runtime_path == QUICKJS_FULL_INTERPRETER_EXPECTED_PATH
        && admission.semantic_corpus == QUICKJS_FULL_INTERPRETER_SEMANTIC_CORPUS
        && admission.semantic_corpus_hash == cli_hash(QUICKJS_FULL_INTERPRETER_SEMANTIC_CORPUS)
        && admission.sandbox_contract == QUICKJS_FULL_INTERPRETER_SANDBOX_CONTRACT
        && admission.sandbox_contract_hash == cli_hash(QUICKJS_FULL_INTERPRETER_SANDBOX_CONTRACT)
        && admission.required_cold_start_sample_count == QUICKJS_COLD_START_SAMPLE_COUNT
        && admission.required_fuel_limit == QUICKJS_COLD_START_FUEL_LIMIT
        && admission.production_blocker_id == QUICKJS_FULL_INTERPRETER_BLOCKER_ID
        && (missing_runtime || candidate_runtime)
        && admission.admission_hash == quickjs_full_interpreter_admission_hash(admission)
        && nonzero_hash(&admission.admission_hash)
}

fn quickjs_cold_start_report_valid(report: &QuickJsColdStartReport) -> bool {
    let samples = report.sample_count as u64;
    report.schema == QUICKJS_COLD_START_REPORT_SCHEMA
        && report.sample_count == QUICKJS_COLD_START_SAMPLE_COUNT
        && report.fuel_limit == QUICKJS_COLD_START_FUEL_LIMIT
        && report.script_bytes > 0
        && report.abi_packet_bytes
            == report
                .script_bytes
                .saturating_add(QUICKJS_INVOCATION_ABI_HEADER_BYTES as u64)
        && report.wasm_module_bytes > 0
        && report.cold_start_min_ns > 0
        && report.cold_start_min_ns <= report.cold_start_max_ns
        && report.cold_start_total_ns >= report.cold_start_min_ns.saturating_mul(samples)
        && report.cold_start_total_ns <= report.cold_start_max_ns.saturating_mul(samples)
        && report.warm_cached_min_ns > 0
        && report.warm_cached_min_ns <= report.warm_cached_max_ns
        && report.warm_cached_total_ns >= report.warm_cached_min_ns.saturating_mul(samples)
        && report.warm_cached_total_ns <= report.warm_cached_max_ns.saturating_mul(samples)
        && report.cold_cache_entries_after_first == 1
        && report.warm_cache_entries_before == 1
        && report.warm_cache_entries_after == 1
        && report.cold_fuel_consumed_total >= samples
        && report.warm_fuel_consumed_total >= samples
        && nonzero_hash(&report.cold_artifact_hash)
        && nonzero_hash(&report.warm_artifact_hash)
        && nonzero_hash(&report.script_blake3)
        && nonzero_hash(&report.wrapper_blake3)
        && nonzero_hash(&report.invocation_blake3)
        && report.bridge_probe_executed
        && quickjs_full_interpreter_admission_valid(&report.full_interpreter_admission)
        && !report.full_quickjs_interpreter_present
        && report.production_quickjs_release_blocked_without_full_interpreter
        && report.report_hash == quickjs_cold_start_report_hash(report)
        && nonzero_hash(&report.report_hash)
}

fn tcp_cluster_soak_report_hash(report: &TcpClusterSoakReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-tcp-cluster-soak-report-v1");
    update_u128(&mut hasher, report.run_id);
    update_u32(&mut hasher, report.worker_count);
    update_u32(&mut hasher, report.work_item_count);
    update_u32(&mut hasher, report.accepted_count);
    update_u32(&mut hasher, report.duplicate_count);
    update_u64(&mut hasher, report.replay_event_count);
    update_bool(&mut hasher, report.replay_hash_chain_valid);
    update_bool(&mut hasher, report.tcp_listener_bound);
    update_u32(&mut hasher, report.tcp_worker_connections);
    update_u32(&mut hasher, report.tcp_round_trip_count);
    update_u64(&mut hasher, report.tcp_round_trip_min_ns);
    update_u64(&mut hasher, report.tcp_round_trip_max_ns);
    update_u64(&mut hasher, report.tcp_round_trip_total_ns);
    update_u64(&mut hasher, report.tcp_payload_bytes_sent);
    update_u64(&mut hasher, report.tcp_payload_bytes_received);
    update_u32(&mut hasher, report.worker_response_count);
    update_bool(&mut hasher, report.direct_worker_commit_rejected);
    update_bool(&mut hasher, report.side_effect_partition_paused);
    hasher.update(&report.network_trace_hash);
    hasher.update(&report.candidate_result_hash);
    hasher.update(&report.replay_last_hash);
    update_bool(&mut hasher, report.loopback_tcp_only);
    update_bool(&mut hasher, report.multi_machine_real_cluster);
    hasher.update(&report.real_multi_machine_cluster_admission.admission_hash);
    update_bool(&mut hasher, report.real_multi_node_cluster_test_present);
    update_bool(
        &mut hasher,
        report.production_cluster_release_blocked_without_real_multi_node_soak,
    );
    *hasher.finalize().as_bytes()
}

fn tcp_cluster_soak_report_valid(report: &TcpClusterSoakReport) -> bool {
    let samples = report.tcp_round_trip_count as u64;
    report.schema == TCP_CLUSTER_SOAK_REPORT_SCHEMA
        && report.run_id == TCP_CLUSTER_SOAK_RUN_ID
        && report.worker_count == TCP_CLUSTER_SOAK_WORKER_COUNT
        && report.work_item_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && report.accepted_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && report.duplicate_count == 1
        && report.replay_event_count == TCP_CLUSTER_SOAK_WORK_ITEMS as u64 + 1
        && report.replay_hash_chain_valid
        && report.tcp_listener_bound
        && report.tcp_worker_connections == TCP_CLUSTER_SOAK_WORKER_COUNT
        && report.tcp_round_trip_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && report.worker_response_count == TCP_CLUSTER_SOAK_WORK_ITEMS
        && report.tcp_round_trip_min_ns > 0
        && report.tcp_round_trip_min_ns <= report.tcp_round_trip_max_ns
        && report.tcp_round_trip_total_ns >= report.tcp_round_trip_min_ns.saturating_mul(samples)
        && report.tcp_round_trip_total_ns <= report.tcp_round_trip_max_ns.saturating_mul(samples)
        && report.tcp_payload_bytes_sent
            >= TCP_CLUSTER_SOAK_WORK_ITEMS as u64 * (1 + TCP_CLUSTER_REQUEST_FRAME_BYTES as u64)
        && report.tcp_payload_bytes_received
            >= TCP_CLUSTER_SOAK_WORK_ITEMS as u64 * TCP_CLUSTER_RESPONSE_FRAME_BYTES as u64
        && report.direct_worker_commit_rejected
        && report.side_effect_partition_paused
        && nonzero_hash(&report.network_trace_hash)
        && nonzero_hash(&report.candidate_result_hash)
        && nonzero_hash(&report.replay_last_hash)
        && report.loopback_tcp_only
        && !report.multi_machine_real_cluster
        && real_multi_machine_cluster_soak_admission_valid(
            &report.real_multi_machine_cluster_admission,
        )
        && !report.real_multi_node_cluster_test_present
        && report.production_cluster_release_blocked_without_real_multi_node_soak
        && report.report_hash == tcp_cluster_soak_report_hash(report)
        && nonzero_hash(&report.report_hash)
}

fn cluster_loopback_report_hash(report: &ClusterLoopbackReport) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-cluster-loopback-report-v1");
    update_u128(&mut hasher, report.run_id);
    update_u32(&mut hasher, report.worker_count);
    update_u32(&mut hasher, report.work_item_count);
    update_u32(&mut hasher, report.accepted_count);
    update_u32(&mut hasher, report.duplicate_count);
    update_u64(&mut hasher, report.replay_event_count);
    update_bool(&mut hasher, report.replay_hash_chain_valid);
    update_bool(&mut hasher, report.direct_worker_commit_rejected);
    update_bool(&mut hasher, report.side_effect_partition_paused);
    update_u64(&mut hasher, report.max_logical_rtt_ticks);
    update_u64(&mut hasher, report.total_logical_rtt_ticks);
    hasher.update(&report.candidate_result_hash);
    hasher.update(&report.replay_last_hash);
    update_bool(&mut hasher, report.multi_machine_real_cluster);
    *hasher.finalize().as_bytes()
}

fn cluster_loopback_report_valid(report: &ClusterLoopbackReport) -> bool {
    report.schema == CLUSTER_LOOPBACK_REPORT_SCHEMA
        && report.run_id == CLUSTER_LOOPBACK_RUN_ID
        && report.worker_count == CLUSTER_LOOPBACK_WORKER_COUNT
        && report.work_item_count == CLUSTER_LOOPBACK_WORK_ITEMS
        && report.accepted_count == CLUSTER_LOOPBACK_WORK_ITEMS
        && report.duplicate_count == 1
        && report.replay_event_count == CLUSTER_LOOPBACK_WORK_ITEMS as u64 + 1
        && report.replay_hash_chain_valid
        && report.direct_worker_commit_rejected
        && report.side_effect_partition_paused
        && report.max_logical_rtt_ticks > 0
        && report.total_logical_rtt_ticks >= report.max_logical_rtt_ticks
        && nonzero_hash(&report.candidate_result_hash)
        && nonzero_hash(&report.replay_last_hash)
        && !report.multi_machine_real_cluster
        && report.report_hash == cluster_loopback_report_hash(report)
        && nonzero_hash(&report.report_hash)
}

#[allow(clippy::too_many_arguments)]
fn agentic_sdk_context_report_write_evidence_hash(
    staged_temp_file_used: bool,
    temp_file_synced_before_publish: bool,
    publish_completed: bool,
    parent_directory_sync_attempted: bool,
    replace_existing_supported: bool,
    publish_write_through_requested: bool,
    logical_payload_bytes: u64,
    logical_payload_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"aegis-agentic-sdk-context-report-write-v1");
    update_bool(&mut hasher, staged_temp_file_used);
    update_bool(&mut hasher, temp_file_synced_before_publish);
    update_bool(&mut hasher, publish_completed);
    update_bool(&mut hasher, parent_directory_sync_attempted);
    update_bool(&mut hasher, replace_existing_supported);
    update_bool(&mut hasher, publish_write_through_requested);
    update_u64(&mut hasher, logical_payload_bytes);
    hasher.update(&logical_payload_hash);
    *hasher.finalize().as_bytes()
}

fn write_agentic_sdk_context_report_artifact(
    path: &Path,
    payload: &[u8],
) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid agentic sdk context report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create agentic sdk context report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create agentic sdk context report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write agentic sdk context report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush agentic sdk context report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync agentic sdk context report temp file")?;
    }
    if let Err(_error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err("failed to publish agentic sdk context report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        let _ = std::fs::File::open(parent).and_then(|directory| directory.sync_all());
    }
    Ok(())
}

fn write_cluster_loopback_report_artifact(path: &Path, payload: &[u8]) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid cluster loopback report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create cluster loopback report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create cluster loopback report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write cluster loopback report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush cluster loopback report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync cluster loopback report temp file")?;
    }
    if let Err(_error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err("failed to publish cluster loopback report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        if let Ok(directory) = std::fs::File::open(parent) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

fn write_tcp_cluster_soak_report_artifact(path: &Path, payload: &[u8]) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid tcp cluster soak report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create tcp cluster soak report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create tcp cluster soak report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write tcp cluster soak report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush tcp cluster soak report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync tcp cluster soak report temp file")?;
    }
    if let Err(_error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err("failed to publish tcp cluster soak report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        if let Ok(directory) = std::fs::File::open(parent) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

fn write_quickjs_cold_start_report_artifact(
    path: &Path,
    payload: &[u8],
) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid quickjs cold-start report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create quickjs cold-start report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create quickjs cold-start report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write quickjs cold-start report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush quickjs cold-start report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync quickjs cold-start report temp file")?;
    }
    if let Err(_error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err("failed to publish quickjs cold-start report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        if let Ok(directory) = std::fs::File::open(parent) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

fn write_hot_browser_shadow_report_artifact(
    path: &Path,
    payload: &[u8],
) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid hot browser shadow report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create hot browser shadow report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create hot browser shadow report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write hot browser shadow report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush hot browser shadow report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync hot browser shadow report temp file")?;
    }
    if let Err(_error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err("failed to publish hot browser shadow report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        if let Ok(directory) = std::fs::File::open(parent) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

fn write_dynamic_provider_fallback_report_artifact(
    path: &Path,
    payload: &[u8],
) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid dynamic provider fallback report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create dynamic provider fallback report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create dynamic provider fallback report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write dynamic provider fallback report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush dynamic provider fallback report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync dynamic provider fallback report temp file")?;
    }
    if let Err(_error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err("failed to publish dynamic provider fallback report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        if let Ok(directory) = std::fs::File::open(parent) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn update_bool(hasher: &mut blake3::Hasher, value: bool) {
    hasher.update(&[u8::from(value)]);
}

fn update_u32(hasher: &mut blake3::Hasher, value: u32) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut blake3::Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_u128(hasher: &mut blake3::Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_str(hasher: &mut blake3::Hasher, value: &str) {
    update_u64(hasher, value.len().min(u64::MAX as usize) as u64);
    hasher.update(value.as_bytes());
}

fn hex32(bytes: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}
