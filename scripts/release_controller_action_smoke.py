"""Run the packaged typed-controller action smoke in an isolated venv."""

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
    import os
    import sys

    os.environ["AEGIS_API_KEY"] = "test-key"
    from aegis_cognition.agent import Agent

    class Session:
        current_url = "https://allowed.example/start"

        def __init__(self):
            self.actions = []

        async def click(self, selector):
            self.actions.append(selector)
            return "clicked"

    class Capture:
        browser_action_result_hash = "capture-hash"

    class RuntimeAdapter:
        async def capture_action(self, *, action, **_):
            await action()
            return Capture()

    session = Session()
    calls = [0]

    def response(_task, **_):
        calls[0] += 1
        if calls[0] == 1:
            return {
                "schema": "aegis-lab-action-plan-v1",
                "hypotheses": [{
                    "hypothesis_id": "h-packaged",
                    "statement": "the bounded cell remains finite",
                    "prior_bps": 5000,
                    "falsifiers": ["non-finite observation"],
                }],
                "experiment_spec": {
                    "experiment_id": "e-packaged",
                    "hypothesis_id": "h-packaged",
                    "design": "deterministic",
                    "variables": ["input"],
                    "controls": ["baseline"],
                    "preregistered_seeds": [1, 2, 3, 4, 5],
                    "expected_observations": 1,
                },
                "actions": [
                    {
                        "kind": "search_program",
                        "program": {
                            "provider": "fixture-search",
                            "operations": [{"kind": "query", "text": "bounded"}],
                        },
                    },
                    {"kind": "browser_action", "action": {"kind": "click", "selector": "#packaged"}},
                    {"kind": "experiment_action", "experiment_id": "e-packaged"},
                    {
                        "kind": "simulation_action",
                        "experiment_id": "e-packaged",
                        "simulation_spec": {
                            "simulation_id": "sim-packaged",
                            "experiment_id": "e-packaged",
                            "model_hash": "a" * 64,
                            "seeds": [1],
                            "max_steps": 2,
                        },
                    },
                ],
            }
        return {"answer": "packaged action smoke"}

    class RateLimited(RuntimeError):
        status_code = 429

    class Primary:
        model = "primary"

        async def ainvoke(self, _task, **_):
            raise RateLimited("primary quota")

    class Fallback:
        model = "fallback"

        async def ainvoke(self, task, **kwargs):
            return response(task, **kwargs)

    async def provider(_query, **_):
        return [{"uri": "https://example.test/evidence", "content": "bounded evidence"}]

    def experiment_runner(_spec, **_):
        return [{"observation_id": "o-packaged-real", "seed": 1, "measurement": 0.25}]

    def simulation_runner(_spec, **_):
        return [{"observation_id": "o-packaged-sim", "seed": 1, "measurement": 0.25}]

    benchmark_validator_code = (
        "import hashlib,json,sys;"
        "payload=json.load(sys.stdin);"
        "input_hash=hashlib.blake2b(json.dumps(payload['values'],sort_keys=True,separators=(',',':'))"
        ".encode(),digest_size=32).hexdigest();"
        "print(json.dumps({'schema':'aegis-hidden-validator-result-v1','valid':len(payload['values'])==30,"
        "'input_hash':input_hash,'validator_version':'packaged-test-v1'}))"
    )

    result = Agent(
        "packaged controller action smoke",
        llm=Primary(),
        lab=True,
        lab_iterations=1,
        search_query_provider=provider,
        browser=True,
        browser_session=session,
        browser_policy={"allowed_hosts": ["allowed.example"]},
        browser_runtime_adapter=RuntimeAdapter(),
        experiment_runner=experiment_runner,
        simulation_runner=simulation_runner,
        provider="primary",
        fallback_providers=(("fallback", Fallback()),),
        lab_require_native_authority=True,
        benchmark_protocol={
            "name": "packaged-benchmark",
            "metric": "accuracy",
            "preregistered_seeds": (1, 2, 3, 4, 5),
            "frozen_split_hash": "packaged-split-v1",
            "contamination_checks": ("dedupe",),
        },
        benchmark_trials=[0.9] * 30,
        benchmark_baseline=0.5,
        benchmark_environment_hash="packaged-env-v1",
        benchmark_validator_command=(sys.executable, "-c", benchmark_validator_code),
    ).run()
    manifest = result.lab_manifest or {}
    provider_attempts = [
        event for event in result.lab_events
        if event["kind"] == "tool_execution_recorded"
        and str(event["payload"].get("tool_name", "")).startswith("provider.")
    ]
    memory_index_effects = [
        event for event in result.lab_events
        if event["kind"] == "tool_execution_recorded"
        and event["payload"].get("tool_name") == "memory.index_session"
    ]
    context_retrieval_effects = [
        event for event in result.lab_events
        if event["kind"] == "tool_execution_recorded"
        and event["payload"].get("tool_name") == "memory.search_past"
    ]
    benchmark_validator_events = [
        event for event in result.lab_events
        if event["kind"] == "tool_execution_recorded"
        and event["payload"].get("tool_name") == "benchmark.hidden_validator"
    ]
    print(json.dumps({
        "authority": manifest.get("event_chain_authority"),
        "blockers": list(manifest.get("blockers", ())),
        "browser_actions": session.actions,
        "experiment_count": manifest.get("experiment_count"),
        "hypothesis_count": manifest.get("hypothesis_count"),
        "observation_count": manifest.get("observation_count"),
        "replay_archive": manifest.get("replay_archive") is not None,
        "source_count": manifest.get("source_count"),
        "state": manifest.get("state"),
        "controller_calls": calls[0],
        "provider_attempt_count": len(provider_attempts),
        "provider_attempt_statuses": [event["payload"].get("status") for event in provider_attempts],
        "memory_index_effect_count": len(memory_index_effects),
        "memory_index_effect_statuses": [event["payload"].get("status") for event in memory_index_effects],
        "context_retrieval_effect_count": len(context_retrieval_effects),
        "context_retrieval_effect_statuses": [
            event["payload"].get("status") for event in context_retrieval_effects
        ],
        "benchmark_status": manifest.get("benchmark_status"),
        "benchmark_validator_event_count": len(benchmark_validator_events),
        "benchmark_validator_event_statuses": [
            event["payload"].get("status") for event in benchmark_validator_events
        ],
    }, sort_keys=True))
    ''',
)


def run_smoke(wheel: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "aegis-release-controller-action-smoke-v1",
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "wheel": str(wheel),
        "wheel_sha256": sha256(wheel),
        "platform": platform.platform(),
        "status": "NOT VERIFIED",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-controller-action-smoke-") as directory:
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
                raise ValueError("controller action smoke output must be a mapping")
            typed_scenario = cast(dict[str, Any], scenario)
            report["scenario"] = scenario
            report["python"] = subprocess.check_output(
                [str(python), "--version"], text=True
            ).strip()
            report["status"] = (
                "PROVEN"
                if typed_scenario.get("authority") == "rust_native_verified"
                and typed_scenario.get("state") == "completed"
                and typed_scenario.get("blockers") == []
                and typed_scenario.get("source_count") == 1
                and typed_scenario.get("observation_count") == 2
                and typed_scenario.get("replay_archive") is True
                and typed_scenario.get("browser_actions") == ["#packaged"]
                and typed_scenario.get("controller_calls") == 2
                and typed_scenario.get("provider_attempt_count") == 4
                and typed_scenario.get("provider_attempt_statuses")
                == ["REJECTED", "SUCCESS", "REJECTED", "SUCCESS"]
                and typed_scenario.get("memory_index_effect_count") == 1
                and typed_scenario.get("memory_index_effect_statuses") == ["SUCCESS"]
                and typed_scenario.get("context_retrieval_effect_count") == 1
                and typed_scenario.get("context_retrieval_effect_statuses") == ["SUCCESS"]
                and typed_scenario.get("benchmark_status") == "PASS"
                and typed_scenario.get("benchmark_validator_event_count") == 1
                and typed_scenario.get("benchmark_validator_event_statuses") == ["SUCCESS"]
                else "REJECTED"
            )
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
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PROVEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
