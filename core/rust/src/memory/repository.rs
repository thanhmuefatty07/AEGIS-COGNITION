//! Durable semantic-memory lifecycle owned by the local Rust store.
//!
//! Session search is a rebuildable projection. This module owns the
//! user-visible memory record state and its semantic events; it deliberately
//! does not promote a candidate to physical execution authority.

use crate::memory::nudge::MemoryCandidate;
use blake3::Hasher;
use rusqlite::{
    Connection, OptionalExtension, Transaction, TransactionBehavior, backup::Backup, params,
};
use std::fs;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const MAX_MEMORY_CONTENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_MEMORY_QUERY_BYTES: usize = 64 * 1024;
const MAX_FILE_RELATION_PROJECT_ID_BYTES: usize = 256;
const MAX_FILE_RELATION_SOURCE_REVISION_BYTES: usize = 256;
const MAX_FILE_RELATION_PATH_BYTES: usize = 4096;
pub const MAX_FILE_RELATION_CONTEXT_RESULTS: usize = 8;
pub const MAX_FILE_RELATION_PAGE_SIZE: usize = 100;
pub const DEFAULT_MEMORY_KIND: &str = "SEMANTIC_FACT";

/// Kinds accepted by the semantic-memory repository. Physical execution
/// artifacts are intentionally absent: they have a separate authority path.
pub const MEMORY_KINDS: [&str; 7] = [
    DEFAULT_MEMORY_KIND,
    "USER_ASSERTION",
    "USER_PREFERENCE",
    "USER_WORKFLOW",
    "PROCEDURAL_SKILL",
    "SOURCE_OBSERVED",
    "EXTERNAL_EVIDENCE",
];

#[derive(Debug)]
pub enum MemoryRepositoryError {
    Sqlite(rusqlite::Error),
    Io(String),
    InvalidInput(&'static str),
    Conflict(&'static str),
    PermissionDenied,
    IdempotencyConflict,
    CommittedIndexPending { memory_id: u128, cause: String },
}

impl From<rusqlite::Error> for MemoryRepositoryError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryRecordView {
    pub memory_id: u128,
    pub owner_id: String,
    pub scope_kind: String,
    pub memory_kind: String,
    pub lifecycle: String,
    pub validation: String,
    pub validation_basis: Option<String>,
    pub validation_reason: Option<String>,
    pub revision: u64,
    pub observed_at_ms: u64,
    pub content_hash: Option<[u8; 32]>,
    pub content: Option<String>,
    pub source_session_id: Option<u128>,
    pub nudge_id: Option<u128>,
    pub nudge_hash: Option<[u8; 32]>,
    pub candidate_hash: Option<[u8; 32]>,
    pub relevance_score_micros: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryNudgeProvenance {
    pub source_session_id: u128,
    pub nudge_id: u128,
    pub nudge_hash: [u8; 32],
    pub candidate_hash: [u8; 32],
    pub relevance_score_micros: u32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryFileRelationView {
    pub relation_id: u128,
    pub memory_id: u128,
    pub scope_kind: String,
    pub project_id: String,
    pub relative_path: String,
    pub source_revision: String,
    pub source_content_hash: [u8; 32],
    pub memory_revision: u64,
    pub memory_content_hash: [u8; 32],
    pub created_at_ms: u64,
    pub memory_lifecycle: String,
    pub memory_validation: String,
    pub memory_content_current: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MemoryFileRelationCursor {
    pub created_at_ms: u64,
    pub relation_id: u128,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryFileRelationPage {
    pub relations: Vec<MemoryFileRelationView>,
    pub next_cursor: Option<MemoryFileRelationCursor>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryCaptureOutcome {
    pub content_hash: [u8; 32],
    pub created: bool,
    pub index_pending: bool,
}

pub struct MemoryRepository {
    connection: Connection,
}

type MemoryStateRow = (String, i64, Option<Vec<u8>>, Option<String>);
type CurrentMemoryRow = (String, String, i64, Option<Vec<u8>>, Option<String>);
type DuplicateRelationRow = (String, String, Vec<u8>, i64, Vec<u8>);

struct StoredMemoryFileRelation {
    relation_id: String,
    memory_id: String,
    scope_kind: String,
    project_id: String,
    relative_path: String,
    source_revision: String,
    source_content_hash: Vec<u8>,
    memory_revision: i64,
    memory_content_hash: Vec<u8>,
    created_at_ms: i64,
    memory_lifecycle: String,
    memory_validation: String,
    memory_content_current: bool,
}

fn valid_scope(scope_kind: &str) -> bool {
    matches!(
        scope_kind,
        "USER_PRIVATE" | "AGENT_PRIVATE" | "SESSION" | "PROJECT" | "EXTERNAL"
    )
}

fn valid_memory_kind(memory_kind: &str) -> bool {
    MEMORY_KINDS.contains(&memory_kind)
}

fn hash_content(memory_id: u128, owner_id: &str, scope_kind: &str, content: &str) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-memory-content-v1");
    hasher.update(&[0]);
    hasher.update(&memory_id.to_le_bytes());
    hasher.update(owner_id.as_bytes());
    hasher.update(&[0]);
    hasher.update(scope_kind.as_bytes());
    hasher.update(&[0]);
    hasher.update(content.as_bytes());
    *hasher.finalize().as_bytes()
}

fn hash_request(
    request_id: u128,
    memory_id: u128,
    owner_id: &str,
    scope_kind: &str,
    content: &str,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-memory-request-v1");
    hasher.update(&[0]);
    hasher.update(&request_id.to_le_bytes());
    hasher.update(&memory_id.to_le_bytes());
    hasher.update(owner_id.as_bytes());
    hasher.update(&[0]);
    hasher.update(scope_kind.as_bytes());
    hasher.update(&[0]);
    hasher.update(content.as_bytes());
    *hasher.finalize().as_bytes()
}

fn decode_hash(value: Option<Vec<u8>>) -> Result<Option<[u8; 32]>, MemoryRepositoryError> {
    value
        .map(|bytes| {
            bytes
                .try_into()
                .map_err(|_| MemoryRepositoryError::Conflict("invalid stored content hash"))
        })
        .transpose()
}

fn parse_id(value: String) -> Result<u128, MemoryRepositoryError> {
    value
        .parse::<u128>()
        .map_err(|_| MemoryRepositoryError::Conflict("invalid stored memory id"))
}

fn parse_optional_id(value: Option<String>) -> Result<Option<u128>, MemoryRepositoryError> {
    value.map(parse_id).transpose()
}

fn parse_u64(value: i64) -> Result<u64, MemoryRepositoryError> {
    u64::try_from(value).map_err(|_| MemoryRepositoryError::Conflict("invalid stored integer"))
}

fn validate_identity(
    subject_id: &str,
    owner_id: &str,
    scope_kind: &str,
    timestamp_ms: u64,
) -> Result<(), MemoryRepositoryError> {
    if subject_id.trim().is_empty()
        || subject_id.len() > 256
        || owner_id.trim().is_empty()
        || owner_id.len() > 256
        || !valid_scope(scope_kind)
        || scope_kind.len() > 64
        || timestamp_ms == 0
    {
        return Err(MemoryRepositoryError::InvalidInput(
            "invalid memory access identity",
        ));
    }
    Ok(())
}

fn validate_file_relation_target(
    project_id: &str,
    relative_path: &str,
) -> Result<(), MemoryRepositoryError> {
    let drive_absolute = relative_path.as_bytes().get(1) == Some(&b':')
        && relative_path
            .as_bytes()
            .first()
            .is_some_and(|byte| byte.is_ascii_alphabetic());
    if project_id.trim().is_empty()
        || project_id.len() > MAX_FILE_RELATION_PROJECT_ID_BYTES
        || project_id.chars().any(char::is_control)
        || relative_path.is_empty()
        || relative_path.len() > MAX_FILE_RELATION_PATH_BYTES
        || relative_path.starts_with('/')
        || drive_absolute
        || relative_path.contains('\\')
        || relative_path.chars().any(char::is_control)
        || relative_path.split('/').any(|part| {
            part.is_empty()
                || matches!(part, "." | "..")
                || part.eq_ignore_ascii_case(".git")
                || part.eq_ignore_ascii_case(".aegis")
        })
    {
        return Err(MemoryRepositoryError::InvalidInput(
            "invalid project or normalized relative path",
        ));
    }
    Ok(())
}

fn validate_file_relation_input(
    relation_id: u128,
    memory_id: u128,
    project_id: &str,
    relative_path: &str,
    source_revision: &str,
    memory_revision: u64,
    observed_at_ms: u64,
) -> Result<(), MemoryRepositoryError> {
    if relation_id == 0 || memory_id == 0 || memory_revision == 0 || observed_at_ms == 0 {
        return Err(MemoryRepositoryError::InvalidInput(
            "file relation identifiers, revision, and time must be non-zero",
        ));
    }
    if source_revision.trim().is_empty()
        || source_revision.len() > MAX_FILE_RELATION_SOURCE_REVISION_BYTES
        || source_revision.chars().any(char::is_control)
    {
        return Err(MemoryRepositoryError::InvalidInput(
            "invalid source revision",
        ));
    }
    if i64::try_from(memory_revision).is_err() || i64::try_from(observed_at_ms).is_err() {
        return Err(MemoryRepositoryError::InvalidInput(
            "file relation revision or time is out of range",
        ));
    }
    validate_file_relation_target(project_id, relative_path)
}

fn stored_file_relation_from_row(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<StoredMemoryFileRelation> {
    Ok(StoredMemoryFileRelation {
        relation_id: row.get(0)?,
        memory_id: row.get(1)?,
        scope_kind: row.get(2)?,
        project_id: row.get(3)?,
        relative_path: row.get(4)?,
        source_revision: row.get(5)?,
        source_content_hash: row.get(6)?,
        memory_revision: row.get(7)?,
        memory_content_hash: row.get(8)?,
        created_at_ms: row.get(9)?,
        memory_lifecycle: row.get(10)?,
        memory_validation: row.get(11)?,
        memory_content_current: row.get(12)?,
    })
}

fn decode_stored_file_relation(
    row: StoredMemoryFileRelation,
) -> Result<MemoryFileRelationView, MemoryRepositoryError> {
    Ok(MemoryFileRelationView {
        relation_id: parse_id(row.relation_id)?,
        memory_id: parse_id(row.memory_id)?,
        scope_kind: row.scope_kind,
        project_id: row.project_id,
        relative_path: row.relative_path,
        source_revision: row.source_revision,
        source_content_hash: row
            .source_content_hash
            .try_into()
            .map_err(|_| MemoryRepositoryError::Conflict("invalid stored source content hash"))?,
        memory_revision: parse_u64(row.memory_revision)?,
        memory_content_hash: row
            .memory_content_hash
            .try_into()
            .map_err(|_| MemoryRepositoryError::Conflict("invalid stored memory content hash"))?,
        created_at_ms: parse_u64(row.created_at_ms)?,
        memory_lifecycle: row.memory_lifecycle,
        memory_validation: row.memory_validation,
        memory_content_current: row.memory_content_current,
    })
}

const SELECT_MEMORY_FILE_RELATION: &str =
    "SELECT relation.relation_id, relation.memory_id, relation.scope_kind,
            relation.project_id, relation.relative_path, relation.source_revision,
            relation.source_content_hash, relation.memory_revision,
            relation.memory_content_hash, relation.created_at_ms,
            memory.lifecycle, memory.validation,
            COALESCE(
                memory.content_hash = relation.memory_content_hash AND memory.content IS NOT NULL,
                0
            )
     FROM memory_file_relations relation
     JOIN memory_records memory
       ON memory.memory_id = relation.memory_id
      AND memory.owner_id = relation.owner_id
      AND memory.scope_kind = relation.scope_kind";

fn load_memory_file_relation(
    connection: &Connection,
    relation_id: u128,
    owner_id: &str,
    scope_kind: &str,
) -> Result<Option<MemoryFileRelationView>, MemoryRepositoryError> {
    let query = format!(
        "{SELECT_MEMORY_FILE_RELATION}
         WHERE relation.relation_id = ?1 AND relation.owner_id = ?2 AND relation.scope_kind = ?3"
    );
    connection
        .query_row(
            &query,
            params![relation_id.to_string(), owner_id, scope_kind],
            stored_file_relation_from_row,
        )
        .optional()?
        .map(decode_stored_file_relation)
        .transpose()
}

fn current_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn ensure_column(
    connection: &Connection,
    table: &str,
    definition: &str,
) -> Result<(), MemoryRepositoryError> {
    let column =
        definition
            .split_whitespace()
            .next()
            .ok_or(MemoryRepositoryError::InvalidInput(
                "invalid migration column",
            ))?;
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    if !columns.iter().any(|existing| existing == column) {
        connection.execute(&format!("ALTER TABLE {table} ADD COLUMN {definition}"), [])?;
    }
    Ok(())
}

impl MemoryRepository {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, MemoryRepositoryError> {
        let connection = Connection::open(path)?;
        connection.execute_batch(
            "PRAGMA foreign_keys = ON;
             PRAGMA busy_timeout = 5000;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = FULL;
             CREATE TABLE IF NOT EXISTS memory_records (
                 memory_id TEXT PRIMARY KEY NOT NULL,
                 owner_id TEXT NOT NULL,
                 scope_kind TEXT NOT NULL,
                 memory_kind TEXT NOT NULL DEFAULT 'SEMANTIC_FACT',
                 content_hash BLOB,
                 content TEXT,
                 lifecycle TEXT NOT NULL,
                 validation TEXT NOT NULL,
                 revision INTEGER NOT NULL,
                 observed_at_ms INTEGER NOT NULL,
                 request_id TEXT NOT NULL UNIQUE,
                 request_hash BLOB NOT NULL
             );
             CREATE INDEX IF NOT EXISTS memory_records_owner_scope
                 ON memory_records(owner_id, scope_kind, lifecycle);
             CREATE TABLE IF NOT EXISTS memory_access_grants (
                 subject_id TEXT NOT NULL,
                 owner_id TEXT NOT NULL,
                 scope_kind TEXT NOT NULL,
                 can_read INTEGER NOT NULL CHECK (can_read IN (0, 1)),
                 can_write INTEGER NOT NULL CHECK (can_write IN (0, 1)),
                 expires_at_ms INTEGER,
                 revision INTEGER NOT NULL,
                 updated_at_ms INTEGER NOT NULL,
                 revoked_at_ms INTEGER,
                 PRIMARY KEY(subject_id, owner_id, scope_kind)
             );
             CREATE TABLE IF NOT EXISTS memory_index_state (
                 index_name TEXT PRIMARY KEY NOT NULL,
                 generation INTEGER NOT NULL,
                 dirty_generation INTEGER NOT NULL,
                 updated_at_ms INTEGER NOT NULL
             );
             INSERT OR IGNORE INTO memory_index_state
                 (index_name, generation, dirty_generation, updated_at_ms)
             VALUES ('memory_fts', 0, 0, 0);
             CREATE TABLE IF NOT EXISTS memory_index_dirty (
                 memory_id TEXT PRIMARY KEY NOT NULL,
                 generation INTEGER NOT NULL,
                 operation TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS memory_revisions (
                 memory_id TEXT NOT NULL REFERENCES memory_records(memory_id),
                 revision INTEGER NOT NULL,
                 content_hash BLOB NOT NULL,
                 content TEXT NOT NULL,
                 created_at_ms INTEGER NOT NULL,
                 request_id TEXT NOT NULL UNIQUE,
                 PRIMARY KEY(memory_id, revision)
             );
             CREATE TABLE IF NOT EXISTS memory_events (
                 event_id TEXT PRIMARY KEY NOT NULL,
                 memory_id TEXT NOT NULL REFERENCES memory_records(memory_id),
                 event_type TEXT NOT NULL,
                 revision INTEGER NOT NULL,
                 request_id TEXT NOT NULL UNIQUE,
                 request_hash BLOB NOT NULL,
                 content_hash BLOB,
                 occurred_at_ms INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS memory_nudge_provenance (
                 memory_id TEXT PRIMARY KEY NOT NULL REFERENCES memory_records(memory_id),
                 source_session_id TEXT NOT NULL,
                 nudge_id TEXT NOT NULL,
                 nudge_hash BLOB NOT NULL,
                 candidate_hash BLOB NOT NULL,
                 relevance_score_micros INTEGER NOT NULL CHECK (relevance_score_micros BETWEEN 0 AND 1000000)
             );
             CREATE INDEX IF NOT EXISTS memory_nudge_provenance_source
                 ON memory_nudge_provenance(source_session_id);
             CREATE TABLE IF NOT EXISTS memory_file_relations (
                 relation_id TEXT PRIMARY KEY NOT NULL,
                 memory_id TEXT NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                 owner_id TEXT NOT NULL,
                 scope_kind TEXT NOT NULL,
                 project_id TEXT NOT NULL CHECK (length(project_id) BETWEEN 1 AND 256),
                 relative_path TEXT NOT NULL CHECK (length(relative_path) BETWEEN 1 AND 4096),
                 source_revision TEXT NOT NULL CHECK (length(source_revision) BETWEEN 1 AND 256),
                 source_content_hash BLOB NOT NULL CHECK (length(source_content_hash) = 32),
                 memory_revision INTEGER NOT NULL CHECK (memory_revision > 0),
                 memory_content_hash BLOB NOT NULL CHECK (length(memory_content_hash) = 32),
                 created_at_ms INTEGER NOT NULL CHECK (created_at_ms > 0),
                 UNIQUE(memory_id, owner_id, scope_kind, project_id, relative_path)
             );
             CREATE INDEX IF NOT EXISTS memory_file_relations_owner_target
                 ON memory_file_relations(owner_id, scope_kind, project_id, relative_path);
            CREATE INDEX IF NOT EXISTS memory_file_relations_owner_target_keyset
                 ON memory_file_relations(
                    owner_id, scope_kind, project_id, relative_path, created_at_ms,
                    length(relation_id), relation_id
                 );
             CREATE INDEX IF NOT EXISTS memory_file_relations_current_context
                 ON memory_file_relations(
                     owner_id, scope_kind, project_id, relative_path,
                     source_content_hash, created_at_ms, relation_id
                 );
             CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts
                 USING fts5(memory_id UNINDEXED, owner_id UNINDEXED, scope_kind UNINDEXED, content);
             ",
        )?;
        ensure_column(&connection, "memory_records", "validation_basis TEXT")?;
        ensure_column(&connection, "memory_records", "validation_reason TEXT")?;
        ensure_column(
            &connection,
            "memory_records",
            "memory_kind TEXT NOT NULL DEFAULT 'SEMANTIC_FACT'",
        )?;
        let mut repository = Self { connection };
        repository.rebuild_pending_indexes()?;
        Ok(repository)
    }

    /// Create a consistent SQLite backup through the online backup API. The
    /// caller supplies an unused destination; no live WAL file is copied.
    pub fn backup_to(&self, destination: impl AsRef<Path>) -> Result<(), MemoryRepositoryError> {
        let destination = destination.as_ref();
        if destination.exists() {
            return Err(MemoryRepositoryError::Conflict(
                "backup destination already exists",
            ));
        }
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| MemoryRepositoryError::Io(error.to_string()))?;
        }
        let staging = destination.with_extension("staging");
        if staging.exists() {
            return Err(MemoryRepositoryError::Conflict(
                "backup staging destination already exists",
            ));
        }
        let mut backup_connection = Connection::open(&staging)?;
        {
            let backup = Backup::new(&self.connection, &mut backup_connection)?;
            backup.run_to_completion(64, Duration::from_millis(1), None)?;
        }
        let integrity: String =
            backup_connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
        if integrity != "ok" {
            let _ = fs::remove_file(&staging);
            return Err(MemoryRepositoryError::Conflict(
                "backup integrity check failed",
            ));
        }
        drop(backup_connection);
        fs::rename(&staging, destination)
            .map_err(|error| MemoryRepositoryError::Io(error.to_string()))
    }

    fn sync_index_in_transaction(
        transaction: &Transaction<'_>,
        memory_id: u128,
        observed_at_ms: Option<u64>,
    ) -> Result<(), MemoryRepositoryError> {
        let generation: i64 = transaction.query_row(
            "SELECT generation + 1 FROM memory_index_state WHERE index_name = 'memory_fts'",
            [],
            |row| row.get(0),
        )?;
        let current: Option<(String, String, String, String, Option<String>)> = transaction
            .query_row(
                "SELECT owner_id, scope_kind, lifecycle, validation, content FROM memory_records
                 WHERE memory_id = ?1",
                params![memory_id.to_string()],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .optional()?;
        transaction.execute(
            "DELETE FROM memory_records_fts WHERE memory_id = ?1",
            params![memory_id.to_string()],
        )?;
        if let Some((owner_id, scope_kind, lifecycle, validation, content)) = current {
            if matches!(lifecycle.as_str(), "CANDIDATE" | "ACTIVE")
                && matches!(validation.as_str(), "UNREVIEWED" | "ACCEPTED")
            {
                if let Some(content) = content {
                    transaction.execute(
                        "INSERT INTO memory_records_fts (memory_id, owner_id, scope_kind, content)
                         VALUES (?1, ?2, ?3, ?4)",
                        params![memory_id.to_string(), owner_id, scope_kind, content],
                    )?;
                }
            }
        }
        transaction.execute(
            "DELETE FROM memory_index_dirty WHERE memory_id = ?1",
            params![memory_id.to_string()],
        )?;
        let remaining: i64 =
            transaction.query_row("SELECT COUNT(*) FROM memory_index_dirty", [], |row| {
                row.get(0)
            })?;
        if let Some(observed_at_ms) = observed_at_ms {
            let updated_at = i64::try_from(observed_at_ms)
                .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?;
            if remaining == 0 {
                transaction.execute(
                    "UPDATE memory_index_state
                     SET generation = ?1, dirty_generation = ?1, updated_at_ms = ?2
                     WHERE index_name = 'memory_fts'",
                    params![generation, updated_at],
                )?;
            } else {
                transaction.execute(
                    "UPDATE memory_index_state
                     SET generation = ?1, updated_at_ms = ?2
                     WHERE index_name = 'memory_fts'",
                    params![generation, updated_at],
                )?;
            }
        } else if remaining == 0 {
            transaction.execute(
                "UPDATE memory_index_state
                 SET dirty_generation = generation, updated_at_ms = updated_at_ms
                 WHERE index_name = 'memory_fts'",
                [],
            )?;
        }
        Ok(())
    }

    fn is_index_dirty(&self, memory_id: u128) -> Result<bool, MemoryRepositoryError> {
        let count: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM memory_index_dirty WHERE memory_id = ?1",
            params![memory_id.to_string()],
            |row| row.get(0),
        )?;
        Ok(count > 0)
    }

    fn rebuild_pending_indexes(&mut self) -> Result<(), MemoryRepositoryError> {
        let pending = {
            let mut statement = self.connection.prepare(
                "SELECT memory_id FROM memory_index_dirty ORDER BY generation, memory_id",
            )?;
            statement
                .query_map([], |row| row.get::<_, String>(0))?
                .collect::<Result<Vec<_>, _>>()?
        };
        for memory_id in pending {
            let memory_id = parse_id(memory_id)?;
            self.rebuild_memory_index(memory_id)?;
        }
        Ok(())
    }

    fn rebuild_memory_index(&mut self, memory_id: u128) -> Result<(), MemoryRepositoryError> {
        let transaction = self.connection.transaction()?;
        Self::sync_index_in_transaction(&transaction, memory_id, None)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn grant_access(
        &mut self,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        can_read: bool,
        can_write: bool,
        expires_at_ms: Option<u64>,
        expected_revision: Option<u64>,
        updated_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        validate_identity(subject_id, owner_id, scope_kind, updated_at_ms)?;
        if !can_read && can_write {
            return Err(MemoryRepositoryError::InvalidInput(
                "write grant requires read grant",
            ));
        }
        if expires_at_ms.is_some_and(|expires| expires <= updated_at_ms) {
            return Err(MemoryRepositoryError::InvalidInput(
                "grant expiry must be in the future",
            ));
        }
        let expires_at_ms = expires_at_ms
            .map(|value| {
                i64::try_from(value)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("grant expiry out of range"))
            })
            .transpose()?;
        let transaction = self.connection.transaction()?;
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM memory_access_grants
                 WHERE subject_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![subject_id, owner_id, scope_kind],
                |row| row.get(0),
            )
            .optional()?;
        if let (Some(expected), Some(current)) = (expected_revision, current) {
            if u64::try_from(current).ok() != Some(expected) {
                return Err(MemoryRepositoryError::Conflict("grant revision conflict"));
            }
        } else if expected_revision.is_some() && current.is_none() {
            return Err(MemoryRepositoryError::Conflict("grant does not exist"));
        }
        let next_revision = current
            .map(parse_u64)
            .transpose()?
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(MemoryRepositoryError::Conflict("grant revision overflow"))?;
        transaction.execute(
            "INSERT INTO memory_access_grants
                (subject_id, owner_id, scope_kind, can_read, can_write, expires_at_ms,
                 revision, updated_at_ms, revoked_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, NULL)
             ON CONFLICT(subject_id, owner_id, scope_kind) DO UPDATE SET
                 can_read = excluded.can_read,
                 can_write = excluded.can_write,
                 expires_at_ms = excluded.expires_at_ms,
                 revision = excluded.revision,
                 updated_at_ms = excluded.updated_at_ms,
                 revoked_at_ms = NULL",
            params![
                subject_id,
                owner_id,
                scope_kind,
                can_read,
                can_write,
                expires_at_ms,
                i64::try_from(next_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("grant revision out of range"))?,
                i64::try_from(updated_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
            ],
        )?;
        transaction.commit()?;
        Ok(next_revision)
    }

    /// Grant access through the local service principal. The actor must
    /// already have write authority for the owner's scope; the owner itself
    /// is implicitly authorized. This keeps the storage API usable in tests
    /// while making the FFI path fail closed for an unrelated profile.
    pub fn grant_access_as(
        &mut self,
        actor_id: &str,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        can_read: bool,
        can_write: bool,
        expires_at_ms: Option<u64>,
        expected_revision: Option<u64>,
        updated_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        self.require_access(actor_id, owner_id, scope_kind, true, updated_at_ms)?;
        self.grant_access(
            subject_id,
            owner_id,
            scope_kind,
            can_read,
            can_write,
            expires_at_ms,
            expected_revision,
            updated_at_ms,
        )
    }

    pub fn revoke_access(
        &mut self,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        expected_revision: u64,
        revoked_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        validate_identity(subject_id, owner_id, scope_kind, revoked_at_ms)?;
        let transaction = self.connection.transaction()?;
        let current: i64 = transaction
            .query_row(
                "SELECT revision FROM memory_access_grants
                 WHERE subject_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![subject_id, owner_id, scope_kind],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(MemoryRepositoryError::Conflict("grant does not exist"))?;
        if u64::try_from(current).ok() != Some(expected_revision) {
            return Err(MemoryRepositoryError::Conflict("grant revision conflict"));
        }
        let next_revision = expected_revision
            .checked_add(1)
            .ok_or(MemoryRepositoryError::Conflict("grant revision overflow"))?;
        transaction.execute(
            "UPDATE memory_access_grants
             SET can_read = 0, can_write = 0, revision = ?1, revoked_at_ms = ?2,
                 updated_at_ms = ?2
             WHERE subject_id = ?3 AND owner_id = ?4 AND scope_kind = ?5 AND revision = ?6",
            params![
                i64::try_from(next_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("grant revision out of range"))?,
                i64::try_from(revoked_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                subject_id,
                owner_id,
                scope_kind,
                i64::try_from(expected_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("grant revision out of range"))?,
            ],
        )?;
        transaction.commit()?;
        Ok(next_revision)
    }

    pub fn revoke_access_as(
        &mut self,
        actor_id: &str,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        expected_revision: u64,
        revoked_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        self.require_access(actor_id, owner_id, scope_kind, true, revoked_at_ms)?;
        self.revoke_access(
            subject_id,
            owner_id,
            scope_kind,
            expected_revision,
            revoked_at_ms,
        )
    }

    fn require_access(
        &self,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        write: bool,
        now_ms: u64,
    ) -> Result<(), MemoryRepositoryError> {
        Self::require_access_from(
            &self.connection,
            subject_id,
            owner_id,
            scope_kind,
            write,
            now_ms,
        )
    }

    fn require_access_from(
        connection: &Connection,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        write: bool,
        now_ms: u64,
    ) -> Result<(), MemoryRepositoryError> {
        validate_identity(subject_id, owner_id, scope_kind, now_ms)?;
        if subject_id == owner_id {
            return Ok(());
        }
        let permission: Option<(i64, i64, Option<i64>, Option<i64>)> = connection
            .query_row(
                "SELECT can_read, can_write, expires_at_ms, revoked_at_ms
                 FROM memory_access_grants
                 WHERE subject_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![subject_id, owner_id, scope_kind],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .optional()?;
        let Some((can_read, can_write, expires_at_ms, revoked_at_ms)) = permission else {
            return Err(MemoryRepositoryError::PermissionDenied);
        };
        let active = revoked_at_ms.is_none()
            && expires_at_ms.is_none_or(|expires| {
                u64::try_from(expires)
                    .ok()
                    .is_some_and(|value| value > now_ms)
            });
        let allowed = if write { can_write == 1 } else { can_read == 1 };
        if active && allowed {
            Ok(())
        } else {
            Err(MemoryRepositoryError::PermissionDenied)
        }
    }

    pub fn capture(
        &mut self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
        content: &str,
        observed_at_ms: u64,
        request_id: u128,
    ) -> Result<MemoryCaptureOutcome, MemoryRepositoryError> {
        self.capture_with_kind_as(
            memory_id,
            owner_id,
            owner_id,
            scope_kind,
            DEFAULT_MEMORY_KIND,
            content,
            observed_at_ms,
            request_id,
        )
    }

    pub fn capture_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        content: &str,
        observed_at_ms: u64,
        request_id: u128,
    ) -> Result<MemoryCaptureOutcome, MemoryRepositoryError> {
        self.capture_with_kind_as(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            DEFAULT_MEMORY_KIND,
            content,
            observed_at_ms,
            request_id,
        )
    }

    pub fn capture_with_kind_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        memory_kind: &str,
        content: &str,
        observed_at_ms: u64,
        request_id: u128,
    ) -> Result<MemoryCaptureOutcome, MemoryRepositoryError> {
        self.capture_with_kind_as_provenance(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            memory_kind,
            content,
            observed_at_ms,
            request_id,
            None,
        )
    }

    /// Capture a candidate together with the sealed nudge provenance that
    /// produced it. The candidate remains CANDIDATE/UNREVIEWED until an
    /// explicit validation call promotes or rejects it.
    pub fn capture_nudged_candidate(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        memory_kind: &str,
        content: &str,
        observed_at_ms: u64,
        request_id: u128,
        provenance: &MemoryNudgeProvenance,
    ) -> Result<MemoryCaptureOutcome, MemoryRepositoryError> {
        if provenance.source_session_id == 0
            || provenance.nudge_id == 0
            || provenance.nudge_hash == [0; 32]
            || provenance.candidate_hash == [0; 32]
            || provenance.relevance_score_micros > 1_000_000
            || provenance.candidate_hash != MemoryCandidate::content_hash_for(content)
        {
            return Err(MemoryRepositoryError::InvalidInput(
                "invalid memory nudge provenance",
            ));
        }
        self.capture_with_kind_as_provenance(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            memory_kind,
            content,
            observed_at_ms,
            request_id,
            Some(provenance),
        )
    }

    fn capture_with_kind_as_provenance(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        memory_kind: &str,
        content: &str,
        observed_at_ms: u64,
        request_id: u128,
        provenance: Option<&MemoryNudgeProvenance>,
    ) -> Result<MemoryCaptureOutcome, MemoryRepositoryError> {
        self.require_access(subject_id, owner_id, scope_kind, true, observed_at_ms)?;
        if memory_id == 0 || request_id == 0 {
            return Err(MemoryRepositoryError::InvalidInput("ids must be non-zero"));
        }
        if owner_id.trim().is_empty() || owner_id.len() > 256 {
            return Err(MemoryRepositoryError::InvalidInput(
                "owner_id must be non-empty",
            ));
        }
        if !valid_scope(scope_kind) || scope_kind.len() > 64 {
            return Err(MemoryRepositoryError::InvalidInput("invalid scope_kind"));
        }
        if !valid_memory_kind(memory_kind) || memory_kind.len() > 64 {
            return Err(MemoryRepositoryError::InvalidInput("invalid memory_kind"));
        }
        if content.trim().is_empty()
            || content.len() > MAX_MEMORY_CONTENT_BYTES
            || observed_at_ms == 0
        {
            return Err(MemoryRepositoryError::InvalidInput(
                "content and observed_at_ms must be non-empty",
            ));
        }

        let content_hash = hash_content(memory_id, owner_id, scope_kind, content);
        let request_hash = hash_request(request_id, memory_id, owner_id, scope_kind, content);
        if let Some(existing) = self
            .connection
            .query_row(
                "SELECT request_hash, content_hash, memory_kind FROM memory_records WHERE request_id = ?1",
                params![request_id.to_string()],
                |row| {
                    let request_hash: Vec<u8> = row.get(0)?;
                    let content_hash: Vec<u8> = row.get(1)?;
                    let memory_kind: String = row.get(2)?;
                    Ok((request_hash, content_hash, memory_kind))
                },
            )
            .optional()?
        {
            if existing.0.as_slice() != request_hash || existing.2 != memory_kind {
                return Err(MemoryRepositoryError::IdempotencyConflict);
            }
            let existing_hash: [u8; 32] = existing
                .1
                .try_into()
                .map_err(|_| MemoryRepositoryError::Conflict("invalid stored content hash"))?;
            return Ok(MemoryCaptureOutcome {
                content_hash: existing_hash,
                created: false,
                index_pending: self.is_index_dirty(memory_id)?,
            });
        }

        if let Some(lifecycle) = self
            .connection
            .query_row(
                "SELECT lifecycle FROM memory_records WHERE memory_id = ?1",
                params![memory_id.to_string()],
                |row| row.get::<_, String>(0),
            )
            .optional()?
        {
            if matches!(lifecycle.as_str(), "TOMBSTONED" | "PURGED") {
                return Err(MemoryRepositoryError::Conflict(
                    "forgotten memory cannot be recaptured",
                ));
            }
            return Err(MemoryRepositoryError::Conflict("memory id already exists"));
        }

        let transaction = self.connection.transaction()?;
        transaction.execute(
            "INSERT INTO memory_records
                (memory_id, owner_id, scope_kind, memory_kind, content_hash, content, lifecycle,
                 validation, revision, observed_at_ms, request_id, request_hash)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'CANDIDATE', 'UNREVIEWED', 1, ?7, ?8, ?9)",
            params![
                memory_id.to_string(),
                owner_id,
                scope_kind,
                memory_kind,
                content_hash.as_slice(),
                content,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                request_id.to_string(),
                request_hash.as_slice(),
            ],
        )?;
        transaction.execute(
            "INSERT INTO memory_revisions
                (memory_id, revision, content_hash, content, created_at_ms, request_id)
             VALUES (?1, 1, ?2, ?3, ?4, ?5)",
            params![
                memory_id.to_string(),
                content_hash.as_slice(),
                content,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                request_id.to_string(),
            ],
        )?;
        insert_event(
            &transaction,
            memory_id,
            "MemoryCandidateCreated",
            1,
            request_id,
            request_hash,
            Some(content_hash),
            observed_at_ms,
        )?;
        if let Some(provenance) = provenance {
            transaction.execute(
                "INSERT INTO memory_nudge_provenance
                    (memory_id, source_session_id, nudge_id, nudge_hash, candidate_hash,
                     relevance_score_micros)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    memory_id.to_string(),
                    provenance.source_session_id.to_string(),
                    provenance.nudge_id.to_string(),
                    provenance.nudge_hash.as_slice(),
                    provenance.candidate_hash.as_slice(),
                    i64::from(provenance.relevance_score_micros),
                ],
            )?;
        }
        Self::sync_index_in_transaction(&transaction, memory_id, Some(observed_at_ms))?;
        transaction.commit()?;
        Ok(MemoryCaptureOutcome {
            content_hash,
            created: true,
            index_pending: false,
        })
    }

    pub fn inspect(
        &self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
    ) -> Result<Option<MemoryRecordView>, MemoryRepositoryError> {
        self.inspect_as(memory_id, owner_id, owner_id, scope_kind)
    }

    pub fn inspect_as(
        &self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
    ) -> Result<Option<MemoryRecordView>, MemoryRepositoryError> {
        self.require_access(subject_id, owner_id, scope_kind, false, current_time_ms())?;
        if memory_id == 0 || owner_id.trim().is_empty() || !valid_scope(scope_kind) {
            return Err(MemoryRepositoryError::InvalidInput("invalid memory lookup"));
        }
        self.connection
            .query_row(
                "SELECT r.memory_id, r.owner_id, r.scope_kind, r.memory_kind, r.lifecycle, r.validation,
                        r.validation_basis, r.validation_reason, r.revision,
                        r.observed_at_ms, r.content_hash, r.content,
                        p.source_session_id, p.nudge_id, p.nudge_hash, p.candidate_hash,
                        p.relevance_score_micros
                 FROM memory_records r
                 LEFT JOIN memory_nudge_provenance p ON p.memory_id = r.memory_id
                 WHERE r.memory_id = ?1 AND r.owner_id = ?2 AND r.scope_kind = ?3",
                params![memory_id.to_string(), owner_id, scope_kind],
                |row| {
                    let memory_kind: String = row.get(3)?;
                    if !valid_memory_kind(&memory_kind) {
                        return Err(rusqlite::Error::InvalidQuery);
                    }
                    Ok(MemoryRecordView {
                        memory_id: parse_id(row.get(0)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        owner_id: row.get(1)?,
                        scope_kind: row.get(2)?,
                        memory_kind,
                        lifecycle: row.get(4)?,
                        validation: row.get(5)?,
                        validation_basis: row.get(6)?,
                        validation_reason: row.get(7)?,
                        revision: parse_u64(row.get(8)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        observed_at_ms: parse_u64(row.get(9)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        content_hash: decode_hash(row.get(10)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        content: row.get(11)?,
                        source_session_id: parse_optional_id(row.get(12)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        nudge_id: parse_optional_id(row.get(13)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        nudge_hash: decode_hash(row.get(14)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        candidate_hash: decode_hash(row.get(15)?)
                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                        relevance_score_micros: row
                            .get::<_, Option<i64>>(16)?
                            .map(|value| {
                                u32::try_from(value).map_err(|_| rusqlite::Error::InvalidQuery)
                            })
                            .transpose()?,
                    })
                },
            )
            .optional()
            .map_err(MemoryRepositoryError::Sqlite)
    }

    /// Apply a human/agent validation decision to a live memory record.
    ///
    /// Validation is deliberately a storage-only operation: callers must
    /// provide the evidence basis or rejection reason, while no model or
    /// network call is allowed inside this transaction. Accepted records are
    /// promoted to ACTIVE and remain searchable; rejected/quarantined records
    /// remain candidates and are removed from the FTS projection.
    pub fn validate(
        &mut self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
        validation: &str,
        basis: Option<&str>,
        reason: Option<&str>,
        expected_revision: u64,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        self.validate_as(
            memory_id,
            owner_id,
            owner_id,
            scope_kind,
            validation,
            basis,
            reason,
            expected_revision,
            request_id,
            observed_at_ms,
        )
    }

    pub fn validate_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        validation: &str,
        basis: Option<&str>,
        reason: Option<&str>,
        expected_revision: u64,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        self.require_access(subject_id, owner_id, scope_kind, true, observed_at_ms)?;
        if memory_id == 0 || request_id == 0 || observed_at_ms == 0 {
            return Err(MemoryRepositoryError::InvalidInput(
                "ids and time must be non-zero",
            ));
        }
        if owner_id.trim().is_empty()
            || owner_id.len() > 256
            || !valid_scope(scope_kind)
            || scope_kind.len() > 64
        {
            return Err(MemoryRepositoryError::InvalidInput("invalid memory scope"));
        }
        let basis = basis.map(str::trim).filter(|value| !value.is_empty());
        let reason = reason.map(str::trim).filter(|value| !value.is_empty());
        match validation {
            "ACCEPTED" if basis.is_some() && reason.is_none() => {}
            "REJECTED" | "QUARANTINED" if reason.is_some() && basis.is_none() => {}
            "ACCEPTED" => {
                return Err(MemoryRepositoryError::InvalidInput(
                    "accepted validation requires a non-empty basis and no reason",
                ));
            }
            "REJECTED" | "QUARANTINED" => {
                return Err(MemoryRepositoryError::InvalidInput(
                    "rejected or quarantined validation requires a reason and no basis",
                ));
            }
            _ => {
                return Err(MemoryRepositoryError::InvalidInput(
                    "validation must be ACCEPTED, REJECTED, or QUARANTINED",
                ));
            }
        }
        if basis.is_some_and(|value| value.len() > 16 * 1024)
            || reason.is_some_and(|value| value.len() > 16 * 1024)
        {
            return Err(MemoryRepositoryError::InvalidInput(
                "validation evidence exceeds the maximum size",
            ));
        }
        let request_material = format!(
            "validation:{validation}:{}:{}",
            basis.unwrap_or_default(),
            reason.unwrap_or_default()
        );
        let request_hash = hash_request(
            request_id,
            memory_id,
            owner_id,
            scope_kind,
            &request_material,
        );
        if let Some(existing) = self
            .connection
            .query_row(
                "SELECT request_hash, revision FROM memory_events WHERE request_id = ?1",
                params![request_id.to_string()],
                |row| Ok((row.get::<_, Vec<u8>>(0)?, row.get::<_, i64>(1)?)),
            )
            .optional()?
        {
            if existing.0.as_slice() != request_hash {
                return Err(MemoryRepositoryError::IdempotencyConflict);
            }
            return parse_u64(existing.1);
        }

        let transaction = self.connection.transaction()?;
        let current: Option<(String, i64, Option<Vec<u8>>)> = transaction
            .query_row(
                "SELECT lifecycle, revision, content_hash FROM memory_records
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![memory_id.to_string(), owner_id, scope_kind],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .optional()?;
        let Some((lifecycle, revision, content_hash)) = current else {
            return Err(MemoryRepositoryError::Conflict("memory not found in scope"));
        };
        if lifecycle == "PURGED" || lifecycle == "TOMBSTONED" {
            return Err(MemoryRepositoryError::Conflict(
                "only a live memory can be validated",
            ));
        }
        let revision = parse_u64(revision)?;
        if revision != expected_revision {
            return Err(MemoryRepositoryError::Conflict("memory revision conflict"));
        }
        let next_revision = revision
            .checked_add(1)
            .ok_or(MemoryRepositoryError::Conflict("memory revision overflow"))?;
        let next_lifecycle = if validation == "ACCEPTED" {
            "ACTIVE"
        } else {
            "CANDIDATE"
        };
        let changed = transaction.execute(
            "UPDATE memory_records
             SET lifecycle = ?1, validation = ?2, validation_basis = ?3,
                 validation_reason = ?4, revision = ?5, observed_at_ms = ?6
             WHERE memory_id = ?7 AND owner_id = ?8 AND scope_kind = ?9 AND revision = ?10",
            params![
                next_lifecycle,
                validation,
                basis,
                reason,
                i64::try_from(next_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                memory_id.to_string(),
                owner_id,
                scope_kind,
                i64::try_from(expected_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
            ],
        )?;
        if changed != 1 {
            return Err(MemoryRepositoryError::Conflict("memory revision conflict"));
        }
        insert_event(
            &transaction,
            memory_id,
            "MemoryValidated",
            next_revision,
            request_id,
            request_hash,
            decode_hash(content_hash.clone())?,
            observed_at_ms,
        )?;
        Self::sync_index_in_transaction(&transaction, memory_id, Some(observed_at_ms))?;
        transaction.commit()?;
        Ok(next_revision)
    }

    pub fn search_candidates(
        &self,
        query: &str,
        owner_id: &str,
        scope_kind: &str,
        top_k: usize,
    ) -> Result<Vec<MemoryRecordView>, MemoryRepositoryError> {
        self.search_candidates_as(query, owner_id, owner_id, scope_kind, top_k)
    }

    pub fn search_candidates_as(
        &self,
        query: &str,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        top_k: usize,
    ) -> Result<Vec<MemoryRecordView>, MemoryRepositoryError> {
        self.require_access(subject_id, owner_id, scope_kind, false, current_time_ms())?;
        if query.trim().is_empty()
            || query.len() > MAX_MEMORY_QUERY_BYTES
            || owner_id.trim().is_empty()
            || owner_id.len() > 256
            || !valid_scope(scope_kind)
            || scope_kind.len() > 64
            || top_k == 0
            || top_k > 100
        {
            return Err(MemoryRepositoryError::InvalidInput("invalid memory search"));
        }
        let fts_terms = query
            .split_whitespace()
            .map(|term| {
                term.trim_matches(|ch: char| !ch.is_alphanumeric() && ch != '_' && ch != '-')
            })
            .filter(|term| term.chars().any(char::is_alphanumeric))
            .take(32)
            .map(|term| format!("\"{}\"", term.replace('"', "\"\"")))
            .collect::<Vec<_>>();
        if fts_terms.is_empty() {
            return Ok(Vec::new());
        }
        let mut statement = self.connection.prepare(
            "SELECT r.memory_id, r.owner_id, r.scope_kind, r.memory_kind, r.lifecycle, r.validation,
                    r.validation_basis, r.validation_reason, r.revision, r.observed_at_ms, r.content_hash,
                    p.source_session_id, p.nudge_id, p.nudge_hash, p.candidate_hash,
                    p.relevance_score_micros
             FROM memory_records_fts f
             JOIN memory_records r ON r.memory_id = f.memory_id
             LEFT JOIN memory_nudge_provenance p ON p.memory_id = r.memory_id
             WHERE memory_records_fts MATCH ?1
               AND r.owner_id = ?2 AND r.scope_kind = ?3
               AND r.lifecycle IN ('CANDIDATE', 'ACTIVE')
               AND r.validation IN ('UNREVIEWED', 'ACCEPTED')
             ORDER BY bm25(memory_records_fts), r.observed_at_ms DESC
             LIMIT ?4",
        )?;
        let limit = i64::try_from(top_k).unwrap_or(100);
        let run_fts = |statement: &mut rusqlite::Statement<'_>,
                       fts_expr: &str|
         -> Result<Vec<MemoryRecordView>, MemoryRepositoryError> {
            let rows =
                statement.query_map(params![fts_expr, owner_id, scope_kind, limit], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, Option<String>>(6)?,
                        row.get::<_, Option<String>>(7)?,
                        row.get::<_, i64>(8)?,
                        row.get::<_, i64>(9)?,
                        row.get::<_, Option<Vec<u8>>>(10)?,
                        row.get::<_, Option<String>>(11)?,
                        row.get::<_, Option<String>>(12)?,
                        row.get::<_, Option<Vec<u8>>>(13)?,
                        row.get::<_, Option<Vec<u8>>>(14)?,
                        row.get::<_, Option<i64>>(15)?,
                    ))
                })?;
            rows.map(|row| {
                let (
                    memory_id,
                    owner_id,
                    scope_kind,
                    memory_kind,
                    lifecycle,
                    validation,
                    validation_basis,
                    validation_reason,
                    revision,
                    observed_at_ms,
                    content_hash,
                    source_session_id,
                    nudge_id,
                    nudge_hash,
                    candidate_hash,
                    relevance_score_micros,
                ) = row?;
                Ok(MemoryRecordView {
                    memory_id: parse_id(memory_id)?,
                    owner_id,
                    scope_kind,
                    memory_kind: if valid_memory_kind(&memory_kind) {
                        memory_kind
                    } else {
                        return Err(MemoryRepositoryError::Conflict(
                            "invalid stored memory kind",
                        ));
                    },
                    lifecycle,
                    validation,
                    validation_basis,
                    validation_reason,
                    revision: parse_u64(revision)?,
                    observed_at_ms: parse_u64(observed_at_ms)?,
                    content_hash: decode_hash(content_hash)?,
                    content: None,
                    source_session_id: parse_optional_id(source_session_id)?,
                    nudge_id: parse_optional_id(nudge_id)?,
                    nudge_hash: decode_hash(nudge_hash)?,
                    candidate_hash: decode_hash(candidate_hash)?,
                    relevance_score_micros: relevance_score_micros
                        .map(|value| {
                            u32::try_from(value).map_err(|_| {
                                MemoryRepositoryError::Conflict("invalid stored score")
                            })
                        })
                        .transpose()?,
                })
            })
            .collect()
        };

        let and_query = fts_terms.join(" AND ");
        let mut results = run_fts(&mut statement, &and_query)?;
        if results.len() < top_k && fts_terms.len() > 1 {
            let or_query = fts_terms.join(" OR ");
            for candidate in run_fts(&mut statement, &or_query)? {
                if results.len() >= top_k {
                    break;
                }
                if !results
                    .iter()
                    .any(|existing| existing.memory_id == candidate.memory_id)
                {
                    results.push(candidate);
                }
            }
        }
        Ok(results)
    }

    pub fn link_file_relation_as(
        &mut self,
        relation_id: u128,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        project_id: &str,
        relative_path: &str,
        source_revision: &str,
        source_content_hash: [u8; 32],
        expected_memory_revision: u64,
        expected_memory_content_hash: [u8; 32],
        observed_at_ms: u64,
    ) -> Result<MemoryFileRelationView, MemoryRepositoryError> {
        validate_file_relation_input(
            relation_id,
            memory_id,
            project_id,
            relative_path,
            source_revision,
            expected_memory_revision,
            observed_at_ms,
        )?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        Self::require_access_from(
            &transaction,
            subject_id,
            owner_id,
            scope_kind,
            true,
            observed_at_ms,
        )?;

        let current: Option<CurrentMemoryRow> = transaction
            .query_row(
                "SELECT lifecycle, validation, revision, content_hash, content
                 FROM memory_records
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![memory_id.to_string(), owner_id, scope_kind],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .optional()?;
        let Some((lifecycle, validation, revision, content_hash, content)) = current else {
            return Err(MemoryRepositoryError::Conflict("memory not found in scope"));
        };
        if !matches!(lifecycle.as_str(), "CANDIDATE" | "ACTIVE")
            || !matches!(validation.as_str(), "UNREVIEWED" | "ACCEPTED")
            || content.is_none()
        {
            return Err(MemoryRepositoryError::Conflict(
                "memory is not eligible for a file relation",
            ));
        }
        if parse_u64(revision)? != expected_memory_revision {
            return Err(MemoryRepositoryError::Conflict("memory revision conflict"));
        }
        let current_hash = content_hash.ok_or(MemoryRepositoryError::Conflict(
            "memory content is unavailable",
        ))?;
        if current_hash.as_slice() != expected_memory_content_hash.as_slice() {
            return Err(MemoryRepositoryError::Conflict(
                "memory content hash conflict",
            ));
        }

        if let Some(existing) =
            load_memory_file_relation(&transaction, relation_id, owner_id, scope_kind)?
        {
            if existing.memory_id == memory_id
                && existing.project_id == project_id
                && existing.relative_path == relative_path
                && existing.source_revision == source_revision
                && existing.source_content_hash == source_content_hash
                && existing.memory_revision == expected_memory_revision
                && existing.memory_content_hash == expected_memory_content_hash
            {
                transaction.commit()?;
                return Ok(existing);
            }
            return Err(MemoryRepositoryError::Conflict(
                "file relation identifier has different evidence",
            ));
        }
        let relation_id_in_use: bool = transaction.query_row(
            "SELECT EXISTS(
                SELECT 1 FROM memory_file_relations WHERE relation_id = ?1
             )",
            params![relation_id.to_string()],
            |row| row.get(0),
        )?;
        if relation_id_in_use {
            return Err(MemoryRepositoryError::Conflict(
                "file relation identifier conflict",
            ));
        }

        let duplicate_target: Option<DuplicateRelationRow> = transaction
            .query_row(
                "SELECT relation_id, source_revision, source_content_hash,
                        memory_revision, memory_content_hash
                 FROM memory_file_relations
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3
                   AND project_id = ?4 AND relative_path = ?5",
                params![
                    memory_id.to_string(),
                    owner_id,
                    scope_kind,
                    project_id,
                    relative_path
                ],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .optional()?;
        if let Some((
            existing_relation_id,
            existing_source_revision,
            existing_source_hash,
            existing_memory_revision,
            existing_memory_hash,
        )) = duplicate_target
        {
            if existing_source_revision == source_revision
                && existing_source_hash.as_slice() == source_content_hash.as_slice()
                && parse_u64(existing_memory_revision)? == expected_memory_revision
                && existing_memory_hash.as_slice() == expected_memory_content_hash.as_slice()
            {
                let existing = load_memory_file_relation(
                    &transaction,
                    parse_id(existing_relation_id)?,
                    owner_id,
                    scope_kind,
                )?
                .ok_or(MemoryRepositoryError::Conflict(
                    "file relation could not be read after idempotent retry",
                ))?;
                transaction.commit()?;
                return Ok(existing);
            }
            return Err(MemoryRepositoryError::Conflict(
                "file relation target has different evidence",
            ));
        }

        transaction.execute(
            "INSERT INTO memory_file_relations
                (relation_id, memory_id, owner_id, scope_kind, project_id, relative_path,
                 source_revision, source_content_hash, memory_revision, memory_content_hash,
                 created_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                relation_id.to_string(),
                memory_id.to_string(),
                owner_id,
                scope_kind,
                project_id,
                relative_path,
                source_revision,
                source_content_hash.as_slice(),
                i64::try_from(expected_memory_revision).map_err(|_| {
                    MemoryRepositoryError::InvalidInput("memory revision is out of range")
                })?,
                expected_memory_content_hash.as_slice(),
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
            ],
        )?;
        let relation = load_memory_file_relation(&transaction, relation_id, owner_id, scope_kind)?
            .ok_or(MemoryRepositoryError::Conflict(
                "file relation could not be read after insertion",
            ))?;
        transaction.commit()?;
        Ok(relation)
    }

    pub fn list_file_relations_as(
        &mut self,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        project_id: &str,
        relative_path: &str,
    ) -> Result<Vec<MemoryFileRelationView>, MemoryRepositoryError> {
        validate_file_relation_target(project_id, relative_path)?;
        let transaction = self.connection.transaction()?;
        Self::require_access_from(
            &transaction,
            subject_id,
            owner_id,
            scope_kind,
            false,
            current_time_ms(),
        )?;
        let query = format!(
            "{SELECT_MEMORY_FILE_RELATION}
             WHERE relation.owner_id = ?1 AND relation.scope_kind = ?2
               AND relation.project_id = ?3 AND relation.relative_path = ?4
             ORDER BY relation.created_at_ms, relation.relation_id"
        );
        let relations = {
            let mut statement = transaction.prepare(&query)?;
            statement
                .query_map(
                    params![owner_id, scope_kind, project_id, relative_path],
                    stored_file_relation_from_row,
                )?
                .map(|row| decode_stored_file_relation(row?))
                .collect::<Result<Vec<_>, _>>()?
        };
        transaction.commit()?;
        Ok(relations)
    }

    pub fn list_file_relations_page_as(
        &mut self,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        project_id: &str,
        relative_path: &str,
        limit: usize,
        cursor: Option<MemoryFileRelationCursor>,
    ) -> Result<MemoryFileRelationPage, MemoryRepositoryError> {
        validate_file_relation_target(project_id, relative_path)?;
        if !(1..=MAX_FILE_RELATION_PAGE_SIZE).contains(&limit) {
            return Err(MemoryRepositoryError::InvalidInput(
                "file relation page limit is out of range",
            ));
        }
        if cursor.is_some_and(|value| value.created_at_ms == 0 || value.relation_id == 0) {
            return Err(MemoryRepositoryError::InvalidInput(
                "file relation page cursor is invalid",
            ));
        }
        let transaction = self.connection.transaction()?;
        Self::require_access_from(
            &transaction,
            subject_id,
            owner_id,
            scope_kind,
            false,
            current_time_ms(),
        )?;
        let query = match cursor {
            Some(_) => format!(
                "{SELECT_MEMORY_FILE_RELATION}
                 WHERE relation.owner_id = ?1 AND relation.scope_kind = ?2
                   AND relation.project_id = ?3 AND relation.relative_path = ?4
                   AND (relation.created_at_ms, length(relation.relation_id), relation.relation_id)
                       > (?5, length(?6), ?6)
                 ORDER BY relation.created_at_ms, length(relation.relation_id), relation.relation_id
                 LIMIT ?7"
            ),
            None => format!(
                "{SELECT_MEMORY_FILE_RELATION}
                 WHERE relation.owner_id = ?1 AND relation.scope_kind = ?2
                   AND relation.project_id = ?3 AND relation.relative_path = ?4
                 ORDER BY relation.created_at_ms, length(relation.relation_id), relation.relation_id
                 LIMIT ?5"
            ),
        };
        let fetch_limit = i64::try_from(limit + 1).map_err(|_| {
            MemoryRepositoryError::InvalidInput("file relation page limit is out of range")
        })?;
        let mut relations = {
            let mut statement = transaction.prepare(&query)?;
            match cursor {
                Some(value) => {
                    let created_at_ms = i64::try_from(value.created_at_ms).map_err(|_| {
                        MemoryRepositoryError::InvalidInput("file relation cursor is out of range")
                    })?;
                    statement
                        .query_map(
                            params![
                                owner_id,
                                scope_kind,
                                project_id,
                                relative_path,
                                created_at_ms,
                                value.relation_id.to_string(),
                                fetch_limit
                            ],
                            stored_file_relation_from_row,
                        )?
                        .map(|row| decode_stored_file_relation(row?))
                        .collect::<Result<Vec<_>, _>>()?
                }
                None => statement
                    .query_map(
                        params![owner_id, scope_kind, project_id, relative_path, fetch_limit],
                        stored_file_relation_from_row,
                    )?
                    .map(|row| decode_stored_file_relation(row?))
                    .collect::<Result<Vec<_>, _>>()?,
            }
        };
        let has_more = relations.len() > limit;
        relations.truncate(limit);
        let next_cursor = has_more
            .then(|| {
                relations.last().map(|last| MemoryFileRelationCursor {
                    created_at_ms: last.created_at_ms,
                    relation_id: last.relation_id,
                })
            })
            .flatten();
        transaction.commit()?;
        Ok(MemoryFileRelationPage {
            relations,
            next_cursor,
        })
    }

    pub fn list_current_file_relations_for_context_as(
        &mut self,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        project_id: &str,
        relative_path: &str,
        source_content_hash: &[u8; 32],
        limit: usize,
    ) -> Result<Vec<MemoryFileRelationView>, MemoryRepositoryError> {
        validate_file_relation_target(project_id, relative_path)?;
        if limit > MAX_FILE_RELATION_CONTEXT_RESULTS {
            return Err(MemoryRepositoryError::InvalidInput(
                "file relation context limit exceeds maximum",
            ));
        }
        let transaction = self.connection.transaction()?;
        Self::require_access_from(
            &transaction,
            subject_id,
            owner_id,
            scope_kind,
            false,
            current_time_ms(),
        )?;
        let query = format!(
            "{SELECT_MEMORY_FILE_RELATION}
             WHERE relation.owner_id = ?1 AND relation.scope_kind = ?2
               AND relation.project_id = ?3 AND relation.relative_path = ?4
               AND relation.source_content_hash = ?5
               AND memory.lifecycle = 'ACTIVE'
               AND memory.validation = 'ACCEPTED'
               AND memory.content_hash = relation.memory_content_hash
               AND memory.content IS NOT NULL
             ORDER BY relation.created_at_ms, relation.relation_id
             LIMIT ?6"
        );
        let limit = i64::try_from(limit)
            .map_err(|_| MemoryRepositoryError::InvalidInput("file relation limit out of range"))?;
        let relations = {
            let mut statement = transaction.prepare(&query)?;
            statement
                .query_map(
                    params![
                        owner_id,
                        scope_kind,
                        project_id,
                        relative_path,
                        source_content_hash.as_slice(),
                        limit
                    ],
                    stored_file_relation_from_row,
                )?
                .map(|row| decode_stored_file_relation(row?))
                .collect::<Result<Vec<_>, _>>()?
        };
        transaction.commit()?;
        Ok(relations)
    }

    pub fn unlink_file_relation_as(
        &mut self,
        relation_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        project_id: &str,
        relative_path: &str,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        if relation_id == 0 || observed_at_ms == 0 || i64::try_from(observed_at_ms).is_err() {
            return Err(MemoryRepositoryError::InvalidInput(
                "file relation identifier and time must be valid",
            ));
        }
        validate_file_relation_target(project_id, relative_path)?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        Self::require_access_from(
            &transaction,
            subject_id,
            owner_id,
            scope_kind,
            true,
            observed_at_ms,
        )?;
        let deleted = transaction.execute(
            "DELETE FROM memory_file_relations
             WHERE relation_id = ?1 AND owner_id = ?2 AND scope_kind = ?3
               AND project_id = ?4 AND relative_path = ?5",
            params![
                relation_id.to_string(),
                owner_id,
                scope_kind,
                project_id,
                relative_path
            ],
        )?;
        transaction.commit()?;
        Ok(deleted > 0)
    }

    pub fn forget(
        &mut self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        self.transition(
            memory_id,
            owner_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            "TOMBSTONED",
            "MemoryTombstoned",
            false,
            None,
        )
    }

    pub fn forget_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        self.forget_as_checked(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            None,
        )
    }

    pub fn forget_as_checked(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
        expected_revision: Option<u64>,
    ) -> Result<bool, MemoryRepositoryError> {
        self.transition(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            "TOMBSTONED",
            "MemoryTombstoned",
            false,
            expected_revision,
        )
    }

    pub fn correct(
        &mut self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
        content: &str,
        expected_revision: u64,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        self.correct_as(
            memory_id,
            owner_id,
            owner_id,
            scope_kind,
            content,
            expected_revision,
            request_id,
            observed_at_ms,
        )
    }

    pub fn correct_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        content: &str,
        expected_revision: u64,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<u64, MemoryRepositoryError> {
        self.require_access(subject_id, owner_id, scope_kind, true, observed_at_ms)?;
        if memory_id == 0 || request_id == 0 || observed_at_ms == 0 {
            return Err(MemoryRepositoryError::InvalidInput(
                "ids and time must be non-zero",
            ));
        }
        if owner_id.trim().is_empty()
            || owner_id.len() > 256
            || !valid_scope(scope_kind)
            || scope_kind.len() > 64
            || content.trim().is_empty()
            || content.len() > MAX_MEMORY_CONTENT_BYTES
        {
            return Err(MemoryRepositoryError::InvalidInput(
                "invalid correction input",
            ));
        }
        let request_hash = hash_request(request_id, memory_id, owner_id, scope_kind, content);
        if let Some(existing) = self
            .connection
            .query_row(
                "SELECT request_hash, revision FROM memory_events WHERE request_id = ?1",
                params![request_id.to_string()],
                |row| Ok((row.get::<_, Vec<u8>>(0)?, row.get::<_, i64>(1)?)),
            )
            .optional()?
        {
            if existing.0.as_slice() != request_hash {
                return Err(MemoryRepositoryError::IdempotencyConflict);
            }
            return parse_u64(existing.1);
        }

        let transaction = self.connection.transaction()?;
        let current: Option<(String, i64)> = transaction
            .query_row(
                "SELECT lifecycle, revision FROM memory_records
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![memory_id.to_string(), owner_id, scope_kind],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        let Some((lifecycle, revision)) = current else {
            return Err(MemoryRepositoryError::Conflict("memory not found in scope"));
        };
        if lifecycle == "PURGED" || lifecycle == "TOMBSTONED" {
            return Err(MemoryRepositoryError::Conflict(
                "only a live memory can be corrected",
            ));
        }
        let revision = parse_u64(revision)?;
        if revision != expected_revision {
            return Err(MemoryRepositoryError::Conflict("memory revision conflict"));
        }
        let next_revision = revision
            .checked_add(1)
            .ok_or(MemoryRepositoryError::Conflict("memory revision overflow"))?;
        let content_hash = hash_content(memory_id, owner_id, scope_kind, content);
        transaction.execute(
            "INSERT INTO memory_revisions
                (memory_id, revision, content_hash, content, created_at_ms, request_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                memory_id.to_string(),
                i64::try_from(next_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
                content_hash.as_slice(),
                content,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                request_id.to_string(),
            ],
        )?;
        let changed = transaction.execute(
            "UPDATE memory_records
             SET content_hash = ?1, content = ?2, lifecycle = 'CANDIDATE', validation = 'UNREVIEWED',
                 validation_basis = NULL, validation_reason = NULL,
                 revision = ?3, observed_at_ms = ?4
             WHERE memory_id = ?5 AND owner_id = ?6 AND scope_kind = ?7 AND revision = ?8",
            params![
                content_hash.as_slice(),
                content,
                i64::try_from(next_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                memory_id.to_string(),
                owner_id,
                scope_kind,
                i64::try_from(expected_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
            ],
        )?;
        if changed != 1 {
            return Err(MemoryRepositoryError::Conflict("memory revision conflict"));
        }
        insert_event(
            &transaction,
            memory_id,
            "MemoryCorrected",
            next_revision,
            request_id,
            request_hash,
            Some(content_hash),
            observed_at_ms,
        )?;
        Self::sync_index_in_transaction(&transaction, memory_id, Some(observed_at_ms))?;
        transaction.commit()?;
        Ok(next_revision)
    }

    pub fn restore(
        &mut self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        self.transition(
            memory_id,
            owner_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            "CANDIDATE",
            "MemoryRestored",
            true,
            None,
        )
    }

    pub fn restore_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        self.restore_as_checked(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            None,
        )
    }

    pub fn restore_as_checked(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
        expected_revision: Option<u64>,
    ) -> Result<bool, MemoryRepositoryError> {
        self.transition(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            "CANDIDATE",
            "MemoryRestored",
            true,
            expected_revision,
        )
    }

    pub fn purge(
        &mut self,
        memory_id: u128,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        self.transition(
            memory_id,
            owner_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            "PURGED",
            "MemoryPurged",
            false,
            None,
        )
    }

    pub fn purge_as(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
    ) -> Result<bool, MemoryRepositoryError> {
        self.purge_as_checked(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            None,
        )
    }

    pub fn purge_as_checked(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
        expected_revision: Option<u64>,
    ) -> Result<bool, MemoryRepositoryError> {
        self.transition(
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            request_id,
            observed_at_ms,
            "PURGED",
            "MemoryPurged",
            false,
            expected_revision,
        )
    }

    fn transition(
        &mut self,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        request_id: u128,
        observed_at_ms: u64,
        next_lifecycle: &str,
        event_type: &str,
        restore_candidate: bool,
        expected_revision: Option<u64>,
    ) -> Result<bool, MemoryRepositoryError> {
        self.require_access(subject_id, owner_id, scope_kind, true, observed_at_ms)?;
        if memory_id == 0 || request_id == 0 || observed_at_ms == 0 {
            return Err(MemoryRepositoryError::InvalidInput(
                "ids and time must be non-zero",
            ));
        }
        if owner_id.trim().is_empty()
            || owner_id.len() > 256
            || !valid_scope(scope_kind)
            || scope_kind.len() > 64
        {
            return Err(MemoryRepositoryError::InvalidInput("invalid memory scope"));
        }
        let request_hash = hash_request(
            request_id,
            memory_id,
            owner_id,
            scope_kind,
            &format!("{event_type}:{next_lifecycle}"),
        );
        if let Some(existing) = self
            .connection
            .query_row(
                "SELECT request_hash FROM memory_events WHERE request_id = ?1",
                params![request_id.to_string()],
                |row| row.get::<_, Vec<u8>>(0),
            )
            .optional()?
        {
            if existing.as_slice() != request_hash {
                return Err(MemoryRepositoryError::IdempotencyConflict);
            }
            return Ok(false);
        }

        let transaction = self.connection.transaction()?;
        let current: Option<MemoryStateRow> = transaction
            .query_row(
                "SELECT lifecycle, revision, content_hash, content FROM memory_records
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![memory_id.to_string(), owner_id, scope_kind],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .optional()?;
        let Some((current_lifecycle, revision, content_hash, content)) = current else {
            return Err(MemoryRepositoryError::Conflict("memory not found in scope"));
        };
        let revision = parse_u64(revision)?;
        if expected_revision.is_some_and(|expected| expected != revision) {
            return Err(MemoryRepositoryError::Conflict("memory revision conflict"));
        }
        let already_terminal = current_lifecycle == next_lifecycle;
        if next_lifecycle == "TOMBSTONED" && current_lifecycle == "PURGED" {
            return Err(MemoryRepositoryError::Conflict(
                "purged memory cannot be restored by forget",
            ));
        }
        if next_lifecycle == "CANDIDATE" && current_lifecycle != "TOMBSTONED" {
            return Err(MemoryRepositoryError::Conflict(
                "only tombstoned memory can be restored",
            ));
        }
        if already_terminal {
            return Ok(false);
        }
        let next_revision = revision
            .checked_add(1)
            .ok_or(MemoryRepositoryError::Conflict("memory revision overflow"))?;
        let (next_hash, next_content) = if restore_candidate {
            (content_hash, content)
        } else if next_lifecycle == "PURGED" {
            (None, None)
        } else {
            (content_hash, content)
        };
        let event_hash = decode_hash(next_hash.clone())?;
        transaction.execute(
            "UPDATE memory_records
             SET lifecycle = ?1,
                 validation = CASE WHEN ?1 = 'CANDIDATE' THEN 'UNREVIEWED' ELSE validation END,
                 validation_basis = CASE WHEN ?1 = 'CANDIDATE' THEN NULL ELSE validation_basis END,
                 validation_reason = CASE WHEN ?1 = 'CANDIDATE' THEN NULL ELSE validation_reason END,
                 revision = ?2, content_hash = ?3, content = ?4, observed_at_ms = ?5
             WHERE memory_id = ?6 AND owner_id = ?7 AND scope_kind = ?8 AND revision = ?9",
            params![
                next_lifecycle,
                i64::try_from(next_revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
                next_hash.as_deref(),
                next_content,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
                memory_id.to_string(),
                owner_id,
                scope_kind,
                i64::try_from(revision)
                    .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
            ],
        )?;
        if next_lifecycle == "PURGED" {
            transaction.execute(
                "DELETE FROM memory_revisions WHERE memory_id = ?1",
                params![memory_id.to_string()],
            )?;
        }
        if matches!(next_lifecycle, "TOMBSTONED" | "PURGED") {
            transaction.execute(
                "DELETE FROM memory_file_relations
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params![memory_id.to_string(), owner_id, scope_kind],
            )?;
        }
        insert_event(
            &transaction,
            memory_id,
            event_type,
            next_revision,
            request_id,
            request_hash,
            event_hash,
            observed_at_ms,
        )?;
        Self::sync_index_in_transaction(&transaction, memory_id, Some(observed_at_ms))?;
        transaction.commit()?;
        Ok(true)
    }
}

fn insert_event(
    transaction: &Transaction<'_>,
    memory_id: u128,
    event_type: &str,
    revision: u64,
    request_id: u128,
    request_hash: [u8; 32],
    content_hash: Option<[u8; 32]>,
    occurred_at_ms: u64,
) -> Result<(), MemoryRepositoryError> {
    transaction.execute(
        "INSERT INTO memory_events
            (event_id, memory_id, event_type, revision, request_id, request_hash,
             content_hash, occurred_at_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            format!("{memory_id}:{revision}:{event_type}"),
            memory_id.to_string(),
            event_type,
            i64::try_from(revision)
                .map_err(|_| MemoryRepositoryError::Conflict("memory revision out of range"))?,
            request_id.to_string(),
            request_hash.as_slice(),
            content_hash.as_ref().map(|hash| hash.as_slice()),
            i64::try_from(occurred_at_ms)
                .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
        ],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::memory::nudge::MemoryCandidate;
    use tempfile::tempdir;

    const RELATION_TEST_TIME_MS: u64 = 1_700_000_000_000;

    fn link_file(
        repository: &mut MemoryRepository,
        relation_id: u128,
        memory_id: u128,
        subject_id: &str,
        owner_id: &str,
        scope_kind: &str,
        project_id: &str,
        relative_path: &str,
        source_revision: &str,
        source_content_hash: [u8; 32],
        memory_revision: u64,
        memory_content_hash: [u8; 32],
    ) -> Result<MemoryFileRelationView, MemoryRepositoryError> {
        repository.link_file_relation_as(
            relation_id,
            memory_id,
            subject_id,
            owner_id,
            scope_kind,
            project_id,
            relative_path,
            source_revision,
            source_content_hash,
            memory_revision,
            memory_content_hash,
            RELATION_TEST_TIME_MS,
        )
    }

    #[test]
    fn file_relations_are_authorized_project_scoped_and_idempotent() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        let memory = repository
            .capture(
                900,
                "owner-a",
                "USER_PRIVATE",
                "a parser fact",
                RELATION_TEST_TIME_MS,
                9_001,
            )
            .unwrap();
        repository
            .grant_access(
                "reader",
                "owner-a",
                "USER_PRIVATE",
                true,
                false,
                Some(1_900_000_000_000),
                None,
                RELATION_TEST_TIME_MS + 1,
            )
            .unwrap();

        let first = link_file(
            &mut repository,
            9_002,
            900,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-a",
            [1; 32],
            1,
            memory.content_hash,
        )
        .unwrap();
        assert_eq!(first.memory_lifecycle, "CANDIDATE");
        assert_eq!(first.memory_validation, "UNREVIEWED");
        assert_eq!(first.project_id, "project-a");
        assert_eq!(first.relative_path, "src/parser.py");
        assert_eq!(first.source_revision, "rev-a");
        assert_eq!(first.source_content_hash, [1; 32]);
        assert_eq!(first.memory_content_hash, memory.content_hash);
        assert!(first.memory_content_current);
        let replay = link_file(
            &mut repository,
            9_002,
            900,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-a",
            [1; 32],
            1,
            memory.content_hash,
        )
        .unwrap();
        assert_eq!(replay, first);

        let other_project = link_file(
            &mut repository,
            9_003,
            900,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-b",
            "src/parser.py",
            "rev-b",
            [2; 32],
            1,
            memory.content_hash,
        )
        .unwrap();
        assert_ne!(other_project.relation_id, first.relation_id);
        let retry = link_file(
            &mut repository,
            9_004,
            900,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-a",
            [1; 32],
            1,
            memory.content_hash,
        )
        .unwrap();
        assert_eq!(retry, first);
        assert!(matches!(
            link_file(
                &mut repository,
                9_010,
                900,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-new",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict(
                "file relation target has different evidence"
            ))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_002,
                900,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-changed",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict(_))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_005,
                900,
                "reader",
                "owner-a",
                "USER_PRIVATE",
                "project-c",
                "src/parser.py",
                "rev-c",
                [3; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_006,
                900,
                "owner-b",
                "owner-b",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict("memory not found in scope"))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_007,
                900,
                "owner-a",
                "owner-a",
                "PROJECT",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict("memory not found in scope"))
        ));

        let foreign_memory = repository
            .capture(
                906,
                "owner-b",
                "USER_PRIVATE",
                "a private parser fact",
                RELATION_TEST_TIME_MS + 2,
                9_008,
            )
            .unwrap();
        link_file(
            &mut repository,
            9_009,
            906,
            "owner-b",
            "owner-b",
            "USER_PRIVATE",
            "private-project",
            "src/private.py",
            "private-rev",
            [8; 32],
            1,
            foreign_memory.content_hash,
        )
        .unwrap();
        assert!(matches!(
            link_file(
                &mut repository,
                9_009,
                900,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "private-project",
                "src/private.py",
                "private-rev",
                [8; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict(
                "file relation identifier conflict"
            ))
        ));

        let listed = repository
            .list_file_relations_as(
                "reader",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
            )
            .unwrap();
        assert_eq!(listed, vec![first.clone()]);
        assert_eq!(
            repository
                .list_file_relations_as(
                    "reader",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-b",
                    "src/parser.py",
                )
                .unwrap(),
            vec![other_project]
        );
        assert!(matches!(
            repository.list_file_relations_as(
                "unrelated",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
            ),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
        assert!(matches!(
            repository.unlink_file_relation_as(
                9_002,
                "reader",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                RELATION_TEST_TIME_MS + 2,
            ),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
        drop(repository);
        let mut reopened = MemoryRepository::open(path).unwrap();
        assert_eq!(
            reopened
                .list_file_relations_as(
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                )
                .unwrap(),
            vec![first]
        );
    }

    #[test]
    fn file_relation_context_query_filters_current_rows_before_limit() {
        let dir = tempdir().unwrap();
        let mut repository = MemoryRepository::open(dir.path().join("state.db")).unwrap();

        repository
            .capture(
                901,
                "owner-a",
                "USER_PRIVATE",
                "unreviewed parser candidate",
                RELATION_TEST_TIME_MS,
                9_101,
            )
            .unwrap();
        let candidate = repository
            .inspect(901, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        link_file(
            &mut repository,
            9_001,
            901,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-a",
            [1; 32],
            candidate.revision,
            candidate.content_hash.unwrap(),
        )
        .unwrap();

        repository
            .capture(
                902,
                "owner-a",
                "USER_PRIVATE",
                "accepted parser fact",
                RELATION_TEST_TIME_MS,
                9_102,
            )
            .unwrap();
        let accepted_before_validation = repository
            .inspect(902, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        repository
            .validate(
                902,
                "owner-a",
                "USER_PRIVATE",
                "ACCEPTED",
                Some("confirmed fact"),
                None,
                accepted_before_validation.revision,
                9_103,
                RELATION_TEST_TIME_MS + 1,
            )
            .unwrap();
        let accepted = repository
            .inspect(902, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        link_file(
            &mut repository,
            9_002,
            902,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-a",
            [1; 32],
            accepted.revision,
            accepted.content_hash.unwrap(),
        )
        .unwrap();

        repository
            .capture(
                903,
                "owner-a",
                "USER_PRIVATE",
                "accepted fact for an old parser file",
                RELATION_TEST_TIME_MS,
                9_104,
            )
            .unwrap();
        let stale_source_before_validation = repository
            .inspect(903, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        repository
            .validate(
                903,
                "owner-a",
                "USER_PRIVATE",
                "ACCEPTED",
                Some("confirmed fact"),
                None,
                stale_source_before_validation.revision,
                9_105,
                RELATION_TEST_TIME_MS + 1,
            )
            .unwrap();
        let stale_source = repository
            .inspect(903, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        link_file(
            &mut repository,
            9_003,
            903,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-old",
            [2; 32],
            stale_source.revision,
            stale_source.content_hash.unwrap(),
        )
        .unwrap();

        let ui_relations = repository
            .list_file_relations_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
            )
            .unwrap();
        assert_eq!(ui_relations.len(), 3, "UI listing must retain stale rows");

        let context_relations = repository
            .list_current_file_relations_for_context_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                &[1; 32],
                1,
            )
            .unwrap();
        assert_eq!(context_relations.len(), 1);
        assert_eq!(context_relations[0].relation_id, 9_002);
        assert_eq!(context_relations[0].memory_id, 902);
        assert_eq!(context_relations[0].source_content_hash, [1; 32]);
        assert!(
            repository
                .list_current_file_relations_for_context_as(
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                    &[1; 32],
                    0,
                )
                .unwrap()
                .is_empty()
        );
        assert!(matches!(
            repository.list_current_file_relations_for_context_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                &[1; 32],
                MAX_FILE_RELATION_CONTEXT_RESULTS + 1,
            ),
            Err(MemoryRepositoryError::InvalidInput(
                "file relation context limit exceeds maximum"
            ))
        ));
        assert!(matches!(
            repository.list_current_file_relations_for_context_as(
                "unrelated",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                &[1; 32],
                1,
            ),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
    }

    #[test]
    fn file_relation_history_uses_owner_scoped_keyset_pages_with_stable_ties() {
        let dir = tempdir().unwrap();
        let mut repository = MemoryRepository::open(dir.path().join("state.db")).unwrap();
        let query_plan = {
            let query = format!(
                "EXPLAIN QUERY PLAN {SELECT_MEMORY_FILE_RELATION}
                 WHERE relation.owner_id = ?1 AND relation.scope_kind = ?2
                   AND relation.project_id = ?3 AND relation.relative_path = ?4
                   AND (relation.created_at_ms, length(relation.relation_id), relation.relation_id)
                       > (?5, length(?6), ?6)
                 ORDER BY relation.created_at_ms, length(relation.relation_id), relation.relation_id
                 LIMIT ?7"
            );
            let mut statement = repository.connection.prepare(&query).unwrap();
            statement
                .query_map(
                    params![
                        "owner-a",
                        "USER_PRIVATE",
                        "project-a",
                        "src/parser.py",
                        RELATION_TEST_TIME_MS as i64,
                        "10",
                        3_i64
                    ],
                    |row| row.get::<_, String>(3),
                )
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
        };
        assert!(
            query_plan
                .iter()
                .any(|detail| detail.contains("memory_file_relations_owner_target_keyset"))
        );
        assert!(
            !query_plan
                .iter()
                .any(|detail| detail.contains("TEMP B-TREE"))
        );
        let relation_ids = [9_u128, 10, 99, 100, u128::MAX];
        for (index, relation_id) in relation_ids.into_iter().enumerate() {
            let memory_id = 500 + index as u128;
            repository
                .capture(
                    memory_id,
                    "owner-a",
                    "USER_PRIVATE",
                    &format!("memory {index}"),
                    RELATION_TEST_TIME_MS,
                    10_000 + index as u128,
                )
                .unwrap();
            let memory = repository
                .inspect(memory_id, "owner-a", "USER_PRIVATE")
                .unwrap()
                .unwrap();
            link_file(
                &mut repository,
                relation_id,
                memory_id,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                memory.revision,
                memory.content_hash.unwrap(),
            )
            .unwrap();
        }

        let first = repository
            .list_file_relations_page_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                2,
                None,
            )
            .unwrap();
        assert_eq!(
            first
                .relations
                .iter()
                .map(|item| item.relation_id)
                .collect::<Vec<_>>(),
            vec![9, 10]
        );
        assert_eq!(
            first.next_cursor,
            Some(MemoryFileRelationCursor {
                created_at_ms: RELATION_TEST_TIME_MS,
                relation_id: 10,
            })
        );

        let second = repository
            .list_file_relations_page_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                2,
                first.next_cursor,
            )
            .unwrap();
        assert_eq!(
            second
                .relations
                .iter()
                .map(|item| item.relation_id)
                .collect::<Vec<_>>(),
            vec![99, 100]
        );
        assert_eq!(second.next_cursor.unwrap().relation_id, 100);

        let final_page = repository
            .list_file_relations_page_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                2,
                second.next_cursor,
            )
            .unwrap();
        assert_eq!(
            final_page
                .relations
                .iter()
                .map(|item| item.relation_id)
                .collect::<Vec<_>>(),
            vec![u128::MAX]
        );
        assert!(final_page.next_cursor.is_none());
        assert!(
            repository
                .list_file_relations_page_as(
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                    0,
                    None,
                )
                .is_err()
        );
        assert!(
            repository
                .list_file_relations_page_as(
                    "outsider",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                    2,
                    None,
                )
                .is_err()
        );
    }

    #[test]
    fn file_relation_rejects_stale_memory_revision_or_hash() {
        let dir = tempdir().unwrap();
        let mut repository = MemoryRepository::open(dir.path().join("state.db")).unwrap();
        let memory = repository
            .capture(
                901,
                "owner-a",
                "USER_PRIVATE",
                "a parser fact",
                RELATION_TEST_TIME_MS,
                9_011,
            )
            .unwrap();

        assert!(matches!(
            link_file(
                &mut repository,
                9_012,
                901,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                2,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict("memory revision conflict"))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_013,
                901,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                1,
                [9; 32],
            ),
            Err(MemoryRepositoryError::Conflict(
                "memory content hash conflict"
            ))
        ));
        assert!(
            repository
                .list_file_relations_as(
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                )
                .unwrap()
                .is_empty()
        );
        repository
            .validate(
                901,
                "owner-a",
                "USER_PRIVATE",
                "REJECTED",
                None,
                Some("not eligible for file binding"),
                1,
                9_014,
                RELATION_TEST_TIME_MS + 1,
            )
            .unwrap();
        assert!(matches!(
            link_file(
                &mut repository,
                9_015,
                901,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                2,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::Conflict(
                "memory is not eligible for a file relation"
            ))
        ));
    }

    #[test]
    fn file_relation_content_correction_marks_stale_and_forget_removes_path_metadata() {
        let dir = tempdir().unwrap();
        let mut repository = MemoryRepository::open(dir.path().join("state.db")).unwrap();
        let memory = repository
            .capture(
                902,
                "owner-a",
                "USER_PRIVATE",
                "the parser accepts 100 items",
                RELATION_TEST_TIME_MS,
                9_021,
            )
            .unwrap();
        link_file(
            &mut repository,
            9_022,
            902,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/parser.py",
            "rev-a",
            [1; 32],
            1,
            memory.content_hash,
        )
        .unwrap();
        repository
            .correct(
                902,
                "owner-a",
                "USER_PRIVATE",
                "the parser accepts 200 items",
                1,
                9_023,
                RELATION_TEST_TIME_MS + 1,
            )
            .unwrap();

        let (corrected_revision, corrected_hash): (i64, Vec<u8>) = repository
            .connection
            .query_row(
                "SELECT revision, content_hash FROM memory_records
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
                params!["902", "owner-a", "USER_PRIVATE"],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert!(matches!(
            link_file(
                &mut repository,
                9_029,
                902,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                "rev-a",
                [1; 32],
                parse_u64(corrected_revision).unwrap(),
                corrected_hash.try_into().unwrap(),
            ),
            Err(MemoryRepositoryError::Conflict(
                "file relation target has different evidence"
            ))
        ));

        let stale = repository
            .list_file_relations_as(
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
            )
            .unwrap();
        assert_eq!(stale.len(), 1);
        assert!(!stale[0].memory_content_current);
        assert_eq!(stale[0].memory_revision, 1);
        assert_eq!(stale[0].memory_lifecycle, "CANDIDATE");

        repository
            .forget(
                902,
                "owner-a",
                "USER_PRIVATE",
                9_024,
                RELATION_TEST_TIME_MS + 2,
            )
            .unwrap();
        assert!(
            repository
                .list_file_relations_as(
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                )
                .unwrap()
                .is_empty()
        );
        repository
            .purge(
                902,
                "owner-a",
                "USER_PRIVATE",
                9_025,
                RELATION_TEST_TIME_MS + 3,
            )
            .unwrap();
        let purged_memory = repository
            .capture(
                905,
                "owner-a",
                "USER_PRIVATE",
                "another parser fact",
                RELATION_TEST_TIME_MS + 4,
                9_026,
            )
            .unwrap();
        link_file(
            &mut repository,
            9_027,
            905,
            "owner-a",
            "owner-a",
            "USER_PRIVATE",
            "project-a",
            "src/other_parser.py",
            "rev-a",
            [2; 32],
            1,
            purged_memory.content_hash,
        )
        .unwrap();
        repository
            .purge(
                905,
                "owner-a",
                "USER_PRIVATE",
                9_028,
                RELATION_TEST_TIME_MS + 5,
            )
            .unwrap();
        let relation_count: i64 = repository
            .connection
            .query_row("SELECT COUNT(*) FROM memory_file_relations", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(relation_count, 0);
    }

    #[test]
    fn file_relation_unlink_is_bound_to_project_and_relative_path() {
        let dir = tempdir().unwrap();
        let mut repository = MemoryRepository::open(dir.path().join("state.db")).unwrap();
        let memory = repository
            .capture(
                903,
                "owner-a",
                "USER_PRIVATE",
                "a parser fact",
                RELATION_TEST_TIME_MS,
                9_031,
            )
            .unwrap();
        for (relation_id, project_id) in [(9_032, "project-a"), (9_033, "project-b")] {
            link_file(
                &mut repository,
                relation_id,
                903,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                project_id,
                "src/parser.py",
                "rev-a",
                [1; 32],
                1,
                memory.content_hash,
            )
            .unwrap();
        }

        assert!(
            !repository
                .unlink_file_relation_as(
                    9_033,
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                    RELATION_TEST_TIME_MS + 1,
                )
                .unwrap()
        );
        assert!(
            !repository
                .unlink_file_relation_as(
                    9_033,
                    "owner-b",
                    "owner-b",
                    "USER_PRIVATE",
                    "project-b",
                    "src/parser.py",
                    RELATION_TEST_TIME_MS + 1,
                )
                .unwrap()
        );
        assert!(
            !repository
                .unlink_file_relation_as(
                    9_033,
                    "owner-a",
                    "owner-a",
                    "PROJECT",
                    "project-b",
                    "src/parser.py",
                    RELATION_TEST_TIME_MS + 1,
                )
                .unwrap()
        );
        assert!(
            repository
                .unlink_file_relation_as(
                    9_033,
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-b",
                    "src/parser.py",
                    RELATION_TEST_TIME_MS + 2,
                )
                .unwrap()
        );
        assert!(
            !repository
                .unlink_file_relation_as(
                    9_033,
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-b",
                    "src/parser.py",
                    RELATION_TEST_TIME_MS + 3,
                )
                .unwrap()
        );
        assert_eq!(
            repository
                .list_file_relations_as(
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    "project-a",
                    "src/parser.py",
                )
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn file_relation_rejects_non_normalized_or_absolute_paths() {
        let dir = tempdir().unwrap();
        let mut repository = MemoryRepository::open(dir.path().join("state.db")).unwrap();
        let memory = repository
            .capture(
                904,
                "owner-a",
                "USER_PRIVATE",
                "a parser fact",
                RELATION_TEST_TIME_MS,
                9_041,
            )
            .unwrap();

        for (index, path) in [
            "",
            "/src/parser.py",
            "C:/src/parser.py",
            "src/../parser.py",
            "src/./parser.py",
            "src//parser.py",
            "src\\parser.py",
            ".git/config",
            ".aegis/private",
            "src/.GIT/config",
        ]
        .into_iter()
        .enumerate()
        {
            assert!(
                matches!(
                    link_file(
                        &mut repository,
                        9_050 + index as u128,
                        904,
                        "owner-a",
                        "owner-a",
                        "USER_PRIVATE",
                        "project-a",
                        path,
                        "rev-a",
                        [1; 32],
                        1,
                        memory.content_hash,
                    ),
                    Err(MemoryRepositoryError::InvalidInput(_))
                ),
                "accepted invalid relative path {path:?}"
            );
        }
        assert!(matches!(
            link_file(
                &mut repository,
                9_060,
                904,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "",
                "src/parser.py",
                "rev-a",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::InvalidInput(_))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_063,
                904,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                &"p".repeat(MAX_FILE_RELATION_PROJECT_ID_BYTES + 1),
                "src/parser.py",
                "rev-a",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::InvalidInput(_))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_061,
                904,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                &"p".repeat(MAX_FILE_RELATION_PATH_BYTES + 1),
                "rev-a",
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::InvalidInput(_))
        ));
        assert!(matches!(
            link_file(
                &mut repository,
                9_062,
                904,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "project-a",
                "src/parser.py",
                &"r".repeat(MAX_FILE_RELATION_SOURCE_REVISION_BYTES + 1),
                [1; 32],
                1,
                memory.content_hash,
            ),
            Err(MemoryRepositoryError::InvalidInput(_))
        ));
    }

    #[test]
    fn capture_is_restart_persistent_and_idempotent() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let content = "user prefers a local first workflow";
        let first_hash;
        {
            let mut repository = MemoryRepository::open(&path).unwrap();
            let first = repository
                .capture(7, "owner-a", "USER_PRIVATE", content, 1_700_000_000_000, 71)
                .unwrap();
            assert!(first.created);
            first_hash = first.content_hash;
            let replay = repository
                .capture(7, "owner-a", "USER_PRIVATE", content, 1_700_000_000_000, 71)
                .unwrap();
            assert!(!replay.created);
            assert_eq!(replay.content_hash, first_hash);
        }
        let repository = MemoryRepository::open(&path).unwrap();
        let record = repository
            .inspect(7, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(record.content.as_deref(), Some(content));
        assert_eq!(record.lifecycle, "CANDIDATE");
        assert_eq!(record.revision, 1);
        assert_eq!(record.memory_kind, DEFAULT_MEMORY_KIND);
    }

    #[test]
    fn nudged_candidate_persists_provenance_and_replays_without_activation() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let content = "user prefers a bounded local workflow";
        let provenance = MemoryNudgeProvenance {
            source_session_id: 42,
            nudge_id: 77,
            nudge_hash: [9; 32],
            candidate_hash: MemoryCandidate::content_hash_for(content),
            relevance_score_micros: 850_000,
        };
        let mut repository = MemoryRepository::open(&path).unwrap();
        let first = repository
            .capture_nudged_candidate(
                700,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                DEFAULT_MEMORY_KIND,
                content,
                1_700_000_000_000,
                7_001,
                &provenance,
            )
            .unwrap();
        assert!(first.created);
        let replay = repository
            .capture_nudged_candidate(
                700,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                DEFAULT_MEMORY_KIND,
                content,
                1_700_000_000_000,
                7_001,
                &provenance,
            )
            .unwrap();
        assert!(!replay.created);

        let record = repository
            .inspect(700, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(record.lifecycle, "CANDIDATE");
        assert_eq!(record.validation, "UNREVIEWED");
        assert_eq!(record.source_session_id, Some(42));
        assert_eq!(record.nudge_id, Some(77));
        assert_eq!(record.nudge_hash, Some([9; 32]));
        assert_eq!(record.candidate_hash, Some(provenance.candidate_hash));
        assert_eq!(record.relevance_score_micros, Some(850_000));

        let results = repository
            .search_candidates("bounded local", "owner-a", "USER_PRIVATE", 5)
            .unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].source_session_id, Some(42));
        assert_eq!(results[0].lifecycle, "CANDIDATE");
    }

    #[test]
    fn semantic_memory_kind_is_explicit_and_physical_kinds_are_rejected() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        for (offset, kind) in [
            "USER_ASSERTION",
            "USER_PREFERENCE",
            "USER_WORKFLOW",
            "PROCEDURAL_SKILL",
        ]
        .into_iter()
        .enumerate()
        {
            let memory_id = 70 + offset as u128;
            repository
                .capture_with_kind_as(
                    memory_id,
                    "owner-a",
                    "owner-a",
                    "USER_PRIVATE",
                    kind,
                    "user prefers local first",
                    1_700_000_000_000 + offset as u64,
                    701 + offset as u128,
                )
                .unwrap();
            let record = repository
                .inspect(memory_id, "owner-a", "USER_PRIVATE")
                .unwrap()
                .unwrap();
            assert_eq!(record.memory_kind, kind);
        }
        assert!(matches!(
            repository.capture_with_kind_as(
                79,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "PHYSICAL_ARTIFACT",
                "must fail",
                1_700_000_000_010,
                710,
            ),
            Err(MemoryRepositoryError::InvalidInput("invalid memory_kind"))
        ));
    }

    #[test]
    fn scope_and_forget_are_enforced() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                8,
                "owner-a",
                "USER_PRIVATE",
                "private fact",
                1_700_000_000_000,
                81,
            )
            .unwrap();
        assert!(
            repository
                .inspect(8, "owner-b", "USER_PRIVATE")
                .unwrap()
                .is_none()
        );
        assert!(matches!(
            repository.forget_as_checked(
                8,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                82,
                1_700_000_000_001,
                Some(2),
            ),
            Err(MemoryRepositoryError::Conflict("memory revision conflict"))
        ));
        assert!(
            repository
                .forget(8, "owner-a", "USER_PRIVATE", 82, 1_700_000_000_001)
                .unwrap()
        );
        let record = repository
            .inspect(8, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(record.lifecycle, "TOMBSTONED");
        assert!(!record.content.is_none());
        assert!(
            repository
                .restore(8, "owner-a", "USER_PRIVATE", 83, 1_700_000_000_002)
                .unwrap()
        );
        assert_eq!(
            repository
                .inspect(8, "owner-a", "USER_PRIVATE")
                .unwrap()
                .unwrap()
                .lifecycle,
            "CANDIDATE"
        );
        assert!(
            repository
                .purge(8, "owner-a", "USER_PRIVATE", 84, 1_700_000_000_003)
                .unwrap()
        );
        let purged = repository
            .inspect(8, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(purged.lifecycle, "PURGED");
        assert!(purged.content.is_none());
        assert!(purged.content_hash.is_none());
    }

    #[test]
    fn correction_requires_current_revision_and_preserves_history() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                9,
                "owner-a",
                "USER_PRIVATE",
                "old fact",
                1_700_000_000_000,
                91,
            )
            .unwrap();
        assert!(matches!(
            repository.grant_access_as(
                "other-profile",
                "agent-1",
                "owner-a",
                "USER_PRIVATE",
                true,
                false,
                None,
                None,
                1_700_000_000_001,
            ),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
        assert_eq!(
            repository
                .correct(
                    9,
                    "owner-a",
                    "USER_PRIVATE",
                    "new fact",
                    1,
                    92,
                    1_700_000_000_001,
                )
                .unwrap(),
            2
        );
        assert!(
            repository
                .correct(
                    9,
                    "owner-a",
                    "USER_PRIVATE",
                    "stale correction",
                    1,
                    93,
                    1_700_000_000_002,
                )
                .is_err()
        );
        let current = repository
            .inspect(9, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(current.content.as_deref(), Some("new fact"));
        assert_eq!(current.revision, 2);
    }

    #[test]
    fn candidate_search_excludes_other_scopes_and_tombstones() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                10,
                "owner-a",
                "USER_PRIVATE",
                "provider preference fact",
                1_700_000_000_000,
                101,
            )
            .unwrap();
        repository
            .capture(
                11,
                "owner-b",
                "USER_PRIVATE",
                "provider preference fact",
                1_700_000_000_001,
                102,
            )
            .unwrap();
        assert_eq!(
            repository
                .search_candidates("provider preference", "owner-a", "USER_PRIVATE", 5)
                .unwrap()
                .len(),
            1
        );
        repository
            .forget(10, "owner-a", "USER_PRIVATE", 103, 1_700_000_000_002)
            .unwrap();
        assert!(
            repository
                .search_candidates("provider preference", "owner-a", "USER_PRIVATE", 5)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn online_backup_reopens_with_memory_content() {
        let dir = tempdir().unwrap();
        let source = dir.path().join("state.db");
        let backup = dir.path().join("backup.db");
        let mut repository = MemoryRepository::open(&source).unwrap();
        repository
            .capture(
                14,
                "owner-a",
                "USER_PRIVATE",
                "backup source",
                1_700_000_000_000,
                141,
            )
            .unwrap();
        repository.backup_to(&backup).unwrap();
        let restored = MemoryRepository::open(&backup).unwrap();
        assert_eq!(
            restored
                .inspect(14, "owner-a", "USER_PRIVATE")
                .unwrap()
                .unwrap()
                .content
                .as_deref(),
            Some("backup source")
        );
    }

    #[test]
    fn pending_index_rebuilds_after_source_commit() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        {
            let mut repository = MemoryRepository::open(&path).unwrap();
            repository
                .capture(
                    15,
                    "owner-a",
                    "USER_PRIVATE",
                    "rebuild after crash",
                    1_700_000_000_000,
                    151,
                )
                .unwrap();
            repository
                .connection
                .execute("DELETE FROM memory_records_fts WHERE memory_id = '15'", [])
                .unwrap();
            repository
                .connection
                .execute(
                    "INSERT INTO memory_index_dirty (memory_id, generation, operation)
                     VALUES ('15', 99, 'crash_recovery')",
                    [],
                )
                .unwrap();
        }
        let reopened = MemoryRepository::open(&path).unwrap();
        assert_eq!(
            reopened
                .search_candidates("rebuild crash", "owner-a", "USER_PRIVATE", 5)
                .unwrap()
                .len(),
            1
        );
        let dirty: i64 = reopened
            .connection
            .query_row("SELECT COUNT(*) FROM memory_index_dirty", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(dirty, 0);
    }

    #[test]
    fn grants_separate_subject_from_memory_owner_and_revoke_access() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                16,
                "owner-a",
                "USER_PRIVATE",
                "grant visible fact",
                1_700_000_000_000,
                161,
            )
            .unwrap();
        assert_eq!(
            repository
                .grant_access(
                    "agent-1",
                    "owner-a",
                    "USER_PRIVATE",
                    true,
                    false,
                    Some(1_900_000_000_000),
                    None,
                    1_700_000_000_001,
                )
                .unwrap(),
            1
        );
        assert!(
            repository
                .inspect_as(16, "agent-1", "owner-a", "USER_PRIVATE")
                .unwrap()
                .is_some()
        );
        assert!(matches!(
            repository.capture_as(
                17,
                "agent-1",
                "owner-a",
                "USER_PRIVATE",
                "agent write denied",
                1_700_000_000_002,
                171,
            ),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
        assert_eq!(
            repository
                .revoke_access("agent-1", "owner-a", "USER_PRIVATE", 1, 1_700_000_000_003,)
                .unwrap(),
            2
        );
        assert!(matches!(
            repository.inspect_as(16, "agent-1", "owner-a", "USER_PRIVATE"),
            Err(MemoryRepositoryError::PermissionDenied)
        ));
    }

    #[test]
    fn validation_controls_lifecycle_basis_and_search_projection() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                18,
                "owner-a",
                "USER_PRIVATE",
                "verified local preference",
                1_700_000_000_000,
                181,
            )
            .unwrap();
        assert_eq!(
            repository
                .validate(
                    18,
                    "owner-a",
                    "USER_PRIVATE",
                    "ACCEPTED",
                    Some("explicit user statement"),
                    None,
                    1,
                    182,
                    1_700_000_000_001,
                )
                .unwrap(),
            2
        );
        let accepted = repository
            .inspect(18, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(accepted.lifecycle, "ACTIVE");
        assert_eq!(accepted.validation, "ACCEPTED");
        assert_eq!(
            accepted.validation_basis.as_deref(),
            Some("explicit user statement")
        );
        assert!(accepted.validation_reason.is_none());
        assert_eq!(
            repository
                .search_candidates("verified local", "owner-a", "USER_PRIVATE", 5)
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            repository
                .validate(
                    18,
                    "owner-a",
                    "USER_PRIVATE",
                    "ACCEPTED",
                    Some("explicit user statement"),
                    None,
                    1,
                    182,
                    1_700_000_000_001,
                )
                .unwrap(),
            2
        );
        assert!(matches!(
            repository.validate(
                18,
                "owner-a",
                "USER_PRIVATE",
                "REJECTED",
                None,
                Some("stale evidence"),
                1,
                183,
                1_700_000_000_002,
            ),
            Err(MemoryRepositoryError::Conflict("memory revision conflict"))
        ));
        assert_eq!(
            repository
                .validate(
                    18,
                    "owner-a",
                    "USER_PRIVATE",
                    "REJECTED",
                    None,
                    Some("stale evidence"),
                    2,
                    184,
                    1_700_000_000_003,
                )
                .unwrap(),
            3
        );
        let rejected = repository
            .inspect(18, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(rejected.lifecycle, "CANDIDATE");
        assert_eq!(rejected.validation, "REJECTED");
        assert!(rejected.validation_basis.is_none());
        assert_eq!(
            rejected.validation_reason.as_deref(),
            Some("stale evidence")
        );
        assert!(
            repository
                .search_candidates("verified local", "owner-a", "USER_PRIVATE", 5)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn forgotten_memory_cannot_be_recaptured_under_same_id() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                19,
                "owner-a",
                "USER_PRIVATE",
                "forget me",
                1_700_000_000_000,
                191,
            )
            .unwrap();
        repository
            .forget(19, "owner-a", "USER_PRIVATE", 192, 1_700_000_000_001)
            .unwrap();
        assert!(matches!(
            repository.capture(
                19,
                "owner-a",
                "USER_PRIVATE",
                "new text",
                1_700_000_000_002,
                193,
            ),
            Err(MemoryRepositoryError::Conflict(
                "forgotten memory cannot be recaptured"
            ))
        ));
    }

    #[test]
    fn search_candidates_matches_natural_language_prompts_via_bm25_fallback() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture(
                20,
                "owner-a",
                "USER_PRIVATE",
                "PostgreSQL connection pool max_connections is 32",
                1_700_000_000_000,
                201,
            )
            .unwrap();
        repository
            .validate(
                20,
                "owner-a",
                "USER_PRIVATE",
                "ACCEPTED",
                Some("USER_CONFIRMED"),
                None,
                1,
                202,
                1_700_000_000_001,
            )
            .unwrap();

        let results = repository
            .search_candidates(
                "What should we set PostgreSQL max_connections to during deployment?",
                "owner-a",
                "USER_PRIVATE",
                5,
            )
            .unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].memory_id, 20);
        assert_eq!(results[0].lifecycle, "ACTIVE");
    }
}
