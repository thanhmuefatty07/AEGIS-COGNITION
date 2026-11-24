import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
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

test("task state breaks identical started_at_ms execution ties by higher revision", () => {
  const snapshot = {
    turns: [
      { role: "assistant", status: "CANCELLED", revision: 3 },
      { role: "assistant", status: "COMPLETED", revision: 6 },
    ],
    executions: [
      { status: "CANCELLED", started_at_ms: 1000, revision: 3 },
      { status: "COMPLETED", started_at_ms: 1000, revision: 6 },
    ],
  };
  assert.equal(deriveTaskState(snapshot), "COMPLETED");
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

test("capability icon registry assigns animate-ui motion archetypes without Sparkles slop and brand mark uses single-path reductive monolith", () => {
  const iconSource = readFileSync(new URL("../src/capability-icons.tsx", import.meta.url), "utf8");
  assert.equal(iconSource.includes("Sparkles"), false, "capability-icons.tsx must not import Sparkles AI-slop icon");
  assert.equal(iconSource.includes("data-animate-ui-icon"), true, "CapabilityIcon must expose data-animate-ui-icon");
  assert.equal(iconSource.includes("data-icon-context"), true, "CapabilityIcon must expose data-icon-context");

  const expectedMonolithPath =
    "M32 4L56 16V28C56 44 44 52 32 60C20 52 8 44 8 28V16ZM32 12L16 20V28C16 32 16 36 20 40L32 24L44 40C48 36 48 32 48 28V20ZM32 36L24 48L32 52L40 48Z";
  assert.equal(iconSource.includes(expectedMonolithPath), true, "capability-icons.tsx must define the pixel-grid-aligned Aegis Greek Shield path");
  const pathNumbers = expectedMonolithPath.match(/\d+/g)?.map(Number) ?? [];
  assert.equal(pathNumbers.length > 0 && pathNumbers.every((n) => n % 4 === 0), true, "Every coordinate in AEGIS_BRAND_PATH must be a multiple of 4 so 16x16 lands on exact integer pixels");

  assert.equal(iconSource.includes('fill="currentColor"'), true, "In-app AegisBrandMark must use currentColor so it follows dark/light themes");

  const svgSource = readFileSync(new URL("../src/assets/aegis-icon.svg", import.meta.url), "utf8");
  assert.equal(svgSource.includes('viewBox="0 0 64 64"'), true, "aegis-icon.svg must use 64x64 geometric grid");
  assert.equal(svgSource.includes(expectedMonolithPath), true, "aegis-icon.svg must contain the single-path Aegis Tectonic Bastion");
  assert.equal(svgSource.includes("@media (prefers-color-scheme: dark)"), true, "aegis-icon.svg must embed prefers-color-scheme: dark for dark browser/OS tab bars");
  assert.equal(svgSource.includes("#0B0B0D") && svgSource.includes("#FAFAFC"), true, "aegis-icon.svg must define #0B0B0D light and #FAFAFC dark fills");
  assert.equal(svgSource.includes("<rect"), false, "aegis-icon.svg must not contain a rounded-square background box");
  assert.equal((svgSource.match(/<path\b/g) ?? []).length, 1, "aegis-icon.svg must consist of a single reductive <path>");

  const indexHtml = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.equal(indexHtml.includes('href="/src/assets/aegis-icon.svg"'), true, "index.html must link /src/assets/aegis-icon.svg as its favicon");

  const iconsBlockMatch = iconSource.match(/const icons:\s*Record<string,\s*LucideIcon>\s*=\s*\{([\s\S]*?)\};/);
  const archetypesBlockMatch = iconSource.match(/export const ICON_MOTION_ARCHETYPES:\s*Record<string,\s*IconMotionArchetype>\s*=\s*\{([\s\S]*?)\};/);
  assert.notEqual(iconsBlockMatch, null);
  assert.notEqual(archetypesBlockMatch, null);
  const parseKeys = (block) =>
    [...block.matchAll(/^\s*(?:"([^"]+)"|([a-zA-Z0-9_-]+))\s*:/gm)].map((m) => m[1] ?? m[2]);
  const iconKeys = parseKeys(iconsBlockMatch[1]);
  const archetypeKeys = new Set(parseKeys(archetypesBlockMatch[1]));
  assert.deepEqual(
    iconKeys.filter((k) => !archetypeKeys.has(k)),
    [],
    "Every registered icon in capability-icons.tsx must have an explicit ICON_MOTION_ARCHETYPES entry",
  );

  const themeSource = readFileSync(new URL("../src/desktop-theme.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  assert.equal(
    themeSource.includes(".aegis-settings-row span {\n  display: block;"),
    false,
    "desktop-theme.css must not use unscoped .aegis-settings-row span { display: block } that breaks .aegis-settings-value",
  );
  assert.equal(
    themeSource.includes(".settings-frame .destination-page,\n.settings-frame .settings-page {\n  grid-column: 1 / -1;\n  grid-template-rows: minmax(0, 1fr);"),
    true,
    "desktop-theme.css must collapse .settings-frame .destination-page to a single 1fr row so Settings fills 100% of viewport height",
  );
  assert.equal(
    /\.panel-empty-state\s*>\s*svg\s*\{[^}]*width:\s*32px/.test(themeSource),
    false,
    "desktop-theme.css must not override .panel-empty-state > svg to 32px",
  );
  assert.equal(
    themeSource.includes(".sidebar-brand .brand {\n  display: inline-flex;\n  min-width: 0;\n  height: 32px;\n  min-height: 32px;\n  align-items: center;\n  gap: 8px;\n  border: 0;\n  border-radius: 8px;\n  padding: 0 8px 0 6px;\n  color: var(--text-primary);\n  background: transparent;"),
    true,
    "desktop-theme.css must keep .sidebar-brand .brand quiet at rest (background: transparent) and synchronized with the 32px/8px sidebar icon button primitive",
  );
  assert.equal(
    /stroke-width:\s*(?:1\.5|1\.55|2)\b/.test(themeSource) || /stroke-width:\s*(?:1\.5|1\.55|2)\b/.test(stylesSource),
    false,
    "CSS must not override icon stroke-width away from canonical 1.75",
  );
  for (const legacyGreenHex of ["#1b221e", "#141a16", "#3c4c40", "#a8c4ac", "#287847", "#8aa28e", "#315139", "#151d17", "#304635"]) {
    assert.equal(
      stylesSource.toLowerCase().includes(legacyGreenHex),
      false,
      `styles.css must not contain legacy sage-green color ${legacyGreenHex}`,
    );
  }
  assert.equal(
    themeSource.includes(".shortcut-settings-card,\n.instruction-settings-card,\n.capability-settings-group,\n.import-settings-card,\n.model-default-row,\n.model-provider-row,\n.model-vendor-empty,\n.model-catalog-row,\n.model-catalog-item,\n.mcp-registry-search,\n.mcp-add-card {\n  border: 1px solid var(--line);\n  border-radius: 12px;\n  background: var(--surface-1);\n}"),
    true,
    "desktop-theme.css must synchronize all Settings card containers to 1px solid var(--line) and 12px border-radius",
  );
  const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.equal(
    appSource.includes("General appearance and network"),
    false,
    "App.tsx global search must not contain stale 'General appearance and network' copy",
  );
  for (const section of ["General", "AI", "Shortcuts", "Instructions", "Models", "Skills", "MCP", "Plugins", "Subagents", "Import", "Projects", "Remote Hosts", "Info"]) {
    assert.equal(
      new RegExp(`\\["${section}",\\s*"[^"]+",\\s*"[a-z-]+"\\]`).test(appSource),
      true,
      `App.tsx global search must index Settings section ${section} with description and icon`,
    );
  }
});

test("zero raw lucide-react icons or non-canonical icon sizes are rendered outside capability-icons.tsx", () => {
  const srcRoot = fileURLToPath(new URL("../src", import.meta.url));
  const collectSourceFiles = (dir) => {
    const entries = readdirSync(dir);
    const files = [];
    for (const entry of entries) {
      const fullPath = join(dir, entry);
      if (statSync(fullPath).isDirectory()) {
        files.push(...collectSourceFiles(fullPath));
      } else if (/\.(ts|tsx)$/.test(entry)) {
        files.push(fullPath);
      }
    }
    return files;
  };

  const files = collectSourceFiles(srcRoot);
  const allowedSizes = new Set(["12", "14", "16", "18"]);

  for (const file of files) {
    const content = readFileSync(file, "utf8");
    const isPrimitiveFile = file.endsWith("capability-icons.tsx");

    if (!isPrimitiveFile) {
      assert.equal(
        /from\s+["']lucide-react["']/.test(content),
        false,
        `Raw lucide-react import found outside capability-icons.tsx in ${file}`,
      );
    }

    const sizeMatches = content.matchAll(/<(?:Icon|CapabilityIcon)\b[^>]*\bsize=\{(\d+)\}/g);
    for (const match of sizeMatches) {
      assert.equal(
        allowedSizes.has(match[1]),
        true,
        `Non-canonical icon size={${match[1]}} in ${file}; expected one of 12, 14, 16, 18`,
      );
    }
  }
});

