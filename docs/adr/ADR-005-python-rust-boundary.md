# ADR-005: Coarse typed PyO3 boundary

Status: Accepted

Cross the language boundary with mission/proposal/work-batch contracts, not individual
TaskLedger mutations. Rust types are authoritative; Python receives validated mirrors
or JSON/schema envelopes for integration. Process isolation is reserved for unsafe
native plugins, untrusted code, and crash-prone drivers.
