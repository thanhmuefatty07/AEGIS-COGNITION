import { useEffect, useMemo, useRef, useState } from "react";
import {
  desktopRequest,
  parseConnectionList,
  parseConversationList,
  parseConversationInspection,
  parseConversationRecordResult,
  parseConversationSnapshot,
  parseMemorySearchResult,
  parseModelList,
  parseSourceSnapshot,
  parseSubagentCancelResult,
  parseSubagentEventPage,
  parseSubagentGraph,
  parseSubagentRunResult,
  parseSubagentStartResult,
  parseSubagentStatusResult,
  parseWorkspaceSnapshot,
  type Conversation,
  type ConversationInspection,
  type ConversationSnapshot,
  type MemoryRecord,
  type SourceSnapshot,
  type SubagentCancelResult,
  type SubagentEvent,
  type SubagentGraph,
  type SubagentRunResult,
  type SubagentStartResult,
  type SubagentStatusResult,
  type WorkspaceSnapshot,
} from "./protocol";
import WorkspaceGraphView from "./WorkspaceGraphView";
import { buildWorkspaceGraph, type WorkspaceGraph } from "./workspace_graph";

type Destination = "chat" | "settings" | "extensions";
type WorkTab = "files" | "map" | "activity" | "context";
const defaultEndpoint = "http://127.0.0.1:8080/v1";

function shortPath(value: string | null | undefined, length = 32) {
  if (!value) return "—";
  return value.length > length ? `…${value.slice(-length + 1)}` : value;
}

function formatTime(value: number) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(value);
}

function fileKind(path: string) {
  const extension = path.split(".").pop()?.toLowerCase();
  if (extension === "rs") return "RS";
  if (extension === "py") return "PY";
  if (["tsx", "ts", "jsx", "js"].includes(extension ?? "")) return "JS";
  if (["json", "toml", "yaml", "yml"].includes(extension ?? "")) return "CFG";
  return "FILE";
}

type IconName =
  | "logo"
  | "plus"
  | "chevron-left"
  | "chevron-right"
  | "chevron-down"
  | "search"
  | "sessions"
  | "folder"
  | "branch"
  | "extensions"
  | "settings"
  | "panel"
  | "more"
  | "spark"
  | "attach"
  | "arrow-up"
  | "check"
  | "activity"
  | "map"
  | "files"
  | "circle"
  | "external";

function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "logo": return <svg {...common}><path d="M12 3 20 7.5v9L12 21l-8-4.5v-9L12 3Z" /><path d="m8 9 4-2 4 2v6l-4 2-4-2V9Z" /></svg>;
    case "plus": return <svg {...common}><path d="M12 5v14M5 12h14" /></svg>;
    case "chevron-left": return <svg {...common}><path d="m14.5 5-7 7 7 7" /></svg>;
    case "chevron-right": return <svg {...common}><path d="m9.5 5 7 7-7 7" /></svg>;
    case "chevron-down": return <svg {...common}><path d="m6 9 6 6 6-6" /></svg>;
    case "search": return <svg {...common}><circle cx="10.8" cy="10.8" r="6.3" /><path d="m16 16 4.2 4.2" /></svg>;
    case "sessions": return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 9h8M8 13h5M8 17h3" /></svg>;
    case "folder": return <svg {...common}><path d="M3.5 7.5h6l1.8 2h9.2v7.8a2.2 2.2 0 0 1-2.2 2.2H5.7a2.2 2.2 0 0 1-2.2-2.2V7.5Z" /><path d="M3.5 7.5V6.7A2.2 2.2 0 0 1 5.7 4.5h3l1.8 2h6.8" /></svg>;
    case "branch": return <svg {...common}><circle cx="7" cy="5" r="2" /><circle cx="17" cy="19" r="2" /><path d="M7 7v5a7 7 0 0 0 7 7h1" /><path d="M17 7v3a5 5 0 0 1-5 5H9" /></svg>;
    case "extensions": return <svg {...common}><path d="M8 4v4M16 4v4M4 8h16M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z" /><path d="M8 13h.01M12 13h.01M16 13h.01M8 17h.01M12 17h.01" /></svg>;
    case "settings": return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H6v-2.4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L7.3 8l1.7-1.7.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5V5h2.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 8l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2v2.4h-.2a1.7 1.7 0 0 0-1.5 1Z" /></svg>;
    case "panel": return <svg {...common}><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M15 5v14" /></svg>;
    case "more": return <svg {...common}><circle cx="6" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="18" cy="12" r="1" fill="currentColor" stroke="none" /></svg>;
    case "spark": return <svg {...common}><path d="m12 3 1.4 6.6L20 12l-6.6 1.4L12 20l-1.4-6.6L4 12l6.6-2.4L12 3Z" /></svg>;
    case "attach": return <svg {...common}><path d="m9 12.5 5.9-5.9a3 3 0 0 1 4.2 4.2l-7.7 7.7a4.4 4.4 0 0 1-6.2-6.2l7.5-7.5" /></svg>;
    case "arrow-up": return <svg {...common}><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5" /></svg>;
    case "check": return <svg {...common}><path d="m5 12 4.3 4.3L19 6.7" /></svg>;
    case "activity": return <svg {...common}><path d="M4 12h3l2-6 4 12 2-6h5" /></svg>;
    case "map": return <svg {...common}><path d="m4 6 5-2 6 2 5-2v14l-5 2-6-2-5 2V6Z" /><path d="M9 4v14M15 6v14" /></svg>;
    case "files": return <svg {...common}><path d="M6 3.8h8l4 4V20H6a2 2 0 0 1-2-2V5.8a2 2 0 0 1 2-2Z" /><path d="M14 3.8v4h4M8 12h6M8 16h6" /></svg>;
    case "circle": return <svg {...common}><circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none" /></svg>;
    case "external": return <svg {...common}><path d="M14 5h5v5M19 5l-8 8" /><path d="M17 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h5" /></svg>;
  }
}

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [workspaceGraph, setWorkspaceGraph] = useState<WorkspaceGraph | null>(null);
  const [sourceSnapshot, setSourceSnapshot] = useState<SourceSnapshot | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationSnapshot | null>(null);
  const [conversationInspection, setConversationInspection] = useState<ConversationInspection | null>(null);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [endpoint, setEndpoint] = useState(defaultEndpoint);
  const [modelId, setModelId] = useState("local-model");
  const [connectionSaved, setConnectionSaved] = useState(false);
  const [destination, setDestination] = useState<Destination>("chat");
  const [workTab, setWorkTab] = useState<WorkTab>("files");
  const [workPanelOpen, setWorkPanelOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [fileFilter, setFileFilter] = useState("");
  const [settingsSection, setSettingsSection] = useState("General");
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subagentBusy, setSubagentBusy] = useState(false);
  const [subagentResult, setSubagentResult] = useState<SubagentRunResult | null>(null);
  const [subagentRunId, setSubagentRunId] = useState<string | null>(null);
  const [subagentStatus, setSubagentStatus] = useState<SubagentStatusResult | null>(null);
  const [subagentEvents, setSubagentEvents] = useState<SubagentEvent[]>([]);
  const [subagentGraph, setSubagentGraph] = useState<SubagentGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const bootstrapStarted = useRef(false);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    void bootstrap();
  }, []);

  useEffect(() => {
    const runId = subagentRunId;
    if (!runId) return;
    let stopped = false;
    let cursor = 0;
    let timer: number | undefined;

    async function pollSubagent() {
      try {
        const page = await desktopRequest("subagents.events", {
          run_id: runId,
          cursor,
          max_messages: 64,
        }, parseSubagentEventPage);
        if (stopped) return;
        cursor = page.latest_cursor;
        setSubagentEvents((current) => {
          const merged = new Map<number, SubagentEvent>(page.resync_required ? [] : current.map((event) => [event.cursor, event]));
          page.events.forEach((event) => merged.set(event.cursor, event));
          return [...merged.values()].sort((left, right) => left.cursor - right.cursor).slice(-128);
        });
        const status = await desktopRequest("subagents.status", { run_id: runId }, parseSubagentStatusResult);
        if (stopped) return;
        setSubagentStatus(status);
        const graph = await desktopRequest("subagents.graph", { run_id: runId }, parseSubagentGraph);
        if (stopped) return;
        setSubagentGraph(graph);
        if (status.result !== null || status.status !== "RUNNING") {
          setSubagentResult(status.result);
          setSubagentBusy(false);
          setSubagentRunId(null);
          return;
        }
        timer = window.setTimeout(() => void pollSubagent(), 250);
      } catch (reason) {
        if (stopped) return;
        setError(reason instanceof Error ? reason.message : "The parallel run status could not be read");
        setSubagentBusy(false);
        setSubagentRunId(null);
      }
    }

    void pollSubagent();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [subagentRunId]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "b") {
        event.preventDefault();
        setSidebarCollapsed((current) => !current);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") searchRef.current?.blur();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function bootstrap() {
    try {
      setError(null);
      const snapshot = await desktopRequest("workspace.open", {}, parseWorkspaceSnapshot);
      setWorkspace(snapshot);
      await loadWorkspaceGraph();
      await configureConnection();
      const records = await loadConversations();
      if (records.length > 0) await selectConversation(records[0].conversation_id);
      else await createConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to open the local workspace");
    }
  }

  async function loadWorkspaceGraph() {
    try {
      const snapshot = await desktopRequest("workspace.source_snapshot", {}, parseSourceSnapshot);
      setSourceSnapshot(snapshot);
      setWorkspaceGraph(buildWorkspaceGraph(snapshot));
    } catch (reason) {
      setSourceSnapshot(null);
      setWorkspaceGraph(null);
      setError(reason instanceof Error ? reason.message : "Unable to map the local workspace");
    }
  }

  async function configureConnection() {
    const connections = await desktopRequest("connections.list", {}, parseConnectionList);
    const existingConnection = connections.find((item) => item.connection_id === "local");
    const payload: Record<string, unknown> = {
      connection_id: "local",
      provider_kind: "openai-compatible",
      endpoint,
      protocol: "chat-completions",
    };
    if (existingConnection) {
      payload.expected_revision = existingConnection.revision;
      const models = await desktopRequest("models.list", { connection_id: "local" }, parseModelList);
      if (!models.some((item) => item.model_id === modelId)) payload.model_id = modelId;
    } else {
      payload.model_id = modelId;
    }
    await desktopRequest("connections.save", payload, (value) => value);
    setConnectionSaved(true);
  }

  async function loadConversations(): Promise<Conversation[]> {
    const records = await desktopRequest("conversations.list", {}, parseConversationList);
    const sorted = [...records].sort((left, right) => right.updated_at_ms - left.updated_at_ms);
    setConversations(sorted);
    return sorted;
  }

  async function createConversation() {
    const id = `desktop-${crypto.randomUUID()}`;
    const record = await desktopRequest("conversations.create", {
      conversation_id: id,
      title: "New thread",
      connection_id: "local",
      model_id: modelId,
    }, parseConversationRecordResult);
    setConversations((current) => [record, ...current.filter((item) => item.conversation_id !== id)]);
    await selectConversation(id);
    setDestination("chat");
  }

  async function selectConversation(id: string) {
    setActiveConversationId(id);
    setConversationInspection(null);
    setSubagentResult(null);
    setSubagentRunId(null);
    setSubagentStatus(null);
    setSubagentEvents([]);
    setSubagentGraph(null);
    await loadConversation(id);
  }

  async function loadConversation(id: string) {
    try {
      const [snapshot, inspection] = await Promise.all([
        desktopRequest("conversations.read", { conversation_id: id }, parseConversationSnapshot),
        desktopRequest("conversations.inspect", { conversation_id: id }, parseConversationInspection),
      ]);
      setConversation(snapshot);
      setConversationInspection(inspection);
      if (snapshot.conversation.model_id !== modelId) setConnectionSaved(false);
      setModelId(snapshot.conversation.model_id);
    } catch (reason) {
      setConversation(null);
      if (reason instanceof Error && !reason.message.includes("conversation not found")) setError(reason.message);
    }
  }

  async function sendMessage() {
    const text = message.trim();
    if (!text || busy || !activeConversationId) return;
    setBusy(true);
    try {
      setError(null);
      const snapshot = await desktopRequest("conversations.send", {
        conversation_id: activeConversationId,
        message: text,
        mode,
      }, parseConversationSnapshot);
      setConversation(snapshot);
      setMessage("");
      await loadConversations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation failed");
    } finally {
      setBusy(false);
    }
  }

  async function loadMemoriesForFile(node: { path: string | null }): Promise<MemoryRecord[]> {
    if (!node.path) return [];
    return desktopRequest("memory.search", { query: node.path, top_k: 6, scope_kind: "USER_PRIVATE" }, parseMemorySearchResult);
  }

  async function runSubagents() {
    const task = message.trim() || "Inspect this workspace in parallel and summarize the relevant evidence.";
    if (subagentBusy || !activeConversationId || !runtimeReady || !connectionSaved) return;
    setSubagentBusy(true);
    let started = false;
    try {
      setError(null);
      const result = await desktopRequest("subagents.start", {
        task,
        conversation_id: activeConversationId,
        max_concurrency: 4,
      }, parseSubagentStartResult);
      setSubagentResult(null);
      setSubagentStatus({
        schema: "aegis-desktop-subagents-status-v1",
        run_id: result.run_id,
        status: result.status,
        event_cursor: result.event_cursor,
        started_at_ms: Date.now(),
        finished_at_ms: null,
        cancel_requested_at_ms: null,
        cancel_supported: true,
        thread_alive: true,
        result: null,
        error: null,
      });
      setSubagentEvents([]);
      setSubagentRunId(result.run_id);
      setMessage("");
      started = true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The parallel run failed");
    } finally {
      if (!started) setSubagentBusy(false);
    }
  }

  async function cancelSubagents() {
    if (!subagentRunId) return;
    try {
      const result = await desktopRequest<SubagentCancelResult>(
        "subagents.cancel",
        { run_id: subagentRunId },
        parseSubagentCancelResult,
      );
      setSubagentStatus((current) => current ? { ...current, status: result.status, event_cursor: result.event_cursor } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The parallel run could not be cancelled");
    }
  }

  const previewMode = workspace?.preview_mode === true;
  const runtimeReady = previewMode || workspace?.native_runtime_available === true;
  const activeTitle = conversation?.conversation.title ?? "New thread";
  const activeConversation = conversations.find((item) => item.conversation_id === activeConversationId);
  const filteredFiles = useMemo(() => {
    const query = fileFilter.trim().toLowerCase();
    return (sourceSnapshot?.files ?? [])
      .filter((file) => !query || file.relative_path.toLowerCase().includes(query))
      .sort((left, right) => left.relative_path.localeCompare(right.relative_path))
      .slice(0, 120);
  }, [fileFilter, sourceSnapshot]);
  const activityItems = useMemo(() => {
    const items: Array<{ icon: IconName; title: string; detail: string; timestamp: number }> = [
      {
        icon: "check",
        title: "Workspace opened",
        detail: "Rust host admitted the local session",
        timestamp: workspace ? (sourceSnapshot?.created_at_ms ?? 1) : 0,
      },
      {
        icon: "map",
        title: "Source map ready",
        detail: `${workspaceGraph?.stats.files ?? 0} files available to inspect`,
        timestamp: sourceSnapshot?.created_at_ms ?? 0,
      },
    ];
    if (conversation) {
      for (const execution of conversation.executions) {
        items.push({
          icon: execution.status === "COMPLETED" ? "check" : "activity",
          title: `Provider execution ${execution.status.toLowerCase()}`,
          detail: `${execution.model_id} · checkpoint ${execution.checkpoint_seq}`,
          timestamp: execution.finished_at_ms ?? execution.started_at_ms,
        });
      }
      for (const checkpoint of conversation.checkpoints) {
        items.push({
          icon: "activity",
          title: `Checkpoint ${checkpoint.state.toLowerCase()}`,
          detail: `Sequence ${checkpoint.sequence} · execution ${checkpoint.execution_id.slice(-12)}`,
          timestamp: checkpoint.created_at_ms,
        });
      }
      for (const toolCall of conversation.tool_calls) {
        items.push({
          icon: toolCall.status === "COMPLETED" ? "check" : "activity",
          title: `Tool ${toolCall.status.toLowerCase()}`,
          detail: `${toolCall.tool_name} · ${toolCall.call_id.slice(-12)}`,
          timestamp: toolCall.completed_at_ms ?? toolCall.created_at_ms,
        });
      }
    }
    return items
      .filter((item) => item.timestamp > 0)
      .sort((left, right) => right.timestamp - left.timestamp)
      .slice(0, 8);
  }, [conversation, sourceSnapshot, workspace, workspaceGraph]);

  function openDestination(next: Destination) {
    setDestination(next);
    if (next !== "chat") setWorkTab("files");
  }

  function renderSidebar() {
    return (
      <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`} aria-label="Workspace navigation">
        <div className="sidebar-brand">
          <div className="brand-mark" aria-hidden="true"><Icon name="logo" size={17} /></div>
          {!sidebarCollapsed && <div className="brand-copy"><strong>AEGIS</strong><span>LOCAL AGENT</span></div>}
          <button className="sidebar-toggle" type="button" onClick={() => setSidebarCollapsed((current) => !current)} aria-label="Toggle sidebar"><Icon name={sidebarCollapsed ? "chevron-right" : "chevron-left"} size={15} /></button>
        </div>
        <button className="new-task" type="button" onClick={() => void createConversation()} disabled={!workspace || busy}>
          <Icon name="plus" size={15} />{!sidebarCollapsed && <span>New thread</span>}
        </button>
        <div className="sidebar-scroll">
          <div className="sidebar-group">
            {!sidebarCollapsed && <div className="sidebar-label"><span>THREADS</span><span className="sidebar-count">{conversations.length}</span></div>}
            <button className={`sidebar-nav ${destination === "chat" ? "active" : ""}`} type="button" onClick={() => openDestination("chat")} title="Threads">
              <span className="nav-icon"><Icon name="sessions" size={15} /></span>{!sidebarCollapsed && <span>Threads</span>}
            </button>
            {!sidebarCollapsed && <div className="session-list">
              {conversations.length === 0 ? <p className="sidebar-empty">No sessions yet.</p> : conversations.map((item) => (
                <button className={`session-row ${item.conversation_id === activeConversationId ? "active" : ""}`} key={item.conversation_id} type="button" onClick={() => void selectConversation(item.conversation_id)}>
                  <span className="session-dot"><Icon name="circle" size={9} /></span>
                  <span className="session-info"><strong>{item.title}</strong><small>{formatTime(item.updated_at_ms)}</small></span>
                </button>
              ))}
            </div>}
          </div>
          <div className="sidebar-group project-group">
            {!sidebarCollapsed && <div className="sidebar-label">PROJECTS</div>}
            <button className="project-row" type="button" onClick={() => { openDestination("chat"); setWorkTab("files"); setWorkPanelOpen(true); }} title={workspace?.workspace_path ?? "Workspace"}>
              <span className="project-chevron"><Icon name="chevron-down" size={13} /></span><span className="project-icon"><Icon name="folder" size={14} /></span>{!sidebarCollapsed && <span className="project-name">{shortPath(workspace?.workspace_path, 23)}</span>}
            </button>
            {!sidebarCollapsed && <div className="project-subrow"><span className="project-subdot"><Icon name="circle" size={8} /></span>{activeTitle}</div>}
          </div>
        </div>
        <div className="sidebar-bottom">
          <button className={`utility-row ${destination === "extensions" ? "active" : ""}`} type="button" onClick={() => openDestination("extensions")} title="Skills"><span><Icon name="extensions" size={15} /></span>{!sidebarCollapsed && <span>Skills</span>}</button>
          <button className={`utility-row ${destination === "settings" ? "active" : ""}`} type="button" onClick={() => openDestination("settings")} title="Settings"><span><Icon name="settings" size={15} /></span>{!sidebarCollapsed && <span>Settings</span>}</button>
          {!sidebarCollapsed && <div className="sidebar-status"><span className={`status-dot ${runtimeReady ? "ready" : "attention"}`} />{previewMode ? "Preview · local" : runtimeReady ? "Local host online" : "Opening local host"}</div>}
          {!sidebarCollapsed && <div className="sidebar-version">AEGIS 0.1.0 · protocol v1</div>}
        </div>
      </aside>
    );
  }

  function renderConversation() {
    function renderTurnContent(turn: ConversationSnapshot["turns"][number]) {
      const parts = conversation?.parts.filter((part) => part.turn_id === turn.turn_id) ?? [];
      if (parts.length === 0) return <p>{turn.content || "…"}</p>;
      return (
        <div className="message-parts">
          {parts.map((part) => part.kind === "TEXT" ? (
            <p key={`${part.turn_id}-${part.part_index}`}>{part.content}</p>
          ) : (
            <div className="message-part-record" key={`${part.turn_id}-${part.part_index}`}>
              <span>{part.kind}</span>
              <code>{part.content}</code>
            </div>
          ))}
        </div>
      );
    }

    function renderSubagentActivity() {
      if (!subagentRunId && subagentEvents.length === 0) return null;
      const status = subagentStatus?.status ?? "RUNNING";
      return (
        <article className="subagent-live" aria-label="Live subagent activity">
          <div className="subagent-result-header">
            <div><span className="eyebrow">LIVE WORKFLOW</span><strong>{status}</strong></div>
            <div className="subagent-live-actions"><span className="status-chip"><i /> {subagentEvents.length} events</span>{status === "RUNNING" && <button className="subagent-cancel" type="button" onClick={() => void cancelSubagents()}>Stop</button>}</div>
          </div>
          <div className="subagent-event-list">
            {subagentEvents.slice(-8).map((event) => {
              const payload = event.payload;
              const summary = typeof payload.summary === "string" ? payload.summary : "worker admitted";
              const detail = event.message_kind === "TASK_REQUEST"
                ? `Worker ${event.task_id} started · ${String(payload.role ?? "task")}`
                : `Worker ${event.task_id} · ${String(payload.status ?? "result")}`;
              return <div className="subagent-event" key={event.cursor}><span className="activity-icon"><Icon name={event.message_kind === "TASK_RESULT" ? "check" : "activity"} size={12} /></span><div><strong>{detail}</strong><small>{summary.slice(0, 180)}</small></div></div>;
            })}
          </div>
          {subagentGraph && subagentGraph.nodes.length > 0 && <div className="subagent-graph" aria-label="Subagent task graph">
            <div className="eyebrow">TASK GRAPH</div>
            {subagentGraph.nodes.map((node) => <div className="subagent-graph-row" key={node.task_id}><span className={`worker-dot ${node.status.toLowerCase()}`} /><strong>Worker {node.task_id}</strong><span>{node.role || "task"}</span><em>{node.status}</em></div>)}
          </div>}
        </article>
      );
    }

    return (
      <section className="chat-view" aria-label="Conversation">
        <header className="conversation-topbar">
          <div className="conversation-title">
            <span className="workspace-crumb"><Icon name="folder" size={13} />{shortPath(workspace?.workspace_path, 38)}</span>
            <div className="title-line"><h1>{activeTitle}</h1><span className="status-chip"><i /> Local</span></div>
          </div>
          <div className="topbar-actions">
            <span className="revision-label">r{conversation?.conversation.revision ?? "—"}</span>
            <button className={`topbar-text-action ${workPanelOpen ? "active" : ""}`} type="button" onClick={() => { setWorkPanelOpen(true); setWorkTab("files"); }} aria-label="Open project files">Open</button>
            <button className="topbar-icon" type="button" onClick={() => setWorkPanelOpen((current) => !current)} aria-label="Toggle work panel" aria-expanded={workPanelOpen}><Icon name="panel" size={16} /></button>
            <button className="topbar-icon" type="button" onClick={() => openDestination("settings")} aria-label="Open settings"><Icon name="more" size={16} /></button>
          </div>
        </header>
        {error && <div className="error" role="alert"><span className="error-mark">!</span>{error}</div>}
        <div className="chat-scroll">
          <div className="chat-column">
            <div className="chat-intro"><div className="intro-mark"><Icon name="spark" size={17} /></div><div><strong>AEGIS is ready</strong><span>{mode === "mock" ? "Safe preview mode" : "Live provider mode"} · {conversationInspection?.context.item_count ?? 0} context items</span></div><span className="context-chip">{runtimeReady ? "Local" : "Opening"}</span></div>
            {renderSubagentActivity()}
            {subagentResult && <article className="subagent-result" aria-label="Subagent run result"><div className="subagent-result-header"><div><span className="eyebrow">PARALLEL RUN</span><strong>{subagentResult.status}</strong></div><span className="status-chip"><i /> {subagentResult.child_results.length} workers</span></div><p>{subagentResult.root_output}</p><div className="subagent-workers">{subagentResult.child_results.map((worker) => <span className={`worker-chip ${worker.status.toLowerCase()}`} key={`${worker.task_id}-${worker.packet_hash}`}><i />Worker {worker.task_id} · {worker.status}</span>)}</div><small>Graph {shortPath(subagentResult.graph_hash, 18)} · {subagentResult.graph_authority}</small></article>}
            {conversationInspection?.approvals.map((approval) => <article className="approval-card" key={approval.approval_id} aria-label="Approval required"><div><span className="eyebrow">REVIEW REQUIRED</span><strong>{approval.tool_name}</strong></div><span className="status-chip warning"><i />{approval.status}</span><p>AEGIS is waiting for a policy decision. Arguments are redacted at the renderer boundary.</p><small>Risk {approval.risk} · {approval.argument_keys.length} argument keys</small></article>)}
            {conversation?.turns.length ? conversation.turns.map((turn) => (
              <article className={`message-row ${turn.role}`} key={turn.turn_id}>
                <div className="message-avatar">{turn.role === "user" ? "Y" : "A"}</div>
                <div className="message-content"><div className="message-meta"><strong>{turn.role === "user" ? "You" : "AEGIS"}</strong><span>r{turn.revision}</span></div>{renderTurnContent(turn)}</div>
              </article>
            )) : <div className="empty-chat"><h2><span className="empty-kicker">Let's build</span><span className="empty-project">{shortPath(workspace?.workspace_path, 28)} <Icon name="chevron-down" size={14} /></span></h2><p>Ask AEGIS to research, inspect, or reason over this workspace.</p><div className="suggestion-block"><span className="suggestion-caption">Explore more</span><div className="suggestion-row"><button type="button" onClick={() => setMessage("Summarize this workspace")}><Icon name="spark" size={14} /><span>Summarize workspace</span></button><button type="button" onClick={() => setMessage("Inspect the current project")}><Icon name="search" size={14} /><span>Inspect project</span></button><button type="button" onClick={() => void runSubagents()} disabled={!runtimeReady || !connectionSaved || subagentBusy}><Icon name="extensions" size={14} /><span>Run parallel workers</span></button></div></div></div>}
          </div>
        </div>
        <div className="composer-wrap">
          <div className="composer-pill">
            <div className="composer-toolbar"><button className="mode-chip" type="button" onClick={() => setMode(mode === "mock" ? "live" : "mock")}><span className="mode-dot" />{mode === "mock" ? "Agent · Preview" : "Agent · Live"}<span className="chevron"><Icon name="chevron-down" size={12} /></span></button><span className="composer-model">{modelId}</span><span className="composer-spacer" /><button className="composer-tool" type="button" onClick={() => void runSubagents()} disabled={subagentBusy || !runtimeReady || !connectionSaved || !activeConversationId} aria-label="Run parallel workers" title="Run parallel workers"><Icon name="extensions" size={15} /></button><button className="composer-tool" type="button" onClick={() => { setWorkPanelOpen(true); setWorkTab("files"); }} aria-label="Browse files"><Icon name="attach" size={15} /></button></div>
            <textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} placeholder="Ask AEGIS anything…" rows={1} disabled={busy || subagentBusy || !activeConversationId} />
            <div className="composer-bottom"><span>Ctrl/⌘ Enter to send · Shift Enter for a new line</span><button className="send-button" type="button" onClick={() => void sendMessage()} disabled={busy || subagentBusy || !message.trim() || !activeConversationId}>{busy || subagentBusy ? <span className="send-loading" /> : <Icon name="arrow-up" size={16} />}</button></div>
          </div>
          <div className="composer-status" aria-label="Workspace environment"><div className="environment-status"><span className="environment-option active">Local</span><span className="environment-option">Checkout</span></div><span className="branch-status"><Icon name="branch" size={12} />{shortPath(sourceSnapshot?.identity.branch, 32)}</span></div>
        </div>
      </section>
    );
  }

  function renderWorkPanel() {
    const selected = sourceSnapshot?.files.find((file) => file.relative_path === selectedFile);
    return (
      <aside className="work-panel" aria-label="Workspace panel">
        <div className="work-panel-header"><div className="work-tabs" role="tablist" aria-label="Workspace panel tabs"><button className={workTab === "files" ? "active" : ""} type="button" onClick={() => setWorkTab("files")}><Icon name="files" size={14} />Files</button><button className={workTab === "map" ? "active" : ""} type="button" onClick={() => setWorkTab("map")}><Icon name="map" size={14} />Map</button><button className={workTab === "activity" ? "active" : ""} type="button" onClick={() => setWorkTab("activity")}><Icon name="activity" size={14} />Activity</button><button className={workTab === "context" ? "active" : ""} type="button" onClick={() => setWorkTab("context")}><Icon name="spark" size={14} />Context</button></div><button className="panel-more" type="button" onClick={() => setWorkPanelOpen(false)} aria-label="Close workspace panel"><Icon name="chevron-right" size={15} /></button></div>
        {workTab === "files" && <div className="file-panel"><div className="panel-title"><div><span className="eyebrow">WORKSPACE</span><strong>Project files</strong></div><span className="file-count">{sourceSnapshot?.files.length ?? "—"}</span></div><label className="file-search"><Icon name="search" size={14} /><input ref={searchRef} value={fileFilter} onChange={(event) => setFileFilter(event.target.value)} placeholder="Search files" /></label><div className="file-tree">{filteredFiles.length ? filteredFiles.map((file) => <button className={`file-row ${selectedFile === file.relative_path ? "active" : ""}`} key={file.relative_path} type="button" onClick={() => setSelectedFile(file.relative_path)}><span className="file-kind">{fileKind(file.relative_path)}</span><span className="file-name">{file.relative_path}</span></button>) : <p className="panel-empty">No indexed files match this search.</p>}</div>{selected && <div className="file-inspector"><span className="eyebrow">SELECTED FILE</span><strong>{selected.relative_path}</strong><div><span>{selected.language || "unknown"}</span><span>{Math.round(selected.size_bytes / 1024)} KB</span></div><p>{selected.extraction_status === "ok" ? "Symbols and imports are indexed." : selected.extraction_status}</p></div>}</div>}
        {workTab === "map" && <div className="map-panel">{workspaceGraph && sourceSnapshot ? <WorkspaceGraphView graph={workspaceGraph} loadMemories={loadMemoriesForFile} /> : <p className="panel-empty">The source map is opening.</p>}</div>}
        {workTab === "activity" && <div className="activity-panel"><div className="panel-title"><div><span className="eyebrow">SESSION</span><strong>Activity</strong></div><span className="status-chip"><i /> {conversation?.executions.some((item) => item.status === "RUNNING") ? "Live" : "Ready"}</span></div>{conversationInspection?.timeline.length ? conversationInspection.timeline.slice().reverse().map((item) => <div className="activity-item" key={item.event_id}><span className="activity-icon"><Icon name={item.kind === "EXECUTION" && item.status === "COMPLETED" ? "check" : "activity"} size={13} /></span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>) : activityItems.length ? activityItems.map((item) => <div className="activity-item" key={`${item.title}-${item.timestamp}-${item.detail}`}><span className="activity-icon"><Icon name={item.icon} size={13} /></span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>) : <p className="panel-empty">No session activity yet.</p>}</div>}
        {workTab === "context" && <div className="context-panel"><div className="panel-title"><div><span className="eyebrow">CONTEXT COCKPIT</span><strong>What AEGIS used</strong></div><span className="status-chip"><i />{conversationInspection?.context.status ?? "Unknown"}</span></div>{conversationInspection ? <><div className="context-metrics"><div><span>Items</span><strong>{conversationInspection.context.item_count ?? 0}</strong></div><div><span>Tokens</span><strong>{conversationInspection.context.token_count ?? "—"}</strong></div><div><span>History</span><strong>{conversationInspection.context.history_turn_count}</strong></div></div><div className="context-detail"><span>Source revision</span><code>{shortPath(conversationInspection.context.source_revision, 22)}</code></div><div className="context-detail"><span>Manifest</span><code>{shortPath(conversationInspection.context.context_manifest_hash, 22)}</code></div><div className="context-detail"><span>Selector</span><code>{conversationInspection.context.selection_backend ?? "—"}</code></div><p className="context-redaction">Sensitive prompt, tool arguments, and provider output remain outside this observer view.</p></> : <p className="panel-empty">Context inspection is opening.</p>}</div>}
      </aside>
    );
  }

  function renderSettings() {
    const sections = ["General", "Connections", "Workspace", "Keyboard"];
    return <main className="destination-page"><header className="destination-header"><div><span className="eyebrow">AEGIS / SETTINGS</span><h1>Settings</h1><p>Configure the local workspace without leaving the desktop shell.</p></div><span className="status-chip"><i /> Local-first</span></header><div className="settings-layout"><nav className="settings-nav" aria-label="Settings sections">{sections.map((section) => <button className={settingsSection === section ? "active" : ""} key={section} type="button" onClick={() => setSettingsSection(section)}>{section}<Icon name="chevron-right" size={13} /></button>)}</nav><section className="settings-content"><div className="settings-heading"><h2>{settingsSection}</h2><p>{settingsSection === "Connections" ? "Choose the provider endpoint used by local conversations." : "Small controls that keep the workspace predictable and focused."}</p></div>{settingsSection === "Connections" ? <><div className="settings-card"><div className="card-heading"><div><strong>Local provider</strong><span>OpenAI-compatible chat endpoint</span></div><span className={connectionSaved ? "saved" : "unsaved"}>{connectionSaved ? "Saved" : "Not saved"}</span></div><label htmlFor="settings-endpoint">Endpoint</label><input id="settings-endpoint" value={endpoint} onChange={(event) => { setEndpoint(event.target.value); setConnectionSaved(false); }} /><label htmlFor="settings-model">Model</label><input id="settings-model" value={modelId} onChange={(event) => { setModelId(event.target.value); setConnectionSaved(false); }} /><div className="card-actions"><button className="primary-action" type="button" onClick={() => void configureConnection()} disabled={!endpoint.trim() || !modelId.trim()}>Save connection</button><span>Secrets stay outside the renderer.</span></div></div><div className="settings-card compact-card"><strong>Authority</strong><p>The renderer sends versioned commands. Rust owns admission; the local service owns provider access.</p></div></> : <div className="settings-card settings-summary"><div className="summary-row"><span>Workspace</span><strong>{workspace?.workspace_path ?? "Opening…"}</strong></div><div className="summary-row"><span>Profile</span><strong>{workspace?.profile_id ?? "—"}</strong></div><div className="summary-row"><span>Runtime</span><strong className={runtimeReady ? "good" : "warn"}>{runtimeReady ? "Native host online" : "Opening local host"}</strong></div><div className="summary-row"><span>Keyboard</span><strong>Ctrl/⌘ B sidebar · Ctrl/⌘ K search</strong></div></div>}</section></div></main>;
  }

  function renderExtensions() {
    const cards = [
      ["Source map", "Inspect indexed files, symbols, imports, and lineage.", "Connected", "map"],
      ["Memory", "Search workspace-aware local memory from the work panel.", "Connected", "chat"],
      ["Conversations", "Append-only local sessions with revisioned turns.", "Connected", "chat"],
      ["Provider adapter", "Use the configured OpenAI-compatible local endpoint.", connectionSaved ? "Connected" : "Needs setup", "settings"],
      ["Browser research", "Public research adapters are available; browser capture remains explicitly host-injected.", "Needs setup", "extensions"],
      ["Subagents", "Bounded parallel workers with native graph validation and hash-bound result packets.", runtimeReady && connectionSaved ? "Connected" : "Needs setup", "chat"],
    ];
    const selected = cards.find(([title]) => title === selectedSkill);
    return <main className="destination-page extensions-page"><header className="destination-header"><div><span className="eyebrow">AEGIS / SKILLS</span><h1>Skills</h1><p>Local capabilities that AEGIS can inspect and use without hiding their authority boundary.</p></div><button className="outline-action" type="button" onClick={() => openDestination("settings")}>Configure connections</button></header><div className="extension-toolbar"><label className="extension-search"><Icon name="search" size={14} /><input placeholder="Search skills" /></label><div className="extension-filters"><span className="filter-label">Installed</span><span className="filter-label">Local-first</span></div></div>{selected && <article className="skill-detail"><div><span className="eyebrow">CAPABILITY DETAILS</span><h2>{selected[0]}</h2><p>{selected[1]}</p><small>{selected[2]} · Host boundary enforced</small></div><button type="button" onClick={() => setSelectedSkill(null)} aria-label="Close skill details">Close</button></article>}<div className="extension-grid">{cards.map(([title, description, status, target]) => <article className="extension-card" key={title}><div className="extension-icon"><Icon name={title === "Source map" ? "map" : title === "Memory" ? "spark" : title === "Conversations" ? "sessions" : title === "Provider adapter" ? "external" : title === "Browser research" ? "search" : "extensions"} size={16} /></div><div className="extension-card-body"><div className="extension-card-title"><h2>{title}</h2><span className={status === "Connected" ? "connected" : "pending"}>{status}</span></div><p>{description}</p><button type="button" onClick={() => target === "settings" ? openDestination("settings") : target === "map" ? (openDestination("chat"), setWorkTab("map"), setWorkPanelOpen(true)) : target === "chat" ? openDestination("chat") : setSelectedSkill(title)}>{status === "Connected" ? "Open" : "View details"}<span><Icon name="chevron-right" size={13} /></span></button></div></article>)}</div></main>;
  }

  return <div className={`app-frame ${destination !== "chat" ? "destination-frame" : ""} ${workPanelOpen && destination === "chat" ? "work-panel-open" : ""}`}>
    {renderSidebar()}
    {destination === "chat" ? <>{renderConversation()}{workPanelOpen && renderWorkPanel()}</> : destination === "settings" ? renderSettings() : renderExtensions()}
  </div>;
}
