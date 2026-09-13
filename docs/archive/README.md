# Historical archive

Reports previously scattered across the repository root are in `reports/`.
The former `AEGIS-COGNITION/` and `core/rust/AEGIS-COGNITION/` trees are in
`legacy/`. Historical planning is in `planning/`; external comparator research
is kept under `scripts/research/comparators/external/`.
These documents and source snapshots do not establish current release readiness.
Existing evidence JSON retains original paths and hashes as historical records.
Historical licensing or business-model wording in this archive is superseded and
does not grant permission. The only current project terms are in the root
[`LICENSE.txt`](../../LICENSE.txt).

Current implementation lives in `aegis_cognition/`, `core/`, and `desktop/`.
`aegis_cognition` is the Python import package, so its name is required by callers.
The root Python/Rust manifests, lockfiles, toolchain pins, installers, README,
contribution guide, changelog, and CI directory remain development contracts.
Hermes baseline scripts name the external comparator they measure; changing that
name to AEGIS would misidentify the experiment.

Local editor, agent, runtime, cache, and generated data belongs under `.local/`
or the ignored `.aegis/` runtime directory; Git and Docker exclude them.
Pytest and Ruff caches use `.local/cache/`. Concurrent test runs should each use
a distinct `.local/tmp/<run-id>` if an explicit pytest basetemp is needed; pytest
can delete the selected basetemp, so never point it at a shared directory,
the repository root, or a user directory. Default system temporary directories
remain acceptable when no explicit basetemp is supplied.

Prior cleanup quarantine is retained in `.local/quarantine/cleanup-20260909/`.
Its manifests describe original locations before this relocation. Moving the
quarantine groups files for recovery; it does not reclaim disk space. Retention
and permanent deletion require checking which evidence remains useful.
