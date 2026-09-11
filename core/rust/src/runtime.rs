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

#[derive(Debug)]
pub struct AuthoritativeRuntime {
    pub ledger: TaskLedger,
    pub admission: AdmissionController,
    pub lanes: ExecutionLaneRegistry,
    pending_admissions: BTreeMap<(u128, u64), RuntimeAdmission>,
    cooperative_mode: CooperativeRuntimeMode,
    cooperative_admission: Option<CooperativeAdmissionLedger>,
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
            let attempt_id = request.attempt_id;
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
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        attempt_id,
                        admission,
                    });
                }
                AdmissionDecision::Queued { position, reason } => {
                    let _ = self.lanes.release(lane);
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        attempt_id,
                        admission: RuntimeAdmission::Queued { position, reason },
                    });
                    break;
                }
                AdmissionDecision::Rejected { reason } => {
                    let _ = self.lanes.release(lane);
                    results.push(QueuedRuntimeAdmission {
                        task_id,
                        attempt_id,
                        admission: RuntimeAdmission::Rejected { reason },
                    });
                }
            }
        }
        results
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
        self.pending_admissions.remove(&(task_id, attempt_id));

        let task = self
            .ledger
            .task(task_id)
            .ok_or(RuntimeError::Task(TaskLedgerError::MissingTask(task_id)))?;
        if task.attempt_id != attempt_id {
            return Err(RuntimeError::AttemptMismatch);
        }
        if task.status != TaskStatus::Ready {
            return Ok(false);
        }
        if !self.admission.remove_queued(task_id, attempt_id) {
            return Ok(false);
        }
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
