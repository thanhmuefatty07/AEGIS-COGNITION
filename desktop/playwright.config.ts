import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  createPlaywrightOutputDir,
  PLAYWRIGHT_OUTPUT_ROOT,
} from "./playwright-output-lifecycle";

const desktopRoot = dirname(fileURLToPath(import.meta.url));
const outputDir = createPlaywrightOutputDir();
const outputCleanupReporter = resolve(
  desktopRoot,
  "test",
  "support",
  "cleanup-passed-playwright-output-reporter.ts",
);

export default Object.assign(
  defineConfig({
    testDir: "./test/e2e",
    outputDir,
    timeout: 30_000,
    expect: { timeout: 5_000 },
    fullyParallel: true,
    retries: 0,
    reporter: [
      ["list"],
      [outputCleanupReporter, { outputRoot: PLAYWRIGHT_OUTPUT_ROOT, managedOutputDir: outputDir }],
    ],
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
  }),
  { outputDir },
);
