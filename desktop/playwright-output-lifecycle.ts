import { randomUUID } from "node:crypto";
import { lstat, realpath, rm } from "node:fs/promises";
import {
  basename,
  dirname,
  resolve,
} from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = dirname(fileURLToPath(import.meta.url));
export const PLAYWRIGHT_OUTPUT_ROOT = resolve(
  desktopRoot,
  "..",
  ".local",
  "tmp",
  "evaluator",
);

const runDirectoryName = /^playwright-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function createPlaywrightOutputDir(outputRoot = PLAYWRIGHT_OUTPUT_ROOT): string {
  return resolve(outputRoot, `playwright-${randomUUID()}`);
}

function samePath(left: string, right: string): boolean {
  return process.platform === "win32"
    ? left.toLocaleLowerCase("en-US") === right.toLocaleLowerCase("en-US")
    : left === right;
}

function isMissing(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "ENOENT"
  );
}

export async function cleanupPassedPlaywrightOutput(options: {
  status: "passed" | "failed" | "timedout" | "interrupted";
  outputRoot: string;
  managedOutputDir: string;
  actualOutputDirs: readonly string[];
}): Promise<boolean> {
  const managedOutputDir = resolve(options.managedOutputDir);
  const outputRoot = resolve(options.outputRoot);
  const actualOutputDirs = options.actualOutputDirs.map((path) => resolve(path));

  if (
    options.status !== "passed" ||
    actualOutputDirs.length !== 1 ||
    !samePath(actualOutputDirs[0], managedOutputDir) ||
    !samePath(dirname(managedOutputDir), outputRoot) ||
    !runDirectoryName.test(basename(managedOutputDir))
  ) {
    return false;
  }

  try {
    const [realOutputRoot, outputStat] = await Promise.all([
      realpath(outputRoot),
      lstat(managedOutputDir),
    ]);
    if (!outputStat.isDirectory() || outputStat.isSymbolicLink()) return false;

    const realOutputDir = await realpath(managedOutputDir);
    if (!samePath(dirname(realOutputDir), realOutputRoot)) return false;

    const realProjectOutputDir = await realpath(actualOutputDirs[0]);
    if (!samePath(realProjectOutputDir, realOutputDir)) return false;

    await rm(realOutputDir, { recursive: true, force: false });
    return true;
  } catch (error) {
    if (isMissing(error)) return false;
    throw error;
  }
}
