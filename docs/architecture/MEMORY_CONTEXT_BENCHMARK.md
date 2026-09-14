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
uv run --locked --extra all --extra dev python scripts/memory_context_benchmark.py --output artifacts/memory-context-benchmark-local.json
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
