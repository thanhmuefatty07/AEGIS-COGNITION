from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
PROJECT_PYTHON = re.compile(
    r"\bpython(?:3)?\s+(?:-[BIu]\s+)*[\"']?scripts/|\bpytest\s|"
    r"^\s*uv\s+run\b[^\r\n]*\s+python(?:3)?\s+(?:-[BIu]\s+)*(?:-[cm]\b|-(?=\s|$))|"
    r"^\s*python(?:3)?\s+(?:-[BIu]\s+)*(?:-[cm]\b|-(?=\s|$))",
    re.MULTILINE,
)
PINNED_PYTHON = re.compile(r"3\.(?:14|15)\.\d+(?:-rc\.\d+)?")


def test_python_bootstrap_scanner_detects_uv_run_inline_python() -> None:
    command = "uv run --locked --no-sync python -c 'print(1)'"

    assert PROJECT_PYTHON.search(command), "uv run inline Python must be checked for a prior pinned bootstrap"


def _selected_versions(selection: str, job: dict) -> list[str]:
    if not selection.startswith("${{"):
        return [selection]
    variable = re.fullmatch(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}", selection)
    assert variable is not None, f"unsupported Python selection: {selection}"
    key = variable.group(1)
    matrix = job["strategy"]["matrix"]
    if key in matrix:
        return matrix[key]
    return [entry[key] for entry in matrix["include"]]


@pytest.mark.parametrize("workflow_path", sorted((ROOT / ".github" / "workflows").glob("*.yml")), ids=lambda p: p.name)
def test_project_python_execution_has_supported_pinned_bootstrap(workflow_path: Path) -> None:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for job_id, job in workflow["jobs"].items():
        versions: list[str] = []
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/setup-python@"):
                assert step["uses"] == SETUP_PYTHON, f"{job_id}: Python setup action must be pinned"
                assert "if" not in step, f"{job_id}: required Python bootstrap must be unconditional"
                versions = _selected_versions(step["with"]["python-version"], job)
                assert versions and all(PINNED_PYTHON.fullmatch(version) for version in versions), (
                    f"{job_id}: Python versions must be exact supported pins: {versions}"
                )
            if PROJECT_PYTHON.search(step.get("run", "")) and not versions:
                failures.append(f"{job_id}: {step.get('name', 'project Python execution')}")
    assert not failures, "Project Python runs before supported pinned bootstrap: " + "; ".join(failures)
