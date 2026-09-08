use crate::evidence_index::{
    CandidateEvidenceRef, EvidenceCandidateTier, HotLexicalIndex, LexicalIndexError,
};
use crate::learning::{LearningEventType, LearningLedger};
use crate::licensing::{Feature as LicenseFeature, LicenseManager};
use blake3::Hasher;
use rusqlite::{Connection, OptionalExtension, params};
use std::collections::BTreeMap;
use std::path::Path;

const MAX_SESSION_CONTENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_SESSION_QUERY_BYTES: usize = 64 * 1024;

#[derive(Debug)]
pub enum SessionStoreError {
    Index(LexicalIndexError),
    Sqlite(rusqlite::Error),
    InvalidStoredRecord,
    SchemaTooNew(u32),
    NotPersistent,
}

impl From<rusqlite::Error> for SessionStoreError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

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
    pub scope_kind: String,
    pub owner_id: String,
}

/// Authoritative source returned only after the caller supplies the exact
/// owner and scope. Candidate search never includes this payload.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionSourceRecord {
    pub document: SessionDocument,
    pub content: String,
}

impl SessionDocument {
    pub fn new(session_id: u128, content: &str, timestamp: u64) -> Option<Self> {
        Self::new_scoped(
            session_id,
            content,
            timestamp,
            "USER_PRIVATE",
            "local-profile",
        )
    }

    pub fn new_scoped(
        session_id: u128,
        content: &str,
        timestamp: u64,
        scope_kind: &str,
        owner_id: &str,
    ) -> Option<Self> {
        if session_id == 0
            || content.trim().is_empty()
            || content.len() > MAX_SESSION_CONTENT_BYTES
            || timestamp == 0
        {
            return None;
        }
        if !valid_scope(scope_kind) || owner_id.trim().is_empty() {
            return None;
        }
        if owner_id.len() > 256 || scope_kind.len() > 64 {
            return None;
        }
        let content_hash =
            session_document_domain_hash(session_id, content, timestamp, scope_kind, owner_id);
        Some(Self {
            session_id,
            content_hash,
            timestamp,
            scope_kind: scope_kind.to_string(),
            owner_id: owner_id.to_string(),
        })
    }

    pub fn is_valid(&self) -> bool {
        self.session_id > 0
            && nonzero_hash(&self.content_hash)
            && self.timestamp > 0
            && valid_scope(&self.scope_kind)
            && !self.owner_id.trim().is_empty()
    }
}

fn valid_scope(scope_kind: &str) -> bool {
    matches!(
        scope_kind,
        "USER_PRIVATE" | "AGENT_PRIVATE" | "SESSION" | "PROJECT" | "EXTERNAL"
    )
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
    connection: Option<Connection>,
    default_owner_id: String,
}

impl SessionSearchIndex {
    /// Create a new index bound to the given evidence epoch.
    pub fn new(index_epoch_hash: [u8; 32]) -> Result<Self, LexicalIndexError> {
        let inverted_index = HotLexicalIndex::new(index_epoch_hash)?;
        Ok(Self {
            documents: BTreeMap::new(),
            inverted_index,
            indexed_count: 0,
            connection: None,
            default_owner_id: "local-profile".to_string(),
        })
    }

    /// Open the durable session store and rebuild the in-memory lexical
    /// projection from authoritative rows. The projection can be discarded
    /// and rebuilt; the SQLite rows contain the source text needed for
    /// hydration after restart.
    pub fn open(
        path: impl AsRef<Path>,
        index_epoch_hash: [u8; 32],
    ) -> Result<Self, SessionStoreError> {
        Self::open_scoped(path, index_epoch_hash, "local-profile")
    }

    pub fn open_scoped(
        path: impl AsRef<Path>,
        index_epoch_hash: [u8; 32],
        default_owner_id: &str,
    ) -> Result<Self, SessionStoreError> {
        if default_owner_id.trim().is_empty() {
            return Err(SessionStoreError::InvalidStoredRecord);
        }
        let connection = Connection::open(path)?;
        let schema_version: u32 =
            connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        let legacy_schema = schema_version == 1;
        if schema_version > 2 {
            return Err(SessionStoreError::SchemaTooNew(schema_version));
        }
        connection.execute_batch(
            "PRAGMA foreign_keys = ON;
             PRAGMA busy_timeout = 5000;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = FULL;
             CREATE TABLE IF NOT EXISTS session_documents (
                 session_id TEXT PRIMARY KEY NOT NULL,
                 content_hash BLOB NOT NULL UNIQUE,
                 content TEXT NOT NULL,
                 observed_at_ms INTEGER NOT NULL,
                 scope_kind TEXT NOT NULL DEFAULT 'USER_PRIVATE',
                 owner_id TEXT NOT NULL DEFAULT 'local-profile'
             );
             CREATE VIRTUAL TABLE IF NOT EXISTS session_documents_fts
                 USING fts5(session_id UNINDEXED, content);
             ",
        )?;
        if schema_version == 0 {
            connection.execute_batch("PRAGMA user_version = 2;")?;
        } else if schema_version == 1 {
            connection.execute_batch(
                "ALTER TABLE session_documents ADD COLUMN scope_kind TEXT NOT NULL DEFAULT 'USER_PRIVATE';
                 ALTER TABLE session_documents ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local-profile';
                 PRAGMA user_version = 2;",
            )?;
        }
        let mut index = Self {
            documents: BTreeMap::new(),
            inverted_index: HotLexicalIndex::new(index_epoch_hash)
                .map_err(SessionStoreError::Index)?,
            indexed_count: 0,
            connection: Some(connection),
            default_owner_id: default_owner_id.to_string(),
        };

        let stored_rows = {
            let connection = index
                .connection
                .as_ref()
                .ok_or(SessionStoreError::NotPersistent)?;
            let mut statement = connection.prepare(
                "SELECT session_id, content_hash, content, observed_at_ms, scope_kind, owner_id
                 FROM session_documents ORDER BY session_id",
            )?;
            statement
                .query_map([], |row| {
                    let session_id: String = row.get(0)?;
                    let content_hash: Vec<u8> = row.get(1)?;
                    let content: String = row.get(2)?;
                    let observed_at_ms: i64 = row.get(3)?;
                    let scope_kind: String = row.get(4)?;
                    let owner_id: String = row.get(5)?;
                    Ok((
                        session_id,
                        content_hash,
                        content,
                        observed_at_ms,
                        scope_kind,
                        owner_id,
                    ))
                })?
                .collect::<Result<Vec<_>, _>>()?
        };

        for (session_id, content_hash, content, observed_at_ms, scope_kind, owner_id) in stored_rows
        {
            let session_id = session_id
                .parse::<u128>()
                .map_err(|_| SessionStoreError::InvalidStoredRecord)?;
            let content_hash: [u8; 32] = content_hash
                .try_into()
                .map_err(|_| SessionStoreError::InvalidStoredRecord)?;
            let observed_at_ms = u64::try_from(observed_at_ms)
                .map_err(|_| SessionStoreError::InvalidStoredRecord)?;
            let document = SessionDocument {
                session_id,
                content_hash,
                timestamp: observed_at_ms,
                scope_kind,
                owner_id,
            };
            if !document.is_valid()
                || (!legacy_schema
                    && document.content_hash
                        != session_document_domain_hash(
                            document.session_id,
                            &content,
                            document.timestamp,
                            &document.scope_kind,
                            &document.owner_id,
                        )
                    && document.content_hash
                        != session_document_legacy_hash(
                            document.session_id,
                            &content,
                            document.timestamp,
                        ))
            {
                return Err(SessionStoreError::InvalidStoredRecord);
            }
            let terms: Vec<&str> = content.split_whitespace().collect();
            if terms.is_empty() {
                return Err(SessionStoreError::InvalidStoredRecord);
            }
            index
                .inverted_index
                .insert_document(
                    content_hash,
                    u64::try_from(session_id)
                        .map_err(|_| SessionStoreError::InvalidStoredRecord)?,
                    &terms,
                )
                .map_err(SessionStoreError::Index)?;
            index.documents.insert(session_id, document);
            index.indexed_count = index.indexed_count.saturating_add(1);
        }
        Ok(index)
    }

    /// Number of sessions indexed so far.
    pub fn len(&self) -> usize {
        self.documents.len()
    }

    pub fn len_scoped(&self, owner_id: &str) -> usize {
        self.documents
            .values()
            .filter(|document| document.owner_id == owner_id)
            .count()
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
        let owner_id = self.default_owner_id.clone();
        self.index_session_scoped(
            session_id,
            content,
            timestamp,
            "USER_PRIVATE",
            &owner_id,
            learning_ledger,
            license_manager,
        )
    }

    pub fn index_session_scoped(
        &mut self,
        session_id: u128,
        content: &str,
        timestamp: u64,
        scope_kind: &str,
        owner_id: &str,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<[u8; 32], LexicalIndexError> {
        // ── Commercial Gate: CrossSessionMemory requires Pro/Team/Enterprise ──
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::CrossSessionMemory)
                .map_err(|e| LexicalIndexError::LicenseRequired(e))?;
        }

        let doc = SessionDocument::new_scoped(session_id, content, timestamp, scope_kind, owner_id)
            .ok_or(LexicalIndexError::InvalidDocument)?;

        // Index the document in HotLexicalIndex.
        // We use session_id as segment_id and content_hash as evidence_ref_hash.
        let terms: Vec<&str> = content.split_whitespace().collect();
        if terms.is_empty() {
            return Err(LexicalIndexError::EmptyTerms);
        }

        self.inverted_index.insert_document(
            doc.content_hash,
            u64::try_from(session_id).map_err(|_| LexicalIndexError::InvalidDocument)?,
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

    /// Persist a transcript before updating the rebuildable lexical
    /// projection. This is the durable owner used by the application bridge.
    pub fn index_session_durable(
        &mut self,
        session_id: u128,
        content: &str,
        timestamp: u64,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<[u8; 32], SessionStoreError> {
        let owner_id = self.default_owner_id.clone();
        self.index_session_durable_scoped(
            session_id,
            content,
            timestamp,
            "USER_PRIVATE",
            &owner_id,
            learning_ledger,
            license_manager,
        )
    }

    pub fn index_session_durable_scoped(
        &mut self,
        session_id: u128,
        content: &str,
        timestamp: u64,
        scope_kind: &str,
        owner_id: &str,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<[u8; 32], SessionStoreError> {
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::CrossSessionMemory)
                .map_err(|error| {
                    SessionStoreError::Index(LexicalIndexError::LicenseRequired(error))
                })?;
        }
        let document =
            SessionDocument::new_scoped(session_id, content, timestamp, scope_kind, owner_id)
                .ok_or(SessionStoreError::InvalidStoredRecord)?;
        let terms: Vec<&str> = content.split_whitespace().collect();
        if terms.is_empty() {
            return Err(SessionStoreError::InvalidStoredRecord);
        }
        if self.documents.contains_key(&session_id) {
            return Err(SessionStoreError::Index(
                LexicalIndexError::DuplicateDocument,
            ));
        }

        let connection = self
            .connection
            .as_mut()
            .ok_or(SessionStoreError::NotPersistent)?;
        let transaction = connection.transaction()?;
        transaction.execute(
            "INSERT INTO session_documents
                (session_id, content_hash, content, observed_at_ms, scope_kind, owner_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                session_id.to_string(),
                document.content_hash.as_slice(),
                content,
                i64::try_from(timestamp).map_err(|_| SessionStoreError::InvalidStoredRecord)?,
                scope_kind,
                owner_id,
            ],
        )?;
        transaction.execute(
            "INSERT INTO session_documents_fts (session_id, content)
             VALUES (?1, ?2)",
            params![session_id.to_string(), content],
        )?;
        transaction.commit()?;

        self.inverted_index
            .insert_document(
                document.content_hash,
                u64::try_from(session_id).map_err(|_| SessionStoreError::InvalidStoredRecord)?,
                &terms,
            )
            .map_err(SessionStoreError::Index)?;
        let content_hash = document.content_hash;
        self.documents.insert(session_id, document);
        self.indexed_count = self.indexed_count.saturating_add(1);
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

    /// Read the authoritative transcript for hydration, not just its index
    /// metadata. This method is intentionally unavailable on an in-memory
    /// compatibility index.
    pub fn read_session_content(
        &self,
        session_id: u128,
    ) -> Result<Option<String>, SessionStoreError> {
        self.read_session_content_scoped(session_id, "USER_PRIVATE", &self.default_owner_id)
    }

    pub fn read_session_content_scoped(
        &self,
        session_id: u128,
        scope_kind: &str,
        owner_id: &str,
    ) -> Result<Option<String>, SessionStoreError> {
        if !valid_scope(scope_kind) || owner_id.trim().is_empty() {
            return Err(SessionStoreError::InvalidStoredRecord);
        }
        let connection = self
            .connection
            .as_ref()
            .ok_or(SessionStoreError::NotPersistent)?;
        connection
            .query_row(
                "SELECT content FROM session_documents
                 WHERE session_id = ?1 AND scope_kind = ?2 AND owner_id = ?3",
                params![session_id.to_string(), scope_kind, owner_id],
                |row| row.get(0),
            )
            .map(Some)
            .or_else(|error| {
                if matches!(error, rusqlite::Error::QueryReturnedNoRows) {
                    Ok(None)
                } else {
                    Err(SessionStoreError::Sqlite(error))
                }
            })
    }

    pub fn read_session_record_scoped(
        &self,
        session_id: u128,
        scope_kind: &str,
        owner_id: &str,
    ) -> Result<Option<SessionSourceRecord>, SessionStoreError> {
        if !valid_scope(scope_kind) || owner_id.trim().is_empty() {
            return Err(SessionStoreError::InvalidStoredRecord);
        }
        let connection = self
            .connection
            .as_ref()
            .ok_or(SessionStoreError::NotPersistent)?;
        let stored = connection
            .query_row(
                "SELECT content, content_hash, observed_at_ms FROM session_documents
                 WHERE session_id = ?1 AND scope_kind = ?2 AND owner_id = ?3",
                params![session_id.to_string(), scope_kind, owner_id],
                |row| {
                    let content: String = row.get(0)?;
                    let content_hash: Vec<u8> = row.get(1)?;
                    let timestamp: i64 = row.get(2)?;
                    Ok((content, content_hash, timestamp))
                },
            )
            .optional()?;
        let Some((content, content_hash, timestamp)) = stored else {
            return Ok(None);
        };
        let content_hash: [u8; 32] = content_hash
            .try_into()
            .map_err(|_| SessionStoreError::InvalidStoredRecord)?;
        let timestamp =
            u64::try_from(timestamp).map_err(|_| SessionStoreError::InvalidStoredRecord)?;
        let document =
            SessionDocument::new_scoped(session_id, &content, timestamp, scope_kind, owner_id)
                .ok_or(SessionStoreError::InvalidStoredRecord)?;
        let valid_hash = document.content_hash == content_hash
            || session_document_legacy_hash(session_id, &content, timestamp) == content_hash;
        if !valid_hash {
            return Err(SessionStoreError::InvalidStoredRecord);
        }
        Ok(Some(SessionSourceRecord {
            document: SessionDocument {
                content_hash,
                ..document
            },
            content,
        }))
    }

    /// Search indexed sessions and return candidate evidence refs.
    ///
    /// Results are `EvidenceCandidateTier::ColdVectorExpansion` to
    /// signal that they originate from an external retrieval system
    /// and therefore require replay-event binding through
    /// `CandidateOnlyGate` when injected into context packs.
    ///
    /// Returns at most `top_k` results, ordered by relevance.
    pub fn search_sessions(&self, query: &str, top_k: usize) -> Vec<CandidateEvidenceRef> {
        self.search_sessions_scoped(query, top_k, "USER_PRIVATE", &self.default_owner_id)
    }

    pub fn search_sessions_scoped(
        &self,
        query: &str,
        top_k: usize,
        scope_kind: &str,
        owner_id: &str,
    ) -> Vec<CandidateEvidenceRef> {
        if !valid_scope(scope_kind)
            || owner_id.trim().is_empty()
            || query.len() > MAX_SESSION_QUERY_BYTES
        {
            return Vec::new();
        }
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
        let allowed_ids: std::collections::BTreeSet<u64> = self
            .documents
            .values()
            .filter(|document| document.owner_id == owner_id && document.scope_kind == scope_kind)
            .filter_map(|document| u64::try_from(document.session_id).ok())
            .collect();
        let raw_results = self
            .inverted_index
            .query_top_k(&terms, top_k.saturating_mul(self.documents.len().max(1)))
            .into_iter()
            .filter(|candidate| allowed_ids.contains(&candidate.segment_id))
            .take(top_k)
            .collect::<Vec<_>>();

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

fn session_document_domain_hash(
    session_id: u128,
    content: &str,
    timestamp: u64,
    scope_kind: &str,
    owner_id: &str,
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-session-document-v2");
    hasher.update(&session_id.to_le_bytes());
    hasher.update(scope_kind.as_bytes());
    hasher.update(&[0]);
    hasher.update(owner_id.as_bytes());
    hasher.update(&[0]);
    hasher.update(content.as_bytes());
    hasher.update(&timestamp.to_le_bytes());
    *hasher.finalize().as_bytes()
}

fn session_document_legacy_hash(session_id: u128, content: &str, timestamp: u64) -> [u8; 32] {
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
    use crate::learning::LearningLedger;
    use tempfile::tempdir;

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

    #[test]
    fn durable_session_survives_reopen_and_hydrates_content() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("state.db");
        let epoch_hash = *blake3::hash(b"session-persistence-test").as_bytes();
        let content = "durable transcript source text";

        {
            let mut index = SessionSearchIndex::open(&path, epoch_hash).unwrap();
            let mut ledger = LearningLedger::new();
            index
                .index_session_durable(7, content, 1_700_000_000_000, &mut ledger, None)
                .unwrap();
            assert_eq!(
                index.read_session_content(7).unwrap().as_deref(),
                Some(content)
            );
            assert_eq!(ledger.len(), 1);
        }

        let reopened = SessionSearchIndex::open(&path, epoch_hash).unwrap();
        assert_eq!(
            reopened.read_session_content(7).unwrap().as_deref(),
            Some(content)
        );
        let source = reopened
            .read_session_record_scoped(7, "USER_PRIVATE", "local-profile")
            .unwrap()
            .unwrap();
        assert_eq!(source.content, content);
        assert_eq!(
            source.document.content_hash,
            reopened.documents[&7].content_hash
        );
        assert_eq!(reopened.search_sessions("durable source", 5).len(), 1);
    }

    #[test]
    fn durable_session_scope_filters_candidates_and_hydration() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("state.db");
        let epoch_hash = *blake3::hash(b"session-scope-test").as_bytes();
        let mut index = SessionSearchIndex::open_scoped(&path, epoch_hash, "owner-a").unwrap();
        let mut ledger = LearningLedger::new();

        index
            .index_session_durable_scoped(
                11,
                "private owner a source",
                1_700_000_000_000,
                "USER_PRIVATE",
                "owner-a",
                &mut ledger,
                None,
            )
            .unwrap();
        index
            .index_session_durable_scoped(
                12,
                "private owner b source",
                1_700_000_000_001,
                "USER_PRIVATE",
                "owner-b",
                &mut ledger,
                None,
            )
            .unwrap();

        assert_eq!(
            index
                .search_sessions_scoped("private source", 5, "USER_PRIVATE", "owner-a")
                .len(),
            1
        );
        assert_eq!(
            index
                .search_sessions_scoped("private source", 5, "USER_PRIVATE", "owner-b")
                .len(),
            1
        );
        assert_eq!(
            index
                .read_session_content_scoped(12, "USER_PRIVATE", "owner-a")
                .unwrap(),
            None
        );
        assert_eq!(
            index
                .read_session_content_scoped(12, "USER_PRIVATE", "owner-b")
                .unwrap()
                .as_deref(),
            Some("private owner b source")
        );
    }

    #[test]
    fn schema_v1_session_store_migrates_scope_columns() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("state.db");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE session_documents (
                    session_id TEXT PRIMARY KEY NOT NULL,
                    content_hash BLOB NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL
                 );
                 CREATE VIRTUAL TABLE session_documents_fts
                     USING fts5(session_id UNINDEXED, content);
                 PRAGMA user_version = 1;",
            )
            .unwrap();
        let hash = session_document_legacy_hash(13, "legacy transcript", 1_700_000_000_000);
        connection
            .execute(
                "INSERT INTO session_documents
                    (session_id, content_hash, content, observed_at_ms)
                 VALUES ('13', ?1, 'legacy transcript', 1700000000000)",
                params![hash.as_slice()],
            )
            .unwrap();
        drop(connection);

        let reopened = SessionSearchIndex::open(&path, *blake3::hash(b"epoch").as_bytes()).unwrap();
        assert_eq!(
            reopened.read_session_content(13).unwrap().as_deref(),
            Some("legacy transcript")
        );
        let reopened_again =
            SessionSearchIndex::open(&path, *blake3::hash(b"epoch").as_bytes()).unwrap();
        assert_eq!(
            reopened_again.read_session_content(13).unwrap().as_deref(),
            Some("legacy transcript")
        );
    }
}
