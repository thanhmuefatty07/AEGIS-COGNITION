"""
AEGIS-COGNITION: Cryptographically-verified AI agent harness.

Drop-in developer experience:
    from aegis_cognition import Agent
    agent = Agent(task="Find trending repos on GitHub")
    result = agent.run()

For quick setup:
    pip install aegis-cognition
    aegis init
    aegis run "Hello world"
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "DEFAULT_UNIT_REGISTRY",
    "EVIDENCE_CLASSES",
    "EVIDENCE_STATUSES",
    "AdaptiveController",
    "AdaptiveMeasurementResult",
    "AdaptiveMeasurementSession",
    "AdaptiveMeasurementSpec",
    "Agent",
    "AnalyticPredictionModel",
    "AnchorCandidate",
    "AnchorObservation",
    "AnchorSelectionPlan",
    "AuthorityMode",
    "BenchmarkProtocolV2",
    "BenchmarkResultV2",
    "BenchmarkTrialRecord",
    "BrowserObserverView",
    "CalibrationResult",
    "CorrelationContext",
    "CoverageVector",
    "ElectricalSignalCell",
    "ElectricalSignalResult",
    "ElectricalSignalSpec",
    "EnvironmentFingerprint",
    "EvidenceLedger",
    "EvidenceLedgerEntry",
    "ExecutionCellBinding",
    "ExecutionCellRegistry",
    "HardwareCapabilityVector",
    "Lab",
    "LabApplication",
    "LabBudget",
    "LabDossier",
    "LabMissionSpec",
    "LabPolicy",
    "LabSession",
    "ODEIntegrationResult",
    "PhysicalConstraint",
    "PredictionResult",
    "ProcessExecutionCell",
    "ReplayWriterLease",
    "RuntimeMetrics",
    "RuntimeTelemetry",
    "SearchOperation",
    "SearchProgram",
    "SearchProgramExecutor",
    "SimulationCell",
    "SimulationEvidence",
    "SimulationSpec",
    "SkillAdmission",
    "SkillAdmissionError",
    "SkillExecutionReceipt",
    "SkillManifest",
    "SkillRegistry",
    "UnitDefinition",
    "UnitRegistry",
    "WorkloadSignature",
    "calibrate_simulation",
    "evaluate_adaptive_measurement",
    "evaluate_benchmark",
    "finish_runtime_lease",
    "hardware_profile",
    "predict_cross_hardware",
    "resource_contract_version",
    "run",
    "select_anchor_plan",
    "submit_runtime_task",
    "version",
]

# Re-export the Friendly Gateway Agent with simple name
from .agent import Agent
from .agent import run as run
from .benchmark import (
    BenchmarkProtocolV2,
    BenchmarkResultV2,
    BenchmarkTrialRecord,
    EnvironmentFingerprint,
    evaluate_benchmark,
)
from .aese import (
    AdaptiveMeasurementResult,
    AdaptiveMeasurementSpec,
    AdaptiveMeasurementSession,
    AnchorCandidate,
    AnchorSelectionPlan,
    AnchorObservation,
    AnalyticPredictionModel,
    EVIDENCE_CLASSES,
    EVIDENCE_STATUSES,
    EvidenceLedger,
    EvidenceLedgerEntry,
    HardwareCapabilityVector,
    PredictionResult,
    CoverageVector,
    SimulationEvidence,
    WorkloadSignature,
    evaluate_adaptive_measurement,
    predict_cross_hardware,
    select_anchor_plan,
)
from .lab import (
    DEFAULT_UNIT_REGISTRY,
    AdaptiveController,
    AuthorityMode,
    CalibrationResult,
    ElectricalSignalCell,
    ElectricalSignalResult,
    ElectricalSignalSpec,
    ExecutionCellBinding,
    ExecutionCellRegistry,
    Lab,
    LabApplication,
    LabBudget,
    LabDossier,
    LabMissionSpec,
    LabPolicy,
    LabSession,
    ODEIntegrationResult,
    BrowserObserverView,
    PhysicalConstraint,
    ProcessExecutionCell,
    ReplayWriterLease,
    SearchOperation,
    SearchProgram,
    SearchProgramExecutor,
    SkillAdmission,
    SkillAdmissionError,
    SkillExecutionReceipt,
    SkillManifest,
    SkillRegistry,
    SimulationCell,
    SimulationSpec,
    UnitDefinition,
    UnitRegistry,
    calibrate_simulation,
)
from .metrics import RuntimeMetrics
from .observability import CorrelationContext, RuntimeTelemetry
from .runtime import (
    finish_runtime_lease,
    hardware_profile,
    resource_contract_version,
    submit_runtime_task,
)


def version() -> str:
    """Return AEGIS-COGNITION version."""
    return __version__
