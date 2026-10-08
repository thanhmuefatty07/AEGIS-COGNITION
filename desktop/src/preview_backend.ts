import type {
  Conversation,
  ConversationSnapshot,
  ConversationInspection,
  DesktopRunStatus,
  DesktopCommand,
  SubagentEvent,
  SubagentResultPacket,
  SubagentRunResult,
  SubagentStatusResult,
  SubagentGraph,
} from "./protocol";

type PreviewRun = {
  runId: string;
  task: string;
  startedAt: number;
  cancelRequestedAt: number | null;
  events: SubagentEvent[];
  status: string;
  result: SubagentRunResult | null;
};

type PreviewDesktopRun = {
  runId: string;
  conversationId: string;
  message: string;
  status: DesktopRunStatus["status"];
  startedAt: number;
  cancelRequestedAt: number | null;
  finishedAt: number | null;
  timer: ReturnType<typeof setTimeout> | null;
};

const previewConversations = new Map<string, Conversation>();
const previewSnapshots = new Map<string, ConversationSnapshot>();
const previewRuns = new Map<string, PreviewRun>();
const previewDesktopRuns = new Map<string, PreviewDesktopRun>();
const previewConnections = new Map<string, Record<string, unknown>>();
const previewCapabilityState = { revision: 0, records: [] as Array<Record<string, unknown>> };
const previewSettings = {
  revision: 0,
  settings: {
    theme: "system" as const,
    font_size_preset: "Grande" as const,
    proxy_mode: "Direct" as const,
    permission_mode: "Ask every time" as const,
    agent_default_mode: "Agent" as const,
    voice_enabled: false,
    thinking_default: "Auto" as const,
    context_usage: "Remaining" as const,
    enter_to_send: true,
    instructions: "Use the local workspace context, preserve evidence boundaries, and report uncertainty explicitly.",
    parallel_workers_enabled: true,
  },
};

function now() {
  return Date.now();
}

function id(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function makeConversation(payload: Record<string, unknown>): Conversation {
  const timestamp = now();
  return {
    conversation_id: String(payload.conversation_id ?? id("preview")),
    owner_id: "preview-user",
    title: String(payload.title ?? "New thread"),
    connection_id: String(payload.connection_id ?? "local"),
    model_id: String(payload.model_id ?? "local-model"),
    status: "OPEN",
    revision: 1,
    created_at_ms: timestamp,
    updated_at_ms: timestamp,
  };
}

function emptySnapshot(conversation: Conversation): ConversationSnapshot {
  return {
    conversation,
    turns: [],
    executions: [],
    checkpoints: [],
    tool_calls: [],
    parts: [],
  };
}

function getOrCreateConversation(conversationId: string) {
  const existing = previewSnapshots.get(conversationId);
  if (existing) return existing;
  const conversation = makeConversation({ conversation_id: conversationId });
  previewConversations.set(conversationId, conversation);
  const snapshot = emptySnapshot(conversation);
  previewSnapshots.set(conversationId, snapshot);
  return snapshot;
}

function event(run: PreviewRun, messageKind: string, taskId: number, payload: Record<string, unknown>): SubagentEvent {
  const cursor = run.events.length + 1;
  const value: SubagentEvent = {
    cursor,
    message_kind: messageKind,
    run_id: run.runId,
    sender_id: "preview-supervisor",
    recipient_id: taskId === 0 ? "preview-root" : `preview-worker-${taskId}`,
    task_id: taskId,
    parent_task_id: taskId === 0 ? null : 0,
    attempt_id: 1,
    message_hash: `preview-${cursor}`,
    artifact_refs: [],
    payload,
  };
  run.events.push(value);
  return value;
}

function workerResult(taskId: number, summary: string, status = "SUCCEEDED"): SubagentResultPacket {
  return {
    task_id: taskId,
    status,
    summary,
    summary_truncated: false,
    claims: [],
    artifacts: [],
    uncertainty: ["Browser preview uses a simulated worker result."],
    blockers: [],
    tokens_in: 0,
    tokens_out: 0,
    packet_hash: `preview-worker-${taskId}`,
  };
}

function finishRun(run: PreviewRun, status: string, summary: string) {
  if (run.result) return;
  const childResults = [1, 2, 3].map((taskId) => workerResult(taskId, summary, status === "CANCELLED" ? "CANCELLED" : "SUCCEEDED"));
  run.status = status;
  run.result = {
    schema: "aegis-desktop-subagents-result-v1",
    run_id: run.runId,
    graph_hash: "preview-graph",
    graph_authority: "PREVIEW_ONLY",
    status,
    root_output: summary,
    root_output_truncated: false,
    child_results: childResults,
    failed_task_ids: [],
    blocked_task_ids: [],
    coordination_hash: "preview-coordination",
    event_cursor: run.events.length,
  };
  event(run, "TASK_RESULT", 0, { status, summary });
}

function desktopRunStatus(run: PreviewDesktopRun): DesktopRunStatus {
  return {
    schema: "aegis-desktop-run-status-v1",
    run_id: run.runId,
    conversation_id: run.conversationId,
    status: run.status,
    started_at_ms: run.startedAt,
    finished_at_ms: run.finishedAt,
    cancel_requested_at_ms: run.cancelRequestedAt,
    cancel_supported: true,
    thread_alive: run.status === "RUNNING" || run.status === "CANCELLING",
    error_code: null,
  };
}

function settleDesktopRun(run: PreviewDesktopRun, status: "COMPLETED" | "CANCELLED") {
  if (run.status !== "RUNNING" && run.status !== "CANCELLING") return;
  if (run.timer) clearTimeout(run.timer);
  run.timer = null;
  run.status = status;
  run.finishedAt = now();
  const snapshot = getOrCreateConversation(run.conversationId);
  const assistant = snapshot.turns.at(-1);
  if (assistant) {
    assistant.status = status;
    assistant.content = status === "COMPLETED"
      ? "Preview response: I received “" + run.message + "”."
      : "Task stopped by the user.";
    assistant.revision = snapshot.conversation.revision + 1;
  }
  const execution = snapshot.executions.at(-1);
  if (execution) {
    execution.status = status;
    execution.finished_at_ms = run.finishedAt;
    execution.revision = snapshot.conversation.revision + 1;
  }
  snapshot.conversation = {
    ...snapshot.conversation,
    revision: snapshot.conversation.revision + 1,
    updated_at_ms: run.finishedAt,
  };
  previewConversations.set(run.conversationId, snapshot.conversation);
}

function advanceRun(run: PreviewRun) {
  if (run.result) return;
  const elapsed = now() - run.startedAt;
  if (elapsed > 260) {
    if (run.events.length < 2) event(run, "TASK_REQUEST", 1, { role: "research", summary: "Preview worker admitted" });
    if (run.events.length < 3) event(run, "TASK_REQUEST", 2, { role: "browser", summary: "Preview worker admitted" });
  }
  if (elapsed > 620) {
    if (run.events.length < 4) event(run, "TASK_REQUEST", 3, { role: "verification", summary: "Preview worker admitted" });
    finishRun(run, "SUCCEEDED", `Preview completed: ${run.task}`);
  }
}

function sourceSnapshot() {
  const paths = [
    ["aegis_cognition/agent.py", "python", 18240],
    ["aegis_cognition/subagents.py", "python", 64320],
    ["aegis_cognition/desktop_service.py", "python", 51200],
    ["desktop/src/App.tsx", "typescript", 28400],
    ["desktop/src/styles.css", "css", 19800],
    ["core/rust/src/gt96.rs", "rust", 42100],
  ] as const;
  return {
    snapshot: {
      identity: {
        project_id: "preview-project",
        root: "local://AEGIS-COGNITION",
        vcs: "git",
        repository_root: "local://AEGIS-COGNITION",
        common_git_dir: null,
        checkout_id: "preview-checkout",
        branch: "codex/core-agent-integration",
        head: "2d16b1d",
        dirty: false,
        dirty_paths: [],
      },
      revision: "preview-r1",
      created_at_ms: now(),
      files: paths.map(([relative_path, language, size_bytes]) => ({
        relative_path,
        language,
        size_bytes,
        modified_ns: 0,
        content_hash: null,
        extraction_status: "ok",
        symbols: [],
        imports: [],
        error: null,
        symlink_target: null,
      })),
      lineage: [],
      stale_paths: [],
      deleted_paths: [],
      overflowed: false,
    },
  };
}

function previewConversationInspection(snapshot: ConversationSnapshot): ConversationInspection {
  return {
    schema: "aegis-desktop-conversation-inspection-v1",
    conversation_id: snapshot.conversation.conversation_id,
    revision: snapshot.conversation.revision,
    context: {
      schema: "aegis-desktop-context-inspection-v1",
      status: "NOT_CAPTURED",
      source_revision: "preview-r1",
      context_manifest_hash: null,
      prompt_hash: null,
      token_budget: null,
      token_count: null,
      item_count: null,
      selection_backend: null,
      history_turn_count: snapshot.turns.length,
      sensitive_content: "REDACTED",
    },
    timeline: snapshot.turns.map((turn, index) => ({
      event_id: `turn:${turn.turn_id}`,
      kind: "TURN",
      status: turn.status,
      title: `${turn.role === "user" ? "User" : "Assistant"} turn`,
      detail: "Turn content is hidden from the observer projection.",
      timestamp_ms: index + 1,
      reference: turn.turn_id,
    })),
    approvals: [],
    redaction: "provider prompts, sensitive tool values, full results, and continuation bodies are redacted",
  };
}

function previewSubagentGraph(run: PreviewRun): SubagentGraph {
  const nodes = run.events
    .filter((item) => item.task_id > 0)
    .reduce<SubagentGraph["nodes"]>((items, item) => {
      const existing = items.find((node) => node.task_id === item.task_id);
      const payload = item.payload;
      if (existing) {
        if (item.message_kind === "TASK_RESULT") {
          existing.status = String(payload.status ?? "COMPLETED");
          existing.summary = String(payload.summary ?? "");
        }
        return items;
      }
      items.push({
        task_id: item.task_id,
        parent_task_id: item.parent_task_id,
        status: item.message_kind === "TASK_RESULT" ? String(payload.status ?? "COMPLETED") : "RUNNING",
        role: String(payload.role ?? ""),
        dependencies: Array.isArray(payload.dependency_ids) ? payload.dependency_ids.filter((value): value is number => typeof value === "number") : [],
        capabilities: Array.isArray(payload.capabilities) ? payload.capabilities.filter((value): value is string => typeof value === "string") : [],
        side_effect_class: String(payload.side_effect_class ?? ""),
        summary: String(payload.summary ?? ""),
        uncertainty: [],
        blockers: [],
        redacted: true,
      });
      return items;
    }, []);
  return {
    schema: "aegis-desktop-subagent-graph-v1",
    run_id: run.runId,
    nodes,
    edges: nodes.flatMap((node) => node.dependencies.map((from) => ({ from, to: node.task_id }))),
    redaction: "task prompts and raw result bodies are redacted",
  };
}

export async function previewRequest(command: DesktopCommand, payload: Record<string, unknown>): Promise<unknown> {
  switch (command) {
    case "workspace.open":
      return {
        open: true,
        workspace_path: "AEGIS-COGNITION",
        state_path: null,
        profile_id: "preview",
        native_runtime_available: false,
        preview_mode: true,
      };
    case "workspace.switch":
      return {
        open: true,
        workspace_path: String(payload.workspace_path ?? "AEGIS-COGNITION"),
        state_path: null,
        profile_id: "preview",
        native_runtime_available: false,
        preview_mode: true,
      };
    case "workspace.clone": {
      const cloneUrl = String(payload.clone_url ?? "").trim();
      const name = cloneUrl.split(/[\\/]/).filter(Boolean).at(-1)?.replace(/\.git$/i, "") || "preview-project";
      return { workspace_path: `preview/projects/${name}`, name };
    }
    case "workspace.source_snapshot":
      return sourceSnapshot();
    case "workspace.file_read": {
      const relativePath = String(payload.relative_path ?? "");
      return {
        schema: "aegis-desktop-file-v1",
        relative_path: relativePath,
        content: `Browser preview does not read local files.\n\nOpen AEGIS Desktop to inspect ${relativePath}.`,
        size_bytes: 0,
        sha256: "preview-only",
        modified_at_ms: now(),
        source: "PREVIEW_ONLY",
      };
    }
    case "workspace.changes":
      return payload.relative_path
        ? {
            schema: "aegis-desktop-change-diff-v1",
            relative_path: String(payload.relative_path),
            diff: "Browser preview cannot read local Git changes.",
            truncated: false,
            source: "PREVIEW_ONLY",
          }
        : { schema: "aegis-desktop-changes-v1", source: "PREVIEW_ONLY", entries: [], truncated: false };
    case "workspace.open_external":
      throw new Error("Browser preview cannot open files outside the browser.");
    case "projects.list":
      return {
        schema: "aegis-desktop-projects-v1",
        projects: [{
          project_id: "project:preview",
          path: "preview/AEGIS-COGNITION",
          name: "AEGIS-COGNITION",
          parent_path: "preview",
          last_opened_at_ms: now(),
          open_count: 1,
          status: "AVAILABLE",
        }],
        active_project_id: "project:preview",
      };
    case "projects.remove":
      return { schema: "aegis-desktop-project-remove-v1", project_id: String(payload.project_id ?? ""), removed: true };
    case "settings.get":
      return { schema: "aegis-desktop-settings-v1", ...previewSettings };
    case "settings.update": {
      const expectedRevision = Number(payload.expected_revision ?? -1);
      if (expectedRevision !== previewSettings.revision) throw new Error("SETTINGS_CONFLICT: preview settings are stale");
      previewSettings.revision += 1;
      previewSettings.settings = { ...previewSettings.settings, ...(payload.settings as Partial<typeof previewSettings.settings>) };
      return { schema: "aegis-desktop-settings-v1", ...previewSettings };
    }
    case "connections.list":
      return {
        records: [...previewConnections.values()],
        providers: [{
          provider_kind: "openai-compatible",
          provider_label: "Preview-compatible provider",
          endpoint: "http://127.0.0.1:8080/v1",
          protocol: "chat-completions",
          requires_endpoint: false,
        }],
      };
    case "connections.identify":
      return {
        identity: {
          provider_kind: "openai-compatible",
          provider_label: "Preview provider",
          endpoint: "http://127.0.0.1:8080/v1",
          protocol: "chat-completions",
          confidence: "low",
          hints: ["Browser preview does not validate API keys."],
          requires_endpoint: false,
          requires_confirmation: false,
        },
      };
    case "connections.connect": {
      const providerKind = String(payload.provider_kind ?? "openai-compatible");
      const connection = {
        connection_id: "local",
        provider_kind: providerKind,
        endpoint: String(payload.endpoint ?? "http://127.0.0.1:8080/v1"),
        protocol: "chat-completions",
        secret_ref: null,
        enabled: true,
        revision: 1,
        updated_at_ms: now(),
      };
      previewConnections.set("local", connection);
      return {
        identity: {
          provider_kind: providerKind,
          provider_label: "Preview provider",
          endpoint: connection.endpoint,
          protocol: connection.protocol,
          confidence: "low",
          hints: ["Browser preview does not validate API keys."],
          requires_endpoint: false,
          requires_confirmation: false,
        },
        record: connection,
        models: [{ connection_id: "local", model_id: "preview-model", family: "preview", capabilities: ["chat"], context_limit: null, output_limit: null, source: "preview", revision: 1, observed_at_ms: now(), reasoning_efforts: [], supports_vision: false, input_modalities: ["text"] }],
        secret_scope: "session",
        persistent: false,
        discovery_error: null,
        model_catalog_state: "FRESH",
      };
    }
    case "connections.save": {
      const connection = {
        connection_id: String(payload.connection_id ?? "local"),
        provider_kind: String(payload.provider_kind ?? "openai-compatible"),
        endpoint: String(payload.endpoint ?? "http://127.0.0.1:8080/v1"),
        protocol: String(payload.protocol ?? "chat-completions"),
        secret_ref: null,
        enabled: true,
        revision: 1,
        updated_at_ms: now(),
      };
      previewConnections.set(String(connection.connection_id), connection);
      return connection;
    }
    case "models.list":
      return { models: [{ connection_id: "local", model_id: "local-model", family: "preview", capabilities: ["chat"], context_limit: null, output_limit: null, source: "preview", revision: 1, observed_at_ms: now(), reasoning_efforts: [], supports_vision: false, input_modalities: ["text"] }] };
    case "connections.discover":
      return {
        models: [{ connection_id: "local", model_id: "preview-model", family: "preview", capabilities: ["chat"], context_limit: null, output_limit: null, source: "preview", revision: 1, observed_at_ms: now(), reasoning_efforts: [], supports_vision: false, input_modalities: ["text"] }],
        model_catalog_state: "FRESH",
        discovery_error: null,
      };
    case "extensions.discover":
      return {
        schema: "aegis-desktop-extension-catalog-v1",
        workspace_path: "AEGIS-COGNITION",
        extensions: [],
        skills: [],
        selected_skills: [],
        mcp_servers: [],
        activation: { extensions: "metadata-only", skills: "load-on-demand", mcp: "explicit-host-approval-required" },
      };
    case "extensions.load_skill":
      throw new Error("SKILL_NOT_FOUND: browser preview has no local skill files");
    case "extensions.mcp_preview":
      return { schema: "aegis-desktop-mcp-catalog-v1", servers: [], activation: "not performed; explicit host approval is required" };
    case "extensions.mcp_registry_search":
      return { schema: "aegis-desktop-mcp-registry-search-v1", query: String(payload.query ?? ""), servers: [], next_cursor: null };
    case "extensions.add_mcp_metadata":
      return { schema: "aegis-desktop-mcp-metadata-v1", server_id: String(payload.server_id ?? "preview"), path: `.aegis/mcp/${String(payload.server_id ?? "preview")}/mcp.toml`, transport: String(payload.transport ?? "stdio"), activated: false };
    case "extensions.state":
      return { schema: "aegis-capability-state-v1", ...previewCapabilityState };
    case "extensions.set_state": {
      const expectedRevision = Number(payload.expected_revision ?? -1);
      if (expectedRevision !== previewCapabilityState.revision) throw new Error("CAPABILITY_STATE_CONFLICT: preview state is stale");
      const kind = String(payload.kind ?? "");
      const id = String(payload.id ?? "");
      previewCapabilityState.records = previewCapabilityState.records.filter((record) => !(record.kind === kind && record.id === id));
      previewCapabilityState.records.push({
        kind,
        id,
        descriptor_hash: String(payload.descriptor_hash ?? "preview"),
        enabled: Boolean(payload.enabled),
        approved: Boolean(payload.approved),
        updated_at_ms: now(),
      });
      previewCapabilityState.revision += 1;
      return { schema: "aegis-capability-state-v1", ...previewCapabilityState };
    }
    case "extensions.test_mcp":
      return { schema: "aegis-desktop-mcp-test-v2", server_id: String(payload.server_id ?? "preview"), test_token: "preview-only-test-token-not-authorizing-anything", tools: [], tools_total: 0, tools_truncated: false, connected: true, left_running: false, tools_executed: false };
    case "extensions.activate_mcp":
      return { schema: "aegis-desktop-mcp-activation-v1", server_id: String(payload.server_id ?? "preview"), lifecycle: "ACTIVE", tools: [] };
    case "extensions.deactivate_mcp":
      return { schema: "aegis-desktop-mcp-activation-v1", server_id: String(payload.server_id ?? "preview"), lifecycle: "INACTIVE", tools: [] };
    case "extensions.mcp_prompts_list":
      return { schema: "aegis-desktop-mcp-prompts-v1", server_id: String(payload.server_id ?? "preview"), prompts: [] };
    case "extensions.mcp_prompts_get":
      throw new Error("MCP_PROMPT_FAILED: browser preview has no active MCP server");
    case "conversations.list":
      return { records: [...previewConversations.values()] };
    case "conversations.create": {
      const conversation = makeConversation(payload);
      previewConversations.set(conversation.conversation_id, conversation);
      previewSnapshots.set(conversation.conversation_id, emptySnapshot(conversation));
      return conversation;
    }
    case "conversations.read":
      return previewSnapshots.get(String(payload.conversation_id)) ?? getOrCreateConversation(String(payload.conversation_id));
    case "conversations.inspect": {
      const snapshot = previewSnapshots.get(String(payload.conversation_id)) ?? getOrCreateConversation(String(payload.conversation_id));
      return { inspection: previewConversationInspection(snapshot) };
    }
    case "approvals.resolve":
      throw new Error("One-time tool approvals are available only in the live desktop session.");
    case "tool_calls.reconcile":
      throw new Error("Tool outcome reconciliation is available only in the live desktop session.");
    case "conversations.switch_model": {
      const conversationId = String(payload.conversation_id);
      const snapshot = getOrCreateConversation(conversationId);
      const modelId = String(payload.model_id ?? "local-model");
      const connectionId = String(payload.connection_id ?? "local");
      snapshot.conversation = {
        ...snapshot.conversation,
        connection_id: connectionId,
        model_id: modelId,
        revision: snapshot.conversation.revision + 1,
        updated_at_ms: now(),
      };
      previewConversations.set(conversationId, snapshot.conversation);
      return snapshot.conversation;
    }
    case "conversations.send": {
      const conversationId = String(payload.conversation_id);
      const snapshot = getOrCreateConversation(conversationId);
      const text = String(payload.message ?? "");
      const attachments = Array.isArray(payload.attachments) ? payload.attachments : [];
      const attachmentSuffix = attachments.length > 0 ? `\n[${attachments.length} image attachment${attachments.length === 1 ? "" : "s"}]` : "";
      const transcriptText = `${text}${attachmentSuffix}`.trim();
      const revision = snapshot.conversation.revision + 1;
      const userTurn = { turn_id: id("turn"), role: "user", status: "COMPLETED", content: transcriptText, revision };
      const assistantTurn = { turn_id: id("turn"), role: "assistant", status: "COMPLETED", content: `Preview response: I received “${transcriptText}”.`, revision: revision + 1 };
      snapshot.turns.push(userTurn, assistantTurn);
      snapshot.conversation = { ...snapshot.conversation, revision: revision + 1, updated_at_ms: now() };
      previewConversations.set(conversationId, snapshot.conversation);
      return snapshot;
    }
    case "runs.start": {
      const conversationId = String(payload.conversation_id ?? "");
      const snapshot = getOrCreateConversation(conversationId);
      if (snapshot.turns.some((turn) => ["QUEUED", "RUNNING", "WAITING_APPROVAL"].includes(turn.status))) {
        throw new Error("CONVERSATION_BUSY: resolve the current task before sending another message");
      }
      if ([...previewDesktopRuns.values()].some((run) => run.status === "RUNNING" || run.status === "CANCELLING")) {
        throw new Error("RUN_BUSY: another foreground task is still running");
      }
      const message = String(payload.message ?? "").trim();
      const attachments = Array.isArray(payload.attachments) ? payload.attachments : [];
      if (!message && attachments.length === 0) throw new Error("INVALID_ARGUMENT: message or attachment is required");
      const run: PreviewDesktopRun = {
        runId: id("preview-task"),
        conversationId,
        message: message || "Attached " + attachments.length + " image(s)",
        status: "RUNNING",
        startedAt: now(),
        cancelRequestedAt: null,
        finishedAt: null,
        timer: null,
      };
      const revision = snapshot.conversation.revision + 1;
      const assistantTurnId = id("turn");
      snapshot.turns.push(
        { turn_id: id("turn"), role: "user", status: "COMPLETED", content: run.message, revision },
        { turn_id: assistantTurnId, role: "assistant", status: "RUNNING", content: "", revision: revision + 1 },
      );
      snapshot.executions.push({
        execution_id: id("execution"),
        conversation_id: conversationId,
        turn_id: assistantTurnId,
        provider_kind: "preview",
        connection_id: snapshot.conversation.connection_id,
        model_id: snapshot.conversation.model_id,
        status: "RUNNING",
        checkpoint_seq: 0,
        revision: revision + 1,
        started_at_ms: run.startedAt,
        finished_at_ms: null,
      });
      snapshot.conversation = { ...snapshot.conversation, revision: revision + 1, updated_at_ms: run.startedAt };
      previewConversations.set(conversationId, snapshot.conversation);
      previewDesktopRuns.set(run.runId, run);
      run.timer = setTimeout(() => settleDesktopRun(run, "COMPLETED"), 450);
      return desktopRunStatus(run);
    }
    case "runs.inspect": {
      const conversationId = String(payload.conversation_id ?? "");
      const run = typeof payload.run_id === "string"
        ? previewDesktopRuns.get(payload.run_id)
        : [...previewDesktopRuns.values()].reverse().find((item) => item.conversationId === conversationId);
      if (!run || run.conversationId !== conversationId) {
        throw new Error("RUN_NOT_FOUND: task status is unavailable");
      }
      return desktopRunStatus(run);
    }
    case "runs.cancel": {
      const run = previewDesktopRuns.get(String(payload.run_id ?? ""));
      if (!run || run.conversationId !== String(payload.conversation_id ?? "")) {
        throw new Error("RUN_NOT_FOUND: task status is unavailable");
      }
      if (run.status === "RUNNING") {
        run.status = "CANCELLING";
        run.cancelRequestedAt = now();
        if (run.timer) clearTimeout(run.timer);
        run.timer = setTimeout(() => settleDesktopRun(run, "CANCELLED"), 80);
      }
      return desktopRunStatus(run);
    }
    case "memory.search":
      return { records: [] };
    case "subagents.start": {
      const runId = id("preview-run");
      const run: PreviewRun = { runId, task: String(payload.task ?? "Inspect workspace"), startedAt: now(), cancelRequestedAt: null, events: [], status: "RUNNING", result: null };
      event(run, "TASK_REQUEST", 0, { role: "supervisor", summary: "Preview run started" });
      previewRuns.set(runId, run);
      return { schema: "aegis-desktop-subagents-start-v1", run_id: runId, status: "RUNNING", event_cursor: run.events.length };
    }
    case "subagents.events": {
      const run = previewRuns.get(String(payload.run_id));
      if (!run) return { schema: "aegis-desktop-subagent-events-v1", run_id: String(payload.run_id), latest_cursor: 0, oldest_cursor: 0, resync_required: false, events: [] };
      advanceRun(run);
      const cursor = Number(payload.cursor ?? 0);
      return { schema: "aegis-desktop-subagent-events-v1", run_id: run.runId, latest_cursor: run.events.length, oldest_cursor: run.events[0]?.cursor ?? 0, resync_required: cursor < (run.events[0]?.cursor ?? 0) - 1, events: run.events.filter((item) => item.cursor > cursor) };
    }
    case "subagents.status": {
      const run = previewRuns.get(String(payload.run_id));
      if (!run) throw new Error("SUBAGENT_NOT_FOUND: preview run not found");
      advanceRun(run);
      const result: SubagentStatusResult = { schema: "aegis-desktop-subagents-status-v1", run_id: run.runId, status: run.status, event_cursor: run.events.length, started_at_ms: run.startedAt, finished_at_ms: run.result ? now() : null, cancel_requested_at_ms: run.cancelRequestedAt, cancel_supported: true, thread_alive: !run.result, result: run.result, error: null };
      return result;
    }
    case "subagents.graph": {
      const run = previewRuns.get(String(payload.run_id));
      if (!run) throw new Error("SUBAGENT_NOT_FOUND: preview run not found");
      advanceRun(run);
      return { graph: previewSubagentGraph(run) };
    }
    case "subagents.cancel": {
      const run = previewRuns.get(String(payload.run_id));
      if (!run) throw new Error("SUBAGENT_NOT_FOUND: preview run not found");
      if (!run.result) {
        run.cancelRequestedAt = now();
        finishRun(run, "CANCELLED", "Preview run cancelled cooperatively.");
      }
      return { schema: "aegis-desktop-subagents-cancel-v1", run_id: run.runId, status: run.status, event_cursor: run.events.length };
    }
    default:
      return {};
  }
}
