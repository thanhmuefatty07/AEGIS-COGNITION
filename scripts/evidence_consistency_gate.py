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
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GT96_ID_RE = re.compile(r"\|\s*(GT96-\d{3})\s*\|")
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
            artifacts[name] = candidate
    return artifacts


def find_suite_artifact(suite_artifacts: dict[str, dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Match canonical CI names and local ``-final`` evidence names."""
    candidates = (name, f"{name}-final")
    for candidate in candidates:
        if candidate in suite_artifacts:
            return suite_artifacts[candidate]
    return None


def materialize_for_head(template: dict[str, Any], expected_head: str) -> dict[str, Any]:
    """Create a non-self-referential evidence artifact for this checkout."""
    manifest = json.loads(json.dumps(template))
    manifest["commit"] = expected_head
    manifest["generated_at_utc"] = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
                if source and source.get("status") == "PROVEN" and source.get("failed") == 0:
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
            if source:
                for field in (
                    "command",
                    "timestamp_utc",
                    "platform",
                    "toolchain",
                    "discovered",
                    "passed",
                    "failed",
                    "ignored",
                    "filtered",
                    "status",
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
            for field in ("priority", "implementation", "closure", "evidence_class", "status", "final_sha"):
                if not isinstance(requirement.get(field), str) or not requirement.get(field):
                    errors.append(f"remediation[{requirement_id or index}] lacks {field}")
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
