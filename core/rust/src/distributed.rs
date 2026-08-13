use crate::policy::SideEffectClass;
use crate::replay::{RunEventLedger, RunId};
use blake3::Hasher;
use std::collections::BTreeMap;

pub type WorkId = u128;
pub type WorkerId = u128;
pub type TaskId = u128;

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WorkerRole {
    WasmSandbox = 1,
    BrowserWitness = 2,
    ComputerWitness = 3,
    InferenceBackend = 4,
    RetrievalIndexer = 5,
    BenchmarkRunner = 6,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ClusterPartitionState {
    WriterReachable = 1,
    WriterPartitioned = 2,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DistributedRuntimeError {
    InvalidEnvelope,
    InvalidLease,
    LeaseAlreadyHeld,
    LeaseExpired,
    WorkerMismatch,
    PolicyWindowMismatch,
    ArtifactMismatch,
    EvidenceContractMismatch,
    PartitionPausesSideEffects,
    DirectWorkerCommitRejected,
    ReplayAppendRejected,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClusterWorkEnvelope {
    pub run_id: RunId,
    pub work_id: WorkId,
    pub task_id: TaskId,
    pub worker_role: WorkerRole,
    pub attempt: u32,
    pub input_artifact_hashes: Vec<[u8; 32]>,
    pub evidence_contract_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub side_effect_class: SideEffectClass,
    pub deadline_ms: u64,
    pub idempotency_key: [u8; 32],
    pub envelope_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CandidateArtifactRef {
    pub run_id: RunId,
    pub work_id: WorkId,
    pub worker_id: WorkerId,
    pub artifact_hash: [u8; 32],
    pub artifact_kind_hash: [u8; 32],
    pub byte_len: u64,
    pub evidence_contract_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub worker_result_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WorkLease {
    pub run_id: RunId,
    pub work_id: WorkId,
    pub worker_id: WorkerId,
    pub worker_role: WorkerRole,
    pub attempt: u32,
    pub acquired_at_ms: u64,
    pub expires_at_ms: u64,
    pub idempotency_key: [u8; 32],
    pub lease_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClusterCandidateAdmissionProof {
    pub run_id: RunId,
    pub work_id: WorkId,
    pub task_id: TaskId,
    pub worker_id: WorkerId,
    pub attempt: u32,
    pub envelope_hash: [u8; 32],
    pub lease_hash: [u8; 32],
    pub candidate_artifact_hash: [u8; 32],
    pub worker_result_hash: [u8; 32],
    pub idempotency_key: [u8; 32],
    pub replay_event_hash: [u8; 32],
    pub admission_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SingleWriterAdmission {
    Accepted(ClusterCandidateAdmissionProof),
    Duplicate(ClusterCandidateAdmissionProof),
}

#[derive(Clone, Debug)]
pub struct WorkLeaseTable {
    leases: BTreeMap<[u8; 32], WorkLease>,
}

#[derive(Clone, Debug)]
pub struct SingleWriterRunLog {
    run_id: RunId,
    committed: BTreeMap<[u8; 32], ClusterCandidateAdmissionProof>,
}

impl ClusterWorkEnvelope {
    pub fn new(
        run_id: RunId,
        work_id: WorkId,
        task_id: TaskId,
        worker_role: WorkerRole,
        attempt: u32,
        input_artifact_hashes: Vec<[u8; 32]>,
        evidence_contract_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        side_effect_class: SideEffectClass,
        deadline_ms: u64,
    ) -> Result<Self, DistributedRuntimeError> {
        if run_id == 0
            || work_id == 0
            || task_id == 0
            || attempt == 0
            || deadline_ms == 0
            || input_artifact_hashes.is_empty()
            || input_artifact_hashes.iter().any(|hash| !nonzero_hash(hash))
            || !nonzero_hash(&evidence_contract_hash)
            || !nonzero_hash(&policy_window_hash)
        {
            return Err(DistributedRuntimeError::InvalidEnvelope);
        }
        let idempotency_key = cluster_work_idempotency_key(
            run_id,
            work_id,
            task_id,
            worker_role,
            attempt,
            &input_artifact_hashes,
            evidence_contract_hash,
            policy_window_hash,
            side_effect_class,
        );
        let envelope_hash = cluster_work_envelope_hash(
            run_id,
            work_id,
            task_id,
            worker_role,
            attempt,
            &input_artifact_hashes,
            evidence_contract_hash,
            policy_window_hash,
            side_effect_class,
            deadline_ms,
            idempotency_key,
        );
        Ok(Self {
            run_id,
            work_id,
            task_id,
            worker_role,
            attempt,
            input_artifact_hashes,
            evidence_contract_hash,
            policy_window_hash,
            side_effect_class,
            deadline_ms,
            idempotency_key,
            envelope_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.run_id != 0
            && self.work_id != 0
            && self.task_id != 0
            && self.attempt != 0
            && self.deadline_ms != 0
            && !self.input_artifact_hashes.is_empty()
            && self.input_artifact_hashes.iter().all(nonzero_hash)
            && nonzero_hash(&self.evidence_contract_hash)
            && nonzero_hash(&self.policy_window_hash)
            && self.idempotency_key
                == cluster_work_idempotency_key(
                    self.run_id,
                    self.work_id,
                    self.task_id,
                    self.worker_role,
                    self.attempt,
                    &self.input_artifact_hashes,
                    self.evidence_contract_hash,
                    self.policy_window_hash,
                    self.side_effect_class,
                )
            && self.envelope_hash
                == cluster_work_envelope_hash(
                    self.run_id,
                    self.work_id,
                    self.task_id,
                    self.worker_role,
                    self.attempt,
                    &self.input_artifact_hashes,
                    self.evidence_contract_hash,
                    self.policy_window_hash,
                    self.side_effect_class,
                    self.deadline_ms,
                    self.idempotency_key,
                )
    }

    pub fn pauses_on_partition(&self) -> bool {
        matches!(
            self.side_effect_class,
            SideEffectClass::ExternalWrite
                | SideEffectClass::Destructive
                | SideEffectClass::FinancialLegal
        )
    }
}

impl CandidateArtifactRef {
    pub fn new(
        envelope: &ClusterWorkEnvelope,
        worker_id: WorkerId,
        artifact_hash: [u8; 32],
        artifact_kind_hash: [u8; 32],
        byte_len: u64,
    ) -> Result<Self, DistributedRuntimeError> {
        if !envelope.is_valid()
            || worker_id == 0
            || !nonzero_hash(&artifact_hash)
            || !nonzero_hash(&artifact_kind_hash)
            || byte_len == 0
        {
            return Err(DistributedRuntimeError::InvalidEnvelope);
        }
        let worker_result_hash = candidate_artifact_ref_hash(
            envelope.run_id,
            envelope.work_id,
            worker_id,
            artifact_hash,
            artifact_kind_hash,
            byte_len,
            envelope.evidence_contract_hash,
            envelope.policy_window_hash,
        );
        Ok(Self {
            run_id: envelope.run_id,
            work_id: envelope.work_id,
            worker_id,
            artifact_hash,
            artifact_kind_hash,
            byte_len,
            evidence_contract_hash: envelope.evidence_contract_hash,
            policy_window_hash: envelope.policy_window_hash,
            worker_result_hash,
        })
    }

    pub fn is_valid_for(&self, envelope: &ClusterWorkEnvelope) -> bool {
        self.run_id == envelope.run_id
            && self.work_id == envelope.work_id
            && self.worker_id != 0
            && self.byte_len > 0
            && self.evidence_contract_hash == envelope.evidence_contract_hash
            && self.policy_window_hash == envelope.policy_window_hash
            && nonzero_hash(&self.artifact_hash)
            && nonzero_hash(&self.artifact_kind_hash)
            && self.worker_result_hash
                == candidate_artifact_ref_hash(
                    self.run_id,
                    self.work_id,
                    self.worker_id,
                    self.artifact_hash,
                    self.artifact_kind_hash,
                    self.byte_len,
                    self.evidence_contract_hash,
                    self.policy_window_hash,
                )
    }
}

impl WorkLeaseTable {
    pub fn new() -> Self {
        Self {
            leases: BTreeMap::new(),
        }
    }

    pub fn acquire(
        &mut self,
        envelope: &ClusterWorkEnvelope,
        worker_id: WorkerId,
        now_ms: u64,
        lease_ttl_ms: u64,
    ) -> Result<WorkLease, DistributedRuntimeError> {
        if !envelope.is_valid()
            || worker_id == 0
            || lease_ttl_ms == 0
            || now_ms >= envelope.deadline_ms
        {
            return Err(DistributedRuntimeError::InvalidLease);
        }
        if let Some(existing) = self.leases.get(&envelope.idempotency_key) {
            if existing.expires_at_ms > now_ms {
                return Err(DistributedRuntimeError::LeaseAlreadyHeld);
            }
        }
        let expires_at_ms = now_ms
            .saturating_add(lease_ttl_ms)
            .min(envelope.deadline_ms);
        if expires_at_ms <= now_ms {
            return Err(DistributedRuntimeError::InvalidLease);
        }
        let lease_hash = work_lease_hash(
            envelope.run_id,
            envelope.work_id,
            worker_id,
            envelope.worker_role,
            envelope.attempt,
            now_ms,
            expires_at_ms,
            envelope.idempotency_key,
        );
        let lease = WorkLease {
            run_id: envelope.run_id,
            work_id: envelope.work_id,
            worker_id,
            worker_role: envelope.worker_role,
            attempt: envelope.attempt,
            acquired_at_ms: now_ms,
            expires_at_ms,
            idempotency_key: envelope.idempotency_key,
            lease_hash,
        };
        self.leases.insert(envelope.idempotency_key, lease.clone());
        Ok(lease)
    }
}

impl Default for WorkLeaseTable {
    fn default() -> Self {
        Self::new()
    }
}

impl WorkLease {
    pub fn is_valid_for(&self, envelope: &ClusterWorkEnvelope, now_ms: u64) -> bool {
        self.run_id == envelope.run_id
            && self.work_id == envelope.work_id
            && self.worker_role == envelope.worker_role
            && self.attempt == envelope.attempt
            && self.idempotency_key == envelope.idempotency_key
            && self.acquired_at_ms < self.expires_at_ms
            && now_ms < self.expires_at_ms
            && self.lease_hash
                == work_lease_hash(
                    self.run_id,
                    self.work_id,
                    self.worker_id,
                    self.worker_role,
                    self.attempt,
                    self.acquired_at_ms,
                    self.expires_at_ms,
                    self.idempotency_key,
                )
    }
}

impl SingleWriterRunLog {
    pub fn new(run_id: RunId) -> Self {
        Self {
            run_id,
            committed: BTreeMap::new(),
        }
    }

    pub fn commit_candidate(
        &mut self,
        ledger: &mut RunEventLedger,
        envelope: &ClusterWorkEnvelope,
        lease: &WorkLease,
        candidate: &CandidateArtifactRef,
        partition_state: ClusterPartitionState,
        now_ms: u64,
    ) -> Result<SingleWriterAdmission, DistributedRuntimeError> {
        if envelope.run_id != self.run_id || ledger.run_id != self.run_id || !envelope.is_valid() {
            return Err(DistributedRuntimeError::InvalidEnvelope);
        }
        if partition_state == ClusterPartitionState::WriterPartitioned
            && envelope.pauses_on_partition()
        {
            return Err(DistributedRuntimeError::PartitionPausesSideEffects);
        }
        if !lease.is_valid_for(envelope, now_ms) {
            return Err(DistributedRuntimeError::LeaseExpired);
        }
        if lease.worker_id != candidate.worker_id {
            return Err(DistributedRuntimeError::WorkerMismatch);
        }
        if candidate.evidence_contract_hash != envelope.evidence_contract_hash {
            return Err(DistributedRuntimeError::EvidenceContractMismatch);
        }
        if candidate.policy_window_hash != envelope.policy_window_hash {
            return Err(DistributedRuntimeError::PolicyWindowMismatch);
        }
        if !candidate.is_valid_for(envelope) {
            return Err(DistributedRuntimeError::ArtifactMismatch);
        }
        let commit_key = cluster_commit_key(envelope.idempotency_key, candidate.artifact_hash);
        if let Some(existing) = self.committed.get(&commit_key) {
            return Ok(SingleWriterAdmission::Duplicate(existing.clone()));
        }
        let appended = ledger
            .append_cluster_candidate_accepted(
                envelope.work_id,
                candidate.worker_result_hash,
                cluster_candidate_replay_binding_hash(envelope, lease, candidate),
            )
            .map_err(|_| DistributedRuntimeError::ReplayAppendRejected)?;
        let admission_hash = cluster_candidate_admission_hash(
            envelope.envelope_hash,
            lease.lease_hash,
            candidate.artifact_hash,
            candidate.worker_result_hash,
            envelope.idempotency_key,
            appended.event_hash,
        );
        let proof = ClusterCandidateAdmissionProof {
            run_id: self.run_id,
            work_id: envelope.work_id,
            task_id: envelope.task_id,
            worker_id: candidate.worker_id,
            attempt: envelope.attempt,
            envelope_hash: envelope.envelope_hash,
            lease_hash: lease.lease_hash,
            candidate_artifact_hash: candidate.artifact_hash,
            worker_result_hash: candidate.worker_result_hash,
            idempotency_key: envelope.idempotency_key,
            replay_event_hash: appended.event_hash,
            admission_hash,
        };
        self.committed.insert(commit_key, proof.clone());
        Ok(SingleWriterAdmission::Accepted(proof))
    }

    pub fn reject_direct_worker_commit(
        &self,
        _worker_id: WorkerId,
    ) -> Result<(), DistributedRuntimeError> {
        Err(DistributedRuntimeError::DirectWorkerCommitRejected)
    }

    pub fn committed_len(&self) -> usize {
        self.committed.len()
    }
}

impl ClusterCandidateAdmissionProof {
    pub fn is_valid_for(
        &self,
        envelope: &ClusterWorkEnvelope,
        lease: &WorkLease,
        candidate: &CandidateArtifactRef,
    ) -> bool {
        self.run_id == envelope.run_id
            && self.work_id == envelope.work_id
            && self.task_id == envelope.task_id
            && self.worker_id == candidate.worker_id
            && self.attempt == envelope.attempt
            && self.envelope_hash == envelope.envelope_hash
            && self.lease_hash == lease.lease_hash
            && self.candidate_artifact_hash == candidate.artifact_hash
            && self.worker_result_hash == candidate.worker_result_hash
            && self.idempotency_key == envelope.idempotency_key
            && nonzero_hash(&self.replay_event_hash)
            && self.admission_hash
                == cluster_candidate_admission_hash(
                    self.envelope_hash,
                    self.lease_hash,
                    self.candidate_artifact_hash,
                    self.worker_result_hash,
                    self.idempotency_key,
                    self.replay_event_hash,
                )
    }
}

pub fn cluster_candidate_replay_binding_hash(
    envelope: &ClusterWorkEnvelope,
    lease: &WorkLease,
    candidate: &CandidateArtifactRef,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cluster-candidate-replay-binding-v1");
    update_hash(&mut hasher, &envelope.envelope_hash);
    update_hash(&mut hasher, &lease.lease_hash);
    update_hash(&mut hasher, &candidate.worker_result_hash);
    update_hash(&mut hasher, &envelope.idempotency_key);
    update_hash(&mut hasher, &candidate.artifact_hash);
    *hasher.finalize().as_bytes()
}

pub fn cluster_commit_key(idempotency_key: [u8; 32], artifact_hash: [u8; 32]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cluster-commit-key-v1");
    update_hash(&mut hasher, &idempotency_key);
    update_hash(&mut hasher, &artifact_hash);
    *hasher.finalize().as_bytes()
}

fn cluster_work_idempotency_key(
    run_id: RunId,
    work_id: WorkId,
    task_id: TaskId,
    worker_role: WorkerRole,
    attempt: u32,
    input_artifact_hashes: &[[u8; 32]],
    evidence_contract_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    side_effect_class: SideEffectClass,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cluster-work-idempotency-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, work_id);
    update_u128(&mut hasher, task_id);
    update_u8(&mut hasher, worker_role as u8);
    update_u32(&mut hasher, attempt);
    update_u64(&mut hasher, input_artifact_hashes.len() as u64);
    for hash in input_artifact_hashes {
        update_hash(&mut hasher, hash);
    }
    update_hash(&mut hasher, &evidence_contract_hash);
    update_hash(&mut hasher, &policy_window_hash);
    update_u8(&mut hasher, side_effect_class_code(side_effect_class));
    *hasher.finalize().as_bytes()
}

fn cluster_work_envelope_hash(
    run_id: RunId,
    work_id: WorkId,
    task_id: TaskId,
    worker_role: WorkerRole,
    attempt: u32,
    input_artifact_hashes: &[[u8; 32]],
    evidence_contract_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    side_effect_class: SideEffectClass,
    deadline_ms: u64,
    idempotency_key: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cluster-work-envelope-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, work_id);
    update_u128(&mut hasher, task_id);
    update_u8(&mut hasher, worker_role as u8);
    update_u32(&mut hasher, attempt);
    update_u64(&mut hasher, input_artifact_hashes.len() as u64);
    for hash in input_artifact_hashes {
        update_hash(&mut hasher, hash);
    }
    update_hash(&mut hasher, &evidence_contract_hash);
    update_hash(&mut hasher, &policy_window_hash);
    update_u8(&mut hasher, side_effect_class_code(side_effect_class));
    update_u64(&mut hasher, deadline_ms);
    update_hash(&mut hasher, &idempotency_key);
    *hasher.finalize().as_bytes()
}

fn candidate_artifact_ref_hash(
    run_id: RunId,
    work_id: WorkId,
    worker_id: WorkerId,
    artifact_hash: [u8; 32],
    artifact_kind_hash: [u8; 32],
    byte_len: u64,
    evidence_contract_hash: [u8; 32],
    policy_window_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-candidate-artifact-ref-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, work_id);
    update_u128(&mut hasher, worker_id);
    update_hash(&mut hasher, &artifact_hash);
    update_hash(&mut hasher, &artifact_kind_hash);
    update_u64(&mut hasher, byte_len);
    update_hash(&mut hasher, &evidence_contract_hash);
    update_hash(&mut hasher, &policy_window_hash);
    *hasher.finalize().as_bytes()
}

fn work_lease_hash(
    run_id: RunId,
    work_id: WorkId,
    worker_id: WorkerId,
    worker_role: WorkerRole,
    attempt: u32,
    acquired_at_ms: u64,
    expires_at_ms: u64,
    idempotency_key: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-work-lease-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, work_id);
    update_u128(&mut hasher, worker_id);
    update_u8(&mut hasher, worker_role as u8);
    update_u32(&mut hasher, attempt);
    update_u64(&mut hasher, acquired_at_ms);
    update_u64(&mut hasher, expires_at_ms);
    update_hash(&mut hasher, &idempotency_key);
    *hasher.finalize().as_bytes()
}

fn cluster_candidate_admission_hash(
    envelope_hash: [u8; 32],
    lease_hash: [u8; 32],
    artifact_hash: [u8; 32],
    worker_result_hash: [u8; 32],
    idempotency_key: [u8; 32],
    replay_event_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cluster-candidate-admission-v1");
    update_hash(&mut hasher, &envelope_hash);
    update_hash(&mut hasher, &lease_hash);
    update_hash(&mut hasher, &artifact_hash);
    update_hash(&mut hasher, &worker_result_hash);
    update_hash(&mut hasher, &idempotency_key);
    update_hash(&mut hasher, &replay_event_hash);
    *hasher.finalize().as_bytes()
}

fn side_effect_class_code(side_effect_class: SideEffectClass) -> u8 {
    match side_effect_class {
        SideEffectClass::None => 1,
        SideEffectClass::LocalReversible => 2,
        SideEffectClass::ExternalRead => 3,
        SideEffectClass::ExternalWrite => 4,
        SideEffectClass::Destructive => 5,
        SideEffectClass::FinancialLegal => 6,
    }
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn update_hash(hasher: &mut Hasher, hash: &[u8; 32]) {
    hasher.update(hash);
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

pub fn test_hash(label: &str) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-distributed-test-hash-v1");
    hasher.update(label.as_bytes());
    *hasher.finalize().as_bytes()
}
