import { expect, test } from "@playwright/test";

test("MCP OAuth Client ID guidance stays understandable across desktop sizes and themes", async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    localStorage.setItem("aegis-workbench-v2", "enabled");
    localStorage.setItem("aegis-theme", "dark");
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeVisible();

  await page.evaluate(async () => {
    const protocolUrl = new URL("/src/protocol.ts", location.origin).href;
    const previewUrl = new URL("/src/preview_backend.ts", location.origin).href;
    const { installDesktopTransport } = await import(protocolUrl);
    const { previewRequest } = await import(previewUrl);
    const server = {
      schema: "aegis-desktop-mcp-server-v1",
      server_id: "oauth-example",
      description: "Remote MCP server used by the browser test",
      transport: "streamable-http",
      source: "workspace",
      approved: true,
      descriptor_hash: "a".repeat(64),
      environment_variables: [],
    };

    installDesktopTransport(async (command, payload) => {
      const result = await previewRequest(command, payload) as Record<string, unknown>;
      if (command === "extensions.discover") {
        return { ...result, mcp_servers: [server] };
      }
      if (command === "extensions.state") {
        const records = Array.isArray(result.records) ? result.records : [];
        return {
          ...result,
          records: [...records, {
            kind: "mcp",
            id: server.server_id,
            descriptor_hash: server.descriptor_hash,
            enabled: false,
            approved: true,
            updated_at_ms: 0,
          }],
        };
      }
      return result;
    });
  });

  await page.getByRole("button", { name: "Utilities", exact: true }).click();
  await page.getByRole("button", { name: "Scan local catalog", exact: true }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "MCP", exact: true }).click();
  await expect(page.getByRole("heading", { name: "MCP", exact: true })).toBeVisible();

  const clientId = page.getByLabel("OAuth Client ID (only if requested)");
  const help = page.getByText(/Leave this blank to let AEGIS reuse a saved Client ID/);
  await expect(clientId).toBeVisible();
  await expect(clientId).toHaveAttribute("placeholder", "Leave blank for automatic registration");
  await expect(help).toBeVisible();
  await expect(page.getByText(/server requires a pre-registered Client ID, enter it here/)).toBeVisible();
  await expect(page.getByText(/uses that ID directly and skips automatic registration/)).toBeVisible();
  expect(await clientId.ariaSnapshot()).toContain("OAuth Client ID (only if requested)");
  await clientId.focus();
  await expect(clientId).toBeFocused();

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
      await help.scrollIntoViewIfNeeded();
      const documentOverflows = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
      );
      expect(documentOverflows, `${theme} MCP settings overflowed at ${viewport.width}x${viewport.height}`).toBe(false);

      if ((theme === "dark" && viewport.width === 1280) || (theme === "light" && viewport.width === 640)) {
        await page.screenshot({
          path: testInfo.outputPath(`mcp-oauth-${theme}-${viewport.width}x${viewport.height}.png`),
          fullPage: false,
          animations: "disabled",
        });
      }
    }
  }
});
