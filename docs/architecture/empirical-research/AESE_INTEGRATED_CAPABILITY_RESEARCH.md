# AESE Integrated Capability — Research and Evidence Ledger

**Status:** research synthesis for implementation planning; runtime implementation is not claimed

**Decision scope:** whether AESE should become a first-class capability of AEGIS, where AEGIS is both harness and orchestrator, and how AESE must participate before, during and after an external coding agent changes a project.

**Repository revision inspected:** `45480ee173be50603929b2461c1a6a0b742b7a9e`

**Documentation handoff commit:** the current commit containing this report is documentation-only; runtime evidence remains bound to the inspected revision above and is not silently upgraded to current-source proof.

**Research rule:** a passing test suite proves only the executed scope. It does not prove that the test suite is complete, that generated tests have a useful oracle, or that an unexecuted failure would have been found.

## 1. Decision in one page

The selected architecture is an **integrated AESE development control plane**, not a post-processing reviewer and not a second runtime.

```text
task intake
  -> AESE requirement and risk contract
  -> project/toolchain/test discovery
  -> agent implementation packet
  -> normal agent edits in the user's workspace
  -> AESE observes each coherent change
  -> targeted feedback and candidate test changes
  -> Lab/Rust-authorized execution receipts
  -> provisional result
  -> deep verification and replayable final scoped report
```

The following choices are supported by the current repository and by the external evidence reviewed here:

1. **AESE enters before code is written.** Waiting until an agent has already authored tests loses the opportunity to derive test obligations from the requirement and to steer the implementation toward observable behavior.
2. **The agent remains the normal coding worker.** AESE supplies contracts, feedback and bounded test operations; it does not require the agent to understand internal scheduling or evidence storage.
3. **AESE may author or modify tests freely as candidates.** Freedom is at the proposal layer, not at the final-authority layer. Candidate changes require parsing, discovery, oracle, non-weakening, fault-detection and resource checks.
4. **Rust/Lab remains the only execution authority.** AESE must not create a second scheduler, lease registry, lifecycle ledger or privileged subprocess path.
5. **Local execution is the default.** GitHub Actions is the preferred repeatable hosted evidence lane when the user or repository policy allows it. A Google Cloud VM is optional and must be treated as a bounded, cost-controlled, disposable probe—not as an always-on worker.
6. **All-language reach is a core design goal, not a claim of immediate support.** The core contracts are language-neutral; each language/framework becomes supported only after adapter conformance evidence.
7. **Optimization is non-authoritative until held-out evaluation proves it.** Impact selection, sharding, caching, mutation prioritization and predictive selection can reduce time, but they must widen conservatively and retain a full-suite fallback.

This is the right direction for the stated product goal: AEGIS users should be able to ask an agent to develop a feature while the project itself continuously supplies high-quality verification capability. It is not evidence that the capability has already been implemented.

## 2. Evidence classes

| Class | Meaning in this report |
|---|---|
| `VERIFIED` | Direct repository inspection or a bounded command run on the current checkout produced the stated observation. |
| `MEASURED` | A run produced a numeric or structured result with identity, command and environment. |
| `SOURCE-BACKED` | Primary upstream documentation or research supports the claim. |
| `INFERRED` | The conclusion follows from multiple observations but was not directly proven in this repository. |
| `NOT VERIFIED` | The required run, independent host, or held-out evaluation has not established the claim. |

No evidence in this report is a universal “quality score” for AESE. The relevant future score must be workload-specific and must prioritize false passes and critical misses before speed.

## 3. Current repository findings

### 3.1 Existing foundations worth reusing

`VERIFIED` by source inspection:

- `docs/adr/ADR-001-runtime-authority.md` assigns TaskLedger, admission, leases, transitions, cancellation and reconciliation to Rust; Python proposals are not runtime authority.
- `docs/adr/ADR-009-runtime-lanes.md` already distinguishes CPU, I/O, Python and untrusted execution lanes.
- `aegis_cognition/lab.py` already exposes mission contracts, execution-cell registration, tool admission, replay and goal evaluation primitives.
- `aegis_cognition/aese.py` already contains AESE measurement/planning primitives, so the new capability should extend and harden existing ownership rather than add a parallel engine.
- `core/python/aegis/code_intelligence.py` supplies source snapshots and change/lineage signals, but its lineage is a candidate signal and must not be treated as proof when the graph is incomplete.
- `.github/workflows/ci.yml` already emits commit-bound suite evidence for Python, Rust and three hosted platforms.
- `.github/workflows/deep.yml` already provides separate deep evidence lanes for sanitizer/Miri, fuzzing, resource benchmarking, platform probes, replay and dependency/security evidence.

### 3.2 Current blockers that affect AESE integration

`VERIFIED` by bounded reproduction on the current checkout:

```text
AdaptiveMeasurementSpec(metric="latency_ms")
baseline=NaN
observations=[1.0]*30
observed result: status="PASS", failure_reasons=("baseline_invalid",)
```

This means an invalid baseline can coexist with a `PASS` status. Any AESE contract or quality assessment built on this primitive must reject invalid numeric inputs before status evaluation and must have a regression test.

```text
_observed_critical_false_negative_status(
    {"critical-test": "ERROR"},
    {"critical-test"},
    []
)
observed result: "OBSERVED_COMPLETE_ZERO"
```

This means an invalid legacy outcome can be counted as a complete observation. The result normalizer must validate outcome values before coverage/completeness calculations.

`INFERRED` from source inspection:

- `scripts/aese_affected_closure.py` is a literal declared-edge inventory and shadow planner, not yet a complete semantic dependency graph. Unknown, dynamic, generated, plugin and watcher paths must widen rather than narrow execution.
- `scripts/aese_shadow_planner.py` is currently shadow-only. It must not become an authoritative selector until invalid outcomes, missing observations, stale revisions and critical-test misses are fail-closed.
- `ExecutionCellRegistry` binds one cell per `action_kind`; a second `tool_call` binding would create an integration conflict. AESE should use the existing trusted dispatcher or a versioned capability, not compete for the same binding.
- `ProcessExecutionCell` does not by itself prove memory isolation or complete process-tree containment. AESE reports the actual enforcement level; it never labels cooperative cleanup as sandbox isolation.
- The native Rust process path must be reviewed for child-process output deadlock, cancellation, descendant cleanup and resource enforcement before untrusted/generated tests can use it.
- The desktop protocol currently has runs-related commands but not a complete `verification.*` contract. Python allowlists, TypeScript unions, handlers, capability discovery and tests must change together.

### 3.3 Current integration seam audit

`VERIFIED` by direct source inspection on the planning checkout, with structural cross-checks from the ready codebase-memory index:

- **The codebase-memory graph is useful for discovery, not final source authority.** The index is ready and exposes the relevant package/call/test graph, but some stored line references have drifted from the current files. The implementation agent must use graph results to find candidate owners and then re-read the current source before changing a contract, runner or protocol. A stale graph result is not evidence that a current symbol or line still exists.
- **Lab already has the correct execution seam.** `aegis_cognition/lab.py` includes `tool_call` in the controller/execution-cell action kinds. `ExecutionCellRegistry` is a sealed single trusted lookup table and rejects duplicate action-kind bindings. `LabApplication._run_tool_calls` already places generic tool work behind admission/settlement fences and resolves the registered cell instead of accepting a caller-provided callable. AESE must bind through this seam or a deliberately versioned existing capability; it must not add a second `tool_call` registry, scheduler, lease ledger or privileged subprocess path.
- **The current local process cell is bounded but not a sandbox.** `ProcessExecutionCell` uses a killable child process, wall-time timeout, POSIX process-group handling and scoped Windows tree termination. Its own contract explicitly does not prove memory quotas, complete resource enforcement or cross-platform isolation. Generated tests must therefore remain behind a separately verified Lab policy; the adapter result must expose the actual enforcement level and fail closed when required controls are unavailable.
- **Desktop has a real source-snapshot boundary but no AESE session boundary yet.** `desktop_service.py` opens a workspace through `SourceMapper`, refreshes a bounded source snapshot and includes source revision/file context in live provider prompts. That is the correct place to attach session creation and change observation, but it is not yet an AESE contract or evidence ledger. AESE must consume the snapshot as an input, record its own contract/revision, and invalidate evidence when the snapshot becomes stale.
- **Desktop protocol parity is currently incomplete.** Python `ALLOWED_COMMANDS` and the router expose runs/approval/object/maintenance commands, while `desktop/src/protocol.ts` has a narrower command union and neither side has typed `verification.*` operations. Adding AESE requires a coordinated Python allowlist/router, TypeScript union/result decoders, service handlers, capability discovery and protocol tests. Updating only one side would create a version/contract split.
- **The CLI has no verification surface today.** `aegis_cognition/cli.py` currently supports `init`, `run`, `examples`, `version` and `config`. The proposed `aegis verify ...` commands must call the same service façade as desktop and future API clients, not create a CLI-only execution path.

Integration consequence: the first code change should establish one typed AESE façade and connect it to the existing desktop/source-snapshot and Lab/admission seams. It should not start by building all language adapters or by making the shadow selector authoritative. The minimum vertical slice must prove session creation, revision invalidation, one adapter discovery/execution receipt and feedback through the existing authority boundary before broader protocol or language expansion.

### 3.4 Existing AESE S1–S6 work: what is real and what is not

This repository already contains a meaningful AESE shadow program. Direct inspection of the current `quality/registry` artifacts and a focused current-checkout run produced:

```text
command: pytest tests/test_aese_inventory.py \
  tests/test_aese_statistical_calibration.py \
  tests/test_aese_s2_mapping.py \
  tests/test_aese_affected_closure.py \
  tests/test_aese_shadow_planner.py \
  tests/test_aese_validation_corpus.py -q -rs
result: 59 passed in 389.26s (0:06:29)
```

Current artifact states:

| Existing AESE stage | Current observed state | What it proves | What it does not prove |
|---|---|---|---|
| S1/S1.1 calibration | metadata/validity work exists; calibration remains insufficient for promotion | protocol and statistical-boundary discipline | calibrated real-world test-selection quality |
| S2 mapping | `S2_FAIL_CLOSED_MAPPING_COMPLETE`, 9 critical mappings, 147 unknown surfaces, selection disabled | declared records are mapped with an explicit unknown policy | all repository surfaces are mapped or safe to omit |
| S3 closure | `AFFECTED_CLOSURE_PLAN_ONLY_SELECTION_DISABLED`, 15 mapped records checked, unknowns widen to retained suite | mapped structural reachability and conservative widening | observed regression/fault detection |
| S4 shadow planner | `SHADOW_PLAN_ONLY_SELECTION_DISABLED`, one recorded `WOULD_RUN`, 161 `WOULD_SKIP` predictions, zero reuse, execution not performed | explainable predictions can be generated | the skipped tests are safe to omit |
| S5 held-out corpus | `LOCAL_SHADOW_VALIDATION_ONLY`, 6 final cases, 5 planning targets reached, critical false-negative status not measured | unknown/partial cases widen in the synthetic planner | mutation kill rate, real false negatives or production non-inferiority |
| S6 paired cost | `MEASURED_EXPLORATORY_PAIRED`, 3 warm-cache samples; legacy median 71.778817s, planner 6.937248s, selected evidence 1.521086s, median net saving 63.632245s; artifact records `measurement_reused=true` | one Rust change class can have large local exploratory savings | Python, cold builds, other change types, flaky/hidden-fault behavior or a universal speed ratio |

This changes the assessment from “AESE has only primitives” to “AESE has a serious local shadow foundation”. It does **not** change the authority decision: the current artifacts explicitly preserve `AESE_MODE=SHADOW`, `SELECTIVE_TEST_AUTHORITY=DISABLED`, `TEST_SKIPPING_AUTHORITY=DISABLED` and `EVIDENCE_PROMOTION=DISABLED`. The current plan must extend this work into an integrated development session; it must not silently promote the existing planner into runtime authority.

### 3.4 What the existing test suite proves

`MEASURED` local targeted run on Windows, project `.venv` Python 3.14.7:

```text
command: .venv/Scripts/python.exe -m pytest \
  tests/test_aese_primitives.py \
  tests/test_code_intelligence.py \
  tests/test_goal_contract.py -q -rs --durations=3
result: 145 passed, 1 skipped, 3.36s
skip: external symlink test; Windows symlink privilege unavailable
```

`MEASURED` current-SHA GitHub Actions evidence from run `34800284798`:

- head SHA: `45480ee173be50603929b2461c1a6a0b742b7a9e`;
- CI conclusion: `success`;
- the retained cross-platform evidence artifact reports the same locked pytest command on `ubuntu-latest`, `windows-latest` and `macos-14`;
- each lane reports `discovered=821`, `passed=821`, `failed=0`, `skipped=0`, `filtered=0`, `ignored=0`;
- each lane reports Python `3.14.7` and status `PROVEN` within the artifact's scope;
- the cross-platform evidence gate completed successfully at `2026-09-14T03:04:59Z`.

This is strong evidence that the current declared suite is portable for that revision and those runners. It does **not** show that AESE can detect missing tests, weak assertions, hidden regressions or harmful generated-test edits.

`NOT VERIFIED` limitations remain:

- the hosted artifact itself declares `independent_verification="NOT VERIFIED"`;
- the project-level full Rust evidence is a separate claim and must not be inferred from Python evidence;
- no held-out fault corpus has yet measured AESE's critical-miss rate;
- one local skipped symlink case is an environment limitation, not a successful security result;
- no complete all-language adapter conformance matrix exists yet.

## 4. External research that changes the design

### 4.1 Test runner contracts are not interchangeable

`SOURCE-BACKED`: pytest publishes distinct exit codes for test failures, collection errors, no tests collected, interrupted runs, internal errors, usage errors and warnings-as-errors. AESE must preserve the underlying exit semantics in `ExecutionReceipt` rather than flattening all non-zero results into “test failed”. See [pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html).

`SOURCE-BACKED`: pytest plugins can load at startup through entry points, `PYTEST_PLUGINS`, `conftest.py` and related mechanisms. An AESE host must not import arbitrary project test modules into the long-lived AEGIS process; discovery and execution belong behind the adapter/Lab boundary. See [pytest plugin authoring and loading](https://docs.pytest.org/en/8.2.x/how-to/writing_plugins.html).

`SOURCE-BACKED`: cargo-nextest provides machine-readable test listing, but the listing is not equivalent to building or executing tests. The adapter must keep discovery, build and execution states separate and preserve missing/broken listing conditions. See [cargo-nextest machine-readable lists](https://nexte.st/docs/machine-readable/list/).

### 4.2 High-quality testing needs multiple oracles

`SOURCE-BACKED`: Hypothesis stateful testing demonstrates sequence-based testing where correctness depends on state transitions, not only independent examples. This supports adding stateful/property strategies when the requirement exposes an invariant; it does not justify adding a dependency to every project. See [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html).

`SOURCE-BACKED`: Microsoft CHESS shows why systematic schedule exploration is useful for concurrency failures that ordinary randomized tests may miss. AESE should expose bounded schedule/failure-injection strategies as optional deep techniques, not pretend a normal unit suite proves concurrency correctness. See [CHESS: a systematic testing tool for concurrent software](https://www.microsoft.com/en-us/research/publication/chess-a-systematic-testing-tool-for-concurrent-software/).

`SOURCE-BACKED`: Google research on Predictive Test Selection reported high failure detection and lower infrastructure cost for a particular production workload. This supports an experimental, history-based selector with held-out evaluation; it does not prove a universal speed/quality ratio for new repositories. See [Predictive Test Selection](https://arxiv.org/abs/1810.05286) and [regression testing in CI](https://research.google/pubs/techniques-for-improving-regression-testing-in-continuous-integration-development-environments/).

### 4.3 Hosted execution has a different trust boundary

`SOURCE-BACKED`: GitHub warns that self-hosted runners can be dangerous for public repositories because forked pull requests may execute code on the runner; runner groups and repository access controls are required. Therefore AESE must prefer GitHub-hosted ephemeral runners for generic untrusted project code and must not silently use a persistent self-hosted runner. See [GitHub self-hosted runner access](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access).

`SOURCE-BACKED`: GitHub artifacts retain logs, results, screenshots and coverage for later inspection and are distinct from caches. AESE should store small commit-bound receipts and artifact references, while caches remain an optimization that can never become authoritative evidence. See [GitHub workflow artifacts](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts).

`SOURCE-BACKED`: GitHub documents script-injection risk when untrusted repository data is interpolated into workflow scripts. Generated test names, branch names, issue text and model output must be treated as data, passed as safely encoded arguments and never interpolated into shell source. See [GitHub script injection prevention](https://docs.github.com/en/actions/concepts/security/script-injections).

### 4.4 The inspected Google Cloud VM is not an automatic AESE runner

`MEASURED` bounded environment probe on `aegis-test-linux-02`, project `aegis-verification-2026`, zone `asia-southeast1-b`:

```text
machine: e2-standard-2
CPU: 2 vCPU
memory: 7.7 GiB
disk: 30 GiB, 21 GiB available at probe time
OS/kernel: Ubuntu 24.04, Linux 7.0.0-1011-gcp
Python: 3.12.3
Rust/Cargo: not installed
Git: 2.43.0
guest agent: active
```

The VM was started only for this probe, no project source was uploaded, no toolchain or service was installed, no new cloud resource was created, and it was stopped immediately afterward. Final status was measured as `TERMINATED`.

The inspected configuration also showed `enableVtpm=true`, `enableIntegrityMonitoring=true`, but `enableSecureBoot=false`, and the instance uses the project default Compute Engine service account. Those facts are a warning against treating this VM as a hardened untrusted-code sandbox.

`SOURCE-BACKED`: Google recommends least-privilege user-managed service accounts and documents that attached service-account permissions are available to users/processes connected to the VM. See [Compute Engine service accounts](https://docs.cloud.google.com/compute/docs/access/service-accounts), [Compute Engine access control](https://docs.cloud.google.com/compute/docs/access) and [VM access overview](https://docs.cloud.google.com/compute/docs/instances/access-overview).

`SOURCE-BACKED`: Shielded VMs provide Secure Boot, vTPM measured boot and integrity monitoring, with Secure Boot recommended when compatible. See [Shielded VM](https://docs.cloud.google.com/compute/docs/about-shielded-vm).

**Decision:** this VM is an explicitly requested, bounded diagnostic host only until a separate hardening and cost-control design is approved. It is not the default AESE server lane.

## 5. Alternatives considered

| Option | Benefit | Main failure mode | Decision |
|---|---|---|---|
| Post-process agent tests after coding | Small initial change | Too late to shape requirements; cannot reliably repair weak tests; misses intent and change-time feedback | Reject |
| AESE as an independent daemon with its own scheduler | Isolation in theory | Duplicate authority, leases, cancellation, replay and policy; difficult to keep evidence coherent | Reject for v1 |
| GitHub-only verification | Strong repeatability and platform matrix | Violates local-first behavior; feedback arrives too late; does not support offline/private development by default | Use as hosted evidence lane, not sole runtime |
| Immediate broad support for every language/framework | Large headline coverage | High adapter rent, inconsistent semantics, false support claims, hard-to-debug failures | Build language-neutral core plus staged adapters |
| Free AESE mutation of tests in the working tree | Maximum flexibility | Overwrites agent edits, weakens requirements, creates accidental skips/goldens, destroys provenance | Candidate patches + three-way merge + fail-closed gates |
| Static dependency selection as final authority | Very fast on known graphs | Dynamic imports, plugins, generated code, FFI, config and watcher changes create silent omissions | Shadow first; conservative widening; full fallback |
| Cloud VM as default runner | More resources | Cost leakage, credential exposure, persistent contamination, unclear isolation | Explicit opt-in only; GitHub-hosted preferred for generic CI |
| Integrated AESE control plane using existing Lab | Early steering, one authority, local/hosted modes, replayable evidence | Larger contract and adapter design | Select |

## 6. Target architecture derived from evidence

### 6.1 Ownership boundaries

```text
AEGIS orchestrator
  owns session intent, agent lifecycle and user-visible policy

AESE Python service
  owns requirement compilation, project profile, test quality plan,
  change observation, candidate test patches, assessment and reports

Rust/Lab
  owns admission, execution cells, leases, cancellation, resource policy,
  lifecycle transitions, evidence receipt authority and replay

language adapter
  owns discovery/build/run/parse for one declared toolchain family

workspace
  remains agent-visible; AESE checkpoints and merges its own changes safely
```

No model output may directly choose a shell string, filesystem path, cloud target, secret or privileged operation. It may produce typed proposals that are validated against project policy and admitted through Lab.

### 6.2 Mandatory integrated lifecycle

1. Orchestrator creates a `DevelopmentVerificationSession` before source implementation.
2. AESE compiles the user request into versioned requirements, invariants, observable outcomes, risk classes and mandatory checks.
3. AESE discovers project identity, language/framework/toolchain, existing tests, build commands, generated paths, plugin/dynamic boundaries and supported adapter level.
4. AESE emits an implementation packet containing code obligations, test obligations, allowed test operations, known risks and an explicit fallback policy.
5. The agent edits the normal workspace.
6. AESE receives coherent change events or bounded snapshots, records agent/AESE ownership, updates the contract and invalidates stale evidence.
7. AESE proposes targeted checks and, when useful, candidate test changes.
8. Lab admits execution and returns a receipt preserving command, argv, environment identity, source revision, adapter/toolchain, counts, exit semantics, timeout/cancellation, resource observations and artifact hashes.
9. AESE gives a compact provisional result and sends the agent actionable feedback.
10. Deep verification runs mutation/property/stateful/concurrency/contract/soak techniques only when the risk model justifies them.
11. A final report is scoped to exact requirements, revision, environment, adapter, checks and evidence; missing or stale evidence is not green.

### 6.3 Required service operations

The first public service façade should support:

```text
verification.inspect_project
verification.create_contract
verification.start_session
verification.get_agent_packet
verification.observe_change
verification.get_feedback
verification.propose_test_change
verification.evaluate_test_change
verification.apply_test_change
verification.request_deep_run
verification.inspect_run
verification.cancel_run
verification.resume_session
verification.read_report
```

These are logical operations, not permission to expose arbitrary subprocess execution. Desktop, CLI and a future API must call the same façade.

## 7. Quality model: what “high quality” must mean

AESE should report a vector of evidence, not a decorative scalar score:

```text
requirement coverage
invariant coverage
critical-path coverage
fault-detection evidence
negative-control correctness
mutation score for selected risk
property/stateful depth
platform/toolchain coverage
reproducibility
flakiness
resource cost
unknown/blocked scope
```

The minimum acceptance rule for a candidate test is:

- parses and is discoverable;
- runs through an adapter in the declared environment;
- has a real oracle or explicitly records why the oracle is pending;
- binds to a requirement/invariant;
- cannot weaken an existing mandatory check;
- survives a relevant known-bug, mutation or negative-control probe;
- does not rely on wall-clock, locale, random order, internet or developer state without control;
- does not leak secrets or escape the workspace/effect policy;
- records time/resource impact when materially changed;
- can be rejected or rolled back without losing the prior test version.

The model that authors a test cannot be the only authority that says the test is good. Execution, oracle behavior, seeded faults, mutation/property evidence and Lab receipts provide the independence.

## 8. Local, GitHub and Google Cloud operating policy

### 8.1 Local default

- no source upload;
- no server invocation;
- bounded local resources and explicit user-visible policy;
- local artifact references with source revision and worktree state;
- full-suite fallback when closure is incomplete;
- no claim of isolation where the platform only provides cooperative cleanup.

### 8.2 GitHub Actions lane

GitHub Actions is the preferred repeatable hosted lane for public/authorized repository validation because the project already has pinned actions, platform matrices and commit-bound artifacts. The implementation must:

- keep permissions least-privilege;
- use hosted ephemeral runners for generic untrusted code;
- pass untrusted names/text as encoded arguments, never shell interpolation;
- separate artifacts from caches;
- bind every receipt to `github.sha`, workflow/run/attempt, runner/platform, toolchain and command;
- enforce job timeouts and fail closed on missing/partial evidence;
- retain only bounded artifacts needed for report/replay;
- keep optimization lanes non-authoritative until held-out evaluation.

### 8.3 Google Cloud optional lane

Cloud execution requires an explicit consent record containing provider, project, region/zone, source/artifact scope, network policy, cost ceiling, maximum wall time, cancellation action, retention and whether secrets are allowed. Secrets are prohibited by default.

For the current free-credit VM specifically:

- do not start it for routine local feedback;
- do not install a persistent agent, Rust toolchain or large cache without a bounded experiment record;
- do not upload private source or credentials;
- do not create disks, snapshots, images, IPs, MIGs, autoscalers or additional instances;
- prefer a read-only probe or one bounded run, then stop and verify `TERMINATED`;
- treat the current default service account and disabled Secure Boot as insufficient for generic untrusted code;
- set an explicit TTL and hard cost budget in any future server adapter;
- if a cloud lane is needed, prefer a new hardened ephemeral design over silently repurposing this VM.

## 9. Implementation gates for the other agent

The implementation plan is valid only if the other agent follows these gates in order:

### Gate A — harden evidence primitives

Add regression tests and fixes for invalid baseline status and invalid legacy outcomes. Confirm no `PASS`, complete observation or final assurance state can be produced from invalid data.

### Gate B — prove one integrated vertical slice

Implement one source-changing task → requirement contract → one changed file → one changed test → one bounded adapter → Lab receipt → provisional feedback → deep run → replayable report. Do not begin all-language work before this slice has fail-closed evidence.

### Gate C — adapter conformance

For every adapter, test discovery, build, pass, fail, skip, zero tests, collection/build error, malformed output, timeout, cancellation, parameterized tests, missing toolchain, version mismatch and unsupported feature. Publish a level (`L0` through `L3`) per language/framework/toolchain/platform.

### Gate D — test-authoring safety

Validate candidate patch, path boundaries, original-versus-candidate behavior, requirement binding, oracle, non-weakening, mutation/negative controls, resource impact and three-way merge. A conflict stops application; it must not be auto-overwritten.

### Gate E — selection promotion

Run paired A/B/C evaluation:

```text
A = existing full workflow
B = AESE impact-aware scheduling without authoring
C = scheduling plus candidate test authoring/assessment
```

Use the same revision, toolchain, platform class, seeded faults and held-out changes. Report critical misses, false passes, invalid evidence, flakiness and regressions before time/CPU/token/cost. Keep selection shadow-only until no unacceptable quality regression is shown.

### Gate F — external execution safety

Verify local-only default, explicit cloud consent, cancellation, timeout, cost/TTL guard, source redaction, artifact retention, replay identity and stop/cleanup behavior. A cloud run that cannot prove these properties is `BLOCKED`, not successful.

## 10. Research gaps that must remain visible

These are not allowed to be hidden by a broad “all languages supported” label:

- actual adapter conformance for languages/frameworks not yet implemented;
- held-out detection rate for missing/weak tests;
- mutation score and critical-fault corpus for each adapter family;
- dynamic plugin/generated/FFI dependency closure completeness;
- process-tree/resource enforcement on each host OS;
- macOS and Windows isolation capabilities beyond hosted smoke evidence;
- cloud cost per run and cancellation latency;
- signed artifact provenance and retention behavior for any future server lane;
- test flakiness under repeated and parallel execution;
- whether model-authored tests improve fault detection after accounting for their execution cost.

## 11. Final recommendation

Proceed with the integrated-control-plane plan, but make the first implementation slice deliberately small and evidence-heavy. The largest risk is not lack of test-generation freedom; it is allowing a plausible generated test or a partial impact graph to become authoritative without an independent oracle and a complete receipt.

The implementation agent should therefore begin with evidence hardening and the single vertical slice, then expand adapter coverage. It should not begin with a cloud service, a second scheduler, a universal language claim, or predictive selection.

## 12. Primary references

- [pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html)
- [pytest plugin loading](https://docs.pytest.org/en/8.2.x/how-to/writing_plugins.html)
- [cargo-nextest machine-readable listing](https://nexte.st/docs/machine-readable/list/)
- [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html)
- [CHESS systematic concurrency testing](https://www.microsoft.com/en-us/research/publication/chess-a-systematic-testing-tool-for-concurrent-software/)
- [Predictive Test Selection](https://arxiv.org/abs/1810.05286)
- [Google regression testing in CI](https://research.google/pubs/techniques-for-improving-regression-testing-in-continuous-integration-development-environments/)
- [GitHub self-hosted runner access](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access)
- [GitHub workflow artifacts](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)
- [GitHub script injection](https://docs.github.com/en/actions/concepts/security/script-injections)
- [Google Cloud service accounts](https://docs.cloud.google.com/compute/docs/access/service-accounts)
- [Google Cloud access control](https://docs.cloud.google.com/compute/docs/access)
- [Google Cloud VM access overview](https://docs.cloud.google.com/compute/docs/instances/access-overview)
- [Google Cloud Shielded VM](https://docs.cloud.google.com/compute/docs/about-shielded-vm)

## 13. Evidence status at publication

| Item | Status |
|---|---|
| Current local AESE targeted tests | `MEASURED`: 145 passed, 1 skipped, 3.36s |
| Current AESE registry/planner validation | `MEASURED`: 59 passed, 389.26s |
| Current-SHA GitHub CI | `MEASURED`: run `34800284798`, success |
| Current-SHA three-platform Python evidence | `MEASURED`: 821/821 on Ubuntu, Windows and macOS lanes |
| GCP VM environment probe | `MEASURED`: bounded probe; VM stopped and verified `TERMINATED` |
| AESE integrated runtime implementation | `NOT VERIFIED`: this document is a plan/research artifact |
| AESE held-out quality improvement | `NOT VERIFIED`: no corpus/evaluation yet |
| Deep GitHub run `34800294483` | `MEASURED`: terminal `success`; Miri/sanitizer, fuzz, native platform, resource, security/replay/dependency and completeness jobs all succeeded on commit `45480ee173be50603929b2461c1a6a0b742b7a9e` |
| Deep fuzz artifact interpretation | `MEASURED` but limited: the commit-bound artifact was downloaded and its four target logs were zero bytes; this supports no observed fuzz failure in that bounded run, not coverage, mutation strength, or absence of latent bugs |
