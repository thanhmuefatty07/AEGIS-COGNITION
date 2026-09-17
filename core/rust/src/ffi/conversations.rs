//! PyO3 bindings for the canonical local conversation repository.

use super::{get_conversation_repository, py_safe};
use pyo3::prelude::*;

fn conversation_json(record: &crate::conversations::ConversationRecord) -> serde_json::Value {
    serde_json::json!({
        "conversation_id": record.conversation_id,
        "owner_id": record.owner_id,
        "title": record.title,
        "connection_id": record.connection_id,
        "model_id": record.model_id,
        "status": record.status,
        "revision": record.revision,
        "created_at_ms": record.created_at_ms,
        "updated_at_ms": record.updated_at_ms,
    })
}

fn turn_json(record: &crate::conversations::ConversationTurnRecord) -> serde_json::Value {
    serde_json::json!({
        "conversation_id": record.conversation_id,
        "turn_id": record.turn_id,
        "ordinal": record.ordinal,
        "role": record.role,
        "status": record.status,
        "content": record.content,
        "connection_id": record.connection_id,
        "model_id": record.model_id,
        "revision": record.revision,
        "created_at_ms": record.created_at_ms,
        "finished_at_ms": record.finished_at_ms,
        "execution_id": record.execution_id,
    })
}

fn part_json(record: &crate::conversations::ConversationPartRecord) -> serde_json::Value {
    serde_json::json!({
        "conversation_id": record.conversation_id,
        "turn_id": record.turn_id,
        "part_index": record.part_index,
        "kind": record.kind,
        "content": record.content,
    })
}

fn execution_json(record: &crate::conversations::ConversationExecutionRecord) -> serde_json::Value {
    serde_json::json!({
        "execution_id": record.execution_id,
        "conversation_id": record.conversation_id,
        "turn_id": record.turn_id,
        "provider_kind": record.provider_kind,
        "connection_id": record.connection_id,
        "model_id": record.model_id,
        "status": record.status,
        "checkpoint_seq": record.checkpoint_seq,
        "revision": record.revision,
        "started_at_ms": record.started_at_ms,
        "finished_at_ms": record.finished_at_ms,
    })
}

fn checkpoint_json(
    record: &crate::conversations::ConversationCheckpointRecord,
) -> serde_json::Value {
    serde_json::json!({
        "execution_id": record.execution_id,
        "sequence": record.sequence,
        "state": record.state,
        "continuation_json": record.continuation_json,
        "continuation_hash": record.continuation_hash,
        "created_at_ms": record.created_at_ms,
    })
}

fn tool_call_json(record: &crate::conversations::ConversationToolCallRecord) -> serde_json::Value {
    serde_json::json!({
        "call_id": record.call_id,
        "conversation_id": record.conversation_id,
        "request_turn_id": record.request_turn_id,
        "result_turn_id": record.result_turn_id,
        "tool_name": record.tool_name,
        "arguments_json": record.arguments_json,
        "result_content": record.result_content,
        "status": record.status,
        "revision": record.revision,
        "created_at_ms": record.created_at_ms,
        "completed_at_ms": record.completed_at_ms,
    })
}

fn snapshot_json(snapshot: &crate::conversations::ConversationSnapshot) -> serde_json::Value {
    serde_json::json!({
        "conversation": conversation_json(&snapshot.conversation),
        "turns": snapshot.turns.iter().map(turn_json).collect::<Vec<_>>(),
        "parts": snapshot.parts.iter().map(part_json).collect::<Vec<_>>(),
        "executions": snapshot.executions.iter().map(execution_json).collect::<Vec<_>>(),
        "checkpoints": snapshot.checkpoints.iter().map(checkpoint_json).collect::<Vec<_>>(),
        "tool_calls": snapshot.tool_calls.iter().map(tool_call_json).collect::<Vec<_>>(),
    })
}

fn repository_error(error: crate::conversations::ConversationRepositoryError) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(format!(
        "Conversation repository operation failed: {error:?}"
    ))
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, title, connection_id, model_id, created_at_ms=0u64))]
pub fn aegis_create_conversation(
    conversation_id: String,
    owner_id: String,
    title: String,
    connection_id: String,
    model_id: String,
    created_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .create_conversation(
                &conversation_id,
                &owner_id,
                &title,
                &connection_id,
                &model_id,
                created_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-record-v1",
            "status": "committed",
            "record": conversation_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_list_conversations(owner_id: String) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let records = repository
            .lock()
            .list_conversations(&owner_id)
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-list-v1",
            "owner_id": owner_id,
            "count": records.len(),
            "records": records.iter().map(conversation_json).collect::<Vec<_>>(),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_read_conversation(conversation_id: String, owner_id: String) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let snapshot = repository
            .lock()
            .get_conversation(&conversation_id, &owner_id)
            .map_err(repository_error)?
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("conversation not found"))?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-snapshot-v1",
            "status": "read",
            "snapshot": snapshot_json(&snapshot),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, turn_id, role, content, connection_id=None, model_id=None, status="COMPLETED".to_string(), expected_revision=0u64, created_at_ms=0u64))]
pub fn aegis_append_conversation_turn(
    conversation_id: String,
    owner_id: String,
    turn_id: String,
    role: String,
    content: String,
    connection_id: Option<String>,
    model_id: Option<String>,
    status: String,
    expected_revision: u64,
    created_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let turn = repository
            .lock()
            .append_turn(
                &conversation_id,
                &owner_id,
                &turn_id,
                &role,
                &content,
                connection_id.as_deref(),
                model_id.as_deref(),
                &status,
                expected_revision,
                created_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-turn-v1",
            "status": "committed",
            "record": turn_json(&turn),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_switch_conversation_model(
    conversation_id: String,
    owner_id: String,
    connection_id: String,
    model_id: String,
    expected_revision: u64,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .switch_model(
                &conversation_id,
                &owner_id,
                &connection_id,
                &model_id,
                expected_revision,
                updated_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-record-v1",
            "status": "switched",
            "record": conversation_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_set_conversation_status(
    conversation_id: String,
    owner_id: String,
    status: String,
    expected_revision: u64,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .set_status(
                &conversation_id,
                &owner_id,
                &status,
                expected_revision,
                updated_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-record-v1",
            "status": "updated",
            "record": conversation_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, turn_id, kind, content, expected_revision, created_at_ms=0u64))]
pub fn aegis_append_conversation_part(
    conversation_id: String,
    owner_id: String,
    turn_id: String,
    kind: String,
    content: String,
    expected_revision: u64,
    created_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let turn = repository
            .lock()
            .append_turn_part(
                &conversation_id,
                &owner_id,
                &turn_id,
                &kind,
                &content,
                expected_revision,
                created_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-turn-v1",
            "status": "committed",
            "record": turn_json(&turn),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, turn_id, status, expected_revision, updated_at_ms=0u64))]
pub fn aegis_transition_conversation_turn(
    conversation_id: String,
    owner_id: String,
    turn_id: String,
    status: String,
    expected_revision: u64,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let turn = repository
            .lock()
            .transition_turn(
                &conversation_id,
                &owner_id,
                &turn_id,
                &status,
                expected_revision,
                updated_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-turn-v1",
            "status": "updated",
            "record": turn_json(&turn),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, execution_id, turn_id, provider_kind, connection_id, model_id, expected_revision, started_at_ms=0u64))]
pub fn aegis_start_conversation_execution(
    conversation_id: String,
    owner_id: String,
    execution_id: String,
    turn_id: String,
    provider_kind: String,
    connection_id: String,
    model_id: String,
    expected_revision: u64,
    started_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let execution = repository
            .lock()
            .start_execution(
                &conversation_id,
                &owner_id,
                &execution_id,
                &turn_id,
                &provider_kind,
                &connection_id,
                &model_id,
                expected_revision,
                started_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-execution-v1",
            "status": "started",
            "record": execution_json(&execution),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, execution_id, sequence, state, continuation_json, expected_revision, created_at_ms=0u64))]
pub fn aegis_checkpoint_conversation_execution(
    conversation_id: String,
    owner_id: String,
    execution_id: String,
    sequence: u64,
    state: String,
    continuation_json: String,
    expected_revision: u64,
    created_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let execution = repository
            .lock()
            .checkpoint_execution(
                &conversation_id,
                &owner_id,
                &execution_id,
                sequence,
                &state,
                &continuation_json,
                expected_revision,
                created_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-execution-v1",
            "status": "checkpointed",
            "record": execution_json(&execution),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, execution_id, status, expected_revision, finished_at_ms=0u64))]
pub fn aegis_finish_conversation_execution(
    conversation_id: String,
    owner_id: String,
    execution_id: String,
    status: String,
    expected_revision: u64,
    finished_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let execution = repository
            .lock()
            .finish_execution(
                &conversation_id,
                &owner_id,
                &execution_id,
                &status,
                expected_revision,
                finished_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-execution-v1",
            "status": "finished",
            "record": execution_json(&execution),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, call_id, request_turn_id, tool_name, arguments_json, expected_revision, created_at_ms=0u64))]
pub fn aegis_record_conversation_tool_call(
    conversation_id: String,
    owner_id: String,
    call_id: String,
    request_turn_id: String,
    tool_name: String,
    arguments_json: String,
    expected_revision: u64,
    created_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let call = repository
            .lock()
            .record_tool_call(
                &conversation_id,
                &owner_id,
                &call_id,
                &request_turn_id,
                &tool_name,
                &arguments_json,
                expected_revision,
                created_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-tool-call-v1",
            "status": "requested",
            "record": tool_call_json(&call),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, call_id, result_turn_id, result_content, status, expected_revision, completed_at_ms=0u64))]
pub fn aegis_record_conversation_tool_result(
    conversation_id: String,
    owner_id: String,
    call_id: String,
    result_turn_id: String,
    result_content: String,
    status: String,
    expected_revision: u64,
    completed_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let call = repository
            .lock()
            .record_tool_result(
                &conversation_id,
                &owner_id,
                &call_id,
                &result_turn_id,
                &result_content,
                &status,
                expected_revision,
                completed_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-tool-call-v1",
            "status": "settled",
            "record": tool_call_json(&call),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (conversation_id, owner_id, call_id, status, note, expected_revision, reconciled_at_ms=0u64))]
pub fn aegis_reconcile_conversation_tool_call(
    conversation_id: String,
    owner_id: String,
    call_id: String,
    status: String,
    note: String,
    expected_revision: u64,
    reconciled_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_conversation_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Conversation repository init failed: {error}"
            ))
        })?;
        let call = repository
            .lock()
            .reconcile_tool_call(
                &conversation_id,
                &owner_id,
                &call_id,
                &status,
                &note,
                expected_revision,
                reconciled_at_ms,
            )
            .map_err(repository_error)?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-conversation-tool-call-v1",
            "status": "reconciled",
            "record": tool_call_json(&call),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}
