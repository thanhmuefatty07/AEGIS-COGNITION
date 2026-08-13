use crate::policy::{
    CapabilityClass, EvidenceContract, RiskClass, SideEffectClass, ToolId, TypedToolIR,
};
use crate::task_ledger::{TaskCard, TaskId, TaskStatus};
use blake3::Hasher;

pub type GoalId = u128;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GoalIntakeError {
    InvalidInput,
    InvalidProof,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GoalIntakePacket {
    pub goal_id: GoalId,
    pub session_id: u128,
    pub operator_id_hash: [u8; 32],
    pub raw_goal_ref_hash: [u8; 32],
    pub normalized_goal_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub classified_capability: CapabilityClass,
    pub classified_side_effect: SideEffectClass,
    pub classified_risk: RiskClass,
    pub classifier_rule_bits: u64,
    pub packet_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GoalIntakeProof {
    pub packet: GoalIntakePacket,
    pub root_task: TaskCard,
    pub initial_ir: TypedToolIR,
    pub root_task_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

const RULE_LOCAL_WRITE: u64 = 1 << 0;
const RULE_EXTERNAL_READ: u64 = 1 << 1;
const RULE_EXTERNAL_WRITE: u64 = 1 << 2;
const RULE_BROWSER: u64 = 1 << 3;
const RULE_COMPUTER: u64 = 1 << 4;
const RULE_INFERENCE: u64 = 1 << 5;
const RULE_RETRIEVAL: u64 = 1 << 6;
const RULE_DESTRUCTIVE: u64 = 1 << 7;
const RULE_FINANCIAL_LEGAL: u64 = 1 << 8;

impl GoalIntakePacket {
    pub fn from_goal_text(
        session_id: u128,
        operator_id_hash: [u8; 32],
        raw_goal_ref_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        goal_text: &str,
    ) -> Result<Self, GoalIntakeError> {
        let normalized_goal = normalize_goal_text(goal_text)?;
        let normalized_goal_hash = normalized_goal_hash_from_normalized(&normalized_goal);
        Self::from_verified_normalized_goal(
            session_id,
            operator_id_hash,
            raw_goal_ref_hash,
            normalized_goal_hash,
            policy_window_hash,
            &normalized_goal,
        )
    }

    pub fn from_normalized_hash(
        session_id: u128,
        operator_id_hash: [u8; 32],
        raw_goal_ref_hash: [u8; 32],
        normalized_goal_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        goal_text_for_classification: &str,
    ) -> Result<Self, GoalIntakeError> {
        let normalized_goal = normalize_goal_text(goal_text_for_classification)?;
        if normalized_goal_hash != normalized_goal_hash_from_normalized(&normalized_goal) {
            return Err(GoalIntakeError::InvalidInput);
        }
        Self::from_verified_normalized_goal(
            session_id,
            operator_id_hash,
            raw_goal_ref_hash,
            normalized_goal_hash,
            policy_window_hash,
            &normalized_goal,
        )
    }

    fn from_verified_normalized_goal(
        session_id: u128,
        operator_id_hash: [u8; 32],
        raw_goal_ref_hash: [u8; 32],
        normalized_goal_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        normalized_goal_for_classification: &str,
    ) -> Result<Self, GoalIntakeError> {
        if session_id == 0
            || !nonzero_hash(&operator_id_hash)
            || !nonzero_hash(&raw_goal_ref_hash)
            || !nonzero_hash(&normalized_goal_hash)
            || !nonzero_hash(&policy_window_hash)
        {
            return Err(GoalIntakeError::InvalidInput);
        }
        let classification = classify_normalized_goal_text(normalized_goal_for_classification)?;
        let goal_id = derive_goal_id(session_id, normalized_goal_hash, policy_window_hash);
        let packet_hash = goal_intake_packet_hash(
            goal_id,
            session_id,
            operator_id_hash,
            raw_goal_ref_hash,
            normalized_goal_hash,
            policy_window_hash,
            classification.capability,
            classification.side_effect,
            classification.risk,
            classification.rule_bits,
        );
        let packet = Self {
            goal_id,
            session_id,
            operator_id_hash,
            raw_goal_ref_hash,
            normalized_goal_hash,
            policy_window_hash,
            classified_capability: classification.capability,
            classified_side_effect: classification.side_effect,
            classified_risk: classification.risk,
            classifier_rule_bits: classification.rule_bits,
            packet_hash,
        };
        packet
            .is_valid()
            .then_some(packet)
            .ok_or(GoalIntakeError::InvalidInput)
    }

    pub fn is_valid(&self) -> bool {
        self.goal_id > 0
            && self.session_id > 0
            && nonzero_hash(&self.operator_id_hash)
            && nonzero_hash(&self.raw_goal_ref_hash)
            && nonzero_hash(&self.normalized_goal_hash)
            && nonzero_hash(&self.policy_window_hash)
            && self.classifier_rule_bits != 0
            && self.classified_risk
                == minimum_goal_risk(self.classified_capability, self.classified_side_effect)
            && self.packet_hash
                == goal_intake_packet_hash(
                    self.goal_id,
                    self.session_id,
                    self.operator_id_hash,
                    self.raw_goal_ref_hash,
                    self.normalized_goal_hash,
                    self.policy_window_hash,
                    self.classified_capability,
                    self.classified_side_effect,
                    self.classified_risk,
                    self.classifier_rule_bits,
                )
            && nonzero_hash(&self.packet_hash)
    }

    pub fn to_root_task(
        &self,
        evidence_unblock_count: u32,
        hard_deadline_ms: Option<u64>,
    ) -> TaskCard {
        TaskCard::new(
            self.root_task_id(),
            Vec::new(),
            evidence_unblock_count,
            hard_deadline_ms,
            None,
        )
    }

    pub fn to_initial_typed_tool_ir(&self) -> TypedToolIR {
        let evidence_contract = evidence_contract_for_goal_risk(self.classified_risk);
        TypedToolIR::new(
            self.tool_id(),
            self.operation_id(),
            self.classified_capability,
            self.classified_side_effect,
            self.operator_id_hash,
            self.classified_risk,
            self.normalized_goal_hash,
            self.packet_hash,
            evidence_contract,
            approval_scope_for_goal_risk(self.classified_risk, self.policy_window_hash),
        )
    }

    fn root_task_id(&self) -> TaskId {
        derive_subject_id(b"aegis-goal-root-task-id-v1", self.packet_hash)
    }

    fn tool_id(&self) -> ToolId {
        derive_subject_id(b"aegis-goal-tool-id-v1", self.packet_hash)
    }

    fn operation_id(&self) -> u128 {
        derive_subject_id(b"aegis-goal-operation-id-v1", self.normalized_goal_hash)
    }
}

impl GoalIntakeProof {
    pub fn from_packet(
        packet: GoalIntakePacket,
        evidence_unblock_count: u32,
        hard_deadline_ms: Option<u64>,
    ) -> Result<Self, GoalIntakeError> {
        if !packet.is_valid() {
            return Err(GoalIntakeError::InvalidInput);
        }
        let root_task = packet.to_root_task(evidence_unblock_count, hard_deadline_ms);
        let initial_ir = packet.to_initial_typed_tool_ir();
        let root_task_hash = goal_root_task_hash(&root_task);
        let proof_hash = goal_intake_proof_hash(&packet, &root_task, &initial_ir, root_task_hash);
        let proof = Self {
            packet,
            root_task,
            initial_ir,
            root_task_hash,
            proof_hash,
        };
        proof
            .is_valid()
            .then_some(proof)
            .ok_or(GoalIntakeError::InvalidProof)
    }

    pub fn from_goal_text(
        session_id: u128,
        operator_id_hash: [u8; 32],
        raw_goal_ref_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        goal_text: &str,
        evidence_unblock_count: u32,
        hard_deadline_ms: Option<u64>,
    ) -> Result<Self, GoalIntakeError> {
        let packet = GoalIntakePacket::from_goal_text(
            session_id,
            operator_id_hash,
            raw_goal_ref_hash,
            policy_window_hash,
            goal_text,
        )?;
        Self::from_packet(packet, evidence_unblock_count, hard_deadline_ms)
    }

    pub fn is_valid(&self) -> bool {
        self.packet.is_valid()
            && self.root_task.task_id == self.packet.root_task_id()
            && self.root_task.dependency_ids.is_empty()
            && self.root_task.suggested_priority.is_none()
            && self.root_task_hash == goal_root_task_hash(&self.root_task)
            && self.initial_ir.is_valid()
            && self.initial_ir.tool_id == self.packet.tool_id()
            && self.initial_ir.operation_id == self.packet.operation_id()
            && self.initial_ir.capability == self.packet.classified_capability
            && self.initial_ir.side_effect == self.packet.classified_side_effect
            && self.initial_ir.risk_class == self.packet.classified_risk
            && self.initial_ir.credential_scope_hash == self.packet.operator_id_hash
            && self.initial_ir.precondition_hash == self.packet.normalized_goal_hash
            && self.initial_ir.effect_hash == self.packet.packet_hash
            && self.proof_hash
                == goal_intake_proof_hash(
                    &self.packet,
                    &self.root_task,
                    &self.initial_ir,
                    self.root_task_hash,
                )
            && nonzero_hash(&self.proof_hash)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct GoalClassification {
    capability: CapabilityClass,
    side_effect: SideEffectClass,
    risk: RiskClass,
    rule_bits: u64,
}

fn classify_normalized_goal_text(normalized: &str) -> Result<GoalClassification, GoalIntakeError> {
    if normalized.is_empty() {
        return Err(GoalIntakeError::InvalidInput);
    }
    let mut rule_bits = 0_u64;

    if contains_any(
        normalized,
        &["delete", "remove", "destroy", "wipe", "reset", "drop table"],
    ) {
        rule_bits |= RULE_DESTRUCTIVE;
    }
    if contains_any(
        normalized,
        &[
            "financial",
            "payment",
            "wire",
            "bank",
            "legal",
            "contract",
            "invoice",
            "tax",
        ],
    ) {
        rule_bits |= RULE_FINANCIAL_LEGAL;
    }
    if contains_any(
        normalized,
        &["browser", "chrome", "playwright", "web page", "website"],
    ) {
        rule_bits |= RULE_BROWSER;
    }
    if contains_any(
        normalized,
        &["computer", "desktop", "windows app", "ui automation"],
    ) {
        rule_bits |= RULE_COMPUTER;
    }
    if contains_any(normalized, &["search", "fetch", "download", "http", "api"]) {
        rule_bits |= RULE_EXTERNAL_READ;
    }
    if contains_non_negated_any(
        normalized,
        &[
            "post",
            "submit",
            "send",
            "upload",
            "publish",
            "external write",
        ],
    ) {
        rule_bits |= RULE_EXTERNAL_WRITE;
    }
    if contains_any(
        normalized,
        &["write file", "edit", "modify", "patch", "save"],
    ) {
        rule_bits |= RULE_LOCAL_WRITE;
    }
    if contains_any(
        normalized,
        &["infer", "llm", "model", "generate", "summarize"],
    ) {
        rule_bits |= RULE_INFERENCE;
    }
    if contains_any(normalized, &["retrieve", "candidate", "index", "context"]) {
        rule_bits |= RULE_RETRIEVAL;
    }
    if rule_bits == 0 {
        rule_bits = RULE_RETRIEVAL;
    }

    let capability = if rule_bits & RULE_FINANCIAL_LEGAL != 0 {
        CapabilityClass::FinancialLegal
    } else if rule_bits & RULE_COMPUTER != 0 {
        CapabilityClass::Computer
    } else if rule_bits & RULE_BROWSER != 0 {
        CapabilityClass::Browser
    } else if rule_bits & RULE_EXTERNAL_WRITE != 0 {
        CapabilityClass::ExternalWrite
    } else if rule_bits & RULE_EXTERNAL_READ != 0 {
        CapabilityClass::ExternalRead
    } else if rule_bits & RULE_LOCAL_WRITE != 0 {
        CapabilityClass::LocalWrite
    } else if rule_bits & RULE_INFERENCE != 0 {
        CapabilityClass::Inference
    } else {
        CapabilityClass::Retrieval
    };
    let side_effect = if rule_bits & RULE_FINANCIAL_LEGAL != 0 {
        SideEffectClass::FinancialLegal
    } else if rule_bits & RULE_DESTRUCTIVE != 0 {
        SideEffectClass::Destructive
    } else if rule_bits & RULE_EXTERNAL_WRITE != 0 {
        SideEffectClass::ExternalWrite
    } else if rule_bits & RULE_EXTERNAL_READ != 0
        || rule_bits & RULE_BROWSER != 0
        || rule_bits & RULE_COMPUTER != 0
    {
        SideEffectClass::ExternalRead
    } else if rule_bits & RULE_LOCAL_WRITE != 0 {
        SideEffectClass::LocalReversible
    } else {
        SideEffectClass::None
    };
    Ok(GoalClassification {
        capability,
        side_effect,
        risk: minimum_goal_risk(capability, side_effect),
        rule_bits,
    })
}

fn evidence_contract_for_goal_risk(risk: RiskClass) -> EvidenceContract {
    match risk {
        RiskClass::R0 | RiskClass::R1 | RiskClass::R2 => EvidenceContract::physical(),
        RiskClass::R3 => EvidenceContract {
            requires_physical_witness: true,
            requires_approval: true,
            requires_staging: false,
            expected_artifact_hash: None,
        },
        RiskClass::R4 => EvidenceContract::r4_staged(),
    }
}

fn approval_scope_for_goal_risk(risk: RiskClass, policy_window_hash: [u8; 32]) -> Option<[u8; 32]> {
    (risk >= RiskClass::R3).then_some(policy_window_hash)
}

fn minimum_goal_risk(capability: CapabilityClass, side_effect: SideEffectClass) -> RiskClass {
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

pub fn normalized_goal_hash(goal_text: &str) -> Result<[u8; 32], GoalIntakeError> {
    let normalized = normalize_goal_text(goal_text)?;
    Ok(normalized_goal_hash_from_normalized(&normalized))
}

fn normalized_goal_hash_from_normalized(normalized: &str) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-normalized-goal-v1");
    hasher.update(normalized.as_bytes());
    *hasher.finalize().as_bytes()
}

fn normalize_goal_text(goal_text: &str) -> Result<String, GoalIntakeError> {
    let normalized = goal_text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase();
    if normalized.is_empty() {
        return Err(GoalIntakeError::InvalidInput);
    }
    Ok(normalized)
}

fn contains_any(normalized: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| normalized.contains(needle))
}

fn contains_non_negated_any(normalized: &str, needles: &[&str]) -> bool {
    needles
        .iter()
        .any(|needle| contains_non_negated_phrase(normalized, needle))
}

fn contains_non_negated_phrase(normalized: &str, needle: &str) -> bool {
    let mut search_from = 0;
    while let Some(offset) = normalized[search_from..].find(needle) {
        let phrase_start = search_from + offset;
        if !is_negated_at(normalized, phrase_start) {
            return true;
        }
        search_from = phrase_start + needle.len();
    }
    false
}

fn is_negated_at(normalized: &str, phrase_start: usize) -> bool {
    for prefix in ["no ", "not ", "without ", "avoid "] {
        if phrase_start >= prefix.len() && normalized[..phrase_start].ends_with(prefix) {
            return true;
        }
    }
    false
}

fn derive_goal_id(
    session_id: u128,
    normalized_goal_hash: [u8; 32],
    policy_window_hash: [u8; 32],
) -> GoalId {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-id-v1");
    update_u128(&mut hasher, session_id);
    hasher.update(&normalized_goal_hash);
    hasher.update(&policy_window_hash);
    hash_to_nonzero_u128(*hasher.finalize().as_bytes())
}

fn derive_subject_id(domain: &[u8], hash: [u8; 32]) -> u128 {
    let mut hasher = Hasher::new();
    hasher.update(domain);
    hasher.update(&hash);
    hash_to_nonzero_u128(*hasher.finalize().as_bytes())
}

fn hash_to_nonzero_u128(hash: [u8; 32]) -> u128 {
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&hash[..16]);
    let value = u128::from_le_bytes(bytes);
    if value == 0 {
        1
    } else {
        value
    }
}

#[allow(clippy::too_many_arguments)]
fn goal_intake_packet_hash(
    goal_id: GoalId,
    session_id: u128,
    operator_id_hash: [u8; 32],
    raw_goal_ref_hash: [u8; 32],
    normalized_goal_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    capability: CapabilityClass,
    side_effect: SideEffectClass,
    risk: RiskClass,
    classifier_rule_bits: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-intake-packet-v1");
    update_u128(&mut hasher, goal_id);
    update_u128(&mut hasher, session_id);
    hasher.update(&operator_id_hash);
    hasher.update(&raw_goal_ref_hash);
    hasher.update(&normalized_goal_hash);
    hasher.update(&policy_window_hash);
    update_u8(&mut hasher, capability_code(capability));
    update_u8(&mut hasher, side_effect_code(side_effect));
    update_u8(&mut hasher, risk_code(risk));
    update_u64(&mut hasher, classifier_rule_bits);
    *hasher.finalize().as_bytes()
}

fn capability_code(capability: CapabilityClass) -> u8 {
    match capability {
        CapabilityClass::LocalRead => 1,
        CapabilityClass::LocalWrite => 2,
        CapabilityClass::ExternalRead => 3,
        CapabilityClass::ExternalWrite => 4,
        CapabilityClass::Browser => 5,
        CapabilityClass::Computer => 6,
        CapabilityClass::Inference => 7,
        CapabilityClass::Retrieval => 8,
        CapabilityClass::FinancialLegal => 9,
    }
}

fn side_effect_code(side_effect: SideEffectClass) -> u8 {
    match side_effect {
        SideEffectClass::None => 1,
        SideEffectClass::LocalReversible => 2,
        SideEffectClass::ExternalRead => 3,
        SideEffectClass::ExternalWrite => 4,
        SideEffectClass::Destructive => 5,
        SideEffectClass::FinancialLegal => 6,
    }
}

fn risk_code(risk: RiskClass) -> u8 {
    match risk {
        RiskClass::R0 => 1,
        RiskClass::R1 => 2,
        RiskClass::R2 => 3,
        RiskClass::R3 => 4,
        RiskClass::R4 => 5,
    }
}

fn goal_root_task_hash(task: &TaskCard) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-root-task-v1");
    update_u128(&mut hasher, task.task_id);
    update_u8(&mut hasher, task_status_code(task.status));
    update_u64(&mut hasher, task.dependency_ids.len() as u64);
    for dependency_id in &task.dependency_ids {
        update_u128(&mut hasher, *dependency_id);
    }
    update_u64(&mut hasher, task.deterministic_priority_score);
    update_u32(&mut hasher, task.critical_path_len);
    update_u32(&mut hasher, task.blocked_descendant_count);
    update_u32(&mut hasher, task.evidence_unblock_count);
    update_optional_u64(&mut hasher, task.hard_deadline_ms);
    *hasher.finalize().as_bytes()
}

fn task_status_code(status: TaskStatus) -> u8 {
    match status {
        TaskStatus::Pending => 1,
        TaskStatus::Ready => 2,
        TaskStatus::Running => 3,
        TaskStatus::Done => 4,
        TaskStatus::Failed => 5,
        TaskStatus::Cancelled => 6,
    }
}

fn goal_intake_proof_hash(
    packet: &GoalIntakePacket,
    root_task: &TaskCard,
    initial_ir: &TypedToolIR,
    root_task_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-intake-proof-v1");
    hasher.update(&packet.packet_hash);
    update_u128(&mut hasher, root_task.task_id);
    hasher.update(&root_task_hash);
    hasher.update(&initial_ir.canonical_hash);
    *hasher.finalize().as_bytes()
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn update_u8(hasher: &mut Hasher, value: u8) {
    hasher.update(&[value]);
}

fn update_u32(hasher: &mut Hasher, value: u32) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_optional_u64(hasher: &mut Hasher, value: Option<u64>) {
    match value {
        Some(value) => {
            update_u8(hasher, 1);
            update_u64(hasher, value);
        }
        None => update_u8(hasher, 0),
    }
}
