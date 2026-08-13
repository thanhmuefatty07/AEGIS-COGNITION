use crate::browser_witness::{
    BrowserActionPlanRecord, BrowserActionTrace, BrowserArtifactFilePathRef,
    BrowserArtifactReadError, BrowserCollectorEvidenceEnvelope, BrowserCollectorKind,
    BrowserLiveCollectorManifest, BrowserObservationPacket,
};
use crate::memory::fold::{CogniFoldStore, MemoryCrystallization};
use crate::memory::frame::SemanticNode;
use crate::physical::{BacktrackSignal, PhysicalArtifact, PhysicalWatchdog, TrapReason};
use crate::policy::{
    CapabilityClass, PolicyDecision, PolicyFacts, PolicyProofTrace, StagingEvidenceKind,
    TypedToolIR,
};
use crate::replay::{
    browser_observation_packet_replay_binding_hash, NextActionKind, NextActionPacket,
    ReplayDeterminismProof, RunCheckpoint, RunEvent, RunEventKind, RunEventLedger,
    RunEventSegmentArchive, RunEventSegmentManifest, SubjectId, ToolExecutionEvidence,
    ToolExecutionStatus, ToolExecutorKind,
};
use crate::sandbox::{SandboxBackendKind, WasmExecutionSandbox};
use crate::task_ledger::TaskSelectionProof;
use blake3::Hasher;
use std::path::Path;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionReceipt {
    pub call_id: SubjectId,
    pub typed_tool_ir_hash: [u8; 32],
    pub policy_proof_trace_hash: [u8; 32],
    pub tool_output_hash: [u8; 32],
    pub physical_evidence_hash: [u8; 32],
    pub artifact_evidence: Option<ToolExecutionArtifactEvidence>,
    pub tool_execution_evidence: ToolExecutionEvidence,
    pub request_event_hash: [u8; 32],
    pub policy_event_hash: [u8; 32],
    pub completion_event_hash: [u8; 32],
    pub browser_observation_event_hash: Option<[u8; 32]>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserGatewayExecutionProof {
    pub packet: BrowserObservationPacket,
    pub policy_facts: PolicyFacts,
    pub policy_trace: PolicyProofTrace,
    pub receipt: ToolExecutionReceipt,
    pub browser_action_plan_hash: Option<[u8; 32]>,
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ToolExecutionArtifactEvidence {
    pub artifact_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub fuel_consumed: u64,
    pub bytes_changed: usize,
    pub evidence_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionReplayProof {
    pub receipt: ToolExecutionReceipt,
    pub replay_determinism_proof: ReplayDeterminismProof,
    pub checkpoint: RunCheckpoint,
    pub next_action_packet: NextActionPacket,
    pub task_selection_event_hash: Option<[u8; 32]>,
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq)]
pub struct ToolMemoryCommitProof {
    pub session_id: SubjectId,
    pub execution_replay_proof_hash: [u8; 32],
    pub artifact_evidence_hash: [u8; 32],
    pub semantic_node: SemanticNode,
    pub memory_len_after_commit: usize,
    pub proof_hash: [u8; 32],
}

impl ToolExecutionReceipt {
    pub fn has_valid_fields(&self) -> bool {
        self.call_id > 0
            && nonzero_hash(&self.typed_tool_ir_hash)
            && nonzero_hash(&self.policy_proof_trace_hash)
            && nonzero_hash(&self.tool_output_hash)
            && nonzero_hash(&self.physical_evidence_hash)
            && self.tool_execution_evidence.tool_output_hash == self.tool_output_hash
            && self.tool_execution_evidence.physical_evidence_hash == self.physical_evidence_hash
            && self.artifact_evidence_matches_status()
            && self
                .tool_execution_evidence
                .binds_policy_and_request(self.typed_tool_ir_hash, self.policy_proof_trace_hash)
            && nonzero_hash(&self.request_event_hash)
            && nonzero_hash(&self.policy_event_hash)
            && nonzero_hash(&self.completion_event_hash)
            && self
                .browser_observation_event_hash
                .is_none_or(|hash| nonzero_hash(&hash))
            && self.browser_observation_event_hash.is_some()
                == matches!(
                    self.tool_execution_evidence.executor_kind,
                    ToolExecutorKind::Browser
                        | ToolExecutorKind::Chrome
                        | ToolExecutorKind::Computer
                )
    }

    pub fn is_valid_for(&self, ir: &TypedToolIR, proof: &PolicyProofTrace) -> bool {
        ir.is_valid()
            && proof.is_replay_consistent_with(ir)
            && self.has_valid_fields()
            && self.call_id > 0
            && self.typed_tool_ir_hash == ir.canonical_hash
            && self.policy_proof_trace_hash == proof.compute_hash()
    }

    fn artifact_evidence_matches_status(&self) -> bool {
        match (
            self.tool_execution_evidence.status,
            self.artifact_evidence.as_ref(),
        ) {
            (ToolExecutionStatus::Succeeded, Some(artifact_evidence)) => {
                artifact_evidence.is_valid()
                    && artifact_evidence.artifact_hash == self.tool_output_hash
                    && artifact_evidence.artifact_hash
                        == self.tool_execution_evidence.tool_output_hash
            }
            (ToolExecutionStatus::Succeeded, None) => matches!(
                self.tool_execution_evidence.executor_kind,
                ToolExecutorKind::Browser | ToolExecutorKind::Chrome | ToolExecutorKind::Computer
            ),
            (ToolExecutionStatus::Failed, None) => true,
            _ => false,
        }
    }
}

impl BrowserGatewayExecutionProof {
    pub fn is_valid_for(
        &self,
        ledger_run_id: SubjectId,
        ir: &TypedToolIR,
        policy_version_hash: [u8; 32],
        expected_policy_window_hash: [u8; 32],
        expected_browser_session_hash: [u8; 32],
        expected_redaction_policy_hash: [u8; 32],
    ) -> bool {
        ledger_run_id > 0
            && ir.is_valid()
            && nonzero_hash(&policy_version_hash)
            && self.packet.run_id == ledger_run_id
            && self.packet.is_valid_staging_packet_for(
                expected_policy_window_hash,
                expected_browser_session_hash,
                expected_redaction_policy_hash,
            )
            && self.policy_facts.is_valid()
            && self.policy_facts.policy_version_hash == policy_version_hash
            && self.policy_facts.staging_proof_ref_hash == Some(self.packet.packet_hash)
            && self.policy_facts.staging_evidence_kind
                == Some(StagingEvidenceKind::IsolatedBrowserSession)
            && self.policy_trace.is_replay_consistent_with(ir)
            && self.policy_trace.decision == PolicyDecision::Allow
            && self.policy_trace.policy_version_hash == self.policy_facts.policy_version_hash
            && self.policy_trace.facts_hash == self.policy_facts.facts_hash
            && self.receipt.is_valid_for(ir, &self.policy_trace)
            && self.receipt.tool_output_hash == self.packet.packet_hash
            && self.receipt.browser_observation_event_hash.is_some()
            && self
                .browser_action_plan_hash
                .is_none_or(|hash| nonzero_hash(&hash))
            && self.proof_hash
                == browser_gateway_execution_proof_hash(
                    &self.packet,
                    &self.policy_facts,
                    &self.policy_trace,
                    &self.receipt,
                    self.browser_action_plan_hash,
                )
            && nonzero_hash(&self.proof_hash)
    }
}

impl ToolExecutionArtifactEvidence {
    pub fn from_artifact(artifact: &PhysicalArtifact) -> Self {
        let evidence_hash = tool_execution_artifact_evidence_hash(
            artifact.artifact_hash,
            artifact.ast_fingerprint,
            artifact.fuel_consumed,
            artifact.bytes_changed,
        );
        Self {
            artifact_hash: artifact.artifact_hash,
            ast_fingerprint: artifact.ast_fingerprint,
            fuel_consumed: artifact.fuel_consumed,
            bytes_changed: artifact.bytes_changed,
            evidence_hash,
        }
    }

    pub fn to_physical_artifact(self) -> Result<PhysicalArtifact, TrapReason> {
        if !self.is_valid() {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(PhysicalArtifact {
            artifact_hash: self.artifact_hash,
            ast_fingerprint: self.ast_fingerprint,
            fuel_consumed: self.fuel_consumed,
            bytes_changed: self.bytes_changed,
        })
    }

    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.artifact_hash)
            && self.fuel_consumed > 0
            && self.bytes_changed > 0
            && self.evidence_hash
                == tool_execution_artifact_evidence_hash(
                    self.artifact_hash,
                    self.ast_fingerprint,
                    self.fuel_consumed,
                    self.bytes_changed,
                )
            && nonzero_hash(&self.evidence_hash)
    }
}

impl ToolExecutionReplayProof {
    pub fn is_valid_for(
        &self,
        ir: &TypedToolIR,
        proof: &PolicyProofTrace,
        manifest: &RunEventSegmentManifest,
    ) -> bool {
        self.receipt.is_valid_for(ir, proof)
            && manifest.is_valid()
            && self.replay_determinism_proof.is_valid()
            && self.replay_determinism_proof.manifest_hash == manifest.manifest_hash
            && self.replay_determinism_proof.run_id == manifest.run_id
            && self.checkpoint.is_valid()
            && self
                .next_action_packet
                .is_valid_for_checkpoint(&self.checkpoint)
            && self.checkpoint.run_id == manifest.run_id
            && self.checkpoint.event_count == self.replay_determinism_proof.event_count
            && self.checkpoint.segment_count == manifest.entries.len()
            && self.checkpoint.manifest_hash == manifest.manifest_hash
            && self.checkpoint.replay_determinism_proof_hash
                == self.replay_determinism_proof.proof_hash
            && self.next_action_packet.typed_tool_ir_hash == self.receipt.typed_tool_ir_hash
            && self.next_action_packet.policy_proof_hash == self.receipt.policy_proof_trace_hash
            && self.next_action_packet.candidate_evidence_hash
                == self.receipt.physical_evidence_hash
            && self
                .task_selection_event_hash
                .is_none_or(|hash| nonzero_hash(&hash))
            && self.next_action_packet.task_selection_proof_hash.is_some()
                == self.task_selection_event_hash.is_some()
            && self.proof_hash
                == tool_execution_replay_proof_hash(
                    &self.receipt,
                    &self.replay_determinism_proof,
                    &self.checkpoint,
                    &self.next_action_packet,
                    self.task_selection_event_hash,
                )
            && nonzero_hash(&self.proof_hash)
    }

    pub fn is_valid_for_task_selection(
        &self,
        ir: &TypedToolIR,
        proof: &PolicyProofTrace,
        manifest: &RunEventSegmentManifest,
        task_selection_proof: &TaskSelectionProof,
    ) -> bool {
        self.is_valid_for(ir, proof, manifest)
            && self
                .task_selection_event_hash
                .is_some_and(|hash| nonzero_hash(&hash))
            && self
                .next_action_packet
                .is_valid_for_task_selection(&self.checkpoint, task_selection_proof)
    }
}

impl ToolMemoryCommitProof {
    pub fn has_valid_fields(&self) -> bool {
        self.session_id > 0
            && nonzero_hash(&self.execution_replay_proof_hash)
            && nonzero_hash(&self.artifact_evidence_hash)
            && self.semantic_node.is_valid()
            && self.semantic_node.session_id == self.session_id
            && self.memory_len_after_commit > 0
            && self.proof_hash
                == tool_memory_commit_proof_hash(
                    self.session_id,
                    self.execution_replay_proof_hash,
                    self.artifact_evidence_hash,
                    &self.semantic_node,
                    self.memory_len_after_commit,
                )
            && nonzero_hash(&self.proof_hash)
    }

    pub fn is_valid_for(
        &self,
        execution_replay_proof: &ToolExecutionReplayProof,
        artifact_evidence: &ToolExecutionArtifactEvidence,
    ) -> bool {
        self.has_valid_fields()
            && execution_replay_proof.proof_hash == self.execution_replay_proof_hash
            && artifact_evidence.evidence_hash == self.artifact_evidence_hash
            && self.semantic_node.artifact_hash == artifact_evidence.artifact_hash
            && self.semantic_node.ast_fingerprint == artifact_evidence.ast_fingerprint
    }
}

pub struct ToolExecutionGateway;

impl ToolExecutionGateway {
    #[allow(clippy::too_many_arguments)]
    pub fn execute_wasm_with_replay<S: WasmExecutionSandbox>(
        ledger: &mut RunEventLedger,
        sandbox: &S,
        call_id: SubjectId,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        proof: &PolicyProofTrace,
        wasm_bytes: &[u8],
        fuel_limit: u64,
    ) -> Result<ToolExecutionReceipt, TrapReason> {
        if call_id == 0
            || policy_trace_id == 0
            || !ir.is_valid()
            || !proof.is_replay_consistent_with(ir)
            || proof.decision != PolicyDecision::Allow
            || fuel_limit == 0
            || wasm_bytes.is_empty()
        {
            return Err(TrapReason::InvariantViolation);
        }
        let backend_config = sandbox.backend_config();
        if !backend_config.is_hardened() {
            return Err(TrapReason::InvariantViolation);
        }

        let request_event = ledger
            .append_llm_derived_tool_call_requested(call_id, ir.canonical_hash)
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();
        let policy_proof_trace_hash = proof.compute_hash();
        let policy_event = ledger
            .append_policy_decision_recorded(
                policy_trace_id,
                policy_proof_trace_hash,
                ir.canonical_hash,
            )
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();

        // Once request and policy are in the ledger, even sandbox traps must close the attempt.
        let (tool_output_hash, physical_evidence_hash, artifact_evidence, status) =
            match sandbox.execute_wasm_binary(wasm_bytes, fuel_limit) {
                Ok(sandbox_result) if sandbox_result.is_valid() => {
                    let tool_output_hash = sandbox_result.artifact.artifact_hash;
                    (
                        tool_output_hash,
                        tool_physical_evidence_hash(
                            call_id,
                            ir.canonical_hash,
                            policy_proof_trace_hash,
                            tool_output_hash,
                            sandbox_result.fuel_consumed,
                            sandbox_result.backend,
                            backend_config.max_memory_pages,
                            wasm_bytes,
                        ),
                        Some(ToolExecutionArtifactEvidence::from_artifact(
                            &sandbox_result.artifact,
                        )),
                        ToolExecutionStatus::Succeeded,
                    )
                }
                Ok(_) => {
                    let tool_output_hash = tool_failure_output_hash(
                        TrapReason::InvariantViolation,
                        wasm_bytes,
                        fuel_limit,
                        backend_config.backend,
                        backend_config.max_memory_pages,
                    );
                    (
                        tool_output_hash,
                        tool_physical_evidence_hash(
                            call_id,
                            ir.canonical_hash,
                            policy_proof_trace_hash,
                            tool_output_hash,
                            0,
                            backend_config.backend,
                            backend_config.max_memory_pages,
                            wasm_bytes,
                        ),
                        None,
                        ToolExecutionStatus::Failed,
                    )
                }
                Err(trap_reason) => {
                    let tool_output_hash = tool_failure_output_hash(
                        trap_reason,
                        wasm_bytes,
                        fuel_limit,
                        backend_config.backend,
                        backend_config.max_memory_pages,
                    );
                    (
                        tool_output_hash,
                        tool_physical_evidence_hash(
                            call_id,
                            ir.canonical_hash,
                            policy_proof_trace_hash,
                            tool_output_hash,
                            0,
                            backend_config.backend,
                            backend_config.max_memory_pages,
                            wasm_bytes,
                        ),
                        None,
                        ToolExecutionStatus::Failed,
                    )
                }
            };
        let evidence = ToolExecutionEvidence::new(
            ir.canonical_hash,
            policy_proof_trace_hash,
            tool_output_hash,
            physical_evidence_hash,
            ToolExecutorKind::Wasmtime,
            status,
        );
        let completion_event = ledger
            .append_tool_call_completed(call_id, &evidence)
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();

        Ok(ToolExecutionReceipt {
            call_id,
            typed_tool_ir_hash: ir.canonical_hash,
            policy_proof_trace_hash,
            tool_output_hash,
            physical_evidence_hash,
            artifact_evidence,
            tool_execution_evidence: evidence,
            request_event_hash: request_event.event_hash,
            policy_event_hash: policy_event.event_hash,
            completion_event_hash: completion_event.event_hash,
            browser_observation_event_hash: None,
        })
    }

    pub fn execute_browser_observation_with_replay(
        ledger: &mut RunEventLedger,
        call_id: SubjectId,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        facts: &PolicyFacts,
        proof: &PolicyProofTrace,
        packet: &BrowserObservationPacket,
    ) -> Result<ToolExecutionReceipt, TrapReason> {
        if call_id == 0
            || policy_trace_id == 0
            || !ir.is_valid()
            || !matches!(
                ir.capability,
                CapabilityClass::Browser | CapabilityClass::Computer
            )
            || !proof.is_replay_consistent_with(ir)
            || proof.decision != PolicyDecision::Allow
            || !facts.is_valid()
            || proof.policy_version_hash != facts.policy_version_hash
            || proof.facts_hash != facts.facts_hash
            || !facts.has_staging_environment
            || facts.staging_proof_ref_hash != Some(packet.packet_hash)
            || facts.staging_evidence_kind != Some(StagingEvidenceKind::IsolatedBrowserSession)
            || !packet.is_valid()
            || packet.run_id != ledger.run_id
            || packet.action_id != call_id
            || packet.proof.side_effect_class != ir.side_effect
        {
            return Err(TrapReason::InvariantViolation);
        }

        let request_event = ledger
            .append_llm_derived_tool_call_requested(call_id, ir.canonical_hash)
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();
        let packet_event = ledger
            .append_browser_observation_packet_recorded(packet)
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();
        let policy_proof_trace_hash = proof.compute_hash();
        let policy_event = ledger
            .append_policy_decision_recorded_after_browser_observation(
                policy_trace_id,
                policy_proof_trace_hash,
                ir.canonical_hash,
                packet.packet_hash,
            )
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();
        let tool_output_hash = packet.packet_hash;
        let physical_evidence_hash = browser_tool_physical_evidence_hash(
            call_id,
            ir.canonical_hash,
            policy_proof_trace_hash,
            packet,
        );
        let evidence = ToolExecutionEvidence::new(
            ir.canonical_hash,
            policy_proof_trace_hash,
            tool_output_hash,
            physical_evidence_hash,
            browser_executor_kind(packet.collector_kind),
            ToolExecutionStatus::Succeeded,
        );
        let completion_event = ledger
            .append_tool_call_completed(call_id, &evidence)
            .map_err(|_| TrapReason::InvariantViolation)?
            .clone();

        if packet_event.primary_hash != packet.packet_hash {
            return Err(TrapReason::InvariantViolation);
        }

        Ok(ToolExecutionReceipt {
            call_id,
            typed_tool_ir_hash: ir.canonical_hash,
            policy_proof_trace_hash,
            tool_output_hash,
            physical_evidence_hash,
            artifact_evidence: None,
            tool_execution_evidence: evidence,
            request_event_hash: request_event.event_hash,
            policy_event_hash: policy_event.event_hash,
            completion_event_hash: completion_event.event_hash,
            browser_observation_event_hash: Some(packet_event.event_hash),
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn execute_browser_envelope_with_replay(
        ledger: &mut RunEventLedger,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        envelope: &BrowserCollectorEvidenceEnvelope,
        action_trace: BrowserActionTrace,
        side_effect_class: crate::policy::SideEffectClass,
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
    ) -> Result<BrowserGatewayExecutionProof, TrapReason> {
        if !envelope.is_valid()
            || envelope.run_id != ledger.run_id
            || envelope.action_id == 0
            || side_effect_class != ir.side_effect
            || !nonzero_hash(&policy_version_hash)
        {
            return Err(TrapReason::InvariantViolation);
        }
        let packet = BrowserObservationPacket::from_collector_envelope(
            envelope,
            action_trace,
            side_effect_class,
        )
        .ok_or(TrapReason::InvariantViolation)?;
        let policy_facts = packet
            .isolated_browser_policy_facts(
                policy_version_hash,
                has_valid_approval,
                envelope.policy_window_hash,
                envelope.browser_session_hash,
                envelope.redaction_policy_hash,
            )
            .ok_or(TrapReason::InvariantViolation)?;
        let policy_trace = crate::policy::DeterministicPolicyKernel.evaluate(ir, &policy_facts);
        let receipt = Self::execute_browser_observation_with_replay(
            ledger,
            packet.action_id,
            policy_trace_id,
            ir,
            &policy_facts,
            &policy_trace,
            &packet,
        )?;
        let proof_hash = browser_gateway_execution_proof_hash(
            &packet,
            &policy_facts,
            &policy_trace,
            &receipt,
            None,
        );
        let proof = BrowserGatewayExecutionProof {
            packet,
            policy_facts,
            policy_trace,
            receipt,
            browser_action_plan_hash: None,
            proof_hash,
        };
        if !proof.is_valid_for(
            ledger.run_id,
            ir,
            policy_version_hash,
            envelope.policy_window_hash,
            envelope.browser_session_hash,
            envelope.redaction_policy_hash,
        ) {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(proof)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn execute_browser_file_path_refs_with_replay(
        ledger: &mut RunEventLedger,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        collector_kind: BrowserCollectorKind,
        action_trace: BrowserActionTrace,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        artifact_path_refs: &[BrowserArtifactFilePathRef],
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
    ) -> Result<BrowserGatewayExecutionProof, TrapReason> {
        let envelope = BrowserCollectorEvidenceEnvelope::from_file_path_refs(
            collector_kind,
            ledger.run_id,
            action_trace.action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            action_trace.policy_window_hash,
            artifact_path_refs,
        )
        .map_err(browser_artifact_read_error_to_trap)?;
        Self::execute_browser_envelope_with_replay(
            ledger,
            policy_trace_id,
            ir,
            &envelope,
            action_trace,
            ir.side_effect,
            policy_version_hash,
            has_valid_approval,
        )
    }

    pub fn execute_browser_live_manifest_with_replay(
        ledger: &mut RunEventLedger,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        manifest: &BrowserLiveCollectorManifest,
        action_trace: BrowserActionTrace,
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
    ) -> Result<BrowserGatewayExecutionProof, TrapReason> {
        if !manifest.binds_action_trace(&action_trace) || manifest.run_id != ledger.run_id {
            return Err(TrapReason::InvariantViolation);
        }
        let envelope = manifest
            .to_envelope()
            .map_err(browser_artifact_read_error_to_trap)?;
        Self::execute_browser_envelope_with_replay(
            ledger,
            policy_trace_id,
            ir,
            &envelope,
            action_trace,
            ir.side_effect,
            policy_version_hash,
            has_valid_approval,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn execute_browser_action_plan_file_path_refs_with_replay(
        ledger: &mut RunEventLedger,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        collector_kind: BrowserCollectorKind,
        plan: &BrowserActionPlanRecord,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        artifact_path_refs: &[BrowserArtifactFilePathRef],
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
    ) -> Result<BrowserGatewayExecutionProof, TrapReason> {
        let action_trace = plan
            .to_action_trace()
            .ok_or(TrapReason::InvariantViolation)?;
        if plan.run_id != ledger.run_id
            || plan.typed_tool_ir_hash != ir.canonical_hash
            || plan.sequence_number == 0
            || !nonzero_hash(&plan.plan_hash)
        {
            return Err(TrapReason::InvariantViolation);
        }
        let envelope = BrowserCollectorEvidenceEnvelope::from_file_path_refs(
            collector_kind,
            ledger.run_id,
            plan.action_id,
            plan.sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            plan.browser_session_hash,
            plan.redaction_policy_hash,
            plan.policy_window_hash,
            artifact_path_refs,
        )
        .map_err(browser_artifact_read_error_to_trap)?;
        let packet = BrowserObservationPacket::from_collector_envelope(
            &envelope,
            action_trace,
            ir.side_effect,
        )
        .ok_or(TrapReason::InvariantViolation)?;
        if !plan.is_valid_for_packet(ir.canonical_hash, &packet) {
            return Err(TrapReason::InvariantViolation);
        }
        let policy_facts = packet
            .isolated_browser_policy_facts(
                policy_version_hash,
                has_valid_approval,
                plan.policy_window_hash,
                plan.browser_session_hash,
                plan.redaction_policy_hash,
            )
            .ok_or(TrapReason::InvariantViolation)?;
        let policy_trace = crate::policy::DeterministicPolicyKernel.evaluate(ir, &policy_facts);
        let receipt = Self::execute_browser_observation_with_replay(
            ledger,
            packet.action_id,
            policy_trace_id,
            ir,
            &policy_facts,
            &policy_trace,
            &packet,
        )?;
        let proof_hash = browser_gateway_execution_proof_hash(
            &packet,
            &policy_facts,
            &policy_trace,
            &receipt,
            Some(plan.plan_hash),
        );
        let proof = BrowserGatewayExecutionProof {
            packet,
            policy_facts,
            policy_trace,
            receipt,
            browser_action_plan_hash: Some(plan.plan_hash),
            proof_hash,
        };
        if !proof.is_valid_for(
            ledger.run_id,
            ir,
            policy_version_hash,
            plan.policy_window_hash,
            plan.browser_session_hash,
            plan.redaction_policy_hash,
        ) {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(proof)
    }

    pub fn execute_browser_action_plan_live_manifest_with_replay(
        ledger: &mut RunEventLedger,
        policy_trace_id: SubjectId,
        ir: &TypedToolIR,
        manifest: &BrowserLiveCollectorManifest,
        plan: &BrowserActionPlanRecord,
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
    ) -> Result<BrowserGatewayExecutionProof, TrapReason> {
        let action_trace = plan
            .to_action_trace()
            .ok_or(TrapReason::InvariantViolation)?;
        if !manifest.binds_plan(plan)
            || manifest.run_id != ledger.run_id
            || plan.typed_tool_ir_hash != ir.canonical_hash
            || !nonzero_hash(&policy_version_hash)
        {
            return Err(TrapReason::InvariantViolation);
        }
        let envelope = manifest
            .to_envelope()
            .map_err(browser_artifact_read_error_to_trap)?;
        let packet = BrowserObservationPacket::from_collector_envelope(
            &envelope,
            action_trace,
            ir.side_effect,
        )
        .ok_or(TrapReason::InvariantViolation)?;
        if !plan.is_valid_for_packet(ir.canonical_hash, &packet) {
            return Err(TrapReason::InvariantViolation);
        }
        let policy_facts = packet
            .isolated_browser_policy_facts(
                policy_version_hash,
                has_valid_approval,
                plan.policy_window_hash,
                plan.browser_session_hash,
                plan.redaction_policy_hash,
            )
            .ok_or(TrapReason::InvariantViolation)?;
        let policy_trace = crate::policy::DeterministicPolicyKernel.evaluate(ir, &policy_facts);
        let receipt = Self::execute_browser_observation_with_replay(
            ledger,
            packet.action_id,
            policy_trace_id,
            ir,
            &policy_facts,
            &policy_trace,
            &packet,
        )?;
        let proof_hash = browser_gateway_execution_proof_hash(
            &packet,
            &policy_facts,
            &policy_trace,
            &receipt,
            Some(plan.plan_hash),
        );
        let proof = BrowserGatewayExecutionProof {
            packet,
            policy_facts,
            policy_trace,
            receipt,
            browser_action_plan_hash: Some(plan.plan_hash),
            proof_hash,
        };
        if !proof.is_valid_for(
            ledger.run_id,
            ir,
            policy_version_hash,
            plan.policy_window_hash,
            plan.browser_session_hash,
            plan.redaction_policy_hash,
        ) {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(proof)
    }

    pub fn replay_events_are_bound(receipt: &ToolExecutionReceipt, events: &[RunEvent]) -> bool {
        events
            .iter()
            .any(|event| event.event_hash == receipt.request_event_hash)
            && events
                .iter()
                .any(|event| event.event_hash == receipt.policy_event_hash)
            && events
                .iter()
                .any(|event| event.event_hash == receipt.completion_event_hash)
            && receipt
                .browser_observation_event_hash
                .is_none_or(|hash| events.iter().any(|event| event.event_hash == hash))
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_replay_checkpoint_and_next_action(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        receipt: ToolExecutionReceipt,
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        task_id: SubjectId,
        evidence_contract_hash: [u8; 32],
    ) -> Result<ToolExecutionReplayProof, &'static str> {
        Self::seal_replay_checkpoint_and_next_action_inner(
            directory,
            manifest,
            receipt,
            checkpoint_id,
            packet_id,
            action_kind,
            task_id,
            evidence_contract_hash,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_replay_checkpoint_and_next_action_with_task_selection(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        receipt: ToolExecutionReceipt,
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        evidence_contract_hash: [u8; 32],
        task_selection_proof: &TaskSelectionProof,
    ) -> Result<ToolExecutionReplayProof, &'static str> {
        Self::seal_replay_checkpoint_and_next_action_inner(
            directory,
            manifest,
            receipt,
            checkpoint_id,
            packet_id,
            action_kind,
            task_selection_proof.selected_task_id,
            evidence_contract_hash,
            Some(task_selection_proof),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn seal_replay_checkpoint_and_next_action_inner(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        receipt: ToolExecutionReceipt,
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        task_id: SubjectId,
        evidence_contract_hash: [u8; 32],
        task_selection_proof: Option<&TaskSelectionProof>,
    ) -> Result<ToolExecutionReplayProof, &'static str> {
        if checkpoint_id == 0
            || packet_id == 0
            || task_id == 0
            || !manifest.is_valid()
            || !receipt.has_valid_fields()
            || !nonzero_hash(&evidence_contract_hash)
            || task_selection_proof
                .is_some_and(|proof| !proof.has_valid_fields() || proof.selected_task_id != task_id)
        {
            return Err("invalid tool execution replay proof inputs");
        }
        let directory = directory.as_ref();
        let replay_determinism_proof =
            RunEventSegmentArchive::prove_replay_determinism(directory, manifest)?;
        if replay_determinism_proof.run_id != manifest.run_id
            || replay_determinism_proof.manifest_hash != manifest.manifest_hash
        {
            return Err("tool execution replay proof manifest mismatch");
        }
        let ledger = RunEventSegmentArchive::read_ledger_mmap(directory, manifest)?;
        if ledger.len() != replay_determinism_proof.event_count
            || ledger.last_hash() != replay_determinism_proof.first_pass_ledger_hash
            || !Self::replay_events_are_bound(&receipt, ledger.events())
        {
            return Err("tool execution replay receipt missing from archive");
        }
        let request_event = ledger
            .events()
            .iter()
            .find(|event| event.event_hash == receipt.request_event_hash)
            .ok_or("tool execution request event missing from archive")?;
        if let Some(browser_observation_event_hash) = receipt.browser_observation_event_hash {
            let event = ledger
                .events()
                .iter()
                .find(|event| event.event_hash == browser_observation_event_hash)
                .ok_or("browser observation event missing from replay archive")?;
            if event.kind != RunEventKind::BrowserObservationPacketRecorded
                || event.event_id != request_event.event_id.saturating_add(1)
                || event.primary_hash != receipt.tool_output_hash
            {
                return Err("browser observation event is not bound to tool request");
            }
        }
        let task_selection_event_hash = if let Some(proof) = task_selection_proof {
            let event = ledger
                .events()
                .iter()
                .find(|event| {
                    event.kind == RunEventKind::TaskSelectionProofRecorded
                        && event.subject_id == proof.selected_task_id
                        && event.primary_hash == proof.proof_hash
                        && event.secondary_hash == Some(proof.ready_queue_hash)
                })
                .ok_or("task selection proof event missing from replay archive")?;
            if event.event_id >= request_event.event_id {
                return Err("task selection proof event must precede tool request");
            }
            Some(event.event_hash)
        } else {
            None
        };
        let checkpoint = RunCheckpoint::new(
            manifest.run_id,
            checkpoint_id,
            replay_determinism_proof.event_count,
            manifest.entries.len(),
            replay_determinism_proof.first_pass_ledger_hash,
            manifest.manifest_hash,
            replay_determinism_proof.proof_hash,
            receipt.tool_execution_evidence.evidence_hash,
        );
        if !checkpoint.is_valid() {
            return Err("invalid tool execution replay checkpoint");
        }
        let next_action_packet = if let Some(proof) = task_selection_proof {
            NextActionPacket::new_with_task_selection(
                manifest.run_id,
                packet_id,
                checkpoint.checkpoint_hash,
                action_kind,
                task_id,
                receipt.typed_tool_ir_hash,
                evidence_contract_hash,
                receipt.policy_proof_trace_hash,
                receipt.physical_evidence_hash,
                proof,
            )
        } else {
            NextActionPacket::new(
                manifest.run_id,
                packet_id,
                checkpoint.checkpoint_hash,
                action_kind,
                task_id,
                receipt.typed_tool_ir_hash,
                evidence_contract_hash,
                receipt.policy_proof_trace_hash,
                receipt.physical_evidence_hash,
            )
        };
        let next_action_is_valid = task_selection_proof
            .map(|proof| next_action_packet.is_valid_for_task_selection(&checkpoint, proof))
            .unwrap_or_else(|| next_action_packet.is_valid_for_checkpoint(&checkpoint));
        if !next_action_is_valid {
            return Err("invalid tool execution next action packet");
        }
        let proof_hash = tool_execution_replay_proof_hash(
            &receipt,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
            task_selection_event_hash,
        );
        Ok(ToolExecutionReplayProof {
            receipt,
            replay_determinism_proof,
            checkpoint,
            next_action_packet,
            task_selection_event_hash,
            proof_hash,
        })
    }

    pub fn commit_replay_proven_tool_output_to_cognifold(
        store: &mut CogniFoldStore,
        session_id: SubjectId,
        execution_replay_proof: &ToolExecutionReplayProof,
        ir: &TypedToolIR,
        proof: &PolicyProofTrace,
        manifest: &RunEventSegmentManifest,
        watchdog: &PhysicalWatchdog,
    ) -> Result<ToolMemoryCommitProof, BacktrackSignal> {
        if session_id == 0 || !execution_replay_proof.is_valid_for(ir, proof, manifest) {
            return Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation,
            ));
        }
        if execution_replay_proof
            .receipt
            .tool_execution_evidence
            .status
            != ToolExecutionStatus::Succeeded
        {
            return Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation,
            ));
        }
        let artifact_evidence = execution_replay_proof.receipt.artifact_evidence.ok_or(
            BacktrackSignal::HardBacktrack(TrapReason::InvariantViolation),
        )?;
        let artifact = artifact_evidence
            .to_physical_artifact()
            .map_err(BacktrackSignal::HardBacktrack)?;
        let node = store.commit_to_cognifold(session_id, &artifact, watchdog)?;
        let memory_len_after_commit = store.len();
        let proof_hash = tool_memory_commit_proof_hash(
            session_id,
            execution_replay_proof.proof_hash,
            artifact_evidence.evidence_hash,
            &node,
            memory_len_after_commit,
        );
        let commit_proof = ToolMemoryCommitProof {
            session_id,
            execution_replay_proof_hash: execution_replay_proof.proof_hash,
            artifact_evidence_hash: artifact_evidence.evidence_hash,
            semantic_node: node,
            memory_len_after_commit,
            proof_hash,
        };
        if !commit_proof.is_valid_for(execution_replay_proof, &artifact_evidence) {
            return Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation,
            ));
        }
        Ok(commit_proof)
    }
}

fn browser_artifact_read_error_to_trap(_error: BrowserArtifactReadError) -> TrapReason {
    TrapReason::InvariantViolation
}

pub fn browser_tool_physical_evidence_hash(
    call_id: SubjectId,
    typed_tool_ir_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    packet: &BrowserObservationPacket,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-tool-physical-evidence-v1");
    hasher.update(&call_id.to_le_bytes());
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&policy_proof_trace_hash);
    hasher.update(&packet.packet_hash);
    hasher.update(&packet.proof.proof_hash);
    hasher.update(&packet.action_trace.trace_hash);
    hasher.update(&packet.raw_artifact_manifest_hash);
    hasher.update(&packet.modality_bundle_hash);
    hasher.update(&packet.collector_provenance_hash);
    hasher.update(&browser_observation_packet_replay_binding_hash(packet));
    *hasher.finalize().as_bytes()
}

pub fn browser_gateway_execution_proof_hash(
    packet: &BrowserObservationPacket,
    facts: &PolicyFacts,
    policy_trace: &PolicyProofTrace,
    receipt: &ToolExecutionReceipt,
    browser_action_plan_hash: Option<[u8; 32]>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-gateway-execution-proof-v1");
    hasher.update(&packet.packet_hash);
    hasher.update(&facts.facts_hash);
    hasher.update(&policy_trace.compute_hash());
    hasher.update(&receipt.request_event_hash);
    if let Some(hash) = receipt.browser_observation_event_hash {
        hasher.update(&[1]);
        hasher.update(&hash);
    } else {
        hasher.update(&[0]);
    }
    hasher.update(&receipt.policy_event_hash);
    hasher.update(&receipt.completion_event_hash);
    hasher.update(&receipt.tool_execution_evidence.evidence_hash);
    hasher.update(&receipt.physical_evidence_hash);
    match browser_action_plan_hash {
        Some(hash) => {
            hasher.update(&[1]);
            hasher.update(&hash);
        }
        None => {
            hasher.update(&[0]);
        }
    }
    *hasher.finalize().as_bytes()
}

fn browser_executor_kind(collector_kind: BrowserCollectorKind) -> ToolExecutorKind {
    match collector_kind {
        BrowserCollectorKind::ChromeExtension => ToolExecutorKind::Chrome,
        BrowserCollectorKind::ComputerUse => ToolExecutorKind::Computer,
        BrowserCollectorKind::InAppBrowser | BrowserCollectorKind::PlaywrightCdp => {
            ToolExecutorKind::Browser
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub fn tool_physical_evidence_hash(
    call_id: SubjectId,
    typed_tool_ir_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    tool_output_hash: [u8; 32],
    fuel_consumed: u64,
    backend: SandboxBackendKind,
    max_memory_pages: u32,
    wasm_bytes: &[u8],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-physical-evidence-v1");
    hasher.update(&call_id.to_le_bytes());
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&policy_proof_trace_hash);
    hasher.update(&tool_output_hash);
    hasher.update(&fuel_consumed.to_le_bytes());
    hasher.update(&[backend as u8]);
    hasher.update(&max_memory_pages.to_le_bytes());
    hasher.update(&crate::physical::blake3_digest(wasm_bytes));
    *hasher.finalize().as_bytes()
}

pub fn tool_failure_output_hash(
    trap_reason: TrapReason,
    wasm_bytes: &[u8],
    fuel_limit: u64,
    backend: SandboxBackendKind,
    max_memory_pages: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-failure-output-v1");
    hasher.update(&[trap_reason_tag(trap_reason)]);
    hasher.update(&fuel_limit.to_le_bytes());
    hasher.update(&[backend as u8]);
    hasher.update(&max_memory_pages.to_le_bytes());
    hasher.update(&crate::physical::blake3_digest(wasm_bytes));
    *hasher.finalize().as_bytes()
}

pub fn evidence_contract_hash(ir: &TypedToolIR) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-evidence-contract-v1");
    hasher.update(&ir.canonical_hash);
    hasher.update(&[u8::from(ir.evidence_contract.requires_physical_witness)]);
    hasher.update(&[u8::from(ir.evidence_contract.requires_approval)]);
    hasher.update(&[u8::from(ir.evidence_contract.requires_staging)]);
    match ir.evidence_contract.expected_artifact_hash {
        Some(hash) => {
            hasher.update(&[1]);
            hasher.update(&hash);
        }
        None => {
            hasher.update(&[0]);
        }
    }
    *hasher.finalize().as_bytes()
}

pub fn tool_execution_artifact_evidence_hash(
    artifact_hash: [u8; 32],
    ast_fingerprint: u64,
    fuel_consumed: u64,
    bytes_changed: usize,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-execution-artifact-evidence-v1");
    hasher.update(&artifact_hash);
    hasher.update(&ast_fingerprint.to_le_bytes());
    hasher.update(&fuel_consumed.to_le_bytes());
    hasher.update(&(bytes_changed as u64).to_le_bytes());
    *hasher.finalize().as_bytes()
}

pub fn tool_execution_replay_proof_hash(
    receipt: &ToolExecutionReceipt,
    replay_determinism_proof: &ReplayDeterminismProof,
    checkpoint: &RunCheckpoint,
    next_action_packet: &NextActionPacket,
    task_selection_event_hash: Option<[u8; 32]>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-execution-replay-proof-v2");
    hasher.update(&receipt.request_event_hash);
    hasher.update(&receipt.policy_event_hash);
    hasher.update(&receipt.completion_event_hash);
    update_optional_hash(&mut hasher, receipt.browser_observation_event_hash);
    hasher.update(&receipt.tool_execution_evidence.evidence_hash);
    match receipt.artifact_evidence {
        Some(artifact_evidence) => {
            hasher.update(&[1]);
            hasher.update(&artifact_evidence.evidence_hash);
        }
        None => {
            hasher.update(&[0]);
        }
    }
    hasher.update(&replay_determinism_proof.proof_hash);
    hasher.update(&checkpoint.checkpoint_hash);
    hasher.update(&next_action_packet.packet_hash);
    update_optional_hash(&mut hasher, task_selection_event_hash);
    *hasher.finalize().as_bytes()
}

pub fn tool_memory_commit_proof_hash(
    session_id: SubjectId,
    execution_replay_proof_hash: [u8; 32],
    artifact_evidence_hash: [u8; 32],
    semantic_node: &SemanticNode,
    memory_len_after_commit: usize,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-memory-commit-proof-v1");
    hasher.update(&session_id.to_le_bytes());
    hasher.update(&execution_replay_proof_hash);
    hasher.update(&artifact_evidence_hash);
    hasher.update(&semantic_node.node_id.to_le_bytes());
    hasher.update(&semantic_node.session_id.to_le_bytes());
    hasher.update(&semantic_node.artifact_hash);
    hasher.update(&semantic_node.ast_fingerprint.to_le_bytes());
    hasher.update(&semantic_node.fidelity.to_bits().to_le_bytes());
    hasher.update(&(memory_len_after_commit as u64).to_le_bytes());
    *hasher.finalize().as_bytes()
}

fn trap_reason_tag(trap_reason: TrapReason) -> u8 {
    match trap_reason {
        TrapReason::EmptyArtifact => 1,
        TrapReason::FuelExhausted => 2,
        TrapReason::InvariantViolation => 3,
        TrapReason::PavBelowThreshold => 4,
        TrapReason::NoConsensus => 5,
    }
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn update_optional_hash(hasher: &mut Hasher, hash: Option<[u8; 32]>) {
    match hash {
        Some(hash) => {
            hasher.update(&[1]);
            hasher.update(&hash);
        }
        None => {
            hasher.update(&[0]);
        }
    }
}
