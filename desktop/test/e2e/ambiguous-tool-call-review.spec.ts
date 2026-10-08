import { expect, test } from "@playwright/test";

test("ambiguous outcome review is explicit, keyboard accessible, and responsive", async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/test/e2e/ambiguous-tool-call-review.html?theme=dark", { waitUntil: "domcontentloaded" });

  const card = page.getByRole("article", { name: "External action outcome unclear" });
  const inspectTarget = page.getByRole("button", { name: "I checked the target" });
  await expect(card).toBeVisible();
  await inspectTarget.click();

  const explanation = page.getByText(/Continue only if you checked the external target/);
  await expect(explanation).toBeFocused();
  const semantics = await page.getByRole("main").ariaSnapshot();
  expect(semantics).toContain("Confirm external action outcome");
  expect(semantics).toContain("Record as not performed");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Keep unresolved" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Record as not performed" })).toBeFocused();

  const motionDuration = await page.locator("button").first().evaluate((button) => getComputedStyle(button).transitionDuration);
  const motionSeconds = Number.parseFloat(motionDuration) * (motionDuration.endsWith("ms") ? 0.001 : 1);
  expect(motionSeconds).toBeLessThanOrEqual(0.00001);
  await testInfo.attach("ambiguous-review-dark-wide", {
    body: await card.screenshot(),
    contentType: "image/png",
  });

  await page.getByRole("button", { name: "Keep unresolved" }).click();
  await expect(inspectTarget).toBeVisible();
  await inspectTarget.click();
  await page.getByRole("button", { name: "Record as not performed" }).click();
  await expect(page.getByRole("status")).toContainText("did not independently verify or undo");
  await expect(card).toHaveCount(0);

  await page.setViewportSize({ width: 640, height: 480 });
  await page.goto("/test/e2e/ambiguous-tool-call-review.html?theme=light", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "I checked the target" }).click();
  const bounds = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(bounds.content).toBeLessThanOrEqual(bounds.viewport);
  await testInfo.attach("ambiguous-review-light-compact", {
    body: await page.getByRole("article", { name: "External action outcome unclear" }).screenshot(),
    contentType: "image/png",
  });
});

test("the review stays unavailable while another task is active", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/test/e2e/ambiguous-tool-call-review.html?theme=dark&active=true", {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByText(/A task is still active/)).toBeVisible();
  await expect(page.getByRole("button", { name: "I checked the target" })).toHaveCount(0);
});

test("the browser preview can start a separate local demo conversation", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await expect(composer).toBeVisible();
  await composer.fill("Check the local preview chat");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText(/Preview response: I received/)).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".chat-intro")).toContainText("Replies are simulated");
  await page.setViewportSize({ width: 640, height: 480 });
  const bounds = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(bounds.content).toBeLessThanOrEqual(bounds.viewport);
});
