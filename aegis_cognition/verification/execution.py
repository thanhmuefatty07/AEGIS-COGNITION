"""Lab-bound local verification command construction and execution helpers.

This module deliberately does not expose a general shell.  It builds bounded
argv vectors from discovered project metadata and is intended to be invoked by
the existing Lab ``tool_call`` execution cell.  The worker is therefore an
edge adapter, not a second scheduler or evidence authority.
"""

from __future__ import annotations

import asyncio
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
import sysconfig
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from ..agent_efficiency import managed_scratch_dir
from .contracts import ProjectProfile

_PROCESS_EXECUTION_CELL_ACTIVE: ContextVar[bool] = ContextVar("aegis_process_execution_cell_active", default=False)


@contextmanager
def process_execution_cell_scope() -> Generator[None]:
    token = _PROCESS_EXECUTION_CELL_ACTIVE.set(True)
    try:
        yield
    finally:
        _PROCESS_EXECUTION_CELL_ACTIVE.reset(token)


_MAX_OUTPUT_BYTES = 256 * 1024
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_OUTPUT_READ_CHUNK_BYTES = 64 * 1024
_MAX_JUNIT_XML_BYTES = 64 * 1024 * 1024
_MAX_CARGO_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_TIMEOUT_SECONDS = 3_600.0
_MAX_COMMAND_ARGUMENTS = 512


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _bounded_text(data: bytes) -> str:
    if len(data) <= _MAX_OUTPUT_BYTES:
        return data.decode("utf-8", errors="replace")
    clipped = data[:_MAX_OUTPUT_BYTES]
    return clipped.decode("utf-8", errors="replace") + "\n[AESE_OUTPUT_TRUNCATED]"


class _BoundedOutputCapture:
    def __init__(self, stdout_file: Any, stderr_file: Any) -> None:
        self.stdout_file = stdout_file
        self.stderr_file = stderr_file
        self.captured_bytes = 0
        self.exceeded = False
        self.failed = False
        self._lock = threading.Lock()

    def append(self, stream: str, chunk: bytes) -> bool:
        target = self.stdout_file if stream == "stdout" else self.stderr_file
        with self._lock:
            remaining = max(0, _MAX_CAPTURE_BYTES - self.captured_bytes)
            captured = min(len(chunk), remaining)
            if captured:
                target.write(chunk[:captured])
                self.captured_bytes += captured
            if captured < len(chunk):
                self.exceeded = True
            return self.exceeded

    def mark_failed(self) -> None:
        with self._lock:
            self.failed = True


def _capture_sync_pipe(
    pipe: Any,
    *,
    stream: str,
    capture: _BoundedOutputCapture,
    limit_reached: threading.Event,
) -> None:
    try:
        while True:
            chunk = pipe.read(_OUTPUT_READ_CHUNK_BYTES)
            if not chunk:
                return
            if capture.append(stream, chunk):
                limit_reached.set()
    except OSError, ValueError:
        capture.mark_failed()
        limit_reached.set()
    finally:
        with contextlib.suppress(OSError, ValueError):
            pipe.close()


async def _capture_async_pipe(
    pipe: asyncio.StreamReader,
    *,
    stream: str,
    capture: _BoundedOutputCapture,
    limit_reached: asyncio.Event,
) -> None:
    try:
        while True:
            chunk = await pipe.read(_OUTPUT_READ_CHUNK_BYTES)
            if not chunk:
                return
            if capture.append(stream, chunk):
                limit_reached.set()
    except OSError, ValueError:
        capture.mark_failed()
        limit_reached.set()


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


@dataclass(frozen=True, slots=True)
class _PytestJUnitSummary:
    discovered: int
    passed: int
    failed: int
    skipped: int
    test_ids: tuple[str, ...]
    report_sha256: str
    report_size: int


@dataclass(frozen=True, slots=True)
class _CargoTestSummary:
    discovered: int
    passed: int
    failed: int
    ignored: int
    filtered: int
    report_sha256: str
    report_size: int
    summary: str
    skipped: int = 0
    test_ids: tuple[str, ...] = ()


_CARGO_RUNNING_RE = re.compile(r"^running (?P<count>\d+) tests?$", re.ASCII)
_CARGO_RESULT_RE = re.compile(
    r"^test result: (?P<result>ok|FAILED)\. "
    r"(?P<passed>\d+) passed; (?P<failed>\d+) failed; "
    r"(?P<ignored>\d+) ignored; (?P<measured>\d+) measured; "
    r"(?P<filtered>\d+) filtered out; finished in (?P<duration>\d+(?:\.\d+)?s)$",
    re.ASCII,
)
_CARGO_CASE_RE = re.compile(r"^test (?P<name>.+) \.\.\. (?P<result>ok|FAILED|ignored)$")


def _parse_cargo_test_report(report_file: Any) -> _CargoTestSummary:
    report_file.seek(0)
    payload = report_file.read(_MAX_CARGO_OUTPUT_BYTES + 1)
    if len(payload) > _MAX_CARGO_OUTPUT_BYTES:
        raise ValueError("Cargo test output exceeds the size limit")
    if not payload:
        raise ValueError("Cargo test output is empty")

    pending_run_count: int | None = None
    pending_case_counts = {"ok": 0, "FAILED": 0, "ignored": 0}
    pending_test_ids: list[str] = []
    pending_test_id_set: set[str] = set()
    reading_failure_details = False
    summary_counts = {"passed": 0, "failed": 0, "ignored": 0, "filtered": 0, "discovered": 0}
    test_ids: list[str] = []
    summary_lines: list[str] = []
    summary_count = 0
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if line.startswith("running "):
            match = _CARGO_RUNNING_RE.fullmatch(line)
            if match is None or pending_run_count is not None:
                raise ValueError("Cargo test run header is malformed or incomplete")
            pending_run_count = int(match.group("count"), 10)
            pending_case_counts = {"ok": 0, "FAILED": 0, "ignored": 0}
            pending_test_ids = []
            pending_test_id_set = set()
            reading_failure_details = False
            continue

        if line.startswith("test result:"):
            match = _CARGO_RESULT_RE.fullmatch(line)
            if match is None or pending_run_count is None:
                raise ValueError("Cargo test result summary is malformed or unbound")
            counts = {
                name: int(match.group(name), 10) for name in ("passed", "failed", "ignored", "measured", "filtered")
            }
            if counts["measured"]:
                raise ValueError("Cargo measured benchmark results are unsupported")
            executed = counts["passed"] + counts["failed"] + counts["ignored"]
            if pending_run_count != executed:
                raise ValueError("Cargo running count does not match the test result summary")
            expected_case_counts = {
                "ok": counts["passed"],
                "FAILED": counts["failed"],
                "ignored": counts["ignored"],
            }
            if pending_case_counts != expected_case_counts:
                raise ValueError("Cargo test case lines do not match the result summary")
            expected_result = "FAILED" if counts["failed"] else "ok"
            if match.group("result") != expected_result:
                raise ValueError("Cargo summary status does not match its failure count")
            summary_counts["passed"] += counts["passed"]
            summary_counts["failed"] += counts["failed"]
            summary_counts["ignored"] += counts["ignored"]
            summary_counts["filtered"] += counts["filtered"]
            summary_counts["discovered"] += executed + counts["filtered"]
            suite_prefix = f"cargo-suite-{summary_count + 1}::"
            test_ids.extend(f"{suite_prefix}{test_id}" for test_id in pending_test_ids)
            summary_lines.append(line)
            summary_count += 1
            pending_run_count = None
            reading_failure_details = False
            continue

        if pending_run_count is not None and line.strip():
            if line == "failures:":
                reading_failure_details = True
                continue
            if reading_failure_details:
                continue
            match = _CARGO_CASE_RE.fullmatch(line)
            if match is None:
                raise ValueError("Cargo test output contains an unrecognized case result")
            test_id = match.group("name")
            if test_id in pending_test_id_set:
                raise ValueError("Cargo test output contains duplicate case identities")
            pending_test_id_set.add(test_id)
            pending_test_ids.append(test_id)
            pending_case_counts[match.group("result")] += 1

    if pending_run_count is not None:
        raise ValueError("Cargo test output ended before the result summary")
    if summary_count == 0:
        raise ValueError("Cargo test output contains no result summary")
    return _CargoTestSummary(
        discovered=summary_counts["discovered"],
        passed=summary_counts["passed"],
        failed=summary_counts["failed"],
        ignored=summary_counts["ignored"],
        filtered=summary_counts["filtered"],
        report_sha256=hashlib.sha256(payload).hexdigest(),
        report_size=len(payload),
        summary=summary_lines[-1],
        test_ids=tuple(test_ids),
    )


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _junit_count(root: ElementTree.Element, name: str) -> int | None:
    raw_value = root.attrib.get(name)
    if raw_value is None:
        return None
    try:
        value = int(raw_value, 10)
    except ValueError as error:
        raise ValueError(f"JUnit {name} count is malformed") from error
    if value < 0:
        raise ValueError(f"JUnit {name} count is negative")
    return value


def _junit_total(root: ElementTree.Element, suites: tuple[ElementTree.Element, ...], name: str) -> int | None:
    total = _junit_count(root, name)
    if total is not None:
        return total
    if _xml_local_name(root.tag) == "testsuite":
        return None
    if not suites:
        return 0
    suite_counts = tuple(_junit_count(suite, name) for suite in suites)
    if any(count is None for count in suite_counts):
        return None
    return sum(cast(int, count) for count in suite_counts)


def _parse_pytest_junit_report(report_path: Path) -> _PytestJUnitSummary:
    try:
        with report_path.open("rb") as report_file:
            payload = report_file.read(_MAX_JUNIT_XML_BYTES + 1)
        if len(payload) > _MAX_JUNIT_XML_BYTES:
            raise ValueError("JUnit report exceeds the size limit")
    except OSError as error:
        raise ValueError("JUnit report is unavailable") from error
    report_size = len(payload)
    if not payload:
        raise ValueError("JUnit report is empty")
    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("JUnit report declarations are unsupported")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise ValueError("JUnit report is malformed XML") from error
    if _xml_local_name(root.tag) not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit report root is not a test suite")
    suites = (
        (root,)
        if _xml_local_name(root.tag) == "testsuite"
        else tuple(child for child in root if _xml_local_name(child.tag) == "testsuite")
    )

    test_ids: list[str] = []
    outcomes = {"failure": 0, "error": 0, "skipped": 0}
    allowed_case_children = {
        "failure",
        "error",
        "skipped",
        "system-out",
        "system-err",
        "properties",
        "rerunFailure",
        "flakyFailure",
    }
    failure_tags = {"failure", "rerunFailure", "flakyFailure"}
    seen_test_ids: set[str] = set()
    for test_case in (node for node in root.iter() if _xml_local_name(node.tag) == "testcase"):
        classname = test_case.attrib.get("classname", "")
        name = test_case.attrib.get("name", "")
        if not classname.strip() or not name.strip():
            raise ValueError("JUnit test case identity is incomplete")
        test_id = f"{classname}::{name}"
        if test_id in seen_test_ids:
            raise ValueError("JUnit test case identities are not unique")
        seen_test_ids.add(test_id)
        test_ids.append(test_id)
        child_tags = [_xml_local_name(child.tag) for child in test_case]
        if any(tag not in allowed_case_children for tag in child_tags):
            raise ValueError("JUnit test case contains an unknown result element")
        case_outcomes = [tag for tag in child_tags if tag in outcomes or tag in failure_tags]
        if len(case_outcomes) > 1:
            raise ValueError("JUnit test case has conflicting outcomes")
        if case_outcomes:
            outcome = case_outcomes[0]
            if outcome in failure_tags:
                outcomes["failure"] += 1
            else:
                outcomes[outcome] += 1

    declared_tests = _junit_total(root, suites, "tests")
    if declared_tests is None:
        if test_ids:
            raise ValueError("JUnit report omits the collected test count")
        declared_tests = 0
    if declared_tests != len(test_ids):
        raise ValueError("JUnit collected test count does not match test cases")
    for name, actual in outcomes.items():
        declared = _junit_total(root, suites, name if name != "failure" else "failures")
        if declared is not None and declared != actual:
            raise ValueError(f"JUnit {name} count does not match test case outcomes")

    failed = outcomes["failure"] + outcomes["error"]
    return _PytestJUnitSummary(
        discovered=declared_tests,
        passed=declared_tests - failed - outcomes["skipped"],
        failed=failed,
        skipped=outcomes["skipped"],
        test_ids=tuple(sorted(test_ids)),
        report_sha256=hashlib.sha256(payload).hexdigest(),
        report_size=report_size,
    )


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
    except OSError, subprocess.SubprocessError:
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


def _require_active_cargo_executable(command: LocalVerificationCommand, *, cwd: Path) -> None:
    if command.adapter != "rust-cargo" or command.framework != "cargo":
        return
    expected = _resolve_cargo_executable(cwd=cwd)
    if expected is None:
        raise ValueError("Cargo adapter cannot resolve the active toolchain executable")
    actual_path = os.path.normcase(str(Path(command.executable).expanduser().resolve()))
    expected_path = os.path.normcase(str(Path(expected).expanduser().resolve()))
    if actual_path != expected_path:
        raise ValueError("Cargo adapter executable does not match the active toolchain binary")
    for variable, tool, required in (("RUSTC", "rustc", True), ("RUSTDOC", "rustdoc", False)):
        expected_tool = _resolve_rustup_tool(tool, cwd=cwd)
        configured_tool = command.environment.get(variable)
        if expected_tool is None:
            if required or configured_tool is not None:
                raise ValueError(f"Cargo adapter cannot bind its active {tool} executable")
            continue
        if configured_tool is None:
            raise ValueError(f"Cargo adapter must bind the active {tool} executable")
        configured_path = os.path.normcase(str(Path(configured_tool).expanduser().resolve()))
        expected_tool_path = os.path.normcase(str(Path(expected_tool).expanduser().resolve()))
        if configured_path != expected_tool_path:
            raise ValueError(f"Cargo adapter {tool} does not match the active toolchain binary")


def _rustc_toolchain_version(environment: Mapping[str, str], *, cwd: Path) -> str:
    executable = environment.get("RUSTC")
    if executable is None:
        return "UNKNOWN"
    try:
        result = subprocess.run(
            (executable, "--version"),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            cwd=cwd,
            env=dict(environment),
            shell=False,
        )
    except OSError, subprocess.SubprocessError:
        return "UNKNOWN"
    version = result.stdout.strip()
    if not version.startswith("rustc ") or len(version) > 128 or "\n" in version:
        return "UNKNOWN"
    return version


def _python_runtime_dll_directory() -> Path | None:
    """Find the active Python runtime directory for native extension loads."""

    major_minor = f"{sys.version_info.major}{sys.version_info.minor}"
    dot_major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    dll_names = (
        f"python{major_minor}.dll",
        f"python{major_minor}t.dll",
        f"libpython{dot_major_minor}.dylib",
        f"libpython{dot_major_minor}t.dylib",
        f"libpython{dot_major_minor}.so",
        f"libpython{dot_major_minor}t.so",
        f"libpython{dot_major_minor}.so.1.0",
    )
    raw_candidates = [
        Path(sys.base_prefix),
        Path(sys.prefix),
        Path(sys.base_prefix) / "lib",
        Path(sys.prefix) / "lib",
        Path(sys.executable).parent,
        Path(sys.executable).parent.parent,
    ]
    libdir = sysconfig.get_config_var("LIBDIR")
    if isinstance(libdir, str) and libdir:
        raw_candidates.insert(0, Path(libdir))
    for candidate in dict.fromkeys(path.expanduser().resolve() for path in raw_candidates):
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
            type(key) is not str or type(value) is not str or not key.strip() or "\x00" in key or "\x00" in value
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
            argv = (
                ("test", "--workspace", "--no-default-features")
                if deep
                else (
                    "test",
                    "-p",
                    "aegis-nerve",
                    "--lib",
                    "--no-default-features",
                )
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


def _terminate_child(process: subprocess.Popen[Any]) -> None:
    try:
        if os.name == "nt":
            if process.poll() is not None:
                return
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
            if _PROCESS_EXECUTION_CELL_ACTIVE.get():
                # The cell owns the shared worker group. Stop this command
                # here; the cell tears down its full group after the runner
                # returns the timeout receipt.
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=2.0)
            else:
                # Keep the leader unreaped until the group is escalated. A
                # completed leader can leave descendants that still need the
                # same process-group kill.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                time.sleep(1.0)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2.0)
    except OSError, subprocess.SubprocessError:
        with contextlib.suppress(OSError):
            process.kill()


def _verification_environment(command: LocalVerificationCommand, *, is_pytest: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(dict(command.environment))
    if is_pytest:
        # Test selection belongs in the validated command argv, not ambient environment.
        for key in tuple(environment):
            if key.casefold() in ("pytest_addopts", "pytest_plugins"):
                environment.pop(key, None)
    if command.adapter == "rust-cargo":
        python_runtime = _python_runtime_dll_directory()
        if python_runtime is not None:
            inherited_path = environment.get("PATH", "")
            environment["PATH"] = os.pathsep.join(value for value in (str(python_runtime), inherited_path) if value)
            if sys.platform == "darwin":
                for variable in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
                    inherited = environment.get(variable, "")
                    environment[variable] = os.pathsep.join(
                        value for value in (str(python_runtime), inherited) if value
                    )
            elif os.name != "nt":
                inherited = environment.get("LD_LIBRARY_PATH", "")
                environment["LD_LIBRARY_PATH"] = os.pathsep.join(
                    value for value in (str(python_runtime), inherited) if value
                )
    return environment


def _resolve_working_directory(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _verification_argv(
    command: LocalVerificationCommand,
    *,
    is_pytest: bool,
    report_directory: str | None,
) -> tuple[tuple[str, ...], Path | None]:
    execution_argv = tuple(command.argv)
    if not is_pytest:
        return execution_argv, None
    if report_directory is None:
        return (*execution_argv, "-o", "addopts="), None
    report_path = Path(report_directory) / "junit.xml"
    return (
        (
            *execution_argv,
            "-o",
            "addopts=",
            "-o",
            "junit_family=xunit2",
            f"--junitxml={report_path}",
        ),
        report_path,
    )


def _build_local_verification_result(
    command: LocalVerificationCommand,
    *,
    root: Path,
    is_pytest: bool,
    environment: dict[str, str],
    started_at: str,
    timed_out: bool,
    output_limited: bool,
    output_capture_failed: bool,
    process_tree_remains: bool = False,
    return_code: int | None,
    execution_argv: tuple[str, ...],
    stdout_file: Any,
    stderr_file: Any,
    report_path: Path | None,
) -> dict[str, object]:
    stdout_hash, stdout_size, stdout_text = _read_artifact(stdout_file)
    stderr_hash, stderr_size, stderr_text = _read_artifact(stderr_file)
    is_cargo = command.adapter == "rust-cargo" and command.framework == "cargo"
    report_summary: _PytestJUnitSummary | _CargoTestSummary | None = None
    report_error: str | None = None
    if is_pytest and not timed_out and report_path is not None:
        try:
            report_summary = _parse_pytest_junit_report(report_path)
        except ValueError as error:
            report_error = str(error)
    elif is_cargo and not timed_out:
        try:
            report_summary = _parse_cargo_test_report(stdout_file)
        except ValueError as error:
            report_error = str(error)

    combined = f"{stdout_text}\n{stderr_text}"
    discovered = report_summary.discovered if report_summary is not None else 0
    passed = report_summary.passed if report_summary is not None else 0
    failed = report_summary.failed if report_summary is not None else 0
    skipped = report_summary.skipped if isinstance(report_summary, _PytestJUnitSummary) else 0
    filtered = report_summary.filtered if isinstance(report_summary, _CargoTestSummary) else 0
    ignored = report_summary.ignored if isinstance(report_summary, _CargoTestSummary) else 0
    if timed_out:
        status = "TIMEOUT"
        failure_class = "TIMEOUT"
        detail = "verification command timed out"
    elif output_limited:
        status = "ERROR"
        failure_class = "OUTPUT_LIMIT"
        detail = f"verification output exceeded the {_MAX_CAPTURE_BYTES}-byte capture limit"
    elif output_capture_failed:
        status = "ERROR"
        failure_class = "OUTPUT_CAPTURE_FAILED"
        detail = "verification output could not be captured completely"
    elif process_tree_remains:
        status = "ERROR"
        failure_class = "PROCESS_TREE_REMAINS"
        detail = "verification command left background processes running"
    elif not (is_pytest or is_cargo):
        status = "ERROR"
        failure_class = "UNSUPPORTED_RESULT_FORMAT"
        detail = next(
            (line.strip() for line in reversed(combined.splitlines()) if line.strip()),
            f"no structured test collector for {command.adapter}/{command.framework}",
        )
    elif report_summary is None:
        status = "MALFORMED"
        failure_class = "MALFORMED_RESULT"
        detail = report_error or (
            "JUnit report was not produced" if is_pytest else "Cargo test output was not produced"
        )
    elif discovered == 0 or (passed == 0 and failed == 0):
        status = "ZERO_TESTS"
        failure_class = "ZERO_TESTS"
        detail = "no test case passed in the collected result"
    elif return_code == 0 and failed == 0:
        status = "PASS"
        failure_class = None
        detail = (
            report_summary.summary
            if isinstance(report_summary, _CargoTestSummary)
            else next((line.strip() for line in reversed(combined.splitlines()) if line.strip()), "pytest completed")
        )
    elif return_code != 0 and failed > 0:
        status = "FAIL"
        failure_class = "TEST_FAILURE"
        detail = (
            report_summary.summary
            if isinstance(report_summary, _CargoTestSummary)
            else next(
                (line.strip() for line in reversed(combined.splitlines()) if line.strip()), "pytest reported failures"
            )
        )
    else:
        status = "ERROR"
        failure_class = "RESULT_CONTRADICTS_PROCESS_EXIT"
        detail = "JUnit outcomes and process exit code disagree"
    return {
        "command_id": command.command_id,
        "source_revision": command.source_revision,
        "adapter": command.adapter,
        "framework": command.framework,
        "toolchain": sys.version.split()[0]
        if is_pytest
        else _rustc_toolchain_version(environment, cwd=root)
        if is_cargo
        else "UNKNOWN",
        "platform": platform.platform(aliased=True),
        "executable": command.executable,
        "argv": execution_argv,
        "working_directory": str(root),
        "environment_fingerprint": {
            "PYTHONHASHSEED": environment.get("PYTHONHASHSEED", ""),
            "PYTHONIOENCODING": environment.get("PYTHONIOENCODING", ""),
            "PYTEST_ADDOPTS": environment.get("PYTEST_ADDOPTS", "") if is_pytest else "",
            "PYTEST_PLUGINS": environment.get("PYTEST_PLUGINS", "") if is_pytest else "",
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
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "filtered": filtered,
        "ignored": ignored,
        "collector": "pytest-junitxml-xunit2" if is_pytest else "cargo-libtest-summary" if is_cargo else "unsupported",
        "collected_test_ids": report_summary.test_ids if report_summary is not None else (),
        "collection_report_sha256": report_summary.report_sha256 if report_summary is not None else "",
        "collection_report_size": report_summary.report_size if report_summary is not None else 0,
        "stdout_hash": stdout_hash,
        "stderr_hash": stderr_hash,
        "stdout_size": stdout_size,
        "stderr_size": stderr_size,
        "output_truncated": output_limited,
        "output_limit_bytes": _MAX_CAPTURE_BYTES,
        "started_at": started_at,
        "finished_at": _utc_now(),
    }


def _windows_process_creation_filetime(pid: int, *, require_active: bool = True) -> int | None:
    if os.name != "nt" or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        if require_active:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                return None
        creation = FileTime()
        exit_time = FileTime()
        kernel_time = FileTime()
        user_time = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


class _WindowsVerificationJob:
    def __init__(self) -> None:
        self._handle: int = 0
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if handle:
            self._handle = int(handle)

    def assign_tree(self, root_pid: int) -> None:
        if os.name != "nt" or not self._handle or root_pid <= 0:
            return
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        def _assign_single(pid: int) -> None:
            proc_handle = kernel32.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)
            if not proc_handle:
                return
            try:
                kernel32.AssignProcessToJobObject(wintypes.HANDLE(self._handle), proc_handle)
            finally:
                kernel32.CloseHandle(proc_handle)

        _assign_single(root_pid)
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value or not snapshot:
            return
        children_by_parent: dict[int, list[int]] = {}
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(ProcessEntry32W)
            has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while has_entry:
                pid = int(entry.th32ProcessID)
                parent_pid = int(entry.th32ParentProcessID)
                if pid > 0 and pid != root_pid:
                    children_by_parent.setdefault(parent_pid, []).append(pid)
                has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
        queue = list(children_by_parent.get(root_pid, ()))
        seen: set[int] = set()
        while queue:
            candidate = queue.pop(0)
            if candidate in seen:
                continue
            seen.add(candidate)
            _assign_single(candidate)
            queue.extend(children_by_parent.get(candidate, ()))

    def reap_remaining_descendants(self, *, exclude_pid: int = 0) -> bool:
        if os.name != "nt" or not self._handle:
            return False
        import ctypes
        from ctypes import wintypes

        class JobObjectBasicProcessIdList(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * 512),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        info = JobObjectBasicProcessIdList()
        if not kernel32.QueryInformationJobObject(
            wintypes.HANDLE(self._handle),
            3,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return False
        count = min(int(info.NumberOfProcessIdsInList), 512)
        remaining: list[int] = []
        for idx in range(count):
            pid = int(info.ProcessIdList[idx])
            if (
                pid > 0
                and pid != exclude_pid
                and _windows_process_creation_filetime(pid, require_active=True) is not None
            ):
                remaining.append(pid)
        if not remaining:
            return False
        self.close()
        return True

    def close(self) -> None:
        if os.name != "nt" or not self._handle:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = self._handle
        self._handle = 0
        try:
            kernel32.TerminateJobObject(wintypes.HANDLE(handle), 1)
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))


def _windows_reap_remaining_descendants(root_pid: int, min_creation_filetime: int) -> bool:
    if os.name != "nt" or root_pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value or not snapshot:
        return False
    children_by_parent: dict[int, list[int]] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while has_entry:
            pid = int(entry.th32ProcessID)
            parent_pid = int(entry.th32ParentProcessID)
            if pid > 0 and pid != root_pid:
                children_by_parent.setdefault(parent_pid, []).append(pid)
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)

    queue = list(children_by_parent.get(root_pid, ()))
    seen: set[int] = set()
    live_descendants: list[int] = []
    while queue:
        candidate = queue.pop(0)
        if candidate in seen:
            continue
        seen.add(candidate)
        creation_time = _windows_process_creation_filetime(candidate, require_active=True)
        if creation_time is not None and creation_time >= min_creation_filetime:
            live_descendants.append(candidate)
            queue.extend(children_by_parent.get(candidate, ()))

    if not live_descendants:
        return False
    for pid in reversed(live_descendants):
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 2000)
            finally:
                kernel32.CloseHandle(handle)
    return True


def _posix_process_group_exists(process_group_id: int) -> bool:
    killpg: Callable[[int, int], None] | None = getattr(os, "killpg", None)
    if killpg is None:
        return False
    try:
        killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_posix_process_group_exit(process_group_id: int, timeout_seconds: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while _posix_process_group_exists(process_group_id):
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.02, remaining))
    return True


async def _terminate_async_child(process: asyncio.subprocess.Process) -> None:
    process_id = process.pid
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is not None and process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                taskkill,
                "/PID",
                str(process_id),
                "/T",
                "/F",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=3.0)
            except TimeoutError:
                killer.kill()
                await killer.wait()
        if process.returncode is None:
            process.kill()
            await process.wait()
        await asyncio.to_thread(_windows_reap_remaining_descendants, process_id, 0)
        return

    killpg: Callable[[int, int], None] | None = getattr(os, "killpg", None)
    sigterm: int = getattr(signal, "SIGTERM", 15)
    sigkill: int = getattr(signal, "SIGKILL", 9)
    if killpg is not None:
        with contextlib.suppress(ProcessLookupError):
            killpg(process_id, sigterm)
        if not await _wait_for_posix_process_group_exit(process_id, 1.0):
            with contextlib.suppress(ProcessLookupError):
                killpg(process_id, sigkill)
            await _wait_for_posix_process_group_exit(process_id, 1.0)
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()


async def _await_task_to_completion(task: asyncio.Task[Any]) -> Any:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _terminate_async_child_resilient(process: asyncio.subprocess.Process) -> None:
    cleanup = asyncio.create_task(_terminate_async_child(process))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await _await_task_to_completion(cleanup)
        raise


async def _build_local_verification_result_async(
    command: LocalVerificationCommand,
    *,
    root: Path,
    is_pytest: bool,
    environment: dict[str, str],
    started_at: str,
    timed_out: bool,
    output_limited: bool,
    output_capture_failed: bool,
    process_tree_remains: bool = False,
    return_code: int | None,
    execution_argv: tuple[str, ...],
    stdout_file: Any,
    stderr_file: Any,
    report_path: Path | None,
) -> dict[str, object]:
    build = asyncio.create_task(
        asyncio.to_thread(
            _build_local_verification_result,
            command,
            root=root,
            is_pytest=is_pytest,
            environment=environment,
            started_at=started_at,
            timed_out=timed_out,
            output_limited=output_limited,
            output_capture_failed=output_capture_failed,
            process_tree_remains=process_tree_remains,
            return_code=return_code,
            execution_argv=execution_argv,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            report_path=report_path,
        )
    )
    try:
        return await asyncio.shield(build)
    except asyncio.CancelledError:
        await _await_task_to_completion(build)
        raise


async def run_local_verification_command_async(payload: object) -> dict[str, object]:
    """Run and supervise a verification process group in the application process."""

    command = LocalVerificationCommand.from_mapping(payload)
    root = await asyncio.to_thread(_resolve_working_directory, command.working_directory)
    await asyncio.to_thread(_require_active_cargo_executable, command, cwd=root)
    is_pytest = command.adapter == "python-pytest" and command.framework == "pytest"
    environment = _verification_environment(command, is_pytest=is_pytest)
    started_at = _utc_now()
    report_context = (
        managed_scratch_dir("verification", prefix="aese-pytest-report-") if is_pytest else contextlib.nullcontext(None)
    )
    with report_context as report_directory:
        execution_argv, report_path = _verification_argv(
            command,
            is_pytest=is_pytest,
            report_directory=str(report_directory) if report_directory is not None else None,
        )
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            capture = _BoundedOutputCapture(stdout_file, stderr_file)
            output_limit_reached = asyncio.Event()
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            creation = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    command.executable,
                    *execution_argv,
                    cwd=root,
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=(os.name != "nt" and not _PROCESS_EXECUTION_CELL_ACTIVE.get()),
                    creationflags=creationflags,
                )
            )
            win_job = _WindowsVerificationJob()
            try:
                try:
                    process = await asyncio.shield(creation)
                except asyncio.CancelledError:
                    process = await _await_task_to_completion(creation)
                    await _terminate_async_child_resilient(process)
                    raise

                win_job.assign_tree(process.pid)
                leader_creation_time = _windows_process_creation_filetime(process.pid, require_active=False) or 0
                timed_out = False
                output_limited = False
                output_capture_failed = False
                process_tree_remains = False
                return_code: int | None = None
                if process.stdout is None or process.stderr is None:
                    await _terminate_async_child_resilient(process)
                    raise RuntimeError("verification output pipes were not created")
                capture_tasks = (
                    asyncio.create_task(
                        _capture_async_pipe(
                            process.stdout,
                            stream="stdout",
                            capture=capture,
                            limit_reached=output_limit_reached,
                        )
                    ),
                    asyncio.create_task(
                        _capture_async_pipe(
                            process.stderr,
                            stream="stderr",
                            capture=capture,
                            limit_reached=output_limit_reached,
                        )
                    ),
                )
                process_wait = asyncio.create_task(process.wait())
                limit_wait = asyncio.create_task(output_limit_reached.wait())
                try:
                    completed, _ = await asyncio.wait(
                        (process_wait, limit_wait),
                        timeout=float(command.timeout_seconds),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not completed:
                        timed_out = True
                        await _terminate_async_child_resilient(process)
                    elif output_limit_reached.is_set():
                        output_limited = capture.exceeded
                        await _terminate_async_child_resilient(process)
                    else:
                        return_code = process_wait.result()
                        if os.name == "nt":
                            job_reaped = win_job.reap_remaining_descendants(exclude_pid=process.pid)
                            snap_reaped = await asyncio.to_thread(
                                _windows_reap_remaining_descendants, process.pid, leader_creation_time
                            )
                            process_tree_remains = job_reaped or snap_reaped
                        elif not _PROCESS_EXECUTION_CELL_ACTIVE.get() and _posix_process_group_exists(process.pid):
                            process_tree_remains = True
                            await _terminate_async_child_resilient(process)

                    _, pending_capture = await asyncio.wait(
                        capture_tasks,
                        timeout=2.0,
                        return_when=asyncio.ALL_COMPLETED,
                    )
                    if pending_capture:
                        output_capture_failed = True
                        await _terminate_async_child_resilient(process)
                        for task in pending_capture:
                            task.cancel()
                        await asyncio.gather(*pending_capture, return_exceptions=True)
                    output_limited = output_limited or capture.exceeded
                    output_capture_failed = output_capture_failed or capture.failed
                    return_code = process.returncode if return_code is None else return_code
                    return await _build_local_verification_result_async(
                        command,
                        root=root,
                        is_pytest=is_pytest,
                        environment=environment,
                        started_at=started_at,
                        timed_out=timed_out,
                        output_limited=output_limited,
                        output_capture_failed=output_capture_failed,
                        process_tree_remains=process_tree_remains,
                        return_code=return_code,
                        execution_argv=execution_argv,
                        stdout_file=stdout_file,
                        stderr_file=stderr_file,
                        report_path=report_path,
                    )
                except asyncio.CancelledError:
                    await _terminate_async_child_resilient(process)
                    raise
                except Exception:
                    await _terminate_async_child_resilient(process)
                    raise
                finally:
                    for task in (process_wait, limit_wait):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(process_wait, limit_wait, return_exceptions=True)
            finally:
                win_job.close()


def run_local_verification_command(payload: object) -> dict[str, object]:
    """Run one validated command inside the Lab-provided process cell.

    The function is top-level and argument-only so ``ProcessExecutionCell``
    can spawn it on Windows and POSIX without serializing a live Lab object.
    """

    command = LocalVerificationCommand.from_mapping(payload)
    root = Path(command.working_directory).expanduser().resolve()
    _require_active_cargo_executable(command, cwd=root)
    is_pytest = command.adapter == "python-pytest" and command.framework == "pytest"
    environment = _verification_environment(command, is_pytest=is_pytest)
    started_at = _utc_now()
    timed_out = False
    process_tree_remains = False
    return_code: int | None = None
    report_context = (
        managed_scratch_dir("verification", prefix="aese-pytest-report-") if is_pytest else contextlib.nullcontext(None)
    )
    with report_context as report_directory:
        execution_argv, report_path = _verification_argv(
            command,
            is_pytest=is_pytest,
            report_directory=str(report_directory) if report_directory is not None else None,
        )
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            capture = _BoundedOutputCapture(stdout_file, stderr_file)
            output_limit_reached = threading.Event()
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            process_options: dict[str, Any] = {}
            if os.name != "nt":
                process_options["start_new_session"] = not _PROCESS_EXECUTION_CELL_ACTIVE.get()
            win_job = _WindowsVerificationJob()
            try:
                process = subprocess.Popen(
                    [command.executable, *execution_argv],
                    cwd=root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    creationflags=creationflags,
                    **process_options,
                )
                win_job.assign_tree(process.pid)
                leader_creation_time = _windows_process_creation_filetime(process.pid, require_active=False) or 0
                if process.stdout is None or process.stderr is None:
                    _terminate_child(process)
                    raise RuntimeError("verification output pipes were not created")
                capture_threads = (
                    threading.Thread(
                        target=_capture_sync_pipe,
                        args=(process.stdout,),
                        kwargs={"stream": "stdout", "capture": capture, "limit_reached": output_limit_reached},
                        daemon=True,
                    ),
                    threading.Thread(
                        target=_capture_sync_pipe,
                        args=(process.stderr,),
                        kwargs={"stream": "stderr", "capture": capture, "limit_reached": output_limit_reached},
                        daemon=True,
                    ),
                )
                for thread in capture_threads:
                    thread.start()
                deadline = time.monotonic() + float(command.timeout_seconds)
                while True:
                    if output_limit_reached.is_set():
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    try:
                        return_code = process.wait(timeout=min(0.02, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if timed_out or output_limit_reached.is_set():
                    _terminate_child(process)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=2.0)
                elif os.name == "nt":
                    job_reaped = win_job.reap_remaining_descendants(exclude_pid=process.pid)
                    snap_reaped = _windows_reap_remaining_descendants(process.pid, leader_creation_time)
                    process_tree_remains = job_reaped or snap_reaped
                elif not _PROCESS_EXECUTION_CELL_ACTIVE.get() and _posix_process_group_exists(process.pid):
                    process_tree_remains = True
                    _terminate_child(process)
                for thread in capture_threads:
                    thread.join(timeout=2.0)
                output_capture_failed = any(thread.is_alive() for thread in capture_threads) or capture.failed
                output_limited = capture.exceeded
                return_code = process.returncode if return_code is None else return_code
                result = _build_local_verification_result(
                    command,
                    root=root,
                    is_pytest=is_pytest,
                    environment=environment,
                    started_at=started_at,
                    timed_out=timed_out,
                    output_limited=output_limited,
                    output_capture_failed=output_capture_failed,
                    process_tree_remains=process_tree_remains,
                    return_code=return_code,
                    execution_argv=execution_argv,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                    report_path=report_path,
                )
            finally:
                win_job.close()
    return result


__all__ = [
    "LocalVerificationCommand",
    "build_local_verification_commands",
    "process_execution_cell_scope",
    "run_local_verification_command",
    "run_local_verification_command_async",
]
