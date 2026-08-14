# ADR-003: Free-threaded Python is an optimization lane

Status: Accepted (2026-08-14)

## Context and problem

Free-threaded Python changes extension ABI and concurrency assumptions. Treating
it as a default runtime could turn a speed experiment into a correctness risk.

## Constraints and options

The core must work on ordinary CPython; optional builds may be evaluated. The
options were no free-threaded support, default free-threaded support, or an
isolated experimental lane.

## Decision and rationale

Free-threaded Python is experimental and cannot change Rust authority or token
semantics. It is enabled only after native-extension and dependency tests pass.

## Trade-offs and consequences

This delays possible parallel speedups but preserves a predictable production
ABI and makes GIL assumptions visible.

## Rejected alternatives

Making free-threaded Python the default was rejected because ABI coverage and
third-party compatibility are not yet measured.

## Migration, security, performance, operations, rollback

Add a separate CI matrix lane, record interpreter ABI, and run race/stress
tests before enabling it. Keep FFI inputs immutable and Rust-side fencing
unchanged. Compare throughput and tail latency to the production lane. Remove
the lane or pin the prior interpreter if regressions appear.

## Evidence

The policy is reflected in ADR-002 and the release matrix; free-threaded
production readiness remains `NOT VERIFIED`.
