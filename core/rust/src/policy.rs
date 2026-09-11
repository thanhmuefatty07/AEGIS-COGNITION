use blake3::Hasher;
use serde::{Deserialize, Serialize};

pub type ToolId = u128;
pub type OperationId = u128;
pub type ReviewId = u128;
pub type ApprovalId = u128;
pub type BenchId = u128;
pub type TaskId = u128;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapabilityClass {
    LocalRead,
    LocalWrite,
    ExternalRead,
    ExternalWrite,
    Browser,
    Computer,
    Inference,
    Retrieval,
    FinancialLegal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SideEffectClass {
    None,
    LocalReversible,
    ExternalRead,
    ExternalWrite,
    Destructive,
    FinancialLegal,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub enum RiskClass {
    R0,
    R1,
    R2,
    R3,
    R4,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PolicyDecision {
    Allow,
    RequireApproval,
    RequireStagingSandbox,
    HardBlock,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StagingEvidenceKind {
    SandboxExecution,
    TestnetExecution,
    IsolatedSnapshotExecution,
    IsolatedBrowserSession,
    LlmGeneratedText,
    MockLog,
}

pub const POLICY_RULE_INVALID_IR: u128 = 1;
pub const POLICY_RULE_INVALID_FACTS: u128 = 2;
pub const POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL: u128 = 3;
pub const POLICY_RULE_STAGING_REQUIRED: u128 = 4;
pub const POLICY_RULE_APPROVAL_REQUIRED: u128 = 5;
pub const POLICY_RULE_ALLOW: u128 = 6;

const POLICY_DATALOG_ATOM_FACTS_VALID: u64 = 1 << 0;
const POLICY_DATALOG_ATOM_FACTS_INVALID: u64 = 1 << 1;
const POLICY_DATALOG_ATOM_IR_VALID: u64 = 1 << 2;
const POLICY_DATALOG_ATOM_IR_INVALID: u64 = 1 << 3;
const POLICY_DATALOG_ATOM_R4: u64 = 1 << 4;
const POLICY_DATALOG_ATOM_CONTRACT_REQUIRES_STAGING: u64 = 1 << 5;
const POLICY_DATALOG_ATOM_CONTRACT_REQUIRES_APPROVAL: u64 = 1 << 6;
const POLICY_DATALOG_ATOM_APPROVAL_PRESENT: u64 = 1 << 7;
const POLICY_DATALOG_ATOM_STAGING_PRESENT: u64 = 1 << 8;
const POLICY_DATALOG_ATOM_HITL_PRESENT: u64 = 1 << 9;
const POLICY_DATALOG_ATOM_MISSING_STAGING_AND_HITL: u64 = 1 << 10;
const POLICY_DATALOG_ATOM_R4_WITHOUT_STAGING_OR_HITL: u64 = 1 << 11;
const POLICY_DATALOG_ATOM_STAGING_REQUIRED: u64 = 1 << 12;
const POLICY_DATALOG_ATOM_APPROVAL_REQUIRED: u64 = 1 << 13;
const POLICY_DATALOG_ATOM_ALLOW: u64 = 1 << 14;
const POLICY_DATALOG_ATOM_HARD_BLOCK: u64 = 1 << 15;

const POLICY_DATALOG_RULE_MISSING_STAGING_AND_HITL: u64 = 1 << 0;
const POLICY_DATALOG_RULE_R4_WITHOUT_STAGING_OR_HITL: u64 = 1 << 1;
const POLICY_DATALOG_RULE_STAGING_REQUIRED: u64 = 1 << 2;
const POLICY_DATALOG_RULE_APPROVAL_REQUIRED: u64 = 1 << 3;
const POLICY_DATALOG_RULE_HARD_BLOCK_INVALID_FACTS: u64 = 1 << 4;
const POLICY_DATALOG_RULE_HARD_BLOCK_INVALID_IR: u64 = 1 << 5;
const POLICY_DATALOG_RULE_HARD_BLOCK_R4: u64 = 1 << 6;
const POLICY_DATALOG_RULE_ALLOW: u64 = 1 << 7;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EvidenceContract {
    pub requires_physical_witness: bool,
    pub requires_approval: bool,
    pub requires_staging: bool,
    pub expected_artifact_hash: Option<[u8; 32]>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TypedToolIR {
    pub tool_id: ToolId,
    pub operation_id: OperationId,
    pub capability: CapabilityClass,
    pub side_effect: SideEffectClass,
    pub credential_scope_hash: [u8; 32],
    pub risk_class: RiskClass,
    pub precondition_hash: [u8; 32],
    pub effect_hash: [u8; 32],
    pub evidence_contract: EvidenceContract,
    pub approval_scope_hash: Option<[u8; 32]>,
    pub canonical_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PolicyProofTrace {
    pub policy_version_hash: [u8; 32],
    pub input_ir_hash: [u8; 32],
    pub facts_hash: [u8; 32],
    pub decision: PolicyDecision,
    pub matched_rule_ids: Vec<u128>,
    pub counterexample_ref_hash: Option<[u8; 32]>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PolicyFacts {
    pub policy_version_hash: [u8; 32],
    pub facts_hash: [u8; 32],
    pub has_valid_approval: bool,
    pub has_staging_environment: bool,
    pub has_hitl_signature: bool,
    pub staging_proof_ref_hash: Option<[u8; 32]>,
    pub staging_evidence_kind: Option<StagingEvidenceKind>,
    pub hitl_signature_ref_hash: Option<[u8; 32]>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PolicyDatalogClosureProof {
    pub policy_version_hash: [u8; 32],
    pub input_ir_hash: [u8; 32],
    pub facts_hash: [u8; 32],
    pub initial_atom_bits: u64,
    pub derived_atom_bits: u64,
    pub fired_rule_bits: u64,
    pub decision: PolicyDecision,
    pub matched_rule_ids: Vec<u128>,
    pub counterexample_ref_hash: Option<[u8; 32]>,
    pub iteration_count: u8,
    pub closure_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct DeterministicPolicyKernel;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReviewPacket {
    pub review_id: ReviewId,
    pub typed_tool_ir_hash: [u8; 32],
    pub policy_proof_trace_hash: [u8; 32],
    pub risk_class: RiskClass,
    pub side_effect: SideEffectClass,
    pub resource_scope_hash: [u8; 32],
    pub expected_effect_hash: [u8; 32],
    pub evidence_ref_hashes: Vec<[u8; 32]>,
    pub staging_proof_ref_hash: Option<[u8; 32]>,
    pub staging_proof_kind: Option<StagingEvidenceKind>,
    pub hitl_override_ref_hash: Option<[u8; 32]>,
    pub reversibility_plan_hash: Option<[u8; 32]>,
    pub redaction_policy_hash: [u8; 32],
    pub review_packet_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SignedApprovalToken {
    pub approval_id: ApprovalId,
    pub approver_key_hash: [u8; 32],
    pub review_packet_hash: [u8; 32],
    pub typed_tool_ir_hash: [u8; 32],
    pub max_risk_class: RiskClass,
    pub resource_scope_hash: [u8; 32],
    pub max_spend_minor_units: Option<u64>,
    pub expires_at_ms: u64,
    pub challenge_nonce: [u8; 32],
    pub signature: [u8; 64],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalScopeReplayDecision {
    Covered,
    MissingOrInvalid,
    Expired,
    ScopeMismatch,
    SpendLimitExceeded,
    PolicyWindowMismatch,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalScopeReplay {
    pub decision: ApprovalScopeReplayDecision,
    pub approval_id: ApprovalId,
    pub review_packet_hash: [u8; 32],
    pub typed_tool_ir_hash: [u8; 32],
    pub resource_scope_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub requested_spend_minor_units: Option<u64>,
    pub replay_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OperatorReviewArtifact {
    pub review_packet_hash: [u8; 32],
    pub policy_proof_trace_hash: [u8; 32],
    pub approval_scope_replay_hash: Option<[u8; 32]>,
    pub policy_window_hash: [u8; 32],
    pub replay_event_hashes: Vec<[u8; 32]>,
    pub redaction_policy_hash: [u8; 32],
    pub helper_text_hash: Option<[u8; 32]>,
    pub signing_target_hash: [u8; 32],
    pub artifact_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DualApprovalProof {
    pub first_approval_id: ApprovalId,
    pub second_approval_id: ApprovalId,
    pub first_approver_key_hash: [u8; 32],
    pub second_approver_key_hash: [u8; 32],
    pub signing_target_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TelemetryEventKind {
    PolicyDecision,
    WitnessVerification,
    SandboxTrap,
    ApprovalDecision,
    ReplayRecovery,
    Benchmark,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TelemetryEnvelope {
    pub run_id: u128,
    pub trace_id: [u8; 16],
    pub span_id: [u8; 8],
    pub event_kind: TelemetryEventKind,
    pub risk_class: RiskClass,
    pub evidence_ref_hash: Option<[u8; 32]>,
    pub policy_window_hash: Option<[u8; 32]>,
    pub redaction_policy_hash: [u8; 32],
    pub monotonic_time_ns: u64,
    pub wall_time_ms: u64,
    pub payload_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct HarnessBenchScorecard {
    pub bench_id: BenchId,
    pub task_id: TaskId,
    pub success: bool,
    pub physical_witness_hash: [u8; 32],
    pub replay_hash: [u8; 32],
    pub policy_violation_count: u32,
    pub hard_block_count: u32,
    pub approval_required_count: u32,
    pub witness_coverage_ppm: u32,
    pub crash_recovery_passed: bool,
    pub p50_wall_ms: u64,
    pub p99_wall_ms: u64,
    pub token_count: u64,
    pub tool_call_count: u32,
}

impl EvidenceContract {
    pub fn physical() -> Self {
        Self {
            requires_physical_witness: true,
            requires_approval: false,
            requires_staging: false,
            expected_artifact_hash: None,
        }
    }

    pub fn r4_staged() -> Self {
        Self {
            requires_physical_witness: true,
            requires_approval: true,
            requires_staging: true,
            expected_artifact_hash: None,
        }
    }
}

impl TypedToolIR {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        tool_id: ToolId,
        operation_id: OperationId,
        capability: CapabilityClass,
        side_effect: SideEffectClass,
        credential_scope_hash: [u8; 32],
        risk_class: RiskClass,
        precondition_hash: [u8; 32],
        effect_hash: [u8; 32],
        evidence_contract: EvidenceContract,
        approval_scope_hash: Option<[u8; 32]>,
    ) -> Self {
        let canonical_hash = typed_tool_ir_hash(
            tool_id,
            operation_id,
            capability,
            side_effect,
            credential_scope_hash,
            risk_class,
            precondition_hash,
            effect_hash,
            &evidence_contract,
            approval_scope_hash,
        );
        Self {
            tool_id,
            operation_id,
            capability,
            side_effect,
            credential_scope_hash,
            risk_class,
            precondition_hash,
            effect_hash,
            evidence_contract,
            approval_scope_hash,
            canonical_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.tool_id > 0
            && self.operation_id > 0
            && nonzero_hash(&self.credential_scope_hash)
            && nonzero_hash(&self.precondition_hash)
            && nonzero_hash(&self.effect_hash)
            && self.canonical_hash
                == typed_tool_ir_hash(
                    self.tool_id,
                    self.operation_id,
                    self.capability,
                    self.side_effect,
                    self.credential_scope_hash,
                    self.risk_class,
                    self.precondition_hash,
                    self.effect_hash,
                    &self.evidence_contract,
                    self.approval_scope_hash,
                )
            && (self.risk_class < RiskClass::R3 || self.evidence_contract.requires_approval)
            && (self.risk_class < RiskClass::R4 || self.evidence_contract.requires_staging)
            && self.risk_class >= minimum_required_risk(self.capability, self.side_effect)
    }
}

impl PolicyProofTrace {
    pub fn is_replay_consistent_with(&self, typed_tool_ir: &TypedToolIR) -> bool {
        typed_tool_ir.is_valid()
            && self.input_ir_hash == typed_tool_ir.canonical_hash
            && nonzero_hash(&self.policy_version_hash)
            && nonzero_hash(&self.facts_hash)
    }

    pub fn is_replay_consistent_with_hash(&self, typed_tool_ir_hash: [u8; 32]) -> bool {
        self.input_ir_hash == typed_tool_ir_hash
            && nonzero_hash(&self.policy_version_hash)
            && nonzero_hash(&self.facts_hash)
            && nonzero_hash(&self.compute_hash())
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = Hasher::new();
        hasher.update(b"policy-proof-trace-v1");
        hasher.update(&self.policy_version_hash);
        hasher.update(&self.input_ir_hash);
        hasher.update(&self.facts_hash);
        update_u8(&mut hasher, self.decision as u8);
        update_u64(&mut hasher, self.matched_rule_ids.len() as u64);
        for rule_id in &self.matched_rule_ids {
            update_u128(&mut hasher, *rule_id);
        }
        update_optional_hash(&mut hasher, self.counterexample_ref_hash);
        *hasher.finalize().as_bytes()
    }

    pub fn binds_datalog_closure(&self, closure: &PolicyDatalogClosureProof) -> bool {
        closure.is_valid_hash()
            && self.policy_version_hash == closure.policy_version_hash
            && self.input_ir_hash == closure.input_ir_hash
            && self.facts_hash == closure.facts_hash
            && self.decision == closure.decision
            && self.matched_rule_ids == closure.matched_rule_ids
            && self.counterexample_ref_hash == closure.counterexample_ref_hash
    }
}

impl PolicyDatalogClosureProof {
    pub fn derive(ir: &TypedToolIR, facts: &PolicyFacts) -> Self {
        let facts_are_valid = facts.is_valid();
        let ir_is_valid = ir.is_valid();
        let mut atoms = 0_u64;
        insert_atom(
            &mut atoms,
            if facts_are_valid {
                POLICY_DATALOG_ATOM_FACTS_VALID
            } else {
                POLICY_DATALOG_ATOM_FACTS_INVALID
            },
        );
        insert_atom(
            &mut atoms,
            if ir_is_valid {
                POLICY_DATALOG_ATOM_IR_VALID
            } else {
                POLICY_DATALOG_ATOM_IR_INVALID
            },
        );
        if ir.risk_class == RiskClass::R4 {
            insert_atom(&mut atoms, POLICY_DATALOG_ATOM_R4);
        }
        if ir.evidence_contract.requires_staging {
            insert_atom(&mut atoms, POLICY_DATALOG_ATOM_CONTRACT_REQUIRES_STAGING);
        }
        if ir.evidence_contract.requires_approval {
            insert_atom(&mut atoms, POLICY_DATALOG_ATOM_CONTRACT_REQUIRES_APPROVAL);
        }
        if facts.has_valid_approval {
            insert_atom(&mut atoms, POLICY_DATALOG_ATOM_APPROVAL_PRESENT);
        }
        if facts.has_staging_environment {
            insert_atom(&mut atoms, POLICY_DATALOG_ATOM_STAGING_PRESENT);
        }
        if facts.has_hitl_signature {
            insert_atom(&mut atoms, POLICY_DATALOG_ATOM_HITL_PRESENT);
        }

        let initial_atom_bits = atoms;
        let mut fired_rule_bits = 0_u64;
        let mut iteration_count = 0_u8;
        for iteration in 1..=4 {
            let before = atoms;
            if !has_atom(atoms, POLICY_DATALOG_ATOM_STAGING_PRESENT)
                && !has_atom(atoms, POLICY_DATALOG_ATOM_HITL_PRESENT)
            {
                fire_rule(
                    &mut atoms,
                    &mut fired_rule_bits,
                    POLICY_DATALOG_ATOM_MISSING_STAGING_AND_HITL,
                    POLICY_DATALOG_RULE_MISSING_STAGING_AND_HITL,
                );
            }
            if has_atom(atoms, POLICY_DATALOG_ATOM_R4)
                && has_atom(atoms, POLICY_DATALOG_ATOM_MISSING_STAGING_AND_HITL)
            {
                fire_rule(
                    &mut atoms,
                    &mut fired_rule_bits,
                    POLICY_DATALOG_ATOM_R4_WITHOUT_STAGING_OR_HITL,
                    POLICY_DATALOG_RULE_R4_WITHOUT_STAGING_OR_HITL,
                );
            }
            if has_atom(atoms, POLICY_DATALOG_ATOM_CONTRACT_REQUIRES_STAGING)
                && has_atom(atoms, POLICY_DATALOG_ATOM_MISSING_STAGING_AND_HITL)
            {
                fire_rule(
                    &mut atoms,
                    &mut fired_rule_bits,
                    POLICY_DATALOG_ATOM_STAGING_REQUIRED,
                    POLICY_DATALOG_RULE_STAGING_REQUIRED,
                );
            }
            if has_atom(atoms, POLICY_DATALOG_ATOM_CONTRACT_REQUIRES_APPROVAL)
                && !has_atom(atoms, POLICY_DATALOG_ATOM_APPROVAL_PRESENT)
            {
                fire_rule(
                    &mut atoms,
                    &mut fired_rule_bits,
                    POLICY_DATALOG_ATOM_APPROVAL_REQUIRED,
                    POLICY_DATALOG_RULE_APPROVAL_REQUIRED,
                );
            }
            iteration_count = iteration;
            if atoms == before {
                break;
            }
        }

        let (decision, matched_rule_ids, counterexample_ref_hash) =
            policy_decision_from_datalog_atoms(atoms);
        match decision {
            PolicyDecision::HardBlock => insert_atom(&mut atoms, POLICY_DATALOG_ATOM_HARD_BLOCK),
            PolicyDecision::Allow => {
                insert_atom(&mut atoms, POLICY_DATALOG_ATOM_ALLOW);
                fired_rule_bits |= POLICY_DATALOG_RULE_ALLOW;
            }
            PolicyDecision::RequireStagingSandbox | PolicyDecision::RequireApproval => {}
        }
        match matched_rule_ids.first().copied() {
            Some(POLICY_RULE_INVALID_FACTS) => {
                fired_rule_bits |= POLICY_DATALOG_RULE_HARD_BLOCK_INVALID_FACTS;
            }
            Some(POLICY_RULE_INVALID_IR) => {
                fired_rule_bits |= POLICY_DATALOG_RULE_HARD_BLOCK_INVALID_IR;
            }
            Some(POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL) => {
                fired_rule_bits |= POLICY_DATALOG_RULE_HARD_BLOCK_R4;
            }
            _ => {}
        }

        let closure_hash = policy_datalog_closure_hash(
            facts.policy_version_hash,
            ir.canonical_hash,
            facts.facts_hash,
            initial_atom_bits,
            atoms,
            fired_rule_bits,
            decision,
            &matched_rule_ids,
            counterexample_ref_hash,
            iteration_count,
        );
        Self {
            policy_version_hash: facts.policy_version_hash,
            input_ir_hash: ir.canonical_hash,
            facts_hash: facts.facts_hash,
            initial_atom_bits,
            derived_atom_bits: atoms,
            fired_rule_bits,
            decision,
            matched_rule_ids,
            counterexample_ref_hash,
            iteration_count,
            closure_hash,
        }
    }

    pub fn is_valid_for(&self, ir: &TypedToolIR, facts: &PolicyFacts) -> bool {
        self == &Self::derive(ir, facts)
    }

    pub fn is_valid_hash(&self) -> bool {
        nonzero_hash(&self.policy_version_hash)
            && nonzero_hash(&self.input_ir_hash)
            && nonzero_hash(&self.facts_hash)
            && nonzero_hash(&self.closure_hash)
            && self.iteration_count > 0
            && self.closure_hash
                == policy_datalog_closure_hash(
                    self.policy_version_hash,
                    self.input_ir_hash,
                    self.facts_hash,
                    self.initial_atom_bits,
                    self.derived_atom_bits,
                    self.fired_rule_bits,
                    self.decision,
                    &self.matched_rule_ids,
                    self.counterexample_ref_hash,
                    self.iteration_count,
                )
    }

    pub fn has_atom(&self, atom: u64) -> bool {
        has_atom(self.derived_atom_bits, atom)
    }

    pub fn proves_r4_without_staging_or_hitl_hard_block(&self) -> bool {
        self.decision == PolicyDecision::HardBlock
            && self.matched_rule_ids.as_slice() == [POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL]
            && self.has_atom(POLICY_DATALOG_ATOM_R4_WITHOUT_STAGING_OR_HITL)
            && self.has_atom(POLICY_DATALOG_ATOM_HARD_BLOCK)
    }

    pub fn proves_approval_requirement(&self) -> bool {
        self.decision == PolicyDecision::RequireApproval
            && self.matched_rule_ids.as_slice() == [POLICY_RULE_APPROVAL_REQUIRED]
            && self.has_atom(POLICY_DATALOG_ATOM_APPROVAL_REQUIRED)
    }

    pub fn proves_allow(&self) -> bool {
        self.decision == PolicyDecision::Allow
            && self.matched_rule_ids.as_slice() == [POLICY_RULE_ALLOW]
            && self.has_atom(POLICY_DATALOG_ATOM_ALLOW)
    }
}

impl PolicyFacts {
    pub fn new(
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
        has_staging_environment: bool,
        has_hitl_signature: bool,
    ) -> Self {
        Self::new_internal(
            policy_version_hash,
            has_valid_approval,
            has_staging_environment,
            has_hitl_signature,
            None,
            None,
            None,
        )
    }

    pub fn with_staging_evidence(
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
        staging_proof_ref_hash: [u8; 32],
        staging_evidence_kind: StagingEvidenceKind,
    ) -> Self {
        Self::new_internal(
            policy_version_hash,
            has_valid_approval,
            true,
            false,
            Some(staging_proof_ref_hash),
            Some(staging_evidence_kind),
            None,
        )
    }

    pub fn with_hitl_signature(
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
        hitl_signature_ref_hash: [u8; 32],
    ) -> Self {
        Self::new_internal(
            policy_version_hash,
            has_valid_approval,
            false,
            true,
            None,
            None,
            Some(hitl_signature_ref_hash),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn new_internal(
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
        has_staging_environment: bool,
        has_hitl_signature: bool,
        staging_proof_ref_hash: Option<[u8; 32]>,
        staging_evidence_kind: Option<StagingEvidenceKind>,
        hitl_signature_ref_hash: Option<[u8; 32]>,
    ) -> Self {
        let facts_hash = policy_facts_hash(
            policy_version_hash,
            has_valid_approval,
            has_staging_environment,
            has_hitl_signature,
            staging_proof_ref_hash,
            staging_evidence_kind,
            hitl_signature_ref_hash,
        );
        Self {
            policy_version_hash,
            facts_hash,
            has_valid_approval,
            has_staging_environment,
            has_hitl_signature,
            staging_proof_ref_hash,
            staging_evidence_kind,
            hitl_signature_ref_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        let staging_fact_is_valid = match (
            self.has_staging_environment,
            self.staging_proof_ref_hash,
            self.staging_evidence_kind,
        ) {
            (true, Some(hash), Some(kind)) => nonzero_hash(&hash) && kind.is_physical(),
            (false, None, None) => true,
            _ => false,
        };
        let hitl_fact_is_valid = match (self.has_hitl_signature, self.hitl_signature_ref_hash) {
            (true, Some(hash)) => nonzero_hash(&hash),
            (false, None) => true,
            _ => false,
        };
        nonzero_hash(&self.policy_version_hash)
            && staging_fact_is_valid
            && hitl_fact_is_valid
            && self.facts_hash
                == policy_facts_hash(
                    self.policy_version_hash,
                    self.has_valid_approval,
                    self.has_staging_environment,
                    self.has_hitl_signature,
                    self.staging_proof_ref_hash,
                    self.staging_evidence_kind,
                    self.hitl_signature_ref_hash,
                )
    }
}

impl DeterministicPolicyKernel {
    pub fn evaluate(&self, ir: &TypedToolIR, facts: &PolicyFacts) -> PolicyProofTrace {
        self.evaluate_with_datalog(ir, facts).0
    }

    pub fn evaluate_with_datalog(
        &self,
        ir: &TypedToolIR,
        facts: &PolicyFacts,
    ) -> (PolicyProofTrace, PolicyDatalogClosureProof) {
        let closure = PolicyDatalogClosureProof::derive(ir, facts);
        let trace = PolicyProofTrace {
            policy_version_hash: facts.policy_version_hash,
            input_ir_hash: ir.canonical_hash,
            facts_hash: facts.facts_hash,
            decision: closure.decision,
            matched_rule_ids: closure.matched_rule_ids.clone(),
            counterexample_ref_hash: closure.counterexample_ref_hash,
        };
        (trace, closure)
    }
}

impl StagingEvidenceKind {
    pub const fn is_physical(self) -> bool {
        matches!(
            self,
            Self::SandboxExecution
                | Self::TestnetExecution
                | Self::IsolatedSnapshotExecution
                | Self::IsolatedBrowserSession
        )
    }
}

impl ReviewPacket {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        review_id: ReviewId,
        typed_tool_ir_hash: [u8; 32],
        policy_proof_trace_hash: [u8; 32],
        risk_class: RiskClass,
        side_effect: SideEffectClass,
        resource_scope_hash: [u8; 32],
        expected_effect_hash: [u8; 32],
        evidence_ref_hashes: Vec<[u8; 32]>,
        staging_proof_ref_hash: Option<[u8; 32]>,
        hitl_override_ref_hash: Option<[u8; 32]>,
        reversibility_plan_hash: Option<[u8; 32]>,
        redaction_policy_hash: [u8; 32],
    ) -> Self {
        Self::new_internal(
            review_id,
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            risk_class,
            side_effect,
            resource_scope_hash,
            expected_effect_hash,
            evidence_ref_hashes,
            staging_proof_ref_hash,
            None,
            hitl_override_ref_hash,
            reversibility_plan_hash,
            redaction_policy_hash,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn new_with_staging_evidence_kind(
        review_id: ReviewId,
        typed_tool_ir_hash: [u8; 32],
        policy_proof_trace_hash: [u8; 32],
        risk_class: RiskClass,
        side_effect: SideEffectClass,
        resource_scope_hash: [u8; 32],
        expected_effect_hash: [u8; 32],
        evidence_ref_hashes: Vec<[u8; 32]>,
        staging_proof_ref_hash: Option<[u8; 32]>,
        staging_proof_kind: Option<StagingEvidenceKind>,
        hitl_override_ref_hash: Option<[u8; 32]>,
        reversibility_plan_hash: Option<[u8; 32]>,
        redaction_policy_hash: [u8; 32],
    ) -> Self {
        Self::new_internal(
            review_id,
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            risk_class,
            side_effect,
            resource_scope_hash,
            expected_effect_hash,
            evidence_ref_hashes,
            staging_proof_ref_hash,
            staging_proof_kind,
            hitl_override_ref_hash,
            reversibility_plan_hash,
            redaction_policy_hash,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn new_internal(
        review_id: ReviewId,
        typed_tool_ir_hash: [u8; 32],
        policy_proof_trace_hash: [u8; 32],
        risk_class: RiskClass,
        side_effect: SideEffectClass,
        resource_scope_hash: [u8; 32],
        expected_effect_hash: [u8; 32],
        evidence_ref_hashes: Vec<[u8; 32]>,
        staging_proof_ref_hash: Option<[u8; 32]>,
        staging_proof_kind: Option<StagingEvidenceKind>,
        hitl_override_ref_hash: Option<[u8; 32]>,
        reversibility_plan_hash: Option<[u8; 32]>,
        redaction_policy_hash: [u8; 32],
    ) -> Self {
        let review_packet_hash = review_packet_hash(
            review_id,
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            risk_class,
            side_effect,
            resource_scope_hash,
            expected_effect_hash,
            &evidence_ref_hashes,
            staging_proof_ref_hash,
            staging_proof_kind,
            hitl_override_ref_hash,
            reversibility_plan_hash,
            redaction_policy_hash,
        );
        Self {
            review_id,
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            risk_class,
            side_effect,
            resource_scope_hash,
            expected_effect_hash,
            evidence_ref_hashes,
            staging_proof_ref_hash,
            staging_proof_kind,
            hitl_override_ref_hash,
            reversibility_plan_hash,
            redaction_policy_hash,
            review_packet_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        let staging_proof_is_physical = match (self.staging_proof_ref_hash, self.staging_proof_kind)
        {
            (Some(hash), Some(kind)) => nonzero_hash(&hash) && kind.is_physical(),
            (None, None) => true,
            _ => false,
        };
        self.review_id > 0
            && nonzero_hash(&self.typed_tool_ir_hash)
            && nonzero_hash(&self.policy_proof_trace_hash)
            && nonzero_hash(&self.resource_scope_hash)
            && nonzero_hash(&self.expected_effect_hash)
            && nonzero_hash(&self.redaction_policy_hash)
            && !self.evidence_ref_hashes.is_empty()
            && staging_proof_is_physical
            && (self.risk_class < RiskClass::R4
                || (self.staging_proof_ref_hash.is_some()
                    && self
                        .staging_proof_kind
                        .is_some_and(StagingEvidenceKind::is_physical))
                || self.hitl_override_ref_hash.is_some())
            && self.review_packet_hash
                == review_packet_hash(
                    self.review_id,
                    self.typed_tool_ir_hash,
                    self.policy_proof_trace_hash,
                    self.risk_class,
                    self.side_effect,
                    self.resource_scope_hash,
                    self.expected_effect_hash,
                    &self.evidence_ref_hashes,
                    self.staging_proof_ref_hash,
                    self.staging_proof_kind,
                    self.hitl_override_ref_hash,
                    self.reversibility_plan_hash,
                    self.redaction_policy_hash,
                )
    }
}

impl SignedApprovalToken {
    pub fn is_expired(&self, now_ms: u64) -> bool {
        now_ms >= self.expires_at_ms
    }

    pub fn binds_review_packet(&self, packet: &ReviewPacket, now_ms: u64) -> bool {
        packet.is_valid()
            && !self.is_expired(now_ms)
            && self.approval_id > 0
            && nonzero_hash(&self.approver_key_hash)
            && nonzero_hash(&self.challenge_nonce)
            && nonzero_signature(&self.signature)
            && self.review_packet_hash == packet.review_packet_hash
            && self.typed_tool_ir_hash == packet.typed_tool_ir_hash
            && self.resource_scope_hash == packet.resource_scope_hash
            && self.max_risk_class >= packet.risk_class
    }

    pub fn replay_scope(
        &self,
        packet: &ReviewPacket,
        requested_spend_minor_units: Option<u64>,
        expected_policy_window_hash: [u8; 32],
        observed_policy_window_hash: [u8; 32],
        now_ms: u64,
    ) -> ApprovalScopeReplay {
        let decision = if !nonzero_hash(&expected_policy_window_hash)
            || expected_policy_window_hash != observed_policy_window_hash
        {
            ApprovalScopeReplayDecision::PolicyWindowMismatch
        } else if self.is_expired(now_ms) {
            ApprovalScopeReplayDecision::Expired
        } else if !self.binds_review_packet(packet, now_ms) {
            if self.approval_id == 0
                || !nonzero_hash(&self.approver_key_hash)
                || !nonzero_hash(&self.challenge_nonce)
                || !nonzero_signature(&self.signature)
            {
                ApprovalScopeReplayDecision::MissingOrInvalid
            } else {
                ApprovalScopeReplayDecision::ScopeMismatch
            }
        } else if !self.covers_spend(requested_spend_minor_units) {
            ApprovalScopeReplayDecision::SpendLimitExceeded
        } else {
            ApprovalScopeReplayDecision::Covered
        };

        ApprovalScopeReplay::new(
            decision,
            self.approval_id,
            packet.review_packet_hash,
            packet.typed_tool_ir_hash,
            packet.resource_scope_hash,
            expected_policy_window_hash,
            requested_spend_minor_units,
        )
    }

    fn covers_spend(&self, requested_spend_minor_units: Option<u64>) -> bool {
        match (requested_spend_minor_units, self.max_spend_minor_units) {
            (None, _) => true,
            (Some(_), None) => false,
            (Some(requested), Some(maximum)) => requested <= maximum,
        }
    }
}

impl ApprovalScopeReplay {
    #[allow(clippy::too_many_arguments)]
    fn new(
        decision: ApprovalScopeReplayDecision,
        approval_id: ApprovalId,
        review_packet_hash: [u8; 32],
        typed_tool_ir_hash: [u8; 32],
        resource_scope_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        requested_spend_minor_units: Option<u64>,
    ) -> Self {
        let replay_hash = approval_scope_replay_hash(
            decision,
            approval_id,
            review_packet_hash,
            typed_tool_ir_hash,
            resource_scope_hash,
            policy_window_hash,
            requested_spend_minor_units,
        );
        Self {
            decision,
            approval_id,
            review_packet_hash,
            typed_tool_ir_hash,
            resource_scope_hash,
            policy_window_hash,
            requested_spend_minor_units,
            replay_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.approval_id > 0
            && nonzero_hash(&self.review_packet_hash)
            && nonzero_hash(&self.typed_tool_ir_hash)
            && nonzero_hash(&self.resource_scope_hash)
            && nonzero_hash(&self.policy_window_hash)
            && nonzero_hash(&self.replay_hash)
            && self.replay_hash
                == approval_scope_replay_hash(
                    self.decision,
                    self.approval_id,
                    self.review_packet_hash,
                    self.typed_tool_ir_hash,
                    self.resource_scope_hash,
                    self.policy_window_hash,
                    self.requested_spend_minor_units,
                )
    }

    pub fn proves_covered(&self) -> bool {
        self.decision == ApprovalScopeReplayDecision::Covered && self.is_valid()
    }
}

impl OperatorReviewArtifact {
    pub fn new(
        packet: &ReviewPacket,
        proof: &PolicyProofTrace,
        approval_replay: Option<&ApprovalScopeReplay>,
        policy_window_hash: [u8; 32],
        replay_event_hashes: Vec<[u8; 32]>,
        helper_text_hash: Option<[u8; 32]>,
    ) -> Self {
        let approval_scope_replay_hash = approval_replay.map(|replay| replay.replay_hash);
        let signing_target_hash = operator_review_signing_target_hash(
            packet.review_packet_hash,
            proof.compute_hash(),
            approval_scope_replay_hash,
            policy_window_hash,
            &replay_event_hashes,
            packet.redaction_policy_hash,
        );
        let artifact_hash = operator_review_artifact_hash(
            signing_target_hash,
            helper_text_hash,
            packet.redaction_policy_hash,
        );
        Self {
            review_packet_hash: packet.review_packet_hash,
            policy_proof_trace_hash: proof.compute_hash(),
            approval_scope_replay_hash,
            policy_window_hash,
            replay_event_hashes,
            redaction_policy_hash: packet.redaction_policy_hash,
            helper_text_hash,
            signing_target_hash,
            artifact_hash,
        }
    }

    pub fn is_valid_with(
        &self,
        packet: &ReviewPacket,
        proof: &PolicyProofTrace,
        approval_replay: Option<&ApprovalScopeReplay>,
    ) -> bool {
        packet.is_valid()
            && proof.is_replay_consistent_with_hash(packet.typed_tool_ir_hash)
            && self.review_packet_hash == packet.review_packet_hash
            && self.policy_proof_trace_hash == proof.compute_hash()
            && self.redaction_policy_hash == packet.redaction_policy_hash
            && nonzero_hash(&self.policy_window_hash)
            && !self.replay_event_hashes.is_empty()
            && self.replay_event_hashes.iter().all(nonzero_hash)
            && approval_replay.is_none_or(|replay| {
                replay.is_valid() && self.approval_scope_replay_hash == Some(replay.replay_hash)
            })
            && (approval_replay.is_some() || self.approval_scope_replay_hash.is_none())
            && self.signing_target_hash
                == operator_review_signing_target_hash(
                    self.review_packet_hash,
                    self.policy_proof_trace_hash,
                    self.approval_scope_replay_hash,
                    self.policy_window_hash,
                    &self.replay_event_hashes,
                    self.redaction_policy_hash,
                )
            && self.artifact_hash
                == operator_review_artifact_hash(
                    self.signing_target_hash,
                    self.helper_text_hash,
                    self.redaction_policy_hash,
                )
    }

    pub fn signing_target_excludes_helper_text(&self) -> bool {
        self.signing_target_hash
            == operator_review_signing_target_hash(
                self.review_packet_hash,
                self.policy_proof_trace_hash,
                self.approval_scope_replay_hash,
                self.policy_window_hash,
                &self.replay_event_hashes,
                self.redaction_policy_hash,
            )
    }
}

impl DualApprovalProof {
    pub fn new(
        artifact: &OperatorReviewArtifact,
        packet: &ReviewPacket,
        first: &SignedApprovalToken,
        second: &SignedApprovalToken,
        now_ms: u64,
    ) -> Self {
        let proof_hash = if dual_approval_tokens_cover(artifact, packet, first, second, now_ms) {
            dual_approval_proof_hash(
                first.approval_id,
                second.approval_id,
                first.approver_key_hash,
                second.approver_key_hash,
                artifact.signing_target_hash,
            )
        } else {
            [0; 32]
        };
        Self {
            first_approval_id: first.approval_id,
            second_approval_id: second.approval_id,
            first_approver_key_hash: first.approver_key_hash,
            second_approver_key_hash: second.approver_key_hash,
            signing_target_hash: artifact.signing_target_hash,
            proof_hash,
        }
    }

    pub fn is_valid_for(
        &self,
        artifact: &OperatorReviewArtifact,
        packet: &ReviewPacket,
        first: &SignedApprovalToken,
        second: &SignedApprovalToken,
        now_ms: u64,
    ) -> bool {
        dual_approval_tokens_cover(artifact, packet, first, second, now_ms)
            && self.first_approval_id == first.approval_id
            && self.second_approval_id == second.approval_id
            && self.first_approver_key_hash == first.approver_key_hash
            && self.second_approver_key_hash == second.approver_key_hash
            && self.signing_target_hash == artifact.signing_target_hash
            && self.proof_hash
                == dual_approval_proof_hash(
                    self.first_approval_id,
                    self.second_approval_id,
                    self.first_approver_key_hash,
                    self.second_approver_key_hash,
                    self.signing_target_hash,
                )
    }
}

impl TelemetryEnvelope {
    pub fn is_valid(&self) -> bool {
        self.run_id > 0
            && self.trace_id != [0; 16]
            && self.span_id != [0; 8]
            && nonzero_hash(&self.redaction_policy_hash)
            && nonzero_hash(&self.payload_hash)
    }
}

impl HarnessBenchScorecard {
    pub fn is_valid_physical_result(&self) -> bool {
        self.bench_id > 0
            && self.task_id > 0
            && (!self.success
                || (nonzero_hash(&self.physical_witness_hash)
                    && nonzero_hash(&self.replay_hash)
                    && self.policy_violation_count == 0
                    && self.crash_recovery_passed
                    && self.witness_coverage_ppm > 0))
    }
}

fn policy_decision_from_datalog_atoms(atoms: u64) -> (PolicyDecision, Vec<u128>, Option<[u8; 32]>) {
    if has_atom(atoms, POLICY_DATALOG_ATOM_FACTS_INVALID) {
        (
            PolicyDecision::HardBlock,
            vec![POLICY_RULE_INVALID_FACTS],
            Some(static_policy_counterexample_hash("invalid-policy-facts")),
        )
    } else if has_atom(atoms, POLICY_DATALOG_ATOM_IR_INVALID) {
        (
            PolicyDecision::HardBlock,
            vec![POLICY_RULE_INVALID_IR],
            Some(static_policy_counterexample_hash("invalid-typed-tool-ir")),
        )
    } else if has_atom(atoms, POLICY_DATALOG_ATOM_R4_WITHOUT_STAGING_OR_HITL) {
        (
            PolicyDecision::HardBlock,
            vec![POLICY_RULE_R4_REQUIRES_STAGING_OR_HITL],
            Some(static_policy_counterexample_hash(
                "r4-without-staging-or-hitl",
            )),
        )
    } else if has_atom(atoms, POLICY_DATALOG_ATOM_STAGING_REQUIRED) {
        (
            PolicyDecision::RequireStagingSandbox,
            vec![POLICY_RULE_STAGING_REQUIRED],
            None,
        )
    } else if has_atom(atoms, POLICY_DATALOG_ATOM_APPROVAL_REQUIRED) {
        (
            PolicyDecision::RequireApproval,
            vec![POLICY_RULE_APPROVAL_REQUIRED],
            None,
        )
    } else {
        (PolicyDecision::Allow, vec![POLICY_RULE_ALLOW], None)
    }
}

#[allow(clippy::too_many_arguments)]
fn policy_datalog_closure_hash(
    policy_version_hash: [u8; 32],
    input_ir_hash: [u8; 32],
    facts_hash: [u8; 32],
    initial_atom_bits: u64,
    derived_atom_bits: u64,
    fired_rule_bits: u64,
    decision: PolicyDecision,
    matched_rule_ids: &[u128],
    counterexample_ref_hash: Option<[u8; 32]>,
    iteration_count: u8,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"policy-datalog-closure-v1");
    hasher.update(&policy_version_hash);
    hasher.update(&input_ir_hash);
    hasher.update(&facts_hash);
    update_u64(&mut hasher, initial_atom_bits);
    update_u64(&mut hasher, derived_atom_bits);
    update_u64(&mut hasher, fired_rule_bits);
    update_u8(&mut hasher, decision as u8);
    update_u64(&mut hasher, matched_rule_ids.len() as u64);
    for rule_id in matched_rule_ids {
        update_u128(&mut hasher, *rule_id);
    }
    update_optional_hash(&mut hasher, counterexample_ref_hash);
    update_u8(&mut hasher, iteration_count);
    *hasher.finalize().as_bytes()
}

fn has_atom(atoms: u64, atom: u64) -> bool {
    atoms & atom != 0
}

fn insert_atom(atoms: &mut u64, atom: u64) {
    *atoms |= atom;
}

fn fire_rule(atoms: &mut u64, fired_rule_bits: &mut u64, atom: u64, rule: u64) {
    if !has_atom(*atoms, atom) {
        insert_atom(atoms, atom);
        *fired_rule_bits |= rule;
    }
}

#[allow(clippy::too_many_arguments)]
fn typed_tool_ir_hash(
    tool_id: ToolId,
    operation_id: OperationId,
    capability: CapabilityClass,
    side_effect: SideEffectClass,
    credential_scope_hash: [u8; 32],
    risk_class: RiskClass,
    precondition_hash: [u8; 32],
    effect_hash: [u8; 32],
    evidence_contract: &EvidenceContract,
    approval_scope_hash: Option<[u8; 32]>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    update_u128(&mut hasher, tool_id);
    update_u128(&mut hasher, operation_id);
    update_u8(&mut hasher, capability as u8);
    update_u8(&mut hasher, side_effect as u8);
    hasher.update(&credential_scope_hash);
    update_u8(&mut hasher, risk_class as u8);
    hasher.update(&precondition_hash);
    hasher.update(&effect_hash);
    update_bool(&mut hasher, evidence_contract.requires_physical_witness);
    update_bool(&mut hasher, evidence_contract.requires_approval);
    update_bool(&mut hasher, evidence_contract.requires_staging);
    update_optional_hash(&mut hasher, evidence_contract.expected_artifact_hash);
    update_optional_hash(&mut hasher, approval_scope_hash);
    *hasher.finalize().as_bytes()
}

fn policy_facts_hash(
    policy_version_hash: [u8; 32],
    has_valid_approval: bool,
    has_staging_environment: bool,
    has_hitl_signature: bool,
    staging_proof_ref_hash: Option<[u8; 32]>,
    staging_evidence_kind: Option<StagingEvidenceKind>,
    hitl_signature_ref_hash: Option<[u8; 32]>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(&policy_version_hash);
    update_bool(&mut hasher, has_valid_approval);
    update_bool(&mut hasher, has_staging_environment);
    update_bool(&mut hasher, has_hitl_signature);
    update_optional_hash(&mut hasher, staging_proof_ref_hash);
    update_optional_staging_evidence_kind(&mut hasher, staging_evidence_kind);
    update_optional_hash(&mut hasher, hitl_signature_ref_hash);
    *hasher.finalize().as_bytes()
}

fn approval_scope_replay_hash(
    decision: ApprovalScopeReplayDecision,
    approval_id: ApprovalId,
    review_packet_hash: [u8; 32],
    typed_tool_ir_hash: [u8; 32],
    resource_scope_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    requested_spend_minor_units: Option<u64>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"approval-scope-replay-v1");
    update_u8(&mut hasher, decision as u8);
    update_u128(&mut hasher, approval_id);
    hasher.update(&review_packet_hash);
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&resource_scope_hash);
    hasher.update(&policy_window_hash);
    match requested_spend_minor_units {
        Some(value) => {
            update_bool(&mut hasher, true);
            update_u64(&mut hasher, value);
        }
        None => update_bool(&mut hasher, false),
    }
    *hasher.finalize().as_bytes()
}

fn operator_review_signing_target_hash(
    review_packet_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    approval_scope_replay_hash: Option<[u8; 32]>,
    policy_window_hash: [u8; 32],
    replay_event_hashes: &[[u8; 32]],
    redaction_policy_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"operator-review-signing-target-v1");
    hasher.update(&review_packet_hash);
    hasher.update(&policy_proof_trace_hash);
    update_optional_hash(&mut hasher, approval_scope_replay_hash);
    hasher.update(&policy_window_hash);
    update_u64(&mut hasher, replay_event_hashes.len() as u64);
    for event_hash in replay_event_hashes {
        hasher.update(event_hash);
    }
    hasher.update(&redaction_policy_hash);
    *hasher.finalize().as_bytes()
}

fn operator_review_artifact_hash(
    signing_target_hash: [u8; 32],
    helper_text_hash: Option<[u8; 32]>,
    redaction_policy_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"operator-review-artifact-v1");
    hasher.update(&signing_target_hash);
    update_optional_hash(&mut hasher, helper_text_hash);
    hasher.update(&redaction_policy_hash);
    *hasher.finalize().as_bytes()
}

fn dual_approval_tokens_cover(
    artifact: &OperatorReviewArtifact,
    packet: &ReviewPacket,
    first: &SignedApprovalToken,
    second: &SignedApprovalToken,
    now_ms: u64,
) -> bool {
    artifact.review_packet_hash == packet.review_packet_hash
        && artifact.signing_target_excludes_helper_text()
        && first.approval_id != second.approval_id
        && first.approver_key_hash != second.approver_key_hash
        && first.binds_review_packet(packet, now_ms)
        && second.binds_review_packet(packet, now_ms)
        && first.review_packet_hash == artifact.review_packet_hash
        && second.review_packet_hash == artifact.review_packet_hash
}

fn dual_approval_proof_hash(
    first_approval_id: ApprovalId,
    second_approval_id: ApprovalId,
    first_approver_key_hash: [u8; 32],
    second_approver_key_hash: [u8; 32],
    signing_target_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"dual-approval-proof-v1");
    update_u128(&mut hasher, first_approval_id);
    update_u128(&mut hasher, second_approval_id);
    hasher.update(&first_approver_key_hash);
    hasher.update(&second_approver_key_hash);
    hasher.update(&signing_target_hash);
    *hasher.finalize().as_bytes()
}

fn static_policy_counterexample_hash(label: &str) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"policy-counterexample:");
    hasher.update(label.as_bytes());
    *hasher.finalize().as_bytes()
}

fn minimum_required_risk(capability: CapabilityClass, side_effect: SideEffectClass) -> RiskClass {
    let capability_risk = match capability {
        CapabilityClass::LocalRead | CapabilityClass::Inference | CapabilityClass::Retrieval => {
            RiskClass::R0
        }
        CapabilityClass::LocalWrite => RiskClass::R1,
        CapabilityClass::ExternalRead | CapabilityClass::Browser => RiskClass::R2,
        CapabilityClass::ExternalWrite | CapabilityClass::Computer => RiskClass::R3,
        CapabilityClass::FinancialLegal => RiskClass::R4,
    };
    let side_effect_risk = match side_effect {
        SideEffectClass::None => RiskClass::R0,
        SideEffectClass::LocalReversible => RiskClass::R1,
        SideEffectClass::ExternalRead => RiskClass::R2,
        SideEffectClass::ExternalWrite => RiskClass::R3,
        SideEffectClass::Destructive | SideEffectClass::FinancialLegal => RiskClass::R4,
    };
    capability_risk.max(side_effect_risk)
}

#[allow(clippy::too_many_arguments)]
fn review_packet_hash(
    review_id: ReviewId,
    typed_tool_ir_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    risk_class: RiskClass,
    side_effect: SideEffectClass,
    resource_scope_hash: [u8; 32],
    expected_effect_hash: [u8; 32],
    evidence_ref_hashes: &[[u8; 32]],
    staging_proof_ref_hash: Option<[u8; 32]>,
    staging_proof_kind: Option<StagingEvidenceKind>,
    hitl_override_ref_hash: Option<[u8; 32]>,
    reversibility_plan_hash: Option<[u8; 32]>,
    redaction_policy_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    update_u128(&mut hasher, review_id);
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&policy_proof_trace_hash);
    update_u8(&mut hasher, risk_class as u8);
    update_u8(&mut hasher, side_effect as u8);
    hasher.update(&resource_scope_hash);
    hasher.update(&expected_effect_hash);
    update_u64(&mut hasher, evidence_ref_hashes.len() as u64);
    for evidence_ref_hash in evidence_ref_hashes {
        hasher.update(evidence_ref_hash);
    }
    update_optional_hash(&mut hasher, staging_proof_ref_hash);
    update_optional_staging_evidence_kind(&mut hasher, staging_proof_kind);
    update_optional_hash(&mut hasher, hitl_override_ref_hash);
    update_optional_hash(&mut hasher, reversibility_plan_hash);
    hasher.update(&redaction_policy_hash);
    *hasher.finalize().as_bytes()
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn nonzero_signature(signature: &[u8; 64]) -> bool {
    signature.iter().any(|byte| *byte != 0)
}

fn update_bool(hasher: &mut Hasher, value: bool) {
    update_u8(hasher, u8::from(value));
}

fn update_u8(hasher: &mut Hasher, value: u8) {
    hasher.update(&[value]);
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_optional_hash(hasher: &mut Hasher, value: Option<[u8; 32]>) {
    match value {
        Some(hash) => {
            update_bool(hasher, true);
            hasher.update(&hash);
        }
        None => update_bool(hasher, false),
    }
}

fn update_optional_staging_evidence_kind(hasher: &mut Hasher, value: Option<StagingEvidenceKind>) {
    match value {
        Some(kind) => {
            update_bool(hasher, true);
            update_u8(hasher, kind as u8);
        }
        None => update_bool(hasher, false),
    }
}
