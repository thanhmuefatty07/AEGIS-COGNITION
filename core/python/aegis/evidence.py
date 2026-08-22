"""Trust policy and hot evidence publication boundary."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .contracts import HotCommitRecord, TrustPolicySnapshot
from .hashing import stable_hash
from .native import native_module


VALID_TRUST_LEVELS = {"DEV", "STAGING", "PROD"}


def normalize_trust_level(value: str | None) -> str:
    level = (value or os.environ.get("AEGIS_TRUST_LEVEL") or "PROD").strip().upper()
    if level not in VALID_TRUST_LEVELS:
        raise ValueError("AEGIS_TRUST_LEVEL must be DEV, STAGING, or PROD")
    return level


def trust_policy_snapshot(trust_level: str | None = None) -> TrustPolicySnapshot:
    level = normalize_trust_level(trust_level)
    physical_witness_required = level == "PROD"
    fail_closed = level == "PROD"
    degraded_hot_evidence_allowed = level == "DEV"
    rust_extension_required = level != "DEV"
    dual_approval_required = level == "PROD"
    missing_artifact_policy = {
        "DEV": "warn_continue",
        "STAGING": "record_gap_without_truth_claim",
        "PROD": "fail_closed",
    }[level]
    payload = {
        "schema": "aegis-friendly-trust-policy-v1",
        "trust_level": level,
        "physical_witness_required": physical_witness_required,
        "fail_closed": fail_closed,
        "degraded_hot_evidence_allowed": degraded_hot_evidence_allowed,
        "rust_extension_required": rust_extension_required,
        "missing_artifact_policy": missing_artifact_policy,
        "dual_approval_required": dual_approval_required,
        "truth_claim": False,
    }
    return TrustPolicySnapshot(
        schema="aegis-friendly-trust-policy-v1",
        truth_claim=False,
        trust_level=level,
        physical_witness_required=physical_witness_required,
        fail_closed=fail_closed,
        degraded_hot_evidence_allowed=degraded_hot_evidence_allowed,
        rust_extension_required=rust_extension_required,
        missing_artifact_policy=missing_artifact_policy,
        dual_approval_required=dual_approval_required,
        trust_policy_hash=stable_hash(payload),
    )


def commit_hot_evidence(payload: bytes, trust_level: str | None = None) -> HotCommitRecord:
    level = normalize_trust_level(trust_level)
    try:
        aegis_nerve = native_module()
    except Exception as exc:
        if level != "DEV":
            raise RuntimeError("Rust aegis_nerve extension is required outside DEV trust level") from exc
        digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
        storage_ref = hashlib.blake2b(
            b"aegis-dev-hot-storage-ref-v1" + digest.encode("ascii"), digest_size=32
        ).hexdigest()
        return HotCommitRecord(
            schema="aegis-hot-arena-commit-v1",
            truth_claim=False,
            verifier="python-dev-fallback",
            byte_len=len(payload),
            artifact_hash=digest,
            storage_ref_hash=storage_ref,
            trust_level=level,
            admission="degraded_warning",
            physical_witness_required=False,
            fail_closed=False,
            handle_valid=True,
            degraded_reason="missing-aegis-nerve-extension",
        )
    raw = aegis_nerve.aegis_hot_commit(payload, level)
    return HotCommitRecord.from_mapping(json.loads(raw))


def commit_hot_evidence_batch(artifacts: list[Any], trust_level: str | None = None) -> dict[str, Any] | str:
    level = normalize_trust_level(trust_level)
    payloads = [artifact.bytes for artifact in artifacts]
    if not payloads or any(not payload for payload in payloads):
        raise ValueError("hot evidence batch requires non-empty browser artifacts")
    try:
        aegis_nerve = native_module()
    except Exception as exc:
        if level != "DEV":
            raise RuntimeError("Rust aegis_nerve extension is required outside DEV trust level") from exc
        commits: list[dict[str, Any]] = []
        total_bytes = 0
        batch_hasher = hashlib.blake2b(digest_size=32)
        batch_hasher.update(b"aegis-hot-arena-commit-batch-v1")
        batch_hasher.update(len(payloads).to_bytes(8, "little"))
        for index, payload in enumerate(payloads):
            digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
            storage_ref = hashlib.blake2b(
                b"aegis-dev-hot-arena-batch-storage-ref-v1"
                + index.to_bytes(8, "little")
                + digest.encode("ascii"),
                digest_size=32,
            ).hexdigest()
            total_bytes += len(payload)
            batch_hasher.update(index.to_bytes(8, "little"))
            batch_hasher.update(len(payload).to_bytes(8, "little"))
            batch_hasher.update(digest.encode("ascii"))
            batch_hasher.update(storage_ref.encode("ascii"))
            commits.append(
                {
                    "schema": "aegis-hot-arena-commit-v1",
                    "truth_claim": False,
                    "verifier": "python-dev-batch-fallback",
                    "index": index,
                    "byte_len": len(payload),
                    "artifact_hash": digest,
                    "storage_ref_hash": storage_ref,
                    "trust_level": level,
                    "admission": "degraded_warning",
                    "physical_witness_required": False,
                    "fail_closed": False,
                    "handle_valid": True,
                }
            )
        return {
            "schema": "aegis-hot-arena-commit-batch-v1",
            "truth_claim": False,
            "verifier": "python-dev-batch-fallback",
            "trust_level": level,
            "artifact_count": len(commits),
            "total_bytes": total_bytes,
            "no_file_roundtrip_on_hot_path": True,
            "batch_digest": batch_hasher.hexdigest(),
            "arena_live_bytes": total_bytes,
            "arena_live_slots": len(commits),
            "arena_total_commits": len(commits),
            "arena_total_degraded_commits": len(commits),
            "commits": commits,
        }
    return aegis_nerve.aegis_hot_commit_batch(payloads, level)
