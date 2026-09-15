"""Trust policy and hot evidence publication boundary."""

from __future__ import annotations

import json
import hashlib
from typing import Any

from .contracts import HotCommitRecord, TrustPolicySnapshot
from .native import native_module
from .trust_policy import (
    TRUST_POLICY_SCHEMA,
    VALID_TRUST_LEVELS,
    normalize_trust_level,
    trust_policy_hash,
    trust_policy_payload,
)


__all__ = [
    "VALID_TRUST_LEVELS",
    "commit_hot_evidence",
    "commit_hot_evidence_batch",
    "normalize_trust_level",
    "trust_policy_snapshot",
]


def trust_policy_snapshot(trust_level: str | None = None) -> TrustPolicySnapshot:
    level = normalize_trust_level(trust_level)
    payload = trust_policy_payload(level)
    return TrustPolicySnapshot(
        schema=TRUST_POLICY_SCHEMA,
        truth_claim=False,
        trust_level=level,
        physical_witness_required=payload["physical_witness_required"],
        fail_closed=payload["fail_closed"],
        degraded_hot_evidence_allowed=payload["degraded_hot_evidence_allowed"],
        rust_extension_required=payload["rust_extension_required"],
        missing_artifact_policy=payload["missing_artifact_policy"],
        dual_approval_required=payload["dual_approval_required"],
        trust_policy_hash=trust_policy_hash(level),
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
    buffer_commit = getattr(aegis_nerve, "aegis_hot_commit_buffer", None)
    # Immutable ``bytes`` already has an ownership-preserving native path:
    # PyO3 materializes one Vec and Rust moves that Vec into the arena. Use the
    # buffer adapter for other exporters, where it avoids a boundary Vec and
    # can release the GIL for read-only memory.
    if callable(buffer_commit) and not isinstance(payload, bytes):
        raw = buffer_commit(payload, level)
    else:
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
                b"aegis-dev-hot-arena-batch-storage-ref-v1" + index.to_bytes(8, "little") + digest.encode("ascii"),
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
