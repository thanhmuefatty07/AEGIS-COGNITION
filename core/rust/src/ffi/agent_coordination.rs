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
