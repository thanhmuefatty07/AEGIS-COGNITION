import type {
  Conversation,
  ConversationSnapshot,
  ConversationInspection,
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

const previewConversations = new Map<string, Conversation>();
const previewSnapshots = new Map<string, ConversationSnapshot>();
const previewRuns = new Map<string, PreviewRun>();
const previewConnections = new Map<string, Record<string, unknown>>();

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
    redaction: "provider prompts, raw tool arguments, tool results, and continuation bodies are redacted",
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
    case "connections.list":
      return { records: [...previewConnections.values()] };
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
      return { models: [{ connection_id: "local", model_id: "local-model", family: "preview", capabilities: ["chat"], context_limit: null, output_limit: null, source: "preview", revision: 1, observed_at_ms: now() }] };
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
      const revision = snapshot.conversation.revision + 1;
      const userTurn = { turn_id: id("turn"), role: "user", status: "COMPLETED", content: text, revision };
      const assistantTurn = { turn_id: id("turn"), role: "assistant", status: "COMPLETED", content: `Preview response: I received “${text}”.`, revision: revision + 1 };
      snapshot.turns.push(userTurn, assistantTurn);
      snapshot.conversation = { ...snapshot.conversation, revision: revision + 1, updated_at_ms: now() };
      previewConversations.set(conversationId, snapshot.conversation);
      return snapshot;
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
