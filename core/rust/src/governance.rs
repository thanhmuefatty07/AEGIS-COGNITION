use blake3::Hasher;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GovernanceError {
    InvalidLaneMap,
    MissingWave,
    DuplicateWave,
    InvalidBenchmarkProfile,
    InvalidDependencyAudit,
    MissingRequiredDependency,
    InvalidRateLimitBudget,
    NoProviderAvailable,
    InvalidE2EScenario,
    MissingE2EStage,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GovernanceLane {
    Runtime,
    Evidence,
    Security,
    Evaluation,
    Product,
}

impl GovernanceLane {
    fn code(self) -> u8 {
        match self {
            GovernanceLane::Runtime => 1,
            GovernanceLane::Evidence => 2,
            GovernanceLane::Security => 3,
            GovernanceLane::Evaluation => 4,
            GovernanceLane::Product => 5,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BenchmarkGateProfileKind {
    Innovation,
    Release,
}

impl BenchmarkGateProfileKind {
    fn code(self) -> u8 {
        match self {
            BenchmarkGateProfileKind::Innovation => 1,
            BenchmarkGateProfileKind::Release => 2,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DependencyRiskClass {
    SandboxRuntime,
    ColumnarIpc,
    CryptoHash,
    PythonFfi,
    BenchmarkHarness,
}

impl DependencyRiskClass {
    fn code(self) -> u8 {
        match self {
            DependencyRiskClass::SandboxRuntime => 1,
            DependencyRiskClass::ColumnarIpc => 2,
            DependencyRiskClass::CryptoHash => 3,
            DependencyRiskClass::PythonFfi => 4,
            DependencyRiskClass::BenchmarkHarness => 5,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum E2EStageKind {
    GoalIntake,
    TaskPlan,
    ContextPack,
    TypedToolIr,
    PolicyProof,
    ToolExecution,
    EvidenceIngest,
    ReplayAppend,
    CheckpointSeal,
    MemoryCandidate,
    NextAction,
}

impl E2EStageKind {
    fn code(self) -> u8 {
        match self {
            E2EStageKind::GoalIntake => 1,
            E2EStageKind::TaskPlan => 2,
            E2EStageKind::ContextPack => 3,
            E2EStageKind::TypedToolIr => 4,
            E2EStageKind::PolicyProof => 5,
            E2EStageKind::ToolExecution => 6,
            E2EStageKind::EvidenceIngest => 7,
            E2EStageKind::ReplayAppend => 8,
            E2EStageKind::CheckpointSeal => 9,
            E2EStageKind::MemoryCandidate => 10,
            E2EStageKind::NextAction => 11,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WaveLaneAssignment {
    pub wave_id: u8,
    pub lane: GovernanceLane,
    pub priority_rank: u8,
    pub requirement_hash: [u8; 32],
    pub assignment_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GovernanceLaneMap {
    pub covered_wave_mask: u32,
    pub assignment_count: u32,
    pub lane_count: u32,
    pub lane_load_hash: [u8; 32],
    pub assignment_list_hash: [u8; 32],
    pub map_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct BenchmarkGateProfile {
    pub kind: BenchmarkGateProfileKind,
    pub threshold_multiplier_ppm: u32,
    pub requires_e2e_gate: bool,
    pub allows_stale_microbench_manifest: bool,
    pub profile_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DependencyAuditRecord {
    pub name: &'static str,
    pub version_req: &'static str,
    pub risk_class: DependencyRiskClass,
    pub source_hash: [u8; 32],
    pub license_hash: [u8; 32],
    pub audit_evidence_hash: [u8; 32],
    pub record_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DependencyAuditManifest {
    pub required_dependency_count: u32,
    pub audited_dependency_count: u32,
    pub dependency_set_hash: [u8; 32],
    pub record_list_hash: [u8; 32],
    pub manifest_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProviderRateLimitBudget {
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub remaining_requests: u32,
    pub remaining_tokens: u32,
    pub reset_epoch_ms: u64,
    pub budget_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProviderRateLimitMitigationProof {
    pub request_hash: [u8; 32],
    pub required_tokens: u32,
    pub selected_provider_hash: Option<[u8; 32]>,
    pub throttled_provider_hashes: Vec<[u8; 32]>,
    pub budget_set_hash: [u8; 32],
    pub route_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct E2EStageEvidence {
    pub stage: E2EStageKind,
    pub artifact_hash: [u8; 32],
    pub replay_event_hash: [u8; 32],
    pub verifier_hash: [u8; 32],
    pub stage_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct E2EScenarioProof {
    pub scenario_id: u128,
    pub stage_mask: u32,
    pub stage_count: u32,
    pub ordered_stage_hash: [u8; 32],
    pub scenario_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

pub const REQUIRED_WAVE_MASK_18: u32 = (1u32 << 18) - 1;
pub const REQUIRED_E2E_STAGE_MASK: u32 = (1u32 << 11) - 1;

impl WaveLaneAssignment {
    pub fn new(
        wave_id: u8,
        lane: GovernanceLane,
        priority_rank: u8,
        requirement_hash: [u8; 32],
    ) -> Result<Self, GovernanceError> {
        if wave_id == 0 || wave_id > 18 || priority_rank == 0 || requirement_hash == [0; 32] {
            return Err(GovernanceError::InvalidLaneMap);
        }
        let assignment_hash =
            wave_lane_assignment_hash(wave_id, lane, priority_rank, requirement_hash);
        Ok(Self {
            wave_id,
            lane,
            priority_rank,
            requirement_hash,
            assignment_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.wave_id > 0
            && self.wave_id <= 18
            && self.priority_rank > 0
            && self.requirement_hash != [0; 32]
            && self.assignment_hash
                == wave_lane_assignment_hash(
                    self.wave_id,
                    self.lane,
                    self.priority_rank,
                    self.requirement_hash,
                )
    }
}

impl GovernanceLaneMap {
    pub fn build(assignments: &[WaveLaneAssignment]) -> Result<Self, GovernanceError> {
        if assignments.len() != 18 {
            return Err(GovernanceError::MissingWave);
        }
        let mut ordered = assignments.to_vec();
        ordered.sort_by_key(|assignment| assignment.wave_id);
        let mut covered_wave_mask = 0u32;
        let mut lane_seen = [false; 5];
        let mut lane_loads = [0u8; 5];
        let mut expected_wave = 1u8;
        for assignment in &ordered {
            if !assignment.is_valid() {
                return Err(GovernanceError::InvalidLaneMap);
            }
            if assignment.wave_id != expected_wave {
                return if assignment.wave_id < expected_wave {
                    Err(GovernanceError::DuplicateWave)
                } else {
                    Err(GovernanceError::MissingWave)
                };
            }
            let lane_index = assignment.lane.code() as usize - 1;
            lane_seen[lane_index] = true;
            lane_loads[lane_index] = lane_loads[lane_index].saturating_add(1);
            covered_wave_mask |= 1u32 << (assignment.wave_id - 1);
            expected_wave = expected_wave.saturating_add(1);
        }
        if covered_wave_mask != REQUIRED_WAVE_MASK_18 {
            return Err(GovernanceError::MissingWave);
        }
        let lane_count = lane_seen.iter().filter(|seen| **seen).count() as u32;
        if lane_count != 5 {
            return Err(GovernanceError::InvalidLaneMap);
        }
        let lane_load_hash = governance_lane_load_hash(&lane_loads);
        let assignment_hashes: Vec<_> = ordered
            .iter()
            .map(|assignment| assignment.assignment_hash)
            .collect();
        let assignment_list_hash =
            governance_hash_list(b"aegis-governance-assignment-list-v1", &assignment_hashes);
        let map_hash = governance_lane_map_hash(
            covered_wave_mask,
            ordered.len() as u32,
            lane_count,
            lane_load_hash,
            assignment_list_hash,
        );
        Ok(Self {
            covered_wave_mask,
            assignment_count: ordered.len() as u32,
            lane_count,
            lane_load_hash,
            assignment_list_hash,
            map_hash,
        })
    }
}

impl BenchmarkGateProfile {
    pub fn innovation() -> Self {
        Self::build(BenchmarkGateProfileKind::Innovation, 3_000_000, true, true)
            .expect("valid innovation benchmark profile")
    }

    pub fn release() -> Self {
        Self::build(BenchmarkGateProfileKind::Release, 1_000_000, true, false)
            .expect("valid release benchmark profile")
    }

    pub fn build(
        kind: BenchmarkGateProfileKind,
        threshold_multiplier_ppm: u32,
        requires_e2e_gate: bool,
        allows_stale_microbench_manifest: bool,
    ) -> Result<Self, GovernanceError> {
        let valid = match kind {
            BenchmarkGateProfileKind::Innovation => threshold_multiplier_ppm >= 2_000_000,
            BenchmarkGateProfileKind::Release => threshold_multiplier_ppm == 1_000_000,
        };
        if !valid || !requires_e2e_gate {
            return Err(GovernanceError::InvalidBenchmarkProfile);
        }
        let profile_hash = benchmark_gate_profile_hash(
            kind,
            threshold_multiplier_ppm,
            requires_e2e_gate,
            allows_stale_microbench_manifest,
        );
        Ok(Self {
            kind,
            threshold_multiplier_ppm,
            requires_e2e_gate,
            allows_stale_microbench_manifest,
            profile_hash,
        })
    }

    pub fn threshold_for(&self, release_threshold_ns: u64) -> u64 {
        let scaled = (release_threshold_ns as u128)
            .saturating_mul(self.threshold_multiplier_ppm as u128)
            / 1_000_000u128;
        scaled.min(u64::MAX as u128) as u64
    }
}

impl DependencyAuditRecord {
    pub fn new(
        name: &'static str,
        version_req: &'static str,
        risk_class: DependencyRiskClass,
        source_hash: [u8; 32],
        license_hash: [u8; 32],
        audit_evidence_hash: [u8; 32],
    ) -> Result<Self, GovernanceError> {
        if name.is_empty()
            || version_req.is_empty()
            || source_hash == [0; 32]
            || license_hash == [0; 32]
            || audit_evidence_hash == [0; 32]
        {
            return Err(GovernanceError::InvalidDependencyAudit);
        }
        let record_hash = dependency_audit_record_hash(
            name,
            version_req,
            risk_class,
            source_hash,
            license_hash,
            audit_evidence_hash,
        );
        Ok(Self {
            name,
            version_req,
            risk_class,
            source_hash,
            license_hash,
            audit_evidence_hash,
            record_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        !self.name.is_empty()
            && !self.version_req.is_empty()
            && self.source_hash != [0; 32]
            && self.license_hash != [0; 32]
            && self.audit_evidence_hash != [0; 32]
            && self.record_hash
                == dependency_audit_record_hash(
                    self.name,
                    self.version_req,
                    self.risk_class,
                    self.source_hash,
                    self.license_hash,
                    self.audit_evidence_hash,
                )
    }
}

impl DependencyAuditManifest {
    pub fn build(records: &[DependencyAuditRecord]) -> Result<Self, GovernanceError> {
        if records.iter().any(|record| !record.is_valid()) {
            return Err(GovernanceError::InvalidDependencyAudit);
        }
        let required = ["arrow", "arrow-buffer", "blake3", "pyo3", "wasmtime"];
        for required_name in required {
            if !records.iter().any(|record| record.name == required_name) {
                return Err(GovernanceError::MissingRequiredDependency);
            }
        }
        let mut ordered = records.to_vec();
        ordered.sort_by(|left, right| left.name.cmp(right.name));
        let record_hashes: Vec<_> = ordered.iter().map(|record| record.record_hash).collect();
        let dependency_set_hash = dependency_set_hash(&ordered);
        let record_list_hash =
            governance_hash_list(b"aegis-dependency-audit-record-list-v1", &record_hashes);
        let manifest_hash = dependency_audit_manifest_hash(
            required.len() as u32,
            ordered.len() as u32,
            dependency_set_hash,
            record_list_hash,
        );
        Ok(Self {
            required_dependency_count: required.len() as u32,
            audited_dependency_count: ordered.len() as u32,
            dependency_set_hash,
            record_list_hash,
            manifest_hash,
        })
    }
}

impl ProviderRateLimitBudget {
    pub fn new(
        provider_id: &'static str,
        model_name: &'static str,
        remaining_requests: u32,
        remaining_tokens: u32,
        reset_epoch_ms: u64,
    ) -> Result<Self, GovernanceError> {
        if provider_id.is_empty() || model_name.is_empty() {
            return Err(GovernanceError::InvalidRateLimitBudget);
        }
        let budget_hash = provider_rate_limit_budget_hash(
            provider_id,
            model_name,
            remaining_requests,
            remaining_tokens,
            reset_epoch_ms,
        );
        Ok(Self {
            provider_id,
            model_name,
            remaining_requests,
            remaining_tokens,
            reset_epoch_ms,
            budget_hash,
        })
    }

    pub fn can_serve(&self, required_tokens: u32) -> bool {
        self.remaining_requests > 0 && self.remaining_tokens >= required_tokens
    }

    pub fn is_valid(&self) -> bool {
        !self.provider_id.is_empty()
            && !self.model_name.is_empty()
            && self.budget_hash
                == provider_rate_limit_budget_hash(
                    self.provider_id,
                    self.model_name,
                    self.remaining_requests,
                    self.remaining_tokens,
                    self.reset_epoch_ms,
                )
    }
}

impl ProviderRateLimitMitigationProof {
    pub fn build(
        request_hash: [u8; 32],
        required_tokens: u32,
        budgets: &[ProviderRateLimitBudget],
    ) -> Result<Self, GovernanceError> {
        if request_hash == [0; 32] || required_tokens == 0 || budgets.is_empty() {
            return Err(GovernanceError::InvalidRateLimitBudget);
        }
        if budgets.iter().any(|budget| !budget.is_valid()) {
            return Err(GovernanceError::InvalidRateLimitBudget);
        }
        let mut ordered = budgets.to_vec();
        ordered.sort_by(|left, right| {
            left.provider_id
                .cmp(right.provider_id)
                .then_with(|| left.model_name.cmp(right.model_name))
        });
        let selected = ordered
            .iter()
            .find(|budget| budget.can_serve(required_tokens));
        let throttled_provider_hashes: Vec<_> = ordered
            .iter()
            .filter(|budget| !budget.can_serve(required_tokens))
            .map(|budget| budget.budget_hash)
            .collect();
        let selected_provider_hash = selected.map(|budget| budget.budget_hash);
        let budget_hashes: Vec<_> = ordered.iter().map(|budget| budget.budget_hash).collect();
        let budget_set_hash =
            governance_hash_list(b"aegis-provider-rate-limit-budget-set-v1", &budget_hashes);
        let throttled_hash = governance_hash_list(
            b"aegis-provider-rate-limit-throttled-list-v1",
            &throttled_provider_hashes,
        );
        let route_hash = provider_rate_limit_route_hash(
            request_hash,
            required_tokens,
            selected_provider_hash,
            throttled_hash,
            budget_set_hash,
        );
        let proof_hash = provider_rate_limit_mitigation_proof_hash(
            request_hash,
            required_tokens,
            selected_provider_hash,
            throttled_hash,
            budget_set_hash,
            route_hash,
        );
        if selected_provider_hash.is_none() {
            return Err(GovernanceError::NoProviderAvailable);
        }
        Ok(Self {
            request_hash,
            required_tokens,
            selected_provider_hash,
            throttled_provider_hashes,
            budget_set_hash,
            route_hash,
            proof_hash,
        })
    }
}

impl E2EStageEvidence {
    pub fn new(
        stage: E2EStageKind,
        artifact_hash: [u8; 32],
        replay_event_hash: [u8; 32],
        verifier_hash: [u8; 32],
    ) -> Result<Self, GovernanceError> {
        if artifact_hash == [0; 32] || replay_event_hash == [0; 32] || verifier_hash == [0; 32] {
            return Err(GovernanceError::InvalidE2EScenario);
        }
        let stage_hash = e2e_stage_hash(stage, artifact_hash, replay_event_hash, verifier_hash);
        Ok(Self {
            stage,
            artifact_hash,
            replay_event_hash,
            verifier_hash,
            stage_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.artifact_hash != [0; 32]
            && self.replay_event_hash != [0; 32]
            && self.verifier_hash != [0; 32]
            && self.stage_hash
                == e2e_stage_hash(
                    self.stage,
                    self.artifact_hash,
                    self.replay_event_hash,
                    self.verifier_hash,
                )
    }
}

impl E2EScenarioProof {
    pub fn build(scenario_id: u128, stages: &[E2EStageEvidence]) -> Result<Self, GovernanceError> {
        if scenario_id == 0 || stages.len() != 11 {
            return Err(GovernanceError::MissingE2EStage);
        }
        let mut expected_code = 1u8;
        let mut stage_mask = 0u32;
        let mut stage_hashes = Vec::with_capacity(stages.len());
        for stage in stages {
            if !stage.is_valid() || stage.stage.code() != expected_code {
                return Err(GovernanceError::InvalidE2EScenario);
            }
            stage_mask |= 1u32 << (stage.stage.code() - 1);
            stage_hashes.push(stage.stage_hash);
            expected_code = expected_code.saturating_add(1);
        }
        if stage_mask != REQUIRED_E2E_STAGE_MASK {
            return Err(GovernanceError::MissingE2EStage);
        }
        let ordered_stage_hash =
            governance_hash_list(b"aegis-e2e-ordered-stage-list-v1", &stage_hashes);
        let scenario_hash = e2e_scenario_hash(scenario_id, stage_mask, ordered_stage_hash);
        let proof_hash = e2e_scenario_proof_hash(
            scenario_id,
            stage_mask,
            stages.len() as u32,
            ordered_stage_hash,
            scenario_hash,
        );
        Ok(Self {
            scenario_id,
            stage_mask,
            stage_count: stages.len() as u32,
            ordered_stage_hash,
            scenario_hash,
            proof_hash,
        })
    }
}

pub fn governance_fixture_hash(label: &str, value: u32) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-governance-fixture-v1");
    update_str(&mut hasher, label);
    update_u32(&mut hasher, value);
    hasher.finalize().into()
}

pub fn wave_lane_assignment_hash(
    wave_id: u8,
    lane: GovernanceLane,
    priority_rank: u8,
    requirement_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-wave-lane-assignment-v1");
    hasher.update(&[wave_id, lane.code(), priority_rank]);
    hasher.update(&requirement_hash);
    hasher.finalize().into()
}

pub fn governance_lane_load_hash(lane_loads: &[u8; 5]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-governance-lane-load-v1");
    hasher.update(lane_loads);
    hasher.finalize().into()
}

pub fn governance_lane_map_hash(
    covered_wave_mask: u32,
    assignment_count: u32,
    lane_count: u32,
    lane_load_hash: [u8; 32],
    assignment_list_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-governance-lane-map-v1");
    update_u32(&mut hasher, covered_wave_mask);
    update_u32(&mut hasher, assignment_count);
    update_u32(&mut hasher, lane_count);
    hasher.update(&lane_load_hash);
    hasher.update(&assignment_list_hash);
    hasher.finalize().into()
}

pub fn benchmark_gate_profile_hash(
    kind: BenchmarkGateProfileKind,
    threshold_multiplier_ppm: u32,
    requires_e2e_gate: bool,
    allows_stale_microbench_manifest: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-benchmark-gate-profile-v1");
    hasher.update(&[kind.code()]);
    update_u32(&mut hasher, threshold_multiplier_ppm);
    update_bool(&mut hasher, requires_e2e_gate);
    update_bool(&mut hasher, allows_stale_microbench_manifest);
    hasher.finalize().into()
}

pub fn dependency_audit_record_hash(
    name: &str,
    version_req: &str,
    risk_class: DependencyRiskClass,
    source_hash: [u8; 32],
    license_hash: [u8; 32],
    audit_evidence_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-dependency-audit-record-v1");
    update_str(&mut hasher, name);
    update_str(&mut hasher, version_req);
    hasher.update(&[risk_class.code()]);
    hasher.update(&source_hash);
    hasher.update(&license_hash);
    hasher.update(&audit_evidence_hash);
    hasher.finalize().into()
}

pub fn dependency_set_hash(records: &[DependencyAuditRecord]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-dependency-set-v1");
    update_u64(&mut hasher, records.len().min(u64::MAX as usize) as u64);
    for record in records {
        update_str(&mut hasher, record.name);
        update_str(&mut hasher, record.version_req);
        hasher.update(&[record.risk_class.code()]);
    }
    hasher.finalize().into()
}

pub fn dependency_audit_manifest_hash(
    required_dependency_count: u32,
    audited_dependency_count: u32,
    dependency_set_hash: [u8; 32],
    record_list_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-dependency-audit-manifest-v1");
    update_u32(&mut hasher, required_dependency_count);
    update_u32(&mut hasher, audited_dependency_count);
    hasher.update(&dependency_set_hash);
    hasher.update(&record_list_hash);
    hasher.finalize().into()
}

pub fn provider_rate_limit_budget_hash(
    provider_id: &str,
    model_name: &str,
    remaining_requests: u32,
    remaining_tokens: u32,
    reset_epoch_ms: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-rate-limit-budget-v1");
    update_str(&mut hasher, provider_id);
    update_str(&mut hasher, model_name);
    update_u32(&mut hasher, remaining_requests);
    update_u32(&mut hasher, remaining_tokens);
    update_u64(&mut hasher, reset_epoch_ms);
    hasher.finalize().into()
}

pub fn provider_rate_limit_route_hash(
    request_hash: [u8; 32],
    required_tokens: u32,
    selected_provider_hash: Option<[u8; 32]>,
    throttled_list_hash: [u8; 32],
    budget_set_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-rate-limit-route-v1");
    hasher.update(&request_hash);
    update_u32(&mut hasher, required_tokens);
    update_optional_hash(&mut hasher, selected_provider_hash);
    hasher.update(&throttled_list_hash);
    hasher.update(&budget_set_hash);
    hasher.finalize().into()
}

pub fn provider_rate_limit_mitigation_proof_hash(
    request_hash: [u8; 32],
    required_tokens: u32,
    selected_provider_hash: Option<[u8; 32]>,
    throttled_list_hash: [u8; 32],
    budget_set_hash: [u8; 32],
    route_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-rate-limit-mitigation-proof-v1");
    hasher.update(&request_hash);
    update_u32(&mut hasher, required_tokens);
    update_optional_hash(&mut hasher, selected_provider_hash);
    hasher.update(&throttled_list_hash);
    hasher.update(&budget_set_hash);
    hasher.update(&route_hash);
    hasher.finalize().into()
}

pub fn e2e_stage_hash(
    stage: E2EStageKind,
    artifact_hash: [u8; 32],
    replay_event_hash: [u8; 32],
    verifier_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-e2e-stage-evidence-v1");
    hasher.update(&[stage.code()]);
    hasher.update(&artifact_hash);
    hasher.update(&replay_event_hash);
    hasher.update(&verifier_hash);
    hasher.finalize().into()
}

pub fn e2e_scenario_hash(
    scenario_id: u128,
    stage_mask: u32,
    ordered_stage_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-e2e-scenario-v1");
    update_u128(&mut hasher, scenario_id);
    update_u32(&mut hasher, stage_mask);
    hasher.update(&ordered_stage_hash);
    hasher.finalize().into()
}

pub fn e2e_scenario_proof_hash(
    scenario_id: u128,
    stage_mask: u32,
    stage_count: u32,
    ordered_stage_hash: [u8; 32],
    scenario_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-e2e-scenario-proof-v1");
    update_u128(&mut hasher, scenario_id);
    update_u32(&mut hasher, stage_mask);
    update_u32(&mut hasher, stage_count);
    hasher.update(&ordered_stage_hash);
    hasher.update(&scenario_hash);
    hasher.finalize().into()
}

fn governance_hash_list(domain: &[u8], hashes: &[[u8; 32]]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(domain);
    update_u64(&mut hasher, hashes.len().min(u64::MAX as usize) as u64);
    for hash in hashes {
        hasher.update(hash);
    }
    hasher.finalize().into()
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

fn update_bool(hasher: &mut Hasher, value: bool) {
    hasher.update(&[u8::from(value)]);
}

fn update_str(hasher: &mut Hasher, value: &str) {
    update_u64(hasher, value.len().min(u64::MAX as usize) as u64);
    hasher.update(value.as_bytes());
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_u32(hasher: &mut Hasher, value: u32) {
    hasher.update(&value.to_le_bytes());
}
