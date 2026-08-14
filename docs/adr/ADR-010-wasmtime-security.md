# ADR-010: Wasmtime is a migration, not a version bump

Status: Accepted (2026-08-14)

## Context and problem

Wasmtime is security-sensitive and changed from the prior 22.x line to 47.0.3.
A version edit without fuel, epoch, memory, WASI, replay, and adversarial proof
does not establish a safe sandbox.

## Constraints and options

The runtime needs bounded execution and no accidental network/preopen grant.
Options were stay on 22.x, upgrade without evidence, or upgrade with a staged
security gate.

## Decision and rationale

Keep `47.0.3`, enable fuel and epoch interruption, enforce memory limits, retain
WASI policy restrictions, and require source/static, Rust behavior, advisory,
fuzz, adversarial, and replay-parity evidence.

## Trade-offs and consequences

Migration work and possible throughput changes are accepted for supported
security fixes. The host kernel/process remains a residual boundary; no escape
guarantee is claimed.

## Rejected alternatives

Blind downgrade and “dependency version equals sandbox” were rejected.

## Migration, security, performance, operations, rollback

Use `docs/architecture/WASMTIME_MIGRATION.md` and its commands. Review fuel,
epoch, memory, WASI, replay, and error behavior together; retain raw fuzz and
hostile-module artifacts. Measure cold start, fuel throughput, and tail latency.
Rollback is a pinned, advisory-reviewed change with the same gate.

## Evidence

`sandbox.rs`, Wasmtime migration gate, sandbox tests, `cargo audit`; fuzz and
privileged isolation evidence remain explicitly open.
