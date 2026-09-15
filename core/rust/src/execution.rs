//! Concrete bounded execution lanes.
//!
//! Admission and execution are separate concerns, but the executor still
//! acquires the same lane before invoking work. This prevents a caller from
//! bypassing the registry by calling a worker directly.

use crate::resource::{
    AcceleratorProfile, AcceleratorRequest, DeviceHealth, ExecutionLane, ExecutionLaneRegistry,
    HardwareProfile, ResourceController, ResourceError, ResourceLease,
};
use rayon::ThreadPool;
use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::future::Future;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Capability-driven seam for optional accelerator adapters. The core runtime
/// depends only on this contract and remains CPU-first.
pub trait AcceleratorExecutor: Send + Sync {
    fn profile(&self) -> &AcceleratorProfile;
    fn execute(&self, request: &AcceleratorRequest, input: &[u8])
    -> Result<Vec<u8>, ResourceError>;

    fn supports(&self, request: &AcceleratorRequest) -> bool {
        let profile = self.profile();
        profile.health == DeviceHealth::Healthy
            && !profile.id.trim().is_empty()
            && profile.kind == request.kind
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
    healthy_accelerators: BTreeMap<String, AcceleratorProfile>,
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
        let healthy_accelerators = profile
            .accelerators
            .iter()
            .filter(|accelerator| {
                accelerator.health == DeviceHealth::Healthy && !accelerator.id.trim().is_empty()
            })
            .map(|accelerator| (accelerator.id.clone(), accelerator.clone()))
            .collect();
        Ok(Self {
            registry: Arc::new(Mutex::new(registry)),
            cpu_pool,
            io_runtime: Mutex::new(io_runtime),
            healthy_accelerators,
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
            let mut child = match Command::new(program)
                .args(args)
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
            {
                Ok(child) => child,
                Err(error) => {
                    let primary = ResourceError::ResourceUnavailable(format!(
                        "untrusted process spawn failed: {error}"
                    ));
                    return Err(release_scope_after_error(controller, lease, primary));
                }
            };
            if let Err(error) = controller.apply_to_process(lease, child.id()) {
                let _ = child.kill();
                let _ = child.wait();
                return Err(release_scope_after_error(controller, lease, error));
            }

            let started = Instant::now();
            let mut timed_out = false;
            loop {
                let status = match child.try_wait() {
                    Ok(status) => status,
                    Err(error) => {
                        let primary = ResourceError::ResourceUnavailable(format!(
                            "untrusted process wait failed: {error}"
                        ));
                        return Err(release_scope_after_error(controller, lease, primary));
                    }
                };
                if status.is_some() {
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
            let output = match child.wait_with_output() {
                Ok(output) => output,
                Err(error) => {
                    let primary = ResourceError::ResourceUnavailable(format!(
                        "untrusted process output failed: {error}"
                    ));
                    return Err(release_scope_after_error(controller, lease, primary));
                }
            };
            controller.release_scope(lease)?;
            Ok(UntrustedProcessOutput {
                status: output.status,
                stdout: output.stdout,
                stderr: output.stderr,
                timed_out,
            })
        })
    }

    /// Execute a trusted closure through the accelerator lane.
    ///
    /// This compatibility helper provides lane accounting only. It is useful
    /// for trusted adapters and tests, but it does not prove that a device
    /// executed the closure. Device-bound work should use
    /// [`Self::run_accelerator_request`].
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

    /// Execute one request through a declared, healthy accelerator adapter.
    ///
    /// The adapter identity must be present in the hardware profile supplied
    /// to [`Self::new`]. Capability matching happens before lane admission, so
    /// a missing, degraded, or mismatched device fails closed without calling
    /// vendor code. The adapter remains responsible for the actual backend
    /// operation and its correctness contract.
    pub fn run_accelerator_request(
        &self,
        executor: &dyn AcceleratorExecutor,
        request: &AcceleratorRequest,
        input: &[u8],
    ) -> Result<Vec<u8>, ResourceError> {
        let profile = executor.profile();
        if profile.health != DeviceHealth::Healthy {
            return Err(ResourceError::CapabilityDenied(
                "accelerator profile is not healthy".to_string(),
            ));
        }
        let Some(inventory_profile) = self.healthy_accelerators.get(&profile.id) else {
            return Err(ResourceError::CapabilityDenied(
                "accelerator profile is not present in the lane inventory".to_string(),
            ));
        };
        if inventory_profile != profile {
            return Err(ResourceError::CapabilityDenied(
                "accelerator profile does not match the lane inventory".to_string(),
            ));
        }
        if !executor.supports(request) {
            return Err(ResourceError::CapabilityDenied(
                "accelerator adapter does not support the request".to_string(),
            ));
        }
        self.with_lane(ExecutionLane::Accelerator, || {
            catch_unwind(AssertUnwindSafe(|| executor.execute(request, input))).map_err(|_| {
                ResourceError::InvalidRequest("accelerator adapter panicked".to_string())
            })?
        })
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

fn release_scope_after_error(
    controller: &dyn ResourceController,
    lease: &ResourceLease,
    primary: ResourceError,
) -> ResourceError {
    match controller.release_scope(lease) {
        Ok(()) => primary,
        Err(cleanup) => ResourceError::ResourceUnavailable(format!(
            "{primary:?}; resource scope cleanup failed: {cleanup:?}"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resource::{AcceleratorKind, BackendKind, MemoryRequest};

    struct EchoAccelerator {
        profile: AcceleratorProfile,
    }

    impl AcceleratorExecutor for EchoAccelerator {
        fn profile(&self) -> &AcceleratorProfile {
            &self.profile
        }

        fn execute(
            &self,
            _request: &AcceleratorRequest,
            input: &[u8],
        ) -> Result<Vec<u8>, ResourceError> {
            Ok(input.iter().map(|byte| byte.wrapping_add(1)).collect())
        }
    }

    fn accelerator_profile(id: &str, health: DeviceHealth) -> AcceleratorProfile {
        AcceleratorProfile {
            id: id.to_string(),
            kind: AcceleratorKind::Gpu,
            backend: BackendKind::Vulkan,
            vendor: "test".to_string(),
            memory_domain: "host".to_string(),
            capabilities: vec!["compute".to_string()],
            health,
        }
    }

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
    fn accelerator_request_requires_a_healthy_inventory_bound_adapter() {
        let mut profile = HardwareProfile::probe();
        profile
            .accelerators
            .push(accelerator_profile("igpu-0", DeviceHealth::Healthy));
        let lanes = ExecutionLanes::new(&profile).unwrap();
        let executor = EchoAccelerator {
            profile: accelerator_profile("igpu-0", DeviceHealth::Healthy),
        };
        let request = AcceleratorRequest {
            kind: AcceleratorKind::Gpu,
            backend: Some(BackendKind::Vulkan),
            required_capabilities: vec!["compute".to_string()],
            memory: MemoryRequest { bytes: 1 },
        };

        assert_eq!(
            lanes
                .run_accelerator_request(&executor, &request, &[1, 2, 255])
                .unwrap(),
            vec![2, 3, 0]
        );
        assert_eq!(
            lanes.snapshot().lanes[&ExecutionLane::Accelerator].active,
            0
        );

        let unsupported = AcceleratorRequest {
            required_capabilities: vec!["matrix".to_string()],
            ..request
        };
        assert!(matches!(
            lanes.run_accelerator_request(&executor, &unsupported, &[]),
            Err(ResourceError::CapabilityDenied(reason))
                if reason.contains("does not support")
        ));

        let missing = EchoAccelerator {
            profile: accelerator_profile("other-gpu", DeviceHealth::Healthy),
        };
        assert!(matches!(
            lanes.run_accelerator_request(&missing, &request, &[]),
            Err(ResourceError::CapabilityDenied(reason))
                if reason.contains("not present")
        ));

        let mismatched = EchoAccelerator {
            profile: AcceleratorProfile {
                backend: BackendKind::Cuda,
                ..accelerator_profile("igpu-0", DeviceHealth::Healthy)
            },
        };
        assert!(matches!(
            lanes.run_accelerator_request(&mismatched, &request, &[]),
            Err(ResourceError::CapabilityDenied(reason))
                if reason.contains("does not match")
        ));

        let degraded = EchoAccelerator {
            profile: accelerator_profile("igpu-0", DeviceHealth::Degraded),
        };
        assert!(matches!(
            lanes.run_accelerator_request(&degraded, &request, &[]),
            Err(ResourceError::CapabilityDenied(reason))
                if reason.contains("not healthy")
        ));
    }

    #[test]
    fn accelerator_lane_counts_only_distinct_nonempty_healthy_devices() {
        let mut profile = HardwareProfile::probe();
        profile
            .accelerators
            .push(accelerator_profile("igpu-0", DeviceHealth::Healthy));
        profile
            .accelerators
            .push(accelerator_profile("igpu-0", DeviceHealth::Healthy));
        profile
            .accelerators
            .push(accelerator_profile("gpu-0", DeviceHealth::Degraded));
        profile
            .accelerators
            .push(accelerator_profile("", DeviceHealth::Healthy));

        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(
            lanes.snapshot().lanes[&ExecutionLane::Accelerator].max_in_flight,
            1
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

    #[test]
    fn cpu_lane_stress_completes_all_contenders_without_starvation() {
        let profile = HardwareProfile::probe();
        let lanes = Arc::new(ExecutionLanes::new(&profile).unwrap());
        let completed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let handles: Vec<_> = (0..64)
            .map(|_| {
                let lanes = Arc::clone(&lanes);
                let completed = Arc::clone(&completed);
                std::thread::spawn(move || {
                    let mut attempts = 0;
                    loop {
                        match lanes.run_cpu(|| 1 + 1) {
                            Ok(_) => break,
                            Err(ResourceError::ResourceExhausted) => {
                                attempts += 1;
                                assert!(attempts < 10_000, "CPU contender starved");
                                std::thread::yield_now();
                            }
                            Err(error) => panic!("unexpected CPU lane error: {error:?}"),
                        }
                    }
                    completed.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }
        assert_eq!(completed.load(std::sync::atomic::Ordering::SeqCst), 64);
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Cpu].active, 0);
    }
}
