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

export function reorderTaskQueue(queue, promptId, delta, activeConversationId = null, paused = false) {
  const visibleQueue = paused
    ? queue
    : queue.filter((item) => item.conversationId === activeConversationId);
  const visibleIndex = visibleQueue.findIndex((item) => item.id === promptId);
  const targetVisibleIndex = visibleIndex + delta;
  if (visibleIndex < 0 || targetVisibleIndex < 0 || targetVisibleIndex >= visibleQueue.length) {
    return queue;
  }
  const sourceId = visibleQueue[visibleIndex].id;
  const targetId = visibleQueue[targetVisibleIndex].id;
  const next = [...queue];
  const fromIndex = next.findIndex((item) => item.id === sourceId);
  const toIndex = next.findIndex((item) => item.id === targetId);
  if (fromIndex < 0 || toIndex < 0) return queue;
  [next[fromIndex], next[toIndex]] = [next[toIndex], next[fromIndex]];
  return next;
}

export function popQueuedPromptForEdit(queue, promptId, currentMessage = "", currentAttachments = []) {
  const target = queue.find((item) => item.id === promptId);
  if (!target) return null;
  const nextQueue = queue.filter((item) => item.id !== promptId);
  const trimmedCurrent = String(currentMessage ?? "").trim();
  const nextMessage = trimmedCurrent
    ? (target.message ? `${target.message}\n\n${currentMessage}` : String(currentMessage))
    : target.message;
  const targetAttachments = Array.isArray(target.attachments) ? target.attachments : [];
  const existingAttachments = Array.isArray(currentAttachments) ? currentAttachments : [];
  const nextAttachments = existingAttachments.length > 0
    ? [
      ...targetAttachments,
      ...existingAttachments.filter((item) => !targetAttachments.some((existing) => existing.id === item.id)),
    ]
    : targetAttachments;
  return {
    queue: nextQueue,
    message: nextMessage,
    attachments: nextAttachments,
    target,
  };
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
