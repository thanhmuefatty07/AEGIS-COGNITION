import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
} from "@playwright/test/reporter";
import { resolve } from "node:path";
import { cleanupPassedPlaywrightOutput } from "../../playwright-output-lifecycle";

interface Options {
  outputRoot: string;
  managedOutputDir: string;
}

export default class CleanupPassedPlaywrightOutputReporter implements Reporter {
  private actualOutputDirs: string[] = [];
  private runStatus: FullResult["status"] | undefined;

  constructor(private readonly options: Options) {}

  onBegin(config: FullConfig, _suite: Suite): void {
    this.actualOutputDirs = config.projects.map((project) => resolve(project.outputDir));
  }

  onEnd(result: FullResult): void {
    this.runStatus = result.status;
  }

  async onExit(): Promise<void> {
    if (this.runStatus !== "passed" || this.actualOutputDirs.length === 0) return;

    await cleanupPassedPlaywrightOutput({
      status: this.runStatus,
      outputRoot: this.options.outputRoot,
      managedOutputDir: this.options.managedOutputDir,
      actualOutputDirs: this.actualOutputDirs,
    });
  }

  printsToStdio(): boolean {
    return false;
  }
}
