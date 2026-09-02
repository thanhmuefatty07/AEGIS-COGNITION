//! Resource admission and process-local runtime bindings.
//!
//! The wrappers expose the existing Rust resource/runtime authority.  They do
//! not create a second scheduler or normalize caller metadata implicitly.

use super::{get_authoritative_runtime, py_safe};
use crate::resource::ResourceController;
use pyo3::prelude::*;

/// Return the normalized hardware contract used by the authoritative runtime.
#[pyfunction]
pub fn aegis_hardware_profile() -> PyResult<String> {
    py_safe(|| {
        serde_json::to_string(&crate::resource::HardwareProfile::probe()).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "hardware profile serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_resource_contract_version() -> PyResult<&'static str> {
    py_safe(|| crate::resource::RESOURCE_CONTRACT_SCHEMA_V1)
}

/// Validate and preview admission for one typed resource request.
#[pyfunction]
#[pyo3(signature = (request_json, now_ms=None))]
pub fn aegis_resource_admission_preview(
    request_json: String,
    now_ms: Option<u64>,
) -> PyResult<String> {
    py_safe(move || {
        let request: crate::resource::ResourceRequest = serde_json::from_str(&request_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource request JSON: {error}"
                ))
            })?;
        let profile = crate::resource::HardwareProfile::probe();
        let mut controller = crate::resource::AdmissionController::from_hardware(&profile);
        let decision = controller.admit(request, now_ms.unwrap_or(profile.profile_epoch));
        serde_json::to_string(&decision).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "admission decision serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_execution_lanes() -> PyResult<String> {
    py_safe(|| {
        let profile = crate::resource::HardwareProfile::probe();
        serde_json::to_string(&crate::resource::ExecutionLaneRegistry::for_profile(
            &profile,
        ))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "execution lane serialization failed: {error}"
            ))
        })
    })?
}

/// Submit one task/resource proposal to the process-local authoritative runtime.
#[pyfunction]
pub fn aegis_runtime_submit(
    task_id: u128,
    dependency_ids_json: String,
    request_json: String,
    now_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let dependency_ids: Vec<crate::task_ledger::TaskId> =
            serde_json::from_str(&dependency_ids_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid dependency IDs JSON: {error}"
                ))
            })?;
        let request: crate::resource::ResourceRequest = serde_json::from_str(&request_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource request JSON: {error}"
                ))
            })?;
        let task = crate::task_ledger::TaskCard::new(task_id, dependency_ids, 0, None, None);
        let mut runtime = get_authoritative_runtime().lock();
        let admission = runtime.submit(task, request, now_ms).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("runtime submit failed: {error:?}"))
        })?;
        let response = match admission {
            crate::runtime::RuntimeAdmission::Admitted(token) => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "admitted",
                "lease_token": token,
            }),
            crate::runtime::RuntimeAdmission::Queued { position, reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "queued",
                "position": position,
                "reason": reason,
            }),
            crate::runtime::RuntimeAdmission::Rejected { reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "rejected",
                "reason": reason,
            }),
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "runtime response serialization failed: {error}"
            ))
        })
    })?
}

/// Start a strictly newer attempt for a task that is in RetryWait.
#[pyfunction]
pub fn aegis_runtime_retry(task_id: u128, request_json: String, now_ms: u64) -> PyResult<String> {
    py_safe(move || {
        let request: crate::resource::ResourceRequest = serde_json::from_str(&request_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource request JSON: {error}"
                ))
            })?;
        let mut runtime = get_authoritative_runtime().lock();
        let admission = runtime.retry(task_id, request, now_ms).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("runtime retry failed: {error:?}"))
        })?;
        let response = match admission {
            crate::runtime::RuntimeAdmission::Admitted(token) => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "admitted",
                "lease_token": token,
            }),
            crate::runtime::RuntimeAdmission::Queued { position, reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "queued",
                "position": position,
                "reason": reason,
            }),
            crate::runtime::RuntimeAdmission::Rejected { reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "rejected",
                "reason": reason,
            }),
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "runtime retry response serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_runtime_finish(lease_token_json: String, outcome: String) -> PyResult<bool> {
    py_safe(move || {
        let token: crate::resource::ResourceLeaseToken = serde_json::from_str(&lease_token_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource lease token JSON: {error}"
                ))
            })?;
        let mut runtime = get_authoritative_runtime().lock();
        let runtime_outcome = match outcome.trim().to_ascii_lowercase().as_str() {
            "done" => Ok(crate::runtime::RuntimeOutcome::Done),
            "failed" => Ok(crate::runtime::RuntimeOutcome::Failed),
            "retry_wait" | "retry-wait" => Ok(crate::runtime::RuntimeOutcome::RetryWait),
            "timed_out" | "timed-out" | "timeout" => Ok(crate::runtime::RuntimeOutcome::TimedOut),
            "cancelled" | "canceled" => Ok(crate::runtime::RuntimeOutcome::Cancelled),
            _ => Err(crate::runtime::RuntimeError::InvalidOutcome(outcome)),
        }
        .map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid runtime outcome: {error:?}"))
        })?;
        runtime
            .finish(token, runtime_outcome)
            .map(|_| true)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "runtime finish failed: {error:?}"
                ))
            })
    })?
}

/// Return one observation-only resource sample.
#[pyfunction]
pub fn aegis_resource_usage_sample() -> PyResult<String> {
    py_safe(|| {
        let sample = crate::resource::PortableResourceController.sample();
        serde_json::to_string(&sample).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "resource usage serialization failed: {error}"
            ))
        })
    })?
}
