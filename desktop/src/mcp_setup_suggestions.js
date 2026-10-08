/**
 * @typedef {{server_id: string, transport: "stdio", command: string[], cwd?: string} | {server_id: string, transport: "streamable-http", endpoint: string, allowed_host: string}} McpSetupSuggestion
 * @typedef {{extension_id: string, mcp_server_ids: string[], mcp_setup_suggestions: McpSetupSuggestion[]}} ExtensionPackImportResult
 */

const extensionIdPattern = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

/**
 * Validate the bounded host response before using untrusted package values as form drafts.
 * Older sidecars may omit suggestions; imported capabilities remain usable without them.
 * @param {unknown} value
 * @returns {ExtensionPackImportResult | null}
 */
export function parseExtensionPackImport(value) {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-extension-pack-import-v1"
    || typeof value.extension_id !== "string"
    || !extensionIdPattern.test(value.extension_id)
    || !Array.isArray(value.mcp_server_ids)
    || value.mcp_server_ids.length > 256) return null;

  const serverIds = value.mcp_server_ids;
  if (serverIds.some((id) => typeof id !== "string" || !extensionIdPattern.test(id))) return null;
  const uniqueIds = new Set(serverIds.map((id) => id.toLocaleLowerCase()));
  if (uniqueIds.size !== serverIds.length) return null;

  const rawSuggestions = value.mcp_setup_suggestions ?? [];
  if (!Array.isArray(rawSuggestions) || rawSuggestions.length > serverIds.length) return null;
  /** @type {McpSetupSuggestion[]} */
  const suggestions = [];
  const seenSuggestions = new Set();
  for (const item of rawSuggestions) {
    if (!isRecord(item) || typeof item.server_id !== "string") return null;
    const normalizedId = item.server_id.toLocaleLowerCase();
    if (!uniqueIds.has(normalizedId) || seenSuggestions.has(normalizedId)) return null;
    seenSuggestions.add(normalizedId);
    if (item.transport === "stdio") {
      if (!Array.isArray(item.command) || item.command.length < 1 || item.command.length > 32
        || item.command.some((part) => typeof part !== "string"
          || !part.trim()
          || part.length > 4_096
          || /[\x00\r\n]/.test(part))) return null;
      if (item.cwd !== undefined && (typeof item.cwd !== "string"
        || !item.cwd.trim()
        || item.cwd.length > 4_096
        || /[\x00\r\n]/.test(item.cwd))) return null;
      suggestions.push({
        server_id: item.server_id,
        transport: "stdio",
        command: [...item.command],
        ...(item.cwd === undefined ? {} : { cwd: item.cwd }),
      });
      continue;
    }
    if (item.transport === "streamable-http"
      && typeof item.endpoint === "string"
      && item.endpoint.length <= 8_192
      && typeof item.allowed_host === "string") {
      try {
        const endpoint = new URL(item.endpoint);
        if (endpoint.protocol !== "https:"
          || endpoint.username
          || endpoint.password
          || endpoint.search
          || endpoint.hash
          || endpoint.hostname.toLocaleLowerCase() !== item.allowed_host.toLocaleLowerCase()) return null;
      } catch {
        return null;
      }
      suggestions.push({
        server_id: item.server_id,
        transport: "streamable-http",
        endpoint: item.endpoint,
        allowed_host: item.allowed_host,
      });
      continue;
    }
    return null;
  }

  return {
    extension_id: value.extension_id,
    mcp_server_ids: [...serverIds],
    mcp_setup_suggestions: suggestions,
  };
}

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
