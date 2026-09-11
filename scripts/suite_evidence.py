"""Run a test suite and retain normalized, scope-aware metadata."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, suppress
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


COUNT_PATTERNS = {
    "passed": re.compile(r"(?P<count>\d+)\s+passed\b"),
    "failed": re.compile(r"(?P<count>\d+)\s+failed\b"),
    "ignored": re.compile(r"(?P<count>\d+)\s+ignored\b"),
    "filtered": re.compile(r"(?P<count>\d+)\s+filtered(?:\s+out)?\b"),
    "skipped": re.compile(r"(?P<count>\d+)\s+skipped\b"),
}
DISCOVERED_RE = re.compile(r"(?P<count>\d+)\s+(?:tests?|cases?)\s+(?:run|collected)\b")


def parse_counts(output: str) -> dict[str, int | None]:
    counts: dict[str, int | None] = {name: 0 for name in COUNT_PATTERNS}
    for name, pattern in COUNT_PATTERNS.items():
        matches = [int(match.group("count")) for match in pattern.finditer(output)]
        if matches:
            counts[name] = max(matches)
    discovered_match = DISCOVERED_RE.search(output)
    if discovered_match:
        discovered: int | None = int(discovered_match.group("count"))
    else:
        discovered = sum(int(counts[name] or 0) for name in ("passed", "failed", "ignored", "skipped"))
    counts["discovered"] = discovered
    return counts


def toolchain(command: list[str]) -> str:
    executable = command[0].lower() if command else ""
    if "cargo" in executable:
        probe = ["rustc", "--version"]
    elif executable == "uv" and "python" in command:
        python_index = command.index("python")
        probe = [*command[: python_index + 1], "--version"]
    else:
        probe = ["python", "--version"]
    result = subprocess.run(probe, capture_output=True, text=True, check=False)
    return (result.stdout or result.stderr).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def execution_run_key(
    *,
    gate_id: str,
    commit: str,
    command: list[str],
    platform_name: str,
    toolchain_name: str,
    claim_scope: str,
) -> str:
    """Identify duplicate work independently of a human-readable attempt ID."""
    payload = {
        "gate_id": gate_id,
        "commit": commit,
        "command": command,
        "platform": platform_name,
        "toolchain": toolchain_name,
        "claim_scope": claim_scope,
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _capture_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_command(command: list[str], timeout_seconds: int) -> tuple[str, str, int, bool]:
    """Run one command with a bounded deadline and explicit timeout state."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **(
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
    except OSError as exc:
        return "", str(exc), 127, False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired as terminated:
            stdout = getattr(terminated, "stdout", None) or getattr(terminated, "output", None)
            stderr = getattr(terminated, "stderr", None)
            with suppress(OSError):
                process.kill()
            with suppress(OSError):
                process.wait(timeout=0.5)
        stdout = _capture_text(stdout)
        stderr = _capture_text(stderr)
        return stdout, stderr, 124, True
    return _capture_text(stdout), _capture_text(stderr), process.returncode, False


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the timed-out process and descendants without leaving a runner leak."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            with suppress(OSError):
                process.kill()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        with suppress(OSError):
            process.kill()
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)


@contextmanager
def exclusive_suite_lock(path: Path):
    """Serialize suite evidence commands within one local workspace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"another suite evidence command holds {path}") from exc
    try:
        yield
    finally:
        with suppress(OSError):
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _git_remote_observation(revision: str) -> dict[str, object]:
    """Report only what the local git ref cache can establish.

    This deliberately does not call the network.  A cached origin ref is not
    an independent GitHub observation, so the scope is explicit in the JSON.
    """
    try:
        remote_head = subprocess.check_output(
            ["git", "rev-parse", "origin/main"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        contains = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "origin/main"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, subprocess.CalledProcessError):
        remote_head = ""
        contains = False
    return {
        "ref": "origin/main",
        "head": remote_head,
        "contains_commit": contains,
        "observation_scope": "LOCAL_GIT_REF_CACHE_ONLY",
        "independent_verification": "NOT VERIFIED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--attempt-id")
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("a command is required after --")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    owner_id = args.owner_id.strip()
    gate_id = args.gate_id.strip()
    if not owner_id or not gate_id:
        raise SystemExit("--owner-id and --gate-id must be non-empty")
    platform_name = platform.platform()
    toolchain_name = toolchain(command)
    claim_scope = "LOCAL_CHECKOUT_ONLY"
    attempt_id = args.attempt_id or os.environ.get("GITHUB_RUN_ID") or "local"
    run_key = execution_run_key(
        gate_id=gate_id,
        commit=revision,
        command=command,
        platform_name=platform_name,
        toolchain_name=toolchain_name,
        claim_scope=claim_scope,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_suite_lock(args.output.parent / ".suite-evidence.lock"):
            started = datetime.now(UTC)
            stdout, stderr, exit_code, timed_out = run_command(command, args.timeout_seconds)
            finished = datetime.now(UTC)
    except RuntimeError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 75
    output = stdout + stderr
    counts = parse_counts(output)
    result = {
        "schema": "aegis-suite-evidence-v1",
        "name": args.name,
        "command": shlex.join(command),
        "commit": revision,
        "timestamp_utc": finished.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "duration_seconds": (finished - started).total_seconds(),
        "platform": platform_name,
        "toolchain": toolchain_name,
        "discovered": counts["discovered"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "ignored": counts["ignored"],
        "filtered": counts["filtered"],
        "skipped": counts["skipped"],
        "exit_code": exit_code,
        "status": "PROVEN" if exit_code == 0 and not timed_out else "FAILED",
        "claim_scope": claim_scope,
        "claim_label": (
            "LOCALLY PROVEN"
            if exit_code == 0 and not timed_out
            else "LOCAL TIMEOUT"
            if timed_out
            else "LOCAL FAILED"
        ),
        "independent_verification": "NOT VERIFIED",
        "owner_id": owner_id,
        "gate_id": gate_id,
        "attempt_id": attempt_id,
        "run_key": run_key,
        "release_eligible": exit_code == 0 and not timed_out,
        "timeout_seconds": args.timeout_seconds,
        "timed_out": timed_out,
        "termination": "TIMEOUT" if timed_out else "EXITED",
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr),
        "combined_output_sha256": _sha256(output),
        "remote_observation": _git_remote_observation(revision),
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output, end="")
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
