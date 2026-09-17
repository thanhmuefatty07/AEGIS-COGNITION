//! Learning-loop and session-index PyO3 bindings.
//!
//! The parent facade re-exports these functions to preserve the Python ABI.

use super::{
    get_memory_repository, get_session_index, get_session_ledger, hex32, py_safe,
    session_profile_id,
};
use crate::memory::MemoryNudgeProvenance;
use crate::memory::nudge::{MAX_MEMORY_CANDIDATE_BYTES, MemoryCandidate, MemoryNudgeSystem};
use crate::memory::repository::DEFAULT_MEMORY_KIND;
use blake3::Hasher;
use pyo3::prelude::*;
use std::path::PathBuf;

const MAX_LEARNING_LEDGER_JSON_BYTES: usize = 8 * 1024 * 1024;
const MAX_MEMORY_NUDGE_CANDIDATES: usize = 64;

fn parse_session_id(session_id_str: &str) -> PyResult<u128> {
    let session_id = if let Some(hex) = session_id_str.strip_prefix("0x") {
        u128::from_str_radix(hex, 16)
    } else {
        session_id_str.parse::<u128>()
    }
    .map_err(|error| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "Invalid session_id '{}': {}",
            session_id_str, error
        ))
    })?;
    if session_id == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "session_id must be non-zero",
        ));
    }
    Ok(session_id)
}

fn parse_nudge_id(value: &serde_json::Value, name: &str) -> PyResult<u128> {
    let raw = match value {
        serde_json::Value::String(value) => value.clone(),
        serde_json::Value::Number(value) => value.to_string(),
        _ => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "{name} must be a decimal or hexadecimal integer"
            )));
        }
    };
    let parsed = if let Some(hex) = raw.strip_prefix("0x") {
        u128::from_str_radix(hex, 16)
    } else {
        raw.parse::<u128>()
    }
    .map_err(|error| {
        pyo3::exceptions::PyValueError::new_err(format!("{name} is invalid: {error}"))
    })?;
    if parsed == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{name} must be non-zero"
        )));
    }
    Ok(parsed)
}

fn parse_nudge_candidates(
    candidates_json: &str,
    session_id: u128,
) -> PyResult<Vec<MemoryCandidate>> {
    let decoded: serde_json::Value = serde_json::from_str(candidates_json).map_err(|error| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "memory nudge candidates must be valid JSON: {error}"
        ))
    })?;
    let values = decoded.as_array().ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err("memory nudge candidates must be a JSON array")
    })?;
    if values.is_empty() || values.len() > MAX_MEMORY_NUDGE_CANDIDATES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "memory nudge candidates must contain 1..{} items",
            MAX_MEMORY_NUDGE_CANDIDATES
        )));
    }
    values
        .iter()
        .map(|value| {
            let object = value.as_object().ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err(
                    "each memory nudge candidate must be an object",
                )
            })?;
            if object.keys().any(|key| {
                !matches!(
                    key.as_str(),
                    "content" | "relevance_score" | "source_session_id"
                )
            }) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "memory nudge candidate contains an unsupported field",
                ));
            }
            let content = object
                .get("content")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "memory nudge candidate content must be a string",
                    )
                })?;
            if content.trim().is_empty() || content.len() > MAX_MEMORY_CANDIDATE_BYTES {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "memory nudge candidate content is empty or too large",
                ));
            }
            let score = object
                .get("relevance_score")
                .and_then(serde_json::Value::as_f64)
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "memory nudge candidate relevance_score must be a number",
                    )
                })?;
            if !score.is_finite() || !(0.0..=1.0).contains(&score) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "memory nudge candidate relevance_score must be between 0 and 1",
                ));
            }
            let source_session_id = object
                .get("source_session_id")
                .map(|value| parse_nudge_id(value, "source_session_id"))
                .transpose()?
                .unwrap_or(session_id);
            if source_session_id != session_id {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "candidate source_session_id must match session_id",
                ));
            }
            MemoryCandidate::new(content.to_string(), score as f32, source_session_id).ok_or_else(
                || {
                    pyo3::exceptions::PyValueError::new_err(
                        "memory nudge candidate failed native validation",
                    )
                },
            )
        })
        .collect()
}

fn derive_nudge_component_id(
    kind: &[u8],
    nudge_id: u128,
    session_id: u128,
    candidate_hash: [u8; 32],
    ordinal: usize,
    owner_id: &str,
    scope_kind: &str,
) -> u128 {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-memory-nudge-component-v1");
    hasher.update(&[0]);
    hasher.update(kind);
    hasher.update(&[0]);
    hasher.update(&nudge_id.to_le_bytes());
    hasher.update(&session_id.to_le_bytes());
    hasher.update(&candidate_hash);
    hasher.update(&(ordinal as u64).to_le_bytes());
    hasher.update(owner_id.as_bytes());
    hasher.update(&[0]);
    hasher.update(scope_kind.as_bytes());
    let digest = *hasher.finalize().as_bytes();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    let value = u128::from_le_bytes(bytes);
    if value == 0 { 1 } else { value }
}

fn memory_owner(owner_id: Option<String>) -> PyResult<String> {
    match owner_id {
        None => Ok(session_profile_id()),
        Some(value) if value.trim().is_empty() => Err(pyo3::exceptions::PyValueError::new_err(
            "owner_id must be non-empty when provided",
        )),
        Some(value) => Ok(value),
    }
}

fn memory_scope(scope_kind: Option<String>) -> PyResult<String> {
    match scope_kind {
        None => Ok("USER_PRIVATE".to_string()),
        Some(value) if value.trim().is_empty() => Err(pyo3::exceptions::PyValueError::new_err(
            "scope_kind must be non-empty when provided",
        )),
        Some(value) => Ok(value),
    }
}

fn memory_subject(subject_id: Option<String>, owner_id: &str) -> PyResult<String> {
    match subject_id {
        None => Ok(owner_id.to_string()),
        Some(value) if value.trim().is_empty() => Err(pyo3::exceptions::PyValueError::new_err(
            "subject_id must be non-empty when provided",
        )),
        Some(value) => Ok(value),
    }
}

fn memory_view_json(view: &crate::memory::repository::MemoryRecordView) -> serde_json::Value {
    serde_json::json!({
        "memory_id": format!("0x{:x}", view.memory_id),
        "owner_id": view.owner_id,
        "scope_kind": view.scope_kind,
        "memory_kind": view.memory_kind,
        "lifecycle": view.lifecycle,
        "validation": view.validation,
        "validation_basis": view.validation_basis,
        "validation_reason": view.validation_reason,
        "revision": view.revision,
        "observed_at_ms": view.observed_at_ms,
        "content_hash": view.content_hash.as_ref().map(hex32),
        "content": view.content,
        "source_session_id": view.source_session_id.map(|value| format!("0x{:x}", value)),
        "nudge_id": view.nudge_id.map(|value| format!("0x{:x}", value)),
        "nudge_hash": view.nudge_hash.as_ref().map(hex32),
        "candidate_hash": view.candidate_hash.as_ref().map(hex32),
        "relevance_score": view.relevance_score_micros.map(|value| f64::from(value) / 1_000_000.0),
    })
}

#[pyfunction]
#[pyo3(signature = (memory_id, content, timestamp, request_id, scope_kind=None, owner_id=None, subject_id=None, memory_kind=None))]
pub fn aegis_capture_memory(
    memory_id: u128,
    content: String,
    timestamp: u64,
    request_id: u128,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
    memory_kind: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let subject_id = memory_subject(subject_id, &owner_id)?;
        let memory_kind = memory_kind.unwrap_or_else(|| DEFAULT_MEMORY_KIND.to_string());
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let outcome = match repository.lock().capture_with_kind_as(
            memory_id,
            &subject_id,
            &owner_id,
            &scope_kind,
            &memory_kind,
            &content,
            timestamp,
            request_id,
        ) {
            Ok(outcome) => outcome,
            Err(crate::memory::repository::MemoryRepositoryError::CommittedIndexPending {
                memory_id: committed_memory_id,
                ..
            }) => {
                return serde_json::to_string(&serde_json::json!({
                    "schema": "aegis-memory-capture-v1",
                    "status": "committed_index_pending",
                    "memory_id": format!("0x{:x}", committed_memory_id),
                    "owner_id": owner_id,
                    "scope_kind": scope_kind,
                    "memory_kind": memory_kind,
                    "lifecycle": "CANDIDATE",
                    "validation": "UNREVIEWED",
                    "index_pending": true,
                }))
                .map_err(|error| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "Serialization failed: {error}"
                    ))
                });
            }
            Err(error) => {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Memory capture failed: {error:?}"
                )));
            }
        };
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-capture-v1",
            "status": if outcome.index_pending {
                "committed_index_pending"
            } else if outcome.created {
                "committed"
            } else {
                "replayed"
            },
            "memory_id": format!("0x{:x}", memory_id),
            "owner_id": owner_id,
            "scope_kind": scope_kind,
            "memory_kind": memory_kind,
            "lifecycle": "CANDIDATE",
            "validation": "UNREVIEWED",
            "content_hash": hex32(&outcome.content_hash),
            "index_pending": outcome.index_pending,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (memory_id, scope_kind=None, owner_id=None, subject_id=None))]
pub fn aegis_inspect_memory(
    memory_id: u128,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let subject_id = memory_subject(subject_id, &owner_id)?;
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let view = repository
            .lock()
            .inspect_as(memory_id, &subject_id, &owner_id, &scope_kind)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Memory inspect failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-record-v1",
            "found": view.is_some(),
            "record": view.as_ref().map(memory_view_json),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (query, top_k=5usize, scope_kind=None, owner_id=None, subject_id=None))]
pub fn aegis_search_memories(
    query: String,
    top_k: usize,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let subject_id = memory_subject(subject_id, &owner_id)?;
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let records = repository
            .lock()
            .search_candidates_as(&query, &subject_id, &owner_id, &scope_kind, top_k)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!("Memory search failed: {error:?}"))
            })?;
        let results = records.iter().map(memory_view_json).collect::<Vec<_>>();
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-search-result-v1",
            "query": query,
            "top_k": top_k,
            "count": results.len(),
            "results": results,
            "gate": "CandidateOnly",
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_backup_memory_store(destination: String) -> PyResult<String> {
    py_safe(move || {
        if destination.trim().is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "destination must be non-empty",
            ));
        }
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        repository
            .lock()
            .backup_to(PathBuf::from(&destination))
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Memory backup failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-backup-v1",
            "status": "committed",
            "destination": destination,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_grant_memory_access(
    subject_id: String,
    owner_id: String,
    scope_kind: String,
    can_read: bool,
    can_write: bool,
    expires_at_ms: Option<u64>,
    expected_revision: Option<u64>,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let actor_id = session_profile_id();
        let revision = repository
            .lock()
            .grant_access_as(
                &actor_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                can_read,
                can_write,
                expires_at_ms,
                expected_revision,
                updated_at_ms,
            )
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Memory grant failed: {error:?}"))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-grant-v1",
            "status": "committed",
            "subject_id": subject_id,
            "owner_id": owner_id,
            "scope_kind": scope_kind,
            "can_read": can_read,
            "can_write": can_write,
            "revision": revision,
            "expires_at_ms": expires_at_ms,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_revoke_memory_access(
    subject_id: String,
    owner_id: String,
    scope_kind: String,
    expected_revision: u64,
    revoked_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let actor_id = session_profile_id();
        let revision = repository
            .lock()
            .revoke_access_as(
                &actor_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                expected_revision,
                revoked_at_ms,
            )
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Memory revoke failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-grant-v1",
            "status": "revoked",
            "subject_id": subject_id,
            "owner_id": owner_id,
            "scope_kind": scope_kind,
            "revision": revision,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (memory_id, request_id, timestamp, scope_kind=None, owner_id=None, subject_id=None, expected_revision=None))]
pub fn aegis_forget_memory(
    memory_id: u128,
    request_id: u128,
    timestamp: u64,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
    expected_revision: Option<u64>,
) -> PyResult<String> {
    memory_transition(
        memory_id,
        request_id,
        timestamp,
        scope_kind,
        owner_id,
        subject_id,
        expected_revision,
        "forget",
    )
}

#[pyfunction]
#[pyo3(signature = (memory_id, content, expected_revision, request_id, timestamp, scope_kind=None, owner_id=None, subject_id=None))]
pub fn aegis_correct_memory(
    memory_id: u128,
    content: String,
    expected_revision: u64,
    request_id: u128,
    timestamp: u64,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let subject_id = memory_subject(subject_id, &owner_id)?;
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let revision = repository
            .lock()
            .correct_as(
                memory_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                &content,
                expected_revision,
                request_id,
                timestamp,
            )
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Memory correction failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-correction-v1",
            "status": "committed",
            "memory_id": format!("0x{:x}", memory_id),
            "owner_id": owner_id,
            "scope_kind": scope_kind,
            "revision": revision,
            "lifecycle": "CANDIDATE",
            "validation": "UNREVIEWED",
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (memory_id, validation, basis=None, reason=None, expected_revision=0u64, request_id=0u128, timestamp=0u64, scope_kind=None, owner_id=None, subject_id=None))]
pub fn aegis_validate_memory(
    memory_id: u128,
    validation: String,
    basis: Option<String>,
    reason: Option<String>,
    expected_revision: u64,
    request_id: u128,
    timestamp: u64,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let subject_id = memory_subject(subject_id, &owner_id)?;
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let revision = repository
            .lock()
            .validate_as(
                memory_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                &validation,
                basis.as_deref(),
                reason.as_deref(),
                expected_revision,
                request_id,
                timestamp,
            )
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Memory validation failed: {error:?}"
                ))
            })?;
        let lifecycle = if validation == "ACCEPTED" {
            "ACTIVE"
        } else {
            "CANDIDATE"
        };
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-validation-v1",
            "status": "committed",
            "memory_id": format!("0x{:x}", memory_id),
            "owner_id": owner_id,
            "scope_kind": scope_kind,
            "lifecycle": lifecycle,
            "validation": validation,
            "validation_basis": basis,
            "validation_reason": reason,
            "revision": revision,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (memory_id, request_id, timestamp, scope_kind=None, owner_id=None, subject_id=None, expected_revision=None))]
pub fn aegis_restore_memory(
    memory_id: u128,
    request_id: u128,
    timestamp: u64,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
    expected_revision: Option<u64>,
) -> PyResult<String> {
    memory_transition(
        memory_id,
        request_id,
        timestamp,
        scope_kind,
        owner_id,
        subject_id,
        expected_revision,
        "restore",
    )
}

#[pyfunction]
#[pyo3(signature = (memory_id, request_id, timestamp, scope_kind=None, owner_id=None, subject_id=None, expected_revision=None))]
pub fn aegis_purge_memory(
    memory_id: u128,
    request_id: u128,
    timestamp: u64,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
    expected_revision: Option<u64>,
) -> PyResult<String> {
    memory_transition(
        memory_id,
        request_id,
        timestamp,
        scope_kind,
        owner_id,
        subject_id,
        expected_revision,
        "purge",
    )
}

fn memory_transition(
    memory_id: u128,
    request_id: u128,
    timestamp: u64,
    scope_kind: Option<String>,
    owner_id: Option<String>,
    subject_id: Option<String>,
    expected_revision: Option<u64>,
    operation: &str,
) -> PyResult<String> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let subject_id = memory_subject(subject_id, &owner_id)?;
        if !matches!(operation, "forget" | "restore" | "purge") {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "unsupported memory transition operation",
            ));
        }
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let changed = match operation {
            "forget" => repository.lock().forget_as_checked(
                memory_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                request_id,
                timestamp,
                expected_revision,
            ),
            "restore" => repository.lock().restore_as_checked(
                memory_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                request_id,
                timestamp,
                expected_revision,
            ),
            "purge" => repository.lock().purge_as_checked(
                memory_id,
                &subject_id,
                &owner_id,
                &scope_kind,
                request_id,
                timestamp,
                expected_revision,
            ),
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "unsupported memory transition operation",
                ));
            }
        }
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory {operation} failed: {error:?}"
            ))
        })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-memory-transition-v1",
            "status": if changed { "committed" } else { "replayed_or_noop" },
            "operation": operation,
            "memory_id": format!("0x{:x}", memory_id),
            "owner_id": owner_id,
            "scope_kind": scope_kind,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

// Learning Loop PyO3 Bindings (Sprint 5 — Python DX Bridge)
// ─────────────────────────────────────────────────────────────────────────

/// Return a JSON object with learning loop statistics:
///   { "ledger_events": N, "improved_skills": M, "nudged_memories": K,
///     "indexed_sessions": S, "user_models": U }
/// These stats are computed live from the Rust LearningLedger and
/// in-memory indices — no filesystem round-trip.
#[pyfunction]
#[pyo3(signature = (ledger_json, _skill_registry_stats_json=String::new(), search_index_count=0u64, user_model_count=0u64))]
pub fn aegis_get_learning_stats(
    ledger_json: String,
    _skill_registry_stats_json: String,
    search_index_count: u64,
    user_model_count: u64,
) -> PyResult<String> {
    py_safe(move || {
        if ledger_json.len() > MAX_LEARNING_LEDGER_JSON_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "ledger_json exceeds the bounded learning input size",
            ));
        }
        // Parse the ledger to count events by type
        let ledger: serde_json::Value = serde_json::from_str(&ledger_json).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid ledger JSON: {}", e))
        })?;

        let total_events = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| arr.len())
            .unwrap_or(0);

        // Count SkillImproved events for improved_skills
        let improved_skills = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|ev| ev.get("type").and_then(|t| t.as_str()) == Some("SkillImproved"))
                    .count()
            })
            .unwrap_or(0);

        // Count only confirmed durable commits. Historical MemoryPersisted
        // events are legacy and are intentionally not treated as proof of a
        // commit.
        let nudged_memories = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|ev| ev.get("type").and_then(|t| t.as_str()) == Some("MemoryCommitted"))
                    .count()
            })
            .unwrap_or(0);

        let index = get_session_index().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Session index init failed: {error:?}"
            ))
        })?;
        let indexed_session_count = u64::try_from(index.lock().len_scoped(&session_profile_id()))
            .map_err(|_| {
            pyo3::exceptions::PyOverflowError::new_err("session index count exceeds u64")
        })?;
        let final_search_index_count = search_index_count.max(indexed_session_count);

        let stats = serde_json::json!({
            "schema": "aegis-learning-stats-v1",
            "ledger_events": total_events,
            "improved_skills": improved_skills,
            "nudged_memories": nudged_memories,
            "indexed_sessions": final_search_index_count,
            "user_models": user_model_count,
        });

        serde_json::to_string(&stats).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

#[cfg(test)]
mod tests {
    use super::aegis_get_learning_stats;

    #[test]
    fn learning_stats_rejects_oversized_ledger_before_json_parse() {
        let oversized = "x".repeat(super::MAX_LEARNING_LEDGER_JSON_BYTES + 1);
        let result = aegis_get_learning_stats(oversized, String::new(), 0, 0);
        assert!(result.is_err());
    }

    #[cfg(feature = "python-extension")]
    #[test]
    fn memory_transition_rejects_unknown_operation_without_panicking() {
        pyo3::Python::initialize();
        let result = super::memory_transition(
            1,
            1,
            1,
            None,
            Some("owner".to_string()),
            None,
            None,
            "unknown",
        );
        let error = result.expect_err("unknown operations must fail closed");
        assert!(
            error
                .to_string()
                .contains("unsupported memory transition operation")
        );
    }
}

/// Filter and persist bounded memory candidates for the given session.
///
/// The operation is deliberately candidate-only: it never promotes a record
/// to ACTIVE and never hydrates a candidate into model context. The old
/// no-candidate call remains a compatibility acknowledgment.
#[pyfunction]
#[pyo3(signature = (session_id_str, nudge_id=0u128, candidate_count=0usize, candidates_json=None, relevance_threshold=0.7f32, scope_kind=None, owner_id=None))]
pub fn aegis_trigger_memory_nudge(
    session_id_str: String,
    nudge_id: u128,
    candidate_count: usize,
    candidates_json: Option<String>,
    relevance_threshold: f32,
    scope_kind: Option<String>,
    owner_id: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let session_id = parse_session_id(&session_id_str)?;
        let Some(candidates_json) = candidates_json else {
            let ack = serde_json::json!({
                "schema": "aegis-memory-nudge-status-v2",
                "status": "not_executed",
                "candidate_only": true,
                "durable_commit": false,
                "session_id": format!("0x{:x}", session_id),
                "nudge_id": nudge_id,
                "candidate_count": candidate_count,
                "message": "Candidate nudge validated by the compatibility bridge; no background queue or durable commit was executed.",
            });
            return serde_json::to_string(&ack).map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
            });
        };
        if nudge_id == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "nudge_id must be non-zero when candidates are supplied",
            ));
        }
        if !relevance_threshold.is_finite() || !(0.0..=1.0).contains(&relevance_threshold) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "relevance_threshold must be between 0 and 1",
            ));
        }
        let raw_candidates = parse_nudge_candidates(&candidates_json, session_id)?;
        if candidate_count != 0 && candidate_count != raw_candidates.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "candidate_count does not match the candidate payload",
            ));
        }
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(scope_kind)?;
        let ledger = get_session_ledger().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Learning ledger init failed: {error}"
            ))
        })?;
        let nudge = MemoryNudgeSystem::new(relevance_threshold).periodic_nudge(
            nudge_id,
            session_id,
            raw_candidates,
            &mut ledger.lock(),
            None,
        );
        let nudge = match nudge {
            Ok(nudge) => nudge,
            Err(crate::memory::nudge::MemoryError::EmptyCandidates) => {
                let ack = serde_json::json!({
                    "schema": "aegis-memory-nudge-status-v3",
                    "status": "no_candidates",
                    "candidate_only": true,
                    "durable_commit": false,
                    "durable_candidate_commit": false,
                    "session_id": format!("0x{:x}", session_id),
                    "nudge_id": nudge_id,
                    "candidate_count": 0,
                    "message": "No candidate met the relevance threshold.",
                });
                return serde_json::to_string(&ack).map_err(|error| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "Serialization failed: {error}"
                    ))
                });
            }
            Err(error) => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "Memory nudge validation failed: {error:?}"
                )));
            }
        };
        let repository = get_memory_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Memory repository init failed: {error}"
            ))
        })?;
        let mut repository = repository.lock();
        let mut staged = 0usize;
        let mut replayed = 0usize;
        let mut index_pending = 0usize;
        let mut errors = Vec::new();
        for (ordinal, candidate) in nudge.candidates.iter().enumerate() {
            let memory_id = derive_nudge_component_id(
                b"memory",
                nudge_id,
                session_id,
                candidate.content_hash,
                ordinal,
                &owner_id,
                &scope_kind,
            );
            let request_id = derive_nudge_component_id(
                b"request",
                nudge_id,
                session_id,
                candidate.content_hash,
                ordinal,
                &owner_id,
                &scope_kind,
            );
            let provenance = MemoryNudgeProvenance {
                source_session_id: candidate.source_session_id,
                nudge_id,
                nudge_hash: nudge.nudge_hash,
                candidate_hash: candidate.content_hash,
                relevance_score_micros: (f64::from(candidate.relevance_score) * 1_000_000.0).round()
                    as u32,
            };
            match repository.capture_nudged_candidate(
                memory_id,
                &owner_id,
                &owner_id,
                &scope_kind,
                DEFAULT_MEMORY_KIND,
                &candidate.content,
                nudge.timestamp,
                request_id,
                &provenance,
            ) {
                Ok(outcome) if outcome.created => staged += 1,
                Ok(_) => replayed += 1,
                Err(crate::memory::repository::MemoryRepositoryError::CommittedIndexPending {
                    ..
                }) => {
                    staged += 1;
                    index_pending += 1;
                }
                Err(error) => errors.push(format!("candidate {ordinal}: {error:?}")),
            }
        }
        let persisted = staged + replayed;
        let status = if errors.is_empty() {
            "committed"
        } else if persisted > 0 {
            "partial"
        } else {
            "failed"
        };
        let ack = serde_json::json!({
            "schema": "aegis-memory-nudge-status-v3",
            "status": status,
            "candidate_only": true,
            "durable_commit": false,
            "durable_candidate_commit": persisted > 0,
            "activation": "requires_explicit_validation",
            "session_id": format!("0x{:x}", session_id),
            "nudge_id": nudge_id,
            "nudge_hash": hex32(&nudge.nudge_hash),
            "candidate_count": nudge.candidates.len(),
            "staged_count": staged,
            "replayed_count": replayed,
            "index_pending_count": index_pending,
            "errors": errors,
        });
        serde_json::to_string(&ack).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

/// Index a session transcript for cross-session recall and return its content hash.
#[pyfunction]
pub fn aegis_index_session(session_id: u128, content: String, timestamp: u64) -> PyResult<String> {
    aegis_index_session_scoped(
        session_id,
        content,
        timestamp,
        "USER_PRIVATE".to_string(),
        session_profile_id(),
    )
}

/// Index a session with an explicit scope and owner. The owner is validated
/// again by the Rust storage owner; it is not a UI-provided permission grant.
#[pyfunction]
pub fn aegis_index_session_scoped(
    session_id: u128,
    content: String,
    timestamp: u64,
    scope_kind: String,
    owner_id: String,
) -> PyResult<String> {
    py_safe(move || {
        let index = get_session_index().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Session index init failed: {error:?}"
            ))
        })?;
        let mut index = index.lock();
        let session_ledger = get_session_ledger().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Session ledger init failed: {error:?}"
            ))
        })?;
        let mut ledger = session_ledger.lock();
        let content_hash = index
            .index_session_durable_scoped(
                session_id,
                &content,
                timestamp,
                &scope_kind,
                &owner_id,
                &mut ledger,
                None,
            )
            .map_err(|err| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Index failed: {:?}", err))
            })?;
        Ok(hex32(&content_hash))
    })?
}

/// Read the authoritative transcript for a session. Search results alone
/// never hydrate model context; callers must request this separately.
#[pyfunction]
#[pyo3(signature = (session_id, owner_id=None))]
pub fn aegis_read_session_content(
    session_id: u128,
    owner_id: Option<String>,
) -> PyResult<Option<String>> {
    aegis_read_session_content_scoped(session_id, "USER_PRIVATE".to_string(), owner_id)
}

#[pyfunction]
#[pyo3(signature = (session_id, scope_kind="USER_PRIVATE".to_string(), owner_id=None))]
pub fn aegis_read_session_content_scoped(
    session_id: u128,
    scope_kind: String,
    owner_id: Option<String>,
) -> PyResult<Option<String>> {
    py_safe(move || {
        let owner_id = owner_id.unwrap_or_else(session_profile_id);
        let index = get_session_index().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Session index init failed: {error:?}"
            ))
        })?;
        index
            .lock()
            .read_session_content_scoped(session_id, &scope_kind, &owner_id)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Session hydration failed: {error:?}"
                ))
            })
    })?
}

/// Read one authorized session source together with its immutable revision
/// metadata. This is the hydration boundary; candidate search stays payload-free.
#[pyfunction]
#[pyo3(signature = (session_id, scope_kind="USER_PRIVATE".to_string(), owner_id=None))]
pub fn aegis_read_session_record_scoped(
    session_id: u128,
    scope_kind: String,
    owner_id: Option<String>,
) -> PyResult<Option<String>> {
    py_safe(move || {
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(Some(scope_kind))?;
        let index = get_session_index().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Session index init failed: {error:?}"
            ))
        })?;
        let record = index
            .lock()
            .read_session_record_scoped(session_id, &scope_kind, &owner_id)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Session source read failed: {error:?}"
                ))
            })?;
        record
            .map(|source| {
                serde_json::to_string(&serde_json::json!({
                    "session_id": format!("0x{:x}", source.document.session_id),
                    "content_hash": hex32(&source.document.content_hash),
                    "timestamp": source.document.timestamp,
                    "scope_kind": source.document.scope_kind,
                    "owner_id": source.document.owner_id,
                    "content": source.content,
                }))
                .map_err(|error| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "Serialization failed: {error}"
                    ))
                })
            })
            .transpose()
    })?
}

/// Search past sessions and return cryptographically-bound candidate refs.
///
/// Returns a JSON object with an array of CandidateEvidenceRef-like results:
///   {
///     "schema": "aegis-session-search-result-v1",
///     "query": "...",
///     "top_k": 5,
///     "count": ..., "results": [...],
///     "tier": "ColdVectorExpansion",
///     "gate": "CandidateOnly"
///   }
///
/// **Security**: results are CandidateOnlyGate-tiered — they are NOT truth
/// and cannot be used as PhysicalWitness or PolicyApproval.
#[pyfunction]
#[pyo3(signature = (query, top_k=5usize))]
pub fn aegis_search_past_sessions(query: String, top_k: usize) -> PyResult<String> {
    aegis_search_past_sessions_scoped(
        query,
        top_k,
        "USER_PRIVATE".to_string(),
        Some(session_profile_id()),
    )
}

/// Search sessions within one owner scope. Results remain candidate-only
/// references and never include source text.
#[pyfunction]
#[pyo3(signature = (query, top_k=5usize, scope_kind="USER_PRIVATE".to_string(), owner_id=None))]
pub fn aegis_search_past_sessions_scoped(
    query: String,
    top_k: usize,
    scope_kind: String,
    owner_id: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        if query.len() > 64 * 1024 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "query exceeds the bounded session search size",
            ));
        }
        if query.trim().is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "query must be non-empty",
            ));
        }

        if top_k == 0 || top_k > 100 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "top_k must be between 1 and 100",
            ));
        }

        let session_index = get_session_index().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Session index init failed: {error:?}"
            ))
        })?;
        let index = session_index.lock();
        let owner_id = memory_owner(owner_id)?;
        let scope_kind = memory_scope(Some(scope_kind))?;
        let candidates = index.search_sessions_scoped(&query, top_k, &scope_kind, &owner_id);

        let results: Vec<serde_json::Value> = candidates
            .iter()
            .map(|c| {
                serde_json::json!({
                    "evidence_ref_hash": hex32(&c.evidence_ref_hash),
                    "segment_id": c.segment_id,
                    "tier": "ColdVectorExpansion",
                    "score": (c.score_quantized as f64) / (crate::evidence_index::CANDIDATE_SCORE_PPM_MAX as f64),
                    "epoch_hash": hex32(&c.index_epoch_hash),
                })
            })
            .collect();

        let envelope = serde_json::json!({
            "schema": "aegis-session-search-result-v1",
            "query": query,
            "top_k": top_k,
            "count": results.len(),
            "results": results,
            "tier": "ColdVectorExpansion",
            "gate": "CandidateOnly",
        });

        serde_json::to_string(&envelope).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}
