export type McpSetupSuggestion =
  | { server_id: string; transport: "stdio"; command: string[]; cwd?: string }
  | {
      server_id: string;
      transport: "streamable-http";
      endpoint: string;
      allowed_host: string;
    };

export type ExtensionPackImportResult = {
  extension_id: string;
  mcp_server_ids: string[];
  mcp_setup_suggestions: McpSetupSuggestion[];
};

export function parseExtensionPackImport(value: unknown): ExtensionPackImportResult | null;
