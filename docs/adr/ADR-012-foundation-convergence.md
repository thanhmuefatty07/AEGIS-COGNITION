# ADR-012: Foundation convergence boundaries

## Context

The foundation review found that the repository's evidence and runtime policy
were stronger than the Python structure and fast CI boundaries. The canonical
`Agent` facade mixed configuration, prompt/RAG preparation, gateway creation,
and learning indexing. The friendly Rust bridge also mixed DTOs, provider
routing, trust/evidence publication, browser capture, and learning.

## Decision

- `aegis_cognition.Agent` is a thin facade over `config.py`, `application.py`,
  `infrastructure.py`, and `models.py`.
- `core/python/aegis_adapter.py` remains the compatibility facade, while typed
  contracts, provider routing, hashing, evidence publication, and learning live
  in `core/python/aegis/` modules.
- `rust-toolchain.toml` is the exact compiler source of truth. Cargo keeps the
  derived major/minor minimum because Cargo manifests cannot interpolate files;
  CI uses `toolchain: file` and architecture fitness verifies the derivation.
- Rust 2024 uses resolver 3. POCs remain workspace members for deep evidence,
  but production/default CI uses `default-members` so experiments cannot make
  the fast gate authoritative.
- Python runtime telemetry uses the versioned Rust schema, bounded local
  metrics, shared correlation IDs, and an optional PyO3 forwarding call. It is
  observation-only and cannot affect Rust authority.

## Options considered

1. Keep the existing large modules and add more tests. Rejected: tests would
   document coupling without preventing responsibility drift.
2. Create separate services for Python orchestration and telemetry. Rejected:
   the modular monolith already gives explicit dependency boundaries without
   network failure, deployment, and retry complexity.
3. Adopt the module boundaries above. Chosen: reversible, local, testable, and
   sufficient for the current blast radius.

## Migration, security, performance, operations, rollback

- Migration is additive: old import paths re-export the split DTOs and learning
  types; the root wheel still includes `core/python`.
- Trust/evidence publication remains fail-closed outside DEV; telemetry never
  becomes a source of truth and never carries task output, secrets, or raw
  provider content.
- Fast CI excludes POCs by default; deep CI still exercises the full workspace,
  nextest, deny policy, and LLVM coverage.
- Rollback is a single commit revert because contracts and schemas remain
  versioned; no persisted migration is required.

## Evidence

- `scripts/architecture_fitness.py` enforces the source-of-truth, resolver,
  default-member, Python boundary, adapter split, and legacy ownership rules.
- Ruff expanded to `UP`, `B`, `SIM`, `PERF`, `RUF`, and `ASYNC`; Pyright remains
  strict.
- `tests/test_runtime_contracts.py` proves correlation-chain preservation and
  bounded Python metrics; Rust telemetry tests prove JSON-boundary validation.
- Full CI/deep evidence remains subject to the current GitHub run for the final
  commit; external privileged OS, OTel exporter, and H0/H1/H2 proof remain
  explicitly `NOT VERIFIED`.
