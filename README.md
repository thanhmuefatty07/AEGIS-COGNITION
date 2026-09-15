# AEGIS-COGNITION

> Evidence-first runtime for AI agents: durable memory, replayable execution,
> and bounded resource coordination.

[![CI](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/workflows/ci.yml/badge.svg)](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/workflows/ci.yml)
[![Python 3.14–3.15](https://img.shields.io/badge/python-3.14--3.15-blue.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-toolchain-orange.svg)](https://www.rust-lang.org/)
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](LICENSE.txt)

AEGIS-COGNITION combines a small Python-facing agent API with a Rust-owned
authority layer. The design keeps intent and provider integration easy to use
while making task admission, resource limits, cancellation, evidence, and
replay explicit and auditable.

## What it provides

- **Durable evidence:** hash-bound records and append-only replay boundaries
  make important transitions reconstructable.
- **Bounded execution:** typed resource contracts, admission leases, deadlines,
  cancellation, and capability-aware platform controls.
- **Clear trust boundaries:** Wasmtime sandboxing, fail-closed policy checks,
  and explicit `NOT VERIFIED` states prevent unsupported claims from becoming
  runtime authority.
- **A practical developer surface:** a thin `Agent` facade, CLI, provider
  adapters, browser integration seams, and a Rust plugin/skill SDK.
- **Cross-platform verification:** the same test contract runs on native
  Ubuntu, Windows, and macOS GitHub-hosted runners, with deeper Linux evidence
  available on a persistent test host.

## Memory context efficiency

The recorded cross-platform benchmark shows a **58.296% token reduction** on
Ubuntu, Windows, and macOS. In the 593-file workspace case, the reduction
reached **93.874%**. See the [benchmark report](docs/architecture/MEMORY_CONTEXT_BENCHMARK.md)
and [GitHub Actions evidence](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34831605691).

## Quick start

The repository uses a locked `uv` environment and a pinned Rust toolchain.

```bash
git clone https://github.com/thanhmuefatty07/AEGIS-COGNITION.git
cd AEGIS-COGNITION
uv python install 3.14.7
uv sync --locked --extra all --extra dev
uv run --locked --extra all --extra dev aegis --help
```

Run the complete Python suite locally:

```bash
uv run --locked --extra all --extra dev python -m pytest -q
```

To call an external model provider, copy `.env.example` to `.env` and add a
key for an approved provider. `.env` is local-only and must never be committed.

## Python API

```python
from aegis_cognition import Agent, run

result = Agent(task="Summarize the supplied research notes").run()
print(result.output)

quick_result = run("Explain the evidence recorded for this task")
print(quick_result.output)
```

Development tasks are automatically prepared for AESE verification. Provide an
explicit expected behavior before source work begins:

```python
result = Agent(
    "Implement the bounded verification lane",
    aese_expected_behavior="The lane records a hash-bound Lab receipt",
    aese_project_root=".",
).run()
```

AESE uses the existing Lab execution path for its local fast lane. Results are
provisional while `EVIDENCE_PROMOTION=DISABLED`; `aese=False` or
`aese_auto=False` disables the automatic development-task entry point.

Provider credentials and browser access are explicit configuration choices;
examples do not imply that external services or live browsing are enabled.

## Trust levels

| Level | Evidence behavior | Intended use |
|---|---|---|
| `DEV` | Hash-only, degraded operation allowed | Local development |
| `STAGING` | Hash and replay evidence, gaps recorded | CI and integration work |
| `PROD` | Full evidence chain, fail-closed policy | Future production deployment |

## Architecture

```text
             task intent
                  │
          ┌───────▼────────┐
          │ Python facade  │  provider and semantic adapters
          └───────┬────────┘
                  │ versioned contracts
          ┌───────▼────────┐
          │ Rust authority │  admission, limits, evidence, replay
          └───────┬────────┘
                  │
       ┌──────────▼──────────┐
       │ bounded execution   │  sandbox and platform capability seams
       └──────────┬──────────┘
                  │
       append-only evidence and replay archive
```

The detailed ownership map and current status live in
[`docs/architecture/README.md`](docs/architecture/README.md). The canonical
runtime plan is [`docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md`](docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md).

## Verification status

The repository publishes evidence rather than broad quality claims. Current
verification is visible in [GitHub Actions](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions)
and governed by:

- [`docs/architecture/VERIFICATION_INDEX.md`](docs/architecture/VERIFICATION_INDEX.md)
- [`docs/architecture/TESTING_AND_EVIDENCE.md`](docs/architecture/TESTING_AND_EVIDENCE.md)
- [`docs/architecture/NOT_VERIFIED_REGISTRY.md`](docs/architecture/NOT_VERIFIED_REGISTRY.md)
- [`docs/architecture/evidence/current.json`](docs/architecture/evidence/current.json)

The current release posture remains **production readiness not claimed**. Local
or CI measurements are scoped to their host and workload; they do not become
universal performance, hardware, or security guarantees.

## Documentation

- [Quick start](docs/quickstart.md)
- [API reference](docs/api/)
- [Tutorials](docs/tutorials/)
- [Integrations](docs/integrations/)
- [Troubleshooting](docs/troubleshooting/common-errors.md)
- [Architecture and contracts](docs/architecture/)
- [Architecture decisions](docs/adr/)
- [Repository layout and hygiene](docs/WORKSPACE_HYGIENE.md)
- [Historical records](docs/archive/README.md)

## License and use

AEGIS-COGNITION is proprietary software. All rights are reserved. The public
repository is available for inspection and GitHub service operation only. No permission is
granted for execution, copying, modification, distribution,
deployment, evaluation, research, or commercial use without prior written
authorization from the copyright holder. See [LICENSE.txt](LICENSE.txt) for the
complete terms.

## Project status

- Product name: **AEGIS-COGNITION**
- Python package: `aegis_cognition`
- Rust authority: pinned by `rust-toolchain.toml`
- Release posture: **evidence-gated; production readiness not claimed**

[Website](https://aegis-cognition.ai) · [Documentation](https://docs.aegis-cognition.ai) · [GitHub](https://github.com/thanhmuefatty07/AEGIS-COGNITION)
