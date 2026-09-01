import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "production_closure_workflow_report.json"
SCHEMA = "aegis-production-closure-workflow-v1"
EMPTY_STRING_ENV_ALLOWED = frozenset({"AEGIS_LIVE_PROVIDER_429_SOAK_BODY"})

CommandRunner = Callable[[list[str], str, int], dict[str, Any]]


def stable_payload_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_production_closure_workflow(
    root: str | Path = ROOT,
    readiness_report: dict[str, Any] | None = None,
    *,
    after_readiness_report: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    execute: bool = False,
    command_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from scripts.production_readiness import evaluate_production_readiness

    root_path = Path(root)
    before = readiness_report if readiness_report is not None else evaluate_production_readiness(root_path)
    environment = env if env is not None else dict(os.environ)
    packets = _closure_packets(before)
    steps = [_closure_step(root_path, packet, environment) for packet in packets]
    before_ids = _active_blocker_ids(before)
    after_ids = _active_blocker_ids(after_readiness_report) if after_readiness_report is not None else before_ids
    closed_ids = sorted(set(before_ids) - set(after_ids))
    results = command_results or []
    command_failure_count = sum(1 for result in results if result.get("returncode") != 0 or result.get("timed_out") is True)
    payload = {
        "schema": SCHEMA,
        "truth_claim": False,
        "verifier": "hash-bound-production-blocker-closure-workflow",
        "execute": bool(execute),
        "production_deployable": bool((after_readiness_report or before).get("production_deployable") is True),
        "readiness_hash": str(before.get("readiness_hash", "")),
        "after_readiness_hash": str((after_readiness_report or {}).get("readiness_hash", "")),
        "active_blocker_count": len(before_ids),
        "active_blocker_ids": before_ids,
        "after_active_blocker_count": len(after_ids),
        "after_active_blocker_ids": after_ids,
        "closed_blocker_count": len(closed_ids),
        "closed_blocker_ids": closed_ids,
        "step_count": len(steps),
        "ready_to_execute_count": sum(1 for step in steps if step["ready_to_execute"]),
        "blocked_preflight_count": sum(1 for step in steps if not step["ready_to_execute"]),
        "command_result_count": len(results),
        "command_failure_count": command_failure_count,
        "steps": steps,
        "command_results": results,
    }
    payload["overall_ok"] = command_failure_count == 0
    payload["workflow_hash"] = stable_payload_sha256(payload)
    return payload


def run_production_closure_workflow(
    root: str | Path = ROOT,
    *,
    readiness_report: dict[str, Any] | None = None,
    after_readiness_report: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    execute: bool = False,
    runner: CommandRunner | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    from scripts.production_readiness import evaluate_production_readiness

    root_path = Path(root)
    before = readiness_report if readiness_report is not None else evaluate_production_readiness(root_path)
    environment = env if env is not None else dict(os.environ)
    preflight = build_production_closure_workflow(root_path, before, env=environment, execute=execute)
    command_results: list[dict[str, Any]] = []
    if execute:
        command_runner = runner if runner is not None else default_command_runner
        for step in preflight["steps"]:
            if not step["ready_to_execute"]:
                continue
            for role, command in (
                ("producer", step["resolved_producer_command"]),
                ("verifier", step["resolved_verifier_command"]),
            ):
                result = command_runner(list(command), str(root_path), timeout_seconds)
                command_results.append(
                    {
                        "blocker_id": step["blocker_id"],
                        "role": role,
                        "command": list(command),
                        "returncode": int(result.get("returncode", 1)),
                        "timed_out": result.get("timed_out") is True,
                        "stdout_tail": str(result.get("stdout", ""))[-2000:],
                        "stderr_tail": str(result.get("stderr", ""))[-2000:],
                    }
                )
                if result.get("returncode") != 0 or result.get("timed_out") is True:
                    break
    after = after_readiness_report
    if after is None:
        after = evaluate_production_readiness(root_path) if execute else None
    return build_production_closure_workflow(
        root_path,
        before,
        after_readiness_report=after,
        env=environment,
        execute=execute,
        command_results=command_results,
    )


def write_production_closure_workflow_report(
    root: str | Path = ROOT,
    report_path: Path | None = None,
    *,
    execute: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    root_path = Path(root)
    report = run_production_closure_workflow(root_path, execute=execute, timeout_seconds=timeout_seconds)
    target = report_path if report_path is not None else root_path / "artifacts" / "production_closure_workflow_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def default_command_runner(command: list[str], cwd: str, timeout_seconds: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "command timed out",
            "timed_out": True,
        }
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": False,
    }


def _closure_packets(readiness_report: dict[str, Any]) -> list[dict[str, Any]]:
    packets = readiness_report.get("closure_packets", [])
    return [dict(packet) for packet in packets if isinstance(packet, dict)]


def _active_blocker_ids(readiness_report: dict[str, Any] | None) -> list[str]:
    if not isinstance(readiness_report, dict):
        return []
    ids = readiness_report.get("active_production_blocker_ids")
    if isinstance(ids, list):
        return [str(blocker_id) for blocker_id in ids if str(blocker_id)]
    blockers = readiness_report.get("active_production_blockers", [])
    if not isinstance(blockers, list):
        return []
    return [str(blocker.get("id", "")) for blocker in blockers if isinstance(blocker, dict) and blocker.get("id")]


def _closure_step(root: Path, packet: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    producer_command = _string_list(packet.get("producer_command"))
    verifier_command = _string_list(packet.get("verifier_command"))
    required_env = _string_list(packet.get("required_env"))
    required_artifacts = _string_list(packet.get("required_artifacts"))
    expected_artifacts = _string_list(packet.get("expected_artifacts"))
    missing_required_env = [
        name
        for name in required_env
        if name not in env or (name not in EMPTY_STRING_ENV_ALLOWED and not str(env.get(name, "")).strip())
    ]
    missing_required_artifacts = _missing_paths(root, required_artifacts)
    missing_expected_artifacts = _missing_paths(root, expected_artifacts)
    step = {
        "schema": "aegis-production-closure-step-v1",
        "truth_claim": False,
        "blocker_id": str(packet.get("blocker_id", "")),
        "evidence_artifact": str(packet.get("evidence_artifact", "")),
        "admission_gate": str(packet.get("admission_gate", "")),
        "producer_available": bool(producer_command),
        "verifier_available": bool(verifier_command),
        "producer_command": producer_command,
        "verifier_command": verifier_command,
        "resolved_producer_command": _resolve_command(producer_command),
        "resolved_verifier_command": _resolve_command(verifier_command),
        "required_env": required_env,
        "present_required_env": [name for name in required_env if name not in missing_required_env],
        "missing_required_env": missing_required_env,
        "required_artifacts": required_artifacts,
        "present_required_artifacts": [path for path in required_artifacts if path not in missing_required_artifacts],
        "missing_required_artifacts": missing_required_artifacts,
        "expected_artifacts": expected_artifacts,
        "present_expected_artifacts": [path for path in expected_artifacts if path not in missing_expected_artifacts],
        "missing_expected_artifacts": missing_expected_artifacts,
        "closure_condition": str(packet.get("closure_condition", "")),
        "operator_action": str(packet.get("operator_action", "")),
    }
    step["ready_to_execute"] = (
        step["producer_available"]
        and step["verifier_available"]
        and not missing_required_env
        and not missing_required_artifacts
    )
    step["step_hash"] = stable_payload_sha256(step)
    return step


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _missing_paths(root: Path, paths: list[str]) -> list[str]:
    return [path for path in paths if not (root / path).exists()]


def _resolve_command(command: list[str]) -> list[str]:
    if command and command[0] == "python":
        return [sys.executable, *command[1:]]
    return list(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or execute the AEGIS production blocker closure workflow.")
    parser.add_argument("--execute", action="store_true", help="Run ready producer/verifier command pairs.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = write_production_closure_workflow_report(
        ROOT,
        args.output,
        execute=args.execute,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
