import { invoke } from "@tauri-apps/api/core";

export const REQUEST_SCHEMA = "aegis-desktop-command-v1" as const;
export const RESPONSE_SCHEMA = "aegis-desktop-response-v1" as const;
export const PROTOCOL_VERSION = 1 as const;

export type DesktopCommand =
  | "workspace.open"
  | "workspace.snapshot"
  | "workspace.source_snapshot"
  | "connections.list"
  | "connections.save"
  | "connections.discover"
  | "connections.disable"
  | "models.list"
  | "conversations.create"
  | "conversations.read"
  | "conversations.send"
  | "conversations.switch_model"
  | "memory.search"
  | "memory.inspect"
  | "memory.capture"
  | "memory.correct"
  | "memory.forget"
  | "memory.restore"
  | "memory.purge"
  | "service.shutdown";

export type DesktopRequest = {
  schema: typeof REQUEST_SCHEMA;
  protocol_version: typeof PROTOCOL_VERSION;
  request_id: string;
  command: DesktopCommand;
  payload: Record<string, unknown>;
};

export type DesktopResponse<T> = {
  schema: typeof RESPONSE_SCHEMA;
  protocol_version: typeof PROTOCOL_VERSION;
  request_id: string;
  status: "ok";
  result: T;
} | {
  schema: typeof RESPONSE_SCHEMA;
  protocol_version: typeof PROTOCOL_VERSION;
  request_id: string;
  status: "error";
  error: { code: string; message: string };
};

export type WorkspaceSnapshot = {
  open: boolean;
  workspace_path: string | null;
  state_path: string | null;
  profile_id: string;
  native_runtime_available: boolean;
};

export type SourceSnapshot = {
  identity: {
    project_id: string;
    root: string;
    vcs: string;
    repository_root: string | null;
    common_git_dir: string | null;
    checkout_id: string;
    branch: string | null;
    head: string | null;
    dirty: boolean;
    dirty_paths: string[];
  };
  revision: string;
  created_at_ms: number;
  files: Array<{
    relative_path: string;
    language: string;
    size_bytes: number;
    modified_ns: number;
    content_hash: string | null;
    extraction_status: string;
    symbols: Array<{ name: string; kind: string; line: number; signature_hash: string }>;
    imports: string[];
    error: string | null;
    symlink_target: string | null;
  }>;
  lineage: Array<{
    source_path: string;
    symbol: string;
    candidate_paths: string[];
    reason: string;
    certainty: string;
  }>;
  stale_paths: string[];
  deleted_paths: string[];
  overflowed: boolean;
};

export type Conversation = {
  conversation_id: string;
  owner_id: string;
  title: string;
  connection_id: string;
  model_id: string;
  status: string;
  revision: number;
  created_at_ms: number;
  updated_at_ms: number;
};

export type ConversationTurn = {
  turn_id: string;
  role: string;
  status: string;
  content: string;
  revision: number;
};

export type ConversationSnapshot = {
  conversation: Conversation;
  turns: ConversationTurn[];
};

export type MemoryRecord = {
  memory_id: string;
  owner_id: string;
  scope_kind: string;
  lifecycle: string;
  validation: string;
  revision: number;
  observed_at_ms: number;
  content_hash: string | null;
  content: string | null;
  validation_basis?: string | null;
  validation_reason?: string | null;
  memory_kind: string;
};

export function createRequest(command: DesktopCommand, payload: Record<string, unknown>): DesktopRequest {
  return {
    schema: REQUEST_SCHEMA,
    protocol_version: PROTOCOL_VERSION,
    request_id: crypto.randomUUID(),
    command,
    payload,
  };
}

export async function desktopRequest<T>(
  command: DesktopCommand,
  payload: Record<string, unknown>,
  decode: (value: unknown) => T,
): Promise<T> {
  const request = createRequest(command, payload);
  const raw = await invoke<string>("desktop_request", { frame: JSON.stringify(request) });
  const response: unknown = JSON.parse(raw);
  if (!isDesktopResponse<T>(response)) {
    throw new Error("Desktop service returned an invalid response");
  }
  if (response.status === "error") {
    throw new Error(`${response.error.code}: ${response.error.message}`);
  }
  return decode(response.result);
}

export function parseWorkspaceSnapshot(value: unknown): WorkspaceSnapshot {
  if (!isRecord(value)
    || typeof value.open !== "boolean"
    || (value.workspace_path !== null && typeof value.workspace_path !== "string")
    || (value.state_path !== null && typeof value.state_path !== "string")
    || typeof value.profile_id !== "string"
    || typeof value.native_runtime_available !== "boolean") {
    throw new Error("Desktop service returned an invalid workspace snapshot");
  }
  return value as unknown as WorkspaceSnapshot;
}

export function parseSourceSnapshot(value: unknown): SourceSnapshot {
  if (!isRecord(value) || !isRecord(value.snapshot)) {
    throw new Error("Desktop service returned an invalid source snapshot");
  }
  const snapshot = value.snapshot;
  const identity = snapshot.identity;
  if (typeof snapshot.revision !== "string"
    || typeof snapshot.created_at_ms !== "number"
    || !isRecord(identity)
    || typeof identity.project_id !== "string"
    || typeof identity.root !== "string"
    || typeof identity.vcs !== "string"
    || !isNullableString(identity.repository_root)
    || !isNullableString(identity.common_git_dir)
    || typeof identity.checkout_id !== "string"
    || !isNullableString(identity.branch)
    || !isNullableString(identity.head)
    || typeof identity.dirty !== "boolean"
    || !isStringArray(identity.dirty_paths)
    || !Array.isArray(snapshot.files)
    || !Array.isArray(snapshot.lineage)
    || !isStringArray(snapshot.stale_paths)
    || !isStringArray(snapshot.deleted_paths)
    || typeof snapshot.overflowed !== "boolean") {
    throw new Error("Desktop service returned an invalid source snapshot");
  }
  if (!snapshot.files.every((file) => isRecord(file)
    && typeof file.relative_path === "string"
    && typeof file.language === "string"
    && typeof file.size_bytes === "number"
    && typeof file.modified_ns === "number"
    && (file.content_hash === null || typeof file.content_hash === "string")
    && typeof file.extraction_status === "string"
    && Array.isArray(file.symbols)
    && file.symbols.every((symbol) => isRecord(symbol)
      && typeof symbol.name === "string"
      && typeof symbol.kind === "string"
      && typeof symbol.line === "number"
      && typeof symbol.signature_hash === "string")
    && isStringArray(file.imports)
    && isNullableString(file.error)
    && isNullableString(file.symlink_target))) {
    throw new Error("Desktop service returned an invalid source file record");
  }
  if (!snapshot.lineage.every((item) => isRecord(item)
    && typeof item.source_path === "string"
    && typeof item.symbol === "string"
    && isStringArray(item.candidate_paths)
    && typeof item.reason === "string"
    && typeof item.certainty === "string")) {
    throw new Error("Desktop service returned an invalid source lineage record");
  }
  return snapshot as unknown as SourceSnapshot;
}

export function parseConversationSnapshot(value: unknown): ConversationSnapshot {
  if (isRecord(value) && isRecord(value.snapshot)) {
    return parseConversationSnapshot(value.snapshot);
  }
  if (!isRecord(value) || !isRecord(value.conversation) || !Array.isArray(value.turns)) {
    throw new Error("Desktop service returned an invalid conversation snapshot");
  }
  const conversation = value.conversation;
  if (typeof conversation.conversation_id !== "string"
    || typeof conversation.title !== "string"
    || typeof conversation.status !== "string"
    || typeof conversation.revision !== "number") {
    throw new Error("Desktop service returned an invalid conversation record");
  }
  if (!value.turns.every((turn) => isRecord(turn)
    && typeof turn.turn_id === "string"
    && typeof turn.role === "string"
    && typeof turn.status === "string"
    && typeof turn.content === "string"
    && typeof turn.revision === "number")) {
    throw new Error("Desktop service returned an invalid conversation turn");
  }
  return value as unknown as ConversationSnapshot;
}

export function parseMemorySearchResult(value: unknown): MemoryRecord[] {
  if (!isRecord(value) || !Array.isArray(value.records)) {
    throw new Error("Desktop service returned an invalid memory search result");
  }
  if (!value.records.every(isMemoryRecord)) {
    throw new Error("Desktop service returned an invalid memory record");
  }
  return value.records as MemoryRecord[];
}

export function parseEmptyResult(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error("Desktop service returned an invalid command result");
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isMemoryRecord(value: unknown): value is MemoryRecord {
  if (!isRecord(value)) return false;
  return typeof value.memory_id === "string"
    && typeof value.owner_id === "string"
    && typeof value.scope_kind === "string"
    && typeof value.lifecycle === "string"
    && typeof value.validation === "string"
    && typeof value.revision === "number"
    && typeof value.observed_at_ms === "number"
    && isNullableString(value.content_hash)
    && isNullableString(value.content)
    && (value.validation_basis === undefined || isNullableString(value.validation_basis))
    && (value.validation_reason === undefined || isNullableString(value.validation_reason))
    && typeof value.memory_kind === "string";
}

function isDesktopResponse<T>(value: unknown): value is DesktopResponse<T> {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return candidate.schema === RESPONSE_SCHEMA
    && candidate.protocol_version === PROTOCOL_VERSION
    && typeof candidate.request_id === "string"
    && (candidate.status === "ok" || candidate.status === "error");
}
