use std::collections::BTreeMap;

use crate::licensing::LicenseError;
use aho_corasick::AhoCorasick;
use blake3::Hasher;

pub type EvidenceRefHash = [u8; 32];
pub const CANDIDATE_SCORE_PPM_MAX: u32 = 1_000_000;

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EvidenceCandidateTier {
    ActivatedContext = 1,
    LexicalBaseline = 2,
    LexicalBm25 = 3,
    FstExpansion = 4,
    BitmapFilter = 5,
    ExactSimdRerank = 6,
    ColdVectorExpansion = 7,
    AgenticProgramOutput = 8,
    BrowserPageSearch = 9,
    AstSignatureMatch = 10,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CandidateEvidenceUse {
    ContextPackCandidate,
    PhysicalWitness,
    PolicyApproval,
    MemoryCommit,
    TaskStatusDone,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CandidateOnlyGateError {
    InvalidCandidate,
    CandidateCannotCommit(CandidateEvidenceUse),
    MissingReplayEvent,
    ReplayRecordMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LexicalIndexError {
    InvalidIndexEpoch,
    InvalidDocument,
    DuplicateDocument,
    EmptyTerms,
    LicenseRequired(LicenseError),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BitmapFilterError {
    InvalidIndexEpoch,
    EmptyFilterSet,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TermDictionaryError {
    InvalidIndexEpoch,
    EmptyDictionary,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ColdVectorIndexError {
    InvalidIndexEpoch,
    InvalidDimension,
    InvalidVector,
    DuplicateVector,
    EmptyQuery,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HotEvidenceIndexError {
    InvalidIndexEpoch,
    InvalidDocument,
    DuplicateDocument,
    EmptyPatterns,
    InvalidArtifactBinding,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IndexEpochReplayError {
    InvalidIndexEpoch,
    EmptyQuery,
    InvalidCandidate,
    CandidateEpochMismatch,
    CandidateLimitExceeded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ColdVectorExpansionReplayError {
    InvalidIndexEpoch,
    InvalidExpansion,
    InvalidCandidate,
    CandidateEpochMismatch,
    CandidateTierMismatch,
    CandidateLimitExceeded,
    LatencyEvidenceMissing,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgenticEvidenceProgramError {
    InvalidIndexEpoch,
    EmptyProgram,
    EmptyQuery,
    InvalidStep,
    FuelExceeded,
    MissingLexicalIndex,
    MissingExactIndex,
    MissingBitmapFilter,
    ReplayRecordMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgenticEvidenceSdkError {
    InvalidCapsuleId,
    MissingLexicalIndex,
    MissingExactIndex,
    MissingBitmapFilter,
    LexicalIndexEpochMismatch,
    ExactIndexEpochMismatch,
    BitmapFilterEpochMismatch,
    EmptyCandidateSet,
    Program(AgenticEvidenceProgramError),
    Capsule(CandidateStateCapsuleError),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BrowserPageSearchCandidateError {
    InvalidPacket,
    InvalidPattern,
    InvalidDomArtifact,
    InvalidCandidate,
    CandidateTierMismatch,
    CandidateLimitExceeded,
    NoMatches,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CandidateStateCapsuleError {
    InvalidCapsule,
    InvalidCandidate,
    CandidateGate(CandidateOnlyGateError),
    ReplayRecordMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CandidateEvidenceRef {
    pub evidence_ref_hash: EvidenceRefHash,
    pub segment_id: u64,
    pub origin_tier: EvidenceCandidateTier,
    pub score_quantized: u32,
    pub index_epoch_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IndexEpochInputs {
    pub segment_catalog_hash: [u8; 32],
    pub tokenizer_config_hash: [u8; 32],
    pub schema_hash: [u8; 32],
    pub redaction_policy_hash: [u8; 32],
    pub embedding_model_hash_or_zero: [u8; 32],
    pub feature_profile_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct CandidateOnlyGate;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SortedEvidenceSet {
    values: Vec<u64>,
    set_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HotBitmapFilter {
    index_epoch_hash: [u8; 32],
    allowed_segments: SortedEvidenceSet,
    filter_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExpandedTermRef {
    pub term_hash: u64,
    pub dictionary_ordinal: u32,
    pub term_len: u16,
    pub index_epoch_hash: [u8; 32],
    pub expansion_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HotTermDictionary {
    index_epoch_hash: [u8; 32],
    terms: Vec<DictionaryTerm>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ColdVectorIndex {
    index_epoch_hash: [u8; 32],
    dimension: usize,
    vectors: Vec<ColdVectorDocMeta>,
    vector_values: Vec<Box<[i16]>>,
    doc_index_by_key: BTreeMap<LexicalDocKey, usize>,
    index_hash: [u8; 32],
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ColdVectorQueryScratch {
    scored_doc_indices: Vec<usize>,
    scores: Vec<u32>,
    results: Vec<CandidateEvidenceRef>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IndexEpochReplayRecord {
    pub query_hash: [u8; 32],
    pub index_epoch_hash: [u8; 32],
    pub limit: u32,
    pub candidate_count: u32,
    pub candidate_list_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ColdVectorExpansionReplayRecord {
    pub query_hash: [u8; 32],
    pub index_epoch_hash: [u8; 32],
    pub expansion_config_hash: [u8; 32],
    pub expansion_artifact_hash: [u8; 32],
    pub limit: u32,
    pub latency_ns: u64,
    pub candidate_count: u32,
    pub candidate_list_hash: [u8; 32],
    pub record_hash: [u8; 32],
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgenticEvidencePrimitiveKind {
    LexicalTopK = 1,
    ExactArtifactRerank = 2,
    BitmapFilter = 3,
    AstSignatureLookup = 4,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AgenticEvidenceProgramStep {
    LexicalTopK {
        query_terms: Vec<String>,
        limit: usize,
    },
    ExactArtifactRerank {
        exact_patterns: Vec<String>,
        limit: usize,
    },
    AstSignatureLookup {
        ast_signature_hashes: Vec<[u8; 32]>,
        limit: usize,
    },
    BitmapFilter {
        limit: usize,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgenticEvidenceProgram {
    index_epoch_hash: [u8; 32],
    max_fuel: u32,
    steps: Vec<AgenticEvidenceProgramStep>,
    program_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgenticEvidenceExecutionRecord {
    pub program_hash: [u8; 32],
    pub index_epoch_hash: [u8; 32],
    pub step_count: u32,
    pub fuel_used: u32,
    pub candidate_count: u32,
    pub candidate_list_hash: [u8; 32],
    pub trace_hash: [u8; 32],
    pub record_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AgenticEvidenceSdkManifest {
    pub program_hash: [u8; 32],
    pub index_epoch_hash: [u8; 32],
    pub max_fuel: u32,
    pub step_count: u32,
    pub required_primitive_hash: [u8; 32],
    pub requires_lexical_index: bool,
    pub requires_exact_index: bool,
    pub requires_bitmap_filter: bool,
    pub lexical_index_hash: [u8; 32],
    pub exact_index_hash: [u8; 32],
    pub bitmap_filter_hash: [u8; 32],
    pub manifest_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgenticEvidenceSdkRun {
    pub manifest: AgenticEvidenceSdkManifest,
    pub execution_record: AgenticEvidenceExecutionRecord,
    pub capsule: CandidateStateCapsule,
    pub run_hash: [u8; 32],
}

pub struct AgenticEvidenceSdk;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BrowserPageSearchCandidateRecord {
    pub browser_observation_packet_hash: [u8; 32],
    pub dom_artifact_hash: [u8; 32],
    pub pattern_hash: [u8; 32],
    pub index_epoch_hash: [u8; 32],
    pub limit: u32,
    pub match_count: u32,
    pub candidate_count: u32,
    pub candidate_list_hash: [u8; 32],
    pub record_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CandidateStateCapsule {
    pub capsule_id: u128,
    pub index_epoch_hash: [u8; 32],
    pub candidate_count: u32,
    pub candidate_list_hash: [u8; 32],
    pub cold_vector_replay_record_hash: [u8; 32],
    pub agentic_evidence_execution_record_hash: [u8; 32],
    pub payload_byte_len: u32,
    pub payload_hash: [u8; 32],
    pub capsule_hash: [u8; 32],
    candidates: Vec<CandidateEvidenceRef>,
    payload: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HotLexicalIndex {
    index_epoch_hash: [u8; 32],
    docs: Vec<LexicalDocMeta>,
    doc_index_by_key: BTreeMap<LexicalDocKey, usize>,
    postings_by_token: BTreeMap<u64, Vec<LexicalPosting>>,
    index_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HotEvidenceIndex {
    index_epoch_hash: [u8; 32],
    docs: Vec<HotEvidenceDocMeta>,
    doc_index_by_key: BTreeMap<LexicalDocKey, usize>,
    doc_indices_by_segment: BTreeMap<u64, Vec<usize>>,
    doc_indices_by_ast_signature: BTreeMap<[u8; 32], Vec<usize>>,
    document_texts: Vec<Box<[u8]>>,
    index_hash: [u8; 32],
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct HotLexicalQueryScratch {
    query_tokens: Vec<u64>,
    scores: Vec<u32>,
    score_epochs: Vec<u32>,
    query_epoch: u32,
    touched_doc_indices: Vec<usize>,
    top_doc_indices: Vec<usize>,
    results: Vec<CandidateEvidenceRef>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct HotEvidenceQueryScratch {
    normalized_patterns: Vec<String>,
    ast_signature_hashes: Vec<[u8; 32]>,
    scores: Vec<u32>,
    top_doc_indices: Vec<usize>,
    pattern_epochs: Vec<u32>,
    doc_epoch: u32,
    results: Vec<CandidateEvidenceRef>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AgenticEvidenceProgramScratch {
    lexical_scratch: HotLexicalQueryScratch,
    exact_scratch: HotEvidenceQueryScratch,
    working_candidates: Vec<CandidateEvidenceRef>,
    trace_hashes: Vec<[u8; 32]>,
    exact_allowed_segments: Vec<u64>,
    filter_output: Vec<CandidateEvidenceRef>,
    fuel_used: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
struct LexicalDocKey {
    evidence_ref_hash: [u8; 32],
    segment_id: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LexicalDocMeta {
    evidence_ref_hash: [u8; 32],
    segment_id: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct HotEvidenceDocMeta {
    evidence_ref_hash: [u8; 32],
    segment_id: u64,
    artifact_hash: [u8; 32],
    ast_signature_hash: [u8; 32],
    content_hash: [u8; 32],
    binding_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ColdVectorDocMeta {
    evidence_ref_hash: [u8; 32],
    segment_id: u64,
    artifact_hash: [u8; 32],
    vector_hash: [u8; 32],
    norm_sq: u64,
    binding_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LexicalPosting {
    doc_index: usize,
    term_frequency: u16,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct DictionaryTerm {
    normalized: String,
    term_hash: u64,
}

impl EvidenceCandidateTier {
    pub fn from_u8(value: u8) -> Option<Self> {
        match value {
            1 => Some(Self::ActivatedContext),
            2 => Some(Self::LexicalBaseline),
            3 => Some(Self::LexicalBm25),
            4 => Some(Self::FstExpansion),
            5 => Some(Self::BitmapFilter),
            6 => Some(Self::ExactSimdRerank),
            7 => Some(Self::ColdVectorExpansion),
            8 => Some(Self::AgenticProgramOutput),
            9 => Some(Self::BrowserPageSearch),
            10 => Some(Self::AstSignatureMatch),
            _ => None,
        }
    }

    pub fn requires_replay_event(self) -> bool {
        matches!(
            self,
            Self::ColdVectorExpansion | Self::AgenticProgramOutput | Self::BrowserPageSearch
        )
    }
}

impl CandidateEvidenceRef {
    pub fn new(
        evidence_ref_hash: EvidenceRefHash,
        segment_id: u64,
        origin_tier: EvidenceCandidateTier,
        score_quantized: u32,
        index_epoch_hash: [u8; 32],
    ) -> Self {
        Self {
            evidence_ref_hash,
            segment_id,
            origin_tier,
            score_quantized,
            index_epoch_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.evidence_ref_hash)
            && nonzero_hash(&self.index_epoch_hash)
            && self.score_quantized <= CANDIDATE_SCORE_PPM_MAX
    }

    pub fn candidate_hash(&self) -> [u8; 32] {
        candidate_evidence_ref_hash(self)
    }
}

impl IndexEpochInputs {
    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.segment_catalog_hash)
            && nonzero_hash(&self.tokenizer_config_hash)
            && nonzero_hash(&self.schema_hash)
            && nonzero_hash(&self.redaction_policy_hash)
            && nonzero_hash(&self.feature_profile_hash)
    }

    pub fn epoch_hash(&self) -> [u8; 32] {
        index_epoch_hash(self)
    }
}

impl CandidateOnlyGate {
    pub fn validate_use(
        candidate: &CandidateEvidenceRef,
        intended_use: CandidateEvidenceUse,
    ) -> Result<(), CandidateOnlyGateError> {
        if !candidate.is_valid() {
            return Err(CandidateOnlyGateError::InvalidCandidate);
        }
        match intended_use {
            CandidateEvidenceUse::ContextPackCandidate => {
                if candidate.origin_tier.requires_replay_event() {
                    Err(CandidateOnlyGateError::MissingReplayEvent)
                } else {
                    Ok(())
                }
            }
            forbidden => Err(CandidateOnlyGateError::CandidateCannotCommit(forbidden)),
        }
    }

    pub fn validate_context_pack_candidates(
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
    ) -> Result<(), CandidateOnlyGateError> {
        Self::validate_replay_bound_context_pack_candidates(candidates, cold_vector_record, None)
    }

    pub fn validate_replay_bound_context_pack_candidates(
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
    ) -> Result<(), CandidateOnlyGateError> {
        Self::validate_replay_bound_context_pack_candidates_with_browser(
            candidates,
            cold_vector_record,
            agentic_record,
            None,
        )
    }

    pub fn validate_replay_bound_context_pack_candidates_with_browser(
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> Result<(), CandidateOnlyGateError> {
        let mut cold_candidates = Vec::new();
        let mut agentic_candidates = Vec::new();
        let mut browser_page_search_candidates = Vec::new();
        for candidate in candidates {
            if !candidate.is_valid() {
                return Err(CandidateOnlyGateError::InvalidCandidate);
            }
            match candidate.origin_tier {
                EvidenceCandidateTier::ColdVectorExpansion => cold_candidates.push(*candidate),
                EvidenceCandidateTier::AgenticProgramOutput => agentic_candidates.push(*candidate),
                EvidenceCandidateTier::BrowserPageSearch => {
                    browser_page_search_candidates.push(*candidate)
                }
                _ => {}
            }
        }

        if !cold_candidates.is_empty() {
            let record = cold_vector_record.ok_or(CandidateOnlyGateError::MissingReplayEvent)?;
            if !record.is_valid()
                || record.candidate_count != cold_candidates.len().min(u32::MAX as usize) as u32
                || record.candidate_list_hash != candidate_list_hash(&cold_candidates)
                || cold_candidates.iter().any(|candidate| {
                    candidate.origin_tier != EvidenceCandidateTier::ColdVectorExpansion
                        || candidate.index_epoch_hash != record.index_epoch_hash
                })
            {
                return Err(CandidateOnlyGateError::ReplayRecordMismatch);
            }
        }

        if !agentic_candidates.is_empty() {
            let record = agentic_record.ok_or(CandidateOnlyGateError::MissingReplayEvent)?;
            if !record.is_valid()
                || record.candidate_count != agentic_candidates.len().min(u32::MAX as usize) as u32
                || record.candidate_list_hash != candidate_list_hash(&agentic_candidates)
                || agentic_candidates.iter().any(|candidate| {
                    candidate.origin_tier != EvidenceCandidateTier::AgenticProgramOutput
                        || candidate.index_epoch_hash != record.index_epoch_hash
                })
            {
                return Err(CandidateOnlyGateError::ReplayRecordMismatch);
            }
        }
        if !browser_page_search_candidates.is_empty() {
            let record =
                browser_page_search_record.ok_or(CandidateOnlyGateError::MissingReplayEvent)?;
            if !record.matches_candidates(&browser_page_search_candidates) {
                return Err(CandidateOnlyGateError::ReplayRecordMismatch);
            }
        }
        Ok(())
    }

    pub fn validate_browser_page_search_candidates(
        candidates: &[CandidateEvidenceRef],
        record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> Result<(), CandidateOnlyGateError> {
        let browser_candidates: Vec<CandidateEvidenceRef> = candidates
            .iter()
            .copied()
            .filter(|candidate| candidate.origin_tier == EvidenceCandidateTier::BrowserPageSearch)
            .collect();
        if browser_candidates.is_empty() {
            return Ok(());
        }
        let record = record.ok_or(CandidateOnlyGateError::MissingReplayEvent)?;
        if !record.is_valid()
            || record.candidate_count != browser_candidates.len().min(u32::MAX as usize) as u32
            || record.candidate_list_hash != candidate_list_hash(&browser_candidates)
            || browser_candidates.iter().any(|candidate| {
                candidate.origin_tier != EvidenceCandidateTier::BrowserPageSearch
                    || candidate.index_epoch_hash != record.index_epoch_hash
            })
        {
            return Err(CandidateOnlyGateError::ReplayRecordMismatch);
        }
        Ok(())
    }
}

impl SortedEvidenceSet {
    pub fn from_unsorted(mut values: Vec<u64>) -> Self {
        values.sort_unstable();
        values.dedup();
        Self::from_sorted_unique(values)
    }

    pub fn len(&self) -> usize {
        self.values.len()
    }

    pub fn is_empty(&self) -> bool {
        self.values.is_empty()
    }

    pub fn values(&self) -> &[u64] {
        &self.values
    }

    pub fn set_hash(&self) -> [u8; 32] {
        self.set_hash
    }

    pub fn contains(&self, value: u64) -> bool {
        self.values.binary_search(&value).is_ok()
    }

    pub fn intersect(&self, other: &Self) -> Self {
        let mut output = Vec::with_capacity(self.values.len().min(other.values.len()));
        self.intersect_into(other, &mut output);
        Self::from_sorted_unique(output)
    }

    pub fn intersect_into(&self, other: &Self, output: &mut Vec<u64>) {
        output.clear();
        let mut left_index = 0usize;
        let mut right_index = 0usize;
        while left_index < self.values.len() && right_index < other.values.len() {
            let left = self.values[left_index];
            let right = other.values[right_index];
            if left == right {
                output.push(left);
                left_index += 1;
                right_index += 1;
            } else if left < right {
                left_index += 1;
            } else {
                right_index += 1;
            }
        }
    }

    fn from_sorted_unique(values: Vec<u64>) -> Self {
        let set_hash = sorted_evidence_set_hash(&values);
        Self { values, set_hash }
    }
}

impl HotBitmapFilter {
    pub fn new(
        index_epoch_hash: [u8; 32],
        allowed_segments: SortedEvidenceSet,
    ) -> Result<Self, BitmapFilterError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(BitmapFilterError::InvalidIndexEpoch);
        }
        if allowed_segments.is_empty() {
            return Err(BitmapFilterError::EmptyFilterSet);
        }
        let filter_hash = agentic_evidence_bitmap_filter_hash_parts(
            index_epoch_hash,
            allowed_segments.len(),
            allowed_segments.set_hash(),
        );
        Ok(Self {
            index_epoch_hash,
            allowed_segments,
            filter_hash,
        })
    }

    pub fn allowed_segment_count(&self) -> usize {
        self.allowed_segments.len()
    }

    pub fn filter_candidates(
        &self,
        candidates: &[CandidateEvidenceRef],
        limit: usize,
    ) -> Vec<CandidateEvidenceRef> {
        if limit == 0 {
            return Vec::new();
        }

        let mut filtered = Vec::with_capacity(limit.min(candidates.len()));
        for candidate in candidates {
            if filtered.len() >= limit {
                break;
            }
            if candidate.is_valid()
                && candidate.index_epoch_hash == self.index_epoch_hash
                && self.allowed_segments.contains(candidate.segment_id)
            {
                filtered.push(CandidateEvidenceRef::new(
                    candidate.evidence_ref_hash,
                    candidate.segment_id,
                    EvidenceCandidateTier::BitmapFilter,
                    candidate.score_quantized,
                    candidate.index_epoch_hash,
                ));
            }
        }
        filtered
    }
}

impl ExpandedTermRef {
    pub fn is_valid(&self) -> bool {
        self.term_hash != 0
            && self.term_len > 0
            && nonzero_hash(&self.index_epoch_hash)
            && nonzero_hash(&self.expansion_hash)
            && self.expansion_hash == expanded_term_ref_hash(self)
    }
}

impl HotTermDictionary {
    pub fn from_terms(
        index_epoch_hash: [u8; 32],
        terms: &[&str],
    ) -> Result<Self, TermDictionaryError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(TermDictionaryError::InvalidIndexEpoch);
        }

        let mut normalized_terms: Vec<String> = terms
            .iter()
            .filter_map(|term| normalize_dictionary_term(term))
            .collect();
        normalized_terms.sort_unstable();
        normalized_terms.dedup();
        if normalized_terms.is_empty() {
            return Err(TermDictionaryError::EmptyDictionary);
        }

        let terms = normalized_terms
            .into_iter()
            .map(|normalized| DictionaryTerm {
                term_hash: lexical_token_hash(&normalized).expect("normalized term is non-empty"),
                normalized,
            })
            .collect();
        Ok(Self {
            index_epoch_hash,
            terms,
        })
    }

    pub fn term_count(&self) -> usize {
        self.terms.len()
    }

    pub fn expand_prefix(&self, prefix: &str, limit: usize) -> Vec<ExpandedTermRef> {
        if limit == 0 {
            return Vec::new();
        }
        let Some(normalized_prefix) = normalize_dictionary_term(prefix) else {
            return Vec::new();
        };

        let start = self
            .terms
            .partition_point(|term| term.normalized.as_str() < normalized_prefix.as_str());
        let mut expanded = Vec::with_capacity(limit.min(self.terms.len().saturating_sub(start)));
        for (relative_index, term) in self.terms[start..].iter().enumerate() {
            if expanded.len() >= limit || !term.normalized.starts_with(&normalized_prefix) {
                break;
            }
            let dictionary_ordinal = start.saturating_add(relative_index).min(u32::MAX as usize);
            let mut term_ref = ExpandedTermRef {
                term_hash: term.term_hash,
                dictionary_ordinal: dictionary_ordinal as u32,
                term_len: term.normalized.len().min(u16::MAX as usize) as u16,
                index_epoch_hash: self.index_epoch_hash,
                expansion_hash: [0; 32],
            };
            term_ref.expansion_hash = expanded_term_ref_hash(&term_ref);
            expanded.push(term_ref);
        }
        expanded
    }
}

impl ColdVectorIndex {
    pub fn new(index_epoch_hash: [u8; 32], dimension: usize) -> Result<Self, ColdVectorIndexError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(ColdVectorIndexError::InvalidIndexEpoch);
        }
        if dimension == 0 || dimension > 4096 {
            return Err(ColdVectorIndexError::InvalidDimension);
        }
        Ok(Self {
            index_epoch_hash,
            dimension,
            vectors: Vec::new(),
            vector_values: Vec::new(),
            doc_index_by_key: BTreeMap::new(),
            index_hash: cold_vector_index_hash(index_epoch_hash, dimension, &[]),
        })
    }

    pub fn dimension(&self) -> usize {
        self.dimension
    }

    pub fn vector_count(&self) -> usize {
        self.vectors.len()
    }

    pub fn index_hash(&self) -> [u8; 32] {
        self.index_hash
    }

    pub fn expansion_config_hash(&self) -> [u8; 32] {
        cold_vector_expansion_config_hash(self.index_epoch_hash, self.dimension)
    }

    pub fn query_scratch(&self) -> ColdVectorQueryScratch {
        ColdVectorQueryScratch::with_capacity(self.vectors.len())
    }

    pub fn insert_vector(
        &mut self,
        evidence_ref_hash: [u8; 32],
        segment_id: u64,
        artifact_hash: [u8; 32],
        vector: &[i16],
    ) -> Result<(), ColdVectorIndexError> {
        if !nonzero_hash(&evidence_ref_hash) || !nonzero_hash(&artifact_hash) {
            return Err(ColdVectorIndexError::InvalidVector);
        }
        if vector.len() != self.dimension || vector.iter().all(|value| *value == 0) {
            return Err(ColdVectorIndexError::InvalidVector);
        }
        let key = LexicalDocKey {
            evidence_ref_hash,
            segment_id,
        };
        if self.doc_index_by_key.contains_key(&key) {
            return Err(ColdVectorIndexError::DuplicateVector);
        }

        let vector_hash = cold_vector_payload_hash(vector);
        let norm_sq = vector_norm_sq(vector);
        if norm_sq == 0 {
            return Err(ColdVectorIndexError::InvalidVector);
        }
        let binding_hash =
            cold_vector_document_binding_hash(evidence_ref_hash, artifact_hash, vector_hash);
        let meta = ColdVectorDocMeta {
            evidence_ref_hash,
            segment_id,
            artifact_hash,
            vector_hash,
            norm_sq,
            binding_hash,
        };
        if !meta.is_valid() {
            return Err(ColdVectorIndexError::InvalidVector);
        }
        let doc_index = self.vectors.len();
        self.vectors.push(meta);
        self.vector_values.push(vector.to_vec().into_boxed_slice());
        self.doc_index_by_key.insert(key, doc_index);
        self.index_hash =
            cold_vector_index_hash(self.index_epoch_hash, self.dimension, &self.vectors);
        Ok(())
    }

    pub fn query_top_k(
        &self,
        query_vector: &[i16],
        limit: usize,
    ) -> Result<Vec<CandidateEvidenceRef>, ColdVectorIndexError> {
        let mut scratch = self.query_scratch();
        Ok(self
            .query_top_k_into_scratch(query_vector, limit, &mut scratch)?
            .to_vec())
    }

    pub fn query_top_k_into_scratch<'a>(
        &self,
        query_vector: &[i16],
        limit: usize,
        scratch: &'a mut ColdVectorQueryScratch,
    ) -> Result<&'a [CandidateEvidenceRef], ColdVectorIndexError> {
        scratch.prepare_for_query(self.vectors.len(), limit);
        if limit == 0 {
            return Ok(&scratch.results);
        }
        if query_vector.len() != self.dimension || query_vector.iter().all(|value| *value == 0) {
            return Err(ColdVectorIndexError::EmptyQuery);
        }
        let query_norm_sq = vector_norm_sq(query_vector);
        if query_norm_sq == 0 {
            return Err(ColdVectorIndexError::EmptyQuery);
        }

        for (doc_index, doc_vector) in self.vector_values.iter().enumerate() {
            let score = vector_similarity_ppm(
                query_vector,
                query_norm_sq,
                doc_vector,
                self.vectors[doc_index].norm_sq,
            );
            if score == 0 {
                continue;
            }
            scratch.scores[doc_index] = score;
            push_ranked_vector_doc(
                &mut scratch.scored_doc_indices,
                doc_index,
                &scratch.scores,
                &self.vectors,
            );
            if scratch.scored_doc_indices.len() > limit {
                scratch.scored_doc_indices.pop();
            }
        }

        for doc_index in scratch.scored_doc_indices.iter().copied() {
            if let Some(doc) = self.vectors.get(doc_index) {
                scratch.results.push(CandidateEvidenceRef::new(
                    doc.evidence_ref_hash,
                    doc.segment_id,
                    EvidenceCandidateTier::ColdVectorExpansion,
                    scratch.scores[doc_index],
                    self.index_epoch_hash,
                ));
            }
        }
        Ok(&scratch.results)
    }
}

impl IndexEpochReplayRecord {
    pub fn new(
        index_epoch_hash: [u8; 32],
        query_terms: &[&str],
        limit: usize,
        candidates: &[CandidateEvidenceRef],
    ) -> Result<Self, IndexEpochReplayError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(IndexEpochReplayError::InvalidIndexEpoch);
        }
        if candidates.len() > limit {
            return Err(IndexEpochReplayError::CandidateLimitExceeded);
        }
        for candidate in candidates {
            if !candidate.is_valid() {
                return Err(IndexEpochReplayError::InvalidCandidate);
            }
            if candidate.index_epoch_hash != index_epoch_hash {
                return Err(IndexEpochReplayError::CandidateEpochMismatch);
            }
        }
        let query_hash =
            lexical_query_hash(query_terms, limit).ok_or(IndexEpochReplayError::EmptyQuery)?;
        Ok(Self {
            query_hash,
            index_epoch_hash,
            limit: limit.min(u32::MAX as usize) as u32,
            candidate_count: candidates.len().min(u32::MAX as usize) as u32,
            candidate_list_hash: candidate_list_hash(candidates),
        })
    }

    pub fn matches_candidates(
        &self,
        query_terms: &[&str],
        limit: usize,
        candidates: &[CandidateEvidenceRef],
    ) -> bool {
        match Self::new(self.index_epoch_hash, query_terms, limit, candidates) {
            Ok(other) => other == *self,
            Err(_) => false,
        }
    }
}

impl ColdVectorExpansionReplayRecord {
    pub fn new(
        index_epoch_hash: [u8; 32],
        query_terms: &[&str],
        limit: usize,
        expansion_config_hash: [u8; 32],
        expansion_artifact_hash: [u8; 32],
        latency_ns: u64,
        candidates: &[CandidateEvidenceRef],
    ) -> Result<Self, ColdVectorExpansionReplayError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(ColdVectorExpansionReplayError::InvalidIndexEpoch);
        }
        if !nonzero_hash(&expansion_config_hash) || !nonzero_hash(&expansion_artifact_hash) {
            return Err(ColdVectorExpansionReplayError::InvalidExpansion);
        }
        if latency_ns == 0 {
            return Err(ColdVectorExpansionReplayError::LatencyEvidenceMissing);
        }
        if candidates.len() > limit {
            return Err(ColdVectorExpansionReplayError::CandidateLimitExceeded);
        }
        let query_hash = lexical_query_hash(query_terms, limit)
            .ok_or(ColdVectorExpansionReplayError::InvalidExpansion)?;
        Self::new_with_query_hash(
            index_epoch_hash,
            query_hash,
            limit,
            expansion_config_hash,
            expansion_artifact_hash,
            latency_ns,
            candidates,
        )
    }

    pub fn new_with_query_hash(
        index_epoch_hash: [u8; 32],
        query_hash: [u8; 32],
        limit: usize,
        expansion_config_hash: [u8; 32],
        expansion_artifact_hash: [u8; 32],
        latency_ns: u64,
        candidates: &[CandidateEvidenceRef],
    ) -> Result<Self, ColdVectorExpansionReplayError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(ColdVectorExpansionReplayError::InvalidIndexEpoch);
        }
        if !nonzero_hash(&query_hash)
            || !nonzero_hash(&expansion_config_hash)
            || !nonzero_hash(&expansion_artifact_hash)
        {
            return Err(ColdVectorExpansionReplayError::InvalidExpansion);
        }
        if latency_ns == 0 {
            return Err(ColdVectorExpansionReplayError::LatencyEvidenceMissing);
        }
        if candidates.len() > limit {
            return Err(ColdVectorExpansionReplayError::CandidateLimitExceeded);
        }
        for candidate in candidates {
            if !candidate.is_valid() {
                return Err(ColdVectorExpansionReplayError::InvalidCandidate);
            }
            if candidate.index_epoch_hash != index_epoch_hash {
                return Err(ColdVectorExpansionReplayError::CandidateEpochMismatch);
            }
            if candidate.origin_tier != EvidenceCandidateTier::ColdVectorExpansion {
                return Err(ColdVectorExpansionReplayError::CandidateTierMismatch);
            }
        }
        let mut record = Self {
            query_hash,
            index_epoch_hash,
            expansion_config_hash,
            expansion_artifact_hash,
            limit: limit.min(u32::MAX as usize) as u32,
            latency_ns,
            candidate_count: candidates.len().min(u32::MAX as usize) as u32,
            candidate_list_hash: candidate_list_hash(candidates),
            record_hash: [0; 32],
        };
        record.record_hash = cold_vector_expansion_replay_record_hash(&record);
        Ok(record)
    }

    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.query_hash)
            && nonzero_hash(&self.index_epoch_hash)
            && nonzero_hash(&self.expansion_config_hash)
            && nonzero_hash(&self.expansion_artifact_hash)
            && self.latency_ns > 0
            && nonzero_hash(&self.candidate_list_hash)
            && self.record_hash == cold_vector_expansion_replay_record_hash(self)
    }
}

impl AgenticEvidenceProgramStep {
    pub fn lexical_top_k(query_terms: &[&str], limit: usize) -> Self {
        Self::LexicalTopK {
            query_terms: query_terms.iter().map(|term| (*term).to_owned()).collect(),
            limit,
        }
    }

    pub fn exact_artifact_rerank(exact_patterns: &[&str], limit: usize) -> Self {
        Self::ExactArtifactRerank {
            exact_patterns: exact_patterns
                .iter()
                .map(|pattern| (*pattern).to_owned())
                .collect(),
            limit,
        }
    }

    pub fn ast_signature_lookup(ast_signature_hashes: &[[u8; 32]], limit: usize) -> Self {
        Self::AstSignatureLookup {
            ast_signature_hashes: ast_signature_hashes.to_vec(),
            limit,
        }
    }

    pub fn bitmap_filter(limit: usize) -> Self {
        Self::BitmapFilter { limit }
    }

    pub fn primitive_kind(&self) -> AgenticEvidencePrimitiveKind {
        match self {
            Self::LexicalTopK { .. } => AgenticEvidencePrimitiveKind::LexicalTopK,
            Self::ExactArtifactRerank { .. } => AgenticEvidencePrimitiveKind::ExactArtifactRerank,
            Self::AstSignatureLookup { .. } => AgenticEvidencePrimitiveKind::AstSignatureLookup,
            Self::BitmapFilter { .. } => AgenticEvidencePrimitiveKind::BitmapFilter,
        }
    }

    pub fn fuel_cost(&self) -> u32 {
        match self {
            Self::LexicalTopK { query_terms, limit } => 1u32
                .saturating_add(query_terms.len().min(u32::MAX as usize) as u32)
                .saturating_add((*limit).min(u32::MAX as usize) as u32),
            Self::ExactArtifactRerank {
                exact_patterns,
                limit,
            } => 4u32
                .saturating_add(exact_patterns.len().min(u32::MAX as usize) as u32)
                .saturating_add((*limit).min(u32::MAX as usize) as u32),
            Self::AstSignatureLookup {
                ast_signature_hashes,
                limit,
            } => 2u32
                .saturating_add(ast_signature_hashes.len().min(u32::MAX as usize) as u32)
                .saturating_add((*limit).min(u32::MAX as usize) as u32),
            Self::BitmapFilter { limit } => {
                1u32.saturating_add((*limit).min(u32::MAX as usize) as u32)
            }
        }
    }

    fn is_valid(&self) -> bool {
        match self {
            Self::LexicalTopK { query_terms, limit } => {
                *limit > 0 && !normalized_owned_terms(query_terms).is_empty()
            }
            Self::ExactArtifactRerank {
                exact_patterns,
                limit,
            } => *limit > 0 && !normalized_owned_exact_patterns(exact_patterns).is_empty(),
            Self::AstSignatureLookup {
                ast_signature_hashes,
                limit,
            } => *limit > 0 && ast_signature_hashes.iter().any(nonzero_hash),
            Self::BitmapFilter { limit } => *limit > 0,
        }
    }
}

impl AgenticEvidenceProgram {
    pub fn new(
        index_epoch_hash: [u8; 32],
        max_fuel: u32,
        steps: Vec<AgenticEvidenceProgramStep>,
    ) -> Result<Self, AgenticEvidenceProgramError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(AgenticEvidenceProgramError::InvalidIndexEpoch);
        }
        if steps.is_empty() || max_fuel == 0 {
            return Err(AgenticEvidenceProgramError::EmptyProgram);
        }
        let mut fuel_required = 0u32;
        for step in &steps {
            if !step.is_valid() {
                return Err(AgenticEvidenceProgramError::InvalidStep);
            }
            fuel_required = fuel_required.saturating_add(step.fuel_cost());
            if fuel_required > max_fuel {
                return Err(AgenticEvidenceProgramError::FuelExceeded);
            }
        }

        let program_hash = agentic_evidence_program_hash(index_epoch_hash, max_fuel, &steps);
        Ok(Self {
            index_epoch_hash,
            max_fuel,
            steps,
            program_hash,
        })
    }

    pub fn program_hash(&self) -> [u8; 32] {
        self.program_hash
    }

    pub fn index_epoch_hash(&self) -> [u8; 32] {
        self.index_epoch_hash
    }

    pub fn steps(&self) -> &[AgenticEvidenceProgramStep] {
        &self.steps
    }

    pub fn query_scratch(&self) -> AgenticEvidenceProgramScratch {
        AgenticEvidenceProgramScratch::default()
    }

    pub fn execute(
        &self,
        lexical_index: Option<&HotLexicalIndex>,
        exact_index: Option<&HotEvidenceIndex>,
        bitmap_filter: Option<&HotBitmapFilter>,
    ) -> Result<
        (Vec<CandidateEvidenceRef>, AgenticEvidenceExecutionRecord),
        AgenticEvidenceProgramError,
    > {
        let mut scratch = self.query_scratch();
        let results =
            self.execute_into_scratch(lexical_index, exact_index, bitmap_filter, &mut scratch)?;
        Ok((results.to_vec(), scratch.execution_record(self)))
    }

    pub fn execute_into_scratch<'a>(
        &self,
        lexical_index: Option<&HotLexicalIndex>,
        exact_index: Option<&HotEvidenceIndex>,
        bitmap_filter: Option<&HotBitmapFilter>,
        scratch: &'a mut AgenticEvidenceProgramScratch,
    ) -> Result<&'a [CandidateEvidenceRef], AgenticEvidenceProgramError> {
        scratch.prepare();
        let mut fuel_used = 0u32;

        for (step_index, step) in self.steps.iter().enumerate() {
            fuel_used = fuel_used.saturating_add(step.fuel_cost());
            if fuel_used > self.max_fuel {
                return Err(AgenticEvidenceProgramError::FuelExceeded);
            }
            let is_final_step = step_index + 1 == self.steps.len();

            match step {
                AgenticEvidenceProgramStep::LexicalTopK { query_terms, limit } => {
                    let index =
                        lexical_index.ok_or(AgenticEvidenceProgramError::MissingLexicalIndex)?;
                    if index.index_epoch_hash != self.index_epoch_hash {
                        return Err(AgenticEvidenceProgramError::InvalidIndexEpoch);
                    }
                    let query_refs = owned_terms_as_refs(query_terms);
                    let results = index.query_top_k_into_scratch(
                        &query_refs,
                        *limit,
                        &mut scratch.lexical_scratch,
                    );
                    scratch.working_candidates.clear();
                    scratch.working_candidates.extend_from_slice(results);
                    if is_final_step {
                        brand_agentic_program_output(&mut scratch.working_candidates);
                    }
                    scratch.trace_hashes.push(agentic_evidence_step_trace_hash(
                        self.program_hash,
                        step,
                        fuel_used,
                        &scratch.working_candidates,
                    ));
                }
                AgenticEvidenceProgramStep::ExactArtifactRerank {
                    exact_patterns,
                    limit,
                } => {
                    let index =
                        exact_index.ok_or(AgenticEvidenceProgramError::MissingExactIndex)?;
                    if index.index_epoch_hash != self.index_epoch_hash {
                        return Err(AgenticEvidenceProgramError::InvalidIndexEpoch);
                    }
                    scratch.exact_allowed_segments.clear();
                    for candidate in &scratch.working_candidates {
                        if candidate.is_valid()
                            && candidate.index_epoch_hash == self.index_epoch_hash
                        {
                            scratch.exact_allowed_segments.push(candidate.segment_id);
                        }
                    }
                    let allowed = (!scratch.exact_allowed_segments.is_empty()).then(|| {
                        SortedEvidenceSet::from_unsorted(scratch.exact_allowed_segments.clone())
                    });
                    let pattern_refs = owned_terms_as_refs(exact_patterns);
                    let results = index.query_exact_literals_into_scratch(
                        &pattern_refs,
                        allowed.as_ref(),
                        *limit,
                        &mut scratch.exact_scratch,
                    );
                    scratch.working_candidates.clear();
                    scratch.working_candidates.extend_from_slice(results);
                    if is_final_step {
                        brand_agentic_program_output(&mut scratch.working_candidates);
                    }
                    scratch.trace_hashes.push(agentic_evidence_step_trace_hash(
                        self.program_hash,
                        step,
                        fuel_used,
                        &scratch.working_candidates,
                    ));
                }
                AgenticEvidenceProgramStep::AstSignatureLookup {
                    ast_signature_hashes,
                    limit,
                } => {
                    let index =
                        exact_index.ok_or(AgenticEvidenceProgramError::MissingExactIndex)?;
                    if index.index_epoch_hash != self.index_epoch_hash {
                        return Err(AgenticEvidenceProgramError::InvalidIndexEpoch);
                    }
                    scratch.exact_allowed_segments.clear();
                    for candidate in &scratch.working_candidates {
                        if candidate.is_valid()
                            && candidate.index_epoch_hash == self.index_epoch_hash
                        {
                            scratch.exact_allowed_segments.push(candidate.segment_id);
                        }
                    }
                    let allowed = (!scratch.exact_allowed_segments.is_empty()).then(|| {
                        SortedEvidenceSet::from_unsorted(scratch.exact_allowed_segments.clone())
                    });
                    let results = index.query_ast_signatures_into_scratch(
                        ast_signature_hashes,
                        allowed.as_ref(),
                        *limit,
                        &mut scratch.exact_scratch,
                    );
                    scratch.working_candidates.clear();
                    scratch.working_candidates.extend_from_slice(results);
                    if is_final_step {
                        brand_agentic_program_output(&mut scratch.working_candidates);
                    }
                    scratch.trace_hashes.push(agentic_evidence_step_trace_hash(
                        self.program_hash,
                        step,
                        fuel_used,
                        &scratch.working_candidates,
                    ));
                }
                AgenticEvidenceProgramStep::BitmapFilter { limit } => {
                    let filter =
                        bitmap_filter.ok_or(AgenticEvidenceProgramError::MissingBitmapFilter)?;
                    if filter.index_epoch_hash != self.index_epoch_hash {
                        return Err(AgenticEvidenceProgramError::InvalidIndexEpoch);
                    }
                    scratch.filter_output =
                        filter.filter_candidates(&scratch.working_candidates, *limit);
                    scratch.working_candidates.clear();
                    scratch
                        .working_candidates
                        .extend_from_slice(&scratch.filter_output);
                    if is_final_step {
                        brand_agentic_program_output(&mut scratch.working_candidates);
                    }
                    scratch.trace_hashes.push(agentic_evidence_step_trace_hash(
                        self.program_hash,
                        step,
                        fuel_used,
                        &scratch.working_candidates,
                    ));
                }
            }

            if scratch
                .working_candidates
                .iter()
                .any(|candidate| !candidate.is_valid())
            {
                return Err(AgenticEvidenceProgramError::ReplayRecordMismatch);
            }
        }

        scratch.fuel_used = fuel_used;
        Ok(&scratch.working_candidates)
    }
}

fn brand_agentic_program_output(candidates: &mut [CandidateEvidenceRef]) {
    for candidate in candidates {
        candidate.origin_tier = EvidenceCandidateTier::AgenticProgramOutput;
    }
}

impl AgenticEvidenceExecutionRecord {
    pub fn new(
        program_hash: [u8; 32],
        index_epoch_hash: [u8; 32],
        step_count: usize,
        fuel_used: u32,
        candidates: &[CandidateEvidenceRef],
        trace_hashes: &[[u8; 32]],
    ) -> Result<Self, AgenticEvidenceProgramError> {
        if !nonzero_hash(&program_hash) || !nonzero_hash(&index_epoch_hash) || step_count == 0 {
            return Err(AgenticEvidenceProgramError::EmptyProgram);
        }
        if trace_hashes.len() != step_count {
            return Err(AgenticEvidenceProgramError::ReplayRecordMismatch);
        }
        for candidate in candidates {
            if !candidate.is_valid() || candidate.index_epoch_hash != index_epoch_hash {
                return Err(AgenticEvidenceProgramError::ReplayRecordMismatch);
            }
        }
        let trace_hash = agentic_evidence_trace_hash(trace_hashes);
        let mut record = Self {
            program_hash,
            index_epoch_hash,
            step_count: step_count.min(u32::MAX as usize) as u32,
            fuel_used,
            candidate_count: candidates.len().min(u32::MAX as usize) as u32,
            candidate_list_hash: candidate_list_hash(candidates),
            trace_hash,
            record_hash: [0; 32],
        };
        record.record_hash = agentic_evidence_execution_record_hash(&record);
        Ok(record)
    }

    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.program_hash)
            && nonzero_hash(&self.index_epoch_hash)
            && self.step_count > 0
            && self.fuel_used > 0
            && nonzero_hash(&self.candidate_list_hash)
            && nonzero_hash(&self.trace_hash)
            && self.record_hash == agentic_evidence_execution_record_hash(self)
    }

    pub fn matches_candidates(
        &self,
        program: &AgenticEvidenceProgram,
        candidates: &[CandidateEvidenceRef],
    ) -> bool {
        self.is_valid()
            && self.program_hash == program.program_hash
            && self.index_epoch_hash == program.index_epoch_hash
            && self.step_count == program.steps.len().min(u32::MAX as usize) as u32
            && self.candidate_count == candidates.len().min(u32::MAX as usize) as u32
            && self.candidate_list_hash == candidate_list_hash(candidates)
    }
}

impl AgenticEvidenceSdkManifest {
    pub fn for_program(
        program: &AgenticEvidenceProgram,
        lexical_index: Option<&HotLexicalIndex>,
        exact_index: Option<&HotEvidenceIndex>,
        bitmap_filter: Option<&HotBitmapFilter>,
    ) -> Result<Self, AgenticEvidenceSdkError> {
        let requirements = AgenticEvidenceSdkRequirements::from_steps(program.steps());
        let lexical_index_hash =
            required_lexical_index_hash(program.index_epoch_hash, lexical_index, requirements)?;
        let exact_index_hash =
            required_exact_index_hash(program.index_epoch_hash, exact_index, requirements)?;
        let bitmap_filter_hash =
            required_bitmap_filter_hash(program.index_epoch_hash, bitmap_filter, requirements)?;
        let mut manifest = Self {
            program_hash: program.program_hash,
            index_epoch_hash: program.index_epoch_hash,
            max_fuel: program.max_fuel,
            step_count: program.steps.len().min(u32::MAX as usize) as u32,
            required_primitive_hash: requirements.primitive_hash,
            requires_lexical_index: requirements.requires_lexical_index,
            requires_exact_index: requirements.requires_exact_index,
            requires_bitmap_filter: requirements.requires_bitmap_filter,
            lexical_index_hash,
            exact_index_hash,
            bitmap_filter_hash,
            manifest_hash: [0; 32],
        };
        manifest.manifest_hash = agentic_evidence_sdk_manifest_hash(&manifest);
        Ok(manifest)
    }

    pub fn is_valid_for_program(&self, program: &AgenticEvidenceProgram) -> bool {
        let requirements = AgenticEvidenceSdkRequirements::from_steps(program.steps());
        self.program_hash == program.program_hash
            && self.index_epoch_hash == program.index_epoch_hash
            && self.max_fuel == program.max_fuel
            && self.step_count == program.steps.len().min(u32::MAX as usize) as u32
            && self.required_primitive_hash == requirements.primitive_hash
            && self.requires_lexical_index == requirements.requires_lexical_index
            && self.requires_exact_index == requirements.requires_exact_index
            && self.requires_bitmap_filter == requirements.requires_bitmap_filter
            && required_component_hash_shape(self.requires_lexical_index, &self.lexical_index_hash)
            && required_component_hash_shape(self.requires_exact_index, &self.exact_index_hash)
            && required_component_hash_shape(self.requires_bitmap_filter, &self.bitmap_filter_hash)
            && self.manifest_hash == agentic_evidence_sdk_manifest_hash(self)
            && nonzero_hash(&self.manifest_hash)
    }
}

impl AgenticEvidenceSdkRun {
    pub fn is_valid_for_program(&self, program: &AgenticEvidenceProgram) -> bool {
        self.manifest.is_valid_for_program(program)
            && self.execution_record.is_valid()
            && self
                .execution_record
                .matches_candidates(program, self.capsule.candidates())
            && self
                .capsule
                .is_valid_for_replay_records(None, Some(&self.execution_record))
            && self.run_hash
                == agentic_evidence_sdk_run_hash(
                    self.manifest.manifest_hash,
                    self.execution_record.record_hash,
                    self.capsule.capsule_hash,
                )
            && nonzero_hash(&self.run_hash)
    }
}

impl AgenticEvidenceSdk {
    pub fn run_program(
        program: &AgenticEvidenceProgram,
        capsule_id: u128,
        lexical_index: Option<&HotLexicalIndex>,
        exact_index: Option<&HotEvidenceIndex>,
        bitmap_filter: Option<&HotBitmapFilter>,
    ) -> Result<AgenticEvidenceSdkRun, AgenticEvidenceSdkError> {
        if capsule_id == 0 {
            return Err(AgenticEvidenceSdkError::InvalidCapsuleId);
        }
        let manifest = AgenticEvidenceSdkManifest::for_program(
            program,
            lexical_index,
            exact_index,
            bitmap_filter,
        )?;
        let (candidates, execution_record) = program
            .execute(lexical_index, exact_index, bitmap_filter)
            .map_err(AgenticEvidenceSdkError::Program)?;
        if candidates.is_empty() {
            return Err(AgenticEvidenceSdkError::EmptyCandidateSet);
        }
        let capsule =
            CandidateStateCapsule::new(capsule_id, &candidates, None, Some(&execution_record))
                .map_err(AgenticEvidenceSdkError::Capsule)?;
        let run_hash = agentic_evidence_sdk_run_hash(
            manifest.manifest_hash,
            execution_record.record_hash,
            capsule.capsule_hash,
        );
        Ok(AgenticEvidenceSdkRun {
            manifest,
            execution_record,
            capsule,
            run_hash,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AgenticEvidenceSdkRequirements {
    requires_lexical_index: bool,
    requires_exact_index: bool,
    requires_bitmap_filter: bool,
    primitive_hash: [u8; 32],
}

impl AgenticEvidenceSdkRequirements {
    fn from_steps(steps: &[AgenticEvidenceProgramStep]) -> Self {
        let mut requires_lexical_index = false;
        let mut requires_exact_index = false;
        let mut requires_bitmap_filter = false;
        for step in steps {
            match step {
                AgenticEvidenceProgramStep::LexicalTopK { .. } => requires_lexical_index = true,
                AgenticEvidenceProgramStep::ExactArtifactRerank { .. } => {
                    requires_exact_index = true
                }
                AgenticEvidenceProgramStep::AstSignatureLookup { .. } => {
                    requires_exact_index = true
                }
                AgenticEvidenceProgramStep::BitmapFilter { .. } => requires_bitmap_filter = true,
            }
        }
        let primitive_hash = agentic_evidence_required_primitive_hash(
            steps,
            requires_lexical_index,
            requires_exact_index,
            requires_bitmap_filter,
        );
        Self {
            requires_lexical_index,
            requires_exact_index,
            requires_bitmap_filter,
            primitive_hash,
        }
    }
}

impl BrowserPageSearchCandidateRecord {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        browser_observation_packet_hash: [u8; 32],
        dom_artifact_hash: [u8; 32],
        pattern_hash: [u8; 32],
        index_epoch_hash: [u8; 32],
        limit: usize,
        match_count: u32,
        candidates: &[CandidateEvidenceRef],
    ) -> Result<Self, BrowserPageSearchCandidateError> {
        if !nonzero_hash(&browser_observation_packet_hash) {
            return Err(BrowserPageSearchCandidateError::InvalidPacket);
        }
        if !nonzero_hash(&dom_artifact_hash) {
            return Err(BrowserPageSearchCandidateError::InvalidDomArtifact);
        }
        if !nonzero_hash(&pattern_hash) || !nonzero_hash(&index_epoch_hash) || limit == 0 {
            return Err(BrowserPageSearchCandidateError::InvalidPattern);
        }
        if match_count == 0 || candidates.is_empty() {
            return Err(BrowserPageSearchCandidateError::NoMatches);
        }
        if candidates.len() > limit {
            return Err(BrowserPageSearchCandidateError::CandidateLimitExceeded);
        }
        for candidate in candidates {
            if !candidate.is_valid() {
                return Err(BrowserPageSearchCandidateError::InvalidCandidate);
            }
            if candidate.index_epoch_hash != index_epoch_hash {
                return Err(BrowserPageSearchCandidateError::InvalidCandidate);
            }
            if candidate.origin_tier != EvidenceCandidateTier::BrowserPageSearch {
                return Err(BrowserPageSearchCandidateError::CandidateTierMismatch);
            }
        }
        let mut record = Self {
            browser_observation_packet_hash,
            dom_artifact_hash,
            pattern_hash,
            index_epoch_hash,
            limit: limit.min(u32::MAX as usize) as u32,
            match_count,
            candidate_count: candidates.len().min(u32::MAX as usize) as u32,
            candidate_list_hash: candidate_list_hash(candidates),
            record_hash: [0; 32],
        };
        record.record_hash = browser_page_search_candidate_record_hash(&record);
        Ok(record)
    }

    pub fn is_valid(&self) -> bool {
        nonzero_hash(&self.browser_observation_packet_hash)
            && nonzero_hash(&self.dom_artifact_hash)
            && nonzero_hash(&self.pattern_hash)
            && nonzero_hash(&self.index_epoch_hash)
            && self.limit > 0
            && self.match_count > 0
            && self.candidate_count > 0
            && nonzero_hash(&self.candidate_list_hash)
            && self.record_hash == browser_page_search_candidate_record_hash(self)
    }

    pub fn matches_candidates(&self, candidates: &[CandidateEvidenceRef]) -> bool {
        self.is_valid()
            && self.candidate_count == candidates.len().min(u32::MAX as usize) as u32
            && self.candidate_list_hash == candidate_list_hash(candidates)
            && candidates.iter().all(|candidate| {
                candidate.is_valid()
                    && candidate.origin_tier == EvidenceCandidateTier::BrowserPageSearch
                    && candidate.index_epoch_hash == self.index_epoch_hash
            })
    }
}

impl CandidateStateCapsule {
    pub fn new(
        capsule_id: u128,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
    ) -> Result<Self, CandidateStateCapsuleError> {
        if capsule_id == 0 || candidates.is_empty() {
            return Err(CandidateStateCapsuleError::InvalidCapsule);
        }
        CandidateOnlyGate::validate_replay_bound_context_pack_candidates(
            candidates,
            cold_vector_record,
            agentic_record,
        )
        .map_err(CandidateStateCapsuleError::CandidateGate)?;
        let index_epoch_hash = shared_candidate_epoch(candidates)?;
        let cold_vector_replay_record_hash =
            cold_vector_record_hash_for_capsule(candidates, cold_vector_record)?;
        let agentic_evidence_execution_record_hash =
            agentic_record_hash_for_capsule(candidates, agentic_record)?;
        let candidate_list_hash = candidate_list_hash(candidates);
        let payload = candidate_state_capsule_payload(
            capsule_id,
            index_epoch_hash,
            candidate_list_hash,
            cold_vector_replay_record_hash,
            agentic_evidence_execution_record_hash,
            candidates,
        );
        let payload_hash = blake3_digest_bytes(&payload);
        let payload_byte_len = payload.len().min(u32::MAX as usize) as u32;
        let capsule_hash = candidate_state_capsule_hash(
            capsule_id,
            index_epoch_hash,
            candidates.len().min(u32::MAX as usize) as u32,
            candidate_list_hash,
            cold_vector_replay_record_hash,
            agentic_evidence_execution_record_hash,
            payload_byte_len,
            payload_hash,
        );
        Ok(Self {
            capsule_id,
            index_epoch_hash,
            candidate_count: candidates.len().min(u32::MAX as usize) as u32,
            candidate_list_hash,
            cold_vector_replay_record_hash,
            agentic_evidence_execution_record_hash,
            payload_byte_len,
            payload_hash,
            capsule_hash,
            candidates: candidates.to_vec(),
            payload,
        })
    }

    pub fn from_canonical_bytes(
        payload: &[u8],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
    ) -> Result<Self, CandidateStateCapsuleError> {
        let decoded = decode_candidate_state_capsule_payload(payload)?;
        let capsule = Self::new(
            decoded.capsule_id,
            &decoded.candidates,
            cold_vector_record,
            agentic_record,
        )?;
        if capsule.index_epoch_hash != decoded.index_epoch_hash
            || capsule.candidate_count != decoded.candidate_count
            || capsule.candidate_list_hash != decoded.candidate_list_hash
            || capsule.cold_vector_replay_record_hash != decoded.cold_vector_replay_record_hash
            || capsule.agentic_evidence_execution_record_hash
                != decoded.agentic_evidence_execution_record_hash
            || capsule.payload.as_slice() != payload
        {
            return Err(CandidateStateCapsuleError::ReplayRecordMismatch);
        }
        Ok(capsule)
    }

    pub fn candidates(&self) -> &[CandidateEvidenceRef] {
        &self.candidates
    }

    pub fn canonical_bytes(&self) -> &[u8] {
        &self.payload
    }

    pub fn is_valid_for_replay_records(
        &self,
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
    ) -> bool {
        self.capsule_id != 0
            && self.candidate_count == self.candidates.len().min(u32::MAX as usize) as u32
            && self.candidate_count > 0
            && nonzero_hash(&self.index_epoch_hash)
            && self.candidate_list_hash == candidate_list_hash(&self.candidates)
            && self
                .candidates
                .iter()
                .all(|candidate| candidate.index_epoch_hash == self.index_epoch_hash)
            && cold_vector_record_hash_for_capsule(&self.candidates, cold_vector_record)
                == Ok(self.cold_vector_replay_record_hash)
            && agentic_record_hash_for_capsule(&self.candidates, agentic_record)
                == Ok(self.agentic_evidence_execution_record_hash)
            && self.payload_byte_len == self.payload.len().min(u32::MAX as usize) as u32
            && self.payload_hash == blake3_digest_bytes(&self.payload)
            && self.payload
                == candidate_state_capsule_payload(
                    self.capsule_id,
                    self.index_epoch_hash,
                    self.candidate_list_hash,
                    self.cold_vector_replay_record_hash,
                    self.agentic_evidence_execution_record_hash,
                    &self.candidates,
                )
            && self.capsule_hash
                == candidate_state_capsule_hash(
                    self.capsule_id,
                    self.index_epoch_hash,
                    self.candidate_count,
                    self.candidate_list_hash,
                    self.cold_vector_replay_record_hash,
                    self.agentic_evidence_execution_record_hash,
                    self.payload_byte_len,
                    self.payload_hash,
                )
            && nonzero_hash(&self.capsule_hash)
    }
}

impl HotLexicalIndex {
    pub fn new(index_epoch_hash: [u8; 32]) -> Result<Self, LexicalIndexError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(LexicalIndexError::InvalidIndexEpoch);
        }
        Ok(Self {
            index_epoch_hash,
            docs: Vec::new(),
            doc_index_by_key: BTreeMap::new(),
            postings_by_token: BTreeMap::new(),
            index_hash: empty_agentic_evidence_lexical_index_hash(index_epoch_hash),
        })
    }

    pub fn document_count(&self) -> usize {
        self.docs.len()
    }

    pub fn indexed_token_count(&self) -> usize {
        self.postings_by_token.len()
    }

    pub fn query_scratch(&self) -> HotLexicalQueryScratch {
        HotLexicalQueryScratch::with_document_capacity(self.docs.len())
    }

    pub fn insert_document(
        &mut self,
        evidence_ref_hash: [u8; 32],
        segment_id: u64,
        terms: &[&str],
    ) -> Result<(), LexicalIndexError> {
        if !nonzero_hash(&evidence_ref_hash) {
            return Err(LexicalIndexError::InvalidDocument);
        }

        let mut frequencies = BTreeMap::<u64, u16>::new();
        for term in terms {
            if let Some(token_hash) = lexical_token_hash(term) {
                let count = frequencies.entry(token_hash).or_insert(0);
                *count = count.saturating_add(1);
            }
        }
        if frequencies.is_empty() {
            return Err(LexicalIndexError::EmptyTerms);
        }

        let doc_key = LexicalDocKey {
            evidence_ref_hash,
            segment_id,
        };
        if self.doc_index_by_key.contains_key(&doc_key) {
            return Err(LexicalIndexError::DuplicateDocument);
        }
        let doc_index = self.docs.len();
        self.docs.push(LexicalDocMeta {
            evidence_ref_hash,
            segment_id,
        });
        self.doc_index_by_key.insert(doc_key, doc_index);
        for (token_hash, term_frequency) in frequencies {
            self.postings_by_token
                .entry(token_hash)
                .or_default()
                .push(LexicalPosting {
                    doc_index,
                    term_frequency,
                });
        }
        self.index_hash = compute_agentic_evidence_lexical_index_hash(self);
        Ok(())
    }

    pub fn query_top_k(&self, query_terms: &[&str], limit: usize) -> Vec<CandidateEvidenceRef> {
        let mut scratch = self.query_scratch();
        self.query_top_k_with_scratch(query_terms, limit, &mut scratch)
    }

    pub fn query_top_k_with_scratch(
        &self,
        query_terms: &[&str],
        limit: usize,
        scratch: &mut HotLexicalQueryScratch,
    ) -> Vec<CandidateEvidenceRef> {
        self.query_top_k_into_scratch(query_terms, limit, scratch)
            .to_vec()
    }

    pub fn query_top_k_into_scratch<'a>(
        &self,
        query_terms: &[&str],
        limit: usize,
        scratch: &'a mut HotLexicalQueryScratch,
    ) -> &'a [CandidateEvidenceRef] {
        scratch.prepare_for_query(self.docs.len(), limit);
        if limit == 0 {
            return &scratch.results;
        }
        normalized_query_tokens_into(query_terms, &mut scratch.query_tokens);
        if scratch.query_tokens.is_empty() {
            return &scratch.results;
        }

        for token_hash in &scratch.query_tokens {
            if let Some(postings) = self.postings_by_token.get(token_hash) {
                for posting in postings {
                    if scratch.score_epochs[posting.doc_index] != scratch.query_epoch {
                        scratch.score_epochs[posting.doc_index] = scratch.query_epoch;
                        scratch.scores[posting.doc_index] = posting.term_frequency as u32;
                        scratch.touched_doc_indices.push(posting.doc_index);
                    } else {
                        scratch.scores[posting.doc_index] = scratch.scores[posting.doc_index]
                            .saturating_add(posting.term_frequency as u32);
                    }
                }
            }
        }

        let query_token_count = scratch.query_tokens.len().max(1) as u32;
        let score_unit = (CANDIDATE_SCORE_PPM_MAX / query_token_count).max(1);
        for doc_index in scratch.touched_doc_indices.iter().copied() {
            if scratch.top_doc_indices.len() < limit {
                push_ranked_doc(
                    &mut scratch.top_doc_indices,
                    doc_index,
                    &scratch.scores,
                    &self.docs,
                );
            } else if scratch.top_doc_indices.last().is_some_and(|worst| {
                lexical_rank_order(doc_index, *worst, &scratch.scores, &self.docs).is_lt()
            }) {
                let last_index = scratch.top_doc_indices.len() - 1;
                scratch.top_doc_indices[last_index] = doc_index;
                bubble_ranked_doc(
                    &mut scratch.top_doc_indices,
                    last_index,
                    &scratch.scores,
                    &self.docs,
                );
            }
        }

        for doc_index in scratch.top_doc_indices.iter().copied() {
            if let Some(doc) = self.docs.get(doc_index) {
                let raw_score = scratch.scores[doc_index];
                let bounded_score = raw_score.min(query_token_count).saturating_mul(score_unit);
                scratch.results.push(CandidateEvidenceRef::new(
                    doc.evidence_ref_hash,
                    doc.segment_id,
                    EvidenceCandidateTier::LexicalBaseline,
                    bounded_score.min(CANDIDATE_SCORE_PPM_MAX),
                    self.index_epoch_hash,
                ));
            }
        }
        &scratch.results
    }
}

impl HotEvidenceIndex {
    pub fn new(index_epoch_hash: [u8; 32]) -> Result<Self, HotEvidenceIndexError> {
        if !nonzero_hash(&index_epoch_hash) {
            return Err(HotEvidenceIndexError::InvalidIndexEpoch);
        }
        Ok(Self {
            index_epoch_hash,
            docs: Vec::new(),
            doc_index_by_key: BTreeMap::new(),
            doc_indices_by_segment: BTreeMap::new(),
            doc_indices_by_ast_signature: BTreeMap::new(),
            document_texts: Vec::new(),
            index_hash: empty_agentic_evidence_exact_index_hash(index_epoch_hash),
        })
    }

    pub fn document_count(&self) -> usize {
        self.docs.len()
    }

    pub fn query_scratch(&self) -> HotEvidenceQueryScratch {
        HotEvidenceQueryScratch::with_document_capacity(self.docs.len())
    }

    pub fn insert_artifact_document(
        &mut self,
        evidence_ref_hash: [u8; 32],
        segment_id: u64,
        artifact_hash: [u8; 32],
        ast_signature_hash: [u8; 32],
        document_text: &str,
    ) -> Result<(), HotEvidenceIndexError> {
        if !nonzero_hash(&evidence_ref_hash) || document_text.trim().is_empty() {
            return Err(HotEvidenceIndexError::InvalidDocument);
        }
        if !nonzero_hash(&artifact_hash) || !nonzero_hash(&ast_signature_hash) {
            return Err(HotEvidenceIndexError::InvalidArtifactBinding);
        }
        let key = LexicalDocKey {
            evidence_ref_hash,
            segment_id,
        };
        if self.doc_index_by_key.contains_key(&key) {
            return Err(HotEvidenceIndexError::DuplicateDocument);
        }

        let binding_hash = hot_evidence_document_binding_hash(
            evidence_ref_hash,
            artifact_hash,
            ast_signature_hash,
        );
        let content_hash = blake3_digest_bytes(document_text.as_bytes());
        let meta = HotEvidenceDocMeta {
            evidence_ref_hash,
            segment_id,
            artifact_hash,
            ast_signature_hash,
            content_hash,
            binding_hash,
        };
        if !meta.is_valid() {
            return Err(HotEvidenceIndexError::InvalidDocument);
        }

        let doc_index = self.docs.len();
        self.docs.push(meta);
        self.document_texts
            .push(lowercase_ascii_bytes(document_text).into_boxed_slice());
        self.doc_index_by_key.insert(key, doc_index);
        self.doc_indices_by_segment
            .entry(segment_id)
            .or_default()
            .push(doc_index);
        self.doc_indices_by_ast_signature
            .entry(ast_signature_hash)
            .or_default()
            .push(doc_index);
        self.index_hash = compute_agentic_evidence_exact_index_hash(self);
        Ok(())
    }

    pub fn query_exact_literals(
        &self,
        patterns: &[&str],
        allowed_segments: Option<&SortedEvidenceSet>,
        limit: usize,
    ) -> Vec<CandidateEvidenceRef> {
        let mut scratch = self.query_scratch();
        self.query_exact_literals_with_scratch(patterns, allowed_segments, limit, &mut scratch)
    }

    pub fn query_exact_literals_with_scratch(
        &self,
        patterns: &[&str],
        allowed_segments: Option<&SortedEvidenceSet>,
        limit: usize,
        scratch: &mut HotEvidenceQueryScratch,
    ) -> Vec<CandidateEvidenceRef> {
        self.query_exact_literals_into_scratch(patterns, allowed_segments, limit, scratch)
            .to_vec()
    }

    pub fn query_exact_literals_into_scratch<'a>(
        &self,
        patterns: &[&str],
        allowed_segments: Option<&SortedEvidenceSet>,
        limit: usize,
        scratch: &'a mut HotEvidenceQueryScratch,
    ) -> &'a [CandidateEvidenceRef] {
        scratch.prepare_for_query(self.docs.len(), limit);
        if limit == 0 {
            return &scratch.results;
        }
        normalized_exact_patterns_into(patterns, &mut scratch.normalized_patterns);
        if scratch.normalized_patterns.is_empty() {
            return &scratch.results;
        }
        scratch.prepare_pattern_epochs();

        let automaton = match AhoCorasick::new(&scratch.normalized_patterns) {
            Ok(automaton) => automaton,
            Err(_) => return &scratch.results,
        };
        let pattern_count = scratch.normalized_patterns.len().max(1) as u32;

        if let Some(allowed) = allowed_segments {
            for segment_id in allowed.values() {
                if let Some(doc_indices) = self.doc_indices_by_segment.get(segment_id) {
                    for doc_index in doc_indices.iter().copied() {
                        self.score_hot_doc_into_top(&automaton, doc_index, limit, scratch);
                    }
                }
            }
        } else {
            for doc_index in 0..self.docs.len() {
                self.score_hot_doc_into_top(&automaton, doc_index, limit, scratch);
            }
        }

        for doc_index in scratch.top_doc_indices.iter().copied() {
            if let Some(doc) = self.docs.get(doc_index) {
                let score = ((scratch.scores[doc_index] as u64)
                    .saturating_mul(CANDIDATE_SCORE_PPM_MAX as u64)
                    / pattern_count as u64)
                    .min(CANDIDATE_SCORE_PPM_MAX as u64) as u32;
                scratch.results.push(CandidateEvidenceRef::new(
                    doc.binding_hash,
                    doc.segment_id,
                    EvidenceCandidateTier::ExactSimdRerank,
                    score,
                    self.index_epoch_hash,
                ));
            }
        }
        &scratch.results
    }

    pub fn query_ast_signatures(
        &self,
        ast_signature_hashes: &[[u8; 32]],
        allowed_segments: Option<&SortedEvidenceSet>,
        limit: usize,
    ) -> Vec<CandidateEvidenceRef> {
        let mut scratch = self.query_scratch();
        self.query_ast_signatures_with_scratch(
            ast_signature_hashes,
            allowed_segments,
            limit,
            &mut scratch,
        )
    }

    pub fn query_ast_signatures_with_scratch(
        &self,
        ast_signature_hashes: &[[u8; 32]],
        allowed_segments: Option<&SortedEvidenceSet>,
        limit: usize,
        scratch: &mut HotEvidenceQueryScratch,
    ) -> Vec<CandidateEvidenceRef> {
        self.query_ast_signatures_into_scratch(
            ast_signature_hashes,
            allowed_segments,
            limit,
            scratch,
        )
        .to_vec()
    }

    pub fn query_ast_signatures_into_scratch<'a>(
        &self,
        ast_signature_hashes: &[[u8; 32]],
        allowed_segments: Option<&SortedEvidenceSet>,
        limit: usize,
        scratch: &'a mut HotEvidenceQueryScratch,
    ) -> &'a [CandidateEvidenceRef] {
        scratch.prepare_for_query(self.docs.len(), limit);
        if limit == 0 {
            return &scratch.results;
        }
        normalize_ast_signature_hashes_into(
            ast_signature_hashes,
            &mut scratch.ast_signature_hashes,
        );
        if scratch.ast_signature_hashes.is_empty() {
            return &scratch.results;
        }

        for signature_index in 0..scratch.ast_signature_hashes.len() {
            let signature_hash = scratch.ast_signature_hashes[signature_index];
            if let Some(doc_indices) = self.doc_indices_by_ast_signature.get(&signature_hash) {
                for doc_index in doc_indices.iter().copied() {
                    if allowed_segments.is_some_and(|allowed| {
                        self.docs
                            .get(doc_index)
                            .is_none_or(|doc| !allowed.contains(doc.segment_id))
                    }) {
                        continue;
                    }
                    self.push_ast_doc_into_top(doc_index, limit, scratch);
                }
            }
        }

        for doc_index in scratch.top_doc_indices.iter().copied() {
            if let Some(doc) = self.docs.get(doc_index) {
                scratch.results.push(CandidateEvidenceRef::new(
                    doc.binding_hash,
                    doc.segment_id,
                    EvidenceCandidateTier::AstSignatureMatch,
                    CANDIDATE_SCORE_PPM_MAX,
                    self.index_epoch_hash,
                ));
            }
        }
        &scratch.results
    }

    pub fn validates_artifact_binding(
        &self,
        candidate: &CandidateEvidenceRef,
        evidence_ref_hash: [u8; 32],
        artifact_hash: [u8; 32],
        ast_signature_hash: [u8; 32],
    ) -> bool {
        if !candidate.is_valid() || candidate.index_epoch_hash != self.index_epoch_hash {
            return false;
        }
        let binding_hash = hot_evidence_document_binding_hash(
            evidence_ref_hash,
            artifact_hash,
            ast_signature_hash,
        );
        if candidate.evidence_ref_hash != binding_hash {
            return false;
        }
        self.docs.iter().any(|doc| {
            doc.binding_hash == binding_hash
                && doc.segment_id == candidate.segment_id
                && doc.evidence_ref_hash == evidence_ref_hash
                && doc.artifact_hash == artifact_hash
                && doc.ast_signature_hash == ast_signature_hash
        })
    }

    fn score_hot_doc_into_top(
        &self,
        automaton: &AhoCorasick,
        doc_index: usize,
        limit: usize,
        scratch: &mut HotEvidenceQueryScratch,
    ) {
        let doc_epoch = scratch.next_doc_epoch();
        let mut unique_matches = 0u32;
        for matched in automaton.find_iter(&self.document_texts[doc_index]) {
            let pattern_index = matched.pattern().as_usize();
            if scratch.pattern_epochs[pattern_index] != doc_epoch {
                scratch.pattern_epochs[pattern_index] = doc_epoch;
                unique_matches = unique_matches.saturating_add(1);
            }
        }
        if unique_matches == 0 {
            return;
        }
        scratch.scores[doc_index] = unique_matches;
        if scratch.top_doc_indices.len() < limit {
            push_ranked_hot_evidence_doc(
                &mut scratch.top_doc_indices,
                doc_index,
                &scratch.scores,
                &self.docs,
            );
        } else if scratch.top_doc_indices.last().is_some_and(|worst| {
            hot_evidence_rank_order(doc_index, *worst, &scratch.scores, &self.docs).is_lt()
        }) {
            let last_index = scratch.top_doc_indices.len() - 1;
            scratch.top_doc_indices[last_index] = doc_index;
            bubble_ranked_hot_evidence_doc(
                &mut scratch.top_doc_indices,
                last_index,
                &scratch.scores,
                &self.docs,
            );
        }
    }

    fn push_ast_doc_into_top(
        &self,
        doc_index: usize,
        limit: usize,
        scratch: &mut HotEvidenceQueryScratch,
    ) {
        if doc_index >= self.docs.len() || limit == 0 {
            return;
        }
        scratch.scores[doc_index] = CANDIDATE_SCORE_PPM_MAX;
        if scratch.top_doc_indices.contains(&doc_index) {
            return;
        }
        if scratch.top_doc_indices.len() < limit {
            push_ranked_hot_evidence_doc(
                &mut scratch.top_doc_indices,
                doc_index,
                &scratch.scores,
                &self.docs,
            );
        } else if scratch.top_doc_indices.last().is_some_and(|worst| {
            hot_evidence_rank_order(doc_index, *worst, &scratch.scores, &self.docs).is_lt()
        }) {
            let last_index = scratch.top_doc_indices.len() - 1;
            scratch.top_doc_indices[last_index] = doc_index;
            bubble_ranked_hot_evidence_doc(
                &mut scratch.top_doc_indices,
                last_index,
                &scratch.scores,
                &self.docs,
            );
        }
    }
}

impl HotLexicalQueryScratch {
    pub fn with_document_capacity(document_count: usize) -> Self {
        Self {
            query_tokens: Vec::new(),
            scores: vec![0; document_count],
            score_epochs: vec![0; document_count],
            query_epoch: 0,
            touched_doc_indices: Vec::new(),
            top_doc_indices: Vec::new(),
            results: Vec::new(),
        }
    }

    fn prepare_for_query(&mut self, document_count: usize, limit: usize) {
        if self.scores.len() < document_count {
            self.scores.resize(document_count, 0);
            self.score_epochs.resize(document_count, 0);
        }
        self.query_epoch = self.query_epoch.wrapping_add(1);
        if self.query_epoch == 0 {
            self.score_epochs.fill(0);
            self.query_epoch = 1;
        }
        self.touched_doc_indices.clear();
        self.top_doc_indices.clear();
        if self.top_doc_indices.capacity() < limit {
            self.top_doc_indices.reserve(limit);
        }
        self.query_tokens.clear();
        if self.query_tokens.capacity() < limit {
            self.query_tokens.reserve(limit);
        }
        self.results.clear();
        if self.results.capacity() < limit {
            self.results.reserve(limit);
        }
    }
}

impl HotEvidenceQueryScratch {
    pub fn with_document_capacity(document_count: usize) -> Self {
        Self {
            normalized_patterns: Vec::new(),
            ast_signature_hashes: Vec::new(),
            scores: vec![0; document_count],
            top_doc_indices: Vec::new(),
            pattern_epochs: Vec::new(),
            doc_epoch: 0,
            results: Vec::new(),
        }
    }

    fn prepare_for_query(&mut self, document_count: usize, limit: usize) {
        if self.scores.len() < document_count {
            self.scores.resize(document_count, 0);
        }
        self.top_doc_indices.clear();
        if self.top_doc_indices.capacity() < limit {
            self.top_doc_indices.reserve(limit);
        }
        self.normalized_patterns.clear();
        if self.normalized_patterns.capacity() < limit {
            self.normalized_patterns.reserve(limit);
        }
        self.ast_signature_hashes.clear();
        if self.ast_signature_hashes.capacity() < limit {
            self.ast_signature_hashes.reserve(limit);
        }
        self.results.clear();
        if self.results.capacity() < limit {
            self.results.reserve(limit);
        }
    }

    fn prepare_pattern_epochs(&mut self) {
        if self.pattern_epochs.len() < self.normalized_patterns.len() {
            self.pattern_epochs
                .resize(self.normalized_patterns.len(), 0);
        }
    }

    fn next_doc_epoch(&mut self) -> u32 {
        self.doc_epoch = self.doc_epoch.wrapping_add(1);
        if self.doc_epoch == 0 {
            self.pattern_epochs.fill(0);
            self.doc_epoch = 1;
        }
        self.doc_epoch
    }
}

impl ColdVectorQueryScratch {
    fn with_capacity(document_count: usize) -> Self {
        Self {
            scored_doc_indices: Vec::new(),
            scores: vec![0; document_count],
            results: Vec::new(),
        }
    }

    fn prepare_for_query(&mut self, document_count: usize, limit: usize) {
        if self.scores.len() < document_count {
            self.scores.resize(document_count, 0);
        }
        self.scored_doc_indices.clear();
        if self.scored_doc_indices.capacity() < limit {
            self.scored_doc_indices.reserve(limit);
        }
        self.results.clear();
        if self.results.capacity() < limit {
            self.results.reserve(limit);
        }
    }
}

impl AgenticEvidenceProgramScratch {
    fn prepare(&mut self) {
        self.working_candidates.clear();
        self.trace_hashes.clear();
        self.exact_allowed_segments.clear();
        self.filter_output.clear();
        self.fuel_used = 0;
    }

    fn execution_record(&self, program: &AgenticEvidenceProgram) -> AgenticEvidenceExecutionRecord {
        AgenticEvidenceExecutionRecord::new(
            program.program_hash,
            program.index_epoch_hash,
            program.steps.len(),
            self.fuel_used,
            &self.working_candidates,
            &self.trace_hashes,
        )
        .expect("program execution builds a valid replay record")
    }
}

impl HotEvidenceDocMeta {
    fn is_valid(&self) -> bool {
        nonzero_hash(&self.evidence_ref_hash)
            && nonzero_hash(&self.artifact_hash)
            && nonzero_hash(&self.ast_signature_hash)
            && nonzero_hash(&self.content_hash)
            && nonzero_hash(&self.binding_hash)
    }
}

impl ColdVectorDocMeta {
    fn is_valid(&self) -> bool {
        nonzero_hash(&self.evidence_ref_hash)
            && nonzero_hash(&self.artifact_hash)
            && nonzero_hash(&self.vector_hash)
            && self.norm_sq > 0
            && nonzero_hash(&self.binding_hash)
            && self.binding_hash
                == cold_vector_document_binding_hash(
                    self.evidence_ref_hash,
                    self.artifact_hash,
                    self.vector_hash,
                )
    }
}

fn push_ranked_doc(
    top_doc_indices: &mut Vec<usize>,
    doc_index: usize,
    scores: &[u32],
    docs: &[LexicalDocMeta],
) {
    top_doc_indices.push(doc_index);
    let last_index = top_doc_indices.len() - 1;
    bubble_ranked_doc(top_doc_indices, last_index, scores, docs);
}

fn push_ranked_vector_doc(
    top_doc_indices: &mut Vec<usize>,
    doc_index: usize,
    scores: &[u32],
    docs: &[ColdVectorDocMeta],
) {
    top_doc_indices.push(doc_index);
    let mut index = top_doc_indices.len() - 1;
    while index > 0 {
        let current_doc_index = top_doc_indices[index];
        let previous_doc_index = top_doc_indices[index - 1];
        let current = &docs[current_doc_index];
        let previous = &docs[previous_doc_index];
        let current_key = (
            std::cmp::Reverse(scores[current_doc_index]),
            current.segment_id,
            current.evidence_ref_hash,
        );
        let previous_key = (
            std::cmp::Reverse(scores[previous_doc_index]),
            previous.segment_id,
            previous.evidence_ref_hash,
        );
        if current_key >= previous_key {
            break;
        }
        top_doc_indices.swap(index, index - 1);
        index -= 1;
    }
}

fn bubble_ranked_doc(
    top_doc_indices: &mut [usize],
    mut index: usize,
    scores: &[u32],
    docs: &[LexicalDocMeta],
) {
    while index > 0
        && lexical_rank_order(
            top_doc_indices[index],
            top_doc_indices[index - 1],
            scores,
            docs,
        )
        .is_lt()
    {
        top_doc_indices.swap(index, index - 1);
        index -= 1;
    }
}

fn lexical_rank_order(
    left: usize,
    right: usize,
    scores: &[u32],
    docs: &[LexicalDocMeta],
) -> std::cmp::Ordering {
    scores[right]
        .cmp(&scores[left])
        .then_with(|| doc_key_from_meta(&docs[left]).cmp(&doc_key_from_meta(&docs[right])))
}

fn push_ranked_hot_evidence_doc(
    top_doc_indices: &mut Vec<usize>,
    doc_index: usize,
    scores: &[u32],
    docs: &[HotEvidenceDocMeta],
) {
    top_doc_indices.push(doc_index);
    let last_index = top_doc_indices.len() - 1;
    bubble_ranked_hot_evidence_doc(top_doc_indices, last_index, scores, docs);
}

fn bubble_ranked_hot_evidence_doc(
    top_doc_indices: &mut [usize],
    mut index: usize,
    scores: &[u32],
    docs: &[HotEvidenceDocMeta],
) {
    while index > 0
        && hot_evidence_rank_order(
            top_doc_indices[index],
            top_doc_indices[index - 1],
            scores,
            docs,
        )
        .is_lt()
    {
        top_doc_indices.swap(index, index - 1);
        index -= 1;
    }
}

fn hot_evidence_rank_order(
    left: usize,
    right: usize,
    scores: &[u32],
    docs: &[HotEvidenceDocMeta],
) -> std::cmp::Ordering {
    scores[right]
        .cmp(&scores[left])
        .then_with(|| hot_evidence_doc_key(&docs[left]).cmp(&hot_evidence_doc_key(&docs[right])))
}

fn hot_evidence_doc_key(meta: &HotEvidenceDocMeta) -> LexicalDocKey {
    LexicalDocKey {
        evidence_ref_hash: meta.binding_hash,
        segment_id: meta.segment_id,
    }
}

fn doc_key_from_meta(meta: &LexicalDocMeta) -> LexicalDocKey {
    LexicalDocKey {
        evidence_ref_hash: meta.evidence_ref_hash,
        segment_id: meta.segment_id,
    }
}

pub fn hot_evidence_document_binding_hash(
    evidence_ref_hash: [u8; 32],
    artifact_hash: [u8; 32],
    ast_signature_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-hot-evidence-document-binding-v1");
    hasher.update(&evidence_ref_hash);
    hasher.update(&artifact_hash);
    hasher.update(&ast_signature_hash);
    hasher.finalize().into()
}

pub fn cold_vector_payload_hash(vector: &[i16]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cold-vector-payload-v1");
    update_u64(&mut hasher, vector.len() as u64);
    for value in vector {
        hasher.update(&value.to_le_bytes());
    }
    hasher.finalize().into()
}

pub fn cold_vector_query_hash(query_vector: &[i16], limit: usize) -> Option<[u8; 32]> {
    if query_vector.is_empty() || query_vector.iter().all(|value| *value == 0) || limit == 0 {
        return None;
    }
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cold-vector-query-v1");
    update_u64(&mut hasher, limit as u64);
    update_hash(&mut hasher, &cold_vector_payload_hash(query_vector));
    Some(hasher.finalize().into())
}

pub fn cold_vector_document_binding_hash(
    evidence_ref_hash: [u8; 32],
    artifact_hash: [u8; 32],
    vector_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cold-vector-document-binding-v1");
    update_hash(&mut hasher, &evidence_ref_hash);
    update_hash(&mut hasher, &artifact_hash);
    update_hash(&mut hasher, &vector_hash);
    hasher.finalize().into()
}

pub fn cold_vector_expansion_config_hash(index_epoch_hash: [u8; 32], dimension: usize) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cold-vector-expansion-config-v1");
    update_hash(&mut hasher, &index_epoch_hash);
    update_u64(&mut hasher, dimension as u64);
    hasher.update(b"metric=deterministic-squared-cosine-i16");
    hasher.finalize().into()
}

fn cold_vector_index_hash(
    index_epoch_hash: [u8; 32],
    dimension: usize,
    vectors: &[ColdVectorDocMeta],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cold-vector-index-v1");
    update_hash(&mut hasher, &index_epoch_hash);
    update_u64(&mut hasher, dimension as u64);
    update_u64(&mut hasher, vectors.len() as u64);
    for vector in vectors {
        update_hash(&mut hasher, &vector.evidence_ref_hash);
        update_u64(&mut hasher, vector.segment_id);
        update_hash(&mut hasher, &vector.artifact_hash);
        update_hash(&mut hasher, &vector.vector_hash);
        update_u64(&mut hasher, vector.norm_sq);
        update_hash(&mut hasher, &vector.binding_hash);
    }
    hasher.finalize().into()
}

pub fn index_epoch_hash(inputs: &IndexEpochInputs) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-index-epoch-v1");
    update_hash(&mut hasher, &inputs.segment_catalog_hash);
    update_hash(&mut hasher, &inputs.tokenizer_config_hash);
    update_hash(&mut hasher, &inputs.schema_hash);
    update_hash(&mut hasher, &inputs.redaction_policy_hash);
    update_hash(&mut hasher, &inputs.embedding_model_hash_or_zero);
    update_hash(&mut hasher, &inputs.feature_profile_hash);
    hasher.finalize().into()
}

pub fn candidate_evidence_ref_hash(candidate: &CandidateEvidenceRef) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-candidate-evidence-ref-v1");
    update_hash(&mut hasher, &candidate.evidence_ref_hash);
    update_u64(&mut hasher, candidate.segment_id);
    hasher.update(&[candidate.origin_tier as u8]);
    update_u32(&mut hasher, candidate.score_quantized);
    update_hash(&mut hasher, &candidate.index_epoch_hash);
    hasher.finalize().into()
}

pub fn lexical_query_hash(query_terms: &[&str], limit: usize) -> Option<[u8; 32]> {
    let query_tokens = normalized_query_tokens(query_terms);
    if query_tokens.is_empty() {
        return None;
    }

    let mut hasher = Hasher::new();
    hasher.update(b"aegis-lexical-query-v1");
    update_u64(&mut hasher, limit as u64);
    update_u64(&mut hasher, query_tokens.len() as u64);
    for token_hash in query_tokens {
        update_u64(&mut hasher, token_hash);
    }
    Some(hasher.finalize().into())
}

fn normalized_query_tokens(query_terms: &[&str]) -> Vec<u64> {
    let mut query_tokens = Vec::new();
    normalized_query_tokens_into(query_terms, &mut query_tokens);
    query_tokens
}

fn normalized_query_tokens_into(query_terms: &[&str], query_tokens: &mut Vec<u64>) {
    query_tokens.clear();
    query_tokens.extend(
        query_terms
            .iter()
            .filter_map(|term| lexical_token_hash(term)),
    );
    query_tokens.sort_unstable();
    query_tokens.dedup();
}

fn owned_terms_as_refs(terms: &[String]) -> Vec<&str> {
    terms.iter().map(String::as_str).collect()
}

fn normalized_owned_terms(terms: &[String]) -> Vec<u64> {
    let mut query_tokens = Vec::new();
    query_tokens.extend(terms.iter().filter_map(|term| lexical_token_hash(term)));
    query_tokens.sort_unstable();
    query_tokens.dedup();
    query_tokens
}

fn normalized_owned_exact_patterns(patterns: &[String]) -> Vec<String> {
    let mut normalized_patterns = Vec::new();
    for pattern in patterns {
        let normalized = pattern.trim().to_ascii_lowercase();
        if !normalized.is_empty() {
            normalized_patterns.push(normalized);
        }
    }
    normalized_patterns.sort_unstable();
    normalized_patterns.dedup();
    normalized_patterns
}

fn normalized_exact_patterns_into(patterns: &[&str], normalized_patterns: &mut Vec<String>) {
    normalized_patterns.clear();
    for pattern in patterns {
        let normalized = pattern.trim().to_ascii_lowercase();
        if !normalized.is_empty() {
            normalized_patterns.push(normalized);
        }
    }
    normalized_patterns.sort_unstable();
    normalized_patterns.dedup();
}

fn normalized_ast_signature_hashes(ast_signature_hashes: &[[u8; 32]]) -> Vec<[u8; 32]> {
    let mut normalized = Vec::new();
    normalize_ast_signature_hashes_into(ast_signature_hashes, &mut normalized);
    normalized
}

fn normalize_ast_signature_hashes_into(
    ast_signature_hashes: &[[u8; 32]],
    normalized: &mut Vec<[u8; 32]>,
) {
    normalized.clear();
    normalized.extend(ast_signature_hashes.iter().copied().filter(nonzero_hash));
    normalized.sort_unstable();
    normalized.dedup();
}

fn lowercase_ascii_bytes(text: &str) -> Vec<u8> {
    text.bytes().map(|byte| byte.to_ascii_lowercase()).collect()
}

pub fn candidate_list_hash(candidates: &[CandidateEvidenceRef]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-candidate-list-v1");
    update_u64(&mut hasher, candidates.len() as u64);
    for candidate in candidates {
        update_hash(&mut hasher, &candidate.candidate_hash());
    }
    hasher.finalize().into()
}

pub fn cold_vector_expansion_replay_record_hash(
    record: &ColdVectorExpansionReplayRecord,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-cold-vector-expansion-replay-v1");
    update_hash(&mut hasher, &record.query_hash);
    update_hash(&mut hasher, &record.index_epoch_hash);
    update_hash(&mut hasher, &record.expansion_config_hash);
    update_hash(&mut hasher, &record.expansion_artifact_hash);
    update_u32(&mut hasher, record.limit);
    update_u64(&mut hasher, record.latency_ns);
    update_u32(&mut hasher, record.candidate_count);
    update_hash(&mut hasher, &record.candidate_list_hash);
    hasher.finalize().into()
}

pub fn agentic_evidence_program_hash(
    index_epoch_hash: [u8; 32],
    max_fuel: u32,
    steps: &[AgenticEvidenceProgramStep],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-program-v1");
    update_hash(&mut hasher, &index_epoch_hash);
    update_u32(&mut hasher, max_fuel);
    update_u64(&mut hasher, steps.len() as u64);
    for step in steps {
        update_agentic_evidence_step_hash(&mut hasher, step);
    }
    hasher.finalize().into()
}

pub fn agentic_evidence_step_trace_hash(
    program_hash: [u8; 32],
    step: &AgenticEvidenceProgramStep,
    fuel_used: u32,
    candidates: &[CandidateEvidenceRef],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-step-trace-v1");
    update_hash(&mut hasher, &program_hash);
    update_agentic_evidence_step_hash(&mut hasher, step);
    update_u32(&mut hasher, fuel_used);
    update_hash(&mut hasher, &candidate_list_hash(candidates));
    hasher.finalize().into()
}

pub fn agentic_evidence_trace_hash(trace_hashes: &[[u8; 32]]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-trace-v1");
    update_u64(&mut hasher, trace_hashes.len() as u64);
    for trace_hash in trace_hashes {
        update_hash(&mut hasher, trace_hash);
    }
    hasher.finalize().into()
}

pub fn agentic_evidence_execution_record_hash(record: &AgenticEvidenceExecutionRecord) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-execution-record-v1");
    update_hash(&mut hasher, &record.program_hash);
    update_hash(&mut hasher, &record.index_epoch_hash);
    update_u32(&mut hasher, record.step_count);
    update_u32(&mut hasher, record.fuel_used);
    update_u32(&mut hasher, record.candidate_count);
    update_hash(&mut hasher, &record.candidate_list_hash);
    update_hash(&mut hasher, &record.trace_hash);
    hasher.finalize().into()
}

pub fn agentic_evidence_sdk_manifest_hash(manifest: &AgenticEvidenceSdkManifest) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-sdk-manifest-v1");
    update_hash(&mut hasher, &manifest.program_hash);
    update_hash(&mut hasher, &manifest.index_epoch_hash);
    update_u32(&mut hasher, manifest.max_fuel);
    update_u32(&mut hasher, manifest.step_count);
    update_hash(&mut hasher, &manifest.required_primitive_hash);
    update_bool(&mut hasher, manifest.requires_lexical_index);
    update_bool(&mut hasher, manifest.requires_exact_index);
    update_bool(&mut hasher, manifest.requires_bitmap_filter);
    update_hash(&mut hasher, &manifest.lexical_index_hash);
    update_hash(&mut hasher, &manifest.exact_index_hash);
    update_hash(&mut hasher, &manifest.bitmap_filter_hash);
    hasher.finalize().into()
}

pub fn agentic_evidence_sdk_run_hash(
    manifest_hash: [u8; 32],
    execution_record_hash: [u8; 32],
    capsule_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-sdk-run-v1");
    update_hash(&mut hasher, &manifest_hash);
    update_hash(&mut hasher, &execution_record_hash);
    update_hash(&mut hasher, &capsule_hash);
    hasher.finalize().into()
}

pub fn agentic_evidence_required_primitive_hash(
    steps: &[AgenticEvidenceProgramStep],
    requires_lexical_index: bool,
    requires_exact_index: bool,
    requires_bitmap_filter: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-required-primitives-v1");
    update_bool(&mut hasher, requires_lexical_index);
    update_bool(&mut hasher, requires_exact_index);
    update_bool(&mut hasher, requires_bitmap_filter);
    update_u64(&mut hasher, steps.len() as u64);
    for step in steps {
        hasher.update(&[step.primitive_kind() as u8]);
        update_u32(&mut hasher, step.fuel_cost());
    }
    hasher.finalize().into()
}

pub fn agentic_evidence_lexical_index_hash(index: &HotLexicalIndex) -> [u8; 32] {
    index.index_hash
}

fn empty_agentic_evidence_lexical_index_hash(index_epoch_hash: [u8; 32]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-lexical-index-v1");
    update_hash(&mut hasher, &index_epoch_hash);
    update_u64(&mut hasher, 0);
    update_u64(&mut hasher, 0);
    hasher.finalize().into()
}

fn compute_agentic_evidence_lexical_index_hash(index: &HotLexicalIndex) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-lexical-index-v1");
    update_hash(&mut hasher, &index.index_epoch_hash);
    update_u64(&mut hasher, index.docs.len() as u64);
    for doc in &index.docs {
        update_hash(&mut hasher, &doc.evidence_ref_hash);
        update_u64(&mut hasher, doc.segment_id);
    }
    update_u64(&mut hasher, index.postings_by_token.len() as u64);
    for (term_hash, postings) in &index.postings_by_token {
        update_u64(&mut hasher, *term_hash);
        update_u64(&mut hasher, postings.len() as u64);
        for posting in postings {
            update_u64(&mut hasher, posting.doc_index as u64);
            update_u32(&mut hasher, posting.term_frequency as u32);
        }
    }
    hasher.finalize().into()
}

pub fn agentic_evidence_exact_index_hash(index: &HotEvidenceIndex) -> [u8; 32] {
    index.index_hash
}

fn empty_agentic_evidence_exact_index_hash(index_epoch_hash: [u8; 32]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-exact-index-v1");
    update_hash(&mut hasher, &index_epoch_hash);
    update_u64(&mut hasher, 0);
    hasher.finalize().into()
}

fn compute_agentic_evidence_exact_index_hash(index: &HotEvidenceIndex) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-exact-index-v1");
    update_hash(&mut hasher, &index.index_epoch_hash);
    update_u64(&mut hasher, index.docs.len() as u64);
    for doc in &index.docs {
        update_hash(&mut hasher, &doc.evidence_ref_hash);
        update_u64(&mut hasher, doc.segment_id);
        update_hash(&mut hasher, &doc.artifact_hash);
        update_hash(&mut hasher, &doc.ast_signature_hash);
        update_hash(&mut hasher, &doc.content_hash);
        update_hash(&mut hasher, &doc.binding_hash);
    }
    hasher.finalize().into()
}

pub fn agentic_evidence_bitmap_filter_hash(filter: &HotBitmapFilter) -> [u8; 32] {
    filter.filter_hash
}

fn agentic_evidence_bitmap_filter_hash_parts(
    index_epoch_hash: [u8; 32],
    allowed_segment_count: usize,
    allowed_segment_set_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agentic-evidence-bitmap-filter-v1");
    update_hash(&mut hasher, &index_epoch_hash);
    update_u64(&mut hasher, allowed_segment_count as u64);
    update_hash(&mut hasher, &allowed_segment_set_hash);
    hasher.finalize().into()
}

pub fn browser_page_search_pattern_hash(
    pattern: &str,
    case_sensitive: bool,
    regex: bool,
) -> Option<[u8; 32]> {
    let pattern = pattern.trim();
    if pattern.is_empty() {
        return None;
    }
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-page-search-pattern-v1");
    update_bool(&mut hasher, case_sensitive);
    update_bool(&mut hasher, regex);
    update_u64(&mut hasher, pattern.len() as u64);
    hasher.update(pattern.as_bytes());
    Some(hasher.finalize().into())
}

pub fn browser_page_search_evidence_ref_hash(
    browser_observation_packet_hash: [u8; 32],
    dom_artifact_hash: [u8; 32],
    pattern_hash: [u8; 32],
    match_ordinal: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-page-search-evidence-ref-v1");
    update_hash(&mut hasher, &browser_observation_packet_hash);
    update_hash(&mut hasher, &dom_artifact_hash);
    update_hash(&mut hasher, &pattern_hash);
    update_u32(&mut hasher, match_ordinal);
    hasher.finalize().into()
}

pub fn browser_page_search_candidate_record_hash(
    record: &BrowserPageSearchCandidateRecord,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-browser-page-search-candidate-record-v1");
    update_hash(&mut hasher, &record.browser_observation_packet_hash);
    update_hash(&mut hasher, &record.dom_artifact_hash);
    update_hash(&mut hasher, &record.pattern_hash);
    update_hash(&mut hasher, &record.index_epoch_hash);
    update_u32(&mut hasher, record.limit);
    update_u32(&mut hasher, record.match_count);
    update_u32(&mut hasher, record.candidate_count);
    update_hash(&mut hasher, &record.candidate_list_hash);
    hasher.finalize().into()
}

#[allow(clippy::too_many_arguments)]
pub fn candidate_state_capsule_hash(
    capsule_id: u128,
    index_epoch_hash: [u8; 32],
    candidate_count: u32,
    candidate_list_hash: [u8; 32],
    cold_vector_replay_record_hash: [u8; 32],
    agentic_evidence_execution_record_hash: [u8; 32],
    payload_byte_len: u32,
    payload_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-candidate-state-capsule-v1");
    update_u128(&mut hasher, capsule_id);
    update_hash(&mut hasher, &index_epoch_hash);
    update_u32(&mut hasher, candidate_count);
    update_hash(&mut hasher, &candidate_list_hash);
    update_hash(&mut hasher, &cold_vector_replay_record_hash);
    update_hash(&mut hasher, &agentic_evidence_execution_record_hash);
    update_u32(&mut hasher, payload_byte_len);
    update_hash(&mut hasher, &payload_hash);
    hasher.finalize().into()
}

pub fn sorted_evidence_set_hash(values: &[u64]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-sorted-evidence-set-v1");
    update_u64(&mut hasher, values.len() as u64);
    for value in values {
        update_u64(&mut hasher, *value);
    }
    hasher.finalize().into()
}

fn update_agentic_evidence_step_hash(hasher: &mut Hasher, step: &AgenticEvidenceProgramStep) {
    hasher.update(&[step.primitive_kind() as u8]);
    update_u32(hasher, step.fuel_cost());
    match step {
        AgenticEvidenceProgramStep::LexicalTopK { query_terms, limit } => {
            update_u64(hasher, *limit as u64);
            let normalized = normalized_owned_terms(query_terms);
            update_u64(hasher, normalized.len() as u64);
            for term_hash in normalized {
                update_u64(hasher, term_hash);
            }
        }
        AgenticEvidenceProgramStep::ExactArtifactRerank {
            exact_patterns,
            limit,
        } => {
            update_u64(hasher, *limit as u64);
            let normalized = normalized_owned_exact_patterns(exact_patterns);
            update_u64(hasher, normalized.len() as u64);
            for pattern in normalized {
                hasher.update(pattern.as_bytes());
                update_u64(hasher, pattern.len() as u64);
            }
        }
        AgenticEvidenceProgramStep::AstSignatureLookup {
            ast_signature_hashes,
            limit,
        } => {
            update_u64(hasher, *limit as u64);
            let normalized = normalized_ast_signature_hashes(ast_signature_hashes);
            update_u64(hasher, normalized.len() as u64);
            for ast_signature_hash in normalized {
                update_hash(hasher, &ast_signature_hash);
            }
        }
        AgenticEvidenceProgramStep::BitmapFilter { limit } => {
            update_u64(hasher, *limit as u64);
        }
    }
}

fn required_lexical_index_hash(
    index_epoch_hash: [u8; 32],
    lexical_index: Option<&HotLexicalIndex>,
    requirements: AgenticEvidenceSdkRequirements,
) -> Result<[u8; 32], AgenticEvidenceSdkError> {
    if !requirements.requires_lexical_index {
        return Ok([0; 32]);
    }
    let index = lexical_index.ok_or(AgenticEvidenceSdkError::MissingLexicalIndex)?;
    if index.index_epoch_hash != index_epoch_hash {
        return Err(AgenticEvidenceSdkError::LexicalIndexEpochMismatch);
    }
    Ok(agentic_evidence_lexical_index_hash(index))
}

fn required_exact_index_hash(
    index_epoch_hash: [u8; 32],
    exact_index: Option<&HotEvidenceIndex>,
    requirements: AgenticEvidenceSdkRequirements,
) -> Result<[u8; 32], AgenticEvidenceSdkError> {
    if !requirements.requires_exact_index {
        return Ok([0; 32]);
    }
    let index = exact_index.ok_or(AgenticEvidenceSdkError::MissingExactIndex)?;
    if index.index_epoch_hash != index_epoch_hash {
        return Err(AgenticEvidenceSdkError::ExactIndexEpochMismatch);
    }
    Ok(agentic_evidence_exact_index_hash(index))
}

fn required_bitmap_filter_hash(
    index_epoch_hash: [u8; 32],
    bitmap_filter: Option<&HotBitmapFilter>,
    requirements: AgenticEvidenceSdkRequirements,
) -> Result<[u8; 32], AgenticEvidenceSdkError> {
    if !requirements.requires_bitmap_filter {
        return Ok([0; 32]);
    }
    let filter = bitmap_filter.ok_or(AgenticEvidenceSdkError::MissingBitmapFilter)?;
    if filter.index_epoch_hash != index_epoch_hash {
        return Err(AgenticEvidenceSdkError::BitmapFilterEpochMismatch);
    }
    Ok(agentic_evidence_bitmap_filter_hash(filter))
}

fn required_component_hash_shape(required: bool, hash: &[u8; 32]) -> bool {
    if required {
        nonzero_hash(hash)
    } else {
        *hash == [0; 32]
    }
}

pub fn expanded_term_ref_hash(term_ref: &ExpandedTermRef) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-expanded-term-ref-v1");
    update_u64(&mut hasher, term_ref.term_hash);
    update_u32(&mut hasher, term_ref.dictionary_ordinal);
    update_u32(&mut hasher, term_ref.term_len as u32);
    update_hash(&mut hasher, &term_ref.index_epoch_hash);
    hasher.finalize().into()
}

pub fn lexical_token_hash(term: &str) -> Option<u64> {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-lexical-token-v1");
    let mut has_token_byte = false;
    for byte in term.bytes() {
        if byte.is_ascii_alphanumeric() || byte == b'_' {
            hasher.update(&[byte.to_ascii_lowercase()]);
            has_token_byte = true;
        }
    }
    if !has_token_byte {
        return None;
    }
    let digest = hasher.finalize();
    let bytes = digest.as_bytes();
    Some(u64::from_le_bytes([
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
    ]))
}

fn normalize_dictionary_term(term: &str) -> Option<String> {
    let mut normalized = String::with_capacity(term.len());
    for byte in term.bytes() {
        if byte.is_ascii_alphanumeric() || byte == b'_' {
            normalized.push(byte.to_ascii_lowercase() as char);
        }
    }
    (!normalized.is_empty()).then_some(normalized)
}

fn update_hash(hasher: &mut Hasher, hash: &[u8; 32]) {
    hasher.update(hash);
}

fn update_bool(hasher: &mut Hasher, value: bool) {
    hasher.update(&[u8::from(value)]);
}

fn blake3_digest_bytes(bytes: &[u8]) -> [u8; 32] {
    blake3::hash(bytes).into()
}

fn vector_norm_sq(vector: &[i16]) -> u64 {
    vector
        .iter()
        .map(|value| {
            let widened = i64::from(*value);
            (widened * widened) as u64
        })
        .sum()
}

fn vector_similarity_ppm(
    query_vector: &[i16],
    query_norm_sq: u64,
    doc_vector: &[i16],
    doc_norm_sq: u64,
) -> u32 {
    if query_norm_sq == 0 || doc_norm_sq == 0 || query_vector.len() != doc_vector.len() {
        return 0;
    }
    let dot: i128 = query_vector
        .iter()
        .zip(doc_vector.iter())
        .map(|(query, doc)| i128::from(*query) * i128::from(*doc))
        .sum();
    if dot <= 0 {
        return 0;
    }
    let numerator = (dot as u128)
        .saturating_mul(dot as u128)
        .saturating_mul(u128::from(CANDIDATE_SCORE_PPM_MAX));
    let denominator = u128::from(query_norm_sq).saturating_mul(u128::from(doc_norm_sq));
    if denominator == 0 {
        return 0;
    }
    (numerator / denominator).min(u128::from(CANDIDATE_SCORE_PPM_MAX)) as u32
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_u32(hasher: &mut Hasher, value: u32) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

fn shared_candidate_epoch(
    candidates: &[CandidateEvidenceRef],
) -> Result<[u8; 32], CandidateStateCapsuleError> {
    let first = candidates
        .first()
        .ok_or(CandidateStateCapsuleError::InvalidCapsule)?;
    if !first.is_valid() {
        return Err(CandidateStateCapsuleError::InvalidCandidate);
    }
    for candidate in candidates {
        if !candidate.is_valid() {
            return Err(CandidateStateCapsuleError::InvalidCandidate);
        }
        if candidate.index_epoch_hash != first.index_epoch_hash {
            return Err(CandidateStateCapsuleError::ReplayRecordMismatch);
        }
    }
    Ok(first.index_epoch_hash)
}

fn cold_vector_record_hash_for_capsule(
    candidates: &[CandidateEvidenceRef],
    cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
) -> Result<[u8; 32], CandidateStateCapsuleError> {
    let cold_candidates: Vec<CandidateEvidenceRef> = candidates
        .iter()
        .copied()
        .filter(|candidate| candidate.origin_tier == EvidenceCandidateTier::ColdVectorExpansion)
        .collect();
    if cold_candidates.is_empty() {
        return Ok([0; 32]);
    }
    let record = cold_vector_record.ok_or(CandidateStateCapsuleError::CandidateGate(
        CandidateOnlyGateError::MissingReplayEvent,
    ))?;
    if !record.is_valid()
        || record.candidate_count != cold_candidates.len().min(u32::MAX as usize) as u32
        || record.candidate_list_hash != candidate_list_hash(&cold_candidates)
        || cold_candidates
            .iter()
            .any(|candidate| candidate.index_epoch_hash != record.index_epoch_hash)
    {
        return Err(CandidateStateCapsuleError::ReplayRecordMismatch);
    }
    Ok(record.record_hash)
}

fn agentic_record_hash_for_capsule(
    candidates: &[CandidateEvidenceRef],
    agentic_record: Option<&AgenticEvidenceExecutionRecord>,
) -> Result<[u8; 32], CandidateStateCapsuleError> {
    let agentic_candidates: Vec<CandidateEvidenceRef> = candidates
        .iter()
        .copied()
        .filter(|candidate| candidate.origin_tier == EvidenceCandidateTier::AgenticProgramOutput)
        .collect();
    if agentic_candidates.is_empty() {
        return Ok([0; 32]);
    }
    let record = agentic_record.ok_or(CandidateStateCapsuleError::CandidateGate(
        CandidateOnlyGateError::MissingReplayEvent,
    ))?;
    if !record.is_valid()
        || record.candidate_count != agentic_candidates.len().min(u32::MAX as usize) as u32
        || record.candidate_list_hash != candidate_list_hash(&agentic_candidates)
        || agentic_candidates
            .iter()
            .any(|candidate| candidate.index_epoch_hash != record.index_epoch_hash)
    {
        return Err(CandidateStateCapsuleError::ReplayRecordMismatch);
    }
    Ok(record.record_hash)
}

#[allow(clippy::too_many_arguments)]
fn candidate_state_capsule_payload(
    capsule_id: u128,
    index_epoch_hash: [u8; 32],
    candidate_list_hash: [u8; 32],
    cold_vector_replay_record_hash: [u8; 32],
    agentic_evidence_execution_record_hash: [u8; 32],
    candidates: &[CandidateEvidenceRef],
) -> Vec<u8> {
    let mut payload = Vec::with_capacity(8 + 16 + 32 * 4 + 4 + candidates.len() * 77);
    payload.extend_from_slice(b"AEGCS1\0\0");
    payload.extend_from_slice(&capsule_id.to_le_bytes());
    payload.extend_from_slice(&index_epoch_hash);
    payload.extend_from_slice(&candidate_list_hash);
    payload.extend_from_slice(&cold_vector_replay_record_hash);
    payload.extend_from_slice(&agentic_evidence_execution_record_hash);
    payload.extend_from_slice(&(candidates.len().min(u32::MAX as usize) as u32).to_le_bytes());
    for candidate in candidates {
        payload.extend_from_slice(&candidate.evidence_ref_hash);
        payload.extend_from_slice(&candidate.segment_id.to_le_bytes());
        payload.push(candidate.origin_tier as u8);
        payload.extend_from_slice(&candidate.score_quantized.to_le_bytes());
        payload.extend_from_slice(&candidate.index_epoch_hash);
    }
    payload
}

struct DecodedCandidateStateCapsule {
    capsule_id: u128,
    index_epoch_hash: [u8; 32],
    candidate_count: u32,
    candidate_list_hash: [u8; 32],
    cold_vector_replay_record_hash: [u8; 32],
    agentic_evidence_execution_record_hash: [u8; 32],
    candidates: Vec<CandidateEvidenceRef>,
}

fn decode_candidate_state_capsule_payload(
    payload: &[u8],
) -> Result<DecodedCandidateStateCapsule, CandidateStateCapsuleError> {
    const HEADER_BYTES: usize = 8 + 16 + 32 + 32 + 32 + 32 + 4;
    const CANDIDATE_BYTES: usize = 32 + 8 + 1 + 4 + 32;
    if payload.len() < HEADER_BYTES || &payload[0..8] != b"AEGCS1\0\0" {
        return Err(CandidateStateCapsuleError::InvalidCapsule);
    }
    let capsule_id = read_u128(payload, 8)?;
    let index_epoch_hash = read_hash(payload, 24)?;
    let decoded_candidate_list_hash = read_hash(payload, 56)?;
    let cold_vector_replay_record_hash = read_hash(payload, 88)?;
    let agentic_evidence_execution_record_hash = read_hash(payload, 120)?;
    let candidate_count = read_u32(payload, 152)?;
    let expected_len = HEADER_BYTES
        .checked_add(candidate_count as usize * CANDIDATE_BYTES)
        .ok_or(CandidateStateCapsuleError::InvalidCapsule)?;
    if expected_len != payload.len() || candidate_count == 0 {
        return Err(CandidateStateCapsuleError::InvalidCapsule);
    }
    let mut candidates = Vec::with_capacity(candidate_count as usize);
    let mut offset = HEADER_BYTES;
    for _ in 0..candidate_count {
        let evidence_ref_hash = read_hash(payload, offset)?;
        offset += 32;
        let segment_id = read_u64(payload, offset)?;
        offset += 8;
        let origin_tier = EvidenceCandidateTier::from_u8(payload[offset])
            .ok_or(CandidateStateCapsuleError::InvalidCandidate)?;
        offset += 1;
        let score_quantized = read_u32(payload, offset)?;
        offset += 4;
        let candidate_epoch_hash = read_hash(payload, offset)?;
        offset += 32;
        let candidate = CandidateEvidenceRef::new(
            evidence_ref_hash,
            segment_id,
            origin_tier,
            score_quantized,
            candidate_epoch_hash,
        );
        if !candidate.is_valid() || candidate_epoch_hash != index_epoch_hash {
            return Err(CandidateStateCapsuleError::InvalidCandidate);
        }
        candidates.push(candidate);
    }
    if decoded_candidate_list_hash != candidate_list_hash(&candidates) {
        return Err(CandidateStateCapsuleError::ReplayRecordMismatch);
    }
    Ok(DecodedCandidateStateCapsule {
        capsule_id,
        index_epoch_hash,
        candidate_count,
        candidate_list_hash: decoded_candidate_list_hash,
        cold_vector_replay_record_hash,
        agentic_evidence_execution_record_hash,
        candidates,
    })
}

fn read_hash(payload: &[u8], offset: usize) -> Result<[u8; 32], CandidateStateCapsuleError> {
    let slice = payload
        .get(offset..offset.saturating_add(32))
        .ok_or(CandidateStateCapsuleError::InvalidCapsule)?;
    slice
        .try_into()
        .map_err(|_| CandidateStateCapsuleError::InvalidCapsule)
}

fn read_u128(payload: &[u8], offset: usize) -> Result<u128, CandidateStateCapsuleError> {
    let slice = payload
        .get(offset..offset.saturating_add(16))
        .ok_or(CandidateStateCapsuleError::InvalidCapsule)?;
    Ok(u128::from_le_bytes(
        slice
            .try_into()
            .map_err(|_| CandidateStateCapsuleError::InvalidCapsule)?,
    ))
}

fn read_u64(payload: &[u8], offset: usize) -> Result<u64, CandidateStateCapsuleError> {
    let slice = payload
        .get(offset..offset.saturating_add(8))
        .ok_or(CandidateStateCapsuleError::InvalidCapsule)?;
    Ok(u64::from_le_bytes(
        slice
            .try_into()
            .map_err(|_| CandidateStateCapsuleError::InvalidCapsule)?,
    ))
}

fn read_u32(payload: &[u8], offset: usize) -> Result<u32, CandidateStateCapsuleError> {
    let slice = payload
        .get(offset..offset.saturating_add(4))
        .ok_or(CandidateStateCapsuleError::InvalidCapsule)?;
    Ok(u32::from_le_bytes(
        slice
            .try_into()
            .map_err(|_| CandidateStateCapsuleError::InvalidCapsule)?,
    ))
}
