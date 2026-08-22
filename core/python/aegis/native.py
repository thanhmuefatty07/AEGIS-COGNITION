"""Canonical loader for the packaged PyO3 extension."""

from __future__ import annotations

import sys
from typing import Any, cast


def native_module() -> Any:
    """Load the top-level or maturin-packaged Rust extension."""

    # Tests deliberately install ``None`` sentinels in ``sys.modules`` to
    # exercise the no-extension fallback even when an editable maturin build
    # has already attached the packaged extension to ``aegis_cognition``.
    if any(
        name in sys.modules and sys.modules[name] is None
        for name in ("aegis_nerve", "aegis_cognition.aegis_nerve")
    ):
        raise ImportError("Rust aegis_nerve extension is explicitly unavailable")

    try:
        import aegis_nerve  # type: ignore[import-untyped]
    except ImportError:
        from aegis_cognition import aegis_nerve  # type: ignore[import-not-found]
    return cast(Any, aegis_nerve)
