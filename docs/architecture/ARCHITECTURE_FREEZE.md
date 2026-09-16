# AEGIS-COGNITION Architecture Freeze

**Status:** DESIGN_FREEZE_INPUT — not a production-readiness claim  
**Source of truth:** current working tree and the evidence pack named below  
**Scope:** freeze target boundaries and sequencing before surgical convergence

This document turns the already-audited target architecture into explicit,
reversible decisions. It does not assert that the checkout already satisfies
the target. A decision is a design constraint; its implementation still needs
the migration entry and exit evidence in
`docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md` §19.8.

## Evidence and current boundary

The final local reconciliation is recorded in the local-only cleanup archive
and its JSON artifacts. The local package owner is proven for the current Windows/CPython
lane, while the following remain open before surgical convergence:

- Python `LabRun` and Rust `LabController` are not one lossless reducer.
- Trust defaults are split between DEV and PROD contexts.
- Retry ownership and provider/SDK idempotency are not globally bounded.
- Final-SHA manifest provenance requires owner/external evidence.

These are gates, not reasons to silently downgrade the target.

## Frozen principles

1. Rust owns Lab scheduling, policy admission, replay order, budget, lease,
   settlement, and completion eligibility.
2. The model proposes typed intents and candidate evidence only; it cannot
   append authority events or promote retrieval/telemetry into truth.
3. Every effect-bearing action crosses a capability-scoped execution cell:
   admission → lease → attempt → effect → terminal settlement.
4. Search and browser outputs are untrusted data. A research program is
   declarative and hash-bound; page text never becomes an instruction.
5. Simulation is labelled `SIMULATED` or `MEASURED_INPUTS_ONLY` until an
   independent calibration witness exists.
6. Optional performance mechanisms are feature-gated, benchmark-gated and
   reversible. No zero-copy, platform, SOTA, or generalization claim is made
   without the matching witness.
7. Unknown, ambiguous, unauthorised, over-budget or schema-invalid actions
   fail closed and remain replay-visible.

## Decision records

The following decisions refine D-001–D-011 in master-plan §19.1.

| ID | Decision | Rationale from current evidence | Guard / rollback |
|---|---|---|---|---|
| AF-001 | The first JavaScript artifact is a QuickJS-compatible Wasm module behind the existing host-validated linear-memory packet. Javy is an optional later producer, not a second runtime authority. | The checkout already validates the Wasm wrapper, packet hashes and bounded host bridge; interpreter/cold-start evidence is still absent. | Keep Wasmtime byte-only production input. Add Javy only behind a separate artifact/schema and benchmark gate. |
| AF-002 | The JS ABI is the versioned host-validated linear-memory packet plus a narrow host function; raw script text is data, never executable Wasmtime input. | This preserves deterministic hash binding and the existing Wasm-only boundary. | Reject magic/version/header/hash/fuel drift. Roll back by disabling the optional bridge capability. |
| AF-003 | Replay uses sealed append-only segments with one writer lease and explicit recovery; mutable multi-process mmap writing is deferred. | Current evidence proves sealed segment/recovery paths, not mutable writer crash safety. | Old segments remain readable during cutover; no new format without manifest/version. |
| AF-004 | Playwright is the first browser backend behind `BrowserCell`; Browser Use/MCP adapters remain compatibility candidates. | The repository has a bounded Playwright runtime and browser artifact contract; live SSRF, permissions and descendant cleanup remain external. | Actor/observer split, HTTPS/allowlist/DNS policy and cleanup receipts are mandatory. Direct compatibility runtime cannot produce release verification. |
| AF-005 | API/CLI is the first control-plane surface. TUI/dashboard is an observation projection, not kernel authority. | The root CLI and operator API are locally reachable; adding a dashboard would not close an authority or evidence gap. | Keep control actions on the typed session API; projection failure cannot mutate the reducer. |
| AF-006 | The native hot lexical/context index is the first memory provider. Mem0/Letta-style providers may be adapters later, never sources of truth. | A no-dependency deterministic index and candidate-only semantics already exist; external memory providers add dependency and provenance rent. | Candidate refs require index-epoch/hash binding and cannot become witness or Done state. |
| AF-007 | The 100-hour protocol starts with deterministic synthetic runs and bounded local crash/recovery fixtures. Heartbeat/cron and hosted workers follow only after replay, lease, telemetry and recovery gates. | Local evidence cannot prove hosted scheduling, multi-worker recovery or long-duration resource behaviour. | Synthetic mode is labelled; no real-world side effect or production claim is inferred from it. |

## Ownership map

| Contract / state | Canonical module | Canonical owner | Python role | Evidence required before cutover |
|---|---|---|---|---|
| Mission, policy and trust subject | `core/rust/src/lab.rs`, `policy.rs` | Rust `LabMissionSpec`/controller | Compile typed input and display | Constructor matrix, matching policy hash at every boundary |
| Event sequence/state epoch | `core/rust/src/lab.rs`, `replay.rs` | Rust `LabController` | Read-only projection | Fresh-process replay and rejected-admission snapshot equality |
| Source/claim/hypothesis/experiment/observation | `core/rust/src/lab.rs` | Rust typed runtime | Materialize compatibility view | Lossless DTO schema or explicit compatibility label; typed-chain replay |
| Effect attempt/lease/settlement | `core/rust/src/execution.rs`, `resource.rs`, `lab.rs` | Execution cell + Rust registry | Invoke runner after admission | Finite attempts, idempotency, timeout ambiguity and cancellation matrix |
| Browser/network/provider/process/filesystem effect | `core/rust/src/tool_gateway.rs`, `execution.rs`, `core/python/browser_runtime_adapter.py` | Capability-scoped cell/platform adapter | Adapter only | Sink census, admission receipt, terminal receipt and external witness where required |
| Candidate retrieval/context | `core/rust/src/context.rs`, `memory/` | Native index/context cell | Candidate presentation | Epoch/hash binding; candidate cannot self-promote |
| Replay/archive | `core/rust/src/replay.rs` | Rust replay writer/reader | Export/read projection | Segment manifest, corruption/truncation and recovery witness |
| Telemetry | `core/rust/src/telemetry.rs` | Observation-only telemetry plane | Render/forward | Prove no authority mutation and redact sensitive data |

The module paths above are the P0 ownership boundary. A compatibility adapter
may call a canonical module, but it may not mint an authority event, alter the
Rust state epoch, or convert a candidate/telemetry record into a terminal
truth state. A path move is a contract change and therefore requires an ADR,
stale-reference scan, focused contract tests and a regenerated evidence epoch.

## Sprint A task cards and evidence contracts

These cards describe the smallest next verifiable packet. `BASELINE_PRESENT`
means the checkout contains the local implementation and focused tests; it is
not a cross-platform or production-readiness claim.

| Card | Owner / scope | Required output | Acceptance evidence | Fail-closed / rollback |
|---|---|---|---|---|
| A-001 Typed tool intent | `policy.rs` | `TypedToolIR` with versioned kind/resource/risk/spend hashes | Rust unit tests, schema marker, replay binding test | Reject unknown kind, scope drift or zero hashes; retain prior IR path |
| A-002 Policy closure | `policy.rs` | `PolicyFacts` + deterministic `PolicyProofTrace`/closure hash | R3/R4 hard-block and approval-expiry tests | No executor call without valid closure; disable new rule and use prior policy window |
| A-003 Human approval | `policy.rs`, `replay.rs` | `ReviewPacket`, signed token, scope replay and dual-approval proof | Expiry, replay-scope, distinct-key and helper-text exclusion tests | Expired/replayed/mismatched approval is rejected; no side effect |
| A-004 Observation envelope | `telemetry.rs` | `TelemetryEnvelope` with correlation and redaction fields | Schema round-trip and bounded telemetry tests | Telemetry loss cannot mutate authority; drop/mark degraded on overflow |
| A-005 Bench scorecard | `policy.rs`, `replay.rs` | `HarnessBenchScorecard` with raw trials and validator result | Validator rejects LLM truth claims; deterministic replay binding | Missing/raw-trial drift blocks promotion; preserve candidate-only result |

Each card must record the exact command, source revision, worktree epoch,
sample count and limitations. A passing unit test is local evidence for that
card only; it does not close M2–M4 or any external blocker.

## Implementation / no-build decision log

| Date | Decision | Evidence used | Consequence / revisit trigger |
|---|---|---|---|
| 2026-08-31 | Implement only the two audit gates and this freeze metadata in the current packet | Local closure `PARTIAL_LOCAL`; document gate and focused gate tests | No runtime authority change; revisit after fresh M2 characterization |
| 2026-08-31 | Do not add a new dependency, service, database, browser crawl or provider call | No unresolved local design question requires one; external blockers remain open | Keeps rollback trivial; revisit only with a named gate and budget |
| 2026-08-31 | Defer mutable mmap writer, vector truth, remote commit and hosted 100h workers | Existing replay/single-writer and candidate-only evidence; target gates are explicit | Any adoption requires its own schema, crash/benchmark/security evidence |
| 2026-08-31 | Keep QuickJS-compatible Wasm, Playwright, API/CLI, native lexical index as first candidates | AF-001–AF-007 and current boundary audit | Compatibility alternatives remain non-authoritative until comparative gates pass |

This log records decisions, not completion. A future implementation packet must
append a new row rather than silently rewriting the rationale for an earlier
choice.

## Migration gates

The only permitted sequence is:

```text
M0 provenance
  → M1 package/CLI ownership
  → M2 canonical reducer + projection
  → M3 trust owner
  → M4 retry/effect-cell owner
  → M5 research/browser/experiment cells
  → M6 FFI internal convergence
  → M7 replay/recovery cutover
  → M8 benchmark/operations/release
```

Each node must provide: one owner, one schema, one change budget, one
rollback, affected-callers evidence, focused tests, adversarial checks and a
recomputed worktree epoch. An open upstream contract gate prevents downstream
implementation. Compatibility paths may remain, but must be explicitly
labelled and must not emit release-grade verification.

## First implementation packet after this freeze

The next safe packet is a bounded replay/authority characterization, not a
framework expansion:

1. Add a native-vs-Python snapshot equality test for a valid typed evidence
   chain and for a rejected duplicate/stale settlement.
2. Add a test that a tampered Python projection is rejected before it can be
   used to validate a new cross-reference.
3. Add a deterministic crash-prefix fixture that verifies no open admission
   becomes `SUCCESS` after restore.
4. Record the result as a migration artifact; do not claim the complete M2
   exit until all required lanes and the owner-approved contract exist.

No new dependency, service, database, browser crawl, provider call, hosted
worker or platform-specific memory mechanism is justified by this packet.

## Explicit non-adoption decisions

Until their gates pass, do not make any of these default or authoritative:

- HTTP/3/QUIC or SIMD JSON;
- mutable mmap writers or a global allocator replacement;
- vector databases, DataFusion or ANN candidates as truth;
- CP-SAT in the hot scheduler;
- remote workers committing directly to the run log;
- quantized model tool-call authority;
- persistent browser profiles without approval/redaction/credential proof;
- WebAuthn, SMT, e-graph or Datalog expansion of the deterministic kernel.

## Definition of a safe design handoff

This freeze is ready for target-architecture elaboration because the local
ownership, package, effect, trust, retry, schema, FFI and external-boundary
facts are recorded. It is **not** a surgical-convergence authorization yet:
M2–M4 contract decisions and final provenance must be resolved and verified
before changing authority ownership.
