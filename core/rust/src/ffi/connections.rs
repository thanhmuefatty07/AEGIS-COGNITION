//! PyO3 bindings for the local connection/model catalog.

use super::{get_connection_repository, py_safe};
use pyo3::prelude::*;

fn connection_json(record: &crate::connections::ConnectionRecord) -> serde_json::Value {
    serde_json::json!({
        "connection_id": record.connection_id,
        "provider_kind": record.provider_kind,
        "endpoint": record.endpoint,
        "protocol": record.protocol,
        "secret_ref": record.secret_ref,
        "enabled": record.enabled,
        "revision": record.revision,
        "updated_at_ms": record.updated_at_ms,
    })
}

fn model_json(record: &crate::connections::ModelDescriptorRecord) -> serde_json::Value {
    serde_json::json!({
        "connection_id": record.connection_id,
        "model_id": record.model_id,
        "family": record.family,
        "capabilities": record.capabilities,
        "context_limit": record.context_limit,
        "output_limit": record.output_limit,
        "source": record.source,
        "revision": record.revision,
        "observed_at_ms": record.observed_at_ms,
    })
}

fn egress_json(record: &crate::connections::EgressGrantRecord) -> serde_json::Value {
    serde_json::json!({
        "grant_id": record.grant_id,
        "connection_id": record.connection_id,
        "connection_revision": record.connection_revision,
        "data_class": record.data_class,
        "operation": record.operation,
        "expires_at_ms": record.expires_at_ms,
        "revision": record.revision,
        "updated_at_ms": record.updated_at_ms,
        "revoked_at_ms": record.revoked_at_ms,
    })
}

#[pyfunction]
#[pyo3(signature = (connection_id, provider_kind, endpoint, protocol, secret_ref=None, enabled=true, expected_revision=None, updated_at_ms=0u64))]
pub fn aegis_upsert_connection(
    connection_id: String,
    provider_kind: String,
    endpoint: String,
    protocol: String,
    secret_ref: Option<String>,
    enabled: bool,
    expected_revision: Option<u64>,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .upsert_connection(
                &connection_id,
                &provider_kind,
                &endpoint,
                &protocol,
                secret_ref.as_deref(),
                enabled,
                expected_revision,
                updated_at_ms,
            )
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Connection upsert failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-connection-record-v1",
            "status": "committed",
            "record": connection_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_list_connections() -> PyResult<String> {
    py_safe(|| {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let records = repository.lock().list_connections().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Connection list failed: {error:?}"))
        })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-connection-list-v1",
            "count": records.len(),
            "records": records.iter().map(connection_json).collect::<Vec<_>>(),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_revoke_connection(
    connection_id: String,
    expected_revision: u64,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .revoke_connection(&connection_id, expected_revision, updated_at_ms)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Connection revoke failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-connection-record-v1",
            "status": "revoked",
            "record": connection_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (connection_id, model_id, family=None, capabilities_json="[]".to_string(), context_limit=None, output_limit=None, source="manual".to_string(), expected_revision=None, observed_at_ms=0u64))]
pub fn aegis_upsert_model_descriptor(
    connection_id: String,
    model_id: String,
    family: Option<String>,
    capabilities_json: String,
    context_limit: Option<u64>,
    output_limit: Option<u64>,
    source: String,
    expected_revision: Option<u64>,
    observed_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let capabilities: Vec<String> =
            serde_json::from_str(&capabilities_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "capabilities_json must be an array of strings: {error}"
                ))
            })?;
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .upsert_model(
                &connection_id,
                &model_id,
                family.as_deref(),
                &capabilities,
                context_limit,
                output_limit,
                &source,
                expected_revision,
                observed_at_ms,
            )
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Model descriptor upsert failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-model-descriptor-v1",
            "status": "committed",
            "record": model_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_list_model_descriptors(connection_id: String) -> PyResult<String> {
    py_safe(move || {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let records = repository
            .lock()
            .list_models(&connection_id)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Model descriptor list failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-model-descriptor-list-v1",
            "connection_id": connection_id,
            "count": records.len(),
            "records": records.iter().map(model_json).collect::<Vec<_>>(),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (grant_id, connection_id, data_class, operation, expires_at_ms=None, expected_revision=None, updated_at_ms=0u64))]
pub fn aegis_grant_connection_egress(
    grant_id: String,
    connection_id: String,
    data_class: String,
    operation: String,
    expires_at_ms: Option<u64>,
    expected_revision: Option<u64>,
    updated_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .grant_egress(
                &grant_id,
                &connection_id,
                &data_class,
                &operation,
                expires_at_ms,
                expected_revision,
                updated_at_ms,
            )
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!("Egress grant failed: {error:?}"))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-egress-grant-v1",
            "status": "committed",
            "record": egress_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_revoke_connection_egress(
    grant_id: String,
    expected_revision: u64,
    revoked_at_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let record = repository
            .lock()
            .revoke_egress(&grant_id, expected_revision, revoked_at_ms)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!("Egress revoke failed: {error:?}"))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-egress-grant-v1",
            "status": "revoked",
            "record": egress_json(&record),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}

#[pyfunction]
pub fn aegis_check_connection_egress(
    connection_id: String,
    data_class: String,
    operation: String,
    now_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        let repository = get_connection_repository().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Connection repository init failed: {error}"
            ))
        })?;
        let allowed = repository
            .lock()
            .egress_allowed(&connection_id, &data_class, &operation, now_ms)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!("Egress check failed: {error:?}"))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-egress-check-v1",
            "connection_id": connection_id,
            "data_class": data_class,
            "operation": operation,
            "now_ms": now_ms,
            "allowed": allowed,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {error}"))
        })
    })?
}
