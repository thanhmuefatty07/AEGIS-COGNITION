# Current verification index

This is the human-readable entry point for the machine-readable evidence
source of truth at [`evidence/current.json`](evidence/current.json). The
manifest binds each current suite and evidence item to the exact checked-out
commit. Historical reports may retain older SHAs, but they are not current
evidence and must not be used to close a requirement.

The tracked JSON is a non-self-referential template. CI materializes
`artifacts/evidence/current.json` with the exact checkout SHA and the gate
validates that artifact (`generated from checkout HEAD`). When retained suite
artifacts exist under `artifacts/suites`, the materializer copies their exact
command, commit, timestamp, platform, toolchain, and counts into the
checkout-bound manifest; absent or stale artifacts remain `NOT VERIFIED`.

## Status vocabulary

- `PROVEN`: deterministic implementation/test or external CI evidence directly demonstrates the stated scope.
- `MEASURED`: retained benchmark output with host, workload, toolchain, and sample metadata; not a portability or policy-freeze claim.
- `SOURCE-BACKED`: evidence is backed by an authoritative external source and its retrieval is recorded.
- `IMPLEMENTED / NOT VERIFIED`: implementation exists, but the required live, cross-platform, privileged, or release evidence is open.
- `ASSUMED`: explicit default or policy input; never equivalent to measured evidence.
- `NOT IMPLEMENTED`: no production implementation exists; the closure procedure is recorded in the requirement matrix.
- `NOT VERIFIED`: the implementation or test path exists, but the required evidence has not been retained.

## Authority rules

1. `current.json` is the only current evidence source of truth.
2. A current `PROVEN`, `MEASURED`, or `SOURCE-BACKED` row must carry the final commit SHA and run/artifact reference; local proof rows use `source_artifact` when no hosted run ID exists.
3. Suite counts always include command, commit, timestamp, platform, toolchain, discovered, passed, failed, ignored, and filtered fields.
4. Historical counts and reports remain useful context only when explicitly labeled `historical`.
5. The consistency gate runs in CI and release workflows and rejects stale current evidence.

The complete GT96 requirement mapping is [`GT96_TRACEABILITY.md`](GT96_TRACEABILITY.md),
with the row-level evidence fields defined in
[`GT96_TRACEABILITY_DETAIL.md`](GT96_TRACEABILITY_DETAIL.md). The P0/P1/P2
remediation lanes are machine-readable in `current.json` under
`remediation_requirements`; unresolved platform and provider gaps are tracked in
[`NOT_VERIFIED_REGISTRY.md`](NOT_VERIFIED_REGISTRY.md).
