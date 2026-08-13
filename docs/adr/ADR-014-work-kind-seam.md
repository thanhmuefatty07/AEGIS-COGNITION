# ADR-014: Future Agent work kind

Status: Accepted

`WorkKind::Agent` is reserved as a future consumer of the same task, capability,
resource, deadline, cancellation, and evidence contracts. Subagent topology and
planning algorithms are intentionally deferred until the single-node runtime is
measured and stable.
