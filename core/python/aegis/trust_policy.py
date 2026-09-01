"""Canonical, hash-bound trust-policy subject shared by all Python bridges.

The mission boundary still chooses the entrypoint context (for example, the
friendly Agent path defaults to ``DEV`` while the standalone evidence bridge
defaults to ``PROD``).  This module owns the policy schema and digest so those
contextual defaults cannot drift in their fields or hash representation.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any


TRUST_POLICY_SCHEMA = "aegis-friendly-trust-policy-v1"
VALID_TRUST_LEVELS = frozenset({"DEV", "STAGING", "PROD"})


def normalize_trust_level(value: str | None, *, default: str = "PROD") -> str:
    """Resolve and validate one trust level without silently changing policy.

    ``default`` is explicit at each compatibility boundary because changing a
    legacy default would be a public security/compatibility change.  The
    policy fields and digest are nevertheless canonical for every level.
    """

    selected = (value or os.environ.get("AEGIS_TRUST_LEVEL") or default).strip().upper()
    if selected not in VALID_TRUST_LEVELS:
        raise ValueError("AEGIS_TRUST_LEVEL must be DEV, STAGING, or PROD")
    return selected


def trust_policy_payload(trust_level: str) -> dict[str, Any]:
    """Return the canonical, non-truth-claiming policy subject."""

    level = str(trust_level).strip().upper()
    if level not in VALID_TRUST_LEVELS:
        raise ValueError("trust level must be DEV, STAGING, or PROD")
    return {
        "schema": TRUST_POLICY_SCHEMA,
        "trust_level": level,
        "physical_witness_required": level == "PROD",
        "fail_closed": level == "PROD",
        "degraded_hot_evidence_allowed": level == "DEV",
        "rust_extension_required": level != "DEV",
        "missing_artifact_policy": {
            "DEV": "warn_continue",
            "STAGING": "record_gap_without_truth_claim",
            "PROD": "fail_closed",
        }[level],
        "dual_approval_required": level == "PROD",
        "truth_claim": False,
    }


def trust_policy_hash(trust_level: str) -> str:
    """Return the canonical BLAKE2b-256 digest of a policy subject."""

    encoded = json.dumps(
        trust_policy_payload(trust_level), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=32).hexdigest()


__all__ = [
    "TRUST_POLICY_SCHEMA",
    "VALID_TRUST_LEVELS",
    "normalize_trust_level",
    "trust_policy_hash",
    "trust_policy_payload",
]
