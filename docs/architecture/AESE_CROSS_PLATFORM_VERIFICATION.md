# AESE cross-platform verification contract

AEGIS has three Tier-1 operating-system obligations: Ubuntu, Windows, and
macOS. A local run on one host, a Linux VM, or a passing smoke test does not
close the other two obligations.

The authoritative cross-platform lane is the `cross-platform-full-suite`
matrix in `.github/workflows/ci.yml`. Every lane checks out the same commit,
resolves the locked Python environment, and executes the same full portable
suite command:

```text
uv run --locked --extra all --extra dev python -m pytest tests core/python/tests.py -v -W error::DeprecationWarning
```

The portable suite includes deterministic provider protocol, discovery, and
authentication contracts in `tests/test_provider_contract_matrix.py`. Live
provider observations belong to the explicit `tests/live/` tier, whose modules
use names outside pytest's default test filename patterns. The scheduled
and manually dispatched `.github/workflows/provider-public-catalog-smoke.yml`
sets `AEGIS_TEST_PROVIDER_NO_KEY_SMOKE=1` and invokes
`tests/live/provider_public_catalog.py` through `suite_evidence.py`. All nine
built-in provider catalog/auth checks remain in that credential-free live
lane. Portable evidence does not establish current provider endpoint behavior;
that requires the separate live result.

Windows-specific junction, installer, and command-indirection checks live in
`tests/platform/windows.py`, outside pytest's default filename patterns. The
Windows lane explicitly runs this suite and validates its evidence separately
with the same failed, empty, skipped, filtered, timeout, and clean-worktree
requirements. The portable matrix and this Windows suite together retain the
applicable filesystem and packaging contracts; portable parity alone does not
establish those Windows-specific behaviors. Symlink checks remain in the
portable suite and require a runner that can actually create symbolic links.

Every workflow job that invokes project Python scripts establishes a supported
Python interpreter before the first invocation. Matrix jobs retain their
declared versions; other script jobs use the pinned stable Python version.
Project scripts may use syntax introduced in Python 3.14, so an unversioned
runner interpreter is not a valid bootstrap contract.

Each lane emits `aegis-suite-evidence-v1` with its commit, clean-worktree
state, command, toolchain, host observation, canonical runner label, counts,
timeout state, exit code, and output hashes. The separate
`cross-platform-evidence-gate` fails closed when any lane is missing, failed,
timed out, dirty, mislabeled, filtered, ignored, skipped, or has a different
count vector. A green matrix job without the evidence gate is not a portability
proof.

Suite evidence also records the requested and executed argument vectors and
the result source. The legacy `--command-fragment` option now requires the
complete command: every argument must match, and the display command must
decode to the recorded vector. A command embedded in an unrelated program's
arguments is insufficient. Python counts come from a fresh, bounded pytest
JUnit report; missing or malformed reports have no stdout fallback. Cargo
counts use native harness or nextest summary records. Arbitrary programs that
print pass counts remain `NOT_VERIFIED`. Older artifacts without this metadata
can still be read but cannot be promoted as current suite evidence.
Cargo commands with CLI `--config` overrides are also diagnostic-only: an
override can replace the test binary's execution with an arbitrary runner.
Repository and host runner configuration remain inside the trusted boundary.

These records assume the runner, checkout and test infrastructure are trusted.
Reports, hashes and command metadata are not independent attestations: code
with the same OS authority can forge them. Resistance to that threat requires
an independently controlled collector and execution boundary, and remains
`NOT VERIFIED` here. The temporary JUnit file is removed after collection; only
normalized counts, its size and digest are retained.

AESE uses this evidence as a platform anchor while it remains in `SHADOW`.
It may explain a smaller candidate set, but it cannot skip the retained
authoritative suite or promote a local result. Unknown, unmapped, dynamic,
security-critical, release-critical, or platform-sensitive changes widen to
the retained suite. Selective execution can be considered only after paired
legacy-versus-AESE campaigns on the same commit and workload establish
coverage and false-negative behavior with retained, reproducible evidence.

The GCP Ubuntu VM is useful for an additional independent Linux run, soak,
and external-environment checks. It cannot provide macOS evidence. macOS
evidence must come from a real macOS host or the pinned GitHub-hosted macOS
runner; for release claims, the hosted artifact and gate are the recorded
authority.

AESE corpus provenance follows the same boundary. Its `environment_hash` is
derived only from `project.requires-python` in `pyproject.toml`, so a corpus
generated on one supported OS remains verifiable on the others under the same
declared runtime contract. The provenance envelope retains the generating OS,
kernel release, machine architecture, and interpreter filename
under `host_observation`; those fields describe where the artifact was made and
are deliberately not used as a cross-host reproducibility key.

## Evidence interpretation

- `PROVEN`: the three hosted lanes and the aggregate gate passed for one
  commit.
- `FAILED`: at least one lane or the aggregate consistency check failed.
- `NOT VERIFIED`: a required lane or external witness has not run; it is never
  inferred as passing.

This contract establishes reported test execution parity for the declared suite. It does
not by itself prove production deployment, long-run durability, provider
behavior, hardware coverage, or release signing.
