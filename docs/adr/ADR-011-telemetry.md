# ADR-011: Runtime telemetry facade

Status: Accepted (2026-08-14)

## Context and problem

Resource and security decisions need observable status, but telemetry must not
become a second authority or leak task/provider secrets.

## Constraints and options

Telemetry must be bounded, schema-versioned, and safe when unavailable. Options
were ad hoc logs, a full event-sourced telemetry writer, or a small facade over
authoritative state.

## Decision and rationale

Expose normalized capability, admission, lane, and sandbox status through the
Rust facade. It reports decisions already made by authority and labels
measurement versus enforcement.

## Trade-offs and consequences

The facade may omit platform-specific detail, but it avoids coupling policy to
logging infrastructure and supports degraded operation.

## Rejected alternatives

Telemetry-driven admission was rejected because dropped metrics cannot decide
whether a task owns a resource.

## Migration, security, performance, operations, rollback

Version payloads, redact identifiers where needed, cap event sizes, and test
failure/backpressure. Measure overhead in the benchmark harness. Operators must
see `NOT VERIFIED`/`MeasurementOnly` states. Rollback disables optional export,
not the authoritative runtime.

## Evidence

Existing FFI status/capability functions and the resource usage contract.
