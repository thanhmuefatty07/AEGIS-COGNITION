# Workspace hygiene

This repository has one canonical implementation root: `C:\Users\ADMIN\AEGIS-COGNITION`.
The product name is `AEGIS-COGNITION`; the Python import package is
`aegis_cognition`. New implementation, tests, manifests, evidence, and scripts
must use those canonical names and paths.

## Allowed locations

- Source: `aegis_cognition/`, `core/`, `desktop/`, `schemas/`, and `aegis-plugins/`.
- Tests: `tests/`, `core/rust/tests/`, and the existing language-specific test roots.
- Durable documentation: `docs/`; historical material: `docs/archive/`.
- Local caches and disposable run data: `.local/cache/` and `.local/tmp/`.
- Reviewed machine evidence: the existing `quality/registry/` schemas. Do not
  create ad-hoc evidence folders or duplicate registry files.

Never create project or test directories directly under `C:\Users\ADMIN`, the
repository parent, or a shared user temp directory. A test that needs an explicit
temporary root must use a unique path such as
`.local/tmp/evaluator/20260912T120000Z-<run-id>` and must record the path in its
report. Concurrent pytest runs must never share a basetemp directory.

Pytest and Ruff caches are configured under `.local/cache/`. Build output such as
`.venv/` and `target/` is disposable machine state and must remain ignored. Do not
commit cache files, basetemp contents, logs, lock files, or one-off helper scripts.

## Naming and ownership

Use the existing language convention for new source (`snake_case` for Python and
Rust modules) and clear lower-case names for new documentation. Do not add names
such as `r2`, `r3`, `r4`, `current-final`, or timestamped sibling directories to
represent retries; put run identity, date, and revision in structured evidence
metadata instead. Do not use `Hermes`, `crypto`, `trading-bot`, or another product
name for AEGIS source or output. Hermes references remain only where a comparator
or historical research document explicitly requires that name.

Each agent owns a declared scope. Before editing, inspect `git status`; after
editing, report exact files, the reason each changed, the command and exit code,
and the next bounded action. Do not rewrite another agent's registry, generated
evidence, or source files. Do not commit or push until the coordinator has reviewed
the complete diff.

## Verification and CI

Run the smallest affected check first. Full suites, Rust workspace tests,
benchmarks, and GitHub Actions require a named gate, an expected artifact, a time
limit, and a stop condition. macOS/Linux checks belong in the existing GitHub
Actions workflow; do not create local macOS/Linux simulation folders or dispatch
repeated runs when no input changed.

When a check fails, preserve the first useful failure and change the approach.
Do not rerun indefinitely, create `-r2`/`-r3` copies, or report success from a
stale artifact. A stopped or unavailable external gate remains `NOT VERIFIED`.
