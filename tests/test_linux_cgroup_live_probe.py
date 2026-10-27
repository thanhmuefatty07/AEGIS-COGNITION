from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

from scripts import linux_cgroup_live_probe


_CGROUP_ROOT_FILES = (
    "cgroup.controllers",
    "cgroup.procs",
    "cgroup.subtree_control",
)

_CGROUP_GROUP_FILES = (
    "cgroup.procs",
    "cgroup.events",
    "cgroup.kill",
    "memory.max",
    "memory.swap.max",
    "memory.current",
    "memory.events",
    "cpu.max",
    "cpu.stat",
    "pids.max",
    "pids.current",
    "pids.events",
)


def _cgroup_root(tmp_path: Path) -> Path:
    root = tmp_path / "cgroup"
    root.mkdir()
    for name in _CGROUP_ROOT_FILES:
        contents = "cpu memory pids" if name != "cgroup.procs" else ""
        (root / name).write_text(contents, encoding="utf-8")
    return root


def _run_synthetic_live_probe(
    tmp_path: Path,
    monkeypatch,
    *,
    cleanup_fails: bool,
) -> tuple[Path, dict[str, object]]:
    root = _cgroup_root(tmp_path)
    process_ids = iter((101, 102, 103, 104))

    class ChildInput:
        def write(self, value: bytes) -> int:
            return len(value)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    class CompletedProcess:
        def __init__(self, pid: int, returncode_on_wait: int) -> None:
            self.pid = pid
            self.returncode: int | None = None
            self.returncode_on_wait = returncode_on_wait
            self.stdin = ChildInput()

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = self.returncode_on_wait
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    def start_child(*_args, **_kwargs) -> CompletedProcess:
        code = _args[0][2]
        returncode = -9 if "os.fork" in code or "time.sleep(30)" in code or "blocks=[]" in code else 0
        return CompletedProcess(next(process_ids), returncode)

    monotonic_value = 0.0

    def monotonic() -> float:
        nonlocal monotonic_value
        current = monotonic_value
        monotonic_value += 0.6
        return current

    read_counts = {"memory.current": 0, "memory.events": 0}
    real_read = linux_cgroup_live_probe._read

    def read(path: Path) -> str | None:
        if path.name == "memory.current":
            read_counts[path.name] += 1
            return "0" if read_counts[path.name] == 1 else "1024"
        if path.name == "memory.events":
            read_counts[path.name] += 1
            return "oom_kill 0" if read_counts[path.name] == 1 else "oom_kill 1"
        if path.name == "cpu.stat":
            return ""
        if path.name == "pids.events":
            return "max 0"
        if path.name == "pids.current":
            return "0"
        if path.name == "cgroup.events":
            return "populated 0"
        return real_read(path)

    real_rmdir = Path.rmdir
    real_mkdir = Path.mkdir

    def mkdir(path: Path, *args, **kwargs):
        result = real_mkdir(path, *args, **kwargs)
        if path.name.startswith("aegis-live-probe-"):
            for name in _CGROUP_GROUP_FILES:
                (path / name).write_text("", encoding="utf-8")
        return result

    def rmdir(path: Path) -> None:
        if path.name.startswith("aegis-live-probe-"):
            if cleanup_fails:
                raise OSError(errno.EBUSY, "synthetic cgroup cleanup failure")
            for entry in path.iterdir():
                entry.unlink()
        real_rmdir(path)

    monkeypatch.setattr(linux_cgroup_live_probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux_cgroup_live_probe.os, "access", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(linux_cgroup_live_probe.subprocess, "Popen", start_child)
    monkeypatch.setattr(linux_cgroup_live_probe.time, "monotonic", monotonic)
    monkeypatch.setattr(linux_cgroup_live_probe.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(linux_cgroup_live_probe, "_read", read)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "rmdir", rmdir)

    return root, linux_cgroup_live_probe.probe(cgroup_root=root)


def test_probe_does_not_remove_a_preexisting_cgroup(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _cgroup_root(tmp_path)
    preexisting = root / f"aegis-live-probe-{os.getpid()}"
    preexisting.mkdir()
    monkeypatch.setattr(linux_cgroup_live_probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux_cgroup_live_probe.os, "access", lambda *_args, **_kwargs: True)

    report = linux_cgroup_live_probe.probe(cgroup_root=root)
    capsys.readouterr()

    assert report["status"] == "NOT VERIFIED"
    assert preexisting.is_dir()


def test_probe_does_not_report_verified_when_cgroup_cleanup_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    root, report = _run_synthetic_live_probe(tmp_path, monkeypatch, cleanup_fails=True)
    capsys.readouterr()

    assert report["status"] == "NOT VERIFIED"
    assert report["checks"]["scope_cleanup_observed"] is False
    assert "temporary cgroup cleanup" in report["verification_scope"]["not_verified"]
    assert "cleanup_warning" in report
    assert next(root.glob("aegis-live-probe-*")).is_dir()


def test_probe_stays_partial_without_cpu_process_and_tree_enforcement(tmp_path: Path, monkeypatch, capsys) -> None:
    root, report = _run_synthetic_live_probe(tmp_path, monkeypatch, cleanup_fails=False)
    capsys.readouterr()

    assert report["status"] == "PARTIALLY LIVE VERIFIED"
    assert report["checks"].get("cpu_quota_enforcement_observed") is False
    assert report["checks"].get("process_count_denial_observed") is False
    assert report["checks"].get("descendant_containment_observed") is False
    assert "CPU quota enforcement" in report["verification_scope"]["not_verified"]
    assert "process-count denial" in report["verification_scope"]["not_verified"]
    assert "descendant containment" in report["verification_scope"]["not_verified"]
    assert not next(root.glob("aegis-live-probe-*"), None)


def test_cpu_quota_oracle_requires_observed_kernel_throttling() -> None:
    observe = getattr(linux_cgroup_live_probe, "_cpu_quota_enforced", None)
    assert callable(observe), "the live probe must define a CPU-stat enforcement oracle"

    before = "usage_usec 100\nnr_periods 1\nnr_throttled 0\nthrottled_usec 0"
    usage_without_throttle = "usage_usec 40000\nnr_periods 8\nnr_throttled 0\nthrottled_usec 0"
    throttled = "usage_usec 11000\nnr_periods 8\nnr_throttled 7\nthrottled_usec 25000"

    assert not observe(before, usage_without_throttle)
    assert observe(before, throttled)


def test_process_limit_oracle_requires_fork_denial_and_pids_event() -> None:
    observe = getattr(linux_cgroup_live_probe, "_pids_limit_denial_observed", None)
    assert callable(observe), "the live probe must define a pids.events enforcement oracle"

    before = "max 2"
    denied = "max 3"
    no_new_event = "max 2"

    assert observe(before, denied, "4", 4, True)
    assert not observe(before, denied, "4", 4, False)
    assert not observe(before, no_new_event, "4", 4, True)
    assert not observe(before, denied, "3", 4, True)


def test_descendant_oracle_requires_populated_transition_and_empty_group() -> None:
    observe = getattr(linux_cgroup_live_probe, "_descendant_termination_observed", None)
    assert callable(observe), "the live probe must define a cgroup-tree termination oracle"

    assert observe("populated 1", "populated 0", "4", "0")
    assert not observe("populated 0", "populated 0", "4", "0")
    assert not observe("populated 1", "populated 1", "4", "0")
    assert not observe("populated 1", "populated 0", "4", "1")


def test_pid_probe_reaps_killed_leader_before_observing_empty_cgroup(tmp_path: Path, monkeypatch) -> None:
    group = tmp_path / "probe"
    group.mkdir()
    (group / "cgroup.procs").write_text("", encoding="utf-8")
    (group / "cgroup.kill").write_text("", encoding="utf-8")

    class ChildInput:
        def write(self, value: bytes) -> int:
            return len(value)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    class KilledChild:
        pid = 201

        def __init__(self) -> None:
            self.stdin = ChildInput()
            self.killed = False
            self.reaped = False
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.reaped = True
            self.returncode = -9
            return self.returncode

    child = KilledChild()
    pids_event_reads = 0
    real_read = linux_cgroup_live_probe._read

    def read(path: Path) -> str | None:
        nonlocal pids_event_reads
        if path.name == "cgroup.procs":
            return str(child.pid)
        if path.name == "pids.events":
            pids_event_reads += 1
            return "max 0" if pids_event_reads == 1 else "max 1"
        if path.name == "pids.current":
            return "0" if child.reaped else "4"
        if path.name == "cgroup.events":
            if child.killed:
                assert child.reaped, "reap the killed group leader before checking cgroup emptiness"
            return "populated 0" if child.reaped else "populated 1"
        return real_read(path)

    def write(path: Path, value: str) -> None:
        path.write_text(value, encoding="utf-8")
        if path.name == "cgroup.kill":
            child.killed = True

    monkeypatch.setattr(linux_cgroup_live_probe, "_start_gated_child", lambda _code: child)
    monkeypatch.setattr(linux_cgroup_live_probe, "_read", read)
    monkeypatch.setattr(linux_cgroup_live_probe, "_write_text", write)
    checks: dict[str, object] = {
        "pids_child_attached": False,
        "process_count_denial_observed": False,
        "descendant_containment_observed": False,
        "cgroup_kill_termination_observed": False,
    }

    linux_cgroup_live_probe._probe_pids(group, [], checks)

    assert child.reaped
    assert checks["process_count_denial_observed"] is True
    assert checks["descendant_containment_observed"] is True
    assert checks["cgroup_kill_termination_observed"] is True


def test_main_strict_mode_rejects_partial_evidence_but_allows_collection(monkeypatch) -> None:
    report = {"status": "PARTIALLY LIVE VERIFIED"}
    monkeypatch.setattr(linux_cgroup_live_probe, "probe", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(sys, "argv", ["linux_cgroup_live_probe.py"])
    assert linux_cgroup_live_probe.main() == 0

    monkeypatch.setattr(sys, "argv", ["linux_cgroup_live_probe.py", "--require-live"])
    assert linux_cgroup_live_probe.main() == 1

    report["status"] = "LIVE VERIFIED"
    assert linux_cgroup_live_probe.main() == 0
