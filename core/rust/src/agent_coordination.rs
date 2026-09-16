//! Native validation for the bounded in-process subagent task graph.
//!
//! Python owns handler execution, while Rust owns the graph invariants that
//! must hold before any child is admitted: identity, dependency closure,
//! acyclicity and disjoint artifact namespaces.

use std::collections::{BTreeMap, BTreeSet};

use blake3::Hasher;
use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const AGENT_GRAPH_SCHEMA_V1: &str = "aegis-agent-graph-v1";
pub const MAX_AGENT_GRAPH_TASKS: usize = 256;
pub const MAX_AGENT_GRAPH_DEPENDENCIES: usize = 4_096;
pub const MAX_AGENT_GRAPH_RESOURCES: usize = 64;
const MAX_AGENT_GRAPH_STRING_BYTES: usize = 4_096;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentGraphTask {
    pub task_id: u128,
    pub dependency_ids: Vec<u128>,
    pub artifact_namespace: String,
    pub exclusive_resource_keys: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentGraphRequest {
    pub schema: String,
    pub tasks: Vec<AgentGraphTask>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AgentGraphValidation {
    pub schema: &'static str,
    pub authority: &'static str,
    pub status: &'static str,
    pub executable: bool,
    pub graph_hash: String,
    pub topological_order: Vec<u128>,
    pub task_count: usize,
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
pub enum AgentGraphError {
    #[error("unsupported agent graph schema")]
    Schema,
    #[error("agent graph exceeds its task bound")]
    TaskBound,
    #[error("agent graph exceeds its dependency edge bound")]
    DependencyBound,
    #[error("agent graph exceeds its exclusive resource bound")]
    ResourceBound,
    #[error("agent graph task id is invalid")]
    InvalidTaskId,
    #[error("agent graph task ids must be unique")]
    DuplicateTaskId,
    #[error("agent graph dependency is invalid")]
    InvalidDependency,
    #[error("agent graph references a missing dependency")]
    MissingDependency,
    #[error("agent graph contains a cycle")]
    Cycle,
    #[error("agent graph artifact namespaces must be disjoint")]
    DuplicateNamespace,
    #[error("agent graph string field is invalid")]
    InvalidString,
    #[error("agent graph exclusive resource keys must be unique")]
    DuplicateResourceKey,
    #[error("agent graph serialization failed")]
    Serialization,
}

#[derive(Clone, Debug, Serialize)]
struct CanonicalTask {
    task_id: u128,
    dependency_ids: Vec<u128>,
    artifact_namespace: String,
    exclusive_resource_keys: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
struct CanonicalGraph {
    schema: &'static str,
    tasks: Vec<CanonicalTask>,
}

pub fn validate(request: AgentGraphRequest) -> Result<AgentGraphValidation, AgentGraphError> {
    if request.schema != AGENT_GRAPH_SCHEMA_V1 {
        return Err(AgentGraphError::Schema);
    }
    if request.tasks.len() > MAX_AGENT_GRAPH_TASKS {
        return Err(AgentGraphError::TaskBound);
    }

    let mut task_map: BTreeMap<u128, Vec<u128>> = BTreeMap::new();
    let mut namespaces = BTreeSet::new();
    let mut canonical_tasks = Vec::with_capacity(request.tasks.len());
    let mut edge_count = 0usize;

    for task in request.tasks {
        if task.task_id == 0 {
            return Err(AgentGraphError::InvalidTaskId);
        }
        if task_map.contains_key(&task.task_id) {
            return Err(AgentGraphError::DuplicateTaskId);
        }
        if task.artifact_namespace.is_empty()
            || task.artifact_namespace.len() > MAX_AGENT_GRAPH_STRING_BYTES
            || task.artifact_namespace.contains('\0')
        {
            return Err(AgentGraphError::InvalidString);
        }
        if !namespaces.insert(task.artifact_namespace.clone()) {
            return Err(AgentGraphError::DuplicateNamespace);
        }
        if task.dependency_ids.len() > MAX_AGENT_GRAPH_DEPENDENCIES {
            return Err(AgentGraphError::DependencyBound);
        }
        let original_dependency_count = task.dependency_ids.len();
        let mut dependencies = task.dependency_ids;
        dependencies.sort_unstable();
        dependencies.dedup();
        if dependencies.len() != original_dependency_count
            || dependencies
                .iter()
                .any(|dependency| *dependency == 0 || *dependency == task.task_id)
        {
            return Err(AgentGraphError::InvalidDependency);
        }
        let mut resource_keys = task.exclusive_resource_keys;
        for key in &resource_keys {
            if key.is_empty() || key.len() > MAX_AGENT_GRAPH_STRING_BYTES || key.contains('\0') {
                return Err(AgentGraphError::InvalidString);
            }
        }
        let original_resource_count = resource_keys.len();
        resource_keys.sort_unstable();
        resource_keys.dedup();
        if resource_keys.len() != original_resource_count {
            return Err(AgentGraphError::DuplicateResourceKey);
        }
        if resource_keys.len() > MAX_AGENT_GRAPH_RESOURCES {
            return Err(AgentGraphError::ResourceBound);
        }
        edge_count = edge_count
            .checked_add(dependencies.len())
            .ok_or(AgentGraphError::DependencyBound)?;
        if edge_count > MAX_AGENT_GRAPH_DEPENDENCIES {
            return Err(AgentGraphError::DependencyBound);
        }
        task_map.insert(task.task_id, dependencies.clone());
        canonical_tasks.push(CanonicalTask {
            task_id: task.task_id,
            dependency_ids: dependencies,
            artifact_namespace: task.artifact_namespace,
            exclusive_resource_keys: resource_keys,
        });
    }

    for dependencies in task_map.values() {
        if dependencies
            .iter()
            .any(|dependency| !task_map.contains_key(dependency))
        {
            return Err(AgentGraphError::MissingDependency);
        }
    }

    let mut children: BTreeMap<u128, Vec<u128>> = task_map
        .keys()
        .copied()
        .map(|task_id| (task_id, Vec::new()))
        .collect();
    let mut indegree: BTreeMap<u128, usize> = task_map
        .iter()
        .map(|(task_id, dependencies)| (*task_id, dependencies.len()))
        .collect();
    for (task_id, dependencies) in &task_map {
        for dependency in dependencies {
            children
                .get_mut(dependency)
                .expect("dependency existence validated")
                .push(*task_id);
        }
    }
    let mut ready: BTreeSet<u128> = indegree
        .iter()
        .filter_map(|(task_id, degree)| (*degree == 0).then_some(*task_id))
        .collect();
    let mut topological_order = Vec::with_capacity(task_map.len());
    while let Some(task_id) = ready.pop_first() {
        topological_order.push(task_id);
        for child in children.get(&task_id).expect("task exists") {
            let degree = indegree.get_mut(child).expect("child exists");
            *degree -= 1;
            if *degree == 0 {
                ready.insert(*child);
            }
        }
    }
    if topological_order.len() != task_map.len() {
        return Err(AgentGraphError::Cycle);
    }

    canonical_tasks.sort_by_key(|task| task.task_id);
    let canonical = CanonicalGraph {
        schema: AGENT_GRAPH_SCHEMA_V1,
        tasks: canonical_tasks,
    };
    let bytes = serde_json::to_vec(&canonical).map_err(|_| AgentGraphError::Serialization)?;
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-agent-graph-hash-v1");
    hasher.update(&bytes);
    Ok(AgentGraphValidation {
        schema: AGENT_GRAPH_SCHEMA_V1,
        authority: "native_runtime",
        status: "validated",
        executable: true,
        graph_hash: hasher.finalize().to_hex().to_string(),
        topological_order,
        task_count: task_map.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::{
        AGENT_GRAPH_SCHEMA_V1, AgentGraphError, AgentGraphRequest, AgentGraphTask,
        MAX_AGENT_GRAPH_RESOURCES, validate,
    };

    fn task(task_id: u128, dependency_ids: Vec<u128>, namespace: &str) -> AgentGraphTask {
        AgentGraphTask {
            task_id,
            dependency_ids,
            artifact_namespace: namespace.to_owned(),
            exclusive_resource_keys: Vec::new(),
        }
    }

    #[test]
    fn validates_deterministic_topological_order() {
        let result = validate(AgentGraphRequest {
            schema: AGENT_GRAPH_SCHEMA_V1.to_owned(),
            tasks: vec![
                task(3, vec![1, 2], "agent/3"),
                task(2, vec![], "agent/2"),
                task(1, vec![], "agent/1"),
            ],
        })
        .expect("graph should validate");
        assert_eq!(result.topological_order, vec![1, 2, 3]);
        assert_eq!(result.task_count, 3);
        assert_eq!(result.graph_hash.len(), 64);
    }

    #[test]
    fn rejects_missing_dependency_cycle_and_namespace_collision() {
        let missing = validate(AgentGraphRequest {
            schema: AGENT_GRAPH_SCHEMA_V1.to_owned(),
            tasks: vec![task(1, vec![2], "agent/1")],
        });
        assert_eq!(missing, Err(AgentGraphError::MissingDependency));

        let cycle = validate(AgentGraphRequest {
            schema: AGENT_GRAPH_SCHEMA_V1.to_owned(),
            tasks: vec![task(1, vec![2], "agent/1"), task(2, vec![1], "agent/2")],
        });
        assert_eq!(cycle, Err(AgentGraphError::Cycle));

        let duplicate_namespace = validate(AgentGraphRequest {
            schema: AGENT_GRAPH_SCHEMA_V1.to_owned(),
            tasks: vec![task(1, vec![], "same"), task(2, vec![], "same")],
        });
        assert_eq!(
            duplicate_namespace,
            Err(AgentGraphError::DuplicateNamespace)
        );
    }

    #[test]
    fn rejects_excessive_exclusive_resources() {
        let resource_keys = (0..=MAX_AGENT_GRAPH_RESOURCES)
            .map(|index| format!("resource:{index}"))
            .collect();
        let result = validate(AgentGraphRequest {
            schema: AGENT_GRAPH_SCHEMA_V1.to_owned(),
            tasks: vec![AgentGraphTask {
                task_id: 1,
                dependency_ids: Vec::new(),
                artifact_namespace: "agent/1".to_owned(),
                exclusive_resource_keys: resource_keys,
            }],
        });
        assert_eq!(result, Err(AgentGraphError::ResourceBound));
    }

    #[test]
    fn graph_wire_requires_dependency_and_resource_fields() {
        let decoded: Result<AgentGraphRequest, _> = serde_json::from_str(
            r#"{"schema":"aegis-agent-graph-v1","tasks":[{"task_id":1,"artifact_namespace":"agent/1"}]}"#,
        );
        assert!(decoded.is_err());
    }
}
