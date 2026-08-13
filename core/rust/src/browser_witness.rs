use crate::evidence_index::{
    browser_page_search_candidate_record_hash, browser_page_search_evidence_ref_hash,
    browser_page_search_pattern_hash, BrowserPageSearchCandidateError,
    BrowserPageSearchCandidateRecord, CandidateEvidenceRef, EvidenceCandidateTier,
};
use crate::policy::{PolicyFacts, SideEffectClass, StagingEvidenceKind};
use blake3::Hasher;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

pub type BrowserRunId = u128;
pub type BrowserActionId = u128;

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum BrowserActionKind {
    Navigate,
    Click,
    TypeText,
    SelectOption,
    Download,
    PermissionPrompt,
    StateSnapshot,
    SearchPage,
    FindElements,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum BrowserActionPlanKind {
    Navigate,
    StateSnapshot,
    SearchPage,
    FindElements,
    Click,
    InputText,
    SelectOption,
    Download,
    PermissionPrompt,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum BrowserPromptDecision {
    Deny,
    Allow,
    Dismiss,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum BrowserCollectorKind {
    InAppBrowser,
    ChromeExtension,
    ComputerUse,
    PlaywrightCdp,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
pub enum BrowserArtifactKind {
    UrlBefore = 1,
    UrlAfter = 2,
    DomSnapshotBefore = 3,
    DomSnapshotAfter = 4,
    ScreenshotBefore = 5,
    ScreenshotAfter = 6,
    AccessibilityTreeAfter = 7,
    NetworkLog = 8,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserActionTrace {
    pub action_id: BrowserActionId,
    pub action_kind: BrowserActionKind,
    pub target_hash: [u8; 32],
    pub input_hash: Option<[u8; 32]>,
    pub coordinate_x: Option<i32>,
    pub coordinate_y: Option<i32>,
    pub prompt_decision: Option<BrowserPromptDecision>,
    pub policy_window_hash: [u8; 32],
    pub trace_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserActionPlanRecord {
    pub run_id: BrowserRunId,
    pub action_id: BrowserActionId,
    pub plan_kind: BrowserActionPlanKind,
    pub sequence_number: u64,
    pub typed_tool_ir_hash: [u8; 32],
    pub expected_url_before_hash: [u8; 32],
    pub target_hash: [u8; 32],
    pub input_hash: Option<[u8; 32]>,
    pub coordinate_x: Option<i32>,
    pub coordinate_y: Option<i32>,
    pub prompt_decision: Option<BrowserPromptDecision>,
    pub policy_window_hash: [u8; 32],
    pub browser_session_hash: [u8; 32],
    pub redaction_policy_hash: [u8; 32],
    pub terminates_sequence: bool,
    pub plan_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserWitnessProof {
    pub run_id: BrowserRunId,
    pub action_id: BrowserActionId,
    pub side_effect_class: SideEffectClass,
    pub url_before_hash: [u8; 32],
    pub url_after_hash: [u8; 32],
    pub dom_snapshot_hash_before: [u8; 32],
    pub dom_snapshot_hash_after: [u8; 32],
    pub screenshot_hash_before: [u8; 32],
    pub screenshot_hash_after: [u8; 32],
    pub accessibility_tree_hash_after: [u8; 32],
    pub network_log_hash: [u8; 32],
    pub action_trace_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub browser_session_hash: [u8; 32],
    pub redaction_policy_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserArtifactRef {
    pub kind: BrowserArtifactKind,
    pub byte_len: u64,
    pub content_hash: [u8; 32],
    pub storage_ref_hash: [u8; 32],
    pub artifact_ref_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserArtifactFilePathRef {
    pub kind: BrowserArtifactKind,
    pub canonical_path: PathBuf,
    pub normalized_path_hash: [u8; 32],
    pub path_ref_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserLiveCollectorManifest {
    pub collector_kind: BrowserCollectorKind,
    pub run_id: BrowserRunId,
    pub action_id: BrowserActionId,
    pub sequence_number: u64,
    pub observed_at_unix_ms: u64,
    pub collector_config_hash: [u8; 32],
    pub collector_capability_hash: [u8; 32],
    pub browser_session_hash: [u8; 32],
    pub redaction_policy_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub artifact_path_refs: Vec<BrowserArtifactFilePathRef>,
    pub manifest_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserLiveCollectorArtifact {
    pub kind: BrowserArtifactKind,
    pub bytes: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserLiveCollectorRun {
    pub manifest: BrowserLiveCollectorManifest,
    pub run_directory: PathBuf,
    pub initial_artifact_manifest_hash: [u8; 32],
    pub collector_run_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BrowserArtifactReadError {
    InvalidMaxBytes,
    Missing,
    NotFile,
    Empty,
    Oversized,
    PathCanonicalizationFailed,
    ReadFailed,
    ModifiedDuringRead,
    ArtifactMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BrowserLiveCollectorRunError {
    InvalidMetadata,
    InvalidOutputDirectory,
    EmptyArtifact,
    OversizedArtifact,
    MissingRequiredArtifact,
    DuplicateArtifactKind,
    WriteFailed,
    ArtifactRead(BrowserArtifactReadError),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserCollectorEvidenceEnvelope {
    pub collector_kind: BrowserCollectorKind,
    pub run_id: BrowserRunId,
    pub action_id: BrowserActionId,
    pub sequence_number: u64,
    pub observed_at_unix_ms: u64,
    pub collector_config_hash: [u8; 32],
    pub collector_capability_hash: [u8; 32],
    pub browser_session_hash: [u8; 32],
    pub redaction_policy_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub artifacts: Vec<BrowserArtifactRef>,
    pub artifact_manifest_hash: [u8; 32],
    pub envelope_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserObservationPacket {
    pub collector_kind: BrowserCollectorKind,
    pub run_id: BrowserRunId,
    pub action_id: BrowserActionId,
    pub sequence_number: u64,
    pub observed_at_unix_ms: u64,
    pub collector_config_hash: [u8; 32],
    pub collector_capability_hash: [u8; 32],
    pub raw_artifact_manifest_hash: [u8; 32],
    pub action_trace: BrowserActionTrace,
    pub proof: BrowserWitnessProof,
    pub modality_bundle_hash: [u8; 32],
    pub collector_provenance_hash: [u8; 32],
    pub packet_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserPageSearchCandidateProof {
    pub record: BrowserPageSearchCandidateRecord,
    pub candidates: Vec<CandidateEvidenceRef>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserOpsBenchVerifiedTaskRecord {
    pub task_id_hash: [u8; 32],
    pub run_id: BrowserRunId,
    pub action_id: BrowserActionId,
    pub sequence_number: u64,
    pub manifest: BrowserLiveCollectorManifest,
    pub envelope: BrowserCollectorEvidenceEnvelope,
    pub predicate_count: u64,
    pub record_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BrowserOpsBenchVerificationProof {
    pub scorecard_path: PathBuf,
    pub suite_hash: [u8; 32],
    pub task_records: Vec<BrowserOpsBenchVerifiedTaskRecord>,
    pub scorecard_file_hash: [u8; 32],
    pub verified_task_count: u64,
    pub verified_records_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserOpsBenchVerificationReportWriteEvidence {
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserOpsBenchVerificationReport {
    pub schema: &'static str,
    pub scorecard_path: PathBuf,
    pub scorecard_file_hash: [u8; 32],
    pub suite_hash: [u8; 32],
    pub proof_hash: [u8; 32],
    pub verified_records_hash: [u8; 32],
    pub verified_task_count: u64,
    pub replay_recorded: bool,
    pub run_id: u128,
    pub verification_subject_id: u128,
    pub verification_event_id: u64,
    pub verification_event_hash: Option<[u8; 32]>,
    pub replay_binding_hash: Option<[u8; 32]>,
    pub report_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrowserOpsBenchVerificationReportArtifact {
    pub schema_version: u32,
    pub report: BrowserOpsBenchVerificationReport,
    pub write_evidence: BrowserOpsBenchVerificationReportWriteEvidence,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BrowserOpsBenchVerificationError {
    ScorecardReadFailed,
    ScorecardParseFailed,
    InvalidScorecard,
    MissingTaskRecord,
    FailedTaskRecord,
    InvalidProducerMetadata,
    ArtifactRead(BrowserArtifactReadError),
}

const REQUIRED_BROWSER_ARTIFACT_KIND_COUNT: usize = 8;
const DEFAULT_BROWSER_ARTIFACT_MAX_BYTES: u64 = 64 * 1024 * 1024;
const REQUIRED_BROWSER_ARTIFACT_KINDS: [BrowserArtifactKind; REQUIRED_BROWSER_ARTIFACT_KIND_COUNT] = [
    BrowserArtifactKind::UrlBefore,
    BrowserArtifactKind::UrlAfter,
    BrowserArtifactKind::DomSnapshotBefore,
    BrowserArtifactKind::DomSnapshotAfter,
    BrowserArtifactKind::ScreenshotBefore,
    BrowserArtifactKind::ScreenshotAfter,
    BrowserArtifactKind::AccessibilityTreeAfter,
    BrowserArtifactKind::NetworkLog,
];

impl BrowserActionTrace {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        action_id: BrowserActionId,
        action_kind: BrowserActionKind,
        target_hash: [u8; 32],
        input_hash: Option<[u8; 32]>,
        coordinates: Option<(i32, i32)>,
        prompt_decision: Option<BrowserPromptDecision>,
        policy_window_hash: [u8; 32],
    ) -> Self {
        let (coordinate_x, coordinate_y) = match coordinates {
            Some((x, y)) => (Some(x), Some(y)),
            None => (None, None),
        };
        let trace_hash = browser_action_trace_hash(
            action_id,
            action_kind,
            target_hash,
            input_hash,
            coordinate_x,
            coordinate_y,
            prompt_decision,
            policy_window_hash,
        );
        Self {
            action_id,
            action_kind,
            target_hash,
            input_hash,
            coordinate_x,
            coordinate_y,
            prompt_decision,
            policy_window_hash,
            trace_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        let coordinates_are_coherent = self.coordinate_x.is_some() == self.coordinate_y.is_some();
        let input_is_valid = match self.action_kind {
            BrowserActionKind::Navigate
            | BrowserActionKind::Click
            | BrowserActionKind::StateSnapshot => {
                self.input_hash.is_none() && self.prompt_decision.is_none()
            }
            BrowserActionKind::TypeText
            | BrowserActionKind::SelectOption
            | BrowserActionKind::Download
            | BrowserActionKind::SearchPage
            | BrowserActionKind::FindElements => {
                self.input_hash.is_some_and(|hash| nonzero_hash(&hash))
            }
            BrowserActionKind::PermissionPrompt => {
                self.input_hash.is_none() && self.prompt_decision.is_some()
            }
        };
        self.action_id > 0
            && nonzero_hash(&self.target_hash)
            && nonzero_hash(&self.policy_window_hash)
            && coordinates_are_coherent
            && input_is_valid
            && self.trace_hash
                == browser_action_trace_hash(
                    self.action_id,
                    self.action_kind,
                    self.target_hash,
                    self.input_hash,
                    self.coordinate_x,
                    self.coordinate_y,
                    self.prompt_decision,
                    self.policy_window_hash,
                )
    }
}

impl BrowserActionPlanKind {
    pub fn to_action_kind(self) -> BrowserActionKind {
        match self {
            Self::Navigate => BrowserActionKind::Navigate,
            Self::StateSnapshot => BrowserActionKind::StateSnapshot,
            Self::SearchPage => BrowserActionKind::SearchPage,
            Self::FindElements => BrowserActionKind::FindElements,
            Self::Click => BrowserActionKind::Click,
            Self::InputText => BrowserActionKind::TypeText,
            Self::SelectOption => BrowserActionKind::SelectOption,
            Self::Download => BrowserActionKind::Download,
            Self::PermissionPrompt => BrowserActionKind::PermissionPrompt,
        }
    }

    pub fn requires_sequence_termination(self) -> bool {
        matches!(self, Self::Navigate | Self::SearchPage | Self::Download)
    }
}

impl BrowserActionPlanRecord {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        plan_kind: BrowserActionPlanKind,
        sequence_number: u64,
        typed_tool_ir_hash: [u8; 32],
        expected_url_before_hash: [u8; 32],
        target_hash: [u8; 32],
        input_hash: Option<[u8; 32]>,
        coordinates: Option<(i32, i32)>,
        prompt_decision: Option<BrowserPromptDecision>,
        policy_window_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        terminates_sequence: bool,
    ) -> Self {
        let (coordinate_x, coordinate_y) = match coordinates {
            Some((x, y)) => (Some(x), Some(y)),
            None => (None, None),
        };
        let plan_hash = browser_action_plan_record_hash(
            run_id,
            action_id,
            plan_kind,
            sequence_number,
            typed_tool_ir_hash,
            expected_url_before_hash,
            target_hash,
            input_hash,
            coordinate_x,
            coordinate_y,
            prompt_decision,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            terminates_sequence,
        );
        Self {
            run_id,
            action_id,
            plan_kind,
            sequence_number,
            typed_tool_ir_hash,
            expected_url_before_hash,
            target_hash,
            input_hash,
            coordinate_x,
            coordinate_y,
            prompt_decision,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            terminates_sequence,
            plan_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        let coordinates_are_coherent = self.coordinate_x.is_some() == self.coordinate_y.is_some();
        let input_is_valid = match self.plan_kind {
            BrowserActionPlanKind::Navigate
            | BrowserActionPlanKind::Click
            | BrowserActionPlanKind::StateSnapshot => {
                self.input_hash.is_none() && self.prompt_decision.is_none()
            }
            BrowserActionPlanKind::SearchPage
            | BrowserActionPlanKind::FindElements
            | BrowserActionPlanKind::InputText
            | BrowserActionPlanKind::SelectOption
            | BrowserActionPlanKind::Download => {
                self.input_hash.is_some_and(|hash| nonzero_hash(&hash))
                    && self.prompt_decision.is_none()
            }
            BrowserActionPlanKind::PermissionPrompt => {
                self.input_hash.is_none() && self.prompt_decision.is_some()
            }
        };
        self.run_id > 0
            && self.action_id > 0
            && self.sequence_number > 0
            && nonzero_hash(&self.typed_tool_ir_hash)
            && nonzero_hash(&self.expected_url_before_hash)
            && nonzero_hash(&self.target_hash)
            && nonzero_hash(&self.policy_window_hash)
            && nonzero_hash(&self.browser_session_hash)
            && nonzero_hash(&self.redaction_policy_hash)
            && coordinates_are_coherent
            && input_is_valid
            && (!self.plan_kind.requires_sequence_termination() || self.terminates_sequence)
            && self.plan_hash
                == browser_action_plan_record_hash(
                    self.run_id,
                    self.action_id,
                    self.plan_kind,
                    self.sequence_number,
                    self.typed_tool_ir_hash,
                    self.expected_url_before_hash,
                    self.target_hash,
                    self.input_hash,
                    self.coordinate_x,
                    self.coordinate_y,
                    self.prompt_decision,
                    self.policy_window_hash,
                    self.browser_session_hash,
                    self.redaction_policy_hash,
                    self.terminates_sequence,
                )
            && nonzero_hash(&self.plan_hash)
    }

    pub fn to_action_trace(&self) -> Option<BrowserActionTrace> {
        self.is_valid().then(|| {
            BrowserActionTrace::new(
                self.action_id,
                self.plan_kind.to_action_kind(),
                self.target_hash,
                self.input_hash,
                self.coordinate_x.zip(self.coordinate_y),
                self.prompt_decision,
                self.policy_window_hash,
            )
        })
    }

    pub fn is_valid_for_packet(
        &self,
        expected_typed_tool_ir_hash: [u8; 32],
        packet: &BrowserObservationPacket,
    ) -> bool {
        let Some(action_trace) = self.to_action_trace() else {
            return false;
        };
        self.typed_tool_ir_hash == expected_typed_tool_ir_hash
            && nonzero_hash(&expected_typed_tool_ir_hash)
            && packet.is_valid()
            && self.run_id == packet.run_id
            && self.action_id == packet.action_id
            && self.sequence_number == packet.sequence_number
            && self.expected_url_before_hash == packet.proof.url_before_hash
            && self.policy_window_hash == packet.proof.policy_window_hash
            && self.browser_session_hash == packet.proof.browser_session_hash
            && self.redaction_policy_hash == packet.proof.redaction_policy_hash
            && action_trace == packet.action_trace
    }

    pub fn sequence_should_abort_after(
        &self,
        expected_typed_tool_ir_hash: [u8; 32],
        packet: &BrowserObservationPacket,
    ) -> Option<bool> {
        self.is_valid_for_packet(expected_typed_tool_ir_hash, packet)
            .then(|| {
                self.terminates_sequence
                    || packet.proof.url_before_hash != packet.proof.url_after_hash
            })
    }
}

impl BrowserArtifactFilePathRef {
    pub fn from_path(
        kind: BrowserArtifactKind,
        path: impl AsRef<Path>,
    ) -> Result<Self, BrowserArtifactReadError> {
        Self::from_path_with_max_bytes(kind, path, DEFAULT_BROWSER_ARTIFACT_MAX_BYTES)
    }

    pub fn from_path_with_max_bytes(
        kind: BrowserArtifactKind,
        path: impl AsRef<Path>,
        max_bytes: u64,
    ) -> Result<Self, BrowserArtifactReadError> {
        canonical_browser_artifact_file_path_ref(kind, path.as_ref(), max_bytes)
    }

    pub fn is_valid(&self) -> bool {
        !self.canonical_path.as_os_str().is_empty()
            && self.normalized_path_hash
                == browser_file_normalized_path_hash(self.kind, &self.canonical_path)
            && nonzero_hash(&self.normalized_path_hash)
            && self.path_ref_hash
                == browser_file_path_ref_hash(self.kind, self.normalized_path_hash)
            && nonzero_hash(&self.path_ref_hash)
    }
}

impl BrowserLiveCollectorManifest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        mut artifact_path_refs: Vec<BrowserArtifactFilePathRef>,
    ) -> Self {
        artifact_path_refs.sort_by_key(|path_ref| path_ref.kind);
        let manifest_hash = browser_live_collector_manifest_hash(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            &artifact_path_refs,
        );
        Self {
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifact_path_refs,
            manifest_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.run_id > 0
            && self.action_id > 0
            && self.sequence_number > 0
            && self.observed_at_unix_ms > 0
            && nonzero_hash(&self.collector_config_hash)
            && nonzero_hash(&self.collector_capability_hash)
            && nonzero_hash(&self.browser_session_hash)
            && nonzero_hash(&self.redaction_policy_hash)
            && nonzero_hash(&self.policy_window_hash)
            && self.has_exact_required_path_refs()
            && self.manifest_hash
                == browser_live_collector_manifest_hash(
                    self.collector_kind,
                    self.run_id,
                    self.action_id,
                    self.sequence_number,
                    self.observed_at_unix_ms,
                    self.collector_config_hash,
                    self.collector_capability_hash,
                    self.browser_session_hash,
                    self.redaction_policy_hash,
                    self.policy_window_hash,
                    &self.artifact_path_refs,
                )
            && nonzero_hash(&self.manifest_hash)
    }

    pub fn to_envelope(
        &self,
    ) -> Result<BrowserCollectorEvidenceEnvelope, BrowserArtifactReadError> {
        self.to_envelope_with_max_bytes(DEFAULT_BROWSER_ARTIFACT_MAX_BYTES)
    }

    pub fn to_envelope_with_max_bytes(
        &self,
        max_bytes: u64,
    ) -> Result<BrowserCollectorEvidenceEnvelope, BrowserArtifactReadError> {
        if !self.is_valid() {
            return Err(BrowserArtifactReadError::ArtifactMismatch);
        }
        BrowserCollectorEvidenceEnvelope::from_file_path_refs_with_max_bytes(
            self.collector_kind,
            self.run_id,
            self.action_id,
            self.sequence_number,
            self.observed_at_unix_ms,
            self.collector_config_hash,
            self.collector_capability_hash,
            self.browser_session_hash,
            self.redaction_policy_hash,
            self.policy_window_hash,
            &self.artifact_path_refs,
            max_bytes,
        )
    }

    pub fn verify_files(&self) -> Result<(), BrowserArtifactReadError> {
        let envelope = self.to_envelope()?;
        envelope.verify_file_path_refs(&self.artifact_path_refs)
    }

    pub fn binds_action_trace(&self, action_trace: &BrowserActionTrace) -> bool {
        self.is_valid()
            && action_trace.is_valid()
            && self.action_id == action_trace.action_id
            && self.policy_window_hash == action_trace.policy_window_hash
    }

    pub fn binds_plan(&self, plan: &BrowserActionPlanRecord) -> bool {
        self.is_valid()
            && plan.is_valid()
            && self.run_id == plan.run_id
            && self.action_id == plan.action_id
            && self.sequence_number == plan.sequence_number
            && self.browser_session_hash == plan.browser_session_hash
            && self.redaction_policy_hash == plan.redaction_policy_hash
            && self.policy_window_hash == plan.policy_window_hash
    }

    fn has_exact_required_path_refs(&self) -> bool {
        self.artifact_path_refs.len() == REQUIRED_BROWSER_ARTIFACT_KIND_COUNT
            && self
                .artifact_path_refs
                .iter()
                .enumerate()
                .all(|(index, path_ref)| {
                    path_ref.is_valid() && path_ref.kind as u8 == (index as u8 + 1)
                })
    }
}

impl BrowserLiveCollectorArtifact {
    pub fn new(kind: BrowserArtifactKind, bytes: impl Into<Vec<u8>>) -> Self {
        Self {
            kind,
            bytes: bytes.into(),
        }
    }

    pub fn is_valid_with_max_bytes(&self, max_bytes: u64) -> bool {
        !self.bytes.is_empty() && (self.bytes.len() as u64) <= max_bytes
    }
}

impl BrowserLiveCollectorRun {
    #[allow(clippy::too_many_arguments)]
    pub fn from_artifacts(
        output_root: impl AsRef<Path>,
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        artifacts: &[BrowserLiveCollectorArtifact],
    ) -> Result<Self, BrowserLiveCollectorRunError> {
        Self::from_artifacts_with_max_bytes(
            output_root,
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifacts,
            DEFAULT_BROWSER_ARTIFACT_MAX_BYTES,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn from_artifacts_with_max_bytes(
        output_root: impl AsRef<Path>,
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        artifacts: &[BrowserLiveCollectorArtifact],
        max_bytes: u64,
    ) -> Result<Self, BrowserLiveCollectorRunError> {
        if max_bytes == 0
            || run_id == 0
            || action_id == 0
            || sequence_number == 0
            || observed_at_unix_ms == 0
            || !nonzero_hash(&collector_config_hash)
            || !nonzero_hash(&collector_capability_hash)
            || !nonzero_hash(&browser_session_hash)
            || !nonzero_hash(&redaction_policy_hash)
            || !nonzero_hash(&policy_window_hash)
        {
            return Err(BrowserLiveCollectorRunError::InvalidMetadata);
        }
        let output_root = output_root.as_ref();
        if output_root.as_os_str().is_empty() {
            return Err(BrowserLiveCollectorRunError::InvalidOutputDirectory);
        }
        let ordered_artifacts = required_live_collector_artifacts(artifacts, max_bytes)?;
        let run_directory = output_root.join(browser_live_collector_run_directory_name(
            run_id,
            action_id,
            sequence_number,
        ));
        fs::create_dir_all(&run_directory)
            .map_err(|_| BrowserLiveCollectorRunError::WriteFailed)?;
        let run_directory = fs::canonicalize(&run_directory)
            .map_err(|_| BrowserLiveCollectorRunError::InvalidOutputDirectory)?;

        let mut artifact_path_refs = Vec::with_capacity(REQUIRED_BROWSER_ARTIFACT_KIND_COUNT);
        for artifact in ordered_artifacts {
            let path = run_directory.join(browser_artifact_file_name(artifact.kind));
            write_browser_live_collector_artifact(&path, &artifact.bytes, max_bytes)?;
            artifact_path_refs.push(
                BrowserArtifactFilePathRef::from_path_with_max_bytes(
                    artifact.kind,
                    &path,
                    max_bytes,
                )
                .map_err(BrowserLiveCollectorRunError::ArtifactRead)?,
            );
        }

        let manifest = BrowserLiveCollectorManifest::new(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifact_path_refs,
        );
        manifest
            .verify_files()
            .map_err(BrowserLiveCollectorRunError::ArtifactRead)?;
        let initial_envelope = manifest
            .to_envelope()
            .map_err(BrowserLiveCollectorRunError::ArtifactRead)?;
        let initial_artifact_manifest_hash = initial_envelope.artifact_manifest_hash;
        let collector_run_hash = browser_live_collector_run_hash(
            manifest.manifest_hash,
            initial_artifact_manifest_hash,
            &run_directory,
        );
        Ok(Self {
            manifest,
            run_directory,
            initial_artifact_manifest_hash,
            collector_run_hash,
        })
    }

    pub fn is_valid_for_action_trace(&self, action_trace: &BrowserActionTrace) -> bool {
        self.manifest.binds_action_trace(action_trace)
            && self.files_match_initial_artifact_manifest()
            && self.collector_run_hash
                == browser_live_collector_run_hash(
                    self.manifest.manifest_hash,
                    self.initial_artifact_manifest_hash,
                    &self.run_directory,
                )
            && nonzero_hash(&self.collector_run_hash)
    }

    pub fn is_valid_for_plan(&self, plan: &BrowserActionPlanRecord) -> bool {
        self.manifest.binds_plan(plan)
            && self.files_match_initial_artifact_manifest()
            && self.collector_run_hash
                == browser_live_collector_run_hash(
                    self.manifest.manifest_hash,
                    self.initial_artifact_manifest_hash,
                    &self.run_directory,
                )
            && nonzero_hash(&self.collector_run_hash)
    }

    fn files_match_initial_artifact_manifest(&self) -> bool {
        self.manifest.to_envelope().is_ok_and(|envelope| {
            envelope.artifact_manifest_hash == self.initial_artifact_manifest_hash
        })
    }
}

#[derive(Deserialize)]
struct BrowserOpsBenchScorecardImport {
    schema: String,
    suite_name: String,
    total: u64,
    passed: u64,
    failed: u64,
    overall_ok: bool,
    truth_claim: bool,
    verifier: String,
    records: Vec<BrowserOpsBenchTaskRecordImport>,
}

#[derive(Deserialize)]
struct BrowserOpsBenchTaskRecordImport {
    task_id: String,
    ok: bool,
    run_directory: String,
    producer_metadata_path: String,
    predicate_results: Vec<BrowserOpsBenchPredicateResultImport>,
    error: String,
}

#[derive(Deserialize)]
struct BrowserOpsBenchPredicateResultImport {
    name: String,
    artifact_kind: String,
    artifact_path: String,
    byte_len: u64,
    ok: bool,
    detail: String,
}

#[derive(Deserialize)]
struct BrowserOpsBenchProducerMetadataImport {
    schema: String,
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    sequence_number: u64,
    run_directory: String,
    artifact_paths: BTreeMap<String, String>,
    verifier: String,
    truth_claim: bool,
}

impl BrowserOpsBenchVerificationProof {
    #[allow(clippy::too_many_arguments)]
    pub fn verify_scorecard(
        scorecard_path: impl AsRef<Path>,
        collector_kind: BrowserCollectorKind,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
    ) -> Result<Self, BrowserOpsBenchVerificationError> {
        if observed_at_unix_ms == 0
            || !nonzero_hash(&collector_config_hash)
            || !nonzero_hash(&collector_capability_hash)
            || !nonzero_hash(&browser_session_hash)
            || !nonzero_hash(&redaction_policy_hash)
            || !nonzero_hash(&policy_window_hash)
        {
            return Err(BrowserOpsBenchVerificationError::InvalidScorecard);
        }
        let scorecard_path = scorecard_path.as_ref();
        let scorecard_bytes = fs::read(scorecard_path)
            .map_err(|_| BrowserOpsBenchVerificationError::ScorecardReadFailed)?;
        let scorecard_file_hash = *blake3::hash(&scorecard_bytes).as_bytes();
        let scorecard: BrowserOpsBenchScorecardImport = serde_json::from_slice(&scorecard_bytes)
            .map_err(|_| BrowserOpsBenchVerificationError::ScorecardParseFailed)?;
        if scorecard.schema != "aegis-browser-ops-bench-scorecard-v1"
            || scorecard.truth_claim
            || scorecard.verifier != "rust-browser-live-collector-run"
            || !scorecard.overall_ok
            || scorecard.total == 0
            || scorecard.failed != 0
            || scorecard.passed != scorecard.total
            || scorecard.records.len() as u64 != scorecard.total
        {
            return Err(BrowserOpsBenchVerificationError::InvalidScorecard);
        }

        let mut task_records = Vec::with_capacity(scorecard.records.len());
        for record in scorecard.records {
            if !record.ok || !record.error.is_empty() {
                return Err(BrowserOpsBenchVerificationError::FailedTaskRecord);
            }
            if record.task_id.is_empty()
                || record.run_directory.is_empty()
                || record.producer_metadata_path.is_empty()
                || record.predicate_results.is_empty()
                || record
                    .predicate_results
                    .iter()
                    .any(|predicate| !predicate.ok || predicate.name.is_empty())
            {
                return Err(BrowserOpsBenchVerificationError::MissingTaskRecord);
            }

            let metadata_bytes = fs::read(&record.producer_metadata_path)
                .map_err(|_| BrowserOpsBenchVerificationError::ScorecardReadFailed)?;
            let metadata: BrowserOpsBenchProducerMetadataImport =
                serde_json::from_slice(&metadata_bytes)
                    .map_err(|_| BrowserOpsBenchVerificationError::ScorecardParseFailed)?;
            if metadata.schema != "aegis-browser-live-collector-producer-v1"
                || metadata.truth_claim
                || metadata.verifier != "rust-browser-live-collector-run"
                || metadata.run_id == 0
                || metadata.action_id == 0
                || metadata.sequence_number == 0
                || metadata.run_directory != record.run_directory
            {
                return Err(BrowserOpsBenchVerificationError::InvalidProducerMetadata);
            }

            let path_refs = browser_ops_bench_path_refs(&metadata.artifact_paths)?;
            for predicate in &record.predicate_results {
                let artifact_kind = browser_artifact_kind_from_scorecard(&predicate.artifact_kind)
                    .ok_or(BrowserOpsBenchVerificationError::InvalidScorecard)?;
                let artifact_path = metadata
                    .artifact_paths
                    .get(&predicate.artifact_kind)
                    .ok_or(BrowserOpsBenchVerificationError::InvalidScorecard)?;
                if artifact_path != &predicate.artifact_path || predicate.byte_len == 0 {
                    return Err(BrowserOpsBenchVerificationError::InvalidScorecard);
                }
                let fresh = BrowserArtifactRef::from_file(artifact_kind, artifact_path)
                    .map_err(BrowserOpsBenchVerificationError::ArtifactRead)?;
                if fresh.byte_len != predicate.byte_len || predicate.detail.is_empty() {
                    return Err(BrowserOpsBenchVerificationError::InvalidScorecard);
                }
            }

            let manifest = BrowserLiveCollectorManifest::new(
                collector_kind,
                metadata.run_id,
                metadata.action_id,
                metadata.sequence_number,
                observed_at_unix_ms,
                collector_config_hash,
                collector_capability_hash,
                browser_session_hash,
                redaction_policy_hash,
                policy_window_hash,
                path_refs,
            );
            let envelope = manifest
                .to_envelope()
                .map_err(BrowserOpsBenchVerificationError::ArtifactRead)?;
            let task_id_hash = *blake3::hash(record.task_id.as_bytes()).as_bytes();
            let record_hash = browser_ops_bench_verified_task_record_hash(
                task_id_hash,
                manifest.manifest_hash,
                envelope.artifact_manifest_hash,
                record.predicate_results.len() as u64,
            );
            task_records.push(BrowserOpsBenchVerifiedTaskRecord {
                task_id_hash,
                run_id: metadata.run_id,
                action_id: metadata.action_id,
                sequence_number: metadata.sequence_number,
                manifest,
                envelope,
                predicate_count: record.predicate_results.len() as u64,
                record_hash,
            });
        }
        let verified_records_hash = browser_ops_bench_verified_records_hash(&task_records);
        let suite_hash = *blake3::hash(scorecard.suite_name.as_bytes()).as_bytes();
        let proof_hash = browser_ops_bench_verification_proof_hash(
            suite_hash,
            scorecard_file_hash,
            verified_records_hash,
            task_records.len() as u64,
        );
        Ok(Self {
            scorecard_path: scorecard_path.to_path_buf(),
            suite_hash,
            verified_task_count: task_records.len() as u64,
            task_records,
            scorecard_file_hash,
            verified_records_hash,
            proof_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        !self.task_records.is_empty()
            && nonzero_hash(&self.suite_hash)
            && nonzero_hash(&self.scorecard_file_hash)
            && self.verified_task_count == self.task_records.len() as u64
            && self
                .task_records
                .iter()
                .all(BrowserOpsBenchVerifiedTaskRecord::is_valid)
            && self.verified_records_hash
                == browser_ops_bench_verified_records_hash(&self.task_records)
            && nonzero_hash(&self.verified_records_hash)
            && self.proof_hash
                == browser_ops_bench_verification_proof_hash(
                    self.suite_hash,
                    self.scorecard_file_hash,
                    self.verified_records_hash,
                    self.verified_task_count,
                )
            && nonzero_hash(&self.proof_hash)
    }

    pub fn replay_subject_id(&self) -> u128 {
        let mut bytes = [0u8; 16];
        bytes.copy_from_slice(&self.proof_hash[..16]);
        let value = u128::from_le_bytes(bytes);
        if value == 0 {
            1
        } else {
            value
        }
    }

    pub fn to_report(
        &self,
        run_id: u128,
        verification_event_id: u64,
        verification_event_hash: Option<[u8; 32]>,
        replay_binding_hash: Option<[u8; 32]>,
    ) -> BrowserOpsBenchVerificationReport {
        let replay_recorded = verification_event_id > 0
            && verification_event_hash.is_some_and(|hash| nonzero_hash(&hash))
            && replay_binding_hash.is_some_and(|hash| nonzero_hash(&hash));
        let report_hash = browser_ops_bench_verification_report_hash(
            self.scorecard_file_hash,
            self.suite_hash,
            self.proof_hash,
            self.verified_records_hash,
            self.verified_task_count,
            replay_recorded,
            run_id,
            self.replay_subject_id(),
            verification_event_id,
            verification_event_hash,
            replay_binding_hash,
        );
        BrowserOpsBenchVerificationReport {
            schema: "aegis-browser-ops-bench-verification-report-v1",
            scorecard_path: self.scorecard_path.clone(),
            scorecard_file_hash: self.scorecard_file_hash,
            suite_hash: self.suite_hash,
            proof_hash: self.proof_hash,
            verified_records_hash: self.verified_records_hash,
            verified_task_count: self.verified_task_count,
            replay_recorded,
            run_id,
            verification_subject_id: self.replay_subject_id(),
            verification_event_id,
            verification_event_hash,
            replay_binding_hash,
            report_hash,
        }
    }
}

impl BrowserOpsBenchVerificationReportWriteEvidence {
    fn for_logical_payload(logical_payload_bytes: u64, logical_payload_hash: [u8; 32]) -> Self {
        let staged_temp_file_used = true;
        let temp_file_synced_before_publish = true;
        let publish_completed = true;
        let parent_directory_sync_attempted = true;
        let replace_existing_supported = true;
        let publish_write_through_requested = cfg!(windows);
        let evidence_hash = browser_ops_bench_verification_report_write_evidence_hash(
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

    pub fn is_valid(&self) -> bool {
        self.staged_temp_file_used
            && self.temp_file_synced_before_publish
            && self.publish_completed
            && self.parent_directory_sync_attempted
            && self.replace_existing_supported
            && self.logical_payload_bytes > 0
            && nonzero_hash(&self.logical_payload_hash)
            && self.evidence_hash
                == browser_ops_bench_verification_report_write_evidence_hash(
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

impl BrowserOpsBenchVerificationReport {
    pub fn is_valid_for(&self, proof: &BrowserOpsBenchVerificationProof) -> bool {
        self.schema == "aegis-browser-ops-bench-verification-report-v1"
            && proof.is_valid()
            && self.scorecard_path == proof.scorecard_path
            && self.scorecard_file_hash == proof.scorecard_file_hash
            && self.suite_hash == proof.suite_hash
            && self.proof_hash == proof.proof_hash
            && self.verified_records_hash == proof.verified_records_hash
            && self.verified_task_count == proof.verified_task_count
            && self.verification_subject_id == proof.replay_subject_id()
            && (!self.replay_recorded
                || (self.run_id != 0
                    && self.verification_event_id > 0
                    && self
                        .verification_event_hash
                        .is_some_and(|hash| nonzero_hash(&hash))
                    && self
                        .replay_binding_hash
                        .is_some_and(|hash| nonzero_hash(&hash))))
            && self.report_hash
                == browser_ops_bench_verification_report_hash(
                    self.scorecard_file_hash,
                    self.suite_hash,
                    self.proof_hash,
                    self.verified_records_hash,
                    self.verified_task_count,
                    self.replay_recorded,
                    self.run_id,
                    self.verification_subject_id,
                    self.verification_event_id,
                    self.verification_event_hash,
                    self.replay_binding_hash,
                )
            && nonzero_hash(&self.report_hash)
    }

    pub fn write_artifact(&self, path: impl AsRef<Path>) -> Result<[u8; 32], &'static str> {
        if !nonzero_hash(&self.report_hash) {
            return Err("invalid browser ops bench verification report");
        }
        let body_payload = serde_json::to_vec_pretty(self)
            .map_err(|_| "failed to serialize browser ops bench verification report body")?;
        let write_evidence = BrowserOpsBenchVerificationReportWriteEvidence::for_logical_payload(
            body_payload.len() as u64,
            *blake3::hash(&body_payload).as_bytes(),
        );
        if !write_evidence.is_valid() {
            return Err("invalid browser ops bench verification report write evidence");
        }
        let artifact = BrowserOpsBenchVerificationReportArtifact {
            schema_version: 1,
            report: self.clone(),
            write_evidence,
        };
        let payload = serde_json::to_vec_pretty(&artifact)
            .map_err(|_| "failed to serialize browser ops bench verification report artifact")?;
        write_browser_ops_bench_verification_report_artifact(path.as_ref(), &payload)?;
        Ok(*blake3::hash(&payload).as_bytes())
    }
}

impl BrowserOpsBenchVerifiedTaskRecord {
    pub fn is_valid(&self) -> bool {
        self.run_id > 0
            && self.action_id > 0
            && self.sequence_number > 0
            && self.predicate_count > 0
            && nonzero_hash(&self.task_id_hash)
            && self.manifest.run_id == self.run_id
            && self.manifest.action_id == self.action_id
            && self.manifest.sequence_number == self.sequence_number
            && self.manifest.is_valid()
            && self.envelope.is_valid()
            && self.envelope.run_id == self.run_id
            && self.envelope.action_id == self.action_id
            && self.envelope.sequence_number == self.sequence_number
            && self.record_hash
                == browser_ops_bench_verified_task_record_hash(
                    self.task_id_hash,
                    self.manifest.manifest_hash,
                    self.envelope.artifact_manifest_hash,
                    self.predicate_count,
                )
            && nonzero_hash(&self.record_hash)
    }
}

impl BrowserArtifactRef {
    pub const DEFAULT_MAX_FILE_BYTES: u64 = DEFAULT_BROWSER_ARTIFACT_MAX_BYTES;

    pub fn new(
        kind: BrowserArtifactKind,
        byte_len: u64,
        content_hash: [u8; 32],
        storage_ref_hash: [u8; 32],
    ) -> Self {
        let artifact_ref_hash =
            browser_artifact_ref_hash(kind, byte_len, content_hash, storage_ref_hash);
        Self {
            kind,
            byte_len,
            content_hash,
            storage_ref_hash,
            artifact_ref_hash,
        }
    }

    pub fn from_hot_arena_handle(
        kind: BrowserArtifactKind,
        handle: &crate::hot_engine::EvidenceHandle,
    ) -> Result<Self, BrowserArtifactReadError> {
        handle
            .is_valid()
            .then_some(Self::new(
                kind,
                handle.byte_len,
                handle.artifact_hash,
                handle.storage_ref_hash,
            ))
            .ok_or(BrowserArtifactReadError::ArtifactMismatch)
    }

    pub fn from_file(
        kind: BrowserArtifactKind,
        path: impl AsRef<Path>,
    ) -> Result<Self, BrowserArtifactReadError> {
        Self::from_file_with_max_bytes(kind, path, DEFAULT_BROWSER_ARTIFACT_MAX_BYTES)
    }

    pub fn from_file_with_max_bytes(
        kind: BrowserArtifactKind,
        path: impl AsRef<Path>,
        max_bytes: u64,
    ) -> Result<Self, BrowserArtifactReadError> {
        read_browser_artifact_ref_from_file(kind, path.as_ref(), max_bytes)
    }

    pub fn from_file_path_ref(
        path_ref: &BrowserArtifactFilePathRef,
    ) -> Result<Self, BrowserArtifactReadError> {
        Self::from_file_path_ref_with_max_bytes(path_ref, DEFAULT_BROWSER_ARTIFACT_MAX_BYTES)
    }

    pub fn from_file_path_ref_with_max_bytes(
        path_ref: &BrowserArtifactFilePathRef,
        max_bytes: u64,
    ) -> Result<Self, BrowserArtifactReadError> {
        read_browser_artifact_ref_from_file_path_ref(path_ref, max_bytes)
    }

    pub fn verify_file(&self, path: impl AsRef<Path>) -> Result<(), BrowserArtifactReadError> {
        self.verify_file_with_max_bytes(path, DEFAULT_BROWSER_ARTIFACT_MAX_BYTES)
    }

    pub fn verify_file_with_max_bytes(
        &self,
        path: impl AsRef<Path>,
        max_bytes: u64,
    ) -> Result<(), BrowserArtifactReadError> {
        let fresh = Self::from_file_with_max_bytes(self.kind, path, max_bytes)?;
        (fresh == *self)
            .then_some(())
            .ok_or(BrowserArtifactReadError::ArtifactMismatch)
    }

    pub fn is_valid(&self) -> bool {
        self.byte_len > 0
            && nonzero_hash(&self.content_hash)
            && nonzero_hash(&self.storage_ref_hash)
            && self.artifact_ref_hash
                == browser_artifact_ref_hash(
                    self.kind,
                    self.byte_len,
                    self.content_hash,
                    self.storage_ref_hash,
                )
            && nonzero_hash(&self.artifact_ref_hash)
    }
}

impl BrowserCollectorEvidenceEnvelope {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        mut artifacts: Vec<BrowserArtifactRef>,
    ) -> Self {
        artifacts.sort_by_key(|artifact| artifact.kind);
        let artifact_manifest_hash = browser_raw_artifact_manifest_hash(&artifacts);
        let envelope_hash = browser_collector_evidence_envelope_hash(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifact_manifest_hash,
        );
        Self {
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifacts,
            artifact_manifest_hash,
            envelope_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.run_id > 0
            && self.action_id > 0
            && self.sequence_number > 0
            && self.observed_at_unix_ms > 0
            && nonzero_hash(&self.collector_config_hash)
            && nonzero_hash(&self.collector_capability_hash)
            && nonzero_hash(&self.browser_session_hash)
            && nonzero_hash(&self.redaction_policy_hash)
            && nonzero_hash(&self.policy_window_hash)
            && self.has_exact_required_artifacts()
            && self.artifact_manifest_hash == browser_raw_artifact_manifest_hash(&self.artifacts)
            && nonzero_hash(&self.artifact_manifest_hash)
            && self.envelope_hash
                == browser_collector_evidence_envelope_hash(
                    self.collector_kind,
                    self.run_id,
                    self.action_id,
                    self.sequence_number,
                    self.observed_at_unix_ms,
                    self.collector_config_hash,
                    self.collector_capability_hash,
                    self.browser_session_hash,
                    self.redaction_policy_hash,
                    self.policy_window_hash,
                    self.artifact_manifest_hash,
                )
            && nonzero_hash(&self.envelope_hash)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn from_file_paths<P: AsRef<Path>>(
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        artifact_paths: &[(BrowserArtifactKind, P)],
    ) -> Result<Self, BrowserArtifactReadError> {
        Self::from_file_paths_with_max_bytes(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifact_paths,
            DEFAULT_BROWSER_ARTIFACT_MAX_BYTES,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn from_file_paths_with_max_bytes<P: AsRef<Path>>(
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        artifact_paths: &[(BrowserArtifactKind, P)],
        max_bytes: u64,
    ) -> Result<Self, BrowserArtifactReadError> {
        let mut artifacts = Vec::with_capacity(artifact_paths.len());
        for (kind, path) in artifact_paths {
            artifacts.push(BrowserArtifactRef::from_file_with_max_bytes(
                *kind,
                path.as_ref(),
                max_bytes,
            )?);
        }
        Ok(Self::new(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifacts,
        ))
    }

    pub fn verify_file_paths<P: AsRef<Path>>(
        &self,
        artifact_paths: &[(BrowserArtifactKind, P)],
    ) -> Result<(), BrowserArtifactReadError> {
        self.verify_file_paths_with_max_bytes(artifact_paths, DEFAULT_BROWSER_ARTIFACT_MAX_BYTES)
    }

    pub fn verify_file_paths_with_max_bytes<P: AsRef<Path>>(
        &self,
        artifact_paths: &[(BrowserArtifactKind, P)],
        max_bytes: u64,
    ) -> Result<(), BrowserArtifactReadError> {
        let fresh = Self::from_file_paths_with_max_bytes(
            self.collector_kind,
            self.run_id,
            self.action_id,
            self.sequence_number,
            self.observed_at_unix_ms,
            self.collector_config_hash,
            self.collector_capability_hash,
            self.browser_session_hash,
            self.redaction_policy_hash,
            self.policy_window_hash,
            artifact_paths,
            max_bytes,
        )?;
        (fresh == *self)
            .then_some(())
            .ok_or(BrowserArtifactReadError::ArtifactMismatch)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn from_file_path_refs(
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        artifact_path_refs: &[BrowserArtifactFilePathRef],
    ) -> Result<Self, BrowserArtifactReadError> {
        Self::from_file_path_refs_with_max_bytes(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifact_path_refs,
            DEFAULT_BROWSER_ARTIFACT_MAX_BYTES,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn from_file_path_refs_with_max_bytes(
        collector_kind: BrowserCollectorKind,
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        artifact_path_refs: &[BrowserArtifactFilePathRef],
        max_bytes: u64,
    ) -> Result<Self, BrowserArtifactReadError> {
        let mut artifacts = Vec::with_capacity(artifact_path_refs.len());
        for path_ref in artifact_path_refs {
            artifacts.push(BrowserArtifactRef::from_file_path_ref_with_max_bytes(
                path_ref, max_bytes,
            )?);
        }
        Ok(Self::new(
            collector_kind,
            run_id,
            action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            browser_session_hash,
            redaction_policy_hash,
            policy_window_hash,
            artifacts,
        ))
    }

    pub fn verify_file_path_refs(
        &self,
        artifact_path_refs: &[BrowserArtifactFilePathRef],
    ) -> Result<(), BrowserArtifactReadError> {
        self.verify_file_path_refs_with_max_bytes(
            artifact_path_refs,
            DEFAULT_BROWSER_ARTIFACT_MAX_BYTES,
        )
    }

    pub fn verify_file_path_refs_with_max_bytes(
        &self,
        artifact_path_refs: &[BrowserArtifactFilePathRef],
        max_bytes: u64,
    ) -> Result<(), BrowserArtifactReadError> {
        let fresh = Self::from_file_path_refs_with_max_bytes(
            self.collector_kind,
            self.run_id,
            self.action_id,
            self.sequence_number,
            self.observed_at_unix_ms,
            self.collector_config_hash,
            self.collector_capability_hash,
            self.browser_session_hash,
            self.redaction_policy_hash,
            self.policy_window_hash,
            artifact_path_refs,
            max_bytes,
        )?;
        (fresh == *self)
            .then_some(())
            .ok_or(BrowserArtifactReadError::ArtifactMismatch)
    }

    pub fn content_hash_for(&self, kind: BrowserArtifactKind) -> Option<[u8; 32]> {
        self.is_valid().then(|| {
            self.artifacts
                .iter()
                .find(|artifact| artifact.kind == kind)
                .map(|artifact| artifact.content_hash)
        })?
    }

    pub fn to_observation_packet(
        &self,
        action_trace: BrowserActionTrace,
        side_effect_class: SideEffectClass,
    ) -> Option<BrowserObservationPacket> {
        if !self.is_valid()
            || !action_trace.is_valid()
            || action_trace.action_id != self.action_id
            || action_trace.policy_window_hash != self.policy_window_hash
        {
            return None;
        }
        let proof = BrowserWitnessProof::new(
            self.run_id,
            self.action_id,
            side_effect_class,
            self.content_hash_for_unchecked(BrowserArtifactKind::UrlBefore)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::UrlAfter)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::DomSnapshotBefore)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::DomSnapshotAfter)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::ScreenshotBefore)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::ScreenshotAfter)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::AccessibilityTreeAfter)?,
            self.content_hash_for_unchecked(BrowserArtifactKind::NetworkLog)?,
            action_trace.trace_hash,
            self.policy_window_hash,
            self.browser_session_hash,
            self.redaction_policy_hash,
        );
        let packet = BrowserObservationPacket::new(
            self.collector_kind,
            self.sequence_number,
            self.observed_at_unix_ms,
            self.collector_config_hash,
            self.collector_capability_hash,
            self.artifact_manifest_hash,
            action_trace,
            proof,
        );
        packet.is_valid().then_some(packet)
    }

    fn has_exact_required_artifacts(&self) -> bool {
        self.artifacts.len() == REQUIRED_BROWSER_ARTIFACT_KIND_COUNT
            && self.artifacts.iter().enumerate().all(|(index, artifact)| {
                artifact.is_valid() && artifact.kind as u8 == (index as u8 + 1)
            })
    }

    fn content_hash_for_unchecked(&self, kind: BrowserArtifactKind) -> Option<[u8; 32]> {
        self.artifacts
            .iter()
            .find(|artifact| artifact.kind == kind)
            .map(|artifact| artifact.content_hash)
    }
}

impl BrowserObservationPacket {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        collector_kind: BrowserCollectorKind,
        sequence_number: u64,
        observed_at_unix_ms: u64,
        collector_config_hash: [u8; 32],
        collector_capability_hash: [u8; 32],
        raw_artifact_manifest_hash: [u8; 32],
        action_trace: BrowserActionTrace,
        proof: BrowserWitnessProof,
    ) -> Self {
        let modality_bundle_hash = browser_modality_bundle_hash(
            proof.url_before_hash,
            proof.url_after_hash,
            proof.dom_snapshot_hash_before,
            proof.dom_snapshot_hash_after,
            proof.screenshot_hash_before,
            proof.screenshot_hash_after,
            proof.accessibility_tree_hash_after,
            proof.network_log_hash,
            raw_artifact_manifest_hash,
        );
        let collector_provenance_hash = browser_collector_provenance_hash(
            collector_kind,
            proof.run_id,
            proof.action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            raw_artifact_manifest_hash,
            proof.browser_session_hash,
            proof.redaction_policy_hash,
            proof.policy_window_hash,
        );
        let packet_hash = browser_observation_packet_hash(
            modality_bundle_hash,
            collector_provenance_hash,
            action_trace.trace_hash,
            proof.proof_hash,
        );
        Self {
            collector_kind,
            run_id: proof.run_id,
            action_id: proof.action_id,
            sequence_number,
            observed_at_unix_ms,
            collector_config_hash,
            collector_capability_hash,
            raw_artifact_manifest_hash,
            action_trace,
            proof,
            modality_bundle_hash,
            collector_provenance_hash,
            packet_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.run_id == self.proof.run_id
            && self.action_id == self.proof.action_id
            && self.sequence_number > 0
            && self.observed_at_unix_ms > 0
            && nonzero_hash(&self.collector_config_hash)
            && nonzero_hash(&self.collector_capability_hash)
            && nonzero_hash(&self.raw_artifact_manifest_hash)
            && self.proof.is_valid_staging_evidence_for(
                &self.action_trace,
                self.action_trace.policy_window_hash,
            )
            && self.modality_bundle_hash
                == browser_modality_bundle_hash(
                    self.proof.url_before_hash,
                    self.proof.url_after_hash,
                    self.proof.dom_snapshot_hash_before,
                    self.proof.dom_snapshot_hash_after,
                    self.proof.screenshot_hash_before,
                    self.proof.screenshot_hash_after,
                    self.proof.accessibility_tree_hash_after,
                    self.proof.network_log_hash,
                    self.raw_artifact_manifest_hash,
                )
            && self.collector_provenance_hash
                == browser_collector_provenance_hash(
                    self.collector_kind,
                    self.run_id,
                    self.action_id,
                    self.sequence_number,
                    self.observed_at_unix_ms,
                    self.collector_config_hash,
                    self.collector_capability_hash,
                    self.raw_artifact_manifest_hash,
                    self.proof.browser_session_hash,
                    self.proof.redaction_policy_hash,
                    self.proof.policy_window_hash,
                )
            && self.packet_hash
                == browser_observation_packet_hash(
                    self.modality_bundle_hash,
                    self.collector_provenance_hash,
                    self.action_trace.trace_hash,
                    self.proof.proof_hash,
                )
    }

    pub fn is_valid_staging_packet_for(
        &self,
        expected_policy_window_hash: [u8; 32],
        expected_browser_session_hash: [u8; 32],
        expected_redaction_policy_hash: [u8; 32],
    ) -> bool {
        self.is_valid()
            && nonzero_hash(&expected_policy_window_hash)
            && nonzero_hash(&expected_browser_session_hash)
            && nonzero_hash(&expected_redaction_policy_hash)
            && self.proof.policy_window_hash == expected_policy_window_hash
            && self.proof.browser_session_hash == expected_browser_session_hash
            && self.proof.redaction_policy_hash == expected_redaction_policy_hash
    }

    pub fn isolated_browser_policy_facts(
        &self,
        policy_version_hash: [u8; 32],
        has_valid_approval: bool,
        expected_policy_window_hash: [u8; 32],
        expected_browser_session_hash: [u8; 32],
        expected_redaction_policy_hash: [u8; 32],
    ) -> Option<PolicyFacts> {
        (nonzero_hash(&policy_version_hash)
            && self.is_valid_staging_packet_for(
                expected_policy_window_hash,
                expected_browser_session_hash,
                expected_redaction_policy_hash,
            ))
        .then(|| {
            PolicyFacts::with_staging_evidence(
                policy_version_hash,
                has_valid_approval,
                self.packet_hash,
                StagingEvidenceKind::IsolatedBrowserSession,
            )
        })
    }

    pub fn from_collector_envelope(
        envelope: &BrowserCollectorEvidenceEnvelope,
        action_trace: BrowserActionTrace,
        side_effect_class: SideEffectClass,
    ) -> Option<Self> {
        envelope.to_observation_packet(action_trace, side_effect_class)
    }

    pub fn page_search_candidates(
        &self,
        pattern: &str,
        case_sensitive: bool,
        regex: bool,
        match_count: u32,
        limit: usize,
    ) -> Result<BrowserPageSearchCandidateProof, BrowserPageSearchCandidateError> {
        if !self.is_valid() {
            return Err(BrowserPageSearchCandidateError::InvalidPacket);
        }
        if self.action_trace.action_kind != BrowserActionKind::SearchPage {
            return Err(BrowserPageSearchCandidateError::InvalidPacket);
        }
        if limit == 0 {
            return Err(BrowserPageSearchCandidateError::InvalidPattern);
        }
        if match_count == 0 {
            return Err(BrowserPageSearchCandidateError::NoMatches);
        }
        let pattern_hash = browser_page_search_pattern_hash(pattern, case_sensitive, regex)
            .ok_or(BrowserPageSearchCandidateError::InvalidPattern)?;
        let dom_artifact_hash = self.proof.dom_snapshot_hash_after;
        if !nonzero_hash(&dom_artifact_hash) {
            return Err(BrowserPageSearchCandidateError::InvalidDomArtifact);
        }
        let candidate_count = (match_count as usize).min(limit);
        let mut candidates = Vec::with_capacity(candidate_count);
        for ordinal in 0..candidate_count {
            let evidence_ref_hash = browser_page_search_evidence_ref_hash(
                self.packet_hash,
                dom_artifact_hash,
                pattern_hash,
                ordinal.saturating_add(1).min(u32::MAX as usize) as u32,
            );
            candidates.push(CandidateEvidenceRef::new(
                evidence_ref_hash,
                self.action_id.saturating_add(ordinal as u128) as u64,
                EvidenceCandidateTier::BrowserPageSearch,
                browser_page_search_score(match_count, ordinal),
                self.packet_hash,
            ));
        }
        let record = BrowserPageSearchCandidateRecord::new(
            self.packet_hash,
            dom_artifact_hash,
            pattern_hash,
            self.packet_hash,
            limit,
            match_count,
            &candidates,
        )?;
        if record.record_hash != browser_page_search_candidate_record_hash(&record)
            || !record.matches_candidates(&candidates)
        {
            return Err(BrowserPageSearchCandidateError::InvalidCandidate);
        }
        Ok(BrowserPageSearchCandidateProof { record, candidates })
    }
}

impl BrowserWitnessProof {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        run_id: BrowserRunId,
        action_id: BrowserActionId,
        side_effect_class: SideEffectClass,
        url_before_hash: [u8; 32],
        url_after_hash: [u8; 32],
        dom_snapshot_hash_before: [u8; 32],
        dom_snapshot_hash_after: [u8; 32],
        screenshot_hash_before: [u8; 32],
        screenshot_hash_after: [u8; 32],
        accessibility_tree_hash_after: [u8; 32],
        network_log_hash: [u8; 32],
        action_trace_hash: [u8; 32],
        policy_window_hash: [u8; 32],
        browser_session_hash: [u8; 32],
        redaction_policy_hash: [u8; 32],
    ) -> Self {
        let proof_hash = browser_witness_proof_hash(
            run_id,
            action_id,
            side_effect_class,
            url_before_hash,
            url_after_hash,
            dom_snapshot_hash_before,
            dom_snapshot_hash_after,
            screenshot_hash_before,
            screenshot_hash_after,
            accessibility_tree_hash_after,
            network_log_hash,
            action_trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
        );
        Self {
            run_id,
            action_id,
            side_effect_class,
            url_before_hash,
            url_after_hash,
            dom_snapshot_hash_before,
            dom_snapshot_hash_after,
            screenshot_hash_before,
            screenshot_hash_after,
            accessibility_tree_hash_after,
            network_log_hash,
            action_trace_hash,
            policy_window_hash,
            browser_session_hash,
            redaction_policy_hash,
            proof_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.run_id > 0
            && self.action_id > 0
            && nonzero_hash(&self.url_before_hash)
            && nonzero_hash(&self.url_after_hash)
            && nonzero_hash(&self.dom_snapshot_hash_before)
            && nonzero_hash(&self.dom_snapshot_hash_after)
            && nonzero_hash(&self.screenshot_hash_before)
            && nonzero_hash(&self.screenshot_hash_after)
            && nonzero_hash(&self.accessibility_tree_hash_after)
            && nonzero_hash(&self.network_log_hash)
            && nonzero_hash(&self.action_trace_hash)
            && nonzero_hash(&self.policy_window_hash)
            && nonzero_hash(&self.browser_session_hash)
            && nonzero_hash(&self.redaction_policy_hash)
            && self.proof_hash
                == browser_witness_proof_hash(
                    self.run_id,
                    self.action_id,
                    self.side_effect_class,
                    self.url_before_hash,
                    self.url_after_hash,
                    self.dom_snapshot_hash_before,
                    self.dom_snapshot_hash_after,
                    self.screenshot_hash_before,
                    self.screenshot_hash_after,
                    self.accessibility_tree_hash_after,
                    self.network_log_hash,
                    self.action_trace_hash,
                    self.policy_window_hash,
                    self.browser_session_hash,
                    self.redaction_policy_hash,
                )
    }

    pub fn binds_action_trace(&self, action: &BrowserActionTrace) -> bool {
        self.is_valid()
            && action.is_valid()
            && self.action_id == action.action_id
            && self.action_trace_hash == action.trace_hash
            && self.policy_window_hash == action.policy_window_hash
    }

    pub fn is_valid_staging_evidence_for(
        &self,
        action: &BrowserActionTrace,
        expected_policy_window_hash: [u8; 32],
    ) -> bool {
        self.binds_action_trace(action)
            && nonzero_hash(&expected_policy_window_hash)
            && self.policy_window_hash == expected_policy_window_hash
    }
}

#[allow(clippy::too_many_arguments)]
fn browser_modality_bundle_hash(
    url_before_hash: [u8; 32],
    url_after_hash: [u8; 32],
    dom_snapshot_hash_before: [u8; 32],
    dom_snapshot_hash_after: [u8; 32],
    screenshot_hash_before: [u8; 32],
    screenshot_hash_after: [u8; 32],
    accessibility_tree_hash_after: [u8; 32],
    network_log_hash: [u8; 32],
    raw_artifact_manifest_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-modality-bundle-v1");
    hasher.update(&url_before_hash);
    hasher.update(&url_after_hash);
    hasher.update(&dom_snapshot_hash_before);
    hasher.update(&dom_snapshot_hash_after);
    hasher.update(&screenshot_hash_before);
    hasher.update(&screenshot_hash_after);
    hasher.update(&accessibility_tree_hash_after);
    hasher.update(&network_log_hash);
    hasher.update(&raw_artifact_manifest_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn browser_collector_provenance_hash(
    collector_kind: BrowserCollectorKind,
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    sequence_number: u64,
    observed_at_unix_ms: u64,
    collector_config_hash: [u8; 32],
    collector_capability_hash: [u8; 32],
    raw_artifact_manifest_hash: [u8; 32],
    browser_session_hash: [u8; 32],
    redaction_policy_hash: [u8; 32],
    policy_window_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-collector-provenance-v1");
    update_u8(&mut hasher, collector_kind as u8);
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, action_id);
    update_u64(&mut hasher, sequence_number);
    update_u64(&mut hasher, observed_at_unix_ms);
    hasher.update(&collector_config_hash);
    hasher.update(&collector_capability_hash);
    hasher.update(&raw_artifact_manifest_hash);
    hasher.update(&browser_session_hash);
    hasher.update(&redaction_policy_hash);
    hasher.update(&policy_window_hash);
    *hasher.finalize().as_bytes()
}

fn browser_observation_packet_hash(
    modality_bundle_hash: [u8; 32],
    collector_provenance_hash: [u8; 32],
    action_trace_hash: [u8; 32],
    proof_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-observation-packet-v1");
    hasher.update(&modality_bundle_hash);
    hasher.update(&collector_provenance_hash);
    hasher.update(&action_trace_hash);
    hasher.update(&proof_hash);
    *hasher.finalize().as_bytes()
}

fn browser_artifact_ref_hash(
    kind: BrowserArtifactKind,
    byte_len: u64,
    content_hash: [u8; 32],
    storage_ref_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-artifact-ref-v1");
    update_u8(&mut hasher, kind as u8);
    update_u64(&mut hasher, byte_len);
    hasher.update(&content_hash);
    hasher.update(&storage_ref_hash);
    *hasher.finalize().as_bytes()
}

fn read_browser_artifact_ref_from_file(
    kind: BrowserArtifactKind,
    path: &Path,
    max_bytes: u64,
) -> Result<BrowserArtifactRef, BrowserArtifactReadError> {
    if max_bytes == 0 {
        return Err(BrowserArtifactReadError::InvalidMaxBytes);
    }
    let metadata = fs::metadata(path).map_err(|_| BrowserArtifactReadError::Missing)?;
    if !metadata.is_file() {
        return Err(BrowserArtifactReadError::NotFile);
    }
    let byte_len = metadata.len();
    if byte_len == 0 {
        return Err(BrowserArtifactReadError::Empty);
    }
    if byte_len > max_bytes {
        return Err(BrowserArtifactReadError::Oversized);
    }
    let canonical_path =
        fs::canonicalize(path).map_err(|_| BrowserArtifactReadError::PathCanonicalizationFailed)?;
    let content_hash = browser_file_content_hash(&canonical_path, byte_len, max_bytes)?;
    let storage_ref_hash =
        browser_file_storage_ref_hash(kind, &canonical_path, byte_len, content_hash);
    Ok(BrowserArtifactRef::new(
        kind,
        byte_len,
        content_hash,
        storage_ref_hash,
    ))
}

fn canonical_browser_artifact_file_path_ref(
    kind: BrowserArtifactKind,
    path: &Path,
    max_bytes: u64,
) -> Result<BrowserArtifactFilePathRef, BrowserArtifactReadError> {
    if max_bytes == 0 {
        return Err(BrowserArtifactReadError::InvalidMaxBytes);
    }
    let metadata = fs::metadata(path).map_err(|_| BrowserArtifactReadError::Missing)?;
    if !metadata.is_file() {
        return Err(BrowserArtifactReadError::NotFile);
    }
    let byte_len = metadata.len();
    if byte_len == 0 {
        return Err(BrowserArtifactReadError::Empty);
    }
    if byte_len > max_bytes {
        return Err(BrowserArtifactReadError::Oversized);
    }
    let canonical_path =
        fs::canonicalize(path).map_err(|_| BrowserArtifactReadError::PathCanonicalizationFailed)?;
    let normalized_path_hash = browser_file_normalized_path_hash(kind, &canonical_path);
    let path_ref_hash = browser_file_path_ref_hash(kind, normalized_path_hash);
    Ok(BrowserArtifactFilePathRef {
        kind,
        canonical_path,
        normalized_path_hash,
        path_ref_hash,
    })
}

fn read_browser_artifact_ref_from_file_path_ref(
    path_ref: &BrowserArtifactFilePathRef,
    max_bytes: u64,
) -> Result<BrowserArtifactRef, BrowserArtifactReadError> {
    if max_bytes == 0 {
        return Err(BrowserArtifactReadError::InvalidMaxBytes);
    }
    if !path_ref.is_valid() {
        return Err(BrowserArtifactReadError::PathCanonicalizationFailed);
    }
    let mut file = fs::File::open(&path_ref.canonical_path)
        .map_err(|_| BrowserArtifactReadError::ReadFailed)?;
    let metadata = file
        .metadata()
        .map_err(|_| BrowserArtifactReadError::ReadFailed)?;
    if !metadata.is_file() {
        return Err(BrowserArtifactReadError::NotFile);
    }
    let byte_len = metadata.len();
    if byte_len == 0 {
        return Err(BrowserArtifactReadError::Empty);
    }
    if byte_len > max_bytes {
        return Err(BrowserArtifactReadError::Oversized);
    }
    let content_hash = browser_file_content_hash_from_open_file(&mut file, byte_len, max_bytes)?;
    let storage_ref_hash = browser_file_storage_ref_hash(
        path_ref.kind,
        &path_ref.canonical_path,
        byte_len,
        content_hash,
    );
    Ok(BrowserArtifactRef::new(
        path_ref.kind,
        byte_len,
        content_hash,
        storage_ref_hash,
    ))
}

fn browser_file_content_hash(
    canonical_path: &Path,
    expected_len: u64,
    max_bytes: u64,
) -> Result<[u8; 32], BrowserArtifactReadError> {
    let mut file =
        fs::File::open(canonical_path).map_err(|_| BrowserArtifactReadError::ReadFailed)?;
    let hash = browser_file_content_hash_from_open_file(&mut file, expected_len, max_bytes)?;
    let post_len = fs::metadata(canonical_path)
        .map_err(|_| BrowserArtifactReadError::Missing)?
        .len();
    if post_len != expected_len {
        return Err(BrowserArtifactReadError::ModifiedDuringRead);
    }
    Ok(hash)
}

fn browser_file_content_hash_from_open_file(
    file: &mut fs::File,
    expected_len: u64,
    max_bytes: u64,
) -> Result<[u8; 32], BrowserArtifactReadError> {
    let mut total = 0u64;
    let mut buffer = [0u8; 8192];
    let mut hasher = Hasher::new();
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|_| BrowserArtifactReadError::ReadFailed)?;
        if read == 0 {
            break;
        }
        total = total
            .checked_add(read as u64)
            .ok_or(BrowserArtifactReadError::Oversized)?;
        if total > max_bytes {
            return Err(BrowserArtifactReadError::Oversized);
        }
        hasher.update(&buffer[..read]);
    }
    if total != expected_len {
        return Err(BrowserArtifactReadError::ModifiedDuringRead);
    }
    Ok(*hasher.finalize().as_bytes())
}

fn browser_file_storage_ref_hash(
    kind: BrowserArtifactKind,
    canonical_path: &Path,
    byte_len: u64,
    content_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-file-storage-ref-v1");
    update_u8(&mut hasher, kind as u8);
    update_u64(&mut hasher, byte_len);
    hasher.update(&content_hash);
    let normalized_path = normalize_browser_artifact_path(canonical_path);
    update_u64(&mut hasher, normalized_path.len() as u64);
    hasher.update(normalized_path.as_bytes());
    *hasher.finalize().as_bytes()
}

fn browser_file_normalized_path_hash(kind: BrowserArtifactKind, canonical_path: &Path) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-file-normalized-path-v1");
    update_u8(&mut hasher, kind as u8);
    let normalized_path = normalize_browser_artifact_path(canonical_path);
    update_u64(&mut hasher, normalized_path.len() as u64);
    hasher.update(normalized_path.as_bytes());
    *hasher.finalize().as_bytes()
}

fn browser_file_path_ref_hash(
    kind: BrowserArtifactKind,
    normalized_path_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-file-path-ref-v1");
    update_u8(&mut hasher, kind as u8);
    hasher.update(&normalized_path_hash);
    *hasher.finalize().as_bytes()
}

fn required_live_collector_artifacts(
    artifacts: &[BrowserLiveCollectorArtifact],
    max_bytes: u64,
) -> Result<Vec<BrowserLiveCollectorArtifact>, BrowserLiveCollectorRunError> {
    if artifacts.len() != REQUIRED_BROWSER_ARTIFACT_KIND_COUNT {
        return Err(BrowserLiveCollectorRunError::MissingRequiredArtifact);
    }
    let mut ordered = Vec::with_capacity(REQUIRED_BROWSER_ARTIFACT_KIND_COUNT);
    for required_kind in REQUIRED_BROWSER_ARTIFACT_KINDS {
        let mut matches = artifacts
            .iter()
            .filter(|artifact| artifact.kind == required_kind);
        let artifact = matches
            .next()
            .ok_or(BrowserLiveCollectorRunError::MissingRequiredArtifact)?;
        if matches.next().is_some() {
            return Err(BrowserLiveCollectorRunError::DuplicateArtifactKind);
        }
        if artifact.bytes.is_empty() {
            return Err(BrowserLiveCollectorRunError::EmptyArtifact);
        }
        if artifact.bytes.len() as u64 > max_bytes {
            return Err(BrowserLiveCollectorRunError::OversizedArtifact);
        }
        ordered.push(artifact.clone());
    }
    Ok(ordered)
}

fn write_browser_live_collector_artifact(
    path: &Path,
    bytes: &[u8],
    max_bytes: u64,
) -> Result<(), BrowserLiveCollectorRunError> {
    if bytes.is_empty() {
        return Err(BrowserLiveCollectorRunError::EmptyArtifact);
    }
    if bytes.len() as u64 > max_bytes {
        return Err(BrowserLiveCollectorRunError::OversizedArtifact);
    }
    let parent = path
        .parent()
        .ok_or(BrowserLiveCollectorRunError::InvalidOutputDirectory)?;
    fs::create_dir_all(parent).map_err(|_| BrowserLiveCollectorRunError::WriteFailed)?;
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("artifact")
    ));
    {
        let mut file =
            fs::File::create(&temp_path).map_err(|_| BrowserLiveCollectorRunError::WriteFailed)?;
        file.write_all(bytes)
            .map_err(|_| BrowserLiveCollectorRunError::WriteFailed)?;
        file.sync_all()
            .map_err(|_| BrowserLiveCollectorRunError::WriteFailed)?;
    }
    fs::rename(&temp_path, path).map_err(|_| BrowserLiveCollectorRunError::WriteFailed)?;
    Ok(())
}

fn write_browser_ops_bench_verification_report_artifact(
    path: &Path,
    payload: &[u8],
) -> Result<(), &'static str> {
    if path.as_os_str().is_empty() || payload.is_empty() {
        return Err("invalid browser ops bench verification report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)
            .map_err(|_| "failed to create browser ops bench report directory")?;
    }
    let temp_path = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    {
        let mut file = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
            .map_err(|_| "failed to create browser ops bench report temp file")?;
        file.write_all(payload)
            .map_err(|_| "failed to write browser ops bench report temp file")?;
        file.flush()
            .map_err(|_| "failed to flush browser ops bench report temp file")?;
        file.sync_all()
            .map_err(|_| "failed to sync browser ops bench report temp file")?;
    }
    if let Err(_error) = fs::rename(&temp_path, path) {
        let _ = fs::remove_file(&temp_path);
        return Err("failed to publish browser ops bench report artifact");
    }
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        let _ = fs::File::open(parent).and_then(|directory| directory.sync_all());
    }
    Ok(())
}

fn browser_live_collector_run_directory_name(
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    sequence_number: u64,
) -> String {
    format!("run-{run_id:032x}-action-{action_id:032x}-seq-{sequence_number:016x}")
}

fn browser_artifact_file_name(kind: BrowserArtifactKind) -> &'static str {
    match kind {
        BrowserArtifactKind::UrlBefore => "01-url-before.txt",
        BrowserArtifactKind::UrlAfter => "02-url-after.txt",
        BrowserArtifactKind::DomSnapshotBefore => "03-dom-before.html",
        BrowserArtifactKind::DomSnapshotAfter => "04-dom-after.html",
        BrowserArtifactKind::ScreenshotBefore => "05-screenshot-before.bin",
        BrowserArtifactKind::ScreenshotAfter => "06-screenshot-after.bin",
        BrowserArtifactKind::AccessibilityTreeAfter => "07-accessibility-after.json",
        BrowserArtifactKind::NetworkLog => "08-network-log.jsonl",
    }
}

fn browser_artifact_kind_from_scorecard(kind: &str) -> Option<BrowserArtifactKind> {
    match kind {
        "url_before" => Some(BrowserArtifactKind::UrlBefore),
        "url_after" => Some(BrowserArtifactKind::UrlAfter),
        "dom_snapshot_before" => Some(BrowserArtifactKind::DomSnapshotBefore),
        "dom_snapshot_after" => Some(BrowserArtifactKind::DomSnapshotAfter),
        "screenshot_before" => Some(BrowserArtifactKind::ScreenshotBefore),
        "screenshot_after" => Some(BrowserArtifactKind::ScreenshotAfter),
        "accessibility_tree_after" => Some(BrowserArtifactKind::AccessibilityTreeAfter),
        "network_log" => Some(BrowserArtifactKind::NetworkLog),
        _ => None,
    }
}

fn browser_ops_bench_path_refs(
    artifact_paths: &BTreeMap<String, String>,
) -> Result<Vec<BrowserArtifactFilePathRef>, BrowserOpsBenchVerificationError> {
    if artifact_paths.len() != REQUIRED_BROWSER_ARTIFACT_KIND_COUNT {
        return Err(BrowserOpsBenchVerificationError::InvalidProducerMetadata);
    }
    let mut refs = Vec::with_capacity(REQUIRED_BROWSER_ARTIFACT_KIND_COUNT);
    for required_kind in REQUIRED_BROWSER_ARTIFACT_KINDS {
        let scorecard_kind = browser_artifact_kind_scorecard_name(required_kind);
        let path = artifact_paths
            .get(scorecard_kind)
            .ok_or(BrowserOpsBenchVerificationError::InvalidProducerMetadata)?;
        refs.push(
            BrowserArtifactFilePathRef::from_path(required_kind, path)
                .map_err(BrowserOpsBenchVerificationError::ArtifactRead)?,
        );
    }
    Ok(refs)
}

fn browser_artifact_kind_scorecard_name(kind: BrowserArtifactKind) -> &'static str {
    match kind {
        BrowserArtifactKind::UrlBefore => "url_before",
        BrowserArtifactKind::UrlAfter => "url_after",
        BrowserArtifactKind::DomSnapshotBefore => "dom_snapshot_before",
        BrowserArtifactKind::DomSnapshotAfter => "dom_snapshot_after",
        BrowserArtifactKind::ScreenshotBefore => "screenshot_before",
        BrowserArtifactKind::ScreenshotAfter => "screenshot_after",
        BrowserArtifactKind::AccessibilityTreeAfter => "accessibility_tree_after",
        BrowserArtifactKind::NetworkLog => "network_log",
    }
}

pub fn browser_live_collector_run_hash(
    manifest_hash: [u8; 32],
    initial_artifact_manifest_hash: [u8; 32],
    run_directory: &Path,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-live-collector-run-v1");
    hasher.update(&manifest_hash);
    hasher.update(&initial_artifact_manifest_hash);
    let normalized_path = normalize_browser_artifact_path(run_directory);
    update_u64(&mut hasher, normalized_path.len() as u64);
    hasher.update(normalized_path.as_bytes());
    *hasher.finalize().as_bytes()
}

fn browser_ops_bench_verified_task_record_hash(
    task_id_hash: [u8; 32],
    manifest_hash: [u8; 32],
    artifact_manifest_hash: [u8; 32],
    predicate_count: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-ops-bench-task-record-v1");
    hasher.update(&task_id_hash);
    hasher.update(&manifest_hash);
    hasher.update(&artifact_manifest_hash);
    update_u64(&mut hasher, predicate_count);
    *hasher.finalize().as_bytes()
}

fn browser_ops_bench_verified_records_hash(
    records: &[BrowserOpsBenchVerifiedTaskRecord],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-ops-bench-records-v1");
    update_u64(&mut hasher, records.len() as u64);
    for record in records {
        hasher.update(&record.record_hash);
    }
    *hasher.finalize().as_bytes()
}

fn browser_ops_bench_verification_proof_hash(
    suite_hash: [u8; 32],
    scorecard_file_hash: [u8; 32],
    verified_records_hash: [u8; 32],
    verified_task_count: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-ops-bench-verification-proof-v1");
    hasher.update(&suite_hash);
    hasher.update(&scorecard_file_hash);
    hasher.update(&verified_records_hash);
    update_u64(&mut hasher, verified_task_count);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn browser_ops_bench_verification_report_hash(
    scorecard_file_hash: [u8; 32],
    suite_hash: [u8; 32],
    proof_hash: [u8; 32],
    verified_records_hash: [u8; 32],
    verified_task_count: u64,
    replay_recorded: bool,
    run_id: u128,
    verification_subject_id: u128,
    verification_event_id: u64,
    verification_event_hash: Option<[u8; 32]>,
    replay_binding_hash: Option<[u8; 32]>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-ops-bench-verification-report-v1");
    hasher.update(&scorecard_file_hash);
    hasher.update(&suite_hash);
    hasher.update(&proof_hash);
    hasher.update(&verified_records_hash);
    update_u64(&mut hasher, verified_task_count);
    update_u8(&mut hasher, u8::from(replay_recorded));
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, verification_subject_id);
    update_u64(&mut hasher, verification_event_id);
    hasher.update(&verification_event_hash.unwrap_or([0; 32]));
    hasher.update(&replay_binding_hash.unwrap_or([0; 32]));
    *hasher.finalize().as_bytes()
}

fn browser_ops_bench_verification_report_write_evidence_hash(
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
    hasher.update(b"aegis-browser-ops-bench-verification-report-write-v1");
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

#[allow(clippy::too_many_arguments)]
pub fn browser_live_collector_manifest_hash(
    collector_kind: BrowserCollectorKind,
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    sequence_number: u64,
    observed_at_unix_ms: u64,
    collector_config_hash: [u8; 32],
    collector_capability_hash: [u8; 32],
    browser_session_hash: [u8; 32],
    redaction_policy_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    artifact_path_refs: &[BrowserArtifactFilePathRef],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-live-collector-manifest-v1");
    update_u8(&mut hasher, collector_kind as u8);
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, action_id);
    update_u64(&mut hasher, sequence_number);
    update_u64(&mut hasher, observed_at_unix_ms);
    hasher.update(&collector_config_hash);
    hasher.update(&collector_capability_hash);
    hasher.update(&browser_session_hash);
    hasher.update(&redaction_policy_hash);
    hasher.update(&policy_window_hash);
    update_u64(&mut hasher, artifact_path_refs.len() as u64);
    for path_ref in artifact_path_refs {
        update_u8(&mut hasher, path_ref.kind as u8);
        hasher.update(&path_ref.path_ref_hash);
    }
    *hasher.finalize().as_bytes()
}

fn normalize_browser_artifact_path(path: &Path) -> String {
    let path_string = path.to_string_lossy().replace('\\', "/");
    if cfg!(windows) {
        path_string.to_ascii_lowercase()
    } else {
        path_string
    }
}

fn browser_raw_artifact_manifest_hash(artifacts: &[BrowserArtifactRef]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-raw-artifact-manifest-v1");
    update_u64(&mut hasher, artifacts.len() as u64);
    for artifact in artifacts {
        hasher.update(&artifact.artifact_ref_hash);
    }
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn browser_collector_evidence_envelope_hash(
    collector_kind: BrowserCollectorKind,
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    sequence_number: u64,
    observed_at_unix_ms: u64,
    collector_config_hash: [u8; 32],
    collector_capability_hash: [u8; 32],
    browser_session_hash: [u8; 32],
    redaction_policy_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    artifact_manifest_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-collector-evidence-envelope-v1");
    update_u8(&mut hasher, collector_kind as u8);
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, action_id);
    update_u64(&mut hasher, sequence_number);
    update_u64(&mut hasher, observed_at_unix_ms);
    hasher.update(&collector_config_hash);
    hasher.update(&collector_capability_hash);
    hasher.update(&browser_session_hash);
    hasher.update(&redaction_policy_hash);
    hasher.update(&policy_window_hash);
    hasher.update(&artifact_manifest_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn browser_action_trace_hash(
    action_id: BrowserActionId,
    action_kind: BrowserActionKind,
    target_hash: [u8; 32],
    input_hash: Option<[u8; 32]>,
    coordinate_x: Option<i32>,
    coordinate_y: Option<i32>,
    prompt_decision: Option<BrowserPromptDecision>,
    policy_window_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-action-trace-v1");
    update_u128(&mut hasher, action_id);
    update_u8(&mut hasher, action_kind as u8);
    hasher.update(&target_hash);
    update_optional_hash(&mut hasher, input_hash);
    update_optional_i32(&mut hasher, coordinate_x);
    update_optional_i32(&mut hasher, coordinate_y);
    match prompt_decision {
        Some(decision) => {
            update_bool(&mut hasher, true);
            update_u8(&mut hasher, decision as u8);
        }
        None => update_bool(&mut hasher, false),
    }
    hasher.update(&policy_window_hash);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn browser_action_plan_record_hash(
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    plan_kind: BrowserActionPlanKind,
    sequence_number: u64,
    typed_tool_ir_hash: [u8; 32],
    expected_url_before_hash: [u8; 32],
    target_hash: [u8; 32],
    input_hash: Option<[u8; 32]>,
    coordinate_x: Option<i32>,
    coordinate_y: Option<i32>,
    prompt_decision: Option<BrowserPromptDecision>,
    policy_window_hash: [u8; 32],
    browser_session_hash: [u8; 32],
    redaction_policy_hash: [u8; 32],
    terminates_sequence: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-action-plan-record-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, action_id);
    update_u8(&mut hasher, plan_kind as u8);
    update_u64(&mut hasher, sequence_number);
    hasher.update(&typed_tool_ir_hash);
    hasher.update(&expected_url_before_hash);
    hasher.update(&target_hash);
    update_optional_hash(&mut hasher, input_hash);
    update_optional_i32(&mut hasher, coordinate_x);
    update_optional_i32(&mut hasher, coordinate_y);
    match prompt_decision {
        Some(decision) => {
            update_bool(&mut hasher, true);
            update_u8(&mut hasher, decision as u8);
        }
        None => update_bool(&mut hasher, false),
    }
    hasher.update(&policy_window_hash);
    hasher.update(&browser_session_hash);
    hasher.update(&redaction_policy_hash);
    update_bool(&mut hasher, terminates_sequence);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
fn browser_witness_proof_hash(
    run_id: BrowserRunId,
    action_id: BrowserActionId,
    side_effect_class: SideEffectClass,
    url_before_hash: [u8; 32],
    url_after_hash: [u8; 32],
    dom_snapshot_hash_before: [u8; 32],
    dom_snapshot_hash_after: [u8; 32],
    screenshot_hash_before: [u8; 32],
    screenshot_hash_after: [u8; 32],
    accessibility_tree_hash_after: [u8; 32],
    network_log_hash: [u8; 32],
    action_trace_hash: [u8; 32],
    policy_window_hash: [u8; 32],
    browser_session_hash: [u8; 32],
    redaction_policy_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"browser-witness-proof-v1");
    update_u128(&mut hasher, run_id);
    update_u128(&mut hasher, action_id);
    update_u8(&mut hasher, side_effect_class as u8);
    hasher.update(&url_before_hash);
    hasher.update(&url_after_hash);
    hasher.update(&dom_snapshot_hash_before);
    hasher.update(&dom_snapshot_hash_after);
    hasher.update(&screenshot_hash_before);
    hasher.update(&screenshot_hash_after);
    hasher.update(&accessibility_tree_hash_after);
    hasher.update(&network_log_hash);
    hasher.update(&action_trace_hash);
    hasher.update(&policy_window_hash);
    hasher.update(&browser_session_hash);
    hasher.update(&redaction_policy_hash);
    *hasher.finalize().as_bytes()
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn browser_page_search_score(match_count: u32, ordinal: usize) -> u32 {
    let ordinal = ordinal.min(u32::MAX as usize) as u32;
    let base = 1_000_000u32.saturating_sub(ordinal.saturating_mul(10_000));
    let density_bonus = match_count.min(100).saturating_mul(100);
    base.saturating_sub(density_bonus).min(1_000_000)
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

fn update_i32(hasher: &mut Hasher, value: i32) {
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

fn update_optional_i32(hasher: &mut Hasher, value: Option<i32>) {
    match value {
        Some(value) => {
            update_bool(hasher, true);
            update_i32(hasher, value);
        }
        None => update_bool(hasher, false),
    }
}
