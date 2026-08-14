# ADR-005: Coarse typed PyO3 boundary

Status: Accepted (2026-08-14)

## Context and problem

Passing scheduler internals field by field across PyO3 invites partial writes,
version skew, and authority confusion.

## Constraints and options

The boundary must be versioned, inspectable, and usable by Python clients.
Options were a broad object bridge, opaque bytes, or coarse JSON contracts with
Rust validation.

## Decision and rationale

Use versioned JSON requests/results for coarse operations and keep lease grants
inside Rust. The only returned completion handle is an opaque schema/id/
generation/attempt token.

## Trade-offs and consequences

Serialization adds overhead but makes malformed input rejectable and supports
future alternate hosts. Python cannot perform direct state mutation.

## Rejected alternatives

Returning `ResourceLease` was rejected after the duplicate-finish authority
review; raw unversioned bytes were rejected for observability and migration.

## Migration, security, performance, operations, rollback

Validate schema and nonzero identifiers at every ingress. Fuzz JSON and test
forged tokens. Measure serialization separately from scheduling. Log rejected
schema/token reasons without logging secrets. Roll back only with a compatible
token decoder and a new schema identifier.

## Evidence

`core/rust/src/ffi.rs`, `resource.rs`, `aegis_cognition/runtime.py`, and runtime
forgery tests.
