# ADR-008: Admission before spawn

Status: Accepted

Every executable work item declares a `ResourceRequest`. The Rust admission controller
either issues a `ResourceLease`, places work into a bounded queue, or rejects it with a
typed reason. This prevents unbounded spawning and keeps overload behavior explicit.
