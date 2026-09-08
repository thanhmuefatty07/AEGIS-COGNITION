//! Local-first connection and model catalog metadata.
//!
//! The repository stores endpoint metadata and opaque secret references only.
//! Credential values belong to a platform secret-store adapter and never cross
//! this persistence boundary.

use rusqlite::{Connection, OptionalExtension, params};
use std::net::IpAddr;
use std::path::Path;

const MAX_CONNECTION_ID_BYTES: usize = 128;
const MAX_PROVIDER_KIND_BYTES: usize = 64;
const MAX_PROTOCOL_BYTES: usize = 64;
const MAX_ENDPOINT_BYTES: usize = 2048;
const MAX_SECRET_REF_BYTES: usize = 512;
const MAX_MODEL_ID_BYTES: usize = 256;
const MAX_CAPABILITY_BYTES: usize = 128;
const MAX_CAPABILITIES: usize = 64;
const MAX_GRANT_ID_BYTES: usize = 128;
const MAX_DATA_CLASS_BYTES: usize = 128;
const MAX_OPERATION_BYTES: usize = 128;

#[derive(Debug)]
pub enum ConnectionRepositoryError {
    Sqlite(rusqlite::Error),
    InvalidInput(&'static str),
    Conflict(&'static str),
    NotFound,
    PermissionDenied,
}

impl From<rusqlite::Error> for ConnectionRepositoryError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionRecord {
    pub connection_id: String,
    pub provider_kind: String,
    pub endpoint: String,
    pub protocol: String,
    pub secret_ref: Option<String>,
    pub enabled: bool,
    pub revision: u64,
    pub updated_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ModelDescriptorRecord {
    pub connection_id: String,
    pub model_id: String,
    pub family: Option<String>,
    pub capabilities: Vec<String>,
    pub context_limit: Option<u64>,
    pub output_limit: Option<u64>,
    pub source: String,
    pub revision: u64,
    pub observed_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EgressGrantRecord {
    pub grant_id: String,
    pub connection_id: String,
    pub connection_revision: u64,
    pub data_class: String,
    pub operation: String,
    pub expires_at_ms: Option<u64>,
    pub revision: u64,
    pub updated_at_ms: u64,
    pub revoked_at_ms: Option<u64>,
}

type EgressCheckRow = (i64, i64, Option<i64>, Option<i64>, i64);

pub struct ConnectionRepository {
    connection: Connection,
}

impl ConnectionRepository {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, ConnectionRepositoryError> {
        let connection = Connection::open(path)?;
        connection.execute_batch(
            "PRAGMA foreign_keys = ON;
             PRAGMA busy_timeout = 5000;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = FULL;
             CREATE TABLE IF NOT EXISTS provider_connections (
                 connection_id TEXT PRIMARY KEY NOT NULL,
                 provider_kind TEXT NOT NULL,
                 endpoint TEXT NOT NULL,
                 protocol TEXT NOT NULL,
                 secret_ref TEXT,
                 enabled INTEGER NOT NULL,
                 revision INTEGER NOT NULL,
                 updated_at_ms INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS model_descriptors (
                 connection_id TEXT NOT NULL,
                 model_id TEXT NOT NULL,
                 family TEXT,
                 capabilities_json TEXT NOT NULL,
                 context_limit INTEGER,
                 output_limit INTEGER,
                 source TEXT NOT NULL,
                 revision INTEGER NOT NULL,
                 observed_at_ms INTEGER NOT NULL,
                 PRIMARY KEY (connection_id, model_id),
                 FOREIGN KEY (connection_id) REFERENCES provider_connections(connection_id)
             );
             CREATE INDEX IF NOT EXISTS model_descriptors_connection_idx
                 ON model_descriptors(connection_id, model_id);
             CREATE TABLE IF NOT EXISTS connection_egress_grants (
                 grant_id TEXT PRIMARY KEY NOT NULL,
                 connection_id TEXT NOT NULL,
                 connection_revision INTEGER NOT NULL,
                 data_class TEXT NOT NULL,
                 operation TEXT NOT NULL,
                 expires_at_ms INTEGER,
                 revision INTEGER NOT NULL,
                 updated_at_ms INTEGER NOT NULL,
                 revoked_at_ms INTEGER,
                 UNIQUE(connection_id, data_class, operation),
                 FOREIGN KEY (connection_id) REFERENCES provider_connections(connection_id)
             );
             CREATE INDEX IF NOT EXISTS connection_egress_grants_lookup_idx
                 ON connection_egress_grants(connection_id, data_class, operation);",
        )?;
        Ok(Self { connection })
    }

    pub fn upsert_connection(
        &mut self,
        connection_id: &str,
        provider_kind: &str,
        endpoint: &str,
        protocol: &str,
        secret_ref: Option<&str>,
        enabled: bool,
        expected_revision: Option<u64>,
        updated_at_ms: u64,
    ) -> Result<ConnectionRecord, ConnectionRepositoryError> {
        validate_connection(
            connection_id,
            provider_kind,
            endpoint,
            protocol,
            secret_ref,
            updated_at_ms,
        )?;
        let transaction = self.connection.transaction()?;
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM provider_connections WHERE connection_id = ?1",
                params![connection_id],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(expected) = expected_revision {
            if current.and_then(|value| u64::try_from(value).ok()) != Some(expected) {
                return Err(ConnectionRepositoryError::Conflict(
                    "connection revision conflict",
                ));
            }
        } else if current.is_some() {
            return Err(ConnectionRepositoryError::Conflict(
                "existing connection requires expected_revision",
            ));
        }
        let revision = current
            .map(parse_u64)
            .transpose()?
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(ConnectionRepositoryError::Conflict(
                "connection revision overflow",
            ))?;
        transaction.execute(
            "INSERT INTO provider_connections
                (connection_id, provider_kind, endpoint, protocol, secret_ref, enabled, revision, updated_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
             ON CONFLICT(connection_id) DO UPDATE SET
                 provider_kind = excluded.provider_kind,
                 endpoint = excluded.endpoint,
                 protocol = excluded.protocol,
                 secret_ref = excluded.secret_ref,
                 enabled = excluded.enabled,
                 revision = excluded.revision,
                 updated_at_ms = excluded.updated_at_ms",
            params![
                connection_id,
                provider_kind,
                endpoint,
                protocol,
                secret_ref,
                enabled,
                i64::try_from(revision).map_err(|_| ConnectionRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(updated_at_ms).map_err(|_| ConnectionRepositoryError::InvalidInput("timestamp out of range"))?,
            ],
        )?;
        transaction.commit()?;
        Ok(ConnectionRecord {
            connection_id: connection_id.to_string(),
            provider_kind: provider_kind.to_string(),
            endpoint: endpoint.to_string(),
            protocol: protocol.to_string(),
            secret_ref: secret_ref.map(str::to_string),
            enabled,
            revision,
            updated_at_ms,
        })
    }

    pub fn revoke_connection(
        &mut self,
        connection_id: &str,
        expected_revision: u64,
        updated_at_ms: u64,
    ) -> Result<ConnectionRecord, ConnectionRepositoryError> {
        let current = self
            .get_connection(connection_id)?
            .ok_or(ConnectionRepositoryError::NotFound)?;
        if current.revision != expected_revision {
            return Err(ConnectionRepositoryError::Conflict(
                "connection revision conflict",
            ));
        }
        self.upsert_connection(
            &current.connection_id,
            &current.provider_kind,
            &current.endpoint,
            &current.protocol,
            current.secret_ref.as_deref(),
            false,
            Some(expected_revision),
            updated_at_ms,
        )
    }

    pub fn get_connection(
        &self,
        connection_id: &str,
    ) -> Result<Option<ConnectionRecord>, ConnectionRepositoryError> {
        let record = self
            .connection
            .query_row(
                "SELECT connection_id, provider_kind, endpoint, protocol, secret_ref, enabled, revision, updated_at_ms
                 FROM provider_connections WHERE connection_id = ?1",
                params![connection_id],
                row_to_connection,
            )
            .optional()?;
        Ok(record)
    }

    pub fn list_connections(&self) -> Result<Vec<ConnectionRecord>, ConnectionRepositoryError> {
        let mut statement = self.connection.prepare(
            "SELECT connection_id, provider_kind, endpoint, protocol, secret_ref, enabled, revision, updated_at_ms
             FROM provider_connections ORDER BY connection_id",
        )?;
        let rows = statement.query_map([], row_to_connection)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn upsert_model(
        &mut self,
        connection_id: &str,
        model_id: &str,
        family: Option<&str>,
        capabilities: &[String],
        context_limit: Option<u64>,
        output_limit: Option<u64>,
        source: &str,
        expected_revision: Option<u64>,
        observed_at_ms: u64,
    ) -> Result<ModelDescriptorRecord, ConnectionRepositoryError> {
        validate_model(
            connection_id,
            model_id,
            family,
            capabilities,
            context_limit,
            output_limit,
            source,
            observed_at_ms,
        )?;
        if self.get_connection(connection_id)?.is_none() {
            return Err(ConnectionRepositoryError::NotFound);
        }
        let capabilities = canonical_capabilities(capabilities)?;
        let capabilities_json = serde_json::to_string(&capabilities).map_err(|_| {
            ConnectionRepositoryError::InvalidInput("capabilities serialization failed")
        })?;
        let transaction = self.connection.transaction()?;
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM model_descriptors WHERE connection_id = ?1 AND model_id = ?2",
                params![connection_id, model_id],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(expected) = expected_revision {
            if current.and_then(|value| u64::try_from(value).ok()) != Some(expected) {
                return Err(ConnectionRepositoryError::Conflict(
                    "model revision conflict",
                ));
            }
        } else if current.is_some() {
            return Err(ConnectionRepositoryError::Conflict(
                "existing model requires expected_revision",
            ));
        }
        let revision = current
            .map(parse_u64)
            .transpose()?
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(ConnectionRepositoryError::Conflict(
                "model revision overflow",
            ))?;
        transaction.execute(
            "INSERT INTO model_descriptors
                (connection_id, model_id, family, capabilities_json, context_limit, output_limit, source, revision, observed_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
             ON CONFLICT(connection_id, model_id) DO UPDATE SET
                 family = excluded.family,
                 capabilities_json = excluded.capabilities_json,
                 context_limit = excluded.context_limit,
                 output_limit = excluded.output_limit,
                 source = excluded.source,
                 revision = excluded.revision,
                 observed_at_ms = excluded.observed_at_ms",
            params![
                connection_id,
                model_id,
                family,
                capabilities_json,
                context_limit
                    .map(|value| {
                        i64::try_from(value).map_err(|_| {
                            ConnectionRepositoryError::InvalidInput("context_limit out of range")
                        })
                    })
                    .transpose()?,
                output_limit
                    .map(|value| {
                        i64::try_from(value).map_err(|_| {
                            ConnectionRepositoryError::InvalidInput("output_limit out of range")
                        })
                    })
                    .transpose()?,
                source,
                i64::try_from(revision).map_err(|_| ConnectionRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(observed_at_ms).map_err(|_| ConnectionRepositoryError::InvalidInput("timestamp out of range"))?,
            ],
        )?;
        transaction.commit()?;
        Ok(ModelDescriptorRecord {
            connection_id: connection_id.to_string(),
            model_id: model_id.to_string(),
            family: family.map(str::to_string),
            capabilities,
            context_limit,
            output_limit,
            source: source.to_string(),
            revision,
            observed_at_ms,
        })
    }

    pub fn list_models(
        &self,
        connection_id: &str,
    ) -> Result<Vec<ModelDescriptorRecord>, ConnectionRepositoryError> {
        let mut statement = self.connection.prepare(
            "SELECT connection_id, model_id, family, capabilities_json, context_limit, output_limit, source, revision, observed_at_ms
             FROM model_descriptors WHERE connection_id = ?1 ORDER BY model_id",
        )?;
        let rows = statement.query_map(params![connection_id], row_to_model)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn grant_egress(
        &mut self,
        grant_id: &str,
        connection_id: &str,
        data_class: &str,
        operation: &str,
        expires_at_ms: Option<u64>,
        expected_revision: Option<u64>,
        updated_at_ms: u64,
    ) -> Result<EgressGrantRecord, ConnectionRepositoryError> {
        validate_egress(
            grant_id,
            connection_id,
            data_class,
            operation,
            expires_at_ms,
            updated_at_ms,
        )?;
        if expires_at_ms.is_some_and(|expires| expires <= updated_at_ms) {
            return Err(ConnectionRepositoryError::InvalidInput(
                "grant expiry must be in the future",
            ));
        }
        let transaction = self.connection.transaction()?;
        let (connection_revision, enabled): (i64, i64) = transaction
            .query_row(
                "SELECT revision, enabled FROM provider_connections WHERE connection_id = ?1",
                params![connection_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?
            .ok_or(ConnectionRepositoryError::NotFound)?;
        if enabled != 1 {
            return Err(ConnectionRepositoryError::Conflict(
                "cannot grant a disabled connection",
            ));
        }
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM connection_egress_grants
                 WHERE connection_id = ?1 AND data_class = ?2 AND operation = ?3",
                params![connection_id, data_class, operation],
                |row| row.get(0),
            )
            .optional()?;
        if let Some(expected) = expected_revision {
            if current.and_then(|value| u64::try_from(value).ok()) != Some(expected) {
                return Err(ConnectionRepositoryError::Conflict(
                    "egress grant revision conflict",
                ));
            }
        } else if current.is_some() {
            return Err(ConnectionRepositoryError::Conflict(
                "existing egress grant requires expected_revision",
            ));
        }
        let revision = current
            .map(parse_u64)
            .transpose()?
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(ConnectionRepositoryError::Conflict(
                "egress grant revision overflow",
            ))?;
        transaction.execute(
            "INSERT INTO connection_egress_grants
                (grant_id, connection_id, connection_revision, data_class, operation,
                 expires_at_ms, revision, updated_at_ms, revoked_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, NULL)
             ON CONFLICT(connection_id, data_class, operation) DO UPDATE SET
                 grant_id = excluded.grant_id,
                 connection_revision = excluded.connection_revision,
                 expires_at_ms = excluded.expires_at_ms,
                 revision = excluded.revision,
                 updated_at_ms = excluded.updated_at_ms,
                 revoked_at_ms = NULL",
            params![
                grant_id,
                connection_id,
                connection_revision,
                data_class,
                operation,
                expires_at_ms
                    .map(|value| {
                        i64::try_from(value).map_err(|_| {
                            ConnectionRepositoryError::InvalidInput("grant expiry out of range")
                        })
                    })
                    .transpose()?,
                i64::try_from(revision)
                    .map_err(|_| ConnectionRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(updated_at_ms).map_err(|_| {
                    ConnectionRepositoryError::InvalidInput("timestamp out of range")
                })?,
            ],
        )?;
        transaction.commit()?;
        self.get_egress_grant(connection_id, data_class, operation)?
            .ok_or(ConnectionRepositoryError::NotFound)
    }

    pub fn get_egress_grant(
        &self,
        connection_id: &str,
        data_class: &str,
        operation: &str,
    ) -> Result<Option<EgressGrantRecord>, ConnectionRepositoryError> {
        self.connection
            .query_row(
                "SELECT grant_id, connection_id, connection_revision, data_class, operation,
                        expires_at_ms, revision, updated_at_ms, revoked_at_ms
                 FROM connection_egress_grants
                 WHERE connection_id = ?1 AND data_class = ?2 AND operation = ?3",
                params![connection_id, data_class, operation],
                row_to_egress_grant,
            )
            .optional()
            .map_err(Into::into)
    }

    pub fn revoke_egress(
        &mut self,
        grant_id: &str,
        expected_revision: u64,
        revoked_at_ms: u64,
    ) -> Result<EgressGrantRecord, ConnectionRepositoryError> {
        if grant_id.trim().is_empty() || grant_id.len() > MAX_GRANT_ID_BYTES || revoked_at_ms == 0 {
            return Err(ConnectionRepositoryError::InvalidInput(
                "invalid egress revocation",
            ));
        }
        let transaction = self.connection.transaction()?;
        let current: Option<i64> = transaction
            .query_row(
                "SELECT revision FROM connection_egress_grants WHERE grant_id = ?1",
                params![grant_id],
                |row| row.get(0),
            )
            .optional()?;
        let Some(current) = current else {
            return Err(ConnectionRepositoryError::NotFound);
        };
        if u64::try_from(current).ok() != Some(expected_revision) {
            return Err(ConnectionRepositoryError::Conflict(
                "egress grant revision conflict",
            ));
        }
        let next_revision =
            expected_revision
                .checked_add(1)
                .ok_or(ConnectionRepositoryError::Conflict(
                    "egress grant revision overflow",
                ))?;
        transaction.execute(
            "UPDATE connection_egress_grants
             SET revision = ?1, revoked_at_ms = ?2, updated_at_ms = ?2
             WHERE grant_id = ?3 AND revision = ?4",
            params![
                i64::try_from(next_revision)
                    .map_err(|_| ConnectionRepositoryError::Conflict("revision out of range"))?,
                i64::try_from(revoked_at_ms).map_err(|_| {
                    ConnectionRepositoryError::InvalidInput("timestamp out of range")
                })?,
                grant_id,
                i64::try_from(expected_revision)
                    .map_err(|_| ConnectionRepositoryError::Conflict("revision out of range"))?,
            ],
        )?;
        transaction.commit()?;
        self.connection
            .query_row(
                "SELECT grant_id, connection_id, connection_revision, data_class, operation,
                        expires_at_ms, revision, updated_at_ms, revoked_at_ms
                 FROM connection_egress_grants WHERE grant_id = ?1",
                params![grant_id],
                row_to_egress_grant,
            )
            .map_err(Into::into)
    }

    pub fn egress_allowed(
        &self,
        connection_id: &str,
        data_class: &str,
        operation: &str,
        now_ms: u64,
    ) -> Result<bool, ConnectionRepositoryError> {
        if connection_id.trim().is_empty()
            || data_class.trim().is_empty()
            || operation.trim().is_empty()
            || now_ms == 0
        {
            return Err(ConnectionRepositoryError::InvalidInput(
                "invalid egress check",
            ));
        }
        let allowed: Option<EgressCheckRow> = self
            .connection
            .query_row(
                "SELECT g.connection_revision, c.revision, g.expires_at_ms,
                        g.revoked_at_ms, c.enabled
                 FROM connection_egress_grants g
                 JOIN provider_connections c ON c.connection_id = g.connection_id
                 WHERE g.connection_id = ?1 AND g.data_class = ?2 AND g.operation = ?3",
                params![connection_id, data_class, operation],
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
        let Some((
            grant_connection_revision,
            current_connection_revision,
            expires_at_ms,
            revoked_at_ms,
            enabled,
        )) = allowed
        else {
            return Ok(false);
        };
        let not_expired = expires_at_ms.is_none_or(|expires| {
            u64::try_from(expires)
                .ok()
                .is_some_and(|value| value > now_ms)
        });
        Ok(enabled == 1
            && revoked_at_ms.is_none()
            && grant_connection_revision == current_connection_revision
            && not_expired)
    }
}

fn validate_connection(
    connection_id: &str,
    provider_kind: &str,
    endpoint: &str,
    protocol: &str,
    secret_ref: Option<&str>,
    updated_at_ms: u64,
) -> Result<(), ConnectionRepositoryError> {
    if connection_id.trim().is_empty() || connection_id.len() > MAX_CONNECTION_ID_BYTES {
        return Err(ConnectionRepositoryError::InvalidInput(
            "invalid connection_id",
        ));
    }
    if provider_kind.trim().is_empty() || provider_kind.len() > MAX_PROVIDER_KIND_BYTES {
        return Err(ConnectionRepositoryError::InvalidInput(
            "invalid provider_kind",
        ));
    }
    if protocol.trim().is_empty() || protocol.len() > MAX_PROTOCOL_BYTES {
        return Err(ConnectionRepositoryError::InvalidInput("invalid protocol"));
    }
    validate_endpoint(endpoint)?;
    if let Some(secret_ref) = secret_ref {
        let allowed_prefix = [
            "windows-credential:",
            "keychain:",
            "secret-service:",
            "env:",
            "session:",
        ]
        .iter()
        .any(|prefix| secret_ref.starts_with(prefix));
        if secret_ref.trim().is_empty()
            || secret_ref.len() > MAX_SECRET_REF_BYTES
            || !allowed_prefix
        {
            return Err(ConnectionRepositoryError::InvalidInput(
                "secret_ref must be an opaque platform reference",
            ));
        }
    }
    if updated_at_ms == 0 {
        return Err(ConnectionRepositoryError::InvalidInput(
            "timestamp must be non-zero",
        ));
    }
    Ok(())
}

fn validate_endpoint(endpoint: &str) -> Result<(), ConnectionRepositoryError> {
    if endpoint.len() > MAX_ENDPOINT_BYTES
        || endpoint.chars().any(char::is_whitespace)
        || endpoint.contains('?')
        || endpoint.contains('#')
    {
        return Err(ConnectionRepositoryError::InvalidInput("invalid endpoint"));
    }
    let Some((scheme, authority)) = endpoint.split_once("://") else {
        return Err(ConnectionRepositoryError::InvalidInput(
            "endpoint requires a URL scheme",
        ));
    };
    if !matches!(scheme, "http" | "https") || authority.is_empty() || authority.contains('@') {
        return Err(ConnectionRepositoryError::InvalidInput(
            "endpoint scheme or authority is not allowed",
        ));
    }
    let host = authority.split('/').next().unwrap_or_default();
    if host.is_empty() {
        return Err(ConnectionRepositoryError::InvalidInput(
            "endpoint host is not allowed",
        ));
    }
    if scheme == "http" && !is_loopback_host(host) {
        return Err(ConnectionRepositoryError::InvalidInput(
            "plain HTTP is allowed only for loopback endpoints",
        ));
    }
    Ok(())
}

fn is_loopback_host(authority: &str) -> bool {
    let host = authority
        .strip_prefix('[')
        .and_then(|value| value.split_once(']').map(|(host, _)| host))
        .unwrap_or_else(|| authority.split(':').next().unwrap_or(authority));
    host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false)
}

fn validate_model(
    connection_id: &str,
    model_id: &str,
    family: Option<&str>,
    capabilities: &[String],
    context_limit: Option<u64>,
    output_limit: Option<u64>,
    source: &str,
    observed_at_ms: u64,
) -> Result<(), ConnectionRepositoryError> {
    if connection_id.trim().is_empty()
        || model_id.trim().is_empty()
        || model_id.len() > MAX_MODEL_ID_BYTES
        || source.trim().is_empty()
        || source.len() > 32
        || observed_at_ms == 0
    {
        return Err(ConnectionRepositoryError::InvalidInput(
            "invalid model descriptor",
        ));
    }
    if family.is_some_and(|value| value.trim().is_empty() || value.len() > 128)
        || context_limit.is_some_and(|value| value == 0)
        || output_limit.is_some_and(|value| value == 0)
    {
        return Err(ConnectionRepositoryError::InvalidInput(
            "invalid model capability metadata",
        ));
    }
    if !matches!(source, "manual" | "discovered") {
        return Err(ConnectionRepositoryError::InvalidInput(
            "model source must be manual or discovered",
        ));
    }
    if capabilities.len() > MAX_CAPABILITIES {
        return Err(ConnectionRepositoryError::InvalidInput(
            "too many model capabilities",
        ));
    }
    Ok(())
}

fn validate_egress(
    grant_id: &str,
    connection_id: &str,
    data_class: &str,
    operation: &str,
    expires_at_ms: Option<u64>,
    updated_at_ms: u64,
) -> Result<(), ConnectionRepositoryError> {
    if grant_id.trim().is_empty()
        || grant_id.len() > MAX_GRANT_ID_BYTES
        || connection_id.trim().is_empty()
        || data_class.trim().is_empty()
        || data_class.len() > MAX_DATA_CLASS_BYTES
        || operation.trim().is_empty()
        || operation.len() > MAX_OPERATION_BYTES
        || updated_at_ms == 0
        || expires_at_ms.is_some_and(|value| value == 0)
    {
        return Err(ConnectionRepositoryError::InvalidInput(
            "invalid egress grant",
        ));
    }
    Ok(())
}

fn canonical_capabilities(values: &[String]) -> Result<Vec<String>, ConnectionRepositoryError> {
    let mut capabilities = values.to_vec();
    if capabilities
        .iter()
        .any(|value| value.trim().is_empty() || value.len() > MAX_CAPABILITY_BYTES)
    {
        return Err(ConnectionRepositoryError::InvalidInput(
            "invalid model capability",
        ));
    }
    capabilities.sort();
    capabilities.dedup();
    Ok(capabilities)
}

fn parse_u64(value: i64) -> Result<u64, ConnectionRepositoryError> {
    u64::try_from(value)
        .map_err(|_| ConnectionRepositoryError::InvalidInput("stored integer out of range"))
}

fn row_to_connection(row: &rusqlite::Row<'_>) -> rusqlite::Result<ConnectionRecord> {
    Ok(ConnectionRecord {
        connection_id: row.get(0)?,
        provider_kind: row.get(1)?,
        endpoint: row.get(2)?,
        protocol: row.get(3)?,
        secret_ref: row.get(4)?,
        enabled: row.get::<_, i64>(5)? == 1,
        revision: parse_sql_u64(row.get(6)?, 6)?,
        updated_at_ms: parse_sql_u64(row.get(7)?, 7)?,
    })
}

fn row_to_model(row: &rusqlite::Row<'_>) -> rusqlite::Result<ModelDescriptorRecord> {
    let capabilities_json: String = row.get(3)?;
    let capabilities = serde_json::from_str(&capabilities_json).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(3, rusqlite::types::Type::Text, Box::new(error))
    })?;
    Ok(ModelDescriptorRecord {
        connection_id: row.get(0)?,
        model_id: row.get(1)?,
        family: row.get(2)?,
        capabilities,
        context_limit: row
            .get::<_, Option<i64>>(4)?
            .map(|value| parse_sql_u64(value, 4))
            .transpose()?,
        output_limit: row
            .get::<_, Option<i64>>(5)?
            .map(|value| parse_sql_u64(value, 5))
            .transpose()?,
        source: row.get(6)?,
        revision: parse_sql_u64(row.get(7)?, 7)?,
        observed_at_ms: parse_sql_u64(row.get(8)?, 8)?,
    })
}

fn row_to_egress_grant(row: &rusqlite::Row<'_>) -> rusqlite::Result<EgressGrantRecord> {
    Ok(EgressGrantRecord {
        grant_id: row.get(0)?,
        connection_id: row.get(1)?,
        connection_revision: parse_sql_u64(row.get(2)?, 2)?,
        data_class: row.get(3)?,
        operation: row.get(4)?,
        expires_at_ms: row
            .get::<_, Option<i64>>(5)?
            .map(|value| parse_sql_u64(value, 5))
            .transpose()?,
        revision: parse_sql_u64(row.get(6)?, 6)?,
        updated_at_ms: parse_sql_u64(row.get(7)?, 7)?,
        revoked_at_ms: row
            .get::<_, Option<i64>>(8)?
            .map(|value| parse_sql_u64(value, 8))
            .transpose()?,
    })
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

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn connections_and_manual_models_are_revisioned_and_secret_opaque() {
        let dir = tempdir().unwrap();
        let mut repository = ConnectionRepository::open(dir.path().join("state.db")).unwrap();
        let connection = repository
            .upsert_connection(
                "local",
                "openai-compatible",
                "http://127.0.0.1:8080/v1",
                "chat-completions",
                Some("windows-credential:local-token"),
                true,
                None,
                1_700_000_000_000,
            )
            .unwrap();
        assert_eq!(connection.revision, 1);
        assert_eq!(
            connection.secret_ref.as_deref(),
            Some("windows-credential:local-token")
        );
        let model = repository
            .upsert_model(
                "local",
                "local-model",
                Some("text"),
                &["text-generation".to_string(), "text-generation".to_string()],
                Some(8192),
                Some(1024),
                "manual",
                None,
                1_700_000_000_001,
            )
            .unwrap();
        assert_eq!(model.capabilities, vec!["text-generation"]);
        assert!(
            repository
                .upsert_model(
                    "local",
                    "local-model",
                    None,
                    &[],
                    Some(4096),
                    None,
                    "manual",
                    None,
                    1_700_000_000_002,
                )
                .is_err()
        );
        assert_eq!(repository.list_models("local").unwrap().len(), 1);
    }

    #[test]
    fn endpoint_validation_rejects_credentials_and_unsafe_schemes() {
        assert!(validate_endpoint("https://user:pass@example.com").is_err());
        assert!(validate_endpoint("file:///tmp/provider").is_err());
        assert!(validate_endpoint("https://example.com/v1").is_ok());
        assert!(validate_endpoint("http://example.com/v1").is_err());
    }

    #[test]
    fn egress_grants_bind_to_connection_revision_and_revoke() {
        let dir = tempdir().unwrap();
        let mut repository = ConnectionRepository::open(dir.path().join("state.db")).unwrap();
        repository
            .upsert_connection(
                "local",
                "openai-compatible",
                "http://127.0.0.1:8080/v1",
                "chat-completions",
                None,
                true,
                None,
                1_700_000_000_000,
            )
            .unwrap();
        let grant = repository
            .grant_egress(
                "grant-1",
                "local",
                "PROJECT_TEXT",
                "foreground_inference",
                Some(1_800_000_000_000),
                None,
                1_700_000_000_001,
            )
            .unwrap();
        assert_eq!(grant.revision, 1);
        assert!(
            repository
                .egress_allowed(
                    "local",
                    "PROJECT_TEXT",
                    "foreground_inference",
                    1_700_000_000_002
                )
                .unwrap()
        );
        repository
            .upsert_connection(
                "local",
                "openai-compatible",
                "http://127.0.0.1:8081/v1",
                "chat-completions",
                None,
                true,
                Some(1),
                1_700_000_000_003,
            )
            .unwrap();
        assert!(
            !repository
                .egress_allowed(
                    "local",
                    "PROJECT_TEXT",
                    "foreground_inference",
                    1_700_000_000_004
                )
                .unwrap()
        );
        let revoked = repository
            .revoke_egress("grant-1", 1, 1_700_000_000_005)
            .unwrap();
        assert_eq!(revoked.revision, 2);
        assert!(revoked.revoked_at_ms.is_some());
    }
}
