use blake3::Hasher;
use std::collections::{BTreeMap, BTreeSet};
use std::num::NonZeroU64;

pub type TaskId = u128;
type ChildrenByDependency = BTreeMap<TaskId, Vec<TaskId>>;
type TopologicalOrderWithChildren = (Vec<TaskId>, ChildrenByDependency);

pub const PRIORITY_WEIGHT_CRITICAL_PATH: u64 = 1_000_000_000;
pub const PRIORITY_WEIGHT_BLOCKED_DESCENDANT: u64 = 1_000_000;
pub const PRIORITY_WEIGHT_EVIDENCE_UNBLOCK: u64 = 1_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RetryPolicy {
    max_total_attempts: u8,
    initial_backoff_ms: u16,
    max_backoff_ms: u16,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_total_attempts: 3,
            initial_backoff_ms: 2_000,
            max_backoff_ms: 4_000,
        }
    }
}

impl RetryPolicy {
    fn delay_after(self, completed_attempt_id: u64) -> Option<u64> {
        if completed_attempt_id == 0 || completed_attempt_id >= u64::from(self.max_total_attempts) {
            return None;
        }
        let exponent = completed_attempt_id.saturating_sub(1).min(63) as u32;
        let multiplier = 1_u64.checked_shl(exponent).unwrap_or(u64::MAX);
        let delay = u64::from(self.initial_backoff_ms)
            .saturating_mul(multiplier)
            .min(u64::from(self.max_backoff_ms));
        (delay > 0).then_some(delay)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TaskStatus {
    Pending,
    Ready,
    Admitted,
    Running,
    RetryWait,
    Cancelling,
    Done,
    Failed,
    Cancelled,
    TimedOut,
    NeedsReconciliation,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TaskCard {
    pub task_id: TaskId,
    pub attempt_id: u64,
    pub status: TaskStatus,
    pub dependency_ids: Vec<TaskId>,
    pub deterministic_priority_score: u64,
    pub suggested_priority: Option<i64>,
    pub critical_path_len: u32,
    pub blocked_descendant_count: u32,
    pub evidence_unblock_count: u32,
    pub hard_deadline_ms: Option<u64>,
    retry_not_before_ms: Option<NonZeroU64>,
    insertion_order: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TaskSelectionProof {
    pub now_ms: u64,
    pub state_epoch: u64,
    pub deadline_horizon_ms: u64,
    pub task_count: u32,
    pub ready_count: u32,
    pub selected_task_id: TaskId,
    pub selected_priority_score: u64,
    pub selected_dependency_hash: [u8; 32],
    pub ledger_state_hash: [u8; 32],
    pub ready_queue_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TaskLedgerError {
    DuplicateTask(TaskId),
    MissingTask(TaskId),
    MissingDependency(TaskId),
    InvalidTask(TaskId),
    InvalidAttempt(TaskId),
    InvalidStatusTransition {
        task_id: TaskId,
        from: TaskStatus,
        to: TaskStatus,
    },
    CycleDetected,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TaskLedger {
    tasks: BTreeMap<TaskId, TaskCard>,
    retry_policy: RetryPolicy,
    deadline_horizon_ms: u64,
    next_insertion_order: u64,
    state_epoch: u64,
    graph_cache: Option<TaskGraphCache>,
    priority_metric_cache: Option<PriorityMetricCache>,
    ready_candidate_cache: Option<ReadyCandidateCache>,
    ready_cache: Option<ReadyQueueCache>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct TaskGraphCache {
    topological_order: Vec<TaskId>,
    children_by_index: Vec<Vec<usize>>,
    is_dependency_forest: bool,
    topological_order_matches_task_order: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct PriorityMetricCache {
    state_epoch: u64,
    critical_path_by_index: Vec<u32>,
    blocked_descendants_by_index: Vec<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ReadyCandidateCache {
    state_epoch: u64,
    task_ids: Vec<TaskId>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ReadyQueueCache {
    now_ms: u64,
    state_epoch: u64,
    task_ids: Vec<TaskId>,
}

impl TaskStatus {
    pub fn can_transition_to(self, next: Self) -> bool {
        matches!(
            (self, next),
            (
                Self::Pending,
                Self::Ready | Self::Cancelled | Self::TimedOut
            ) | (
                Self::Ready,
                Self::Admitted | Self::Cancelled | Self::TimedOut
            ) | (
                Self::Admitted,
                Self::Running | Self::Cancelling | Self::TimedOut | Self::NeedsReconciliation
            ) | (
                Self::Running,
                Self::Done
                    | Self::Failed
                    | Self::RetryWait
                    | Self::Cancelling
                    | Self::TimedOut
                    | Self::NeedsReconciliation
            ) | (Self::RetryWait, Self::Cancelled | Self::TimedOut)
                | (
                    Self::Cancelling,
                    Self::Cancelled | Self::Failed | Self::NeedsReconciliation
                )
        )
    }

    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Done | Self::Failed | Self::Cancelled | Self::TimedOut
        )
    }

    fn can_enter_ready_queue(self) -> bool {
        matches!(self, TaskStatus::Pending | TaskStatus::Ready)
    }

    fn blocks_descendants(self) -> bool {
        !matches!(self, TaskStatus::Done | TaskStatus::Cancelled)
    }
}

impl TaskCard {
    pub fn new(
        task_id: TaskId,
        dependency_ids: Vec<TaskId>,
        evidence_unblock_count: u32,
        hard_deadline_ms: Option<u64>,
        suggested_priority: Option<i64>,
    ) -> Self {
        Self {
            task_id,
            attempt_id: 1,
            status: TaskStatus::Pending,
            dependency_ids,
            deterministic_priority_score: 0,
            suggested_priority,
            critical_path_len: 0,
            blocked_descendant_count: 0,
            evidence_unblock_count,
            hard_deadline_ms,
            retry_not_before_ms: None,
            insertion_order: 0,
        }
    }

    pub fn with_status(mut self, status: TaskStatus) -> Self {
        self.status = status;
        self
    }

    pub fn with_attempt_id(mut self, attempt_id: u64) -> Self {
        self.attempt_id = attempt_id.max(1);
        self
    }

    pub(crate) fn retry_not_before_ms(&self) -> Option<u64> {
        self.retry_not_before_ms.map(NonZeroU64::get)
    }
}

impl TaskLedger {
    pub fn new(deadline_horizon_ms: u64) -> Self {
        Self {
            tasks: BTreeMap::new(),
            retry_policy: RetryPolicy::default(),
            deadline_horizon_ms,
            next_insertion_order: 1,
            state_epoch: 0,
            graph_cache: None,
            priority_metric_cache: None,
            ready_candidate_cache: None,
            ready_cache: None,
        }
    }

    pub fn insert_task(&mut self, mut task: TaskCard) -> Result<(), TaskLedgerError> {
        if task.task_id == 0
            || task
                .dependency_ids
                .iter()
                .any(|dependency| *dependency == 0 || *dependency == task.task_id)
        {
            return Err(TaskLedgerError::InvalidTask(task.task_id));
        }
        if task.attempt_id == 0 || task.attempt_id > u64::from(self.retry_policy.max_total_attempts)
        {
            return Err(TaskLedgerError::InvalidAttempt(task.task_id));
        }
        if self.tasks.contains_key(&task.task_id) {
            return Err(TaskLedgerError::DuplicateTask(task.task_id));
        }

        task.dependency_ids.sort_unstable();
        task.dependency_ids.dedup();
        task.insertion_order = self.next_insertion_order;
        self.next_insertion_order = self.next_insertion_order.saturating_add(1);
        self.tasks.insert(task.task_id, task);
        self.graph_cache = None;
        self.priority_metric_cache = None;
        self.ready_candidate_cache = None;
        self.bump_state_epoch();
        Ok(())
    }

    pub fn task(&self, task_id: TaskId) -> Option<&TaskCard> {
        self.tasks.get(&task_id)
    }

    pub fn mark_status(
        &mut self,
        task_id: TaskId,
        status: TaskStatus,
    ) -> Result<(), TaskLedgerError> {
        self.validate_status_transition(task_id, status)?;
        self.set_status(task_id, status)
    }

    /// Complete a task through the same legal state transitions used by the
    /// runtime. This is useful for replay/import adapters that have a trusted
    /// completion fact but must not mutate `Pending` directly to `Done`.
    pub fn complete_task(&mut self, task_id: TaskId) -> Result<(), TaskLedgerError> {
        loop {
            let current = self
                .tasks
                .get(&task_id)
                .ok_or(TaskLedgerError::MissingTask(task_id))?
                .status;
            let next = match current {
                TaskStatus::Pending => TaskStatus::Ready,
                TaskStatus::Ready => TaskStatus::Admitted,
                TaskStatus::Admitted => TaskStatus::Running,
                TaskStatus::Running => TaskStatus::Done,
                TaskStatus::RetryWait => TaskStatus::Ready,
                TaskStatus::Done => return Ok(()),
                TaskStatus::Failed
                | TaskStatus::Cancelled
                | TaskStatus::TimedOut
                | TaskStatus::Cancelling
                | TaskStatus::NeedsReconciliation => {
                    return Err(TaskLedgerError::InvalidStatusTransition {
                        task_id,
                        from: current,
                        to: TaskStatus::Done,
                    });
                }
            };
            self.transition_status(task_id, next)?;
        }
    }

    fn set_status(&mut self, task_id: TaskId, status: TaskStatus) -> Result<(), TaskLedgerError> {
        {
            let task = self
                .tasks
                .get_mut(&task_id)
                .ok_or(TaskLedgerError::MissingTask(task_id))?;
            if task.status == status {
                return Ok(());
            }
            task.status = status;
            if status != TaskStatus::RetryWait {
                task.retry_not_before_ms = None;
            }
        }
        self.bump_state_epoch();
        Ok(())
    }

    pub fn validate_status_transition(
        &self,
        task_id: TaskId,
        status: TaskStatus,
    ) -> Result<(), TaskLedgerError> {
        let task = self
            .tasks
            .get(&task_id)
            .ok_or(TaskLedgerError::MissingTask(task_id))?;
        if task.status == status || task.status.can_transition_to(status) {
            return Ok(());
        }
        Err(TaskLedgerError::InvalidStatusTransition {
            task_id,
            from: task.status,
            to: status,
        })
    }

    pub fn transition_status(
        &mut self,
        task_id: TaskId,
        status: TaskStatus,
    ) -> Result<(), TaskLedgerError> {
        self.validate_status_transition(task_id, status)?;
        self.set_status(task_id, status)
    }

    /// Retained for source compatibility; retry attempts must be committed by
    /// `AuthoritativeRuntime` only after the resource admission succeeds.
    #[deprecated(note = "retry attempts are committed by AuthoritativeRuntime after admission")]
    pub fn set_attempt_id(
        &mut self,
        task_id: TaskId,
        attempt_id: u64,
    ) -> Result<(), TaskLedgerError> {
        if attempt_id == 0 {
            return Err(TaskLedgerError::InvalidAttempt(task_id));
        }
        self.tasks
            .get(&task_id)
            .ok_or(TaskLedgerError::MissingTask(task_id))?;
        Err(TaskLedgerError::InvalidAttempt(task_id))
    }

    pub(crate) fn retry_delay_after_current_attempt(&self, task_id: TaskId) -> Option<u64> {
        let task = self.tasks.get(&task_id)?;
        self.retry_policy.delay_after(task.attempt_id)
    }

    pub(crate) fn schedule_retry_wait(
        &mut self,
        task_id: TaskId,
        not_before_ms: u64,
    ) -> Result<(), TaskLedgerError> {
        let not_before =
            NonZeroU64::new(not_before_ms).ok_or(TaskLedgerError::InvalidAttempt(task_id))?;
        self.validate_retry_wait(task_id, not_before_ms)?;
        let task = self
            .tasks
            .get_mut(&task_id)
            .ok_or(TaskLedgerError::MissingTask(task_id))?;
        task.retry_not_before_ms = Some(not_before);
        task.status = TaskStatus::RetryWait;
        self.bump_state_epoch();
        Ok(())
    }

    pub(crate) fn validate_retry_wait(
        &self,
        task_id: TaskId,
        not_before_ms: u64,
    ) -> Result<(), TaskLedgerError> {
        let task = self
            .tasks
            .get(&task_id)
            .ok_or(TaskLedgerError::MissingTask(task_id))?;
        if task.status != TaskStatus::Running
            || !task.status.can_transition_to(TaskStatus::RetryWait)
            || self.retry_policy.delay_after(task.attempt_id).is_none()
            || task
                .hard_deadline_ms
                .is_some_and(|deadline_ms| not_before_ms >= deadline_ms)
        {
            return Err(TaskLedgerError::InvalidStatusTransition {
                task_id,
                from: task.status,
                to: TaskStatus::RetryWait,
            });
        }
        Ok(())
    }

    pub(crate) fn validate_retry_start(
        &self,
        task_id: TaskId,
        attempt_id: u64,
        now_ms: u64,
    ) -> Result<(), TaskLedgerError> {
        let task = self
            .tasks
            .get(&task_id)
            .ok_or(TaskLedgerError::MissingTask(task_id))?;
        if task.status != TaskStatus::RetryWait
            || task.attempt_id.checked_add(1) != Some(attempt_id)
            || attempt_id > u64::from(self.retry_policy.max_total_attempts)
            || task
                .retry_not_before_ms()
                .is_none_or(|not_before_ms| now_ms < not_before_ms)
            || task
                .hard_deadline_ms
                .is_some_and(|deadline_ms| now_ms >= deadline_ms)
        {
            return Err(TaskLedgerError::InvalidAttempt(task_id));
        }
        let valid_transition_path = TaskStatus::Ready.can_transition_to(TaskStatus::Admitted)
            && TaskStatus::Admitted.can_transition_to(TaskStatus::Running);
        if !valid_transition_path {
            return Err(TaskLedgerError::InvalidStatusTransition {
                task_id,
                from: task.status,
                to: TaskStatus::Running,
            });
        }
        Ok(())
    }

    pub(crate) fn start_retry_attempt(
        &mut self,
        task_id: TaskId,
        attempt_id: u64,
        now_ms: u64,
    ) -> Result<(), TaskLedgerError> {
        self.validate_retry_start(task_id, attempt_id, now_ms)?;
        let task = self
            .tasks
            .get_mut(&task_id)
            .ok_or(TaskLedgerError::MissingTask(task_id))?;
        task.attempt_id = attempt_id;
        task.retry_not_before_ms = None;
        task.status = TaskStatus::Running;
        self.bump_state_epoch();
        Ok(())
    }

    pub fn recompute_priorities(&mut self, now_ms: u64) -> Result<(), TaskLedgerError> {
        if self.graph_cache.is_none() {
            self.graph_cache = Some(self.build_graph_cache()?);
            self.priority_metric_cache = None;
        }

        let should_rebuild_metrics = self
            .priority_metric_cache
            .as_ref()
            .map(|metrics| metrics.state_epoch != self.state_epoch)
            .unwrap_or(true);

        if should_rebuild_metrics {
            let cache = self
                .graph_cache
                .as_ref()
                .expect("graph cache is initialized before recompute");
            let metrics = if cache.is_dependency_forest {
                build_forest_priority_metrics_cached(&self.tasks, cache, self.state_epoch)
            } else {
                build_exact_dag_priority_metrics_cached(&self.tasks, cache, self.state_epoch)?
            };
            self.priority_metric_cache = Some(metrics);
        }

        apply_priority_scores_cached_metrics(
            &mut self.tasks,
            now_ms,
            self.deadline_horizon_ms,
            self.graph_cache
                .as_ref()
                .expect("graph cache is initialized before applying metrics"),
            self.priority_metric_cache
                .as_ref()
                .expect("priority metric cache is initialized before applying scores"),
        );
        Ok(())
    }

    fn build_graph_cache(&self) -> Result<TaskGraphCache, TaskLedgerError> {
        let (topological_order, children) = self.topological_order_with_children()?;
        let index_by_task: BTreeMap<TaskId, usize> = topological_order
            .iter()
            .enumerate()
            .map(|(index, task_id)| (*task_id, index))
            .collect();
        let mut children_by_index: Vec<Vec<usize>> = vec![Vec::new(); topological_order.len()];
        for (task_id, child_ids) in &children {
            let parent_index = *index_by_task
                .get(task_id)
                .expect("children map contains known task ids");
            children_by_index[parent_index] = child_ids
                .iter()
                .map(|child_id| {
                    *index_by_task
                        .get(child_id)
                        .expect("child id exists in topological order")
                })
                .collect();
        }

        let topological_order_matches_task_order = topological_order
            .iter()
            .copied()
            .eq(self.tasks.keys().copied());

        Ok(TaskGraphCache {
            topological_order,
            children_by_index,
            is_dependency_forest: self
                .tasks
                .values()
                .all(|task| task.dependency_ids.len() <= 1),
            topological_order_matches_task_order,
        })
    }

    pub fn ordered_ready_tasks(&mut self, now_ms: u64) -> Result<Vec<TaskId>, TaskLedgerError> {
        if let Some(cache) = &self.ready_cache {
            if cache.now_ms == now_ms && cache.state_epoch == self.state_epoch {
                return Ok(cache.task_ids.clone());
            }
        }

        self.recompute_priorities(now_ms)?;
        let ready_ids = self.ready_candidate_ids()?;
        let mut ready: Vec<&TaskCard> = ready_ids
            .iter()
            .map(|task_id| {
                self.tasks
                    .get(task_id)
                    .expect("ready candidate cache contains known tasks")
            })
            .collect();
        ready.sort_by(|left, right| priority_order(left, right));
        let task_ids: Vec<TaskId> = ready.into_iter().map(|task| task.task_id).collect();
        self.ready_cache = Some(ReadyQueueCache {
            now_ms,
            state_epoch: self.state_epoch,
            task_ids: task_ids.clone(),
        });
        Ok(task_ids)
    }

    pub fn select_next_task(&mut self, now_ms: u64) -> Result<Option<TaskId>, TaskLedgerError> {
        Ok(self.ordered_ready_tasks(now_ms)?.into_iter().next())
    }

    pub fn select_next_task_proof(
        &mut self,
        now_ms: u64,
    ) -> Result<Option<TaskSelectionProof>, TaskLedgerError> {
        let ready_task_ids = self.ordered_ready_tasks(now_ms)?;
        let Some(selected_task_id) = ready_task_ids.first().copied() else {
            return Ok(None);
        };
        let selected_task = self
            .tasks
            .get(&selected_task_id)
            .ok_or(TaskLedgerError::MissingTask(selected_task_id))?;
        let selected_dependency_hash = task_dependency_hash(&selected_task.dependency_ids);
        let ledger_state_hash = self.ledger_state_hash()?;
        let ready_queue_hash = task_ready_queue_hash(
            now_ms,
            self.state_epoch,
            self.deadline_horizon_ms,
            &ready_task_ids,
            &self.tasks,
        )?;
        let proof_hash = task_selection_proof_hash(
            now_ms,
            self.state_epoch,
            self.deadline_horizon_ms,
            self.tasks.len().min(u32::MAX as usize) as u32,
            ready_task_ids.len().min(u32::MAX as usize) as u32,
            selected_task_id,
            selected_task.deterministic_priority_score,
            selected_dependency_hash,
            ledger_state_hash,
            ready_queue_hash,
        );
        let proof = TaskSelectionProof {
            now_ms,
            state_epoch: self.state_epoch,
            deadline_horizon_ms: self.deadline_horizon_ms,
            task_count: self.tasks.len().min(u32::MAX as usize) as u32,
            ready_count: ready_task_ids.len().min(u32::MAX as usize) as u32,
            selected_task_id,
            selected_priority_score: selected_task.deterministic_priority_score,
            selected_dependency_hash,
            ledger_state_hash,
            ready_queue_hash,
            proof_hash,
        };
        proof
            .has_valid_fields()
            .then_some(Some(proof))
            .ok_or(TaskLedgerError::InvalidTask(selected_task_id))
    }

    pub fn validates_task_selection_proof(
        &mut self,
        proof: &TaskSelectionProof,
    ) -> Result<bool, TaskLedgerError> {
        if !proof.has_valid_fields() {
            return Ok(false);
        }
        Ok(self
            .select_next_task_proof(proof.now_ms)?
            .is_some_and(|rebuilt| rebuilt == *proof))
    }

    pub fn topological_order(&self) -> Result<Vec<TaskId>, TaskLedgerError> {
        self.topological_order_with_children()
            .map(|(topological_order, _children)| topological_order)
    }

    fn topological_order_with_children(
        &self,
    ) -> Result<TopologicalOrderWithChildren, TaskLedgerError> {
        self.validate_dependencies()?;

        let children = self.children_by_dependency_unchecked();
        let mut indegree: BTreeMap<TaskId, usize> = self
            .tasks
            .iter()
            .map(|(task_id, task)| (*task_id, task.dependency_ids.len()))
            .collect();
        let mut zero_indegree: BTreeSet<TaskId> = indegree
            .iter()
            .filter_map(|(task_id, degree)| (*degree == 0).then_some(*task_id))
            .collect();
        let mut order = Vec::with_capacity(self.tasks.len());

        while let Some(task_id) = zero_indegree.iter().next().copied() {
            zero_indegree.remove(&task_id);
            order.push(task_id);

            if let Some(child_ids) = children.get(&task_id) {
                for child_id in child_ids {
                    let child_indegree = indegree
                        .get_mut(child_id)
                        .expect("children map contains known task ids");
                    *child_indegree = child_indegree.saturating_sub(1);
                    if *child_indegree == 0 {
                        zero_indegree.insert(*child_id);
                    }
                }
            }
        }

        if order.len() != self.tasks.len() {
            return Err(TaskLedgerError::CycleDetected);
        }
        Ok((order, children))
    }

    fn dependencies_are_done(&self, task: &TaskCard) -> bool {
        task.dependency_ids.iter().all(|dependency_id| {
            self.tasks
                .get(dependency_id)
                .map(|dependency| dependency.status == TaskStatus::Done)
                .unwrap_or(false)
        })
    }

    fn validate_dependencies(&self) -> Result<(), TaskLedgerError> {
        for task in self.tasks.values() {
            if task.task_id == 0 {
                return Err(TaskLedgerError::InvalidTask(task.task_id));
            }
            for dependency_id in &task.dependency_ids {
                if *dependency_id == 0 || *dependency_id == task.task_id {
                    return Err(TaskLedgerError::InvalidTask(task.task_id));
                }
                if !self.tasks.contains_key(dependency_id) {
                    return Err(TaskLedgerError::MissingDependency(*dependency_id));
                }
            }
        }
        Ok(())
    }

    fn children_by_dependency_unchecked(&self) -> BTreeMap<TaskId, Vec<TaskId>> {
        let mut children: BTreeMap<TaskId, Vec<TaskId>> = self
            .tasks
            .keys()
            .map(|task_id| (*task_id, Vec::new()))
            .collect();
        for task in self.tasks.values() {
            for dependency_id in &task.dependency_ids {
                children
                    .get_mut(dependency_id)
                    .expect("dependency validated as present")
                    .push(task.task_id);
            }
        }
        children
    }

    fn ready_candidate_ids(&mut self) -> Result<Vec<TaskId>, TaskLedgerError> {
        if let Some(cache) = &self.ready_candidate_cache {
            if cache.state_epoch == self.state_epoch {
                return Ok(cache.task_ids.clone());
            }
        }

        let task_ids: Vec<TaskId> = self
            .tasks
            .values()
            .filter(|task| task.status.can_enter_ready_queue() && self.dependencies_are_done(task))
            .map(|task| task.task_id)
            .collect();
        self.ready_candidate_cache = Some(ReadyCandidateCache {
            state_epoch: self.state_epoch,
            task_ids: task_ids.clone(),
        });
        Ok(task_ids)
    }

    pub(crate) fn ledger_state_hash(&self) -> Result<[u8; 32], TaskLedgerError> {
        self.validate_dependencies()?;
        Ok(task_ledger_state_hash(
            self.state_epoch,
            self.deadline_horizon_ms,
            self.retry_policy,
            &self.tasks,
        ))
    }

    #[cfg(test)]
    pub(crate) fn ledger_state_hash_for_test(&self) -> Result<[u8; 32], TaskLedgerError> {
        self.ledger_state_hash()
    }

    fn bump_state_epoch(&mut self) {
        self.state_epoch = self.state_epoch.saturating_add(1);
        self.priority_metric_cache = None;
        self.ready_candidate_cache = None;
        self.ready_cache = None;
    }
}

impl TaskSelectionProof {
    pub fn has_valid_fields(&self) -> bool {
        self.task_count > 0
            && self.ready_count > 0
            && self.ready_count <= self.task_count
            && self.selected_task_id > 0
            && nonzero_hash(&self.selected_dependency_hash)
            && nonzero_hash(&self.ledger_state_hash)
            && nonzero_hash(&self.ready_queue_hash)
            && self.proof_hash
                == task_selection_proof_hash(
                    self.now_ms,
                    self.state_epoch,
                    self.deadline_horizon_ms,
                    self.task_count,
                    self.ready_count,
                    self.selected_task_id,
                    self.selected_priority_score,
                    self.selected_dependency_hash,
                    self.ledger_state_hash,
                    self.ready_queue_hash,
                )
            && nonzero_hash(&self.proof_hash)
    }
}

fn build_forest_priority_metrics_cached(
    tasks: &BTreeMap<TaskId, TaskCard>,
    cache: &TaskGraphCache,
    state_epoch: u64,
) -> PriorityMetricCache {
    let blocks_by_index: Vec<bool> = cache
        .topological_order
        .iter()
        .map(|task_id| {
            tasks
                .get(task_id)
                .map(|task| task.status.blocks_descendants())
                .unwrap_or(false)
        })
        .collect();
    let mut critical_path_by_index = vec![1u32; cache.topological_order.len()];
    let mut blocked_descendants_by_index = vec![0u32; cache.topological_order.len()];

    for index in (0..cache.topological_order.len()).rev() {
        let mut critical_path_len = 1u32;
        let mut blocked_descendant_count = 0u32;

        for child_index in &cache.children_by_index[index] {
            critical_path_len =
                critical_path_len.max(critical_path_by_index[*child_index].saturating_add(1));
            blocked_descendant_count =
                blocked_descendant_count.saturating_add(blocked_descendants_by_index[*child_index]);
            if blocks_by_index[*child_index] {
                blocked_descendant_count = blocked_descendant_count.saturating_add(1);
            }
        }

        critical_path_by_index[index] = critical_path_len;
        blocked_descendants_by_index[index] = blocked_descendant_count;
    }

    PriorityMetricCache {
        state_epoch,
        critical_path_by_index,
        blocked_descendants_by_index,
    }
}

fn build_exact_dag_priority_metrics_cached(
    tasks: &BTreeMap<TaskId, TaskCard>,
    cache: &TaskGraphCache,
    state_epoch: u64,
) -> Result<PriorityMetricCache, TaskLedgerError> {
    let blocks_by_index: Vec<bool> = cache
        .topological_order
        .iter()
        .map(|task_id| {
            tasks
                .get(task_id)
                .map(|task| task.status.blocks_descendants())
                .unwrap_or(false)
        })
        .collect();
    let mut critical_path_by_index = vec![1u32; cache.topological_order.len()];
    let mut descendants_by_index: Vec<BTreeSet<usize>> =
        vec![BTreeSet::new(); cache.topological_order.len()];

    for index in (0..cache.topological_order.len()).rev() {
        let mut critical_path_len = 1u32;
        let mut descendants = BTreeSet::new();

        for child_index in &cache.children_by_index[index] {
            critical_path_len =
                critical_path_len.max(critical_path_by_index[*child_index].saturating_add(1));
            descendants.insert(*child_index);
            descendants.extend(descendants_by_index[*child_index].iter().copied());
        }

        critical_path_by_index[index] = critical_path_len;
        descendants_by_index[index] = descendants;
    }

    let mut blocked_descendants_by_index = vec![0u32; cache.topological_order.len()];
    for (index, blocked_descendant_count) in blocked_descendants_by_index.iter_mut().enumerate() {
        *blocked_descendant_count = descendants_by_index[index]
            .iter()
            .filter(|descendant_index| blocks_by_index[**descendant_index])
            .count()
            .min(u32::MAX as usize) as u32;
    }

    Ok(PriorityMetricCache {
        state_epoch,
        critical_path_by_index,
        blocked_descendants_by_index,
    })
}

fn apply_priority_scores_cached_metrics(
    tasks: &mut BTreeMap<TaskId, TaskCard>,
    now_ms: u64,
    deadline_horizon_ms: u64,
    cache: &TaskGraphCache,
    metrics: &PriorityMetricCache,
) {
    if cache.topological_order_matches_task_order {
        for (index, task) in tasks.values_mut().enumerate() {
            apply_priority_score_to_task(
                task,
                metrics.critical_path_by_index[index],
                metrics.blocked_descendants_by_index[index],
                now_ms,
                deadline_horizon_ms,
            );
        }
        return;
    }

    for (index, task_id) in cache.topological_order.iter().enumerate() {
        let task = tasks
            .get_mut(task_id)
            .expect("topological order contains only known tasks");
        apply_priority_score_to_task(
            task,
            metrics.critical_path_by_index[index],
            metrics.blocked_descendants_by_index[index],
            now_ms,
            deadline_horizon_ms,
        );
    }
}

fn apply_priority_score_to_task(
    task: &mut TaskCard,
    critical_path_len: u32,
    blocked_descendant_count: u32,
    now_ms: u64,
    deadline_horizon_ms: u64,
) {
    task.critical_path_len = critical_path_len;
    task.blocked_descendant_count = blocked_descendant_count;
    task.deterministic_priority_score = compute_priority_score(
        task.critical_path_len,
        task.blocked_descendant_count,
        task.evidence_unblock_count,
        deadline_pressure_score(task.hard_deadline_ms, now_ms, deadline_horizon_ms),
    );
}

pub fn deadline_pressure_score(hard_deadline_ms: Option<u64>, now_ms: u64, horizon_ms: u64) -> u64 {
    match hard_deadline_ms {
        None => 0,
        Some(deadline_ms) if deadline_ms <= now_ms => horizon_ms,
        Some(deadline_ms) => horizon_ms.min(horizon_ms.saturating_sub(deadline_ms - now_ms)),
    }
}

pub fn compute_priority_score(
    critical_path_len: u32,
    blocked_descendant_count: u32,
    evidence_unblock_count: u32,
    deadline_pressure: u64,
) -> u64 {
    (critical_path_len as u64)
        .saturating_mul(PRIORITY_WEIGHT_CRITICAL_PATH)
        .saturating_add(
            (blocked_descendant_count as u64).saturating_mul(PRIORITY_WEIGHT_BLOCKED_DESCENDANT),
        )
        .saturating_add(
            (evidence_unblock_count as u64).saturating_mul(PRIORITY_WEIGHT_EVIDENCE_UNBLOCK),
        )
        .saturating_add(deadline_pressure)
}

fn priority_order(left: &TaskCard, right: &TaskCard) -> std::cmp::Ordering {
    right
        .deterministic_priority_score
        .cmp(&left.deterministic_priority_score)
        .then_with(|| {
            left.hard_deadline_ms
                .unwrap_or(u64::MAX)
                .cmp(&right.hard_deadline_ms.unwrap_or(u64::MAX))
        })
        .then_with(|| left.task_id.cmp(&right.task_id))
        .then_with(|| left.insertion_order.cmp(&right.insertion_order))
}

fn task_dependency_hash(dependency_ids: &[TaskId]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-task-dependency-set-v1");
    update_u64(&mut hasher, dependency_ids.len() as u64);
    for dependency_id in dependency_ids {
        update_u128(&mut hasher, *dependency_id);
    }
    *hasher.finalize().as_bytes()
}

fn task_card_state_hash(task: &TaskCard) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-task-card-state-v2");
    update_u128(&mut hasher, task.task_id);
    update_u64(&mut hasher, task.attempt_id);
    update_task_status(&mut hasher, task.status);
    hasher.update(&task_dependency_hash(&task.dependency_ids));
    update_u64(&mut hasher, task.deterministic_priority_score);
    update_optional_i64(&mut hasher, task.suggested_priority);
    update_u32(&mut hasher, task.critical_path_len);
    update_u32(&mut hasher, task.blocked_descendant_count);
    update_u32(&mut hasher, task.evidence_unblock_count);
    update_optional_u64(&mut hasher, task.hard_deadline_ms);
    update_optional_u64(&mut hasher, task.retry_not_before_ms());
    update_u64(&mut hasher, task.insertion_order);
    *hasher.finalize().as_bytes()
}

fn task_ledger_state_hash(
    state_epoch: u64,
    deadline_horizon_ms: u64,
    retry_policy: RetryPolicy,
    tasks: &BTreeMap<TaskId, TaskCard>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-task-ledger-state-v2");
    update_u64(&mut hasher, state_epoch);
    update_u64(&mut hasher, deadline_horizon_ms);
    update_u64(&mut hasher, u64::from(retry_policy.max_total_attempts));
    update_u64(&mut hasher, u64::from(retry_policy.initial_backoff_ms));
    update_u64(&mut hasher, u64::from(retry_policy.max_backoff_ms));
    update_u64(&mut hasher, tasks.len() as u64);
    for (task_id, task) in tasks {
        update_u128(&mut hasher, *task_id);
        hasher.update(&task_card_state_hash(task));
    }
    *hasher.finalize().as_bytes()
}

fn task_ready_queue_hash(
    now_ms: u64,
    state_epoch: u64,
    deadline_horizon_ms: u64,
    ready_task_ids: &[TaskId],
    tasks: &BTreeMap<TaskId, TaskCard>,
) -> Result<[u8; 32], TaskLedgerError> {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-task-ready-queue-v2");
    update_u64(&mut hasher, now_ms);
    update_u64(&mut hasher, state_epoch);
    update_u64(&mut hasher, deadline_horizon_ms);
    update_u64(&mut hasher, ready_task_ids.len() as u64);
    for task_id in ready_task_ids {
        let task = tasks
            .get(task_id)
            .ok_or(TaskLedgerError::MissingTask(*task_id))?;
        update_u128(&mut hasher, *task_id);
        update_u64(&mut hasher, task.deterministic_priority_score);
        update_optional_u64(&mut hasher, task.hard_deadline_ms);
        update_u64(&mut hasher, task.insertion_order);
        hasher.update(&task_dependency_hash(&task.dependency_ids));
        hasher.update(&task_card_state_hash(task));
    }
    Ok(*hasher.finalize().as_bytes())
}

#[allow(clippy::too_many_arguments)]
fn task_selection_proof_hash(
    now_ms: u64,
    state_epoch: u64,
    deadline_horizon_ms: u64,
    task_count: u32,
    ready_count: u32,
    selected_task_id: TaskId,
    selected_priority_score: u64,
    selected_dependency_hash: [u8; 32],
    ledger_state_hash: [u8; 32],
    ready_queue_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-task-selection-proof-v2");
    update_u64(&mut hasher, now_ms);
    update_u64(&mut hasher, state_epoch);
    update_u64(&mut hasher, deadline_horizon_ms);
    update_u32(&mut hasher, task_count);
    update_u32(&mut hasher, ready_count);
    update_u128(&mut hasher, selected_task_id);
    update_u64(&mut hasher, selected_priority_score);
    hasher.update(&selected_dependency_hash);
    hasher.update(&ledger_state_hash);
    hasher.update(&ready_queue_hash);
    *hasher.finalize().as_bytes()
}

fn update_task_status(hasher: &mut Hasher, status: TaskStatus) {
    let value = match status {
        TaskStatus::Pending => 1u8,
        TaskStatus::Ready => 2,
        TaskStatus::Admitted => 3,
        TaskStatus::Running => 4,
        TaskStatus::RetryWait => 5,
        TaskStatus::Cancelling => 6,
        TaskStatus::Done => 7,
        TaskStatus::Failed => 8,
        TaskStatus::Cancelled => 9,
        TaskStatus::TimedOut => 10,
        TaskStatus::NeedsReconciliation => 11,
    };
    hasher.update(&[value]);
}

fn update_optional_i64(hasher: &mut Hasher, value: Option<i64>) {
    match value {
        Some(value) => {
            hasher.update(&[1]);
            hasher.update(&value.to_le_bytes());
        }
        None => {
            hasher.update(&[0]);
        }
    }
}

fn update_optional_u64(hasher: &mut Hasher, value: Option<u64>) {
    match value {
        Some(value) => {
            hasher.update(&[1]);
            update_u64(hasher, value);
        }
        None => {
            hasher.update(&[0]);
        }
    }
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_u32(hasher: &mut Hasher, value: u32) {
    hasher.update(&value.to_le_bytes());
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

#[cfg(test)]
mod retry_attempt_invariant_tests {
    use super::{
        NonZeroU64, RetryPolicy, TaskCard, TaskLedger, TaskLedgerError, TaskStatus,
        task_card_state_hash,
    };

    #[test]
    fn insertion_rejects_an_attempt_above_the_total_attempt_budget() {
        let mut ledger = TaskLedger::new(60_000);
        let task = TaskCard::new(1, Vec::new(), 0, None, None).with_attempt_id(4);

        assert_eq!(
            ledger.insert_task(task),
            Err(TaskLedgerError::InvalidAttempt(1))
        );
    }

    #[test]
    fn public_attempt_setter_cannot_consume_a_retry_before_admission() {
        let mut ledger = TaskLedger::new(60_000);
        ledger
            .insert_task(TaskCard::new(1, Vec::new(), 0, None, None))
            .unwrap();
        for status in [TaskStatus::Ready, TaskStatus::Admitted, TaskStatus::Running] {
            ledger.transition_status(1, status).unwrap();
        }
        ledger.schedule_retry_wait(1, 2_000).unwrap();
        let before_hash = ledger.ledger_state_hash().unwrap();

        #[allow(deprecated)]
        let result = ledger.set_attempt_id(1, 2);

        assert_eq!(result, Err(TaskLedgerError::InvalidAttempt(1)));
        assert_eq!(ledger.task(1).map(|task| task.attempt_id), Some(1));
        assert_eq!(
            ledger.task(1).map(|task| task.status),
            Some(TaskStatus::RetryWait)
        );
        assert_eq!(ledger.ledger_state_hash().unwrap(), before_hash);
    }

    #[test]
    fn public_status_transitions_cannot_bypass_retry_admission() {
        let mut ledger = TaskLedger::new(60_000);
        ledger
            .insert_task(TaskCard::new(1, Vec::new(), 0, None, None))
            .unwrap();
        for status in [TaskStatus::Ready, TaskStatus::Admitted, TaskStatus::Running] {
            ledger.transition_status(1, status).unwrap();
        }
        ledger.schedule_retry_wait(1, 2_000).unwrap();
        let before_task = ledger.task(1).cloned().unwrap();
        let before_hash = ledger.ledger_state_hash().unwrap();

        assert!(!TaskStatus::RetryWait.can_transition_to(TaskStatus::Ready));
        assert!(matches!(
            ledger.transition_status(1, TaskStatus::Ready),
            Err(TaskLedgerError::InvalidStatusTransition {
                task_id: 1,
                from: TaskStatus::RetryWait,
                to: TaskStatus::Ready,
            })
        ));
        assert!(matches!(
            ledger.complete_task(1),
            Err(TaskLedgerError::InvalidStatusTransition {
                task_id: 1,
                from: TaskStatus::RetryWait,
                to: TaskStatus::Ready,
            })
        ));
        assert_eq!(ledger.task(1), Some(&before_task));
        assert_eq!(ledger.ledger_state_hash().unwrap(), before_hash);
    }

    #[test]
    fn task_state_hash_binds_retry_not_before_time() {
        let task = TaskCard::new(1, Vec::new(), 0, None, None);
        let original_hash = task_card_state_hash(&task);
        let mut scheduled = task.clone();
        scheduled.retry_not_before_ms = NonZeroU64::new(2_000);

        assert_ne!(task_card_state_hash(&scheduled), original_hash);
    }

    #[test]
    fn ledger_state_hash_binds_retry_policy_and_scheduled_retry() {
        let mut ledger = TaskLedger::new(60_000);
        ledger
            .insert_task(TaskCard::new(1, Vec::new(), 0, None, None))
            .unwrap();
        let original_hash = ledger.ledger_state_hash().unwrap();

        ledger.retry_policy.max_total_attempts += 1;
        assert_ne!(ledger.ledger_state_hash().unwrap(), original_hash);

        ledger.retry_policy = RetryPolicy::default();
        ledger.tasks.get_mut(&1).unwrap().retry_not_before_ms = NonZeroU64::new(2_000);
        assert_ne!(ledger.ledger_state_hash().unwrap(), original_hash);
    }

    #[test]
    fn retry_policy_uses_two_then_four_seconds_and_stops_after_three_total_attempts() {
        let policy = RetryPolicy::default();

        assert_eq!(policy.delay_after(1), Some(2_000));
        assert_eq!(policy.delay_after(2), Some(4_000));
        assert_eq!(policy.delay_after(3), None);
    }
}
