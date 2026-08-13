use crate::evidence_index::{
    CandidateEvidenceRef, EvidenceCandidateTier, HotLexicalIndex, LexicalIndexError,
};
use crate::learning::{LearningEventType, LearningLedger};
use crate::licensing::{Feature as LicenseFeature, LicenseManager};
use blake3::Hasher;
use std::collections::BTreeMap;

/// Domain-tagged BLAKE3 hasher.
fn domain_hasher(domain: &[u8]) -> Hasher {
    let mut hasher = Hasher::new();
    hasher.update(domain);
    hasher.update(&[0]);
    hasher
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

// ── SessionDocument ──────────────────────────────────────────────────────

/// A lightweight index record for a single session.
/// The full transcript is stored externally (e.g. in the Arrow audit
/// stream or SQLite state.db); this struct only holds the
/// cryptographic hash and metadata needed for candidate-generation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionDocument {
    pub session_id: u128,
    pub content_hash: [u8; 32],
    pub timestamp: u64,
}

impl SessionDocument {
    pub fn new(session_id: u128, content: &str, timestamp: u64) -> Option<Self> {
        if session_id == 0 || content.trim().is_empty() || timestamp == 0 {
            return None;
        }
        let content_hash = session_document_domain_hash(session_id, content, timestamp);
        Some(Self {
            session_id,
            content_hash,
            timestamp,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.session_id > 0
            && nonzero_hash(&self.content_hash)
            && self.timestamp > 0
    }
}

// ── SessionSearchIndex ───────────────────────────────────────────────────

/// Cross-session recall engine.
///
/// Wraps AEGIS's `HotLexicalIndex` (the same engine used for
/// evidence retrieval) and maps session transcripts into
/// `CandidateEvidenceRef`s that `ContextGovernor` can inject
/// into context packs — subject to `CandidateOnlyGate`.
///
/// **Important**: this index returns CANDIDATES, not truth.
/// `CandidateOnlyGate` ensures these refs can never be used as
/// `PhysicalWitness`, `PolicyApproval`, or `MemoryCommit`.
pub struct SessionSearchIndex {
    documents: BTreeMap<u128, SessionDocument>,
    inverted_index: HotLexicalIndex,
    indexed_count: u64,
}

impl SessionSearchIndex {
    /// Create a new index bound to the given evidence epoch.
    pub fn new(index_epoch_hash: [u8; 32]) -> Result<Self, LexicalIndexError> {
        let inverted_index = HotLexicalIndex::new(index_epoch_hash)?;
        Ok(Self {
            documents: BTreeMap::new(),
            inverted_index,
            indexed_count: 0,
        })
    }

    /// Number of sessions indexed so far.
    pub fn len(&self) -> usize {
        self.documents.len()
    }

    pub fn is_empty(&self) -> bool {
        self.documents.is_empty()
    }

    /// Index a session transcript.
    ///
    /// The content is hashed and inserted into the `HotLexicalIndex`.
    /// A `LearningEventType::SessionRecallIndexed` event is appended
    /// to the `LearningLedger` for cryptographic audit.
    ///
    /// Returns the `content_hash` of the newly-indexed document.
    pub fn index_session(
        &mut self,
        session_id: u128,
        content: &str,
        timestamp: u64,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<[u8; 32], LexicalIndexError> {
        // ── Commercial Gate: CrossSessionMemory requires Pro/Team/Enterprise ──
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::CrossSessionMemory)
                .map_err(|e| LexicalIndexError::LicenseRequired(e))?;
        }

        let doc = SessionDocument::new(session_id, content, timestamp)
            .ok_or(LexicalIndexError::InvalidDocument)?;

        // Index the document in HotLexicalIndex.
        // We use session_id as segment_id and content_hash as evidence_ref_hash.
        let terms: Vec<&str> = content.split_whitespace().collect();
        if terms.is_empty() {
            return Err(LexicalIndexError::EmptyTerms);
        }

        self.inverted_index.insert_document(
            doc.content_hash,
            session_id as u64, // segment_id: session_id as u64
            &terms,
        )?;

        // Store locally
        let content_hash = doc.content_hash;
        self.documents.insert(session_id, doc);
        self.indexed_count += 1;

        // Append to learning ledger
        learning_ledger.append(
            LearningEventType::SessionRecallIndexed {
                session_hash: content_hash,
            },
            None,
            timestamp,
            session_id,
        );

        Ok(content_hash)
    }

    /// Search indexed sessions and return candidate evidence refs.
    ///
    /// Results are `EvidenceCandidateTier::ColdVectorExpansion` to
    /// signal that they originate from an external retrieval system
    /// and therefore require replay-event binding through
    /// `CandidateOnlyGate` when injected into context packs.
    ///
    /// Returns at most `top_k` results, ordered by relevance.
    pub fn search_sessions(
        &self,
        query: &str,
        top_k: usize,
    ) -> Vec<CandidateEvidenceRef> {
        // Hash the query text for audit
        let _query_hash = {
            let mut h = domain_hasher(b"aegis-session-query-v1");
            h.update(query.as_bytes());
            *h.finalize().as_bytes()
        };

        let terms: Vec<&str> = query.split_whitespace().collect();
        if terms.is_empty() {
            return Vec::new();
        }

        // Query the HotLexicalIndex — returns CandidateEvidenceRef with
        // tier EvidenceCandidateTier::LexicalBaseline.
        // We re-tier them to ColdVectorExpansion so that
        // CandidateOnlyGate treats them correctly.
        let raw_results = self.inverted_index.query_top_k(&terms, top_k);

        raw_results
            .into_iter()
            .map(|c| {
                CandidateEvidenceRef::new(
                    c.evidence_ref_hash,
                    c.segment_id,
                    EvidenceCandidateTier::ColdVectorExpansion,
                    c.score_quantized,
                    c.index_epoch_hash,
                )
            })
            .collect()
    }
}

// ── Hash helpers ─────────────────────────────────────────────────────────

fn session_document_domain_hash(session_id: u128, content: &str, timestamp: u64) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-session-document-v1");
    hasher.update(&session_id.to_le_bytes());
    hasher.update(content.as_bytes());
    hasher.update(&timestamp.to_le_bytes());
    *hasher.finalize().as_bytes()
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    // LearningLedger removed in Phase 1A cleanup — unused in mod tests (verified by clippy 2026-06-15).

    #[test]
    fn test_session_document_rejects_zero_id() {
        let doc = SessionDocument::new(0, "test content", 1_700_000_000_000);
        assert!(doc.is_none());
    }

    #[test]
    fn test_session_document_rejects_empty_content() {
        let doc = SessionDocument::new(1, "   ", 1_700_000_000_000);
        assert!(doc.is_none());
    }

    #[test]
    fn test_session_document_is_valid() {
        let doc = SessionDocument::new(42, "test session content", 1_700_000_000_000).unwrap();
        assert!(doc.is_valid());
        assert_ne!(doc.content_hash, [0; 32]);
    }

    #[test]
    fn test_session_document_hash_deterministic() {
        let d1 = SessionDocument::new(1, "hello world", 1_700_000_000_000).unwrap();
        let d2 = SessionDocument::new(1, "hello world", 1_700_000_000_000).unwrap();
        assert_eq!(d1.content_hash, d2.content_hash);
    }

    #[test]
    fn test_session_document_hash_differs_per_content() {
        let d1 = SessionDocument::new(1, "hello", 1_700_000_000_000).unwrap();
        let d2 = SessionDocument::new(1, "world", 1_700_000_000_000).unwrap();
        assert_ne!(d1.content_hash, d2.content_hash);
    }
}