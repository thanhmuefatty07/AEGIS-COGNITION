//! PyO3 bindings for the Rust-owned deterministic context selector.

use super::{hex32, py_safe};
use crate::context::{
    ContextGovernor, ContextGovernorConfig, ContextNode, ContextNodeKind, ContextRetentionClass,
};
use pyo3::prelude::*;

const MAX_CONTEXT_ITEMS_JSON_BYTES: usize = 2 * 1024 * 1024;

fn parse_node_id(value: &serde_json::Value) -> PyResult<u128> {
    if let Some(number) = value.as_u64() {
        return Ok(u128::from(number));
    }
    let Some(text) = value.as_str() else {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "context node id must be an integer or string",
        ));
    };
    let parsed = text
        .strip_prefix("0x")
        .map(|hex| u128::from_str_radix(hex, 16))
        .unwrap_or_else(|| text.parse::<u128>());
    parsed.map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("context node id must be a valid u128")
    })
}

fn parse_retention_class(value: Option<&serde_json::Value>) -> PyResult<ContextRetentionClass> {
    let Some(value) = value else {
        return Ok(ContextRetentionClass::Condensable);
    };
    let Some(value) = value.as_str() else {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "context item retention_class must be a string",
        ));
    };
    match value {
        "protected" => Ok(ContextRetentionClass::Protected),
        "condensable" => Ok(ContextRetentionClass::Condensable),
        "ephemeral" => Ok(ContextRetentionClass::Ephemeral),
        _ => Err(pyo3::exceptions::PyValueError::new_err(
            "context item retention_class must be protected, condensable, or ephemeral",
        )),
    }
}

#[pyfunction]
#[pyo3(signature = (items_json, token_budget, mandatory_ids_json="[]".to_string()))]
pub fn aegis_select_context_items(
    items_json: String,
    token_budget: u32,
    mandatory_ids_json: String,
) -> PyResult<String> {
    py_safe(move || {
        if items_json.len() > MAX_CONTEXT_ITEMS_JSON_BYTES
            || mandatory_ids_json.len() > MAX_CONTEXT_ITEMS_JSON_BYTES
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context selector input exceeds the bounded size",
            ));
        }
        let items = serde_json::from_str::<serde_json::Value>(&items_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid context items JSON: {error}"))
        })?;
        let Some(items) = items.as_array() else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context items must be a JSON array",
            ));
        };
        if items.is_empty() || items.len() > 100 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context items must contain between 1 and 100 entries",
            ));
        }
        let mandatory =
            serde_json::from_str::<serde_json::Value>(&mandatory_ids_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid mandatory context ids JSON: {error}"
                ))
            })?;
        let Some(mandatory) = mandatory.as_array() else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "mandatory context ids must be a JSON array",
            ));
        };
        let mandatory_ids = mandatory
            .iter()
            .map(parse_node_id)
            .collect::<PyResult<Vec<_>>>()?;

        let mut governor = ContextGovernor::new(ContextGovernorConfig::bounded(token_budget, 128))
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid context selector configuration: {error:?}"
                ))
            })?;
        for item in items {
            let Some(item) = item.as_object() else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "each context item must be an object",
                ));
            };
            let node_id = item
                .get("node_id")
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err("context item missing node_id")
                })
                .and_then(parse_node_id)?;
            let token_cost = item
                .get("token_cost")
                .and_then(serde_json::Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err("context item token_cost must be a u32")
                })?;
            let utility_score = item
                .get("utility_score")
                .and_then(serde_json::Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .unwrap_or(0);
            let dependency_coverage = item
                .get("dependency_coverage")
                .and_then(serde_json::Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .unwrap_or(0);
            let contradiction_risk = item
                .get("contradiction_risk")
                .and_then(serde_json::Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .unwrap_or(0);
            let retention_class = parse_retention_class(item.get("retention_class"))?;
            governor
                .insert_node(
                    ContextNode::new(
                        node_id,
                        ContextNodeKind::Memory,
                        token_cost,
                        utility_score,
                        dependency_coverage,
                        contradiction_risk,
                    )
                    .with_retention_class(retention_class),
                )
                .map_err(|error| {
                    pyo3::exceptions::PyValueError::new_err(format!(
                        "invalid context item: {error:?}"
                    ))
                })?;
        }
        let pack = governor
            .build_hydrated_context_pack(&mandatory_ids)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "context selection failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-context-selection-v1",
            "selected_node_ids": pack.node_ids.iter().map(|id| format!("0x{id:x}")).collect::<Vec<_>>(),
            "token_count": pack.token_count,
            "utility_score": pack.utility_score,
            "activation_node_count": pack.activation_node_count,
            "digest": hex32(&pack.digest),
            "backend": "rust-context-governor-v1",
        }))
        .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(format!(
            "context selection serialization failed: {error}"
        )))
    })?
}
