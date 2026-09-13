# Contributing

AEGIS-COGNITION is proprietary software. The repository may be inspected publicly, but
no contribution, patch, fork-based reuse, or derivative work is accepted without prior
written authorization from the copyright holder. Authorized contributions still use the
**Audit-First Workflow** and must arrive with evidence. See [LICENSE.txt](LICENSE.txt).

## Ground Rules

1. **Measure before change.** Run `cargo test --lib` and capture before/after
   for any refactor that touches `core/`, `bench/`, or `~`-tier hot paths.
2. **Tests are sacred.** Every currently supported Rust and Python test must
   keep passing. A change that breaks a single test is not a contribution —
   it is a regression report. Use the current checkout-bound evidence for
   suite counts; historical counts in archived reports are not a gate.
3. **Cryptographic invariants stay tight.** Do not weaken hash bindings,
   trust-level gates, or replay determinism without an explicit audit report.
4. **No behavior changes in cleanup commits.** Cleanup = code hygiene, doc sync,
   CI wiring. If you need a behavior change, open a separate change with its
   own rationale.

## Workflow

1. Pick an issue or open one describing the gap precisely with file:line
   evidence. Audit prompts with unverified "CRITICAL" claims get bounced.
2. Write an implementation plan under `docs/architecture/` when the change
   affects architecture, contracts, or release evidence.
3. Implement with the smallest possible diff.
4. Run:
   ```
   cargo test --locked --manifest-path core/rust/Cargo.toml --lib
   cargo clippy --all-targets --all-features -- -W unused_imports -W dead_code
   uv run --locked --extra all --extra dev python -m pytest -q
   ```
5. Append an entry to `CHANGELOG.md` under `[Unreleased]`.

## Layout

- `core/rust/` — Hot engine, memory plane, replay ledger. Changes here go through
  the rust-architect role.
- `core/python/` — Friendly gateway, Agent API. Keep 1-import dev experience.
- `aegis-plugins/` — External tooling. Each plugin has its own README.
- `artifacts/` — Audit evidence. Append-only; archive, do not delete.
- `docs/` — Developer-facing guides. Treat as curated, not exhaustive.

## Generated and historical material

- Keep disposable caches, local agent metadata, and generated benchmark output
  out of commits. Durable evidence belongs in the existing registry and
  architecture documentation paths.
- Historical records under `docs/archive/` are provenance only. Update the
  current authority documents when a contract or release status changes; do
  not silently rewrite archived reports.

## Questions?

Open an issue with `type:question` label.
