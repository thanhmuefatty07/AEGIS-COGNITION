# Verification baseline

This document is a rendered view, not the evidence authority. Current evidence
is in [`evidence/current.json`](evidence/current.json) and is accepted only
when the consistency gate binds it to the checked-out `main` SHA.

## Historical pre-remediation baseline (immutable)

| Field | Value |
|---|---|
| Main SHA | `0ebef3e382fba167b76674b39c66f6301619087f` |
| Current fast CI | Run `32781549128`, success, exact SHA above |
| Current plugin CI | Run `32781549195`, success, exact SHA above |
| Current deep evidence | `NOT VERIFIED` until rerun after remediation |
| Current release evidence | `NOT VERIFIED` until rerun after remediation |
| Current external attestation | `NOT VERIFIED`; previous run was blocked by private-repository provider availability |
| Current default-member Rust count | 418/418 in the pre-remediation baseline; rerun required after final changes |
| Current Python local count | 89/89 in the pre-remediation baseline; exact CI lanes are authoritative |

The 409, 408, and 418 counts seen in older reports have different workspace
or checkout scopes. They are never combined into one global current count.
Every future count must include command, commit, timestamp, platform,
toolchain, discovered, passed, failed, ignored, and filtered fields in the
manifest.

## Historical evidence

The following runs are retained only as historical context and cannot close the
current release:

- `07101ce3c2df0fedeaa51ea6426a18bb55aa424f`: CI `32778221183`, plugin
  `32778221299`, deep `32778232964`, release `32779649368`.
- The previous release run built the wheel, SBOM, metadata, and uploaded the
  artifact, but provider-side attestation persistence was unavailable for the
  private user-owned repository. This is a `NOT VERIFIED` platform gap, not a
  successful attestation.

## Open closure work

See [`NOT_VERIFIED_REGISTRY.md`](NOT_VERIFIED_REGISTRY.md) for privileged OS
enforcement, H0/H1/H2 policy data, fuzz duration/corpus retention, Miri/ASan
scope, wheel parity, rollback/restore, external OTel export, branch
protection, and final release provenance.
