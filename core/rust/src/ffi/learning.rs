//! Learning-loop and session-index PyO3 bindings.
//!
//! The parent facade re-exports these functions to preserve the Python ABI.

use super::{
    get_memory_repository, get_session_index, get_session_ledger, hex32, py_safe,
    session_profile_id,
};
use crate::memory::repository::DEFAULT_MEMORY_KIND;
use pyo3::prelude::*;
use std::path::PathBuf;

const MAX_LEARNING_LEDGER_JSON_BYTES: usize = 8 * 1024 * 1024;

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

/// Trigger a background memory nudge for the given session.
///
/// This is a Python-facing stub that validates the inputs and returns
/// a JSON acknowledgment. The actual Rust nudge must be called via
/// the module's in-process MemoryNudgeSystem — this FFI entrypoint
/// ensures the Python layer can invoke it without panicking.
///
/// Returns a truthful candidate-only status. This compatibility entrypoint
/// validates the identifier but does not own a queue or perform persistence.
#[pyfunction]
#[pyo3(signature = (session_id_str, nudge_id=0u128, candidate_count=0usize))]
pub fn aegis_trigger_memory_nudge(
    session_id_str: String,
    nudge_id: u128,
    candidate_count: usize,
) -> PyResult<String> {
    py_safe(move || {
        // Parse session_id from hex or decimal string
        let session_id = if let Some(hex) = session_id_str.strip_prefix("0x") {
            u128::from_str_radix(hex, 16)
        } else {
            session_id_str.parse::<u128>()
        }
        .map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid session_id '{}': {}",
                session_id_str, e
            ))
        })?;

        if session_id == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "session_id must be non-zero",
            ));
        }

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

        serde_json::to_string(&ack).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
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
