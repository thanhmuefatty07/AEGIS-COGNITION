---
document_id: AEGIS-AGENT-GOAL-TARGET-PRODUCT-AUDIT
document_type: primary-source-product-semantics-and-integration-research
status: RESEARCH_COMPLETE_INTEGRATION_IN_PROGRESS
authority: evidence-first-agent-method
created_at: 2026-09-09
last_verified_at: 2026-09-10
applies_to_checkout: working tree on main; checkout is dirty because concurrent agents own unrelated changes
scope: Google Antigravity, OpenAI Codex, Anthropic Claude Code; mapping to AEGIS-Cognition Agent Lab
---

# Audit: goal/target semantics in Antigravity, Codex, and Claude Code

## Executive result

The three products solve related but different problems. Treating them as interchangeable
"sandboxes" or as evidence that a generic `goal = prompt` design is sufficient would be a
category error.

| Product | Strongest observed primitive | What it does not establish | Reusable lesson for AEGIS |
|---|---|---|---|
| OpenAI Codex | A thread-scoped `ThreadGoal` with objective, lifecycle status, token budget, usage accounting, and runtime stop behavior | The public goal shape does not contain machine-checkable success predicates or an evidence verdict; `complete` is not proof of correctness | Make goal state durable and controller-owned, but add acceptance predicates and independent verification |
| Google Antigravity | Project-scoped agent work, worktree selection, implementation-plan/artifact review, browser/editor/terminal execution, and granular permission resources | The inspected public docs do not expose a first-class `goal_id`/objective/status contract or prove artifact correctness | Separate project/scope from goal; make plans and evidence reviewable artifacts with an explicit human gate |
| Anthropic Claude Code | Session/prompt plus plan mode, resumable/forkable sessions, context-isolated subagents, permission modes, hooks, and optional worktree isolation | The inspected public docs do not expose a durable goal entity or formal success predicate; `CLAUDE.md` is context, not a hard enforcement layer | Keep session, task, goal, policy, and evidence as separate objects; enforce hard controls below model context |

**Recommendation:** AEGIS should implement an evidence-first `GoalContract` above the existing
`LabRun`/execution-cell architecture. It should combine Codex-like durable accounting, Antigravity-like
project/artifact review, and Claude-Code-like explicit permission/session/subagent boundaries. It
should not copy any vendor's product surface wholesale, and it should not replace the current
Rust authority, replay, admission, or evidence archive.

The highest-value distinction is:

```text
objective       = what outcome the user wants
target          = which versioned resources the run may affect or inspect
acceptance      = how success can be checked
policy          = which actions are permitted
budget          = when execution must stop
evidence        = what supports each claim
verdict         = what the verifier can actually establish
```

None of those fields should be inferred from the others. In particular, a sandbox boundary is a
security control, not a success criterion; a plan is an intention, not an experiment result; and an
agent saying “done” is not a verified verdict.

## Evidence discipline

This report uses the following evidence classes:

| Class | Meaning in this report |
|---|---|
| `MEASURED` | Observed directly in this checkout or by a bounded command/probe. |
| `SOURCE-BACKED` | Present in official vendor documentation or official upstream source at the cited snapshot. |
| `INFERRED` | A design implication derived from one or more cited facts. |
| `ASSUMED` | A working assumption required to propose an AEGIS integration. |
| `UNKNOWN` | Not established by the sources or probes inspected. |
| `CONFLICTED` | Sources disagree or the same behavior is version/surface dependent. |
| `NOT VERIFIED` | A claim would require a live product task, hostile test, or environment that was not available. |

The report intentionally does not rank the products by coding quality, speed, model intelligence,
or security. The sources are largely vendor documentation and upstream implementations; they are
appropriate for product semantics, but not independent evidence of outcome quality or security.

The Python `GoalContract` JSON hash and Rust GT96 internal hash are intentionally separate domains
and encodings. The current bridge validates the Python contract JSON at the native mission boundary;
it must not be described as a single lossless cross-language contract protocol.

## Method and reproducibility

### Sources and retrieval

Research used primary sources first:

1. Official Antigravity documentation and blog.
2. Official OpenAI Codex source repository, app-server schemas, local CLI help, and OpenAI engineering documentation.
3. Official Claude Code documentation and the official Claude Code repository.
4. Existing AEGIS source, plans, evidence manifests, and prior empirical report.

Internet retrieval used the `agent-reach` workflow. `agent-reach doctor --json` reported:

- `web`: `ok`, Jina Reader active.
- `github`: `warn`, `gh CLI` active but its status probe timed out in the doctor check.
- `exa_search`: `off` because `mcporter`/Exa was not installed.

Therefore this report does **not** claim Exa coverage. It used official web pages and `gh`/raw
GitHub retrieval where available. The exact retrieval date is 2026-09-09, Asia/Saigon.

### Local probes

| Probe | Result | Evidence class | Limitation |
|---|---|---|---|
| `codex --version` | `codex-cli 0.116.0` | `MEASURED` | Installed CLI version is not asserted to equal the upstream source snapshot below. |
| `codex --help` | Exposes `exec`, `app-server`, `cloud`, `sandbox`, `resume`, `fork`, explicit sandbox and approval policies | `MEASURED` | Help output proves CLI surface, not successful model execution. |
| `codex exec --help` | Supports non-interactive prompt, `--ephemeral`, JSONL events, output schema, sandbox and approval options | `MEASURED` | No paid/live model task was run in this audit. |
| `codex cloud exec --help` | Accepts a task query, environment, branch, and best-of-N attempts | `MEASURED` | Does not prove cloud availability or task quality. |
| Codex `ThreadGoalSetParams.json` at upstream commit `73a1148c9c775c2a4616ce5096291740a00ed68a` | Parsed fields: `threadId`, `objective`, `status`, `tokenBudget`; statuses include active/paused/blocked/usageLimited/budgetLimited/complete | `MEASURED` + `SOURCE-BACKED` | Schema is from upstream `main`, not necessarily the installed CLI binary. |
| Official source URL checks | GitHub/raw Codex and Antigravity documentation returned HTTP 200; old Anthropic documentation URLs returned 404 while the current `code.claude.com` pages were found through official search | `MEASURED` | HTTP `HEAD` behavior is not a semantic validation; OpenAI page returned 403 to `HEAD` although official search/open succeeded. |
| Antigravity executable discovery | `agy.exe` exists at `C:\Users\ADMIN\AppData\Local\agy\bin\agy.exe` | `MEASURED` | A version/help invocation did not terminate promptly; no GUI task was driven. |
| Claude Code executable discovery | No `claude` command was available in the active PowerShell path | `MEASURED` | Claude Code was audited from official docs/source, not locally executed. |

### AEGIS baseline

The existing repository already contains most of the high-value substrate needed for this research
direction:

- `aegis_cognition/lab.py`: `LabRun`, admission/settlement of tool executions, `ProcessExecutionCell`,
  `ExecutionCellBinding`, `ExecutionCellRegistry`, and `LabApplication`.
- `core/rust/src/sandbox.rs`: `WasmtimeSandbox` and Wasm execution.
- `core/rust/src/execution.rs`: untrusted-process execution through a resource-controller boundary.
- `core/rust/src/resource_platform.rs`: Linux cgroup v2, Windows Job Object, and cooperative/platform
  controller implementations with explicit capability levels.
- `aegis_cognition/runtime.py`: explicitly non-authoritative fallback responses when the native runtime
  is unavailable.
- `docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md`: already defines the intended split between a
  `LabController`, research/experiment/evidence planes, and execution cells.
- `docs/architecture/empirical-research/EMPIRICAL_RESEARCH_REPORT.md`: prior local tests and literature
  research, including ToolSandbox, AgentDojo, OSWorld, AndroidWorld, τ-bench, replay, sandbox, and
  hardware evidence.

At report creation, no tracked source/config/test file was modified for this report. This was
deliberate because the checkout contained concurrent-agent changes outside this research artifact;
the verification update below records the subsequent, bounded integration slice.

## Verification update — 2026-09-10

The implementation work now has a bounded integration slice; this supersedes the older statement
above that no source was modified, but does not close provider/kernel isolation or release gaps.

| Change | Evidence | Scope and limitation |
|---|---|---|
| `LabRun.controller_context()` and restart snapshots expose bounded objective, typed goal/target metadata, lifecycle projection, evidence records, gaps, and replay cursor | `tests/test_lab_progress_context.py` plus `tests/test_goal_contract.py`: direct assertions pass, including tampered-progress restore rejection | Projection quality and boundedness; a verdict is present only after a contract-bound `GoalVerification` receipt is appended; this is not automatic crash-resume or semantic research proof |
| Rust GT96 target hash source contains an escaped byte-string delimiter rather than a raw NUL byte | Direct byte scan reports `nul_count=0`; `rustfmt --edition 2024 --check core/rust/src/gt96.rs` passes | Source hygiene is verified; the focused native Lab build/test passes, while the packaged extension and release matrix remain `NOT VERIFIED` |
| Per-step `progress_checkpoint` is admitted and settled as a `state_write` effect | Python success/failure regression passes; Rust manifest allowlist test was added | Durable checkpoint contract; automatic crash resume is not implemented |
| `Agent(..., lab=True)` binds the checkpoint callback to the existing `ConversationManager` execution | Application wiring regression passes | Uses conversation persistence; hosted writer, multi-process recovery, and resume orchestration remain open |
| Python `GoalContract` now separates objective, target, acceptance predicates, policy digest, budget, evidence policy, and verifier progress | `tests/test_goal_contract.py`: `28` tests pass; Lab snapshot/context tests pass | Explicit v1 contracts reject unknown/missing fields, unbound targets, missing evidence refs, malformed snapshots, unknown verdict inputs, missing execution bindings, unsupported Lab record-type policies, and undeclared generic external effects; independent verification requires a policy-listed verifier identity (the receipt's `independent=true` flag is not sufficient), binds to the exact generation, hashes the receipt, and is append-only; complete receipts may reference retained source, claim, hypothesis, experiment, and observation records, satisfy the configured source quorum, and contain every required Lab record type; Python/native hash vectors are asserted explicitly; progress snapshots round-trip only against the exact contract; evolution rejects stale writes, verdict rewrites, retargeting, capability expansion, and weakening or replacing existing predicates; child goals now require explicit parent lineage, preserve every parent acceptance predicate, and may only narrow scope, target capabilities, non-goals, and budget; the Lab exposes a fixed deterministic evaluator vocabulary without executing contract-supplied code; legacy missions remain an unbound draft for compatibility |
| Rust GT96 contract removes mutable criterion status from the immutable definition and binds target, author, and effective evolution epoch | Current evidence: `15` GT96 tests, `24` native Lab tests and `508` full no-default-features Rust tests in `artifacts/verification/full-rust-lib-20260910-r3.txt`; Rust 1.98.1 current-source rebuild passes locally; file-scoped rustfmt passes | The native mission boundary now receives canonical Goal/Target JSON and validates the strict v1 field set, schema, objective, bound target, policy, acceptance, BLAKE3 contract hash, child lineage, typed Goal Verification receipt shape, exact generic external-effect keys, duplicate/untrimmed target-scope rejection, and ambiguous URI-root rejection; it still does not execute evaluator code, provide cryptographic verifier attestation, or enforce OS isolation |
| Native Python integration | `artifacts/local-release-20260910-current/release-install-smoke.json`: wheel install return code 0, import return code 0, Windows 11 / CPython 3.14.7, status `PROVEN` for that retained artifact | This proves local packaging/import for that Windows wheel only; the latest current-source packaging gate is `19/19` with `overall_ok=true` in `artifacts/production_packaging_smoke_gate_report.json`; the full cross-platform wheel matrix/runtime semantics/production readiness remain open |
| Goal target egress and local external-effect coordination | `artifacts/verification/goal-target-lab-regression-20260910.txt`: `384` scoped tests (`29` Goal/Target + `355` Lab runtime) | Explicit host-based research/browser/generic network reads and generic local paths are narrowed or rejected against the target; read/write roots support semantic descendant narrowing while sibling expansion is rejected; exact declared external keys receive local advisory leases, including cross-process contention; provider-side idempotency, DNS/proxy/kernel containment, hosted locks and multi-machine behavior remain `NOT VERIFIED` |
| URI target-scope hardening | Test-first regression rejects `fixture://dataset/input/../secret`, encoded separators and backslashes; literal `.` segments are normalized for candidate paths; native Lab rejects ambiguous read/write URI roots | Design follows the URI dot-segment removal rule in [RFC 3986 §5.2.4](https://www.rfc-editor.org/rfc/rfc3986#section-5.2.4); this is a lexical/admission guard, not symlink, provider or kernel containment |
| Heterogeneous resource placement | Source review of `placement.rs` and `runtime.py` | Current plan selects one executor and one data tier; multi-stage transfer/pipeline measurement and a real GPU/iGPU adapter are not verified |
| Current local toolchain | `uv run python` = CPython 3.14.7; `native_runtime_available=True`; `uv 0.12.9`; Rust 1.98.1 | Current Windows checkout only; the older 3.11/native-unavailable probe remains historical |

Latest focused verification: `tests/test_goal_contract.py` passes 29 tests and
`tests/test_lab_runtime.py` passes 355 tests under CPython 3.14.7. The current
Rust focused runs pass 15 GT96 tests and 24 native Lab tests; the current full
no-default-features Rust run passes 508 tests with 0 failures. This evidence is
local Windows scope only.

Historical focused verification: `tests/test_goal_contract.py` plus
`tests/test_lab_progress_context.py` passes 25 tests;
the combined Goal/Target–Lab command (`tests/test_goal_contract.py` plus
`tests/test_lab_progress_context.py`) passes 25 tests under the project `uv`
toolchain; and the current `uv run python -m pytest -q tests/test_lab_runtime.py`
rerun passes 353 tests under CPython 3.14.7. A separate CPython 3.11 run also
passed 14 and 353 tests, providing compatibility evidence. The last retained
Rust focused runs pass 24 native Lab tests and 15 GT96 tests after the latest
Goal Verification and cross-language canonicalization changes, including native
recomputation of the receipt hash and independent Python/Rust hash vectors.
The current full no-default-features Rust run passes 508 tests with 0 failures
on the local Windows checkout; the retained ledger is
`artifacts/verification/full-rust-lib-20260910-r3.txt`.
The full cross-language extension/release matrix is still `NOT VERIFIED`.
Ruff, Pyright, Python compilation, and file-scoped rustfmt checks pass for the touched contract/Lab files.
The earlier native Python smoke rejected a tampered target; the current release-install smoke now
proves wheel installation/import on Windows, but not semantic parity across the full matrix. The
The current full Python run passes `632 tests` with `1 skipped` and exit code 0
in `586.74s` under CPython 3.14.7 Windows with an external basetemp; the
retained ledger is `artifacts/verification/full-python-suite-20260910-r4.txt`.
The current focused Goal/Target + Lab runtime command passes `384/384`
(`29/29` Goal/Target and `355/355` Lab). These are local Windows evidence only;
hosted/Tier-1/OS/multi-machine/production gates remain `NOT VERIFIED`.
These are local Windows evidence only. Whole-workspace formatting is also
`NOT VERIFIED` because unrelated agent-owned Rust files currently have pending
formatting differences. Cross-platform CI, provider/kernel egress containment,
automatic resume, hosted single-writer authority and multi-machine lease
evidence remain `NOT VERIFIED`.

# 1. Definitions needed for an Agent Lab

The vendor products use overlapping language, but AEGIS needs stricter nouns.

| Term | AEGIS meaning | What it must not mean |
|---|---|---|
| Goal | Durable user-level outcome contract with lifecycle, budget, scope, acceptance, and evidence requirements | Just the last prompt or a model's plan |
| Objective | Human/model-readable description of the desired outcome | Proof that the outcome is achievable or achieved |
| Target | Versioned resource set and intended effect surface: repository commit, dataset snapshot, browser origin, file roots, service, or experiment cell | A vague label such as “the project” |
| Task | Bounded attempt to advance one goal, with an execution environment and attempt identity | The whole multi-day goal |
| Thread/session | Conversation and event-history transport for one agent interaction | Authoritative project state |
| Plan | Proposed sequence, assumptions, and expected changes | A verification result |
| Artifact | Reviewable output with provenance, such as a plan, diff, browser recording, test report, or evidence bundle | A trusted result merely because it is formatted nicely |
| Policy | Controller-enforced capability and side-effect rules | Instructions placed only in Markdown context |
| Budget | Controller-owned token, time, cost, attempt, resource, or rate limits | A prompt asking the model to stop |
| Evidence | Observation, test, trace, source, or measurement linked to a claim | Model narration without provenance |
| Verdict | `VERIFIED`, `INCONCLUSIVE`, `BLOCKED`, `FAILED`, or `INVALID` based on explicit checks | `complete` used as a synonym for correct |

The word “target” is especially important. Codex's current `ThreadGoal` exposes an objective and
thread/budget metadata, while its thread start surface separately carries `cwd`, environment roots,
sandbox, approval policy, model, and instructions. This is evidence that intent and execution target
are distinct concerns even within one product. AEGIS should make that distinction explicit rather
than leaving it to prompt wording.

# 2. OpenAI Codex audit

## 2.1 What is directly established

The official Codex source snapshot at commit
[`73a1148c9c775c2a4616ce5096291740a00ed68a`](https://github.com/openai/codex/tree/73a1148c9c775c2a4616ce5096291740a00ed68a)
contains a dedicated goal extension and app-server protocol.

### Public wire shape

[`ThreadGoalSetParams.json`](https://raw.githubusercontent.com/openai/codex/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/app-server-protocol/schema/json/v2/ThreadGoalSetParams.json)
defines a request keyed by `threadId`, with optional `objective`, `status`, and `tokenBudget`.
The wire status enum is:

```text
active | paused | blocked | usageLimited | budgetLimited | complete
```

The generated wire type
[`ThreadGoal.ts`](https://raw.githubusercontent.com/openai/codex/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/app-server-protocol/schema/typescript/v2/ThreadGoal.ts)
contains `threadId`, `objective`, `status`, `tokenBudget`, `tokensUsed`, `timeUsedSeconds`,
`createdAt`, and `updatedAt`. It does **not** expose the internal goal generation identifier.

### Internal persistence and concurrency

The upstream state model
[`thread_goal.rs`](https://github.com/openai/codex/blob/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/state/src/model/thread_goal.rs)
contains an internal `goal_id`, objective, status, token budget, token usage, and timestamps.
[`goals.rs`](https://github.com/openai/codex/blob/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/state/src/runtime/goals.rs)
uses the internal identifier as an expected value when updating/accounting state. This is an
optimistic-concurrency defense against stale goal updates; it is not evidence that the model can
define correctness predicates.

The source also shows these semantics:

- A thread can have one unfinished goal; creating another unfinished goal is rejected.
- Replacing a goal creates a new internal goal identifier and resets token usage.
- Goal progress is accounted through runtime state, not only at final response time.
- Budget exhaustion can produce `budget_limited`; usage limits and execution failures can stop an
  active goal with distinct statuses.
- A turn error or unavailable execution path can transition an active goal to `blocked`.
- Runtime code serializes goal-state mutation and flushes accounting before operations such as fork.

These are unusually strong primitives for an agent product, but they remain execution-accounting
semantics. They are not a theorem that the goal was achieved.

### Thread, task, and environment surfaces

The official Codex engineering description
[`Unlocking the Codex harness`](https://openai.com/index/unlocking-the-codex-harness/)
describes a thread as a persisted conversation whose event history can be created, resumed, forked,
and archived. It also describes sandboxed tool execution, MCP, and skills as part of the harness.

The installed CLI probe confirms separate surfaces for:

- local interactive/session work;
- non-interactive `exec` with JSONL output and optional ephemeral persistence;
- cloud task submission with environment, branch, query, and attempt count;
- app-server protocol;
- sandbox command execution.

This matters for AEGIS: a task submission, a thread, a goal, and a sandbox are not the same object.

### Security and verification posture

The Codex CLI exposes `read-only`, `workspace-write`, and `danger-full-access` sandbox modes and
approval policies including `untrusted`, `on-request`, and `never`. The CLI help is
`MEASURED`; the broader security rationale is described in
[`Running Codex safely`](https://openai.com/index/running-codex-safely/) and
[`Building a safe Windows sandbox`](https://openai.com/index/building-codex-windows-sandbox/).

The Codex harness material emphasizes tests, logs, metrics, browser validation, review loops, and
repository-local knowledge. These are useful design patterns, but vendor-reported workflow success
is not independent benchmark evidence for AEGIS.

## 2.2 Codex goal/target interpretation

| Claim | Status | Reason |
|---|---|---|
| Codex has a first-class persisted goal primitive in current upstream source | `SOURCE-BACKED` | Dedicated state model, runtime, tool, events, and app-server schemas exist. |
| The goal is thread-scoped | `SOURCE-BACKED` | Public and internal shapes are keyed by `threadId`. |
| Codex accounts usage and can stop a goal on budget/usage/execution conditions | `SOURCE-BACKED` | Runtime accounting and terminal/limited statuses are present in source. |
| Goal updates are protected against stale state | `SOURCE-BACKED` | Internal `goal_id` is used as an expected value in updates/accounting. |
| Codex's public goal schema defines objective plus machine-checkable acceptance predicates | `UNKNOWN` / `NOT VERIFIED` | No acceptance-predicate field was found in the inspected schema/source; absence in inspected files is not proof of absence from every product surface. |
| `complete` means the outcome is independently correct | `NOT VERIFIED` | The goal state is an agent/runtime lifecycle signal, not an evidence verdict. |
| Codex cloud task `attempts` is equivalent to a goal retry policy | `INFERRED: false` | The CLI exposes attempts for cloud submission, but the inspected help does not establish equivalence to the goal state machine. |

## 2.3 Codex lessons and failure modes

**Reusable:** durable state, explicit lifecycle, token accounting, stale-update defense,
thread/task separation, and clear execution-policy modes.

**Do not copy blindly:** expose internal product states as if they were scientific verdicts; use a
single prompt/objective as the full task specification; assume `complete` implies correctness; or
mix local/cloud task identity with the target resource identity.

**Important failure cases:** stale fork/resume, token budget crossed during a turn, model reports
completion after a failed tool call, cloud task branch diverges from local checkout, and a goal update
arrives after a newer goal replacement. AEGIS must test each explicitly.

# 3. Google Antigravity audit

The name is treated here as Google's current Antigravity platform, including the IDE/Agent surface,
Antigravity 2.0, CLI, and documented managed-agent direction. This is not the unrelated physics
term or an arbitrary third-party repository.

## 3.1 Project and target boundary

The official [`Projects`](https://antigravity.google/docs/projects/) page defines a Project as a
configuration of folders that defines an agent's environment and scope. It can span multiple folders
and has isolated agent settings and permissions.

The same page documents two materially different concurrency modes:

- **Local Mode:** agents work in the same active folders.
- **New Worktree Mode:** a new Git worktree is created for the conversation; the page recommends it
  for complex tasks and avoiding parallel-agent conflicts.

This is the clearest direct answer to the user's current conflict concern: local-mode parallel agents
share mutable state; new worktrees isolate Git checkouts but do not automatically solve shared external
resources, ports, databases, caches, or human merge conflicts.

The project boundary is therefore a **target/scope primitive**, not a goal primitive. The inspected
public pages did not show a documented `goal_id`, objective schema, acceptance-predicate schema, or
goal-state database comparable to Codex's current source.

## 3.2 Plan and artifact semantics

Antigravity's [`Artifact Review`](https://antigravity.google/docs/artifact-review/) page distinguishes:

- **Planning Mode:** research, task groups, and structured implementation-plan artifacts before execution.
- **Fast Mode:** direct execution for localized work.

The [`Implementation Plan`](https://antigravity.google/docs/implementation-plan/) page says the plan
contains technical revisions intended for user review. Depending on the artifact review policy, the
agent pauses for review, comments can redirect scope, and the user can proceed or continue reviewing.

The [`Artifacts`](https://antigravity.google/docs/artifacts/) page describes plans, diffs, architecture
diagrams, images, and browser recordings as structured deliverables for asynchronous collaboration.
This is a strong human-steering design: the user reviews high-level artifacts at milestones instead
of following every tool call.

However, an artifact is still agent-produced data. Its existence, visual richness, or approval does
not independently prove that an implementation works. AEGIS should store artifact provenance and then
run separate falsifiers/verifiers.

## 3.3 Permissions and terminal sandbox

The official [`Permissions`](https://antigravity.google/docs/permissions/) page models sensitive actions
as `action(target)` resources and documents allow/ask/deny evaluation. It covers files, commands,
URLs, MCP, and unsandboxed execution. It also distinguishes `read_url` from browser actuation and
terminal network access.

The official [`Sandbox`](https://antigravity.google/docs/sandbox/) page describes a terminal sandbox
implemented with native OS primitives, project read/write mounts, restricted visibility for sensitive
files, and approved-domain network access. It also documents an `unsandboxed(...)` escape hatch that
can run on the host with full privileges and may require approval.

This is a capability boundary, not an acceptance boundary. The following must remain separate in
AEGIS:

```text
permission(resource)  !=  goal_acceptance(predicate)
allowed command       !=  safe command outcome
artifact approved     !=  experiment replicated
```

## 3.4 Subagents and asynchronous execution

The official [`Subagents`](https://antigravity.google/docs/subagents/) page documents concurrent
subagents with `inherit`, `branch`, or `share` workspace options; fresh context rather than inherited
conversation history; parent monitoring; and states including active/completed/terminated behavior.
It also documents inherited permission scopes and permission bubbling to the main UI.

The CLI page on [`Background Tasks & Subagents`](https://antigravity.google/docs/cli/subagents) adds
running/done/killed/error status indicators and a distinction between agent subagents and background
tasks such as tests or shell work.

The model should not be allowed to choose `share`/local-mode concurrency in a high-integrity AEGIS
run without a resource lease. Git worktree isolation alone does not fence a shared browser profile,
database, network side effect, or evidence archive.

## 3.5 Antigravity evidence assessment

| Claim | Status | Reason |
|---|---|---|
| Antigravity has project-scoped folders and per-project permission/settings boundaries | `SOURCE-BACKED` | Official Projects page. |
| Antigravity offers a new-worktree mode for conversation isolation | `SOURCE-BACKED` | Official Projects page and FAQ. |
| Antigravity uses plans/artifacts as reviewable intermediate deliverables | `SOURCE-BACKED` | Official Artifact Review, Implementation Plan, and Artifacts pages. |
| Antigravity can operate across editor, terminal, and browser | `SOURCE-BACKED` | Official IDE overview and Agent page. |
| Antigravity has native terminal sandboxing and explicit unsandboxed escape behavior | `SOURCE-BACKED` | Official Sandbox and Permissions pages. |
| Antigravity has a public first-class durable goal schema comparable to Codex `ThreadGoal` | `UNKNOWN` / `NOT VERIFIED` | Not found in the inspected public documentation. This is not a proof that no private/internal representation exists. |
| Artifact approval is equivalent to correctness | `FALSE AS AN INTEGRATION ASSUMPTION` | Approval is a human steering gate; independent test/evidence remains necessary. |
| New worktree eliminates all parallel-agent conflict | `FALSE` | It isolates a Git checkout, not every external resource or side effect. |

# 4. Anthropic Claude Code audit

The current public documentation is hosted at `code.claude.com/docs/en`; the older
`docs.anthropic.com/en/docs/claude-code/...` paths are not reliable for direct retrieval. The official
repository snapshot observed during this audit was `anthropics/claude-code` commit
[`347b38e4a733d95b2f00690a4ca58ac1544f8a1c`](https://github.com/anthropics/claude-code/tree/347b38e4a733d95b2f00690a4ca58ac1544f8a1c).

## 4.1 Prompt, session, plan, and memory

The official [`Common workflows`](https://code.claude.com/docs/en/common-workflows) page models a
complex change as a prompt/session workflow. Plan mode lets Claude read and propose changes without
editing until the user approves. The official [`Permission modes`](https://code.claude.com/docs/en/permission-modes)
page says a plan can be approved, edited, or refined before the session moves into an editing mode.

Claude Code's CLI surface includes non-interactive `-p`, `--max-turns`, structured JSON output,
`--resume`, `--continue`, and `--permission-mode`. These are execution/session controls, not a public
goal contract.

The official [`Memory`](https://code.claude.com/docs/en/memory) page documents project/user/organization
`CLAUDE.md` context and auto memory across sessions and worktrees. It explicitly warns that
`CLAUDE.md` is context rather than enforced configuration; hard blocks should use hooks or permissions.
This is an important security lesson for AEGIS: a Markdown rule cannot be the only owner of a policy
invariant.

## 4.2 Subagents and parallelism

The official [`Subagents`](https://code.claude.com/docs/en/sub-agents) page documents:

- fresh, isolated subagent context;
- tool, model, permission, hook, and skill restrictions;
- foreground and background execution;
- automatic delegation and explicit `@`/CLI invocation;
- optional worktree isolation;
- auto-denial when a background subagent would otherwise need an interactive permission prompt;
- no nested subagent spawning in the normal subagent model.

The page distinguishes subagents from agent teams: subagents return summarized work to one session,
while teams coordinate independent sessions. This is useful to AEGIS, but any summary must retain
provenance and cannot silently become authoritative evidence.

## 4.3 Permissions, sandbox, and hooks

The official [`Security`](https://code.claude.com/docs/en/security) and
[`Permission modes`](https://code.claude.com/docs/en/permission-modes) pages document default,
acceptEdits, plan, auto, dontAsk, and bypassPermissions modes. They describe read-only defaults,
manual approval, classifier-backed auto mode, and the warning that bypass modes are intended for
isolated containers/VMs.

The official security page also distinguishes a Bash sandbox from other tools. The official settings
examples state that the sandbox property applies to Bash and not automatically to Read, Write, Edit,
WebFetch, MCP, hooks, or internal commands. AEGIS must never infer whole-agent isolation from a
single tool's sandbox flag.

## 4.4 Claude Code evidence assessment

| Claim | Status | Reason |
|---|---|---|
| Claude Code has plan mode that prevents edits until approval | `SOURCE-BACKED` | Official permission-mode/common-workflow docs. |
| Claude Code sessions are resumable/forkable and can persist transcripts | `SOURCE-BACKED` | Official session/CLI documentation. |
| Subagents can have isolated context and constrained tools/permissions | `SOURCE-BACKED` | Official subagent documentation. |
| `CLAUDE.md` is an instruction/context mechanism, not a hard policy engine | `SOURCE-BACKED` | Official memory documentation. |
| Claude Code public docs expose a durable goal object with acceptance predicates | `UNKNOWN` / `NOT VERIFIED` | No such public object was found in the inspected current docs/repository. |
| A successful plan approval proves code correctness | `FALSE AS AN INTEGRATION ASSUMPTION` | Approval authorizes the next phase; tests and independent verification are still required. |
| Background subagents are equivalent to independent secure sandboxes | `FALSE` | Context/tool/permission isolation is documented; full host/data isolation depends on mode, worktree, and environment. |

# 5. Cross-product comparison

The table is deliberately about contract shape, not perceived product quality.

| Dimension | Codex | Antigravity | Claude Code | AEGIS implication |
|---|---|---|---|---|
| User intent carrier | Thread goal plus objective; otherwise prompt/task | Prompt/conversation plus project and plan artifact | Prompt/session plus plan mode and memory context | Store objective separately from session transcript |
| Goal lifecycle | Explicit active/paused/blocked/usage/budget/complete in current source | Public docs show conversation/subagent/task/artifact workflows; no comparable goal schema found | Public docs show session/plan/permission lifecycle; no comparable goal schema found | Add a vendor-neutral controller-owned state machine |
| Target/scope | Thread start carries cwd, environment roots, sandbox/approval; cloud carries environment/branch | Project folders, per-project settings, Local/New Worktree | Working directory, additional dirs, permissions, optional worktree | Make target roots, revision, capabilities, and side effects first-class |
| Acceptance | No public predicate field found in inspected goal schema | Plan/artifact review and verification affordances; no formal predicate contract found | Plan approval and tests are workflow instructions; no formal goal predicate found | Require machine-checkable predicates for high-risk goals |
| Evidence surface | Tests, logs, metrics, browser validation, PR/review loops | Plans, diffs, diagrams, images, browser recordings, artifact comments | Structured output, transcripts, hooks, test reports, plans | Every claim needs evidence lineage, digest, and verifier |
| Human gate | Approval policy and review surface; can be automated | Artifact review policy can pause or always proceed | Permission modes and plan approval | Gate risky transitions and keep audit trail |
| Tool authorization | Sandbox modes and approval policy | `action(target)` allow/ask/deny plus sandbox/unsandboxed | Permission modes/rules, hooks, Bash sandbox | Enforce below prompt/context layer |
| Parallelism | Threads/worktrees/agent graph and cloud tasks | Subagents, projects, local/new worktree, tasks | Subagents, teams, sessions, optional worktrees | Use goal-scoped leases; isolate Git and external resources separately |
| Budget accounting | Explicit goal token/time fields in current source | Rate limits correlated with work; no public goal budget schema found | `--max-turns` and plan/provider controls; no goal budget object found | Add tokens/time/cost/attempt/resource budgets with stop reasons |
| Persistence | Thread event history and goal state | Conversations, project association, artifacts | Session transcripts, resume/fork, memory | Persist state/event log independently of UI |
| Main residual risk | `complete` can still be self-reported; product version drift | Artifact/plan can be trusted too much; shared mode conflicts | Context instructions mistaken for policy; background prompt denial surprises | Independent verification, policy enforcement, and explicit `UNKNOWN` |

# 6. Adversarial and multi-dimensional audit

## 6.1 Failure matrix

| Failure case | Why it is plausible | Product weakness exposed | AEGIS control |
|---|---|---|---|
| Vague objective | Natural-language prompts omit “done” conditions | All three permit a plan/task without a formal acceptance contract | Require acceptance predicates or classify result `INCONCLUSIVE` |
| Agent declares completion after a tool failure | Model narration and tool state can diverge | Codex has a `complete` status but it is not a correctness proof; other products rely more on workflow | Rust controller blocks terminal success until verifier settles required evidence |
| Stale resume/fork update | Old session continues after newer plan or target revision | Codex explicitly needs internal goal generation protection; session-oriented tools can resume old context | Goal generation + target digest + compare-and-swap transition |
| Two agents share a dirty checkout | Parallel work mutates the same files | Antigravity Local Mode and ordinary terminal sessions allow this by design | Default new worktree/lease; fail closed when an active write lease exists |
| Git worktree but shared database/browser/cache | Checkout isolation is narrower than side-effect isolation | All three can still reach external resources depending on policy | Resource leases, namespace isolation, network allowlist, side-effect ledger |
| Prompt injection in repository/web/browser data | Untrusted content can contain instructions | All are agentic and can read web/files; permission prompts are not a complete semantic defense | Treat content as data; typed tool boundary; provenance and policy check before action |
| Plan/artifact is mistaken for evidence | A polished plan can look authoritative | Antigravity's artifact review is a steering gate, not proof | Separate plan artifact from test/evidence artifact and verifier verdict |
| Markdown instructions drift | Rules become stale or conflict | Claude docs explicitly call `CLAUDE.md` context, not enforcement | Compile/validate policy in controller; use docs as navigational context only |
| Background worker needs a permission | Async worker cannot prompt safely | Claude background subagents auto-deny prompting operations; Antigravity bubbles approval | Model explicit `WAITING_FOR_AUTH`, `BLOCKED`, and resumable approval state |
| Budget crosses during a tool call | Stop decision arrives after work has started | Codex accounts progress at runtime; other tools expose simpler turn limits | Reserve budget before execution, reconcile after, record overshoot/ambiguity |
| Unsandboxed escape is accepted too broadly | Host command can access secrets or network | Antigravity documents `unsandboxed`; Codex has dangerous bypass; Claude has bypass mode | Deny by default, explicit target-scoped approval, secret/path/network checks |
| External dependency is unavailable | Agent cannot finish but may keep retrying | Long-horizon tools can spend budget on retries | Retry budget, backoff, terminal dependency classification, no silent success |
| Artifact/event is lost after crash | UI state is not durable truth | Product surfaces differ in persistence and retention | Append-only event/evidence log with replay and crash recovery |
| Agent summarizes away negative evidence | Main context receives only a positive summary | Subagents intentionally compress context | Preserve raw evidence references, hashes, and counterclaims in the dossier |

## 6.2 Rival hypotheses and disconfirmation

### Hypothesis H1: “A goal is just a prompt plus a sandbox.”

This is falsified by Codex's separate persisted goal state/accounting and by Antigravity/Claude
separating projects, plans, permissions, sessions, and artifacts. A sandbox constrains execution but
does not express acceptance, budget, evidence, or lifecycle.

### Hypothesis H2: “The product with the richest artifact UX is the best research-lab architecture.”

This is not established. Artifacts improve human steering, but they do not by themselves provide
authoritative event replay, deterministic experiment settlement, or independent claim verification.

### Hypothesis H3: “The explicit Codex goal schema can be copied directly into AEGIS.”

This is also incomplete. Codex's objective/status/budget shape is valuable, but AEGIS needs target
digests, acceptance predicates, execution-cell binding, evidence requirements, experiment replication,
resource leases, and verdict semantics that are not represented in the inspected public Codex goal wire
shape.

### Hypothesis H4: “Parallel agents are safe if each has a separate conversation.”

False under shared folders and external resources. Conversation isolation does not imply filesystem,
process, network, browser-profile, database, or evidence-store isolation. AEGIS must make each boundary
explicit and test it.

# 7. AEGIS integration target

## 7.1 Proposed `GoalContract`

This is a target design, not a claim that the current code already implements it.

```text
GoalContract {
  goal_id: UUID                         # stable logical goal identity
  generation: u64                       # optimistic-concurrency generation
  parent_goal_id: UUID | null           # delegated/subagent lineage
  run_id: UUID                          # concrete LabRun owner
  objective: String                     # human/model-readable intent
  acceptance: [AcceptancePredicate]     # machine-checkable conditions
  target: TargetDescriptor               # versioned objects/resources
  scope: CapabilityScope                # read/write/execute/network/browser limits
  policy: PolicySnapshot                 # immutable admission-time policy digest
  budget: BudgetContract                 # token/time/cost/attempt/resource limits
  execution: ExecutionSpec              # selected cell, environment, seed, replay mode
  evidence_policy: EvidencePolicy       # required artifacts and trusted verifier identities
  concurrency: LeaseContract             # worktree/resource keys and ownership
  lifecycle: GoalLifecycle               # controller-owned state and stop reason
  verdict: Verdict | null                # verifier-owned; never inferred from lifecycle
  created_at, updated_at, completed_at
}
```

Minimum `AcceptancePredicate` fields:

```text
predicate_id, description, evaluator, inputs, expected_result, severity,
evidence_refs, reproducibility_requirements
```

Predicate status is kept in the separate `GoalProgress` ledger so the immutable
contract cannot be rewritten merely by reporting a new result.

For the implemented v1 slice, `EvidencePolicy` includes required record-type labels,
a minimum source quorum, an explicit independent-verifier requirement, and a
`trusted_verifier_ids` allowlist. The allowlist is an authorization input, not a
cryptographic attestation; production identity/signature verification remains open.

Minimum `TargetDescriptor` fields:

```text
kind, stable_id, revision_or_digest, read_roots, write_roots,
network_allowlist, external_side_effects, owner
```

Minimum `BudgetContract` fields:

```text
token_limit, wall_time_limit, cost_limit, attempt_limit,
resource_limits, reserved, consumed, overshoot, stop_reason
```

The schema must keep `objective`, `acceptance`, `target`, `policy`, `budget`, and `verdict` distinct.
Combining them into one large prompt would recreate the failure modes this audit identifies.

## 7.2 Lifecycle and authority

Recommended lifecycle:

```text
DRAFT → ADMITTED → ACTIVE
                     ├── PAUSED
                     ├── BLOCKED
                     ├── USAGE_LIMITED
                     ├── BUDGET_LIMITED
                     ├── CANCELLED
                     ├── FAILED
                     └── SETTLED → VERIFIED | INCONCLUSIVE | INVALID
```

Rules:

1. `DRAFT` is editable; `ADMITTED` freezes target, policy, budget, and acceptance digests.
2. Only the controller can transition lifecycle or account budget.
3. The agent may request `complete` or `blocked`; it cannot set `VERIFIED`.
4. `SETTLED` requires required evidence and evaluator results. Missing evidence yields `INCONCLUSIVE`,
   not success.
5. Terminal goals are immutable. A retry creates a new attempt with the same logical `goal_id` plus a
   new attempt identity, or a new generation under an explicit restart operation; it never silently
   resurrects a terminal execution.
6. Every state mutation includes `generation`/expected-goal identity and is rejected when stale.
7. A child goal inherits only an explicit subset of parent scope, budget, and policy. It cannot widen
   the parent's capabilities by prompt or summary.
8. Paused/blocked goals retain durable reason codes and resume conditions; no polling loop may convert
   a non-actionable state into repeated uncontrolled execution.

## 7.3 Mapping to current AEGIS ownership

| New contract concern | Existing AEGIS owner | Minimal integration direction |
|---|---|---|
| Logical run and evidence | `LabRun`, event log, claim/evidence graph | The bounded slice now persists a goal reference, contract hash, target summary, acceptance metadata, and an append-only verifier receipt in context/manifest/snapshot; complete receipts may point to retained source, claim, hypothesis, experiment, and observation records, satisfy the configured source quorum, and include the supported Lab record types; fixed deterministic Lab evaluators now link selected predicates to those records, while arbitrary external evaluators and free-form semantic interpretation remain future work |
| Admission and settlement | `admit_tool_execution` / `record_tool_execution` | Python execution admission/settlement events and the Goal Verification review record carry and verify goal generation, contract hash, and target digest; Rust source validates the corresponding native binding and typed receipt shape |
| Execution target | `ExecutionCellBinding` / `ExecutionCellRegistry` | Bind each attempt to one explicit cell and environment snapshot |
| Process isolation | `ProcessExecutionCell`, Rust execution/resource controller | Use controller-owned resource lease and stop reason; do not let Python infer enforcement |
| Wasm isolation | `WasmtimeSandbox` | Treat Wasm as one execution-cell tier, not the universal agent sandbox |
| Browser | existing browser actor/observer split | Separate browser observation from actuation and attach URL/DOM/screenshot provenance |
| Native fallback | `aegis_cognition/runtime.py` | Keep `authoritative=false`/`NOT VERIFIED` behavior when the native controller is absent |
| Replay | Rust controller/reducer and evidence archive | Replay lifecycle, admission, budget, and verifier events deterministically |
| Multi-agent | child `LabRun`/goal lineage | Use explicit child goals and leases; never share a mutable write target by default |

The existing master plan already points in this direction: LLM proposes, Rust validates schema/policy/
budget/transition/evidence, and execution cells implement capability-specific isolation. The new
research changes the plan's missing detail: the user intent itself needs a durable, verifiable contract.

## 7.4 Vendor-pattern translation table

| Observed pattern | AEGIS adaptation | Constraint |
|---|---|---|
| Codex `ThreadGoal` lifecycle and accounting | `GoalContract.lifecycle` + controller-owned token/time/attempt accounting | Add predicates, target digests, and verifier verdicts |
| Codex internal generation/CAS defense | `generation` + expected-goal/attempt id on every mutation | Reject stale updates; emit a conflict evidence record |
| Antigravity Project | `TargetDescriptor` + `CapabilityScope` | Projects do not replace per-attempt policy or leases |
| Antigravity new worktree | default Git isolation for concurrent write goals | Also isolate non-Git resources |
| Antigravity implementation-plan artifact | `PlanArtifact` before high-risk transition | Plan approval is authorization, not correctness |
| Antigravity artifact comments | structured human feedback event linked to plan revision | Re-plan on material scope/target changes |
| Claude plan mode | read-only planning phase | Controller must enforce no writes, not merely prompt the model |
| Claude `CLAUDE.md` memory | navigational context and project conventions | Never use as sole security or acceptance control |
| Claude subagent restrictions | child-goal capability subset and summarized result | Preserve raw evidence refs; summary is non-authoritative |
| Claude background auto-denial | explicit `WAITING_FOR_AUTH`/`BLOCKED` state | Do not spin or silently drop the task |
| All three permission systems | typed capability resource + allow/ask/deny | Resource matcher must be tested on paths, URLs, commands, MCP, and browser actuation |

# 8. Implementation plan with gates

The audit defines the target design. A bounded integration slice has now been implemented and locally
verified; the remaining phases below are intentionally kept explicit so the slice is not mistaken for
the complete Lab product.

## Phase 0 — Contract and characterization

**Change:** add schema/documentation and pure state-machine tests after reconciling with other
agents' current changes. **Status:** locally implemented for Python `GoalContract`, `TargetDescriptor`,
`GoalProgress`, and the Rust GT96 definition.

**Gate:** round-trip serialization; invalid objective/target/budget rejection; terminal-state
immutability; stale-generation rejection; explicit state-transition table; no change to current
`LabRun` behavior. **Evidence:** Python Goal/Target tests 29/29, current Rust GT96 tests 15/15, and
current native Lab tests 24/24 after Goal Verification, child-lineage, external-effect-key, and
target-root hardening. The full cross-platform and
release matrix remains `NOT VERIFIED`.

## Phase 1 — Shadow goal adapter

**Change:** create a bounded adapter that derives a `GoalContract` view from existing `LabRun` and
execution events without changing execution authority. **Status:** partially implemented; explicit
contracts now flow through `Lab.start`/`LabRun`, context, manifest, snapshot, and native mission JSON.

**Gate:** deterministic replay produces the same goal view; every derived field has provenance; unknown
fields remain `UNKNOWN`; current tests remain unchanged; no duplicate source of truth. **Evidence:**
native Python smoke created a controller from a valid contract and rejected a tampered target; full
`tests/test_lab_runtime.py` passed 353/353.

## Phase 2 — Admission and budget enforcement

**Change:** attach frozen target/policy/budget/generation snapshots to execution admission and settle
them through the existing event path. **Status:** Python per-attempt event binding and the
append-only Goal Verification review record are implemented; the Rust native-side binding, typed-receipt,
and exact generic external-effect-key checks are implemented and covered by 24 native Lab tests. The native evaluator
still consumes a receipt rather than executing arbitrary predicate code.

**Gate:** duplicate admission, stale settlement, budget boundary, timeout, crash/restart, and partial
tool failure tests; controller remains the only authority for transitions and budget.

## Phase 3 — Acceptance/evidence settlement

**Change:** introduce typed predicates/evaluators and a verdict reducer. Connect existing claim/evidence
records and experiment outputs; preserve raw evidence and counterclaims. **Status:** typed predicates,
independent `GoalVerification` receipts with policy-listed verifier identities, one-way
`GoalProgress` settlement, append-only Python event-log integration, Lab-side
evidence-retention/source-quorum/record-type checks, and a fixed deterministic evaluator vocabulary
(`record_exists`, source quorum, claim confidence/status, experiment completion, and observation
count) are implemented. The evaluator never executes code named by a contract. Arbitrary external
evaluators, cryptographic verifier attestation, and semantic interpretation of free-form claim text
remain open.

**Gate:** no `VERIFIED` without all required predicates/evidence; a failing predicate is `FAILED` or
`INCONCLUSIVE` according to policy; model text cannot upgrade evidence status.

## Phase 4 — Concurrent child goals and resource leases

**Change:** add child-goal lineage, worktree/resource leases, and explicit external-side-effect keys.
**Status:** contract-level child lineage is implemented and checked in Python and native Rust;
Python additionally enforces retention of the parent's acceptance predicates and evidence
obligations;
runtime-level shared-resource leases, parent cancellation propagation, and cleanup verification
remain open.

The current `TargetDescriptor.external_side_effects` is now an exact
`tool_name::effect_class` allowlist at the generic Lab admission boundary:
explicit Goal contracts reject undeclared external tool effects, and the native
event-binding path applies the same check. The field remains canonicalized,
hashed, persisted in context/replay metadata, and narrowed for evolved/child
goals. It does not yet provide provider-side containment or an exclusive
resource lease; adapter idempotency, timeout/cancellation, settlement,
recovery, and lease contention remain separate gates.

The target network surface is now consumed by the local research adapter:
host-based search programs are narrowed to the target host set, requested hosts
outside that set are rejected, and an explicit target with no usable host cannot
open network research. Browser policy hosts are checked as a target subset before
browser cells are prepared. This closes the prior metadata-only gap at the
application boundary, but it does not prove DNS-race resistance, proxy/kernel
egress enforcement, or provider containment.

**Gate:** two agents targeting one write key cannot both acquire an exclusive lease; read-only parallel
research remains possible; a child cannot widen parent capability/budget; cleanup after cancellation is
verified.

## Phase 5 — Benchmark and hostile validation

**Change:** run a fixed corpus across local process, Wasm, browser, research, and multi-agent cells.

**Gate:** report p50/p95/p99 admission/settlement overhead, stop latency, replay determinism, evidence
completeness, false-verification cases, and resource leakage. Do not call the system secure, scalable,
or production-ready without these measurements and the existing release gates.

# 9. Test and measurement protocol

## 9.1 Contract tests

- objective normalization and length/type limits;
- required acceptance predicates by risk tier;
- target revision/digest mismatch;
- scope containment for files, directories, URLs, MCP, browser, and process descendants;
- budget reservation, consumption, overshoot, and stop reason;
- goal-generation compare-and-swap;
- duplicate event/idempotency keys;
- terminal-state and retry semantics;
- parent/child capability and budget inheritance.

## 9.2 Failure and adversarial tests

- prompt injection in a repository file, web page, browser DOM, PDF, and tool output;
- two agents writing the same checkout in Local/shared mode;
- two agents with different Git worktrees but one shared database/port/browser profile;
- stale child completion after parent cancellation or goal replacement;
- crash between admission and execution, execution and settlement, or settlement and artifact write;
- non-cooperative descendant process and resource exhaustion;
- multi-stage executor/data-tier transfer, queue pressure, accelerator availability, and pipeline failure;
- network allowlist bypass and unsandboxed escape request;
- background worker requiring a human approval;
- verifier receives a polished plan/artifact but missing raw test evidence;
- evidence source contradiction and replay from an incomplete event log.

Resource placement must not be marked complete from a single placement decision: the acceptance
corpus needs at least one measured multi-stage transfer path and an explicit `UNAVAILABLE` result
when no verified GPU/iGPU adapter exists.

## 9.3 Measurement discipline

The prior AEGIS report measured local resource-policy admission primitives at approximately sub-
millisecond median in the tested Windows workload, but that is not a product-level agent latency
claim. The prior report also recorded Rust/Python test failures tied to the current environment and
did not mark them as full-suite success. Those results must remain bounded evidence.

For the new contract, record at minimum:

```text
workload, input size, platform, runtime/model version, seed,
attempt count, p50/p95/p99, error/timeout rate, stop latency,
replay divergence, evidence completeness, resource leakage,
and confidence/uncertainty notes
```

No numeric threshold should be invented before the AEGIS workload and risk tier are fixed. The first
benchmark should establish a baseline; later claims of improvement require the same workload,
environment, version, and acceptance criteria.

# 10. Integration decision

### Adopt now as design principles

1. A first-class, durable goal object is worth adding to AEGIS; Codex provides direct evidence that
   objective/status/budget/accounting can be implemented as a runtime contract.
2. Goal and target must be separate. Antigravity's Project/worktree distinction and Codex's separate
   thread/environment fields support this separation.
3. Plans/artifacts are valuable human-control surfaces, not proof. Adopt them alongside independent
   verifier artifacts.
4. Policy must be controller-enforced. Claude's explicit distinction between context and enforcement,
   and all three products' permission/sandbox boundaries, support this.
5. Parallel agents require explicit leases and scope inheritance. A conversation or Git worktree alone
   is insufficient.
6. Budgets and stop reasons must be durable and replayable. Codex's current goal accounting is the
   strongest source-backed pattern here.

### Do not adopt without new evidence

- vendor-specific model/product ranking;
- claims that any of the three products is secure against arbitrary prompt injection;
- claims that artifact review or plan approval proves correctness;
- claims that a worktree isolates databases, browsers, ports, network, or secrets;
- claims of exact behavioral parity with Codex, Antigravity, or Claude Code;
- a new external dependency or vendor runtime in AEGIS;
- replacing the existing Rust authority or evidence archive with prompt instructions.

### Go/no-go for implementation

Proceed to Phase 0 only after the concurrent agents' changes are reconciled and the proposed schema
has one owner. Do not begin Phase 2 enforcement until Phase 1 replay shows no divergence and all
unknowns in the target/policy/budget contract have an explicit representation. Do not mark the Agent
Lab “complete” or “production-ready” from this research: this artifact is a design/evidence input,
not an implementation or release gate.

# 11. Limitations and unresolved questions

1. Antigravity internals are not public in the inspected material; its public docs are a capability
   description, not an independent implementation audit.
2. Claude Code was not executable in this environment; no live permission, plan, worktree, or hostile
   prompt test was run against it.
3. Antigravity's UI/CLI was not driven end-to-end; an installed `agy.exe` was discovered, but a
   version/help invocation did not terminate promptly and was not used as semantic evidence.
4. Codex upstream `main` source and the installed `0.116.0` CLI are different evidence snapshots;
   features in the source snapshot must be version-pinned before implementation decisions.
5. Public documentation may omit private product schemas. “Not found” is recorded as `UNKNOWN`, not
   as a proof of nonexistence.
6. No cross-vendor benchmark was run. Product capability descriptions cannot establish comparative
   reliability, security, latency, or cost.
7. The current checkout is dirty with concurrent-agent changes. This artifact deliberately does not
   assume those changes are compatible with the current master plan until a separate reconciliation.

# 12. Source register

## OpenAI Codex

- [Codex source repository](https://github.com/openai/codex) — upstream repository; source snapshot pinned above.
- [Codex `ThreadGoalSetParams` schema](https://raw.githubusercontent.com/openai/codex/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/app-server-protocol/schema/json/v2/ThreadGoalSetParams.json) — objective/status/token budget wire shape.
- [Codex generated `ThreadGoal` type](https://raw.githubusercontent.com/openai/codex/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/app-server-protocol/schema/typescript/v2/ThreadGoal.ts) — public response fields.
- [Codex internal goal model](https://github.com/openai/codex/blob/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/state/src/model/thread_goal.rs) — internal generation/accounting state.
- [Codex goal runtime store](https://github.com/openai/codex/blob/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/state/src/runtime/goals.rs) — persistence, accounting, and stale-update handling.
- [Codex goal tools](https://github.com/openai/codex/blob/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/ext/goal/src/tool.rs) — create/get/update behavior and agent-visible terminal-status restrictions.
- [Codex thread-start schema](https://raw.githubusercontent.com/openai/codex/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs/app-server-protocol/schema/json/v2/ThreadStartParams.json) — environment, sandbox, approval, model, and instruction fields kept separate from goal state.
- [Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/) — thread lifecycle, tool execution, and app-server architecture.
- [Harness engineering](https://openai.com/index/harness-engineering/) — repository knowledge, plans, verification loops, and agent legibility; vendor engineering report, not independent benchmark evidence.
- [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/) — tasks, worktrees, skills, and asynchronous workflows.
- [Running Codex safely](https://openai.com/index/running-codex-safely/) — sandbox and approval posture.
- [Windows Codex sandbox](https://openai.com/index/building-codex-windows-sandbox/) — OS-enforced boundary rationale.

## Google Antigravity

- [Antigravity home](https://antigravity.google/docs/home) — surfaces and core capabilities.
- [Antigravity Agent](https://antigravity.google/docs/agent) — agent, tasks, artifacts, and parallel conversations.
- [Projects](https://antigravity.google/docs/projects/) — project scope, folder roots, settings, Local/New Worktree modes.
- [Artifact Review](https://antigravity.google/docs/artifact-review/) — Planning/Fast modes and approval policy.
- [Implementation Plan](https://antigravity.google/docs/implementation-plan/) — plan artifact and review workflow.
- [Artifacts](https://antigravity.google/docs/artifacts/) — structured deliverables and asynchronous collaboration.
- [Permissions](https://antigravity.google/docs/permissions/) — resource-scoped allow/ask/deny and URL/command boundaries.
- [Sandbox](https://antigravity.google/docs/sandbox/) — native terminal sandbox and unsandboxed escape behavior.
- [Subagents](https://antigravity.google/docs/subagents/) — context, workspace, lifecycle, inheritance, and concurrency.
- [CLI background tasks and subagents](https://antigravity.google/docs/cli/subagents) — status/monitoring/task distinction.
- [Google Antigravity introduction](https://www.antigravity.google/blog/introducing-google-antigravity) — product intent; marketing/announcement source, not an independent efficacy test.

## Anthropic Claude Code

- [Claude Code repository](https://github.com/anthropics/claude-code) — official repository; source snapshot pinned above.
- [Claude Code security](https://code.claude.com/docs/en/security) — permission, prompt-injection, trust, and sandbox posture.
- [Permission modes](https://code.claude.com/docs/en/permission-modes) — default/plan/auto/bypass modes and plan approval.
- [Subagents](https://code.claude.com/docs/en/sub-agents) — isolated contexts, tools, background execution, and worktree options.
- [Memory](https://code.claude.com/docs/en/memory) — CLAUDE.md/auto-memory scope and non-enforcement warning.
- [Common workflows](https://code.claude.com/docs/en/common-workflows) — plan-before-edit and delegation patterns.
- [CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage) — non-interactive output, max turns, resume, permissions, and session flags.
- [Official Claude Code GitHub Actions page](https://docs.anthropic.com/en/docs/claude-code/github-actions) — automation context and repository instructions.

## AEGIS local sources

- [`AEGIS_LAB_RUNTIME_MASTER_PLAN.md`](../AEGIS_LAB_RUNTIME_MASTER_PLAN.md) — current target architecture and authority rules.
- [`EMPIRICAL_RESEARCH_REPORT.md`](EMPIRICAL_RESEARCH_REPORT.md) — prior measurements, literature, sandbox/replay evidence, and limitations.
- [`AEGIS_CURRENT_ARCHITECTURE_TRUTH_AUDIT.md`](../AEGIS_CURRENT_ARCHITECTURE_TRUTH_AUDIT.md) — current architecture truth audit.
- [`TESTING_AND_EVIDENCE.md`](../TESTING_AND_EVIDENCE.md) — local testing/evidence conventions.

## Final evidence statement

**Proven by this artifact:** the cited current Codex source contains a durable thread goal and
runtime accounting surface; the cited Antigravity docs contain project/worktree/artifact/permission/
sandbox/subagent primitives; the cited Claude Code docs contain plan/session/memory/subagent/
permission primitives; and the current AEGIS checkout contains the lab/execution/evidence substrate
listed above. The verification update also establishes a bounded, locally tested Goal/Target contract
slice across Python and Rust.

**Not proven by this artifact:** that any vendor product is secure, correct, faster, cheaper, or better
than another; that the products expose identical goal semantics; that Antigravity or Claude Code lack
private goal schemas; that the bounded slice is a complete Agent Lab product; or that the current dirty
checkout is ready to merge or release.

## 13. Current-source implementation addendum (2026-09-11)

The implementation has moved beyond the original design-only Goal/Target slice,
but the evidence boundary remains explicit:

- Python and native Rust reject structurally unbounded Goal/Target wire data
  before hashing or admission. The profile bounds wire size, text and
  identifier bytes, scope entries, sequence length, total sequence items,
  nesting depth and node count. These are deployment-profile limits, not a
  host-isolation guarantee.
- `LabRun` applies the explicit admission rule on both construction and
  snapshot restore. A bound target without acceptance cannot enter through the
  restore path that only checks shape and hash.
- Receipt-side objects are bounded before their own hashes are produced:
  Python `ExecutionBinding`, `GoalVerification`, `GoalProgress`, and native
  Rust `ProjectionExecutionBinding`. Network allowlist entries are rejected at
  bound-target admission when their shape would be discarded by the host
  matcher.
- Current local gates include Python wire limits `1/1`, Rust GoalContract
  limits `5/5`, and native binding limits `2/2`; the retained Rust artifacts
  are bound to `ed1cbc28e2236aa0c8422d07f93bff55d661d1fa`. The Python full
  contract/Lab command was directly observed at `419 passed` on CPython
  `3.14.7`; its older retained suite record is explicitly marked as preceding
  the later coordination HEAD, not as an independent current-SHA proof.
- Suite evidence now includes a dirty-worktree status and a SHA-256 fingerprint
  of tracked diffs plus non-ignored untracked files. This closes an evidence
  provenance gap; it does not turn local tests into hosted, cross-platform,
  signed, kernel-isolated or production evidence.

The remaining high-consequence claims are still open: external parent-registry
lineage, provider/network/kernel enforcement, multi-machine behavior, hosted
CI, signed attestation, external deployment and production promotion. The
correct integration remains a tiered Agent Lab—control plane, typed Goal/Target
contract, capability admission, leases, execution cells, evidence and replay—
not a single undifferentiated sandbox.
