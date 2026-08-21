# Testing and evidence process

The repository uses four evidence tiers aligned with the architecture plan:

1. Fast PR checks: formatting, lint/type checks, focused Rust and Python tests,
   cross-language smoke, secret/lock/architecture gates.
2. Compatibility checks: production Python, forward CPython, free-threaded
   experimental lanes, pinned Rust, MSRV/beta signals, and Tier-1 operating
   systems when runners are available.
3. Deep checks: Miri/sanitizers, fuzz/property tests, dependency audit, replay
   chaos, stress, and Wasmtime adversarial cases.
4. Release checks: clean tag build, clean-environment install, SBOM/provenance,
   signed attestation, vulnerability scan, compatibility/rollback, and restore
   or replay drill.

Every report must record the commit, host/runner, OS, toolchain, dependency
lock state, workload/sample count, and whether the result is `PROVEN`,
`MEASURED`, `ASSUMED`, or `NOT VERIFIED`. A local result must never be relabeled
as a cross-platform or H0/H1/H2 result.

The current repository intentionally keeps privileged OS enforcement, macOS
live controls, H0/H1/H2 freeze data, fuzz campaigns, external signed release
attestation, and full OTel export as open evidence. CI definitions establish
the repeatable path; they do not turn an unexecuted path into proof.
