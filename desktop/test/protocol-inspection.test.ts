import { describe, expect, it } from "vitest";
import { parseConversationInspection } from "../src/protocol";

type InspectionFixture = {
  schema: string;
  conversation_id: string;
  revision: number;
  context: Record<string, unknown>;
  timeline: unknown[];
  approvals: unknown[];
};

function legacyInspection(): InspectionFixture {
  return {
    schema: "aegis-desktop-conversation-inspection-v1",
    conversation_id: "conversation-1",
    revision: 1,
    context: {
      schema: "aegis-desktop-context-inspection-v1",
      status: "NOT_CAPTURED",
      source_revision: null,
      context_manifest_hash: null,
      prompt_hash: null,
      token_budget: null,
      token_count: null,
      item_count: null,
      selection_backend: null,
      history_turn_count: 0,
      sensitive_content: "REDACTED",
    },
    timeline: [],
    approvals: [],
  };
}

describe("conversation inspection protocol", () => {
  it("accepts older responses without optional context delivery details", () => {
    expect(parseConversationInspection(legacyInspection()).context.inclusion).toBeUndefined();
  });

  it("preserves valid context inclusion and dispatch metadata", () => {
    const inspection = legacyInspection();
    inspection.context.inclusion = {
      schema: "aegis-context-inclusion-inspection-v1",
      status: "COMPOSED",
      manifest_sha256: "a".repeat(64),
      prompt_chars: 24,
      prompt_limit_chars: 2048,
      selected_count: 2,
      included_count: 2,
      omitted_count: 0,
      omitted_reasons: [],
      deduplicated_turn_count: 0,
    };
    inspection.context.request_attempt = {
      schema: "aegis-provider-request-attempt-inspection-v1",
      ordinal: 1,
      state: "DISPATCH_ATTEMPTED",
      payload_sha256: "b".repeat(64),
      payload_bytes: 24,
    };
    inspection.context.request_attempt_count = 1;

    const parsed = parseConversationInspection(inspection);

    expect(parsed.context.inclusion?.included_count).toBe(2);
    expect(parsed.context.request_attempt?.payload_bytes).toBe(24);
    expect(parsed.context.request_attempt_count).toBe(1);
  });

  it("rejects malformed context inclusion before rendering", () => {
    const inspection = legacyInspection();
    inspection.context.inclusion = {
      schema: "aegis-context-inclusion-inspection-v1",
      status: "COMPOSED",
      manifest_sha256: "a".repeat(64),
      prompt_chars: 24,
      prompt_limit_chars: "2048",
      selected_count: 2,
      included_count: 2,
      omitted_count: 0,
      omitted_reasons: [],
      deduplicated_turn_count: 0,
    };

    expect(() => parseConversationInspection(inspection)).toThrow(
      "Desktop service returned an invalid conversation inspection",
    );
  });

  it("rejects malformed provider attempt metadata before rendering", () => {
    const inspection = legacyInspection();
    inspection.context.request_attempt = {
      schema: "aegis-provider-request-attempt-inspection-v1",
      ordinal: 1,
      state: "DISPATCH_ATTEMPTED",
      payload_sha256: "not-a-sha256",
      payload_bytes: 24,
    };

    expect(() => parseConversationInspection(inspection)).toThrow(
      "Desktop service returned an invalid conversation inspection",
    );
  });

  it("rejects a negative provider attempt count", () => {
    const inspection = legacyInspection();
    inspection.context.request_attempt_count = -1;

    expect(() => parseConversationInspection(inspection)).toThrow(
      "Desktop service returned an invalid conversation inspection",
    );
  });
});
