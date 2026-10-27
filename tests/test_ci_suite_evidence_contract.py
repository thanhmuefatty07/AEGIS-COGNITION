from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from scripts import cross_platform_evidence_gate, evidence_consistency_gate, suite_evidence


ROOT = Path(__file__).parents[1]
WORKFLOWS = tuple(
    sorted(
        (path for pattern in ("*.yml", "*.yaml") for path in (ROOT / ".github" / "workflows").glob(pattern)),
        key=lambda path: path.name,
    )
)
PYTHON_JOBS = (
    "python-fast-gate",
    "desktop-build",
    "cross-platform-full-suite",
    "wheel-parity",
)
REQUIRED_SUITE_GATES = {
    "plugins-rust-workspace",
    "rust-default-members",
    "python-cross-language",
    "cross-platform-full-suite-${{matrix.platform-id}}",
    "windows-platform-contracts",
    "rust-workspace-doctests",
    "rust-full-workspace",
    "provider-public-catalog-no-key",
    "release-rust-workspace",
    "release-python-cross-language",
    "tauri-host-contracts-${{matrix.os}}",
    "wasm-plugin-runtime-${{matrix.os}}",
    "wp12-windows-native-process",
    "wp12-runtime-deadline",
    "wp12-linux-cgroup-enforcement",
}
EVIDENCE_AUTHORITY_PATHS = {
    "docs/architecture/evidence/current.json",
    "docs/architecture/not_verified_registry.json",
    "docs/architecture/deployment_policy.json",
    "docs/architecture/NOT_VERIFIED_REGISTRY.md",
    "docs/architecture/GT96_TRACEABILITY.md",
    "docs/architecture/GT96_TRACEABILITY_DETAIL.md",
    "docs/architecture/VERIFICATION_INDEX.md",
    "scripts/evidence_consistency_gate.py",
}


@pytest.fixture(scope="module")
def ci_jobs() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]


@pytest.mark.parametrize("job_id", PYTHON_JOBS)
def test_python_jobs_bind_uv_to_the_setup_python_request(ci_jobs: dict, job_id: str) -> None:
    job = ci_jobs[job_id]
    setup = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    expected = setup["with"]["python-version"]
    for step in job["steps"]:
        if re.search(r"\buv (?:sync|run|build)\b", step.get("run", "")):
            environment = {**job.get("env", {}), **step.get("env", {})}
            assert environment.get("UV_PYTHON") == expected, (job_id, step["name"])


@pytest.mark.parametrize("job_id", ("python-fast-gate", "wheel-parity"))
def test_python_matrix_request_overrides_the_project_pin(ci_jobs: dict, job_id: str, tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to verify CI interpreter selection"
    request = ci_jobs[job_id].get("env", {}).get("UV_PYTHON", "")
    request = request.replace("${{ matrix.python-version }}", sys.executable)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME"}
    }
    environment["UV_CACHE_DIR"] = str(tmp_path / "cache")
    if request:
        environment["UV_PYTHON"] = request
    (tmp_path / ".python-version").write_text("99.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ci-python-selection-probe"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    for command in (
        [uv, "sync", "--offline", "--no-python-downloads", "--no-install-project"],
        [
            uv,
            "run",
            "--offline",
            "--no-python-downloads",
            "--no-sync",
            "python",
            "-c",
            "import sys; print(sys.version)",
        ],
    ):
        result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.version.strip()


@pytest.mark.parametrize("job_id", PYTHON_JOBS)
@pytest.mark.parametrize(
    ("requested", "actual", "rejected"),
    (
        ("3.14.7", "3.14.7", False),
        ("3.15.0-rc.1", "3.15.0rc1", False),
        ("3.15.0-rc.1", "3.14.7", True),
        ("3.14.7", "3.15.0rc1", True),
    ),
)
def test_python_runtime_guard_rejects_a_mislabeled_lane(
    ci_jobs: dict, job_id: str, requested: str, actual: str, rejected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    guards = [step for step in ci_jobs[job_id]["steps"] if step.get("name") == "Verify selected Python runtime"]
    assert len(guards) == 1, f"{job_id} must verify its selected runtime"
    command = shlex.split(guards[0]["run"])
    code = command[command.index("-c") + 1]
    monkeypatch.setenv("UV_PYTHON", requested)
    monkeypatch.setattr(platform, "python_version", lambda: actual)
    if rejected:
        with pytest.raises(SystemExit, match="Expected Python"):
            exec(compile(code, "<ci-python-runtime-guard>", "exec"), {})
    else:
        exec(compile(code, "<ci-python-runtime-guard>", "exec"), {})


def test_python_matrix_artifact_names_are_unique(ci_jobs: dict) -> None:
    job = ci_jobs["python-fast-gate"]
    versions = [lane["python-version"] for lane in job["strategy"]["matrix"]["include"]]
    uploads = [step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert uploads
    for upload in uploads:
        names = [upload["with"]["name"].replace("${{ matrix.python-version }}", version) for version in versions]
        assert len(names) == len(set(names)), names


def _is_scoped_cargo_probe(command: list[str], working_directory: Path) -> bool:
    if (
        not command
        or command[0] != "cargo"
        or {"--workspace", "--all", "--", ";", "&&", "||", "|", "&"}.intersection(command)
    ):
        return False
    subcommand_index = 1
    if subcommand_index < len(command) and command[subcommand_index].startswith("+"):
        subcommand_index += 1
    if command[subcommand_index : subcommand_index + 1] != ["test"]:
        return False
    if "-p" in command and "--lib" in command:
        package = command[command.index("-p") + 1]
        return bool(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", package)
            and re.fullmatch(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)+", command[-1])
        )
    if "--manifest-path" in command:
        manifest = (working_directory / command[command.index("--manifest-path") + 1]).resolve()
        if manifest.is_relative_to(ROOT) and manifest.is_file():
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
            if "package" in data and data.get("workspace") == {}:
                return True
            return (
                "package" in data
                and "--lib" in command
                and re.fullmatch(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)+", command[-1]) is not None
            )
    return False


def test_scoped_cargo_probe_accepts_an_explicit_toolchain_selector() -> None:
    command = ["cargo", "+stable", "test", "-p", "aegis-nerve", "--lib", "wasm_plugins::tests"]

    assert _is_scoped_cargo_probe(command, ROOT)


def _assert_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout() -> None:
    gates = []
    for workflow_path in WORKFLOWS:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_id, job in workflow["jobs"].items():
            if "runs-on" in job:
                timeout = job.get("timeout-minutes")
                assert type(timeout) is int and timeout > 0
            for step in job.get("steps", []):
                for line in step.get("run", "").splitlines():
                    if not re.search(
                        r"\b(?:suite_evidence\.py|cargo\s+(?:\+\S+\s+)?(?:test|nextest\s+run)|pytest)\b",
                        line,
                    ):
                        continue
                    expanded = re.sub(r"\$\{\{.*?\}\}", lambda match: re.sub(r"\s+", "", match[0]), line)
                    command = shlex.split(expanded, comments=True)
                    # The validator compares the recorded command; this step does not execute it.
                    if command[:2] == ["python", "scripts/cross_platform_evidence_gate.py"]:
                        continue
                    if command[:2] == ["python", "scripts/suite_evidence.py"]:
                        concurrency = workflow.get("concurrency", {})
                        assert concurrency.get("group")
                        assert type(concurrency.get("cancel-in-progress")) is bool
                        assert "--" in command and command[-1] != "--"
                        arguments = command[: command.index("--")]
                        values = {}
                        for option in ("--owner-id", "--gate-id", "--attempt-id", "--timeout-seconds"):
                            assert option in arguments
                            value = arguments[arguments.index(option) + 1]
                            assert value.strip() and not value.startswith("--")
                            values[option] = value
                        assert int(values["--timeout-seconds"]) > 0
                        assert "--name" in arguments
                        assert arguments[arguments.index("--name") + 1].strip()
                        assert "--output" in arguments
                        output_path = arguments[arguments.index("--output") + 1]
                        assert output_path.startswith("artifacts/suites/") and output_path.endswith(".json")
                        wrapped_command = command[command.index("--") + 1 :]
                        if (
                            values["--gate-id"].startswith("cross-platform-full-suite-")
                            or values["--gate-id"] == "windows-platform-contracts"
                        ):
                            assert workflow_path.name == "ci.yml" and job_id == "cross-platform-full-suite"
                            paths = (
                                ["tests/platform/windows.py"]
                                if values["--gate-id"] == "windows-platform-contracts"
                                else ["tests", "core/python/tests.py"]
                            )
                            assert wrapped_command == [
                                "uv",
                                "run",
                                "--locked",
                                "--extra",
                                "all",
                                "--extra",
                                "dev",
                                "python",
                                "-m",
                                "pytest",
                                *paths,
                                "-v",
                                "-W",
                                "error::DeprecationWarning",
                            ], "required suite command must be the executed child argv"
                        elif values["--gate-id"].startswith("tauri-host-contracts-"):
                            assert workflow_path.name == "ci.yml" and job_id == "desktop-build"
                            assert step.get("working-directory") == "."
                            assert "--platform-id" in arguments
                            assert arguments[arguments.index("--platform-id") + 1] == "${{matrix.os}}"
                            assert wrapped_command == [
                                "cargo",
                                "test",
                                "--locked",
                                "--manifest-path",
                                "desktop/src-tauri/Cargo.toml",
                            ]
                        elif values["--gate-id"].startswith("wasm-plugin-runtime-"):
                            assert workflow_path.name == "ci.yml" and job_id == "wheel-parity"
                            assert "--platform-id" in arguments
                            assert arguments[arguments.index("--platform-id") + 1] == "${{matrix.os}}"
                            assert wrapped_command == [
                                "cargo",
                                "test",
                                "--locked",
                                "--release",
                                "-p",
                                "aegis-nerve",
                                "--features",
                                "wasm-plugins",
                                "--lib",
                                "wasm_plugins::tests",
                            ]
                        elif values["--gate-id"] == "wp12-windows-native-process":
                            assert workflow_path.name == "deep.yml" and job_id == "wp12-windows-native-process"
                            assert wrapped_command == [
                                "cargo",
                                "test",
                                "--locked",
                                "--manifest-path",
                                "core/rust/Cargo.toml",
                                "--lib",
                                "--no-default-features",
                                "execution::tests::native_process_lane_",
                            ]
                        elif values["--gate-id"] == "wp12-runtime-deadline":
                            assert workflow_path.name == "deep.yml" and job_id == "wp12-windows-native-process"
                            assert wrapped_command == [
                                "cargo",
                                "test",
                                "--locked",
                                "--manifest-path",
                                "core/rust/Cargo.toml",
                                "--lib",
                                "--no-default-features",
                                "runtime::tests::",
                            ]
                        elif values["--gate-id"] == "wp12-linux-cgroup-enforcement":
                            assert workflow_path.name == "deep.yml" and job_id == "wp12-linux-cgroup-enforcement"
                            assert wrapped_command == [
                                "cargo",
                                "test",
                                "--locked",
                                "--manifest-path",
                                "core/rust/Cargo.toml",
                                "--no-default-features",
                                "--test",
                                "linux_cgroup_native_probe",
                                "linux_cgroup_native_adapter_live_controls",
                                "--",
                                "--ignored",
                                "--exact",
                            ]
                        gates.append(values["--gate-id"])
                    else:
                        working_directory = ROOT / step.get(
                            "working-directory", job.get("defaults", {}).get("run", {}).get("working-directory", ".")
                        )
                        assert _is_scoped_cargo_probe(command, working_directory), (
                            f"Unwrapped full suite in {workflow_path.name}:{job_id}: {line}"
                        )
    assert gates and len(gates) == len(set(gates))
    assert set(gates) == REQUIRED_SUITE_GATES


def test_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout() -> None:
    _assert_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout()


@pytest.mark.parametrize("gate_id", ["cross-platform-full-suite", "windows-platform-contracts"])
def test_ci_full_suite_rejects_noop_with_inert_pytest_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate_id: str
) -> None:
    original = ROOT / ".github" / "workflows" / "ci.yml"
    fixture = yaml.safe_load(original.read_text(encoding="utf-8"))
    job = fixture["jobs"]["cross-platform-full-suite"]
    steps = [
        step for step in job["steps"] if "scripts/suite_evidence.py" in step.get("run", "") and gate_id in step["run"]
    ]
    assert len(steps) == 1
    workflow = tmp_path / "ci.yml"
    workflow.write_text(yaml.safe_dump(fixture, width=10000), encoding="utf-8")
    monkeypatch.setattr(
        sys.modules[__name__], "WORKFLOWS", tuple(workflow if path == original else path for path in WORKFLOWS)
    )
    _assert_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout()
    prefix, separator, command = steps[0]["run"].partition(" -- ")
    assert separator
    steps[0]["run"] = prefix + separator + shlex.join(["python", "-c", "print('1 passed')", command])
    workflow.write_text(yaml.safe_dump(fixture, width=10000), encoding="utf-8")

    with pytest.raises(AssertionError, match="required suite command"):
        _assert_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout()


def _workflow_triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True, {}))


def test_ci_triggers_document_changes_but_classifies_unrelated_docs_without_runtime() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    triggers = _workflow_triggers(workflow)
    for event in ("push", "pull_request"):
        assert "paths" not in triggers[event]
        assert "paths-ignore" not in triggers[event]

    jobs = workflow["jobs"]
    change_scope = jobs["change-scope"]
    assert change_scope["outputs"]["runtime"] == "${{ steps.classify.outputs.runtime }}"
    assert change_scope["outputs"]["documentation"] == "${{ steps.classify.outputs.documentation }}"
    assert change_scope["outputs"]["authority"] == "${{ steps.classify.outputs.authority }}"
    assert any(step.get("id") == "classify" for step in change_scope["steps"])

    secret_scan = jobs["secret-scan"]
    assert "needs" not in secret_scan
    assert "if" not in secret_scan
    assert any("scripts/secret_scan.py" in step.get("run", "") for step in secret_scan["steps"])
    assert jobs["documentation-contract"]["if"] == (
        "${{ needs.change-scope.result == 'success' && needs.change-scope.outputs.documentation == 'true' }}"
    )
    assert jobs["evidence-authority"]["if"] == (
        "${{ needs.change-scope.result == 'success' && needs.change-scope.outputs.authority == 'true' }}"
    )
    assert any(
        "scripts/document_consistency_gate.py" in step.get("run", "")
        for step in jobs["documentation-contract"]["steps"]
    )
    assert any(
        "scripts/architecture_fitness.py" in step.get("run", "") for step in jobs["documentation-contract"]["steps"]
    )
    assert any(
        "scripts/evidence_consistency_gate.py --generate artifacts/evidence/current.json" in step.get("run", "")
        for step in jobs["evidence-authority"]["steps"]
    )
    assert "paths-ignore" not in triggers["push"] and "paths-ignore" not in triggers["pull_request"]

    for job_id, job in jobs.items():
        if job_id in {"change-scope", "secret-scan", "documentation-contract", "evidence-authority"}:
            continue
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "change-scope" in needs, job_id
        condition = job.get("if", "")
        assert "needs.change-scope.outputs.runtime == 'true'" in condition, job_id
        assert "needs.change-scope.result != 'success'" in condition, job_id


def _git_commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True, text=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _run_change_classifier(root: Path, event: str, head: str, *, base: str = "", before: str = "") -> dict[str, str]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    step = next(step for step in workflow["jobs"]["change-scope"]["steps"] if step.get("id") == "classify")
    git = shutil.which("git")
    git_bash = Path(git).resolve().parents[1] / "bin" / ("bash.exe" if os.name == "nt" else "bash") if git else None
    bash = str(git_bash) if git_bash and git_bash.is_file() else shutil.which("bash")
    assert bash is not None, "Git Bash/bash is required to execute the CI change classifier"
    output_path = root / "github-output.txt"
    output_path.write_text("", encoding="utf-8")
    environment = {
        **os.environ,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_SHA": head,
        "GITHUB_OUTPUT": output_path.name,
        "PR_BASE_SHA": base,
        "PUSH_BEFORE_SHA": before,
    }
    result = subprocess.run(
        [bash, "-e", "-c", step["run"]],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in output_path.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize(
    ("event", "changed_paths", "expected"),
    (
        (
            "pull_request",
            ("docs/architecture/evidence/current.json",),
            {"runtime": "false", "documentation": "true", "authority": "true"},
        ),
        (
            "pull_request",
            ("docs/guide.md",),
            {"runtime": "false", "documentation": "true", "authority": "false"},
        ),
        (
            "pull_request",
            ("docs/architecture/design-closure/design_closure_collect.py",),
            {"runtime": "true", "documentation": "true", "authority": "false"},
        ),
        (
            "pull_request",
            ("docs/adr/ADR-003.md",),
            {"runtime": "false", "documentation": "true", "authority": "false"},
        ),
        (
            "pull_request",
            (".local/private-evidence.json",),
            {"runtime": "false", "documentation": "false", "authority": "false"},
        ),
        (
            "push",
            ("src/runtime.py",),
            {"runtime": "true", "documentation": "false", "authority": "false"},
        ),
        (
            "pull_request",
            ("scripts/evidence_consistency_gate.py",),
            {"runtime": "true", "documentation": "false", "authority": "true"},
        ),
        (
            "pull_request",
            ("src/runtime.py", "docs/architecture/evidence/current.json"),
            {"runtime": "true", "documentation": "true", "authority": "true"},
        ),
        (
            "pull_request",
            ("schemas/resource-contract-v1.json",),
            {"runtime": "true", "documentation": "false", "authority": "true"},
        ),
        (
            "pull_request",
            ("quality/registry/current_inventory.json",),
            {"runtime": "true", "documentation": "false", "authority": "true"},
        ),
        (
            "pull_request",
            ("docs/architecture/document_inventory.json",),
            {"runtime": "false", "documentation": "true", "authority": "true"},
        ),
    ),
)
def test_change_classifier_routes_docs_and_runtime_changes(
    tmp_path: Path, event: str, changed_paths: tuple[str, ...], expected: dict[str, str]
) -> None:
    subprocess.run(["git", "init", "--initial-branch=main", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "CI Contract Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci-contract@example.invalid"], cwd=tmp_path, check=True)
    initial_path = tmp_path / "src" / "base.py"
    initial_path.parent.mkdir(parents=True)
    initial_path.write_text("BASE = True\n", encoding="utf-8")
    base = _git_commit(tmp_path, "base")
    for changed_path in changed_paths:
        changed_file = tmp_path / Path(changed_path)
        changed_file.parent.mkdir(parents=True, exist_ok=True)
        changed_file.write_text("CHANGED = True\n", encoding="utf-8")
    head = _git_commit(tmp_path, "change")

    if event == "push":
        actual = _run_change_classifier(tmp_path, event, head, before=base)
    else:
        actual = _run_change_classifier(tmp_path, event, head, base=base)
    assert actual == expected


def test_manual_ci_dispatch_runs_all_lanes(tmp_path: Path) -> None:
    assert _run_change_classifier(tmp_path, "workflow_dispatch", "manual") == {
        "runtime": "true",
        "documentation": "true",
        "authority": "true",
    }


@pytest.mark.parametrize(
    ("authority", "expected_schema"),
    (
        ("registry", "aegis-not-verified-registry-v1"),
        ("deployment policy", "aegis-deployment-policy-v1"),
    ),
)
def test_evidence_authority_schema_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority: str, expected_schema: str
) -> None:
    registry_path = tmp_path / "not_verified_registry.json"
    policy_path = tmp_path / "deployment_policy.json"
    registry = {"schema": "aegis-not-verified-registry-v1", "entries": [{"id": "NV-001", "status": "NOT VERIFIED"}]}
    policy = {
        "schema": "aegis-deployment-policy-v1",
        "blockers": [],
        "non_blocking_registry_ids": ["NV-001"],
    }
    if authority == "registry":
        registry["schema"] = "unknown-schema"
    else:
        policy["schema"] = "unknown-schema"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(evidence_consistency_gate, "NOT_VERIFIED_REGISTRY", registry_path)
    monkeypatch.setattr(evidence_consistency_gate, "DEPLOYMENT_POLICY", policy_path)
    monkeypatch.setattr(evidence_consistency_gate, "ROOT", tmp_path)

    errors = evidence_consistency_gate.validate_registry_parity({"not_verified_ids": ["NV-001"]})

    assert any(expected_schema in error for error in errors)


def test_malformed_evidence_authority_json_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry_path = tmp_path / "not_verified_registry.json"
    policy_path = tmp_path / "deployment_policy.json"
    registry_path.write_text('{"schema":', encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema": "aegis-deployment-policy-v1",
                "blockers": [],
                "non_blocking_registry_ids": ["NV-001"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(evidence_consistency_gate, "NOT_VERIFIED_REGISTRY", registry_path)
    monkeypatch.setattr(evidence_consistency_gate, "DEPLOYMENT_POLICY", policy_path)
    monkeypatch.setattr(evidence_consistency_gate, "ROOT", tmp_path)

    errors = evidence_consistency_gate.validate_registry_parity({"not_verified_ids": ["NV-001"]})

    assert len(errors) == 1 and errors[0].startswith("registry authority unreadable:")


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected_reason"),
    ((7, False, "NON_ZERO_EXIT"), (124, True, "TIMEOUT")),
)
def test_suite_wrapper_writes_failure_metadata_before_returning_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    timed_out: bool,
    expected_reason: str,
) -> None:
    output_path = tmp_path / "suite.json"
    child_output = "2 tests run: 1 passed, 1 failed, 0 skipped"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(suite_evidence, "toolchain", lambda _command: "test toolchain")
    monkeypatch.setattr(suite_evidence, "_git_worktree_fingerprint", lambda: ("a" * 64, "CLEAN"))
    monkeypatch.setattr(
        suite_evidence,
        "run_command",
        lambda _command, _timeout: (child_output, "bounded stderr", exit_code, timed_out),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite_evidence.py",
            "--name",
            "failure-fixture",
            "--output",
            str(output_path),
            "--owner-id",
            "ci-test",
            "--gate-id",
            "failure-fixture",
            "--platform-id",
            "ubuntu-latest",
            "--attempt-id",
            "run-1-attempt-1",
            "--timeout-seconds",
            "10",
            "--",
            "cargo",
            "test",
        ],
    )

    assert suite_evidence.main() == exit_code
    evidence = json.loads(output_path.read_text(encoding="utf-8"))
    assert evidence["commit"] == subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert evidence["owner_id"] == "ci-test"
    assert evidence["gate_id"] == "failure-fixture"
    assert evidence["attempt_id"] == "run-1-attempt-1"
    assert evidence["platform_id"] == "ubuntu-latest"
    assert evidence["exit_code"] == exit_code
    assert evidence["duration_seconds"] >= 0
    assert evidence["timed_out"] is timed_out
    assert evidence["status"] == "FAILED"
    assert evidence["failure_reason"] == expected_reason


@pytest.mark.parametrize(
    ("job_id", "step_name", "artifact_name", "artifact_path"),
    (
        (
            "desktop-build",
            "Upload Tauri host suite evidence",
            "tauri-host-suite-${{ matrix.os }}-${{ github.sha }}-${{ github.run_attempt }}",
            "artifacts/suite-status/tauri-host-contracts-${{ matrix.os }}.json",
        ),
        (
            "wheel-parity",
            "Upload WASM plugin suite evidence",
            "wasm-plugin-suite-${{ matrix.os }}-${{ github.sha }}-${{ github.run_attempt }}",
            "artifacts/suite-status/wasm-plugin-runtime-${{ matrix.os }}.json",
        ),
    ),
)
def test_added_rust_suites_upload_failure_evidence(
    ci_jobs: dict, job_id: str, step_name: str, artifact_name: str, artifact_path: str
) -> None:
    job = ci_jobs[job_id]
    upload = next(step for step in job["steps"] if step.get("name") == step_name)
    assert upload.get("if") == "always()"
    assert upload.get("uses", "").startswith("actions/upload-artifact@")
    assert upload["with"]["name"] == artifact_name
    assert upload["with"]["path"] == artifact_path


@pytest.mark.parametrize(
    "command",
    (
        "cargo test --workspace",
        "cargo +stable test --workspace",
        "cargo nextest run --workspace",
        "cargo test --manifest-path Cargo.toml",
        "cargo test -p aegis-nerve --lib -- --skip wasm_plugins::tests",
        "cargo test --workspace # --command-fragment",
        "cargo test --workspace # scripts/cross_platform_evidence_gate.py",
        "python -m pytest tests",
    ),
)
def test_full_suites_outside_the_evidence_wrapper_are_rejected(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = {
        "concurrency": {"group": "ci-test", "cancel-in-progress": True},
        "jobs": {
            "test": {
                "runs-on": "ubuntu-latest",
                "timeout-minutes": 1,
                "steps": [
                    {
                        "run": "python scripts/suite_evidence.py --name fixture --output artifacts/suites/fixture.json "
                        "--owner-id ci-test --gate-id fixture --attempt-id test --timeout-seconds 30 "
                        "-- cargo test --workspace"
                    },
                    {"run": "echo probe"},
                ],
            }
        },
    }
    workflow = tmp_path / "ci.yml"
    workflow.write_text(yaml.safe_dump(fixture, width=10000), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOWS", (workflow,))
    monkeypatch.setattr(sys.modules[__name__], "REQUIRED_SUITE_GATES", {"fixture"})
    _assert_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout()
    fixture["jobs"]["test"]["steps"][1]["run"] = command
    workflow.write_text(yaml.safe_dump(fixture, width=10000), encoding="utf-8")
    with pytest.raises(AssertionError, match="Unwrapped full suite"):
        _assert_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout()


def test_deep_workflow_publishes_only_sanitized_fuzz_status() -> None:
    content = (ROOT / ".github" / "workflows" / "deep.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(content)
    steps = workflow["jobs"]["fuzz-resource-contract"]["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    artifact_paths = upload["with"]["path"].splitlines()

    assert "            artifacts/rust-coverage.lcov\n" not in content
    assert 'cat "$stdout_log" "$stderr_log" | tee "$combined_log"' not in content
    assert "            fuzz/corpus\n" not in content
    assert "            fuzz/artifacts\n" not in content
    assert artifact_paths == ["artifacts/fuzz/*.status", "artifacts/fuzz/commit.txt"]


def test_deep_linux_enforcement_lane_requires_live_status_and_keeps_failed_report() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deep.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["platform-enforcement-matrix"]["steps"]
    probe = next(step for step in steps if step.get("name") == "Run native platform probe")
    bind = next(step for step in steps if step.get("name") == "Bind probe to commit and runner metadata")
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))

    assert probe["env"]["REQUIRE_LIVE"] == "${{ matrix.platform-id == 'ubuntu-latest' }}"
    assert "--require-live" in probe["run"]
    assert bind["if"] == "always()"
    assert upload["if"] == "always()"


def test_uploaded_workflow_artifacts_are_unique_per_run_attempt() -> None:
    missing_attempt: list[str] = []
    unscoped_downloads: list[str] = []
    for workflow_path in WORKFLOWS:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_id, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                values = step.get("with", {})
                if uses.startswith("actions/upload-artifact@"):
                    name = values.get("name", "")
                    if "${{ github.run_attempt }}" not in name:
                        missing_attempt.append(f"{workflow_path.name}:{job_id}:{step.get('name', 'upload')}")
                if uses.startswith("actions/download-artifact@"):
                    pattern = values.get("pattern", "")
                    if "${{ github.sha }}" in pattern and "${{ github.run_attempt }}" not in pattern:
                        unscoped_downloads.append(f"{workflow_path.name}:{job_id}:{step.get('name', 'download')}")
    assert not missing_attempt, "Artifacts can collide when a GitHub workflow job is rerun: " + "; ".join(
        missing_attempt
    )
    assert not unscoped_downloads, "Artifact collectors can mix results from prior attempts: " + "; ".join(
        unscoped_downloads
    )


def test_workflow_artifacts_exclude_private_suite_and_release_inputs() -> None:
    violations: list[str] = []
    for workflow_path in WORKFLOWS:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_id, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                if not step.get("uses", "").startswith("actions/upload-artifact@"):
                    continue
                paths = step.get("with", {}).get("path", "")
                for path in paths.splitlines() if isinstance(paths, str) else []:
                    normalized = path.strip().replace("\\", "/")
                    if normalized.startswith(("artifacts/suites/", "artifacts/evidence/", "artifacts/private/")):
                        violations.append(f"{workflow_path.name}:{job_id}:{normalized}")
                    if normalized.startswith("artifacts/release-input/") and normalized != (
                        "artifacts/release-input/wheels/*.whl"
                    ):
                        violations.append(f"{workflow_path.name}:{job_id}:{normalized}")
    assert not violations, "Raw reports or private build inputs are uploaded: " + "; ".join(violations)


def test_cross_platform_full_suite_is_a_closed_three_runner_gate() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    content = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "cross-platform-full-suite:" in content
    assert "cross-platform-evidence-gate:" in content
    evidence_gate = workflow["jobs"]["cross-platform-evidence-gate"]
    assert "!cancelled()" in evidence_gate["if"]
    assert "needs.change-scope.outputs.runtime == 'true'" in evidence_gate["if"]
    assert any(step.get("if") == "always()" for step in evidence_gate["steps"])
    assert '--platform-id "${{ matrix.platform-id }}"' in content
    for runner in ("ubuntu-latest", "windows-latest", "macos-14"):
        assert f"platform-id: {runner}" in content
        assert f"--expected-platform {runner}" in content
    cross_platform_job = content.split("  cross-platform-full-suite:", 1)[1].split(
        "  cross-platform-evidence-gate:", 1
    )[0]
    assert "continue-on-error" not in cross_platform_job
    assert "if-no-files-found: error" in cross_platform_job
    assert "Produce sanitized cross-platform lane attestation" in cross_platform_job
    assert "--attestation-output" in cross_platform_job

    full_suite = next(
        step
        for step in workflow["jobs"]["cross-platform-full-suite"]["steps"]
        if step.get("name") == "Full Python and cross-language suite"
    )
    full_suite_args = shlex.split(full_suite["run"])
    full_suite_output = full_suite_args[full_suite_args.index("--output") + 1]
    lane_attestation = next(
        step
        for step in workflow["jobs"]["cross-platform-full-suite"]["steps"]
        if step.get("name") == "Produce sanitized cross-platform lane attestation"
    )
    lane_args = shlex.split(lane_attestation["run"])
    lane_directory = lane_args[lane_args.index("--directory") + 1]
    assert full_suite_output.rsplit("/", 1)[0] == lane_directory

    windows_contract = next(
        step
        for step in workflow["jobs"]["cross-platform-full-suite"]["steps"]
        if step.get("name") == "Windows platform contracts"
    )
    windows_contract_args = shlex.split(windows_contract["run"])
    windows_contract_output = windows_contract_args[windows_contract_args.index("--output") + 1]
    windows_attestation = next(
        step
        for step in workflow["jobs"]["cross-platform-full-suite"]["steps"]
        if step.get("name") == "Validate Windows platform evidence"
    )
    windows_lane_args = shlex.split(windows_attestation["run"])
    windows_lane_directory = windows_lane_args[windows_lane_args.index("--directory") + 1]
    assert windows_contract_output.rsplit("/", 1)[0] == windows_lane_directory
    assert lane_directory != windows_lane_directory

    aggregate_gate = next(
        step for step in evidence_gate["steps"] if step.get("name") == "Validate complete three-platform evidence"
    )
    assert "--require-attestations" in aggregate_gate["run"]
    aggregate_args = shlex.split(aggregate_gate["run"])
    aggregate_input = aggregate_args[aggregate_args.index("--directory") + 1]
    aggregate_output = aggregate_args[aggregate_args.index("--output") + 1]
    assert aggregate_output == "artifacts/suite-status/cross-platform.json"
    assert not aggregate_output.startswith(aggregate_input.rstrip("/") + "/")
    aggregate_upload = next(
        step for step in evidence_gate["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert aggregate_upload["with"]["path"] == aggregate_output


@pytest.mark.parametrize(
    ("github_actions", "runner_environment", "expected_scope"),
    (
        (None, None, "LOCAL_CHECKOUT_ONLY"),
        ("true", None, "LOCAL_CHECKOUT_ONLY"),
        (None, "github-hosted", "LOCAL_CHECKOUT_ONLY"),
        ("false", "github-hosted", "LOCAL_CHECKOUT_ONLY"),
        ("true", "self-hosted", "LOCAL_CHECKOUT_ONLY"),
        ("true", "github-hosted", "GITHUB_HOSTED_RUNNERS"),
    ),
)
def test_cross_platform_claim_scope_requires_github_hosted_runner_context(
    github_actions: str | None,
    runner_environment: str | None,
    expected_scope: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    if github_actions is not None:
        monkeypatch.setenv("GITHUB_ACTIONS", github_actions)
    if runner_environment is not None:
        monkeypatch.setenv("RUNNER_ENVIRONMENT", runner_environment)

    report = cross_platform_evidence_gate.validate_directory(
        tmp_path,
        "a" * 40,
        ("ubuntu-latest",),
        "pytest",
    )

    assert report["claim_scope"] == expected_scope
    assert report["independent_verification"] == "NOT VERIFIED"


def _assert_memory_context_live_eval_secrets_are_step_scoped_and_main_guarded(workflow: dict) -> None:
    job = workflow["jobs"]["live-evaluation"]
    provider_secrets = {
        "OPENROUTER_API_KEY": "${{ secrets.OPENROUTER_API_KEY }}",
        "NVIDIA_NIM_API_KEY": "${{ secrets.NVIDIA_NIM_API_KEY }}",
    }

    assert not (set(workflow.get("env", {})) & set(provider_secrets)), "provider secrets must not be workflow-scoped"
    for job_id, candidate_job in workflow["jobs"].items():
        assert not (set(candidate_job.get("env", {})) & set(provider_secrets)), (
            job_id,
            "provider secrets must not be job-scoped",
        )
    matching_steps = [step for step in job["steps"] if step.get("name") == "Run opt-in live evaluation"]
    assert len(matching_steps) == 1
    evaluation_step = matching_steps[0]
    assert evaluation_step.get("if") == "github.ref == 'refs/heads/main'"
    step_env = evaluation_step.get("env", {})
    assert {name: step_env.get(name) for name in provider_secrets} == provider_secrets
    secret_bindings = [
        (job_id, step.get("name"), set(step.get("env", {})) & set(provider_secrets))
        for job_id, candidate_job in workflow["jobs"].items()
        for step in candidate_job.get("steps", [])
        if set(step.get("env", {})) & set(provider_secrets)
    ]
    assert secret_bindings == [("live-evaluation", "Run opt-in live evaluation", set(provider_secrets))], (
        "provider secrets must be present only on the guarded evaluation step"
    )
    evaluation_step_index = job["steps"].index(evaluation_step)
    expected_references = {
        (f"$.jobs.live-evaluation.steps[{evaluation_step_index}].env.{name}", f"secrets.{name}")
        for name in provider_secrets
    }
    github_expression = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
    references: list[tuple[str, str]] = []

    def collect_secret_references(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "secrets" and child == "inherit":
                    references.append((f"{path}.secrets", "inherit"))
                collect_secret_references(key, f"{path}.<key>")
                collect_secret_references(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect_secret_references(child, f"{path}[{index}]")
        elif isinstance(value, str):
            for match in github_expression.finditer(value):
                expression = match.group(1).strip()
                if re.search(r"\bsecrets\b", expression):
                    references.append((path, expression))

    collect_secret_references(workflow, "$")
    assert set(references) == expected_references and len(references) == len(expected_references), (
        "provider secret references are allowed only in the guarded evaluation step env",
        references,
    )


def test_memory_context_live_eval_secrets_are_step_scoped_and_main_guarded() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "memory-context-live-eval.yml").read_text(encoding="utf-8")
    )
    _assert_memory_context_live_eval_secrets_are_step_scoped_and_main_guarded(workflow)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("run", "echo ${{ secrets['OPENROUTER_API_KEY'] }}"),
        ("run", "echo ${{ format('{0}', secrets.OPENROUTER_API_KEY) }}"),
        ("run", "echo ${{ secrets.OPENROUTER_API_KEY || '' }}"),
        ("run", "echo ${{ secrets[inputs.key] }}"),
        ("with", {"token": "${{ secrets.NVIDIA_NIM_API_KEY }}"}),
    ),
)
def test_memory_context_live_eval_rejects_secret_references_outside_guarded_env(field: str, value: object) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "memory-context-live-eval.yml").read_text(encoding="utf-8")
    )
    workflow["jobs"]["live-evaluation"]["steps"].append({"name": "unexpected", field: value})
    with pytest.raises(AssertionError, match="secret references are allowed only"):
        _assert_memory_context_live_eval_secrets_are_step_scoped_and_main_guarded(workflow)


def test_memory_context_live_eval_rejects_inherited_secrets_outside_guarded_env() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "memory-context-live-eval.yml").read_text(encoding="utf-8")
    )
    workflow["jobs"]["live-evaluation"]["secrets"] = "inherit"
    with pytest.raises(AssertionError, match="secret references are allowed only"):
        _assert_memory_context_live_eval_secrets_are_step_scoped_and_main_guarded(workflow)


def test_deep_workflow_dispatch_runs_only_the_selected_wp12_native_gate() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deep.yml").read_text(encoding="utf-8"))
    triggers = _workflow_triggers(workflow)
    target = triggers["workflow_dispatch"]["inputs"]["target"]

    assert target["type"] == "choice"
    assert target["default"] == "wp12-windows-native-process"
    assert target["options"] == [
        "all",
        "wp12-windows-native-process",
        "wp12-linux-cgroup-enforcement",
    ]
    assert target["required"] is True

    full_job_condition = "${{ github.event_name != 'workflow_dispatch' || inputs.target == 'all' }}"
    completion_job_condition = "${{ always() && (github.event_name != 'workflow_dispatch' || inputs.target == 'all') }}"
    full_jobs = {
        "platform-enforcement-matrix",
        "platform-enforcement-evidence-completeness",
        "security-and-replay",
        "rust-resource-benchmark",
        "hardware-buffer-benchmark",
        "fuzz-resource-contract",
        "miri-and-sanitizers",
    }
    jobs = workflow["jobs"]
    assert set(jobs) == full_jobs | {
        "wp12-windows-native-process",
        "wp12-linux-cgroup-enforcement",
    }
    for job_id in full_jobs:
        expected = (
            completion_job_condition if job_id == "platform-enforcement-evidence-completeness" else full_job_condition
        )
        assert jobs[job_id]["if"] == expected

    focused = jobs["wp12-windows-native-process"]
    assert (
        focused["if"]
        == "${{ github.event_name == 'workflow_dispatch' && inputs.target == 'wp12-windows-native-process' }}"
    )
    assert focused["runs-on"] == "windows-latest"
    assert focused["timeout-minutes"] == 45
    test_steps = [step for step in focused["steps"] if step.get("name") == "Run scoped WP12 Windows lifecycle test"]
    assert len(test_steps) == 1
    assert "--gate-id wp12-windows-native-process" in test_steps[0]["run"]
    deadline_steps = [step for step in focused["steps"] if step.get("name") == "Run WP12 runtime deadline regressions"]
    assert len(deadline_steps) == 1
    assert "--gate-id wp12-runtime-deadline" in deadline_steps[0]["run"]

    linux = jobs["wp12-linux-cgroup-enforcement"]
    assert linux["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.target == 'wp12-linux-cgroup-enforcement' }}"
    )
    assert linux["runs-on"] == "ubuntu-latest"
    assert linux["timeout-minutes"] == 45
    assert linux["permissions"] == {"contents": "read"}
    assert "needs" not in linux

    checkout_steps = [step for step in linux["steps"] if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkout_steps) == 1
    assert checkout_steps[0].get("with", {}).get("persist-credentials") is False

    python_probe = next(step for step in linux["steps"] if step.get("name") == "Run strict Linux Python cgroup probe")
    assert python_probe["id"] == "linux-probe"
    assert python_probe["continue-on-error"] is True
    assert "scripts/linux_cgroup_live_probe.py --require-live" in python_probe["run"]
    assert "--cgroup-root /sys/fs/cgroup/user.slice" in python_probe["run"]

    rust_toolchain = next(step for step in linux["steps"] if step.get("name") == "Resolve pinned Rust toolchain")
    assert "if" not in rust_toolchain
    rust_probe = next(step for step in linux["steps"] if step.get("name") == "Run ignored native Linux cgroup probe")
    assert rust_probe["id"] == "native-linux-probe"
    assert rust_probe["continue-on-error"] is True
    assert "--test linux_cgroup_native_probe" in rust_probe["run"]
    assert "--output artifacts/suites/wp12-linux-cgroup-rust.json" in rust_probe["run"]
    assert "linux_cgroup_native_adapter_live_controls -- --ignored --exact" in rust_probe["run"]
    assert "--timeout-seconds 1800" in rust_probe["run"]
    assert rust_probe["env"]["AEGIS_LINUX_CGROUP_ROOT"] == "/sys/fs/cgroup/user.slice"

    binder = next(step for step in linux["steps"] if step.get("name") == "Bind Linux cgroup outcomes to this run")
    assert binder["if"] == "always()"
    assert "GITHUB_SHA" in binder["env"]
    assert "GITHUB_RUN_ID" in binder["env"]
    assert "steps.linux-probe.outcome" in binder["env"]["PYTHON_PROBE_OUTCOME"]
    assert "steps.native-linux-probe.outcome" in binder["env"]["RUST_PROBE_OUTCOME"]
    assert '"LIVE VERIFIED"' in binder["run"]
    assert '"independent_verification": "NOT VERIFIED"' in binder["run"]

    upload = next(step for step in linux["steps"] if step.get("name") == "Upload sanitized WP12 Linux evidence")
    assert upload["if"] == "always()"
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["path"] == "artifacts/wp12-linux-cgroup-verification.json"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == 3

    final_gate = next(step for step in linux["steps"] if step.get("name") == "Require live Linux cgroup evidence")
    assert final_gate["if"] == "always()"
    assert 'status") != "LIVE VERIFIED"' in final_gate["run"]
