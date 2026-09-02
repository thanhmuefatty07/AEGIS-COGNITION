//! Execution-and-cache PyO3 bindings.
//!
//! The parent facade re-exports these functions to preserve the Python ABI.

use super::py_safe;
use pyo3::prelude::*;

// ─── EaC PyO3 Bindings ──────────────────────────────────────────────────────

/// Create a new SemanticCache and look up a prompt.
/// Returns the cached response if found (exact or semantic match), else None.
#[pyfunction]
#[pyo3(signature = (prompt, embedding, namespace, threshold=0.95, cache_file=None))]
pub fn aegis_eac_cache_lookup(
    prompt: String,
    embedding: Vec<f32>,
    namespace: String,
    threshold: f32,
    cache_file: Option<String>,
) -> PyResult<Option<String>> {
    py_safe(move || {
        let cache = crate::eac::cache::SemanticCache::new(threshold, cache_file);
        cache.lookup(&prompt, embedding, &namespace)
    })
}

/// Insert a new record into the semantic cache.
#[pyfunction]
#[pyo3(signature = (prompt, response, embedding, namespace, threshold=0.95, cache_file=None))]
pub fn aegis_eac_cache_insert(
    prompt: String,
    response: String,
    embedding: Vec<f32>,
    namespace: String,
    threshold: f32,
    cache_file: Option<String>,
) -> PyResult<bool> {
    py_safe(move || {
        let mut cache = crate::eac::cache::SemanticCache::new(threshold, cache_file);
        cache.insert(prompt, response, embedding, namespace);
        true
    })
}

/// Invalidate all cache entries for a given namespace.
#[pyfunction]
#[pyo3(signature = (namespace, threshold=0.95, cache_file=None))]
pub fn aegis_eac_cache_invalidate(
    namespace: String,
    threshold: f32,
    cache_file: Option<String>,
) -> PyResult<bool> {
    py_safe(move || {
        let mut cache = crate::eac::cache::SemanticCache::new(threshold, cache_file);
        cache.invalidate(&namespace);
        true
    })
}

/// Execute a batch of tool calls synchronously and return JSON results.
/// `calls_json` must be a JSON array of objects with `call_id`, `tool_name`, `arguments`.
/// Returns a JSON string of results.
#[pyfunction]
pub fn aegis_eac_batch(calls_json: String, policy: String) -> PyResult<String> {
    py_safe(move || {
        let calls: Vec<crate::eac::sandbox::ToolCall> =
            serde_json::from_str(&calls_json).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("Invalid calls JSON: {}", e))
            })?;

        let tx_policy = match policy.as_str() {
            "HaltOnFailure" => crate::eac::sandbox::TransactionPolicy::HaltOnFailure,
            "ContinueOnFailure" => crate::eac::sandbox::TransactionPolicy::ContinueOnFailure,
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "invalid transaction policy; expected HaltOnFailure or ContinueOnFailure",
                ));
            }
        };

        // Default mock executor for the FFI layer — in production this would
        // be wired to the real tool gateway.
        let results = crate::eac::sandbox::BatchExecutor::execute_batch(
            calls,
            tx_policy,
            |call| crate::eac::sandbox::ToolResult {
                call_id: call.call_id,
                success: true,
                output: format!("Executed {} with {:?}", call.tool_name, call.arguments),
                error: None,
            },
            None,
        );

        serde_json::to_string(&results).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

#[cfg(test)]
mod tests {
    use super::aegis_eac_batch;

    #[test]
    fn eac_batch_rejects_unknown_transaction_policy() {
        let result = aegis_eac_batch("[]".to_owned(), "unknown".to_owned());
        assert!(result.is_err());
    }

    #[test]
    fn eac_batch_accepts_explicit_transaction_policy() {
        let result = aegis_eac_batch("[]".to_owned(), "ContinueOnFailure".to_owned());
        assert_eq!(result.expect("empty batch should serialize"), "[]");
    }
}

/// Persist state to the filesystem with BLAKE3 chain integrity.
/// `data_json` is a JSON object of key-value pairs.
/// Returns the state hash as a hex string.
#[pyfunction]
#[pyo3(signature = (base_dir, turn_index, namespace, data_json, prev_state_hash=None))]
pub fn aegis_eac_persist_state(
    base_dir: String,
    turn_index: u64,
    namespace: String,
    data_json: String,
    prev_state_hash: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let data: std::collections::BTreeMap<String, serde_json::Value> =
            serde_json::from_str(&data_json).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("Invalid data JSON: {}", e))
            })?;

        let serde = crate::eac::sandbox::FilesystemSerde::new(&base_dir).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("FilesystemSerde init failed: {}", e))
        })?;

        let turn_state = serde
            .persist(turn_index, &namespace, data, prev_state_hash, None)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Persist failed: {}", e))
            })?;

        Ok(turn_state.state_hash)
    })?
}

/// Load state from the filesystem for a given turn index.
/// Returns the state as a JSON string.
#[pyfunction]
pub fn aegis_eac_load_state(base_dir: String, turn_index: u64) -> PyResult<String> {
    py_safe(move || {
        let serde = crate::eac::sandbox::FilesystemSerde::new(&base_dir).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("FilesystemSerde init failed: {}", e))
        })?;

        let turn_state = serde.load(turn_index, None).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Load failed: {}", e))
        })?;

        serde_json::to_string(&turn_state).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

// ─────────────────────────────────────────────────────────────────────────
