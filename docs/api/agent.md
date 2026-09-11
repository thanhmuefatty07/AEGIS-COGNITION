# Agent API

`Agent` is the compatibility facade for bounded one-shot work. It preserves
the existing `run()` contract and does not silently grant browser or external
write authority.

```python
from aegis_cognition import Agent

result = Agent("Summarize the supplied evidence", trust_level="DEV").run()
print(result.output)
```

For a multi-step research or experiment mission, use the explicit Lab facade:

```python
from aegis_cognition import Lab, LabMissionSpec, LabPolicy

lab = Lab(policy=LabPolicy(allowed_hosts=("example.com",)))
session = lab.start(LabMissionSpec("Compare two claims from live sources"))
result, dossier = await session.result()
```

The canonical contract, action vocabulary, evidence classes and known limits
are documented in
[`AEGIS_LAB_RUNTIME_MASTER_PLAN.md`](../architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md).

## Goal and target contracts

Use `GoalContract` when a Lab run needs a durable, machine-checkable definition
of success. `TargetDescriptor` binds the identity and revision being inspected,
the read/write roots, network allowlist and declared external effects. The
contract hash is carried through Lab admissions, replay events, snapshots and
verification receipts, so a result for another target or generation is rejected.

```python
from aegis_cognition import GoalContract, TargetDescriptor

contract = GoalContract(
    goal_id="goal-001",
    objective="verify the bounded change",
    target=TargetDescriptor(
        kind="repository",
        stable_id="aegis-cognition",
        revision_or_digest="<operator-supplied-revision>",
        read_roots=("C:/work/aegis-cognition",),
        write_roots=("C:/work/aegis-cognition",),
        owner="operator",
    ),
    scope=("read code", "run tests"),
    non_goals=("publish remotely",),
    # Add explicit acceptance predicates and a bound policy before admission.
)
```

An explicit contract is admitted only after its target, policy digest, required
acceptance predicates and evidence obligations are bound. Legacy Lab calls still
produce an unbound draft for compatibility and must not be described as an
independent verification. A GoalContract is a scope and evidence boundary; it
does not by itself prove filesystem, network, browser or kernel isolation.
For generic tool admissions, `external_side_effects` may contain exact
`tool_name::effect_class` keys. An explicit Goal contract fails closed when an
external tool does not match one of those keys; native-required runs also fail
closed when the Goal target is unbound. `LabPolicy` must still allow external
writes. The runtime-owned `memory.index_session::memory_write` path is a
separate exception only when its replay-bound `post_completion_effect` cell is
registered; it does not make arbitrary durable writes local-safe. This is only
a Lab admission allowlist, not proof of provider-side containment or an
exclusive resource lease. External adapters remain
responsible for idempotency, timeout/cancellation, settlement and recovery
evidence.

For explicit network research, the Goal target is now an upper bound: host-based
search programs are automatically narrowed to `target.network_allowlist`, and
an empty or malformed target host list rejects network research. A browser run
with an explicit Goal must likewise provide a browser host policy contained by
the target. This is application-level egress narrowing; it is not a DNS-race,
proxy, kernel firewall, or provider-containment proof.

For generic tool inputs, explicit path fields (`path`, `target_path`,
`file_path`, `directory`, `read_path`, `write_path` or `paths`) are checked
against the Goal target as well. Read-like effects require `read_roots`,
write-like effects require `write_roots`, and `compute` may use either. The
comparison is lexical and boundary-aware, so `C:/repo-other` does not satisfy
`C:/repo`; it does not resolve symlinks and therefore remains an application
check rather than OS-level containment. A malformed path field or an explicit
path outside the target is rejected before generic tool admission.

## Lab compatibility and recovery

`Agent(..., lab=True)` uses the same bounded controller lane while preserving
the one-shot output shape. When the packaged native authority is available it
enables a durable replay archive by default; set `lab_replay_archive=False` to
opt out explicitly. Provider fallback attempts are recorded independently, so
an HTTP-429 route is visible as `REJECTED` followed by the selected fallback's
`SUCCESS`, rather than being collapsed into one opaque call.

For `trust_level="PROD"`, native Lab authority is required by default even
through this compatibility facade. A missing native extension therefore fails
closed at startup; development callers must choose `DEV`/`STAGING` or set an
explicit non-production policy instead of silently running an unaudited
projection.

The compatibility facade's post-run learning-store write is also represented
as a `memory.index_session` admission/settlement through the dedicated
`post_completion_effect` execution cell. In `DEV`, an unavailable optional
learning bridge is retained as a rejected cache receipt without blocking the
scientific dossier; `PROD` or `lab_require_post_completion_effect=True` fails
closed with `post_completion_effect_missing` when no trusted cell is present.

Lab compatibility retrieval is fenced the same way. When `Agent(..., lab=True)`
uses its RAG bridge, the `memory.search_past` read is admitted/settled through a
`context_retrieval` cell before the first controller/provider call. Only a
bounded digest and character count enter the replay ledger; bounded prompt-
injection markers reject the retrieved context with hash-only security
evidence. This removes the previous pre-Lab implicit read while preserving the
normal non-Lab `prepare()` behavior.

With `lab_require_native_authority=True`, a custom gateway factory must forward
the supplied `provider_attempt_hook` and expose the installed callback as its
`provider_attempt_hook` attribute. A factory that accepts the keyword but drops
it is rejected before any model/provider side effect.

If a worker stops after admission but before settlement, restore the snapshot
and call `LabRun.reconcile_unsettled_executions(operator_id=..., reason=...)`.
The operation scans the event log for open tool, research, experiment,
browser-observation/action and skill admissions, records each outcome as a
typed `REJECTED` settlement with `UNKNOWN_SIDE_EFFECT`, blocks the dossier, and
never asserts that an ambiguous external effect succeeded. The older
`reconcile_unsettled_tool_executions` method remains as a tool-only compatibility
surface. Recovery must run before a terminal `aborted` transition; otherwise it
fails closed and leaves the open admission visible.

Archive restore is also identity-bound: when the packaged native extension
exposes the strict verifier, it compares the recovered sealed-segment prefix to
the snapshot's persisted `manifest_hash`. A missing/corrupt tail therefore
fails closed instead of being accepted as a completed run. Older adapters that
only expose the prefix verifier remain compatibility-only and do not provide
that completion guarantee.

## Execution-cell registry

Every controller-selected edge runner is resolved through one trusted registry.
The legacy `experiment_runner`, `simulation_runner`, `tool_runner`,
`search_program_executor`, `browser_runtime_adapter` and `skill_executor`
options are converted to bounded cells automatically. For a stricter deployment, provide explicit
`ExecutionCellBinding` values; the model may select only the registered
`cell_id`, capability, effect class and trust envelope, never a callable:

```python
from aegis_cognition import ExecutionCellBinding, Lab, LabPolicy

cells = {
    "physics": ExecutionCellBinding(
        cell_id="physics-v1",
        action_kinds=("simulation_action",),
        runner=simulate,
        capabilities=("compute",),
        effect_classes=("compute",),
        trust_levels=("DEV", "STAGING"),
    )
}
lab = Lab(policy=LabPolicy())
session = lab.start("calibrate the model", lab_execution_cells=cells)
```

The cell manifest is persisted with the Lab snapshot and included in the
dossier manifest hash. Unknown cells, duplicate action-kind registrations,
capability/effect mismatches and trust-level violations become explicit
blockers before the runner is invoked. Once this trusted manifest is captured,
the registry is sealed; late registration is rejected rather than allowing the
runtime binding to diverge from the replay-bound manifest. Lookup and manifest
generation then use a sealed snapshot, so mutation of the construction table
cannot replace the runner or alter the replay identity after capture.

When `lab_execution_cells` is supplied explicitly, it is a strict allowlist:
the legacy runner options are not a fallback. A lane omitted from that
allowlist records `execution_cell_not_registered:<action_kind>` and its
admission is settled non-successfully; this prevents an adapter hidden in a
compatibility option from bypassing the registry. Omitting the option preserves
the compatibility mode in which the legacy options are converted into the
default trusted bindings. A native-required run (including the default
`trust_level="PROD"` path) also uses strict edge resolution after converting
those options, so a missing browser/search/tool/skill cell fails closed instead
of falling through to an implicit adapter.

High-level edge invocations also pass through a cancellation fence. If an
adapter catches `CancelledError` and returns a value anyway, the fence rejects
that value and the already-admitted execution is settled as `CANCELLED`; it
does not claim that process-level interruption is solved. Crash recovery uses
the event-log reconciliation API above for admissions that remain open.

When a run is given `browser_launcher` rather than an already-owned
`browser_session`, opening the browser is itself a typed `launch` actor action.
The Lab writes its admission receipt before invoking the launcher and settles
that receipt on success, rejection or cancellation. This ordering prevents a
custom launcher from creating a process before the Lab has a replay-visible
decision; it is still not an OS/process isolation guarantee.

Browser URL admission rejects credential-bearing URLs, non-HTTPS URLs when
required, hosts outside the configured allowlist, and literal (including
legacy decimal/octal/hex IPv4 spellings of) private, loopback, link-local,
multicast, reserved or unspecified IP addresses. A
managed Playwright session applies the same literal-address guard to its
initial URL and every intercepted route.
The managed Playwright adapter also resolves every initial and intercepted
hostname request and rejects the request if any returned address is private,
loopback, link-local, multicast, reserved or unspecified. A DNS rebinding race
between that check and the browser connection still requires the hosted
DNS/egress enforcement described in the Lab blocker register; this local check
must not be read as OS isolation.

Browser observer projections are scanned in memory for the bounded
prompt-injection marker set also used by search ingestion. A match records a
`browser_prompt_injection_detected` security event with a digest and settles the
observer receipt as `REJECTED`; raw page/accessibility/network text is not
written to the Lab event ledger. This is a local marker gate, not a complete
classifier or a substitute for hosted hostile-content evaluation.

The built-in research fetcher applies an analogous egress preflight before
`urlopen`: literal and legacy-obfuscated private IPs are rejected, hostnames
are resolved and rejected when any current address is private, and redirects
are checked again against HTTPS/credential/allowlist policy. Query-provider
candidates with unsafe literal destinations are rejected before they can enter
the research projection. The preflight does not close a DNS-rebinding race;
that still requires OS/network egress enforcement.

For a picklable local adapter that may ignore cancellation, operators may wrap
the runner in `ProcessExecutionCell(runner, timeout_seconds=...)` before placing
it in an `ExecutionCellBinding`. The cell proves local wall-time timeout and
task-cancellation termination of its child process. It is not an OS sandbox and
does not prove descendant cleanup, Job Object/cgroup enforcement, or reversal
of an already-issued external side effect.

When `lab_replay_directory` is configured, `LabApplication` acquires a
`ReplayWriterLease` for the whole run. The lease is an advisory OS file lock,
so a second local process cannot append to the same replay directory until the
first run releases it; the descriptor-based lock is released by normal process
exit. This is local writer serialization, not a hosted lock service or proof
that an external provider effect is reversible.

When an explicit Goal declares `TargetDescriptor.external_side_effects`, the
Lab also acquires one `ExternalSideEffectLease` per exact
`tool_name::effect_class` key for the whole run. It uses
`lab_external_effect_lease_directory`, falling back to `lab_replay_directory`;
without either directory the explicit external-effect run fails closed. Two
local processes sharing that directory cannot hold the same key concurrently,
while different keys remain independently leaseable. This is advisory local
coordination only: it does not lock a provider, undo a request already sent, or
create a kernel/network boundary.

Benchmark options may provide `benchmark_validator_command` as an operator-owned
argv sequence, or a synchronous `benchmark_validator` callback. The callback
is itself a `benchmark_validation` compute cell; when `lab_execution_cells` is
explicitly supplied, omitting that cell fails closed instead of invoking the
legacy callback. `BenchmarkProtocolV2` sends the metric values, protocol hash,
raw-trial hash and an input digest to that command in the
`aegis-hidden-validator-input-v1` JSON envelope. The command must return one
`aegis-hidden-validator-result-v1` object with a boolean verdict, matching input
digest and non-empty `validator_version`; shell execution is never used. A
timeout, non-zero exit, malformed output, digest mismatch or output over the
configured limit is recorded as `REJECTED`. This gives the local harness a
separate validator process; it is not hosted secrecy, and the command must
never be selected from model output.
