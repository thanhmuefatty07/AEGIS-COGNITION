from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _collect_node_ids(arguments: list[str], temporary_root: Path) -> set[str]:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("AEGIS_TEST_PROVIDER_NO_KEY_SMOKE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *(argument for argument in arguments if argument not in {"-v", "-q"}),
            "--collect-only",
            "-q",
            "--basetemp",
            str(temporary_root / "fixtures"),
            "-o",
            f"cache_dir={temporary_root / 'cache'}",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return {
        line.strip() for line in result.stdout.splitlines() if line.partition("::")[0].endswith(".py") and "::" in line
    }


def test_live_provider_smoke_is_explicit_and_portable_suite_has_no_live_cases(tmp_path: Path) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "provider-public-catalog-smoke.yml").read_text(encoding="utf-8")
    )
    smoke_step = next(
        step for step in workflow["jobs"]["provider-no-key"]["steps"] if "suite_evidence.py" in step.get("run", "")
    )
    smoke_command = shlex.split(smoke_step["run"])
    native_command = smoke_command[smoke_command.index("--") + 1 :]
    smoke_arguments = native_command[native_command.index("pytest") + 1 :]
    smoke_nodes = _collect_node_ids(smoke_arguments, tmp_path / "smoke")
    portable_nodes = _collect_node_ids(["tests", "core/python/tests.py"], tmp_path / "portable")

    assert len(smoke_nodes) == 9
    assert {node.rpartition("[")[2].removesuffix("]") for node in smoke_nodes} == {
        "anthropic",
        "openrouter",
        "google-gemini",
        "groq",
        "xai",
        "perplexity",
        "nvidia-nim",
        "deepseek",
        "openai",
    }
    assert portable_nodes
    assert smoke_nodes.isdisjoint(portable_nodes), "live provider checks belong to the explicit smoke tier"
