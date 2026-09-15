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
from .execution import LocalVerificationCommand, build_local_verification_commands, run_local_verification_command
from .session import SessionState, VerificationSessionError

__all__ = [
    "AgentImplementationPacket",
    "ArtifactReference",
    "ContractValidationError",
    "DevelopmentVerificationSession",
    "ExecutionReceipt",
    "LocalVerificationCommand",
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
    "build_local_verification_commands",
    "canonical_hash",
    "run_local_verification_command",
]
