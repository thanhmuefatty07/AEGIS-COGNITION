# ADR-006: Locked Python environment and native packaging

Status: Accepted

`pyproject.toml` expresses dependency intent and `uv.lock` is the reproducible Python
environment source of truth. Native packaging will converge on maturin after the PyO3
compatibility and wheel smoke tests are in place. A generated requirements export is
compatibility output, not the authoritative resolver input.
