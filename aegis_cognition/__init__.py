"""AEGIS-COGNITION public exports.

Loaded lazily by the package initializer so small bounded entry points, such as
 the desktop sidecar, do not import the entire public gateway graph.
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent as Agent
    from .agent import run as run

__version__ = "0.1.0"

if os.environ.get("AEGIS_DESKTOP_SIDECAR") != "1":
    _exports = importlib.import_module(f"{__name__}._public_exports")
    _export_names = list(_exports.__all__)
    globals()["__all__"] = _export_names
    for _name in _export_names:
        globals()[_name] = getattr(_exports, _name)
else:
    globals()["__all__"] = []


def version() -> str:
    """Return AEGIS-COGNITION version."""
    return __version__
