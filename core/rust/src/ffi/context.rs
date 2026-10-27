//! PyO3 bindings for the Rust-owned deterministic context selector.

use super::{hex32, py_safe};
use crate::context::{
    ConfirmedFileMemoryRelation, ContextGovernor, ContextGovernorConfig, ContextNode,
    ContextNodeKind, ContextRetentionClass,
};
use pyo3::prelude::*;
use std::collections::{BTreeMap, BTreeSet};

const MAX_CONTEXT_ITEMS_JSON_BYTES: usize = 2 * 1024 * 1024;
const MAX_CONTEXT_DEPENDENCY_COVERAGE: u32 = 1_000_000;
const MAX_CONTEXT_RELATIONS: usize = 8;

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

fn parse_mandatory_ids(mandatory_ids_json: &str) -> PyResult<Vec<u128>> {
    let mandatory =
        serde_json::from_str::<serde_json::Value>(mandatory_ids_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid mandatory context ids JSON: {error}"
            ))
        })?;
    let Some(mandatory) = mandatory.as_array() else {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "mandatory context ids must be a JSON array",
        ));
    };
    mandatory.iter().map(parse_node_id).collect()
}

fn context_governor_from_items(
    items: &[serde_json::Value],
    token_budget: u32,
    use_scalar_dependency_coverage: bool,
) -> PyResult<ContextGovernor> {
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
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("context item missing node_id"))
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
        let dependency_coverage = if use_scalar_dependency_coverage {
            item.get("dependency_coverage")
                .and_then(serde_json::Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .unwrap_or(0)
                .min(MAX_CONTEXT_DEPENDENCY_COVERAGE)
        } else {
            0
        };
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
                pyo3::exceptions::PyValueError::new_err(format!("invalid context item: {error:?}"))
            })?;
    }
    Ok(governor)
}

fn select_context_items_inner(
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
        let mandatory_ids = parse_mandatory_ids(&mandatory_ids_json)?;
        let governor = context_governor_from_items(items, token_budget, true)?;
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

#[pyfunction]
#[pyo3(signature = (items_json, token_budget, mandatory_ids_json="[]".to_string()))]
pub fn aegis_select_context_items(
    py: Python<'_>,
    items_json: String,
    token_budget: u32,
    mandatory_ids_json: String,
) -> PyResult<String> {
    py.detach(move || select_context_items_inner(items_json, token_budget, mandatory_ids_json))
}

fn select_context_items_v2_inner(
    items_json: String,
    relation_graph_json: String,
    token_budget: u32,
    mandatory_ids_json: String,
) -> PyResult<String> {
    py_safe(move || {
        if items_json.len() > MAX_CONTEXT_ITEMS_JSON_BYTES
            || relation_graph_json.len() > MAX_CONTEXT_ITEMS_JSON_BYTES
            || mandatory_ids_json.len() > MAX_CONTEXT_ITEMS_JSON_BYTES
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context selector input exceeds the bounded size",
            ));
        }
        let items = serde_json::from_str::<serde_json::Value>(&items_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid context items JSON: {error}"))
        })?;
        let Some(items_array) = items.as_array() else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context items must be a JSON array",
            ));
        };
        if items_array.is_empty() || items_array.len() > 100 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context items must contain between 1 and 100 entries",
            ));
        }

        let mut item_kinds = BTreeMap::new();
        for item in items_array.iter() {
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
            if node_id == 0 {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "context item node_id must be non-zero",
                ));
            }
            let kind = item
                .get("kind")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "context item kind must be session or memory",
                    )
                })?;
            if !matches!(kind, "session" | "memory")
                || item_kinds.insert(node_id, kind.to_owned()).is_some()
            {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "context item kinds must be session or memory and node ids must be unique",
                ));
            }
        }

        let relation_graph = serde_json::from_str::<serde_json::Value>(&relation_graph_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid context relation graph JSON: {error}"
                ))
            })?;
        let Some(graph) = relation_graph.as_object() else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context relation graph must be an object",
            ));
        };
        if graph.get("schema").and_then(serde_json::Value::as_str)
            != Some("aegis-context-relation-graph-v1")
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "unsupported context relation graph schema",
            ));
        }
        let file_roots = graph
            .get("active_file_node_ids")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("active_file_node_ids must be a JSON array")
            })?;
        if file_roots.len() > MAX_CONTEXT_RELATIONS {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context relation graph exceeds the bounded file-root limit",
            ));
        }
        let mut active_file_ids = BTreeSet::new();
        for value in file_roots {
            let file_id = parse_node_id(value)?;
            if file_id == 0 || item_kinds.contains_key(&file_id) || !active_file_ids.insert(file_id)
            {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "active file roots must be unique non-zero ids outside the selectable items",
                ));
            }
        }

        let relations = graph
            .get("relations")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("context relations must be a JSON array")
            })?;
        if relations.len() > MAX_CONTEXT_RELATIONS {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "context relation graph exceeds the bounded edge limit",
            ));
        }
        let mut relation_ids = BTreeSet::new();
        let mut edge_file_ids = BTreeSet::new();
        let mut typed_relations = Vec::with_capacity(relations.len());
        let mut related_memory_ids = BTreeSet::new();
        for relation in relations {
            let Some(relation) = relation.as_object() else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "each context relation must be an object",
                ));
            };
            if relation.get("kind").and_then(serde_json::Value::as_str)
                != Some("confirmed_file_memory")
            {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "context relation kind must be confirmed_file_memory",
                ));
            }
            let relation_id = relation
                .get("relation_id")
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err("context relation missing relation_id")
                })
                .and_then(parse_node_id)?;
            let file_id = relation
                .get("file_node_id")
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err("context relation missing file_node_id")
                })
                .and_then(parse_node_id)?;
            let memory_id = relation
                .get("memory_node_id")
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "context relation missing memory_node_id",
                    )
                })
                .and_then(parse_node_id)?;
            if relation_id == 0
                || file_id == 0
                || memory_id == 0
                || !relation_ids.insert(relation_id)
                || !active_file_ids.contains(&file_id)
                || item_kinds.get(&memory_id).map(String::as_str) != Some("memory")
            {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "context relation must use unique ids, an active file root, and a memory item",
                ));
            }
            edge_file_ids.insert(file_id);
            related_memory_ids.insert(memory_id);
            typed_relations.push(ConfirmedFileMemoryRelation {
                relation_id,
                file_node_id: file_id,
                memory_node_id: memory_id,
            });
        }
        if edge_file_ids != active_file_ids {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "active file roots must match the relation graph roots",
            ));
        }

        let mandatory_ids = parse_mandatory_ids(&mandatory_ids_json)?;
        let governor = context_governor_from_items(items_array, token_budget, false)?;
        let active_file_ids: Vec<u128> = active_file_ids.into_iter().collect();
        let pack = governor
            .build_hydrated_context_pack_with_file_relations(
                &active_file_ids,
                &typed_relations,
                &mandatory_ids,
            )
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "context selection failed: {error:?}"
                ))
            })?;
        let selected_node_ids: BTreeSet<u128> = pack.node_ids.iter().copied().collect();
        let selected_related_ids: Vec<String> = related_memory_ids
            .iter()
            .filter(|node_id| selected_node_ids.contains(node_id))
            .map(|node_id| format!("0x{node_id:x}"))
            .collect();
        typed_relations.sort_unstable_by_key(|relation| relation.relation_id);
        let selected_relation_ids: Vec<String> = typed_relations
            .into_iter()
            .filter(|relation| selected_node_ids.contains(&relation.memory_node_id))
            .map(|relation| format!("0x{:x}", relation.relation_id))
            .collect();
        serde_json::to_string(&serde_json::json!({
            "schema": "aegis-context-selection-v2",
            "selected_node_ids": pack.node_ids.iter().map(|id| format!("0x{id:x}")).collect::<Vec<_>>(),
            "related_memory_node_ids": selected_related_ids,
            "selected_relation_ids": selected_relation_ids,
            "token_count": pack.token_count,
            "utility_score": pack.utility_score,
            "activation_node_count": pack.activation_node_count,
            "digest": hex32(&pack.digest),
            "backend": "rust-context-governor-v2",
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "context selection serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (items_json, relation_graph_json, token_budget, mandatory_ids_json="[]".to_string()))]
pub fn aegis_select_context_items_v2(
    py: Python<'_>,
    items_json: String,
    relation_graph_json: String,
    token_budget: u32,
    mandatory_ids_json: String,
) -> PyResult<String> {
    py.detach(move || {
        select_context_items_v2_inner(
            items_json,
            relation_graph_json,
            token_budget,
            mandatory_ids_json,
        )
    })
}

#[cfg(test)]
mod tests {
    use super::{
        MAX_CONTEXT_ITEMS_JSON_BYTES, select_context_items_inner as aegis_select_context_items,
        select_context_items_v2_inner as aegis_select_context_items_v2,
    };
    use serde_json::{Value, json};

    fn items_json(count: usize) -> String {
        serde_json::to_string(
            &(1..=count)
                .map(|node_id| {
                    json!({
                        "node_id": format!("0x{node_id:x}"),
                        "token_cost": 1,
                        "utility_score": 500_000,
                    })
                })
                .collect::<Vec<_>>(),
        )
        .expect("test selector items serialize")
    }

    #[test]
    fn selector_accepts_99_and_100_items() {
        for count in [99, 100] {
            let result = aegis_select_context_items(items_json(count), 4096, "[]".to_owned())
                .expect("count is within the FFI item limit");
            let parsed = serde_json::from_str::<Value>(&result).expect("selector result is JSON");
            assert_eq!(
                parsed["selected_node_ids"]
                    .as_array()
                    .map(|items| items.len()),
                Some(count)
            );
        }
    }

    #[test]
    fn selector_rejects_empty_and_101_items() {
        assert!(aegis_select_context_items("[]".to_owned(), 4096, "[]".to_owned()).is_err());
        assert!(aegis_select_context_items(items_json(101), 4096, "[]".to_owned()).is_err());
    }

    #[test]
    fn selector_rejects_each_json_argument_over_two_mib() {
        let oversized_json = " ".repeat(MAX_CONTEXT_ITEMS_JSON_BYTES + 1);
        assert!(aegis_select_context_items(oversized_json, 4096, "[]".to_owned()).is_err());

        assert!(
            aegis_select_context_items(
                items_json(1),
                4096,
                " ".repeat(MAX_CONTEXT_ITEMS_JSON_BYTES + 1),
            )
            .is_err()
        );
    }

    #[test]
    fn selector_prefers_current_file_relation_support_when_candidate_utility_ties() {
        let without_relation = serde_json::to_string(&json!([
            {"node_id": "0x1", "token_cost": 5, "utility_score": 500_000},
            {"node_id": "0x2", "token_cost": 5, "utility_score": 500_000},
        ]))
        .expect("unlinked items serialize");
        let with_relation = serde_json::to_string(&json!([
            {"node_id": "0x1", "token_cost": 5, "utility_score": 500_000},
            {
                "node_id": "0x2",
                "token_cost": 5,
                "utility_score": 500_000,
                "dependency_coverage": 1_000_000,
            },
        ]))
        .expect("linked items serialize");

        let unlinked = aegis_select_context_items(without_relation, 5, "[]".to_owned())
            .expect("unlinked items select");
        let linked = aegis_select_context_items(with_relation, 5, "[]".to_owned())
            .expect("linked items select");
        let unlinked = serde_json::from_str::<Value>(&unlinked).expect("unlinked result is JSON");
        let linked = serde_json::from_str::<Value>(&linked).expect("linked result is JSON");

        assert_eq!(unlinked["selected_node_ids"], json!(["0x1"]));
        assert_eq!(linked["selected_node_ids"], json!(["0x2"]));
    }

    #[test]
    fn selector_caps_dependency_coverage_at_one_million_utility_units() {
        let at_limit = serde_json::to_string(&json!([
            {
                "node_id": "0x1",
                "token_cost": 1,
                "utility_score": 500_000,
                "dependency_coverage": 1_000_000,
            },
        ]))
        .expect("bounded item serializes");
        let above_limit = serde_json::to_string(&json!([
            {
                "node_id": "0x1",
                "token_cost": 1,
                "utility_score": 500_000,
                "dependency_coverage": u32::MAX,
            },
        ]))
        .expect("oversized item serializes");

        let bounded_result = aegis_select_context_items(at_limit, 1, "[]".to_owned())
            .expect("bounded coverage selects");
        let oversized_result = aegis_select_context_items(above_limit, 1, "[]".to_owned())
            .expect("oversized coverage is bounded");

        assert_eq!(oversized_result, bounded_result);
    }

    #[test]
    fn v2_selector_uses_verified_file_memory_edges_and_explains_the_selection() {
        let items = serde_json::to_string(&json!([
            {
                "node_id": "0x1",
                "kind": "memory",
                "token_cost": 5,
                "utility_score": 500_000,
            },
            {
                "node_id": "0x2",
                "kind": "memory",
                "token_cost": 5,
                "utility_score": 500_000,
            },
        ]))
        .expect("context items serialize");
        let no_edges = serde_json::to_string(&json!({
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": [],
            "relations": [],
        }))
        .expect("empty graph serializes");
        let verified_edge = serde_json::to_string(&json!({
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": ["0xf00"],
            "relations": [{
                "relation_id": "0x91",
                "kind": "confirmed_file_memory",
                "file_node_id": "0xf00",
                "memory_node_id": "0x2",
            }],
        }))
        .expect("verified graph serializes");

        let legacy = aegis_select_context_items(items.clone(), 5, "[]".to_owned())
            .expect("legacy selector remains available");
        let unlinked = aegis_select_context_items_v2(items.clone(), no_edges, 5, "[]".to_owned())
            .expect("unlinked v2 selection succeeds");
        let linked = aegis_select_context_items_v2(items, verified_edge, 5, "[]".to_owned())
            .expect("linked v2 selection succeeds");
        let legacy = serde_json::from_str::<Value>(&legacy).expect("legacy result is JSON");
        let unlinked = serde_json::from_str::<Value>(&unlinked).expect("unlinked result is JSON");
        let linked = serde_json::from_str::<Value>(&linked).expect("linked result is JSON");

        assert_eq!(legacy["schema"], "aegis-context-selection-v1");
        assert_eq!(unlinked["schema"], "aegis-context-selection-v2");
        assert_eq!(unlinked["selected_node_ids"], json!(["0x1"]));
        assert_eq!(linked["selected_node_ids"], json!(["0x2"]));
        assert_eq!(linked["related_memory_node_ids"], json!(["0x2"]));
        assert_eq!(linked["selected_relation_ids"], json!(["0x91"]));
        assert_eq!(linked["activation_node_count"], 2);
        assert_eq!(linked["token_count"], 5);
    }

    #[test]
    fn v2_selector_ignores_untyped_scores_and_caps_duplicate_edge_support() {
        let items = json!([
            {
                "node_id": "0x1",
                "kind": "memory",
                "token_cost": 5,
                "utility_score": 500_000,
                "dependency_coverage": u32::MAX,
            },
            {
                "node_id": "0x2",
                "kind": "memory",
                "token_cost": 5,
                "utility_score": 500_000,
                "dependency_coverage": u32::MAX,
            },
        ]);
        let graph = |relations| {
            serde_json::to_string(&json!({
                "schema": "aegis-context-relation-graph-v1",
                "active_file_node_ids": ["0xf00"],
                "relations": relations,
            }))
            .expect("relation graph serializes")
        };
        let one_edge = graph(json!([{
            "relation_id": "0x91",
            "kind": "confirmed_file_memory",
            "file_node_id": "0xf00",
            "memory_node_id": "0x2",
        }]));
        let duplicate_target_edges = graph(json!([
            {
                "relation_id": "0x91",
                "kind": "confirmed_file_memory",
                "file_node_id": "0xf00",
                "memory_node_id": "0x2",
            },
            {
                "relation_id": "0x92",
                "kind": "confirmed_file_memory",
                "file_node_id": "0xf00",
                "memory_node_id": "0x2",
            },
        ]));
        let no_edges = serde_json::to_string(&json!({
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": [],
            "relations": [],
        }))
        .expect("empty graph serializes");
        let items_json = serde_json::to_string(&items).expect("items serialize");
        let unlinked =
            aegis_select_context_items_v2(items_json.clone(), no_edges, 5, "[]".to_owned())
                .expect("unlinked selection succeeds");
        let linked =
            aegis_select_context_items_v2(items_json.clone(), one_edge, 5, "[]".to_owned())
                .expect("linked selection succeeds");
        let duplicated =
            aegis_select_context_items_v2(items_json, duplicate_target_edges, 5, "[]".to_owned())
                .expect("duplicate target edges remain bounded");
        let unlinked = serde_json::from_str::<Value>(&unlinked).expect("result is JSON");
        let linked = serde_json::from_str::<Value>(&linked).expect("result is JSON");
        let duplicated = serde_json::from_str::<Value>(&duplicated).expect("result is JSON");

        assert_eq!(unlinked["selected_node_ids"], json!(["0x1"]));
        assert_eq!(linked["selected_node_ids"], json!(["0x2"]));
        assert_eq!(duplicated["selected_node_ids"], linked["selected_node_ids"]);
        assert_eq!(duplicated["utility_score"], linked["utility_score"]);
        assert_eq!(duplicated["selected_relation_ids"], json!(["0x91", "0x92"]));
    }

    #[test]
    fn v2_selector_rejects_unrooted_duplicate_and_non_memory_relations() {
        let items = serde_json::to_string(&json!([
            {"node_id": "0x1", "kind": "memory", "token_cost": 1},
            {"node_id": "0x2", "kind": "session", "token_cost": 1},
        ]))
        .expect("items serialize");
        let graph = |roots, relations| {
            serde_json::to_string(&json!({
                "schema": "aegis-context-relation-graph-v1",
                "active_file_node_ids": roots,
                "relations": relations,
            }))
            .expect("relation graph serializes")
        };
        let edge = |relation_id, file_node_id, memory_node_id| {
            json!({
                "relation_id": relation_id,
                "kind": "confirmed_file_memory",
                "file_node_id": file_node_id,
                "memory_node_id": memory_node_id,
            })
        };
        for invalid_graph in [
            graph(json!(["0xf00"]), json!([edge("0x91", "0xf01", "0x1")])),
            graph(
                json!(["0xf00"]),
                json!([edge("0x91", "0xf00", "0x1"), edge("0x91", "0xf00", "0x1")]),
            ),
            graph(json!(["0xf00"]), json!([edge("0x91", "0xf00", "0x2")])),
        ] {
            assert!(
                aegis_select_context_items_v2(items.clone(), invalid_graph, 2, "[]".to_owned(),)
                    .is_err()
            );
        }
    }

    #[test]
    fn v2_selector_rejects_more_than_eight_relations() {
        let relations: Vec<serde_json::Value> = (1..=9)
            .map(|relation_id| {
                json!({
                    "relation_id": format!("0x{relation_id:x}"),
                    "kind": "confirmed_file_memory",
                    "file_node_id": "0xf00",
                    "memory_node_id": "0x1",
                })
            })
            .collect();
        let graph = serde_json::to_string(&json!({
            "schema": "aegis-context-relation-graph-v1",
            "active_file_node_ids": ["0xf00"],
            "relations": relations,
        }))
        .expect("oversized relation graph serializes");
        let items = serde_json::to_string(&json!([
            {"node_id": "0x1", "kind": "memory", "token_cost": 1},
        ]))
        .expect("items serialize");

        assert!(aegis_select_context_items_v2(items, graph, 1, "[]".to_owned()).is_err());
    }
}
