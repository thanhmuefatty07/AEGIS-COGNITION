"""Run a test suite and retain normalized, scope-aware metadata."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext, suppress
import fnmatch
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree


COUNT_PATTERNS = {
    "passed": re.compile(r"(?P<count>\d+)\s+passed\b"),
    "failed": re.compile(r"(?P<count>\d+)\s+failed\b"),
    "ignored": re.compile(r"(?P<count>\d+)\s+ignored\b"),
    "filtered": re.compile(r"(?P<count>\d+)\s+filtered(?:\s+out)?\b"),
    "skipped": re.compile(r"(?P<count>\d+)\s+skipped\b"),
}
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
DISCOVERED_RE = re.compile(r"(?P<count>\d+)\s+(?:tests?|cases?)\s+(?:run|collected)\b")
MAX_JUNIT_BYTES = 64 * 1024 * 1024
MAX_CARGO_CONFIG_BYTES = 1024 * 1024
MAX_CARGO_CONFIG_FILES = 128
MAX_CARGO_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_CARGO_WORKSPACE_MEMBERS = 256
COUNT_SOURCES = {
    "pytest": "PYTEST_JUNIT_XML",
    "cargo-test": "RUST_TEST_HARNESS",
    "cargo-nextest": "NEXTEST_SUMMARY",
}
PUBLIC_STATUSES = frozenset({"PROVEN", "FAILED", "NOT_VERIFIED"})
PUBLIC_FAILURE_REASONS = frozenset(
    {
        "TIMEOUT",
        "NON_ZERO_EXIT",
        "FAILED_TESTS",
        "ZERO_TESTS",
        "NO_PASSED_TESTS",
        "INCONSISTENT_COUNTS",
        "UNSUPPORTED_TEST_RUNNER",
        "UNSUPPORTED_TEST_HARNESS",
        "INVALID_RUNNER_REPORT",
    }
)
PUBLIC_GATE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
RUST_SUMMARY_RE = re.compile(
    r"^test result: (?:ok|FAILED)\.\s+(?P<passed>\d+) passed;\s+(?P<failed>\d+) failed;\s+"
    r"(?P<ignored>\d+) ignored;\s+\d+ measured;\s+(?P<filtered>\d+) filtered out;",
    re.MULTILINE,
)
NEXTEST_SUMMARY_RE = re.compile(r"^\s*Summary\s+\[[^\]\r\n]*\]\s+\d+ tests? run:[^\r\n]*$", re.MULTILINE)


def _test_runner(command: list[str]) -> str | None:
    index = 0
    if not command:
        return None

    def executable_at(position: int) -> str:
        return command[position].replace("\\", "/").rsplit("/", 1)[-1].lower()

    if executable_at(0) in {"uv", "uv.exe"}:
        if command[1:2] != ["run"]:
            return None
        index = 2
        while index < len(command):
            if command[index] in {"--locked", "--no-sync", "--no-project", "--offline", "--frozen"}:
                index += 1
            elif command[index] in {"--extra", "--python"} and index + 1 < len(command):
                index += 2
            elif command[index] == "--":
                index += 1
                break
            else:
                break
    if index >= len(command):
        return None
    executable = executable_at(index)
    if executable in {"pytest", "pytest.exe"}:
        return "pytest"
    if re.fullmatch(r"python(?:3(?:\.\d+)*)?(?:\.exe)?", executable):
        index += 1
        while command[index : index + 1] == ["-B"]:
            index += 1
        return "pytest" if command[index : index + 2] == ["-m", "pytest"] else None
    if executable in {"cargo", "cargo.exe"}:
        index += 1
        if index < len(command) and command[index].startswith("+"):
            index += 1
        cargo_arguments = command[index:]
        cargo_options = cargo_arguments[: cargo_arguments.index("--")] if "--" in cargo_arguments else cargo_arguments
        # CLI configuration can replace execution with an arbitrary target runner.
        if any(option == "--config" or option.startswith("--config=") for option in cargo_options):
            return None
        if command[index : index + 1] == ["test"]:
            return "cargo-test"
        if command[index : index + 2] == ["nextest", "run"]:
            return "cargo-nextest"
    return None


def _cargo_config_documents(config_paths: list[Path]) -> list[dict[str, object]] | None:
    documents: list[dict[str, object]] = []
    visited: set[str] = set()
    active: set[str] = set()

    def load(path: Path, *, optional: bool = False, depth: int = 0) -> list[dict[str, object]] | None:
        if depth > 32 or len(visited) >= MAX_CARGO_CONFIG_FILES:
            return None
        try:
            identity = os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            return None
        if identity in active:
            return None
        if identity in visited:
            return []
        try:
            with path.open("rb") as config_file:
                payload = config_file.read(MAX_CARGO_CONFIG_BYTES + 1)
        except FileNotFoundError:
            return [] if optional else None
        except OSError:
            return None
        if len(payload) > MAX_CARGO_CONFIG_BYTES:
            return None
        try:
            config = tomllib.loads(payload.decode("utf-8"))
        except UnicodeDecodeError, tomllib.TOMLDecodeError:
            return None
        includes = config.pop("include", [])
        if not isinstance(includes, list):
            return None
        active.add(identity)
        result = [config]
        for include in reversed(includes):
            include_optional = False
            if isinstance(include, str):
                include_path = include
            elif isinstance(include, dict):
                include_path = include.get("path")
                include_optional = include.get("optional", False)
                if not isinstance(include_optional, bool):
                    active.remove(identity)
                    return None
            else:
                active.remove(identity)
                return None
            if not isinstance(include_path, str) or Path(include_path).suffix.casefold() != ".toml":
                active.remove(identity)
                return None
            included = load(path.parent / include_path, optional=include_optional, depth=depth + 1)
            if included is None:
                active.remove(identity)
                return None
            result.extend(included)
        active.remove(identity)
        visited.add(identity)
        return result

    for config_path in config_paths:
        loaded = load(config_path, optional=True)
        if loaded is None:
            return None
        documents.extend(loaded)
    return documents


def _cargo_runner_configuration_present(command: list[str], test_runner: str | None) -> bool:
    """Fail closed when a selected Cargo target may execute through a configured runner."""
    index = 0
    if not command:
        return False

    def executable_at(position: int) -> str:
        return command[position].replace("\\", "/").rsplit("/", 1)[-1].lower()

    if executable_at(0) in {"uv", "uv.exe"}:
        if command[1:2] != ["run"]:
            return False
        index = 2
        while index < len(command):
            if command[index] in {"--locked", "--no-sync", "--no-project", "--offline", "--frozen"}:
                index += 1
            elif command[index] in {"--extra", "--python"} and index + 1 < len(command):
                index += 2
            elif command[index] == "--":
                index += 1
                break
            else:
                break
    if index >= len(command) or executable_at(index) not in {"cargo", "cargo.exe"}:
        return False

    cargo_index = index
    argument_index = index + 1
    if argument_index < len(command) and command[argument_index].startswith("+"):
        argument_index += 1
    cargo_arguments = command[argument_index:]
    cargo_options = cargo_arguments[: cargo_arguments.index("--")] if "--" in cargo_arguments else cargo_arguments

    def cargo_host_target() -> str | None:
        probe = [*command[: cargo_index + 1], "-Vv"]
        if argument_index == cargo_index + 2:
            probe.insert(cargo_index + 1, command[cargo_index + 1])
        try:
            result = subprocess.run(probe, capture_output=True, text=True, check=False, timeout=5)
        except OSError, subprocess.TimeoutExpired:
            return None
        if result.returncode != 0:
            return None
        match = re.search(r"(?m)^host:\s*([^\r\n]+)$", result.stdout + result.stderr)
        return match.group(1).strip() if match is not None else None

    environment = os.environ
    target_runner_variables = {
        name.upper()
        for name in environment
        if name.upper().startswith("CARGO_TARGET_") and name.upper().endswith("_RUNNER")
    }
    config_paths: list[Path] = []
    current = Path.cwd()
    for directory in (current, *current.parents):
        legacy_config = directory / ".cargo" / "config"
        config_paths.append(legacy_config if legacy_config.exists() else directory / ".cargo" / "config.toml")
    cargo_home = environment.get("CARGO_HOME")
    cargo_home_path = Path(cargo_home) if cargo_home else Path.home() / ".cargo"
    legacy_config = cargo_home_path / "config"
    config_paths.append(legacy_config if legacy_config.exists() else cargo_home_path / "config.toml")
    configs = _cargo_config_documents(config_paths)
    if configs is None:
        # Cargo may read a configuration source that this process cannot safely resolve.
        return True

    configured_targets: set[str] = set()
    has_cfg_runner = False
    build_targets: list[str] = []
    build_target_type: type[str] | type[list] | None = None
    for config in configs:
        target_table = config.get("target")
        if isinstance(target_table, dict):
            for target_name, target_settings in target_table.items():
                if not isinstance(target_settings, dict) or "runner" not in target_settings:
                    continue
                target_key = str(target_name).strip().casefold()
                if re.match(r"cfg\s*\(", target_key):
                    has_cfg_runner = True
                else:
                    configured_targets.add(target_key)

        build_table = config.get("build")
        if not isinstance(build_table, dict) or "target" not in build_table:
            continue
        value = build_table["target"]
        if isinstance(value, str):
            if build_target_type is list:
                return True
            build_target_type = str
            build_targets.append(value)
            break
        elif isinstance(value, list) and all(isinstance(target, str) for target in value):
            if build_target_type is str:
                return True
            build_target_type = list
            build_targets.extend(value)
        else:
            return True

    if not target_runner_variables and not configured_targets and not has_cfg_runner:
        return False
    if has_cfg_runner:
        # Evaluating Cargo's cfg expression here would duplicate Cargo's target matcher.
        return True

    selected_targets: list[str] = []
    host_target: str | None = None
    explicit_targets: list[str] = []
    for position, option in enumerate(cargo_options):
        if option == "--target" and position + 1 < len(cargo_options):
            explicit_targets.append(cargo_options[position + 1])
        elif option.startswith("--target="):
            explicit_targets.append(option.split("=", 1)[1])
    if explicit_targets:
        selected_targets = explicit_targets
    elif environment.get("CARGO_BUILD_TARGET"):
        selected_targets = [environment["CARGO_BUILD_TARGET"]]
    elif build_targets:
        selected_targets = build_targets
    else:
        host_target = cargo_host_target()
        if host_target is None:
            return True
        selected_targets = [host_target]
    if test_runner == "cargo-nextest":
        host_target = host_target or cargo_host_target()
        if host_target is None:
            return True
        selected_targets.append(host_target)

    target_names: set[str] = set()
    host_tuple_requested = False
    for target in selected_targets:
        if target.casefold() == "host-tuple":
            host_tuple_requested = True
            continue
        normalized = target.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]
        target_names.add(normalized.casefold())
        target_names.add(basename.casefold())
        if basename.lower().endswith(".json"):
            target_names.add(basename[:-5].casefold())
        environment_target = re.sub(r"[-.]", "_", basename).upper()
        if f"CARGO_TARGET_{environment_target}_RUNNER" in target_runner_variables:
            return True
        if basename.lower().endswith(".json"):
            environment_target = re.sub(r"[-.]", "_", basename[:-5]).upper()
            if f"CARGO_TARGET_{environment_target}_RUNNER" in target_runner_variables:
                return True
    if host_tuple_requested:
        host_target = host_target or cargo_host_target()
        if host_target is None:
            return True
        target_names.add(host_target.casefold())
        environment_target = re.sub(r"[-.]", "_", host_target).upper()
        if f"CARGO_TARGET_{environment_target}_RUNNER" in target_runner_variables:
            return True
    return bool(target_names & configured_targets)


def _cargo_test_has_custom_harness(command: list[str]) -> bool:
    """Detect selected Cargo test targets that do not use Rust's libtest harness."""
    index = 0
    if not command:
        return False

    def executable_at(position: int) -> str:
        return command[position].replace("\\", "/").rsplit("/", 1)[-1].lower()

    if executable_at(0) in {"uv", "uv.exe"}:
        if command[1:2] != ["run"]:
            return False
        index = 2
        while index < len(command):
            if command[index] in {"--locked", "--no-sync", "--no-project", "--offline", "--frozen"}:
                index += 1
            elif command[index] in {"--extra", "--python"} and index + 1 < len(command):
                index += 2
            elif command[index] == "--":
                index += 1
                break
            else:
                break
    if index >= len(command) or executable_at(index) not in {"cargo", "cargo.exe"}:
        return False
    argument_index = index + 1
    if argument_index < len(command) and command[argument_index].startswith("+"):
        argument_index += 1
    arguments = command[argument_index:]
    if not arguments or arguments[0] != "test":
        return False
    options = arguments[1 : arguments.index("--")] if "--" in arguments else arguments[1:]

    explicit_manifest: Path | None = None
    package_names: list[str] = []
    exclusions: list[str] = []
    selectors: dict[str, list[str]] = {}
    all_targets = False
    doc_tests_only = False
    for position, option in enumerate(options):
        if option == "--manifest-path" and position + 1 < len(options):
            explicit_manifest = Path(options[position + 1])
        elif option.startswith("--manifest-path="):
            explicit_manifest = Path(option.split("=", 1)[1])
        elif option in {"-p", "--package"} and position + 1 < len(options):
            package_names.append(options[position + 1])
        elif option.startswith("--package="):
            package_names.append(option.split("=", 1)[1])
        elif option.startswith("-p") and len(option) > 2:
            package_names.append(option[2:])
        elif option == "--exclude" and position + 1 < len(options):
            exclusions.append(options[position + 1])
        elif option.startswith("--exclude="):
            exclusions.append(option.split("=", 1)[1])
        elif option == "--all-targets":
            all_targets = True
        elif option in {"--lib", "--bins", "--tests", "--examples", "--benches"}:
            selectors[option[2:-1] if option.endswith("s") else option[2:]] = []
        elif option in {"--bin", "--test", "--example", "--bench"} and position + 1 < len(options):
            kind = {"--bin": "bin", "--test": "test", "--example": "example", "--bench": "bench"}[option]
            selectors.setdefault(kind, []).append(options[position + 1])
        elif any(option.startswith(f"{flag}=") for flag in ("--bin", "--test", "--example", "--bench")):
            flag, name = option.split("=", 1)
            kind = {"--bin": "bin", "--test": "test", "--example": "example", "--bench": "bench"}[flag]
            selectors.setdefault(kind, []).append(name)
        elif option == "--doc":
            doc_tests_only = True
    if doc_tests_only:
        return False

    def read_manifest(path: Path) -> dict[str, object] | None:
        try:
            with path.open("rb") as manifest_file:
                payload = manifest_file.read(MAX_CARGO_MANIFEST_BYTES + 1)
        except OSError:
            return None
        if len(payload) > MAX_CARGO_MANIFEST_BYTES:
            return None
        try:
            parsed = tomllib.loads(payload.decode("utf-8"))
        except UnicodeDecodeError, tomllib.TOMLDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    if explicit_manifest is not None:
        manifest_path = explicit_manifest if explicit_manifest.is_absolute() else Path.cwd() / explicit_manifest
        manifest_path = manifest_path.resolve(strict=False)
        if not manifest_path.is_file():
            return True
    else:
        manifest_path = next(
            (
                directory / "Cargo.toml"
                for directory in (Path.cwd(), *Path.cwd().parents)
                if (directory / "Cargo.toml").is_file()
            ),
            None,
        )
        if manifest_path is None:
            return False
        manifest_path = manifest_path.resolve(strict=False)

    manifest = read_manifest(manifest_path)
    if manifest is None:
        return True
    workspace_path: Path | None = None
    workspace_manifest = manifest
    for directory in (manifest_path.parent, *manifest_path.parent.parents):
        candidate_path = directory / "Cargo.toml"
        if candidate_path == manifest_path:
            candidate = manifest
        elif candidate_path.is_file():
            candidate = read_manifest(candidate_path)
            if candidate is None:
                return True
        else:
            continue
        if isinstance(candidate.get("workspace"), dict):
            workspace_path = candidate_path.resolve(strict=False)
            workspace_manifest = candidate
            break

    workspace = workspace_manifest.get("workspace")
    if workspace_path is None or not isinstance(workspace, dict):
        package_manifests = [manifest_path]
        workspace_root = manifest_path.parent
    else:
        workspace_root = workspace_path.parent
        member_patterns = workspace.get("members", [])
        exclude_patterns = workspace.get("exclude", [])
        if not isinstance(member_patterns, list) or not all(isinstance(item, str) for item in member_patterns):
            return True
        if not isinstance(exclude_patterns, list) or not all(isinstance(item, str) for item in exclude_patterns):
            return True
        candidates: set[Path] = set()
        if isinstance(workspace_manifest.get("package"), dict):
            candidates.add(workspace_path)
        try:
            for pattern in member_patterns:
                for member in workspace_root.glob(pattern):
                    member_path = member / "Cargo.toml" if member.is_dir() else member
                    if member_path.name == "Cargo.toml" and member_path.is_file():
                        candidates.add(member_path.resolve(strict=False))
        except OSError, ValueError:
            return True
        if len(candidates) > MAX_CARGO_WORKSPACE_MEMBERS:
            return True
        package_manifests = []
        for candidate_path in candidates:
            try:
                relative = candidate_path.parent.relative_to(workspace_root).as_posix()
            except ValueError:
                return True
            excluded = any(
                relative == pattern.rstrip("/")
                or fnmatch.fnmatchcase(relative, pattern)
                or Path(relative).match(pattern)
                for pattern in exclude_patterns
            )
            if not excluded:
                package_manifests.append(candidate_path)
        package_manifests.sort()
        if not package_manifests:
            return True

    package_documents: dict[Path, dict[str, object]] = {}
    package_by_name: dict[str, Path] = {}
    for package_manifest in package_manifests:
        package_data = manifest if package_manifest == manifest_path else read_manifest(package_manifest)
        if package_data is None:
            return True
        package_table = package_data.get("package")
        if isinstance(package_table, dict) and isinstance(package_table.get("name"), str):
            package_documents[package_manifest] = package_data
            package_by_name[package_table["name"]] = package_manifest
    if package_names:
        selected_manifests: set[Path] = set()
        for selector in package_names:
            name, separator, version = selector.partition("@")
            package_manifest = package_by_name.get(name)
            if package_manifest is None:
                return True
            package_data = package_documents[package_manifest]["package"]
            if separator and package_data.get("version") != version:
                return True
            selected_manifests.add(package_manifest)
    elif "--workspace" in options or "--all" in options:
        selected_manifests = set(package_documents)
    elif explicit_manifest is not None and isinstance(manifest.get("package"), dict):
        selected_manifests = {manifest_path}
    elif workspace_path is None:
        selected_manifests = set(package_documents)
    elif Path.cwd().resolve(strict=False) != workspace_root:
        selected_manifests = {
            package_manifest
            for package_manifest in package_documents
            if Path.cwd().resolve(strict=False).is_relative_to(package_manifest.parent)
        }
        if not selected_manifests:
            selected_manifests = set(package_documents)
    else:
        default_patterns = workspace.get("default-members")
        if default_patterns is None:
            selected_manifests = (
                {workspace_path}
                if isinstance(workspace_manifest.get("package"), dict) and workspace_path in package_documents
                else set(package_documents)
            )
        elif isinstance(default_patterns, list) and all(isinstance(item, str) for item in default_patterns):
            selected_manifests = {
                package_manifest
                for package_manifest in package_documents
                if any(
                    package_manifest.parent == (workspace_root / pattern).resolve(strict=False)
                    or package_manifest
                    in {match / "Cargo.toml" if match.is_dir() else match for match in workspace_root.glob(pattern)}
                    for pattern in default_patterns
                )
            }
        else:
            return True
    if "--workspace" in options or "--all" in options:
        selected_manifests = {
            package_manifest
            for package_manifest in selected_manifests
            if package_documents[package_manifest]["package"].get("name") not in exclusions
        }

    def selected_target(kind: str, name: str, target: dict[str, object]) -> bool:
        if all_targets:
            return target.get("test", True) is not False
        if not selectors:
            if kind in {"lib", "bin", "test"}:
                return target.get("test", True) is not False
            return kind == "example" and target.get("test", False) is True
        if kind not in selectors:
            return False
        names = selectors[kind]
        return not names or name in names

    for package_manifest in selected_manifests:
        package_data = package_documents.get(package_manifest)
        if package_data is None:
            return True
        package_table = package_data.get("package")
        if not isinstance(package_table, dict):
            return True
        targets: list[tuple[str, dict[str, object]]] = []
        lib_target = package_data.get("lib")
        if isinstance(lib_target, dict):
            targets.append(("lib", lib_target))
        for kind in ("bin", "test", "example", "bench"):
            definitions = package_data.get(kind, [])
            if isinstance(definitions, dict) and kind == "bin":
                definitions = [definitions]
            if not isinstance(definitions, list):
                continue
            targets.extend((kind, target) for target in definitions if isinstance(target, dict))
        for kind, target in targets:
            name = target.get("name")
            if kind == "lib" and not isinstance(name, str):
                name = str(package_table.get("name", "")).replace("-", "_")
            if not isinstance(name, str) or not selected_target(kind, name, target):
                continue
            harness = target.get("harness", True)
            if not isinstance(harness, bool):
                return True
            if harness is False:
                return True
    return False


def suite_execution_valid(candidate: Mapping[str, object]) -> bool:
    """Validate observed command/report metadata, without claiming independent attestation."""
    raw_argv = candidate.get("command_argv")
    command = candidate.get("command")
    if type(raw_argv) is not list or not raw_argv or any(type(part) is not str for part in raw_argv):
        return False
    argv = cast(list[str], raw_argv)
    if not isinstance(command, str):
        return False
    try:
        if shlex.split(command, comments=False) != argv:
            return False
    except ValueError:
        return False
    runner = _test_runner(argv)
    if runner is None or candidate.get("count_source") != COUNT_SOURCES[runner]:
        return False
    executed = candidate.get("executed_command_argv")
    if runner != "pytest":
        return executed == argv
    report_hash = candidate.get("runner_report_sha256")
    report_bytes = candidate.get("runner_report_bytes")
    if type(executed) is not list or executed[: len(argv)] != argv:
        return False
    suffix = executed[len(argv) :]
    valid_pytest_suffix = (
        len(suffix) == 6 and suffix[:-1] == ["-o", "junit_family=xunit2", "-o", "junit_logging=no", "--junitxml"]
    ) or (
        len(suffix) == 8
        and suffix[:-1] == ["-o", "addopts=", "-o", "junit_family=xunit2", "-o", "junit_logging=no", "--junitxml"]
    )
    return (
        valid_pytest_suffix
        and isinstance(suffix[-1], str)
        and bool(suffix[-1])
        and isinstance(report_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", report_hash) is not None
        and type(report_bytes) is int
        and 0 < report_bytes <= MAX_JUNIT_BYTES
    )


def _empty_counts() -> dict[str, int]:
    return dict.fromkeys(("discovered", *COUNT_PATTERNS), 0)


def _pytest_report(path: Path) -> tuple[dict[str, int], str, int]:
    if path.is_symlink():
        raise ValueError("pytest report must be a regular file")
    with path.open("rb") as handle:
        payload = handle.read(MAX_JUNIT_BYTES + 1)
    if not payload or len(payload) > MAX_JUNIT_BYTES or b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("pytest report is empty, oversized, or contains unsupported declarations")
    root = ElementTree.fromstring(payload)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("pytest report has an unsupported root")
    suites = [root] if root.tag == "testsuite" else list(root)
    counts = _empty_counts()
    identities: set[tuple[str, str]] = set()
    for suite in suites:
        if suite.tag != "testsuite":
            raise ValueError("pytest report contains an unsupported suite")
        cases = suite.findall("testcase")
        observed = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
        for case in cases:
            identity = (case.get("classname", ""), case.get("name", ""))
            if not all(identity) or identity in identities:
                raise ValueError("pytest report has missing or duplicate test identities")
            identities.add(identity)
            outcomes = [child.tag for child in case if child.tag in {"failure", "error", "skipped"}]
            if len(outcomes) > 1 or any(
                child.tag not in {"failure", "error", "skipped", "properties", "system-out", "system-err"}
                for child in case
            ):
                raise ValueError("pytest report contains an unsupported outcome")
            if outcomes:
                observed[{"failure": "failures", "error": "errors", "skipped": "skipped"}[outcomes[0]]] += 1
        for field, value in observed.items():
            declared = suite.get(field, "")
            if re.fullmatch(r"[0-9]+", declared) is None or int(declared) != value:
                raise ValueError("pytest report counts do not match its test cases")
        counts["discovered"] += observed["tests"]
        counts["failed"] += observed["failures"] + observed["errors"]
        counts["skipped"] += observed["skipped"]
    counts["passed"] = counts["discovered"] - counts["failed"] - counts["skipped"]
    return counts, hashlib.sha256(payload).hexdigest(), len(payload)


def _rust_counts(output: str, runner: str) -> dict[str, int]:
    clean = ANSI_ESCAPE_RE.sub("", output)
    if runner == "cargo-nextest":
        summaries = NEXTEST_SUMMARY_RE.findall(clean)
        counts = parse_counts(summaries[0]) if len(summaries) == 1 else _empty_counts()
        # Nextest's run total excludes skips; discovered includes every reported case.
        counts["discovered"] += counts["skipped"]
        return counts
    counts = _empty_counts()
    for match in RUST_SUMMARY_RE.finditer(clean):
        for field in ("passed", "failed", "ignored", "filtered"):
            counts[field] += int(match.group(field))
    counts["discovered"] = counts["passed"] + counts["failed"] + counts["ignored"]
    return counts


def parse_counts(output: str) -> dict[str, int]:
    output = ANSI_ESCAPE_RE.sub("", output)
    counts: dict[str, int] = {name: 0 for name in COUNT_PATTERNS}
    for name, pattern in COUNT_PATTERNS.items():
        matches = [int(match.group("count")) for match in pattern.finditer(output)]
        if matches:
            counts[name] = max(matches)
    discovered_match = DISCOVERED_RE.search(output)
    if discovered_match:
        discovered = int(discovered_match.group("count"))
    else:
        discovered = sum(int(counts[name] or 0) for name in ("passed", "failed", "ignored", "skipped"))
    counts["discovered"] = discovered
    return counts


def classify_result(
    *,
    exit_code: int,
    timed_out: bool,
    discovered: int | None,
    passed: int | None,
    failed: int | None,
    ignored: int | None,
    skipped: int | None,
) -> tuple[str, str, bool, str | None]:
    """Classify a bounded suite without promoting empty, inconsistent, or failing evidence."""

    if timed_out:
        return "FAILED", "LOCAL TIMEOUT", False, "TIMEOUT"
    if exit_code != 0:
        return "FAILED", "LOCAL FAILED", False, "NON_ZERO_EXIT"
    if type(failed) is int and failed > 0:
        return "FAILED", "LOCAL FAILED", False, "FAILED_TESTS"
    if type(discovered) is not int or discovered <= 0:
        return "NOT_VERIFIED", "LOCAL ZERO TESTS", False, "ZERO_TESTS"
    if type(passed) is not int or passed <= 0:
        return "NOT_VERIFIED", "LOCAL NO PASSES", False, "NO_PASSED_TESTS"
    result_counts = (passed, failed, ignored, skipped)
    if any(type(count) is not int or count < 0 for count in result_counts):
        return "NOT_VERIFIED", "LOCAL INCONSISTENT COUNTS", False, "INCONSISTENT_COUNTS"
    if discovered != sum(result_counts):
        return "NOT_VERIFIED", "LOCAL INCONSISTENT COUNTS", False, "INCONSISTENT_COUNTS"
    return "PROVEN", "LOCALLY PROVEN", True, None


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


def _git_worktree_fingerprint() -> tuple[str, str]:
    """Fingerprint tracked changes and non-ignored untracked files.

    A commit SHA alone does not identify a dirty checkout.  The fingerprint
    deliberately excludes ignored files (which may contain local secrets or
    build output) and fails closed when Git cannot describe the checkout.
    """

    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        check=False,
    )
    diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"],
        capture_output=True,
        check=False,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    if status.returncode != 0 or diff.returncode != 0 or untracked.returncode != 0:
        raise RuntimeError("unable to fingerprint the Git worktree")

    digest = hashlib.sha256()
    digest.update(b"aegis-suite-worktree-v1\0")
    digest.update(status.stdout)
    digest.update(b"\0tracked-diff\0")
    digest.update(diff.stdout)
    paths = sorted(path for path in untracked.stdout.split(b"\0") if path)
    for raw_path in paths:
        path = Path(os.fsdecode(raw_path))
        digest.update(b"\0untracked\0")
        digest.update(raw_path)
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            raise RuntimeError(f"unable to read untracked worktree file: {path}") from exc
    return digest.hexdigest(), "DIRTY" if status.stdout else "CLEAN"


def execution_run_key(
    *,
    gate_id: str,
    commit: str,
    command: list[str],
    platform_name: str,
    toolchain_name: str,
    claim_scope: str,
    worktree_fingerprint: str | None = None,
) -> str:
    """Identify duplicate work independently of a human-readable attempt ID."""
    payload = {
        "gate_id": gate_id,
        "commit": commit,
        "command": command,
        "platform": platform_name,
        "toolchain": toolchain_name,
        "claim_scope": claim_scope,
        "worktree_fingerprint": worktree_fingerprint,
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _capture_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class _WindowsProcessJob:
    """Own one Windows Job Object so every child ends with its runner scope."""

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        import ctypes
        import uuid
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = None
        self.name = f"Local\\AEGIS-SuiteEvidence-{uuid.uuid4().hex}"

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = self._kernel32.CreateJobObjectW(None, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        self._handle = handle
        if error == self._ERROR_ALREADY_EXISTS:
            self.close()
            raise ctypes.WinError(error)

        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def terminate(self) -> bool:
        return self._handle is not None and bool(self._kernel32.TerminateJobObject(self._handle, 1))

    def close(self) -> bool:
        if self._handle is None:
            return True
        if not self._kernel32.CloseHandle(self._handle):
            return False
        self._handle = None
        return True


def _assign_current_process_to_windows_job(job_name: str) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job_handle = kernel32.OpenJobObjectW(0x0001, False, job_name)
    if not job_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    assignment_error = None
    try:
        if not kernel32.AssignProcessToJobObject(job_handle, kernel32.GetCurrentProcess()):
            assignment_error = ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel32.CloseHandle(job_handle) and assignment_error is None:
            assignment_error = ctypes.WinError(ctypes.get_last_error())
    if assignment_error is not None:
        raise assignment_error


def _windows_job_child_entry(arguments: list[str]) -> int:
    if len(arguments) < 3 or arguments[1] != "--" or not arguments[2:]:
        print("invalid internal Windows job invocation", file=sys.stderr)
        return 125

    job_name, _, *command = arguments
    try:
        _assign_current_process_to_windows_job(job_name)
    except OSError as exc:
        print(f"Windows Job Object assignment failed: {exc}", file=sys.stderr)
        return 125

    sanitized_env = os.environ.copy()
    for bad_key in list(sanitized_env):
        if bad_key.casefold() in ("pytest_addopts", "pytest_plugins"):
            sanitized_env.pop(bad_key, None)
    try:
        return subprocess.run(command, env=sanitized_env, check=False).returncode
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 127


def run_command(command: list[str], timeout_seconds: int) -> tuple[str, str, int, bool]:
    """Run one command with a bounded deadline and explicit timeout state."""
    windows_job = None
    launch_command = command
    if os.name == "nt":
        try:
            windows_job = _WindowsProcessJob()
        except OSError as exc:
            return "", str(exc), 127, False
        launch_command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--_windows-job-child",
            windows_job.name,
            "--",
            *command,
        ]

    raw_env = getattr(os, "environ", None)
    sanitized_env: dict[str, str] | None = None
    if raw_env is not None:
        sanitized_env = dict(raw_env)
        for bad_key in list(sanitized_env):
            if bad_key.casefold() in ("pytest_addopts", "pytest_plugins"):
                sanitized_env.pop(bad_key, None)
        pathsep = getattr(os, "pathsep", ":")
        lib_dirs: list[str] = []
        for candidate in (
            sysconfig.get_config_var("LIBDIR"),
            str(Path(sys.base_prefix) / "lib"),
            str(Path(sys.prefix) / "lib"),
            str(Path(sys.base_prefix)),
        ):
            if isinstance(candidate, str) and candidate:
                with suppress(OSError):
                    resolved = str(Path(candidate).expanduser().resolve())
                    if Path(resolved).is_dir() and resolved not in lib_dirs:
                        lib_dirs.append(resolved)
        if lib_dirs:
            if sys.platform == "darwin":
                for var in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
                    existing = sanitized_env.get(var, "")
                    sanitized_env[var] = pathsep.join([*lib_dirs, *([existing] if existing else [])])
            elif os.name != "nt":
                existing = sanitized_env.get("LD_LIBRARY_PATH", "")
                sanitized_env["LD_LIBRARY_PATH"] = pathsep.join([*lib_dirs, *([existing] if existing else [])])

    result: tuple[str, str, int, bool] | None = None
    try:
        try:
            process = subprocess.Popen(
                launch_command,
                env=sanitized_env,
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
            result = "", str(exc), 127, False
        else:
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                if windows_job is not None:
                    if not windows_job.terminate():
                        _terminate_process_tree(process)
                else:
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
                result = _capture_text(stdout), _capture_text(stderr), 124, True
            else:
                if os.name != "nt" and isinstance(process, subprocess.Popen):
                    killpg = getattr(os, "killpg", None)
                    if killpg is not None:
                        with suppress(OSError):
                            killpg(process.pid, signal.SIGKILL)
                result = _capture_text(stdout), _capture_text(stderr), process.returncode, False
    finally:
        if windows_job is not None and not windows_job.close():
            windows_job.terminate()
            windows_job.close()
            if result is not None:
                stdout, stderr, exit_code, timed_out = result
                diagnostic = "Windows Job Object could not be closed cleanly; process cleanup is unverified"
                result = (
                    stdout,
                    f"{stderr}\n{diagnostic}" if stderr else diagnostic,
                    exit_code if exit_code != 0 else 125,
                    timed_out,
                )
    if result is None:
        return "", "command did not produce a result", 125, False
    return result


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
        except OSError, subprocess.TimeoutExpired:
            with suppress(OSError):
                process.kill()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        with suppress(OSError):
            process.kill()
        return
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)
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
        contains = (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", revision, "origin/main"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError, subprocess.CalledProcessError:
        remote_head = ""
        contains = False
    return {
        "ref": "origin/main",
        "head": remote_head,
        "contains_commit": contains,
        "observation_scope": "LOCAL_GIT_REF_CACHE_ONLY",
        "independent_verification": "NOT VERIFIED",
    }


def _public_status(evidence: Mapping[str, object]) -> dict[str, str]:
    """Return the fixed, low-detail status allowed in public CI logs."""

    status = evidence.get("status")
    public: dict[str, str] = {
        "schema": "aegis-suite-status-v1",
        "status": status if isinstance(status, str) and status in PUBLIC_STATUSES else "NOT_VERIFIED",
    }
    gate_id = evidence.get("gate_id")
    if isinstance(gate_id, str) and PUBLIC_GATE_ID_RE.fullmatch(gate_id):
        public["gate_id"] = gate_id
    failure_reason = evidence.get("failure_reason")
    if isinstance(failure_reason, str) and failure_reason in PUBLIC_FAILURE_REASONS:
        public["failure_reason"] = failure_reason
    return public


def canonical_receipt_digest(receipt: Mapping[str, Any]) -> str:
    """Calculate the canonical SHA-256 digest of a suite evidence receipt for local tamper-evidence.

    NOTE: Per Plan §5.3 and parent security directives, this digest provides
    tamper-evidence within the same OS user process/session only. It is NOT
    independent attestation and does not satisfy NV004.
    """
    receipt_copy = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    canonical_bytes = json.dumps(receipt_copy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def verify_receipt(path: Path) -> tuple[bool, str]:
    """Verify the tamper-evidence integrity and counts of a suite evidence receipt."""
    if not path.is_file():
        return False, f"receipt file not found: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, f"failed to read receipt JSON: {exc}"
    if not isinstance(data, dict):
        return False, "receipt root is not a JSON object"

    recorded_digest = data.get("receipt_digest")
    if not recorded_digest or not isinstance(recorded_digest, str):
        return False, "receipt is missing receipt_digest field"

    expected_digest = canonical_receipt_digest(data)
    if recorded_digest != expected_digest:
        return False, f"tamper-evidence digest mismatch: recorded {recorded_digest} != computed {expected_digest}"

    # Zero-assertion rejection
    passed = data.get("passed", 0)
    discovered = data.get("discovered", 0)
    status = data.get("status")
    if (
        status != "PROVEN"
        or not isinstance(passed, int)
        or passed <= 0
        or not isinstance(discovered, int)
        or discovered <= 0
    ):
        return False, f"zero-assertion rejection: gate status is {status}, discovered={discovered}, passed={passed}"

    return True, f"receipt verified successfully ({passed}/{discovered} passed, digest={recorded_digest[:16]}...)"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        sys.argv.pop(1)
    if len(sys.argv) > 1 and sys.argv[1] in ("verify", "--verify"):
        if len(sys.argv) < 3:
            print("Usage: suite_evidence.py verify <receipt_path>", file=sys.stderr)
            return 2
        receipt_path = Path(sys.argv[2])
        ok, msg = verify_receipt(receipt_path)
        if not ok:
            print(f"VERIFICATION FAILED: {msg}", file=sys.stderr)
            return 1
        print(f"VERIFIED: {msg}")
        return 0

    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status-output", type=Path, help="write only the sanitized status contract")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--attempt-id")
    parser.add_argument("--platform-id", help="canonical hosted-runner label for cross-platform evidence")
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.status_output is not None and args.status_output.resolve() == args.output.resolve():
        parser.error("--status-output must be separate from private suite evidence")
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
    platform_id = args.platform_id.strip() if args.platform_id else None
    if args.platform_id is not None and not platform_id:
        raise SystemExit("--platform-id must be non-empty when provided")
    platform_name = platform.platform()
    toolchain_name = toolchain(command)
    claim_scope = "LOCAL_CHECKOUT_ONLY"
    worktree_fingerprint, worktree_status = _git_worktree_fingerprint()
    attempt_id = args.attempt_id or os.environ.get("GITHUB_RUN_ID") or "local"
    run_key = execution_run_key(
        gate_id=gate_id,
        commit=revision,
        command=command,
        platform_name=platform_name,
        toolchain_name=toolchain_name,
        claim_scope=claim_scope,
        worktree_fingerprint=worktree_fingerprint,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    runner = _test_runner(command)
    cargo_runner_override = runner in {"cargo-test", "cargo-nextest"} and _cargo_runner_configuration_present(
        command, runner
    )
    cargo_custom_harness = runner == "cargo-test" and _cargo_test_has_custom_harness(command)
    executed_command = list(command)
    report_counts: dict[str, int] | None = None
    report_sha256: str | None = None
    report_bytes: int | None = None
    report_error: str | None = None
    try:
        with exclusive_suite_lock(args.output.parent / ".suite-evidence.lock"):
            started = datetime.now(UTC)
            report_context = (
                tempfile.TemporaryDirectory(prefix=".pytest-evidence-", dir=args.output.parent)
                if runner == "pytest"
                else nullcontext(None)
            )
            with report_context as report_directory:
                report_path = Path(report_directory) / "junit.xml" if report_directory is not None else None
                if runner == "pytest":
                    executed_command.extend(["-o", "addopts="])
                if report_path is not None:
                    executed_command.extend(
                        ["-o", "junit_family=xunit2", "-o", "junit_logging=no", "--junitxml", str(report_path)]
                    )
                stdout, stderr, exit_code, timed_out = run_command(executed_command, args.timeout_seconds)
                if report_path is not None and not timed_out:
                    try:
                        report_counts, report_sha256, report_bytes = _pytest_report(report_path)
                    except OSError, ValueError, ElementTree.ParseError:
                        report_error = "pytest did not produce a valid bounded JUnit report"
            finished = datetime.now(UTC)
    except RuntimeError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 75
    output = stdout + stderr
    if runner == "pytest":
        counts = report_counts if report_counts is not None else _empty_counts()
    elif runner in {"cargo-test", "cargo-nextest"}:
        counts = _rust_counts(output, runner)
    else:
        counts = parse_counts(output)
    status, claim_label, release_eligible, failure_reason = classify_result(
        exit_code=exit_code,
        timed_out=timed_out,
        discovered=counts["discovered"],
        passed=counts["passed"],
        failed=counts["failed"],
        ignored=counts["ignored"],
        skipped=counts["skipped"],
    )
    if status == "PROVEN" and cargo_runner_override:
        status, claim_label, release_eligible, failure_reason = (
            "NOT_VERIFIED",
            "LOCAL CARGO RUNNER OVERRIDE",
            False,
            "UNSUPPORTED_TEST_RUNNER",
        )
    if status == "PROVEN" and cargo_custom_harness:
        status, claim_label, release_eligible, failure_reason = (
            "NOT_VERIFIED",
            "LOCAL CARGO CUSTOM HARNESS",
            False,
            "UNSUPPORTED_TEST_HARNESS",
        )
    if status == "PROVEN" and runner is None:
        status, claim_label, release_eligible, failure_reason = (
            "NOT_VERIFIED",
            "LOCAL UNVERIFIED RUNNER",
            False,
            "UNSUPPORTED_TEST_RUNNER",
        )
    if report_error is not None and not timed_out and exit_code == 0:
        status, claim_label, release_eligible, failure_reason = (
            "NOT_VERIFIED",
            "LOCAL INVALID RUNNER REPORT",
            False,
            "INVALID_RUNNER_REPORT",
        )
    result = {
        "schema": "aegis-suite-evidence-v1",
        "name": args.name,
        "command": shlex.join(command),
        "command_argv": command,
        "executed_command_argv": executed_command,
        "count_source": COUNT_SOURCES.get(runner or "", "UNVERIFIED_OUTPUT"),
        "runner_report_sha256": report_sha256,
        "runner_report_bytes": report_bytes,
        "commit": revision,
        "worktree_status": worktree_status,
        "worktree_sha256": worktree_fingerprint,
        "timestamp_utc": finished.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "duration_seconds": (finished - started).total_seconds(),
        "platform": platform_name,
        "platform_id": platform_id,
        "toolchain": toolchain_name,
        "discovered": counts["discovered"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "ignored": counts["ignored"],
        "filtered": counts["filtered"],
        "skipped": counts["skipped"],
        "exit_code": exit_code,
        "status": status,
        "claim_scope": claim_scope,
        "claim_label": claim_label,
        "independent_verification": "NOT VERIFIED",
        "owner_id": owner_id,
        "gate_id": gate_id,
        "attempt_id": attempt_id,
        "run_key": run_key,
        "release_eligible": release_eligible,
        "timeout_seconds": args.timeout_seconds,
        "timed_out": timed_out,
        "termination": "TIMEOUT" if timed_out else "EXITED",
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr),
        "combined_output_sha256": _sha256(output),
        "remote_observation": _git_remote_observation(revision),
    }
    if failure_reason is not None:
        result["failure_reason"] = failure_reason
    result["tamper_evidence_label"] = "LOCAL_TAMPER_EVIDENCE_ONLY_SAME_USER_NOT_ATTESTATION"
    result["receipt_digest"] = canonical_receipt_digest(result)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.status_output is not None:
        args.status_output.parent.mkdir(parents=True, exist_ok=True)
        args.status_output.write_text(
            json.dumps(_public_status(result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print(output, end="")
        printable_result: Mapping[str, object] = result
    else:
        printable_result = _public_status(result)
    print(json.dumps(printable_result, sort_keys=True))
    return exit_code if exit_code != 0 else (1 if status == "FAILED" else 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--_windows-job-child"]:
        raise SystemExit(_windows_job_child_entry(sys.argv[2:]))
    raise SystemExit(main())
