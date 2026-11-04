import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { DesktopCommand } from "../../src/protocol";

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    localStorage.setItem("aegis-workbench-v2", "enabled");
    localStorage.setItem("aegis-theme", "dark");
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeVisible();
});

async function assertHallmarkAndImpeccableGatesOnCurrentSurface(page: Page, surfaceLabel: string) {
  // Gate 63 (Anti-Punctuation-Stuffing): zero text nodes OR leaf elements with 2+ middle dots, bullets, em-dashes, or pipes
  const gate63Violations = await page.evaluate(() => {
    const violations: string[] = [];
    const excludedTags = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "NOSCRIPT", "TEXTAREA"]);
    const pattern = /(\S+\s*(?:·|•|—|\|)\s*){2,}\S+/;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let current = walker.nextNode();
    while (current) {
      const parent = current.parentElement;
      if (parent && !excludedTags.has(parent.tagName) && !parent.closest("code, pre, script, style")) {
        const text = (current.nodeValue ?? "").trim();
        if (text.length > 0 && pattern.test(text)) {
          violations.push(`textNode in ${parent.tagName.toLowerCase()}.${parent.className}: ${JSON.stringify(text)}`);
        }
      }
      current = walker.nextNode();
    }
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      if (excludedTags.has(el.tagName) || el.closest("code, pre, script, style")) continue;
      if (el.getClientRects().length === 0) continue;
      // Check elements whose children are only inline formatting or <wbr>
      const hasBlockOrSpanChildren = Array.from(el.children).some((child) => child.tagName !== "WBR");
      if (!hasBlockOrSpanChildren) {
        const fullText = (el.textContent ?? "").trim();
        if (fullText.length > 0 && pattern.test(fullText)) {
          violations.push(`element ${el.tagName.toLowerCase()}.${el.className}: ${JSON.stringify(fullText)}`);
        }
      }
    }
    return violations;
  });
  expect(gate63Violations, `Gate 63 punctuation-stuffing on ${surfaceLabel}`).toEqual([]);

  // Gate 48 & 67 (Anti-Uppercase-Tracking): zero visible elements (fontSize <= 15px) with uppercase + letterSpacing > 0.5px
  const gate48And67Violations = await page.evaluate(() => {
    const violations: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      if (el.getClientRects().length === 0) continue;
      const style = window.getComputedStyle(el);
      if (style.visibility === "hidden" || style.display === "none") continue;
      const fontSize = Number.parseFloat(style.fontSize);
      const letterSpacing = Number.parseFloat(style.letterSpacing);
      if (
        style.textTransform === "uppercase" &&
        Number.isFinite(letterSpacing) &&
        letterSpacing > 0.5 &&
        (!Number.isFinite(fontSize) || fontSize <= 15)
      ) {
        violations.push(
          `${el.tagName.toLowerCase()}.${el.className} (font-size: ${style.fontSize}, letter-spacing: ${style.letterSpacing})`,
        );
      }
    }
    return violations;
  });
  expect(gate48And67Violations, `Gate 48/67 uppercase tracking on ${surfaceLabel}`).toEqual([]);

  // Anti-Nested-Card & Asymmetric Left-Border Gate
  const cardViolations = await page.evaluate(() => {
    const asymmetricBorders: string[] = [];
    const deepNestedCards: string[] = [];
    const ignoredCardTags = new Set([
      "BUTTON",
      "INPUT",
      "SELECT",
      "TEXTAREA",
      "SPAN",
      "SMALL",
      "STRONG",
      "EM",
      "CODE",
      "PRE",
      "KBD",
      "I",
      "SVG",
      "PATH",
      "LABEL",
      "A",
    ]);
    const isBorderedCard = (el: HTMLElement): boolean => {
      if (ignoredCardTags.has(el.tagName)) return false;
      if (
        el.classList.contains("app-frame") ||
        el.classList.contains("sidebar") ||
        el.classList.contains("chat-view") ||
        el.classList.contains("work-panel") ||
        el.classList.contains("destination-page") ||
        el.classList.contains("settings-nav") ||
        el.classList.contains("settings-content")
      ) {
        return false;
      }
      const style = window.getComputedStyle(el);
      const topW = Number.parseFloat(style.borderTopWidth);
      const bottomW = Number.parseFloat(style.borderBottomWidth);
      const leftW = Number.parseFloat(style.borderLeftWidth);
      const rightW = Number.parseFloat(style.borderRightWidth);
      const bg = style.backgroundColor;
      const hasBg = bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent";
      return (
        hasBg &&
        topW >= 1 &&
        bottomW >= 1 &&
        leftW >= 1 &&
        rightW >= 1 &&
        style.borderTopStyle !== "none" &&
        style.borderBottomStyle !== "none"
      );
    };

    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      if (el.getClientRects().length === 0) continue;
      const style = window.getComputedStyle(el);
      const leftW = Number.parseFloat(style.borderLeftWidth);
      const topW = Number.parseFloat(style.borderTopWidth);
      if (leftW >= 2 && leftW > topW) {
        asymmetricBorders.push(
          `${el.tagName.toLowerCase()}.${el.className} (border-left: ${style.borderLeftWidth}, border-top: ${style.borderTopWidth})`,
        );
      }
      if (el.classList.contains("provider-context-disclosure") && style.borderLeftWidth !== "1px") {
        asymmetricBorders.push(`.provider-context-disclosure borderLeftWidth=${style.borderLeftWidth}`);
      }
      if (isBorderedCard(el)) {
        let ancestorCards = 0;
        let parent = el.parentElement;
        while (parent && parent !== document.body) {
          if (isBorderedCard(parent)) ancestorCards += 1;
          parent = parent.parentElement;
        }
        if (ancestorCards >= 2) {
          deepNestedCards.push(`${el.tagName.toLowerCase()}.${el.className} (depth ${ancestorCards + 1})`);
        }
      }
    }
    return { asymmetricBorders, deepNestedCards };
  });
  expect(cardViolations.asymmetricBorders, `Asymmetric border-left on ${surfaceLabel}`).toEqual([]);
  expect(cardViolations.deepNestedCards, `Nested cards 3+ deep on ${surfaceLabel}`).toEqual([]);

  // Runtime Icon & Copy Synchronization Gate
  const iconAndCopyViolations = await page.evaluate(() => {
    const violations: string[] = [];
    const allowedSizes = new Set(["12", "14", "16", "18"]);
    for (const svg of Array.from(document.querySelectorAll<SVGElement>("svg.aegis-capability-icon"))) {
      const rect = svg.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      const declaredSize = svg.getAttribute("data-icon-size") ?? "";
      if (!allowedSizes.has(declaredSize)) {
        violations.push(`non-canonical data-icon-size="${declaredSize}" on ${svg.getAttribute("data-capability-icon")}`);
      }
      const expectedPx = Number(declaredSize);
      const computed = window.getComputedStyle(svg);
      const layoutW = Number.parseFloat(computed.width);
      const layoutH = Number.parseFloat(computed.height);
      if (Math.abs(layoutW - expectedPx) > 0.5 || Math.abs(layoutH - expectedPx) > 0.5) {
        violations.push(
          `icon ${svg.getAttribute("data-capability-icon")} has layout size ${computed.width}x${computed.height} instead of ${expectedPx}px`,
        );
      }
      if (computed.strokeWidth && computed.strokeWidth !== "1.75px") {
        violations.push(`icon ${svg.getAttribute("data-capability-icon")} has strokeWidth=${computed.strokeWidth}`);
      }
    }
    const bodyText = document.body.innerText ?? "";
    for (const slopPattern of ["skill(s)", "server(s)", "tool(s)", "0 skills"]) {
      if (bodyText.includes(slopPattern)) {
        violations.push(`slop copy "${slopPattern}" present on page`);
      }
    }
    return violations;
  });
  expect(iconAndCopyViolations, `Icon/copy synchronization violations on ${surfaceLabel}`).toEqual([]);
}

test("automated Hallmark v2.0.0 and Impeccable v4.8.0 gates pass across all workbench surfaces in dark and light themes", async ({ page }) => {
  // Zero-Layout-Transition Gate on stylesheet rules
  const layoutTransitionViolations = await page.evaluate(() => {
    const violations: string[] = [];
    const forbidden = /(?<![\w-])(width|height|left|top|right|bottom|margin|padding|grid-template-rows|all)(?![\w-])/i;
    for (const sheet of Array.from(document.styleSheets)) {
      let rules: CSSRuleList;
      try {
        rules = sheet.cssRules;
      } catch {
        continue;
      }
      for (const rule of Array.from(rules)) {
        if (rule instanceof CSSStyleRule) {
          const tp = rule.style.transitionProperty;
          const tr = rule.style.transition;
          if ((tp && forbidden.test(tp)) || (tr && forbidden.test(tr))) {
            violations.push(`${rule.selectorText}: transition=${tr || tp}`);
          }
        }
      }
    }
    return violations;
  });
  expect(layoutTransitionViolations).toEqual([]);

  // Verify .provider-context-disclosure rule has 1px symmetric border
  const providerDisclosureBorderOk = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "provider-context-disclosure";
    document.body.appendChild(probe);
    const style = window.getComputedStyle(probe);
    const result = style.borderLeftWidth === "1px" && style.borderTopWidth === "1px";
    probe.remove();
    return result;
  });
  expect(providerDisclosureBorderOk).toBe(true);

  // Open project so Sidebar, Thread/Home, and Work Panel resize handles and tabs are mounted
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();
  await page.keyboard.press("Tab");

  // Check Focus-Visible ring (outlineStyle !== "none") on resize handles, composer, brand button, buttons, and tabs
  for (const selector of [
    ".sidebar-resize-handle",
    ".work-panel-resize",
    ".composer-input",
    ".sidebar-brand .brand",
    ".sidebar-toggle",
    "#inspector-tab-files",
  ]) {
    const loc = page.locator(selector).first();
    await loc.focus();
    await expect(loc).toBeFocused();
    const outlineStyle = await loc.evaluate((el) => window.getComputedStyle(el).outlineStyle);
    expect(outlineStyle, `Focus-visible outlineStyle on ${selector}`).not.toBe("none");
  }

  for (const theme of ["dark", "light"] as const) {
    await page.evaluate((appearance) => {
      document.documentElement.dataset.theme = appearance;
    }, theme);

    // Home / Chat surface — verify sidebar brand is quiet (transparent) at rest and matches sidebar icon button primitive on hover
    await page.getByRole("button", { name: "Home" }).click();
    await page.mouse.move(0, 0);
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect(page.locator(".sidebar-brand .brand")).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    const brandRestStyle = await page.locator(".sidebar-brand .brand").evaluate((el) => {
      const s = window.getComputedStyle(el);
      return { bg: s.backgroundColor, radius: s.borderRadius, height: s.height };
    });
    expect(brandRestStyle.bg, `Sidebar brand must have no box at rest (${theme})`).toBe("rgba(0, 0, 0, 0)");
    expect(brandRestStyle.radius).toBe("8px");
    expect(brandRestStyle.height).toBe("32px");

    const expectedHoverBg = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.backgroundColor = "var(--hover)";
      document.body.appendChild(probe);
      const color = window.getComputedStyle(probe).backgroundColor;
      probe.remove();
      return color;
    });
    expect(expectedHoverBg).not.toBe("rgba(0, 0, 0, 0)");
    await page.locator(".sidebar-brand .brand").hover();
    await expect(page.locator(".sidebar-brand .brand")).toHaveCSS("background-color", expectedHoverBg);
    await page.locator(".sidebar-header-action").first().hover();
    await expect(page.locator(".sidebar-header-action").first()).toHaveCSS("background-color", expectedHoverBg);
    await page.mouse.move(0, 0);
    await expect(page.locator(".sidebar-header-action").first()).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");

    await assertHallmarkAndImpeccableGatesOnCurrentSurface(page, `chat-home-${theme}`);

    // Command Palette (Ctrl+Shift+P)
    await page.keyboard.press("Control+Shift+P");
    await expect(page.getByRole("dialog", { name: "Command Palette" })).toBeVisible();
    await assertHallmarkAndImpeccableGatesOnCurrentSurface(page, `command-palette-${theme}`);
    await page.keyboard.press("Escape");

    // Search Modal (Ctrl+F)
    await page.keyboard.press("Control+f");
    await expect(page.getByRole("dialog", { name: "Search" })).toBeVisible();
    const searchSettingsCheck = await page.evaluate(() => {
      const results = document.querySelector<HTMLElement>("#aegis-search-results");
      const text = results?.textContent ?? "";
      const labels = Array.from(results?.querySelectorAll<HTMLElement>(".search-group-label") ?? []);
      const settingsGroupLabel = labels.find((el) => el.textContent?.trim() === "Settings");
      const settingOptions: Array<{ title: string; icon: string | null }> = [];
      let sibling = settingsGroupLabel?.nextElementSibling ?? null;
      while (sibling && !sibling.classList.contains("search-group-label")) {
        if (sibling.matches("button.search-item")) {
          settingOptions.push({
            title: sibling.querySelector(".search-item-title")?.textContent?.trim() ?? "",
            icon: sibling.querySelector("[data-capability-icon]")?.getAttribute("data-capability-icon") ?? null,
          });
        }
        sibling = sibling.nextElementSibling;
      }
      return {
        hasStaleNetworkCopy: text.includes("General appearance and network"),
        count: settingOptions.length,
        uniqueIcons: new Set(settingOptions.map((item) => item.icon)).size,
        titles: settingOptions.map((item) => item.title),
      };
    });
    expect(searchSettingsCheck.hasStaleNetworkCopy).toBe(false);
    expect(searchSettingsCheck.count).toBe(13);
    expect(searchSettingsCheck.uniqueIcons).toBe(13);
    await assertHallmarkAndImpeccableGatesOnCurrentSurface(page, `search-modal-${theme}`);
    await page.keyboard.press("Escape");

    // Inspector tabs (Files, Changes, Focus Graph, Activity, Context)
    for (const tabName of ["Files", "Changes", "Focus Graph", "Activity", "Context"] as const) {
      await page.getByRole("tab", { name: tabName }).click();
      if (tabName === "Focus Graph") {
        await expect(page.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
        // Click a file node with memories in Focus Graph so candidate memory metadata is rendered
        const fileButton = page.locator(".graph-accessible-list button").first();
        if (await fileButton.isVisible()) {
          await fileButton.click();
        }
        const graphInspectorCheck = await page.evaluate(() => {
          const inspector = document.querySelector<HTMLElement>(".graph-inspector");
          if (!inspector) return null;
          const s = window.getComputedStyle(inspector);
          return { bg: s.backgroundColor, borderWidth: s.borderTopWidth, borderStyle: s.borderTopStyle };
        });
        expect(graphInspectorCheck).not.toBeNull();
        expect(graphInspectorCheck?.bg).not.toBe("rgb(21, 29, 23)");
        expect(graphInspectorCheck?.borderWidth).toBe("1px");
        expect(graphInspectorCheck?.borderStyle).toBe("solid");
      }
      await assertHallmarkAndImpeccableGatesOnCurrentSurface(page, `inspector-${tabName}-${theme}`);
    }

    // Utilities destination
    await page.getByRole("button", { name: "Utilities", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Utilities" })).toBeVisible();
    await assertHallmarkAndImpeccableGatesOnCurrentSurface(page, `utilities-${theme}`);

    // Settings sections (all 13 sections + 0px bottom-band height invariant + bordered 12px card primitive)
    await page.getByRole("button", { name: "Settings" }).click();
    for (const section of [
      "General",
      "AI",
      "Shortcuts",
      "Instructions",
      "Models",
      "Skills",
      "MCP",
      "Plugins",
      "Subagents",
      "Import",
      "Projects",
      "Remote Hosts",
      "Info",
    ] as const) {
      await page.getByRole("button", { name: section, exact: true }).click();
      const bounds = await page.evaluate(() => {
        const nav = document.querySelector<HTMLElement>(".settings-nav")?.getBoundingClientRect();
        const content = document.querySelector<HTMLElement>(".settings-content")?.getBoundingClientRect();
        const cardSelectors = ".aegis-settings-card, .shortcut-settings-card, .instruction-settings-card, .capability-settings-group, .import-settings-card, .model-default-row, .model-provider-row, .model-vendor-empty, .model-catalog-row, .mcp-registry-search";
        const unborderedCards: string[] = [];
        for (const card of Array.from(document.querySelectorAll<HTMLElement>(cardSelectors))) {
          if (card.getClientRects().length === 0) continue;
          const cs = window.getComputedStyle(card);
          if (cs.borderTopWidth !== "1px" || cs.borderTopStyle !== "solid" || cs.borderRadius !== "12px") {
            unborderedCards.push(`${card.className} (border=${cs.borderTopWidth} ${cs.borderTopStyle}, radius=${cs.borderRadius})`);
          }
        }
        const emptyRow = document.querySelector<HTMLElement>(".capability-settings-group > .capability-settings-empty");
        const emptyStyle = emptyRow ? window.getComputedStyle(emptyRow) : null;
        return {
          innerHeight: window.innerHeight,
          navBottom: Math.round(nav?.bottom ?? 0),
          contentBottom: Math.round(content?.bottom ?? 0),
          unborderedCards,
          emptyCheck: emptyStyle ? { borderTopWidth: emptyStyle.borderTopWidth, paddingLeft: emptyStyle.paddingLeft } : null,
        };
      });
      expect(bounds.navBottom, `Settings nav bottom on ${section} (${theme})`).toBe(bounds.innerHeight);
      expect(bounds.contentBottom, `Settings content bottom on ${section} (${theme})`).toBe(bounds.innerHeight);
      expect(bounds.unborderedCards, `Unbordered or wrong-radius Settings cards on ${section} (${theme})`).toEqual([]);
      if (bounds.emptyCheck) {
        expect(bounds.emptyCheck.borderTopWidth, `Empty state top divider on ${section} (${theme})`).toBe("1px");
        expect(bounds.emptyCheck.paddingLeft, `Empty state horizontal padding on ${section} (${theme})`).toBe("18px");
      }
      await assertHallmarkAndImpeccableGatesOnCurrentSurface(page, `settings-${section}-${theme}`);
    }
    await page.getByRole("button", { name: "Back to app" }).click();
  }
});

test("Scenario 1: Search overlay (Ctrl+F) full keyboard lifecycle, session switch on Enter, zero-match guard, and focus trap/restore", async ({ page }) => {
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("Alpha first turn message");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText(/Preview response: I received “Alpha first turn message”/)).toBeVisible();

  // Return to Home ("New chat") so we can verify opening an existing session via Ctrl+F + Enter
  await page.getByRole("button", { name: "New chat" }).first().click();
  await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeVisible();

  await composer.focus();
  await expect(composer).toBeFocused();
  await page.keyboard.press("Control+f");

  const searchDialog = page.getByRole("dialog", { name: "Search" });
  await expect(searchDialog).toBeVisible();
  await expect(searchDialog).toHaveClass(/search-card/);

  const searchInput = searchDialog.getByRole("combobox", { name: "Search" });
  await expect(searchInput).toBeFocused();
  await searchInput.fill("Alpha first turn");

  // Index 1 (first matching session) is automatically highlighted when query is non-empty
  await expect(searchInput).toHaveAttribute("aria-activedescendant", "search-result-option-1");
  await expect(page.locator("#search-result-option-1")).toHaveClass(/is-active/);

  // Navigate with ArrowDown / ArrowUp while focus remains in .search-input
  await page.keyboard.press("ArrowDown");
  await expect(searchInput).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(searchInput).toHaveAttribute("aria-activedescendant", "search-result-option-1");

  // Verify Tab / Shift+Tab stays trapped inside .search-card
  await page.keyboard.press("Shift+Tab");
  const focusedInsideAfterShiftTab = await page.evaluate(() =>
    Boolean(document.activeElement?.closest(".search-card")),
  );
  expect(focusedInsideAfterShiftTab).toBe(true);

  // Focus search input and press Enter to open the matching conversation from Home
  await searchInput.focus();
  await page.keyboard.press("Enter");
  await expect(searchDialog).toHaveCount(0);
  await expect(page.getByText(/Preview response: I received “Alpha first turn message”/)).toBeVisible();

  // Re-open Ctrl+F, type a zero-match query, and verify Enter does not start a new chat
  await composer.focus();
  await page.keyboard.press("Control+f");
  await expect(searchDialog).toBeVisible();
  await searchInput.fill("zzzz-nonexistent-query-9999");
  await expect(searchDialog.getByText("No matching results")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(searchDialog).toBeVisible();

  // Press Escape and verify focus returns to the exact opener element (.composer-input)
  await page.keyboard.press("Escape");
  await expect(searchDialog).toHaveCount(0);
  await expect(composer).toBeFocused();
});

test("Scenario 2: Command Palette (Ctrl+Shift+P) full keyboard lifecycle, command execution, and focus restoration without dropping to document.body", async ({ page }) => {
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();
  await page.mouse.move(10, 10);
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.focus();

  await page.keyboard.press("Control+Shift+P");
  const paletteDialog = page.getByRole("dialog", { name: "Command Palette" });
  await expect(paletteDialog).toBeVisible();
  const paletteInput = paletteDialog.getByRole("combobox", { name: "Search commands" });
  await expect(paletteInput).toBeFocused();
  await expect(paletteInput).toHaveAttribute("aria-activedescendant", "command-palette-option-quick-open");

  await page.keyboard.press("ArrowDown");
  await expect(paletteInput).toHaveAttribute("aria-activedescendant", "command-palette-option-new-task");
  await page.keyboard.press("ArrowUp");
  await expect(paletteInput).toHaveAttribute("aria-activedescendant", "command-palette-option-quick-open");

  // Execute commands via Command Palette and verify focus never lands on document.body
  for (const query of [
    "Open Tracked Changes",
    "Open Focus Graph",
    "Open Context Cockpit",
    "Toggle Navigator",
    "Toggle Navigator",
    "New Task",
    "Preferences",
  ]) {
    if (!(await paletteDialog.isVisible())) {
      await page.keyboard.press("Control+Shift+P");
      await expect(paletteDialog).toBeVisible();
    }
    await paletteInput.fill(query);
    await page.keyboard.press("Enter");
    await expect(paletteDialog).toHaveCount(0);
    const activeTag = await page.evaluate(() => document.activeElement?.tagName ?? "BODY");
    expect(activeTag, `Active element after running "${query}" must not be BODY`).not.toBe("BODY");
  }
});

test("Scenario 3: rapid double-trigger send deduplication (Enter + click race) creates at most one conversation and one run", async ({ page }) => {
  await page.evaluate(async () => {
    const protocolUrl = new URL("/src/protocol.ts", window.location.origin).href;
    const previewUrl = new URL("/src/preview_backend.ts", window.location.origin).href;
    const protocol = await import(/* @vite-ignore */ protocolUrl);
    const preview = await import(/* @vite-ignore */ previewUrl);
    const createdConversations: Array<Record<string, unknown>> = [];
    const startedCommands: Array<{ command: string; payload: Record<string, unknown> }> = [];
    const restore = protocol.installDesktopTransport(async (command: DesktopCommand, payload: Record<string, unknown>) => {
      if (command === "conversations.create") {
        createdConversations.push({ ...payload });
        await new Promise((resolve) => setTimeout(resolve, 40));
      }
      if (command === "runs.start" || command === "conversations.send") {
        startedCommands.push({ command, payload: { ...payload } });
        await new Promise((resolve) => setTimeout(resolve, 60));
      }
      return preview.previewRequest(command, payload);
    });
    (window as typeof window & {
      __sendDedupeState?: {
        createdConversations: Array<Record<string, unknown>>;
        startedCommands: Array<{ command: string; payload: Record<string, unknown> }>;
        restore: () => void;
      };
    }).__sendDedupeState = { createdConversations, startedCommands, restore };
  });

  try {
    const composer = page.getByRole("textbox", { name: "Message" });
    await composer.fill("Deduplicate rapid double trigger prompt");
    await composer.focus();

    // Fire Enter keydown and Send button click in the same tick from Home
    await page.evaluate(() => {
      const composerEl = document.querySelector<HTMLElement>(".composer-input");
      const sendBtn = document.querySelector<HTMLButtonElement>('button[aria-label="Send message"]');
      composerEl?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
      sendBtn?.click();
    });

    await expect(page.getByText(/Preview response: I received “Deduplicate rapid double trigger prompt”/)).toBeVisible();
    const counts = await page.evaluate(() => {
      const state = (window as typeof window & {
        __sendDedupeState?: {
          createdConversations: Array<Record<string, unknown>>;
          startedCommands: Array<{ command: string; payload: Record<string, unknown> }>;
        };
      }).__sendDedupeState;
      return {
        createdCount: state?.createdConversations.length ?? 0,
        sendCount: state?.startedCommands.length ?? 0,
      };
    });
    expect(counts.createdCount).toBe(1);
    expect(counts.sendCount).toBe(1);
  } finally {
    await page.evaluate(() => {
      const state = (window as typeof window & { __sendDedupeState?: { restore: () => void } }).__sendDedupeState;
      state?.restore();
      delete (window as typeof window & { __sendDedupeState?: unknown }).__sendDedupeState;
    });
  }
});

test("Scenario 4: UI scale (Tall, Grande, Venti, Trenta) x viewport (640x480, 1280x800, 1920x1080) x Project Switcher containment", async ({ page }) => {
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();

  for (const scale of ["tall", "grande", "venti", "trenta"] as const) {
    await page.evaluate((fontSize) => {
      document.documentElement.dataset.fontSize = fontSize;
    }, scale);

    for (const viewport of [
      { width: 640, height: 480 },
      { width: 1280, height: 800 },
      { width: 1920, height: 1080 },
    ] as const) {
      await page.setViewportSize(viewport);

      const layoutMetrics = await page.evaluate(() => {
        const rootRect = document.getElementById("root")?.getBoundingClientRect();
        return {
          rootHeight: rootRect?.height ?? 0,
          innerHeight: window.innerHeight,
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        };
      });
      expect(
        layoutMetrics.scrollWidth <= layoutMetrics.clientWidth,
        `No horizontal overflow at scale=${scale} viewport=${viewport.width}x${viewport.height}`,
      ).toBe(true);
      expect(
        layoutMetrics.rootHeight <= layoutMetrics.innerHeight + 2,
        `#root height (${layoutMetrics.rootHeight}) <= innerHeight (${layoutMetrics.innerHeight}) at scale=${scale}`,
      ).toBe(true);

      // Test Project Switcher from Home composer under-bar trigger
      const composerTrigger = page.locator(".composer-under-action").first();
      await composerTrigger.click();
      const projectMenu = page.getByRole("menu", { name: "Switch project" });
      await expect(projectMenu).toBeVisible();
      const bounds = await projectMenu.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(11);
      expect(bounds!.y).toBeGreaterThanOrEqual(11);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width - 11);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height - 11);
      await page.keyboard.press("Escape");
      await expect(projectMenu).toHaveCount(0);
    }
  }

  // Also verify Project Switcher from expanded Sidebar and collapsed Sidebar at 1280x800
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.evaluate(() => {
    document.documentElement.dataset.fontSize = "grande";
  });
  const expandBtn = page.getByRole("button", { name: /Expand sidebar|Open navigation/ }).first();
  if (await expandBtn.isVisible()) {
    await expandBtn.click();
  }
  const sidebarSwitchBtn = page.getByRole("button", { name: "Switch project", exact: true });
  await sidebarSwitchBtn.click();
  const sidebarMenu = page.getByRole("menu", { name: "Switch project" });
  await expect(sidebarMenu).toBeVisible();
  const sidebarMenuBox = await sidebarMenu.boundingBox();
  expect(sidebarMenuBox).not.toBeNull();
  expect(sidebarMenuBox!.x).toBeGreaterThanOrEqual(12);
  expect(sidebarMenuBox!.x + sidebarMenuBox!.width).toBeLessThanOrEqual(1280 - 12);
  await page.keyboard.press("Escape");
  await expect(sidebarMenu).toHaveCount(0);
});

test("Scenario 5: resize drag cancellation (Escape / blur) and outside-click dismissal for project switcher, session menu, and home actions menu", async ({ page }) => {
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();
  const sidebarResize = page.locator(".sidebar-resize-handle");
  const workPanelResize = page.locator(".work-panel-resize");
  await expect(sidebarResize).toBeVisible();
  await expect(workPanelResize).toBeVisible();

  // Start sidebar resize drag, press Escape, then move mouse further
  const sidebarBox = await sidebarResize.boundingBox();
  expect(sidebarBox).not.toBeNull();
  const initialSidebarWidth = await page.locator(".sidebar").evaluate((el) => el.getBoundingClientRect().width);
  await page.mouse.move(sidebarBox!.x + 2, sidebarBox!.y + 40);
  await page.mouse.down();
  await page.mouse.move(sidebarBox!.x + 40, sidebarBox!.y + 40);
  await page.keyboard.press("Escape");
  const widthRightAfterEscape = await page.locator(".sidebar").evaluate((el) => el.getBoundingClientRect().width);
  expect(widthRightAfterEscape).toBe(initialSidebarWidth);
  await page.mouse.move(sidebarBox!.x + 110, sidebarBox!.y + 40);
  const widthAfterEscapeMove = await page.locator(".sidebar").evaluate((el) => el.getBoundingClientRect().width);
  expect(widthAfterEscapeMove).toBe(widthRightAfterEscape);
  expect(await page.evaluate(() => document.body.style.userSelect)).toBe("");
  await page.mouse.up();

  // Start work-panel resize drag, trigger window blur, then move mouse further
  const panelBox = await workPanelResize.boundingBox();
  expect(panelBox).not.toBeNull();
  const initialPanelWidth = await page.locator(".work-panel").evaluate((el) => el.getBoundingClientRect().width);
  await page.mouse.move(panelBox!.x + 2, panelBox!.y + 40);
  await page.mouse.down();
  await page.mouse.move(panelBox!.x - 30, panelBox!.y + 40);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  const panelWidthRightAfterBlur = await page.locator(".work-panel").evaluate((el) => el.getBoundingClientRect().width);
  expect(panelWidthRightAfterBlur).toBe(initialPanelWidth);
  await page.mouse.move(panelBox!.x - 100, panelBox!.y + 40);
  const panelWidthAfterBlurMove = await page.locator(".work-panel").evaluate((el) => el.getBoundingClientRect().width);
  expect(panelWidthAfterBlurMove).toBe(panelWidthRightAfterBlur);
  expect(await page.evaluate(() => document.body.style.userSelect)).toBe("");
  await page.mouse.up();

  // Open .home-actions-menu and click outside to dismiss
  const workspaceActionsBtn = page.getByRole("button", { name: "Workspace actions" });
  await workspaceActionsBtn.click();
  const homeActionsMenu = page.getByRole("menu", { name: "Workspace actions" });
  await expect(homeActionsMenu).toBeVisible();
  await page.mouse.click(500, 120);
  await expect(homeActionsMenu).toHaveCount(0);

  // Create a session so .session-row-action exists in the sidebar, open .session-row-menu, and click outside to dismiss
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("Session menu outside click test");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText(/Preview response: I received “Session menu outside click test”/)).toBeVisible();

  const sessionMoreBtn = page.locator(".session-row-action").first();
  await sessionMoreBtn.click();
  const sessionMenu = page.locator(".session-row-menu");
  await expect(sessionMenu).toBeVisible();
  await page.mouse.click(600, 200);
  await expect(sessionMenu).toHaveCount(0);

  // Open project switcher popover, resize viewport, and click outside to dismiss
  const projectTrigger = page.getByRole("button", { name: "Switch project", exact: true });
  await projectTrigger.click();
  const projectMenu = page.getByRole("menu", { name: "Switch project" });
  await expect(projectMenu).toBeVisible();
  await page.setViewportSize({ width: 1180, height: 760 });
  const menuBounds = await projectMenu.boundingBox();
  expect(menuBounds).not.toBeNull();
  expect(menuBounds!.x).toBeGreaterThanOrEqual(12);
  expect(menuBounds!.x + menuBounds!.width).toBeLessThanOrEqual(1180);
  await page.mouse.click(600, 200);
  await expect(projectMenu).toHaveCount(0);
});

test("Scenario 6: mid-sentence @file and /slash autocomplete, sanitized rich-text paste with caret preservation, and queued prompt reorder/edit", async ({ page }) => {
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();
  await expect(page.getByRole("tabpanel", { name: "Files" }).getByText("Project files", { exact: true })).toBeVisible();

  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.click();

  // Type sentence, move caret to middle (after "Check ("), trigger @file after '('
  await composer.fill("Check () before shipping");
  await composer.evaluate((element) => {
    const textNode = element.firstChild;
    if (!textNode) return;
    const selection = window.getSelection();
    const range = document.createRange();
    range.setStart(textNode, "Check (".length);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
  });
  await page.keyboard.type("@App");

  const fileListbox = page.getByRole("listbox", { name: "Files" });
  await expect(fileListbox).toBeVisible();
  await expect(fileListbox.getByRole("option", { name: /@desktop\/src\/App\.tsx/ })).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");

  await expect(composer).toHaveText("Check (@desktop/src/App.tsx ) before shipping");

  // Dispatch synthetic paste event in the middle of the text with malicious HTML + plain text
  const pasteCheck = await composer.evaluate((element) => {
    const data = new DataTransfer();
    data.setData("text/html", "<img src=x onerror=alert(1)><b>now</b>");
    data.setData("text/plain", "now ");
    const pasteEvent = new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      clipboardData: data,
    });
    element.dispatchEvent(pasteEvent);
    const selection = window.getSelection();
    let caretOffset = -1;
    if (selection && selection.rangeCount > 0) {
      const range = selection.getRangeAt(0);
      const pre = range.cloneRange();
      pre.selectNodeContents(element);
      pre.setEnd(range.startContainer, range.startOffset);
      caretOffset = pre.toString().length;
    }
    return {
      childElementCount: element.childElementCount,
      innerHTML: element.innerHTML,
      textContent: element.textContent,
      caretOffset,
    };
  });
  expect(pasteCheck.childElementCount).toBe(0);
  expect(pasteCheck.innerHTML).not.toContain("<img");
  expect(pasteCheck.innerHTML).not.toContain("<b>");
  expect(pasteCheck.textContent).toBe("Check (@desktop/src/App.tsx now ) before shipping");
  expect(pasteCheck.caretOffset).toBe("Check (@desktop/src/App.tsx now ".length);

  // Test /slash autocomplete and Escape dismissal
  await composer.fill("/foc");
  const commandListbox = page.getByRole("listbox", { name: "Commands" });
  await expect(commandListbox).toBeVisible();
  await expect(commandListbox.getByRole("option", { name: /\/focus/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(commandListbox).toHaveCount(0);

  // Test queued prompt reorder and edit while a run is active
  const pageTime = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(pageTime + 1_000);
  await composer.fill("Initial running task");
  await page.getByRole("button", { name: "Send message" }).click();
  const stopButton = page.getByRole("button", { name: "Stop active task" });
  await expect(stopButton).toBeVisible();

  await composer.fill("First queued follow-up");
  await page.getByRole("button", { name: "Queue message" }).click();
  await composer.fill("Second queued follow-up");
  await page.getByRole("button", { name: "Queue message" }).click();

  const queueRegion = page.getByLabel("Queued task messages");
  await expect(queueRegion).toBeVisible();
  const queueItems = queueRegion.locator(".task-queue-item");
  await expect(queueItems).toHaveCount(2);

  // Move second item up
  await queueItems.nth(1).getByRole("button", { name: "Move queued message up" }).click();
  await expect(queueItems.nth(0)).toContainText("Second queued follow-up");

  // Type draft text into composer, then click Edit on the first queued item
  await composer.fill("unsent draft text");
  await queueItems.nth(0).getByRole("button", { name: "Edit queued message" }).click();
  await expect(queueItems).toHaveCount(1);
  await expect(composer).toHaveText("Second queued follow-up\n\nunsent draft text");
  await expect(composer).toBeFocused();

  await stopButton.click();
  await page.clock.runFor(2_000);
});

test("Scenario 7: Focus Graph canvas keyboard shortcuts (+, -, 0, f, Arrow pan) and BiDi spoofing guard in approval cards", async ({ page }) => {
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();
  const workspace = page.locator('[data-workbench-pane="inspector"][aria-label="Workspace panel"]');
  await workspace.getByRole("tab", { name: "Focus Graph" }).click();
  const graphPanel = workspace.getByRole("tabpanel", { name: "Focus Graph" });
  await expect(graphPanel.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });

  const graphCanvas = graphPanel.getByRole("region", { name: "Project graph canvas" });
  const firstNode = graphCanvas.locator(".graph-node").first();
  await firstNode.focus();
  await expect(firstNode).toBeFocused();

  await page.keyboard.press("+");
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("125%");

  await page.keyboard.press("-");
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("100%");

  await page.keyboard.press("+");
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("125%");
  await page.keyboard.press("0");
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("100%");

  await page.keyboard.press("+");
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("125%");
  await page.keyboard.press("f");
  await expect(graphPanel.getByLabel("Graph zoom")).toHaveText("100%");

  const beforeTop = await graphCanvas.evaluate((el) => el.scrollTop);
  await page.keyboard.press("ArrowDown");
  await expect.poll(() => graphCanvas.evaluate((el) => el.scrollTop)).toBeGreaterThan(beforeTop);
  await expect(firstNode).toBeFocused();

  // Verify BiDi control character neutralization in tool approval cards
  await page.evaluate(async () => {
    const protocolUrl = new URL("/src/protocol.ts", window.location.origin).href;
    const previewUrl = new URL("/src/preview_backend.ts", window.location.origin).href;
    const protocol = await import(/* @vite-ignore */ protocolUrl);
    const preview = await import(/* @vite-ignore */ previewUrl);
    const restore = protocol.installDesktopTransport(async (command: DesktopCommand, payload: Record<string, unknown>) => {
      const response = await preview.previewRequest(command, payload);
      if (command === "conversations.inspect" && response && typeof response === "object") {
        const envelope = response as { inspection?: Record<string, unknown> };
        if (envelope.inspection && typeof envelope.inspection === "object") {
          return {
            ...envelope,
            inspection: {
              ...envelope.inspection,
              approvals: [
                {
                  approval_id: "approval-bidi-e2e-1",
                  tool_name: "filesystem\u202E_write",
                  status: "PENDING",
                  risk: "MUTATING",
                  argument_keys: ["path"],
                  argument_preview: [
                    { key: "path\u202E", value: "report_\u202Etxt.exe\u202C" },
                  ],
                  arguments: "REDACTED",
                  result: "REDACTED",
                  can_resolve: true,
                  can_reconcile: false,
                  effect_class: "WORKSPACE_MUTATION",
                },
              ],
            },
          };
        }
      }
      return response;
    });
    (window as typeof window & { __bidiRestore?: () => void }).__bidiRestore = restore;
  });

  try {
    const composer = page.getByRole("textbox", { name: "Message" });
    await composer.fill("Trigger conversation with pending approval");
    await page.getByRole("button", { name: "Send message" }).click();

    const approvalCard = page.getByRole("article", { name: "Approval required" });
    await expect(approvalCard).toBeVisible();
    await expect(approvalCard).toContainText("filesystem\\u202E_write");
    await expect(approvalCard).toContainText("report_\\u202Etxt.exe\\u202C");

    const rawBidiPresent = await approvalCard.evaluate((el) =>
      /[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/.test(el.textContent ?? ""),
    );
    expect(rawBidiPresent).toBe(false);
  } finally {
    await page.evaluate(() => {
      const state = window as typeof window & { __bidiRestore?: () => void };
      state.__bidiRestore?.();
      delete state.__bidiRestore;
    });
  }
});

test("Scenario 8: viewport x theme x reduced-motion matrix (640x480, 1280x800, 1920x1080 x dark, light) with WCAG 2.2 AA Axe verification", async ({ page }) => {
  test.setTimeout(240_000);
  await page.emulateMedia({ reducedMotion: "reduce" });

  const verifyCurrentSurfaceAcrossMatrix = async (surfaceLabel: string, runAxe: boolean) => {
    for (const theme of ["dark", "light"] as const) {
      await page.evaluate((appearance) => {
        document.documentElement.dataset.theme = appearance;
      }, theme);

      for (const viewport of [
        { width: 640, height: 480 },
        { width: 1280, height: 800 },
        { width: 1920, height: 1080 },
      ] as const) {
        await page.setViewportSize(viewport);
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
          `${surfaceLabel} overflow at ${viewport.width}x${viewport.height} (${theme})`,
        ).toBe(true);

        const undersizedButtons = await page.evaluate(() => {
          const bad: string[] = [];
          const selectors = ".sidebar-toolbar-button, .ct-icon-btn, .work-panel-tab-close, .session-row-action, .project-row-action";
          for (const btn of Array.from(document.querySelectorAll<HTMLElement>(selectors))) {
            const rect = btn.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) continue;
            if (rect.width < 27.5 || rect.height < 27.5) {
              bad.push(`${btn.className} (${rect.width.toFixed(1)}x${rect.height.toFixed(1)})`);
            }
          }
          return bad;
        });
        expect(undersizedButtons, `Undersized hit targets on ${surfaceLabel} at ${viewport.width}x${viewport.height} (${theme})`).toEqual([]);

        if (runAxe && viewport.width === 1280) {
          const axe = await new AxeBuilder({ page })
            .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
            .analyze();
          const severe = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
          expect(severe, `Axe violations on ${surfaceLabel} in ${theme} at 1280x800`).toEqual([]);
        }
      }
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    const expandNavBtn = page.getByRole("button", { name: /Expand sidebar|Open navigation/ }).first();
    if (await expandNavBtn.isVisible()) {
      await expandNavBtn.click();
    }
  };

  // 1. Chat + Inspector surface
  await page.getByRole("button", { name: /Browse project|Open a project/ }).click();

  // Verify all 5 work-panel tab buttons stay on a single line (no 2-line wrapping on "Focus Graph")
  const tabMetrics = await page.evaluate(() => {
    const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>(".work-panel-direct-tabs .work-panel-tab-button"));
    return tabs.map((tab) => {
      const rect = tab.getBoundingClientRect();
      return { text: tab.textContent?.trim() ?? "", top: Math.round(rect.top), height: Math.round(rect.height) };
    });
  });
  expect(tabMetrics.length).toBe(5);
  expect(new Set(tabMetrics.map((t) => t.top)).size).toBe(1);
  expect(tabMetrics.every((t) => t.height <= 34)).toBe(true);

  // Select a file in Files tab and verify .source-viewer does not blow out .file-panel width
  const firstFileRow = page.locator(".file-panel button.file-tree-row").first();
  if (await firstFileRow.isVisible()) {
    await firstFileRow.click();
    const fileContainment = await page.evaluate(() => {
      const filePanel = document.querySelector<HTMLElement>(".file-panel");
      const workPanel = document.querySelector<HTMLElement>(".work-panel");
      const openBtn = Array.from(document.querySelectorAll<HTMLButtonElement>(".source-viewer-actions button")).find((b) =>
        b.textContent?.includes("Open externally"),
      );
      const panelRect = workPanel?.getBoundingClientRect();
      const btnRect = openBtn?.getBoundingClientRect();
      return {
        scrollWidth: filePanel?.scrollWidth ?? 0,
        clientWidth: filePanel?.clientWidth ?? 0,
        buttonRightWithinPanel: panelRect && btnRect ? btnRect.right <= panelRect.right + 1 : true,
      };
    });
    expect(fileContainment.scrollWidth).toBeLessThanOrEqual(fileContainment.clientWidth + 1);
    expect(fileContainment.buttonRightWithinPanel).toBe(true);
  }

  await verifyCurrentSurfaceAcrossMatrix("chat-inspector", true);

  // 2. Utilities surface
  await page.getByRole("button", { name: "Utilities", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Utilities" })).toBeVisible();
  await verifyCurrentSurfaceAcrossMatrix("utilities", false);

  // 3. Settings surfaces (Models, Skills, MCP, Projects, Subagents, Remote Hosts, Info)
  await page.getByRole("button", { name: "Settings" }).click();
  const settingsNavIcons = await page.evaluate(() => {
    const navButtons = Array.from(document.querySelectorAll<HTMLButtonElement>(".settings-nav .settings-nav-block button"));
    return navButtons.map((btn) => ({
      label: btn.textContent?.trim() ?? "",
      icon: btn.querySelector("[data-capability-icon]")?.getAttribute("data-capability-icon") ?? null,
      archetype: btn.querySelector("[data-animate-ui-icon]")?.getAttribute("data-animate-ui-icon") ?? null,
    }));
  });
  expect(settingsNavIcons.length).toBe(13);
  const uniqueIconNames = new Set(settingsNavIcons.map((item) => item.icon));
  expect(uniqueIconNames.size, `Expected 13 unique Settings icons, got: ${JSON.stringify(settingsNavIcons)}`).toBe(13);
  expect(settingsNavIcons.every((item) => Boolean(item.archetype))).toBe(true);

  for (const section of ["Models", "Skills", "MCP", "Projects", "Subagents", "Remote Hosts", "Info"] as const) {
    await page.getByRole("button", { name: section, exact: true }).click();
    if (section === "Subagents") {
      const subagentsCheck = await page.evaluate(() => {
        const toggle = document.querySelector(".settings-toggle");
        const chip = document.querySelector<HTMLElement>(".extension-scope-chip");
        const meta = document.querySelector<HTMLElement>(".meta-inline-list");
        return {
          toggleOutsideValueBadge: Boolean(toggle && !toggle.closest(".aegis-settings-value")),
          chipAlignItems: chip ? window.getComputedStyle(chip).alignItems : null,
          metaDisplay: meta ? window.getComputedStyle(meta).display : null,
        };
      });
      expect(subagentsCheck.toggleOutsideValueBadge).toBe(true);
      expect(subagentsCheck.chipAlignItems).toBe("center");
      expect(["flex", "inline-flex"]).toContain(subagentsCheck.metaDisplay);
    }
    if (section === "Info" || section === "Remote Hosts") {
      const valueStyle = await page.evaluate(() => {
        const val = document.querySelector<HTMLElement>(".aegis-settings-value");
        if (!val) return null;
        const style = window.getComputedStyle(val);
        return { display: style.display, alignItems: style.alignItems };
      });
      expect(valueStyle).not.toBeNull();
      expect(["flex", "inline-flex"]).toContain(valueStyle?.display);
      expect(valueStyle?.alignItems).toBe("center");
    }
    if (section === "Projects") {
      const addProjectBtn = page.getByRole("button", { name: "Add project" });
      await addProjectBtn.click();
      const switcherMenu = page.getByRole("menu", { name: "Switch project" });
      await expect(switcherMenu).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(switcherMenu).toHaveCount(0);
    }
    await verifyCurrentSurfaceAcrossMatrix(`settings-${section}`, section === "Models");
  }

  const auditOutDir = (globalThis as unknown as { process?: { env?: Record<string, string | undefined> } }).process?.env?.AUDIT_OUT_DIR;
  if (auditOutDir) {
    await page.evaluate(() => {
      localStorage.clear();
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeVisible();

    let capturedCount = 0;
    const snap = async (slug: string, theme: "dark" | "light", width: number, height: number) => {
      await page.screenshot({
        path: `${auditOutDir}/${slug}-${theme}-${width}x${height}.png`,
        fullPage: false,
        animations: "disabled",
      });
      capturedCount += 1;
    };

    for (const [width, height] of [[1280, 800], [1920, 1080]] as const) {
      await page.setViewportSize({ width, height });
      for (const theme of ["dark", "light"] as const) {
        await page.evaluate((appearance) => {
          document.documentElement.dataset.theme = appearance;
          localStorage.setItem("aegis-theme", appearance);
        }, theme);

        await page.getByRole("button", { name: "New chat" }).first().click();
        const closeWorkPanel = page.locator(".work-panel-close");
        if (await closeWorkPanel.isVisible()) {
          await closeWorkPanel.click();
        }
        await page.mouse.move(0, 0);
        await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

        // 1. home + logo-brand
        await snap("home", theme, width, height);
        await page.locator(".sidebar-brand .brand").screenshot({
          path: `${auditOutDir}/logo-brand-${theme}-${width}x${height}.png`,
          animations: "disabled",
        });
        capturedCount += 1;

        // 2. search
        await page.keyboard.press("Control+f");
        await expect(page.getByRole("dialog", { name: "Search" })).toBeVisible();
        await snap("search", theme, width, height);
        await page.keyboard.press("Escape");

        // 3. command-palette
        await page.keyboard.press("Control+Shift+P");
        await expect(page.getByRole("dialog", { name: "Command Palette" })).toBeVisible();
        await snap("command-palette", theme, width, height);
        await page.keyboard.press("Escape");

        // 4-8. workbench inspector tabs
        const openDetailsBtn = page.getByRole("button", { name: "Open project details" });
        if (await openDetailsBtn.isVisible()) {
          await openDetailsBtn.click();
        }
        for (const [tabLabel, slug] of [
          ["Files", "workbench-files"],
          ["Changes", "workbench-changes"],
          ["Focus Graph", "workbench-focus-graph"],
          ["Activity", "workbench-activity"],
          ["Context", "workbench-context"],
        ] as const) {
          await page.getByRole("tab", { name: tabLabel }).click();
          if (tabLabel === "Focus Graph") {
            await expect(page.getByText("Revision-bound project map")).toBeVisible({ timeout: 20_000 });
            const fileButton = page.locator(".graph-accessible-list button").first();
            if (await fileButton.isVisible()) {
              await fileButton.click();
            }
          }
          await snap(slug, theme, width, height);
        }
        await page.locator(".work-panel-close").click();

        // 9. utilities
        await page.getByRole("button", { name: "Utilities", exact: true }).click();
        await expect(page.getByRole("heading", { name: "Utilities" })).toBeVisible();
        await snap("utilities", theme, width, height);

        // 10-22. 13 settings sections
        await page.getByRole("button", { name: "Settings" }).click();
        for (const [sectionLabel, slug] of [
          ["General", "settings-general"],
          ["AI", "settings-ai"],
          ["Shortcuts", "settings-shortcuts"],
          ["Instructions", "settings-instructions"],
          ["Models", "settings-models"],
          ["Skills", "settings-skills"],
          ["MCP", "settings-mcp"],
          ["Plugins", "settings-plugins"],
          ["Subagents", "settings-subagents"],
          ["Import", "settings-import"],
          ["Projects", "settings-projects"],
          ["Remote Hosts", "settings-remote-hosts"],
          ["Info", "settings-info"],
        ] as const) {
          await page.getByRole("button", { name: sectionLabel, exact: true }).click();
          await snap(slug, theme, width, height);
        }
        await page.getByRole("button", { name: "Back to app" }).click();
      }
    }

    // 92 captured so far; capture the 6 logo-icon-{size}x{size}.png + 2 logo-scale-sheet-{theme}.png = 100 total
    const monolithPath =
      "M32 4L56 16V28C56 44 44 52 32 60C20 52 8 44 8 28V16ZM32 12L16 20V28C16 32 16 36 20 40L32 24L44 40C48 36 48 32 48 28V20ZM32 36L24 48L32 52L40 48Z";
    for (const size of [16, 32, 64, 128, 256, 512] as const) {
      await page.setViewportSize({ width: size, height: size });
      await page.setContent(
        `<!doctype html><html><body style="margin:0;background:#0B0B0D;display:grid;place-items:center;width:${size}px;height:${size}px"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${size}" height="${size}"><path d="${monolithPath}" fill="#FAFAFC"/></svg></body></html>`,
      );
      await page.screenshot({ path: `${auditOutDir}/logo-icon-${size}x${size}.png`, animations: "disabled" });
      capturedCount += 1;
    }
    await page.setViewportSize({ width: 960, height: 220 });
    for (const [theme, bg, fg] of [
      ["dark", "#0B0B0D", "#FAFAFC"],
      ["light", "#F5F6F8", "#0B0B0D"],
    ] as const) {
      await page.setContent(
        `<!doctype html><html><body style="margin:0;background:${bg};color:${fg};font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:space-around;width:960px;height:220px">${[16, 32, 64, 128].map((s) => `<div style="display:grid;justify-items:center;gap:10px"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="${s}" height="${s}"><path d="${monolithPath}" fill="${fg}"/></svg><span style="font-size:12px;opacity:0.75">${s}×${s}</span></div>`).join("")}</body></html>`,
      );
      await page.screenshot({ path: `${auditOutDir}/logo-scale-sheet-${theme}.png`, animations: "disabled" });
      capturedCount += 1;
    }
    expect(capturedCount).toBe(100);
  }
});

