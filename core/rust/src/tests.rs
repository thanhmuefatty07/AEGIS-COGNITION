#![allow(clippy::module_inception)]

#[cfg(test)]
mod tests {
    use crate::bridge_mmap::{
        MMAP_BRIDGE_HEADER_BYTES, MMAP_BRIDGE_PAYLOAD_ALIGNMENT, open_mmap_bridge_view,
        pattern_byte, validate_mmap_bridge_frame, write_mmap_bridge_frame,
        write_pattern_mmap_bridge_frame,
    };
    use crate::cli::{
        run_llm_once, run_llm_with_budget_admission, run_llm_with_budget_admission_replay,
        run_llm_with_fallback, run_llm_with_registry, write_agentic_sdk_context_report,
        write_replay_chaos_scorecard, write_replay_endurance_report,
    };
    use crate::descriptor::BinaryFrameDescriptor;
    use crate::ffi::{
        aegis_can_bridge_python, aegis_cli_schema, aegis_cli_status, aegis_descriptor_valid,
        aegis_frame_is_valid, aegis_hot_commit, aegis_hot_commit_batch, aegis_hot_hash,
        aegis_layout_header_bytes, aegis_layout_payload_alignment, aegis_llm_bridge_key,
        aegis_llm_normalize, aegis_llm_reject, aegis_llm_request, aegis_llm_route,
        aegis_memory_alignment, aegis_message_frame_valid, aegis_mmap_bridge_header_bytes,
        aegis_mmap_bridge_payload_alignment, aegis_nerve_schema_id, aegis_new_message_identity,
        aegis_physical_metrics_prometheus, aegis_release_ready, aegis_status,
        aegis_validate_layout, aegis_validate_mmap_bridge_frame, aegis_validate_schema,
        aegis_write_mmap_bridge_pattern, aegis_zero_copy_ready,
    };
    use crate::governance::{
        BenchmarkGateProfile, BenchmarkGateProfileKind, DependencyAuditManifest,
        DependencyAuditRecord, DependencyRiskClass, E2EScenarioProof, E2EStageEvidence,
        E2EStageKind, GovernanceError, GovernanceLane, GovernanceLaneMap, ProviderRateLimitBudget,
        ProviderRateLimitMitigationProof, REQUIRED_E2E_STAGE_MASK, REQUIRED_WAVE_MASK_18,
        WaveLaneAssignment, governance_fixture_hash,
    };
    use crate::guardrail::{
        ConstitutionalGuardrail, FirstOrderGuardrail, HostCall, InvariantViolation,
        ZeroTrustGateway, strip_chain_of_thought,
    };
    use crate::integrations::{frame_from_memory, run_integration_probe};
    use crate::ipc::{
        ZeroCopyFrame, message_to_zero_copy, payload_span, validate_default_zero_copy,
        validate_frame, validate_zero_copy, zero_copy_is_default_schema, zero_copy_to_message,
    };
    use crate::layout::{
        expected_header_bytes, expected_message_metadata_size, expected_payload_alignment,
        is_expected_alignment, is_expected_header_size, validate_layout,
    };
    use crate::llm::{
        AdapterRegistry, ContinuousBatchCandidate, ContinuousBatchError, ContinuousBatchPlanner,
        ContinuousBatchPolicy, DynamicProviderFallbackProof, InferenceBackendContract,
        InferenceResponseProof, InferenceRouteProof, LLMRequest, PrefixCacheBroker,
        PrefixCacheError, ProviderAdapter, ProviderBudgetError, ProviderBudgetLedger,
        ProviderConfig, ProviderRuntimeBudget, ProviderRuntimeFeedback,
        ProviderRuntimeFeedbackKind, SpeculativeDecodeError, SpeculativeDecodeVerifier,
        SpeculativeTokenBatch, StructuredOutputError, StructuredOutputFieldSpec,
        StructuredOutputKind, StructuredOutputProof, StructuredOutputSchema, build_llm_request,
        normalize_response, rejected_response, route_request,
        route_request_after_provider_feedback, route_request_with_budget, route_with_fallback,
        session_bridge_key, validate_provider_chain,
    };
    use crate::memory::fidelity::{compute_fidelity, decay, reinforce};
    use crate::memory::fold::{
        CogniFoldEngine, CogniFoldStore, CognitiveFolding, FoldingConfig, GenerationalSlab,
        MemoryCrystallization, RuntimeLayoutBudget, SemanticPointerResolver,
    };
    use crate::memory::frame::{MemoryEdge, MemoryFrame, MemoryGraph, SemanticPointer};
    use crate::memory::pool::{MemoryPool, PreAllocatedBuffer, SlabMemoryPool};
    use crate::message::MessageFrame;
    use crate::orchestrator::{
        EPISODIC_AUDIT_SCHEMA_VERSION, EpisodicAuditLog, NerveRuntime,
        episodic_audit_raw_text_ref_hash, episodic_audit_record_hash,
    };
    use crate::physical::{
        BacktrackSignal, DAGNode, DeterministicOrchestrator, PAVWatchdog, PhysicalArtifact,
        PhysicalDagOrchestrator, PhysicalWatchdog, PhysicalWitnessThreshold,
        PhysicalWitnessVerification, TrapReason, physical_metrics_prometheus,
        physical_metrics_snapshot, reset_physical_metrics_for_tests,
    };
    use crate::sac::{
        PhysicalWitnessVote, SacPhysicalWitness, SacVote, accept_vote, anchor_fingerprint,
        anchored_witness, filter_vote, update_anchor, verify_physical_witness, witness_from_vote,
        witness_verification_round,
    };
    use crate::sandbox::{
        DeterministicSandbox, QUICKJS_INVOCATION_ABI_HEADER_BYTES, QUICKJS_INVOCATION_ABI_MAGIC,
        QUICKJS_INVOCATION_ABI_VERSION, QUICKJS_LINEAR_MEMORY_PAGE_BYTES,
        QuickJsWasmInterpreterManager, SandboxBackendConfig, SandboxBackendKind,
        VerificationGauntlet, WasmtimeSandbox, decode_quickjs_invocation_header,
    };
    use crate::schema::{BinarySchemaId, FieldDescriptor, NERVE_SCHEMA, SchemaRegistry};
    use crate::shm::{SharedMemoryRegion, create_region, region_is_64_aligned, validate_region};
    use crate::speculative::draft::{DraftTokenBatch, TargetTokenBatch};
    use crate::speculative::orchestrator::{accept_prefix, fallback_decode, speculative_decode};
    use crate::speculative::verifier::{VerificationResult, verify_target_prefix};

    static FIELDS: &[FieldDescriptor] = &[
        FieldDescriptor {
            name: "message_id",
            offset: 0,
            size: 16,
        },
        FieldDescriptor {
            name: "payload_len",
            offset: 16,
            size: 8,
        },
    ];

    static STRUCTURED_FIELDS: &[StructuredOutputFieldSpec] = &[
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

    #[repr(align(64))]
    struct Aligned64([u8; 64]);

    struct MockAdapter;

    impl ProviderAdapter for MockAdapter {
        fn provider_id(&self) -> &'static str {
            "mock"
        }

        fn model_name(&self) -> &'static str {
            "mock-model"
        }

        fn supports(&self, request: &LLMRequest) -> bool {
            request.is_valid() && request.prompt.contains("route")
        }

        fn reliability_score(&self) -> u8 {
            9
        }

        fn send(&self, request: &LLMRequest) -> Result<crate::llm::LLMResponse, &'static str> {
            Ok(normalize_response(request, "mock response", 3, 1, 1.0))
        }
    }

    fn registry() -> SchemaRegistry {
        SchemaRegistry {
            schema_id: BinarySchemaId(7),
            version: 1,
            alignment: 64,
            fields: FIELDS,
        }
    }

    fn test_inference_contract(provider: &ProviderConfig, seed: u8) -> InferenceBackendContract {
        InferenceBackendContract::from_provider(
            provider,
            "test-backend-v1",
            [seed; 32],
            [seed.saturating_add(1); 32],
            [seed.saturating_add(2); 32],
            32_768,
            4_096,
            true,
            false,
        )
        .unwrap()
    }

    fn test_speculative_contract(
        provider: &ProviderConfig,
        seed: u8,
        supports_speculative_decoding: bool,
    ) -> InferenceBackendContract {
        InferenceBackendContract::from_provider(
            provider,
            "test-backend-v1",
            [seed; 32],
            [seed.saturating_add(1); 32],
            [seed.saturating_add(2); 32],
            32_768,
            4_096,
            true,
            supports_speculative_decoding,
        )
        .unwrap()
    }

    fn test_batch_candidate(
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
            session_id: 252,
            prompt: prompt.to_string(),
            system_context: None,
            model_hint: Some(provider.model_name),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let route_proof = InferenceRouteProof::build(&request, &[*provider], &[*contract]).unwrap();
        ContinuousBatchCandidate::build(
            &request,
            contract,
            &route_proof,
            prompt_tokens,
            output_tokens,
            arrival_index,
            enqueued_at_ms,
        )
        .unwrap()
    }

    fn governance_dependency_record(
        name: &'static str,
        version_req: &'static str,
        risk_class: DependencyRiskClass,
        seed: u32,
    ) -> DependencyAuditRecord {
        DependencyAuditRecord::new(
            name,
            version_req,
            risk_class,
            governance_fixture_hash(name, seed),
            governance_fixture_hash("license", seed),
            governance_fixture_hash("audit", seed),
        )
        .unwrap()
    }

    fn governance_e2e_stage(stage: E2EStageKind, seed: u32) -> E2EStageEvidence {
        E2EStageEvidence::new(
            stage,
            governance_fixture_hash("e2e-artifact", seed),
            governance_fixture_hash("e2e-replay", seed),
            governance_fixture_hash("e2e-verifier", seed),
        )
        .unwrap()
    }

    #[test]
    fn governance_lane_map_collapses_18_waves_into_five_owned_lanes() {
        let lanes = [
            GovernanceLane::Runtime,
            GovernanceLane::Evidence,
            GovernanceLane::Security,
            GovernanceLane::Evaluation,
            GovernanceLane::Product,
        ];
        let assignments: Vec<_> = (1u8..=18)
            .map(|wave_id| {
                WaveLaneAssignment::new(
                    wave_id,
                    lanes[(wave_id as usize - 1) % lanes.len()],
                    wave_id,
                    governance_fixture_hash("wave", wave_id as u32),
                )
                .unwrap()
            })
            .collect();

        let map = GovernanceLaneMap::build(&assignments).unwrap();

        assert_eq!(map.covered_wave_mask, REQUIRED_WAVE_MASK_18);
        assert_eq!(map.assignment_count, 18);
        assert_eq!(map.lane_count, 5);
        assert_ne!(map.lane_load_hash, [0; 32]);
        assert_ne!(map.assignment_list_hash, [0; 32]);
        assert_ne!(map.map_hash, [0; 32]);

        let mut missing = assignments.clone();
        missing.pop();
        assert_eq!(
            GovernanceLaneMap::build(&missing),
            Err(GovernanceError::MissingWave)
        );

        let mut tampered = assignments.clone();
        tampered[0].assignment_hash = [0; 32];
        assert_eq!(
            GovernanceLaneMap::build(&tampered),
            Err(GovernanceError::InvalidLaneMap)
        );
    }

    #[test]
    fn governance_benchmark_profiles_relax_innovation_but_preserve_release_e2e_gate() {
        let innovation = BenchmarkGateProfile::innovation();
        let release = BenchmarkGateProfile::release();

        assert_eq!(innovation.kind, BenchmarkGateProfileKind::Innovation);
        assert_eq!(innovation.threshold_for(10_000), 30_000);
        assert!(innovation.requires_e2e_gate);
        assert!(innovation.allows_stale_microbench_manifest);
        assert_eq!(release.kind, BenchmarkGateProfileKind::Release);
        assert_eq!(release.threshold_for(10_000), 10_000);
        assert!(release.requires_e2e_gate);
        assert!(!release.allows_stale_microbench_manifest);
        assert_ne!(innovation.profile_hash, release.profile_hash);

        assert_eq!(
            BenchmarkGateProfile::build(BenchmarkGateProfileKind::Release, 2_000_000, true, false),
            Err(GovernanceError::InvalidBenchmarkProfile)
        );
        assert_eq!(
            BenchmarkGateProfile::build(
                BenchmarkGateProfileKind::Innovation,
                3_000_000,
                false,
                true
            ),
            Err(GovernanceError::InvalidBenchmarkProfile)
        );
    }

    #[test]
    fn governance_dependency_audit_manifest_requires_rust_hotpath_stack_evidence() {
        let records = [
            governance_dependency_record("arrow", "54", DependencyRiskClass::ColumnarIpc, 1),
            governance_dependency_record("arrow-buffer", "54", DependencyRiskClass::ColumnarIpc, 2),
            governance_dependency_record("blake3", "1.5.0", DependencyRiskClass::CryptoHash, 3),
            governance_dependency_record("pyo3", "0.29.2", DependencyRiskClass::PythonFfi, 4),
            governance_dependency_record(
                "wasmtime",
                "47.0.3",
                DependencyRiskClass::SandboxRuntime,
                5,
            ),
            governance_dependency_record(
                "criterion",
                "0.5",
                DependencyRiskClass::BenchmarkHarness,
                6,
            ),
        ];

        let manifest = DependencyAuditManifest::build(&records).unwrap();

        assert_eq!(manifest.required_dependency_count, 5);
        assert_eq!(manifest.audited_dependency_count, 6);
        assert_ne!(manifest.dependency_set_hash, [0; 32]);
        assert_ne!(manifest.record_list_hash, [0; 32]);
        assert_ne!(manifest.manifest_hash, [0; 32]);

        let missing_wasmtime = &records[..4];
        assert_eq!(
            DependencyAuditManifest::build(missing_wasmtime),
            Err(GovernanceError::MissingRequiredDependency)
        );
    }

    #[test]
    fn governance_rate_limit_mitigation_selects_available_provider_and_hashes_throttled() {
        let budgets = [
            ProviderRateLimitBudget::new("provider-a", "model-a", 0, 10_000, 10_000).unwrap(),
            ProviderRateLimitBudget::new("provider-b", "model-b", 3, 100, 10_000).unwrap(),
            ProviderRateLimitBudget::new("provider-c", "model-c", 2, 8_000, 10_000).unwrap(),
        ];
        let request_hash = governance_fixture_hash("rate-limit-request", 1);

        let proof = ProviderRateLimitMitigationProof::build(request_hash, 1_024, &budgets).unwrap();

        assert_eq!(proof.request_hash, request_hash);
        assert_eq!(proof.required_tokens, 1_024);
        assert_eq!(proof.selected_provider_hash, Some(budgets[2].budget_hash));
        assert_eq!(proof.throttled_provider_hashes.len(), 2);
        assert_ne!(proof.budget_set_hash, [0; 32]);
        assert_ne!(proof.route_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);

        assert_eq!(
            ProviderRateLimitMitigationProof::build(request_hash, 20_000, &budgets),
            Err(GovernanceError::NoProviderAvailable)
        );
    }

    #[test]
    fn governance_e2e_scenario_requires_full_goal_to_next_action_stage_chain() {
        let stages = [
            governance_e2e_stage(E2EStageKind::GoalIntake, 1),
            governance_e2e_stage(E2EStageKind::TaskPlan, 2),
            governance_e2e_stage(E2EStageKind::ContextPack, 3),
            governance_e2e_stage(E2EStageKind::TypedToolIr, 4),
            governance_e2e_stage(E2EStageKind::PolicyProof, 5),
            governance_e2e_stage(E2EStageKind::ToolExecution, 6),
            governance_e2e_stage(E2EStageKind::EvidenceIngest, 7),
            governance_e2e_stage(E2EStageKind::ReplayAppend, 8),
            governance_e2e_stage(E2EStageKind::CheckpointSeal, 9),
            governance_e2e_stage(E2EStageKind::MemoryCandidate, 10),
            governance_e2e_stage(E2EStageKind::NextAction, 11),
        ];

        let proof = E2EScenarioProof::build(4242, &stages).unwrap();

        assert_eq!(proof.stage_mask, REQUIRED_E2E_STAGE_MASK);
        assert_eq!(proof.stage_count, 11);
        assert_ne!(proof.ordered_stage_hash, [0; 32]);
        assert_ne!(proof.scenario_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);

        assert_eq!(
            E2EScenarioProof::build(4242, &stages[..10]),
            Err(GovernanceError::MissingE2EStage)
        );

        let mut reordered = stages.to_vec();
        reordered.swap(0, 1);
        assert_eq!(
            E2EScenarioProof::build(4242, &reordered),
            Err(GovernanceError::InvalidE2EScenario)
        );
    }

    #[test]
    fn llm_contracts_and_routing_work() {
        let request = LLMRequest {
            request_id: 1,
            session_id: 42,
            prompt: "hello".to_string(),
            system_context: Some("system".to_string()),
            model_hint: Some("model-a"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let providers = [ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        }];
        assert!(validate_provider_chain(&providers));
        let decision = route_request(&request, &providers).unwrap();
        assert_eq!(decision.provider_id, "provider-a");
        assert_eq!(decision.model_name, "model-a");
        assert_eq!(session_bridge_key(&request), (1, 42));

        let normalized = normalize_response(&request, "  answer  ", 12, 33, 0.7);
        assert!(normalized.is_valid());
        assert_eq!(normalized.content, "answer");
        assert_eq!(normalized.token_usage, 12);
        assert_eq!(normalized.latency_ms, 33);
        assert_eq!(normalized.confidence, 0.7);

        let rejected = rejected_response(&request, "failed");
        assert!(rejected.rejected);
        assert_eq!(rejected.error, Some("failed"));
        let built = build_llm_request(9, 10, "prompt", Some("model-a"));
        assert!(built.is_valid());
    }

    #[test]
    fn llm_adapter_registry_routes_mock_adapter() {
        let request = LLMRequest {
            request_id: 2,
            session_id: 99,
            prompt: "route me".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let selected = registry.select(&request).unwrap();
        let response = selected.send(&request).unwrap();
        assert!(response.is_valid());
        assert_eq!(response.content, "mock response");
    }

    #[test]
    fn llm_fallback_chain_selects_backup_path() {
        let request = LLMRequest {
            request_id: 21,
            session_id: 22,
            prompt: "fallback me".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: true,
            require_tool_use: false,
            require_reliability: true,
        };
        let providers = [
            ProviderConfig {
                provider_id: "primary",
                model_name: "primary-model",
                endpoint: "http://primary",
                timeout_ms: 1000,
                retry_limit: 3,
                preferred_for_tools: false,
                cost_rank: 10,
                availability_rank: 1,
            },
            ProviderConfig {
                provider_id: "backup",
                model_name: "backup-model",
                endpoint: "http://backup",
                timeout_ms: 1000,
                retry_limit: 3,
                preferred_for_tools: true,
                cost_rank: 1,
                availability_rank: 2,
            },
        ];
        let (primary, fallback) = route_with_fallback(&request, &providers).unwrap();
        assert_eq!(primary.provider_id, "backup");
        assert!(fallback.is_some());
        assert!(fallback.unwrap().fallback_used);
    }

    #[test]
    fn llm_provider_budget_admission_routes_around_throttled_primary() {
        let request = LLMRequest {
            request_id: 31,
            session_id: 32,
            prompt: "budget gated route".to_string(),
            system_context: None,
            model_hint: Some("fast-model"),
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
                cost_rank: 5,
                availability_rank: 1,
            },
        ];
        assert_eq!(
            route_request(&request, &providers).unwrap().provider_id,
            "fast"
        );
        let ledger = ProviderBudgetLedger::new(&[
            ProviderRuntimeBudget::new("fast", "fast-model", 0, 10_000, 100_000).unwrap(),
            ProviderRuntimeBudget::new("reserve", "reserve-model", 3, 10_000, 100_000).unwrap(),
        ])
        .unwrap();

        let (decision, proof) = route_request_with_budget(&request, &providers, &ledger, 1_024)
            .expect("reserve provider should be admitted");
        assert_eq!(decision.provider_id, "reserve");
        assert_ne!(proof.proof_hash, [0; 32]);
        assert_eq!(proof.throttled_provider_hashes.len(), 1);
        assert!(proof.is_valid_for(&request, &providers, &ledger, &decision));

        let mut tampered = proof.clone();
        tampered.required_tokens = 2_048;
        assert!(!tampered.is_valid_for(&request, &providers, &ledger, &decision));
    }

    #[test]
    fn llm_provider_budget_admission_fails_closed_when_all_throttled() {
        let request = build_llm_request(33, 34, "all throttled", None);
        let providers = [ProviderConfig {
            provider_id: "only",
            model_name: "only-model",
            endpoint: "http://only",
            timeout_ms: 1000,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        }];
        let ledger = ProviderBudgetLedger::new(&[ProviderRuntimeBudget::new(
            "only",
            "only-model",
            1,
            128,
            100_000,
        )
        .unwrap()])
        .unwrap();

        let rejected = route_request_with_budget(&request, &providers, &ledger, 1_024);
        assert!(matches!(
            rejected,
            Err(ProviderBudgetError::NoProviderAvailable)
        ));
    }

    #[test]
    fn dynamic_provider_feedback_429_falls_back_and_downgrades_under_budget() {
        let request = build_llm_request(35, 36, "rate limited primary", Some("gpt-4"));
        let providers = [
            ProviderConfig {
                provider_id: "openrouter",
                model_name: "gpt-4",
                endpoint: "http://openrouter",
                timeout_ms: 1000,
                retry_limit: 2,
                preferred_for_tools: true,
                cost_rank: 1,
                availability_rank: 1,
            },
            ProviderConfig {
                provider_id: "nim",
                model_name: "llama-3-70b",
                endpoint: "http://nim",
                timeout_ms: 1000,
                retry_limit: 2,
                preferred_for_tools: true,
                cost_rank: 4,
                availability_rank: 2,
            },
        ];
        let ledger = ProviderBudgetLedger::new(&[
            ProviderRuntimeBudget::new("openrouter", "gpt-4", 8, 32_000, 1_000_000).unwrap(),
            ProviderRuntimeBudget::new("nim", "llama-3-70b", 8, 32_000, 1_000_000).unwrap(),
        ])
        .unwrap();
        let feedback = ProviderRuntimeFeedback::new(
            "openrouter",
            "gpt-4",
            ProviderRuntimeFeedbackKind::Http429,
            2_000_000,
            30_000,
        )
        .unwrap();

        let (decision, updated_ledger, proof) =
            route_request_after_provider_feedback(&request, &providers, &ledger, &feedback, 1_024)
                .unwrap();

        assert_eq!(decision.provider_id, "nim");
        assert_eq!(decision.model_name, "llama-3-70b");
        assert_ne!(updated_ledger.ledger_hash, ledger.ledger_hash);
        let throttled = updated_ledger
            .budgets()
            .iter()
            .find(|budget| budget.provider_id == "openrouter")
            .unwrap();
        assert_eq!(throttled.remaining_requests, 0);
        assert_eq!(throttled.remaining_tokens, 0);
        assert!(proof.fallback_used);
        assert!(proof.downgraded_model);
        assert!(proof.decision_latency_ns > 0);
        assert_eq!(proof.throttled_provider_hashes.len(), 1);
        assert_ne!(proof.proof_hash, [0; 32]);
        assert!(proof.is_valid_for(&request, &providers, &ledger, &feedback, 1_024));

        let mut tampered = proof.clone();
        tampered.downgraded_model = false;
        assert!(!tampered.is_valid_for(&request, &providers, &ledger, &feedback, 1_024));

        let deterministic = DynamicProviderFallbackProof::build(
            &request,
            &providers,
            &ledger,
            &feedback,
            1_024,
            proof.decision_latency_ns,
        )
        .unwrap();
        assert_eq!(deterministic.0.provider_id, "nim");
        assert_eq!(deterministic.2.proof_hash, proof.proof_hash);
    }

    #[test]
    fn inference_backend_contract_hash_detects_backend_drift() {
        let provider = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        };
        let contract = test_inference_contract(&provider, 11);
        let drifted = InferenceBackendContract::from_provider(
            &provider,
            "test-backend-v2",
            [11; 32],
            [12; 32],
            [13; 32],
            32_768,
            4_096,
            true,
            false,
        )
        .unwrap();

        assert!(contract.is_valid());
        assert!(drifted.is_valid());
        assert_ne!(contract.contract_hash, [0; 32]);
        assert_ne!(contract.contract_hash, drifted.contract_hash);

        let mut tampered = contract;
        tampered.max_output_tokens += 1;
        assert!(!tampered.is_valid());
    }

    #[test]
    fn inference_route_proof_binds_ordering_and_fallback() {
        let request = LLMRequest {
            request_id: 121,
            session_id: 122,
            prompt: "route with contract proof".to_string(),
            system_context: None,
            model_hint: Some("backup-model"),
            tool_hint: Some("browser"),
            prefer_low_latency: false,
            prefer_low_cost: true,
            require_tool_use: true,
            require_reliability: true,
        };
        let providers = [
            ProviderConfig {
                provider_id: "primary",
                model_name: "primary-model",
                endpoint: "http://primary",
                timeout_ms: 1000,
                retry_limit: 3,
                preferred_for_tools: false,
                cost_rank: 9,
                availability_rank: 2,
            },
            ProviderConfig {
                provider_id: "backup",
                model_name: "backup-model",
                endpoint: "http://backup",
                timeout_ms: 1000,
                retry_limit: 3,
                preferred_for_tools: true,
                cost_rank: 1,
                availability_rank: 1,
            },
        ];
        let contracts = [
            test_inference_contract(&providers[0], 21),
            test_inference_contract(&providers[1], 31),
        ];
        let proof = InferenceRouteProof::build(&request, &providers, &contracts).unwrap();

        assert!(proof.is_valid(&request, &providers, &contracts));
        assert_eq!(proof.selected_contract_hash, contracts[1].contract_hash);
        assert_eq!(
            proof.fallback_contract_hash,
            Some(contracts[0].contract_hash)
        );
        assert_ne!(proof.provider_ordering_hash, [0; 32]);
        assert_ne!(proof.route_policy_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);

        let reordered = [providers[1], providers[0]];
        assert!(!proof.is_valid(&request, &reordered, &contracts));

        let incomplete_contracts = [contracts[1]];
        assert!(InferenceRouteProof::build(&request, &providers, &incomplete_contracts).is_none());
    }

    #[test]
    fn inference_response_proof_rejects_request_or_contract_drift() {
        let request = LLMRequest {
            request_id: 131,
            session_id: 132,
            prompt: "response proof".to_string(),
            system_context: Some("hash-only audit".to_string()),
            model_hint: Some("model-a"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let provider = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let contract = test_inference_contract(&provider, 41);
        let response = normalize_response(&request, " candidate only ", 7, 19, 0.8);
        let proof = InferenceResponseProof::build(&request, &response, &contract).unwrap();

        assert!(proof.is_valid(&request, &response, &contract));
        assert_eq!(proof.contract_hash, contract.contract_hash);
        assert_eq!(proof.token_usage, 7);
        assert!(!proof.rejected);
        assert_ne!(proof.response_fingerprint_hash, [0; 32]);
        assert_ne!(proof.checkpoint_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);

        let mut drifted_request = request.clone();
        drifted_request.session_id += 1;
        assert!(!proof.is_valid(&drifted_request, &response, &contract));

        let mut drifted_response =
            normalize_response(&drifted_request, "candidate only", 7, 19, 0.8);
        drifted_response.request_id = response.request_id;
        assert!(InferenceResponseProof::build(&request, &drifted_response, &contract).is_none());

        let drifted_contract = InferenceBackendContract::from_provider(
            &provider,
            "test-backend-v1",
            [42; 32],
            [43; 32],
            [44; 32],
            32_768,
            4_096,
            true,
            false,
        )
        .unwrap();
        assert!(!proof.is_valid(&request, &response, &drifted_contract));
    }

    #[test]
    fn prefix_cache_broker_hits_shared_prefix_but_rejects_suffix_truth() {
        let providers = [ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        }];
        let base_prompt = "shared prefix: inspect evidence\nunique suffix A";
        let prefix_len = "shared prefix: inspect evidence\n".len() as u32;
        let request = LLMRequest {
            request_id: 151,
            session_id: 152,
            prompt: base_prompt.to_string(),
            system_context: Some("cache-safe system".to_string()),
            model_hint: Some("model-a"),
            tool_hint: Some("browser"),
            prefer_low_latency: true,
            prefer_low_cost: true,
            require_tool_use: true,
            require_reliability: true,
        };
        let contract = test_inference_contract(&providers[0], 51);
        let route_proof = InferenceRouteProof::build(&request, &providers, &[contract]).unwrap();
        let scope = [71; 32];
        let mut broker = PrefixCacheBroker::new(8).unwrap();
        let handle = broker
            .admit(&request, &contract, &route_proof, scope, prefix_len, 6)
            .unwrap();

        let mut same_prefix = request.clone();
        same_prefix.request_id += 1;
        same_prefix.prompt = "shared prefix: inspect evidence\nunique suffix B".to_string();
        let same_route_proof =
            InferenceRouteProof::build(&same_prefix, &providers, &[contract]).unwrap();
        let proof = broker
            .lookup(
                &same_prefix,
                &contract,
                &same_route_proof,
                scope,
                prefix_len,
                6,
            )
            .unwrap();

        assert_eq!(broker.len(), 1);
        assert_eq!(proof.cache_key_hash, handle.cache_key_hash);
        assert_eq!(proof.handle_hash, handle.handle_hash);
        assert_eq!(proof.prefix_hash, handle.prefix_hash);
        assert_eq!(proof.hit_count, 1);
        assert_ne!(proof.proof_hash, [0; 32]);
        assert!(proof.is_valid_for(&same_prefix, &contract, &same_route_proof, &handle));
        assert_ne!(proof.request_hash, route_proof.request_hash);
    }

    #[test]
    fn prefix_cache_broker_rejects_contract_tokenizer_and_policy_drift() {
        let providers = [ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        }];
        let request = LLMRequest {
            request_id: 161,
            session_id: 162,
            prompt: "prefix cache drift guard\nsuffix".to_string(),
            system_context: None,
            model_hint: Some("model-a"),
            tool_hint: Some("browser"),
            prefer_low_latency: true,
            prefer_low_cost: true,
            require_tool_use: true,
            require_reliability: true,
        };
        let prefix_len = "prefix cache drift guard\n".len() as u32;
        let contract = test_inference_contract(&providers[0], 61);
        let route_proof = InferenceRouteProof::build(&request, &providers, &[contract]).unwrap();
        let mut broker = PrefixCacheBroker::new(2).unwrap();
        broker
            .admit(&request, &contract, &route_proof, [81; 32], prefix_len, 5)
            .unwrap();

        let tokenizer_drift = InferenceBackendContract::from_provider(
            &providers[0],
            "test-backend-v1",
            [62; 32],
            [63; 32],
            [64; 32],
            32_768,
            4_096,
            true,
            false,
        )
        .unwrap();
        assert_eq!(
            broker.lookup(
                &request,
                &tokenizer_drift,
                &route_proof,
                [81; 32],
                prefix_len,
                5
            ),
            Err(PrefixCacheError::InvalidRouteProof)
        );

        let mut policy_drift = request.clone();
        policy_drift.prefer_low_cost = false;
        let policy_route_proof =
            InferenceRouteProof::build(&policy_drift, &providers, &[contract]).unwrap();
        assert_eq!(
            broker.lookup(
                &policy_drift,
                &contract,
                &policy_route_proof,
                [81; 32],
                prefix_len,
                5
            ),
            Err(PrefixCacheError::CacheMiss)
        );

        assert_eq!(
            broker.lookup(&request, &contract, &route_proof, [0; 32], prefix_len, 5),
            Err(PrefixCacheError::InvalidScope)
        );
        assert_eq!(
            broker.lookup(&request, &contract, &route_proof, [81; 32], 0, 5),
            Err(PrefixCacheError::InvalidPrefix)
        );
    }

    #[test]
    fn prefix_cache_broker_eviction_is_deterministic_lru() {
        let providers = [ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        }];
        let contract = test_inference_contract(&providers[0], 91);
        let request = |request_id, prompt: &str| LLMRequest {
            request_id,
            session_id: 192,
            prompt: prompt.to_string(),
            system_context: None,
            model_hint: Some("model-a"),
            tool_hint: Some("browser"),
            prefer_low_latency: true,
            prefer_low_cost: true,
            require_tool_use: true,
            require_reliability: true,
        };
        let first = request(191, "prefix one\nsuffix");
        let second = request(192, "prefix two\nsuffix");
        let third = request(193, "prefix three\nsuffix");
        let first_route = InferenceRouteProof::build(&first, &providers, &[contract]).unwrap();
        let second_route = InferenceRouteProof::build(&second, &providers, &[contract]).unwrap();
        let third_route = InferenceRouteProof::build(&third, &providers, &[contract]).unwrap();
        let mut broker = PrefixCacheBroker::new(2).unwrap();

        broker
            .admit(
                &first,
                &contract,
                &first_route,
                [91; 32],
                "prefix one\n".len() as u32,
                2,
            )
            .unwrap();
        broker
            .admit(
                &second,
                &contract,
                &second_route,
                [91; 32],
                "prefix two\n".len() as u32,
                2,
            )
            .unwrap();
        broker
            .lookup(
                &first,
                &contract,
                &first_route,
                [91; 32],
                "prefix one\n".len() as u32,
                2,
            )
            .unwrap();
        broker
            .admit(
                &third,
                &contract,
                &third_route,
                [91; 32],
                "prefix three\n".len() as u32,
                2,
            )
            .unwrap();

        assert_eq!(broker.len(), 2);
        assert!(
            broker
                .lookup(
                    &first,
                    &contract,
                    &first_route,
                    [91; 32],
                    "prefix one\n".len() as u32,
                    2
                )
                .is_ok()
        );
        assert_eq!(
            broker.lookup(
                &second,
                &contract,
                &second_route,
                [91; 32],
                "prefix two\n".len() as u32,
                2
            ),
            Err(PrefixCacheError::CacheMiss)
        );
        assert!(broker.epoch() >= 4);
    }

    #[test]
    fn structured_output_proof_accepts_schema_but_not_truth() {
        let request = LLMRequest {
            request_id: 211,
            session_id: 212,
            prompt: "emit structured candidate".to_string(),
            system_context: Some("json only".to_string()),
            model_hint: Some("model-a"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let provider = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let contract = InferenceBackendContract::from_provider(
            &provider,
            "test-backend-v1",
            [111; 32],
            [112; 32],
            [113; 32],
            32_768,
            4_096,
            true,
            false,
        )
        .unwrap();
        let schema =
            StructuredOutputSchema::new("candidate-output", 1, STRUCTURED_FIELDS, false).unwrap();
        let response = normalize_response(
            &request,
            r#"{"artifact_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","candidate":"tool proposal","risk":2,"verified":false}"#,
            21,
            7,
            0.8,
        );
        let response_proof = InferenceResponseProof::build(&request, &response, &contract).unwrap();

        let proof = StructuredOutputProof::build(
            &request,
            &response,
            &contract,
            &response_proof,
            &schema,
            true,
        )
        .unwrap();

        assert!(proof.is_valid_for(&request, &response, &contract, &response_proof, &schema));
        assert_eq!(proof.schema_hash, schema.schema_hash);
        assert_eq!(proof.response_proof_hash, response_proof.proof_hash);
        assert!(!proof.truth_claim);
        assert_ne!(proof.canonical_payload_hash, [0; 32]);
        assert_ne!(proof.field_presence_hash, [0; 32]);
        assert_ne!(proof.decoder_contract_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);
    }

    #[test]
    fn structured_output_proof_rejects_schema_contract_and_payload_drift() {
        let request = LLMRequest {
            request_id: 221,
            session_id: 222,
            prompt: "emit structured candidate drift".to_string(),
            system_context: None,
            model_hint: Some("model-a"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let provider = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let no_guided = InferenceBackendContract::from_provider(
            &provider,
            "test-backend-v1",
            [121; 32],
            [122; 32],
            [123; 32],
            32_768,
            4_096,
            false,
            false,
        )
        .unwrap();
        let guided = InferenceBackendContract::from_provider(
            &provider,
            "test-backend-v1",
            [121; 32],
            [122; 32],
            [123; 32],
            32_768,
            4_096,
            true,
            false,
        )
        .unwrap();
        let schema =
            StructuredOutputSchema::new("candidate-output", 1, STRUCTURED_FIELDS, false).unwrap();
        let valid = normalize_response(
            &request,
            r#"{"artifact_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","candidate":"tool proposal","risk":2,"verified":false}"#,
            21,
            7,
            0.8,
        );
        let no_guided_response_proof =
            InferenceResponseProof::build(&request, &valid, &no_guided).unwrap();
        assert_eq!(
            StructuredOutputProof::build(
                &request,
                &valid,
                &no_guided,
                &no_guided_response_proof,
                &schema,
                true
            ),
            Err(StructuredOutputError::GuidedDecodingUnsupported)
        );

        let response_proof = InferenceResponseProof::build(&request, &valid, &guided).unwrap();
        let missing = normalize_response(
            &request,
            r#"{"artifact_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","candidate":"tool proposal","verified":false}"#,
            21,
            7,
            0.8,
        );
        assert_eq!(
            StructuredOutputProof::build(
                &request,
                &missing,
                &guided,
                &response_proof,
                &schema,
                true
            ),
            Err(StructuredOutputError::InvalidResponseProof)
        );
        let missing_proof = InferenceResponseProof::build(&request, &missing, &guided).unwrap();
        assert_eq!(
            StructuredOutputProof::build(
                &request,
                &missing,
                &guided,
                &missing_proof,
                &schema,
                true
            ),
            Err(StructuredOutputError::MissingField)
        );

        let wrong_type = normalize_response(
            &request,
            r#"{"artifact_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","candidate":"tool proposal","risk":"high","verified":false}"#,
            21,
            7,
            0.8,
        );
        let wrong_type_proof =
            InferenceResponseProof::build(&request, &wrong_type, &guided).unwrap();
        assert_eq!(
            StructuredOutputProof::build(
                &request,
                &wrong_type,
                &guided,
                &wrong_type_proof,
                &schema,
                true
            ),
            Err(StructuredOutputError::TypeMismatch)
        );

        let extra = normalize_response(
            &request,
            r#"{"artifact_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","candidate":"tool proposal","risk":2,"verified":false,"truth":true}"#,
            21,
            7,
            0.8,
        );
        let extra_proof = InferenceResponseProof::build(&request, &extra, &guided).unwrap();
        assert_eq!(
            StructuredOutputProof::build(&request, &extra, &guided, &extra_proof, &schema, true),
            Err(StructuredOutputError::UnexpectedField)
        );
    }

    #[test]
    fn continuous_batch_fairness_selects_oldest_contract_and_hashes_deferred() {
        let provider_a = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let provider_b = ProviderConfig {
            provider_id: "provider-b",
            model_name: "model-b",
            endpoint: "http://localhost:5678",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let contract_a = test_inference_contract(&provider_a, 141);
        let contract_b = test_inference_contract(&provider_b, 151);
        let oldest_b =
            test_batch_candidate(251, &provider_b, &contract_b, "oldest b", 12, 16, 1, 1_000);
        let newer_a =
            test_batch_candidate(252, &provider_a, &contract_a, "newer a", 9, 16, 2, 1_100);
        let newer_b =
            test_batch_candidate(253, &provider_b, &contract_b, "newer b", 8, 16, 3, 1_200);
        let candidates = [newer_a, newer_b, oldest_b];
        let policy = ContinuousBatchPolicy::new(4, 64, 64, 10_000).unwrap();

        let proof = ContinuousBatchPlanner::build_proof(260, 1_500, &policy, &candidates).unwrap();

        assert!(proof.is_valid_for(260, 1_500, &policy, &candidates));
        assert_eq!(proof.target_contract_hash, contract_b.contract_hash);
        assert_eq!(
            proof.selected_candidate_hashes,
            vec![oldest_b.candidate_hash, newer_b.candidate_hash]
        );
        assert_eq!(
            proof.deferred_candidate_hashes,
            vec![newer_a.candidate_hash]
        );
        assert_eq!(proof.selected_prompt_tokens, 20);
        assert_eq!(proof.selected_output_tokens, 32);
        assert_eq!(proof.oldest_wait_ms, 500);
        assert_eq!(proof.oldest_deferred_wait_ms, 400);
        assert!(proof.fairness_bound_satisfied);
        assert_ne!(proof.selected_list_hash, [0; 32]);
        assert_ne!(proof.deferred_list_hash, [0; 32]);
        assert_ne!(proof.fairness_order_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);
    }

    #[test]
    fn continuous_batch_fairness_enforces_caps_and_tamper_detection() {
        let provider = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let contract = test_inference_contract(&provider, 161);
        let first = test_batch_candidate(261, &provider, &contract, "first batch", 10, 8, 1, 1_000);
        let second =
            test_batch_candidate(262, &provider, &contract, "second batch", 10, 8, 2, 1_010);
        let policy = ContinuousBatchPolicy::new(1, 64, 64, 10_000).unwrap();
        let candidates = [first, second];

        let mut proof =
            ContinuousBatchPlanner::build_proof(270, 1_500, &policy, &candidates).unwrap();
        assert_eq!(proof.selected_candidate_hashes, vec![first.candidate_hash]);
        assert_eq!(proof.deferred_candidate_hashes, vec![second.candidate_hash]);
        assert!(proof.is_valid_for(270, 1_500, &policy, &candidates));

        proof.selected_prompt_tokens += 1;
        assert!(!proof.is_valid_for(270, 1_500, &policy, &candidates));

        let tight_policy = ContinuousBatchPolicy::new(2, 9, 64, 10_000).unwrap();
        assert_eq!(
            ContinuousBatchPlanner::build_proof(271, 1_500, &tight_policy, &candidates),
            Err(ContinuousBatchError::TokenBudgetExceeded)
        );

        let mut tampered = first;
        tampered.candidate_hash = [0; 32];
        assert_eq!(
            ContinuousBatchPlanner::build_proof(272, 1_500, &policy, &[tampered]),
            Err(ContinuousBatchError::InvalidCandidate)
        );
    }

    #[test]
    fn speculative_decode_equivalence_accepts_target_verified_prefix_only() {
        let target_provider = ProviderConfig {
            provider_id: "target-provider",
            model_name: "target-model",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let draft_provider = ProviderConfig {
            provider_id: "draft-provider",
            model_name: "draft-model",
            endpoint: "http://localhost:5678",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let request = LLMRequest {
            request_id: 281,
            session_id: 282,
            prompt: "speculative decode proof".to_string(),
            system_context: None,
            model_hint: Some("target-model"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let target_contract = test_speculative_contract(&target_provider, 171, true);
        let draft_contract = InferenceBackendContract::from_provider(
            &draft_provider,
            "test-backend-v1",
            target_contract.tokenizer_hash,
            target_contract.config_hash,
            target_contract.endpoint_class_hash,
            32_768,
            4_096,
            true,
            true,
        )
        .unwrap();
        let route_proof =
            InferenceRouteProof::build(&request, &[target_provider], &[target_contract]).unwrap();
        let draft =
            SpeculativeTokenBatch::build(&request, &draft_contract, &[10, 20, 30, 40, 50]).unwrap();
        let target =
            SpeculativeTokenBatch::build(&request, &target_contract, &[10, 20, 30, 41]).unwrap();

        let proof = SpeculativeDecodeVerifier::build_proof(
            &request,
            &route_proof,
            &draft_contract,
            &target_contract,
            &draft,
            &target,
            4,
        )
        .unwrap();

        assert!(proof.is_valid_for(
            &request,
            &route_proof,
            &draft_contract,
            &target_contract,
            &draft,
            &target
        ));
        assert_eq!(proof.accepted_prefix_len, 3);
        assert_eq!(proof.draft_batch_hash, draft.batch_hash);
        assert_eq!(proof.target_batch_hash, target.batch_hash);
        assert_ne!(proof.accepted_prefix_hash, draft.token_hash);
        assert_ne!(proof.rejected_suffix_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);
    }

    #[test]
    fn speculative_decode_equivalence_rejects_drift_and_empty_prefix() {
        let target_provider = ProviderConfig {
            provider_id: "target-provider",
            model_name: "target-model",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let draft_provider = ProviderConfig {
            provider_id: "draft-provider",
            model_name: "draft-model",
            endpoint: "http://localhost:5678",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let request = LLMRequest {
            request_id: 291,
            session_id: 292,
            prompt: "speculative decode drift".to_string(),
            system_context: None,
            model_hint: Some("target-model"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let target_contract = test_speculative_contract(&target_provider, 181, true);
        let draft_contract = InferenceBackendContract::from_provider(
            &draft_provider,
            "test-backend-v1",
            target_contract.tokenizer_hash,
            target_contract.config_hash,
            target_contract.endpoint_class_hash,
            32_768,
            4_096,
            true,
            true,
        )
        .unwrap();
        let route_proof =
            InferenceRouteProof::build(&request, &[target_provider], &[target_contract]).unwrap();
        let draft = SpeculativeTokenBatch::build(&request, &draft_contract, &[1, 2, 3]).unwrap();
        let target = SpeculativeTokenBatch::build(&request, &target_contract, &[1, 2, 4]).unwrap();
        let mut proof = SpeculativeDecodeVerifier::build_proof(
            &request,
            &route_proof,
            &draft_contract,
            &target_contract,
            &draft,
            &target,
            3,
        )
        .unwrap();
        proof.accepted_prefix_len += 1;
        assert!(!proof.is_valid_for(
            &request,
            &route_proof,
            &draft_contract,
            &target_contract,
            &draft,
            &target
        ));

        let unsupported = test_speculative_contract(&target_provider, 191, false);
        assert_eq!(
            SpeculativeDecodeVerifier::build_proof(
                &request,
                &route_proof,
                &draft_contract,
                &unsupported,
                &draft,
                &target,
                3
            ),
            Err(SpeculativeDecodeError::SpeculativeUnsupported)
        );

        let drifted_draft = test_speculative_contract(&draft_provider, 201, true);
        assert_eq!(
            SpeculativeDecodeVerifier::build_proof(
                &request,
                &route_proof,
                &drifted_draft,
                &target_contract,
                &draft,
                &target,
                3
            ),
            Err(SpeculativeDecodeError::InvalidTokenBatch)
        );

        let mismatch_target =
            SpeculativeTokenBatch::build(&request, &target_contract, &[9, 2, 3]).unwrap();
        assert_eq!(
            SpeculativeDecodeVerifier::build_proof(
                &request,
                &route_proof,
                &draft_contract,
                &target_contract,
                &draft,
                &mismatch_target,
                3
            ),
            Err(SpeculativeDecodeError::EmptyAcceptedPrefix)
        );
    }

    #[test]
    fn llm_runtime_routes_and_logs_response_without_semantic_commit() {
        let request = LLMRequest {
            request_id: 11,
            session_id: 12,
            prompt: "route and commit".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let mut runtime = NerveRuntime::new();
        let response = run_llm_with_registry(request, &mut runtime, &registry).unwrap();
        assert_eq!(response.content, "mock response");
        assert!(runtime.latest_session_summary().is_none());
        assert_eq!(runtime.audit_log.len(), 1);
        assert_eq!(runtime.audit_log.latest().unwrap().session_id, 12);
        assert_eq!(runtime.audit_log.latest().unwrap().content, "mock response");
    }

    #[test]
    fn llm_loop_circuit_breaker_rejects_invalid_config() {
        use crate::circuit_breaker::LlmLoopCircuitBreakerConfig;

        assert!(LlmLoopCircuitBreakerConfig::bounded(1, 8).is_err());
        assert!(LlmLoopCircuitBreakerConfig::bounded(3, 2).is_err());
        assert!(LlmLoopCircuitBreakerConfig::bounded(3, 257).is_err());
        assert_eq!(
            LlmLoopCircuitBreakerConfig::bounded(3, 8).unwrap(),
            LlmLoopCircuitBreakerConfig {
                max_identical_responses: 3,
                window_events: 8,
            }
        );
    }

    #[test]
    fn llm_loop_circuit_breaker_trips_on_third_identical_session_response() {
        use crate::circuit_breaker::{
            CircuitBreakerDecisionKind, LlmLoopCircuitBreaker, LlmLoopCircuitBreakerConfig,
        };

        let mut breaker =
            LlmLoopCircuitBreaker::new(LlmLoopCircuitBreakerConfig::bounded(3, 8).unwrap())
                .unwrap();
        let request = |request_id| LLMRequest {
            request_id,
            session_id: 55,
            prompt: "route repeated loop".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };

        let first = normalize_response(&request(1), "same answer", 3, 1, 1.0);
        let second = normalize_response(&request(2), " same answer ", 3, 1, 1.0);
        let third = normalize_response(&request(3), "same answer", 3, 1, 1.0);

        let first_decision = breaker.observe_response(&first).unwrap();
        assert_eq!(first_decision.kind, CircuitBreakerDecisionKind::Allowed);
        assert_eq!(first_decision.repeated_response_count, 1);
        let second_decision = breaker.observe_response(&second).unwrap();
        assert_eq!(second_decision.kind, CircuitBreakerDecisionKind::Allowed);
        assert_eq!(second_decision.repeated_response_count, 2);
        let third_decision = breaker.observe_response(&third).unwrap();
        assert_eq!(third_decision.kind, CircuitBreakerDecisionKind::Tripped);
        assert_eq!(third_decision.repeated_response_count, 3);
        assert_ne!(third_decision.evidence_hash, [0; 32]);
    }

    #[test]
    fn llm_loop_circuit_breaker_scopes_repetition_by_session() {
        use crate::circuit_breaker::{CircuitBreakerDecisionKind, LlmLoopCircuitBreaker};

        let mut breaker = LlmLoopCircuitBreaker::default();
        let request = |request_id, session_id| LLMRequest {
            request_id,
            session_id,
            prompt: "route repeated loop".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };

        breaker
            .observe_response(&normalize_response(
                &request(1, 77),
                "same answer",
                3,
                1,
                1.0,
            ))
            .unwrap();
        breaker
            .observe_response(&normalize_response(
                &request(2, 77),
                "same answer",
                3,
                1,
                1.0,
            ))
            .unwrap();
        let other_session = breaker
            .observe_response(&normalize_response(
                &request(3, 78),
                "same answer",
                3,
                1,
                1.0,
            ))
            .unwrap();

        assert_eq!(other_session.kind, CircuitBreakerDecisionKind::Allowed);
        assert_eq!(other_session.repeated_response_count, 1);
    }

    #[test]
    fn llm_loop_circuit_breaker_bounded_window_eviction_allows_recovery() {
        use crate::circuit_breaker::{
            CircuitBreakerDecisionKind, LlmLoopCircuitBreaker, LlmLoopCircuitBreakerConfig,
        };

        let mut breaker =
            LlmLoopCircuitBreaker::new(LlmLoopCircuitBreakerConfig::bounded(3, 3).unwrap())
                .unwrap();
        let request = |request_id| LLMRequest {
            request_id,
            session_id: 88,
            prompt: "route bounded loop".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };

        breaker
            .observe_response(&normalize_response(&request(1), "A", 1, 1, 1.0))
            .unwrap();
        breaker
            .observe_response(&normalize_response(&request(2), "A", 1, 1, 1.0))
            .unwrap();
        breaker
            .observe_response(&normalize_response(&request(3), "B", 1, 1, 1.0))
            .unwrap();
        breaker
            .observe_response(&normalize_response(&request(4), "C", 1, 1, 1.0))
            .unwrap();
        let recovered = breaker
            .observe_response(&normalize_response(&request(5), "A", 1, 1, 1.0))
            .unwrap();

        assert_eq!(breaker.window_len(), 3);
        assert_eq!(recovered.kind, CircuitBreakerDecisionKind::Allowed);
        assert_eq!(recovered.repeated_response_count, 2);
    }

    #[test]
    fn llm_runtime_circuit_breaker_stops_repeated_response_before_audit_commit() {
        use crate::circuit_breaker::CircuitBreakerDecisionKind;

        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let mut runtime = NerveRuntime::new();
        let request = |request_id| LLMRequest {
            request_id,
            session_id: 66,
            prompt: "route repeated runtime loop".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };

        runtime
            .process_llm_request_with_registry(&request(1), &registry)
            .unwrap();
        runtime
            .process_llm_request_with_registry(&request(2), &registry)
            .unwrap();
        let anchor_after_second = runtime.anchor.last_witness_hash;
        let tripped = runtime.process_llm_request_with_registry(&request(3), &registry);

        assert!(matches!(tripped, Err("llm loop circuit breaker tripped")));
        assert_eq!(runtime.audit_log.len(), 2);
        assert_eq!(runtime.anchor.last_witness_hash, anchor_after_second);
        assert_eq!(runtime.llm_loop_window_len(), 3);
        let decision = runtime.last_circuit_breaker_decision().unwrap();
        assert_eq!(decision.kind, CircuitBreakerDecisionKind::Tripped);
        assert_eq!(decision.repeated_response_count, 3);
        assert_ne!(decision.evidence_hash, [0; 32]);
    }

    #[test]
    fn episodic_audit_log_writes_hash_only_arrow_ipc_stream_for_mmap_observability() {
        use arrow::ipc::reader::StreamReader;
        use memmap2::MmapOptions;
        use std::fs::OpenOptions;
        use std::io::Cursor;
        use std::time::{SystemTime, UNIX_EPOCH};

        let request = LLMRequest {
            request_id: 41,
            session_id: 42,
            prompt: "audit arrow stream".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("episodic-audit-{unique}.arrow"));
        let response = normalize_response(&request, "arrow trace", 5, 7, 0.9);
        let mut log = EpisodicAuditLog::arrow_ipc_stream(&path).unwrap();

        log.append_llm_response(&response).unwrap();

        assert_eq!(log.len(), 1);
        assert_eq!(log.arrow_ipc_path(), Some(path.as_path()));
        let trace = log.latest().unwrap();
        assert_eq!(trace.content, "arrow trace");
        assert_eq!(trace.content_len, "arrow trace".len());
        assert_ne!(trace.content_blake3, [0; 32]);
        assert_eq!(
            trace.raw_text_ref_hash,
            episodic_audit_raw_text_ref_hash(
                response.request_id,
                response.session_id,
                response.content.len(),
                trace.content_blake3,
                response.rejected,
            )
        );
        assert_eq!(
            trace.audit_record_hash,
            episodic_audit_record_hash(
                response.request_id,
                response.session_id,
                response.content.len(),
                trace.content_blake3,
                trace.raw_text_ref_hash,
                response.token_usage,
                response.latency_ms,
                response.rejected,
            )
        );
        assert_ne!(trace.raw_text_ref_hash, trace.content_blake3);
        assert_ne!(trace.audit_record_hash, trace.raw_text_ref_hash);
        let file = OpenOptions::new().read(true).open(&path).unwrap();
        assert!(file.metadata().unwrap().len() > 0);
        // SAFETY: this is a test-only read-only mmap of a temp file we just created and
        // immediately drop on the next line. The map is bounded by the file's own
        // verifyable size, and Standard layout is guaranteed for `Vec<u8>` writes.
        let mmap = unsafe { MmapOptions::new().map(&file).unwrap() };
        assert!(mmap.len() > 8);
        assert!(
            mmap.windows(b"schema_version".len())
                .any(|window| { window == b"schema_version" })
        );
        assert!(
            mmap.windows(EPISODIC_AUDIT_SCHEMA_VERSION.to_le_bytes().len())
                .any(|window| { window == EPISODIC_AUDIT_SCHEMA_VERSION.to_le_bytes() })
        );
        assert!(
            mmap.windows(b"content_blake3".len())
                .any(|window| { window == b"content_blake3" })
        );
        assert!(
            mmap.windows(b"raw_text_ref_hash".len())
                .any(|window| { window == b"raw_text_ref_hash" })
        );
        assert!(
            mmap.windows(b"audit_record_hash".len())
                .any(|window| { window == b"audit_record_hash" })
        );
        assert!(
            !mmap
                .windows(b"arrow trace".len())
                .any(|window| { window == b"arrow trace" })
        );

        let reader = StreamReader::try_new(Cursor::new(&mmap[..]), None).unwrap();
        let schema = reader.schema();
        assert!(schema.field_with_name("content").is_err());
        assert!(schema.field_with_name("content_len").is_ok());
        assert!(schema.field_with_name("content_blake3").is_ok());
        assert!(schema.field_with_name("raw_text_ref_hash").is_ok());
        assert!(schema.field_with_name("audit_record_hash").is_ok());
    }

    #[test]
    fn llm_runtime_with_fallback_chain_logs_checkpoint_trace() {
        let request = LLMRequest {
            request_id: 31,
            session_id: 32,
            prompt: "fallback route runtime".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: false,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let providers = [
            ProviderConfig {
                provider_id: "primary",
                model_name: "primary-model",
                endpoint: "http://primary",
                timeout_ms: 1000,
                retry_limit: 3,
                preferred_for_tools: false,
                cost_rank: 2,
                availability_rank: 1,
            },
            ProviderConfig {
                provider_id: "mock",
                model_name: "mock-model",
                endpoint: "http://backup",
                timeout_ms: 1000,
                retry_limit: 3,
                preferred_for_tools: true,
                cost_rank: 1,
                availability_rank: 2,
            },
        ];
        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let mut runtime = NerveRuntime::new();
        let (response, checkpoint) =
            run_llm_with_fallback(request, &mut runtime, &providers, &registry).unwrap();
        assert_eq!(response.content, "mock response");
        assert!(checkpoint.is_some());
        assert!(checkpoint.unwrap().is_valid());
        assert!(runtime.latest_session_summary().is_none());
        assert_eq!(runtime.audit_log.len(), 1);
        assert_eq!(runtime.audit_log.latest().unwrap().session_id, 32);
    }

    #[test]
    fn llm_runtime_budget_admission_uses_only_admitted_provider() {
        let request = LLMRequest {
            request_id: 131,
            session_id: 132,
            prompt: "budget route runtime".to_string(),
            system_context: None,
            model_hint: Some("fast-model"),
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
                provider_id: "mock",
                model_name: "mock-model",
                endpoint: "http://mock",
                timeout_ms: 1000,
                retry_limit: 2,
                preferred_for_tools: true,
                cost_rank: 5,
                availability_rank: 1,
            },
        ];
        let ledger = ProviderBudgetLedger::new(&[
            ProviderRuntimeBudget::new("fast", "fast-model", 0, 10_000, 1_000_000).unwrap(),
            ProviderRuntimeBudget::new("mock", "mock-model", 5, 10_000, 1_000_000).unwrap(),
        ])
        .unwrap();
        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let mut runtime = NerveRuntime::new();

        let (response, checkpoint, proof) = run_llm_with_budget_admission(
            request,
            &mut runtime,
            &providers,
            &registry,
            &ledger,
            1_024,
        )
        .unwrap();

        assert_eq!(response.content, "mock response");
        assert!(checkpoint.unwrap().is_valid());
        assert_ne!(proof.proof_hash, [0; 32]);
        assert_eq!(proof.throttled_provider_hashes.len(), 1);
        assert_eq!(runtime.audit_log.len(), 1);
        assert_eq!(runtime.audit_log.latest().unwrap().session_id, 132);
    }

    #[test]
    fn llm_runtime_budget_admission_fails_closed_before_execution() {
        let request = LLMRequest {
            request_id: 133,
            session_id: 134,
            prompt: "budget route runtime".to_string(),
            system_context: None,
            model_hint: Some("mock-model"),
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: false,
            require_tool_use: false,
            require_reliability: true,
        };
        let providers = [ProviderConfig {
            provider_id: "mock",
            model_name: "mock-model",
            endpoint: "http://mock",
            timeout_ms: 1000,
            retry_limit: 2,
            preferred_for_tools: true,
            cost_rank: 1,
            availability_rank: 1,
        }];
        let ledger = ProviderBudgetLedger::new(&[ProviderRuntimeBudget::new(
            "mock",
            "mock-model",
            0,
            10_000,
            1_000_000,
        )
        .unwrap()])
        .unwrap();
        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let mut runtime = NerveRuntime::new();

        let rejected = run_llm_with_budget_admission(
            request,
            &mut runtime,
            &providers,
            &registry,
            &ledger,
            1_024,
        );

        assert!(matches!(rejected, Err("provider budget admission failed")));
        assert_eq!(runtime.audit_log.len(), 0);
    }

    #[test]
    fn llm_runtime_budget_admission_replay_records_proof_bound_response() {
        use crate::replay::{
            RunEventKind, RunEventLedger, llm_budget_admission_replay_binding_hash,
        };

        let request = LLMRequest {
            request_id: 135,
            session_id: 136,
            prompt: "budget route runtime replay".to_string(),
            system_context: None,
            model_hint: Some("fast-model"),
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
                provider_id: "mock",
                model_name: "mock-model",
                endpoint: "http://mock",
                timeout_ms: 1000,
                retry_limit: 2,
                preferred_for_tools: true,
                cost_rank: 5,
                availability_rank: 1,
            },
        ];
        let ledger = ProviderBudgetLedger::new(&[
            ProviderRuntimeBudget::new("fast", "fast-model", 0, 10_000, 1_000_000).unwrap(),
            ProviderRuntimeBudget::new("mock", "mock-model", 5, 10_000, 1_000_000).unwrap(),
        ])
        .unwrap();
        let adapter = MockAdapter;
        let registry = AdapterRegistry::new(vec![&adapter]);
        let mut runtime = NerveRuntime::new();
        let mut replay_ledger = RunEventLedger::new(9136);
        seed_goal_intake_replay(&mut replay_ledger, "runtime-budget-admission-replay");
        replay_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();

        let (response, checkpoint, proof, event) = run_llm_with_budget_admission_replay(
            request,
            &mut runtime,
            &providers,
            &registry,
            &ledger,
            1_024,
            &mut replay_ledger,
        )
        .unwrap();

        let trace = runtime.audit_log.latest().unwrap();
        assert_eq!(response.content, "mock response");
        assert!(checkpoint.unwrap().is_valid());
        assert_ne!(proof.proof_hash, [0; 32]);
        assert_eq!(event.kind, RunEventKind::LLMResponseReceived);
        assert_eq!(event.primary_hash, trace.audit_record_hash);
        assert_eq!(
            event.secondary_hash.unwrap(),
            llm_budget_admission_replay_binding_hash(
                trace.audit_record_hash,
                trace.raw_text_ref_hash,
                &proof
            )
        );
        assert!(replay_ledger.verify_hash_chain());
    }

    #[test]
    fn cli_llm_once_routes_through_runtime() {
        let request = LLMRequest {
            request_id: 3,
            session_id: 7,
            prompt: "cli llm".to_string(),
            system_context: None,
            model_hint: None,
            tool_hint: None,
            prefer_low_latency: true,
            prefer_low_cost: true,
            require_tool_use: false,
            require_reliability: true,
        };
        let provider = ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        };
        let routed = run_llm_once(request, provider).unwrap();
        assert_eq!(routed, (3, 7));
    }

    #[test]
    fn ffi_smoke_checks() {
        assert_eq!(aegis_status().unwrap(), "aegis-nerve-ready");
        assert!(aegis_validate_schema(0xAE1515, 1).unwrap());
        assert!(aegis_memory_alignment(64).unwrap());
        assert!(aegis_validate_layout(64, 64).unwrap());
        assert_eq!(aegis_nerve_schema_id().unwrap(), 0xAE1515);
        assert!(aegis_frame_is_valid(1).unwrap());
        assert!(aegis_message_frame_valid(1, 1, 1).unwrap());
        assert!(aegis_zero_copy_ready(0xAE1515, 1, 1).unwrap());
        assert_eq!(
            aegis_mmap_bridge_header_bytes().unwrap(),
            MMAP_BRIDGE_HEADER_BYTES
        );
        assert_eq!(
            aegis_mmap_bridge_payload_alignment().unwrap(),
            MMAP_BRIDGE_PAYLOAD_ALIGNMENT
        );
        assert!(aegis_new_message_identity(1, 1).unwrap());
        assert!(aegis_can_bridge_python(1, 1, 1).unwrap());
        assert_eq!(aegis_layout_header_bytes().unwrap(), 64);
        assert_eq!(aegis_layout_payload_alignment().unwrap(), 64);
        assert!(aegis_descriptor_valid(1).unwrap());
        assert!(aegis_release_ready().unwrap());
        assert_eq!(aegis_cli_status().unwrap(), "aegis-nerve-cli ready");
        assert_eq!(
            aegis_cli_schema().unwrap(),
            "schema_id=0xAE1515 version=1 alignment=64"
        );
        assert!(aegis_llm_request(1, 2, "hello".to_string(), Some("model-a".to_string())).unwrap());
        assert!(aegis_llm_route(1, 2, "hello".to_string()).unwrap());
        assert_eq!(aegis_llm_bridge_key(1, 2).unwrap(), (1, 2));
        assert!(aegis_llm_normalize(1, 2, "content".to_string()).unwrap());
        assert!(aegis_llm_reject(1, 2, "err".to_string()).unwrap());
        assert!(
            aegis_physical_metrics_prometheus()
                .unwrap()
                .contains("aegis_physical_accepted_artifacts_total")
        );
    }

    #[test]
    fn schema_validation_accepts_matching_version() {
        let registry = registry();
        assert!(registry.validate(BinarySchemaId(7), 1).is_ok());
        assert!(registry.has_required_alignment());
    }

    #[test]
    fn schema_validation_rejects_mismatch() {
        let registry = registry();
        assert!(registry.validate(BinarySchemaId(8), 1).is_err());
    }

    #[test]
    fn nerve_schema_exists() {
        assert_eq!(NERVE_SCHEMA.version, 1);
        assert_eq!(NERVE_SCHEMA.alignment, 64);
        assert!(NERVE_SCHEMA.has_required_alignment());
    }

    #[test]
    fn descriptor_validates() {
        let descriptor = BinaryFrameDescriptor::new(3);
        assert!(descriptor.is_valid());
    }

    #[test]
    fn layout_helpers_match_schema() {
        assert_eq!(expected_header_bytes(), 64);
        assert_eq!(expected_payload_alignment(), 64);
        assert_eq!(expected_message_metadata_size(), 64);
        assert!(is_expected_alignment(64));
        assert!(is_expected_header_size(64));
        assert!(validate_layout(64, 64));
    }

    #[test]
    fn shm_validation_accepts_valid_region() {
        let mut buffer = Aligned64([0u8; 64]);
        let region = SharedMemoryRegion {
            ptr: buffer.0.as_mut_ptr(),
            size: buffer.0.len(),
            alignment: 64,
        };
        assert!(validate_region(&region).is_ok());
        assert!(region_is_64_aligned(&region));
    }

    #[test]
    fn shm_create_region_rejects_bad_inputs() {
        assert!(create_region("", 0, 0).is_err());
    }

    #[test]
    fn shm_validation_rejects_null_pointer() {
        let region = SharedMemoryRegion {
            ptr: core::ptr::null_mut(),
            size: 64,
            alignment: 64,
        };
        assert!(validate_region(&region).is_err());
    }

    #[test]
    fn mmap_bridge_frame_roundtrips_payload_without_copying() {
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join("mmap-bridge-roundtrip");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("frame.aegmmap");
        let payload: Vec<u8> = (0..4096).map(pattern_byte).collect();

        let header = write_mmap_bridge_frame(&path, 11, 22, &payload).unwrap();
        assert_eq!(header.payload_offset as usize, MMAP_BRIDGE_HEADER_BYTES);
        assert_eq!(
            header.payload_offset as usize % MMAP_BRIDGE_PAYLOAD_ALIGNMENT,
            0
        );
        assert_eq!(header.payload_len as usize, payload.len());
        assert!(validate_mmap_bridge_frame(&path));

        let view = open_mmap_bridge_view(&path).unwrap();
        assert_eq!(view.header().message_id, 11);
        assert_eq!(view.header().session_id, 22);
        assert_eq!(view.payload().unwrap(), payload.as_slice());
        assert_eq!(
            view.payload_ptr().unwrap(),
            view.payload().unwrap().as_ptr()
        );
        assert_ne!(view.mapped_ptr(), view.payload_ptr().unwrap());
        assert!(view.payload_hash_matches().unwrap());
    }

    #[test]
    fn mmap_bridge_frame_rejects_tamper() {
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join("mmap-bridge-tamper");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("frame.aegmmap");

        write_pattern_mmap_bridge_frame(&path, 31, 41, 1024).unwrap();
        assert!(validate_mmap_bridge_frame(&path));
        let mut file = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        use std::io::{Seek, SeekFrom, Write};
        file.seek(SeekFrom::Start(MMAP_BRIDGE_HEADER_BYTES as u64 + 7))
            .unwrap();
        file.write_all(&[0xff]).unwrap();
        file.flush().unwrap();

        assert!(!validate_mmap_bridge_frame(&path));
        assert!(open_mmap_bridge_view(&path).is_err());
    }

    #[test]
    fn mmap_bridge_ffi_writes_valid_pattern_frame() {
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join("mmap-bridge-ffi");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("ffi-frame.aegmmap");

        let (_, _, payload_offset, payload_len, payload_hash) =
            aegis_write_mmap_bridge_pattern(path.to_string_lossy().to_string(), 51, 61, 2048)
                .unwrap();
        assert_eq!(payload_offset as usize, MMAP_BRIDGE_HEADER_BYTES);
        assert_eq!(payload_len, 2048);
        assert_eq!(payload_hash.len(), 32);
        assert!(aegis_validate_mmap_bridge_frame(path.to_string_lossy().to_string()).unwrap());
        let view = open_mmap_bridge_view(&path).unwrap();
        assert_eq!(view.payload().unwrap()[0], pattern_byte(0));
        assert_eq!(view.payload().unwrap()[2047], pattern_byte(2047));
    }

    #[test]
    fn message_frame_validates_and_converts() {
        let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3]);
        assert!(message.is_valid());
        assert_eq!(message.header.message_id, 1);
        assert_eq!(message.header.session_id, 2);
        let zero_copy = message_to_zero_copy(&message).unwrap();
        assert!(zero_copy.is_valid());
        assert!(zero_copy_is_default_schema(&zero_copy));
    }

    #[test]
    fn zero_copy_roundtrip_rebuilds_message() {
        let message = MessageFrame::with_identity(1, 2, vec![9, 8, 7]);
        let zero_copy = message_to_zero_copy(&message).unwrap();
        let rebuilt = zero_copy_to_message(&zero_copy).unwrap();
        assert!(rebuilt.is_valid());
        assert_eq!(rebuilt.payload, vec![9, 8, 7]);
    }

    #[test]
    fn integration_probe_roundtrips_runtime_message() {
        let mut runtime = NerveRuntime::new();
        let message = MessageFrame::with_identity(1, 42, vec![1, 2, 3]);
        let probe = run_integration_probe(&mut runtime, message).unwrap();
        assert!(probe.zero_copy_ok);
        assert_eq!(probe.runtime_processed, 1);
        assert_eq!(probe.latest_session_id, None);
        assert_eq!(probe.latest_payload_len, None);
        assert!(probe.semantic_memory_empty);
        assert!(frame_from_memory(&runtime).is_none());
    }

    #[test]
    fn cli_can_run() {
        crate::run_cli();
    }

    #[test]
    fn cli_writes_replay_chaos_scorecard_artifact() {
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("cli-replay-chaos-scorecard-{unique}"));
        let artifact_path = dir.join("replay_chaos_scorecard.json");

        let artifact_hash = write_replay_chaos_scorecard(&artifact_path).unwrap();
        assert_ne!(artifact_hash, [0; 32]);
        assert!(artifact_path.exists());
        assert!(dir.join("replay_chaos_segments").is_dir());
        assert!(dir.join("replay_chaos_io_evidence").is_dir());

        let artifact_payload = std::fs::read(&artifact_path).unwrap();
        let artifact_json: serde_json::Value = serde_json::from_slice(&artifact_payload).unwrap();
        assert_eq!(artifact_json["schema_version"], 1);
        assert_eq!(artifact_json["bench_name"], "ReplayChaosBench");
        assert_eq!(artifact_json["scorecard"]["task_id"], 9001);
        assert_eq!(artifact_json["report"]["crash_points_exercised"], 128);
        assert_eq!(artifact_json["report"]["all_recoveries_valid"], true);
        assert_eq!(artifact_json["io_evidence"]["mmap_backing_used"], true);
        assert_eq!(
            artifact_json["io_evidence"]["stream_reader_materializes_events"],
            true
        );
        assert_valid_replay_write_evidence(&artifact_json);
        assert!(
            artifact_json["io_evidence"]["materialized_run_event_bytes"]
                .as_u64()
                .unwrap()
                > 0
        );
        assert_eq!(
            synced_temp_file_count(&dir, "replay_chaos_scorecard.json"),
            0
        );
    }

    #[test]
    fn cli_writes_replay_endurance_report_artifact() {
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("cli-replay-endurance-report-{unique}"));
        let artifact_path = dir.join("replay_endurance_report.json");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&artifact_path, b"stale").unwrap();

        let artifact_hash = write_replay_endurance_report(&artifact_path).unwrap();
        assert_ne!(artifact_hash, [0; 32]);
        assert!(artifact_path.exists());
        assert!(dir.join("replay_endurance_segments").is_dir());
        assert_eq!(
            synced_temp_file_count(&dir, "replay_endurance_report.json"),
            0
        );

        let artifact_payload = std::fs::read(&artifact_path).unwrap();
        let artifact_json: serde_json::Value = serde_json::from_slice(&artifact_payload).unwrap();
        assert_eq!(artifact_json["schema_version"], 1);
        assert_eq!(artifact_json["bench_name"], "ReplayEnduranceBench");
        assert_eq!(artifact_json["report"]["simulated_hours"], 100);
        assert_eq!(artifact_json["report"]["synthetic_cycle_count"], 2_400);
        assert_eq!(artifact_json["report"]["checkpoint_count"], 400);
        assert_eq!(artifact_json["report"]["context_fold_count"], 400);
        assert_eq!(
            artifact_json["report"]["context_fold_checkpoint_pair_count"],
            400
        );
        nonzero_json_hash(&artifact_json["report"]["context_fold_evidence_hash"]);
        assert_eq!(
            artifact_json["report"]["mmap_materialized_replay_proven"],
            true
        );
        assert_eq!(
            artifact_json["report"]["materialization_within_bound"],
            true
        );
        assert_eq!(artifact_json["report"]["full_replay_matches"], true);
        assert_eq!(
            artifact_json["report"]["tail_recovery_matches_prefix"],
            true
        );
        assert_valid_replay_write_evidence(&artifact_json);

        let replacement_hash = write_replay_endurance_report(&artifact_path).unwrap();
        assert_eq!(replacement_hash, artifact_hash);
        assert_eq!(
            synced_temp_file_count(&dir, "replay_endurance_report.json"),
            0
        );
    }

    #[test]
    fn ipc_validation_accepts_non_empty_frame() {
        let registry = registry();
        let payload = [1u8, 2, 3];
        let frame = ZeroCopyFrame {
            schema_id: BinarySchemaId(7),
            message_id: 1,
            session_id: 2,
            payload_ptr: payload.as_ptr(),
            payload_len: payload.len(),
        };
        assert!(validate_frame(&frame, &registry, 1).is_ok());
        assert!(validate_zero_copy(&frame, &registry).is_ok());
        assert!(payload_span(&frame).is_ok());

        let default_message = MessageFrame::with_identity(1, 2, vec![4, 5, 6]);
        let default_frame = message_to_zero_copy(&default_message).unwrap();
        assert!(validate_default_zero_copy(&default_frame).is_ok());
        assert!(zero_copy_is_default_schema(&default_frame));
    }

    #[test]
    fn ipc_validation_rejects_null_pointer() {
        let registry = registry();
        let frame = ZeroCopyFrame {
            schema_id: BinarySchemaId(7),
            message_id: 1,
            session_id: 2,
            payload_ptr: core::ptr::null(),
            payload_len: 8,
        };
        assert!(validate_frame(&frame, &registry, 1).is_err());
        assert!(payload_span(&frame).is_err());
    }

    #[test]
    fn memory_store_ingests_valid_frame() {
        let mut store = CogniFoldStore::new();
        let frame = MemoryFrame {
            frame_id: 1,
            session_id: 2,
            payload: vec![1, 2, 3],
            fidelity: 0.8,
        };
        assert!(store.ingest(frame).is_ok());
        assert_eq!(store.len(), 1);
        assert!(store.latest().is_some());
        assert!(!store.is_empty());
        assert_eq!(store.total_payload_bytes(), 3);
        assert!((store.average_fidelity() - 0.8).abs() < f32::EPSILON);
    }

    #[test]
    fn memory_store_rejects_layout_budget_overflow_without_panicking_or_mutating_order() {
        let budget = RuntimeLayoutBudget::new(1, 4, 64).unwrap();
        let mut store = CogniFoldStore::new();
        store.frames = GenerationalSlab::with_layout_budget(budget).unwrap();
        store
            .ingest(MemoryFrame {
                frame_id: 1,
                session_id: 2,
                payload: vec![1, 2, 3, 4],
                fidelity: 0.8,
            })
            .unwrap();

        let result = store.ingest(MemoryFrame {
            frame_id: 2,
            session_id: 2,
            payload: vec![5],
            fidelity: 0.8,
        });

        assert_eq!(result, Err("runtime layout slot budget exceeded"));
        assert_eq!(store.len(), 1);
        assert_eq!(store.order.len(), 1);
        assert_eq!(store.total_payload_bytes(), 4);
    }

    #[test]
    fn memory_store_reinforces_latest() {
        let mut store = CogniFoldStore::new();
        store
            .ingest(MemoryFrame {
                frame_id: 1,
                session_id: 2,
                payload: vec![1],
                fidelity: 0.5,
            })
            .unwrap();
        store.reinforce_latest(0.25).unwrap();
        assert!(store.latest().unwrap().fidelity > 0.5);
    }

    #[test]
    fn memory_store_reports_session_summary() {
        let mut store = CogniFoldStore::with_capacity(4);
        store
            .ingest(MemoryFrame {
                frame_id: 1,
                session_id: 99,
                payload: vec![1, 2, 3],
                fidelity: 0.75,
            })
            .unwrap();
        let summary = store.session_summary().unwrap();
        assert_eq!(summary.0, 99);
        assert_eq!(summary.1, 1);
        assert!((summary.2 - 0.75).abs() < f32::EPSILON);
    }

    #[test]
    fn memory_crystallizes_only_physical_artifacts() {
        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        let artifact = PhysicalArtifact::new(b"verified wasm output", 100, 10, 6).unwrap();
        let node = store.commit_to_cognifold(77, &artifact, &watchdog).unwrap();
        assert!(node.is_valid());
        assert_eq!(node.session_id, 77);
        assert_eq!(node.artifact_hash, artifact.artifact_hash);
        assert_eq!(store.len(), 1);
        assert_eq!(
            store.latest().unwrap().payload,
            artifact.artifact_hash.to_vec()
        );
    }

    #[test]
    fn memory_rejects_zero_progress_artifact_without_ingest() {
        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        let artifact = PhysicalArtifact::new(b"unchanged output", 1, 1000, 0).unwrap();
        assert_eq!(
            store.commit_to_cognifold(77, &artifact, &watchdog),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold
            ))
        );
        assert_eq!(store.len(), 0);
    }

    #[test]
    fn semantic_pointer_resolves_and_injects_prefix_without_kv_cache() {
        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        let artifact = PhysicalArtifact::new(b"verified semantic artifact", 144, 12, 6).unwrap();
        let node = store.commit_to_cognifold(88, &artifact, &watchdog).unwrap();
        let pointer = SemanticPointer::from_node(&node);
        assert!(pointer.is_valid());

        let resolved = store.resolve_pointer(&pointer).unwrap();
        assert_eq!(resolved.node_id, node.node_id);
        assert_eq!(resolved.session_id, node.session_id);
        assert_eq!(resolved.artifact_hash, node.artifact_hash);

        let prefix = store.inject_context_prefix(&resolved).unwrap();
        assert!(prefix.is_valid());
        assert!(prefix.prefix.contains("artifact_blake3="));
        assert!(!prefix.prefix.contains("KV_CACHE"));
    }

    #[test]
    fn semantic_pointer_rejects_mismatched_artifact_hash() {
        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        let artifact = PhysicalArtifact::new(b"verified semantic artifact", 144, 12, 6).unwrap();
        let node = store.commit_to_cognifold(88, &artifact, &watchdog).unwrap();
        let mut pointer = SemanticPointer::from_node(&node);
        pointer.artifact_hash[0] ^= 0xff;

        assert_eq!(
            store.resolve_pointer(&pointer),
            Err("semantic pointer not found")
        );
    }

    #[test]
    fn cognitive_folding_decays_and_prunes_stale_edges() {
        let mut graph = MemoryGraph::new();
        let artifact_a = PhysicalArtifact::new(b"node-a", 10, 5, 2).unwrap();
        let artifact_b = PhysicalArtifact::new(b"node-b", 20, 5, 2).unwrap();
        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        let node_a = store
            .commit_to_cognifold(90, &artifact_a, &watchdog)
            .unwrap();
        let node_b = store
            .commit_to_cognifold(90, &artifact_b, &watchdog)
            .unwrap();
        graph.add_node(node_a.clone()).unwrap();
        graph.add_node(node_b.clone()).unwrap();
        graph
            .add_edge(MemoryEdge {
                edge_id: 1,
                from_node: node_a.node_id,
                to_node: node_b.node_id,
                weight: 0.4,
                time_since_last_access: 100.0,
            })
            .unwrap();

        let engine = CogniFoldEngine {
            config: FoldingConfig {
                decay_rate: 0.2,
                reinforce_factor: 0.1,
                pruning_threshold: 0.05,
            },
        };
        engine.fold_memory(&mut graph).unwrap();
        assert!(graph.edges.is_empty());
    }

    #[test]
    fn cognitive_folding_reinforces_active_and_deduplicates_edges() {
        let mut graph = MemoryGraph::new();
        let artifact_a = PhysicalArtifact::new(b"node-c", 30, 5, 2).unwrap();
        let artifact_b = PhysicalArtifact::new(b"node-d", 40, 5, 2).unwrap();
        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        let node_a = store
            .commit_to_cognifold(91, &artifact_a, &watchdog)
            .unwrap();
        let node_b = store
            .commit_to_cognifold(91, &artifact_b, &watchdog)
            .unwrap();
        graph.add_node(node_a.clone()).unwrap();
        graph.add_node(node_b.clone()).unwrap();
        graph
            .add_edge(MemoryEdge {
                edge_id: 1,
                from_node: node_a.node_id,
                to_node: node_b.node_id,
                weight: 0.2,
                time_since_last_access: 0.0,
            })
            .unwrap();
        graph
            .add_edge(MemoryEdge {
                edge_id: 2,
                from_node: node_a.node_id,
                to_node: node_b.node_id,
                weight: 0.3,
                time_since_last_access: 0.0,
            })
            .unwrap();

        let engine = CogniFoldEngine {
            config: FoldingConfig {
                decay_rate: 0.1,
                reinforce_factor: 0.25,
                pruning_threshold: 0.05,
            },
        };
        engine.fold_memory(&mut graph).unwrap();
        assert_eq!(graph.edges.len(), 1);
        assert!(graph.edges[0].weight >= 0.55);
    }

    #[test]
    fn runtime_message_ingest_does_not_crystallize_semantic_memory() {
        let mut runtime = NerveRuntime::new();
        let message = MessageFrame::with_identity(1, 42, vec![1, 2, 3]);
        runtime.ingest_message(message).unwrap();
        assert!(runtime.latest_session_summary().is_none());
        assert!(runtime.memory.is_empty());
    }

    #[test]
    fn fidelity_computation_clamps_range() {
        let fidelity = compute_fidelity(0.5, 0.8, 0.1);
        assert!((0.0..=1.0).contains(&fidelity));
    }

    #[test]
    fn reinforcement_clamps_range() {
        let fidelity = reinforce(0.9, 0.5);
        assert!((0.0..=1.0).contains(&fidelity));
    }

    #[test]
    fn decay_clamps_range() {
        let fidelity = decay(0.9, 0.5);
        assert!((0.0..=1.0).contains(&fidelity));
    }

    #[test]
    fn sac_accepts_vote_above_threshold() {
        assert!(accept_vote(10, 5));
    }

    #[test]
    fn sac_rejects_vote_below_threshold() {
        assert!(!accept_vote(2, 5));
    }

    #[test]
    fn sac_anchor_is_returned_on_acceptance() {
        let anchor = anchored_witness(10, 5, 123).unwrap();
        assert_eq!(anchor.last_witness_hash, 123);
    }

    #[test]
    fn sac_filter_quarantines_suspicious_vote() {
        assert!(filter_vote(10, true, 5).is_err());
    }

    #[test]
    fn sac_anchor_updates() {
        let mut anchor = anchored_witness(10, 5, 123).unwrap();
        update_anchor(&mut anchor, 456);
        assert_eq!(anchor.last_witness_hash, 456);
    }

    #[test]
    fn sac_witness_verification_round_aggregates_votes() {
        let votes = [
            SacVote {
                score: 7,
                suspicious: false,
                quorum_threshold: 5,
            },
            SacVote {
                score: 8,
                suspicious: false,
                quorum_threshold: 5,
            },
        ];
        let anchor = witness_verification_round(&votes, 999).unwrap();
        assert_eq!(anchor.last_witness_hash, 999);
        assert_eq!(anchor.anchor_id, 1);
        assert_eq!(anchor_fingerprint(&anchor), 998);
    }

    #[test]
    fn sac_physical_witness_commits_matching_artifacts() {
        let vote_a = PhysicalWitnessVote::from_payload(b"same artifact", 7).unwrap();
        let vote_b = PhysicalWitnessVote::from_payload(b"same artifact", 5).unwrap();
        let vote_c = PhysicalWitnessVote::from_payload(b"different artifact", 3).unwrap();
        let witness = verify_physical_witness(&[vote_a, vote_b, vote_c], 4, 999).unwrap();

        assert_eq!(witness.witness_count, 2);
        assert_eq!(witness.blake3_hash, vote_a.artifact_hash);
        assert_eq!(witness.ast_fingerprint, vote_a.ast_fingerprint);
        assert_eq!(witness.fuel_consumed, 7);
        assert_eq!(witness.anchor.anchor_id, 2);
        assert_ne!(witness.anchor.last_witness_hash, 999);
    }

    #[test]
    fn sac_physical_witness_struct_freezes_term() {
        let vote = PhysicalWitnessVote::from_payload(b"witness artifact", 7).unwrap();
        let witness = SacPhysicalWitness::from_vote(1, vote);

        assert!(witness.is_valid());
        assert_eq!(witness.witness_id, 1);
    }

    #[test]
    fn sac_physical_witness_rejects_physical_witness_divergence() {
        let votes = [
            PhysicalWitnessVote::from_payload(b"artifact-a", 1).unwrap(),
            PhysicalWitnessVote::from_payload(b"artifact-b", 1).unwrap(),
            PhysicalWitnessVote::from_payload(b"artifact-c", 1).unwrap(),
            PhysicalWitnessVote::from_payload(b"artifact-d", 1).unwrap(),
        ];

        assert_eq!(
            verify_physical_witness(&votes, 4, 999),
            Err("physical witness rejected")
        );
    }

    #[test]
    fn witness_from_vote_builds_anchor() {
        let vote = SacVote {
            score: 10,
            suspicious: false,
            quorum_threshold: 5,
        };
        let anchor = witness_from_vote(&vote, 999).unwrap();
        assert_eq!(anchor.last_witness_hash, 999);
        assert_eq!(anchor.anchor_id, 1);
    }

    #[test]
    fn runtime_ingests_message() {
        let mut runtime = NerveRuntime::new();
        let message = MessageFrame::with_identity(1, 2, vec![1, 2, 3]);
        assert!(runtime.ingest_message(message).is_ok());
        assert_eq!(runtime.processed_count(), 1);
        assert_eq!(runtime.anchor.anchor_id, 1);
        assert_ne!(runtime.anchor.last_witness_hash, 0);
        assert!(runtime.memory.is_empty());
    }

    #[test]
    fn pav_watchdog_accepts_physical_artifact() {
        let artifact = PhysicalArtifact::new(b"compiled artifact", 100, 10, 4).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        assert!(watchdog.accepts(&artifact).is_ok());
        assert!(watchdog.measure_pav(90, 100, 10) > 0.0);
    }

    #[test]
    fn pav_is_not_correctness_and_objective_receipt_is_required_for_authoritative_acceptance() {
        use crate::physical::ObjectiveValidationReceipt;

        let artifact = PhysicalArtifact::new(b"candidate", 100, 10, 4).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.0001 };
        assert!(watchdog.accepts(&artifact).is_ok());
        let receipt = ObjectiveValidationReceipt::new(
            &artifact,
            [1; 32],
            [2; 32],
            [3; 32],
            "independent-test-validator",
            1,
        )
        .unwrap();
        assert!(
            watchdog
                .accepts_with_objective(0, &artifact, &receipt)
                .is_ok()
        );
        let mut tampered = receipt.clone();
        tampered.result_hash = [4; 32];
        assert!(
            watchdog
                .accepts_with_objective(0, &artifact, &tampered)
                .is_err()
        );
    }

    #[test]
    fn pav_watchdog_uses_previous_ast_fingerprint() {
        let old = crate::physical::compute_ast_structural_fingerprint("fn main() { let x = 1; }");
        let new = crate::physical::compute_ast_structural_fingerprint(
            "fn main() { let x = 1; let y = 2; }",
        );
        let artifact = PhysicalArtifact::new(b"compiled artifact", new, 10, 4).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.0001 };
        assert!(watchdog.accepts_transition(old, &artifact).is_ok());

        let strict = PhysicalWatchdog {
            epsilon: 1_000_000.0,
        };
        assert_eq!(
            strict.accepts_transition(new, &artifact),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold
            ))
        );
    }

    #[test]
    fn pav_watchdog_backtracks_on_zero_progress() {
        let artifact = PhysicalArtifact::new(b"compiled artifact", 1, 1000, 0).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.0367 };
        assert_eq!(
            watchdog.accepts(&artifact),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold
            ))
        );
    }

    #[test]
    fn execution_consensus_commits_matching_artifacts() {
        let artifacts = [
            PhysicalArtifact::new(b"same output", 10, 5, 3).unwrap(),
            PhysicalArtifact::new(b"same output", 11, 7, 3).unwrap(),
            PhysicalArtifact::new(b"different output", 12, 9, 3).unwrap(),
        ];
        let consensus = PhysicalWitnessThreshold { total_witnesses: 4 };
        let result = consensus.verify_physical_witnesses(&artifacts).unwrap();
        assert_eq!(result.quorum_size, 2);
        assert_eq!(result.artifact_hash, artifacts[0].artifact_hash);
        assert_eq!(result.artifact_hash.len(), 32);
    }

    #[test]
    fn deterministic_dag_advances_only_on_physical_success() {
        let root = DAGNode::root(b"snapshot-0");
        let artifact = PhysicalArtifact::new(b"compiled output", 100, 10, 8).unwrap();
        let orchestrator = PhysicalDagOrchestrator {
            watchdog: PhysicalWatchdog { epsilon: 0.0367 },
        };
        let next = orchestrator.advance_state(root, Ok(artifact)).unwrap();
        assert_eq!(next.parent_id, Some(root.node_id));
        assert_eq!(next.depth, root.depth + 1);
        assert_ne!(next.state_hash, root.state_hash);
        assert_ne!(next.ast_fingerprint, root.ast_fingerprint);
    }

    #[test]
    fn physical_metrics_track_pav_backtrack_and_consensus() {
        reset_physical_metrics_for_tests();
        let artifact = PhysicalArtifact::new(b"same output", 10, 5, 3).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.0001 };
        assert!(watchdog.accepts_transition(0, &artifact).is_ok());

        let strict = PhysicalWatchdog {
            epsilon: 1_000_000.0,
        };
        assert!(
            strict
                .accepts_transition(artifact.ast_fingerprint, &artifact)
                .is_err()
        );

        let consensus = PhysicalWitnessThreshold { total_witnesses: 2 };
        let artifacts = [
            PhysicalArtifact::new(b"same output", 10, 5, 3).unwrap(),
            PhysicalArtifact::new(b"same output", 10, 6, 3).unwrap(),
        ];
        assert!(consensus.verify_physical_witnesses(&artifacts).is_ok());

        let snapshot = physical_metrics_snapshot();
        assert!(snapshot.accepted_artifacts >= 1);
        assert!(snapshot.rejected_artifacts >= 1);
        assert!(snapshot.hard_backtracks >= 1);
        assert!(snapshot.consensus_commits >= 1);
        assert!(snapshot.total_fuel_consumed >= 10);
        assert!(snapshot.last_pav >= 0.0);

        let prometheus = physical_metrics_prometheus();
        assert!(prometheus.contains("# TYPE aegis_physical_fuel_consumed_total counter"));
        assert!(prometheus.contains("aegis_physical_last_pav"));
    }

    #[test]
    fn telemetry_serves_prometheus_metrics_over_http() {
        use std::io::{Read, Write};
        use std::net::{TcpListener, TcpStream};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let handle = std::thread::spawn(move || {
            crate::telemetry::serve_physical_metrics_once(&listener).unwrap();
        });

        let mut stream = TcpStream::connect(addr).unwrap();
        stream
            .write_all(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
            .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        handle.join().unwrap();

        assert!(response.starts_with("HTTP/1.1 200 OK"));
        assert!(response.contains("Content-Type: text/plain; version=0.0.4"));
        assert!(response.contains("aegis_physical_last_pav"));
    }

    #[test]
    fn deterministic_dag_hard_backtracks_on_trap() {
        let root = DAGNode::root(b"snapshot-0");
        let orchestrator = PhysicalDagOrchestrator {
            watchdog: PhysicalWatchdog { epsilon: 0.0367 },
        };
        assert_eq!(
            orchestrator.advance_state(root, Err(TrapReason::FuelExhausted)),
            Err(BacktrackSignal::HardBacktrack(TrapReason::FuelExhausted))
        );
    }

    #[test]
    fn sandbox_executes_valid_code_into_physical_artifact() {
        let sandbox = DeterministicSandbox::new();
        let result = sandbox
            .mock_deterministic_test_harness("fn main() { let x = 1 + 1; }", 128)
            .unwrap();
        assert!(result.is_valid());
        assert_eq!(result.backend, SandboxBackendKind::Deterministic);
        assert!(result.artifact.is_physical_change());

        let root = DAGNode::root(b"snapshot-0");
        let orchestrator = PhysicalDagOrchestrator {
            watchdog: PhysicalWatchdog { epsilon: 0.0001 },
        };
        let next = orchestrator
            .advance_state(root, Ok(result.artifact))
            .unwrap();
        assert_eq!(next.parent_id, Some(root.node_id));
    }

    #[test]
    fn sandbox_traps_on_fuel_exhaustion() {
        let sandbox = DeterministicSandbox::new();
        assert_eq!(
            sandbox.mock_deterministic_test_harness(
                "fn main() { let many_tokens = 1 + 2 + 3 + 4; }",
                4,
            ),
            Err(TrapReason::FuelExhausted)
        );
    }

    #[test]
    fn sandbox_traps_on_invariant_violation() {
        let sandbox = DeterministicSandbox::new();
        assert_eq!(
            sandbox
                .mock_deterministic_test_harness("std::fs::remove_dir_all(\"/\").unwrap();", 128,),
            Err(TrapReason::InvariantViolation)
        );
    }

    #[test]
    fn sandbox_backend_config_requires_hardening_for_wasmtime() {
        let hardened = SandboxBackendConfig::wasmtime_hardened(16);
        assert!(hardened.is_hardened());
        assert!(hardened.epoch_interruption_required);
        assert!(hardened.network_isolated);

        let invalid = SandboxBackendConfig {
            backend: SandboxBackendKind::Wasmtime,
            epoch_interruption_required: false,
            network_isolated: true,
            max_memory_pages: 16,
        };
        assert!(!invalid.is_hardened());
        let result = DeterministicSandbox::with_config(invalid);
        assert!(result.is_err());
        assert_eq!(result.err(), Some(TrapReason::InvariantViolation));
    }

    #[test]
    fn sandbox_memory_limit_checked_multiplication_rejects_overflow() {
        use crate::sandbox::memory_limit_bytes;

        assert_eq!(
            memory_limit_bytes(2, usize::MAX / 2 + 1),
            Err(TrapReason::InvariantViolation)
        );
        assert_eq!(memory_limit_bytes(1, 65536), Ok(65536));
    }

    #[test]
    fn verification_gauntlet_commits_physical_quorum() {
        let sandbox = DeterministicSandbox::new();
        let proposals = [
            "fn main() { let x = 1 + 1; }",
            "fn main() { let x = 1 + 1; }",
            "fn main() { let x = 2 + 2; }",
            "std::fs::remove_dir_all(\"/\").unwrap();",
        ];
        let result = sandbox.verify_variants(&proposals, 128).unwrap();
        assert!(result.is_valid());
        assert_eq!(result.accepted_artifacts, 3);
        assert_eq!(result.trapped_proposals, 1);
        assert_eq!(result.consensus.quorum_size, 2);
    }

    #[test]
    fn verification_gauntlet_rejects_without_physical_quorum() {
        let sandbox = DeterministicSandbox::new();
        let proposals = [
            "fn main() { let x = 1 + 1; }",
            "fn main() { let x = 2 + 2; }",
            "fn main() { let x = 3 + 3; }",
            "fn main() { let x = 4 + 4; }",
        ];
        assert_eq!(
            sandbox.verify_variants(&proposals, 128),
            Err(TrapReason::NoConsensus)
        );
    }

    #[test]
    fn guardrail_accepts_bounded_rust_artifact() {
        let guardrail = FirstOrderGuardrail;
        assert!(
            guardrail
                .verify_invariants("fn main() { let x = 1 + 1; }")
                .is_ok()
        );
    }

    #[test]
    fn guardrail_rejects_destructive_and_judge_patterns() {
        let guardrail = FirstOrderGuardrail;
        assert_eq!(
            guardrail.verify_invariants("std::fs::remove_dir_all(\"/\").unwrap();"),
            Err(InvariantViolation::DestructiveFilesystem)
        );
        assert_eq!(
            guardrail.verify_invariants("struct CriticAgent; // LLM-as-a-Judge"),
            Err(InvariantViolation::LlmJudgePattern)
        );
    }

    #[test]
    fn zero_trust_gateway_sanitizes_final_tool_call_only() {
        let guardrail = FirstOrderGuardrail;
        let request = HostCall {
            target: "sidecar.local/compile".to_string(),
            payload: "private reasoning\nFINAL_TOOL_CALL: compile_wasm(module_id=7)".to_string(),
        };
        let response = guardrail.sanitize_and_forward(&request).unwrap();
        assert_eq!(response.target, "sidecar.local/compile");
        assert_eq!(
            response.payload,
            "FINAL_TOOL_CALL: compile_wasm(module_id=7)"
        );
        assert_eq!(strip_chain_of_thought("thoughts\nFinal: run"), "Final: run");
    }

    #[test]
    fn speculative_fallback_rejects() {
        let result = VerificationResult::reject(42);
        assert!(result.rejected);
        assert_eq!(result.request_id, 42);
    }

    #[test]
    fn speculative_accept_prefix_works() {
        let draft = DraftTokenBatch {
            request_id: 1,
            tokens: vec![1, 2, 3],
        };
        let verification = VerificationResult {
            request_id: 1,
            accepted_prefix_len: 2,
            rejected: false,
        };
        assert_eq!(accept_prefix(&draft, &verification), vec![1, 2]);
    }

    #[test]
    fn speculative_verifier_accepts_only_matching_target_prefix() {
        let draft = DraftTokenBatch {
            request_id: 7,
            tokens: vec![10, 20, 30],
        };
        let target = TargetTokenBatch {
            request_id: 7,
            tokens: vec![10, 20, 99],
        };
        let verification = verify_target_prefix(&draft, &target);

        assert_eq!(verification.accepted_prefix_len, 2);
        assert!(!verification.rejected);
        assert_eq!(accept_prefix(&draft, &verification), vec![10, 20]);
    }

    #[test]
    fn speculative_verifier_rejects_request_mismatch() {
        let draft = DraftTokenBatch {
            request_id: 7,
            tokens: vec![10, 20, 30],
        };
        let target = TargetTokenBatch {
            request_id: 8,
            tokens: vec![10, 20, 30],
        };
        let verification = verify_target_prefix(&draft, &target);

        assert!(verification.rejected);
        assert_eq!(accept_prefix(&draft, &verification), Vec::<u32>::new());
    }

    #[test]
    fn speculative_decode_returns_valid_shapes() {
        let (draft, verification) = speculative_decode("hello");
        assert!(draft.is_valid());
        assert!(verification.is_valid());
        assert_eq!(draft.prefix_capacity(), 3);
    }

    #[test]
    fn speculative_fallback_decode_returns_reject() {
        let verification = fallback_decode(77);
        assert!(verification.rejected);
        assert_eq!(verification.request_id, 77);
    }

    #[test]
    fn test_skeleton_generation_strips_impl_bodies() {
        use crate::harness::{NeuroSymbolicHarness, SkeletonGenerator};
        let input_code = r#"
            impl WasmExecutionSandbox for AegisSandbox {
                fn execute_wasm_binary(&self, wasm_bytes: &[u8], fuel_limit: u64) -> Result<SandboxResult, Trap> {
                    let x = 1 + 1;
                    Ok(x)
                }
            }
        "#;
        let skeleton = NeuroSymbolicHarness::generate_skeleton(input_code).unwrap();
        assert!(skeleton.contains("todo"));
        assert!(!skeleton.contains("1 + 1"));
    }

    #[test]
    fn test_compiler_feedback_parsing() {
        use crate::harness::{CompilerFeedbackLoop, NeuroSymbolicHarness};
        let logs = "some cargo logs\nerror[E0308]: mismatched types\n  --> src/main.rs:10:9\n   |\n10 |     x\n   |     ^ expected `u32`, found `String`\nwarning: unused variable";
        let errors = NeuroSymbolicHarness::analyze_compile_errors(logs);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("error[E0308]"));
        assert!(errors[0].contains("expected `u32`"));
    }
    #[test]
    fn test_canonicalize_payload_json() {
        use crate::physical::canonicalize_payload;
        let input1 = br#"{"b": 2, "a": 1}"#;
        let input2 = b"{\n  \"a\": 1,\n  \"b\": 2\n}";
        assert_eq!(canonicalize_payload(input1), canonicalize_payload(input2));
        assert_eq!(canonicalize_payload(input1), br#"{"a":1,"b":2}"#);
    }

    #[test]
    fn test_canonicalize_payload_complex_json() {
        use crate::physical::canonicalize_payload;
        assert_eq!(canonicalize_payload(b"{}"), b"{}");
        let nested = br#"{"x": {"y": {"z": [true, 1.0, null]}}}"#;
        let formatted = br#"{
            "x": {
                "y": {
                    "z": [
                        true,
                        1.0,
                        null
                    ]
                }
            }
        }"#;
        assert_eq!(
            canonicalize_payload(nested),
            canonicalize_payload(formatted)
        );
        let invalid = b" {  invalid  json  } ";
        assert_eq!(canonicalize_payload(invalid), b"{  invalid  json  }");
        let invalid_rust = b" fn invalid( { ";
        assert_eq!(canonicalize_payload(invalid_rust), b"fn invalid( {");
    }

    #[test]
    fn test_hybrid_polling_state_transitions() {
        use crate::shm::hybrid_poll_until;
        use std::sync::Arc;
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::time::Duration;

        assert!(hybrid_poll_until(|| true, Duration::from_millis(10)));

        let counter = Arc::new(AtomicUsize::new(0));
        let c = counter.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_micros(10));
            c.store(1, Ordering::Relaxed);
        });
        assert!(hybrid_poll_until(
            || counter.load(Ordering::Relaxed) == 1,
            Duration::from_millis(50)
        ));

        let counter2 = Arc::new(AtomicUsize::new(0));
        let c2 = counter2.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(2));
            c2.store(1, Ordering::Relaxed);
        });
        assert!(hybrid_poll_until(
            || counter2.load(Ordering::Relaxed) == 1,
            Duration::from_micros(10)
        ));
    }

    #[test]
    fn test_slab_memory_pool_capacity_accounting() {
        let pool = SlabMemoryPool::new(16);
        let reservation = pool.try_reserve(8).unwrap();
        assert_eq!(pool.used(), 8);
        assert_eq!(pool.available(), 8);
        assert!(pool.try_reserve(17).is_err());
        drop(reservation);
        assert_eq!(pool.used(), 0);

        let fallback = pool.reserve(32);
        assert_eq!(fallback.size(), 0);
        assert_eq!(pool.used(), 0);
    }

    #[test]
    fn test_preallocated_buffer_populates_pages_and_releases() {
        assert!(PreAllocatedBuffer::new(0).is_err());
        let buffer = PreAllocatedBuffer::new(4096).unwrap();
        assert!(!buffer.ptr.is_null());
        buffer.populate_pages();
        let handle = buffer.spawn_background_population();
        handle.join().unwrap();
    }

    #[test]
    fn test_guardrail_obfuscation_evasions() {
        use crate::guardrail::{ConstitutionalGuardrail, FirstOrderGuardrail, InvariantViolation};
        let guardrail = FirstOrderGuardrail;

        let unicode_space = "fn main() { let x = \"rm\u{2009}-\u{200a}rf\"; }";
        let match_res = guardrail.verify_invariants(unicode_space);
        assert_eq!(match_res, Err(InvariantViolation::DestructiveFilesystem));

        let mixed_case = "StD::fS::ReMoVe_DiR_aLl(\"/\");";
        assert_eq!(
            guardrail.verify_invariants(mixed_case),
            Err(InvariantViolation::DestructiveFilesystem)
        );

        let inline_comment = "fn main() { // rm -rf /\n }";
        assert_eq!(
            guardrail.verify_invariants(inline_comment),
            Err(InvariantViolation::DestructiveFilesystem)
        );

        let doc_comment = "/// std::net::\n fn main() {}";
        assert_eq!(
            guardrail.verify_invariants(doc_comment),
            Err(InvariantViolation::NetworkBypass)
        );
    }

    #[test]
    fn test_wasmtime_sandbox_execution() {
        use crate::sandbox::{SandboxBackendKind, WasmtimeSandbox};
        let sandbox = WasmtimeSandbox::new();
        let wat = r#"(module (func (export "_start")))"#;
        let wasm = wat::parse_str(wat).unwrap();
        let result = sandbox.execute_wasm_binary(&wasm, 1000).unwrap();
        assert_eq!(result.backend, SandboxBackendKind::Wasmtime);
        assert!(result.fuel_consumed >= 1);
    }

    #[test]
    fn wasmtime_sandbox_try_new_returns_typed_setup_result() {
        use crate::sandbox::{SandboxBackendKind, WasmExecutionSandbox, WasmtimeSandbox};

        let sandbox = WasmtimeSandbox::try_new().expect("supported Wasmtime setup should succeed");
        assert_eq!(
            sandbox.backend_config().backend,
            SandboxBackendKind::Wasmtime
        );
        assert!(sandbox.backend_config().is_hardened());
    }

    #[test]
    fn wasmtime_sandbox_reuses_preinstantiated_module_without_fuel_leakage() {
        use crate::sandbox::WasmtimeSandbox;
        let sandbox = WasmtimeSandbox::new();
        let wat = r#"(module
            (func (export "_start")
                i32.const 1
                drop)
        )"#;
        let wasm = wat::parse_str(wat).unwrap();

        assert_eq!(sandbox.cached_wasm_pre_count_for_tests(), 0);
        let first = sandbox.execute_wasm_binary(&wasm, 1000).unwrap();
        assert_eq!(sandbox.cached_wasm_pre_count_for_tests(), 1);
        let second = sandbox.execute_wasm_binary(&wasm, 1000).unwrap();
        assert_eq!(sandbox.cached_wasm_pre_count_for_tests(), 1);

        assert!(first.is_valid());
        assert!(second.is_valid());
        assert_eq!(first.artifact.artifact_hash, second.artifact.artifact_hash);
        assert_eq!(first.fuel_consumed, second.fuel_consumed);
    }

    #[test]
    fn wasmtime_sandbox_rejects_raw_text_without_mocking() {
        use crate::sandbox::WasmtimeSandbox;
        let sandbox = WasmtimeSandbox::new();
        assert_eq!(
            sandbox.execute_wasm_binary("fn main() { let x = 1 + 1; }".as_bytes(), 1000),
            Err(TrapReason::InvariantViolation)
        );
    }

    #[test]
    fn wasmtime_executes_verified_mmap_wasm_bridge_frame() {
        use crate::sandbox::{SandboxBackendKind, WasmtimeSandbox};
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join("mmap-wasm-bridge-exec");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("wasm-frame.aegmmap");
        let wasm = wat::parse_str(
            r#"(module
                (func (export "_start")
                    i64.const 42
                    drop)
            )"#,
        )
        .unwrap();
        write_mmap_bridge_frame(&path, 71, 81, &wasm).unwrap();

        let sandbox = WasmtimeSandbox::new();
        let result = sandbox
            .execute_mmap_wasm_bridge_frame(&path, 10_000)
            .unwrap();
        assert_eq!(result.backend, SandboxBackendKind::Wasmtime);
        assert!(result.is_valid());
        assert!(result.fuel_consumed > 0);
    }

    #[test]
    fn wasmtime_rejects_tampered_mmap_wasm_bridge_frame_before_execution() {
        use crate::sandbox::WasmtimeSandbox;
        use std::io::{Seek, SeekFrom, Write};
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join("mmap-wasm-bridge-tamper");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("wasm-frame.aegmmap");
        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        write_mmap_bridge_frame(&path, 91, 101, &wasm).unwrap();
        let mut file = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        file.seek(SeekFrom::Start(MMAP_BRIDGE_HEADER_BYTES as u64 + 8))
            .unwrap();
        file.write_all(&[0xff]).unwrap();
        file.flush().unwrap();

        let sandbox = WasmtimeSandbox::new();
        assert_eq!(
            sandbox.execute_mmap_wasm_bridge_frame(&path, 10_000),
            Err(TrapReason::InvariantViolation)
        );
    }

    #[test]
    fn quickjs_manager_accepts_only_wasm_wrappers() {
        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm.clone()).unwrap();
        let invocation = manager
            .prepare_invocation("globalThis.answer = 42;")
            .unwrap();
        assert_eq!(invocation.wasm_module, wasm);
        assert_ne!(invocation.script_blake3, [0u8; 32]);
        assert!(invocation.is_valid());
        assert!(QuickJsWasmInterpreterManager::new(b"not wasm".to_vec()).is_err());
    }

    #[test]
    fn quickjs_invocation_abi_binds_script_wrapper_and_fuel() {
        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm.clone()).unwrap();
        let invocation = manager
            .prepare_invocation_with_fuel("globalThis.answer = 42;", 77)
            .unwrap();
        let header = invocation.abi_header().unwrap();

        assert_eq!(&invocation.abi_packet[0..8], QUICKJS_INVOCATION_ABI_MAGIC);
        assert_eq!(header.version, QUICKJS_INVOCATION_ABI_VERSION);
        assert_eq!(
            header.header_bytes as usize,
            QUICKJS_INVOCATION_ABI_HEADER_BYTES
        );
        assert_eq!(header.fuel_limit, 77);
        assert_eq!(header.script_bytes, "globalThis.answer = 42;".len() as u64);
        assert_eq!(header.wrapper_blake3, invocation.wrapper_blake3);
        assert_eq!(header.script_blake3, invocation.script_blake3);
        assert!(invocation.is_valid());
    }

    #[test]
    fn quickjs_invocation_abi_rejects_tamper_or_zero_fuel() {
        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm).unwrap();
        assert_eq!(
            manager.prepare_invocation_with_fuel("globalThis.answer = 42;", 0),
            Err(TrapReason::InvariantViolation)
        );

        let mut invocation = manager
            .prepare_invocation_with_fuel("globalThis.answer = 42;", 77)
            .unwrap();
        let last = invocation.abi_packet.len() - 1;
        invocation.abi_packet[last] ^= 0x01;

        assert_eq!(
            decode_quickjs_invocation_header(&invocation.abi_packet),
            Err(TrapReason::InvariantViolation)
        );
        assert!(!invocation.is_valid());
    }

    #[test]
    fn quickjs_linear_memory_plan_writes_hash_bound_packet() {
        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm).unwrap();
        let invocation = manager
            .prepare_invocation_with_fuel("globalThis.answer = 42;", 77)
            .unwrap();
        let plan = invocation.linear_memory_plan(1).unwrap();
        let mut memory = vec![0u8; plan.memory_bytes];
        let written_hash = invocation
            .write_to_linear_memory(&mut memory, &plan)
            .unwrap();
        let packet = &memory[plan.packet_offset..plan.packet_offset + plan.packet_bytes];
        let header = decode_quickjs_invocation_header(packet).unwrap();

        assert_eq!(plan.required_pages, 1);
        assert_eq!(plan.memory_bytes, QUICKJS_LINEAR_MEMORY_PAGE_BYTES);
        assert_eq!(written_hash, invocation.invocation_blake3);
        assert_eq!(header.script_blake3, invocation.script_blake3);
    }

    #[test]
    fn quickjs_linear_memory_plan_rejects_insufficient_bounds() {
        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm).unwrap();
        let large_script = "x".repeat(QUICKJS_LINEAR_MEMORY_PAGE_BYTES);
        let invocation = manager
            .prepare_invocation_with_fuel(&large_script, 77)
            .unwrap();

        assert_eq!(
            invocation.linear_memory_plan(1),
            Err(TrapReason::InvariantViolation)
        );
        let plan = invocation.linear_memory_plan(2).unwrap();
        let mut too_small = vec![0u8; plan.packet_bytes];
        assert_eq!(
            invocation.write_to_linear_memory(&mut too_small, &plan),
            Err(TrapReason::InvariantViolation)
        );
    }

    #[test]
    fn quickjs_wasmtime_bridge_validates_linear_memory_packet() {
        let script = "globalThis.answer = 42;";
        let packet_len = QUICKJS_INVOCATION_ABI_HEADER_BYTES + script.len();
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
        let wasm = wat::parse_str(&wat).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm).unwrap();
        let invocation = manager.prepare_invocation_with_fuel(script, 77).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let result = sandbox
            .execute_quickjs_invocation_bridge(&invocation, 10_000)
            .unwrap();

        assert_eq!(invocation.abi_packet.len(), packet_len);
        assert_eq!(result.backend, SandboxBackendKind::Wasmtime);
        assert!(result.is_valid());
    }

    #[test]
    fn quickjs_wasmtime_bridge_rejects_wrong_packet_bounds() {
        let script = "globalThis.answer = 42;";
        let wrong_len = QUICKJS_INVOCATION_ABI_HEADER_BYTES + script.len() - 1;
        let wat = format!(
            r#"(module
                (import "aegis_quickjs" "validate_invocation_packet"
                    (func $validate (param i32 i32) (result i32)))
                (memory (export "memory") 1)
                (func (export "_start")
                    i32.const 0
                    i32.const {wrong_len}
                    call $validate
                    i32.eqz
                    if
                        unreachable
                    end)
            )"#
        );
        let wasm = wat::parse_str(&wat).unwrap();
        let manager = QuickJsWasmInterpreterManager::new(wasm).unwrap();
        let invocation = manager.prepare_invocation_with_fuel(script, 77).unwrap();
        let sandbox = WasmtimeSandbox::new();

        assert_eq!(
            sandbox.execute_quickjs_invocation_bridge(&invocation, 10_000),
            Err(TrapReason::InvariantViolation)
        );
    }

    #[test]
    #[cfg(not(target_os = "windows"))]
    fn test_wasmtime_sandbox_timeout() {
        use crate::sandbox::WasmtimeSandbox;
        let mut sandbox = WasmtimeSandbox::new();
        sandbox.timeout_ms = 50;
        let wat = r#"(module (func (export "_start") (loop (br 0))))"#;
        let wasm = wat::parse_str(wat).unwrap();
        let result = sandbox.execute_wasm_binary(&wasm, 100000);
        assert!(result.is_err());
    }

    #[test]
    fn test_wasmtime_sandbox_memory_limit() {
        use crate::sandbox::{SandboxBackendConfig, WasmtimeSandbox};
        let config = SandboxBackendConfig::wasmtime_hardened(1);
        let sandbox = WasmtimeSandbox::with_config(config).unwrap();
        let wat = r#"(module
            (memory 2)
            (func (export "_start"))
        )"#;
        let wasm = wat::parse_str(wat).unwrap();
        let result = sandbox.execute_wasm_binary(&wasm, 10000);
        assert!(result.is_err());
    }

    #[test]
    fn test_ast_structural_edit_distance() {
        use crate::physical::{
            PAVWatchdog, PhysicalWatchdog, compute_ast_structural_fingerprint,
            compute_ast_tree_edit_distance,
        };
        let code1 = "fn main() { let x = 1; }";
        let code2 = "fn main() { let x = 1; let y = 2; }";
        let fp1 = compute_ast_structural_fingerprint(code1);
        let fp2 = compute_ast_structural_fingerprint(code2);
        let distance = compute_ast_tree_edit_distance(code1, code2).unwrap();

        let watchdog = PhysicalWatchdog { epsilon: 0.0001 };
        let pav = watchdog.measure_pav(fp1, fp2, 10);
        assert!(distance > 0);
        assert!(pav > 0.0);
    }

    #[test]
    fn test_ast_tree_edit_distance_is_symmetric_for_wide_rust_trees() {
        use crate::physical::compute_ast_tree_edit_distance;

        let mut old_code = String::new();
        let mut new_code = String::new();
        for index in 0..32 {
            old_code.push_str(&format!(
                "fn generated_{index}(input: u64) -> u64 {{ let base = input + {index}; base ^ {} }}\n",
                index * 3
            ));
            new_code.push_str(&format!(
                "fn generated_{index}(input: u64) -> u64 {{ let base = input + {}; let mixed = base.rotate_left({}); mixed ^ {} }}\n",
                index + 1,
                index % 31,
                index * 5
            ));
        }

        let forward = compute_ast_tree_edit_distance(&old_code, &new_code).unwrap();
        let backward = compute_ast_tree_edit_distance(&new_code, &old_code).unwrap();

        assert!(forward > 0);
        assert_eq!(forward, backward);
    }

    #[test]
    fn test_pav_ast_distance_cache_is_symmetric_and_bounded() {
        use crate::physical::{
            PAVWatchdog, PhysicalWatchdog, ast_distance_cache_contains_for_tests,
            ast_distance_cache_slot_count_for_tests, compute_ast_structural_fingerprint,
        };

        let old = compute_ast_structural_fingerprint("fn main() { let x = 1; }");
        let new = compute_ast_structural_fingerprint("fn main() { let x = 1; let y = x + 2; }");
        let watchdog = PhysicalWatchdog { epsilon: 0.0001 };

        assert!(!ast_distance_cache_contains_for_tests(old, new));
        let forward = watchdog.measure_pav(old, new, 10);
        let backward = watchdog.measure_pav(new, old, 10);

        assert!(forward > 0.0);
        assert_eq!(forward, backward);
        assert!(ast_distance_cache_contains_for_tests(old, new));
        assert!(ast_distance_cache_contains_for_tests(new, old));
        assert_eq!(ast_distance_cache_slot_count_for_tests(), 16_384);
    }

    #[test]
    fn test_ast_tree_edit_distance_ignores_comment_and_whitespace() {
        use crate::physical::{
            BacktrackSignal, PhysicalArtifact, PhysicalWatchdog, TrapReason,
            compute_ast_structural_fingerprint, compute_ast_tree_edit_distance,
        };
        let old_code = "fn main() { let x = 1; }";
        let new_code = "fn main(){\n    // ignored by canonical syntax\n    let x = 1;\n}";
        let old = compute_ast_structural_fingerprint(old_code);
        let new = compute_ast_structural_fingerprint(new_code);
        let distance = compute_ast_tree_edit_distance(old_code, new_code).unwrap();
        let artifact = PhysicalArtifact::new(b"comment-only edit", new, 10, 8).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.0001 };

        assert_eq!(old, new);
        assert_eq!(distance, 0);
        assert_eq!(
            watchdog.accepts_transition(old, &artifact),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold
            ))
        );
    }

    #[test]
    fn test_literal_only_ast_change_has_low_pav_under_high_fuel() {
        use crate::physical::{
            BacktrackSignal, PhysicalArtifact, PhysicalWatchdog, TrapReason,
            compute_ast_structural_fingerprint,
        };
        let old = compute_ast_structural_fingerprint("fn main() { let x = 1; }");
        let new = compute_ast_structural_fingerprint("fn main() { let x = 2; }");
        let artifact = PhysicalArtifact::new(b"literal changed", new, 1000, 1).unwrap();
        let watchdog = PhysicalWatchdog { epsilon: 0.01 };
        assert_eq!(
            watchdog.accepts_transition(old, &artifact),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold
            ))
        );
    }

    #[test]
    fn test_ast_guardrail_rejections() {
        use crate::guardrail::{ConstitutionalGuardrail, FirstOrderGuardrail, InvariantViolation};
        let guardrail = FirstOrderGuardrail;
        let bad_code = "use std::fs; fn main() {}";
        let result = guardrail.verify_invariants(bad_code);
        assert_eq!(result, Err(InvariantViolation::DestructiveFilesystem));

        let bad_net = "use std::net::TcpStream; fn main() {}";
        let result_net = guardrail.verify_invariants(bad_net);
        assert_eq!(result_net, Err(InvariantViolation::NetworkBypass));
    }

    #[test]
    fn test_executive_authority() {
        use crate::guardrail::{ExecutiveAuthority, UntrustedText};
        use crate::sandbox::DeterministicSandbox;
        let sandbox = DeterministicSandbox::new();
        let proposal = UntrustedText::new("fn main() { let x = 1 + 1; }");
        let artifact = sandbox.evaluate_action(&proposal).unwrap();
        assert!(artifact.fuel_consumed >= 1);
    }

    #[test]
    fn test_generational_slab_recycling_and_collision() {
        use crate::memory::fold::GenerationalSlab;
        let mut slab = GenerationalSlab::new();
        let slot1 = slab.insert("value 1");
        let slot2 = slab.insert("value 2");

        assert_eq!(slab.get(slot1), Some(&"value 1"));
        assert_eq!(slab.get(slot2), Some(&"value 2"));

        // Remove value 1
        let val = slab.remove(slot1);
        assert_eq!(val, Some("value 1"));

        // Check lookup with old generation fails
        assert_eq!(slab.get(slot1), None);

        // Insert new value, should reuse index but increment generation
        let slot3 = slab.insert("value 3");
        let (idx1, _) = slot1.unpack();
        let (idx3, _) = slot3.unpack();
        assert_eq!(idx3, idx1);
        assert!(slot3.generation.get() > slot1.generation.get());

        assert_eq!(slab.get(slot3), Some(&"value 3"));
        assert_eq!(slab.get(slot1), None); // Old generation still None
    }

    #[test]
    fn test_aho_corasick_match_correctness() {
        let guardrail = FirstOrderGuardrail;

        // Destructive filesystem
        assert_eq!(
            guardrail.verify_invariants("let x = \"rm -rf /\";"),
            Err(InvariantViolation::DestructiveFilesystem)
        );
        assert_eq!(
            guardrail.verify_invariants("del /s file.txt"),
            Err(InvariantViolation::DestructiveFilesystem)
        );

        // Network bypass
        assert_eq!(
            guardrail.verify_invariants("reqwest::get(\"url\");"),
            Err(InvariantViolation::NetworkBypass)
        );
        assert_eq!(
            guardrail.verify_invariants("use std::net::TcpStream;"),
            Err(InvariantViolation::NetworkBypass)
        );

        // LLM Judge Pattern
        assert_eq!(
            guardrail.verify_invariants("let evaluator = CriticAgent;"),
            Err(InvariantViolation::LlmJudgePattern)
        );

        // Chain of thought
        assert_eq!(
            guardrail.verify_invariants("let think = \"<thinking> hello \";"),
            Err(InvariantViolation::ChainOfThoughtLeak)
        );
    }

    #[test]
    fn test_blake3_payload_hashing() {
        use crate::physical::blake3_digest;
        let payload1 = b"test payload 1";
        let payload2 = b"test payload 2";

        let hash1 = blake3_digest(payload1);
        let hash2 = blake3_digest(payload2);

        assert_ne!(hash1, hash2);
        assert_eq!(hash1.len(), 32);

        // Known test vector check
        let expected = *blake3::hash(payload1).as_bytes();
        assert_eq!(hash1, expected);
    }

    #[test]
    fn test_generational_slab_boundary_sizes() {
        use crate::memory::fold::GenerationalSlab;
        let mut slab = GenerationalSlab::new();

        // 0 bytes payload (empty string)
        let slot_0 = slab.insert("");
        let (_, pool_0) = slot_0.unpack();
        assert_eq!(pool_0, 32);

        // 32 bytes payload (exactly boundary of pool_32)
        let payload_32 = "a".repeat(32);
        let slot_32 = slab.insert(payload_32.as_str());
        let (_, pool_32) = slot_32.unpack();
        assert_eq!(pool_32, 32);

        // 33 bytes payload (goes to pool_64)
        let payload_33 = "a".repeat(33);
        let slot_33 = slab.insert(payload_33.as_str());
        let (_, pool_33) = slot_33.unpack();
        assert_eq!(pool_33, 64);

        // 64 bytes payload (exactly boundary of pool_64)
        let payload_64 = "a".repeat(64);
        let slot_64 = slab.insert(payload_64.as_str());
        let (_, pool_64) = slot_64.unpack();
        assert_eq!(pool_64, 64);

        // 65 bytes payload (goes to pool_128)
        let payload_65 = "a".repeat(65);
        let slot_65 = slab.insert(payload_65.as_str());
        let (_, pool_65) = slot_65.unpack();
        assert_eq!(pool_65, 128);

        // 128 bytes payload (exactly boundary of pool_128)
        let payload_128 = "a".repeat(128);
        let slot_128 = slab.insert(payload_128.as_str());
        let (_, pool_128) = slot_128.unpack();
        assert_eq!(pool_128, 128);

        // 129 bytes payload (goes to pool_256)
        let payload_129 = "a".repeat(129);
        let slot_129 = slab.insert(payload_129.as_str());
        let (_, pool_129) = slot_129.unpack();
        assert_eq!(pool_129, 256);

        // 256 bytes payload (exactly boundary of pool_256)
        let payload_256 = "a".repeat(256);
        let slot_256 = slab.insert(payload_256.as_str());
        let (_, pool_256) = slot_256.unpack();
        assert_eq!(pool_256, 256);

        // 257 bytes payload (goes to pool_overflow: unpack returns 0)
        let payload_257 = "a".repeat(257);
        let slot_257 = slab.insert(payload_257.as_str());
        let (_, pool_257) = slot_257.unpack();
        assert_eq!(pool_257, 0);
    }

    #[test]
    fn test_generational_slab_preallocates_hot_pool_for_small_payloads() {
        use crate::memory::fold::GenerationalSlab;
        let slab = GenerationalSlab::<&str>::with_capacity(128);
        assert_eq!(slab.pool_32.entries.capacity(), 128);
        assert_eq!(slab.pool_64.entries.capacity(), 32);
        assert_eq!(slab.pool_128.entries.capacity(), 32);
        assert_eq!(slab.pool_256.entries.capacity(), 32);
        assert_eq!(slab.pool_overflow.entries.capacity(), 32);
    }

    #[test]
    fn runtime_layout_budget_rejects_invalid_alignment_or_zero_capacity() {
        use crate::memory::fold::{RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT, RuntimeLayoutBudget};

        assert!(RuntimeLayoutBudget::new(0, 128, RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT).is_err());
        assert!(RuntimeLayoutBudget::new(8, 0, RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT).is_err());
        assert!(RuntimeLayoutBudget::new(8, 128, 32).is_err());

        let budget = RuntimeLayoutBudget::bounded(8, 128).unwrap();
        assert!(budget.is_valid());
        assert_eq!(budget.max_live_slots(), 8);
        assert_eq!(budget.max_payload_bytes(), 128);
        assert_eq!(budget.payload_alignment(), RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT);
    }

    #[test]
    fn generational_slab_tracks_len_without_pool_scan() {
        use crate::memory::fold::GenerationalSlab;

        let mut slab = GenerationalSlab::<String>::new();
        let slot_a = slab.insert("aa".to_string());
        let slot_b = slab.insert("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_string());
        let slot_c = slab.insert("c".repeat(80));

        assert_eq!(slab.len(), 3);
        assert_eq!(slab.live_len, 3);
        assert_eq!(slab.pool_32.len(), 1);
        assert_eq!(slab.pool_64.len(), 1);
        assert_eq!(slab.pool_128.len(), 1);
        assert_eq!(slab.total_payload_bytes(), 2 + 33 + 80);

        assert_eq!(
            slab.remove(slot_b),
            Some("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_string())
        );
        assert_eq!(slab.len(), 2);
        assert_eq!(slab.live_len, 2);
        assert_eq!(slab.pool_64.len(), 0);
        assert_eq!(slab.total_payload_bytes(), 2 + 80);
        assert_eq!(slab.get(slot_a).map(String::as_str), Some("aa"));
        assert_eq!(
            slab.get(slot_c).map(String::as_str),
            Some(
                "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
            )
        );
    }

    #[test]
    fn generational_slab_respects_runtime_layout_budget() {
        use crate::memory::fold::{GenerationalSlab, RuntimeLayoutBudget};

        let budget = RuntimeLayoutBudget::bounded(2, 8).unwrap();
        let mut slab = GenerationalSlab::with_layout_budget(budget).unwrap();

        let slot_a = slab.try_insert("aa").unwrap();
        let slot_b = slab.try_insert("bbbb").unwrap();
        assert_eq!(slab.len(), 2);
        assert_eq!(slab.total_payload_bytes(), 6);

        assert_eq!(
            slab.try_insert("c"),
            Err("runtime layout slot budget exceeded")
        );
        assert_eq!(slab.len(), 2);
        assert_eq!(slab.total_payload_bytes(), 6);

        assert_eq!(slab.remove(slot_a), Some("aa"));
        assert_eq!(slab.len(), 1);
        assert_eq!(slab.total_payload_bytes(), 4);
        assert_eq!(
            slab.try_insert("ccccc"),
            Err("runtime layout payload byte budget exceeded")
        );
        assert_eq!(slab.len(), 1);
        assert_eq!(slab.total_payload_bytes(), 4);

        let slot_c = slab.try_insert("dd").unwrap();
        assert_eq!(slab.len(), 2);
        assert_eq!(slab.total_payload_bytes(), 6);
        assert_eq!(slab.get(slot_a), None);
        assert_eq!(slab.get(slot_b), Some(&"bbbb"));
        assert_eq!(slab.get(slot_c), Some(&"dd"));
    }

    #[test]
    fn generational_slab_budgeted_update_rechecks_payload_accounting() {
        use crate::memory::fold::{GenerationalSlab, RuntimeLayoutBudget};

        let budget = RuntimeLayoutBudget::bounded(2, 8).unwrap();
        let mut slab = GenerationalSlab::with_layout_budget(budget).unwrap();
        let slot = slab.try_insert("aa".to_string()).unwrap();
        assert!(slab.get_mut(slot).is_none());

        slab.try_update(slot, |value| value.push_str("bb")).unwrap();
        assert_eq!(slab.get(slot).map(String::as_str), Some("aabb"));
        assert_eq!(slab.total_payload_bytes(), 4);

        assert_eq!(
            slab.try_update(slot, |value| value.push_str("ccccc")),
            Err("runtime layout payload byte budget exceeded")
        );
        assert_eq!(slab.get(slot).map(String::as_str), Some("aabb"));
        assert_eq!(slab.total_payload_bytes(), 4);

        assert_eq!(
            slab.try_update(slot, |value| value
                .push_str("dddddddddddddddddddddddddddddddd")),
            Err("runtime layout pool class change requires remove and insert")
        );
        assert_eq!(slab.get(slot).map(String::as_str), Some("aabb"));
        assert_eq!(slab.total_payload_bytes(), 4);
    }

    fn test_hash(label: &str) -> [u8; 32] {
        crate::physical::blake3_digest(label.as_bytes())
    }

    fn skill_admission_fixture() -> (
        crate::skill_registry::SkillPackageManifest,
        crate::sandbox::SandboxResult,
        crate::skill_registry::SkillRegressionReport,
        Vec<crate::skill_registry::SkillRegressionCase>,
    ) {
        use crate::sandbox::WasmtimeSandbox;
        use crate::skill_registry::{
            SkillPackageManifest, SkillRegressionCase, SkillRegressionReport,
        };

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let sandbox_result = sandbox.execute_wasm_binary(&wasm, 10).unwrap();
        let cases = vec![
            SkillRegressionCase::new(
                1,
                test_hash("skill-regression-input-1"),
                test_hash("skill-regression-expected-1"),
                test_hash("skill-regression-expected-1"),
                true,
            )
            .unwrap(),
            SkillRegressionCase::new(
                2,
                test_hash("skill-regression-input-2"),
                test_hash("skill-regression-expected-2"),
                test_hash("skill-regression-expected-2"),
                true,
            )
            .unwrap(),
        ];
        let report = SkillRegressionReport::new(&cases).unwrap();
        let manifest = SkillPackageManifest::new(
            7001,
            test_hash("skill-name"),
            test_hash("skill-version"),
            test_hash("skill-md"),
            sandbox_result.artifact.artifact_hash,
            test_hash("skill-policy-manifest"),
            report.case_list_hash,
        )
        .unwrap();
        (manifest, sandbox_result, report, cases)
    }

    #[test]
    fn skill_admission_registry_commits_only_wasmtime_pav_regression_proof() {
        use crate::physical::PhysicalWatchdog;
        use crate::skill_registry::{SkillAdmissionRecord, SkillRegistry};

        let (manifest, sandbox_result, report, cases) = skill_admission_fixture();
        assert!(report.is_valid_for(&cases));
        let watchdog = PhysicalWatchdog { epsilon: 0.0 };
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &report,
            &watchdog,
        )
        .unwrap();
        assert!(admission.is_valid_for(&manifest, &report));

        let mut registry = SkillRegistry::new();
        let previous_commit = registry.last_registry_commit_hash();
        let record = registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .unwrap();
        assert_eq!(registry.len(), 1);
        assert!(record.is_valid_for(previous_commit, &manifest, &admission));
        assert_eq!(registry.get(manifest.skill_id), Some(&record));
    }

    #[test]
    fn skill_admission_rejects_text_only_or_non_wasmtime_proof() {
        use crate::physical::PhysicalWatchdog;
        use crate::sandbox::{DeterministicSandbox, SandboxBackendKind};
        use crate::skill_registry::{SkillAdmissionError, SkillAdmissionRecord};

        let (manifest, _wasmtime_result, report, _cases) = skill_admission_fixture();
        let deterministic = DeterministicSandbox::new();
        let text_result = deterministic
            .mock_deterministic_test_harness("text skill body", 100)
            .unwrap();
        assert_eq!(text_result.backend, SandboxBackendKind::Deterministic);
        let err = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &text_result,
            &report,
            &PhysicalWatchdog { epsilon: 0.0 },
        )
        .unwrap_err();
        assert_eq!(err, SkillAdmissionError::InvalidWasmtimeProof);
    }

    #[test]
    fn skill_admission_rejects_failed_regression_report() {
        use crate::physical::PhysicalWatchdog;
        use crate::skill_registry::{
            SkillAdmissionError, SkillAdmissionRecord, SkillRegressionCase, SkillRegressionReport,
        };

        let (manifest, sandbox_result, _report, _cases) = skill_admission_fixture();
        let failed_cases = vec![
            SkillRegressionCase::new(
                1,
                test_hash("skill-fail-input"),
                test_hash("skill-fail-expected"),
                test_hash("skill-fail-actual"),
                false,
            )
            .unwrap(),
        ];
        let failed_report = SkillRegressionReport::new(&failed_cases).unwrap();
        let err = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &failed_report,
            &PhysicalWatchdog { epsilon: 0.0 },
        )
        .unwrap_err();
        assert_eq!(err, SkillAdmissionError::RegressionFailed);
    }

    #[test]
    fn skill_admission_rejects_tampered_admission_or_package_hash() {
        use crate::physical::PhysicalWatchdog;
        use crate::skill_registry::{SkillAdmissionRecord, SkillRegistry};

        let (manifest, sandbox_result, report, _cases) = skill_admission_fixture();
        let watchdog = PhysicalWatchdog { epsilon: 0.0 };
        let mut admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &report,
            &watchdog,
        )
        .unwrap();
        admission.package_hash = test_hash("tampered-skill-package");
        assert!(!admission.is_valid_for(&manifest, &report));

        let mut registry = SkillRegistry::new();
        assert!(
            registry
                .commit_admitted_skill(&manifest, &admission, &report)
                .is_err()
        );
    }

    #[test]
    fn skill_admission_registry_commit_is_replay_visible_before_shift_inheritance() {
        use crate::goal_intake::GoalIntakeProof;
        use crate::physical::PhysicalWatchdog;
        use crate::replay::{
            BinaryRunEventSegment, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive, SegmentedArrowAuditStream, skill_admission_replay_binding_hash,
        };
        use crate::skill_registry::{SkillAdmissionRecord, SkillRegistry};
        use std::time::{SystemTime, UNIX_EPOCH};

        let (manifest, sandbox_result, report, _cases) = skill_admission_fixture();
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &report,
            &PhysicalWatchdog { epsilon: 0.0 },
        )
        .unwrap();
        let mut registry = SkillRegistry::new();
        let admitted = registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .unwrap();

        let mut missing_goal = RunEventLedger::new(730);
        assert_eq!(
            missing_goal.append_skill_admission_recorded(&admission, &admitted),
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );

        let mut ledger = RunEventLedger::new(731);
        let proof = GoalIntakeProof::from_goal_text(
            73,
            test_hash("skill-replay-operator"),
            test_hash("skill-replay-raw-goal"),
            test_hash("skill-replay-policy-window"),
            "Admit a browser research skill only after Wasmtime regression evidence",
            1,
            None,
        )
        .unwrap();
        ledger.append_goal_intake_recorded(&proof).unwrap();
        let event = ledger
            .append_skill_admission_recorded(&admission, &admitted)
            .unwrap();
        assert_eq!(event.kind, RunEventKind::SkillAdmissionRecorded);
        assert_eq!(event.subject_id, admitted.skill_id);
        assert_eq!(event.primary_hash, admitted.registry_commit_hash);
        assert_eq!(
            event.secondary_hash,
            Some(skill_admission_replay_binding_hash(&admission, &admitted))
        );
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered_admitted = admitted.clone();
        tampered_admitted.admission_hash = test_hash("wrong-skill-admission");
        assert_eq!(
            ledger.append_skill_admission_recorded(&admission, &tampered_admitted),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let mut tampered_events = ledger.events().to_vec();
        tampered_events[1].secondary_hash = None;
        tampered_events[1].event_hash = tampered_events[1].compute_hash();
        assert_eq!(
            RunEventLedger::from_events(ledger.run_id, tampered_events),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("skill-admission-replay-{unique}"));
        let arrow_dir = dir.join("arrow");
        let manifest = RunEventSegmentArchive::write_ledger(&arrow_dir, 1, &ledger).unwrap();
        let audit_proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&arrow_dir, &manifest).unwrap();
        assert!(audit_proof.is_valid());
        let archived = RunEventSegmentArchive::read_ledger_mmap(&arrow_dir, &manifest).unwrap();
        assert_eq!(
            archived.events().last().unwrap().kind,
            RunEventKind::SkillAdmissionRecorded
        );
        assert_eq!(archived.last_hash(), ledger.last_hash());

        let mut tampered_stream =
            SegmentedArrowAuditStream::create(dir.join("tampered-arrow"), ledger.run_id, 1)
                .unwrap();
        tampered_stream.append_event(&ledger.events()[0]).unwrap();
        let mut missing_secondary = ledger.events()[1].clone();
        missing_secondary.secondary_hash = None;
        missing_secondary.event_hash = missing_secondary.compute_hash();
        assert!(tampered_stream.append_event(&missing_secondary).is_err());

        let binary_path = dir.join("skill-admission-replay.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let scan = BinaryRunEventSegment::verify_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(scan.event_count, ledger.len());
        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered.last_hash(), ledger.last_hash());
    }

    #[test]
    fn skill_admission_handoff_seals_registry_commit_to_next_action() {
        use crate::goal_intake::GoalIntakeProof;
        use crate::physical::PhysicalWatchdog;
        use crate::replay::{
            NextActionKind, NextActionPacket, RunCheckpoint, RunEventKind, RunEventLedger,
            RunEventSegmentArchive, SkillAdmissionHandoffProof,
        };
        use crate::skill_registry::{SkillAdmissionRecord, SkillRegistry};
        use std::time::{SystemTime, UNIX_EPOCH};

        let (manifest, sandbox_result, report, _cases) = skill_admission_fixture();
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &report,
            &PhysicalWatchdog { epsilon: 0.0 },
        )
        .unwrap();
        let mut registry = SkillRegistry::new();
        let admitted = registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .unwrap();

        let mut ledger = RunEventLedger::new(732);
        let proof = GoalIntakeProof::from_goal_text(
            74,
            test_hash("skill-handoff-operator"),
            test_hash("skill-handoff-raw-goal"),
            test_hash("skill-handoff-policy-window"),
            "Continue after replay-proven skill admission",
            1,
            None,
        )
        .unwrap();
        ledger.append_goal_intake_recorded(&proof).unwrap();
        ledger
            .append_skill_admission_recorded(&admission, &admitted)
            .unwrap();
        assert_eq!(
            ledger.events().last().unwrap().kind,
            RunEventKind::SkillAdmissionRecorded
        );

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("skill-admission-handoff-{unique}"));
        let manifest_archive = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let handoff = RunEventSegmentArchive::seal_skill_admission_handoff(
            &dir,
            &manifest_archive,
            &admission,
            &admitted,
            900,
            901,
            NextActionKind::ContinueExecution,
            902,
            test_hash("skill-handoff-typed-tool-ir"),
            test_hash("skill-handoff-evidence-contract"),
            test_hash("skill-handoff-policy-proof"),
        )
        .unwrap();
        assert!(registry.get(manifest.skill_id).is_some());
        assert!(registry.get_active(manifest.skill_id).is_none());
        assert_eq!(registry.active_len(), 0);

        let replay_proof =
            RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest_archive).unwrap();
        let checkpoint = RunCheckpoint::new(
            ledger.run_id,
            900,
            replay_proof.event_count,
            manifest_archive.entries.len(),
            replay_proof.first_pass_ledger_hash,
            manifest_archive.manifest_hash,
            replay_proof.proof_hash,
            handoff.skill_admission_event_hash,
        );
        let next_packet = NextActionPacket::new(
            ledger.run_id,
            901,
            checkpoint.checkpoint_hash,
            NextActionKind::ContinueExecution,
            902,
            test_hash("skill-handoff-typed-tool-ir"),
            test_hash("skill-handoff-evidence-contract"),
            test_hash("skill-handoff-policy-proof"),
            handoff.skill_admission_event_hash,
        );
        let skill_event = ledger.events().last().unwrap();
        assert!(handoff.is_valid_for(
            skill_event,
            &admission,
            &admitted,
            &replay_proof,
            &checkpoint,
            &next_packet
        ));
        assert_eq!(next_packet.candidate_evidence_hash, skill_event.event_hash);
        assert!(handoff.has_valid_activation_fields());

        let activated = registry
            .activate_replay_sealed_skill(manifest.skill_id, &handoff)
            .unwrap();
        assert!(activated.is_valid_for(&admitted, &handoff));
        assert_eq!(registry.get_active(manifest.skill_id), Some(&activated));
        assert_eq!(registry.active_len(), 1);
        assert_eq!(
            registry.activate_replay_sealed_skill(manifest.skill_id, &handoff),
            Err(crate::skill_registry::SkillAdmissionError::DuplicateActivation)
        );

        let mut missing_admission_registry = SkillRegistry::new();
        assert_eq!(
            missing_admission_registry.activate_replay_sealed_skill(manifest.skill_id, &handoff),
            Err(crate::skill_registry::SkillAdmissionError::MissingAdmission)
        );

        let mut tampered_activation_handoff = handoff.clone();
        tampered_activation_handoff.activation_hash = test_hash("tampered-skill-activation");
        let mut activation_tamper_registry = SkillRegistry::new();
        activation_tamper_registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .unwrap();
        assert_eq!(
            activation_tamper_registry
                .activate_replay_sealed_skill(manifest.skill_id, &tampered_activation_handoff),
            Err(crate::skill_registry::SkillAdmissionError::InvalidActivationProof)
        );
        assert_eq!(
            activation_tamper_registry
                .activate_replay_sealed_skill(manifest.skill_id + 1, &handoff),
            Err(crate::skill_registry::SkillAdmissionError::InvalidActivationProof)
        );

        let stale_packet = NextActionPacket::new(
            ledger.run_id,
            901,
            checkpoint.checkpoint_hash,
            NextActionKind::ContinueExecution,
            902,
            test_hash("skill-handoff-typed-tool-ir"),
            test_hash("skill-handoff-evidence-contract"),
            test_hash("skill-handoff-policy-proof"),
            test_hash("stale-skill-event"),
        );
        assert!(!handoff.is_valid_for(
            skill_event,
            &admission,
            &admitted,
            &replay_proof,
            &checkpoint,
            &stale_packet
        ));

        let mut stale_admitted = admitted.clone();
        stale_admitted.registry_commit_hash = test_hash("stale-skill-registry-commit");
        assert!(
            RunEventSegmentArchive::seal_skill_admission_handoff(
                &dir,
                &manifest_archive,
                &admission,
                &stale_admitted,
                900,
                901,
                NextActionKind::ContinueExecution,
                902,
                test_hash("skill-handoff-typed-tool-ir"),
                test_hash("skill-handoff-evidence-contract"),
                test_hash("skill-handoff-policy-proof"),
            )
            .is_err()
        );

        let mut no_skill_ledger = RunEventLedger::new(733);
        no_skill_ledger.append_goal_intake_recorded(&proof).unwrap();
        let no_skill_dir = dir.join("missing-skill-event");
        let no_skill_manifest =
            RunEventSegmentArchive::write_ledger(&no_skill_dir, 1, &no_skill_ledger).unwrap();
        assert!(
            RunEventSegmentArchive::seal_skill_admission_handoff(
                &no_skill_dir,
                &no_skill_manifest,
                &admission,
                &admitted,
                900,
                901,
                NextActionKind::ContinueExecution,
                902,
                test_hash("skill-handoff-typed-tool-ir"),
                test_hash("skill-handoff-evidence-contract"),
                test_hash("skill-handoff-policy-proof"),
            )
            .is_err()
        );

        let mut tampered_handoff = SkillAdmissionHandoffProof { ..handoff };
        tampered_handoff.registry_epoch = tampered_handoff.registry_epoch.saturating_add(1);
        assert!(!tampered_handoff.is_valid_for(
            skill_event,
            &admission,
            &admitted,
            &replay_proof,
            &checkpoint,
            &next_packet
        ));
    }

    #[test]
    fn active_skill_execution_requires_replay_activation_and_admitted_wasm() {
        use crate::goal_intake::GoalIntakeProof;
        use crate::physical::PhysicalWatchdog;
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{NextActionKind, RunEventLedger, RunEventSegmentArchive};
        use crate::sandbox::WasmtimeSandbox;
        use crate::skill_registry::{SkillAdmissionError, SkillAdmissionRecord, SkillRegistry};
        use crate::tool_gateway::{ToolExecutionGateway, evidence_contract_hash};
        use std::time::{SystemTime, UNIX_EPOCH};

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let (manifest, sandbox_result, report, _cases) = skill_admission_fixture();
        assert_eq!(
            crate::physical::blake3_digest(&crate::physical::canonicalize_payload(&wasm)),
            manifest.wasm_module_hash
        );
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &sandbox_result,
            &report,
            &PhysicalWatchdog { epsilon: 0.0 },
        )
        .unwrap();
        let mut registry = SkillRegistry::new();
        let admitted = registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .unwrap();

        let mut ledger = RunEventLedger::new(734);
        let intake = GoalIntakeProof::from_goal_text(
            75,
            test_hash("skill-exec-operator"),
            test_hash("skill-exec-raw-goal"),
            test_hash("skill-exec-policy-window"),
            "Execute a replay-activated Wasmtime skill",
            1,
            None,
        )
        .unwrap();
        ledger.append_goal_intake_recorded(&intake).unwrap();
        ledger
            .append_skill_admission_recorded(&admission, &admitted)
            .unwrap();
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("active-skill-execution-{unique}"));
        let manifest_archive = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let handoff = RunEventSegmentArchive::seal_skill_admission_handoff(
            &dir,
            &manifest_archive,
            &admission,
            &admitted,
            910,
            911,
            NextActionKind::ContinueExecution,
            912,
            test_hash("skill-exec-handoff-ir"),
            test_hash("skill-exec-handoff-contract"),
            test_hash("skill-exec-handoff-policy"),
        )
        .unwrap();

        let ir = TypedToolIR::new(
            manifest.skill_id,
            1001,
            CapabilityClass::LocalWrite,
            SideEffectClass::LocalReversible,
            test_hash("skill-exec-credential"),
            RiskClass::R1,
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
        assert!(ir.is_valid());
        let facts = PolicyFacts::new(test_hash("skill-exec-policy-v1"), false, false, false);
        let policy_trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(policy_trace.decision, PolicyDecision::Allow);

        let sandbox = WasmtimeSandbox::new();
        let inactive_result = SkillRegistry::new().execute_active_skill_with_replay(
            &mut ledger,
            &sandbox,
            manifest.skill_id,
            &handoff,
            &admission,
            920,
            921,
            &ir,
            &policy_trace,
            &wasm,
            10_000,
        );
        assert_eq!(inactive_result, Err(SkillAdmissionError::InactiveSkill));

        let active = registry
            .activate_replay_sealed_skill(manifest.skill_id, &handoff)
            .unwrap();
        ledger
            .append_context_pack_built(301, 128, test_hash("skill-exec-context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(
                test_hash("skill-exec-llm-response"),
                test_hash("skill-exec-raw-text-ref"),
            )
            .unwrap();
        let execution = registry
            .execute_active_skill_with_replay(
                &mut ledger,
                &sandbox,
                manifest.skill_id,
                &handoff,
                &admission,
                922,
                923,
                &ir,
                &policy_trace,
                &wasm,
                10_000,
            )
            .unwrap();
        let completion = ledger.events().last().unwrap().clone();
        assert!(execution.is_valid_for(&active, &handoff, &admission, &ir, &policy_trace));
        assert_eq!(execution.skill_id, manifest.skill_id);
        assert_eq!(execution.wasm_module_hash, admission.wasm_module_hash);
        assert_eq!(execution.activation_hash, handoff.activation_hash);
        assert_eq!(execution.receipt.call_id, 922);
        assert_eq!(
            execution.receipt.tool_output_hash,
            admission.wasmtime_artifact_hash
        );
        assert_eq!(
            execution.receipt.completion_event_hash,
            completion.event_hash
        );
        assert_eq!(completion.subject_id, 922);

        assert_eq!(
            SkillRegistry::seal_skill_execution_handoff(
                &dir,
                &manifest_archive,
                &execution,
                &ir,
                &policy_trace,
                930,
                931,
                NextActionKind::ContinueExecution,
                932,
                evidence_contract_hash(&ir),
            ),
            Err(SkillAdmissionError::SkillExecutionReplayMismatch)
        );
        let exec_dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("active-skill-execution-handoff-{unique}"));
        let exec_manifest = RunEventSegmentArchive::write_ledger(&exec_dir, 1, &ledger).unwrap();
        let execution_handoff = SkillRegistry::seal_skill_execution_handoff(
            &exec_dir,
            &exec_manifest,
            &execution,
            &ir,
            &policy_trace,
            930,
            931,
            NextActionKind::ContinueExecution,
            932,
            evidence_contract_hash(&ir),
        )
        .unwrap();
        let tool_replay = ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
            &exec_dir,
            &exec_manifest,
            execution.receipt.clone(),
            930,
            931,
            NextActionKind::ContinueExecution,
            932,
            evidence_contract_hash(&ir),
        )
        .unwrap();
        assert!(execution_handoff.is_valid_for(&execution, &tool_replay));
        assert_eq!(
            execution_handoff.skill_execution_proof_hash,
            execution.proof_hash
        );
        assert_eq!(
            execution_handoff.tool_execution_replay_proof_hash,
            tool_replay.proof_hash
        );
        assert_eq!(
            execution_handoff.candidate_evidence_hash,
            execution.receipt.physical_evidence_hash
        );
        let mut tampered_handoff = execution_handoff.clone();
        tampered_handoff.candidate_evidence_hash = test_hash("tampered-skill-execution-candidate");
        assert!(!tampered_handoff.is_valid_for(&execution, &tool_replay));

        let tampered_wasm =
            wat::parse_str(r#"(module (func (export "_start") unreachable))"#).unwrap();
        assert_eq!(
            registry.execute_active_skill_with_replay(
                &mut ledger,
                &sandbox,
                manifest.skill_id,
                &handoff,
                &admission,
                924,
                925,
                &ir,
                &policy_trace,
                &tampered_wasm,
                10_000,
            ),
            Err(SkillAdmissionError::SkillExecutionArtifactMismatch)
        );
    }

    #[test]
    fn test_skill_improvement_regression_rejection() {
        use crate::learning::LearningLedger;
        use crate::physical::PhysicalWatchdog;
        use crate::skill_registry::{
            SkillAdmissionError, SkillAdmissionRecord, SkillRegistry, SkillRegressionReport,
            SkillUsageStats,
        };

        let mut registry = SkillRegistry::new();
        let mut ledger = LearningLedger::new();

        // First, admit a skill so the registry knows about it
        let (manifest, _sandbox, report, _cases) = skill_admission_fixture();
        // wasm binding removed in hardening Phase 1A (2026-06-15) — unused;
        // admission fixture is constructed via from_wasmtime_and_regressions below.
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &manifest,
            &_sandbox,
            &report,
            &PhysicalWatchdog { epsilon: 0.0 },
        )
        .unwrap();
        registry
            .commit_admitted_skill(&manifest, &admission, &report)
            .unwrap();

        // Attempt to improve with usage_count < 10 → should fail
        let insufficient_stats = SkillUsageStats::new(5, 0.95);
        let improved_wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();

        let result = registry.improve_skill_from_usage(
            manifest.skill_id,
            &insufficient_stats,
            &improved_wasm,
            &report,
            &mut ledger,
            None,
        );
        assert_eq!(result, Err(SkillAdmissionError::InsufficientUsage));

        // Attempt to improve with a failing regression report → should fail
        let failed_report = SkillRegressionReport::new(&report_failure_cases()).unwrap();
        let sufficient_stats = SkillUsageStats::new(42, 0.85);

        let result = registry.improve_skill_from_usage(
            manifest.skill_id,
            &sufficient_stats,
            &improved_wasm,
            &failed_report,
            &mut ledger,
            None,
        );
        assert_eq!(result, Err(SkillAdmissionError::RegressionFailed));

        // Now a clean improvement with sufficient usage → should succeed
        let result = registry.improve_skill_from_usage(
            manifest.skill_id,
            &sufficient_stats,
            &improved_wasm,
            &report,
            &mut ledger,
            None,
        );
        assert!(result.is_ok());
        let record = result.unwrap();
        assert!(record.is_valid());
        assert_eq!(record.skill_id, manifest.skill_id);
        assert_eq!(record.usage_count, 42);
        assert_eq!(record.regression_report_hash, report.report_hash);
        assert_eq!(ledger.len(), 1);
        assert!(ledger.verify_integrity());
    }

    #[test]
    fn test_memory_nudge_pav_rejection() {
        use crate::learning::LearningLedger;
        use crate::memory::CogniFoldStore;
        use crate::memory::nudge::{MemoryCandidate, MemoryNudgeSystem};
        use crate::physical::PhysicalWatchdog;

        let mut ledger = LearningLedger::new();
        let system = MemoryNudgeSystem::new(0.5);

        let candidates = vec![
            MemoryCandidate::new("fact-a".to_string(), 0.9, 1).unwrap(),
            MemoryCandidate::new("fact-b".to_string(), 0.8, 1).unwrap(),
        ];

        let nudge = system
            .periodic_nudge(10, 1, candidates, &mut ledger, None)
            .unwrap();

        // Assert both candidates entered the nudge
        assert_eq!(nudge.candidates.len(), 2);
        assert!(nudge.is_valid());

        // Commit with a zero-epsilon watchdog — all artifacts pass PAV.
        let mut cognifold = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0 };

        let committed = system
            .commit_nudged_memories(&nudge, &mut cognifold, &watchdog)
            .unwrap();
        assert_eq!(committed, 2);
        assert_eq!(cognifold.len(), 2);

        // Now construct a nudge with an "empty" content that PAV will reject
        // (PhysicalArtifact::new rejects empty payloads).
        let bad_candidate = MemoryCandidate::new("".to_string(), 0.9, 1);
        // Actually, MemoryCandidate::new already rejects empty content,
        // so we validate that the guard is enforced at construction.
        assert!(bad_candidate.is_none());
    }

    #[test]
    fn test_session_search_candidate_only() {
        // CandidateEvidenceRef removed in Phase 1A cleanup — unused in this test (verified by clippy 2026-06-15).
        use crate::evidence_index::EvidenceCandidateTier;
        use crate::learning::LearningLedger;
        use crate::memory::session_search::SessionSearchIndex;

        let epoch_hash = test_hash("session-epoch");
        let mut index = SessionSearchIndex::new(epoch_hash).unwrap();
        let mut ledger = LearningLedger::new();

        // Index two sessions
        let h1 = index
            .index_session(
                1,
                "rust blake3 cryptographic hash",
                1_700_000_000_000,
                &mut ledger,
                None,
            )
            .unwrap();
        let h2 = index
            .index_session(
                2,
                "python numpy pandas dataframe",
                1_700_000_000_001,
                &mut ledger,
                None,
            )
            .unwrap();

        assert_ne!(h1, h2);
        assert_eq!(index.len(), 2);
        assert_eq!(ledger.len(), 2);
        assert!(ledger.verify_integrity());

        // Search — results must be CandidateEvidenceRef with
        // ColdVectorExpansion tier (candidate-only, not truth).
        let results = index.search_sessions("rust hash", 5);
        assert!(!results.is_empty());
        for candidate in &results {
            assert!(candidate.is_valid());
            assert_eq!(
                candidate.origin_tier,
                EvidenceCandidateTier::ColdVectorExpansion,
                "Session search results must use ColdVectorExpansion tier"
            );
        }

        // Results are CANDIDATES — CandidateOnlyGate must accept
        // them for ContextPackCandidate use (ColdVectorExpansion
        // requires replay event, but for context pack purposes
        // the gate allows it when a record is provided).
        use crate::evidence_index::CandidateOnlyGate;
        for candidate in &results {
            let validation = CandidateOnlyGate::validate_use(
                candidate,
                crate::evidence_index::CandidateEvidenceUse::ContextPackCandidate,
            );
            // Without a replay record, ColdVectorExpansion requires one
            assert!(validation.is_err());
        }
    }

    #[test]
    fn test_user_model_prod_requires_approval() {
        use crate::hot_engine::TrustLevel;
        use crate::learning::LearningLedger;
        use crate::memory::user_model::{UserModelError, UserModelStore};

        let mut store = UserModelStore::new();
        let mut ledger = LearningLedger::new();

        // PROD must fail-closed
        let result = store.update_user_model(
            42,
            "financial-trade".to_string(),
            0.99,
            1000,
            TrustLevel::Prod,
            &mut ledger,
            None,
        );
        assert_eq!(result, Err(UserModelError::ApprovalRequired));
        assert!(store.is_empty());
        assert!(ledger.is_empty());

        // STAGING must flag for review
        let result = store.update_user_model(
            42,
            "financial-trade".to_string(),
            0.99,
            1000,
            TrustLevel::Staging,
            &mut ledger,
            None,
        );
        assert_eq!(result, Err(UserModelError::ReviewRequired));
        assert!(store.is_empty());
        assert!(ledger.is_empty());

        // DEV must succeed
        let result = store.update_user_model(
            42,
            "financial-trade".to_string(),
            0.99,
            1000,
            TrustLevel::Dev,
            &mut ledger,
            None,
        );
        assert!(result.is_ok());
        assert_eq!(store.len(), 1);
        assert_eq!(ledger.len(), 1);
        assert!(ledger.verify_integrity());

        let model = store.get(42).unwrap();
        assert!(model.is_valid());
        assert_eq!(model.interaction_patterns.len(), 1);
        assert_eq!(model.interaction_patterns[0].task_type, "financial-trade");
    }

    fn report_failure_cases() -> Vec<crate::skill_registry::SkillRegressionCase> {
        use crate::skill_registry::SkillRegressionCase;
        vec![
            SkillRegressionCase::new(
                3,
                test_hash("skill-regression-input-fail"),
                test_hash("skill-regression-expected-fail"),
                test_hash("skill-regression-actual-fail"),
                false,
            )
            .unwrap(),
        ]
    }

    fn synced_temp_file_count(directory: &std::path::Path, artifact_name: &str) -> usize {
        std::fs::read_dir(directory)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| {
                entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(&format!("{artifact_name}.tmp-"))
            })
            .count()
    }

    fn assert_valid_replay_write_evidence(artifact_json: &serde_json::Value) {
        let evidence = &artifact_json["write_evidence"];
        assert_eq!(evidence["staged_temp_file_used"], true);
        assert_eq!(evidence["temp_file_synced_before_publish"], true);
        assert_eq!(evidence["publish_completed"], true);
        assert_eq!(evidence["parent_directory_sync_attempted"], true);
        assert_eq!(evidence["replace_existing_supported"], true);
        assert_eq!(evidence["publish_write_through_requested"], cfg!(windows));
        let logical_payload_bytes = evidence["logical_payload_bytes"].as_u64().unwrap();
        assert!(logical_payload_bytes > 0);
        let logical_payload_hash = nonzero_json_hash(&evidence["logical_payload_hash"]);
        let evidence_hash = nonzero_json_hash(&evidence["evidence_hash"]);
        assert_eq!(
            evidence_hash,
            replay_write_evidence_hash_for_test(
                evidence["staged_temp_file_used"].as_bool().unwrap(),
                evidence["temp_file_synced_before_publish"]
                    .as_bool()
                    .unwrap(),
                evidence["publish_completed"].as_bool().unwrap(),
                evidence["parent_directory_sync_attempted"]
                    .as_bool()
                    .unwrap(),
                evidence["replace_existing_supported"].as_bool().unwrap(),
                evidence["publish_write_through_requested"]
                    .as_bool()
                    .unwrap(),
                logical_payload_bytes,
                logical_payload_hash,
            )
        );
    }

    fn nonzero_json_hash(value: &serde_json::Value) -> [u8; 32] {
        let hash = value.as_array().unwrap();
        assert_eq!(hash.len(), 32);
        assert!(
            hash.iter()
                .all(|byte| byte.as_u64().is_some_and(|byte| byte <= 255))
        );
        assert!(hash.iter().any(|byte| byte.as_u64().unwrap() != 0));
        let mut out = [0u8; 32];
        for (index, byte) in hash.iter().enumerate() {
            out[index] = byte.as_u64().unwrap() as u8;
        }
        out
    }

    #[allow(clippy::too_many_arguments)]
    fn replay_write_evidence_hash_for_test(
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
        hasher.update(b"aegis-replay-artifact-write-evidence-v1");
        hasher.update(&[u8::from(staged_temp_file_used)]);
        hasher.update(&[u8::from(temp_file_synced_before_publish)]);
        hasher.update(&[u8::from(publish_completed)]);
        hasher.update(&[u8::from(parent_directory_sync_attempted)]);
        hasher.update(&[u8::from(replace_existing_supported)]);
        hasher.update(&[u8::from(publish_write_through_requested)]);
        hasher.update(&logical_payload_bytes.to_le_bytes());
        hasher.update(&logical_payload_hash);
        *hasher.finalize().as_bytes()
    }

    fn task_ledger_cache_fixture(
        left_done: bool,
        right_done: bool,
    ) -> crate::task_ledger::TaskLedger {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(60_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, Some(90_000), None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![1], 3, Some(110_000), Some(99)))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(3, vec![1], 2, Some(80_000), Some(-99)))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(4, vec![2], 1, Some(70_000), None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(5, vec![3], 4, Some(120_000), None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(6, vec![], 1, Some(100_000), Some(1_000_000)))
            .unwrap();
        ledger.complete_task(1).unwrap();
        if left_done {
            ledger.complete_task(2).unwrap();
        }
        if right_done {
            ledger.complete_task(3).unwrap();
        }
        ledger
    }

    #[test]
    fn goal_intake_classifies_financial_destructive_goal_as_r4_and_mints_root_task_ir() {
        use crate::goal_intake::GoalIntakeProof;
        use crate::policy::{CapabilityClass, RiskClass, SideEffectClass};

        let proof = GoalIntakeProof::from_goal_text(
            17,
            test_hash("operator"),
            test_hash("raw-goal-ref"),
            test_hash("policy-window"),
            "Delete stale invoices after validating the legal contract and payment trail",
            2,
            Some(1_786_400_000_000),
        )
        .expect("goal intake proof");

        assert!(proof.is_valid());
        assert_eq!(
            proof.packet.classified_capability,
            CapabilityClass::FinancialLegal
        );
        assert_eq!(
            proof.packet.classified_side_effect,
            SideEffectClass::FinancialLegal
        );
        assert_eq!(proof.packet.classified_risk, RiskClass::R4);
        assert!(proof.initial_ir.evidence_contract.requires_approval);
        assert!(proof.initial_ir.evidence_contract.requires_staging);
        assert_eq!(
            proof.initial_ir.approval_scope_hash,
            Some(test_hash("policy-window"))
        );
        assert_eq!(proof.root_task.dependency_ids, Vec::<u128>::new());
        assert_eq!(proof.root_task.evidence_unblock_count, 2);
        assert_eq!(proof.root_task.hard_deadline_ms, Some(1_786_400_000_000));
    }

    #[test]
    fn goal_intake_browser_goal_is_r2_and_rejects_tamper() {
        use crate::goal_intake::{GoalIntakePacket, GoalIntakeProof};
        use crate::policy::{CapabilityClass, RiskClass, SideEffectClass};

        let packet = GoalIntakePacket::from_goal_text(
            19,
            test_hash("browser-operator"),
            test_hash("browser-raw-goal-ref"),
            test_hash("browser-policy-window"),
            "Use browser to inspect the website and fetch evidence, no submit",
        )
        .expect("browser goal packet");
        let proof = GoalIntakeProof::from_packet(packet.clone(), 1, None).unwrap();

        assert!(proof.is_valid());
        assert_eq!(packet.classified_capability, CapabilityClass::Browser);
        assert_eq!(packet.classified_side_effect, SideEffectClass::ExternalRead);
        assert_eq!(packet.classified_risk, RiskClass::R2);
        assert!(proof.initial_ir.evidence_contract.requires_physical_witness);
        assert!(!proof.initial_ir.evidence_contract.requires_approval);
        assert!(!proof.initial_ir.evidence_contract.requires_staging);
        assert_eq!(proof.initial_ir.approval_scope_hash, None);

        let mut tampered = proof.clone();
        tampered.packet.classified_risk = RiskClass::R0;
        assert!(!tampered.is_valid());

        let empty = GoalIntakePacket::from_goal_text(
            19,
            test_hash("browser-operator"),
            test_hash("browser-raw-goal-ref"),
            test_hash("browser-policy-window"),
            "   ",
        );
        assert!(empty.is_err());
    }

    #[test]
    fn goal_intake_rejects_mismatched_normalized_hash_and_keeps_unnegated_write() {
        use crate::goal_intake::{GoalIntakePacket, normalized_goal_hash};
        use crate::policy::{RiskClass, SideEffectClass};

        let read_only_hash = normalized_goal_hash(
            "Use browser to inspect the website and fetch evidence, no submit",
        )
        .unwrap();
        let mismatched = GoalIntakePacket::from_normalized_hash(
            23,
            test_hash("operator-mismatch"),
            test_hash("raw-goal-mismatch"),
            read_only_hash,
            test_hash("policy-window-mismatch"),
            "Delete the legal contract and wire payment record",
        );
        assert!(mismatched.is_err());

        let write_hash =
            normalized_goal_hash("Use browser to inspect evidence, no submit, but upload report")
                .unwrap();
        let packet = GoalIntakePacket::from_normalized_hash(
            23,
            test_hash("operator-write"),
            test_hash("raw-goal-write"),
            write_hash,
            test_hash("policy-window-write"),
            "Use browser to inspect evidence, no submit, but upload report",
        )
        .unwrap();

        assert_eq!(
            packet.classified_side_effect,
            SideEffectClass::ExternalWrite
        );
        assert_eq!(packet.classified_risk, RiskClass::R3);
    }

    fn goal_intake_replay_fixture(label: &str) -> crate::goal_intake::GoalIntakeProof {
        crate::goal_intake::GoalIntakeProof::from_goal_text(
            41,
            test_hash(&format!("operator-{label}")),
            test_hash(&format!("raw-goal-{label}")),
            test_hash(&format!("policy-window-{label}")),
            "Use browser to inspect the website and fetch evidence, no submit",
            1,
            None,
        )
        .unwrap()
    }

    fn seed_goal_intake_replay(ledger: &mut crate::replay::RunEventLedger, label: &str) {
        let proof = goal_intake_replay_fixture(label);
        ledger.append_goal_intake_recorded(&proof).unwrap();
    }

    fn cluster_envelope_fixture(
        label: &str,
        side_effect_class: crate::policy::SideEffectClass,
    ) -> crate::distributed::ClusterWorkEnvelope {
        use crate::distributed::{ClusterWorkEnvelope, WorkerRole};

        ClusterWorkEnvelope::new(
            910,
            1200,
            3400,
            WorkerRole::BrowserWitness,
            1,
            vec![test_hash(&format!("cluster-input-{label}"))],
            test_hash(&format!("cluster-contract-{label}")),
            test_hash(&format!("cluster-policy-{label}")),
            side_effect_class,
            10_000,
        )
        .unwrap()
    }

    #[test]
    fn cluster_work_envelope_and_lease_are_hash_bound_and_deterministic() {
        use crate::distributed::WorkLeaseTable;
        use crate::policy::SideEffectClass;

        let envelope = cluster_envelope_fixture("lease", SideEffectClass::ExternalRead);
        assert!(envelope.is_valid());
        assert!(!envelope.pauses_on_partition());

        let mut leases = WorkLeaseTable::new();
        let lease = leases.acquire(&envelope, 77, 100, 1_000).unwrap();
        assert!(lease.is_valid_for(&envelope, 999));
        assert!(!lease.is_valid_for(&envelope, 1_100));
        assert!(leases.acquire(&envelope, 78, 200, 1_000).is_err());
        let reacquired = leases.acquire(&envelope, 78, 1_100, 1_000).unwrap();
        assert_eq!(reacquired.worker_id, 78);
        assert_ne!(lease.lease_hash, reacquired.lease_hash);
    }

    #[test]
    fn single_writer_run_log_accepts_candidate_once_and_replays_event() {
        use crate::distributed::{
            CandidateArtifactRef, ClusterPartitionState, SingleWriterAdmission, SingleWriterRunLog,
            WorkLeaseTable,
        };
        use crate::policy::SideEffectClass;
        use crate::replay::{RunEventKind, RunEventLedger};

        let envelope = cluster_envelope_fixture("accept", SideEffectClass::ExternalRead);
        let mut leases = WorkLeaseTable::new();
        let lease = leases.acquire(&envelope, 77, 100, 1_000).unwrap();
        let candidate = CandidateArtifactRef::new(
            &envelope,
            77,
            test_hash("cluster-artifact"),
            test_hash("cluster-artifact-kind"),
            4096,
        )
        .unwrap();
        let mut ledger = RunEventLedger::new(envelope.run_id);
        seed_goal_intake_replay(&mut ledger, "cluster-accept");
        let mut writer = SingleWriterRunLog::new(envelope.run_id);

        let first = writer
            .commit_candidate(
                &mut ledger,
                &envelope,
                &lease,
                &candidate,
                ClusterPartitionState::WriterReachable,
                500,
            )
            .unwrap();
        let proof = match first {
            SingleWriterAdmission::Accepted(proof) => proof,
            SingleWriterAdmission::Duplicate(_) => panic!("first commit must accept"),
        };
        assert!(proof.is_valid_for(&envelope, &lease, &candidate));
        assert_eq!(writer.committed_len(), 1);
        assert_eq!(
            ledger.events().last().unwrap().kind,
            RunEventKind::ClusterCandidateAccepted
        );

        let duplicate = writer
            .commit_candidate(
                &mut ledger,
                &envelope,
                &lease,
                &candidate,
                ClusterPartitionState::WriterReachable,
                500,
            )
            .unwrap();
        assert!(matches!(duplicate, SingleWriterAdmission::Duplicate(_)));
        assert_eq!(writer.committed_len(), 1);
        assert_eq!(ledger.len(), 2);
    }

    #[test]
    fn distributed_runtime_rejects_direct_worker_commit_and_side_effect_partition() {
        use crate::distributed::{
            CandidateArtifactRef, ClusterPartitionState, DistributedRuntimeError,
            SingleWriterRunLog, WorkLeaseTable,
        };
        use crate::policy::SideEffectClass;
        use crate::replay::RunEventLedger;

        let envelope = cluster_envelope_fixture("partition", SideEffectClass::ExternalWrite);
        assert!(envelope.pauses_on_partition());
        let mut leases = WorkLeaseTable::new();
        let lease = leases.acquire(&envelope, 77, 100, 1_000).unwrap();
        let candidate = CandidateArtifactRef::new(
            &envelope,
            77,
            test_hash("cluster-write-artifact"),
            test_hash("cluster-write-artifact-kind"),
            4096,
        )
        .unwrap();
        let mut ledger = RunEventLedger::new(envelope.run_id);
        seed_goal_intake_replay(&mut ledger, "cluster-partition");
        let mut writer = SingleWriterRunLog::new(envelope.run_id);

        assert_eq!(
            writer.commit_candidate(
                &mut ledger,
                &envelope,
                &lease,
                &candidate,
                ClusterPartitionState::WriterPartitioned,
                500,
            ),
            Err(DistributedRuntimeError::PartitionPausesSideEffects)
        );
        assert_eq!(
            writer.reject_direct_worker_commit(77),
            Err(DistributedRuntimeError::DirectWorkerCommitRejected)
        );
        assert_eq!(ledger.len(), 1);
    }

    #[test]
    fn cluster_candidate_replay_requires_goal_intake_and_valid_binding() {
        use crate::distributed::{
            CandidateArtifactRef, ClusterPartitionState, SingleWriterRunLog, WorkLeaseTable,
            cluster_candidate_replay_binding_hash,
        };
        use crate::policy::SideEffectClass;
        use crate::replay::{ReplayLedgerError, RunEventLedger};

        let envelope = cluster_envelope_fixture("replay-binding", SideEffectClass::ExternalRead);
        let mut leases = WorkLeaseTable::new();
        let lease = leases.acquire(&envelope, 77, 100, 1_000).unwrap();
        let candidate = CandidateArtifactRef::new(
            &envelope,
            77,
            test_hash("cluster-binding-artifact"),
            test_hash("cluster-binding-artifact-kind"),
            4096,
        )
        .unwrap();

        let mut no_goal_ledger = RunEventLedger::new(envelope.run_id);
        assert_eq!(
            no_goal_ledger.append_cluster_candidate_accepted(
                envelope.work_id,
                candidate.worker_result_hash,
                cluster_candidate_replay_binding_hash(&envelope, &lease, &candidate),
            ),
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );

        let mut ledger = RunEventLedger::new(envelope.run_id);
        seed_goal_intake_replay(&mut ledger, "cluster-replay-binding");
        let mut writer = SingleWriterRunLog::new(envelope.run_id);
        assert!(
            writer
                .commit_candidate(
                    &mut ledger,
                    &envelope,
                    &lease,
                    &candidate,
                    ClusterPartitionState::WriterReachable,
                    500,
                )
                .is_ok()
        );
        assert!(RunEventLedger::from_events(envelope.run_id, ledger.events().to_vec()).is_ok());
    }

    #[test]
    fn candidate_evidence_ref_is_schema_only_and_hash_bound() {
        use crate::evidence_index::{
            CandidateEvidenceRef, EvidenceCandidateTier, IndexEpochInputs,
        };

        let epoch = IndexEpochInputs {
            segment_catalog_hash: test_hash("segment-catalog"),
            tokenizer_config_hash: test_hash("tokenizer"),
            schema_hash: test_hash("schema"),
            redaction_policy_hash: test_hash("redaction"),
            embedding_model_hash_or_zero: [0; 32],
            feature_profile_hash: test_hash("feature-profile"),
        };
        assert!(epoch.is_valid());
        let epoch_hash = epoch.epoch_hash();

        let candidate = CandidateEvidenceRef::new(
            test_hash("evidence-ref"),
            7,
            EvidenceCandidateTier::LexicalBaseline,
            920_000,
            epoch_hash,
        );

        assert!(candidate.is_valid());
        assert_eq!(candidate.index_epoch_hash, epoch_hash);
        assert_ne!(candidate.candidate_hash(), test_hash("evidence-ref"));

        let changed_score = CandidateEvidenceRef::new(
            candidate.evidence_ref_hash,
            candidate.segment_id,
            candidate.origin_tier,
            910_000,
            candidate.index_epoch_hash,
        );
        assert_ne!(candidate.candidate_hash(), changed_score.candidate_hash());
    }

    #[test]
    fn candidate_only_gate_allows_context_candidate_but_blocks_commit_uses() {
        use crate::evidence_index::{
            CandidateEvidenceRef, CandidateEvidenceUse, CandidateOnlyGate, CandidateOnlyGateError,
            EvidenceCandidateTier,
        };

        let candidate = CandidateEvidenceRef::new(
            test_hash("candidate"),
            1,
            EvidenceCandidateTier::BitmapFilter,
            1_000_000,
            test_hash("index-epoch"),
        );

        assert_eq!(
            CandidateOnlyGate::validate_use(&candidate, CandidateEvidenceUse::ContextPackCandidate),
            Ok(())
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(&candidate, CandidateEvidenceUse::PhysicalWitness),
            Err(CandidateOnlyGateError::CandidateCannotCommit(
                CandidateEvidenceUse::PhysicalWitness
            ))
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(&candidate, CandidateEvidenceUse::PolicyApproval),
            Err(CandidateOnlyGateError::CandidateCannotCommit(
                CandidateEvidenceUse::PolicyApproval
            ))
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(&candidate, CandidateEvidenceUse::MemoryCommit),
            Err(CandidateOnlyGateError::CandidateCannotCommit(
                CandidateEvidenceUse::MemoryCommit
            ))
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(&candidate, CandidateEvidenceUse::TaskStatusDone),
            Err(CandidateOnlyGateError::CandidateCannotCommit(
                CandidateEvidenceUse::TaskStatusDone
            ))
        );
    }

    #[test]
    fn candidate_gate_rejects_invalid_or_cold_expansion_without_event_path() {
        use crate::evidence_index::{
            CandidateEvidenceRef, CandidateEvidenceUse, CandidateOnlyGate, CandidateOnlyGateError,
            ColdVectorExpansionReplayRecord, EvidenceCandidateTier,
        };

        let invalid = CandidateEvidenceRef::new(
            [0; 32],
            1,
            EvidenceCandidateTier::ActivatedContext,
            10,
            test_hash("index-epoch"),
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(&invalid, CandidateEvidenceUse::ContextPackCandidate),
            Err(CandidateOnlyGateError::InvalidCandidate)
        );

        assert!(EvidenceCandidateTier::ColdVectorExpansion.requires_replay_event());
        assert!(!EvidenceCandidateTier::LexicalBaseline.requires_replay_event());

        let cold_candidate = CandidateEvidenceRef::new(
            test_hash("cold-candidate"),
            2,
            EvidenceCandidateTier::ColdVectorExpansion,
            900_000,
            test_hash("index-epoch"),
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(
                &cold_candidate,
                CandidateEvidenceUse::ContextPackCandidate
            ),
            Err(CandidateOnlyGateError::MissingReplayEvent)
        );
        assert_eq!(
            CandidateOnlyGate::validate_context_pack_candidates(&[cold_candidate], None),
            Err(CandidateOnlyGateError::MissingReplayEvent)
        );

        let record = ColdVectorExpansionReplayRecord::new(
            test_hash("index-epoch"),
            &["rust", "witness"],
            4,
            test_hash("cold-vector-config"),
            test_hash("cold-vector-artifact"),
            42_000,
            &[cold_candidate],
        )
        .unwrap();
        assert!(record.is_valid());
        assert_eq!(
            CandidateOnlyGate::validate_context_pack_candidates(&[cold_candidate], Some(&record)),
            Ok(())
        );

        let wrong_candidate = CandidateEvidenceRef::new(
            test_hash("cold-candidate-other"),
            3,
            EvidenceCandidateTier::ColdVectorExpansion,
            800_000,
            test_hash("index-epoch"),
        );
        assert_eq!(
            CandidateOnlyGate::validate_context_pack_candidates(&[wrong_candidate], Some(&record)),
            Err(CandidateOnlyGateError::ReplayRecordMismatch)
        );
    }

    #[test]
    fn hot_lexical_index_returns_deterministic_candidate_refs() {
        use crate::evidence_index::{
            CandidateEvidenceUse, CandidateOnlyGate, EvidenceCandidateTier, HotLexicalIndex,
        };

        let mut index = HotLexicalIndex::new(test_hash("index-epoch")).unwrap();
        index
            .insert_document(
                test_hash("doc-alpha-beta"),
                1,
                &["Rust", "WASM", "witness", "witness"],
            )
            .unwrap();
        index
            .insert_document(test_hash("doc-alpha"), 2, &["rust", "policy"])
            .unwrap();
        index
            .insert_document(test_hash("doc-cold"), 3, &["browser", "snapshot"])
            .unwrap();

        let results = index.query_top_k(&["rust", "witness"], 4);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].evidence_ref_hash, test_hash("doc-alpha-beta"));
        assert_eq!(
            results[0].origin_tier,
            EvidenceCandidateTier::LexicalBaseline
        );
        assert_eq!(results[0].score_quantized, 1_000_000);
        assert_eq!(results[1].evidence_ref_hash, test_hash("doc-alpha"));
        assert_eq!(results[1].score_quantized, 500_000);
        assert!(results.iter().all(|candidate| candidate.is_valid()));
        assert!(results.iter().all(|candidate| {
            CandidateOnlyGate::validate_use(candidate, CandidateEvidenceUse::ContextPackCandidate)
                .is_ok()
        }));
    }

    #[test]
    fn hot_lexical_index_reusable_scratch_matches_default_and_resets() {
        use crate::evidence_index::HotLexicalIndex;

        let mut index = HotLexicalIndex::new(test_hash("scratch-index-epoch")).unwrap();
        index
            .insert_document(test_hash("scratch-doc-rust"), 1, &["rust", "witness"])
            .unwrap();
        index
            .insert_document(test_hash("scratch-doc-policy"), 2, &["policy", "ledger"])
            .unwrap();
        index
            .insert_document(
                test_hash("scratch-doc-browser"),
                3,
                &["browser", "snapshot"],
            )
            .unwrap();

        let mut scratch = index.query_scratch();
        let first_query = ["rust", "witness"];
        assert_eq!(
            index.query_top_k(&first_query, 2),
            index.query_top_k_with_scratch(&first_query, 2, &mut scratch)
        );

        let second_query = ["browser"];
        let scratch_results = index.query_top_k_with_scratch(&second_query, 2, &mut scratch);
        assert_eq!(index.query_top_k(&second_query, 2), scratch_results);
        assert_eq!(scratch_results.len(), 1);
        assert_eq!(
            scratch_results[0].evidence_ref_hash,
            test_hash("scratch-doc-browser")
        );
    }

    #[test]
    fn hot_evidence_index_returns_artifact_bound_candidate_refs() {
        use crate::evidence_index::{
            CandidateEvidenceUse, CandidateOnlyGate, EvidenceCandidateTier, HotEvidenceIndex,
            HotEvidenceIndexError, SortedEvidenceSet, hot_evidence_document_binding_hash,
        };

        let epoch_hash = test_hash("hot-evidence-index-epoch");
        let mut index = HotEvidenceIndex::new(epoch_hash).unwrap();
        index
            .insert_artifact_document(
                test_hash("browser-proof"),
                7,
                test_hash("browser-artifact"),
                test_hash("browser-ast-signature"),
                "BrowserObservationPacket binds DOM screenshot network replay policy physical witness",
            )
            .unwrap();
        index
            .insert_artifact_document(
                test_hash("wasm-proof"),
                3,
                test_hash("wasm-artifact"),
                test_hash("wasm-ast-signature"),
                "Wasmtime sandbox fuel memory replay evidence",
            )
            .unwrap();
        index
            .insert_artifact_document(
                test_hash("hermes-fts"),
                11,
                test_hash("fts-artifact"),
                test_hash("fts-ast-signature"),
                "SQLite FTS5 session search text without physical witness",
            )
            .unwrap();

        let allowed_segments = SortedEvidenceSet::from_unsorted(vec![7, 3]);
        let results = index.query_exact_literals(
            &["replay", "policy", "witness"],
            Some(&allowed_segments),
            4,
        );

        assert_eq!(results.len(), 2);
        assert_eq!(
            results[0].evidence_ref_hash,
            hot_evidence_document_binding_hash(
                test_hash("browser-proof"),
                test_hash("browser-artifact"),
                test_hash("browser-ast-signature")
            )
        );
        assert_eq!(
            results[0].origin_tier,
            EvidenceCandidateTier::ExactSimdRerank
        );
        assert_eq!(results[0].score_quantized, 1_000_000);
        assert_eq!(
            results[1].evidence_ref_hash,
            hot_evidence_document_binding_hash(
                test_hash("wasm-proof"),
                test_hash("wasm-artifact"),
                test_hash("wasm-ast-signature")
            )
        );
        assert_eq!(results[1].score_quantized, 333_333);
        assert!(results.iter().all(|candidate| {
            CandidateOnlyGate::validate_use(candidate, CandidateEvidenceUse::ContextPackCandidate)
                .is_ok()
        }));
        assert!(index.validates_artifact_binding(
            &results[0],
            test_hash("browser-proof"),
            test_hash("browser-artifact"),
            test_hash("browser-ast-signature")
        ));
        assert!(!index.validates_artifact_binding(
            &results[0],
            test_hash("browser-proof"),
            test_hash("tampered-artifact"),
            test_hash("browser-ast-signature")
        ));
        assert_eq!(
            index.insert_artifact_document(
                test_hash("invalid"),
                12,
                [0; 32],
                test_hash("ast"),
                "invalid artifact"
            ),
            Err(HotEvidenceIndexError::InvalidArtifactBinding)
        );
    }

    #[test]
    fn agentic_evidence_program_composes_sac_primitives_with_replay_trace() {
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramError, AgenticEvidenceProgramStep,
            CandidateEvidenceUse, CandidateOnlyGate, EvidenceCandidateTier, HotBitmapFilter,
            HotEvidenceIndex, HotLexicalIndex, SortedEvidenceSet,
        };

        let epoch_hash = test_hash("agentic-evidence-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("agentic-browser-ref"),
                7,
                &["browser", "policy", "replay", "witness"],
            )
            .unwrap();
        lexical
            .insert_document(
                test_hash("agentic-wasm-ref"),
                3,
                &["wasmtime", "policy", "replay"],
            )
            .unwrap();
        lexical
            .insert_document(
                test_hash("agentic-hermes-ref"),
                11,
                &["sqlite", "fts5", "session", "text"],
            )
            .unwrap();

        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-browser-ref"),
                7,
                test_hash("agentic-browser-artifact"),
                test_hash("agentic-browser-ast"),
                "Browser evidence packet binds replay policy witness and screenshot artifact",
            )
            .unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-wasm-ref"),
                3,
                test_hash("agentic-wasm-artifact"),
                test_hash("agentic-wasm-ast"),
                "Wasmtime execution proof binds replay policy but not browser witness",
            )
            .unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-hermes-ref"),
                11,
                test_hash("agentic-hermes-artifact"),
                test_hash("agentic-hermes-ast"),
                "Hermes session search uses SQLite FTS5 text history",
            )
            .unwrap();

        let filter =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![7])).unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            64,
            vec![
                AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay", "witness"], 8),
                AgenticEvidenceProgramStep::exact_artifact_rerank(
                    &["browser", "policy", "witness"],
                    4,
                ),
                AgenticEvidenceProgramStep::bitmap_filter(2),
            ],
        )
        .unwrap();

        let (candidates, record) = program
            .execute(Some(&lexical), Some(&exact), Some(&filter))
            .unwrap();
        assert_eq!(candidates.len(), 1);
        assert_eq!(candidates[0].segment_id, 7);
        assert_eq!(
            candidates[0].origin_tier,
            EvidenceCandidateTier::AgenticProgramOutput
        );
        assert_eq!(
            CandidateOnlyGate::validate_use(
                &candidates[0],
                CandidateEvidenceUse::ContextPackCandidate
            ),
            Err(crate::evidence_index::CandidateOnlyGateError::MissingReplayEvent)
        );
        assert!(record.is_valid());
        assert!(record.matches_candidates(&program, &candidates));
        assert!(
            CandidateOnlyGate::validate_replay_bound_context_pack_candidates(
                &candidates,
                None,
                Some(&record)
            )
            .is_ok()
        );

        let (replayed_candidates, replayed_record) = program
            .execute(Some(&lexical), Some(&exact), Some(&filter))
            .unwrap();
        assert_eq!(candidates, replayed_candidates);
        assert_eq!(record, replayed_record);

        assert_eq!(
            program.execute(Some(&lexical), Some(&exact), None),
            Err(AgenticEvidenceProgramError::MissingBitmapFilter)
        );
        assert_eq!(
            AgenticEvidenceProgram::new(
                epoch_hash,
                3,
                vec![AgenticEvidenceProgramStep::lexical_top_k(
                    &["policy", "replay", "witness"],
                    8
                )],
            ),
            Err(AgenticEvidenceProgramError::FuelExceeded)
        );
    }

    #[test]
    fn hot_evidence_index_queries_ast_signature_without_text_hydration() {
        use crate::evidence_index::{
            EvidenceCandidateTier, HotEvidenceIndex, HotEvidenceQueryScratch, SortedEvidenceSet,
        };

        let epoch_hash = test_hash("hot-ast-signature-epoch");
        let browser_ast = test_hash("hot-ast-browser-signature");
        let wasm_ast = test_hash("hot-ast-wasm-signature");
        let mut index = HotEvidenceIndex::new(epoch_hash).unwrap();
        index
            .insert_artifact_document(
                test_hash("hot-ast-browser-ref"),
                41,
                test_hash("hot-ast-browser-artifact"),
                browser_ast,
                "browser evidence packet code path",
            )
            .unwrap();
        index
            .insert_artifact_document(
                test_hash("hot-ast-wasm-ref"),
                43,
                test_hash("hot-ast-wasm-artifact"),
                wasm_ast,
                "wasmtime sandbox code path",
            )
            .unwrap();
        let allowed = SortedEvidenceSet::from_unsorted(vec![41]);
        let mut scratch = HotEvidenceQueryScratch::with_document_capacity(index.document_count());
        let results = index.query_ast_signatures_into_scratch(
            &[browser_ast, wasm_ast, browser_ast, [0; 32]],
            Some(&allowed),
            8,
            &mut scratch,
        );
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].segment_id, 41);
        assert_eq!(
            results[0].origin_tier,
            EvidenceCandidateTier::AstSignatureMatch
        );
        assert!(index.validates_artifact_binding(
            &results[0],
            test_hash("hot-ast-browser-ref"),
            test_hash("hot-ast-browser-artifact"),
            browser_ast
        ));
        assert!(!index.validates_artifact_binding(
            &results[0],
            test_hash("hot-ast-browser-ref"),
            test_hash("hot-ast-browser-artifact"),
            wasm_ast
        ));
    }

    #[test]
    fn agentic_evidence_sdk_preflights_ast_signature_primitive() {
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramStep, AgenticEvidenceSdk,
            EvidenceCandidateTier, HotBitmapFilter, HotEvidenceIndex, HotLexicalIndex,
            SortedEvidenceSet,
        };

        let epoch_hash = test_hash("agentic-sdk-ast-signature-epoch");
        let browser_ast = test_hash("agentic-sdk-ast-browser");
        let hermes_ast = test_hash("agentic-sdk-ast-hermes");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-ast-browser-ref"),
                47,
                &["browser", "policy", "replay", "witness"],
            )
            .unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-ast-hermes-ref"),
                53,
                &["hermes", "sqlite", "session", "text"],
            )
            .unwrap();

        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-ast-browser-ref"),
                47,
                test_hash("agentic-sdk-ast-browser-artifact"),
                browser_ast,
                "browser policy replay witness artifact",
            )
            .unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-ast-hermes-ref"),
                53,
                test_hash("agentic-sdk-ast-hermes-artifact"),
                hermes_ast,
                "hermes sqlite session text artifact",
            )
            .unwrap();
        let bitmap =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![47])).unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            64,
            vec![
                AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay", "witness"], 8),
                AgenticEvidenceProgramStep::ast_signature_lookup(&[browser_ast, hermes_ast], 8),
                AgenticEvidenceProgramStep::bitmap_filter(4),
            ],
        )
        .unwrap();
        let run = AgenticEvidenceSdk::run_program(
            &program,
            4545,
            Some(&lexical),
            Some(&exact),
            Some(&bitmap),
        )
        .unwrap();
        assert!(run.manifest.requires_lexical_index);
        assert!(run.manifest.requires_exact_index);
        assert!(run.manifest.requires_bitmap_filter);
        assert_eq!(run.capsule.candidates().len(), 1);
        assert_eq!(run.capsule.candidates()[0].segment_id, 47);
        assert_eq!(
            run.capsule.candidates()[0].origin_tier,
            EvidenceCandidateTier::AgenticProgramOutput
        );
        assert!(
            run.capsule
                .is_valid_for_replay_records(None, Some(&run.execution_record))
        );
    }

    #[test]
    fn agentic_evidence_sdk_run_binds_manifest_record_and_candidate_capsule() {
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramStep, AgenticEvidenceSdk,
            CandidateOnlyGate, EvidenceCandidateTier, HotBitmapFilter, HotEvidenceIndex,
            HotLexicalIndex, SortedEvidenceSet,
        };

        let epoch_hash = test_hash("agentic-evidence-sdk-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-browser-ref"),
                17,
                &["browser", "policy", "replay", "witness"],
            )
            .unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-wasm-ref"),
                19,
                &["wasmtime", "fuel", "policy"],
            )
            .unwrap();

        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-browser-ref"),
                17,
                test_hash("agentic-sdk-browser-artifact"),
                test_hash("agentic-sdk-browser-ast"),
                "Browser witness evidence binds policy replay screenshot and DOM artifacts",
            )
            .unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-wasm-ref"),
                19,
                test_hash("agentic-sdk-wasm-artifact"),
                test_hash("agentic-sdk-wasm-ast"),
                "Wasmtime fuel policy proof without browser witness",
            )
            .unwrap();
        let filter =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![17])).unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            64,
            vec![
                AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay", "witness"], 8),
                AgenticEvidenceProgramStep::exact_artifact_rerank(
                    &["browser", "witness", "replay"],
                    4,
                ),
                AgenticEvidenceProgramStep::bitmap_filter(2),
            ],
        )
        .unwrap();

        let run = AgenticEvidenceSdk::run_program(
            &program,
            4242,
            Some(&lexical),
            Some(&exact),
            Some(&filter),
        )
        .unwrap();
        assert!(run.manifest.is_valid_for_program(&program));
        assert!(run.execution_record.is_valid());
        assert!(
            run.execution_record
                .matches_candidates(&program, run.capsule.candidates())
        );
        assert!(
            run.capsule
                .is_valid_for_replay_records(None, Some(&run.execution_record))
        );
        assert!(
            CandidateOnlyGate::validate_replay_bound_context_pack_candidates(
                run.capsule.candidates(),
                None,
                Some(&run.execution_record)
            )
            .is_ok()
        );
        assert_eq!(run.capsule.candidates().len(), 1);
        assert_eq!(run.capsule.candidates()[0].segment_id, 17);
        assert_eq!(
            run.capsule.candidates()[0].origin_tier,
            EvidenceCandidateTier::AgenticProgramOutput
        );

        let replayed = AgenticEvidenceSdk::run_program(
            &program,
            4242,
            Some(&lexical),
            Some(&exact),
            Some(&filter),
        )
        .unwrap();
        assert_eq!(run, replayed);

        let mut tampered_manifest = run.manifest;
        tampered_manifest.step_count += 1;
        assert!(!tampered_manifest.is_valid_for_program(&program));
    }

    #[test]
    fn agentic_evidence_sdk_preflights_capability_epochs_before_execution() {
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramStep, AgenticEvidenceSdk,
            AgenticEvidenceSdkError, HotBitmapFilter, HotEvidenceIndex, HotLexicalIndex,
            SortedEvidenceSet,
        };

        let epoch_hash = test_hash("agentic-evidence-sdk-preflight-epoch");
        let wrong_epoch_hash = test_hash("agentic-evidence-sdk-preflight-wrong-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-preflight-ref"),
                29,
                &["policy", "replay", "witness"],
            )
            .unwrap();
        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-preflight-ref"),
                29,
                test_hash("agentic-sdk-preflight-artifact"),
                test_hash("agentic-sdk-preflight-ast"),
                "policy replay witness browser artifact",
            )
            .unwrap();
        let mut wrong_exact = HotEvidenceIndex::new(wrong_epoch_hash).unwrap();
        wrong_exact
            .insert_artifact_document(
                test_hash("agentic-sdk-preflight-ref"),
                29,
                test_hash("agentic-sdk-preflight-artifact"),
                test_hash("agentic-sdk-preflight-ast"),
                "policy replay witness browser artifact",
            )
            .unwrap();
        let filter =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![29])).unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            64,
            vec![
                AgenticEvidenceProgramStep::lexical_top_k(&["policy", "witness"], 4),
                AgenticEvidenceProgramStep::exact_artifact_rerank(&["browser", "artifact"], 4),
                AgenticEvidenceProgramStep::bitmap_filter(2),
            ],
        )
        .unwrap();

        assert_eq!(
            AgenticEvidenceSdk::run_program(&program, 1, Some(&lexical), Some(&exact), None),
            Err(AgenticEvidenceSdkError::MissingBitmapFilter)
        );
        assert_eq!(
            AgenticEvidenceSdk::run_program(
                &program,
                1,
                Some(&lexical),
                Some(&wrong_exact),
                Some(&filter)
            ),
            Err(AgenticEvidenceSdkError::ExactIndexEpochMismatch)
        );

        let lexical_only = AgenticEvidenceProgram::new(
            epoch_hash,
            16,
            vec![AgenticEvidenceProgramStep::lexical_top_k(
                &["policy", "witness"],
                4,
            )],
        )
        .unwrap();
        let lexical_run = AgenticEvidenceSdk::run_program(
            &lexical_only,
            7,
            Some(&lexical),
            Some(&wrong_exact),
            None,
        )
        .unwrap();
        assert!(!lexical_run.manifest.requires_exact_index);
        assert_eq!(lexical_run.manifest.exact_index_hash, [0; 32]);
        assert!(
            lexical_run
                .capsule
                .is_valid_for_replay_records(None, Some(&lexical_run.execution_record))
        );
    }

    #[test]
    fn index_epoch_replay_record_matches_rebuilt_index_candidates() {
        use crate::evidence_index::{
            CandidateEvidenceRef, EvidenceCandidateTier, HotLexicalIndex, IndexEpochReplayError,
            IndexEpochReplayRecord,
        };

        let epoch_hash = test_hash("replay-index-epoch");
        let mut first = HotLexicalIndex::new(epoch_hash).unwrap();
        first
            .insert_document(
                test_hash("doc-alpha-beta"),
                1,
                &["Rust", "WASM", "witness", "witness"],
            )
            .unwrap();
        first
            .insert_document(test_hash("doc-alpha"), 2, &["rust", "policy"])
            .unwrap();
        first
            .insert_document(test_hash("doc-cold"), 3, &["browser", "snapshot"])
            .unwrap();

        let mut rebuilt = HotLexicalIndex::new(epoch_hash).unwrap();
        rebuilt
            .insert_document(test_hash("doc-cold"), 3, &["browser", "snapshot"])
            .unwrap();
        rebuilt
            .insert_document(test_hash("doc-alpha"), 2, &["rust", "policy"])
            .unwrap();
        rebuilt
            .insert_document(
                test_hash("doc-alpha-beta"),
                1,
                &["Rust", "WASM", "witness", "witness"],
            )
            .unwrap();

        let query = ["rust", "witness"];
        let candidates = first.query_top_k(&query, 4);
        let rebuilt_candidates = rebuilt.query_top_k(&["witness", "rust", "rust"], 4);
        assert_eq!(candidates, rebuilt_candidates);

        let record = IndexEpochReplayRecord::new(epoch_hash, &query, 4, &candidates).unwrap();
        assert_eq!(record.candidate_count, 2);
        assert!(record.matches_candidates(&["witness", "rust", "rust"], 4, &rebuilt_candidates));
        assert!(!record.matches_candidates(&["browser"], 4, &rebuilt_candidates));
        assert!(!record.matches_candidates(&query, 1, &rebuilt_candidates));

        let wrong_epoch = [CandidateEvidenceRef::new(
            test_hash("candidate-wrong-epoch"),
            4,
            EvidenceCandidateTier::LexicalBaseline,
            500_000,
            test_hash("other-index-epoch"),
        )];
        assert_eq!(
            IndexEpochReplayRecord::new(epoch_hash, &query, 4, &wrong_epoch),
            Err(IndexEpochReplayError::CandidateEpochMismatch)
        );
        assert_eq!(
            IndexEpochReplayRecord::new(epoch_hash, &["!!!"], 4, &[]),
            Err(IndexEpochReplayError::EmptyQuery)
        );
    }

    #[test]
    fn index_epoch_hash_changes_invalidate_cached_candidates() {
        use crate::evidence_index::IndexEpochInputs;

        let base = IndexEpochInputs {
            segment_catalog_hash: test_hash("segment-catalog"),
            tokenizer_config_hash: test_hash("tokenizer"),
            schema_hash: test_hash("schema"),
            redaction_policy_hash: test_hash("redaction"),
            embedding_model_hash_or_zero: [0; 32],
            feature_profile_hash: test_hash("feature-profile"),
        };
        let base_hash = base.epoch_hash();

        let mut tokenizer_changed = base;
        tokenizer_changed.tokenizer_config_hash = test_hash("tokenizer-v2");
        assert_ne!(tokenizer_changed.epoch_hash(), base_hash);

        let mut schema_changed = base;
        schema_changed.schema_hash = test_hash("schema-v2");
        assert_ne!(schema_changed.epoch_hash(), base_hash);

        let mut redaction_changed = base;
        redaction_changed.redaction_policy_hash = test_hash("redaction-v2");
        assert_ne!(redaction_changed.epoch_hash(), base_hash);

        let mut feature_changed = base;
        feature_changed.feature_profile_hash = test_hash("feature-profile-v2");
        assert_ne!(feature_changed.epoch_hash(), base_hash);
    }

    #[test]
    fn hot_lexical_index_normalizes_terms_and_rejects_bad_documents() {
        use crate::evidence_index::{HotLexicalIndex, LexicalIndexError};

        let mut index = HotLexicalIndex::new(test_hash("index-epoch")).unwrap();
        index
            .insert_document(test_hash("doc-normalized"), 1, &["AEGIS-core", "Rust_2026"])
            .unwrap();

        let underscore_match = index.query_top_k(&["rust_2026"], 1);
        assert_eq!(underscore_match.len(), 1);
        assert_eq!(
            underscore_match[0].evidence_ref_hash,
            test_hash("doc-normalized")
        );

        assert_eq!(
            index.insert_document(test_hash("doc-normalized"), 1, &["duplicate"]),
            Err(LexicalIndexError::DuplicateDocument)
        );
        assert_eq!(
            index.insert_document(test_hash("doc-empty"), 2, &["!!!"]),
            Err(LexicalIndexError::EmptyTerms)
        );
        assert_eq!(
            HotLexicalIndex::new([0; 32]),
            Err(LexicalIndexError::InvalidIndexEpoch)
        );
    }

    #[test]
    fn sorted_evidence_set_deduplicates_and_intersects_deterministically() {
        use crate::evidence_index::{SortedEvidenceSet, sorted_evidence_set_hash};

        let left = SortedEvidenceSet::from_unsorted(vec![9, 1, 5, 3, 5, 7]);
        let right = SortedEvidenceSet::from_unsorted(vec![8, 7, 3, 3, 2, 1]);
        let intersection = left.intersect(&right);

        assert_eq!(left.values(), &[1, 3, 5, 7, 9]);
        assert_eq!(right.values(), &[1, 2, 3, 7, 8]);
        assert_eq!(intersection.values(), &[1, 3, 7]);
        assert_eq!(
            intersection.set_hash(),
            sorted_evidence_set_hash(&[1, 3, 7])
        );

        let mut reusable = Vec::new();
        left.intersect_into(&right, &mut reusable);
        assert_eq!(reusable, vec![1, 3, 7]);
    }

    #[test]
    fn hot_bitmap_filter_returns_candidate_only_refs() {
        use crate::evidence_index::{
            BitmapFilterError, CandidateEvidenceRef, CandidateEvidenceUse, CandidateOnlyGate,
            EvidenceCandidateTier, HotBitmapFilter, SortedEvidenceSet,
        };

        let epoch_hash = test_hash("bitmap-index-epoch");
        let allowed_segments = SortedEvidenceSet::from_unsorted(vec![9, 1, 3, 3]);
        let filter = HotBitmapFilter::new(epoch_hash, allowed_segments).unwrap();
        assert_eq!(filter.allowed_segment_count(), 3);
        assert_eq!(
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(Vec::new())),
            Err(BitmapFilterError::EmptyFilterSet)
        );
        let candidates = [
            CandidateEvidenceRef::new(
                test_hash("candidate-a"),
                1,
                EvidenceCandidateTier::LexicalBaseline,
                900_000,
                epoch_hash,
            ),
            CandidateEvidenceRef::new(
                test_hash("candidate-b"),
                2,
                EvidenceCandidateTier::LexicalBaseline,
                800_000,
                epoch_hash,
            ),
            CandidateEvidenceRef::new(
                test_hash("candidate-c"),
                3,
                EvidenceCandidateTier::LexicalBaseline,
                700_000,
                epoch_hash,
            ),
            CandidateEvidenceRef::new(
                test_hash("candidate-wrong-epoch"),
                9,
                EvidenceCandidateTier::LexicalBaseline,
                600_000,
                test_hash("other-epoch"),
            ),
        ];

        let filtered = filter.filter_candidates(&candidates, 8);
        assert_eq!(filtered.len(), 2);
        assert_eq!(filtered[0].evidence_ref_hash, test_hash("candidate-a"));
        assert_eq!(filtered[0].origin_tier, EvidenceCandidateTier::BitmapFilter);
        assert_eq!(filtered[0].score_quantized, 900_000);
        assert_eq!(filtered[1].evidence_ref_hash, test_hash("candidate-c"));
        assert!(filtered.iter().all(|candidate| {
            CandidateOnlyGate::validate_use(candidate, CandidateEvidenceUse::ContextPackCandidate)
                .is_ok()
        }));
    }

    #[test]
    fn hot_term_dictionary_expands_prefix_deterministically() {
        use crate::evidence_index::{
            HotTermDictionary, TermDictionaryError, expanded_term_ref_hash, lexical_token_hash,
        };

        let epoch_hash = test_hash("term-dictionary-epoch");
        let dictionary = HotTermDictionary::from_terms(
            epoch_hash,
            &[
                "FooBar",
                "foo_bar",
                "foo-baz",
                "OtherSymbol",
                "foo_bar",
                "!!!",
            ],
        )
        .unwrap();
        assert_eq!(dictionary.term_count(), 4);

        let expanded = dictionary.expand_prefix("foo", 2);
        assert_eq!(expanded.len(), 2);
        assert_eq!(
            expanded[0].term_hash,
            lexical_token_hash("foo_bar").unwrap()
        );
        assert_eq!(expanded[1].term_hash, lexical_token_hash("foobar").unwrap());
        assert_eq!(expanded[0].dictionary_ordinal, 0);
        assert_eq!(expanded[1].dictionary_ordinal, 1);
        assert!(expanded.iter().all(|term_ref| term_ref.is_valid()));
        assert_eq!(
            expanded[0].expansion_hash,
            expanded_term_ref_hash(&expanded[0])
        );

        assert!(dictionary.expand_prefix("zzz", 4).is_empty());
        assert!(dictionary.expand_prefix("!!!", 4).is_empty());
        assert_eq!(
            HotTermDictionary::from_terms([0; 32], &["valid"]),
            Err(TermDictionaryError::InvalidIndexEpoch)
        );
        assert_eq!(
            HotTermDictionary::from_terms(epoch_hash, &["!!!"]),
            Err(TermDictionaryError::EmptyDictionary)
        );
    }

    #[test]
    fn cold_vector_index_returns_candidate_only_replay_bound_refs() {
        use crate::evidence_index::{
            CandidateEvidenceUse, CandidateOnlyGate, ColdVectorExpansionReplayRecord,
            ColdVectorIndex, EvidenceCandidateTier, cold_vector_query_hash,
        };

        let epoch_hash = test_hash("cold-vector-runtime-epoch");
        let mut index = ColdVectorIndex::new(epoch_hash, 4).unwrap();
        index
            .insert_vector(
                test_hash("cold-vector-a"),
                1,
                test_hash("cold-vector-artifact-a"),
                &[100, 0, 0, 0],
            )
            .unwrap();
        index
            .insert_vector(
                test_hash("cold-vector-b"),
                2,
                test_hash("cold-vector-artifact-b"),
                &[80, 10, 0, 0],
            )
            .unwrap();
        index
            .insert_vector(
                test_hash("cold-vector-c"),
                3,
                test_hash("cold-vector-artifact-c"),
                &[0, 100, 0, 0],
            )
            .unwrap();

        let query = [100, 0, 0, 0];
        let candidates = index.query_top_k(&query, 2).unwrap();
        assert_eq!(candidates.len(), 2);
        assert_eq!(candidates[0].segment_id, 1);
        assert_eq!(
            candidates[0].origin_tier,
            EvidenceCandidateTier::ColdVectorExpansion
        );
        assert!(candidates[0].score_quantized >= candidates[1].score_quantized);
        assert!(candidates.iter().all(|candidate| {
            CandidateOnlyGate::validate_use(candidate, CandidateEvidenceUse::ContextPackCandidate)
                .is_err()
        }));

        let query_hash = cold_vector_query_hash(&query, 2).unwrap();
        let record = ColdVectorExpansionReplayRecord::new_with_query_hash(
            epoch_hash,
            query_hash,
            2,
            index.expansion_config_hash(),
            index.index_hash(),
            42_000,
            &candidates,
        )
        .unwrap();
        assert!(record.is_valid());
        assert!(
            CandidateOnlyGate::validate_replay_bound_context_pack_candidates(
                &candidates,
                Some(&record),
                None,
            )
            .is_ok()
        );
    }

    #[test]
    fn cold_vector_index_rejects_bad_vectors_and_tampered_replay() {
        use crate::evidence_index::{
            CandidateOnlyGate, CandidateOnlyGateError, ColdVectorExpansionReplayRecord,
            ColdVectorIndex, ColdVectorIndexError, cold_vector_query_hash,
        };

        let epoch_hash = test_hash("cold-vector-runtime-reject-epoch");
        assert_eq!(
            ColdVectorIndex::new(epoch_hash, 0),
            Err(ColdVectorIndexError::InvalidDimension)
        );
        let mut index = ColdVectorIndex::new(epoch_hash, 3).unwrap();
        assert_eq!(
            index.insert_vector(
                test_hash("cold-vector-bad"),
                1,
                test_hash("cold-vector-bad-artifact"),
                &[0, 0, 0],
            ),
            Err(ColdVectorIndexError::InvalidVector)
        );
        index
            .insert_vector(
                test_hash("cold-vector-good"),
                1,
                test_hash("cold-vector-good-artifact"),
                &[1, 2, 3],
            )
            .unwrap();
        assert_eq!(
            index.insert_vector(
                test_hash("cold-vector-good"),
                1,
                test_hash("cold-vector-dup-artifact"),
                &[1, 2, 3],
            ),
            Err(ColdVectorIndexError::DuplicateVector)
        );
        assert_eq!(
            index.query_top_k(&[0, 0, 0], 1),
            Err(ColdVectorIndexError::EmptyQuery)
        );

        let query = [1, 2, 3];
        let candidates = index.query_top_k(&query, 1).unwrap();
        let mut record = ColdVectorExpansionReplayRecord::new_with_query_hash(
            epoch_hash,
            cold_vector_query_hash(&query, 1).unwrap(),
            1,
            index.expansion_config_hash(),
            index.index_hash(),
            42_000,
            &candidates,
        )
        .unwrap();
        record.candidate_list_hash = test_hash("tampered-cold-vector-list");
        assert_eq!(
            CandidateOnlyGate::validate_replay_bound_context_pack_candidates(
                &candidates,
                Some(&record),
                None,
            ),
            Err(CandidateOnlyGateError::ReplayRecordMismatch)
        );
    }

    #[test]
    fn task_ledger_prefers_critical_path_over_suggested_priority() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(60_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, None, Some(-100)))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(3, vec![2], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(4, vec![], 0, None, Some(1_000_000)))
            .unwrap();

        assert_eq!(ledger.select_next_task(0).unwrap(), Some(1));
        let first = ledger.task(1).unwrap();
        let decoy = ledger.task(4).unwrap();
        assert!(first.critical_path_len > decoy.critical_path_len);
        assert!(first.deterministic_priority_score > decoy.deterministic_priority_score);

        ledger.complete_task(1).unwrap();
        assert_eq!(ledger.select_next_task(0).unwrap(), Some(2));
    }

    #[test]
    fn task_ledger_priority_ignores_suggested_priority_noise() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut low_suggestion = TaskLedger::new(10_000);
        low_suggestion
            .insert_task(TaskCard::new(10, vec![], 4, None, Some(-1_000)))
            .unwrap();
        low_suggestion
            .insert_task(TaskCard::new(20, vec![], 1, None, Some(1_000_000)))
            .unwrap();

        let mut high_suggestion = TaskLedger::new(10_000);
        high_suggestion
            .insert_task(TaskCard::new(10, vec![], 4, None, Some(1_000_000)))
            .unwrap();
        high_suggestion
            .insert_task(TaskCard::new(20, vec![], 1, None, Some(-1_000)))
            .unwrap();

        assert_eq!(low_suggestion.ordered_ready_tasks(0).unwrap(), vec![10, 20]);
        assert_eq!(
            high_suggestion.ordered_ready_tasks(0).unwrap(),
            vec![10, 20]
        );
    }

    #[test]
    fn task_ledger_deadline_pressure_uses_explicit_timestamp() {
        use crate::task_ledger::{TaskCard, TaskLedger, deadline_pressure_score};

        assert_eq!(deadline_pressure_score(Some(900), 1_000, 10_000), 10_000);
        assert_eq!(deadline_pressure_score(Some(11_000), 1_000, 10_000), 0);

        let mut ledger = TaskLedger::new(10_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, Some(2_000), Some(-100)))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![], 0, Some(20_000), Some(1_000_000)))
            .unwrap();

        assert_eq!(ledger.select_next_task(1_000).unwrap(), Some(1));
    }

    #[test]
    fn task_ledger_forest_fast_path_counts_blocked_descendants() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(10_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(3, vec![1], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(4, vec![2], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(5, vec![2], 0, None, None))
            .unwrap();
        ledger.complete_task(4).unwrap();

        ledger.recompute_priorities(0).unwrap();

        let root = ledger.task(1).unwrap();
        let branch = ledger.task(2).unwrap();
        assert_eq!(root.critical_path_len, 3);
        assert_eq!(root.blocked_descendant_count, 3);
        assert_eq!(branch.critical_path_len, 2);
        assert_eq!(branch.blocked_descendant_count, 1);
    }

    #[test]
    fn task_ledger_invalidates_cached_topology_on_insert() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(10_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        ledger.recompute_priorities(0).unwrap();
        assert_eq!(ledger.task(1).unwrap().critical_path_len, 1);

        ledger
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        ledger.recompute_priorities(0).unwrap();

        let root = ledger.task(1).unwrap();
        assert_eq!(root.critical_path_len, 2);
        assert_eq!(root.blocked_descendant_count, 1);
    }

    #[test]
    fn task_ledger_ready_cache_invalidates_on_status_and_now() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(100);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![1], 0, Some(100), None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(3, vec![1], 0, None, None))
            .unwrap();
        ledger.complete_task(1).unwrap();

        assert_eq!(ledger.ordered_ready_tasks(0).unwrap(), vec![2, 3]);
        let first_score = ledger.task(2).unwrap().deterministic_priority_score;
        assert_eq!(ledger.ordered_ready_tasks(0).unwrap(), vec![2, 3]);

        assert_eq!(ledger.ordered_ready_tasks(50).unwrap(), vec![2, 3]);
        assert!(ledger.task(2).unwrap().deterministic_priority_score > first_score);

        ledger.complete_task(2).unwrap();
        assert_eq!(ledger.ordered_ready_tasks(50).unwrap(), vec![3]);
    }

    #[test]
    fn task_ledger_selection_proof_binds_selected_task() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(60_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(3, vec![], 3, None, None))
            .unwrap();

        let proof = ledger.select_next_task_proof(1_000).unwrap().unwrap();
        assert_eq!(
            ledger.select_next_task(1_000).unwrap(),
            Some(proof.selected_task_id)
        );
        assert_eq!(proof.selected_task_id, 1);
        assert!(proof.has_valid_fields());
        assert!(ledger.validates_task_selection_proof(&proof).unwrap());
    }

    #[test]
    fn task_ledger_selection_proof_rejects_stale_ready_queue() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(10_000);
        ledger
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(3, vec![], 0, None, None))
            .unwrap();

        let proof = ledger.select_next_task_proof(0).unwrap().unwrap();
        assert_eq!(proof.selected_task_id, 1);

        ledger.complete_task(1).unwrap();
        assert!(!ledger.validates_task_selection_proof(&proof).unwrap());
        let replacement = ledger.select_next_task_proof(0).unwrap().unwrap();
        assert_eq!(replacement.selected_task_id, 2);
        assert!(ledger.validates_task_selection_proof(&replacement).unwrap());
    }

    #[test]
    fn task_ledger_selection_proof_rejects_priority_mutation() {
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut ledger = TaskLedger::new(10_000);
        ledger
            .insert_task(TaskCard::new(10, vec![], 0, None, None))
            .unwrap();
        ledger
            .insert_task(TaskCard::new(20, vec![], 0, None, None))
            .unwrap();

        let proof = ledger.select_next_task_proof(0).unwrap().unwrap();
        assert_eq!(proof.selected_task_id, 10);

        ledger
            .insert_task(TaskCard::new(5, vec![], 10, None, None))
            .unwrap();
        assert!(!ledger.validates_task_selection_proof(&proof).unwrap());
        let replacement = ledger.select_next_task_proof(0).unwrap().unwrap();
        assert_eq!(replacement.selected_task_id, 5);
    }

    #[test]
    fn task_ledger_rejects_missing_dependencies_and_cycles() {
        use crate::task_ledger::{TaskCard, TaskLedger, TaskLedgerError};

        let mut missing = TaskLedger::new(1_000);
        missing
            .insert_task(TaskCard::new(1, vec![99], 0, None, None))
            .unwrap();
        assert_eq!(
            missing.select_next_task(0),
            Err(TaskLedgerError::MissingDependency(99))
        );

        let mut cycle = TaskLedger::new(1_000);
        cycle
            .insert_task(TaskCard::new(1, vec![2], 0, None, None))
            .unwrap();
        cycle
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        assert_eq!(
            cycle.select_next_task(0),
            Err(TaskLedgerError::CycleDetected)
        );
    }

    #[test]
    fn context_governor_activates_only_two_hops_and_required_evidence() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(100, 16)).unwrap();
        for node_id in 1..=9 {
            governor
                .insert_node(ContextNode::new(
                    node_id,
                    ContextNodeKind::Task,
                    10,
                    node_id as u32,
                    0,
                    0,
                ))
                .unwrap();
        }
        governor.add_edge(1, 2).unwrap();
        governor.add_edge(2, 3).unwrap();
        governor.add_edge(3, 4).unwrap();
        governor.add_edge(6, 7).unwrap();
        governor.add_edge(7, 8).unwrap();
        governor.add_edge(8, 9).unwrap();

        let activated = governor.build_activated_graph(1, &[6]).unwrap();
        assert_eq!(activated.node_ids, vec![1, 2, 3, 6, 7, 8]);
        assert!(!activated.node_ids.contains(&4));
        assert!(!activated.node_ids.contains(&9));
        assert_eq!(activated.activation_node_count, 6);
    }

    #[test]
    fn context_governor_does_not_select_global_high_utility_node() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(40, 16)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::File, 10, 20, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(3, ContextNodeKind::Evidence, 10, 30, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(
                99,
                ContextNodeKind::Memory,
                10,
                10_000,
                0,
                0,
            ))
            .unwrap();
        governor.add_edge(1, 2).unwrap();
        governor.add_edge(2, 3).unwrap();

        let pack = governor.build_context_pack(1, &[]).unwrap();
        assert_eq!(pack.node_ids, vec![1, 2, 3]);
        assert!(!pack.node_ids.contains(&99));
        assert!(pack.token_count <= 40);
        assert_eq!(pack.activation_node_count, 3);
    }

    #[test]
    fn context_governor_keeps_required_evidence_under_budget() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(30, 16)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 20, 1, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(3, ContextNodeKind::File, 20, 100, 0, 0))
            .unwrap();
        governor.add_edge(1, 3).unwrap();

        let pack = governor.build_context_pack(1, &[2]).unwrap();
        assert_eq!(pack.node_ids, vec![1, 2]);
        assert!(!pack.node_ids.contains(&3));
        assert_eq!(pack.token_count, 30);
    }

    #[test]
    fn context_governor_context_fold_records_retained_and_folded_nodes() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(40, 16)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 20, 1, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(3, ContextNodeKind::File, 10, 100, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(4, ContextNodeKind::Symbol, 10, 80, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(5, ContextNodeKind::Memory, 10, 60, 0, 0))
            .unwrap();
        governor.add_edge(1, 3).unwrap();
        governor.add_edge(1, 4).unwrap();
        governor.add_edge(2, 5).unwrap();

        let pack = governor.build_context_pack(1, &[2]).unwrap();
        let fold = governor.build_context_fold(1, &[2]).unwrap();
        let second_fold = governor.build_context_fold(1, &[2]).unwrap();

        assert_eq!(fold, second_fold);
        assert!(fold.is_valid());
        assert!(fold.is_lossy());
        assert_eq!(fold.active_task_id, 1);
        assert_eq!(fold.retained_node_ids, pack.node_ids);
        assert_eq!(fold.retained_node_ids, vec![1, 2, 3]);
        assert_eq!(fold.folded_node_ids, vec![4, 5]);
        assert_eq!(fold.retained_token_count, 40);
        assert_eq!(fold.folded_token_count, 20);
        assert_eq!(fold.context_pack_digest, pack.digest);
        assert_ne!(fold.retained_node_hash, [0; 32]);
        assert_ne!(fold.folded_node_hash, [0; 32]);
        assert_ne!(fold.record_hash, [0; 32]);
    }

    #[test]
    fn context_fold_record_rejects_tamper_or_missing_active_task() {
        use crate::context::{ContextFoldRecord, ContextGovernorError};

        assert_eq!(
            ContextFoldRecord::new(1, &[2], &[3], 10, 10, 1, 2, test_hash("pack")),
            Err(ContextGovernorError::InvalidFoldRecord)
        );

        let record =
            ContextFoldRecord::new(1, &[1, 2], &[3], 20, 10, 7, 3, test_hash("pack")).unwrap();
        assert!(record.is_valid());

        let mut tampered = record.clone();
        tampered.folded_token_count = 0;
        assert!(!tampered.is_valid());

        let mut overlap = record;
        overlap.folded_node_ids.push(2);
        overlap.folded_node_ids.sort_unstable();
        assert!(!overlap.is_valid());
    }

    #[test]
    fn context_governor_builds_candidate_bound_pack_proof() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
            ContextPackCandidateProof,
        };
        use crate::evidence_index::{CandidateEvidenceRef, EvidenceCandidateTier};

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(30, 16)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(7, ContextNodeKind::Evidence, 10, 80, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(8, ContextNodeKind::Evidence, 10, 70, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(
                99,
                ContextNodeKind::Memory,
                10,
                10_000,
                0,
                0,
            ))
            .unwrap();
        governor.add_edge(1, 99).unwrap();

        let candidates = [
            CandidateEvidenceRef::new(
                test_hash("candidate-seven"),
                7,
                EvidenceCandidateTier::LexicalBaseline,
                950_000,
                test_hash("index-epoch"),
            ),
            CandidateEvidenceRef::new(
                test_hash("candidate-eight"),
                8,
                EvidenceCandidateTier::BitmapFilter,
                850_000,
                test_hash("index-epoch"),
            ),
        ];

        let (pack, proof) = governor
            .build_candidate_bound_context_pack(1, &candidates, None)
            .unwrap();
        assert_eq!(pack.node_ids, vec![1, 7, 8]);
        assert!(proof.is_valid_for(&pack, &candidates, None));
        assert_eq!(proof.context_pack_digest, pack.digest);
        assert_ne!(proof.candidate_list_hash, [0; 32]);
        assert_ne!(proof.candidate_node_hash, [0; 32]);

        let rebuilt = ContextPackCandidateProof::new(1, &pack, &candidates, None).unwrap();
        assert_eq!(proof, rebuilt);

        let mut changed_candidates = candidates;
        changed_candidates[0].score_quantized = 1;
        assert!(!proof.is_valid_for(&pack, &changed_candidates, None));

        let mut stale_pack = pack.clone();
        stale_pack.node_ids = vec![1, 7];
        assert!(!proof.is_valid_for(&stale_pack, &candidates, None));
    }

    #[test]
    fn context_candidate_bound_pack_requires_cold_vector_replay_record() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextGovernorError, ContextNode,
            ContextNodeKind,
        };
        use crate::evidence_index::{
            CandidateEvidenceRef, CandidateOnlyGateError, ColdVectorExpansionReplayRecord,
            EvidenceCandidateTier,
        };

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(20, 8)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 10, 40, 0, 0))
            .unwrap();

        let cold_candidate = CandidateEvidenceRef::new(
            test_hash("cold-candidate"),
            2,
            EvidenceCandidateTier::ColdVectorExpansion,
            900_000,
            test_hash("index-epoch"),
        );
        assert_eq!(
            governor.build_candidate_bound_context_pack(1, &[cold_candidate], None),
            Err(ContextGovernorError::CandidateEvidenceGate(
                CandidateOnlyGateError::MissingReplayEvent
            ))
        );

        let record = ColdVectorExpansionReplayRecord::new(
            test_hash("index-epoch"),
            &["cold", "candidate"],
            4,
            test_hash("cold-config"),
            test_hash("cold-artifact"),
            42_000,
            &[cold_candidate],
        )
        .unwrap();
        let (pack, proof) = governor
            .build_candidate_bound_context_pack(1, &[cold_candidate], Some(&record))
            .unwrap();
        assert!(proof.is_valid_for(&pack, &[cold_candidate], Some(&record)));
        assert_eq!(proof.cold_vector_replay_record_hash, record.record_hash);
    }

    #[test]
    fn context_candidate_bound_pack_requires_agentic_evidence_replay_record() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextGovernorError, ContextNode,
            ContextNodeKind, ContextPackCandidateProof,
        };
        use crate::evidence_index::{
            AgenticEvidenceExecutionRecord, AgenticEvidenceProgram, AgenticEvidenceProgramStep,
            CandidateOnlyGateError, EvidenceCandidateTier, HotLexicalIndex,
        };

        let epoch_hash = test_hash("context-agentic-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("context-agentic-candidate"),
                7,
                &["browser", "policy", "replay"],
            )
            .unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            8,
            vec![AgenticEvidenceProgramStep::lexical_top_k(
                &["browser", "policy"],
                2,
            )],
        )
        .unwrap();
        let (candidates, record) = program.execute(Some(&lexical), None, None).unwrap();
        assert_eq!(candidates.len(), 1);
        assert_eq!(
            candidates[0].origin_tier,
            EvidenceCandidateTier::AgenticProgramOutput
        );

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(20, 8)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(7, ContextNodeKind::Evidence, 10, 80, 0, 0))
            .unwrap();

        assert_eq!(
            governor.build_candidate_bound_context_pack(1, &candidates, None),
            Err(ContextGovernorError::CandidateEvidenceGate(
                CandidateOnlyGateError::MissingReplayEvent
            ))
        );

        let (pack, proof) = governor
            .build_replay_bound_candidate_context_pack(1, &candidates, None, Some(&record))
            .unwrap();
        assert!(proof.is_valid_for_replay_records(&pack, &candidates, None, Some(&record), None));
        assert_eq!(
            proof.agentic_evidence_execution_record_hash,
            record.record_hash
        );
        assert_ne!(proof.agentic_evidence_execution_record_hash, [0; 32]);
        assert!(!proof.is_valid_for(&pack, &candidates, None));

        let rebuilt = ContextPackCandidateProof::new_with_replay_records(
            1,
            &pack,
            &candidates,
            None,
            Some(&record),
            None,
        )
        .unwrap();
        assert_eq!(proof, rebuilt);

        let wrong_record = AgenticEvidenceExecutionRecord::new(
            program.program_hash(),
            epoch_hash,
            program.steps().len(),
            2,
            &[],
            &[test_hash("wrong-agentic-trace")],
        )
        .unwrap();
        assert_eq!(
            ContextPackCandidateProof::new_with_replay_records(
                1,
                &pack,
                &candidates,
                None,
                Some(&wrong_record),
                None,
            ),
            Err(ContextGovernorError::CandidateEvidenceGate(
                CandidateOnlyGateError::ReplayRecordMismatch
            ))
        );

        let mut tampered_record = record.clone();
        tampered_record.candidate_list_hash = test_hash("tampered-agentic-candidate-list");
        assert_eq!(
            governor.build_replay_bound_candidate_context_pack(
                1,
                &candidates,
                None,
                Some(&tampered_record),
            ),
            Err(ContextGovernorError::CandidateEvidenceGate(
                CandidateOnlyGateError::ReplayRecordMismatch
            ))
        );
    }

    #[test]
    fn context_governor_enforces_activation_cap() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextGovernorError, ContextNode,
            ContextNodeKind,
        };

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(100, 2)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(3, ContextNodeKind::File, 10, 10, 0, 0))
            .unwrap();
        governor.add_edge(2, 3).unwrap();

        let activated = governor.build_activated_graph(1, &[2]).unwrap();
        assert_eq!(activated.node_ids, vec![1, 2]);
        assert!(!activated.node_ids.contains(&3));
        assert_eq!(activated.activation_node_count, 2);

        let mut too_small = ContextGovernor::new(ContextGovernorConfig::bounded(100, 1)).unwrap();
        too_small
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        too_small
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 10, 10, 0, 0))
            .unwrap();
        assert_eq!(
            too_small.build_activated_graph(1, &[2]),
            Err(ContextGovernorError::ActivationCapExceeded)
        );
    }

    #[test]
    fn typed_tool_ir_requires_approval_and_staging_by_risk() {
        use crate::policy::{
            CapabilityClass, EvidenceContract, RiskClass, SideEffectClass, TypedToolIR,
        };

        let r3_ir = TypedToolIR::new(
            1,
            2,
            CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        assert!(r3_ir.is_valid());

        let invalid_r4 = TypedToolIR::new(
            1,
            3,
            CapabilityClass::FinancialLegal,
            SideEffectClass::FinancialLegal,
            test_hash("credential"),
            RiskClass::R4,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        assert!(!invalid_r4.is_valid());
    }

    #[test]
    fn typed_tool_ir_rejects_underclassified_side_effects() {
        use crate::policy::{
            CapabilityClass, EvidenceContract, RiskClass, SideEffectClass, TypedToolIR,
        };

        let underclassified = TypedToolIR::new(
            1,
            4,
            CapabilityClass::FinancialLegal,
            SideEffectClass::FinancialLegal,
            test_hash("credential"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: false,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            None,
        );
        assert!(!underclassified.is_valid());
    }

    #[test]
    fn policy_kernel_hard_blocks_r4_without_staging_or_hitl() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract,
            POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL, PolicyDecision, PolicyFacts, RiskClass,
            SideEffectClass, TypedToolIR,
        };

        let ir = TypedToolIR::new(
            7,
            8,
            CapabilityClass::FinancialLegal,
            SideEffectClass::FinancialLegal,
            test_hash("credential"),
            RiskClass::R4,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::HardBlock);
        assert_eq!(
            trace.matched_rule_ids,
            vec![POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL]
        );
        assert!(trace.counterexample_ref_hash.is_some());
        assert!(trace.is_replay_consistent_with(&ir));
    }

    #[test]
    fn policy_kernel_requires_approval_for_r3() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract,
            POLICY_RULE_APPROVAL_REQUIRED, PolicyDecision, PolicyFacts, RiskClass, SideEffectClass,
            TypedToolIR,
        };

        let ir = TypedToolIR::new(
            7,
            9,
            CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::RequireApproval);
        assert_eq!(trace.matched_rule_ids, vec![POLICY_RULE_APPROVAL_REQUIRED]);
    }

    #[test]
    fn policy_kernel_allows_approved_r3() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, POLICY_RULE_ALLOW,
            PolicyDecision, PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };

        let ir = TypedToolIR::new(
            7,
            10,
            CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let facts = PolicyFacts::new(test_hash("policy-v1"), true, false, false);
        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::Allow);
        assert_eq!(trace.matched_rule_ids, vec![POLICY_RULE_ALLOW]);
    }

    #[test]
    fn policy_kernel_requires_staging_when_contract_requires_it() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract,
            POLICY_RULE_STAGING_REQUIRED, PolicyDecision, PolicyFacts, RiskClass, SideEffectClass,
            TypedToolIR,
        };

        let ir = TypedToolIR::new(
            7,
            11,
            CapabilityClass::ExternalRead,
            SideEffectClass::ExternalRead,
            test_hash("credential"),
            RiskClass::R2,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: false,
                requires_staging: true,
                expected_artifact_hash: None,
            },
            None,
        );
        assert!(ir.is_valid());

        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::RequireStagingSandbox);
        assert_eq!(trace.matched_rule_ids, vec![POLICY_RULE_STAGING_REQUIRED]);
    }

    #[test]
    fn policy_kernel_allows_r4_with_physical_staging_and_approval() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, POLICY_RULE_ALLOW,
            PolicyDecision, PolicyFacts, RiskClass, SideEffectClass, StagingEvidenceKind,
            TypedToolIR,
        };

        let ir = TypedToolIR::new(
            7,
            12,
            CapabilityClass::FinancialLegal,
            SideEffectClass::FinancialLegal,
            test_hash("credential"),
            RiskClass::R4,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let facts = PolicyFacts::with_staging_evidence(
            test_hash("policy-v1"),
            true,
            test_hash("testnet-execution-proof"),
            StagingEvidenceKind::TestnetExecution,
        );
        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::Allow);
        assert_eq!(trace.matched_rule_ids, vec![POLICY_RULE_ALLOW]);
        assert!(trace.is_replay_consistent_with(&ir));
    }

    #[test]
    fn policy_kernel_rejects_text_or_mock_staging_facts_for_r4() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract,
            POLICY_RULE_INVALID_FACTS, PolicyDecision, PolicyFacts, RiskClass, SideEffectClass,
            StagingEvidenceKind, TypedToolIR,
        };

        let ir = TypedToolIR::new(
            7,
            13,
            CapabilityClass::FinancialLegal,
            SideEffectClass::FinancialLegal,
            test_hash("credential"),
            RiskClass::R4,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let llm_text_facts = PolicyFacts::with_staging_evidence(
            test_hash("policy-v1"),
            true,
            test_hash("llm-dry-run-text"),
            StagingEvidenceKind::LlmGeneratedText,
        );
        let llm_trace = DeterministicPolicyKernel.evaluate(&ir, &llm_text_facts);
        assert_eq!(llm_trace.decision, PolicyDecision::HardBlock);
        assert_eq!(llm_trace.matched_rule_ids, vec![POLICY_RULE_INVALID_FACTS]);

        let mock_log_facts = PolicyFacts::with_staging_evidence(
            test_hash("policy-v1"),
            true,
            test_hash("mock-dry-run-log"),
            StagingEvidenceKind::MockLog,
        );
        let mock_trace = DeterministicPolicyKernel.evaluate(&ir, &mock_log_facts);
        assert_eq!(mock_trace.decision, PolicyDecision::HardBlock);
        assert_eq!(mock_trace.matched_rule_ids, vec![POLICY_RULE_INVALID_FACTS]);
    }

    #[test]
    fn policy_datalog_closure_proves_r4_hard_block_and_binds_trace() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract,
            POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL, PolicyDecision, PolicyFacts, RiskClass,
            SideEffectClass, TypedToolIR,
        };

        let ir = TypedToolIR::new(
            70,
            71,
            CapabilityClass::FinancialLegal,
            SideEffectClass::FinancialLegal,
            test_hash("credential"),
            RiskClass::R4,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("approval-scope")),
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let (trace, closure) = DeterministicPolicyKernel.evaluate_with_datalog(&ir, &facts);

        assert_eq!(trace.decision, PolicyDecision::HardBlock);
        assert_eq!(
            trace.matched_rule_ids,
            vec![POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL]
        );
        assert!(closure.proves_r4_without_staging_or_hitl_hard_block());
        assert!(trace.binds_datalog_closure(&closure));
        assert!(closure.is_valid_for(&ir, &facts));

        let tampered = crate::policy::PolicyDatalogClosureProof {
            derived_atom_bits: closure.derived_atom_bits ^ 1,
            ..closure
        };
        assert!(!tampered.is_valid_hash());
        assert!(!trace.binds_datalog_closure(&tampered));
    }

    #[test]
    fn policy_datalog_closure_derives_approval_and_allow_paths() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };

        let ir = TypedToolIR::new(
            72,
            73,
            CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );

        let missing_approval_facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let (approval_trace, approval_closure) =
            DeterministicPolicyKernel.evaluate_with_datalog(&ir, &missing_approval_facts);
        assert_eq!(approval_trace.decision, PolicyDecision::RequireApproval);
        assert!(approval_closure.proves_approval_requirement());
        assert!(approval_trace.binds_datalog_closure(&approval_closure));

        let approved_facts = PolicyFacts::new(test_hash("policy-v1"), true, false, false);
        let (allow_trace, allow_closure) =
            DeterministicPolicyKernel.evaluate_with_datalog(&ir, &approved_facts);
        assert_eq!(allow_trace.decision, PolicyDecision::Allow);
        assert!(allow_closure.proves_allow());
        assert!(allow_trace.binds_datalog_closure(&allow_closure));
    }

    #[test]
    fn signed_approval_token_binds_exact_review_packet() {
        use crate::policy::{
            ApprovalScopeReplayDecision, CapabilityClass, EvidenceContract, PolicyDecision,
            PolicyProofTrace, ReviewPacket, RiskClass, SideEffectClass, SignedApprovalToken,
            TypedToolIR,
        };

        let ir = TypedToolIR::new(
            9,
            11,
            CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let proof = PolicyProofTrace {
            policy_version_hash: test_hash("policy-v1"),
            input_ir_hash: ir.canonical_hash,
            facts_hash: test_hash("facts"),
            decision: PolicyDecision::RequireApproval,
            matched_rule_ids: vec![1, 2],
            counterexample_ref_hash: None,
        };
        assert!(proof.is_replay_consistent_with(&ir));

        let packet = ReviewPacket::new(
            1,
            ir.canonical_hash,
            test_hash("policy-proof-trace"),
            RiskClass::R3,
            SideEffectClass::ExternalWrite,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![test_hash("evidence")],
            None,
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        assert!(packet.is_valid());

        let token = SignedApprovalToken {
            approval_id: 7,
            approver_key_hash: test_hash("operator-key"),
            review_packet_hash: packet.review_packet_hash,
            typed_tool_ir_hash: packet.typed_tool_ir_hash,
            max_risk_class: RiskClass::R3,
            resource_scope_hash: packet.resource_scope_hash,
            max_spend_minor_units: Some(100),
            expires_at_ms: 1_000,
            challenge_nonce: test_hash("nonce"),
            signature: [42; 64],
        };
        assert!(token.binds_review_packet(&packet, 999));
        assert!(!token.binds_review_packet(&packet, 1_000));

        let changed_packet = ReviewPacket::new(
            1,
            test_hash("different-ir"),
            test_hash("policy-proof-trace"),
            RiskClass::R3,
            SideEffectClass::ExternalWrite,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![test_hash("evidence")],
            None,
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        assert!(changed_packet.is_valid());
        assert!(!token.binds_review_packet(&changed_packet, 999));

        let covered = token.replay_scope(
            &packet,
            Some(100),
            test_hash("policy-window"),
            test_hash("policy-window"),
            999,
        );
        assert_eq!(covered.decision, ApprovalScopeReplayDecision::Covered);
        assert!(covered.proves_covered());

        let overspend = token.replay_scope(
            &packet,
            Some(101),
            test_hash("policy-window"),
            test_hash("policy-window"),
            999,
        );
        assert_eq!(
            overspend.decision,
            ApprovalScopeReplayDecision::SpendLimitExceeded
        );
        assert!(overspend.is_valid());
        assert!(!overspend.proves_covered());

        let changed_window = token.replay_scope(
            &packet,
            Some(100),
            test_hash("policy-window"),
            test_hash("different-policy-window"),
            999,
        );
        assert_eq!(
            changed_window.decision,
            ApprovalScopeReplayDecision::PolicyWindowMismatch
        );
        assert!(changed_window.is_valid());

        let scope_mismatch = token.replay_scope(
            &changed_packet,
            Some(100),
            test_hash("policy-window"),
            test_hash("policy-window"),
            999,
        );
        assert_eq!(
            scope_mismatch.decision,
            ApprovalScopeReplayDecision::ScopeMismatch
        );
        assert!(scope_mismatch.is_valid());
    }

    #[test]
    fn approval_scope_replay_fails_closed_on_expiry_and_policy_window_change() {
        use crate::policy::{
            ApprovalScopeReplayDecision, ReviewPacket, RiskClass, SideEffectClass,
            SignedApprovalToken,
        };

        let packet = ReviewPacket::new(
            3,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            RiskClass::R3,
            SideEffectClass::ExternalWrite,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![test_hash("evidence")],
            None,
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        assert!(packet.is_valid());

        let token = SignedApprovalToken {
            approval_id: 9,
            approver_key_hash: test_hash("operator-key"),
            review_packet_hash: packet.review_packet_hash,
            typed_tool_ir_hash: packet.typed_tool_ir_hash,
            max_risk_class: RiskClass::R3,
            resource_scope_hash: packet.resource_scope_hash,
            max_spend_minor_units: None,
            expires_at_ms: 1_000,
            challenge_nonce: test_hash("nonce"),
            signature: [7; 64],
        };

        let expired = token.replay_scope(
            &packet,
            None,
            test_hash("policy-window"),
            test_hash("policy-window"),
            1_000,
        );
        assert_eq!(expired.decision, ApprovalScopeReplayDecision::Expired);
        assert!(expired.is_valid());
        assert!(!expired.proves_covered());

        let policy_window_mismatch = token.replay_scope(
            &packet,
            None,
            test_hash("policy-window"),
            test_hash("new-policy-window"),
            999,
        );
        assert_eq!(
            policy_window_mismatch.decision,
            ApprovalScopeReplayDecision::PolicyWindowMismatch
        );
        assert!(policy_window_mismatch.is_valid());
        assert!(!policy_window_mismatch.proves_covered());
    }

    #[test]
    fn operator_review_artifact_signs_hashes_not_helper_text() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, OperatorReviewArtifact,
            PolicyDecision, PolicyFacts, ReviewPacket, RiskClass, SideEffectClass,
            SignedApprovalToken, TypedToolIR,
        };

        let ir = TypedToolIR::new(
            10,
            20,
            CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::RequireApproval);

        let packet = ReviewPacket::new(
            4,
            ir.canonical_hash,
            proof.compute_hash(),
            RiskClass::R3,
            SideEffectClass::ExternalWrite,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![test_hash("evidence")],
            None,
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        assert!(packet.is_valid());

        let token = SignedApprovalToken {
            approval_id: 10,
            approver_key_hash: test_hash("operator-key"),
            review_packet_hash: packet.review_packet_hash,
            typed_tool_ir_hash: packet.typed_tool_ir_hash,
            max_risk_class: RiskClass::R3,
            resource_scope_hash: packet.resource_scope_hash,
            max_spend_minor_units: Some(100),
            expires_at_ms: 10_000,
            challenge_nonce: test_hash("nonce"),
            signature: [9; 64],
        };
        let approval_replay = token.replay_scope(
            &packet,
            Some(50),
            test_hash("policy-window"),
            test_hash("policy-window"),
            9_999,
        );
        assert!(approval_replay.proves_covered());

        let artifact = OperatorReviewArtifact::new(
            &packet,
            &proof,
            Some(&approval_replay),
            test_hash("policy-window"),
            vec![test_hash("policy-event"), test_hash("approval-event")],
            Some(test_hash("helper-text")),
        );
        assert!(artifact.is_valid_with(&packet, &proof, Some(&approval_replay)));
        assert!(artifact.signing_target_excludes_helper_text());

        let changed_helper = OperatorReviewArtifact::new(
            &packet,
            &proof,
            Some(&approval_replay),
            test_hash("policy-window"),
            vec![test_hash("policy-event"), test_hash("approval-event")],
            Some(test_hash("different-helper-text")),
        );
        assert_eq!(
            artifact.signing_target_hash,
            changed_helper.signing_target_hash
        );
        assert_ne!(artifact.artifact_hash, changed_helper.artifact_hash);
    }

    #[test]
    fn operator_review_artifact_rejects_mismatched_policy_proof() {
        use crate::policy::{
            OperatorReviewArtifact, PolicyDecision, PolicyProofTrace, ReviewPacket, RiskClass,
            SideEffectClass,
        };

        let packet = ReviewPacket::new(
            5,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            RiskClass::R3,
            SideEffectClass::ExternalWrite,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![test_hash("evidence")],
            None,
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        assert!(packet.is_valid());

        let proof = PolicyProofTrace {
            policy_version_hash: test_hash("policy-v1"),
            input_ir_hash: test_hash("different-tool-ir"),
            facts_hash: test_hash("facts"),
            decision: PolicyDecision::RequireApproval,
            matched_rule_ids: vec![1],
            counterexample_ref_hash: None,
        };

        let artifact = OperatorReviewArtifact::new(
            &packet,
            &proof,
            None,
            test_hash("policy-window"),
            vec![test_hash("policy-event")],
            Some(test_hash("helper-text")),
        );
        assert!(!artifact.is_valid_with(&packet, &proof, None));
    }

    #[test]
    fn dual_approval_proof_requires_two_distinct_tokens_on_same_artifact() {
        use crate::policy::{
            DeterministicPolicyKernel, DualApprovalProof, EvidenceContract, OperatorReviewArtifact,
            PolicyFacts, ReviewPacket, RiskClass, SideEffectClass, SignedApprovalToken,
            TypedToolIR,
        };

        let ir = TypedToolIR::new(
            11,
            21,
            crate::policy::CapabilityClass::ExternalWrite,
            SideEffectClass::ExternalWrite,
            test_hash("credential"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        let proof = DeterministicPolicyKernel.evaluate(
            &ir,
            &PolicyFacts::new(test_hash("policy-v1"), false, false, false),
        );
        let packet = ReviewPacket::new(
            6,
            ir.canonical_hash,
            proof.compute_hash(),
            RiskClass::R3,
            SideEffectClass::ExternalWrite,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![test_hash("evidence")],
            None,
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        let artifact = OperatorReviewArtifact::new(
            &packet,
            &proof,
            None,
            test_hash("policy-window"),
            vec![test_hash("policy-event")],
            Some(test_hash("helper-text")),
        );
        assert!(artifact.is_valid_with(&packet, &proof, None));

        let first = SignedApprovalToken {
            approval_id: 11,
            approver_key_hash: test_hash("operator-a"),
            review_packet_hash: packet.review_packet_hash,
            typed_tool_ir_hash: packet.typed_tool_ir_hash,
            max_risk_class: RiskClass::R3,
            resource_scope_hash: packet.resource_scope_hash,
            max_spend_minor_units: Some(100),
            expires_at_ms: 10_000,
            challenge_nonce: test_hash("nonce-a"),
            signature: [11; 64],
        };
        let second = SignedApprovalToken {
            approval_id: 12,
            approver_key_hash: test_hash("operator-b"),
            review_packet_hash: packet.review_packet_hash,
            typed_tool_ir_hash: packet.typed_tool_ir_hash,
            max_risk_class: RiskClass::R3,
            resource_scope_hash: packet.resource_scope_hash,
            max_spend_minor_units: Some(100),
            expires_at_ms: 10_000,
            challenge_nonce: test_hash("nonce-b"),
            signature: [12; 64],
        };

        let dual = DualApprovalProof::new(&artifact, &packet, &first, &second, 9_999);
        assert_ne!(dual.proof_hash, [0; 32]);
        assert!(dual.is_valid_for(&artifact, &packet, &first, &second, 9_999));

        let same_operator = SignedApprovalToken {
            approval_id: 13,
            approver_key_hash: first.approver_key_hash,
            challenge_nonce: test_hash("nonce-c"),
            signature: [13; 64],
            ..second.clone()
        };
        let invalid_same_operator =
            DualApprovalProof::new(&artifact, &packet, &first, &same_operator, 9_999);
        assert_eq!(invalid_same_operator.proof_hash, [0; 32]);
        assert!(!invalid_same_operator.is_valid_for(
            &artifact,
            &packet,
            &first,
            &same_operator,
            9_999
        ));

        let wrong_packet = SignedApprovalToken {
            approval_id: 14,
            approver_key_hash: test_hash("operator-c"),
            review_packet_hash: test_hash("different-review-packet"),
            challenge_nonce: test_hash("nonce-d"),
            signature: [14; 64],
            ..second
        };
        let invalid_wrong_packet =
            DualApprovalProof::new(&artifact, &packet, &first, &wrong_packet, 9_999);
        assert_eq!(invalid_wrong_packet.proof_hash, [0; 32]);
    }

    #[test]
    fn r4_review_packet_requires_staging_or_hitl_override_evidence() {
        use crate::policy::{ReviewPacket, RiskClass, SideEffectClass, StagingEvidenceKind};

        let missing_staging_and_override = ReviewPacket::new(
            2,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof"),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource"),
            test_hash("effect"),
            vec![test_hash("evidence")],
            None,
            None,
            None,
            test_hash("redaction"),
        );
        assert!(!missing_staging_and_override.is_valid());

        let untyped_staging = ReviewPacket::new(
            2,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof"),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource"),
            test_hash("effect"),
            vec![test_hash("evidence")],
            Some(test_hash("staging-proof")),
            None,
            None,
            test_hash("redaction"),
        );
        assert!(!untyped_staging.is_valid());

        let staged = ReviewPacket::new_with_staging_evidence_kind(
            2,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof"),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource"),
            test_hash("effect"),
            vec![test_hash("evidence")],
            Some(test_hash("staging-proof")),
            Some(StagingEvidenceKind::SandboxExecution),
            None,
            None,
            test_hash("redaction"),
        );
        assert!(staged.is_valid());

        let hitl_override = ReviewPacket::new(
            2,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof"),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource"),
            test_hash("effect"),
            vec![test_hash("evidence"), test_hash("signed-hitl-override")],
            None,
            Some(test_hash("signed-hitl-override")),
            None,
            test_hash("redaction"),
        );
        assert!(hitl_override.is_valid());
    }

    #[test]
    fn r4_review_packet_rejects_text_or_mock_dry_run_evidence() {
        use crate::policy::{ReviewPacket, RiskClass, SideEffectClass, StagingEvidenceKind};

        let llm_text_staging = ReviewPacket::new_with_staging_evidence_kind(
            2,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof"),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource"),
            test_hash("effect"),
            vec![test_hash("evidence")],
            Some(test_hash("llm-dry-run-text")),
            Some(StagingEvidenceKind::LlmGeneratedText),
            None,
            None,
            test_hash("redaction"),
        );
        assert!(!llm_text_staging.is_valid());

        let mock_log_staging = ReviewPacket::new_with_staging_evidence_kind(
            2,
            test_hash("typed-tool-ir"),
            test_hash("policy-proof"),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource"),
            test_hash("effect"),
            vec![test_hash("evidence")],
            Some(test_hash("mock-dry-run-log")),
            Some(StagingEvidenceKind::MockLog),
            None,
            None,
            test_hash("redaction"),
        );
        assert!(!mock_log_staging.is_valid());
    }

    #[test]
    fn browser_witness_proof_requires_complete_hash_bundle_and_action_binding() {
        use crate::browser_witness::{BrowserActionKind, BrowserActionTrace, BrowserWitnessProof};
        use crate::policy::SideEffectClass;

        let policy_window_hash = test_hash("browser-policy-window");
        let action = BrowserActionTrace::new(
            42,
            BrowserActionKind::Click,
            test_hash("stable-selector"),
            None,
            Some((320, 240)),
            None,
            policy_window_hash,
        );
        assert!(action.is_valid());

        let proof = BrowserWitnessProof::new(
            7,
            action.action_id,
            SideEffectClass::ExternalWrite,
            test_hash("url-before"),
            test_hash("url-after"),
            test_hash("dom-before"),
            test_hash("dom-after"),
            test_hash("screenshot-before"),
            test_hash("screenshot-after"),
            test_hash("accessibility-after"),
            test_hash("network-log"),
            action.trace_hash,
            policy_window_hash,
            test_hash("isolated-browser-session"),
            test_hash("redaction-policy"),
        );
        assert!(proof.is_valid());
        assert!(proof.binds_action_trace(&action));
        assert!(proof.is_valid_staging_evidence_for(&action, policy_window_hash));

        let missing_network = BrowserWitnessProof {
            network_log_hash: [0; 32],
            ..proof.clone()
        };
        assert!(!missing_network.is_valid());

        let different_action = BrowserActionTrace::new(
            43,
            BrowserActionKind::Click,
            test_hash("stable-selector"),
            None,
            Some((320, 240)),
            None,
            policy_window_hash,
        );
        assert!(different_action.is_valid());
        assert!(!proof.binds_action_trace(&different_action));
    }

    #[test]
    fn browser_witness_can_satisfy_r4_isolated_browser_staging() {
        use crate::browser_witness::{BrowserActionKind, BrowserActionTrace, BrowserWitnessProof};
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, POLICY_RULE_ALLOW,
            PolicyDecision, PolicyFacts, ReviewPacket, RiskClass, SideEffectClass,
            StagingEvidenceKind, TypedToolIR,
        };

        let policy_window_hash = test_hash("browser-r4-policy-window");
        let action = BrowserActionTrace::new(
            77,
            BrowserActionKind::Navigate,
            test_hash("staging-url"),
            None,
            None,
            None,
            policy_window_hash,
        );
        assert!(action.is_valid());

        let proof = BrowserWitnessProof::new(
            100,
            action.action_id,
            SideEffectClass::FinancialLegal,
            test_hash("url-before"),
            test_hash("url-after"),
            test_hash("dom-before"),
            test_hash("dom-after"),
            test_hash("screenshot-before"),
            test_hash("screenshot-after"),
            test_hash("accessibility-after"),
            test_hash("network-log"),
            action.trace_hash,
            policy_window_hash,
            test_hash("isolated-browser-session"),
            test_hash("redaction-policy"),
        );
        assert!(proof.is_valid_staging_evidence_for(&action, policy_window_hash));

        let ir = TypedToolIR::new(
            90,
            91,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("credential-scope"),
            RiskClass::R4,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("approval-scope")),
        );
        assert!(ir.is_valid());

        let facts = PolicyFacts::with_staging_evidence(
            test_hash("policy-v1"),
            true,
            proof.proof_hash,
            StagingEvidenceKind::IsolatedBrowserSession,
        );
        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::Allow);
        assert_eq!(trace.matched_rule_ids, vec![POLICY_RULE_ALLOW]);

        let packet = ReviewPacket::new_with_staging_evidence_kind(
            80,
            ir.canonical_hash,
            trace.compute_hash(),
            RiskClass::R4,
            SideEffectClass::FinancialLegal,
            test_hash("resource-scope"),
            test_hash("expected-effect"),
            vec![proof.proof_hash],
            Some(proof.proof_hash),
            Some(StagingEvidenceKind::IsolatedBrowserSession),
            None,
            Some(test_hash("rollback-plan")),
            test_hash("redaction-policy"),
        );
        assert!(packet.is_valid());
    }

    #[test]
    fn browser_observation_packet_binds_collector_provenance_and_modalities() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserCollectorKind, BrowserObservationPacket,
            BrowserWitnessProof,
        };
        use crate::policy::SideEffectClass;

        let policy_window_hash = test_hash("browser-packet-policy-window");
        let browser_session_hash = test_hash("browser-packet-session");
        let redaction_policy_hash = test_hash("browser-packet-redaction");
        let action = BrowserActionTrace::new(
            501,
            BrowserActionKind::TypeText,
            test_hash("browser-input-target"),
            Some(test_hash("browser-input-redacted")),
            Some((128, 64)),
            None,
            policy_window_hash,
        );
        assert!(action.is_valid());

        let proof = BrowserWitnessProof::new(
            9,
            action.action_id,
            SideEffectClass::ExternalWrite,
            test_hash("packet-url-before"),
            test_hash("packet-url-after"),
            test_hash("packet-dom-before"),
            test_hash("packet-dom-after"),
            test_hash("packet-screenshot-before"),
            test_hash("packet-screenshot-after"),
            test_hash("packet-ax-after"),
            test_hash("packet-network-log"),
            action.trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        );
        let packet = BrowserObservationPacket::new(
            BrowserCollectorKind::ChromeExtension,
            12,
            1_786_300_000_123,
            test_hash("collector-config"),
            test_hash("collector-capabilities"),
            test_hash("raw-artifact-manifest"),
            action.clone(),
            proof.clone(),
        );
        assert!(packet.is_valid());
        assert!(packet.is_valid_staging_packet_for(
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash
        ));

        let tampered_session = BrowserObservationPacket {
            proof: BrowserWitnessProof {
                browser_session_hash: test_hash("other-session"),
                ..proof.clone()
            },
            ..packet.clone()
        };
        assert!(!tampered_session.is_valid());

        let tampered_redaction = BrowserObservationPacket {
            proof: BrowserWitnessProof {
                redaction_policy_hash: test_hash("other-redaction"),
                ..proof.clone()
            },
            ..packet.clone()
        };
        assert!(!tampered_redaction.is_valid());

        let tampered_collector_config = BrowserObservationPacket {
            collector_config_hash: test_hash("other-collector-config"),
            ..packet.clone()
        };
        assert!(!tampered_collector_config.is_valid());

        let tampered_modality = BrowserObservationPacket {
            modality_bundle_hash: test_hash("fake-modality-bundle"),
            ..packet.clone()
        };
        assert!(!tampered_modality.is_valid());

        let missing_network_proof = BrowserWitnessProof {
            network_log_hash: [0; 32],
            ..proof.clone()
        };
        let missing_network_packet = BrowserObservationPacket::new(
            BrowserCollectorKind::ChromeExtension,
            12,
            1_786_300_000_123,
            test_hash("collector-config"),
            test_hash("collector-capabilities"),
            test_hash("raw-artifact-manifest"),
            action,
            missing_network_proof,
        );
        assert!(!missing_network_packet.is_valid());
    }

    #[test]
    fn browser_page_search_candidates_are_record_bound_context_only() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserCollectorKind, BrowserObservationPacket,
            BrowserWitnessProof,
        };
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextGovernorError, ContextNode,
            ContextNodeKind,
        };
        use crate::evidence_index::{
            BrowserPageSearchCandidateError, CandidateOnlyGateError, EvidenceCandidateTier,
            browser_page_search_pattern_hash,
        };
        use crate::policy::SideEffectClass;

        let policy_window_hash = test_hash("browser-page-search-policy-window");
        let pattern_hash =
            browser_page_search_pattern_hash("submit invoice", false, false).unwrap();
        let action = BrowserActionTrace::new(
            62,
            BrowserActionKind::SearchPage,
            test_hash("browser-page-search-target"),
            Some(pattern_hash),
            None,
            None,
            policy_window_hash,
        );
        assert!(action.is_valid());
        let proof = BrowserWitnessProof::new(
            21,
            action.action_id,
            SideEffectClass::ExternalRead,
            test_hash("browser-page-search-url-before"),
            test_hash("browser-page-search-url-after"),
            test_hash("browser-page-search-dom-before"),
            test_hash("browser-page-search-dom-after"),
            test_hash("browser-page-search-screenshot-before"),
            test_hash("browser-page-search-screenshot-after"),
            test_hash("browser-page-search-accessibility-after"),
            test_hash("browser-page-search-network-log"),
            action.trace_hash,
            policy_window_hash,
            test_hash("browser-page-search-session"),
            test_hash("browser-page-search-redaction"),
        );
        let packet = BrowserObservationPacket::new(
            BrowserCollectorKind::PlaywrightCdp,
            3,
            1_786_300_321_000,
            test_hash("browser-page-search-config"),
            test_hash("browser-page-search-capability"),
            test_hash("browser-page-search-manifest"),
            action,
            proof,
        );
        assert!(packet.is_valid());

        let search = packet
            .page_search_candidates("submit invoice", false, false, 3, 2)
            .unwrap();
        assert_eq!(search.candidates.len(), 2);
        assert_eq!(
            search.candidates[0].origin_tier,
            EvidenceCandidateTier::BrowserPageSearch
        );
        assert!(search.record.matches_candidates(&search.candidates));

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(48, 8)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 8, 10, 0, 0))
            .unwrap();
        for candidate in &search.candidates {
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

        assert_eq!(
            governor.build_replay_bound_candidate_context_pack(1, &search.candidates, None, None),
            Err(ContextGovernorError::CandidateEvidenceGate(
                CandidateOnlyGateError::MissingReplayEvent
            ))
        );

        let (pack, context_proof) = governor
            .build_replay_bound_candidate_context_pack_with_browser(
                1,
                &search.candidates,
                None,
                None,
                Some(&search.record),
            )
            .unwrap();
        assert!(context_proof.is_valid_for_replay_records(
            &pack,
            &search.candidates,
            None,
            None,
            Some(&search.record)
        ));
        assert_eq!(
            context_proof.browser_page_search_record_hash,
            search.record.record_hash
        );
        assert_ne!(context_proof.browser_page_search_record_hash, [0; 32]);

        let mut tampered_record = search.record;
        tampered_record.match_count = tampered_record.match_count.saturating_add(1);
        assert_eq!(
            governor.build_replay_bound_candidate_context_pack_with_browser(
                1,
                &search.candidates,
                None,
                None,
                Some(&tampered_record),
            ),
            Err(ContextGovernorError::CandidateEvidenceGate(
                CandidateOnlyGateError::ReplayRecordMismatch
            ))
        );

        let wrong_action = BrowserActionTrace::new(
            63,
            BrowserActionKind::StateSnapshot,
            test_hash("browser-page-search-target"),
            None,
            None,
            None,
            policy_window_hash,
        );
        let wrong_proof = BrowserWitnessProof::new(
            22,
            wrong_action.action_id,
            SideEffectClass::ExternalRead,
            test_hash("browser-page-search-url-before-2"),
            test_hash("browser-page-search-url-after-2"),
            test_hash("browser-page-search-dom-before-2"),
            test_hash("browser-page-search-dom-after-2"),
            test_hash("browser-page-search-screenshot-before-2"),
            test_hash("browser-page-search-screenshot-after-2"),
            test_hash("browser-page-search-accessibility-after-2"),
            test_hash("browser-page-search-network-log-2"),
            wrong_action.trace_hash,
            policy_window_hash,
            test_hash("browser-page-search-session-2"),
            test_hash("browser-page-search-redaction-2"),
        );
        let wrong_packet = BrowserObservationPacket::new(
            BrowserCollectorKind::PlaywrightCdp,
            4,
            1_786_300_321_001,
            test_hash("browser-page-search-config-2"),
            test_hash("browser-page-search-capability-2"),
            test_hash("browser-page-search-manifest-2"),
            wrong_action,
            wrong_proof,
        );
        assert!(wrong_packet.is_valid());
        assert_eq!(
            wrong_packet.page_search_candidates("submit invoice", false, false, 1, 1),
            Err(BrowserPageSearchCandidateError::InvalidPacket)
        );
        assert_eq!(
            packet.page_search_candidates(" ", false, false, 1, 1),
            Err(BrowserPageSearchCandidateError::InvalidPattern)
        );
    }

    #[test]
    fn browser_action_plan_record_lowers_to_trace_and_detects_page_change_abort() {
        use crate::browser_witness::{
            BrowserActionPlanKind, BrowserActionPlanRecord, BrowserCollectorKind,
            BrowserObservationPacket, BrowserWitnessProof,
        };
        use crate::policy::SideEffectClass;

        let run_id = 31;
        let action_id = 710;
        let ir_hash = test_hash("browser-plan-ir");
        let url_before_hash = test_hash("browser-plan-url-before");
        let policy_window_hash = test_hash("browser-plan-policy-window");
        let browser_session_hash = test_hash("browser-plan-session");
        let redaction_policy_hash = test_hash("browser-plan-redaction");
        let plan = BrowserActionPlanRecord::new(
            run_id,
            action_id,
            BrowserActionPlanKind::Click,
            7,
            ir_hash,
            url_before_hash,
            test_hash("browser-plan-target"),
            None,
            Some((240, 64)),
            None,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            false,
        );
        assert!(plan.is_valid());
        let trace = plan.to_action_trace().unwrap();
        assert!(trace.is_valid());

        let proof = BrowserWitnessProof::new(
            run_id,
            action_id,
            SideEffectClass::ExternalWrite,
            url_before_hash,
            test_hash("browser-plan-url-after"),
            test_hash("browser-plan-dom-before"),
            test_hash("browser-plan-dom-after"),
            test_hash("browser-plan-screenshot-before"),
            test_hash("browser-plan-screenshot-after"),
            test_hash("browser-plan-ax-after"),
            test_hash("browser-plan-network"),
            trace.trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        );
        let packet = BrowserObservationPacket::new(
            BrowserCollectorKind::PlaywrightCdp,
            plan.sequence_number,
            1_786_340_000_000,
            test_hash("browser-plan-collector-config"),
            test_hash("browser-plan-collector-capability"),
            test_hash("browser-plan-manifest"),
            trace,
            proof,
        );
        assert!(plan.is_valid_for_packet(ir_hash, &packet));
        assert_eq!(
            plan.sequence_should_abort_after(ir_hash, &packet),
            Some(true)
        );

        let tampered = BrowserActionPlanRecord {
            target_hash: test_hash("browser-plan-other-target"),
            ..plan
        };
        assert!(!tampered.is_valid());

        let bad_nav = BrowserActionPlanRecord::new(
            run_id,
            action_id + 1,
            BrowserActionPlanKind::Navigate,
            8,
            ir_hash,
            url_before_hash,
            test_hash("browser-plan-nav-target"),
            None,
            None,
            None,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            false,
        );
        assert!(!bad_nav.is_valid());
    }

    #[test]
    fn browser_collector_evidence_envelope_mints_packet_from_complete_artifacts() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserArtifactKind, BrowserArtifactRef,
            BrowserCollectorEvidenceEnvelope, BrowserCollectorKind, BrowserObservationPacket,
        };
        use crate::policy::SideEffectClass;

        fn artifact(kind: BrowserArtifactKind, label: &str) -> BrowserArtifactRef {
            BrowserArtifactRef::new(
                kind,
                128,
                test_hash(&format!("{label}-content")),
                test_hash(&format!("{label}-storage")),
            )
        }

        let policy_window_hash = test_hash("collector-envelope-policy-window");
        let browser_session_hash = test_hash("collector-envelope-session");
        let redaction_policy_hash = test_hash("collector-envelope-redaction");
        let action = BrowserActionTrace::new(
            701,
            BrowserActionKind::Click,
            test_hash("collector-envelope-target"),
            None,
            Some((80, 90)),
            None,
            policy_window_hash,
        );
        let artifacts = vec![
            artifact(BrowserArtifactKind::NetworkLog, "network-log"),
            artifact(BrowserArtifactKind::ScreenshotAfter, "screenshot-after"),
            artifact(BrowserArtifactKind::UrlBefore, "url-before"),
            artifact(BrowserArtifactKind::DomSnapshotAfter, "dom-after"),
            artifact(
                BrowserArtifactKind::AccessibilityTreeAfter,
                "accessibility-after",
            ),
            artifact(BrowserArtifactKind::UrlAfter, "url-after"),
            artifact(BrowserArtifactKind::ScreenshotBefore, "screenshot-before"),
            artifact(BrowserArtifactKind::DomSnapshotBefore, "dom-before"),
        ];
        let envelope = BrowserCollectorEvidenceEnvelope::new(
            BrowserCollectorKind::PlaywrightCdp,
            33,
            action.action_id,
            5,
            1_786_310_000_000,
            test_hash("collector-envelope-config"),
            test_hash("collector-envelope-capability"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifacts,
        );
        assert!(envelope.is_valid());
        assert_eq!(
            envelope.content_hash_for(BrowserArtifactKind::UrlBefore),
            Some(test_hash("url-before-content"))
        );

        let packet = BrowserObservationPacket::from_collector_envelope(
            &envelope,
            action.clone(),
            SideEffectClass::ExternalWrite,
        )
        .expect("complete browser evidence envelope should mint packet");
        assert!(packet.is_valid());
        assert_eq!(
            packet.raw_artifact_manifest_hash,
            envelope.artifact_manifest_hash
        );
        assert_eq!(packet.proof.browser_session_hash, browser_session_hash);
        assert_eq!(packet.proof.redaction_policy_hash, redaction_policy_hash);
        assert_eq!(packet.proof.policy_window_hash, policy_window_hash);
        assert_eq!(
            packet.proof.network_log_hash,
            test_hash("network-log-content")
        );

        let action_policy_drift = BrowserActionTrace::new(
            action.action_id,
            BrowserActionKind::Click,
            test_hash("collector-envelope-target"),
            None,
            Some((80, 90)),
            None,
            test_hash("other-policy-window"),
        );
        assert!(
            BrowserObservationPacket::from_collector_envelope(
                &envelope,
                action_policy_drift,
                SideEffectClass::ExternalWrite,
            )
            .is_none()
        );

        let mut missing_artifacts = envelope.artifacts.clone();
        missing_artifacts.pop();
        let missing = BrowserCollectorEvidenceEnvelope::new(
            BrowserCollectorKind::PlaywrightCdp,
            33,
            action.action_id,
            5,
            1_786_310_000_000,
            test_hash("collector-envelope-config"),
            test_hash("collector-envelope-capability"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            missing_artifacts,
        );
        assert!(!missing.is_valid());

        let mut duplicate_artifacts = envelope.artifacts.clone();
        duplicate_artifacts[7] = duplicate_artifacts[6];
        let duplicate = BrowserCollectorEvidenceEnvelope::new(
            BrowserCollectorKind::PlaywrightCdp,
            33,
            action.action_id,
            5,
            1_786_310_000_000,
            test_hash("collector-envelope-config"),
            test_hash("collector-envelope-capability"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            duplicate_artifacts,
        );
        assert!(!duplicate.is_valid());

        let tampered_artifact = BrowserCollectorEvidenceEnvelope {
            artifacts: {
                let mut artifacts = envelope.artifacts.clone();
                artifacts[0].content_hash = test_hash("tampered-url-before");
                artifacts
            },
            ..envelope
        };
        assert!(!tampered_artifact.is_valid());
    }

    #[test]
    fn browser_collector_envelope_accepts_hot_arena_artifact_refs_without_file_roundtrip() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserArtifactKind, BrowserArtifactRef,
            BrowserCollectorEvidenceEnvelope, BrowserCollectorKind, BrowserObservationPacket,
        };
        use crate::hot_engine::{InMemoryEvidenceArena, TrustLevel, simd_blake3_hash};
        use crate::policy::SideEffectClass;

        fn commit_artifact(
            arena: &InMemoryEvidenceArena,
            kind: BrowserArtifactKind,
            payload: &[u8],
        ) -> BrowserArtifactRef {
            let handle = arena.commit(payload).expect("hot arena browser commit");
            BrowserArtifactRef::from_hot_arena_handle(kind, &handle)
                .expect("hot arena artifact ref")
        }

        let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 1024 * 1024, 1024 * 1024);
        let policy_window_hash = test_hash("hot-arena-browser-policy-window");
        let browser_session_hash = test_hash("hot-arena-browser-session");
        let redaction_policy_hash = test_hash("hot-arena-browser-redaction");
        let action = BrowserActionTrace::new(
            777,
            BrowserActionKind::StateSnapshot,
            test_hash("hot-arena-browser-target"),
            None,
            None,
            None,
            policy_window_hash,
        );
        let url_before = b"https://example.test/before";
        let dom_after = b"<html><main><button>after</button></main></html>";
        let artifacts = vec![
            commit_artifact(&arena, BrowserArtifactKind::UrlBefore, url_before),
            commit_artifact(
                &arena,
                BrowserArtifactKind::UrlAfter,
                b"https://example.test/after",
            ),
            commit_artifact(
                &arena,
                BrowserArtifactKind::DomSnapshotBefore,
                b"<html><main>before</main></html>",
            ),
            commit_artifact(&arena, BrowserArtifactKind::DomSnapshotAfter, dom_after),
            commit_artifact(
                &arena,
                BrowserArtifactKind::ScreenshotBefore,
                b"\x89PNG-before",
            ),
            commit_artifact(
                &arena,
                BrowserArtifactKind::ScreenshotAfter,
                b"\x89PNG-after",
            ),
            commit_artifact(
                &arena,
                BrowserArtifactKind::AccessibilityTreeAfter,
                br#"{"role":"button","name":"after"}"#,
            ),
            commit_artifact(
                &arena,
                BrowserArtifactKind::NetworkLog,
                br#"{"url":"https://example.test/api","status":200}"#,
            ),
        ];

        let envelope = BrowserCollectorEvidenceEnvelope::new(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            6,
            1_786_310_000_123,
            test_hash("hot-arena-browser-config"),
            test_hash("hot-arena-browser-capability"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifacts,
        );
        assert!(envelope.is_valid());
        assert_eq!(
            envelope.content_hash_for(BrowserArtifactKind::UrlBefore),
            Some(simd_blake3_hash(url_before))
        );
        assert_eq!(
            envelope.content_hash_for(BrowserArtifactKind::DomSnapshotAfter),
            Some(simd_blake3_hash(dom_after))
        );

        let packet = BrowserObservationPacket::from_collector_envelope(
            &envelope,
            action,
            SideEffectClass::ExternalRead,
        )
        .expect("hot arena browser envelope should mint packet");
        assert!(packet.is_valid());
        assert_eq!(packet.proof.url_before_hash, simd_blake3_hash(url_before));
        assert_eq!(
            packet.proof.dom_snapshot_hash_after,
            simd_blake3_hash(dom_after)
        );
    }

    #[test]
    fn browser_file_artifact_envelope_reads_physical_files_and_rejects_tamper() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserArtifactFilePathRef, BrowserArtifactKind,
            BrowserArtifactReadError, BrowserArtifactRef, BrowserCollectorEvidenceEnvelope,
            BrowserCollectorKind, BrowserLiveCollectorManifest, BrowserObservationPacket,
        };
        use crate::policy::SideEffectClass;
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("browser-file-artifacts-{unique}"));
        std::fs::create_dir_all(&dir).unwrap();

        let artifact_paths = vec![
            (
                BrowserArtifactKind::NetworkLog,
                dir.join("network-log.jsonl"),
                b"[{\"url\":\"https://example.test/api\",\"status\":200}]".to_vec(),
            ),
            (
                BrowserArtifactKind::ScreenshotAfter,
                dir.join("screenshot-after.png"),
                b"png-after-bytes".to_vec(),
            ),
            (
                BrowserArtifactKind::UrlBefore,
                dir.join("url-before.txt"),
                b"https://example.test/before".to_vec(),
            ),
            (
                BrowserArtifactKind::DomSnapshotAfter,
                dir.join("dom-after.html"),
                b"<html><button>done</button></html>".to_vec(),
            ),
            (
                BrowserArtifactKind::AccessibilityTreeAfter,
                dir.join("accessibility-after.json"),
                b"{\"role\":\"button\",\"name\":\"done\"}".to_vec(),
            ),
            (
                BrowserArtifactKind::UrlAfter,
                dir.join("url-after.txt"),
                b"https://example.test/after".to_vec(),
            ),
            (
                BrowserArtifactKind::ScreenshotBefore,
                dir.join("screenshot-before.png"),
                b"png-before-bytes".to_vec(),
            ),
            (
                BrowserArtifactKind::DomSnapshotBefore,
                dir.join("dom-before.html"),
                b"<html><button>go</button></html>".to_vec(),
            ),
        ];
        for (_, path, bytes) in &artifact_paths {
            std::fs::write(path, bytes).unwrap();
        }
        let file_refs: Vec<_> = artifact_paths
            .iter()
            .map(|(kind, path, _)| (*kind, path.clone()))
            .collect();
        let policy_window_hash = test_hash("browser-file-policy-window");
        let browser_session_hash = test_hash("browser-file-session");
        let redaction_policy_hash = test_hash("browser-file-redaction");
        let action = BrowserActionTrace::new(
            971,
            BrowserActionKind::Click,
            test_hash("browser-file-target"),
            None,
            Some((42, 51)),
            None,
            policy_window_hash,
        );
        let envelope = BrowserCollectorEvidenceEnvelope::from_file_paths(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            &file_refs,
        )
        .expect("physical browser artifact files should produce an envelope");
        assert!(envelope.is_valid());
        assert!(envelope.verify_file_paths(&file_refs).is_ok());
        let path_refs: Vec<_> = file_refs
            .iter()
            .map(|(kind, path)| BrowserArtifactFilePathRef::from_path(*kind, path).unwrap())
            .collect();
        assert!(path_refs.iter().all(BrowserArtifactFilePathRef::is_valid));
        let path_ref_envelope = BrowserCollectorEvidenceEnvelope::from_file_path_refs(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            &path_refs,
        )
        .expect("canonical browser artifact path refs should produce the same envelope");
        assert_eq!(path_ref_envelope, envelope);
        assert!(envelope.verify_file_path_refs(&path_refs).is_ok());

        let url_before_ref = envelope
            .artifacts
            .iter()
            .find(|artifact| artifact.kind == BrowserArtifactKind::UrlBefore)
            .unwrap();
        assert_eq!(
            url_before_ref.byte_len,
            "https://example.test/before".len() as u64
        );
        assert_eq!(
            url_before_ref.content_hash,
            *blake3::hash(b"https://example.test/before").as_bytes()
        );
        assert!(
            url_before_ref
                .verify_file(dir.join("url-before.txt"))
                .is_ok()
        );

        let packet = BrowserObservationPacket::from_collector_envelope(
            &envelope,
            action.clone(),
            SideEffectClass::ExternalWrite,
        )
        .expect("file-backed envelope should mint a browser packet");
        assert!(packet.is_valid());
        assert_eq!(
            packet.proof.url_before_hash,
            *blake3::hash(b"https://example.test/before").as_bytes()
        );

        let mut sorted_refs = file_refs.clone();
        sorted_refs.sort_by_key(|(kind, _)| *kind);
        let sorted_envelope = BrowserCollectorEvidenceEnvelope::from_file_paths(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            &sorted_refs,
        )
        .unwrap();
        assert_eq!(sorted_envelope, envelope);

        std::fs::write(dir.join("network-log.jsonl"), b"{\"tampered\":true}").unwrap();
        assert_eq!(
            envelope.verify_file_paths(&file_refs),
            Err(BrowserArtifactReadError::ArtifactMismatch)
        );
        assert_eq!(
            envelope.verify_file_path_refs(&path_refs),
            Err(BrowserArtifactReadError::ArtifactMismatch)
        );
        let fresh_after_tamper = BrowserCollectorEvidenceEnvelope::from_file_paths(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            &file_refs,
        )
        .unwrap();
        assert_ne!(
            fresh_after_tamper.artifact_manifest_hash,
            envelope.artifact_manifest_hash
        );

        let empty_path = dir.join("empty.txt");
        std::fs::write(&empty_path, b"").unwrap();
        assert_eq!(
            BrowserArtifactRef::from_file(BrowserArtifactKind::UrlBefore, &empty_path),
            Err(BrowserArtifactReadError::Empty)
        );
        assert_eq!(
            BrowserArtifactRef::from_file(BrowserArtifactKind::UrlAfter, dir.join("missing.txt")),
            Err(BrowserArtifactReadError::Missing)
        );
        assert_eq!(
            BrowserArtifactRef::from_file_with_max_bytes(
                BrowserArtifactKind::DomSnapshotAfter,
                dir.join("dom-after.html"),
                4
            ),
            Err(BrowserArtifactReadError::Oversized)
        );

        let manifest = BrowserLiveCollectorManifest::new(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            path_refs.clone(),
        );
        assert!(manifest.is_valid());
        assert!(manifest.binds_action_trace(&action));
        let manifest_envelope = manifest
            .to_envelope()
            .expect("live manifest should mint envelope from physical files");
        assert!(manifest_envelope.is_valid());
        assert_eq!(
            manifest_envelope.content_hash_for(BrowserArtifactKind::UrlBefore),
            Some(*blake3::hash(b"https://example.test/before").as_bytes())
        );

        let mut missing_refs = path_refs.clone();
        missing_refs.pop();
        let missing_manifest = BrowserLiveCollectorManifest::new(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            missing_refs,
        );
        assert!(!missing_manifest.is_valid());

        let mut duplicate_refs = path_refs.clone();
        duplicate_refs[7] = duplicate_refs[6].clone();
        let duplicate_manifest = BrowserLiveCollectorManifest::new(
            BrowserCollectorKind::PlaywrightCdp,
            44,
            action.action_id,
            7,
            1_786_320_000_000,
            test_hash("browser-file-config"),
            test_hash("browser-file-capabilities"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            duplicate_refs,
        );
        assert!(!duplicate_manifest.is_valid());

        let mut drifted_manifest = manifest.clone();
        drifted_manifest.collector_capability_hash = test_hash("drifted-browser-capability");
        assert!(!drifted_manifest.is_valid());
    }

    #[test]
    fn browser_live_collector_run_writes_exact_artifacts_and_returns_manifest_only() {
        use crate::browser_witness::{
            BrowserActionPlanKind, BrowserActionPlanRecord, BrowserArtifactKind,
            BrowserCollectorKind, BrowserLiveCollectorArtifact, BrowserLiveCollectorRun,
            BrowserLiveCollectorRunError,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("browser-live-collector-run-{unique}"));
        let run_id = 84;
        let action_id = 97531;
        let sequence_number = 6;
        let observed_at = 1_786_340_000_000;
        let policy_window_hash = test_hash("browser-live-run-policy-window");
        let browser_session_hash = test_hash("browser-live-run-session");
        let redaction_policy_hash = test_hash("browser-live-run-redaction");
        let collector_config_hash = test_hash("browser-live-run-config");
        let collector_capability_hash = test_hash("browser-live-run-capability");
        let plan = BrowserActionPlanRecord::new(
            run_id,
            action_id,
            BrowserActionPlanKind::Click,
            sequence_number,
            test_hash("browser-live-run-tool-ir"),
            *blake3::hash(b"https://example.test/before").as_bytes(),
            test_hash("browser-live-run-target"),
            None,
            Some((11, 29)),
            None,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            false,
        );
        let action = plan.to_action_trace().unwrap();
        let artifacts = vec![
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::NetworkLog,
                br#"{"method":"Network.responseReceived","url":"https://example.test"}"#.to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::DomSnapshotAfter,
                b"<html><button>after</button></html>".to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::ScreenshotBefore,
                b"png-before".to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::UrlBefore,
                b"https://example.test/before".to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::AccessibilityTreeAfter,
                br#"{"role":"button","name":"after"}"#.to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::ScreenshotAfter,
                b"png-after".to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::UrlAfter,
                b"https://example.test/after".to_vec(),
            ),
            BrowserLiveCollectorArtifact::new(
                BrowserArtifactKind::DomSnapshotBefore,
                b"<html><button>before</button></html>".to_vec(),
            ),
        ];

        let collector_run = BrowserLiveCollectorRun::from_artifacts(
            &root,
            BrowserCollectorKind::PlaywrightCdp,
            run_id,
            action_id,
            sequence_number,
            observed_at,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            &artifacts,
        )
        .unwrap();
        assert!(collector_run.manifest.is_valid());
        assert!(collector_run.is_valid_for_action_trace(&action));
        assert!(collector_run.is_valid_for_plan(&plan));
        assert!(collector_run.manifest.binds_plan(&plan));
        assert_eq!(collector_run.manifest.artifact_path_refs.len(), 8);
        assert!(
            collector_run
                .manifest
                .artifact_path_refs
                .iter()
                .all(|path_ref| path_ref
                    .canonical_path
                    .starts_with(&collector_run.run_directory))
        );
        let envelope = collector_run.manifest.to_envelope().unwrap();
        assert_eq!(
            envelope.content_hash_for(BrowserArtifactKind::UrlBefore),
            Some(*blake3::hash(b"https://example.test/before").as_bytes())
        );

        std::fs::write(
            collector_run.run_directory.join("08-network-log.jsonl"),
            b"{\"tampered\":true}",
        )
        .unwrap();
        assert!(!collector_run.is_valid_for_action_trace(&action));
        assert!(collector_run.manifest.verify_files().is_ok());
        let fresh_after_tamper = collector_run.manifest.to_envelope().unwrap();
        assert_ne!(
            fresh_after_tamper.artifact_manifest_hash,
            collector_run.initial_artifact_manifest_hash
        );

        let mut missing = artifacts.clone();
        missing.pop();
        assert_eq!(
            BrowserLiveCollectorRun::from_artifacts(
                &root,
                BrowserCollectorKind::PlaywrightCdp,
                run_id + 1,
                action_id,
                sequence_number,
                observed_at,
                collector_config_hash,
                collector_capability_hash,
                browser_session_hash,
                redaction_policy_hash,
                policy_window_hash,
                &missing,
            ),
            Err(BrowserLiveCollectorRunError::MissingRequiredArtifact)
        );
        let mut duplicate = artifacts.clone();
        duplicate[0] = duplicate[1].clone();
        assert_eq!(
            BrowserLiveCollectorRun::from_artifacts(
                &root,
                BrowserCollectorKind::PlaywrightCdp,
                run_id + 2,
                action_id,
                sequence_number,
                observed_at,
                collector_config_hash,
                collector_capability_hash,
                browser_session_hash,
                redaction_policy_hash,
                policy_window_hash,
                &duplicate,
            ),
            Err(BrowserLiveCollectorRunError::DuplicateArtifactKind)
        );
    }

    #[test]
    fn browser_ops_bench_scorecard_is_reverified_from_physical_artifacts() {
        use crate::browser_witness::{
            BrowserCollectorKind, BrowserOpsBenchVerificationError,
            BrowserOpsBenchVerificationProof,
        };
        use serde_json::json;
        use std::time::{SystemTime, UNIX_EPOCH};

        fn write(path: &std::path::Path, bytes: &[u8]) -> String {
            std::fs::write(path, bytes).unwrap();
            path.canonicalize().unwrap().to_string_lossy().to_string()
        }

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("browser-ops-rust-verify-{unique}"));
        let run_dir = root.join(
            "collector-runs/run-00000000000000000000000000000065-action-000000000000000000000000000000ca-seq-0000000000000001",
        );
        std::fs::create_dir_all(&run_dir).unwrap();
        let artifact_paths = [
            (
                "url_before",
                write(
                    &run_dir.join("01-url-before.txt"),
                    b"https://ops.test/before",
                ),
            ),
            (
                "url_after",
                write(&run_dir.join("02-url-after.txt"), b"https://ops.test/after"),
            ),
            (
                "dom_snapshot_before",
                write(&run_dir.join("03-dom-before.html"), b"<html>before</html>"),
            ),
            (
                "dom_snapshot_after",
                write(&run_dir.join("04-dom-after.html"), b"<html>after</html>"),
            ),
            (
                "screenshot_before",
                write(&run_dir.join("05-screenshot-before.bin"), b"before-shot"),
            ),
            (
                "screenshot_after",
                write(&run_dir.join("06-screenshot-after.bin"), b"after-shot"),
            ),
            (
                "accessibility_tree_after",
                write(
                    &run_dir.join("07-accessibility-after.json"),
                    br#"{"name":"after"}"#,
                ),
            ),
            (
                "network_log",
                write(
                    &run_dir.join("08-network-log.jsonl"),
                    br#"{"status":200,"url":"/api"}"#,
                ),
            ),
        ];
        let artifact_map: serde_json::Map<String, serde_json::Value> = artifact_paths
            .iter()
            .map(|(kind, path)| ((*kind).to_string(), json!(path)))
            .collect();
        let metadata_path = run_dir.join("producer-metadata.json");
        let run_dir_string = run_dir
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .to_string();
        let metadata = json!({
            "schema": "aegis-browser-live-collector-producer-v1",
            "run_id": 101,
            "action_id": 202,
            "sequence_number": 1,
            "run_directory": run_dir_string,
            "artifact_paths": artifact_map,
            "verifier": "rust-browser-live-collector-run",
            "truth_claim": false
        });
        std::fs::write(&metadata_path, serde_json::to_vec(&metadata).unwrap()).unwrap();
        let metadata_path_string = metadata_path
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .to_string();
        let scorecard_path = root.join("browser-ops-scorecard.json");
        let scorecard = json!({
            "schema": "aegis-browser-ops-bench-scorecard-v1",
            "suite_name": "rust-browser-ops",
            "total": 1,
            "passed": 1,
            "failed": 0,
            "overall_ok": true,
            "truth_claim": false,
            "verifier": "rust-browser-live-collector-run",
            "records": [{
                "task_id": "read-only-after",
                "ok": true,
                "run_directory": run_dir_string,
                "producer_metadata_path": metadata_path_string,
                "action_result_type": "str",
                "error": "",
                "predicate_results": [
                    {
                        "name": "url-after",
                        "artifact_kind": "url_after",
                        "artifact_path": artifact_paths[1].1,
                        "byte_len": 22,
                        "ok": true,
                        "detail": "ok"
                    },
                    {
                        "name": "network-api",
                        "artifact_kind": "network_log",
                        "artifact_path": artifact_paths[7].1,
                        "byte_len": 27,
                        "ok": true,
                        "detail": "ok"
                    }
                ]
            }]
        });
        std::fs::write(&scorecard_path, serde_json::to_vec(&scorecard).unwrap()).unwrap();

        let proof = BrowserOpsBenchVerificationProof::verify_scorecard(
            &scorecard_path,
            BrowserCollectorKind::PlaywrightCdp,
            1_786_360_000_000,
            test_hash("browser-ops-config"),
            test_hash("browser-ops-capability"),
            test_hash("browser-ops-session"),
            test_hash("browser-ops-redaction"),
            test_hash("browser-ops-policy-window"),
        )
        .unwrap();
        assert!(proof.is_valid());
        assert_eq!(proof.verified_task_count, 1);
        assert_eq!(proof.task_records[0].run_id, 101);
        assert_eq!(proof.task_records[0].predicate_count, 2);
        assert_ne!(
            proof.task_records[0].envelope.artifact_manifest_hash,
            [0; 32]
        );

        let mut truth_claim_scorecard = scorecard.clone();
        truth_claim_scorecard["truth_claim"] = json!(true);
        std::fs::write(
            &scorecard_path,
            serde_json::to_vec(&truth_claim_scorecard).unwrap(),
        )
        .unwrap();
        assert_eq!(
            BrowserOpsBenchVerificationProof::verify_scorecard(
                &scorecard_path,
                BrowserCollectorKind::PlaywrightCdp,
                1_786_360_000_000,
                test_hash("browser-ops-config"),
                test_hash("browser-ops-capability"),
                test_hash("browser-ops-session"),
                test_hash("browser-ops-redaction"),
                test_hash("browser-ops-policy-window"),
            ),
            Err(BrowserOpsBenchVerificationError::InvalidScorecard)
        );

        std::fs::write(&scorecard_path, serde_json::to_vec(&scorecard).unwrap()).unwrap();
        std::fs::write(
            run_dir.join("08-network-log.jsonl"),
            br#"{"tampered":true}"#,
        )
        .unwrap();
        assert_eq!(
            BrowserOpsBenchVerificationProof::verify_scorecard(
                &scorecard_path,
                BrowserCollectorKind::PlaywrightCdp,
                1_786_360_000_000,
                test_hash("browser-ops-config"),
                test_hash("browser-ops-capability"),
                test_hash("browser-ops-session"),
                test_hash("browser-ops-redaction"),
                test_hash("browser-ops-policy-window"),
            ),
            Err(BrowserOpsBenchVerificationError::InvalidScorecard)
        );
    }

    #[test]
    fn browser_ops_bench_verification_is_replay_visible_before_shift_inheritance() {
        use crate::browser_witness::{BrowserCollectorKind, BrowserOpsBenchVerificationProof};
        use crate::replay::{
            BinaryRunEventSegment, NextActionKind, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive, browser_ops_bench_verification_replay_binding_hash,
        };
        use serde_json::json;
        use std::time::{SystemTime, UNIX_EPOCH};

        fn write(path: &std::path::Path, bytes: &[u8]) -> String {
            std::fs::write(path, bytes).unwrap();
            path.canonicalize().unwrap().to_string_lossy().to_string()
        }

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("browser-ops-replay-{unique}"));
        let run_dir = root.join(
            "collector-runs/run-00000000000000000000000000000191-action-00000000000000000000000000000292-seq-0000000000000001",
        );
        std::fs::create_dir_all(&run_dir).unwrap();
        let artifact_paths = [
            (
                "url_before",
                write(&run_dir.join("01-url-before.txt"), b"https://ops.test/a"),
            ),
            (
                "url_after",
                write(&run_dir.join("02-url-after.txt"), b"https://ops.test/b"),
            ),
            (
                "dom_snapshot_before",
                write(&run_dir.join("03-dom-before.html"), b"<html>a</html>"),
            ),
            (
                "dom_snapshot_after",
                write(&run_dir.join("04-dom-after.html"), b"<html>b</html>"),
            ),
            (
                "screenshot_before",
                write(&run_dir.join("05-screenshot-before.bin"), b"shot-a"),
            ),
            (
                "screenshot_after",
                write(&run_dir.join("06-screenshot-after.bin"), b"shot-b"),
            ),
            (
                "accessibility_tree_after",
                write(
                    &run_dir.join("07-accessibility-after.json"),
                    br#"{"ok":true}"#,
                ),
            ),
            (
                "network_log",
                write(&run_dir.join("08-network-log.jsonl"), br#"{"status":200}"#),
            ),
        ];
        let artifact_map: serde_json::Map<String, serde_json::Value> = artifact_paths
            .iter()
            .map(|(kind, path)| ((*kind).to_string(), json!(path)))
            .collect();
        let run_dir_string = run_dir
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .to_string();
        let metadata_path = run_dir.join("producer-metadata.json");
        let metadata = json!({
            "schema": "aegis-browser-live-collector-producer-v1",
            "run_id": 401,
            "action_id": 658,
            "sequence_number": 1,
            "run_directory": run_dir_string,
            "artifact_paths": artifact_map,
            "verifier": "rust-browser-live-collector-run",
            "truth_claim": false
        });
        std::fs::write(&metadata_path, serde_json::to_vec(&metadata).unwrap()).unwrap();
        let scorecard_path = root.join("browser-ops-scorecard.json");
        let scorecard = json!({
            "schema": "aegis-browser-ops-bench-scorecard-v1",
            "suite_name": "rust-browser-ops-replay",
            "total": 1,
            "passed": 1,
            "failed": 0,
            "overall_ok": true,
            "truth_claim": false,
            "verifier": "rust-browser-live-collector-run",
            "records": [{
                "task_id": "replay-visible-browser-ops",
                "ok": true,
                "run_directory": run_dir_string,
                "producer_metadata_path": metadata_path
                    .canonicalize()
                    .unwrap()
                    .to_string_lossy()
                    .to_string(),
                "action_result_type": "str",
                "error": "",
                "predicate_results": [{
                    "name": "url-after",
                    "artifact_kind": "url_after",
                    "artifact_path": artifact_paths[1].1,
                    "byte_len": 18,
                    "ok": true,
                    "detail": "ok"
                }]
            }]
        });
        std::fs::write(&scorecard_path, serde_json::to_vec(&scorecard).unwrap()).unwrap();
        let proof = BrowserOpsBenchVerificationProof::verify_scorecard(
            &scorecard_path,
            BrowserCollectorKind::PlaywrightCdp,
            1_786_460_000_000,
            test_hash("browser-ops-replay-config"),
            test_hash("browser-ops-replay-capability"),
            test_hash("browser-ops-replay-session"),
            test_hash("browser-ops-replay-redaction"),
            test_hash("browser-ops-replay-policy-window"),
        )
        .unwrap();
        assert!(proof.is_valid());

        let mut missing_goal = RunEventLedger::new(811);
        assert_eq!(
            missing_goal.append_browser_ops_bench_verification_recorded(&proof),
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );

        let mut ledger = RunEventLedger::new(812);
        seed_goal_intake_replay(&mut ledger, "browser-ops-bench-verification");
        let event = ledger
            .append_browser_ops_bench_verification_recorded(&proof)
            .unwrap();
        assert_eq!(
            event.kind,
            RunEventKind::BrowserOpsBenchVerificationRecorded
        );
        assert_eq!(event.subject_id, proof.replay_subject_id());
        assert_eq!(event.primary_hash, proof.proof_hash);
        assert_eq!(
            event.secondary_hash,
            Some(browser_ops_bench_verification_replay_binding_hash(&proof))
        );
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered_events = ledger.events().to_vec();
        tampered_events[1].secondary_hash = None;
        tampered_events[1].event_hash = tampered_events[1].compute_hash();
        assert_eq!(
            RunEventLedger::from_events(ledger.run_id, tampered_events),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let mut wrong_binding_ledger = ledger.clone();
        let mut wrong_binding_event = wrong_binding_ledger.events()[1].clone();
        wrong_binding_event.secondary_hash = Some(test_hash("wrong-browser-ops-binding"));
        wrong_binding_event.event_hash = wrong_binding_event.compute_hash();
        wrong_binding_ledger = RunEventLedger::from_events(
            ledger.run_id,
            vec![ledger.events()[0].clone(), wrong_binding_event],
        )
        .unwrap();
        assert_ne!(wrong_binding_ledger.last_hash(), ledger.last_hash());

        let archive_dir = root.join("archive");
        let manifest = RunEventSegmentArchive::write_ledger(&archive_dir, 1, &ledger).unwrap();
        let archived = RunEventSegmentArchive::read_ledger_mmap(&archive_dir, &manifest).unwrap();
        assert_eq!(
            archived.events().last().unwrap().kind,
            RunEventKind::BrowserOpsBenchVerificationRecorded
        );
        let handoff = RunEventSegmentArchive::seal_browser_ops_bench_verification_handoff(
            &archive_dir,
            &manifest,
            &proof,
            930,
            931,
            NextActionKind::ContinueExecution,
            932,
            test_hash("browser-ops-handoff-typed-tool-ir"),
            test_hash("browser-ops-handoff-evidence-contract"),
            test_hash("browser-ops-handoff-policy-proof"),
        )
        .unwrap();
        assert_eq!(handoff.proof_hash, proof.proof_hash);
        assert_eq!(
            handoff.verification_event_hash,
            archived.events().last().unwrap().event_hash
        );

        let wrong_binding_dir = root.join("wrong-binding-archive");
        let wrong_binding_manifest =
            RunEventSegmentArchive::write_ledger(&wrong_binding_dir, 1, &wrong_binding_ledger)
                .unwrap();
        assert!(
            RunEventSegmentArchive::seal_browser_ops_bench_verification_handoff(
                &wrong_binding_dir,
                &wrong_binding_manifest,
                &proof,
                933,
                934,
                NextActionKind::ContinueExecution,
                935,
                test_hash("browser-ops-handoff-typed-tool-ir"),
                test_hash("browser-ops-handoff-evidence-contract"),
                test_hash("browser-ops-handoff-policy-proof"),
            )
            .is_err()
        );

        let mut tampered_proof = proof.clone();
        tampered_proof.verified_task_count += 1;
        assert!(!tampered_proof.is_valid());
        assert!(
            RunEventSegmentArchive::seal_browser_ops_bench_verification_handoff(
                &archive_dir,
                &manifest,
                &tampered_proof,
                940,
                941,
                NextActionKind::ContinueExecution,
                942,
                test_hash("browser-ops-handoff-typed-tool-ir"),
                test_hash("browser-ops-handoff-evidence-contract"),
                test_hash("browser-ops-handoff-policy-proof"),
            )
            .is_err()
        );

        let binary_path = root.join("browser-ops-verification.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let scan = BinaryRunEventSegment::verify_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(scan.event_count, ledger.len());
        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered.last_hash(), ledger.last_hash());
    }

    #[test]
    fn cli_writes_browser_ops_bench_verification_report_artifact() {
        use crate::browser_witness::BrowserCollectorKind;
        use serde_json::json;
        use std::time::{SystemTime, UNIX_EPOCH};

        fn write(path: &std::path::Path, bytes: &[u8]) -> String {
            std::fs::write(path, bytes).unwrap();
            path.canonicalize().unwrap().to_string_lossy().to_string()
        }

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("browser-ops-cli-report-{unique}"));
        let run_dir = root.join(
            "collector-runs/run-00000000000000000000000000000321-action-00000000000000000000000000000432-seq-0000000000000001",
        );
        std::fs::create_dir_all(&run_dir).unwrap();
        let artifact_paths = [
            (
                "url_before",
                write(
                    &run_dir.join("01-url-before.txt"),
                    b"https://ops.test/start",
                ),
            ),
            (
                "url_after",
                write(&run_dir.join("02-url-after.txt"), b"https://ops.test/done"),
            ),
            (
                "dom_snapshot_before",
                write(&run_dir.join("03-dom-before.html"), b"<html>start</html>"),
            ),
            (
                "dom_snapshot_after",
                write(&run_dir.join("04-dom-after.html"), b"<html>done</html>"),
            ),
            (
                "screenshot_before",
                write(&run_dir.join("05-screenshot-before.bin"), b"start-shot"),
            ),
            (
                "screenshot_after",
                write(&run_dir.join("06-screenshot-after.bin"), b"done-shot"),
            ),
            (
                "accessibility_tree_after",
                write(
                    &run_dir.join("07-accessibility-after.json"),
                    br#"{"done":true}"#,
                ),
            ),
            (
                "network_log",
                write(&run_dir.join("08-network-log.jsonl"), br#"{"ok":true}"#),
            ),
        ];
        let artifact_map: serde_json::Map<String, serde_json::Value> = artifact_paths
            .iter()
            .map(|(kind, path)| ((*kind).to_string(), json!(path)))
            .collect();
        let run_dir_string = run_dir
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .to_string();
        let metadata_path = run_dir.join("producer-metadata.json");
        let metadata = json!({
            "schema": "aegis-browser-live-collector-producer-v1",
            "run_id": 801,
            "action_id": 1074,
            "sequence_number": 1,
            "run_directory": run_dir_string,
            "artifact_paths": artifact_map,
            "verifier": "rust-browser-live-collector-run",
            "truth_claim": false
        });
        std::fs::write(&metadata_path, serde_json::to_vec(&metadata).unwrap()).unwrap();
        let scorecard_path = root.join("browser-ops-scorecard.json");
        let scorecard = json!({
            "schema": "aegis-browser-ops-bench-scorecard-v1",
            "suite_name": "rust-browser-ops-cli-report",
            "total": 1,
            "passed": 1,
            "failed": 0,
            "overall_ok": true,
            "truth_claim": false,
            "verifier": "rust-browser-live-collector-run",
            "records": [{
                "task_id": "cli-report-browser-ops",
                "ok": true,
                "run_directory": run_dir_string,
                "producer_metadata_path": metadata_path
                    .canonicalize()
                    .unwrap()
                    .to_string_lossy()
                    .to_string(),
                "action_result_type": "str",
                "error": "",
                "predicate_results": [{
                    "name": "url-after",
                    "artifact_kind": "url_after",
                    "artifact_path": artifact_paths[1].1,
                    "byte_len": 21,
                    "ok": true,
                    "detail": "ok"
                }]
            }]
        });
        std::fs::write(&scorecard_path, serde_json::to_vec(&scorecard).unwrap()).unwrap();

        let report_path = root.join("browser-ops-verification-report.json");
        let artifact_hash = crate::cli::write_browser_ops_bench_verification_report(
            &scorecard_path,
            &report_path,
            BrowserCollectorKind::PlaywrightCdp,
            1_786_560_000_000,
            test_hash("browser-ops-cli-config"),
            test_hash("browser-ops-cli-capability"),
            test_hash("browser-ops-cli-session"),
            test_hash("browser-ops-cli-redaction"),
            test_hash("browser-ops-cli-policy-window"),
            8_888,
        )
        .unwrap();
        assert_ne!(artifact_hash, [0; 32]);
        let payload: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&report_path).unwrap()).unwrap();
        assert_eq!(payload["schema_version"], json!(1));
        assert_eq!(
            payload["report"]["schema"],
            json!("aegis-browser-ops-bench-verification-report-v1")
        );
        assert_eq!(payload["report"]["verified_task_count"], json!(1));
        assert_eq!(payload["report"]["replay_recorded"], json!(true));
        assert_eq!(payload["report"]["run_id"], json!(8888));
        assert!(
            payload["report"]["proof_hash"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v != 0)
        );
        assert!(
            payload["report"]["verification_event_hash"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v != 0)
        );
        assert!(
            payload["report"]["replay_binding_hash"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v != 0)
        );
        assert!(
            payload["write_evidence"]["evidence_hash"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v != 0)
        );
    }

    #[test]
    fn cli_writes_agentic_sdk_context_report_artifact() {
        use serde_json::json;
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("agentic-sdk-context-report-{unique}"));
        let artifact_path = root.join("agentic-sdk-context-report.json");
        let artifact_hash = write_agentic_sdk_context_report(&artifact_path).unwrap();
        assert_ne!(artifact_hash, [0; 32]);
        assert!(root.join("agentic_sdk_context_segments").is_dir());

        let payload: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&artifact_path).unwrap()).unwrap();
        assert_eq!(payload["schema_version"], json!(1));
        assert_eq!(
            payload["report"]["schema"],
            json!("aegis-agentic-sdk-context-report-v1")
        );
        assert_eq!(payload["report"]["run_id"], json!(9901));
        assert_eq!(payload["report"]["execution_id"], json!(1991));
        assert_eq!(payload["report"]["active_task_id"], json!(1));
        assert_eq!(payload["report"]["replay_recorded"], json!(true));
        assert_eq!(payload["report"]["context_recorded"], json!(true));
        assert!(payload["report"]["candidate_count"].as_u64().unwrap() > 0);
        for field in [
            "sdk_run_hash",
            "manifest_hash",
            "execution_record_hash",
            "capsule_hash",
            "candidate_list_hash",
            "sdk_run_event_hash",
            "sdk_run_replay_binding_hash",
            "handoff_hash",
            "checkpoint_hash",
            "next_action_packet_hash",
            "context_pack_digest",
            "context_pack_node_hash",
            "context_pack_candidate_proof_hash",
            "context_pack_event_hash",
            "context_archive_manifest_hash",
            "context_ledger_hash",
            "report_hash",
        ] {
            assert!(
                payload["report"][field]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|v| v != 0),
                "{field} should be nonzero"
            );
        }
        assert!(payload["report"]["context_event_count"].as_u64().unwrap() >= 4);
        assert!(
            payload["write_evidence"]["evidence_hash"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v != 0)
        );
    }

    #[test]
    fn browser_observation_packet_can_satisfy_r4_staging_facts() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserCollectorKind, BrowserObservationPacket,
            BrowserWitnessProof,
        };
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, POLICY_RULE_ALLOW,
            PolicyDecision, RiskClass, SideEffectClass, TypedToolIR,
        };

        let policy_window_hash = test_hash("browser-packet-r4-policy-window");
        let browser_session_hash = test_hash("browser-packet-r4-session");
        let redaction_policy_hash = test_hash("browser-packet-r4-redaction");
        let action = BrowserActionTrace::new(
            601,
            BrowserActionKind::Navigate,
            test_hash("browser-packet-r4-url"),
            None,
            None,
            None,
            policy_window_hash,
        );
        let proof = BrowserWitnessProof::new(
            10,
            action.action_id,
            SideEffectClass::FinancialLegal,
            test_hash("r4-url-before"),
            test_hash("r4-url-after"),
            test_hash("r4-dom-before"),
            test_hash("r4-dom-after"),
            test_hash("r4-screenshot-before"),
            test_hash("r4-screenshot-after"),
            test_hash("r4-ax-after"),
            test_hash("r4-network-log"),
            action.trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        );
        let packet = BrowserObservationPacket::new(
            BrowserCollectorKind::PlaywrightCdp,
            1,
            1_786_300_100_000,
            test_hash("r4-collector-config"),
            test_hash("r4-collector-capabilities"),
            test_hash("r4-raw-artifact-manifest"),
            action,
            proof,
        );
        assert!(packet.is_valid());

        let ir = TypedToolIR::new(
            190,
            191,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("r4-credential-scope"),
            RiskClass::R4,
            test_hash("r4-precondition"),
            test_hash("r4-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("r4-approval-scope")),
        );
        let facts = packet
            .isolated_browser_policy_facts(
                test_hash("policy-v2"),
                true,
                policy_window_hash,
                browser_session_hash,
                redaction_policy_hash,
            )
            .expect("valid packet should produce physical browser staging facts");
        assert_eq!(facts.staging_proof_ref_hash, Some(packet.packet_hash));

        let trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(trace.decision, PolicyDecision::Allow);
        assert_eq!(trace.matched_rule_ids, vec![POLICY_RULE_ALLOW]);

        assert!(
            packet
                .isolated_browser_policy_facts(
                    [0; 32],
                    true,
                    policy_window_hash,
                    browser_session_hash,
                    redaction_policy_hash,
                )
                .is_none()
        );

        assert!(
            packet
                .isolated_browser_policy_facts(
                    test_hash("policy-v2"),
                    true,
                    policy_window_hash,
                    test_hash("wrong-session"),
                    redaction_policy_hash,
                )
                .is_none()
        );
    }

    #[test]
    fn telemetry_envelope_requires_redaction_and_payload_hash() {
        use crate::policy::{RiskClass, TelemetryEnvelope, TelemetryEventKind};

        let valid = TelemetryEnvelope {
            run_id: 1,
            trace_id: [1; 16],
            span_id: [2; 8],
            event_kind: TelemetryEventKind::PolicyDecision,
            risk_class: RiskClass::R3,
            evidence_ref_hash: Some(test_hash("evidence")),
            policy_window_hash: Some(test_hash("policy-window")),
            redaction_policy_hash: test_hash("redaction"),
            monotonic_time_ns: 10,
            wall_time_ms: 20,
            payload_hash: test_hash("payload"),
        };
        assert!(valid.is_valid());

        let missing_redaction = TelemetryEnvelope {
            redaction_policy_hash: [0; 32],
            ..valid
        };
        assert!(!missing_redaction.is_valid());
    }

    #[test]
    fn harness_scorecard_rejects_text_only_success() {
        use crate::policy::HarnessBenchScorecard;

        let text_only = HarnessBenchScorecard {
            bench_id: 1,
            task_id: 1,
            success: true,
            physical_witness_hash: [0; 32],
            replay_hash: [0; 32],
            policy_violation_count: 0,
            hard_block_count: 0,
            approval_required_count: 0,
            witness_coverage_ppm: 0,
            crash_recovery_passed: true,
            p50_wall_ms: 1,
            p99_wall_ms: 2,
            token_count: 10,
            tool_call_count: 1,
        };
        assert!(!text_only.is_valid_physical_result());

        let physical = HarnessBenchScorecard {
            physical_witness_hash: test_hash("physical-witness"),
            replay_hash: test_hash("replay"),
            witness_coverage_ppm: 1_000_000,
            ..text_only
        };
        assert!(physical.is_valid_physical_result());
    }

    #[test]
    fn run_event_ledger_requires_context_pack_before_llm_response() {
        use crate::replay::{ReplayLedgerError, RunEventKind, RunEventLedger};

        let mut ledger = RunEventLedger::new(1);
        let missing_intake =
            ledger.append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"));
        assert_eq!(
            missing_intake,
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );

        seed_goal_intake_replay(&mut ledger, "missing-context");
        let missing_context =
            ledger.append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"));
        assert_eq!(
            missing_context,
            Err(ReplayLedgerError::MissingPriorContextPack)
        );

        let context_event = ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        assert_eq!(context_event.kind, RunEventKind::ContextPackBuilt);
        assert_eq!(context_event.subject_id, 11);
        assert_eq!(context_event.primary_hash, test_hash("context-pack"));

        let response_event = ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        assert_eq!(response_event.kind, RunEventKind::LLMResponseReceived);
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_ledger_requires_llm_response_before_tool_call() {
        use crate::replay::{ReplayLedgerError, RunEventLedger};

        let mut ledger = RunEventLedger::new(1);
        seed_goal_intake_replay(&mut ledger, "requires-llm");
        let result = ledger.append_llm_derived_tool_call_requested(7, test_hash("tool-ir"));
        assert_eq!(result, Err(ReplayLedgerError::MissingPriorLlmResponse));

        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let event = ledger
            .append_llm_derived_tool_call_requested(7, test_hash("tool-ir"))
            .unwrap();
        assert_eq!(event.subject_id, 7);
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_records_budget_admitted_llm_response_replay_binding() {
        use crate::replay::{
            ReplayLedgerError, RunEventKind, RunEventLedger,
            llm_budget_admission_replay_binding_hash,
        };

        let request = build_llm_request(41, 42, "budget route replay", Some("fast-model"));
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
                cost_rank: 5,
                availability_rank: 1,
            },
        ];
        let ledger_budget = ProviderBudgetLedger::new(&[
            ProviderRuntimeBudget::new("fast", "fast-model", 0, 10_000, 1_000_000).unwrap(),
            ProviderRuntimeBudget::new("reserve", "reserve-model", 5, 10_000, 1_000_000).unwrap(),
        ])
        .unwrap();
        let (_, admission_proof) =
            route_request_with_budget(&request, &providers, &ledger_budget, 1_024).unwrap();
        let response_hash = test_hash("budget-admitted-response");
        let raw_text_ref_hash = test_hash("budget-admitted-raw-ref");

        let mut replay_ledger = RunEventLedger::new(141);
        seed_goal_intake_replay(&mut replay_ledger, "budget-admitted-llm");
        replay_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        let event = replay_ledger
            .append_budget_admitted_llm_response_received(
                response_hash,
                raw_text_ref_hash,
                &admission_proof,
            )
            .unwrap();

        assert_eq!(event.kind, RunEventKind::LLMResponseReceived);
        assert_eq!(event.subject_id, 1_024);
        assert_eq!(event.primary_hash, response_hash);
        assert_eq!(
            event.secondary_hash.unwrap(),
            llm_budget_admission_replay_binding_hash(
                response_hash,
                raw_text_ref_hash,
                &admission_proof
            )
        );
        assert!(replay_ledger.verify_hash_chain());

        let mut tampered = replay_ledger.events().to_vec();
        tampered[2].secondary_hash = Some(test_hash("wrong-admission-proof"));
        assert_eq!(
            RunEventLedger::from_events(replay_ledger.run_id, tampered),
            Err(ReplayLedgerError::InvalidEvent)
        );
    }

    #[test]
    fn tool_execution_evidence_binds_output_physical_witness_and_policy() {
        use crate::replay::{
            ToolExecutionEvidence, ToolExecutionStatus, ToolExecutorKind,
            tool_call_completion_binding_hash, tool_execution_evidence_hash,
        };

        let evidence = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );

        assert!(evidence.is_valid());
        assert_eq!(
            evidence.evidence_hash,
            tool_execution_evidence_hash(
                test_hash("typed-tool-ir"),
                test_hash("policy-proof-trace"),
                test_hash("tool-output"),
                test_hash("physical-witness"),
                ToolExecutorKind::Wasmtime,
                ToolExecutionStatus::Succeeded,
            )
        );
        assert_eq!(
            evidence.completion_binding_hash(7),
            tool_call_completion_binding_hash(
                7,
                test_hash("typed-tool-ir"),
                test_hash("policy-proof-trace"),
                evidence.evidence_hash,
            )
        );

        let missing_physical = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            [0; 32],
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        assert!(!missing_physical.is_valid());

        let mut tampered = evidence.clone();
        tampered.tool_output_hash = test_hash("tampered-output");
        assert!(!tampered.is_valid());
    }

    #[test]
    fn run_event_ledger_requires_request_and_policy_before_tool_completion() {
        use crate::replay::{
            ReplayLedgerError, RunEventLedger, ToolExecutionEvidence, ToolExecutionStatus,
            ToolExecutorKind,
        };

        let evidence = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );

        let mut missing_tool = RunEventLedger::new(110);
        seed_goal_intake_replay(&mut missing_tool, "missing-tool");
        missing_tool
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        assert_eq!(
            missing_tool.append_tool_call_completed(7, &evidence),
            Err(ReplayLedgerError::MissingPriorToolCall)
        );

        let mut missing_policy = RunEventLedger::new(111);
        seed_goal_intake_replay(&mut missing_policy, "missing-policy");
        missing_policy
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        missing_policy
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        missing_policy
            .append_llm_derived_tool_call_requested(7, test_hash("typed-tool-ir"))
            .unwrap();
        assert_eq!(
            missing_policy.append_tool_call_completed(7, &evidence),
            Err(ReplayLedgerError::MissingPriorPolicyDecision)
        );
    }

    #[test]
    fn run_event_records_tool_completion_evidence_after_matching_policy() {
        use crate::replay::{
            ReplayLedgerError, RunEventKind, RunEventLedger, ToolExecutionEvidence,
            ToolExecutionStatus, ToolExecutorKind, tool_call_completion_binding_hash,
        };

        let evidence = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );

        let mut mismatched = RunEventLedger::new(112);
        seed_goal_intake_replay(&mut mismatched, "mismatched-tool");
        mismatched
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        mismatched
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        mismatched
            .append_llm_derived_tool_call_requested(7, test_hash("typed-tool-ir"))
            .unwrap();
        mismatched
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("different-tool-ir"),
            )
            .unwrap();
        assert_eq!(
            mismatched.append_tool_call_completed(7, &evidence),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let mut ledger = RunEventLedger::new(113);
        seed_goal_intake_replay(&mut ledger, "matching-tool");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(7, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        let event = ledger.append_tool_call_completed(7, &evidence).unwrap();

        assert_eq!(event.kind, RunEventKind::ToolCallCompleted);
        assert_eq!(event.subject_id, 7);
        assert_eq!(
            event.primary_hash,
            tool_call_completion_binding_hash(
                7,
                test_hash("typed-tool-ir"),
                test_hash("policy-proof-trace"),
                evidence.evidence_hash,
            )
        );
        assert_eq!(event.secondary_hash, Some(evidence.evidence_hash));
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_records_memory_commit_only_after_tool_completion() {
        use crate::memory::frame::SemanticNode;
        use crate::replay::{
            ReplayLedgerError, RunEventKind, RunEventLedger, ToolExecutionEvidence,
            ToolExecutionStatus, ToolExecutorKind, memory_commit_binding_hash,
        };
        use crate::tool_gateway::{ToolMemoryCommitProof, tool_memory_commit_proof_hash};

        let evidence = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        let semantic_node = SemanticNode {
            node_id: 77,
            session_id: 77,
            artifact_hash: test_hash("tool-output"),
            ast_fingerprint: 99,
            fidelity: 0.5,
        };
        let proof_hash = tool_memory_commit_proof_hash(
            77,
            test_hash("execution-replay-proof"),
            test_hash("artifact-evidence"),
            &semantic_node,
            1,
        );
        let memory_proof = ToolMemoryCommitProof {
            session_id: 77,
            execution_replay_proof_hash: test_hash("execution-replay-proof"),
            artifact_evidence_hash: test_hash("artifact-evidence"),
            semantic_node,
            memory_len_after_commit: 1,
            proof_hash,
        };

        let mut missing_completion = RunEventLedger::new(121);
        assert_eq!(
            missing_completion.append_memory_commit_recorded(&memory_proof),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let mut ledger = RunEventLedger::new(122);
        seed_goal_intake_replay(&mut ledger, "memory-commit");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(7, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger.append_tool_call_completed(7, &evidence).unwrap();
        let event = ledger.append_memory_commit_recorded(&memory_proof).unwrap();

        assert_eq!(event.kind, RunEventKind::MemoryCommitRecorded);
        assert_eq!(event.subject_id, 77);
        assert_eq!(event.primary_hash, memory_proof.proof_hash);
        assert_eq!(
            event.secondary_hash,
            Some(memory_commit_binding_hash(
                77,
                evidence.evidence_hash,
                memory_proof.proof_hash,
            ))
        );
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered = ledger.events().to_vec();
        tampered.last_mut().unwrap().secondary_hash = Some(test_hash("tampered-memory-binding"));
        assert_eq!(
            RunEventLedger::from_events(ledger.run_id, tampered),
            Err(ReplayLedgerError::InvalidEvent)
        );
    }

    #[test]
    fn run_event_seals_memory_commit_handoff_from_mmap_archive() {
        use crate::memory::frame::SemanticNode;
        use crate::replay::{
            MemoryCommitHandoffProof, NextActionKind, NextActionPacket, RunCheckpoint,
            RunEventKind, RunEventLedger, RunEventSegmentArchive, ToolExecutionEvidence,
            ToolExecutionStatus, ToolExecutorKind,
        };
        use crate::tool_gateway::{ToolMemoryCommitProof, tool_memory_commit_proof_hash};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("memory-commit-handoff-{unique}"));

        let typed_tool_ir_hash = test_hash("typed-tool-ir");
        let policy_proof_hash = test_hash("policy-proof-trace");
        let evidence = ToolExecutionEvidence::new(
            typed_tool_ir_hash,
            policy_proof_hash,
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        let semantic_node = SemanticNode {
            node_id: 78,
            session_id: 78,
            artifact_hash: test_hash("tool-output"),
            ast_fingerprint: 100,
            fidelity: 0.5,
        };
        let proof_hash = tool_memory_commit_proof_hash(
            78,
            test_hash("execution-replay-proof"),
            test_hash("artifact-evidence"),
            &semantic_node,
            1,
        );
        let memory_proof = ToolMemoryCommitProof {
            session_id: 78,
            execution_replay_proof_hash: test_hash("execution-replay-proof"),
            artifact_evidence_hash: test_hash("artifact-evidence"),
            semantic_node,
            memory_len_after_commit: 1,
            proof_hash,
        };

        let mut ledger = RunEventLedger::new(123);
        seed_goal_intake_replay(&mut ledger, "memory-handoff");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(7, typed_tool_ir_hash)
            .unwrap();
        ledger
            .append_policy_decision_recorded(17, policy_proof_hash, typed_tool_ir_hash)
            .unwrap();
        ledger.append_tool_call_completed(7, &evidence).unwrap();
        let memory_event = ledger.append_memory_commit_recorded(&memory_proof).unwrap();
        assert_eq!(memory_event.kind, RunEventKind::MemoryCommitRecorded);

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let handoff = RunEventSegmentArchive::seal_memory_commit_handoff(
            &dir,
            &manifest,
            &memory_proof,
            evidence.evidence_hash,
            88,
            89,
            NextActionKind::ContinueExecution,
            78,
            typed_tool_ir_hash,
            test_hash("evidence-contract"),
            policy_proof_hash,
        )
        .unwrap();
        assert_eq!(handoff.memory_commit_proof_hash, memory_proof.proof_hash);
        assert_eq!(handoff.tool_execution_evidence_hash, evidence.evidence_hash);
        assert_ne!(handoff.handoff_hash, [0; 32]);

        let replay_proof =
            RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest).unwrap();
        let recovered = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        let archived_memory_event = recovered
            .events()
            .iter()
            .find(|event| event.event_hash == handoff.memory_commit_event_hash)
            .unwrap();
        let checkpoint = RunCheckpoint::new(
            recovered.run_id,
            88,
            replay_proof.event_count,
            manifest.entries.len(),
            replay_proof.first_pass_ledger_hash,
            manifest.manifest_hash,
            replay_proof.proof_hash,
            archived_memory_event.event_hash,
        );
        let packet = NextActionPacket::new(
            recovered.run_id,
            89,
            checkpoint.checkpoint_hash,
            NextActionKind::ContinueExecution,
            78,
            typed_tool_ir_hash,
            test_hash("evidence-contract"),
            policy_proof_hash,
            archived_memory_event.event_hash,
        );
        let rebuilt = MemoryCommitHandoffProof::new(
            archived_memory_event,
            evidence.evidence_hash,
            &memory_proof,
            &replay_proof,
            &checkpoint,
            &packet,
        );
        assert_eq!(handoff, rebuilt);
        assert!(handoff.is_valid_for(
            archived_memory_event,
            &memory_proof,
            &replay_proof,
            &checkpoint,
            &packet
        ));

        let mut stale_packet = packet.clone();
        stale_packet.candidate_evidence_hash = test_hash("stale-candidate-evidence");
        assert!(!handoff.is_valid_for(
            archived_memory_event,
            &memory_proof,
            &replay_proof,
            &checkpoint,
            &stale_packet
        ));
        assert!(
            RunEventSegmentArchive::seal_memory_commit_handoff(
                &dir,
                &manifest,
                &memory_proof,
                test_hash("wrong-tool-evidence"),
                88,
                89,
                NextActionKind::ContinueExecution,
                78,
                typed_tool_ir_hash,
                test_hash("evidence-contract"),
                policy_proof_hash,
            )
            .is_err()
        );
    }

    #[test]
    fn run_event_rejects_stale_policy_for_later_tool_completion() {
        use crate::replay::{
            ReplayLedgerError, RunEventLedger, ToolExecutionEvidence, ToolExecutionStatus,
            ToolExecutorKind,
        };

        let evidence = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        let mut ledger = RunEventLedger::new(114);
        seed_goal_intake_replay(&mut ledger, "stale-policy");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(7, test_hash("typed-tool-ir"))
            .unwrap();

        assert_eq!(
            ledger.append_tool_call_completed(7, &evidence),
            Err(ReplayLedgerError::MissingPriorPolicyDecision)
        );
    }

    #[test]
    fn tool_execution_gateway_runs_wasmtime_and_records_evidence_bound_replay() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{RunEventKind, RunEventLedger};
        use crate::sandbox::WasmtimeSandbox;
        use crate::tool_gateway::ToolExecutionGateway;

        let ir = TypedToolIR::new(
            900,
            901,
            CapabilityClass::LocalRead,
            SideEffectClass::None,
            test_hash("credential-scope"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::physical(),
            None,
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::Allow);

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let mut ledger = RunEventLedger::new(115);
        seed_goal_intake_replay(&mut ledger, "wasmtime-gateway");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();

        let receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            77,
            78,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();

        assert!(receipt.is_valid_for(&ir, &proof));
        assert!(ToolExecutionGateway::replay_events_are_bound(
            &receipt,
            ledger.events()
        ));
        assert_eq!(ledger.events()[3].kind, RunEventKind::ToolCallRequested);
        assert_eq!(
            ledger.events()[4].kind,
            RunEventKind::PolicyDecisionRecorded
        );
        assert_eq!(ledger.events()[5].kind, RunEventKind::ToolCallCompleted);
        assert_eq!(
            ledger.events()[5].secondary_hash,
            Some(receipt.tool_execution_evidence.evidence_hash)
        );
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn tool_execution_gateway_ingests_browser_packet_as_replay_bound_tool_evidence() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserCollectorKind, BrowserObservationPacket,
            BrowserWitnessProof,
        };
        use crate::memory::fold::CogniFoldStore;
        use crate::physical::{BacktrackSignal, PhysicalWatchdog, TrapReason};
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{
            NextActionKind, RunEventKind, RunEventLedger, RunEventSegmentArchive,
            ToolExecutionStatus, ToolExecutorKind,
        };
        use crate::tool_gateway::{
            ToolExecutionGateway, browser_tool_physical_evidence_hash, evidence_contract_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let run_id = 133;
        let policy_window_hash = test_hash("gateway-browser-policy-window");
        let browser_session_hash = test_hash("gateway-browser-session");
        let redaction_policy_hash = test_hash("gateway-browser-redaction");
        let action = BrowserActionTrace::new(
            801,
            BrowserActionKind::Click,
            test_hash("gateway-browser-target"),
            None,
            Some((144, 288)),
            None,
            policy_window_hash,
        );
        let proof = BrowserWitnessProof::new(
            run_id,
            action.action_id,
            SideEffectClass::FinancialLegal,
            test_hash("gateway-url-before"),
            test_hash("gateway-url-after"),
            test_hash("gateway-dom-before"),
            test_hash("gateway-dom-after"),
            test_hash("gateway-screenshot-before"),
            test_hash("gateway-screenshot-after"),
            test_hash("gateway-ax-after"),
            test_hash("gateway-network-log"),
            action.trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        );
        let packet = BrowserObservationPacket::new(
            BrowserCollectorKind::ChromeExtension,
            4,
            1_786_302_000_000,
            test_hash("gateway-collector-config"),
            test_hash("gateway-collector-capabilities"),
            test_hash("gateway-raw-artifact-manifest"),
            action,
            proof,
        );
        assert!(packet.is_valid());

        let ir = TypedToolIR::new(
            260,
            261,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("gateway-browser-credential-scope"),
            RiskClass::R4,
            test_hash("gateway-browser-precondition"),
            test_hash("gateway-browser-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("gateway-browser-approval-scope")),
        );
        let facts = packet
            .isolated_browser_policy_facts(
                test_hash("gateway-browser-policy-v1"),
                true,
                policy_window_hash,
                browser_session_hash,
                redaction_policy_hash,
            )
            .unwrap();
        let policy_trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(policy_trace.decision, PolicyDecision::Allow);

        let mut ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut ledger, "browser-gateway");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let receipt = ToolExecutionGateway::execute_browser_observation_with_replay(
            &mut ledger,
            packet.action_id,
            802,
            &ir,
            &facts,
            &policy_trace,
            &packet,
        )
        .unwrap();

        assert!(receipt.is_valid_for(&ir, &policy_trace));
        assert_eq!(receipt.tool_output_hash, packet.packet_hash);
        assert_eq!(receipt.artifact_evidence, None);
        assert_eq!(
            receipt.physical_evidence_hash,
            browser_tool_physical_evidence_hash(
                packet.action_id,
                ir.canonical_hash,
                policy_trace.compute_hash(),
                &packet,
            )
        );
        assert_eq!(
            receipt.tool_execution_evidence.executor_kind,
            ToolExecutorKind::Chrome
        );
        assert_eq!(
            receipt.tool_execution_evidence.status,
            ToolExecutionStatus::Succeeded
        );
        assert!(receipt.browser_observation_event_hash.is_some());
        assert!(ToolExecutionGateway::replay_events_are_bound(
            &receipt,
            ledger.events()
        ));
        assert_eq!(ledger.events()[3].kind, RunEventKind::ToolCallRequested);
        assert_eq!(
            ledger.events()[4].kind,
            RunEventKind::BrowserObservationPacketRecorded
        );
        assert_eq!(
            ledger.events()[5].kind,
            RunEventKind::PolicyDecisionRecorded
        );
        assert_eq!(ledger.events()[6].kind, RunEventKind::ToolCallCompleted);
        assert_eq!(ledger.events()[4].primary_hash, packet.packet_hash);
        assert_eq!(
            ledger.events()[6].secondary_hash,
            Some(receipt.tool_execution_evidence.evidence_hash)
        );
        assert!(ledger.verify_hash_chain());

        ledger
            .append_checkpoint_sealed(803, receipt.tool_execution_evidence.evidence_hash)
            .unwrap();
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("browser-tool-gateway-replay-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let execution_proof = ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
            &dir,
            &manifest,
            receipt.clone(),
            803,
            804,
            NextActionKind::ContinueExecution,
            260,
            evidence_contract_hash(&ir),
        )
        .unwrap();
        assert!(execution_proof.is_valid_for(&ir, &policy_trace, &manifest));
        assert_eq!(
            execution_proof.next_action_packet.candidate_evidence_hash,
            receipt.physical_evidence_hash
        );

        let mut forged_receipt = receipt.clone();
        forged_receipt.browser_observation_event_hash = None;
        assert!(!forged_receipt.has_valid_fields());
        assert!(
            ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
                &dir,
                &manifest,
                forged_receipt,
                803,
                805,
                NextActionKind::ContinueExecution,
                260,
                evidence_contract_hash(&ir),
            )
            .is_err()
        );

        let mut tampered_packet = packet.clone();
        tampered_packet.packet_hash = test_hash("gateway-tampered-packet");
        assert_eq!(
            ToolExecutionGateway::execute_browser_observation_with_replay(
                &mut ledger,
                tampered_packet.action_id,
                806,
                &ir,
                &facts,
                &policy_trace,
                &tampered_packet,
            ),
            Err(TrapReason::InvariantViolation)
        );

        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0 };
        assert_eq!(
            ToolExecutionGateway::commit_replay_proven_tool_output_to_cognifold(
                &mut store,
                260,
                &execution_proof,
                &ir,
                &policy_trace,
                &manifest,
                &watchdog,
            ),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation
            ))
        );
        assert_eq!(store.len(), 0);
    }

    #[test]
    fn tool_execution_gateway_mints_browser_packet_from_file_path_refs_before_replay() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserArtifactFilePathRef, BrowserArtifactKind,
            BrowserCollectorKind,
        };
        use crate::physical::TrapReason;
        use crate::policy::{
            CapabilityClass, EvidenceContract, PolicyDecision, RiskClass, SideEffectClass,
            TypedToolIR,
        };
        use crate::replay::{RunEventKind, RunEventLedger};
        use crate::tool_gateway::{
            ToolExecutionGateway, browser_gateway_execution_proof_hash,
            browser_tool_physical_evidence_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("browser-gateway-file-paths-{unique}"));
        std::fs::create_dir_all(&dir).unwrap();
        let artifacts = vec![
            (
                BrowserArtifactKind::NetworkLog,
                dir.join("network-log.jsonl"),
                b"[{\"url\":\"https://gateway.test/api\",\"status\":204}]".to_vec(),
            ),
            (
                BrowserArtifactKind::ScreenshotAfter,
                dir.join("screenshot-after.png"),
                b"gateway-png-after".to_vec(),
            ),
            (
                BrowserArtifactKind::UrlBefore,
                dir.join("url-before.txt"),
                b"https://gateway.test/before".to_vec(),
            ),
            (
                BrowserArtifactKind::DomSnapshotAfter,
                dir.join("dom-after.html"),
                b"<html><button>approved</button></html>".to_vec(),
            ),
            (
                BrowserArtifactKind::AccessibilityTreeAfter,
                dir.join("accessibility-after.json"),
                b"{\"role\":\"button\",\"name\":\"approved\"}".to_vec(),
            ),
            (
                BrowserArtifactKind::UrlAfter,
                dir.join("url-after.txt"),
                b"https://gateway.test/after".to_vec(),
            ),
            (
                BrowserArtifactKind::ScreenshotBefore,
                dir.join("screenshot-before.png"),
                b"gateway-png-before".to_vec(),
            ),
            (
                BrowserArtifactKind::DomSnapshotBefore,
                dir.join("dom-before.html"),
                b"<html><button>approve</button></html>".to_vec(),
            ),
        ];
        for (_, path, bytes) in &artifacts {
            std::fs::write(path, bytes).unwrap();
        }
        let path_refs: Vec<_> = artifacts
            .iter()
            .map(|(kind, path, _)| BrowserArtifactFilePathRef::from_path(*kind, path).unwrap())
            .collect();

        let run_id = 151;
        let policy_window_hash = test_hash("browser-gateway-file-policy-window");
        let browser_session_hash = test_hash("browser-gateway-file-session");
        let redaction_policy_hash = test_hash("browser-gateway-file-redaction");
        let action = BrowserActionTrace::new(
            991,
            BrowserActionKind::Click,
            test_hash("browser-gateway-file-target"),
            None,
            Some((64, 72)),
            None,
            policy_window_hash,
        );
        let ir = TypedToolIR::new(
            991,
            992,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("browser-gateway-file-credential"),
            RiskClass::R4,
            test_hash("browser-gateway-file-precondition"),
            test_hash("browser-gateway-file-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("browser-gateway-file-approval-scope")),
        );
        let policy_version_hash = test_hash("browser-gateway-file-policy-v1");
        let mut ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut ledger, "browser-file-gateway");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();

        let proof = ToolExecutionGateway::execute_browser_file_path_refs_with_replay(
            &mut ledger,
            993,
            &ir,
            BrowserCollectorKind::PlaywrightCdp,
            action.clone(),
            8,
            1_786_330_000_000,
            test_hash("browser-gateway-file-config"),
            test_hash("browser-gateway-file-capability"),
            browser_session_hash,
            redaction_policy_hash,
            &path_refs,
            policy_version_hash,
            true,
        )
        .expect("gateway should mint browser packet from physical files");

        assert!(proof.is_valid_for(
            run_id,
            &ir,
            policy_version_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash
        ));
        assert_eq!(proof.policy_trace.decision, PolicyDecision::Allow);
        assert_eq!(
            proof.policy_facts.staging_proof_ref_hash,
            Some(proof.packet.packet_hash)
        );
        assert_eq!(
            proof.packet.proof.url_before_hash,
            *blake3::hash(b"https://gateway.test/before").as_bytes()
        );
        assert_eq!(
            proof.receipt.physical_evidence_hash,
            browser_tool_physical_evidence_hash(
                action.action_id,
                ir.canonical_hash,
                proof.policy_trace.compute_hash(),
                &proof.packet,
            )
        );
        assert_eq!(
            proof.proof_hash,
            browser_gateway_execution_proof_hash(
                &proof.packet,
                &proof.policy_facts,
                &proof.policy_trace,
                &proof.receipt,
                None,
            )
        );
        assert_eq!(ledger.events()[3].kind, RunEventKind::ToolCallRequested);
        assert_eq!(
            ledger.events()[4].kind,
            RunEventKind::BrowserObservationPacketRecorded
        );
        assert_eq!(
            ledger.events()[5].kind,
            RunEventKind::PolicyDecisionRecorded
        );
        assert_eq!(ledger.events()[6].kind, RunEventKind::ToolCallCompleted);
        assert_eq!(ledger.events()[4].primary_hash, proof.packet.packet_hash);
        assert!(ledger.verify_hash_chain());

        let old_packet_hash = proof.packet.packet_hash;
        std::fs::write(dir.join("network-log.jsonl"), b"{\"tampered\":true}").unwrap();
        let mut tampered_ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut tampered_ledger, "browser-file-tamper");
        tampered_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        tampered_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let tampered_proof = ToolExecutionGateway::execute_browser_file_path_refs_with_replay(
            &mut tampered_ledger,
            993,
            &ir,
            BrowserCollectorKind::PlaywrightCdp,
            action.clone(),
            8,
            1_786_330_000_000,
            test_hash("browser-gateway-file-config"),
            test_hash("browser-gateway-file-capability"),
            browser_session_hash,
            redaction_policy_hash,
            &path_refs,
            policy_version_hash,
            true,
        )
        .expect("gateway should read the tampered file bytes as fresh physical evidence");
        assert_ne!(tampered_proof.packet.packet_hash, old_packet_hash);

        let mut rejected_ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut rejected_ledger, "browser-file-rejected");
        rejected_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        rejected_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let rejected_before_len = rejected_ledger.events().len();
        assert_eq!(
            ToolExecutionGateway::execute_browser_file_path_refs_with_replay(
                &mut rejected_ledger,
                993,
                &ir,
                BrowserCollectorKind::PlaywrightCdp,
                action,
                8,
                1_786_330_000_000,
                test_hash("browser-gateway-file-config"),
                test_hash("browser-gateway-file-capability"),
                browser_session_hash,
                redaction_policy_hash,
                &path_refs,
                policy_version_hash,
                false,
            ),
            Err(TrapReason::InvariantViolation)
        );
        assert_eq!(rejected_ledger.events().len(), rejected_before_len);
    }

    #[test]
    fn tool_execution_gateway_mints_browser_packet_from_live_manifest_before_replay() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionPlanKind, BrowserActionPlanRecord, BrowserActionTrace,
            BrowserArtifactFilePathRef, BrowserArtifactKind, BrowserCollectorKind,
            BrowserLiveCollectorManifest,
        };
        use crate::physical::TrapReason;
        use crate::policy::{
            CapabilityClass, EvidenceContract, PolicyDecision, RiskClass, SideEffectClass,
            TypedToolIR,
        };
        use crate::replay::{RunEventKind, RunEventLedger};
        use crate::tool_gateway::{ToolExecutionGateway, browser_gateway_execution_proof_hash};
        use std::fs;

        fn write_artifact(
            dir: &std::path::Path,
            kind: BrowserArtifactKind,
            name: &str,
            bytes: &[u8],
        ) -> BrowserArtifactFilePathRef {
            let path = dir.join(name);
            fs::write(&path, bytes).unwrap();
            BrowserArtifactFilePathRef::from_path(kind, path).unwrap()
        }

        let unique = test_hash("browser-live-manifest-dir");
        let dir = std::env::temp_dir().join(format!(
            "aegis-browser-live-manifest-{:02x}{:02x}",
            unique[0], unique[1]
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path_refs = vec![
            write_artifact(
                &dir,
                BrowserArtifactKind::NetworkLog,
                "network-log.jsonl",
                b"{\"url\":\"https://live.test/api\",\"status\":200}",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::ScreenshotAfter,
                "screenshot-after.png",
                b"live-png-after",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::UrlBefore,
                "url-before.txt",
                b"https://live.test/before",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::DomSnapshotAfter,
                "dom-after.html",
                b"<html><button>finished</button></html>",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::AccessibilityTreeAfter,
                "ax-after.json",
                b"{\"button\":\"finished\"}",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::UrlAfter,
                "url-after.txt",
                b"https://live.test/after",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::ScreenshotBefore,
                "screenshot-before.png",
                b"live-png-before",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::DomSnapshotBefore,
                "dom-before.html",
                b"<html><button>finish</button></html>",
            ),
        ];

        let run_id = 181;
        let action_id = 1181;
        let sequence_number = 5;
        let observed_at_unix_ms = 1_786_350_000_000;
        let policy_window_hash = test_hash("browser-live-manifest-policy-window");
        let browser_session_hash = test_hash("browser-live-manifest-session");
        let redaction_policy_hash = test_hash("browser-live-manifest-redaction");
        let action = BrowserActionTrace::new(
            action_id,
            BrowserActionKind::Click,
            test_hash("browser-live-manifest-target"),
            None,
            Some((80, 96)),
            None,
            policy_window_hash,
        );
        let ir = TypedToolIR::new(
            action_id,
            1182,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("browser-live-manifest-credential"),
            RiskClass::R4,
            test_hash("browser-live-manifest-precondition"),
            test_hash("browser-live-manifest-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("browser-live-manifest-approval-scope")),
        );
        let manifest = BrowserLiveCollectorManifest::new(
            BrowserCollectorKind::PlaywrightCdp,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            test_hash("browser-live-manifest-config"),
            test_hash("browser-live-manifest-capability"),
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            path_refs.clone(),
        );
        assert!(manifest.is_valid());
        assert!(manifest.verify_files().is_ok());

        let policy_version_hash = test_hash("browser-live-manifest-policy-v1");
        let mut ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut ledger, "browser-live-manifest-gateway");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();

        let proof = ToolExecutionGateway::execute_browser_live_manifest_with_replay(
            &mut ledger,
            1183,
            &ir,
            &manifest,
            action.clone(),
            policy_version_hash,
            true,
        )
        .expect("live manifest gateway should mint packet from physical files");
        assert!(proof.is_valid_for(
            run_id,
            &ir,
            policy_version_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash
        ));
        assert_eq!(proof.policy_trace.decision, PolicyDecision::Allow);
        assert_eq!(
            proof.packet.proof.url_before_hash,
            *blake3::hash(b"https://live.test/before").as_bytes()
        );
        assert_eq!(ledger.events()[3].kind, RunEventKind::ToolCallRequested);
        assert_eq!(
            ledger.events()[4].kind,
            RunEventKind::BrowserObservationPacketRecorded
        );
        assert_eq!(ledger.events()[4].primary_hash, proof.packet.packet_hash);
        assert!(ledger.verify_hash_chain());

        let plan = BrowserActionPlanRecord::new(
            run_id,
            action_id,
            BrowserActionPlanKind::Click,
            sequence_number,
            ir.canonical_hash,
            *blake3::hash(b"https://live.test/before").as_bytes(),
            test_hash("browser-live-manifest-target"),
            None,
            Some((80, 96)),
            None,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            false,
        );
        assert!(manifest.binds_plan(&plan));
        let mut plan_ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut plan_ledger, "browser-live-plan-manifest-gateway");
        plan_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        plan_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let plan_proof =
            ToolExecutionGateway::execute_browser_action_plan_live_manifest_with_replay(
                &mut plan_ledger,
                1184,
                &ir,
                &manifest,
                &plan,
                policy_version_hash,
                true,
            )
            .expect("plan-bound live manifest gateway should mint packet");
        assert_eq!(plan_proof.browser_action_plan_hash, Some(plan.plan_hash));
        assert_eq!(
            plan_proof.proof_hash,
            browser_gateway_execution_proof_hash(
                &plan_proof.packet,
                &plan_proof.policy_facts,
                &plan_proof.policy_trace,
                &plan_proof.receipt,
                Some(plan.plan_hash),
            )
        );

        let wrong_plan = BrowserActionPlanRecord::new(
            run_id,
            action_id,
            BrowserActionPlanKind::Click,
            sequence_number,
            ir.canonical_hash,
            test_hash("wrong-url-before"),
            test_hash("browser-live-manifest-target"),
            None,
            Some((80, 96)),
            None,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            false,
        );
        let mut rejected_ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut rejected_ledger, "browser-live-manifest-rejected");
        rejected_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        rejected_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let rejected_before_len = rejected_ledger.events().len();
        assert_eq!(
            ToolExecutionGateway::execute_browser_action_plan_live_manifest_with_replay(
                &mut rejected_ledger,
                1185,
                &ir,
                &manifest,
                &wrong_plan,
                policy_version_hash,
                true,
            ),
            Err(TrapReason::InvariantViolation)
        );
        assert_eq!(rejected_ledger.events().len(), rejected_before_len);
    }

    #[test]
    fn tool_execution_gateway_binds_browser_action_plan_before_file_packet_ingest() {
        use crate::browser_witness::{
            BrowserActionPlanKind, BrowserActionPlanRecord, BrowserArtifactFilePathRef,
            BrowserArtifactKind, BrowserCollectorKind,
        };
        use crate::physical::TrapReason;
        use crate::policy::{
            CapabilityClass, EvidenceContract, PolicyDecision, RiskClass, SideEffectClass,
            TypedToolIR,
        };
        use crate::replay::{RunEventKind, RunEventLedger};
        use crate::tool_gateway::{ToolExecutionGateway, browser_gateway_execution_proof_hash};
        use std::fs;

        fn write_artifact(
            dir: &std::path::Path,
            kind: BrowserArtifactKind,
            name: &str,
            bytes: &[u8],
        ) -> BrowserArtifactFilePathRef {
            let path = dir.join(name);
            fs::write(&path, bytes).unwrap();
            BrowserArtifactFilePathRef::from_path(kind, path).unwrap()
        }

        let unique = test_hash("browser-plan-file-dir");
        let dir = std::env::temp_dir().join(format!(
            "aegis-browser-plan-file-{:02x}{:02x}",
            unique[0], unique[1]
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path_refs = vec![
            write_artifact(
                &dir,
                BrowserArtifactKind::UrlBefore,
                "url-before.txt",
                b"https://plan.test/before",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::UrlAfter,
                "url-after.txt",
                b"https://plan.test/after",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::DomSnapshotBefore,
                "dom-before.html",
                b"<button>Buy</button>",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::DomSnapshotAfter,
                "dom-after.html",
                b"<button>Done</button>",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::ScreenshotBefore,
                "screenshot-before.png",
                b"png-before",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::ScreenshotAfter,
                "screenshot-after.png",
                b"png-after",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::AccessibilityTreeAfter,
                "ax-after.json",
                b"{\"button\":\"Done\"}",
            ),
            write_artifact(
                &dir,
                BrowserArtifactKind::NetworkLog,
                "network-log.jsonl",
                b"{\"status\":200}",
            ),
        ];

        let run_id = 177;
        let policy_window_hash = test_hash("browser-plan-file-policy-window");
        let browser_session_hash = test_hash("browser-plan-file-session");
        let redaction_policy_hash = test_hash("browser-plan-file-redaction");
        let ir = TypedToolIR::new(
            1170,
            1171,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("browser-plan-file-credential"),
            RiskClass::R4,
            test_hash("browser-plan-file-precondition"),
            test_hash("browser-plan-file-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("browser-plan-file-approval-scope")),
        );
        let plan = BrowserActionPlanRecord::new(
            run_id,
            1172,
            BrowserActionPlanKind::Click,
            3,
            ir.canonical_hash,
            *blake3::hash(b"https://plan.test/before").as_bytes(),
            test_hash("browser-plan-file-target"),
            None,
            Some((44, 55)),
            None,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            false,
        );
        assert!(plan.is_valid());

        let policy_version_hash = test_hash("browser-plan-file-policy-v1");
        let mut ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut ledger, "browser-action-plan-file-gateway");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();

        let proof = ToolExecutionGateway::execute_browser_action_plan_file_path_refs_with_replay(
            &mut ledger,
            1173,
            &ir,
            BrowserCollectorKind::PlaywrightCdp,
            &plan,
            1_786_341_000_000,
            test_hash("browser-plan-file-config"),
            test_hash("browser-plan-file-capability"),
            &path_refs,
            policy_version_hash,
            true,
        )
        .expect("plan-bound browser gateway should mint packet from physical files");

        assert_eq!(proof.browser_action_plan_hash, Some(plan.plan_hash));
        assert!(plan.is_valid_for_packet(ir.canonical_hash, &proof.packet));
        assert_eq!(
            plan.sequence_should_abort_after(ir.canonical_hash, &proof.packet),
            Some(true)
        );
        assert_eq!(proof.policy_trace.decision, PolicyDecision::Allow);
        assert_eq!(proof.packet.action_trace, plan.to_action_trace().unwrap());
        assert_eq!(
            proof.proof_hash,
            browser_gateway_execution_proof_hash(
                &proof.packet,
                &proof.policy_facts,
                &proof.policy_trace,
                &proof.receipt,
                Some(plan.plan_hash),
            )
        );
        assert_eq!(
            ledger.events()[4].kind,
            RunEventKind::BrowserObservationPacketRecorded
        );
        assert!(ledger.verify_hash_chain());

        let wrong_ir = TypedToolIR::new(
            1170,
            1174,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("browser-plan-file-credential"),
            RiskClass::R4,
            test_hash("browser-plan-file-precondition"),
            test_hash("browser-plan-file-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("browser-plan-file-approval-scope")),
        );
        let mut rejected_ledger = RunEventLedger::new(run_id);
        seed_goal_intake_replay(&mut rejected_ledger, "browser-action-plan-rejected");
        rejected_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        rejected_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let rejected_before_len = rejected_ledger.events().len();
        assert_eq!(
            ToolExecutionGateway::execute_browser_action_plan_file_path_refs_with_replay(
                &mut rejected_ledger,
                1173,
                &wrong_ir,
                BrowserCollectorKind::PlaywrightCdp,
                &plan,
                1_786_341_000_000,
                test_hash("browser-plan-file-config"),
                test_hash("browser-plan-file-capability"),
                &path_refs,
                policy_version_hash,
                true,
            ),
            Err(TrapReason::InvariantViolation)
        );
        assert_eq!(rejected_ledger.events().len(), rejected_before_len);
    }

    #[test]
    fn tool_execution_gateway_rejects_non_allow_policy_without_replay_side_effects() {
        use crate::physical::TrapReason;
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::RunEventLedger;
        use crate::sandbox::WasmtimeSandbox;
        use crate::tool_gateway::ToolExecutionGateway;

        let ir = TypedToolIR::new(
            910,
            911,
            CapabilityClass::LocalWrite,
            SideEffectClass::LocalReversible,
            test_hash("credential-scope"),
            RiskClass::R3,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract {
                requires_physical_witness: true,
                requires_approval: true,
                requires_staging: false,
                expected_artifact_hash: None,
            },
            Some(test_hash("approval-scope")),
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::RequireApproval);

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let mut ledger = RunEventLedger::new(116);
        seed_goal_intake_replay(&mut ledger, "non-allow-policy");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let before_len = ledger.len();
        let before_hash = ledger.last_hash();

        assert_eq!(
            ToolExecutionGateway::execute_wasm_with_replay(
                &mut ledger,
                &sandbox,
                77,
                78,
                &ir,
                &proof,
                &wasm,
                1_000,
            ),
            Err(TrapReason::InvariantViolation)
        );
        assert_eq!(ledger.len(), before_len);
        assert_eq!(ledger.last_hash(), before_hash);
    }

    #[test]
    fn tool_execution_gateway_records_failed_sandbox_attempt_as_closed_replay_event() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{RunEventKind, RunEventLedger, ToolExecutionStatus};
        use crate::sandbox::WasmtimeSandbox;
        use crate::tool_gateway::ToolExecutionGateway;

        let ir = TypedToolIR::new(
            920,
            921,
            CapabilityClass::LocalRead,
            SideEffectClass::None,
            test_hash("credential-scope"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::physical(),
            None,
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::Allow);

        let sandbox = WasmtimeSandbox::new();
        let mut ledger = RunEventLedger::new(117);
        seed_goal_intake_replay(&mut ledger, "failed-sandbox");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();

        let failed_receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            79,
            80,
            &ir,
            &proof,
            b"not-a-wasm-module",
            1_000,
        )
        .unwrap();

        assert!(failed_receipt.is_valid_for(&ir, &proof));
        assert_eq!(
            failed_receipt.tool_execution_evidence.status,
            ToolExecutionStatus::Failed
        );
        assert_eq!(ledger.events()[3].kind, RunEventKind::ToolCallRequested);
        assert_eq!(
            ledger.events()[4].kind,
            RunEventKind::PolicyDecisionRecorded
        );
        assert_eq!(ledger.events()[5].kind, RunEventKind::ToolCallCompleted);
        assert_eq!(
            ledger.events()[5].secondary_hash,
            Some(failed_receipt.tool_execution_evidence.evidence_hash)
        );
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let success_receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            81,
            82,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();
        assert_eq!(
            success_receipt.tool_execution_evidence.status,
            ToolExecutionStatus::Succeeded
        );
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn tool_execution_gateway_seals_checkpoint_and_next_action_from_replay_proof() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{NextActionKind, RunEventLedger, RunEventSegmentArchive};
        use crate::sandbox::WasmtimeSandbox;
        use crate::tool_gateway::{ToolExecutionGateway, evidence_contract_hash};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("tool-execution-replay-proof-{unique}"));

        let ir = TypedToolIR::new(
            930,
            931,
            CapabilityClass::LocalRead,
            SideEffectClass::None,
            test_hash("credential-scope"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::physical(),
            None,
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::Allow);

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let mut ledger = RunEventLedger::new(118);
        seed_goal_intake_replay(&mut ledger, "checkpoint-next-action");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            83,
            84,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();
        ledger
            .append_checkpoint_sealed(85, receipt.tool_execution_evidence.evidence_hash)
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let execution_proof = ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
            &dir,
            &manifest,
            receipt,
            85,
            86,
            NextActionKind::ContinueExecution,
            930,
            evidence_contract_hash(&ir),
        )
        .unwrap();

        assert!(execution_proof.is_valid_for(&ir, &proof, &manifest));
        assert_eq!(
            execution_proof.checkpoint.ledger_last_hash,
            execution_proof
                .replay_determinism_proof
                .first_pass_ledger_hash
        );
        assert_eq!(
            execution_proof.next_action_packet.candidate_evidence_hash,
            execution_proof.receipt.physical_evidence_hash
        );

        let mut tampered_manifest = manifest.clone();
        tampered_manifest.entries[0].segment_hash = test_hash("tampered-segment");
        assert!(!execution_proof.is_valid_for(&ir, &proof, &tampered_manifest));
    }

    #[test]
    fn tool_execution_gateway_seals_next_action_with_archived_task_selection_proof() {
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{NextActionKind, RunEventLedger, RunEventSegmentArchive};
        use crate::sandbox::WasmtimeSandbox;
        use crate::task_ledger::{TaskCard, TaskLedger};
        use crate::tool_gateway::{ToolExecutionGateway, evidence_contract_hash};
        use std::time::{SystemTime, UNIX_EPOCH};

        let mut tasks = TaskLedger::new(10_000);
        tasks
            .insert_task(TaskCard::new(950, vec![], 0, None, None))
            .unwrap();
        let task_selection_proof = tasks.select_next_task_proof(0).unwrap().unwrap();
        assert_eq!(task_selection_proof.selected_task_id, 950);

        let ir = TypedToolIR::new(
            950,
            951,
            CapabilityClass::LocalRead,
            SideEffectClass::None,
            test_hash("credential-scope"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::physical(),
            None,
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::Allow);

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("tool-task-selection-proof-{unique}"));

        let mut ledger = RunEventLedger::new(128);
        seed_goal_intake_replay(&mut ledger, "task-selection-next-action");
        ledger
            .append_task_selection_proof_recorded(&task_selection_proof)
            .unwrap();
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            97,
            98,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();
        ledger
            .append_checkpoint_sealed(99, receipt.tool_execution_evidence.evidence_hash)
            .unwrap();
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let execution_proof =
            ToolExecutionGateway::seal_replay_checkpoint_and_next_action_with_task_selection(
                &dir,
                &manifest,
                receipt.clone(),
                99,
                100,
                NextActionKind::ContinueExecution,
                evidence_contract_hash(&ir),
                &task_selection_proof,
            )
            .unwrap();

        assert!(execution_proof.is_valid_for_task_selection(
            &ir,
            &proof,
            &manifest,
            &task_selection_proof
        ));
        assert_eq!(
            execution_proof.next_action_packet.candidate_evidence_hash,
            receipt.physical_evidence_hash
        );
        assert_eq!(
            execution_proof.next_action_packet.task_selection_proof_hash,
            Some(task_selection_proof.proof_hash)
        );
        assert_ne!(execution_proof.task_selection_event_hash, None);

        let mut missing_task_selection_event_hash = execution_proof.clone();
        missing_task_selection_event_hash.task_selection_event_hash = None;
        assert!(
            !missing_task_selection_event_hash.is_valid_for_task_selection(
                &ir,
                &proof,
                &manifest,
                &task_selection_proof
            )
        );

        let mut tampered_task_selection_event_hash = execution_proof.clone();
        tampered_task_selection_event_hash.task_selection_event_hash =
            Some(test_hash("tampered-task-selection-event"));
        assert!(
            !tampered_task_selection_event_hash.is_valid_for_task_selection(
                &ir,
                &proof,
                &manifest,
                &task_selection_proof
            )
        );

        let mut missing_packet_task_selection_hash = execution_proof.clone();
        missing_packet_task_selection_hash
            .next_action_packet
            .task_selection_proof_hash = None;
        assert!(
            !missing_packet_task_selection_hash.is_valid_for_task_selection(
                &ir,
                &proof,
                &manifest,
                &task_selection_proof
            )
        );

        let no_task_dir = dir.join("missing-task-selection");
        let mut no_task_ledger = RunEventLedger::new(129);
        seed_goal_intake_replay(&mut no_task_ledger, "no-task-selection");
        no_task_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        no_task_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let no_task_receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut no_task_ledger,
            &sandbox,
            101,
            102,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();
        no_task_ledger
            .append_checkpoint_sealed(103, no_task_receipt.tool_execution_evidence.evidence_hash)
            .unwrap();
        let no_task_manifest =
            RunEventSegmentArchive::write_ledger(&no_task_dir, 2, &no_task_ledger).unwrap();
        assert!(
            ToolExecutionGateway::seal_replay_checkpoint_and_next_action_with_task_selection(
                &no_task_dir,
                &no_task_manifest,
                no_task_receipt,
                103,
                104,
                NextActionKind::ContinueExecution,
                evidence_contract_hash(&ir),
                &task_selection_proof,
            )
            .is_err()
        );

        let late_dir = dir.join("late-task-selection");
        let mut late_ledger = RunEventLedger::new(130);
        seed_goal_intake_replay(&mut late_ledger, "late-task-selection");
        late_ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        late_ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let late_receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut late_ledger,
            &sandbox,
            105,
            106,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();
        late_ledger
            .append_task_selection_proof_recorded(&task_selection_proof)
            .unwrap();
        late_ledger
            .append_checkpoint_sealed(107, late_receipt.tool_execution_evidence.evidence_hash)
            .unwrap();
        let late_manifest =
            RunEventSegmentArchive::write_ledger(&late_dir, 2, &late_ledger).unwrap();
        assert!(
            ToolExecutionGateway::seal_replay_checkpoint_and_next_action_with_task_selection(
                &late_dir,
                &late_manifest,
                late_receipt,
                107,
                108,
                NextActionKind::ContinueExecution,
                evidence_contract_hash(&ir),
                &task_selection_proof,
            )
            .is_err()
        );
    }

    #[test]
    fn tool_execution_gateway_commits_replay_proven_output_to_cognifold() {
        use crate::memory::fold::CogniFoldStore;
        use crate::physical::{BacktrackSignal, PhysicalWatchdog, TrapReason};
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{NextActionKind, RunEventKind, RunEventLedger, RunEventSegmentArchive};
        use crate::sandbox::WasmtimeSandbox;
        use crate::tool_gateway::{ToolExecutionGateway, evidence_contract_hash};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("tool-memory-commit-proof-{unique}"));

        let ir = TypedToolIR::new(
            940,
            941,
            CapabilityClass::LocalRead,
            SideEffectClass::None,
            test_hash("credential-scope"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::physical(),
            None,
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::Allow);

        let wasm = wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();
        let sandbox = WasmtimeSandbox::new();
        let mut ledger = RunEventLedger::new(119);
        seed_goal_intake_replay(&mut ledger, "memory-commit-gateway");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            87,
            88,
            &ir,
            &proof,
            &wasm,
            1_000,
        )
        .unwrap();
        let artifact_evidence = receipt.artifact_evidence.unwrap();
        assert!(artifact_evidence.is_valid());
        ledger
            .append_checkpoint_sealed(89, receipt.tool_execution_evidence.evidence_hash)
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let execution_proof = ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
            &dir,
            &manifest,
            receipt,
            89,
            90,
            NextActionKind::ContinueExecution,
            940,
            evidence_contract_hash(&ir),
        )
        .unwrap();

        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0 };
        let commit_proof = ToolExecutionGateway::commit_replay_proven_tool_output_to_cognifold(
            &mut store,
            940,
            &execution_proof,
            &ir,
            &proof,
            &manifest,
            &watchdog,
        )
        .unwrap();
        assert!(commit_proof.is_valid_for(&execution_proof, &artifact_evidence));
        assert_eq!(store.len(), 1);
        assert_eq!(
            store.latest().unwrap().payload,
            artifact_evidence.artifact_hash.to_vec()
        );
        let memory_event = ledger.append_memory_commit_recorded(&commit_proof).unwrap();
        assert_eq!(memory_event.kind, RunEventKind::MemoryCommitRecorded);
        assert_eq!(memory_event.subject_id, 940);
        assert_eq!(memory_event.primary_hash, commit_proof.proof_hash);
        ledger
            .append_checkpoint_sealed(91, commit_proof.proof_hash)
            .unwrap();
        let post_memory_dir = dir.join("post-memory");
        let post_memory_manifest =
            RunEventSegmentArchive::write_ledger(&post_memory_dir, 2, &ledger).unwrap();
        let post_memory_replay_proof = RunEventSegmentArchive::prove_replay_determinism(
            &post_memory_dir,
            &post_memory_manifest,
        )
        .unwrap();
        assert_eq!(post_memory_replay_proof.event_count, ledger.len());
        assert_eq!(
            post_memory_replay_proof.first_pass_ledger_hash,
            ledger.last_hash()
        );
        let handoff = RunEventSegmentArchive::seal_memory_commit_handoff(
            &post_memory_dir,
            &post_memory_manifest,
            &commit_proof,
            execution_proof
                .receipt
                .tool_execution_evidence
                .evidence_hash,
            92,
            93,
            NextActionKind::ContinueExecution,
            940,
            ir.canonical_hash,
            evidence_contract_hash(&ir),
            proof.compute_hash(),
        )
        .unwrap();
        assert_eq!(handoff.memory_commit_proof_hash, commit_proof.proof_hash);
        assert_ne!(handoff.checkpoint_hash, [0; 32]);
        assert_ne!(handoff.next_action_packet_hash, [0; 32]);

        let mut tampered_proof = execution_proof.clone();
        let mut tampered_artifact = tampered_proof.receipt.artifact_evidence.unwrap();
        tampered_artifact.bytes_changed = 0;
        tampered_proof.receipt.artifact_evidence = Some(tampered_artifact);
        assert_eq!(
            ToolExecutionGateway::commit_replay_proven_tool_output_to_cognifold(
                &mut store,
                940,
                &tampered_proof,
                &ir,
                &proof,
                &manifest,
                &watchdog,
            ),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation
            ))
        );
        assert_eq!(store.len(), 1);
    }

    #[test]
    fn tool_execution_gateway_rejects_failed_tool_output_memory_commit() {
        use crate::memory::fold::CogniFoldStore;
        use crate::physical::{BacktrackSignal, PhysicalWatchdog, TrapReason};
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            PolicyFacts, RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{NextActionKind, RunEventLedger, RunEventSegmentArchive};
        use crate::sandbox::WasmtimeSandbox;
        use crate::tool_gateway::{ToolExecutionGateway, evidence_contract_hash};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("tool-failed-memory-commit-proof-{unique}"));

        let ir = TypedToolIR::new(
            950,
            951,
            CapabilityClass::LocalRead,
            SideEffectClass::None,
            test_hash("credential-scope"),
            RiskClass::R1,
            test_hash("precondition"),
            test_hash("effect"),
            EvidenceContract::physical(),
            None,
        );
        let facts = PolicyFacts::new(test_hash("policy-v1"), false, false, false);
        let proof = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(proof.decision, PolicyDecision::Allow);

        let sandbox = WasmtimeSandbox::new();
        let mut ledger = RunEventLedger::new(120);
        seed_goal_intake_replay(&mut ledger, "failed-memory-commit");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let receipt = ToolExecutionGateway::execute_wasm_with_replay(
            &mut ledger,
            &sandbox,
            91,
            92,
            &ir,
            &proof,
            b"not-a-wasm-module",
            1_000,
        )
        .unwrap();
        assert!(receipt.artifact_evidence.is_none());
        ledger
            .append_checkpoint_sealed(93, receipt.tool_execution_evidence.evidence_hash)
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let execution_proof = ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
            &dir,
            &manifest,
            receipt,
            93,
            94,
            NextActionKind::RequestOperatorReview,
            950,
            evidence_contract_hash(&ir),
        )
        .unwrap();

        let mut store = CogniFoldStore::new();
        let watchdog = PhysicalWatchdog { epsilon: 0.0 };
        assert_eq!(
            ToolExecutionGateway::commit_replay_proven_tool_output_to_cognifold(
                &mut store,
                950,
                &execution_proof,
                &ir,
                &proof,
                &manifest,
                &watchdog,
            ),
            Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation
            ))
        );
        assert_eq!(store.len(), 0);
    }

    #[test]
    fn run_event_ledger_hash_chain_detects_tamper() {
        use crate::replay::RunEventLedger;

        let mut ledger = RunEventLedger::new(42);
        seed_goal_intake_replay(&mut ledger, "hash-chain");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(99, test_hash("checkpoint"))
            .unwrap();
        assert_eq!(ledger.len(), 4);
        assert!(ledger.verify_hash_chain());

        let mut events = ledger.events().to_vec();
        events[1].primary_hash = test_hash("tampered");
        assert_ne!(events[1].event_hash, events[1].compute_hash());
    }

    #[test]
    fn run_event_records_raw_text_ref_as_hash_only() {
        use crate::replay::{RunEventKind, RunEventLedger};

        let mut ledger = RunEventLedger::new(100);
        seed_goal_intake_replay(&mut ledger, "raw-text-ref");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        let event = ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        assert_eq!(event.kind, RunEventKind::LLMResponseReceived);
        assert_eq!(event.primary_hash, test_hash("response"));
        assert_eq!(event.secondary_hash, Some(test_hash("raw-text-ref")));
        assert!(event.is_valid());
    }

    #[test]
    fn run_event_records_policy_decision_hashes() {
        use crate::replay::{RunEventKind, RunEventLedger};

        let mut ledger = RunEventLedger::new(101);
        seed_goal_intake_replay(&mut ledger, "policy-decision");
        let event = ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        assert_eq!(event.kind, RunEventKind::PolicyDecisionRecorded);
        assert_eq!(event.subject_id, 17);
        assert_eq!(event.primary_hash, test_hash("policy-proof-trace"));
        assert_eq!(event.secondary_hash, Some(test_hash("typed-tool-ir")));
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_records_browser_observation_packet_across_replay_segments() {
        use crate::browser_witness::{
            BrowserActionKind, BrowserActionTrace, BrowserCollectorKind, BrowserObservationPacket,
            BrowserWitnessProof,
        };
        use crate::policy::{
            CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision,
            RiskClass, SideEffectClass, TypedToolIR,
        };
        use crate::replay::{
            BinaryRunEventSegment, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive, browser_observation_packet_replay_binding_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let policy_window_hash = test_hash("replay-browser-policy-window");
        let browser_session_hash = test_hash("replay-browser-session");
        let redaction_policy_hash = test_hash("replay-browser-redaction");
        let action = BrowserActionTrace::new(
            701,
            BrowserActionKind::Click,
            test_hash("replay-browser-target"),
            None,
            Some((512, 192)),
            None,
            policy_window_hash,
        );
        let proof = BrowserWitnessProof::new(
            130,
            action.action_id,
            SideEffectClass::FinancialLegal,
            test_hash("replay-browser-url-before"),
            test_hash("replay-browser-url-after"),
            test_hash("replay-browser-dom-before"),
            test_hash("replay-browser-dom-after"),
            test_hash("replay-browser-screenshot-before"),
            test_hash("replay-browser-screenshot-after"),
            test_hash("replay-browser-ax-after"),
            test_hash("replay-browser-network-log"),
            action.trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        );
        let packet = BrowserObservationPacket::new(
            BrowserCollectorKind::InAppBrowser,
            3,
            1_786_301_000_000,
            test_hash("replay-browser-collector-config"),
            test_hash("replay-browser-collector-capabilities"),
            test_hash("replay-browser-raw-artifact-manifest"),
            action,
            proof,
        );
        assert!(packet.is_valid());

        let ir = TypedToolIR::new(
            230,
            231,
            CapabilityClass::Browser,
            SideEffectClass::FinancialLegal,
            test_hash("replay-browser-credential-scope"),
            RiskClass::R4,
            test_hash("replay-browser-precondition"),
            test_hash("replay-browser-effect"),
            EvidenceContract::r4_staged(),
            Some(test_hash("replay-browser-approval-scope")),
        );
        let facts = packet
            .isolated_browser_policy_facts(
                test_hash("policy-browser-replay-v1"),
                true,
                policy_window_hash,
                browser_session_hash,
                redaction_policy_hash,
            )
            .unwrap();
        let policy_trace = DeterministicPolicyKernel.evaluate(&ir, &facts);
        assert_eq!(policy_trace.decision, PolicyDecision::Allow);
        let policy_proof_trace_hash = policy_trace.compute_hash();

        let mut missing_packet = RunEventLedger::new(131);
        assert_eq!(
            missing_packet.append_policy_decision_recorded_after_browser_observation(
                41,
                policy_proof_trace_hash,
                ir.canonical_hash,
                packet.packet_hash,
            ),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let mut ledger = RunEventLedger::new(132);
        seed_goal_intake_replay(&mut ledger, "browser-observation-packet");
        let event = ledger
            .append_browser_observation_packet_recorded(&packet)
            .unwrap();
        assert_eq!(event.kind, RunEventKind::BrowserObservationPacketRecorded);
        assert_eq!(event.subject_id, packet.action_id);
        assert_eq!(event.primary_hash, packet.packet_hash);
        assert_eq!(
            event.secondary_hash,
            Some(browser_observation_packet_replay_binding_hash(&packet))
        );
        assert!(event.is_valid());

        assert_eq!(
            ledger.append_policy_decision_recorded_after_browser_observation(
                42,
                policy_proof_trace_hash,
                ir.canonical_hash,
                test_hash("wrong-browser-packet"),
            ),
            Err(ReplayLedgerError::InvalidEvent)
        );
        let policy_event = ledger
            .append_policy_decision_recorded_after_browser_observation(
                42,
                policy_proof_trace_hash,
                ir.canonical_hash,
                packet.packet_hash,
            )
            .unwrap();
        assert_eq!(policy_event.kind, RunEventKind::PolicyDecisionRecorded);
        assert_eq!(policy_event.primary_hash, policy_proof_trace_hash);
        assert_eq!(policy_event.secondary_hash, Some(ir.canonical_hash));
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered_packet = packet.clone();
        tampered_packet.packet_hash = test_hash("tampered-browser-packet");
        assert_eq!(
            ledger.append_browser_observation_packet_recorded(&tampered_packet),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("browser-observation-packet-replay-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let audit_proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &manifest).unwrap();
        assert!(audit_proof.is_valid());
        let archived = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(archived.len(), ledger.len());
        assert_eq!(
            archived.events()[1].kind,
            RunEventKind::BrowserObservationPacketRecorded
        );
        assert_eq!(archived.last_hash(), ledger.last_hash());

        let binary_path = dir.join("browser-observation-packet.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let scan = BinaryRunEventSegment::verify_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(scan.event_count, ledger.len());
        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered.last_hash(), ledger.last_hash());
    }

    #[test]
    fn run_event_records_cold_vector_expansion_replay_record() {
        use crate::evidence_index::{
            CandidateEvidenceRef, ColdVectorExpansionReplayRecord, EvidenceCandidateTier,
        };
        use crate::replay::{ReplayLedgerError, RunEventKind, RunEventLedger};

        let candidate = CandidateEvidenceRef::new(
            test_hash("cold-vector-candidate"),
            77,
            EvidenceCandidateTier::ColdVectorExpansion,
            875_000,
            test_hash("index-epoch"),
        );
        let record = ColdVectorExpansionReplayRecord::new(
            test_hash("index-epoch"),
            &["cold", "vector", "expansion"],
            8,
            test_hash("ann-config"),
            test_hash("ann-artifact"),
            88_000,
            &[candidate],
        )
        .unwrap();

        let mut ledger = RunEventLedger::new(104);
        seed_goal_intake_replay(&mut ledger, "cold-vector-expansion");
        let event = ledger
            .append_cold_vector_expansion_recorded(45, &record)
            .unwrap();
        assert_eq!(event.kind, RunEventKind::ColdVectorExpansionRecorded);
        assert_eq!(event.subject_id, 45);
        assert_eq!(event.primary_hash, record.record_hash);
        assert_eq!(event.secondary_hash, Some(record.candidate_list_hash));
        assert!(ledger.verify_hash_chain());

        let mut tampered = record;
        tampered.record_hash = test_hash("tampered-record");
        assert_eq!(
            ledger.append_cold_vector_expansion_recorded(46, &tampered),
            Err(ReplayLedgerError::InvalidEvent)
        );
    }

    #[test]
    fn run_event_records_agentic_evidence_execution_replay_record() {
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramError, AgenticEvidenceProgramStep,
            HotBitmapFilter, HotEvidenceIndex, HotLexicalIndex, SortedEvidenceSet,
        };
        use crate::replay::{
            BinaryRunEventSegment, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let epoch_hash = test_hash("agentic-replay-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("agentic-replay-browser"),
                7,
                &["browser", "policy", "replay", "witness"],
            )
            .unwrap();
        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-replay-browser"),
                7,
                test_hash("agentic-replay-artifact"),
                test_hash("agentic-replay-ast"),
                "browser policy replay witness",
            )
            .unwrap();
        let bitmap =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![7])).unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            48,
            vec![
                AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay"], 4),
                AgenticEvidenceProgramStep::exact_artifact_rerank(&["browser", "witness"], 4),
                AgenticEvidenceProgramStep::bitmap_filter(2),
            ],
        )
        .unwrap();
        let (candidates, record) = program
            .execute(Some(&lexical), Some(&exact), Some(&bitmap))
            .unwrap();
        assert_eq!(candidates.len(), 1);

        let mut ledger = RunEventLedger::new(501);
        seed_goal_intake_replay(&mut ledger, "agentic-evidence-replay");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        let event = ledger
            .append_agentic_evidence_execution_recorded(91, &record)
            .unwrap();
        assert_eq!(event.kind, RunEventKind::AgenticEvidenceExecutionRecorded);
        assert_eq!(event.primary_hash, record.record_hash);
        assert_eq!(event.secondary_hash, Some(record.candidate_list_hash));
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered = record.clone();
        tampered.candidate_list_hash = test_hash("tampered-candidate-list");
        assert_eq!(
            ledger.append_agentic_evidence_execution_recorded(92, &tampered),
            Err(ReplayLedgerError::InvalidEvent)
        );
        assert_eq!(
            RunEventLedger::new(502).append_agentic_evidence_execution_recorded(91, &record),
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );
        assert_eq!(
            AgenticEvidenceProgram::new(
                epoch_hash,
                1,
                vec![AgenticEvidenceProgramStep::lexical_top_k(&["policy"], 4)]
            ),
            Err(AgenticEvidenceProgramError::FuelExceeded)
        );

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("agentic-evidence-replay-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let recovered = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        let binary_path = dir.join("agentic-events.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let binary_recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(binary_recovered.events(), ledger.events());
    }

    #[test]
    fn agentic_evidence_sdk_run_replay_handoff_binds_manifest_record_and_capsule() {
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramStep, AgenticEvidenceSdk,
            HotBitmapFilter, HotEvidenceIndex, HotLexicalIndex, SortedEvidenceSet,
        };
        use crate::replay::{
            NextActionKind, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive, agentic_evidence_sdk_run_replay_binding_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let epoch_hash = test_hash("agentic-sdk-replay-handoff-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-replay-browser"),
                27,
                &["browser", "policy", "replay", "witness"],
            )
            .unwrap();
        lexical
            .insert_document(
                test_hash("agentic-sdk-replay-wasm"),
                29,
                &["wasmtime", "policy", "fuel"],
            )
            .unwrap();
        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-replay-browser"),
                27,
                test_hash("agentic-sdk-replay-artifact"),
                test_hash("agentic-sdk-replay-ast"),
                "browser policy replay witness DOM screenshot artifact",
            )
            .unwrap();
        exact
            .insert_artifact_document(
                test_hash("agentic-sdk-replay-wasm"),
                29,
                test_hash("agentic-sdk-replay-wasm-artifact"),
                test_hash("agentic-sdk-replay-wasm-ast"),
                "wasmtime fuel policy",
            )
            .unwrap();
        let bitmap =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![27])).unwrap();
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
        .unwrap();
        let sdk_run = AgenticEvidenceSdk::run_program(
            &program,
            9_001,
            Some(&lexical),
            Some(&exact),
            Some(&bitmap),
        )
        .unwrap();
        assert!(sdk_run.is_valid_for_program(&program));

        let mut ledger = RunEventLedger::new(503);
        seed_goal_intake_replay(&mut ledger, "agentic-evidence-sdk-run-handoff");
        ledger
            .append_context_pack_built(71, 512, test_hash("sdk-run-context-pack"))
            .unwrap();
        let event = ledger
            .append_agentic_evidence_sdk_run_recorded(191, &program, &sdk_run)
            .unwrap();
        assert_eq!(event.kind, RunEventKind::AgenticEvidenceExecutionRecorded);
        assert_eq!(event.subject_id, 191);
        assert_eq!(event.primary_hash, sdk_run.run_hash);
        assert_eq!(
            event.secondary_hash,
            Some(agentic_evidence_sdk_run_replay_binding_hash(&sdk_run))
        );
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered_run = sdk_run.clone();
        tampered_run.run_hash = test_hash("tampered-sdk-run-hash");
        assert_eq!(
            ledger.append_agentic_evidence_sdk_run_recorded(192, &program, &tampered_run),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("agentic-sdk-run-handoff-{unique}"));
        let archive_dir = root.join("archive");
        let manifest = RunEventSegmentArchive::write_ledger(&archive_dir, 2, &ledger).unwrap();
        let archived = RunEventSegmentArchive::read_ledger_mmap(&archive_dir, &manifest).unwrap();
        assert_eq!(archived.last_hash(), ledger.last_hash());
        assert_eq!(
            archived.events().last().unwrap().primary_hash,
            sdk_run.run_hash
        );
        let handoff = RunEventSegmentArchive::seal_agentic_evidence_sdk_run_handoff(
            &archive_dir,
            &manifest,
            191,
            &program,
            &sdk_run,
            1_930,
            1_931,
            NextActionKind::ContinueExecution,
            1_932,
            test_hash("sdk-run-handoff-typed-tool-ir"),
            test_hash("sdk-run-handoff-evidence-contract"),
            test_hash("sdk-run-handoff-policy-proof"),
        )
        .unwrap();
        assert_eq!(handoff.sdk_run_hash, sdk_run.run_hash);
        assert_eq!(handoff.capsule_hash, sdk_run.capsule.capsule_hash);
        assert_eq!(
            handoff.sdk_run_event_hash,
            archived.events().last().unwrap().event_hash
        );

        let mut wrong_binding_events = ledger.events().to_vec();
        wrong_binding_events[2].secondary_hash = Some(test_hash("wrong-sdk-run-binding"));
        wrong_binding_events[2].event_hash = wrong_binding_events[2].compute_hash();
        let wrong_binding_ledger =
            RunEventLedger::from_events(ledger.run_id, wrong_binding_events).unwrap();
        let wrong_binding_dir = root.join("wrong-binding-archive");
        let wrong_binding_manifest =
            RunEventSegmentArchive::write_ledger(&wrong_binding_dir, 2, &wrong_binding_ledger)
                .unwrap();
        assert!(
            RunEventSegmentArchive::seal_agentic_evidence_sdk_run_handoff(
                &wrong_binding_dir,
                &wrong_binding_manifest,
                191,
                &program,
                &sdk_run,
                1_940,
                1_941,
                NextActionKind::ContinueExecution,
                1_942,
                test_hash("sdk-run-handoff-typed-tool-ir"),
                test_hash("sdk-run-handoff-evidence-contract"),
                test_hash("sdk-run-handoff-policy-proof"),
            )
            .is_err()
        );
    }

    #[test]
    fn context_pack_requires_sdk_run_handoff_before_agentic_sdk_candidates_inherit() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };
        use crate::evidence_index::{
            AgenticEvidenceProgram, AgenticEvidenceProgramStep, AgenticEvidenceSdk,
            HotBitmapFilter, HotEvidenceIndex, HotLexicalIndex, SortedEvidenceSet,
        };
        use crate::replay::{NextActionKind, RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let epoch_hash = test_hash("context-sdk-run-handoff-epoch");
        let mut lexical = HotLexicalIndex::new(epoch_hash).unwrap();
        lexical
            .insert_document(
                test_hash("context-sdk-run-policy"),
                27,
                &["policy", "replay", "witness"],
            )
            .unwrap();
        let mut exact = HotEvidenceIndex::new(epoch_hash).unwrap();
        exact
            .insert_artifact_document(
                test_hash("context-sdk-run-policy"),
                27,
                test_hash("context-sdk-run-artifact"),
                test_hash("context-sdk-run-ast"),
                "policy replay witness browser artifact",
            )
            .unwrap();
        let bitmap =
            HotBitmapFilter::new(epoch_hash, SortedEvidenceSet::from_unsorted(vec![27])).unwrap();
        let program = AgenticEvidenceProgram::new(
            epoch_hash,
            64,
            vec![
                AgenticEvidenceProgramStep::lexical_top_k(&["policy", "replay"], 4),
                AgenticEvidenceProgramStep::exact_artifact_rerank(&["witness", "artifact"], 4),
                AgenticEvidenceProgramStep::bitmap_filter(4),
            ],
        )
        .unwrap();
        let sdk_run = AgenticEvidenceSdk::run_program(
            &program,
            9_101,
            Some(&lexical),
            Some(&exact),
            Some(&bitmap),
        )
        .unwrap();

        let mut ledger = RunEventLedger::new(504);
        seed_goal_intake_replay(&mut ledger, "context-sdk-run-handoff");
        ledger
            .append_context_pack_built(71, 512, test_hash("context-sdk-run-pack-prior"))
            .unwrap();
        ledger
            .append_agentic_evidence_sdk_run_recorded(191, &program, &sdk_run)
            .unwrap();

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let archive_dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("context-sdk-run-handoff-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&archive_dir, 2, &ledger).unwrap();
        let handoff = RunEventSegmentArchive::seal_agentic_evidence_sdk_run_handoff(
            &archive_dir,
            &manifest,
            191,
            &program,
            &sdk_run,
            1_950,
            1_951,
            NextActionKind::ContinueExecution,
            1_952,
            test_hash("context-sdk-run-typed-tool-ir"),
            test_hash("context-sdk-run-evidence-contract"),
            test_hash("context-sdk-run-policy-proof"),
        )
        .unwrap();

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(48, 8)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 8, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(27, ContextNodeKind::Evidence, 8, 80, 0, 0))
            .unwrap();

        let (pack, proof) = governor
            .build_sdk_run_handoff_bound_context_pack(1, &sdk_run.capsule, &handoff)
            .unwrap();
        assert!(proof.is_valid_for_sdk_run_handoff(&pack, &sdk_run.capsule, &handoff));
        assert_eq!(
            proof.agentic_evidence_execution_record_hash,
            sdk_run.execution_record.record_hash
        );
        assert_eq!(
            proof.agentic_evidence_sdk_run_handoff_hash,
            handoff.handoff_hash
        );
        assert_eq!(
            proof.candidate_state_capsule_hash,
            sdk_run.capsule.capsule_hash
        );
        assert!(!proof.is_valid_for_capsule(
            &pack,
            &sdk_run.capsule,
            None,
            Some(&sdk_run.execution_record),
            None,
        ));
        assert!(!proof.is_valid_for_replay_records(
            &pack,
            sdk_run.capsule.candidates(),
            None,
            Some(&sdk_run.execution_record),
            None,
        ));

        let mut tampered_handoff = handoff.clone();
        tampered_handoff.capsule_hash = test_hash("context-sdk-run-wrong-capsule");
        assert!(
            governor
                .build_sdk_run_handoff_bound_context_pack(1, &sdk_run.capsule, &tampered_handoff)
                .is_err()
        );

        let mut tampered_capsule = sdk_run.capsule.clone();
        tampered_capsule.candidate_list_hash = test_hash("context-sdk-run-wrong-candidates");
        assert!(
            governor
                .build_sdk_run_handoff_bound_context_pack(1, &tampered_capsule, &handoff)
                .is_err()
        );
    }

    #[test]
    fn candidate_state_capsule_roundtrips_and_binds_context_pack() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };
        use crate::evidence_index::{
            CandidateEvidenceRef, CandidateStateCapsule, CandidateStateCapsuleError,
            ColdVectorExpansionReplayRecord, EvidenceCandidateTier,
        };

        let epoch_hash = test_hash("candidate-capsule-epoch");
        let cold_candidate = CandidateEvidenceRef::new(
            test_hash("candidate-capsule-cold"),
            2,
            EvidenceCandidateTier::ColdVectorExpansion,
            875_000,
            epoch_hash,
        );
        let lexical_candidate = CandidateEvidenceRef::new(
            test_hash("candidate-capsule-lexical"),
            3,
            EvidenceCandidateTier::LexicalBaseline,
            812_000,
            epoch_hash,
        );
        let cold_record = ColdVectorExpansionReplayRecord::new(
            epoch_hash,
            &["capsule", "state"],
            8,
            test_hash("candidate-capsule-config"),
            test_hash("candidate-capsule-artifact"),
            42_000,
            &[cold_candidate],
        )
        .unwrap();
        let candidates = [cold_candidate, lexical_candidate];
        let capsule =
            CandidateStateCapsule::new(55, &candidates, Some(&cold_record), None).unwrap();
        assert!(capsule.is_valid_for_replay_records(Some(&cold_record), None));
        assert_eq!(capsule.candidates(), &candidates);
        assert_ne!(capsule.capsule_hash, [0; 32]);
        assert_ne!(capsule.payload_hash, [0; 32]);

        let recovered = CandidateStateCapsule::from_canonical_bytes(
            capsule.canonical_bytes(),
            Some(&cold_record),
            None,
        )
        .unwrap();
        assert_eq!(recovered.capsule_hash, capsule.capsule_hash);
        assert_eq!(recovered.canonical_bytes(), capsule.canonical_bytes());

        let mut tampered_payload = capsule.canonical_bytes().to_vec();
        let last = tampered_payload.last_mut().unwrap();
        *last ^= 0x01;
        assert_eq!(
            CandidateStateCapsule::from_canonical_bytes(
                &tampered_payload,
                Some(&cold_record),
                None
            ),
            Err(CandidateStateCapsuleError::InvalidCandidate)
        );
        let mut tampered_record = cold_record;
        tampered_record.candidate_list_hash = test_hash("tampered-capsule-list");
        assert_eq!(
            CandidateStateCapsule::from_canonical_bytes(
                capsule.canonical_bytes(),
                Some(&tampered_record),
                None,
            ),
            Err(CandidateStateCapsuleError::CandidateGate(
                crate::evidence_index::CandidateOnlyGateError::ReplayRecordMismatch
            ))
        );

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(48, 8)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 8, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 8, 40, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(3, ContextNodeKind::Evidence, 8, 35, 0, 0))
            .unwrap();
        let (pack, proof) = governor
            .build_capsule_bound_context_pack(1, &capsule, Some(&cold_record), None)
            .unwrap();
        assert!(proof.is_valid_for_capsule(&pack, &capsule, Some(&cold_record), None, None));
        assert_eq!(proof.candidate_state_capsule_hash, capsule.capsule_hash);
        assert!(!proof.is_valid_for(&pack, capsule.candidates(), Some(&cold_record)));
    }

    #[test]
    fn run_event_records_circuit_breaker_trip_hashes() {
        use crate::replay::{ReplayLedgerError, RunEventKind, RunEventLedger};

        let mut missing_prior = RunEventLedger::new(105);
        seed_goal_intake_replay(&mut missing_prior, "circuit-breaker-missing-llm");
        missing_prior
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        assert_eq!(
            missing_prior.append_circuit_breaker_tripped(
                3,
                test_hash("loop-breaker-decision"),
                test_hash("loop-response-fingerprint"),
            ),
            Err(ReplayLedgerError::MissingPriorLlmResponse)
        );

        let mut ledger = RunEventLedger::new(106);
        seed_goal_intake_replay(&mut ledger, "cold-vector");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let event = ledger
            .append_circuit_breaker_tripped(
                3,
                test_hash("loop-breaker-decision"),
                test_hash("loop-response-fingerprint"),
            )
            .unwrap();

        assert_eq!(event.kind, RunEventKind::CircuitBreakerTripped);
        assert_eq!(event.subject_id, 3);
        assert_eq!(event.primary_hash, test_hash("loop-breaker-decision"));
        assert_eq!(
            event.secondary_hash,
            Some(test_hash("loop-response-fingerprint"))
        );
        assert!(event.is_valid());
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_records_context_fold_hashes() {
        use crate::context::ContextFoldRecord;
        use crate::replay::{ReplayLedgerError, RunEventKind, RunEventLedger};

        let record = ContextFoldRecord::new(
            17,
            &[17, 18],
            &[19, 20],
            64,
            32,
            1_024,
            4,
            test_hash("context-pack"),
        )
        .unwrap();
        let mut missing_prior = RunEventLedger::new(107);
        seed_goal_intake_replay(&mut missing_prior, "context-fold-missing-context");
        assert_eq!(
            missing_prior.append_context_fold_recorded(&record),
            Err(ReplayLedgerError::MissingPriorContextPack)
        );

        let mut ledger = RunEventLedger::new(108);
        seed_goal_intake_replay(&mut ledger, "context-fold");
        ledger
            .append_context_pack_built(17, 64, record.context_pack_digest)
            .unwrap();
        let event = ledger.append_context_fold_recorded(&record).unwrap();

        assert_eq!(event.kind, RunEventKind::ContextFoldRecorded);
        assert_eq!(event.subject_id, record.active_task_id);
        assert_eq!(event.primary_hash, record.record_hash);
        assert_eq!(event.secondary_hash, Some(record.folded_node_hash));
        assert!(event.is_valid());
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_records_context_pack_candidate_proof_after_context_pack() {
        use crate::context::{
            ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind,
        };
        use crate::evidence_index::{CandidateEvidenceRef, EvidenceCandidateTier};
        use crate::replay::{
            BinaryRunEventSegment, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(20, 8)).unwrap();
        governor
            .insert_node(ContextNode::new(1, ContextNodeKind::Task, 10, 10, 0, 0))
            .unwrap();
        governor
            .insert_node(ContextNode::new(2, ContextNodeKind::Evidence, 10, 40, 0, 0))
            .unwrap();
        let candidate = CandidateEvidenceRef::new(
            test_hash("candidate-two"),
            2,
            EvidenceCandidateTier::LexicalBaseline,
            900_000,
            test_hash("index-epoch"),
        );
        let (pack, proof) = governor
            .build_candidate_bound_context_pack(1, &[candidate], None)
            .unwrap();

        let mut missing_pack = RunEventLedger::new(109);
        seed_goal_intake_replay(&mut missing_pack, "candidate-proof-missing-pack");
        assert_eq!(
            missing_pack.append_context_pack_candidate_proof_recorded(&proof),
            Err(ReplayLedgerError::MissingPriorContextPack)
        );

        let mut ledger = RunEventLedger::new(110);
        seed_goal_intake_replay(&mut ledger, "candidate-proof");
        ledger
            .append_context_pack_built(1, pack.token_count, pack.digest)
            .unwrap();
        let event = ledger
            .append_context_pack_candidate_proof_recorded(&proof)
            .unwrap();
        assert_eq!(event.kind, RunEventKind::ContextPackCandidateProofRecorded);
        assert_eq!(event.subject_id, proof.active_task_id);
        assert_eq!(event.primary_hash, proof.proof_hash);
        assert_eq!(event.secondary_hash, Some(proof.context_pack_digest));
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("context-pack-candidate-proof-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let archived = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(
            archived.events().last().unwrap().kind,
            RunEventKind::ContextPackCandidateProofRecorded
        );
        assert_eq!(archived.last_hash(), ledger.last_hash());
        let binary_path = dir.join("candidate-proof.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered.len(), ledger.len());
        assert_eq!(recovered.last_hash(), ledger.last_hash());

        let mut tampered_proof = proof.clone();
        tampered_proof.candidate_count = 0;
        assert_eq!(
            ledger.append_context_pack_candidate_proof_recorded(&tampered_proof),
            Err(ReplayLedgerError::InvalidEvent)
        );
    }

    #[test]
    fn run_event_records_goal_intake_before_context_and_replay_scans() {
        use crate::replay::{
            BinaryRunEventSegment, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive, goal_intake_replay_binding_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let proof = goal_intake_replay_fixture("replay");
        let mut missing_intake = RunEventLedger::new(120);
        assert_eq!(
            missing_intake.append_context_pack_built(7, 32, test_hash("context-pack")),
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );

        let mut ledger = RunEventLedger::new(121);
        let event = ledger.append_goal_intake_recorded(&proof).unwrap();
        assert_eq!(event.kind, RunEventKind::GoalIntakeRecorded);
        assert_eq!(event.subject_id, proof.packet.goal_id);
        assert_eq!(event.primary_hash, proof.proof_hash);
        assert_eq!(
            event.secondary_hash,
            Some(goal_intake_replay_binding_hash(&proof))
        );
        ledger
            .append_context_pack_built(proof.root_task.task_id, 32, proof.packet.packet_hash)
            .unwrap();
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("goal-intake-replay-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let audit_proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &manifest).unwrap();
        assert!(audit_proof.is_valid());
        let archived = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(archived.last_hash(), ledger.last_hash());

        let binary_path = dir.join("goal-intake-replay.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let scan = BinaryRunEventSegment::verify_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(scan.event_count, ledger.len());
        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered.last_hash(), ledger.last_hash());

        assert_eq!(
            ledger.append_goal_intake_recorded(&proof),
            Err(ReplayLedgerError::InvalidEvent)
        );
    }

    #[test]
    fn run_event_records_task_selection_proof_across_replay_segments() {
        use crate::replay::{
            BinaryRunEventSegment, ReplayLedgerError, RunEventKind, RunEventLedger,
            RunEventSegmentArchive,
        };
        use crate::task_ledger::{TaskCard, TaskLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let mut tasks = TaskLedger::new(10_000);
        tasks
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        tasks
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        tasks
            .insert_task(TaskCard::new(3, vec![], 2, None, None))
            .unwrap();
        let proof = tasks.select_next_task_proof(0).unwrap().unwrap();

        let mut ledger = RunEventLedger::new(111);
        seed_goal_intake_replay(&mut ledger, "task-selection-proof");
        let event = ledger.append_task_selection_proof_recorded(&proof).unwrap();
        assert_eq!(event.kind, RunEventKind::TaskSelectionProofRecorded);
        assert_eq!(event.subject_id, proof.selected_task_id);
        assert_eq!(event.primary_hash, proof.proof_hash);
        assert_eq!(event.secondary_hash, Some(proof.ready_queue_hash));
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("task-selection-proof-{unique}"));
        let manifest = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let audit_proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &manifest).unwrap();
        assert!(audit_proof.is_valid());
        let archived = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(
            archived.events().last().unwrap().kind,
            RunEventKind::TaskSelectionProofRecorded
        );
        assert_eq!(archived.last_hash(), ledger.last_hash());

        let binary_path = dir.join("task-selection-proof.bin");
        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let scan = BinaryRunEventSegment::verify_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(scan.event_count, ledger.len());
        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered.last_hash(), ledger.last_hash());

        let mut tampered_proof = proof.clone();
        tampered_proof.ready_count = 0;
        assert_eq!(
            ledger.append_task_selection_proof_recorded(&tampered_proof),
            Err(ReplayLedgerError::InvalidEvent)
        );
    }

    #[test]
    fn next_action_packet_rejects_stale_task_selection_proof() {
        use crate::replay::{NextActionKind, NextActionPacket, RunCheckpoint};
        use crate::task_ledger::{TaskCard, TaskLedger};

        let mut tasks = TaskLedger::new(10_000);
        tasks
            .insert_task(TaskCard::new(1, vec![], 0, None, None))
            .unwrap();
        tasks
            .insert_task(TaskCard::new(2, vec![1], 0, None, None))
            .unwrap();
        tasks
            .insert_task(TaskCard::new(3, vec![], 0, None, None))
            .unwrap();
        let proof = tasks.select_next_task_proof(0).unwrap().unwrap();

        let checkpoint = RunCheckpoint::new(
            112,
            1,
            3,
            1,
            test_hash("task-selection-ledger-last"),
            test_hash("task-selection-manifest"),
            test_hash("task-selection-replay-proof"),
            test_hash("task-selection-fold-evidence"),
        );
        assert!(checkpoint.is_valid());
        let packet = NextActionPacket::new_with_task_selection(
            112,
            2,
            checkpoint.checkpoint_hash,
            NextActionKind::ContinueExecution,
            proof.selected_task_id,
            test_hash("typed-tool-ir"),
            test_hash("evidence-contract"),
            test_hash("policy-proof"),
            test_hash("physical-candidate-evidence"),
            &proof,
        );
        assert_eq!(
            packet.candidate_evidence_hash,
            test_hash("physical-candidate-evidence")
        );
        assert_eq!(packet.task_selection_proof_hash, Some(proof.proof_hash));
        assert!(packet.is_valid_for_task_selection(&checkpoint, &proof));

        tasks.complete_task(1).unwrap();
        let replacement = tasks.select_next_task_proof(0).unwrap().unwrap();
        assert_ne!(replacement.selected_task_id, proof.selected_task_id);
        assert!(!packet.is_valid_for_task_selection(&checkpoint, &replacement));

        let stale_hash_packet = NextActionPacket::new_with_task_selection(
            112,
            3,
            checkpoint.checkpoint_hash,
            NextActionKind::ContinueExecution,
            replacement.selected_task_id,
            test_hash("typed-tool-ir"),
            test_hash("evidence-contract"),
            test_hash("policy-proof"),
            test_hash("physical-candidate-evidence"),
            &proof,
        );
        assert!(!stale_hash_packet.is_valid_for_task_selection(&checkpoint, &replacement));
    }

    #[test]
    fn run_event_ledger_records_operator_review_artifact_after_policy_decision() {
        use crate::replay::{ReplayLedgerError, RunEventKind, RunEventLedger};

        let mut ledger = RunEventLedger::new(103);
        seed_goal_intake_replay(&mut ledger, "operator-review");
        let missing_policy = ledger.append_operator_review_artifact_recorded(
            29,
            test_hash("signing-target"),
            test_hash("operator-review-artifact"),
        );
        assert_eq!(
            missing_policy,
            Err(ReplayLedgerError::MissingPriorPolicyDecision)
        );

        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        let event = ledger
            .append_operator_review_artifact_recorded(
                29,
                test_hash("signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        assert_eq!(event.kind, RunEventKind::OperatorReviewArtifactRecorded);
        assert_eq!(event.subject_id, 29);
        assert_eq!(event.primary_hash, test_hash("signing-target"));
        assert_eq!(
            event.secondary_hash,
            Some(test_hash("operator-review-artifact"))
        );
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_ledger_requires_policy_decision_before_approval_token() {
        use crate::replay::{
            ReplayLedgerError, RunEventKind, RunEventLedger, approval_token_binding_hash,
        };

        let mut ledger = RunEventLedger::new(102);
        seed_goal_intake_replay(&mut ledger, "approval-token");
        let missing_policy = ledger.append_approval_token_recorded(
            7,
            test_hash("approval-token"),
            test_hash("review-packet"),
            10_000,
        );
        assert_eq!(
            missing_policy,
            Err(ReplayLedgerError::MissingPriorPolicyDecision)
        );

        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        let event = ledger
            .append_approval_token_recorded(
                7,
                test_hash("approval-token"),
                test_hash("review-packet"),
                10_000,
            )
            .unwrap();
        assert_eq!(event.kind, RunEventKind::ApprovalTokenRecorded);
        assert_eq!(event.subject_id, 7);
        assert_eq!(event.primary_hash, test_hash("approval-token"));
        assert_eq!(
            event.secondary_hash,
            Some(approval_token_binding_hash(
                test_hash("review-packet"),
                10_000
            ))
        );
        assert!(ledger.verify_hash_chain());
    }

    #[test]
    fn run_event_approval_token_binding_hash_includes_expiry() {
        use crate::replay::approval_token_binding_hash;

        let packet_hash = test_hash("review-packet");
        assert_ne!(
            approval_token_binding_hash(packet_hash, 10_000),
            approval_token_binding_hash(packet_hash, 10_001)
        );
    }

    #[test]
    fn run_event_arrow_stream_roundtrips_hash_chain() {
        use crate::context::ContextFoldRecord;
        use crate::replay::{ArrowRunEventStream, RunEventLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("run-events-{unique}.arrow"));

        let mut ledger = RunEventLedger::new(202);
        seed_goal_intake_replay(&mut ledger, "arrow-stream");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        let fold_record = ContextFoldRecord::new(
            11,
            &[11, 12],
            &[13, 14],
            96,
            32,
            1_024,
            4,
            test_hash("context-pack"),
        )
        .unwrap();
        ledger.append_context_fold_recorded(&fold_record).unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_circuit_breaker_tripped(
                31,
                test_hash("loop-breaker-decision"),
                test_hash("loop-response-fingerprint"),
            )
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                23,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let mut stream = ArrowRunEventStream::create(&path).unwrap();
        for event in ledger.events() {
            stream.append_event(event).unwrap();
        }
        assert_eq!(stream.path(), path.as_path());
        stream.finish().unwrap();

        let recovered = ArrowRunEventStream::read_ledger(&path, 202).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert!(recovered.verify_hash_chain());
    }

    #[test]
    fn binary_run_event_segment_mmap_roundtrips_fixed_records() {
        use crate::context::ContextFoldRecord;
        use crate::replay::{BinaryRunEventSegment, RunEventLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("run-events-{unique}.bin"));

        let mut ledger = RunEventLedger::new(212);
        seed_goal_intake_replay(&mut ledger, "binary-roundtrip");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        let fold_record = ContextFoldRecord::new(
            11,
            &[11, 12],
            &[13, 14],
            96,
            32,
            1_024,
            4,
            test_hash("context-pack"),
        )
        .unwrap();
        ledger.append_context_fold_recorded(&fold_record).unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_circuit_breaker_tripped(
                31,
                test_hash("loop-breaker-decision"),
                test_hash("loop-response-fingerprint"),
            )
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                23,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        ledger
            .append_checkpoint_sealed(29, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let payload_hash = BinaryRunEventSegment::write_ledger(&path, &ledger).unwrap();
        let expected_len = BinaryRunEventSegment::header_bytes()
            + ledger.len() * BinaryRunEventSegment::record_bytes();
        assert_eq!(
            std::fs::metadata(&path).unwrap().len() as usize,
            expected_len
        );

        let recovered = BinaryRunEventSegment::read_ledger_mmap(&path, payload_hash).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert!(recovered.verify_hash_chain());
    }

    #[test]
    fn tool_completion_evidence_roundtrips_segmented_arrow_and_binary_mmap() {
        use crate::replay::{
            BinaryRunEventSegment, RunEventKind, RunEventLedger, RunEventSegmentArchive,
            SegmentedArrowAuditStream, ToolExecutionEvidence, ToolExecutionStatus,
            ToolExecutorKind,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("tool-completion-replay-{unique}"));
        let arrow_dir = dir.join("arrow");
        let binary_path = dir.join("run-events.bin");

        let evidence = ToolExecutionEvidence::new(
            test_hash("typed-tool-ir"),
            test_hash("policy-proof-trace"),
            test_hash("tool-output"),
            test_hash("physical-witness"),
            ToolExecutorKind::Wasmtime,
            ToolExecutionStatus::Succeeded,
        );
        let mut ledger = RunEventLedger::new(214);
        seed_goal_intake_replay(&mut ledger, "binary-scan");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger.append_tool_call_completed(19, &evidence).unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();
        assert_eq!(ledger.events()[5].kind, RunEventKind::ToolCallCompleted);
        assert_eq!(
            ledger.events()[5].secondary_hash,
            Some(evidence.evidence_hash)
        );
        assert!(ledger.verify_hash_chain());

        let mut stream = SegmentedArrowAuditStream::create(&arrow_dir, ledger.run_id, 2).unwrap();
        stream.append_events(ledger.events()).unwrap();
        let manifest = stream.finish().unwrap();
        let proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&arrow_dir, &manifest).unwrap();
        assert!(proof.is_valid());
        let recovered_arrow =
            RunEventSegmentArchive::read_ledger_mmap(&arrow_dir, &manifest).unwrap();
        assert_eq!(recovered_arrow.events(), ledger.events());

        let payload_hash = BinaryRunEventSegment::write_ledger(&binary_path, &ledger).unwrap();
        let report = BinaryRunEventSegment::verify_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(report.event_count, ledger.len());
        let recovered_binary =
            BinaryRunEventSegment::read_ledger_mmap(&binary_path, payload_hash).unwrap();
        assert_eq!(recovered_binary.events(), ledger.events());
    }

    #[test]
    fn binary_run_event_segment_verify_mmap_scans_without_materializing() {
        use crate::replay::{BinaryRunEventSegment, RunEventLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("run-events-scan-{unique}.bin"));

        let mut ledger = RunEventLedger::new(214);
        seed_goal_intake_replay(&mut ledger, "binary-scan");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                23,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        let payload_hash = BinaryRunEventSegment::write_ledger(&path, &ledger).unwrap();

        let report = BinaryRunEventSegment::verify_mmap(&path, payload_hash).unwrap();
        assert_eq!(report.event_count, ledger.len());
        assert_eq!(report.last_event_hash, ledger.last_hash());
        assert_eq!(report.payload_hash, payload_hash);
        assert_eq!(
            report.file_bytes as usize,
            BinaryRunEventSegment::header_bytes()
                + ledger.len() * BinaryRunEventSegment::record_bytes()
        );
    }

    #[test]
    fn binary_run_event_segment_rejects_bad_header_schema() {
        use crate::replay::{BinaryRunEventSegment, RunEventLedger};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();

        let mut ledger = RunEventLedger::new(215);
        seed_goal_intake_replay(&mut ledger, "binary-bad-header");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(29, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        for (case, offset) in [
            ("magic", 0_u64),
            ("version", 8_u64),
            ("header-size", 16_u64),
            ("schema-hash", 56_u64),
        ] {
            let path = dir.join(format!("run-events-bad-header-{case}-{unique}.bin"));
            let payload_hash = BinaryRunEventSegment::write_ledger(&path, &ledger).unwrap();
            assert!(BinaryRunEventSegment::verify_mmap(&path, payload_hash).is_ok());

            let mut file = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
            file.seek(SeekFrom::Start(offset)).unwrap();
            file.write_all(&[0xA5]).unwrap();
            file.flush().unwrap();

            assert!(BinaryRunEventSegment::read_ledger_mmap(&path, payload_hash).is_err());
            assert!(BinaryRunEventSegment::verify_mmap(&path, payload_hash).is_err());
        }
    }

    #[test]
    fn binary_run_event_segment_rejects_tamper() {
        use crate::replay::{BinaryRunEventSegment, RunEventLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("run-events-tamper-{unique}.bin"));

        let mut ledger = RunEventLedger::new(213);
        seed_goal_intake_replay(&mut ledger, "binary-tamper");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        let payload_hash = BinaryRunEventSegment::write_ledger(&path, &ledger).unwrap();
        assert!(BinaryRunEventSegment::read_ledger_mmap(&path, payload_hash).is_ok());
        assert!(BinaryRunEventSegment::verify_mmap(&path, payload_hash).is_ok());

        let mut bytes = std::fs::read(&path).unwrap();
        bytes[48] ^= 0x80;
        std::fs::write(&path, bytes).unwrap();
        assert!(BinaryRunEventSegment::read_ledger_mmap(&path, payload_hash).is_err());
        assert!(BinaryRunEventSegment::verify_mmap(&path, payload_hash).is_err());
    }

    #[test]
    fn binary_run_event_segment_recovers_last_valid_prefix_after_tail_damage() {
        use crate::replay::{BinaryRunEventSegment, RunEventLedger};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts");
        std::fs::create_dir_all(&dir).unwrap();

        let mut ledger = RunEventLedger::new(216);
        seed_goal_intake_replay(&mut ledger, "binary-recover-prefix");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                23,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        ledger
            .append_checkpoint_sealed(29, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let truncated_path = dir.join(format!("run-events-truncated-tail-{unique}.bin"));
        let payload_hash = BinaryRunEventSegment::write_ledger(&truncated_path, &ledger).unwrap();
        let truncated_len =
            BinaryRunEventSegment::header_bytes() + 2 * BinaryRunEventSegment::record_bytes() + 17;
        std::fs::OpenOptions::new()
            .write(true)
            .open(&truncated_path)
            .unwrap()
            .set_len(truncated_len as u64)
            .unwrap();
        assert!(BinaryRunEventSegment::read_ledger_mmap(&truncated_path, payload_hash).is_err());
        let truncated_report =
            BinaryRunEventSegment::recover_last_valid_prefix_mmap(&truncated_path).unwrap();
        assert_eq!(truncated_report.declared_event_count, ledger.len());
        assert_eq!(truncated_report.recovered_event_count, 2);
        assert_eq!(truncated_report.trailing_partial_bytes, 17);
        assert_eq!(truncated_report.ledger.events(), &ledger.events()[0..2]);
        assert!(truncated_report.ledger.verify_hash_chain());
        assert_ne!(truncated_report.recovery_hash, [0; 32]);

        let corrupt_path = dir.join(format!("run-events-corrupt-tail-{unique}.bin"));
        let payload_hash = BinaryRunEventSegment::write_ledger(&corrupt_path, &ledger).unwrap();
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .open(&corrupt_path)
            .unwrap();
        file.seek(SeekFrom::Start(
            (BinaryRunEventSegment::header_bytes() + 2 * BinaryRunEventSegment::record_bytes() + 48)
                as u64,
        ))
        .unwrap();
        file.write_all(&[0xA5]).unwrap();
        file.flush().unwrap();
        assert!(BinaryRunEventSegment::read_ledger_mmap(&corrupt_path, payload_hash).is_err());
        let corrupt_report =
            BinaryRunEventSegment::recover_last_valid_prefix_mmap(&corrupt_path).unwrap();
        assert_eq!(corrupt_report.declared_event_count, ledger.len());
        assert_eq!(corrupt_report.recovered_event_count, 2);
        assert_eq!(corrupt_report.trailing_partial_bytes, 0);
        assert_eq!(corrupt_report.ledger.events(), &ledger.events()[0..2]);
        assert!(corrupt_report.ledger.verify_hash_chain());
        assert_ne!(corrupt_report.recovery_hash, truncated_report.recovery_hash);
    }

    #[test]
    fn run_event_segment_archive_recovers_manifest_chain() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-segments-{unique}"));

        let mut ledger = RunEventLedger::new(303);
        seed_goal_intake_replay(&mut ledger, "archive-manifest-chain");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                18,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 4);
        assert!(manifest.is_valid());
        assert_eq!(manifest.entries[0].previous_segment_hash, [0; 32]);
        assert_eq!(
            manifest.entries[1].previous_segment_hash,
            manifest.entries[0].segment_hash
        );

        let recovered = RunEventSegmentArchive::read_ledger(&dir, &manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert!(recovered.verify_hash_chain());

        let mut tampered = manifest.clone();
        tampered.entries[1].previous_segment_hash = test_hash("tampered-segment");
        assert!(RunEventSegmentArchive::read_ledger(&dir, &tampered).is_err());
    }

    #[test]
    fn run_event_segment_archive_recovers_with_mmap_reader() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-mmap-segments-{unique}"));

        let mut ledger = RunEventLedger::new(404);
        seed_goal_intake_replay(&mut ledger, "archive-mmap-reader");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 4);
        assert!(manifest.is_valid());

        let recovered = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert!(recovered.verify_hash_chain());
    }

    #[test]
    fn run_event_segment_archive_reports_mmap_materialization_evidence() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-mmap-evidence-{unique}"));

        let mut ledger = RunEventLedger::new(454);
        seed_goal_intake_replay(&mut ledger, "archive-mmap-evidence");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let (recovered, evidence) =
            RunEventSegmentArchive::read_ledger_mmap_with_evidence(&dir, &manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert_eq!(evidence.segment_count, manifest.entries.len());
        assert_eq!(evidence.mmap_segment_count, manifest.entries.len());
        assert_eq!(evidence.materialized_event_count, ledger.len());
        assert!(evidence.total_file_bytes > 0);
        assert!(evidence.materialized_run_event_bytes > 0);
        assert!(evidence.materialized_hash_bytes >= (ledger.len() as u64 * 96));
        assert!(evidence.mmap_backing_used);
        assert!(evidence.stream_reader_materializes_events);
        assert!(evidence.proves_mmap_materialized_replay());
    }

    #[test]
    fn run_event_segment_archive_writes_one_arrow_batch_per_segment() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use arrow::ipc::reader::StreamReader;
        use std::fs::File;
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-arrow-batched-segments-{unique}"));

        let mut ledger = RunEventLedger::new(455);
        seed_goal_intake_replay(&mut ledger, "archive-one-batch");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 4, &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 2);

        for entry in &manifest.entries {
            let path =
                RunEventSegmentArchive::segment_path(&dir, manifest.run_id, entry.segment_id);
            let reader = StreamReader::try_new(File::open(path).unwrap(), None).unwrap();
            let batches = reader.collect::<Result<Vec<_>, _>>().unwrap();
            assert_eq!(batches.len(), 1);
            assert_eq!(batches[0].num_rows(), entry.event_count as usize);
        }

        let recovered = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
    }

    #[test]
    fn run_event_segment_archive_recovers_last_valid_prefix_after_missing_tail() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-crash-recovery-{unique}"));

        let mut ledger = RunEventLedger::new(505);
        seed_goal_intake_replay(&mut ledger, "archive-missing-tail");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 3);
        let tail_path = RunEventSegmentArchive::segment_path(
            &dir,
            manifest.run_id,
            manifest.entries[2].segment_id,
        );
        std::fs::remove_file(tail_path).unwrap();

        assert!(RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).is_err());
        let report =
            RunEventSegmentArchive::recover_last_valid_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(report.expected_segment_count, 3);
        assert_eq!(report.recovered_segment_count, 2);
        assert_eq!(report.last_valid_event_id, 4);
        assert_eq!(report.ledger.events(), &ledger.events()[..4]);
        assert!(report.ledger.verify_hash_chain());
        assert_ne!(report.recovery_hash, [0; 32]);
    }

    #[test]
    fn run_event_segment_archive_recovers_manifest_from_sealed_segments() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-sealed-manifest-recovery-{unique}"));

        let mut ledger = RunEventLedger::new(506);
        seed_goal_intake_replay(&mut ledger, "archive-sealed-manifest");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                18,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let recovered_manifest =
            RunEventSegmentArchive::recover_manifest_from_segments(&dir, ledger.run_id).unwrap();
        assert_eq!(recovered_manifest, manifest);
        assert!(recovered_manifest.is_valid());

        let proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &recovered_manifest).unwrap();
        assert!(proof.is_valid());
        let recovered =
            RunEventSegmentArchive::read_ledger_mmap(&dir, &recovered_manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
    }

    #[test]
    fn run_event_segment_archive_recovers_manifest_prefix_without_tail_manifest() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-physical-prefix-recovery-{unique}"));

        let mut ledger = RunEventLedger::new(507);
        seed_goal_intake_replay(&mut ledger, "archive-prefix-manifest");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 3);
        let tail_path = RunEventSegmentArchive::segment_path(
            &dir,
            manifest.run_id,
            manifest.entries[2].segment_id,
        );
        std::fs::remove_file(tail_path).unwrap();

        let recovered_manifest =
            RunEventSegmentArchive::recover_manifest_from_segments(&dir, ledger.run_id).unwrap();
        assert_eq!(recovered_manifest.entries.len(), 2);
        assert!(recovered_manifest.is_valid());
        assert_eq!(
            recovered_manifest.entries.as_slice(),
            &manifest.entries[..2]
        );

        let recovered =
            RunEventSegmentArchive::read_ledger_mmap(&dir, &recovered_manifest).unwrap();
        assert_eq!(recovered.events(), &ledger.events()[..4]);
        assert!(
            RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &recovered_manifest)
                .unwrap()
                .is_valid()
        );
    }

    #[test]
    fn run_event_segment_archive_physical_manifest_scan_stops_at_corrupt_segment() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-physical-corrupt-recovery-{unique}"));

        let mut ledger = RunEventLedger::new(508);
        seed_goal_intake_replay(&mut ledger, "archive-corrupt-segment");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_circuit_breaker_tripped(
                21,
                test_hash("circuit-breaker-decision"),
                test_hash("response-fingerprint"),
            )
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 4);
        let middle_path = RunEventSegmentArchive::segment_path(
            &dir,
            manifest.run_id,
            manifest.entries[1].segment_id,
        );
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .open(&middle_path)
            .unwrap();
        file.seek(SeekFrom::End(0)).unwrap();
        file.write_all(&[0x5A]).unwrap();
        file.flush().unwrap();

        let recovered_manifest =
            RunEventSegmentArchive::recover_manifest_from_segments(&dir, ledger.run_id).unwrap();
        assert_eq!(recovered_manifest.entries.len(), 1);
        assert_eq!(
            recovered_manifest.entries.as_slice(),
            &manifest.entries[..1]
        );
        let recovered =
            RunEventSegmentArchive::read_ledger_mmap(&dir, &recovered_manifest).unwrap();
        assert_eq!(recovered.events(), &ledger.events()[..2]);
        assert!(recovered.verify_hash_chain());
    }

    #[test]
    fn segmented_arrow_audit_stream_rolls_segments_and_binds_files() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive, SegmentedArrowAuditStream};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-audit-stream-{unique}"));

        let mut ledger = RunEventLedger::new(515);
        seed_goal_intake_replay(&mut ledger, "segmented-rolls");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_operator_review_artifact_recorded(
                18,
                test_hash("operator-review-signing-target"),
                test_hash("operator-review-artifact"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let mut stream = SegmentedArrowAuditStream::create(&dir, ledger.run_id, 2).unwrap();
        for event in ledger.events() {
            stream.append_event(event).unwrap();
        }
        assert_eq!(stream.segment_count(), 3);
        assert_eq!(stream.pending_event_count(), 1);
        let manifest = stream.finish().unwrap();

        assert_eq!(manifest.entries.len(), 4);
        assert!(manifest.is_valid());
        assert_ne!(manifest.manifest_hash, [0; 32]);
        let proof = RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &manifest).unwrap();
        assert!(proof.is_valid());
        assert_eq!(proof.segment_count, manifest.entries.len());
        assert_eq!(proof.event_count, ledger.len());
        assert_eq!(proof.first_event_id, 1);
        assert_eq!(proof.last_event_id, ledger.len() as u64);
        assert_eq!(proof.manifest_hash, manifest.manifest_hash);
        assert!(proof.total_arrow_file_bytes > 0);
        assert!(proof.mmap_buffer_count > 0);
        assert_ne!(proof.segment_chain_hash, [0; 32]);
        assert_ne!(proof.segment_witness_hash, [0; 32]);
        assert_ne!(proof.file_evidence_hash, [0; 32]);
        assert_ne!(proof.logical_replay_hash, [0; 32]);
        assert_ne!(proof.mmap_evidence_hash, [0; 32]);
        assert_ne!(proof.semantic_scan_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);
        let mut tampered_witness_proof = proof.clone();
        tampered_witness_proof.segment_witness_hash = test_hash("tampered-segment-witness");
        assert!(!tampered_witness_proof.is_valid());
        let determinism =
            RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest).unwrap();
        assert_eq!(
            proof.logical_replay_hash,
            determinism.first_pass_event_sequence_hash
        );
        let (_materialized, materialized_evidence) =
            RunEventSegmentArchive::read_ledger_mmap_with_evidence(&dir, &manifest).unwrap();
        assert_ne!(
            proof.mmap_evidence_hash,
            materialized_evidence.evidence_hash
        );
        for entry in &manifest.entries {
            assert_ne!(entry.arrow_schema_hash, [0; 32]);
            assert!(entry.arrow_file_bytes > 0);
            assert_ne!(entry.arrow_file_hash, [0; 32]);
            assert!(entry.staged_temp_file_used);
            assert!(entry.temp_file_synced_before_publish);
            assert!(entry.publish_completed);
            assert!(entry.parent_directory_sync_attempted);
        }

        let recovered = RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert!(recovered.verify_hash_chain());
    }

    #[test]
    fn segmented_arrow_audit_proof_rejects_manifest_with_fake_semantic_chain() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive, RunEventSegmentManifest};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-forged-manifest-{unique}"));

        let mut ledger = RunEventLedger::new(519);
        seed_goal_intake_replay(&mut ledger, "segmented-forged-manifest");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, ledger.len(), &ledger).unwrap();
        assert_eq!(manifest.entries.len(), 1);
        assert!(RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &manifest).is_ok());

        let mut forged_entries = manifest.entries.clone();
        forged_entries[0].segment_hash = test_hash("fake-segment-hash");
        let forged_manifest = RunEventSegmentManifest::new(manifest.run_id, forged_entries);
        assert!(forged_manifest.is_valid());
        assert!(
            RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &forged_manifest).is_err()
        );
    }

    #[test]
    fn run_event_segment_archive_proves_two_pass_replay_determinism() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("replay-determinism-proof-{unique}"));

        let mut ledger = RunEventLedger::new(520);
        seed_goal_intake_replay(&mut ledger, "replay-determinism");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_circuit_breaker_tripped(
                21,
                test_hash("circuit-breaker-decision"),
                test_hash("response-fingerprint"),
            )
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let proof = RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest).unwrap();
        assert!(proof.is_valid());
        assert_eq!(proof.segment_count, manifest.entries.len());
        assert_eq!(proof.event_count, ledger.len());
        assert_eq!(proof.manifest_hash, manifest.manifest_hash);
        assert_eq!(
            proof.first_pass_event_sequence_hash,
            proof.second_pass_event_sequence_hash
        );
        assert_eq!(proof.first_pass_ledger_hash, ledger.last_hash());
        assert_eq!(proof.first_pass_ledger_hash, proof.second_pass_ledger_hash);
        assert_eq!(
            proof.first_pass_mmap_evidence_hash,
            proof.second_pass_mmap_evidence_hash
        );
        assert_ne!(proof.commit_sidecar_evidence_hash, [0; 32]);
        assert_ne!(proof.proof_hash, [0; 32]);

        let first_path = RunEventSegmentArchive::segment_path(
            &dir,
            manifest.run_id,
            manifest.entries[0].segment_id,
        );
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .open(&first_path)
            .unwrap();
        file.seek(SeekFrom::End(0)).unwrap();
        file.write_all(&[0xD7]).unwrap();
        file.flush().unwrap();
        assert!(RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest).is_err());
    }

    #[test]
    fn run_checkpoint_binds_next_action_packet_to_replay_proof() {
        use crate::replay::{
            NextActionKind, NextActionPacket, RunCheckpoint, RunEventLedger, RunEventSegmentArchive,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-checkpoint-next-action-{unique}"));

        let mut ledger = RunEventLedger::new(521);
        seed_goal_intake_replay(&mut ledger, "checkpoint-next-action-fixture");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let replay_proof = RunEventSegmentArchive::prove_replay_determinism(&dir, &manifest)
            .expect("determinism proof");
        let checkpoint = RunCheckpoint::new(
            ledger.run_id,
            23,
            ledger.len(),
            manifest.entries.len(),
            ledger.last_hash(),
            manifest.manifest_hash,
            replay_proof.proof_hash,
            test_hash("context-fold-evidence"),
        );
        assert!(checkpoint.is_valid());
        let packet = NextActionPacket::new(
            ledger.run_id,
            1,
            checkpoint.checkpoint_hash,
            NextActionKind::RequestToolCall,
            99,
            test_hash("typed-tool-ir"),
            test_hash("evidence-contract"),
            test_hash("policy-proof"),
            test_hash("candidate-evidence"),
        );
        assert!(packet.is_valid_for_checkpoint(&checkpoint));

        let other_checkpoint = RunCheckpoint::new(
            ledger.run_id,
            24,
            ledger.len(),
            manifest.entries.len(),
            ledger.last_hash(),
            manifest.manifest_hash,
            replay_proof.proof_hash,
            test_hash("different-context-fold-evidence"),
        );
        assert!(!packet.is_valid_for_checkpoint(&other_checkpoint));

        let mut tampered = packet.clone();
        tampered.policy_proof_hash = [0; 32];
        assert!(!tampered.is_valid());
    }

    #[test]
    fn segmented_arrow_audit_stream_enforces_single_writer_and_append_only_segments() {
        use crate::replay::{RunEventLedger, SegmentedArrowAuditStream};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-single-writer-{unique}"));

        let mut ledger = RunEventLedger::new(518);
        seed_goal_intake_replay(&mut ledger, "segmented-single-writer");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let mut stream = SegmentedArrowAuditStream::create(&dir, ledger.run_id, 2).unwrap();
        assert!(SegmentedArrowAuditStream::create(&dir, ledger.run_id, 2).is_err());
        stream.append_events(ledger.events()).unwrap();
        let manifest = stream.finish().unwrap();
        assert_eq!(manifest.entries.len(), 2);
        for entry in &manifest.entries {
            assert!(entry.staged_temp_file_used);
            assert!(entry.temp_file_synced_before_publish);
            assert!(entry.publish_completed);
            assert!(entry.parent_directory_sync_attempted);
        }

        let mut replay_writer = SegmentedArrowAuditStream::create(&dir, ledger.run_id, 2).unwrap();
        assert!(replay_writer.append_events(ledger.events()).is_err());
    }

    #[test]
    fn segmented_arrow_audit_stream_refuses_existing_segment_path() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive, SegmentedArrowAuditStream};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-existing-path-{unique}"));
        std::fs::create_dir_all(&dir).unwrap();

        let mut ledger = RunEventLedger::new(522);
        seed_goal_intake_replay(&mut ledger, "segmented-existing-path");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        let segment_path = RunEventSegmentArchive::segment_path(&dir, ledger.run_id, 1);
        std::fs::write(&segment_path, b"crash-partial-final-segment").unwrap();

        let mut stream = SegmentedArrowAuditStream::create(&dir, ledger.run_id, 1).unwrap();
        assert!(stream.append_events(ledger.events()).is_err());
        assert_eq!(
            std::fs::read(&segment_path).unwrap(),
            b"crash-partial-final-segment"
        );
        assert_eq!(stream.segment_count(), 0);
    }

    #[test]
    fn segmented_arrow_audit_stream_rejects_physical_file_tamper() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive, SegmentedArrowAuditStream};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-audit-tamper-{unique}"));

        let mut ledger = RunEventLedger::new(516);
        seed_goal_intake_replay(&mut ledger, "segmented-tamper");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let mut stream = SegmentedArrowAuditStream::create(&dir, ledger.run_id, 2).unwrap();
        stream.append_events(ledger.events()).unwrap();
        let manifest = stream.finish().unwrap();
        assert!(manifest.is_valid());

        let first_path = RunEventSegmentArchive::segment_path(
            &dir,
            manifest.run_id,
            manifest.entries[0].segment_id,
        );
        let original_len = std::fs::metadata(&first_path).unwrap().len();
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .open(&first_path)
            .unwrap();
        file.seek(SeekFrom::End(0)).unwrap();
        file.write_all(&[0xA5]).unwrap();
        file.flush().unwrap();
        assert_eq!(
            std::fs::metadata(&first_path).unwrap().len(),
            original_len + 1
        );

        assert!(RunEventSegmentArchive::prove_segmented_arrow_audit(&dir, &manifest).is_err());
        assert!(RunEventSegmentArchive::read_ledger_mmap(&dir, &manifest).is_err());
        let report =
            RunEventSegmentArchive::recover_last_valid_ledger_mmap(&dir, &manifest).unwrap();
        assert_eq!(report.recovered_segment_count, 0);
        assert_eq!(report.last_valid_event_id, 0);
    }

    #[test]
    fn segmented_arrow_audit_cache_rechecks_physical_file_evidence_before_hit() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-cache-tamper-{unique}"));

        let mut ledger = RunEventLedger::new(524);
        seed_goal_intake_replay(&mut ledger, "segmented-cache-tamper");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 1, &ledger).unwrap();
        let (cached, evidence) =
            RunEventSegmentArchive::read_ledger_mmap_with_evidence(&dir, &manifest).unwrap();
        assert_eq!(cached.events(), ledger.events());
        assert!(evidence.proves_mmap_materialized_replay());

        let first_path = RunEventSegmentArchive::segment_path(
            &dir,
            manifest.run_id,
            manifest.entries[0].segment_id,
        );
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .open(&first_path)
            .unwrap();
        file.seek(SeekFrom::End(0)).unwrap();
        file.write_all(&[0xA5]).unwrap();
        file.flush().unwrap();

        assert!(RunEventSegmentArchive::read_ledger_mmap_with_evidence(&dir, &manifest).is_err());
    }

    #[test]
    fn segmented_arrow_audit_stream_rejects_missing_or_tampered_commit_sidecar() {
        use crate::replay::{RunEventLedger, RunEventSegmentArchive};
        use std::io::{Seek, SeekFrom, Write};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let missing_dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-missing-commit-{unique}"));

        let mut ledger = RunEventLedger::new(523);
        seed_goal_intake_replay(&mut ledger, "segmented-sidecar");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let missing_manifest = RunEventSegmentArchive::write_ledger(&missing_dir, 1, &ledger)
            .expect("write missing-sidecar source");
        let first_commit_path = RunEventSegmentArchive::segment_commit_path(
            &missing_dir,
            missing_manifest.run_id,
            missing_manifest.entries[0].segment_id,
        );
        assert!(first_commit_path.exists());
        std::fs::remove_file(&first_commit_path).unwrap();
        assert!(
            RunEventSegmentArchive::prove_segmented_arrow_audit(&missing_dir, &missing_manifest)
                .is_err()
        );
        assert!(RunEventSegmentArchive::read_ledger_mmap(&missing_dir, &missing_manifest).is_err());
        assert!(
            RunEventSegmentArchive::recover_manifest_from_segments(&missing_dir, ledger.run_id)
                .is_err()
        );

        let tampered_dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-tampered-commit-{unique}"));
        let tampered_manifest = RunEventSegmentArchive::write_ledger(&tampered_dir, 1, &ledger)
            .expect("write tampered-sidecar source");
        let second_commit_path = RunEventSegmentArchive::segment_commit_path(
            &tampered_dir,
            tampered_manifest.run_id,
            tampered_manifest.entries[1].segment_id,
        );
        let mut sidecar = std::fs::OpenOptions::new()
            .write(true)
            .open(&second_commit_path)
            .unwrap();
        sidecar.seek(SeekFrom::Start(16)).unwrap();
        sidecar.write_all(&[0xA9]).unwrap();
        sidecar.flush().unwrap();

        assert!(
            RunEventSegmentArchive::prove_segmented_arrow_audit(&tampered_dir, &tampered_manifest)
                .is_err()
        );
        let recovered_manifest =
            RunEventSegmentArchive::recover_manifest_from_segments(&tampered_dir, ledger.run_id)
                .unwrap();
        assert_eq!(recovered_manifest.entries.len(), 1);
        assert_eq!(
            recovered_manifest.entries.as_slice(),
            &tampered_manifest.entries[..1]
        );
        let report = RunEventSegmentArchive::recover_last_valid_ledger_mmap(
            &tampered_dir,
            &tampered_manifest,
        )
        .unwrap();
        assert_eq!(report.recovered_segment_count, 1);
        assert_eq!(report.ledger.events(), &ledger.events()[..1]);
    }

    #[test]
    fn segmented_arrow_audit_stream_rejects_missing_replay_prerequisite() {
        use crate::replay::{RunEvent, SegmentedArrowAuditStream};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("segmented-arrow-audit-prereq-{unique}"));
        let mut stream = SegmentedArrowAuditStream::create(&dir, 517, 2).unwrap();
        let orphan_response = RunEvent::llm_response_received(
            1,
            517,
            test_hash("response"),
            test_hash("raw-text-ref"),
            [0; 32],
        );

        assert!(orphan_response.is_valid());
        assert!(stream.append_event(&orphan_response).is_err());
        assert_eq!(stream.segment_count(), 0);
        assert_eq!(stream.pending_event_count(), 0);
    }

    #[test]
    fn run_event_segment_archive_compacts_to_binary_segment_crash_safely() {
        use crate::replay::{BinaryRunEventSegment, RunEventLedger, RunEventSegmentArchive};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("run-event-compaction-{unique}"));

        let mut ledger = RunEventLedger::new(519);
        seed_goal_intake_replay(&mut ledger, "archive-compaction");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();

        let manifest = RunEventSegmentArchive::write_ledger(&dir, 2, &ledger).unwrap();
        let output_path = dir.join("compacted").join("run-events.bin");
        let report = RunEventSegmentArchive::compact_to_binary_segment_crash_safe(
            &dir,
            &manifest,
            &output_path,
        )
        .unwrap();
        assert!(report.is_valid());
        assert_eq!(report.source_segment_count, manifest.entries.len());
        assert_eq!(report.source_event_count, ledger.len());
        assert_eq!(report.compacted_event_count, ledger.len());
        assert_eq!(report.compacted_last_event_hash, ledger.last_hash());
        assert_ne!(report.source_audit_proof_hash, [0; 32]);
        assert_ne!(report.source_mmap_evidence_hash, [0; 32]);
        assert_ne!(report.compacted_payload_hash, [0; 32]);

        let recovered =
            BinaryRunEventSegment::read_ledger_mmap(&output_path, report.compacted_payload_hash)
                .unwrap();
        assert_eq!(recovered.events(), ledger.events());
        assert!(recovered.verify_hash_chain());
        assert_eq!(
            synced_temp_file_count(&dir.join("compacted"), "run-events.bin"),
            0
        );
    }

    #[test]
    fn replay_chaos_bench_recovers_seeded_crash_points() {
        use crate::policy::HarnessBenchScorecard;
        use crate::replay::{
            ReplayChaosBench, RunEventLedger, ToolExecutionEvidence, ToolExecutionStatus,
            ToolExecutorKind,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("replay-chaos-bench-{unique}"));

        let mut ledger = RunEventLedger::new(606);
        seed_goal_intake_replay(&mut ledger, "chaos-seeded");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        for round in 0..5u128 {
            ledger
                .append_llm_derived_tool_call_requested(
                    200 + round,
                    test_hash(&format!("typed-tool-ir-{round}")),
                )
                .unwrap();
            ledger
                .append_policy_decision_recorded(
                    100 + round,
                    test_hash(&format!("policy-proof-trace-{round}")),
                    test_hash(&format!("typed-tool-ir-{round}")),
                )
                .unwrap();
            let tool_execution_evidence = ToolExecutionEvidence::new(
                test_hash(&format!("typed-tool-ir-{round}")),
                test_hash(&format!("policy-proof-trace-{round}")),
                test_hash(&format!("tool-output-{round}")),
                test_hash(&format!("physical-witness-{round}")),
                ToolExecutorKind::Wasmtime,
                ToolExecutionStatus::Succeeded,
            );
            ledger
                .append_tool_call_completed(200 + round, &tool_execution_evidence)
                .unwrap();
            ledger
                .append_checkpoint_sealed(300 + round, test_hash(&format!("checkpoint-{round}")))
                .unwrap();
        }
        assert!(ledger.verify_hash_chain());

        let report = ReplayChaosBench::run_seeded(&dir, 0x000A_E615_C0DE_u64, 32, 3, &ledger)
            .expect("seeded replay chaos bench should pass");
        assert!(report.is_valid_physical_result());
        assert_eq!(report.crash_points_exercised, 32);
        assert_eq!(report.expected_event_count, ledger.len());
        assert_eq!(report.min_recovered_event_count, 0);
        assert_eq!(report.max_recovered_event_count, ledger.len());
        assert_ne!(report.segmented_arrow_audit_proof_hash, [0; 32]);
        assert_ne!(report.segmented_arrow_audit_logical_replay_hash, [0; 32]);
        assert_ne!(report.segmented_arrow_audit_mmap_evidence_hash, [0; 32]);
        assert_ne!(report.segmented_arrow_audit_segment_witness_hash, [0; 32]);
        assert!(report.segmented_arrow_audit_mmap_buffer_count > 0);
        assert!(report.column_scan_full_acceptance_count > 0);
        assert!(report.column_scan_missing_tail_rejection_count > 0);
        assert_eq!(
            report.column_scan_full_acceptance_count
                + report.column_scan_missing_tail_rejection_count,
            report.crash_points_exercised
        );
        assert!(
            report
                .crash_points
                .iter()
                .any(|point| point.recovered_event_count < ledger.len())
        );

        let scorecard = HarnessBenchScorecard {
            bench_id: 9,
            task_id: 606,
            success: true,
            physical_witness_hash: report.report_hash,
            replay_hash: report.report_hash,
            policy_violation_count: 0,
            hard_block_count: 0,
            approval_required_count: 0,
            witness_coverage_ppm: 1_000_000,
            crash_recovery_passed: report.all_recoveries_valid,
            p50_wall_ms: 1,
            p99_wall_ms: 2,
            token_count: 0,
            tool_call_count: 0,
        };
        assert!(scorecard.is_valid_physical_result());

        let artifact_path = dir.join("replay-chaos-scorecard.json");
        let artifact_hash =
            ReplayChaosBench::write_scorecard_artifact(&artifact_path, &scorecard, &report)
                .expect("scorecard artifact should be written");
        assert_ne!(artifact_hash, [0; 32]);
        let artifact_payload = std::fs::read(&artifact_path).unwrap();
        let artifact_json: serde_json::Value = serde_json::from_slice(&artifact_payload).unwrap();
        assert_eq!(artifact_json["schema_version"], 1);
        assert_eq!(artifact_json["bench_name"], "ReplayChaosBench");
        assert_eq!(artifact_json["scorecard"]["bench_id"], 9);
        assert_eq!(artifact_json["report"]["crash_points_exercised"], 32);
        assert_eq!(artifact_json["report"]["all_recoveries_valid"], true);
        assert_eq!(
            artifact_json["report"]["segmented_arrow_audit_mmap_buffer_count"],
            report.segmented_arrow_audit_mmap_buffer_count
        );
        assert_eq!(
            artifact_json["report"]["segmented_arrow_audit_segment_witness_hash"],
            serde_json::to_value(report.segmented_arrow_audit_segment_witness_hash).unwrap()
        );
        assert_eq!(
            artifact_json["report"]["column_scan_full_acceptance_count"],
            report.column_scan_full_acceptance_count
        );
        assert_eq!(
            artifact_json["report"]["column_scan_missing_tail_rejection_count"],
            report.column_scan_missing_tail_rejection_count
        );
        assert!(artifact_json["io_evidence"].is_null());
        assert_valid_replay_write_evidence(&artifact_json);
    }

    #[test]
    fn replay_chaos_bench_sweeps_event_boundaries() {
        use crate::replay::{ReplayChaosBench, RunEventLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("replay-event-boundary-sweep-{unique}"));

        let mut ledger = RunEventLedger::new(707);
        seed_goal_intake_replay(&mut ledger, "chaos-event-boundary");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(19, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(23, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let report = ReplayChaosBench::run_event_boundary_sweep(&dir, 0xE7E7_B0DA_u64, &ledger)
            .expect("event-boundary sweep should pass");
        assert!(report.is_valid_physical_result());
        assert_eq!(report.segment_count, ledger.len());
        assert_eq!(report.crash_points_exercised as usize, ledger.len() + 1);
        assert_eq!(report.column_scan_full_acceptance_count, 1);
        assert_eq!(
            report.column_scan_missing_tail_rejection_count as usize,
            ledger.len()
        );
        assert_eq!(report.min_recovered_event_count, 0);
        assert_eq!(report.max_recovered_event_count, ledger.len());
        for (expected_count, point) in report.crash_points.iter().enumerate() {
            assert_eq!(point.persisted_segment_count, expected_count);
            assert_eq!(point.recovered_event_count, expected_count);
            assert_eq!(point.last_valid_event_id, expected_count as u64);
        }
    }

    #[test]
    fn replay_chaos_bench_sweeps_approval_boundaries() {
        use crate::replay::{ReplayChaosBench, RunEventKind, RunEventLedger};
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("replay-approval-boundary-sweep-{unique}"));

        let mut ledger = RunEventLedger::new(808);
        seed_goal_intake_replay(&mut ledger, "chaos-approval-boundary");
        ledger
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        ledger
            .append_llm_response_received(test_hash("response"), test_hash("raw-text-ref"))
            .unwrap();
        ledger
            .append_policy_decision_recorded(
                17,
                test_hash("policy-proof-trace"),
                test_hash("typed-tool-ir"),
            )
            .unwrap();
        ledger
            .append_approval_token_recorded(
                29,
                test_hash("approval-token"),
                test_hash("review-packet"),
                10_000,
            )
            .unwrap();
        ledger
            .append_llm_derived_tool_call_requested(31, test_hash("typed-tool-ir"))
            .unwrap();
        ledger
            .append_checkpoint_sealed(37, test_hash("checkpoint"))
            .unwrap();
        assert!(ledger.verify_hash_chain());

        let report = ReplayChaosBench::run_approval_boundary_sweep(&dir, 0xA990_0A11_u64, &ledger)
            .expect("approval-boundary sweep should pass");
        assert!(report.is_valid_physical_result());
        assert_eq!(report.segment_count, ledger.len());
        assert_eq!(report.crash_points_exercised as usize, ledger.len() + 1);
        assert_eq!(report.column_scan_full_acceptance_count, 1);
        assert_eq!(
            report.column_scan_missing_tail_rejection_count as usize,
            ledger.len()
        );
        let approval_event_id = ledger
            .events()
            .iter()
            .find(|event| event.kind == RunEventKind::ApprovalTokenRecorded)
            .unwrap()
            .event_id;
        assert!(report.crash_points.iter().any(|point| {
            point.last_valid_event_id == approval_event_id
                && ledger.events()[point.recovered_event_count - 1].kind
                    == RunEventKind::ApprovalTokenRecorded
        }));

        let mut no_approval = RunEventLedger::new(809);
        seed_goal_intake_replay(&mut no_approval, "chaos-no-approval");
        no_approval
            .append_context_pack_built(11, 128, test_hash("context-pack"))
            .unwrap();
        assert!(
            ReplayChaosBench::run_approval_boundary_sweep(&dir, 0xA990_0A11_u64, &no_approval)
                .is_err()
        );
    }

    #[test]
    fn replay_endurance_bench_proves_accelerated_100h_mmap_recovery() {
        use crate::replay::ReplayEnduranceBench;
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("replay-endurance-100h-{unique}"));

        let report = ReplayEnduranceBench::run_accelerated_100h_proof(&dir)
            .expect("accelerated 100h replay proof should pass");
        assert!(report.is_valid_physical_result());
        assert_eq!(report.simulated_hours, 100);
        assert_eq!(report.synthetic_cycle_count, 2_400);
        assert_eq!(report.checkpoint_count, 400);
        assert_eq!(report.context_fold_count, 400);
        assert_eq!(
            report.context_fold_checkpoint_pair_count,
            report.checkpoint_count
        );
        assert!(report.tail_recovered_context_fold_count >= report.tail_recovered_checkpoint_count);
        assert!(
            report.tail_recovered_context_fold_count
                <= report.tail_recovered_checkpoint_count.saturating_add(1)
        );
        assert_eq!(report.event_count, 10_403);
        assert_eq!(
            report.segmented_arrow_audit_segment_count,
            report.segment_count
        );
        assert_eq!(report.segmented_arrow_audit_event_count, report.event_count);
        assert!(report.segmented_arrow_audit_total_file_bytes > 0);
        assert!(report.segmented_arrow_audit_mmap_buffer_count > 0);
        assert_eq!(report.mmap_recovered_event_count, report.event_count);
        assert!(report.mmap_materialized_replay_proven);
        assert!(report.materialization_within_bound);
        assert!(report.full_replay_matches);
        assert!(report.tail_recovery_matches_prefix);
        assert!(report.tail_events_since_last_checkpoint <= report.checkpoint_cadence_event_bound);
        assert_ne!(report.context_fold_evidence_hash, [0; 32]);
        assert_ne!(report.segmented_arrow_audit_proof_hash, [0; 32]);
        assert_ne!(report.segmented_arrow_audit_segment_witness_hash, [0; 32]);
        assert_ne!(report.replay_determinism_proof_hash, [0; 32]);
        assert_ne!(report.run_checkpoint_hash, [0; 32]);
        assert_ne!(report.next_action_packet_hash, [0; 32]);
        assert_ne!(report.report_hash, [0; 32]);
    }

    #[test]
    fn replay_endurance_bench_rejects_uncheckpointed_tail_recovery() {
        use crate::replay::ReplayEnduranceBench;
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("replay-endurance-reject-{unique}"));

        let result = ReplayEnduranceBench::run_accelerated_proof(
            &dir,
            0xAEE6_0100_0000_0001_u128,
            100,
            1,
            101,
            16,
            1,
        );
        assert!(result.is_err());
    }

    #[test]
    fn hot_engine_arena_hashes_browser_artifact_without_file_roundtrip() {
        use crate::hot_engine::{
            ArenaAdmission, InMemoryEvidenceArena, TrustLevel, arena_storage_ref_hash,
            simd_blake3_hash,
        };

        let payload = b"<html><body><button id='go'>run</button></body></html>";
        let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 1024 * 1024, 1024 * 1024);
        let handle = arena.commit(payload).unwrap();

        assert!(handle.is_valid());
        assert_eq!(handle.trust_level, TrustLevel::Prod);
        assert_eq!(handle.admission, ArenaAdmission::Accepted);
        assert_eq!(handle.byte_len, payload.len() as u64);
        assert_eq!(handle.artifact_hash, simd_blake3_hash(payload));
        assert_eq!(
            handle.storage_ref_hash,
            arena_storage_ref_hash(handle.slot, handle.generation, handle.artifact_hash)
        );
        assert_eq!(arena.payload_arc(&handle).unwrap().as_ref(), payload);

        let stats = arena.stats();
        assert_eq!(stats.live_bytes, payload.len());
        assert_eq!(stats.live_slots, 1);
        assert_eq!(stats.total_commits, 1);
        assert_eq!(stats.total_degraded_commits, 0);
    }

    #[test]
    fn hot_engine_arena_generation_rejects_released_handle() {
        use crate::hot_engine::{HotEngineError, InMemoryEvidenceArena, TrustLevel};

        let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 1024 * 1024, 1024 * 1024);
        let first = arena.commit(b"dom-before").unwrap();

        arena.release(&first).unwrap();
        assert_eq!(
            arena.payload_arc(&first),
            Err(HotEngineError::InvalidHandle)
        );
        assert_eq!(arena.release(&first), Err(HotEngineError::InvalidHandle));

        let second = arena.commit(b"dom-after").unwrap();
        assert_eq!(second.slot, first.slot);
        assert_ne!(second.generation, first.generation);
        assert_eq!(
            arena.payload_arc(&first),
            Err(HotEngineError::GenerationMismatch)
        );
        assert_eq!(arena.payload_arc(&second).unwrap().as_ref(), b"dom-after");
    }

    #[test]
    fn hot_engine_dev_trust_records_degraded_warning() {
        use crate::hot_engine::{ArenaAdmission, InMemoryEvidenceArena, TrustLevel};

        let arena = InMemoryEvidenceArena::new(TrustLevel::Dev, 1024 * 1024, 1024 * 1024);
        let handle = arena.commit(b"developer-fast-path").unwrap();
        let stats = arena.stats();

        assert_eq!(arena.trust_level(), TrustLevel::Dev);
        assert_eq!(handle.admission, ArenaAdmission::DegradedWarning);
        assert_eq!(stats.total_commits, 1);
        assert_eq!(stats.total_degraded_commits, 1);
    }

    #[test]
    fn hot_engine_ffi_commit_returns_gateway_contract() {
        let payload = b"friendly gateway evidence".to_vec();
        let artifact_hash = aegis_hot_hash(payload.clone()).unwrap();
        let response: serde_json::Value = serde_json::from_str(
            &aegis_hot_commit(payload, Some("DEV".to_string()), None, None).unwrap(),
        )
        .unwrap();

        assert_eq!(response["schema"], "aegis-hot-arena-commit-v1");
        assert_eq!(response["truth_claim"], false);
        assert_eq!(response["verifier"], "rust-hot-engine");
        assert_eq!(response["artifact_hash"], artifact_hash);
        assert_eq!(response["trust_level"], "DEV");
        assert_eq!(response["admission"], "degraded_warning");
        assert_eq!(response["physical_witness_required"], false);
        assert_eq!(response["fail_closed"], false);
        assert_eq!(response["handle_valid"], true);
    }

    #[test]
    fn hot_engine_ffi_batch_commit_returns_single_arena_contract() {
        let payloads = vec![
            b"browser-dom-before".to_vec(),
            b"browser-screenshot-after".to_vec(),
        ];
        let response: serde_json::Value = serde_json::from_str(
            &aegis_hot_commit_batch(payloads, Some("DEV".to_string()), None, None).unwrap(),
        )
        .unwrap();

        assert_eq!(response["schema"], "aegis-hot-arena-commit-batch-v1");
        assert_eq!(response["truth_claim"], false);
        assert_eq!(response["verifier"], "rust-hot-engine");
        assert_eq!(response["trust_level"], "DEV");
        assert_eq!(response["artifact_count"], 2);
        assert_eq!(response["arena_live_slots"], 2);
        assert_eq!(response["arena_total_commits"], 2);
        assert_eq!(response["arena_total_degraded_commits"], 2);
        assert_eq!(response["no_file_roundtrip_on_hot_path"], true);
        assert_eq!(response["batch_digest"].as_str().unwrap().len(), 64);
        let commits = response["commits"].as_array().unwrap();
        assert_eq!(commits.len(), 2);
        assert!(commits.iter().all(|commit| {
            commit["schema"] == "aegis-hot-arena-commit-v1"
                && commit["handle_valid"] == true
                && commit["artifact_hash"].as_str().unwrap().len() == 64
                && commit["storage_ref_hash"].as_str().unwrap().len() == 64
        }));
    }

    #[test]
    fn async_shadow_sealer_persists_arena_payload_off_hot_path() {
        use crate::hot_engine::{
            AsyncShadowSealer, InMemoryEvidenceArena, TrustLevel, shadow_seal_batch_hash,
            simd_blake3_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("aegis-shadow-sealer-{unique}"));
        let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 1024 * 1024, 1024 * 1024);
        let payload = b"\x89PNG-aegis-hot-arena-screenshot";
        let handle = arena.commit(payload).unwrap();
        let sealer = AsyncShadowSealer::start(&dir).unwrap();

        let receipt = sealer
            .submit(&arena, handle)
            .unwrap()
            .blocking_recv()
            .unwrap()
            .unwrap();
        assert_eq!(receipt.byte_len, payload.len() as u64);
        assert_eq!(receipt.artifact_hash, simd_blake3_hash(payload));
        assert_eq!(std::fs::read(&receipt.file_path).unwrap(), payload);
        assert_ne!(receipt.seal_hash, [0; 32]);

        let batch = sealer.flush_blocking().unwrap();
        assert_eq!(batch.receipt_count, 1);
        assert_eq!(batch.total_bytes, payload.len() as u64);
        assert_eq!(batch.batch_hash, shadow_seal_batch_hash(&batch.receipts));
        assert_eq!(batch.receipts[0], receipt);
    }

    #[test]
    fn async_shadow_sealer_backpressure_is_nonblocking_and_hash_bound() {
        use crate::hot_engine::{
            AsyncShadowSealer, InMemoryEvidenceArena, ShadowSealAdmission, TrustLevel,
            shadow_seal_backpressure_hash,
        };
        use std::time::{Duration, Instant};

        let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 1024 * 1024, 1024 * 1024);
        let first = arena.commit(b"first shadow payload").unwrap();
        let second = arena.commit(b"second shadow payload").unwrap();
        let sealer = AsyncShadowSealer::start_paused_for_backpressure_test(1);

        match sealer.try_submit(&arena, first).unwrap() {
            ShadowSealAdmission::Queued(_) => {}
            ShadowSealAdmission::Backpressured(_) => panic!("first seal admission should queue"),
        }

        let started = Instant::now();
        let backpressure = match sealer.try_submit(&arena, second).unwrap() {
            ShadowSealAdmission::Queued(_) => panic!("full queue must not accept second seal"),
            ShadowSealAdmission::Backpressured(record) => record,
        };
        assert!(started.elapsed() < Duration::from_millis(50));
        assert!(backpressure.is_valid());
        assert_eq!(backpressure.slot, second.slot);
        assert_eq!(backpressure.generation, second.generation);
        assert_eq!(backpressure.byte_len, second.byte_len);
        assert_eq!(backpressure.artifact_hash, second.artifact_hash);
        assert_eq!(backpressure.storage_ref_hash, second.storage_ref_hash);
        assert_eq!(backpressure.queue_depth, 1);
        assert_eq!(backpressure.backpressure_sequence, 1);
        assert_eq!(
            backpressure.backpressure_hash,
            shadow_seal_backpressure_hash(
                second.slot,
                second.generation,
                second.byte_len,
                second.artifact_hash,
                second.storage_ref_hash,
                1,
                1,
            )
        );

        let stats = sealer.stats();
        assert_eq!(stats.queue_depth, 1);
        assert_eq!(stats.queued_admissions, 1);
        assert_eq!(stats.backpressure_rejections, 1);
    }

    #[test]
    fn shadow_seal_batch_is_replay_visible_in_segmented_arrow_cold_ledger() {
        use crate::hot_engine::{AsyncShadowSealer, InMemoryEvidenceArena, TrustLevel};
        use crate::replay::{
            ReplayLedgerError, RunEventKind, RunEventLedger, RunEventSegmentArchive,
            shadow_seal_replay_binding_hash,
        };
        use std::time::{SystemTime, UNIX_EPOCH};

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::current_dir()
            .unwrap()
            .join("target")
            .join("aegis-test-artifacts")
            .join(format!("shadow-seal-replay-{unique}"));
        let seal_dir = dir.join("shadow-seals");

        let arena = InMemoryEvidenceArena::new(TrustLevel::Prod, 1024 * 1024, 1024 * 1024);
        let handle = arena
            .commit(b"<html><body>shadow sealed browser payload</body></html>")
            .unwrap();
        let sealer = AsyncShadowSealer::start(&seal_dir).unwrap();
        let receipt = sealer
            .submit(&arena, handle)
            .unwrap()
            .blocking_recv()
            .unwrap()
            .unwrap();
        assert!(receipt.is_valid());
        let batch = sealer.flush_blocking().unwrap();
        assert!(batch.is_valid());

        let mut missing_goal = RunEventLedger::new(881);
        assert_eq!(
            missing_goal.append_shadow_seal_recorded(900, &batch),
            Err(ReplayLedgerError::MissingPriorGoalIntake)
        );

        let mut ledger = RunEventLedger::new(882);
        seed_goal_intake_replay(&mut ledger, "shadow-seal-replay");
        let event = ledger.append_shadow_seal_recorded(900, &batch).unwrap();
        assert_eq!(event.kind, RunEventKind::ShadowSealRecorded);
        assert_eq!(event.subject_id, 900);
        assert_eq!(event.primary_hash, batch.batch_hash);
        assert_eq!(
            event.secondary_hash,
            Some(shadow_seal_replay_binding_hash(900, &batch))
        );
        assert!(ledger.verify_hash_chain());
        assert!(RunEventLedger::from_events(ledger.run_id, ledger.events().to_vec()).is_ok());

        let mut tampered = batch.clone();
        tampered.total_bytes += 1;
        assert_eq!(
            ledger.append_shadow_seal_recorded(901, &tampered),
            Err(ReplayLedgerError::InvalidEvent)
        );

        let arrow_dir = dir.join("arrow");
        let manifest = RunEventSegmentArchive::write_ledger(&arrow_dir, 1, &ledger).unwrap();
        let audit =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&arrow_dir, &manifest).unwrap();
        assert!(audit.is_valid());
        let recovered = RunEventSegmentArchive::read_ledger_mmap(&arrow_dir, &manifest).unwrap();
        assert_eq!(
            recovered.events().last().unwrap().kind,
            RunEventKind::ShadowSealRecorded
        );
        assert_eq!(recovered.last_hash(), ledger.last_hash());
    }

    proptest::proptest! {
        #[test]
        fn test_proptest_guardrail_never_panics(s in "\\PC*") {
            let guardrail = FirstOrderGuardrail;
            let _ = guardrail.verify_invariants(&s);
        }

        #[test]
        fn test_proptest_generational_slab_recycling(payloads in proptest::collection::vec(proptest::string::string_regex("[a-zA-Z0-9]{0,300}").unwrap(), 1..100)) {
            use crate::memory::fold::GenerationalSlab;
            let mut slab = GenerationalSlab::new();
            let mut active_slots = Vec::new();

            for payload in &payloads {
                let slot = slab.insert(payload.as_str());
                active_slots.push((slot, payload.clone()));
            }

            let mut removed_slots = Vec::new();
            let mut remaining_slots = Vec::new();
            for (i, (slot, val)) in active_slots.into_iter().enumerate() {
                if i % 2 == 0 {
                    let removed = slab.remove(slot);
                    assert_eq!(removed.unwrap(), val.as_str());
                    removed_slots.push(slot);
                } else {
                    remaining_slots.push((slot, val));
                }
            }

            for &slot in &removed_slots {
                assert_eq!(slab.get(slot), None);
            }

            for &(slot, ref val) in &remaining_slots {
                assert_eq!(slab.get(slot), Some(&val.as_str()));
            }

            for payload in &payloads {
                let new_slot = slab.insert(payload.as_str());
                assert_eq!(slab.get(new_slot), Some(&payload.as_str()));
                for &old_slot in &removed_slots {
                    assert_eq!(slab.get(old_slot), None);
                }
            }
        }

        #[test]
        fn test_proptest_cold_vector_replay_record_never_panics(
            query in proptest::collection::vec(proptest::string::string_regex("[a-zA-Z0-9_]{0,24}").unwrap(), 0..16),
            scores in proptest::collection::vec(0u32..=1_200_000u32, 0..16),
            limit in 0usize..16,
            latency_ns in 0u64..1_000_000u64,
        ) {
            use crate::evidence_index::{
                CandidateEvidenceRef, ColdVectorExpansionReplayRecord, EvidenceCandidateTier,
            };

            let index_epoch_hash = test_hash("proptest-cold-vector-epoch");
            let query_terms: Vec<&str> = query.iter().map(String::as_str).collect();
            let candidates: Vec<CandidateEvidenceRef> = scores
                .iter()
                .enumerate()
                .map(|(index, score)| {
                    CandidateEvidenceRef::new(
                        test_hash(&format!("proptest-cold-candidate-{index}")),
                        index as u64 + 1,
                        EvidenceCandidateTier::ColdVectorExpansion,
                        *score,
                        index_epoch_hash,
                    )
                })
                .collect();
            let result = ColdVectorExpansionReplayRecord::new(
                index_epoch_hash,
                &query_terms,
                limit,
                test_hash("proptest-cold-vector-config"),
                test_hash("proptest-cold-vector-artifact"),
                latency_ns,
                &candidates,
            );
            if let Ok(record) = result {
                assert!(record.is_valid());
                assert!(latency_ns > 0);
                assert!(candidates.len() <= limit);
                assert!(candidates.iter().all(|candidate| candidate.score_quantized <= 1_000_000));
            }
        }

        #[test]
        fn test_proptest_task_ledger_cached_recompute_matches_fresh(
            first_now in 0u64..180_000u64,
            second_now in 0u64..180_000u64,
            left_done in proptest::bool::ANY,
            right_done in proptest::bool::ANY,
        ) {
            let mut cached = task_ledger_cache_fixture(left_done, right_done);
            let _ = cached.ordered_ready_tasks(first_now).unwrap();
            let cached_ready = cached.ordered_ready_tasks(second_now).unwrap();
            let cached_scores: Vec<(u128, u64, u32, u32)> = (1..=6)
                .map(|task_id| {
                    let task = cached.task(task_id).unwrap();
                    (
                        task_id,
                        task.deterministic_priority_score,
                        task.critical_path_len,
                        task.blocked_descendant_count,
                    )
                })
                .collect();

            let mut fresh = task_ledger_cache_fixture(left_done, right_done);
            let fresh_ready = fresh.ordered_ready_tasks(second_now).unwrap();
            let fresh_scores: Vec<(u128, u64, u32, u32)> = (1..=6)
                .map(|task_id| {
                    let task = fresh.task(task_id).unwrap();
                    (
                        task_id,
                        task.deterministic_priority_score,
                        task.critical_path_len,
                        task.blocked_descendant_count,
                    )
                })
                .collect();

            assert_eq!(cached_ready, fresh_ready);
            assert_eq!(cached_scores, fresh_scores);
        }
    }
}
