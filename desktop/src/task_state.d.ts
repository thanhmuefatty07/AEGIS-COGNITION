import type { ConversationSnapshot, DesktopRunStatus } from "./protocol";

export type WorkbenchTaskState =
  | "DRAFT"
  | "QUEUED"
  | "PREPARING"
  | "RUNNING"
  | "CANCELLING"
  | "WAITING_USER"
  | "WAITING_PERMISSION"
  | "VERIFYING"
  | "REVIEW_REQUIRED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "INTERRUPTED";

export const taskStates: readonly WorkbenchTaskState[];
export function isTaskRunActive(status: string | undefined): boolean;
export function interruptedDesktopRunStatus<T extends { status: string }>(run: T, error: unknown): (Omit<T, "status"> & { status: "INTERRUPTED"; cancel_supported: false; thread_alive: false; error_code: "RUN_NOT_FOUND" }) | null;
export function shouldDrainTaskQueue(activeRunIds: string, queueLength: number, draining: boolean, paused: boolean): boolean;
export function isProjectSwitchBlocked(actionBusy: boolean, activeRun: boolean): boolean;
export function deriveTaskState(
  snapshot: ConversationSnapshot | null | undefined,
  run?: DesktopRunStatus | null,
  approvalPending?: boolean,
): WorkbenchTaskState;
export function taskStateLabel(status: string): string;
