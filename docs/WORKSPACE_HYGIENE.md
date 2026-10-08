# Workspace hygiene

This repository has one canonical implementation root: the repository root.
The product name is `AEGIS-COGNITION`; the Python import package is
`aegis_cognition`. New implementation, tests, manifests, evidence, and scripts
must use those canonical names and paths. Do not encode a developer's local
drive, username, or checkout path in tracked files.

## Allowed locations

- Source: `aegis_cognition/`, `core/`, `desktop/`, `schemas/`, and `aegis-plugins/`.
- Tests: `tests/`, `core/rust/tests/`, and the existing language-specific test roots.
- Durable documentation: `docs/`; historical material: `docs/archive/`.
- Local caches and disposable run data: `.local/cache/` and `.local/tmp/`.
- Reviewed machine evidence: the existing `quality/registry/` schemas. Do not
  create ad-hoc evidence folders or duplicate registry files.
- `artifacts/` is ignored local gate output and benchmark input/output. Keep
  active gate inputs, fixtures, and evidence required by consumers in place;
  preserve result files until review and any required findings have been
  promoted to the existing documentation or registry authority.

Never create project or test directories directly under the repository parent or
a shared user temp directory. A test that needs an explicit
temporary root must use a unique path such as
`.local/tmp/evaluator/20260912T120000Z-<run-id>` and must record the path in its
report. Concurrent pytest runs must never share a basetemp directory.

Pytest and Ruff caches are configured under `.local/cache/`. Build output such as
`.venv/` and `target/` is disposable machine state and must remain ignored. Do not
commit cache files, basetemp contents, logs, lock files, or one-off helper scripts.

## Private research archive

Report-only research documents, completed raw benchmark runs, and superseded
visual or audit captures belong in the private archive outside the repository
after their decision-relevant findings have been recorded. Do not use
`docs/archive/` for raw reports or large measurement dumps. Keep source code,
collectors, fixtures, current gate inputs, and evidence still consumed by a
workflow in their required locations.

Before moving an item, search code, tests, workflows, documentation, and
manifests for references. Copy it to the private archive first, preserve its
relative path and third-party license/attribution, and record enough provenance
to reproduce or audit it: source revision, date, command, environment, units,
size, and SHA-256 as applicable. Verify the copy before removing the workspace
copy. Preserve prior data when a consumer is unclear.

Resolve the archive from the local `AEGIS_PRIVATE_ARCHIVE` environment variable,
the ignored `.local/private-archive.path` file, or a path explicitly supplied by
the user. Do not put a local absolute path in tracked files, write a fallback
archive inside the repository, or silently create a new archive if the known
location is missing. If no valid destination is available, leave the source in
place and report that archival is pending. Never place credentials, secrets, or
unnecessary personal data in an archive.

Keep concise validated results and their limits in the canonical architecture
or `quality/registry/` records. Raw reports remain private: do not print them in
public workflow logs or upload them as GitHub Actions artifacts. A `.gitignore`
rule protects untracked local files only; check tracked files, workflow output,
artifact-upload paths, and intended published refs separately before publication.

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
the complete diff. When delegation is authorized and independent work benefits
from parallel execution, use the available capacity without arbitrary agent-count,
duration, or effort caps; give each agent non-overlapping file ownership and review
integrated results.

## Verification and CI

Run the smallest affected check first. Full suites, Rust workspace tests,
benchmarks, and GitHub Actions require a named gate, an expected artifact, a time
limit, and a stop condition. macOS/Linux checks belong in the existing GitHub
Actions workflow; do not create local macOS/Linux simulation folders or dispatch
repeated runs when no input changed.

When a check fails, preserve the first useful failure and change the approach.
Do not rerun indefinitely, create `-r2`/`-r3` copies, or report success from a
stale artifact. A stopped or unavailable external gate remains `NOT VERIFIED`.

Do not poll background jobs. Continue independent useful work while they run; if
none remains, end the response and wait for the platform's completion signal.
