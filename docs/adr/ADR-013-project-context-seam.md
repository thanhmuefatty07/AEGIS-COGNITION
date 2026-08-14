# ADR-013: Future project-context provider seam

Status: Accepted, deferred implementation (2026-08-14)

## Context and problem

Project-aware agents may later need repository metadata, memory, tools, and
policy context. Importing those concerns into the runtime authority now would
expand the trusted computing base before the core closure is measured.

## Constraints and options

The seam must not grant context providers task authority. Options were embed
project context in the ledger, add a provider trait, or defer it.

## Decision and rationale

Reserve a provider seam for read/proposal context only; keep implementation
deferred until resource/runtime closure and requirements traceability are green.

## Trade-offs and consequences

Feature work waits, but provider failures cannot corrupt leases or transitions.
The future API must carry provenance and versioned snapshots.

## Rejected alternatives

Direct provider callbacks inside transition code were rejected for reentrancy,
latency, and authority reasons.

## Migration, security, performance, operations, rollback

Introduce a typed snapshot and capability list, add timeout/cancellation, and
fuzz provider responses before implementation. Measure context latency and
cache invalidation. Disable the provider without changing the ledger on
rollback.

## Evidence

This is a documented deferred seam; no implementation claim is made. See the
traceability matrix for scope boundaries.
