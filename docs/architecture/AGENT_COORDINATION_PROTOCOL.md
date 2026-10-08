# Agent coordination protocol

This is the operational contract for the three project research lanes. It is
deliberately small: coordination must reduce duplicate work and false progress,
not create another planning system.

## Ownership

| Lane | Single owner | Allowed scope while unblocked |
|---|---|---|
| Hardware | Hardware agent | hardware profile, placement/resource research, Windows-first measurements |
| Lab | Lab agent | GoalContract/TargetDescriptor, effect-class and Python/Rust integration analysis |
| Evaluation | Evaluator agent | acceptance criteria, evidence/provenance, readiness and cross-platform gaps |
| Coordination | Coordinator | gate assignment, conflict resolution, evidence scripts and this protocol |

One gate has one owner and one writer. A lane must not modify another lane's
file family or start a gate whose acceptance evidence is owned elsewhere.

## State machine

`READY → RUNNING → PROVEN | FAILED | BLOCKED_BUT_PRODUCTIVE | BLOCKED_BACKEND`.

`BLOCKED_BUT_PRODUCTIVE` means the blocking dependency is recorded and the agent
gets one bounded read-only lane that reduces uncertainty. That lane may inspect
callers, contracts, stale references, test coverage, artifact provenance, or a
rollback/acceptance plan. It must not create temporary project folders, repeat a
heavy command, modify another owner's files, or turn a hypothesis into a pass.

Use `BLOCKED_BACKEND` only when the tool explicitly reports a terminal failure,
the handle is authoritatively missing, or a required external dependency is
unavailable. An observation timeout is not terminal evidence. Keep the result
`NOT VERIFIED`, do not restart the same work, and continue independent audits.

Background commands are never polled for status. After a tool yields a live
handle, continue independent useful work; when none remains, end the turn and
rely on the host to deliver the command's output. Do not use elapsed time,
process enumeration, a lock file, or an old log to infer completion.

## Dispatch and resource rules

1. Before dispatch, record `owner_id`, `gate_id`, source revision, file scope,
   acceptance checks, and the next bounded observation.
2. Allow one heavy command globally. No second run starts while the same
   gate/commit/command/environment identity is unresolved. Every suite gate
   declares a positive wall-clock timeout; a timeout is a
   failed/not-verified outcome, never an implicit permission to retry.
3. Prefer Windows/local focused checks. macOS/Linux work is prepared as a
   contract or minimal CI lane until a real runner is available; an unexecuted
   workflow is not evidence.
4. Never poll a background process or job. Use terminal output delivered by the
   execution tool. A UI `active` label, lock file, old log, elapsed time, or
   intention is not proof that work is still running or has completed.
5. On completion, reconcile the artifact before opening the next gate. If the
   artifact is missing, incomplete, stale, unowned, or ambiguous, hold the gate.

## Evidence contract

A suite artifact is release-eligible only when it has the checked-out commit,
the `worktree_status` and 64-character `worktree_sha256` fingerprint for
tracked changes plus non-ignored untracked files, non-empty `owner_id` and
`gate_id`, `attempt_id`, deterministic `run_key`, zero exit code, zero failures,
and valid output digest. Otherwise its status is `NOT VERIFIED`. The commit
alone is insufficient for a dirty checkout. The run key identifies duplicate
work; it is not a scheduler lock, so the coordinator must still serialize
dispatches. The `suite_evidence` command additionally takes one local OS-level
lock for the workspace, preventing concurrent suite commands on the same
machine and releasing automatically when the process exits; separate CI
runners remain independent.

Reports must state, in order: current gate owner, files changed since the last
checkpoint, live-process evidence, newest artifact and its status, next bounded
action, and unresolved `NOT VERIFIED` claims. Never infer percentage completion
from elapsed time or a stale UI state.

If multiple eligible files claim the same `gate_id`, they are interchangeable
only when their owner, attempt, `run_key`, and result fingerprint are identical.
Competing identities, result drift, or duplicate files that claim the same
artifact name are ambiguous duplicate work and keep that gate `NOT VERIFIED`
until one result is selected by the owning runner.

Cross-language parity checks are separate gates unless one wrapper runs the
complete parity command as one attempt. For example, use distinct
`GOAL-TARGET-LIMITS-PYTHON-V1` and `GOAL-TARGET-LIMITS-RUST-V1` identities for
separate Python and Rust runs; do not reuse one gate ID and ask the materializer
to infer that the results are complementary.
