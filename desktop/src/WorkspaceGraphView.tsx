import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type {
  MemoryFileRelation,
  MemoryFileRelationCursor,
  MemoryFileRelationLinkRequest,
  MemoryFileRelationListRequest,
  MemoryFileRelationPage,
  MemoryFileRelationUnlinkRequest,
  MemoryRecord,
} from "./protocol";
import {
  fileMemoryRelationProjectId,
  isMemorySearchCandidateCurrent,
  memorySearchCandidateSource,
  memoryFileRelationStatus,
  isMemoryFileRelationSourceCurrent,
  orderWorkspaceGraphNodes,
  workspaceGraphEdgePath,
  type WorkspaceGraph,
  type WorkspaceGraphMemoryCandidateSource,
  type WorkspaceGraphNode,
} from "./workspace_graph";

const MAX_VISIBLE_NODES = 180;
const NODE_WIDTH = 218;
const NODE_HEIGHT = 126;
const NODE_GAP = 22;
const MIN_ZOOM = 0.75;
const MAX_ZOOM = 1.5;
const ZOOM_STEP = 0.25;
const MAX_COLUMNS = 4;
const MEMORY_RELATION_PAGE_SIZE = 24;

type MemoryFileRelationLoadResult = MemoryFileRelationPage | MemoryFileRelation[];

function normalizeMemoryRelationPage(result: MemoryFileRelationLoadResult): MemoryFileRelationPage {
  return Array.isArray(result) ? { relations: result, next_cursor: null } : result;
}

type Props = {
  graph: WorkspaceGraph;
  loadMemories?: (node: WorkspaceGraphNode, query: string) => Promise<MemoryRecord[]>;
  loadFileRelations?: (request: MemoryFileRelationListRequest) => Promise<MemoryFileRelationLoadResult>;
  linkMemoryToFile?: (request: MemoryFileRelationLinkRequest) => Promise<MemoryFileRelation>;
  unlinkFileRelation?: (request: MemoryFileRelationUnlinkRequest) => Promise<boolean>;
};

type MemorySearchState = {
  source: WorkspaceGraphMemoryCandidateSource;
  searchMode: "path" | "text";
  records: MemoryRecord[];
  busy: boolean;
  error: string | null;
  stale: boolean;
};

type MemoryRelationState = {
  source: WorkspaceGraphMemoryCandidateSource;
  relations: MemoryFileRelation[];
  nextCursor: MemoryFileRelationCursor | null;
  busy: boolean;
  loadingMore: boolean;
  error: string | null;
  pageError: string | null;
  pageAnnouncement: string;
  staleRequest: boolean;
};

type WorkspaceGraphLayout = {
  columns: number;
  nodeWidth: number;
  nodeHeight: number;
  nodeGap: number;
  canvasWidth: number;
};

export function workspaceGraphLayout(viewportWidth: number, zoom: number): WorkspaceGraphLayout {
  const width = Number.isFinite(viewportWidth) ? Math.max(280, viewportWidth) : 960;
  const scale = Number.isFinite(zoom) ? Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom)) : 1;
  const nodeWidth = Math.round(NODE_WIDTH * scale);
  const nodeHeight = Math.round(NODE_HEIGHT * scale);
  const nodeGap = Math.round(NODE_GAP * scale);
  const columns = Math.max(1, Math.min(MAX_COLUMNS, Math.floor((width - nodeGap) / (nodeWidth + nodeGap))));
  const contentWidth = columns * nodeWidth + (columns + 1) * nodeGap;
  return { columns, nodeWidth, nodeHeight, nodeGap, canvasWidth: Math.max(width, contentWidth) };
}

export default function WorkspaceGraphView({ graph, loadMemories, loadFileRelations, linkMemoryToFile, unlinkFileRelation }: Props) {
  const [query, setQuery] = useState("");
  const [viewMode, setViewMode] = useState<"graph" | "list">("graph");
  const [folderFilter, setFolderFilter] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [viewportWidth, setViewportWidth] = useState(960);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memorySearch, setMemorySearch] = useState<MemorySearchState | null>(null);
  const [memoryRelations, setMemoryRelations] = useState<MemoryRelationState | null>(null);
  const [relationActionId, setRelationActionId] = useState<string | null>(null);
  const [relationActionError, setRelationActionError] = useState<string | null>(null);
  const memoryRequest = useRef(0);
  const relationRequest = useRef(0);
  const selectedNodeIdRef = useRef<string | null>(null);
  const graphRef = useRef(graph);
  graphRef.current = graph;
  const canvasViewport = useRef<HTMLDivElement>(null);
  const files = useMemo(
    () => graph.nodes.filter((node) => node.kind === "file"),
    [graph.nodes],
  );
  const folders = useMemo(() => [...new Set(files.map((file) => folderForPath(file.path ?? "")))].sort((a, b) => a.localeCompare(b)), [files]);
  const filteredFiles = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return files.filter((file) => {
      const path = file.path ?? "";
      const matchesFolder = folderFilter === null || folderForPath(path) === folderFilter;
      const matchesQuery = !needle || `${path} ${file.language}`.toLocaleLowerCase().includes(needle);
      return matchesFolder && matchesQuery;
    });
  }, [files, folderFilter, query]);
  const visibleFiles = useMemo(
    () => orderWorkspaceGraphNodes(filteredFiles.slice(0, MAX_VISIBLE_NODES), graph.edges),
    [filteredFiles, graph.edges],
  );
  const visibleIds = new Set(visibleFiles.map((file) => file.id));
  const layout = workspaceGraphLayout(viewportWidth, zoom);
  const positions = new Map(visibleFiles.map((file, index) => [
    file.id,
    {
      x: layout.nodeGap + (index % layout.columns) * (layout.nodeWidth + layout.nodeGap),
      y: layout.nodeGap + Math.floor(index / layout.columns) * (layout.nodeHeight + layout.nodeGap),
    },
  ]));
  const rows = Math.max(1, Math.ceil(visibleFiles.length / layout.columns));
  const canvasHeight = rows * layout.nodeHeight + (rows + 1) * layout.nodeGap;
  const listGroups = useMemo(() => {
    const groups = new Map<string, WorkspaceGraphNode[]>();
    for (const file of filteredFiles) {
      const folder = folderForPath(file.path ?? "");
      const items = groups.get(folder) ?? [];
      items.push(file);
      groups.set(folder, items);
    }
    return [...groups.entries()];
  }, [filteredFiles]);
  const selected = graph.nodes.find((node) => node.id === selectedId) ?? null;
  const selectedEdges = selected
    ? graph.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
    : [];
  const selectedMemorySource = selected ? memorySearchCandidateSource(graph, selected) : null;
  const memorySearchIsCurrent = Boolean(
    memorySearch
    && !memorySearch.stale
    && selected
    && selectedMemorySource
    && isMemorySearchCandidateCurrent(memorySearch.source, graph, selected),
  );
  const currentMemorySearch = memorySearchIsCurrent ? memorySearch : null;
  const memorySearchIsStale = Boolean(memorySearch && selectedMemorySource && (memorySearch.stale || !memorySearchIsCurrent));
  const currentMemoryRelations = memoryRelations
    && selected
    && memoryRelations.source.project_id === graph.project_id
    && memoryRelations.source.source_path === selected.path
    ? memoryRelations
    : null;
  const memoryRelationRequestIsStale = Boolean(currentMemoryRelations?.staleRequest);
  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const edgesByNode = useMemo(() => {
    const result = new Map<string, typeof graph.edges>();
    for (const edge of graph.edges) {
      for (const id of [edge.source, edge.target]) {
        const edges = result.get(id) ?? [];
        edges.push(edge);
        result.set(id, edges);
      }
    }
    return result;
  }, [graph.edges]);

  useEffect(() => {
    const viewport = canvasViewport.current;
    if (!viewport) return;
    const updateWidth = () => {
      const width = viewport.clientWidth;
      if (width > 0) setViewportWidth((current) => current === width ? current : width);
    };
    updateWidth();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateWidth);
      return () => window.removeEventListener("resize", updateWidth);
    }
    const observer = new ResizeObserver(updateWidth);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!memorySearch || memorySearch.stale) return;
    const sourceNode = graph.nodes.find(
      (node) => node.kind === "file" && node.path === memorySearch.source.source_path,
    );
    if (sourceNode && isMemorySearchCandidateCurrent(memorySearch.source, graph, sourceNode)) return;

    memoryRequest.current += 1;
    setMemorySearch((current) => current && !current.stale
      ? { ...current, records: [], busy: false, error: null, stale: true }
      : current);
  }, [graph, memorySearch]);

  useEffect(() => {
    if ((!memoryRelations?.busy && !memoryRelations?.loadingMore) || memoryRelations.staleRequest) return;
    if (isMemoryFileRelationSourceCurrent(memoryRelations.source, graph)) return;

    relationRequest.current += 1;
    setMemoryRelations((current) => current && (current.busy || current.loadingMore)
      ? { ...current, busy: false, loadingMore: false, error: null, pageError: null, staleRequest: true }
      : current);
  }, [graph, memoryRelations]);

  function startMemorySearch(node: WorkspaceGraphNode, rawQuery: string, searchMode: MemorySearchState["searchMode"]) {
    const requestId = memoryRequest.current + 1;
    memoryRequest.current = requestId;
    const source = memorySearchCandidateSource(graph, node);
    const searchQuery = rawQuery.trim();
    if (!source || !loadMemories || !searchQuery) {
      setMemorySearch(null);
      return;
    }
    setMemorySearch({ source, searchMode, records: [], busy: true, error: null, stale: false });
    void Promise.resolve().then(() => loadMemories(node, searchQuery)).then((result) => {
      if (memoryRequest.current === requestId && isMemoryCandidateSourceCurrent(source, graphRef.current)) {
        setMemorySearch({ source, searchMode, records: result, busy: false, error: null, stale: false });
      }
    }).catch((reason: unknown) => {
      if (memoryRequest.current === requestId && isMemoryCandidateSourceCurrent(source, graphRef.current)) {
        setMemorySearch({
          source,
          searchMode,
          records: [],
          busy: false,
          error: reason instanceof Error ? reason.message : "Memory search failed",
          stale: false,
        });
      }
    });
  }

  function selectNode(node: WorkspaceGraphNode) {
    const relationsRequestId = relationRequest.current + 1;
    relationRequest.current = relationsRequestId;
    setSelectedId(node.id);
    selectedNodeIdRef.current = node.id;
    setMemoryQuery(node.path ?? "");
    setRelationActionId(null);
    setRelationActionError(null);
    const source = memorySearchCandidateSource(graph, node);
    startMemorySearch(node, node.path ?? "", "path");
    if (!source) {
      setMemoryRelations(null);
      return;
    }
    setMemoryRelations(loadFileRelations
      ? {
        source,
        relations: [],
        nextCursor: null,
        busy: true,
        loadingMore: false,
        error: null,
        pageError: null,
        pageAnnouncement: "",
        staleRequest: false,
      }
      : null);
    if (loadFileRelations) {
      const request = {
        project_id: fileMemoryRelationProjectId(source.project_id),
        relative_path: source.source_path,
        limit: MEMORY_RELATION_PAGE_SIZE,
      };
      void Promise.resolve().then(() => loadFileRelations(request)).then((result) => {
        if (relationRequest.current === relationsRequestId && isMemoryFileRelationSourceCurrent(source, graphRef.current)) {
          const page = normalizeMemoryRelationPage(result);
          setMemoryRelations({
            source,
            relations: page.relations,
            nextCursor: page.next_cursor,
            busy: false,
            loadingMore: false,
            error: null,
            pageError: null,
            pageAnnouncement: "",
            staleRequest: false,
          });
        }
      }).catch((reason: unknown) => {
        if (relationRequest.current === relationsRequestId && isMemoryFileRelationSourceCurrent(source, graphRef.current)) {
          setMemoryRelations({
            source,
            relations: [],
            nextCursor: null,
            busy: false,
            loadingMore: false,
            error: reason instanceof Error ? reason.message : "Saved file-memory relations failed to load",
            pageError: null,
            pageAnnouncement: "",
            staleRequest: false,
          });
        }
      });
    }
  }

  function submitMemorySearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected) startMemorySearch(selected, memoryQuery, "text");
  }

  async function loadMoreMemoryRelations() {
    const current = currentMemoryRelations;
    if (!loadFileRelations || !current || !current.nextCursor || current.busy || current.loadingMore) return;
    const { source, nextCursor } = current;
    const requestId = relationRequest.current + 1;
    relationRequest.current = requestId;
    setMemoryRelations((state) => state
      && state.source.project_id === source.project_id
      && state.source.source_path === source.source_path
      ? { ...state, loadingMore: true, pageError: null, pageAnnouncement: "" }
      : state);
    try {
      const page = normalizeMemoryRelationPage(await loadFileRelations({
        project_id: fileMemoryRelationProjectId(source.project_id),
        relative_path: source.source_path,
        limit: MEMORY_RELATION_PAGE_SIZE,
        cursor: nextCursor,
      }));
      if (relationRequest.current !== requestId || !isMemoryFileRelationSourceCurrent(source, graphRef.current)) return;
      setMemoryRelations((state) => {
        if (!state
          || state.source.project_id !== source.project_id
          || state.source.source_path !== source.source_path
          || state.source.source_content_hash !== source.source_content_hash) return state;
        const knownIds = new Set(state.relations.map((relation) => relation.relation_id));
        const additions = page.relations.filter((relation) => !knownIds.has(relation.relation_id));
        const count = additions.length;
        return {
          ...state,
          relations: [...state.relations, ...additions],
          nextCursor: page.next_cursor,
          loadingMore: false,
          pageError: null,
          pageAnnouncement: `Loaded ${count} more saved relation${count === 1 ? "" : "s"}.`,
        };
      });
    } catch (reason) {
      if (relationRequest.current === requestId && isMemoryFileRelationSourceCurrent(source, graphRef.current)) {
        setMemoryRelations((state) => state
          && state.source.project_id === source.project_id
          && state.source.source_path === source.source_path
          ? {
            ...state,
            loadingMore: false,
            pageError: reason instanceof Error ? reason.message : "Saved file-memory relations failed to load",
            pageAnnouncement: "",
          }
          : state);
      }
    }
  }

  async function restartMemoryRelations() {
    const current = currentMemoryRelations;
    if (!loadFileRelations || !current || current.busy || current.loadingMore) return;
    const { source } = current;
    const requestId = relationRequest.current + 1;
    relationRequest.current = requestId;
    setMemoryRelations((state) => state
      && state.source.project_id === source.project_id
      && state.source.source_path === source.source_path
      ? { ...state, loadingMore: true, pageError: null, pageAnnouncement: "" }
      : state);
    try {
      const page = normalizeMemoryRelationPage(await loadFileRelations({
        project_id: fileMemoryRelationProjectId(source.project_id),
        relative_path: source.source_path,
        limit: MEMORY_RELATION_PAGE_SIZE,
      }));
      if (relationRequest.current !== requestId || !isMemoryFileRelationSourceCurrent(source, graphRef.current)) return;
      setMemoryRelations((state) => {
        if (!state
          || state.source.project_id !== source.project_id
          || state.source.source_path !== source.source_path
          || state.source.source_content_hash !== source.source_content_hash) return state;
        return {
          ...state,
          relations: page.relations,
          nextCursor: page.next_cursor,
          loadingMore: false,
          pageError: null,
          pageAnnouncement: "Saved relation history restarted from the first page.",
        };
      });
    } catch (reason) {
      if (relationRequest.current === requestId && isMemoryFileRelationSourceCurrent(source, graphRef.current)) {
        setMemoryRelations((state) => state
          && state.source.project_id === source.project_id
          && state.source.source_path === source.source_path
          ? {
            ...state,
            loadingMore: false,
            pageError: reason instanceof Error ? reason.message : "Saved file-memory relations failed to load",
            pageAnnouncement: "",
          }
          : state);
      }
    }
  }

  async function saveMemoryRelation(memory: MemoryRecord) {
    if (!selected
      || !currentMemorySearch
      || !linkMemoryToFile
      || currentMemoryRelations?.busy
      || currentMemoryRelations?.loadingMore) return;
    const selectedNodeId = selected.id;
    const source = currentMemorySearch.source;
    const request = memoryFileRelationLinkRequest(source, memory);
    if (!request
      || selectedNodeIdRef.current !== selectedNodeId
      || !isMemoryCandidateSourceCurrent(source, graphRef.current)) {
      setRelationActionError("A current file and memory hash are required before saving this link.");
      return;
    }
    if (!isFileRelationEligible(memory)) {
      setRelationActionError("This memory is not eligible for recall and cannot be linked to a file.");
      return;
    }
    if (memoryRelations?.relations.some((relation) => relationMatchesRequest(relation, request))) return;

    const actionId = `link:${memory.memory_id}`;
    setRelationActionId(actionId);
    setRelationActionError(null);
    try {
      const relation = await linkMemoryToFile(request);
      if (selectedNodeIdRef.current !== selectedNodeId || !isMemoryFileRelationSourceCurrent(source, graphRef.current)) return;
      if (!relationMatchesRequest(relation, request)) {
        throw new Error("The saved relation did not match the confirmed file and memory.");
      }
      relationRequest.current += 1;
      setMemoryRelations((current) => {
        const relations = current !== null
          && current.source.project_id === source.project_id
          && current.source.source_path === source.source_path
          && current.source.source_content_hash === source.source_content_hash
          ? current.relations.filter((item) => item.relation_id !== relation.relation_id)
          : [];
        return {
          source,
          relations: [...relations, relation],
          nextCursor: current?.nextCursor ?? null,
          busy: false,
          loadingMore: false,
          error: null,
          pageError: null,
          pageAnnouncement: "Saved relation added.",
          staleRequest: false,
        };
      });
    } catch (reason) {
      if (selectedNodeIdRef.current === selectedNodeId && isMemoryFileRelationSourceCurrent(source, graphRef.current)) {
        setRelationActionError(reason instanceof Error ? reason.message : "Could not save the file-memory link.");
      }
    } finally {
      if (selectedNodeIdRef.current === selectedNodeId) setRelationActionId(null);
    }
  }

  async function removeMemoryRelation(request: MemoryFileRelationUnlinkRequest) {
    if (!unlinkFileRelation) return;
    const initialState = memoryRelations;
    if (initialState?.busy || initialState?.loadingMore) return;
    const source = initialState?.source;
    const relation = initialState?.relations.find((item) => item.relation_id === request.relation_id);
    const selectedNodeId = selectedNodeIdRef.current;
    if (!source
      || !relation
      || selectedNodeId === null
      || relation.project_id !== request.project_id
      || relation.relative_path !== request.relative_path
      || relation.scope_kind !== request.scope_kind
      || !isMemoryFileRelationSourceCurrent(source, graphRef.current)) return;
    const actionRequestId = ++relationRequest.current;
    setRelationActionId(`unlink:${request.relation_id}`);
    setRelationActionError(null);
    try {
      const removed = await unlinkFileRelation(request);
      if (relationRequest.current !== actionRequestId
        || selectedNodeIdRef.current !== selectedNodeId
        || !isMemoryFileRelationSourceCurrent(source, graphRef.current)) return;
      if (!removed) {
        setRelationActionError("The saved link could not be removed. Refresh this file and try again.");
        return;
      }
      setMemoryRelations((current) => current
        ? { ...current, relations: current.relations.filter((relation) => relation.relation_id !== request.relation_id) }
        : current);
    } catch (reason) {
      if (relationRequest.current === actionRequestId
        && selectedNodeIdRef.current === selectedNodeId
        && isMemoryFileRelationSourceCurrent(source, graphRef.current)) {
        setRelationActionError(reason instanceof Error ? reason.message : "Could not remove the saved file-memory link.");
      }
    } finally {
      if (relationRequest.current === actionRequestId && selectedNodeIdRef.current === selectedNodeId) setRelationActionId(null);
    }
  }
  function panCanvas(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const step = Math.max(80, Math.round(event.currentTarget.clientWidth * 0.75));
    const directions: Record<string, [number, number]> = {
      ArrowDown: [0, step],
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
    };
    const direction = directions[event.key];
    if (!direction) return;
    event.preventDefault();
    event.currentTarget.scrollLeft += direction[0];
    event.currentTarget.scrollTop += direction[1];
  }
  function changeZoom(delta: number) {
    setZoom((current) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, current + delta)));
  }
  function fitWidth() {
    setZoom(1);
    if (canvasViewport.current) {
      canvasViewport.current.scrollLeft = 0;
      canvasViewport.current.scrollTop = 0;
    }
  }
  const visibleCount = viewMode === "graph" ? visibleFiles.length : filteredFiles.length;
  const inspectorPanel = (
    <aside className="graph-inspector" aria-label="Selected project map item">
      {selected ? <Inspector
        node={selected}
        graph={graph}
        edges={selectedEdges}
        memorySource={selectedMemorySource}
        memoryQuery={memoryQuery}
        memorySearchEnabled={Boolean(loadMemories)}
        onMemoryQueryChange={setMemoryQuery}
        onSearchMemories={submitMemorySearch}
        candidateSource={currentMemorySearch?.source ?? null}
        memories={currentMemorySearch?.records ?? []}
        memorySearchMode={currentMemorySearch?.searchMode ?? null}
        memoryBusy={currentMemorySearch?.busy ?? false}
        memoryError={currentMemorySearch?.error ?? null}
        memorySearchIsStale={memorySearchIsStale}
        relations={currentMemoryRelations?.relations ?? []}
        relationsBusy={currentMemoryRelations?.busy ?? false}
        relationsError={currentMemoryRelations?.error ?? null}
        relationsRequestIsStale={memoryRelationRequestIsStale}
        relationsNextCursor={currentMemoryRelations?.nextCursor ?? null}
        relationsPageError={currentMemoryRelations?.pageError ?? null}
        relationsPageAnnouncement={currentMemoryRelations?.pageAnnouncement ?? ""}
        relationsLoadingMore={currentMemoryRelations?.loadingMore ?? false}
        canSaveRelations={Boolean(currentMemorySearch && selectedMemorySource?.source_state === "current" && linkMemoryToFile)}
        relationServiceAvailable={Boolean(loadFileRelations)}
        relationActionId={relationActionId}
        relationActionError={relationActionError}
        onSaveRelation={saveMemoryRelation}
        onRemoveRelation={removeMemoryRelation}
        onLoadMoreRelations={loadMoreMemoryRelations}
        onRestartRelations={restartMemoryRelations}
      /> : (
        <div className="graph-empty">
          <span className="label">Inspector</span>
          <p>Select a file card in Graph or a row in List to inspect saved memory links and search local memory candidates. A Save link button is offered only for a current file and a current, eligible candidate that is not already linked.</p>
        </div>
      )}
    </aside>
  );
  return (
    <section className="graph-card" aria-label="Project map">
      <div className="graph-heading">
        <div>
          <span className="label">Revision-bound project map</span>
          <h2>{graph.stats.files} projected files · {graph.stats.symbols} symbols</h2>
        </div>
        <div className="graph-meta">
          <span className="graph-revision">source {graph.source_revision.slice(0, 12)}</span>
          <span className={`graph-state ${graph.truncated ? "attention" : "verified"}`}>
            {graph.stats.source_overflowed ? "Source snapshot overflowed" : graph.stats.omitted_files ? "Projection incomplete" : "Snapshot complete"}
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
        <label htmlFor="project-map-folder">Folder</label>
        <select id="project-map-folder" value={folderFilter === null ? "all" : `folder:${folderFilter}`} onChange={(event) => setFolderFilter(event.target.value === "all" ? null : event.target.value.slice("folder:".length))}>
          <option value="all">All folders</option>
          {folders.map((folder) => <option key={folderGroupId(folder)} value={`folder:${folder}`}>{folder || "Workspace root"}</option>)}
        </select>
        <span className="graph-count">Showing {visibleCount} of {filteredFiles.length} matching projected files · {graph.stats.snapshot_files} in source snapshot</span>
        <div className="graph-view-switch" role="group" aria-label="Map display">
          <button type="button" aria-pressed={viewMode === "graph"} onClick={() => setViewMode("graph")}>Graph</button>
          <button type="button" aria-pressed={viewMode === "list"} onClick={() => setViewMode("list")}>List</button>
        </div>
      </div>
      {!selected && filteredFiles.length > 0 && (
        <p className="muted" role="status">Select a file in Graph or List to view its memory links and search local memory candidates.</p>
      )}
      {graph.stats.source_overflowed && (
        <p className="graph-notice" role="status">
          The source scan reported overflow. {graph.stats.snapshot_files} files are present in this snapshot; the full workspace total is unavailable.
        </p>
      )}
      {graph.stats.omitted_files > 0 && <p className="graph-notice" role="status">The {graph.stats.files}-file projection omitted {graph.stats.omitted_files} of {graph.stats.snapshot_files} files present in the source snapshot.</p>}
      {viewMode === "graph" && filteredFiles.length > visibleFiles.length && <p className="graph-notice" role="status">The graph draws at most {MAX_VISIBLE_NODES} matching files. Switch to List to browse all {filteredFiles.length} projected matches.</p>}
      {viewMode === "graph" ? <div className="graph-layout">
        <div className="graph-canvas-column">
          <div className="graph-navigation-controls" role="group" aria-label="Graph size and navigation">
            <button type="button" onClick={() => changeZoom(-ZOOM_STEP)} disabled={zoom <= MIN_ZOOM} aria-label="Zoom out">−</button>
            <output aria-live="polite" aria-label="Graph zoom">{Math.round(zoom * 100)}%</output>
            <button type="button" onClick={() => changeZoom(ZOOM_STEP)} disabled={zoom >= MAX_ZOOM} aria-label="Zoom in">+</button>
            <button type="button" onClick={fitWidth}>Fit width</button>
          </div>
          <div
            className="graph-canvas-wrap"
            ref={canvasViewport}
            role="region"
            aria-label="Project graph canvas"
            aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight"
            tabIndex={0}
            onKeyDown={panCanvas}
          >
          <div className="graph-canvas" style={{ width: layout.canvasWidth, minHeight: canvasHeight }}>
            <svg className="graph-edges" width={layout.canvasWidth} height={canvasHeight} aria-hidden="true">
              {graph.edges
                .filter((edge) => edge.kind !== "contains" && visibleIds.has(edge.source) && visibleIds.has(edge.target))
                .map((edge) => {
                  const source = positions.get(edge.source);
                  const target = positions.get(edge.target);
                  if (!source || !target) return null;
                  return (
                    <path
                      key={edge.id}
                      d={workspaceGraphEdgePath(
                        { x: source.x + layout.nodeWidth / 2, y: source.y + layout.nodeHeight / 2 },
                        { x: target.x + layout.nodeWidth / 2, y: target.y + layout.nodeHeight / 2 },
                      )}
                      className={`graph-edge ${edge.kind}${selectedId !== null && (edge.source === selectedId || edge.target === selectedId) ? " active" : ""}`}
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
                width={layout.nodeWidth}
                height={layout.nodeHeight}
                onSelect={() => void selectNode(file)}
              />;
            })}
          </div>
          </div>
        </div>
        {inspectorPanel}
      </div> : <div className="graph-layout">
        <div className="graph-canvas-column">
          <div className="graph-accessible-list" role="region" aria-label="Project files and relationships" tabIndex={0}>
            {listGroups.length ? listGroups.map(([folder, groupedFiles]) => {
              const groupId = folderGroupId(folder);
              return <section className="graph-list-group" key={groupId} aria-labelledby={`${groupId}-heading`}>
                <h3 id={`${groupId}-heading`}>{folder || "Workspace root"} <span>{groupedFiles.length} files</span></h3>
                <div role="list">
                  {groupedFiles.map((file) => {
                    const relationships = edgesByNode.get(file.id) ?? [];
                    return <article className="graph-list-item" role="listitem" key={file.id}>
                      <button className="graph-list-file" type="button" aria-label={`${file.language || "source"} ${file.path ?? file.label}`} aria-pressed={file.id === selectedId} onClick={() => void selectNode(file)}>
                        <span>{file.language || "source"}</span><strong>{file.path}</strong>
                      </button>
                      {relationships.length > 0 ? <ul aria-label={`Relationships for ${file.path}`}>
                        {relationships.map((edge) => {
                          const otherId = edge.source === file.id ? edge.target : edge.source;
                          const other = nodeById.get(otherId);
                          return <li key={edge.id}><span>{edge.kind.replaceAll("_", " ")} · {edge.certainty}:</span> <code>{other?.path ?? other?.label ?? otherId}</code></li>;
                        })}
                      </ul> : <p>No relationships in this map.</p>}
                    </article>;
                  })}
                </div>
              </section>;
            }) : <p className="muted">No files match this folder and search.</p>}
          </div>
        </div>
        {inspectorPanel}
      </div>}
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
  width,
  height,
  onSelect,
}: {
  node: WorkspaceGraphNode;
  selected: boolean;
  position: { x: number; y: number };
  width: number;
  height: number;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`graph-node ${languageClass(node.language)} ${selected ? "selected" : ""}`}
      style={{ left: position.x, top: position.y, width, height }}
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${node.language || "source"} ${node.label} ${node.path ?? ""}`}
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
  graph,
  edges,
  memorySource,
  memoryQuery,
  memorySearchEnabled,
  onMemoryQueryChange,
  onSearchMemories,
  candidateSource,
  memories,
  memorySearchMode,
  memoryBusy,
  memoryError,
  memorySearchIsStale,
  relations,
  relationsBusy,
  relationsError,
  relationsRequestIsStale,
  relationsNextCursor,
  relationsPageError,
  relationsPageAnnouncement,
  relationsLoadingMore,
  canSaveRelations,
  relationServiceAvailable,
  relationActionId,
  relationActionError,
  onSaveRelation,
  onRemoveRelation,
  onLoadMoreRelations,
  onRestartRelations,
}: {
  node: WorkspaceGraphNode;
  graph: WorkspaceGraph;
  edges: WorkspaceGraph["edges"];
  memorySource: WorkspaceGraphMemoryCandidateSource | null;
  memoryQuery: string;
  memorySearchEnabled: boolean;
  onMemoryQueryChange: (query: string) => void;
  onSearchMemories: (event: FormEvent<HTMLFormElement>) => void;
  candidateSource: WorkspaceGraphMemoryCandidateSource | null;
  memories: MemoryRecord[];
  memorySearchMode: MemorySearchState["searchMode"] | null;
  memoryBusy: boolean;
  memoryError: string | null;
  memorySearchIsStale: boolean;
  relations: MemoryFileRelation[];
  relationsBusy: boolean;
  relationsError: string | null;
  relationsRequestIsStale: boolean;
  relationsNextCursor: MemoryFileRelationCursor | null;
  relationsPageError: string | null;
  relationsPageAnnouncement: string;
  relationsLoadingMore: boolean;
  canSaveRelations: boolean;
  relationServiceAvailable: boolean;
  relationActionId: string | null;
  relationActionError: string | null;
  onSaveRelation: (memory: MemoryRecord) => void;
  onRemoveRelation: (request: MemoryFileRelationUnlinkRequest) => void;
  onLoadMoreRelations: () => void;
  onRestartRelations: () => void;
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
      {edges.length ? <ul className="relationship-list">{edges.map((edge) => <li key={edge.id}><span>{edge.kind.replaceAll("_", " ")} · {edge.certainty}</span><code>{edge.target === node.id ? edge.source : edge.target}</code></li>)}</ul> : <p className="muted">No candidate relationship was resolved.</p>}
      <section className="memory-relations" aria-labelledby="saved-memory-relations-heading">
        <h4 id="saved-memory-relations-heading">Saved file-memory relations</h4>
        {relationActionError && <p className="memory-error" role="alert">{relationActionError}</p>}
        {relationsRequestIsStale ? <p className="graph-notice" role="status">Saved links were hidden because the source changed (project, file path, or file hash). Select this file again to reload them.</p>
          : relationsBusy ? <p className="muted" role="status">Loading saved file-memory links…</p>
            : relationsError ? <p className="memory-error" role="alert">{relationsError}</p>
              : relations.length ? <ul id="saved-file-memory-relations" className="memory-relation-list">{relations.map((relation) => {
                const status = memoryFileRelationStatus(relation, graph, node);
                const actionId = `unlink:${relation.relation_id}`;
                return <li key={relation.relation_id}>
                  <div className="memory-relation-heading">
                    <strong>{relation.memory_id} · {relation.scope_kind}</strong>
                    <span className={`memory-relation-status ${status.toLocaleLowerCase().replaceAll(" ", "-")}`} aria-label={`Relation status: ${status}`}>{status}</span>
                  </div>
                  <p>Project <code>{relation.project_id}</code> · owner ID hidden (enforced by local memory service)</p>
                  <p>File <code>{relation.relative_path}</code> · source {relation.source_revision}</p>
                  <p>Memory r{relation.memory_revision} · {relation.memory_lifecycle.toLocaleLowerCase()} · {relation.memory_validation.toLocaleLowerCase()}</p>
                  <p>Saved file hash <code>{relation.source_content_hash}</code></p>
                  <p>Saved memory hash <code>{relation.memory_content_hash}</code></p>
                    <button type="button" className="memory-relation-remove" aria-label={`Remove only relation ${relation.relation_id}`} disabled={relationActionId !== null || relationsBusy || relationsLoadingMore} onClick={() => onRemoveRelation({ relation_id: relation.relation_id, project_id: relation.project_id, relative_path: relation.relative_path, scope_kind: relation.scope_kind })}>
                    {relationActionId === actionId ? "Removing…" : "Remove link"}
                  </button>
                </li>;
              })}</ul> : relationServiceAvailable ? <p className="muted">No saved file-memory links for this file.</p> : <p className="muted">Saved file-memory links are unavailable in this view.</p>}
        {!relationsBusy && !relationsRequestIsStale && relations.length > 0 && (
          <div className="memory-relation-pagination">
            {relationsPageError && <>
              <p className="memory-error" role="alert">{relationsPageError}</p>
              {relationsNextCursor && <button
                className="settings-secondary"
                type="button"
                aria-controls="saved-file-memory-relations"
                disabled={relationsLoadingMore || relationActionId !== null}
                onClick={onRestartRelations}
              >Restart saved relation history</button>}
            </>}
            {relationsPageAnnouncement && <p className="muted" role="status" aria-live="polite">{relationsPageAnnouncement}</p>}
            {relationsLoadingMore && <p className="muted" role="status">Loading saved relations…</p>}
            {relationsNextCursor && !relationsPageError && <button
              className="settings-secondary"
              type="button"
              aria-controls="saved-file-memory-relations"
              disabled={relationsLoadingMore || relationActionId !== null}
              onClick={onLoadMoreRelations}
            >{relationsLoadingMore ? "Loading saved relations…" : "Load more saved relations"}</button>}
          </div>
        )}
      </section>
      <section className="memory-candidates" aria-labelledby="memory-candidates-heading">
      <h4 id="memory-candidates-heading">Memory search candidates</h4>
      <p className="memory-candidate-note" role="note">
        Local memory search only. Results are candidates, not verified file-memory relationships.
      </p>
      {memorySource && memorySearchEnabled && <form className="memory-search-form" aria-label="Search memories for selected file" onSubmit={onSearchMemories}>
        <label htmlFor="memory-candidate-query">Search memory text for this file</label>
        <div className="memory-search-controls">
          <input
            id="memory-candidate-query"
            type="text"
            value={memoryQuery}
            onChange={(event) => onMemoryQueryChange(event.target.value)}
            disabled={memoryBusy}
          />
          <button type="submit" disabled={memoryBusy || !memoryQuery.trim()}>Search memories</button>
        </div>
      </form>}
      {memorySource?.source_state === "stale" && <p className="graph-notice" role="status">This file path is marked stale in the source snapshot. Search results remain candidates only.</p>}
      {memorySource && <details className="memory-provenance">
        <summary>Source and memory provenance</summary>
        <dl>
          <dt>Candidate search</dt><dd>Local lexical memory search</dd>
          <dt>Project</dt><dd><code>{memorySource.project_id}</code></dd>
          <dt>Snapshot</dt><dd><code>{memorySource.source_revision}</code></dd>
          <dt>File</dt><dd><code>{memorySource.source_path}</code></dd>
          <dt>File hash</dt><dd><code>{memorySource.source_content_hash ?? "Unavailable"}</code></dd>
          <dt>Source state</dt><dd>{memorySource.source_state}</dd>
          <dt>Owner filter</dt><dd>Enforced by the local memory service</dd>
        </dl>
      </details>}
      {memorySearchIsStale ? <p className="graph-notice" role="status">Results hidden because the source snapshot changed. Select this file to search again.</p> : memoryBusy ? <p className="muted" role="status">Searching owner-scoped memory candidates…</p> : memoryError ? <p className="memory-error" role="alert">{memoryError}</p> : memories.length ? (
        <ul className="memory-list">
          {memories.map((memory) => {
            const linkRequest = candidateSource ? memoryFileRelationLinkRequest(candidateSource, memory) : null;
            const existingRelation = linkRequest === null
              ? null
              : relations.find((relation) => relationMatchesTarget(relation, linkRequest)) ?? null;
            const evidenceAlreadyLinked = existingRelation !== null
              && linkRequest !== null
              && relationMatchesCandidateEvidence(existingRelation, linkRequest);
            const canOfferLink = Boolean(linkRequest
              && canSaveRelations
              && isFileRelationEligible(memory)
              && existingRelation === null);
            return <li key={memory.memory_id}>
              <div><strong>Candidate · {memory.memory_kind.replaceAll("_", " ").toLocaleLowerCase()}</strong><span>{memory.lifecycle.toLocaleLowerCase()} · {memory.validation.toLocaleLowerCase()} · r{memory.revision}</span></div>
              <p>Record <code>{memory.memory_id}</code> · {memory.scope_kind}</p>
              <p>Record hash <code>{memory.content_hash ?? "Unavailable"}</code></p>
              <p>{memory.content?.slice(0, 220) || "(metadata-only record)"}</p>
              {existingRelation && <p className="memory-candidate-note">{evidenceAlreadyLinked
                ? "This memory version is already linked to this file."
                : "Remove the saved link above before linking updated evidence."}</p>}
              {!existingRelation && linkRequest === null && <p className="memory-candidate-note">A link needs a current file and memory content hash.</p>}
              {!existingRelation && linkRequest !== null && !isFileRelationEligible(memory) && <p className="memory-candidate-note">Only candidate or active memories with unreviewed or accepted validation can be linked.</p>}
              {canOfferLink && <button
                type="button"
                className="memory-relation-link"
                aria-label={`Save link from ${memory.memory_id} to ${candidateSource!.source_path}`}
                disabled={relationActionId !== null || relationsBusy || relationsLoadingMore}
                onClick={() => onSaveRelation(memory)}
              >
                {relationActionId === `link:${memory.memory_id}` ? "Saving…" : `Save link to ${candidateSource!.source_path}`}
              </button>}
            </li>;
          })}
        </ul>
      ) : <p className="muted">{memorySearchMode === "text" ? "No memory candidates matched that search." : "No lexical path matches."}</p>}
      {!canSaveRelations && memories.length > 0 && !memorySearchIsStale && <p className="memory-candidate-note" role="note">A file-memory link can be saved only when the selected source snapshot and candidate hashes are current.</p>}
      </section>
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

function memoryFileRelationLinkRequest(
  source: WorkspaceGraphMemoryCandidateSource | null,
  memory: MemoryRecord,
): MemoryFileRelationLinkRequest | null {
  const sourceHash = source?.source_content_hash;
  const memoryHash = memory.content_hash;
  if (!source
    || source.source_state !== "current"
    || sourceHash === undefined
    || !isSha256(sourceHash)
    || !isSha256(memoryHash)) return null;
  return {
    project_id: fileMemoryRelationProjectId(source.project_id),
    source_revision: source.source_revision,
    relative_path: source.source_path,
    source_content_hash: sourceHash,
    memory_id: memory.memory_id,
    scope_kind: memory.scope_kind,
    memory_revision: memory.revision,
    memory_content_hash: memoryHash,
  };
}

function relationMatchesRequest(relation: MemoryFileRelation, request: MemoryFileRelationLinkRequest): boolean {
  return relation.project_id === request.project_id
    && relation.source_revision === request.source_revision
    && relation.relative_path === request.relative_path
    && relation.source_content_hash === request.source_content_hash
    && relation.memory_id === request.memory_id
    && relation.scope_kind === request.scope_kind
    && relation.memory_revision === request.memory_revision
    && relation.memory_content_hash === request.memory_content_hash;
}

function relationMatchesTarget(relation: MemoryFileRelation, request: MemoryFileRelationLinkRequest): boolean {
  return relation.project_id === request.project_id
    && relation.relative_path === request.relative_path
    && relation.memory_id === request.memory_id
    && relation.scope_kind === request.scope_kind;
}

function relationMatchesCandidateEvidence(relation: MemoryFileRelation, request: MemoryFileRelationLinkRequest): boolean {
  return relation.source_content_hash === request.source_content_hash
    && relation.memory_revision === request.memory_revision
    && relation.memory_content_hash === request.memory_content_hash;
}

function isMemoryCandidateSourceCurrent(source: WorkspaceGraphMemoryCandidateSource, graph: WorkspaceGraph): boolean {
  const node = graph.nodes.find((candidate) => candidate.kind === "file" && candidate.path === source.source_path);
  return node !== undefined && isMemorySearchCandidateCurrent(source, graph, node);
}

function isSha256(value: string | null): value is string {
  return value !== null && /^[0-9a-f]{64}$/.test(value);
}

function isFileRelationEligible(memory: Pick<MemoryRecord, "lifecycle" | "validation">): boolean {
  return (memory.lifecycle === "CANDIDATE" || memory.lifecycle === "ACTIVE")
    && (memory.validation === "UNREVIEWED" || memory.validation === "ACCEPTED");
}

function folderForPath(path: string): string {
  const separator = path.lastIndexOf("/");
  return separator < 0 ? "" : path.slice(0, separator);
}

function folderGroupId(folder: string): string {
  return folder ? `folder-${encodeURIComponent(folder)}` : "workspace-root";
}
