"""Lab-bound local verification command construction and execution helpers.

This module deliberately does not expose a general shell.  It builds bounded
argv vectors from discovered project metadata and is intended to be invoked by
the existing Lab ``tool_call`` execution cell.  The worker is therefore an
edge adapter, not a second scheduler or evidence authority.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .contracts import ProjectProfile


_MAX_OUTPUT_BYTES = 256 * 1024
_MAX_TIMEOUT_SECONDS = 3_600.0
_MAX_COMMAND_ARGUMENTS = 512
_COUNT_PATTERNS = {
    "passed": re.compile(r"(?P<count>\d+)\s+passed\b", flags=re.IGNORECASE),
    "failed": re.compile(r"(?P<count>\d+)\s+failed\b", flags=re.IGNORECASE),
    "skipped": re.compile(r"(?P<count>\d+)\s+skipped\b", flags=re.IGNORECASE),
    "filtered": re.compile(r"(?P<count>\d+)\s+deselected\b", flags=re.IGNORECASE),
    "ignored": re.compile(r"(?P<count>\d+)\s+(?:xfailed|ignored)\b", flags=re.IGNORECASE),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _bounded_text(data: bytes) -> str:
    if len(data) <= _MAX_OUTPUT_BYTES:
        return data.decode("utf-8", errors="replace")
    clipped = data[:_MAX_OUTPUT_BYTES]
    return clipped.decode("utf-8", errors="replace") + "\n[AESE_OUTPUT_TRUNCATED]"


def _read_artifact(handle: Any) -> tuple[str, int, str]:
    """Hash a temporary output file and return only bounded text to memory."""

    handle.seek(0)
    digest = hashlib.sha256()
    size = 0
    prefix = bytearray()
    while True:
        chunk = handle.read(64 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
        if len(prefix) < _MAX_OUTPUT_BYTES:
            prefix.extend(chunk[: _MAX_OUTPUT_BYTES - len(prefix)])
    text = _bounded_text(bytes(prefix))
    if size > _MAX_OUTPUT_BYTES:
        text += "\n[AESE_OUTPUT_TRUNCATED]"
    return digest.hexdigest(), size, text


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_existing_paths(root: Path, candidates: list[Path]) -> tuple[str, ...]:
    selected: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if not _path_is_within(resolved, root) or not resolved.exists():
            continue
        selected.append(resolved.relative_to(root).as_posix())
    return tuple(dict.fromkeys(selected))


def _resolve_rustup_tool(tool: str, *, cwd: Path) -> str | None:
    """Resolve one Rust tool through rustup without invoking a shell."""

    discovered = shutil.which(tool)
    if discovered is None:
        return None
    rustup = shutil.which("rustup")
    if rustup is None:
        return str(Path(discovered))
    try:
        resolved = subprocess.run(
            (rustup, "which", tool),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            cwd=cwd,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return str(Path(discovered))
    candidate_text = resolved.stdout.strip().splitlines()[-1] if resolved.stdout.strip() else ""
    if not candidate_text:
        return str(Path(discovered))
    candidate = Path(candidate_text).expanduser()
    if candidate.is_file() and candidate.name.casefold() in {tool, f"{tool}.exe"}:
        return str(candidate)
    return str(Path(discovered))


def _resolve_cargo_executable(*, cwd: Path) -> str | None:
    """Resolve Cargo's real toolchain binary when rustup exposes a shim.

    Windows can execute the rustup-managed ``cargo.exe`` shim from an
    interactive shell but fail when the same shim is passed directly to a
    spawned process.  Resolving through rustup keeps command construction
    shell-free while giving Lab a stable executable path.
    """

    return _resolve_rustup_tool("cargo", cwd=cwd)


def _python_runtime_dll_directory() -> Path | None:
    """Find the active Python runtime directory for native extension loads."""

    major_minor = f"{sys.version_info.major}{sys.version_info.minor}"
    dll_names = (f"python{major_minor}.dll", f"python{major_minor}t.dll")
    candidates = (
        Path(sys.base_prefix),
        Path(sys.prefix),
        Path(sys.executable).parent,
        Path(sys.executable).parent.parent,
    )
    for candidate in dict.fromkeys(path.expanduser().resolve() for path in candidates):
        if any((candidate / dll_name).is_file() for dll_name in dll_names):
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class LocalVerificationCommand:
    """One generated, shell-free command admitted through Lab."""

    command_id: str
    adapter: str
    framework: str
    executable: str
    argv: tuple[str, ...]
    working_directory: str
    timeout_seconds: float
    source_revision: str
    environment: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())

    def validate(self) -> None:
        if any(
            type(value) is not str or not value.strip() or "\x00" in value
            for value in (
                self.command_id,
                self.adapter,
                self.framework,
                self.executable,
                self.working_directory,
                self.source_revision,
            )
        ):
            raise ValueError("local verification command identity is invalid")
        if type(self.argv) not in (tuple, list) or not self.argv or len(self.argv) > _MAX_COMMAND_ARGUMENTS:
            raise ValueError("local verification command argv is invalid")
        if any(type(argument) is not str or "\x00" in argument for argument in self.argv):
            raise ValueError("local verification command argv contains invalid text")
        if (
            type(self.timeout_seconds) not in (int, float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(float(self.timeout_seconds))
            or not 0 < float(self.timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("local verification timeout is outside the bounded range")
        root = Path(self.working_directory).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("local verification working directory is not a directory")
        executable = Path(self.executable).expanduser().resolve()
        if not executable.is_file():
            raise ValueError("local verification executable is not an existing file")
        if any(
            type(key) is not str
            or type(value) is not str
            or not key.strip()
            or "\x00" in key
            or "\x00" in value
            for key, value in self.environment.items()
        ):
            raise ValueError("local verification environment is invalid")

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "command_id": self.command_id,
            "adapter": self.adapter,
            "framework": self.framework,
            "executable": self.executable,
            "argv": tuple(self.argv),
            "working_directory": self.working_directory,
            "timeout_seconds": float(self.timeout_seconds),
            "source_revision": self.source_revision,
            "environment": dict(self.environment),
        }

    @classmethod
    def from_mapping(cls, value: object) -> LocalVerificationCommand:
        if not isinstance(value, Mapping):
            raise ValueError("local verification command payload is not a mapping")
        typed = cast(Mapping[str, object], value)
        raw_argv = typed.get("argv")
        if type(raw_argv) not in (list, tuple):
            raise ValueError("local verification command argv payload is invalid")
        raw_environment = typed.get("environment", {})
        if not isinstance(raw_environment, Mapping):
            raise ValueError("local verification command environment payload is invalid")
        environment_items = tuple(cast(Mapping[object, object], raw_environment).items())
        if any(type(key) is not str or type(item) is not str for key, item in environment_items):
            raise ValueError("local verification command environment values are invalid")
        command = cls(
            command_id=cast(str, typed.get("command_id", "")),
            adapter=cast(str, typed.get("adapter", "")),
            framework=cast(str, typed.get("framework", "")),
            executable=cast(str, typed.get("executable", "")),
            argv=tuple(cast(list[Any] | tuple[Any, ...], raw_argv)),
            working_directory=cast(str, typed.get("working_directory", "")),
            timeout_seconds=cast(float, typed.get("timeout_seconds", 0.0)),
            source_revision=cast(str, typed.get("source_revision", "")),
            environment={cast(str, key): cast(str, item) for key, item in environment_items},
        )
        command.validate()
        return command


def _python_test_paths(root: Path, *, deep: bool) -> tuple[str, ...]:
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return ()
    if deep:
        candidates = [tests_root]
        core_tests = root / "core" / "python" / "tests.py"
        if core_tests.is_file():
            candidates.append(core_tests)
        return _relative_existing_paths(root, candidates)
    focused = sorted(tests_root.glob("test_verification_*.py"))
    focused.extend(sorted(tests_root.glob("test_aese_primitives.py")))
    return _relative_existing_paths(root, focused)


def build_local_verification_commands(
    profile: ProjectProfile,
    *,
    deep: bool = False,
    include_rust: bool = False,
) -> tuple[LocalVerificationCommand, ...]:
    """Build the conservative local lane for a discovered project.

    Python/pytest is the first active conformance lane because it is the
    current AEGIS implementation language.  Rust can be explicitly included
    for a deeper run.  Other detected ecosystems remain discovery-only until
    their adapter fixtures prove a safe command/result contract.
    """

    profile.validate()
    root = Path(profile.root_path).expanduser().resolve()
    commands: list[LocalVerificationCommand] = []
    if "python" in profile.languages and "pytest" in profile.frameworks:
        test_paths = _python_test_paths(root, deep=deep)
        executable = Path(sys.executable).resolve()
        if test_paths and executable.is_file():
            command = LocalVerificationCommand(
                command_id="python-pytest-deep" if deep else "python-pytest-aese-fast",
                adapter="python-pytest",
                framework="pytest",
                executable=str(executable),
                argv=("-m", "pytest", "-q", *test_paths),
                working_directory=str(root),
                timeout_seconds=900.0 if deep else 300.0,
                source_revision=profile.source_revision,
                environment={"PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8"},
            )
            command.validate()
            commands.append(command)

    if include_rust and "rust" in profile.languages and "cargo" in profile.frameworks:
        cargo = _resolve_cargo_executable(cwd=root)
        if cargo is not None:
            argv = ("test", "--workspace", "--no-default-features") if deep else (
                "test",
                "-p",
                "aegis-nerve",
                "--lib",
                "--no-default-features",
            )
            rust_environment: dict[str, str] = {}
            rustc = _resolve_rustup_tool("rustc", cwd=root)
            rustdoc = _resolve_rustup_tool("rustdoc", cwd=root)
            if rustc is not None:
                rust_environment["RUSTC"] = rustc
            if rustdoc is not None:
                rust_environment["RUSTDOC"] = rustdoc
            command = LocalVerificationCommand(
                command_id="rust-cargo-deep" if deep else "rust-cargo-aese-fast",
                adapter="rust-cargo",
                framework="cargo",
                executable=cargo,
                argv=argv,
                working_directory=str(root),
                timeout_seconds=1_800.0 if deep else 600.0,
                source_revision=profile.source_revision,
                environment=rust_environment,
            )
            command.validate()
            commands.append(command)
    return tuple(commands)


def _terminate_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill is not None and process.pid > 0:
                subprocess.run(
                    (taskkill, "/PID", str(process.pid), "/T", "/F"),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                )
            else:
                process.kill()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        with contextlib.suppress(OSError):
            process.kill()


def _summary_counts(output: str) -> dict[str, int]:
    counts = {name: 0 for name in _COUNT_PATTERNS}
    for name, pattern in _COUNT_PATTERNS.items():
        matches = tuple(pattern.finditer(output))
        if matches:
            counts[name] = int(matches[-1].group("count"))
    return counts


def run_local_verification_command(payload: object) -> dict[str, object]:
    """Run one validated command inside the Lab-provided process cell.

    The function is top-level and argument-only so ``ProcessExecutionCell``
    can spawn it on Windows and POSIX without serializing a live Lab object.
    """

    command = LocalVerificationCommand.from_mapping(payload)
    root = Path(command.working_directory).expanduser().resolve()
    environment = os.environ.copy()
    environment.update(dict(command.environment))
    if command.adapter == "rust-cargo":
        python_runtime = _python_runtime_dll_directory()
        if python_runtime is not None:
            inherited_path = environment.get("PATH", "")
            environment["PATH"] = os.pathsep.join(
                value for value in (str(python_runtime), inherited_path) if value
            )
    started_at = _utc_now()
    timed_out = False
    return_code: int | None = None
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            [command.executable, *command.argv],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        try:
            return_code = process.wait(timeout=float(command.timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_child(process)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2.0)
        stdout_hash, stdout_size, stdout_text = _read_artifact(stdout_file)
        stderr_hash, stderr_size, stderr_text = _read_artifact(stderr_file)

    combined = f"{stdout_text}\n{stderr_text}"
    counts = _summary_counts(combined)
    discovered = sum(counts.values())
    if timed_out:
        status = "TIMEOUT"
        failure_class = "TIMEOUT"
    elif discovered == 0 and return_code == 0:
        status = "ZERO_TESTS"
        failure_class = "ZERO_TESTS"
    elif discovered == 0:
        status = "ERROR" if re.search(r"collect|compile|build|error", combined, re.IGNORECASE) else "ZERO_TESTS"
        failure_class = "COLLECTION_OR_BUILD_ERROR" if status == "ERROR" else "ZERO_TESTS"
    elif return_code == 0:
        status = "PASS"
        failure_class = None
    else:
        status = "FAIL"
        failure_class = "TEST_FAILURE"
    detail = next((line.strip() for line in reversed(combined.splitlines()) if line.strip()), "command completed")
    return {
        "command_id": command.command_id,
        "source_revision": command.source_revision,
        "adapter": command.adapter,
        "framework": command.framework,
        "toolchain": sys.version.split()[0] if command.adapter == "python-pytest" else "UNKNOWN",
        "platform": platform.platform(aliased=True),
        "executable": command.executable,
        "argv": tuple(command.argv),
        "working_directory": str(root),
        "environment_fingerprint": {
            "PYTHONHASHSEED": environment.get("PYTHONHASHSEED", ""),
            "PYTHONIOENCODING": environment.get("PYTHONIOENCODING", ""),
            "RUSTC": environment.get("RUSTC", ""),
            "RUSTDOC": environment.get("RUSTDOC", ""),
        },
        "resource_class": "LOCAL_PROCESS_CELL_BOUNDED_WALL_TIME",
        "timeout_seconds": float(command.timeout_seconds),
        "cancellation_requested": False,
        "exit_code": return_code,
        "exit_semantics": "TIMEOUT" if timed_out else "PROCESS_EXIT",
        "status": status,
        "failure_class": failure_class,
        "detail": detail[:512],
        "discovered": discovered,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "filtered": counts["filtered"],
        "ignored": counts["ignored"],
        "stdout_hash": stdout_hash,
        "stderr_hash": stderr_hash,
        "stdout_size": stdout_size,
        "stderr_size": stderr_size,
        "started_at": started_at,
        "finished_at": _utc_now(),
    }


__all__ = [
    "LocalVerificationCommand",
    "build_local_verification_commands",
    "run_local_verification_command",
]
