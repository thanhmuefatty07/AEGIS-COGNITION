declare module "node:crypto" {
  export function randomUUID(): string;
}

declare module "node:url" {
  export function fileURLToPath(url: string | URL): string;
}

declare module "node:path" {
  export function basename(path: string, suffix?: string): string;
  export function dirname(path: string): string;
  export function join(...paths: string[]): string;
  export function resolve(...paths: string[]): string;
}

declare module "node:fs/promises" {
  export interface StatsLike {
    isDirectory(): boolean;
    isSymbolicLink(): boolean;
  }
  export interface FileHandleLike {
    writeFile(content: string, encoding?: string): Promise<void>;
    close(): Promise<void>;
  }
  export function access(path: string, mode?: number): Promise<void>;
  export function lstat(path: string): Promise<StatsLike>;
  export function mkdir(path: string, options?: { recursive?: boolean }): Promise<string | undefined>;
  export function mkdtemp(prefix: string): Promise<string>;
  export function open(path: string, flags?: string): Promise<FileHandleLike>;
  export function readFile(path: string, encoding: BufferEncoding | "utf8"): Promise<string>;
  export function realpath(path: string): Promise<string>;
  export function rm(path: string, options?: { recursive?: boolean; force?: boolean }): Promise<void>;
  export function writeFile(path: string, data: string, encoding?: BufferEncoding | "utf8"): Promise<void>;
}

declare module "node:child_process" {
  export interface SpawnSyncResult {
    status: number | null;
    stdout: string | null;
    stderr: string | null;
    error?: Error;
  }
  export function spawnSync(
    command: string,
    args?: readonly string[],
    options?: {
      cwd?: string;
      encoding?: string;
      env?: Record<string, string | undefined>;
      maxBuffer?: number;
      timeout?: number;
    },
  ): SpawnSyncResult;
}

declare var process: {
  readonly platform: string;
  readonly execPath: string;
  readonly env: Record<string, string | undefined>;
  readonly stderr: {
    write(chunk: string): boolean;
  };
};
