# AESE integrated capability — implemented vertical slice

This document records what is implemented in the first safe vertical slice
of the integrated AESE capability. It complements
`AESE_INTEGRATED_DEVELOPMENT_CAPABILITY_PLAN.md`; it does not promote any
shadow decision into execution authority.

## Runtime boundary

The shared façade is `aegis_cognition.verification.VerificationFacade`.
Agent runtime, desktop service and CLI use the same façade. The façade owns
contracts, session state, passive discovery, packet construction, change
observation, candidate-test assessment, Lab-bound receipt recording and report
assembly.

Execution authority remains the existing Lab path:

```text
AESE control plane
  -> structured plan / command description
  -> existing Lab ExecutionCellRegistry + tool_call admission/settlement
  -> existing ProcessExecutionCell
  -> bounded structured result
  -> ExecutionReceipt
```

The adapter accepts only an executable plus an `argv` tuple; it never builds a
shell string. `LocalVerificationCommand` contains the working directory,
timeout, source revision and a small redacted environment declaration. The
command is executed only after Lab has admitted the generic tool call and the
existing `ProcessExecutionCell` has created its bounded child process. This
prevents a second scheduler, lease ledger or execution authority from
appearing in AESE.

## Safety state

The first slice is permanently shadow-only:

```text
AESE_MODE=SHADOW
SELECTIVE_TEST_AUTHORITY=DISABLED
TEST_SKIPPING_AUTHORITY=DISABLED
EVIDENCE_PROMOTION=DISABLED
```

Unknown or incomplete requirements do not get inferred. Development-looking
agent tasks automatically enter AESE unless `aese=False` or
`aese_auto=False` is supplied. An active source-development session must
provide `aese_expected_behavior`; otherwise packet creation fails closed before
the agent prompt is sent. Explicit `aese=True` remains available for tasks
whose wording is not classified as development work.

Candidate test patches can be proposed and assessed. Assessment rejects a
missing oracle, skip/xfail weakening, stale base revision, or non-test-scoped
path. Applying a candidate returns `BLOCKED_SHADOW_ONLY`; no workspace write
or evidence promotion is performed by this slice.

## Public operations

The façade provides:

```text
inspect_project
create_contract
start_session
get_agent_packet
observe_change
get_feedback
propose_test_change
evaluate_test_change
apply_test_change
request_deep_run
inspect_run
cancel_run
resume_session
read_report
```

Desktop exposes the same operations through the versioned `verification.*`
protocol commands. CLI entry points are currently:

```text
aegis verify inspect [path]
aegis verify start <task> --expected <behavior> [path]
```

The current façade store is process-local. It is a control-plane seam, not a
replacement for the durable Lab ledger. Lab-bound local receipts are now
recorded in the façade for the lifetime of the session; durable cross-process
session storage, cursors across process restarts and hosted execution remain
open work and must preserve the existing contract hashes and authority
boundary.

For the current AEGIS repository, the default local fast lane discovers and
runs the focused Python AESE/verification tests and, when Cargo is available,
the bounded `aegis-nerve` library test lane. Set `aese_deep=True` for the
broader Python/Rust command set. On rustup-managed Windows installations, the
adapter resolves the real Cargo/rustc/rustdoc binaries and adds the active
Python runtime DLL directory to the child `PATH`; this is required because
the Rust test binary links to the Python ABI. It does not hard-code a Python
version or use a shell. A stale compiler cache is reported as a build error;
AESE does not silently clean a user's target directory. The result is still
provisional: a passing receipt is not final assurance while
`EVIDENCE_PROMOTION=DISABLED`.

The local process cell proves a bounded wall-time and process-cleanup path for
the declared host. It does not by itself prove a complete filesystem,
network, memory or kernel sandbox. Those claims remain platform-specific and
must continue to use the existing Linux/Windows/macOS evidence lanes.

The clean checkout at commit `fa7c341ec4b2784d85b40c503f7292097f10861b`
passed the [GitHub Actions CI run](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/35031855905)
with 20/20 jobs successful. That run covered Rust quality gates, strict
Pyright/Ruff gates, Python suites, wheel installation, desktop graph builds,
and platform smoke on Ubuntu, Windows and macOS. This is evidence for the
tested hosted matrix, not a claim of compatibility with every Linux
distribution or every project language.

The [deep-evidence run](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/35031856691)
is intentionally separate and longer-running. At the time of this update,
Miri/sanitizer, native platform evidence, resource benchmarks, security and
replay/dependency gates had completed successfully; the trust-boundary fuzz
campaign was still running. No authority promotion depends on an unfinished
deep run.

## Adapter support matrix

| Adapter family | Current level | Meaning |
| --- | --- | --- |
| Python/pytest | L1 | bounded command execution with structured counts |
| Rust/Cargo | L1 | bounded command execution with structured counts |
| JavaScript/TypeScript | L0 | detection only |
| Go | L0 | detection only |
| .NET | L0 | detection only |
| JVM/JUnit | L0 | detection only |
| C/C++/CTest | L0 | detection only |
| Ruby/PHP/Swift/Dart | L0 | detection only |
| Custom | L1 | bounded command/result model only |

No family is L2 or L3 until adapter fixtures demonstrate structured
discovery, result parsing, impact-aware planning and quality assessment.

## Evidence contract

All contracts are versioned and canonical-hash bound. Execution receipts
reserve fields for adapter/framework/toolchain/platform, executable/argv,
working directory, redacted environment fingerprint, resource policy,
timeout/cancellation, exit semantics, test counts, artifact references,
stdout/stderr references, source revision, plan hash, contract hash and
timestamps. A zero-test result cannot be represented as `PASS`.

The baseline AESE measurement path now also rejects a non-finite baseline as
`INSUFFICIENT_EVIDENCE`, and invalid legacy outcomes cannot complete the
false-negative measurement.

## Verification performed for this slice

The local end-to-end smoke path exercised the real façade and Lab boundary on
Windows: 102 Python tests and 558 Rust library tests passed, producing two
`LAB_BOUND` receipts. The report correctly remained
`PROVISIONAL_PASS` with `final_assurance=false`; evidence promotion and
selective-test authority remain disabled. The focused Python integration gate
also passes 9/9, including the rustup/toolchain-resolution regression. This is
local Windows evidence only: broader repository, hosted GitHub Actions,
Linux/macOS/VM and full adapter-matrix lanes remain required before authority
promotion.
