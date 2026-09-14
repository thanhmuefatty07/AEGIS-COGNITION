import { useEffect, useState } from "react";
import {
  desktopRequest,
  parseConversationSnapshot,
  parseMemorySearchResult,
  parseSourceSnapshot,
  parseWorkspaceSnapshot,
  type ConversationSnapshot,
  type MemoryRecord,
  type SourceSnapshot,
  type WorkspaceSnapshot,
} from "./protocol";
import WorkspaceGraphView from "./WorkspaceGraphView";
import { buildWorkspaceGraph, type WorkspaceGraph } from "./workspace_graph";

const conversationId = "desktop-smoke-conversation";

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [workspaceGraph, setWorkspaceGraph] = useState<WorkspaceGraph | null>(null);
  const [sourceSnapshot, setSourceSnapshot] = useState<SourceSnapshot | null>(null);
  const [conversation, setConversation] = useState<ConversationSnapshot | null>(null);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:8080/v1");
  const [modelId, setModelId] = useState("local-model");
  const [connectionSaved, setConnectionSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void openWorkspace();
  }, []);

  async function openWorkspace() {
    try {
      setError(null);
      const snapshot = await desktopRequest("workspace.open", {}, parseWorkspaceSnapshot);
      setWorkspace(snapshot);
      await loadWorkspaceGraph();
      await configureConnection();
      try {
        await desktopRequest("conversations.create", {
          conversation_id: conversationId,
          title: "Local smoke conversation",
          connection_id: "local",
          model_id: modelId,
        }, (value) => value);
      } catch {
        try {
          const existing = await desktopRequest("conversations.read", {
            conversation_id: conversationId,
          }, parseConversationSnapshot);
          if (existing.conversation.model_id !== modelId) {
            await desktopRequest("conversations.switch_model", {
              conversation_id: conversationId,
              connection_id: "local",
              model_id: modelId,
              expected_revision: existing.conversation.revision,
            }, (value) => value);
          }
        } catch {
          // An existing conversation is expected on reopen; read it below.
        }
      }
      await loadConversation();
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

  async function loadConversation() {
    try {
      const snapshot = await desktopRequest("conversations.read", {
        conversation_id: conversationId,
      }, parseConversationSnapshot);
      setConversation(snapshot);
    } catch (reason) {
      setConversation(null);
      if (reason instanceof Error && !reason.message.includes("conversation not found")) {
        setError(reason.message);
      }
    }
  }

  async function sendMockMessage() {
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      setError(null);
      const snapshot = await desktopRequest("conversations.send", {
        conversation_id: conversationId,
        message: text,
        mode,
      }, parseConversationSnapshot);
      setConversation(snapshot);
      setMessage("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation failed");
    } finally {
      setBusy(false);
    }
  }

  async function loadMemoriesForFile(node: { path: string | null }): Promise<MemoryRecord[]> {
    if (!node.path) return [];
    const result = await desktopRequest("memory.search", {
      query: node.path,
      top_k: 6,
      scope_kind: "USER_PRIVATE",
    }, parseMemorySearchResult);
    return result;
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">AEGIS / LOCAL PROFILE</p>
          <h1>Living Workspace</h1>
        </div>
        <div className={`status ${workspace?.native_runtime_available ? "ok" : "warn"}`}>
          <span className="status-dot" />
          {workspace?.native_runtime_available ? "Rust authority online" : "Native runtime unavailable"}
        </div>
      </header>

      {error && <div className="error" role="alert">{error}</div>}

      <section className="workspace-card" aria-label="Workspace status">
        <div>
          <span className="label">Local workspace</span>
          <strong>{workspace?.workspace_path ?? "Opening profile…"}</strong>
        </div>
        <div>
          <span className="label">State store</span>
          <strong>{workspace?.state_path ?? "Waiting for service"}</strong>
        </div>
        <div className="connection-settings">
          <label htmlFor="endpoint">Provider endpoint</label>
          <input id="endpoint" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} />
          <label htmlFor="model">Model</label>
          <input id="model" value={modelId} onChange={(event) => setModelId(event.target.value)} />
          <button type="button" onClick={() => void configureConnection()} disabled={!endpoint.trim() || !modelId.trim()}>
            {connectionSaved ? "Connection saved" : "Save connection"}
          </button>
        </div>
      </section>

      {workspaceGraph && sourceSnapshot && <WorkspaceGraphView graph={workspaceGraph} loadMemories={loadMemoriesForFile} />}

      <section className="conversation-card" aria-label="Conversation">
        <div className="card-heading">
          <div>
            <span className="label">Canonical conversation</span>
            <h2>{conversation?.conversation.title ?? "Local smoke conversation"}</h2>
          </div>
          <span className="revision">revision {conversation?.conversation.revision ?? "—"}</span>
        </div>
        <div className="turns" aria-live="polite">
          {conversation?.turns.map((turn) => (
            <article className={`turn ${turn.role}`} key={turn.turn_id}>
              <span className="turn-role">{turn.role}</span>
              <p>{turn.content || "…"}</p>
            </article>
          )) ?? <p className="empty">Create a conversation through the service to begin.</p>}
        </div>
        <div className="composer">
          <label htmlFor="message">Send a {mode} turn</label>
          <div className="composer-row">
            <input
              id="message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void sendMockMessage();
              }}
              placeholder="Type a message"
              disabled={busy}
            />
            <button type="button" onClick={() => void sendMockMessage()} disabled={busy || !message.trim()}>
              {busy ? "Sending…" : "Send"}
            </button>
          </div>
          <div className="mode-row" role="group" aria-label="Conversation mode">
            <button type="button" className={mode === "mock" ? "selected" : ""} onClick={() => setMode("mock")}>Mock</button>
            <button type="button" className={mode === "live" ? "selected" : ""} onClick={() => setMode("live")}>Live model</button>
          </div>
        </div>
      </section>
    </main>
  );
}
