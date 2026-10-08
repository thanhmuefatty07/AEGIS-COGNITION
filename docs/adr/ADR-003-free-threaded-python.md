# ADR-003: Free-threaded Python experiment (superseded)

Status: Superseded (2026-10-05; see current policy below)

Superseding outcome: the current runtime and CI support standard CPython only.
Free-threaded Python is not a supported or experimental lane. Reconsider it only
through a new decision after native-extension ABI, dependency, and concurrency
compatibility are independently verified.

## Context and problem

Free-threaded Python changes extension ABI and concurrency assumptions. Treating
it as a default runtime could turn a speed experiment into a correctness risk.

## Constraints and options

The core must work on ordinary CPython; optional builds may be evaluated. The
options were no free-threaded support, default free-threaded support, or an
isolated experimental lane.

## Historical decision and rationale

The former decision treated free-threaded Python as experimental and required
that it not change Rust authority or token semantics. It could be enabled only
after native-extension and dependency tests passed.

## Historical trade-offs and consequences

That approach delayed possible parallel speedups while preserving a predictable
production ABI and making GIL assumptions visible.

## Historical rejected alternatives

Making free-threaded Python the default was rejected because ABI coverage and
third-party compatibility had not been measured.

## Migration, security, performance, operations, rollback

The former plan was to add a separate CI matrix lane, record interpreter ABI,
and run race/stress tests before enabling it. It also required immutable FFI
inputs, unchanged Rust-side fencing, and throughput/tail-latency comparison.

## Evidence

This ADR records the former experimental-lane decision. The current standard-
CPython-only matrix is reflected in ADR-002, `.python-version`, and
`.github/workflows/ci.yml`; historical free-threaded runs do not establish
current support.
