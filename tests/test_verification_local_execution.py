from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from aegis_cognition.lab import ProcessExecutionCell
from aegis_cognition.verification import (
    LocalVerificationCommand,
    VerificationFacade,
    VerificationSessionError,
    run_local_verification_command,
)
from aegis_cognition.verification.discovery import inspect_project
from aegis_cognition.verification import execution as execution_module


def _session(tmp_path: Path) -> tuple[VerificationFacade, str, str]:
    facade = VerificationFacade()
    profile = inspect_project(tmp_path)
    requirements = facade.create_contract(
        "implement the bounded verification lane",
        expected_behavior="the lane records a structured receipt",
        source_revision=profile.source_revision,
        policy_hash="test-policy",
    )
    session = facade.start_session(profile, requirements)
    run = facade.request_deep_run(session.session_id)
    return facade, session.session_id, str(run["run_id"])


def _result(source_revision: str, run_id: str) -> dict[str, object]:
    return {
        "source_revision": source_revision,
        "command_id": "test-command",
        "adapter": "custom",
        "framework": "fixture",
        "toolchain": "test",
        "platform": "test",
        "executable": sys.executable,
        "argv": ("-c", "print('1 passed')"),
        "working_directory": str(Path.cwd()),
        "environment_fingerprint": {},
        "timeout_seconds": 5.0,
        "cancellation_requested": False,
        "exit_code": 0,
        "exit_semantics": "PROCESS_EXIT",
        "status": "PASS",
        "discovered": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "filtered": 0,
        "ignored": 0,
        "stdout_hash": "a" * 64,
        "stderr_hash": "b" * 64,
        "stdout_size": 12,
        "stderr_size": 0,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
    }


def test_local_command_rejects_missing_executable_and_shell_payload(tmp_path: Path) -> None:
    command = LocalVerificationCommand(
        command_id="fixture",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", "import sys; print('1 passed'); print(sys.argv[1])", "&& whoami"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    command.validate()
    result = run_local_verification_command(command.as_dict())
    assert result["status"] == "PASS"
    assert result["passed"] == 1
    assert result["detail"] == "&& whoami"

    invalid = LocalVerificationCommand(
        command_id="invalid",
        adapter="custom",
        framework="fixture",
        executable=str(tmp_path / "missing-executable"),
        argv=("-c", "print('1 passed')"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    with pytest.raises(ValueError, match="executable"):
        invalid.validate()


def test_rustup_tool_resolution_binds_real_toolchain_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rustup = tmp_path / "rustup.exe"
    shim = tmp_path / "cargo.exe"
    real_cargo = tmp_path / "toolchain" / "cargo.exe"
    real_rustc = tmp_path / "toolchain" / "rustc.exe"
    rustup.write_bytes(b"rustup")
    shim.write_bytes(b"shim")
    real_cargo.parent.mkdir()
    real_cargo.write_bytes(b"cargo")
    real_rustc.write_bytes(b"rustc")

    discovered = {"cargo": str(shim), "rustc": str(shim), "rustup": str(rustup)}
    monkeypatch.setattr(execution_module.shutil, "which", discovered.get)

    calls: list[tuple[object, dict[str, object]]] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        command = args[0]
        tool = command[2]
        return type("Completed", (), {"stdout": str(real_cargo if tool == "cargo" else real_rustc)})()

    monkeypatch.setattr(execution_module.subprocess, "run", fake_run)

    assert execution_module._resolve_cargo_executable(cwd=tmp_path) == str(real_cargo)
    assert execution_module._resolve_rustup_tool("rustc", cwd=tmp_path) == str(real_rustc)
    assert calls[0][0][0] == (str(rustup), "which", "cargo")
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["shell"] is False


def test_process_cell_executes_local_verification_without_second_lab_path(tmp_path: Path) -> None:
    command = LocalVerificationCommand(
        command_id="process-cell",
        adapter="custom",
        framework="fixture",
        executable=sys.executable,
        argv=("-c", "print('1 passed')"),
        working_directory=str(tmp_path),
        timeout_seconds=5.0,
        source_revision="fixture-revision",
    )
    result = asyncio.run(
        ProcessExecutionCell(run_local_verification_command, timeout_seconds=10.0)(command.as_dict())
    )
    assert result["status"] == "PASS"
    assert result["resource_class"] == "LOCAL_PROCESS_CELL_BOUNDED_WALL_TIME"


def test_lab_bound_receipt_is_provisional_even_when_command_passes(tmp_path: Path) -> None:
    facade, session_id, run_id = _session(tmp_path)
    profile = facade.inspect_project(tmp_path)
    receipt = facade.record_execution_result(session_id, run_id, _result(profile.source_revision, run_id))
    assert receipt.status == "PASS"
    report = facade.read_report(session_id)
    assert report["execution"] == "LAB_BOUND"
    assert report["final_assurance"] is False
    assert report["assessment"]["failure_reasons"] == ["evidence_promotion_disabled_shadow_mode"]
    assert len(report["receipts"]) == 1
    assert len(report["artifacts"]) == 2


def test_stale_receipt_is_rejected_and_cannot_become_pass(tmp_path: Path) -> None:
    facade, session_id, run_id = _session(tmp_path)
    with pytest.raises(VerificationSessionError, match="stale"):
        facade.record_execution_result(session_id, run_id, _result("different-revision", run_id))
    report = facade.read_report(session_id)
    assert report["execution"] == "NOT_EXECUTED"
    assert report["final_assurance"] is False
