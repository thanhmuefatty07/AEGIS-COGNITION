import { useMemo, useRef, useState } from "react";
import type { MemoryRecord } from "./protocol";
import type { WorkspaceGraph, WorkspaceGraphNode } from "./workspace_graph";

const MAX_VISIBLE_NODES = 180;
const COLUMNS = 4;
const NODE_WIDTH = 218;
const NODE_HEIGHT = 126;
const NODE_GAP = 22;

type Props = {
  graph: WorkspaceGraph;
  loadMemories?: (node: WorkspaceGraphNode) => Promise<MemoryRecord[]>;
};

export default function WorkspaceGraphView({ graph, loadMemories }: Props) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const memoryRequest = useRef(0);
  const files = useMemo(
    () => graph.nodes.filter((node) => node.kind === "file"),
    [graph.nodes],
  );
  const filteredFiles = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return files;
    return files.filter((file) => `${file.path} ${file.language}`.toLocaleLowerCase().includes(needle));
  }, [files, query]);
  const visibleFiles = filteredFiles.slice(0, MAX_VISIBLE_NODES);
  const visibleIds = new Set(visibleFiles.map((file) => file.id));
  const positions = new Map(visibleFiles.map((file, index) => [
    file.id,
    {
      x: NODE_GAP + (index % COLUMNS) * (NODE_WIDTH + NODE_GAP),
      y: NODE_GAP + Math.floor(index / COLUMNS) * (NODE_HEIGHT + NODE_GAP),
    },
  ]));
  const rows = Math.max(1, Math.ceil(visibleFiles.length / COLUMNS));
  const canvasWidth = COLUMNS * NODE_WIDTH + (COLUMNS + 1) * NODE_GAP;
  const canvasHeight = rows * NODE_HEIGHT + (rows + 1) * NODE_GAP;
  const selected = graph.nodes.find((node) => node.id === selectedId) ?? null;
  const selectedEdges = selected
    ? graph.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
    : [];

  async function selectNode(node: WorkspaceGraphNode) {
    const requestId = memoryRequest.current + 1;
    memoryRequest.current = requestId;
    setSelectedId(node.id);
    setMemories([]);
    setMemoryError(null);
    if (!loadMemories || node.kind !== "file" || !node.path) return;
    setMemoryBusy(true);
    try {
      const result = await loadMemories(node);
      if (memoryRequest.current === requestId) setMemories(result);
    } catch (reason) {
      if (memoryRequest.current === requestId) {
        setMemoryError(reason instanceof Error ? reason.message : "Memory search failed");
      }
    } finally {
      if (memoryRequest.current === requestId) setMemoryBusy(false);
    }
  }
  return (
    <section className="graph-card" aria-label="Project map">
      <div className="graph-heading">
        <div>
          <span className="label">Revision-bound project map</span>
          <h2>{graph.stats.files} files · {graph.stats.symbols} symbols</h2>
        </div>
        <div className="graph-meta">
          <span className="graph-revision">source {graph.source_revision.slice(0, 12)}</span>
          <span className={`graph-state ${graph.truncated ? "attention" : "verified"}`}>
            {graph.truncated ? "Bounded / incomplete" : "Snapshot complete"}
          </span>
        </div>
      </div>
      <div className="graph-toolbar">
        <label htmlFor="project-map-search">Find a file or language</label>
        <input
          id="project-map-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search the source map"
        />
        <span className="graph-count">Showing {visibleFiles.length} of {filteredFiles.length}</span>
      </div>
      {graph.truncated && (
        <p className="graph-notice" role="status">
          This preview is bounded to keep the canvas responsive. {graph.stats.omitted_files} files are omitted from the visual layer; the source snapshot remains authoritative.
        </p>
      )}
      <div className="graph-layout">
        <div className="graph-canvas-wrap">
          <div className="graph-canvas" style={{ width: canvasWidth, minHeight: canvasHeight }}>
            <svg className="graph-edges" width={canvasWidth} height={canvasHeight} aria-hidden="true">
              {graph.edges
                .filter((edge) => edge.kind !== "contains" && visibleIds.has(edge.source) && visibleIds.has(edge.target))
                .map((edge) => {
                  const source = positions.get(edge.source);
                  const target = positions.get(edge.target);
                  if (!source || !target) return null;
                  return (
                    <line
                      key={edge.id}
                      x1={source.x + NODE_WIDTH / 2}
                      y1={source.y + NODE_HEIGHT / 2}
                      x2={target.x + NODE_WIDTH / 2}
                      y2={target.y + NODE_HEIGHT / 2}
                      className={`graph-edge ${edge.kind}`}
                    />
                  );
                })}
            </svg>
            {visibleFiles.map((file) => {
              const position = positions.get(file.id);
              if (!position) return null;
              return <GraphNodeCard
                key={file.id}
                node={file}
                selected={file.id === selectedId}
                position={position}
                onSelect={() => void selectNode(file)}
              />;
            })}
          </div>
        </div>
        <aside className="graph-inspector" aria-label="Selected project map item">
          {selected ? <Inspector node={selected} edges={selectedEdges} memories={memories} memoryBusy={memoryBusy} memoryError={memoryError} /> : (
            <div className="graph-empty">
              <span className="label">Inspector</span>
              <p>Select a file to see its symbols and revision-bound relationships.</p>
            </div>
          )}
        </aside>
      </div>
      <div className="graph-legend" aria-label="Project map legend">
        <span><i className="legend-dot observed" /> observed containment</span>
        <span><i className="legend-line candidate" /> candidate import or lineage</span>
        <span>unresolved candidates: {graph.stats.unresolved_candidates}</span>
      </div>
    </section>
  );
}

function GraphNodeCard({
  node,
  selected,
  position,
  onSelect,
}: {
  node: WorkspaceGraphNode;
  selected: boolean;
  position: { x: number; y: number };
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`graph-node ${languageClass(node.language)} ${selected ? "selected" : ""}`}
      style={{ left: position.x, top: position.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
      onClick={onSelect}
      aria-pressed={selected}
      title={node.path ?? node.label}
    >
      <span className="graph-node-kind">{node.language || "source"}</span>
      <strong>{node.label}</strong>
      <span className="graph-node-path">{node.path}</span>
      <span className="graph-node-foot">{node.symbols.length} symbols · {node.extraction_status?.toLocaleLowerCase() ?? "unknown"}</span>
    </button>
  );
}

function Inspector({
  node,
  edges,
  memories,
  memoryBusy,
  memoryError,
}: {
  node: WorkspaceGraphNode;
  edges: WorkspaceGraph["edges"];
  memories: MemoryRecord[];
  memoryBusy: boolean;
  memoryError: string | null;
}) {
  return (
    <div>
      <span className="label">Focused file</span>
      <h3>{node.path}</h3>
      <p className="inspector-status">{node.extraction_status?.toLocaleLowerCase() ?? "status unavailable"}</p>
      <span className="label">Symbols</span>
      {node.symbols.length ? (
        <ul className="symbol-list">
          {node.symbols.slice(0, 24).map((symbol) => <li key={`${symbol.name}:${symbol.line}`}><code>{symbol.name}</code><span>line {symbol.line}</span></li>)}
        </ul>
      ) : <p className="muted">No extracted symbols in this snapshot.</p>}
      <span className="label">Relationships</span>
      {edges.length ? <ul className="relationship-list">{edges.map((edge) => <li key={edge.id}><span>{edge.kind.replaceAll("_", " ")}</span><code>{edge.target === node.id ? edge.source : edge.target}</code></li>)}</ul> : <p className="muted">No candidate relationship was resolved.</p>}
      <span className="label">Linked memory</span>
      {memoryBusy ? <p className="muted">Searching authorized memory…</p> : memoryError ? <p className="memory-error">{memoryError}</p> : memories.length ? (
        <ul className="memory-list">
          {memories.map((memory) => <li key={memory.memory_id}>
            <div><strong>{memory.memory_kind.replaceAll("_", " ").toLocaleLowerCase()}</strong><span>{memory.validation.toLocaleLowerCase()} · r{memory.revision}</span></div>
            <p>{memory.content?.slice(0, 220) || "(metadata-only record)"}</p>
          </li>)}
        </ul>
      ) : <p className="muted">No authorized memory linked by this file path.</p>}
    </div>
  );
}

function languageClass(language: string | null): string {
  const value = language?.toLocaleLowerCase() ?? "unknown";
  if (value.includes("python")) return "graph-node-python";
  if (value.includes("rust")) return "graph-node-rust";
  if (value.includes("typescript") || value.includes("javascript")) return "graph-node-web";
  return "graph-node-other";
}
