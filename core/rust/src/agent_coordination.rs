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
pub const MAX_AGENT_GRAPH_TASKS: usize = 1_024;
pub const MAX_AGENT_GRAPH_DEPENDENCIES: usize = 16_384;
pub const MAX_AGENT_GRAPH_RESOURCES: usize = 256;
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

/// Parsed hierarchical scope for a resource key (`path:...`, `file:...`, `symbol:...::...`, or opaque).
#[derive(Clone, Debug, Eq, PartialEq)]
enum ParsedResourceScope {
    Path(String),
    Symbol { file_path: String, symbol: String },
    Opaque(String),
}

fn normalize_rel_path(raw: &str) -> String {
    let replaced = raw.trim().replace('\\', "/");
    let mut parts = Vec::new();
    for seg in replaced.split('/') {
        let trimmed = seg.trim();
        if trimmed.is_empty() || trimmed == "." {
            continue;
        }
        parts.push(trimmed);
    }
    parts.join("/")
}

fn parse_resource_scope(raw_key: &str) -> ParsedResourceScope {
    let trimmed = raw_key.trim();
    if let Some(rest) = trimmed.strip_prefix("symbol:") {
        if let Some((path_part, sym_part)) = rest.split_once("::") {
            let file_path = normalize_rel_path(path_part);
            let symbol = sym_part.trim().to_string();
            if !file_path.is_empty() && !symbol.is_empty() {
                return ParsedResourceScope::Symbol { file_path, symbol };
            }
        }
    }
    if let Some(rest) = trimmed
        .strip_prefix("path:")
        .or_else(|| trimmed.strip_prefix("file:"))
    {
        if let Some((path_part, sym_part)) = rest.split_once("::") {
            let file_path = normalize_rel_path(path_part);
            let symbol = sym_part.trim().to_string();
            if !file_path.is_empty() && !symbol.is_empty() {
                return ParsedResourceScope::Symbol { file_path, symbol };
            }
        }
        let norm = normalize_rel_path(rest);
        if !norm.is_empty() {
            return ParsedResourceScope::Path(norm);
        }
    }
    if !trimmed.contains(':') {
        if let Some((path_part, sym_part)) = trimmed.split_once("::") {
            if path_part.contains('/') || path_part.contains('\\') || path_part.contains('.') {
                let file_path = normalize_rel_path(path_part);
                let symbol = sym_part.trim().to_string();
                if !file_path.is_empty() && !symbol.is_empty() {
                    return ParsedResourceScope::Symbol { file_path, symbol };
                }
            }
        }
    }
    ParsedResourceScope::Opaque(trimmed.to_string())
}

fn path_overlaps(left: &str, right: &str) -> bool {
    if left == right {
        return true;
    }
    left.strip_prefix(right)
        .is_some_and(|rest| rest.starts_with('/'))
        || right
            .strip_prefix(left)
            .is_some_and(|rest| rest.starts_with('/'))
}

fn symbol_overlaps(left: &str, right: &str) -> bool {
    if left == right {
        return true;
    }
    left.strip_prefix(right)
        .is_some_and(|rest| rest.starts_with("::") || rest.starts_with('.'))
        || right
            .strip_prefix(left)
            .is_some_and(|rest| rest.starts_with("::") || rest.starts_with('.'))
}

/// Determine whether two resource lease keys conflict hierarchically:
/// - `path:a/b` conflicts with `path:a/b/c.rs` and `symbol:a/b/c.rs::fn_x`
/// - `path:a/b/c.rs` conflicts with `symbol:a/b/c.rs::fn_x`
/// - `symbol:a/b/c.rs::Impl` conflicts with `symbol:a/b/c.rs::Impl::method`
/// - `symbol:a/b/c.rs::fn_x` does NOT conflict with `symbol:a/b/c.rs::fn_y`
pub fn resource_keys_conflict(left: &str, right: &str) -> bool {
    if left.trim() == right.trim() {
        return true;
    }
    match (parse_resource_scope(left), parse_resource_scope(right)) {
        (ParsedResourceScope::Path(lp), ParsedResourceScope::Path(rp)) => path_overlaps(&lp, &rp),
        (ParsedResourceScope::Path(lp), ParsedResourceScope::Symbol { file_path, .. })
        | (ParsedResourceScope::Symbol { file_path, .. }, ParsedResourceScope::Path(lp)) => {
            path_overlaps(&lp, &file_path)
        }
        (
            ParsedResourceScope::Symbol {
                file_path: lf,
                symbol: ls,
            },
            ParsedResourceScope::Symbol {
                file_path: rf,
                symbol: rs,
            },
        ) => lf == rf && symbol_overlaps(&ls, &rs),
        _ => false,
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AgentResourceConflict {
    pub left_task_id: u128,
    pub right_task_id: u128,
    pub left_key: String,
    pub right_key: String,
}

/// Identify pairs of tasks in a validated graph that are NOT ordered by
/// transitive DAG dependencies yet claim hierarchically overlapping resources.
pub fn detect_unserialized_resource_conflicts(
    request: &AgentGraphRequest,
) -> Result<Vec<AgentResourceConflict>, AgentGraphError> {
    let _ = validate(request.clone())?;
    let mut ancestors: BTreeMap<u128, BTreeSet<u128>> = BTreeMap::new();
    let task_by_id: BTreeMap<u128, &AgentGraphTask> =
        request.tasks.iter().map(|t| (t.task_id, t)).collect();

    fn collect_ancestors(
        task_id: u128,
        task_by_id: &BTreeMap<u128, &AgentGraphTask>,
        memo: &mut BTreeMap<u128, BTreeSet<u128>>,
    ) -> BTreeSet<u128> {
        if let Some(cached) = memo.get(&task_id) {
            return cached.clone();
        }
        let mut acc = BTreeSet::new();
        if let Some(task) = task_by_id.get(&task_id) {
            for dep in &task.dependency_ids {
                acc.insert(*dep);
                acc.extend(collect_ancestors(*dep, task_by_id, memo));
            }
        }
        memo.insert(task_id, acc.clone());
        acc
    }

    for &task_id in task_by_id.keys() {
        collect_ancestors(task_id, &task_by_id, &mut ancestors);
    }

    let ordered_tasks: Vec<&AgentGraphTask> = task_by_id.values().copied().collect();
    let mut conflicts = Vec::new();
    for i in 0..ordered_tasks.len() {
        for j in (i + 1)..ordered_tasks.len() {
            let a = ordered_tasks[i];
            let b = ordered_tasks[j];
            let ordered = ancestors
                .get(&a.task_id)
                .is_some_and(|set| set.contains(&b.task_id))
                || ancestors
                    .get(&b.task_id)
                    .is_some_and(|set| set.contains(&a.task_id));
            if ordered {
                continue;
            }
            for lk in &a.exclusive_resource_keys {
                for rk in &b.exclusive_resource_keys {
                    if resource_keys_conflict(lk, rk) {
                        conflicts.push(AgentResourceConflict {
                            left_task_id: a.task_id,
                            right_task_id: b.task_id,
                            left_key: lk.clone(),
                            right_key: rk.clone(),
                        });
                    }
                }
            }
        }
    }
    Ok(conflicts)
}

/// Validate compact machine handle syntax (`@sym:<hex>`, `@ev:<hex>`, `@fold:<hex>`, `@obl:<hex>`).
pub fn is_valid_compact_handle(handle: &str) -> bool {
    let Some((prefix, digest)) = handle.split_once(':') else {
        return false;
    };
    if !matches!(prefix, "@sym" | "@ev" | "@fold" | "@obl") {
        return false;
    }
    (16..=64).contains(&digest.len())
        && digest
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TeamTaskStatus {
    Pending,
    Claimed,
    Completed,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TeamTaskRecord {
    pub task_id: u128,
    pub blocked_by: Vec<u128>,
    pub blocks: Vec<u128>,
    pub lease_resources: Vec<String>,
    pub status: TeamTaskStatus,
    pub owner_agent_id: Option<String>,
    pub result_handles: Vec<String>,
    pub updated_seq: u64,
}

/// Monotonic delta-synchronized multi-agent task board (`AIP-v2`) enforcing
/// DAG readiness, hierarchical file/symbol leases, and compact handle envelopes.
#[derive(Clone, Debug, Default)]
pub struct TeamTaskDeltaBoard {
    seq: u64,
    tasks: BTreeMap<u128, TeamTaskRecord>,
}

impl TeamTaskDeltaBoard {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn latest_seq(&self) -> u64 {
        self.seq
    }

    pub fn upsert_task(
        &mut self,
        task_id: u128,
        mut blocked_by: Vec<u128>,
        mut lease_resources: Vec<String>,
    ) -> Result<u64, &'static str> {
        if task_id == 0 {
            return Err("team task_id must be positive");
        }
        blocked_by.sort_unstable();
        blocked_by.dedup();
        if blocked_by.contains(&task_id) {
            return Err("team task cannot block itself");
        }
        lease_resources.sort();
        lease_resources.dedup();
        self.seq = self.seq.saturating_add(1);
        let seq = self.seq;
        let existing_blocks = self
            .tasks
            .get(&task_id)
            .map(|r| r.blocks.clone())
            .unwrap_or_default();
        self.tasks.insert(
            task_id,
            TeamTaskRecord {
                task_id,
                blocked_by: blocked_by.clone(),
                blocks: existing_blocks,
                lease_resources,
                status: TeamTaskStatus::Pending,
                owner_agent_id: None,
                result_handles: Vec::new(),
                updated_seq: seq,
            },
        );
        for dep_id in blocked_by {
            if let Some(dep_task) = self.tasks.get_mut(&dep_id) {
                if !dep_task.blocks.contains(&task_id) {
                    dep_task.blocks.push(task_id);
                    dep_task.blocks.sort_unstable();
                    dep_task.updated_seq = seq;
                }
            }
        }
        Ok(seq)
    }

    pub fn claim_task(&mut self, task_id: u128, agent_id: &str) -> Result<u64, &'static str> {
        if agent_id.trim().is_empty() {
            return Err("agent_id cannot be empty");
        }
        let target = self.tasks.get(&task_id).ok_or("unknown team task")?;
        if target.status != TeamTaskStatus::Pending {
            return Err("team task is not pending");
        }
        for dep_id in &target.blocked_by {
            match self.tasks.get(dep_id) {
                Some(dep) if dep.status == TeamTaskStatus::Completed => {}
                _ => return Err("team task has incomplete dependencies"),
            }
        }
        let needed_leases = target.lease_resources.clone();
        for other in self.tasks.values() {
            if other.task_id != task_id && other.status == TeamTaskStatus::Claimed {
                for lk in &needed_leases {
                    for rk in &other.lease_resources {
                        if resource_keys_conflict(lk, rk) {
                            return Err("hierarchical resource lease conflict with active agent");
                        }
                    }
                }
            }
        }
        self.seq = self.seq.saturating_add(1);
        let seq = self.seq;
        let record = self.tasks.get_mut(&task_id).expect("checked");
        record.status = TeamTaskStatus::Claimed;
        record.owner_agent_id = Some(agent_id.trim().to_string());
        record.updated_seq = seq;
        Ok(seq)
    }

    pub fn complete_task(
        &mut self,
        task_id: u128,
        agent_id: &str,
        handles: Vec<String>,
    ) -> Result<u64, &'static str> {
        for handle in &handles {
            if !is_valid_compact_handle(handle) {
                return Err("team result must use compact @sym/@ev/@fold/@obl handles");
            }
        }
        let record = self.tasks.get_mut(&task_id).ok_or("unknown team task")?;
        if record.status != TeamTaskStatus::Claimed
            || record.owner_agent_id.as_deref() != Some(agent_id.trim())
        {
            return Err("team task is not claimed by this agent");
        }
        self.seq = self.seq.saturating_add(1);
        record.status = TeamTaskStatus::Completed;
        record.result_handles = handles;
        record.updated_seq = self.seq;
        Ok(self.seq)
    }

    pub fn delta_since(&self, since_seq: u64) -> Vec<TeamTaskRecord> {
        self.tasks
            .values()
            .filter(|record| record.updated_seq > since_seq)
            .cloned()
            .collect()
    }
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
