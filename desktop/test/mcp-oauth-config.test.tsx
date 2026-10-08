// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../src/App";
import { installDesktopTransport } from "../src/protocol";
import { previewRequest } from "../src/preview_backend";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MCP OAuth setup guidance", () => {
  it("requires a successful connection test before enabling an approved server", async () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", { configurable: true, value: vi.fn() });
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
    }));
    vi.stubGlobal("scrollTo", vi.fn());

    const server = {
      schema: "aegis-desktop-mcp-server-v1",
      server_id: "oauth-example",
      description: "Remote MCP server used by the renderer test",
      transport: "streamable-http",
      source: "workspace",
      approved: true,
      descriptor_hash: "a".repeat(64),
      environment_variables: [],
    };
    const restoreTransport = installDesktopTransport(async (command, payload) => {
      const result = await previewRequest(command, payload) as Record<string, unknown>;
      if (command === "extensions.discover") return { ...result, mcp_servers: [server] };
      if (command === "extensions.state") {
        const records = Array.isArray(result.records) ? result.records : [];
        return {
          ...result,
          records: [...records, {
            kind: "mcp",
            id: server.server_id,
            descriptor_hash: server.descriptor_hash,
            enabled: false,
            approved: true,
            updated_at_ms: 0,
          }],
        };
      }
      return result;
    });

    try {
      render(<App />);
      await screen.findByRole("heading", { name: "What are you working on?" });
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      fireEvent.click(screen.getByRole("button", { name: "MCP", exact: true }));

      const enableButton = await screen.findByRole("button", { name: "Enable tools" });
      expect(enableButton.hasAttribute("disabled")).toBe(true);
    } finally {
      restoreTransport();
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
    }
  });

  it("explains that a supplied Client ID bypasses automatic registration", async () => {
    const originalInnerWidth = window.innerWidth;
    const originalElementScrollTo = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollTo");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
    }));
    vi.stubGlobal("scrollTo", vi.fn());

    const server = {
      schema: "aegis-desktop-mcp-server-v1",
      server_id: "oauth-example",
      description: "Remote MCP server used by the renderer test",
      transport: "streamable-http",
      source: "workspace",
      approved: true,
      descriptor_hash: "a".repeat(64),
      environment_variables: [],
    };
    const restoreTransport = installDesktopTransport(async (command, payload) => {
      const result = await previewRequest(command, payload) as Record<string, unknown>;
      if (command === "extensions.discover") {
        return { ...result, mcp_servers: [server] };
      }
      if (command === "extensions.state") {
        const records = Array.isArray(result.records) ? result.records : [];
        return {
          ...result,
          records: [...records, {
            kind: "mcp",
            id: server.server_id,
            descriptor_hash: server.descriptor_hash,
            enabled: false,
            approved: true,
            updated_at_ms: 0,
          }],
        };
      }
      return result;
    });

    try {
      render(<App />);
      await screen.findByRole("heading", { name: "What are you working on?" });
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      fireEvent.click(screen.getByRole("button", { name: "MCP", exact: true }));

      const clientId = await screen.findByLabelText("OAuth Client ID (only if requested)");
      expect(clientId.getAttribute("placeholder")).toBe("Leave blank for automatic registration");
      expect(screen.getByText(/reuse a saved Client ID or try automatic registration when needed/)).toBeDefined();
      expect(screen.getByText(/server requires a pre-registered Client ID, enter it here/)).toBeDefined();
      expect(screen.getByText(/AEGIS uses that ID directly and skips automatic registration\./)).toBeDefined();
    } finally {
      restoreTransport();
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      if (originalElementScrollTo) {
        Object.defineProperty(HTMLElement.prototype, "scrollTo", originalElementScrollTo);
      } else {
        Reflect.deleteProperty(HTMLElement.prototype, "scrollTo");
      }
    }
  });
});
