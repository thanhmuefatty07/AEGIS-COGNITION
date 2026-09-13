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
                process_count: crate::resource::EnforcementLevel::KernelEnforced,
                thread_count: crate::resource::EnforcementLevel::BestEffort,
                io: crate::resource::EnforcementLevel::MeasurementOnly,
                termination: crate::resource::EnforcementLevel::KernelEnforced,
                backend: "linux-cgroup-v2".to_string(),
            }
        }

        fn sample(&self) -> ResourceUsageSample {
            let memory = read_u64(&self.root.join("memory.current"));
            let pressure = fs::read_to_string(self.root.join("memory.pressure"))
                .map(|value| value.contains("some avg10=\""))
                .unwrap_or(false);
            ResourceUsageSample {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                sampled_at_ms: now_ms(),
                cpu_threads_active: 0,
                host_memory_bytes: memory,
                host_memory_available_bytes: None,
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
    use std::ptr::null;
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOB_OBJECT_LIMIT_PROCESS_MEMORY, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JobObjectExtendedLimitInformation, SetInformationJobObject, TerminateJobObject,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    };

    #[derive(Debug)]
    pub struct WindowsJobObjectController {
        handle: HANDLE,
    }

    impl WindowsJobObjectController {
        pub fn new() -> Result<Self, ResourceError> {
            let handle = unsafe { CreateJobObjectW(null(), null()) };
            if handle.is_null() {
                return Err(ResourceError::UnsupportedControl(format!(
                    "CreateJobObjectW failed: {}",
                    unsafe { GetLastError() }
                )));
            }
            Ok(Self { handle })
        }

        fn configure_limits(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags =
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY;
            limits.ProcessMemoryLimit = lease.granted.host_memory_bytes as usize;
            let ok = unsafe {
                SetInformationJobObject(
                    self.handle,
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

        pub fn apply_to_process(
            &self,
            lease: &ResourceLease,
            pid: u32,
        ) -> Result<(), ResourceError> {
            self.configure_limits(lease)?;
            let process = unsafe {
                OpenProcess(
                    PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION,
                    0,
                    pid,
                )
            };
            if process.is_null() {
                return Err(last_error("OpenProcess"));
            }
            let assigned = unsafe { AssignProcessToJobObject(self.handle, process) };
            unsafe { CloseHandle(process) };
            if assigned == 0 {
                return Err(last_error("AssignProcessToJobObject"));
            }
            Ok(())
        }
    }

    impl Drop for WindowsJobObjectController {
        fn drop(&mut self) {
            if !self.handle.is_null() {
                unsafe { CloseHandle(self.handle) };
            }
        }
    }

    // The OS handle is a process-wide kernel object and all operations are
    // synchronized by the kernel. The controller is immutable after creation.
    unsafe impl Send for WindowsJobObjectController {}
    unsafe impl Sync for WindowsJobObjectController {}

    impl ResourceController for WindowsJobObjectController {
        fn capabilities(&self) -> ResourceControlCapabilities {
            ResourceControlCapabilities {
                cpu: crate::resource::EnforcementLevel::BestEffort,
                memory: crate::resource::EnforcementLevel::KernelEnforced,
                process_count: crate::resource::EnforcementLevel::BestEffort,
                thread_count: crate::resource::EnforcementLevel::BestEffort,
                io: crate::resource::EnforcementLevel::MeasurementOnly,
                termination: crate::resource::EnforcementLevel::KernelEnforced,
                backend: "windows-job-object".to_string(),
            }
        }

        fn sample(&self) -> ResourceUsageSample {
            ResourceUsageSample {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                sampled_at_ms: now_ms(),
                cpu_threads_active: 0,
                host_memory_bytes: None,
                host_memory_available_bytes: None,
                queue_depth: 0,
                memory_pressure: false,
            }
        }

        fn create_scope(&self, lease: &ResourceLease) -> Result<ResourceScope, ResourceError> {
            self.configure_limits(lease)?;
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

        fn terminate(&self, _lease: &ResourceLease) -> Result<(), ResourceError> {
            let ok = unsafe { TerminateJobObject(self.handle, 1) };
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

    fn now_ms() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_millis() as u64)
            .unwrap_or(0)
    }
}

#[cfg(target_os = "windows")]
pub use windows::WindowsJobObjectController;

#[cfg(target_os = "macos")]
mod macos {
    use super::{
        RESOURCE_CONTRACT_SCHEMA_V1, ResourceControlCapabilities, ResourceController,
        ResourceError, ResourceLease, ResourceUsageSample,
    };

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
            ResourceUsageSample {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                sampled_at_ms: now_ms(),
                cpu_threads_active: 0,
                host_memory_bytes: None,
                host_memory_available_bytes: None,
                queue_depth: 0,
                memory_pressure: false,
            }
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            lease.cancel();
            Ok(())
        }
    }

    fn now_ms() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_millis() as u64)
            .unwrap_or(0)
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
        fs::write(root.path().join("memory.pressure"), "some avg10=0.00").unwrap();
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
    }
}
