# Historical source ownership

This directory is a historical artifact, not an implementation tree.

- The authoritative implementation is the repository root, especially
  `core/rust`, `core/python`, `aegis_cognition`, `schemas`, and `scripts`.
- The tracked transformation report is retained as historical context only.
- Rust and website snapshots here preserve historical source; they are outside
  the Cargo workspace and current runtime. Do not develop them here.
- New code, tests, manifests, workflows, and operational scripts belong in the
  canonical implementation paths.
- The architecture fitness gate rejects recreation of either former nested
  project directory (`AEGIS-COGNITION` or `core/rust/AEGIS-COGNITION`).
