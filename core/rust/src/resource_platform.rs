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
fn ensure_native_lease_budget(lease: &ResourceLease) -> Result<(), ResourceError> {
    lease
        .remaining_execution_budget(crate::resource::unix_time_millis_for_runtime())
        .map(|_| ())
}

#[cfg(any(target_os = "linux", target_os = "windows"))]
use crate::resource::ResourceScope;

#[cfg(target_os = "linux")]
mod linux {
    use super::{
        RESOURCE_CONTRACT_SCHEMA_V1, ResourceControlCapabilities, ResourceController,
        ResourceError, ResourceLease, ResourceScope, ResourceUsageSample,
        ensure_native_lease_budget,
    };
    use parking_lot::Mutex;
    use std::collections::HashMap;
    use std::ffi::CString;
    use std::fs::{File, OpenOptions};
    use std::io::{self, Read, Write};
    use std::os::fd::{AsRawFd, FromRawFd, RawFd};
    use std::os::unix::fs::OpenOptionsExt;
    use std::path::{Path, PathBuf};
    use std::sync::Arc;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_SCOPE_ID: AtomicU64 = AtomicU64::new(1);

    #[derive(Debug)]
    struct ActiveScope {
        name: String,
        directory: Arc<File>,
    }

    #[derive(Clone, Debug)]
    pub struct LinuxCgroupV2Controller {
        root: PathBuf,
        root_directory: Arc<File>,
        scopes: Arc<Mutex<HashMap<crate::resource::LeaseId, ActiveScope>>>,
    }

    impl LinuxCgroupV2Controller {
        pub fn try_new(root: impl AsRef<Path>) -> Result<Self, ResourceError> {
            let root = root.as_ref().to_path_buf();
            let root_directory = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW)
                .open(&root)
                .map_err(io_error)?;
            verify_cgroup2(root_directory.as_raw_fd())?;
            for file in [
                "memory.max",
                "memory.current",
                "cpu.max",
                "pids.max",
                "cgroup.procs",
                "cgroup.events",
                "cgroup.subtree_control",
            ] {
                open_control(&root_directory, file, libc::O_RDONLY).map_err(|error| {
                    ResourceError::UnsupportedControl(format!(
                        "cgroup v2 required control {file} is unavailable: {error:?}"
                    ))
                })?;
            }
            Ok(Self {
                root,
                root_directory: Arc::new(root_directory),
                scopes: Arc::new(Mutex::new(HashMap::new())),
            })
        }

        pub fn root(&self) -> &Path {
            &self.root
        }

        pub fn scope_path(&self, lease: &ResourceLease) -> Result<PathBuf, ResourceError> {
            self.scopes
                .lock()
                .get(&lease.lease_id)
                .map(|scope| self.root.join(&scope.name))
                .ok_or(ResourceError::UnknownLease(lease.lease_id))
        }

        pub fn apply_to_process(
            &self,
            lease: &ResourceLease,
            pid: u32,
        ) -> Result<(), ResourceError> {
            let scope = self.scope(lease)?;
            let processes = open_control(&scope.directory, "cgroup.procs", libc::O_WRONLY)?;
            write_file(&processes, pid.to_string().as_bytes())
        }

        fn configure_group(
            &self,
            group: &File,
            lease: &ResourceLease,
        ) -> Result<(), ResourceError> {
            let memory = open_control(group, "memory.max", libc::O_WRONLY)?;
            write_file(
                &memory,
                lease.granted.host_memory_bytes.to_string().as_bytes(),
            )?;
            let processes = open_control(group, "pids.max", libc::O_WRONLY)?;
            write_file(
                &processes,
                lease.granted.process_limit.to_string().as_bytes(),
            )?;
            let quota = u64::from(lease.granted.cpu_threads).saturating_mul(100_000);
            let cpu = open_control(group, "cpu.max", libc::O_WRONLY)?;
            write_file(&cpu, format!("{quota} 100000").as_bytes())
        }

        fn enabled_controllers(&self) -> Option<String> {
            let mut file = open_control(
                &self.root_directory,
                "cgroup.subtree_control",
                libc::O_RDONLY,
            )
            .ok()?;
            let mut enabled = String::new();
            file.read_to_string(&mut enabled).ok()?;
            Some(enabled)
        }

        fn group_for(&self, lease: &ResourceLease) -> PathBuf {
            let id = NEXT_SCOPE_ID.fetch_add(1, Ordering::Relaxed);
            self.root.join(format!(
                "aegis-{}-{}-{id}",
                std::process::id(),
                lease.lease_id
            ))
        }

        fn scope(&self, lease: &ResourceLease) -> Result<ActiveScope, ResourceError> {
            self.scopes
                .lock()
                .get(&lease.lease_id)
                .map(|scope| ActiveScope {
                    name: scope.name.clone(),
                    directory: Arc::clone(&scope.directory),
                })
                .ok_or(ResourceError::UnknownLease(lease.lease_id))
        }
    }

    impl ResourceController for LinuxCgroupV2Controller {
        fn capabilities(&self) -> ResourceControlCapabilities {
            let enabled = self.enabled_controllers().unwrap_or_default();
            let enabled = |controller: &str| {
                enabled
                    .split_whitespace()
                    .any(|candidate| candidate == controller)
            };
            ResourceControlCapabilities {
                cpu: if enabled("cpu") {
                    crate::resource::EnforcementLevel::KernelEnforced
                } else {
                    crate::resource::EnforcementLevel::Unsupported
                },
                memory: if enabled("memory") {
                    crate::resource::EnforcementLevel::KernelEnforced
                } else {
                    crate::resource::EnforcementLevel::Unsupported
                },
                memory_priority: crate::resource::EnforcementLevel::MeasurementOnly,
                process_count: if enabled("pids") {
                    crate::resource::EnforcementLevel::KernelEnforced
                } else {
                    crate::resource::EnforcementLevel::Unsupported
                },
                thread_count: crate::resource::EnforcementLevel::BestEffort,
                io: crate::resource::EnforcementLevel::MeasurementOnly,
                termination: if open_control(&self.root_directory, "cgroup.kill", libc::O_WRONLY)
                    .is_ok()
                {
                    crate::resource::EnforcementLevel::KernelEnforced
                } else {
                    crate::resource::EnforcementLevel::BestEffort
                },
                backend: "linux-cgroup-v2".to_string(),
            }
        }

        fn sample(&self) -> ResourceUsageSample {
            let memory_limit = read_control(&self.root_directory, "memory.max")
                .as_deref()
                .and_then(parse_memory_limit);
            let memory_current = read_control(&self.root_directory, "memory.current")
                .as_deref()
                .and_then(parse_u64);
            let memory_available = memory_available(memory_limit, memory_current);
            let pressure = read_control(&self.root_directory, "memory.pressure")
                .is_some_and(|value| psi_memory_pressure(&value));
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
            ensure_native_lease_budget(lease)?;
            let mut scopes = self.scopes.lock();
            if scopes.contains_key(&lease.lease_id) {
                return Err(ResourceError::UnsupportedControl(
                    "cgroup scope already exists for this lease".to_string(),
                ));
            }
            let capabilities = self.capabilities();
            if capabilities.cpu != crate::resource::EnforcementLevel::KernelEnforced
                || capabilities.memory != crate::resource::EnforcementLevel::KernelEnforced
                || capabilities.process_count != crate::resource::EnforcementLevel::KernelEnforced
            {
                return Err(ResourceError::UnsupportedControl(
                    "cgroup v2 cpu, memory, and pids controllers must be enabled for child scopes"
                        .to_string(),
                ));
            }
            let group = self.group_for(lease);
            let name = group
                .file_name()
                .and_then(|value| value.to_str())
                .ok_or_else(|| {
                    ResourceError::InvalidRequest("invalid cgroup scope name".to_string())
                })?;
            let name = CString::new(name).map_err(|error| {
                ResourceError::InvalidRequest(format!("invalid cgroup scope name: {error}"))
            })?;
            if unsafe { libc::mkdirat(self.root_directory.as_raw_fd(), name.as_ptr(), 0o755) } == -1
            {
                return Err(io_error(io::Error::last_os_error()));
            }
            let directory = match open_at(
                self.root_directory.as_raw_fd(),
                &name,
                libc::O_RDONLY | libc::O_DIRECTORY,
            ) {
                Ok(directory) => Arc::new(directory),
                Err(error) => {
                    let _ = remove_group_at(self.root_directory.as_raw_fd(), &name);
                    return Err(io_error(error));
                }
            };
            if let Err(error) = self.configure_group(&directory, lease) {
                drop(directory);
                let _ = remove_group_at(self.root_directory.as_raw_fd(), &name);
                return Err(error);
            }
            scopes.insert(
                lease.lease_id,
                ActiveScope {
                    name: name.to_string_lossy().into_owned(),
                    directory,
                },
            );
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
            let mut scopes = self.scopes.lock();
            let Some(scope) = scopes.get(&lease.lease_id) else {
                return Ok(());
            };
            if !scope_is_empty(&scope.directory)? {
                return Err(ResourceError::ResourceUnavailable(
                    "cannot release a cgroup scope while it contains processes".to_string(),
                ));
            }
            let name = CString::new(scope.name.as_bytes()).map_err(|error| {
                ResourceError::InvalidRequest(format!("invalid cgroup scope name: {error}"))
            })?;
            remove_group_at(self.root_directory.as_raw_fd(), &name).map_err(io_error)?;
            scopes.remove(&lease.lease_id);
            Ok(())
        }

        fn spawn_in_scope(
            &self,
            lease: &ResourceLease,
            mut command: std::process::Command,
        ) -> Result<std::process::Child, ResourceError> {
            if lease.is_cancelled() {
                return Err(ResourceError::ResourceUnavailable(
                    "untrusted process lease was cancelled".to_string(),
                ));
            }
            ensure_native_lease_budget(lease)?;
            let scope = self.scope(lease)?;
            let processes = open_control(&scope.directory, "cgroup.procs", libc::O_WRONLY)?;
            let descriptor = processes.as_raw_fd();
            let child_lease = lease.clone();
            unsafe {
                use std::os::unix::process::CommandExt;
                command.pre_exec(move || {
                    if child_lease.is_cancelled() {
                        return Err(io::Error::from_raw_os_error(libc::ECANCELED));
                    }
                    ensure_pre_exec_lease_budget(&child_lease)?;
                    attach_current_process(descriptor)
                });
            }
            command.spawn().map_err(|error| {
                if error.raw_os_error() == Some(libc::ETIMEDOUT) {
                    ResourceError::DeadlineExceeded
                } else {
                    ResourceError::ResourceUnavailable(format!(
                        "cgroup-contained process spawn failed: {error}"
                    ))
                }
            })
        }

        fn scope_is_empty(&self, lease: &ResourceLease) -> Result<bool, ResourceError> {
            let scope = self.scope(lease)?;
            scope_is_empty(&scope.directory)
        }

        fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
            let scope = self.scope(lease)?;
            let kill = open_control(&scope.directory, "cgroup.kill", libc::O_WRONLY)?;
            write_file(&kill, b"1")
        }
    }

    fn ensure_pre_exec_lease_budget(lease: &ResourceLease) -> io::Result<()> {
        let Some(deadline) = lease.expires_at_ms else {
            return Ok(());
        };
        let mut now = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        if unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, &mut now) } != 0 {
            return Err(io::Error::last_os_error());
        }
        let now_ms = (now.tv_sec as u64)
            .saturating_mul(1_000)
            .saturating_add((now.tv_nsec as u64) / 1_000_000);
        if deadline.saturating_sub(now_ms) <= 1 {
            return Err(io::Error::from_raw_os_error(libc::ETIMEDOUT));
        }
        Ok(())
    }

    fn verify_cgroup2(descriptor: RawFd) -> Result<(), ResourceError> {
        let mut details = unsafe { std::mem::zeroed::<libc::statfs>() };
        if unsafe { libc::fstatfs(descriptor, &mut details) } == -1 {
            return Err(io_error(io::Error::last_os_error()));
        }
        if details.f_type as u64 != libc::CGROUP2_SUPER_MAGIC as u64 {
            return Err(ResourceError::UnsupportedControl(
                "resource controller root is not a cgroup v2 filesystem".to_string(),
            ));
        }
        Ok(())
    }

    fn open_at(directory: RawFd, name: &CString, flags: libc::c_int) -> io::Result<File> {
        let descriptor = unsafe {
            libc::openat(
                directory,
                name.as_ptr(),
                flags | libc::O_CLOEXEC | libc::O_NOFOLLOW,
            )
        };
        if descriptor == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok(unsafe { File::from_raw_fd(descriptor) })
    }

    fn open_control(
        directory: &File,
        name: &str,
        flags: libc::c_int,
    ) -> Result<File, ResourceError> {
        let name = CString::new(name).map_err(|error| {
            ResourceError::InvalidRequest(format!("invalid cgroup control name: {error}"))
        })?;
        let file = open_at(directory.as_raw_fd(), &name, flags).map_err(io_error)?;
        verify_cgroup2(file.as_raw_fd())?;
        Ok(file)
    }

    fn write_file(file: &File, bytes: &[u8]) -> Result<(), ResourceError> {
        let mut file = file;
        loop {
            match file.write(bytes) {
                Ok(count) if count == bytes.len() => return Ok(()),
                Ok(_) => return Err(io_error(io::Error::from_raw_os_error(libc::EIO))),
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(io_error(error)),
            }
        }
    }

    fn scope_is_empty(directory: &File) -> Result<bool, ResourceError> {
        let mut events = open_control(directory, "cgroup.events", libc::O_RDONLY).map_err(
            |error| match error {
                ResourceError::UnsupportedControl(reason) => {
                    ResourceError::ResourceUnavailable(reason)
                }
                other => other,
            },
        )?;
        let mut content = String::new();
        events.read_to_string(&mut content).map_err(io_error)?;
        parse_scope_is_empty(&content)
    }

    pub(super) fn parse_scope_is_empty(content: &str) -> Result<bool, ResourceError> {
        let mut populated = None;
        for line in content.lines() {
            let mut fields = line.split_whitespace();
            let Some(key) = fields.next() else {
                continue;
            };
            if key != "populated" {
                continue;
            }
            let value = fields.next();
            if fields.next().is_some() {
                return Err(ResourceError::ResourceUnavailable(
                    "cgroup.events has a malformed population entry".to_string(),
                ));
            }
            if populated.is_some() {
                return Err(ResourceError::ResourceUnavailable(
                    "cgroup.events has duplicate population entries".to_string(),
                ));
            }
            populated = Some(match value.unwrap_or_default() {
                "0" => true,
                "1" => false,
                _ => {
                    return Err(ResourceError::ResourceUnavailable(
                        "cgroup.events has an invalid population value".to_string(),
                    ));
                }
            });
        }
        populated.ok_or_else(|| {
            ResourceError::ResourceUnavailable(
                "cgroup.events does not report process population".to_string(),
            )
        })
    }

    fn remove_group_at(root: RawFd, name: &CString) -> io::Result<()> {
        if unsafe { libc::unlinkat(root, name.as_ptr(), libc::AT_REMOVEDIR) } == -1 {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }

    fn attach_current_process(processes_fd: RawFd) -> io::Result<()> {
        loop {
            let written = unsafe { libc::write(processes_fd, b"0".as_ptr().cast(), 1) };
            if written == 1 {
                return Ok(());
            }
            if written == -1 && io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(if written == -1 {
                io::Error::last_os_error()
            } else {
                io::Error::from_raw_os_error(libc::EIO)
            });
        }
    }

    fn read_control(directory: &File, name: &str) -> Option<String> {
        let mut file = open_control(directory, name, libc::O_RDONLY).ok()?;
        let mut value = String::new();
        file.read_to_string(&mut value).ok()?;
        Some(value)
    }

    pub(super) fn parse_u64(value: &str) -> Option<u64> {
        value.trim().parse().ok()
    }

    pub(super) fn parse_memory_limit(value: &str) -> Option<u64> {
        let value = value.trim();
        if value == "max" {
            return None;
        }
        value.parse().ok()
    }

    pub(super) fn memory_available(limit: Option<u64>, current: Option<u64>) -> Option<u64> {
        limit
            .zip(current)
            .map(|(limit, current)| limit.saturating_sub(current))
    }

    pub(super) fn psi_memory_pressure(value: &str) -> bool {
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
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::os::windows::process::CommandExt;
    use std::process::{Child, Command};
    use std::ptr::null;
    use windows_sys::Win32::Foundation::{
        CloseHandle, ERROR_NO_MORE_FILES, GetLastError, HANDLE, INVALID_HANDLE_VALUE, WAIT_FAILED,
        WAIT_OBJECT_0, WAIT_TIMEOUT,
    };
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
    };
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
        JOB_OBJECT_LIMIT_JOB_MEMORY, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
        QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
    };
    use windows_sys::Win32::System::Threading::{
        CREATE_NO_WINDOW, CREATE_SUSPENDED, GetProcessIdOfThread, MEMORY_PRIORITY_INFORMATION,
        MEMORY_PRIORITY_VERY_LOW, OpenProcess, OpenThread, PROCESS_QUERY_LIMITED_INFORMATION,
        PROCESS_SET_INFORMATION, PROCESS_SET_QUOTA, PROCESS_TERMINATE, ProcessMemoryPriority,
        ResumeThread, SetProcessInformation, THREAD_QUERY_LIMITED_INFORMATION,
        THREAD_SUSPEND_RESUME, WaitForSingleObject,
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

        fn ensure_job(
            jobs: &mut HashMap<LeaseId, HANDLE>,
            lease: &ResourceLease,
        ) -> Result<(HANDLE, bool), ResourceError> {
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
            #[cfg(test)]
            {
                self.apply_to_process_with_before_assign(lease, pid, || {})
            }
            #[cfg(not(test))]
            {
                self.apply_to_process_with_before_assign(lease, pid)
            }
        }

        fn apply_to_process_with_before_assign(
            &self,
            lease: &ResourceLease,
            pid: u32,
            #[cfg(test)] before_assign: impl FnOnce(),
        ) -> Result<(), ResourceError> {
            // Keep the registry lock until assignment is complete so release_scope
            // cannot close or recycle this kernel handle during attachment.
            let mut jobs = self.jobs.lock();
            let (handle, created) = Self::ensure_job(&mut jobs, lease)?;
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
                    if let Some(handle) = jobs.remove(&lease.lease_id) {
                        unsafe { CloseHandle(handle) };
                    }
                }
                return Err(last_error("OpenProcess"));
            }
            #[cfg(test)]
            before_assign();
            let assigned = unsafe { AssignProcessToJobObject(handle, process) };
            let assignment_error = (assigned == 0).then(|| last_error("AssignProcessToJobObject"));
            let priority_result = if assigned != 0 {
                Self::apply_memory_priority(process, lease)
            } else {
                Ok(())
            };
            unsafe { CloseHandle(process) };
            if let Some(error) = assignment_error {
                if created {
                    if let Some(handle) = jobs.remove(&lease.lease_id) {
                        unsafe { CloseHandle(handle) };
                    }
                }
                return Err(error);
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
            ensure_native_lease_budget(lease)?;
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

        fn spawn_in_scope(
            &self,
            lease: &ResourceLease,
            mut command: Command,
        ) -> Result<Child, ResourceError> {
            if lease.is_cancelled() {
                return Err(ResourceError::ResourceUnavailable(
                    "untrusted process lease was cancelled".to_string(),
                ));
            }
            ensure_native_lease_budget(lease)?;
            command.creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW);
            let mut child = command.spawn().map_err(|error| {
                ResourceError::ResourceUnavailable(format!(
                    "suspended process spawn failed: {error}"
                ))
            })?;
            let attached = (|| {
                // Scope release cannot close or reuse the job handle between
                // assignment and resuming the still-suspended primary thread.
                let jobs = self.jobs.lock();
                let job = jobs
                    .get(&lease.lease_id)
                    .copied()
                    .ok_or(ResourceError::UnknownLease(lease.lease_id))?;
                if unsafe { AssignProcessToJobObject(job, child.as_raw_handle()) } == 0 {
                    return Err(last_error("AssignProcessToJobObject"));
                }
                let thread = suspended_primary_thread(child.id())?;
                if let Err(error) = Self::apply_memory_priority(child.as_raw_handle(), lease) {
                    tracing::debug!(
                        lease_id = lease.lease_id,
                        ?error,
                        "Windows memory-priority hint unavailable"
                    );
                }
                if child
                    .try_wait()
                    .map_err(|error| {
                        ResourceError::ResourceUnavailable(format!(
                            "suspended child wait failed: {error}"
                        ))
                    })?
                    .is_some()
                {
                    return Err(ResourceError::UnsupportedControl(
                        "suspended child exited before resume".to_string(),
                    ));
                }
                if lease.is_cancelled() {
                    return Err(ResourceError::ResourceUnavailable(
                        "untrusted process lease was cancelled before resume".to_string(),
                    ));
                }
                ensure_native_lease_budget(lease)?;
                let previous = unsafe { ResumeThread(thread.as_raw_handle()) };
                if previous == u32::MAX {
                    return Err(last_error("ResumeThread"));
                }
                if previous != 1 {
                    return Err(ResourceError::UnsupportedControl(format!(
                        "primary thread had unexpected suspend count {previous}"
                    )));
                }
                Ok(())
            })();
            match attached {
                Ok(()) => Ok(child),
                Err(error) => Err(reap_failed_spawn(child, error)),
            }
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
            let jobs = self.jobs.lock();
            let handle = jobs
                .get(&lease.lease_id)
                .copied()
                .ok_or(ResourceError::UnknownLease(lease.lease_id))?;
            let ok = unsafe { TerminateJobObject(handle, 1) };
            if ok == 0 {
                return Err(last_error("TerminateJobObject"));
            }
            Ok(())
        }

        fn scope_is_empty(&self, lease: &ResourceLease) -> Result<bool, ResourceError> {
            let jobs = self.jobs.lock();
            let handle = jobs
                .get(&lease.lease_id)
                .copied()
                .ok_or(ResourceError::UnknownLease(lease.lease_id))?;
            let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
            let ok = unsafe {
                QueryInformationJobObject(
                    handle,
                    JobObjectBasicAccountingInformation,
                    (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                    std::mem::size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                    std::ptr::null_mut(),
                )
            };
            if ok == 0 {
                return Err(last_error("QueryInformationJobObject"));
            }
            if accounting.ActiveProcesses != 0 {
                return Ok(false);
            }
            if accounting.TotalProcesses == 0 {
                return Ok(true);
            }
            match unsafe { WaitForSingleObject(handle, 0) } {
                WAIT_OBJECT_0 => Ok(true),
                WAIT_TIMEOUT => Ok(false),
                WAIT_FAILED => Err(last_error("WaitForSingleObject(job)")),
                _ => Ok(false),
            }
        }
    }

    fn last_error(operation: &str) -> ResourceError {
        ResourceError::UnsupportedControl(format!("{operation} failed: {}", unsafe {
            GetLastError()
        }))
    }

    fn suspended_primary_thread(pid: u32) -> Result<OwnedHandle, ResourceError> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(last_error("CreateToolhelp32Snapshot"));
        }
        // The successful API transfers this unique handle to the caller.
        let snapshot = unsafe { OwnedHandle::from_raw_handle(snapshot) };
        let mut entry = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        let mut found = None;
        let mut available = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) };
        while available != 0 {
            if entry.dwSize < std::mem::size_of::<THREADENTRY32>() as u32 {
                return Err(ResourceError::UnsupportedControl(
                    "incomplete Windows thread snapshot entry".to_string(),
                ));
            }
            if entry.th32OwnerProcessID == pid && found.replace(entry.th32ThreadID).is_some() {
                return Err(ResourceError::UnsupportedControl(
                    "suspended child has more than one thread".to_string(),
                ));
            }
            available = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) };
        }
        let error = unsafe { GetLastError() };
        if error != ERROR_NO_MORE_FILES {
            return Err(ResourceError::UnsupportedControl(format!(
                "Windows thread enumeration failed: {error}"
            )));
        }
        let thread_id = found.ok_or_else(|| {
            ResourceError::UnsupportedControl("suspended child thread not found".to_string())
        })?;
        let thread = unsafe {
            OpenThread(
                THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
                0,
                thread_id,
            )
        };
        if thread.is_null() {
            return Err(last_error("OpenThread"));
        }
        let thread = unsafe { OwnedHandle::from_raw_handle(thread) };
        let owner = unsafe { GetProcessIdOfThread(thread.as_raw_handle()) };
        if owner == 0 {
            return Err(last_error("GetProcessIdOfThread"));
        }
        if owner != pid {
            return Err(ResourceError::UnsupportedControl(
                "suspended child thread ownership changed".to_string(),
            ));
        }
        Ok(thread)
    }

    fn reap_failed_spawn(mut child: Child, primary: ResourceError) -> ResourceError {
        let killed = child.kill();
        // A failed termination must not turn cleanup into an unbounded wait.
        let waited = unsafe { WaitForSingleObject(child.as_raw_handle(), 5_000) };
        if waited == WAIT_OBJECT_0 {
            return match child.wait() {
                Ok(_) => primary,
                Err(error) => ResourceError::ResourceUnavailable(format!(
                    "{primary:?}; failed-spawn reap failed: {error}"
                )),
            };
        }
        let detail = if waited == WAIT_FAILED {
            format!("wait failed: {}", unsafe { GetLastError() })
        } else {
            "termination was not observed within five seconds".to_string()
        };
        ResourceError::ResourceUnavailable(format!(
            "{primary:?}; failed-spawn cleanup unconfirmed ({detail}); kill result: {killed:?}"
        ))
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use crate::resource::{
            AdmissionController, AdmissionDecision, HardwareProfile, Priority, WorkKind,
        };
        use std::os::windows::io::AsRawHandle;
        use std::process::{Command, Stdio};
        use windows_sys::Win32::System::JobObjects::IsProcessInJob;

        fn native_lease(task_id: u128) -> ResourceLease {
            let mut admission = AdmissionController::from_hardware(&HardwareProfile::probe());
            let mut request =
                crate::resource::ResourceRequest::minimal(task_id, WorkKind::NativeTask);
            request.host_memory.bytes = 64 * 1024 * 1024;
            request.process_limit = Some(1);
            match admission.admit(request, 1) {
                AdmissionDecision::Admitted(lease) => lease,
                other => panic!("expected native lease, got {other:?}"),
            }
        }

        #[test]
        fn contained_spawn_preserves_output_and_assigns_the_lease_job() {
            let lease = native_lease(4);
            let controller = WindowsJobObjectController::new().expect("controller");
            controller.create_scope(&lease).expect("scope");
            let mut command = Command::new("ping.exe");
            command
                .args(["-n", "2", "127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            let child = controller
                .spawn_in_scope(&lease, command)
                .expect("contained spawn must be supported");
            let mut contained = 0;
            let queried = unsafe {
                IsProcessInJob(
                    child.as_raw_handle().cast(),
                    controller.jobs.lock()[&lease.lease_id],
                    &mut contained,
                )
            };
            let output = child.wait_with_output().expect("child output");
            controller.release_scope(&lease).expect("scope cleanup");

            assert_ne!(queried, 0);
            assert_ne!(contained, 0);
            assert!(output.status.success());
            assert!(String::from_utf8_lossy(&output.stdout).contains("127.0.0.1"));
            assert!(output.stderr.is_empty());
            assert!(controller.jobs.lock().is_empty());
        }

        #[test]
        fn cancelled_lease_refuses_to_launch_a_process() {
            let lease = native_lease(7);
            let controller = WindowsJobObjectController::new().expect("controller");
            controller.create_scope(&lease).expect("scope");
            lease.cancel();
            assert!(
                lease.is_cancelled(),
                "the lease cancellation signal must be visible"
            );

            let mut command = Command::new("cmd.exe");
            command
                .args(["/D", "/C", "echo", "must-not-run"])
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            let result = controller.spawn_in_scope(&lease, command);
            let result_detail = format!("{result:?}");
            let refused = matches!(
                &result,
                Err(ResourceError::ResourceUnavailable(reason))
                    if reason.contains("lease was cancelled")
            );
            if let Ok(mut child) = result {
                let _ = child.kill();
                let _ = child.wait();
            }
            let empty = controller.scope_is_empty(&lease).expect("empty scope");
            controller.release_scope(&lease).expect("scope cleanup");

            assert!(
                refused,
                "a cancelled lease must be rejected before launch: {result_detail}"
            );
            assert!(empty, "cancellation must not leave a process in the job");
            assert!(controller.jobs.lock().is_empty());
        }

        #[test]
        fn exhausted_job_refuses_payload_without_stopping_its_existing_child() {
            let lease = native_lease(5);
            let controller = WindowsJobObjectController::new().expect("controller");
            controller.create_scope(&lease).expect("scope");
            let scratch_root = std::env::var_os("AEGIS_TEST_SCRATCH_ROOT")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| {
                    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                        .join("../../.local/tmp/evaluator")
                });
            std::fs::create_dir_all(&scratch_root).expect("scratch root");
            let scratch = tempfile::Builder::new()
                .prefix("windows-contained-spawn-")
                .tempdir_in(scratch_root)
                .expect("scratch");
            let marker = scratch.path().join("uncontained.txt");
            assert!(
                Command::new("cmd.exe")
                    .args(["/D", "/C", "echo", "uncontained"])
                    .stdout(std::fs::File::create(&marker).expect("control marker"))
                    .status()
                    .expect("marker oracle control")
                    .success()
            );
            assert!(
                std::fs::read(&marker).expect("control marker bytes") == b"uncontained\r\n",
                "the control payload must create its marker"
            );
            std::fs::remove_file(&marker).expect("remove control marker");
            let mut first_command = Command::new("ping.exe");
            first_command
                .args(["-n", "30", "127.0.0.1"])
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            let mut first = controller
                .spawn_in_scope(&lease, first_command)
                .expect("first contained child");
            let mut denied_command = Command::new("cmd.exe");
            denied_command
                .args(["/D", "/C", "echo", "uncontained"])
                .stdout(std::fs::File::create(&marker).expect("denied marker"))
                .stderr(Stdio::null());
            let denied = controller.spawn_in_scope(&lease, denied_command);
            let refused = matches!(&denied, Err(ResourceError::UnsupportedControl(reason))
                if reason.starts_with("AssignProcessToJobObject failed:"));
            if let Ok(mut unexpected) = denied {
                let _ = unexpected.kill();
                unexpected.wait().expect("unexpected child cleanup");
            }
            let first_survived = first.try_wait().expect("first child status").is_none();
            controller.release_scope(&lease).expect("scope cleanup");
            first.wait().expect("first child cleanup");

            assert!(refused, "a full job must refuse another process");
            assert!(
                std::fs::read(&marker)
                    .expect("denied marker bytes")
                    .is_empty(),
                "a refused payload must never execute"
            );
            assert!(first_survived, "a refused spawn must preserve its sibling");
            assert!(controller.jobs.lock().is_empty());
        }

        #[test]
        #[ignore = "subprocess fixture invoked by the controller memory-limit test"]
        fn memory_allocation_child_fixture() {
            let expected = std::env::var("AEGIS_JOB_MEMORY_PROBE")
                .expect("fixture requires an explicit allocation expectation");
            let mut bytes = Vec::<u8>::new();
            let allocated = bytes.try_reserve_exact(128 * 1024 * 1024).is_ok();
            std::hint::black_box(&bytes);
            match expected.as_str() {
                "allow" => assert!(allocated, "allocation control must succeed outside a job"),
                "deny" => assert!(!allocated, "the 64 MiB job must deny a 128 MiB allocation"),
                _ => panic!("unknown memory fixture expectation"),
            }
            println!("ALLOCATION_{expected}");
        }

        #[test]
        fn job_memory_limit_denies_allocation_through_the_actual_controller() {
            let executable = std::env::current_exe().expect("test executable");
            let command = |expected| {
                let mut command = Command::new(&executable);
                command
                    .args([
                        "--ignored",
                        "--exact",
                        "resource_platform::windows::tests::memory_allocation_child_fixture",
                        "--nocapture",
                    ])
                    .env("AEGIS_JOB_MEMORY_PROBE", expected)
                    .stdin(Stdio::null())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped());
                command
            };
            let control = command("allow").output().expect("allocation control");
            assert!(control.status.success(), "{control:?}");
            assert!(String::from_utf8_lossy(&control.stdout).contains("ALLOCATION_allow"));

            let lease = native_lease(6);
            let controller = WindowsJobObjectController::new().expect("controller");
            controller.create_scope(&lease).expect("scope");
            let child = controller
                .spawn_in_scope(&lease, command("deny"))
                .expect("contained allocation child");
            let output = child.wait_with_output().expect("allocation result");
            controller.release_scope(&lease).expect("scope cleanup");

            assert!(output.status.success(), "{output:?}");
            assert!(String::from_utf8_lossy(&output.stdout).contains("ALLOCATION_deny"));
            assert!(controller.jobs.lock().is_empty());
        }

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

        #[test]
        fn apply_to_process_cannot_race_scope_release() {
            use std::sync::{Arc, mpsc};
            use std::thread;
            use std::time::{Duration, Instant};

            let lease = native_lease(8);
            let controller = Arc::new(WindowsJobObjectController::new().expect("controller"));
            controller.create_scope(&lease).expect("scope");
            let mut child = Command::new("ping.exe")
                .args(["-n", "30", "127.0.0.1"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("long-running target process");

            let (reached_tx, reached_rx) = mpsc::channel();
            let (resume_tx, resume_rx) = mpsc::channel();
            let apply_controller = Arc::clone(&controller);
            let apply_lease = lease.clone();
            let pid = child.id();
            let worker = thread::spawn(move || {
                apply_controller.apply_to_process_with_before_assign(&apply_lease, pid, || {
                    let _ = reached_tx.send(());
                    let _ = resume_rx.recv_timeout(Duration::from_secs(5));
                })
            });

            let reached_assign = reached_rx.recv_timeout(Duration::from_secs(5)).is_ok();
            let release_would_overtake = if reached_assign {
                match controller.jobs.try_lock() {
                    Some(guard) => {
                        drop(guard);
                        controller.release_scope(&lease).is_ok()
                    }
                    None => false,
                }
            } else {
                false
            };
            let _ = resume_tx.send(());
            let applied = worker.join().ok().is_some_and(|result| result.is_ok());

            let released_after_apply = if release_would_overtake {
                true
            } else {
                controller.release_scope(&lease).is_ok()
            };
            let deadline = Instant::now() + Duration::from_secs(3);
            let terminated_by_scope_close = loop {
                match child.try_wait() {
                    Ok(Some(_)) => break true,
                    Ok(None) if Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(20));
                    }
                    _ => break false,
                }
            };
            if !terminated_by_scope_close {
                let _ = child.kill();
            }
            let _ = child.wait();

            assert!(reached_assign, "assignment hook must be reached");
            assert!(
                !release_would_overtake,
                "scope release must not close the Job Object before assignment"
            );
            assert!(applied, "target process must be assigned to its job");
            assert!(released_after_apply, "scope release must succeed");
            assert!(
                terminated_by_scope_close,
                "closing the released scope must terminate its assigned process"
            );
            assert!(controller.jobs.lock().is_empty());
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
    use std::path::{Path, PathBuf};

    #[test]
    fn cgroup_controller_rejects_regular_files_that_impersonate_cgroup_controls() {
        let root = scratch_directory();
        for file in ["memory.max", "memory.current", "cpu.max", "pids.max"] {
            fs::write(root.path().join(file), "max").unwrap();
        }
        let result = LinuxCgroupV2Controller::try_new(root.path());
        assert!(matches!(
            result,
            Err(ResourceError::UnsupportedControl(reason))
                if reason.contains("not a cgroup v2 filesystem")
        ));
    }

    #[test]
    fn cgroup_controller_rejects_a_symlinked_mount_root() {
        let root = scratch_directory();
        let alias = root.path().join("mount-alias");
        std::os::unix::fs::symlink(root.path(), &alias).expect("create mount alias");

        let result = LinuxCgroupV2Controller::try_new(alias);
        assert!(
            result.is_err(),
            "a symlink must not select the controller root"
        );
    }

    #[test]
    fn cgroup_population_parser_distinguishes_empty_live_and_invalid_scopes() {
        assert!(linux::parse_scope_is_empty("populated 0\nfrozen 0\n").unwrap());
        assert!(!linux::parse_scope_is_empty("frozen 0\npopulated 1\n").unwrap());
        assert!(linux::parse_scope_is_empty("populated\t0\n").unwrap());
        assert!(linux::parse_scope_is_empty("populated 2\n").is_err());
        assert!(linux::parse_scope_is_empty("populated 0\npopulated 1\n").is_err());
        assert!(linux::parse_scope_is_empty("frozen 0\n").is_err());
    }

    #[test]
    fn cgroup_memory_sample_parser_preserves_unlimited_and_saturating_semantics() {
        assert_eq!(linux::parse_memory_limit("1000\n"), Some(1_000));
        assert_eq!(linux::parse_memory_limit("max\n"), None);
        assert_eq!(linux::parse_memory_limit("invalid\n"), None);
        assert_eq!(linux::parse_u64("650\n"), Some(650));
        assert_eq!(linux::memory_available(Some(1_000), Some(650)), Some(350));
        assert_eq!(linux::memory_available(Some(1_000), Some(1_100)), Some(0));
        assert_eq!(linux::memory_available(None, Some(650)), None);
    }

    #[test]
    fn psi_memory_pressure_requires_meaningful_some_or_any_full_stall() {
        assert!(!linux::psi_memory_pressure(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0"
        ));
        assert!(!linux::psi_memory_pressure(
            "some avg10=0.99 avg60=0.00 avg300=0.00 total=0"
        ));
        assert!(linux::psi_memory_pressure(
            "some avg10=1.00 avg60=0.00 avg300=0.00 total=0"
        ));
        assert!(linux::psi_memory_pressure(
            "some avg10=0.01 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.01 avg60=0.00 avg300=0.00 total=1"
        ));
        assert!(!linux::psi_memory_pressure("some avg10=not-a-number"));
    }

    fn scratch_directory() -> tempfile::TempDir {
        let root = std::env::var_os("AEGIS_TEST_SCRATCH_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.local/tmp/evaluator")
            });
        fs::create_dir_all(&root).expect("create owned evaluator scratch root");
        tempfile::Builder::new()
            .prefix("linux-cgroup-contract-")
            .tempdir_in(root)
            .expect("create scoped cgroup test scratch")
    }
}
