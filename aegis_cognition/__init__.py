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
    "Agent",
    "CorrelationContext",
    "RuntimeMetrics",
    "RuntimeTelemetry",
    "finish_runtime_lease",
    "hardware_profile",
    "resource_contract_version",
    "run",
    "submit_runtime_task",
    "version",
]

# Re-export the Friendly Gateway Agent with simple name
from .agent import Agent
from .agent import run as run
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
