# Documentation hierarchy

Current implementation and release authority is separated from user guidance,
design notes, and historical records. The root `README.md` remains the public
repository entry point.

## Current authority

- [`architecture/`](architecture/): implementation baseline, contracts, evidence,
  verification, and deployment-readiness status.
- [`architecture/VERIFICATION_INDEX.md`](architecture/VERIFICATION_INDEX.md):
  evidence authority and status rules.
- [`architecture/evidence/current.json`](architecture/evidence/current.json):
  machine-readable evidence template.
- [`architecture/NOT_VERIFIED_REGISTRY.md`](architecture/NOT_VERIFIED_REGISTRY.md):
  open external, privileged, or unavailable-platform closure items.
- [`adr/`](adr/): durable architecture decisions.
- [`WORKSPACE_HYGIENE.md`](WORKSPACE_HYGIENE.md): repository layout, ownership,
  naming, and disposable-state rules.
- [`ENGINEERING_CONSTITUTION.md`](ENGINEERING_CONSTITUTION.md): normative
  engineering policy for this repository.

## User guidance

- [`quickstart.md`](quickstart.md): installation and first-run guide.
- [`api/`](api/): API and CLI references.
- [`tutorials/`](tutorials/): task-oriented guides.
- [`integrations/`](integrations/): provider and plugin integration notes.
- [`troubleshooting/`](troubleshooting/): common failures and remedies.
- [`crystallized/`](crystallized/): design notes, not release or performance proof.

## Historical records

[`archive/`](archive/README.md) contains superseded reports, plans, source
snapshots, and external research. Historical records do not override the
current authority paths above.
