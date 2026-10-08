import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }, testInfo) => {
  if (testInfo.title === "active tasks remain steerable, queue in order, and distinguish stop request from stop") {
    await page.clock.install();
  }
  await page.addInitScript(() => {
    localStorage.setItem("aegis-workbench-v2", "enabled");
    localStorage.setItem("aegis-theme", "dark");
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeVisible();
});

test("command palette opens by shortcut, filters actions, and restores focus", async ({ page }) => {
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.focus();
  await page.keyboard.press("Control+Shift+P");

  const palette = page.getByRole("dialog", { name: "Command Palette" });
  await expect(palette).toBeVisible();
  await page.getByRole("combobox", { name: "Search commands" }).fill("Focus Graph");
  await expect(page.getByRole("option", { name: "Open Focus Graph" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(palette).toHaveCount(0);
  await expect(composer).toBeFocused();
});

test("active tasks remain steerable, queue in order, and distinguish stop request from stop", async ({ page }) => {
  const pageTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(pageTime + 1_000);
  await page.getByRole("textbox", { name: "Message" }).fill("Inspect a preview task");
  await page.getByRole("button", { name: "Send message" }).click();

  const stopButton = page.getByRole("button", { name: "Stop active task" });
  await expect(stopButton).toBeVisible();
  await page.getByRole("textbox", { name: "Message" }).fill("Then summarize the result");
  await page.getByRole("button", { name: "Queue message" }).click();
  await expect(page.getByLabel("Queued task messages")).toContainText("Then summarize the result");

  await stopButton.click();
  await expect(page.getByRole("button", { name: "Stop requested", exact: true })).toBeVisible();
  await page.clock.runFor(2_000);
  await expect(page.getByText("Task stopped by the user.", { exact: true })).toBeVisible();
  await expect(page.getByText("Preview response: I received “Then summarize the result”.", { exact: true })).toBeVisible();
  await expect(page.getByRole("toolbar", { name: "Conversation" }).getByText("Completed", { exact: true })).toBeVisible();
});

test("Explorer, Changes, and Focus Graph label browser data honestly", async ({ page }) => {
  await page.getByRole("button", { name: "Details" }).click();
  await page.getByRole("tab", { name: "Files" }).click();
  await page.getByRole("button", { name: "App.tsx" }).click();
  await expect(page.getByText(/Preview only · local file contents are not available/)).toBeVisible();

  await page.getByRole("tab", { name: "Changes" }).click();
  await expect(page.getByText(/Preview only · open the desktop app to inspect local Git changes/)).toBeVisible();

  await page.getByRole("tab", { name: "Focus Graph" }).click();
  await expect(page.getByText(/Revision-bound project map/)).toBeVisible();
  await page.getByRole("button", { name: "List", exact: true }).click();
  await expect(page.getByRole("list", { name: "Project files and relationships" })).toBeVisible();
});

test("capability setup stays explicit and does not contact the public directory on open", async ({ page }) => {
  const registryRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).hostname === "registry.modelcontextprotocol.io") {
      registryRequests.push(request.url());
    }
  });

  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(page.getByText(/The skill stays disabled until enabled, and bundled scripts are never run by import\./)).toBeVisible();

  await page.getByRole("button", { name: "MCP", exact: true }).click();
  await expect(page.getByRole("heading", { name: "MCP", exact: true })).toBeVisible();
  await expect(page.getByText(/Search the official public directory/)).toBeVisible();
  await expect(page.getByText(/nothing is installed or started/i)).toBeVisible();

  await page.getByRole("button", { name: "Back to app" }).click();
  await page.getByRole("button", { name: "Utilities", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Utilities" })).toBeVisible();
  await expect(page.getByText(/Import copies and validates files without running code\./)).toBeVisible();
  await expect(page.getByRole("button", { name: "Import folder" })).toBeDisabled();
  expect(registryRequests).toEqual([]);
});

test("skill and MCP settings remain readable without horizontal clipping", async ({ page }, testInfo) => {
  await page.getByRole("button", { name: "Settings" }).click();

  for (const section of ["Skills", "MCP"] as const) {
    await page.getByRole("button", { name: section, exact: true }).click();
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();

    for (const theme of ["dark", "light"] as const) {
      await page.evaluate((appearance) => {
        document.documentElement.dataset.theme = appearance;
      }, theme);

      for (const viewport of [
        { width: 640, height: 480 },
        { width: 1280, height: 800 },
        { width: 1920, height: 1080 },
      ]) {
        await page.setViewportSize(viewport);
        const documentOverflows = await page.evaluate(
          () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
        );
        expect(documentOverflows, `${section} settings overflowed at ${viewport.width}x${viewport.height}`).toBe(false);

        if ((viewport.width === 640 && theme === "dark") || (viewport.width === 1280 && theme === "light")) {
          await page.screenshot({
            path: testInfo.outputPath(`${section.toLowerCase()}-${viewport.width}-${theme}.png`),
            fullPage: false,
          });
        }
      }
    }
  }
});

test("home and inspector reflow without document horizontal scroll at desktop and minimum viewports", async ({ page }) => {
  await page.getByRole("button", { name: "Details" }).click();
  await page.getByRole("tab", { name: "Changes" }).click();

  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 1920, height: 1080 },
    { width: 640, height: 480 },
  ]) {
    await page.setViewportSize(viewport);
    const documentOverflows = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(documentOverflows, `document overflowed at ${viewport.width}x${viewport.height}`).toBe(false);
    if (viewport.width === 640) {
      await page.screenshot({ path: test.info().outputPath("home-640x480-dark.png"), fullPage: true });
    }
  }
});

test("home has no serious or critical axe violations in light or dark appearance", async ({ page }) => {
  for (const theme of ["dark", "light"] as const) {
    await page.evaluate((appearance) => {
      document.documentElement.dataset.theme = appearance;
    }, theme);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    const severe = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical");
    expect(
      severe,
      `${theme}: ${JSON.stringify(severe.map(({ id, description, nodes }) => ({ id, description, nodes: nodes.map((node) => node.target) })), null, 2)}`,
    ).toEqual([]);
  }
});
