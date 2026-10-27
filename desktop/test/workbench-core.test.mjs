import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { resolveWorkbenchShortcut, workbenchInspectorCommands } from "../src/command_registry.js";
import { deriveTaskState, interruptedDesktopRunStatus, isProjectSwitchBlocked, isTaskRunActive, popQueuedPromptForEdit, reorderTaskQueue, shouldDrainTaskQueue } from "../src/task_state.js";

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

test("reorderTaskQueue swaps visible items for the active conversation while preserving interleaved multi-conversation positions", () => {
  const queue = [
    { id: "a-1", conversationId: "conv-a", message: "First A", attachments: [] },
    { id: "b-1", conversationId: "conv-b", message: "Only B", attachments: [] },
    { id: "a-2", conversationId: "conv-a", message: "Second A", attachments: [] },
  ];
  const swappedActive = reorderTaskQueue(queue, "a-2", -1, "conv-a", false);
  assert.deepEqual(swappedActive.map((item) => item.id), ["a-2", "b-1", "a-1"]);
  assert.equal(reorderTaskQueue(queue, "a-1", -1, "conv-a", false), queue);
  assert.equal(reorderTaskQueue(queue, "a-2", 1, "conv-a", false), queue);

  const swappedPaused = reorderTaskQueue(queue, "b-1", -1, "conv-a", true);
  assert.deepEqual(swappedPaused.map((item) => item.id), ["b-1", "a-1", "a-2"]);
});

test("popQueuedPromptForEdit preserves unsent composer drafts and deduplicates attachments", () => {
  const queue = [
    { id: "q-1", conversationId: "conv-a", message: "Queued prompt", attachments: [{ id: "img-1", name: "a.png" }] },
    { id: "q-2", conversationId: "conv-a", message: "Second prompt", attachments: [] },
  ];
  const emptyComposerPop = popQueuedPromptForEdit(queue, "q-1", "", []);
  assert.equal(emptyComposerPop?.message, "Queued prompt");
  assert.deepEqual(emptyComposerPop?.attachments.map((item) => item.id), ["img-1"]);
  assert.deepEqual(emptyComposerPop?.queue.map((item) => item.id), ["q-2"]);

  const draftPreservedPop = popQueuedPromptForEdit(
    queue,
    "q-1",
    "Unsent composer note",
    [{ id: "img-1", name: "a.png" }, { id: "img-2", name: "b.png" }],
  );
  assert.equal(draftPreservedPop?.message, "Queued prompt\n\nUnsent composer note");
  assert.deepEqual(draftPreservedPop?.attachments.map((item) => item.id), ["img-1", "img-2"]);
  assert.equal(popQueuedPromptForEdit(queue, "missing", "draft", []), null);
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
  assert.deepEqual(
    workbenchInspectorCommands.map((command) => ({ id: command.id, label: command.label, tab: command.tab })),
    [
      { id: "open-files", label: "Open Project Explorer", tab: "files" },
      { id: "open-changes", label: "Open Tracked Changes", tab: "changes" },
      { id: "open-map", label: "Open Focus Graph", tab: "map" },
      { id: "open-activity", label: "Open Task Activity", tab: "activity" },
      { id: "open-context", label: "Open Context Cockpit", tab: "context" },
    ],
  );
});

test("workbench v2 reduced-motion stylesheet targets root attribute and interactive controls", () => {
  const css = readFileSync(new URL("../src/desktop-workbench-v2.css", import.meta.url), "utf8");
  const reducedMotionIndex = css.indexOf("@media (prefers-reduced-motion: reduce)");
  assert.notEqual(reducedMotionIndex, -1);
  const reducedMotionBlock = css.slice(reducedMotionIndex);
  assert.equal(reducedMotionBlock.includes('.app-frame[data-workbench-v2="true"]'), false);
  for (const selector of [
    ':root[data-workbench-v2="true"] .sidebar-nav-item',
    ".run-stop-button",
    ".task-queue-item button",
    ".command-palette-results button",
    ".changes-file",
    ".graph-view-switch button",
    ".graph-edge",
  ]) {
    assert.equal(reducedMotionBlock.includes(selector), true, `missing reduced-motion selector: ${selector}`);
  }
});

