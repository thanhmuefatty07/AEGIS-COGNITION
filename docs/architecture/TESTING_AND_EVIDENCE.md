# Testing and evidence process

The repository uses four evidence tiers aligned with the architecture plan:

1. Fast PR checks: formatting, strict Ruff/Pyright checks, default-member Rust
   tests through `cargo-nextest`, `cargo-deny`, focused Python tests,
   cross-language smoke, secret/lock/architecture gates.
2. Compatibility checks: standard CPython 3.14.7 production and standard
   CPython 3.15.0-rc.1 forward lanes, pinned Rust, MSRV/beta signals, and Tier-1
   operating systems when runners are available. Free-threaded Python is not a
   current support or CI lane.
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

The current repository retains scoped live evidence for the Linux cgroup-v2
kernel primitive, the Rust/Lab native-process seam, and Windows Job Object
controls. The Linux evidence is bound to one GCP Ubuntu 24.04/kernel run and a
clean probe SHA; it does not prove all Linux kernels or uncommitted workspace
changes. macOS live controls, H0/H1/H2 freeze data, fuzz campaigns, external
signed release attestation, and full OTel export remain open evidence. CI
definitions establish the repeatable path; they do not turn an unexecuted path
into proof.

The blocked-but-productive dispatch and one-owner/one-gate rules are defined in
[`AGENT_COORDINATION_PROTOCOL.md`](AGENT_COORDINATION_PROTOCOL.md).

## V3 benchmark observations

Paired collectors use the registered alternating AB/BA order and record the
order dispatched for each measured block. Missing orders, imbalanced orders,
orders that differ from the registered schedule, and counts that disagree
with the observed pairs are rejected before computing a comparison.
The `aegis-paired-v3-observations-v1` hash binds the ordered observations,
warmups, block count, protocol and environment. The protocol binds the
workload digest and the metric, including its unit. Paired records without
`artifact_hash_schema` use the legacy hash, which did not bind the observations.
Benchmark gate caches include the V3 evaluator in their input digest. Fresh
and cached reports must bind the stored protocol, environment, observation
digest and raw p-value to the comparison hash. A comparison from the legacy
hash format remains historical evidence.

Collectors sample the host before and after the timed workload. They retain
the one-minute load average where the OS exposes it, the Linux CPUFreq hardware
readings where the kernel provides them, and the time spent taking each
sample. The load threshold uses the host's logical CPU count. The frequency
check compares the median across matching policies before and after the run.
A frequency drop does not establish its cause. Missing required observations
produce `EXPLORATORY` results with an `UNKNOWN` contamination verdict. An
exploratory result may retain estimates for diagnosis. It cannot qualify a
gate or establish a practical improvement after multiple-comparison
correction. These host observations do not attest filesystem or sandbox
isolation.

The Rust benchmark library's best-effort environment capture leaves the two
load and frequency observations unset. Its clean-environment gate rejects
unavailable observations. Legacy numeric observations are still readable.
