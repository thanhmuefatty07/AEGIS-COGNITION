# ADR-006: Locked Python environment and native packaging

Status: Accepted (2026-08-14)

## Context and problem

The project is a Python/Rust package. A setuptools-only metadata build leaves
the PyO3 extension and its Cargo feature implicit.

## Constraints and options

Wheels must contain the Python package and a native extension for supported
platforms. Options were setuptools plus a separate native build, maturin, or a
manual artifact copier.

## Decision and rationale

Maturin 1.14.1 is the root PEP 517 backend, targeting
`aegis_cognition.aegis_nerve`; the wheel explicitly includes the
`core/python` bridge package, and Cargo enables `python-extension` only for
wheel builds. `uv.lock` is the environment authority and tests run through
`uv run`.

## Trade-offs and consequences

Builds require a Rust toolchain and can be slower, but Python/Rust ABI wiring is
centralized and inspectable. A direct `aegis_nerve` import remains supported as
a compatibility path where separately installed.

## Rejected alternatives

Keeping extension-module enabled for all Rust tests was rejected because it
caused linker failures. A shell copy step was rejected because it is not a
portable wheel contract.

## Migration, security, performance, operations, rollback

Build wheels with maturin, inspect their contents, install in a clean venv, and
run import/FFI tests. Pin build dependencies and scan artifacts. Record build
time and wheel size. Rollback means a reviewed backend change, not a mixed
setuptools/maturin build in one release.

## Evidence

`pyproject.toml`, `core/rust/Cargo.toml`, CI packaging steps, and the packaging
gate are the source of truth; local exact-3.14.7 build is pending when available.
The bridge-only setuptools manifest is retained for legacy narrow images and is
not published as the canonical distribution.
