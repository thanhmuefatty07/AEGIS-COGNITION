import { useEffect, useMemo, useRef, useState } from "react";
import {
  desktopRequest,
  parseConversationList,
  parseConversationRecordResult,
  parseConversationSnapshot,
  parseMemorySearchResult,
  parseSourceSnapshot,
  parseWorkspaceSnapshot,
  type Conversation,
  type ConversationSnapshot,
  type MemoryRecord,
  type SourceSnapshot,
  type WorkspaceSnapshot,
} from "./protocol";
import WorkspaceGraphView from "./WorkspaceGraphView";
import { buildWorkspaceGraph, type WorkspaceGraph } from "./workspace_graph";

type Destination = "chat" | "settings" | "extensions";
type WorkTab = "files" | "map" | "activity";
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

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [workspaceGraph, setWorkspaceGraph] = useState<WorkspaceGraph | null>(null);
  const [sourceSnapshot, setSourceSnapshot] = useState<SourceSnapshot | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationSnapshot | null>(null);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [endpoint, setEndpoint] = useState(defaultEndpoint);
  const [modelId, setModelId] = useState("local-model");
  const [connectionSaved, setConnectionSaved] = useState(false);
  const [destination, setDestination] = useState<Destination>("chat");
  const [workTab, setWorkTab] = useState<WorkTab>("files");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [fileFilter, setFileFilter] = useState("");
  const [settingsSection, setSettingsSection] = useState("General");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const bootstrapStarted = useRef(false);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    void bootstrap();
  }, []);

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
    await desktopRequest("connections.save", {
      connection_id: "local",
      provider_kind: "openai-compatible",
      endpoint,
      protocol: "chat-completions",
      model_id: modelId,
    }, (value) => value);
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
      title: "New local task",
      connection_id: "local",
      model_id: modelId,
    }, parseConversationRecordResult);
    setConversations((current) => [record, ...current.filter((item) => item.conversation_id !== id)]);
    await selectConversation(id);
    setDestination("chat");
  }

  async function selectConversation(id: string) {
    setActiveConversationId(id);
    await loadConversation(id);
  }

  async function loadConversation(id: string) {
    try {
      const snapshot = await desktopRequest("conversations.read", { conversation_id: id }, parseConversationSnapshot);
      setConversation(snapshot);
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

  const runtimeReady = workspace?.native_runtime_available === true;
  const activeTitle = conversation?.conversation.title ?? "New local task";
  const activeConversation = conversations.find((item) => item.conversation_id === activeConversationId);
  const filteredFiles = useMemo(() => {
    const query = fileFilter.trim().toLowerCase();
    return (sourceSnapshot?.files ?? [])
      .filter((file) => !query || file.relative_path.toLowerCase().includes(query))
      .sort((left, right) => left.relative_path.localeCompare(right.relative_path))
      .slice(0, 120);
  }, [fileFilter, sourceSnapshot]);

  function openDestination(next: Destination) {
    setDestination(next);
    if (next !== "chat") setWorkTab("files");
  }

  function renderSidebar() {
    return (
      <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`} aria-label="Workspace navigation">
        <div className="sidebar-brand">
          <div className="brand-mark" aria-hidden="true">A</div>
          {!sidebarCollapsed && <div className="brand-copy"><strong>AEGIS</strong><span>LOCAL WORKSPACE</span></div>}
          <button className="sidebar-toggle" type="button" onClick={() => setSidebarCollapsed((current) => !current)} aria-label="Toggle sidebar">{sidebarCollapsed ? "›" : "‹"}</button>
        </div>
        <button className="new-task" type="button" onClick={() => void createConversation()} disabled={!workspace || busy}>
          <span aria-hidden="true">+</span>{!sidebarCollapsed && "New task"}
        </button>
        <div className="sidebar-scroll">
          <div className="sidebar-group">
            {!sidebarCollapsed && <div className="sidebar-label">SESSIONS <span>{conversations.length}</span></div>}
            <button className={`sidebar-nav ${destination === "chat" ? "active" : ""}`} type="button" onClick={() => openDestination("chat")} title="Sessions">
              <span className="nav-icon">◌</span>{!sidebarCollapsed && "Sessions"}
            </button>
            {!sidebarCollapsed && <div className="session-list">
              {conversations.length === 0 ? <p className="sidebar-empty">No sessions yet.</p> : conversations.map((item) => (
                <button className={`session-row ${item.conversation_id === activeConversationId ? "active" : ""}`} key={item.conversation_id} type="button" onClick={() => void selectConversation(item.conversation_id)}>
                  <span className="session-dot" />
                  <span className="session-info"><strong>{item.title}</strong><small>{formatTime(item.updated_at_ms)}</small></span>
                </button>
              ))}
            </div>}
          </div>
          <div className="sidebar-group project-group">
            {!sidebarCollapsed && <div className="sidebar-label">PROJECTS</div>}
            <button className="project-row" type="button" onClick={() => { openDestination("chat"); setWorkTab("files"); }} title={workspace?.workspace_path ?? "Workspace"}>
              <span className="project-chevron">⌄</span><span className="project-icon">◆</span>{!sidebarCollapsed && <span className="project-name">{shortPath(workspace?.workspace_path, 23)}</span>}
            </button>
            {!sidebarCollapsed && <div className="project-subrow"><span className="project-subdot" />{activeTitle}</div>}
          </div>
        </div>
        <div className="sidebar-bottom">
          <button className={`utility-row ${destination === "extensions" ? "active" : ""}`} type="button" onClick={() => openDestination("extensions")} title="Extensions"><span>⊞</span>{!sidebarCollapsed && "Extensions"}</button>
          <button className={`utility-row ${destination === "settings" ? "active" : ""}`} type="button" onClick={() => openDestination("settings")} title="Settings"><span>⚙</span>{!sidebarCollapsed && "Settings"}</button>
          {!sidebarCollapsed && <div className="sidebar-status"><span className={`status-dot ${runtimeReady ? "ready" : "attention"}`} />{runtimeReady ? "Rust host online" : "Opening local host"}</div>}
          {!sidebarCollapsed && <div className="sidebar-version">AEGIS 0.1.0 · protocol v1</div>}
        </div>
      </aside>
    );
  }

  function renderConversation() {
    return (
      <section className="chat-view" aria-label="Conversation">
        <header className="conversation-topbar">
          <div className="conversation-title">
            <span className="eyebrow">{shortPath(workspace?.workspace_path, 38)}</span>
            <div className="title-line"><h1>{activeTitle}</h1><span className="status-chip"><i /> Local</span></div>
          </div>
          <div className="topbar-actions">
            <span className="revision-label">r{conversation?.conversation.revision ?? "—"}</span>
            <button className="topbar-icon" type="button" onClick={() => setWorkTab((current) => current === "files" ? "map" : "files")} aria-label="Toggle work panel">▤</button>
            <button className="topbar-icon" type="button" onClick={() => openDestination("settings")} aria-label="Open settings">⋯</button>
          </div>
        </header>
        {error && <div className="error" role="alert"><span>!</span>{error}</div>}
        <div className="chat-scroll">
          <div className="chat-column">
            <div className="chat-intro"><div className="intro-mark">✦</div><div><strong>AEGIS is ready</strong><span>Local workspace · {mode === "mock" ? "safe preview mode" : "live provider mode"}</span></div></div>
            {conversation?.turns.length ? conversation.turns.map((turn) => (
              <article className={`message-row ${turn.role}`} key={turn.turn_id}>
                <div className="message-avatar">{turn.role === "user" ? "Y" : "A"}</div>
                <div className="message-content"><div className="message-meta"><strong>{turn.role === "user" ? "You" : "AEGIS"}</strong><span>r{turn.revision}</span></div><p>{turn.content || "…"}</p></div>
              </article>
            )) : <div className="empty-chat"><h2>Start a local task</h2><p>Ask AEGIS to research, inspect, or reason over this workspace.</p><div className="suggestion-row"><button type="button" onClick={() => setMessage("Summarize this workspace")}>Summarize workspace</button><button type="button" onClick={() => setMessage("Inspect the current project")}>Inspect project</button></div></div>}
          </div>
        </div>
        <div className="composer-wrap">
          <div className="composer-pill">
            <div className="composer-toolbar"><button className="mode-chip" type="button" onClick={() => setMode(mode === "mock" ? "live" : "mock")}><span className="mode-dot" />{mode === "mock" ? "Agent · Preview" : "Agent · Live"}<span className="chevron">⌄</span></button><span className="composer-model">{modelId}</span><span className="composer-spacer" /><button className="composer-tool" type="button" onClick={() => setWorkTab("files")} aria-label="Browse files">⊕</button></div>
            <textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} placeholder="Message AEGIS…" rows={1} disabled={busy || !activeConversationId} />
            <div className="composer-bottom"><span>⌘ Enter to send · Shift Enter for a new line</span><button className="send-button" type="button" onClick={() => void sendMessage()} disabled={busy || !message.trim() || !activeConversationId}>{busy ? "…" : "↑"}</button></div>
          </div>
        </div>
      </section>
    );
  }

  function renderWorkPanel() {
    const selected = sourceSnapshot?.files.find((file) => file.relative_path === selectedFile);
    return (
      <aside className="work-panel" aria-label="Workspace panel">
        <div className="work-panel-header"><div className="work-tabs" role="tablist" aria-label="Workspace panel tabs"><button className={workTab === "files" ? "active" : ""} type="button" onClick={() => setWorkTab("files")}>Files</button><button className={workTab === "map" ? "active" : ""} type="button" onClick={() => setWorkTab("map")}>Map</button><button className={workTab === "activity" ? "active" : ""} type="button" onClick={() => setWorkTab("activity")}>Activity</button></div><button className="panel-more" type="button" aria-label="Work panel options">+</button></div>
        {workTab === "files" && <div className="file-panel"><div className="panel-title"><div><span className="eyebrow">WORKSPACE</span><strong>Project files</strong></div><span className="file-count">{sourceSnapshot?.files.length ?? "—"}</span></div><div className="file-search"><span>⌕</span><input ref={searchRef} value={fileFilter} onChange={(event) => setFileFilter(event.target.value)} placeholder="Search files" /></div><div className="file-tree">{filteredFiles.length ? filteredFiles.map((file) => <button className={`file-row ${selectedFile === file.relative_path ? "active" : ""}`} key={file.relative_path} type="button" onClick={() => setSelectedFile(file.relative_path)}><span className="file-kind">{fileKind(file.relative_path)}</span><span className="file-name">{file.relative_path}</span></button>) : <p className="panel-empty">No indexed files match this search.</p>}</div>{selected && <div className="file-inspector"><span className="eyebrow">SELECTED FILE</span><strong>{selected.relative_path}</strong><div><span>{selected.language || "unknown"}</span><span>{Math.round(selected.size_bytes / 1024)} KB</span></div><p>{selected.extraction_status === "ok" ? "Symbols and imports are indexed." : selected.extraction_status}</p></div>}</div>}
        {workTab === "map" && <div className="map-panel">{workspaceGraph && sourceSnapshot ? <WorkspaceGraphView graph={workspaceGraph} loadMemories={loadMemoriesForFile} /> : <p className="panel-empty">The source map is opening.</p>}</div>}
        {workTab === "activity" && <div className="activity-panel"><div className="panel-title"><div><span className="eyebrow">SESSION</span><strong>Activity</strong></div><span className="status-chip"><i /> Live</span></div><div className="activity-item"><span className="activity-icon">✓</span><div><strong>Workspace opened</strong><small>Rust host admitted the local session</small></div></div><div className="activity-item"><span className="activity-icon">⌁</span><div><strong>Source map ready</strong><small>{workspaceGraph?.stats.files ?? 0} files available to inspect</small></div></div><div className="activity-item"><span className="activity-icon">◌</span><div><strong>Conversation state</strong><small>{activeConversation?.status ?? "idle"} · revision {conversation?.conversation.revision ?? "—"}</small></div></div></div>}
      </aside>
    );
  }

  function renderSettings() {
    const sections = ["General", "Connections", "Workspace", "Keyboard"];
    return <main className="destination-page"><header className="destination-header"><div><span className="eyebrow">AEGIS / SETTINGS</span><h1>Settings</h1><p>Configure the local workspace without leaving the desktop shell.</p></div><span className="status-chip"><i /> Local-first</span></header><div className="settings-layout"><nav className="settings-nav" aria-label="Settings sections">{sections.map((section) => <button className={settingsSection === section ? "active" : ""} key={section} type="button" onClick={() => setSettingsSection(section)}>{section}<span>›</span></button>)}</nav><section className="settings-content"><div className="settings-heading"><h2>{settingsSection}</h2><p>{settingsSection === "Connections" ? "Choose the provider endpoint used by local conversations." : "Small controls that keep the workspace predictable and focused."}</p></div>{settingsSection === "Connections" ? <><div className="settings-card"><div className="card-heading"><div><strong>Local provider</strong><span>OpenAI-compatible chat endpoint</span></div><span className={connectionSaved ? "saved" : "unsaved"}>{connectionSaved ? "Saved" : "Not saved"}</span></div><label htmlFor="settings-endpoint">Endpoint</label><input id="settings-endpoint" value={endpoint} onChange={(event) => { setEndpoint(event.target.value); setConnectionSaved(false); }} /><label htmlFor="settings-model">Model</label><input id="settings-model" value={modelId} onChange={(event) => { setModelId(event.target.value); setConnectionSaved(false); }} /><div className="card-actions"><button className="primary-action" type="button" onClick={() => void configureConnection()} disabled={!endpoint.trim() || !modelId.trim()}>Save connection</button><span>Secrets stay outside the renderer.</span></div></div><div className="settings-card compact-card"><strong>Authority</strong><p>The renderer sends versioned commands. Rust owns admission; the local service owns provider access.</p></div></> : <div className="settings-card settings-summary"><div className="summary-row"><span>Workspace</span><strong>{workspace?.workspace_path ?? "Opening…"}</strong></div><div className="summary-row"><span>Profile</span><strong>{workspace?.profile_id ?? "—"}</strong></div><div className="summary-row"><span>Runtime</span><strong className={runtimeReady ? "good" : "warn"}>{runtimeReady ? "Native host online" : "Opening local host"}</strong></div><div className="summary-row"><span>Keyboard</span><strong>⌘/Ctrl B sidebar · ⌘/Ctrl K search</strong></div></div>}</section></div></main>;
  }

  function renderExtensions() {
    const cards = [
      ["Source map", "Inspect indexed files, symbols, imports, and lineage.", "Connected", "map"],
      ["Memory", "Search workspace-aware local memory from the work panel.", "Connected", "chat"],
      ["Conversations", "Append-only local sessions with revisioned turns.", "Connected", "chat"],
      ["Provider adapter", "Use the configured OpenAI-compatible local endpoint.", connectionSaved ? "Connected" : "Needs setup", "settings"],
      ["Browser research", "A future connector can add browser-backed research.", "Not connected", "extensions"],
      ["Subagents", "A future surface for admitted parallel workers.", "Not connected", "extensions"],
    ];
    return <main className="destination-page extensions-page"><header className="destination-header"><div><span className="eyebrow">AEGIS / EXTENSIONS</span><h1>Extensions</h1><p>Workspace capabilities are surfaced here without hiding their authority boundary.</p></div><button className="outline-action" type="button" onClick={() => openDestination("settings")}>Configure connections</button></header><div className="extension-toolbar"><div className="extension-search">⌕ <input placeholder="Search capabilities" /></div><div className="extension-filters"><button className="active" type="button">All</button><button type="button">Connected</button><button type="button">Local</button></div></div><div className="extension-grid">{cards.map(([title, description, status, target]) => <article className="extension-card" key={title}><div className="extension-icon">{title === "Source map" ? "⌘" : title === "Memory" ? "◈" : title === "Conversations" ? "◌" : title === "Provider adapter" ? "↗" : "⊞"}</div><div className="extension-card-body"><div className="extension-card-title"><h2>{title}</h2><span className={status === "Connected" ? "connected" : "pending"}>{status}</span></div><p>{description}</p><button type="button" onClick={() => target === "settings" ? openDestination("settings") : target === "map" ? (openDestination("chat"), setWorkTab("map")) : target === "chat" ? openDestination("chat") : undefined}>{status === "Connected" ? "Open" : "View details"}<span>→</span></button></div></article>)}</div></main>;
  }

  return <div className={`app-frame ${destination !== "chat" ? "destination-frame" : ""}`}>
    {renderSidebar()}
    {destination === "chat" ? <>{renderConversation()}{renderWorkPanel()}</> : destination === "settings" ? renderSettings() : renderExtensions()}
  </div>;
}
