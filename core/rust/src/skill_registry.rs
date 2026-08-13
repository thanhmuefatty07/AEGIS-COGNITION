use std::collections::BTreeMap;

use blake3::Hasher;

use crate::learning::LearningLedger;
use crate::licensing::{Feature as LicenseFeature, LicenseError, LicenseManager};
use crate::physical::{canonicalize_payload, BacktrackSignal, PhysicalWatchdog};
use crate::policy::{PolicyProofTrace, TypedToolIR};
use crate::replay::{
    NextActionKind, RunEventLedger, RunEventSegmentManifest, SkillAdmissionHandoffProof,
};
use crate::sandbox::{SandboxBackendKind, SandboxResult, WasmExecutionSandbox};
use crate::tool_gateway::{ToolExecutionGateway, ToolExecutionReceipt, ToolExecutionReplayProof};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SkillAdmissionError {
    InvalidManifest,
    InvalidRegression,
    InvalidWasmtimeProof,
    PavRejected,
    RegressionFailed,
    InvalidAdmissionRecord,
    DuplicateSkill,
    AdmissionMismatch,
    MissingAdmission,
    DuplicateActivation,
    InvalidActivationProof,
    InactiveSkill,
    SkillExecutionPolicyMismatch,
    SkillExecutionArtifactMismatch,
    SkillExecutionGatewayRejected,
    SkillExecutionReplayMismatch,
    InsufficientUsage,
    LicenseRequired(LicenseError),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillPackageManifest {
    pub skill_id: u128,
    pub name_hash: [u8; 32],
    pub version_hash: [u8; 32],
    pub skill_md_hash: [u8; 32],
    pub wasm_module_hash: [u8; 32],
    pub policy_manifest_hash: [u8; 32],
    pub regression_suite_hash: [u8; 32],
    pub package_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SkillRegressionCase {
    pub case_id: u64,
    pub input_hash: [u8; 32],
    pub expected_hash: [u8; 32],
    pub actual_hash: [u8; 32],
    pub passed: bool,
    pub case_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillRegressionReport {
    pub case_count: u32,
    pub passed_count: u32,
    pub failed_count: u32,
    pub case_list_hash: [u8; 32],
    pub report_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillAdmissionRecord {
    pub package_hash: [u8; 32],
    pub wasm_module_hash: [u8; 32],
    pub wasmtime_artifact_hash: [u8; 32],
    pub wasmtime_ast_fingerprint: u64,
    pub fuel_consumed: u64,
    pub regression_report_hash: [u8; 32],
    pub pav_acceptance_hash: [u8; 32],
    pub admission_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AdmittedSkillRecord {
    pub skill_id: u128,
    pub package_hash: [u8; 32],
    pub admission_hash: [u8; 32],
    pub registry_epoch: u64,
    pub registry_commit_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ActivatedSkillRecord {
    pub skill_id: u128,
    pub package_hash: [u8; 32],
    pub admission_hash: [u8; 32],
    pub registry_epoch: u64,
    pub registry_commit_hash: [u8; 32],
    pub skill_admission_event_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
    pub activation_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillExecutionProof {
    pub receipt: ToolExecutionReceipt,
    pub skill_id: u128,
    pub package_hash: [u8; 32],
    pub admission_hash: [u8; 32],
    pub wasm_module_hash: [u8; 32],
    pub activation_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
    pub typed_tool_ir_hash: [u8; 32],
    pub policy_proof_trace_hash: [u8; 32],
    pub request_event_hash: [u8; 32],
    pub policy_event_hash: [u8; 32],
    pub completion_event_hash: [u8; 32],
    pub tool_execution_evidence_hash: [u8; 32],
    pub artifact_evidence_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillExecutionHandoffProof {
    pub skill_id: u128,
    pub package_hash: [u8; 32],
    pub admission_hash: [u8; 32],
    pub activation_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
    pub skill_execution_proof_hash: [u8; 32],
    pub tool_execution_replay_proof_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub candidate_evidence_hash: [u8; 32],
    pub replay_handoff_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq)]
pub struct SkillUsageStats {
    pub usage_count: u32,
    pub success_rate: f32,
    pub failure_modes: Vec<String>,
}

impl SkillUsageStats {
    pub fn new(usage_count: u32, success_rate: f32) -> Self {
        Self {
            usage_count,
            success_rate,
            failure_modes: Vec::new(),
        }
    }

    pub fn is_valid(&self) -> bool {
        self.usage_count > 0 && self.success_rate >= 0.0 && self.success_rate <= 1.0
    }
}

/// Record of an autonomous skill improvement.
///
/// Chained into the `LearningLedger` for cryptographic audit.
/// Every field is hashed into `record_hash` using the same domain-hasher
/// pattern as the rest of AEGIS.
#[derive(Clone, Debug, PartialEq)]
pub struct SkillImprovementRecord {
    pub skill_id: u128,
    pub previous_package_hash: [u8; 32],
    pub previous_admission_hash: [u8; 32],
    pub usage_count: u32,
    pub success_rate: f32,
    pub improvement_hash: [u8; 32],
    pub regression_report_hash: [u8; 32],
    pub timestamp: u64,
    pub record_hash: [u8; 32],
}

impl SkillImprovementRecord {
    pub fn new(
        skill_id: u128,
        previous_package_hash: [u8; 32],
        previous_admission_hash: [u8; 32],
        usage_count: u32,
        success_rate: f32,
        improvement_hash: [u8; 32],
        regression_report_hash: [u8; 32],
        timestamp: u64,
    ) -> Option<Self> {
        if skill_id == 0
            || usage_count == 0
            || !nonzero_hash(&previous_package_hash)
            || !nonzero_hash(&previous_admission_hash)
            || !nonzero_hash(&improvement_hash)
            || !nonzero_hash(&regression_report_hash)
            || timestamp == 0
        {
            return None;
        }
        let record_hash = skill_improvement_record_hash(
            skill_id,
            previous_package_hash,
            previous_admission_hash,
            usage_count,
            success_rate,
            improvement_hash,
            regression_report_hash,
            timestamp,
        );
        Some(Self {
            skill_id,
            previous_package_hash,
            previous_admission_hash,
            usage_count,
            success_rate,
            improvement_hash,
            regression_report_hash,
            timestamp,
            record_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.skill_id > 0
            && self.usage_count > 0
            && nonzero_hash(&self.previous_package_hash)
            && nonzero_hash(&self.previous_admission_hash)
            && nonzero_hash(&self.improvement_hash)
            && nonzero_hash(&self.regression_report_hash)
            && self.timestamp > 0
            && self.record_hash
                == skill_improvement_record_hash(
                    self.skill_id,
                    self.previous_package_hash,
                    self.previous_admission_hash,
                    self.usage_count,
                    self.success_rate,
                    self.improvement_hash,
                    self.regression_report_hash,
                    self.timestamp,
                )
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SkillRegistry {
    records: BTreeMap<u128, AdmittedSkillRecord>,
    active_records: BTreeMap<u128, ActivatedSkillRecord>,
    last_registry_commit_hash: [u8; 32],
    epoch: u64,
}

impl SkillPackageManifest {
    pub fn new(
        skill_id: u128,
        name_hash: [u8; 32],
        version_hash: [u8; 32],
        skill_md_hash: [u8; 32],
        wasm_module_hash: [u8; 32],
        policy_manifest_hash: [u8; 32],
        regression_suite_hash: [u8; 32],
    ) -> Result<Self, SkillAdmissionError> {
        if skill_id == 0
            || !nonzero_hash(&name_hash)
            || !nonzero_hash(&version_hash)
            || !nonzero_hash(&skill_md_hash)
            || !nonzero_hash(&wasm_module_hash)
            || !nonzero_hash(&policy_manifest_hash)
            || !nonzero_hash(&regression_suite_hash)
        {
            return Err(SkillAdmissionError::InvalidManifest);
        }
        let package_hash = skill_package_hash(
            skill_id,
            name_hash,
            version_hash,
            skill_md_hash,
            wasm_module_hash,
            policy_manifest_hash,
            regression_suite_hash,
        );
        Ok(Self {
            skill_id,
            name_hash,
            version_hash,
            skill_md_hash,
            wasm_module_hash,
            policy_manifest_hash,
            regression_suite_hash,
            package_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.skill_id > 0
            && nonzero_hash(&self.name_hash)
            && nonzero_hash(&self.version_hash)
            && nonzero_hash(&self.skill_md_hash)
            && nonzero_hash(&self.wasm_module_hash)
            && nonzero_hash(&self.policy_manifest_hash)
            && nonzero_hash(&self.regression_suite_hash)
            && self.package_hash
                == skill_package_hash(
                    self.skill_id,
                    self.name_hash,
                    self.version_hash,
                    self.skill_md_hash,
                    self.wasm_module_hash,
                    self.policy_manifest_hash,
                    self.regression_suite_hash,
                )
    }
}

impl SkillRegressionCase {
    pub fn new(
        case_id: u64,
        input_hash: [u8; 32],
        expected_hash: [u8; 32],
        actual_hash: [u8; 32],
        passed: bool,
    ) -> Result<Self, SkillAdmissionError> {
        if case_id == 0
            || !nonzero_hash(&input_hash)
            || !nonzero_hash(&expected_hash)
            || !nonzero_hash(&actual_hash)
        {
            return Err(SkillAdmissionError::InvalidRegression);
        }
        let case_hash =
            skill_regression_case_hash(case_id, input_hash, expected_hash, actual_hash, passed);
        Ok(Self {
            case_id,
            input_hash,
            expected_hash,
            actual_hash,
            passed,
            case_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.case_id > 0
            && nonzero_hash(&self.input_hash)
            && nonzero_hash(&self.expected_hash)
            && nonzero_hash(&self.actual_hash)
            && self.case_hash
                == skill_regression_case_hash(
                    self.case_id,
                    self.input_hash,
                    self.expected_hash,
                    self.actual_hash,
                    self.passed,
                )
    }
}

impl SkillRegressionReport {
    pub fn new(cases: &[SkillRegressionCase]) -> Result<Self, SkillAdmissionError> {
        if cases.is_empty() || cases.iter().any(|case| !case.is_valid()) {
            return Err(SkillAdmissionError::InvalidRegression);
        }
        let failed_count = cases.iter().filter(|case| !case.passed).count() as u32;
        let passed_count = cases.len() as u32 - failed_count;
        let case_list_hash = skill_regression_suite_hash(cases);
        let report_hash = skill_regression_report_hash(
            cases.len() as u32,
            passed_count,
            failed_count,
            case_list_hash,
        );
        Ok(Self {
            case_count: cases.len() as u32,
            passed_count,
            failed_count,
            case_list_hash,
            report_hash,
        })
    }

    pub fn is_valid_for(&self, cases: &[SkillRegressionCase]) -> bool {
        !cases.is_empty()
            && cases.iter().all(SkillRegressionCase::is_valid)
            && self.case_count == cases.len() as u32
            && self.failed_count == cases.iter().filter(|case| !case.passed).count() as u32
            && self.passed_count == self.case_count.saturating_sub(self.failed_count)
            && self.case_list_hash == skill_regression_suite_hash(cases)
            && self.report_hash
                == skill_regression_report_hash(
                    self.case_count,
                    self.passed_count,
                    self.failed_count,
                    self.case_list_hash,
                )
    }
}

impl SkillAdmissionRecord {
    pub fn from_wasmtime_and_regressions(
        manifest: &SkillPackageManifest,
        sandbox_result: &SandboxResult,
        regression_report: &SkillRegressionReport,
        watchdog: &PhysicalWatchdog,
    ) -> Result<Self, SkillAdmissionError> {
        if !manifest.is_valid() {
            return Err(SkillAdmissionError::InvalidManifest);
        }
        if sandbox_result.backend != SandboxBackendKind::Wasmtime
            || !sandbox_result.is_valid()
            || sandbox_result.artifact.artifact_hash != manifest.wasm_module_hash
        {
            return Err(SkillAdmissionError::InvalidWasmtimeProof);
        }
        if regression_report.failed_count != 0
            || regression_report.passed_count == 0
            || !nonzero_hash(&regression_report.report_hash)
        {
            return Err(SkillAdmissionError::RegressionFailed);
        }
        let pav_acceptance_hash = match watchdog.accepts(&sandbox_result.artifact) {
            Ok(()) => skill_pav_acceptance_hash(
                manifest.package_hash,
                sandbox_result.artifact.artifact_hash,
                sandbox_result.artifact.ast_fingerprint,
                sandbox_result.fuel_consumed,
            ),
            Err(BacktrackSignal::HardBacktrack(_)) => {
                return Err(SkillAdmissionError::PavRejected);
            }
        };
        let admission_hash = skill_admission_hash(
            manifest.package_hash,
            manifest.wasm_module_hash,
            sandbox_result.artifact.artifact_hash,
            sandbox_result.artifact.ast_fingerprint,
            sandbox_result.fuel_consumed,
            regression_report.report_hash,
            pav_acceptance_hash,
        );
        Ok(Self {
            package_hash: manifest.package_hash,
            wasm_module_hash: manifest.wasm_module_hash,
            wasmtime_artifact_hash: sandbox_result.artifact.artifact_hash,
            wasmtime_ast_fingerprint: sandbox_result.artifact.ast_fingerprint,
            fuel_consumed: sandbox_result.fuel_consumed,
            regression_report_hash: regression_report.report_hash,
            pav_acceptance_hash,
            admission_hash,
        })
    }

    pub fn is_valid_for(
        &self,
        manifest: &SkillPackageManifest,
        regression_report: &SkillRegressionReport,
    ) -> bool {
        manifest.is_valid()
            && regression_report.failed_count == 0
            && regression_report.passed_count > 0
            && self.package_hash == manifest.package_hash
            && self.wasm_module_hash == manifest.wasm_module_hash
            && self.wasmtime_artifact_hash == manifest.wasm_module_hash
            && self.fuel_consumed > 0
            && nonzero_hash(&self.pav_acceptance_hash)
            && self.regression_report_hash == regression_report.report_hash
            && self.pav_acceptance_hash
                == skill_pav_acceptance_hash(
                    self.package_hash,
                    self.wasmtime_artifact_hash,
                    self.wasmtime_ast_fingerprint,
                    self.fuel_consumed,
                )
            && self.admission_hash
                == skill_admission_hash(
                    self.package_hash,
                    self.wasm_module_hash,
                    self.wasmtime_artifact_hash,
                    self.wasmtime_ast_fingerprint,
                    self.fuel_consumed,
                    self.regression_report_hash,
                    self.pav_acceptance_hash,
                )
    }

    pub fn has_valid_fields(&self) -> bool {
        nonzero_hash(&self.package_hash)
            && nonzero_hash(&self.wasm_module_hash)
            && nonzero_hash(&self.wasmtime_artifact_hash)
            && self.wasmtime_ast_fingerprint > 0
            && self.fuel_consumed > 0
            && nonzero_hash(&self.regression_report_hash)
            && self.pav_acceptance_hash
                == skill_pav_acceptance_hash(
                    self.package_hash,
                    self.wasmtime_artifact_hash,
                    self.wasmtime_ast_fingerprint,
                    self.fuel_consumed,
                )
            && self.admission_hash
                == skill_admission_hash(
                    self.package_hash,
                    self.wasm_module_hash,
                    self.wasmtime_artifact_hash,
                    self.wasmtime_ast_fingerprint,
                    self.fuel_consumed,
                    self.regression_report_hash,
                    self.pav_acceptance_hash,
                )
    }
}

impl SkillRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn commit_admitted_skill(
        &mut self,
        manifest: &SkillPackageManifest,
        admission: &SkillAdmissionRecord,
        regression_report: &SkillRegressionReport,
    ) -> Result<AdmittedSkillRecord, SkillAdmissionError> {
        if self.records.contains_key(&manifest.skill_id) {
            return Err(SkillAdmissionError::DuplicateSkill);
        }
        if !admission.is_valid_for(manifest, regression_report) {
            return Err(SkillAdmissionError::AdmissionMismatch);
        }
        let registry_epoch = self.epoch.saturating_add(1);
        let registry_commit_hash = skill_registry_commit_hash(
            self.last_registry_commit_hash,
            registry_epoch,
            manifest.skill_id,
            manifest.package_hash,
            admission.admission_hash,
        );
        let record = AdmittedSkillRecord {
            skill_id: manifest.skill_id,
            package_hash: manifest.package_hash,
            admission_hash: admission.admission_hash,
            registry_epoch,
            registry_commit_hash,
        };
        if !record.is_valid_for(self.last_registry_commit_hash, manifest, admission) {
            return Err(SkillAdmissionError::InvalidAdmissionRecord);
        }
        self.records.insert(manifest.skill_id, record.clone());
        self.epoch = registry_epoch;
        self.last_registry_commit_hash = registry_commit_hash;
        Ok(record)
    }

    pub fn get(&self, skill_id: u128) -> Option<&AdmittedSkillRecord> {
        self.records.get(&skill_id)
    }

    pub fn activate_replay_sealed_skill(
        &mut self,
        skill_id: u128,
        handoff: &SkillAdmissionHandoffProof,
    ) -> Result<ActivatedSkillRecord, SkillAdmissionError> {
        if skill_id == 0 || skill_id != handoff.skill_id {
            return Err(SkillAdmissionError::InvalidActivationProof);
        }
        if self.active_records.contains_key(&skill_id) {
            return Err(SkillAdmissionError::DuplicateActivation);
        }
        let admitted = self
            .records
            .get(&skill_id)
            .ok_or(SkillAdmissionError::MissingAdmission)?;
        let record = ActivatedSkillRecord::from_handoff(admitted, handoff)?;
        self.active_records.insert(skill_id, record.clone());
        Ok(record)
    }

    pub fn get_active(&self, skill_id: u128) -> Option<&ActivatedSkillRecord> {
        self.active_records.get(&skill_id)
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn active_len(&self) -> usize {
        self.active_records.len()
    }

    #[allow(clippy::too_many_arguments)]
    pub fn execute_active_skill_with_replay<S: WasmExecutionSandbox>(
        &self,
        ledger: &mut RunEventLedger,
        sandbox: &S,
        skill_id: u128,
        handoff: &SkillAdmissionHandoffProof,
        admission: &SkillAdmissionRecord,
        call_id: u128,
        policy_trace_id: u128,
        ir: &TypedToolIR,
        proof: &PolicyProofTrace,
        wasm_bytes: &[u8],
        fuel_limit: u64,
    ) -> Result<SkillExecutionProof, SkillAdmissionError> {
        let active = self
            .get_active(skill_id)
            .ok_or(SkillAdmissionError::InactiveSkill)?;
        if !active.is_valid_for_handoff(handoff) {
            return Err(SkillAdmissionError::InvalidActivationProof);
        }
        if !admission.has_valid_fields()
            || admission.package_hash != active.package_hash
            || admission.admission_hash != active.admission_hash
        {
            return Err(SkillAdmissionError::AdmissionMismatch);
        }
        let expected_artifact_hash = ir
            .evidence_contract
            .expected_artifact_hash
            .ok_or(SkillAdmissionError::SkillExecutionPolicyMismatch)?;
        if expected_artifact_hash != admission.wasmtime_artifact_hash
            || expected_artifact_hash != admission.wasm_module_hash
        {
            return Err(SkillAdmissionError::SkillExecutionPolicyMismatch);
        }
        if crate::physical::blake3_digest(&canonicalize_payload(wasm_bytes))
            != admission.wasmtime_artifact_hash
        {
            return Err(SkillAdmissionError::SkillExecutionArtifactMismatch);
        }
        let receipt = ToolExecutionGateway::execute_wasm_with_replay(
            ledger,
            sandbox,
            call_id,
            policy_trace_id,
            ir,
            proof,
            wasm_bytes,
            fuel_limit,
        )
        .map_err(|_| SkillAdmissionError::SkillExecutionGatewayRejected)?;
        SkillExecutionProof::from_receipt(active, handoff, admission, ir, proof, &receipt)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_skill_execution_handoff(
        directory: impl AsRef<std::path::Path>,
        manifest: &RunEventSegmentManifest,
        execution: &SkillExecutionProof,
        ir: &TypedToolIR,
        proof: &PolicyProofTrace,
        checkpoint_id: u128,
        packet_id: u128,
        action_kind: NextActionKind,
        task_id: u128,
        evidence_contract_hash: [u8; 32],
    ) -> Result<SkillExecutionHandoffProof, SkillAdmissionError> {
        if !execution.is_valid_receipt_for(ir, proof) {
            return Err(SkillAdmissionError::SkillExecutionPolicyMismatch);
        }
        let tool_replay_proof = ToolExecutionGateway::seal_replay_checkpoint_and_next_action(
            directory,
            manifest,
            execution.receipt.clone(),
            checkpoint_id,
            packet_id,
            action_kind,
            task_id,
            evidence_contract_hash,
        )
        .map_err(|_| SkillAdmissionError::SkillExecutionReplayMismatch)?;
        SkillExecutionHandoffProof::from_tool_replay(execution, &tool_replay_proof)
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    pub fn last_registry_commit_hash(&self) -> [u8; 32] {
        self.last_registry_commit_hash
    }

    /// Attempt to autonomously improve a skill based on usage statistics.
    ///
    /// # Gates (in order)
    /// 1. `usage_count >= 10` — insufficient experience blocks improvement.
    /// 2. `regression_report.failed_count == 0` — any regression failure rejects.
    /// 3. The improved WASM bytes are BLAKE3-hashed and recorded.
    /// 4. A `SkillImprovementRecord` is built and cryptographically sealed.
    /// 5. A `LearningEventType::SkillImproved` event is appended to the
    ///    supplied `LearningLedger` for audit.
    ///
    /// # Returns
    /// `Ok(SkillImprovementRecord)` on success, or a `SkillAdmissionError`
    /// explaining which gate failed.
    pub fn improve_skill_from_usage(
        &mut self,
        skill_id: u128,
        usage_stats: &SkillUsageStats,
        improved_wasm_bytes: &[u8],
        regression_report: &SkillRegressionReport,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<SkillImprovementRecord, SkillAdmissionError> {
        // ── Commercial Gate: SelfImprovingSkills requires Pro/Team/Enterprise ──
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::SelfImprovingSkills)
                .map_err(SkillAdmissionError::LicenseRequired)?;
        }

        // Gate 1: minimum usage threshold
        if usage_stats.usage_count < 10 {
            return Err(SkillAdmissionError::InsufficientUsage);
        }

        // Gate 2: regression must be clean
        if regression_report.failed_count > 0 {
            return Err(SkillAdmissionError::RegressionFailed);
        }
        if regression_report.passed_count == 0 {
            return Err(SkillAdmissionError::RegressionFailed);
        }

        // Look up the existing skill record for audit trail linkage
        let existing = self
            .records
            .get(&skill_id)
            .ok_or(SkillAdmissionError::MissingAdmission)?;

        // Hash the improved WASM bytes
        let improvement_hash = {
            let mut h = domain_hasher(b"aegis-skill-improvement-wasm-v1");
            h.update(improved_wasm_bytes);
            *h.finalize().as_bytes()
        };

        let timestamp = now_millis();

        // Build the improvement record (cryptographically sealed)
        let record = SkillImprovementRecord::new(
            skill_id,
            existing.package_hash,
            existing.admission_hash,
            usage_stats.usage_count,
            usage_stats.success_rate,
            improvement_hash,
            regression_report.report_hash,
            timestamp,
        )
        .ok_or(SkillAdmissionError::InvalidAdmissionRecord)?;

        // Append to the learning audit trail
        learning_ledger.append(
            crate::learning::LearningEventType::SkillImproved {
                usage_count: usage_stats.usage_count,
                improvement_hash,
            },
            Some(skill_id),
            timestamp,
            1, // session_id: non-zero for autonomous learning tasks
        );

        Ok(record)
    }
}

impl AdmittedSkillRecord {
    pub fn is_valid_for(
        &self,
        previous_registry_commit_hash: [u8; 32],
        manifest: &SkillPackageManifest,
        admission: &SkillAdmissionRecord,
    ) -> bool {
        self.skill_id == manifest.skill_id
            && self.registry_epoch > 0
            && self.package_hash == manifest.package_hash
            && self.admission_hash == admission.admission_hash
            && self.registry_commit_hash
                == skill_registry_commit_hash(
                    previous_registry_commit_hash,
                    self.registry_epoch,
                    self.skill_id,
                    self.package_hash,
                    self.admission_hash,
                )
    }

    pub fn has_valid_fields(&self) -> bool {
        self.skill_id > 0
            && self.registry_epoch > 0
            && nonzero_hash(&self.package_hash)
            && nonzero_hash(&self.admission_hash)
            && nonzero_hash(&self.registry_commit_hash)
    }
}

impl ActivatedSkillRecord {
    pub fn from_handoff(
        admitted: &AdmittedSkillRecord,
        handoff: &SkillAdmissionHandoffProof,
    ) -> Result<Self, SkillAdmissionError> {
        if !handoff.has_valid_activation_fields()
            || !admitted.has_valid_fields()
            || handoff.skill_id != admitted.skill_id
            || handoff.package_hash != admitted.package_hash
            || handoff.admission_hash != admitted.admission_hash
            || handoff.registry_epoch != admitted.registry_epoch
            || handoff.registry_commit_hash != admitted.registry_commit_hash
        {
            return Err(SkillAdmissionError::InvalidActivationProof);
        }
        let record = Self {
            skill_id: admitted.skill_id,
            package_hash: admitted.package_hash,
            admission_hash: admitted.admission_hash,
            registry_epoch: admitted.registry_epoch,
            registry_commit_hash: admitted.registry_commit_hash,
            skill_admission_event_hash: handoff.skill_admission_event_hash,
            handoff_hash: handoff.handoff_hash,
            activation_hash: handoff.activation_hash,
        };
        if !record.is_valid_for(admitted, handoff) {
            return Err(SkillAdmissionError::InvalidActivationProof);
        }
        Ok(record)
    }

    pub fn is_valid_for(
        &self,
        admitted: &AdmittedSkillRecord,
        handoff: &SkillAdmissionHandoffProof,
    ) -> bool {
        self.has_valid_fields()
            && admitted.has_valid_fields()
            && handoff.has_valid_activation_fields()
            && self.skill_id == admitted.skill_id
            && self.skill_id == handoff.skill_id
            && self.package_hash == admitted.package_hash
            && self.package_hash == handoff.package_hash
            && self.admission_hash == admitted.admission_hash
            && self.admission_hash == handoff.admission_hash
            && self.registry_epoch == admitted.registry_epoch
            && self.registry_epoch == handoff.registry_epoch
            && self.registry_commit_hash == admitted.registry_commit_hash
            && self.registry_commit_hash == handoff.registry_commit_hash
            && self.skill_admission_event_hash == handoff.skill_admission_event_hash
            && self.handoff_hash == handoff.handoff_hash
            && self.activation_hash == handoff.activation_hash
    }

    pub fn is_valid_for_handoff(&self, handoff: &SkillAdmissionHandoffProof) -> bool {
        self.has_valid_fields()
            && handoff.has_valid_activation_fields()
            && self.skill_id == handoff.skill_id
            && self.package_hash == handoff.package_hash
            && self.admission_hash == handoff.admission_hash
            && self.registry_epoch == handoff.registry_epoch
            && self.registry_commit_hash == handoff.registry_commit_hash
            && self.skill_admission_event_hash == handoff.skill_admission_event_hash
            && self.handoff_hash == handoff.handoff_hash
            && self.activation_hash == handoff.activation_hash
    }

    pub fn has_valid_fields(&self) -> bool {
        self.skill_id > 0
            && self.registry_epoch > 0
            && nonzero_hash(&self.package_hash)
            && nonzero_hash(&self.admission_hash)
            && nonzero_hash(&self.registry_commit_hash)
            && nonzero_hash(&self.skill_admission_event_hash)
            && nonzero_hash(&self.handoff_hash)
            && nonzero_hash(&self.activation_hash)
    }
}

impl SkillExecutionProof {
    pub fn from_receipt(
        active: &ActivatedSkillRecord,
        handoff: &SkillAdmissionHandoffProof,
        admission: &SkillAdmissionRecord,
        ir: &TypedToolIR,
        policy_proof: &PolicyProofTrace,
        receipt: &ToolExecutionReceipt,
    ) -> Result<Self, SkillAdmissionError> {
        if !active.is_valid_for_handoff(handoff)
            || !admission.has_valid_fields()
            || admission.package_hash != active.package_hash
            || admission.admission_hash != active.admission_hash
            || !receipt.is_valid_for(ir, policy_proof)
            || receipt.tool_execution_evidence.evidence_hash == [0; 32]
            || receipt.artifact_evidence.is_none()
            || ir.tool_id != active.skill_id
        {
            return Err(SkillAdmissionError::SkillExecutionPolicyMismatch);
        }
        let artifact_evidence = receipt
            .artifact_evidence
            .as_ref()
            .ok_or(SkillAdmissionError::SkillExecutionArtifactMismatch)?;
        let expected_artifact_hash = ir
            .evidence_contract
            .expected_artifact_hash
            .ok_or(SkillAdmissionError::SkillExecutionPolicyMismatch)?;
        if expected_artifact_hash != receipt.tool_output_hash
            || expected_artifact_hash != artifact_evidence.artifact_hash
            || expected_artifact_hash != admission.wasmtime_artifact_hash
            || expected_artifact_hash != admission.wasm_module_hash
        {
            return Err(SkillAdmissionError::SkillExecutionArtifactMismatch);
        }
        let policy_proof_trace_hash = policy_proof.compute_hash();
        let artifact_evidence_hash = artifact_evidence.evidence_hash;
        let proof_hash = skill_execution_proof_hash(
            active.skill_id,
            active.package_hash,
            active.admission_hash,
            admission.wasm_module_hash,
            active.activation_hash,
            active.handoff_hash,
            ir.canonical_hash,
            policy_proof_trace_hash,
            receipt.request_event_hash,
            receipt.policy_event_hash,
            receipt.completion_event_hash,
            receipt.tool_execution_evidence.evidence_hash,
            artifact_evidence_hash,
        );
        Ok(Self {
            receipt: receipt.clone(),
            skill_id: active.skill_id,
            package_hash: active.package_hash,
            admission_hash: active.admission_hash,
            wasm_module_hash: admission.wasm_module_hash,
            activation_hash: active.activation_hash,
            handoff_hash: active.handoff_hash,
            typed_tool_ir_hash: ir.canonical_hash,
            policy_proof_trace_hash,
            request_event_hash: receipt.request_event_hash,
            policy_event_hash: receipt.policy_event_hash,
            completion_event_hash: receipt.completion_event_hash,
            tool_execution_evidence_hash: receipt.tool_execution_evidence.evidence_hash,
            artifact_evidence_hash,
            proof_hash,
        })
    }

    pub fn is_valid_for(
        &self,
        active: &ActivatedSkillRecord,
        handoff: &SkillAdmissionHandoffProof,
        admission: &SkillAdmissionRecord,
        ir: &TypedToolIR,
        policy_proof: &PolicyProofTrace,
    ) -> bool {
        let Some(artifact_evidence) = self.receipt.artifact_evidence.as_ref() else {
            return false;
        };
        active.is_valid_for_handoff(handoff)
            && admission.has_valid_fields()
            && admission.package_hash == active.package_hash
            && admission.admission_hash == active.admission_hash
            && self.receipt.is_valid_for(ir, policy_proof)
            && ir.tool_id == active.skill_id
            && self.skill_id == active.skill_id
            && self.package_hash == active.package_hash
            && self.admission_hash == active.admission_hash
            && self.wasm_module_hash == admission.wasm_module_hash
            && self.activation_hash == active.activation_hash
            && self.handoff_hash == active.handoff_hash
            && self.typed_tool_ir_hash == ir.canonical_hash
            && self.policy_proof_trace_hash == policy_proof.compute_hash()
            && self.request_event_hash == self.receipt.request_event_hash
            && self.policy_event_hash == self.receipt.policy_event_hash
            && self.completion_event_hash == self.receipt.completion_event_hash
            && self.tool_execution_evidence_hash
                == self.receipt.tool_execution_evidence.evidence_hash
            && self.artifact_evidence_hash == artifact_evidence.evidence_hash
            && ir.evidence_contract.expected_artifact_hash == Some(self.receipt.tool_output_hash)
            && ir.evidence_contract.expected_artifact_hash == Some(admission.wasmtime_artifact_hash)
            && self.proof_hash
                == skill_execution_proof_hash(
                    self.skill_id,
                    self.package_hash,
                    self.admission_hash,
                    self.wasm_module_hash,
                    self.activation_hash,
                    self.handoff_hash,
                    self.typed_tool_ir_hash,
                    self.policy_proof_trace_hash,
                    self.request_event_hash,
                    self.policy_event_hash,
                    self.completion_event_hash,
                    self.tool_execution_evidence_hash,
                    self.artifact_evidence_hash,
                )
            && nonzero_hash(&self.proof_hash)
    }

    pub fn is_valid_receipt_for(&self, ir: &TypedToolIR, policy_proof: &PolicyProofTrace) -> bool {
        let Some(artifact_evidence) = self.receipt.artifact_evidence.as_ref() else {
            return false;
        };
        self.receipt.is_valid_for(ir, policy_proof)
            && ir.tool_id == self.skill_id
            && self.typed_tool_ir_hash == ir.canonical_hash
            && self.policy_proof_trace_hash == policy_proof.compute_hash()
            && self.request_event_hash == self.receipt.request_event_hash
            && self.policy_event_hash == self.receipt.policy_event_hash
            && self.completion_event_hash == self.receipt.completion_event_hash
            && self.tool_execution_evidence_hash
                == self.receipt.tool_execution_evidence.evidence_hash
            && self.artifact_evidence_hash == artifact_evidence.evidence_hash
            && self.receipt.tool_output_hash == self.wasm_module_hash
            && self.receipt.tool_output_hash == artifact_evidence.artifact_hash
            && ir.evidence_contract.expected_artifact_hash == Some(self.wasm_module_hash)
            && self.proof_hash
                == skill_execution_proof_hash(
                    self.skill_id,
                    self.package_hash,
                    self.admission_hash,
                    self.wasm_module_hash,
                    self.activation_hash,
                    self.handoff_hash,
                    self.typed_tool_ir_hash,
                    self.policy_proof_trace_hash,
                    self.request_event_hash,
                    self.policy_event_hash,
                    self.completion_event_hash,
                    self.tool_execution_evidence_hash,
                    self.artifact_evidence_hash,
                )
            && nonzero_hash(&self.proof_hash)
    }
}

impl SkillExecutionHandoffProof {
    pub fn from_tool_replay(
        execution: &SkillExecutionProof,
        tool_replay_proof: &ToolExecutionReplayProof,
    ) -> Result<Self, SkillAdmissionError> {
        if !execution.proves_same_receipt(tool_replay_proof) {
            return Err(SkillAdmissionError::SkillExecutionReplayMismatch);
        }
        let replay_handoff_hash = skill_execution_handoff_hash(
            execution.skill_id,
            execution.package_hash,
            execution.admission_hash,
            execution.activation_hash,
            execution.handoff_hash,
            execution.proof_hash,
            tool_replay_proof.proof_hash,
            tool_replay_proof.checkpoint.checkpoint_hash,
            tool_replay_proof.next_action_packet.packet_hash,
            tool_replay_proof.next_action_packet.candidate_evidence_hash,
        );
        Ok(Self {
            skill_id: execution.skill_id,
            package_hash: execution.package_hash,
            admission_hash: execution.admission_hash,
            activation_hash: execution.activation_hash,
            handoff_hash: execution.handoff_hash,
            skill_execution_proof_hash: execution.proof_hash,
            tool_execution_replay_proof_hash: tool_replay_proof.proof_hash,
            checkpoint_hash: tool_replay_proof.checkpoint.checkpoint_hash,
            next_action_packet_hash: tool_replay_proof.next_action_packet.packet_hash,
            candidate_evidence_hash: tool_replay_proof.next_action_packet.candidate_evidence_hash,
            replay_handoff_hash,
        })
    }

    pub fn is_valid_for(
        &self,
        execution: &SkillExecutionProof,
        tool_replay_proof: &ToolExecutionReplayProof,
    ) -> bool {
        execution.proves_same_receipt(tool_replay_proof)
            && self.skill_id == execution.skill_id
            && self.package_hash == execution.package_hash
            && self.admission_hash == execution.admission_hash
            && self.activation_hash == execution.activation_hash
            && self.handoff_hash == execution.handoff_hash
            && self.skill_execution_proof_hash == execution.proof_hash
            && self.tool_execution_replay_proof_hash == tool_replay_proof.proof_hash
            && self.checkpoint_hash == tool_replay_proof.checkpoint.checkpoint_hash
            && self.next_action_packet_hash == tool_replay_proof.next_action_packet.packet_hash
            && self.candidate_evidence_hash
                == tool_replay_proof.next_action_packet.candidate_evidence_hash
            && self.candidate_evidence_hash == execution.physical_evidence_hash()
            && self.replay_handoff_hash
                == skill_execution_handoff_hash(
                    self.skill_id,
                    self.package_hash,
                    self.admission_hash,
                    self.activation_hash,
                    self.handoff_hash,
                    self.skill_execution_proof_hash,
                    self.tool_execution_replay_proof_hash,
                    self.checkpoint_hash,
                    self.next_action_packet_hash,
                    self.candidate_evidence_hash,
                )
            && nonzero_hash(&self.replay_handoff_hash)
    }
}

impl SkillExecutionProof {
    fn proves_same_receipt(&self, tool_replay_proof: &ToolExecutionReplayProof) -> bool {
        self.receipt == tool_replay_proof.receipt
            && self.request_event_hash == tool_replay_proof.receipt.request_event_hash
            && self.policy_event_hash == tool_replay_proof.receipt.policy_event_hash
            && self.completion_event_hash == tool_replay_proof.receipt.completion_event_hash
            && self.tool_execution_evidence_hash
                == tool_replay_proof
                    .receipt
                    .tool_execution_evidence
                    .evidence_hash
            && tool_replay_proof.next_action_packet.typed_tool_ir_hash == self.typed_tool_ir_hash
            && tool_replay_proof.next_action_packet.policy_proof_hash
                == self.policy_proof_trace_hash
            && tool_replay_proof.next_action_packet.candidate_evidence_hash
                == self.physical_evidence_hash()
    }

    fn physical_evidence_hash(&self) -> [u8; 32] {
        self.receipt.physical_evidence_hash
    }
}

fn now_millis() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

// hardening Phase 2A (2026-06-15): method form of the hash function, addresses
// `clippy::too_many_arguments` by allowing callers to compute the record hash from
// an existing record instance. Pure addition; the existing 9-arg
// `skill_improvement_record_hash(...)` free function is kept for backward compat.
impl SkillImprovementRecord {
    /// Compute the canonical record hash for the record's fields (excluding the
    /// `record_hash` field itself, which is the same value). Useful at construction
    /// sites where `Self::new` would validate inputs we don't want to validate (e.g.
    /// emitting a hash before the full record is decidable to commit).
    pub fn compute_record_hash(&self) -> [u8; 32] {
        let mut hasher = domain_hasher(b"aegis-skill-improvement-record-v1");
        hasher.update(&self.skill_id.to_le_bytes());
        hasher.update(&self.previous_package_hash);
        hasher.update(&self.previous_admission_hash);
        hasher.update(&self.usage_count.to_le_bytes());
        hasher.update(&self.success_rate.to_le_bytes());
        hasher.update(&self.improvement_hash);
        hasher.update(&self.regression_report_hash);
        hasher.update(&self.timestamp.to_le_bytes());
        *hasher.finalize().as_bytes()
    }
}

#[allow(clippy::too_many_arguments)]
pub fn skill_improvement_record_hash(
    skill_id: u128,
    previous_package_hash: [u8; 32],
    previous_admission_hash: [u8; 32],
    usage_count: u32,
    success_rate: f32,
    improvement_hash: [u8; 32],
    regression_report_hash: [u8; 32],
    timestamp: u64,
) -> [u8; 32] {
    // Note: this cannot use SkillImprovementRecord::compute_record_hash() because
    // that struct requires `record_hash` field to be set as well; we keep the
    // raw hasher path for the constructor flow.
    let mut hasher = domain_hasher(b"aegis-skill-improvement-record-v1");
    hasher.update(&skill_id.to_le_bytes());
    hasher.update(&previous_package_hash);
    hasher.update(&previous_admission_hash);
    hasher.update(&usage_count.to_le_bytes());
    hasher.update(&success_rate.to_le_bytes());
    hasher.update(&improvement_hash);
    hasher.update(&regression_report_hash);
    hasher.update(&timestamp.to_le_bytes());
    *hasher.finalize().as_bytes()
}

pub fn skill_package_hash(
    skill_id: u128,
    name_hash: [u8; 32],
    version_hash: [u8; 32],
    skill_md_hash: [u8; 32],
    wasm_module_hash: [u8; 32],
    policy_manifest_hash: [u8; 32],
    regression_suite_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-package-v1");
    hasher.update(&skill_id.to_le_bytes());
    hasher.update(&name_hash);
    hasher.update(&version_hash);
    hasher.update(&skill_md_hash);
    hasher.update(&wasm_module_hash);
    hasher.update(&policy_manifest_hash);
    hasher.update(&regression_suite_hash);
    *hasher.finalize().as_bytes()
}

pub fn skill_regression_case_hash(
    case_id: u64,
    input_hash: [u8; 32],
    expected_hash: [u8; 32],
    actual_hash: [u8; 32],
    passed: bool,
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-regression-case-v1");
    hasher.update(&case_id.to_le_bytes());
    hasher.update(&input_hash);
    hasher.update(&expected_hash);
    hasher.update(&actual_hash);
    hasher.update(&[passed as u8]);
    *hasher.finalize().as_bytes()
}

pub fn skill_regression_suite_hash(cases: &[SkillRegressionCase]) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-regression-suite-v1");
    hasher.update(&(cases.len() as u64).to_le_bytes());
    for case in cases {
        hasher.update(&case.case_hash);
    }
    *hasher.finalize().as_bytes()
}

pub fn skill_regression_report_hash(
    case_count: u32,
    passed_count: u32,
    failed_count: u32,
    case_list_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-regression-report-v1");
    hasher.update(&case_count.to_le_bytes());
    hasher.update(&passed_count.to_le_bytes());
    hasher.update(&failed_count.to_le_bytes());
    hasher.update(&case_list_hash);
    *hasher.finalize().as_bytes()
}

pub fn skill_pav_acceptance_hash(
    package_hash: [u8; 32],
    wasmtime_artifact_hash: [u8; 32],
    ast_fingerprint: u64,
    fuel_consumed: u64,
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-pav-acceptance-v1");
    hasher.update(&package_hash);
    hasher.update(&wasmtime_artifact_hash);
    hasher.update(&ast_fingerprint.to_le_bytes());
    hasher.update(&fuel_consumed.to_le_bytes());
    *hasher.finalize().as_bytes()
}

pub fn skill_admission_hash(
    package_hash: [u8; 32],
    wasm_module_hash: [u8; 32],
    wasmtime_artifact_hash: [u8; 32],
    wasmtime_ast_fingerprint: u64,
    fuel_consumed: u64,
    regression_report_hash: [u8; 32],
    pav_acceptance_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-admission-record-v1");
    hasher.update(&package_hash);
    hasher.update(&wasm_module_hash);
    hasher.update(&wasmtime_artifact_hash);
    hasher.update(&wasmtime_ast_fingerprint.to_le_bytes());
    hasher.update(&fuel_consumed.to_le_bytes());
    hasher.update(&regression_report_hash);
    hasher.update(&pav_acceptance_hash);
    *hasher.finalize().as_bytes()
}

pub fn skill_registry_commit_hash(
    previous_registry_commit_hash: [u8; 32],
    registry_epoch: u64,
    skill_id: u128,
    package_hash: [u8; 32],
    admission_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-registry-commit-v1");
    hasher.update(&previous_registry_commit_hash);
    hasher.update(&registry_epoch.to_le_bytes());
    hasher.update(&skill_id.to_le_bytes());
    hasher.update(&package_hash);
    hasher.update(&admission_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn skill_execution_proof_hash(
    skill_id: u128,
    package_hash: [u8; 32],
    admission_hash: [u8; 32],
    wasm_module_hash: [u8; 32],
    activation_hash: [u8; 32],
    handoff_hash: [u8; 32],
    typed_tool_ir_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    request_event_hash: [u8; 32],
    policy_event_hash: [u8; 32],
    completion_event_hash: [u8; 32],
    tool_execution_evidence_hash: [u8; 32],
    artifact_evidence_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-execution-proof-v1");
    hasher.update(&skill_id.to_le_bytes());
    hasher.update(&package_hash);
    hasher.update(&admission_hash);
    hasher.update(&wasm_module_hash);
    hasher.update(&activation_hash);
    hasher.update(&handoff_hash);
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&policy_proof_trace_hash);
    hasher.update(&request_event_hash);
    hasher.update(&policy_event_hash);
    hasher.update(&completion_event_hash);
    hasher.update(&tool_execution_evidence_hash);
    hasher.update(&artifact_evidence_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn skill_execution_handoff_hash(
    skill_id: u128,
    package_hash: [u8; 32],
    admission_hash: [u8; 32],
    activation_hash: [u8; 32],
    handoff_hash: [u8; 32],
    skill_execution_proof_hash: [u8; 32],
    tool_execution_replay_proof_hash: [u8; 32],
    checkpoint_hash: [u8; 32],
    next_action_packet_hash: [u8; 32],
    candidate_evidence_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-skill-execution-handoff-v1");
    hasher.update(&skill_id.to_le_bytes());
    hasher.update(&package_hash);
    hasher.update(&admission_hash);
    hasher.update(&activation_hash);
    hasher.update(&handoff_hash);
    hasher.update(&skill_execution_proof_hash);
    hasher.update(&tool_execution_replay_proof_hash);
    hasher.update(&checkpoint_hash);
    hasher.update(&next_action_packet_hash);
    hasher.update(&candidate_evidence_hash);
    *hasher.finalize().as_bytes()
}

fn domain_hasher(domain: &[u8]) -> Hasher {
    let mut hasher = Hasher::new();
    hasher.update(domain);
    hasher.update(&[0]);
    hasher
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}
