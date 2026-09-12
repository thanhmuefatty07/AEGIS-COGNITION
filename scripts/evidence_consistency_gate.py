"""Fail closed when current evidence is not bound to the checked-out commit.

The JSON manifest is the machine-readable source of truth.  Markdown reports
may summarize it, but they cannot silently replace its commit, scope, or
status.  This gate is intentionally small so it can run before every CI and
release evidence lane.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "architecture" / "evidence" / "current.json"
NOT_VERIFIED_REGISTRY = ROOT / "docs" / "architecture" / "not_verified_registry.json"
DEPLOYMENT_POLICY = ROOT / "docs" / "architecture" / "deployment_policy.json"
REGISTRY_MARKDOWN_RELATIVE = Path("docs") / "architecture" / "NOT_VERIFIED_REGISTRY.md"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GT96_ID_RE = re.compile(r"\|\s*(GT96-\d{3})\s*\|")
NV_ID_RE = re.compile(r"^\|\s*(NV-\d{3})\s*\|", re.MULTILINE)
CURRENT_STATUSES = {"PROVEN", "MEASURED", "SOURCE-BACKED"}
REMEDIATION_STATUSES = CURRENT_STATUSES | {
    "IMPLEMENTED / NOT VERIFIED",
    "IMPLEMENTED / PARTIALLY LIVE VERIFIED",
    "MEASURED LOCAL ONLY / NOT VERIFIED FOR H0-H2",
    "IMPLEMENTED / MEASURED LOCAL ONLY",
    "PROVEN LOCAL PACKAGE DRILL / RESTORE NOT VERIFIED",
    "NOT VERIFIED",
    "ASSUMED",
    "NOT IMPLEMENTED",
}


def git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.STDOUT).strip()


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("evidence manifest must be a JSON object")
    return value


def registry_ids() -> set[str]:
    """Load the canonical blocker IDs used by generated evidence artifacts."""

    registry = load_manifest(NOT_VERIFIED_REGISTRY)
    raw_entries = registry.get("entries")
    if not isinstance(raw_entries, list):
        return set()
    return {
        str(entry["id"])
        for entry in raw_entries
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and entry["id"].strip()
    }


def registry_markdown_ids(path: Path | None = None) -> tuple[str, ...]:
    """Read the human view's IDs without treating its prose as authority."""

    target = path or (ROOT / REGISTRY_MARKDOWN_RELATIVE)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(NV_ID_RE.findall(text))


def registry_markdown_statuses(path: Path | None = None) -> dict[str, str]:
    """Read blocker statuses from the human view for drift detection only."""

    target = path or (ROOT / REGISTRY_MARKDOWN_RELATIVE)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    statuses: dict[str, str] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("| NV-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and NV_ID_RE.fullmatch(f"| {cells[0]} |"):
            statuses[cells[0]] = cells[-1]
    return statuses


def gt96_markdown_statuses(path: Path | None = None) -> dict[str, str]:
    """Read GT96 status cells for machine-to-human drift detection."""

    target = path or (ROOT / "docs" / "architecture" / "GT96_TRACEABILITY.md")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    statuses: dict[str, str] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("| GT96-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and GT96_ID_RE.fullmatch(f"| {cells[0]} |"):
            statuses[cells[0]] = cells[-1]
    return statuses


def validate_registry_parity(manifest: dict[str, Any]) -> list[str]:
    """Reject dropped or unknown blocker IDs across machine authorities.

    The registry remains the canonical inventory.  The evidence manifest is
    required to materialize every registry ID, while deployment policy may
    reference the production-blocking subset only.  This deliberately does
    not rewrite any file: stale tracked evidence must fail closed until a
    generated artifact is produced for the checked-out SHA.
    """

    if "not_verified_ids" not in manifest:
        # Small unit-test fixtures and legacy manifests predate the registry
        # field; the full checkout manifest is still required to carry it.
        return []
    errors: list[str] = []
    try:
        registry = load_manifest(NOT_VERIFIED_REGISTRY)
        policy = load_manifest(DEPLOYMENT_POLICY)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"registry authority unreadable: {error}"]
    raw_entries = registry.get("entries")
    registry_ids: set[str] = set()
    registry_statuses: dict[str, str] = {}
    if not isinstance(raw_entries, list) or not raw_entries:
        errors.append("not-verified registry entries must be a non-empty array")
    else:
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
                errors.append(f"not-verified registry entry[{index}] lacks a valid id")
                continue
            registry_id = str(entry["id"])
            if registry_id in registry_ids:
                errors.append(f"duplicate not-verified registry id: {registry_id}")
            registry_ids.add(registry_id)
            if isinstance(entry.get("status"), str) and entry["status"].strip():
                registry_statuses[registry_id] = str(entry["status"]).strip()
    manifest_ids = {
        item for item in manifest.get("not_verified_ids", ()) if isinstance(item, str)
    }
    if manifest_ids != registry_ids:
        errors.append(
            "evidence manifest not_verified_ids do not match registry: "
            f"missing={sorted(registry_ids - manifest_ids)}, "
            f"unknown={sorted(manifest_ids - registry_ids)}"
        )
    raw_blockers = policy.get("blockers")
    policy_ids: set[str] = set()
    if raw_blockers is not None and not isinstance(raw_blockers, list):
        errors.append("deployment policy blockers must be an array")
    elif isinstance(raw_blockers, list):
        for index, blocker in enumerate(raw_blockers):
            if not isinstance(blocker, dict):
                errors.append(f"deployment policy blocker[{index}] must be an object")
                continue
            raw_policy_ids = blocker.get("registry_ids", ())
            if not isinstance(raw_policy_ids, (list, tuple)):
                errors.append(f"deployment policy blocker[{index}] registry_ids must be an array")
                continue
            for registry_id in raw_policy_ids:
                if isinstance(registry_id, str) and registry_id.strip():
                    policy_ids.add(registry_id)
    if not policy_ids.issubset(registry_ids):
        errors.append(
            "deployment policy references unknown registry IDs: "
            f"{sorted(policy_ids - registry_ids)}"
        )
    if not policy_ids.issubset(manifest_ids):
        errors.append(
            "deployment policy registry IDs are missing from evidence manifest: "
            f"{sorted(policy_ids - manifest_ids)}"
        )

    # The Markdown view is intentionally non-authoritative, but it must still
    # enumerate the same inventory so a human review cannot silently omit a
    # blocker.  Small test fixtures may omit the companion document; the full
    # checkout is checked whenever the file exists.
    markdown_path = ROOT / REGISTRY_MARKDOWN_RELATIVE
    if markdown_path.is_file():
        markdown_ids = registry_markdown_ids(markdown_path)
        if len(markdown_ids) != len(set(markdown_ids)):
            errors.append("NOT_VERIFIED_REGISTRY.md contains duplicate blocker IDs")
        if set(markdown_ids) != registry_ids:
            errors.append(
                "NOT_VERIFIED_REGISTRY.md IDs do not match registry: "
                f"missing={sorted(registry_ids - set(markdown_ids))}, "
                f"unknown={sorted(set(markdown_ids) - registry_ids)}"
            )
        markdown_statuses = registry_markdown_statuses(markdown_path)
        if len(registry_statuses) == len(registry_ids) and len(markdown_statuses) == len(registry_ids):
            status_mismatches = sorted(
                registry_id
                for registry_id in registry_ids
                if registry_statuses.get(registry_id) != markdown_statuses.get(registry_id)
            )
            if status_mismatches:
                errors.append(
                    "NOT_VERIFIED_REGISTRY.md statuses do not match registry: "
                    f"{status_mismatches}"
                )

        # GT96 has its own requirement matrix. Compare its human status cells
        # to the tracked machine manifest, while leaving generated checkout
        # evidence free to derive release-scoped status separately.
        tracked_manifest_path = ROOT / "docs" / "architecture" / "evidence" / "current.json"
        if tracked_manifest_path.is_file():
            try:
                tracked_manifest = load_manifest(tracked_manifest_path)
            except (OSError, ValueError, json.JSONDecodeError):
                tracked_manifest = {}
            tracked_requirements = tracked_manifest.get("requirements", ())
            tracked_statuses = {
                str(item["id"]): str(item["status"]).strip()
                for item in tracked_requirements
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].startswith("GT96-")
                and isinstance(item.get("status"), str)
                and item["status"].strip()
            }
            gt96_statuses = gt96_markdown_statuses(
                ROOT / "docs" / "architecture" / "GT96_TRACEABILITY.md"
            )
            if tracked_statuses and len(gt96_statuses) == len(tracked_statuses):
                gt96_mismatches = sorted(
                    requirement_id
                    for requirement_id in tracked_statuses
                    if tracked_statuses.get(requirement_id) != gt96_statuses.get(requirement_id)
                )
                if gt96_mismatches:
                    errors.append(
                        "GT96_TRACEABILITY.md statuses do not match current evidence: "
                        f"{gt96_mismatches}"
                    )

    # A deployment policy is allowed to block only the release-critical subset,
    # but omission must be explicit.  When the policy declares the companion
    # list, require a disjoint, complete partition of the registry.
    raw_non_blocking = policy.get("non_blocking_registry_ids")
    if raw_non_blocking is not None:
        if not isinstance(raw_non_blocking, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_non_blocking
        ):
            errors.append("deployment policy non_blocking_registry_ids must be an array of IDs")
        else:
            non_blocking_ids = {str(item) for item in raw_non_blocking}
            if len(non_blocking_ids) != len(raw_non_blocking):
                errors.append("deployment policy non_blocking_registry_ids contains duplicates")
            if not non_blocking_ids.issubset(registry_ids):
                errors.append(
                    "deployment policy non_blocking_registry_ids references unknown IDs: "
                    f"{sorted(non_blocking_ids - registry_ids)}"
                )
            overlap = sorted(policy_ids & non_blocking_ids)
            if overlap:
                errors.append(
                    "deployment policy blocking and non-blocking IDs overlap: "
                    f"{overlap}"
                )
            covered = policy_ids | non_blocking_ids
            if covered != registry_ids:
                errors.append(
                    "deployment policy does not partition the registry: "
                    f"missing={sorted(registry_ids - covered)}, "
                    f"unknown={sorted(covered - registry_ids)}"
                )
    return errors


def load_suite_artifacts(root: Path, expected_head: str) -> dict[str, dict[str, Any]]:
    """Return valid suite evidence available in the current checkout."""
    suite_dir = root / "artifacts" / "suites"
    if not suite_dir.is_dir():
        return {}
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(suite_dir.glob("*.json")):
        try:
            candidate = load_manifest(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if candidate.get("schema") != "aegis-suite-evidence-v1":
            continue
        if candidate.get("commit") != expected_head:
            continue
        name = candidate.get("name")
        if isinstance(name, str) and name:
            if name in artifacts:
                artifacts[name] = {
                    "schema": "aegis-suite-evidence-v1",
                    "name": name,
                    "_load_conflict": True,
                }
            else:
                artifacts[name] = candidate
    return artifacts


def suite_artifact_release_eligible(candidate: dict[str, Any]) -> bool:
    """Accept suite evidence only when its result and provenance are complete."""
    return (
        candidate.get("schema") == "aegis-suite-evidence-v1"
        and candidate.get("status") == "PROVEN"
        and candidate.get("release_eligible") is True
        and type(candidate.get("exit_code")) is int
        and candidate["exit_code"] == 0
        and type(candidate.get("failed")) is int
        and candidate["failed"] == 0
        and type(candidate.get("timed_out")) is bool
        and candidate["timed_out"] is False
        and candidate.get("termination") == "EXITED"
        and type(candidate.get("timeout_seconds")) is int
        and candidate["timeout_seconds"] > 0
        and isinstance(candidate.get("owner_id"), str)
        and bool(candidate["owner_id"].strip())
        and isinstance(candidate.get("gate_id"), str)
        and bool(candidate["gate_id"].strip())
        and isinstance(candidate.get("attempt_id"), str)
        and bool(candidate["attempt_id"].strip())
        and isinstance(candidate.get("run_key"), str)
        and bool(SHA256_RE.fullmatch(candidate["run_key"]))
        and candidate.get("worktree_status") in {"CLEAN", "DIRTY"}
        and isinstance(candidate.get("worktree_sha256"), str)
        and bool(SHA256_RE.fullmatch(candidate["worktree_sha256"]))
        and isinstance(candidate.get("combined_output_sha256"), str)
        and bool(SHA256_RE.fullmatch(candidate["combined_output_sha256"]))
    )


def find_suite_artifact(suite_artifacts: dict[str, dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Match canonical CI names and local ``-final`` evidence names."""
    candidates = (name, f"{name}-final")
    for candidate in candidates:
        if candidate in suite_artifacts:
            return suite_artifacts[candidate]
    return None


def suite_artifact_ownership_conflicted(
    candidate: dict[str, Any], suite_artifacts: dict[str, dict[str, Any]]
) -> bool:
    """Reject competing eligible identities for one gate instead of guessing."""
    if not suite_artifact_release_eligible(candidate):
        return True
    gate_id = candidate["gate_id"]
    signatures = {
        (
            artifact["owner_id"],
            artifact["gate_id"],
            artifact["attempt_id"],
            artifact["run_key"],
            artifact.get("discovered"),
            artifact.get("passed"),
            artifact.get("failed"),
            artifact.get("ignored"),
            artifact.get("filtered"),
            artifact["exit_code"],
            artifact["timeout_seconds"],
            artifact["timed_out"],
            artifact["termination"],
            artifact["worktree_status"],
            artifact["worktree_sha256"],
            artifact.get("stdout_sha256"),
            artifact.get("stderr_sha256"),
            artifact["combined_output_sha256"],
        )
        for artifact in suite_artifacts.values()
        if suite_artifact_release_eligible(artifact) and artifact["gate_id"] == gate_id
    }
    return len(signatures) > 1


def materialize_for_head(template: dict[str, Any], expected_head: str) -> dict[str, Any]:
    """Create a non-self-referential evidence artifact for this checkout."""
    manifest = json.loads(json.dumps(template))
    manifest["commit"] = expected_head
    manifest["generated_at_utc"] = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        manifest["not_verified_ids"] = sorted(registry_ids())
    except (OSError, ValueError, json.JSONDecodeError):
        # Keep generation deterministic and let validation emit the precise
        # registry-authority error instead of silently retaining stale IDs.
        manifest["not_verified_ids"] = []
    suite_artifacts = load_suite_artifacts(ROOT, expected_head)
    for entry in manifest.get("evidence", []):
        if isinstance(entry, dict):
            entry["head_sha"] = expected_head
            entry["run_id"] = None
            entry["url"] = None
            entry["status"] = "NOT VERIFIED"
            entry["evidence_class"] = "NOT VERIFIED"
            entry["claim_scope"] = "LOCAL_CHECKOUT_ONLY" if entry.get("kind") == "local" else "UNSPECIFIED"
            entry["claim_label"] = "LOCAL EVIDENCE NOT VERIFIED" if entry.get("kind") == "local" else "NOT VERIFIED"
            entry["independent_verification"] = "NOT VERIFIED"
            if entry.get("kind") == "local":
                source_artifact = entry.get("source_artifact")
                source = (
                    find_suite_artifact(suite_artifacts, source_artifact) if isinstance(source_artifact, str) else None
                )
                if source and not suite_artifact_ownership_conflicted(source, suite_artifacts):
                    entry["status"] = "PROVEN"
                    entry["evidence_class"] = "PROVEN"
                    entry["claim_scope"] = "LOCAL_CHECKOUT_ONLY"
                    entry["claim_label"] = "LOCALLY PROVEN"
                    entry["independent_verification"] = "NOT VERIFIED"
                elif entry.get("kind") == "local":
                    entry["claim_scope"] = "LOCAL_CHECKOUT_ONLY"
                    entry["claim_label"] = "LOCAL EVIDENCE NOT VERIFIED"
                    entry["independent_verification"] = "NOT VERIFIED"
    for suite in manifest.get("suites", []):
        if isinstance(suite, dict):
            suite["commit"] = expected_head
            suite["status"] = "NOT VERIFIED"
            suite["discovered"] = None
            suite["passed"] = None
            suite["failed"] = None
            suite["ignored"] = None
            suite["filtered"] = None
            suite["claim_scope"] = "LOCAL_CHECKOUT_ONLY"
            suite["claim_label"] = "LOCAL SUITE NOT VERIFIED"
            suite["independent_verification"] = "NOT VERIFIED"
            source = find_suite_artifact(suite_artifacts, str(suite.get("name", "")))
            if source and not suite_artifact_ownership_conflicted(source, suite_artifacts):
                for field in (
                    "command",
                    "worktree_status",
                    "worktree_sha256",
                    "timestamp_utc",
                    "platform",
                    "platform_id",
                    "toolchain",
                    "discovered",
                    "passed",
                    "failed",
                    "ignored",
                    "filtered",
                    "status",
                    "exit_code",
                    "timeout_seconds",
                    "timed_out",
                    "termination",
                    "started_at_utc",
                    "finished_at_utc",
                    "duration_seconds",
                    "claim_scope",
                    "claim_label",
                    "independent_verification",
                    "stdout_sha256",
                    "stderr_sha256",
                    "combined_output_sha256",
                    "remote_observation",
                    "owner_id",
                    "gate_id",
                    "attempt_id",
                    "run_key",
                    "release_eligible",
                ):
                    if field in source:
                        suite[field] = source[field]
    evidence_by_id = {
        entry.get("id"): entry
        for entry in manifest.get("evidence", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    for requirement in manifest.get("requirements", []):
        if not isinstance(requirement, dict):
            continue
        evidence_ids = requirement.get("evidence_ids", [])
        referenced = [evidence_by_id.get(evidence_id) for evidence_id in evidence_ids]
        proven = bool(referenced) and all(entry and entry.get("status") in CURRENT_STATUSES for entry in referenced)
        if proven:
            requirement["status"] = "PROVEN"
            requirement["evidence_class"] = "PROVEN"
        elif requirement.get("status") == "PROVEN":
            requirement["status"] = "IMPLEMENTED / NOT VERIFIED"
            requirement["evidence_class"] = "NOT VERIFIED"
    for requirement in manifest.get("remediation_requirements", []):
        if isinstance(requirement, dict):
            requirement["final_sha"] = expected_head
            evidence_ids = requirement.get("evidence_ids", [])
            referenced = [evidence_by_id.get(evidence_id) for evidence_id in evidence_ids]
            proven = bool(referenced) and all(
                entry and entry.get("status") in CURRENT_STATUSES for entry in referenced
            )
            if proven:
                requirement["status"] = "PROVEN"
                requirement["evidence_class"] = "PROVEN"
            elif requirement.get("status") in CURRENT_STATUSES:
                requirement["status"] = "IMPLEMENTED / NOT VERIFIED"
                requirement["evidence_class"] = "NOT VERIFIED"
    return manifest


def validate_manifest(
    manifest: dict[str, Any], expected_head: str, *, verification_index_text: str | None = None
) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_registry_parity(manifest))
    commit = manifest.get("commit")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        errors.append("manifest.commit must be a 40-character lowercase git SHA")
    elif commit != expected_head:
        errors.append(f"manifest.commit={commit} does not match checked-out HEAD={expected_head}")

    if manifest.get("schema") != "aegis-evidence-manifest-v1":
        errors.append("manifest.schema must be aegis-evidence-manifest-v1")
    if manifest.get("branch") != "main":
        errors.append("manifest.branch must be main")

    requirements = manifest.get("requirements")
    raw_evidence = manifest.get("evidence")
    evidence_ids = (
        {entry.get("id") for entry in raw_evidence if isinstance(entry, dict)}
        if isinstance(raw_evidence, list)
        else set()
    )
    if not isinstance(requirements, list) or not requirements:
        errors.append("manifest.requirements must be a non-empty array")
    else:
        seen_requirement_ids: set[str] = set()
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, dict):
                errors.append(f"requirements[{index}] must be an object")
                continue
            requirement_id = requirement.get("id")
            if not isinstance(requirement_id, str) or not requirement_id:
                errors.append(f"requirements[{index}] lacks id")
            elif requirement_id in seen_requirement_ids:
                errors.append(f"duplicate requirement id: {requirement_id}")
            else:
                seen_requirement_ids.add(requirement_id)
            if not isinstance(requirement.get("tests"), str) or not requirement.get("tests"):
                errors.append(f"requirement[{requirement_id or index}] lacks tests")
            errors.extend(
                f"requirement[{requirement_id or index}] references unknown evidence {evidence_id}"
                for evidence_id in requirement.get("evidence_ids", [])
                if evidence_id not in evidence_ids
            )

    remediation_requirements = manifest.get("remediation_requirements")
    if not isinstance(remediation_requirements, list) or not remediation_requirements:
        errors.append("manifest.remediation_requirements must be a non-empty array")
    else:
        seen_remediation_ids: set[str] = set()
        for index, requirement in enumerate(remediation_requirements):
            if not isinstance(requirement, dict):
                errors.append(f"remediation_requirements[{index}] must be an object")
                continue
            requirement_id = requirement.get("id")
            if not isinstance(requirement_id, str) or not requirement_id:
                errors.append(f"remediation_requirements[{index}] lacks id")
            elif requirement_id in seen_remediation_ids:
                errors.append(f"duplicate remediation requirement id: {requirement_id}")
            else:
                seen_remediation_ids.add(requirement_id)
            missing_fields = [
                field
                for field in ("priority", "implementation", "closure", "evidence_class", "status", "final_sha")
                if not isinstance(requirement.get(field), str) or not requirement.get(field)
            ]
            errors.extend(
                f"remediation[{requirement_id or index}] lacks {field}"
                for field in missing_fields
            )
            if requirement.get("status") not in REMEDIATION_STATUSES:
                errors.append(f"remediation[{requirement_id or index}] has unsupported status {requirement.get('status')!r}")
            if requirement.get("evidence_class") not in REMEDIATION_STATUSES:
                errors.append(
                    f"remediation[{requirement_id or index}] has unsupported evidence class {requirement.get('evidence_class')!r}"
                )
            if requirement.get("final_sha") != expected_head:
                errors.append(
                    f"remediation[{requirement_id or index}].final_sha={requirement.get('final_sha')} does not match {expected_head}"
                )

    gt96_ids = {
        requirement.get("id")
        for requirement in requirements
        if isinstance(requirement, dict) and isinstance(requirement.get("id"), str) and requirement["id"].startswith("GT96-")
    }
    detail_path = ROOT / "docs" / "architecture" / "GT96_TRACEABILITY_DETAIL.md"
    if not detail_path.is_file():
        errors.append("GT96_TRACEABILITY_DETAIL.md is missing")
    else:
        detail_ids = set(GT96_ID_RE.findall(detail_path.read_text(encoding="utf-8")))
        if detail_ids != gt96_ids:
            errors.append(
                "GT96 detail matrix IDs do not match manifest requirements: "
                f"missing={sorted(gt96_ids - detail_ids)}, extra={sorted(detail_ids - gt96_ids)}"
            )

    evidence = manifest.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("manifest.evidence must be a non-empty array")
        evidence = []
    for index, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            errors.append(f"evidence[{index}] must be an object")
            continue
        status = entry.get("status")
        head_sha = entry.get("head_sha")
        if status in CURRENT_STATUSES and head_sha != expected_head:
            errors.append(
                f"evidence[{entry.get('id', index)}] status={status} has head_sha={head_sha}, expected {expected_head}"
            )
        if status in CURRENT_STATUSES and not entry.get("run_id") and entry.get("kind") != "local":
            errors.append(f"evidence[{entry.get('id', index)}] lacks run_id for current evidence")

    suites = manifest.get("suites")
    if not isinstance(suites, list) or not suites:
        errors.append("manifest.suites must be a non-empty array")
        suites = []
    required_suite_fields = {
        "name",
        "command",
        "commit",
        "timestamp_utc",
        "platform",
        "toolchain",
        "discovered",
        "passed",
        "failed",
        "ignored",
        "filtered",
    }
    for index, suite in enumerate(suites):
        if not isinstance(suite, dict):
            errors.append(f"suites[{index}] must be an object")
            continue
        missing = sorted(required_suite_fields.difference(suite))
        if missing:
            errors.append(f"suites[{suite.get('name', index)}] missing fields: {', '.join(missing)}")
        if suite.get("commit") != expected_head:
            errors.append(
                f"suite[{suite.get('name', index)}].commit={suite.get('commit')} does not match {expected_head}"
            )

    if verification_index_text is None:
        index_doc = ROOT / "docs" / "architecture" / "VERIFICATION_INDEX.md"
        if not index_doc.is_file():
            errors.append("VERIFICATION_INDEX.md is missing")
        else:
            verification_index_text = index_doc.read_text(encoding="utf-8")
    if verification_index_text is not None and not (
        expected_head in verification_index_text or "generated from checkout HEAD" in verification_index_text
    ):
        errors.append("VERIFICATION_INDEX.md does not reference the manifest commit")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--generate", type=Path, help="materialize a checkout-bound artifact from the tracked template")
    args = parser.parse_args()
    try:
        expected_head = git_head(ROOT)
        index_doc = ROOT / "docs" / "architecture" / "VERIFICATION_INDEX.md"
        index_text = index_doc.read_text(encoding="utf-8") if index_doc.is_file() else None
        manifest_path = args.manifest.resolve()
        if args.generate:
            generated = materialize_for_head(load_manifest(MANIFEST), expected_head)
            manifest_path = args.generate.resolve()
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        errors = validate_manifest(load_manifest(manifest_path), expected_head, verification_index_text=index_text)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"evidence consistency gate failed: {error}")
        return 1
    if errors:
        for error in errors:
            print(f"evidence consistency gate failed: {error}")
        return 1
    print(f"evidence consistency gate passed: current evidence is bound to {expected_head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
