/**
 * @typedef {import("./protocol").McpRegistryServer} McpRegistryServer
 * @typedef {import("./protocol").McpRegistryPackage} McpRegistryPackage
 * @typedef {import("./protocol").McpRegistryRemote} McpRegistryRemote
 * @typedef {{key: string, kind: "package", transport: "stdio", packageInfo: McpRegistryPackage, remote: null, label: string} | {key: string, kind: "remote", transport: "streamable-http", packageInfo: null, remote: McpRegistryRemote, label: string}} McpRegistrySetupOption
 */

/**
 * Enumerate supported setup choices without choosing one on the user's behalf.
 * Registry metadata remains descriptive; this helper never executes it.
 * @param {McpRegistryServer} server
 * @returns {McpRegistrySetupOption[]}
 */
export function getMcpRegistrySetupOptions(server) {
  /** @type {McpRegistrySetupOption[]} */
  const options = [];
  server.packages.forEach((packageInfo, index) => {
    if (packageInfo.transport !== "stdio") return;
    options.push({
      key: `package:${index}`,
      kind: "package",
      transport: "stdio",
      packageInfo,
      remote: null,
      label: `${packageInfo.registry_type} · ${packageInfo.identifier}@${packageInfo.version}${packageInfo.runtime_hint ? ` · ${packageInfo.runtime_hint}` : ""}`,
    });
  });
  server.remotes.forEach((remote, index) => {
    if (remote.transport !== "streamable-http" || !remote.endpoint) return;
    let endpointLabel = `Remote endpoint ${index + 1}`;
    try {
      endpointLabel = new URL(remote.endpoint).origin;
    } catch {
      // Keep malformed directory metadata visible as a numbered choice, not a URL to trust.
    }
    options.push({
      key: `remote:${index}`,
      kind: "remote",
      transport: "streamable-http",
      packageInfo: null,
      remote,
      label: `Remote · ${endpointLabel}`,
    });
  });
  return options;
}

/**
 * Resolve only an exact option key from the current server's choices.
 * @param {McpRegistryServer} server
 * @param {string} key
 * @returns {McpRegistrySetupOption | undefined}
 */
export function findMcpRegistrySetupOption(server, key) {
  if (!key) return undefined;
  return getMcpRegistrySetupOptions(server).find((option) => option.key === key);
}

/**
 * Return required names that do not yet have a non-empty value.
 * @param {string[]} names
 * @param {Record<string, string>} values
 * @returns {string[]}
 */
export function missingRequiredEnvironment(names, values) {
  const validNames = [...new Set(names.filter((name) =>
    typeof name === "string" && /^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name)))];
  return validNames.filter((name) => {
    if (!Object.prototype.hasOwnProperty.call(values, name)) return true;
    return typeof values[name] !== "string" || !values[name].trim();
  });
}

/**
 * Return only explicitly selected, eligible tools from the currently reviewed list.
 * @param {Array<{mcp_descriptor_hash: string, read_only_candidate: boolean}>} tools
 * @param {Readonly<Record<string, boolean>>} selectedByHash
 * @returns {string[]}
 */
export function getSelectedMcpAutoRunHashes(tools, selectedByHash) {
  return [...new Set(tools
    .filter((tool) => tool.read_only_candidate && selectedByHash[tool.mcp_descriptor_hash] === true)
    .map((tool) => tool.mcp_descriptor_hash))].sort();
}
