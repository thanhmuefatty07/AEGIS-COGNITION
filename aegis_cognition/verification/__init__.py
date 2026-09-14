"""Integrated AESE verification capability.

This package is deliberately a control-plane façade.  It owns versioned
contracts, session state, planning and assessment, while execution remains
bound to the existing Lab authority.
"""

from .contracts import (
    ArtifactReference,
    ContractValidationError,
    DevelopmentVerificationSession,
    ExecutionReceipt,
    ProjectProfile,
    TestChangeProposal,
    TestDescriptor,
    VerificationAssessment,
    VerificationEvent,
    VerificationPlan,
    VerificationRequirement,
    canonical_hash,
)
from .facade import AgentImplementationPacket, VerificationFacade
from .session import SessionState, VerificationSessionError

__all__ = [
    "AgentImplementationPacket",
    "ArtifactReference",
    "ContractValidationError",
    "DevelopmentVerificationSession",
    "ExecutionReceipt",
    "ProjectProfile",
    "SessionState",
    "TestChangeProposal",
    "TestDescriptor",
    "VerificationAssessment",
    "VerificationEvent",
    "VerificationFacade",
    "VerificationPlan",
    "VerificationRequirement",
    "VerificationSessionError",
    "canonical_hash",
]
