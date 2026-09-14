# AESE integrated capability — implemented vertical slice

This document records what is implemented in the first safe vertical slice
of the integrated AESE capability. It complements
`AESE_INTEGRATED_DEVELOPMENT_CAPABILITY_PLAN.md`; it does not promote any
shadow decision into execution authority.

## Runtime boundary

The shared façade is `aegis_cognition.verification.VerificationFacade`.
Agent runtime, desktop service and CLI use the same façade. The façade owns
contracts, session state, passive discovery, packet construction, change
observation, candidate-test assessment and report assembly.

Execution authority remains the existing Lab path:

```text
AESE control plane
  -> structured plan / command description
  -> existing Lab ExecutionCellRegistry + tool_call admission/settlement
  -> ExecutionReceipt
```

The new adapter code never calls a shell or subprocess. `BoundedCommand`
contains an executable, an argv tuple, a working directory, a timeout and a
string environment mapping. This prevents a second execution engine from
appearing in AESE.

## Safety state

The first slice is permanently shadow-only:

```text
AESE_MODE=SHADOW
SELECTIVE_TEST_AUTHORITY=DISABLED
TEST_SKIPPING_AUTHORITY=DISABLED
EVIDENCE_PROMOTION=DISABLED
```

Unknown or incomplete requirements do not get inferred. An agent run with
`aese=True` must provide `aese_expected_behavior`; otherwise session packet
creation is incomplete and source implementation is blocked.

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
replacement for the durable Lab ledger. Persistence, cursors across process
restarts and actual Lab-bound execution are later phases and must preserve
the existing contract hashes and authority boundary.

## Adapter support matrix

| Adapter family | Current level | Meaning |
| --- | --- | --- |
| Python/pytest | L0 | detection only |
| Rust/Cargo | L0 | detection only |
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

The focused contract, session, adapter, desktop and agent integration tests
are the decisive checks for this change. Broader repository and hosted lanes
remain required before any authority promotion. A full adapter matrix,
hosted GitHub Actions lane and opt-in GCP lane are not claimed by this
document.
