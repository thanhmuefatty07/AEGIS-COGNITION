//! A deterministic laboratory runtime for research-oriented agent missions.
//!
//! The lab is intentionally a small kernel, not another model orchestrator.
//! It owns mission identity, evidence-bearing records, event ordering and
//! benchmark admission. Provider/browser/experiment implementations remain
//! adapters at the edge and must commit through these records.

use blake3::Hasher;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

use crate::gt96::{BudgetPolicy, ContractError, target_scope_is_ambiguous_uri};

const LAB_SCHEMA: &str = "aegis-lab-runtime-v1";
const MAX_GOAL_CONTRACT_WIRE_BYTES: usize = 1024 * 1024;
const MAX_GOAL_CONTRACT_TEXT_BYTES: usize = 64 * 1024;
const MAX_GOAL_CONTRACT_IDENTIFIER_BYTES: usize = 128;
const MAX_GOAL_CONTRACT_SCOPE_BYTES: usize = 4096;
const MAX_GOAL_CONTRACT_SEQUENCE_ITEMS: usize = 256;
const MAX_GOAL_CONTRACT_TOTAL_SEQUENCE_ITEMS: usize = 2048;
const MAX_GOAL_CONTRACT_DEPTH: usize = 8;
const MAX_GOAL_CONTRACT_NODES: usize = 4096;

fn default_external_attempt_budget(max_steps: u32) -> u64 {
    let n = u64::from(max_steps).saturating_add(1);
    n.saturating_mul(n).saturating_mul(n).saturating_mul(8)
}

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
    ExecutionCellManifestRecorded,
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

fn is_external_admission_kind(kind: LabEventKind) -> bool {
    matches!(
        kind,
        LabEventKind::ExperimentExecutionAdmitted
            | LabEventKind::ToolExecutionAdmitted
            | LabEventKind::ResearchProgramAdmitted
            | LabEventKind::BrowserActionAdmitted
            | LabEventKind::BrowserObservationAdmitted
            | LabEventKind::SkillAdmissionRecorded
    )
}

fn is_goal_bound_event_kind(kind: LabEventKind) -> bool {
    matches!(
        kind,
        LabEventKind::ExperimentExecutionAdmitted
            | LabEventKind::ExperimentExecutionRecorded
            | LabEventKind::ToolExecutionAdmitted
            | LabEventKind::ToolExecutionRecorded
            | LabEventKind::ResearchProgramAdmitted
            | LabEventKind::ResearchProgramExecuted
            | LabEventKind::BrowserActionAdmitted
            | LabEventKind::BrowserActionRecorded
            | LabEventKind::BrowserObservationAdmitted
            | LabEventKind::BrowserObservationRecorded
            | LabEventKind::SkillAdmissionRecorded
            | LabEventKind::SkillExecutionRecorded
            | LabEventKind::CancellationAdmitted
            | LabEventKind::CancellationRecorded
            | LabEventKind::ReviewRecorded
    )
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
    /// Optional bound for Lab-owned execution admissions.  `None` is kept
    /// only for legacy snapshots created before this contract existed.
    #[serde(default)]
    pub max_external_attempts: Option<u64>,
    pub schema: String,
    /// Canonical JSON for an explicit Goal/Target contract.  The field is
    /// optional for legacy missions; when present, the native boundary
    /// validates its identity and digest before admitting the run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub goal_contract_json: Option<String>,
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
            max_external_attempts: Some(default_external_attempt_budget(max_steps)),
            schema: LAB_SCHEMA.to_string(),
            goal_contract_json: None,
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
            || self.max_external_attempts == Some(0)
        {
            return Err(LabError::InvalidMission);
        }
        self.budget_policy.validate().map_err(LabError::Contract)?;
        if let Some(contract_json) = &self.goal_contract_json {
            validate_goal_contract_json(contract_json, self)?;
        }
        Ok(())
    }

    pub fn mission_hash(&self) -> [u8; 32] {
        canonical_hash(self)
    }
}

fn has_exact_json_keys(
    object: &serde_json::Map<String, serde_json::Value>,
    expected: &[&str],
) -> bool {
    object.len() == expected.len()
        && expected.iter().all(|field| object.contains_key(*field))
        && object.keys().all(|key| expected.contains(&key.as_str()))
}

fn canonical_json_value(value: &serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Object(object) => {
            let sorted: BTreeMap<String, serde_json::Value> = object
                .iter()
                .map(|(key, value)| (key.clone(), canonical_json_value(value)))
                .collect();
            serde_json::Value::Object(sorted.into_iter().collect())
        }
        serde_json::Value::Array(values) => {
            serde_json::Value::Array(values.iter().map(canonical_json_value).collect())
        }
        _ => value.clone(),
    }
}

fn goal_contract_string_limit(parent_key: Option<&str>) -> usize {
    match parent_key {
        Some("read_roots" | "write_roots" | "network_allowlist" | "scope" | "non_goals") => {
            MAX_GOAL_CONTRACT_SCOPE_BYTES
        }
        Some(
            "schema"
            | "goal_id"
            | "kind"
            | "stable_id"
            | "revision_or_digest"
            | "owner"
            | "policy_digest"
            | "predicate_id"
            | "evaluator"
            | "severity"
            | "inputs"
            | "evidence_refs"
            | "reproducibility_requirements"
            | "required_record_types"
            | "trusted_verifier_ids"
            | "parent_goal_id"
            | "parent_contract_hash"
            | "author_id"
            | "external_side_effects"
            | "mission_id"
            | "owner_id"
            | "principal_id"
            | "execution_cell_id"
            | "execution_action_kind"
            | "backend_kind",
        ) => MAX_GOAL_CONTRACT_IDENTIFIER_BYTES,
        _ => MAX_GOAL_CONTRACT_TEXT_BYTES,
    }
}

fn validate_goal_contract_limits(value: &serde_json::Value) -> Result<(), LabError> {
    let mut stack: Vec<(&serde_json::Value, usize, Option<&str>)> = vec![(value, 0, None)];
    let mut total_sequence_items = 0_usize;
    let mut total_nodes = 0_usize;
    while let Some((current, depth, parent_key)) = stack.pop() {
        total_nodes = total_nodes.saturating_add(1);
        if total_nodes > MAX_GOAL_CONTRACT_NODES || depth > MAX_GOAL_CONTRACT_DEPTH {
            return Err(LabError::InvalidMission);
        }
        match current {
            serde_json::Value::Object(object) => {
                for (key, child) in object {
                    if key.len() > MAX_GOAL_CONTRACT_IDENTIFIER_BYTES {
                        return Err(LabError::InvalidMission);
                    }
                    stack.push((child, depth.saturating_add(1), Some(key.as_str())));
                }
            }
            serde_json::Value::Array(values) => {
                if values.len() > MAX_GOAL_CONTRACT_SEQUENCE_ITEMS {
                    return Err(LabError::InvalidMission);
                }
                total_sequence_items = total_sequence_items.saturating_add(values.len());
                if total_sequence_items > MAX_GOAL_CONTRACT_TOTAL_SEQUENCE_ITEMS {
                    return Err(LabError::InvalidMission);
                }
                for child in values {
                    stack.push((child, depth.saturating_add(1), parent_key));
                }
            }
            serde_json::Value::String(text) => {
                if text.len() > goal_contract_string_limit(parent_key) {
                    return Err(LabError::InvalidMission);
                }
            }
            serde_json::Value::Null | serde_json::Value::Bool(_) | serde_json::Value::Number(_) => {
            }
        }
    }
    Ok(())
}

fn validate_goal_contract_json(
    contract_json: &str,
    mission: &LabMissionSpec,
) -> Result<(), LabError> {
    if contract_json.len() > MAX_GOAL_CONTRACT_WIRE_BYTES {
        return Err(LabError::InvalidMission);
    }
    let value: serde_json::Value =
        serde_json::from_str(contract_json).map_err(|_| LabError::InvalidMission)?;
    validate_goal_contract_limits(&value)?;
    let Some(object) = value.as_object() else {
        return Err(LabError::InvalidMission);
    };
    if !has_exact_json_keys(
        object,
        &[
            "schema",
            "goal_id",
            "generation",
            "objective",
            "acceptance",
            "target",
            "scope",
            "non_goals",
            "policy_digest",
            "budget",
            "evidence_policy",
            "parent_goal_id",
            "parent_contract_hash",
            "author_id",
            "effective_epoch",
            "evolution_reason",
            "created_at_ms",
            "contract_hash",
        ],
    ) {
        return Err(LabError::InvalidMission);
    }
    validate_goal_contract_shape(object, mission)?;
    if object.get("schema").and_then(serde_json::Value::as_str) != Some("aegis-goal-contract-v1")
        || object.get("objective").and_then(serde_json::Value::as_str)
            != Some(mission.objective.as_str())
        || object
            .get("goal_id")
            .and_then(serde_json::Value::as_str)
            .is_none()
        || object
            .get("policy_digest")
            .and_then(serde_json::Value::as_str)
            .is_none()
        || object
            .get("policy_digest")
            .and_then(serde_json::Value::as_str)
            .is_none_or(|value| value == "unbound" || !is_hex_digest(value))
    {
        return Err(LabError::InvalidMission);
    }
    let Some(target) = object.get("target").and_then(serde_json::Value::as_object) else {
        return Err(LabError::InvalidMission);
    };
    if !has_exact_json_keys(
        target,
        &[
            "kind",
            "stable_id",
            "revision_or_digest",
            "read_roots",
            "write_roots",
            "network_allowlist",
            "external_side_effects",
            "owner",
        ],
    ) {
        return Err(LabError::InvalidMission);
    }
    if target
        .get("stable_id")
        .and_then(serde_json::Value::as_str)
        .is_none_or(|value| value.trim().is_empty() || value == "unbound")
        || target
            .get("revision_or_digest")
            .and_then(serde_json::Value::as_str)
            .is_none_or(|value| value.trim().is_empty() || value == "unbound")
    {
        return Err(LabError::InvalidMission);
    }
    let Some(acceptance) = object
        .get("acceptance")
        .and_then(serde_json::Value::as_array)
    else {
        return Err(LabError::InvalidMission);
    };
    let evidence_policy = object
        .get("evidence_policy")
        .and_then(serde_json::Value::as_object)
        .ok_or(LabError::InvalidMission)?;
    if !has_exact_json_keys(
        evidence_policy,
        &[
            "required_record_types",
            "minimum_sources",
            "require_independent_verifier",
            "trusted_verifier_ids",
        ],
    ) {
        return Err(LabError::InvalidMission);
    }
    let require_independent_verifier = evidence_policy
        .get("require_independent_verifier")
        .and_then(serde_json::Value::as_bool)
        .ok_or(LabError::InvalidMission)?;
    let trusted_verifier_ids = evidence_policy
        .get("trusted_verifier_ids")
        .and_then(serde_json::Value::as_array)
        .ok_or(LabError::InvalidMission)?;
    if require_independent_verifier
        && (trusted_verifier_ids.is_empty()
            || trusted_verifier_ids
                .iter()
                .any(|value| value.as_str().is_none_or(|item| item.trim().is_empty())))
    {
        return Err(LabError::InvalidMission);
    }
    if evidence_policy
        .get("minimum_sources")
        .and_then(serde_json::Value::as_u64)
        .is_none()
        || evidence_policy
            .get("required_record_types")
            .and_then(serde_json::Value::as_array)
            .is_none()
    {
        return Err(LabError::InvalidMission);
    }
    let mut predicate_ids = BTreeSet::new();
    let mut required_predicates = 0_u64;
    for criterion in acceptance {
        let Some(criterion) = criterion.as_object() else {
            return Err(LabError::InvalidMission);
        };
        if !has_exact_json_keys(
            criterion,
            &[
                "predicate_id",
                "description",
                "evaluator",
                "inputs",
                "expected_result",
                "severity",
                "evidence_refs",
                "reproducibility_requirements",
            ],
        ) {
            return Err(LabError::InvalidMission);
        }
        let predicate_id = criterion
            .get("predicate_id")
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or(LabError::InvalidMission)?;
        if !predicate_ids.insert(predicate_id.to_string())
            || criterion
                .get("description")
                .and_then(serde_json::Value::as_str)
                .is_none_or(|value| value.trim().is_empty())
            || criterion
                .get("evaluator")
                .and_then(serde_json::Value::as_str)
                .is_none_or(|value| value.trim().is_empty())
        {
            return Err(LabError::InvalidMission);
        }
        let severity = criterion
            .get("severity")
            .and_then(serde_json::Value::as_str)
            .ok_or(LabError::InvalidMission)?;
        let evidence_refs = criterion
            .get("evidence_refs")
            .and_then(serde_json::Value::as_array)
            .ok_or(LabError::InvalidMission)?;
        if evidence_refs
            .iter()
            .any(|value| value.as_str().is_none_or(|item| item.trim().is_empty()))
        {
            return Err(LabError::InvalidMission);
        }
        match severity {
            "required" => {
                required_predicates = required_predicates.saturating_add(1);
                if require_independent_verifier && evidence_refs.is_empty() {
                    return Err(LabError::InvalidMission);
                }
            }
            "advisory" => {}
            _ => return Err(LabError::InvalidMission),
        }
    }
    if acceptance.is_empty() || required_predicates == 0 {
        return Err(LabError::InvalidMission);
    }
    let budget = object
        .get("budget")
        .and_then(serde_json::Value::as_object)
        .ok_or(LabError::InvalidMission)?;
    if !has_exact_json_keys(
        budget,
        &[
            "token_limit",
            "attempt_limit",
            "wall_time_limit_ms",
            "cost_limit_minor_units",
            "cpu_time_limit_ms",
            "reserved_tokens",
        ],
    ) {
        return Err(LabError::InvalidMission);
    }
    let token_limit = budget
        .get("token_limit")
        .and_then(serde_json::Value::as_u64)
        .ok_or(LabError::InvalidMission)?;
    let attempt_limit = budget
        .get("attempt_limit")
        .and_then(serde_json::Value::as_u64)
        .ok_or(LabError::InvalidMission)?;
    let reserved_tokens = budget
        .get("reserved_tokens")
        .and_then(serde_json::Value::as_u64)
        .ok_or(LabError::InvalidMission)?;
    let expected_reserved_tokens = mission
        .budget_policy
        .finalization_reserve
        .tokens
        .saturating_add(mission.budget_policy.recovery_reserve.tokens);
    if token_limit != mission.budget_policy.total.tokens
        || reserved_tokens != expected_reserved_tokens
        || mission
            .max_external_attempts
            .is_some_and(|expected| attempt_limit != expected)
        || reserved_tokens >= token_limit
    {
        return Err(LabError::InvalidMission);
    }
    let generation = object
        .get("generation")
        .and_then(serde_json::Value::as_u64)
        .ok_or(LabError::InvalidMission)?;
    let _effective_epoch = object
        .get("effective_epoch")
        .and_then(serde_json::Value::as_u64)
        .ok_or(LabError::InvalidMission)?;
    let parent_goal_id = object
        .get("parent_goal_id")
        .ok_or(LabError::InvalidMission)?;
    let parent_contract_hash = object
        .get("parent_contract_hash")
        .ok_or(LabError::InvalidMission)?;
    let evolution_reason = object
        .get("evolution_reason")
        .ok_or(LabError::InvalidMission)?;
    for value in [parent_goal_id, parent_contract_hash, evolution_reason] {
        if !value.is_null()
            && value
                .as_str()
                .is_none_or(|item| item.trim().is_empty() || item != item.trim())
        {
            return Err(LabError::InvalidMission);
        }
    }
    let has_parent =
        !parent_goal_id.is_null() || !parent_contract_hash.is_null() || !evolution_reason.is_null();
    let parent_is_complete = parent_goal_id
        .as_str()
        .is_some_and(|value| !value.trim().is_empty())
        && parent_contract_hash.as_str().is_some_and(is_hex_digest)
        && evolution_reason
            .as_str()
            .is_some_and(|value| !value.trim().is_empty());
    if generation == 0
        || object
            .get("author_id")
            .and_then(serde_json::Value::as_str)
            .is_none_or(|value| value.trim().is_empty())
        || (has_parent && !parent_is_complete)
        || (generation > 1 && !has_parent)
    {
        return Err(LabError::InvalidMission);
    }
    let Some(contract_hash) = object
        .get("contract_hash")
        .and_then(serde_json::Value::as_str)
    else {
        return Err(LabError::InvalidMission);
    };
    let Some(contract_hash_bytes) = decode_hex_digest(contract_hash) else {
        return Err(LabError::InvalidMission);
    };
    if contract_hash_bytes != mission.contract_hash {
        return Err(LabError::InvalidMission);
    }
    let mut definition = object.clone();
    definition.remove("contract_hash");
    let encoded = serde_json::to_vec(&canonical_json_value(&serde_json::Value::Object(
        definition,
    )))
    .map_err(|_| LabError::InvalidMission)?;
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-contract-canonical-v1\0");
    hasher.update(&encoded);
    if hasher.finalize().as_bytes() != &mission.contract_hash {
        return Err(LabError::InvalidMission);
    }
    Ok(())
}

fn goal_required_string<'a>(
    object: &'a serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<&'a str, LabError> {
    let value = object
        .get(key)
        .and_then(serde_json::Value::as_str)
        .ok_or(LabError::InvalidMission)?;
    if value.trim().is_empty() || value != value.trim() {
        return Err(LabError::InvalidMission);
    }
    Ok(value)
}

fn goal_string_array(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<Vec<String>, LabError> {
    let values = object
        .get(key)
        .and_then(serde_json::Value::as_array)
        .ok_or(LabError::InvalidMission)?;
    let mut result = Vec::with_capacity(values.len());
    for value in values {
        let item = value.as_str().ok_or(LabError::InvalidMission)?;
        if item.trim().is_empty() || item != item.trim() {
            return Err(LabError::InvalidMission);
        }
        result.push(item.to_string());
    }
    if result.windows(2).any(|window| window[0] >= window[1]) {
        return Err(LabError::InvalidMission);
    }
    Ok(result)
}

fn is_canonical_external_side_effect_key(value: &str) -> bool {
    let Some((tool_name, effect_class)) = value.split_once("::") else {
        return false;
    };
    !tool_name.is_empty()
        && !effect_class.is_empty()
        && !effect_class.contains("::")
        && tool_name == tool_name.trim()
        && effect_class == effect_class.trim()
}

fn is_valid_target_network_entry(value: &str) -> bool {
    if value.is_empty()
        || value.trim() != value
        || value
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
        || value.starts_with("//")
    {
        return false;
    }

    let lower = value.to_ascii_lowercase();
    let authority = if lower.starts_with("https://") {
        &value[8..]
    } else if lower.starts_with("http://") {
        &value[7..]
    } else if value.contains("://") {
        return false;
    } else {
        value
    };
    let boundary = authority.find(['/', '?', '#']).unwrap_or(authority.len());
    let host = &authority[..boundary];
    let suffix = &authority[boundary..];
    if host.is_empty()
        || host.contains([':', '@', '[', ']', '*'])
        || !suffix.is_empty() && suffix != "/"
    {
        return false;
    }
    true
}

fn goal_optional_nonnegative_u64(
    object: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> Result<(), LabError> {
    match object.get(key) {
        Some(serde_json::Value::Null) => Ok(()),
        Some(value) if value.as_u64().is_some() => Ok(()),
        _ => Err(LabError::InvalidMission),
    }
}

fn goal_exact_keys(
    object: &serde_json::Map<String, serde_json::Value>,
    allowed: &[&str],
) -> Result<(), LabError> {
    if object
        .keys()
        .any(|key| !allowed.iter().any(|allowed_key| *allowed_key == key))
        || allowed.iter().any(|key| !object.contains_key(*key))
    {
        return Err(LabError::InvalidMission);
    }
    Ok(())
}

fn validate_goal_contract_shape(
    object: &serde_json::Map<String, serde_json::Value>,
    mission: &LabMissionSpec,
) -> Result<(), LabError> {
    goal_exact_keys(
        object,
        &[
            "schema",
            "goal_id",
            "generation",
            "objective",
            "acceptance",
            "target",
            "scope",
            "non_goals",
            "policy_digest",
            "budget",
            "evidence_policy",
            "parent_goal_id",
            "parent_contract_hash",
            "author_id",
            "effective_epoch",
            "evolution_reason",
            "created_at_ms",
            "contract_hash",
        ],
    )?;
    if object.get("schema").and_then(serde_json::Value::as_str) != Some("aegis-goal-contract-v1")
        || object.get("objective").and_then(serde_json::Value::as_str)
            != Some(mission.objective.as_str())
    {
        return Err(LabError::InvalidMission);
    }
    let goal_id = goal_required_string(object, "goal_id")?;
    let policy_digest = goal_required_string(object, "policy_digest")?;
    if policy_digest == "unbound" {
        return Err(LabError::InvalidMission);
    }
    let _ = goal_required_string(object, "objective")?;
    let _ = goal_required_string(object, "author_id")?;
    if object
        .get("generation")
        .and_then(serde_json::Value::as_u64)
        .is_none_or(|generation| generation == 0)
        || object
            .get("effective_epoch")
            .and_then(serde_json::Value::as_u64)
            .is_none()
        || object
            .get("created_at_ms")
            .and_then(serde_json::Value::as_u64)
            .is_none()
    {
        return Err(LabError::InvalidMission);
    }

    let scope = goal_string_array(object, "scope")?;
    let non_goals = goal_string_array(object, "non_goals")?;
    let _ = (scope, non_goals, goal_id);

    let target = object
        .get("target")
        .and_then(serde_json::Value::as_object)
        .ok_or(LabError::InvalidMission)?;
    goal_exact_keys(
        target,
        &[
            "kind",
            "stable_id",
            "revision_or_digest",
            "read_roots",
            "write_roots",
            "network_allowlist",
            "external_side_effects",
            "owner",
        ],
    )?;
    for key in ["kind", "stable_id", "revision_or_digest", "owner"] {
        let value = goal_required_string(target, key)?;
        if (key == "stable_id" || key == "revision_or_digest") && value == "unbound" {
            return Err(LabError::InvalidMission);
        }
    }
    for key in [
        "read_roots",
        "write_roots",
        "network_allowlist",
        "external_side_effects",
    ] {
        let values = goal_string_array(target, key)?;
        if key == "external_side_effects"
            && values
                .iter()
                .any(|value| !is_canonical_external_side_effect_key(value))
        {
            return Err(LabError::InvalidMission);
        }
        if key == "network_allowlist"
            && values
                .iter()
                .any(|value| !is_valid_target_network_entry(value))
        {
            return Err(LabError::InvalidMission);
        }
        if matches!(key, "read_roots" | "write_roots")
            && values
                .iter()
                .any(|value| target_scope_is_ambiguous_uri(value))
        {
            return Err(LabError::InvalidMission);
        }
    }

    let acceptance = object
        .get("acceptance")
        .and_then(serde_json::Value::as_array)
        .ok_or(LabError::InvalidMission)?;
    if acceptance.is_empty() {
        return Err(LabError::InvalidMission);
    }
    let mut previous_id: Option<&str> = None;
    let mut required_count = 0_u64;
    for raw in acceptance {
        let criterion = raw.as_object().ok_or(LabError::InvalidMission)?;
        goal_exact_keys(
            criterion,
            &[
                "predicate_id",
                "description",
                "evaluator",
                "inputs",
                "expected_result",
                "severity",
                "evidence_refs",
                "reproducibility_requirements",
            ],
        )?;
        let predicate_id = goal_required_string(criterion, "predicate_id")?;
        if previous_id.is_some_and(|previous| previous >= predicate_id) {
            return Err(LabError::InvalidMission);
        }
        previous_id = Some(predicate_id);
        let _ = goal_required_string(criterion, "description")?;
        let _ = goal_required_string(criterion, "evaluator")?;
        let _ = goal_string_array(criterion, "inputs")?;
        let _ = goal_string_array(criterion, "evidence_refs")?;
        let _ = goal_string_array(criterion, "reproducibility_requirements")?;
        let expected = criterion
            .get("expected_result")
            .ok_or(LabError::InvalidMission)?;
        if !(expected.is_null()
            || expected.is_string()
            || expected.is_boolean()
            || expected.is_number())
        {
            return Err(LabError::InvalidMission);
        }
        match goal_required_string(criterion, "severity")? {
            "required" => {
                required_count = required_count.saturating_add(1);
                if criterion
                    .get("evidence_refs")
                    .and_then(serde_json::Value::as_array)
                    .is_some_and(Vec::is_empty)
                    && object
                        .get("evidence_policy")
                        .and_then(serde_json::Value::as_object)
                        .and_then(|policy| policy.get("require_independent_verifier"))
                        .and_then(serde_json::Value::as_bool)
                        == Some(true)
                {
                    return Err(LabError::InvalidMission);
                }
            }
            "advisory" => {}
            _ => return Err(LabError::InvalidMission),
        }
    }
    if required_count == 0 {
        return Err(LabError::InvalidMission);
    }

    let budget = object
        .get("budget")
        .and_then(serde_json::Value::as_object)
        .ok_or(LabError::InvalidMission)?;
    goal_exact_keys(
        budget,
        &[
            "token_limit",
            "attempt_limit",
            "wall_time_limit_ms",
            "cost_limit_minor_units",
            "cpu_time_limit_ms",
            "reserved_tokens",
        ],
    )?;
    let token_limit = budget
        .get("token_limit")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or(LabError::InvalidMission)?;
    let attempt_limit = budget
        .get("attempt_limit")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or(LabError::InvalidMission)?;
    let reserved_tokens = budget
        .get("reserved_tokens")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value < token_limit)
        .ok_or(LabError::InvalidMission)?;
    for key in [
        "wall_time_limit_ms",
        "cost_limit_minor_units",
        "cpu_time_limit_ms",
    ] {
        goal_optional_nonnegative_u64(budget, key)?;
    }
    let expected_reserved_tokens = mission
        .budget_policy
        .finalization_reserve
        .tokens
        .saturating_add(mission.budget_policy.recovery_reserve.tokens);
    if token_limit != mission.budget_policy.total.tokens
        || reserved_tokens != expected_reserved_tokens
        || mission
            .max_external_attempts
            .is_some_and(|expected| attempt_limit != expected)
    {
        return Err(LabError::InvalidMission);
    }

    let evidence_policy = object
        .get("evidence_policy")
        .and_then(serde_json::Value::as_object)
        .ok_or(LabError::InvalidMission)?;
    goal_exact_keys(
        evidence_policy,
        &[
            "required_record_types",
            "minimum_sources",
            "require_independent_verifier",
            "trusted_verifier_ids",
        ],
    )?;
    let required_record_types = goal_string_array(evidence_policy, "required_record_types")?;
    if required_record_types.iter().any(|record_type| {
        !matches!(
            record_type.as_str(),
            "source" | "claim" | "hypothesis" | "experiment" | "observation"
        )
    }) {
        return Err(LabError::InvalidMission);
    }
    let trusted_verifier_ids = goal_string_array(evidence_policy, "trusted_verifier_ids")?;
    if evidence_policy
        .get("minimum_sources")
        .and_then(serde_json::Value::as_u64)
        .is_none()
        || evidence_policy
            .get("require_independent_verifier")
            .and_then(serde_json::Value::as_bool)
            .is_none()
        || (evidence_policy
            .get("require_independent_verifier")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
            && trusted_verifier_ids.is_empty())
    {
        return Err(LabError::InvalidMission);
    }

    let generation = object
        .get("generation")
        .and_then(serde_json::Value::as_u64)
        .ok_or(LabError::InvalidMission)?;
    let parent_goal_id = object
        .get("parent_goal_id")
        .ok_or(LabError::InvalidMission)?;
    let parent_contract_hash = object
        .get("parent_contract_hash")
        .ok_or(LabError::InvalidMission)?;
    let evolution_reason = object
        .get("evolution_reason")
        .ok_or(LabError::InvalidMission)?;
    let has_parent =
        !parent_goal_id.is_null() || !parent_contract_hash.is_null() || !evolution_reason.is_null();
    if has_parent {
        let parent_goal = goal_required_string(object, "parent_goal_id")?;
        let parent_hash = goal_required_string(object, "parent_contract_hash")?;
        if !is_hex_digest(parent_hash)
            || !goal_required_string(object, "evolution_reason").is_ok()
            || parent_goal.is_empty()
        {
            return Err(LabError::InvalidMission);
        }
    }
    if generation > 1 && !has_parent {
        return Err(LabError::InvalidMission);
    }
    Ok(())
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
    /// Optional execution binding established by the first goal-bound event.
    /// Missing means an unbound legacy/compatibility run; it is intentionally
    /// not an authorization or signature field.
    #[serde(default)]
    projection_execution_binding: Option<ProjectionExecutionBinding>,
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
    #[serde(default)]
    managed_internal: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
struct ProjectionExecutionBinding {
    goal_contract_hash: String,
    generation: u64,
    target_digest: String,
    plan_digest: String,
    mission_id: String,
    task_id: u64,
    attempt_id: u64,
    owner_id: String,
    principal_id: String,
    evidence_digest: Option<String>,
    completion_digest: Option<String>,
    #[serde(default)]
    execution_cell_manifest_hash: Option<String>,
    #[serde(default)]
    execution_cell_id: Option<String>,
    #[serde(default)]
    execution_action_kind: Option<String>,
    #[serde(default)]
    backend_kind: Option<String>,
    #[serde(default)]
    resource_policy_hash: Option<String>,
    binding_hash: String,
}

impl ProjectionExecutionBinding {
    const SCHEMA: &'static str = "aegis-execution-binding-v1";

    fn unsigned_value(&self) -> serde_json::Value {
        let mut value = serde_json::json!({
            "schema": Self::SCHEMA,
            "goal_contract_hash": self.goal_contract_hash,
            "generation": self.generation,
            "target_digest": self.target_digest,
            "plan_digest": self.plan_digest,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "owner_id": self.owner_id,
            "principal_id": self.principal_id,
            "evidence_digest": self.evidence_digest,
            "completion_digest": self.completion_digest,
        });
        if let Some(manifest_hash) = &self.execution_cell_manifest_hash {
            value["execution_cell_manifest_hash"] = serde_json::json!(manifest_hash);
        }
        if let Some(cell_id) = &self.execution_cell_id {
            value["execution_cell_id"] = serde_json::json!(cell_id);
            value["execution_action_kind"] = serde_json::json!(self.execution_action_kind);
        }
        if let Some(backend_kind) = &self.backend_kind {
            value["backend_kind"] = serde_json::json!(backend_kind);
            value["resource_policy_hash"] = serde_json::json!(self.resource_policy_hash);
        }
        value
    }

    fn validate_wire(value: &serde_json::Value) -> Result<Self, LabError> {
        validate_goal_contract_limits(value).map_err(|_| LabError::InvalidRecord)?;
        let object = value.as_object().ok_or(LabError::InvalidRecord)?;
        let legacy_keys = [
            "schema",
            "goal_contract_hash",
            "generation",
            "target_digest",
            "plan_digest",
            "mission_id",
            "task_id",
            "attempt_id",
            "owner_id",
            "principal_id",
            "evidence_digest",
            "completion_digest",
            "binding_hash",
        ];
        let manifest_keys = [
            "schema",
            "goal_contract_hash",
            "generation",
            "target_digest",
            "plan_digest",
            "mission_id",
            "task_id",
            "attempt_id",
            "owner_id",
            "principal_id",
            "evidence_digest",
            "completion_digest",
            "execution_cell_manifest_hash",
            "binding_hash",
        ];
        let identity_keys = [
            "schema",
            "goal_contract_hash",
            "generation",
            "target_digest",
            "plan_digest",
            "mission_id",
            "task_id",
            "attempt_id",
            "owner_id",
            "principal_id",
            "evidence_digest",
            "completion_digest",
            "execution_cell_manifest_hash",
            "backend_kind",
            "resource_policy_hash",
            "binding_hash",
        ];
        let selected_identity_keys = [
            "schema",
            "goal_contract_hash",
            "generation",
            "target_digest",
            "plan_digest",
            "mission_id",
            "task_id",
            "attempt_id",
            "owner_id",
            "principal_id",
            "evidence_digest",
            "completion_digest",
            "execution_cell_manifest_hash",
            "execution_cell_id",
            "execution_action_kind",
            "backend_kind",
            "resource_policy_hash",
            "binding_hash",
        ];
        if !(has_exact_json_keys(object, &legacy_keys)
            || has_exact_json_keys(object, &manifest_keys)
            || has_exact_json_keys(object, &identity_keys)
            || has_exact_json_keys(object, &selected_identity_keys))
            || object.get("schema").and_then(serde_json::Value::as_str) != Some(Self::SCHEMA)
        {
            return Err(LabError::InvalidRecord);
        }

        let digest = |key: &str| {
            object
                .get(key)
                .and_then(serde_json::Value::as_str)
                .filter(|value| is_hex_digest(value))
                .map(str::to_string)
                .ok_or(LabError::InvalidRecord)
        };
        let text = |key: &str| goal_required_string(object, key).map(str::to_string);
        let positive = |key: &str| {
            object
                .get(key)
                .and_then(serde_json::Value::as_u64)
                .filter(|value| *value > 0)
                .ok_or(LabError::InvalidRecord)
        };
        let optional_digest = |key: &str| match object.get(key) {
            Some(serde_json::Value::Null) => Ok(None),
            Some(value) => value
                .as_str()
                .filter(|value| is_hex_digest(value))
                .map(|value| Some(value.to_string()))
                .ok_or(LabError::InvalidRecord),
            None => Err(LabError::InvalidRecord),
        };
        let optional_manifest_digest = match object.get("execution_cell_manifest_hash") {
            None | Some(serde_json::Value::Null) => Ok(None),
            Some(value) => value
                .as_str()
                .filter(|value| is_hex_digest(value))
                .map(|value| Some(value.to_string()))
                .ok_or(LabError::InvalidRecord),
        };
        let optional_execution_cell_id = match object.get("execution_cell_id") {
            None => Ok(None),
            Some(value) => value
                .as_str()
                .filter(|item| !item.trim().is_empty() && *item == item.trim())
                .map(|value| Some(value.to_string()))
                .ok_or(LabError::InvalidRecord),
        };
        let optional_execution_action_kind = match object.get("execution_action_kind") {
            None => Ok(None),
            Some(value) => value
                .as_str()
                .filter(|item| !item.trim().is_empty() && *item == item.trim())
                .map(|value| Some(value.to_ascii_lowercase()))
                .ok_or(LabError::InvalidRecord),
        };
        let optional_backend_kind = match object.get("backend_kind") {
            None => Ok(None),
            Some(value) => value
                .as_str()
                .filter(|value| !value.trim().is_empty() && *value == value.trim())
                .map(|value| Some(value.to_ascii_lowercase()))
                .ok_or(LabError::InvalidRecord),
        };
        let optional_resource_policy_hash = match object.get("resource_policy_hash") {
            None | Some(serde_json::Value::Null) => Ok(None),
            Some(value) => value
                .as_str()
                .filter(|value| is_hex_digest(value))
                .map(|value| Some(value.to_string()))
                .ok_or(LabError::InvalidRecord),
        };

        let binding = Self {
            goal_contract_hash: digest("goal_contract_hash")?,
            generation: positive("generation")?,
            target_digest: digest("target_digest")?,
            plan_digest: digest("plan_digest")?,
            mission_id: text("mission_id")?,
            task_id: positive("task_id")?,
            attempt_id: positive("attempt_id")?,
            owner_id: text("owner_id")?,
            principal_id: text("principal_id")?,
            evidence_digest: optional_digest("evidence_digest")?,
            completion_digest: optional_digest("completion_digest")?,
            execution_cell_manifest_hash: optional_manifest_digest?,
            execution_cell_id: optional_execution_cell_id?,
            execution_action_kind: optional_execution_action_kind?,
            backend_kind: optional_backend_kind?,
            resource_policy_hash: optional_resource_policy_hash?,
            binding_hash: digest("binding_hash")?,
        };
        if (binding.backend_kind.is_some()) != (binding.resource_policy_hash.is_some())
            || (binding.backend_kind.is_some() && binding.execution_cell_manifest_hash.is_none())
            || (binding.execution_cell_id.is_some()) != (binding.execution_action_kind.is_some())
            || (binding.execution_cell_id.is_some() && binding.backend_kind.is_none())
            || ((binding.execution_cell_id.is_some() || binding.backend_kind.is_some())
                && binding.execution_cell_manifest_hash.is_none())
        {
            return Err(LabError::InvalidRecord);
        }
        if binding.binding_hash != execution_binding_digest(&binding.unsigned_value()) {
            return Err(LabError::InvalidRecord);
        }
        Ok(binding)
    }
}

fn execution_binding_digest(value: &serde_json::Value) -> String {
    let encoded = serde_json::to_vec(&canonical_json_value(value))
        .expect("execution binding values are serializable");
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-execution-binding-canonical-v1\0");
    hasher.update(&encoded);
    digest_hex(*hasher.finalize().as_bytes())
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
            projection_execution_binding: None,
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

    fn external_attempt_budget_valid(&self) -> bool {
        let Some(bound) = self.runtime.mission().max_external_attempts else {
            // A missing field is a legacy snapshot. It remains readable, but
            // new missions always carry an explicit bound.
            return true;
        };
        let observed = self
            .runtime
            .events
            .iter()
            .filter(|event| is_external_admission_kind(event.kind))
            .count() as u64;
        observed <= bound
    }

    fn external_attempt_budget_allows(&self, kind: LabEventKind) -> bool {
        let Some(bound) = self.runtime.mission().max_external_attempts else {
            return true;
        };
        if !is_external_admission_kind(kind) {
            return true;
        }
        let observed = self
            .runtime
            .events
            .iter()
            .filter(|event| is_external_admission_kind(event.kind))
            .count() as u64;
        observed < bound
    }

    pub fn snapshot_json(&self) -> Result<String, LabError> {
        serde_json::to_string(self).map_err(|_| LabError::InvalidEvent)
    }

    pub fn from_snapshot_json(snapshot: &str) -> Result<Self, LabError> {
        let controller: Self =
            serde_json::from_str(snapshot).map_err(|_| LabError::InvalidEvent)?;
        controller.runtime.validate_snapshot()?;
        if controller.steps > controller.runtime.mission().max_steps
            || !controller.external_attempt_budget_valid()
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
            LabEventKind::ExecutionCellManifestRecorded
                | LabEventKind::SourceCaptured
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
        if !self.external_attempt_budget_allows(event.kind) {
            return Err(LabError::InvalidTransition);
        }
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
        let execution_binding = self.validate_projection_binding(&event, &payload)?;
        if let Some(binding) = execution_binding {
            if let Some(existing) = &self.projection_execution_binding {
                if existing != &binding {
                    return Err(LabError::InvalidRecord);
                }
            } else {
                self.projection_execution_binding = Some(binding);
            }
        }
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
            let (pairs, _) = raw.as_bytes().as_chunks::<2>();
            let bytes = pairs
                .iter()
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
            LabEventKind::ExecutionCellManifestRecorded => {
                matches!(self.runtime.state, LabRunState::Planned)
            }
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
            LabEventKind::ExecutionCellManifestRecorded => {
                let schema = non_empty("schema")?;
                let manifest_hash = non_empty("manifest_hash")?;
                let manifest = object
                    .get("manifest")
                    .and_then(serde_json::Value::as_array)
                    .ok_or(LabError::InvalidRecord)?;
                let cell_count = object
                    .get("cell_count")
                    .and_then(serde_json::Value::as_u64)
                    .ok_or(LabError::InvalidRecord)?;
                if schema != "aegis-execution-cell-manifest-v1"
                    || !is_hex_digest(&manifest_hash)
                    || cell_count != manifest.len() as u64
                    || digest_hex(projection_payload_hash(
                        object.get("manifest").ok_or(LabError::InvalidRecord)?,
                    )) != manifest_hash
                {
                    return Err(LabError::InvalidRecord);
                }
                let normalized_array = |cell: &serde_json::Map<String, serde_json::Value>,
                                        key: &str,
                                        require_non_empty: bool,
                                        lowercase: bool,
                                        uppercase: bool|
                 -> Result<Vec<String>, LabError> {
                    let values = cell
                        .get(key)
                        .and_then(serde_json::Value::as_array)
                        .ok_or(LabError::InvalidRecord)?;
                    if require_non_empty && values.is_empty() {
                        return Err(LabError::InvalidRecord);
                    }
                    let mut result = Vec::with_capacity(values.len());
                    for value in values {
                        let item = value.as_str().ok_or(LabError::InvalidRecord)?;
                        if item.trim().is_empty()
                            || item != item.trim()
                            || (lowercase && item != item.to_ascii_lowercase())
                            || (uppercase && item != item.to_ascii_uppercase())
                        {
                            return Err(LabError::InvalidRecord);
                        }
                        result.push(item.to_string());
                    }
                    Ok(result)
                };
                let mut identities = BTreeSet::new();
                for entry in manifest {
                    let cell = entry.as_object().ok_or(LabError::InvalidRecord)?;
                    if cell.keys().any(|key| {
                        !matches!(
                            key.as_str(),
                            "cell_id"
                                | "action_kinds"
                                | "capabilities"
                                | "effect_classes"
                                | "trust_levels"
                                | "trust_policy_hash"
                                | "backend_kind"
                                | "resource_policy"
                                | "resource_policy_hash"
                        )
                    }) {
                        return Err(LabError::InvalidRecord);
                    }
                    let cell_id = cell
                        .get("cell_id")
                        .and_then(serde_json::Value::as_str)
                        .ok_or(LabError::InvalidRecord)?;
                    if cell_id.trim().is_empty() || cell_id != cell_id.trim() {
                        return Err(LabError::InvalidRecord);
                    }
                    let action_kinds = normalized_array(cell, "action_kinds", true, true, false)?;
                    if action_kinds.iter().any(|kind| {
                        !matches!(
                            kind.as_str(),
                            "context_retrieval"
                                | "search_program"
                                | "browser_action"
                                | "experiment_action"
                                | "simulation_action"
                                | "tool_call"
                                | "benchmark_validation"
                                | "skill_execution"
                                | "post_completion_effect"
                                | "progress_checkpoint"
                        )
                    }) {
                        return Err(LabError::InvalidRecord);
                    }
                    let _ = normalized_array(cell, "capabilities", false, false, false)?;
                    let _ = normalized_array(cell, "effect_classes", false, false, false)?;
                    let _ = normalized_array(cell, "trust_levels", true, false, true)?;
                    if cell
                        .get("trust_policy_hash")
                        .is_some_and(|value| value.as_str().is_none_or(|item| !is_hex_digest(item)))
                    {
                        return Err(LabError::InvalidRecord);
                    }
                    let backend_kind = cell
                        .get("backend_kind")
                        .map(|value| {
                            value
                                .as_str()
                                .filter(|item| {
                                    !item.trim().is_empty()
                                        && *item == item.trim()
                                        && *item == item.to_ascii_lowercase()
                                })
                                .ok_or(LabError::InvalidRecord)
                        })
                        .transpose()?;
                    let resource_policy_hash = cell
                        .get("resource_policy_hash")
                        .map(|value| {
                            value
                                .as_str()
                                .filter(|item| is_hex_digest(item))
                                .ok_or(LabError::InvalidRecord)
                        })
                        .transpose()?;
                    let resource_policy = cell.get("resource_policy");
                    if backend_kind.is_none()
                        && (resource_policy.is_some() || resource_policy_hash.is_some())
                    {
                        return Err(LabError::InvalidRecord);
                    }
                    if backend_kind.is_some() && resource_policy.is_none() {
                        return Err(LabError::InvalidRecord);
                    }
                    if let (Some(policy), Some(policy_hash)) =
                        (resource_policy, resource_policy_hash)
                    {
                        if execution_cell_resource_policy_hash(policy)? != policy_hash {
                            return Err(LabError::InvalidRecord);
                        }
                    }
                    if backend_kind.is_some() != resource_policy_hash.is_some() {
                        return Err(LabError::InvalidRecord);
                    }
                    if !identities.insert((cell_id.to_string(), action_kinds)) {
                        return Err(LabError::InvalidRecord);
                    }
                }
            }
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
                let managed_internal = match object.get("managed_internal") {
                    None => false,
                    Some(value) => value.as_bool().ok_or(LabError::InvalidRecord)?,
                };
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
                        managed_internal,
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
                let managed_internal = match object.get("managed_internal") {
                    None => false,
                    Some(value) => value.as_bool().ok_or(LabError::InvalidRecord)?,
                };
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
                    || admission.managed_internal != managed_internal
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
            LabEventKind::ReviewRecorded => {
                if object
                    .get("record_type")
                    .and_then(serde_json::Value::as_str)
                    == Some("goal_verification")
                {
                    let verification = object
                        .get("verification")
                        .and_then(serde_json::Value::as_object)
                        .ok_or(LabError::InvalidRecord)?;
                    if !has_exact_json_keys(
                        verification,
                        &[
                            "schema",
                            "contract_hash",
                            "generation",
                            "verifier_id",
                            "independent",
                            "predicate_results",
                            "evidence_refs",
                            "evidence_complete",
                            "verification_hash",
                        ],
                    ) || verification
                        .get("schema")
                        .and_then(serde_json::Value::as_str)
                        != Some("aegis-goal-verification-v1")
                        || verification
                            .get("contract_hash")
                            .and_then(serde_json::Value::as_str)
                            .is_none_or(|value| !is_hex_digest(value))
                        || verification
                            .get("generation")
                            .and_then(serde_json::Value::as_u64)
                            == Some(0)
                        || verification
                            .get("verifier_id")
                            .and_then(serde_json::Value::as_str)
                            .is_none_or(|value| value.trim().is_empty())
                        || verification
                            .get("independent")
                            .and_then(serde_json::Value::as_bool)
                            .is_none()
                        || verification
                            .get("evidence_complete")
                            .and_then(serde_json::Value::as_bool)
                            .is_none()
                        || verification
                            .get("verification_hash")
                            .and_then(serde_json::Value::as_str)
                            .is_none_or(|value| !is_hex_digest(value))
                    {
                        return Err(LabError::InvalidRecord);
                    }
                    let predicate_results = verification
                        .get("predicate_results")
                        .and_then(serde_json::Value::as_array)
                        .ok_or(LabError::InvalidRecord)?;
                    if predicate_results.iter().any(|item| {
                        let Some(pair) = item.as_array() else {
                            return true;
                        };
                        pair.len() != 2
                            || pair[0].as_str().is_none_or(|value| value.trim().is_empty())
                            || pair[1].as_bool().is_none()
                    }) {
                        return Err(LabError::InvalidRecord);
                    }
                    let evidence_refs = verification
                        .get("evidence_refs")
                        .and_then(serde_json::Value::as_array)
                        .ok_or(LabError::InvalidRecord)?;
                    if evidence_refs
                        .iter()
                        .any(|item| item.as_str().is_none_or(|value| value.trim().is_empty()))
                    {
                        return Err(LabError::InvalidRecord);
                    }
                    let supplied_hash = verification
                        .get("verification_hash")
                        .and_then(serde_json::Value::as_str)
                        .ok_or(LabError::InvalidRecord)?;
                    let mut unsigned = verification.clone();
                    unsigned.remove("verification_hash");
                    if supplied_hash != digest_hex(goal_verification_hash(&unsigned)) {
                        return Err(LabError::InvalidRecord);
                    }
                    self.validate_goal_verification_contract(verification)?;
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

    fn validate_goal_verification_contract(
        &self,
        verification: &serde_json::Map<String, serde_json::Value>,
    ) -> Result<(), LabError> {
        let Some(contract_json) = &self.runtime.mission().goal_contract_json else {
            return Err(LabError::InvalidRecord);
        };
        let contract: serde_json::Value =
            serde_json::from_str(contract_json).map_err(|_| LabError::InvalidRecord)?;
        let contract_object = contract.as_object().ok_or(LabError::InvalidRecord)?;
        let contract_hash = contract_object
            .get("contract_hash")
            .and_then(serde_json::Value::as_str)
            .ok_or(LabError::InvalidRecord)?;
        let generation = contract_object
            .get("generation")
            .and_then(serde_json::Value::as_u64)
            .ok_or(LabError::InvalidRecord)?;
        if verification
            .get("contract_hash")
            .and_then(serde_json::Value::as_str)
            != Some(contract_hash)
            || verification
                .get("generation")
                .and_then(serde_json::Value::as_u64)
                != Some(generation)
        {
            return Err(LabError::InvalidRecord);
        }

        let policy = contract_object
            .get("evidence_policy")
            .and_then(serde_json::Value::as_object)
            .ok_or(LabError::InvalidRecord)?;
        let require_independent = policy
            .get("require_independent_verifier")
            .and_then(serde_json::Value::as_bool)
            .ok_or(LabError::InvalidRecord)?;
        let required_record_types = goal_string_array(policy, "required_record_types")?;
        let trusted_verifiers = goal_string_array(policy, "trusted_verifier_ids")?;
        let verifier_id = verification
            .get("verifier_id")
            .and_then(serde_json::Value::as_str)
            .ok_or(LabError::InvalidRecord)?;
        let independent = verification
            .get("independent")
            .and_then(serde_json::Value::as_bool)
            .ok_or(LabError::InvalidRecord)?;
        let author_id = contract_object
            .get("author_id")
            .and_then(serde_json::Value::as_str)
            .ok_or(LabError::InvalidRecord)?;
        if verifier_id == author_id
            || (require_independent
                && (!independent || !trusted_verifiers.iter().any(|id| id == verifier_id)))
        {
            return Err(LabError::InvalidRecord);
        }

        let acceptance = contract_object
            .get("acceptance")
            .and_then(serde_json::Value::as_array)
            .ok_or(LabError::InvalidRecord)?;
        let mut known_predicates = BTreeSet::new();
        let mut predicate_evidence: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        let mut required_predicates = BTreeSet::new();
        let mut required_evidence = BTreeSet::new();
        for raw_predicate in acceptance {
            let predicate = raw_predicate.as_object().ok_or(LabError::InvalidRecord)?;
            let predicate_id = goal_required_string(predicate, "predicate_id")?;
            let severity = goal_required_string(predicate, "severity")?;
            let evidence_refs = goal_string_array(predicate, "evidence_refs")?;
            if !known_predicates.insert(predicate_id.to_string()) {
                return Err(LabError::InvalidRecord);
            }
            let predicate_evidence_set = evidence_refs.into_iter().collect::<BTreeSet<_>>();
            if severity == "required" {
                required_predicates.insert(predicate_id.to_string());
                required_evidence.extend(predicate_evidence_set.iter().cloned());
            }
            predicate_evidence.insert(predicate_id.to_string(), predicate_evidence_set);
        }
        let results = verification
            .get("predicate_results")
            .and_then(serde_json::Value::as_array)
            .ok_or(LabError::InvalidRecord)?;
        let mut result_ids = BTreeSet::new();
        for raw_result in results {
            let pair = raw_result.as_array().ok_or(LabError::InvalidRecord)?;
            if pair.len() != 2 || pair[1].as_bool().is_none() {
                return Err(LabError::InvalidRecord);
            }
            let predicate_id = pair[0]
                .as_str()
                .filter(|value| !value.trim().is_empty())
                .ok_or(LabError::InvalidRecord)?;
            if !known_predicates.contains(predicate_id)
                || !result_ids.insert(predicate_id.to_string())
            {
                return Err(LabError::InvalidRecord);
            }
        }
        let evidence_refs = verification
            .get("evidence_refs")
            .and_then(serde_json::Value::as_array)
            .ok_or(LabError::InvalidRecord)?;
        let mut evidence_ids = BTreeSet::new();
        for raw_ref in evidence_refs {
            let evidence_id = raw_ref
                .as_str()
                .filter(|value| !value.trim().is_empty())
                .ok_or(LabError::InvalidRecord)?;
            if !evidence_ids.insert(evidence_id.to_string()) {
                return Err(LabError::InvalidRecord);
            }
        }
        let evidence_complete = verification
            .get("evidence_complete")
            .and_then(serde_json::Value::as_bool)
            .ok_or(LabError::InvalidRecord)?;
        if evidence_complete {
            for record_type in &required_record_types {
                let present = match record_type.as_str() {
                    "source" => {
                        !self.projection_sources.is_empty() || !self.runtime.sources.is_empty()
                    }
                    "claim" => {
                        !self.projection_claims.is_empty() || !self.runtime.claims.is_empty()
                    }
                    "hypothesis" => {
                        !self.projection_hypotheses.is_empty()
                            || !self.runtime.hypotheses.is_empty()
                    }
                    "experiment" => {
                        !self.projection_experiments.is_empty()
                            || !self.runtime.experiments.is_empty()
                    }
                    "observation" => {
                        !self.projection_observations.is_empty()
                            || !self.runtime.observations.is_empty()
                    }
                    _ => false,
                };
                if !present {
                    return Err(LabError::InvalidRecord);
                }
            }
            if !required_predicates.is_subset(&result_ids)
                || !required_evidence.is_subset(&evidence_ids)
            {
                return Err(LabError::InvalidRecord);
            }
            if result_ids.iter().any(|predicate_id| {
                predicate_evidence
                    .get(predicate_id)
                    .is_some_and(|refs| !refs.is_subset(&evidence_ids))
            }) {
                return Err(LabError::InvalidRecord);
            }
            let evidence_is_retained = |evidence_id: &str| {
                self.projection_sources.contains(evidence_id)
                    || self.runtime.sources.contains_key(evidence_id)
                    || self.projection_claims.contains(evidence_id)
                    || self.runtime.claims.contains_key(evidence_id)
                    || self.projection_hypotheses.contains(evidence_id)
                    || self.runtime.hypotheses.contains_key(evidence_id)
                    || self.projection_experiments.contains_key(evidence_id)
                    || self.runtime.experiments.contains_key(evidence_id)
                    || self.projection_observations.contains_key(evidence_id)
                    || self.runtime.observations.contains_key(evidence_id)
            };
            if required_evidence
                .iter()
                .any(|evidence_id| !evidence_is_retained(evidence_id))
            {
                return Err(LabError::InvalidRecord);
            }
            let minimum_sources = policy
                .get("minimum_sources")
                .and_then(serde_json::Value::as_u64)
                .ok_or(LabError::InvalidRecord)?;
            let retained_count = evidence_ids
                .iter()
                .filter(|evidence_id| {
                    self.projection_sources.contains(*evidence_id)
                        || self.runtime.sources.contains_key(*evidence_id)
                })
                .count() as u64;
            if retained_count < minimum_sources {
                return Err(LabError::InvalidRecord);
            }
        }
        Ok(())
    }

    fn validate_projection_binding(
        &self,
        event: &LabEvent,
        payload: &serde_json::Value,
    ) -> Result<Option<ProjectionExecutionBinding>, LabError> {
        let object = payload.as_object().ok_or(LabError::InvalidRecord)?;
        let execution_binding = self.validate_goal_event_binding(event.kind, object)?;
        let parent_hex = digest_hex(event.previous_event_hash);
        match event.kind {
            LabEventKind::ExecutionCellManifestRecorded => {
                if event.sequence != 2
                    || event.state_epoch != 1
                    || self
                        .runtime
                        .events
                        .first()
                        .is_none_or(|first| first.kind != LabEventKind::MissionCreated)
                {
                    return Err(LabError::InvalidRecord);
                }
            }
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
        Ok(execution_binding)
    }

    fn projection_execution_cell_manifest_hash(&self) -> Result<Option<String>, LabError> {
        let mut manifest_hash: Option<String> = None;
        for event in self
            .runtime
            .events
            .iter()
            .filter(|event| event.kind == LabEventKind::ExecutionCellManifestRecorded)
        {
            let payload = self
                .projection_payloads
                .get(&event.sequence)
                .ok_or(LabError::InvalidRecord)?;
            let hash = payload
                .as_object()
                .and_then(|object| object.get("manifest_hash"))
                .and_then(serde_json::Value::as_str)
                .filter(|value| is_hex_digest(value))
                .ok_or(LabError::InvalidRecord)?
                .to_string();
            if manifest_hash.replace(hash).is_some() {
                return Err(LabError::InvalidRecord);
            }
        }
        Ok(manifest_hash)
    }

    fn projection_execution_cell_identity_matches(
        &self,
        execution_cell_id: Option<&str>,
        execution_action_kind: Option<&str>,
        backend_kind: &str,
        resource_policy_hash: &str,
    ) -> Result<bool, LabError> {
        for event in self
            .runtime
            .events
            .iter()
            .filter(|event| event.kind == LabEventKind::ExecutionCellManifestRecorded)
        {
            let payload = self
                .projection_payloads
                .get(&event.sequence)
                .ok_or(LabError::InvalidRecord)?;
            let Some(manifest) = payload
                .as_object()
                .and_then(|object| object.get("manifest"))
                .and_then(serde_json::Value::as_array)
            else {
                return Err(LabError::InvalidRecord);
            };
            let matches = manifest
                .iter()
                .filter(|entry| {
                    let Some(cell) = entry.as_object() else {
                        return false;
                    };
                    let selected_matches = execution_cell_id.is_none_or(|cell_id| {
                        cell.get("cell_id").and_then(serde_json::Value::as_str) == Some(cell_id)
                    }) && execution_action_kind.is_none_or(|action_kind| {
                        cell.get("action_kinds")
                            .and_then(serde_json::Value::as_array)
                            .is_some_and(|kinds| {
                                kinds.iter().any(|kind| kind.as_str() == Some(action_kind))
                            })
                    });
                    selected_matches
                        && cell.get("backend_kind").and_then(serde_json::Value::as_str)
                            == Some(backend_kind)
                        && cell
                            .get("resource_policy_hash")
                            .and_then(serde_json::Value::as_str)
                            == Some(resource_policy_hash)
                        && cell.get("resource_policy").is_some_and(|policy| {
                            execution_cell_resource_policy_hash(policy).ok().as_deref()
                                == Some(resource_policy_hash)
                        })
                })
                .count();
            if matches == 1 {
                return Ok(true);
            }
            if matches > 1 {
                return Err(LabError::InvalidRecord);
            }
        }
        Ok(false)
    }

    fn validate_goal_event_binding(
        &self,
        kind: LabEventKind,
        payload: &serde_json::Map<String, serde_json::Value>,
    ) -> Result<Option<ProjectionExecutionBinding>, LabError> {
        if !is_goal_bound_event_kind(kind) {
            return Ok(None);
        }
        let Some(contract_json) = &self.runtime.mission().goal_contract_json else {
            // Legacy missions predate goal bindings and remain readable. New
            // explicit contracts are validated strictly below.
            if payload.contains_key("execution_binding") {
                return Err(LabError::InvalidRecord);
            }
            return Ok(None);
        };
        let contract: serde_json::Value =
            serde_json::from_str(contract_json).map_err(|_| LabError::InvalidRecord)?;
        let contract_object = contract.as_object().ok_or(LabError::InvalidRecord)?;
        let goal_id = contract_object
            .get("goal_id")
            .and_then(serde_json::Value::as_str)
            .ok_or(LabError::InvalidRecord)?;
        let generation = contract_object
            .get("generation")
            .and_then(serde_json::Value::as_u64)
            .ok_or(LabError::InvalidRecord)?;
        let contract_hash = contract_object
            .get("contract_hash")
            .and_then(serde_json::Value::as_str)
            .ok_or(LabError::InvalidRecord)?;
        let target = contract_object
            .get("target")
            .ok_or(LabError::InvalidRecord)?;
        let target_digest = digest_hex(projection_payload_hash(target));
        let manifest_hash = self.projection_execution_cell_manifest_hash()?;
        match (&manifest_hash, payload.get("execution_cell_manifest_hash")) {
            (Some(expected), Some(actual)) if actual.as_str() == Some(expected.as_str()) => {}
            (None, None) => {}
            _ => return Err(LabError::InvalidRecord),
        }
        if payload.get("goal_id").and_then(serde_json::Value::as_str) != Some(goal_id)
            || payload
                .get("goal_generation")
                .and_then(serde_json::Value::as_u64)
                != Some(generation)
            || payload
                .get("goal_contract_hash")
                .and_then(serde_json::Value::as_str)
                != Some(contract_hash)
            || payload
                .get("target_digest")
                .and_then(serde_json::Value::as_str)
                != Some(target_digest.as_str())
        {
            return Err(LabError::InvalidRecord);
        }

        let execution_binding = if let Some(raw_binding) = payload.get("execution_binding") {
            let binding = ProjectionExecutionBinding::validate_wire(raw_binding)?;
            let owner = target
                .as_object()
                .and_then(|target| target.get("owner"))
                .and_then(serde_json::Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .ok_or(LabError::InvalidRecord)?;
            if binding.goal_contract_hash != contract_hash
                || binding.generation != generation
                || binding.mission_id != self.runtime.mission().mission_id
                || binding.target_digest != target_digest
                || binding.owner_id != owner
                || binding.execution_cell_manifest_hash.as_deref() != manifest_hash.as_deref()
                || binding.evidence_digest.is_some()
                || binding.completion_digest.is_some()
            {
                return Err(LabError::InvalidRecord);
            }
            if let (Some(backend_kind), Some(resource_policy_hash)) = (
                binding.backend_kind.as_deref(),
                binding.resource_policy_hash.as_deref(),
            ) {
                if binding.execution_cell_id.is_none() || binding.execution_action_kind.is_none() {
                    return Err(LabError::InvalidRecord);
                }
                if !self.projection_execution_cell_identity_matches(
                    binding.execution_cell_id.as_deref(),
                    binding.execution_action_kind.as_deref(),
                    backend_kind,
                    resource_policy_hash,
                )? {
                    return Err(LabError::InvalidRecord);
                }
            }
            if self.projection_execution_binding.is_none()
                && self.runtime.events.iter().any(|event| {
                    is_goal_bound_event_kind(event.kind)
                        && self
                            .projection_payloads
                            .get(&event.sequence)
                            .is_some_and(|prior| {
                                !prior
                                    .as_object()
                                    .is_some_and(|object| object.contains_key("execution_binding"))
                            })
                })
            {
                return Err(LabError::InvalidRecord);
            }
            if self
                .projection_execution_binding
                .as_ref()
                .is_some_and(|existing| existing != &binding)
            {
                return Err(LabError::InvalidRecord);
            }
            Some(binding)
        } else {
            if self.projection_execution_binding.is_some() {
                return Err(LabError::InvalidRecord);
            }
            None
        };
        if matches!(
            kind,
            LabEventKind::ToolExecutionAdmitted | LabEventKind::ToolExecutionRecorded
        ) {
            if let (Some(tool_name), Some(effect_class)) = (
                payload.get("tool_name").and_then(serde_json::Value::as_str),
                payload
                    .get("effect_class")
                    .and_then(serde_json::Value::as_str),
            ) {
                let managed_internal = payload
                    .get("managed_internal")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false);
                let local_effect = matches!(
                    effect_class,
                    "read_only"
                        | "network_read"
                        | "compute"
                        | "model_inference"
                        | "local_reversible"
                );
                if !local_effect
                    && !(managed_internal
                        && tool_name == "memory.index_session"
                        && effect_class == "memory_write"
                        && self.managed_internal_effect_registered())
                {
                    let expected_key = format!("{tool_name}::{effect_class}");
                    let declared = target
                        .get("external_side_effects")
                        .and_then(serde_json::Value::as_array)
                        .is_some_and(|keys| {
                            keys.iter()
                                .any(|key| key.as_str() == Some(expected_key.as_str()))
                        });
                    if !declared {
                        return Err(LabError::InvalidRecord);
                    }
                }
            }
        }
        Ok(execution_binding)
    }

    fn managed_internal_effect_registered(&self) -> bool {
        self.projection_payloads.values().any(|payload| {
            payload.get("schema").and_then(serde_json::Value::as_str)
                == Some("aegis-execution-cell-manifest-v1")
                && payload
                    .get("manifest")
                    .and_then(serde_json::Value::as_array)
                    .is_some_and(|manifest| {
                        manifest.iter().any(|entry| {
                            let Some(cell) = entry.as_object() else {
                                return false;
                            };
                            let has_post_completion_action = cell
                                .get("action_kinds")
                                .and_then(serde_json::Value::as_array)
                                .is_some_and(|kinds| {
                                    kinds
                                        .iter()
                                        .any(|kind| kind.as_str() == Some("post_completion_effect"))
                                });
                            let has_memory_capability = cell
                                .get("capabilities")
                                .and_then(serde_json::Value::as_array)
                                .is_some_and(|capabilities| {
                                    capabilities.iter().any(|capability| {
                                        capability.as_str() == Some("memory_write")
                                    })
                                });
                            let has_memory_effect = cell
                                .get("effect_classes")
                                .and_then(serde_json::Value::as_array)
                                .is_some_and(|effects| {
                                    effects
                                        .iter()
                                        .any(|effect| effect.as_str() == Some("memory_write"))
                                });
                            has_post_completion_action && has_memory_capability && has_memory_effect
                        })
                    })
        })
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
        let manifest_events = self
            .runtime
            .events
            .iter()
            .filter(|event| event.kind == LabEventKind::ExecutionCellManifestRecorded)
            .collect::<Vec<_>>();
        if manifest_events.len() > 1 {
            return Err(LabError::InvalidEvent);
        }
        if let Some(event) = manifest_events.first() {
            let Some(payload) = self.projection_payloads.get(&event.sequence) else {
                return Err(LabError::InvalidEvent);
            };
            let mut candidate = self.clone();
            candidate.validate_projection_payload(event.kind, payload)?;
            let _ = candidate.validate_projection_binding(event, payload)?;
        }
        let mut snapshot_binding: Option<ProjectionExecutionBinding> = None;
        for event in self
            .runtime
            .events
            .iter()
            .filter(|event| is_goal_bound_event_kind(event.kind))
        {
            let Some(payload) = self.projection_payloads.get(&event.sequence) else {
                return Err(LabError::InvalidEvent);
            };
            let object = payload.as_object().ok_or(LabError::InvalidEvent)?;
            let parsed = self
                .validate_goal_event_binding(event.kind, object)
                .map_err(|_| LabError::InvalidEvent)?;
            if let Some(binding) = parsed {
                if snapshot_binding
                    .as_ref()
                    .is_some_and(|existing| existing != &binding)
                {
                    return Err(LabError::InvalidEvent);
                }
                snapshot_binding = Some(binding);
            }
        }
        if snapshot_binding != self.projection_execution_binding {
            return Err(LabError::InvalidEvent);
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
    let encoded = serde_json::to_vec(&canonical_json_value(value))
        .expect("JSON projection payloads are serializable");
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-lab-projection-payload-v1\0");
    hasher.update(&encoded);
    *hasher.finalize().as_bytes()
}

fn execution_cell_resource_policy_hash(value: &serde_json::Value) -> Result<String, LabError> {
    let object = value.as_object().ok_or(LabError::InvalidRecord)?;
    let keys = [
        "schema",
        "cpu_time_limit_ms",
        "memory_bytes",
        "process_limit",
        "thread_limit",
        "fd_limit",
        "input_bytes",
        "output_bytes",
        "timeout_ms",
    ];
    if !has_exact_json_keys(object, &keys)
        || object.get("schema").and_then(serde_json::Value::as_str)
            != Some("aegis-execution-cell-resource-policy-v1")
    {
        return Err(LabError::InvalidRecord);
    }
    for key in keys.iter().skip(1) {
        let value = object
            .get(*key)
            .and_then(serde_json::Value::as_u64)
            .ok_or(LabError::InvalidRecord)?;
        if !matches!(*key, "cpu_time_limit_ms" | "input_bytes" | "output_bytes") && value == 0 {
            return Err(LabError::InvalidRecord);
        }
    }
    Ok(digest_hex(projection_payload_hash(value)))
}

fn goal_verification_hash(value: &serde_json::Map<String, serde_json::Value>) -> [u8; 32] {
    let encoded = serde_json::to_vec(&canonical_json_value(&serde_json::Value::Object(
        value.clone(),
    )))
    .expect("goal verification payloads are serializable");
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-contract-canonical-v1\0");
    hasher.update(&encoded);
    *hasher.finalize().as_bytes()
}

fn is_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn decode_hex_digest(value: &str) -> Option<[u8; 32]> {
    if !is_hex_digest(value) {
        return None;
    }
    let mut decoded = [0_u8; 32];
    for (index, chunk) in value.as_bytes().as_chunks::<2>().0.iter().enumerate() {
        let high = (chunk[0] as char).to_digit(16)? as u8;
        let low = (chunk[1] as char).to_digit(16)? as u8;
        decoded[index] = (high << 4) | low;
    }
    Some(decoded)
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

    fn explicit_goal_contract_json() -> (String, [u8; 32]) {
        let mut definition = serde_json::json!({
            "acceptance": [{
                "description": "the report contains a reproducible result",
                "evaluator": "aegis.test.reproducibility",
                "evidence_refs": ["source-1"],
                "expected_result": true,
                "inputs": ["report"],
                "predicate_id": "reproducible-result",
                "reproducibility_requirements": ["same-inputs"],
                "severity": "required"
            }],
            "author_id": "test-author",
            "budget": {
                "attempt_limit": 20,
                "cpu_time_limit_ms": null,
                "cost_limit_minor_units": null,
                "reserved_tokens": 30,
                "token_limit": 100,
                "wall_time_limit_ms": null
            },
            "created_at_ms": 1,
            "effective_epoch": 1,
            "evidence_policy": {
                "minimum_sources": 1,
                "required_record_types": ["source"],
                "require_independent_verifier": true,
                "trusted_verifier_ids": ["independent-verifier"]
            },
            "evolution_reason": null,
            "generation": 1,
            "goal_id": "goal-1",
            "non_goals": [],
            "objective": "test a hypothesis",
            "parent_contract_hash": null,
            "parent_goal_id": null,
             "policy_digest": "a".repeat(64),
            "scope": ["local deterministic fixture"],
            "schema": "aegis-goal-contract-v1",
            "target": {
                "external_side_effects": [],
                "kind": "fixture",
                "network_allowlist": [],
                "owner": "test-author",
                "read_roots": ["fixture://input"],
                "revision_or_digest": "revision-1",
                "stable_id": "fixture-1",
                "write_roots": []
            }
        });
        let encoded = serde_json::to_vec(&canonical_json_value(&definition)).unwrap();
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-goal-contract-canonical-v1\0");
        hasher.update(&encoded);
        let digest = *hasher.finalize().as_bytes();
        let digest_hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
        definition
            .as_object_mut()
            .unwrap()
            .insert("contract_hash".to_string(), serde_json::json!(digest_hex));
        (serde_json::to_string(&definition).unwrap(), digest)
    }

    fn explicit_execution_binding(contract_json: &str, mission_id: &str) -> serde_json::Value {
        let contract: serde_json::Value = serde_json::from_str(contract_json).unwrap();
        let mut binding = serde_json::json!({
            "schema": "aegis-execution-binding-v1",
            "goal_contract_hash": contract["contract_hash"].clone(),
            "generation": contract["generation"].clone(),
            "target_digest": digest_hex(projection_payload_hash(&contract["target"])),
            "plan_digest": "c".repeat(64),
            "mission_id": mission_id,
            "task_id": 1,
            "attempt_id": 1,
            "owner_id": contract["target"]["owner"].clone(),
            "principal_id": "fixture-agent",
            "evidence_digest": serde_json::Value::Null,
            "completion_digest": serde_json::Value::Null,
        });
        let binding_hash = execution_binding_digest(&binding);
        binding
            .as_object_mut()
            .unwrap()
            .insert("binding_hash".to_string(), serde_json::json!(binding_hash));
        binding
    }

    #[test]
    fn native_goal_contract_wire_accepts_bound_hashed_definition() {
        let (contract_json, contract_hash) = explicit_goal_contract_json();
        let mut mission = mission();
        mission.max_external_attempts = Some(20);
        mission.contract_hash = contract_hash;
        mission.goal_contract_json = Some(contract_json);
        assert!(mission.validate().is_ok());
    }

    #[test]
    fn native_goal_contract_rejects_unbounded_wire_shapes() {
        let (contract_json, _) = explicit_goal_contract_json();
        let mission_for = |mut definition: serde_json::Value| {
            definition.as_object_mut().unwrap().remove("contract_hash");
            let encoded = serde_json::to_vec(&canonical_json_value(&definition)).unwrap();
            let mut hasher = Hasher::new();
            hasher.update(b"aegis-goal-contract-canonical-v1\0");
            hasher.update(&encoded);
            let contract_hash = *hasher.finalize().as_bytes();
            definition.as_object_mut().unwrap().insert(
                "contract_hash".to_string(),
                serde_json::json!(digest_hex(contract_hash)),
            );

            let mut mission = mission();
            mission.objective = definition["objective"].as_str().unwrap().to_string();
            mission.max_external_attempts = Some(20);
            mission.contract_hash = contract_hash;
            mission.goal_contract_json = Some(serde_json::to_string(&definition).unwrap());
            mission
        };

        let mut exact_text: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        exact_text["objective"] = serde_json::json!("x".repeat(MAX_GOAL_CONTRACT_TEXT_BYTES));
        assert!(mission_for(exact_text).validate().is_ok());

        let mut oversized_text: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        oversized_text["objective"] =
            serde_json::json!("x".repeat(MAX_GOAL_CONTRACT_TEXT_BYTES + 1));
        assert!(mission_for(oversized_text).validate().is_err());

        let mut oversized_sequence: serde_json::Value =
            serde_json::from_str(&contract_json).unwrap();
        oversized_sequence["scope"] = serde_json::Value::Array(
            (0..=MAX_GOAL_CONTRACT_SEQUENCE_ITEMS)
                .map(|index| serde_json::json!(format!("scope-{index:03}")))
                .collect(),
        );
        assert!(mission_for(oversized_sequence).validate().is_err());
    }

    #[test]
    fn native_goal_contract_wire_accepts_child_lineage_and_rejects_partial_lineage() {
        let (parent_json, parent_hash) = explicit_goal_contract_json();
        let mut child: serde_json::Value = serde_json::from_str(&parent_json).unwrap();
        child["goal_id"] = serde_json::json!("child-goal-1");
        child["parent_goal_id"] = serde_json::json!("goal-1");
        child["parent_contract_hash"] = serde_json::json!(digest_hex(parent_hash));
        child["evolution_reason"] = serde_json::json!("delegate bounded evidence check");
        let child_object = child.as_object_mut().unwrap();
        child_object.remove("contract_hash");
        let encoded = serde_json::to_vec(&canonical_json_value(&serde_json::Value::Object(
            child_object.clone(),
        )))
        .unwrap();
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-goal-contract-canonical-v1\0");
        hasher.update(&encoded);
        let child_hash = *hasher.finalize().as_bytes();
        child_object.insert(
            "contract_hash".to_string(),
            serde_json::json!(digest_hex(child_hash)),
        );

        let mut child_mission = mission();
        child_mission.max_external_attempts = Some(20);
        child_mission.contract_hash = child_hash;
        child_mission.goal_contract_json = Some(serde_json::to_string(&child).unwrap());
        assert!(child_mission.validate().is_ok());

        child["evolution_reason"] = serde_json::Value::Null;
        let mut partial_lineage = mission();
        partial_lineage.max_external_attempts = Some(20);
        partial_lineage.contract_hash = child_hash;
        partial_lineage.goal_contract_json = Some(serde_json::to_string(&child).unwrap());
        assert!(partial_lineage.validate().is_err());
    }

    #[test]
    fn native_goal_contract_wire_rejects_target_tampering() {
        let (contract_json, contract_hash) = explicit_goal_contract_json();
        let mut tampered: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        tampered["target"]["stable_id"] = serde_json::json!("different-fixture");
        let mut tampered_mission = mission();
        tampered_mission.max_external_attempts = Some(20);
        tampered_mission.contract_hash = contract_hash;
        tampered_mission.goal_contract_json = Some(serde_json::to_string(&tampered).unwrap());
        assert!(tampered_mission.validate().is_err());

        let mut unsupported: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        unsupported["evidence_policy"]["required_record_types"] =
            serde_json::json!(["unsupported"]);
        let mut unsupported_mission = mission();
        unsupported_mission.max_external_attempts = Some(20);
        unsupported_mission.contract_hash = contract_hash;
        unsupported_mission.goal_contract_json = Some(serde_json::to_string(&unsupported).unwrap());
        assert!(unsupported_mission.validate().is_err());

        let mut ambiguous_uri: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        ambiguous_uri["target"]["read_roots"] = serde_json::json!(["fixture://input/../secret"]);
        ambiguous_uri
            .as_object_mut()
            .unwrap()
            .remove("contract_hash");
        let encoded = serde_json::to_vec(&canonical_json_value(&ambiguous_uri)).unwrap();
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-goal-contract-canonical-v1\0");
        hasher.update(&encoded);
        let ambiguous_hash = *hasher.finalize().as_bytes();
        ambiguous_uri.as_object_mut().unwrap().insert(
            "contract_hash".to_string(),
            serde_json::json!(digest_hex(ambiguous_hash)),
        );
        let mut ambiguous_mission = mission();
        ambiguous_mission.max_external_attempts = Some(20);
        ambiguous_mission.contract_hash = ambiguous_hash;
        ambiguous_mission.goal_contract_json = Some(serde_json::to_string(&ambiguous_uri).unwrap());
        assert!(ambiguous_mission.validate().is_err());
    }

    #[test]
    fn native_goal_contract_rejects_noncanonical_external_effect_keys() {
        let (contract_json, _) = explicit_goal_contract_json();
        let mut malformed: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        malformed["target"]["external_side_effects"] =
            serde_json::json!(["fixture.write::external_write::extra"]);
        let object = malformed.as_object_mut().unwrap();
        object.remove("contract_hash");
        let encoded = serde_json::to_vec(&canonical_json_value(&serde_json::Value::Object(
            object.clone(),
        )))
        .unwrap();
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-goal-contract-canonical-v1\0");
        hasher.update(&encoded);
        let contract_hash = *hasher.finalize().as_bytes();
        object.insert(
            "contract_hash".to_string(),
            serde_json::json!(digest_hex(contract_hash)),
        );

        let mut malformed_mission = mission();
        malformed_mission.max_external_attempts = Some(20);
        malformed_mission.contract_hash = contract_hash;
        malformed_mission.goal_contract_json = Some(serde_json::to_string(&malformed).unwrap());
        assert!(malformed_mission.validate().is_err());

        for entry in [
            "https://user:p@pypi.org",
            "https://pypi.org/path",
            "https://pypi.org?query=1",
            "https://*.pypi.org",
            "pypi.org/path",
        ] {
            let mut malformed: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
            malformed["target"]["network_allowlist"] = serde_json::json!([entry]);
            let object = malformed.as_object_mut().unwrap();
            object.remove("contract_hash");
            let encoded = serde_json::to_vec(&canonical_json_value(&serde_json::Value::Object(
                object.clone(),
            )))
            .unwrap();
            let mut hasher = Hasher::new();
            hasher.update(b"aegis-goal-contract-canonical-v1\0");
            hasher.update(&encoded);
            let contract_hash = *hasher.finalize().as_bytes();
            object.insert(
                "contract_hash".to_string(),
                serde_json::json!(digest_hex(contract_hash)),
            );

            let mut malformed_mission = mission();
            malformed_mission.max_external_attempts = Some(20);
            malformed_mission.contract_hash = contract_hash;
            malformed_mission.goal_contract_json = Some(serde_json::to_string(&malformed).unwrap());
            assert!(
                malformed_mission.validate().is_err(),
                "entry {entry} must fail closed"
            );
        }
    }

    #[test]
    fn native_goal_event_binding_rejects_stale_generation() {
        let (contract_json, contract_hash) = explicit_goal_contract_json();
        let mut mission = mission();
        mission.max_external_attempts = Some(20);
        mission.contract_hash = contract_hash;
        mission.goal_contract_json = Some(contract_json.clone());
        let controller = LabController::new(mission).unwrap();
        let contract: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        let target_digest = digest_hex(projection_payload_hash(&contract["target"]));
        assert_eq!(
            target_digest,
            "126fb47163792f9cf0c6ffd0daf18247294283c23d5a74fc3f35fa2046860494"
        );
        let mut payload = serde_json::json!({
            "goal_id": "goal-1",
            "goal_generation": 1,
            "goal_contract_hash": digest_hex(contract_hash),
            "target_digest": target_digest
        });
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap()
                )
                .is_ok()
        );
        payload["goal_generation"] = serde_json::json!(2);
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap()
                )
                .is_err()
        );
    }

    #[test]
    fn native_execution_binding_requires_a_prior_matching_manifest() {
        let (contract_json, contract_hash) = explicit_goal_contract_json();
        let mut explicit_mission = mission();
        explicit_mission.max_external_attempts = Some(20);
        explicit_mission.contract_hash = contract_hash;
        explicit_mission.goal_contract_json = Some(contract_json.clone());
        let mut controller = LabController::new(explicit_mission).unwrap();
        let resource_policy = serde_json::json!({
            "schema": "aegis-execution-cell-resource-policy-v1",
            "cpu_time_limit_ms": 1000,
            "memory_bytes": 64 * 1024 * 1024,
            "process_limit": 1,
            "thread_limit": 1,
            "fd_limit": 256,
            "input_bytes": 1024,
            "output_bytes": 4096,
            "timeout_ms": 5000
        });
        let resource_policy_hash = execution_cell_resource_policy_hash(&resource_policy).unwrap();
        assert_eq!(
            resource_policy_hash,
            "59f2c4be42130952b57cc505f1762be5d2a3fab8c897e6093a4de12ddd6a4fa4"
        );
        let manifest = serde_json::json!([{
            "cell_id": "progress-checkpoint",
            "action_kinds": ["progress_checkpoint"],
            "capabilities": ["state_write"],
            "effect_classes": ["state_write"],
            "trust_levels": ["DEV"],
            "backend_kind": "python-local",
            "resource_policy": resource_policy,
            "resource_policy_hash": resource_policy_hash
        }]);
        let manifest_hash = digest_hex(projection_payload_hash(&manifest));
        let manifest_payload = serde_json::json!({
            "schema": "aegis-execution-cell-manifest-v1",
            "cell_count": 1,
            "manifest": manifest,
            "manifest_hash": manifest_hash
        });
        let manifest_json = serde_json::to_string(&manifest_payload).unwrap();
        let manifest_event = LabEvent::new(
            2,
            1,
            LabEventKind::ExecutionCellManifestRecorded,
            projection_payload_hash(&manifest_payload),
            controller.runtime().events()[0].event_hash,
        );
        controller
            .admit_projection_record(manifest_event, &manifest_json, None)
            .unwrap();

        let contract: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        let mut binding = explicit_execution_binding(&contract_json, "mission-1");
        binding["execution_cell_manifest_hash"] = serde_json::json!(manifest_hash);
        binding["execution_cell_id"] = serde_json::json!("progress-checkpoint");
        binding["execution_action_kind"] = serde_json::json!("progress_checkpoint");
        binding["backend_kind"] = serde_json::json!("python-local");
        binding["resource_policy_hash"] = serde_json::json!(resource_policy_hash);
        let mut unsigned = binding.clone();
        unsigned.as_object_mut().unwrap().remove("binding_hash");
        binding["binding_hash"] = serde_json::json!(execution_binding_digest(&unsigned));
        let mut payload = serde_json::json!({
            "goal_id": contract["goal_id"],
            "goal_generation": contract["generation"],
            "goal_contract_hash": contract["contract_hash"],
            "target_digest": digest_hex(projection_payload_hash(&contract["target"])),
            "execution_cell_manifest_hash": manifest_hash,
            "execution_binding": binding
        });
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap(),
                )
                .is_ok()
        );

        payload["execution_binding"]["execution_action_kind"] = serde_json::json!("tool_call");
        let mut selection_changed = payload["execution_binding"].clone();
        selection_changed
            .as_object_mut()
            .unwrap()
            .remove("binding_hash");
        payload["execution_binding"]["binding_hash"] =
            serde_json::json!(execution_binding_digest(&selection_changed));
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap(),
                )
                .is_err()
        );
        payload["execution_binding"]["execution_action_kind"] =
            serde_json::json!("progress_checkpoint");
        let mut restored_selection = payload["execution_binding"].clone();
        restored_selection
            .as_object_mut()
            .unwrap()
            .remove("binding_hash");
        payload["execution_binding"]["binding_hash"] =
            serde_json::json!(execution_binding_digest(&restored_selection));

        payload["execution_binding"]["resource_policy_hash"] = serde_json::json!("c".repeat(64));
        let mut identity_changed = payload["execution_binding"].clone();
        identity_changed
            .as_object_mut()
            .unwrap()
            .remove("binding_hash");
        payload["execution_binding"]["binding_hash"] =
            serde_json::json!(execution_binding_digest(&identity_changed));
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap(),
                )
                .is_err()
        );
        payload["execution_binding"]["resource_policy_hash"] =
            serde_json::json!(resource_policy_hash);
        let mut restored_identity = payload["execution_binding"].clone();
        restored_identity
            .as_object_mut()
            .unwrap()
            .remove("binding_hash");
        payload["execution_binding"]["binding_hash"] =
            serde_json::json!(execution_binding_digest(&restored_identity));

        payload["execution_binding"]["execution_cell_manifest_hash"] =
            serde_json::json!("f".repeat(64));
        let mut changed = payload["execution_binding"].clone();
        changed.as_object_mut().unwrap().remove("binding_hash");
        payload["execution_binding"]["binding_hash"] =
            serde_json::json!(execution_binding_digest(&changed));
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap(),
                )
                .is_err()
        );

        payload["execution_binding"]["mission_id"] =
            serde_json::json!("m".repeat(MAX_GOAL_CONTRACT_IDENTIFIER_BYTES + 1));
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap(),
                )
                .is_err()
        );

        let mut missing_manifest = LabController::new({
            let mut missing = mission();
            missing.max_external_attempts = Some(20);
            missing.contract_hash = contract_hash;
            missing.goal_contract_json = Some(contract_json);
            missing
        })
        .unwrap();
        let _ = &mut missing_manifest;
        assert!(
            missing_manifest
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap(),
                )
                .is_err()
        );
    }

    #[test]
    fn native_execution_binding_is_strict_immutable_and_snapshot_bound() {
        let (contract_json, contract_hash) = explicit_goal_contract_json();
        let mut mission = mission();
        mission.max_external_attempts = Some(20);
        mission.contract_hash = contract_hash;
        mission.goal_contract_json = Some(contract_json.clone());
        let binding = explicit_execution_binding(&contract_json, "mission-1");
        let contract_hash_hex = digest_hex(contract_hash);
        let contract: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        let target_digest = digest_hex(projection_payload_hash(&contract["target"]));

        let tool_payload = |binding: Option<serde_json::Value>,
                            execution_id: &str,
                            admission_id: &str,
                            parent: [u8; 32],
                            status: &str|
         -> serde_json::Value {
            let mut payload = serde_json::json!({
                "admission_id": admission_id,
                "execution_id": execution_id,
                "tool_name": "fixture.lookup",
                "attempt": 1,
                "lease_id": 1,
                "effect_class": "read_only",
                "actor_role": "actor",
                "expected_observation_schema": "fixture.v1",
                "stop_rule": "single_call",
                "mission_id": "mission-1",
                "replay_parent_hash": digest_hex(parent),
                "input_hash": "a".repeat(64),
                "policy_hash": "b".repeat(64),
                "status": status
            });
            payload["goal_id"] = serde_json::json!("goal-1");
            payload["goal_generation"] = serde_json::json!(1);
            payload["goal_contract_hash"] = serde_json::json!(contract_hash_hex);
            payload["target_digest"] = serde_json::json!(target_digest);
            if let Some(binding) = binding {
                payload["execution_binding"] = binding;
            }
            if status == "SUCCESS" {
                payload["result_hash"] = serde_json::json!("c".repeat(64));
            }
            payload
        };
        let admit = |controller: &mut LabController,
                     sequence: u64,
                     epoch: u64,
                     kind: LabEventKind,
                     payload: serde_json::Value|
         -> Result<(), LabError> {
            let payload_json = serde_json::to_string(&payload).unwrap();
            let previous = controller.runtime().events().last().unwrap().event_hash;
            let event = LabEvent::new(
                sequence,
                epoch,
                kind,
                projection_payload_hash(&payload),
                previous,
            );
            controller.admit_projection_record(event, &payload_json, None)
        };

        let mut controller = LabController::new(mission.clone()).unwrap();
        let first_parent = controller.runtime().events()[0].event_hash;
        let first_payload = tool_payload(
            Some(binding.clone()),
            "tool-1",
            "tool-1-admission",
            first_parent,
            "ADMITTED",
        );
        admit(
            &mut controller,
            2,
            1,
            LabEventKind::ToolExecutionAdmitted,
            first_payload,
        )
        .unwrap();
        assert_eq!(
            controller
                .projection_execution_binding
                .as_ref()
                .unwrap()
                .binding_hash,
            binding["binding_hash"].as_str().unwrap()
        );

        let second_parent = controller.runtime().events().last().unwrap().event_hash;
        let second_payload = tool_payload(
            Some(binding.clone()),
            "tool-1",
            "tool-1-admission",
            second_parent,
            "SUCCESS",
        );
        admit(
            &mut controller,
            3,
            2,
            LabEventKind::ToolExecutionRecorded,
            second_payload,
        )
        .unwrap();
        let before_rejected = controller.snapshot_json().unwrap();

        let mut changed_binding = binding.clone();
        changed_binding["plan_digest"] = serde_json::json!("f".repeat(64));
        let changed_hash = execution_binding_digest(&{
            let mut unsigned = changed_binding.clone();
            unsigned.as_object_mut().unwrap().remove("binding_hash");
            unsigned
        });
        changed_binding["binding_hash"] = serde_json::json!(changed_hash);
        let changed_parent = controller.runtime().events().last().unwrap().event_hash;
        let changed_payload = tool_payload(
            Some(changed_binding),
            "tool-2",
            "tool-2-admission",
            changed_parent,
            "ADMITTED",
        );
        let changed = admit(
            &mut controller,
            4,
            3,
            LabEventKind::ToolExecutionAdmitted,
            changed_payload,
        );
        assert_eq!(changed, Err(LabError::InvalidRecord));
        assert_eq!(controller.snapshot_json().unwrap(), before_rejected);

        let mut tampered_snapshot: serde_json::Value =
            serde_json::from_str(&before_rejected).unwrap();
        tampered_snapshot["projection_payloads"]["2"]["execution_binding"]["plan_digest"] =
            serde_json::json!("f".repeat(64));
        assert!(
            LabController::from_snapshot_json(&serde_json::to_string(&tampered_snapshot).unwrap())
                .is_err()
        );
        let restored = LabController::from_snapshot_json(&before_rejected).unwrap();
        assert_eq!(
            restored.projection_execution_binding,
            controller.projection_execution_binding
        );

        let mut late_binding_controller = LabController::new(mission).unwrap();
        let first_parent = late_binding_controller.runtime().events()[0].event_hash;
        admit(
            &mut late_binding_controller,
            2,
            1,
            LabEventKind::ToolExecutionAdmitted,
            tool_payload(None, "late-1", "late-1-admission", first_parent, "ADMITTED"),
        )
        .unwrap();
        let late_parent = late_binding_controller
            .runtime()
            .events()
            .last()
            .unwrap()
            .event_hash;
        let late_payload = tool_payload(
            Some(binding),
            "late-2",
            "late-2-admission",
            late_parent,
            "ADMITTED",
        );
        let late = admit(
            &mut late_binding_controller,
            3,
            2,
            LabEventKind::ToolExecutionAdmitted,
            late_payload,
        );
        assert_eq!(late, Err(LabError::InvalidRecord));
    }

    #[test]
    fn native_goal_target_allowlists_generic_external_effect_keys() {
        let (parent_json, _) = explicit_goal_contract_json();
        let mut definition: serde_json::Value = serde_json::from_str(&parent_json).unwrap();
        definition["target"]["external_side_effects"] =
            serde_json::json!(["fixture.write::external_write"]);
        definition.as_object_mut().unwrap().remove("contract_hash");
        let encoded = serde_json::to_vec(&canonical_json_value(&definition)).unwrap();
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-goal-contract-canonical-v1\0");
        hasher.update(&encoded);
        let contract_hash = *hasher.finalize().as_bytes();
        let contract_hash_hex: String = contract_hash
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        definition.as_object_mut().unwrap().insert(
            "contract_hash".to_string(),
            serde_json::json!(contract_hash_hex),
        );

        let contract_json = serde_json::to_string(&definition).unwrap();
        let mut mission = mission();
        mission.max_external_attempts = Some(20);
        mission.contract_hash = contract_hash;
        mission.goal_contract_json = Some(contract_json);
        let controller = LabController::new(mission).unwrap();
        let target_digest = digest_hex(projection_payload_hash(&definition["target"]));
        let payload = serde_json::json!({
            "goal_id": "goal-1",
            "goal_generation": 1,
            "goal_contract_hash": contract_hash_hex,
            "target_digest": target_digest,
            "tool_name": "fixture.write",
            "effect_class": "external_write"
        });
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    payload.as_object().unwrap()
                )
                .is_ok()
        );

        let mut rejected = payload;
        rejected["tool_name"] = serde_json::json!("fixture.other_write");
        assert!(
            controller
                .validate_goal_event_binding(
                    LabEventKind::ToolExecutionAdmitted,
                    rejected.as_object().unwrap()
                )
                .is_err()
        );
    }

    #[test]
    fn native_goal_verification_record_requires_typed_receipt() {
        let (contract_json, contract_hash) = explicit_goal_contract_json();
        let mut configured_mission = mission();
        configured_mission.max_external_attempts = Some(20);
        configured_mission.contract_hash = contract_hash;
        configured_mission.goal_contract_json = Some(contract_json.clone());
        let mut controller = LabController::new(configured_mission).unwrap();
        let contract: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        let target_digest = digest_hex(projection_payload_hash(&contract["target"]));
        assert_eq!(
            digest_hex(contract_hash),
            "7c11c990dc9671b4ff16cd039c4885cd68e81ab017bc0999067211daf55cee9f"
        );
        let mut verification = serde_json::json!({
            "schema": "aegis-goal-verification-v1",
            "contract_hash": digest_hex(contract_hash),
            "generation": 1,
            "verifier_id": "independent-verifier",
            "independent": true,
            "predicate_results": [["reproducible-result", true]],
            "evidence_refs": ["source-1"],
            "evidence_complete": true
        });
        let verification_hash = goal_verification_hash(verification.as_object().unwrap());
        assert_eq!(
            digest_hex(verification_hash),
            "43f965d51e2d05394e33b2ca5f13488087d98c008adcf7cedf8dc4f000000c09"
        );
        verification["verification_hash"] = serde_json::json!(digest_hex(verification_hash));
        controller.projection_sources.insert("source-1".to_string());
        let payload = serde_json::json!({
            "record_type": "goal_verification",
            "goal_id": "goal-1",
            "goal_generation": 1,
            "goal_contract_hash": digest_hex(contract_hash),
            "target_digest": target_digest,
            "verification": verification
        });
        assert!(
            controller
                .validate_projection_payload(LabEventKind::ReviewRecorded, &payload)
                .is_ok()
        );
        let mut untrusted = payload.clone();
        untrusted["verification"]["verifier_id"] = serde_json::json!("untrusted-verifier");
        let untrusted_hash = goal_verification_hash(untrusted["verification"].as_object().unwrap());
        untrusted["verification"]["verification_hash"] =
            serde_json::json!(digest_hex(untrusted_hash));
        assert!(
            controller
                .validate_projection_payload(LabEventKind::ReviewRecorded, &untrusted)
                .is_err()
        );
        let mut malformed = payload;
        malformed["verification"]["evidence_refs"] = serde_json::json!([1]);
        assert!(
            controller
                .validate_projection_payload(LabEventKind::ReviewRecorded, &malformed)
                .is_err()
        );
    }

    #[test]
    fn native_goal_verification_accepts_retained_non_source_evidence() {
        let (contract_json, _) = explicit_goal_contract_json();
        let mut contract: serde_json::Value = serde_json::from_str(&contract_json).unwrap();
        contract["acceptance"][0]["evidence_refs"] = serde_json::json!(["claim-1"]);
        let definition = contract.as_object_mut().unwrap();
        definition.remove("contract_hash");
        let encoded = serde_json::to_vec(&canonical_json_value(&serde_json::Value::Object(
            definition.clone(),
        )))
        .unwrap();
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-goal-contract-canonical-v1\0");
        hasher.update(&encoded);
        let contract_hash = *hasher.finalize().as_bytes();
        definition.insert(
            "contract_hash".to_string(),
            serde_json::json!(digest_hex(contract_hash)),
        );
        let mut mission = mission();
        mission.max_external_attempts = Some(20);
        mission.contract_hash = contract_hash;
        mission.goal_contract_json = Some(serde_json::to_string(&contract).unwrap());
        let mut controller = LabController::new(mission).unwrap();
        controller.projection_sources.insert("source-1".to_string());
        controller.projection_claims.insert("claim-1".to_string());
        let target_digest = digest_hex(projection_payload_hash(&contract["target"]));
        let mut verification = serde_json::json!({
            "schema": "aegis-goal-verification-v1",
            "contract_hash": digest_hex(contract_hash),
            "generation": 1,
            "verifier_id": "independent-verifier",
            "independent": true,
            "predicate_results": [["reproducible-result", true]],
            "evidence_refs": ["claim-1", "source-1"],
            "evidence_complete": true
        });
        let verification_hash = goal_verification_hash(verification.as_object().unwrap());
        verification["verification_hash"] = serde_json::json!(digest_hex(verification_hash));
        let payload = serde_json::json!({
            "record_type": "goal_verification",
            "goal_id": "goal-1",
            "goal_generation": 1,
            "goal_contract_hash": digest_hex(contract_hash),
            "target_digest": target_digest,
            "verification": verification
        });
        assert!(
            controller
                .validate_projection_payload(LabEventKind::ReviewRecorded, &payload)
                .is_ok()
        );
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
    fn native_projection_record_binds_execution_cell_manifest() {
        let mut controller = LabController::new(mission()).unwrap();
        let manifest = serde_json::json!([{
            "cell_id": "progress-checkpoint",
            "action_kinds": ["progress_checkpoint"],
            "capabilities": ["state_write"],
            "effect_classes": ["state_write"],
            "trust_levels": ["DEV"]
        }]);
        let payload = serde_json::json!({
            "schema": "aegis-execution-cell-manifest-v1",
            "cell_count": 1,
            "manifest": manifest.clone(),
            "manifest_hash": digest_hex(projection_payload_hash(&manifest)),
        });
        let payload_json = serde_json::to_string(&payload).unwrap();
        let event = LabEvent::new(
            2,
            1,
            LabEventKind::ExecutionCellManifestRecorded,
            projection_payload_hash(&payload),
            controller.runtime().events()[0].event_hash,
        );
        controller
            .admit_projection_record(event, &payload_json, None)
            .unwrap();
        assert_eq!(controller.runtime().events().len(), 2);
        let snapshot = controller.snapshot_json().unwrap();
        assert!(LabController::from_snapshot_json(&snapshot).is_ok());

        let unknown_manifest = serde_json::json!([{
            "cell_id": "progress-checkpoint",
            "action_kinds": ["progress_checkpoint"],
            "capabilities": ["state_write"],
            "effect_classes": ["state_write"],
            "trust_levels": ["DEV"],
            "unknown": true
        }]);
        let unknown_payload = serde_json::json!({
            "schema": "aegis-execution-cell-manifest-v1",
            "cell_count": 1,
            "manifest": unknown_manifest.clone(),
            "manifest_hash": digest_hex(projection_payload_hash(&unknown_manifest)),
        });
        let mut unknown_controller = LabController::new(mission()).unwrap();
        let unknown_event = LabEvent::new(
            2,
            1,
            LabEventKind::ExecutionCellManifestRecorded,
            projection_payload_hash(&unknown_payload),
            unknown_controller.runtime().events()[0].event_hash,
        );
        assert_eq!(
            unknown_controller.admit_projection_record(
                unknown_event,
                &serde_json::to_string(&unknown_payload).unwrap(),
                None,
            ),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(unknown_controller.runtime().events().len(), 1);

        let mut tampered_controller = LabController::new(mission()).unwrap();
        let tampered_payload = serde_json::json!({
            "schema": "aegis-execution-cell-manifest-v1",
            "cell_count": 0,
            "manifest": [],
            "manifest_hash": "0".repeat(64),
        });
        let tampered_event = LabEvent::new(
            2,
            1,
            LabEventKind::ExecutionCellManifestRecorded,
            projection_payload_hash(&tampered_payload),
            tampered_controller.runtime().events()[0].event_hash,
        );
        assert_eq!(
            tampered_controller.admit_projection_record(
                tampered_event,
                &serde_json::to_string(&tampered_payload).unwrap(),
                None,
            ),
            Err(LabError::InvalidRecord)
        );
        assert_eq!(tampered_controller.runtime().events().len(), 1);
    }

    #[test]
    fn native_projection_record_enforces_global_external_attempt_budget() {
        let mut bounded_mission = mission();
        bounded_mission.max_external_attempts = Some(1);
        let mut controller = LabController::new(bounded_mission).unwrap();
        let payload = serde_json::json!({
            "admission_id": "tool-1-admission",
            "execution_id": "tool-1",
            "tool_name": "fixture.lookup",
            "attempt": 1,
            "lease_id": 1,
            "effect_class": "read_only",
            "actor_role": "actor",
            "expected_observation_schema": "fixture.v1",
            "stop_rule": "single_call",
            "mission_id": "mission-1",
            "replay_parent_hash": digest_hex(controller.runtime().events()[0].event_hash),
            "input_hash": "a".repeat(64),
            "policy_hash": "b".repeat(64),
            "status": "ADMITTED"
        });
        let payload_json = serde_json::to_string(&payload).unwrap();
        let first_event = LabEvent::new(
            2,
            1,
            LabEventKind::ToolExecutionAdmitted,
            projection_payload_hash(&payload),
            controller.runtime().events()[0].event_hash,
        );
        controller
            .admit_projection_record(first_event, &payload_json, None)
            .unwrap();
        let before_rejected = controller.snapshot_json().unwrap();

        let second_payload = serde_json::json!({
            "admission_id": "tool-2-admission",
            "execution_id": "tool-2",
            "tool_name": "fixture.lookup",
            "attempt": 1,
            "lease_id": 2,
            "effect_class": "read_only",
            "actor_role": "actor",
            "expected_observation_schema": "fixture.v1",
            "stop_rule": "single_call",
            "mission_id": "mission-1",
            "replay_parent_hash": digest_hex(controller.runtime().events().last().unwrap().event_hash),
            "input_hash": "c".repeat(64),
            "policy_hash": "d".repeat(64),
            "status": "ADMITTED"
        });
        let second_payload_json = serde_json::to_string(&second_payload).unwrap();
        let second_event = LabEvent::new(
            3,
            2,
            LabEventKind::ToolExecutionAdmitted,
            projection_payload_hash(&second_payload),
            controller.runtime().events().last().unwrap().event_hash,
        );
        assert_eq!(
            controller.admit_projection_record(second_event, &second_payload_json, None),
            Err(LabError::InvalidTransition)
        );
        assert_eq!(controller.snapshot_json().unwrap(), before_rejected);
        assert!(LabController::from_snapshot_json(&before_rejected).is_ok());
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
        assert_ne!(manifest.manifest_hash, manifest.legacy_manifest_hash());
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
        assert!(
            crate::replay::RunEventSegmentArchive::verify_lab_events_against_archive(
                directory.path(),
                manifest.run_id,
                runtime.events(),
            )
            .unwrap()
        );

        let mut tampered = runtime.events().to_vec();
        tampered[1].payload_hash = [99; 32];
        tampered[1].event_hash = canonical_hash(&event_without_hash(&tampered[1]));
        for index in 2..tampered.len() {
            tampered[index].previous_event_hash = tampered[index - 1].event_hash;
            tampered[index].event_hash = canonical_hash(&event_without_hash(&tampered[index]));
        }
        assert!(verify_event_chain(&tampered));
        assert!(
            !crate::replay::RunEventSegmentArchive::verify_lab_events_against_archive(
                directory.path(),
                manifest.run_id,
                &tampered,
            )
            .unwrap()
        );
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
