#![cfg(target_os = "linux")]

use aegis_nerve::execution::ExecutionLanes;
use aegis_nerve::resource::{
    AdmissionController, AdmissionDecision, HardwareProfile, ResourceController, ResourceError,
    ResourceLease, ResourceRequest, WorkKind,
};
use aegis_nerve::resource_platform::LinuxCgroupV2Controller;
use serde_json::json;
use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const MEMORY_PRESSURE_CHILD: &str = r#"
import time
blocks = []
while True:
    block = bytearray(1024 * 1024)
    block[:] = b"x" * len(block)
    blocks.append(block)
    time.sleep(0.02)
"#;

const SLEEP_CHILD: &str = "import time; time.sleep(30)";

struct ChildGuard(Option<Child>);

impl ChildGuard {
    fn spawn(program: &Path, args: &[&str]) -> Result<Self, String> {
        Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map(|child| Self(Some(child)))
            .map_err(|error| format!("spawn failed: {error}"))
    }

    fn child(&self) -> &Child {
        self.0.as_ref().expect("child guard must contain a child")
    }

    fn child_mut(&mut self) -> &mut Child {
        self.0.as_mut().expect("child guard must contain a child")
    }

    fn wait(mut self) -> Result<ExitStatus, String> {
        self.0
            .take()
            .expect("child guard must contain a child")
            .wait()
            .map_err(|error| format!("wait failed: {error}"))
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let Some(child) = self.0.as_mut() else {
            return;
        };
        if child.try_wait().ok().flatten().is_none() {
            let _ = child.kill();
        }
        let _ = child.wait();
    }
}

struct ScopeGuard {
    controller: LinuxCgroupV2Controller,
    lease: ResourceLease,
}

impl Drop for ScopeGuard {
    fn drop(&mut self) {
        let _ = self.controller.terminate(&self.lease);
        let _ = self.controller.release_scope(&self.lease);
    }
}

fn cgroup_root() -> PathBuf {
    std::env::var_os("AEGIS_LINUX_CGROUP_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/sys/fs/cgroup/user.slice"))
}

fn python() -> &'static Path {
    Path::new("/usr/bin/python3")
}

fn read(path: &Path) -> Result<String, String> {
    fs::read_to_string(path)
        .map(|value| value.trim().to_string())
        .map_err(|error| format!("read {} failed: {error}", path.display()))
}

fn event_count(payload: &str, key: &str) -> u64 {
    payload
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(' ')?;
            (name == key)
                .then(|| value.trim().parse::<u64>().ok())
                .flatten()
        })
        .unwrap_or(0)
}

fn lease(task_id: u128, profile: &HardwareProfile) -> Result<ResourceLease, String> {
    let mut request = ResourceRequest::minimal(task_id, WorkKind::Tool);
    request.host_memory.bytes = 32 * 1024 * 1024;
    request.process_limit = Some(4);
    request.cpu.max_threads = 1;
    let mut admission = AdmissionController::from_hardware(profile);
    match admission.admit(request, 1) {
        AdmissionDecision::Admitted(lease) => Ok(lease),
        AdmissionDecision::Queued { reason, .. } => {
            Err(format!("unexpected queued lease: {reason}"))
        }
        AdmissionDecision::Rejected { reason } => Err(format!("lease rejected: {reason:?}")),
    }
}

fn native_probe() -> Result<serde_json::Value, String> {
    let root = cgroup_root();
    let controller = LinuxCgroupV2Controller::try_new(&root)
        .map_err(|error| format!("controller construction failed: {error:?}"))?;
    let profile = HardwareProfile::probe();
    let first_lease = lease(1, &profile)?;
    controller
        .create_scope(&first_lease)
        .map_err(|error| format!("native scope creation failed: {error:?}"))?;
    let first_group = root.join(format!("aegis-{}", first_lease.lease_id));
    let _scope_guard = ScopeGuard {
        controller: controller.clone(),
        lease: first_lease.clone(),
    };

    let mut pressure_child = ChildGuard::spawn(python(), &["-c", MEMORY_PRESSURE_CHILD])?;
    let pressure_pid = pressure_child.child().id();
    controller
        .apply_to_process(&first_lease, pressure_pid)
        .map_err(|error| format!("native process attachment failed: {error:?}"))?;
    let child_attached = read(&first_group.join("cgroup.procs"))?
        .lines()
        .any(|line| line.trim() == pressure_pid.to_string());
    let memory_limit = read(&first_group.join("memory.max"))?;
    let cpu_limit = read(&first_group.join("cpu.max"))?;
    let pids_limit = read(&first_group.join("pids.max"))?;
    let limits_configured = memory_limit == first_lease.granted.host_memory_bytes.to_string()
        && cpu_limit == "100000 100000"
        && pids_limit == first_lease.granted.process_limit.to_string();
    let before_memory = read(&first_group.join("memory.current"))?;
    let before_events = read(&first_group.join("memory.events"))?;
    let deadline = Instant::now() + Duration::from_secs(8);
    let mut memory_changed = false;
    let mut oom_kill_seen = false;
    while Instant::now() < deadline
        && pressure_child
            .child_mut()
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
    {
        let current_memory = read(&first_group.join("memory.current"))?;
        let events = read(&first_group.join("memory.events"))?;
        memory_changed |= current_memory != before_memory;
        oom_kill_seen |= event_count(&events, "oom_kill") > event_count(&before_events, "oom_kill");
        if oom_kill_seen {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let events = read(&first_group.join("memory.events"))?;
    oom_kill_seen |= event_count(&events, "oom_kill") > event_count(&before_events, "oom_kill");
    if pressure_child
        .child_mut()
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_none()
    {
        controller
            .terminate(&first_lease)
            .map_err(|error| format!("native cgroup.kill failed: {error:?}"))?;
    }
    let _pressure_status = pressure_child.wait()?;

    let mut deadline_child = ChildGuard::spawn(python(), &["-c", SLEEP_CHILD])?;
    let deadline_pid = deadline_child.child().id();
    controller
        .apply_to_process(&first_lease, deadline_pid)
        .map_err(|error| format!("second native process attachment failed: {error:?}"))?;
    thread::sleep(Duration::from_millis(500));
    let alive_at_deadline = deadline_child
        .child_mut()
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_none();
    controller
        .terminate(&first_lease)
        .map_err(|error| format!("native deadline cancellation failed: {error:?}"))?;
    let deadline_status = deadline_child.wait()?;
    let cgroup_kill_termination_observed = alive_at_deadline && !deadline_status.success();
    let deadline_cancellation_observed = alive_at_deadline && !deadline_status.success();

    let second_lease = lease(2, &profile)?;
    let lanes = ExecutionLanes::new(&profile)
        .map_err(|error| format!("execution lanes construction failed: {error:?}"))?;
    let output = lanes
        .run_untrusted_process(
            python().as_os_str(),
            &[OsStr::new("-c"), OsStr::new(SLEEP_CHILD)],
            &second_lease,
            &controller,
            Duration::from_millis(500),
        )
        .map_err(|error: ResourceError| format!("Lab process lane failed: {error:?}"))?;
    let lab_native_timeout_observed = output.timed_out && !output.status.success();

    Ok(json!({
        "schema": "aegis-linux-cgroup-v2-native-adapter-probe-v1",
        "status": "LIVE VERIFIED",
        "root": root,
        "checks": {
            "controller_constructed": true,
            "limits_configured": limits_configured,
            "child_attached": child_attached,
            "live_memory_accounting_observed": memory_changed,
            "memory_pressure_oom_kill_observed": oom_kill_seen,
            "cgroup_kill_termination_observed": cgroup_kill_termination_observed,
            "deadline_cancellation_observed": deadline_cancellation_observed,
            "lab_native_process_timeout_observed": lab_native_timeout_observed
        }
    }))
}

#[test]
#[ignore = "requires an explicit privileged Linux cgroup-v2 environment"]
fn linux_cgroup_native_adapter_live_controls() {
    let report =
        native_probe().unwrap_or_else(|error| panic!("native Linux cgroup probe failed: {error}"));
    println!(
        "{}",
        serde_json::to_string_pretty(&report).expect("probe report serializes")
    );
    let checks = report["checks"].as_object().expect("probe checks object");
    assert!(
        checks.values().all(|value| value == &json!(true)),
        "native probe checks: {report}"
    );
}
