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
  | "conversations.list"
  | "conversations.read"
  | "conversations.send"
  | "conversations.switch_model"
  | "subagents.run"
  | "subagents.events"
  | "memory.search"
  | "memory.inspect"
  | "memory.capture"
  | "memory.correct"
  | "memory.forget"
  | "memory.restore"
  | "memory.purge"
  | "verification.inspect_project"
  | "verification.create_contract"
  | "verification.start_session"
  | "verification.get_agent_packet"
  | "verification.observe_change"
  | "verification.get_feedback"
  | "verification.propose_test_change"
  | "verification.evaluate_test_change"
  | "verification.apply_test_change"
  | "verification.request_deep_run"
  | "verification.inspect_run"
  | "verification.cancel_run"
  | "verification.resume_session"
  | "verification.read_report"
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

export type ConnectionRecord = {
  connection_id: string;
  provider_kind: string;
  endpoint: string;
  protocol: string;
  secret_ref: string | null;
  enabled: boolean;
  revision: number;
  updated_at_ms: number;
};

export type ModelDescriptor = {
  connection_id: string;
  model_id: string;
  family: string | null;
  capabilities: string[];
  context_limit: number | null;
  output_limit: number | null;
  source: string;
  revision: number;
  observed_at_ms: number;
};

export type ConversationTurn = {
  turn_id: string;
  role: string;
  status: string;
  content: string;
  revision: number;
};

export type ConversationExecution = {
  execution_id: string;
  conversation_id: string;
  turn_id: string;
  provider_kind: string;
  connection_id: string;
  model_id: string;
  status: string;
  checkpoint_seq: number;
  revision: number;
  started_at_ms: number;
  finished_at_ms: number | null;
};

export type ConversationCheckpoint = {
  execution_id: string;
  sequence: number;
  state: string;
  continuation_json: string;
  continuation_hash: string;
  created_at_ms: number;
};

export type ConversationToolCall = {
  call_id: string;
  conversation_id: string;
  request_turn_id: string;
  result_turn_id: string | null;
  tool_name: string;
  arguments_json: string;
  result_content: string | null;
  status: string;
  revision: number;
  created_at_ms: number;
  completed_at_ms: number | null;
};

export type ConversationPart = {
  conversation_id: string;
  turn_id: string;
  part_index: number;
  kind: string;
  content: string;
};

export type ConversationSnapshot = {
  conversation: Conversation;
  turns: ConversationTurn[];
  executions: ConversationExecution[];
  checkpoints: ConversationCheckpoint[];
  tool_calls: ConversationToolCall[];
  parts: ConversationPart[];
};

export type SubagentResultPacket = {
  task_id: number;
  status: string;
  summary: string;
  summary_truncated: boolean;
  claims: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  uncertainty: string[];
  blockers: string[];
  tokens_in: number;
  tokens_out: number;
  packet_hash: string;
};

export type SubagentRunResult = {
  schema: "aegis-desktop-subagents-result-v1";
  run_id: string;
  graph_hash: string;
  graph_authority: string;
  status: string;
  root_output: string;
  root_output_truncated: boolean;
  child_results: SubagentResultPacket[];
  failed_task_ids: number[];
  blocked_task_ids: number[];
  coordination_hash: string;
  event_cursor?: number;
};

export type SubagentEvent = {
  cursor: number;
  message_kind: string;
  run_id: string;
  sender_id: string;
  recipient_id: string;
  task_id: number;
  parent_task_id: number | null;
  attempt_id: number;
  message_hash: string;
  artifact_refs: Array<Record<string, unknown>>;
  payload: Record<string, unknown>;
};

export type SubagentEventPage = {
  schema: "aegis-desktop-subagent-events-v1";
  run_id: string;
  latest_cursor: number;
  oldest_cursor: number;
  resync_required: boolean;
  events: SubagentEvent[];
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

export type VerificationSession = {
  session_id: string;
  project_id: string;
  baseline_revision: string;
  current_revision: string;
  requirement_ids: string[];
  plan_id: string;
  state: string;
  event_cursor: number;
};

export type VerificationAgentPacket = {
  session_id: string;
  project_profile: Record<string, unknown>;
  requirements: Array<Record<string, unknown>>;
  plan: Record<string, unknown>;
  known_risks: string[];
  rules: string[];
  feedback_cursor: number;
  source_revision: string;
  can_start: boolean;
  authority_state: "SHADOW_ONLY";
};

export type VerificationReport = {
  session: Record<string, unknown>;
  plan: Record<string, unknown>;
  assessment: Record<string, unknown>;
  events: Array<Record<string, unknown>>;
  execution: "NOT_EXECUTED";
  final_assurance: false;
  promotion: "DISABLED";
};

export function parseVerificationPacket(value: unknown): VerificationAgentPacket {
  if (!isRecord(value)
    || typeof value.session_id !== "string"
    || !isRecord(value.project_profile)
    || !Array.isArray(value.requirements)
    || !value.requirements.every(isRecord)
    || !isRecord(value.plan)
    || !isStringArray(value.known_risks)
    || !isStringArray(value.rules)
    || typeof value.feedback_cursor !== "number"
    || typeof value.source_revision !== "string"
    || typeof value.can_start !== "boolean"
    || value.authority_state !== "SHADOW_ONLY") {
    throw new Error("Desktop service returned an invalid AESE packet");
  }
  return value as unknown as VerificationAgentPacket;
}

export function parseVerificationReport(value: unknown): VerificationReport {
  if (!isRecord(value)
    || !isRecord(value.session)
    || !isRecord(value.plan)
    || !isRecord(value.assessment)
    || !Array.isArray(value.events)
    || !value.events.every(isRecord)
    || value.execution !== "NOT_EXECUTED"
    || value.final_assurance !== false
    || value.promotion !== "DISABLED") {
    throw new Error("Desktop service returned an invalid AESE report");
  }
  return value as unknown as VerificationReport;
}

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
  const conversation = parseConversationRecord(value.conversation);
  if (!value.turns.every((turn) => isRecord(turn)
    && typeof turn.turn_id === "string"
    && typeof turn.role === "string"
    && typeof turn.status === "string"
    && typeof turn.content === "string"
    && typeof turn.revision === "number")) {
    throw new Error("Desktop service returned an invalid conversation turn");
  }
  const executions = value.executions === undefined ? [] : value.executions;
  const checkpoints = value.checkpoints === undefined ? [] : value.checkpoints;
  const toolCalls = value.tool_calls === undefined ? [] : value.tool_calls;
  const parts = value.parts === undefined ? [] : value.parts;
  if (!Array.isArray(executions) || !executions.every((execution) => isRecord(execution)
    && typeof execution.execution_id === "string"
    && typeof execution.conversation_id === "string"
    && typeof execution.turn_id === "string"
    && typeof execution.provider_kind === "string"
    && typeof execution.connection_id === "string"
    && typeof execution.model_id === "string"
    && typeof execution.status === "string"
    && typeof execution.checkpoint_seq === "number"
    && typeof execution.revision === "number"
    && typeof execution.started_at_ms === "number"
    && (execution.finished_at_ms === null || typeof execution.finished_at_ms === "number"))) {
    throw new Error("Desktop service returned an invalid conversation execution");
  }
  if (!Array.isArray(checkpoints) || !checkpoints.every((checkpoint) => isRecord(checkpoint)
    && typeof checkpoint.execution_id === "string"
    && typeof checkpoint.sequence === "number"
    && typeof checkpoint.state === "string"
    && typeof checkpoint.continuation_json === "string"
    && typeof checkpoint.continuation_hash === "string"
    && typeof checkpoint.created_at_ms === "number")) {
    throw new Error("Desktop service returned an invalid conversation checkpoint");
  }
  if (!Array.isArray(toolCalls) || !toolCalls.every((toolCall) => isRecord(toolCall)
    && typeof toolCall.call_id === "string"
    && typeof toolCall.conversation_id === "string"
    && typeof toolCall.request_turn_id === "string"
    && (toolCall.result_turn_id === null || typeof toolCall.result_turn_id === "string")
    && typeof toolCall.tool_name === "string"
    && typeof toolCall.arguments_json === "string"
    && (toolCall.result_content === null || typeof toolCall.result_content === "string")
    && typeof toolCall.status === "string"
    && typeof toolCall.revision === "number"
    && typeof toolCall.created_at_ms === "number"
    && (toolCall.completed_at_ms === null || typeof toolCall.completed_at_ms === "number"))) {
    throw new Error("Desktop service returned an invalid conversation tool call");
  }
  if (!Array.isArray(parts) || !parts.every((part) => isRecord(part)
    && typeof part.conversation_id === "string"
    && typeof part.turn_id === "string"
    && typeof part.part_index === "number"
    && typeof part.kind === "string"
    && typeof part.content === "string")) {
    throw new Error("Desktop service returned an invalid conversation part");
  }
  return {
    conversation,
    turns: value.turns as unknown as ConversationTurn[],
    executions: executions as unknown as ConversationExecution[],
    checkpoints: checkpoints as unknown as ConversationCheckpoint[],
    tool_calls: toolCalls as unknown as ConversationToolCall[],
    parts: parts as unknown as ConversationPart[],
  };
}

export function parseConversationList(value: unknown): Conversation[] {
  if (!isRecord(value) || !Array.isArray(value.records) || !value.records.every(isRecord)) {
    throw new Error("Desktop service returned an invalid conversation list");
  }
  return value.records.map(parseConversationRecord);
}

export function parseConnectionList(value: unknown): ConnectionRecord[] {
  if (!isRecord(value) || !Array.isArray(value.records) || !value.records.every(isRecord)) {
    throw new Error("Desktop service returned an invalid connection list");
  }
  return value.records.map(parseConnectionRecord);
}

export function parseModelList(value: unknown): ModelDescriptor[] {
  if (!isRecord(value) || !Array.isArray(value.models) || !value.models.every(isRecord)) {
    throw new Error("Desktop service returned an invalid model list");
  }
  return value.models.map(parseModelDescriptor);
}

export function parseConversationRecordResult(value: unknown): Conversation {
  if (isRecord(value) && isRecord(value.record)) return parseConversationRecord(value.record);
  return parseConversationRecord(value);
}

export function parseSubagentRunResult(value: unknown): SubagentRunResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-subagents-result-v1"
    || typeof value.run_id !== "string"
    || typeof value.graph_hash !== "string"
    || typeof value.graph_authority !== "string"
    || typeof value.status !== "string"
    || typeof value.root_output !== "string"
    || typeof value.root_output_truncated !== "boolean"
    || !Array.isArray(value.child_results)
    || !Array.isArray(value.failed_task_ids)
    || !Array.isArray(value.blocked_task_ids)
    || !isNumberArray(value.failed_task_ids)
    || !isNumberArray(value.blocked_task_ids)
    || typeof value.coordination_hash !== "string") {
    throw new Error("Desktop service returned an invalid subagent result");
  }
  const childResults = value.child_results;
  if (!childResults.every((packet) => isRecord(packet)
    && typeof packet.task_id === "number"
    && typeof packet.status === "string"
    && typeof packet.summary === "string"
    && typeof packet.summary_truncated === "boolean"
    && Array.isArray(packet.claims)
    && packet.claims.every(isRecord)
    && Array.isArray(packet.artifacts)
    && packet.artifacts.every(isRecord)
    && isStringArray(packet.uncertainty)
    && isStringArray(packet.blockers)
    && typeof packet.tokens_in === "number"
    && typeof packet.tokens_out === "number"
    && typeof packet.packet_hash === "string")) {
    throw new Error("Desktop service returned an invalid subagent result packet");
  }
  return {
    schema: "aegis-desktop-subagents-result-v1",
    run_id: value.run_id,
    graph_hash: value.graph_hash,
    graph_authority: value.graph_authority,
    status: value.status,
    root_output: value.root_output,
    root_output_truncated: value.root_output_truncated,
    child_results: childResults as unknown as SubagentResultPacket[],
    failed_task_ids: value.failed_task_ids as number[],
    blocked_task_ids: value.blocked_task_ids as number[],
    coordination_hash: value.coordination_hash,
    ...(typeof value.event_cursor === "number" ? { event_cursor: value.event_cursor } : {}),
  };
}

export function parseSubagentEventPage(value: unknown): SubagentEventPage {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-subagent-events-v1"
    || typeof value.run_id !== "string"
    || typeof value.latest_cursor !== "number"
    || typeof value.oldest_cursor !== "number"
    || typeof value.resync_required !== "boolean"
    || !Array.isArray(value.events)) {
    throw new Error("Desktop service returned an invalid subagent event page");
  }
  if (!value.events.every((event) => isRecord(event)
    && typeof event.cursor === "number"
    && typeof event.message_kind === "string"
    && typeof event.run_id === "string"
    && typeof event.sender_id === "string"
    && typeof event.recipient_id === "string"
    && typeof event.task_id === "number"
    && (event.parent_task_id === null || typeof event.parent_task_id === "number")
    && typeof event.attempt_id === "number"
    && typeof event.message_hash === "string"
    && Array.isArray(event.artifact_refs)
    && event.artifact_refs.every(isRecord)
    && isRecord(event.payload))) {
    throw new Error("Desktop service returned an invalid subagent event");
  }
  return {
    schema: "aegis-desktop-subagent-events-v1",
    run_id: value.run_id,
    latest_cursor: value.latest_cursor,
    oldest_cursor: value.oldest_cursor,
    resync_required: value.resync_required,
    events: value.events as unknown as SubagentEvent[],
  };
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

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => typeof item === "number" && Number.isSafeInteger(item));
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

function parseConversationRecord(value: unknown): Conversation {
  if (!isRecord(value)
    || typeof value.conversation_id !== "string"
    || typeof value.owner_id !== "string"
    || typeof value.title !== "string"
    || typeof value.connection_id !== "string"
    || typeof value.model_id !== "string"
    || typeof value.status !== "string"
    || typeof value.revision !== "number"
    || typeof value.created_at_ms !== "number"
    || typeof value.updated_at_ms !== "number") {
    throw new Error("Desktop service returned an invalid conversation record");
  }
  return value as unknown as Conversation;
}

function parseConnectionRecord(value: unknown): ConnectionRecord {
  if (!isRecord(value)
    || typeof value.connection_id !== "string"
    || typeof value.provider_kind !== "string"
    || typeof value.endpoint !== "string"
    || typeof value.protocol !== "string"
    || !isNullableString(value.secret_ref)
    || typeof value.enabled !== "boolean"
    || typeof value.revision !== "number"
    || typeof value.updated_at_ms !== "number") {
    throw new Error("Desktop service returned an invalid connection record");
  }
  return value as unknown as ConnectionRecord;
}

function parseModelDescriptor(value: unknown): ModelDescriptor {
  if (!isRecord(value)
    || typeof value.connection_id !== "string"
    || typeof value.model_id !== "string"
    || !isNullableString(value.family)
    || !isStringArray(value.capabilities)
    || (value.context_limit !== null && typeof value.context_limit !== "number")
    || (value.output_limit !== null && typeof value.output_limit !== "number")
    || typeof value.source !== "string"
    || typeof value.revision !== "number"
    || typeof value.observed_at_ms !== "number") {
    throw new Error("Desktop service returned an invalid model descriptor");
  }
  return value as unknown as ModelDescriptor;
}

function isDesktopResponse<T>(value: unknown): value is DesktopResponse<T> {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return candidate.schema === RESPONSE_SCHEMA
    && candidate.protocol_version === PROTOCOL_VERSION
    && typeof candidate.request_id === "string"
    && (candidate.status === "ok" || candidate.status === "error");
}
