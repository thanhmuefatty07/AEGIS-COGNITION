# Agent operating rules

## Authority and scope

- Follow the user's current instructions and all higher-priority platform rules.
- Read [`docs/ENGINEERING_CONSTITUTION.md`](docs/ENGINEERING_CONSTITUTION.md) for
  engineering requirements and [`docs/WORKSPACE_HYGIENE.md`](docs/WORKSPACE_HYGIENE.md)
  for repository layout, naming, local data, and archival rules.
- Read any nested `AGENTS.md` that applies to files in your scope. When rules
  overlap, keep the more specific compatible rule; do not weaken a higher-level
  security, privacy, or correctness requirement.

## Work method

1. Inspect `git status` before editing. Preserve the user's existing changes and
   keep edits inside the declared task scope.
2. Trace the owning code or document, its callers/consumers, relevant tests,
   contracts, and privacy boundaries. Scale review and verification to risk.
3. Make the smallest complete change that preserves behavior and compatibility.
   Avoid speculative cleanup, duplicated policy, unrelated formatting, and
   machine-specific paths in tracked files.
4. Use the repository's required verification when authorized. Record the exact
   checks and results; never claim a check passed unless it ran successfully.
   If verification is unavailable or not authorized, state `NOT VERIFIED` and
   why.
5. Inspect the final diff and search for stale paths, accidental files, leaked
   local data, and unintended contract changes before reporting completion.

## Research, benchmark, and private data

- Keep current implementation guidance, user documentation, contracts,
  collectors, fixtures, and gate inputs where their consumers require them.
- Keep only concise, reviewed findings and decision-relevant evidence in the
  repository's existing architecture and `quality/registry/` authorities.
  After a research or benchmark result has been used and its findings recorded,
  move report-only documents, raw measurements, and superseded captures to the
  external private archive. Do not publish raw reports or local evidence.
- Before moving anything, search source, tests, workflows, docs, and manifests
  for consumers. Preserve required inputs, fixtures, current gate outputs, and
  reproducibility metadata until their consumers are closed or updated.
- Resolve the archive from `AEGIS_PRIVATE_ARCHIVE`, then the ignored local
  pointer `.local/private-archive.path`, or a path explicitly supplied by the
  user. Never fall back to a tracked project directory or silently create a
  replacement archive. If the location is unavailable, preserve the source and
  report the blocker.
- Archive by copying first, preserving relative source paths and applicable
  licenses/attribution, and recording source revision, date, command,
  environment, units, byte count, and SHA-256 where relevant. Verify every copy
  before removing its source. Do not archive secrets, credentials, or
  unnecessary personal data.
- Never put a developer's drive, username, checkout, archive, or temporary path
  in tracked files. A local ignored pointer is allowed. A filename change does
  not resolve copyright; preserve attribution and licensing for third-party
  material, and use descriptive stable names for project-owned files.

## Agents and parallel work

- Do not impose arbitrary fixed limits on agent count, duration, or available
  effort. When delegation is authorized and parallel work materially helps,
  use the available capacity for genuinely independent scopes.
- Give each agent explicit file ownership and a bounded deliverable. Avoid
  overlapping edits; integrate and review every result against the full diff and
  the same evidence standard as local work.

## Git and external changes

- Do not commit, push, rewrite published history, publish, or change an external
  service unless the user has authorized that specific action. Before any
  authorized publication, confirm private/local outputs are untracked and absent
  from every intended published ref; inspect workflow stdout/stderr and
  `upload-artifact` paths for raw reports and measurements as well. `.gitignore`
  does not remove previously tracked content, public Git history, workflow logs,
  or uploaded Actions artifacts.
- Preserve recoverable local work before any destructive cleanup or history
  operation. Do not discard dirty files, caches, fixtures, or generated state
  without evidence that they are disposable and outside active consumers.

## Background jobs

- Never poll a background job for status. Do not use `sleep`, `ps`, `pgrep`, or
  `top` to check whether it is still running.
- While a job runs, continue independent useful work. If no useful work remains,
  end the response and rely on the platform to wake you when new output is
  available; keep that update minimal.
