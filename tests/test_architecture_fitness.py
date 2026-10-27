from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def adr_validator(monkeypatch: pytest.MonkeyPatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("architecture_fitness")
    return module.adr_has_required_sections


def _adr(
    heading: str,
    *,
    migration_body: str = "A bounded rollout preserves compatibility.",
    evidence: str = "A regression test and review record support this decision.",
) -> str:
    return f"""# ADR-003: Example (superseded)

Status: Superseded

## Historical {heading}

{migration_body}

## Evidence

{evidence}
"""


def test_adr_gate_accepts_populated_historical_evidence(adr_validator) -> None:
    assert adr_validator(_adr("migration, security, performance, operations, rollback"))


@pytest.mark.parametrize(
    "missing_field",
    ["migration", "security", "performance", "operations", "rollback"],
)
def test_adr_gate_rejects_missing_required_field(adr_validator, missing_field: str) -> None:
    fields = ["migration", "security", "performance", "operations", "rollback"]
    fields.remove(missing_field)

    assert not adr_validator(_adr(", ".join(fields)))


@pytest.mark.parametrize(
    ("migration_body", "evidence", "include_evidence_heading"),
    [
        ("", "A test supports this decision.", True),
        ("TBD", "A test supports this decision.", True),
        ("!!!", "A test supports this decision.", True),
        ("___", "A test supports this decision.", True),
        ("A rollout plan exists.", "TODO", True),
        ("A rollout plan exists.", "", True),
        ("A rollout plan exists.", "---", True),
        ("A rollout plan exists.", "___", True),
        ("A rollout plan exists.", "A test supports this decision.", False),
    ],
)
def test_adr_gate_rejects_empty_or_placeholder_sections(
    adr_validator,
    migration_body: str,
    evidence: str,
    include_evidence_heading: bool,
) -> None:
    document = _adr(
        "migration, security, performance, operations, rollback",
        migration_body=migration_body,
        evidence=evidence,
    )
    if not include_evidence_heading:
        document = document.replace("## Evidence\n\nA test supports this decision.\n", "")

    assert not adr_validator(document)


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [("<!--\n", "\n-->"), ("```markdown\n", "\n```"), ("~~~markdown\n", "\n~~~")],
)
def test_adr_gate_ignores_commented_and_fenced_headings(adr_validator, prefix: str, suffix: str) -> None:
    document = prefix + _adr("migration, security, performance, operations, rollback") + suffix

    assert not adr_validator(document)


def test_adr_gate_ignores_content_after_unclosed_html_comment(adr_validator) -> None:
    document = "<!--\n" + _adr("migration, security, performance, operations, rollback")

    assert not adr_validator(document)


def test_adr_gate_bounds_work_for_many_unclosed_comment_markers() -> None:
    repository = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "sys.path.insert(0, 'scripts')\n"
        "from architecture_fitness import adr_has_required_sections\n"
        "assert not adr_has_required_sections(sys.stdin.read())\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            input="<!--" * 16_384,
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("unclosed ADR comments exceeded the bounded validator runtime")

    assert result.returncode == 0, result.stdout + result.stderr


def test_adr_gate_accepts_individual_h2_headings(adr_validator) -> None:
    doc = """# ADR-042: Individual H2 Example

## Migration Considerations
A planned rollout will support smooth backward compatibility across versions.

## Security Considerations
Adversarial token inspection prevents injection and privilege escalation.

## Performance Considerations
Memory caching keeps latency under the 50ms p95 SLA requirement.

## Operations Considerations
Standard Prometheus metrics and health checks monitor runtime status.

## Rollback Considerations
Feature flag toggling reverts directly to previous stable implementation.

## Evidence
Independent unit and integration tests confirm all invariant conditions.
"""
    assert adr_validator(doc)


def test_adr_gate_accepts_individual_h3_headings(adr_validator) -> None:
    doc = """# ADR-043: Individual H3 Subsections

## Architectural Dimensions

### Migration
A planned rollout will support smooth backward compatibility across versions.

### Security
Adversarial token inspection prevents injection and privilege escalation.

### Performance
Memory caching keeps latency under the 50ms p95 SLA requirement.

### Operations
Standard Prometheus metrics and health checks monitor runtime status.

### Rollback
Feature flag toggling reverts directly to previous stable implementation.

## Evidence
Independent unit and integration tests confirm all invariant conditions.
"""
    assert adr_validator(doc)


def test_adr_gate_rejects_individual_heading_with_short_body(adr_validator) -> None:
    doc = """# ADR-044: Short Body Rejection

## Migration
Short text.

## Security
Adversarial token inspection prevents injection and privilege escalation.

## Performance
Memory caching keeps latency under the 50ms p95 SLA requirement.

## Operations
Standard Prometheus metrics and health checks monitor runtime status.

## Rollback
Feature flag toggling reverts directly to previous stable implementation.

## Evidence
Independent unit and integration tests confirm all invariant conditions.
"""
    assert not adr_validator(doc)
