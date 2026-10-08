import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const runId = `${new Date().toISOString().replace(/[:.]/g, "-")}-${process.pid}`;

export default defineConfig({
  testDir: "./test/e2e",
  outputDir: resolve("..", ".local", "tmp", "evaluator", `playwright-${runId}`),
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:1421",
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npx vite --host 127.0.0.1 --port 1421 --strictPort",
    url: "http://127.0.0.1:1421",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
