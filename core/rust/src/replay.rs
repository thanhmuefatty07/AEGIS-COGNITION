#![allow(clippy::items_after_test_module)]

use crate::browser_witness::{BrowserObservationPacket, BrowserOpsBenchVerificationProof};
use crate::context::{
    AgenticEvidenceSdkRunHandoffBinding, ContextFoldRecord, ContextPackCandidateProof,
};
use crate::evidence_index::{
    AgenticEvidenceExecutionRecord, AgenticEvidenceProgram, AgenticEvidenceSdkRun,
    ColdVectorExpansionReplayRecord,
};
use crate::goal_intake::GoalIntakeProof;
use crate::hot_engine::ShadowSealBatchReceipt;
use crate::llm::ProviderRouteAdmissionProof;
use crate::policy::HarnessBenchScorecard;
use crate::skill_registry::{AdmittedSkillRecord, SkillAdmissionRecord};
use crate::task_ledger::TaskSelectionProof;
use crate::tool_gateway::ToolMemoryCommitProof;
use arrow::array::{Array, ArrayRef, BinaryArray, UInt8Array, UInt64Array};
use arrow::datatypes::{DataType, Field, Schema, SchemaRef};
use arrow::ipc::reader::{StreamDecoder, StreamReader};
use arrow::ipc::writer::StreamWriter;
use arrow::ipc::{MessageHeader, root_as_message};
use arrow::record_batch::RecordBatch;
use arrow_buffer::Buffer;
use blake3::Hasher;
use memmap2::{Mmap, MmapOptions};
use parking_lot::RwLock;
use serde::{Serialize, Serializer};
use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::mem::size_of;
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::sync::{Arc, OnceLock};

pub type EventId = u64;
pub type RunId = u128;
pub type SubjectId = u128;

const RUN_EVENT_SEGMENT_COMMIT_MAGIC: &[u8; 8] = b"AEGSCM1\0";
const RUN_EVENT_SEGMENT_COMMIT_VERSION: u64 = 1;
pub const RUN_EVENT_SEGMENT_MANIFEST_SCHEMA: &str = "aegis-run-event-segment-manifest-v1";
pub const RUN_EVENT_SEGMENT_MANIFEST_VERSION: u32 = 1;
const RUN_EVENT_SEGMENT_COMMIT_HASH_OFFSET: usize = 268;
const RUN_EVENT_SEGMENT_COMMIT_BYTES: usize = RUN_EVENT_SEGMENT_COMMIT_HASH_OFFSET + 32;
const RUN_EVENT_SEGMENT_CACHE_MAX_ENTRIES: usize = 256;
const ARROW_STREAM_FILE_EVIDENCE_READ_LIMIT_BYTES: u64 = 256 * 1024;
const MAX_EVENTS_PER_SEGMENT: usize = 65_536;

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RunEventKind {
    MissionCompiled,
    ContextPackBuilt,
    LLMResponseReceived,
    ToolCallRequested,
    ToolCallCompleted,
    CheckpointSealed,
    ConfigChanged,
    WorkCancelled,
    PolicyDecisionRecorded,
    ApprovalTokenRecorded,
    OperatorReviewArtifactRecorded,
    ColdVectorExpansionRecorded,
    CircuitBreakerTripped,
    ContextFoldRecorded,
    MemoryCommitRecorded,
    ContextPackCandidateProofRecorded,
    TaskSelectionProofRecorded,
    BrowserObservationPacketRecorded,
    GoalIntakeRecorded,
    AgenticEvidenceExecutionRecorded,
    SkillAdmissionRecorded,
    BrowserOpsBenchVerificationRecorded,
    ClusterCandidateAccepted,
    ShadowSealRecorded,
    LabEventRecorded,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunEvent {
    pub event_id: EventId,
    pub run_id: RunId,
    pub kind: RunEventKind,
    pub subject_id: SubjectId,
    pub primary_hash: [u8; 32],
    pub secondary_hash: Option<[u8; 32]>,
    pub previous_event_hash: [u8; 32],
    pub event_hash: [u8; 32],
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ToolExecutorKind {
    Wasmtime = 1,
    Browser = 2,
    Chrome = 3,
    Computer = 4,
    Host = 5,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ToolExecutionStatus {
    Succeeded = 1,
    Failed = 2,
    Blocked = 3,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionEvidence {
    pub typed_tool_ir_hash: [u8; 32],
    pub policy_proof_trace_hash: [u8; 32],
    pub tool_output_hash: [u8; 32],
    pub physical_evidence_hash: [u8; 32],
    pub executor_kind: ToolExecutorKind,
    pub status: ToolExecutionStatus,
    pub evidence_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReplayLedgerError {
    InvalidEvent,
    MissingPriorContextPack,
    MissingPriorGoalIntake,
    MissingPriorLlmResponse,
    MissingPriorToolCall,
    MissingPriorPolicyDecision,
    HashChainMismatch,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunEventLedger {
    pub run_id: RunId,
    events: Vec<RunEvent>,
    has_context_pack_built: bool,
    has_goal_intake_recorded: bool,
    has_llm_response_received: bool,
    has_pending_tool_call: bool,
    has_policy_decision_recorded: bool,
    has_tool_completion_recorded: bool,
    has_browser_observation_packet_recorded: bool,
    last_tool_call_id: SubjectId,
    last_tool_call_event_id: EventId,
    last_tool_ir_hash: [u8; 32],
    last_tool_execution_evidence_hash: [u8; 32],
    last_policy_event_id: EventId,
    last_policy_proof_trace_hash: [u8; 32],
    last_policy_tool_ir_hash: [u8; 32],
    last_browser_observation_packet_hash: [u8; 32],
}

pub struct ArrowRunEventStream {
    path: PathBuf,
    schema: SchemaRef,
    writer: Option<StreamWriter<File>>,
}

pub struct SegmentedArrowAuditStream {
    directory: PathBuf,
    run_id: RunId,
    max_events_per_segment: usize,
    pending_events: Vec<RunEvent>,
    entries: Vec<RunEventSegmentEntry>,
    previous_segment_hash: [u8; 32],
    last_event_hash: [u8; 32],
    next_event_id: EventId,
    has_context_pack_built: bool,
    has_goal_intake_recorded: bool,
    has_llm_response_received: bool,
    has_pending_tool_call: bool,
    has_policy_decision_recorded: bool,
    has_tool_completion_recorded: bool,
    last_tool_call_id: SubjectId,
    last_tool_call_event_id: EventId,
    last_tool_ir_hash: [u8; 32],
    last_tool_execution_evidence_hash: [u8; 32],
    last_policy_event_id: EventId,
    last_policy_proof_trace_hash: [u8; 32],
    last_policy_tool_ir_hash: [u8; 32],
    writer_lock_path: PathBuf,
    writer_lock: Option<File>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RunEventSegmentEntry {
    pub segment_id: u64,
    pub start_event_id: EventId,
    pub end_event_id: EventId,
    pub event_count: u64,
    pub first_event_hash: [u8; 32],
    pub last_event_hash: [u8; 32],
    pub segment_hash: [u8; 32],
    pub previous_segment_hash: [u8; 32],
    pub arrow_schema_hash: [u8; 32],
    pub arrow_file_bytes: u64,
    pub arrow_file_hash: [u8; 32],
    pub segment_commit_hash: [u8; 32],
    pub staged_temp_file_used: bool,
    pub temp_file_synced_before_publish: bool,
    pub publish_completed: bool,
    pub parent_directory_sync_attempted: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RunEventSegmentPublishEvidence {
    staged_temp_file_used: bool,
    temp_file_synced_before_publish: bool,
    publish_completed: bool,
    parent_directory_sync_attempted: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RunEventSegmentCommitProof {
    run_id: RunId,
    segment_id: u64,
    start_event_id: EventId,
    end_event_id: EventId,
    event_count: u64,
    arrow_schema_hash: [u8; 32],
    arrow_file_bytes: u64,
    arrow_file_hash: [u8; 32],
    first_event_hash: [u8; 32],
    last_event_hash: [u8; 32],
    segment_hash: [u8; 32],
    previous_segment_hash: [u8; 32],
    publish_evidence: RunEventSegmentPublishEvidence,
    commit_hash: [u8; 32],
}

impl RunEventSegmentPublishEvidence {
    fn synced_temp_publish(parent_directory_sync_attempted: bool) -> Self {
        Self {
            staged_temp_file_used: true,
            temp_file_synced_before_publish: true,
            publish_completed: true,
            parent_directory_sync_attempted,
        }
    }
}

impl RunEventSegmentCommitProof {
    fn from_entry(run_id: RunId, entry: &RunEventSegmentEntry) -> Self {
        let mut proof = Self {
            run_id,
            segment_id: entry.segment_id,
            start_event_id: entry.start_event_id,
            end_event_id: entry.end_event_id,
            event_count: entry.event_count,
            arrow_schema_hash: entry.arrow_schema_hash,
            arrow_file_bytes: entry.arrow_file_bytes,
            arrow_file_hash: entry.arrow_file_hash,
            first_event_hash: entry.first_event_hash,
            last_event_hash: entry.last_event_hash,
            segment_hash: entry.segment_hash,
            previous_segment_hash: entry.previous_segment_hash,
            publish_evidence: entry.publish_evidence(),
            commit_hash: [0; 32],
        };
        proof.commit_hash = proof.compute_hash();
        proof
    }

    fn read_sidecar(path: &Path) -> Result<Self, &'static str> {
        let mut file =
            File::open(path).map_err(|_| "failed to open run event segment commit sidecar")?;
        let file_bytes = file
            .metadata()
            .map_err(|_| "failed to stat run event segment commit sidecar")?
            .len();
        if file_bytes != RUN_EVENT_SEGMENT_COMMIT_BYTES as u64 {
            return Err("invalid run event segment commit sidecar size");
        }
        let mut bytes = [0u8; RUN_EVENT_SEGMENT_COMMIT_BYTES];
        file.read_exact(&mut bytes)
            .map_err(|_| "failed to read run event segment commit sidecar")?;
        Self::decode(&bytes)
    }

    fn write_sidecar(
        path: &Path,
        run_id: RunId,
        entry: &RunEventSegmentEntry,
    ) -> Result<(), &'static str> {
        if path.exists() {
            return Err("run event segment commit sidecar already exists");
        }
        let proof = Self::from_entry(run_id, entry);
        if !proof.binds_entry(entry) {
            return Err("invalid run event segment commit proof");
        }
        let payload = proof.encode();
        write_synced_artifact(path, &payload)?;
        let recovered = Self::read_sidecar(path)?;
        if recovered == proof && recovered.binds_entry(entry) {
            Ok(())
        } else {
            Err("run event segment commit sidecar verification failed")
        }
    }

    fn decode(bytes: &[u8]) -> Result<Self, &'static str> {
        if bytes.len() != RUN_EVENT_SEGMENT_COMMIT_BYTES {
            return Err("invalid run event segment commit proof length");
        }
        if &bytes[0..8] != RUN_EVENT_SEGMENT_COMMIT_MAGIC {
            return Err("invalid run event segment commit proof magic");
        }
        let version = read_u64_le(bytes, 8)?;
        if version != RUN_EVENT_SEGMENT_COMMIT_VERSION {
            return Err("invalid run event segment commit proof version");
        }
        let staged_temp_file_used = decode_bool_flag(bytes[264])?;
        let temp_file_synced_before_publish = decode_bool_flag(bytes[265])?;
        let publish_completed = decode_bool_flag(bytes[266])?;
        let parent_directory_sync_attempted = decode_bool_flag(bytes[267])?;
        let proof = Self {
            run_id: read_u128_le(bytes, 16)?,
            segment_id: read_u64_le(bytes, 32)?,
            start_event_id: read_u64_le(bytes, 40)?,
            end_event_id: read_u64_le(bytes, 48)?,
            event_count: read_u64_le(bytes, 56)?,
            arrow_schema_hash: hash_from_slice(&bytes[64..96])?,
            arrow_file_bytes: read_u64_le(bytes, 96)?,
            arrow_file_hash: hash_from_slice(&bytes[104..136])?,
            first_event_hash: hash_from_slice(&bytes[136..168])?,
            last_event_hash: hash_from_slice(&bytes[168..200])?,
            segment_hash: hash_from_slice(&bytes[200..232])?,
            previous_segment_hash: hash_from_slice(&bytes[232..264])?,
            publish_evidence: RunEventSegmentPublishEvidence {
                staged_temp_file_used,
                temp_file_synced_before_publish,
                publish_completed,
                parent_directory_sync_attempted,
            },
            commit_hash: hash_from_slice(&bytes[RUN_EVENT_SEGMENT_COMMIT_HASH_OFFSET..])?,
        };
        if proof.commit_hash != proof.compute_hash()
            || !proof.has_valid_fields()
            || !nonzero_hash(&proof.commit_hash)
        {
            return Err("invalid run event segment commit proof hash");
        }
        Ok(proof)
    }

    fn encode(&self) -> [u8; RUN_EVENT_SEGMENT_COMMIT_BYTES] {
        let mut bytes = [0u8; RUN_EVENT_SEGMENT_COMMIT_BYTES];
        bytes[0..8].copy_from_slice(RUN_EVENT_SEGMENT_COMMIT_MAGIC);
        bytes[8..16].copy_from_slice(&RUN_EVENT_SEGMENT_COMMIT_VERSION.to_le_bytes());
        bytes[16..32].copy_from_slice(&self.run_id.to_le_bytes());
        bytes[32..40].copy_from_slice(&self.segment_id.to_le_bytes());
        bytes[40..48].copy_from_slice(&self.start_event_id.to_le_bytes());
        bytes[48..56].copy_from_slice(&self.end_event_id.to_le_bytes());
        bytes[56..64].copy_from_slice(&self.event_count.to_le_bytes());
        bytes[64..96].copy_from_slice(&self.arrow_schema_hash);
        bytes[96..104].copy_from_slice(&self.arrow_file_bytes.to_le_bytes());
        bytes[104..136].copy_from_slice(&self.arrow_file_hash);
        bytes[136..168].copy_from_slice(&self.first_event_hash);
        bytes[168..200].copy_from_slice(&self.last_event_hash);
        bytes[200..232].copy_from_slice(&self.segment_hash);
        bytes[232..264].copy_from_slice(&self.previous_segment_hash);
        bytes[264] = u8::from(self.publish_evidence.staged_temp_file_used);
        bytes[265] = u8::from(self.publish_evidence.temp_file_synced_before_publish);
        bytes[266] = u8::from(self.publish_evidence.publish_completed);
        bytes[267] = u8::from(self.publish_evidence.parent_directory_sync_attempted);
        bytes[RUN_EVENT_SEGMENT_COMMIT_HASH_OFFSET..].copy_from_slice(&self.compute_hash());
        bytes
    }

    fn binds_entry(&self, entry: &RunEventSegmentEntry) -> bool {
        self.binds_entry_fields(entry) && self.is_valid()
    }

    fn binds_entry_fields(&self, entry: &RunEventSegmentEntry) -> bool {
        self.segment_id == entry.segment_id
            && self.start_event_id == entry.start_event_id
            && self.end_event_id == entry.end_event_id
            && self.event_count == entry.event_count
            && self.arrow_schema_hash == entry.arrow_schema_hash
            && self.arrow_file_bytes == entry.arrow_file_bytes
            && self.arrow_file_hash == entry.arrow_file_hash
            && self.first_event_hash == entry.first_event_hash
            && self.last_event_hash == entry.last_event_hash
            && self.segment_hash == entry.segment_hash
            && self.previous_segment_hash == entry.previous_segment_hash
            && self.publish_evidence == entry.publish_evidence()
            && self.commit_hash == entry.segment_commit_hash
            && self.has_valid_fields()
    }

    fn is_valid(&self) -> bool {
        self.has_valid_fields()
            && self.commit_hash == self.compute_hash()
            && nonzero_hash(&self.commit_hash)
    }

    fn has_valid_fields(&self) -> bool {
        self.run_id != 0
            && self.segment_id > 0
            && self.start_event_id > 0
            && self.start_event_id <= self.end_event_id
            && self.event_count == self.end_event_id - self.start_event_id + 1
            && self.arrow_schema_hash == ArrowRunEventStream::schema_hash()
            && self.arrow_file_bytes > 0
            && nonzero_hash(&self.arrow_file_hash)
            && nonzero_hash(&self.first_event_hash)
            && nonzero_hash(&self.last_event_hash)
            && nonzero_hash(&self.segment_hash)
            && self.publish_evidence.staged_temp_file_used
            && self.publish_evidence.temp_file_synced_before_publish
            && self.publish_evidence.publish_completed
            && self.publish_evidence.parent_directory_sync_attempted
    }

    fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-run-event-segment-commit-v1");
        update_u128(&mut hasher, self.run_id);
        update_u64(&mut hasher, self.segment_id);
        update_u64(&mut hasher, self.start_event_id);
        update_u64(&mut hasher, self.end_event_id);
        update_u64(&mut hasher, self.event_count);
        hasher.update(&self.arrow_schema_hash);
        update_u64(&mut hasher, self.arrow_file_bytes);
        hasher.update(&self.arrow_file_hash);
        hasher.update(&self.first_event_hash);
        hasher.update(&self.last_event_hash);
        hasher.update(&self.segment_hash);
        hasher.update(&self.previous_segment_hash);
        update_u8(
            &mut hasher,
            u8::from(self.publish_evidence.staged_temp_file_used),
        );
        update_u8(
            &mut hasher,
            u8::from(self.publish_evidence.temp_file_synced_before_publish),
        );
        update_u8(
            &mut hasher,
            u8::from(self.publish_evidence.publish_completed),
        );
        update_u8(
            &mut hasher,
            u8::from(self.publish_evidence.parent_directory_sync_attempted),
        );
        *hasher.finalize().as_bytes()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunEventSegmentManifest {
    pub run_id: RunId,
    pub entries: Vec<RunEventSegmentEntry>,
    pub manifest_hash: [u8; 32],
}

impl Serialize for RunEventSegmentManifest {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        #[derive(Serialize)]
        struct ManifestView<'a> {
            schema: &'static str,
            version: u32,
            run_id: RunId,
            entries: &'a [RunEventSegmentEntry],
            manifest_hash: [u8; 32],
        }

        ManifestView {
            schema: RUN_EVENT_SEGMENT_MANIFEST_SCHEMA,
            version: RUN_EVENT_SEGMENT_MANIFEST_VERSION,
            run_id: self.run_id,
            entries: &self.entries,
            manifest_hash: self.manifest_hash,
        }
        .serialize(serializer)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SegmentedArrowAuditProof {
    pub run_id: RunId,
    pub segment_count: usize,
    pub event_count: usize,
    pub total_arrow_file_bytes: u64,
    pub mmap_buffer_count: usize,
    pub first_event_id: EventId,
    pub last_event_id: EventId,
    pub manifest_hash: [u8; 32],
    pub arrow_schema_hash: [u8; 32],
    pub segment_chain_hash: [u8; 32],
    pub segment_witness_hash: [u8; 32],
    pub file_evidence_hash: [u8; 32],
    pub logical_replay_hash: [u8; 32],
    pub mmap_evidence_hash: [u8; 32],
    pub semantic_scan_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ReplayDeterminismProof {
    pub run_id: RunId,
    pub segment_count: usize,
    pub event_count: usize,
    pub manifest_hash: [u8; 32],
    pub arrow_schema_hash: [u8; 32],
    pub first_pass_event_sequence_hash: [u8; 32],
    pub second_pass_event_sequence_hash: [u8; 32],
    pub first_pass_ledger_hash: [u8; 32],
    pub second_pass_ledger_hash: [u8; 32],
    pub first_pass_mmap_evidence_hash: [u8; 32],
    pub second_pass_mmap_evidence_hash: [u8; 32],
    pub commit_sidecar_evidence_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[repr(u8)]
pub enum NextActionKind {
    ContinueExecution = 1,
    RequestToolCall = 2,
    RequestOperatorReview = 3,
    SealCheckpoint = 4,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RunCheckpoint {
    pub run_id: RunId,
    pub checkpoint_id: SubjectId,
    pub event_count: usize,
    pub segment_count: usize,
    pub ledger_last_hash: [u8; 32],
    pub manifest_hash: [u8; 32],
    pub replay_determinism_proof_hash: [u8; 32],
    pub context_fold_evidence_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct NextActionPacket {
    pub run_id: RunId,
    pub packet_id: SubjectId,
    pub checkpoint_hash: [u8; 32],
    pub action_kind: NextActionKind,
    pub task_id: SubjectId,
    pub typed_tool_ir_hash: [u8; 32],
    pub evidence_contract_hash: [u8; 32],
    pub policy_proof_hash: [u8; 32],
    pub candidate_evidence_hash: [u8; 32],
    pub task_selection_proof_hash: Option<[u8; 32]>,
    pub packet_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MemoryCommitHandoffProof {
    pub run_id: RunId,
    pub session_id: SubjectId,
    pub memory_commit_event_id: EventId,
    pub memory_commit_event_hash: [u8; 32],
    pub tool_execution_evidence_hash: [u8; 32],
    pub memory_commit_proof_hash: [u8; 32],
    pub replay_determinism_proof_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SkillAdmissionHandoffProof {
    pub run_id: RunId,
    pub skill_id: SubjectId,
    pub skill_admission_event_id: EventId,
    pub skill_admission_event_hash: [u8; 32],
    pub package_hash: [u8; 32],
    pub admission_hash: [u8; 32],
    pub registry_epoch: u64,
    pub registry_commit_hash: [u8; 32],
    pub replay_determinism_proof_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub activation_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserOpsBenchVerificationHandoffProof {
    pub run_id: RunId,
    pub verification_subject_id: SubjectId,
    pub verification_event_id: EventId,
    pub verification_event_hash: [u8; 32],
    pub proof_hash: [u8; 32],
    pub suite_hash: [u8; 32],
    pub scorecard_file_hash: [u8; 32],
    pub verified_records_hash: [u8; 32],
    pub verified_task_count: u64,
    pub replay_determinism_proof_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AgenticEvidenceSdkRunHandoffProof {
    pub run_id: RunId,
    pub execution_id: SubjectId,
    pub sdk_run_event_id: EventId,
    pub sdk_run_event_hash: [u8; 32],
    pub manifest_hash: [u8; 32],
    pub execution_record_hash: [u8; 32],
    pub candidate_list_hash: [u8; 32],
    pub candidate_count: u32,
    pub capsule_hash: [u8; 32],
    pub sdk_run_hash: [u8; 32],
    pub sdk_run_replay_binding_hash: [u8; 32],
    pub replay_determinism_proof_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub handoff_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunEventRecoveryReport {
    pub ledger: RunEventLedger,
    pub expected_segment_count: usize,
    pub recovered_segment_count: usize,
    pub last_valid_event_id: EventId,
    pub recovery_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RunEventCompactionReport {
    pub run_id: RunId,
    pub source_segment_count: usize,
    pub source_event_count: usize,
    pub source_manifest_hash: [u8; 32],
    pub source_audit_proof_hash: [u8; 32],
    pub source_mmap_evidence_hash: [u8; 32],
    pub compacted_event_count: usize,
    pub compacted_file_bytes: u64,
    pub compacted_payload_hash: [u8; 32],
    pub compacted_last_event_hash: [u8; 32],
    pub staged_temp_file_used: bool,
    pub temp_file_synced_before_publish: bool,
    pub publish_completed: bool,
    pub parent_directory_sync_attempted: bool,
    pub mmap_scan_verified: bool,
    pub report_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ReplayIoEvidence {
    pub segment_count: usize,
    pub mmap_segment_count: usize,
    pub total_file_bytes: u64,
    pub materialized_event_count: usize,
    pub materialized_run_event_bytes: u64,
    pub materialized_hash_bytes: u64,
    pub mmap_backing_used: bool,
    pub stream_reader_materializes_events: bool,
    pub evidence_hash: [u8; 32],
}

#[derive(Clone, Debug)]
struct ArrowMmapBufferOwner {
    _mmap: Arc<Mmap>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ArrowRunEventMmapColumnScan {
    file_bytes: u64,
    file_hash: [u8; 32],
    event_count: usize,
    first_event_id: EventId,
    last_event_id: EventId,
    first_event_hash: [u8; 32],
    last_event_hash: [u8; 32],
    segment_hash: [u8; 32],
    mmap_buffer_count: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct SegmentedArrowColumnScanProof {
    event_count: usize,
    total_arrow_file_bytes: u64,
    mmap_buffer_count: usize,
    first_event_id: EventId,
    last_event_id: EventId,
    segment_chain_hash: [u8; 32],
    segment_witness_hash: [u8; 32],
    file_evidence_hash: [u8; 32],
    logical_replay_hash: [u8; 32],
    mmap_evidence_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct RunEventSegmentCacheKey {
    run_id: RunId,
    segment_id: u64,
    event_count: u64,
    arrow_file_bytes: u64,
    arrow_file_hash: [u8; 32],
    segment_commit_hash: [u8; 32],
}

#[derive(Clone, Debug)]
struct RunEventSegmentCacheEntry {
    events: Arc<Vec<RunEvent>>,
    evidence: ReplayIoEvidence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ReplayChaosCrashPoint {
    pub iteration: u32,
    pub persisted_segment_count: usize,
    pub recovered_event_count: usize,
    pub last_valid_event_id: EventId,
    pub recovery_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ReplayChaosBenchReport {
    pub seed: u64,
    pub crash_points_exercised: u32,
    pub expected_event_count: usize,
    pub segment_count: usize,
    pub segmented_arrow_audit_proof_hash: [u8; 32],
    pub segmented_arrow_audit_logical_replay_hash: [u8; 32],
    pub segmented_arrow_audit_mmap_evidence_hash: [u8; 32],
    pub segmented_arrow_audit_segment_witness_hash: [u8; 32],
    pub segmented_arrow_audit_mmap_buffer_count: usize,
    pub column_scan_full_acceptance_count: u32,
    pub column_scan_missing_tail_rejection_count: u32,
    pub min_recovered_event_count: usize,
    pub max_recovered_event_count: usize,
    pub all_recoveries_valid: bool,
    pub report_hash: [u8; 32],
    pub crash_points: Vec<ReplayChaosCrashPoint>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ReplayEnduranceBenchReport {
    pub simulated_hours: u32,
    pub cycles_per_hour: u32,
    pub synthetic_cycle_count: u32,
    pub checkpoint_cadence_cycles: u32,
    pub checkpoint_count: usize,
    pub context_fold_count: usize,
    pub tail_recovered_context_fold_count: usize,
    pub context_fold_checkpoint_pair_count: usize,
    pub event_count: usize,
    pub segment_count: usize,
    pub segmented_arrow_audit_segment_count: usize,
    pub segmented_arrow_audit_event_count: usize,
    pub segmented_arrow_audit_total_file_bytes: u64,
    pub segmented_arrow_audit_mmap_buffer_count: usize,
    pub mmap_recovered_event_count: usize,
    pub tail_drop_segment_count: usize,
    pub tail_recovered_segment_count: usize,
    pub tail_recovered_event_count: usize,
    pub tail_recovered_checkpoint_count: usize,
    pub tail_events_since_last_checkpoint: usize,
    pub checkpoint_cadence_event_bound: usize,
    pub materialized_event_count: usize,
    pub materialized_replay_bytes: u64,
    pub bounded_materialized_bytes: u64,
    pub all_hash_chains_valid: bool,
    pub full_replay_matches: bool,
    pub tail_recovery_matches_prefix: bool,
    pub mmap_materialized_replay_proven: bool,
    pub materialization_within_bound: bool,
    pub manifest_hash: [u8; 32],
    pub expected_ledger_hash: [u8; 32],
    pub mmap_recovered_ledger_hash: [u8; 32],
    pub tail_recovered_ledger_hash: [u8; 32],
    pub context_fold_evidence_hash: [u8; 32],
    pub segmented_arrow_audit_proof_hash: [u8; 32],
    pub segmented_arrow_audit_segment_witness_hash: [u8; 32],
    pub replay_determinism_proof_hash: [u8; 32],
    pub run_checkpoint_hash: [u8; 32],
    pub next_action_packet_hash: [u8; 32],
    pub mmap_evidence_hash: [u8; 32],
    pub tail_recovery_hash: [u8; 32],
    pub report_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BinaryRunEventScanReport {
    pub event_count: usize,
    pub file_bytes: u64,
    pub last_event_hash: [u8; 32],
    pub payload_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinaryRunEventRecoveryReport {
    pub ledger: RunEventLedger,
    pub declared_event_count: usize,
    pub recovered_event_count: usize,
    pub file_bytes: u64,
    pub trailing_partial_bytes: usize,
    pub last_valid_event_id: EventId,
    pub last_event_hash: [u8; 32],
    pub recovered_payload_hash: [u8; 32],
    pub recovery_hash: [u8; 32],
}

pub struct RunEventSegmentArchive;

pub struct BinaryRunEventSegment;

pub struct ReplayChaosBench;

pub struct ReplayEnduranceBench;

const BINARY_RUN_EVENT_MAGIC: &[u8; 8] = b"AEGRUN06";
const BINARY_RUN_EVENT_FORMAT_VERSION: u64 = 8;
const BINARY_RUN_EVENT_HEADER_BYTES: usize = 88;
const BINARY_RUN_EVENT_RECORD_BYTES: usize = 184;

#[derive(Serialize)]
struct ReplayChaosBenchArtifactBody<'a> {
    schema_version: u32,
    bench_name: &'static str,
    scorecard: &'a HarnessBenchScorecard,
    report: &'a ReplayChaosBenchReport,
    io_evidence: Option<&'a ReplayIoEvidence>,
}

#[derive(Serialize)]
struct ReplayEnduranceBenchArtifactBody<'a> {
    schema_version: u32,
    bench_name: &'static str,
    report: &'a ReplayEnduranceBenchReport,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ReplayArtifactWriteEvidence {
    pub staged_temp_file_used: bool,
    pub temp_file_synced_before_publish: bool,
    pub publish_completed: bool,
    pub parent_directory_sync_attempted: bool,
    pub replace_existing_supported: bool,
    pub publish_write_through_requested: bool,
    pub logical_payload_bytes: u64,
    pub logical_payload_hash: [u8; 32],
    pub evidence_hash: [u8; 32],
}

#[derive(Serialize)]
struct ReplayChaosBenchArtifact<'a> {
    schema_version: u32,
    bench_name: &'static str,
    scorecard: &'a HarnessBenchScorecard,
    report: &'a ReplayChaosBenchReport,
    io_evidence: Option<&'a ReplayIoEvidence>,
    write_evidence: &'a ReplayArtifactWriteEvidence,
}

#[derive(Serialize)]
struct ReplayEnduranceBenchArtifact<'a> {
    schema_version: u32,
    bench_name: &'static str,
    report: &'a ReplayEnduranceBenchReport,
    write_evidence: &'a ReplayArtifactWriteEvidence,
}

impl ReplayArtifactWriteEvidence {
    fn for_logical_payload(
        logical_payload_bytes: u64,
        logical_payload_hash: [u8; 32],
        _path: &Path,
    ) -> Self {
        let staged_temp_file_used = true;
        let temp_file_synced_before_publish = true;
        let publish_completed = true;
        let parent_directory_sync_attempted = true;
        let replace_existing_supported = true;
        let publish_write_through_requested = cfg!(windows);
        let evidence_hash = replay_artifact_write_evidence_hash(
            staged_temp_file_used,
            temp_file_synced_before_publish,
            publish_completed,
            parent_directory_sync_attempted,
            replace_existing_supported,
            publish_write_through_requested,
            logical_payload_bytes,
            logical_payload_hash,
        );
        Self {
            staged_temp_file_used,
            temp_file_synced_before_publish,
            publish_completed,
            parent_directory_sync_attempted,
            replace_existing_supported,
            publish_write_through_requested,
            logical_payload_bytes,
            logical_payload_hash,
            evidence_hash,
        }
    }

    pub fn is_valid_physical_result(&self) -> bool {
        self.staged_temp_file_used
            && self.temp_file_synced_before_publish
            && self.publish_completed
            && self.parent_directory_sync_attempted
            && self.replace_existing_supported
            && self.logical_payload_bytes > 0
            && nonzero_hash(&self.logical_payload_hash)
            && self.evidence_hash
                == replay_artifact_write_evidence_hash(
                    self.staged_temp_file_used,
                    self.temp_file_synced_before_publish,
                    self.publish_completed,
                    self.parent_directory_sync_attempted,
                    self.replace_existing_supported,
                    self.publish_write_through_requested,
                    self.logical_payload_bytes,
                    self.logical_payload_hash,
                )
            && nonzero_hash(&self.evidence_hash)
    }
}

impl ToolExecutionEvidence {
    pub fn new(
        typed_tool_ir_hash: [u8; 32],
        policy_proof_trace_hash: [u8; 32],
        tool_output_hash: [u8; 32],
        physical_evidence_hash: [u8; 32],
        executor_kind: ToolExecutorKind,
        status: ToolExecutionStatus,
    ) -> Self {
        let evidence_hash = tool_execution_evidence_hash(
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            tool_output_hash,
            physical_evidence_hash,
            executor_kind,
            status,
        );
        Self {
            typed_tool_ir_hash,
            policy_proof_trace_hash,
            tool_output_hash,
            physical_evidence_hash,
            executor_kind,
            status,
            evidence_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.typed_tool_ir_hash)
            && nonzero_hash(&self.policy_proof_trace_hash)
            && nonzero_hash(&self.tool_output_hash)
            && nonzero_hash(&self.physical_evidence_hash)
            && nonzero_hash(&self.evidence_hash)
            && self.evidence_hash
                == tool_execution_evidence_hash(
                    self.typed_tool_ir_hash,
                    self.policy_proof_trace_hash,
                    self.tool_output_hash,
                    self.physical_evidence_hash,
                    self.executor_kind,
                    self.status,
                )
    }

    pub fn completion_binding_hash(&self, call_id: SubjectId) -> [u8; 32] {
        tool_call_completion_binding_hash(
            call_id,
            self.typed_tool_ir_hash,
            self.policy_proof_trace_hash,
            self.evidence_hash,
        )
    }

    pub fn binds_policy_and_request(
        &self,
        typed_tool_ir_hash: [u8; 32],
        policy_proof_trace_hash: [u8; 32],
    ) -> bool {
        self.is_valid()
            && self.typed_tool_ir_hash == typed_tool_ir_hash
            && self.policy_proof_trace_hash == policy_proof_trace_hash
    }
}

impl RunEventKind {
    fn from_u8(value: u8) -> Option<Self> {
        match value {
            0 => Some(Self::MissionCompiled),
            1 => Some(Self::ContextPackBuilt),
            2 => Some(Self::LLMResponseReceived),
            3 => Some(Self::ToolCallRequested),
            4 => Some(Self::ToolCallCompleted),
            5 => Some(Self::CheckpointSealed),
            6 => Some(Self::ConfigChanged),
            7 => Some(Self::WorkCancelled),
            8 => Some(Self::PolicyDecisionRecorded),
            9 => Some(Self::ApprovalTokenRecorded),
            10 => Some(Self::OperatorReviewArtifactRecorded),
            11 => Some(Self::ColdVectorExpansionRecorded),
            12 => Some(Self::CircuitBreakerTripped),
            13 => Some(Self::ContextFoldRecorded),
            14 => Some(Self::MemoryCommitRecorded),
            15 => Some(Self::ContextPackCandidateProofRecorded),
            16 => Some(Self::TaskSelectionProofRecorded),
            17 => Some(Self::BrowserObservationPacketRecorded),
            18 => Some(Self::GoalIntakeRecorded),
            19 => Some(Self::AgenticEvidenceExecutionRecorded),
            20 => Some(Self::SkillAdmissionRecorded),
            21 => Some(Self::BrowserOpsBenchVerificationRecorded),
            22 => Some(Self::ClusterCandidateAccepted),
            23 => Some(Self::ShadowSealRecorded),
            24 => Some(Self::LabEventRecorded),
            _ => None,
        }
    }
}

impl RunEvent {
    pub fn goal_intake_recorded(
        event_id: EventId,
        run_id: RunId,
        proof: &GoalIntakeProof,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::GoalIntakeRecorded,
            proof.packet.goal_id,
            proof.proof_hash,
            Some(goal_intake_replay_binding_hash(proof)),
            previous_event_hash,
        )
    }

    pub fn context_pack_built(
        event_id: EventId,
        run_id: RunId,
        pack_id: SubjectId,
        token_count: u32,
        context_pack_digest: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ContextPackBuilt,
            pack_id,
            context_pack_digest,
            Some(u64_payload_hash(
                "context-pack-token-count",
                token_count as u64,
            )),
            previous_event_hash,
        )
    }

    pub fn llm_response_received(
        event_id: EventId,
        run_id: RunId,
        response_hash: [u8; 32],
        raw_text_ref_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::LLMResponseReceived,
            0,
            response_hash,
            Some(raw_text_ref_hash),
            previous_event_hash,
        )
    }

    pub fn budget_admitted_llm_response_received(
        event_id: EventId,
        run_id: RunId,
        response_hash: [u8; 32],
        raw_text_ref_hash: [u8; 32],
        admission_proof: &ProviderRouteAdmissionProof,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::LLMResponseReceived,
            admission_proof.required_tokens as SubjectId,
            response_hash,
            Some(llm_budget_admission_replay_binding_hash(
                response_hash,
                raw_text_ref_hash,
                admission_proof,
            )),
            previous_event_hash,
        )
    }

    pub fn context_pack_candidate_proof_recorded(
        event_id: EventId,
        run_id: RunId,
        proof: &ContextPackCandidateProof,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ContextPackCandidateProofRecorded,
            proof.active_task_id,
            proof.proof_hash,
            Some(proof.context_pack_digest),
            previous_event_hash,
        )
    }

    pub fn task_selection_proof_recorded(
        event_id: EventId,
        run_id: RunId,
        proof: &TaskSelectionProof,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::TaskSelectionProofRecorded,
            proof.selected_task_id,
            proof.proof_hash,
            Some(proof.ready_queue_hash),
            previous_event_hash,
        )
    }

    pub fn browser_observation_packet_recorded(
        event_id: EventId,
        run_id: RunId,
        packet: &BrowserObservationPacket,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::BrowserObservationPacketRecorded,
            packet.action_id,
            packet.packet_hash,
            Some(browser_observation_packet_replay_binding_hash(packet)),
            previous_event_hash,
        )
    }

    pub fn tool_call_requested(
        event_id: EventId,
        run_id: RunId,
        call_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ToolCallRequested,
            call_id,
            typed_tool_ir_hash,
            None,
            previous_event_hash,
        )
    }

    pub fn tool_call_completed(
        event_id: EventId,
        run_id: RunId,
        call_id: SubjectId,
        evidence: &ToolExecutionEvidence,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ToolCallCompleted,
            call_id,
            evidence.completion_binding_hash(call_id),
            Some(evidence.evidence_hash),
            previous_event_hash,
        )
    }

    pub fn policy_decision_recorded(
        event_id: EventId,
        run_id: RunId,
        policy_trace_id: SubjectId,
        policy_proof_trace_hash: [u8; 32],
        typed_tool_ir_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::PolicyDecisionRecorded,
            policy_trace_id,
            policy_proof_trace_hash,
            Some(typed_tool_ir_hash),
            previous_event_hash,
        )
    }

    pub fn approval_token_recorded(
        event_id: EventId,
        run_id: RunId,
        approval_id: SubjectId,
        approval_token_hash: [u8; 32],
        review_packet_hash: [u8; 32],
        expires_at_ms: u64,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ApprovalTokenRecorded,
            approval_id,
            approval_token_hash,
            Some(approval_token_binding_hash(
                review_packet_hash,
                expires_at_ms,
            )),
            previous_event_hash,
        )
    }

    pub fn operator_review_artifact_recorded(
        event_id: EventId,
        run_id: RunId,
        artifact_id: SubjectId,
        signing_target_hash: [u8; 32],
        artifact_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::OperatorReviewArtifactRecorded,
            artifact_id,
            signing_target_hash,
            Some(artifact_hash),
            previous_event_hash,
        )
    }

    pub fn cold_vector_expansion_recorded(
        event_id: EventId,
        run_id: RunId,
        expansion_id: SubjectId,
        record: &ColdVectorExpansionReplayRecord,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ColdVectorExpansionRecorded,
            expansion_id,
            record.record_hash,
            Some(record.candidate_list_hash),
            previous_event_hash,
        )
    }

    pub fn agentic_evidence_execution_recorded(
        event_id: EventId,
        run_id: RunId,
        execution_id: SubjectId,
        record: &AgenticEvidenceExecutionRecord,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::AgenticEvidenceExecutionRecorded,
            execution_id,
            record.record_hash,
            Some(record.candidate_list_hash),
            previous_event_hash,
        )
    }

    pub fn agentic_evidence_sdk_run_recorded(
        event_id: EventId,
        run_id: RunId,
        execution_id: SubjectId,
        run: &AgenticEvidenceSdkRun,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::AgenticEvidenceExecutionRecorded,
            execution_id,
            run.run_hash,
            Some(agentic_evidence_sdk_run_replay_binding_hash(run)),
            previous_event_hash,
        )
    }

    pub fn skill_admission_recorded(
        event_id: EventId,
        run_id: RunId,
        admission: &SkillAdmissionRecord,
        admitted: &AdmittedSkillRecord,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::SkillAdmissionRecorded,
            admitted.skill_id,
            admitted.registry_commit_hash,
            Some(skill_admission_replay_binding_hash(admission, admitted)),
            previous_event_hash,
        )
    }

    pub fn browser_ops_bench_verification_recorded(
        event_id: EventId,
        run_id: RunId,
        proof: &BrowserOpsBenchVerificationProof,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::BrowserOpsBenchVerificationRecorded,
            proof.replay_subject_id(),
            proof.proof_hash,
            Some(browser_ops_bench_verification_replay_binding_hash(proof)),
            previous_event_hash,
        )
    }

    pub fn cluster_candidate_accepted(
        event_id: EventId,
        run_id: RunId,
        work_id: SubjectId,
        worker_result_hash: [u8; 32],
        replay_binding_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ClusterCandidateAccepted,
            work_id,
            worker_result_hash,
            Some(replay_binding_hash),
            previous_event_hash,
        )
    }

    pub fn shadow_seal_recorded(
        event_id: EventId,
        run_id: RunId,
        seal_id: SubjectId,
        batch: &ShadowSealBatchReceipt,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ShadowSealRecorded,
            seal_id,
            batch.batch_hash,
            Some(shadow_seal_replay_binding_hash(seal_id, batch)),
            previous_event_hash,
        )
    }

    pub fn circuit_breaker_tripped(
        event_id: EventId,
        run_id: RunId,
        request_id: SubjectId,
        evidence_hash: [u8; 32],
        response_fingerprint_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::CircuitBreakerTripped,
            request_id,
            evidence_hash,
            Some(response_fingerprint_hash),
            previous_event_hash,
        )
    }

    pub fn context_fold_recorded(
        event_id: EventId,
        run_id: RunId,
        active_task_id: SubjectId,
        record: &ContextFoldRecord,
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::ContextFoldRecorded,
            active_task_id,
            record.record_hash,
            Some(record.folded_node_hash),
            previous_event_hash,
        )
    }

    pub fn memory_commit_recorded(
        event_id: EventId,
        run_id: RunId,
        session_id: SubjectId,
        memory_commit_proof: &ToolMemoryCommitProof,
        tool_execution_evidence_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::MemoryCommitRecorded,
            session_id,
            memory_commit_proof.proof_hash,
            Some(memory_commit_binding_hash(
                session_id,
                tool_execution_evidence_hash,
                memory_commit_proof.proof_hash,
            )),
            previous_event_hash,
        )
    }

    pub fn checkpoint_sealed(
        event_id: EventId,
        run_id: RunId,
        checkpoint_id: SubjectId,
        checkpoint_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::CheckpointSealed,
            checkpoint_id,
            checkpoint_hash,
            None,
            previous_event_hash,
        )
    }

    pub fn lab_event_recorded(
        event_id: EventId,
        run_id: RunId,
        lab_event_hash: [u8; 32],
        lab_payload_hash: [u8; 32],
        previous_event_hash: [u8; 32],
    ) -> Self {
        Self::new(
            event_id,
            run_id,
            RunEventKind::LabEventRecorded,
            event_id as SubjectId,
            lab_event_hash,
            Some(lab_payload_hash),
            previous_event_hash,
        )
    }

    pub fn is_valid(&self) -> bool {
        self.run_id > 0
            && self.event_id > 0
            && nonzero_hash(&self.primary_hash)
            && self
                .secondary_hash
                .is_none_or(|secondary| nonzero_hash(&secondary))
            && self.event_hash == self.compute_hash()
            && (!matches!(
                self.kind,
                RunEventKind::ContextPackBuilt
                    | RunEventKind::ContextPackCandidateProofRecorded
                    | RunEventKind::LLMResponseReceived
                    | RunEventKind::ToolCallCompleted
                    | RunEventKind::PolicyDecisionRecorded
                    | RunEventKind::ApprovalTokenRecorded
                    | RunEventKind::OperatorReviewArtifactRecorded
                    | RunEventKind::ColdVectorExpansionRecorded
                    | RunEventKind::AgenticEvidenceExecutionRecorded
                    | RunEventKind::SkillAdmissionRecorded
                    | RunEventKind::BrowserOpsBenchVerificationRecorded
                    | RunEventKind::ClusterCandidateAccepted
                    | RunEventKind::ShadowSealRecorded
                    | RunEventKind::CircuitBreakerTripped
                    | RunEventKind::ContextFoldRecorded
                    | RunEventKind::MemoryCommitRecorded
                    | RunEventKind::TaskSelectionProofRecorded
                    | RunEventKind::BrowserObservationPacketRecorded
                    | RunEventKind::GoalIntakeRecorded
                    | RunEventKind::LabEventRecorded
            ) || self.secondary_hash.is_some())
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        compute_run_event_hash(
            self.event_id,
            self.run_id,
            self.kind,
            self.subject_id,
            self.primary_hash,
            self.secondary_hash,
            self.previous_event_hash,
        )
    }

    fn new(
        event_id: EventId,
        run_id: RunId,
        kind: RunEventKind,
        subject_id: SubjectId,
        primary_hash: [u8; 32],
        secondary_hash: Option<[u8; 32]>,
        previous_event_hash: [u8; 32],
    ) -> Self {
        let mut event = Self {
            event_id,
            run_id,
            kind,
            subject_id,
            primary_hash,
            secondary_hash,
            previous_event_hash,
            event_hash: [0; 32],
        };
        event.event_hash = event.compute_hash();
        event
    }
}

impl RunEventLedger {
    pub fn new(run_id: RunId) -> Self {
        Self {
            run_id,
            events: Vec::new(),
            has_context_pack_built: false,
            has_goal_intake_recorded: false,
            has_llm_response_received: false,
            has_pending_tool_call: false,
            has_policy_decision_recorded: false,
            has_tool_completion_recorded: false,
            has_browser_observation_packet_recorded: false,
            last_tool_call_id: 0,
            last_tool_call_event_id: 0,
            last_tool_ir_hash: [0; 32],
            last_tool_execution_evidence_hash: [0; 32],
            last_policy_event_id: 0,
            last_policy_proof_trace_hash: [0; 32],
            last_policy_tool_ir_hash: [0; 32],
            last_browser_observation_packet_hash: [0; 32],
        }
    }

    pub fn from_events(run_id: RunId, events: Vec<RunEvent>) -> Result<Self, ReplayLedgerError> {
        let mut has_context_pack_built = false;
        let mut has_goal_intake_recorded = false;
        let mut has_llm_response_received = false;
        let mut has_pending_tool_call = false;
        let mut has_policy_decision_recorded = false;
        let mut has_tool_completion_recorded = false;
        let mut has_browser_observation_packet_recorded = false;
        let mut last_tool_call_id = 0;
        let mut last_tool_call_event_id = 0;
        let mut last_tool_ir_hash = [0; 32];
        let mut last_tool_execution_evidence_hash = [0; 32];
        let mut last_policy_event_id = 0;
        let mut last_policy_proof_trace_hash = [0; 32];
        let mut last_policy_tool_ir_hash = [0; 32];
        let mut last_browser_observation_packet_hash = [0; 32];
        let mut previous_event_hash = [0; 32];

        for (index, event) in events.iter().enumerate() {
            if !event.is_valid() || event.run_id != run_id || event.event_id != index as EventId + 1
            {
                return Err(ReplayLedgerError::InvalidEvent);
            }
            if event.previous_event_hash != previous_event_hash {
                return Err(ReplayLedgerError::HashChainMismatch);
            }
            if matches!(
                event.kind,
                RunEventKind::ContextPackBuilt
                    | RunEventKind::ContextPackCandidateProofRecorded
                    | RunEventKind::TaskSelectionProofRecorded
                    | RunEventKind::LLMResponseReceived
                    | RunEventKind::ContextFoldRecorded
                    | RunEventKind::ToolCallRequested
                    | RunEventKind::ToolCallCompleted
                    | RunEventKind::PolicyDecisionRecorded
                    | RunEventKind::ApprovalTokenRecorded
                    | RunEventKind::OperatorReviewArtifactRecorded
                    | RunEventKind::ColdVectorExpansionRecorded
                    | RunEventKind::AgenticEvidenceExecutionRecorded
                    | RunEventKind::SkillAdmissionRecorded
                    | RunEventKind::BrowserOpsBenchVerificationRecorded
                    | RunEventKind::ClusterCandidateAccepted
                    | RunEventKind::ShadowSealRecorded
                    | RunEventKind::CircuitBreakerTripped
                    | RunEventKind::MemoryCommitRecorded
                    | RunEventKind::CheckpointSealed
            ) && !has_goal_intake_recorded
            {
                return Err(ReplayLedgerError::MissingPriorGoalIntake);
            }
            if event.kind == RunEventKind::GoalIntakeRecorded && has_goal_intake_recorded {
                return Err(ReplayLedgerError::InvalidEvent);
            }
            if matches!(
                event.kind,
                RunEventKind::LLMResponseReceived
                    | RunEventKind::ContextFoldRecorded
                    | RunEventKind::ContextPackCandidateProofRecorded
            ) && !has_context_pack_built
            {
                return Err(ReplayLedgerError::MissingPriorContextPack);
            }
            if matches!(
                event.kind,
                RunEventKind::ToolCallRequested | RunEventKind::CircuitBreakerTripped
            ) && !has_llm_response_received
            {
                return Err(ReplayLedgerError::MissingPriorLlmResponse);
            }
            if event.kind == RunEventKind::ToolCallRequested && has_pending_tool_call {
                return Err(ReplayLedgerError::InvalidEvent);
            }
            if event.kind == RunEventKind::ToolCallCompleted && !has_pending_tool_call {
                return Err(ReplayLedgerError::MissingPriorToolCall);
            }
            if event.kind == RunEventKind::MemoryCommitRecorded && !has_tool_completion_recorded {
                return Err(ReplayLedgerError::InvalidEvent);
            }
            if matches!(
                event.kind,
                RunEventKind::ToolCallCompleted
                    | RunEventKind::ApprovalTokenRecorded
                    | RunEventKind::OperatorReviewArtifactRecorded
            ) && !has_policy_decision_recorded
            {
                return Err(ReplayLedgerError::MissingPriorPolicyDecision);
            }
            if event.kind == RunEventKind::ToolCallCompleted {
                let evidence_hash = event
                    .secondary_hash
                    .ok_or(ReplayLedgerError::InvalidEvent)?;
                if event.subject_id != last_tool_call_id
                    || last_policy_event_id <= last_tool_call_event_id
                    || last_tool_ir_hash != last_policy_tool_ir_hash
                    || event.primary_hash
                        != tool_call_completion_binding_hash(
                            event.subject_id,
                            last_tool_ir_hash,
                            last_policy_proof_trace_hash,
                            evidence_hash,
                        )
                {
                    return Err(ReplayLedgerError::InvalidEvent);
                }
            }
            if event.kind == RunEventKind::MemoryCommitRecorded {
                let binding_hash = event
                    .secondary_hash
                    .ok_or(ReplayLedgerError::InvalidEvent)?;
                if event.subject_id == 0
                    || has_pending_tool_call
                    || !nonzero_hash(&last_tool_execution_evidence_hash)
                    || binding_hash
                        != memory_commit_binding_hash(
                            event.subject_id,
                            last_tool_execution_evidence_hash,
                            event.primary_hash,
                        )
                {
                    return Err(ReplayLedgerError::InvalidEvent);
                }
            }

            match event.kind {
                RunEventKind::GoalIntakeRecorded => has_goal_intake_recorded = true,
                RunEventKind::ContextPackBuilt => has_context_pack_built = true,
                RunEventKind::LLMResponseReceived => has_llm_response_received = true,
                RunEventKind::ToolCallRequested => {
                    has_pending_tool_call = true;
                    has_tool_completion_recorded = false;
                    last_tool_call_id = event.subject_id;
                    last_tool_call_event_id = event.event_id;
                    last_tool_ir_hash = event.primary_hash;
                }
                RunEventKind::ToolCallCompleted => {
                    has_pending_tool_call = false;
                    has_tool_completion_recorded = true;
                    last_tool_execution_evidence_hash = event
                        .secondary_hash
                        .ok_or(ReplayLedgerError::InvalidEvent)?;
                }
                RunEventKind::PolicyDecisionRecorded => {
                    has_policy_decision_recorded = true;
                    last_policy_event_id = event.event_id;
                    last_policy_proof_trace_hash = event.primary_hash;
                    last_policy_tool_ir_hash = event
                        .secondary_hash
                        .ok_or(ReplayLedgerError::InvalidEvent)?;
                }
                RunEventKind::BrowserObservationPacketRecorded => {
                    has_browser_observation_packet_recorded = true;
                    last_browser_observation_packet_hash = event.primary_hash;
                }
                _ => {}
            }
            previous_event_hash = event.event_hash;
        }

        Ok(Self {
            run_id,
            events,
            has_context_pack_built,
            has_goal_intake_recorded,
            has_llm_response_received,
            has_pending_tool_call,
            has_policy_decision_recorded,
            has_tool_completion_recorded,
            has_browser_observation_packet_recorded,
            last_tool_call_id,
            last_tool_call_event_id,
            last_tool_ir_hash,
            last_tool_execution_evidence_hash,
            last_policy_event_id,
            last_policy_proof_trace_hash,
            last_policy_tool_ir_hash,
            last_browser_observation_packet_hash,
        })
    }

    pub fn append_context_pack_built(
        &mut self,
        pack_id: SubjectId,
        token_count: u32,
        context_pack_digest: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::context_pack_built(
            self.next_event_id(),
            self.run_id,
            pack_id,
            token_count,
            context_pack_digest,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_goal_intake_recorded(
        &mut self,
        proof: &GoalIntakeProof,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !proof.is_valid() || self.has_goal_intake_recorded {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::goal_intake_recorded(
            self.next_event_id(),
            self.run_id,
            proof,
            self.last_hash(),
        );
        self.append(event, false, false, false, false, false)
    }

    pub fn append_context_pack_candidate_proof_recorded(
        &mut self,
        proof: &ContextPackCandidateProof,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !proof.has_valid_fields() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::context_pack_candidate_proof_recorded(
            self.next_event_id(),
            self.run_id,
            proof,
            self.last_hash(),
        );
        self.append(event, true, true, false, false, false)
    }

    pub fn append_task_selection_proof_recorded(
        &mut self,
        proof: &TaskSelectionProof,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !proof.has_valid_fields() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::task_selection_proof_recorded(
            self.next_event_id(),
            self.run_id,
            proof,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_browser_observation_packet_recorded(
        &mut self,
        packet: &BrowserObservationPacket,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !packet.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::browser_observation_packet_recorded(
            self.next_event_id(),
            self.run_id,
            packet,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_llm_response_received(
        &mut self,
        response_hash: [u8; 32],
        raw_text_ref_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::llm_response_received(
            self.next_event_id(),
            self.run_id,
            response_hash,
            raw_text_ref_hash,
            self.last_hash(),
        );
        self.append(event, true, true, false, false, false)
    }

    pub fn append_budget_admitted_llm_response_received(
        &mut self,
        response_hash: [u8; 32],
        raw_text_ref_hash: [u8; 32],
        admission_proof: &ProviderRouteAdmissionProof,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if admission_proof.proof_hash == [0; 32] || admission_proof.required_tokens == 0 {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::budget_admitted_llm_response_received(
            self.next_event_id(),
            self.run_id,
            response_hash,
            raw_text_ref_hash,
            admission_proof,
            self.last_hash(),
        );
        self.append(event, true, true, false, false, false)
    }

    pub fn append_llm_derived_tool_call_requested(
        &mut self,
        call_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::tool_call_requested(
            self.next_event_id(),
            self.run_id,
            call_id,
            typed_tool_ir_hash,
            self.last_hash(),
        );
        self.append(event, true, false, true, false, false)
    }

    pub fn append_tool_call_completed(
        &mut self,
        call_id: SubjectId,
        evidence: &ToolExecutionEvidence,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !evidence.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::tool_call_completed(
            self.next_event_id(),
            self.run_id,
            call_id,
            evidence,
            self.last_hash(),
        );
        self.append(event, true, false, false, true, true)
    }

    pub fn append_memory_commit_recorded(
        &mut self,
        memory_commit_proof: &ToolMemoryCommitProof,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !memory_commit_proof.has_valid_fields()
            || !nonzero_hash(&self.last_tool_execution_evidence_hash)
        {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::memory_commit_recorded(
            self.next_event_id(),
            self.run_id,
            memory_commit_proof.session_id,
            memory_commit_proof,
            self.last_tool_execution_evidence_hash,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_policy_decision_recorded(
        &mut self,
        policy_trace_id: SubjectId,
        policy_proof_trace_hash: [u8; 32],
        typed_tool_ir_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::policy_decision_recorded(
            self.next_event_id(),
            self.run_id,
            policy_trace_id,
            policy_proof_trace_hash,
            typed_tool_ir_hash,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_policy_decision_recorded_after_browser_observation(
        &mut self,
        policy_trace_id: SubjectId,
        policy_proof_trace_hash: [u8; 32],
        typed_tool_ir_hash: [u8; 32],
        browser_observation_packet_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !self.has_browser_observation_packet_recorded
            || !nonzero_hash(&browser_observation_packet_hash)
            || self.last_browser_observation_packet_hash != browser_observation_packet_hash
        {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        self.append_policy_decision_recorded(
            policy_trace_id,
            policy_proof_trace_hash,
            typed_tool_ir_hash,
        )
    }

    pub fn append_approval_token_recorded(
        &mut self,
        approval_id: SubjectId,
        approval_token_hash: [u8; 32],
        review_packet_hash: [u8; 32],
        expires_at_ms: u64,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::approval_token_recorded(
            self.next_event_id(),
            self.run_id,
            approval_id,
            approval_token_hash,
            review_packet_hash,
            expires_at_ms,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, true)
    }

    pub fn append_operator_review_artifact_recorded(
        &mut self,
        artifact_id: SubjectId,
        signing_target_hash: [u8; 32],
        artifact_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::operator_review_artifact_recorded(
            self.next_event_id(),
            self.run_id,
            artifact_id,
            signing_target_hash,
            artifact_hash,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, true)
    }

    pub fn append_cold_vector_expansion_recorded(
        &mut self,
        expansion_id: SubjectId,
        record: &ColdVectorExpansionReplayRecord,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !record.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::cold_vector_expansion_recorded(
            self.next_event_id(),
            self.run_id,
            expansion_id,
            record,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_agentic_evidence_execution_recorded(
        &mut self,
        execution_id: SubjectId,
        record: &AgenticEvidenceExecutionRecord,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if execution_id == 0 || !record.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::agentic_evidence_execution_recorded(
            self.next_event_id(),
            self.run_id,
            execution_id,
            record,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_agentic_evidence_sdk_run_recorded(
        &mut self,
        execution_id: SubjectId,
        program: &AgenticEvidenceProgram,
        run: &AgenticEvidenceSdkRun,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if execution_id == 0 || !run.is_valid_for_program(program) {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::agentic_evidence_sdk_run_recorded(
            self.next_event_id(),
            self.run_id,
            execution_id,
            run,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_skill_admission_recorded(
        &mut self,
        admission: &SkillAdmissionRecord,
        admitted: &AdmittedSkillRecord,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !admission.has_valid_fields()
            || !admitted.has_valid_fields()
            || admitted.admission_hash != admission.admission_hash
            || admitted.package_hash != admission.package_hash
        {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::skill_admission_recorded(
            self.next_event_id(),
            self.run_id,
            admission,
            admitted,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_browser_ops_bench_verification_recorded(
        &mut self,
        proof: &BrowserOpsBenchVerificationProof,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !proof.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::browser_ops_bench_verification_recorded(
            self.next_event_id(),
            self.run_id,
            proof,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_cluster_candidate_accepted(
        &mut self,
        work_id: SubjectId,
        worker_result_hash: [u8; 32],
        replay_binding_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if work_id == 0 || !nonzero_hash(&worker_result_hash) || !nonzero_hash(&replay_binding_hash)
        {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::cluster_candidate_accepted(
            self.next_event_id(),
            self.run_id,
            work_id,
            worker_result_hash,
            replay_binding_hash,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_shadow_seal_recorded(
        &mut self,
        seal_id: SubjectId,
        batch: &ShadowSealBatchReceipt,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if seal_id == 0 || !batch.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::shadow_seal_recorded(
            self.next_event_id(),
            self.run_id,
            seal_id,
            batch,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_circuit_breaker_tripped(
        &mut self,
        request_id: SubjectId,
        evidence_hash: [u8; 32],
        response_fingerprint_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::circuit_breaker_tripped(
            self.next_event_id(),
            self.run_id,
            request_id,
            evidence_hash,
            response_fingerprint_hash,
            self.last_hash(),
        );
        self.append(event, true, false, true, false, false)
    }

    pub fn append_context_fold_recorded(
        &mut self,
        record: &ContextFoldRecord,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !record.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::context_fold_recorded(
            self.next_event_id(),
            self.run_id,
            record.active_task_id,
            record,
            self.last_hash(),
        );
        self.append(event, true, true, false, false, false)
    }

    pub fn append_checkpoint_sealed(
        &mut self,
        checkpoint_id: SubjectId,
        checkpoint_hash: [u8; 32],
    ) -> Result<&RunEvent, ReplayLedgerError> {
        let event = RunEvent::checkpoint_sealed(
            self.next_event_id(),
            self.run_id,
            checkpoint_id,
            checkpoint_hash,
            self.last_hash(),
        );
        self.append(event, true, false, false, false, false)
    }

    pub fn append_lab_event_recorded(
        &mut self,
        lab_event: &crate::lab::LabEvent,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !lab_event.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let event = RunEvent::lab_event_recorded(
            self.next_event_id(),
            self.run_id,
            lab_event.event_hash,
            lab_event.payload_hash,
            self.last_hash(),
        );
        self.append(event, false, false, false, false, false)
    }

    pub fn verify_hash_chain(&self) -> bool {
        let mut previous = [0; 32];
        for event in &self.events {
            if event.previous_event_hash != previous || !event.is_valid() {
                return false;
            }
            previous = event.event_hash;
        }
        true
    }

    pub fn last_hash(&self) -> [u8; 32] {
        self.events.last().map_or([0; 32], |event| event.event_hash)
    }

    pub fn len(&self) -> usize {
        self.events.len()
    }

    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    pub fn events(&self) -> &[RunEvent] {
        &self.events
    }

    fn append(
        &mut self,
        event: RunEvent,
        requires_prior_goal_intake: bool,
        requires_prior_context_pack: bool,
        requires_prior_llm_response: bool,
        requires_prior_tool_call: bool,
        requires_prior_policy_decision: bool,
    ) -> Result<&RunEvent, ReplayLedgerError> {
        if !event.is_valid() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        if event.run_id != self.run_id || event.event_id != self.next_event_id() {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        if event.previous_event_hash != self.last_hash() {
            return Err(ReplayLedgerError::HashChainMismatch);
        }
        if requires_prior_goal_intake && !self.has_goal_intake_recorded {
            return Err(ReplayLedgerError::MissingPriorGoalIntake);
        }
        if requires_prior_context_pack && !self.has_context_pack_built() {
            return Err(ReplayLedgerError::MissingPriorContextPack);
        }
        if requires_prior_llm_response && !self.has_llm_response_received() {
            return Err(ReplayLedgerError::MissingPriorLlmResponse);
        }
        if requires_prior_tool_call && !self.has_pending_tool_call() {
            return Err(ReplayLedgerError::MissingPriorToolCall);
        }
        if requires_prior_policy_decision && !self.has_policy_decision_recorded() {
            return Err(ReplayLedgerError::MissingPriorPolicyDecision);
        }
        if event.kind == RunEventKind::ToolCallCompleted {
            self.validate_tool_call_completed(&event)?;
        }
        if event.kind == RunEventKind::MemoryCommitRecorded {
            self.validate_memory_commit_recorded(&event)?;
        }
        match event.kind {
            RunEventKind::GoalIntakeRecorded => {
                if self.has_goal_intake_recorded {
                    return Err(ReplayLedgerError::InvalidEvent);
                }
                self.has_goal_intake_recorded = true;
            }
            RunEventKind::ContextPackBuilt => self.has_context_pack_built = true,
            RunEventKind::LLMResponseReceived => self.has_llm_response_received = true,
            RunEventKind::ToolCallRequested => {
                if self.has_pending_tool_call {
                    return Err(ReplayLedgerError::InvalidEvent);
                }
                self.has_pending_tool_call = true;
                self.has_tool_completion_recorded = false;
                self.last_tool_call_id = event.subject_id;
                self.last_tool_call_event_id = event.event_id;
                self.last_tool_ir_hash = event.primary_hash;
            }
            RunEventKind::ToolCallCompleted => {
                self.has_pending_tool_call = false;
                self.has_tool_completion_recorded = true;
                self.last_tool_execution_evidence_hash = event
                    .secondary_hash
                    .ok_or(ReplayLedgerError::InvalidEvent)?;
            }
            RunEventKind::PolicyDecisionRecorded => {
                self.has_policy_decision_recorded = true;
                self.last_policy_event_id = event.event_id;
                self.last_policy_proof_trace_hash = event.primary_hash;
                self.last_policy_tool_ir_hash = event
                    .secondary_hash
                    .ok_or(ReplayLedgerError::InvalidEvent)?;
            }
            RunEventKind::BrowserObservationPacketRecorded => {
                self.has_browser_observation_packet_recorded = true;
                self.last_browser_observation_packet_hash = event.primary_hash;
            }
            _ => {}
        }
        self.events.push(event);
        Ok(self.events.last().expect("event just pushed"))
    }

    fn has_llm_response_received(&self) -> bool {
        self.has_llm_response_received
    }

    fn has_context_pack_built(&self) -> bool {
        self.has_context_pack_built
    }

    fn has_policy_decision_recorded(&self) -> bool {
        self.has_policy_decision_recorded
    }

    fn has_pending_tool_call(&self) -> bool {
        self.has_pending_tool_call
    }

    fn validate_tool_call_completed(&self, event: &RunEvent) -> Result<(), ReplayLedgerError> {
        let evidence_hash = event
            .secondary_hash
            .ok_or(ReplayLedgerError::InvalidEvent)?;
        if event.subject_id != self.last_tool_call_id {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        if self.last_policy_event_id <= self.last_tool_call_event_id {
            return Err(ReplayLedgerError::MissingPriorPolicyDecision);
        }
        if self.last_tool_ir_hash != self.last_policy_tool_ir_hash {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let expected_binding = tool_call_completion_binding_hash(
            event.subject_id,
            self.last_tool_ir_hash,
            self.last_policy_proof_trace_hash,
            evidence_hash,
        );
        if event.primary_hash != expected_binding {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        Ok(())
    }

    fn validate_memory_commit_recorded(&self, event: &RunEvent) -> Result<(), ReplayLedgerError> {
        if !self.has_tool_completion_recorded
            || self.has_pending_tool_call
            || event.subject_id == 0
            || !nonzero_hash(&self.last_tool_execution_evidence_hash)
        {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        let binding_hash = event
            .secondary_hash
            .ok_or(ReplayLedgerError::InvalidEvent)?;
        if binding_hash
            != memory_commit_binding_hash(
                event.subject_id,
                self.last_tool_execution_evidence_hash,
                event.primary_hash,
            )
        {
            return Err(ReplayLedgerError::InvalidEvent);
        }
        Ok(())
    }

    fn next_event_id(&self) -> EventId {
        self.events.len() as EventId + 1
    }
}

impl ReplayIoEvidence {
    fn for_mmap_segment(file_bytes: u64, events: &[RunEvent]) -> Self {
        Self::new(1, 1, file_bytes, events, true, true)
    }

    fn new(
        segment_count: usize,
        mmap_segment_count: usize,
        total_file_bytes: u64,
        events: &[RunEvent],
        mmap_backing_used: bool,
        stream_reader_materializes_events: bool,
    ) -> Self {
        let materialized_event_count = events.len();
        let materialized_run_event_bytes = std::mem::size_of_val(events) as u64;
        let materialized_hash_bytes = events
            .iter()
            .map(|event| 32 * 3 + event.secondary_hash.map_or(0, |_| 32))
            .sum::<usize>() as u64;
        let evidence_hash = replay_io_evidence_hash(
            segment_count,
            mmap_segment_count,
            total_file_bytes,
            materialized_event_count,
            materialized_run_event_bytes,
            materialized_hash_bytes,
            mmap_backing_used,
            stream_reader_materializes_events,
        );
        Self {
            segment_count,
            mmap_segment_count,
            total_file_bytes,
            materialized_event_count,
            materialized_run_event_bytes,
            materialized_hash_bytes,
            mmap_backing_used,
            stream_reader_materializes_events,
            evidence_hash,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn from_totals(
        segment_count: usize,
        mmap_segment_count: usize,
        total_file_bytes: u64,
        materialized_event_count: usize,
        materialized_run_event_bytes: u64,
        materialized_hash_bytes: u64,
        mmap_backing_used: bool,
        stream_reader_materializes_events: bool,
    ) -> Self {
        let evidence_hash = replay_io_evidence_hash(
            segment_count,
            mmap_segment_count,
            total_file_bytes,
            materialized_event_count,
            materialized_run_event_bytes,
            materialized_hash_bytes,
            mmap_backing_used,
            stream_reader_materializes_events,
        );
        Self {
            segment_count,
            mmap_segment_count,
            total_file_bytes,
            materialized_event_count,
            materialized_run_event_bytes,
            materialized_hash_bytes,
            mmap_backing_used,
            stream_reader_materializes_events,
            evidence_hash,
        }
    }

    pub fn proves_mmap_materialized_replay(&self) -> bool {
        self.segment_count > 0
            && self.segment_count == self.mmap_segment_count
            && self.total_file_bytes > 0
            && self.materialized_event_count > 0
            && self.materialized_run_event_bytes > 0
            && self.materialized_hash_bytes > 0
            && self.mmap_backing_used
            && self.stream_reader_materializes_events
            && nonzero_hash(&self.evidence_hash)
    }
}

impl ArrowRunEventStream {
    pub fn create(path: impl AsRef<Path>) -> Result<Self, &'static str> {
        Self::create_with_mode(path, false)
    }

    fn create_exclusive(path: impl AsRef<Path>) -> Result<Self, &'static str> {
        Self::create_with_mode(path, true)
    }

    fn create_with_mode(path: impl AsRef<Path>, exclusive: bool) -> Result<Self, &'static str> {
        let path = path.as_ref().to_path_buf();
        let mut options = OpenOptions::new();
        options.write(true);
        if exclusive {
            options.create_new(true);
        } else {
            options.create(true).truncate(true);
        }
        let file = options
            .open(&path)
            .map_err(|_| "failed to open run event arrow stream")?;
        let schema = Arc::new(Self::schema());
        let writer = StreamWriter::try_new(file, schema.as_ref())
            .map_err(|_| "failed to initialize run event arrow stream")?;
        Ok(Self {
            path,
            schema,
            writer: Some(writer),
        })
    }

    pub fn append_event(&mut self, event: &RunEvent) -> Result<(), &'static str> {
        self.append_events(std::slice::from_ref(event))
    }

    pub fn append_events(&mut self, events: &[RunEvent]) -> Result<(), &'static str> {
        if events.is_empty() || events.iter().any(|event| !event.is_valid()) {
            return Err("invalid run event batch");
        }
        let schema = Arc::clone(&self.schema);
        let batch = Self::batch_from_events(schema, events)?;
        let writer = self
            .writer
            .as_mut()
            .ok_or("run event arrow stream already finished")?;
        writer
            .write(&batch)
            .map_err(|_| "failed to write run event arrow batch")?;
        writer
            .flush()
            .map_err(|_| "failed to flush run event arrow stream")?;
        Ok(())
    }

    fn batch_from_events(
        schema: SchemaRef,
        events: &[RunEvent],
    ) -> Result<RecordBatch, &'static str> {
        let primary_hashes: Vec<&[u8]> = events
            .iter()
            .map(|event| event.primary_hash.as_slice())
            .collect();
        let secondary_hashes: Vec<Option<&[u8]>> = events
            .iter()
            .map(|event| event.secondary_hash.as_ref().map(|hash| hash.as_slice()))
            .collect();
        let previous_hashes: Vec<&[u8]> = events
            .iter()
            .map(|event| event.previous_event_hash.as_slice())
            .collect();
        let event_hashes: Vec<&[u8]> = events
            .iter()
            .map(|event| event.event_hash.as_slice())
            .collect();

        RecordBatch::try_new(
            schema,
            vec![
                Arc::new(UInt64Array::from_iter_values(
                    events.iter().map(|event| event.event_id),
                )) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values(
                    events.iter().map(|event| split_u128(event.run_id).0),
                )) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values(
                    events.iter().map(|event| split_u128(event.run_id).1),
                )) as ArrayRef,
                Arc::new(UInt8Array::from_iter_values(
                    events.iter().map(|event| event.kind as u8),
                )) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values(
                    events.iter().map(|event| split_u128(event.subject_id).0),
                )) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values(
                    events.iter().map(|event| split_u128(event.subject_id).1),
                )) as ArrayRef,
                Arc::new(BinaryArray::from_vec(primary_hashes)) as ArrayRef,
                Arc::new(BinaryArray::from_opt_vec(secondary_hashes)) as ArrayRef,
                Arc::new(BinaryArray::from_vec(previous_hashes)) as ArrayRef,
                Arc::new(BinaryArray::from_vec(event_hashes)) as ArrayRef,
            ],
        )
        .map_err(|_| "failed to encode run event arrow batch")
    }

    pub fn finish(&mut self) -> Result<(), &'static str> {
        if let Some(mut writer) = self.writer.take() {
            writer
                .finish()
                .map_err(|_| "failed to finish run event arrow stream")?;
            writer
                .get_mut()
                .sync_all()
                .map_err(|_| "failed to sync run event arrow stream")?;
        }
        Ok(())
    }

    pub fn path(&self) -> &Path {
        self.path.as_path()
    }

    pub fn read_ledger(
        path: impl AsRef<Path>,
        run_id: RunId,
    ) -> Result<RunEventLedger, &'static str> {
        let events = Self::read_events(path)?;
        RunEventLedger::from_events(run_id, events).map_err(|_| "invalid run event ledger")
    }

    pub fn read_ledger_mmap(
        path: impl AsRef<Path>,
        run_id: RunId,
    ) -> Result<RunEventLedger, &'static str> {
        let events = Self::read_events_mmap(path)?;
        RunEventLedger::from_events(run_id, events).map_err(|_| "invalid mmap run event ledger")
    }

    pub fn read_events(path: impl AsRef<Path>) -> Result<Vec<RunEvent>, &'static str> {
        let file = File::open(path).map_err(|_| "failed to open run event arrow stream")?;
        let reader =
            StreamReader::try_new(file, None).map_err(|_| "failed to read run event stream")?;
        events_from_reader(reader)
    }

    pub fn read_events_mmap(path: impl AsRef<Path>) -> Result<Vec<RunEvent>, &'static str> {
        Self::read_events_mmap_with_evidence(path).map(|(events, _evidence)| events)
    }

    pub fn read_events_mmap_with_evidence(
        path: impl AsRef<Path>,
    ) -> Result<(Vec<RunEvent>, ReplayIoEvidence), &'static str> {
        Self::read_events_mmap_with_capacity_and_evidence(path, 0)
    }

    fn read_events_mmap_with_capacity_and_evidence(
        path: impl AsRef<Path>,
        expected_event_count: usize,
    ) -> Result<(Vec<RunEvent>, ReplayIoEvidence), &'static str> {
        let (events, evidence, _file_hash) =
            Self::read_events_mmap_with_capacity_evidence_and_file_hash(
                path,
                expected_event_count,
            )?;
        Ok((events, evidence))
    }

    fn read_events_mmap_with_capacity_evidence_and_file_hash(
        path: impl AsRef<Path>,
        expected_event_count: usize,
    ) -> Result<(Vec<RunEvent>, ReplayIoEvidence, [u8; 32]), &'static str> {
        // The expected count may originate in a persisted manifest.  It is a
        // validation hint, not an allocation authority; reserve only as rows
        // are decoded so hostile metadata cannot force a giant allocation.
        let mut events = Vec::new();
        let (evidence, file_hash) = Self::append_events_mmap_with_capacity_evidence_and_file_hash(
            path,
            expected_event_count,
            &mut events,
            true,
        )?;
        Ok((events, evidence, file_hash))
    }

    fn append_events_mmap_with_capacity_evidence_and_file_hash(
        path: impl AsRef<Path>,
        _expected_event_count: usize,
        events: &mut Vec<RunEvent>,
        validate_events: bool,
    ) -> Result<(ReplayIoEvidence, [u8; 32]), &'static str> {
        let file = File::open(path).map_err(|_| "failed to open mmap run event arrow stream")?;
        // SAFETY: `file` is opened read-only above and `MmapOptions::new().map(&file)`
        // produces a default-sized read-only mapping that matches the file size.
        // `map` (non-`map_mut`) requires no exclusivity because no mutable mapping exists.
        // The mapping is wrapped in `Arc` so it can be shared with downstream Arrow
        // buffer slicing without aliasing; `Arc::new` is itself safe and the mmap
        // pointer is `Send + Sync` because the file is read-only on disk.
        let mmap = Arc::new(unsafe {
            MmapOptions::new()
                .map(&file)
                .map_err(|_| "failed to mmap run event arrow stream")?
        });
        let file_bytes = mmap.len() as u64;
        let file_hash = blake3_hash_bytes(&mmap[..]);
        validate_arrow_ipc_stream_zero_copy_metadata(&mmap[..])?;
        let mut buffer = arrow_buffer_from_mmap(Arc::clone(&mmap))?;
        let mut decoder = trusted_mmap_stream_decoder();
        let segment_start = events.len();
        while !buffer.is_empty() {
            let before = buffer.len();
            if let Some(batch) = decoder
                .decode(&mut buffer)
                .map_err(|_| "failed to decode mmap event stream")?
            {
                append_events_from_batch(&batch, events, validate_events)?;
            }
            if buffer.len() == before {
                return Err("mmap event stream decoder did not advance");
            }
        }
        let evidence = ReplayIoEvidence::for_mmap_segment(file_bytes, &events[segment_start..]);
        Ok((evidence, file_hash))
    }

    fn scan_mmap_columns_for_entry(
        path: impl AsRef<Path>,
        entry: &RunEventSegmentEntry,
        run_id: RunId,
        expected_event_id: &mut EventId,
        expected_previous_hash: &mut [u8; 32],
        scan_state: &mut RunEventColumnScanState,
        logical_hasher: &mut Hasher,
    ) -> Result<ArrowRunEventMmapColumnScan, &'static str> {
        let file =
            File::open(path.as_ref()).map_err(|_| "failed to open mmap run event column scan")?;
        // SAFETY: identical invariants to the arrow-stream `mmap` at line 2580+.
        // `file` is opened read-only; `MmapOptions::new().map(&file)` produces a
        // default-sized read-only mapping that exactly matches the file size.
        // No mutable mapping is outstanding (file is opened read-only and the
        // surrounding `ArrowRunEventMmapColumnScan` constructor does not hold
        // any other borrow), so `&[u8]` aliasing rules are respected. Arc allows
        // the same mapping to back multiple Arrow column slices (each slice
        // borrows a sub-range; Arc's strong-count ensures the bytes outlive them).
        let mmap = Arc::new(unsafe {
            MmapOptions::new()
                .map(&file)
                .map_err(|_| "failed to mmap run event arrow column scan")?
        });
        let file_bytes = mmap.len() as u64;
        let file_hash = blake3_hash_bytes(&mmap[..]);
        validate_arrow_ipc_stream_zero_copy_metadata(&mmap[..])?;
        let mmap_start = mmap.as_ptr() as usize;
        let mmap_end = mmap_start
            .checked_add(mmap.len())
            .ok_or("mmap run event arrow column scan range overflow")?;
        let mut buffer = arrow_buffer_from_mmap(Arc::clone(&mmap))?;
        let mut decoder = trusted_mmap_stream_decoder();
        let mut segment_hasher = run_event_segment_hasher(
            entry.segment_id,
            entry.previous_segment_hash,
            usize::try_from(entry.event_count)
                .map_err(|_| "run event segment count exceeds target width")?,
        );
        let mut event_count = 0usize;
        let mut mmap_buffer_count = 0usize;
        let mut first_seen: Option<(EventId, [u8; 32])> = None;
        let mut last_seen: Option<(EventId, [u8; 32])> = None;

        while !buffer.is_empty() {
            let before = buffer.len();
            if let Some(batch) = decoder
                .decode(&mut buffer)
                .map_err(|_| "failed to decode mmap arrow column scan batch")?
            {
                if batch.schema().as_ref() != &Self::schema() {
                    return Err("invalid mmap run event arrow column scan schema");
                }
                mmap_buffer_count = mmap_buffer_count
                    .checked_add(validate_batch_buffers_within_mmap(
                        &batch, mmap_start, mmap_end,
                    )?)
                    .ok_or("mmap arrow column scan buffer count overflow")?;
                let scanned = scan_arrow_run_event_batch_columns(
                    &batch,
                    run_id,
                    expected_event_id,
                    expected_previous_hash,
                    scan_state,
                    logical_hasher,
                    &mut segment_hasher,
                    &mut first_seen,
                    &mut last_seen,
                )?;
                event_count = event_count
                    .checked_add(scanned)
                    .ok_or("mmap arrow column scan event count overflow")?;
            }
            if buffer.len() == before {
                return Err("mmap arrow column scan decoder made no progress");
            }
        }
        decoder
            .finish()
            .map_err(|_| "failed to finish mmap arrow column scan decoder")?;

        let (first_event_id, first_event_hash) =
            first_seen.ok_or("empty mmap arrow column scan segment")?;
        let (last_event_id, last_event_hash) =
            last_seen.ok_or("empty mmap arrow column scan segment")?;
        let segment_hash = *segment_hasher.finalize().as_bytes();
        Ok(ArrowRunEventMmapColumnScan {
            file_bytes,
            file_hash,
            event_count,
            first_event_id,
            last_event_id,
            first_event_hash,
            last_event_hash,
            segment_hash,
            mmap_buffer_count,
        })
    }

    fn schema() -> Schema {
        Schema::new(vec![
            Field::new("event_id", DataType::UInt64, false),
            Field::new("run_id_hi", DataType::UInt64, false),
            Field::new("run_id_lo", DataType::UInt64, false),
            Field::new("kind", DataType::UInt8, false),
            Field::new("subject_id_hi", DataType::UInt64, false),
            Field::new("subject_id_lo", DataType::UInt64, false),
            Field::new("primary_hash", DataType::Binary, false),
            Field::new("secondary_hash", DataType::Binary, true),
            Field::new("previous_event_hash", DataType::Binary, false),
            Field::new("event_hash", DataType::Binary, false),
        ])
    }

    pub fn schema_hash() -> [u8; 32] {
        arrow_run_event_schema_hash()
    }
}

impl SegmentedArrowAuditStream {
    pub fn create(
        directory: impl AsRef<Path>,
        run_id: RunId,
        max_events_per_segment: usize,
    ) -> Result<Self, &'static str> {
        if run_id == 0
            || max_events_per_segment == 0
            || max_events_per_segment > MAX_EVENTS_PER_SEGMENT
        {
            return Err("invalid segmented arrow audit stream config");
        }
        let directory = directory.as_ref().to_path_buf();
        std::fs::create_dir_all(&directory)
            .map_err(|_| "failed to create segmented arrow audit directory")?;
        let (writer_lock_path, writer_lock) = Self::acquire_writer_lock(&directory, run_id)?;
        Ok(Self {
            directory,
            run_id,
            max_events_per_segment,
            pending_events: Vec::with_capacity(max_events_per_segment),
            entries: Vec::new(),
            previous_segment_hash: [0; 32],
            last_event_hash: [0; 32],
            next_event_id: 1,
            has_context_pack_built: false,
            has_goal_intake_recorded: false,
            has_llm_response_received: false,
            has_pending_tool_call: false,
            has_policy_decision_recorded: false,
            has_tool_completion_recorded: false,
            last_tool_call_id: 0,
            last_tool_call_event_id: 0,
            last_tool_ir_hash: [0; 32],
            last_tool_execution_evidence_hash: [0; 32],
            last_policy_event_id: 0,
            last_policy_proof_trace_hash: [0; 32],
            last_policy_tool_ir_hash: [0; 32],
            writer_lock_path,
            writer_lock: Some(writer_lock),
        })
    }

    fn acquire_writer_lock(
        directory: &Path,
        run_id: RunId,
    ) -> Result<(PathBuf, File), &'static str> {
        let lock_path = Self::writer_lock_path(directory, run_id);
        let mut lock = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&lock_path)
            .map_err(|_| "segmented arrow audit stream already has a writer")?;
        lock.write_all(b"AEGIS-SEGMENTED-ARROW-WRITER-LOCK\n")
            .map_err(|_| "failed to write segmented arrow writer lock")?;
        lock.write_all(&run_id.to_le_bytes())
            .map_err(|_| "failed to bind segmented arrow writer lock")?;
        lock.sync_all()
            .map_err(|_| "failed to sync segmented arrow writer lock")?;
        Ok((lock_path, lock))
    }

    fn writer_lock_path(directory: &Path, run_id: RunId) -> PathBuf {
        directory.join(format!("run-{run_id}.writer.lock"))
    }

    pub fn append_event(&mut self, event: &RunEvent) -> Result<(), &'static str> {
        self.append_events(std::slice::from_ref(event))
    }

    pub fn append_events(&mut self, events: &[RunEvent]) -> Result<(), &'static str> {
        if events.is_empty() {
            return Err("empty segmented arrow audit batch");
        }
        let mut expected_event_id = self.next_event_id;
        let mut expected_previous_hash = self.last_event_hash;
        let mut has_context_pack_built = self.has_context_pack_built;
        let mut has_goal_intake_recorded = self.has_goal_intake_recorded;
        let mut has_llm_response_received = self.has_llm_response_received;
        let mut has_pending_tool_call = self.has_pending_tool_call;
        let mut has_policy_decision_recorded = self.has_policy_decision_recorded;
        let mut has_tool_completion_recorded = self.has_tool_completion_recorded;
        let mut last_tool_call_id = self.last_tool_call_id;
        let mut last_tool_call_event_id = self.last_tool_call_event_id;
        let mut last_tool_ir_hash = self.last_tool_ir_hash;
        let mut last_tool_execution_evidence_hash = self.last_tool_execution_evidence_hash;
        let mut last_policy_event_id = self.last_policy_event_id;
        let mut last_policy_proof_trace_hash = self.last_policy_proof_trace_hash;
        let mut last_policy_tool_ir_hash = self.last_policy_tool_ir_hash;
        for event in events {
            if !event.is_valid()
                || event.run_id != self.run_id
                || event.event_id != expected_event_id
                || event.previous_event_hash != expected_previous_hash
            {
                return Err("invalid segmented arrow audit event sequence");
            }
            if matches!(
                event.kind,
                RunEventKind::ContextPackBuilt
                    | RunEventKind::ContextPackCandidateProofRecorded
                    | RunEventKind::TaskSelectionProofRecorded
                    | RunEventKind::LLMResponseReceived
                    | RunEventKind::ContextFoldRecorded
                    | RunEventKind::ToolCallRequested
                    | RunEventKind::ToolCallCompleted
                    | RunEventKind::PolicyDecisionRecorded
                    | RunEventKind::ApprovalTokenRecorded
                    | RunEventKind::OperatorReviewArtifactRecorded
                    | RunEventKind::ColdVectorExpansionRecorded
                    | RunEventKind::AgenticEvidenceExecutionRecorded
                    | RunEventKind::SkillAdmissionRecorded
                    | RunEventKind::BrowserOpsBenchVerificationRecorded
                    | RunEventKind::ClusterCandidateAccepted
                    | RunEventKind::ShadowSealRecorded
                    | RunEventKind::CircuitBreakerTripped
                    | RunEventKind::MemoryCommitRecorded
                    | RunEventKind::CheckpointSealed
            ) && !has_goal_intake_recorded
            {
                return Err("segmented arrow audit event missing prior goal intake");
            }
            if matches!(
                event.kind,
                RunEventKind::LLMResponseReceived
                    | RunEventKind::ContextFoldRecorded
                    | RunEventKind::ContextPackCandidateProofRecorded
            ) && !has_context_pack_built
            {
                return Err("segmented arrow audit event missing prior context pack");
            }
            if matches!(
                event.kind,
                RunEventKind::ToolCallRequested | RunEventKind::CircuitBreakerTripped
            ) && !has_llm_response_received
            {
                return Err("segmented arrow audit event missing prior llm response");
            }
            if event.kind == RunEventKind::ToolCallCompleted && !has_pending_tool_call {
                return Err("segmented arrow audit event missing prior tool call");
            }
            if event.kind == RunEventKind::MemoryCommitRecorded && !has_tool_completion_recorded {
                return Err("segmented arrow audit memory commit missing tool completion");
            }
            if event.kind == RunEventKind::ToolCallRequested && has_pending_tool_call {
                return Err("segmented arrow audit nested pending tool call");
            }
            if matches!(
                event.kind,
                RunEventKind::ToolCallCompleted
                    | RunEventKind::ApprovalTokenRecorded
                    | RunEventKind::OperatorReviewArtifactRecorded
            ) && !has_policy_decision_recorded
            {
                return Err("segmented arrow audit event missing prior policy decision");
            }
            if event.kind == RunEventKind::ToolCallCompleted {
                let evidence_hash = event
                    .secondary_hash
                    .ok_or("segmented arrow audit missing tool execution evidence hash")?;
                if event.subject_id != last_tool_call_id
                    || last_policy_event_id <= last_tool_call_event_id
                    || last_tool_ir_hash != last_policy_tool_ir_hash
                    || event.primary_hash
                        != tool_call_completion_binding_hash(
                            event.subject_id,
                            last_tool_ir_hash,
                            last_policy_proof_trace_hash,
                            evidence_hash,
                        )
                {
                    return Err("segmented arrow audit tool completion binding mismatch");
                }
            }
            if event.kind == RunEventKind::MemoryCommitRecorded {
                let binding_hash = event
                    .secondary_hash
                    .ok_or("segmented arrow audit missing memory commit binding")?;
                if event.subject_id == 0
                    || has_pending_tool_call
                    || !nonzero_hash(&last_tool_execution_evidence_hash)
                    || binding_hash
                        != memory_commit_binding_hash(
                            event.subject_id,
                            last_tool_execution_evidence_hash,
                            event.primary_hash,
                        )
                {
                    return Err("segmented arrow audit memory commit binding mismatch");
                }
            }

            match event.kind {
                RunEventKind::GoalIntakeRecorded => has_goal_intake_recorded = true,
                RunEventKind::ContextPackBuilt => has_context_pack_built = true,
                RunEventKind::LLMResponseReceived => has_llm_response_received = true,
                RunEventKind::ToolCallRequested => {
                    has_pending_tool_call = true;
                    has_tool_completion_recorded = false;
                    last_tool_call_id = event.subject_id;
                    last_tool_call_event_id = event.event_id;
                    last_tool_ir_hash = event.primary_hash;
                }
                RunEventKind::ToolCallCompleted => {
                    has_pending_tool_call = false;
                    has_tool_completion_recorded = true;
                    last_tool_execution_evidence_hash = event
                        .secondary_hash
                        .ok_or("segmented arrow audit missing tool execution evidence hash")?;
                }
                RunEventKind::PolicyDecisionRecorded => {
                    has_policy_decision_recorded = true;
                    last_policy_event_id = event.event_id;
                    last_policy_proof_trace_hash = event.primary_hash;
                    last_policy_tool_ir_hash = event
                        .secondary_hash
                        .ok_or("segmented arrow audit missing policy typed tool ir hash")?;
                }
                _ => {}
            }
            expected_event_id = expected_event_id.saturating_add(1);
            expected_previous_hash = event.event_hash;
        }

        for event in events {
            self.pending_events.push(event.clone());
            self.last_event_hash = event.event_hash;
            self.next_event_id = event.event_id.saturating_add(1);
            match event.kind {
                RunEventKind::GoalIntakeRecorded => self.has_goal_intake_recorded = true,
                RunEventKind::ContextPackBuilt => self.has_context_pack_built = true,
                RunEventKind::LLMResponseReceived => self.has_llm_response_received = true,
                RunEventKind::ToolCallRequested => {
                    if self.has_pending_tool_call {
                        return Err("segmented arrow audit nested pending tool call");
                    }
                    self.has_pending_tool_call = true;
                    self.has_tool_completion_recorded = false;
                    self.last_tool_call_id = event.subject_id;
                    self.last_tool_call_event_id = event.event_id;
                    self.last_tool_ir_hash = event.primary_hash;
                }
                RunEventKind::ToolCallCompleted => {
                    self.has_pending_tool_call = false;
                    self.has_tool_completion_recorded = true;
                    self.last_tool_execution_evidence_hash = event
                        .secondary_hash
                        .ok_or("segmented arrow audit missing tool execution evidence hash")?;
                }
                RunEventKind::PolicyDecisionRecorded => {
                    self.has_policy_decision_recorded = true;
                    self.last_policy_event_id = event.event_id;
                    self.last_policy_proof_trace_hash = event.primary_hash;
                    self.last_policy_tool_ir_hash = event
                        .secondary_hash
                        .ok_or("segmented arrow audit missing policy typed tool ir hash")?;
                }
                _ => {}
            }
            if self.pending_events.len() == self.max_events_per_segment {
                self.flush_segment()?;
            }
        }
        Ok(())
    }

    pub fn flush(&mut self) -> Result<(), &'static str> {
        if self.pending_events.is_empty() {
            return Ok(());
        }
        self.flush_segment()
    }

    pub fn finish(mut self) -> Result<RunEventSegmentManifest, &'static str> {
        self.flush()?;
        if self.entries.is_empty() {
            return Err("empty segmented arrow audit stream");
        }
        self.release_writer_lock();
        Ok(RunEventSegmentManifest::new(
            self.run_id,
            self.entries.clone(),
        ))
    }

    pub fn segment_count(&self) -> usize {
        self.entries.len()
    }

    pub fn pending_event_count(&self) -> usize {
        self.pending_events.len()
    }

    fn flush_segment(&mut self) -> Result<(), &'static str> {
        if self.pending_events.is_empty() {
            return Err("empty segmented arrow audit segment");
        }
        let segment_id = self.entries.len() as u64 + 1;
        let path = RunEventSegmentArchive::segment_path(&self.directory, self.run_id, segment_id);
        if path.exists() {
            return Err("segmented arrow audit segment already exists");
        }
        let commit_path =
            RunEventSegmentArchive::segment_commit_path(&self.directory, self.run_id, segment_id);
        if commit_path.exists() {
            return Err("segmented arrow audit segment commit already exists");
        }
        let temp_path = synced_artifact_temp_path(&path);
        let write_result = (|| {
            let mut stream = ArrowRunEventStream::create_exclusive(&temp_path)?;
            stream.append_events(&self.pending_events)?;
            stream.finish()
        })();
        if let Err(error) = write_result {
            let _ = std::fs::remove_file(&temp_path);
            return Err(error);
        }
        if path.exists() {
            let _ = std::fs::remove_file(&temp_path);
            return Err("segmented arrow audit segment already exists");
        }
        if let Err(error) = publish_synced_artifact_temp(&temp_path, &path) {
            let _ = std::fs::remove_file(&temp_path);
            return Err(error);
        }
        let parent_directory_sync_attempted = if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            let _ = File::open(parent).and_then(|directory| directory.sync_all());
            true
        } else {
            true
        };
        let (arrow_file_bytes, arrow_file_hash) = arrow_stream_file_evidence(&path)?;
        let entry = RunEventSegmentEntry::from_events(
            segment_id,
            &self.pending_events,
            self.previous_segment_hash,
            arrow_file_bytes,
            arrow_file_hash,
            RunEventSegmentPublishEvidence::synced_temp_publish(parent_directory_sync_attempted),
        )?;
        RunEventSegmentCommitProof::write_sidecar(&commit_path, self.run_id, &entry)?;
        self.previous_segment_hash = entry.segment_hash;
        self.entries.push(entry);
        self.pending_events.clear();
        Ok(())
    }

    fn release_writer_lock(&mut self) {
        if let Some(lock) = self.writer_lock.take() {
            drop(lock);
        }
        if !self.writer_lock_path.as_os_str().is_empty() {
            let _ = std::fs::remove_file(&self.writer_lock_path);
        }
    }
}

impl Drop for SegmentedArrowAuditStream {
    fn drop(&mut self) {
        self.release_writer_lock();
    }
}

impl BinaryRunEventSegment {
    pub fn header_bytes() -> usize {
        BINARY_RUN_EVENT_HEADER_BYTES
    }

    pub fn record_bytes() -> usize {
        BINARY_RUN_EVENT_RECORD_BYTES
    }

    pub fn schema_hash() -> [u8; 32] {
        binary_run_event_schema_hash()
    }

    pub fn write_ledger(
        path: impl AsRef<Path>,
        ledger: &RunEventLedger,
    ) -> Result<[u8; 32], &'static str> {
        if ledger.is_empty() || !ledger.verify_hash_chain() {
            return Err("invalid binary run event ledger");
        }
        let path = path.as_ref();
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent)
                .map_err(|_| "failed to create binary run event segment directory")?;
        }

        let payload_hash = binary_run_event_payload_hash(ledger.events());
        let mut file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(path)
            .map_err(|_| "failed to create binary run event segment")?;
        file.write_all(BINARY_RUN_EVENT_MAGIC)
            .map_err(|_| "failed to write binary run event magic")?;
        file.write_all(&BINARY_RUN_EVENT_FORMAT_VERSION.to_le_bytes())
            .map_err(|_| "failed to write binary run event format version")?;
        file.write_all(&(BINARY_RUN_EVENT_HEADER_BYTES as u64).to_le_bytes())
            .map_err(|_| "failed to write binary run event header size")?;
        file.write_all(&(BINARY_RUN_EVENT_RECORD_BYTES as u64).to_le_bytes())
            .map_err(|_| "failed to write binary run event record size")?;
        file.write_all(&(ledger.events().len() as u64).to_le_bytes())
            .map_err(|_| "failed to write binary run event count")?;
        file.write_all(&ledger.run_id.to_le_bytes())
            .map_err(|_| "failed to write binary run id")?;
        file.write_all(&binary_run_event_schema_hash())
            .map_err(|_| "failed to write binary run event schema hash")?;
        for event in ledger.events() {
            let record = encode_binary_run_event_record(event);
            file.write_all(&record)
                .map_err(|_| "failed to write binary run event record")?;
        }
        file.flush()
            .map_err(|_| "failed to flush binary run event segment")?;
        file.sync_all()
            .map_err(|_| "failed to sync binary run event segment")?;
        Ok(payload_hash)
    }

    pub fn read_ledger_mmap(
        path: impl AsRef<Path>,
        expected_payload_hash: [u8; 32],
    ) -> Result<RunEventLedger, &'static str> {
        let file = File::open(path).map_err(|_| "failed to open binary run event segment")?;
        let file_bytes = usize::try_from(
            file.metadata()
                .map_err(|_| "failed to stat binary run event segment")?
                .len(),
        )
        .map_err(|_| "binary run event segment is too large for this target")?;
        // SAFETY: `file` is opened read-only above (File::open defaults). `file_bytes
        // == file.metadata().len()`, so `MmapOptions::new().map(&file)` produces a
        // mapping exactly that size — it cannot extend past EOF. Read-only map
        // implies non-aliasing: no other `&mut [u8]` to this file exists in the
        // process. The mapping is stored in `RunEventLedger` and accessed via
        // bounds-checked slicing inside the same module (`&mmap[start..end]`).
        let mmap = unsafe {
            MmapOptions::new()
                .map(&file)
                .map_err(|_| "failed to mmap binary run event segment")?
        };
        if mmap.len() != file_bytes || mmap.len() < BINARY_RUN_EVENT_HEADER_BYTES {
            return Err("invalid binary run event segment length");
        }
        if &mmap[0..8] != BINARY_RUN_EVENT_MAGIC {
            return Err("invalid binary run event segment magic");
        }
        let format_version = read_u64_le(&mmap, 8)?;
        if format_version != BINARY_RUN_EVENT_FORMAT_VERSION {
            return Err("invalid binary run event format version");
        }
        let header_bytes = read_usize_le(&mmap, 16)?;
        if header_bytes != BINARY_RUN_EVENT_HEADER_BYTES {
            return Err("invalid binary run event header size");
        }
        let record_bytes = read_usize_le(&mmap, 24)?;
        if record_bytes != BINARY_RUN_EVENT_RECORD_BYTES {
            return Err("invalid binary run event record size");
        }
        let event_count = read_usize_le(&mmap, 32)?;
        let run_id = read_u128_le(&mmap, 40)?;
        if hash_from_slice(&mmap[56..88])? != binary_run_event_schema_hash() {
            return Err("invalid binary run event schema hash");
        }
        let expected_len = BINARY_RUN_EVENT_HEADER_BYTES
            .checked_add(
                event_count
                    .checked_mul(BINARY_RUN_EVENT_RECORD_BYTES)
                    .ok_or("binary run event length overflow")?,
            )
            .ok_or("binary run event length overflow")?;
        if expected_len != mmap.len() {
            return Err("invalid binary run event segment length");
        }

        let mut payload_hasher = binary_run_event_payload_hasher(event_count);
        let mut events = Vec::with_capacity(event_count);
        let mut offset = BINARY_RUN_EVENT_HEADER_BYTES;
        for _ in 0..event_count {
            let record = &mmap[offset..offset + record_bytes];
            events.push(decode_binary_run_event_record(record)?);
            payload_hasher.update(record);
            offset += record_bytes;
        }
        if *payload_hasher.finalize().as_bytes() != expected_payload_hash {
            return Err("binary run event payload hash mismatch");
        }
        RunEventLedger::from_events(run_id, events).map_err(|_| "invalid binary run event ledger")
    }

    pub fn verify_mmap(
        path: impl AsRef<Path>,
        expected_payload_hash: [u8; 32],
    ) -> Result<BinaryRunEventScanReport, &'static str> {
        let file = File::open(path).map_err(|_| "failed to open binary run event segment")?;
        let file_bytes = file
            .metadata()
            .map_err(|_| "failed to stat binary run event segment")?
            .len();
        // SAFETY: identical invariants to the binary run-event ledger mmap at
        // line 3166+. Read-only open + metadata-driven size + read-only `map` =
        // non-extending, non-aliasing mapping. `verify_mmap` is a read-only
        // verification function called at audit time; no concurrent writer exists.
        let mmap = unsafe {
            MmapOptions::new()
                .map(&file)
                .map_err(|_| "failed to mmap binary run event segment")?
        };
        if u64::try_from(mmap.len()).map_err(|_| "binary run event length exceeds u64")?
            != file_bytes
            || mmap.len() < BINARY_RUN_EVENT_HEADER_BYTES
        {
            return Err("invalid binary run event segment length");
        }
        if &mmap[0..8] != BINARY_RUN_EVENT_MAGIC {
            return Err("invalid binary run event segment magic");
        }
        let format_version = read_u64_le(&mmap, 8)?;
        if format_version != BINARY_RUN_EVENT_FORMAT_VERSION {
            return Err("invalid binary run event format version");
        }
        let header_bytes = read_usize_le(&mmap, 16)?;
        if header_bytes != BINARY_RUN_EVENT_HEADER_BYTES {
            return Err("invalid binary run event header size");
        }
        let record_bytes = read_usize_le(&mmap, 24)?;
        if record_bytes != BINARY_RUN_EVENT_RECORD_BYTES {
            return Err("invalid binary run event record size");
        }
        let event_count = read_usize_le(&mmap, 32)?;
        let run_id = read_u128_le(&mmap, 40)?;
        if hash_from_slice(&mmap[56..88])? != binary_run_event_schema_hash() {
            return Err("invalid binary run event schema hash");
        }
        let expected_len = BINARY_RUN_EVENT_HEADER_BYTES
            .checked_add(
                event_count
                    .checked_mul(BINARY_RUN_EVENT_RECORD_BYTES)
                    .ok_or("binary run event length overflow")?,
            )
            .ok_or("binary run event length overflow")?;
        if expected_len != mmap.len() {
            return Err("invalid binary run event segment length");
        }

        let mut payload_hasher = binary_run_event_payload_hasher(event_count);
        let mut scan_state = BinaryRunEventScanState::default();
        let mut previous_event_hash = [0; 32];
        let mut offset = BINARY_RUN_EVENT_HEADER_BYTES;
        for index in 0..event_count {
            let record = &mmap[offset..offset + record_bytes];
            let event_hash = validate_binary_run_event_record(
                record,
                run_id,
                index as EventId + 1,
                previous_event_hash,
                &mut scan_state,
            )?;
            payload_hasher.update(record);
            previous_event_hash = event_hash;
            offset += record_bytes;
        }
        let payload_hash = *payload_hasher.finalize().as_bytes();
        if payload_hash != expected_payload_hash {
            return Err("binary run event payload hash mismatch");
        }
        Ok(BinaryRunEventScanReport {
            event_count,
            file_bytes,
            last_event_hash: previous_event_hash,
            payload_hash,
        })
    }

    pub fn recover_last_valid_prefix_mmap(
        path: impl AsRef<Path>,
    ) -> Result<BinaryRunEventRecoveryReport, &'static str> {
        let file = File::open(path).map_err(|_| "failed to open binary run event segment")?;
        let file_bytes = file
            .metadata()
            .map_err(|_| "failed to stat binary run event segment")?
            .len();
        // SAFETY: identical invariants to the binary run-event ledger mmap at
        // line 3166+ and `verify_mmap` at line 3235+: read-only `File::open`,
        // metadata-driven size, read-only `MmapOptions::map`. Recovery scans
        // append-only history; no concurrent writer holds a mutable borrow of
        // the file. The mapping is consumed linearily by the recovery scan
        // before being dropped at end of `recover_last_valid_prefix_mmap`.
        let mmap = unsafe {
            MmapOptions::new()
                .map(&file)
                .map_err(|_| "failed to mmap binary run event segment")?
        };
        if u64::try_from(mmap.len()).map_err(|_| "binary run event length exceeds u64")?
            != file_bytes
            || mmap.len() < BINARY_RUN_EVENT_HEADER_BYTES
        {
            return Err("invalid binary run event segment length");
        }
        if &mmap[0..8] != BINARY_RUN_EVENT_MAGIC {
            return Err("invalid binary run event segment magic");
        }
        let format_version = read_u64_le(&mmap, 8)?;
        if format_version != BINARY_RUN_EVENT_FORMAT_VERSION {
            return Err("invalid binary run event format version");
        }
        let header_bytes = read_usize_le(&mmap, 16)?;
        if header_bytes != BINARY_RUN_EVENT_HEADER_BYTES {
            return Err("invalid binary run event header size");
        }
        let record_bytes = read_usize_le(&mmap, 24)?;
        if record_bytes != BINARY_RUN_EVENT_RECORD_BYTES {
            return Err("invalid binary run event record size");
        }
        let declared_event_count = read_usize_le(&mmap, 32)?;
        let run_id = read_u128_le(&mmap, 40)?;
        if hash_from_slice(&mmap[56..88])? != binary_run_event_schema_hash() {
            return Err("invalid binary run event schema hash");
        }

        let available_payload_bytes = mmap.len().saturating_sub(BINARY_RUN_EVENT_HEADER_BYTES);
        let complete_record_count =
            (available_payload_bytes / BINARY_RUN_EVENT_RECORD_BYTES).min(declared_event_count);
        let trailing_partial_bytes = available_payload_bytes % BINARY_RUN_EVENT_RECORD_BYTES;

        let mut scan_state = BinaryRunEventScanState::default();
        let mut previous_event_hash = [0; 32];
        let mut events = Vec::with_capacity(complete_record_count);
        let mut offset = BINARY_RUN_EVENT_HEADER_BYTES;
        for index in 0..complete_record_count {
            let record = &mmap[offset..offset + record_bytes];
            let event_hash = match validate_binary_run_event_record(
                record,
                run_id,
                index as EventId + 1,
                previous_event_hash,
                &mut scan_state,
            ) {
                Ok(event_hash) => event_hash,
                Err(_) => break,
            };
            events.push(decode_binary_run_event_record(record)?);
            previous_event_hash = event_hash;
            offset += record_bytes;
        }

        let recovered_payload_hash = binary_run_event_payload_hash(&events);
        let last_valid_event_id = events.last().map_or(0, |event| event.event_id);
        let ledger = RunEventLedger::from_events(run_id, events)
            .map_err(|_| "invalid binary run event recovery ledger")?;
        let recovered_event_count = ledger.len();
        let recovery_hash = binary_run_event_recovery_hash(
            run_id,
            declared_event_count,
            recovered_event_count,
            file_bytes,
            trailing_partial_bytes,
            last_valid_event_id,
            previous_event_hash,
            recovered_payload_hash,
        );

        Ok(BinaryRunEventRecoveryReport {
            ledger,
            declared_event_count,
            recovered_event_count,
            file_bytes,
            trailing_partial_bytes,
            last_valid_event_id,
            last_event_hash: previous_event_hash,
            recovered_payload_hash,
            recovery_hash,
        })
    }
}

fn events_from_reader<R: std::io::Read>(
    reader: StreamReader<R>,
) -> Result<Vec<RunEvent>, &'static str> {
    events_from_reader_with_capacity(reader, 0)
}

fn events_from_reader_with_capacity<R: std::io::Read>(
    reader: StreamReader<R>,
    expected_event_count: usize,
) -> Result<Vec<RunEvent>, &'static str> {
    let mut events = Vec::with_capacity(expected_event_count);
    append_events_from_reader(reader, &mut events, true)?;
    Ok(events)
}

fn append_events_from_reader<R: std::io::Read>(
    reader: StreamReader<R>,
    events: &mut Vec<RunEvent>,
    validate_events: bool,
) -> Result<(), &'static str> {
    for batch in reader {
        let batch = batch.map_err(|_| "failed to read run event batch")?;
        append_events_from_batch(&batch, events, validate_events)?;
    }
    Ok(())
}

fn trusted_mmap_stream_decoder() -> StreamDecoder {
    StreamDecoder::new().with_require_alignment(true)
}

fn append_events_from_batch(
    batch: &RecordBatch,
    events: &mut Vec<RunEvent>,
    validate_events: bool,
) -> Result<(), &'static str> {
    let event_ids = downcast_u64(batch, 0)?;
    let run_id_hi = downcast_u64(batch, 1)?;
    let run_id_lo = downcast_u64(batch, 2)?;
    let kinds = downcast_u8(batch, 3)?;
    let subject_id_hi = downcast_u64(batch, 4)?;
    let subject_id_lo = downcast_u64(batch, 5)?;
    let primary_hashes = downcast_binary(batch, 6)?;
    let secondary_hashes = downcast_binary(batch, 7)?;
    let previous_hashes = downcast_binary(batch, 8)?;
    let event_hashes = downcast_binary(batch, 9)?;

    events.reserve(batch.num_rows());
    for row in 0..batch.num_rows() {
        let secondary_hash = if secondary_hashes.is_null(row) {
            None
        } else {
            Some(hash_from_binary(secondary_hashes.value(row))?)
        };
        let event = RunEvent {
            event_id: event_ids.value(row),
            run_id: join_u128(run_id_hi.value(row), run_id_lo.value(row)),
            kind: RunEventKind::from_u8(kinds.value(row)).ok_or("invalid run event kind")?,
            subject_id: join_u128(subject_id_hi.value(row), subject_id_lo.value(row)),
            primary_hash: hash_from_binary(primary_hashes.value(row))?,
            secondary_hash,
            previous_event_hash: hash_from_binary(previous_hashes.value(row))?,
            event_hash: hash_from_binary(event_hashes.value(row))?,
        };
        if validate_events && !event.is_valid() {
            return Err("invalid persisted run event");
        }
        events.push(event);
    }
    Ok(())
}

impl RunEventSegmentEntry {
    fn from_events(
        segment_id: u64,
        events: &[RunEvent],
        previous_segment_hash: [u8; 32],
        arrow_file_bytes: u64,
        arrow_file_hash: [u8; 32],
        publish_evidence: RunEventSegmentPublishEvidence,
    ) -> Result<Self, &'static str> {
        let first = events.first().ok_or("empty run event segment")?;
        let last = events.last().ok_or("empty run event segment")?;
        if arrow_file_bytes == 0 || !nonzero_hash(&arrow_file_hash) {
            return Err("invalid run event arrow segment file evidence");
        }
        let segment_hash = run_event_segment_hash(segment_id, events, previous_segment_hash);
        let mut entry = Self {
            segment_id,
            start_event_id: first.event_id,
            end_event_id: last.event_id,
            event_count: events.len() as u64,
            first_event_hash: first.event_hash,
            last_event_hash: last.event_hash,
            segment_hash,
            previous_segment_hash,
            arrow_schema_hash: ArrowRunEventStream::schema_hash(),
            arrow_file_bytes,
            arrow_file_hash,
            segment_commit_hash: [0; 32],
            staged_temp_file_used: publish_evidence.staged_temp_file_used,
            temp_file_synced_before_publish: publish_evidence.temp_file_synced_before_publish,
            publish_completed: publish_evidence.publish_completed,
            parent_directory_sync_attempted: publish_evidence.parent_directory_sync_attempted,
        };
        entry.refresh_segment_commit_hash(first.run_id);
        Ok(entry)
    }

    fn matches_events(&self, events: &[RunEvent]) -> bool {
        self.matches_events_after_manifest_validation(events)
            && self.segment_commit_hash
                == RunEventSegmentCommitProof::from_entry(
                    events.first().map_or(0, |event| event.run_id),
                    self,
                )
                .commit_hash
    }

    fn matches_events_after_manifest_validation(&self, events: &[RunEvent]) -> bool {
        events.first().is_some_and(|event| {
            event.event_id == self.start_event_id && event.event_hash == self.first_event_hash
        }) && events.last().is_some_and(|event| {
            event.event_id == self.end_event_id && event.event_hash == self.last_event_hash
        }) && self.event_count == events.len() as u64
            && self.segment_hash
                == run_event_segment_hash(self.segment_id, events, self.previous_segment_hash)
            && self.arrow_schema_hash == ArrowRunEventStream::schema_hash()
            && self.arrow_file_bytes > 0
            && nonzero_hash(&self.arrow_file_hash)
            && self.staged_temp_file_used
            && self.temp_file_synced_before_publish
            && self.publish_completed
            && self.parent_directory_sync_attempted
    }

    fn matches_cached_events(&self, events: &[RunEvent]) -> bool {
        events.first().is_some_and(|event| {
            event.event_id == self.start_event_id && event.event_hash == self.first_event_hash
        }) && events.last().is_some_and(|event| {
            event.event_id == self.end_event_id && event.event_hash == self.last_event_hash
        }) && self.event_count == events.len() as u64
            && self.arrow_schema_hash == ArrowRunEventStream::schema_hash()
            && self.arrow_file_bytes > 0
            && nonzero_hash(&self.arrow_file_hash)
            && nonzero_hash(&self.segment_hash)
            && nonzero_hash(&self.segment_commit_hash)
            && self.staged_temp_file_used
            && self.temp_file_synced_before_publish
            && self.publish_completed
            && self.parent_directory_sync_attempted
    }

    fn matches_arrow_file(&self, path: &Path) -> bool {
        arrow_stream_file_evidence(path).is_ok_and(|(file_bytes, file_hash)| {
            self.arrow_file_bytes == file_bytes && self.arrow_file_hash == file_hash
        })
    }

    fn matches_commit_file(&self, path: &Path, run_id: RunId) -> bool {
        RunEventSegmentCommitProof::read_sidecar(path)
            .is_ok_and(|proof| proof.run_id == run_id && proof.binds_entry_fields(self))
    }

    fn refresh_segment_commit_hash(&mut self, run_id: RunId) {
        self.segment_commit_hash = RunEventSegmentCommitProof::from_entry(run_id, self).commit_hash;
    }

    fn publish_evidence(&self) -> RunEventSegmentPublishEvidence {
        RunEventSegmentPublishEvidence {
            staged_temp_file_used: self.staged_temp_file_used,
            temp_file_synced_before_publish: self.temp_file_synced_before_publish,
            publish_completed: self.publish_completed,
            parent_directory_sync_attempted: self.parent_directory_sync_attempted,
        }
    }
}

impl RunEventSegmentManifest {
    pub fn new(run_id: RunId, mut entries: Vec<RunEventSegmentEntry>) -> Self {
        for entry in &mut entries {
            entry.refresh_segment_commit_hash(run_id);
        }
        let manifest_hash = run_event_manifest_hash(run_id, &entries);
        Self {
            run_id,
            entries,
            manifest_hash,
        }
    }

    pub(crate) fn legacy_manifest_hash(&self) -> [u8; 32] {
        run_event_manifest_hash_legacy(self.run_id, &self.entries)
    }

    pub fn is_valid(&self) -> bool {
        self.manifest_hash == run_event_manifest_hash(self.run_id, &self.entries)
            && self
                .entries
                .iter()
                .enumerate()
                .try_fold(
                    ([0; 32], 0u64),
                    |(previous_hash, previous_end), (index, entry)| {
                        let expected_segment_id = index as u64 + 1;
                        let start_ok = index == 0
                            || previous_end
                                .checked_add(1)
                                .is_some_and(|next| entry.start_event_id == next);
                        (entry.segment_id == expected_segment_id
                            && entry.previous_segment_hash == previous_hash
                            && entry.arrow_schema_hash == ArrowRunEventStream::schema_hash()
                            && entry.arrow_file_bytes > 0
                            && nonzero_hash(&entry.arrow_file_hash)
                            && entry.segment_commit_hash
                                == RunEventSegmentCommitProof::from_entry(self.run_id, entry)
                                    .commit_hash
                            && entry.staged_temp_file_used
                            && entry.temp_file_synced_before_publish
                            && entry.publish_completed
                            && entry.parent_directory_sync_attempted
                            && entry.start_event_id <= entry.end_event_id
                            && entry.event_count == entry.end_event_id - entry.start_event_id + 1
                            && usize::try_from(entry.event_count).is_ok()
                            && start_ok)
                            .then_some((entry.segment_hash, entry.end_event_id))
                    },
                )
                .is_some()
    }
}

impl SegmentedArrowAuditProof {
    pub fn is_valid(&self) -> bool {
        self.run_id != 0
            && self.segment_count > 0
            && self.event_count > 0
            && self.event_count as u64 == self.last_event_id - self.first_event_id + 1
            && self.total_arrow_file_bytes > 0
            && self.mmap_buffer_count > 0
            && self.first_event_id > 0
            && self.first_event_id <= self.last_event_id
            && self.arrow_schema_hash == ArrowRunEventStream::schema_hash()
            && nonzero_hash(&self.manifest_hash)
            && nonzero_hash(&self.segment_chain_hash)
            && nonzero_hash(&self.segment_witness_hash)
            && nonzero_hash(&self.file_evidence_hash)
            && nonzero_hash(&self.logical_replay_hash)
            && nonzero_hash(&self.mmap_evidence_hash)
            && self.semantic_scan_hash == segmented_arrow_semantic_scan_hash(self)
            && nonzero_hash(&self.semantic_scan_hash)
            && self.proof_hash == segmented_arrow_audit_proof_hash(self)
            && nonzero_hash(&self.proof_hash)
    }
}

impl ReplayDeterminismProof {
    pub fn is_valid(&self) -> bool {
        self.run_id != 0
            && self.segment_count > 0
            && self.event_count > 0
            && self.arrow_schema_hash == ArrowRunEventStream::schema_hash()
            && nonzero_hash(&self.manifest_hash)
            && nonzero_hash(&self.first_pass_event_sequence_hash)
            && self.first_pass_event_sequence_hash == self.second_pass_event_sequence_hash
            && nonzero_hash(&self.first_pass_ledger_hash)
            && self.first_pass_ledger_hash == self.second_pass_ledger_hash
            && nonzero_hash(&self.first_pass_mmap_evidence_hash)
            && self.first_pass_mmap_evidence_hash == self.second_pass_mmap_evidence_hash
            && nonzero_hash(&self.commit_sidecar_evidence_hash)
            && self.proof_hash == replay_determinism_proof_hash(self)
            && nonzero_hash(&self.proof_hash)
    }
}

impl RunCheckpoint {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        run_id: RunId,
        checkpoint_id: SubjectId,
        event_count: usize,
        segment_count: usize,
        ledger_last_hash: [u8; 32],
        manifest_hash: [u8; 32],
        replay_determinism_proof_hash: [u8; 32],
        context_fold_evidence_hash: [u8; 32],
    ) -> Self {
        let checkpoint_hash = run_checkpoint_hash(
            run_id,
            checkpoint_id,
            event_count,
            segment_count,
            ledger_last_hash,
            manifest_hash,
            replay_determinism_proof_hash,
            context_fold_evidence_hash,
        );
        Self {
            run_id,
            checkpoint_id,
            event_count,
            segment_count,
            ledger_last_hash,
            manifest_hash,
            replay_determinism_proof_hash,
            context_fold_evidence_hash,
            checkpoint_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.run_id != 0
            && self.checkpoint_id != 0
            && self.event_count > 0
            && self.segment_count > 0
            && nonzero_hash(&self.ledger_last_hash)
            && nonzero_hash(&self.manifest_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.context_fold_evidence_hash)
            && self.checkpoint_hash
                == run_checkpoint_hash(
                    self.run_id,
                    self.checkpoint_id,
                    self.event_count,
                    self.segment_count,
                    self.ledger_last_hash,
                    self.manifest_hash,
                    self.replay_determinism_proof_hash,
                    self.context_fold_evidence_hash,
                )
            && nonzero_hash(&self.checkpoint_hash)
    }
}

impl NextActionPacket {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        run_id: RunId,
        packet_id: SubjectId,
        checkpoint_hash: [u8; 32],
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
        candidate_evidence_hash: [u8; 32],
    ) -> Self {
        Self::new_with_optional_task_selection(
            run_id,
            packet_id,
            checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            candidate_evidence_hash,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn new_with_task_selection(
        run_id: RunId,
        packet_id: SubjectId,
        checkpoint_hash: [u8; 32],
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
        candidate_evidence_hash: [u8; 32],
        proof: &TaskSelectionProof,
    ) -> Self {
        Self::new_with_optional_task_selection(
            run_id,
            packet_id,
            checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            candidate_evidence_hash,
            Some(proof.proof_hash),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn new_with_optional_task_selection(
        run_id: RunId,
        packet_id: SubjectId,
        checkpoint_hash: [u8; 32],
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
        candidate_evidence_hash: [u8; 32],
        task_selection_proof_hash: Option<[u8; 32]>,
    ) -> Self {
        let packet_hash = next_action_packet_hash(
            run_id,
            packet_id,
            checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            candidate_evidence_hash,
            task_selection_proof_hash,
        );
        Self {
            run_id,
            packet_id,
            checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            candidate_evidence_hash,
            task_selection_proof_hash,
            packet_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.run_id != 0
            && self.packet_id != 0
            && self.task_id != 0
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.typed_tool_ir_hash)
            && nonzero_hash(&self.evidence_contract_hash)
            && nonzero_hash(&self.policy_proof_hash)
            && nonzero_hash(&self.candidate_evidence_hash)
            && self
                .task_selection_proof_hash
                .is_none_or(|hash| nonzero_hash(&hash))
            && self.packet_hash
                == next_action_packet_hash(
                    self.run_id,
                    self.packet_id,
                    self.checkpoint_hash,
                    self.action_kind,
                    self.task_id,
                    self.typed_tool_ir_hash,
                    self.evidence_contract_hash,
                    self.policy_proof_hash,
                    self.candidate_evidence_hash,
                    self.task_selection_proof_hash,
                )
            && nonzero_hash(&self.packet_hash)
    }

    pub fn is_valid_for_checkpoint(&self, checkpoint: &RunCheckpoint) -> bool {
        self.is_valid()
            && checkpoint.is_valid()
            && self.run_id == checkpoint.run_id
            && self.checkpoint_hash == checkpoint.checkpoint_hash
    }

    pub fn is_valid_for_task_selection(
        &self,
        checkpoint: &RunCheckpoint,
        proof: &TaskSelectionProof,
    ) -> bool {
        self.is_valid_for_checkpoint(checkpoint)
            && proof.has_valid_fields()
            && self.task_id == proof.selected_task_id
            && self.task_selection_proof_hash == Some(proof.proof_hash)
    }
}

impl MemoryCommitHandoffProof {
    pub fn new(
        memory_commit_event: &RunEvent,
        tool_execution_evidence_hash: [u8; 32],
        memory_commit_proof: &ToolMemoryCommitProof,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> Self {
        let handoff_hash = memory_commit_handoff_proof_hash(
            memory_commit_event.run_id,
            memory_commit_proof.session_id,
            memory_commit_event.event_id,
            memory_commit_event.event_hash,
            tool_execution_evidence_hash,
            memory_commit_proof.proof_hash,
            replay_determinism_proof.proof_hash,
            checkpoint.checkpoint_hash,
            next_action_packet.packet_hash,
        );
        Self {
            run_id: memory_commit_event.run_id,
            session_id: memory_commit_proof.session_id,
            memory_commit_event_id: memory_commit_event.event_id,
            memory_commit_event_hash: memory_commit_event.event_hash,
            tool_execution_evidence_hash,
            memory_commit_proof_hash: memory_commit_proof.proof_hash,
            replay_determinism_proof_hash: replay_determinism_proof.proof_hash,
            checkpoint_hash: checkpoint.checkpoint_hash,
            next_action_packet_hash: next_action_packet.packet_hash,
            handoff_hash,
        }
    }

    pub fn is_valid_for(
        &self,
        memory_commit_event: &RunEvent,
        memory_commit_proof: &ToolMemoryCommitProof,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> bool {
        self.run_id != 0
            && self.session_id != 0
            && self.memory_commit_event_id > 0
            && nonzero_hash(&self.memory_commit_event_hash)
            && nonzero_hash(&self.tool_execution_evidence_hash)
            && nonzero_hash(&self.memory_commit_proof_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.handoff_hash)
            && memory_commit_event.is_valid()
            && memory_commit_event.kind == RunEventKind::MemoryCommitRecorded
            && memory_commit_event.run_id == self.run_id
            && memory_commit_event.subject_id == self.session_id
            && memory_commit_event.event_id == self.memory_commit_event_id
            && memory_commit_event.event_hash == self.memory_commit_event_hash
            && memory_commit_event.primary_hash == self.memory_commit_proof_hash
            && memory_commit_event.secondary_hash
                == Some(memory_commit_binding_hash(
                    self.session_id,
                    self.tool_execution_evidence_hash,
                    self.memory_commit_proof_hash,
                ))
            && memory_commit_proof.has_valid_fields()
            && memory_commit_proof.session_id == self.session_id
            && memory_commit_proof.proof_hash == self.memory_commit_proof_hash
            && replay_determinism_proof.is_valid()
            && replay_determinism_proof.run_id == self.run_id
            && replay_determinism_proof.proof_hash == self.replay_determinism_proof_hash
            && (self.memory_commit_event_id as usize) <= replay_determinism_proof.event_count
            && checkpoint.is_valid()
            && checkpoint.run_id == self.run_id
            && checkpoint.event_count == replay_determinism_proof.event_count
            && checkpoint.segment_count == replay_determinism_proof.segment_count
            && checkpoint.ledger_last_hash == replay_determinism_proof.first_pass_ledger_hash
            && checkpoint.manifest_hash == replay_determinism_proof.manifest_hash
            && checkpoint.replay_determinism_proof_hash == replay_determinism_proof.proof_hash
            && checkpoint.context_fold_evidence_hash == memory_commit_event.event_hash
            && checkpoint.checkpoint_hash == self.checkpoint_hash
            && next_action_packet.is_valid_for_checkpoint(checkpoint)
            && next_action_packet.run_id == self.run_id
            && next_action_packet.candidate_evidence_hash == memory_commit_event.event_hash
            && next_action_packet.packet_hash == self.next_action_packet_hash
            && self.handoff_hash
                == memory_commit_handoff_proof_hash(
                    self.run_id,
                    self.session_id,
                    self.memory_commit_event_id,
                    self.memory_commit_event_hash,
                    self.tool_execution_evidence_hash,
                    self.memory_commit_proof_hash,
                    self.replay_determinism_proof_hash,
                    self.checkpoint_hash,
                    self.next_action_packet_hash,
                )
    }
}

impl SkillAdmissionHandoffProof {
    pub fn new(
        skill_admission_event: &RunEvent,
        admission: &SkillAdmissionRecord,
        admitted: &AdmittedSkillRecord,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> Self {
        let activation_hash = skill_activation_hash(
            skill_admission_event.run_id,
            admitted.skill_id,
            skill_admission_event.event_hash,
            admission.package_hash,
            admission.admission_hash,
            admitted.registry_epoch,
            admitted.registry_commit_hash,
            next_action_packet.packet_hash,
        );
        let handoff_hash = skill_admission_handoff_proof_hash(
            skill_admission_event.run_id,
            admitted.skill_id,
            skill_admission_event.event_id,
            skill_admission_event.event_hash,
            admission.package_hash,
            admission.admission_hash,
            admitted.registry_epoch,
            admitted.registry_commit_hash,
            replay_determinism_proof.proof_hash,
            checkpoint.checkpoint_hash,
            next_action_packet.packet_hash,
            activation_hash,
        );
        Self {
            run_id: skill_admission_event.run_id,
            skill_id: admitted.skill_id,
            skill_admission_event_id: skill_admission_event.event_id,
            skill_admission_event_hash: skill_admission_event.event_hash,
            package_hash: admission.package_hash,
            admission_hash: admission.admission_hash,
            registry_epoch: admitted.registry_epoch,
            registry_commit_hash: admitted.registry_commit_hash,
            replay_determinism_proof_hash: replay_determinism_proof.proof_hash,
            checkpoint_hash: checkpoint.checkpoint_hash,
            next_action_packet_hash: next_action_packet.packet_hash,
            activation_hash,
            handoff_hash,
        }
    }

    pub fn is_valid_for(
        &self,
        skill_admission_event: &RunEvent,
        admission: &SkillAdmissionRecord,
        admitted: &AdmittedSkillRecord,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> bool {
        self.run_id != 0
            && self.skill_id != 0
            && self.skill_admission_event_id > 0
            && nonzero_hash(&self.skill_admission_event_hash)
            && nonzero_hash(&self.package_hash)
            && nonzero_hash(&self.admission_hash)
            && self.registry_epoch > 0
            && nonzero_hash(&self.registry_commit_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.activation_hash)
            && nonzero_hash(&self.handoff_hash)
            && admission.has_valid_fields()
            && admitted.has_valid_fields()
            && admitted.skill_id == self.skill_id
            && admitted.package_hash == self.package_hash
            && admitted.admission_hash == self.admission_hash
            && admitted.registry_epoch == self.registry_epoch
            && admitted.registry_commit_hash == self.registry_commit_hash
            && admission.package_hash == self.package_hash
            && admission.admission_hash == self.admission_hash
            && skill_admission_event.is_valid()
            && skill_admission_event.kind == RunEventKind::SkillAdmissionRecorded
            && skill_admission_event.run_id == self.run_id
            && skill_admission_event.subject_id == self.skill_id
            && skill_admission_event.event_id == self.skill_admission_event_id
            && skill_admission_event.event_hash == self.skill_admission_event_hash
            && skill_admission_event.primary_hash == self.registry_commit_hash
            && skill_admission_event.secondary_hash
                == Some(skill_admission_replay_binding_hash(admission, admitted))
            && replay_determinism_proof.is_valid()
            && replay_determinism_proof.run_id == self.run_id
            && replay_determinism_proof.proof_hash == self.replay_determinism_proof_hash
            && (self.skill_admission_event_id as usize) <= replay_determinism_proof.event_count
            && checkpoint.is_valid()
            && checkpoint.run_id == self.run_id
            && checkpoint.event_count == replay_determinism_proof.event_count
            && checkpoint.segment_count == replay_determinism_proof.segment_count
            && checkpoint.ledger_last_hash == replay_determinism_proof.first_pass_ledger_hash
            && checkpoint.manifest_hash == replay_determinism_proof.manifest_hash
            && checkpoint.replay_determinism_proof_hash == replay_determinism_proof.proof_hash
            && checkpoint.context_fold_evidence_hash == self.skill_admission_event_hash
            && checkpoint.checkpoint_hash == self.checkpoint_hash
            && next_action_packet.is_valid_for_checkpoint(checkpoint)
            && next_action_packet.run_id == self.run_id
            && next_action_packet.candidate_evidence_hash == self.skill_admission_event_hash
            && next_action_packet.packet_hash == self.next_action_packet_hash
            && self.activation_hash
                == skill_activation_hash(
                    self.run_id,
                    self.skill_id,
                    self.skill_admission_event_hash,
                    self.package_hash,
                    self.admission_hash,
                    self.registry_epoch,
                    self.registry_commit_hash,
                    self.next_action_packet_hash,
                )
            && self.handoff_hash
                == skill_admission_handoff_proof_hash(
                    self.run_id,
                    self.skill_id,
                    self.skill_admission_event_id,
                    self.skill_admission_event_hash,
                    self.package_hash,
                    self.admission_hash,
                    self.registry_epoch,
                    self.registry_commit_hash,
                    self.replay_determinism_proof_hash,
                    self.checkpoint_hash,
                    self.next_action_packet_hash,
                    self.activation_hash,
                )
    }

    pub fn has_valid_activation_fields(&self) -> bool {
        self.run_id != 0
            && self.skill_id != 0
            && self.skill_admission_event_id > 0
            && nonzero_hash(&self.skill_admission_event_hash)
            && nonzero_hash(&self.package_hash)
            && nonzero_hash(&self.admission_hash)
            && self.registry_epoch > 0
            && nonzero_hash(&self.registry_commit_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.activation_hash)
            && nonzero_hash(&self.handoff_hash)
            && self.activation_hash
                == skill_activation_hash(
                    self.run_id,
                    self.skill_id,
                    self.skill_admission_event_hash,
                    self.package_hash,
                    self.admission_hash,
                    self.registry_epoch,
                    self.registry_commit_hash,
                    self.next_action_packet_hash,
                )
            && self.handoff_hash
                == skill_admission_handoff_proof_hash(
                    self.run_id,
                    self.skill_id,
                    self.skill_admission_event_id,
                    self.skill_admission_event_hash,
                    self.package_hash,
                    self.admission_hash,
                    self.registry_epoch,
                    self.registry_commit_hash,
                    self.replay_determinism_proof_hash,
                    self.checkpoint_hash,
                    self.next_action_packet_hash,
                    self.activation_hash,
                )
    }
}

impl RunEventCompactionReport {
    pub fn is_valid(&self) -> bool {
        self.run_id != 0
            && self.source_segment_count > 0
            && self.source_event_count > 0
            && self.source_event_count == self.compacted_event_count
            && self.compacted_file_bytes > 0
            && self.staged_temp_file_used
            && self.temp_file_synced_before_publish
            && self.publish_completed
            && self.parent_directory_sync_attempted
            && self.mmap_scan_verified
            && nonzero_hash(&self.source_manifest_hash)
            && nonzero_hash(&self.source_audit_proof_hash)
            && nonzero_hash(&self.source_mmap_evidence_hash)
            && nonzero_hash(&self.compacted_payload_hash)
            && nonzero_hash(&self.compacted_last_event_hash)
            && self.report_hash == run_event_compaction_report_hash(self)
            && nonzero_hash(&self.report_hash)
    }
}

impl BrowserOpsBenchVerificationHandoffProof {
    pub fn new(
        verification_event: &RunEvent,
        proof: &BrowserOpsBenchVerificationProof,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> Self {
        let handoff_hash = browser_ops_bench_verification_handoff_proof_hash(
            verification_event.run_id,
            verification_event.subject_id,
            verification_event.event_id,
            verification_event.event_hash,
            proof.proof_hash,
            proof.suite_hash,
            proof.scorecard_file_hash,
            proof.verified_records_hash,
            proof.verified_task_count,
            replay_determinism_proof.proof_hash,
            checkpoint.checkpoint_hash,
            next_action_packet.packet_hash,
        );
        Self {
            run_id: verification_event.run_id,
            verification_subject_id: verification_event.subject_id,
            verification_event_id: verification_event.event_id,
            verification_event_hash: verification_event.event_hash,
            proof_hash: proof.proof_hash,
            suite_hash: proof.suite_hash,
            scorecard_file_hash: proof.scorecard_file_hash,
            verified_records_hash: proof.verified_records_hash,
            verified_task_count: proof.verified_task_count,
            replay_determinism_proof_hash: replay_determinism_proof.proof_hash,
            checkpoint_hash: checkpoint.checkpoint_hash,
            next_action_packet_hash: next_action_packet.packet_hash,
            handoff_hash,
        }
    }

    pub fn is_valid_for(
        &self,
        verification_event: &RunEvent,
        proof: &BrowserOpsBenchVerificationProof,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> bool {
        self.run_id != 0
            && self.verification_subject_id != 0
            && self.verification_event_id > 0
            && self.verified_task_count > 0
            && nonzero_hash(&self.verification_event_hash)
            && nonzero_hash(&self.proof_hash)
            && nonzero_hash(&self.suite_hash)
            && nonzero_hash(&self.scorecard_file_hash)
            && nonzero_hash(&self.verified_records_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.handoff_hash)
            && proof.is_valid()
            && proof.replay_subject_id() == self.verification_subject_id
            && proof.proof_hash == self.proof_hash
            && proof.suite_hash == self.suite_hash
            && proof.scorecard_file_hash == self.scorecard_file_hash
            && proof.verified_records_hash == self.verified_records_hash
            && proof.verified_task_count == self.verified_task_count
            && verification_event.is_valid()
            && verification_event.kind == RunEventKind::BrowserOpsBenchVerificationRecorded
            && verification_event.run_id == self.run_id
            && verification_event.subject_id == self.verification_subject_id
            && verification_event.event_id == self.verification_event_id
            && verification_event.event_hash == self.verification_event_hash
            && verification_event.primary_hash == self.proof_hash
            && verification_event.secondary_hash
                == Some(browser_ops_bench_verification_replay_binding_hash(proof))
            && replay_determinism_proof.is_valid()
            && replay_determinism_proof.run_id == self.run_id
            && replay_determinism_proof.proof_hash == self.replay_determinism_proof_hash
            && (self.verification_event_id as usize) <= replay_determinism_proof.event_count
            && checkpoint.is_valid()
            && checkpoint.run_id == self.run_id
            && checkpoint.event_count == replay_determinism_proof.event_count
            && checkpoint.segment_count == replay_determinism_proof.segment_count
            && checkpoint.ledger_last_hash == replay_determinism_proof.first_pass_ledger_hash
            && checkpoint.manifest_hash == replay_determinism_proof.manifest_hash
            && checkpoint.replay_determinism_proof_hash == replay_determinism_proof.proof_hash
            && checkpoint.context_fold_evidence_hash == self.verification_event_hash
            && checkpoint.checkpoint_hash == self.checkpoint_hash
            && next_action_packet.is_valid_for_checkpoint(checkpoint)
            && next_action_packet.run_id == self.run_id
            && next_action_packet.candidate_evidence_hash == self.verification_event_hash
            && next_action_packet.packet_hash == self.next_action_packet_hash
            && self.handoff_hash
                == browser_ops_bench_verification_handoff_proof_hash(
                    self.run_id,
                    self.verification_subject_id,
                    self.verification_event_id,
                    self.verification_event_hash,
                    self.proof_hash,
                    self.suite_hash,
                    self.scorecard_file_hash,
                    self.verified_records_hash,
                    self.verified_task_count,
                    self.replay_determinism_proof_hash,
                    self.checkpoint_hash,
                    self.next_action_packet_hash,
                )
    }
}

impl AgenticEvidenceSdkRunHandoffProof {
    pub fn new(
        sdk_run_event: &RunEvent,
        run: &AgenticEvidenceSdkRun,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> Self {
        let sdk_run_replay_binding_hash = agentic_evidence_sdk_run_replay_binding_hash(run);
        let handoff_hash = agentic_evidence_sdk_run_handoff_proof_hash(
            sdk_run_event.run_id,
            sdk_run_event.subject_id,
            sdk_run_event.event_id,
            sdk_run_event.event_hash,
            run.manifest.manifest_hash,
            run.execution_record.record_hash,
            run.execution_record.candidate_list_hash,
            run.execution_record.candidate_count,
            run.capsule.capsule_hash,
            run.run_hash,
            sdk_run_replay_binding_hash,
            replay_determinism_proof.proof_hash,
            checkpoint.checkpoint_hash,
            next_action_packet.packet_hash,
        );
        Self {
            run_id: sdk_run_event.run_id,
            execution_id: sdk_run_event.subject_id,
            sdk_run_event_id: sdk_run_event.event_id,
            sdk_run_event_hash: sdk_run_event.event_hash,
            manifest_hash: run.manifest.manifest_hash,
            execution_record_hash: run.execution_record.record_hash,
            candidate_list_hash: run.execution_record.candidate_list_hash,
            candidate_count: run.execution_record.candidate_count,
            capsule_hash: run.capsule.capsule_hash,
            sdk_run_hash: run.run_hash,
            sdk_run_replay_binding_hash,
            replay_determinism_proof_hash: replay_determinism_proof.proof_hash,
            checkpoint_hash: checkpoint.checkpoint_hash,
            next_action_packet_hash: next_action_packet.packet_hash,
            handoff_hash,
        }
    }

    pub fn is_valid_for(
        &self,
        sdk_run_event: &RunEvent,
        program: &AgenticEvidenceProgram,
        run: &AgenticEvidenceSdkRun,
        replay_determinism_proof: &ReplayDeterminismProof,
        checkpoint: &RunCheckpoint,
        next_action_packet: &NextActionPacket,
    ) -> bool {
        self.run_id != 0
            && self.execution_id != 0
            && self.sdk_run_event_id > 0
            && self.candidate_count > 0
            && nonzero_hash(&self.sdk_run_event_hash)
            && nonzero_hash(&self.manifest_hash)
            && nonzero_hash(&self.execution_record_hash)
            && nonzero_hash(&self.candidate_list_hash)
            && nonzero_hash(&self.capsule_hash)
            && nonzero_hash(&self.sdk_run_hash)
            && nonzero_hash(&self.sdk_run_replay_binding_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.handoff_hash)
            && run.is_valid_for_program(program)
            && run.manifest.manifest_hash == self.manifest_hash
            && run.execution_record.record_hash == self.execution_record_hash
            && run.execution_record.candidate_list_hash == self.candidate_list_hash
            && run.execution_record.candidate_count == self.candidate_count
            && run.capsule.capsule_hash == self.capsule_hash
            && run.run_hash == self.sdk_run_hash
            && self.sdk_run_replay_binding_hash == agentic_evidence_sdk_run_replay_binding_hash(run)
            && sdk_run_event.is_valid()
            && sdk_run_event.kind == RunEventKind::AgenticEvidenceExecutionRecorded
            && sdk_run_event.run_id == self.run_id
            && sdk_run_event.subject_id == self.execution_id
            && sdk_run_event.event_id == self.sdk_run_event_id
            && sdk_run_event.event_hash == self.sdk_run_event_hash
            && sdk_run_event.primary_hash == self.sdk_run_hash
            && sdk_run_event.secondary_hash == Some(self.sdk_run_replay_binding_hash)
            && replay_determinism_proof.is_valid()
            && replay_determinism_proof.run_id == self.run_id
            && replay_determinism_proof.proof_hash == self.replay_determinism_proof_hash
            && (self.sdk_run_event_id as usize) <= replay_determinism_proof.event_count
            && checkpoint.is_valid()
            && checkpoint.run_id == self.run_id
            && checkpoint.event_count == replay_determinism_proof.event_count
            && checkpoint.segment_count == replay_determinism_proof.segment_count
            && checkpoint.ledger_last_hash == replay_determinism_proof.first_pass_ledger_hash
            && checkpoint.manifest_hash == replay_determinism_proof.manifest_hash
            && checkpoint.replay_determinism_proof_hash == replay_determinism_proof.proof_hash
            && checkpoint.context_fold_evidence_hash == self.sdk_run_event_hash
            && checkpoint.checkpoint_hash == self.checkpoint_hash
            && next_action_packet.is_valid_for_checkpoint(checkpoint)
            && next_action_packet.run_id == self.run_id
            && next_action_packet.candidate_evidence_hash == self.sdk_run_event_hash
            && next_action_packet.packet_hash == self.next_action_packet_hash
            && self.handoff_hash
                == agentic_evidence_sdk_run_handoff_proof_hash(
                    self.run_id,
                    self.execution_id,
                    self.sdk_run_event_id,
                    self.sdk_run_event_hash,
                    self.manifest_hash,
                    self.execution_record_hash,
                    self.candidate_list_hash,
                    self.candidate_count,
                    self.capsule_hash,
                    self.sdk_run_hash,
                    self.sdk_run_replay_binding_hash,
                    self.replay_determinism_proof_hash,
                    self.checkpoint_hash,
                    self.next_action_packet_hash,
                )
    }
}

impl AgenticEvidenceSdkRunHandoffBinding for AgenticEvidenceSdkRunHandoffProof {
    fn sdk_handoff_hash(&self) -> [u8; 32] {
        self.handoff_hash
    }

    fn sdk_execution_record_hash(&self) -> [u8; 32] {
        self.execution_record_hash
    }

    fn sdk_candidate_list_hash(&self) -> [u8; 32] {
        self.candidate_list_hash
    }

    fn sdk_candidate_count(&self) -> u32 {
        self.candidate_count
    }

    fn sdk_capsule_hash(&self) -> [u8; 32] {
        self.capsule_hash
    }
}

impl RunEventSegmentArchive {
    pub fn write_ledger(
        directory: impl AsRef<Path>,
        max_events_per_segment: usize,
        ledger: &RunEventLedger,
    ) -> Result<RunEventSegmentManifest, &'static str> {
        if max_events_per_segment == 0 || ledger.is_empty() || !ledger.verify_hash_chain() {
            return Err("invalid run event segment archive config");
        }
        let mut stream =
            SegmentedArrowAuditStream::create(directory, ledger.run_id, max_events_per_segment)?;
        stream.append_events(ledger.events())?;
        stream.finish()
    }

    /// Persist a complete Lab event chain inside the canonical segmented
    /// replay archive.  The Lab chain is validated before any file is
    /// published, so a malformed projection cannot become replay evidence.
    pub fn write_lab_events(
        directory: impl AsRef<Path>,
        max_events_per_segment: usize,
        run_id: RunId,
        lab_events: &[crate::lab::LabEvent],
    ) -> Result<RunEventSegmentManifest, &'static str> {
        if run_id == 0
            || max_events_per_segment == 0
            || lab_events.is_empty()
            || !crate::lab::verify_event_chain(lab_events)
        {
            return Err("invalid lab replay archive config");
        }
        let mut ledger = RunEventLedger::new(run_id);
        for lab_event in lab_events {
            ledger
                .append_lab_event_recorded(lab_event)
                .map_err(|_| "invalid lab replay event")?;
        }
        Self::write_ledger(directory, max_events_per_segment, &ledger)
    }

    /// Verify that a persisted Lab archive contains the exact event identity
    /// sequence supplied by a snapshot.  The segmented archive stores Lab
    /// event and payload hashes as the primary/secondary hashes of each
    /// ``LabEventRecorded`` envelope; comparing those hashes prevents a
    /// snapshot writer from replacing payloads and recomputing a locally
    /// valid Python chain without changing the already-sealed archive.
    pub fn verify_lab_events_against_archive(
        directory: impl AsRef<Path>,
        run_id: RunId,
        lab_events: &[crate::lab::LabEvent],
    ) -> Result<bool, &'static str> {
        if run_id == 0 || lab_events.is_empty() || !crate::lab::verify_event_chain(lab_events) {
            return Ok(false);
        }
        let manifest = Self::recover_manifest_from_segments(directory.as_ref(), run_id)?;
        let ledger = Self::read_ledger_mmap(directory, &manifest)?;
        if ledger.len() != lab_events.len()
            || ledger
                .events()
                .iter()
                .any(|event| event.kind != RunEventKind::LabEventRecorded)
        {
            return Ok(false);
        }
        Ok(ledger
            .events()
            .iter()
            .zip(lab_events.iter())
            .enumerate()
            .all(|(index, (archived, expected))| {
                archived.event_id == index as u64 + 1
                    && archived.subject_id == u128::from(expected.sequence)
                    && archived.primary_hash == expected.event_hash
                    && archived.secondary_hash == Some(expected.payload_hash)
            }))
    }

    pub fn prove_segmented_arrow_audit(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
    ) -> Result<SegmentedArrowAuditProof, &'static str> {
        if !manifest.is_valid() {
            return Err("invalid segmented arrow audit proof manifest");
        }
        let directory = directory.as_ref();
        let scan = Self::scan_segmented_arrow_audit_columns(directory, manifest)?;
        let mut proof = SegmentedArrowAuditProof {
            run_id: manifest.run_id,
            segment_count: manifest.entries.len(),
            event_count: scan.event_count,
            total_arrow_file_bytes: scan.total_arrow_file_bytes,
            mmap_buffer_count: scan.mmap_buffer_count,
            first_event_id: scan.first_event_id,
            last_event_id: scan.last_event_id,
            manifest_hash: manifest.manifest_hash,
            arrow_schema_hash: ArrowRunEventStream::schema_hash(),
            segment_chain_hash: scan.segment_chain_hash,
            segment_witness_hash: scan.segment_witness_hash,
            file_evidence_hash: scan.file_evidence_hash,
            logical_replay_hash: scan.logical_replay_hash,
            mmap_evidence_hash: scan.mmap_evidence_hash,
            semantic_scan_hash: [0; 32],
            proof_hash: [0; 32],
        };
        proof.semantic_scan_hash = segmented_arrow_semantic_scan_hash(&proof);
        proof.proof_hash = segmented_arrow_audit_proof_hash(&proof);
        if !proof.is_valid() {
            return Err("invalid segmented arrow audit proof");
        }
        Ok(proof)
    }

    fn scan_segmented_arrow_audit_columns(
        directory: &Path,
        manifest: &RunEventSegmentManifest,
    ) -> Result<SegmentedArrowColumnScanProof, &'static str> {
        if !manifest.is_valid() {
            return Err("invalid segmented arrow column scan manifest");
        }
        let expected_event_count =
            manifest.entries.iter().try_fold(0usize, |total, entry| {
                total
                    .checked_add(usize::try_from(entry.event_count).map_err(
                        |_| "segmented arrow column scan event count exceeds target width",
                    )?)
                    .ok_or("segmented arrow column scan event count overflow")
            })?;
        let mut total_arrow_file_bytes = 0u64;
        let mut mmap_buffer_count = 0usize;
        let mut expected_event_id = 1u64;
        let mut expected_previous_hash = [0; 32];
        let mut scan_state = RunEventColumnScanState::default();
        let mut logical_hasher = run_event_logical_replay_hasher(expected_event_count);
        let mut first_event_id = None;
        let mut last_event_id = None;

        let mut segment_chain_hasher = Hasher::new();
        segment_chain_hasher.update(b"aegis-segmented-arrow-audit-chain-v1");
        update_u128(&mut segment_chain_hasher, manifest.run_id);
        segment_chain_hasher.update(&manifest.manifest_hash);
        segment_chain_hasher.update(&ArrowRunEventStream::schema_hash());

        let mut file_evidence_hasher = Hasher::new();
        file_evidence_hasher.update(b"aegis-segmented-arrow-file-evidence-v1");
        update_u128(&mut file_evidence_hasher, manifest.run_id);
        file_evidence_hasher.update(&manifest.manifest_hash);

        let mut segment_witness_hasher = Hasher::new();
        segment_witness_hasher.update(b"aegis-segmented-arrow-segment-witnesses-v1");
        update_u128(&mut segment_witness_hasher, manifest.run_id);
        segment_witness_hasher.update(&manifest.manifest_hash);
        segment_witness_hasher.update(&ArrowRunEventStream::schema_hash());
        update_u64(&mut segment_witness_hasher, manifest.entries.len() as u64);

        for entry in &manifest.entries {
            let commit_path =
                Self::segment_commit_path(directory, manifest.run_id, entry.segment_id);
            if !entry.matches_commit_file(&commit_path, manifest.run_id) {
                return Err("segmented arrow column scan commit evidence mismatch");
            }
            let path = Self::segment_path(directory, manifest.run_id, entry.segment_id);
            let scan = ArrowRunEventStream::scan_mmap_columns_for_entry(
                &path,
                entry,
                manifest.run_id,
                &mut expected_event_id,
                &mut expected_previous_hash,
                &mut scan_state,
                &mut logical_hasher,
            )?;
            if scan.file_bytes != entry.arrow_file_bytes
                || scan.file_hash != entry.arrow_file_hash
                || scan.event_count as u64 != entry.event_count
                || scan.first_event_id != entry.start_event_id
                || scan.last_event_id != entry.end_event_id
                || scan.first_event_hash != entry.first_event_hash
                || scan.last_event_hash != entry.last_event_hash
                || scan.segment_hash != entry.segment_hash
            {
                return Err("segmented arrow column scan entry mismatch");
            }
            total_arrow_file_bytes = total_arrow_file_bytes
                .checked_add(entry.arrow_file_bytes)
                .ok_or("segmented arrow audit file byte overflow")?;
            mmap_buffer_count = mmap_buffer_count
                .checked_add(scan.mmap_buffer_count)
                .ok_or("segmented arrow column scan mmap buffer count overflow")?;
            if first_event_id.is_none() {
                first_event_id = Some(scan.first_event_id);
            }
            last_event_id = Some(scan.last_event_id);

            update_u64(&mut segment_chain_hasher, entry.segment_id);
            update_u64(&mut segment_chain_hasher, entry.start_event_id);
            update_u64(&mut segment_chain_hasher, entry.end_event_id);
            update_u64(&mut segment_chain_hasher, entry.event_count);
            segment_chain_hasher.update(&entry.first_event_hash);
            segment_chain_hasher.update(&entry.last_event_hash);
            segment_chain_hasher.update(&entry.segment_hash);
            segment_chain_hasher.update(&entry.previous_segment_hash);
            segment_chain_hasher.update(&entry.arrow_schema_hash);
            update_u64(&mut segment_chain_hasher, entry.arrow_file_bytes);
            segment_chain_hasher.update(&entry.arrow_file_hash);
            segment_chain_hasher.update(&entry.segment_commit_hash);
            update_u8(
                &mut segment_chain_hasher,
                u8::from(entry.staged_temp_file_used),
            );
            update_u8(
                &mut segment_chain_hasher,
                u8::from(entry.temp_file_synced_before_publish),
            );
            update_u8(&mut segment_chain_hasher, u8::from(entry.publish_completed));
            update_u8(
                &mut segment_chain_hasher,
                u8::from(entry.parent_directory_sync_attempted),
            );

            update_u64(&mut file_evidence_hasher, entry.segment_id);
            update_u64(&mut file_evidence_hasher, entry.arrow_file_bytes);
            file_evidence_hasher.update(&entry.arrow_file_hash);
            file_evidence_hasher.update(&entry.segment_commit_hash);

            segment_witness_hasher.update(&segmented_arrow_segment_witness_hash(
                manifest.run_id,
                manifest.manifest_hash,
                entry,
                &scan,
            ));
        }
        if expected_event_count == 0 || expected_event_id != expected_event_count as u64 + 1 {
            return Err("segmented arrow column scan event count mismatch");
        }
        let logical_replay_hash = *logical_hasher.finalize().as_bytes();
        let mmap_evidence_hash = arrow_mmap_column_scan_evidence_hash(
            manifest.run_id,
            manifest.manifest_hash,
            manifest.entries.len(),
            total_arrow_file_bytes,
            expected_event_count,
            mmap_buffer_count,
            logical_replay_hash,
        );
        Ok(SegmentedArrowColumnScanProof {
            event_count: expected_event_count,
            total_arrow_file_bytes,
            mmap_buffer_count,
            first_event_id: first_event_id.ok_or("empty segmented arrow column scan")?,
            last_event_id: last_event_id.ok_or("empty segmented arrow column scan")?,
            segment_chain_hash: *segment_chain_hasher.finalize().as_bytes(),
            segment_witness_hash: *segment_witness_hasher.finalize().as_bytes(),
            file_evidence_hash: *file_evidence_hasher.finalize().as_bytes(),
            logical_replay_hash,
            mmap_evidence_hash,
        })
    }

    pub fn compact_to_binary_segment_crash_safe(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        output_path: impl AsRef<Path>,
    ) -> Result<RunEventCompactionReport, &'static str> {
        let output_path = output_path.as_ref();
        if output_path.as_os_str().is_empty() {
            return Err("invalid replay compaction output path");
        }
        if let Some(parent) = output_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent)
                .map_err(|_| "failed to create replay compaction directory")?;
        }

        let source_audit_proof = Self::prove_segmented_arrow_audit(&directory, manifest)?;
        let (ledger, source_mmap_evidence) =
            Self::read_ledger_mmap_with_evidence(&directory, manifest)?;
        let source_last_event_hash = manifest
            .entries
            .last()
            .ok_or("empty replay compaction manifest")?
            .last_event_hash;
        if ledger.len() != source_audit_proof.event_count
            || ledger.last_hash() != source_last_event_hash
            || source_mmap_evidence.materialized_event_count != ledger.len()
        {
            return Err("replay compaction source evidence mismatch");
        }

        let temp_path = synced_artifact_temp_path(output_path);
        let compacted_payload_hash = match BinaryRunEventSegment::write_ledger(&temp_path, &ledger)
        {
            Ok(hash) => hash,
            Err(error) => {
                let _ = std::fs::remove_file(&temp_path);
                return Err(error);
            }
        };
        let pre_publish_scan =
            match BinaryRunEventSegment::verify_mmap(&temp_path, compacted_payload_hash) {
                Ok(scan) => scan,
                Err(error) => {
                    let _ = std::fs::remove_file(&temp_path);
                    return Err(error);
                }
            };
        if pre_publish_scan.event_count != ledger.len()
            || pre_publish_scan.last_event_hash != ledger.last_hash()
        {
            let _ = std::fs::remove_file(&temp_path);
            return Err("invalid replay compaction pre-publish scan");
        }

        if let Err(error) = publish_synced_artifact_temp(&temp_path, output_path) {
            let _ = std::fs::remove_file(&temp_path);
            return Err(error);
        }
        let parent_directory_sync_attempted = if let Some(parent) = output_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            let _ = File::open(parent).and_then(|directory| directory.sync_all());
            true
        } else {
            true
        };
        let final_scan = BinaryRunEventSegment::verify_mmap(output_path, compacted_payload_hash)?;
        if final_scan.event_count != ledger.len()
            || final_scan.last_event_hash != ledger.last_hash()
        {
            return Err("invalid replay compaction final mmap scan");
        }

        let mut report = RunEventCompactionReport {
            run_id: manifest.run_id,
            source_segment_count: manifest.entries.len(),
            source_event_count: ledger.len(),
            source_manifest_hash: manifest.manifest_hash,
            source_audit_proof_hash: source_audit_proof.proof_hash,
            source_mmap_evidence_hash: source_mmap_evidence.evidence_hash,
            compacted_event_count: final_scan.event_count,
            compacted_file_bytes: final_scan.file_bytes,
            compacted_payload_hash: final_scan.payload_hash,
            compacted_last_event_hash: final_scan.last_event_hash,
            staged_temp_file_used: true,
            temp_file_synced_before_publish: true,
            publish_completed: true,
            parent_directory_sync_attempted,
            mmap_scan_verified: true,
            report_hash: [0; 32],
        };
        report.report_hash = run_event_compaction_report_hash(&report);
        if !report.is_valid() {
            return Err("invalid replay compaction report");
        }
        Ok(report)
    }

    pub fn prove_replay_determinism(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
    ) -> Result<ReplayDeterminismProof, &'static str> {
        if !manifest.is_valid() {
            return Err("invalid replay determinism manifest");
        }
        let directory = directory.as_ref();
        let commit_sidecar_evidence_hash =
            Self::verify_segment_commit_sidecars(directory, manifest)?;
        let expected_event_count = manifest.entries.iter().try_fold(0usize, |total, entry| {
            total
                .checked_add(
                    usize::try_from(entry.event_count)
                        .map_err(|_| "replay determinism event count exceeds target width")?,
                )
                .ok_or("replay determinism event count overflow")
        })?;
        let (first_ledger, first_evidence) =
            Self::read_ledger_mmap_with_evidence_inner(directory, manifest, false)?;
        let (second_ledger, second_evidence) =
            Self::read_ledger_mmap_with_evidence_inner(directory, manifest, false)?;
        let first_event_sequence_hash = run_event_logical_replay_hash(first_ledger.events());
        let second_event_sequence_hash = run_event_logical_replay_hash(second_ledger.events());
        if first_ledger.len() != expected_event_count
            || second_ledger.len() != expected_event_count
            || first_event_sequence_hash != second_event_sequence_hash
            || first_ledger.last_hash() != second_ledger.last_hash()
            || first_evidence.evidence_hash != second_evidence.evidence_hash
            || !first_evidence.proves_mmap_materialized_replay()
            || !second_evidence.proves_mmap_materialized_replay()
        {
            return Err("replay determinism proof mismatch");
        }
        let mut proof = ReplayDeterminismProof {
            run_id: manifest.run_id,
            segment_count: manifest.entries.len(),
            event_count: first_ledger.len(),
            manifest_hash: manifest.manifest_hash,
            arrow_schema_hash: ArrowRunEventStream::schema_hash(),
            first_pass_event_sequence_hash: first_event_sequence_hash,
            second_pass_event_sequence_hash: second_event_sequence_hash,
            first_pass_ledger_hash: first_ledger.last_hash(),
            second_pass_ledger_hash: second_ledger.last_hash(),
            first_pass_mmap_evidence_hash: first_evidence.evidence_hash,
            second_pass_mmap_evidence_hash: second_evidence.evidence_hash,
            commit_sidecar_evidence_hash,
            proof_hash: [0; 32],
        };
        proof.proof_hash = replay_determinism_proof_hash(&proof);
        if !proof.is_valid() {
            return Err("invalid replay determinism proof");
        }
        Ok(proof)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_memory_commit_handoff(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        memory_commit_proof: &ToolMemoryCommitProof,
        tool_execution_evidence_hash: [u8; 32],
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
    ) -> Result<MemoryCommitHandoffProof, &'static str> {
        if !manifest.is_valid()
            || !memory_commit_proof.has_valid_fields()
            || !nonzero_hash(&tool_execution_evidence_hash)
            || checkpoint_id == 0
            || packet_id == 0
            || task_id == 0
            || !nonzero_hash(&typed_tool_ir_hash)
            || !nonzero_hash(&evidence_contract_hash)
            || !nonzero_hash(&policy_proof_hash)
        {
            return Err("invalid memory commit handoff inputs");
        }
        let directory = directory.as_ref();
        let replay_determinism_proof = Self::prove_replay_determinism(directory, manifest)?;
        if replay_determinism_proof.run_id != manifest.run_id
            || replay_determinism_proof.manifest_hash != manifest.manifest_hash
        {
            return Err("memory commit handoff replay proof manifest mismatch");
        }
        let ledger = Self::read_ledger_mmap(directory, manifest)?;
        if ledger.len() != replay_determinism_proof.event_count
            || ledger.last_hash() != replay_determinism_proof.first_pass_ledger_hash
        {
            return Err("memory commit handoff ledger mismatch");
        }
        let expected_memory_binding = memory_commit_binding_hash(
            memory_commit_proof.session_id,
            tool_execution_evidence_hash,
            memory_commit_proof.proof_hash,
        );
        let memory_commit_event = ledger
            .events()
            .iter()
            .rev()
            .find(|event| {
                event.kind == RunEventKind::MemoryCommitRecorded
                    && event.subject_id == memory_commit_proof.session_id
                    && event.primary_hash == memory_commit_proof.proof_hash
                    && event.secondary_hash == Some(expected_memory_binding)
            })
            .ok_or("memory commit event missing from replay archive")?;

        let checkpoint = RunCheckpoint::new(
            manifest.run_id,
            checkpoint_id,
            replay_determinism_proof.event_count,
            manifest.entries.len(),
            replay_determinism_proof.first_pass_ledger_hash,
            manifest.manifest_hash,
            replay_determinism_proof.proof_hash,
            memory_commit_event.event_hash,
        );
        if !checkpoint.is_valid() {
            return Err("invalid memory commit handoff checkpoint");
        }
        let next_action_packet = NextActionPacket::new(
            manifest.run_id,
            packet_id,
            checkpoint.checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            memory_commit_event.event_hash,
        );
        if !next_action_packet.is_valid_for_checkpoint(&checkpoint) {
            return Err("invalid memory commit handoff next action packet");
        }
        let proof = MemoryCommitHandoffProof::new(
            memory_commit_event,
            tool_execution_evidence_hash,
            memory_commit_proof,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        );
        if !proof.is_valid_for(
            memory_commit_event,
            memory_commit_proof,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        ) {
            return Err("invalid memory commit handoff proof");
        }
        Ok(proof)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_skill_admission_handoff(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        admission: &SkillAdmissionRecord,
        admitted: &AdmittedSkillRecord,
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
    ) -> Result<SkillAdmissionHandoffProof, &'static str> {
        if !manifest.is_valid()
            || !admission.has_valid_fields()
            || !admitted.has_valid_fields()
            || admitted.admission_hash != admission.admission_hash
            || admitted.package_hash != admission.package_hash
            || checkpoint_id == 0
            || packet_id == 0
            || task_id == 0
            || !nonzero_hash(&typed_tool_ir_hash)
            || !nonzero_hash(&evidence_contract_hash)
            || !nonzero_hash(&policy_proof_hash)
        {
            return Err("invalid skill admission handoff inputs");
        }
        let directory = directory.as_ref();
        let replay_determinism_proof = Self::prove_replay_determinism(directory, manifest)?;
        if replay_determinism_proof.run_id != manifest.run_id
            || replay_determinism_proof.manifest_hash != manifest.manifest_hash
        {
            return Err("skill admission handoff replay proof manifest mismatch");
        }
        let ledger = Self::read_ledger_mmap(directory, manifest)?;
        if ledger.len() != replay_determinism_proof.event_count
            || ledger.last_hash() != replay_determinism_proof.first_pass_ledger_hash
        {
            return Err("skill admission handoff ledger mismatch");
        }
        let expected_binding = skill_admission_replay_binding_hash(admission, admitted);
        let skill_admission_event = ledger
            .events()
            .iter()
            .rev()
            .find(|event| {
                event.kind == RunEventKind::SkillAdmissionRecorded
                    && event.subject_id == admitted.skill_id
                    && event.primary_hash == admitted.registry_commit_hash
                    && event.secondary_hash == Some(expected_binding)
            })
            .ok_or("skill admission event missing from replay archive")?;

        let checkpoint = RunCheckpoint::new(
            manifest.run_id,
            checkpoint_id,
            replay_determinism_proof.event_count,
            manifest.entries.len(),
            replay_determinism_proof.first_pass_ledger_hash,
            manifest.manifest_hash,
            replay_determinism_proof.proof_hash,
            skill_admission_event.event_hash,
        );
        if !checkpoint.is_valid() {
            return Err("invalid skill admission handoff checkpoint");
        }
        let next_action_packet = NextActionPacket::new(
            manifest.run_id,
            packet_id,
            checkpoint.checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            skill_admission_event.event_hash,
        );
        if !next_action_packet.is_valid_for_checkpoint(&checkpoint) {
            return Err("invalid skill admission handoff next action packet");
        }
        let proof = SkillAdmissionHandoffProof::new(
            skill_admission_event,
            admission,
            admitted,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        );
        if !proof.is_valid_for(
            skill_admission_event,
            admission,
            admitted,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        ) {
            return Err("invalid skill admission handoff proof");
        }
        Ok(proof)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_browser_ops_bench_verification_handoff(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        proof: &BrowserOpsBenchVerificationProof,
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
    ) -> Result<BrowserOpsBenchVerificationHandoffProof, &'static str> {
        if !manifest.is_valid()
            || !proof.is_valid()
            || checkpoint_id == 0
            || packet_id == 0
            || task_id == 0
            || !nonzero_hash(&typed_tool_ir_hash)
            || !nonzero_hash(&evidence_contract_hash)
            || !nonzero_hash(&policy_proof_hash)
        {
            return Err("invalid browser ops bench verification handoff inputs");
        }
        let directory = directory.as_ref();
        let replay_determinism_proof = Self::prove_replay_determinism(directory, manifest)?;
        if replay_determinism_proof.run_id != manifest.run_id
            || replay_determinism_proof.manifest_hash != manifest.manifest_hash
        {
            return Err("browser ops bench verification handoff replay proof manifest mismatch");
        }
        let ledger = Self::read_ledger_mmap(directory, manifest)?;
        if ledger.len() != replay_determinism_proof.event_count
            || ledger.last_hash() != replay_determinism_proof.first_pass_ledger_hash
        {
            return Err("browser ops bench verification handoff ledger mismatch");
        }
        let expected_binding = browser_ops_bench_verification_replay_binding_hash(proof);
        let verification_event = ledger
            .events()
            .iter()
            .rev()
            .find(|event| {
                event.kind == RunEventKind::BrowserOpsBenchVerificationRecorded
                    && event.subject_id == proof.replay_subject_id()
                    && event.primary_hash == proof.proof_hash
                    && event.secondary_hash == Some(expected_binding)
            })
            .ok_or("browser ops bench verification event missing from replay archive")?;

        let checkpoint = RunCheckpoint::new(
            manifest.run_id,
            checkpoint_id,
            replay_determinism_proof.event_count,
            manifest.entries.len(),
            replay_determinism_proof.first_pass_ledger_hash,
            manifest.manifest_hash,
            replay_determinism_proof.proof_hash,
            verification_event.event_hash,
        );
        if !checkpoint.is_valid() {
            return Err("invalid browser ops bench verification handoff checkpoint");
        }
        let next_action_packet = NextActionPacket::new(
            manifest.run_id,
            packet_id,
            checkpoint.checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            verification_event.event_hash,
        );
        if !next_action_packet.is_valid_for_checkpoint(&checkpoint) {
            return Err("invalid browser ops bench verification handoff next action packet");
        }
        let handoff = BrowserOpsBenchVerificationHandoffProof::new(
            verification_event,
            proof,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        );
        if !handoff.is_valid_for(
            verification_event,
            proof,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        ) {
            return Err("invalid browser ops bench verification handoff proof");
        }
        Ok(handoff)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn seal_agentic_evidence_sdk_run_handoff(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
        execution_id: SubjectId,
        program: &AgenticEvidenceProgram,
        run: &AgenticEvidenceSdkRun,
        checkpoint_id: SubjectId,
        packet_id: SubjectId,
        action_kind: NextActionKind,
        task_id: SubjectId,
        typed_tool_ir_hash: [u8; 32],
        evidence_contract_hash: [u8; 32],
        policy_proof_hash: [u8; 32],
    ) -> Result<AgenticEvidenceSdkRunHandoffProof, &'static str> {
        if !manifest.is_valid()
            || execution_id == 0
            || !run.is_valid_for_program(program)
            || checkpoint_id == 0
            || packet_id == 0
            || task_id == 0
            || !nonzero_hash(&typed_tool_ir_hash)
            || !nonzero_hash(&evidence_contract_hash)
            || !nonzero_hash(&policy_proof_hash)
        {
            return Err("invalid agentic evidence sdk run handoff inputs");
        }
        let directory = directory.as_ref();
        let replay_determinism_proof = Self::prove_replay_determinism(directory, manifest)?;
        if replay_determinism_proof.run_id != manifest.run_id
            || replay_determinism_proof.manifest_hash != manifest.manifest_hash
        {
            return Err("agentic evidence sdk run handoff replay proof manifest mismatch");
        }
        let ledger = Self::read_ledger_mmap(directory, manifest)?;
        if ledger.len() != replay_determinism_proof.event_count
            || ledger.last_hash() != replay_determinism_proof.first_pass_ledger_hash
        {
            return Err("agentic evidence sdk run handoff ledger mismatch");
        }
        let expected_binding = agentic_evidence_sdk_run_replay_binding_hash(run);
        let sdk_run_event = ledger
            .events()
            .iter()
            .rev()
            .find(|event| {
                event.kind == RunEventKind::AgenticEvidenceExecutionRecorded
                    && event.subject_id == execution_id
                    && event.primary_hash == run.run_hash
                    && event.secondary_hash == Some(expected_binding)
            })
            .ok_or("agentic evidence sdk run event missing from replay archive")?;

        let checkpoint = RunCheckpoint::new(
            manifest.run_id,
            checkpoint_id,
            replay_determinism_proof.event_count,
            manifest.entries.len(),
            replay_determinism_proof.first_pass_ledger_hash,
            manifest.manifest_hash,
            replay_determinism_proof.proof_hash,
            sdk_run_event.event_hash,
        );
        if !checkpoint.is_valid() {
            return Err("invalid agentic evidence sdk run handoff checkpoint");
        }
        let next_action_packet = NextActionPacket::new(
            manifest.run_id,
            packet_id,
            checkpoint.checkpoint_hash,
            action_kind,
            task_id,
            typed_tool_ir_hash,
            evidence_contract_hash,
            policy_proof_hash,
            sdk_run_event.event_hash,
        );
        if !next_action_packet.is_valid_for_checkpoint(&checkpoint) {
            return Err("invalid agentic evidence sdk run handoff next action packet");
        }
        let handoff = AgenticEvidenceSdkRunHandoffProof::new(
            sdk_run_event,
            run,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        );
        if !handoff.is_valid_for(
            sdk_run_event,
            program,
            run,
            &replay_determinism_proof,
            &checkpoint,
            &next_action_packet,
        ) {
            return Err("invalid agentic evidence sdk run handoff proof");
        }
        Ok(handoff)
    }

    pub fn recover_manifest_from_segments(
        directory: impl AsRef<Path>,
        run_id: RunId,
    ) -> Result<RunEventSegmentManifest, &'static str> {
        if run_id == 0 {
            return Err("invalid sealed segment recovery run id");
        }
        let directory = directory.as_ref();
        let mut entries = Vec::new();
        let mut previous_segment_hash = [0; 32];
        let mut ledger = RunEventLedger::new(run_id);
        let mut expected_event_id = 1u64;
        let mut expected_previous_hash = [0; 32];
        let mut segment_id = 1u64;

        loop {
            let path = Self::segment_path(directory, run_id, segment_id);
            let commit_path = Self::segment_commit_path(directory, run_id, segment_id);
            if !path.exists() {
                break;
            }
            let (segment_events, evidence, arrow_file_hash) =
                match ArrowRunEventStream::read_events_mmap_with_capacity_evidence_and_file_hash(
                    &path, 0,
                ) {
                    Ok(segment) => segment,
                    Err(_) => break,
                };
            if segment_events.is_empty() || !evidence.proves_mmap_materialized_replay() {
                break;
            }
            let commit_proof = match RunEventSegmentCommitProof::read_sidecar(&commit_path) {
                Ok(proof)
                    if proof.run_id == run_id
                        && proof.segment_id == segment_id
                        && proof.arrow_file_bytes == evidence.total_file_bytes
                        && proof.arrow_file_hash == arrow_file_hash =>
                {
                    proof
                }
                _ => break,
            };

            let mut segment_expected_event_id = expected_event_id;
            let mut segment_expected_previous_hash = expected_previous_hash;
            let mut segment_is_valid = true;
            for event in &segment_events {
                if !event.is_valid()
                    || event.run_id != run_id
                    || event.event_id != segment_expected_event_id
                    || event.previous_event_hash != segment_expected_previous_hash
                {
                    segment_is_valid = false;
                    break;
                }
                segment_expected_event_id = segment_expected_event_id.saturating_add(1);
                segment_expected_previous_hash = event.event_hash;
            }
            if !segment_is_valid {
                break;
            }

            let entry = match RunEventSegmentEntry::from_events(
                segment_id,
                &segment_events,
                previous_segment_hash,
                evidence.total_file_bytes,
                arrow_file_hash,
                commit_proof.publish_evidence,
            ) {
                Ok(entry)
                    if entry.matches_events(&segment_events)
                        && commit_proof.binds_entry(&entry) =>
                {
                    entry
                }
                _ => break,
            };
            let mut append_ok = true;
            for event in segment_events.iter().cloned() {
                let requires_prior_context_pack = matches!(
                    event.kind,
                    RunEventKind::LLMResponseReceived
                        | RunEventKind::ContextFoldRecorded
                        | RunEventKind::ContextPackCandidateProofRecorded
                );
                let requires_prior_goal_intake = matches!(
                    event.kind,
                    RunEventKind::ContextPackBuilt
                        | RunEventKind::ContextPackCandidateProofRecorded
                        | RunEventKind::TaskSelectionProofRecorded
                        | RunEventKind::LLMResponseReceived
                        | RunEventKind::ContextFoldRecorded
                        | RunEventKind::ToolCallRequested
                        | RunEventKind::ToolCallCompleted
                        | RunEventKind::PolicyDecisionRecorded
                        | RunEventKind::ApprovalTokenRecorded
                        | RunEventKind::OperatorReviewArtifactRecorded
                        | RunEventKind::ColdVectorExpansionRecorded
                        | RunEventKind::AgenticEvidenceExecutionRecorded
                        | RunEventKind::SkillAdmissionRecorded
                        | RunEventKind::BrowserOpsBenchVerificationRecorded
                        | RunEventKind::ClusterCandidateAccepted
                        | RunEventKind::ShadowSealRecorded
                        | RunEventKind::CircuitBreakerTripped
                        | RunEventKind::MemoryCommitRecorded
                        | RunEventKind::CheckpointSealed
                );
                let requires_prior_llm_response = matches!(
                    event.kind,
                    RunEventKind::ToolCallRequested | RunEventKind::CircuitBreakerTripped
                );
                let requires_prior_policy_decision = matches!(
                    event.kind,
                    RunEventKind::ToolCallCompleted
                        | RunEventKind::ApprovalTokenRecorded
                        | RunEventKind::OperatorReviewArtifactRecorded
                );
                let requires_prior_tool_call =
                    matches!(event.kind, RunEventKind::ToolCallCompleted);
                if ledger
                    .append(
                        event,
                        requires_prior_goal_intake,
                        requires_prior_context_pack,
                        requires_prior_llm_response,
                        requires_prior_tool_call,
                        requires_prior_policy_decision,
                    )
                    .is_err()
                {
                    append_ok = false;
                    break;
                }
            }
            if !append_ok {
                break;
            }
            previous_segment_hash = entry.segment_hash;
            entries.push(entry);
            expected_event_id = segment_expected_event_id;
            expected_previous_hash = segment_expected_previous_hash;
            segment_id = segment_id.saturating_add(1);
        }

        if entries.is_empty() || ledger.is_empty() || !ledger.verify_hash_chain() {
            return Err("no sealed run event segments recovered");
        }
        let manifest = RunEventSegmentManifest::new(run_id, entries);
        manifest
            .is_valid()
            .then_some(manifest)
            .ok_or("invalid recovered sealed segment manifest")
    }

    pub fn read_ledger(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
    ) -> Result<RunEventLedger, &'static str> {
        if !manifest.is_valid() {
            return Err("invalid run event segment manifest");
        }
        let mut events = Vec::new();
        for entry in &manifest.entries {
            let path = Self::segment_path(directory.as_ref(), manifest.run_id, entry.segment_id);
            let commit_path =
                Self::segment_commit_path(directory.as_ref(), manifest.run_id, entry.segment_id);
            if !entry.matches_commit_file(&commit_path, manifest.run_id) {
                return Err("run event arrow segment commit evidence mismatch");
            }
            if !entry.matches_arrow_file(&path) {
                return Err("run event arrow segment file evidence mismatch");
            }
            let segment_events = ArrowRunEventStream::read_events(&path)?;
            if !entry.matches_events(&segment_events) {
                return Err("run event segment hash mismatch");
            }
            events.extend(segment_events);
        }
        RunEventLedger::from_events(manifest.run_id, events)
            .map_err(|_| "invalid recovered run event segment ledger")
    }

    pub fn read_ledger_mmap(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
    ) -> Result<RunEventLedger, &'static str> {
        Self::read_ledger_mmap_with_evidence(directory, manifest).map(|(ledger, _evidence)| ledger)
    }

    pub fn read_ledger_mmap_with_evidence(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
    ) -> Result<(RunEventLedger, ReplayIoEvidence), &'static str> {
        Self::read_ledger_mmap_with_evidence_inner(directory.as_ref(), manifest, true)
    }

    fn read_ledger_mmap_with_evidence_inner(
        directory: &Path,
        manifest: &RunEventSegmentManifest,
        verify_commit_sidecars: bool,
    ) -> Result<(RunEventLedger, ReplayIoEvidence), &'static str> {
        if !manifest.is_valid() {
            return Err("invalid mmap run event segment manifest");
        }
        let total_event_count = manifest.entries.iter().try_fold(0usize, |total, entry| {
            total
                .checked_add(
                    usize::try_from(entry.event_count)
                        .map_err(|_| "run event count exceeds target width")?,
                )
                .ok_or("run event count overflow")
        })?;
        let mut events = Vec::new();
        let mut segment_count = 0usize;
        let mut mmap_segment_count = 0usize;
        let mut total_file_bytes = 0u64;
        let mut materialized_hash_bytes = 0u64;
        let mut mmap_backing_used = true;
        let mut stream_reader_materializes_events = false;
        let mut file_evidence_buffer = Vec::new();
        for entry in &manifest.entries {
            let path = Self::segment_path(directory, manifest.run_id, entry.segment_id);
            let commit_path =
                Self::segment_commit_path(directory, manifest.run_id, entry.segment_id);
            if verify_commit_sidecars && !entry.matches_commit_file(&commit_path, manifest.run_id) {
                return Err("mmap run event arrow segment commit evidence mismatch");
            }
            let (arrow_file_bytes, arrow_file_hash) =
                arrow_stream_file_evidence_with_buffer(&path, &mut file_evidence_buffer)?;
            if arrow_file_bytes != entry.arrow_file_bytes
                || arrow_file_hash != entry.arrow_file_hash
            {
                return Err("mmap run event arrow segment file evidence mismatch");
            }
            let cache_key = RunEventSegmentCacheKey::from_entry(manifest.run_id, entry);
            if let Some(cached) = run_event_segment_cache_get(&cache_key) {
                if !entry.matches_cached_events(cached.events.as_slice()) {
                    return Err("cached mmap run event segment hash mismatch");
                }
                events.extend(cached.events.iter().cloned());
                segment_count = segment_count.saturating_add(cached.evidence.segment_count);
                mmap_segment_count =
                    mmap_segment_count.saturating_add(cached.evidence.mmap_segment_count);
                total_file_bytes =
                    total_file_bytes.saturating_add(cached.evidence.total_file_bytes);
                materialized_hash_bytes =
                    materialized_hash_bytes.saturating_add(cached.evidence.materialized_hash_bytes);
                mmap_backing_used &= cached.evidence.mmap_backing_used;
                stream_reader_materializes_events |=
                    cached.evidence.stream_reader_materializes_events;
                continue;
            }
            let segment_start = events.len();
            let (evidence, decoded_arrow_file_hash) =
                ArrowRunEventStream::append_events_mmap_with_capacity_evidence_and_file_hash(
                    &path,
                    usize::try_from(entry.event_count)
                        .map_err(|_| "run event segment count exceeds target width")?,
                    &mut events,
                    false,
                )?;
            if evidence.total_file_bytes != entry.arrow_file_bytes
                || decoded_arrow_file_hash != entry.arrow_file_hash
            {
                return Err("mmap run event arrow segment file evidence mismatch");
            }
            let segment_events = &events[segment_start..];
            if !entry.matches_events_after_manifest_validation(segment_events) {
                return Err("mmap run event segment hash mismatch");
            }
            run_event_segment_cache_put(
                cache_key,
                RunEventSegmentCacheEntry {
                    events: Arc::new(segment_events.to_vec()),
                    evidence: evidence.clone(),
                },
            );
            segment_count = segment_count.saturating_add(evidence.segment_count);
            mmap_segment_count = mmap_segment_count.saturating_add(evidence.mmap_segment_count);
            total_file_bytes = total_file_bytes.saturating_add(evidence.total_file_bytes);
            materialized_hash_bytes =
                materialized_hash_bytes.saturating_add(evidence.materialized_hash_bytes);
            mmap_backing_used &= evidence.mmap_backing_used;
            stream_reader_materializes_events |= evidence.stream_reader_materializes_events;
        }
        let evidence = ReplayIoEvidence::from_totals(
            segment_count,
            mmap_segment_count,
            total_file_bytes,
            events.len(),
            (events.len() * size_of::<RunEvent>()) as u64,
            materialized_hash_bytes,
            mmap_backing_used,
            stream_reader_materializes_events,
        );
        let ledger = RunEventLedger::from_events(manifest.run_id, events)
            .map_err(|_| "invalid recovered mmap run event segment ledger")?;
        if ledger.len() != total_event_count {
            return Err("run event count mismatch");
        }
        Ok((ledger, evidence))
    }

    fn verify_segment_commit_sidecars(
        directory: &Path,
        manifest: &RunEventSegmentManifest,
    ) -> Result<[u8; 32], &'static str> {
        if !manifest.is_valid() {
            return Err("invalid segment commit sidecar manifest");
        }
        let mut hasher = Hasher::new();
        hasher.update(b"aegis-segment-commit-sidecar-evidence-v1");
        update_u128(&mut hasher, manifest.run_id);
        hasher.update(&manifest.manifest_hash);
        update_u64(&mut hasher, manifest.entries.len() as u64);
        for entry in &manifest.entries {
            let commit_path =
                Self::segment_commit_path(directory, manifest.run_id, entry.segment_id);
            let proof = RunEventSegmentCommitProof::read_sidecar(&commit_path)?;
            if proof.run_id != manifest.run_id || !proof.binds_entry_fields(entry) {
                return Err("run event segment commit sidecar evidence mismatch");
            }
            update_u64(&mut hasher, entry.segment_id);
            hasher.update(&entry.segment_commit_hash);
            hasher.update(&proof.commit_hash);
        }
        Ok(*hasher.finalize().as_bytes())
    }

    pub fn recover_last_valid_ledger_mmap(
        directory: impl AsRef<Path>,
        manifest: &RunEventSegmentManifest,
    ) -> Result<RunEventRecoveryReport, &'static str> {
        if !manifest.is_valid() {
            return Err("invalid recovery segment manifest");
        }
        let mut events = Vec::new();
        let mut recovered_segment_count = 0usize;
        for entry in &manifest.entries {
            let path = Self::segment_path(directory.as_ref(), manifest.run_id, entry.segment_id);
            let commit_path =
                Self::segment_commit_path(directory.as_ref(), manifest.run_id, entry.segment_id);
            if !entry.matches_commit_file(&commit_path, manifest.run_id) {
                break;
            }
            let segment_events =
                match ArrowRunEventStream::read_events_mmap_with_capacity_evidence_and_file_hash(
                    &path,
                    usize::try_from(entry.event_count)
                        .map_err(|_| "run event segment count exceeds target width")?,
                ) {
                    Ok((segment_events, evidence, arrow_file_hash))
                        if evidence.total_file_bytes == entry.arrow_file_bytes
                            && arrow_file_hash == entry.arrow_file_hash
                            && entry.matches_events(&segment_events) =>
                    {
                        segment_events
                    }
                    _ => break,
                };
            recovered_segment_count += 1;
            events.extend(segment_events);
        }
        let ledger = RunEventLedger::from_events(manifest.run_id, events)
            .map_err(|_| "invalid last-valid run event ledger")?;
        let last_valid_event_id = ledger.events().last().map_or(0, |event| event.event_id);
        let recovery_hash = run_event_recovery_hash(
            manifest.manifest_hash,
            recovered_segment_count,
            last_valid_event_id,
            ledger.last_hash(),
        );
        Ok(RunEventRecoveryReport {
            ledger,
            expected_segment_count: manifest.entries.len(),
            recovered_segment_count,
            last_valid_event_id,
            recovery_hash,
        })
    }

    pub fn segment_path(directory: &Path, run_id: RunId, segment_id: u64) -> PathBuf {
        directory.join(format!("run-{run_id}-segment-{segment_id}.arrow"))
    }

    pub fn segment_commit_path(directory: &Path, run_id: RunId, segment_id: u64) -> PathBuf {
        directory.join(format!("run-{run_id}-segment-{segment_id}.commit"))
    }
}

impl ReplayChaosBenchReport {
    pub fn is_valid_physical_result(&self) -> bool {
        self.seed > 0
            && self.crash_points_exercised > 0
            && self.crash_points_exercised as usize == self.crash_points.len()
            && self.expected_event_count > 0
            && self.segment_count > 0
            && nonzero_hash(&self.segmented_arrow_audit_proof_hash)
            && nonzero_hash(&self.segmented_arrow_audit_logical_replay_hash)
            && nonzero_hash(&self.segmented_arrow_audit_mmap_evidence_hash)
            && nonzero_hash(&self.segmented_arrow_audit_segment_witness_hash)
            && self.segmented_arrow_audit_mmap_buffer_count > 0
            && self.column_scan_full_acceptance_count > 0
            && self.column_scan_missing_tail_rejection_count > 0
            && self
                .column_scan_full_acceptance_count
                .checked_add(self.column_scan_missing_tail_rejection_count)
                == Some(self.crash_points_exercised)
            && self.min_recovered_event_count <= self.max_recovered_event_count
            && self.max_recovered_event_count <= self.expected_event_count
            && self.all_recoveries_valid
            && nonzero_hash(&self.report_hash)
    }
}

impl ReplayChaosBench {
    pub fn run_seeded(
        directory: impl AsRef<Path>,
        seed: u64,
        iterations: u32,
        max_events_per_segment: usize,
        ledger: &RunEventLedger,
    ) -> Result<ReplayChaosBenchReport, &'static str> {
        if max_events_per_segment == 0 || ledger.is_empty() {
            return Err("invalid replay chaos bench config");
        }
        let segment_count =
            (ledger.len().saturating_add(max_events_per_segment - 1)) / max_events_per_segment;
        let mut rng_state = seed ^ (ledger.run_id as u64);
        let persisted_segment_counts = (0..iterations)
            .map(|iteration| {
                replay_chaos_persisted_segment_count(&mut rng_state, iteration, segment_count)
            })
            .collect::<Vec<_>>();
        Self::run_with_persisted_segment_counts(
            directory,
            seed,
            max_events_per_segment,
            ledger,
            &persisted_segment_counts,
        )
    }

    pub fn run_event_boundary_sweep(
        directory: impl AsRef<Path>,
        seed: u64,
        ledger: &RunEventLedger,
    ) -> Result<ReplayChaosBenchReport, &'static str> {
        let persisted_event_counts = (0..=ledger.len()).collect::<Vec<_>>();
        Self::run_with_persisted_segment_counts(directory, seed, 1, ledger, &persisted_event_counts)
    }

    pub fn run_approval_boundary_sweep(
        directory: impl AsRef<Path>,
        seed: u64,
        ledger: &RunEventLedger,
    ) -> Result<ReplayChaosBenchReport, &'static str> {
        let has_approval_event = ledger
            .events()
            .iter()
            .any(|event| event.kind == RunEventKind::ApprovalTokenRecorded);
        if !has_approval_event {
            return Err("approval boundary sweep requires approval event");
        }
        Self::run_event_boundary_sweep(directory, seed, ledger)
    }

    pub fn write_scorecard_artifact(
        path: impl AsRef<Path>,
        scorecard: &HarnessBenchScorecard,
        report: &ReplayChaosBenchReport,
    ) -> Result<[u8; 32], &'static str> {
        Self::write_scorecard_artifact_inner(path, scorecard, report, None)
    }

    pub fn write_scorecard_artifact_with_io_evidence(
        path: impl AsRef<Path>,
        scorecard: &HarnessBenchScorecard,
        report: &ReplayChaosBenchReport,
        io_evidence: &ReplayIoEvidence,
    ) -> Result<[u8; 32], &'static str> {
        if !io_evidence.proves_mmap_materialized_replay() {
            return Err("invalid replay chaos io evidence");
        }
        Self::write_scorecard_artifact_inner(path, scorecard, report, Some(io_evidence))
    }

    fn write_scorecard_artifact_inner(
        path: impl AsRef<Path>,
        scorecard: &HarnessBenchScorecard,
        report: &ReplayChaosBenchReport,
        io_evidence: Option<&ReplayIoEvidence>,
    ) -> Result<[u8; 32], &'static str> {
        if !scorecard.is_valid_physical_result()
            || !report.is_valid_physical_result()
            || scorecard.replay_hash != report.report_hash
            || scorecard.physical_witness_hash != report.report_hash
            || scorecard.crash_recovery_passed != report.all_recoveries_valid
        {
            return Err("invalid replay chaos scorecard artifact");
        }
        let artifact_body = ReplayChaosBenchArtifactBody {
            schema_version: 1,
            bench_name: "ReplayChaosBench",
            scorecard,
            report,
            io_evidence,
        };
        let body_payload = serde_json::to_vec_pretty(&artifact_body)
            .map_err(|_| "failed to serialize replay chaos scorecard body")?;
        let write_evidence = ReplayArtifactWriteEvidence::for_logical_payload(
            body_payload.len() as u64,
            blake3_hash_bytes(&body_payload),
            path.as_ref(),
        );
        let artifact = ReplayChaosBenchArtifact {
            schema_version: 1,
            bench_name: "ReplayChaosBench",
            scorecard,
            report,
            io_evidence,
            write_evidence: &write_evidence,
        };
        let payload = serde_json::to_vec_pretty(&artifact)
            .map_err(|_| "failed to serialize replay chaos scorecard artifact")?;
        write_synced_artifact(path.as_ref(), &payload)?;
        let mut hasher = Hasher::new();
        hasher.update(&payload);
        Ok(*hasher.finalize().as_bytes())
    }

    fn run_with_persisted_segment_counts(
        directory: impl AsRef<Path>,
        seed: u64,
        max_events_per_segment: usize,
        ledger: &RunEventLedger,
        persisted_segment_counts: &[usize],
    ) -> Result<ReplayChaosBenchReport, &'static str> {
        if seed == 0
            || persisted_segment_counts.is_empty()
            || max_events_per_segment == 0
            || ledger.is_empty()
            || !ledger.verify_hash_chain()
        {
            return Err("invalid replay chaos bench config");
        }
        std::fs::create_dir_all(directory.as_ref())
            .map_err(|_| "failed to create replay chaos bench directory")?;
        let proof_dir = directory
            .as_ref()
            .join(format!("seed-{seed}-column-scan-proof"));
        std::fs::create_dir_all(&proof_dir)
            .map_err(|_| "failed to create replay chaos column scan proof directory")?;
        let proof_manifest =
            RunEventSegmentArchive::write_ledger(&proof_dir, max_events_per_segment, ledger)?;
        let segmented_arrow_audit_proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&proof_dir, &proof_manifest)?;
        if segmented_arrow_audit_proof.event_count != ledger.len()
            || segmented_arrow_audit_proof.segment_count != proof_manifest.entries.len()
            || segmented_arrow_audit_proof.logical_replay_hash
                != run_event_logical_replay_hash(ledger.events())
        {
            return Err("invalid replay chaos segmented arrow column scan proof");
        }
        let mut crash_points = Vec::with_capacity(persisted_segment_counts.len());
        let mut min_recovered_event_count = usize::MAX;
        let mut max_recovered_event_count = 0usize;
        let mut all_recoveries_valid = true;
        let segment_count = proof_manifest.entries.len();
        let mut column_scan_full_acceptance_count = 0u32;
        let mut column_scan_missing_tail_rejection_count = 0u32;

        for (iteration_index, persisted_segment_count) in
            persisted_segment_counts.iter().copied().enumerate()
        {
            let iteration = iteration_index as u32;
            let iteration_dir = directory
                .as_ref()
                .join(format!("seed-{seed}-iter-{iteration}"));
            std::fs::create_dir_all(&iteration_dir)
                .map_err(|_| "failed to create replay chaos iteration directory")?;
            let manifest = RunEventSegmentArchive::write_ledger(
                &iteration_dir,
                max_events_per_segment,
                ledger,
            )?;
            if manifest.entries.len() != segment_count {
                return Err("replay chaos segmented arrow segment count drift");
            }
            if persisted_segment_count > segment_count {
                return Err("invalid replay chaos persisted segment count");
            }

            for entry in manifest.entries.iter().skip(persisted_segment_count) {
                let path = RunEventSegmentArchive::segment_path(
                    &iteration_dir,
                    manifest.run_id,
                    entry.segment_id,
                );
                if path.exists() {
                    std::fs::remove_file(path)
                        .map_err(|_| "failed to inject replay chaos crash point")?;
                }
            }

            let column_scan_after_crash_ok =
                RunEventSegmentArchive::prove_segmented_arrow_audit(&iteration_dir, &manifest)
                    .is_ok();
            if persisted_segment_count == segment_count {
                column_scan_full_acceptance_count = column_scan_full_acceptance_count
                    .checked_add(u32::from(column_scan_after_crash_ok))
                    .ok_or("replay chaos column scan acceptance count overflow")?;
                all_recoveries_valid &= column_scan_after_crash_ok;
            } else {
                column_scan_missing_tail_rejection_count = column_scan_missing_tail_rejection_count
                    .checked_add(u32::from(!column_scan_after_crash_ok))
                    .ok_or("replay chaos column scan rejection count overflow")?;
                all_recoveries_valid &= !column_scan_after_crash_ok;
            }

            let full_read_ok =
                RunEventSegmentArchive::read_ledger_mmap(&iteration_dir, &manifest).is_ok();
            if persisted_segment_count < manifest.entries.len() && full_read_ok {
                all_recoveries_valid = false;
            }

            let report =
                RunEventSegmentArchive::recover_last_valid_ledger_mmap(&iteration_dir, &manifest)?;
            let expected_recovered_event_count = manifest
                .entries
                .iter()
                .take(persisted_segment_count)
                .try_fold(0usize, |total, entry| {
                    total
                        .checked_add(
                            usize::try_from(entry.event_count)
                                .map_err(|_| "replay chaos event count exceeds target width")?,
                        )
                        .ok_or("replay chaos event count overflow")
                })?;
            let recovered_event_count = report.ledger.len();
            let prefix_matches =
                report.ledger.events() == &ledger.events()[..recovered_event_count];
            let report_valid = report.recovered_segment_count == persisted_segment_count
                && recovered_event_count == expected_recovered_event_count
                && report.last_valid_event_id == recovered_event_count as EventId
                && report.ledger.verify_hash_chain()
                && prefix_matches
                && nonzero_hash(&report.recovery_hash);
            all_recoveries_valid &= report_valid;
            min_recovered_event_count = min_recovered_event_count.min(recovered_event_count);
            max_recovered_event_count = max_recovered_event_count.max(recovered_event_count);
            crash_points.push(ReplayChaosCrashPoint {
                iteration,
                persisted_segment_count,
                recovered_event_count,
                last_valid_event_id: report.last_valid_event_id,
                recovery_hash: report.recovery_hash,
            });
        }

        let report_hash = replay_chaos_report_hash(&ReplayChaosReportHashInputs {
            seed,
            expected_ledger_hash: ledger.last_hash(),
            iterations: persisted_segment_counts.len() as u32,
            max_events_per_segment,
            segmented_arrow_audit_proof_hash: segmented_arrow_audit_proof.proof_hash,
            segmented_arrow_audit_logical_replay_hash: segmented_arrow_audit_proof
                .logical_replay_hash,
            segmented_arrow_audit_mmap_evidence_hash: segmented_arrow_audit_proof
                .mmap_evidence_hash,
            segmented_arrow_audit_segment_witness_hash: segmented_arrow_audit_proof
                .segment_witness_hash,
            segmented_arrow_audit_mmap_buffer_count: segmented_arrow_audit_proof.mmap_buffer_count,
            column_scan_full_acceptance_count,
            column_scan_missing_tail_rejection_count,
            all_recoveries_valid,
            crash_points: &crash_points,
        });
        Ok(ReplayChaosBenchReport {
            seed,
            crash_points_exercised: persisted_segment_counts.len() as u32,
            expected_event_count: ledger.len(),
            segment_count,
            segmented_arrow_audit_proof_hash: segmented_arrow_audit_proof.proof_hash,
            segmented_arrow_audit_logical_replay_hash: segmented_arrow_audit_proof
                .logical_replay_hash,
            segmented_arrow_audit_mmap_evidence_hash: segmented_arrow_audit_proof
                .mmap_evidence_hash,
            segmented_arrow_audit_segment_witness_hash: segmented_arrow_audit_proof
                .segment_witness_hash,
            segmented_arrow_audit_mmap_buffer_count: segmented_arrow_audit_proof.mmap_buffer_count,
            column_scan_full_acceptance_count,
            column_scan_missing_tail_rejection_count,
            min_recovered_event_count,
            max_recovered_event_count,
            all_recoveries_valid,
            report_hash,
            crash_points,
        })
    }
}

impl ReplayEnduranceBenchReport {
    pub fn is_valid_physical_result(&self) -> bool {
        self.simulated_hours >= 100
            && self.cycles_per_hour > 0
            && self.synthetic_cycle_count
                == self.simulated_hours.saturating_mul(self.cycles_per_hour)
            && self.checkpoint_cadence_cycles > 0
            && self.checkpoint_count > 0
            && self.context_fold_count == self.checkpoint_count
            && self.tail_recovered_context_fold_count >= self.tail_recovered_checkpoint_count
            && self.tail_recovered_context_fold_count
                <= self.tail_recovered_checkpoint_count.saturating_add(1)
            && self.context_fold_checkpoint_pair_count == self.checkpoint_count
            && self.event_count > 0
            && self.segment_count > 1
            && self.segmented_arrow_audit_segment_count == self.segment_count
            && self.segmented_arrow_audit_event_count == self.event_count
            && self.segmented_arrow_audit_total_file_bytes > 0
            && self.segmented_arrow_audit_mmap_buffer_count > 0
            && nonzero_hash(&self.segmented_arrow_audit_segment_witness_hash)
            && self.mmap_recovered_event_count == self.event_count
            && self.tail_drop_segment_count > 0
            && self.tail_recovered_segment_count + self.tail_drop_segment_count
                == self.segment_count
            && self.tail_recovered_event_count < self.event_count
            && self.tail_recovered_checkpoint_count > 0
            && self.tail_events_since_last_checkpoint <= self.checkpoint_cadence_event_bound
            && self.materialized_event_count == self.event_count
            && self.materialized_replay_bytes > 0
            && self.bounded_materialized_bytes >= self.materialized_replay_bytes
            && self.all_hash_chains_valid
            && self.full_replay_matches
            && self.tail_recovery_matches_prefix
            && self.mmap_materialized_replay_proven
            && self.materialization_within_bound
            && nonzero_hash(&self.manifest_hash)
            && nonzero_hash(&self.expected_ledger_hash)
            && self.mmap_recovered_ledger_hash == self.expected_ledger_hash
            && nonzero_hash(&self.tail_recovered_ledger_hash)
            && self.tail_recovered_ledger_hash != self.expected_ledger_hash
            && nonzero_hash(&self.context_fold_evidence_hash)
            && nonzero_hash(&self.segmented_arrow_audit_proof_hash)
            && nonzero_hash(&self.replay_determinism_proof_hash)
            && nonzero_hash(&self.run_checkpoint_hash)
            && nonzero_hash(&self.next_action_packet_hash)
            && nonzero_hash(&self.mmap_evidence_hash)
            && nonzero_hash(&self.tail_recovery_hash)
            && self.report_hash == replay_endurance_report_hash(self)
            && nonzero_hash(&self.report_hash)
    }
}

impl ReplayEnduranceBench {
    pub fn run_accelerated_100h_proof(
        directory: impl AsRef<Path>,
    ) -> Result<ReplayEnduranceBenchReport, &'static str> {
        Self::run_accelerated_proof(directory, 0xAEE6_0100_0000_0064_u128, 100, 24, 6, 24, 1)
    }

    pub fn run_accelerated_proof(
        directory: impl AsRef<Path>,
        run_id: RunId,
        simulated_hours: u32,
        cycles_per_hour: u32,
        checkpoint_cadence_cycles: u32,
        max_events_per_segment: usize,
        tail_drop_segment_count: usize,
    ) -> Result<ReplayEnduranceBenchReport, &'static str> {
        if run_id == 0
            || simulated_hours == 0
            || cycles_per_hour == 0
            || checkpoint_cadence_cycles == 0
            || max_events_per_segment == 0
            || tail_drop_segment_count == 0
        {
            return Err("invalid replay endurance bench config");
        }
        let synthetic_cycle_count = simulated_hours
            .checked_mul(cycles_per_hour)
            .ok_or("replay endurance cycle count overflow")?;
        let ledger =
            Self::build_synthetic_ledger(run_id, synthetic_cycle_count, checkpoint_cadence_cycles)?;
        if !ledger.verify_hash_chain() {
            return Err("invalid replay endurance ledger hash chain");
        }
        std::fs::create_dir_all(directory.as_ref())
            .map_err(|_| "failed to create replay endurance directory")?;
        let manifest =
            RunEventSegmentArchive::write_ledger(&directory, max_events_per_segment, &ledger)?;
        let segmented_arrow_audit_proof =
            RunEventSegmentArchive::prove_segmented_arrow_audit(&directory, &manifest)?;
        let replay_determinism_proof =
            RunEventSegmentArchive::prove_replay_determinism(&directory, &manifest)?;
        if tail_drop_segment_count >= manifest.entries.len() {
            return Err("invalid replay endurance tail drop");
        }
        let (mmap_recovered, mmap_evidence) =
            RunEventSegmentArchive::read_ledger_mmap_with_evidence(&directory, &manifest)?;
        if replay_determinism_proof.first_pass_ledger_hash != ledger.last_hash()
            || replay_determinism_proof.event_count != ledger.len()
            || replay_determinism_proof.manifest_hash != manifest.manifest_hash
        {
            return Err("replay endurance determinism proof mismatch");
        }
        let full_replay_matches = mmap_recovered.events() == ledger.events();

        let tail_dir = directory.as_ref().join("missing-tail-recovery");
        let tail_manifest =
            RunEventSegmentArchive::write_ledger(&tail_dir, max_events_per_segment, &ledger)?;
        let persisted_segment_count = tail_manifest
            .entries
            .len()
            .checked_sub(tail_drop_segment_count)
            .ok_or("invalid replay endurance persisted segment count")?;
        for entry in tail_manifest.entries.iter().skip(persisted_segment_count) {
            let path = RunEventSegmentArchive::segment_path(
                &tail_dir,
                tail_manifest.run_id,
                entry.segment_id,
            );
            if path.exists() {
                std::fs::remove_file(path)
                    .map_err(|_| "failed to inject replay endurance tail loss")?;
            }
        }
        let tail_report =
            RunEventSegmentArchive::recover_last_valid_ledger_mmap(&tail_dir, &tail_manifest)?;
        let tail_recovered_event_count = tail_report.ledger.len();
        let tail_recovery_matches_prefix =
            tail_report.ledger.events() == &ledger.events()[..tail_recovered_event_count];
        let checkpoint_count = count_checkpoint_events(&ledger);
        let context_fold_count = count_context_fold_events(&ledger);
        let tail_recovered_checkpoint_count = count_checkpoint_events(&tail_report.ledger);
        let tail_recovered_context_fold_count = count_context_fold_events(&tail_report.ledger);
        let context_fold_checkpoint_pair_count = count_context_fold_checkpoint_pairs(&ledger);
        let tail_events_since_last_checkpoint = events_since_last_checkpoint(&tail_report.ledger)
            .ok_or("missing endurance checkpoint")?;
        let checkpoint_cadence_event_bound = (checkpoint_cadence_cycles as usize)
            .saturating_mul(3)
            .saturating_add(1);
        let materialized_replay_bytes = mmap_evidence
            .materialized_run_event_bytes
            .saturating_add(mmap_evidence.materialized_hash_bytes);
        let bounded_materialized_bytes = ((ledger.len() * size_of::<RunEvent>()) as u64)
            .saturating_add((ledger.len() as u64).saturating_mul(128));
        let materialization_within_bound = materialized_replay_bytes <= bounded_materialized_bytes;
        let context_fold_evidence_hash = context_fold_replay_evidence_hash(&ledger);
        let run_checkpoint = RunCheckpoint::new(
            run_id,
            checkpoint_count as SubjectId,
            ledger.len(),
            manifest.entries.len(),
            ledger.last_hash(),
            manifest.manifest_hash,
            replay_determinism_proof.proof_hash,
            context_fold_evidence_hash,
        );
        let next_action_packet = NextActionPacket::new(
            run_id,
            1,
            run_checkpoint.checkpoint_hash,
            NextActionKind::RequestToolCall,
            0xAE61_5000_0000_0001_u128,
            endurance_hash("next-action-typed-tool-ir", run_id, synthetic_cycle_count),
            endurance_hash(
                "next-action-evidence-contract",
                run_id,
                synthetic_cycle_count,
            ),
            endurance_hash("next-action-policy-proof", run_id, synthetic_cycle_count),
            endurance_hash(
                "next-action-candidate-evidence",
                run_id,
                synthetic_cycle_count,
            ),
        );
        if !next_action_packet.is_valid_for_checkpoint(&run_checkpoint) {
            return Err("invalid replay endurance next action packet");
        }
        let all_hash_chains_valid = ledger.verify_hash_chain()
            && mmap_recovered.verify_hash_chain()
            && tail_report.ledger.verify_hash_chain();
        let mut report = ReplayEnduranceBenchReport {
            simulated_hours,
            cycles_per_hour,
            synthetic_cycle_count,
            checkpoint_cadence_cycles,
            checkpoint_count,
            context_fold_count,
            tail_recovered_context_fold_count,
            context_fold_checkpoint_pair_count,
            event_count: ledger.len(),
            segment_count: manifest.entries.len(),
            segmented_arrow_audit_segment_count: segmented_arrow_audit_proof.segment_count,
            segmented_arrow_audit_event_count: segmented_arrow_audit_proof.event_count,
            segmented_arrow_audit_total_file_bytes: segmented_arrow_audit_proof
                .total_arrow_file_bytes,
            segmented_arrow_audit_mmap_buffer_count: segmented_arrow_audit_proof.mmap_buffer_count,
            segmented_arrow_audit_segment_witness_hash: segmented_arrow_audit_proof
                .segment_witness_hash,
            mmap_recovered_event_count: mmap_recovered.len(),
            tail_drop_segment_count,
            tail_recovered_segment_count: tail_report.recovered_segment_count,
            tail_recovered_event_count,
            tail_recovered_checkpoint_count,
            tail_events_since_last_checkpoint,
            checkpoint_cadence_event_bound,
            materialized_event_count: mmap_evidence.materialized_event_count,
            materialized_replay_bytes,
            bounded_materialized_bytes,
            all_hash_chains_valid,
            full_replay_matches,
            tail_recovery_matches_prefix,
            mmap_materialized_replay_proven: mmap_evidence.proves_mmap_materialized_replay(),
            materialization_within_bound,
            manifest_hash: manifest.manifest_hash,
            expected_ledger_hash: ledger.last_hash(),
            mmap_recovered_ledger_hash: mmap_recovered.last_hash(),
            tail_recovered_ledger_hash: tail_report.ledger.last_hash(),
            context_fold_evidence_hash,
            segmented_arrow_audit_proof_hash: segmented_arrow_audit_proof.proof_hash,
            replay_determinism_proof_hash: replay_determinism_proof.proof_hash,
            run_checkpoint_hash: run_checkpoint.checkpoint_hash,
            next_action_packet_hash: next_action_packet.packet_hash,
            mmap_evidence_hash: mmap_evidence.evidence_hash,
            tail_recovery_hash: tail_report.recovery_hash,
            report_hash: [0; 32],
        };
        report.report_hash = replay_endurance_report_hash(&report);
        if !report.is_valid_physical_result() {
            return Err("invalid replay endurance proof");
        }
        Ok(report)
    }

    pub fn write_report_artifact(
        path: impl AsRef<Path>,
        report: &ReplayEnduranceBenchReport,
    ) -> Result<[u8; 32], &'static str> {
        if !report.is_valid_physical_result() {
            return Err("invalid replay endurance report artifact");
        }
        let artifact_body = ReplayEnduranceBenchArtifactBody {
            schema_version: 1,
            bench_name: "ReplayEnduranceBench",
            report,
        };
        let body_payload = serde_json::to_vec_pretty(&artifact_body)
            .map_err(|_| "failed to serialize replay endurance body")?;
        let write_evidence = ReplayArtifactWriteEvidence::for_logical_payload(
            body_payload.len() as u64,
            blake3_hash_bytes(&body_payload),
            path.as_ref(),
        );
        let artifact = ReplayEnduranceBenchArtifact {
            schema_version: 1,
            bench_name: "ReplayEnduranceBench",
            report,
            write_evidence: &write_evidence,
        };
        let payload = serde_json::to_vec_pretty(&artifact)
            .map_err(|_| "failed to serialize replay endurance artifact")?;
        write_synced_artifact(path.as_ref(), &payload)?;
        let mut hasher = Hasher::new();
        hasher.update(&payload);
        Ok(*hasher.finalize().as_bytes())
    }

    fn build_synthetic_ledger(
        run_id: RunId,
        synthetic_cycle_count: u32,
        checkpoint_cadence_cycles: u32,
    ) -> Result<RunEventLedger, &'static str> {
        let mut ledger = RunEventLedger::new(run_id);
        let goal_proof = GoalIntakeProof::from_goal_text(
            run_id,
            endurance_hash("operator", run_id, 0),
            endurance_hash("raw-goal-ref", run_id, 0),
            endurance_hash("policy-window", run_id, 0),
            "Run deterministic replay endurance proof with context folds and checkpoints",
            1,
            None,
        )
        .map_err(|_| "failed to build endurance goal intake proof")?;
        ledger
            .append_goal_intake_recorded(&goal_proof)
            .map_err(|_| "failed to append endurance goal intake")?;
        ledger
            .append_context_pack_built(1, 2_048, endurance_hash("context-pack", run_id, 0))
            .map_err(|_| "failed to append endurance context pack")?;
        ledger
            .append_llm_response_received(
                endurance_hash("llm-response", run_id, 0),
                endurance_hash("raw-text-ref", run_id, 0),
            )
            .map_err(|_| "failed to append endurance llm response")?;

        for cycle in 1..=synthetic_cycle_count {
            let subject = cycle as SubjectId;
            let typed_tool_ir_hash = endurance_hash("typed-tool-ir", run_id, cycle);
            let policy_proof_trace_hash = endurance_hash("policy-proof-trace", run_id, cycle);
            ledger
                .append_llm_derived_tool_call_requested(30_000 + subject, typed_tool_ir_hash)
                .map_err(|_| "failed to append endurance tool call")?;
            ledger
                .append_policy_decision_recorded(
                    10_000 + subject,
                    policy_proof_trace_hash,
                    typed_tool_ir_hash,
                )
                .map_err(|_| "failed to append endurance policy decision")?;
            ledger
                .append_operator_review_artifact_recorded(
                    20_000 + subject,
                    endurance_hash("operator-review-signing-target", run_id, cycle),
                    endurance_hash("operator-review-artifact", run_id, cycle),
                )
                .map_err(|_| "failed to append endurance operator review")?;
            let tool_execution_evidence = ToolExecutionEvidence::new(
                typed_tool_ir_hash,
                policy_proof_trace_hash,
                endurance_hash("tool-output", run_id, cycle),
                endurance_hash("physical-witness", run_id, cycle),
                ToolExecutorKind::Wasmtime,
                ToolExecutionStatus::Succeeded,
            );
            ledger
                .append_tool_call_completed(30_000 + subject, &tool_execution_evidence)
                .map_err(|_| "failed to append endurance tool completion")?;
            if cycle % checkpoint_cadence_cycles == 0 || cycle == synthetic_cycle_count {
                let fold_record = synthetic_context_fold_record(run_id, cycle)?;
                ledger
                    .append_context_fold_recorded(&fold_record)
                    .map_err(|_| "failed to append endurance context fold")?;
                ledger
                    .append_checkpoint_sealed(
                        40_000 + subject,
                        endurance_hash("checkpoint", run_id, cycle),
                    )
                    .map_err(|_| "failed to append endurance checkpoint")?;
            }
        }
        Ok(ledger)
    }
}

fn downcast_u64(batch: &RecordBatch, index: usize) -> Result<&UInt64Array, &'static str> {
    batch
        .column(index)
        .as_any()
        .downcast_ref::<UInt64Array>()
        .ok_or("invalid u64 run event column")
}

fn downcast_u8(batch: &RecordBatch, index: usize) -> Result<&UInt8Array, &'static str> {
    batch
        .column(index)
        .as_any()
        .downcast_ref::<UInt8Array>()
        .ok_or("invalid u8 run event column")
}

fn downcast_binary(batch: &RecordBatch, index: usize) -> Result<&BinaryArray, &'static str> {
    batch
        .column(index)
        .as_any()
        .downcast_ref::<BinaryArray>()
        .ok_or("invalid binary run event column")
}

fn hash_from_binary(value: &[u8]) -> Result<[u8; 32], &'static str> {
    value
        .try_into()
        .map_err(|_| "invalid persisted hash length")
}

fn arrow_buffer_from_mmap(mmap: Arc<Mmap>) -> Result<Buffer, &'static str> {
    let ptr =
        NonNull::new(mmap.as_ptr() as *mut u8).ok_or("invalid null mmap arrow buffer pointer")?;
    let len = mmap.len();
    if len == 0 {
        return Err("empty mmap arrow buffer");
    }
    // SAFETY: `Buffer::from_custom_allocation` requires a non-null, properly
    // aligned pointer to `len` valid bytes plus a custom deallocator that
    // outlives every clone of the returned `Buffer`. We:
    //   (1) obtained `NonNull::new(mmap.as_ptr() as *mut u8)` so the pointer
    //       is non-null; `mmap.as_ptr()` was sourced from a successful
    //       `MmapOptions::map`/`map_mut` call earlier in the call chain which
    //       guarantees proper alignment and a `mmap.len() > 0` byte range.
    //   (2) bounded `len = mmap.len()` so the slice `[ptr, ptr+len)` matches
    //       the mmap allocation exactly (no overflow at construction time).
    //   (3) wrapped the `Arc<Mmap>` (the allocation owner) in a custom
    //       `ArrowMmapBufferOwner` which `Drop`s the Arc only after the last
    //       `Buffer` clone is destroyed; this outlives every Arrow slice.
    // Buffer is logically immutable (no write through the `Buffer` mutability),
    // satisfying the underlying Arrow contract.
    Ok(unsafe {
        Buffer::from_custom_allocation(ptr, len, Arc::new(ArrowMmapBufferOwner { _mmap: mmap }))
    })
}

fn validate_arrow_ipc_stream_zero_copy_metadata(bytes: &[u8]) -> Result<usize, &'static str> {
    let mut offset = 0usize;
    let mut saw_schema = false;
    let mut record_batch_count = 0usize;
    while offset < bytes.len() {
        let prefix_end = offset
            .checked_add(4)
            .ok_or("arrow ipc metadata prefix overflow")?;
        if prefix_end > bytes.len() {
            return Err("truncated arrow ipc metadata prefix");
        }
        let mut prefix = [0u8; 4];
        prefix.copy_from_slice(&bytes[offset..prefix_end]);
        offset = prefix_end;

        let metadata_size = if prefix == [0xff; 4] {
            let size_end = offset
                .checked_add(4)
                .ok_or("arrow ipc continuation size overflow")?;
            if size_end > bytes.len() {
                return Err("truncated arrow ipc continuation size");
            }
            let mut size_bytes = [0u8; 4];
            size_bytes.copy_from_slice(&bytes[offset..size_end]);
            offset = size_end;
            u32::from_le_bytes(size_bytes) as usize
        } else {
            u32::from_le_bytes(prefix) as usize
        };
        if metadata_size == 0 {
            break;
        }

        let metadata_end = offset
            .checked_add(metadata_size)
            .ok_or("arrow ipc metadata range overflow")?;
        if metadata_end > bytes.len() {
            return Err("truncated arrow ipc metadata");
        }
        let message = root_as_message(&bytes[offset..metadata_end])
            .map_err(|_| "invalid arrow ipc metadata message")?;
        offset = metadata_end;

        match message.header_type() {
            MessageHeader::Schema => {
                saw_schema = true;
            }
            MessageHeader::RecordBatch => {
                if !saw_schema {
                    return Err("arrow ipc record batch before schema");
                }
                let batch = message
                    .header_as_record_batch()
                    .ok_or("invalid arrow ipc record batch header")?;
                if batch.compression().is_some() {
                    return Err("compressed arrow ipc cannot prove mmap zero-copy");
                }
                record_batch_count = record_batch_count
                    .checked_add(1)
                    .ok_or("arrow ipc record batch count overflow")?;
            }
            MessageHeader::DictionaryBatch => {
                return Err("dictionary arrow ipc batch cannot prove mmap zero-copy");
            }
            MessageHeader::NONE => {}
            _ => return Err("unsupported arrow ipc stream message"),
        }

        let body_length = message.bodyLength();
        if body_length < 0 {
            return Err("invalid negative arrow ipc body length");
        }
        offset = offset
            .checked_add(body_length as usize)
            .ok_or("arrow ipc body range overflow")?;
        if offset > bytes.len() {
            return Err("truncated arrow ipc body");
        }
    }
    if record_batch_count == 0 {
        return Err("arrow ipc stream has no record batch");
    }
    Ok(record_batch_count)
}

fn validate_batch_buffers_within_mmap(
    batch: &RecordBatch,
    mmap_start: usize,
    mmap_end: usize,
) -> Result<usize, &'static str> {
    let mut count = 0usize;
    for column in batch.columns() {
        count = count
            .checked_add(validate_array_buffers_within_mmap(
                &column.to_data(),
                mmap_start,
                mmap_end,
            )?)
            .ok_or("mmap arrow buffer count overflow")?;
    }
    Ok(count)
}

fn validate_array_buffers_within_mmap(
    data: &arrow::array::ArrayData,
    mmap_start: usize,
    mmap_end: usize,
) -> Result<usize, &'static str> {
    let mut count = 0usize;
    if let Some(nulls) = data.nulls() {
        validate_arrow_buffer_within_mmap(nulls.buffer(), mmap_start, mmap_end)?;
        count = count.saturating_add(1);
    }
    for buffer in data.buffers() {
        validate_arrow_buffer_within_mmap(buffer, mmap_start, mmap_end)?;
        count = count.saturating_add(1);
    }
    for child in data.child_data() {
        count = count
            .checked_add(validate_array_buffers_within_mmap(
                child, mmap_start, mmap_end,
            )?)
            .ok_or("mmap arrow child buffer count overflow")?;
    }
    Ok(count)
}

fn validate_arrow_buffer_within_mmap(
    buffer: &Buffer,
    mmap_start: usize,
    mmap_end: usize,
) -> Result<(), &'static str> {
    if buffer.is_empty() {
        return Ok(());
    }
    let start = buffer.as_ptr() as usize;
    let end = start
        .checked_add(buffer.len())
        .ok_or("mmap arrow buffer range overflow")?;
    if start < mmap_start || end > mmap_end {
        return Err("arrow column buffer is not mmap-backed");
    }
    Ok(())
}

#[derive(Default)]
struct RunEventColumnScanState {
    has_context_pack_built: bool,
    has_goal_intake_recorded: bool,
    has_llm_response_received: bool,
    has_pending_tool_call: bool,
    has_policy_decision_recorded: bool,
    has_tool_completion_recorded: bool,
    last_tool_call_id: SubjectId,
    last_tool_call_event_id: EventId,
    last_tool_ir_hash: [u8; 32],
    last_tool_execution_evidence_hash: [u8; 32],
    last_policy_event_id: EventId,
    last_policy_proof_trace_hash: [u8; 32],
    last_policy_tool_ir_hash: [u8; 32],
}

#[allow(clippy::too_many_arguments)]
fn scan_arrow_run_event_batch_columns(
    batch: &RecordBatch,
    expected_run_id: RunId,
    expected_event_id: &mut EventId,
    expected_previous_hash: &mut [u8; 32],
    scan_state: &mut RunEventColumnScanState,
    logical_hasher: &mut Hasher,
    segment_hasher: &mut Hasher,
    first_seen: &mut Option<(EventId, [u8; 32])>,
    last_seen: &mut Option<(EventId, [u8; 32])>,
) -> Result<usize, &'static str> {
    let event_ids = downcast_u64(batch, 0)?;
    let run_id_hi = downcast_u64(batch, 1)?;
    let run_id_lo = downcast_u64(batch, 2)?;
    let kinds = downcast_u8(batch, 3)?;
    let subject_id_hi = downcast_u64(batch, 4)?;
    let subject_id_lo = downcast_u64(batch, 5)?;
    let primary_hashes = downcast_binary(batch, 6)?;
    let secondary_hashes = downcast_binary(batch, 7)?;
    let previous_hashes = downcast_binary(batch, 8)?;
    let event_hashes = downcast_binary(batch, 9)?;

    for row in 0..batch.num_rows() {
        if event_ids.is_null(row)
            || run_id_hi.is_null(row)
            || run_id_lo.is_null(row)
            || kinds.is_null(row)
            || subject_id_hi.is_null(row)
            || subject_id_lo.is_null(row)
            || primary_hashes.is_null(row)
            || previous_hashes.is_null(row)
            || event_hashes.is_null(row)
        {
            return Err("null in required mmap arrow run event column");
        }
        let event_id = event_ids.value(row);
        let run_id = join_u128(run_id_hi.value(row), run_id_lo.value(row));
        let kind = RunEventKind::from_u8(kinds.value(row)).ok_or("invalid run event kind")?;
        let subject_id = join_u128(subject_id_hi.value(row), subject_id_lo.value(row));
        let primary_hash = hash_from_binary(primary_hashes.value(row))?;
        let secondary_hash = if secondary_hashes.is_null(row) {
            None
        } else {
            Some(hash_from_binary(secondary_hashes.value(row))?)
        };
        let previous_event_hash = hash_from_binary(previous_hashes.value(row))?;
        let event_hash = hash_from_binary(event_hashes.value(row))?;
        validate_run_event_column_scan_row(
            event_id,
            run_id,
            kind,
            subject_id,
            primary_hash,
            secondary_hash,
            previous_event_hash,
            event_hash,
            expected_run_id,
            *expected_event_id,
            *expected_previous_hash,
            scan_state,
        )?;
        if first_seen.is_none() {
            *first_seen = Some((event_id, event_hash));
        }
        *last_seen = Some((event_id, event_hash));
        update_run_event_logical_replay_hasher(
            logical_hasher,
            event_id,
            run_id,
            kind,
            subject_id,
            primary_hash,
            secondary_hash,
            previous_event_hash,
            event_hash,
        );
        segment_hasher.update(&event_hash);
        *expected_previous_hash = event_hash;
        *expected_event_id = expected_event_id
            .checked_add(1)
            .ok_or("mmap arrow column scan event id overflow")?;
    }
    Ok(batch.num_rows())
}

#[allow(clippy::too_many_arguments)]
fn validate_run_event_column_scan_row(
    event_id: EventId,
    run_id: RunId,
    kind: RunEventKind,
    subject_id: SubjectId,
    primary_hash: [u8; 32],
    secondary_hash: Option<[u8; 32]>,
    previous_event_hash: [u8; 32],
    event_hash: [u8; 32],
    expected_run_id: RunId,
    expected_event_id: EventId,
    expected_previous_hash: [u8; 32],
    scan_state: &mut RunEventColumnScanState,
) -> Result<(), &'static str> {
    if run_id != expected_run_id
        || event_id != expected_event_id
        || previous_event_hash != expected_previous_hash
        || !nonzero_hash(&primary_hash)
        || secondary_hash.is_some_and(|secondary| !nonzero_hash(&secondary))
    {
        return Err("invalid mmap arrow run event column fields");
    }
    if matches!(
        kind,
        RunEventKind::ContextPackBuilt
            | RunEventKind::LLMResponseReceived
            | RunEventKind::ToolCallCompleted
            | RunEventKind::PolicyDecisionRecorded
            | RunEventKind::ApprovalTokenRecorded
            | RunEventKind::OperatorReviewArtifactRecorded
            | RunEventKind::ColdVectorExpansionRecorded
            | RunEventKind::AgenticEvidenceExecutionRecorded
            | RunEventKind::SkillAdmissionRecorded
            | RunEventKind::BrowserOpsBenchVerificationRecorded
            | RunEventKind::ClusterCandidateAccepted
            | RunEventKind::ShadowSealRecorded
            | RunEventKind::CircuitBreakerTripped
            | RunEventKind::ContextFoldRecorded
            | RunEventKind::MemoryCommitRecorded
            | RunEventKind::ContextPackCandidateProofRecorded
            | RunEventKind::TaskSelectionProofRecorded
            | RunEventKind::BrowserObservationPacketRecorded
            | RunEventKind::GoalIntakeRecorded
            | RunEventKind::LabEventRecorded
    ) && secondary_hash.is_none()
    {
        return Err("missing mmap arrow run event secondary hash");
    }
    if matches!(
        kind,
        RunEventKind::ContextPackBuilt
            | RunEventKind::ContextPackCandidateProofRecorded
            | RunEventKind::TaskSelectionProofRecorded
            | RunEventKind::LLMResponseReceived
            | RunEventKind::ContextFoldRecorded
            | RunEventKind::ToolCallRequested
            | RunEventKind::ToolCallCompleted
            | RunEventKind::PolicyDecisionRecorded
            | RunEventKind::ApprovalTokenRecorded
            | RunEventKind::OperatorReviewArtifactRecorded
            | RunEventKind::ColdVectorExpansionRecorded
            | RunEventKind::AgenticEvidenceExecutionRecorded
            | RunEventKind::SkillAdmissionRecorded
            | RunEventKind::BrowserOpsBenchVerificationRecorded
            | RunEventKind::ClusterCandidateAccepted
            | RunEventKind::ShadowSealRecorded
            | RunEventKind::CircuitBreakerTripped
            | RunEventKind::MemoryCommitRecorded
            | RunEventKind::CheckpointSealed
    ) && !scan_state.has_goal_intake_recorded
    {
        return Err("mmap arrow run event missing prior goal intake");
    }
    if matches!(
        kind,
        RunEventKind::LLMResponseReceived
            | RunEventKind::ContextFoldRecorded
            | RunEventKind::ContextPackCandidateProofRecorded
    ) && !scan_state.has_context_pack_built
    {
        return Err("mmap arrow run event missing prior context pack");
    }
    if matches!(
        kind,
        RunEventKind::ToolCallRequested | RunEventKind::CircuitBreakerTripped
    ) && !scan_state.has_llm_response_received
    {
        return Err("mmap arrow run event missing prior llm response");
    }
    if kind == RunEventKind::ToolCallCompleted && !scan_state.has_pending_tool_call {
        return Err("mmap arrow run event missing prior tool call");
    }
    if kind == RunEventKind::MemoryCommitRecorded && !scan_state.has_tool_completion_recorded {
        return Err("mmap arrow run event memory commit missing tool completion");
    }
    if kind == RunEventKind::ToolCallRequested && scan_state.has_pending_tool_call {
        return Err("mmap arrow run event nested pending tool call");
    }
    if matches!(
        kind,
        RunEventKind::ToolCallCompleted
            | RunEventKind::ApprovalTokenRecorded
            | RunEventKind::OperatorReviewArtifactRecorded
    ) && !scan_state.has_policy_decision_recorded
    {
        return Err("mmap arrow run event missing prior policy decision");
    }
    if kind == RunEventKind::ToolCallCompleted {
        let evidence_hash =
            secondary_hash.ok_or("missing mmap arrow tool execution evidence hash")?;
        if subject_id != scan_state.last_tool_call_id
            || scan_state.last_policy_event_id <= scan_state.last_tool_call_event_id
            || scan_state.last_tool_ir_hash != scan_state.last_policy_tool_ir_hash
            || primary_hash
                != tool_call_completion_binding_hash(
                    subject_id,
                    scan_state.last_tool_ir_hash,
                    scan_state.last_policy_proof_trace_hash,
                    evidence_hash,
                )
        {
            return Err("mmap arrow run event tool completion binding mismatch");
        }
    }
    if kind == RunEventKind::MemoryCommitRecorded {
        let binding_hash = secondary_hash.ok_or("missing mmap arrow memory commit binding")?;
        if subject_id == 0
            || scan_state.has_pending_tool_call
            || !nonzero_hash(&scan_state.last_tool_execution_evidence_hash)
            || binding_hash
                != memory_commit_binding_hash(
                    subject_id,
                    scan_state.last_tool_execution_evidence_hash,
                    primary_hash,
                )
        {
            return Err("mmap arrow run event memory commit binding mismatch");
        }
    }

    let computed_hash = compute_run_event_hash(
        event_id,
        run_id,
        kind,
        subject_id,
        primary_hash,
        secondary_hash,
        previous_event_hash,
    );
    if event_hash != computed_hash {
        return Err("mmap arrow run event hash mismatch");
    }
    match kind {
        RunEventKind::GoalIntakeRecorded => scan_state.has_goal_intake_recorded = true,
        RunEventKind::ContextPackBuilt => scan_state.has_context_pack_built = true,
        RunEventKind::LLMResponseReceived => scan_state.has_llm_response_received = true,
        RunEventKind::ToolCallRequested => {
            scan_state.has_pending_tool_call = true;
            scan_state.has_tool_completion_recorded = false;
            scan_state.last_tool_call_id = subject_id;
            scan_state.last_tool_call_event_id = event_id;
            scan_state.last_tool_ir_hash = primary_hash;
        }
        RunEventKind::ToolCallCompleted => {
            scan_state.has_pending_tool_call = false;
            scan_state.has_tool_completion_recorded = true;
            scan_state.last_tool_execution_evidence_hash =
                secondary_hash.ok_or("missing mmap arrow tool execution evidence hash")?;
        }
        RunEventKind::PolicyDecisionRecorded => {
            scan_state.has_policy_decision_recorded = true;
            scan_state.last_policy_event_id = event_id;
            scan_state.last_policy_proof_trace_hash = primary_hash;
            scan_state.last_policy_tool_ir_hash =
                secondary_hash.ok_or("missing mmap arrow policy typed tool ir hash")?;
        }
        _ => {}
    }
    Ok(())
}

#[derive(Default)]
struct BinaryRunEventScanState {
    has_context_pack_built: bool,
    has_goal_intake_recorded: bool,
    has_llm_response_received: bool,
    has_pending_tool_call: bool,
    has_policy_decision_recorded: bool,
    has_tool_completion_recorded: bool,
    last_tool_call_id: SubjectId,
    last_tool_call_event_id: EventId,
    last_tool_ir_hash: [u8; 32],
    last_tool_execution_evidence_hash: [u8; 32],
    last_policy_event_id: EventId,
    last_policy_proof_trace_hash: [u8; 32],
    last_policy_tool_ir_hash: [u8; 32],
}

fn encode_binary_run_event_record(event: &RunEvent) -> [u8; BINARY_RUN_EVENT_RECORD_BYTES] {
    let mut record = [0u8; BINARY_RUN_EVENT_RECORD_BYTES];
    record[0..8].copy_from_slice(&event.event_id.to_le_bytes());
    record[8..24].copy_from_slice(&event.run_id.to_le_bytes());
    record[24] = event.kind as u8;
    record[25] = u8::from(event.secondary_hash.is_some());
    record[32..48].copy_from_slice(&event.subject_id.to_le_bytes());
    record[48..80].copy_from_slice(&event.primary_hash);
    if let Some(secondary_hash) = event.secondary_hash {
        record[80..112].copy_from_slice(&secondary_hash);
    }
    record[112..144].copy_from_slice(&event.previous_event_hash);
    record[144..176].copy_from_slice(&event.event_hash);
    let record_hash = binary_run_event_record_hash(&record[0..176]);
    record[176..184].copy_from_slice(&record_hash[0..8]);
    record
}

fn decode_binary_run_event_record(record: &[u8]) -> Result<RunEvent, &'static str> {
    if record.len() != BINARY_RUN_EVENT_RECORD_BYTES {
        return Err("invalid binary run event record length");
    }
    let expected_record_hash = binary_run_event_record_hash(&record[0..176]);
    if record[176..184] != expected_record_hash[0..8] {
        return Err("binary run event record hash mismatch");
    }
    let secondary_flag = record[25];
    if secondary_flag > 1 || record[26..32].iter().any(|byte| *byte != 0) {
        return Err("invalid binary run event record flags");
    }
    let secondary_hash = if secondary_flag == 1 {
        Some(hash_from_slice(&record[80..112])?)
    } else {
        if record[80..112].iter().any(|byte| *byte != 0) {
            return Err("unexpected binary run event secondary hash bytes");
        }
        None
    };
    let event = RunEvent {
        event_id: read_u64_le(record, 0)?,
        run_id: read_u128_le(record, 8)?,
        kind: RunEventKind::from_u8(record[24]).ok_or("invalid binary run event kind")?,
        subject_id: read_u128_le(record, 32)?,
        primary_hash: hash_from_slice(&record[48..80])?,
        secondary_hash,
        previous_event_hash: hash_from_slice(&record[112..144])?,
        event_hash: hash_from_slice(&record[144..176])?,
    };
    if !event.is_valid() {
        return Err("invalid binary run event");
    }
    Ok(event)
}

fn validate_binary_run_event_record(
    record: &[u8],
    expected_run_id: RunId,
    expected_event_id: EventId,
    expected_previous_hash: [u8; 32],
    scan_state: &mut BinaryRunEventScanState,
) -> Result<[u8; 32], &'static str> {
    if record.len() != BINARY_RUN_EVENT_RECORD_BYTES {
        return Err("invalid binary run event record length");
    }
    let expected_record_hash = binary_run_event_record_hash(&record[0..176]);
    if record[176..184] != expected_record_hash[0..8] {
        return Err("binary run event record hash mismatch");
    }
    if record[26..32].iter().any(|byte| *byte != 0) {
        return Err("invalid binary run event record flags");
    }

    let event_id = read_u64_le(record, 0)?;
    let run_id = read_u128_le(record, 8)?;
    let kind = RunEventKind::from_u8(record[24]).ok_or("invalid binary run event kind")?;
    let secondary_flag = record[25];
    let subject_id = read_u128_le(record, 32)?;
    let primary_hash = hash_from_slice(&record[48..80])?;
    let secondary_hash = match secondary_flag {
        0 => {
            if record[80..112].iter().any(|byte| *byte != 0) {
                return Err("unexpected binary run event secondary hash bytes");
            }
            None
        }
        1 => Some(hash_from_slice(&record[80..112])?),
        _ => return Err("invalid binary run event secondary flag"),
    };
    let previous_event_hash = hash_from_slice(&record[112..144])?;
    let event_hash = hash_from_slice(&record[144..176])?;
    if event_id != expected_event_id
        || run_id != expected_run_id
        || previous_event_hash != expected_previous_hash
        || !nonzero_hash(&primary_hash)
        || secondary_hash.is_some_and(|secondary| !nonzero_hash(&secondary))
    {
        return Err("invalid binary run event fields");
    }
    if matches!(
        kind,
        RunEventKind::ContextPackBuilt
            | RunEventKind::LLMResponseReceived
            | RunEventKind::ToolCallCompleted
            | RunEventKind::PolicyDecisionRecorded
            | RunEventKind::ApprovalTokenRecorded
            | RunEventKind::OperatorReviewArtifactRecorded
            | RunEventKind::ColdVectorExpansionRecorded
            | RunEventKind::AgenticEvidenceExecutionRecorded
            | RunEventKind::SkillAdmissionRecorded
            | RunEventKind::BrowserOpsBenchVerificationRecorded
            | RunEventKind::ClusterCandidateAccepted
            | RunEventKind::ShadowSealRecorded
            | RunEventKind::CircuitBreakerTripped
            | RunEventKind::ContextFoldRecorded
            | RunEventKind::MemoryCommitRecorded
            | RunEventKind::ContextPackCandidateProofRecorded
            | RunEventKind::TaskSelectionProofRecorded
            | RunEventKind::BrowserObservationPacketRecorded
            | RunEventKind::GoalIntakeRecorded
            | RunEventKind::LabEventRecorded
    ) && secondary_hash.is_none()
    {
        return Err("missing binary run event secondary hash");
    }
    if matches!(
        kind,
        RunEventKind::ContextPackBuilt
            | RunEventKind::ContextPackCandidateProofRecorded
            | RunEventKind::TaskSelectionProofRecorded
            | RunEventKind::LLMResponseReceived
            | RunEventKind::ContextFoldRecorded
            | RunEventKind::ToolCallRequested
            | RunEventKind::ToolCallCompleted
            | RunEventKind::PolicyDecisionRecorded
            | RunEventKind::ApprovalTokenRecorded
            | RunEventKind::OperatorReviewArtifactRecorded
            | RunEventKind::ColdVectorExpansionRecorded
            | RunEventKind::AgenticEvidenceExecutionRecorded
            | RunEventKind::SkillAdmissionRecorded
            | RunEventKind::BrowserOpsBenchVerificationRecorded
            | RunEventKind::ClusterCandidateAccepted
            | RunEventKind::ShadowSealRecorded
            | RunEventKind::CircuitBreakerTripped
            | RunEventKind::MemoryCommitRecorded
            | RunEventKind::CheckpointSealed
    ) && !scan_state.has_goal_intake_recorded
    {
        return Err("binary run event missing prior goal intake");
    }
    if matches!(
        kind,
        RunEventKind::LLMResponseReceived
            | RunEventKind::ContextFoldRecorded
            | RunEventKind::ContextPackCandidateProofRecorded
    ) && !scan_state.has_context_pack_built
    {
        return Err("binary run event missing prior context pack");
    }
    if matches!(
        kind,
        RunEventKind::ToolCallRequested | RunEventKind::CircuitBreakerTripped
    ) && !scan_state.has_llm_response_received
    {
        return Err("binary run event missing prior llm response");
    }
    if kind == RunEventKind::ToolCallCompleted && !scan_state.has_pending_tool_call {
        return Err("binary run event missing prior tool call");
    }
    if kind == RunEventKind::MemoryCommitRecorded && !scan_state.has_tool_completion_recorded {
        return Err("binary run event memory commit missing tool completion");
    }
    if kind == RunEventKind::ToolCallRequested && scan_state.has_pending_tool_call {
        return Err("binary run event nested pending tool call");
    }
    if matches!(
        kind,
        RunEventKind::ToolCallCompleted
            | RunEventKind::ApprovalTokenRecorded
            | RunEventKind::OperatorReviewArtifactRecorded
    ) && !scan_state.has_policy_decision_recorded
    {
        return Err("binary run event missing prior policy decision");
    }
    if kind == RunEventKind::ToolCallCompleted {
        let evidence_hash = secondary_hash.ok_or("missing binary tool execution evidence hash")?;
        if subject_id != scan_state.last_tool_call_id
            || scan_state.last_policy_event_id <= scan_state.last_tool_call_event_id
            || scan_state.last_tool_ir_hash != scan_state.last_policy_tool_ir_hash
            || primary_hash
                != tool_call_completion_binding_hash(
                    subject_id,
                    scan_state.last_tool_ir_hash,
                    scan_state.last_policy_proof_trace_hash,
                    evidence_hash,
                )
        {
            return Err("binary run event tool completion binding mismatch");
        }
    }
    if kind == RunEventKind::MemoryCommitRecorded {
        let binding_hash = secondary_hash.ok_or("missing binary memory commit binding")?;
        if subject_id == 0
            || scan_state.has_pending_tool_call
            || !nonzero_hash(&scan_state.last_tool_execution_evidence_hash)
            || binding_hash
                != memory_commit_binding_hash(
                    subject_id,
                    scan_state.last_tool_execution_evidence_hash,
                    primary_hash,
                )
        {
            return Err("binary run event memory commit binding mismatch");
        }
    }

    let computed_hash = compute_run_event_hash(
        event_id,
        run_id,
        kind,
        subject_id,
        primary_hash,
        secondary_hash,
        previous_event_hash,
    );
    if event_hash != computed_hash {
        return Err("binary run event hash mismatch");
    }
    match kind {
        RunEventKind::GoalIntakeRecorded => scan_state.has_goal_intake_recorded = true,
        RunEventKind::ContextPackBuilt => scan_state.has_context_pack_built = true,
        RunEventKind::LLMResponseReceived => scan_state.has_llm_response_received = true,
        RunEventKind::ToolCallRequested => {
            scan_state.has_pending_tool_call = true;
            scan_state.has_tool_completion_recorded = false;
            scan_state.last_tool_call_id = subject_id;
            scan_state.last_tool_call_event_id = event_id;
            scan_state.last_tool_ir_hash = primary_hash;
        }
        RunEventKind::ToolCallCompleted => {
            scan_state.has_pending_tool_call = false;
            scan_state.has_tool_completion_recorded = true;
            scan_state.last_tool_execution_evidence_hash =
                secondary_hash.ok_or("missing binary tool execution evidence hash")?;
        }
        RunEventKind::PolicyDecisionRecorded => {
            scan_state.has_policy_decision_recorded = true;
            scan_state.last_policy_event_id = event_id;
            scan_state.last_policy_proof_trace_hash = primary_hash;
            scan_state.last_policy_tool_ir_hash =
                secondary_hash.ok_or("missing binary policy typed tool ir hash")?;
        }
        _ => {}
    }
    Ok(event_hash)
}

fn binary_run_event_payload_hash(events: &[RunEvent]) -> [u8; 32] {
    let mut hasher = binary_run_event_payload_hasher(events.len());
    for event in events {
        hasher.update(&encode_binary_run_event_record(event));
    }
    *hasher.finalize().as_bytes()
}

fn binary_run_event_payload_hasher(event_count: usize) -> Hasher {
    let mut hasher = Hasher::new();
    hasher.update(b"binary-run-event-segment-v8");
    hasher.update(&binary_run_event_schema_hash());
    update_u64(&mut hasher, BINARY_RUN_EVENT_HEADER_BYTES as u64);
    update_u64(&mut hasher, BINARY_RUN_EVENT_RECORD_BYTES as u64);
    update_u64(&mut hasher, event_count as u64);
    hasher
}

fn binary_run_event_schema_hash() -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"binary-run-event-schema-v11");
    hasher.update(BINARY_RUN_EVENT_MAGIC);
    update_u64(&mut hasher, BINARY_RUN_EVENT_FORMAT_VERSION);
    update_u64(&mut hasher, BINARY_RUN_EVENT_HEADER_BYTES as u64);
    update_u64(&mut hasher, BINARY_RUN_EVENT_RECORD_BYTES as u64);
    hasher.update(
        b"event_id:u64,run_id:u128,kind:u8,secondary_flag:u8,\
          reserved:u8[6],subject_id:u128,primary_hash:blake3[32],\
          secondary_hash:optional-blake3[32],previous_event_hash:blake3[32],\
          event_hash:blake3[32],record_hash_prefix:blake3[8],\
          kind14:memory_commit_recorded,kind15:context_pack_candidate_proof_recorded,\
          kind16:task_selection_proof_recorded,kind17:browser_observation_packet_recorded,\
          kind18:goal_intake_recorded,kind19:agentic_evidence_execution_recorded,\
          kind20:skill_admission_recorded,kind21:browser_ops_bench_verification_recorded,\
          kind22:cluster_candidate_accepted,kind23:shadow_seal_recorded,\
          kind24:lab_event_recorded",
    );
    *hasher.finalize().as_bytes()
}

fn arrow_run_event_schema_hash() -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"arrow-run-event-schema-v7");
    hasher.update(
        b"event_id:u64,run_id_hi:u64,run_id_lo:u64,kind:u8,\
          subject_id_hi:u64,subject_id_lo:u64,primary_hash:binary,\
          secondary_hash:nullable-binary,previous_event_hash:binary,event_hash:binary,\
          kind14:memory_commit_recorded,kind15:context_pack_candidate_proof_recorded,\
          kind16:task_selection_proof_recorded,kind17:browser_observation_packet_recorded,\
          kind18:goal_intake_recorded,kind19:agentic_evidence_execution_recorded,\
          kind20:skill_admission_recorded,kind21:browser_ops_bench_verification_recorded,\
          kind22:cluster_candidate_accepted,kind23:shadow_seal_recorded,\
          kind24:lab_event_recorded",
    );
    *hasher.finalize().as_bytes()
}

fn arrow_stream_file_evidence(path: &Path) -> Result<(u64, [u8; 32]), &'static str> {
    let mut buffer = Vec::new();
    arrow_stream_file_evidence_with_buffer(path, &mut buffer)
}

fn arrow_stream_file_evidence_with_buffer(
    path: &Path,
    buffer: &mut Vec<u8>,
) -> Result<(u64, [u8; 32]), &'static str> {
    let file = File::open(path).map_err(|_| "failed to open arrow stream file evidence")?;
    let file_bytes = file
        .metadata()
        .map_err(|_| "failed to stat arrow stream file evidence")?
        .len();
    if file_bytes == 0 {
        return Err("empty arrow stream file evidence");
    }
    if file_bytes <= ARROW_STREAM_FILE_EVIDENCE_READ_LIMIT_BYTES {
        buffer.clear();
        buffer.reserve(file_bytes as usize);
        let read_bytes = file
            .take(file_bytes.saturating_add(1))
            .read_to_end(buffer)
            .map_err(|_| "failed to read small arrow stream file evidence")?;
        if read_bytes as u64 != file_bytes {
            return Err("small arrow stream file evidence size changed during read");
        }
        return Ok((file_bytes, blake3_hash_bytes(buffer)));
    }
    // SAFETY: `file` is read-only above and `MmapOptions::new().map(&file)`
    // defaults to a mapping the size of the file. The surrounding function
    // (`read_arrow_stream_file_evidence_blob`) checks `file_bytes <
    // SMALL_FILE_THRESHOLD` and exits via the read+blob path above before
    // reaching this branch, so the mapping is the canonical tooling for
    // hash-evidence of an arrow stream file. The mapping is read-only,
    // immediately hashed via `blake3_hash_bytes(&mmap[..])`, then dropped
    // before `file` goes out of scope.
    let mmap = unsafe {
        MmapOptions::new()
            .map(&file)
            .map_err(|_| "failed to mmap arrow stream file evidence")?
    };
    Ok((file_bytes, blake3_hash_bytes(&mmap[..])))
}

impl RunEventSegmentCacheKey {
    fn from_entry(run_id: RunId, entry: &RunEventSegmentEntry) -> Self {
        Self {
            run_id,
            segment_id: entry.segment_id,
            event_count: entry.event_count,
            arrow_file_bytes: entry.arrow_file_bytes,
            arrow_file_hash: entry.arrow_file_hash,
            segment_commit_hash: entry.segment_commit_hash,
        }
    }
}

fn run_event_segment_cache()
-> &'static RwLock<HashMap<RunEventSegmentCacheKey, RunEventSegmentCacheEntry>> {
    static CACHE: OnceLock<RwLock<HashMap<RunEventSegmentCacheKey, RunEventSegmentCacheEntry>>> =
        OnceLock::new();
    CACHE.get_or_init(|| RwLock::new(HashMap::new()))
}

fn run_event_segment_cache_get(key: &RunEventSegmentCacheKey) -> Option<RunEventSegmentCacheEntry> {
    run_event_segment_cache().read().get(key).cloned()
}

fn run_event_segment_cache_put(key: RunEventSegmentCacheKey, entry: RunEventSegmentCacheEntry) {
    let mut cache = run_event_segment_cache().write();
    if cache.len() >= RUN_EVENT_SEGMENT_CACHE_MAX_ENTRIES && !cache.contains_key(&key) {
        cache.clear();
    }
    cache.insert(key, entry);
}

fn binary_run_event_record_hash(bytes: &[u8]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"binary-run-event-record-v1");
    hasher.update(bytes);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn binary_run_event_recovery_hash(
    run_id: RunId,
    declared_event_count: usize,
    recovered_event_count: usize,
    file_bytes: u64,
    trailing_partial_bytes: usize,
    last_valid_event_id: EventId,
    last_event_hash: [u8; 32],
    recovered_payload_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"binary-run-event-recovery-v1");
    hasher.update(&run_id.to_le_bytes());
    update_u64(&mut hasher, declared_event_count as u64);
    update_u64(&mut hasher, recovered_event_count as u64);
    update_u64(&mut hasher, file_bytes);
    update_u64(&mut hasher, trailing_partial_bytes as u64);
    update_u64(&mut hasher, last_valid_event_id);
    hasher.update(&last_event_hash);
    hasher.update(&recovered_payload_hash);
    *hasher.finalize().as_bytes()
}

fn hash_from_slice(bytes: &[u8]) -> Result<[u8; 32], &'static str> {
    bytes
        .try_into()
        .map_err(|_| "invalid binary run event hash length")
}

fn read_u64_le(bytes: &[u8], offset: usize) -> Result<u64, &'static str> {
    let end = offset
        .checked_add(8)
        .ok_or("binary run event offset overflow")?;
    let slice = bytes
        .get(offset..end)
        .ok_or("binary run event u64 out of bounds")?;
    Ok(u64::from_le_bytes(
        slice
            .try_into()
            .map_err(|_| "invalid binary run event u64")?,
    ))
}

fn read_usize_le(bytes: &[u8], offset: usize) -> Result<usize, &'static str> {
    usize::try_from(read_u64_le(bytes, offset)?)
        .map_err(|_| "binary run event integer exceeds target width")
}

fn read_u128_le(bytes: &[u8], offset: usize) -> Result<u128, &'static str> {
    let end = offset
        .checked_add(16)
        .ok_or("binary run event offset overflow")?;
    let slice = bytes
        .get(offset..end)
        .ok_or("binary run event u128 out of bounds")?;
    Ok(u128::from_le_bytes(
        slice
            .try_into()
            .map_err(|_| "invalid binary run event u128")?,
    ))
}

fn decode_bool_flag(value: u8) -> Result<bool, &'static str> {
    match value {
        0 => Ok(false),
        1 => Ok(true),
        _ => Err("invalid binary bool flag"),
    }
}

pub fn approval_token_binding_hash(review_packet_hash: [u8; 32], expires_at_ms: u64) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"approval-token-binding-v1");
    hasher.update(&review_packet_hash);
    update_u64(&mut hasher, expires_at_ms);
    *hasher.finalize().as_bytes()
}

pub fn browser_observation_packet_replay_binding_hash(
    packet: &BrowserObservationPacket,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-observation-packet-replay-binding-v1");
    hasher.update(&packet.packet_hash);
    hasher.update(&packet.proof.proof_hash);
    hasher.update(&packet.action_trace.trace_hash);
    hasher.update(&packet.raw_artifact_manifest_hash);
    hasher.update(&packet.modality_bundle_hash);
    hasher.update(&packet.collector_provenance_hash);
    hasher.update(&packet.proof.browser_session_hash);
    hasher.update(&packet.proof.redaction_policy_hash);
    hasher.update(&packet.proof.policy_window_hash);
    *hasher.finalize().as_bytes()
}

pub fn agentic_evidence_sdk_run_replay_binding_hash(run: &AgenticEvidenceSdkRun) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-sdk-run-replay-binding-v1");
    hasher.update(&run.run_hash);
    hasher.update(&run.manifest.manifest_hash);
    hasher.update(&run.manifest.program_hash);
    hasher.update(&run.manifest.index_epoch_hash);
    update_u64(&mut hasher, run.manifest.max_fuel as u64);
    update_u64(&mut hasher, run.manifest.step_count as u64);
    hasher.update(&run.manifest.required_primitive_hash);
    update_u8(&mut hasher, u8::from(run.manifest.requires_lexical_index));
    update_u8(&mut hasher, u8::from(run.manifest.requires_exact_index));
    update_u8(&mut hasher, u8::from(run.manifest.requires_bitmap_filter));
    hasher.update(&run.manifest.lexical_index_hash);
    hasher.update(&run.manifest.exact_index_hash);
    hasher.update(&run.manifest.bitmap_filter_hash);
    hasher.update(&run.execution_record.record_hash);
    hasher.update(&run.execution_record.program_hash);
    hasher.update(&run.execution_record.index_epoch_hash);
    update_u64(&mut hasher, run.execution_record.step_count as u64);
    update_u64(&mut hasher, run.execution_record.fuel_used as u64);
    update_u64(&mut hasher, run.execution_record.candidate_count as u64);
    hasher.update(&run.execution_record.candidate_list_hash);
    hasher.update(&run.execution_record.trace_hash);
    update_u128(&mut hasher, run.capsule.capsule_id);
    hasher.update(&run.capsule.index_epoch_hash);
    update_u64(&mut hasher, run.capsule.candidate_count as u64);
    hasher.update(&run.capsule.candidate_list_hash);
    hasher.update(&run.capsule.cold_vector_replay_record_hash);
    hasher.update(&run.capsule.agentic_evidence_execution_record_hash);
    update_u64(&mut hasher, run.capsule.payload_byte_len as u64);
    hasher.update(&run.capsule.payload_hash);
    hasher.update(&run.capsule.capsule_hash);
    *hasher.finalize().as_bytes()
}

pub fn llm_budget_admission_replay_binding_hash(
    response_hash: [u8; 32],
    raw_text_ref_hash: [u8; 32],
    admission_proof: &ProviderRouteAdmissionProof,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-budget-admission-replay-binding-v1");
    hasher.update(&response_hash);
    hasher.update(&raw_text_ref_hash);
    hasher.update(&admission_proof.request_hash);
    update_u64(&mut hasher, admission_proof.required_tokens as u64);
    hasher.update(&admission_proof.selected_provider_hash);
    update_u64(
        &mut hasher,
        admission_proof.throttled_provider_hashes.len() as u64,
    );
    for throttled_hash in &admission_proof.throttled_provider_hashes {
        hasher.update(throttled_hash);
    }
    hasher.update(&admission_proof.admitted_provider_ordering_hash);
    hasher.update(&admission_proof.budget_ledger_hash);
    hasher.update(&admission_proof.route_policy_hash);
    hasher.update(&admission_proof.decision_hash);
    hasher.update(&admission_proof.proof_hash);
    *hasher.finalize().as_bytes()
}

pub fn shadow_seal_replay_binding_hash(
    seal_id: SubjectId,
    batch: &ShadowSealBatchReceipt,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-shadow-seal-replay-binding-v1");
    update_u128(&mut hasher, seal_id);
    update_u64(&mut hasher, batch.receipt_count as u64);
    update_u64(&mut hasher, batch.total_bytes);
    hasher.update(&batch.batch_hash);
    for receipt in &batch.receipts {
        update_u32(&mut hasher, receipt.slot);
        update_u32(&mut hasher, receipt.generation);
        update_u64(&mut hasher, receipt.byte_len);
        hasher.update(&receipt.artifact_hash);
        hasher.update(&receipt.seal_hash);
    }
    *hasher.finalize().as_bytes()
}

pub fn goal_intake_replay_binding_hash(proof: &GoalIntakeProof) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-goal-intake-replay-binding-v1");
    hasher.update(&proof.packet.packet_hash);
    hasher.update(&proof.packet.raw_goal_ref_hash);
    hasher.update(&proof.packet.normalized_goal_hash);
    hasher.update(&proof.packet.policy_window_hash);
    hasher.update(&proof.root_task_hash);
    hasher.update(&proof.initial_ir.canonical_hash);
    hasher.update(&proof.proof_hash);
    *hasher.finalize().as_bytes()
}

pub fn tool_execution_evidence_hash(
    typed_tool_ir_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    tool_output_hash: [u8; 32],
    physical_evidence_hash: [u8; 32],
    executor_kind: ToolExecutorKind,
    status: ToolExecutionStatus,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-execution-evidence-v1");
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&policy_proof_trace_hash);
    hasher.update(&tool_output_hash);
    hasher.update(&physical_evidence_hash);
    update_u8(&mut hasher, executor_kind as u8);
    update_u8(&mut hasher, status as u8);
    *hasher.finalize().as_bytes()
}

pub fn tool_call_completion_binding_hash(
    call_id: SubjectId,
    typed_tool_ir_hash: [u8; 32],
    policy_proof_trace_hash: [u8; 32],
    tool_execution_evidence_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-tool-call-completion-binding-v1");
    update_u128(&mut hasher, call_id);
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&policy_proof_trace_hash);
    hasher.update(&tool_execution_evidence_hash);
    *hasher.finalize().as_bytes()
}

pub fn memory_commit_binding_hash(
    session_id: SubjectId,
    tool_execution_evidence_hash: [u8; 32],
    memory_commit_proof_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-memory-commit-binding-v1");
    update_u128(&mut hasher, session_id);
    hasher.update(&tool_execution_evidence_hash);
    hasher.update(&memory_commit_proof_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn memory_commit_handoff_proof_hash(
    run_id: RunId,
    session_id: SubjectId,
    memory_commit_event_id: EventId,
    memory_commit_event_hash: [u8; 32],
    tool_execution_evidence_hash: [u8; 32],
    memory_commit_proof_hash: [u8; 32],
    replay_determinism_proof_hash: [u8; 32],
    checkpoint_hash: [u8; 32],
    next_action_packet_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-memory-commit-handoff-proof-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, session_id);
    update_u64(&mut hasher, memory_commit_event_id);
    hasher.update(&memory_commit_event_hash);
    hasher.update(&tool_execution_evidence_hash);
    hasher.update(&memory_commit_proof_hash);
    hasher.update(&replay_determinism_proof_hash);
    hasher.update(&checkpoint_hash);
    hasher.update(&next_action_packet_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn agentic_evidence_sdk_run_handoff_proof_hash(
    run_id: RunId,
    execution_id: SubjectId,
    sdk_run_event_id: EventId,
    sdk_run_event_hash: [u8; 32],
    manifest_hash: [u8; 32],
    execution_record_hash: [u8; 32],
    candidate_list_hash: [u8; 32],
    candidate_count: u32,
    capsule_hash: [u8; 32],
    sdk_run_hash: [u8; 32],
    sdk_run_replay_binding_hash: [u8; 32],
    replay_determinism_proof_hash: [u8; 32],
    checkpoint_hash: [u8; 32],
    next_action_packet_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-sdk-run-handoff-proof-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, execution_id);
    update_u64(&mut hasher, sdk_run_event_id);
    hasher.update(&sdk_run_event_hash);
    hasher.update(&manifest_hash);
    hasher.update(&execution_record_hash);
    hasher.update(&candidate_list_hash);
    update_u64(&mut hasher, candidate_count as u64);
    hasher.update(&capsule_hash);
    hasher.update(&sdk_run_hash);
    hasher.update(&sdk_run_replay_binding_hash);
    hasher.update(&replay_determinism_proof_hash);
    hasher.update(&checkpoint_hash);
    hasher.update(&next_action_packet_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn skill_admission_handoff_proof_hash(
    run_id: RunId,
    skill_id: SubjectId,
    skill_admission_event_id: EventId,
    skill_admission_event_hash: [u8; 32],
    package_hash: [u8; 32],
    admission_hash: [u8; 32],
    registry_epoch: u64,
    registry_commit_hash: [u8; 32],
    replay_determinism_proof_hash: [u8; 32],
    checkpoint_hash: [u8; 32],
    next_action_packet_hash: [u8; 32],
    activation_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-skill-admission-handoff-proof-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, skill_id);
    update_u64(&mut hasher, skill_admission_event_id);
    hasher.update(&skill_admission_event_hash);
    hasher.update(&package_hash);
    hasher.update(&admission_hash);
    update_u64(&mut hasher, registry_epoch);
    hasher.update(&registry_commit_hash);
    hasher.update(&replay_determinism_proof_hash);
    hasher.update(&checkpoint_hash);
    hasher.update(&next_action_packet_hash);
    hasher.update(&activation_hash);
    *hasher.finalize().as_bytes()
}

pub fn skill_activation_hash(
    run_id: RunId,
    skill_id: SubjectId,
    skill_admission_event_hash: [u8; 32],
    package_hash: [u8; 32],
    admission_hash: [u8; 32],
    registry_epoch: u64,
    registry_commit_hash: [u8; 32],
    next_action_packet_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-skill-activation-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, skill_id);
    hasher.update(&skill_admission_event_hash);
    hasher.update(&package_hash);
    hasher.update(&admission_hash);
    update_u64(&mut hasher, registry_epoch);
    hasher.update(&registry_commit_hash);
    hasher.update(&next_action_packet_hash);
    *hasher.finalize().as_bytes()
}

pub fn browser_ops_bench_verification_replay_binding_hash(
    proof: &BrowserOpsBenchVerificationProof,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-ops-bench-verification-replay-binding-v1");
    update_u128(&mut hasher, proof.replay_subject_id());
    hasher.update(&proof.proof_hash);
    hasher.update(&proof.suite_hash);
    hasher.update(&proof.scorecard_file_hash);
    hasher.update(&proof.verified_records_hash);
    update_u64(&mut hasher, proof.verified_task_count);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn browser_ops_bench_verification_handoff_proof_hash(
    run_id: RunId,
    verification_subject_id: SubjectId,
    verification_event_id: EventId,
    verification_event_hash: [u8; 32],
    proof_hash: [u8; 32],
    suite_hash: [u8; 32],
    scorecard_file_hash: [u8; 32],
    verified_records_hash: [u8; 32],
    verified_task_count: u64,
    replay_determinism_proof_hash: [u8; 32],
    checkpoint_hash: [u8; 32],
    next_action_packet_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-ops-bench-verification-handoff-proof-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, verification_subject_id);
    update_u64(&mut hasher, verification_event_id);
    hasher.update(&verification_event_hash);
    hasher.update(&proof_hash);
    hasher.update(&suite_hash);
    hasher.update(&scorecard_file_hash);
    hasher.update(&verified_records_hash);
    update_u64(&mut hasher, verified_task_count);
    hasher.update(&replay_determinism_proof_hash);
    hasher.update(&checkpoint_hash);
    hasher.update(&next_action_packet_hash);
    *hasher.finalize().as_bytes()
}

fn compute_run_event_hash(
    event_id: EventId,
    run_id: RunId,
    kind: RunEventKind,
    subject_id: SubjectId,
    primary_hash: [u8; 32],
    secondary_hash: Option<[u8; 32]>,
    previous_event_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    update_u64(&mut hasher, event_id);
    update_u128(&mut hasher, run_id);
    update_u8(&mut hasher, kind as u8);
    update_u128(&mut hasher, subject_id);
    hasher.update(&primary_hash);
    update_optional_hash(&mut hasher, secondary_hash);
    hasher.update(&previous_event_hash);
    *hasher.finalize().as_bytes()
}

pub fn skill_admission_replay_binding_hash(
    admission: &SkillAdmissionRecord,
    admitted: &AdmittedSkillRecord,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-skill-admission-replay-binding-v1");
    update_u128(&mut hasher, admitted.skill_id);
    hasher.update(&admission.package_hash);
    hasher.update(&admission.wasm_module_hash);
    hasher.update(&admission.wasmtime_artifact_hash);
    update_u64(&mut hasher, admission.wasmtime_ast_fingerprint);
    update_u64(&mut hasher, admission.fuel_consumed);
    hasher.update(&admission.regression_report_hash);
    hasher.update(&admission.pav_acceptance_hash);
    hasher.update(&admission.admission_hash);
    hasher.update(&admitted.package_hash);
    hasher.update(&admitted.admission_hash);
    update_u64(&mut hasher, admitted.registry_epoch);
    hasher.update(&admitted.registry_commit_hash);
    *hasher.finalize().as_bytes()
}

fn run_event_segment_hash(
    segment_id: u64,
    events: &[RunEvent],
    previous_segment_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = run_event_segment_hasher(segment_id, previous_segment_hash, events.len());
    for event in events {
        hasher.update(&event.event_hash);
    }
    *hasher.finalize().as_bytes()
}

fn run_event_segment_hasher(
    segment_id: u64,
    previous_segment_hash: [u8; 32],
    event_count: usize,
) -> Hasher {
    let mut hasher = Hasher::new();
    update_u64(&mut hasher, segment_id);
    hasher.update(&previous_segment_hash);
    update_u64(&mut hasher, event_count as u64);
    hasher
}

fn run_event_manifest_hash(run_id: RunId, entries: &[RunEventSegmentEntry]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(RUN_EVENT_SEGMENT_MANIFEST_SCHEMA.as_bytes());
    update_u32(&mut hasher, RUN_EVENT_SEGMENT_MANIFEST_VERSION);
    update_run_event_manifest_hash_fields(&mut hasher, run_id, entries);
    *hasher.finalize().as_bytes()
}

fn run_event_manifest_hash_legacy(run_id: RunId, entries: &[RunEventSegmentEntry]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    update_run_event_manifest_hash_fields(&mut hasher, run_id, entries);
    *hasher.finalize().as_bytes()
}

fn update_run_event_manifest_hash_fields(
    hasher: &mut Hasher,
    run_id: RunId,
    entries: &[RunEventSegmentEntry],
) {
    update_u128(hasher, run_id);
    update_u64(hasher, entries.len() as u64);
    for entry in entries {
        update_u64(hasher, entry.segment_id);
        update_u64(hasher, entry.start_event_id);
        update_u64(hasher, entry.end_event_id);
        update_u64(hasher, entry.event_count);
        hasher.update(&entry.first_event_hash);
        hasher.update(&entry.last_event_hash);
        hasher.update(&entry.segment_hash);
        hasher.update(&entry.previous_segment_hash);
        hasher.update(&entry.arrow_schema_hash);
        update_u64(hasher, entry.arrow_file_bytes);
        hasher.update(&entry.arrow_file_hash);
        hasher.update(&entry.segment_commit_hash);
        update_u8(hasher, u8::from(entry.staged_temp_file_used));
        update_u8(hasher, u8::from(entry.temp_file_synced_before_publish));
        update_u8(hasher, u8::from(entry.publish_completed));
        update_u8(hasher, u8::from(entry.parent_directory_sync_attempted));
    }
}

fn segmented_arrow_segment_witness_hash(
    run_id: RunId,
    manifest_hash: [u8; 32],
    entry: &RunEventSegmentEntry,
    scan: &ArrowRunEventMmapColumnScan,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-segmented-arrow-segment-witness-v1");
    update_u128(&mut hasher, run_id);
    hasher.update(&manifest_hash);
    hasher.update(&ArrowRunEventStream::schema_hash());
    update_u64(&mut hasher, entry.segment_id);
    update_u64(&mut hasher, entry.start_event_id);
    update_u64(&mut hasher, entry.end_event_id);
    update_u64(&mut hasher, entry.event_count);
    hasher.update(&entry.first_event_hash);
    hasher.update(&entry.last_event_hash);
    hasher.update(&entry.segment_hash);
    hasher.update(&entry.previous_segment_hash);
    update_u64(&mut hasher, entry.arrow_file_bytes);
    hasher.update(&entry.arrow_file_hash);
    hasher.update(&entry.segment_commit_hash);
    update_u8(&mut hasher, u8::from(entry.staged_temp_file_used));
    update_u8(&mut hasher, u8::from(entry.temp_file_synced_before_publish));
    update_u8(&mut hasher, u8::from(entry.publish_completed));
    update_u8(&mut hasher, u8::from(entry.parent_directory_sync_attempted));
    update_u64(&mut hasher, scan.file_bytes);
    hasher.update(&scan.file_hash);
    update_u64(&mut hasher, scan.event_count as u64);
    update_u64(&mut hasher, scan.first_event_id);
    update_u64(&mut hasher, scan.last_event_id);
    hasher.update(&scan.first_event_hash);
    hasher.update(&scan.last_event_hash);
    hasher.update(&scan.segment_hash);
    update_u64(&mut hasher, scan.mmap_buffer_count as u64);
    update_u8(&mut hasher, 1); // StreamDecoder alignment was required.
    update_u8(&mut hasher, 1); // All Arrow buffers were range-checked against mmap.
    *hasher.finalize().as_bytes()
}

fn segmented_arrow_audit_proof_hash(proof: &SegmentedArrowAuditProof) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-segmented-arrow-audit-proof-v2");
    update_u128(&mut hasher, proof.run_id);
    update_u64(&mut hasher, proof.segment_count as u64);
    update_u64(&mut hasher, proof.event_count as u64);
    update_u64(&mut hasher, proof.total_arrow_file_bytes);
    update_u64(&mut hasher, proof.first_event_id);
    update_u64(&mut hasher, proof.last_event_id);
    hasher.update(&proof.manifest_hash);
    hasher.update(&proof.arrow_schema_hash);
    update_u64(&mut hasher, proof.mmap_buffer_count as u64);
    hasher.update(&proof.segment_chain_hash);
    hasher.update(&proof.segment_witness_hash);
    hasher.update(&proof.file_evidence_hash);
    hasher.update(&proof.logical_replay_hash);
    hasher.update(&proof.mmap_evidence_hash);
    hasher.update(&proof.semantic_scan_hash);
    *hasher.finalize().as_bytes()
}

fn segmented_arrow_semantic_scan_hash(proof: &SegmentedArrowAuditProof) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-segmented-arrow-semantic-scan-v2");
    update_u128(&mut hasher, proof.run_id);
    update_u64(&mut hasher, proof.segment_count as u64);
    update_u64(&mut hasher, proof.event_count as u64);
    update_u64(&mut hasher, proof.total_arrow_file_bytes);
    update_u64(&mut hasher, proof.mmap_buffer_count as u64);
    update_u64(&mut hasher, proof.first_event_id);
    update_u64(&mut hasher, proof.last_event_id);
    hasher.update(&proof.manifest_hash);
    hasher.update(&proof.arrow_schema_hash);
    hasher.update(&proof.segment_chain_hash);
    hasher.update(&proof.segment_witness_hash);
    hasher.update(&proof.file_evidence_hash);
    hasher.update(&proof.logical_replay_hash);
    hasher.update(&proof.mmap_evidence_hash);
    *hasher.finalize().as_bytes()
}

fn run_event_logical_replay_hash(events: &[RunEvent]) -> [u8; 32] {
    let mut hasher = run_event_logical_replay_hasher(events.len());
    for event in events {
        update_run_event_logical_replay_hasher(
            &mut hasher,
            event.event_id,
            event.run_id,
            event.kind,
            event.subject_id,
            event.primary_hash,
            event.secondary_hash,
            event.previous_event_hash,
            event.event_hash,
        );
    }
    *hasher.finalize().as_bytes()
}

fn run_event_logical_replay_hasher(event_count: usize) -> Hasher {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-run-event-logical-replay-v1");
    update_u64(&mut hasher, event_count as u64);
    hasher
}

#[allow(clippy::too_many_arguments)]
fn update_run_event_logical_replay_hasher(
    hasher: &mut Hasher,
    event_id: EventId,
    run_id: RunId,
    kind: RunEventKind,
    subject_id: SubjectId,
    primary_hash: [u8; 32],
    secondary_hash: Option<[u8; 32]>,
    previous_event_hash: [u8; 32],
    event_hash: [u8; 32],
) {
    update_u64(hasher, event_id);
    update_u128(hasher, run_id);
    update_u8(hasher, kind as u8);
    update_u128(hasher, subject_id);
    hasher.update(&primary_hash);
    match secondary_hash {
        Some(secondary_hash) => {
            update_u8(hasher, 1);
            hasher.update(&secondary_hash);
        }
        None => update_u8(hasher, 0),
    }
    hasher.update(&previous_event_hash);
    hasher.update(&event_hash);
}

fn replay_determinism_proof_hash(proof: &ReplayDeterminismProof) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-replay-determinism-proof-v1");
    update_u128(&mut hasher, proof.run_id);
    update_u64(&mut hasher, proof.segment_count as u64);
    update_u64(&mut hasher, proof.event_count as u64);
    hasher.update(&proof.manifest_hash);
    hasher.update(&proof.arrow_schema_hash);
    hasher.update(&proof.first_pass_event_sequence_hash);
    hasher.update(&proof.second_pass_event_sequence_hash);
    hasher.update(&proof.first_pass_ledger_hash);
    hasher.update(&proof.second_pass_ledger_hash);
    hasher.update(&proof.first_pass_mmap_evidence_hash);
    hasher.update(&proof.second_pass_mmap_evidence_hash);
    hasher.update(&proof.commit_sidecar_evidence_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn run_checkpoint_hash(
    run_id: RunId,
    checkpoint_id: SubjectId,
    event_count: usize,
    segment_count: usize,
    ledger_last_hash: [u8; 32],
    manifest_hash: [u8; 32],
    replay_determinism_proof_hash: [u8; 32],
    context_fold_evidence_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-run-checkpoint-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, checkpoint_id);
    update_u64(&mut hasher, event_count as u64);
    update_u64(&mut hasher, segment_count as u64);
    hasher.update(&ledger_last_hash);
    hasher.update(&manifest_hash);
    hasher.update(&replay_determinism_proof_hash);
    hasher.update(&context_fold_evidence_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn next_action_packet_hash(
    run_id: RunId,
    packet_id: SubjectId,
    checkpoint_hash: [u8; 32],
    action_kind: NextActionKind,
    task_id: SubjectId,
    typed_tool_ir_hash: [u8; 32],
    evidence_contract_hash: [u8; 32],
    policy_proof_hash: [u8; 32],
    candidate_evidence_hash: [u8; 32],
    task_selection_proof_hash: Option<[u8; 32]>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-next-action-packet-v2");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, packet_id);
    hasher.update(&checkpoint_hash);
    update_u8(&mut hasher, action_kind as u8);
    update_u128(&mut hasher, task_id);
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&evidence_contract_hash);
    hasher.update(&policy_proof_hash);
    hasher.update(&candidate_evidence_hash);
    update_optional_hash(&mut hasher, task_selection_proof_hash);
    *hasher.finalize().as_bytes()
}

fn run_event_compaction_report_hash(report: &RunEventCompactionReport) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-run-event-compaction-report-v1");
    update_u128(&mut hasher, report.run_id);
    update_u64(&mut hasher, report.source_segment_count as u64);
    update_u64(&mut hasher, report.source_event_count as u64);
    hasher.update(&report.source_manifest_hash);
    hasher.update(&report.source_audit_proof_hash);
    hasher.update(&report.source_mmap_evidence_hash);
    update_u64(&mut hasher, report.compacted_event_count as u64);
    update_u64(&mut hasher, report.compacted_file_bytes);
    hasher.update(&report.compacted_payload_hash);
    hasher.update(&report.compacted_last_event_hash);
    update_u8(&mut hasher, u8::from(report.staged_temp_file_used));
    update_u8(
        &mut hasher,
        u8::from(report.temp_file_synced_before_publish),
    );
    update_u8(&mut hasher, u8::from(report.publish_completed));
    update_u8(
        &mut hasher,
        u8::from(report.parent_directory_sync_attempted),
    );
    update_u8(&mut hasher, u8::from(report.mmap_scan_verified));
    *hasher.finalize().as_bytes()
}

fn run_event_recovery_hash(
    manifest_hash: [u8; 32],
    recovered_segment_count: usize,
    last_valid_event_id: EventId,
    last_event_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(&manifest_hash);
    update_u64(&mut hasher, recovered_segment_count as u64);
    update_u64(&mut hasher, last_valid_event_id);
    hasher.update(&last_event_hash);
    *hasher.finalize().as_bytes()
}

fn replay_chaos_persisted_segment_count(
    rng_state: &mut u64,
    iteration: u32,
    segment_count: usize,
) -> usize {
    if iteration == 0 {
        0
    } else if iteration == 1 {
        segment_count
    } else {
        let next = next_replay_chaos_state(rng_state);
        (next as usize) % (segment_count + 1)
    }
}

fn next_replay_chaos_state(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *state ^ (*state >> 33)
}

struct ReplayChaosReportHashInputs<'a> {
    seed: u64,
    expected_ledger_hash: [u8; 32],
    iterations: u32,
    max_events_per_segment: usize,
    segmented_arrow_audit_proof_hash: [u8; 32],
    segmented_arrow_audit_logical_replay_hash: [u8; 32],
    segmented_arrow_audit_mmap_evidence_hash: [u8; 32],
    segmented_arrow_audit_segment_witness_hash: [u8; 32],
    segmented_arrow_audit_mmap_buffer_count: usize,
    column_scan_full_acceptance_count: u32,
    column_scan_missing_tail_rejection_count: u32,
    all_recoveries_valid: bool,
    crash_points: &'a [ReplayChaosCrashPoint],
}

fn replay_chaos_report_hash(inputs: &ReplayChaosReportHashInputs<'_>) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-replay-chaos-column-scan-proof-v3");
    update_u64(&mut hasher, inputs.seed);
    hasher.update(&inputs.expected_ledger_hash);
    update_u64(&mut hasher, inputs.iterations as u64);
    update_u64(&mut hasher, inputs.max_events_per_segment as u64);
    hasher.update(&inputs.segmented_arrow_audit_proof_hash);
    hasher.update(&inputs.segmented_arrow_audit_logical_replay_hash);
    hasher.update(&inputs.segmented_arrow_audit_mmap_evidence_hash);
    hasher.update(&inputs.segmented_arrow_audit_segment_witness_hash);
    update_u64(
        &mut hasher,
        inputs.segmented_arrow_audit_mmap_buffer_count as u64,
    );
    update_u64(&mut hasher, inputs.column_scan_full_acceptance_count as u64);
    update_u64(
        &mut hasher,
        inputs.column_scan_missing_tail_rejection_count as u64,
    );
    update_u8(&mut hasher, u8::from(inputs.all_recoveries_valid));
    update_u64(&mut hasher, inputs.crash_points.len() as u64);
    for point in inputs.crash_points {
        update_u64(&mut hasher, point.iteration as u64);
        update_u64(&mut hasher, point.persisted_segment_count as u64);
        update_u64(&mut hasher, point.recovered_event_count as u64);
        update_u64(&mut hasher, point.last_valid_event_id);
        hasher.update(&point.recovery_hash);
    }
    *hasher.finalize().as_bytes()
}

fn replay_endurance_report_hash(report: &ReplayEnduranceBenchReport) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-replay-endurance-proof-v2");
    update_u64(&mut hasher, report.simulated_hours as u64);
    update_u64(&mut hasher, report.cycles_per_hour as u64);
    update_u64(&mut hasher, report.synthetic_cycle_count as u64);
    update_u64(&mut hasher, report.checkpoint_cadence_cycles as u64);
    update_u64(&mut hasher, report.checkpoint_count as u64);
    update_u64(&mut hasher, report.context_fold_count as u64);
    update_u64(&mut hasher, report.tail_recovered_context_fold_count as u64);
    update_u64(
        &mut hasher,
        report.context_fold_checkpoint_pair_count as u64,
    );
    update_u64(&mut hasher, report.event_count as u64);
    update_u64(&mut hasher, report.segment_count as u64);
    update_u64(
        &mut hasher,
        report.segmented_arrow_audit_segment_count as u64,
    );
    update_u64(&mut hasher, report.segmented_arrow_audit_event_count as u64);
    update_u64(&mut hasher, report.segmented_arrow_audit_total_file_bytes);
    update_u64(
        &mut hasher,
        report.segmented_arrow_audit_mmap_buffer_count as u64,
    );
    update_u64(&mut hasher, report.mmap_recovered_event_count as u64);
    update_u64(&mut hasher, report.tail_drop_segment_count as u64);
    update_u64(&mut hasher, report.tail_recovered_segment_count as u64);
    update_u64(&mut hasher, report.tail_recovered_event_count as u64);
    update_u64(&mut hasher, report.tail_recovered_checkpoint_count as u64);
    update_u64(&mut hasher, report.tail_events_since_last_checkpoint as u64);
    update_u64(&mut hasher, report.checkpoint_cadence_event_bound as u64);
    update_u64(&mut hasher, report.materialized_event_count as u64);
    update_u64(&mut hasher, report.materialized_replay_bytes);
    update_u64(&mut hasher, report.bounded_materialized_bytes);
    update_u8(&mut hasher, u8::from(report.all_hash_chains_valid));
    update_u8(&mut hasher, u8::from(report.full_replay_matches));
    update_u8(&mut hasher, u8::from(report.tail_recovery_matches_prefix));
    update_u8(
        &mut hasher,
        u8::from(report.mmap_materialized_replay_proven),
    );
    update_u8(&mut hasher, u8::from(report.materialization_within_bound));
    hasher.update(&report.manifest_hash);
    hasher.update(&report.expected_ledger_hash);
    hasher.update(&report.mmap_recovered_ledger_hash);
    hasher.update(&report.tail_recovered_ledger_hash);
    hasher.update(&report.context_fold_evidence_hash);
    hasher.update(&report.segmented_arrow_audit_proof_hash);
    hasher.update(&report.segmented_arrow_audit_segment_witness_hash);
    hasher.update(&report.replay_determinism_proof_hash);
    hasher.update(&report.run_checkpoint_hash);
    hasher.update(&report.next_action_packet_hash);
    hasher.update(&report.mmap_evidence_hash);
    hasher.update(&report.tail_recovery_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn replay_io_evidence_hash(
    segment_count: usize,
    mmap_segment_count: usize,
    total_file_bytes: u64,
    materialized_event_count: usize,
    materialized_run_event_bytes: u64,
    materialized_hash_bytes: u64,
    mmap_backing_used: bool,
    stream_reader_materializes_events: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    update_u64(&mut hasher, segment_count as u64);
    update_u64(&mut hasher, mmap_segment_count as u64);
    update_u64(&mut hasher, total_file_bytes);
    update_u64(&mut hasher, materialized_event_count as u64);
    update_u64(&mut hasher, materialized_run_event_bytes);
    update_u64(&mut hasher, materialized_hash_bytes);
    update_u8(&mut hasher, u8::from(mmap_backing_used));
    update_u8(&mut hasher, u8::from(stream_reader_materializes_events));
    *hasher.finalize().as_bytes()
}

fn arrow_mmap_column_scan_evidence_hash(
    run_id: RunId,
    manifest_hash: [u8; 32],
    segment_count: usize,
    total_file_bytes: u64,
    event_count: usize,
    mmap_buffer_count: usize,
    logical_replay_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-arrow-mmap-column-scan-evidence-v1");
    update_u128(&mut hasher, run_id);
    hasher.update(&manifest_hash);
    hasher.update(&ArrowRunEventStream::schema_hash());
    update_u64(&mut hasher, segment_count as u64);
    update_u64(&mut hasher, total_file_bytes);
    update_u64(&mut hasher, event_count as u64);
    update_u64(&mut hasher, mmap_buffer_count as u64);
    update_u8(&mut hasher, 1); // mmap owner backs the root Arrow buffer.
    update_u8(&mut hasher, 1); // StreamDecoder requires aligned zero-copy buffers.
    update_u8(&mut hasher, 1); // Every batch data/null buffer was range-checked.
    update_u8(&mut hasher, 0); // No Vec<RunEvent> materialization in this scan.
    hasher.update(&logical_replay_hash);
    *hasher.finalize().as_bytes()
}

#[cfg(test)]
mod replay_internal_tests {
    use super::*;
    use arrow::ipc::{
        BodyCompression, BodyCompressionArgs, BodyCompressionMethod, CompressionType,
        DictionaryBatch as IpcDictionaryBatch, DictionaryBatchArgs, Endianness, FieldNode, Message,
        MessageArgs, MetadataVersion, RecordBatch as IpcRecordBatch, RecordBatchArgs,
        Schema as IpcSchema, SchemaArgs,
    };
    use flatbuffers::FlatBufferBuilder;

    fn append_ipc_stream_frame(payload: &mut Vec<u8>, metadata: &[u8]) {
        payload.extend_from_slice(&[0xff; 4]);
        payload.extend_from_slice(&(metadata.len() as u32).to_le_bytes());
        payload.extend_from_slice(metadata);
    }

    fn ipc_schema_message() -> Vec<u8> {
        let mut builder = FlatBufferBuilder::new();
        let schema = IpcSchema::create(
            &mut builder,
            &SchemaArgs {
                endianness: Endianness::Little,
                ..Default::default()
            },
        );
        let message = Message::create(
            &mut builder,
            &MessageArgs {
                version: MetadataVersion::V5,
                header_type: MessageHeader::Schema,
                header: Some(schema.as_union_value()),
                bodyLength: 0,
                custom_metadata: None,
            },
        );
        builder.finish(message, None);
        builder.finished_data().to_vec()
    }

    fn ipc_compressed_record_batch_message() -> Vec<u8> {
        let mut builder = FlatBufferBuilder::new();
        let nodes = builder.create_vector(&[FieldNode::new(1, 0)]);
        let buffers = builder.create_vector(&[arrow::ipc::Buffer::new(0, 0)]);
        let compression = BodyCompression::create(
            &mut builder,
            &BodyCompressionArgs {
                codec: CompressionType::LZ4_FRAME,
                method: BodyCompressionMethod::BUFFER,
            },
        );
        let batch = IpcRecordBatch::create(
            &mut builder,
            &RecordBatchArgs {
                length: 1,
                nodes: Some(nodes),
                buffers: Some(buffers),
                compression: Some(compression),
                variadicBufferCounts: None,
            },
        );
        let message = Message::create(
            &mut builder,
            &MessageArgs {
                version: MetadataVersion::V5,
                header_type: MessageHeader::RecordBatch,
                header: Some(batch.as_union_value()),
                bodyLength: 0,
                custom_metadata: None,
            },
        );
        builder.finish(message, None);
        builder.finished_data().to_vec()
    }

    fn ipc_dictionary_batch_message() -> Vec<u8> {
        let mut builder = FlatBufferBuilder::new();
        let nodes = builder.create_vector(&[FieldNode::new(1, 0)]);
        let buffers = builder.create_vector(&[arrow::ipc::Buffer::new(0, 0)]);
        let data = IpcRecordBatch::create(
            &mut builder,
            &RecordBatchArgs {
                length: 1,
                nodes: Some(nodes),
                buffers: Some(buffers),
                compression: None,
                variadicBufferCounts: None,
            },
        );
        let dictionary = IpcDictionaryBatch::create(
            &mut builder,
            &DictionaryBatchArgs {
                id: 7,
                data: Some(data),
                isDelta: false,
            },
        );
        let message = Message::create(
            &mut builder,
            &MessageArgs {
                version: MetadataVersion::V5,
                header_type: MessageHeader::DictionaryBatch,
                header: Some(dictionary.as_union_value()),
                bodyLength: 0,
                custom_metadata: None,
            },
        );
        builder.finish(message, None);
        builder.finished_data().to_vec()
    }

    fn compressed_ipc_payload() -> Vec<u8> {
        let mut payload = Vec::new();
        append_ipc_stream_frame(&mut payload, &ipc_schema_message());
        append_ipc_stream_frame(&mut payload, &ipc_compressed_record_batch_message());
        payload.extend_from_slice(&[0xff; 4]);
        payload.extend_from_slice(&0_u32.to_le_bytes());
        payload
    }

    fn dictionary_ipc_payload() -> Vec<u8> {
        let mut payload = Vec::new();
        append_ipc_stream_frame(&mut payload, &ipc_schema_message());
        append_ipc_stream_frame(&mut payload, &ipc_dictionary_batch_message());
        payload.extend_from_slice(&[0xff; 4]);
        payload.extend_from_slice(&0_u32.to_le_bytes());
        payload
    }

    #[test]
    fn arrow_ipc_zero_copy_metadata_rejects_compressed_record_batch() {
        let payload = compressed_ipc_payload();
        assert_eq!(
            validate_arrow_ipc_stream_zero_copy_metadata(&payload),
            Err("compressed arrow ipc cannot prove mmap zero-copy")
        );
    }

    #[test]
    fn arrow_ipc_zero_copy_metadata_rejects_dictionary_batch() {
        let payload = dictionary_ipc_payload();
        assert_eq!(
            validate_arrow_ipc_stream_zero_copy_metadata(&payload),
            Err("dictionary arrow ipc batch cannot prove mmap zero-copy")
        );
    }

    #[test]
    fn mmap_materialized_reader_rejects_compressed_ipc_before_decode() {
        let path = std::env::temp_dir().join(format!(
            "aegis-compressed-ipc-{}-{}.arrow",
            std::process::id(),
            blake3_hash_bytes(&compressed_ipc_payload())[0]
        ));
        std::fs::write(&path, compressed_ipc_payload()).unwrap();
        let mut events = Vec::new();
        let result = ArrowRunEventStream::append_events_mmap_with_capacity_evidence_and_file_hash(
            &path,
            1,
            &mut events,
            true,
        );
        let _ = std::fs::remove_file(&path);
        assert_eq!(
            result.err(),
            Some("compressed arrow ipc cannot prove mmap zero-copy")
        );
        assert!(events.is_empty());
    }

    #[test]
    fn mmap_materialized_reader_rejects_dictionary_ipc_before_decode() {
        let path = std::env::temp_dir().join(format!(
            "aegis-dictionary-ipc-{}-{}.arrow",
            std::process::id(),
            blake3_hash_bytes(&dictionary_ipc_payload())[0]
        ));
        std::fs::write(&path, dictionary_ipc_payload()).unwrap();
        let mut events = Vec::new();
        let result = ArrowRunEventStream::append_events_mmap_with_capacity_evidence_and_file_hash(
            &path,
            1,
            &mut events,
            true,
        );
        let _ = std::fs::remove_file(&path);
        assert_eq!(
            result.err(),
            Some("dictionary arrow ipc batch cannot prove mmap zero-copy")
        );
        assert!(events.is_empty());
    }

    #[test]
    fn binary_archive_recovery_preserves_committed_prefix_and_rejects_invalid_header() {
        let path = std::env::temp_dir().join(format!(
            "aegis-binary-recovery-{}-{}.bin",
            std::process::id(),
            1_u64
        ));
        let event = RunEvent::new(
            1,
            1,
            RunEventKind::MissionCompiled,
            1,
            [1; 32],
            None,
            [0; 32],
        );
        let ledger = RunEventLedger::from_events(1, vec![event]).unwrap();
        BinaryRunEventSegment::write_ledger(&path, &ledger).unwrap();
        let mut bytes = std::fs::read(&path).unwrap();

        bytes.extend(std::iter::repeat_n(
            0xaa,
            BinaryRunEventSegment::record_bytes() / 2,
        ));
        std::fs::write(&path, &bytes).unwrap();
        let recovered = BinaryRunEventSegment::recover_last_valid_prefix_mmap(&path).unwrap();
        assert_eq!(recovered.recovered_event_count, 1);
        assert!(recovered.trailing_partial_bytes > 0);

        bytes[BinaryRunEventSegment::header_bytes() + 5] ^= 0xff;
        std::fs::write(&path, &bytes).unwrap();
        let corrupted = BinaryRunEventSegment::recover_last_valid_prefix_mmap(&path).unwrap();
        assert_eq!(corrupted.recovered_event_count, 0);

        BinaryRunEventSegment::write_ledger(&path, &ledger).unwrap();
        let mut future_version = std::fs::read(&path).unwrap();
        future_version[8..16].copy_from_slice(&99_u64.to_le_bytes());
        std::fs::write(&path, &future_version).unwrap();
        assert!(BinaryRunEventSegment::recover_last_valid_prefix_mmap(&path).is_err());

        bytes.truncate(BinaryRunEventSegment::header_bytes());
        std::fs::write(&path, &bytes).unwrap();
        let header_only = BinaryRunEventSegment::recover_last_valid_prefix_mmap(&path).unwrap();
        assert_eq!(header_only.recovered_event_count, 0);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn mmap_archive_does_not_allocate_from_untrusted_manifest_event_count() {
        let directory = std::env::temp_dir().join(format!(
            "aegis-mmap-manifest-count-{}-{}",
            std::process::id(),
            1_u64
        ));
        let event = RunEvent::new(
            1,
            1,
            RunEventKind::MissionCompiled,
            1,
            [1; 32],
            None,
            [0; 32],
        );
        let ledger = RunEventLedger::from_events(1, vec![event]).unwrap();
        let manifest = RunEventSegmentArchive::write_ledger(&directory, 1, &ledger).unwrap();
        let mut forged_entry = manifest.entries[0].clone();
        forged_entry.end_event_id = u64::MAX;
        forged_entry.event_count = u64::MAX;
        forged_entry.refresh_segment_commit_hash(manifest.run_id);
        let forged_manifest = RunEventSegmentManifest::new(manifest.run_id, vec![forged_entry]);

        // The declared count is metadata, not an allocation authority.  The
        // reader must reject the real one-row segment without attempting a
        // usize::MAX allocation or panicking the process.
        assert!(RunEventSegmentArchive::read_ledger_mmap(&directory, &forged_manifest).is_err());
        let _ = std::fs::remove_dir_all(directory);
    }

    #[test]
    fn segmented_writer_rejects_unbounded_segment_capacity() {
        let directory = std::env::temp_dir().join(format!(
            "aegis-segmented-writer-capacity-{}-{}",
            std::process::id(),
            1_u64
        ));
        let result = SegmentedArrowAuditStream::create(&directory, 1, usize::MAX);
        assert_eq!(
            result.err(),
            Some("invalid segmented arrow audit stream config")
        );
        assert!(!directory.exists());
    }

    #[test]
    fn segmented_writer_lock_rejects_concurrent_writer_and_releases_on_drop() {
        let directory = std::env::temp_dir().join(format!(
            "aegis-segmented-writer-lock-{}-{}",
            std::process::id(),
            1_u64
        ));
        let run_id = 0xAEE6_0200_0000_0001_u128;
        let writer = SegmentedArrowAuditStream::create(&directory, run_id, 1).unwrap();
        let concurrent = SegmentedArrowAuditStream::create(&directory, run_id, 1);
        assert_eq!(
            concurrent.err(),
            Some("segmented arrow audit stream already has a writer")
        );

        drop(writer);
        let reopened = SegmentedArrowAuditStream::create(&directory, run_id, 1).unwrap();
        drop(reopened);
        assert!(!directory.join(format!("run-{run_id}.writer.lock")).exists());
        let _ = std::fs::remove_dir_all(directory);
    }
}

fn blake3_hash_bytes(bytes: &[u8]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(bytes);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn replay_artifact_write_evidence_hash(
    staged_temp_file_used: bool,
    temp_file_synced_before_publish: bool,
    publish_completed: bool,
    parent_directory_sync_attempted: bool,
    replace_existing_supported: bool,
    publish_write_through_requested: bool,
    logical_payload_bytes: u64,
    logical_payload_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-replay-artifact-write-evidence-v1");
    update_u8(&mut hasher, u8::from(staged_temp_file_used));
    update_u8(&mut hasher, u8::from(temp_file_synced_before_publish));
    update_u8(&mut hasher, u8::from(publish_completed));
    update_u8(&mut hasher, u8::from(parent_directory_sync_attempted));
    update_u8(&mut hasher, u8::from(replace_existing_supported));
    update_u8(&mut hasher, u8::from(publish_write_through_requested));
    update_u64(&mut hasher, logical_payload_bytes);
    hasher.update(&logical_payload_hash);
    *hasher.finalize().as_bytes()
}

fn write_synced_artifact(path: &Path, payload: &[u8]) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() {
        return Err("invalid synced artifact path");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .map_err(|_| "failed to create synced artifact directory")?;
    }

    let temp_path = synced_artifact_temp_path(path);
    let write_result = (|| {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create synced artifact temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write synced artifact temp file")?;
        file.flush()
            .map_err(|_| "failed to flush synced artifact temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync synced artifact temp file")?;
        Ok::<(), &'static str>(())
    })();
    if write_result.is_err() {
        let _ = std::fs::remove_file(&temp_path);
        return write_result;
    }

    if let Err(error) = publish_synced_artifact_temp(&temp_path, path) {
        let _ = std::fs::remove_file(&temp_path);
        return Err(error);
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        let _ = File::open(parent).and_then(|directory| directory.sync_all());
    }
    Ok(())
}

#[cfg(windows)]
fn publish_synced_artifact_temp(temp_path: &Path, path: &Path) -> Result<(), &'static str> {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;

    const MOVEFILE_REPLACE_EXISTING: u32 = 0x1;
    const MOVEFILE_WRITE_THROUGH: u32 = 0x8;

    unsafe extern "system" {
        fn MoveFileExW(
            existing_file_name: *const u16,
            new_file_name: *const u16,
            flags: u32,
        ) -> i32;
    }

    fn wide_null(value: &OsStr) -> Vec<u16> {
        value.encode_wide().chain(std::iter::once(0)).collect()
    }

    let temp = wide_null(temp_path.as_os_str());
    let target = wide_null(path.as_os_str());
    // SAFETY: FFI call to Win32 `MoveFileExW` for atomic file rename. Both
    // pointers come from `wide_null(...)` which appends a UTF-16 NUL
    // terminator (`std::iter::once(0)`), so each is a valid NUL-terminated
    // UTF-16 string as required by the Win32 API. The `Vec<u16>` storage
    // remains valid for the duration of the call (used immediately below).
    // Flags (`MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH`) are valid
    // per Windows docs. No concurrent caller renames the same pair — this is
    // sequential crash-safety rename, and any failure surfaces via the non-zero
    // return code check. The `false` return on success is ignored as Windows
    // convention is zero return != 0 = success.
    let ok = unsafe {
        MoveFileExW(
            temp.as_ptr(),
            target.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if ok == 0 {
        return Err("failed to publish synced artifact");
    }
    Ok(())
}

#[cfg(not(windows))]
fn publish_synced_artifact_temp(temp_path: &Path, path: &Path) -> Result<(), &'static str> {
    std::fs::rename(temp_path, path).map_err(|_| "failed to publish synced artifact")
}

fn synced_artifact_temp_path(path: &Path) -> PathBuf {
    let mut file_name = path
        .file_name()
        .unwrap_or_else(|| std::ffi::OsStr::new("artifact"))
        .to_os_string();
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    file_name.push(format!(".tmp-{}-{unique}", std::process::id()));
    path.with_file_name(file_name)
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

fn count_checkpoint_events(ledger: &RunEventLedger) -> usize {
    ledger
        .events()
        .iter()
        .filter(|event| event.kind == RunEventKind::CheckpointSealed)
        .count()
}

fn count_context_fold_events(ledger: &RunEventLedger) -> usize {
    ledger
        .events()
        .iter()
        .filter(|event| event.kind == RunEventKind::ContextFoldRecorded)
        .count()
}

fn count_context_fold_checkpoint_pairs(ledger: &RunEventLedger) -> usize {
    ledger
        .events()
        .windows(2)
        .filter(|window| {
            window[0].kind == RunEventKind::ContextFoldRecorded
                && window[1].kind == RunEventKind::CheckpointSealed
        })
        .count()
}

fn events_since_last_checkpoint(ledger: &RunEventLedger) -> Option<usize> {
    ledger
        .events()
        .iter()
        .rposition(|event| event.kind == RunEventKind::CheckpointSealed)
        .map(|index| ledger.len().saturating_sub(index + 1))
}

fn context_fold_replay_evidence_hash(ledger: &RunEventLedger) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-context-fold-replay-evidence-v1");
    let mut count = 0u64;
    for event in ledger
        .events()
        .iter()
        .filter(|event| event.kind == RunEventKind::ContextFoldRecorded)
    {
        count = count.saturating_add(1);
        update_u64(&mut hasher, event.event_id);
        update_u128(&mut hasher, event.subject_id);
        hasher.update(&event.primary_hash);
        hasher.update(&event.secondary_hash.unwrap_or([0; 32]));
        hasher.update(&event.event_hash);
    }
    update_u64(&mut hasher, count);
    *hasher.finalize().as_bytes()
}

fn synthetic_context_fold_record(
    run_id: RunId,
    cycle: u32,
) -> Result<ContextFoldRecord, &'static str> {
    let subject = cycle as SubjectId;
    let retained_node_ids = [subject, 100_000 + subject, 200_000 + subject];
    let folded_node_ids = [300_000 + subject, 400_000 + subject, 500_000 + subject];
    ContextFoldRecord::new(
        subject,
        &retained_node_ids,
        &folded_node_ids,
        768 + (cycle % 97),
        1_280 + (cycle % 193),
        10_000 + cycle as u64,
        retained_node_ids.len() + folded_node_ids.len(),
        endurance_hash("context-fold-pack", run_id, cycle),
    )
    .map_err(|_| "failed to build endurance context fold")
}

fn endurance_hash(label: &str, run_id: RunId, cycle: u32) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-replay-endurance-synthetic-v1");
    hasher.update(label.as_bytes());
    update_u128(&mut hasher, run_id);
    update_u64(&mut hasher, cycle as u64);
    *hasher.finalize().as_bytes()
}

fn split_u128(value: u128) -> (u64, u64) {
    ((value >> 64) as u64, value as u64)
}

fn join_u128(hi: u64, lo: u64) -> u128 {
    ((hi as u128) << 64) | lo as u128
}

fn u64_payload_hash(label: &str, value: u64) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(label.as_bytes());
    hasher.update(&value.to_le_bytes());
    *hasher.finalize().as_bytes()
}

fn update_optional_hash(hasher: &mut Hasher, value: Option<[u8; 32]>) {
    match value {
        Some(hash) => {
            update_u8(hasher, 1);
            hasher.update(&hash);
        }
        None => update_u8(hasher, 0),
    }
}
