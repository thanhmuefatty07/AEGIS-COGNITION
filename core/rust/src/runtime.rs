//! Authoritative single-node runtime facade.
//!
//! This is the first integration seam between the existing TaskLedger and the
//! resource contracts. It intentionally does not execute user code; it proves
//! admission/state transitions before a future scheduler invokes an execution lane.

use crate::resource::{
    AdmissionController, AdmissionDecision, ExecutionLane, ExecutionLaneRegistry, ResourceError,
    ResourceLease, ResourceRequest,
};
use crate::task_ledger::{TaskCard, TaskLedger, TaskLedgerError, TaskStatus};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RuntimeAdmission {
    Admitted(ResourceLease),
    Queued { position: usize, reason: String },
    Rejected { reason: ResourceError },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RuntimeError {
    Task(TaskLedgerError),
    Resource(ResourceError),
    TaskRequestMismatch,
    InvalidTransition(TaskStatus),
    InvalidOutcome(String),
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
        self.ledger.insert_task(task).map_err(RuntimeError::Task)?;
        self.ledger
            .mark_status(request.task_id, TaskStatus::Ready)
            .map_err(RuntimeError::Task)?;

        let lane = lane_for_work_kind(request.work_kind);
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
                self.ledger
                    .mark_status(lease.task_id, TaskStatus::Running)
                    .map_err(RuntimeError::Task)?;
                Ok(RuntimeAdmission::Admitted(lease))
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

    pub fn complete(&mut self, lease: ResourceLease) -> Result<(), RuntimeError> {
        let lane = lane_for_work_kind(lease.work_kind);
        let _ = self.lanes.release(lane);
        self.admission
            .release(lease.lease_id)
            .map_err(RuntimeError::Resource)?;
        self.ledger
            .mark_status(lease.task_id, TaskStatus::Done)
            .map_err(RuntimeError::Task)
    }

    pub fn fail(&mut self, lease: ResourceLease) -> Result<(), RuntimeError> {
        self.release_lease(&lease)?;
        self.ledger
            .mark_status(lease.task_id, TaskStatus::Failed)
            .map_err(RuntimeError::Task)
    }

    pub fn cancel(&mut self, lease: ResourceLease) -> Result<(), RuntimeError> {
        lease.cancel();
        self.release_lease(&lease)?;
        self.ledger
            .mark_status(lease.task_id, TaskStatus::Cancelled)
            .map_err(RuntimeError::Task)
    }

    fn release_lease(&mut self, lease: &ResourceLease) -> Result<(), RuntimeError> {
        self.admission
            .release(lease.lease_id)
            .map_err(RuntimeError::Resource)?;
        let _ = self.lanes.release(lane_for_work_kind(lease.work_kind));
        Ok(())
    }
}

fn lane_for_work_kind(kind: crate::resource::WorkKind) -> ExecutionLane {
    match kind {
        crate::resource::WorkKind::NativeTask => ExecutionLane::Cpu,
        crate::resource::WorkKind::PythonCognition => ExecutionLane::PythonCognition,
        crate::resource::WorkKind::Tool => ExecutionLane::Untrusted,
        crate::resource::WorkKind::Agent => ExecutionLane::PythonCognition,
        crate::resource::WorkKind::Accelerator => ExecutionLane::Accelerator,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resource::{CpuRequest, MemoryRequest, WorkKind};

    #[test]
    fn task_ledger_and_admission_move_together() {
        let profile = crate::resource::HardwareProfile::probe();
        let mut runtime = AuthoritativeRuntime::new(60_000, &profile);
        let task = TaskCard::new(1, vec![], 0, None, None);
        let mut request = ResourceRequest::minimal(1, WorkKind::NativeTask);
        request.cpu = CpuRequest {
            min_threads: 1,
            max_threads: 1,
        };
        request.host_memory = MemoryRequest { bytes: 1 };
        let result = runtime
            .submit(task, request, 1)
            .expect("submit should be valid");
        let lease = match result {
            RuntimeAdmission::Admitted(lease) => lease,
            other => panic!("expected admitted task, got {other:?}"),
        };
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Running)
        );
        runtime
            .complete(lease)
            .expect("completion should release resources");
        assert_eq!(
            runtime.ledger.task(1).map(|task| task.status),
            Some(TaskStatus::Done)
        );
    }
}
