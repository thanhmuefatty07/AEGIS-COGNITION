//! Concrete bounded execution lanes.
//!
//! Admission and execution are separate concerns, but the executor still
//! acquires the same lane before invoking work. This prevents a caller from
//! bypassing the registry by calling a worker directly.

use crate::resource::{
    AcceleratorProfile, AcceleratorRequest, ExecutionLane, ExecutionLaneRegistry, HardwareProfile,
    ResourceController, ResourceError, ResourceLease,
};
use rayon::ThreadPool;
use std::ffi::OsStr;
use std::future::Future;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Capability-driven seam for optional CUDA/ROCm/Metal adapters. The core
/// runtime depends only on this contract and remains CPU-first.
pub trait AcceleratorExecutor: Send + Sync {
    fn profile(&self) -> &AcceleratorProfile;
    fn execute(&self, request: &AcceleratorRequest, input: &[u8])
    -> Result<Vec<u8>, ResourceError>;

    fn supports(&self, request: &AcceleratorRequest) -> bool {
        let profile = self.profile();
        profile.kind == request.kind
            && request
                .backend
                .is_none_or(|backend| backend == profile.backend)
            && request
                .required_capabilities
                .iter()
                .all(|capability| profile.capabilities.iter().any(|item| item == capability))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UntrustedProcessOutput {
    pub status: ExitStatus,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub timed_out: bool,
}

#[derive(Debug)]
pub struct ExecutionLanes {
    registry: Arc<Mutex<ExecutionLaneRegistry>>,
    cpu_pool: ThreadPool,
    io_runtime: Mutex<tokio::runtime::Runtime>,
}

impl ExecutionLanes {
    pub fn new(profile: &HardwareProfile) -> Result<Self, ResourceError> {
        let registry = ExecutionLaneRegistry::for_profile(profile);
        let cpu_threads = registry
            .lanes
            .get(&ExecutionLane::Cpu)
            .map(|limit| limit.max_in_flight)
            .unwrap_or(1)
            .max(1);
        let cpu_pool = rayon::ThreadPoolBuilder::new()
            .num_threads(cpu_threads as usize)
            .thread_name(|index| format!("aegis-cpu-{index}"))
            .build()
            .map_err(|error| {
                ResourceError::InvalidRequest(format!("CPU lane build failed: {error}"))
            })?;
        let io_runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| {
                ResourceError::InvalidRequest(format!("I/O lane build failed: {error}"))
            })?;
        Ok(Self {
            registry: Arc::new(Mutex::new(registry)),
            cpu_pool,
            io_runtime: Mutex::new(io_runtime),
        })
    }

    pub fn snapshot(&self) -> ExecutionLaneRegistry {
        self.registry
            .lock()
            .expect("execution lane mutex poisoned")
            .clone()
    }

    pub fn run_cpu<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::Cpu, || {
            catch_unwind(AssertUnwindSafe(|| self.cpu_pool.install(work)))
                .map_err(|_| ResourceError::InvalidRequest("CPU lane work panicked".to_string()))
        })?)
    }

    pub fn run_python_blocking<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::PythonCognition, || {
            catch_unwind(AssertUnwindSafe(work))
                .map_err(|_| ResourceError::InvalidRequest("Python lane work panicked".to_string()))
        })?)
    }

    pub fn run_untrusted<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::Untrusted, || {
            catch_unwind(AssertUnwindSafe(work)).map_err(|_| {
                ResourceError::InvalidRequest("untrusted lane work panicked".to_string())
            })
        })?)
    }

    /// Run arbitrary native/external work only after an OS controller has
    /// attached the Rust-owned lease to the child process. No shell is used.
    pub fn run_untrusted_process(
        &self,
        program: &OsStr,
        args: &[&OsStr],
        lease: &ResourceLease,
        controller: &dyn ResourceController,
        timeout: Duration,
    ) -> Result<UntrustedProcessOutput, ResourceError> {
        self.with_lane(ExecutionLane::Untrusted, || {
            let capabilities = controller.capabilities();
            if capabilities.memory != crate::resource::EnforcementLevel::KernelEnforced
                || capabilities.termination != crate::resource::EnforcementLevel::KernelEnforced
            {
                return Err(ResourceError::UnsupportedControl(
                    "native process lane requires kernel-enforced memory and termination"
                        .to_string(),
                ));
            }
            controller.create_scope(lease)?;
            let mut child = Command::new(program)
                .args(args)
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(|error| {
                    ResourceError::ResourceUnavailable(format!(
                        "untrusted process spawn failed: {error}"
                    ))
                })?;
            if let Err(error) = controller.apply_to_process(lease, child.id()) {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }

            let started = Instant::now();
            let mut timed_out = false;
            loop {
                if child
                    .try_wait()
                    .map_err(|error| {
                        ResourceError::ResourceUnavailable(format!(
                            "untrusted process wait failed: {error}"
                        ))
                    })?
                    .is_some()
                {
                    break;
                }
                if started.elapsed() >= timeout {
                    timed_out = true;
                    let _ = controller.terminate(lease);
                    let _ = child.kill();
                    break;
                }
                std::thread::sleep(Duration::from_millis(2));
            }
            let output = child.wait_with_output().map_err(|error| {
                ResourceError::ResourceUnavailable(format!(
                    "untrusted process output failed: {error}"
                ))
            })?;
            Ok(UntrustedProcessOutput {
                status: output.status,
                stdout: output.stdout,
                stderr: output.stderr,
                timed_out,
            })
        })
    }

    /// Execute an accelerator-capable work unit only when the probed profile
    /// advertised accelerator capacity. Vendor-specific execution remains an
    /// adapter concern; this lane is the bounded admission seam.
    pub fn run_accelerator<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::Accelerator, || {
            catch_unwind(AssertUnwindSafe(work)).map_err(|_| {
                ResourceError::InvalidRequest("accelerator lane work panicked".to_string())
            })
        })?)
    }

    pub fn run_io<F, R>(&self, future: F) -> Result<R, ResourceError>
    where
        F: Future<Output = R>,
    {
        Ok(self.with_lane(ExecutionLane::Io, || {
            let runtime = self.io_runtime.lock().map_err(|_| {
                ResourceError::InvalidRequest("I/O runtime mutex poisoned".to_string())
            })?;
            catch_unwind(AssertUnwindSafe(|| runtime.block_on(future)))
                .map_err(|_| ResourceError::InvalidRequest("I/O lane work panicked".to_string()))
        })?)
    }

    fn with_lane<F, R>(&self, lane: ExecutionLane, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> Result<R, ResourceError>,
    {
        {
            let mut registry = self.registry.lock().map_err(|_| {
                ResourceError::InvalidRequest("execution lane mutex poisoned".to_string())
            })?;
            if !registry.try_acquire(lane) {
                return Err(ResourceError::ResourceExhausted);
            }
        }
        let result = work();
        let released = self
            .registry
            .lock()
            .map_err(|_| {
                ResourceError::InvalidRequest("execution lane mutex poisoned".to_string())
            })?
            .release(lane);
        if !released {
            return Err(ResourceError::InvalidRequest(
                "execution lane release mismatch".to_string(),
            ));
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_and_python_lanes_execute_with_bounded_accounting() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(lanes.run_cpu(|| 2 + 2).unwrap(), 4);
        assert_eq!(lanes.run_python_blocking(|| "ok").unwrap(), "ok");
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Cpu].active, 0);
        assert_eq!(
            lanes.snapshot().lanes[&ExecutionLane::PythonCognition].active,
            0
        );
    }

    #[test]
    fn io_lane_runs_future_without_leaking_capacity() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(lanes.run_io(async { 7_u8 }).unwrap(), 7);
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Io].active, 0);
    }

    #[test]
    fn accelerator_lane_is_optional_and_fails_closed_without_a_device() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(
            lanes.run_accelerator(|| 7),
            Err(ResourceError::ResourceExhausted)
        );
    }

    #[test]
    fn native_process_lane_requires_controller_attachment() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        let mut admission = crate::resource::AdmissionController::from_hardware(&profile);
        let request = crate::resource::ResourceRequest::minimal(1, crate::resource::WorkKind::Tool);
        let lease = match admission.admit(request, 1) {
            crate::resource::AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected tool lease, got {other:?}"),
        };
        let controller = crate::resource::PortableResourceController;
        let program = std::env::current_exe().unwrap();
        let error = lanes
            .run_untrusted_process(
                program.as_os_str(),
                &[],
                &lease,
                &controller,
                Duration::from_millis(50),
            )
            .unwrap_err();
        assert!(matches!(error, ResourceError::UnsupportedControl(_)));
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Untrusted].active, 0);
    }
}
