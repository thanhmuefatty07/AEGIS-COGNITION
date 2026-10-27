//! Authoritative single-node runtime and fenced lease state machine.
//!
//! Python proposes work through typed JSON, but Rust owns the task status,
//! attempt, lease and execution-lane state.  A caller receives only an opaque
//! lease token; all authoritative lease attributes are looked up in Rust.

use std::collections::BTreeMap;

use crate::resource::{
    AdmissionController, AdmissionDecision, CapacityFeedback, CooperativeAdmissionDecision,
    CooperativeAdmissionLease, CooperativeAdmissionLedger, CooperativeAdmissionRequest,
    ExecutionLane, ExecutionLaneRegistry, ResourceError, ResourceLeaseToken, ResourceRequest,
    ResourceUsageSample, WorkKind,
};
use crate::task_ledger::{TaskCard, TaskLedger, TaskLedgerError, TaskStatus};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RuntimeAdmission {
    Admitted(ResourceLeaseToken),
    Queued { position: usize, reason: String },
    Rejected { reason: ResourceError },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueuedRuntimeAdmission {
    pub task_id: u128,
    pub attempt_id: u64,
    pub admission: RuntimeAdmission,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeOutcome {
    Done,
    Failed,
    RetryWait,
    Cancelled,
    TimedOut,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum CooperativeRuntimeMode {
    #[default]
    Disabled,
    Shadow,
    Enforced,
}

/// The runtime-facing result for a cooperative admission.  The lease token
/// intentionally hides the aggregate reservation and cannot be constructed
/// by callers; Rust keeps the authoritative lease for release validation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CooperativeRuntimeAdmission {
    Admitted(Box<CooperativeLeaseToken>),
    Shadow,
}

#[derive(Clone, Eq, PartialEq)]
pub struct CooperativeLeaseToken {
    lease: CooperativeAdmissionLease,
}

impl std::fmt::Debug for CooperativeLeaseToken {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CooperativeLeaseToken")
            .field("task_id", &self.lease.task_id)
            .field("attempt_id", &self.lease.attempt_id)
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RuntimeError {
    Task(TaskLedgerError),
    Resource(ResourceError),
    TaskRequestMismatch,
    AttemptMismatch,
    InvalidTransition(TaskStatus),
    InvalidOutcome(String),
    LaneAccounting(ExecutionLane),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AttemptKind {
    Initial,
    Retry,
}

#[derive(Debug)]
pub struct AuthoritativeRuntime {
    pub ledger: TaskLedger,
    pub admission: AdmissionController,
    pub lanes: ExecutionLaneRegistry,
    pending_admissions: BTreeMap<(u128, u64), RuntimeAdmission>,
    cooperative_mode: CooperativeRuntimeMode,
    cooperative_admission: Option<CooperativeAdmissionLedger>,
}

fn constrain_request_to_task_deadline(
    request: &mut ResourceRequest,
    task_hard_deadline_ms: Option<u64>,
) {
    request.deadline.deadline_ms = match (request.deadline.deadline_ms, task_hard_deadline_ms) {
        (Some(request_deadline_ms), Some(task_deadline_ms)) => {
            Some(request_deadline_ms.min(task_deadline_ms))
        }
        (None, task_deadline_ms) => task_deadline_ms,
        (request_deadline_ms, None) => request_deadline_ms,
    };
}

impl AuthoritativeRuntime {
    pub fn new(deadline_horizon_ms: u64, profile: &crate::resource::HardwareProfile) -> Self {
        Self {
            ledger: TaskLedger::new(deadline_horizon_ms),
            admission: AdmissionController::from_hardware(profile),
            lanes: ExecutionLaneRegistry::for_profile(profile),
            pending_admissions: BTreeMap::new(),
            cooperative_mode: CooperativeRuntimeMode::Disabled,
            cooperative_admission: None,
        }
    }

    pub fn cooperative_mode(&self) -> CooperativeRuntimeMode {
        self.cooperative_mode
    }

    /// Configure resource-only cooperative admission from validated platform
    /// inventory.  The planner ledger is not retained and active leases fence
    /// reconfiguration so no admitted reservation can disappear underneath a
    /// running task.
    pub fn configure_cooperative(
        &mut self,
        mode: CooperativeRuntimeMode,
        capabilities: &[crate::placement::PlacementCapability],
        paths: &[crate::placement::PlacementTransferPath],
    ) -> Result<(), RuntimeError> {
        if self
            .cooperative_admission
            .as_ref()
            .is_some_and(|ledger| ledger.active_leases() != 0)
        {
            return Err(RuntimeError::Resource(ResourceError::ResourceUnavailable(
                "cooperative admission has active leases".to_string(),
            )));
        }

        let ledger = match mode {
            CooperativeRuntimeMode::Disabled => None,
            CooperativeRuntimeMode::Shadow | CooperativeRuntimeMode::Enforced => Some(
                CooperativeAdmissionLedger::from_inventory(capabilities, paths)
                    .map_err(RuntimeError::Resource)?,
            ),
        };
        self.cooperative_mode = mode;
        self.cooperative_admission = ledger;
        Ok(())
    }

    /// Admit a previously planned aggregate reservation without acquiring the
    /// legacy execution lane.  H1 executor queue slots are the cooperative
    /// execution gate; adding a second lane gate would double-count capacity.
    pub fn submit_cooperative(
        &mut self,
        request: CooperativeAdmissionRequest,
        now_ms: u64,
    ) -> Result<CooperativeRuntimeAdmission, RuntimeError> {
        let mode = self.cooperative_mode;
        if mode == CooperativeRuntimeMode::Disabled {
            return Err(RuntimeError::Resource(ResourceError::ResourceUnavailable(
                "cooperative admission is disabled".to_string(),
            )));
        }

        let ledger = self.cooperative_admission.as_mut().ok_or_else(|| {
            RuntimeError::Resource(ResourceError::ResourceUnavailable(
                "cooperative admission inventory is not configured".to_string(),
            ))
        })?;
        if mode == CooperativeRuntimeMode::Shadow {
            request.validate().map_err(RuntimeError::Resource)?;
            ledger
                .ensure_fits(&request.reservation)
                .map_err(RuntimeError::Resource)?;
            return Ok(CooperativeRuntimeAdmission::Shadow);
        }

        let decision = ledger
            .admit(request, now_ms)
            .map_err(RuntimeError::Resource)?;
        let lease = match decision {
            CooperativeAdmissionDecision::Admitted(lease)
            | CooperativeAdmissionDecision::AlreadyAdmitted(lease) => lease,
        };
        Ok(CooperativeRuntimeAdmission::Admitted(Box::new(
            CooperativeLeaseToken { lease },
        )))
    }

    pub fn finish_cooperative(
        &mut self,
        token: CooperativeLeaseToken,
        outcome: RuntimeOutcome,
    ) -> Result<(), RuntimeError> {
        let ledger = self.cooperative_admission.as_mut().ok_or_else(|| {
            RuntimeError::Resource(ResourceError::ResourceUnavailable(
                "cooperative admission inventory is not configured".to_string(),
            ))
        })?;
        let release = if outcome == RuntimeOutcome::Cancelled {
            ledger.cancel(&token.lease)
        } else {
            ledger.release(&token.lease)
        };
        release.map(|_| ()).map_err(RuntimeError::Resource)
    }

    pub fn cancel_cooperative(&mut self, token: CooperativeLeaseToken) -> Result<(), RuntimeError> {
        let ledger = self.cooperative_admission.as_mut().ok_or_else(|| {
            RuntimeError::Resource(ResourceError::ResourceUnavailable(
                "cooperative admission inventory is not configured".to_string(),
            ))
        })?;
        ledger
            .cancel(&token.lease)
            .map(|_| ())
            .map_err(RuntimeError::Resource)
    }

    pub fn submit(
        &mut self,
        task: TaskCard,
        mut request: ResourceRequest,
        now_ms: u64,
    ) -> Result<RuntimeAdmission, RuntimeError> {
        if task.task_id != request.task_id {
            return Err(RuntimeError::TaskRequestMismatch);
        }
        if task.attempt_id != request.attempt_id {
            return Err(RuntimeError::AttemptMismatch);
        }
        if task.status != TaskStatus::Pending {
            return Err(RuntimeError::InvalidTransition(task.status));
        }
        constrain_request_to_task_deadline(&mut request, task.hard_deadline_ms);
        self.ledger.insert_task(task).map_err(RuntimeError::Task)?;
        self.ledger
            .transition_status(request.task_id, TaskStatus::Ready)
            .map_err(RuntimeError::Task)?;
        self.admit_existing(request, now_ms, AttemptKind::Initial)
    }

    /// Start or re-enter one retry attempt identified by its task and attempt IDs.
    /// A queued attempt remains idempotent; re-entering after a retained rejection
    /// acknowledges that result and makes a fresh admission request. Use
    /// `poll_queued` to retrieve an outcome retained for this task by another poll.
    pub fn retry(
        &mut self,
        task_id: u128,
        mut request: ResourceRequest,
        now_ms: u64,
    ) -> Result<RuntimeAdmission, RuntimeError> {
        if request.task_id != task_id {
            return Err(RuntimeError::TaskRequestMismatch);
        }
        let task = self
            .ledger
            .task(task_id)
            .ok_or(RuntimeError::Task(TaskLedgerError::MissingTask(task_id)))?;
        if task.status != TaskStatus::RetryWait {
            return Err(RuntimeError::InvalidTransition(task.status));
        }
        if task.attempt_id.checked_add(1) != Some(request.attempt_id) {
            return Err(RuntimeError::AttemptMismatch);
        }
        let hard_deadline_ms = task.hard_deadline_ms;
        constrain_request_to_task_deadline(&mut request, hard_deadline_ms);
        let key = (task_id, request.attempt_id);
        if matches!(
            self.pending_admissions.get(&key),
            Some(RuntimeAdmission::Rejected { .. })
        ) {
            // The same caller is re-entering this attempt, so consume the old
            // terminal result before evaluating the new request.
            self.pending_admissions.remove(&key);
        }
        // An accepted queue entry owns this attempt until it is consumed or canceled.
        if let Some((position, queued)) = self.admission.queued_attempt(task_id, request.attempt_id)
        {
            if queued == &request {
                self.pending_admissions.remove(&key);
                return Ok(RuntimeAdmission::Queued {
                    position,
                    reason: "retry attempt is already queued".to_string(),
                });
            }
            return Err(RuntimeError::Resource(ResourceError::InvalidRequest(
                "queued retry attempt already has a different resource request".to_string(),
            )));
        }
        // A queued notification without a live queue entry is stale; this call
        // consumes it and proceeds with the current request.
        self.pending_admissions.remove(&key);
        self.admit_existing(request, now_ms, AttemptKind::Retry)
    }

    fn admit_existing(
        &mut self,
        request: ResourceRequest,
        now_ms: u64,
        attempt_kind: AttemptKind,
    ) -> Result<RuntimeAdmission, RuntimeError> {
        let lane = lane_for_work_kind(request.work_kind);
        let task = self.ledger.task(request.task_id).ok_or(RuntimeError::Task(
            TaskLedgerError::MissingTask(request.task_id),
        ))?;
        match attempt_kind {
            AttemptKind::Initial => {
                if task.status != TaskStatus::Ready {
                    return Err(RuntimeError::InvalidTransition(task.status));
                }
                if request.attempt_id != task.attempt_id {
                    return Err(RuntimeError::AttemptMismatch);
                }
                self.ledger
                    .validate_status_transition(request.task_id, TaskStatus::Admitted)
                    .map_err(map_transition_error)?;
                if !TaskStatus::Admitted.can_transition_to(TaskStatus::Running) {
                    return Err(RuntimeError::InvalidTransition(TaskStatus::Admitted));
                }
            }
            AttemptKind::Retry => {
                if task.status != TaskStatus::RetryWait {
                    return Err(RuntimeError::InvalidTransition(task.status));
                }
                if task.attempt_id.checked_add(1) != Some(request.attempt_id) {
                    return Err(RuntimeError::AttemptMismatch);
                }
                if task
                    .hard_deadline_ms
                    .is_some_and(|deadline_ms| now_ms >= deadline_ms)
                {
                    return Ok(RuntimeAdmission::Rejected {
                        reason: ResourceError::DeadlineExceeded,
                    });
                }
                if task
                    .retry_not_before_ms()
                    .is_none_or(|not_before_ms| now_ms < not_before_ms)
                {
                    return Ok(RuntimeAdmission::Rejected {
                        reason: ResourceError::ResourceUnavailable(
                            "retry backoff has not elapsed".to_string(),
                        ),
                    });
                }
                self.ledger
                    .validate_retry_start(request.task_id, request.attempt_id, now_ms)
                    .map_err(RuntimeError::Task)?;
            }
        }
        if !self.lanes.try_acquire(lane) {
            let position = self
                .admission
                .enqueue(request, now_ms)
                .map_err(RuntimeError::Resource)?;
            return Ok(RuntimeAdmission::Queued {
                position,
                reason: "execution lane at bounded capacity".to_string(),
            });
        }
        let decision = self.admission.admit(request, now_ms);
        match decision {
            AdmissionDecision::Admitted(lease) => {
                let transition = match attempt_kind {
                    AttemptKind::Initial => self
                        .ledger
                        .transition_status(lease.task_id, TaskStatus::Admitted)
                        .and_then(|_| {
                            self.ledger
                                .transition_status(lease.task_id, TaskStatus::Running)
                        }),
                    AttemptKind::Retry => {
                        self.ledger
                            .start_retry_attempt(lease.task_id, lease.attempt_id, now_ms)
                    }
                };
                if let Err(error) = transition {
                    let _ = self.admission.release(lease.lease_id);
                    let _ = self.lanes.release(lane);
                    return Err(RuntimeError::Task(error));
                }
                Ok(RuntimeAdmission::Admitted(ResourceLeaseToken::from_lease(
                    &lease,
                )))
            }
            AdmissionDecision::Queued { position, reason } => {
                let _ = self.lanes.release(lane);
                Ok(RuntimeAdmission::Queued { position, reason })
            }
            AdmissionDecision::Rejected { reason } => {
                let _ = self.lanes.release(lane);
                Ok(RuntimeAdmission::Rejected { reason })
            }
        }
    }

    pub fn finish(
        &mut self,
        token: ResourceLeaseToken,
        outcome: RuntimeOutcome,
    ) -> Result<(), RuntimeError> {
        self.finish_at(
            token,
            outcome,
            crate::resource::unix_time_millis_for_runtime(),
        )
    }

    fn finish_at(
        &mut self,
        token: ResourceLeaseToken,
        outcome: RuntimeOutcome,
        now_ms: u64,
    ) -> Result<(), RuntimeError> {
        let outcome = if outcome != RuntimeOutcome::Cancelled
            && self
                .admission
                .inspect_token(&token)
                .map_err(RuntimeError::Resource)?
                .is_expired(now_ms)
        {
            RuntimeOutcome::TimedOut
        } else {
            outcome
        };
        if outcome == RuntimeOutcome::Cancelled {
            return self.cancel_and_release(token);
        }
        let (target, retry_not_before_ms) = match outcome {
            RuntimeOutcome::Done => (TaskStatus::Done, None),
            RuntimeOutcome::Failed => (TaskStatus::Failed, None),
            RuntimeOutcome::RetryWait => {
                let task_id = self
                    .admission
                    .inspect_token(&token)
                    .map_err(RuntimeError::Resource)?
                    .task_id;
                let hard_deadline_ms = self
                    .ledger
                    .task(task_id)
                    .ok_or(RuntimeError::Task(TaskLedgerError::MissingTask(task_id)))?
                    .hard_deadline_ms;
                let not_before_ms = self
                    .ledger
                    .retry_delay_after_current_attempt(task_id)
                    .and_then(|delay_ms| now_ms.checked_add(delay_ms))
                    .filter(|due_ms| hard_deadline_ms.is_none_or(|deadline| *due_ms < deadline));
                match not_before_ms {
                    Some(not_before_ms) => (TaskStatus::RetryWait, Some(not_before_ms)),
                    None => (TaskStatus::Failed, None),
                }
            }
            RuntimeOutcome::TimedOut => (TaskStatus::TimedOut, None),
            RuntimeOutcome::Cancelled => unreachable!("cancelled is handled above"),
        };
        self.release_and_transition(token, target, retry_not_before_ms)
    }

    /// Re-attempt queued requests in FIFO order after capacity or lane state
    /// changes. Queue ownership remains in Rust; callers receive only coarse
    /// outcomes and opaque tokens.
    pub fn drain_queued(&mut self, now_ms: u64) -> Vec<QueuedRuntimeAdmission> {
        let queued = self.admission.queued();
        let mut results = Vec::with_capacity(queued);
        for _ in 0..queued {
            let Some(request) = self.admission.peek_queued() else {
                break;
            };
            let task_id = request.task_id;
            let attempt_id = request.attempt_id;
            let attempt_kind = match self.queued_attempt_kind(request) {
                Ok(attempt_kind) => attempt_kind,
                Err(error) => {
                    let Some(_request) = self.admission.pop_queued() else {
                        break;
                    };
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        attempt_id,
                        admission: RuntimeAdmission::Rejected {
                            reason: ResourceError::ResourceUnavailable(format!(
                                "queued task state could not be restored: {error:?}"
                            )),
                        },
                    });
                    continue;
                }
            };
            if attempt_kind == AttemptKind::Retry {
                let task = self
                    .ledger
                    .task(task_id)
                    .expect("queued retry task was validated above");
                if task
                    .hard_deadline_ms
                    .is_some_and(|deadline_ms| now_ms >= deadline_ms)
                {
                    let Some(_request) = self.admission.pop_queued() else {
                        break;
                    };
                    let transition = self.ledger.transition_status(task_id, TaskStatus::TimedOut);
                    let reason = transition.map_or_else(
                        |error| {
                            ResourceError::ResourceUnavailable(format!(
                                "expired queued retry could not be timed out: {error:?}"
                            ))
                        },
                        |_| ResourceError::DeadlineExceeded,
                    );
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        attempt_id,
                        admission: RuntimeAdmission::Rejected { reason },
                    });
                    continue;
                }
                if task
                    .retry_not_before_ms()
                    .is_none_or(|not_before_ms| now_ms < not_before_ms)
                {
                    break;
                }
            }

            let Some(request) = self.admission.pop_queued() else {
                break;
            };
            let admission = self
                .admit_existing(request, now_ms, attempt_kind)
                .unwrap_or_else(|error| RuntimeAdmission::Rejected {
                    reason: ResourceError::ResourceUnavailable(format!(
                        "queued task state could not be restored: {error:?}"
                    )),
                });
            let still_queued = matches!(admission, RuntimeAdmission::Queued { .. });
            results.push(QueuedRuntimeAdmission {
                task_id,
                attempt_id,
                admission,
            });
            if still_queued {
                break;
            }
        }
        results
    }

    fn queued_attempt_kind(&self, request: &ResourceRequest) -> Result<AttemptKind, RuntimeError> {
        let task = self.ledger.task(request.task_id).ok_or(RuntimeError::Task(
            TaskLedgerError::MissingTask(request.task_id),
        ))?;
        match task.status {
            TaskStatus::Ready if request.attempt_id == task.attempt_id => Ok(AttemptKind::Initial),
            TaskStatus::RetryWait if task.attempt_id.checked_add(1) == Some(request.attempt_id) => {
                Ok(AttemptKind::Retry)
            }
            TaskStatus::Ready | TaskStatus::RetryWait => Err(RuntimeError::AttemptMismatch),
            status => Err(RuntimeError::InvalidTransition(status)),
        }
    }

    /// Poll the bounded queue for one task. Other tasks admitted by the same
    /// drain are retained until their owners poll them, so no lease is lost.
    pub fn poll_queued(
        &mut self,
        task_id: u128,
        attempt_id: u64,
        now_ms: u64,
    ) -> Option<RuntimeAdmission> {
        let key = (task_id, attempt_id);
        if let Some(admission) = self.pending_admissions.remove(&key) {
            return Some(admission);
        }

        let mut target = None;
        for result in self.drain_queued(now_ms) {
            let result_key = (result.task_id, result.attempt_id);
            if result_key == key && target.is_none() {
                target = Some(result.admission);
            } else {
                self.pending_admissions.insert(result_key, result.admission);
            }
        }
        target
    }

    /// Cancel a task that is still queued. An admitted result is never
    /// cancelled through this method; its owner must use the lease token.
    pub fn cancel_queued(&mut self, task_id: u128, attempt_id: u64) -> Result<bool, RuntimeError> {
        if matches!(
            self.pending_admissions.get(&(task_id, attempt_id)),
            Some(RuntimeAdmission::Admitted(_))
        ) {
            return Ok(false);
        }
        let task = self
            .ledger
            .task(task_id)
            .ok_or(RuntimeError::Task(TaskLedgerError::MissingTask(task_id)))?;
        let is_initial = task.status == TaskStatus::Ready && task.attempt_id == attempt_id;
        let is_retry = task.status == TaskStatus::RetryWait
            && task.attempt_id.checked_add(1) == Some(attempt_id);
        if !is_initial && !is_retry {
            if task.attempt_id == attempt_id {
                return Ok(false);
            }
            return Err(RuntimeError::AttemptMismatch);
        }
        if !self.admission.remove_queued(task_id, attempt_id) {
            return Ok(false);
        }
        self.pending_admissions.remove(&(task_id, attempt_id));
        self.ledger
            .transition_status(task_id, TaskStatus::Cancelled)
            .map(|_| true)
            .map_err(RuntimeError::Task)
    }

    /// Release leases that crossed their deadline and preserve the explicit
    /// `TimedOut` task state. Returns the number of leases reclaimed.
    pub fn reap_expired(&mut self, now_ms: u64) -> usize {
        let tokens = self.admission.expired_tokens(now_ms);
        let mut reclaimed = 0;
        for token in tokens {
            if self
                .finish_at(token, RuntimeOutcome::TimedOut, now_ms)
                .is_ok()
            {
                reclaimed += 1;
            }
        }
        reclaimed
    }

    pub fn observe_resource_sample(&mut self, sample: &ResourceUsageSample) -> CapacityFeedback {
        self.admission.capacity_feedback(sample)
    }

    #[cfg(test)]
    fn begin_cancellation(&mut self, token: &ResourceLeaseToken) -> Result<(), RuntimeError> {
        let lease = self
            .admission
            .inspect_token(token)
            .map_err(RuntimeError::Resource)?;
        self.ledger
            .validate_status_transition(lease.task_id, TaskStatus::Cancelling)
            .map_err(|error| match error {
                TaskLedgerError::InvalidStatusTransition { from, .. } => {
                    RuntimeError::InvalidTransition(from)
                }
                other => RuntimeError::Task(other),
            })?;
        self.ledger
            .transition_status(lease.task_id, TaskStatus::Cancelling)
            .map_err(RuntimeError::Task)
    }

    fn cancel_and_release(&mut self, token: ResourceLeaseToken) -> Result<(), RuntimeError> {
        // Validate both transitions and both resource counters before exposing
        // Cancelling. If any precondition fails, the call is side-effect free.
        let lease = self
            .admission
            .inspect_token(&token)
            .map_err(RuntimeError::Resource)?
            .clone();
        self.ledger
            .validate_status_transition(lease.task_id, TaskStatus::Cancelling)
            .map_err(map_transition_error)?;
        if !TaskStatus::Cancelling.can_transition_to(TaskStatus::Cancelled) {
            return Err(RuntimeError::InvalidTransition(TaskStatus::Cancelling));
        }
        let lane = lane_for_work_kind(lease.work_kind);
        if !self.lanes.can_release(lane) {
            return Err(RuntimeError::LaneAccounting(lane));
        }

        self.ledger
            .transition_status(lease.task_id, TaskStatus::Cancelling)
            .map_err(RuntimeError::Task)?;
        let release_result = self
            .admission
            .release_fenced(&token)
            .map_err(RuntimeError::Resource)
            .and_then(|released| {
                debug_assert_eq!(released.lease_id, lease.lease_id);
                if self.lanes.release(lane) {
                    Ok(())
                } else {
                    Err(RuntimeError::LaneAccounting(lane))
                }
            });
        match release_result {
            Ok(()) => self
                .ledger
                .transition_status(lease.task_id, TaskStatus::Cancelled)
                .map_err(RuntimeError::Task),
            Err(error) => {
                let _ = self
                    .ledger
                    .transition_status(lease.task_id, TaskStatus::NeedsReconciliation);
                Err(error)
            }
        }
    }

    fn release_and_transition(
        &mut self,
        token: ResourceLeaseToken,
        target: TaskStatus,
        retry_not_before_ms: Option<u64>,
    ) -> Result<(), RuntimeError> {
        // Validate every mutable subsystem before changing any of them. This
        // makes duplicate, stale, and forged completion calls side-effect free.
        let lease = self
            .admission
            .inspect_token(&token)
            .map_err(RuntimeError::Resource)?
            .clone();
        let validation = if let Some(not_before_ms) = retry_not_before_ms {
            self.ledger
                .validate_retry_wait(lease.task_id, not_before_ms)
        } else {
            self.ledger
                .validate_status_transition(lease.task_id, target)
        };
        validation.map_err(|error| match error {
            TaskLedgerError::InvalidStatusTransition { from, .. } => {
                RuntimeError::InvalidTransition(from)
            }
            other => RuntimeError::Task(other),
        })?;
        let lane = lane_for_work_kind(lease.work_kind);
        if !self.lanes.can_release(lane) {
            return Err(RuntimeError::LaneAccounting(lane));
        }

        let released = self
            .admission
            .release_fenced(&token)
            .map_err(RuntimeError::Resource)?;
        debug_assert_eq!(released.lease_id, lease.lease_id);
        if !self.lanes.release(lane) {
            return Err(RuntimeError::LaneAccounting(lane));
        }
        if let Some(not_before_ms) = retry_not_before_ms {
            self.ledger
                .schedule_retry_wait(lease.task_id, not_before_ms)
                .map_err(RuntimeError::Task)
        } else {
            self.ledger
                .transition_status(lease.task_id, target)
                .map_err(RuntimeError::Task)
        }
    }
}

fn map_transition_error(error: TaskLedgerError) -> RuntimeError {
    match error {
        TaskLedgerError::InvalidStatusTransition { from, .. } => {
            RuntimeError::InvalidTransition(from)
        }
        other => RuntimeError::Task(other),
    }
}

fn lane_for_work_kind(kind: WorkKind) -> ExecutionLane {
    match kind {
        WorkKind::NativeTask => ExecutionLane::Cpu,
        WorkKind::PythonCognition | WorkKind::Agent => ExecutionLane::PythonCognition,
        WorkKind::Tool => ExecutionLane::Untrusted,
        WorkKind::Accelerator => ExecutionLane::Accelerator,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resource::{CpuRequest, MemoryRequest, ResourceError, ResourceLeaseToken};

    fn cooperative_inventory() -> Vec<crate::placement::PlacementCapability> {
        vec![
            crate::placement::PlacementCapability {
                id: "cpu-0".to_string(),
                domain: crate::placement::PlacementDomain::Cpu,
                usable_bytes: Some(1024),
                compute_units_per_us: Some(1),
                bandwidth_bytes_per_s: None,
                latency_us: Some(1),
                queue_depth: 0,
                queue_capacity: Some(2),
                pressure: false,
                local_only: true,
                confidence: crate::placement::PlacementConfidence::Measured,
                accelerator_kind: None,
                backend: None,
                vendor: None,
                capabilities: Vec::new(),
                transfer_paths: Vec::new(),
            },
            crate::placement::PlacementCapability {
                id: "ram-0".to_string(),
                domain: crate::placement::PlacementDomain::HostMemory,
                usable_bytes: Some(4096),
                compute_units_per_us: None,
                bandwidth_bytes_per_s: None,
                latency_us: Some(1),
                queue_depth: 0,
                queue_capacity: Some(2),
                pressure: false,
                local_only: true,
                confidence: crate::placement::PlacementConfidence::Measured,
                accelerator_kind: None,
                backend: None,
                vendor: None,
                capabilities: Vec::new(),
                transfer_paths: Vec::new(),
            },
        ]
    }

    fn cooperative_request(
        task_id: u128,
        attempt_id: u64,
        plan_digest: &str,
    ) -> CooperativeAdmissionRequest {
        let mut reservation = crate::placement::CooperativeReservation::default();
        reservation
            .executor_queue_slots
            .insert("cpu-0".to_string(), 1);
        reservation
            .executor_memory_bytes
            .insert("cpu-0".to_string(), 128);
        CooperativeAdmissionRequest {
            schema: crate::resource::COOPERATIVE_ADMISSION_SCHEMA_V1.to_string(),
            task_id,
            attempt_id,
            plan_digest: plan_digest.to_string(),
            reservation,
            spill_paths: BTreeMap::new(),
        }
    }

    fn runtime() -> AuthoritativeRuntime {
        let profile = crate::resource::HardwareProfile::probe();
        AuthoritativeRuntime::new(60_000, &profile)
    }

    fn single_slot_runtime(queue_limit: usize) -> AuthoritativeRuntime {
        let profile = crate::resource::HardwareProfile::probe();
        let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
        runtime.admission = AdmissionController::new(
            crate::resource::GrantedResources {
                cpu_threads: 1,
                host_memory_bytes: 1024,
                accelerator_memory_bytes: 0,
                io_in_flight: 1,
                process_limit: 1,
                thread_limit: 1,
                fd_limit: 256,
            },
            queue_limit,
        );
        runtime
    }

    fn request(task_id: u128, attempt_id: u64) -> ResourceRequest {
        let mut request = ResourceRequest::minimal(task_id, WorkKind::NativeTask);
        request.attempt_id = attempt_id;
        request.cpu = CpuRequest {
            min_threads: 1,
            max_threads: 1,
        };
        request.host_memory = MemoryRequest { bytes: 1 };
        request
    }

    #[test]
    fn task_ledger_and_admission_move_together() {
        let mut runtime = runtime();
        let task = TaskCard::new(1, vec![], 0, None, None);
        let result = runtime
            .submit(task, request(1, 1), 1)
            .expect("submit should be valid");
        let token = match result {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted task, got {other:?}"),
        };
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Running)
        );
        runtime
            .finish(token, RuntimeOutcome::Done)
            .expect("completion should release resources");
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Done)
        );
    }

    #[test]
    fn forged_or_duplicate_token_is_side_effect_free() {
        let mut runtime = runtime();
        let task = TaskCard::new(1, vec![], 0, None, None);
        let token = match runtime.submit(task, request(1, 1), 1).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admission, got {other:?}"),
        };
        let before_lane = runtime.lanes.clone();
        let mut forged = token.clone();
        forged.generation = forged.generation.saturating_add(1);
        assert!(matches!(
            runtime.finish(forged, RuntimeOutcome::Done),
            Err(RuntimeError::Resource(ResourceError::LeaseFenced(1)))
        ));
        assert_eq!(runtime.lanes, before_lane);
        runtime.finish(token.clone(), RuntimeOutcome::Done).unwrap();
        let after = runtime.lanes.clone();
        assert!(matches!(
            runtime.finish(token, RuntimeOutcome::Done),
            Err(RuntimeError::Resource(ResourceError::UnknownLease(1)))
        ));
        assert_eq!(runtime.lanes, after);
    }

    #[test]
    fn state_machine_supports_retry_and_fenced_attempts() {
        let mut runtime = runtime();
        let started_at_ms = 1;
        let task = TaskCard::new(1, vec![], 0, None, None);
        let token = match runtime.submit(task, request(1, 1), started_at_ms).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admission, got {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::RetryWait, started_at_ms)
            .unwrap();
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::RetryWait)
        );
        let retry_started_at_ms = started_at_ms + 2_000;
        let retry_token = match runtime
            .retry(1, request(1, 2), retry_started_at_ms)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected retry admission, got {other:?}"),
        };
        assert_eq!(retry_token.attempt_id, 2);
        runtime
            .finish_at(
                retry_token,
                RuntimeOutcome::TimedOut,
                retry_started_at_ms + 1,
            )
            .unwrap();
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::TimedOut)
        );
    }

    #[test]
    fn retry_before_backoff_is_rejected_without_mutating_runtime_state() {
        let mut runtime = runtime();
        let retry_started_at_ms = crate::resource::unix_time_millis_for_runtime();
        let task = TaskCard::new(1, vec![], 0, None, None);
        let token = match runtime
            .submit(task, request(1, 1), retry_started_at_ms)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::RetryWait, retry_started_at_ms)
            .unwrap();

        let before_task = runtime.ledger.task(1).cloned().unwrap();
        let before_ledger_hash = runtime.ledger.ledger_state_hash_for_test().unwrap();
        let before_lanes = runtime.lanes.clone();
        let before_pending_admissions = runtime.pending_admissions.clone();
        let before_used_resources = runtime.admission.used();
        let before_active_leases = runtime.admission.active_leases();
        let before_queued = runtime.admission.queued();
        let result = runtime.retry(1, request(1, 2), retry_started_at_ms);

        assert!(
            matches!(result, Ok(RuntimeAdmission::Rejected { .. })),
            "a retry requested before its 2-second not-before time must be rejected, got {result:?}"
        );
        assert_eq!(runtime.ledger.task(1), Some(&before_task));
        assert_eq!(
            runtime.ledger.ledger_state_hash_for_test().unwrap(),
            before_ledger_hash
        );
        assert_eq!(runtime.lanes, before_lanes);
        assert_eq!(runtime.pending_admissions, before_pending_admissions);
        assert_eq!(runtime.admission.used(), before_used_resources);
        assert_eq!(runtime.admission.active_leases(), before_active_leases);
        assert_eq!(runtime.admission.queued(), before_queued);
    }

    #[test]
    fn retry_rejects_skipped_attempt_ids_without_mutating_runtime_state() {
        let mut runtime = runtime();
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                now_ms,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::RetryWait, now_ms)
            .unwrap();

        let before_task = runtime.ledger.task(1).cloned().unwrap();
        let before_ledger_hash = runtime.ledger.ledger_state_hash_for_test().unwrap();
        let before_lanes = runtime.lanes.clone();
        let before_pending_admissions = runtime.pending_admissions.clone();
        let before_used_resources = runtime.admission.used();
        let before_active_leases = runtime.admission.active_leases();
        let before_queued = runtime.admission.queued();
        let result = runtime.retry(1, request(1, 3), now_ms + 10_000);

        assert_eq!(result, Err(RuntimeError::AttemptMismatch));
        assert_eq!(runtime.ledger.task(1), Some(&before_task));
        assert_eq!(
            runtime.ledger.ledger_state_hash_for_test().unwrap(),
            before_ledger_hash
        );
        assert_eq!(runtime.lanes, before_lanes);
        assert_eq!(runtime.pending_admissions, before_pending_admissions);
        assert_eq!(runtime.admission.used(), before_used_resources);
        assert_eq!(runtime.admission.active_leases(), before_active_leases);
        assert_eq!(runtime.admission.queued(), before_queued);
    }

    #[test]
    fn rejected_retry_admission_does_not_consume_the_execution_attempt() {
        let mut runtime = runtime();
        runtime.admission = crate::resource::AdmissionController::new(
            crate::resource::GrantedResources {
                cpu_threads: 4,
                host_memory_bytes: 4_096,
                accelerator_memory_bytes: 0,
                io_in_flight: 4,
                process_limit: 4,
                thread_limit: 4,
                fd_limit: 256,
            },
            0,
        );
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                now_ms,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::RetryWait, now_ms)
            .unwrap();

        let before_task = runtime.ledger.task(1).cloned().unwrap();
        let before_ledger_hash = runtime.ledger.ledger_state_hash_for_test().unwrap();
        let before_lanes = runtime.lanes.clone();
        let before_pending_admissions = runtime.pending_admissions.clone();
        let before_used_resources = runtime.admission.used();
        let before_active_leases = runtime.admission.active_leases();
        let before_queued = runtime.admission.queued();
        let mut impossible_request = request(1, 2);
        impossible_request.host_memory.bytes = u64::MAX;
        let result = runtime.retry(1, impossible_request, now_ms + 10_000);

        assert!(
            matches!(result, Ok(RuntimeAdmission::Rejected { .. })),
            "a resource-rejected retry must not be reported as admitted or queued, got {result:?}"
        );
        assert_eq!(runtime.ledger.task(1), Some(&before_task));
        assert_eq!(
            runtime.ledger.ledger_state_hash_for_test().unwrap(),
            before_ledger_hash
        );
        assert_eq!(runtime.lanes, before_lanes);
        assert_eq!(runtime.pending_admissions, before_pending_admissions);
        assert_eq!(runtime.admission.used(), before_used_resources);
        assert_eq!(runtime.admission.active_leases(), before_active_leases);
        assert_eq!(runtime.admission.queued(), before_queued);
    }

    #[test]
    fn queued_retry_before_not_before_keeps_queue_and_state_unchanged() {
        let mut runtime = single_slot_runtime(2);
        let first_token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                10_000,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(first_token, RuntimeOutcome::RetryWait, 10_000)
            .unwrap();
        let retry_not_before_ms = runtime
            .ledger
            .task(1)
            .and_then(TaskCard::retry_not_before_ms)
            .unwrap();
        let blocker_token = match runtime
            .submit(
                TaskCard::new(2, vec![], 0, None, None),
                request(2, 1),
                10_001,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected capacity blocker admission, got {other:?}"),
        };
        assert!(matches!(
            runtime
                .retry(1, request(1, 2), retry_not_before_ms)
                .unwrap(),
            RuntimeAdmission::Queued { .. }
        ));

        let before_task = runtime.ledger.task(1).cloned().unwrap();
        let before_ledger_hash = runtime.ledger.ledger_state_hash_for_test().unwrap();
        let before_lanes = runtime.lanes.clone();
        let before_pending_admissions = runtime.pending_admissions.clone();
        let before_used_resources = runtime.admission.used();
        let before_active_leases = runtime.admission.active_leases();
        let before_queued = runtime.admission.queued();
        let before_queued_request = runtime.admission.peek_queued().cloned().unwrap();

        assert!(runtime.drain_queued(retry_not_before_ms - 1).is_empty());
        assert_eq!(runtime.admission.queued(), before_queued);
        assert_eq!(
            runtime.admission.peek_queued(),
            Some(&before_queued_request)
        );
        assert_eq!(runtime.ledger.task(1), Some(&before_task));
        assert_eq!(
            runtime.ledger.ledger_state_hash_for_test().unwrap(),
            before_ledger_hash
        );
        assert_eq!(runtime.lanes, before_lanes);
        assert_eq!(runtime.pending_admissions, before_pending_admissions);
        assert_eq!(runtime.admission.used(), before_used_resources);
        assert_eq!(runtime.admission.active_leases(), before_active_leases);

        runtime
            .finish_at(blocker_token, RuntimeOutcome::Done, retry_not_before_ms)
            .unwrap();
        let drained = runtime.drain_queued(retry_not_before_ms);
        let retry_token = match drained.as_slice() {
            [
                QueuedRuntimeAdmission {
                    task_id: 1,
                    attempt_id: 2,
                    admission: RuntimeAdmission::Admitted(token),
                },
            ] => token.clone(),
            other => panic!("expected retained retry admission at its due time, got {other:?}"),
        };
        assert_eq!(runtime.ledger.task(1).map(|task| task.attempt_id), Some(2));
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Running)
        );
        runtime
            .finish_at(retry_token, RuntimeOutcome::Done, retry_not_before_ms + 1)
            .unwrap();
    }

    #[test]
    fn duplicate_queued_retry_is_idempotent_and_poll_keeps_first_result() {
        let mut runtime = single_slot_runtime(2);
        let first_token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                10_000,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected first task admission, got {other:?}"),
        };
        runtime
            .finish_at(first_token, RuntimeOutcome::RetryWait, 10_000)
            .unwrap();
        let retry_at_ms = runtime
            .ledger
            .task(1)
            .and_then(TaskCard::retry_not_before_ms)
            .unwrap();
        let blocker_token = match runtime
            .submit(
                TaskCard::new(2, vec![], 0, None, None),
                request(2, 1),
                10_001,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected capacity blocker admission, got {other:?}"),
        };

        let first_retry = runtime.retry(1, request(1, 2), retry_at_ms);
        let unrelated_poll = runtime.poll_queued(99, 1, retry_at_ms);
        let duplicate_retry = runtime.retry(1, request(1, 2), retry_at_ms);
        let mut conflicting_request = request(1, 2);
        conflicting_request.host_memory.bytes = 2;
        let conflicting_retry = runtime.retry(1, conflicting_request, retry_at_ms);
        let position = |result: &Result<RuntimeAdmission, RuntimeError>| match result {
            Ok(RuntimeAdmission::Queued { position, .. }) => Some(*position),
            _ => None,
        };
        let first_position = position(&first_retry);
        let duplicate_position = position(&duplicate_retry);
        let conflicting_retry_rejected = matches!(
            &conflicting_retry,
            Err(RuntimeError::Resource(ResourceError::InvalidRequest(_)))
        );
        let queued_before_release = runtime.admission.queued();

        runtime
            .finish_at(blocker_token, RuntimeOutcome::Done, retry_at_ms)
            .unwrap();
        let first_poll = runtime.poll_queued(1, 2, retry_at_ms);
        let repeated_poll = runtime.poll_queued(1, 2, retry_at_ms);

        assert!(
            first_position == Some(1)
                && unrelated_poll.is_none()
                && duplicate_position == Some(1)
                && conflicting_retry_rejected
                && queued_before_release == 1
                && matches!(first_poll.as_ref(), Some(RuntimeAdmission::Admitted(_)))
                && repeated_poll.is_none(),
            "same retry key must share one queue entry, clear retained queue notices, reject changed requests, and retain one result; first={first_retry:?}, unrelated_poll={unrelated_poll:?}, duplicate={duplicate_retry:?}, conflict={conflicting_retry:?}, queued={queued_before_release}, first_poll={first_poll:?}, repeated_poll={repeated_poll:?}"
        );
    }

    #[test]
    fn retry_reentry_consumes_a_pending_rejection_before_new_admission() {
        let mut runtime = single_slot_runtime(2);
        let first_token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                10_000,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(first_token, RuntimeOutcome::RetryWait, 10_000)
            .unwrap();
        let retry_at_ms = runtime
            .ledger
            .task(1)
            .and_then(TaskCard::retry_not_before_ms)
            .unwrap();

        let lane = lane_for_work_kind(WorkKind::NativeTask);
        let mut held_lanes = 0;
        while runtime.lanes.try_acquire(lane) {
            held_lanes += 1;
        }
        assert!(
            held_lanes > 0,
            "the native lane must have positive capacity"
        );

        let mut expiring_retry = request(1, 2);
        expiring_retry.deadline.deadline_ms = Some(retry_at_ms.saturating_add(1));
        assert!(matches!(
            runtime.retry(1, expiring_retry, retry_at_ms).unwrap(),
            RuntimeAdmission::Queued { .. }
        ));
        for _ in 0..held_lanes {
            assert!(runtime.lanes.release(lane));
        }

        let poll_time_ms = retry_at_ms.saturating_add(2);
        assert!(runtime.poll_queued(99, 1, poll_time_ms).is_none());
        let retry_reentry = runtime.retry(1, request(1, 2), poll_time_ms).unwrap();
        let stale_result = runtime.poll_queued(1, 2, poll_time_ms);
        assert!(
            matches!(&retry_reentry, RuntimeAdmission::Admitted(_)) && stale_result.is_none(),
            "re-entering a rejected attempt must consume its old result and return only the new admission; retry={retry_reentry:?}, stale_poll={stale_result:?}"
        );
        assert_eq!(runtime.admission.active_leases(), 1);
    }

    #[test]
    fn cancelling_queued_retry_does_not_consume_an_attempt() {
        let mut runtime = runtime();
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                now_ms,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime.finish(token, RuntimeOutcome::RetryWait).unwrap();

        let lane = lane_for_work_kind(WorkKind::NativeTask);
        let mut held_lanes = 0;
        while runtime.lanes.try_acquire(lane) {
            held_lanes += 1;
        }
        assert!(
            held_lanes > 0,
            "the native lane must have positive capacity"
        );
        let retry_now_ms = crate::resource::unix_time_millis_for_runtime() + 10_000;
        assert!(matches!(
            runtime.retry(1, request(1, 2), retry_now_ms).unwrap(),
            RuntimeAdmission::Queued { .. }
        ));

        assert!(runtime.cancel_queued(1, 2).unwrap());

        assert_eq!(runtime.ledger.task(1).map(|task| task.attempt_id), Some(1));
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Cancelled)
        );
        assert_eq!(runtime.admission.queued(), 0);
    }

    #[test]
    fn retry_budget_counts_the_initial_attempt_and_stops_at_three() {
        let mut runtime = runtime();
        let mut token = match runtime
            .submit(TaskCard::new(1, vec![], 0, None, None), request(1, 1), 1)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::RetryWait, 1)
            .unwrap();

        for (attempt_id, due_ms) in [(2, 2_001), (3, 6_001)] {
            token = match runtime.retry(1, request(1, attempt_id), due_ms).unwrap() {
                RuntimeAdmission::Admitted(token) => token,
                other => panic!("expected attempt {attempt_id} admission, got {other:?}"),
            };
            runtime
                .finish_at(token, RuntimeOutcome::RetryWait, due_ms)
                .unwrap();
        }

        let exhausted_task = runtime.ledger.task(1).cloned().unwrap();
        assert_eq!(exhausted_task.attempt_id, 3);
        assert_eq!(exhausted_task.status, TaskStatus::Failed);
        assert_eq!(runtime.admission.active_leases(), 0);
        assert!(matches!(
            runtime.retry(1, request(1, 4), 10_001),
            Err(RuntimeError::InvalidTransition(TaskStatus::Failed))
        ));
        assert_eq!(runtime.ledger.task(1), Some(&exhausted_task));
    }

    #[test]
    fn retry_backoff_is_two_then_four_seconds_and_due_time_is_inclusive() {
        let mut runtime = runtime();
        let first_attempt_started_at_ms = 10_000;
        let token = match runtime
            .submit(
                TaskCard::new(1, vec![], 0, None, None),
                request(1, 1),
                first_attempt_started_at_ms,
            )
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected initial admission, got {other:?}"),
        };
        runtime
            .finish_at(
                token,
                RuntimeOutcome::RetryWait,
                first_attempt_started_at_ms,
            )
            .unwrap();
        assert_eq!(
            runtime
                .ledger
                .task(1)
                .and_then(TaskCard::retry_not_before_ms),
            Some(first_attempt_started_at_ms + 2_000)
        );
        assert!(matches!(
            runtime
                .retry(1, request(1, 2), first_attempt_started_at_ms + 1_999)
                .unwrap(),
            RuntimeAdmission::Rejected { .. }
        ));
        let second_attempt_started_at_ms = first_attempt_started_at_ms + 2_000;
        let token = match runtime
            .retry(1, request(1, 2), second_attempt_started_at_ms)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("retry at its exact not-before time was rejected: {other:?}"),
        };

        let second_attempt_finished_at_ms = 30_000;
        runtime
            .finish_at(
                token,
                RuntimeOutcome::RetryWait,
                second_attempt_finished_at_ms,
            )
            .unwrap();
        assert_eq!(
            runtime
                .ledger
                .task(1)
                .and_then(TaskCard::retry_not_before_ms),
            Some(second_attempt_finished_at_ms + 4_000)
        );
        assert!(matches!(
            runtime
                .retry(1, request(1, 3), second_attempt_finished_at_ms + 3_999)
                .unwrap(),
            RuntimeAdmission::Rejected { .. }
        ));
        let third_attempt_started_at_ms = second_attempt_finished_at_ms + 4_000;
        let token = match runtime
            .retry(1, request(1, 3), third_attempt_started_at_ms)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("retry at its exact not-before time was rejected: {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::Done, third_attempt_started_at_ms)
            .unwrap();
    }

    #[test]
    fn retry_is_not_scheduled_when_backoff_reaches_or_exceeds_deadline() {
        let mut runtime = runtime();
        for (task_id, hard_deadline_ms) in [(1, 12_000), (2, 11_999)] {
            let token = match runtime
                .submit(
                    TaskCard::new(task_id, vec![], 0, Some(hard_deadline_ms), None),
                    request(task_id, 1),
                    10_000,
                )
                .unwrap()
            {
                RuntimeAdmission::Admitted(token) => token,
                other => panic!("expected initial task admission, got {other:?}"),
            };
            runtime
                .finish_at(token, RuntimeOutcome::RetryWait, 10_000)
                .unwrap();
            let task = runtime.ledger.task(task_id).unwrap();
            assert_eq!(task.status, TaskStatus::Failed);
            assert_eq!(task.attempt_id, 1);
            assert_eq!(task.retry_not_before_ms(), None);
        }
        assert_eq!(runtime.admission.active_leases(), 0);
    }

    #[test]
    fn submit_binds_task_deadline_to_the_initial_lease() {
        let mut runtime = runtime();
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let hard_deadline_ms = now_ms + 60_000;
        let mut initial_request = request(1, 1);
        initial_request.deadline.deadline_ms = Some(hard_deadline_ms + 60_000);
        let task = TaskCard::new(1, vec![], 0, Some(hard_deadline_ms), None);

        let token = match runtime.submit(task, initial_request, now_ms).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted task, got {other:?}"),
        };

        assert_eq!(
            runtime
                .admission
                .inspect_token(&token)
                .unwrap()
                .expires_at_ms,
            Some(hard_deadline_ms)
        );
    }

    #[test]
    fn retry_cannot_extend_or_drop_the_task_hard_deadline() {
        let mut runtime = runtime();
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let task_deadline_ms = now_ms + 120_000;
        let mut first_request = request(1, 1);
        first_request.deadline.deadline_ms = Some(task_deadline_ms + 60_000);
        let task = TaskCard::new(1, vec![], 0, Some(task_deadline_ms), None);
        let token = match runtime.submit(task, first_request, now_ms).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted task, got {other:?}"),
        };
        runtime
            .finish_at(token, RuntimeOutcome::RetryWait, now_ms)
            .unwrap();

        let mut retry_request = request(1, 2);
        retry_request.deadline.deadline_ms = Some(task_deadline_ms + 120_000);
        let first_retry_started_at_ms = now_ms + 2_000;
        let retry_token = match runtime
            .retry(1, retry_request, first_retry_started_at_ms)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted retry, got {other:?}"),
        };

        assert_eq!(
            runtime
                .admission
                .inspect_token(&retry_token)
                .unwrap()
                .expires_at_ms,
            Some(task_deadline_ms)
        );

        runtime
            .finish_at(
                retry_token,
                RuntimeOutcome::RetryWait,
                first_retry_started_at_ms + 1,
            )
            .unwrap();
        let retry_without_deadline = request(1, 3);
        let second_retry_started_at_ms = first_retry_started_at_ms + 1 + 4_000;
        let retry_token = match runtime
            .retry(1, retry_without_deadline, second_retry_started_at_ms)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted retry, got {other:?}"),
        };

        assert_eq!(
            runtime
                .admission
                .inspect_token(&retry_token)
                .unwrap()
                .expires_at_ms,
            Some(task_deadline_ms)
        );
    }

    #[test]
    fn retry_after_task_deadline_is_rejected_without_state_mutation() {
        let mut runtime = runtime();
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let hard_deadline_ms = now_ms + 60_000;
        let task = TaskCard::new(1, vec![], 0, Some(hard_deadline_ms), None);
        let token = match runtime.submit(task, request(1, 1), now_ms).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted task, got {other:?}"),
        };

        runtime.finish(token, RuntimeOutcome::RetryWait).unwrap();

        for retry_now_ms in [hard_deadline_ms + 1, hard_deadline_ms] {
            let before_task = runtime.ledger.task(1).cloned().unwrap();
            let before_lanes = runtime.lanes.clone();
            let before_pending_admissions = runtime.pending_admissions.clone();
            let before_used_resources = runtime.admission.used();
            let before_active_leases = runtime.admission.active_leases();
            let before_queued = runtime.admission.queued();
            let result = runtime.retry(1, request(1, 2), retry_now_ms);

            assert_eq!(
                result.unwrap(),
                RuntimeAdmission::Rejected {
                    reason: ResourceError::DeadlineExceeded,
                }
            );
            assert_eq!(runtime.ledger.task(1), Some(&before_task));
            assert_eq!(runtime.lanes, before_lanes);
            assert_eq!(runtime.pending_admissions, before_pending_admissions);
            assert_eq!(runtime.admission.used(), before_used_resources);
            assert_eq!(runtime.admission.active_leases(), before_active_leases);
            assert_eq!(runtime.admission.queued(), before_queued);
        }
    }

    #[test]
    fn retry_deadline_rejection_does_not_mutate_runtime_state() {
        let mut runtime = runtime();
        let now_ms = crate::resource::unix_time_millis_for_runtime();
        let hard_deadline_ms = now_ms + 60_000;
        let mut initial_request = request(1, 1);
        initial_request.deadline.deadline_ms = Some(hard_deadline_ms);
        let task = TaskCard::new(1, vec![], 0, Some(hard_deadline_ms), None);

        let token = match runtime.submit(task, initial_request, now_ms).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admitted task, got {other:?}"),
        };
        runtime.finish(token, RuntimeOutcome::RetryWait).unwrap();

        for retry_now_ms in [hard_deadline_ms + 1, hard_deadline_ms] {
            let before_task = runtime.ledger.task(1).cloned().unwrap();
            let before_lanes = runtime.lanes.clone();
            let before_pending_admissions = runtime.pending_admissions.clone();
            let before_used_resources = runtime.admission.used();
            let before_active_leases = runtime.admission.active_leases();
            let before_queued = runtime.admission.queued();
            let mut expired_request = request(1, 2);
            expired_request.deadline.deadline_ms = Some(hard_deadline_ms);
            let result = runtime.retry(1, expired_request, retry_now_ms);

            assert_eq!(
                result.unwrap(),
                RuntimeAdmission::Rejected {
                    reason: ResourceError::DeadlineExceeded,
                }
            );
            assert_eq!(runtime.ledger.task(1), Some(&before_task));
            assert_eq!(runtime.lanes, before_lanes);
            assert_eq!(runtime.pending_admissions, before_pending_admissions);
            assert_eq!(runtime.admission.used(), before_used_resources);
            assert_eq!(runtime.admission.active_leases(), before_active_leases);
            assert_eq!(runtime.admission.queued(), before_queued);
        }
    }

    #[test]
    fn cancellation_exposes_cancelling_transition_before_release() {
        let mut runtime = runtime();
        let task = TaskCard::new(1, vec![], 0, None, None);
        let token = match runtime.submit(task, request(1, 1), 1).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admission, got {other:?}"),
        };
        runtime.begin_cancellation(&token).unwrap();
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Cancelling)
        );
        runtime
            .release_and_transition(token, TaskStatus::Cancelled, None)
            .unwrap();
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Cancelled)
        );
    }

    #[test]
    fn queued_task_is_released_and_re_admitted_through_the_ledger() {
        let profile = crate::resource::HardwareProfile::probe();
        let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
        runtime.admission = crate::resource::AdmissionController::new(
            crate::resource::GrantedResources {
                cpu_threads: 1,
                host_memory_bytes: 1024,
                accelerator_memory_bytes: 0,
                io_in_flight: 1,
                process_limit: 1,
                thread_limit: 1,
                fd_limit: 256,
            },
            1,
        );
        let first = match runtime
            .submit(TaskCard::new(1, vec![], 0, None, None), request(1, 1), 1)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected first task admission, got {other:?}"),
        };
        assert!(matches!(
            runtime
                .submit(TaskCard::new(2, vec![], 0, None, None), request(2, 1), 2)
                .unwrap(),
            RuntimeAdmission::Queued { .. }
        ));
        runtime.finish(first, RuntimeOutcome::Done).unwrap();
        let drained = runtime.drain_queued(3);
        assert!(matches!(
            drained.as_slice(),
            [QueuedRuntimeAdmission {
                task_id: 2,
                attempt_id: 1,
                admission: RuntimeAdmission::Admitted(_),
            }]
        ));
        assert_eq!(
            runtime.ledger.task(2).map(|task| task.status),
            Some(TaskStatus::Running)
        );
    }

    #[test]
    fn queue_poll_preserves_other_admissions_and_retries_pending_work() {
        let profile = crate::resource::HardwareProfile::probe();
        let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
        runtime.admission = crate::resource::AdmissionController::new(
            crate::resource::GrantedResources {
                cpu_threads: 1,
                host_memory_bytes: 1024,
                accelerator_memory_bytes: 0,
                io_in_flight: 1,
                process_limit: 1,
                thread_limit: 1,
                fd_limit: 256,
            },
            2,
        );
        let first = match runtime
            .submit(TaskCard::new(1, vec![], 0, None, None), request(1, 1), 1)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected first task admission, got {other:?}"),
        };
        assert!(matches!(
            runtime
                .submit(TaskCard::new(2, vec![], 0, None, None), request(2, 1), 2)
                .unwrap(),
            RuntimeAdmission::Queued { .. }
        ));
        assert!(matches!(
            runtime
                .submit(TaskCard::new(3, vec![], 0, None, None), request(3, 1), 2)
                .unwrap(),
            RuntimeAdmission::Queued { .. }
        ));

        runtime.finish(first, RuntimeOutcome::Done).unwrap();
        assert!(matches!(
            runtime.poll_queued(3, 1, 3),
            Some(RuntimeAdmission::Queued { .. })
        ));
        let second = match runtime.poll_queued(2, 1, 3) {
            Some(RuntimeAdmission::Admitted(token)) => token,
            other => panic!("expected task 2 admission, got {other:?}"),
        };
        runtime.finish(second, RuntimeOutcome::Done).unwrap();
        assert!(matches!(
            runtime.poll_queued(3, 1, 4),
            Some(RuntimeAdmission::Admitted(_))
        ));
    }

    #[test]
    fn queued_cancellation_removes_work_without_a_lease() {
        let profile = crate::resource::HardwareProfile::probe();
        let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
        runtime.admission = crate::resource::AdmissionController::new(
            crate::resource::GrantedResources {
                cpu_threads: 1,
                host_memory_bytes: 1024,
                accelerator_memory_bytes: 0,
                io_in_flight: 1,
                process_limit: 1,
                thread_limit: 1,
                fd_limit: 256,
            },
            3,
        );
        let first = match runtime
            .submit(TaskCard::new(1, vec![], 0, None, None), request(1, 1), 1)
            .unwrap()
        {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected first task admission, got {other:?}"),
        };
        assert!(matches!(
            runtime
                .submit(TaskCard::new(2, vec![], 0, None, None), request(2, 1), 2)
                .unwrap(),
            RuntimeAdmission::Queued { .. }
        ));
        assert!(runtime.cancel_queued(2, 1).unwrap());
        assert_eq!(runtime.admission.queued(), 0);
        assert_eq!(runtime.admission.active_leases(), 1);
        assert_eq!(
            runtime.ledger.task(2).map(|task| task.status),
            Some(TaskStatus::Cancelled)
        );
        runtime.finish(first, RuntimeOutcome::Done).unwrap();
    }

    #[test]
    fn expired_lease_is_reaped_as_timeout_and_capacity_is_reclaimed() {
        let profile = crate::resource::HardwareProfile::probe();
        let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
        let mut request = request(1, 1);
        request.deadline.deadline_ms = Some(10);
        let admission = runtime
            .submit(TaskCard::new(1, vec![], 0, Some(10), None), request, 1)
            .unwrap();
        assert!(matches!(admission, RuntimeAdmission::Admitted(_)));
        assert_eq!(runtime.reap_expired(10), 1);
        assert_eq!(runtime.admission.active_leases(), 0);
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::TimedOut)
        );
    }

    #[test]
    fn token_does_not_serialize_authoritative_grants() {
        let token = ResourceLeaseToken {
            schema: crate::resource::LEASE_TOKEN_SCHEMA_V1.to_string(),
            lease_id: 1,
            generation: 1,
            attempt_id: 1,
        };
        let json = serde_json::to_string(&token).unwrap();
        assert!(!json.contains("granted"));
        assert!(!json.contains("work_kind"));
    }

    #[test]
    fn cooperative_disabled_fails_closed() {
        let mut runtime = runtime();
        assert_eq!(runtime.cooperative_mode(), CooperativeRuntimeMode::Disabled);
        assert!(matches!(
            runtime.submit_cooperative(cooperative_request(1, 1, "plan-a"), 1),
            Err(RuntimeError::Resource(ResourceError::ResourceUnavailable(
                _
            )))
        ));
    }

    #[test]
    fn cooperative_shadow_validates_without_mutating_usage() {
        let mut runtime = runtime();
        let capabilities = cooperative_inventory();
        runtime
            .configure_cooperative(CooperativeRuntimeMode::Shadow, &capabilities, &[])
            .unwrap();
        let before = runtime.cooperative_admission.as_ref().unwrap().clone();

        assert_eq!(
            runtime.submit_cooperative(cooperative_request(1, 1, "plan-a"), 1),
            Ok(CooperativeRuntimeAdmission::Shadow)
        );
        assert_eq!(runtime.cooperative_admission.as_ref().unwrap(), &before);
    }

    #[test]
    fn cooperative_enforced_is_idempotent_and_releases_exactly_once() {
        let mut runtime = runtime();
        let capabilities = cooperative_inventory();
        runtime
            .configure_cooperative(CooperativeRuntimeMode::Enforced, &capabilities, &[])
            .unwrap();
        let request = cooperative_request(1, 1, "plan-a");
        let token = match runtime.submit_cooperative(request.clone(), 10).unwrap() {
            CooperativeRuntimeAdmission::Admitted(token) => *token,
            other => panic!("expected enforced admission, got {other:?}"),
        };
        let duplicate = match runtime.submit_cooperative(request, 11).unwrap() {
            CooperativeRuntimeAdmission::Admitted(token) => *token,
            other => panic!("expected idempotent admission, got {other:?}"),
        };
        assert_eq!(token, duplicate);
        assert_eq!(
            runtime
                .cooperative_admission
                .as_ref()
                .unwrap()
                .active_leases(),
            1
        );

        runtime
            .finish_cooperative(token.clone(), RuntimeOutcome::Done)
            .unwrap();
        assert_eq!(
            runtime
                .cooperative_admission
                .as_ref()
                .unwrap()
                .active_leases(),
            0
        );
        assert!(matches!(
            runtime.finish_cooperative(token, RuntimeOutcome::Done),
            Err(RuntimeError::Resource(ResourceError::UnknownLease(_)))
        ));
    }

    #[test]
    fn cooperative_reconfiguration_is_fenced_by_active_leases() {
        let mut runtime = runtime();
        let capabilities = cooperative_inventory();
        runtime
            .configure_cooperative(CooperativeRuntimeMode::Enforced, &capabilities, &[])
            .unwrap();
        let token = match runtime
            .submit_cooperative(cooperative_request(1, 1, "plan-a"), 1)
            .unwrap()
        {
            CooperativeRuntimeAdmission::Admitted(token) => *token,
            other => panic!("expected enforced admission, got {other:?}"),
        };

        assert!(matches!(
            runtime.configure_cooperative(CooperativeRuntimeMode::Shadow, &capabilities, &[]),
            Err(RuntimeError::Resource(ResourceError::ResourceUnavailable(
                _
            )))
        ));
        assert_eq!(runtime.cooperative_mode(), CooperativeRuntimeMode::Enforced);
        runtime.cancel_cooperative(token).unwrap();
        runtime
            .configure_cooperative(CooperativeRuntimeMode::Shadow, &capabilities, &[])
            .unwrap();
    }
}
