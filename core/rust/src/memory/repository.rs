//! Durable semantic-memory lifecycle owned by the local Rust store.
//!
//! Session search is a rebuildable projection. This module owns the
//! user-visible memory record state and its semantic events; it deliberately
//! does not promote a candidate to physical execution authority.

use blake3::Hasher;
use rusqlite::{Connection, OptionalExtension, Transaction, backup::Backup, params};
use std::fs;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const MAX_MEMORY_CONTENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_MEMORY_QUERY_BYTES: usize = 64 * 1024;
pub const DEFAULT_MEMORY_KIND: &str = "SEMANTIC_FACT";

/// Kinds accepted by the semantic-memory repository. Physical execution
/// artifacts are intentionally absent: they have a separate authority path.
pub const MEMORY_KINDS: [&str; 4] = [
    DEFAULT_MEMORY_KIND,
    "USER_ASSERTION",
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

    fn mark_index_dirty(
        transaction: &Transaction<'_>,
        memory_id: u128,
        operation: &str,
        observed_at_ms: u64,
    ) -> Result<(), MemoryRepositoryError> {
        let generation: i64 = transaction.query_row(
            "SELECT generation + 1 FROM memory_index_state WHERE index_name = 'memory_fts'",
            [],
            |row| row.get(0),
        )?;
        transaction.execute(
            "UPDATE memory_index_state
             SET generation = ?1, dirty_generation = ?1, updated_at_ms = ?2
             WHERE index_name = 'memory_fts'",
            params![
                generation,
                i64::try_from(observed_at_ms)
                    .map_err(|_| MemoryRepositoryError::InvalidInput("timestamp out of range"))?,
            ],
        )?;
        transaction.execute(
            "INSERT INTO memory_index_dirty (memory_id, generation, operation)
             VALUES (?1, ?2, ?3)
             ON CONFLICT(memory_id) DO UPDATE SET generation = excluded.generation, operation = excluded.operation",
            params![memory_id.to_string(), generation, operation],
        )?;
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
        if remaining == 0 {
            transaction.execute(
                "UPDATE memory_index_state
                 SET dirty_generation = generation, updated_at_ms = updated_at_ms
                 WHERE index_name = 'memory_fts'",
                [],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }

    fn finish_index(&mut self, memory_id: u128) -> Result<(), MemoryRepositoryError> {
        self.rebuild_memory_index(memory_id).map_err(|error| {
            MemoryRepositoryError::CommittedIndexPending {
                memory_id,
                cause: format!("{error:?}"),
            }
        })
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
        validate_identity(subject_id, owner_id, scope_kind, now_ms)?;
        if subject_id == owner_id {
            return Ok(());
        }
        let permission: Option<(i64, i64, Option<i64>, Option<i64>)> = self
            .connection
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
        Self::mark_index_dirty(&transaction, memory_id, "capture", observed_at_ms)?;
        transaction.commit()?;
        self.finish_index(memory_id)?;
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
                "SELECT memory_id, owner_id, scope_kind, memory_kind, lifecycle, validation,
                        validation_basis, validation_reason, revision,
                        observed_at_ms, content_hash, content
                FROM memory_records
                 WHERE memory_id = ?1 AND owner_id = ?2 AND scope_kind = ?3",
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
        Self::mark_index_dirty(&transaction, memory_id, "validate", observed_at_ms)?;
        transaction.commit()?;
        self.finish_index(memory_id)?;
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
        let fts_query = query
            .split_whitespace()
            .take(32)
            .map(|term| format!("\"{}\"", term.replace('"', "\"\"")))
            .collect::<Vec<_>>()
            .join(" AND ");
        if fts_query.is_empty() {
            return Ok(Vec::new());
        }
        let mut statement = self.connection.prepare(
            "SELECT r.memory_id, r.owner_id, r.scope_kind, r.memory_kind, r.lifecycle, r.validation,
                    r.validation_basis, r.validation_reason, r.revision, r.observed_at_ms, r.content_hash
             FROM memory_records_fts f
             JOIN memory_records r ON r.memory_id = f.memory_id
             WHERE memory_records_fts MATCH ?1
               AND r.owner_id = ?2 AND r.scope_kind = ?3
               AND r.lifecycle IN ('CANDIDATE', 'ACTIVE')
               AND r.validation IN ('UNREVIEWED', 'ACCEPTED')
             ORDER BY bm25(memory_records_fts), r.observed_at_ms DESC
             LIMIT ?4",
        )?;
        let rows = statement.query_map(
            params![
                fts_query,
                owner_id,
                scope_kind,
                i64::try_from(top_k).unwrap_or(100)
            ],
            |row| {
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
                ))
            },
        )?;
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
            })
        })
        .collect()
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
        Self::mark_index_dirty(&transaction, memory_id, "correct", observed_at_ms)?;
        transaction.commit()?;
        self.finish_index(memory_id)?;
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
        Self::mark_index_dirty(&transaction, memory_id, event_type, observed_at_ms)?;
        transaction.commit()?;
        self.finish_index(memory_id)?;
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
    use tempfile::tempdir;

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
    fn semantic_memory_kind_is_explicit_and_physical_kinds_are_rejected() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        let mut repository = MemoryRepository::open(&path).unwrap();
        repository
            .capture_with_kind_as(
                70,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "USER_ASSERTION",
                "user prefers local first",
                1_700_000_000_000,
                701,
            )
            .unwrap();
        let record = repository
            .inspect(70, "owner-a", "USER_PRIVATE")
            .unwrap()
            .unwrap();
        assert_eq!(record.memory_kind, "USER_ASSERTION");
        assert!(matches!(
            repository.capture_with_kind_as(
                71,
                "owner-a",
                "owner-a",
                "USER_PRIVATE",
                "PHYSICAL_ARTIFACT",
                "must fail",
                1_700_000_000_001,
                702,
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
}
