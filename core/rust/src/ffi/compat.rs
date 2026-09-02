//! Compatibility-oriented LLM, harness and telemetry bindings.
//!
//! These entry points preserve the historical Python-facing surface. They
//! route through the existing Rust contracts without becoming another source
//! of Lab state.

use super::{hex32, py_safe, trust_level_label};
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (request_id, session_id, prompt, model_hint=None))]
pub fn aegis_llm_request(
    request_id: u128,
    session_id: u128,
    prompt: String,
    model_hint: Option<String>,
) -> PyResult<bool> {
    py_safe(move || {
        drop(model_hint);
        let request = crate::llm::build_llm_request(request_id, session_id, &prompt, None);
        request.is_valid()
    })
}

#[pyfunction]
pub fn aegis_llm_route(request_id: u128, session_id: u128, prompt: String) -> PyResult<bool> {
    py_safe(move || {
        let request = crate::llm::build_llm_request(request_id, session_id, &prompt, None);
        let providers = [crate::llm::ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        }];
        crate::llm::route_request(&request, &providers).is_some()
    })
}

#[pyfunction]
pub fn aegis_llm_bridge_key(request_id: u128, session_id: u128) -> PyResult<(u128, u128)> {
    py_safe(|| (request_id, session_id))
}

#[pyfunction]
pub fn aegis_llm_normalize(request_id: u128, session_id: u128, content: String) -> PyResult<bool> {
    py_safe(move || {
        let request = crate::llm::build_llm_request(request_id, session_id, "prompt", None);
        let response = crate::llm::normalize_response(&request, &content, 1, 1, 1.0);
        response.is_valid()
    })
}

#[pyfunction]
pub fn aegis_llm_reject(request_id: u128, session_id: u128, error: String) -> PyResult<bool> {
    py_safe(move || {
        drop(error);
        let request = crate::llm::build_llm_request(request_id, session_id, "prompt", None);
        let response = crate::llm::rejected_response(&request, "ffi rejection");
        response.is_valid()
    })
}

#[pyfunction]
pub fn aegis_harness_generate_skeleton(code: String) -> PyResult<String> {
    use crate::harness::{NeuroSymbolicHarness, SkeletonGenerator};
    py_safe(move || NeuroSymbolicHarness::generate_skeleton(&code).unwrap_or_else(|e| e))
}

#[pyfunction]
pub fn aegis_harness_analyze_errors(logs: String) -> PyResult<Vec<String>> {
    use crate::harness::{CompilerFeedbackLoop, NeuroSymbolicHarness};
    py_safe(move || NeuroSymbolicHarness::analyze_compile_errors(&logs))
}

#[pyfunction]
pub fn aegis_physical_metrics_prometheus() -> PyResult<String> {
    py_safe(crate::physical::physical_metrics_prometheus)
}

#[pyfunction]
pub fn aegis_runtime_telemetry_emit(event_json: String) -> PyResult<String> {
    py_safe(move || {
        crate::telemetry::record_python_event_json(&event_json)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    })?
}

#[pyfunction]
pub fn aegis_runtime_telemetry_snapshot() -> PyResult<String> {
    py_safe(|| {
        crate::telemetry::python_event_snapshot_json()
            .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))
    })?
}

#[pyfunction]
pub fn aegis_trust_level() -> PyResult<String> {
    py_safe(|| trust_level_label(crate::hot_engine::TrustLevel::from_env()).to_string())
}

#[pyfunction]
pub fn aegis_hot_hash(payload: Vec<u8>) -> PyResult<String> {
    py_safe(move || hex32(&crate::hot_engine::simd_blake3_hash(&payload)))
}
