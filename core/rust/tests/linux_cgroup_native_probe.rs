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
use std::os::unix::process::ExitStatusExt;
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

const CPU_PRESSURE_CHILD: &str = r#"
import time
deadline = time.monotonic() + 1.0
while time.monotonic() < deadline:
    pass
"#;

const PID_PRESSURE_CHILD: &str = r#"
import errno, os, time
denied = False
for _ in range(8):
    try:
        pid = os.fork()
    except OSError as exc:
        if exc.errno != errno.EAGAIN:
            raise
        denied = True
        break
    if pid == 0:
        while True:
            time.sleep(1)
if denied:
    while True:
        time.sleep(1)
"#;

struct ChildGuard(Option<Child>);

impl ChildGuard {
    fn spawn(
        controller: &LinuxCgroupV2Controller,
        lease: &ResourceLease,
        program: &Path,
        args: &[&str],
    ) -> Result<Self, String> {
        let mut command = Command::new(program);
        command
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        controller
            .spawn_in_scope(lease, command)
            .map(|child| Self(Some(child)))
            .map_err(|error| format!("contained spawn failed: {error:?}"))
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
    released: bool,
}

impl ScopeGuard {
    fn close(&mut self) -> Result<(), String> {
        if self.released {
            return Ok(());
        }
        self.controller
            .terminate(&self.lease)
            .map_err(|error| format!("scope termination failed: {error:?}"))?;
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if self
                .controller
                .scope_is_empty(&self.lease)
                .map_err(|error| format!("scope population query failed: {error:?}"))?
            {
                break;
            }
            if Instant::now() >= deadline {
                return Err("scope remained populated after five seconds".to_string());
            }
            thread::sleep(Duration::from_millis(10));
        }
        self.controller
            .release_scope(&self.lease)
            .map_err(|error| format!("scope release failed: {error:?}"))?;
        self.released = true;
        Ok(())
    }
}

impl Drop for ScopeGuard {
    fn drop(&mut self) {
        let _ = self.close();
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

fn counter(payload: &str, key: &str) -> Option<u64> {
    let mut found = None;
    for line in payload.lines() {
        let mut fields = line.split_whitespace();
        if fields.next() != Some(key) {
            continue;
        }
        let value = fields.next()?.parse::<u64>().ok()?;
        if fields.next().is_some() || found.replace(value).is_some() {
            return None;
        }
    }
    found
}

fn counter_increased(before: &str, after: &str, key: &str) -> bool {
    matches!((counter(before, key), counter(after, key)), (Some(before), Some(after)) if after > before)
}

fn scalar(payload: &str) -> Option<u64> {
    payload.trim().parse::<u64>().ok()
}

fn cpu_quota_enforced(before: &str, after: &str) -> bool {
    ["usage_usec", "nr_periods", "nr_throttled", "throttled_usec"]
        .iter()
        .all(|key| counter_increased(before, after, key))
}

fn pids_limit_denial_observed(
    before_events: &str,
    after_events: &str,
    current: &str,
    limit: u64,
    child_alive: bool,
) -> bool {
    child_alive
        && counter_increased(before_events, after_events, "max")
        && scalar(current).is_some_and(|value| value >= limit)
}

fn descendant_tree_terminated(
    before_events: &str,
    after_events: &str,
    before_current: &str,
    after_current: &str,
) -> bool {
    counter(before_events, "populated") == Some(1)
        && counter(after_events, "populated") == Some(0)
        && scalar(before_current).is_some_and(|value| value > 1)
        && scalar(after_current) == Some(0)
}

fn wait_scope_empty(
    controller: &LinuxCgroupV2Controller,
    lease: &ResourceLease,
    timeout: Duration,
) -> Result<bool, String> {
    let deadline = Instant::now() + timeout;
    loop {
        if controller
            .scope_is_empty(lease)
            .map_err(|error| format!("scope population query failed: {error:?}"))?
        {
            return Ok(true);
        }
        if Instant::now() >= deadline {
            return Ok(false);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn terminate_and_wait(
    controller: &LinuxCgroupV2Controller,
    lease: &ResourceLease,
) -> Result<bool, String> {
    controller
        .terminate(lease)
        .map_err(|error| format!("native cgroup.kill failed: {error:?}"))?;
    wait_scope_empty(controller, lease, Duration::from_secs(5))
}

fn wait_for_child(child: &mut ChildGuard, timeout: Duration) -> Result<Option<ExitStatus>, String> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child
            .child_mut()
            .try_wait()
            .map_err(|error| format!("child status query failed: {error}"))?
        {
            return Ok(Some(status));
        }
        if Instant::now() >= deadline {
            return Ok(None);
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn lease(task_id: u128, profile: &HardwareProfile) -> Result<ResourceLease, String> {
    let mut request = ResourceRequest::minimal(task_id, WorkKind::Tool);
    request.host_memory.bytes = 32 * 1024 * 1024;
    request.process_limit = Some(2);
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
    let first_group = controller
        .scope_path(&first_lease)
        .map_err(|error| format!("native scope path query failed: {error:?}"))?;
    let mut scope_guard = ScopeGuard {
        controller: controller.clone(),
        lease: first_lease.clone(),
        released: false,
    };

    let memory_limit = read(&first_group.join("memory.max"))?;
    fs::write(first_group.join("memory.swap.max"), "0")
        .map_err(|error| format!("setting bounded memory-swap probe limit failed: {error}"))?;
    let swap_limit_configured = read(&first_group.join("memory.swap.max"))? == "0";
    let configured_cpu_limit = read(&first_group.join("cpu.max"))?;
    let pids_limit = read(&first_group.join("pids.max"))?;
    let limits_configured = memory_limit == first_lease.granted.host_memory_bytes.to_string()
        && configured_cpu_limit == "100000 100000"
        && pids_limit == first_lease.granted.process_limit.to_string();

    let cpu_probe_limit = "10000 100000";
    fs::write(first_group.join("cpu.max"), cpu_probe_limit)
        .map_err(|error| format!("setting bounded CPU-probe quota failed: {error}"))?;
    let cpu_quota_probe_limit_configured = read(&first_group.join("cpu.max"))? == cpu_probe_limit;

    let before_memory = read(&first_group.join("memory.current"))?;
    let before_memory_events = read(&first_group.join("memory.events"))?;
    let mut pressure_child = ChildGuard::spawn(
        &controller,
        &first_lease,
        python(),
        &["-c", MEMORY_PRESSURE_CHILD],
    )?;
    let pressure_pid = pressure_child.child().id();
    let child_attached = read(&first_group.join("cgroup.procs"))?
        .lines()
        .any(|line| line.trim() == pressure_pid.to_string());
    let deadline = Instant::now() + Duration::from_secs(8);
    let mut memory_changed = false;
    let mut memory_oom_kill_seen = false;
    while Instant::now() < deadline
        && pressure_child
            .child_mut()
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
    {
        let current_memory = read(&first_group.join("memory.current"))?;
        let events = read(&first_group.join("memory.events"))?;
        memory_changed |= matches!(
            (
                before_memory.parse::<u64>().ok(),
                current_memory.parse::<u64>().ok()
            ),
            (Some(before), Some(after)) if after > before
        );
        memory_oom_kill_seen |= counter_increased(&before_memory_events, &events, "oom_kill");
        if memory_oom_kill_seen {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let final_memory_events = read(&first_group.join("memory.events"))?;
    memory_oom_kill_seen |=
        counter_increased(&before_memory_events, &final_memory_events, "oom_kill");
    let pressure_exit_before_group_kill =
        wait_for_child(&mut pressure_child, Duration::from_secs(1))?;
    let memory_pressure_oom_kill_observed = child_attached
        && memory_oom_kill_seen
        && pressure_exit_before_group_kill
            .as_ref()
            .is_some_and(|status| status.signal() == Some(libc::SIGKILL));
    let memory_scope_empty = terminate_and_wait(&controller, &first_lease)?;
    let _pressure_status = pressure_child.wait()?;

    let before_cpu_stats = read(&first_group.join("cpu.stat"))?;
    let mut cpu_child = ChildGuard::spawn(
        &controller,
        &first_lease,
        python(),
        &["-c", CPU_PRESSURE_CHILD],
    )?;
    let cpu_pid = cpu_child.child().id();
    let cpu_child_attached = read(&first_group.join("cgroup.procs"))?
        .lines()
        .any(|line| line.trim() == cpu_pid.to_string());
    let cpu_exit_before_timeout = wait_for_child(&mut cpu_child, Duration::from_secs(4))?;
    let cpu_scope_empty = terminate_and_wait(&controller, &first_lease)?;
    let cpu_status = cpu_child.wait()?;
    let after_cpu_stats = read(&first_group.join("cpu.stat"))?;
    let cpu_quota_enforcement_observed = cpu_quota_probe_limit_configured
        && cpu_child_attached
        && cpu_status.success()
        && cpu_quota_enforced(&before_cpu_stats, &after_cpu_stats)
        && cpu_scope_empty
        && cpu_exit_before_timeout.is_some();

    let before_pids_events = read(&first_group.join("pids.events"))?;
    let mut pids_child = ChildGuard::spawn(
        &controller,
        &first_lease,
        python(),
        &["-c", PID_PRESSURE_CHILD],
    )?;
    let pids_pid = pids_child.child().id();
    let pids_child_attached = read(&first_group.join("cgroup.procs"))?
        .lines()
        .any(|line| line.trim() == pids_pid.to_string());
    let pids_deadline = Instant::now() + Duration::from_secs(3);
    let mut pids_denial_candidate = false;
    while pids_child_attached && Instant::now() < pids_deadline {
        let pids_events_after = read(&first_group.join("pids.events"))?;
        let pids_current = read(&first_group.join("pids.current"))?;
        let child_alive = pids_child
            .child_mut()
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none();
        pids_denial_candidate = pids_limit_denial_observed(
            &before_pids_events,
            &pids_events_after,
            &pids_current,
            u64::from(first_lease.granted.process_limit),
            child_alive,
        );
        if pids_denial_candidate {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    let pids_denial_observed = pids_denial_candidate;
    let before_tree_events = read(&first_group.join("cgroup.events"))?;
    let before_tree_current = read(&first_group.join("pids.current"))?;
    let pids_scope_empty = terminate_and_wait(&controller, &first_lease)?;
    let pids_status = wait_for_child(&mut pids_child, Duration::from_secs(1))?
        .ok_or_else(|| "pids child remained alive after cgroup.kill".to_string())?;
    let after_tree_events = read(&first_group.join("cgroup.events"))?;
    let after_tree_current = read(&first_group.join("pids.current"))?;
    let descendant_containment_observed = pids_scope_empty
        && pids_status.signal() == Some(libc::SIGKILL)
        && descendant_tree_terminated(
            &before_tree_events,
            &after_tree_events,
            &before_tree_current,
            &after_tree_current,
        );
    let pids_cgroup_kill_observed = pids_scope_empty && pids_status.signal() == Some(libc::SIGKILL);

    let mut deadline_child =
        ChildGuard::spawn(&controller, &first_lease, python(), &["-c", SLEEP_CHILD])?;
    let deadline_pid = deadline_child.child().id();
    let deadline_child_attached = read(&first_group.join("cgroup.procs"))?
        .lines()
        .any(|line| line.trim() == deadline_pid.to_string());
    thread::sleep(Duration::from_millis(500));
    let alive_at_deadline = deadline_child
        .child_mut()
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_none();
    let deadline_scope_empty = terminate_and_wait(&controller, &first_lease)?;
    let deadline_status = deadline_child.wait()?;
    let deadline_was_killed = deadline_status.signal() == Some(libc::SIGKILL)
        && deadline_scope_empty
        && deadline_child_attached;
    let deadline_cancellation_observed = alive_at_deadline && deadline_was_killed;
    scope_guard.close()?;

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
    let checks = json!({
        "controller_constructed": true,
        "limits_configured": limits_configured,
        "swap_limit_configured": swap_limit_configured,
        "cpu_quota_probe_limit_configured": cpu_quota_probe_limit_configured,
        "child_attached": child_attached,
        "live_memory_accounting_observed": memory_changed,
        "memory_pressure_oom_kill_observed": memory_pressure_oom_kill_observed,
        "memory_scope_cleanup_observed": memory_scope_empty,
        "cpu_child_attached": cpu_child_attached,
        "cpu_quota_enforcement_observed": cpu_quota_enforcement_observed,
        "pids_child_attached": pids_child_attached,
        "process_count_denial_observed": pids_denial_observed,
        "descendant_containment_observed": descendant_containment_observed,
        "cgroup_kill_termination_observed": pids_cgroup_kill_observed || deadline_was_killed,
        "deadline_child_attached": deadline_child_attached,
        "deadline_cancellation_observed": deadline_cancellation_observed,
        "scope_cleanup_observed": true,
        "lab_native_process_timeout_observed": lab_native_timeout_observed
    });
    let check_values = checks
        .as_object()
        .expect("native probe checks serialize as an object");
    let all_checks_pass = check_values.values().all(|value| value == &json!(true));
    let some_check_pass = check_values.values().any(|value| value == &json!(true));
    let status = if all_checks_pass {
        "LIVE VERIFIED"
    } else if some_check_pass {
        "PARTIALLY LIVE VERIFIED"
    } else {
        "NOT VERIFIED"
    };
    let verified: Vec<String> = check_values
        .iter()
        .filter(|&(_, value)| value == &json!(true))
        .map(|(name, _)| name.clone())
        .collect();
    let not_verified: Vec<String> = check_values
        .iter()
        .filter(|&(_, value)| value != &json!(true))
        .map(|(name, _)| name.clone())
        .collect();
    Ok(json!({
        "schema": "aegis-linux-cgroup-v2-native-adapter-probe-v1",
        "status": status,
        "reason": if all_checks_pass { None } else { Some("one or more native kernel checks did not pass") },
        "root": root,
        "limits": {
            "memory_max_bytes": first_lease.granted.host_memory_bytes,
            "configured_cpu_max": configured_cpu_limit,
            "probe_cpu_max": cpu_probe_limit,
            "pids_max": first_lease.granted.process_limit
        },
        "verification_scope": {
            "verified": verified,
            "not_verified": not_verified
        },
        "checks": checks
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
    assert_eq!(report["status"], json!("LIVE VERIFIED"));
}
