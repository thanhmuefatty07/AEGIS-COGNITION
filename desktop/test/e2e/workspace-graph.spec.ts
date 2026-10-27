import { writeFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { DesktopCommand } from "../../src/protocol";

test.beforeEach(async ({ page }) => {
  test.setTimeout(60_000);
  await page.addInitScript(() => {
    localStorage.setItem("aegis-workbench-v2", "enabled");
    localStorage.setItem("aegis-theme", "dark");
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeVisible();
});

test("unselected Memory Graph guidance stays visible at wide and narrow sizes", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.getByRole("button", { name: /Browse project/ }).click();
  const workspace = page.locator('[data-workbench-pane="inspector"][aria-label="Workspace panel"]');
  await workspace.getByRole("tab", { name: "Focus Graph" }).click();
  const graphPanel = workspace.getByRole("tabpanel", { name: "Focus Graph" });
  await expect(graphPanel.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
  const guidance = graphPanel.getByText(
    "Select a file in Graph or List to view its memory links and search local memory candidates.",
    { exact: true },
  );

  for (const viewport of [
    { width: 1280, height: 800, theme: "light" },
    { width: 640, height: 480, theme: "dark" },
  ] as const) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; }, viewport.theme);
    if (viewport.width <= 1100) {
      await page.getByRole("button", { name: "Open project details" }).click();
      await expect(page.getByRole("dialog", { name: "Workspace panel" })).toBeVisible();
    }
    await expect(guidance).toBeInViewport({ ratio: 1 });
    await expect(graphPanel.getByRole("textbox", { name: "Search memory text for this file" })).toHaveCount(0);
    await expect(graphPanel.getByRole("button", { name: /Save link/ })).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`memory-empty-${viewport.width}x${viewport.height}-${viewport.theme}.png`),
      fullPage: false,
      animations: "disabled",
    });
    await writeFile(
      testInfo.outputPath(`memory-empty-${viewport.width}x${viewport.height}-aria.txt`),
      await graphPanel.ariaSnapshot(),
      "utf8",
    );
  }

  const selectedFile = graphPanel.getByRole("button", { name: /agent\.py/ }).first();
  await selectedFile.click();
  await expect(selectedFile).toHaveAttribute("aria-pressed", "true");
  await expect(guidance).toHaveCount(0);
});

test("selected-file memory inspector is reachable in Graph and List across viewports", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole("button", { name: /Browse project/ }).click();
  const workspace = page.locator('[data-workbench-pane="inspector"][aria-label="Workspace panel"]');
  await workspace.getByRole("tab", { name: "Focus Graph" }).click();
  const graphPanel = workspace.getByRole("tabpanel", { name: "Focus Graph" });
  await expect(graphPanel.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });

  const file = graphPanel.getByRole("button", { name: /desktop\/src\/App\.tsx/ });
  await file.click();
  await expect(file).toHaveAttribute("aria-pressed", "true");
  const memoryHeading = graphPanel.getByRole("heading", { name: "Memory search candidates" });
  const memoryNote = graphPanel.getByText("Local memory search only. Results are candidates, not verified file-memory relationships.", { exact: true });
  await expect(memoryHeading).toBeInViewport({ ratio: 1 });
  await expect(memoryNote).toBeInViewport({ ratio: 1 });

  for (const viewport of [
    { width: 1280, height: 800, theme: "light" },
    { width: 640, height: 480, theme: "dark" },
  ] as const) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; }, viewport.theme);
    if (viewport.width <= 1100) {
      await page.getByRole("button", { name: "Open project details" }).click();
      await expect(page.getByRole("dialog", { name: "Workspace panel" })).toBeVisible();
    }
    await expect(graphPanel).toBeVisible();
    const responsiveFile = graphPanel.getByRole("button", { name: /desktop\/src\/App\.tsx/ });
    await responsiveFile.click();
    await expect(responsiveFile).toHaveAttribute("aria-pressed", "true");
    await expect(memoryHeading).toBeVisible();
    await memoryNote.scrollIntoViewIfNeeded();
    await expect(memoryNote).toBeInViewport({ ratio: 1 });
  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  await graphPanel.getByRole("button", { name: "List" }).click();
  const listRegion = graphPanel.getByRole("region", { name: "Project files and relationships" });
  await expect(listRegion).toBeVisible();
  await expect(listRegion).toHaveAttribute("tabindex", "0");
  const listGeometry = await listRegion.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      overflowY: style.overflowY,
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    };
  });
  expect(listGeometry.overflowY).toBe("auto");
  expect(listGeometry.clientHeight).toBeLessThanOrEqual(184);
  expect(listGeometry.scrollHeight).toBeGreaterThan(listGeometry.clientHeight);
  await listRegion.focus();
  await page.keyboard.press("ArrowDown");
  await expect.poll(() => listRegion.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

  const listFile = graphPanel.getByRole("button", { name: /desktop\/src\/App\.tsx/ });
  await listFile.click();
  await expect(listFile).toHaveAttribute("aria-pressed", "true");
  const inspector = graphPanel.getByRole("complementary", { name: "Selected project map item" });
  await expect(inspector.getByRole("heading", { name: "Saved file-memory relations" })).toBeVisible();
  const listMemoryHeading = inspector.getByRole("heading", { name: "Memory search candidates" });
  await listMemoryHeading.scrollIntoViewIfNeeded();
  await expect(listMemoryHeading).toBeInViewport({ ratio: 1 });
  const listSemantics = await graphPanel.ariaSnapshot();
  expect(listSemantics).toContain("Project files and relationships");
  expect(listSemantics).toContain("Memory search candidates");
  await testInfo.attach("workspace-list-inspector-aria-640-dark.txt", {
    body: listSemantics,
    contentType: "text/plain",
  });
  const listAxe = await new AxeBuilder({ page })
    .include(".map-panel")
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(
    listAxe.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) })),
  ).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("workspace-list-inspector-640-dark.png"),
    fullPage: false,
    animations: "disabled",
  });
});

test("manual memory text search reaches the local service and saves only after explicit confirmation", async ({ page }, testInfo) => {
  const targetPath = "desktop/src/App.tsx";
  const query = "parser limit 100";
  await page.evaluate(async () => {
    const protocolUrl = new URL("/src/protocol.ts", window.location.origin).href;
    const previewUrl = new URL("/src/preview_backend.ts", window.location.origin).href;
    const protocol = await import(/* @vite-ignore */ protocolUrl);
    const preview = await import(/* @vite-ignore */ previewUrl);
    const sourceHash = "a".repeat(64);
    const memoryHash = "b".repeat(64);
    const memory = {
      memory_id: "0x2a",
      owner_id: "private-owner-fixture",
      scope_kind: "USER_PRIVATE",
      lifecycle: "ACTIVE",
      validation: "ACCEPTED",
      revision: 4,
      observed_at_ms: 10,
      content_hash: memoryHash,
      content: "Parser limit is 100 tokens.",
      memory_kind: "SEMANTIC_FACT",
    };
    const calls: Array<{ command: string; payload: Record<string, unknown> }> = [];
    const restore = protocol.installDesktopTransport(async (command: string, payload: Record<string, unknown>) => {
      calls.push({ command, payload: { ...payload } });
      if (command === "memory.search") {
        return { records: payload.query === "parser limit 100" ? [memory] : [] };
      }
      if (command === "memory.file_relations.list") return { relations: [], next_cursor: null };
      if (command === "memory.file_relation.link") {
        return {
          relation: {
            relation_id: "0x91",
            memory_id: payload.memory_id,
            scope_kind: payload.scope_kind,
            project_id: payload.project_id,
            relative_path: payload.relative_path,
            source_revision: payload.source_revision,
            source_content_hash: payload.source_content_hash,
            memory_revision: payload.memory_revision,
            memory_content_hash: payload.memory_content_hash,
            created_at_ms: 11,
            memory_lifecycle: "ACTIVE",
            memory_validation: "ACCEPTED",
            memory_content_current: true,
          },
        };
      }
      const response = await preview.previewRequest(command, payload);
      if (command !== "workspace.source_snapshot" || !response || typeof response !== "object") return response;
      const envelope = response as { snapshot?: { files?: Array<Record<string, unknown>> } };
      if (!envelope.snapshot?.files) return response;
      return {
        ...envelope,
        snapshot: {
          ...envelope.snapshot,
          files: envelope.snapshot.files.map((file) => file.relative_path === "desktop/src/App.tsx"
            ? { ...file, content_hash: sourceHash }
            : file),
        },
      };
    });
    const state = window as typeof window & {
      __m08ManualMemorySearch?: { calls: Array<{ command: string; payload: Record<string, unknown> }>; restore: () => void };
    };
    state.__m08ManualMemorySearch = { calls, restore };
  });

  try {
    await page.getByRole("button", { name: "Switch project", exact: true }).click();
    const projectMenu = page.getByRole("menu", { name: "Switch project" });
    await projectMenu.getByRole("menuitemradio", { name: /AEGIS-COGNITION/ }).click();
    await page.getByRole("button", { name: /Browse project/ }).click();
    const workspace = page.locator('[data-workbench-pane="inspector"][aria-label="Workspace panel"]');
    await workspace.getByRole("tab", { name: "Focus Graph" }).click();
    const graphPanel = workspace.getByRole("tabpanel", { name: "Focus Graph" });
    await expect(graphPanel.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
    await graphPanel.getByRole("button", { name: /desktop\/src\/App\.tsx/ }).click();
    await expect(graphPanel.getByText("No lexical path matches.")).toBeVisible();
    await expect(graphPanel.getByRole("button", { name: /Save link from 0x2a/ })).toHaveCount(0);

    const queryBox = graphPanel.getByRole("textbox", { name: "Search memory text for this file" });
    await queryBox.fill(query);
    await graphPanel.getByRole("button", { name: "Search memories" }).click();
    const manualSearch = await page.evaluate((expectedQuery) => {
      const state = (window as typeof window & { __m08ManualMemorySearch?: { calls: Array<{ command: string; payload: Record<string, unknown> }> } }).__m08ManualMemorySearch;
      return state?.calls.find((call) => call.command === "memory.search" && call.payload.query === expectedQuery)?.payload ?? null;
    }, query);
    expect(manualSearch).toEqual({ query, top_k: 6, scope_kind: "USER_PRIVATE" });
    await expect(graphPanel.getByText("0x2a")).toBeVisible();
    await expect(graphPanel.getByRole("button", { name: "Save link from 0x2a to desktop/src/App.tsx" })).toBeVisible();
    const beforeSave = await page.evaluate(() => {
      const state = (window as typeof window & { __m08ManualMemorySearch?: { calls: Array<{ command: string; payload: Record<string, unknown> }> } }).__m08ManualMemorySearch;
      return {
        searches: state?.calls.filter((call) => call.command === "memory.search").map((call) => call.payload) ?? [],
        links: state?.calls.filter((call) => call.command === "memory.file_relation.link").length ?? -1,
      };
    });
    expect(beforeSave.searches).toContainEqual({ query, top_k: 6, scope_kind: "USER_PRIVATE" });
    expect(beforeSave.links).toBe(0);

    await graphPanel.getByRole("button", { name: "Save link from 0x2a to desktop/src/App.tsx" }).click();
    await expect(graphPanel.getByText("0x2a · USER_PRIVATE", { exact: true })).toBeVisible();
    const saved = await page.evaluate(() => {
      const state = (window as typeof window & { __m08ManualMemorySearch?: { calls: Array<{ command: string; payload: Record<string, unknown> }> } }).__m08ManualMemorySearch;
      return state?.calls.filter((call) => call.command === "memory.file_relation.link").map((call) => call.payload) ?? [];
    });
    expect(saved).toHaveLength(1);
    expect(saved[0]).toMatchObject({
      relative_path: targetPath,
      source_content_hash: "a".repeat(64),
      memory_id: "0x2a",
      scope_kind: "USER_PRIVATE",
      memory_revision: 4,
      memory_content_hash: "b".repeat(64),
    });
    await expect(graphPanel.getByText("private-owner-fixture")).toHaveCount(0);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    await page.screenshot({
      path: testInfo.outputPath("memory-candidate-linked-1920-dark.png"),
      fullPage: false,
      animations: "disabled",
    });
  } finally {
    await page.evaluate(() => {
      const state = (window as typeof window & { __m08ManualMemorySearch?: { restore: () => void } }).__m08ManualMemorySearch;
      state?.restore();
      delete (window as typeof window & { __m08ManualMemorySearch?: unknown }).__m08ManualMemorySearch;
    });
  }
});

test("Focus Graph controls and provenance stay reachable at narrow and wide viewports", async ({ page }, testInfo) => {
  await page.evaluate(async () => {
    const protocolUrl = new URL("/src/protocol.ts", window.location.origin).href;
    const previewUrl = new URL("/src/preview_backend.ts", window.location.origin).href;
    const protocol = await import(/* @vite-ignore */ protocolUrl);
    const preview = await import(/* @vite-ignore */ previewUrl);
    const calls: DesktopCommand[] = [];
    const restore = protocol.installDesktopTransport(async (command: DesktopCommand, payload: Record<string, unknown>) => {
      calls.push(command);
      const response = await preview.previewRequest(command, payload);
      if (command !== "workspace.source_snapshot" || !response || typeof response !== "object") return response;
      const envelope = response as { snapshot?: Record<string, unknown> };
      if (!envelope.snapshot) return response;
      return { ...envelope, snapshot: { ...envelope.snapshot, overflowed: true } };
    });
    const state = window as typeof window & { __workspaceGraphOverflow?: { calls: DesktopCommand[]; restore: () => void } };
    state.__workspaceGraphOverflow = { calls, restore };
  });
  await page.getByRole("button", { name: "Switch project", exact: true }).click();
  const projectMenu = page.getByRole("menu", { name: "Switch project" });
  await projectMenu.getByRole("menuitemradio", { name: /AEGIS-COGNITION/ }).click();
  await expect.poll(() => page.evaluate(() => {
    const state = window as typeof window & { __workspaceGraphOverflow?: { calls: DesktopCommand[] } };
    return state.__workspaceGraphOverflow?.calls.filter((command) => command === "workspace.source_snapshot").length ?? 0;
  })).toBeGreaterThan(0);
  await page.getByRole("button", { name: /Browse project/ }).click();
  const workspace = page.locator('[data-workbench-pane="inspector"][aria-label="Workspace panel"]');
  await expect(page.getByRole("complementary", { name: "Workspace panel" })).toBeVisible();
  await workspace.getByRole("tab", { name: "Focus Graph" }).click();
  const graphPanel = workspace.getByRole("tabpanel", { name: "Focus Graph" });
  await expect(graphPanel.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
  const sourceSnapshotCalls = await page.evaluate(() => {
    const state = window as typeof window & { __workspaceGraphOverflow?: { calls: DesktopCommand[] } };
    return state.__workspaceGraphOverflow?.calls.filter((command) => command === "workspace.source_snapshot").length ?? 0;
  });
  expect(sourceSnapshotCalls).toBeGreaterThan(0);
  const overflowNotice = graphPanel.getByText(/The source scan reported overflow\./);
  await expect(overflowNotice).toBeVisible();
  await expect(overflowNotice).toContainText("the full workspace total is unavailable");
  await page.evaluate(() => {
    const state = window as typeof window & { __workspaceGraphOverflow?: { restore: () => void } };
    state.__workspaceGraphOverflow?.restore();
    delete state.__workspaceGraphOverflow;
  });
  await expect(graphPanel.getByLabel("Folder")).toBeVisible();
  await expect(graphPanel.getByLabel("Find a file or language")).toBeVisible();
  await expect(graphPanel.getByRole("button", { name: "List" })).toBeVisible();
  await expect(graphPanel.getByRole("complementary", { name: "Selected project map item" })).toBeVisible();
  const graphCanvas = graphPanel.getByRole("region", { name: "Project graph canvas" });
  await expect(graphCanvas).toBeVisible();

  for (const view of [
    { width: 640, height: 480, theme: "dark", capture: true },
    { width: 1280, height: 800, theme: "light", capture: true },
    { width: 1920, height: 1080, theme: "dark", capture: true },
  ] as const) {
    await page.setViewportSize({ width: view.width, height: view.height });
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, view.theme);

    if (view.width <= 1100) {
      await page.getByRole("button", { name: "Open project details" }).click();
      await expect(page.getByRole("dialog", { name: "Workspace panel" })).toBeVisible();
    } else {
      await expect(page.getByRole("complementary", { name: "Workspace panel" })).toBeVisible();
    }
    await expect(graphPanel).toBeVisible();

    const bounds = await graphPanel.boundingBox();
    expect(bounds, view.width + "×" + view.height + " graph panel is rendered").not.toBeNull();
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(view.width + 1);
    const semantics = await graphPanel.ariaSnapshot();
    expect(semantics).toContain("Project graph canvas");
    expect(semantics).toContain("Folder");
    expect(semantics).toContain("Zoom in");
    await testInfo.attach(`workspace-graph-aria-${view.width}-${view.theme}.txt`, {
      body: semantics,
      contentType: "text/plain",
    });
    await expect(graphPanel.getByRole("button", { name: "Fit width" })).toBeVisible();

    if (view.capture) {
      await page.screenshot({
        path: testInfo.outputPath("workspace-graph-" + view.width + "-" + view.theme + ".png"),
        fullPage: false,
        animations: "disabled",
      });
    }
  }

  await graphPanel.getByRole("button", { name: "Zoom in" }).click();
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("125%");
  await graphPanel.getByRole("button", { name: "Fit width" }).click();
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("100%");

  const scrollGeometry = await graphCanvas.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(scrollGeometry.scrollHeight).toBeGreaterThan(scrollGeometry.clientHeight);
  await graphCanvas.focus();
  const beforeKeyboardPan = await graphCanvas.evaluate((element) => element.scrollTop);
  await page.keyboard.press("ArrowDown");
  await expect.poll(() => graphCanvas.evaluate((element) => element.scrollTop)).toBeGreaterThan(beforeKeyboardPan);

  await graphCanvas.evaluate((element) => {
    element.scrollTop = 0;
  });
  await expect.poll(() => graphCanvas.evaluate((element) => element.scrollTop)).toBe(0);
  const beforePointerPan = await graphCanvas.evaluate((element) => element.scrollTop);
  const canvasBounds = await graphCanvas.boundingBox();
  expect(canvasBounds).not.toBeNull();
  await page.mouse.move(canvasBounds!.x + canvasBounds!.width / 2, canvasBounds!.y + canvasBounds!.height / 2);
  await page.mouse.wheel(0, 120);
  await expect.poll(() => graphCanvas.evaluate((element) => element.scrollTop)).toBeGreaterThan(beforePointerPan);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const reducedMotion = await graphCanvas.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      requested: matchMedia("(prefers-reduced-motion: reduce)").matches,
      scrollBehavior: style.scrollBehavior,
      animationDuration: style.animationDuration,
      transitionDuration: style.transitionDuration,
    };
  });
  expect(reducedMotion.requested).toBe(true);
  expect(reducedMotion.scrollBehavior).toBe("auto");
  const durationMs = (value: string) => Number.parseFloat(value) * (value.endsWith("ms") ? 1 : 1000);
  expect(durationMs(reducedMotion.animationDuration)).toBeLessThanOrEqual(0.01);
  expect(durationMs(reducedMotion.transitionDuration)).toBeLessThanOrEqual(0.01);

  const results = await new AxeBuilder({ page })
    .include(".map-panel")
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  await testInfo.attach("workspace-graph-axe-summary.json", {
    body: JSON.stringify({
      viewport: page.viewportSize(),
      tags: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
      violationCount: results.violations.length,
      violations: results.violations.map(({ id, impact, description, help, nodes }) => ({
        id,
        impact,
        description,
        help,
        targets: nodes.map((node) => node.target),
      })),
      passCount: results.passes.length,
      incompleteCount: results.incomplete.length,
    }, null, 2),
    contentType: "application/json",
  });
  expect(
    results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical")
      .map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) })),
  ).toEqual([]);
  for (const theme of ["dark", "light"] as const) {
    await page.evaluate((appearance) => {
      document.documentElement.dataset.theme = appearance;
    }, theme);
    const noticeContrast = await new AxeBuilder({ page })
      .include(".graph-notice")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(
      noticeContrast.violations.filter((violation) => violation.id === "color-contrast")
        .map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) })),
    ).toEqual([]);
  }
});

test("saved relation history recovers from an invalidated cursor", async ({ page }, testInfo) => {
  const targetPath = "desktop/src/App.tsx";
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.evaluate(async () => {
    const protocolUrl = new URL("/src/protocol.ts", window.location.origin).href;
    const previewUrl = new URL("/src/preview_backend.ts", window.location.origin).href;
    const protocol = await import(/* @vite-ignore */ protocolUrl);
    const preview = await import(/* @vite-ignore */ previewUrl);
    const savedSourceHash = "a".repeat(64);
    const memoryHash = "b".repeat(64);
    const target = "desktop/src/App.tsx";
    const memory = {
      memory_id: "0x2a",
      owner_id: "private-owner-fixture",
      scope_kind: "USER_PRIVATE",
      lifecycle: "ACTIVE",
      validation: "ACCEPTED",
      revision: 4,
      observed_at_ms: 10,
      content_hash: memoryHash,
      content: "Synthetic relation journey fixture.",
      memory_kind: "SEMANTIC_FACT",
    };
    const relation = {
      relation_id: "0x91",
      memory_id: "0x2a",
      scope_kind: "USER_PRIVATE",
      project_id: "preview-project",
      relative_path: target,
      source_revision: "preview-r1",
      source_content_hash: savedSourceHash,
      memory_revision: 4,
      memory_content_hash: memoryHash,
      created_at_ms: 11,
      memory_lifecycle: "ACTIVE",
      memory_validation: "ACCEPTED",
      memory_content_current: true,
    };
    const calls: Array<{ command: string; payload: Record<string, unknown> }> = [];
    let firstPageReads = 0;
    const restore = protocol.installDesktopTransport(async (command: string, payload: Record<string, unknown>) => {
      calls.push({ command, payload: { ...payload } });
      if (command === "memory.search") {
        return { records: payload.query === target ? [memory] : [] };
      }
      if (command === "memory.file_relation.link") {
        throw new Error("The preview has no source hash and must not save a relation.");
      }
      if (command === "memory.file_relations.list") {
        if (payload.relative_path !== target) return { relations: [], next_cursor: null };
        if (typeof payload.cursor === "string") {
          throw new Error("INVALID_ARGUMENT: cursor is invalid for this relation query");
        }
        firstPageReads += 1;
        return { relations: [relation], next_cursor: firstPageReads === 1 ? "synthetic-invalidated-cursor" : null };
      }
      if (command === "memory.file_relation.unlink") return { removed: true };
      return preview.previewRequest(command, payload);
    });
    const state = window as typeof window & {
      __m08RelationJourney?: { calls: Array<{ command: string; payload: Record<string, unknown> }>; restore: () => void };
    };
    state.__m08RelationJourney = { calls, restore };
  });

  try {
    await page.getByRole("button", { name: /Browse project/ }).click();
    const workspace = page.locator('[data-workbench-pane="inspector"][aria-label="Workspace panel"]');
    await workspace.getByRole("tab", { name: "Focus Graph" }).click();
    const graphPanel = workspace.getByRole("tabpanel", { name: "Focus Graph" });
    await expect(graphPanel.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
    await graphPanel.getByRole("button", { name: /desktop\/src\/App\.tsx/ }).click();

    await expect(graphPanel.getByText("Local memory search only. Results are candidates, not verified file-memory relationships.")).toBeVisible();
    await expect(graphPanel.getByText(/Candidate · semantic fact/)).toBeVisible();
    await expect(graphPanel.getByLabel("Relation status: Stale source")).toBeVisible();
    const provenance = graphPanel.locator("details.memory-provenance");
    await graphPanel.getByText("Source and memory provenance").click();
    await expect(provenance.getByText("Unavailable", { exact: true })).toBeVisible();
    await expect(graphPanel.getByText("A link needs a current file and memory content hash.")).toBeVisible();
    await expect(graphPanel.getByRole("button", { name: "Save link from 0x2a to desktop/src/App.tsx" })).toHaveCount(0);
    expect(await page.evaluate(() => {
      const state = (window as typeof window & { __m08RelationJourney?: { calls: Array<{ command: string }> } }).__m08RelationJourney;
      return state?.calls.filter((call) => call.command === "memory.file_relation.link").length ?? -1;
    })).toBe(0);
    const savedRelations = graphPanel.locator("#saved-file-memory-relations");
    await expect(graphPanel.getByText("0x2a · USER_PRIVATE", { exact: true })).toBeVisible();
    await expect(savedRelations.getByText("a".repeat(64), { exact: true })).toBeVisible();
    await expect(savedRelations.getByText("b".repeat(64), { exact: true })).toBeVisible();
    await expect(graphPanel.getByText("private-owner-fixture")).toHaveCount(0);

    await graphPanel.getByRole("button", { name: "Load more saved relations" }).click();
    await expect(graphPanel.getByRole("alert")).toHaveText("INVALID_ARGUMENT: cursor is invalid for this relation query");
    await page.evaluate(() => {
      document.documentElement.dataset.theme = "light";
    });
    const lightErrorAccessibility = await new AxeBuilder({ page })
      .include(".map-panel")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    await testInfo.attach("saved-relation-light-error-axe-summary.json", {
      body: JSON.stringify({
        viewport: page.viewportSize(),
        violationCount: lightErrorAccessibility.violations.length,
        violations: lightErrorAccessibility.violations.map(({ id, impact, nodes }) => ({
          id,
          impact,
          targets: nodes.map((node) => node.target),
        })),
      }, null, 2),
      contentType: "application/json",
    });
    expect(
      lightErrorAccessibility.violations.filter((violation) => violation.id === "color-contrast")
        .map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) })),
    ).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath("saved-relation-light-error-1280.png"),
      fullPage: false,
      animations: "disabled",
    });

    await page.evaluate(() => {
      document.documentElement.dataset.theme = "dark";
    });
    await graphPanel.getByRole("button", { name: "Restart saved relation history" }).click();
    await expect(graphPanel.getByText("Saved relation history restarted from the first page.")).toBeVisible();
    await expect(graphPanel.getByLabel("Relation status: Stale source")).toBeVisible();
    await expect(graphPanel.getByRole("button", { name: "Load more saved relations" })).toHaveCount(0);

    const state = await page.evaluate(() => {
      const calls = (window as typeof window & { __m08RelationJourney?: { calls: Array<{ command: string; payload: Record<string, unknown> }> } }).__m08RelationJourney?.calls ?? [];
      return {
        links: calls.filter((call) => call.command === "memory.file_relation.link").map((call) => call.payload),
        targetPages: calls.filter((call) => call.command === "memory.file_relations.list" && call.payload.relative_path === "desktop/src/App.tsx")
          .map((call) => ({ cursor: typeof call.payload.cursor === "string" ? call.payload.cursor : null, limit: call.payload.limit })),
      };
    });
    expect(state.links).toEqual([]);
    expect(state.targetPages).toEqual([
      { cursor: null, limit: 24 },
      { cursor: "synthetic-invalidated-cursor", limit: 24 },
      { cursor: null, limit: 24 },
    ]);

    const semantics = await graphPanel.ariaSnapshot();
    expect(semantics).toContain("Saved file-memory relations");
    expect(semantics).toContain("Stale source");
    await testInfo.attach("saved-relation-history-aria.txt", { body: semantics, contentType: "text/plain" });
    await page.screenshot({
      path: testInfo.outputPath("saved-relation-history-1280-dark.png"),
      fullPage: false,
      animations: "disabled",
    });
    const accessibility = await new AxeBuilder({ page })
      .include(".map-panel")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    await testInfo.attach("saved-relation-history-axe-summary.json", {
      body: JSON.stringify({
        viewport: page.viewportSize(),
        violationCount: accessibility.violations.length,
        violations: accessibility.violations.map(({ id, impact, nodes }) => ({
          id,
          impact,
          targets: nodes.map((node) => node.target),
        })),
      }, null, 2),
      contentType: "application/json",
    });
    expect(accessibility.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical")).toEqual([]);

  } finally {
    await page.evaluate(() => {
      const state = (window as typeof window & { __m08RelationJourney?: { restore: () => void } }).__m08RelationJourney;
      state?.restore();
      delete (window as typeof window & { __m08RelationJourney?: unknown }).__m08RelationJourney;
    });
  }
});
