//! Durable, provider-independent conversation ownership.
//!
//! This repository stores canonical turns and parts. Provider adapters may
//! render them, but they do not own conversation identity or revision state.

use rusqlite::{Connection, OptionalExtension, params};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_ID_BYTES: usize = 128;
const MAX_TITLE_BYTES: usize = 256;
const MAX_MODEL_BYTES: usize = 256;
const MAX_CONNECTION_BYTES: usize = 128;
const MAX_ROLE_BYTES: usize = 32;
const MAX_PART_KIND_BYTES: usize = 32;
const MAX_STATUS_BYTES: usize = 32;
const MAX_PROVIDER_BYTES: usize = 64;
const MAX_CONTENT_BYTES: usize = 4 * 1024 * 1024;
const MAX_CONTINUATION_BYTES: usize = 256 * 1024;

#[derive(Debug)]
pub enum ConversationRepositoryError {
    Sqlite(rusqlite::Error),
    InvalidInput(&'static str),
    Conflict(&'static str),
    NotFound,
}

impl From<rusqlite::Error> for ConversationRepositoryError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConversationRecord {
    pub conversation_id: String,
    pub owner_id: String,
    pub title: String,
    pub connection_id: String,
    pub model_id: String,
    pub status: String,
    pub revision: u64,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConversationTurnRecord {
    pub conversation_id: String,
    pub turn_id: String,
    pub ordinal: u64,
    pub role: String,
    pub status: String,
    pub content: String,
    pub connection_id: Option<String>,
    pub model_id: Option<String>,
    pub revision: u64,
    pub created_at_ms: u64,
    pub finished_at_ms: Option<u64>,
    pub execution_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConversationSnapshot {
    pub conversation: ConversationRecord,
    pub turns: Vec<ConversationTurnRecord>,
    pub executions: Vec<ConversationExecutionRecord>,
    pub checkpoints: Vec<ConversationCheckpointRecord>,
    pub tool_calls: Vec<ConversationToolCallRecord>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConversationExecutionRecord {
    pub execution_id: String,
    pub conversation_id: String,
    pub turn_id: String,
    pub provider_kind: String,
    pub connection_id: String,
    pub model_id: String,
    pub status: String,
    pub checkpoint_seq: u64,
    pub revision: u64,
    pub started_at_ms: u64,
    pub finished_at_ms: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConversationCheckpointRecord {
    pub execution_id: String,
    pub sequence: u64,
    pub state: String,
    pub continuation_json: String,
    pub continuation_hash: String,
    pub created_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConversationToolCallRecord {
    pub call_id: String,
    pub conversation_id: String,
    pub request_turn_id: String,
    pub result_turn_id: Option<String>,
    pub tool_name: String,
    pub arguments_json: String,
    pub result_content: Option<String>,
    pub status: String,
    pub revision: u64,
    pub created_at_ms: u64,
    pub completed_at_ms: Option<u64>,
}

pub struct ConversationRepository {
    connection: Connection,
}

impl ConversationRepository {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, ConversationRepositoryError> {
        let mut connection = Connection::open(path)?;
        connection.execute_batch(
            "PRAGMA foreign_keys = ON;
             PRAGMA busy_timeout = 5000;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = FULL;
             CREATE TABLE IF NOT EXISTS conversations (
                 conversation_id TEXT PRIMARY KEY NOT NULL,
                 owner_id TEXT NOT NULL,
                 title TEXT NOT NULL,
                 connection_id TEXT NOT NULL,
                 model_id TEXT NOT NULL,
                 status TEXT NOT NULL,
                 revision INTEGER NOT NULL,
                 created_at_ms INTEGER NOT NULL,
                 updated_at_ms INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS conversations_owner_idx
                 ON conversations(owner_id, updated_at_ms DESC, conversation_id);
             CREATE TABLE IF NOT EXISTS conversation_turns (
                 conversation_id TEXT NOT NULL,
                 turn_id TEXT NOT NULL,
                 ordinal INTEGER NOT NULL,
                 role TEXT NOT NULL,
                 status TEXT NOT NULL,
                 connection_id TEXT,
                 model_id TEXT,
                 revision INTEGER NOT NULL,
                 created_at_ms INTEGER NOT NULL,
                 finished_at_ms INTEGER,
                 PRIMARY KEY (conversation_id, turn_id),
                 UNIQUE (conversation_id, ordinal),
                 FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
             );
             CREATE INDEX IF NOT EXISTS conversation_turns_order_idx
                 ON conversation_turns(conversation_id, ordinal);
             CREATE TABLE IF NOT EXISTS conversation_parts (
                 conversation_id TEXT NOT NULL,
                 turn_id TEXT NOT NULL,
                 part_index INTEGER NOT NULL,
                 kind TEXT NOT NULL,
                 content TEXT NOT NULL,
                 PRIMARY KEY (conversation_id, turn_id, part_index),
                 FOREIGN KEY (conversation_id, turn_id)
                    REFERENCES conversation_turns(conversation_id, turn_id)
             );
             CREATE TABLE IF NOT EXISTS conversation_executions (
                 execution_id TEXT PRIMARY KEY NOT NULL,
                 conversation_id TEXT NOT NULL,
                 turn_id TEXT NOT NULL,
                 provider_kind TEXT NOT NULL,
                 connection_id TEXT NOT NULL,
                 model_id TEXT NOT NULL,
                 status TEXT NOT NULL,
                 checkpoint_seq INTEGER NOT NULL,
                 revision INTEGER NOT NULL,
                 started_at_ms INTEGER NOT NULL,
                 finished_at_ms INTEGER,
                 UNIQUE (conversation_id, execution_id),
                 FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                 FOREIGN KEY (conversation_id, turn_id)
                    REFERENCES conversation_turns(conversation_id, turn_id)
             );
             CREATE INDEX IF NOT EXISTS conversation_executions_turn_idx
                 ON conversation_executions(conversation_id, turn_id, started_at_ms);
             CREATE TABLE IF NOT EXISTS conversation_checkpoints (
                 execution_id TEXT NOT NULL,
                 sequence INTEGER NOT NULL,
                 state TEXT NOT NULL,
                 continuation_json TEXT NOT NULL,
                 continuation_hash TEXT NOT NULL,
                 created_at_ms INTEGER NOT NULL,
                 PRIMARY KEY (execution_id, sequence),
                 FOREIGN KEY (execution_id) REFERENCES conversation_executions(execution_id)
             );
             CREATE TABLE IF NOT EXISTS conversation_tool_calls (
                 call_id TEXT PRIMARY KEY NOT NULL,
                 conversation_id TEXT NOT NULL,
                 request_turn_id TEXT NOT NULL,
                 result_turn_id TEXT,
                 tool_name TEXT NOT NULL,
                 arguments_json TEXT NOT NULL,
                 result_content TEXT,
                 status TEXT NOT NULL,
                 revision INTEGER NOT NULL,
                 created_at_ms INTEGER NOT NULL,
                 completed_at_ms INTEGER,
                 FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                 FOREIGN KEY (conversation_id, request_turn_id)
                    REFERENCES conversation_turns(conversation_id, turn_id),
                 FOREIGN KEY (conversation_id, result_turn_id)
                    REFERENCES conversation_turns(conversation_id, turn_id)
             );",
        )?;
        let has_execution_id: bool = connection.query_row(
            "SELECT EXISTS(
                 SELECT 1 FROM pragma_table_info('conversation_turns')
                 WHERE name = 'execution_id'
             )",
            [],
            |row| row.get(0),
        )?;
        if !has_execution_id {
            connection.execute(
                "ALTER TABLE conversation_turns ADD COLUMN execution_id TEXT",
                [],
            )?;
        }
        recover_incomplete_runs(&mut connection)?;
        Ok(Self { connection })
    }

    pub fn create_conversation(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        title: &str,
        connection_id: &str,
        model_id: &str,
        created_at_ms: u64,
    ) -> Result<ConversationRecord, ConversationRepositoryError> {
        validate_conversation(
            conversation_id,
            owner_id,
            title,
            connection_id,
            model_id,
            created_at_ms,
        )?;
        let transaction = self.connection.transaction()?;
        if transaction
            .query_row(
                "SELECT 1 FROM conversations WHERE conversation_id = ?1",
                params![conversation_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .is_some()
        {
            return Err(ConversationRepositoryError::Conflict(
                "conversation already exists",
            ));
        }
        transaction.execute(
            "INSERT INTO conversations
                (conversation_id, owner_id, title, connection_id, model_id, status,
                 revision, created_at_ms, updated_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, 'ACTIVE', 1, ?6, ?6)",
            params![
                conversation_id,
                owner_id,
                title,
                connection_id,
                model_id,
                i64::try_from(created_at_ms).map_err(|_| {
                    ConversationRepositoryError::InvalidInput("timestamp out of range")
                })?,
            ],
        )?;
        transaction.commit()?;
        self.get_conversation(conversation_id, owner_id)?
            .map(|snapshot| snapshot.conversation)
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn list_conversations(
        &self,
        owner_id: &str,
    ) -> Result<Vec<ConversationRecord>, ConversationRepositoryError> {
        if owner_id.trim().is_empty() || owner_id.len() > MAX_ID_BYTES {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid owner_id",
            ));
        }
        let mut statement = self.connection.prepare(
            "SELECT conversation_id, owner_id, title, connection_id, model_id, status,
                    revision, created_at_ms, updated_at_ms
             FROM conversations WHERE owner_id = ?1
             ORDER BY updated_at_ms DESC, conversation_id",
        )?;
        let rows = statement.query_map(params![owner_id], row_to_conversation)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn get_conversation(
        &self,
        conversation_id: &str,
        owner_id: &str,
    ) -> Result<Option<ConversationSnapshot>, ConversationRepositoryError> {
        if conversation_id.trim().is_empty()
            || conversation_id.len() > MAX_ID_BYTES
            || owner_id.trim().is_empty()
            || owner_id.len() > MAX_ID_BYTES
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid conversation identity",
            ));
        }
        let conversation = self
            .connection
            .query_row(
                "SELECT conversation_id, owner_id, title, connection_id, model_id, status,
                        revision, created_at_ms, updated_at_ms
                 FROM conversations WHERE conversation_id = ?1 AND owner_id = ?2",
                params![conversation_id, owner_id],
                row_to_conversation,
            )
            .optional()?;
        let Some(conversation) = conversation else {
            return Ok(None);
        };
        let mut statement = self.connection.prepare(
            "SELECT t.conversation_id, t.turn_id, t.ordinal, t.role, t.status,
                    COALESCE((SELECT group_concat(ordered.content, '')
                              FROM (SELECT p.content
                                    FROM conversation_parts p
                                    WHERE p.conversation_id = t.conversation_id
                                      AND p.turn_id = t.turn_id
                                    ORDER BY p.part_index) ordered), ''),
                    t.connection_id, t.model_id, t.revision, t.created_at_ms, t.finished_at_ms,
                    t.execution_id
             FROM conversation_turns t
             WHERE t.conversation_id = ?1 ORDER BY t.ordinal",
        )?;
        let rows = statement.query_map(params![conversation_id], row_to_turn)?;
        let turns = rows.collect::<Result<Vec<_>, _>>()?;
        let mut execution_statement = self.connection.prepare(
            "SELECT execution_id, conversation_id, turn_id, provider_kind, connection_id,
                    model_id, status, checkpoint_seq, revision, started_at_ms, finished_at_ms
             FROM conversation_executions WHERE conversation_id = ?1
             ORDER BY started_at_ms, execution_id",
        )?;
        let executions = execution_statement
            .query_map(params![conversation_id], row_to_execution)?
            .collect::<Result<Vec<_>, _>>()?;
        let mut checkpoint_statement = self.connection.prepare(
            "SELECT p.execution_id, p.sequence, p.state, p.continuation_json,
                    p.continuation_hash, p.created_at_ms
             FROM conversation_checkpoints p
             JOIN conversation_executions e ON e.execution_id = p.execution_id
             WHERE e.conversation_id = ?1
             ORDER BY p.execution_id, p.sequence",
        )?;
        let checkpoints = checkpoint_statement
            .query_map(params![conversation_id], row_to_checkpoint)?
            .collect::<Result<Vec<_>, _>>()?;
        let mut tool_statement = self.connection.prepare(
            "SELECT call_id, conversation_id, request_turn_id, result_turn_id, tool_name,
                    arguments_json, result_content, status, revision, created_at_ms, completed_at_ms
             FROM conversation_tool_calls WHERE conversation_id = ?1
             ORDER BY created_at_ms, call_id",
        )?;
        let tool_calls = tool_statement
            .query_map(params![conversation_id], row_to_tool_call)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Some(ConversationSnapshot {
            conversation,
            turns,
            executions,
            checkpoints,
            tool_calls,
        }))
    }

    pub fn append_turn(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        turn_id: &str,
        role: &str,
        content: &str,
        connection_id: Option<&str>,
        model_id: Option<&str>,
        status: &str,
        expected_revision: u64,
        created_at_ms: u64,
    ) -> Result<ConversationTurnRecord, ConversationRepositoryError> {
        validate_turn(
            conversation_id,
            owner_id,
            turn_id,
            role,
            content,
            connection_id,
            model_id,
            status,
            created_at_ms,
        )?;
        let transaction = self.connection.transaction()?;
        let (current_revision, current_status): (i64, String) = transaction
            .query_row(
                "SELECT revision, status FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2",
                params![conversation_id, owner_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        if current_status != "ACTIVE" {
            return Err(ConversationRepositoryError::Conflict(
                "conversation is not active",
            ));
        }
        if transaction
            .query_row(
                "SELECT 1 FROM conversation_turns
                 WHERE conversation_id = ?1 AND turn_id = ?2",
                params![conversation_id, turn_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .is_some()
        {
            return Err(ConversationRepositoryError::Conflict("turn already exists"));
        }
        let ordinal: i64 = transaction.query_row(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM conversation_turns
             WHERE conversation_id = ?1",
            params![conversation_id],
            |row| row.get(0),
        )?;
        let next_revision =
            expected_revision
                .checked_add(1)
                .ok_or(ConversationRepositoryError::Conflict(
                    "conversation revision overflow",
                ))?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                i64::try_from(next_revision)
                    .map_err(|_| ConversationRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(created_at_ms).map_err(|_| {
                    ConversationRepositoryError::InvalidInput("timestamp out of range")
                })?,
                conversation_id,
                owner_id,
                i64::try_from(expected_revision)
                    .map_err(|_| ConversationRepositoryError::Conflict("revision out of range"))?,
            ],
        )?;
        transaction.execute(
            "INSERT INTO conversation_turns
                (conversation_id, turn_id, ordinal, role, status, connection_id,
                 model_id, revision, created_at_ms, finished_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                conversation_id,
                turn_id,
                ordinal,
                role,
                status,
                connection_id,
                model_id,
                i64::try_from(next_revision)
                    .map_err(|_| ConversationRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(created_at_ms).map_err(|_| {
                    ConversationRepositoryError::InvalidInput("timestamp out of range")
                })?,
                if matches!(status, "COMPLETED" | "INTERRUPTED" | "FAILED") {
                    Some(i64::try_from(created_at_ms).map_err(|_| {
                        ConversationRepositoryError::InvalidInput("timestamp out of range")
                    })?)
                } else {
                    None
                },
            ],
        )?;
        transaction.execute(
            "INSERT INTO conversation_parts
                (conversation_id, turn_id, part_index, kind, content)
             VALUES (?1, ?2, 0, 'TEXT', ?3)",
            params![conversation_id, turn_id, content],
        )?;
        transaction.commit()?;
        self.get_turn(conversation_id, turn_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn start_execution(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        execution_id: &str,
        turn_id: &str,
        provider_kind: &str,
        connection_id: &str,
        model_id: &str,
        expected_revision: u64,
        started_at_ms: u64,
    ) -> Result<ConversationExecutionRecord, ConversationRepositoryError> {
        validate_identity(conversation_id, owner_id, turn_id)?;
        if execution_id.trim().is_empty()
            || execution_id.len() > MAX_ID_BYTES
            || provider_kind.trim().is_empty()
            || provider_kind.len() > MAX_PROVIDER_BYTES
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid execution identity",
            ));
        }
        validate_connection_model(connection_id, model_id, started_at_ms)?;
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2 AND status = 'ACTIVE'",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let turn_status: String = transaction
            .query_row(
                "SELECT status FROM conversation_turns
                 WHERE conversation_id = ?1 AND turn_id = ?2",
                params![conversation_id, turn_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if !matches!(turn_status.as_str(), "QUEUED" | "RUNNING") {
            return Err(ConversationRepositoryError::Conflict(
                "execution requires a pending or streaming turn",
            ));
        }
        if transaction
            .query_row(
                "SELECT 1 FROM conversation_executions WHERE execution_id = ?1",
                params![execution_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .is_some()
        {
            return Err(ConversationRepositoryError::Conflict(
                "execution already exists",
            ));
        }
        if transaction
            .query_row(
                "SELECT 1 FROM conversation_executions
                 WHERE conversation_id = ?1 AND turn_id = ?2 AND status = 'RUNNING'",
                params![conversation_id, turn_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .is_some()
        {
            return Err(ConversationRepositoryError::Conflict(
                "turn already has a running execution",
            ));
        }
        let revision = next_revision(expected_revision)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(started_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        if turn_status == "QUEUED" {
            transaction.execute(
                "UPDATE conversation_turns SET status = 'RUNNING', revision = ?1
                 WHERE conversation_id = ?2 AND turn_id = ?3 AND status = 'QUEUED'",
                params![to_i64_revision(revision)?, conversation_id, turn_id],
            )?;
        }
        transaction.execute(
            "UPDATE conversation_turns SET execution_id = ?1, revision = ?4
             WHERE conversation_id = ?2 AND turn_id = ?3",
            params![
                execution_id,
                conversation_id,
                turn_id,
                to_i64_revision(revision)?
            ],
        )?;
        transaction.execute(
            "INSERT INTO conversation_executions
                (execution_id, conversation_id, turn_id, provider_kind, connection_id,
                 model_id, status, checkpoint_seq, revision, started_at_ms, finished_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'RUNNING', 0, ?7, ?8, NULL)",
            params![
                execution_id,
                conversation_id,
                turn_id,
                provider_kind,
                connection_id,
                model_id,
                to_i64_revision(revision)?,
                to_i64_timestamp(started_at_ms)?,
            ],
        )?;
        transaction.commit()?;
        self.get_execution(execution_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn checkpoint_execution(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        execution_id: &str,
        sequence: u64,
        state: &str,
        continuation_json: &str,
        expected_revision: u64,
        created_at_ms: u64,
    ) -> Result<ConversationExecutionRecord, ConversationRepositoryError> {
        if conversation_id.trim().is_empty()
            || owner_id.trim().is_empty()
            || execution_id.trim().is_empty()
            || state.trim().is_empty()
            || state.len() > MAX_STATUS_BYTES
            || continuation_json.len() > MAX_CONTINUATION_BYTES
            || created_at_ms == 0
            || sequence == 0
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid execution checkpoint",
            ));
        }
        serde_json::from_str::<serde_json::Value>(continuation_json).map_err(|_| {
            ConversationRepositoryError::InvalidInput("continuation_json must be valid JSON")
        })?;
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT c.revision FROM conversations c
                 JOIN conversation_executions e ON e.conversation_id = c.conversation_id
                 WHERE c.conversation_id = ?1 AND c.owner_id = ?2 AND e.execution_id = ?3
                   AND c.status = 'ACTIVE' AND e.status = 'RUNNING'",
                params![conversation_id, owner_id, execution_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let current_sequence: i64 = transaction.query_row(
            "SELECT checkpoint_seq FROM conversation_executions WHERE execution_id = ?1",
            params![execution_id],
            |row| row.get(0),
        )?;
        let expected_sequence = parse_u64(current_sequence)?.checked_add(1).ok_or(
            ConversationRepositoryError::Conflict("checkpoint sequence overflow"),
        )?;
        if sequence != expected_sequence {
            return Err(ConversationRepositoryError::Conflict(
                "checkpoint sequence must advance by one",
            ));
        }
        let revision = next_revision(expected_revision)?;
        let continuation_hash = blake3::hash(continuation_json.as_bytes())
            .to_hex()
            .to_string();
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(created_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_executions SET checkpoint_seq = ?1, revision = ?2
             WHERE execution_id = ?3 AND status = 'RUNNING'",
            params![
                to_i64_revision(sequence)?,
                to_i64_revision(revision)?,
                execution_id
            ],
        )?;
        transaction.execute(
            "INSERT INTO conversation_checkpoints
                (execution_id, sequence, state, continuation_json, continuation_hash, created_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                execution_id,
                to_i64_revision(sequence)?,
                state,
                continuation_json,
                continuation_hash,
                to_i64_timestamp(created_at_ms)?,
            ],
        )?;
        transaction.commit()?;
        self.get_execution(execution_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn finish_execution(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        execution_id: &str,
        status: &str,
        expected_revision: u64,
        finished_at_ms: u64,
    ) -> Result<ConversationExecutionRecord, ConversationRepositoryError> {
        if !matches!(status, "COMPLETED" | "FAILED" | "CANCELLED" | "INTERRUPTED") {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid execution terminal status",
            ));
        }
        if finished_at_ms == 0 {
            return Err(ConversationRepositoryError::InvalidInput(
                "timestamp must be non-zero",
            ));
        }
        let transaction = self.connection.transaction()?;
        let (current_revision, turn_id): (i64, String) = transaction
            .query_row(
                "SELECT c.revision, e.turn_id FROM conversations c
                 JOIN conversation_executions e ON e.conversation_id = c.conversation_id
                 WHERE c.conversation_id = ?1 AND c.owner_id = ?2 AND e.execution_id = ?3
                   AND c.status = 'ACTIVE' AND e.status = 'RUNNING'",
                params![conversation_id, owner_id, execution_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let turn_status: String = transaction.query_row(
            "SELECT status FROM conversation_turns WHERE conversation_id = ?1 AND turn_id = ?2",
            params![conversation_id, turn_id],
            |row| row.get(0),
        )?;
        let next_turn_status = status;
        if !turn_transition_allowed(&turn_status, next_turn_status) {
            return Err(ConversationRepositoryError::Conflict(
                "execution cannot close the turn from its current status",
            ));
        }
        let revision = next_revision(expected_revision)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(finished_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_executions SET status = ?1, revision = ?2, finished_at_ms = ?3
             WHERE execution_id = ?4 AND status = 'RUNNING'",
            params![
                status,
                to_i64_revision(revision)?,
                to_i64_timestamp(finished_at_ms)?,
                execution_id
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_turns SET status = ?1, revision = ?2, finished_at_ms = ?3
             WHERE conversation_id = ?4 AND turn_id = ?5",
            params![
                next_turn_status,
                to_i64_revision(revision)?,
                to_i64_timestamp(finished_at_ms)?,
                conversation_id,
                turn_id,
            ],
        )?;
        transaction.commit()?;
        self.get_execution(execution_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn record_tool_call(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        call_id: &str,
        request_turn_id: &str,
        tool_name: &str,
        arguments_json: &str,
        expected_revision: u64,
        created_at_ms: u64,
    ) -> Result<ConversationToolCallRecord, ConversationRepositoryError> {
        validate_identity(conversation_id, owner_id, request_turn_id)?;
        if call_id.trim().is_empty()
            || call_id.len() > MAX_ID_BYTES
            || tool_name.trim().is_empty()
            || tool_name.len() > MAX_MODEL_BYTES
            || arguments_json.len() > MAX_CONTENT_BYTES
            || created_at_ms == 0
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid tool call",
            ));
        }
        let arguments =
            serde_json::from_str::<serde_json::Value>(arguments_json).map_err(|_| {
                ConversationRepositoryError::InvalidInput("arguments_json must be valid JSON")
            })?;
        if !arguments.is_object() {
            return Err(ConversationRepositoryError::InvalidInput(
                "arguments_json must be an object",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2 AND status = 'ACTIVE'",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let (role, turn_status): (String, String) = transaction
            .query_row(
                "SELECT role, status FROM conversation_turns
                 WHERE conversation_id = ?1 AND turn_id = ?2",
                params![conversation_id, request_turn_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if role != "assistant" || !matches!(turn_status.as_str(), "RUNNING" | "COMPLETED") {
            return Err(ConversationRepositoryError::Conflict(
                "tool calls must originate from a running or completed assistant turn",
            ));
        }
        if transaction
            .query_row(
                "SELECT 1 FROM conversation_tool_calls WHERE call_id = ?1",
                params![call_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .is_some()
        {
            return Err(ConversationRepositoryError::Conflict(
                "tool call already exists",
            ));
        }
        let revision = next_revision(expected_revision)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(created_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "INSERT INTO conversation_tool_calls
                (call_id, conversation_id, request_turn_id, result_turn_id, tool_name,
                 arguments_json, result_content, status, revision, created_at_ms, completed_at_ms)
             VALUES (?1, ?2, ?3, NULL, ?4, ?5, NULL, 'REQUESTED', ?6, ?7, NULL)",
            params![
                call_id,
                conversation_id,
                request_turn_id,
                tool_name,
                arguments_json,
                to_i64_revision(revision)?,
                to_i64_timestamp(created_at_ms)?,
            ],
        )?;
        transaction.commit()?;
        self.get_tool_call(call_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn record_tool_result(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        call_id: &str,
        result_turn_id: &str,
        result_content: &str,
        status: &str,
        expected_revision: u64,
        completed_at_ms: u64,
    ) -> Result<ConversationToolCallRecord, ConversationRepositoryError> {
        validate_identity(conversation_id, owner_id, result_turn_id)?;
        if call_id.trim().is_empty()
            || call_id.len() > MAX_ID_BYTES
            || result_content.len() > MAX_CONTENT_BYTES
            || result_content.is_empty()
            || !matches!(status, "COMPLETED" | "FAILED")
            || completed_at_ms == 0
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid tool result",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2 AND status = 'ACTIVE'",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let call_status: String = transaction
            .query_row(
                "SELECT status FROM conversation_tool_calls
                 WHERE call_id = ?1 AND conversation_id = ?2",
                params![call_id, conversation_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if call_status != "REQUESTED" {
            return Err(ConversationRepositoryError::Conflict(
                "tool call has already been settled",
            ));
        }
        let role: String = transaction
            .query_row(
                "SELECT role FROM conversation_turns WHERE conversation_id = ?1 AND turn_id = ?2",
                params![conversation_id, result_turn_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if role != "tool" {
            return Err(ConversationRepositoryError::Conflict(
                "tool result must be recorded on a tool turn",
            ));
        }
        let revision = next_revision(expected_revision)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(completed_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_tool_calls
             SET result_turn_id = ?1, result_content = ?2, status = ?3,
                 revision = ?4, completed_at_ms = ?5
             WHERE call_id = ?6 AND status = 'REQUESTED'",
            params![
                result_turn_id,
                result_content,
                status,
                to_i64_revision(revision)?,
                to_i64_timestamp(completed_at_ms)?,
                call_id,
            ],
        )?;
        transaction.commit()?;
        self.get_tool_call(call_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn reconcile_tool_call(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        call_id: &str,
        status: &str,
        note: &str,
        expected_revision: u64,
        reconciled_at_ms: u64,
    ) -> Result<ConversationToolCallRecord, ConversationRepositoryError> {
        validate_identity(conversation_id, owner_id, call_id)?;
        if !matches!(status, "CANCELLED" | "FAILED")
            || note.is_empty()
            || note.len() > MAX_CONTENT_BYTES
            || reconciled_at_ms == 0
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid tool reconciliation",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT c.revision FROM conversations c
                 JOIN conversation_tool_calls t ON t.conversation_id = c.conversation_id
                 WHERE c.conversation_id = ?1 AND c.owner_id = ?2 AND t.call_id = ?3
                   AND c.status = 'ACTIVE' AND t.status = 'AMBIGUOUS'",
                params![conversation_id, owner_id, call_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let revision = next_revision(expected_revision)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(reconciled_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_tool_calls
             SET status = ?1, result_content = ?2, revision = ?3, completed_at_ms = ?4
             WHERE call_id = ?5 AND conversation_id = ?6 AND status = 'AMBIGUOUS'",
            params![
                status,
                note,
                to_i64_revision(revision)?,
                to_i64_timestamp(reconciled_at_ms)?,
                call_id,
                conversation_id,
            ],
        )?;
        transaction.commit()?;
        self.get_tool_call(call_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn append_turn_part(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        turn_id: &str,
        kind: &str,
        content: &str,
        expected_revision: u64,
        created_at_ms: u64,
    ) -> Result<ConversationTurnRecord, ConversationRepositoryError> {
        validate_identity(conversation_id, owner_id, turn_id)?;
        if kind.trim().is_empty()
            || kind.len() > MAX_PART_KIND_BYTES
            || content.is_empty()
            || content.len() > MAX_CONTENT_BYTES
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid conversation part",
            ));
        }
        validate_part_kind(kind)?;
        if created_at_ms == 0 {
            return Err(ConversationRepositoryError::InvalidInput(
                "timestamp must be non-zero",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2 AND status = 'ACTIVE'",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let turn_status: String = transaction
            .query_row(
                "SELECT status FROM conversation_turns
                 WHERE conversation_id = ?1 AND turn_id = ?2",
                params![conversation_id, turn_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if !matches!(
            turn_status.as_str(),
            "QUEUED" | "RUNNING" | "WAITING_APPROVAL"
        ) {
            return Err(ConversationRepositoryError::Conflict(
                "parts can only be appended to a pending or streaming turn",
            ));
        }
        let part_index: i64 = transaction.query_row(
            "SELECT COALESCE(MAX(part_index), -1) + 1 FROM conversation_parts
             WHERE conversation_id = ?1 AND turn_id = ?2",
            params![conversation_id, turn_id],
            |row| row.get(0),
        )?;
        let next_revision = next_revision(expected_revision)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(next_revision)?,
                to_i64_timestamp(created_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_turns SET revision = ?1
             WHERE conversation_id = ?2 AND turn_id = ?3",
            params![to_i64_revision(next_revision)?, conversation_id, turn_id],
        )?;
        transaction.execute(
            "INSERT INTO conversation_parts
                (conversation_id, turn_id, part_index, kind, content)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![conversation_id, turn_id, part_index, kind, content],
        )?;
        transaction.commit()?;
        self.get_turn(conversation_id, turn_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn transition_turn(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        turn_id: &str,
        status: &str,
        expected_revision: u64,
        updated_at_ms: u64,
    ) -> Result<ConversationTurnRecord, ConversationRepositoryError> {
        validate_identity(conversation_id, owner_id, turn_id)?;
        validate_turn_status(status)?;
        if updated_at_ms == 0 {
            return Err(ConversationRepositoryError::InvalidInput(
                "timestamp must be non-zero",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current_revision: i64 = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2 AND status = 'ACTIVE'",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if parse_u64(current_revision)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let current_status: String = transaction
            .query_row(
                "SELECT status FROM conversation_turns
                 WHERE conversation_id = ?1 AND turn_id = ?2",
                params![conversation_id, turn_id],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(ConversationRepositoryError::NotFound)?;
        if !turn_transition_allowed(&current_status, status) {
            return Err(ConversationRepositoryError::Conflict(
                "invalid conversation turn status transition",
            ));
        }
        let next_revision = next_revision(expected_revision)?;
        let finished_at = if matches!(status, "COMPLETED" | "INTERRUPTED" | "FAILED") {
            Some(to_i64_timestamp(updated_at_ms)?)
        } else {
            None
        };
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND owner_id = ?4 AND revision = ?5",
            params![
                to_i64_revision(next_revision)?,
                to_i64_timestamp(updated_at_ms)?,
                conversation_id,
                owner_id,
                to_i64_revision(expected_revision)?,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_turns SET status = ?1, revision = ?2, finished_at_ms = ?3
             WHERE conversation_id = ?4 AND turn_id = ?5",
            params![
                status,
                to_i64_revision(next_revision)?,
                finished_at,
                conversation_id,
                turn_id
            ],
        )?;
        transaction.commit()?;
        self.get_turn(conversation_id, turn_id)?
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn switch_model(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        connection_id: &str,
        model_id: &str,
        expected_revision: u64,
        updated_at_ms: u64,
    ) -> Result<ConversationRecord, ConversationRepositoryError> {
        validate_connection_model(connection_id, model_id, updated_at_ms)?;
        let transaction = self.connection.transaction()?;
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2 AND status = 'ACTIVE'",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?;
        let Some(current) = current else {
            return Err(ConversationRepositoryError::NotFound);
        };
        if parse_u64(current)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        if transaction
            .query_row(
                "SELECT 1 FROM conversation_turns
                 WHERE conversation_id = ?1
                   AND status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL')
                 LIMIT 1",
                params![conversation_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .is_some()
            || transaction
                .query_row(
                    "SELECT 1 FROM conversation_tool_calls
                     WHERE conversation_id = ?1 AND status IN ('REQUESTED', 'AMBIGUOUS')
                     LIMIT 1",
                    params![conversation_id],
                    |row| row.get::<_, i64>(0),
                )
                .optional()?
                .is_some()
            || transaction
                .query_row(
                    "SELECT 1 FROM conversation_parts
                     WHERE conversation_id = ?1 AND kind <> 'TEXT'
                     LIMIT 1",
                    params![conversation_id],
                    |row| row.get::<_, i64>(0),
                )
                .optional()?
                .is_some()
            || transaction
                .query_row(
                    "SELECT 1 FROM conversation_tool_calls
                     WHERE conversation_id = ?1
                     LIMIT 1",
                    params![conversation_id],
                    |row| row.get::<_, i64>(0),
                )
                .optional()?
                .is_some()
        {
            return Err(ConversationRepositoryError::Conflict(
                "model switch requires resolved state and verified target capabilities",
            ));
        }
        let next_revision =
            expected_revision
                .checked_add(1)
                .ok_or(ConversationRepositoryError::Conflict(
                    "conversation revision overflow",
                ))?;
        transaction.execute(
            "UPDATE conversations SET connection_id = ?1, model_id = ?2,
                    revision = ?3, updated_at_ms = ?4
             WHERE conversation_id = ?5 AND owner_id = ?6 AND revision = ?7",
            params![
                connection_id,
                model_id,
                i64::try_from(next_revision)
                    .map_err(|_| ConversationRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(updated_at_ms).map_err(|_| {
                    ConversationRepositoryError::InvalidInput("timestamp out of range")
                })?,
                conversation_id,
                owner_id,
                current,
            ],
        )?;
        transaction.commit()?;
        self.get_conversation(conversation_id, owner_id)?
            .map(|snapshot| snapshot.conversation)
            .ok_or(ConversationRepositoryError::NotFound)
    }

    pub fn set_status(
        &mut self,
        conversation_id: &str,
        owner_id: &str,
        status: &str,
        expected_revision: u64,
        updated_at_ms: u64,
    ) -> Result<ConversationRecord, ConversationRepositoryError> {
        validate_status(status)?;
        if conversation_id.trim().is_empty()
            || conversation_id.len() > MAX_ID_BYTES
            || owner_id.trim().is_empty()
            || owner_id.len() > MAX_ID_BYTES
            || updated_at_ms == 0
        {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid conversation status request",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM conversations
                 WHERE conversation_id = ?1 AND owner_id = ?2",
                params![conversation_id, owner_id],
                |row| row.get(0),
            )
            .optional()?;
        let Some(current) = current else {
            return Err(ConversationRepositoryError::NotFound);
        };
        if parse_u64(current)? != expected_revision {
            return Err(ConversationRepositoryError::Conflict(
                "conversation revision conflict",
            ));
        }
        let next_revision =
            expected_revision
                .checked_add(1)
                .ok_or(ConversationRepositoryError::Conflict(
                    "conversation revision overflow",
                ))?;
        transaction.execute(
            "UPDATE conversations SET status = ?1, revision = ?2, updated_at_ms = ?3
             WHERE conversation_id = ?4 AND owner_id = ?5 AND revision = ?6",
            params![
                status,
                i64::try_from(next_revision)
                    .map_err(|_| ConversationRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(updated_at_ms).map_err(|_| {
                    ConversationRepositoryError::InvalidInput("timestamp out of range")
                })?,
                conversation_id,
                owner_id,
                current,
            ],
        )?;
        transaction.commit()?;
        self.get_conversation(conversation_id, owner_id)?
            .map(|snapshot| snapshot.conversation)
            .ok_or(ConversationRepositoryError::NotFound)
    }

    fn get_turn(
        &self,
        conversation_id: &str,
        turn_id: &str,
    ) -> Result<Option<ConversationTurnRecord>, ConversationRepositoryError> {
        self.connection
            .query_row(
                "SELECT t.conversation_id, t.turn_id, t.ordinal, t.role, t.status,
                        COALESCE((SELECT group_concat(ordered.content, '')
                                  FROM (SELECT p.content
                                        FROM conversation_parts p
                                        WHERE p.conversation_id = t.conversation_id
                                          AND p.turn_id = t.turn_id
                                        ORDER BY p.part_index) ordered), ''),
                        t.connection_id, t.model_id, t.revision, t.created_at_ms, t.finished_at_ms,
                        t.execution_id
                 FROM conversation_turns t
                 WHERE t.conversation_id = ?1 AND t.turn_id = ?2",
                params![conversation_id, turn_id],
                row_to_turn,
            )
            .optional()
            .map_err(Into::into)
    }

    fn get_execution(
        &self,
        execution_id: &str,
    ) -> Result<Option<ConversationExecutionRecord>, ConversationRepositoryError> {
        self.connection
            .query_row(
                "SELECT execution_id, conversation_id, turn_id, provider_kind, connection_id,
                        model_id, status, checkpoint_seq, revision, started_at_ms, finished_at_ms
                 FROM conversation_executions WHERE execution_id = ?1",
                params![execution_id],
                row_to_execution,
            )
            .optional()
            .map_err(Into::into)
    }

    fn get_tool_call(
        &self,
        call_id: &str,
    ) -> Result<Option<ConversationToolCallRecord>, ConversationRepositoryError> {
        self.connection
            .query_row(
                "SELECT call_id, conversation_id, request_turn_id, result_turn_id, tool_name,
                        arguments_json, result_content, status, revision, created_at_ms, completed_at_ms
                 FROM conversation_tool_calls WHERE call_id = ?1",
                params![call_id],
                row_to_tool_call,
            )
            .optional()
            .map_err(Into::into)
    }
}

fn validate_conversation(
    conversation_id: &str,
    owner_id: &str,
    title: &str,
    connection_id: &str,
    model_id: &str,
    created_at_ms: u64,
) -> Result<(), ConversationRepositoryError> {
    if conversation_id.trim().is_empty()
        || conversation_id.len() > MAX_ID_BYTES
        || owner_id.trim().is_empty()
        || owner_id.len() > MAX_ID_BYTES
        || title.trim().is_empty()
        || title.len() > MAX_TITLE_BYTES
        || created_at_ms == 0
    {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid conversation metadata",
        ));
    }
    validate_connection_model(connection_id, model_id, created_at_ms)
}

fn recover_incomplete_runs(connection: &mut Connection) -> Result<(), ConversationRepositoryError> {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let now_ms = u64::try_from(now_ms)
        .map_err(|_| ConversationRepositoryError::InvalidInput("system timestamp out of range"))?;
    let transaction = connection.transaction()?;
    let mut statement = transaction.prepare(
        "SELECT conversation_id FROM conversation_turns
         WHERE status IN ('QUEUED', 'RUNNING')
         UNION
         SELECT conversation_id FROM conversation_executions WHERE status = 'RUNNING'
         UNION
         SELECT conversation_id FROM conversation_tool_calls WHERE status = 'REQUESTED'",
    )?;
    let conversation_ids = statement
        .query_map([], |row| row.get::<_, String>(0))?
        .collect::<Result<Vec<_>, _>>()?;
    drop(statement);
    for conversation_id in conversation_ids {
        let current_revision: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM conversations
             WHERE conversation_id = ?1 AND status = 'ACTIVE'",
                params![conversation_id],
                |row| row.get(0),
            )
            .optional()?;
        let Some(current_revision) = current_revision else {
            continue;
        };
        let revision = next_revision(parse_u64(current_revision)?)?;
        transaction.execute(
            "UPDATE conversations SET revision = ?1, updated_at_ms = ?2
             WHERE conversation_id = ?3 AND status = 'ACTIVE'",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(now_ms)?,
                conversation_id,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_turns SET status = 'INTERRUPTED', revision = ?1,
                    finished_at_ms = ?2
             WHERE conversation_id = ?3 AND status IN ('QUEUED', 'RUNNING')",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(now_ms)?,
                conversation_id,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_executions SET status = 'INTERRUPTED', revision = ?1,
                    finished_at_ms = ?2
             WHERE conversation_id = ?3 AND status = 'RUNNING'",
            params![
                to_i64_revision(revision)?,
                to_i64_timestamp(now_ms)?,
                conversation_id,
            ],
        )?;
        transaction.execute(
            "UPDATE conversation_tool_calls SET status = 'AMBIGUOUS', revision = ?1
             WHERE conversation_id = ?2 AND status = 'REQUESTED'",
            params![to_i64_revision(revision)?, conversation_id],
        )?;
    }
    transaction.commit()?;
    Ok(())
}

fn validate_identity(
    conversation_id: &str,
    owner_id: &str,
    turn_id: &str,
) -> Result<(), ConversationRepositoryError> {
    if conversation_id.trim().is_empty()
        || conversation_id.len() > MAX_ID_BYTES
        || owner_id.trim().is_empty()
        || owner_id.len() > MAX_ID_BYTES
        || turn_id.trim().is_empty()
        || turn_id.len() > MAX_ID_BYTES
    {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid conversation identity",
        ));
    }
    Ok(())
}

fn validate_connection_model(
    connection_id: &str,
    model_id: &str,
    timestamp: u64,
) -> Result<(), ConversationRepositoryError> {
    if connection_id.trim().is_empty()
        || connection_id.len() > MAX_CONNECTION_BYTES
        || model_id.trim().is_empty()
        || model_id.len() > MAX_MODEL_BYTES
        || timestamp == 0
    {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid connection or model selection",
        ));
    }
    Ok(())
}

fn validate_turn(
    conversation_id: &str,
    owner_id: &str,
    turn_id: &str,
    role: &str,
    content: &str,
    connection_id: Option<&str>,
    model_id: Option<&str>,
    status: &str,
    created_at_ms: u64,
) -> Result<(), ConversationRepositoryError> {
    if conversation_id.trim().is_empty()
        || conversation_id.len() > MAX_ID_BYTES
        || owner_id.trim().is_empty()
        || owner_id.len() > MAX_ID_BYTES
        || turn_id.trim().is_empty()
        || turn_id.len() > MAX_ID_BYTES
        || role.trim().is_empty()
        || role.len() > MAX_ROLE_BYTES
        || !matches!(role, "system" | "user" | "assistant" | "tool")
        || (content.is_empty() && !matches!(status, "QUEUED" | "RUNNING" | "WAITING_APPROVAL"))
        || content.len() > MAX_CONTENT_BYTES
        || status.len() > MAX_STATUS_BYTES
        || created_at_ms == 0
    {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid conversation turn",
        ));
    }
    validate_turn_status(status)?;
    if let Some(connection_id) = connection_id {
        if connection_id.trim().is_empty() || connection_id.len() > MAX_CONNECTION_BYTES {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid turn connection_id",
            ));
        }
    }
    if let Some(model_id) = model_id {
        if model_id.trim().is_empty() || model_id.len() > MAX_MODEL_BYTES {
            return Err(ConversationRepositoryError::InvalidInput(
                "invalid turn model_id",
            ));
        }
    }
    Ok(())
}

fn validate_status(status: &str) -> Result<(), ConversationRepositoryError> {
    if status.trim().is_empty()
        || status.len() > MAX_STATUS_BYTES
        || !matches!(status, "ACTIVE" | "INTERRUPTED" | "COMPLETED" | "FAILED")
    {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid conversation status",
        ));
    }
    Ok(())
}

fn validate_turn_status(status: &str) -> Result<(), ConversationRepositoryError> {
    if status.trim().is_empty()
        || status.len() > MAX_STATUS_BYTES
        || !matches!(
            status,
            "QUEUED"
                | "RUNNING"
                | "WAITING_APPROVAL"
                | "COMPLETED"
                | "FAILED"
                | "CANCELLED"
                | "INTERRUPTED"
        )
    {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid conversation turn status",
        ));
    }
    Ok(())
}

fn validate_part_kind(kind: &str) -> Result<(), ConversationRepositoryError> {
    if !matches!(
        kind,
        "TEXT"
            | "IMAGE_REF"
            | "AUDIO_REF"
            | "FILE_REF"
            | "TOOL_CALL"
            | "TOOL_RESULT"
            | "PROVIDER_EXTENSION"
    ) {
        return Err(ConversationRepositoryError::InvalidInput(
            "invalid conversation part kind",
        ));
    }
    Ok(())
}

fn turn_transition_allowed(current: &str, next: &str) -> bool {
    matches!(
        (current, next),
        ("QUEUED", "RUNNING")
            | ("QUEUED", "WAITING_APPROVAL")
            | ("QUEUED", "CANCELLED")
            | ("QUEUED", "INTERRUPTED")
            | ("RUNNING", "WAITING_APPROVAL")
            | ("RUNNING", "COMPLETED")
            | ("RUNNING", "FAILED")
            | ("RUNNING", "CANCELLED")
            | ("RUNNING", "INTERRUPTED")
            | ("WAITING_APPROVAL", "RUNNING")
            | ("WAITING_APPROVAL", "CANCELLED")
            | ("WAITING_APPROVAL", "INTERRUPTED")
    )
}

fn parse_u64(value: i64) -> Result<u64, ConversationRepositoryError> {
    u64::try_from(value)
        .map_err(|_| ConversationRepositoryError::InvalidInput("stored integer out of range"))
}

fn next_revision(current: u64) -> Result<u64, ConversationRepositoryError> {
    current
        .checked_add(1)
        .ok_or(ConversationRepositoryError::Conflict(
            "conversation revision overflow",
        ))
}

fn to_i64_revision(value: u64) -> Result<i64, ConversationRepositoryError> {
    i64::try_from(value).map_err(|_| ConversationRepositoryError::Conflict("revision out of range"))
}

fn to_i64_timestamp(value: u64) -> Result<i64, ConversationRepositoryError> {
    i64::try_from(value)
        .map_err(|_| ConversationRepositoryError::InvalidInput("timestamp out of range"))
}

fn parse_sql_u64(value: i64, column: usize) -> rusqlite::Result<u64> {
    u64::try_from(value).map_err(|_| {
        rusqlite::Error::FromSqlConversionFailure(
            column,
            rusqlite::types::Type::Integer,
            Box::new(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "stored integer out of range",
            )),
        )
    })
}

fn row_to_conversation(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConversationRecord> {
    Ok(ConversationRecord {
        conversation_id: row.get(0)?,
        owner_id: row.get(1)?,
        title: row.get(2)?,
        connection_id: row.get(3)?,
        model_id: row.get(4)?,
        status: row.get(5)?,
        revision: parse_sql_u64(row.get(6)?, 6)?,
        created_at_ms: parse_sql_u64(row.get(7)?, 7)?,
        updated_at_ms: parse_sql_u64(row.get(8)?, 8)?,
    })
}

fn row_to_turn(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConversationTurnRecord> {
    Ok(ConversationTurnRecord {
        conversation_id: row.get(0)?,
        turn_id: row.get(1)?,
        ordinal: parse_sql_u64(row.get(2)?, 2)?,
        role: row.get(3)?,
        status: row.get(4)?,
        content: row.get(5)?,
        connection_id: row.get(6)?,
        model_id: row.get(7)?,
        revision: parse_sql_u64(row.get(8)?, 8)?,
        created_at_ms: parse_sql_u64(row.get(9)?, 9)?,
        finished_at_ms: row
            .get::<_, Option<i64>>(10)?
            .map(|value| parse_sql_u64(value, 10))
            .transpose()?,
        execution_id: row.get(11)?,
    })
}

fn row_to_execution(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConversationExecutionRecord> {
    Ok(ConversationExecutionRecord {
        execution_id: row.get(0)?,
        conversation_id: row.get(1)?,
        turn_id: row.get(2)?,
        provider_kind: row.get(3)?,
        connection_id: row.get(4)?,
        model_id: row.get(5)?,
        status: row.get(6)?,
        checkpoint_seq: parse_sql_u64(row.get(7)?, 7)?,
        revision: parse_sql_u64(row.get(8)?, 8)?,
        started_at_ms: parse_sql_u64(row.get(9)?, 9)?,
        finished_at_ms: row
            .get::<_, Option<i64>>(10)?
            .map(|value| parse_sql_u64(value, 10))
            .transpose()?,
    })
}

fn row_to_checkpoint(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConversationCheckpointRecord> {
    Ok(ConversationCheckpointRecord {
        execution_id: row.get(0)?,
        sequence: parse_sql_u64(row.get(1)?, 1)?,
        state: row.get(2)?,
        continuation_json: row.get(3)?,
        continuation_hash: row.get(4)?,
        created_at_ms: parse_sql_u64(row.get(5)?, 5)?,
    })
}

fn row_to_tool_call(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConversationToolCallRecord> {
    Ok(ConversationToolCallRecord {
        call_id: row.get(0)?,
        conversation_id: row.get(1)?,
        request_turn_id: row.get(2)?,
        result_turn_id: row.get(3)?,
        tool_name: row.get(4)?,
        arguments_json: row.get(5)?,
        result_content: row.get(6)?,
        status: row.get(7)?,
        revision: parse_sql_u64(row.get(8)?, 8)?,
        created_at_ms: parse_sql_u64(row.get(9)?, 9)?,
        completed_at_ms: row
            .get::<_, Option<i64>>(10)?
            .map(|value| parse_sql_u64(value, 10))
            .transpose()?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn conversation_turns_and_model_switch_are_revisioned() {
        let dir = tempdir().unwrap();
        let mut repository = ConversationRepository::open(dir.path().join("state.db")).unwrap();
        let conversation = repository
            .create_conversation(
                "conv-1",
                "local-user",
                "Test",
                "local",
                "model-a",
                1_700_000_000_000,
            )
            .unwrap();
        assert_eq!(conversation.revision, 1);
        let turn = repository
            .append_turn(
                "conv-1",
                "local-user",
                "turn-1",
                "user",
                "hello",
                Some("local"),
                Some("model-a"),
                "COMPLETED",
                1,
                1_700_000_000_001,
            )
            .unwrap();
        assert_eq!(turn.ordinal, 1);
        assert_eq!(turn.content, "hello");
        let pending = repository
            .append_turn(
                "conv-1",
                "local-user",
                "turn-2",
                "assistant",
                "in progress",
                Some("local"),
                Some("model-a"),
                "QUEUED",
                2,
                1_700_000_000_002,
            )
            .unwrap();
        assert!(pending.finished_at_ms.is_none());
        assert!(
            repository
                .append_turn(
                    "conv-1",
                    "local-user",
                    "turn-3",
                    "assistant",
                    "stale",
                    None,
                    None,
                    "COMPLETED",
                    2,
                    1_700_000_000_003,
                )
                .is_err()
        );
        assert!(
            repository
                .switch_model(
                    "conv-1",
                    "local-user",
                    "remote",
                    "model-b",
                    3,
                    1_700_000_000_004,
                )
                .is_err()
        );
        repository
            .transition_turn(
                "conv-1",
                "local-user",
                "turn-2",
                "INTERRUPTED",
                3,
                1_700_000_000_004,
            )
            .unwrap();
        let switched = repository
            .switch_model(
                "conv-1",
                "local-user",
                "remote",
                "model-b",
                4,
                1_700_000_000_005,
            )
            .unwrap();
        assert_eq!(switched.revision, 5);
        assert_eq!(switched.model_id, "model-b");
        let multimodal_turn = repository
            .append_turn(
                "conv-1",
                "local-user",
                "turn-4",
                "assistant",
                "",
                Some("remote"),
                Some("model-b"),
                "QUEUED",
                5,
                1_700_000_000_006,
            )
            .unwrap();
        let with_image = repository
            .append_turn_part(
                "conv-1",
                "local-user",
                &multimodal_turn.turn_id,
                "IMAGE_REF",
                "sha256:image",
                6,
                1_700_000_000_007,
            )
            .unwrap();
        let interrupted = repository
            .transition_turn(
                "conv-1",
                "local-user",
                &with_image.turn_id,
                "INTERRUPTED",
                7,
                1_700_000_000_008,
            )
            .unwrap();
        assert!(
            repository
                .switch_model(
                    "conv-1",
                    "local-user",
                    "local",
                    "model-a",
                    interrupted.revision,
                    1_700_000_000_009,
                )
                .is_err()
        );
        let snapshot = repository
            .get_conversation("conv-1", "local-user")
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.turns.len(), 3);
    }

    #[test]
    fn owner_scope_and_terminal_status_block_mutation() {
        let dir = tempdir().unwrap();
        let mut repository = ConversationRepository::open(dir.path().join("state.db")).unwrap();
        repository
            .create_conversation("conv-1", "owner-a", "Test", "local", "model-a", 1)
            .unwrap();
        assert!(
            repository
                .get_conversation("conv-1", "owner-b")
                .unwrap()
                .is_none()
        );
        let completed = repository
            .set_status("conv-1", "owner-a", "COMPLETED", 1, 2)
            .unwrap();
        assert_eq!(completed.status, "COMPLETED");
        assert!(
            repository
                .append_turn(
                    "conv-1",
                    "owner-a",
                    "turn-1",
                    "user",
                    "blocked",
                    None,
                    None,
                    "COMPLETED",
                    2,
                    3,
                )
                .is_err()
        );
    }

    #[test]
    fn execution_checkpoints_and_tool_pairs_are_atomic_and_revisioned() {
        let dir = tempdir().unwrap();
        let mut repository = ConversationRepository::open(dir.path().join("state.db")).unwrap();
        repository
            .create_conversation("conv-1", "owner-a", "Test", "local", "model-a", 1)
            .unwrap();
        repository
            .append_turn(
                "conv-1",
                "owner-a",
                "assistant-1",
                "assistant",
                "partial",
                Some("local"),
                Some("model-a"),
                "QUEUED",
                1,
                2,
            )
            .unwrap();
        let execution = repository
            .start_execution(
                "conv-1",
                "owner-a",
                "exec-1",
                "assistant-1",
                "local-compatible",
                "local",
                "model-a",
                2,
                3,
            )
            .unwrap();
        assert_eq!(execution.status, "RUNNING");
        assert_eq!(execution.revision, 3);
        let turn = repository
            .get_conversation("conv-1", "owner-a")
            .unwrap()
            .unwrap()
            .turns[0]
            .clone();
        assert_eq!(turn.status, "RUNNING");
        assert_eq!(turn.execution_id.as_deref(), Some("exec-1"));
        let execution = repository
            .checkpoint_execution(
                "conv-1",
                "owner-a",
                "exec-1",
                1,
                "provider_stream",
                r#"{"cursor":"abc"}"#,
                3,
                4,
            )
            .unwrap();
        assert_eq!(execution.checkpoint_seq, 1);
        assert!(
            repository
                .checkpoint_execution(
                    "conv-1",
                    "owner-a",
                    "exec-1",
                    3,
                    "provider_stream",
                    "{}",
                    4,
                    5,
                )
                .is_err()
        );
        let execution = repository
            .finish_execution("conv-1", "owner-a", "exec-1", "COMPLETED", 4, 5)
            .unwrap();
        assert_eq!(execution.status, "COMPLETED");
        assert_eq!(execution.finished_at_ms, Some(5));

        let assistant = repository
            .append_turn(
                "conv-1",
                "owner-a",
                "assistant-2",
                "assistant",
                "call",
                Some("local"),
                Some("model-a"),
                "COMPLETED",
                5,
                6,
            )
            .unwrap();
        let call = repository
            .record_tool_call(
                "conv-1",
                "owner-a",
                "call-1",
                &assistant.turn_id,
                "search",
                r#"{"q":"rust"}"#,
                6,
                7,
            )
            .unwrap();
        assert_eq!(call.status, "REQUESTED");
        let tool = repository
            .append_turn(
                "conv-1",
                "owner-a",
                "tool-1",
                "tool",
                "rust result",
                None,
                None,
                "COMPLETED",
                7,
                8,
            )
            .unwrap();
        let settled = repository
            .record_tool_result(
                "conv-1",
                "owner-a",
                "call-1",
                &tool.turn_id,
                "rust result",
                "COMPLETED",
                8,
                9,
            )
            .unwrap();
        assert_eq!(settled.result_turn_id.as_deref(), Some("tool-1"));
        assert!(
            repository
                .record_tool_result(
                    "conv-1",
                    "owner-a",
                    "call-1",
                    "tool-1",
                    "duplicate",
                    "COMPLETED",
                    9,
                    10,
                )
                .is_err()
        );
        let snapshot = repository
            .get_conversation("conv-1", "owner-a")
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.executions.len(), 1);
        assert_eq!(snapshot.checkpoints.len(), 1);
        assert_eq!(snapshot.checkpoints[0].continuation_hash.len(), 64);
        assert_eq!(snapshot.tool_calls.len(), 1);
        assert_eq!(snapshot.tool_calls[0].status, "COMPLETED");
    }

    #[test]
    fn reopening_repository_marks_unfinished_execution_interrupted() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        {
            let mut repository = ConversationRepository::open(&path).unwrap();
            repository
                .create_conversation("conv-1", "owner-a", "Test", "local", "model-a", 1)
                .unwrap();
            repository
                .append_turn(
                    "conv-1",
                    "owner-a",
                    "turn-1",
                    "assistant",
                    "partial",
                    Some("local"),
                    Some("model-a"),
                    "QUEUED",
                    1,
                    2,
                )
                .unwrap();
            repository
                .start_execution(
                    "conv-1", "owner-a", "exec-1", "turn-1", "local", "local", "model-a", 2, 3,
                )
                .unwrap();
        }
        let repository = ConversationRepository::open(&path).unwrap();
        let snapshot = repository
            .get_conversation("conv-1", "owner-a")
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.turns[0].status, "INTERRUPTED");
        assert_eq!(snapshot.executions[0].status, "INTERRUPTED");
        assert!(snapshot.executions[0].finished_at_ms.is_some());
    }

    #[test]
    fn reopening_repository_marks_tool_side_effect_ambiguous_until_reconciled() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("state.db");
        {
            let mut repository = ConversationRepository::open(&path).unwrap();
            repository
                .create_conversation("conv-1", "owner-a", "Test", "local", "model-a", 1)
                .unwrap();
            let turn = repository
                .append_turn(
                    "conv-1",
                    "owner-a",
                    "turn-1",
                    "assistant",
                    "call",
                    Some("local"),
                    Some("model-a"),
                    "COMPLETED",
                    1,
                    2,
                )
                .unwrap();
            repository
                .record_tool_call(
                    "conv-1",
                    "owner-a",
                    "call-1",
                    &turn.turn_id,
                    "search",
                    "{}",
                    2,
                    3,
                )
                .unwrap();
        }
        let mut repository = ConversationRepository::open(&path).unwrap();
        let snapshot = repository
            .get_conversation("conv-1", "owner-a")
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.tool_calls[0].status, "AMBIGUOUS");
        let call = repository
            .reconcile_tool_call(
                "conv-1",
                "owner-a",
                "call-1",
                "CANCELLED",
                "operator confirmed the tool side effect was not applied",
                4,
                5,
            )
            .unwrap();
        assert_eq!(call.status, "CANCELLED");
        assert_eq!(call.result_turn_id, None);
    }
}
