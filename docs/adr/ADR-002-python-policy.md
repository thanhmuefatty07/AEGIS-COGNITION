# ADR-002: Python support policy

Status: Accepted (2026-08-14)

## Context and problem

Python versions affect native wheels, free-threaded experiments, CI supply
chain, and reproducibility. A floating `3.15` request can select a release
that does not exist on a runner.

## Constraints and options

Production needs a released CPython; forward work may use a prerelease.
Options were floating minors, one exact version, or exact production and
forward lanes.

## Decision and rationale

Production is CPython `3.14.7`; the forward lane is `3.15.0rc1`, with
`allow-prereleases: true` only for that lane. `.python-version`, project
metadata, and setup-python are aligned.

## Trade-offs and consequences

Exact versions improve reproducibility but require intentional updates. The
release lane is stable; the prerelease lane can fail without blocking the
production policy when CI marks lane semantics clearly.

## Rejected alternatives

Floating `3.15` was rejected because setup-python could not resolve it. A
free-threaded build as the default was rejected until extension compatibility is
measured.

## Migration, security, performance, operations, rollback

Update `.python-version`, CI matrix, `uv.lock`, and release wheel tests together.
Run Python tests with `uv run --locked`, not a system interpreter. Track ABI and
dependency vulnerabilities per lane. Performance comparisons must record the
interpreter build. Rollback is a pinned patch update with a lock refresh.

## Evidence

`.github/workflows/ci.yml`, `.python-version`, `pyproject.toml`, and the CI run
artifacts linked from the traceability matrix.
