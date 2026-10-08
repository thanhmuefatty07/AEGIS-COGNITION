import assert from "node:assert/strict";
import test from "node:test";

import { findModelForConnectionAndId } from "../src/model_selection.js";
import { clipboardImageFiles } from "../src/clipboard_images.js";
import {
  getSelectedMcpAutoRunHashes,
  findMcpRegistrySetupOption,
  getMcpRegistrySetupOptions,
  missingRequiredEnvironment,
} from "../src/mcp_registry_selection.js";

test("grants only individually selected read-only tools from the reviewed MCP list", () => {
  const first = "a".repeat(64);
  const second = "b".repeat(64);
  const writer = "c".repeat(64);
  const stale = "d".repeat(64);
  const tools = [
    { mcp_descriptor_hash: first, read_only_candidate: true },
    { mcp_descriptor_hash: second, read_only_candidate: true },
    { mcp_descriptor_hash: writer, read_only_candidate: false },
  ];

  assert.deepEqual(
    getSelectedMcpAutoRunHashes(tools, { [first]: true, [second]: false, [writer]: true, [stale]: true }),
    [first],
  );
});

test("returns multiple selected MCP grants in stable order", () => {
  const first = "a".repeat(64);
  const second = "b".repeat(64);
  const tools = [
    { mcp_descriptor_hash: second, read_only_candidate: true },
    { mcp_descriptor_hash: first, read_only_candidate: true },
  ];

  assert.deepEqual(getSelectedMcpAutoRunHashes(tools, { [first]: true, [second]: true }), [first, second]);
});

test("selects a duplicate model ID only from the requested provider connection", () => {
  const models = [
    {
      connection_id: "nvidia-nim-session",
      model_id: "openai/gpt-oss-20b",
      supports_vision: false,
      reasoning_efforts: ["low", "medium", "high"],
    },
    {
      connection_id: "groq-session",
      model_id: "openai/gpt-oss-20b",
      supports_vision: true,
      reasoning_efforts: ["none", "high"],
    },
  ];

  assert.equal(findModelForConnectionAndId(models, "groq-session", "openai/gpt-oss-20b"), models[1]);
  assert.equal(findModelForConnectionAndId(models, "nvidia-nim-session", "openai/gpt-oss-20b"), models[0]);
});

test("does not fall back to another provider or an arbitrary model", () => {
  const models = [{ connection_id: "provider-a", model_id: "shared-model" }];

  assert.equal(findModelForConnectionAndId(models, "provider-b", "shared-model"), undefined);
  assert.equal(findModelForConnectionAndId(models, null, "shared-model"), undefined);
  assert.equal(findModelForConnectionAndId(models, "provider-a", ""), undefined);
});

test("prefers clipboard files so the same image is not added twice from file and item views", () => {
  const fileListImage = { name: "image.png", type: "image/png" };
  const itemViewImage = { name: "image.png", type: "image/png" };
  const clipboardData = {
    files: [fileListImage],
    items: [{ kind: "file", type: "image/png", getAsFile: () => itemViewImage }],
  };

  assert.deepEqual(clipboardImageFiles(clipboardData), [fileListImage]);
});

test("falls back to image items when the clipboard file list has no images", () => {
  const imageFile = { name: "image.webp", type: "image/webp" };
  const unsupportedImage = { name: "image.svg", type: "image/svg+xml" };
  const clipboardData = {
    files: [{ name: "notes.txt", type: "text/plain" }],
    items: [
      { kind: "string", type: "text/plain", getAsFile: () => null },
      { kind: "file", type: "image/webp", getAsFile: () => imageFile },
      { kind: "file", type: "image/svg+xml", getAsFile: () => unsupportedImage },
      { kind: "file", type: "image/png", getAsFile: () => null },
    ],
  };

  assert.deepEqual(clipboardImageFiles(clipboardData), [imageFile, unsupportedImage]);
});

test("lists each supported MCP directory package and endpoint without auto-selecting the first", () => {
  const server = {
    name: "example/server",
    title: null,
    version: "1",
    description: "Example",
    packages: [
      { registry_type: "npm", identifier: "@example/first", version: "1.0.0", runtime_hint: "npx", transport: "stdio", required_environment: [] },
      { registry_type: "pypi", identifier: "example-second", version: "2.0.0", runtime_hint: "uvx", transport: "stdio", required_environment: [] },
      { registry_type: "npm", identifier: "unsupported", version: "1", runtime_hint: null, transport: "unsupported", required_environment: [] },
    ],
    remotes: [
      { transport: "streamable-http", endpoint: "https://one.example/mcp?token=secret", requires_headers: true },
      { transport: "unsupported", endpoint: "https://ignored.example/mcp", requires_headers: false },
    ],
  };

  const options = getMcpRegistrySetupOptions(server);
  assert.deepEqual(options.map((option) => option.key), ["package:0", "package:1", "remote:0"]);
  assert.equal(options[2].label, "Remote · https://one.example");
  assert.equal(options[2].label.includes("secret"), false);
  assert.equal(findMcpRegistrySetupOption(server, "package:1")?.packageInfo?.identifier, "example-second");
  assert.equal(findMcpRegistrySetupOption(server, "remote:0")?.remote?.endpoint, server.remotes[0].endpoint);
  assert.equal(findMcpRegistrySetupOption(server, "package:99"), undefined);
});

test("does not offer incomplete MCP remotes or unsupported package transports", () => {
  const server = {
    name: "example/server",
    title: null,
    version: "1",
    description: "Example",
    packages: [{ registry_type: "npm", identifier: "p", version: "1", runtime_hint: null, transport: "unsupported", required_environment: [] }],
    remotes: [{ transport: "streamable-http", endpoint: null, requires_headers: false }],
  };

  assert.deepEqual(getMcpRegistrySetupOptions(server), []);
});

test("detects missing required MCP environment values without treating whitespace as configured", () => {
  const draft = Object.fromEntries([["API_TOKEN", ""], ["REGION", "  "], ["__proto__", ""]]);

  assert.deepEqual(missingRequiredEnvironment(["API_TOKEN", "REGION", "__proto__"], draft), ["API_TOKEN", "REGION", "__proto__"]);
  assert.deepEqual(missingRequiredEnvironment(["API_TOKEN", "REGION"], { API_TOKEN: " secret ", REGION: "west" }), []);
});
