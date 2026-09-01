//! Explicit GT96 contract primitives.
//!
//! These types are deliberately boring and deterministic.  They provide a
//! small authority surface for the parts of the architecture that must not be
//! inferred from model text, telemetry, or a mutable summary.  The existing
//! runtime remains the owner of task/resource leases; this module supplies the
//! versioned goal, progress, budget, finalization, evidence, cache, and
//! capability contracts used at that boundary.

use blake3::Hasher;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};

use crate::policy::SideEffectClass;

pub const GT96_CONTRACT_SCHEMA_V1: &str = "aegis-gt96-contracts-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContractError {
    EmptyField,
    DuplicateCriterion,
    MissingCriterion,
    InvalidVersion,
    InvalidEvidence,
    EvidenceRequired,
    InvalidBudget,
    BudgetExceeded,
    InvalidTransition,
    UnknownInstance,
    OrphanInstance,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub enum CriterionStatus {
    Open,
    InProgress,
    Verified,
    Invalidated,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AcceptanceCriterion {
    pub id: String,
    pub description: String,
    pub status: CriterionStatus,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Default, Serialize, Deserialize)]
pub struct BudgetVector {
    pub tokens: u64,
    pub money_minor_units: u64,
    pub time_ms: u64,
    pub tool_calls: u64,
    pub api_calls: u64,
    pub cpu_ms: u64,
    pub risk_units: u64,
}

impl BudgetVector {
    pub const fn zero() -> Self {
        Self {
            tokens: 0,
            money_minor_units: 0,
            time_ms: 0,
            tool_calls: 0,
            api_calls: 0,
            cpu_ms: 0,
            risk_units: 0,
        }
    }

    pub fn fits_within(self, limit: Self) -> bool {
        self.tokens <= limit.tokens
            && self.money_minor_units <= limit.money_minor_units
            && self.time_ms <= limit.time_ms
            && self.tool_calls <= limit.tool_calls
            && self.api_calls <= limit.api_calls
            && self.cpu_ms <= limit.cpu_ms
            && self.risk_units <= limit.risk_units
    }

    fn checked_sub(self, value: Self) -> Result<Self, ContractError> {
        if !value.fits_within(self) {
            return Err(ContractError::BudgetExceeded);
        }
        Ok(Self {
            tokens: self.tokens - value.tokens,
            money_minor_units: self.money_minor_units - value.money_minor_units,
            time_ms: self.time_ms - value.time_ms,
            tool_calls: self.tool_calls - value.tool_calls,
            api_calls: self.api_calls - value.api_calls,
            cpu_ms: self.cpu_ms - value.cpu_ms,
            risk_units: self.risk_units - value.risk_units,
        })
    }

    fn checked_add(self, value: Self) -> Result<Self, ContractError> {
        Ok(Self {
            tokens: self
                .tokens
                .checked_add(value.tokens)
                .ok_or(ContractError::InvalidBudget)?,
            money_minor_units: self
                .money_minor_units
                .checked_add(value.money_minor_units)
                .ok_or(ContractError::InvalidBudget)?,
            time_ms: self
                .time_ms
                .checked_add(value.time_ms)
                .ok_or(ContractError::InvalidBudget)?,
            tool_calls: self
                .tool_calls
                .checked_add(value.tool_calls)
                .ok_or(ContractError::InvalidBudget)?,
            api_calls: self
                .api_calls
                .checked_add(value.api_calls)
                .ok_or(ContractError::InvalidBudget)?,
            cpu_ms: self
                .cpu_ms
                .checked_add(value.cpu_ms)
                .ok_or(ContractError::InvalidBudget)?,
            risk_units: self
                .risk_units
                .checked_add(value.risk_units)
                .ok_or(ContractError::InvalidBudget)?,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BudgetPolicy {
    pub total: BudgetVector,
    pub finalization_reserve: BudgetVector,
    pub recovery_reserve: BudgetVector,
}

impl BudgetPolicy {
    pub fn validate(&self) -> Result<(), ContractError> {
        let reserved = self
            .finalization_reserve
            .checked_add(self.recovery_reserve)?;
        if !reserved.fits_within(self.total) {
            return Err(ContractError::InvalidBudget);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GoalContract {
    pub schema: String,
    pub version: u64,
    pub objective: String,
    pub acceptance_criteria: Vec<AcceptanceCriterion>,
    pub constraints: Vec<String>,
    pub invariants: Vec<String>,
    pub non_goals: Vec<String>,
    pub effect_policy: SideEffectClass,
    pub evidence_required: bool,
    pub budget_policy: BudgetPolicy,
    /// The immutable parent binding for an evolved contract. `None` is only
    /// valid for the genesis contract (version 1).
    pub parent_contract_hash: Option<[u8; 32]>,
    pub evolution_reason: Option<String>,
    pub contract_hash: [u8; 32],
}

impl GoalContract {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        objective: impl Into<String>,
        acceptance_criteria: Vec<AcceptanceCriterion>,
        constraints: Vec<String>,
        invariants: Vec<String>,
        non_goals: Vec<String>,
        effect_policy: SideEffectClass,
        evidence_required: bool,
        budget_policy: BudgetPolicy,
    ) -> Result<Self, ContractError> {
        let objective = objective.into();
        if objective.trim().is_empty()
            || acceptance_criteria.is_empty()
            || constraints.iter().any(|value| value.trim().is_empty())
            || invariants.iter().any(|value| value.trim().is_empty())
            || non_goals.iter().any(|value| value.trim().is_empty())
        {
            return Err(ContractError::EmptyField);
        }
        let mut ids = BTreeSet::new();
        for criterion in &acceptance_criteria {
            if criterion.id.trim().is_empty() || criterion.description.trim().is_empty() {
                return Err(ContractError::EmptyField);
            }
            if !ids.insert(criterion.id.clone()) {
                return Err(ContractError::DuplicateCriterion);
            }
        }
        budget_policy.validate()?;
        let mut contract = Self {
            schema: GT96_CONTRACT_SCHEMA_V1.to_string(),
            version: 1,
            objective,
            acceptance_criteria,
            constraints,
            invariants,
            non_goals,
            effect_policy,
            evidence_required,
            budget_policy,
            parent_contract_hash: None,
            evolution_reason: None,
            contract_hash: [0; 32],
        };
        contract.contract_hash = contract.compute_hash();
        Ok(contract)
    }

    pub fn evolve(
        &self,
        expected_version: u64,
        objective: impl Into<String>,
        acceptance_criteria: Vec<AcceptanceCriterion>,
    ) -> Result<Self, ContractError> {
        self.evolve_with_reason(
            expected_version,
            objective,
            acceptance_criteria,
            "contract-evolution",
        )
    }

    pub fn evolve_with_reason(
        &self,
        expected_version: u64,
        objective: impl Into<String>,
        acceptance_criteria: Vec<AcceptanceCriterion>,
        reason: impl Into<String>,
    ) -> Result<Self, ContractError> {
        if expected_version != self.version {
            return Err(ContractError::InvalidVersion);
        }
        let reason = reason.into();
        if reason.trim().is_empty() {
            return Err(ContractError::EmptyField);
        }
        for existing in &self.acceptance_criteria {
            if !acceptance_criteria
                .iter()
                .any(|criterion| criterion.id == existing.id)
            {
                return Err(ContractError::MissingCriterion);
            }
        }
        let mut next = Self::new(
            objective,
            acceptance_criteria,
            self.constraints.clone(),
            self.invariants.clone(),
            self.non_goals.clone(),
            self.effect_policy,
            self.evidence_required,
            self.budget_policy.clone(),
        )?;
        next.version = self
            .version
            .checked_add(1)
            .ok_or(ContractError::InvalidVersion)?;
        next.parent_contract_hash = Some(self.contract_hash);
        next.evolution_reason = Some(reason);
        next.contract_hash = next.compute_hash();
        Ok(next)
    }

    fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = Hasher::new();
        hasher.update(b"AEGIS-GT96-GOAL-CONTRACT\0");
        update_string(&mut hasher, &self.schema);
        update_u64(&mut hasher, self.version);
        update_string(&mut hasher, &self.objective);
        update_u64(&mut hasher, self.acceptance_criteria.len() as u64);
        for criterion in &self.acceptance_criteria {
            update_string(&mut hasher, &criterion.id);
            update_string(&mut hasher, &criterion.description);
        }
        for (label, values) in [
            (b"constraints".as_slice(), &self.constraints),
            (b"invariants".as_slice(), &self.invariants),
            (b"non-goals".as_slice(), &self.non_goals),
        ] {
            hasher.update(&(label.len() as u64).to_le_bytes());
            hasher.update(label);
            update_u64(&mut hasher, values.len() as u64);
            for value in values {
                update_string(&mut hasher, value);
            }
        }
        hasher.update(&[self.effect_policy as u8, u8::from(self.evidence_required)]);
        update_budget(&mut hasher, self.budget_policy.total);
        update_budget(&mut hasher, self.budget_policy.finalization_reserve);
        update_budget(&mut hasher, self.budget_policy.recovery_reserve);
        match self.parent_contract_hash {
            Some(parent) => {
                hasher.update(&[1]);
                hasher.update(&parent);
                update_string(
                    &mut hasher,
                    self.evolution_reason.as_deref().unwrap_or_default(),
                );
            }
            None => {
                hasher.update(&[0]);
            }
        }
        *hasher.finalize().as_bytes()
    }

    pub fn is_self_consistent(&self) -> bool {
        self.schema == GT96_CONTRACT_SCHEMA_V1
            && self.version > 0
            && ((self.version == 1
                && self.parent_contract_hash.is_none()
                && self.evolution_reason.is_none())
                || (self.version > 1
                    && self
                        .parent_contract_hash
                        .is_some_and(|hash| hash != [0; 32])
                    && self
                        .evolution_reason
                        .as_deref()
                        .is_some_and(|reason| !reason.trim().is_empty())))
            && self.contract_hash == self.compute_hash()
            && self.budget_policy.validate().is_ok()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArtifactValidationState {
    Unvalidated,
    Validated,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatorProof {
    pub criterion_id: String,
    pub contract_hash: [u8; 32],
    pub validator_id: String,
    pub artifact_content_hash: [u8; 32],
    pub verdict: bool,
    pub proof_hash: [u8; 32],
}

impl ValidatorProof {
    pub fn new(
        criterion_id: impl Into<String>,
        contract_hash: [u8; 32],
        validator_id: impl Into<String>,
        artifact_content_hash: [u8; 32],
        verdict: bool,
    ) -> Result<Self, ContractError> {
        let proof = Self {
            criterion_id: criterion_id.into(),
            contract_hash,
            validator_id: validator_id.into(),
            artifact_content_hash,
            verdict,
            proof_hash: [0; 32],
        };
        if proof.criterion_id.trim().is_empty()
            || proof.validator_id.trim().is_empty()
            || proof.contract_hash == [0; 32]
            || proof.artifact_content_hash == [0; 32]
            || !verdict
        {
            return Err(ContractError::InvalidEvidence);
        }
        let mut proof = proof;
        proof.proof_hash = proof.compute_hash();
        Ok(proof)
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-gt96-validator-proof-v1");
        update_string(&mut hasher, &self.criterion_id);
        hasher.update(&self.contract_hash);
        update_string(&mut hasher, &self.validator_id);
        hasher.update(&self.artifact_content_hash);
        hasher.update(&[u8::from(self.verdict)]);
        *hasher.finalize().as_bytes()
    }

    pub fn is_valid(&self) -> bool {
        self.verdict && self.proof_hash != [0; 32] && self.proof_hash == self.compute_hash()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactRef {
    pub producer: String,
    pub commit_sha: [u8; 32],
    pub schema: String,
    pub trust_domain: String,
    pub validation_state: ArtifactValidationState,
    pub created_at_ms: u64,
    pub content_hash: [u8; 32],
    pub criterion_id: Option<String>,
    pub contract_hash: Option<[u8; 32]>,
    pub validator_id: Option<String>,
    pub validation_hash: [u8; 32],
}

impl ArtifactRef {
    pub fn validated(
        producer: impl Into<String>,
        commit_sha: [u8; 32],
        schema: impl Into<String>,
        trust_domain: impl Into<String>,
        created_at_ms: u64,
        content: &[u8],
    ) -> Result<Self, ContractError> {
        let producer = producer.into();
        let schema = schema.into();
        let trust_domain = trust_domain.into();
        if producer.trim().is_empty()
            || schema.trim().is_empty()
            || trust_domain.trim().is_empty()
            || commit_sha == [0; 32]
            || content.is_empty()
        {
            return Err(ContractError::InvalidEvidence);
        }
        Ok(Self {
            producer,
            commit_sha,
            schema,
            trust_domain,
            // This constructor validates artifact metadata only. A criterion
            // or contract cannot be marked verified until `validated_for`
            // or `validated_with_proof` adds an independent, bound validator
            // proof.  In particular, the public constructor cannot self-claim
            // authoritative validation by setting the state alone.
            validation_state: ArtifactValidationState::Unvalidated,
            created_at_ms,
            content_hash: *blake3::hash(content).as_bytes(),
            criterion_id: None,
            contract_hash: None,
            validator_id: None,
            validation_hash: [0; 32],
        })
    }

    pub fn validated_for(
        criterion_id: impl Into<String>,
        contract: &GoalContract,
        validator_id: impl Into<String>,
        producer: impl Into<String>,
        commit_sha: [u8; 32],
        schema: impl Into<String>,
        trust_domain: impl Into<String>,
        created_at_ms: u64,
        content: &[u8],
    ) -> Result<Self, ContractError> {
        let criterion_id = criterion_id.into();
        let validator_id = validator_id.into();
        if criterion_id.trim().is_empty() || validator_id.trim().is_empty() {
            return Err(ContractError::InvalidEvidence);
        }
        if !contract
            .acceptance_criteria
            .iter()
            .any(|criterion| criterion.id == criterion_id)
        {
            return Err(ContractError::MissingCriterion);
        }
        let artifact = Self::validated(
            producer,
            commit_sha,
            schema,
            trust_domain,
            created_at_ms,
            content,
        )?;
        let proof = ValidatorProof::new(
            criterion_id,
            contract.contract_hash,
            validator_id,
            artifact.content_hash,
            true,
        )?;
        Self::validated_with_proof(artifact, &proof)
    }

    pub fn validated_with_proof(
        mut artifact: Self,
        proof: &ValidatorProof,
    ) -> Result<Self, ContractError> {
        if !proof.is_valid() || proof.artifact_content_hash != artifact.content_hash {
            return Err(ContractError::InvalidEvidence);
        }
        artifact.criterion_id = Some(proof.criterion_id.clone());
        artifact.contract_hash = Some(proof.contract_hash);
        artifact.validator_id = Some(proof.validator_id.clone());
        artifact.validation_state = ArtifactValidationState::Validated;
        artifact.validation_hash = artifact.compute_validation_hash();
        Ok(artifact)
    }

    fn compute_validation_hash(&self) -> [u8; 32] {
        let mut hasher = Hasher::new();
        hasher.update(b"AEGIS-GT96-ARTIFACT-VALIDATION\0");
        update_string(&mut hasher, &self.producer);
        hasher.update(&self.commit_sha);
        update_string(&mut hasher, &self.schema);
        update_string(&mut hasher, &self.trust_domain);
        update_u64(&mut hasher, self.created_at_ms);
        hasher.update(&self.content_hash);
        update_string(
            &mut hasher,
            self.criterion_id.as_deref().unwrap_or_default(),
        );
        if let Some(contract_hash) = self.contract_hash {
            hasher.update(&[1]);
            hasher.update(&contract_hash);
        } else {
            hasher.update(&[0]);
        }
        update_string(
            &mut hasher,
            self.validator_id.as_deref().unwrap_or_default(),
        );
        *hasher.finalize().as_bytes()
    }

    fn is_validated_for(&self, criterion_id: &str, contract_hash: [u8; 32]) -> bool {
        self.is_validated()
            && self.criterion_id.as_deref() == Some(criterion_id)
            && self.contract_hash == Some(contract_hash)
            && self.validation_hash == self.compute_validation_hash()
    }

    pub fn is_validated(&self) -> bool {
        self.validation_state == ArtifactValidationState::Validated
            && self.commit_sha != [0; 32]
            && self.content_hash != [0; 32]
            && !self.producer.trim().is_empty()
            && !self.schema.trim().is_empty()
            && !self.trust_domain.trim().is_empty()
            && self
                .criterion_id
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty())
            && self.contract_hash.is_some_and(|value| value != [0; 32])
            && self
                .validator_id
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty())
            && self.validation_hash != [0; 32]
            && self.validation_hash == self.compute_validation_hash()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProgressLedger {
    contract_hash: [u8; 32],
    criteria: BTreeMap<String, AcceptanceCriterion>,
    evidence: BTreeMap<String, ArtifactRef>,
    state_epoch: u64,
}

impl ProgressLedger {
    pub fn new(contract: &GoalContract) -> Self {
        let criteria = contract
            .acceptance_criteria
            .iter()
            .cloned()
            .map(|criterion| (criterion.id.clone(), criterion))
            .collect();
        Self {
            contract_hash: contract.contract_hash,
            criteria,
            evidence: BTreeMap::new(),
            state_epoch: 0,
        }
    }

    pub fn state_epoch(&self) -> u64 {
        self.state_epoch
    }

    pub fn status(&self, criterion_id: &str) -> Option<CriterionStatus> {
        self.criteria
            .get(criterion_id)
            .map(|criterion| criterion.status)
    }

    pub fn mark_in_progress(&mut self, criterion_id: &str) -> Result<(), ContractError> {
        let criterion = self
            .criteria
            .get_mut(criterion_id)
            .ok_or(ContractError::MissingCriterion)?;
        if matches!(criterion.status, CriterionStatus::Verified) {
            return Err(ContractError::InvalidTransition);
        }
        criterion.status = CriterionStatus::InProgress;
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn mark_verified(
        &mut self,
        criterion_id: &str,
        evidence: ArtifactRef,
    ) -> Result<(), ContractError> {
        if !evidence.is_validated_for(criterion_id, self.contract_hash) {
            return Err(ContractError::InvalidEvidence);
        }
        let criterion = self
            .criteria
            .get_mut(criterion_id)
            .ok_or(ContractError::MissingCriterion)?;
        if matches!(
            criterion.status,
            CriterionStatus::Verified | CriterionStatus::Invalidated
        ) {
            return Err(ContractError::InvalidTransition);
        }
        criterion.status = CriterionStatus::Verified;
        self.evidence.insert(criterion_id.to_string(), evidence);
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn invalidate(&mut self, criterion_id: &str) -> Result<(), ContractError> {
        let criterion = self
            .criteria
            .get_mut(criterion_id)
            .ok_or(ContractError::MissingCriterion)?;
        criterion.status = CriterionStatus::Invalidated;
        self.evidence.remove(criterion_id);
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn evidence(&self, criterion_id: &str) -> Option<&ArtifactRef> {
        self.evidence.get(criterion_id)
    }

    pub fn closure(&self) -> (usize, usize) {
        let closed = self
            .criteria
            .values()
            .filter(|criterion| criterion.status == CriterionStatus::Verified)
            .count();
        (closed, self.criteria.len())
    }

    pub fn is_closed(&self) -> bool {
        let (closed, total) = self.closure();
        total > 0 && closed == total && self.evidence.len() == total
    }

    pub fn binds_contract(&self, contract: &GoalContract) -> bool {
        self.contract_hash == contract.contract_hash
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BudgetLedger {
    total: BudgetVector,
    exploration_remaining: BudgetVector,
    finalization_remaining: BudgetVector,
    recovery_remaining: BudgetVector,
    exploration_spent: BudgetVector,
    state_epoch: u64,
}

impl BudgetLedger {
    pub fn new(policy: &BudgetPolicy) -> Result<Self, ContractError> {
        policy.validate()?;
        let reserved = policy
            .finalization_reserve
            .checked_add(policy.recovery_reserve)?;
        let exploration_remaining = policy.total.checked_sub(reserved)?;
        Ok(Self {
            total: policy.total,
            exploration_remaining,
            finalization_remaining: policy.finalization_reserve,
            recovery_remaining: policy.recovery_reserve,
            exploration_spent: BudgetVector::zero(),
            state_epoch: 0,
        })
    }

    pub fn remaining(&self) -> BudgetVector {
        // The pools are disjoint by construction. Their sum is therefore the
        // only remaining authority; no caller can spend a reserve twice.
        self.exploration_remaining
            .checked_add(self.finalization_remaining)
            .and_then(|value| value.checked_add(self.recovery_remaining))
            .expect("budget pools cannot overflow while bounded by total")
    }

    pub fn finalization_reserve(&self) -> BudgetVector {
        self.finalization_remaining
    }

    pub fn recovery_reserve(&self) -> BudgetVector {
        self.recovery_remaining
    }

    pub fn exploration_reserve(&self) -> BudgetVector {
        self.exploration_remaining
    }

    pub fn snapshot_valid(&self) -> bool {
        self.remaining()
            .checked_add(self.exploration_spent)
            .is_ok_and(|committed| committed.fits_within(self.total))
    }

    pub fn state_epoch(&self) -> u64 {
        self.state_epoch
    }

    pub fn consume_exploration(&mut self, spend: BudgetVector) -> Result<(), ContractError> {
        let next_remaining = self.exploration_remaining.checked_sub(spend)?;
        let next_spent = self.exploration_spent.checked_add(spend)?;
        self.exploration_remaining = next_remaining;
        self.exploration_spent = next_spent;
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn consume_finalizer(&mut self, spend: BudgetVector) -> Result<(), ContractError> {
        self.finalization_remaining = self.finalization_remaining.checked_sub(spend)?;
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn consume_recovery(&mut self, spend: BudgetVector) -> Result<(), ContractError> {
        self.recovery_remaining = self.recovery_remaining.checked_sub(spend)?;
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn allocate_child(&mut self, allocation: BudgetVector) -> Result<(), ContractError> {
        self.consume_exploration(allocation)
    }

    pub fn refund(&mut self, value: BudgetVector) -> Result<(), ContractError> {
        // Legacy refund has no lane argument, so it can only return previously
        // consumed exploration authority. Validate first so a failed refund is
        // atomic and cannot corrupt the ledger or epoch.
        let next_spent = self.exploration_spent.checked_sub(value)?;
        let next_remaining = self.exploration_remaining.checked_add(value)?;
        let total_remaining = next_remaining
            .checked_add(self.finalization_remaining)?
            .checked_add(self.recovery_remaining)?;
        if !total_remaining.fits_within(self.total) {
            return Err(ContractError::InvalidBudget);
        }
        self.exploration_spent = next_spent;
        self.exploration_remaining = next_remaining;
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FinalizationState {
    Exploring,
    Finalizing,
    Reopened,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FinalizationBoundary {
    state: FinalizationState,
    generation: u64,
}

impl Default for FinalizationBoundary {
    fn default() -> Self {
        Self {
            state: FinalizationState::Exploring,
            generation: 0,
        }
    }
}

impl FinalizationBoundary {
    pub fn state(&self) -> FinalizationState {
        self.state
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn begin(&mut self) -> Result<(), ContractError> {
        if self.state == FinalizationState::Finalizing {
            return Err(ContractError::InvalidTransition);
        }
        self.state = FinalizationState::Finalizing;
        self.generation = self.generation.saturating_add(1);
        Ok(())
    }

    pub fn reopen_for_critical_gap(&mut self) -> Result<(), ContractError> {
        if self.state != FinalizationState::Finalizing {
            return Err(ContractError::InvalidTransition);
        }
        self.state = FinalizationState::Reopened;
        self.generation = self.generation.saturating_add(1);
        Ok(())
    }

    pub fn exploration_allowed(&self) -> bool {
        self.state != FinalizationState::Finalizing
    }

    pub fn finalizer_allowed(&self) -> bool {
        matches!(
            self.state,
            FinalizationState::Finalizing | FinalizationState::Reopened
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeInstance {
    pub instance_id: u128,
    pub parent_id: Option<u128>,
    pub state: InstanceState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InstanceState {
    Proposed,
    Admitted,
    Running,
    Cancelled,
    Completed,
    Failed,
}

impl InstanceState {
    fn can_transition_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (Self::Proposed, Self::Admitted | Self::Cancelled)
                | (Self::Admitted, Self::Running | Self::Cancelled)
                | (
                    Self::Running,
                    Self::Completed | Self::Failed | Self::Cancelled
                )
        )
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct RuntimeInstanceGraph {
    instances: BTreeMap<u128, RuntimeInstance>,
    state_epoch: u64,
}

impl RuntimeInstanceGraph {
    pub fn insert(&mut self, instance: RuntimeInstance) -> Result<(), ContractError> {
        if instance.instance_id == 0
            || self.instances.contains_key(&instance.instance_id)
            || instance
                .parent_id
                .is_some_and(|parent| parent == instance.instance_id)
        {
            return Err(ContractError::InvalidTransition);
        }
        if let Some(parent) = instance.parent_id {
            if !self.instances.contains_key(&parent) {
                return Err(ContractError::OrphanInstance);
            }
        }
        self.instances.insert(instance.instance_id, instance);
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn transition(
        &mut self,
        instance_id: u128,
        next: InstanceState,
    ) -> Result<(), ContractError> {
        let instance = self
            .instances
            .get_mut(&instance_id)
            .ok_or(ContractError::UnknownInstance)?;
        if !instance.state.can_transition_to(next) {
            return Err(ContractError::InvalidTransition);
        }
        instance.state = next;
        self.state_epoch = self.state_epoch.saturating_add(1);
        Ok(())
    }

    pub fn instance(&self, instance_id: u128) -> Option<&RuntimeInstance> {
        self.instances.get(&instance_id)
    }

    pub fn state_epoch(&self) -> u64 {
        self.state_epoch
    }

    pub fn all_authoritative_instances_terminal(&self) -> bool {
        !self.instances.is_empty()
            && self.instances.values().all(|instance| {
                matches!(
                    instance.state,
                    InstanceState::Cancelled | InstanceState::Completed | InstanceState::Failed
                )
            })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProgressDecision {
    Continue,
    Escalate,
    Stop,
}

#[derive(Clone, Debug)]
pub struct NoProgressDetector {
    escalation_after: u32,
    stop_after: u32,
    last_signature: Option<[u8; 32]>,
    repeated: u32,
}

impl NoProgressDetector {
    pub fn new(escalation_after: u32, stop_after: u32) -> Result<Self, ContractError> {
        if escalation_after == 0 || stop_after < escalation_after {
            return Err(ContractError::InvalidBudget);
        }
        Ok(Self {
            escalation_after,
            stop_after,
            last_signature: None,
            repeated: 0,
        })
    }

    pub fn observe(&mut self, signature: [u8; 32]) -> ProgressDecision {
        if self.last_signature == Some(signature) {
            self.repeated = self.repeated.saturating_add(1);
        } else {
            self.last_signature = Some(signature);
            self.repeated = 1;
        }
        if self.repeated >= self.stop_after {
            ProgressDecision::Stop
        } else if self.repeated >= self.escalation_after {
            ProgressDecision::Escalate
        } else {
            ProgressDecision::Continue
        }
    }

    pub fn repeated(&self) -> u32 {
        self.repeated
    }
}

#[derive(Clone, Debug)]
pub struct CycleDetector {
    capacity: usize,
    transitions: VecDeque<[u8; 32]>,
}

impl CycleDetector {
    pub fn new(capacity: usize) -> Result<Self, ContractError> {
        if capacity < 2 {
            return Err(ContractError::InvalidBudget);
        }
        Ok(Self {
            capacity,
            transitions: VecDeque::with_capacity(capacity),
        })
    }

    pub fn observe(&mut self, previous: [u8; 32], next: [u8; 32], action: &str) -> bool {
        let mut hasher = Hasher::new();
        hasher.update(&previous);
        hasher.update(&next);
        update_string(&mut hasher, action);
        let signature = *hasher.finalize().as_bytes();
        let repeated = self.transitions.contains(&signature);
        self.transitions.push_back(signature);
        while self.transitions.len() > self.capacity {
            self.transitions.pop_front();
        }
        repeated
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResultContract {
    pub result_hash: [u8; 32],
    pub byte_len: u64,
    pub evidence_refs: Vec<ArtifactRef>,
}

impl ResultContract {
    pub fn new(result: &[u8], evidence_refs: Vec<ArtifactRef>) -> Result<Self, ContractError> {
        if result.is_empty()
            || evidence_refs
                .iter()
                .any(|reference| !reference.is_validated())
        {
            return Err(ContractError::InvalidEvidence);
        }
        Ok(Self {
            result_hash: *blake3::hash(result).as_bytes(),
            byte_len: result.len() as u64,
            evidence_refs,
        })
    }

    pub fn is_authoritative(&self) -> bool {
        !self.evidence_refs.is_empty() && self.evidence_refs.iter().all(ArtifactRef::is_validated)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InferenceCacheIdentity {
    pub model: String,
    pub model_revision: String,
    pub provider_runtime: String,
    pub tokenizer: String,
    pub chat_template: String,
    pub adapter: String,
    pub tools_hash: [u8; 32],
    pub system_prompt_hash: [u8; 32],
    pub input_hash: [u8; 32],
    pub inference_config_hash: [u8; 32],
    pub trust_domain: String,
}

impl InferenceCacheIdentity {
    pub fn key_hash(&self) -> Result<[u8; 32], ContractError> {
        if [
            self.model.trim(),
            self.model_revision.trim(),
            self.provider_runtime.trim(),
            self.tokenizer.trim(),
            self.chat_template.trim(),
            self.adapter.trim(),
            self.trust_domain.trim(),
        ]
        .iter()
        .any(|value| value.is_empty())
            || self.tools_hash == [0; 32]
            || self.system_prompt_hash == [0; 32]
            || self.input_hash == [0; 32]
            || self.inference_config_hash == [0; 32]
        {
            return Err(ContractError::InvalidEvidence);
        }
        let mut hasher = Hasher::new();
        for value in [
            &self.model,
            &self.model_revision,
            &self.provider_runtime,
            &self.tokenizer,
            &self.chat_template,
            &self.adapter,
            &self.trust_domain,
        ] {
            update_string(&mut hasher, value);
        }
        for hash in [
            self.tools_hash,
            self.system_prompt_hash,
            self.input_hash,
            self.inference_config_hash,
        ] {
            hasher.update(&hash);
        }
        Ok(*hasher.finalize().as_bytes())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SemanticCacheDisposition {
    CandidateOnly,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SemanticCacheCandidate {
    pub key_hash: [u8; 32],
    pub content_hash: [u8; 32],
    pub disposition: SemanticCacheDisposition,
}

impl SemanticCacheCandidate {
    pub fn is_authoritative(&self) -> bool {
        false
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub enum ProviderCapability {
    Streaming,
    ToolCalling,
    StructuredOutput,
    ReasoningControls,
    PromptCaching,
    BatchMode,
    KvControls,
    SpeculativeDecoding,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ProviderCapabilitySet {
    capabilities: BTreeSet<ProviderCapability>,
}

impl ProviderCapabilitySet {
    pub fn from<I>(capabilities: I) -> Self
    where
        I: IntoIterator<Item = ProviderCapability>,
    {
        Self {
            capabilities: capabilities.into_iter().collect(),
        }
    }

    pub fn supports(&self, capability: ProviderCapability) -> bool {
        self.capabilities.contains(&capability)
    }

    pub fn supports_all(&self, required: &[ProviderCapability]) -> bool {
        required.iter().all(|capability| self.supports(*capability))
    }

    pub fn degrade_to_supported(
        &self,
        requested: &[ProviderCapability],
    ) -> Vec<ProviderCapability> {
        requested
            .iter()
            .copied()
            .filter(|capability| self.supports(*capability))
            .collect()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetryDisposition {
    SafeBounded,
    RequiresCompensation,
    NeverAutomatic,
}

pub fn retry_disposition(effect: SideEffectClass) -> RetryDisposition {
    match effect {
        SideEffectClass::None
        | SideEffectClass::ExternalRead
        | SideEffectClass::LocalReversible => RetryDisposition::SafeBounded,
        SideEffectClass::ExternalWrite => RetryDisposition::RequiresCompensation,
        SideEffectClass::Destructive | SideEffectClass::FinancialLegal => {
            RetryDisposition::NeverAutomatic
        }
    }
}

fn update_string(hasher: &mut Hasher, value: &str) {
    hasher.update(&(value.len() as u64).to_le_bytes());
    hasher.update(value.as_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_budget(hasher: &mut Hasher, budget: BudgetVector) {
    for value in [
        budget.tokens,
        budget.money_minor_units,
        budget.time_ms,
        budget.tool_calls,
        budget.api_calls,
        budget.cpu_ms,
        budget.risk_units,
    ] {
        update_u64(hasher, value);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> BudgetPolicy {
        BudgetPolicy {
            total: BudgetVector {
                tokens: 100,
                money_minor_units: 10,
                time_ms: 100,
                tool_calls: 10,
                api_calls: 10,
                cpu_ms: 100,
                risk_units: 10,
            },
            finalization_reserve: BudgetVector {
                tokens: 20,
                money_minor_units: 2,
                time_ms: 20,
                tool_calls: 2,
                api_calls: 2,
                cpu_ms: 20,
                risk_units: 2,
            },
            recovery_reserve: BudgetVector {
                tokens: 10,
                money_minor_units: 1,
                time_ms: 10,
                tool_calls: 1,
                api_calls: 1,
                cpu_ms: 10,
                risk_units: 1,
            },
        }
    }

    fn criterion(id: &str) -> AcceptanceCriterion {
        AcceptanceCriterion {
            id: id.to_string(),
            description: format!("criterion {id}"),
            status: CriterionStatus::Open,
        }
    }

    fn contract() -> GoalContract {
        GoalContract::new(
            "close the local remediation",
            vec![criterion("c1"), criterion("c2")],
            vec!["do not push remotely".to_string()],
            vec!["evidence is current-SHA bound".to_string()],
            vec!["do not claim production readiness".to_string()],
            SideEffectClass::LocalReversible,
            true,
            policy(),
        )
        .unwrap()
    }

    fn evidence(criterion_id: &str) -> ArtifactRef {
        ArtifactRef::validated_for(
            criterion_id,
            &contract(),
            "gt96-independent-validator",
            "gt96-tests",
            [7; 32],
            "test-report-v1",
            "local",
            1,
            b"current evidence",
        )
        .unwrap()
    }

    #[test]
    fn goal_contract_is_versioned_and_cannot_evolve_from_stale_version() {
        let current = contract();
        assert!(current.is_self_consistent());
        assert_eq!(
            current.evolve(0, "wrong", vec![criterion("c1")]),
            Err(ContractError::InvalidVersion)
        );
        let next = current
            .evolve(1, "updated", vec![criterion("c1"), criterion("c2")])
            .unwrap();
        assert_eq!(next.version, 2);
        assert_ne!(next.contract_hash, current.contract_hash);
    }

    #[test]
    fn goal_contract_rejects_empty_fields_duplicates_and_removed_criteria() {
        assert_eq!(
            GoalContract::new(
                "",
                vec![criterion("c1")],
                vec!["constraint".to_string()],
                vec!["invariant".to_string()],
                vec!["non-goal".to_string()],
                SideEffectClass::None,
                true,
                policy(),
            ),
            Err(ContractError::EmptyField)
        );
        assert_eq!(
            GoalContract::new(
                "objective",
                vec![criterion("c1"), criterion("c1")],
                vec!["constraint".to_string()],
                vec!["invariant".to_string()],
                vec!["non-goal".to_string()],
                SideEffectClass::None,
                true,
                policy(),
            ),
            Err(ContractError::DuplicateCriterion)
        );
        let current = contract();
        assert_eq!(
            current.evolve(1, "updated", vec![criterion("c1")]),
            Err(ContractError::MissingCriterion)
        );
    }

    #[test]
    fn progress_cannot_be_verified_without_validated_evidence_and_closes_explicitly() {
        let current = contract();
        let mut ledger = ProgressLedger::new(&current);
        ledger.mark_in_progress("c1").unwrap();
        assert_eq!(ledger.status("c1"), Some(CriterionStatus::InProgress));
        ledger.mark_verified("c1", evidence("c1")).unwrap();
        ledger.mark_verified("c2", evidence("c2")).unwrap();
        assert!(ledger.is_closed());
        ledger.invalidate("c1").unwrap();
        assert!(!ledger.is_closed());
        assert!(ledger.evidence("c1").is_none());
    }

    #[test]
    fn validator_proof_binds_artifact_to_exact_criterion_and_contract() {
        let current = contract();
        let artifact = ArtifactRef::validated(
            "gt96-tests",
            [7; 32],
            "test-report-v1",
            "local",
            1,
            b"current evidence",
        )
        .unwrap();
        let proof = ValidatorProof::new(
            "c1",
            current.contract_hash,
            "independent-validator",
            artifact.content_hash,
            true,
        )
        .unwrap();
        let bound = ArtifactRef::validated_with_proof(artifact, &proof).unwrap();
        let mut ledger = ProgressLedger::new(&current);
        ledger.mark_verified("c1", bound.clone()).unwrap();
        assert_eq!(
            ledger.mark_verified("c2", bound),
            Err(ContractError::InvalidEvidence)
        );
    }

    #[test]
    fn metadata_only_artifact_cannot_self_claim_authoritative_validation() {
        let artifact = ArtifactRef::validated(
            "gt96-tests",
            [7; 32],
            "test-report-v1",
            "local",
            1,
            b"metadata only",
        )
        .unwrap();
        assert!(!artifact.is_validated());
        assert_eq!(
            ResultContract::new(b"result", vec![artifact]),
            Err(ContractError::InvalidEvidence)
        );

        let mut forged = ArtifactRef::validated(
            "gt96-tests",
            [7; 32],
            "test-report-v1",
            "local",
            1,
            b"metadata only",
        )
        .unwrap();
        forged.validation_state = ArtifactValidationState::Validated;
        assert!(!forged.is_validated());
    }

    #[test]
    fn budget_never_spends_the_finalization_or_recovery_reserve_for_exploration() {
        let mut ledger = BudgetLedger::new(&policy()).unwrap();
        assert!(
            ledger
                .consume_exploration(BudgetVector {
                    tokens: 70,
                    money_minor_units: 7,
                    time_ms: 70,
                    tool_calls: 7,
                    api_calls: 7,
                    cpu_ms: 70,
                    risk_units: 7,
                })
                .is_ok()
        );
        assert_eq!(ledger.remaining().tokens, 30);
        assert!(
            ledger
                .consume_exploration(BudgetVector {
                    tokens: 1,
                    ..BudgetVector::zero()
                })
                .is_err()
        );
        assert!(
            ledger
                .consume_finalizer(BudgetVector {
                    tokens: 20,
                    money_minor_units: 2,
                    time_ms: 20,
                    tool_calls: 2,
                    api_calls: 2,
                    cpu_ms: 20,
                    risk_units: 2,
                })
                .is_ok()
        );
    }

    #[test]
    fn finalizer_cannot_borrow_exploration_or_recovery_budget() {
        let mut ledger = BudgetLedger::new(&policy()).unwrap();
        ledger
            .consume_exploration(BudgetVector {
                tokens: 70,
                money_minor_units: 7,
                time_ms: 70,
                tool_calls: 7,
                api_calls: 7,
                cpu_ms: 70,
                risk_units: 7,
            })
            .unwrap();
        assert_eq!(
            ledger.consume_finalizer(BudgetVector {
                tokens: 21,
                ..BudgetVector::zero()
            }),
            Err(ContractError::BudgetExceeded)
        );
        assert_eq!(ledger.finalization_reserve().tokens, 20);
        assert_eq!(ledger.recovery_reserve().tokens, 10);
        assert_eq!(ledger.remaining().tokens, 30);
    }

    #[test]
    fn failed_refund_is_atomic_and_cannot_mint_budget() {
        let mut ledger = BudgetLedger::new(&policy()).unwrap();
        let epoch = ledger.state_epoch();
        assert_eq!(
            ledger.refund(BudgetVector {
                tokens: 1,
                ..BudgetVector::zero()
            }),
            Err(ContractError::BudgetExceeded)
        );
        assert_eq!(ledger.state_epoch(), epoch);
        assert_eq!(ledger.remaining().tokens, 100);
        ledger
            .consume_exploration(BudgetVector {
                tokens: 5,
                ..BudgetVector::zero()
            })
            .unwrap();
        ledger
            .refund(BudgetVector {
                tokens: 5,
                ..BudgetVector::zero()
            })
            .unwrap();
        assert_eq!(ledger.remaining().tokens, 100);
    }

    #[test]
    fn finalization_boundary_blocks_exploration_and_allows_explicit_reopen() {
        let mut boundary = FinalizationBoundary::default();
        boundary.begin().unwrap();
        assert!(!boundary.exploration_allowed());
        assert!(boundary.finalizer_allowed());
        boundary.reopen_for_critical_gap().unwrap();
        assert!(boundary.exploration_allowed());
        assert!(boundary.finalizer_allowed());
    }

    #[test]
    fn runtime_instance_graph_rejects_orphans_and_illegal_transitions() {
        let mut graph = RuntimeInstanceGraph::default();
        assert_eq!(
            graph.insert(RuntimeInstance {
                instance_id: 2,
                parent_id: Some(1),
                state: InstanceState::Proposed,
            }),
            Err(ContractError::OrphanInstance)
        );
        graph
            .insert(RuntimeInstance {
                instance_id: 1,
                parent_id: None,
                state: InstanceState::Proposed,
            })
            .unwrap();
        graph
            .insert(RuntimeInstance {
                instance_id: 2,
                parent_id: Some(1),
                state: InstanceState::Proposed,
            })
            .unwrap();
        graph.transition(1, InstanceState::Admitted).unwrap();
        assert_eq!(
            graph.transition(1, InstanceState::Completed),
            Err(ContractError::InvalidTransition)
        );
    }

    #[test]
    fn no_progress_and_cycle_detectors_require_state_evidence() {
        let mut detector = NoProgressDetector::new(2, 3).unwrap();
        assert_eq!(detector.observe([1; 32]), ProgressDecision::Continue);
        assert_eq!(detector.observe([1; 32]), ProgressDecision::Escalate);
        assert_eq!(detector.observe([1; 32]), ProgressDecision::Stop);

        let mut cycle = CycleDetector::new(4).unwrap();
        assert!(!cycle.observe([1; 32], [2; 32], "fix"));
        assert!(cycle.observe([1; 32], [2; 32], "fix"));
    }

    #[test]
    fn result_evidence_and_cache_identity_are_separate_and_complete() {
        let result = ResultContract::new(b"result", vec![evidence("c1")]).unwrap();
        assert!(result.is_authoritative());
        let identity = InferenceCacheIdentity {
            model: "model".to_string(),
            model_revision: "rev".to_string(),
            provider_runtime: "runtime".to_string(),
            tokenizer: "tokenizer".to_string(),
            chat_template: "template".to_string(),
            adapter: "none".to_string(),
            tools_hash: [1; 32],
            system_prompt_hash: [2; 32],
            input_hash: [3; 32],
            inference_config_hash: [4; 32],
            trust_domain: "local".to_string(),
        };
        let key = identity.key_hash().unwrap();
        let mut changed = identity.clone();
        changed.system_prompt_hash = [5; 32];
        assert_ne!(key, changed.key_hash().unwrap());
        let candidate = SemanticCacheCandidate {
            key_hash: key,
            content_hash: [6; 32],
            disposition: SemanticCacheDisposition::CandidateOnly,
        };
        assert!(!candidate.is_authoritative());
    }

    #[test]
    fn retry_disposition_is_bounded_by_effect_semantics() {
        assert_eq!(
            retry_disposition(SideEffectClass::None),
            RetryDisposition::SafeBounded
        );
        assert_eq!(
            retry_disposition(SideEffectClass::ExternalRead),
            RetryDisposition::SafeBounded
        );
        assert_eq!(
            retry_disposition(SideEffectClass::LocalReversible),
            RetryDisposition::SafeBounded
        );
        assert_eq!(
            retry_disposition(SideEffectClass::ExternalWrite),
            RetryDisposition::RequiresCompensation
        );
        assert_eq!(
            retry_disposition(SideEffectClass::Destructive),
            RetryDisposition::NeverAutomatic
        );
        assert_eq!(
            retry_disposition(SideEffectClass::FinancialLegal),
            RetryDisposition::NeverAutomatic
        );
    }

    #[test]
    fn provider_capabilities_degrade_without_claiming_unsupported_features() {
        let capabilities = ProviderCapabilitySet::from([
            ProviderCapability::Streaming,
            ProviderCapability::ToolCalling,
        ]);
        assert!(!capabilities.supports_all(&[
            ProviderCapability::Streaming,
            ProviderCapability::StructuredOutput,
        ]));
        assert_eq!(
            capabilities.degrade_to_supported(&[
                ProviderCapability::Streaming,
                ProviderCapability::StructuredOutput,
            ]),
            vec![ProviderCapability::Streaming]
        );
    }
}
