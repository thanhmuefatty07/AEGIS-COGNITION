# /aegis-resume

Resume AEGIS work from the latest known checkpoint with minimal drift.

## Steps
1. Read the latest checkpoint summary.
2. Restore the current phase, module, and next action.
3. Identify any missing context or blockers.
4. Continue from the exact next step.
5. Report if the checkpoint is stale or incomplete.
6. Prefer durable state reconstruction over verbose replay.
