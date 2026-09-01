//! A deterministic laboratory runtime for research-oriented agent missions.
//!
//! The lab is intentionally a small kernel, not another model orchestrator.
//! It owns mission identity, evidence-bearing records, event ordering and
//! benchmark admission. Provider/browser/experiment implementations remain
//! adapters at the edge and must commit through these records.

use blake3::Hasher;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

use crate::gt96::{BudgetPolicy, ContractError};

const LAB_SCHEMA: &str = "aegis-lab-runtime-v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum LabRunState {
    Planned,
    Researching,
    Experimenting,
    Reviewing,
    Completed,
    Blocked,
    Aborted,
}

impl LabRunState {
    pub fn can_transition_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (
                Self::Planned,
                Self::Researching | Self::Aborted | Self::Blocked
            ) | (
                Self::Researching,
                Self::Experimenting | Self::Reviewing | Self::Blocked | Self::Aborted
            ) | (
                Self::Experimenting,
                Self::Researching | Self::Reviewing | Self::Blocked | Self::Aborted
            ) | (
                Self::Reviewing,
                Self::Completed | Self::Researching | Self::Blocked | Self::Aborted
            ) | (Self::Blocked, Self::Researching | Self::Aborted)
                | (Self::Completed, Self::Blocked | Self::Aborted)
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum LabEventKind {
    MissionCreated,
    StateChanged,
    SourceCaptured,
    ClaimRecorded,
    HypothesisRecorded,
    ExperimentScheduled,
    ExperimentExecutionAdmitted,
    ExperimentExecutionRecorded,
    ToolExecutionAdmitted,
    ToolExecutionRecorded,
    ObservationRecorded,
    BrowserActionAdmitted,
    BrowserActionRecorded,
    BrowserObservationAdmitted,
    BrowserObservationRecorded,
    BenchmarkEvaluated,
    ReviewRecorded,
    ResearchProgramAdmitted,
    ResearchProgramExecuted,
    SecurityEventRecorded,
    BlockerRecorded,
    BlockerResolved,
    SkillAdmissionRecorded,
    SkillExecutionRecorded,
    CancellationAdmitted,
    CancellationRecorded,
    BudgetAdmitted,
    FinalizationStarted,
    FinalizationReopened,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LabMissionSpec {
    pub mission_id: String,
    pub objective: String,
    pub scope: String,
    pub contract_hash: [u8; 32],
    pub budget_policy: BudgetPolicy,
    pub required_evidence: bool,
    pub max_steps: u32,
    pub schema: String,
}

impl LabMissionSpec {
    pub fn new(
        mission_id: impl Into<String>,
        objective: impl Into<String>,
        scope: impl Into<String>,
        contract_hash: [u8; 32],
        budget_policy: BudgetPolicy,
        required_evidence: bool,
        max_steps: u32,
    ) -> Result<Self, LabError> {
        let mission = Self {
            mission_id: mission_id.into(),
            objective: objective.into(),
            scope: scope.into(),
            contract_hash,
            budget_policy,
            required_evidence,
            max_steps,
            schema: LAB_SCHEMA.to_string(),
        };
        mission.validate()?;
        Ok(mission)
    }

    pub fn validate(&self) -> Result<(), LabError> {
        if self.schema != LAB_SCHEMA
            || self.mission_id.trim().is_empty()
            || self.objective.trim().is_empty()
            || self.scope.trim().is_empty()
            || self.contract_hash == [0; 32]
            || self.max_steps == 0
        {
            return Err(LabError::InvalidMission);
        }
        self.budget_policy.validate().map_err(LabError::Contract)?;
        Ok(())
    }

    pub fn mission_hash(&self) -> [u8; 32] {
        canonical_hash(self)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceRecord {
    pub source_id: String,
    pub uri: String,
    pub retrieved_at_ms: u64,
    pub content_hash: [u8; 32],
    pub snapshot_hash: [u8; 32],
    pub trust_tier: u8,
    pub extractor: String,
    #[serde(default)]
    pub provenance_cluster: String,
}

impl SourceRecord {
    pub fn validate(&self) -> Result<(), LabError> {
        let uri = self.uri.trim();
        if self.source_id.trim().is_empty()
            || !uri.starts_with("https://")
            || uri[8..].is_empty()
            || uri.contains('@')
            || self.retrieved_at_ms == 0
            || self.content_hash == [0; 32]
            || self.snapshot_hash == [0; 32]
            || self.extractor.trim().is_empty()
            || self.trust_tier == 0
        {
            return Err(LabError::InvalidRecord);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ClaimRecord {
    pub claim_id: String,
    pub statement: String,
    pub source_ids: Vec<String>,
    pub confidence_bps: u16,
    pub status: ClaimStatus,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ClaimStatus {
    Supported,
    Contradicted,
    Unresolved,
}

impl ClaimRecord {
    fn validate(&self, sources: &BTreeMap<String, SourceRecord>) -> Result<(), LabError> {
        if self.claim_id.trim().is_empty()
            || self.statement.trim().is_empty()
            || self.source_ids.is_empty()
            || self.confidence_bps > 10_000
            || self
                .source_ids
                .iter()
                .any(|source_id| !sources.contains_key(source_id))
        {
            return Err(LabError::InvalidRecord);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HypothesisRecord {
    pub hypothesis_id: String,
    pub statement: String,
    pub prior_bps: u16,
    pub falsifiers: Vec<String>,
    pub supporting_claim_ids: Vec<String>,
    pub contradicting_claim_ids: Vec<String>,
}

impl HypothesisRecord {
    fn validate(&self, claims: &BTreeMap<String, ClaimRecord>) -> Result<(), LabError> {
        if self.hypothesis_id.trim().is_empty()
            || self.statement.trim().is_empty()
            || self.prior_bps > 10_000
            || self.falsifiers.is_empty()
            || self.falsifiers.iter().any(|item| item.trim().is_empty())
            || self
                .supporting_claim_ids
                .iter()
                .chain(self.contradicting_claim_ids.iter())
                .any(|claim_id| !claims.contains_key(claim_id))
        {
            return Err(LabError::InvalidRecord);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExperimentSpec {
    pub experiment_id: String,
    pub hypothesis_id: String,
    pub design: String,
    pub variables: Vec<String>,
    pub controls: Vec<String>,
    pub preregistered_seeds: Vec<u64>,
    pub expected_observations: u32,
    pub measurement_unit: String,
    pub uncertainty_required: bool,
    pub min_clean_replicates: u32,
}

impl ExperimentSpec {
    fn validate(&self, hypotheses: &BTreeMap<String, HypothesisRecord>) -> Result<(), LabError> {
        if self.experiment_id.trim().is_empty()
            || self.design.trim().is_empty()
            || self.variables.is_empty()
            || self.controls.is_empty()
            || self.preregistered_seeds.len() < 5
            || self.expected_observations == 0
            || self.min_clean_replicates > self.expected_observations
            || unit_dimension(&self.measurement_unit).is_none()
            || !hypotheses.contains_key(&self.hypothesis_id)
        {
            return Err(LabError::InvalidRecord);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ObservationRecord {
    pub observation_id: String,
    pub experiment_id: String,
    pub seed: u64,
    pub measurement: f64,
    pub unit: String,
    pub raw_artifact_hash: [u8; 32],
    pub environment_hash: [u8; 32],
    pub valid: bool,
    pub uncertainty: Option<f64>,
    pub replication_of: Option<String>,
    pub operator_id: String,
    pub clean: bool,
    pub epistemic_status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResearchProgramReceipt {
    pub program_hash: [u8; 32],
    pub operation_count: u32,
    pub candidate_count: u32,
    pub provider: String,
}

impl ResearchProgramReceipt {
    fn validate(&self) -> Result<(), LabError> {
        if self.program_hash == [0; 32]
            || self.operation_count == 0
            || self.provider.trim().is_empty()
        {
            return Err(LabError::InvalidRecord);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SecurityEventRecord {
    pub reason: String,
    pub artifact_hash: [u8; 32],
}

impl SecurityEventRecord {
    fn validate(&self) -> Result<(), LabError> {
        if self.reason.trim().is_empty() {
            return Err(LabError::InvalidRecord);
        }
        Ok(())
    }
}

impl ObservationRecord {
    fn validate(
        &self,
        experiments: &BTreeMap<String, ExperimentSpec>,
        observations: &BTreeMap<String, ObservationRecord>,
    ) -> Result<(), LabError> {
        let Some(experiment) = experiments.get(&self.experiment_id) else {
            return Err(LabError::InvalidRecord);
        };
        if self.observation_id.trim().is_empty()
            || self.unit.trim().is_empty()
            || !self.measurement.is_finite()
            || self.raw_artifact_hash == [0; 32]
            || self.environment_hash == [0; 32]
            || !matches!(
                self.epistemic_status.as_str(),
                "OBSERVED" | "DERIVED" | "INFERRED" | "SIMULATED" | "UNKNOWN" | "REJECTED"
            )
            || !experiment.preregistered_seeds.contains(&self.seed)
            || unit_dimension(&self.unit) != unit_dimension(&experiment.measurement_unit)
            || self
                .uncertainty
                .is_some_and(|value| !value.is_finite() || value < 0.0)
            || (experiment.uncertainty_required && self.uncertainty.is_none())
        {
            return Err(LabError::InvalidRecord);
        }
        if let Some(parent_id) = &self.replication_of {
            let Some(parent) = observations.get(parent_id) else {
                return Err(LabError::InvalidRecord);
            };
            if parent.experiment_id != self.experiment_id
                || self.operator_id.trim().is_empty()
                || self.environment_hash == parent.environment_hash
                || !self.clean
            {
                return Err(LabError::InvalidRecord);
            }
        }
        Ok(())
    }
}

fn unit_dimension(unit: &str) -> Option<[i8; 6]> {
    let mut dimensions = [0i8; 6];
    let mut operator = 1i8;
    let mut token = String::new();
    let mut apply = |raw: &str, current_operator: i8| -> Option<()> {
        let trimmed = raw.trim();
        if trimmed.is_empty() || trimmed == "1" {
            return Some(());
        }
        let (symbol, exponent) = match trimmed.split_once('^') {
            Some((symbol, exponent)) => (symbol, exponent.parse::<i8>().ok()?),
            None => (trimmed, 1i8),
        };
        let base = match symbol {
            "ratio" | "score" => [0, 0, 0, 0, 0, 0],
            "m" | "cm" | "mm" => [0, 1, 0, 0, 0, 0],
            "s" | "ms" => [0, 0, 1, 0, 0, 0],
            "kg" | "g" => [1, 0, 0, 0, 0, 0],
            "A" | "mA" | "uA" => [0, 0, 0, 1, 0, 0],
            "V" | "mV" => [1, 2, -3, -1, 0, 0],
            "W" | "mW" | "uW" => [1, 2, -3, 0, 0, 0],
            "J" | "mJ" => [1, 2, -2, 0, 0, 0],
            "K" => [0, 0, 0, 0, 1, 0],
            "Hz" => [0, 0, -1, 0, 0, 0],
            "N" => [1, 1, -2, 0, 0, 0],
            "Pa" => [1, -1, -2, 0, 0, 0],
            "C" => [0, 0, 1, 1, 0, 0],
            "Ohm" => [1, 2, -3, -2, 0, 0],
            _ => return None,
        };
        for (index, component) in base.iter().enumerate() {
            let value = dimensions[index] as i16
                + (*component as i16 * exponent as i16 * current_operator as i16);
            if !(-128..=127).contains(&value) {
                return None;
            }
            dimensions[index] = value as i8;
        }
        Some(())
    };
    for character in unit.trim().chars().chain(std::iter::once('*')) {
        if character == '*' || character == '/' {
            apply(&token, operator)?;
            token.clear();
            operator = if character == '*' { 1 } else { -1 };
        } else {
            token.push(character);
        }
    }
    Some(dimensions)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BenchmarkProtocolV2 {
    pub name: String,
    pub metric: String,
    pub direction: BenchmarkDirection,
    pub alpha_bps: u16,
    pub min_trials: u32,
    pub warmups: u32,
    pub paired_blocks: u32,
    pub preregistered_seeds: Vec<u64>,
    pub frozen_split_hash: [u8; 32],
    pub contamination_checks: Vec<String>,
    pub percentile: Option<u8>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum BenchmarkDirection {
    HigherIsBetter,
    LowerIsBetter,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BenchmarkResultV2 {
    pub protocol_hash: [u8; 32],
    pub passed: bool,
    pub estimate: Option<f64>,
    pub ci_low: Option<f64>,
    pub ci_high: Option<f64>,
    pub trial_count: usize,
    pub warmup_count: u32,
    pub paired_block_count: u32,
    pub raw_trial_hash: [u8; 32],
    pub contamination_flags: Vec<String>,
    pub failure_reasons: Vec<String>,
    pub raw_trials: Vec<f64>,
    pub artifact_hash: [u8; 32],
    pub validator_hash: [u8; 32],
}

impl BenchmarkProtocolV2 {
    pub fn validate(&self) -> Result<(), LabError> {
        if self.name.trim().is_empty()
            || self.metric.trim().is_empty()
            || self.alpha_bps == 0
            || self.alpha_bps >= 5_000
            || self.min_trials < 30
            || self.warmups < 10
            || self.paired_blocks < 30
            || self.preregistered_seeds.len() < 5
            || self.frozen_split_hash == [0; 32]
            || self.contamination_checks.is_empty()
            || self
                .contamination_checks
                .iter()
                .any(|check| check.trim().is_empty())
            || self
                .percentile
                .is_some_and(|percentile| !matches!(percentile, 50 | 90 | 95 | 99))
            || (self.percentile == Some(99) && self.min_trials < 1_000)
        {
            return Err(LabError::InvalidBenchmark);
        }
        Ok(())
    }

    pub fn protocol_hash(&self) -> [u8; 32] {
        canonical_hash(self)
    }
}

pub fn evaluate_benchmark(
    protocol: &BenchmarkProtocolV2,
    trials: &[f64],
    baseline: Option<f64>,
    contamination_flags: &[String],
) -> BenchmarkResultV2 {
    let mut failure_reasons = Vec::new();
    if protocol.validate().is_err() {
        failure_reasons.push("invalid_protocol".to_string());
    }
    if trials.is_empty() || trials.iter().any(|value| !value.is_finite()) {
        failure_reasons.push("raw_trials_empty_or_non_finite".to_string());
    }
    if trials.len() < protocol.min_trials as usize {
        failure_reasons.push("insufficient_trials".to_string());
    }
    if !contamination_flags.is_empty() {
        failure_reasons.push("contamination_detected".to_string());
    }
    let (estimate, ci_low, ci_high) =
        if trials.is_empty() || trials.iter().any(|value| !value.is_finite()) {
            (None, None, None)
        } else if let Some(percentile) = protocol.percentile {
            let mut sorted = trials.to_vec();
            sorted.sort_by(f64::total_cmp);
            let index = (((percentile as usize - 1) * sorted.len()) / 100).min(sorted.len() - 1);
            (
                Some(sorted[index]),
                Some(sorted[index]),
                Some(sorted[index]),
            )
        } else {
            let mean = trials.iter().sum::<f64>() / trials.len() as f64;
            let variance = if trials.len() > 1 {
                trials
                    .iter()
                    .map(|value| (value - mean).powi(2))
                    .sum::<f64>()
                    / (trials.len() - 1) as f64
            } else {
                0.0
            };
            let margin = 1.96 * variance.sqrt() / (trials.len() as f64).sqrt();
            (Some(mean), Some(mean - margin), Some(mean + margin))
        };
    if let (Some(base), Some(low), Some(high)) = (baseline, ci_low, ci_high) {
        if !base.is_finite()
            || (protocol.direction == BenchmarkDirection::HigherIsBetter && low <= base)
            || (protocol.direction == BenchmarkDirection::LowerIsBetter && high >= base)
        {
            failure_reasons.push("confidence_interval_does_not_clear_baseline".to_string());
        }
    }
    let raw_trial_hash = canonical_hash(&trials);
    let artifact_hash = canonical_hash(&(
        protocol.protocol_hash(),
        trials,
        baseline,
        contamination_flags,
    ));
    BenchmarkResultV2 {
        protocol_hash: protocol.protocol_hash(),
        passed: failure_reasons.is_empty(),
        estimate,
        ci_low,
        ci_high,
        trial_count: trials.len(),
        warmup_count: protocol.warmups,
        paired_block_count: protocol.paired_blocks,
        raw_trial_hash,
        contamination_flags: contamination_flags.to_vec(),
        failure_reasons,
        raw_trials: trials.to_vec(),
        artifact_hash,
        validator_hash: [0; 32],
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LabEvent {
    pub sequence: u64,
    pub state_epoch: u64,
    pub kind: LabEventKind,
    pub payload_hash: [u8; 32],
    pub previous_event_hash: [u8; 32],
    pub event_hash: [u8; 32],
}

impl LabEvent {
    fn new(
        sequence: u64,
        state_epoch: u64,
        kind: LabEventKind,
        payload_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        let mut event = Self {
            sequence,
            state_epoch,
            kind,
            payload_hash,
            previous_event_hash,
            event_hash: [0; 32],
        };
        event.event_hash = canonical_hash(&event_without_hash(&event));
        event
    }

    pub fn is_valid(&self) -> bool {
        self.event_hash != [0; 32] && self.event_hash == canonical_hash(&event_without_hash(self))
    }
}

pub fn verify_event_chain(events: &[LabEvent]) -> bool {
    let mut previous = [0; 32];
    let mut previous_epoch = 0;
    for (index, event) in events.iter().enumerate() {
        if event.sequence != index as u64 + 1
            || event.previous_event_hash != previous
            || event.state_epoch < previous_epoch
            || !event.is_valid()
        {
            return false;
        }
        previous = event.event_hash;
        previous_epoch = event.state_epoch;
    }
    !events.is_empty()
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct EventWithoutHash {
    sequence: u64,
    state_epoch: u64,
    kind: LabEventKind,
    payload_hash: [u8; 32],
    previous_event_hash: [u8; 32],
}

fn event_without_hash(event: &LabEvent) -> EventWithoutHash {
    EventWithoutHash {
        sequence: event.sequence,
        state_epoch: event.state_epoch,
        kind: event.kind,
        payload_hash: event.payload_hash,
        previous_event_hash: event.previous_event_hash,
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LabRunManifest {
    pub mission_hash: [u8; 32],
    pub state: LabRunState,
    pub state_epoch: u64,
    pub event_count: u64,
    pub event_root_hash: [u8; 32],
    pub source_count: usize,
    pub claim_count: usize,
    pub hypothesis_count: usize,
    pub experiment_count: usize,
    pub observation_count: usize,
    pub manifest_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LabError {
    InvalidMission,
    InvalidRecord,
    InvalidBenchmark,
    InvalidTransition,
    InvalidEvent,
    MissingRecord,
    Contract(ContractError),
    IncompleteMission,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LabRuntime {
    mission: LabMissionSpec,
    state: LabRunState,
    state_epoch: u64,
    events: Vec<LabEvent>,
    sources: BTreeMap<String, SourceRecord>,
    claims: BTreeMap<String, ClaimRecord>,
    hypotheses: BTreeMap<String, HypothesisRecord>,
    experiments: BTreeMap<String, ExperimentSpec>,
    observations: BTreeMap<String, ObservationRecord>,
}

/// Native single-writer controller around the Lab reducer and its resource
/// reserves.  Callers receive an immutable runtime view; all budget,
/// finalization and record mutations go through this bounded surface.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LabController {
    runtime: LabRuntime,
    budget: crate::gt96::BudgetLedger,
    finalization: crate::gt96::FinalizationBoundary,
    steps: u32,
    /// IDs admitted through the language/runtime projection bridge.  The
    /// native typed reducer owns its own maps; projection IDs are retained
    /// separately so a Python adapter cannot invent a claim/hypothesis/
    /// experiment/observation edge without native cross-reference checks.
    #[serde(default)]
    projection_sources: BTreeSet<String>,
    #[serde(default)]
    projection_claims: BTreeSet<String>,
    #[serde(default)]
    projection_hypotheses: BTreeSet<String>,
    #[serde(default)]
    projection_experiments: BTreeMap<String, ProjectionExperiment>,
    #[serde(default)]
    projection_observations: BTreeMap<String, ProjectionObservation>,
    #[serde(default)]
    projection_experiment_execution_admissions:
        BTreeMap<String, ProjectionExperimentExecutionAdmission>,
    #[serde(default)]
    projection_experiment_executions: BTreeSet<String>,
    #[serde(default)]
    projection_tool_execution_admissions: BTreeMap<String, ProjectionToolExecutionAdmission>,
    #[serde(default)]
    projection_tool_executions: BTreeSet<String>,
    #[serde(default)]
    projection_cancellation_admissions: BTreeMap<String, String>,
    #[serde(default)]
    projection_cancellations: BTreeSet<String>,
    #[serde(default)]
    projection_browser_action_admissions: BTreeMap<String, String>,
    #[serde(default)]
    projection_browser_actions: BTreeSet<String>,
    #[serde(default)]
    projection_browser_observation_admissions: BTreeMap<String, String>,
    #[serde(default)]
    projection_browser_observations: BTreeSet<String>,
    #[serde(default)]
    projection_skill_admissions: BTreeMap<String, ProjectionSkillAdmission>,
    #[serde(default)]
    projection_skill_executions: BTreeSet<String>,
    #[serde(default)]
    projection_research_programs: BTreeSet<String>,
    #[serde(default)]
    projection_research_program_admissions: BTreeMap<String, String>,
    #[serde(default)]
    projection_payloads: BTreeMap<u64, serde_json::Value>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ProjectionExperiment {
    hypothesis_id: String,
    preregistered_seeds: BTreeSet<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ProjectionObservation {
    experiment_id: String,
    environment_hash: String,
    replication_of: Option<String>,
    clean: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ProjectionExperimentExecutionAdmission {
    admission_id: String,
    experiment_id: String,
    attempt: u32,
    input_hash: String,
    policy_hash: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ProjectionToolExecutionAdmission {
    admission_id: String,
    tool_name: String,
    attempt: u32,
    lease_id: u64,
    effect_class: String,
    actor_role: String,
    expected_observation_schema: String,
    stop_rule: String,
    input_hash: String,
    policy_hash: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ProjectionSkillAdmission {
    skill_id: String,
    version: String,
    mission_id: String,
}

impl LabController {
    pub fn new(mission: LabMissionSpec) -> Result<Self, LabError> {
        let budget =
            crate::gt96::BudgetLedger::new(&mission.budget_policy).map_err(LabError::Contract)?;
        Ok(Self {
            runtime: LabRuntime::new(mission)?,
            budget,
            finalization: crate::gt96::FinalizationBoundary::default(),
            steps: 0,
            projection_sources: BTreeSet::new(),
            projection_claims: BTreeSet::new(),
            projection_hypotheses: BTreeSet::new(),
            projection_experiments: BTreeMap::new(),
            projection_observations: BTreeMap::new(),
            projection_experiment_execution_admissions: BTreeMap::new(),
            projection_experiment_executions: BTreeSet::new(),
            projection_tool_execution_admissions: BTreeMap::new(),
            projection_tool_executions: BTreeSet::new(),
            projection_cancellation_admissions: BTreeMap::new(),
            projection_cancellations: BTreeSet::new(),
            projection_browser_action_admissions: BTreeMap::new(),
            projection_browser_actions: BTreeSet::new(),
            projection_browser_observation_admissions: BTreeMap::new(),
            projection_browser_observations: BTreeSet::new(),
            projection_skill_admissions: BTreeMap::new(),
            projection_skill_executions: BTreeSet::new(),
            projection_research_programs: BTreeSet::new(),
            projection_research_program_admissions: BTreeMap::new(),
            projection_payloads: BTreeMap::new(),
        })
    }

    pub fn runtime(&self) -> &LabRuntime {
        &self.runtime
    }

    pub fn budget_remaining(&self) -> crate::gt96::BudgetVector {
        self.budget.remaining()
    }

    pub fn finalization_state(&self) -> crate::gt96::FinalizationState {
        self.finalization.state()
    }

    pub fn steps(&self) -> u32 {
        self.steps
    }

    pub fn snapshot_json(&self) -> Result<String, LabError> {
        serde_json::to_string(self).map_err(|_| LabError::InvalidEvent)
    }

    pub fn from_snapshot_json(snapshot: &str) -> Result<Self, LabError> {
        let controller: Self =
            serde_json::from_str(snapshot).map_err(|_| LabError::InvalidEvent)?;
        controller.runtime.validate_snapshot()?;
        if controller.steps > controller.runtime.mission().max_steps
            || !controller.budget.snapshot_valid()
            || !controller
                .budget
                .exploration_reserve()
                .fits_within(controller.runtime.mission().budget_policy.total)
            || !controller
                .budget
                .finalization_reserve()
                .fits_within(controller.runtime.mission().budget_policy.total)
            || !controller
                .budget
                .recovery_reserve()
                .fits_within(controller.runtime.mission().budget_policy.total)
        {
            return Err(LabError::InvalidEvent);
        }
        controller.validate_projection_snapshot()?;
        Ok(controller)
    }

    /// Admit one bounded exploration action at an expected replay epoch.
    pub fn admit_exploration(
        &mut self,
        expected_state_epoch: u64,
        spend: crate::gt96::BudgetVector,
    ) -> Result<(), LabError> {
        if self.runtime.state_epoch() != expected_state_epoch
            || self.steps >= self.runtime.mission().max_steps
            || !self.finalization.exploration_allowed()
            || matches!(
                self.runtime.state(),
                LabRunState::Completed | LabRunState::Aborted
            )
        {
            return Err(LabError::InvalidTransition);
        }
        self.budget
            .consume_exploration(spend)
            .map_err(LabError::Contract)?;
        self.steps = self.steps.saturating_add(1);
        self.runtime.record_budget_admitted(spend);
        Ok(())
    }

    pub fn begin_finalization(&mut self) -> Result<(), LabError> {
        self.finalization.begin().map_err(LabError::Contract)?;
        self.runtime
            .record_finalization_event(LabEventKind::FinalizationStarted);
        Ok(())
    }

    pub fn reopen_for_critical_gap(&mut self) -> Result<(), LabError> {
        self.finalization
            .reopen_for_critical_gap()
            .map_err(LabError::Contract)?;
        self.runtime
            .record_finalization_event(LabEventKind::FinalizationReopened);
        Ok(())
    }

    /// Consume one exploration allocation and retain its projection event in
    /// the same native chain. The event is supplied by the language/runtime
    /// adapter so its payload and hash remain visible to replay consumers.
    pub fn admit_projection_exploration(
        &mut self,
        expected_state_epoch: u64,
        spend: crate::gt96::BudgetVector,
        event: LabEvent,
    ) -> Result<(), LabError> {
        if event.kind != LabEventKind::BudgetAdmitted
            || self.runtime.state_epoch() != expected_state_epoch
            || event.state_epoch != expected_state_epoch.saturating_add(1)
            || self.steps >= self.runtime.mission().max_steps
            || !self.finalization.exploration_allowed()
            || matches!(
                self.runtime.state(),
                LabRunState::Completed | LabRunState::Aborted
            )
            || event.payload_hash != canonical_hash(&spend)
        {
            return Err(LabError::InvalidTransition);
        }
        self.validate_projection_event(&event)?;
        self.budget
            .consume_exploration(spend)
            .map_err(LabError::Contract)?;
        self.steps = self.steps.saturating_add(1);
        self.runtime.state_epoch = event.state_epoch;
        self.runtime.events.push(event);
        Ok(())
    }

    /// Admit an event produced by a bounded language/runtime projection.
    ///
    /// The native controller remains the single writer for the replay chain:
    /// it validates sequence, parent hash, epoch monotonicity and the event
    /// hash before retaining the projection event.  Domain records are still
    /// owned by their typed methods; this bridge is intentionally narrow so a
    /// Python view cannot mutate native records or bypass the replay fence.
    pub fn admit_projection_event(
        &mut self,
        event: LabEvent,
        projection_state: Option<LabRunState>,
    ) -> Result<(), LabError> {
        if event.sequence == 1 && self.runtime.events.len() == 1 && event == self.runtime.events[0]
        {
            return Ok(());
        }
        // The legacy hash-only surface must not be an alternate write path
        // for records whose payload carries identity, provenance or side
        // effects.  Callers must use `admit_projection_record`, which binds
        // the typed JSON payload and native cross-reference index.
        if matches!(
            event.kind,
            LabEventKind::SourceCaptured
                | LabEventKind::ClaimRecorded
                | LabEventKind::HypothesisRecorded
                | LabEventKind::ExperimentScheduled
                | LabEventKind::ExperimentExecutionAdmitted
                | LabEventKind::ExperimentExecutionRecorded
                | LabEventKind::ToolExecutionAdmitted
                | LabEventKind::ToolExecutionRecorded
                | LabEventKind::ObservationRecorded
                | LabEventKind::BrowserActionAdmitted
                | LabEventKind::BrowserActionRecorded
                | LabEventKind::BrowserObservationAdmitted
                | LabEventKind::BrowserObservationRecorded
                | LabEventKind::ResearchProgramAdmitted
                | LabEventKind::ResearchProgramExecuted
                | LabEventKind::SkillAdmissionRecorded
                | LabEventKind::SkillExecutionRecorded
                | LabEventKind::CancellationAdmitted
                | LabEventKind::CancellationRecorded
                | LabEventKind::BudgetAdmitted
        ) {
            return Err(LabError::InvalidRecord);
        }
        self.validate_projection_event(&event)?;
        if event.kind == LabEventKind::StateChanged {
            let Some(next_state) = projection_state else {
                return Err(LabError::InvalidEvent);
            };
            if self.runtime.state != next_state && !self.runtime.state.can_transition_to(next_state)
            {
                return Err(LabError::InvalidTransition);
            }
            self.runtime.state = next_state;
        }
        match event.kind {
            LabEventKind::FinalizationStarted => {
                self.finalization.begin().map_err(LabError::Contract)?
            }
            LabEventKind::FinalizationReopened => self
                .finalization
                .reopen_for_critical_gap()
                .map_err(LabError::Contract)?,
            _ => {}
        }
        self.runtime.state_epoch = event.state_epoch;
        self.runtime.events.push(event);
        Ok(())
    }

    /// Admit a projection event together with its canonical JSON payload.
    ///
    /// Hash-chain validation alone proves only that an adapter supplied a
    /// self-consistent sequence.  This boundary also validates the typed
    /// record shape and cross-record references before retaining the event.
    /// Payloads are intentionally JSON here because the Python facade carries
    /// a few adapter-only fields; the native authority still owns all
    /// structural invariants and never trusts the adapter's ID graph.
    pub fn admit_projection_record(
        &mut self,
        event: LabEvent,
        payload_json: &str,
        projection_state: Option<LabRunState>,
    ) -> Result<(), LabError> {
        // Validate on a copy so a rejected record can never leave behind a
        // partially-applied projection (for example a duplicate observation
        // replacing an existing value before the duplicate is reported).
        // The native controller is also callable directly through FFI, so
        // adapter-side rollback cannot be the only atomicity guarantee.
        let mut candidate = self.clone();
        candidate.admit_projection_record_mut(event, payload_json, projection_state)?;
        *self = candidate;
        Ok(())
    }

    fn admit_projection_record_mut(
        &mut self,
        event: LabEvent,
        payload_json: &str,
        projection_state: Option<LabRunState>,
    ) -> Result<(), LabError> {
        let payload: serde_json::Value =
            serde_json::from_str(payload_json).map_err(|_| LabError::InvalidRecord)?;
        if event.payload_hash != projection_payload_hash(&payload) {
            return Err(LabError::InvalidEvent);
        }
        self.validate_projection_event(&event)?;
        if !self.projection_state_allows(event.kind) {
            return Err(LabError::InvalidTransition);
        }
        self.validate_projection_payload(event.kind, &payload)?;
        self.validate_projection_binding(&event, &payload)?;
        self.sync_projection_record_to_runtime(event.kind, &payload)?;
        if event.kind == LabEventKind::StateChanged {
            let Some(next_state) = projection_state else {
                return Err(LabError::InvalidEvent);
            };
            if self.runtime.state != next_state && !self.runtime.state.can_transition_to(next_state)
            {
                return Err(LabError::InvalidTransition);
            }
            self.runtime.state = next_state;
        }
        match event.kind {
            LabEventKind::FinalizationStarted => {
                self.finalization.begin().map_err(LabError::Contract)?
            }
            LabEventKind::FinalizationReopened => self
                .finalization
                .reopen_for_critical_gap()
                .map_err(LabError::Contract)?,
            _ => {}
        }
        self.projection_payloads
            .insert(event.sequence, payload.clone());
        self.runtime.state_epoch = event.state_epoch;
        self.runtime.events.push(event);
        Ok(())
    }

    /// Materialize projection records into the typed runtime maps after the
    /// projection contract has validated them.  Adapter-only fields remain in
    /// `projection_payloads`, while the native reducer now retains the typed
    /// identity/reference graph needed for snapshot validation and recovery.
    /// The caller invokes this on a cloned controller, so any conversion or
    /// duplicate failure is atomic at the public admission boundary.
    fn sync_projection_record_to_runtime(
        &mut self,
        kind: LabEventKind,
        payload: &serde_json::Value,
    ) -> Result<(), LabError> {
        fn digest_value(value: &serde_json::Value) -> Result<serde_json::Value, LabError> {
            let raw = value.as_str().ok_or(LabError::InvalidRecord)?;
            if !is_hex_digest(raw) {
                return Err(LabError::InvalidRecord);
            }
            let bytes = raw
                .as_bytes()
                .chunks_exact(2)
                .map(|pair| {
                    let nibble = |byte: u8| match byte {
                        b'0'..=b'9' => Some(byte - b'0'),
                        b'a'..=b'f' => Some(byte - b'a' + 10),
                        _ => None,
                    };
                    Some((nibble(pair[0])? << 4) | nibble(pair[1])?)
                })
                .collect::<Option<Vec<u8>>>()
                .ok_or(LabError::InvalidRecord)?;
            if bytes.len() != 32 {
                return Err(LabError::InvalidRecord);
            }
            Ok(serde_json::Value::Array(
                bytes
                    .into_iter()
                    .map(|byte| serde_json::Value::from(byte))
                    .collect(),
            ))
        }

        fn object_copy(
            payload: &serde_json::Value,
        ) -> Result<serde_json::Map<String, serde_json::Value>, LabError> {
            payload.as_object().cloned().ok_or(LabError::InvalidRecord)
        }

        match kind {
            LabEventKind::SourceCaptured => {
                let mut object = object_copy(payload)?;
                let content_hash = object
                    .get("content_hash")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default();
                let snapshot_hash = object
                    .get("snapshot_hash")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default();
                // Compatibility projections historically accepted opaque
                // non-hex labels such as fixture names.  Keep those records
                // in the validated projection map, but defer typed
                // materialization until a real 32-byte digest is available.
                if !is_hex_digest(content_hash) || !is_hex_digest(snapshot_hash) {
                    return Ok(());
                }
                object.insert(
                    "content_hash".to_string(),
                    digest_value(object.get("content_hash").ok_or(LabError::InvalidRecord)?)?,
                );
                object.insert(
                    "snapshot_hash".to_string(),
                    digest_value(object.get("snapshot_hash").ok_or(LabError::InvalidRecord)?)?,
                );
                let source: SourceRecord =
                    serde_json::from_value(serde_json::Value::Object(object))
                        .map_err(|_| LabError::InvalidRecord)?;
                source.validate()?;
                if self
                    .runtime
                    .sources
                    .insert(source.source_id.clone(), source)
                    .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ClaimRecorded => {
                let mut object = object_copy(payload)?;
                let source_ids = object
                    .get("source_ids")
                    .and_then(serde_json::Value::as_array)
                    .ok_or(LabError::InvalidRecord)?;
                if source_ids.iter().any(|source_id| {
                    source_id
                        .as_str()
                        .is_none_or(|source_id| !self.runtime.sources.contains_key(source_id))
                }) {
                    return Ok(());
                }
                let status = object
                    .get("status")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?
                    .trim()
                    .to_ascii_lowercase();
                let status = match status.as_str() {
                    "supported" => "Supported",
                    "contradicted" => "Contradicted",
                    "unresolved" | "inconclusive" => "Unresolved",
                    _ => return Err(LabError::InvalidRecord),
                };
                object.insert("status".to_string(), serde_json::Value::from(status));
                let claim: ClaimRecord = serde_json::from_value(serde_json::Value::Object(object))
                    .map_err(|_| LabError::InvalidRecord)?;
                claim.validate(&self.runtime.sources)?;
                if self
                    .runtime
                    .claims
                    .insert(claim.claim_id.clone(), claim)
                    .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::HypothesisRecorded => {
                let object = payload.as_object().ok_or(LabError::InvalidRecord)?;
                let mut claim_ids = object
                    .get("supporting_claim_ids")
                    .into_iter()
                    .chain(object.get("contradicting_claim_ids"))
                    .flat_map(serde_json::Value::as_array)
                    .flat_map(|items| items.iter())
                    .filter_map(serde_json::Value::as_str);
                if claim_ids.any(|claim_id| !self.runtime.claims.contains_key(claim_id)) {
                    return Ok(());
                }
                let hypothesis: HypothesisRecord =
                    serde_json::from_value(payload.clone()).map_err(|_| LabError::InvalidRecord)?;
                hypothesis.validate(&self.runtime.claims)?;
                if self
                    .runtime
                    .hypotheses
                    .insert(hypothesis.hypothesis_id.clone(), hypothesis)
                    .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ExperimentScheduled => {
                let hypothesis_id = payload
                    .get("hypothesis_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                if !self.runtime.hypotheses.contains_key(hypothesis_id) {
                    return Ok(());
                }
                let experiment: ExperimentSpec =
                    serde_json::from_value(payload.clone()).map_err(|_| LabError::InvalidRecord)?;
                experiment.validate(&self.runtime.hypotheses)?;
                if self
                    .runtime
                    .experiments
                    .insert(experiment.experiment_id.clone(), experiment)
                    .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ObservationRecorded => {
                let mut object = object_copy(payload)?;
                let experiment_id = object
                    .get("experiment_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                if !self.runtime.experiments.contains_key(experiment_id) {
                    return Ok(());
                }
                let raw_artifact_hash = object
                    .get("raw_artifact_hash")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default();
                let environment_hash = object
                    .get("environment_hash")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default();
                if !is_hex_digest(raw_artifact_hash) || !is_hex_digest(environment_hash) {
                    return Ok(());
                }
                object.insert(
                    "raw_artifact_hash".to_string(),
                    digest_value(
                        object
                            .get("raw_artifact_hash")
                            .ok_or(LabError::InvalidRecord)?,
                    )?,
                );
                object.insert(
                    "environment_hash".to_string(),
                    digest_value(
                        object
                            .get("environment_hash")
                            .ok_or(LabError::InvalidRecord)?,
                    )?,
                );
                let observation: ObservationRecord =
                    serde_json::from_value(serde_json::Value::Object(object))
                        .map_err(|_| LabError::InvalidRecord)?;
                observation.validate(&self.runtime.experiments, &self.runtime.observations)?;
                if self
                    .runtime
                    .observations
                    .insert(observation.observation_id.clone(), observation)
                    .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn projection_state_allows(&self, kind: LabEventKind) -> bool {
        match kind {
            LabEventKind::SourceCaptured => {
                matches!(self.runtime.state, LabRunState::Researching)
            }
            LabEventKind::ClaimRecorded
            | LabEventKind::HypothesisRecorded
            | LabEventKind::ExperimentScheduled => matches!(
                self.runtime.state,
                LabRunState::Researching | LabRunState::Experimenting | LabRunState::Reviewing
            ),
            LabEventKind::ObservationRecorded => {
                matches!(
                    self.runtime.state,
                    LabRunState::Experimenting | LabRunState::Reviewing
                )
            }
            LabEventKind::ExperimentExecutionAdmitted
            | LabEventKind::ExperimentExecutionRecorded => {
                matches!(
                    self.runtime.state,
                    LabRunState::Experimenting | LabRunState::Reviewing | LabRunState::Blocked
                )
            }
            LabEventKind::ToolExecutionAdmitted => {
                matches!(
                    self.runtime.state,
                    LabRunState::Planned
                        | LabRunState::Researching
                        | LabRunState::Experimenting
                        | LabRunState::Reviewing
                )
            }
            LabEventKind::ToolExecutionRecorded => {
                !matches!(self.runtime.state, LabRunState::Completed)
            }
            LabEventKind::CancellationAdmitted => !matches!(
                self.runtime.state,
                LabRunState::Completed | LabRunState::Aborted
            ),
            // A cancellation changes the state through a preceding
            // StateChanged event; its settlement must remain replay-visible
            // even though the reducer is now terminal.
            LabEventKind::CancellationRecorded => {
                !matches!(self.runtime.state, LabRunState::Completed)
            }
            LabEventKind::FinalizationStarted => {
                matches!(
                    self.runtime.state,
                    LabRunState::Researching | LabRunState::Experimenting | LabRunState::Reviewing
                )
            }
            LabEventKind::FinalizationReopened => {
                matches!(
                    self.runtime.state,
                    LabRunState::Researching
                        | LabRunState::Experimenting
                        | LabRunState::Reviewing
                        | LabRunState::Blocked
                )
            }
            LabEventKind::StateChanged => true,
            // The Python facade commits the evidence-bounded synthesis after
            // the dossier has crossed the terminal `Completed` state.  This
            // is still an append-only review record: it cannot mutate the
            // reducer state or reopen authority, but it must remain replayable
            // and hash-bound instead of being rejected as a post-completion
            // side effect.
            LabEventKind::ReviewRecorded => !matches!(self.runtime.state, LabRunState::Aborted),
            _ => !matches!(
                self.runtime.state,
                LabRunState::Completed | LabRunState::Aborted
            ),
        }
    }

    fn validate_projection_payload(
        &mut self,
        kind: LabEventKind,
        payload: &serde_json::Value,
    ) -> Result<(), LabError> {
        let object = payload.as_object().ok_or(LabError::InvalidRecord)?;
        let non_empty = |key: &str| -> Result<String, LabError> {
            let value = object
                .get(key)
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .ok_or(LabError::InvalidRecord)?;
            Ok(value.to_string())
        };
        let string_array = |key: &str| -> Result<Vec<String>, LabError> {
            let values = object
                .get(key)
                .and_then(serde_json::Value::as_array)
                .ok_or(LabError::InvalidRecord)?;
            let mut result = Vec::with_capacity(values.len());
            for value in values {
                let item = value
                    .as_str()
                    .map(str::trim)
                    .filter(|item| !item.is_empty())
                    .ok_or(LabError::InvalidRecord)?;
                result.push(item.to_string());
            }
            Ok(result)
        };
        let bounded_u64 = |key: &str, maximum: u64| -> Result<u64, LabError> {
            let value = object
                .get(key)
                .and_then(serde_json::Value::as_u64)
                .ok_or(LabError::InvalidRecord)?;
            if value > maximum {
                return Err(LabError::InvalidRecord);
            }
            Ok(value)
        };
        match kind {
            LabEventKind::SourceCaptured => {
                let id = non_empty("source_id")?;
                let uri = non_empty("uri")?;
                if !uri.starts_with("https://")
                    || object
                        .get("content_hash")
                        .and_then(serde_json::Value::as_str)
                        .is_none_or(|value| value.trim().is_empty())
                    || object
                        .get("snapshot_hash")
                        .and_then(serde_json::Value::as_str)
                        .is_none_or(|value| value.trim().is_empty())
                    || object
                        .get("retrieved_at_ms")
                        .and_then(serde_json::Value::as_u64)
                        .is_none_or(|value| value == 0)
                    || object
                        .get("trust_tier")
                        .and_then(serde_json::Value::as_u64)
                        .is_none_or(|value| value == 0 || value > u8::MAX as u64)
                {
                    return Err(LabError::InvalidRecord);
                }
                non_empty("extractor")?;
                if !self.projection_sources.insert(id) {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ClaimRecorded => {
                let id = non_empty("claim_id")?;
                non_empty("statement")?;
                let sources = string_array("source_ids")?;
                if sources.is_empty()
                    || bounded_u64("confidence_bps", 10_000)? > 10_000
                    || sources.iter().any(|source| {
                        !self.projection_sources.contains(source)
                            && !self.runtime.sources.contains_key(source)
                    })
                    || !self.projection_claims.insert(id)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::HypothesisRecorded => {
                let id = non_empty("hypothesis_id")?;
                non_empty("statement")?;
                if bounded_u64("prior_bps", 10_000)? > 10_000 {
                    return Err(LabError::InvalidRecord);
                }
                if string_array("falsifiers")?.is_empty() {
                    return Err(LabError::InvalidRecord);
                }
                let mut claims = string_array("supporting_claim_ids")?;
                claims.extend(string_array("contradicting_claim_ids")?);
                if claims.iter().any(|claim| {
                    !self.projection_claims.contains(claim)
                        && !self.runtime.claims.contains_key(claim)
                }) || !self.projection_hypotheses.insert(id)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ExperimentScheduled => {
                let id = non_empty("experiment_id")?;
                let hypothesis_id = non_empty("hypothesis_id")?;
                non_empty("design")?;
                let seed_values = object
                    .get("preregistered_seeds")
                    .and_then(serde_json::Value::as_array)
                    .ok_or(LabError::InvalidRecord)?;
                if string_array("variables")?.is_empty()
                    || string_array("controls")?.is_empty()
                    || seed_values.len() < 5
                {
                    return Err(LabError::InvalidRecord);
                }
                let seeds = seed_values
                    .iter()
                    .map(|value| value.as_u64().ok_or(LabError::InvalidRecord))
                    .collect::<Result<BTreeSet<_>, _>>()?;
                if seeds.len() < 5
                    || object
                        .get("expected_observations")
                        .and_then(serde_json::Value::as_u64)
                        .is_none_or(|value| value == 0 || value > u32::MAX as u64)
                    || !self.projection_hypotheses.contains(&hypothesis_id)
                        && !self.runtime.hypotheses.contains_key(&hypothesis_id)
                    || self.projection_experiments.contains_key(&id)
                    || self.runtime.experiments.contains_key(&id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_experiments.insert(
                    id,
                    ProjectionExperiment {
                        hypothesis_id,
                        preregistered_seeds: seeds,
                    },
                );
            }
            LabEventKind::ExperimentExecutionAdmitted => {
                let admission_id = non_empty("admission_id")?;
                let execution_id = non_empty("execution_id")?;
                let experiment_id = non_empty("experiment_id")?;
                let attempt = object
                    .get("attempt")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value > 0 && *value <= u32::MAX as u64)
                    .ok_or(LabError::InvalidRecord)? as u32;
                if !self.projection_experiments.contains_key(&experiment_id)
                    && !self.runtime.experiments.contains_key(&experiment_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                let input_hash = non_empty("input_hash")?;
                let policy_hash = non_empty("policy_hash")?;
                if !is_hex_digest(&input_hash)
                    || !is_hex_digest(&policy_hash)
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("ADMITTED")
                    )
                    || self
                        .projection_experiment_execution_admissions
                        .contains_key(&execution_id)
                    || self
                        .projection_experiment_execution_admissions
                        .values()
                        .any(|value| value.admission_id == admission_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_experiment_execution_admissions.insert(
                    execution_id,
                    ProjectionExperimentExecutionAdmission {
                        admission_id,
                        experiment_id,
                        attempt,
                        input_hash,
                        policy_hash,
                    },
                );
            }
            LabEventKind::ExperimentExecutionRecorded => {
                let admission_id = non_empty("admission_id")?;
                let execution_id = non_empty("execution_id")?;
                let experiment_id = non_empty("experiment_id")?;
                let attempt = object
                    .get("attempt")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value > 0 && *value <= u32::MAX as u64)
                    .ok_or(LabError::InvalidRecord)? as u32;
                let observation_count = object
                    .get("observation_count")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value <= u32::MAX as u64)
                    .ok_or(LabError::InvalidRecord)?;
                let input_hash = non_empty("input_hash")?;
                let result_hash = non_empty("result_hash")?;
                let policy_hash = non_empty("policy_hash")?;
                if !is_hex_digest(&input_hash)
                    || !is_hex_digest(&result_hash)
                    || !is_hex_digest(&policy_hash)
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("SUCCESS" | "REJECTED" | "TIMED_OUT" | "CANCELLED")
                    )
                    || self
                        .projection_experiment_execution_admissions
                        .get(&execution_id)
                        .is_none_or(|admission| {
                            admission.admission_id != admission_id
                                || admission.experiment_id != experiment_id
                                || admission.attempt != attempt
                                || admission.input_hash != input_hash
                                || admission.policy_hash != policy_hash
                        })
                    || !self.projection_experiment_executions.insert(execution_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                let _ = observation_count;
            }
            LabEventKind::ToolExecutionAdmitted => {
                let admission_id = non_empty("admission_id")?;
                let execution_id = non_empty("execution_id")?;
                let tool_name = non_empty("tool_name")?;
                let effect_class = non_empty("effect_class")?;
                let actor_role = non_empty("actor_role")?;
                let expected_observation_schema = non_empty("expected_observation_schema")?;
                let stop_rule = non_empty("stop_rule")?;
                let attempt = object
                    .get("attempt")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value > 0 && *value <= u32::MAX as u64)
                    .ok_or(LabError::InvalidRecord)? as u32;
                let lease_id = object
                    .get("lease_id")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value > 0)
                    .ok_or(LabError::InvalidRecord)?;
                let input_hash = non_empty("input_hash")?;
                let policy_hash = non_empty("policy_hash")?;
                if !is_hex_digest(&input_hash)
                    || !is_hex_digest(&policy_hash)
                    || !matches!(actor_role.as_str(), "actor" | "observer")
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("ADMITTED")
                    )
                    || self
                        .projection_tool_execution_admissions
                        .contains_key(&execution_id)
                    || self
                        .projection_tool_execution_admissions
                        .values()
                        .any(|value| value.admission_id == admission_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_tool_execution_admissions.insert(
                    execution_id,
                    ProjectionToolExecutionAdmission {
                        admission_id,
                        tool_name,
                        attempt,
                        lease_id,
                        effect_class,
                        actor_role,
                        expected_observation_schema,
                        stop_rule,
                        input_hash,
                        policy_hash,
                    },
                );
            }
            LabEventKind::ToolExecutionRecorded => {
                let admission_id = non_empty("admission_id")?;
                let execution_id = non_empty("execution_id")?;
                let tool_name = non_empty("tool_name")?;
                let effect_class = non_empty("effect_class")?;
                let actor_role = non_empty("actor_role")?;
                let expected_observation_schema = non_empty("expected_observation_schema")?;
                let stop_rule = non_empty("stop_rule")?;
                let attempt = object
                    .get("attempt")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value > 0 && *value <= u32::MAX as u64)
                    .ok_or(LabError::InvalidRecord)? as u32;
                let lease_id = object
                    .get("lease_id")
                    .and_then(serde_json::Value::as_u64)
                    .filter(|value| *value > 0)
                    .ok_or(LabError::InvalidRecord)?;
                let input_hash = non_empty("input_hash")?;
                let result_hash = non_empty("result_hash")?;
                let policy_hash = non_empty("policy_hash")?;
                let status = object
                    .get("status")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                if !is_hex_digest(&input_hash)
                    || !is_hex_digest(&result_hash)
                    || !is_hex_digest(&policy_hash)
                    || !matches!(status, "SUCCESS" | "REJECTED" | "TIMED_OUT" | "CANCELLED")
                    || (self.runtime.state == LabRunState::Aborted && status != "CANCELLED")
                {
                    return Err(LabError::InvalidRecord);
                }
                let Some(admission) = self.projection_tool_execution_admissions.get(&execution_id)
                else {
                    return Err(LabError::InvalidRecord);
                };
                if admission.admission_id != admission_id
                    || admission.tool_name != tool_name
                    || admission.attempt != attempt
                    || admission.lease_id != lease_id
                    || admission.effect_class != effect_class
                    || admission.actor_role != actor_role
                    || admission.expected_observation_schema != expected_observation_schema
                    || admission.stop_rule != stop_rule
                    || admission.input_hash != input_hash
                    || admission.policy_hash != policy_hash
                    || !self.projection_tool_executions.insert(execution_id)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ObservationRecorded => {
                let id = non_empty("observation_id")?;
                let experiment_id = non_empty("experiment_id")?;
                let projection_experiment = self.projection_experiments.get(&experiment_id);
                let runtime_experiment = self.runtime.experiments.get(&experiment_id);
                if object
                    .get("seed")
                    .and_then(serde_json::Value::as_u64)
                    .is_none()
                    || object
                        .get("measurement")
                        .and_then(serde_json::Value::as_f64)
                        .is_none_or(|value| !value.is_finite())
                    || non_empty("unit").is_err()
                    || object
                        .get("raw_artifact_hash")
                        .and_then(serde_json::Value::as_str)
                        .is_none_or(|value| value.trim().is_empty())
                    || object
                        .get("environment_hash")
                        .and_then(serde_json::Value::as_str)
                        .is_none_or(|value| value.trim().is_empty())
                    || object
                        .get("epistemic_status")
                        .and_then(serde_json::Value::as_str)
                        .is_none_or(|value| {
                            !matches!(
                                value,
                                "OBSERVED"
                                    | "DERIVED"
                                    | "INFERRED"
                                    | "SIMULATED"
                                    | "UNKNOWN"
                                    | "REJECTED"
                            )
                        })
                    || projection_experiment.is_none() && runtime_experiment.is_none()
                {
                    return Err(LabError::InvalidRecord);
                }
                let seed = object
                    .get("seed")
                    .and_then(serde_json::Value::as_u64)
                    .ok_or(LabError::InvalidRecord)?;
                if let Some(experiment) = self.projection_experiments.get(&experiment_id) {
                    if !experiment.preregistered_seeds.contains(&seed) {
                        return Err(LabError::InvalidRecord);
                    }
                } else if let Some(experiment) = runtime_experiment {
                    if !experiment.preregistered_seeds.contains(&seed) {
                        return Err(LabError::InvalidRecord);
                    }
                }
                let replication_of = object
                    .get("replication_of")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string);
                let clean = object
                    .get("clean")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false);
                let environment_hash = non_empty("environment_hash")?;
                if let Some(parent_id) = &replication_of {
                    let Some(parent) = self.projection_observations.get(parent_id) else {
                        return Err(LabError::InvalidRecord);
                    };
                    if parent.experiment_id != experiment_id
                        || parent.environment_hash == environment_hash
                        || !clean
                        || non_empty("operator_id").is_err()
                    {
                        return Err(LabError::InvalidRecord);
                    }
                }
                if self
                    .projection_observations
                    .insert(
                        id,
                        ProjectionObservation {
                            experiment_id,
                            environment_hash,
                            replication_of,
                            clean,
                        },
                    )
                    .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::ResearchProgramAdmitted => {
                let admission_id = non_empty("admission_id")?;
                let program_hash = non_empty("program_hash")?;
                non_empty("provider")?;
                if object
                    .get("operation_count")
                    .and_then(serde_json::Value::as_u64)
                    .is_none_or(|value| value == 0 || value > u32::MAX as u64)
                    || !is_hex_digest(&program_hash)
                    || !["input_hash", "policy_hash"].iter().all(|key| {
                        object
                            .get(*key)
                            .and_then(serde_json::Value::as_str)
                            .is_some_and(is_hex_digest)
                    })
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("ADMITTED")
                    )
                    || self
                        .projection_research_program_admissions
                        .contains_key(&program_hash)
                    || self
                        .projection_research_program_admissions
                        .values()
                        .any(|value| value == &admission_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_research_program_admissions
                    .insert(program_hash, admission_id);
            }
            LabEventKind::ResearchProgramExecuted => {
                let admission_id = non_empty("admission_id")?;
                let program_hash = non_empty("program_hash")?;
                non_empty("provider")?;
                if object
                    .get("operation_count")
                    .and_then(serde_json::Value::as_u64)
                    .is_none_or(|value| value == 0 || value > u32::MAX as u64)
                    || object
                        .get("candidate_count")
                        .and_then(serde_json::Value::as_u64)
                        .is_none_or(|value| value > u32::MAX as u64)
                    || !is_hex_digest(&program_hash)
                    || !["input_hash", "result_hash", "policy_hash"]
                        .iter()
                        .all(|key| {
                            object
                                .get(*key)
                                .and_then(serde_json::Value::as_str)
                                .is_some_and(is_hex_digest)
                        })
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("SUCCESS" | "REJECTED" | "CANCELLED")
                    )
                    || self
                        .projection_research_program_admissions
                        .get(&program_hash)
                        != Some(&admission_id)
                    || !self
                        .projection_research_programs
                        .insert(program_hash.clone())
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::BrowserActionAdmitted => {
                let admission_id = non_empty("admission_id")?;
                let action_id = non_empty("action_id")?;
                let action_kind = non_empty("action_kind")?;
                if !matches!(
                    action_kind.as_str(),
                    "launch" | "goto" | "click" | "fill" | "type" | "wait"
                ) || object
                    .get("lease_id")
                    .and_then(serde_json::Value::as_u64)
                    .is_none_or(|value| value == 0)
                    || !matches!(
                        object.get("actor_role").and_then(serde_json::Value::as_str),
                        Some("actor")
                    )
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("ADMITTED")
                    )
                    || !["input_hash", "policy_hash"].iter().all(|key| {
                        object
                            .get(*key)
                            .and_then(serde_json::Value::as_str)
                            .is_some_and(is_hex_digest)
                    })
                    || self
                        .projection_browser_action_admissions
                        .contains_key(&action_id)
                    || self
                        .projection_browser_action_admissions
                        .values()
                        .any(|value| value == &admission_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_browser_action_admissions
                    .insert(action_id, admission_id);
            }
            LabEventKind::BrowserActionRecorded => {
                let action_id = non_empty("action_id")?;
                let admission_id = non_empty("admission_id")?;
                let action_kind = non_empty("action_kind")?;
                if !matches!(
                    action_kind.as_str(),
                    "launch" | "goto" | "click" | "fill" | "type" | "wait"
                ) || object
                    .get("lease_id")
                    .and_then(serde_json::Value::as_u64)
                    .is_none_or(|value| value == 0)
                    || !matches!(
                        object.get("actor_role").and_then(serde_json::Value::as_str),
                        Some("actor")
                    )
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("SUCCESS" | "REJECTED" | "CANCELLED")
                    )
                    || !["input_hash", "result_hash", "policy_hash"]
                        .iter()
                        .all(|key| {
                            object
                                .get(*key)
                                .and_then(serde_json::Value::as_str)
                                .is_some_and(is_hex_digest)
                        })
                    || self.projection_browser_action_admissions.get(&action_id)
                        != Some(&admission_id)
                    || !self.projection_browser_actions.insert(action_id)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::BrowserObservationAdmitted => {
                let admission_id = non_empty("admission_id")?;
                let observation_id = non_empty("observation_id")?;
                let observation_kind = non_empty("observation_kind")?;
                if !matches!(
                    observation_kind.as_str(),
                    "read_url" | "accessibility" | "network_log" | "wait"
                ) || !matches!(
                    object
                        .get("observer_role")
                        .and_then(serde_json::Value::as_str),
                    Some("observer")
                ) || !matches!(
                    object.get("status").and_then(serde_json::Value::as_str),
                    Some("ADMITTED")
                ) || object
                    .get("lease_id")
                    .and_then(serde_json::Value::as_u64)
                    .is_none_or(|value| value == 0)
                    || object
                        .get("observation_count")
                        .and_then(serde_json::Value::as_u64)
                        .is_none_or(|value| value == 0)
                    || !["input_hash", "policy_hash"].iter().all(|key| {
                        object
                            .get(*key)
                            .and_then(serde_json::Value::as_str)
                            .is_some_and(is_hex_digest)
                    })
                    || self
                        .projection_browser_observation_admissions
                        .contains_key(&observation_id)
                    || self
                        .projection_browser_observation_admissions
                        .values()
                        .any(|value| value == &admission_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_browser_observation_admissions
                    .insert(observation_id, admission_id);
            }
            LabEventKind::BrowserObservationRecorded => {
                let admission_id = non_empty("admission_id")?;
                let observation_id = non_empty("observation_id")?;
                let observation_kind = non_empty("observation_kind")?;
                if !matches!(
                    observation_kind.as_str(),
                    "read_url" | "accessibility" | "network_log" | "wait"
                ) || !matches!(
                    object
                        .get("observer_role")
                        .and_then(serde_json::Value::as_str),
                    Some("observer")
                ) || !matches!(
                    object.get("status").and_then(serde_json::Value::as_str),
                    Some("SUCCESS" | "REJECTED" | "CANCELLED")
                ) || object
                    .get("lease_id")
                    .and_then(serde_json::Value::as_u64)
                    .is_none_or(|value| value == 0)
                    || object
                        .get("observation_count")
                        .and_then(serde_json::Value::as_u64)
                        .is_none_or(|value| value == 0)
                    || !["input_hash", "result_hash", "policy_hash"]
                        .iter()
                        .all(|key| {
                            object
                                .get(*key)
                                .and_then(serde_json::Value::as_str)
                                .is_some_and(is_hex_digest)
                        })
                    || self
                        .projection_browser_observation_admissions
                        .get(&observation_id)
                        != Some(&admission_id)
                    || !self.projection_browser_observations.insert(observation_id)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::SkillAdmissionRecorded => {
                let admission_hash = non_empty("admission_hash")?;
                let skill_id = non_empty("skill_id")?;
                let version = non_empty("version")?;
                let mission_id = non_empty("mission_id")?;
                if !is_hex_digest(&admission_hash)
                    || !is_hex_digest(
                        object
                            .get("manifest_hash")
                            .and_then(serde_json::Value::as_str)
                            .ok_or(LabError::InvalidRecord)?,
                    )
                    || !is_hex_digest(
                        object
                            .get("replay_parent_hash")
                            .and_then(serde_json::Value::as_str)
                            .ok_or(LabError::InvalidRecord)?,
                    )
                    || object
                        .get("mission_epoch")
                        .and_then(serde_json::Value::as_u64)
                        .is_none()
                    || string_array("granted_capabilities").is_err()
                {
                    return Err(LabError::InvalidRecord);
                }
                let results = object
                    .get("precondition_results")
                    .and_then(serde_json::Value::as_array)
                    .ok_or(LabError::InvalidRecord)?;
                let mut names = Vec::with_capacity(results.len());
                for result in results {
                    let pair = result.as_array().ok_or(LabError::InvalidRecord)?;
                    if pair.len() != 2
                        || pair[0].as_str().is_none_or(|value| value.trim().is_empty())
                        || pair[1].as_bool() != Some(true)
                    {
                        return Err(LabError::InvalidRecord);
                    }
                    names.push(pair[0].as_str().unwrap_or_default().to_string());
                }
                if names.windows(2).any(|window| window[0] >= window[1])
                    || self
                        .projection_skill_admissions
                        .insert(
                            admission_hash,
                            ProjectionSkillAdmission {
                                skill_id,
                                version,
                                mission_id,
                            },
                        )
                        .is_some()
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::SkillExecutionRecorded => {
                let execution_hash = non_empty("execution_hash")?;
                if !is_hex_digest(&execution_hash)
                    || !is_hex_digest(
                        object
                            .get("admission_hash")
                            .and_then(serde_json::Value::as_str)
                            .ok_or(LabError::InvalidRecord)?,
                    )
                    || !is_hex_digest(
                        object
                            .get("input_hash")
                            .and_then(serde_json::Value::as_str)
                            .ok_or(LabError::InvalidRecord)?,
                    )
                    || !is_hex_digest(
                        object
                            .get("result_hash")
                            .and_then(serde_json::Value::as_str)
                            .ok_or(LabError::InvalidRecord)?,
                    )
                    || !is_hex_digest(
                        object
                            .get("artifact_hash")
                            .and_then(serde_json::Value::as_str)
                            .ok_or(LabError::InvalidRecord)?,
                    )
                    || non_empty("skill_id").is_err()
                    || non_empty("version").is_err()
                    || non_empty("mission_id").is_err()
                    || non_empty("replay_parent_hash").is_err()
                    || non_empty("validator_version").is_err()
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("SUCCESS") | Some("REJECTED") | Some("CANCELLED")
                    )
                    || !self.projection_skill_executions.insert(execution_hash)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::CancellationAdmitted => {
                let admission_id = non_empty("admission_id")?;
                let request_id = non_empty("request_id")?;
                non_empty("mission_id")?;
                non_empty("reason")?;
                let input_hash = non_empty("input_hash")?;
                let policy_hash = non_empty("policy_hash")?;
                if !is_hex_digest(&input_hash)
                    || !is_hex_digest(&policy_hash)
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("ADMITTED")
                    )
                    || self
                        .projection_cancellation_admissions
                        .contains_key(&request_id)
                    || self
                        .projection_cancellation_admissions
                        .values()
                        .any(|value| value == &admission_id)
                {
                    return Err(LabError::InvalidRecord);
                }
                self.projection_cancellation_admissions
                    .insert(request_id, admission_id);
            }
            LabEventKind::CancellationRecorded => {
                let admission_id = non_empty("admission_id")?;
                let request_id = non_empty("request_id")?;
                non_empty("mission_id")?;
                non_empty("reason")?;
                let result_hash = non_empty("result_hash")?;
                if !is_hex_digest(&result_hash)
                    || !matches!(
                        object.get("status").and_then(serde_json::Value::as_str),
                        Some("SUCCESS" | "REJECTED")
                    )
                    || self.projection_cancellation_admissions.get(&request_id)
                        != Some(&admission_id)
                    || !self.projection_cancellations.insert(request_id)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::SecurityEventRecorded
            | LabEventKind::BlockerRecorded
            | LabEventKind::BlockerResolved
            | LabEventKind::FinalizationStarted
            | LabEventKind::FinalizationReopened => {
                non_empty("reason")?;
            }
            LabEventKind::StateChanged => {
                non_empty("state")?;
            }
            _ => {}
        }
        Ok(())
    }

    fn validate_projection_binding(
        &self,
        event: &LabEvent,
        payload: &serde_json::Value,
    ) -> Result<(), LabError> {
        let object = payload.as_object().ok_or(LabError::InvalidRecord)?;
        let parent_hex = digest_hex(event.previous_event_hash);
        match event.kind {
            LabEventKind::ExperimentExecutionAdmitted
            | LabEventKind::ExperimentExecutionRecorded
            | LabEventKind::ToolExecutionAdmitted
            | LabEventKind::ToolExecutionRecorded
            | LabEventKind::CancellationAdmitted
            | LabEventKind::CancellationRecorded => {
                let mission_id = object
                    .get("mission_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                let replay_parent_hash = object
                    .get("replay_parent_hash")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                if mission_id != self.runtime.mission().mission_id
                    || replay_parent_hash != parent_hex
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::SkillAdmissionRecorded => {
                let mission_id = object
                    .get("mission_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                let mission_epoch = object
                    .get("mission_epoch")
                    .and_then(serde_json::Value::as_u64)
                    .ok_or(LabError::InvalidRecord)?;
                let replay_parent_hash = object
                    .get("replay_parent_hash")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                if mission_id != self.runtime.mission().mission_id
                    || mission_epoch.saturating_add(1) != event.state_epoch
                    || replay_parent_hash != parent_hex
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            LabEventKind::SkillExecutionRecorded => {
                let admission_hash = object
                    .get("admission_hash")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                let Some(admission) = self.projection_skill_admissions.get(admission_hash) else {
                    return Err(LabError::InvalidRecord);
                };
                let skill_id = object
                    .get("skill_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                let version = object
                    .get("version")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                let mission_id = object
                    .get("mission_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                let replay_parent_hash = object
                    .get("replay_parent_hash")
                    .and_then(serde_json::Value::as_str)
                    .ok_or(LabError::InvalidRecord)?;
                if skill_id != admission.skill_id
                    || version != admission.version
                    || mission_id != admission.mission_id
                    || mission_id != self.runtime.mission().mission_id
                    || replay_parent_hash != parent_hex
                {
                    return Err(LabError::InvalidRecord);
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn validate_projection_snapshot(&self) -> Result<(), LabError> {
        if self
            .projection_sources
            .iter()
            .any(|id| id.trim().is_empty())
            || self.projection_claims.iter().any(|id| id.trim().is_empty())
            || self
                .projection_hypotheses
                .iter()
                .any(|id| id.trim().is_empty())
        {
            return Err(LabError::InvalidEvent);
        }
        for (experiment_id, experiment) in &self.projection_experiments {
            if experiment_id.trim().is_empty()
                || experiment.hypothesis_id.trim().is_empty()
                || experiment.preregistered_seeds.len() < 5
                || (!self
                    .projection_hypotheses
                    .contains(&experiment.hypothesis_id)
                    && !self
                        .runtime
                        .hypotheses
                        .contains_key(&experiment.hypothesis_id))
            {
                return Err(LabError::InvalidEvent);
            }
        }
        for (observation_id, observation) in &self.projection_observations {
            if observation_id.trim().is_empty()
                || observation.experiment_id.trim().is_empty()
                || observation.environment_hash.trim().is_empty()
                || (!self
                    .projection_experiments
                    .contains_key(&observation.experiment_id)
                    && !self
                        .runtime
                        .experiments
                        .contains_key(&observation.experiment_id))
            {
                return Err(LabError::InvalidEvent);
            }
            if let Some(parent_id) = &observation.replication_of {
                let Some(parent) = self.projection_observations.get(parent_id) else {
                    return Err(LabError::InvalidEvent);
                };
                if parent.experiment_id != observation.experiment_id
                    || parent.environment_hash == observation.environment_hash
                    || !observation.clean
                {
                    return Err(LabError::InvalidEvent);
                }
            }
        }
        for (execution_id, admission) in &self.projection_experiment_execution_admissions {
            if execution_id.trim().is_empty()
                || admission.admission_id.trim().is_empty()
                || admission.experiment_id.trim().is_empty()
                || admission.attempt == 0
                || (!self
                    .projection_experiments
                    .contains_key(&admission.experiment_id)
                    && !self
                        .runtime
                        .experiments
                        .contains_key(&admission.experiment_id))
                || !is_hex_digest(&admission.input_hash)
                || !is_hex_digest(&admission.policy_hash)
            {
                return Err(LabError::InvalidEvent);
            }
        }
        for (execution_id, admission) in &self.projection_tool_execution_admissions {
            if execution_id.trim().is_empty()
                || admission.admission_id.trim().is_empty()
                || admission.tool_name.trim().is_empty()
                || admission.attempt == 0
                || admission.lease_id == 0
                || admission.effect_class.trim().is_empty()
                || !matches!(admission.actor_role.as_str(), "actor" | "observer")
                || admission.expected_observation_schema.trim().is_empty()
                || admission.stop_rule.trim().is_empty()
                || !is_hex_digest(&admission.input_hash)
                || !is_hex_digest(&admission.policy_hash)
            {
                return Err(LabError::InvalidEvent);
            }
        }
        if self
            .projection_experiment_executions
            .iter()
            .any(|id| id.trim().is_empty())
            || self
                .projection_tool_executions
                .iter()
                .any(|id| id.trim().is_empty())
            || self
                .projection_cancellations
                .iter()
                .any(|id| id.trim().is_empty())
            || self
                .projection_cancellation_admissions
                .iter()
                .any(|(request_id, admission_id)| {
                    request_id.trim().is_empty() || admission_id.trim().is_empty()
                })
        {
            return Err(LabError::InvalidEvent);
        }
        if self
            .projection_browser_actions
            .iter()
            .any(|id| id.trim().is_empty())
            || self
                .projection_browser_observations
                .iter()
                .any(|id| id.trim().is_empty())
            || self
                .projection_browser_action_admissions
                .iter()
                .any(|(action_id, admission_id)| {
                    action_id.trim().is_empty() || admission_id.trim().is_empty()
                })
            || self.projection_browser_observation_admissions.iter().any(
                |(observation_id, admission_id)| {
                    observation_id.trim().is_empty() || admission_id.trim().is_empty()
                },
            )
            || self
                .projection_skill_executions
                .iter()
                .any(|hash| !is_hex_digest(hash))
        {
            return Err(LabError::InvalidEvent);
        }
        for (admission_hash, admission) in &self.projection_skill_admissions {
            if !is_hex_digest(admission_hash)
                || admission.skill_id.trim().is_empty()
                || admission.version.trim().is_empty()
                || admission.mission_id != self.runtime.mission().mission_id
            {
                return Err(LabError::InvalidEvent);
            }
        }
        if self
            .projection_research_programs
            .iter()
            .any(|hash| !is_hex_digest(hash))
            || self.projection_research_program_admissions.iter().any(
                |(program_hash, admission_id)| {
                    !is_hex_digest(program_hash) || admission_id.trim().is_empty()
                },
            )
            || self.projection_payloads.iter().any(|(sequence, payload)| {
                if *sequence == 0 || *sequence > self.runtime.events.len() as u64 {
                    return true;
                }
                let Some(event) = self.runtime.events.get(*sequence as usize - 1) else {
                    return true;
                };
                payload.as_object().is_none()
                    || event.payload_hash != projection_payload_hash(payload)
            })
        {
            return Err(LabError::InvalidEvent);
        }
        Ok(())
    }

    fn validate_projection_event(&self, event: &LabEvent) -> Result<(), LabError> {
        let expected_sequence = self.runtime.events.len() as u64 + 1;
        let previous = self
            .runtime
            .events
            .last()
            .map(|item| item.event_hash)
            .unwrap_or([0; 32]);
        if event.sequence != expected_sequence
            || event.previous_event_hash != previous
            || event.state_epoch < self.runtime.state_epoch
            || !event.is_valid()
        {
            return Err(LabError::InvalidEvent);
        }
        Ok(())
    }

    pub fn transition(&mut self, next: LabRunState) -> Result<(), LabError> {
        self.runtime.transition(next)
    }

    pub fn record_source(&mut self, source: SourceRecord) -> Result<(), LabError> {
        self.runtime.record_source(source)
    }

    pub fn record_claim(&mut self, claim: ClaimRecord) -> Result<(), LabError> {
        self.runtime.record_claim(claim)
    }

    pub fn record_hypothesis(&mut self, hypothesis: HypothesisRecord) -> Result<(), LabError> {
        self.runtime.record_hypothesis(hypothesis)
    }

    pub fn schedule_experiment(&mut self, experiment: ExperimentSpec) -> Result<(), LabError> {
        self.runtime.schedule_experiment(experiment)
    }

    pub fn record_observation(&mut self, observation: ObservationRecord) -> Result<(), LabError> {
        self.runtime.record_observation(observation)
    }

    pub fn record_research_program(
        &mut self,
        receipt: ResearchProgramReceipt,
    ) -> Result<(), LabError> {
        self.runtime.record_research_program(receipt)
    }

    pub fn record_security_event(&mut self, event: SecurityEventRecord) -> Result<(), LabError> {
        self.runtime.record_security_event(event)
    }

    pub fn review_and_complete(&mut self) -> Result<(), LabError> {
        if !self.finalization.finalizer_allowed() {
            return Err(LabError::InvalidTransition);
        }
        self.runtime.review_and_complete()
    }
}

impl LabRuntime {
    pub fn new(mission: LabMissionSpec) -> Result<Self, LabError> {
        mission.validate()?;
        let mut runtime = Self {
            mission,
            state: LabRunState::Planned,
            state_epoch: 0,
            events: Vec::new(),
            sources: BTreeMap::new(),
            claims: BTreeMap::new(),
            hypotheses: BTreeMap::new(),
            experiments: BTreeMap::new(),
            observations: BTreeMap::new(),
        };
        runtime.append_event(LabEventKind::MissionCreated, runtime.mission.mission_hash());
        Ok(runtime)
    }

    pub fn mission(&self) -> &LabMissionSpec {
        &self.mission
    }

    pub fn state(&self) -> LabRunState {
        self.state
    }

    pub fn state_epoch(&self) -> u64 {
        self.state_epoch
    }

    pub fn events(&self) -> &[LabEvent] {
        &self.events
    }

    pub fn sources(&self) -> &BTreeMap<String, SourceRecord> {
        &self.sources
    }

    pub fn claims(&self) -> &BTreeMap<String, ClaimRecord> {
        &self.claims
    }

    pub fn hypotheses(&self) -> &BTreeMap<String, HypothesisRecord> {
        &self.hypotheses
    }

    pub fn experiments(&self) -> &BTreeMap<String, ExperimentSpec> {
        &self.experiments
    }

    pub fn observations(&self) -> &BTreeMap<String, ObservationRecord> {
        &self.observations
    }

    pub fn replayable(&self) -> bool {
        verify_event_chain(&self.events)
    }

    pub fn snapshot_json(&self) -> Result<String, LabError> {
        serde_json::to_string(self).map_err(|_| LabError::InvalidEvent)
    }

    pub fn from_snapshot_json(snapshot: &str) -> Result<Self, LabError> {
        let runtime: Self = serde_json::from_str(snapshot).map_err(|_| LabError::InvalidEvent)?;
        runtime.validate_snapshot()?;
        Ok(runtime)
    }

    fn validate_snapshot(&self) -> Result<(), LabError> {
        self.mission.validate()?;
        if !verify_event_chain(&self.events) {
            return Err(LabError::InvalidEvent);
        }
        let Some(first_event) = self.events.first() else {
            return Err(LabError::InvalidEvent);
        };
        if first_event.kind != LabEventKind::MissionCreated
            || first_event.payload_hash != self.mission.mission_hash()
            || self.state_epoch
                != self
                    .events
                    .last()
                    .map(|event| event.state_epoch)
                    .unwrap_or(0)
        {
            return Err(LabError::InvalidEvent);
        }
        for source in self.sources.values() {
            source.validate()?;
        }
        for claim in self.claims.values() {
            claim.validate(&self.sources)?;
        }
        for hypothesis in self.hypotheses.values() {
            hypothesis.validate(&self.claims)?;
        }
        for experiment in self.experiments.values() {
            experiment.validate(&self.hypotheses)?;
        }
        for observation in self.observations.values() {
            observation.validate(&self.experiments, &self.observations)?;
        }
        if self.state == LabRunState::Completed
            && (self.hypotheses.is_empty()
                || self.experiments.is_empty()
                || self.observations.is_empty()
                || self
                    .observations
                    .values()
                    .any(|observation| !observation.valid)
                || self.experiments.values().any(|experiment| {
                    self.observations
                        .values()
                        .filter(|observation| observation.experiment_id == experiment.experiment_id)
                        .count()
                        < experiment.expected_observations as usize
                })
                || self.experiments.values().any(|experiment| {
                    self.observations
                        .values()
                        .filter(|observation| {
                            observation.experiment_id == experiment.experiment_id
                                && observation.replication_of.is_some()
                                && observation.clean
                        })
                        .count()
                        < experiment.min_clean_replicates as usize
                }))
        {
            return Err(LabError::IncompleteMission);
        }
        Ok(())
    }

    pub fn transition(&mut self, next: LabRunState) -> Result<(), LabError> {
        if self.state == next {
            return Ok(());
        }
        if !self.state.can_transition_to(next) {
            return Err(LabError::InvalidTransition);
        }
        self.state = next;
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(LabEventKind::StateChanged, canonical_hash(&next));
        Ok(())
    }

    pub fn record_source(&mut self, source: SourceRecord) -> Result<(), LabError> {
        if !matches!(self.state, LabRunState::Planned | LabRunState::Researching) {
            return Err(LabError::InvalidTransition);
        }
        source.validate()?;
        if self.sources.contains_key(&source.source_id) {
            return Err(LabError::InvalidRecord);
        }
        self.sources
            .insert(source.source_id.clone(), source.clone());
        if self.state == LabRunState::Planned {
            self.transition(LabRunState::Researching)?;
        }
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(LabEventKind::SourceCaptured, canonical_hash(&source));
        Ok(())
    }

    pub fn record_claim(&mut self, claim: ClaimRecord) -> Result<(), LabError> {
        if !matches!(
            self.state,
            LabRunState::Researching | LabRunState::Experimenting | LabRunState::Reviewing
        ) {
            return Err(LabError::InvalidTransition);
        }
        claim.validate(&self.sources)?;
        if self.claims.contains_key(&claim.claim_id) {
            return Err(LabError::InvalidRecord);
        }
        self.claims.insert(claim.claim_id.clone(), claim.clone());
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(LabEventKind::ClaimRecorded, canonical_hash(&claim));
        Ok(())
    }

    pub fn record_hypothesis(&mut self, hypothesis: HypothesisRecord) -> Result<(), LabError> {
        if !matches!(
            self.state,
            LabRunState::Researching | LabRunState::Experimenting | LabRunState::Reviewing
        ) {
            return Err(LabError::InvalidTransition);
        }
        hypothesis.validate(&self.claims)?;
        if self.hypotheses.contains_key(&hypothesis.hypothesis_id) {
            return Err(LabError::InvalidRecord);
        }
        self.hypotheses
            .insert(hypothesis.hypothesis_id.clone(), hypothesis.clone());
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(
            LabEventKind::HypothesisRecorded,
            canonical_hash(&hypothesis),
        );
        Ok(())
    }

    pub fn schedule_experiment(&mut self, experiment: ExperimentSpec) -> Result<(), LabError> {
        if !matches!(
            self.state,
            LabRunState::Researching | LabRunState::Experimenting | LabRunState::Reviewing
        ) {
            return Err(LabError::InvalidTransition);
        }
        experiment.validate(&self.hypotheses)?;
        if self.experiments.contains_key(&experiment.experiment_id) {
            return Err(LabError::InvalidRecord);
        }
        self.experiments
            .insert(experiment.experiment_id.clone(), experiment.clone());
        if self.state == LabRunState::Researching {
            self.transition(LabRunState::Experimenting)?;
        }
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(
            LabEventKind::ExperimentScheduled,
            canonical_hash(&experiment),
        );
        Ok(())
    }

    pub fn record_observation(&mut self, observation: ObservationRecord) -> Result<(), LabError> {
        if !matches!(
            self.state,
            LabRunState::Experimenting | LabRunState::Reviewing
        ) {
            return Err(LabError::InvalidTransition);
        }
        observation.validate(&self.experiments, &self.observations)?;
        if self.observations.contains_key(&observation.observation_id) {
            return Err(LabError::InvalidRecord);
        }
        self.observations
            .insert(observation.observation_id.clone(), observation.clone());
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(
            LabEventKind::ObservationRecorded,
            canonical_hash(&observation),
        );
        Ok(())
    }

    pub fn record_research_program(
        &mut self,
        receipt: ResearchProgramReceipt,
    ) -> Result<(), LabError> {
        if matches!(self.state, LabRunState::Completed | LabRunState::Aborted) {
            return Err(LabError::InvalidTransition);
        }
        receipt.validate()?;
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(
            LabEventKind::ResearchProgramExecuted,
            canonical_hash(&receipt),
        );
        Ok(())
    }

    pub fn record_security_event(&mut self, event: SecurityEventRecord) -> Result<(), LabError> {
        if self.state == LabRunState::Aborted {
            return Err(LabError::InvalidTransition);
        }
        event.validate()?;
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(LabEventKind::SecurityEventRecorded, canonical_hash(&event));
        Ok(())
    }

    fn record_budget_admitted(&mut self, spend: crate::gt96::BudgetVector) {
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(LabEventKind::BudgetAdmitted, canonical_hash(&spend));
    }

    fn record_finalization_event(&mut self, kind: LabEventKind) {
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.append_event(kind, canonical_hash(&self.state_epoch));
    }

    pub fn review_and_complete(&mut self) -> Result<(), LabError> {
        if self.hypotheses.is_empty()
            || self.experiments.is_empty()
            || self.observations.is_empty()
            || self
                .observations
                .values()
                .any(|observation| !observation.valid)
            || self.experiments.values().any(|experiment| {
                self.observations
                    .values()
                    .filter(|observation| observation.experiment_id == experiment.experiment_id)
                    .count()
                    < experiment.expected_observations as usize
            })
            || self.experiments.values().any(|experiment| {
                self.observations
                    .values()
                    .filter(|observation| {
                        observation.experiment_id == experiment.experiment_id
                            && observation.replication_of.is_some()
                            && observation.clean
                    })
                    .count()
                    < experiment.min_clean_replicates as usize
            })
        {
            return Err(LabError::IncompleteMission);
        }
        if self.state != LabRunState::Reviewing {
            self.transition(LabRunState::Reviewing)?;
        }
        self.transition(LabRunState::Completed)
    }

    pub fn manifest(&self) -> LabRunManifest {
        let event_root_hash = self
            .events
            .last()
            .map(|event| event.event_hash)
            .unwrap_or([0; 32]);
        let mut manifest = LabRunManifest {
            mission_hash: self.mission.mission_hash(),
            state: self.state,
            state_epoch: self.state_epoch,
            event_count: self.events.len() as u64,
            event_root_hash,
            source_count: self.sources.len(),
            claim_count: self.claims.len(),
            hypothesis_count: self.hypotheses.len(),
            experiment_count: self.experiments.len(),
            observation_count: self.observations.len(),
            manifest_hash: [0; 32],
        };
        manifest.manifest_hash = canonical_hash(&manifest_without_hash(&manifest));
        manifest
    }

    fn append_event(&mut self, kind: LabEventKind, payload_hash: [u8; 32]) {
        let sequence = self.events.len() as u64 + 1;
        let previous = self
            .events
            .last()
            .map(|event| event.event_hash)
            .unwrap_or([0; 32]);
        self.events.push(LabEvent::new(
            sequence,
            self.state_epoch,
            kind,
            payload_hash,
            previous,
        ));
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ManifestWithoutHash {
    mission_hash: [u8; 32],
    state: LabRunState,
    state_epoch: u64,
    event_count: u64,
    event_root_hash: [u8; 32],
    source_count: usize,
    claim_count: usize,
    hypothesis_count: usize,
    experiment_count: usize,
    observation_count: usize,
}

fn manifest_without_hash(manifest: &LabRunManifest) -> ManifestWithoutHash {
    ManifestWithoutHash {
        mission_hash: manifest.mission_hash,
        state: manifest.state,
        state_epoch: manifest.state_epoch,
        event_count: manifest.event_count,
        event_root_hash: manifest.event_root_hash,
        source_count: manifest.source_count,
        claim_count: manifest.claim_count,
        hypothesis_count: manifest.hypothesis_count,
        experiment_count: manifest.experiment_count,
        observation_count: manifest.observation_count,
    }
}

pub fn canonical_hash<T: Serialize>(value: &T) -> [u8; 32] {
    let encoded = serde_json::to_vec(value).expect("lab records are serializable");
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-lab-canonical-v1\0");
    hasher.update(&encoded);
    *hasher.finalize().as_bytes()
}

/// Hash the canonical JSON payload used by the language/runtime projection
/// boundary.  This is deliberately distinct from the typed Rust record hash:
/// adapters may carry additional presentation fields, but they cannot alter
/// the exact bytes the native validator admitted.
fn projection_payload_hash(value: &serde_json::Value) -> [u8; 32] {
    let encoded = serde_json::to_vec(value).expect("JSON projection payloads are serializable");
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-lab-projection-payload-v1\0");
    hasher.update(&encoded);
    *hasher.finalize().as_bytes()
}

fn is_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn digest_hex(value: [u8; 32]) -> String {
    let mut result = String::with_capacity(64);
    for byte in value {
        result.push(char::from(b"0123456789abcdef"[(byte >> 4) as usize]));
        result.push(char::from(b"0123456789abcdef"[(byte & 0x0f) as usize]));
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gt96::BudgetVector;

    fn mission() -> LabMissionSpec {
        LabMissionSpec::new(
            "mission-1",
            "test a hypothesis",
            "local deterministic fixture",
            [7; 32],
            BudgetPolicy {
                total: BudgetVector {
                    tokens: 100,
                    ..BudgetVector::zero()
                },
                finalization_reserve: BudgetVector {
                    tokens: 20,
                    ..BudgetVector::zero()
                },
                recovery_reserve: BudgetVector {
                    tokens: 10,
                    ..BudgetVector::zero()
                },
            },
            true,
            20,
        )
        .unwrap()
    }

    fn source() -> SourceRecord {
        SourceRecord {
            source_id: "source-1".to_string(),
            uri: "https://example.invalid/paper".to_string(),
            retrieved_at_ms: 1,
            content_hash: [1; 32],
            snapshot_hash: [2; 32],
            trust_tier: 1,
            extractor: "test".to_string(),
            provenance_cluster: "fixture-cluster".to_string(),
        }
    }

    #[test]
    fn native_lab_controller_binds_budget_and_finalization_to_replay_epoch() {
        let mut controller = LabController::new(mission()).unwrap();
        assert_eq!(controller.runtime().state(), LabRunState::Planned);
        assert_eq!(controller.budget_remaining().tokens, 100);
        controller
            .admit_exploration(
                0,
                BudgetVector {
                    tokens: 10,
                    ..BudgetVector::zero()
                },
            )
            .unwrap();
        assert_eq!(controller.steps(), 1);
        assert_eq!(controller.runtime().state_epoch(), 1);
        assert_eq!(controller.budget_remaining().tokens, 90);
        let before = controller.budget_remaining();
        assert_eq!(
            controller.admit_exploration(
                0,
                BudgetVector {
                    tokens: 1,
                    ..BudgetVector::zero()
                },
            ),
            Err(LabError::InvalidTransition)
        );
        assert_eq!(controller.budget_remaining(), before);

        controller.begin_finalization().unwrap();
        assert_eq!(
            controller.finalization_state(),
            crate::gt96::FinalizationState::Finalizing
        );
        assert_eq!(
            controller.admit_exploration(
                controller.runtime().state_epoch(),
                BudgetVector {
                    tokens: 1,
                    ..BudgetVector::zero()
                },
            ),
            Err(LabError::InvalidTransition)
        );
        controller.reopen_for_critical_gap().unwrap();
        assert_eq!(
            controller.finalization_state(),
            crate::gt96::FinalizationState::Reopened
        );
        assert!(controller.runtime().replayable());
        assert!(
            controller
                .runtime()
                .events()
                .iter()
                .any(|event| event.kind == LabEventKind::BudgetAdmitted)
        );
    }

    #[test]
    fn native_lab_controller_admits_projection_events_only_at_chain_boundary() {
        let mut controller = LabController::new(mission()).unwrap();
        let first = controller.runtime().events()[0].clone();
        controller.admit_projection_event(first, None).unwrap();
        let parent = controller.runtime().events()[0].event_hash;
        let invalid = LabEvent::new(2, 0, LabEventKind::ReviewRecorded, [3; 32], [9; 32]);
        assert_eq!(
            controller.admit_projection_event(invalid, None),
            Err(LabError::InvalidEvent)
        );
        assert_eq!(controller.runtime().events().len(), 1);
        let typed_bypass =
            LabEvent::new(2, 0, LabEventKind::BrowserActionRecorded, [3; 32], parent);
        assert_eq!(
            controller.admit_projection_event(typed_bypass, None),
            Err(LabError::InvalidRecord)
        );
        let observer_typed_bypass = LabEvent::new(
            2,
            0,
            LabEventKind::BrowserObservationRecorded,
            [3; 32],
            parent,
        );
        assert_eq!(
            controller.admit_projection_event(observer_typed_bypass, None),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(controller.runtime().events().len(), 1);
        let valid = LabEvent::new(2, 0, LabEventKind::ReviewRecorded, [3; 32], parent);
        let valid_hash = valid.event_hash;
        controller.admit_projection_event(valid, None).unwrap();
        let state_event = LabEvent::new(3, 1, LabEventKind::StateChanged, [4; 32], valid_hash);
        controller
            .admit_projection_event(state_event, Some(LabRunState::Researching))
            .unwrap();
        let finalization_event = LabEvent::new(
            4,
            2,
            LabEventKind::FinalizationStarted,
            [5; 32],
            controller.runtime().events()[2].event_hash,
        );
        controller
            .admit_projection_event(finalization_event, None)
            .unwrap();
        assert!(controller.runtime().replayable());
        assert_eq!(controller.runtime().events().len(), 4);
        assert_eq!(controller.runtime().state(), LabRunState::Researching);
        assert_eq!(
            controller.finalization_state(),
            crate::gt96::FinalizationState::Finalizing
        );
        let reviewing = LabEvent::new(
            5,
            3,
            LabEventKind::StateChanged,
            [6; 32],
            controller.runtime().events()[3].event_hash,
        );
        controller
            .admit_projection_event(reviewing, Some(LabRunState::Reviewing))
            .unwrap();
        let completed = LabEvent::new(
            6,
            4,
            LabEventKind::StateChanged,
            [7; 32],
            controller.runtime().events()[4].event_hash,
        );
        controller
            .admit_projection_event(completed, Some(LabRunState::Completed))
            .unwrap();
        let post_completion_review = LabEvent::new(
            7,
            5,
            LabEventKind::ReviewRecorded,
            [8; 32],
            controller.runtime().events()[5].event_hash,
        );
        controller
            .admit_projection_event(post_completion_review, None)
            .unwrap();
        assert_eq!(controller.runtime().state(), LabRunState::Completed);
        assert!(controller.runtime().replayable());
        assert_eq!(controller.runtime().events().len(), 7);
    }

    #[test]
    fn native_projection_record_admission_validates_cross_references_and_snapshot_state() {
        let mut controller = LabController::new(mission()).unwrap();
        let mut next_sequence = 2_u64;
        let mut next_epoch = 1_u64;
        let mut previous = controller.runtime().events()[0].event_hash;
        let admit = |controller: &mut LabController,
                     next_sequence: &mut u64,
                     next_epoch: &mut u64,
                     previous: &mut [u8; 32],
                     kind: LabEventKind,
                     payload: serde_json::Value| {
            let payload_json = serde_json::to_string(&payload).unwrap();
            let event = LabEvent::new(
                *next_sequence,
                *next_epoch,
                kind,
                projection_payload_hash(&payload),
                *previous,
            );
            *previous = event.event_hash;
            *next_sequence += 1;
            *next_epoch += 1;
            controller
                .admit_projection_record(event, &payload_json, None)
                .unwrap();
        };

        let state_payload = serde_json::json!({"state": "researching"});
        let state_json = serde_json::to_string(&state_payload).unwrap();
        let state_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::StateChanged,
            projection_payload_hash(&state_payload),
            previous,
        );
        previous = state_event.event_hash;
        next_sequence += 1;
        next_epoch += 1;
        controller
            .admit_projection_record(state_event, &state_json, Some(LabRunState::Researching))
            .unwrap();
        assert_eq!(controller.runtime().state(), LabRunState::Researching);

        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::SourceCaptured,
            serde_json::json!({
                "source_id": "s1",
                "uri": "https://example.invalid/source",
                "content_hash": "content",
                "snapshot_hash": "snapshot",
                "retrieved_at_ms": 1,
                "trust_tier": 1,
                "extractor": "fixture"
            }),
        );
        let invalid_claim = serde_json::json!({
            "claim_id": "c-invalid",
            "statement": "missing source",
            "source_ids": ["missing"],
            "confidence_bps": 5000
        });
        let invalid_json = serde_json::to_string(&invalid_claim).unwrap();
        let invalid_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::ClaimRecorded,
            projection_payload_hash(&invalid_claim),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(invalid_event, &invalid_json, None),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(controller.runtime().events().len(), 3);

        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::ClaimRecorded,
            serde_json::json!({
                "claim_id": "c1",
                "statement": "supported",
                "source_ids": ["s1"],
                "confidence_bps": 5000
            }),
        );
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::HypothesisRecorded,
            serde_json::json!({
                "hypothesis_id": "h1",
                "statement": "it works",
                "prior_bps": 5000,
                "falsifiers": ["negative result"],
                "supporting_claim_ids": ["c1"],
                "contradicting_claim_ids": []
            }),
        );
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::ExperimentScheduled,
            serde_json::json!({
                "experiment_id": "e1",
                "hypothesis_id": "h1",
                "design": "paired",
                "variables": ["x"],
                "controls": ["baseline"],
                "preregistered_seeds": [1, 2, 3, 4, 5],
                "expected_observations": 1,
                "measurement_unit": "score",
                "uncertainty_required": false,
                "min_clean_replicates": 0
            }),
        );
        let experiment_state_payload = serde_json::json!({"state": "experimenting"});
        let experiment_state_json = serde_json::to_string(&experiment_state_payload).unwrap();
        let experiment_state_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::StateChanged,
            projection_payload_hash(&experiment_state_payload),
            previous,
        );
        previous = experiment_state_event.event_hash;
        next_sequence += 1;
        next_epoch += 1;
        controller
            .admit_projection_record(
                experiment_state_event,
                &experiment_state_json,
                Some(LabRunState::Experimenting),
            )
            .unwrap();
        let execution_admission_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::ExperimentExecutionAdmitted,
            serde_json::json!({
                "admission_id": "e1-attempt-1-admission",
                "execution_id": "e1-attempt-1",
                "experiment_id": "e1",
                "attempt": 1,
                "mission_id": "mission-1",
                "replay_parent_hash": execution_admission_parent,
                "input_hash": "a".repeat(64),
                "policy_hash": "b".repeat(64),
                "status": "ADMITTED"
            }),
        );
        let execution_record_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::ExperimentExecutionRecorded,
            serde_json::json!({
                "admission_id": "e1-attempt-1-admission",
                "execution_id": "e1-attempt-1",
                "experiment_id": "e1",
                "attempt": 1,
                "observation_count": 1,
                "mission_id": "mission-1",
                "replay_parent_hash": execution_record_parent,
                "input_hash": "a".repeat(64),
                "result_hash": "c".repeat(64),
                "policy_hash": "b".repeat(64),
                "status": "SUCCESS"
            }),
        );
        let before_duplicate_execution = controller.snapshot_json().unwrap();
        let duplicate_execution_payload = serde_json::json!({
            "admission_id": "e1-attempt-1-admission",
            "execution_id": "e1-attempt-1",
            "experiment_id": "e1",
            "attempt": 1,
            "observation_count": 1,
            "mission_id": "mission-1",
            "replay_parent_hash": digest_hex(previous),
            "input_hash": "a".repeat(64),
            "result_hash": "c".repeat(64),
            "policy_hash": "b".repeat(64),
            "status": "SUCCESS"
        });
        let duplicate_execution_json = serde_json::to_string(&duplicate_execution_payload).unwrap();
        let duplicate_execution_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::ExperimentExecutionRecorded,
            projection_payload_hash(&duplicate_execution_payload),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(
                duplicate_execution_event,
                &duplicate_execution_json,
                None,
            ),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(
            controller.snapshot_json().unwrap(),
            before_duplicate_execution
        );
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::ObservationRecorded,
            serde_json::json!({
                "observation_id": "o1",
                "experiment_id": "e1",
                "seed": 1,
                "measurement": 1.0,
                "unit": "score",
                "raw_artifact_hash": "artifact",
                "environment_hash": "env",
                "valid": true,
                "uncertainty": null,
                "replication_of": null,
                "operator_id": "",
                "clean": true,
                "epistemic_status": "OBSERVED"
            }),
        );
        let before_duplicate = controller.snapshot_json().unwrap();
        let duplicate_payload = serde_json::json!({
            "observation_id": "o1",
            "experiment_id": "e1",
            "seed": 1,
            "measurement": 999.0,
            "unit": "score",
            "raw_artifact_hash": "forged-artifact",
            "environment_hash": "env",
            "valid": true,
            "uncertainty": null,
            "replication_of": null,
            "operator_id": "",
            "clean": true,
            "epistemic_status": "OBSERVED"
        });
        let duplicate_json = serde_json::to_string(&duplicate_payload).unwrap();
        let duplicate_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::ObservationRecorded,
            projection_payload_hash(&duplicate_payload),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(duplicate_event, &duplicate_json, None),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(controller.snapshot_json().unwrap(), before_duplicate);
        let cancellation_admission_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::CancellationAdmitted,
            serde_json::json!({
                "request_id": "cancel-1",
                "admission_id": "cancel-1-admission",
                "mission_id": "mission-1",
                "reason": "operator_abort",
                "replay_parent_hash": cancellation_admission_parent,
                "input_hash": "d".repeat(64),
                "policy_hash": "e".repeat(64),
                "status": "ADMITTED"
            }),
        );
        let aborted_payload = serde_json::json!({"state": "aborted"});
        let aborted_json = serde_json::to_string(&aborted_payload).unwrap();
        let aborted_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::StateChanged,
            projection_payload_hash(&aborted_payload),
            previous,
        );
        previous = aborted_event.event_hash;
        next_sequence += 1;
        next_epoch += 1;
        controller
            .admit_projection_record(aborted_event, &aborted_json, Some(LabRunState::Aborted))
            .unwrap();
        let cancellation_record_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut next_sequence,
            &mut next_epoch,
            &mut previous,
            LabEventKind::CancellationRecorded,
            serde_json::json!({
                "request_id": "cancel-1",
                "admission_id": "cancel-1-admission",
                "mission_id": "mission-1",
                "reason": "operator_abort",
                "replay_parent_hash": cancellation_record_parent,
                "result_hash": "f".repeat(64),
                "status": "SUCCESS"
            }),
        );
        let before_duplicate_cancellation = controller.snapshot_json().unwrap();
        let duplicate_cancellation_payload = serde_json::json!({
            "request_id": "cancel-1",
            "admission_id": "cancel-1-admission",
            "mission_id": "mission-1",
            "reason": "operator_abort",
            "replay_parent_hash": digest_hex(previous),
            "result_hash": "f".repeat(64),
            "status": "SUCCESS"
        });
        let duplicate_cancellation_json =
            serde_json::to_string(&duplicate_cancellation_payload).unwrap();
        let duplicate_cancellation_event = LabEvent::new(
            next_sequence,
            next_epoch,
            LabEventKind::CancellationRecorded,
            projection_payload_hash(&duplicate_cancellation_payload),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(
                duplicate_cancellation_event,
                &duplicate_cancellation_json,
                None,
            ),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(
            controller.snapshot_json().unwrap(),
            before_duplicate_cancellation
        );
        assert!(controller.runtime().replayable());
        let snapshot = controller.snapshot_json().unwrap();
        let restored = LabController::from_snapshot_json(&snapshot).unwrap();
        assert_eq!(restored.runtime().events().len(), 13);
        assert!(restored.runtime().replayable());
    }

    #[test]
    fn native_projection_receipts_bind_browser_and_skill_side_effects() {
        let mut controller = LabController::new(mission()).unwrap();
        let mut sequence = 2_u64;
        let mut epoch = 1_u64;
        let mut previous = controller.runtime().events()[0].event_hash;
        let admit = |controller: &mut LabController,
                     sequence: &mut u64,
                     epoch: &mut u64,
                     previous: &mut [u8; 32],
                     kind: LabEventKind,
                     payload: serde_json::Value| {
            let payload_json = serde_json::to_string(&payload).unwrap();
            let event = LabEvent::new(
                *sequence,
                *epoch,
                kind,
                projection_payload_hash(&payload),
                *previous,
            );
            controller
                .admit_projection_record(event, &payload_json, None)
                .unwrap();
            *previous = controller.runtime().events().last().unwrap().event_hash;
            *sequence += 1;
            *epoch += 1;
        };

        let tool_admission_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::ToolExecutionAdmitted,
            serde_json::json!({
                "admission_id": "tool-call-1-attempt-1-admission",
                "execution_id": "tool-call-1-attempt-1",
                "tool_name": "fixture.lookup",
                "attempt": 1,
                "lease_id": 7,
                "effect_class": "read_only",
                "actor_role": "actor",
                "expected_observation_schema": "fixture.v1",
                "stop_rule": "single_call",
                "mission_id": "mission-1",
                "replay_parent_hash": tool_admission_parent,
                "input_hash": "a".repeat(64),
                "policy_hash": "b".repeat(64),
                "status": "ADMITTED"
            }),
        );
        let tool_record_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::ToolExecutionRecorded,
            serde_json::json!({
                "admission_id": "tool-call-1-attempt-1-admission",
                "execution_id": "tool-call-1-attempt-1",
                "tool_name": "fixture.lookup",
                "attempt": 1,
                "lease_id": 7,
                "effect_class": "read_only",
                "actor_role": "actor",
                "expected_observation_schema": "fixture.v1",
                "stop_rule": "single_call",
                "mission_id": "mission-1",
                "replay_parent_hash": tool_record_parent,
                "input_hash": "a".repeat(64),
                "result_hash": "c".repeat(64),
                "policy_hash": "b".repeat(64),
                "status": "SUCCESS"
            }),
        );
        let before_duplicate_tool = controller.snapshot_json().unwrap();
        let duplicate_tool_payload = serde_json::json!({
            "admission_id": "tool-call-1-attempt-1-admission",
            "execution_id": "tool-call-1-attempt-1",
            "tool_name": "fixture.lookup",
            "attempt": 1,
            "lease_id": 7,
            "effect_class": "read_only",
            "actor_role": "actor",
            "expected_observation_schema": "fixture.v1",
            "stop_rule": "single_call",
            "mission_id": "mission-1",
            "replay_parent_hash": digest_hex(previous),
            "input_hash": "a".repeat(64),
            "result_hash": "c".repeat(64),
            "policy_hash": "b".repeat(64),
            "status": "SUCCESS"
        });
        let duplicate_tool_json = serde_json::to_string(&duplicate_tool_payload).unwrap();
        let duplicate_tool_event = LabEvent::new(
            sequence,
            epoch,
            LabEventKind::ToolExecutionRecorded,
            projection_payload_hash(&duplicate_tool_payload),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(duplicate_tool_event, &duplicate_tool_json, None),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(controller.snapshot_json().unwrap(), before_duplicate_tool);

        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::BrowserActionAdmitted,
            serde_json::json!({
                "admission_id": "lease-1-action-1-admission",
                "action_id": "lease-1-action-1",
                "lease_id": 1,
                "actor_role": "actor",
                "action_kind": "click",
                "input_hash": "a".repeat(64),
                "policy_hash": "c".repeat(64),
                "status": "ADMITTED"
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::BrowserActionRecorded,
            serde_json::json!({
                "action_id": "lease-1-action-1",
                "admission_id": "lease-1-action-1-admission",
                "lease_id": 1,
                "actor_role": "actor",
                "action_kind": "click",
                "input_hash": "a".repeat(64),
                "result_hash": "b".repeat(64),
                "policy_hash": "c".repeat(64),
                "status": "SUCCESS"
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::BrowserObservationAdmitted,
            serde_json::json!({
                "admission_id": "lease-1-observation-1-admission",
                "observation_id": "lease-1-observation-1",
                "lease_id": 1,
                "observer_role": "observer",
                "observation_kind": "read_url",
                "observation_count": 1,
                "input_hash": "f".repeat(64),
                "policy_hash": "1".repeat(64),
                "status": "ADMITTED"
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::BrowserObservationRecorded,
            serde_json::json!({
                "observation_id": "lease-1-observation-1",
                "admission_id": "lease-1-observation-1-admission",
                "lease_id": 1,
                "observer_role": "observer",
                "observation_kind": "read_url",
                "observation_count": 1,
                "input_hash": "f".repeat(64),
                "result_hash": "0".repeat(64),
                "policy_hash": "1".repeat(64),
                "status": "SUCCESS"
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::ResearchProgramAdmitted,
            serde_json::json!({
                "admission_id": "research-aaaaaaaaaaaaaaaa-1",
                "program_hash": "a".repeat(64),
                "operation_count": 2,
                "provider": "fixture",
                "input_hash": "b".repeat(64),
                "policy_hash": "c".repeat(64),
                "status": "ADMITTED"
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::ResearchProgramExecuted,
            serde_json::json!({
                "admission_id": "research-aaaaaaaaaaaaaaaa-1",
                "program_hash": "a".repeat(64),
                "operation_count": 2,
                "candidate_count": 1,
                "provider": "fixture",
                "input_hash": "b".repeat(64),
                "result_hash": "d".repeat(64),
                "policy_hash": "c".repeat(64),
                "status": "SUCCESS"
            }),
        );
        let admission_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::SkillAdmissionRecorded,
            serde_json::json!({
                "skill_id": "research.fetch",
                "version": "1.0.0",
                "manifest_hash": "d".repeat(64),
                "mission_id": "mission-1",
                "mission_epoch": 8,
                "granted_capabilities": ["network.read"],
                "precondition_results": [["budget_available", true]],
                "replay_parent_hash": admission_parent,
                "admission_hash": "e".repeat(64),
                "admission_event_hash": ""
            }),
        );
        let execution_parent = digest_hex(previous);
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::SkillExecutionRecorded,
            serde_json::json!({
                "skill_id": "research.fetch",
                "version": "1.0.0",
                "admission_hash": "e".repeat(64),
                "mission_id": "mission-1",
                "replay_parent_hash": execution_parent,
                "input_hash": "f".repeat(64),
                "result_hash": "0".repeat(64),
                "artifact_hash": "1".repeat(64),
                "validator_version": "validator-v1",
                "status": "SUCCESS",
                "execution_hash": "2".repeat(64)
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::BrowserActionAdmitted,
            serde_json::json!({
                "admission_id": "launch-1-admission",
                "action_id": "launch-1",
                "lease_id": 1,
                "actor_role": "actor",
                "action_kind": "launch",
                "input_hash": "3".repeat(64),
                "policy_hash": "4".repeat(64),
                "status": "ADMITTED"
            }),
        );
        admit(
            &mut controller,
            &mut sequence,
            &mut epoch,
            &mut previous,
            LabEventKind::BrowserActionRecorded,
            serde_json::json!({
                "action_id": "launch-1",
                "admission_id": "launch-1-admission",
                "lease_id": 1,
                "actor_role": "actor",
                "action_kind": "launch",
                "input_hash": "3".repeat(64),
                "result_hash": "5".repeat(64),
                "policy_hash": "4".repeat(64),
                "status": "SUCCESS"
            }),
        );
        assert_eq!(controller.runtime().events().len(), 13);
        assert!(controller.runtime().replayable());

        let before_research_duplicate = controller.snapshot_json().unwrap();
        let duplicate_research = serde_json::json!({
            "admission_id": "research-aaaaaaaaaaaaaaaa-1",
            "program_hash": "a".repeat(64),
            "operation_count": 2,
            "candidate_count": 1,
            "provider": "fixture",
            "input_hash": "b".repeat(64),
            "result_hash": "d".repeat(64),
            "policy_hash": "c".repeat(64),
            "status": "SUCCESS"
        });
        let duplicate_research_json = serde_json::to_string(&duplicate_research).unwrap();
        let duplicate_research_event = LabEvent::new(
            sequence,
            epoch,
            LabEventKind::ResearchProgramExecuted,
            projection_payload_hash(&duplicate_research),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(
                duplicate_research_event,
                &duplicate_research_json,
                None
            ),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(
            controller.snapshot_json().unwrap(),
            before_research_duplicate
        );

        let before_rejection = controller.snapshot_json().unwrap();
        let forged = serde_json::json!({
            "skill_id": "forged.skill",
            "version": "1.0.0",
            "admission_hash": "e".repeat(64),
            "mission_id": "mission-1",
            "replay_parent_hash": digest_hex(previous),
            "input_hash": "f".repeat(64),
            "result_hash": "0".repeat(64),
            "artifact_hash": "1".repeat(64),
            "validator_version": "validator-v1",
            "status": "SUCCESS",
            "execution_hash": "3".repeat(64)
        });
        let forged_json = serde_json::to_string(&forged).unwrap();
        let forged_event = LabEvent::new(
            sequence,
            epoch,
            LabEventKind::SkillExecutionRecorded,
            projection_payload_hash(&forged),
            previous,
        );
        assert_eq!(
            controller.admit_projection_record(forged_event, &forged_json, None),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(controller.snapshot_json().unwrap(), before_rejection);
    }

    #[test]
    fn native_projection_record_materializes_typed_runtime_for_valid_digests() {
        let mut controller = LabController::new(mission()).unwrap();
        let state_payload = serde_json::json!({"state": "researching"});
        let state_event = LabEvent::new(
            2,
            1,
            LabEventKind::StateChanged,
            projection_payload_hash(&state_payload),
            controller.runtime().events()[0].event_hash,
        );
        controller
            .admit_projection_record(
                state_event,
                &serde_json::to_string(&state_payload).unwrap(),
                Some(LabRunState::Researching),
            )
            .unwrap();

        let source_payload = serde_json::json!({
            "source_id": "typed-source",
            "uri": "https://example.invalid/source",
            "content_hash": "11".repeat(32),
            "snapshot_hash": "22".repeat(32),
            "retrieved_at_ms": 1,
            "trust_tier": 1,
            "extractor": "fixture"
        });
        let source_event = LabEvent::new(
            3,
            2,
            LabEventKind::SourceCaptured,
            projection_payload_hash(&source_payload),
            controller.runtime().events()[1].event_hash,
        );
        controller
            .admit_projection_record(
                source_event,
                &serde_json::to_string(&source_payload).unwrap(),
                None,
            )
            .unwrap();

        assert_eq!(controller.runtime().sources().len(), 1);
        assert!(controller.runtime().sources().contains_key("typed-source"));
        let restored =
            LabController::from_snapshot_json(&controller.snapshot_json().unwrap()).unwrap();
        assert_eq!(restored.runtime().sources().len(), 1);
        assert!(restored.runtime().sources().contains_key("typed-source"));
        assert!(restored.runtime().replayable());
    }

    #[test]
    fn native_projection_record_materializes_full_typed_chain_for_valid_digests() {
        let mut controller = LabController::new(mission()).unwrap();
        let mut sequence = 2_u64;
        let mut state_epoch = 1_u64;
        let mut previous = controller.runtime().events()[0].event_hash;
        let mut admit = |kind: LabEventKind,
                         payload: serde_json::Value,
                         projection_state: Option<LabRunState>| {
            let event = LabEvent::new(
                sequence,
                state_epoch,
                kind,
                projection_payload_hash(&payload),
                previous,
            );
            previous = event.event_hash;
            sequence += 1;
            state_epoch += 1;
            controller
                .admit_projection_record(
                    event,
                    &serde_json::to_string(&payload).unwrap(),
                    projection_state,
                )
                .unwrap();
        };

        admit(
            LabEventKind::StateChanged,
            serde_json::json!({"state": "researching"}),
            Some(LabRunState::Researching),
        );
        admit(
            LabEventKind::SourceCaptured,
            serde_json::json!({
                "source_id": "source-chain",
                "uri": "https://example.invalid/source-chain",
                "content_hash": "11".repeat(32),
                "snapshot_hash": "22".repeat(32),
                "retrieved_at_ms": 1,
                "trust_tier": 1,
                "extractor": "fixture",
                "provenance_cluster": "chain"
            }),
            None,
        );
        admit(
            LabEventKind::ClaimRecorded,
            serde_json::json!({
                "claim_id": "claim-chain",
                "statement": "typed source supports claim",
                "source_ids": ["source-chain"],
                "confidence_bps": 7500,
                "status": "supported"
            }),
            None,
        );
        admit(
            LabEventKind::HypothesisRecorded,
            serde_json::json!({
                "hypothesis_id": "hypothesis-chain",
                "statement": "typed claim predicts result",
                "prior_bps": 5000,
                "falsifiers": ["negative result"],
                "supporting_claim_ids": ["claim-chain"],
                "contradicting_claim_ids": []
            }),
            None,
        );
        admit(
            LabEventKind::ExperimentScheduled,
            serde_json::json!({
                "experiment_id": "experiment-chain",
                "hypothesis_id": "hypothesis-chain",
                "design": "paired",
                "variables": ["x"],
                "controls": ["baseline"],
                "preregistered_seeds": [1, 2, 3, 4, 5],
                "expected_observations": 1,
                "measurement_unit": "score",
                "uncertainty_required": false,
                "min_clean_replicates": 0
            }),
            None,
        );
        admit(
            LabEventKind::StateChanged,
            serde_json::json!({"state": "experimenting"}),
            Some(LabRunState::Experimenting),
        );
        admit(
            LabEventKind::ObservationRecorded,
            serde_json::json!({
                "observation_id": "observation-chain",
                "experiment_id": "experiment-chain",
                "seed": 1,
                "measurement": 1.25,
                "unit": "score",
                "raw_artifact_hash": "33".repeat(32),
                "environment_hash": "44".repeat(32),
                "valid": true,
                "uncertainty": null,
                "replication_of": null,
                "operator_id": "operator-chain",
                "clean": true,
                "epistemic_status": "OBSERVED",
                "reported_unit": "score"
            }),
            None,
        );

        let runtime = controller.runtime();
        assert_eq!(runtime.sources().len(), 1);
        assert_eq!(runtime.claims().len(), 1);
        assert_eq!(runtime.hypotheses().len(), 1);
        assert_eq!(runtime.experiments().len(), 1);
        assert_eq!(runtime.observations().len(), 1);
        let snapshot = controller.snapshot_json().unwrap();
        let restored = LabController::from_snapshot_json(&snapshot).unwrap();
        assert_eq!(restored.runtime().sources().len(), 1);
        assert_eq!(restored.runtime().claims().len(), 1);
        assert_eq!(restored.runtime().hypotheses().len(), 1);
        assert_eq!(restored.runtime().experiments().len(), 1);
        assert_eq!(restored.runtime().observations().len(), 1);
        assert!(restored.runtime().replayable());
    }

    #[test]
    fn native_lab_controller_consumes_budget_for_projection_admission() {
        let mut controller = LabController::new(mission()).unwrap();
        let parent = controller.runtime().events()[0].event_hash;
        let spend = BudgetVector {
            tokens: 10,
            ..BudgetVector::zero()
        };
        let event = LabEvent::new(
            2,
            1,
            LabEventKind::BudgetAdmitted,
            canonical_hash(&spend),
            parent,
        );
        controller
            .admit_projection_exploration(0, spend, event)
            .unwrap();
        assert_eq!(controller.steps(), 1);
        assert_eq!(controller.budget_remaining().tokens, 90);
        let before = controller.budget_remaining();
        let duplicate = LabEvent::new(
            3,
            2,
            LabEventKind::BudgetAdmitted,
            canonical_hash(&spend),
            controller.runtime().events()[1].event_hash,
        );
        assert_eq!(
            controller.admit_projection_exploration(0, spend, duplicate),
            Err(LabError::InvalidTransition)
        );
        assert_eq!(controller.budget_remaining(), before);
    }

    #[test]
    fn lab_requires_provenance_before_claims_and_experiments() {
        let mut runtime = LabRuntime::new(mission()).unwrap();
        assert_eq!(runtime.state(), LabRunState::Planned);
        assert!(
            runtime
                .record_claim(ClaimRecord {
                    claim_id: "claim".to_string(),
                    statement: "unsupported".to_string(),
                    source_ids: vec!["missing".to_string()],
                    confidence_bps: 5_000,
                    status: ClaimStatus::Unresolved,
                })
                .is_err()
        );
        runtime.record_source(source()).unwrap();
        runtime
            .record_claim(ClaimRecord {
                claim_id: "claim".to_string(),
                statement: "supported".to_string(),
                source_ids: vec!["source-1".to_string()],
                confidence_bps: 5_000,
                status: ClaimStatus::Supported,
            })
            .unwrap();
        runtime
            .record_hypothesis(HypothesisRecord {
                hypothesis_id: "h-1".to_string(),
                statement: "it works".to_string(),
                prior_bps: 5_000,
                falsifiers: vec!["negative result".to_string()],
                supporting_claim_ids: vec!["claim".to_string()],
                contradicting_claim_ids: vec![],
            })
            .unwrap();
        assert_eq!(runtime.events().first().unwrap().sequence, 1);
        runtime
            .record_research_program(ResearchProgramReceipt {
                program_hash: [9; 32],
                operation_count: 2,
                candidate_count: 1,
                provider: "fixture".to_string(),
            })
            .unwrap();
        runtime
            .record_security_event(SecurityEventRecord {
                reason: "prompt_injection_marker".to_string(),
                artifact_hash: [10; 32],
            })
            .unwrap();
        assert!(runtime.events().iter().all(LabEvent::is_valid));
        assert!(verify_event_chain(runtime.events()));
    }

    #[test]
    fn lab_completion_requires_valid_observations_and_manifest_is_hashed() {
        let mut runtime = LabRuntime::new(mission()).unwrap();
        runtime.record_source(source()).unwrap();
        runtime
            .record_claim(ClaimRecord {
                claim_id: "claim".to_string(),
                statement: "supported".to_string(),
                source_ids: vec!["source-1".to_string()],
                confidence_bps: 5_000,
                status: ClaimStatus::Supported,
            })
            .unwrap();
        runtime
            .record_hypothesis(HypothesisRecord {
                hypothesis_id: "h-1".to_string(),
                statement: "it works".to_string(),
                prior_bps: 5_000,
                falsifiers: vec!["negative result".to_string()],
                supporting_claim_ids: vec!["claim".to_string()],
                contradicting_claim_ids: vec![],
            })
            .unwrap();
        runtime
            .schedule_experiment(ExperimentSpec {
                experiment_id: "e-1".to_string(),
                hypothesis_id: "h-1".to_string(),
                design: "paired".to_string(),
                variables: vec!["x".to_string()],
                controls: vec!["baseline".to_string()],
                preregistered_seeds: vec![1, 2, 3, 4, 5],
                expected_observations: 1,
                measurement_unit: "score".to_string(),
                uncertainty_required: false,
                min_clean_replicates: 0,
            })
            .unwrap();
        assert_eq!(
            runtime.review_and_complete(),
            Err(LabError::IncompleteMission)
        );
        runtime
            .record_observation(ObservationRecord {
                observation_id: "o-1".to_string(),
                experiment_id: "e-1".to_string(),
                seed: 1,
                measurement: 1.0,
                unit: "score".to_string(),
                raw_artifact_hash: [3; 32],
                environment_hash: [4; 32],
                valid: true,
                uncertainty: None,
                replication_of: None,
                operator_id: String::new(),
                clean: true,
                epistemic_status: "OBSERVED".to_string(),
            })
            .unwrap();
        runtime.review_and_complete().unwrap();
        let manifest = runtime.manifest();
        assert_eq!(manifest.state, LabRunState::Completed);
        assert_eq!(
            manifest.manifest_hash,
            canonical_hash(&manifest_without_hash(&manifest))
        );
    }

    #[test]
    fn strict_benchmark_rejects_contamination_and_requires_ci_clearance() {
        let protocol = BenchmarkProtocolV2 {
            name: "unit".to_string(),
            metric: "accuracy".to_string(),
            direction: BenchmarkDirection::HigherIsBetter,
            alpha_bps: 500,
            min_trials: 30,
            warmups: 10,
            paired_blocks: 30,
            preregistered_seeds: vec![1, 2, 3, 4, 5],
            frozen_split_hash: [8; 32],
            contamination_checks: vec!["dedupe".to_string()],
            percentile: None,
        };
        let trials = vec![0.9; 30];
        let rejected = evaluate_benchmark(&protocol, &trials, Some(0.5), &["leak".to_string()]);
        assert!(!rejected.passed);
        assert!(
            rejected
                .failure_reasons
                .iter()
                .any(|reason| reason == "contamination_detected")
        );
        let passed = evaluate_benchmark(&protocol, &trials, Some(0.5), &[]);
        assert!(passed.passed);
        assert!(passed.ci_low.unwrap() > 0.5);
    }

    #[test]
    fn lab_measurements_require_declared_units_uncertainty_and_clean_replication() {
        assert_eq!(unit_dimension("V*A"), unit_dimension("W"));
        assert_eq!(unit_dimension("W*s"), unit_dimension("J"));
        assert_eq!(unit_dimension("V/A"), unit_dimension("Ohm"));
        let mut runtime = LabRuntime::new(mission()).unwrap();
        runtime.record_source(source()).unwrap();
        runtime
            .record_claim(ClaimRecord {
                claim_id: "claim".to_string(),
                statement: "supported".to_string(),
                source_ids: vec!["source-1".to_string()],
                confidence_bps: 5_000,
                status: ClaimStatus::Supported,
            })
            .unwrap();
        runtime
            .record_hypothesis(HypothesisRecord {
                hypothesis_id: "h-1".to_string(),
                statement: "it works".to_string(),
                prior_bps: 5_000,
                falsifiers: vec!["negative result".to_string()],
                supporting_claim_ids: vec!["claim".to_string()],
                contradicting_claim_ids: vec![],
            })
            .unwrap();
        runtime
            .schedule_experiment(ExperimentSpec {
                experiment_id: "e-1".to_string(),
                hypothesis_id: "h-1".to_string(),
                design: "paired".to_string(),
                variables: vec!["length".to_string()],
                controls: vec!["baseline".to_string()],
                preregistered_seeds: vec![1, 2, 3, 4, 5],
                expected_observations: 2,
                measurement_unit: "m".to_string(),
                uncertainty_required: true,
                min_clean_replicates: 1,
            })
            .unwrap();
        assert!(
            runtime
                .record_observation(ObservationRecord {
                    observation_id: "bad-seed".to_string(),
                    experiment_id: "e-1".to_string(),
                    seed: 99,
                    measurement: 1.0,
                    unit: "m".to_string(),
                    raw_artifact_hash: [3; 32],
                    environment_hash: [4; 32],
                    valid: true,
                    uncertainty: Some(0.1),
                    replication_of: None,
                    operator_id: String::new(),
                    clean: true,
                    epistemic_status: "OBSERVED".to_string(),
                })
                .is_err()
        );
        runtime
            .record_observation(ObservationRecord {
                observation_id: "o-1".to_string(),
                experiment_id: "e-1".to_string(),
                seed: 1,
                measurement: 1.0,
                unit: "m".to_string(),
                raw_artifact_hash: [3; 32],
                environment_hash: [4; 32],
                valid: true,
                uncertainty: Some(0.1),
                replication_of: None,
                operator_id: "primary".to_string(),
                clean: true,
                epistemic_status: "OBSERVED".to_string(),
            })
            .unwrap();
        runtime
            .record_observation(ObservationRecord {
                observation_id: "o-2".to_string(),
                experiment_id: "e-1".to_string(),
                seed: 2,
                measurement: 1.1,
                unit: "m".to_string(),
                raw_artifact_hash: [5; 32],
                environment_hash: [6; 32],
                valid: true,
                uncertainty: Some(0.1),
                replication_of: Some("o-1".to_string()),
                operator_id: "independent".to_string(),
                clean: true,
                epistemic_status: "OBSERVED".to_string(),
            })
            .unwrap();
        runtime.review_and_complete().unwrap();
        assert_eq!(runtime.state(), LabRunState::Completed);
    }

    #[test]
    fn lab_events_round_trip_through_authoritative_segment_archive() {
        let mut runtime = LabRuntime::new(mission()).unwrap();
        runtime.record_source(source()).unwrap();
        runtime
            .record_claim(ClaimRecord {
                claim_id: "claim".to_string(),
                statement: "supported".to_string(),
                source_ids: vec!["source-1".to_string()],
                confidence_bps: 5_000,
                status: ClaimStatus::Supported,
            })
            .unwrap();
        runtime
            .record_hypothesis(HypothesisRecord {
                hypothesis_id: "h-1".to_string(),
                statement: "it works".to_string(),
                prior_bps: 5_000,
                falsifiers: vec!["negative result".to_string()],
                supporting_claim_ids: vec!["claim".to_string()],
                contradicting_claim_ids: vec![],
            })
            .unwrap();
        runtime
            .schedule_experiment(ExperimentSpec {
                experiment_id: "e-1".to_string(),
                hypothesis_id: "h-1".to_string(),
                design: "paired".to_string(),
                variables: vec!["x".to_string()],
                controls: vec!["baseline".to_string()],
                preregistered_seeds: vec![1, 2, 3, 4, 5],
                expected_observations: 1,
                measurement_unit: "score".to_string(),
                uncertainty_required: false,
                min_clean_replicates: 0,
            })
            .unwrap();
        runtime
            .record_observation(ObservationRecord {
                observation_id: "o-1".to_string(),
                experiment_id: "e-1".to_string(),
                seed: 1,
                measurement: 1.0,
                unit: "score".to_string(),
                raw_artifact_hash: [3; 32],
                environment_hash: [4; 32],
                valid: true,
                uncertainty: None,
                replication_of: None,
                operator_id: String::new(),
                clean: true,
                epistemic_status: "OBSERVED".to_string(),
            })
            .unwrap();
        assert!(runtime.replayable());

        let directory = tempfile::tempdir().unwrap();
        let manifest = crate::replay::RunEventSegmentArchive::write_lab_events(
            directory.path(),
            2,
            55,
            runtime.events(),
        )
        .unwrap();
        assert!(manifest.is_valid());
        let manifest_json = serde_json::to_value(&manifest).unwrap();
        assert_eq!(
            manifest_json["schema"],
            crate::replay::RUN_EVENT_SEGMENT_MANIFEST_SCHEMA
        );
        assert_eq!(
            manifest_json["version"],
            crate::replay::RUN_EVENT_SEGMENT_MANIFEST_VERSION
        );
        let archived =
            crate::replay::RunEventSegmentArchive::read_ledger_mmap(directory.path(), &manifest)
                .unwrap();
        assert_eq!(archived.len(), runtime.events().len());
        assert!(
            archived
                .events()
                .iter()
                .all(|event| event.kind == crate::replay::RunEventKind::LabEventRecorded)
        );
        assert!(archived.verify_hash_chain());
    }

    #[test]
    fn sealed_manifest_rejects_missing_tail_as_complete_archive() {
        let mut runtime = LabRuntime::new(mission()).unwrap();
        runtime.record_source(source()).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let manifest = crate::replay::RunEventSegmentArchive::write_lab_events(
            directory.path(),
            1,
            56,
            runtime.events(),
        )
        .unwrap();
        assert!(manifest.entries.len() > 1);

        let last_entry = manifest.entries.last().unwrap();
        let last_segment = crate::replay::RunEventSegmentArchive::segment_path(
            directory.path(),
            manifest.run_id,
            last_entry.segment_id,
        );
        std::fs::remove_file(last_segment).unwrap();

        let recovered = crate::replay::RunEventSegmentArchive::recover_manifest_from_segments(
            directory.path(),
            manifest.run_id,
        )
        .unwrap();
        assert_eq!(recovered.entries.len(), manifest.entries.len() - 1);
        assert_ne!(recovered.manifest_hash, manifest.manifest_hash);
        assert!(recovered.is_valid());
    }

    #[test]
    fn lab_snapshot_round_trip_rejects_tampered_state_or_chain() {
        let mut runtime = LabRuntime::new(mission()).unwrap();
        runtime.record_source(source()).unwrap();
        let snapshot = runtime.snapshot_json().unwrap();
        let restored = LabRuntime::from_snapshot_json(&snapshot).unwrap();
        assert_eq!(restored, runtime);

        let mut tampered: serde_json::Value = serde_json::from_str(&snapshot).unwrap();
        tampered["state_epoch"] = serde_json::json!(999);
        let tampered_json = serde_json::to_string(&tampered).unwrap();
        assert_eq!(
            LabRuntime::from_snapshot_json(&tampered_json),
            Err(LabError::InvalidEvent)
        );
    }

    #[test]
    fn terminal_lab_state_rejects_new_records() {
        assert!(LabRunState::Completed.can_transition_to(LabRunState::Blocked));
        assert!(LabRunState::Completed.can_transition_to(LabRunState::Aborted));
        let mut runtime = LabRuntime::new(mission()).unwrap();
        runtime.transition(LabRunState::Aborted).unwrap();
        assert_eq!(
            runtime.record_source(source()),
            Err(LabError::InvalidTransition)
        );
        assert_eq!(
            runtime.record_research_program(ResearchProgramReceipt {
                program_hash: [9; 32],
                operation_count: 1,
                candidate_count: 0,
                provider: "fixture".to_string(),
            }),
            Err(LabError::InvalidTransition)
        );
    }
}
