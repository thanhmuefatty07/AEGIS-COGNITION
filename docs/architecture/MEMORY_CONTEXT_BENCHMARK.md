# Memory context benchmark

This benchmark measures the bounded Memory Agent context path against an
unbounded baseline. It is provider-neutral and performs no network calls.

The baseline includes every synthetic workspace path or every authorized
recalled record. The bounded path uses the production workspace path selector
and `ContextCompiler` with its configured token budget. Both sides use the
repository's `estimated-byte-heuristic-v1` accounting, so the result is a
reproducible estimate rather than a claim about a specific provider tokenizer.

Each run records the commit, operating system, Python version, workload cases,
baseline tokens, bounded tokens, saved tokens, reduction percentage, selection
determinism, and mandatory-source retention. It also runs explicit negative
probes for owner-scope isolation and stale mandatory evidence, a relevance
recall oracle, a token-budget invariant, and a Unicode accounting consistency
check against the production compiler. The checked-out workspace case uses the
real repository file list without exporting file contents. The benchmark
intentionally uses synthetic memory content so private source and conversation
data never leaves the machine.

Run it locally with:

```text
uv sync --locked --extra all --extra dev --no-install-project
uv run --no-project --no-sync --locked --extra all --extra dev python scripts/memory_context_benchmark.py --output artifacts/memory-context-benchmark-local.json
```

GitHub Actions repeats the same command on Ubuntu, Windows, and macOS and
retains one JSON artifact per runner. A VM run is an independent Linux sample;
it is useful for environment confirmation but does not replace the hosted
three-platform matrix.

The benchmark output separates quantitative cases from oracle-only cases so
non-token checks cannot inflate the reduction number. A regression test also
executes the same harness to prevent the evidence script from silently drifting
or reporting an invalid aggregate.

Interpret reductions only for the declared workload. The measurement does not
prove answer-quality preservation, end-to-end provider cost, network latency,
or universal savings for arbitrary repositories. Those require paired
provider-specific workloads and a separate quality evaluation.

## Recorded cross-platform evidence

At commit `0dc5b7d53c87a5e9a7a9becd038d4e37523bd851`, the benchmark workflow
completed successfully on `ubuntu-latest`, `windows-latest`, and `macos-14` in
[GitHub Actions run 34831605691](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34831605691).
Each runner produced the JSON artifact defined above.

The regression test `tests/test_memory_context_benchmark.py::test_memory_context_benchmark_is_self_consistent`
also passed in the full Python jobs on all three hosted runners in that CI
attempt. The umbrella CI run was still red because 11 unrelated AESE registry
and recorded-corpus drift tests failed; those failures do not exercise this
benchmark and remain a separate repository baseline issue.

The same commit was run once on the existing GCP Compute Engine VM
`aegis-test-linux-02` in `asia-southeast1-b` (Ubuntu 24.04, Python 3.14.7).
It returned `status: PASS`, with 8 quantitative cases and 5 oracle cases;
the aggregate was 29,633 estimated baseline tokens versus 12,358 bounded
tokens (58.296% for that checkout). The VM was stopped immediately afterward
and verified `TERMINATED`.
