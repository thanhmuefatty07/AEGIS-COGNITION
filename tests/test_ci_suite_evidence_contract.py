from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOWS = tuple(
    sorted(
        (
            path
            for pattern in ("*.yml", "*.yaml")
            for path in (ROOT / ".github" / "workflows").glob(pattern)
        ),
        key=lambda path: path.name,
    )
)


def test_every_ci_suite_gate_has_unique_owner_gate_attempt_and_timeout() -> None:
    workflow_text = {
        workflow.name: workflow.read_text(encoding="utf-8")
        for workflow in WORKFLOWS
    }
    for content in workflow_text.values():
        if "suite_evidence.py" in content:
            assert "concurrency:" in content
            assert "cancel-in-progress:" in content
        job_parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", content)
        for index in range(1, len(job_parts), 2):
            job_body = job_parts[index + 1]
            if "runs-on:" in job_body:
                timeout = re.search(r"(?m)^    timeout-minutes:\s+(\d+)\s*$", job_body)
                assert timeout and int(timeout.group(1)) > 0

    invocations = [
        line.strip()
        for content in workflow_text.values()
        for line in content.splitlines()
        if "suite_evidence.py" in line
    ]

    assert len(invocations) == 8
    owners = []
    gates = []
    for invocation in invocations:
        owner = re.search(r"--owner-id\s+(\S+)", invocation)
        gate = re.search(r"--gate-id\s+(\S+)", invocation)
        attempt = re.search(r"--attempt-id\s+(\S+)", invocation)
        timeout = re.search(r"--timeout-seconds\s+(\d+)", invocation)
        assert owner and owner.group(1)
        assert gate and gate.group(1)
        assert attempt and attempt.group(1)
        assert timeout and int(timeout.group(1)) > 0
        owners.append(owner.group(1))
        gates.append(gate.group(1))

    assert len(gates) == len(set(gates))

    unwrapped_suite_commands = [
        line.strip()
        for content in workflow_text.values()
        for line in content.splitlines()
        if re.search(r"\b(?:cargo (?:nextest )?test|pytest)\b", line)
        and "suite_evidence.py" not in line
        and "--command-fragment" not in line
    ]
    assert unwrapped_suite_commands == []


def test_cross_platform_full_suite_is_a_closed_three_runner_gate() -> None:
    content = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "cross-platform-full-suite:" in content
    assert "cross-platform-evidence-gate:" in content
    assert "if: ${{ always() }}" in content
    assert "--platform-id \"${{ matrix.platform-id }}\"" in content
    for runner in ("ubuntu-latest", "windows-latest", "macos-14"):
        assert f"platform-id: {runner}" in content
        assert f"--expected-platform {runner}" in content
    cross_platform_job = content.split("  cross-platform-full-suite:", 1)[1].split("  cross-platform-evidence-gate:", 1)[0]
    assert "continue-on-error" not in cross_platform_job
    assert "if-no-files-found: error" in cross_platform_job
