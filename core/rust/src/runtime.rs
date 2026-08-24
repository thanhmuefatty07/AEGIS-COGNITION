//! Authoritative single-node runtime and fenced lease state machine.
//!
//! Python proposes work through typed JSON, but Rust owns the task status,
//! attempt, lease and execution-lane state.  A caller receives only an opaque
//! lease token; all authoritative lease attributes are looked up in Rust.

use crate::resource::{
    AdmissionController, AdmissionDecision, CapacityFeedback, ExecutionLane, ExecutionLaneRegistry,
    ResourceError, ResourceLeaseToken, ResourceRequest, ResourceUsageSample, WorkKind,
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

#[derive(Debug)]
pub struct AuthoritativeRuntime {
    pub ledger: TaskLedger,
    pub admission: AdmissionController,
    pub lanes: ExecutionLaneRegistry,
}

impl AuthoritativeRuntime {
    pub fn new(deadline_horizon_ms: u64, profile: &crate::resource::HardwareProfile) -> Self {
        Self {
            ledger: TaskLedger::new(deadline_horizon_ms),
            admission: AdmissionController::from_hardware(profile),
            lanes: ExecutionLaneRegistry::for_profile(profile),
        }
    }

    pub fn submit(
        &mut self,
        task: TaskCard,
        request: ResourceRequest,
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
        self.ledger.insert_task(task).map_err(RuntimeError::Task)?;
        self.ledger
            .transition_status(request.task_id, TaskStatus::Ready)
            .map_err(RuntimeError::Task)?;
        self.admit_existing(request, now_ms)
    }

    pub fn retry(
        &mut self,
        task_id: u128,
        request: ResourceRequest,
        now_ms: u64,
    ) -> Result<RuntimeAdmission, RuntimeError> {
        if request.task_id != task_id {
            return Err(RuntimeError::TaskRequestMismatch);
        }
        let current_attempt = self
            .ledger
            .task(task_id)
            .ok_or(RuntimeError::Task(TaskLedgerError::MissingTask(task_id)))?
            .attempt_id;
        if request.attempt_id <= current_attempt {
            return Err(RuntimeError::AttemptMismatch);
        }
        self.ledger
            .set_attempt_id(task_id, request.attempt_id)
            .map_err(RuntimeError::Task)?;
        self.ledger
            .transition_status(task_id, TaskStatus::Ready)
            .map_err(RuntimeError::Task)?;
        self.admit_existing(request, now_ms)
    }

    fn admit_existing(
        &mut self,
        request: ResourceRequest,
        now_ms: u64,
    ) -> Result<RuntimeAdmission, RuntimeError> {
        let lane = lane_for_work_kind(request.work_kind);
        self.ledger
            .validate_status_transition(request.task_id, TaskStatus::Admitted)
            .map_err(|error| match error {
                TaskLedgerError::InvalidStatusTransition { from, .. } => {
                    RuntimeError::InvalidTransition(from)
                }
                other => RuntimeError::Task(other),
            })?;
        if !TaskStatus::Admitted.can_transition_to(TaskStatus::Running) {
            return Err(RuntimeError::InvalidTransition(TaskStatus::Admitted));
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
                if let Err(error) = self
                    .ledger
                    .transition_status(lease.task_id, TaskStatus::Admitted)
                    .and_then(|_| {
                        self.ledger
                            .transition_status(lease.task_id, TaskStatus::Running)
                    })
                {
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
        let outcome = if outcome != RuntimeOutcome::Cancelled
            && self
                .admission
                .inspect_token(&token)
                .map_err(RuntimeError::Resource)?
                .is_expired(crate::resource::unix_time_millis_for_runtime())
        {
            RuntimeOutcome::TimedOut
        } else {
            outcome
        };
        if outcome == RuntimeOutcome::Cancelled {
            return self.cancel_and_release(token);
        }
        let target = match outcome {
            RuntimeOutcome::Done => TaskStatus::Done,
            RuntimeOutcome::Failed => TaskStatus::Failed,
            RuntimeOutcome::RetryWait => TaskStatus::RetryWait,
            RuntimeOutcome::TimedOut => TaskStatus::TimedOut,
            RuntimeOutcome::Cancelled => unreachable!("cancelled is handled above"),
        };
        self.release_and_transition(token, target)
    }

    /// Re-attempt queued requests in FIFO order after capacity or lane state
    /// changes. Queue ownership remains in Rust; callers receive only coarse
    /// outcomes and opaque tokens.
    pub fn drain_queued(&mut self, now_ms: u64) -> Vec<QueuedRuntimeAdmission> {
        let queued = self.admission.queued();
        let mut results = Vec::with_capacity(queued);
        for _ in 0..queued {
            let Some(request) = self.admission.pop_queued() else {
                break;
            };
            let task_id = request.task_id;
            let lane = lane_for_work_kind(request.work_kind);
            if !self.lanes.try_acquire(lane) {
                let _ = self.admission.enqueue(request, now_ms);
                break;
            }
            match self.admission.admit(request, now_ms) {
                AdmissionDecision::Admitted(lease) => {
                    let admission = self
                        .ledger
                        .transition_status(task_id, TaskStatus::Admitted)
                        .and_then(|_| self.ledger.transition_status(task_id, TaskStatus::Running))
                        .map(|_| RuntimeAdmission::Admitted(ResourceLeaseToken::from_lease(&lease)))
                        .unwrap_or_else(|error| {
                            let _ = self.admission.release(lease.lease_id);
                            RuntimeAdmission::Rejected {
                                reason: ResourceError::ResourceUnavailable(format!(
                                    "queued task state could not be restored: {error:?}"
                                )),
                            }
                        });
                    if !matches!(admission, RuntimeAdmission::Admitted(_)) {
                        let _ = self.lanes.release(lane);
                    }
                    results.push(QueuedRuntimeAdmission { task_id, admission });
                }
                AdmissionDecision::Queued { position, reason } => {
                    let _ = self.lanes.release(lane);
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        admission: RuntimeAdmission::Queued { position, reason },
                    });
                    break;
                }
                AdmissionDecision::Rejected { reason } => {
                    let _ = self.lanes.release(lane);
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        admission: RuntimeAdmission::Rejected { reason },
                    });
                }
            }
        }
        results
    }

    /// Release leases that crossed their deadline and preserve the explicit
    /// `TimedOut` task state. Returns the number of leases reclaimed.
    pub fn reap_expired(&mut self, now_ms: u64) -> usize {
        let tokens = self.admission.expired_tokens(now_ms);
        let mut reclaimed = 0;
        for token in tokens {
            if self.finish(token, RuntimeOutcome::TimedOut).is_ok() {
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
    ) -> Result<(), RuntimeError> {
        // Validate every mutable subsystem before changing any of them. This
        // makes duplicate, stale, and forged completion calls side-effect free.
        let lease = self
            .admission
            .inspect_token(&token)
            .map_err(RuntimeError::Resource)?
            .clone();
        self.ledger
            .validate_status_transition(lease.task_id, target)
            .map_err(|error| match error {
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
        self.ledger
            .transition_status(lease.task_id, target)
            .map_err(RuntimeError::Task)
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

    fn runtime() -> AuthoritativeRuntime {
        let profile = crate::resource::HardwareProfile::probe();
        AuthoritativeRuntime::new(60_000, &profile)
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
        let task = TaskCard::new(1, vec![], 0, None, None);
        let token = match runtime.submit(task, request(1, 1), 1).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected admission, got {other:?}"),
        };
        runtime.finish(token, RuntimeOutcome::RetryWait).unwrap();
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::RetryWait)
        );
        let retry_token = match runtime.retry(1, request(1, 2), 2).unwrap() {
            RuntimeAdmission::Admitted(token) => token,
            other => panic!("expected retry admission, got {other:?}"),
        };
        assert_eq!(retry_token.attempt_id, 2);
        runtime
            .finish(retry_token, RuntimeOutcome::TimedOut)
            .unwrap();
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::TimedOut)
        );
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
            .release_and_transition(token, TaskStatus::Cancelled)
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
                admission: RuntimeAdmission::Admitted(_),
            }]
        ));
        assert_eq!(
            runtime.ledger.task(2).map(|task| task.status),
            Some(TaskStatus::Running)
        );
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
}
