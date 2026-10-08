import type { McpRegistryPackage, McpRegistryRemote, McpRegistryServer } from "./protocol";

export type McpRegistrySetupOption =
  | {
      key: string;
      kind: "package";
      transport: "stdio";
      packageInfo: McpRegistryPackage;
      remote: null;
      label: string;
    }
  | {
      key: string;
      kind: "remote";
      transport: "streamable-http";
      packageInfo: null;
      remote: McpRegistryRemote;
      label: string;
    };

export function getMcpRegistrySetupOptions(server: McpRegistryServer): McpRegistrySetupOption[];
export function findMcpRegistrySetupOption(server: McpRegistryServer, key: string): McpRegistrySetupOption | undefined;
export function missingRequiredEnvironment(names: string[], values: Record<string, string>): string[];
export function getSelectedMcpAutoRunHashes(
  tools: readonly { mcp_descriptor_hash: string; read_only_candidate: boolean }[],
  selectedByHash: Readonly<Record<string, boolean>>,
): string[];
