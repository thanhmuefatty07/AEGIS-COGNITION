# ADR-001: Rust owns runtime authority

Status: Accepted

Rust owns authoritative TaskLedger transitions, resource admission and leases,
capability/policy checks, cancellation, sandbox/process boundaries, replay integrity,
and evidence admissibility. Python may propose missions and semantic work but cannot
mutate authoritative state directly.

This keeps correctness valid when Python crashes, hangs, or returns malformed output
and preserves a future option to host Python differently without changing domain rules.
