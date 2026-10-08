const activeRunStatuses = new Set(["RUNNING", "CANCELLING", "WAITING_PERMISSION"]);

export const taskStates = Object.freeze([
  "DRAFT",
  "QUEUED",
  "PREPARING",
  "RUNNING",
  "CANCELLING",
  "WAITING_USER",
  "WAITING_PERMISSION",
  "VERIFYING",
  "REVIEW_REQUIRED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "INTERRUPTED",
]);

export function isTaskRunActive(status) {
  return activeRunStatuses.has(status);
}

export function interruptedDesktopRunStatus(run, error) {
  if (!(error instanceof Error) || !error.message.startsWith("RUN_NOT_FOUND:")) return null;
  return {
    ...run,
    status: "INTERRUPTED",
    cancel_supported: false,
    thread_alive: false,
    error_code: "RUN_NOT_FOUND",
  };
}

export function shouldDrainTaskQueue(activeRunIds, queueLength, draining, paused) {
  return !activeRunIds && queueLength > 0 && !draining && !paused;
}

export function isProjectSwitchBlocked(actionBusy, activeRun) {
  return actionBusy || activeRun;
}

export function deriveTaskState(snapshot, run = null, approvalPending = false) {
  if (run && activeRunStatuses.has(run.status)) return run.status;
  if (approvalPending) return "WAITING_PERMISSION";
  const executions = snapshot?.executions ?? [];
  const latestExecution = executions.reduce(
    (latest, item) => !latest || item.started_at_ms > latest.started_at_ms ? item : latest,
    null,
  );
  if (latestExecution) {
    if (latestExecution.status === "RUNNING" || latestExecution.status === "CANCELLING") return "INTERRUPTED";
    if (latestExecution.status === "WAITING_APPROVAL") return "WAITING_PERMISSION";
    if (["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(latestExecution.status)) {
      return latestExecution.status;
    }
  }
  const turns = snapshot?.turns ?? [];
  if (turns.length === 0) return "DRAFT";
  const latestAssistant = turns.reduce(
    (latest, item) => item.role === "assistant" && (!latest || item.revision > latest.revision) ? item : latest,
    null,
  );
  if (!latestAssistant) return "DRAFT";
  if (["QUEUED", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(latestAssistant.status)) {
    if (latestAssistant.status === "RUNNING") return "INTERRUPTED";
    return latestAssistant.status === "WAITING_APPROVAL" ? "WAITING_PERMISSION" : latestAssistant.status;
  }
  return "DRAFT";
}

export function taskStateLabel(status) {
  const labels = {
    DRAFT: "Draft",
    QUEUED: "Queued",
    PREPARING: "Preparing",
    RUNNING: "Running",
    CANCELLING: "Stop requested",
    WAITING_USER: "Needs response",
    WAITING_PERMISSION: "Needs approval",
    VERIFYING: "Verifying",
    REVIEW_REQUIRED: "Review changes",
    COMPLETED: "Completed",
    FAILED: "Failed",
    CANCELLED: "Stopped",
    INTERRUPTED: "Interrupted",
  };
  return labels[status] ?? "Unknown";
}
