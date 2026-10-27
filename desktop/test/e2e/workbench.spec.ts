import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }, testInfo) => {
  if (
    testInfo.title === "Home opens a project in Explorer and shows selected file details honestly" ||
    testInfo.title === "provider setup shows only a sanitized destination host"
  ) {
    // The cold dev server can still be transforming lazy application chunks during the first page load.
    test.setTimeout(60_000);
  }
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

test("discloses automatic workspace context before connecting and in the Live composer", async ({ page }, testInfo) => {
  const externalRequestHosts = new Set<string>();
  page.on("request", (request) => {
    const hostname = new URL(request.url()).hostname;
    if (hostname !== "127.0.0.1" && hostname !== "localhost") externalRequestHosts.add(hostname);
  });

  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await page.getByLabel("Provider").selectOption("openai-compatible");

  const disclosure = page.getByRole("note", { name: "Live provider data notice" });
  await expect(disclosure).toBeVisible();
  await expect(disclosure).toContainText("Preview-compatible provider (127.0.0.1:8080)");
  await expect(disclosure).not.toContainText("/v1");
  for (const contextCategory of [
    "your message",
    "images you attach",
    "profile instructions",
    "enabled-skill instructions",
    "recent conversation turns",
    "workspace paths and metadata",
    "source excerpts",
    "saved-memory/session excerpts",
    "tool descriptions and results",
  ]) {
    await expect(disclosure).toContainText(contextCategory);
  }
  await expect(disclosure).toContainText("not every category is sent every time");
  await expect(disclosure).toContainText("redaction is incomplete");

  await page.getByLabel("API key").fill("preview-only-not-a-secret");
  await page.getByRole("button", { name: "Connect & discover models" }).click();
  await page.getByRole("button", { name: "Back to app" }).click();

  await expect(page.getByRole("button", { name: "Execution mode · live provider" })).toBeVisible();
  await expect(disclosure).toBeVisible();
  await expect(disclosure).toContainText("Compatible provider (127.0.0.1:8080)");
  await expect(disclosure).not.toContainText("/v1");

  const noticeAccessibilityTree = await disclosure.ariaSnapshot();
  expect(noticeAccessibilityTree).toContain("Live provider data notice.");
  expect(noticeAccessibilityTree).toContain("profile instructions");
  expect(noticeAccessibilityTree).toContain("images you attach");
  await testInfo.attach("provider-notice-accessibility-tree.txt", {
    body: noticeAccessibilityTree,
    contentType: "text/plain",
  });

  const disclosureAccessibility = await new AxeBuilder({ page })
    .include(".provider-context-disclosure")
    .analyze();
  expect(disclosureAccessibility.violations).toEqual([]);

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
      await expect(disclosure).toBeVisible();
      const bounds = await disclosure.boundingBox();
      expect(bounds, `Live notice has no layout box at ${viewport.width}x${viewport.height}`).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
      if (viewport.width === 640 || viewport.width === 1280) {
        await page.screenshot({
          path: testInfo.outputPath(`live-provider-notice-${theme}-${viewport.width}.png`),
          fullPage: false,
          animations: "disabled",
        });
      }
    }
  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  await expect(disclosure).toBeVisible();
  await page.getByRole("textbox", { name: "Message" }).focus();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeFocused();
  await expect(disclosure).toBeVisible();
  await expect(disclosure).toHaveAttribute("role", "note");
  expect([...externalRequestHosts]).toEqual([]);

  await page.getByRole("textbox", { name: "Message" }).fill("Local preview only: inspect this disclosure.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText(/Preview response: I received/)).toBeVisible();
  await expect(disclosure).toBeVisible();
  expect([...externalRequestHosts]).toEqual([]);

  await page.getByRole("button", { name: "Execution mode · live provider" }).click();
  await expect(disclosure).toHaveCount(0);
});

test("provider setup shows only a sanitized destination host", async ({ page }) => {
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Models", exact: true }).click();
  await page.getByLabel("Provider").selectOption("openai-compatible");

  const destinationCard = page.locator(".provider-selection-card");
  await expect(destinationCard).toBeVisible();
  await expect(destinationCard).toContainText("127.0.0.1:8080");
  await expect(destinationCard).not.toContainText("/v1");
  await expect(destinationCard).not.toContainText("http://");

  const adversarialHost = await page.evaluate(async () => {
    const disclosureUrl = new URL("/src/ProviderContextDisclosure.tsx", window.location.origin).href;
    const { providerDestinationHost } = await import(/* @vite-ignore */ disclosureUrl) as {
      providerDestinationHost: (endpoint: string | null | undefined) => string | null;
    };
    return providerDestinationHost("https://user:secret@provider.example:8443/private/v1?token=secret#fragment");
  });
  expect(adversarialHost).toBe("provider.example:8443");
});

test("command palette opens by shortcut, filters actions, and restores focus", async ({ page }) => {
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.focus();
  await page.keyboard.press("Control+Shift+P");

  const palette = page.getByRole("dialog", { name: "Command Palette" });
  await expect(palette).toBeVisible();
  const searchBox = page.getByRole("combobox", { name: "Search commands" });
  await searchBox.fill("Tracked Changes");
  await expect(page.getByRole("option", { name: "Open Tracked Changes" })).toBeVisible();
  await searchBox.fill("Context Cockpit");
  await expect(page.getByRole("option", { name: "Open Context Cockpit" })).toBeVisible();
  await searchBox.fill("Focus Graph");
  await expect(page.getByRole("option", { name: "Open Focus Graph" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(palette).toHaveCount(0);
  await expect(composer).toBeFocused();

  await page.keyboard.press("Control+Shift+P");
  await searchBox.fill("Tracked Changes");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Changes", selected: true })).toBeFocused();

  await page.keyboard.press("Control+Shift+P");
  await searchBox.fill("Context Cockpit");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Context", selected: true })).toBeFocused();
});

test("active tasks remain steerable, queue in order, and distinguish stop request from stop", async ({ page }) => {
  const pageTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(pageTime + 1_000);
  await page.getByRole("textbox", { name: "Message" }).fill("Inspect a preview task");
  await page.getByRole("button", { name: "Send message" }).click();

  const stopButton = page.getByRole("button", { name: "Stop active task" });
  await expect(stopButton).toBeVisible();
  await page.getByRole("textbox", { name: "Message" }).fill("Draft follow-up");
  await page.getByRole("button", { name: "Queue message" }).click();
  await expect(page.getByLabel("Queued task messages")).toContainText("Draft follow-up");

  await page.getByRole("textbox", { name: "Message" }).fill("Unsent composer note");
  await page.getByRole("button", { name: "Edit queued message" }).click();
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveText("Draft follow-up\n\nUnsent composer note");
  await expect(page.getByRole("textbox", { name: "Message" })).toBeFocused();
  await page.getByRole("textbox", { name: "Message" }).fill("Later follow-up");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByRole("textbox", { name: "Message" }).fill("Then summarize the result");
  await page.getByRole("button", { name: "Queue message" }).click();

  const moveUpButtons = page.getByRole("button", { name: "Move queued message up" });
  await expect(moveUpButtons).toHaveCount(2);
  await moveUpButtons.nth(1).click();
  await page.getByRole("button", { name: "Remove queued message" }).nth(1).click();
  await expect(page.getByLabel("Queued task messages")).toContainText("Then summarize the result");

  await stopButton.click();
  await expect(page.getByRole("button", { name: "Stop requested", exact: true })).toBeVisible();
  await page.clock.runFor(2_000);
  await expect(page.getByText("Task stopped by the user.", { exact: true })).toBeVisible();
  await expect(page.getByText("Preview response: I received “Then summarize the result”.", { exact: true })).toBeVisible();
  await expect(page.getByRole("toolbar", { name: "Conversation" }).getByText("Completed", { exact: true })).toBeVisible();
});

test("Home opens a project in Explorer and shows selected file details honestly", async ({ page }) => {
  const openProjectButton = page.getByRole("button", { name: /Open a project|Browse project/ });
  await expect(openProjectButton).toBeVisible();
  await openProjectButton.click();

  const panel = page.getByRole("complementary", { name: "Workspace panel" });
  await expect(panel).toBeVisible();
  const explorer = panel.getByRole("tabpanel", { name: "Files" });
  await expect(explorer).toBeVisible();
  await expect(explorer.getByText("Project files", { exact: true })).toBeVisible();
  await expect(explorer.getByRole("heading", { name: "Reading project files" })).toHaveCount(0);
  await expect(explorer.getByRole("heading", { name: "Bring a project into scope" })).toHaveCount(0);
  await expect(explorer.getByRole("heading", { name: "Unable to read this project" })).toHaveCount(0);
  await expect(explorer.getByText("No files have been indexed in this project.", { exact: true })).toHaveCount(0);

  await explorer.getByRole("button", { name: "App.tsx" }).click();
  await expect(explorer.getByText("desktop/src/App.tsx", { exact: true })).toBeVisible();
  await expect(explorer.getByText(/Preview only · local file contents are not available/)).toBeVisible();
  await expect(explorer.getByRole("status").filter({ hasText: "Opening file" })).toHaveCount(0);
  await expect(explorer.getByRole("alert")).toHaveCount(0);

  await panel.getByRole("tab", { name: "Changes" }).click();
  await expect(panel.getByRole("tabpanel", { name: "Changes" })).toContainText("Preview only · open the desktop app to inspect local Git changes");

  await panel.getByRole("tab", { name: "Focus Graph" }).click();
  await expect(panel.getByRole("tabpanel", { name: "Focus Graph" }).getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
  await panel.getByRole("button", { name: "List", exact: true }).click();
  await expect(panel.getByRole("region", { name: "Project files and relationships" })).toBeVisible();
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
  await expect(page.getByRole("button", { name: /Browse project/ })).toBeVisible();
  await page.getByRole("button", { name: /Browse project/ }).click();
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
