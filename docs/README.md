# Documentation hierarchy

The durable architecture and release evidence authority lives under
`docs/architecture/`:

Workspace and agent file hygiene is defined in
[`WORKSPACE_HYGIENE.md`](WORKSPACE_HYGIENE.md).

- `evidence/current.json` is the machine-readable evidence template; CI emits
  a checkout-bound artifact with the exact SHA.
- `VERIFICATION_INDEX.md` is the entry point and authority rule.
- `GT96_TRACEABILITY.md` is the requirement-level implementation/test matrix.
- `NOT_VERIFIED_REGISTRY.md` retains every open external, privileged, or
  unavailable-platform closure item.
- `VERIFICATION_BASELINE.md` and `TRACEABILITY.md` are rendered/history views;
  older run IDs are explicitly historical.
- `adr/` contains durable decisions; `crystallized/` contains design notes and
  must not be read as performance or production proof.

Historical reports and former nested project trees now live in
[the archive](archive/README.md). The archive records previous work; current
implementation and release authority remain in the canonical paths above.
