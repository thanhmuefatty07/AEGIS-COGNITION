//! OS-backed resource controllers.
//!
//! Controllers are deliberately opt-in.  A portable profile may measure
//! usage, but a caller must construct a platform controller before claiming
//! kernel-enforced limits.

use crate::resource::{
    RESOURCE_CONTRACT_SCHEMA_V1, ResourceControlCapabilities, ResourceController, ResourceError,
    ResourceLease, ResourceUsageSample,
};

#[cfg(any(target_os = "linux", target_os = "windows"))]
use crate::resource::ResourceScope;

#[cfg(target_os = "linux")]
mod linux {
    use super::{
        RESOURCE_CONTRACT_SCHEMA_V1, ResourceControlCapabilities, ResourceController,
        ResourceError, ResourceLease, ResourceScope, ResourceUsageSample,
    };
    use std::fs;
    use std::path::{Path, PathBuf};

    #[derive(Clone, Debug)]
    pub struct LinuxCgroupV2Controller {
        root: PathBuf,
    }

    impl LinuxCgroupV2Controller {
        pub fn try_new(root: impl AsRef<Path>) -> Result<Self, ResourceError> {
            let root = root.as_ref().to_path_buf();
            for file in ["memory.max", "memory.current", "cpu.max", "pids.max"] {
                if !root.join(file).is_file() {
                    return Err(ResourceError::UnsupportedControl(format!(
                        "cgroup v2 controller missing {}",
                        root.join(file).display()
                    )));
                }
            }
            Ok(Self { root })
        }

        pub fn root(&self) -> &Path {
            &self.root
        }

        pub fn apply_to_process(
            &self,
            lease: &ResourceLease,
            pid: u32,
        ) -> Result<(), ResourceError> {
            let group = self.root.join(format!("aegis-{}", lease.lease_id));
            fs::create_dir_all(&group).map_err(io_error)?;
            self.configure_group(&group, lease)?;
            write_limit(&group.join("cgroup.procs"), pid.to_string())?;
            Ok(())
        }

        fn configure_group(
            &self,
            group: &Path,
            lease: &ResourceLease,
        ) -> Result<(), ResourceError> {
            write_limit(
                &group.join("memory.max"),
                lease.granted.host_memory_bytes.to_string(),
            )?;
            write_limit(
                &group.join("pids.max"),
                lease.granted.process_limit.to_string(),
            )?;
            let quota = u64::from(lease.granted.cpu_threads).saturating_mul(100_000);
            write_limit(&group.join("cpu.max"), format!("{quota} 100000"))
        }

        fn group_for(&self, lease: &ResourceLease) -> PathBuf {
            self.root.join(format!("aegis-{}", lease.lease_id))
        }
    }

    impl ResourceController for LinuxCgroupV2Controller {
        fn capabilities(&self) -> ResourceControlCapabilities {
            ResourceControlCapabilities {
                cpu: crate::resource::EnforcementLevel::KernelEnforced,
                memory: crate::resource::EnforcementLevel::KernelEnforced,
                memory_priority: crate::resource::EnforcementLevel::MeasurementOnly,
                process_count: crate::resource::EnforcementLevel::KernelEnforced,
                thread_count: crate::resource::EnforcementLevel::BestEffort,
                io: crate::resource::EnforcementLevel::MeasurementOnly,
                termination: if self.root.join("cgroup.kill").is_file() {
                    crate::resource::EnforcementLevel::KernelEnforced
                } else {
                    crate::resource::EnforcementLevel::BestEffort
                },
                backend: "linux-cgroup-v2".to_string(),
            }
        }

        fn sample(&self) -> ResourceUsageSample {
            let memory_limit = read_memory_limit(&self.root.join("memory.max"));
            let memory_current = read_u64(&self.root.join("memory.current"));
            let memory_available = memory_limit
                .zip(memory_current)
                .map(|(limit, current)| limit.saturating_sub(current));
            let pressure = fs::read_to_string(self.root.join("memory.pressure"))
                .map(|value| psi_memory_pressure(&value))
                .unwrap_or(false);
            ResourceUsageSample {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                sampled_at_ms: now_ms(),
                cpu_threads_active: 0,
                host_memory_bytes: memory_limit,
                host_memory_available_bytes: memory_available,
                queue_depth: 0,
                memory_pressure: pressure,
            }
        }

        fn create_scope(&self, lease: &ResourceLease) -> Result<ResourceScope, ResourceError> {
            let group = self.group_for(lease);
            fs::create_dir_all(&group).map_err(io_error)?;
            self.configure_group(&group, lease)?;
            Ok(ResourceScope {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                lease_id: lease.lease_id,
                backend: "linux-cgroup-v2".to_string(),
                enforcement: self.capabilities(),
            })
        }

        fn apply_to_process(&self, lease: &ResourceLease, pid: u32) -> Result<(), ResourceError> {
            Self::apply_to_process(self, lease, pid)
        }

        fn release_scope(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            let group = self.group_for(lease);
            if group.exists() {
                fs::remove_dir(&group).map_err(io_error)?;
            }
            Ok(())
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            let group = self.group_for(lease);
            if group.join("cgroup.kill").is_file() {
                write_limit(&group.join("cgroup.kill"), "1")
            } else {
                Err(ResourceError::UnsupportedControl(
                    "cgroup.kill is unavailable".to_string(),
                ))
            }
        }
    }

    fn write_limit(path: &Path, value: impl AsRef<str>) -> Result<(), ResourceError> {
        fs::write(path, value.as_ref()).map_err(io_error)
    }

    fn read_u64(path: &Path) -> Option<u64> {
        fs::read_to_string(path).ok()?.trim().parse().ok()
    }

    fn read_memory_limit(path: &Path) -> Option<u64> {
        let value = fs::read_to_string(path).ok()?;
        let value = value.trim();
        if value == "max" {
            return None;
        }
        value.parse().ok()
    }

    fn psi_memory_pressure(value: &str) -> bool {
        // PSI averages are percentages over the last ten seconds. A small
        // amount of "some" stall is normal background activity; require 1%
        // before entering the critical boolean contract, while any non-zero
        // "full" stall indicates that all non-idle work was stalled.
        const SOME_PRESSURE_PERCENT: f64 = 1.0;
        let mut some_avg10 = None;
        let mut full_avg10 = None;
        for line in value.lines() {
            let Some(kind) = line.split_whitespace().next() else {
                continue;
            };
            let avg10 = line.split_whitespace().find_map(|field| {
                field
                    .strip_prefix("avg10=")
                    .and_then(|raw| raw.parse::<f64>().ok())
                    .filter(|value| value.is_finite() && *value >= 0.0)
            });
            match kind {
                "some" => some_avg10 = avg10,
                "full" => full_avg10 = avg10,
                _ => {}
            }
        }
        full_avg10.is_some_and(|value| value > 0.0)
            || some_avg10.is_some_and(|value| value >= SOME_PRESSURE_PERCENT)
    }

    fn io_error(error: std::io::Error) -> ResourceError {
        ResourceError::UnsupportedControl(format!("cgroup operation failed: {error}"))
    }

    fn now_ms() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_millis() as u64)
            .unwrap_or(0)
    }

    pub use LinuxCgroupV2Controller as Controller;
}

#[cfg(target_os = "linux")]
pub use linux::Controller as LinuxCgroupV2Controller;

#[cfg(target_os = "windows")]
mod windows {
    use super::*;
    use crate::resource::{LeaseId, PortableResourceController};
    use parking_lot::Mutex;
    use std::collections::HashMap;
    use std::ffi::c_void;
    use std::ptr::null;
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
        JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject,
    };
    use windows_sys::Win32::System::Threading::{
        MEMORY_PRIORITY_INFORMATION, MEMORY_PRIORITY_VERY_LOW, OpenProcess,
        PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SET_INFORMATION, PROCESS_SET_QUOTA,
        PROCESS_TERMINATE, ProcessMemoryPriority, SetProcessInformation,
    };

    #[cfg(test)]
    use windows_sys::Win32::System::Threading::GetProcessInformation;

    #[derive(Debug)]
    pub struct WindowsJobObjectController {
        jobs: Mutex<HashMap<LeaseId, HANDLE>>,
    }

    impl WindowsJobObjectController {
        pub fn new() -> Result<Self, ResourceError> {
            Ok(Self {
                jobs: Mutex::new(HashMap::new()),
            })
        }

        fn create_job(lease: &ResourceLease) -> Result<HANDLE, ResourceError> {
            let handle = unsafe { CreateJobObjectW(null(), null()) };
            if handle.is_null() {
                return Err(ResourceError::UnsupportedControl(format!(
                    "CreateJobObjectW failed: {}",
                    unsafe { GetLastError() }
                )));
            }
            if let Err(error) = Self::configure_limits(handle, lease) {
                unsafe { CloseHandle(handle) };
                return Err(error);
            }
            Ok(handle)
        }

        fn configure_limits(handle: HANDLE, lease: &ResourceLease) -> Result<(), ResourceError> {
            let job_memory_limit =
                usize::try_from(lease.granted.host_memory_bytes).map_err(|_| {
                    ResourceError::UnsupportedControl(
                        "host memory limit does not fit the Windows process width".to_string(),
                    )
                })?;
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | JOB_OBJECT_LIMIT_JOB_MEMORY
                | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            limits.BasicLimitInformation.ActiveProcessLimit = lease.granted.process_limit;
            limits.JobMemoryLimit = job_memory_limit;
            let ok = unsafe {
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if ok == 0 {
                return Err(last_error("SetInformationJobObject"));
            }
            Ok(())
        }

        fn ensure_job(&self, lease: &ResourceLease) -> Result<(HANDLE, bool), ResourceError> {
            let mut jobs = self.jobs.lock();
            if let Some(handle) = jobs.get(&lease.lease_id).copied() {
                return Ok((handle, false));
            }
            let handle = Self::create_job(lease)?;
            jobs.insert(lease.lease_id, handle);
            Ok((handle, true))
        }

        pub fn apply_to_process(
            &self,
            lease: &ResourceLease,
            pid: u32,
        ) -> Result<(), ResourceError> {
            let (handle, created) = self.ensure_job(lease)?;
            let process = unsafe {
                OpenProcess(
                    PROCESS_SET_INFORMATION
                        | PROCESS_SET_QUOTA
                        | PROCESS_TERMINATE
                        | PROCESS_QUERY_LIMITED_INFORMATION,
                    0,
                    pid,
                )
            };
            if process.is_null() {
                if created {
                    let _ = self.release_scope(lease);
                }
                return Err(last_error("OpenProcess"));
            }
            let assigned = unsafe { AssignProcessToJobObject(handle, process) };
            let priority_result = if assigned != 0 {
                Self::apply_memory_priority(process, lease)
            } else {
                Ok(())
            };
            unsafe { CloseHandle(process) };
            if assigned == 0 {
                if created {
                    let _ = self.release_scope(lease);
                }
                return Err(last_error("AssignProcessToJobObject"));
            }
            if let Err(error) = priority_result {
                // Process memory priority is an OS reclaim-order hint, not a
                // correctness boundary. Keep the hard Job Object limits even
                // when an older policy, permission, or host configuration
                // declines this optional hint.
                tracing::debug!(
                    lease_id = lease.lease_id,
                    ?error,
                    "Windows memory-priority hint unavailable"
                );
            }
            Ok(())
        }

        fn apply_memory_priority(
            process: HANDLE,
            lease: &ResourceLease,
        ) -> Result<(), ResourceError> {
            if lease.priority != crate::resource::Priority::Background {
                return Ok(());
            }
            let information = MEMORY_PRIORITY_INFORMATION {
                MemoryPriority: MEMORY_PRIORITY_VERY_LOW,
            };
            let ok = unsafe {
                SetProcessInformation(
                    process,
                    ProcessMemoryPriority,
                    (&information as *const MEMORY_PRIORITY_INFORMATION).cast::<c_void>(),
                    std::mem::size_of::<MEMORY_PRIORITY_INFORMATION>() as u32,
                )
            };
            if ok == 0 {
                return Err(last_error("SetProcessInformation(ProcessMemoryPriority)"));
            }
            Ok(())
        }
    }

    impl Drop for WindowsJobObjectController {
        fn drop(&mut self) {
            let jobs = std::mem::take(&mut *self.jobs.lock());
            for handle in jobs.into_values() {
                if !handle.is_null() {
                    unsafe { CloseHandle(handle) };
                }
            }
        }
    }

    // Job handles are kernel objects; the map is protected by `jobs` and each
    // lease receives an independent job so termination cannot affect siblings.
    unsafe impl Send for WindowsJobObjectController {}
    unsafe impl Sync for WindowsJobObjectController {}

    impl ResourceController for WindowsJobObjectController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            ResourceControlCapabilities {
                cpu: crate::resource::EnforcementLevel::BestEffort,
                memory: crate::resource::EnforcementLevel::KernelEnforced,
                memory_priority: crate::resource::EnforcementLevel::BestEffort,
                process_count: crate::resource::EnforcementLevel::KernelEnforced,
                thread_count: crate::resource::EnforcementLevel::BestEffort,
                io: crate::resource::EnforcementLevel::MeasurementOnly,
                termination: crate::resource::EnforcementLevel::KernelEnforced,
                backend: "windows-job-object".to_string(),
            }
        }

        fn sample(&self) -> ResourceUsageSample {
            // Job Objects provide enforcement/accounting for attached
            // processes, while host pressure comes from the portable probe.
            // Returning the latter keeps adaptive admission useful on
            // Windows instead of silently treating memory as unknown.
            PortableResourceController.sample()
        }

        fn create_scope(&self, lease: &ResourceLease) -> Result<ResourceScope, ResourceError> {
            let mut jobs = self.jobs.lock();
            if jobs.contains_key(&lease.lease_id) {
                return Err(ResourceError::UnsupportedControl(
                    "resource scope already exists for lease".to_string(),
                ));
            }
            let handle = Self::create_job(lease)?;
            jobs.insert(lease.lease_id, handle);
            Ok(ResourceScope {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                lease_id: lease.lease_id,
                backend: "windows-job-object".to_string(),
                enforcement: self.capabilities(),
            })
        }

        fn apply_to_process(&self, lease: &ResourceLease, pid: u32) -> Result<(), ResourceError> {
            Self::apply_to_process(self, lease, pid)
        }

        fn release_scope(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            let handle = self.jobs.lock().remove(&lease.lease_id);
            if let Some(handle) = handle {
                let ok = unsafe { CloseHandle(handle) };
                if ok == 0 {
                    return Err(last_error("CloseHandle"));
                }
            }
            Ok(())
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            let handle = self
                .jobs
                .lock()
                .get(&lease.lease_id)
                .copied()
                .ok_or(ResourceError::UnknownLease(lease.lease_id))?;
            let ok = unsafe { TerminateJobObject(handle, 1) };
            if ok == 0 {
                return Err(last_error("TerminateJobObject"));
            }
            Ok(())
        }
    }

    fn last_error(operation: &str) -> ResourceError {
        ResourceError::UnsupportedControl(format!("{operation} failed: {}", unsafe {
            GetLastError()
        }))
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use crate::resource::{
            AdmissionController, AdmissionDecision, HardwareProfile, Priority, WorkKind,
        };
        use std::process::{Command, Stdio};

        #[test]
        fn job_object_scopes_are_isolated_per_lease() {
            let profile = HardwareProfile::probe();
            let mut admission = AdmissionController::from_hardware(&profile);
            let request = |task_id| {
                let mut request =
                    crate::resource::ResourceRequest::minimal(task_id, WorkKind::NativeTask);
                request.host_memory.bytes = 64 * 1024 * 1024;
                request
            };
            let first = match admission.admit(request(1), 1) {
                AdmissionDecision::Admitted(lease) => lease,
                other => panic!("expected first lease, got {other:?}"),
            };
            let second = match admission.admit(request(2), 1) {
                AdmissionDecision::Admitted(lease) => lease,
                other => panic!("expected second lease, got {other:?}"),
            };

            let controller = WindowsJobObjectController::new().expect("controller");
            controller.create_scope(&first).expect("first scope");
            controller.create_scope(&second).expect("second scope");
            let handles = controller.jobs.lock();
            assert_eq!(handles.len(), 2);
            assert_ne!(handles.get(&first.lease_id), handles.get(&second.lease_id));
            drop(handles);

            assert!(matches!(
                controller.create_scope(&first),
                Err(ResourceError::UnsupportedControl(_))
            ));
            controller
                .release_scope(&first)
                .expect("release first scope");
            controller
                .release_scope(&second)
                .expect("release second scope");
        }

        #[test]
        fn background_process_gets_memory_reclaim_hint_when_windows_allows_it() {
            let profile = HardwareProfile::probe();
            let mut admission = AdmissionController::from_hardware(&profile);
            let mut request = crate::resource::ResourceRequest::minimal(3, WorkKind::NativeTask);
            request.host_memory.bytes = 64 * 1024 * 1024;
            request.priority = Priority::Background;
            let lease = match admission.admit(request, 1) {
                AdmissionDecision::Admitted(lease) => lease,
                other => panic!("expected background lease, got {other:?}"),
            };

            let controller = WindowsJobObjectController::new().expect("controller");
            controller.create_scope(&lease).expect("scope");
            let mut child = Command::new("ping")
                .args(["-n", "3", "127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("ping child");
            controller
                .apply_to_process(&lease, child.id())
                .expect("attach background child");

            let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, child.id()) };
            assert!(!process.is_null());
            let mut information = MEMORY_PRIORITY_INFORMATION { MemoryPriority: 0 };
            let queried = unsafe {
                GetProcessInformation(
                    process,
                    ProcessMemoryPriority,
                    (&mut information as *mut MEMORY_PRIORITY_INFORMATION).cast::<c_void>(),
                    std::mem::size_of::<MEMORY_PRIORITY_INFORMATION>() as u32,
                )
            };
            unsafe { CloseHandle(process) };
            assert_ne!(queried, 0);
            assert_eq!(information.MemoryPriority, MEMORY_PRIORITY_VERY_LOW);

            controller.release_scope(&lease).expect("release scope");
            let _ = child.wait();
        }
    }
}

#[cfg(target_os = "windows")]
pub use windows::WindowsJobObjectController;

#[cfg(target_os = "macos")]
mod macos {
    use super::{
        ResourceControlCapabilities, ResourceController, ResourceError, ResourceLease,
        ResourceUsageSample,
    };
    use crate::resource::PortableResourceController;

    /// macOS has no equivalent of the Linux cgroup v2 or Windows Job Object
    /// contract used by this runtime slice. This adapter exposes cooperative
    /// cancellation and measurement-only capability levels explicitly.
    #[derive(Clone, Debug, Default)]
    pub struct MacosCooperativeController;

    impl ResourceController for MacosCooperativeController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            ResourceControlCapabilities::for_current_platform()
        }

        fn sample(&self) -> ResourceUsageSample {
            PortableResourceController.sample()
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            lease.cancel();
            Ok(())
        }
    }
}

#[cfg(target_os = "macos")]
pub use macos::MacosCooperativeController;

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn cgroup_controller_requires_v2_files_and_applies_process_limits() {
        let root = tempfile::tempdir().unwrap();
        for file in ["memory.max", "memory.current", "cpu.max", "pids.max"] {
            fs::write(root.path().join(file), "max").unwrap();
        }
        fs::write(
            root.path().join("memory.pressure"),
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0",
        )
        .unwrap();
        let controller = LinuxCgroupV2Controller::try_new(root.path()).unwrap();
        assert_eq!(controller.capabilities().backend, "linux-cgroup-v2");
        let profile = crate::resource::HardwareProfile::probe();
        let mut admission = crate::resource::AdmissionController::from_hardware(&profile);
        let request =
            crate::resource::ResourceRequest::minimal(1, crate::resource::WorkKind::NativeTask);
        let lease = match admission.admit(request, 1) {
            crate::resource::AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected lease, got {other:?}"),
        };
        controller.apply_to_process(&lease, 42).unwrap();
        assert!(
            fs::read_to_string(root.path().join("aegis-1/memory.max"))
                .unwrap()
                .parse::<u64>()
                .is_ok()
        );
        assert_eq!(
            fs::read_to_string(root.path().join("aegis-1/cgroup.procs")).unwrap(),
            "42"
        );
        controller.release_scope(&lease).unwrap();
        assert!(!root.path().join("aegis-1").exists());
    }

    #[test]
    fn cgroup_capabilities_reflect_kernel_kill_support() {
        let root = tempfile::tempdir().unwrap();
        for file in ["memory.max", "memory.current", "cpu.max", "pids.max"] {
            fs::write(root.path().join(file), "max").unwrap();
        }
        let controller = LinuxCgroupV2Controller::try_new(root.path()).unwrap();
        assert_eq!(
            controller.capabilities().termination,
            crate::resource::EnforcementLevel::BestEffort
        );
        fs::write(root.path().join("cgroup.kill"), "").unwrap();
        assert_eq!(
            controller.capabilities().termination,
            crate::resource::EnforcementLevel::KernelEnforced
        );
    }

    #[test]
    fn cgroup_sample_uses_memory_limit_and_current_as_capacity_and_available() {
        let root = tempfile::tempdir().unwrap();
        for file in ["cpu.max", "pids.max"] {
            fs::write(root.path().join(file), "max").unwrap();
        }
        fs::write(root.path().join("memory.max"), "1000").unwrap();
        fs::write(root.path().join("memory.current"), "650").unwrap();
        fs::write(
            root.path().join("memory.pressure"),
            "some avg10=0.50 avg60=0.10 avg300=0.01 total=10\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0",
        )
        .unwrap();

        let controller = LinuxCgroupV2Controller::try_new(root.path()).unwrap();
        let sample = controller.sample();
        assert_eq!(sample.host_memory_bytes, Some(1_000));
        assert_eq!(sample.host_memory_available_bytes, Some(350));
        assert!(!sample.memory_pressure);
    }

    #[test]
    fn psi_memory_pressure_requires_meaningful_some_or_any_full_stall() {
        assert!(!psi_memory_pressure(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0"
        ));
        assert!(!psi_memory_pressure(
            "some avg10=0.99 avg60=0.00 avg300=0.00 total=0"
        ));
        assert!(psi_memory_pressure(
            "some avg10=1.00 avg60=0.00 avg300=0.00 total=0"
        ));
        assert!(psi_memory_pressure(
            "some avg10=0.01 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.01 avg60=0.00 avg300=0.00 total=1"
        ));
        assert!(!psi_memory_pressure("some avg10=not-a-number"));
    }
}
