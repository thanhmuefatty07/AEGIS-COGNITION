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
use std::io::{self, Read};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::process::{Child, Command, ExitStatus, Stdio};
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

const MAX_UNTRUSTED_OUTPUT_BYTES: usize = 8 * 1024 * 1024;
const PROCESS_CLEANUP_GRACE: Duration = Duration::from_secs(5);

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

    /// Run arbitrary native/external work only through a controller that can
    /// contain the child before its code executes. No shell is used.
    /// Combined stdout/stderr capture is limited to 8 MiB; excess output fails
    /// explicitly. The runtime deadline is followed by a bounded cleanup grace.
    pub fn run_untrusted_process(
        &self,
        program: &OsStr,
        args: &[&OsStr],
        lease: &ResourceLease,
        controller: &dyn ResourceController,
        timeout: Duration,
    ) -> Result<UntrustedProcessOutput, ResourceError> {
        self.with_lane(ExecutionLane::Untrusted, || {
            let started = Instant::now();
            let started_at_ms = crate::resource::unix_time_millis_for_runtime();
            if lease.is_cancelled() {
                return Err(ResourceError::ResourceUnavailable(
                    "untrusted process lease was cancelled".to_string(),
                ));
            }
            let lease_timeout = lease.remaining_execution_budget(started_at_ms)?;
            let timeout = lease_timeout.map_or(timeout, |lease_timeout| timeout.min(lease_timeout));
            let caller_deadline_ms = started_at_ms
                .saturating_add(u64::try_from(timeout.as_millis()).unwrap_or(u64::MAX));
            let mut process_lease = lease.clone();
            process_lease.expires_at_ms =
                Some(lease.expires_at_ms.map_or(caller_deadline_ms, |deadline| {
                    deadline.min(caller_deadline_ms)
                }));
            if lease_deadline_reached(&process_lease, started, Some(timeout)) {
                return Err(ResourceError::DeadlineExceeded);
            }
            let capabilities = controller.capabilities();
            if capabilities.memory != crate::resource::EnforcementLevel::KernelEnforced
                || capabilities.termination != crate::resource::EnforcementLevel::KernelEnforced
            {
                return Err(ResourceError::UnsupportedControl(
                    "native process lane requires kernel-enforced memory and termination"
                        .to_string(),
                ));
            }
            if lease_deadline_reached(&process_lease, started, Some(timeout)) {
                return Err(ResourceError::DeadlineExceeded);
            }
            controller.create_scope(&process_lease)?;
            if lease_deadline_reached(&process_lease, started, Some(timeout)) {
                return Err(release_scope_after_error(
                    controller,
                    &process_lease,
                    ResourceError::DeadlineExceeded,
                ));
            }
            let mut command = Command::new(program);
            command
                .args(args)
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            if lease_deadline_reached(&process_lease, started, Some(timeout)) {
                return Err(release_scope_after_error(
                    controller,
                    &process_lease,
                    ResourceError::DeadlineExceeded,
                ));
            }
            let mut child = match controller.spawn_in_scope(&process_lease, command) {
                Ok(child) => child,
                Err(error) => {
                    return Err(release_scope_after_error(controller, &process_lease, error));
                }
            };

            let output = match capture_contained_process(
                &mut child,
                &process_lease,
                controller,
                started,
                timeout,
            ) {
                Ok(output) => output,
                Err(error) => {
                    return Err(release_scope_after_error(controller, &process_lease, error));
                }
            };
            controller.release_scope(&process_lease)?;
            Ok(output)
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

fn lease_deadline_reached(
    lease: &ResourceLease,
    started: Instant,
    lease_timeout: Option<Duration>,
) -> bool {
    lease.is_expired(crate::resource::unix_time_millis_for_runtime())
        || lease_timeout.is_some_and(|budget| started.elapsed() >= budget)
}

trait ProcessPipe: Read {
    fn prepare(&self) -> io::Result<()>;
    fn read_available(&mut self, buffer: &mut [u8]) -> io::Result<usize>;
}

#[cfg(unix)]
impl<T: Read + std::os::fd::AsRawFd> ProcessPipe for T {
    fn prepare(&self) -> io::Result<()> {
        let descriptor = self.as_raw_fd();
        let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
        if flags == -1
            || unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags | libc::O_NONBLOCK) } == -1
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    fn read_available(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        self.read(buffer)
    }
}

#[cfg(windows)]
impl<T: Read + std::os::windows::io::AsRawHandle> ProcessPipe for T {
    fn prepare(&self) -> io::Result<()> {
        Ok(())
    }

    fn read_available(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        use windows_sys::Win32::Foundation::{ERROR_BROKEN_PIPE, GetLastError};
        use windows_sys::Win32::System::Pipes::PeekNamedPipe;
        let mut available = 0;
        let ok = unsafe {
            PeekNamedPipe(
                self.as_raw_handle(),
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                &mut available,
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            let error = unsafe { GetLastError() };
            return if error == ERROR_BROKEN_PIPE {
                Ok(0)
            } else {
                Err(io::Error::from_raw_os_error(error as i32))
            };
        }
        if available == 0 {
            return Err(io::ErrorKind::WouldBlock.into());
        }
        let count = (available as usize).min(buffer.len());
        // Only this supervisor owns the reader, so available bytes cannot be
        // consumed between the peek and the bounded read of Rust's child pipe.
        self.read(&mut buffer[..count])
    }
}

#[cfg(not(any(unix, windows)))]
impl<T: Read> ProcessPipe for T {
    fn prepare(&self) -> io::Result<()> {
        Err(io::ErrorKind::Unsupported.into())
    }

    fn read_available(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
        Err(io::ErrorKind::Unsupported.into())
    }
}

fn drain_process_pipe<T: ProcessPipe>(
    pipe: &mut Option<T>,
    output: &mut Vec<u8>,
    total: &mut usize,
) -> Result<bool, ResourceError> {
    let Some(reader) = pipe else {
        return Ok(false);
    };
    let mut buffer = [0_u8; 16 * 1024];
    match reader.read_available(&mut buffer) {
        Ok(0) => {
            *pipe = None;
            Ok(true)
        }
        Ok(count) => {
            if count > MAX_UNTRUSTED_OUTPUT_BYTES - *total {
                return Err(ResourceError::ResourceExhausted);
            }
            output
                .try_reserve(count)
                .map_err(|_| ResourceError::ResourceExhausted)?;
            output.extend_from_slice(&buffer[..count]);
            *total += count;
            Ok(true)
        }
        Err(error)
            if matches!(
                error.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
            ) =>
        {
            Ok(false)
        }
        Err(error) => Err(ResourceError::ResourceUnavailable(format!(
            "untrusted process pipe read failed: {error}"
        ))),
    }
}

fn capture_contained_process(
    child: &mut Child,
    lease: &ResourceLease,
    controller: &dyn ResourceController,
    started: Instant,
    timeout: Duration,
) -> Result<UntrustedProcessOutput, ResourceError> {
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();
    let mut failure = (|| {
        stdout_pipe
            .as_ref()
            .ok_or_else(|| io::Error::from(io::ErrorKind::NotFound))?
            .prepare()?;
        stderr_pipe
            .as_ref()
            .ok_or_else(|| io::Error::from(io::ErrorKind::NotFound))?
            .prepare()
    })()
    .err()
    .map(|error| {
        ResourceError::ResourceUnavailable(format!("untrusted process pipe setup failed: {error}"))
    });
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let mut total = 0;
    let mut status = None;
    let mut timed_out = false;
    let mut cleanup_started = None;
    let mut scope_empty = false;
    let mut population_query_failed = false;
    loop {
        if status.is_none() {
            match child.try_wait() {
                Ok(current) => status = current,
                Err(error) => {
                    failure.get_or_insert_with(|| {
                        ResourceError::ResourceUnavailable(format!(
                            "untrusted process wait failed: {error}"
                        ))
                    });
                }
            }
        }
        let mut progressed = false;
        if failure.is_none() {
            let drained =
                drain_process_pipe(&mut stdout_pipe, &mut stdout, &mut total).and_then(|first| {
                    drain_process_pipe(&mut stderr_pipe, &mut stderr, &mut total)
                        .map(|second| first || second)
                });
            match drained {
                Ok(value) => progressed = value,
                Err(error) => failure = Some(error),
            }
        }
        if cleanup_started.is_none() {
            if lease.is_cancelled() {
                failure.get_or_insert_with(|| {
                    ResourceError::ResourceUnavailable(
                        "untrusted process lease was cancelled".to_string(),
                    )
                });
            }
            if status.is_none() && started.elapsed() >= timeout {
                timed_out = true;
            }
            if status.is_some() || timed_out || failure.is_some() {
                cleanup_started = Some(Instant::now());
                if let Err(error) = controller.terminate(lease) {
                    append_cleanup_error(&mut failure, "scope termination", error);
                    if status.is_none()
                        && let Err(error) = child.kill()
                    {
                        append_cleanup_error(&mut failure, "child termination", error);
                    }
                }
            }
        }
        if failure.is_some() {
            stdout_pipe = None;
            stderr_pipe = None;
        }
        if let Some(cleanup_started) = cleanup_started {
            if !scope_empty && !population_query_failed {
                match controller.scope_is_empty(lease) {
                    Ok(empty) => scope_empty = empty,
                    Err(error) => {
                        population_query_failed = true;
                        append_cleanup_error(&mut failure, "scope population check", error);
                    }
                }
            }
            if status.is_some()
                && stdout_pipe.is_none()
                && stderr_pipe.is_none()
                && (scope_empty || population_query_failed)
            {
                break;
            }
            if cleanup_started.elapsed() >= PROCESS_CLEANUP_GRACE {
                append_cleanup_error(
                    &mut failure,
                    "process cleanup",
                    "child reap, descendant exit, or pipe closure unconfirmed within five seconds",
                );
                break;
            }
        }
        if progressed {
            std::thread::yield_now();
        } else {
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    if let Some(error) = failure {
        return Err(error);
    }
    Ok(UntrustedProcessOutput {
        status: status.expect("successful cleanup requires a reaped child"),
        stdout,
        stderr,
        timed_out,
    })
}

fn append_cleanup_error(
    failure: &mut Option<ResourceError>,
    operation: &str,
    error: impl std::fmt::Debug,
) {
    *failure = Some(ResourceError::ResourceUnavailable(match failure.take() {
        Some(primary) => format!("{primary:?}; {operation} failed: {error:?}"),
        None => format!("{operation} failed: {error:?}"),
    }));
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
    use crate::resource::{
        AcceleratorKind, BackendKind, EnforcementLevel, MemoryRequest, RESOURCE_CONTRACT_SCHEMA_V1,
        ResourceControlCapabilities, ResourceUsageSample,
    };

    struct KernelLimitController;

    impl ResourceController for KernelLimitController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            ResourceControlCapabilities {
                cpu: EnforcementLevel::KernelEnforced,
                memory: EnforcementLevel::KernelEnforced,
                memory_priority: EnforcementLevel::MeasurementOnly,
                process_count: EnforcementLevel::KernelEnforced,
                thread_count: EnforcementLevel::BestEffort,
                io: EnforcementLevel::MeasurementOnly,
                termination: EnforcementLevel::KernelEnforced,
                backend: "test-kernel-limits".to_string(),
            }
        }

        fn sample(&self) -> ResourceUsageSample {
            ResourceUsageSample {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                sampled_at_ms: 1,
                cpu_threads_active: 0,
                host_memory_bytes: Some(1024 * 1024),
                host_memory_available_bytes: Some(1024 * 1024),
                queue_depth: 0,
                memory_pressure: false,
            }
        }

        fn terminate(&self, _lease: &ResourceLease) -> Result<(), ResourceError> {
            Ok(())
        }
    }

    #[derive(Default)]
    struct CountingKernelLimitController {
        calls: std::sync::atomic::AtomicUsize,
    }

    impl ResourceController for CountingKernelLimitController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            self.calls
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            KernelLimitController.capabilities()
        }

        fn sample(&self) -> ResourceUsageSample {
            KernelLimitController.sample()
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            KernelLimitController.terminate(lease)
        }
    }

    struct SlowScopeController {
        scope_entered: std::sync::mpsc::Sender<Option<u64>>,
        allow_scope_return: std::sync::Mutex<std::sync::mpsc::Receiver<()>>,
        spawn_calls: std::sync::atomic::AtomicUsize,
        release_calls: std::sync::atomic::AtomicUsize,
    }

    impl ResourceController for SlowScopeController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            KernelLimitController.capabilities()
        }

        fn sample(&self) -> ResourceUsageSample {
            KernelLimitController.sample()
        }

        fn create_scope(
            &self,
            lease: &ResourceLease,
        ) -> Result<crate::resource::ResourceScope, ResourceError> {
            self.scope_entered
                .send(lease.expires_at_ms)
                .map_err(|error| {
                    ResourceError::ResourceUnavailable(format!("scope test signal failed: {error}"))
                })?;
            self.allow_scope_return
                .lock()
                .expect("scope release lock")
                .recv_timeout(Duration::from_secs(5))
                .map_err(|error| {
                    ResourceError::ResourceUnavailable(format!(
                        "scope test release failed: {error}"
                    ))
                })?;
            KernelLimitController.create_scope(lease)
        }

        fn spawn_in_scope(
            &self,
            _lease: &ResourceLease,
            _command: Command,
        ) -> Result<Child, ResourceError> {
            self.spawn_calls
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            Err(ResourceError::ResourceUnavailable(
                "the test spawn trap was reached".to_string(),
            ))
        }

        fn release_scope(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            self.release_calls
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            KernelLimitController.release_scope(lease)
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            KernelLimitController.terminate(lease)
        }
    }

    #[cfg(target_os = "linux")]
    #[derive(Default)]
    struct LeaseDeadlineTestController {
        child_pid: std::sync::atomic::AtomicU32,
        terminated: std::sync::atomic::AtomicBool,
    }

    #[cfg(target_os = "linux")]
    impl ResourceController for LeaseDeadlineTestController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            KernelLimitController.capabilities()
        }

        fn sample(&self) -> ResourceUsageSample {
            KernelLimitController.sample()
        }

        fn spawn_in_scope(
            &self,
            _lease: &ResourceLease,
            mut command: Command,
        ) -> Result<Child, ResourceError> {
            let child = command.spawn().map_err(|error| {
                ResourceError::ResourceUnavailable(format!(
                    "deadline test child spawn failed: {error}"
                ))
            })?;
            self.child_pid
                .store(child.id(), std::sync::atomic::Ordering::Release);
            Ok(child)
        }

        fn scope_is_empty(&self, _lease: &ResourceLease) -> Result<bool, ResourceError> {
            Ok(self.terminated.load(std::sync::atomic::Ordering::Acquire))
        }

        fn terminate(&self, _lease: &ResourceLease) -> Result<(), ResourceError> {
            let pid = self.child_pid.load(std::sync::atomic::Ordering::Acquire);
            if pid == 0 {
                return Err(ResourceError::ResourceUnavailable(
                    "deadline test child was not registered".to_string(),
                ));
            }
            let result = unsafe { libc::kill(pid as libc::pid_t, libc::SIGKILL) };
            if result != 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() != Some(libc::ESRCH) {
                    return Err(ResourceError::ResourceUnavailable(format!(
                        "deadline test child termination failed: {error}"
                    )));
                }
            }
            self.terminated
                .store(true, std::sync::atomic::Ordering::Release);
            Ok(())
        }
    }

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
    fn native_process_lane_refuses_spawn_without_pre_execution_containment() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        let mut admission = crate::resource::AdmissionController::from_hardware(&profile);
        let request = crate::resource::ResourceRequest::minimal(1, crate::resource::WorkKind::Tool);
        let lease = match admission.admit(request, 1) {
            crate::resource::AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected tool lease, got {other:?}"),
        };
        let missing_program = tempfile::tempdir()
            .unwrap()
            .path()
            .join("not-created-child");
        let error = lanes
            .run_untrusted_process(
                missing_program.as_os_str(),
                &[],
                &lease,
                &KernelLimitController,
                Duration::from_millis(50),
            )
            .unwrap_err();

        assert!(matches!(
            error,
            ResourceError::UnsupportedControl(reason)
                if reason.contains("pre-execution")
        ));
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Untrusted].active, 0);
    }

    #[test]
    fn native_process_lane_rejects_expired_lease_before_controller_use() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).expect("execution lanes");
        let mut admission = crate::resource::AdmissionController::from_hardware(&profile);
        let request = crate::resource::ResourceRequest::minimal(1, crate::resource::WorkKind::Tool);
        let mut lease = match admission.admit(request, 1) {
            crate::resource::AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected tool lease, got {other:?}"),
        };
        lease.expires_at_ms = Some(0);
        let controller = CountingKernelLimitController::default();

        let error = lanes
            .run_untrusted_process(
                OsStr::new("must-not-run"),
                &[],
                &lease,
                &controller,
                Duration::from_secs(1),
            )
            .expect_err("an expired lease must be rejected before process setup");

        assert!(matches!(error, ResourceError::DeadlineExceeded));
        assert_eq!(
            controller.calls.load(std::sync::atomic::Ordering::Relaxed),
            0,
            "expired leases must be rejected before any controller call"
        );
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Untrusted].active, 0);
    }

    #[test]
    fn native_process_lane_rejects_caller_timeout_expiring_during_scope_setup() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).expect("execution lanes");
        let mut admission = crate::resource::AdmissionController::from_hardware(&profile);
        let request = crate::resource::ResourceRequest::minimal(1, crate::resource::WorkKind::Tool);
        let mut lease = match admission.admit(request, 1) {
            crate::resource::AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected tool lease, got {other:?}"),
        };
        lease.expires_at_ms = Some(crate::resource::unix_time_millis_for_runtime() + 30_000);
        let original_deadline_ms = lease.expires_at_ms.expect("test lease deadline");
        let (scope_entered, scope_entered_rx) = std::sync::mpsc::channel();
        let (allow_scope_return_tx, allow_scope_return_rx) = std::sync::mpsc::channel();
        let controller = std::sync::Arc::new(SlowScopeController {
            scope_entered,
            allow_scope_return: std::sync::Mutex::new(allow_scope_return_rx),
            spawn_calls: std::sync::atomic::AtomicUsize::new(0),
            release_calls: std::sync::atomic::AtomicUsize::new(0),
        });
        let runner_controller = std::sync::Arc::clone(&controller);
        let runner = std::thread::spawn(move || {
            let result = lanes.run_untrusted_process(
                OsStr::new("program-must-not-be-spawned"),
                &[],
                &lease,
                runner_controller.as_ref(),
                Duration::from_secs(1),
            );
            let active_lanes = lanes.snapshot().lanes[&ExecutionLane::Untrusted].active;
            (result, active_lanes)
        });
        let process_deadline_ms = match scope_entered_rx.recv_timeout(Duration::from_secs(5)) {
            Ok(Some(deadline_ms)) => deadline_ms,
            Ok(None) => panic!("process lease must include the caller timeout"),
            Err(wait_error) => {
                let _ = allow_scope_return_tx.send(());
                let (result, _) = runner.join().expect("runner thread must not panic");
                panic!("scope setup was not reached: {wait_error}; result: {result:?}");
            }
        };
        std::thread::sleep(Duration::from_millis(1_100));
        allow_scope_return_tx
            .send(())
            .expect("scope setup release signal");

        let (result, active_lanes) = runner.join().expect("runner thread must not panic");
        let error = result
            .expect_err("a caller timeout reached during setup must prevent the child launch");

        assert_eq!(
            controller
                .spawn_calls
                .load(std::sync::atomic::Ordering::Relaxed),
            0,
            "the controller must not launch after the caller deadline"
        );
        assert!(
            process_deadline_ms < original_deadline_ms,
            "the controller must receive the caller deadline tightened against the lease"
        );
        assert!(matches!(error, ResourceError::DeadlineExceeded));
        assert_eq!(
            controller
                .release_calls
                .load(std::sync::atomic::Ordering::Relaxed),
            1,
            "a scope created before the deadline must still be released"
        );
        assert_eq!(active_lanes, 0);
    }

    #[cfg(windows)]
    #[test]
    fn native_process_lane_windows_controller_rejects_expired_lease_before_scope_creation() {
        let mut lease = windows_fixture_lease(43, 1);
        lease.expires_at_ms = Some(0);
        let controller = crate::resource_platform::WindowsJobObjectController::new()
            .expect("Windows controller");

        let result = controller.create_scope(&lease);
        if result.is_ok() {
            controller.release_scope(&lease).expect("scope cleanup");
        }

        assert!(
            matches!(&result, Err(ResourceError::DeadlineExceeded)),
            "expired lease must not create a Windows Job Object scope: {result:?}"
        );
    }

    #[cfg(any(target_os = "linux", target_os = "windows"))]
    #[test]
    fn native_process_lane_uses_lease_expiry_to_bound_child_runtime() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).expect("execution lanes");
        #[cfg(target_os = "linux")]
        let mut lease = {
            let mut admission = crate::resource::AdmissionController::from_hardware(&profile);
            let request =
                crate::resource::ResourceRequest::minimal(2, crate::resource::WorkKind::Tool);
            match admission.admit(request, 1) {
                crate::resource::AdmissionDecision::Admitted(lease) => lease,
                other => panic!("expected tool lease, got {other:?}"),
            }
        };
        #[cfg(windows)]
        let mut lease = windows_fixture_lease(42, 1);
        lease.expires_at_ms = Some(crate::resource::unix_time_millis_for_runtime() + 2_000);
        #[cfg(target_os = "linux")]
        let controller = LeaseDeadlineTestController::default();
        #[cfg(windows)]
        let controller = crate::resource_platform::WindowsJobObjectController::new()
            .expect("Windows controller");
        let executable = std::env::current_exe().expect("test executable");
        let started = Instant::now();

        let output = lanes
            .run_untrusted_process(
                executable.as_os_str(),
                &[
                    OsStr::new("--ignored"),
                    OsStr::new("--exact"),
                    OsStr::new("execution::tests::lease_deadline_child_fixture"),
                    OsStr::new("--nocapture"),
                ],
                &lease,
                &controller,
                Duration::from_secs(8),
            )
            .expect("lease expiry must terminate and reap the child");

        assert!(output.timed_out, "the child must reach the lease deadline");
        assert!(
            !output.status.success(),
            "the timed-out child must not succeed"
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("LEASE_CHILD_READY"),
            "the child must have started before the deadline was enforced"
        );
        assert!(
            started.elapsed() < Duration::from_secs(6),
            "the two-second lease must bound an eight-second caller timeout"
        );
        assert!(
            started.elapsed() >= Duration::from_secs(1),
            "the child must run until near the two-second lease deadline"
        );
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Untrusted].active, 0);
    }

    #[cfg(any(target_os = "linux", target_os = "windows"))]
    #[test]
    #[ignore = "launched only by native_process_lane_uses_lease_expiry_to_bound_child_runtime"]
    fn lease_deadline_child_fixture() {
        use std::io::Write;

        let mut stdout = std::io::stdout();
        writeln!(stdout, "LEASE_CHILD_READY").expect("fixture marker");
        stdout.flush().expect("fixture marker flush");
        std::thread::sleep(Duration::from_secs(30));
    }

    #[cfg(windows)]
    fn windows_fixture_run(name: &str, timeout: Duration) -> UntrustedProcessOutput {
        windows_fixture_run_with_process_limit(name, timeout, 2)
    }

    #[cfg(windows)]
    fn windows_fixture_lease(task_id: u128, process_limit: u32) -> ResourceLease {
        let mut request =
            crate::resource::ResourceRequest::minimal(task_id, crate::resource::WorkKind::Tool);
        request.host_memory.bytes = 64 * 1024 * 1024;
        request.process_limit = Some(process_limit);
        let capacity = crate::resource::GrantedResources {
            cpu_threads: request.cpu.max_threads,
            host_memory_bytes: request.host_memory.bytes,
            accelerator_memory_bytes: 0,
            io_in_flight: request.io.max_in_flight,
            process_limit,
            thread_limit: request.thread_limit.unwrap_or(request.cpu.max_threads),
            fd_limit: request.fd_limit.unwrap_or(256),
        };
        let mut admission = crate::resource::AdmissionController::new(capacity, 1);
        match admission.admit(request, 1) {
            crate::resource::AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected native fixture lease, got {other:?}"),
        }
    }

    #[cfg(windows)]
    fn windows_fixture_run_with_process_limit(
        name: &str,
        timeout: Duration,
        process_limit: u32,
    ) -> UntrustedProcessOutput {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).expect("execution lanes");
        let lease = windows_fixture_lease(42, process_limit);
        let controller = crate::resource_platform::WindowsJobObjectController::new()
            .expect("Windows controller");
        let executable = std::env::current_exe().expect("test executable");
        let output = lanes
            .run_untrusted_process(
                executable.as_os_str(),
                &[
                    OsStr::new("--ignored"),
                    OsStr::new("--exact"),
                    OsStr::new(name),
                    OsStr::new("--nocapture"),
                ],
                &lease,
                &controller,
                timeout,
            )
            .expect("native fixture result");
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Untrusted].active, 0);
        controller
            .create_scope(&lease)
            .expect("scope must be reusable");
        controller
            .release_scope(&lease)
            .expect("reused scope cleanup");
        output
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "subprocess fixture for native pipe backpressure"]
    fn bulk_output_child_fixture() {
        use std::io::Write;
        std::io::stdout()
            .write_all(&vec![b'X'; 256 * 1024])
            .unwrap();
        std::io::stderr()
            .write_all(&vec![b'Y'; 256 * 1024])
            .unwrap();
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "subprocess fixture that retains inherited output handles"]
    fn sleeping_child_fixture() {
        std::thread::sleep(Duration::from_secs(10));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "subprocess fixture for descendant cleanup after primary exit"]
    fn descendant_pipe_child_fixture() {
        use std::io::Write;
        let child = Command::new(std::env::current_exe().expect("test executable"))
            .args([
                "--ignored",
                "--exact",
                "execution::tests::sleeping_child_fixture",
                "--nocapture",
            ])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("descendant");
        println!("DESCENDANT_PID:{}", child.id());
        std::io::stdout().flush().unwrap();
        // This fixture deliberately leaves the descendant running so the
        // parent-side cleanup path can prove it terminates the process tree.
        // The fixture process exits immediately; the OS closes this handle
        // with it, so waiting here would change the behavior under test.
        std::mem::forget(child);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "subprocess fixture for active-descendant timeout cleanup"]
    fn active_descendant_timeout_child_fixture() {
        use std::io::Write;
        let child = Command::new(std::env::current_exe().expect("test executable"))
            .args([
                "--ignored",
                "--exact",
                "execution::tests::sleeping_child_fixture",
                "--nocapture",
            ])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("active descendant");
        println!("DESCENDANT_PID:{}", child.id());
        std::io::stdout().flush().expect("flush descendant PID");
        // Keep the descendant alive while the parent fixture is timed out;
        // the parent-side cleanup test owns termination of this process tree.
        std::mem::forget(child);
        std::thread::sleep(Duration::from_secs(30));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "nested-job subprocess fixture for multi-generation cleanup"]
    fn nested_job_multigeneration_descendant_fixture() {
        use std::io::Write;
        let child = Command::new(std::env::current_exe().expect("test executable"))
            .args([
                "--ignored",
                "--exact",
                "execution::tests::sleeping_child_fixture",
                "--nocapture",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("nested grandchild");
        println!("NESTED_GRANDCHILD_PID:{}", child.id());
        std::io::stdout()
            .flush()
            .expect("flush nested grandchild PID");
        // The nested fixture intentionally hands process-tree ownership to
        // the controller test instead of waiting for the grandchild here.
        std::mem::forget(child);
        std::thread::sleep(Duration::from_secs(30));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "nested-job parent fixture for multi-generation cleanup"]
    fn nested_job_scope_close_parent_fixture() {
        use std::io::{BufRead, BufReader, Write};
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::Foundation::{GetLastError, WAIT_OBJECT_0, WAIT_TIMEOUT};
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
        };

        let lease = windows_fixture_lease(43, 2);
        let controller = crate::resource_platform::WindowsJobObjectController::new()
            .expect("nested Windows controller");
        controller.create_scope(&lease).expect("nested job scope");

        let mut command = Command::new(std::env::current_exe().expect("test executable"));
        command
            .args([
                "--ignored",
                "--exact",
                "execution::tests::nested_job_multigeneration_descendant_fixture",
                "--nocapture",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child = controller
            .spawn_in_scope(&lease, command)
            .expect("assign process into a nested Job Object before resume");
        let child_pid = child.id();
        let mut reader = BufReader::new(child.stdout.take().expect("nested child stdout"));
        let grandchild_pid = loop {
            let mut line = String::new();
            let bytes = reader
                .read_line(&mut line)
                .expect("read nested child marker");
            assert!(
                bytes > 0,
                "nested child exited before starting its descendant"
            );
            if let Some(pid) = line.strip_prefix("NESTED_GRANDCHILD_PID:") {
                break pid.trim().parse::<u32>().expect("numeric grandchild PID");
            }
        };
        let grandchild_handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, grandchild_pid) };
        assert!(
            !grandchild_handle.is_null(),
            "grandchild must be alive before closing its nested job: {}",
            unsafe { GetLastError() }
        );
        let grandchild_handle = unsafe { OwnedHandle::from_raw_handle(grandchild_handle) };
        assert_eq!(
            unsafe { WaitForSingleObject(grandchild_handle.as_raw_handle(), 0) },
            WAIT_TIMEOUT,
            "grandchild must still be running before nested-job cleanup"
        );
        assert!(
            child.try_wait().expect("observe nested child").is_none(),
            "nested child must still be running before nested-job cleanup"
        );
        drop(reader);

        controller
            .release_scope(&lease)
            .expect("closing the inner job must terminate its process tree");
        // A Job Object close may terminate with exit code 0; process signaling
        // within the bound is the cleanup oracle.
        let child_wait = unsafe { WaitForSingleObject(child.as_raw_handle(), 2_000) };
        if child_wait != WAIT_OBJECT_0 {
            let _ = child.kill();
        }
        let _child_status = child.wait().expect("reap nested child");
        let grandchild_wait =
            unsafe { WaitForSingleObject(grandchild_handle.as_raw_handle(), 2_000) };
        controller
            .create_scope(&lease)
            .expect("nested controller scope must be reusable");
        controller
            .release_scope(&lease)
            .expect("release reused nested controller scope");

        let tree_terminated = child_wait == WAIT_OBJECT_0 && grandchild_wait == WAIT_OBJECT_0;
        println!("NESTED_DESCENDANT_PID:{child_pid}");
        println!("NESTED_GRANDCHILD_PID:{grandchild_pid}");
        println!("NESTED_JOB_TREE_TERMINATED:{tree_terminated}");
        std::io::stdout()
            .flush()
            .expect("flush nested-job cleanup result");
        assert_eq!(
            child_wait, WAIT_OBJECT_0,
            "closing the nested job must signal its direct child before the 30-second fixture exits"
        );
        assert_eq!(
            grandchild_wait, WAIT_OBJECT_0,
            "closing the nested job must terminate the grandchild before natural exit"
        );
    }

    #[cfg(windows)]
    fn windows_process_terminated(pid: u32) -> bool {
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::Foundation::{
            ERROR_INVALID_PARAMETER, GetLastError, WAIT_OBJECT_0,
        };
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
        };

        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        if handle.is_null() {
            return unsafe { GetLastError() } == ERROR_INVALID_PARAMETER;
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
        (unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) }) == WAIT_OBJECT_0
    }

    #[cfg(windows)]
    #[test]
    fn native_process_lane_drains_both_pipes_without_false_timeout() {
        let output = windows_fixture_run(
            "execution::tests::bulk_output_child_fixture",
            Duration::from_secs(2),
        );
        assert!(
            !output.timed_out,
            "pipe backpressure must not become a timeout"
        );
        assert!(output.status.success(), "{output:?}");
        assert_eq!(
            output.stdout.iter().filter(|byte| **byte == b'X').count(),
            256 * 1024
        );
        assert_eq!(
            output.stderr.iter().filter(|byte| **byte == b'Y').count(),
            256 * 1024
        );
    }

    #[cfg(windows)]
    #[test]
    fn native_process_lane_reaps_descendants_that_keep_pipes_open() {
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::Foundation::{
            ERROR_INVALID_PARAMETER, GetLastError, WAIT_OBJECT_0,
        };
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
        };
        let started = Instant::now();
        let output = windows_fixture_run(
            "execution::tests::descendant_pipe_child_fixture",
            Duration::from_secs(1),
        );
        assert!(
            started.elapsed() < Duration::from_secs(7),
            "inherited handles must not extend the task beyond its cleanup budget"
        );
        assert!(!output.timed_out);
        assert!(output.status.success(), "{output:?}");
        let stdout = String::from_utf8(output.stdout).expect("fixture output");
        let pid: u32 = stdout
            .lines()
            .find_map(|line| line.strip_prefix("DESCENDANT_PID:"))
            .expect("descendant PID")
            .parse()
            .unwrap();
        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        if handle.is_null() {
            assert_eq!(unsafe { GetLastError() }, ERROR_INVALID_PARAMETER);
        } else {
            let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
            assert_eq!(
                unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) },
                WAIT_OBJECT_0,
                "descendant must be terminated"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn native_process_lane_timeout_reaps_active_descendants() {
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::Foundation::{
            ERROR_INVALID_PARAMETER, GetLastError, WAIT_OBJECT_0,
        };
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
        };

        let started = Instant::now();
        let output = windows_fixture_run(
            "execution::tests::active_descendant_timeout_child_fixture",
            Duration::from_secs(3),
        );
        assert!(
            started.elapsed() < Duration::from_secs(9),
            "timeout cleanup must not wait for the 30-second parent or descendant fixture"
        );
        assert!(output.timed_out, "the live parent must reach its deadline");
        assert!(
            !output.status.success(),
            "the timed-out parent must not succeed"
        );
        let stdout = String::from_utf8(output.stdout).expect("fixture output");
        let pid: u32 = stdout
            .lines()
            .find_map(|line| line.strip_prefix("DESCENDANT_PID:"))
            .expect("active descendant PID")
            .parse()
            .expect("numeric descendant PID");
        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
        if handle.is_null() {
            assert_eq!(unsafe { GetLastError() }, ERROR_INVALID_PARAMETER);
        } else {
            let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
            assert_eq!(
                unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) },
                WAIT_OBJECT_0,
                "the descendant must be terminated when the lane deadline expires"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn native_process_lane_closes_nested_multigeneration_job_tree() {
        let started = Instant::now();
        let output = windows_fixture_run_with_process_limit(
            "execution::tests::nested_job_scope_close_parent_fixture",
            Duration::from_secs(3),
            4,
        );
        assert!(
            started.elapsed() < Duration::from_secs(9),
            "nested-job cleanup must finish before the 30-second fixtures exit naturally"
        );
        assert!(!output.timed_out, "nested scope cleanup must not time out");
        assert!(output.status.success(), "{output:?}");

        let stdout = String::from_utf8(output.stdout).expect("fixture output");
        let child_pid: u32 = stdout
            .lines()
            .find_map(|line| line.strip_prefix("NESTED_DESCENDANT_PID:"))
            .expect("nested descendant PID")
            .parse()
            .expect("numeric nested descendant PID");
        let grandchild_pid: u32 = stdout
            .lines()
            .find_map(|line| line.strip_prefix("NESTED_GRANDCHILD_PID:"))
            .expect("nested grandchild PID")
            .parse()
            .expect("numeric nested grandchild PID");
        assert!(
            stdout
                .lines()
                .any(|line| line == "NESTED_JOB_TREE_TERMINATED:true"),
            "the inner controller must observe both generations terminated before outer cleanup: {stdout}"
        );
        assert!(windows_process_terminated(child_pid));
        assert!(windows_process_terminated(grandchild_pid));
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
