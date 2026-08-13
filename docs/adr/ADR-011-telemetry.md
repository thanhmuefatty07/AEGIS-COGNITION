# ADR-011: Runtime telemetry facade

Status: Accepted

Core contracts emit internal runtime/resource events through a small facade. OpenTelemetry
is the external semantic/protocol target, but unstable SDK types do not leak into the
pure domain module. Signals correlate mission, task, run, attempt, and lease IDs.
