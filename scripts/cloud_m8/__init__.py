"""AEGIS Milestone 8 / Cloud Research Infrastructure Orchestration Suite.

Provides fail-closed private-archive provenance helpers, Spot/On-Demand VM
orchestration over Cloud NAT + IAP (`--no-address`), VPC-internal TCP cluster
orchestration, IAM-authenticated Cloud Run verification services, SLSA/Cosign
attestation rehearsal, Track 2 receipt collectors, and master coordination.
"""

from __future__ import annotations

from scripts.cloud_m8.provenance import (
    DEFAULT_ARTIFACT_REGISTRY,
    DEFAULT_RESEARCH_BUCKET,
    PrivateArchiveUnavailableError,
    resolve_private_archive,
)

__version__ = "0.9.0"
__all__ = [
    "DEFAULT_ARTIFACT_REGISTRY",
    "DEFAULT_RESEARCH_BUCKET",
    "PrivateArchiveUnavailableError",
    "__version__",
    "resolve_private_archive",
]
