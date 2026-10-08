import { invoke } from "@tauri-apps/api/core";
import { previewRequest } from "./preview_backend";

export const REQUEST_SCHEMA = "aegis-desktop-command-v1" as const;
export const RESPONSE_SCHEMA = "aegis-desktop-response-v1" as const;
export const PROTOCOL_VERSION = 1 as const;

export type DesktopCommand =
  | "workspace.open"
  | "workspace.switch"
  | "workspace.clone"
  | "workspace.snapshot"
  | "workspace.source_snapshot"
  | "workspace.file_read"
  | "workspace.changes"
  | "workspace.open_external"
  | "projects.list"
  | "projects.remove"
  | "settings.get"
  | "settings.update"
  | "connections.list"
  | "connections.identify"
  | "connections.connect"
  | "connections.save"
  | "connections.discover"
  | "connections.disable"
  | "models.list"
  | "extensions.discover"
  | "extensions.import_skill"
  | "extensions.import_pack"
  | "extensions.load_skill"
  | "extensions.mcp_preview"
  | "extensions.mcp_registry_search"
  | "extensions.add_mcp_metadata"
  | "extensions.state"
  | "extensions.set_state"
  | "extensions.test_mcp"
  | "extensions.activate_mcp"
  | "extensions.deactivate_mcp"
  | "extensions.mcp_prompts_list"
  | "extensions.mcp_prompts_get"
  | "extensions.mcp_skills_list"
  | "extensions.mcp_skill_set_state"
  | "conversations.create"
  | "conversations.list"
  | "conversations.read"
  | "conversations.inspect"
  | "conversations.send"
  | "runs.start"
  | "runs.inspect"
  | "runs.cancel"
  | "approvals.resolve"
  | "tool_calls.reconcile"
  | "conversations.switch_model"
  | "subagents.run"
  | "subagents.start"
  | "subagents.cancel"
  | "subagents.status"
  | "subagents.events"
  | "subagents.graph"
  | "code_reuse.assess"
  | "code_reuse.materialize"
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
  preview_mode?: boolean;
};

export type WorkspaceCloneResult = {
  workspace_path: string;
  name: string;
};

export type ProjectRecord = {
  project_id: string;
  path: string;
  name: string;
  parent_path: string;
  last_opened_at_ms: number;
  open_count: number;
  status: "AVAILABLE" | "MISSING";
};

export type ProjectListResult = {
  schema: "aegis-desktop-projects-v1";
  projects: ProjectRecord[];
  active_project_id: string | null;
};

export type ProjectRemoveResult = {
  schema: "aegis-desktop-project-remove-v1";
  project_id: string;
  removed: boolean;
};

export type DesktopSettings = {
  theme: "system" | "dark" | "light";
  font_size_preset: "Tall" | "Grande" | "Venti" | "Trenta";
  proxy_mode: "System" | "Direct" | "Custom";
  permission_mode: "Ask every time" | "Accept edits" | "Auto";
  agent_default_mode: "Agent" | "Plan" | "Goal";
  voice_enabled: boolean;
  thinking_default: string;
  context_usage: "Remaining" | "Used";
  enter_to_send: boolean;
  instructions: string;
  parallel_workers_enabled: boolean;
};

export type DesktopSettingsResult = {
  schema: "aegis-desktop-settings-v1";
  revision: number;
  settings: DesktopSettings;
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

export type ProviderChoice = {
  provider_kind: string;
  provider_label: string;
  endpoint: string | null;
  protocol: string;
  requires_endpoint: boolean;
};

export type ConnectionListResult = {
  records: ConnectionRecord[];
  providers: ProviderChoice[];
};

export type ProviderIdentity = {
  provider_kind: string;
  provider_label: string;
  endpoint: string | null;
  protocol: string;
  confidence: string;
  hints: string[];
  requires_endpoint: boolean;
  requires_confirmation: boolean;
};

export type ConnectionConnectResult = {
  identity: ProviderIdentity;
  record: ConnectionRecord;
  models: ModelDescriptor[];
  secret_scope: "session";
  persistent: false;
  discovery_error: string | null;
  model_catalog_state: ModelCatalogState;
};

export type ModelCatalogState = "FRESH" | "CACHED" | "STALE" | "UNAVAILABLE";

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
  reasoning_efforts: string[];
  supports_vision: boolean;
  input_modalities: string[];
  max_image_inputs: number | null;
};

export type ExtensionManifestSummary = {
  schema: string;
  extension_id: string;
  version: string;
  description: string;
  capabilities: string[];
  tool_names: string[];
  skill_names: string[];
  source: string;
  trusted: boolean;
  manifest_hash: string;
  wasm_plugin?: boolean;
  descriptor_hash?: string;
  module_sha256?: string | null;
};

export type SkillDescriptorSummary = {
  schema: string;
  name: string;
  description: string;
  version: string;
  content_hash: string;
  keywords: string[];
  source: string;
};

export type McpServerSummary = {
  schema: string;
  server_id: string;
  description: string;
  transport: string;
  source: string;
  approved: boolean;
  descriptor_hash: string;
  environment_variables: McpRegistryEnvironmentVariable[];
};

export type ExtensionCatalogResult = {
  schema: "aegis-desktop-extension-catalog-v1";
  workspace_path: string;
  extensions: ExtensionManifestSummary[];
  skills: SkillDescriptorSummary[];
  selected_skills: SkillDescriptorSummary[];
  mcp_servers: McpServerSummary[];
  activation: {
    extensions: string;
    skills: string;
    mcp: string;
  };
};

export type SkillLoadResult = {
  schema: "aegis-desktop-skill-v1";
  skill: SkillDescriptorSummary;
  body: string;
};

export type McpCatalogResult = {
  schema: "aegis-desktop-mcp-catalog-v1";
  servers: McpServerSummary[];
  activation: string;
};

export type McpRegistryPackage = {
  registry_type: string;
  identifier: string;
  version: string;
  runtime_hint: string | null;
  transport: string;
  required_environment: string[];
  environment_variables: McpRegistryEnvironmentVariable[];
};

export type McpRegistryEnvironmentVariable = {
  name: string;
  is_required: boolean;
  is_secret: boolean;
  description: string | null;
};

export type McpRegistryRemote = {
  transport: string;
  endpoint: string | null;
  requires_headers: boolean;
};

export type McpRegistryServer = {
  name: string;
  title: string | null;
  version: string;
  description: string;
  packages: McpRegistryPackage[];
  remotes: McpRegistryRemote[];
};

export type McpRegistrySearchResult = {
  schema: "aegis-desktop-mcp-registry-search-v1";
  query: string;
  servers: McpRegistryServer[];
  next_cursor: string | null;
};

export type McpMetadataResult = {
  schema: "aegis-desktop-mcp-metadata-v1";
  server_id: string;
  path: string;
  transport: string;
  activated: false;
};

export type CapabilityStateRecord = {
  kind: "skill" | "extension" | "mcp";
  id: string;
  descriptor_hash: string;
  enabled: boolean;
  approved: boolean;
  updated_at_ms: number;
  source_server_id?: string;
  resource_uri?: string;
  skill_name?: string;
  skill_description?: string;
};

export type CapabilityStateResult = {
  schema: "aegis-capability-state-v1";
  revision: number;
  records: CapabilityStateRecord[];
};

export type McpActivationResult = {
  schema: "aegis-desktop-mcp-activation-v1";
  server_id: string;
  lifecycle: "ACTIVE" | "INACTIVE";
  tools: Array<{ name: string; description: string; effect_class: string; extension_id: string; descriptor_hash: string }>;
};

export type McpTestResult = {
  schema: "aegis-desktop-mcp-test-v2";
  server_id: string;
  test_token: string;
  tools: Array<{
    name: string;
    description: string;
    effect_class: string;
    capabilities: string[];
    extension_id: string;
    descriptor_hash: string;
    mcp_descriptor_hash: string;
    read_only_candidate: boolean;
    open_world_hint: boolean;
    host_managed_read_only: boolean;
  }>;
  tools_total: number;
  tools_truncated: boolean;
  connected: true;
  left_running: false;
  tools_executed: false;
};

export type McpPromptArgument = {
  name: string;
  description: string;
  required: boolean;
};

export type McpPromptDescriptor = {
  name: string;
  title: string;
  description: string;
  arguments: McpPromptArgument[];
};

export type McpPromptsListResult = {
  schema: "aegis-desktop-mcp-prompts-v1";
  server_id: string;
  prompts: McpPromptDescriptor[];
};

export type McpPromptResult = {
  schema: "aegis-desktop-mcp-prompt-result-v1";
  server_id: string;
  name: string;
  description: string;
  messages: Array<{ role: "user" | "assistant"; text: string }>;
  trust_notice: string;
};

export type McpSkillSummary = {
  id: string;
  server_id: string;
  uri: string;
  name: string;
  description: string;
  manifest_hash: string | null;
  resource_count: number | null;
  dynamic: boolean;
  approval_supported: boolean;
  approved: boolean;
  enabled: boolean;
};

export type McpSkillsListResult = {
  schema: "aegis-desktop-mcp-skills-v1";
  server_id: string;
  supports_skills: boolean;
  skills: McpSkillSummary[];
  next_cursor: string | null;
  ttl_ms: number;
  state: CapabilityStateResult;
  trust_notice: string;
};

export type McpSkillStateResult = {
  schema: "aegis-desktop-mcp-skill-state-v1";
  server_id: string;
  id: string;
  manifest_hash: string;
  enabled: boolean;
  approved: boolean;
  state: CapabilityStateResult;
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

export type PromptCacheUsage = {
  provider_input_tokens: number;
  cached_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  cache_status: "HIT" | "WRITE" | "UNKNOWN";
};

export type ConversationSendResult = {
  snapshot: ConversationSnapshot;
  prompt_cache_usage: PromptCacheUsage | null;
};

export type DesktopRunStatus = {
  schema: "aegis-desktop-run-status-v1";
  run_id: string;
  conversation_id: string;
  status: "RUNNING" | "CANCELLING" | "WAITING_PERMISSION" | "COMPLETED" | "FAILED" | "CANCELLED" | "INTERRUPTED";
  started_at_ms: number;
  finished_at_ms: number | null;
  cancel_requested_at_ms: number | null;
  cancel_supported: boolean;
  thread_alive: boolean;
  error_code: string | null;
};

export type WorkspaceFileResult = {
  schema: "aegis-desktop-file-v1";
  relative_path: string;
  content: string;
  size_bytes: number;
  sha256: string;
  modified_at_ms: number;
  source?: "WORKSPACE" | "PREVIEW_ONLY";
};

export type WorkspaceChangesResult = {
  schema: "aegis-desktop-changes-v1";
  source: string;
  entries: Array<{ relative_path: string }>;
  truncated: boolean;
};

export type WorkspaceDiffResult = {
  schema: "aegis-desktop-change-diff-v1";
  relative_path: string;
  diff: string;
  truncated: boolean;
  source: string;
};

export type WorkspaceOpenExternalResult = {
  schema: "aegis-desktop-open-external-v1";
  relative_path: string;
  requested: boolean;
};

export type ApprovalResolutionResult = {
  snapshot: ConversationSnapshot;
};

export type ToolCallReconciliationResult = {
  call_id: string;
  status: "CANCELLED";
  conversation_revision: number;
};

export type DesktopTransport = (
  command: DesktopCommand,
  payload: Record<string, unknown>,
) => Promise<unknown>;

let desktopTransportOverride: DesktopTransport | null = null;

/** Install a deterministic host for renderer harnesses; restore the previous host on cleanup. */
export function installDesktopTransport(transport: DesktopTransport | null): () => void {
  const previous = desktopTransportOverride;
  desktopTransportOverride = transport;
  return () => {
    desktopTransportOverride = previous;
  };
}

export type ConversationInspection = {
  schema: "aegis-desktop-conversation-inspection-v1";
  conversation_id: string;
  revision: number;
  context: {
    schema: "aegis-desktop-context-inspection-v1";
    status: string;
    source_revision: string | null;
    context_manifest_hash: string | null;
    prompt_hash: string | null;
    token_budget: number | null;
    token_count: number | null;
    item_count: number | null;
    selection_backend: string | null;
    history_turn_count: number;
    sensitive_content: "REDACTED";
  };
  timeline: Array<{
    event_id: string;
    kind: string;
    status: string;
    title: string;
    detail: string;
    timestamp_ms: number;
    reference: string;
  }>;
  approvals: Array<{
    approval_id: string;
    tool_name: string;
    status: string;
    risk: string;
    argument_keys: string[];
    argument_preview: Array<{ key: string; value: string }>;
    arguments: "REDACTED";
    result: "REDACTED";
    can_resolve: boolean;
    can_reconcile: boolean;
    effect_class: string;
  }>;
  redaction: string;
};

export type SubagentGraph = {
  schema: "aegis-desktop-subagent-graph-v1";
  run_id: string;
  nodes: Array<{
    task_id: number;
    parent_task_id: number | null;
    status: string;
    role: string;
    dependencies: number[];
    capabilities: string[];
    side_effect_class: string;
    summary: string;
    uncertainty: string[];
    blockers: string[];
    redacted: true;
  }>;
  edges: Array<{ from: number; to: number }>;
  redaction: string;
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

export type SubagentStartResult = {
  schema: "aegis-desktop-subagents-start-v1";
  run_id: string;
  status: "RUNNING";
  event_cursor: number;
};

export type SubagentStatusResult = {
  schema: "aegis-desktop-subagents-status-v1";
  run_id: string;
  status: string;
  event_cursor: number;
  started_at_ms: number;
  finished_at_ms: number | null;
  cancel_requested_at_ms: number | null;
  cancel_supported: boolean;
  thread_alive: boolean;
  result: SubagentRunResult | null;
  error: { code: string; message: string } | null;
};

export type SubagentCancelResult = {
  schema: "aegis-desktop-subagents-cancel-v1";
  run_id: string;
  status: string;
  event_cursor: number;
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
  if (desktopTransportOverride) {
    return decode(await desktopTransportOverride(command, payload));
  }
  const request = createRequest(command, payload);
  const nativeRuntime = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const response: unknown = nativeRuntime
    ? JSON.parse(await invoke<string>("desktop_request", { frame: JSON.stringify(request) }))
    : {
        schema: RESPONSE_SCHEMA,
        protocol_version: PROTOCOL_VERSION,
        request_id: request.request_id,
        status: "ok",
        result: await previewRequest(command, payload),
      };
  if (!isDesktopResponse<T>(response)) {
    throw new Error("Desktop service returned an invalid response");
  }
  if (response.status === "error") {
    throw new Error(`${response.error.code}: ${response.error.message}`);
  }
  return decode(response.result);
}

export function parseDesktopRunStatus(value: unknown): DesktopRunStatus {
  const statuses = new Set([
    "RUNNING",
    "CANCELLING",
    "WAITING_PERMISSION",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
  ]);
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-run-status-v1"
    || typeof value.run_id !== "string"
    || typeof value.conversation_id !== "string"
    || typeof value.status !== "string"
    || !statuses.has(value.status)
    || typeof value.started_at_ms !== "number"
    || (value.finished_at_ms !== null && typeof value.finished_at_ms !== "number")
    || (value.cancel_requested_at_ms !== null && typeof value.cancel_requested_at_ms !== "number")
    || typeof value.cancel_supported !== "boolean"
    || typeof value.thread_alive !== "boolean"
    || (value.error_code !== null && typeof value.error_code !== "string")) {
    throw new Error("Desktop service returned an invalid task status");
  }
  return value as unknown as DesktopRunStatus;
}

export function parseWorkspaceSnapshot(value: unknown): WorkspaceSnapshot {
  if (!isRecord(value)
    || typeof value.open !== "boolean"
    || (value.workspace_path !== null && typeof value.workspace_path !== "string")
    || (value.state_path !== null && typeof value.state_path !== "string")
    || typeof value.profile_id !== "string"
    || typeof value.native_runtime_available !== "boolean"
    || (value.preview_mode !== undefined && typeof value.preview_mode !== "boolean")) {
    throw new Error("Desktop service returned an invalid workspace snapshot");
  }
  return value as unknown as WorkspaceSnapshot;
}

export function parseWorkspaceCloneResult(value: unknown): WorkspaceCloneResult {
  if (!isRecord(value) || typeof value.workspace_path !== "string" || !value.workspace_path || typeof value.name !== "string" || !value.name) {
    throw new Error("Desktop service returned an invalid workspace clone result");
  }
  return value as unknown as WorkspaceCloneResult;
}

export function parseProjectList(value: unknown): ProjectListResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-projects-v1"
    || !Array.isArray(value.projects)
    || !value.projects.every(isRecord)
    || !isNullableString(value.active_project_id)) {
    throw new Error("Desktop service returned an invalid project list");
  }
  const projects = value.projects.map((project) => {
    if (typeof project.project_id !== "string"
      || typeof project.path !== "string"
      || typeof project.name !== "string"
      || typeof project.parent_path !== "string"
      || typeof project.last_opened_at_ms !== "number"
      || typeof project.open_count !== "number"
      || (project.status !== "AVAILABLE" && project.status !== "MISSING")) {
      throw new Error("Desktop service returned an invalid project record");
    }
    return project as unknown as ProjectRecord;
  });
  return {
    schema: "aegis-desktop-projects-v1",
    projects,
    active_project_id: value.active_project_id,
  };
}

export function parseProjectRemove(value: unknown): ProjectRemoveResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-project-remove-v1"
    || typeof value.project_id !== "string"
    || typeof value.removed !== "boolean") {
    throw new Error("Desktop service returned an invalid project removal result");
  }
  return value as unknown as ProjectRemoveResult;
}

export function parseSettingsResult(value: unknown): DesktopSettingsResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-settings-v1"
    || typeof value.revision !== "number"
    || !Number.isInteger(value.revision)
    || value.revision < 0
    || !isRecord(value.settings)) {
    throw new Error("Desktop service returned invalid settings");
  }
  const settings = value.settings;
  if ((settings.theme !== "system" && settings.theme !== "dark" && settings.theme !== "light")
    || !(["Tall", "Grande", "Venti", "Trenta"] as const).includes(settings.font_size_preset as never)
    || !(["System", "Direct", "Custom"] as const).includes(settings.proxy_mode as never)
    || !(["Ask every time", "Accept edits", "Auto"] as const).includes(settings.permission_mode as never)
    || !(["Agent", "Plan", "Goal"] as const).includes(settings.agent_default_mode as never)
    || typeof settings.voice_enabled !== "boolean"
    || !isThinkingDefault(settings.thinking_default)
    || !(["Remaining", "Used"] as const).includes(settings.context_usage as never)
    || typeof settings.enter_to_send !== "boolean"
    || typeof settings.instructions !== "string"
    || typeof settings.parallel_workers_enabled !== "boolean") {
    throw new Error("Desktop service returned invalid settings fields");
  }
  return value as unknown as DesktopSettingsResult;
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

export function parseWorkspaceFileResult(value: unknown): WorkspaceFileResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-file-v1"
    || typeof value.relative_path !== "string"
    || typeof value.content !== "string"
    || typeof value.size_bytes !== "number"
    || typeof value.sha256 !== "string"
    || typeof value.modified_at_ms !== "number"
    || (value.source !== undefined && value.source !== "WORKSPACE" && value.source !== "PREVIEW_ONLY")) {
    throw new Error("Desktop service returned an invalid workspace file");
  }
  return value as unknown as WorkspaceFileResult;
}

export function parseWorkspaceChangesResult(value: unknown): WorkspaceChangesResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-changes-v1"
    || typeof value.source !== "string"
    || typeof value.truncated !== "boolean"
    || !Array.isArray(value.entries)
    || !value.entries.every((entry) => isRecord(entry) && typeof entry.relative_path === "string")) {
    throw new Error("Desktop service returned an invalid changes list");
  }
  return value as unknown as WorkspaceChangesResult;
}

export function parseWorkspaceDiffResult(value: unknown): WorkspaceDiffResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-change-diff-v1"
    || typeof value.relative_path !== "string"
    || typeof value.diff !== "string"
    || typeof value.truncated !== "boolean"
    || typeof value.source !== "string") {
    throw new Error("Desktop service returned an invalid workspace diff");
  }
  return value as unknown as WorkspaceDiffResult;
}

export function parseWorkspaceOpenExternalResult(value: unknown): WorkspaceOpenExternalResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-open-external-v1"
    || typeof value.relative_path !== "string"
    || typeof value.requested !== "boolean") {
    throw new Error("Desktop service returned an invalid open-file result");
  }
  return value as unknown as WorkspaceOpenExternalResult;
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

export function parseConversationSendResult(value: unknown): ConversationSendResult {
  if (!isRecord(value)) throw new Error("Desktop service returned an invalid send result");
  const snapshot = parseConversationSnapshot(value);
  const promptCache = isRecord(value.prompt_cache) ? value.prompt_cache : null;
  const rawUsage = promptCache?.usage;
  if (rawUsage === undefined) return { snapshot, prompt_cache_usage: null };
  if (!isRecord(rawUsage)
    || !isNonnegativeSafeInteger(rawUsage.provider_input_tokens)
    || !isNonnegativeSafeInteger(rawUsage.cached_read_tokens)
    || !isNonnegativeSafeInteger(rawUsage.cache_write_tokens)
    || !isNonnegativeSafeInteger(rawUsage.output_tokens)
    || (rawUsage.cache_status !== "HIT" && rawUsage.cache_status !== "WRITE" && rawUsage.cache_status !== "UNKNOWN")) {
    throw new Error("Desktop service returned invalid provider cache usage");
  }
  return {
    snapshot,
    prompt_cache_usage: {
      provider_input_tokens: rawUsage.provider_input_tokens,
      cached_read_tokens: rawUsage.cached_read_tokens,
      cache_write_tokens: rawUsage.cache_write_tokens,
      output_tokens: rawUsage.output_tokens,
      cache_status: rawUsage.cache_status,
    },
  };
}

export function parseApprovalResolutionResult(value: unknown): ApprovalResolutionResult {
  if (!isRecord(value) || value.mode !== "live" || !isRecord(value.snapshot)) {
    throw new Error("Desktop service returned an invalid approval result");
  }
  return { snapshot: parseConversationSnapshot(value.snapshot) };
}

export function parseToolCallReconciliationResult(value: unknown): ToolCallReconciliationResult {
  if (!isRecord(value)
    || typeof value.call_id !== "string"
    || value.status !== "CANCELLED"
    || typeof value.conversation_revision !== "number") {
    throw new Error("Desktop service returned an invalid tool reconciliation result");
  }
  return {
    call_id: value.call_id,
    status: "CANCELLED",
    conversation_revision: value.conversation_revision,
  };
}

export function parseConversationInspection(value: unknown): ConversationInspection {
  const inspection = isRecord(value) && isRecord(value.inspection) ? value.inspection : value;
  if (!isRecord(inspection)
    || inspection.schema !== "aegis-desktop-conversation-inspection-v1"
    || typeof inspection.conversation_id !== "string"
    || typeof inspection.revision !== "number"
    || !isRecord(inspection.context)
    || !Array.isArray(inspection.timeline)
    || !Array.isArray(inspection.approvals)
    || inspection.context.sensitive_content !== "REDACTED"
    || !inspection.timeline.every(isRecord)
    || !inspection.approvals.every((approval) => isRecord(approval)
      && typeof approval.approval_id === "string"
      && typeof approval.tool_name === "string"
      && typeof approval.status === "string"
      && typeof approval.risk === "string"
      && Array.isArray(approval.argument_keys)
      && approval.argument_keys.every((key) => typeof key === "string")
      && Array.isArray(approval.argument_preview)
      && approval.argument_preview.every((item) => isRecord(item)
        && typeof item.key === "string"
        && typeof item.value === "string")
      && approval.arguments === "REDACTED"
      && approval.result === "REDACTED"
      && typeof approval.can_resolve === "boolean"
      && typeof approval.can_reconcile === "boolean"
      && typeof approval.effect_class === "string")) {
    throw new Error("Desktop service returned an invalid conversation inspection");
  }
  return inspection as unknown as ConversationInspection;
}

export function parseConversationList(value: unknown): Conversation[] {
  if (!isRecord(value) || !Array.isArray(value.records) || !value.records.every(isRecord)) {
    throw new Error("Desktop service returned an invalid conversation list");
  }
  return value.records.map(parseConversationRecord);
}

export function parseConnectionList(value: unknown): ConnectionListResult {
  if (!isRecord(value)
    || !Array.isArray(value.records)
    || !value.records.every(isRecord)
    || !Array.isArray(value.providers)
    || !value.providers.every(isRecord)) {
    throw new Error("Desktop service returned an invalid connection list");
  }
  const providers = value.providers.map((provider) => {
    if (typeof provider.provider_kind !== "string"
      || !provider.provider_kind
      || typeof provider.provider_label !== "string"
      || !provider.provider_label
      || !isNullableString(provider.endpoint)
      || typeof provider.protocol !== "string"
      || typeof provider.requires_endpoint !== "boolean"
      || provider.requires_endpoint === (provider.endpoint !== null)) {
      throw new Error("Desktop service returned an invalid provider choice");
    }
    return provider as unknown as ProviderChoice;
  });
  if (providers.length === 0 || new Set(providers.map((provider) => provider.provider_kind)).size !== providers.length) {
    throw new Error("Desktop service returned an empty or duplicated provider list");
  }
  return { records: value.records.map(parseConnectionRecord), providers };
}

export function parseConnectionRecordResult(value: unknown): ConnectionRecord {
  if (!isRecord(value) || !isRecord(value.record)) {
    throw new Error("Desktop service returned an invalid connection result");
  }
  return parseConnectionRecord(value.record);
}

export function parseProviderIdentity(value: unknown): ProviderIdentity {
  const identity = isRecord(value) && isRecord(value.identity) ? value.identity : value;
  if (!isRecord(identity)
    || typeof identity.provider_kind !== "string"
    || typeof identity.provider_label !== "string"
    || !isNullableString(identity.endpoint)
    || typeof identity.protocol !== "string"
    || typeof identity.confidence !== "string"
    || !isStringArray(identity.hints)
    || typeof identity.requires_endpoint !== "boolean"
    || typeof identity.requires_confirmation !== "boolean") {
    throw new Error("Desktop service returned an invalid provider identity");
  }
  return identity as unknown as ProviderIdentity;
}

export function parseConnectionConnectResult(value: unknown): ConnectionConnectResult {
  if (!isRecord(value)
    || !isRecord(value.identity)
    || !isRecord(value.record)
    || !Array.isArray(value.models)
    || !value.models.every(isRecord)
    || value.secret_scope !== "session"
    || value.persistent !== false
    || !isNullableString(value.discovery_error)
    || !isModelCatalogState(value.model_catalog_state)) {
    throw new Error("Desktop service returned an invalid connection result");
  }
  return {
    identity: parseProviderIdentity(value.identity),
    record: parseConnectionRecord(value.record),
    models: value.models.map(parseModelDescriptor),
    secret_scope: "session",
    persistent: false,
    discovery_error: value.discovery_error,
    model_catalog_state: value.model_catalog_state,
  };
}

export function parseModelDiscoveryResult(value: unknown): {
  models: ModelDescriptor[];
  model_catalog_state: ModelCatalogState;
  discovery_error: string | null;
} {
  if (!isRecord(value)
    || !Array.isArray(value.models)
    || !value.models.every(isRecord)
    || !isModelCatalogState(value.model_catalog_state)
    || !isNullableString(value.discovery_error)) {
    throw new Error("Desktop service returned an invalid model discovery result");
  }
  return {
    models: value.models.map(parseModelDescriptor),
    model_catalog_state: value.model_catalog_state,
    discovery_error: value.discovery_error,
  };
}

export function parseModelList(value: unknown): ModelDescriptor[] {
  if (!isRecord(value) || !Array.isArray(value.models) || !value.models.every(isRecord)) {
    throw new Error("Desktop service returned an invalid model list");
  }
  return value.models.map(parseModelDescriptor);
}

export function parseExtensionCatalog(value: unknown): ExtensionCatalogResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-extension-catalog-v1"
    || typeof value.workspace_path !== "string"
    || !Array.isArray(value.extensions)
    || !Array.isArray(value.skills)
    || !Array.isArray(value.selected_skills)
    || !Array.isArray(value.mcp_servers)
    || !isRecord(value.activation)
    || typeof value.activation.extensions !== "string"
    || typeof value.activation.skills !== "string"
    || typeof value.activation.mcp !== "string"
    || !value.extensions.every(isRecord)
    || !value.skills.every(isRecord)
    || !value.selected_skills.every(isRecord)
    || !value.mcp_servers.every(isRecord)) {
    throw new Error("Desktop service returned an invalid extension catalog");
  }
  return {
    schema: "aegis-desktop-extension-catalog-v1",
    workspace_path: value.workspace_path,
    extensions: value.extensions.map(parseExtensionManifestSummary),
    skills: value.skills.map(parseSkillDescriptorSummary),
    selected_skills: value.selected_skills.map(parseSkillDescriptorSummary),
    mcp_servers: value.mcp_servers.map(parseMcpServerSummary),
    activation: {
      extensions: value.activation.extensions,
      skills: value.activation.skills,
      mcp: value.activation.mcp,
    },
  };
}

export function parseSkillLoadResult(value: unknown): SkillLoadResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-skill-v1"
    || !isRecord(value.skill)
    || typeof value.body !== "string") {
    throw new Error("Desktop service returned an invalid skill");
  }
  return {
    schema: "aegis-desktop-skill-v1",
    skill: parseSkillDescriptorSummary(value.skill),
    body: value.body,
  };
}

export function parseMcpCatalog(value: unknown): McpCatalogResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-catalog-v1"
    || !Array.isArray(value.servers)
    || !value.servers.every(isRecord)
    || typeof value.activation !== "string") {
    throw new Error("Desktop service returned an invalid MCP catalog");
  }
  return {
    schema: "aegis-desktop-mcp-catalog-v1",
    servers: value.servers.map(parseMcpServerSummary),
    activation: value.activation,
  };
}

export function parseMcpRegistrySearch(value: unknown): McpRegistrySearchResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-registry-search-v1"
    || typeof value.query !== "string"
    || value.query.length < 1
    || value.query.length > 128
    || !Array.isArray(value.servers)
    || value.servers.length > 10
    || (value.next_cursor !== null && typeof value.next_cursor !== "string")) {
    throw new Error("Desktop service returned an invalid MCP directory response");
  }
  const servers = value.servers.map((rawServer): McpRegistryServer => {
    if (!isRecord(rawServer)
      || typeof rawServer.name !== "string"
      || rawServer.name.length < 1
      || rawServer.name.length > 255
      || (rawServer.title !== null && typeof rawServer.title !== "string")
      || typeof rawServer.version !== "string"
      || rawServer.version.length < 1
      || rawServer.version.length > 128
      || typeof rawServer.description !== "string"
      || rawServer.description.length > 2048
      || !Array.isArray(rawServer.packages)
      || rawServer.packages.length > 8
      || !Array.isArray(rawServer.remotes)
      || rawServer.remotes.length > 8) {
      throw new Error("Desktop service returned an invalid MCP directory server");
    }
    const packages = rawServer.packages.map((rawPackage): McpRegistryPackage => {
      if (!isRecord(rawPackage)
        || typeof rawPackage.registry_type !== "string"
        || typeof rawPackage.identifier !== "string"
        || typeof rawPackage.version !== "string"
        || (rawPackage.runtime_hint !== null && typeof rawPackage.runtime_hint !== "string")
        || typeof rawPackage.transport !== "string"
        || !Array.isArray(rawPackage.required_environment)
        || rawPackage.required_environment.length > 32
        || !rawPackage.required_environment.every((item) => typeof item === "string" && /^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(item))
        || (rawPackage.environment_variables !== undefined
          && (!Array.isArray(rawPackage.environment_variables) || rawPackage.environment_variables.length > 32))) {
        throw new Error("Desktop service returned invalid MCP package metadata");
      }
      const environmentVariables = parseMcpEnvironmentVariables(
        rawPackage.environment_variables,
        rawPackage.required_environment,
      );
      return {
        registry_type: rawPackage.registry_type,
        identifier: rawPackage.identifier,
        version: rawPackage.version,
        runtime_hint: rawPackage.runtime_hint,
        transport: rawPackage.transport,
        required_environment: rawPackage.required_environment,
        environment_variables: environmentVariables,
      };
    });
    const remotes = rawServer.remotes.map((rawRemote): McpRegistryRemote => {
      if (!isRecord(rawRemote)
        || typeof rawRemote.transport !== "string"
        || (rawRemote.endpoint !== null && typeof rawRemote.endpoint !== "string")
        || typeof rawRemote.requires_headers !== "boolean") {
        throw new Error("Desktop service returned invalid MCP remote metadata");
      }
      return {
        transport: rawRemote.transport,
        endpoint: rawRemote.endpoint,
        requires_headers: rawRemote.requires_headers,
      };
    });
    return {
      name: rawServer.name,
      title: rawServer.title,
      version: rawServer.version,
      description: rawServer.description,
      packages,
      remotes,
    };
  });
  if (typeof value.next_cursor === "string" && value.next_cursor.length > 1_024) {
    throw new Error("Desktop service returned an oversized MCP directory cursor");
  }
  return {
    schema: "aegis-desktop-mcp-registry-search-v1",
    query: value.query,
    servers,
    next_cursor: value.next_cursor,
  };
}

export function parseMcpMetadata(value: unknown): McpMetadataResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-metadata-v1"
    || typeof value.server_id !== "string"
    || typeof value.path !== "string"
    || typeof value.transport !== "string"
    || value.activated !== false) {
    throw new Error("Desktop service returned invalid MCP metadata result");
  }
  return value as unknown as McpMetadataResult;
}

export function parseCapabilityState(value: unknown): CapabilityStateResult {
  if (!isRecord(value)
    || value.schema !== "aegis-capability-state-v1"
    || typeof value.revision !== "number"
    || !Number.isInteger(value.revision)
    || value.revision < 0
    || !Array.isArray(value.records)
    || !value.records.every(isRecord)) {
    throw new Error("Desktop service returned invalid capability state");
  }
  const records = value.records.map((record) => {
    if ((record.kind !== "skill" && record.kind !== "extension" && record.kind !== "mcp")
      || typeof record.id !== "string"
      || typeof record.descriptor_hash !== "string"
      || typeof record.enabled !== "boolean"
      || typeof record.approved !== "boolean"
      || typeof record.updated_at_ms !== "number"
      || (record.source_server_id !== undefined
        && (typeof record.source_server_id !== "string" || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(record.source_server_id)))
      || (record.resource_uri !== undefined
        && (typeof record.resource_uri !== "string" || record.resource_uri.length === 0 || record.resource_uri.length > 4096))
      || ((record.source_server_id === undefined) !== (record.resource_uri === undefined))
      || (record.source_server_id !== undefined
        && (record.kind !== "skill"
          || !record.id.startsWith("mcp:" + record.source_server_id + ":")
          || !/^[a-f0-9]{64}$/.test(record.descriptor_hash)))
      || ((record.skill_name === undefined) !== (record.skill_description === undefined))
      || (record.skill_name !== undefined
        && (record.kind !== "skill"
          || record.source_server_id === undefined
          || typeof record.skill_name !== "string"
          || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(record.skill_name)
          || record.skill_name.length > 64
          || typeof record.skill_description !== "string"
          || record.skill_description.trim().length === 0
          || record.skill_description.length > 1024
          || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(record.skill_description)))) {
      throw new Error("Desktop service returned an invalid capability state record");
    }
    return record as unknown as CapabilityStateRecord;
  });
  return { schema: "aegis-capability-state-v1", revision: value.revision, records };
}

export function parseMcpActivation(value: unknown): McpActivationResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-activation-v1"
    || typeof value.server_id !== "string"
    || (value.lifecycle !== "ACTIVE" && value.lifecycle !== "INACTIVE")
    || !Array.isArray(value.tools)
    || !value.tools.every(isRecord)) {
    throw new Error("Desktop service returned invalid MCP activation state");
  }
  const tools = value.tools.map((tool) => {
    if (typeof tool.name !== "string"
      || typeof tool.description !== "string"
      || typeof tool.effect_class !== "string"
      || typeof tool.extension_id !== "string"
      || typeof tool.descriptor_hash !== "string") {
      throw new Error("Desktop service returned invalid MCP tool metadata");
    }
    return tool as unknown as McpActivationResult["tools"][number];
  });
  return { schema: "aegis-desktop-mcp-activation-v1", server_id: value.server_id, lifecycle: value.lifecycle, tools };
}

export function parseMcpTest(value: unknown): McpTestResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-test-v2"
    || typeof value.server_id !== "string"
    || typeof value.test_token !== "string"
    || value.test_token.length < 32
    || value.test_token.length > 128
    || !Number.isSafeInteger(value.tools_total)
    || (value.tools_total as number) < 0
    || typeof value.tools_truncated !== "boolean"
    || value.connected !== true
    || value.left_running !== false
    || value.tools_executed !== false
    || !Array.isArray(value.tools)
    || !value.tools.every(isRecord)) {
    throw new Error("Desktop service returned invalid MCP test results");
  }
  const tools = value.tools.map((tool) => {
    if (typeof tool.name !== "string"
      || typeof tool.description !== "string"
      || typeof tool.effect_class !== "string"
      || !isStringArray(tool.capabilities)
      || typeof tool.extension_id !== "string"
      || typeof tool.descriptor_hash !== "string"
      || typeof tool.mcp_descriptor_hash !== "string"
      || !/^[a-f0-9]{64}$/.test(tool.mcp_descriptor_hash)
      || typeof tool.read_only_candidate !== "boolean"
      || typeof tool.host_managed_read_only !== "boolean"
      || (tool.open_world_hint !== undefined && typeof tool.open_world_hint !== "boolean")) {
      throw new Error("Desktop service returned invalid MCP test tool metadata");
    }
    return {
      ...tool,
      open_world_hint: tool.open_world_hint ?? true,
      host_managed_read_only: tool.host_managed_read_only,
    } as unknown as McpTestResult["tools"][number];
  });
  if ((value.tools_total as number) < tools.length
    || value.tools_truncated !== ((value.tools_total as number) > tools.length)) {
    throw new Error("Desktop service returned inconsistent MCP test counts");
  }
  return {
    schema: "aegis-desktop-mcp-test-v2",
    server_id: value.server_id,
    test_token: value.test_token,
    tools,
    tools_total: value.tools_total as number,
    tools_truncated: value.tools_truncated,
    connected: true,
    left_running: false,
    tools_executed: false,
  };
}

export function parseMcpPromptsList(value: unknown): McpPromptsListResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-prompts-v1"
    || typeof value.server_id !== "string"
    || value.server_id.length === 0
    || !Array.isArray(value.prompts)
    || value.prompts.length > 128
    || !value.prompts.every(isRecord)) {
    throw new Error("Desktop service returned an invalid MCP prompt catalog");
  }
  const prompts = value.prompts.map((prompt) => {
    if (typeof prompt.name !== "string"
      || prompt.name.length === 0
      || typeof prompt.title !== "string"
      || typeof prompt.description !== "string"
      || !Array.isArray(prompt.arguments)
      || prompt.arguments.length > 32
      || !prompt.arguments.every(isRecord)) {
      throw new Error("Desktop service returned invalid MCP prompt metadata");
    }
    const argumentsList = prompt.arguments.map((argument) => {
      if (typeof argument.name !== "string"
        || argument.name.length === 0
        || typeof argument.description !== "string"
        || typeof argument.required !== "boolean") {
        throw new Error("Desktop service returned invalid MCP prompt arguments");
      }
      return argument as unknown as McpPromptArgument;
    });
    return { ...prompt, arguments: argumentsList } as unknown as McpPromptDescriptor;
  });
  return {
    schema: "aegis-desktop-mcp-prompts-v1",
    server_id: value.server_id,
    prompts,
  };
}

export function parseMcpPromptResult(value: unknown): McpPromptResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-prompt-result-v1"
    || typeof value.server_id !== "string"
    || typeof value.name !== "string"
    || typeof value.description !== "string"
    || typeof value.trust_notice !== "string"
    || !Array.isArray(value.messages)
    || value.messages.length === 0
    || value.messages.length > 16
    || !value.messages.every(isRecord)) {
    throw new Error("Desktop service returned an invalid MCP prompt preview");
  }
  const messages = value.messages.map((message) => {
    if ((message.role !== "user" && message.role !== "assistant")
      || typeof message.text !== "string"
      || message.text.length > 65_536) {
      throw new Error("Desktop service returned an invalid MCP prompt message");
    }
    return message as unknown as McpPromptResult["messages"][number];
  });
  return { ...value, messages } as unknown as McpPromptResult;
}

export function parseMcpSkillsList(value: unknown): McpSkillsListResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-skills-v1"
    || typeof value.server_id !== "string"
    || typeof value.supports_skills !== "boolean"
    || !Array.isArray(value.skills)
    || !value.skills.every(isRecord)
    || (value.next_cursor !== null && typeof value.next_cursor !== "string")
    || !Number.isSafeInteger(value.ttl_ms)
    || (value.ttl_ms as number) < 0
    || typeof value.trust_notice !== "string") {
    throw new Error("Desktop service returned an invalid MCP Skills catalog");
  }
  const skills = value.skills.map((skill) => {
    if (typeof skill.id !== "string"
      || skill.id.length === 0
      || skill.id.length > 256
      || typeof skill.server_id !== "string"
      || skill.server_id !== value.server_id
      || typeof skill.uri !== "string"
      || skill.uri.length === 0
      || skill.uri.length > 4096
      || typeof skill.name !== "string"
      || typeof skill.description !== "string"
      || (skill.manifest_hash !== null
        && (typeof skill.manifest_hash !== "string" || !/^[a-f0-9]{64}$/.test(skill.manifest_hash)))
      || (skill.resource_count !== null
        && (!Number.isSafeInteger(skill.resource_count) || (skill.resource_count as number) < 1))
      || typeof skill.dynamic !== "boolean"
      || typeof skill.approval_supported !== "boolean"
      || typeof skill.approved !== "boolean"
      || typeof skill.enabled !== "boolean"
      || skill.approval_supported !== (skill.manifest_hash !== null)
      || skill.dynamic === skill.approval_supported
      || (skill.enabled && !skill.approved)
      || (skill.dynamic && (skill.approved || skill.enabled))) {
      throw new Error("Desktop service returned invalid MCP Skill metadata");
    }
    return skill as unknown as McpSkillSummary;
  });
  if (!value.supports_skills && skills.length !== 0) {
    throw new Error("Desktop service returned skills without declaring the extension");
  }
  const state = parseCapabilityState(value.state);
  return {
    schema: "aegis-desktop-mcp-skills-v1",
    server_id: value.server_id,
    supports_skills: value.supports_skills,
    skills,
    next_cursor: value.next_cursor,
    ttl_ms: value.ttl_ms as number,
    state,
    trust_notice: value.trust_notice,
  };
}

export function parseMcpSkillState(value: unknown): McpSkillStateResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-mcp-skill-state-v1"
    || typeof value.server_id !== "string"
    || typeof value.id !== "string"
    || typeof value.manifest_hash !== "string"
    || !/^[a-f0-9]{64}$/.test(value.manifest_hash)
    || typeof value.enabled !== "boolean"
    || typeof value.approved !== "boolean"
    || (value.enabled && !value.approved)) {
    throw new Error("Desktop service returned an invalid MCP Skill approval");
  }
  return {
    schema: "aegis-desktop-mcp-skill-state-v1",
    server_id: value.server_id,
    id: value.id,
    manifest_hash: value.manifest_hash,
    enabled: value.enabled,
    approved: value.approved,
    state: parseCapabilityState(value.state),
  };
}

function parseExtensionManifestSummary(value: Record<string, unknown>): ExtensionManifestSummary {
  if (typeof value.schema !== "string"
    || typeof value.extension_id !== "string"
    || typeof value.version !== "string"
    || typeof value.description !== "string"
    || !isStringArray(value.capabilities)
    || !isStringArray(value.tool_names)
    || !isStringArray(value.skill_names)
    || typeof value.source !== "string"
    || typeof value.trusted !== "boolean"
    || typeof value.manifest_hash !== "string"
    || (value.wasm_plugin !== undefined && typeof value.wasm_plugin !== "boolean")
    || (value.descriptor_hash !== undefined && typeof value.descriptor_hash !== "string")
    || (value.module_sha256 !== undefined
      && value.module_sha256 !== null
      && typeof value.module_sha256 !== "string")) {
    throw new Error("Desktop service returned an invalid extension descriptor");
  }
  return value as unknown as ExtensionManifestSummary;
}

function parseSkillDescriptorSummary(value: Record<string, unknown>): SkillDescriptorSummary {
  if (typeof value.schema !== "string"
    || typeof value.name !== "string"
    || typeof value.description !== "string"
    || typeof value.version !== "string"
    || typeof value.content_hash !== "string"
    || !isStringArray(value.keywords)
    || typeof value.source !== "string") {
    throw new Error("Desktop service returned an invalid skill descriptor");
  }
  return value as unknown as SkillDescriptorSummary;
}

function parseMcpServerSummary(value: Record<string, unknown>): McpServerSummary {
  if (typeof value.schema !== "string"
    || typeof value.server_id !== "string"
    || typeof value.description !== "string"
    || typeof value.transport !== "string"
    || typeof value.source !== "string"
    || typeof value.approved !== "boolean"
    || typeof value.descriptor_hash !== "string") {
    throw new Error("Desktop service returned an invalid MCP descriptor");
  }
  return {
    ...value as unknown as McpServerSummary,
    environment_variables: parseMcpEnvironmentVariables(value.environment_variables),
  };
}

function parseMcpEnvironmentVariables(
  value: unknown,
  legacyRequiredNames: string[] = [],
): McpRegistryEnvironmentVariable[] {
  if (value === undefined) {
    return legacyRequiredNames.map((name) => ({
      name,
      is_required: true,
      is_secret: true,
      description: null,
    }));
  }
  if (!Array.isArray(value) || value.length > 32) {
    throw new Error("Desktop service returned invalid MCP environment metadata");
  }
  const seen = new Set<string>();
  return value.map((rawVariable): McpRegistryEnvironmentVariable => {
    if (!isRecord(rawVariable)
      || typeof rawVariable.name !== "string"
      || !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(rawVariable.name)
      || typeof rawVariable.is_required !== "boolean"
      || typeof rawVariable.is_secret !== "boolean"
      || (rawVariable.description !== null && typeof rawVariable.description !== "string")
      || (typeof rawVariable.description === "string" && rawVariable.description.length > 512)
      || seen.has(rawVariable.name.toLowerCase())) {
      throw new Error("Desktop service returned invalid MCP environment metadata");
    }
    seen.add(rawVariable.name.toLowerCase());
    return {
      name: rawVariable.name,
      is_required: rawVariable.is_required,
      is_secret: rawVariable.is_secret,
      description: rawVariable.description,
    };
  });
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

export function parseSubagentStartResult(value: unknown): SubagentStartResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-subagents-start-v1"
    || typeof value.run_id !== "string"
    || value.status !== "RUNNING"
    || typeof value.event_cursor !== "number") {
    throw new Error("Desktop service returned an invalid subagent start result");
  }
  return value as unknown as SubagentStartResult;
}

export function parseSubagentStatusResult(value: unknown): SubagentStatusResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-subagents-status-v1"
    || typeof value.run_id !== "string"
    || typeof value.status !== "string"
    || typeof value.event_cursor !== "number"
    || typeof value.started_at_ms !== "number"
    || (value.finished_at_ms !== null && typeof value.finished_at_ms !== "number")
    || (value.cancel_requested_at_ms !== null && typeof value.cancel_requested_at_ms !== "number")
    || typeof value.cancel_supported !== "boolean"
    || typeof value.thread_alive !== "boolean"
    || (value.result !== null && !isRecord(value.result))
    || (value.error !== null && (!isRecord(value.error)
      || typeof value.error.code !== "string"
      || typeof value.error.message !== "string"))) {
    throw new Error("Desktop service returned an invalid subagent status result");
  }
  return {
    schema: "aegis-desktop-subagents-status-v1",
    run_id: value.run_id,
    status: value.status,
    event_cursor: value.event_cursor,
    started_at_ms: value.started_at_ms,
    finished_at_ms: value.finished_at_ms as number | null,
    cancel_requested_at_ms: value.cancel_requested_at_ms as number | null,
    cancel_supported: value.cancel_supported,
    thread_alive: value.thread_alive,
    result: value.result === null ? null : parseSubagentRunResult(value.result),
    error: value.error === null ? null : {
      code: value.error.code as string,
      message: value.error.message as string,
    },
  };
}

export function parseSubagentCancelResult(value: unknown): SubagentCancelResult {
  if (!isRecord(value)
    || value.schema !== "aegis-desktop-subagents-cancel-v1"
    || typeof value.run_id !== "string"
    || typeof value.status !== "string"
    || typeof value.event_cursor !== "number") {
    throw new Error("Desktop service returned an invalid subagent cancellation result");
  }
  return value as unknown as SubagentCancelResult;
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

export function parseSubagentGraph(value: unknown): SubagentGraph {
  const graph = isRecord(value) && isRecord(value.graph) ? value.graph : value;
  if (!isRecord(graph)
    || graph.schema !== "aegis-desktop-subagent-graph-v1"
    || typeof graph.run_id !== "string"
    || !Array.isArray(graph.nodes)
    || !Array.isArray(graph.edges)
    || !graph.nodes.every(isRecord)
    || !graph.edges.every(isRecord)
    || graph.nodes.some((node) => node.redacted !== true)) {
    throw new Error("Desktop service returned an invalid subagent graph");
  }
  return graph as unknown as SubagentGraph;
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

function isNonnegativeSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isModelCatalogState(value: unknown): value is ModelCatalogState {
  return value === "FRESH" || value === "CACHED" || value === "STALE" || value === "UNAVAILABLE";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isReasoningEffortValue(value: unknown): value is string {
  return typeof value === "string"
    && value.length <= 64
    && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value)
    && !["auto", "declared", "inferred"].includes(value.toLowerCase());
}

function isReasoningEffortArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 64 && value.every(isReasoningEffortValue);
}

function isThinkingDefault(value: unknown): value is string {
  return value === "Auto" || isReasoningEffortValue(value);
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
    || typeof value.observed_at_ms !== "number"
    || (value.reasoning_efforts !== undefined && !isReasoningEffortArray(value.reasoning_efforts))
    || (value.supports_vision !== undefined && typeof value.supports_vision !== "boolean")
    || (value.input_modalities !== undefined && !isStringArray(value.input_modalities))
    || (value.max_image_inputs !== undefined
      && value.max_image_inputs !== null
      && (typeof value.max_image_inputs !== "number" || !Number.isInteger(value.max_image_inputs) || value.max_image_inputs < 1 || value.max_image_inputs > 4))) {
    throw new Error("Desktop service returned an invalid model descriptor");
  }
  const capabilities = value.capabilities.map((item) => item.toLowerCase());
  const legacyInferredVision = capabilities.includes("vision:inferred")
    && !capabilities.includes("vision")
    && !capabilities.includes("capability:provider-profile");
  return {
    ...(value as unknown as ModelDescriptor),
    reasoning_efforts: value.reasoning_efforts ?? [],
    supports_vision: value.supports_vision ?? (!legacyInferredVision && capabilities.some((item) => ["vision", "input:image", "image", "multimodal"].includes(item))),
    input_modalities: value.input_modalities ?? ["text"],
    max_image_inputs: value.max_image_inputs ?? null,
  };
}

function isDesktopResponse<T>(value: unknown): value is DesktopResponse<T> {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return candidate.schema === RESPONSE_SCHEMA
    && candidate.protocol_version === PROTOCOL_VERSION
    && typeof candidate.request_id === "string"
    && (candidate.status === "ok" || candidate.status === "error");
}
