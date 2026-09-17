import { useEffect, useRef, useState } from "react";
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

const defaultEndpoint = "http://127.0.0.1:8080/v1";

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
  const [showProjectMap, setShowProjectMap] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bootstrapStarted = useRef(false);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    void bootstrap();
  }, []);

  async function bootstrap() {
    try {
      setError(null);
      const snapshot = await desktopRequest("workspace.open", {}, parseWorkspaceSnapshot);
      setWorkspace(snapshot);
      await loadWorkspaceGraph();
      await configureConnection();
      const records = await loadConversations();
      if (records.length > 0) {
        await selectConversation(records[0].conversation_id);
      } else {
        await createConversation();
      }
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
  }

  async function selectConversation(id: string) {
    setActiveConversationId(id);
    await loadConversation(id);
  }

  async function loadConversation(id: string) {
    try {
      const snapshot = await desktopRequest("conversations.read", {
        conversation_id: id,
      }, parseConversationSnapshot);
      setConversation(snapshot);
      if (snapshot.conversation.model_id !== modelId) setConnectionSaved(false);
      setModelId(snapshot.conversation.model_id);
    } catch (reason) {
      setConversation(null);
      if (reason instanceof Error && !reason.message.includes("conversation not found")) {
        setError(reason.message);
      }
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
    return desktopRequest("memory.search", {
      query: node.path,
      top_k: 6,
      scope_kind: "USER_PRIVATE",
    }, parseMemorySearchResult);
  }

  const runtimeReady = workspace?.native_runtime_available === true;
  const activeTitle = conversation?.conversation.title ?? "New local task";

  return (
    <div className="app-frame">
      <aside className="sidebar" aria-label="Workspace navigation">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">A</div>
          <div>
            <strong>AEGIS</strong>
            <span>LOCAL WORKSPACE</span>
          </div>
        </div>
        <button className="new-task" type="button" onClick={() => void createConversation()} disabled={!workspace || busy}>
          <span aria-hidden="true">+</span> New task
        </button>
        <nav className="side-nav" aria-label="Workspace views">
          <button className="side-nav-item selected" type="button"><span>◎</span> Conversations</button>
          <button className={`side-nav-item ${showProjectMap ? "selected" : ""}`} type="button" onClick={() => setShowProjectMap((current) => !current)}>
            <span>⌘</span> Project map
          </button>
        </nav>
        <div className="sidebar-section">
          <div className="section-heading"><span>RECENT TASKS</span><small>{conversations.length}</small></div>
          <div className="conversation-list">
            {conversations.length === 0 ? <p className="sidebar-empty">No local tasks yet.</p> : conversations.map((item) => (
              <button
                className={`conversation-item ${item.conversation_id === activeConversationId ? "active" : ""}`}
                key={item.conversation_id}
                type="button"
                onClick={() => void selectConversation(item.conversation_id)}
              >
                <span className="conversation-item-title">{item.title}</span>
                <span className="conversation-item-meta">r{item.revision} · {item.model_id}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="sidebar-footer">
          <span className={`runtime-pill ${runtimeReady ? "ready" : "attention"}`}>
            <i /> {runtimeReady ? "Rust authority online" : "Native runtime unavailable"}
          </span>
          <span className="profile-name">{workspace?.profile_id ?? "opening profile"}</span>
        </div>
      </aside>

      <main className="main-pane">
        <header className="conversation-topbar">
          <div>
            <span className="breadcrumb">WORKSPACE / CONVERSATION</span>
            <h1>{activeTitle}</h1>
          </div>
          <div className="topbar-actions">
            <span className="local-badge"><i /> Local-first</span>
            <button className="icon-button" type="button" onClick={() => setShowProjectMap((current) => !current)} aria-label="Toggle project map">
              {showProjectMap ? "Hide map" : "Map"}
            </button>
          </div>
        </header>

        {error && <div className="error" role="alert">{error}</div>}

        <section className="conversation-surface" aria-label="Conversation">
          <div className="surface-meta">
            <span>{mode === "mock" ? "Deterministic smoke mode" : "Live provider mode"}</span>
            <span>revision {conversation?.conversation.revision ?? "—"}</span>
          </div>
          <div className="turns" aria-live="polite">
            {conversation?.turns.map((turn) => (
              <article className={`turn ${turn.role}`} key={turn.turn_id}>
                <div className="turn-heading"><span className="turn-role">{turn.role}</span><span>r{turn.revision}</span></div>
                <p>{turn.content || "…"}</p>
              </article>
            )) ?? <div className="conversation-empty"><span className="empty-orb">✦</span><h2>Start a local task</h2><p>Ask AEGIS to research, inspect, or reason over this workspace.</p></div>}
          </div>
          <div className="composer">
            <div className="composer-label"><span>MESSAGE AEGIS</span><span>{mode === "mock" ? "Safe preview" : "Provider request"}</span></div>
            <div className="composer-box">
              <input
                id="message"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                placeholder="What should we work on?"
                disabled={busy || !activeConversationId}
              />
              <button className="send-button" type="button" onClick={() => void sendMessage()} disabled={busy || !message.trim() || !activeConversationId}>
                {busy ? "Sending…" : "Send"}
              </button>
            </div>
            <div className="composer-footer">
              <div className="mode-row" role="group" aria-label="Conversation mode">
                <button type="button" className={mode === "mock" ? "selected" : ""} onClick={() => setMode("mock")}>Mock</button>
                <button type="button" className={mode === "live" ? "selected" : ""} onClick={() => setMode("live")}>Live model</button>
              </div>
              <span>Enter to send · source and memory stay local</span>
            </div>
          </div>
        </section>

        {showProjectMap && workspaceGraph && sourceSnapshot && <WorkspaceGraphView graph={workspaceGraph} loadMemories={loadMemoriesForFile} />}
      </main>

      <aside className="context-pane" aria-label="Task context">
        <div className="context-heading"><span className="breadcrumb">TASK CONTEXT</span><span className="context-dot">●</span></div>
        <section className="context-card">
          <span className="label">Workspace</span>
          <strong>{workspace?.workspace_path ?? "Opening…"}</strong>
          <dl>
            <div><dt>Profile</dt><dd>{workspace?.profile_id ?? "—"}</dd></div>
            <div><dt>Source revision</dt><dd>{sourceSnapshot?.revision.slice(0, 12) ?? "—"}</dd></div>
            <div><dt>Files mapped</dt><dd>{workspaceGraph?.stats.files ?? "—"}</dd></div>
          </dl>
        </section>
        <section className="context-card">
          <div className="context-card-heading"><span className="label">Provider</span><span className={connectionSaved ? "saved" : "unsaved"}>{connectionSaved ? "saved" : "not saved"}</span></div>
          <label htmlFor="endpoint">Endpoint</label>
          <input id="endpoint" value={endpoint} onChange={(event) => { setEndpoint(event.target.value); setConnectionSaved(false); }} />
          <label htmlFor="model">Model</label>
          <input id="model" value={modelId} onChange={(event) => { setModelId(event.target.value); setConnectionSaved(false); }} />
          <button className="context-action" type="button" onClick={() => void configureConnection()} disabled={!endpoint.trim() || !modelId.trim()}>
            {connectionSaved ? "Connection saved" : "Save connection"}
          </button>
        </section>
        <section className="context-card context-note">
          <span className="label">Authority boundary</span>
          <p>The renderer sends versioned commands only. Rust owns admission; Python owns the local service boundary.</p>
        </section>
        <div className="context-footer">AEGIS desktop · protocol v1</div>
      </aside>
    </div>
  );
}
