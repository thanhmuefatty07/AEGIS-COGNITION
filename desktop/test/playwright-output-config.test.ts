import { afterAll, beforeAll, expect, it } from "vitest";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  access,
  mkdir,
  mkdtemp,
  open,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { FullConfig, FullResult, Suite } from "@playwright/test/reporter";

import config from "../playwright.config";
import {
  createPlaywrightOutputDir,
  PLAYWRIGHT_OUTPUT_ROOT,
} from "../playwright-output-lifecycle";
import CleanupPassedPlaywrightOutputReporter from "./support/cleanup-passed-playwright-output-reporter";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const e2eTestDir = resolve(desktopRoot, "test", "e2e");
const playwrightCli = resolve(desktopRoot, "node_modules", "@playwright", "test", "cli.js");
const outputCleanupReporter = resolve(
  desktopRoot,
  "test",
  "support",
  "cleanup-passed-playwright-output-reporter.ts",
);
let outputContractScratchRoot: string;

beforeAll(async () => {
  await mkdir(PLAYWRIGHT_OUTPUT_ROOT, { recursive: true });
  outputContractScratchRoot = await mkdtemp(
    join(PLAYWRIGHT_OUTPUT_ROOT, "playwright-output-contract-"),
  );
  process.stderr.write(`[test scratch] ${outputContractScratchRoot}\n`);
});

afterAll(async () => {
  await rm(outputContractScratchRoot, { recursive: true });
  await expect(access(outputContractScratchRoot)).rejects.toMatchObject({ code: "ENOENT" });
});

async function withOutputFixture(
  run: (fixture: { outputRoot: string; outputDir: string; artifact: string }) => Promise<void>,
): Promise<void> {
  const scratchRoot = join(outputContractScratchRoot, `case-${randomUUID()}`);
  await mkdir(scratchRoot);
  const outputRoot = join(scratchRoot, "evaluator");
  const outputDir = createPlaywrightOutputDir(outputRoot);
  const artifact = join(outputDir, "trace.zip");

  try {
    await mkdir(outputDir, { recursive: true });
    await writeFile(artifact, "synthetic trace fixture\n", "utf8");
    await run({ outputRoot, outputDir, artifact });
  } finally {
    await rm(scratchRoot, { recursive: true });
    await expect(access(scratchRoot)).rejects.toMatchObject({ code: "ENOENT" });
  }
}

function startReporter(
  reporter: CleanupPassedPlaywrightOutputReporter,
  outputDirs: readonly string[],
): void {
  reporter.onBegin(
    { projects: outputDirs.map((outputDir) => ({ outputDir })) } as FullConfig,
    {} as Suite,
  );
}

async function finishReporter(
  reporter: CleanupPassedPlaywrightOutputReporter,
  status: FullResult["status"],
): Promise<void> {
  await reporter.onEnd({ status } as FullResult);
  await reporter.onExit();
}

it("allocates a run-specific E2E output directory under the local evaluator root", () => {
  expect(dirname(config.outputDir)).toBe(PLAYWRIGHT_OUTPUT_ROOT);
  expect(basename(config.outputDir)).toMatch(/^playwright-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);

  const firstRun = createPlaywrightOutputDir();
  const secondRun = createPlaywrightOutputDir();
  expect(firstRun).not.toBe(secondRun);
  expect(dirname(firstRun)).toBe(PLAYWRIGHT_OUTPUT_ROOT);
  expect(dirname(secondRun)).toBe(PLAYWRIGHT_OUTPUT_ROOT);
});

it("registers success-only cleanup after the visible list reporter", () => {
  expect(config.reporter).toEqual(
    expect.arrayContaining([
      ["list"],
      [
        expect.stringMatching(/cleanup-passed-playwright-output-reporter/),
        expect.objectContaining({
          outputRoot: PLAYWRIGHT_OUTPUT_ROOT,
          managedOutputDir: config.outputDir,
        }),
      ],
    ]),
  );
});

it("removes only this run's output after a fully passed Playwright run", async () => {
  await withOutputFixture(async ({ outputRoot, outputDir, artifact }) => {
    const reporter = new CleanupPassedPlaywrightOutputReporter({
      outputRoot,
      managedOutputDir: outputDir,
    });
    startReporter(reporter, [outputDir]);

    await reporter.onEnd({ status: "passed" } as FullResult);
    await expect(access(outputDir)).resolves.toBeUndefined();

    await reporter.onExit();

    await expect(readFile(artifact, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
    await expect(access(outputDir)).rejects.toMatchObject({ code: "ENOENT" });
  });
});

it.each(["failed", "timedout", "interrupted"] as const)(
  "retains diagnostic output when Playwright ends with %s",
  async (status) => {
    await withOutputFixture(async ({ outputRoot, outputDir, artifact }) => {
      const reporter = new CleanupPassedPlaywrightOutputReporter({
        outputRoot,
        managedOutputDir: outputDir,
      });
      startReporter(reporter, [outputDir]);

      await finishReporter(reporter, status);

      await expect(readFile(artifact, "utf8")).resolves.toBe("synthetic trace fixture\n");
    });
  },
);

it("does not remove a caller-supplied Playwright output override", async () => {
  await withOutputFixture(async ({ outputRoot, outputDir, artifact }) => {
    const callerOutputDir = join(outputDir, "caller-owned-output");
    await mkdir(callerOutputDir);
    const callerArtifact = join(callerOutputDir, "trace.zip");
    await writeFile(callerArtifact, "caller-owned trace\n", "utf8");

    const reporter = new CleanupPassedPlaywrightOutputReporter({
      outputRoot,
      managedOutputDir: outputDir,
    });
    startReporter(reporter, [callerOutputDir]);

    await finishReporter(reporter, "passed");

    await expect(readFile(artifact, "utf8")).resolves.toBe("synthetic trace fixture\n");
    await expect(readFile(callerArtifact, "utf8")).resolves.toBe("caller-owned trace\n");
  });
});

it("does not remove a run directory outside its configured output root", async () => {
  await withOutputFixture(async ({ outputRoot }) => {
    const outsideRunDir = createPlaywrightOutputDir(join(outputRoot, "..", "outside"));
    await mkdir(outsideRunDir, { recursive: true });
    const outsideArtifact = join(outsideRunDir, "trace.zip");
    await writeFile(outsideArtifact, "outside trace\n", "utf8");

    const reporter = new CleanupPassedPlaywrightOutputReporter({
      outputRoot,
      managedOutputDir: outsideRunDir,
    });
    startReporter(reporter, [outsideRunDir]);

    await finishReporter(reporter, "passed");

    await expect(readFile(outsideArtifact, "utf8")).resolves.toBe("outside trace\n");
  });
});

it("preserves outputs when a project-specific directory differs from the run root", async () => {
  await withOutputFixture(async ({ outputRoot, outputDir }) => {
    const projectOutputDir = join(outputDir, "chromium");
    await mkdir(projectOutputDir);
    const projectArtifact = join(projectOutputDir, "video.webm");
    await writeFile(projectArtifact, "synthetic video\n", "utf8");

    const reporter = new CleanupPassedPlaywrightOutputReporter({
      outputRoot,
      managedOutputDir: outputDir,
    });
    startReporter(reporter, [outputDir, projectOutputDir]);

    await finishReporter(reporter, "passed");

    await expect(readFile(projectArtifact, "utf8")).resolves.toBe("synthetic video\n");
    await expect(access(outputDir)).resolves.toBeUndefined();
  });
});

it(
  "runs a browser-free Playwright test and removes its real successful output directory",
  async () => {
    const scratchRoot = await mkdtemp(
      join(outputContractScratchRoot, "runner-integration-"),
    );
    const ownedFiles: string[] = [];
    try {
      const specPath = join(e2eTestDir, `output-lifecycle-${randomUUID()}.spec.ts`);
      const configPath = join(scratchRoot, "playwright-lifecycle.config.mjs");
      const signalPath = join(scratchRoot, "test-body-ran.txt");
      const managedOutputDir = createPlaywrightOutputDir();

      const writeOwnedFile = async (path: string, content: string): Promise<void> => {
        const handle = await open(path, "wx");
        ownedFiles.push(path);
        try {
          await handle.writeFile(content, "utf8");
        } finally {
          await handle.close();
        }
      };

      const specSource = [
        'import { test } from "@playwright/test";',
        'import { writeFile } from "node:fs/promises";',
        'test("writes a sentinel into the real Playwright run output", async ({}, testInfo) => {',
        '  await writeFile(testInfo.outputPath("sentinel.txt"), "runner executed\\n", "utf8");',
        '  const signalPath = process.env.AEGIS_PLAYWRIGHT_OUTPUT_SIGNAL;',
        '  if (!signalPath) throw new Error("integration signal path missing");',
        '  await writeFile(signalPath, "test body completed\\n", "utf8");',
        '});',
        "",
      ].join("\n");
      await writeOwnedFile(specPath, specSource);

      const smokeConfig = {
        testDir: e2eTestDir,
        testMatch: [basename(specPath)],
        outputDir: managedOutputDir,
        reporter: [
          ["list"],
          [
            outputCleanupReporter,
            { outputRoot: PLAYWRIGHT_OUTPUT_ROOT, managedOutputDir },
          ],
        ],
        workers: 1,
        timeout: 10_000,
      };
      await writeOwnedFile(
        configPath,
        `export default ${JSON.stringify(smokeConfig, null, 2)};\n`,
      );

      const result = spawnSync(
        process.execPath,
        [playwrightCli, "test", "--config", configPath],
        {
          cwd: desktopRoot,
          encoding: "utf8",
          env: {
            ...process.env,
            AEGIS_PLAYWRIGHT_OUTPUT_SIGNAL: signalPath,
          },
          maxBuffer: 2 * 1024 * 1024,
          timeout: 60_000,
        },
      );
      const runnerOutput = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
      if (result.error || result.status !== 0) {
        throw new Error(
          `Playwright integration run did not pass; output root: ${managedOutputDir}\n${runnerOutput}`,
          { cause: result.error },
        );
      }

      await expect(readFile(signalPath, "utf8")).resolves.toBe("test body completed\n");
      await expect(access(managedOutputDir)).rejects.toMatchObject({ code: "ENOENT" });
    } finally {
      for (const path of ownedFiles) {
        await rm(path, { force: false });
      }
      await rm(scratchRoot, { recursive: true, force: false });
      await expect(access(scratchRoot)).rejects.toMatchObject({ code: "ENOENT" });
    }
  },
  70_000,
);
