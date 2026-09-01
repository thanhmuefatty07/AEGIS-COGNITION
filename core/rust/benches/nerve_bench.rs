// Benchmark fixtures intentionally mirror the production proof API shape and
// use explicit ranges/casts so generated comparison tables stay readable.
#![allow(
    clippy::int_plus_one,
    clippy::manual_range_contains,
    clippy::too_many_arguments,
    clippy::unnecessary_cast
)]

use aegis_nerve::bridge_mmap::{open_mmap_bridge_view, pattern_byte, write_mmap_bridge_frame};
use aegis_nerve::browser_witness::{
    BrowserActionKind, BrowserActionPlanKind, BrowserActionPlanRecord, BrowserActionTrace,
    BrowserArtifactFilePathRef, BrowserArtifactKind, BrowserArtifactRef,
    BrowserCollectorEvidenceEnvelope, BrowserCollectorKind, BrowserLiveCollectorArtifact,
    BrowserLiveCollectorManifest, BrowserLiveCollectorRun, BrowserObservationPacket,
    BrowserWitnessProof,
};
use aegis_nerve::circuit_breaker::{LlmLoopCircuitBreaker, LlmLoopCircuitBreakerConfig};
use aegis_nerve::context::{ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind};
use aegis_nerve::descriptor::BinaryFrameDescriptor;
use aegis_nerve::distributed::{
    CandidateArtifactRef, ClusterPartitionState, ClusterWorkEnvelope, SingleWriterRunLog,
    WorkLeaseTable, WorkerRole,
};
use aegis_nerve::evidence_index::{
    AgenticEvidenceProgram, AgenticEvidenceProgramScratch, AgenticEvidenceProgramStep,
    AgenticEvidenceSdk, CandidateEvidenceRef, CandidateStateCapsule,
    ColdVectorExpansionReplayRecord, ColdVectorIndex, EvidenceCandidateTier, HotBitmapFilter,
    HotEvidenceIndex, HotEvidenceQueryScratch, HotLexicalIndex, HotLexicalQueryScratch,
    HotTermDictionary, IndexEpochReplayRecord, SortedEvidenceSet, browser_page_search_pattern_hash,
};
use aegis_nerve::goal_intake::GoalIntakeProof;
use aegis_nerve::hot_engine::{InMemoryEvidenceArena, TrustLevel, simd_blake3_hash};
use aegis_nerve::ipc::{message_to_zero_copy, validate_zero_copy, zero_copy_to_message};
use aegis_nerve::layout::{expected_header_bytes, expected_payload_alignment, validate_layout};
use aegis_nerve::learning::{LearningEventType, LearningLedger};
use aegis_nerve::llm::{
    AdapterRegistry, ContinuousBatchCandidate, ContinuousBatchPlanner, ContinuousBatchPolicy,
    InferenceBackendContract, InferenceRouteProof, LLMRequest, PrefixCacheBroker, ProviderAdapter,
    ProviderBudgetLedger, ProviderConfig, ProviderRuntimeBudget, SpeculativeDecodeVerifier,
    SpeculativeTokenBatch, StructuredOutputFieldSpec, StructuredOutputKind, StructuredOutputProof,
    StructuredOutputSchema, normalize_response,
};
use aegis_nerve::memory::fold::{GenerationalSlab, RuntimeLayoutBudget};
use aegis_nerve::message::MessageFrame;
use aegis_nerve::orchestrator::NerveRuntime;
use aegis_nerve::physical::{
    PAVWatchdog, PhysicalWatchdog, compute_ast_structural_fingerprint,
    compute_ast_tree_edit_distance,
};
use aegis_nerve::policy::{
    CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision, PolicyFacts,
    RiskClass, SideEffectClass, TypedToolIR,
};
// LicenseManager removed in Phase 1A cleanup — unused in benches (verified by clippy 2026-06-15).
use aegis_nerve::memory::nudge::{MemoryCandidate, MemoryNudgeSystem};
use aegis_nerve::replay::{
    AgenticEvidenceSdkRunHandoffProof, BinaryRunEventSegment, NextActionKind, NextActionPacket,
    ReplayDeterminismProof, ReplayEnduranceBench, RunCheckpoint, RunEventLedger,
    RunEventSegmentArchive, SegmentedArrowAuditStream, ToolExecutionEvidence, ToolExecutionStatus,
    ToolExecutorKind, agentic_evidence_sdk_run_replay_binding_hash,
    skill_admission_handoff_proof_hash, skill_admission_replay_binding_hash,
};
use aegis_nerve::sandbox::{
    QUICKJS_INVOCATION_ABI_HEADER_BYTES, QuickJsWasmInterpreterManager, WasmtimeSandbox,
};
use aegis_nerve::skill_registry::{
    ActivatedSkillRecord, SkillAdmissionRecord, SkillExecutionHandoffProof, SkillExecutionProof,
    SkillPackageManifest, SkillRegistry, SkillRegressionCase, SkillRegressionReport,
    SkillUsageStats,
};
use aegis_nerve::task_ledger::{TaskCard, TaskLedger};
use aegis_nerve::tool_gateway::{ToolExecutionGateway, evidence_contract_hash};
use criterion::{BatchSize, Criterion, black_box, criterion_group, criterion_main};
use std::path::PathBuf;

struct BenchAdapter;

static BENCH_STRUCTURED_FIELDS: &[StructuredOutputFieldSpec] = &[
    StructuredOutputFieldSpec {
        name: "artifact_hash",
        kind: StructuredOutputKind::Hash32,
        required: true,
    },
    StructuredOutputFieldSpec {
        name: "candidate",
        kind: StructuredOutputKind::String,
        required: true,
    },
    StructuredOutputFieldSpec {
        name: "risk",
        kind: StructuredOutputKind::Integer,
        required: true,
    },
    StructuredOutputFieldSpec {
        name: "verified",
        kind: StructuredOutputKind::Boolean,
        required: true,
    },
];

impl ProviderAdapter for BenchAdapter {
    fn provider_id(&self) -> &'static str {
        "bench"
    }

    fn model_name(&self) -> &'static str {
        "bench-model"
    }

    fn supports(&self, request: &LLMRequest) -> bool {
        request.is_valid()
    }

    fn reliability_score(&self) -> u8 {
        8
    }

    fn send(&self, request: &LLMRequest) -> Result<aegis_nerve::llm::LLMResponse, &'static str> {
        Ok(aegis_nerve::llm::normalize_response(
            request,
            "bench response",
            4,
            2,
            0.9,
        ))
    }
}

fn bench_descriptor_validation(c: &mut Criterion) {
    c.bench_function("descriptor_validation", |b| {
        b.iter(|| {
            let descriptor = BinaryFrameDescriptor::new(black_box(3));
            black_box(descriptor.is_valid())
        })
    });
}

fn bench_goal_intake_classify_packet(c: &mut Criterion) {
    let operator_id_hash = bench_hash("goal-intake-operator", 1);
    let raw_goal_ref_hash = bench_hash("goal-intake-raw-ref", 1);
    let policy_window_hash = bench_hash("goal-intake-policy-window", 1);
    let goal_text =
        "Use browser to inspect the website, fetch evidence, and avoid submit or upload actions";

    c.bench_function("goal_intake_classify_packet", |b| {
        b.iter(|| {
            let proof = GoalIntakeProof::from_goal_text(
                black_box(31),
                black_box(operator_id_hash),
                black_box(raw_goal_ref_hash),
                black_box(policy_window_hash),
                black_box(goal_text),
                black_box(1),
                black_box(Some(1_786_400_100_000)),
            )
            .expect("goal intake proof");
            black_box((
                proof.is_valid(),
                proof.packet.packet_hash,
                proof.initial_ir.canonical_hash,
                proof.root_task.task_id,
            ))
        })
    });
}

fn bench_layout_validation(c: &mut Criterion) {
    c.bench_function("layout_validation", |b| {
        b.iter(|| {
            black_box(validate_layout(
                expected_header_bytes(),
                expected_payload_alignment(),
            ))
        })
    });
}

fn bench_message_to_zero_copy(c: &mut Criterion) {
    c.bench_function("message_to_zero_copy", |b| {
        b.iter(|| {
            let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3, 4]);
            black_box(message_to_zero_copy(&message).is_ok())
        })
    });
}

fn bench_zero_copy_roundtrip(c: &mut Criterion) {
    let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3, 4]);

    c.bench_function("zero_copy_roundtrip", |b| {
        b.iter(|| {
            let zero_copy =
                message_to_zero_copy(black_box(&message)).expect("message should be valid");
            let rebuilt =
                zero_copy_to_message(black_box(&zero_copy)).expect("zero-copy should be valid");
            black_box((rebuilt.header.payload_len, rebuilt.payload))
        })
    });
}

fn bench_zero_copy_validation(c: &mut Criterion) {
    let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3, 4]);
    let zero_copy = message_to_zero_copy(&message).expect("message should be valid");

    c.bench_function("zero_copy_validation", |b| {
        b.iter(|| {
            black_box(
                validate_zero_copy(black_box(&zero_copy), &aegis_nerve::schema::NERVE_SCHEMA)
                    .is_ok(),
            )
        })
    });
}

fn bench_runtime_ingest(c: &mut Criterion) {
    c.bench_function("runtime_ingest", |b| {
        b.iter(|| {
            let mut runtime = NerveRuntime::new();
            let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3, 4]);
            black_box(runtime.ingest_message(message).is_ok())
        })
    });
}

fn bench_runtime_ingest_batch(c: &mut Criterion) {
    c.bench_function("runtime_ingest_batch", |b| {
        let messages: Vec<MessageFrame> = (1..=128)
            .map(|index| {
                MessageFrame::with_identity(index as u128, (index + 1) as u128, vec![1, 2, 3, 4])
            })
            .collect();
        b.iter(|| {
            let mut runtime = NerveRuntime::with_memory_capacity(messages.len());
            for message in &messages {
                let _ = runtime.ingest_message_ref(message);
            }
            black_box(runtime.processed_count())
        })
    });
}

fn bench_llm_route(c: &mut Criterion) {
    c.bench_function("llm_route", |b| {
        let request = LLMRequest {
            request_id: 1,
            session_id: 2,
            prompt: "bench llm".to_string(),
            system_context: None,
            model_hint: Some("bench-model"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let providers = [ProviderConfig {
            provider_id: "bench-provider",
            model_name: "bench-model",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        }];
        b.iter(|| black_box(aegis_nerve::llm::route_request(&request, &providers).is_some()))
    });
}

fn bench_llm_runtime_checkpoint(c: &mut Criterion) {
    c.bench_function("llm_runtime_checkpoint", |b| {
        let request = LLMRequest {
            request_id: 3,
            session_id: 4,
            prompt: "bench llm checkpoint".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let adapter = BenchAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        b.iter(|| {
            let mut runtime = NerveRuntime::new();
            black_box(
                runtime
                    .process_llm_request_with_checkpoint(&request, &registry)
                    .is_ok(),
            )
        })
    });
}

fn bench_inference_route_contract_proof(c: &mut Criterion) {
    let request = LLMRequest {
        request_id: 13,
        session_id: 14,
        prompt: "bench inference route proof".to_string(),
        system_context: None,
        model_hint: Some("bench-model"),
        tool_hint: Some("browser"),
        prefer_low_latency: true,
        prefer_low_cost: true,
        require_tool_use: true,
        require_reliability: true,
    };
    let providers = [
        ProviderConfig {
            provider_id: "bench-provider",
            model_name: "bench-model",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        },
        ProviderConfig {
            provider_id: "backup-provider",
            model_name: "backup-model",
            endpoint: "http://localhost:5678",
            timeout_ms: 1500,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 2,
            availability_rank: 2,
        },
    ];
    let contracts = [
        InferenceBackendContract::from_provider(
            &providers[0],
            "bench-backend-v1",
            bench_hash("tokenizer", 1),
            bench_hash("config", 1),
            bench_hash("endpoint-class", 1),
            32_768,
            4_096,
            true,
            false,
        )
        .expect("bench primary contract"),
        InferenceBackendContract::from_provider(
            &providers[1],
            "bench-backend-v1",
            bench_hash("tokenizer", 2),
            bench_hash("config", 2),
            bench_hash("endpoint-class", 2),
            32_768,
            4_096,
            true,
            false,
        )
        .expect("bench fallback contract"),
    ];

    c.bench_function("inference_route_contract_proof", |b| {
        b.iter(|| {
            let proof = InferenceRouteProof::build(
                black_box(&request),
                black_box(&providers),
                black_box(&contracts),
            )
            .expect("bench inference route proof");
            black_box((proof.proof_hash, proof.fallback_contract_hash))
        })
    });
}

fn bench_provider_route_admission_proof(c: &mut Criterion) {
    let request = LLMRequest {
        request_id: 17,
        session_id: 18,
        prompt: "bench provider budget admission".to_string(),
        system_context: None,
        model_hint: Some("reserve-model"),
        tool_hint: None,
        prefer_low_latency: true,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    };
    let providers = [
        ProviderConfig {
            provider_id: "fast",
            model_name: "fast-model",
            endpoint: "http://fast",
            timeout_ms: 500,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        },
        ProviderConfig {
            provider_id: "reserve",
            model_name: "reserve-model",
            endpoint: "http://reserve",
            timeout_ms: 1000,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 4,
            availability_rank: 1,
        },
    ];
    let ledger = ProviderBudgetLedger::new(&[
        ProviderRuntimeBudget::new("fast", "fast-model", 0, 10_000, 1_000_000).unwrap(),
        ProviderRuntimeBudget::new("reserve", "reserve-model", 10, 10_000, 1_000_000).unwrap(),
    ])
    .unwrap();
    c.bench_function("provider_route_admission_proof", |b| {
        b.iter(|| {
            let result: Result<
                (_, aegis_nerve::llm::ProviderRouteAdmissionProof),
                aegis_nerve::llm::ProviderBudgetError,
            > = aegis_nerve::llm::route_request_with_budget(&request, &providers, &ledger, 1_024);
            let (_decision, proof) = result.expect("provider route admission proof");
            black_box(proof.proof_hash)
        })
    });
}

fn bench_prefix_cache_broker_hit_proof(c: &mut Criterion) {
    let providers = [ProviderConfig {
        provider_id: "bench-provider",
        model_name: "bench-model",
        endpoint: "http://localhost:1234",
        timeout_ms: 1000,
        retry_limit: 3,
        preferred_for_tools: true,
        cost_rank: 1,
        availability_rank: 1,
    }];
    let request = LLMRequest {
        request_id: 23,
        session_id: 24,
        prompt: "bench prefix cache broker\nsuffix A".to_string(),
        system_context: None,
        model_hint: Some("bench-model"),
        tool_hint: Some("browser"),
        prefer_low_latency: true,
        prefer_low_cost: true,
        require_tool_use: true,
        require_reliability: true,
    };
    let mut lookup_request = request.clone();
    lookup_request.request_id += 1;
    lookup_request.prompt = "bench prefix cache broker\nsuffix B".to_string();
    let prefix_len = "bench prefix cache broker\n".len() as u32;
    let contract = InferenceBackendContract::from_provider(
        &providers[0],
        "bench-backend-v1",
        bench_hash("tokenizer", 3),
        bench_hash("config", 3),
        bench_hash("endpoint-class", 3),
        32_768,
        4_096,
        true,
        false,
    )
    .expect("bench prefix cache contract");
    let route_proof =
        InferenceRouteProof::build(&request, &providers, &[contract]).expect("bench route proof");
    let lookup_route_proof = InferenceRouteProof::build(&lookup_request, &providers, &[contract])
        .expect("bench lookup route proof");
    let mut broker = PrefixCacheBroker::new(16).expect("bench prefix cache broker");
    let handle = broker
        .admit(
            &request,
            &contract,
            &route_proof,
            bench_hash("cache-scope", 1),
            prefix_len,
            5,
        )
        .expect("bench prefix cache admit");

    c.bench_function("prefix_cache_broker_hit_proof", |b| {
        b.iter(|| {
            let proof = broker
                .lookup(
                    black_box(&lookup_request),
                    black_box(&contract),
                    black_box(&lookup_route_proof),
                    black_box(bench_hash("cache-scope", 1)),
                    black_box(prefix_len),
                    black_box(5),
                )
                .expect("bench prefix cache hit proof");
            black_box((proof.proof_hash, handle.handle_hash, broker.epoch()))
        })
    });
}

fn bench_structured_output_proof(c: &mut Criterion) {
    let request = LLMRequest {
        request_id: 33,
        session_id: 34,
        prompt: "bench structured output proof".to_string(),
        system_context: None,
        model_hint: Some("bench-model"),
        tool_hint: None,
        prefer_low_latency: true,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    };
    let provider = ProviderConfig {
        provider_id: "bench-provider",
        model_name: "bench-model",
        endpoint: "http://localhost:1234",
        timeout_ms: 1000,
        retry_limit: 3,
        preferred_for_tools: false,
        cost_rank: 1,
        availability_rank: 1,
    };
    let contract = InferenceBackendContract::from_provider(
        &provider,
        "bench-backend-v1",
        bench_hash("tokenizer", 4),
        bench_hash("config", 4),
        bench_hash("endpoint-class", 4),
        32_768,
        4_096,
        true,
        false,
    )
    .expect("bench structured contract");
    let response = normalize_response(
        &request,
        r#"{"artifact_hash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","candidate":"tool proposal","risk":2,"verified":false}"#,
        21,
        7,
        0.8,
    );
    let response_proof =
        aegis_nerve::llm::InferenceResponseProof::build(&request, &response, &contract)
            .expect("bench response proof");
    let schema =
        StructuredOutputSchema::new("bench-candidate-output", 1, BENCH_STRUCTURED_FIELDS, false)
            .expect("bench structured schema");

    c.bench_function("structured_output_proof", |b| {
        b.iter(|| {
            let proof = StructuredOutputProof::build(
                black_box(&request),
                black_box(&response),
                black_box(&contract),
                black_box(&response_proof),
                black_box(&schema),
                black_box(true),
            )
            .expect("bench structured output proof");
            black_box((proof.proof_hash, proof.canonical_payload_hash))
        })
    });
}

fn bench_continuous_batch_fairness_proof(c: &mut Criterion) {
    let provider_a = ProviderConfig {
        provider_id: "bench-provider-a",
        model_name: "bench-model-a",
        endpoint: "http://localhost:1234",
        timeout_ms: 1000,
        retry_limit: 3,
        preferred_for_tools: false,
        cost_rank: 1,
        availability_rank: 1,
    };
    let provider_b = ProviderConfig {
        provider_id: "bench-provider-b",
        model_name: "bench-model-b",
        endpoint: "http://localhost:5678",
        timeout_ms: 1000,
        retry_limit: 3,
        preferred_for_tools: false,
        cost_rank: 1,
        availability_rank: 1,
    };
    let contract_a = InferenceBackendContract::from_provider(
        &provider_a,
        "bench-backend-v1",
        bench_hash("batch-tokenizer", 1),
        bench_hash("batch-config", 1),
        bench_hash("batch-endpoint", 1),
        32_768,
        4_096,
        true,
        false,
    )
    .expect("bench batch contract a");
    let contract_b = InferenceBackendContract::from_provider(
        &provider_b,
        "bench-backend-v1",
        bench_hash("batch-tokenizer", 2),
        bench_hash("batch-config", 2),
        bench_hash("batch-endpoint", 2),
        32_768,
        4_096,
        true,
        false,
    )
    .expect("bench batch contract b");
    let policy = ContinuousBatchPolicy::new(8, 192, 192, 10_000).expect("bench batch policy");
    let candidates = [
        bench_batch_candidate(
            301,
            &provider_b,
            &contract_b,
            "batch oldest b",
            16,
            16,
            1,
            1_000,
        ),
        bench_batch_candidate(
            302,
            &provider_a,
            &contract_a,
            "batch newer a",
            18,
            16,
            2,
            1_010,
        ),
        bench_batch_candidate(
            303,
            &provider_b,
            &contract_b,
            "batch newer b",
            20,
            16,
            3,
            1_020,
        ),
        bench_batch_candidate(
            304,
            &provider_b,
            &contract_b,
            "batch newer b2",
            22,
            16,
            4,
            1_030,
        ),
        bench_batch_candidate(
            305,
            &provider_a,
            &contract_a,
            "batch newer a2",
            24,
            16,
            5,
            1_040,
        ),
    ];

    c.bench_function("continuous_batch_fairness_proof", |b| {
        b.iter(|| {
            let proof = ContinuousBatchPlanner::build_proof(
                black_box(44),
                black_box(1_500),
                black_box(&policy),
                black_box(&candidates),
            )
            .expect("bench continuous batch proof");
            black_box((
                proof.proof_hash,
                proof.selected_list_hash,
                proof.deferred_list_hash,
            ))
        })
    });
}

fn bench_speculative_decode_equivalence_proof(c: &mut Criterion) {
    let target_provider = ProviderConfig {
        provider_id: "bench-target-provider",
        model_name: "bench-target-model",
        endpoint: "http://localhost:1234",
        timeout_ms: 1000,
        retry_limit: 3,
        preferred_for_tools: false,
        cost_rank: 1,
        availability_rank: 1,
    };
    let draft_provider = ProviderConfig {
        provider_id: "bench-draft-provider",
        model_name: "bench-draft-model",
        endpoint: "http://localhost:5678",
        timeout_ms: 1000,
        retry_limit: 3,
        preferred_for_tools: false,
        cost_rank: 1,
        availability_rank: 1,
    };
    let request = LLMRequest {
        request_id: 45,
        session_id: 46,
        prompt: "bench speculative decode equivalence proof".to_string(),
        system_context: None,
        model_hint: Some("bench-target-model"),
        tool_hint: None,
        prefer_low_latency: true,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    };
    let target_contract = InferenceBackendContract::from_provider(
        &target_provider,
        "bench-backend-v1",
        bench_hash("spec-tokenizer", 1),
        bench_hash("spec-config", 1),
        bench_hash("spec-endpoint", 1),
        32_768,
        4_096,
        true,
        true,
    )
    .expect("bench speculative target contract");
    let draft_contract = InferenceBackendContract::from_provider(
        &draft_provider,
        "bench-backend-v1",
        target_contract.tokenizer_hash,
        target_contract.config_hash,
        target_contract.endpoint_class_hash,
        32_768,
        4_096,
        true,
        true,
    )
    .expect("bench speculative draft contract");
    let route_proof = InferenceRouteProof::build(&request, &[target_provider], &[target_contract])
        .expect("bench speculative route proof");
    let draft =
        SpeculativeTokenBatch::build(&request, &draft_contract, &[11, 22, 33, 44, 55, 66, 77, 88])
            .expect("bench speculative draft batch");
    let target =
        SpeculativeTokenBatch::build(&request, &target_contract, &[11, 22, 33, 44, 57, 68])
            .expect("bench speculative target batch");

    c.bench_function("speculative_decode_equivalence_proof", |b| {
        b.iter(|| {
            let proof = SpeculativeDecodeVerifier::build_proof(
                black_box(&request),
                black_box(&route_proof),
                black_box(&draft_contract),
                black_box(&target_contract),
                black_box(&draft),
                black_box(&target),
                black_box(6),
            )
            .expect("bench speculative equivalence proof");
            black_box((proof.proof_hash, proof.accepted_prefix_hash))
        })
    });
}

fn bench_llm_loop_circuit_breaker_observe(c: &mut Criterion) {
    let request = LLMRequest {
        request_id: 5,
        session_id: 6,
        prompt: "bench loop circuit breaker".to_string(),
        system_context: None,
        model_hint: None,
        tool_hint: None,
        prefer_low_latency: false,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    };
    let responses: Vec<_> = (0..8)
        .map(|index| {
            let content = format!("bench response {index}");
            normalize_response(&request, &content, 4, 2, 0.9)
        })
        .collect();
    let config = LlmLoopCircuitBreakerConfig::bounded(3, responses.len()).unwrap();

    c.bench_function("llm_loop_circuit_breaker_observe", |b| {
        let mut index = 0usize;
        let mut breaker = LlmLoopCircuitBreaker::new(config).expect("bench circuit breaker config");
        b.iter(|| {
            index = index.wrapping_add(1);
            let response = &responses[index % responses.len()];
            let decision = breaker
                .observe_response(black_box(response))
                .expect("bench llm loop circuit breaker observe");
            black_box((decision.kind, decision.evidence_hash))
        })
    });
}

fn bench_quickjs_invocation_abi_prepare(c: &mut Criterion) {
    let wasm = quickjs_bench_wasm();
    let manager = QuickJsWasmInterpreterManager::new(wasm).expect("quickjs wasm wrapper");
    let script = "globalThis.answer = 42; globalThis.done = true;";

    c.bench_function("quickjs_invocation_abi_prepare", |b| {
        b.iter(|| {
            let invocation = manager
                .prepare_invocation_with_fuel(black_box(script), black_box(10_000))
                .expect("quickjs invocation abi packet");
            black_box((invocation.abi_packet.len(), invocation.invocation_blake3))
        })
    });
}

fn bench_quickjs_linear_memory_write(c: &mut Criterion) {
    let wasm = quickjs_bench_wasm();
    let manager = QuickJsWasmInterpreterManager::new(wasm).expect("quickjs wasm wrapper");
    let invocation = manager
        .prepare_invocation_with_fuel("globalThis.answer = 42; globalThis.done = true;", 10_000)
        .expect("quickjs invocation abi packet");
    let plan = invocation
        .linear_memory_plan(1)
        .expect("quickjs linear memory plan");
    let mut memory = vec![0u8; plan.memory_bytes];

    c.bench_function("quickjs_linear_memory_write", |b| {
        b.iter(|| {
            let written_hash = invocation
                .write_to_linear_memory(black_box(&mut memory), black_box(&plan))
                .expect("quickjs linear memory write");
            black_box((written_hash, plan.packet_bytes))
        })
    });
}

fn bench_quickjs_wasmtime_bridge_validate(c: &mut Criterion) {
    let script = "globalThis.answer = 42; globalThis.done = true;";
    let packet_len = QUICKJS_INVOCATION_ABI_HEADER_BYTES + script.len();
    let wasm = quickjs_bridge_bench_wasm(packet_len);
    let manager = QuickJsWasmInterpreterManager::new(wasm).expect("quickjs bridge wrapper");
    let invocation = manager
        .prepare_invocation_with_fuel(script, 10_000)
        .expect("quickjs invocation abi packet");
    let sandbox = WasmtimeSandbox::new();

    c.bench_function("quickjs_wasmtime_bridge_validate", |b| {
        b.iter(|| {
            let result = sandbox
                .execute_quickjs_invocation_bridge(black_box(&invocation), black_box(10_000))
                .expect("quickjs wasmtime bridge validation");
            black_box((result.fuel_consumed, result.artifact.artifact_hash))
        })
    });
}

fn bench_wasmtime_execute_cached_module(c: &mut Criterion) {
    let wasm = wasmtime_cached_bench_wasm();
    let sandbox = WasmtimeSandbox::new();
    let warm = sandbox
        .execute_wasm_binary(&wasm, 10_000)
        .expect("warm cached wasmtime module");
    assert!(warm.is_valid());

    c.bench_function("wasmtime_execute_cached_module", |b| {
        b.iter(|| {
            let result = sandbox
                .execute_wasm_binary(black_box(&wasm), black_box(10_000))
                .expect("cached wasmtime module execution");
            black_box((result.fuel_consumed, result.artifact.artifact_hash))
        })
    });
}

fn bench_skill_admission_record_validate(c: &mut Criterion) {
    let (manifest, admission, report) = skill_admission_bench_fixture();
    c.bench_function("skill_admission_record_validate", |b| {
        b.iter(|| black_box(admission.is_valid_for(black_box(&manifest), black_box(&report))));
    });

    c.bench_function("skill_registry_commit_admitted_skill", |b| {
        b.iter_batched(
            SkillRegistry::new,
            |mut registry| {
                black_box(
                    registry
                        .commit_admitted_skill(
                            black_box(&manifest),
                            black_box(&admission),
                            black_box(&report),
                        )
                        .expect("skill registry commit bench"),
                );
            },
            BatchSize::SmallInput,
        )
    });

    let admitted = SkillRegistry::new()
        .commit_admitted_skill(&manifest, &admission, &report)
        .expect("skill registry commit binding bench");
    c.bench_function("skill_admission_replay_binding_hash", |b| {
        b.iter(|| {
            black_box(skill_admission_replay_binding_hash(
                black_box(&admission),
                black_box(&admitted),
            ))
        })
    });

    c.bench_function("skill_admission_replay_append", |b| {
        b.iter_batched(
            || {
                let mut ledger = RunEventLedger::new(9_001);
                let proof = GoalIntakeProof::from_goal_text(
                    9_001,
                    bench_hash("skill-admission-replay-operator", 1),
                    bench_hash("skill-admission-replay-raw-goal", 1),
                    bench_hash("skill-admission-replay-policy-window", 1),
                    "Admit a Wasmtime-gated skill into the replay-visible registry",
                    1,
                    None,
                )
                .expect("skill admission replay bench goal");
                ledger
                    .append_goal_intake_recorded(&proof)
                    .expect("skill admission replay bench intake");
                ledger
            },
            |mut ledger| {
                black_box(
                    ledger
                        .append_skill_admission_recorded(
                            black_box(&admission),
                            black_box(&admitted),
                        )
                        .expect("skill admission replay bench append")
                        .event_hash,
                )
            },
            BatchSize::SmallInput,
        )
    });

    let admitted = SkillRegistry::new()
        .commit_admitted_skill(&manifest, &admission, &report)
        .expect("skill admission handoff bench commit");
    let mut ledger = RunEventLedger::new(9_002);
    seed_bench_goal_intake(&mut ledger, "skill-admission-handoff");
    let skill_event = ledger
        .append_skill_admission_recorded(&admission, &admitted)
        .expect("skill admission handoff bench event")
        .clone();
    let replay_proof = bench_replay_determinism_from_ledger(&ledger, "skill-admission-handoff");
    let checkpoint = RunCheckpoint::new(
        ledger.run_id,
        901,
        replay_proof.event_count,
        replay_proof.segment_count,
        replay_proof.first_pass_ledger_hash,
        replay_proof.manifest_hash,
        replay_proof.proof_hash,
        skill_event.event_hash,
    );
    let packet = NextActionPacket::new(
        ledger.run_id,
        902,
        checkpoint.checkpoint_hash,
        NextActionKind::ContinueExecution,
        903,
        bench_hash("skill-handoff-typed-tool-ir", 1),
        bench_hash("skill-handoff-evidence-contract", 1),
        bench_hash("skill-handoff-policy-proof", 1),
        skill_event.event_hash,
    );
    let handoff = aegis_nerve::replay::SkillAdmissionHandoffProof::new(
        &skill_event,
        &admission,
        &admitted,
        &replay_proof,
        &checkpoint,
        &packet,
    );
    assert!(handoff.is_valid_for(
        &skill_event,
        &admission,
        &admitted,
        &replay_proof,
        &checkpoint,
        &packet
    ));

    c.bench_function("skill_admission_handoff_proof_hash", |b| {
        b.iter(|| {
            black_box(skill_admission_handoff_proof_hash(
                black_box(handoff.run_id),
                black_box(handoff.skill_id),
                black_box(handoff.skill_admission_event_id),
                black_box(handoff.skill_admission_event_hash),
                black_box(handoff.package_hash),
                black_box(handoff.admission_hash),
                black_box(handoff.registry_epoch),
                black_box(handoff.registry_commit_hash),
                black_box(handoff.replay_determinism_proof_hash),
                black_box(handoff.checkpoint_hash),
                black_box(handoff.next_action_packet_hash),
                black_box(handoff.activation_hash),
            ))
        })
    });

    c.bench_function("skill_admission_handoff_validate", |b| {
        b.iter(|| {
            black_box(handoff.is_valid_for(
                black_box(&skill_event),
                black_box(&admission),
                black_box(&admitted),
                black_box(&replay_proof),
                black_box(&checkpoint),
                black_box(&packet),
            ))
        })
    });

    c.bench_function("skill_registry_activate_replay_sealed_skill", |b| {
        b.iter_batched(
            || {
                let mut registry = SkillRegistry::new();
                registry
                    .commit_admitted_skill(&manifest, &admission, &report)
                    .expect("bench admitted skill commit");
                registry
            },
            |mut registry| {
                let activated: ActivatedSkillRecord = registry
                    .activate_replay_sealed_skill(black_box(manifest.skill_id), black_box(&handoff))
                    .expect("bench skill activation");
                black_box(activated.is_valid_for(black_box(&admitted), black_box(&handoff)))
            },
            BatchSize::SmallInput,
        )
    });

    let skill_ir = TypedToolIR::new(
        manifest.skill_id,
        77,
        CapabilityClass::LocalWrite,
        aegis_nerve::policy::SideEffectClass::LocalReversible,
        bench_hash("skill-exec-credential", 1),
        aegis_nerve::policy::RiskClass::R1,
        handoff.activation_hash,
        admission.admission_hash,
        EvidenceContract {
            requires_physical_witness: true,
            requires_approval: false,
            requires_staging: false,
            expected_artifact_hash: Some(admission.wasmtime_artifact_hash),
        },
        None,
    );
    let facts = PolicyFacts::new(bench_hash("skill-exec-policy", 1), false, false, false);
    let policy_trace = DeterministicPolicyKernel.evaluate(&skill_ir, &facts);
    let wasm = wasmtime_cached_bench_wasm();
    let sandbox = WasmtimeSandbox::new();
    c.bench_function("skill_active_wasm_execution_proof", |b| {
        b.iter_batched(
            || {
                let mut registry = SkillRegistry::new();
                registry
                    .commit_admitted_skill(&manifest, &admission, &report)
                    .expect("bench admitted skill commit");
                registry
                    .activate_replay_sealed_skill(manifest.skill_id, &handoff)
                    .expect("bench skill activation");
                let mut ledger = RunEventLedger::new(9_003);
                seed_bench_goal_intake(&mut ledger, "skill-active-exec");
                ledger
                    .append_context_pack_built(70, 128, bench_hash("skill-active-exec-context", 1))
                    .expect("skill exec context");
                ledger
                    .append_llm_response_received(
                        bench_hash("skill-active-exec-response", 1),
                        bench_hash("skill-active-exec-raw-ref", 1),
                    )
                    .expect("skill exec llm response");
                (registry, ledger)
            },
            |(registry, mut ledger)| {
                let proof: SkillExecutionProof = registry
                    .execute_active_skill_with_replay(
                        &mut ledger,
                        black_box(&sandbox),
                        black_box(manifest.skill_id),
                        black_box(&handoff),
                        black_box(&admission),
                        black_box(7_001),
                        black_box(7_002),
                        black_box(&skill_ir),
                        black_box(&policy_trace),
                        black_box(&wasm),
                        black_box(10_000),
                    )
                    .expect("bench active skill execution");
                black_box(proof.proof_hash)
            },
            BatchSize::SmallInput,
        )
    });

    c.bench_function("skill_execution_handoff_seal", |b| {
        b.iter_batched(
            || {
                let mut registry = SkillRegistry::new();
                registry
                    .commit_admitted_skill(&manifest, &admission, &report)
                    .expect("bench admitted skill commit");
                registry
                    .activate_replay_sealed_skill(manifest.skill_id, &handoff)
                    .expect("bench skill activation");
                let mut ledger = RunEventLedger::new(9_004);
                seed_bench_goal_intake(&mut ledger, "skill-exec-handoff");
                ledger
                    .append_context_pack_built(70, 128, bench_hash("skill-exec-handoff-context", 1))
                    .expect("skill execution handoff context");
                ledger
                    .append_llm_response_received(
                        bench_hash("skill-exec-handoff-response", 1),
                        bench_hash("skill-exec-handoff-raw-ref", 1),
                    )
                    .expect("skill execution handoff llm response");
                let execution = registry
                    .execute_active_skill_with_replay(
                        &mut ledger,
                        &sandbox,
                        manifest.skill_id,
                        &handoff,
                        &admission,
                        7_003,
                        7_004,
                        &skill_ir,
                        &policy_trace,
                        &wasm,
                        10_000,
                    )
                    .expect("bench active skill execution");
                let dir = std::env::current_dir()
                    .expect("bench current dir")
                    .join("target")
                    .join("aegis-bench-artifacts")
                    .join("skill-exec-handoff-seal");
                let _ = std::fs::remove_dir_all(&dir);
                let manifest_archive =
                    RunEventSegmentArchive::write_ledger(&dir, 8, &ledger).expect("bench archive");
                (dir, manifest_archive, execution)
            },
            |(dir, manifest_archive, execution)| {
                let handoff_proof: SkillExecutionHandoffProof =
                    SkillRegistry::seal_skill_execution_handoff(
                        black_box(&dir),
                        black_box(&manifest_archive),
                        black_box(&execution),
                        black_box(&skill_ir),
                        black_box(&policy_trace),
                        black_box(7_005),
                        black_box(7_006),
                        black_box(NextActionKind::ContinueExecution),
                        black_box(7_007),
                        black_box(evidence_contract_hash(&skill_ir)),
                    )
                    .expect("bench skill execution handoff");
                black_box(handoff_proof.replay_handoff_hash)
            },
            BatchSize::SmallInput,
        )
    });
}

fn bench_pav_ast_distance_registered(c: &mut Criterion) {
    let old_code = generated_ast_bench_code(48, 7);
    let new_code = generated_ast_bench_code(48, 11);
    let old_fingerprint = compute_ast_structural_fingerprint(&old_code);
    let new_fingerprint = compute_ast_structural_fingerprint(&new_code);
    let expected_distance = compute_ast_tree_edit_distance(&old_code, &new_code)
        .expect("bench ast edit distance should parse");
    let watchdog = PhysicalWatchdog { epsilon: 0.0001 };
    let warm_pav = watchdog.measure_pav(old_fingerprint, new_fingerprint, 10_000);
    assert!(expected_distance > 0);
    assert!(warm_pav > 0.0);

    c.bench_function("pav_ast_distance_registered", |b| {
        b.iter(|| {
            let pav = watchdog.measure_pav(
                black_box(old_fingerprint),
                black_box(new_fingerprint),
                black_box(10_000),
            );
            black_box((pav, expected_distance))
        })
    });
}

fn bench_mmap_bridge_payload_view(c: &mut Criterion) {
    let path = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("mmap-bridge-payload-view")
        .join("frame.aegmmap");
    std::fs::create_dir_all(path.parent().expect("mmap bridge bench dir"))
        .expect("create mmap bridge bench dir");
    let payload: Vec<u8> = (0..(1024 * 1024)).map(pattern_byte).collect();
    write_mmap_bridge_frame(&path, 7, 9, &payload).expect("mmap bridge bench frame");
    let view = open_mmap_bridge_view(&path).expect("mmap bridge bench view");
    assert_eq!(view.payload().expect("payload view").len(), payload.len());

    c.bench_function("mmap_bridge_payload_view", |b| {
        b.iter(|| {
            let payload = view.payload().expect("mmap bridge payload view");
            black_box((payload.as_ptr(), payload.len(), payload[0]))
        })
    });
}

fn bench_mmap_wasm_bridge_execute(c: &mut Criterion) {
    let path = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("mmap-wasm-bridge-execute")
        .join("wasm-frame.aegmmap");
    std::fs::create_dir_all(path.parent().expect("mmap wasm bridge bench dir"))
        .expect("create mmap wasm bridge bench dir");
    let wasm = wasmtime_cached_bench_wasm();
    write_mmap_bridge_frame(&path, 17, 19, &wasm).expect("mmap wasm bridge bench frame");
    let sandbox = WasmtimeSandbox::new();
    let warm = sandbox
        .execute_mmap_wasm_bridge_frame(&path, 10_000)
        .expect("warm mmap wasm bridge execution");
    assert!(warm.is_valid());

    c.bench_function("mmap_wasm_bridge_execute", |b| {
        b.iter(|| {
            let result = sandbox
                .execute_mmap_wasm_bridge_frame(black_box(&path), black_box(10_000))
                .expect("mmap wasm bridge execution");
            black_box((result.fuel_consumed, result.artifact.artifact_hash))
        })
    });
}

fn bench_replay_io_mmap_materialized(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-io-mmap-materialized");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(64);
    let manifest =
        RunEventSegmentArchive::write_ledger(&dir, 8, &ledger).expect("bench ledger archive");

    c.bench_function("replay_io_mmap_materialized", |b| {
        b.iter(|| {
            let (recovered, evidence) =
                RunEventSegmentArchive::read_ledger_mmap_with_evidence(&dir, &manifest)
                    .expect("mmap materialized replay readback");
            black_box((recovered.len(), evidence.materialized_run_event_bytes))
        })
    });
}

fn bench_replay_segmented_arrow_append(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-segmented-arrow-append");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(1);

    c.bench_function("replay_segmented_arrow_append", |b| {
        b.iter_custom(|iters| {
            let _ = std::fs::remove_dir_all(&dir);
            std::fs::create_dir_all(&dir).expect("create segmented arrow append bench dir");
            let start = std::time::Instant::now();
            for iteration in 1..=iters {
                let run_dir = dir.join(format!("iter-{iteration}"));
                let mut stream = SegmentedArrowAuditStream::create(
                    &run_dir,
                    black_box(ledger.run_id),
                    black_box(ledger.len()),
                )
                .expect("segmented arrow audit stream");
                stream
                    .append_events(black_box(ledger.events()))
                    .expect("append segmented arrow audit events");
                let manifest = stream
                    .finish()
                    .expect("finish segmented arrow audit stream");
                black_box((manifest.entries.len(), manifest.manifest_hash));
            }
            start.elapsed()
        })
    });
}

fn bench_replay_segmented_arrow_proof(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-segmented-arrow-proof");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(64);
    let manifest =
        RunEventSegmentArchive::write_ledger(&dir, 8, &ledger).expect("segmented arrow proof");

    c.bench_function("replay_segmented_arrow_proof", |b| {
        b.iter(|| {
            let proof = RunEventSegmentArchive::prove_segmented_arrow_audit(
                black_box(&dir),
                black_box(&manifest),
            )
            .expect("segmented arrow audit proof");
            black_box((
                proof.segment_count,
                proof.event_count,
                proof.file_evidence_hash,
                proof.proof_hash,
            ))
        })
    });
}

fn bench_replay_segmented_arrow_column_scan_proof(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-segmented-arrow-column-scan-proof");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(64);
    let manifest = RunEventSegmentArchive::write_ledger(&dir, 8, &ledger)
        .expect("segmented arrow column scan proof");

    c.bench_function("replay_segmented_arrow_column_scan_proof", |b| {
        b.iter(|| {
            let proof = RunEventSegmentArchive::prove_segmented_arrow_audit(
                black_box(&dir),
                black_box(&manifest),
            )
            .expect("segmented arrow mmap column scan proof");
            black_box((
                proof.event_count,
                proof.logical_replay_hash,
                proof.mmap_evidence_hash,
                proof.proof_hash,
            ))
        })
    });
}

fn bench_replay_segmented_arrow_manifest_recover(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-segmented-arrow-manifest-recover");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(64);
    let manifest = RunEventSegmentArchive::write_ledger(&dir, 8, &ledger)
        .expect("segmented arrow manifest recovery source");

    c.bench_function("replay_segmented_arrow_manifest_recover", |b| {
        b.iter(|| {
            let recovered =
                RunEventSegmentArchive::recover_manifest_from_segments(&dir, ledger.run_id)
                    .expect("recover segmented arrow manifest from physical segments");
            black_box((
                recovered.entries.len(),
                recovered.manifest_hash,
                recovered.manifest_hash == manifest.manifest_hash,
            ))
        })
    });
}

fn bench_replay_determinism_proof(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-determinism-proof");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(64);
    let manifest =
        RunEventSegmentArchive::write_ledger(&dir, 8, &ledger).expect("determinism proof source");

    c.bench_function("replay_determinism_proof", |b| {
        b.iter(|| {
            let proof = RunEventSegmentArchive::prove_replay_determinism(
                black_box(&dir),
                black_box(&manifest),
            )
            .expect("replay determinism proof");
            black_box((
                proof.event_count,
                proof.first_pass_event_sequence_hash,
                proof.first_pass_ledger_hash,
                proof.proof_hash,
            ))
        })
    });
}

fn bench_replay_segmented_arrow_compact(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-segmented-arrow-compact");
    let source_dir = dir.join("source");
    let _ = std::fs::remove_dir_all(&dir);
    let ledger = replay_io_bench_ledger(64);
    let manifest = RunEventSegmentArchive::write_ledger(&source_dir, 8, &ledger)
        .expect("segmented arrow compaction source");

    c.bench_function("replay_segmented_arrow_compact", |b| {
        let mut iteration = 0u64;
        b.iter(|| {
            iteration = iteration.wrapping_add(1);
            let output_path = dir.join(format!("compacted-{}.bin", iteration % 32));
            let report = RunEventSegmentArchive::compact_to_binary_segment_crash_safe(
                black_box(&source_dir),
                black_box(&manifest),
                black_box(&output_path),
            )
            .expect("segmented arrow compaction");
            black_box((
                report.compacted_event_count,
                report.compacted_payload_hash,
                report.report_hash,
            ))
        })
    });
}

fn bench_replay_shift_manager_next_action(c: &mut Criterion) {
    let task_id = 0xAE61_5000_0000_0001_u128;
    let mut tasks = TaskLedger::new(10_000);
    tasks
        .insert_task(TaskCard::new(task_id, vec![], 0, None, None))
        .expect("bench task insert");
    let task_selection_proof = tasks
        .select_next_task_proof(0)
        .expect("bench task selection")
        .expect("bench ready task");
    let checkpoint = RunCheckpoint::new(
        0xAEE6_5151,
        400,
        8_002,
        334,
        bench_hash("ledger-last", 1),
        bench_hash("manifest", 1),
        bench_hash("determinism-proof", 1),
        bench_hash("context-fold-evidence", 1),
    );

    c.bench_function("replay_shift_manager_next_action", |b| {
        b.iter(|| {
            let packet = NextActionPacket::new_with_task_selection(
                black_box(checkpoint.run_id),
                black_box(1),
                black_box(checkpoint.checkpoint_hash),
                black_box(NextActionKind::RequestToolCall),
                black_box(task_selection_proof.selected_task_id),
                black_box(bench_hash("typed-tool-ir", 1)),
                black_box(bench_hash("evidence-contract", 1)),
                black_box(bench_hash("policy-proof", 1)),
                black_box(bench_hash("candidate-evidence", 1)),
                black_box(&task_selection_proof),
            );
            black_box((
                packet.is_valid_for_checkpoint(black_box(&checkpoint)),
                packet.is_valid_for_task_selection(
                    black_box(&checkpoint),
                    black_box(&task_selection_proof),
                ),
                packet.packet_hash,
            ))
        })
    });
}

fn bench_browser_witness_proof_validate(c: &mut Criterion) {
    let policy_window_hash = bench_hash("browser-policy-window", 1);
    let action = BrowserActionTrace::new(
        42,
        BrowserActionKind::Click,
        bench_hash("browser-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
    );
    let proof = BrowserWitnessProof::new(
        7,
        action.action_id,
        SideEffectClass::ExternalWrite,
        bench_hash("url-before", 1),
        bench_hash("url-after", 1),
        bench_hash("dom-before", 1),
        bench_hash("dom-after", 1),
        bench_hash("screenshot-before", 1),
        bench_hash("screenshot-after", 1),
        bench_hash("accessibility-after", 1),
        bench_hash("network-log", 1),
        action.trace_hash,
        policy_window_hash,
        bench_hash("isolated-browser-session", 1),
        bench_hash("redaction-policy", 1),
    );
    c.bench_function("browser_witness_proof_validate", |b| {
        b.iter(|| {
            black_box(proof.is_valid_staging_evidence_for(black_box(&action), policy_window_hash))
        })
    });
}

fn bench_browser_witness_packet_validate(c: &mut Criterion) {
    let policy_window_hash = bench_hash("browser-packet-policy-window", 1);
    let browser_session_hash = bench_hash("browser-packet-session", 1);
    let redaction_policy_hash = bench_hash("browser-packet-redaction", 1);
    let action = BrowserActionTrace::new(
        142,
        BrowserActionKind::Click,
        bench_hash("browser-packet-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
    );
    let proof = BrowserWitnessProof::new(
        17,
        action.action_id,
        SideEffectClass::ExternalWrite,
        bench_hash("packet-url-before", 1),
        bench_hash("packet-url-after", 1),
        bench_hash("packet-dom-before", 1),
        bench_hash("packet-dom-after", 1),
        bench_hash("packet-screenshot-before", 1),
        bench_hash("packet-screenshot-after", 1),
        bench_hash("packet-accessibility-after", 1),
        bench_hash("packet-network-log", 1),
        action.trace_hash,
        policy_window_hash,
        browser_session_hash,
        redaction_policy_hash,
    );
    let packet = BrowserObservationPacket::new(
        BrowserCollectorKind::PlaywrightCdp,
        4,
        1_786_300_100_000,
        bench_hash("packet-collector-config", 1),
        bench_hash("packet-collector-capability", 1),
        bench_hash("packet-raw-artifact-manifest", 1),
        action,
        proof,
    );
    c.bench_function("browser_witness_packet_validate", |b| {
        b.iter(|| {
            black_box(packet.isolated_browser_policy_facts(
                black_box(bench_hash("browser-packet-policy-version", 1)),
                black_box(true),
                black_box(policy_window_hash),
                black_box(browser_session_hash),
                black_box(redaction_policy_hash),
            ))
        })
    });
}

fn bench_browser_collector_envelope_mint_packet(c: &mut Criterion) {
    let policy_window_hash = bench_hash("browser-envelope-policy-window", 1);
    let browser_session_hash = bench_hash("browser-envelope-session", 1);
    let redaction_policy_hash = bench_hash("browser-envelope-redaction", 1);
    let action = BrowserActionTrace::new(
        172,
        BrowserActionKind::Click,
        bench_hash("browser-envelope-selector", 1),
        None,
        Some((128, 256)),
        None,
        policy_window_hash,
    );
    let artifacts = vec![
        bench_browser_artifact(BrowserArtifactKind::NetworkLog, "envelope-network-log"),
        bench_browser_artifact(
            BrowserArtifactKind::ScreenshotAfter,
            "envelope-screenshot-after",
        ),
        bench_browser_artifact(BrowserArtifactKind::UrlBefore, "envelope-url-before"),
        bench_browser_artifact(BrowserArtifactKind::DomSnapshotAfter, "envelope-dom-after"),
        bench_browser_artifact(
            BrowserArtifactKind::AccessibilityTreeAfter,
            "envelope-accessibility-after",
        ),
        bench_browser_artifact(BrowserArtifactKind::UrlAfter, "envelope-url-after"),
        bench_browser_artifact(
            BrowserArtifactKind::ScreenshotBefore,
            "envelope-screenshot-before",
        ),
        bench_browser_artifact(
            BrowserArtifactKind::DomSnapshotBefore,
            "envelope-dom-before",
        ),
    ];
    let envelope = BrowserCollectorEvidenceEnvelope::new(
        BrowserCollectorKind::PlaywrightCdp,
        19,
        action.action_id,
        6,
        1_786_300_150_000,
        bench_hash("envelope-collector-config", 1),
        bench_hash("envelope-collector-capability", 1),
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
        artifacts,
    );
    assert!(envelope.is_valid());

    c.bench_function("browser_collector_envelope_mint_packet", |b| {
        b.iter(|| {
            let packet = BrowserObservationPacket::from_collector_envelope(
                black_box(&envelope),
                black_box(action.clone()),
                black_box(SideEffectClass::ExternalWrite),
            )
            .expect("browser envelope packet mint");
            black_box((packet.packet_hash, packet.raw_artifact_manifest_hash))
        })
    });
}

fn bench_browser_file_artifact_envelope_mint_packet(c: &mut Criterion) {
    let dir = std::env::temp_dir().join("aegis-browser-file-artifact-envelope-bench");
    let file_refs = bench_browser_artifact_files(&dir);
    let first_ref = BrowserArtifactRef::from_file(file_refs[0].0, &file_refs[0].1)
        .expect("file-backed browser artifact ref");
    assert!(first_ref.is_valid());
    let path_refs: Vec<_> = file_refs
        .iter()
        .map(|(kind, path)| {
            BrowserArtifactFilePathRef::from_path(*kind, path)
                .expect("file-backed browser artifact path ref")
        })
        .collect();
    assert!(path_refs.iter().all(BrowserArtifactFilePathRef::is_valid));
    let policy_window_hash = bench_hash("browser-file-envelope-policy-window", 1);
    let browser_session_hash = bench_hash("browser-file-envelope-session", 1);
    let redaction_policy_hash = bench_hash("browser-file-envelope-redaction", 1);
    let action = BrowserActionTrace::new(
        173,
        BrowserActionKind::Click,
        bench_hash("browser-file-envelope-selector", 1),
        None,
        Some((128, 256)),
        None,
        policy_window_hash,
    );
    let raw_path_envelope = BrowserCollectorEvidenceEnvelope::from_file_paths(
        BrowserCollectorKind::PlaywrightCdp,
        20,
        action.action_id,
        7,
        1_786_300_175_000,
        bench_hash("file-envelope-collector-config", 1),
        bench_hash("file-envelope-collector-capability", 1),
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
        &file_refs,
    )
    .expect("raw file path browser envelope");
    let path_ref_envelope = BrowserCollectorEvidenceEnvelope::from_file_path_refs(
        BrowserCollectorKind::PlaywrightCdp,
        20,
        action.action_id,
        7,
        1_786_300_175_000,
        bench_hash("file-envelope-collector-config", 1),
        bench_hash("file-envelope-collector-capability", 1),
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
        &path_refs,
    )
    .expect("path-ref browser envelope");
    assert_eq!(raw_path_envelope, path_ref_envelope);

    c.bench_function("browser_file_artifact_envelope_mint_packet", |b| {
        b.iter(|| {
            let envelope = BrowserCollectorEvidenceEnvelope::from_file_path_refs(
                black_box(BrowserCollectorKind::PlaywrightCdp),
                black_box(20),
                black_box(action.action_id),
                black_box(7),
                black_box(1_786_300_175_000),
                black_box(bench_hash("file-envelope-collector-config", 1)),
                black_box(bench_hash("file-envelope-collector-capability", 1)),
                black_box(browser_session_hash),
                black_box(redaction_policy_hash),
                black_box(policy_window_hash),
                black_box(&path_refs),
            )
            .expect("file-backed browser envelope");
            let packet = BrowserObservationPacket::from_collector_envelope(
                black_box(&envelope),
                black_box(action.clone()),
                black_box(SideEffectClass::ExternalWrite),
            )
            .expect("file-backed browser envelope packet mint");
            black_box((packet.packet_hash, packet.raw_artifact_manifest_hash))
        })
    });
}

fn bench_browser_live_collector_run_write_manifest(c: &mut Criterion) {
    let root = std::env::temp_dir().join("aegis-browser-live-collector-run-bench");
    let _ = std::fs::remove_dir_all(&root);
    let artifacts = bench_browser_live_collector_artifacts();
    let run_id = 24;
    let action_id = 184;
    let sequence_number = 5;
    let policy_window_hash = bench_hash("browser-live-run-policy-window", 1);
    let browser_session_hash = bench_hash("browser-live-run-session", 1);
    let redaction_policy_hash = bench_hash("browser-live-run-redaction", 1);
    let collector_config_hash = bench_hash("browser-live-run-config", 1);
    let collector_capability_hash = bench_hash("browser-live-run-capability", 1);

    c.bench_function("browser_live_collector_run_write_manifest", |b| {
        b.iter(|| {
            let _ = std::fs::remove_dir_all(black_box(&root));
            let collector_run = BrowserLiveCollectorRun::from_artifacts(
                black_box(&root),
                black_box(BrowserCollectorKind::PlaywrightCdp),
                black_box(run_id),
                black_box(action_id),
                black_box(sequence_number),
                black_box(1_786_300_210_000),
                black_box(collector_config_hash),
                black_box(collector_capability_hash),
                black_box(browser_session_hash),
                black_box(redaction_policy_hash),
                black_box(policy_window_hash),
                black_box(&artifacts),
            )
            .expect("browser live collector run");
            black_box((
                collector_run.collector_run_hash,
                collector_run.manifest.manifest_hash,
                collector_run.manifest.artifact_path_refs.len(),
            ))
        })
    });
}

fn bench_browser_tool_gateway_ingest(c: &mut Criterion) {
    let run_id = 17;
    let policy_window_hash = bench_hash("browser-gateway-policy-window", 1);
    let browser_session_hash = bench_hash("browser-gateway-session", 1);
    let redaction_policy_hash = bench_hash("browser-gateway-redaction", 1);
    let action = BrowserActionTrace::new(
        242,
        BrowserActionKind::Click,
        bench_hash("browser-gateway-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
    );
    let proof = BrowserWitnessProof::new(
        run_id,
        action.action_id,
        SideEffectClass::FinancialLegal,
        bench_hash("gateway-url-before", 1),
        bench_hash("gateway-url-after", 1),
        bench_hash("gateway-dom-before", 1),
        bench_hash("gateway-dom-after", 1),
        bench_hash("gateway-screenshot-before", 1),
        bench_hash("gateway-screenshot-after", 1),
        bench_hash("gateway-accessibility-after", 1),
        bench_hash("gateway-network-log", 1),
        action.trace_hash,
        policy_window_hash,
        browser_session_hash,
        redaction_policy_hash,
    );
    let packet = BrowserObservationPacket::new(
        BrowserCollectorKind::ChromeExtension,
        9,
        1_786_300_200_000,
        bench_hash("gateway-collector-config", 1),
        bench_hash("gateway-collector-capability", 1),
        bench_hash("gateway-raw-artifact-manifest", 1),
        action,
        proof,
    );
    let ir = TypedToolIR::new(
        270,
        271,
        CapabilityClass::Browser,
        SideEffectClass::FinancialLegal,
        bench_hash("gateway-credential-scope", 1),
        RiskClass::R4,
        bench_hash("gateway-precondition", 1),
        bench_hash("gateway-effect", 1),
        EvidenceContract::r4_staged(),
        Some(bench_hash("gateway-approval-scope", 1)),
    );
    let facts = packet
        .isolated_browser_policy_facts(
            bench_hash("gateway-policy-version", 1),
            true,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        )
        .expect("valid browser gateway facts");
    let policy_trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
    assert_eq!(policy_trace.decision, PolicyDecision::Allow);

    c.bench_function("browser_tool_gateway_ingest", |b| {
        b.iter_batched(
            || {
                let mut ledger = RunEventLedger::new(black_box(run_id));
                seed_bench_goal_intake(&mut ledger, "browser-tool-gateway");
                ledger
                    .append_context_pack_built(
                        black_box(11),
                        black_box(128),
                        bench_hash("context-pack", 1),
                    )
                    .expect("context pack");
                ledger
                    .append_llm_response_received(
                        bench_hash("gateway-llm-response", 1),
                        bench_hash("gateway-raw-text-ref", 1),
                    )
                    .expect("llm response");
                ledger
            },
            |mut ledger| {
                let receipt = ToolExecutionGateway::execute_browser_observation_with_replay(
                    black_box(&mut ledger),
                    black_box(packet.action_id),
                    black_box(272),
                    black_box(&ir),
                    black_box(&facts),
                    black_box(&policy_trace),
                    black_box(&packet),
                )
                .expect("browser gateway receipt");
                black_box((
                    receipt.has_valid_fields(),
                    ledger.last_hash(),
                    receipt.tool_execution_evidence.evidence_hash,
                ))
            },
            BatchSize::SmallInput,
        )
    });
}

fn bench_browser_file_artifact_gateway_ingest(c: &mut Criterion) {
    let run_id = 18;
    let dir = std::env::temp_dir().join("aegis-browser-file-artifact-gateway-bench");
    let file_refs = bench_browser_artifact_files(&dir);
    let path_refs: Vec<_> = file_refs
        .iter()
        .map(|(kind, path)| {
            BrowserArtifactFilePathRef::from_path(*kind, path)
                .expect("file-backed browser gateway path ref")
        })
        .collect();
    assert!(path_refs.iter().all(BrowserArtifactFilePathRef::is_valid));

    let policy_window_hash = bench_hash("browser-file-gateway-policy-window", 1);
    let browser_session_hash = bench_hash("browser-file-gateway-session", 1);
    let redaction_policy_hash = bench_hash("browser-file-gateway-redaction", 1);
    let policy_version_hash = bench_hash("browser-file-gateway-policy-version", 1);
    let action = BrowserActionTrace::new(
        274,
        BrowserActionKind::Click,
        bench_hash("browser-file-gateway-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
    );
    let ir = TypedToolIR::new(
        280,
        281,
        CapabilityClass::Browser,
        SideEffectClass::FinancialLegal,
        bench_hash("browser-file-gateway-credential-scope", 1),
        RiskClass::R4,
        bench_hash("browser-file-gateway-precondition", 1),
        bench_hash("browser-file-gateway-effect", 1),
        EvidenceContract::r4_staged(),
        Some(bench_hash("browser-file-gateway-approval-scope", 1)),
    );

    c.bench_function("browser_file_artifact_gateway_ingest", |b| {
        b.iter(|| {
            let mut ledger = RunEventLedger::new(black_box(run_id));
            seed_bench_goal_intake(&mut ledger, "browser-file-gateway");
            ledger
                .append_context_pack_built(
                    black_box(11),
                    black_box(128),
                    bench_hash("file-gateway-context-pack", 1),
                )
                .expect("context pack");
            ledger
                .append_llm_response_received(
                    bench_hash("file-gateway-llm-response", 1),
                    bench_hash("file-gateway-raw-text-ref", 1),
                )
                .expect("llm response");
            let proof = ToolExecutionGateway::execute_browser_file_path_refs_with_replay(
                black_box(&mut ledger),
                black_box(282),
                black_box(&ir),
                black_box(BrowserCollectorKind::PlaywrightCdp),
                black_box(action.clone()),
                black_box(9),
                black_box(1_786_300_225_000),
                black_box(bench_hash("file-gateway-collector-config", 1)),
                black_box(bench_hash("file-gateway-collector-capability", 1)),
                black_box(browser_session_hash),
                black_box(redaction_policy_hash),
                black_box(&path_refs),
                black_box(policy_version_hash),
                black_box(true),
            )
            .expect("file-backed browser gateway proof");
            black_box((
                proof.is_valid_for(
                    run_id,
                    &ir,
                    policy_version_hash,
                    policy_window_hash,
                    browser_session_hash,
                    redaction_policy_hash,
                ),
                ledger.last_hash(),
                proof.receipt.tool_execution_evidence.evidence_hash,
                proof.packet.raw_artifact_manifest_hash,
            ))
        })
    });
}

fn bench_browser_live_collector_manifest_gateway_ingest(c: &mut Criterion) {
    let run_id = 21;
    let dir = std::env::temp_dir().join("aegis-browser-live-manifest-gateway-bench");
    let file_refs = bench_browser_artifact_files(&dir);
    let path_refs: Vec<_> = file_refs
        .iter()
        .map(|(kind, path)| {
            BrowserArtifactFilePathRef::from_path(*kind, path)
                .expect("live-manifest browser gateway path ref")
        })
        .collect();
    let policy_window_hash = bench_hash("browser-live-manifest-policy-window", 1);
    let browser_session_hash = bench_hash("browser-live-manifest-session", 1);
    let redaction_policy_hash = bench_hash("browser-live-manifest-redaction", 1);
    let policy_version_hash = bench_hash("browser-live-manifest-policy-version", 1);
    let action = BrowserActionTrace::new(
        286,
        BrowserActionKind::Click,
        bench_hash("browser-live-manifest-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
    );
    let ir = TypedToolIR::new(
        286,
        287,
        CapabilityClass::Browser,
        SideEffectClass::FinancialLegal,
        bench_hash("browser-live-manifest-credential-scope", 1),
        RiskClass::R4,
        bench_hash("browser-live-manifest-precondition", 1),
        bench_hash("browser-live-manifest-effect", 1),
        EvidenceContract::r4_staged(),
        Some(bench_hash("browser-live-manifest-approval-scope", 1)),
    );
    let manifest = BrowserLiveCollectorManifest::new(
        BrowserCollectorKind::PlaywrightCdp,
        run_id,
        action.action_id,
        11,
        1_786_300_240_000,
        bench_hash("live-manifest-collector-config", 1),
        bench_hash("live-manifest-collector-capability", 1),
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
        path_refs,
    );
    assert!(manifest.is_valid());

    c.bench_function("browser_live_collector_manifest_gateway_ingest", |b| {
        b.iter(|| {
            let mut ledger = RunEventLedger::new(black_box(run_id));
            seed_bench_goal_intake(&mut ledger, "browser-live-manifest-gateway");
            ledger
                .append_context_pack_built(
                    black_box(11),
                    black_box(128),
                    bench_hash("live-manifest-context-pack", 1),
                )
                .expect("context pack");
            ledger
                .append_llm_response_received(
                    bench_hash("live-manifest-llm-response", 1),
                    bench_hash("live-manifest-raw-text-ref", 1),
                )
                .expect("llm response");
            let proof = ToolExecutionGateway::execute_browser_live_manifest_with_replay(
                black_box(&mut ledger),
                black_box(288),
                black_box(&ir),
                black_box(&manifest),
                black_box(action.clone()),
                black_box(policy_version_hash),
                black_box(true),
            )
            .expect("live manifest browser gateway proof");
            black_box((
                proof.is_valid_for(
                    run_id,
                    &ir,
                    policy_version_hash,
                    policy_window_hash,
                    browser_session_hash,
                    redaction_policy_hash,
                ),
                ledger.last_hash(),
                proof.receipt.tool_execution_evidence.evidence_hash,
                proof.packet.raw_artifact_manifest_hash,
                manifest.manifest_hash,
            ))
        })
    });
}

fn bench_browser_action_plan_live_manifest_gateway_ingest(c: &mut Criterion) {
    let run_id = 22;
    let dir = std::env::temp_dir().join("aegis-browser-action-plan-live-manifest-gateway-bench");
    let file_refs = bench_browser_artifact_files(&dir);
    let path_refs: Vec<_> = file_refs
        .iter()
        .map(|(kind, path)| {
            BrowserArtifactFilePathRef::from_path(*kind, path)
                .expect("plan live-manifest browser gateway path ref")
        })
        .collect();

    let policy_window_hash = bench_hash("browser-plan-live-manifest-policy-window", 1);
    let browser_session_hash = bench_hash("browser-plan-live-manifest-session", 1);
    let redaction_policy_hash = bench_hash("browser-plan-live-manifest-redaction", 1);
    let policy_version_hash = bench_hash("browser-plan-live-manifest-policy-version", 1);
    let ir = TypedToolIR::new(
        298,
        299,
        CapabilityClass::Browser,
        SideEffectClass::FinancialLegal,
        bench_hash("browser-plan-live-manifest-credential-scope", 1),
        RiskClass::R4,
        bench_hash("browser-plan-live-manifest-precondition", 1),
        bench_hash("browser-plan-live-manifest-effect", 1),
        EvidenceContract::r4_staged(),
        Some(bench_hash("browser-plan-live-manifest-approval-scope", 1)),
    );
    let plan = BrowserActionPlanRecord::new(
        run_id,
        300,
        BrowserActionPlanKind::Click,
        12,
        ir.canonical_hash,
        *blake3::hash(b"https://bench.local/before").as_bytes(),
        bench_hash("browser-plan-live-manifest-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
        browser_session_hash,
        redaction_policy_hash,
        false,
    );
    let manifest = BrowserLiveCollectorManifest::new(
        BrowserCollectorKind::PlaywrightCdp,
        run_id,
        plan.action_id,
        plan.sequence_number,
        1_786_300_280_000,
        bench_hash("plan-live-manifest-collector-config", 1),
        bench_hash("plan-live-manifest-collector-capability", 1),
        browser_session_hash,
        redaction_policy_hash,
        policy_window_hash,
        path_refs,
    );
    assert!(plan.is_valid());
    assert!(manifest.binds_plan(&plan));

    c.bench_function("browser_action_plan_live_manifest_gateway_ingest", |b| {
        b.iter(|| {
            let mut ledger = RunEventLedger::new(black_box(run_id));
            seed_bench_goal_intake(&mut ledger, "browser-action-plan-live-manifest-gateway");
            ledger
                .append_context_pack_built(
                    black_box(11),
                    black_box(128),
                    bench_hash("plan-live-manifest-context-pack", 1),
                )
                .expect("context pack");
            ledger
                .append_llm_response_received(
                    bench_hash("plan-live-manifest-llm-response", 1),
                    bench_hash("plan-live-manifest-raw-text-ref", 1),
                )
                .expect("llm response");
            let proof =
                ToolExecutionGateway::execute_browser_action_plan_live_manifest_with_replay(
                    black_box(&mut ledger),
                    black_box(301),
                    black_box(&ir),
                    black_box(&manifest),
                    black_box(&plan),
                    black_box(policy_version_hash),
                    black_box(true),
                )
                .expect("plan live-manifest browser gateway proof");
            black_box((
                proof.is_valid_for(
                    run_id,
                    &ir,
                    policy_version_hash,
                    policy_window_hash,
                    browser_session_hash,
                    redaction_policy_hash,
                ),
                ledger.last_hash(),
                proof.browser_action_plan_hash,
                proof.receipt.tool_execution_evidence.evidence_hash,
                proof.packet.raw_artifact_manifest_hash,
                manifest.manifest_hash,
            ))
        })
    });
}

fn bench_browser_action_plan_file_artifact_gateway_ingest(c: &mut Criterion) {
    let run_id = 20;
    let dir = std::env::temp_dir().join("aegis-browser-action-plan-file-gateway-bench");
    let file_refs = bench_browser_artifact_files(&dir);
    let path_refs: Vec<_> = file_refs
        .iter()
        .map(|(kind, path)| {
            BrowserArtifactFilePathRef::from_path(*kind, path)
                .expect("plan-backed browser gateway path ref")
        })
        .collect();
    assert!(path_refs.iter().all(BrowserArtifactFilePathRef::is_valid));

    let policy_window_hash = bench_hash("browser-plan-file-gateway-policy-window", 1);
    let browser_session_hash = bench_hash("browser-plan-file-gateway-session", 1);
    let redaction_policy_hash = bench_hash("browser-plan-file-gateway-redaction", 1);
    let policy_version_hash = bench_hash("browser-plan-file-gateway-policy-version", 1);
    let ir = TypedToolIR::new(
        292,
        293,
        CapabilityClass::Browser,
        SideEffectClass::FinancialLegal,
        bench_hash("browser-plan-file-gateway-credential-scope", 1),
        RiskClass::R4,
        bench_hash("browser-plan-file-gateway-precondition", 1),
        bench_hash("browser-plan-file-gateway-effect", 1),
        EvidenceContract::r4_staged(),
        Some(bench_hash("browser-plan-file-gateway-approval-scope", 1)),
    );
    let plan = BrowserActionPlanRecord::new(
        run_id,
        294,
        BrowserActionPlanKind::Click,
        10,
        ir.canonical_hash,
        *blake3::hash(b"https://bench.local/before").as_bytes(),
        bench_hash("browser-plan-file-gateway-selector", 1),
        None,
        Some((320, 240)),
        None,
        policy_window_hash,
        browser_session_hash,
        redaction_policy_hash,
        false,
    );
    assert!(plan.is_valid());

    c.bench_function("browser_action_plan_file_artifact_gateway_ingest", |b| {
        b.iter(|| {
            let mut ledger = RunEventLedger::new(black_box(run_id));
            seed_bench_goal_intake(&mut ledger, "browser-action-plan-file-gateway");
            ledger
                .append_context_pack_built(
                    black_box(11),
                    black_box(128),
                    bench_hash("plan-file-gateway-context-pack", 1),
                )
                .expect("context pack");
            ledger
                .append_llm_response_received(
                    bench_hash("plan-file-gateway-llm-response", 1),
                    bench_hash("plan-file-gateway-raw-text-ref", 1),
                )
                .expect("llm response");
            let proof =
                ToolExecutionGateway::execute_browser_action_plan_file_path_refs_with_replay(
                    black_box(&mut ledger),
                    black_box(295),
                    black_box(&ir),
                    black_box(BrowserCollectorKind::PlaywrightCdp),
                    black_box(&plan),
                    black_box(1_786_300_260_000),
                    black_box(bench_hash("plan-file-gateway-collector-config", 1)),
                    black_box(bench_hash("plan-file-gateway-collector-capability", 1)),
                    black_box(&path_refs),
                    black_box(policy_version_hash),
                    black_box(true),
                )
                .expect("plan-backed file browser gateway proof");
            black_box((
                proof.is_valid_for(
                    run_id,
                    &ir,
                    policy_version_hash,
                    policy_window_hash,
                    browser_session_hash,
                    redaction_policy_hash,
                ),
                ledger.last_hash(),
                proof.browser_action_plan_hash,
                proof.receipt.tool_execution_evidence.evidence_hash,
                proof.packet.raw_artifact_manifest_hash,
            ))
        })
    });
}

fn bench_browser_action_plan_bind_packet(c: &mut Criterion) {
    let run_id = 19;
    let action_id = 290;
    let policy_window_hash = bench_hash("browser-plan-policy-window", 1);
    let browser_session_hash = bench_hash("browser-plan-session", 1);
    let redaction_policy_hash = bench_hash("browser-plan-redaction", 1);
    let typed_tool_ir_hash = bench_hash("browser-plan-ir", 1);
    let url_before_hash = bench_hash("browser-plan-url-before", 1);
    let target_hash = bench_hash("browser-plan-target", 1);
    let url_after_hash = bench_hash("browser-plan-url-after", 1);

    c.bench_function("browser_action_plan_bind_packet", |b| {
        b.iter(|| {
            let plan = BrowserActionPlanRecord::new(
                black_box(run_id),
                black_box(action_id),
                black_box(BrowserActionPlanKind::Click),
                black_box(3),
                black_box(typed_tool_ir_hash),
                black_box(url_before_hash),
                black_box(target_hash),
                black_box(None),
                black_box(Some((128, 96))),
                black_box(None),
                black_box(policy_window_hash),
                black_box(browser_session_hash),
                black_box(redaction_policy_hash),
                black_box(false),
            );
            let action = plan.to_action_trace().expect("valid browser action plan");
            let proof = BrowserWitnessProof::new(
                black_box(run_id),
                black_box(action_id),
                black_box(SideEffectClass::ExternalWrite),
                black_box(url_before_hash),
                black_box(url_after_hash),
                black_box(bench_hash("browser-plan-dom-before", 1)),
                black_box(bench_hash("browser-plan-dom-after", 1)),
                black_box(bench_hash("browser-plan-screenshot-before", 1)),
                black_box(bench_hash("browser-plan-screenshot-after", 1)),
                black_box(bench_hash("browser-plan-ax-after", 1)),
                black_box(bench_hash("browser-plan-network", 1)),
                black_box(action.trace_hash),
                black_box(policy_window_hash),
                black_box(browser_session_hash),
                black_box(redaction_policy_hash),
            );
            let packet = BrowserObservationPacket::new(
                black_box(BrowserCollectorKind::PlaywrightCdp),
                black_box(plan.sequence_number),
                black_box(1_786_300_250_000),
                black_box(bench_hash("browser-plan-collector-config", 1)),
                black_box(bench_hash("browser-plan-collector-capability", 1)),
                black_box(bench_hash("browser-plan-manifest", 1)),
                black_box(action),
                black_box(proof),
            );
            black_box((
                plan.is_valid_for_packet(typed_tool_ir_hash, &packet),
                plan.sequence_should_abort_after(typed_tool_ir_hash, &packet),
                plan.plan_hash,
                packet.packet_hash,
            ))
        })
    });
}

fn bench_browser_artifact(kind: BrowserArtifactKind, label: &str) -> BrowserArtifactRef {
    BrowserArtifactRef::new(kind, 256, bench_hash(label, 1), bench_hash(label, 2))
}

fn bench_browser_artifact_files(dir: &std::path::Path) -> Vec<(BrowserArtifactKind, PathBuf)> {
    std::fs::create_dir_all(dir).expect("browser artifact bench directory");
    let files = vec![
        (
            BrowserArtifactKind::NetworkLog,
            "network-log.jsonl",
            b"[{\"url\":\"https://bench.local/api\",\"status\":200}]".as_slice(),
        ),
        (
            BrowserArtifactKind::ScreenshotAfter,
            "screenshot-after.png",
            b"bench-png-after",
        ),
        (
            BrowserArtifactKind::UrlBefore,
            "url-before.txt",
            b"https://bench.local/before",
        ),
        (
            BrowserArtifactKind::DomSnapshotAfter,
            "dom-after.html",
            b"<html><button>done</button></html>",
        ),
        (
            BrowserArtifactKind::AccessibilityTreeAfter,
            "accessibility-after.json",
            b"{\"role\":\"button\",\"name\":\"done\"}",
        ),
        (
            BrowserArtifactKind::UrlAfter,
            "url-after.txt",
            b"https://bench.local/after",
        ),
        (
            BrowserArtifactKind::ScreenshotBefore,
            "screenshot-before.png",
            b"bench-png-before",
        ),
        (
            BrowserArtifactKind::DomSnapshotBefore,
            "dom-before.html",
            b"<html><button>go</button></html>",
        ),
    ];
    files
        .into_iter()
        .map(|(kind, file_name, bytes)| {
            let path = dir.join(file_name);
            std::fs::write(&path, bytes).expect("browser artifact bench write");
            (kind, path)
        })
        .collect()
}

fn bench_browser_live_collector_artifacts() -> Vec<BrowserLiveCollectorArtifact> {
    vec![
        BrowserLiveCollectorArtifact::new(
            BrowserArtifactKind::UrlBefore,
            b"https://bench.local/before".to_vec(),
        ),
        BrowserLiveCollectorArtifact::new(
            BrowserArtifactKind::UrlAfter,
            b"https://bench.local/after".to_vec(),
        ),
        BrowserLiveCollectorArtifact::new(
            BrowserArtifactKind::DomSnapshotBefore,
            b"<html><button>go</button></html>".to_vec(),
        ),
        BrowserLiveCollectorArtifact::new(
            BrowserArtifactKind::DomSnapshotAfter,
            b"<html><button>done</button></html>".to_vec(),
        ),
        BrowserLiveCollectorArtifact::new(BrowserArtifactKind::ScreenshotBefore, vec![3u8; 4096]),
        BrowserLiveCollectorArtifact::new(BrowserArtifactKind::ScreenshotAfter, vec![5u8; 4096]),
        BrowserLiveCollectorArtifact::new(
            BrowserArtifactKind::AccessibilityTreeAfter,
            b"{\"role\":\"button\",\"name\":\"done\"}".to_vec(),
        ),
        BrowserLiveCollectorArtifact::new(
            BrowserArtifactKind::NetworkLog,
            b"[{\"url\":\"https://bench.local/api\",\"status\":200}]".to_vec(),
        ),
    ]
}

fn bench_policy_datalog_closure_proof(c: &mut Criterion) {
    let ir = TypedToolIR::new(
        70,
        71,
        CapabilityClass::FinancialLegal,
        SideEffectClass::FinancialLegal,
        bench_hash("policy-datalog-credential", 1),
        RiskClass::R4,
        bench_hash("policy-datalog-precondition", 1),
        bench_hash("policy-datalog-effect", 1),
        EvidenceContract::r4_staged(),
        Some(bench_hash("policy-datalog-approval-scope", 1)),
    );
    let facts = PolicyFacts::new(
        bench_hash("policy-datalog-policy-v1", 1),
        false,
        false,
        false,
    );
    let kernel = DeterministicPolicyKernel;
    let (trace, closure) = kernel.evaluate_with_datalog(&ir, &facts);
    assert!(trace.binds_datalog_closure(&closure));
    assert!(closure.proves_r4_without_staging_or_hitl_hard_block());

    c.bench_function("policy_datalog_closure_proof", |b| {
        b.iter(|| {
            let (trace, closure) = kernel.evaluate_with_datalog(black_box(&ir), black_box(&facts));
            black_box((
                trace.binds_datalog_closure(&closure),
                closure.proves_r4_without_staging_or_hitl_hard_block(),
                closure.closure_hash,
            ))
        })
    });
}

fn bench_replay_io_binary_mmap_fixed(c: &mut Criterion) {
    let path = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-io-binary-fixed")
        .join("run-events.bin");
    let ledger = replay_io_bench_ledger(64);
    let payload_hash =
        BinaryRunEventSegment::write_ledger(&path, &ledger).expect("binary bench ledger");

    c.bench_function("replay_io_binary_mmap_fixed", |b| {
        b.iter(|| {
            let recovered = BinaryRunEventSegment::read_ledger_mmap(&path, payload_hash)
                .expect("binary mmap replay readback");
            black_box((recovered.len(), BinaryRunEventSegment::record_bytes()))
        })
    });
}

fn bench_replay_io_binary_mmap_verify(c: &mut Criterion) {
    let path = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-io-binary-verify")
        .join("run-events.bin");
    let ledger = replay_io_bench_ledger(64);
    let payload_hash =
        BinaryRunEventSegment::write_ledger(&path, &ledger).expect("binary bench ledger");

    c.bench_function("replay_io_binary_mmap_verify", |b| {
        b.iter(|| {
            let report = BinaryRunEventSegment::verify_mmap(&path, payload_hash)
                .expect("binary mmap replay scan");
            black_box((report.event_count, report.last_event_hash))
        })
    });
}

fn bench_replay_io_binary_mmap_recover_prefix(c: &mut Criterion) {
    let path = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-io-binary-recover-prefix")
        .join("run-events-damaged-tail.bin");
    let ledger = replay_io_bench_ledger(64);
    BinaryRunEventSegment::write_ledger(&path, &ledger).expect("binary recovery bench ledger");
    let file_len = std::fs::metadata(&path)
        .expect("binary recovery bench metadata")
        .len();
    let damaged_len = file_len.saturating_sub((BinaryRunEventSegment::record_bytes() / 2) as u64);
    std::fs::OpenOptions::new()
        .write(true)
        .open(&path)
        .expect("binary recovery bench open")
        .set_len(damaged_len)
        .expect("binary recovery bench tail truncation");

    c.bench_function("replay_io_binary_mmap_recover_prefix", |b| {
        b.iter(|| {
            let report = BinaryRunEventSegment::recover_last_valid_prefix_mmap(&path)
                .expect("binary mmap prefix recovery");
            black_box((
                report.declared_event_count,
                report.recovered_event_count,
                report.recovery_hash,
            ))
        })
    });
}

fn bench_replay_endurance_report_verify_100h(c: &mut Criterion) {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join("replay-endurance-proof-100h");
    let _ = std::fs::remove_dir_all(&dir);
    let report = ReplayEnduranceBench::run_accelerated_100h_proof(&dir)
        .expect("accelerated 100h replay proof bench report");

    c.bench_function("replay_endurance_report_verify_100h", |b| {
        b.iter(|| {
            let valid = black_box(&report).is_valid_physical_result();
            black_box((valid, report.report_hash))
        })
    });
}

fn bench_context_governor_activated_pack(c: &mut Criterion) {
    let governor = context_bench_governor(10_000, 64);
    let required_evidence_refs = [2, 3, 4, 5];

    c.bench_function("context_governor_activated_pack", |b| {
        b.iter(|| {
            let pack = governor
                .build_context_pack(1, black_box(&required_evidence_refs))
                .expect("activated context pack");
            black_box((pack.node_ids.len(), pack.token_count, pack.digest))
        })
    });
}

fn bench_context_fold_record(c: &mut Criterion) {
    let governor = context_bench_governor(10_000, 64);
    let required_evidence_refs = [2, 3, 4, 5];

    c.bench_function("context_fold_record", |b| {
        b.iter(|| {
            let record = governor
                .build_context_fold(1, black_box(&required_evidence_refs))
                .expect("context fold record");
            black_box((
                record.retained_node_ids.len(),
                record.folded_node_ids.len(),
                record.record_hash,
            ))
        })
    });
}

// -----------------------------------------------------------------------------
// Context length stress: ContextPack token count PLATEAUS as the global node
// graph grows. The bench inserts N total nodes (proxy for "session length"),
// then runs build_context_pack(1, &required_evidence_refs) and prints the
// resulting token_count + node_count as a sanity log. Throughput scaling is
// separately captured by criterion across three N values (1K / 10K / 100K).
// Plateau assertion (token_count <= token_budget across all N) is checked
// once per measurement, so any regression that reintroduces linear growth
// trips the panicking variant instead of the criterion loop.
// -----------------------------------------------------------------------------

fn plateau_context_bench_governor(global_nodes: u128) -> ContextGovernor {
    // Fan-out: active task 1 → 16 evidence → 32 second-hop evidence (≈49 in-pack).
    // Everything else is distractor (no edge back to the active task subgraph).
    let mut governor =
        ContextGovernor::new(ContextGovernorConfig::bounded(4_096, 256)).expect("context governor");

    for node_id in 1..=global_nodes {
        let kind = if node_id == 1 {
            ContextNodeKind::Task
        } else if node_id <= 17 {
            ContextNodeKind::Evidence
        } else if node_id % 7 == 0 {
            ContextNodeKind::Memory
        } else {
            ContextNodeKind::File
        };
        let node = ContextNode::new(
            node_id,
            kind,
            16 + (node_id % 32) as u32,
            ((node_id * 37) % 10_000) as u32 + 1,
            ((node_id * 17) % 512) as u32,
            ((node_id * 7) % 31) as u32,
        );
        governor.insert_node(node).expect("context node");

        if node_id < 17 {
            governor.add_edge(1, node_id).expect("first-hop edge");
        }
        if node_id >= 17 && node_id < 49 {
            // first-hop evidence (node_id - 16) → second-hop evidence node_id
            governor
                .add_edge(node_id - 16, node_id)
                .expect("second-hop edge");
        }
        // Distractor chain: link on a second pass — see bottom of helper — to
        // guarantee both endpoints exist. We mark candidate endpoints here.
    }
    // Second pass: every 1000th node links to its successor to grow the edge
    // set without ever escaping the distractor frontier (none of these nodes
    // have an edge back to 1..49).
    let mut node_id = 1_000u128;
    while node_id + 1 <= global_nodes {
        governor
            .add_edge(node_id, node_id + 1)
            .expect("distractor edge");
        node_id += 1_000;
    }
    governor
}

fn bench_context_length_stress(c: &mut Criterion) {
    let required = [2u128, 3, 4, 5, 17, 33];
    // Three session-length bands. The criterion time/cost limit is 1M nodes
    // (defended by the plateau invariant — runtime does not scale with N).
    let sizes: &[(u128, &str)] = &[
        (1_000, "1k_nodes"),
        (10_000, "10k_nodes"),
        (100_000, "100k_nodes"),
    ];
    let token_budget: u32 = 4_096;

    for &(n, label) in sizes {
        let governor = plateau_context_bench_governor(n);
        // Sanity: pack should still fit in budget regardless of N.
        let pack = governor
            .build_context_pack(1, black_box(&required))
            .expect("activated context pack");
        assert!(
            pack.token_count <= token_budget,
            "plateau violated at N={n}: token_count={} > budget={token_budget}",
            pack.token_count,
        );
        eprintln!(
            "[context_length_stress/{label}] N={n:>7} -> pack.node_ids={} pack.token_count={} (budget={token_budget})",
            pack.node_ids.len(),
            pack.token_count,
        );

        c.bench_function(&format!("context_length_stress/{label}"), |b| {
            b.iter(|| {
                let pack = governor
                    .build_context_pack(1, black_box(&required))
                    .expect("activated context pack");
                black_box((pack.node_ids.len(), pack.token_count))
            })
        });
    }
}

fn bench_task_ledger_ready_queue_10k(c: &mut Criterion) {
    let mut ledger = task_ledger_bench_tree(10_000);

    c.bench_function("task_ledger_ready_queue_10k", |b| {
        b.iter(|| {
            let ready = ledger
                .ordered_ready_tasks(black_box(10_000))
                .expect("ready queue ordering");
            black_box((ready.len(), ready.first().copied()))
        })
    });
}

fn bench_task_ledger_ready_queue_10k_recompute(c: &mut Criterion) {
    let mut ledger = task_ledger_bench_tree(10_000);
    let mut now_ms = 10_000u64;

    c.bench_function("task_ledger_ready_queue_10k_recompute", |b| {
        b.iter(|| {
            now_ms = now_ms.wrapping_add(1);
            let ready = ledger
                .ordered_ready_tasks(black_box(now_ms))
                .expect("ready queue recompute ordering");
            black_box((ready.len(), ready.first().copied()))
        })
    });
}

fn bench_hot_lexical_index_top_k(c: &mut Criterion) {
    let index = hot_lexical_bench_index(10_000);
    let mut scratch = HotLexicalQueryScratch::with_document_capacity(index.document_count());
    let query_terms = ["rust", "witness", "policy", "segment"];

    c.bench_function("hot_lexical_index_top_k", |b| {
        b.iter(|| {
            let results =
                index.query_top_k_into_scratch(black_box(&query_terms), black_box(8), &mut scratch);
            black_box((
                results.len(),
                results.first().map(|candidate| candidate.candidate_hash()),
            ))
        })
    });
}

fn bench_hot_evidence_index_exact_aho(c: &mut Criterion) {
    let index = hot_evidence_bench_index(10_000);
    let allowed_segments = SortedEvidenceSet::from_unsorted(
        (1u64..=10_000)
            .filter(|segment_id| segment_id % 2 == 0 || segment_id % 17 == 0)
            .collect(),
    );
    let mut scratch = HotEvidenceQueryScratch::with_document_capacity(index.document_count());
    let query_terms = ["replay", "policy", "witness", "segment"];

    c.bench_function("hot_evidence_index_exact_aho", |b| {
        b.iter(|| {
            let results = index.query_exact_literals_into_scratch(
                black_box(&query_terms),
                Some(black_box(&allowed_segments)),
                black_box(8),
                &mut scratch,
            );
            black_box((
                results.len(),
                results.first().map(|candidate| candidate.candidate_hash()),
            ))
        })
    });
}

fn bench_hot_evidence_index_ast_signature_lookup(c: &mut Criterion) {
    let index = hot_evidence_bench_index(10_000);
    let allowed_segments = SortedEvidenceSet::from_unsorted(
        (1u64..=10_000)
            .filter(|segment_id| segment_id % 2 == 0 || segment_id % 17 == 0)
            .collect(),
    );
    let signature_hashes: Vec<[u8; 32]> = (0..32)
        .map(|offset| bench_hash("hot-evidence-ast", offset * 64))
        .collect();
    let mut scratch = HotEvidenceQueryScratch::with_document_capacity(index.document_count());

    c.bench_function("hot_evidence_index_ast_signature_lookup", |b| {
        b.iter(|| {
            let results = index.query_ast_signatures_into_scratch(
                black_box(&signature_hashes),
                Some(black_box(&allowed_segments)),
                black_box(8),
                &mut scratch,
            );
            black_box((
                results.len(),
                results.first().map(|candidate| candidate.candidate_hash()),
            ))
        })
    });
}

fn bench_hot_engine_simd_blake3_hash(c: &mut Criterion) {
    let payload = hot_engine_browser_payload();
    c.bench_function("hot_engine_simd_blake3_hash", |b| {
        b.iter(|| black_box(simd_blake3_hash(black_box(&payload))))
    });
}

fn bench_hot_engine_arena_commit(c: &mut Criterion) {
    let payload = hot_engine_browser_payload();
    let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 64 * 1024 * 1024, 1024 * 1024);
    c.bench_function("hot_engine_arena_commit", |b| {
        b.iter(|| {
            let handle = arena
                .commit(black_box(&payload))
                .expect("hot engine arena commit");
            let proof = (
                handle.artifact_hash,
                handle.storage_ref_hash,
                handle.byte_len,
            );
            arena.release(&handle).expect("hot engine arena release");
            black_box(proof)
        })
    });
}

fn bench_agentic_evidence_program_execute(c: &mut Criterion) {
    let epoch_hash = bench_hash("agentic-evidence-index", 0);
    let lexical = hot_lexical_bench_index_with_epoch(epoch_hash, 10_000);
    let exact = hot_evidence_bench_index_with_epoch(epoch_hash, 10_000);
    let allowed_segments = SortedEvidenceSet::from_unsorted(
        (1u64..=10_000)
            .filter(|segment_id| segment_id % 2 == 0 || segment_id % 17 == 0)
            .collect(),
    );
    let bitmap_filter = HotBitmapFilter::new(epoch_hash, allowed_segments).unwrap();
    let program = AgenticEvidenceProgram::new(
        epoch_hash,
        96,
        vec![
            AgenticEvidenceProgramStep::lexical_top_k(
                &["rust", "witness", "policy", "segment"],
                32,
            ),
            AgenticEvidenceProgramStep::exact_artifact_rerank(
                &["replay", "policy", "witness", "segment"],
                16,
            ),
            AgenticEvidenceProgramStep::bitmap_filter(8),
        ],
    )
    .expect("agentic evidence program");
    let mut scratch = AgenticEvidenceProgramScratch::default();

    c.bench_function("agentic_evidence_program_execute", |b| {
        b.iter(|| {
            let results = program
                .execute_into_scratch(
                    Some(black_box(&lexical)),
                    Some(black_box(&exact)),
                    Some(black_box(&bitmap_filter)),
                    black_box(&mut scratch),
                )
                .expect("agentic evidence execution");
            black_box((
                results.len(),
                results.first().map(|candidate| candidate.candidate_hash()),
            ))
        })
    });
}

fn bench_agentic_evidence_ast_signature_program_execute(c: &mut Criterion) {
    let epoch_hash = bench_hash("agentic-evidence-ast-index", 0);
    let lexical = hot_lexical_bench_index_with_epoch(epoch_hash, 10_000);
    let exact = hot_evidence_bench_index_with_epoch(epoch_hash, 10_000);
    let allowed_segments = SortedEvidenceSet::from_unsorted(
        (1u64..=10_000)
            .filter(|segment_id| segment_id % 2 == 0 || segment_id % 17 == 0)
            .collect(),
    );
    let bitmap_filter = HotBitmapFilter::new(epoch_hash, allowed_segments).unwrap();
    let signature_hashes: Vec<[u8; 32]> = (0..16)
        .map(|offset| bench_hash("hot-evidence-ast", offset * 32))
        .collect();
    let program = AgenticEvidenceProgram::new(
        epoch_hash,
        128,
        vec![
            AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay", "witness"], 64),
            AgenticEvidenceProgramStep::ast_signature_lookup(&signature_hashes, 16),
            AgenticEvidenceProgramStep::bitmap_filter(8),
        ],
    )
    .expect("agentic evidence ast signature program");
    let mut scratch = AgenticEvidenceProgramScratch::default();

    c.bench_function("agentic_evidence_ast_signature_program_execute", |b| {
        b.iter(|| {
            let results = program
                .execute_into_scratch(
                    Some(black_box(&lexical)),
                    Some(black_box(&exact)),
                    Some(black_box(&bitmap_filter)),
                    &mut scratch,
                )
                .expect("agentic evidence ast signature execute");
            black_box((
                results.len(),
                results.first().map(|candidate| candidate.candidate_hash()),
            ))
        })
    });
}

fn bench_agentic_evidence_sdk_run(c: &mut Criterion) {
    let (program, lexical, exact, bitmap_filter) =
        agentic_evidence_sdk_bench_fixture("agentic-evidence-sdk-index");

    c.bench_function("agentic_evidence_sdk_run", |b| {
        b.iter(|| {
            let run = AgenticEvidenceSdk::run_program(
                black_box(&program),
                black_box(9001),
                Some(black_box(&lexical)),
                Some(black_box(&exact)),
                Some(black_box(&bitmap_filter)),
            )
            .expect("agentic evidence sdk run");
            black_box((
                run.run_hash,
                run.capsule.candidate_count,
                run.manifest.manifest_hash,
            ))
        })
    });
}

fn bench_agentic_evidence_sdk_run_replay_binding_hash(c: &mut Criterion) {
    let (program, lexical, exact, bitmap_filter) =
        agentic_evidence_sdk_small_bench_fixture("agentic-evidence-sdk-run-binding");
    let run = AgenticEvidenceSdk::run_program(
        &program,
        9001,
        Some(&lexical),
        Some(&exact),
        Some(&bitmap_filter),
    )
    .expect("agentic evidence sdk run replay binding fixture");

    c.bench_function("agentic_evidence_sdk_run_replay_binding_hash", |b| {
        b.iter(|| {
            black_box(agentic_evidence_sdk_run_replay_binding_hash(black_box(
                &run,
            )))
        })
    });
}

fn bench_agentic_evidence_sdk_run_replay_append(c: &mut Criterion) {
    let (program, lexical, exact, bitmap_filter) =
        agentic_evidence_sdk_small_bench_fixture("agentic-evidence-sdk-run-append");
    let run = AgenticEvidenceSdk::run_program(
        &program,
        9001,
        Some(&lexical),
        Some(&exact),
        Some(&bitmap_filter),
    )
    .expect("agentic evidence sdk run replay append fixture");

    c.bench_function("agentic_evidence_sdk_run_replay_append", |b| {
        b.iter_batched(
            || {
                let mut ledger = RunEventLedger::new(50_301);
                seed_bench_goal_intake(&mut ledger, "agentic-sdk-run-append");
                ledger
                    .append_context_pack_built(71, 512, bench_hash("sdk-run-append-context", 1))
                    .expect("agentic sdk run append context");
                ledger
            },
            |mut ledger| {
                let event = ledger
                    .append_agentic_evidence_sdk_run_recorded(
                        191,
                        black_box(&program),
                        black_box(&run),
                    )
                    .expect("agentic evidence sdk run replay append");
                black_box((event.event_hash, event.primary_hash, event.secondary_hash))
            },
            BatchSize::SmallInput,
        )
    });
}

fn bench_agentic_evidence_sdk_run_handoff_seal(c: &mut Criterion) {
    let (program, lexical, exact, bitmap_filter) =
        agentic_evidence_sdk_small_bench_fixture("agentic-evidence-sdk-run-handoff");
    let run = AgenticEvidenceSdk::run_program(
        &program,
        9001,
        Some(&lexical),
        Some(&exact),
        Some(&bitmap_filter),
    )
    .expect("agentic evidence sdk run handoff fixture");

    c.bench_function("agentic_evidence_sdk_run_handoff_seal", |b| {
        b.iter_batched(
            || {
                let mut ledger = RunEventLedger::new(50_302);
                seed_bench_goal_intake(&mut ledger, "agentic-sdk-run-handoff");
                ledger
                    .append_context_pack_built(71, 512, bench_hash("sdk-run-handoff-context", 1))
                    .expect("agentic sdk run handoff context");
                ledger
                    .append_agentic_evidence_sdk_run_recorded(191, &program, &run)
                    .expect("agentic sdk run handoff replay event");
                let dir = std::env::temp_dir()
                    .join("aegis-criterion")
                    .join("agentic-evidence-sdk-run-handoff");
                let _ = std::fs::remove_dir_all(&dir);
                let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger)
                    .expect("agentic sdk run handoff archive");
                (dir, manifest)
            },
            |(dir, manifest)| {
                let handoff: AgenticEvidenceSdkRunHandoffProof =
                    RunEventSegmentArchive::seal_agentic_evidence_sdk_run_handoff(
                        black_box(&dir),
                        black_box(&manifest),
                        black_box(191),
                        black_box(&program),
                        black_box(&run),
                        black_box(1_930),
                        black_box(1_931),
                        black_box(NextActionKind::ContinueExecution),
                        black_box(1_932),
                        black_box(bench_hash("sdk-run-handoff-typed-tool-ir", 1)),
                        black_box(bench_hash("sdk-run-handoff-evidence-contract", 1)),
                        black_box(bench_hash("sdk-run-handoff-policy-proof", 1)),
                    )
                    .expect("agentic sdk run handoff seal");
                black_box(handoff.handoff_hash)
            },
            BatchSize::SmallInput,
        )
    });
}

fn bench_agentic_evidence_sdk_run_handoff_context_pack(c: &mut Criterion) {
    let (program, lexical, exact, bitmap_filter) =
        agentic_evidence_sdk_small_bench_fixture("agentic-evidence-sdk-run-context");
    let run = AgenticEvidenceSdk::run_program(
        &program,
        9001,
        Some(&lexical),
        Some(&exact),
        Some(&bitmap_filter),
    )
    .expect("agentic evidence sdk run context fixture");
    let mut ledger = RunEventLedger::new(50_303);
    seed_bench_goal_intake(&mut ledger, "agentic-sdk-run-context");
    ledger
        .append_context_pack_built(71, 512, bench_hash("sdk-run-context-pack", 1))
        .expect("agentic sdk run context prior");
    ledger
        .append_agentic_evidence_sdk_run_recorded(191, &program, &run)
        .expect("agentic sdk run context replay event");
    let dir = std::env::temp_dir()
        .join("aegis-criterion")
        .join("agentic-evidence-sdk-run-context");
    let _ = std::fs::remove_dir_all(&dir);
    let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger)
        .expect("agentic sdk context archive");
    let handoff = RunEventSegmentArchive::seal_agentic_evidence_sdk_run_handoff(
        &dir,
        &manifest,
        191,
        &program,
        &run,
        1_930,
        1_931,
        NextActionKind::ContinueExecution,
        1_932,
        bench_hash("sdk-run-context-typed-tool-ir", 1),
        bench_hash("sdk-run-context-evidence-contract", 1),
        bench_hash("sdk-run-context-policy-proof", 1),
    )
    .expect("agentic sdk context handoff");
    let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(512, 64)).unwrap();
    governor
        .insert_node(ContextNode::new(9_100, ContextNodeKind::Task, 8, 10, 0, 0))
        .unwrap();
    for candidate in run.capsule.candidates() {
        governor
            .insert_node(ContextNode::new(
                candidate.segment_id as u128,
                ContextNodeKind::Evidence,
                8,
                candidate.score_quantized / 1000,
                0,
                0,
            ))
            .unwrap();
    }

    c.bench_function("agentic_evidence_sdk_run_handoff_context_pack", |b| {
        b.iter(|| {
            let (_pack, proof) = governor
                .build_sdk_run_handoff_bound_context_pack(
                    black_box(9_100),
                    black_box(&run.capsule),
                    black_box(&handoff),
                )
                .expect("agentic sdk handoff context pack");
            black_box((
                proof.proof_hash,
                proof.agentic_evidence_sdk_run_handoff_hash,
            ))
        })
    });
}

fn bench_candidate_state_capsule_roundtrip(c: &mut Criterion) {
    let epoch_hash = bench_hash("candidate-state-capsule", 0);
    let candidates: Vec<CandidateEvidenceRef> = (1..=32)
        .map(|segment_id| {
            CandidateEvidenceRef::new(
                bench_hash("candidate-state-capsule-ref", segment_id),
                segment_id as u64,
                EvidenceCandidateTier::LexicalBaseline,
                900_000u32.saturating_sub(segment_id as u32 * 1000),
                epoch_hash,
            )
        })
        .collect();
    let capsule =
        CandidateStateCapsule::new(77, &candidates, None, None).expect("candidate state capsule");
    let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(512, 64)).unwrap();
    governor
        .insert_node(ContextNode::new(9_000, ContextNodeKind::Task, 8, 10, 0, 0))
        .unwrap();
    for candidate in &candidates {
        governor
            .insert_node(ContextNode::new(
                candidate.segment_id as u128,
                ContextNodeKind::Evidence,
                8,
                candidate.score_quantized / 1000,
                0,
                0,
            ))
            .unwrap();
    }

    c.bench_function("candidate_state_capsule_roundtrip", |b| {
        b.iter(|| {
            let recovered = CandidateStateCapsule::from_canonical_bytes(
                black_box(capsule.canonical_bytes()),
                None,
                None,
            )
            .expect("candidate state capsule roundtrip");
            let (_pack, proof) = governor
                .build_capsule_bound_context_pack(9_000, &recovered, None, None)
                .expect("candidate state capsule context pack");
            black_box((recovered.capsule_hash, proof.proof_hash))
        })
    });
}

fn bench_browser_page_search_candidate_record(c: &mut Criterion) {
    let policy_window_hash = bench_hash("browser-page-search-policy-window", 1);
    let pattern_hash =
        browser_page_search_pattern_hash("submit invoice", false, false).expect("pattern hash");
    let action = BrowserActionTrace::new(
        62,
        BrowserActionKind::SearchPage,
        bench_hash("browser-page-search-target", 1),
        Some(pattern_hash),
        None,
        None,
        policy_window_hash,
    );
    let proof = BrowserWitnessProof::new(
        21,
        action.action_id,
        SideEffectClass::ExternalRead,
        bench_hash("browser-page-search-url-before", 1),
        bench_hash("browser-page-search-url-after", 1),
        bench_hash("browser-page-search-dom-before", 1),
        bench_hash("browser-page-search-dom-after", 1),
        bench_hash("browser-page-search-screenshot-before", 1),
        bench_hash("browser-page-search-screenshot-after", 1),
        bench_hash("browser-page-search-accessibility-after", 1),
        bench_hash("browser-page-search-network-log", 1),
        action.trace_hash,
        policy_window_hash,
        bench_hash("browser-page-search-session", 1),
        bench_hash("browser-page-search-redaction", 1),
    );
    let packet = BrowserObservationPacket::new(
        BrowserCollectorKind::PlaywrightCdp,
        3,
        1_786_300_321_000,
        bench_hash("browser-page-search-config", 1),
        bench_hash("browser-page-search-capability", 1),
        bench_hash("browser-page-search-manifest", 1),
        action,
        proof,
    );
    assert!(packet.is_valid());

    c.bench_function("browser_page_search_candidate_record", |b| {
        b.iter(|| {
            let search = packet
                .page_search_candidates(
                    black_box("submit invoice"),
                    black_box(false),
                    black_box(false),
                    black_box(16),
                    black_box(8),
                )
                .expect("browser page search candidates");
            black_box((search.record.record_hash, search.candidates.len()))
        })
    });
}

fn bench_index_epoch_replay_record(c: &mut Criterion) {
    let index_epoch_hash = bench_hash("hot-lexical-index", 0);
    let index = hot_lexical_bench_index(10_000);
    let query_terms = ["rust", "witness", "policy", "segment"];
    let candidates = index.query_top_k(&query_terms, 8);

    c.bench_function("index_epoch_replay_record", |b| {
        b.iter(|| {
            let record = IndexEpochReplayRecord::new(
                index_epoch_hash,
                black_box(&query_terms),
                black_box(8),
                black_box(&candidates),
            )
            .expect("index epoch replay record");
            black_box((record.candidate_count, record.candidate_list_hash))
        })
    });
}

fn bench_cold_vector_expansion_replay_record(c: &mut Criterion) {
    let index_epoch_hash = bench_hash("cold-vector-index-epoch", 0);
    let query_terms = ["rust", "witness", "policy", "segment"];
    let candidates: Vec<CandidateEvidenceRef> = (0..8)
        .map(|index| {
            CandidateEvidenceRef::new(
                bench_hash("cold-vector-candidate", index),
                (index + 1) as u64,
                EvidenceCandidateTier::ColdVectorExpansion,
                1_000_000u32.saturating_sub(index.saturating_mul(10_000)),
                index_epoch_hash,
            )
        })
        .collect();

    c.bench_function("cold_vector_expansion_replay_record", |b| {
        b.iter(|| {
            let record = ColdVectorExpansionReplayRecord::new(
                index_epoch_hash,
                black_box(&query_terms),
                black_box(8),
                black_box(bench_hash("cold-vector-config", 0)),
                black_box(bench_hash("cold-vector-artifact", 0)),
                black_box(88_000),
                black_box(&candidates),
            )
            .expect("cold vector expansion replay record");
            black_box((record.candidate_count, record.record_hash))
        })
    });
}

fn bench_cold_vector_index_query(c: &mut Criterion) {
    let index = cold_vector_bench_index(4096, 32);
    let query: Vec<i16> = (0..32).map(|dim| ((dim * 13 + 7) % 127) as i16).collect();
    let mut scratch = index.query_scratch();

    c.bench_function("cold_vector_index_query", |b| {
        b.iter(|| {
            let candidates = index
                .query_top_k_into_scratch(black_box(&query), black_box(16), &mut scratch)
                .expect("cold vector query");
            black_box((
                candidates.len(),
                candidates.first().map(|c| c.score_quantized),
            ))
        })
    });
}

fn bench_hot_bitmap_filter_intersection(c: &mut Criterion) {
    let (active_run, risk_allowed, has_evidence_ref) = hot_bitmap_bench_sets(65_536);
    let first_pass = active_run.intersect(&risk_allowed);
    let mut output = Vec::with_capacity(first_pass.len().min(has_evidence_ref.len()));

    c.bench_function("hot_bitmap_filter_intersection", |b| {
        b.iter(|| {
            first_pass.intersect_into(black_box(&has_evidence_ref), &mut output);
            black_box((
                output.len(),
                output.first().copied(),
                output.last().copied(),
            ))
        })
    });
}

fn bench_hot_term_dictionary_prefix_expand(c: &mut Criterion) {
    let dictionary = hot_term_bench_dictionary(50_000);
    let query_prefix = "core_rust_symbol_1";

    c.bench_function("hot_term_dictionary_prefix_expand", |b| {
        b.iter(|| {
            let expanded = dictionary.expand_prefix(black_box(query_prefix), black_box(32));
            black_box((
                expanded.len(),
                expanded.first().map(|term_ref| term_ref.expansion_hash),
            ))
        })
    });
}

fn bench_runtime_latest_summary(c: &mut Criterion) {
    c.bench_function("runtime_latest_summary", |b| {
        b.iter(|| {
            let mut runtime = NerveRuntime::new();
            let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3, 4]);
            let _ = runtime.ingest_message(message);
            black_box(runtime.latest_session_summary())
        })
    });
}

fn bench_runtime_is_ready(c: &mut Criterion) {
    c.bench_function("runtime_is_ready", |b| {
        b.iter(|| {
            let runtime = NerveRuntime::new();
            black_box(runtime.is_ready())
        })
    });
}

fn bench_generational_slab_get_hot(c: &mut Criterion) {
    let mut slab = GenerationalSlab::with_layout_budget(
        RuntimeLayoutBudget::bounded(16_384, 1_048_576).unwrap(),
    )
    .expect("layout budget slab");
    let mut slots = Vec::with_capacity(8_192);
    for index in 0..8_192 {
        let payload = format!("frame-{index:04}");
        slots.push(slab.try_insert(payload).expect("bench slab insert"));
    }
    let mut cursor = 0usize;

    c.bench_function("generational_slab_get_hot", |b| {
        b.iter(|| {
            cursor = (cursor + 1) & 0x1fff;
            black_box(slab.get(black_box(slots[cursor])).map(String::as_str))
        })
    });
}

fn bench_generational_slab_len(c: &mut Criterion) {
    let mut slab = GenerationalSlab::with_layout_budget(
        RuntimeLayoutBudget::bounded(16_384, 1_048_576).unwrap(),
    )
    .expect("layout budget slab");
    for index in 0..8_192 {
        let payload = format!("frame-{index:04}");
        slab.try_insert(payload).expect("bench slab insert");
    }

    c.bench_function("generational_slab_len", |b| {
        b.iter(|| {
            black_box((
                black_box(&slab).len(),
                black_box(&slab).total_payload_bytes(),
            ))
        })
    });
}

fn replay_io_bench_ledger(event_rounds: u32) -> RunEventLedger {
    let mut ledger = RunEventLedger::new(9_001);
    seed_bench_goal_intake(&mut ledger, "replay-io");
    ledger
        .append_context_pack_built(1, 512, bench_hash("context-pack", 0))
        .expect("context pack event");

    for round in 1..=event_rounds {
        ledger
            .append_llm_response_received(
                bench_hash("llm-response", round),
                bench_hash("raw-text-ref", round),
            )
            .expect("llm response event");
        ledger
            .append_llm_derived_tool_call_requested(
                round as u128,
                bench_hash("typed-tool-ir", round),
            )
            .expect("tool-call event");
        ledger
            .append_policy_decision_recorded(
                round as u128,
                bench_hash("policy-proof", round),
                bench_hash("typed-tool-ir", round),
            )
            .expect("policy decision event");
        ledger
            .append_operator_review_artifact_recorded(
                round as u128,
                bench_hash("operator-review-signing-target", round),
                bench_hash("operator-review-artifact", round),
            )
            .expect("operator review event");
        let tool_evidence = ToolExecutionEvidence::new(
            bench_hash("typed-tool-ir", round),
            bench_hash("policy-proof", round),
            bench_hash("tool-output", round),
            bench_hash("physical-witness", round),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        ledger
            .append_tool_call_completed(round as u128, &tool_evidence)
            .expect("tool completion event");
        ledger
            .append_checkpoint_sealed(round as u128, bench_hash("checkpoint", round))
            .expect("checkpoint event");
    }

    ledger
}

fn bench_replay_determinism_from_ledger(
    ledger: &RunEventLedger,
    label: &str,
) -> ReplayDeterminismProof {
    let dir = std::env::current_dir()
        .expect("bench current dir")
        .join("target")
        .join("aegis-bench-artifacts")
        .join(format!("replay-determinism-{label}"));
    let _ = std::fs::remove_dir_all(&dir);
    let manifest =
        RunEventSegmentArchive::write_ledger(&dir, 8, ledger).expect("bench replay proof ledger");
    RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest)
        .expect("bench replay determinism proof")
}

fn seed_bench_goal_intake(ledger: &mut RunEventLedger, label: &str) {
    let proof = GoalIntakeProof::from_goal_text(
        9_001,
        bench_hash(label, 1),
        bench_hash(label, 2),
        bench_hash(label, 3),
        "Use browser to inspect the website and fetch evidence, no submit",
        1,
        None,
    )
    .expect("bench goal intake proof");
    ledger
        .append_goal_intake_recorded(&proof)
        .expect("bench goal intake replay event");
}

fn bench_hash(label: &str, value: u32) -> [u8; 32] {
    let mut hasher = blake3::Hasher::new();
    hasher.update(label.as_bytes());
    hasher.update(&value.to_le_bytes());
    *hasher.finalize().as_bytes()
}

fn hot_engine_browser_payload() -> Vec<u8> {
    let mut payload = Vec::with_capacity(4096);
    while payload.len() < 4096 {
        payload.extend_from_slice(
            br#"<html><body><main data-aegis="browser-capture"><button id="run">run</button><pre>policy witness replay screenshot network</pre></main></body></html>"#,
        );
    }
    payload.truncate(4096);
    payload
}

fn bench_batch_candidate(
    request_id: u128,
    provider: &ProviderConfig,
    contract: &InferenceBackendContract,
    prompt: &str,
    prompt_tokens: u32,
    output_tokens: u32,
    arrival_index: u64,
    enqueued_at_ms: u64,
) -> ContinuousBatchCandidate {
    let request = LLMRequest {
        request_id,
        session_id: 444,
        prompt: prompt.to_string(),
        system_context: None,
        model_hint: Some(provider.model_name),
        tool_hint: None,
        prefer_low_latency: true,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    };
    let route_proof = InferenceRouteProof::build(&request, &[*provider], &[*contract])
        .expect("bench batch route proof");
    ContinuousBatchCandidate::build(
        &request,
        contract,
        &route_proof,
        prompt_tokens,
        output_tokens,
        arrival_index,
        enqueued_at_ms,
    )
    .expect("bench batch candidate")
}

fn agentic_evidence_sdk_bench_fixture(
    label: &str,
) -> (
    AgenticEvidenceProgram,
    HotLexicalIndex,
    HotEvidenceIndex,
    HotBitmapFilter,
) {
    let epoch_hash = bench_hash(label, 0);
    let lexical = hot_lexical_bench_index_with_epoch(epoch_hash, 10_000);
    let exact = hot_evidence_bench_index_with_epoch(epoch_hash, 10_000);
    let allowed_segments = SortedEvidenceSet::from_unsorted(
        (1u64..=10_000)
            .filter(|segment_id| segment_id % 2 == 0 || segment_id % 17 == 0)
            .collect(),
    );
    let bitmap_filter =
        HotBitmapFilter::new(epoch_hash, allowed_segments).expect("agentic sdk bench bitmap");
    let program = AgenticEvidenceProgram::new(
        epoch_hash,
        96,
        vec![
            AgenticEvidenceProgramStep::lexical_top_k(
                &["rust", "witness", "policy", "segment"],
                32,
            ),
            AgenticEvidenceProgramStep::exact_artifact_rerank(
                &["replay", "policy", "witness", "segment"],
                16,
            ),
            AgenticEvidenceProgramStep::bitmap_filter(8),
        ],
    )
    .expect("agentic evidence sdk bench program");
    (program, lexical, exact, bitmap_filter)
}

fn agentic_evidence_sdk_small_bench_fixture(
    label: &str,
) -> (
    AgenticEvidenceProgram,
    HotLexicalIndex,
    HotEvidenceIndex,
    HotBitmapFilter,
) {
    let epoch_hash = bench_hash(label, 0);
    let mut lexical = HotLexicalIndex::new(epoch_hash).expect("agentic sdk small lexical");
    lexical
        .insert_document(
            bench_hash(label, 10),
            27,
            &["browser", "policy", "replay", "witness"],
        )
        .expect("agentic sdk small lexical insert");
    lexical
        .insert_document(bench_hash(label, 11), 29, &["wasmtime", "policy", "fuel"])
        .expect("agentic sdk small lexical insert");
    let mut exact = HotEvidenceIndex::new(epoch_hash).expect("agentic sdk small exact");
    exact
        .insert_artifact_document(
            bench_hash(label, 10),
            27,
            bench_hash(label, 12),
            bench_hash(label, 13),
            "browser policy replay witness DOM screenshot artifact",
        )
        .expect("agentic sdk small exact insert");
    exact
        .insert_artifact_document(
            bench_hash(label, 11),
            29,
            bench_hash(label, 14),
            bench_hash(label, 15),
            "wasmtime fuel policy",
        )
        .expect("agentic sdk small exact insert");
    let bitmap_filter =
        HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![27]))
            .expect("agentic sdk small bitmap");
    let program = AgenticEvidenceProgram::new(
        epoch_hash,
        64,
        vec![
            AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay", "witness"], 8),
            AgenticEvidenceProgramStep::exact_artifact_rerank(
                &["browser", "witness", "artifact"],
                4,
            ),
            AgenticEvidenceProgramStep::bitmap_filter(2),
        ],
    )
    .expect("agentic evidence sdk small bench program");
    (program, lexical, exact, bitmap_filter)
}

fn quickjs_bench_wasm() -> Vec<u8> {
    wat::parse_str(r#"(module (func (export "_start")))"#).expect("quickjs bench wrapper")
}

fn quickjs_bridge_bench_wasm(packet_len: usize) -> Vec<u8> {
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
    wat::parse_str(&wat).expect("quickjs bridge bench wrapper")
}

fn wasmtime_cached_bench_wasm() -> Vec<u8> {
    wat::parse_str(
        r#"(module
            (func (export "_start")
                i32.const 1
                drop
                i32.const 2
                drop)
        )"#,
    )
    .expect("wasmtime cached module bench wrapper")
}

fn skill_admission_bench_fixture() -> (
    SkillPackageManifest,
    SkillAdmissionRecord,
    SkillRegressionReport,
) {
    let wasm = wasmtime_cached_bench_wasm();
    let sandbox = WasmtimeSandbox::new();
    let sandbox_result = sandbox
        .execute_wasm_binary(&wasm, 10_000)
        .expect("skill admission bench wasm");
    let cases = vec![
        SkillRegressionCase::new(
            1,
            bench_hash("skill-regression-input-1", 1),
            bench_hash("skill-regression-expected-1", 1),
            bench_hash("skill-regression-expected-1", 1),
            true,
        )
        .expect("skill regression case"),
        SkillRegressionCase::new(
            2,
            bench_hash("skill-regression-input-2", 2),
            bench_hash("skill-regression-expected-2", 2),
            bench_hash("skill-regression-expected-2", 2),
            true,
        )
        .expect("skill regression case"),
    ];
    let report = SkillRegressionReport::new(&cases).expect("skill regression report");
    let manifest = SkillPackageManifest::new(
        7001,
        bench_hash("skill-name", 1),
        bench_hash("skill-version", 1),
        bench_hash("skill-md", 1),
        sandbox_result.artifact.artifact_hash,
        bench_hash("skill-policy-manifest", 1),
        report.case_list_hash,
    )
    .expect("skill package manifest");
    let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
        &manifest,
        &sandbox_result,
        &report,
        &PhysicalWatchdog { epsilon: 0.0 },
    )
    .expect("skill admission record");
    (manifest, admission, report)
}

fn generated_ast_bench_code(function_count: u32, salt: u32) -> String {
    let mut code = String::with_capacity(function_count as usize * 96);
    for function_index in 0..function_count {
        let offset = function_index.wrapping_mul(17).wrapping_add(salt);
        code.push_str(&format!(
            "fn bench_fn_{function_index}(input: u64) -> u64 {{ let base = input.wrapping_add({offset}); base.rotate_left({}) ^ {} }}\n",
            (function_index + salt) % 31,
            offset.wrapping_mul(13),
        ));
    }
    code
}

fn context_bench_governor(global_nodes: u128, active_fanout: u128) -> ContextGovernor {
    let mut governor =
        ContextGovernor::new(ContextGovernorConfig::bounded(4_096, 256)).expect("context governor");

    for node_id in 1..=global_nodes {
        let kind = if node_id == 1 {
            ContextNodeKind::Task
        } else if node_id <= active_fanout + 1 {
            ContextNodeKind::Evidence
        } else if node_id % 5 == 0 {
            ContextNodeKind::Memory
        } else {
            ContextNodeKind::File
        };
        let utility = ((node_id * 37) % 10_000) as u32 + 1;
        let coverage = ((node_id * 17) % 512) as u32;
        let contradiction = ((node_id * 7) % 31) as u32;
        governor
            .insert_node(ContextNode::new(
                node_id,
                kind,
                16 + (node_id % 32) as u32,
                utility,
                coverage,
                contradiction,
            ))
            .expect("context node");
    }

    for node_id in 2..=active_fanout + 1 {
        governor.add_edge(1, node_id).expect("active edge");
        let second_hop = active_fanout + node_id;
        governor
            .add_edge(node_id, second_hop)
            .expect("second-hop edge");
    }

    for node_id in (active_fanout * 3)..global_nodes {
        if node_id < global_nodes {
            governor
                .add_edge(node_id, node_id + 1)
                .expect("global distractor edge");
        }
    }

    governor
}

fn task_ledger_bench_tree(task_count: u128) -> TaskLedger {
    let mut ledger = TaskLedger::new(60_000);
    for task_id in 1..=task_count {
        let dependencies = if task_id == 1 {
            Vec::new()
        } else {
            vec![task_id / 2]
        };
        ledger
            .insert_task(TaskCard::new(
                task_id,
                dependencies,
                (task_id % 8) as u32,
                Some(120_000 + task_id as u64),
                Some((task_id % 97) as i64),
            ))
            .expect("bench task");
    }
    ledger.complete_task(1).expect("root task status");
    ledger
}

fn hot_lexical_bench_index(doc_count: u32) -> HotLexicalIndex {
    hot_lexical_bench_index_with_epoch(bench_hash("hot-lexical-index", 0), doc_count)
}

fn hot_lexical_bench_index_with_epoch(
    index_epoch_hash: [u8; 32],
    doc_count: u32,
) -> HotLexicalIndex {
    let mut index = HotLexicalIndex::new(index_epoch_hash).expect("hot lexical bench index");
    let term_pool = [
        "rust",
        "wasm",
        "witness",
        "policy",
        "segment",
        "replay",
        "context",
        "browser",
        "sandbox",
        "approval",
        "arrow",
        "ledger",
        "quickjs",
        "candidate",
        "index",
        "epoch",
    ];
    for doc_id in 0..doc_count {
        let base = doc_id as usize;
        let terms = [
            term_pool[base % term_pool.len()],
            term_pool[(base + 3) % term_pool.len()],
            term_pool[(base + 5) % term_pool.len()],
            term_pool[(base + 8) % term_pool.len()],
            term_pool[(base + 13) % term_pool.len()],
        ];
        index
            .insert_document(bench_hash("lexical-doc", doc_id), doc_id as u64 + 1, &terms)
            .expect("bench lexical document");
    }
    index
}

fn hot_evidence_bench_index(doc_count: u32) -> HotEvidenceIndex {
    hot_evidence_bench_index_with_epoch(bench_hash("hot-evidence-index", 0), doc_count)
}

fn hot_evidence_bench_index_with_epoch(
    index_epoch_hash: [u8; 32],
    doc_count: u32,
) -> HotEvidenceIndex {
    let mut index = HotEvidenceIndex::new(index_epoch_hash).expect("hot evidence bench index");
    let term_pool = [
        "rust",
        "wasm",
        "witness",
        "policy",
        "segment",
        "replay",
        "context",
        "browser",
        "sandbox",
        "approval",
        "arrow",
        "ledger",
        "quickjs",
        "candidate",
        "index",
        "epoch",
    ];
    for doc_id in 0..doc_count {
        let base = doc_id as usize;
        let document_text = format!(
            "{} {} {} {} {} artifact_{} ast_signature_{}",
            term_pool[base % term_pool.len()],
            term_pool[(base + 3) % term_pool.len()],
            term_pool[(base + 5) % term_pool.len()],
            term_pool[(base + 8) % term_pool.len()],
            term_pool[(base + 13) % term_pool.len()],
            doc_id,
            doc_id % 257,
        );
        index
            .insert_artifact_document(
                bench_hash("hot-evidence-ref", doc_id),
                doc_id as u64 + 1,
                bench_hash("hot-evidence-artifact", doc_id),
                bench_hash("hot-evidence-ast", doc_id),
                &document_text,
            )
            .expect("bench hot evidence document");
    }
    index
}

fn hot_bitmap_bench_sets(
    max_segment_id: u64,
) -> (SortedEvidenceSet, SortedEvidenceSet, SortedEvidenceSet) {
    let active_run: Vec<u64> = (1..=max_segment_id)
        .filter(|segment_id| segment_id % 2 == 0)
        .collect();
    let risk_allowed: Vec<u64> = (1..=max_segment_id)
        .filter(|segment_id| segment_id % 3 != 0)
        .collect();
    let has_evidence_ref: Vec<u64> = (1..=max_segment_id)
        .filter(|segment_id| segment_id % 5 != 0 && segment_id % 7 != 0)
        .collect();
    (
        SortedEvidenceSet::from_unsorted(active_run),
        SortedEvidenceSet::from_unsorted(risk_allowed),
        SortedEvidenceSet::from_unsorted(has_evidence_ref),
    )
}

fn hot_term_bench_dictionary(term_count: u32) -> HotTermDictionary {
    let terms: Vec<String> = (0..term_count)
        .map(|index| match index % 4 {
            0 => format!("core_rust_symbol_{index:05}"),
            1 => format!("core_python_symbol_{index:05}"),
            2 => format!("planning_section_{index:05}"),
            _ => format!("artifact_segment_{index:05}"),
        })
        .collect();
    let term_refs: Vec<&str> = terms.iter().map(String::as_str).collect();
    HotTermDictionary::from_terms(bench_hash("hot-term-dictionary", 0), &term_refs)
        .expect("hot term bench dictionary")
}

fn cold_vector_bench_index(doc_count: u32, dimension: usize) -> ColdVectorIndex {
    let epoch_hash = bench_hash("cold-vector-runtime-index", 0);
    let mut index = ColdVectorIndex::new(epoch_hash, dimension).expect("cold vector bench index");
    for doc in 0..doc_count {
        let vector: Vec<i16> = (0..dimension)
            .map(|dim| (((doc as usize * 17 + dim * 31 + 11) % 255) as i16) - 127)
            .collect();
        index
            .insert_vector(
                bench_hash("cold-vector-runtime-ref", doc),
                doc as u64 + 1,
                bench_hash("cold-vector-runtime-artifact", doc),
                &vector,
            )
            .expect("cold vector bench insert");
    }
    index
}

fn bench_cluster_work_envelope(c: &mut Criterion) {
    let input_hash = bench_hash("cluster-envelope-input", 0);
    let evidence_contract_hash = bench_hash("cluster-envelope-contract", 0);
    let policy_window_hash = bench_hash("cluster-envelope-policy", 0);
    c.bench_function("cluster_work_envelope", |b| {
        b.iter(|| {
            let envelope = ClusterWorkEnvelope::new(
                black_box(910),
                black_box(1200),
                black_box(3400),
                black_box(WorkerRole::BrowserWitness),
                black_box(1),
                black_box(vec![input_hash]),
                black_box(evidence_contract_hash),
                black_box(policy_window_hash),
                black_box(aegis_nerve::policy::SideEffectClass::ExternalRead),
                black_box(10_000),
            )
            .expect("cluster work envelope");
            black_box((envelope.is_valid(), envelope.envelope_hash))
        })
    });
}

fn bench_work_lease_acquire(c: &mut Criterion) {
    let envelope = ClusterWorkEnvelope::new(
        910,
        1200,
        3400,
        WorkerRole::BrowserWitness,
        1,
        vec![bench_hash("cluster-lease-input", 0)],
        bench_hash("cluster-lease-contract", 0),
        bench_hash("cluster-lease-policy", 0),
        aegis_nerve::policy::SideEffectClass::ExternalRead,
        10_000,
    )
    .expect("cluster work envelope");
    c.bench_function("work_lease_acquire", |b| {
        b.iter_batched(
            WorkLeaseTable::new,
            |mut leases| {
                let lease = leases
                    .acquire(
                        black_box(&envelope),
                        black_box(77),
                        black_box(100),
                        black_box(1_000),
                    )
                    .expect("work lease");
                black_box((lease.is_valid_for(&envelope, 500), lease.lease_hash))
            },
            BatchSize::SmallInput,
        )
    });
}

fn bench_single_writer_cluster_commit(c: &mut Criterion) {
    let envelope = ClusterWorkEnvelope::new(
        910,
        1200,
        3400,
        WorkerRole::BrowserWitness,
        1,
        vec![bench_hash("cluster-commit-input", 0)],
        bench_hash("cluster-commit-contract", 0),
        bench_hash("cluster-commit-policy", 0),
        aegis_nerve::policy::SideEffectClass::ExternalRead,
        10_000,
    )
    .expect("cluster work envelope");
    let mut lease_table = WorkLeaseTable::new();
    let lease = lease_table
        .acquire(&envelope, 77, 100, 1_000)
        .expect("work lease");
    let candidate = CandidateArtifactRef::new(
        &envelope,
        77,
        bench_hash("cluster-commit-artifact", 0),
        bench_hash("cluster-commit-artifact-kind", 0),
        4096,
    )
    .expect("candidate artifact ref");
    c.bench_function("single_writer_cluster_commit", |b| {
        b.iter_batched(
            || {
                let mut ledger = RunEventLedger::new(envelope.run_id);
                seed_bench_goal_intake(&mut ledger, "cluster-commit");
                (ledger, SingleWriterRunLog::new(envelope.run_id))
            },
            |(mut ledger, mut writer)| {
                let admission = writer
                    .commit_candidate(
                        black_box(&mut ledger),
                        black_box(&envelope),
                        black_box(&lease),
                        black_box(&candidate),
                        black_box(ClusterPartitionState::WriterReachable),
                        black_box(500),
                    )
                    .expect("cluster candidate admission");
                black_box((admission, ledger.last_hash()))
            },
            BatchSize::SmallInput,
        )
    });
}

fn bench_memory_periodic_nudge(c: &mut Criterion) {
    // Threshold of 0.5 lets ~70% of synthesised candidates pass the filter,
    // exercising the full hash+ledger-append hot path rather than the
    // empty-candidates short-circuit.
    let system = MemoryNudgeSystem::new(0.5);

    let raw32: Vec<MemoryCandidate> = (0..32)
        .map(|i| make_memory_candidate(i, ((i as f32) * 0.21).sin().abs()))
        .collect();
    let raw256: Vec<MemoryCandidate> = (0..256)
        .map(|i| make_memory_candidate(i, ((i as f32) * 0.13).cos().abs()))
        .collect();

    c.bench_function("memory_periodic_nudge_filter_hash_32", |b| {
        b.iter_batched(
            LearningLedger::new,
            |mut ledger| {
                let r = system
                    .periodic_nudge(
                        black_box(0xABCD_EF01_2345_6789),
                        black_box(7_777_777),
                        black_box(raw32.clone()),
                        black_box(&mut ledger),
                        black_box(None),
                    )
                    .expect("periodic_nudge bench");
                black_box(r.nudge_hash)
            },
            BatchSize::SmallInput,
        )
    });

    c.bench_function("memory_periodic_nudge_filter_hash_256", |b| {
        b.iter_batched(
            LearningLedger::new,
            |mut ledger| {
                let r = system
                    .periodic_nudge(
                        black_box(0xDEAD_BEEF_CAFE_F00D),
                        black_box(8_888_888),
                        black_box(raw256.clone()),
                        black_box(&mut ledger),
                        black_box(None),
                    )
                    .expect("periodic_nudge 256 bench");
                black_box(r.nudge_hash)
            },
            BatchSize::SmallInput,
        )
    });

    // Micro-bench: LearningLedger.append with a MemoryPersisted event.
    // This is the cryptographic land-path of periodic_nudge, isolated so
    // regressions in BLAKE3 throughput are visible without per-iter setup.
    let persisted_hash = {
        let mut h = [0u8; 32];
        for (i, b) in b"aegis-memory-persisted-bench-fixture".iter().enumerate() {
            h[i.min(31)] ^= *b;
        }
        h
    };
    c.bench_function("learning_ledger_append_memory_persisted", |b| {
        b.iter_batched(
            LearningLedger::new,
            |mut ledger| {
                black_box(ledger.append(
                    LearningEventType::MemoryPersisted {
                        memory_hash: persisted_hash,
                    },
                    None,
                    1_700_000_001_000,
                    9_999,
                ));
            },
            BatchSize::SmallInput,
        )
    });
}

fn make_memory_candidate(seed: u64, relevance: f32) -> MemoryCandidate {
    let content = format!("candidate-payload-seed-{}", seed);
    // Use MemoryCandidate::new so the content_hash matches the
    // domain-separator BLAKE3 hash that is_valid() recomputes and
    // compares against. Manually constructing the struct would
    // produce a hash mismatch and a spurious InvalidCandidate panic.
    MemoryCandidate::new(content, relevance, 100u128 + (seed as u128))
        .expect("memory candidate bench fixture must validate")
}

fn make_passing_regression_report() -> SkillRegressionReport {
    let case = SkillRegressionCase::new(1, [0x11; 32], [0x22; 32], [0x22; 32], true)
        .expect("regression case bench fixture");
    SkillRegressionReport::new(&[case]).expect("regression report bench fixture")
}

fn bench_skill_self_improvement(c: &mut Criterion) {
    // Excuse-path benchmark: usage_count below the InsufficientUsage threshold
    // of 10 rejects immediately. Measures gate-check overhead without paying
    // for the WASM hashing + regression-report verification path.
    let low_usage = SkillUsageStats::new(3, 0.95);
    let (manifest, admission, report) = skill_admission_bench_fixture();
    let wasm: Vec<u8> = vec![0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00];
    let rejection_report = make_passing_regression_report();

    c.bench_function(
        "skill_improve_skill_from_usage_insufficient_usage_gate",
        |b| {
            b.iter_batched(
                || {
                    let mut registry = SkillRegistry::new();
                    registry
                        .commit_admitted_skill(&manifest, &admission, &report)
                        .expect("bench: seed registry admission");
                    let ledger = LearningLedger::new();
                    let seed_id = manifest.skill_id;
                    (registry, ledger, seed_id)
                },
                |(mut registry, mut ledger, skill_id)| {
                    let r = registry.improve_skill_from_usage(
                        black_box(skill_id),
                        black_box(&low_usage),
                        black_box(&wasm),
                        black_box(&rejection_report),
                        black_box(&mut ledger),
                        black_box(None),
                    );
                    black_box(r.err().map(|e| format!("{:?}", e)).unwrap_or_default());
                },
                BatchSize::SmallInput,
            )
        },
    );

    // Happy-path overhead: pre-warm a registry that contains a single
    // admitted skill, then measure the deterministic per-call cost paid by
    // `improve_skill_from_usage` once all gates pass.
    // The bench exercises InsufficientUsage + RegressionFailed + look-up
    // paths that sit in front of the BLAKE3 WASM-hash land.
    let adequate = SkillUsageStats::new(50, 0.95);
    c.bench_function("skill_improve_skill_from_usage_happy_path", |b| {
        b.iter_batched(
            || {
                let mut registry = SkillRegistry::new();
                registry
                    .commit_admitted_skill(&manifest, &admission, &report)
                    .expect("bench: seed registry");
                let ledger = LearningLedger::new();
                let skill_id = manifest.skill_id;
                (registry, ledger, skill_id)
            },
            |(mut registry, mut ledger, skill_id)| {
                let r = registry.improve_skill_from_usage(
                    black_box(skill_id),
                    black_box(&adequate),
                    black_box(&wasm),
                    black_box(&rejection_report),
                    black_box(&mut ledger),
                    black_box(None),
                );
                // Successful path returns Ok(SkillImprovementRecord); Err path
                // returns the gate-rejection enum. Either is valid input for
                // black_box — we just want the compiler to keep the call.
                black_box(
                    r.err()
                        .map(|e| format!("{:?}", e))
                        .unwrap_or_default()
                        .len(),
                );
            },
            BatchSize::SmallInput,
        )
    });
}

criterion_group!(
    benches,
    bench_descriptor_validation,
    bench_goal_intake_classify_packet,
    bench_layout_validation,
    bench_message_to_zero_copy,
    bench_zero_copy_roundtrip,
    bench_zero_copy_validation,
    bench_runtime_ingest,
    bench_runtime_ingest_batch,
    bench_runtime_latest_summary,
    bench_runtime_is_ready,
    bench_generational_slab_get_hot,
    bench_generational_slab_len,
    bench_llm_route,
    bench_llm_runtime_checkpoint,
    bench_inference_route_contract_proof,
    bench_provider_route_admission_proof,
    bench_prefix_cache_broker_hit_proof,
    bench_structured_output_proof,
    bench_continuous_batch_fairness_proof,
    bench_speculative_decode_equivalence_proof,
    bench_llm_loop_circuit_breaker_observe,
    bench_quickjs_invocation_abi_prepare,
    bench_quickjs_linear_memory_write,
    bench_quickjs_wasmtime_bridge_validate,
    bench_wasmtime_execute_cached_module,
    bench_skill_admission_record_validate,
    bench_pav_ast_distance_registered,
    bench_mmap_bridge_payload_view,
    bench_mmap_wasm_bridge_execute,
    bench_replay_io_mmap_materialized,
    bench_replay_segmented_arrow_append,
    bench_replay_segmented_arrow_proof,
    bench_replay_segmented_arrow_column_scan_proof,
    bench_replay_segmented_arrow_manifest_recover,
    bench_replay_determinism_proof,
    bench_replay_segmented_arrow_compact,
    bench_replay_shift_manager_next_action,
    bench_browser_witness_proof_validate,
    bench_browser_witness_packet_validate,
    bench_browser_collector_envelope_mint_packet,
    bench_browser_file_artifact_envelope_mint_packet,
    bench_browser_live_collector_run_write_manifest,
    bench_browser_tool_gateway_ingest,
    bench_browser_file_artifact_gateway_ingest,
    bench_browser_live_collector_manifest_gateway_ingest,
    bench_browser_action_plan_live_manifest_gateway_ingest,
    bench_browser_action_plan_file_artifact_gateway_ingest,
    bench_browser_action_plan_bind_packet,
    bench_policy_datalog_closure_proof,
    bench_replay_io_binary_mmap_fixed,
    bench_replay_io_binary_mmap_verify,
    bench_replay_io_binary_mmap_recover_prefix,
    bench_replay_endurance_report_verify_100h,
    bench_context_governor_activated_pack,
    bench_context_fold_record,
    bench_context_length_stress,
    bench_task_ledger_ready_queue_10k,
    bench_task_ledger_ready_queue_10k_recompute,
    bench_hot_lexical_index_top_k,
    bench_hot_evidence_index_exact_aho,
    bench_hot_evidence_index_ast_signature_lookup,
    bench_hot_engine_simd_blake3_hash,
    bench_hot_engine_arena_commit,
    bench_agentic_evidence_program_execute,
    bench_agentic_evidence_ast_signature_program_execute,
    bench_agentic_evidence_sdk_run,
    bench_agentic_evidence_sdk_run_replay_binding_hash,
    bench_agentic_evidence_sdk_run_replay_append,
    bench_agentic_evidence_sdk_run_handoff_seal,
    bench_agentic_evidence_sdk_run_handoff_context_pack,
    bench_candidate_state_capsule_roundtrip,
    bench_browser_page_search_candidate_record,
    bench_index_epoch_replay_record,
    bench_cold_vector_expansion_replay_record,
    bench_cold_vector_index_query,
    bench_hot_bitmap_filter_intersection,
    bench_hot_term_dictionary_prefix_expand,
    bench_cluster_work_envelope,
    bench_work_lease_acquire,
    bench_single_writer_cluster_commit,
    bench_memory_periodic_nudge,
    bench_skill_self_improvement
);
criterion_main!(benches);
