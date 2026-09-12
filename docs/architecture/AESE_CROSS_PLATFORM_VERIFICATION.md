# AESE cross-platform verification contract

AEGIS has three Tier-1 operating-system obligations: Ubuntu, Windows, and
macOS. A local run on one host, a Linux VM, or a passing smoke test does not
close the other two obligations.

The authoritative cross-platform lane is the `cross-platform-full-suite`
matrix in `.github/workflows/ci.yml`. Every lane checks out the same commit,
resolves the locked Python environment, and executes the same full command:

```text
uv run --locked --extra all --extra dev python -m pytest tests core/python/tests.py -v -W error::DeprecationWarning
```

Each lane emits `aegis-suite-evidence-v1` with its commit, clean-worktree
state, command, toolchain, host observation, canonical runner label, counts,
timeout state, exit code, and output hashes. The separate
`cross-platform-evidence-gate` fails closed when any lane is missing, failed,
timed out, dirty, mislabeled, filtered, ignored, skipped, or has a different
count vector. A green matrix job without the evidence gate is not a portability
proof.

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
derived only from the declared Python runtime contract (implementation and
version), so a corpus generated on one supported OS remains verifiable on the
others when the locked toolchain is the same. The provenance envelope retains
the generating OS, kernel release, machine architecture, and interpreter path
under `host_observation`; those fields describe where the artifact was made and
are deliberately not used as a cross-host reproducibility key.

## Evidence interpretation

- `PROVEN`: the three hosted lanes and the aggregate gate passed for one
  commit.
- `FAILED`: at least one lane or the aggregate consistency check failed.
- `NOT VERIFIED`: a required lane or external witness has not run; it is never
  inferred as passing.

This contract proves test execution parity for the declared suite. It does
not by itself prove production deployment, long-run durability, provider
behavior, hardware coverage, or release signing.
