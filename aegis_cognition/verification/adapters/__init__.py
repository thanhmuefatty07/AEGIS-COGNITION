"""Bounded verification adapters.

Adapters describe commands and parse receipts.  They do not own process
execution; the existing Lab execution-cell/tool-call path remains the only
authority allowed to run them.
"""

from .generic import (
    AdapterCapabilities,
    AdapterFailure,
    BoundedCommand,
    GenericBoundedAdapter,
    ParsedTestResult,
)
from .registry import AdapterDescriptor, AdapterRegistry

__all__ = [
    "AdapterCapabilities",
    "AdapterDescriptor",
    "AdapterFailure",
    "AdapterRegistry",
    "BoundedCommand",
    "GenericBoundedAdapter",
    "ParsedTestResult",
]
