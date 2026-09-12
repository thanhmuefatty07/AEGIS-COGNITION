# AEGIS — Final Architecture Design-Closure and Evidence Reconciliation
WORKTREE_EPOCH: f510bd257dda8df093ba61c3a03bc5693e9a0f65a31094c0b737905b3ee9d499
HEAD: 06e2a00e6d18a1fb1ca0edf125aae596ecc6068c
STATUS: PARTIAL_LOCAL
generated_at: 2026-09-12T11:39:55.757574+00:00
method: aegis-design-closure-reconciliation-v1; direct Git/filesystem/source inspection, prior artifact hash reuse, disposable wheel reconciliation, bounded local probes
limitations: local evidence is partial; external-only closure is explicit below

## RECONCILED CONTRADICTIONS
- v63 directory and all four documented records are absent; the documented v63 wheel hash is not replayable. The replacement wheel is an artifact-missing recovery, not proof of v63.
- Root wheel clean import/native/CLI evidence is separate from the core bridge. The current core-owner wheel has no `aegis` console script, but the retained combined venv is a pre-M1 probe and still reports the old collision; a fresh dependency-complete combined runtime probe remains required.
- The prior lexical side-effect graph mixed strict Lab fencing with compatibility/operator paths; this pack separates those paths and names bypass conditions.
- The prior count of 93 PyO3 candidates is not the registered API count; source registration is measured separately (59 `#[pyfunction]`, 59 wrappers, one `#[pymethods]` block).
- Historical snapshots are retained in `docs/archive/legacy`, outside the Cargo workspace and current runtime.
- The performance baseline is reused because the equivalent workload was not repeated and no new campaign was authorized; reused measurements are not source-fresh.

## NEW DESIGN-CRITICAL FACTS
- Packaging owner migration is `UNKNOWN`: root owns `aegis`, the newly built core bridge wheel has no duplicate console script, and the old combined runtime probe must be recreated after dependency-complete installation.
- `AuthorityMode` packaged probe is NOT VERIFIED; compatibility and native authority contexts remain labelled by the legacy flag.
 - The current working-tree M4 settlement fence is locally proven by the retained provider-fence packaging record and NOT RECORDED-test regression gate: experiment, research, browser action/observation, skill, and cancellation receipts require exactly one open event-ledger admission; duplicate identities, stale/unknown admissions, and input/policy hash mismatches fail closed without appending another event. This does not prove external provider idempotency or opaque SDK/user-runner retry behavior.
- Lab-owned gateway, explicit generic-tool, experiment and simulation attempts now require one finite positive deadline (`gateway_timeout_seconds`, `tool_timeout_seconds`, `experiment_timeout_seconds` or `simulation_timeout_seconds`), derive a deterministic 64-hex idempotency key from mission/execution/input/policy identity, and carry both fields through local admission/settlement receipts; a bounded `asyncio.wait_for` timeout settles the execution as `TIMED_OUT`. The required post-completion `memory.index_session` effect now has the same finite timeout/idempotency fence. Explicit `skill_requests` likewise require a finite positive timeout and settle admitted failures/timeouts as hash-bound `REJECTED` receipts. A mission-bound finite envelope now also bounds observed Lab-owned effect admissions (`max_external_attempts`, default `8 * (max_steps + 1)^3`) and is replay-bound in Python/Rust snapshots. Physical network/browser requests, provider idempotency, opaque SDK/user-runner retries, and external effect reversal remain NOT VERIFIED.
- The six-lane fresh-process replay witness is NOT VERIFIED; no claim is made beyond the other local snapshot tests.
- Python `LabRun` is mutable and payload-bearing; Rust `LabController` conditionally materializes valid typed records while retaining a separate adapter projection. Opaque compatibility labels and adapter-only fields remain projection-only, so projection admission does not prove a single lossless reducer.
- Source-level field comparison proves semantic/lossy divergence for Mission, Source, Claim, Hypothesis, Experiment, Observation, Artifact, Event, Replay, ExecutionCell, Trust and Retry.
 - Local trust defaults are inconsistent: Agent/Lab `DEV`; AegisAdapter/evidence and Rust `PROD` when unset.
 - The trust-policy schema and subject digest now have one local Python primitive at `core/python/aegis/trust_policy.py`; compatibility defaults remain contextual (`DEV` for Agent/Lab, `PROD` for standalone evidence), while direct native-capable `LabRun` construction and native-required `LabApplication._gateway` now derive/propagate the canonical subject (active-run hash wins when present). Cross-cell/Rust policy receipts remain open.
- A finite mission-bound envelope now exists for observed Lab-owned effect admissions and is enforced/replayed by Python and Rust; a physical/global external-attempt bound and end-to-end idempotency guarantee remain NOT VERIFIED because provider, SDK, user-runner and descendant behavior is open.
- `SessionSearchIndex::new(...).unwrap()` is safe for the measured nonzero constant hash and contained by `py_safe`; current `ffi.rs` contains no `Box::leak` in the previously targeted request/error wrappers.
- No strong delete candidate is proven. `core/rust/src/ffi.rs` is the sole strong split input; no split was performed.

## PACKAGING TRUTH
- Package/CLI ownership has a local source/wheel decision and regression gate, but a dependency-complete fresh combined runtime probe and final-SHA provenance are still required. State authority, trust owner and retry owner remain open before surgical convergence.
Fresh source/wheel/combined-runtime owner evidence is in `packaging_truth.json`; stale pre-M1 environments are explicitly historical.

## NESTED MIRROR TRUTH
Historical files under `docs/archive/legacy` were relocated from nested project trees. Per-file history and documentary references, canonical twin hashes, and workspace membership checks are recorded in `rust_mirror_truth.json`.

## AUTHORITY TRUTH
- The native/Python authority probe is NOT VERIFIED; the current code still has separate typed and projection representations and no lossless cross-language reducer is proven. See `state_divergence.json` and `schema_graph.json`.
- The planned Python-projection mutation probe is NOT VERIFIED; native/Python mutation divergence remains unresolved.

## SIDE-EFFECT TRUTH
Strict Lab provider/search/browser/process paths admit before effect and settle locally. Non-Lab provider calls, compatibility callbacks, benchmark/operator subprocesses, CLI writes and scripts remain independently executable. See `side_effect_chains.json` and `side_effect_bypasses.json`.

## TRUST TRUTH
The local probe measured Agent/Lab `DEV`, AegisAdapter/evidence `PROD`, and Rust default `PROD` with no provider call. Validated `AgentConfig` missions and native-capable direct `LabRun` construction now create and propagate a 64-hex policy subject, and the packaged Lab probe binds the same subject to the native mission; direct compatibility defaults remain split. This is a local policy contradiction with bounded mission propagation, not a production-security proof. See `trust_truth.json`.
- Cross-cell trust-subject propagation is NOT VERIFIED in the packaged probe.

## RETRY TRUTH
The code gives finite local formulas (`P`, `G`, `T`, `E`, `S`), a one-invocation/no-retry `ProcessExecutionCell` bound, local gateway/tool/experiment/simulation idempotency/deadline contracts, and a mission-bound envelope for observed Lab-owned effect admissions. SDK retries, user runners, physical browser/network subrequests, external receipt idempotency and timeout commit ambiguity still prevent a physical/global bound. See `retry_truth.json`.

## FFI TRUTH
Registered surface counts and hazard evidence are source-based. The static-index unwrap has a proved input invariant plus panic-to-PyErr defense; the previously identified wrapper `Box::leak` calls are absent from current source after a bounded safety fix. No long-run allocation campaign or full FFI split was performed.

## RUST PUBLIC API TRUTH
Public declarations are classified by source exposure and consumer evidence; `ffi.rs` is binding surface, `lab.rs`/`replay.rs` are crate-internal authority candidates, and compatibility/POC surfaces remain separately labelled. See `rust_public_surface.json`.

## STRONG DELETE CANDIDATES
None proven. Historical mirror/docs may only be deleted after owner-approved retention and reference review.

## STRONG SPLIT CANDIDATES
`core/rust/src/ffi.rs` is a strong cohesion/split input because binding, conversion, controller, error and hazard responsibilities co-reside. This is a design input only; no source split occurred.

## PERFORMANCE BASELINE
`performance_baseline.json` is `PASS_REUSED_PRIOR_LOCAL_PROBE`; no new benchmark claim is made. Provider, browser, hosted and cross-platform performance remain unverified.

## LOCAL UNKNOWNS REMAINING
- Final owner-approved SHA/current.json provenance is absent; the fail-closed placeholder was not edited.
- `evidence_consistency_gate.py` is `FAIL` by design at this checkout: `current.json` and remediation/suite records still carry `CHECKOUT_HEAD` instead of the actual 40-character `HEAD`.
- Python/Rust canonical authority, snapshot envelope and lossless projection contract remain undecided.
- DEV/PROD trust owner and retry owner/idempotency contract remain undecided.
- Bounded typed-chain and replay-chaos samples are now retained with local hashes; full provenance/retention for missing v63, independent rebuild/signature and hosted restore samples remains unavailable.

## EXTERNAL-ONLY UNKNOWNS
Linux/macOS/Windows native enforcement; hosted queue/lease/telemetry/restore; multi-machine ordering/clocks; provider SDK retry/idempotency and receipts; live browser SSRF/permissions/descendant cleanup; hardware/electrical calibration; independent scientific/generalization benchmarks; signed release attestation and repository-owner final manifest.

## ARTIFACT PATHS
- docs/architecture/design-closure/AEGIS_FINAL_DESIGN_CLOSURE.md
- docs/architecture/design-closure/design_closure.json
- docs/architecture/design-closure/artifact_retention.json
- docs/architecture/design-closure/cohesion_candidates.json
- docs/architecture/design-closure/config_precedence.json
- docs/architecture/design-closure/data_movement.json
- docs/architecture/design-closure/delete_candidates.json
- docs/architecture/design-closure/dynamic_reachability.json
- docs/architecture/design-closure/entrypoint_truth.json
- docs/architecture/design-closure/error_semantics.json
- docs/architecture/design-closure/external_boundary.json
- docs/architecture/design-closure/ffi_actual_surface.json
- docs/architecture/design-closure/invariant_matrix.json
- docs/architecture/design-closure/packaging_truth.json
- docs/architecture/design-closure/performance_baseline.json
- docs/architecture/design-closure/retry_truth.json
- docs/architecture/design-closure/rust_mirror_truth.json
- docs/architecture/design-closure/rust_public_surface.json
- docs/architecture/design-closure/schema_graph.json
- docs/architecture/design-closure/side_effect_bypasses.json
- docs/architecture/design-closure/side_effect_chains.json
- docs/architecture/design-closure/state_divergence.json
- docs/architecture/design-closure/trust_truth.json
- artifacts/local-runtime/fresh-typed-chain-20260831/typed_chain_replay.json
- artifacts/local-runtime/replay-chaos-20260831/replay_chaos_scorecard.json
- artifacts/local-runtime/m2-all-lane-fresh-replay-20260901/m2_all_lane_fresh_replay.json
- artifacts/local-runtime/provider-fence-20260831/provider_fence_packaging.json

DESIGN_EVIDENCE_STATUS:
PARTIAL_LOCAL
SAFE_TO_DESIGN_TARGET_ARCHITECTURE:
YES
SAFE_TO_BEGIN_SURGICAL_CONVERGENCE:
NO

No broad feature, refactor, delete, rename, migration, optimization, uncontrolled crawl, fuzz, soak, stress, provider call, or live browser run was performed. The bounded M2 snapshot guard, six-lane fresh-process replay witness, conditional typed-materialization hardening, local M3 trust-policy hash binding (including native-capable direct `LabRun` construction), and targeted FFI wrapper leak safety fix are recorded in the master plan and verified by the local gates above; global trust-owner unification remains open.
