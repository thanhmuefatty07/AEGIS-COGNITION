"""Canonical loader for the packaged PyO3 extension."""

from __future__ import annotations

from typing import Any, cast


def native_module() -> Any:
    """Load the top-level or maturin-packaged Rust extension."""

    try:
        import aegis_nerve  # type: ignore[import-untyped]
    except ImportError:
        from aegis_cognition import aegis_nerve  # type: ignore[import-not-found]
    return cast(Any, aegis_nerve)
