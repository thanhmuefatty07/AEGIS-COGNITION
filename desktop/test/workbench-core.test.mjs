import assert from "node:assert/strict";
import test from "node:test";
import { resolveWorkbenchShortcut } from "../src/command_registry.js";
import { deriveTaskState, interruptedDesktopRunStatus, isProjectSwitchBlocked, isTaskRunActive, shouldDrainTaskQueue } from "../src/task_state.js";

test("task state gives host run and approval status precedence over the last completed turn", () => {
  const snapshot = {
    turns: [{ role: "assistant", status: "COMPLETED", revision: 2 }],
    executions: [{ status: "COMPLETED", started_at_ms: 10 }],
  };
  assert.equal(deriveTaskState(snapshot), "COMPLETED");
  assert.equal(deriveTaskState(snapshot, { status: "CANCELLING" }), "CANCELLING");
  assert.equal(deriveTaskState(snapshot, null, true), "WAITING_PERMISSION");
  assert.equal(isTaskRunActive("CANCELLING"), true);
  assert.equal(isTaskRunActive("CANCELLED"), false);
});

test("task state follows the newest persisted execution and distinguishes interruption", () => {
  const snapshot = {
    turns: [{ role: "assistant", status: "FAILED", revision: 3 }],
    executions: [
      { status: "COMPLETED", started_at_ms: 10 },
      { status: "INTERRUPTED", started_at_ms: 20 },
    ],
  };
  assert.equal(deriveTaskState(snapshot), "INTERRUPTED");
  assert.equal(deriveTaskState({ turns: [], executions: [] }), "DRAFT");
  assert.equal(deriveTaskState({ turns: [{ role: "assistant", status: "WAITING_APPROVAL", revision: 1 }] }), "WAITING_PERMISSION");
});

test("a missing host run is shown as interrupted and stops active polling", () => {
  const run = {
    schema: "aegis-desktop-run-status-v1",
    run_id: "run-1",
    conversation_id: "conversation-1",
    status: "RUNNING",
    started_at_ms: 10,
    finished_at_ms: null,
    cancel_requested_at_ms: null,
    cancel_supported: true,
    thread_alive: true,
    error_code: null,
  };

  const recovered = interruptedDesktopRunStatus(run, new Error("RUN_NOT_FOUND: task status is no longer available"));

  assert.deepEqual(recovered, {
    ...run,
    status: "INTERRUPTED",
    cancel_supported: false,
    thread_alive: false,
    error_code: "RUN_NOT_FOUND",
  });
  assert.equal(isTaskRunActive(recovered.status), false);
});

test("transient run inspection failures do not impersonate interruption", () => {
  const run = { status: "RUNNING", run_id: "run-1" };
  assert.equal(interruptedDesktopRunStatus(run, new Error("VCS_TIMEOUT: retry later")), null);
  assert.equal(interruptedDesktopRunStatus(run, new Error("request failed")), null);
});

test("a paused task queue waits for review after the prior run becomes unavailable", () => {
  assert.equal(shouldDrainTaskQueue("", 2, false, true), false);
  assert.equal(shouldDrainTaskQueue("", 2, false, false), true);
  assert.equal(shouldDrainTaskQueue("run-1", 2, false, false), false);
  assert.equal(shouldDrainTaskQueue("", 2, true, false), false);
  assert.equal(shouldDrainTaskQueue("", 0, false, false), false);
});

test("project switching stays blocked while any background task is active", () => {
  assert.equal(isProjectSwitchBlocked(false, true), true);
  assert.equal(isProjectSwitchBlocked(true, false), true);
  assert.equal(isProjectSwitchBlocked(false, false), false);
});


test("workbench shortcuts use platform primary modifiers and preserve modifier boundaries", () => {
  const shortcut = (key, values = {}) => resolveWorkbenchShortcut({
    key,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    altKey: false,
    ...values,
  }, "Windows");
  assert.equal(shortcut("p", { ctrlKey: true, shiftKey: true }), "command-palette");
  assert.equal(shortcut("p", { ctrlKey: true }), "quick-open");
  assert.equal(shortcut("f6", { shiftKey: true }), "focus-previous-pane");
  assert.equal(shortcut("b", { metaKey: true }), null);
  assert.equal(shortcut("b", { ctrlKey: true, altKey: true }), null);
  assert.equal(resolveWorkbenchShortcut({ key: "b", ctrlKey: false, metaKey: true, shiftKey: false, altKey: false }, "MacIntel"), "toggle-navigator");
});
