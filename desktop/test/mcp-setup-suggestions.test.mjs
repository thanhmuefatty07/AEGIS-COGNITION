import assert from "node:assert/strict";
import test from "node:test";

import { parseExtensionPackImport } from "../src/mcp_setup_suggestions.js";

const validResult = {
  schema: "aegis-desktop-extension-pack-import-v1",
  extension_id: "research-pack",
  mcp_server_ids: ["local-docs", "remote-docs"],
  mcp_setup_suggestions: [
    {
      server_id: "local-docs",
      transport: "stdio",
      command: ["uvx", "--from", "example-mcp-server", "example-mcp-server"],
      cwd: "C:\\projects\\research-pack",
    },
    {
      server_id: "remote-docs",
      transport: "streamable-http",
      endpoint: "https://docs.example.test/mcp",
      allowed_host: "docs.example.test",
    },
  ],
};

test("accepts bounded stdio and HTTPS setup drafts for MCP servers in the imported package", () => {
  assert.deepEqual(parseExtensionPackImport(validResult), {
    extension_id: "research-pack",
    mcp_server_ids: ["local-docs", "remote-docs"],
    mcp_setup_suggestions: validResult.mcp_setup_suggestions,
  });
});

test("supports older import responses that contain no optional setup suggestions", () => {
  const { mcp_setup_suggestions: _ignored, ...legacyResult } = validResult;
  assert.deepEqual(parseExtensionPackImport(legacyResult), {
    extension_id: "research-pack",
    mcp_server_ids: ["local-docs", "remote-docs"],
    mcp_setup_suggestions: [],
  });
});

test("accepts older stdio suggestions without a working directory", () => {
  const result = structuredClone(validResult);
  delete result.mcp_setup_suggestions[0].cwd;
  assert.deepEqual(parseExtensionPackImport(result).mcp_setup_suggestions[0], {
    server_id: "local-docs",
    transport: "stdio",
    command: ["uvx", "--from", "example-mcp-server", "example-mcp-server"],
  });
});

test("rejects credentials, non-HTTPS endpoints, mismatched hosts, and out-of-package servers", () => {
  for (const patch of [
    { endpoint: "https://user:secret@docs.example.test/mcp" },
    { endpoint: "https://docs.example.test/mcp?token=secret" },
    { endpoint: "http://docs.example.test/mcp" },
    { allowed_host: "other.example.test" },
    { server_id: "unlisted-server" },
  ]) {
    const result = structuredClone(validResult);
    Object.assign(result.mcp_setup_suggestions[1], patch);
    assert.equal(parseExtensionPackImport(result), null);
  }
});

test("rejects malformed argv and duplicate setup entries before creating UI drafts", () => {
  const malformedArgv = structuredClone(validResult);
  malformedArgv.mcp_setup_suggestions[0].command[1] = "--flag\nsecond-line";
  assert.equal(parseExtensionPackImport(malformedArgv), null);

  const duplicate = structuredClone(validResult);
  duplicate.mcp_setup_suggestions.push(duplicate.mcp_setup_suggestions[0]);
  assert.equal(parseExtensionPackImport(duplicate), null);
});

test("rejects malformed local working directories", () => {
  for (const cwd of ["", "relative\npath", "x".repeat(4_097)]) {
    const result = structuredClone(validResult);
    result.mcp_setup_suggestions[0].cwd = cwd;
    assert.equal(parseExtensionPackImport(result), null);
  }
});
