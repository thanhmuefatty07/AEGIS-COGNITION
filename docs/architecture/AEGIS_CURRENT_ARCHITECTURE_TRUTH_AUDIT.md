# AEGIS-COGNITION — Forensic Architecture & Codebase Truth Audit

**Audit date:** 2026-08-28  
**Repository:** `C:\Users\ADMIN\AEGIS-COGNITION`  
**HEAD:** `f9645caf6d17cee2023d52183990ffcf8317e456`  
**Branch:** `main` (`origin/main`: ahead 10, behind 0)  
**Audit mode:** read-mostly; no production code, schema, workflow, or generated evidence was changed. The only new file is this audit artifact.  
**Evidence labels:** `PROVEN`, `MEASURED`, `SOURCE-BACKED`, `INFERRED`, `ASSUMED`, `UNKNOWN`, `NOT VERIFIED`.

## 1. Executive Truth Summary

AEGIS is not currently a single, fully authoritative laboratory runtime. It is a large hybrid repository with:

1. a public Python facade (`aegis_cognition`);
2. a second compatibility Python package and CLI under `core/python`;
3. a broad Rust crate (`aegis-nerve`) exposed through PyO3 and used for native admission, replay, resource, evidence, and runtime primitives;
4. a newly added but untracked Lab implementation (`aegis_cognition/lab.py`, `core/rust/src/lab.rs`, Lab tests and plans);
5. plugins, proof-of-concept crates, scripts, deployment manifests, and several historical plans;
6. an evidence/registry system that is intentionally fail-closed but is currently invalid for the checkout because its machine manifest still contains the placeholder `CHECKOUT_HEAD` instead of the actual 40-character SHA.

The intended direction is a Lab above explicit Execution Cells: research/search-as-code, browser interaction, experiments, unit-aware observations, claims/hypotheses, falsification, benchmarks, replay, and a dossier. The current implementation approximates this design, but authority is split: Python still owns a mutable Lab projection and lifecycle orchestration while Rust `LabController` admits and validates many events. This is a useful safety boundary, not proof that every side effect is controlled by one native authority.

The most decisive blockers are:

- **Current-tree truth is not the committed truth.** There are 58 modified tracked files and 17 untracked files. The principal Lab implementation, Rust Lab controller, Lab tests, and master plan are untracked. HEAD therefore cannot be used as a complete description of the runtime being audited.
- **Release/provenance evidence is red.** `scripts/evidence_consistency_gate.py` fails because `docs/architecture/evidence/current.json` and remediation records use `CHECKOUT_HEAD`, and suite records do not match `f9645caf6d17cee2023d52183990ffcf8317e456`.
- **Native authority is not universal.** Explicit cells and many Lab transitions are admitted/settled, but hidden planner/adapter/process side effects, descendants, retry policy across adapters, and hosted multi-process single-writer authority remain outside the demonstrated native proof.
- **Packaging and import authority are ambiguous.** Root maturin packaging and a separate setuptools package expose overlapping imports and CLIs; editable source import is current local reality, while historical v63 clean-wheel evidence is not present on disk for re-verification.
- **Research, physics, browser isolation, benchmark generalization, and operations remain bounded/local rather than externally demonstrated.** The plan correctly leaves them open; the repository must not convert those contracts into claims of scientific validity, secure isolation, or production readiness.

**Decision:** freeze feature expansion and begin truth/provenance convergence first. Do not begin broad architecture convergence as if the current design were already canonical. Resolve source-of-truth, packaging, authority ownership, and evidence binding before adding more autonomy.

## 2. Exact Repository State

### 2.1 Git and workspace

| Fact | Observation | Class |
|---|---|---|
| HEAD | `f9645caf6d17cee2023d52183990ffcf8317e456` | PROVEN by `git rev-parse HEAD` |
| Branch | `main`, upstream `origin/main` | PROVEN |
| Remote | `https://github.com/thanhmuefatty07/AEGIS-COGNITION.git` | PROVEN |
| Divergence | `HEAD...origin/main = 10 0` | PROVEN |
| Worktrees | one worktree: `C:\Users\ADMIN\AEGIS-COGNITION` | PROVEN |
| Submodules | none reported by `git submodule status` | PROVEN |
| Staged changes | none | PROVEN |
| Tracked modified files | 58 | PROVEN from porcelain status |
| Untracked files | 17 | PROVEN from porcelain status |
| Total porcelain entries | 75 | PROVEN |
| Diff size | 2,435 insertions, 525 deletions across 58 tracked files | PROVEN; not a quality score |
| `git diff --check` | no whitespace errors; CRLF warnings only | PROVEN |

The working tree is materially different from HEAD. Important untracked files include:

- `aegis_cognition/lab.py` — 8,596 lines;
- `core/rust/src/lab.rs` — 3,976 lines;
- `tests/test_lab_runtime.py` — 4,530 lines;
- `aegis_cognition/benchmark.py` — 490 lines;
- `docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md` — 1,991 lines;
- `docs/architecture/document_inventory.json`, `docs/architecture/AEGIS_LAB_STATUS_GENERATED.md`, new ADR/API/tutorial/troubleshooting files, and new scripts.

The untracked Lab files are not a minor documentation delta: they define the largest current execution path. Any claim about “the repository at HEAD” that relies on them is false unless the claim explicitly says “working tree”.

### 2.2 Top-level shape

The repository contains source, tests, plugins, proof-of-concept crates, deployment material, evidence artifacts, generated/cached directories, and compatibility trees. Relevant directories include `aegis_cognition`, `core`, `aegis-plugins`, `pocs`, `schemas`, `scripts`, `tests`, `docs`, `fuzz`, `deploy`, `cluster`, `examples`, `artifacts`, and `target`. `.venv`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, and `target` are ignored; `.serena` is present but untracked and not ignored.

### 2.3 Commit history signal

Recent commits are heavily concerned with evidence labels, consistency gates, release blockers, GT96 traceability, and platform lanes (`f9645ca`, `4fbec4b`, `9af7d47`, `e37c50f`, `6dbadea`, `9d2df91`, `96ea33e`, `f3c72a6`, `0d75bac`, `41b8fc9`). This indicates deliberate truth-governance work, but commit history does not prove that the current uncommitted Lab tree has been reviewed or released.

## 3. Source of Truth Matrix

| Artifact | Actual role | Authority boundary | Current status |
|---|---|---|---|
| Attached `AGENTS.md` | user-supplied engineering constitution/instructions | normative behavior for this audit; not runtime truth | applicable policy |
| Pasted forensic prompt | user-supplied audit specification | defines required audit sections and read-mostly constraints | applicable request |
| `docs/ENGINEERING_CONSTITUTION.md` and related constitution docs | repository policy | normative, not evidence of implementation | tracked policy |
| `docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md` | declared canonical Lab plan | describes target/current status; cannot override code or machine evidence | untracked; `IN_EXECUTION`; applies to HEAD plus working tree |
| `docs/architecture/document_inventory.json` | document authority/index | identifies canonical plan and generated view | untracked; locally consistent by document gate |
| `docs/architecture/evidence/current.json` | machine-readable current evidence source | should bind requirement → implementation → run → commit → evidence class | tracked modified; invalid current SHA placeholder |
| `docs/architecture/not_verified_registry.json` | explicit uncertainty/blocker registry | enumerates open platform/release/quality gaps | 20 NV entries; useful but not proof of closure |
| `docs/architecture/deployment_policy.json` | release policy | says all production blockers must clear | identifies NV-004, NV-016…NV-019 as production blockers |
| `docs/architecture/AEGIS_LAB_STATUS_GENERATED.md` | generated human view | derivative of registry/policy | generated view present; does not create evidence |
| Source code (`aegis_cognition`, `core/python`, `core/rust`) | executable implementation truth | highest authority for actual behavior | mixed committed/working-tree state |
| Tests and retained artifacts | evidence producers | prove only the exact version/scope/run retained | many local tests exist; current retained final-SHA closure absent |
| CI workflows | intended verification procedure | describe gates, not proof that a run happened now | workflows present; no current hosted run IDs in machine evidence |
| README/quickstart/historical plans | explanatory or historical | cannot override machine evidence | some wording is broader than current evidence |

**Hierarchy used for this audit:** executable working-tree behavior > current machine evidence (when valid) > committed docs/contract descriptions > historical reports/plans > prose claims. A normative instruction document is not evidence of a runtime property.

## 4. Actual Architecture

### 4.1 Runtime planes actually present

```text
User / CLI / examples / tests
        |
        v
public aegis_cognition.Agent
        |
        v
AgentApplication
   |                    \
   | lab enabled          \ non-lab compatibility path
   v                       v
LabApplication          AegisAdapter (core/python)
   |                       |
   |                       +--> provider route / fallback / hot evidence
   |                       +--> LearningManager / compatibility RAG
   |
   +--> LabRun (Python mutable projection/reducer)
   +--> ExecutionCellRegistry (explicit policy/capability/effect checks)
   +--> PyO3 PyLabController / Rust LabRuntime / GT96 admission
   +--> search/fetch, Browser/Playwright, skills/tools, experiments/simulation
   +--> gateway/provider attempts, benchmark validator, memory context/index
   +--> replay/snapshot/archive/Arrow audit

Rust aegis-nerve crate
   +--> runtime, resource, execution, sandbox, replay, evidence, GT96,
       telemetry, IPC/shared memory/mmap, task ledger, tool gateway,
       FFI/PyO3, LabController, platform adapters

Adjacent surfaces
   +--> operator HTTP API/server
   +--> threaded TCP cluster worker
   +--> Wasmtime sandbox and plugins
   +--> POC crates (excluded from default members, included by deep workspace)
```

### 4.2 Architecture ownership

- **Python owns user-facing orchestration and a mutable projection.** `aegis_cognition/application.py` routes Lab/non-Lab execution; `aegis_cognition/lab.py` builds state, executes cells, and appends events.
- **Rust owns native admission primitives and many invariants.** `core/rust/src/lab.rs` defines typed records, event validation, budget/finalization, replay, snapshots, and controller methods; `core/rust/src/ffi.rs` exposes them.
- **The boundary is admission-oriented, not lifecycle-exclusive.** Python still decides sequencing and invokes side-effecting adapters after admission. That is materially safer than unconstrained execution but not a single reducer/authority proof.
- **Compatibility paths remain first-class.** `core/python/aegis_adapter.py`, `core/python/aegis/provider.py`, and compatibility imports can bypass portions of the Lab lifecycle when callers do not use `Agent(..., lab=True)` or when native authority is not required.

### 4.3 Intended versus actual

The master plan intends one native-authoritative LabController above explicit Execution Cells, with research, experiment, benchmark, and dossier semantics. Actual code has most of the named types and gates, but the native controller is used as a projection/admission authority while Python retains lifecycle ownership. The intended “one reducer” property is therefore a target with substantial local evidence, not a proven global invariant.

## 5. Actual End-to-End Execution Flows

### 5.1 Public Agent, non-Lab

1. `aegis_cognition.agent.Agent.run/arun` delegates to `AgentApplication` (`aegis_cognition/agent.py`).
2. `AgentApplication.arun` emits telemetry and calls `AegisAdapter`/gateway when Lab is disabled (`aegis_cognition/application.py`).
3. `AegisAdapter.run` may invoke a provider attempt hook, routes candidates through `invoke_with_provider_route`, and records hot evidence (`core/python/aegis_adapter.py`, `core/python/aegis/provider.py`).
4. Completion may index memory; failures are caught and emitted as failure telemetry.

**Risk:** `AegisAgent` compatibility retry behavior uses Python exponential sleep/`print` and is not wholly owned by the Rust Lab fence. Retry/idempotency semantics therefore differ by entrypoint.

### 5.2 Public Agent, Lab enabled

1. Agent configuration chooses Lab mode; Lab may set replay directory and native-authority requirement.
2. `LabApplication.run` acquires an advisory `ReplayWriterLease` when configured and rejects in-process overlap.
3. `_run_unleased` creates a `LabRun`, prepares an immutable/sealed `ExecutionCellRegistry`, and starts context retrieval, skills/tools, search, browser, experiment, simulation, gateway, benchmark, synthesis, archive, and post-completion cells.
4. `LabRun._append` hashes events, asks native admission/projection methods, and writes the Python projection/Arrow audit. Native rejection is intended to fail closed and roll back the Python projection.
5. Search uses bounded query/fetch/citation logic; browser launch/action/observation is admitted before calling a launcher; browser prompt-injection markers can stop the run.
6. Context retrieval and post-completion memory indexing are represented as typed cells. Benchmark validators can execute in an isolated subprocess with protocol/hash/timeouts.
7. Synthesis runs through an admitted gateway. Archive verification can block completion on invalid or incomplete replay.

**Not proven:** arbitrary adapters cannot create hidden side effects; non-cooperative descendants are terminated; a hosted deployment has a single writer; all retry policies are centrally accounted; a crash prefix fully reveals external effects.

### 5.3 Direct compatibility and auxiliary flows

Scripts, `core/python/aegis_cli.py`, operator API, cluster worker, plugin examples, Rust binary, and POC crates are additional entrypoints. They are not all forced through `AgentApplication` or the Lab controller. “The Agent lifecycle is controlled” must therefore be scoped to a named entrypoint and policy mode.

## 6. Python Import & Packaging Graph

```text
root pyproject.toml (maturin)
  package: aegis_cognition
  native module: aegis_cognition.aegis_nerve
  script: aegis=aegis_cognition.cli:main
  python-source: .; includes core/**/*.py

core/python/pyproject.toml (setuptools)
  package: aegis-cognition-core-python
  package-dir: .; includes "*"
  script: aegis=aegis_cli:main

root aegis_cognition
  -> core.python.aegis_adapter / compatibility modules
  -> root runtime.py tries aegis_nerve then aegis_cognition.aegis_nerve

core/python/aegis/__init__.py
  -> lazy compatibility exports from aegis_adapter
```

### Findings

- There are two package/build/CLI surfaces with the same conceptual product name.
- `Ruff`/`Pyright` root configuration covers `aegis_cognition` but excludes `core/python` and tests; a clean root gate therefore does not mean the compatibility package is equally checked.
- The current venv has `aegis-cognition 0.1.0` editable from the checkout; imports resolve to `C:\Users\ADMIN\AEGIS-COGNITION\aegis_cognition\__init__.py`. This proves current local source import, not wheel parity.
- Historical v63 wheel paths named by the plan are absent from `C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v63`; the claimed package smoke cannot presently be independently replayed from those files.
- Python 3.14.0 is active in `.venv`; system Python 3.11.9 also sees checkout code. This is a packaging/path ambiguity, not evidence of supported 3.11 behavior.

## 7. Rust Crate/Module/Public API Graph

### 7.1 Workspace

Root `Cargo.toml` declares ten members: `core/rust`, six `aegis-plugins` crates (`search-sdk`, `browser`, `sandbox`, `skills`, `evidence`, `bench`), and three POCs (`semantic_cache_poc`, `tool_batching_poc`, `code_orchestration_poc`). Default members exclude POCs; deep workspace tests include them. Resolver 3 and Rust 2024/rust-version 1.97 are declared.

### 7.2 Core crate

`core/rust/src/lib.rs` publicly declares 43 modules, including `ffi`, `lab`, `gt96`, `replay`, `execution`, `resource`, `resource_platform`, `sandbox`, `telemetry`, `tool_gateway`, `task_ledger`, and compatibility surfaces. The crate has a large crate-wide Clippy allow list, which lowers the signal of aggregate lint cleanliness.

Static inventory found approximately 1,838 Rust public declarations and 339 nested Python definitions. These counts are orientation only; public declaration count is not API quality.

### 7.3 Public API shape

The Rust surface includes a `cdylib`, `rlib`, and binary. PyO3 FFI exposes runtime submission/retry/finish, hardware/profile/resource admission, hot commit, learning/session search, archive/verification, and `PyLabController`. The surface is broad enough that “Rust is an internal implementation detail” is inaccurate: it is a public runtime contract for Python and packaging.

### 7.4 Native hazards requiring bounded follow-up

- `core/rust/src/ffi.rs:18-25` initializes a session index with `SessionSearchIndex::new(epoch_hash).unwrap()`. This is a production initialization unwrap; the surrounding comment does not remove the failure mode.
- `aegis_llm_request` and `aegis_llm_reject` use `Box::leak` for request/error strings. Unless an intentional bounded arena exists outside the inspected snippet, this is a per-call leak risk.
- Broad static search found many `.unwrap()`/`.expect()`/`panic!`/`unsafe` occurrences, including tests embedded in production files. The aggregate count is not a production panic proof; no blanket panic-free claim is permitted.

## 8. Rust ↔ Python FFI Graph

```text
Python AgentApplication / LabApplication / runtime.py
    -> import aegis_cognition.aegis_nerve (or fallback)
    -> PyO3 functions in core/rust/src/ffi.rs
       -> runtime/resource/evidence/replay/learning APIs
       -> PyLabController (core/rust/src/lab.rs)
       -> native archive/verify and typed admission
    <- py_safe panic/error translation
```

`py_safe` provides a panic-to-Python error boundary, but error translation does not prove that every caller handles every failure correctly. Python fallback in `runtime.py` is explicitly unverified and the fallback is not a scheduler or a substitute for Rust authority.

`PyLabController` is a strong boundary object with epoch, budget, finalization, projection, snapshot, and restore operations. The observed Python path still serializes and drives many transitions; therefore the FFI graph proves the existence of native controls, not exclusive native ownership.

## 9. Authority & State Mutation Graph

| State/effect | Current writer | Native check | Remaining authority gap |
|---|---|---|---|
| Lab source/claim/hypothesis/experiment/observation projection | Python `LabRun` dictionaries/lists | hash + projection/event admission | mutable Python state remains a second reducer |
| Event chain | Python append plus Rust admission/Arrow audit | hash-chain and typed validation | external effects can occur outside event log |
| Budget/finalization/epoch | Rust `LabController`/`LabRuntime` | native | lifecycle caller still Python |
| Explicit execution cells | Python registry | identity/capability/effect/trust/seal | unregistered/hidden adapter side effects not impossible |
| Browser launch/action | Python launcher after typed admission | native event admission | process/OS containment, DNS race, descendants open |
| Search/fetch | Python `urllib` after admission | URL/address preflight and event | DNS rebinding race and provider semantics open |
| Provider retry | `provider.py` and `AegisAgent` | hooks when installed | split policies, duplicated retries, non-Lab callers |
| Context retrieval/index | compatibility memory layer through cells in Lab | typed admission/settlement | persistence correctness/hosted writer open |
| Replay file | Python writer with `ReplayWriterLease` | local advisory lease + archive verifier | not a hosted/distributed single-writer proof |
| Operator reads | operator API/server | no Lab mutation authority | host/root configuration can expose artifacts |

The crucial invariant is not “each event has a valid hash.” It is “no consequential side effect can happen without a valid, unique, policy-authorized admission and an unambiguous settlement.” The first property has meaningful local evidence; the second remains open for arbitrary adapters, processes, descendants, and external services.

## 10. Side-Effect Graph

| Side effect | Location | Boundary | Threat/failure note |
|---|---|---|---|
| HTTP/DNS/socket fetch | `aegis_cognition/lab.py`, search/browser adapters | URL preflight, redirect/final-host checks | DNS rebinding race remains possible |
| Playwright/browser process | `core/python/browser_playwright_runtime.py` | Browser Cell/capability and launcher admission | OS/kernel/job/cgroup and hostile cross-domain corpus unproven |
| Files/replay/snapshots/artifacts | Lab, replay, memory, scripts | path/config and archive verifier | path/root ownership and crash-prefix external effects need hosted evidence |
| Subprocess benchmark validator | Lab/benchmark | isolated protocol/hash/timeout | isolation/secrecy outside candidate-controlled infrastructure unproven |
| Provider/network API | `core/python/aegis/provider.py`, OpenAI integration | candidate route and optional hooks | rate-limit/retry/fallback semantics split |
| Operator HTTP | `core/python/operator_api_server.py` | localhost default | CLI can bind non-local without auth; configurable root can expose data |
| Cluster TCP | `cluster/worker_service.py` | threaded listener on `0.0.0.0` | no TLS/auth; test/soak worker, not production authority |
| Wasmtime | Rust sandbox modules | runtime resource/sandbox checks | hostile-kernel and full adversarial evidence open |
| mmap/shared memory | Rust memory/shm/bridge modules | native resource policy | platform parity and corruption recovery open |
| process/thread spawn | Rust execution/platform and Python process cell | timeout/cancellation/Job Object where available | descendants and pressure-kill not fully proven |

## 11. Canonical Data Model Matrix

| Concept | Python representation | Rust representation | Current canonical owner | Evidence status |
|---|---|---|---|---|
| Mission | `LabRun` mission fields / plan `LabMissionSpec` | `LabMissionSpec` | split; Rust schema is stronger, Python lifecycle owns construction | NOT VERIFIED as one canonical serializer |
| Source | Python source dict/events | `SourceRecord` | split projection | local typed admission only |
| Claim | Python claim dict/events | `ClaimRecord` | split projection | local validation; semantic truth not proven |
| Hypothesis | Python hypothesis | `HypothesisRecord` | split projection | falsifier linkage bounded |
| Experiment | Python execution model | `ExperimentSpec` | split | bounded execution/settlement |
| Observation | Python observation, units/uncertainty fields | `ObservationRecord` | split | unit/uncertainty contracts; calibrated science open |
| Benchmark | Python `benchmark.py`/validator records | `BenchmarkProtocolV2`, `BenchmarkResultV2` | split protocol with operator-owned validator | local protocol evidence; generalization open |
| Event | Python event dict/object | `LabEvent` and event kinds | Rust validates; Python appends | native admission not universal side-effect proof |
| Dossier/archive | Python projection plus replay/archive | Rust archive/verify functions | split | archive verifier local; external restore open |
| Execution cell | Python `ExecutionCellRegistry` | Rust typed admission receipts | Python registry + Rust receipt | explicit cells covered; hidden side effects open |
| Trust level | `AgentConfig` defaults `DEV` | core evidence normalizer defaults `PROD` | contradictory defaults | policy ambiguity |

The duplicate model is the main cohesion risk. Serialization, hash binding, and projection checks reduce divergence; they do not remove the cost of maintaining two mutable representations or prove that every future field is delegated consistently.

## 12. Cohesion/Coupling Findings

### 12.1 Strong cohesion islands

- `aegis_cognition` has high graph cohesion (~0.927 in the available graph) and a recognizable application facade.
- The Lab plan, blocker registry, generated status view, and document gate form a coherent governance island.
- Rust resource/execution/GT96/replay modules expose deliberate invariant-focused boundaries.

### 12.2 Coupling/debt hotspots

- `scripts` is large and cross-coupled (graph cluster ~570 members); scripts are simultaneously test drivers, evidence producers, release helpers, and report generators.
- `core/rust/src/cli/mod.rs::run` is a hotspot (complexity 45; cognitive complexity 144; 156 lines in graph).
- `aegis_cognition/lab.py` is a very large orchestration/reducer module; `_execute_browser_action` is a hotspot (complexity 28).
- `evidence_consistency_gate.py`, `benchmark.py`, and replay validation are high-branching governance/validation code.
- Root package, `core/python`, Rust FFI, plugins, POCs, scripts, and examples create multiple entrypoint paths that do not share one ownership boundary.
- Codebase-memory graph reports `adr_present: false` despite `docs/adr` existing, showing that the graph cannot be treated as complete repository truth.

## 13. Dead/Stale/Duplicate Candidate Inventory

These are candidates for investigation only; no deletion was performed.

| Candidate | Why it is suspicious | Safe disposition now |
|---|---|---|
| `core/python` package/CLI alongside root package/CLI | overlapping package names and console script | PRESERVE; map callers, then choose compatibility strategy |
| `aegis_cognition/lab.py` and `core/rust/src/lab.rs` parallel models | duplicated state/reducer ownership | PRESERVE; make ownership decision before merge/split |
| Historical plans/reports/plugin READMEs/POCs | document inventory lists historical paths; some say complete | PRESERVE as history; mark non-authoritative |
| ADR-012 versus ADR-015 | ADR-012 marked superseded compatibility stub | PRESERVE note; do not use as current decision authority |
| `.serena` | untracked tool metadata | INVESTIGATE ignore/ownership; do not delete automatically |
| POC crates | deep-only workspace members | PRESERVE if experimental; isolate from production graph |
| Legacy direct script loaders/import-after-bootstrap patterns | modified to fix Ruff E402 | PRESERVE with direct-entrypoint regression tests |
| Generated status/evidence views | generated from registry/manifest | regenerate only after source-of-truth decision; never hand-edit |
| `runtime.py` native fallback | source fallback is explicit but unverified | PRESERVE; label fallback semantics and test scope |

## 14. Dependency Rent Report

### 14.1 Runtime/build inventory

Root runtime includes `blake3`, `fastapi`, `openai`, `python-dotenv`, `pyyaml`, `uvicorn`; extras add Playwright and broader development tooling. Rust includes PyO3, Wasmtime 47.0.3, Arrow 54, memmap2, parking_lot, ed25519-dalek, serde, tokio, rayon, networking/IPC, cryptography, parsing, and Windows platform crates. Six plugins and three POCs add workspace surface.

### 14.2 Rent findings

- PyO3 + maturin is justified by the native authority/resource requirement but creates ABI, wheel, and cross-platform verification obligations.
- Wasmtime is a significant security/runtime dependency; exact pinning is good, but full hostile-kernel and fuzz evidence remains open.
- Arrow, mmap/shared-memory, and multiple IPC/network layers increase serialization, platform, and recovery complexity.
- Playwright is an optional but operationally heavy browser dependency; browser lifecycle and browser binary parity are not proven across all lanes.
- OpenAI/provider dependencies introduce rate-limit, schema, network, and reproducibility variability.
- Plugins and POCs are valuable experiments but enlarge the workspace and deep-suite cost; default-member exclusion reduces fast-gate rent while preserving deep coverage.
- No dependency should be removed solely to reduce counts; first map whether it is on a production path, a test/evidence path, or a POC path.

## 15. Testing Architecture

### 15.1 Test surfaces

- `tests/test_lab_runtime.py`: 116 pytest functions (untracked current Lab regression surface).
- `tests/test_document_consistency_gate.py`: 3 functions.
- `tests/test_evidence_consistency_gate.py`: 8 functions.
- `tests/test_runtime_contracts.py`: 6 functions.
- `tests/test_suite_evidence.py`: 1 function.
- `core/python/tests.py`: 83 functions.
- Rust unit/integration/benchmark/fuzz/Miri/ASan lanes are distributed across source, `tests`, `benches`, and CI workflows.

### 15.2 Local gates observed in this audit

| Gate | Result | Interpretation |
|---|---|---|
| `scripts/architecture_fitness.py` | PASS; 23 structural checks | presence/shape/policy checks only; not runtime proof |
| `scripts/document_consistency_gate.py` | PASS | document inventory/generated-view/link/metadata parity for current checkout |
| `scripts/evidence_consistency_gate.py` | FAIL | current SHA/provenance mismatch; decisive blocker |
| historical `pytest tests core/python/tests.py -W error::DeprecationWarning` | 221 passed, recorded by prior project work | historical local evidence; not rerun/retained here as final-SHA artifact |

The plan also records targeted v63 controller/recovery/rollback and warning-free results. Those statements are scoped to a prior Windows/CPython 3.14 working-tree run. The referenced wheel/JSON files are absent now, so their current status is **historical/self-reported local evidence**, not independently reproducible evidence.

### 15.3 Test architecture gaps

- Current graph/test inventory is stale with respect to untracked Lab additions.
- Deep workflow defines fuzz, Miri, ASan, full-workspace, and soak lanes, but no current hosted run IDs are bound in `current.json`.
- Tests strongly cover explicit local contracts; they do not prove semantic research quality, hostile browser isolation, hidden scorer secrecy, multi-host writer ownership, or calibrated hardware energy.
- Embedded Rust tests inflate naive production source scans; test/production separation should be made explicit before using static counts as release criteria.

## 16. Security Architecture

### 16.1 Existing controls

- Native boundary has `py_safe` panic/error handling.
- Execution cells validate identity, capability, effect, trust, duplicate/late mutation, and sealed manifests.
- Lab rejects known prompt-injection markers in bounded browser/context paths.
- Search preflights literal/obfuscated private/loopback/link-local/reserved addresses and checks resolved addresses/final hosts.
- Replay/event hashes, typed records, archive verification, and crash-prefix rejection provide tamper/ambiguity signals.
- Secret scanning, dependency gates, Cargo deny/audit, fuzz workflow definitions, and supply-chain scripts exist.

### 16.2 Open security boundaries

- DNS rebinding can race preflight and connection.
- Browser capability separation is not kernel/process isolation; cross-domain hostile content and browser crash/egress corpus are open.
- Operator server defaults to localhost but accepts `--host`; no authentication is established for non-local binding. `AEGIS_OPERATOR_ROOT`/`AEGIS_ARTIFACTS_DIR` can redirect reads to a selected filesystem root. This is a potential data-exposure boundary if operationally misconfigured.
- Cluster worker listens on `0.0.0.0` with plaintext threaded TCP and no demonstrated authentication/TLS; it is suitable only as a test/soak surface until hardened.
- Provider/API secrets and external data handling need deployment-specific threat modeling; absence of a scan finding is not proof of safe retention.
- Rust `unsafe`, process, mmap, Wasmtime, and FFI code require targeted adversarial evidence rather than aggregate lint status.

## 17. Replay/Recovery Architecture

### 17.1 Current design

Events carry hashes and typed payload/reference checks. `LabController` supports snapshots/restore, finalization, budget lanes, and projection admission. Python `ReplayWriterLease` serializes configured local writers. Recovery settles open explicit admissions as rejected/unknown-side-effect and blocks the dossier when the prefix cannot prove success. Archive verification rejects incomplete/tampered tails.

### 17.2 What this proves locally

- deterministic rejection of duplicate/invalid typed admissions;
- mixed-lane crash-prefix reconciliation for explicit admissions in recorded tests;
- local process contention rejection for the replay writer;
- rollback/drill behavior in prior local package evidence.

### 17.3 What it does not prove

- an external side effect did not happen just because its log tail is absent;
- an arbitrary non-cooperative process/descendant stopped;
- a hosted or multi-machine writer cannot fork/overwrite a run;
- external backup restore, retention, corruption recovery, or RPO/RTO;
- deterministic replay of live providers, browsers, DNS, or hardware.

## 18. Concurrency/Resource Architecture

- Rust has explicit resource/execution lanes, budget accounting, platform adapters, thread/process primitives, shared memory and mmap paths.
- Python has an in-process active-run guard and advisory replay writer lease.
- Windows Job Object support exists; plan evidence says assignment, active-process quota rejection, deadline cancellation, and termination were observed, but memory-pressure kill was not observed and descendants are not proven.
- Linux cgroup v2, macOS enforcement, WSL/Hyper-V, hosted multi-machine TCP soak, and cross-host policy freeze remain `NOT VERIFIED`.
- `cluster/worker_service.py` is a threaded listener, not a consensus or single-writer service. A network listener is not by itself a distributed authority.
- Retries, cancellation, duplicate delivery, timeout ambiguity, and provider fallback are handled at several layers. A system-wide retry budget/idempotency proof is absent.

## 19. Performance Architecture

### 19.1 Measured

- Repository plan records local Windows sample-size-10 resource/architecture microbenchmarks and bounded electrical energy `1.0 J` with `MEASURED_INPUTS_ONLY`.
- These are local measurements tied to prior artifacts; current cross-tier and cross-host comparison is not retained in the current manifest.

### 19.2 Inferred

- The architecture can support bounded CPU/Python/I/O/untrusted lanes because corresponding Rust contracts and local tests exist.
- Python/Rust projection, hashing, Arrow/audit writes, browser/provider waits, and replay I/O add latency and memory cost.
- Multiple orchestration layers and cross-language serialization are likely hot paths, but no profile here establishes their share of end-to-end latency.

### 19.3 Not verified

- p50/p95/p99 latency, throughput, saturation, queue depth, memory/allocations, browser cold start, provider latency, and cost at target workload;
- Amdahl-relevant fraction of native versus Python work;
- thermal/electrical measurements or calibrated hardware energy;
- solver convergence for PDE/stiff/circuit workloads;
- benchmark generalization, hidden-score integrity, and independent reproduction.

No claim of faster, cheaper, scalable, zero-copy, energy-optimal, or production-performance superiority is justified by the current evidence.

## 20. Cross-Platform Reality

| Platform/lane | Declared support/evidence | Truth status |
|---|---|---|
| Windows CPython 3.14 | active `.venv`; prior local package/controller probes | local evidence, historical artifacts unavailable for replay |
| Windows Job Objects | adapter and prior live probe | partial; memory-pressure/descendant proof open |
| Linux | workflow and cgroup adapters/fixtures | live privileged enforcement not verified |
| macOS | compile/capability workflow and cooperative adapter | hosted runtime/package evidence not verified |
| WSL2 | attempted by prior work | unavailable: `HYPERV_NOT_INSTALLED` |
| Python 3.15 RC/free-threaded | CI matrix declared | current hosted result not bound in manifest |
| Native wheel parity | workflow defined for Ubuntu/Windows/macOS | current clean-wheel artifact absent |
| Multi-machine TCP | report/probe scripts exist | live soak not verified |

The existence of a workflow lane is a design intent. It is not evidence that the lane passed for the current SHA.

## 21. Architecture Fitness Gaps

### What current checks detect

- module/file presence and canonical boundary declarations;
- workspace/Rust/PyO3/maturin configuration shape;
- schema versions, lockfile presence, traceability/document fields;
- telemetry and secret-scan configuration;
- selected forbidden-boundary/canonical-owner rules;
- generated-document parity;
- machine evidence schema and SHA format (the latter currently catches the blocker).

### What current checks cannot detect

- untracked implementation drift unless the checker explicitly scans it;
- hidden side effects in arbitrary Python/Rust adapters or subprocess descendants;
- whether Python and Rust reducers are semantically equivalent for every field/event;
- DNS rebinding races, hostile browser content, real egress/firewall behavior;
- live resource enforcement and memory-pressure behavior on each OS;
- semantic correctness/freshness/contradiction recall of research;
- scientific calibration, solver convergence, instrument uncertainty, or hardware energy;
- hidden benchmark scorer secrecy/contamination resistance;
- hosted single-writer, backup restore, rollback, attestation, or current CI execution.

### Future graph gates (proposal only; not implemented here)

1. fail when a production entrypoint bypasses the canonical Agent/Lab boundary without an explicit compatibility label;
2. fail when a side-effecting symbol is reachable from a Lab run without an ExecutionCell/admission edge;
3. fail when a canonical record has multiple mutable writers without an ADR and contract test;
4. fail when package/CLI ownership is ambiguous or import paths cross an undeclared compatibility boundary;
5. fail when evidence artifacts are not bound to the exact commit and source manifest;
6. fail when generated docs are newer/older than their declared source and commit;
7. fail when deep-only POC/plugin code is accidentally reachable from production default members;
8. fail when operator/network services are externally bindable without an explicit auth/TLS policy.

## 22. Architecture Debt Ledger

| ID | Severity | Evidence | Root cause | Impact | Likely fix direction | Change risk |
|---|---|---|---|---|---|---|
| AD-001 | Critical | `current.json` uses `CHECKOUT_HEAD`; evidence gate fails | evidence generated before final SHA and not regenerated | release/provenance claims cannot be trusted | final-SHA regeneration, immutable artifact binding, hosted IDs | high if hand-edited; low if pipeline-owned |
| AD-002 | Critical | 58 modified + 17 untracked; Lab core untracked | implementation and plan developed outside commit boundary | HEAD/review/CI cannot describe current runtime | freeze, inventory, commit boundary review | high due large diff |
| AD-003 | High | Python `LabRun` plus Rust `LabController` | incremental native-admission migration | split authority, semantic drift | choose canonical reducer; compatibility projection only | high |
| AD-004 | High | root maturin + `core/python` setuptools | historical compatibility retained without one package owner | ambiguous imports, wheel/CLI behavior | explicit package ownership/versioned compatibility | medium/high |
| AD-005 | High | `AgentConfig` DEV default vs core normalizer PROD | policy defaults evolved in different layers | trust-mode inconsistency | one typed policy source and cross-layer contract test | high security impact |
| AD-006 | High | operator host option/no auth; `0.0.0.0` worker | test/operator surfaces reused as services | artifact/data exposure, unauthenticated network access | bind-local by default, auth/TLS/allowlist or isolate test service | medium |
| AD-007 | High | DNS preflight/connection race | URL safety is checked in separate phases | SSRF/private-network bypass possibility | connect-time pinned resolution/egress enforcement | high security impact |
| AD-008 | High | broad FFI unwrap/leak patterns | legacy/native boundary implementation | panic or unbounded memory growth | targeted production-path audit and bounded ownership | medium/high |
| AD-009 | High | split provider retries | compatibility route and Lab hooks evolved separately | retry amplification/duplicate effects | one retry/idempotency authority | high behavior change |
| AD-010 | Medium | graph omits untracked and misses ADRs | index snapshot not current/complete | architecture queries can be stale | re-index under controlled metadata policy | low operational; graph write required |
| AD-011 | Medium | historical docs say complete/PROVEN while manifest says NOT VERIFIED | historical and current statuses coexist | readers overclaim readiness | status namespaces and generated historical labels | medium docs churn |
| AD-012 | Medium | huge `lab.py`, scripts, CLI hotspots | orchestration and evidence concerns accumulated | reviewability and change blast radius | split by ownership only after graph/contract map | high refactor risk |
| AD-013 | Medium | deep POCs/plugins in same workspace | experimentation shares top-level build graph | cost and accidental reachability | explicit experiment boundary and target policy | low/medium |
| AD-014 | Medium | package artifacts referenced but absent | temp evidence retention is not durable | local proof cannot be re-run | retained immutable artifact store | low/medium |
| AD-015 | Medium | embedded tests/source static counts | test and production code co-located | noisy risk metrics | parser-aware inventories and explicit test modules | low |

## 23. Contradiction Register

| ID | Contradiction | Resolution for this audit |
|---|---|---|
| C-001 | `architecture_fitness.py` and document gate PASS; evidence gate FAIL | structural/document health does not imply provenance health; current release status is red |
| C-002 | TRACEABILITY rows say `PROVEN`/`LOCALLY PROVEN`; `current.json` says current suites `NOT VERIFIED` | treat row claims as scoped/historical unless exact SHA/artifact is retained |
| C-003 | plan says v63 wheel/controller/recovery/rollback `PROVEN`; temp files are absent | historical local claim; not current reproducible evidence |
| C-004 | plan calls Rust native controller authority; Python still owns mutable lifecycle/projection | native admission authority is real but universal lifecycle authority is not proven |
| C-005 | `AgentConfig` default trust `DEV`; core evidence normalization default `PROD` | unresolved policy ambiguity; fail closed until one source is chosen |
| C-006 | README says “cryptographically-verified AI agent”; README status also says production not claimed | wording is safe only when scoped to local evidence/hash mechanism; cannot imply full runtime/security verification |
| C-007 | graph says no ADR present; `docs/adr` exists | graph coverage defect, not repository absence |
| C-008 | workflow definitions contain extensive gates; no current hosted run IDs in manifest | procedure exists, current execution is not verified |

## 24. Unknown/Not Verified Register

The following are intentionally not promoted to facts:

- current final-SHA full Rust/Python/deep/release suite results;
- signed release attestation, SBOM/provenance for this working tree, and external deployment smoke;
- Linux cgroup enforcement, macOS runtime/package parity, Windows memory-pressure kill, and descendant containment;
- hosted multi-process/multi-machine replay writer authority and TCP soak;
- DNS rebinding race resistance and real browser egress/firewall/process isolation;
- semantic research freshness, provider drift, contradiction precision/recall, and independent citation reproduction;
- PDE/stiff/circuit solver validity, instrument calibration, uncertainty propagation, and hardware energy;
- hidden benchmark scorer secrecy, contamination resistance, outlier/dispersion behavior, and independent reproduction;
- external backup restore, RPO/RTO, corruption recovery, and retention/deletion behavior;
- native fallback equivalence and clean wheel import/runtime for current untracked Lab code;
- complete graph coverage of untracked/generated/ignored files;
- production-path absence of all unsafe unwrap/leak/side-effect patterns;
- one canonical trust policy and one canonical retry/idempotency policy.

## 25. Preserve/Remove/Merge/Split/Investigate Matrix

| Area | Current action | Reason |
|---|---|---|
| Lab implementation and tests | PRESERVE | central requested capability; untracked status must be resolved before refactor |
| Rust LabController | PRESERVE | valuable invariant/admission kernel; ownership boundary still to decide |
| Python Lab projection | PRESERVE temporarily | compatibility and current lifecycle; convert only with equivalence evidence |
| Root package | PRESERVE | public facade and maturin owner candidate |
| `core/python` package | PRESERVE temporarily | callers exist; classify as compatibility before deprecation/removal |
| Historical plans/reports | PRESERVE, relabel | audit/history value; do not use as current proof |
| POCs | PRESERVE but isolate | experiment value; avoid default production reachability |
| `.serena` | INVESTIGATE | untracked metadata/ownership/ignore policy |
| Operator/cluster test services | INVESTIGATE before exposure | no auth/TLS/hosted authority proof |
| FFI unwrap/leak paths | INVESTIGATE then minimal fix | possible reliability/resource defect; no blind sweep |
| Duplicate data models | MERGE only after contract inventory | premature merge risks breaking replay/wheel compatibility |
| Large Lab/scripts modules | SPLIT only after ownership/graph evidence | size alone is not a sufficient reason |
| Generated evidence views | REGENERATE from canonical machine source | never hand-edit derived state |

## 26. Candidate Target Architecture (CURRENT → MIGRATION → TARGET)

### CURRENT

Hybrid Python/Rust runtime; Python lifecycle and projection; Rust admission/controller; explicit execution cells plus compatibility/direct paths; two Python packaging surfaces; local replay lease; separate operator/cluster/test services; current evidence manifest invalid for HEAD.

### MIGRATION

1. Freeze feature additions and classify every working-tree file as production, compatibility, experiment, generated, evidence, or historical.
2. Commit/review the Lab tree as a coherent change or explicitly exclude it; regenerate evidence only from an immutable final SHA.
3. Choose one package/CLI owner; keep compatibility imports behind a named, versioned boundary.
4. Define the canonical record/event schema and make Python a projection or Rust the reducer—never two implicit authorities.
5. Route every consequential side effect through a declared cell/admission contract, or explicitly label and isolate legacy paths.
6. Unify trust, retry, cancellation, idempotency, and writer-lease policy.
7. Add graph gates and final-SHA evidence gates; then run platform/security/benchmark/restore experiments.

### TARGET (candidate, not approved)

```text
One versioned public Python package/CLI
        |
        v
One Lab session protocol + one canonical event/reducer authority
        |
        +--> explicit ExecutionCell registry (capability/effect/trust/lease)
        +--> browser/search/provider/experiment/benchmark cells
        +--> Rust native policy kernel and resource/process boundary
        +--> durable replay/archive with hosted single-writer service
        +--> independent verifier and evidence manifest bound to final SHA
        +--> operator API authenticated and read-only by default
        +--> platform adapters with real per-OS evidence
```

This target is intentionally smaller than “make every component distributed.” It preserves Python ergonomics, Rust invariant enforcement, and experiment flexibility while reducing authority ambiguity.

## 27. Alternatives

### A. Rust-canonical reducer with Python projection (recommended candidate)

Rust owns event acceptance, state transition, budget, replay, and canonical serialization. Python constructs requests and renders a projection. **Pros:** strongest invariant ownership, deterministic replay, narrower trust boundary. **Cons:** PyO3/schema migration cost, less Python flexibility, large compatibility effort.

### B. Python-canonical orchestration with Rust policy kernel

Python owns the domain reducer; Rust owns admission/resource/cryptographic primitives. **Pros:** quickest evolution and easier experimentation. **Cons:** harder to prove no hidden side effect or semantic drift; current ambiguity remains unless strict effect routing is enforced.

### C. Actor/service Lab with external durable event store

One Lab actor/service owns each run; browser/worker cells are remote and authenticated; event store provides single-writer and recovery. **Pros:** hosted isolation and multi-machine authority. **Cons:** network partitions, auth, deployment, cost, eventual consistency, and operational burden; unjustified until local canonical ownership and workload need are proven.

### D. Keep current hybrid and add documentation/gates only

**Pros:** minimal code risk. **Cons:** does not resolve split authority, package ambiguity, or hidden effects; suitable only as a short stabilization phase, not a target architecture.

**Selection criteria:** correctness/security first; then replayability, compatibility, operational burden, cross-platform feasibility, and reversibility. No alternative is “best” without a workload and evidence; A is the strongest candidate for the stated Lab goal, not a proven final decision.

## 28. Adversarial Critique

If the current solution is seriously wrong, the most likely hidden failures are:

1. an adapter performs an irreversible side effect after a valid-looking admission but outside the registry;
2. a DNS answer changes between preflight and connect, defeating SSRF checks;
3. a cancellation is swallowed, a child/descendant survives, or a provider retries after the Lab has aborted;
4. Python and Rust accept different field defaults or trust/retry semantics;
5. a stale/historical “PROVEN” document is used to authorize a release despite `current.json` being invalid;
6. the editable checkout passes while a clean wheel omits an untracked Lab file or resolves a different import;
7. a benchmark validator leaks corpus/answers or runs in infrastructure controlled by the candidate;
8. a valid hash chain hides an external effect that happened before a crash or timeout;
9. operator/cluster listeners are reachable beyond localhost without authentication;
10. local Windows behavior is generalized to Linux/macOS/hosted deployments;
11. bounded Euler/RK4/unit contracts are mistaken for calibrated science or hardware energy measurement;
12. retry layers amplify cost or duplicate non-idempotent provider/tool effects.

The repository already documents most of these as blockers. The remaining engineering task is to keep the blockers authoritative, not to rename them “closed” because local fixtures pass.

## 29. Recommended Architecture

### FREEZE

- Freeze new autonomy, physics, browser, benchmark, plugin, and distributed-runtime feature work.
- Freeze claims of production readiness, secure isolation, cross-platform support, scientific validity, energy optimization, or benchmark superiority.
- Freeze manual edits to generated evidence/status views.

### IMPLEMENT_NEXT

1. Resolve current-tree boundary: inventory and review the 58 modified/17 untracked files; decide which are part of the product.
2. Repair provenance flow so a final immutable SHA is generated and all current evidence/artifacts reference it; rerun the red evidence gate.
3. Decide canonical package/CLI ownership and trust-policy ownership.
4. Produce a contract matrix for every Python/Rust Lab record/event and one explicit reducer authority.
5. Audit reachable side effects and label every legacy bypass; make external bind/auth policy fail closed.

### EXPERIMENT

- Hostile browser/egress/DNS corpus;
- process/descendant/memory-pressure and cross-OS resource probes;
- hidden benchmark/contamination protocol;
- solver convergence/calibration/hardware energy experiments;
- restore/rollback/soak/multi-machine writer experiments.

Experiments must produce raw retained artifacts, exact command/toolchain/commit, negative cases, dispersion, and independent verification.

### DEFER

- distributed service/event store;
- broad module split or rewrite;
- accelerator/vendor integrations;
- generic plugin framework expansion;
- performance micro-optimization before profile/workload evidence.

### REJECT

- claiming universal authority from event hashes alone;
- treating architecture-fitness/document PASS as runtime/release PASS;
- hand-replacing `CHECKOUT_HEAD` in evidence JSON;
- deleting compatibility code before caller/contract migration evidence;
- using one Windows local run as cross-platform or production proof.

## 30. Proposed Cleanup Sequence (no code changes in this audit)

1. **Truth freeze:** stop feature edits; record the exact worktree state and classify files.
2. **Authority map:** maintain the source-of-truth matrix and mark historical/current documents.
3. **Contract closure:** enumerate public package, CLI, FFI, event, replay, trust, retry, and operator contracts.
4. **Evidence repair:** make manifest generation final-SHA-only; retain raw artifacts; rerun local gates; bind hosted IDs when available.
5. **Package convergence:** choose root package owner; establish compatibility import/CLI deprecation path.
6. **Reducer decision:** approve Rust-canonical, Python-canonical, or explicitly bounded hybrid with tests for every projection edge.
7. **Effect closure:** graph side-effecting calls and route/label all bypasses; secure operator/cluster exposure.
8. **Platform/security experiments:** run only after the local graph and evidence source are stable.
9. **Benchmark/science validation:** use independent, blinded, calibrated, reproducible protocols; keep `MEASURED_INPUTS_ONLY` until proven otherwise.
10. **Targeted simplification:** remove or isolate only proven-dead/duplicate paths; review each deletion against callers, replay, packaging, and rollback.
11. **Convergence review:** re-run architecture graph, contract tests, security/recovery checks, final diff, and adversarial review.

## 31. STOP/GO Decision

### STOP for architecture convergence as implementation expansion

**STOP** is required for adding more Lab autonomy or broad refactoring because:

- current evidence is invalid for the actual SHA;
- the main Lab implementation is untracked;
- authority and package ownership are split;
- hosted/platform/browser/research/physics/benchmark/recovery evidence remains open.

### GO for a bounded truth-convergence phase

**GO** is safe for a narrowly scoped phase that only inventories/classifies the working tree, establishes canonical ownership, repairs final-SHA evidence generation, and writes contract/graph gates. Such work must preserve behavior and must not silently delete or refactor production code.

## 32. Final Evidence Table

| Claim | Evidence | Class | Scope/limitation |
|---|---|---|---|
| HEAD is `f9645ca…` | Git inspection | PROVEN | current checkout only |
| 58 modified + 17 untracked | Git porcelain | PROVEN | exact current worktree snapshot |
| Lab implementation is largely untracked | file status and line counts | PROVEN | current checkout |
| Rust workspace has 10 members | Cargo metadata | PROVEN | default/deep membership distinction applies |
| Python has two overlapping package surfaces | both `pyproject.toml` files/imports | PROVEN | compatibility intent needs owner decision |
| Lab has explicit cells/native controller/events | source inspection and targeted tests/plan | PROVEN for code paths | not universal side-effect authority |
| Native authority controls every side effect | no decisive evidence | NOT VERIFIED | arbitrary adapters/processes/hosted writer open |
| Architecture fitness checks pass | local script exit 0, 23 checks | PROVEN | structural/presence checks only |
| Document consistency passes | local gate exit 0 | PROVEN | current checkout; generated view parity |
| Current evidence/release gate passes | local script exit 1 with SHA mismatch | NOT VERIFIED / FAIL | `CHECKOUT_HEAD` blocker |
| Historical 221-test warning-free suite passed | prior project record | SOURCE-BACKED HISTORICAL | not rerun/bound to current final artifact here |
| v63 wheel/controller/recovery/rollback passed | plan records; temp artifacts absent | HISTORICAL LOCAL CLAIM | not independently replayable now |
| Browser is securely isolated | capability/preflight code | NOT VERIFIED | OS/egress/DNS race/corpus open |
| Research is semantically reliable | bounded fetch/citation code | NOT VERIFIED | freshness/provider drift/contradiction quality open |
| Physics/electrical output is calibrated | unit/Euler/RK4/energy contracts | NOT VERIFIED | `MEASURED_INPUTS_ONLY`; no hardware/calibration proof |
| Benchmark generalizes and is contamination-resistant | local validator protocol | NOT VERIFIED | hidden scorer/independent reproduction open |
| Cross-platform production readiness | workflows/manifests | NOT VERIFIED | no current hosted final-SHA evidence |
| Repository graph is complete/current | codebase-memory snapshot | NOT VERIFIED | stale; excludes untracked and misses ADRs |
| Safe to begin broad architecture convergence | blocker/evidence review | NO | truth/provenance/authority must converge first |

**ARCHITECTURE_TRUTH_AUDIT_STATUS: PARTIAL**

The local truth model is detailed for the inspected production, compatibility, Lab, FFI, evidence, workflow, package, and deployment surfaces. It is `PARTIAL` rather than `COMPLETE` because the working tree contains untracked architecture-critical files, the structural graph is stale/partial, current hosted/final-SHA evidence is absent, and several platform/external proofs cannot be obtained from this checkout alone.

**Missing evidence required to upgrade to COMPLETE:**

1. a reviewed, immutable commit containing the intended Lab implementation and plan;
2. regenerated `current.json` and all derived views bound to that exact SHA;
3. retained current Rust/Python/deep/release artifacts and hosted run IDs;
4. fresh graph/index coverage of the committed tree and generated files;
5. explicit package/CLI/reducer/trust/retry ownership decisions with contract tests;
6. platform, browser, research, physics, benchmark, restore, and hosted writer evidence listed in Sections 20–24.

**SAFE_TO_BEGIN_ARCHITECTURE_CONVERGENCE: NO**
