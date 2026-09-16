# AEGIS architecture

This directory records the implementation baseline for the modular-monolith
architecture described in `AEGIS_LAB_RUNTIME_MASTER_PLAN.md`.

The current runtime direction is intentionally conservative:

- Rust owns authoritative task, resource, capability, cancellation, and evidence transitions.
- Python owns intent interpretation, provider integrations, and semantic proposal generation.
- Cross-language calls use coarse, versioned contracts rather than per-field scheduler mutations.
- CPU-only execution is mandatory; accelerator adapters are optional.
- OS enforcement is reported by capability level. Unsupported controls are never advertised as hard limits.

The foundation convergence pass makes ownership executable: the exact Rust
compiler channel comes from `rust-toolchain.toml`, production fast gates use
resolver 3 `default-members`, the public Python Agent is a facade over
application/config/infrastructure modules, and the compatibility gateway
delegates provider/evidence/learning responsibilities to `core/python/aegis/`.

The current closure slice includes the Rust resource contract, fenced runtime,
bounded execution primitives, and opt-in Linux/Windows OS adapters. It still
does not certify production readiness, cross-host benchmark superiority, or
that a platform adapter is active merely because it is compiled.

The product-facing platform policy is in
[`PORTABILITY.md`](PORTABILITY.md). The initial reference environment is Ubuntu
LTS x86_64 with glibc, systemd, and cgroup v2. Windows and macOS remain
separately scoped: Windows Job Object evidence is partial, while macOS resource
control is cooperative/measurement-only until live evidence exists.

## Current implementation map

| Concern | Implementation | Evidence status |
|---|---|---|
| Hardware profile | `core/rust/src/resource.rs` | Rust unit tests + local probe |
| Resource request/lease | `core/rust/src/resource.rs` | Rust unit tests |
| Bounded admission | `core/rust/src/resource.rs` | Rust unit tests |
| Execution lane limits | `core/rust/src/resource.rs`, `core/rust/src/execution.rs` | bounded executor unit tests; stress fairness NOT VERIFIED |
| Accelerator seam | `core/rust/src/resource.rs`, `core/rust/src/execution.rs` | optional lane fails closed when no device is advertised; vendor backend NOT VERIFIED |
| Python boundary | `core/rust/src/ffi.rs`, `aegis_cognition/runtime.py` | opaque token tests; native wheel build required |
| OS resource control | `core/rust/src/resource_platform.rs` | Linux cgroup primitive plus native Rust/Lab seam live-verified on one GCP Ubuntu 24.04 host/clean probe SHA; current uncommitted changes and cross-kernel coverage remain open; Windows and macOS evidence are scoped separately |
| GT96 authority contracts | `core/rust/src/gt96.rs`, `GT96_TRACEABILITY.md`, `GT96_TRACEABILITY_DETAIL.md` | direct Rust contract tests; full runtime integration and final-SHA closure remain NOT VERIFIED |
| Communication payload matrix | `scripts/communication_payload_benchmark.py`, `core/rust/src/ipc.rs` | local 64B–16MiB measured matrix; copy/zero-copy scope remains explicit |
| Platform closure harnesses | `scripts/linux_cgroup_live_probe.py`, `windows_job_object_live_probe.py`, `macos_capability_probe.py` | platform-specific live/capability evidence retained separately; unavailable lanes NOT VERIFIED |
| End-to-end scheduler/TaskLedger integration | `core/rust/src/runtime.rs`, `task_ledger.rs` | lease/state/attempt integration tests |
| Policy provenance | `ResourcePolicy`, benchmark protocol | ASSUMED until H0/H1/H2 retained outputs |
| Wasmtime migration | `docs/architecture/WASMTIME_MIGRATION.md` | static/security gate + Rust tests required |
| Runtime telemetry facade | `core/rust/src/telemetry.rs`, `schemas/runtime-telemetry-v1.json` | bounded, correlated, non-authoritative sink tests; OTel export NOT VERIFIED |
| Python runtime telemetry | `aegis_cognition/observability.py`, `metrics.py`, `core/rust/src/ffi.rs` | bounded correlation-chain tests; exporter/backend availability NOT VERIFIED |
| Python application boundaries | `aegis_cognition/agent.py`, `application.py`, `config.py`, `infrastructure.py` | architecture fitness + strict Ruff/Pyright + regression tests |
| Gateway responsibility boundaries | `core/python/aegis/contracts.py`, `provider.py`, `evidence.py`, `learning.py` | architecture fitness + full Python bridge regression suite |
| Local subagent coordination | `aegis_cognition/subagents.py`, `core/rust/src/agent_coordination.rs`, `schemas/agent-*.json` | native graph validation + bounded TaskGroup/resource-lock tests; cross-platform CI execution pending |
| Public research adapters | `aegis_cognition/research_adapters.py` | Reddit RSS live read-only smoke + deterministic adapter tests; X requires an app-only token |
| Exact code reuse | `aegis_cognition/code_reuse.py`, `core/python/aegis/code_intelligence.py` | snapshot/hash/license/path-bound tests; public-source retrieval and model-training lookup are not enabled |
| Memory Agent workspace projection | `core/python/aegis/code_intelligence.py`, `desktop/src/workspace_graph.ts`, `desktop/src/WorkspaceGraphView.tsx` | revision-bound read-only graph contract; desktop build PASS; source-proof navigation and memory-linked graph NOT VERIFIED |

The durable standards mapping is in
[`STANDARDS_APPLICABILITY.md`](STANDARDS_APPLICABILITY.md). The testing tiers
and evidence-label rules are in
[`TESTING_AND_EVIDENCE.md`](TESTING_AND_EVIDENCE.md). The current evidence
entry point is [`VERIFICATION_INDEX.md`](VERIFICATION_INDEX.md); its
machine-readable template is [`evidence/current.json`](evidence/current.json),
and unresolved closure work is retained in
[`NOT_VERIFIED_REGISTRY.md`](NOT_VERIFIED_REGISTRY.md).

## Local-only evidence

Raw benchmark runs, empirical research records, generated architecture packs,
and host-specific probe outputs are private project material. They are retained
in the local cleanup archive and are intentionally excluded from the tracked
tree and Git history. The collector sources remain available for reproducible
local generation:

- `empirical-research/collect.py`
- `evidence-pack/collect.py`
- `evidence-pack/final_closure_collect.py`
- `design-closure/design_closure_collect.py`

The collectors write generated outputs to a local-only evidence directory; an
output is not architecture authority until it is reconciled with the canonical
plan, contracts, ADRs, and tracked verification registry.

The public decision input for the local-first Memory Agent remains
[`MEMORY_AGENT_LIVING_WORKSPACE_RESEARCH.md`](MEMORY_AGENT_LIVING_WORKSPACE_RESEARCH.md):
current decision input for the local-first Memory Agent, token-bounded context,
and evidence-backed project graph inspired by external research.
- [`MEMORY_AGENT_LIVING_WORKSPACE_RESEARCH.md`](MEMORY_AGENT_LIVING_WORKSPACE_RESEARCH.md):
  current decision input for the local-first Memory Agent, token-bounded
  context, and evidence-backed project graph inspired by external research.

The subagent decision and protocol are recorded in
[`AGENT_SUBAGENT_RUNTIME_DESIGN.md`](AGENT_SUBAGENT_RUNTIME_DESIGN.md) and
[`ADR-016-subagent-coordination.md`](../adr/ADR-016-subagent-coordination.md).

These generated outputs are evidence views, not hand-maintained implementation
authority. A historical path or label inside a retained local snapshot is
provenance unless the current verification index explicitly promotes it.
