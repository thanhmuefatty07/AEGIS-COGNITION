//! PyO3 bridge for native subagent graph validation.

use super::py_safe;
use pyo3::prelude::*;

const MAX_AGENT_GRAPH_INPUT_JSON_BYTES: usize = 1024 * 1024;

/// Validate a bounded semantic subagent graph before Python schedules it.
#[pyfunction]
pub fn aegis_agent_graph_validate(graph_json: String) -> PyResult<String> {
    py_safe(move || {
        if graph_json.is_empty() || graph_json.len() > MAX_AGENT_GRAPH_INPUT_JSON_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "agent graph JSON exceeds its bounded input size",
            ));
        }
        let request: crate::agent_coordination::AgentGraphRequest =
            serde_json::from_str(&graph_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid agent graph JSON: {error}"
                ))
            })?;
        let result = crate::agent_coordination::validate(request).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "agent graph validation failed: {error}"
            ))
        })?;
        serde_json::to_string(&result).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "agent graph validation response serialization failed: {error}"
            ))
        })
    })?
}

/// Check whether two resource lease keys conflict hierarchically (directory/file/symbol).
#[pyfunction]
pub fn aegis_resource_keys_conflict(left: String, right: String) -> PyResult<bool> {
    py_safe(move || {
        Ok(crate::agent_coordination::resource_keys_conflict(
            &left, &right,
        ))
    })?
}

/// Return JSON list of unserialized hierarchical resource conflicts across concurrent DAG tasks.
#[pyfunction]
pub fn aegis_detect_graph_resource_conflicts(graph_json: String) -> PyResult<String> {
    py_safe(move || {
        if graph_json.is_empty() || graph_json.len() > MAX_AGENT_GRAPH_INPUT_JSON_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "agent graph JSON exceeds its bounded input size",
            ));
        }
        let request: crate::agent_coordination::AgentGraphRequest =
            serde_json::from_str(&graph_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid agent graph JSON: {error}"
                ))
            })?;
        let conflicts = crate::agent_coordination::detect_unserialized_resource_conflicts(&request)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "agent graph validation failed: {error}"
                ))
            })?;
        serde_json::to_string(&conflicts).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "resource conflict serialization failed: {error}"
            ))
        })
    })?
}
