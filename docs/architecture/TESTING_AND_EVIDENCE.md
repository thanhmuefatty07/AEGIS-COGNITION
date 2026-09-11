# Testing and evidence process

The repository uses four evidence tiers aligned with the architecture plan:

1. Fast PR checks: formatting, strict Ruff/Pyright checks, default-member Rust
   tests through `cargo-nextest`, `cargo-deny`, focused Python tests,
   cross-language smoke, secret/lock/architecture gates.
2. Compatibility checks: production Python, forward CPython, free-threaded
   experimental lanes, pinned Rust, MSRV/beta signals, and Tier-1 operating
   systems when runners are available.
3. Deep checks: full-workspace nextest, cargo-deny, LLVM coverage, Miri/
   sanitizers, fuzz/property tests, dependency audit, replay chaos, stress, and
   Wasmtime adversarial cases. This is where POC members are exercised.
4. Release checks: clean tag build, clean-environment install, SBOM/provenance,
   signed attestation, vulnerability scan, compatibility/rollback, and restore
   or replay drill.

Every report must record the commit, dirty-worktree status/fingerprint,
host/runner, OS, toolchain, dependency lock state, workload/sample count, and
whether the result is `PROVEN`,
`MEASURED`, `ASSUMED`, or `NOT VERIFIED`. A local result must never be relabeled
as a cross-platform or H0/H1/H2 result.

Suite evidence also records one `owner_id`, one `gate_id`, an `attempt_id`, and a
deterministic `run_key`. The consistency gate propagates `PROVEN` only when the
exit code, failure count, output digest, and provenance are all present; missing
ownership or incomplete output remains `NOT VERIFIED`. This supports one-owner /
one-gate coordination and lets the coordinator detect a blocked lane being
mistaken for a completed or duplicate run; the run key is an identity signal,
not a scheduler lock.

All workflow suite commands run through the evidence wrapper, and every hosted
job declares a positive `timeout-minutes` bound. The wrapper applies a command
deadline and bounded descendant cleanup; workflow-level concurrency prevents
overlapping runs within each lane. A timeout or cancelled run remains
incomplete evidence and cannot be promoted by the consistency gate.

`docs/architecture/evidence/current.json` is the tracked schema/template. The
workflow materializes a checkout-bound `artifacts/evidence/current.json` and
the consistency gate validates `EvidenceSHA == git rev-parse HEAD`. Remote run
IDs and artifact URLs are filled only after the run completes; old run IDs are
kept under `historical_evidence`, never copied into a current row.

The current repository intentionally keeps privileged OS enforcement, macOS
live controls, H0/H1/H2 freeze data, fuzz campaigns, external signed release
attestation, and full OTel export as open evidence. CI definitions establish
the repeatable path; they do not turn an unexecuted path into proof.

The blocked-but-productive dispatch and one-owner/one-gate rules are defined in
[`AGENT_COORDINATION_PROTOCOL.md`](AGENT_COORDINATION_PROTOCOL.md).
