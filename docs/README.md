# Documentation hierarchy

The durable architecture and release evidence authority lives under
`docs/architecture/`:

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

Root-level reports and the nested `AEGIS-COGNITION/` subtree are historical
artifacts. New implementation, tests, workflows, and evidence must be added
to the canonical root paths, not to that nested subtree.
