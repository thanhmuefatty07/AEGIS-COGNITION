"""Run a packaged native crash-prefix recovery smoke across all Lab lanes."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import textwrap
import venv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SCENARIO = textwrap.dedent(
    r'''
    import json

    from aegis_cognition.lab import (
        BrowserCellPolicy,
        ClaimRecord,
        ExperimentSpec,
        HypothesisRecord,
        LabRun,
        SkillRegistry,
        SourceRecord,
    )

    run = LabRun(
        "packaged mixed-lane recovery",
        max_steps=8,
        token_budget=1000,
        require_native_authority=True,
    )
    run.add_source(SourceRecord("s1", "https://example.test", "content", "snapshot", 1))
    run.add_claim(ClaimRecord("c1", "bounded claim", ("s1",), 5000, "supported"))
    run.add_hypothesis(HypothesisRecord("h1", "claim holds", 5000, ("failure",), ("c1",)))
    run.add_experiment(
        ExperimentSpec(
            "e1", "h1", "deterministic", ("input",), ("baseline",), (1, 2, 3, 4, 5), 1
        )
    )
    experiment_id, _ = run.admit_experiment_execution(
        experiment_id="e1",
        attempt=1,
        input_payload={"seed": 1},
        policy_payload={"cell": "compute"},
        execution_id="experiment-open",
    )
    run.admit_research_program(
        program_hash="a" * 64,
        operation_count=2,
        provider="provider.test",
    )
    policy = BrowserCellPolicy(allowed_hosts=("example.test",))
    action_id = "browser-action-open"
    run.admit_browser_action(
        action_kind="click",
        action={"kind": "click", "selector": "#submit"},
        policy=policy,
        lease_id=1,
        action_id=action_id,
    )
    observation_id, _ = run.admit_browser_observation(
        observation_kind="read_url",
        action={"kind": "read_url"},
        policy=policy,
        lease_id=1,
        observation_count=1,
    )
    manifest = __import__("aegis_cognition.lab", fromlist=["SkillManifest"]).SkillManifest(
        skill_id="recovery.skill",
        version="1.0.0",
        capabilities=("compute",),
        preconditions=("ready",),
        validator_version="validator-v1",
        implementation_hash="b" * 64,
        policy_hash="c" * 64,
    )
    registry = SkillRegistry()
    registry.register(manifest, lambda result: bool(result))
    skill_admission = run.admit_skill(
        registry,
        manifest.skill_id,
        manifest.version,
        available_capabilities=("compute",),
        preconditions={"ready": True},
    )

    restored = LabRun.from_payload(run.to_payload())
    recovered = restored.reconcile_unsettled_executions(
        operator_id="packaged-operator",
        reason="worker_crash_after_admission",
    )
    settlement_statuses = [
        event.payload.get("status")
        for event in restored.events
        if event.kind in {
            "experiment_execution_recorded",
            "research_program_executed",
            "browser_action_recorded",
            "browser_observation_recorded",
            "skill_execution_recorded",
            "tool_execution_recorded",
        }
        and event.payload.get("status") == "REJECTED"
    ]
    expected = {
        ("experiment_execution_admitted", experiment_id),
        ("research_program_admitted", "a" * 64),
        ("browser_action_admitted", action_id),
        ("browser_observation_admitted", observation_id),
        ("skill_admission_recorded", skill_admission.admission_hash),
    }
    print(json.dumps({
        "authority": restored.dossier().manifest.get("event_chain_authority"),
        "recovered": sorted([list(item) for item in recovered]),
        "expected_count": len(expected),
        "open_after_recovery": len(restored.unsettled_execution_admissions()),
        "state": restored.state,
        "rejected_settlement_count": len(settlement_statuses),
        "event_chain_valid": restored.verify_event_chain(),
    }, sort_keys=True))
    if (
        restored.dossier().manifest.get("event_chain_authority") != "rust_native_verified"
        or set(recovered) != expected
        or restored.unsettled_execution_admissions()
        or restored.state != "blocked"
        or len(settlement_statuses) != len(expected)
        or not restored.verify_event_chain()
    ):
        raise SystemExit(2)
    ''',
)


def run_smoke(wheel: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "aegis-release-recovery-smoke-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "wheel": str(wheel),
        "wheel_sha256": sha256(wheel),
        "platform": platform.platform(),
        "status": "NOT VERIFIED",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-recovery-smoke-") as directory:
            environment = Path(directory) / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(environment)
            python = environment / ("Scripts" if sys.platform == "win32" else "bin") / "python"
            subprocess.run(
                [str(python), "-m", "pip", "install", "--force-reinstall", str(wheel)],
                check=True,
                capture_output=True,
                text=True,
                cwd=directory,
            )
            completed = subprocess.run(
                [str(python), "-c", _SCENARIO],
                check=True,
                capture_output=True,
                text=True,
                cwd=directory,
            )
            scenario = json.loads(completed.stdout.strip())
            if not isinstance(scenario, dict):
                raise ValueError("recovery smoke output must be a mapping")
            typed_scenario = cast(dict[str, Any], scenario)
            report["scenario"] = scenario
            report["python"] = subprocess.check_output([str(python), "--version"], text=True).strip()
            report["status"] = "PROVEN" if typed_scenario.get("event_chain_valid") is True else "REJECTED"
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        report["reason"] = str(error)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_smoke(args.wheel.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["status"] == "PROVEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
