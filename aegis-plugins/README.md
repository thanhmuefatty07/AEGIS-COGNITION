# AEGIS Plugin Suite

Rust workspace crates for research, browser automation, sandbox execution, skills,
benchmarking, and evidence verification.

## Crates

- `aegis-search-sdk`: programmable search primitives and pipeline execution.
- `aegis-browser`: browser automation abstractions with CDP-oriented interfaces.
- `aegis-sandbox`: restricted Python execution policy and filesystem state manager.
- `aegis-skills`: markdown skill registry and task-based selection.
- `aegis-evidence`: evidence binding and tamper-evident audit chain.
- `aegis-bench`: benchmark gate data structures and report validation.

## Status

This suite starts with deterministic, testable local cores. OS-level sandbox
backends, external CDP drivers, and remote persistence adapters plug into these
APIs through explicit traits so tests do not claim security or stealth evidence
that the host has not physically proven.
