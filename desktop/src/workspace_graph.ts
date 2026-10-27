import type { MemoryFileRelation, SourceSnapshot } from "./protocol";

export const WORKSPACE_GRAPH_SCHEMA = "aegis-workspace-graph-v1" as const;

export type WorkspaceGraphNodeKind = "project" | "directory" | "file";
export type WorkspaceGraphEdgeKind = "contains" | "import_candidate" | "lineage_candidate";
export type WorkspaceGraphCertainty = "observed" | "candidate";

export type WorkspaceGraphNode = {
  id: string;
  kind: WorkspaceGraphNodeKind;
  label: string;
  path: string | null;
  source_content_hash?: string | null;
  source_stale?: boolean;
  language: string | null;
  extraction_status: string | null;
  symbols: SourceSnapshot["files"][number]["symbols"];
};

export type WorkspaceGraphMemoryCandidateSource = {
  certainty: "candidate";
  basis: "lexical_path_search";
  verified_relation: false;
  project_id: string;
  source_revision: string;
  source_path: string;
  source_content_hash: string | null;
  source_state: "current" | "stale";
};

export type WorkspaceGraphEdge = {
  id: string;
  source: string;
  target: string;
  kind: WorkspaceGraphEdgeKind;
  certainty: WorkspaceGraphCertainty;
  evidence: string;
};

export type WorkspaceGraph = {
  schema: typeof WORKSPACE_GRAPH_SCHEMA;
  project_id: string;
  source_revision: string;
  generated_at_ms: number;
  nodes: WorkspaceGraphNode[];
  edges: WorkspaceGraphEdge[];
  truncated: boolean;
  stats: {
    files: number;
    snapshot_files: number;
    source_overflowed: boolean;
    directories: number;
    symbols: number;
    import_candidates: number;
    lineage_candidates: number;
    unresolved_candidates: number;
    omitted_files: number;
  };
};

export function fileMemoryRelationProjectId(workspaceProjectId: string): string {
  return `project:${workspaceProjectId}`;
}

const DEFAULT_FILE_LIMIT = 600;
const IMPORT_SUFFIXES = ["", ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".json"];

export function buildWorkspaceGraph(
  snapshot: SourceSnapshot,
  options: { maxFiles?: number } = {},
): WorkspaceGraph {
  const requestedLimit = options.maxFiles ?? DEFAULT_FILE_LIMIT;
  const maxFiles = Number.isFinite(requestedLimit)
    ? Math.min(DEFAULT_FILE_LIMIT, Math.max(0, Math.floor(requestedLimit)))
    : DEFAULT_FILE_LIMIT;
  const files = [...snapshot.files].sort((left, right) => left.relative_path.localeCompare(right.relative_path));
  const selectedFiles = files.slice(0, maxFiles);
  const selectedPaths = new Set(selectedFiles.map((file) => file.relative_path));
  const stalePaths = new Set(snapshot.stale_paths);
  const projectId = fileMemoryRelationProjectId(snapshot.identity.project_id);
  const nodes: WorkspaceGraphNode[] = [{
    id: projectId,
    kind: "project",
    label: snapshot.identity.root,
    path: null,
    language: null,
    extraction_status: null,
    symbols: [],
  }];
  const edges: WorkspaceGraphEdge[] = [];
  const directoryPaths = new Set<string>();

  for (const file of selectedFiles) {
    const parts = file.relative_path.split("/");
    let parent = "";
    for (let index = 0; index < parts.length - 1; index += 1) {
      parent = parent ? `${parent}/${parts[index]}` : parts[index];
      directoryPaths.add(parent);
    }
    nodes.push({
      id: fileNodeId(snapshot.identity.project_id, file.relative_path),
      kind: "file",
      label: parts.at(-1) ?? file.relative_path,
      path: file.relative_path,
      source_content_hash: file.content_hash,
      source_stale: stalePaths.has(file.relative_path),
      language: file.language,
      extraction_status: file.extraction_status,
      symbols: file.symbols,
    });
  }

  const sortedDirectories = [...directoryPaths].sort((left, right) => left.localeCompare(right));
  for (const directory of sortedDirectories) {
    nodes.push({
      id: directoryNodeId(snapshot.identity.project_id, directory),
      kind: "directory",
      label: directory.split("/").at(-1) ?? directory,
      path: directory,
      language: null,
      extraction_status: null,
      symbols: [],
    });
  }

  for (const directory of sortedDirectories) {
    const parent = directory.includes("/") ? directory.slice(0, directory.lastIndexOf("/")) : null;
    edges.push({
      id: `contains:${directory}`,
      source: parent ? directoryNodeId(snapshot.identity.project_id, parent) : projectId,
      target: directoryNodeId(snapshot.identity.project_id, directory),
      kind: "contains",
      certainty: "observed",
      evidence: "source_snapshot.files[].relative_path",
    });
  }
  for (const file of selectedFiles) {
    const parent = file.relative_path.includes("/")
      ? file.relative_path.slice(0, file.relative_path.lastIndexOf("/"))
      : null;
    edges.push({
      id: `contains:file:${file.relative_path}`,
      source: parent ? directoryNodeId(snapshot.identity.project_id, parent) : projectId,
      target: fileNodeId(snapshot.identity.project_id, file.relative_path),
      kind: "contains",
      certainty: "observed",
      evidence: "source_snapshot.files[].relative_path",
    });
  }

  const seenRelations = new Set<string>();
  let unresolvedCandidates = 0;
  let importCandidates = 0;
  let lineageCandidates = 0;
  for (const file of selectedFiles) {
    const source = fileNodeId(snapshot.identity.project_id, file.relative_path);
    for (const imported of file.imports) {
      const targetPath = resolveImport(file.relative_path, imported, selectedPaths);
      if (!targetPath) {
        unresolvedCandidates += 1;
        continue;
      }
      const relationKey = `${source}|${targetPath}|import_candidate`;
      if (seenRelations.has(relationKey)) continue;
      seenRelations.add(relationKey);
      importCandidates += 1;
      edges.push({
        id: `import:${file.relative_path}:${targetPath}`,
        source,
        target: fileNodeId(snapshot.identity.project_id, targetPath),
        kind: "import_candidate",
        certainty: "candidate",
        evidence: "source_snapshot.files[].imports; lexical resolution",
      });
    }
  }
  for (const lineage of snapshot.lineage) {
    if (!selectedPaths.has(lineage.source_path)) continue;
    const source = fileNodeId(snapshot.identity.project_id, lineage.source_path);
    for (const candidate of lineage.candidate_paths) {
      const targetPath = resolveImport(lineage.source_path, candidate, selectedPaths);
      if (!targetPath) {
        unresolvedCandidates += 1;
        continue;
      }
      const relationKey = `${source}|${targetPath}|lineage_candidate`;
      if (seenRelations.has(relationKey)) continue;
      seenRelations.add(relationKey);
      lineageCandidates += 1;
      edges.push({
        id: `lineage:${lineage.source_path}:${lineage.symbol}:${targetPath}`,
        source,
        target: fileNodeId(snapshot.identity.project_id, targetPath),
        kind: "lineage_candidate",
        certainty: "candidate",
        evidence: "source_snapshot.lineage; candidate only",
      });
    }
  }

  const fileNodes = nodes.filter((node) => node.kind === "file");
  return {
    schema: WORKSPACE_GRAPH_SCHEMA,
    project_id: snapshot.identity.project_id,
    source_revision: snapshot.revision,
    generated_at_ms: Date.now(),
    nodes,
    edges,
    truncated: snapshot.overflowed || files.length > selectedFiles.length,
    stats: {
      files: fileNodes.length,
      snapshot_files: files.length,
      source_overflowed: snapshot.overflowed,
      directories: sortedDirectories.length,
      symbols: fileNodes.reduce((total, node) => total + node.symbols.length, 0),
      import_candidates: importCandidates,
      lineage_candidates: lineageCandidates,
      unresolved_candidates: unresolvedCandidates,
      omitted_files: Math.max(0, files.length - selectedFiles.length),
    },
  };
}

export function memorySearchCandidateSource(
  graph: WorkspaceGraph,
  node: WorkspaceGraphNode,
): WorkspaceGraphMemoryCandidateSource | null {
  if (node.kind !== "file" || !node.path) return null;
  return {
    certainty: "candidate",
    basis: "lexical_path_search",
    verified_relation: false,
    project_id: graph.project_id,
    source_revision: graph.source_revision,
    source_path: node.path,
    source_content_hash: node.source_content_hash ?? null,
    source_state: node.source_stale ? "stale" : "current",
  };
}

export function isMemorySearchCandidateCurrent(
  source: WorkspaceGraphMemoryCandidateSource,
  graph: WorkspaceGraph,
  node: WorkspaceGraphNode,
): boolean {
  const current = memorySearchCandidateSource(graph, node);
  return current !== null
    && current.project_id === source.project_id
    && current.source_revision === source.source_revision
    && current.source_path === source.source_path
    && current.source_content_hash === source.source_content_hash
    && current.source_state === source.source_state;
}

export type MemoryFileRelationStatus = "Current" | "Stale source" | "Stale memory" | "Memory not active";

export function memoryFileRelationStatus(
  relation: MemoryFileRelation,
  graph: WorkspaceGraph,
  node: WorkspaceGraphNode,
): MemoryFileRelationStatus {
  const sourceMatches = node.kind === "file"
    && relation.project_id === fileMemoryRelationProjectId(graph.project_id)
    && relation.relative_path === node.path
    && node.source_content_hash !== null
    && node.source_content_hash !== undefined
    && relation.source_content_hash === node.source_content_hash
    && !node.source_stale;
  if (!sourceMatches) return "Stale source";
  const memoryEligible = (relation.memory_lifecycle === "CANDIDATE" || relation.memory_lifecycle === "ACTIVE")
    && (relation.memory_validation === "UNREVIEWED" || relation.memory_validation === "ACCEPTED");
  if (!memoryEligible) {
    return "Memory not active";
  }
  if (!relation.memory_content_current) return "Stale memory";
  return "Current";
}

export function isMemoryFileRelationSourceCurrent(
  source: WorkspaceGraphMemoryCandidateSource,
  graph: WorkspaceGraph,
): boolean {
  if (source.project_id !== graph.project_id) return false;
  const node = graph.nodes.find((candidate) => candidate.kind === "file" && candidate.path === source.source_path);
  const current = node ? memorySearchCandidateSource(graph, node) : null;
  return current !== null
    && current.project_id === source.project_id
    && current.source_path === source.source_path
    && current.source_content_hash === source.source_content_hash
    && current.source_state === source.source_state;
}

export function fileNodeId(projectId: string, path: string): string {
  return `file:${projectId}:${path}`;
}

function directoryNodeId(projectId: string, path: string): string {
  return `directory:${projectId}:${path}`;
}

function normalizeRelativeImportSpec(raw: string): string {
  if (!raw.startsWith(".") || raw.includes("/")) return raw;
  const match = /^(\.+)(.*)$/.exec(raw);
  if (!match) return raw;
  const dots = match[1].length;
  const rest = match[2].replaceAll(".", "/");
  const prefix = dots === 1 ? "." : Array.from({ length: dots - 1 }, () => "..").join("/");
  return rest ? `${prefix}/${rest}` : prefix;
}

function resolveImport(sourcePath: string, imported: string, knownPaths: Set<string>): string | null {
  const raw = imported.trim().replace(/^['"]|['"]$/g, "").replaceAll("\\", "/");
  if (!raw || raw.startsWith("/")) return null;
  const candidates = new Set<string>();
  const sourceDirectory = sourcePath.includes("/") ? sourcePath.slice(0, sourcePath.lastIndexOf("/")) : "";
  const normalizedSpec = normalizeRelativeImportSpec(raw);
  const base = normalizedSpec.startsWith(".")
    ? joinPath(sourceDirectory, normalizedSpec)
    : joinPath("", normalizedSpec);
  if (base === null) return null;
  candidates.add(base);
  if (!raw.startsWith(".") && !raw.includes("/") && raw.includes(".")) {
    candidates.add(raw.replaceAll(".", "/"));
  }
  for (const candidate of [...candidates]) {
    const withoutExtension = candidate.replace(/\.(py|rs|ts|tsx|js|jsx|json)$/, "");
    for (const suffix of IMPORT_SUFFIXES) candidates.add(`${withoutExtension}${suffix}`);
    candidates.add(`${withoutExtension}/__init__.py`);
    candidates.add(`${withoutExtension}/mod.rs`);
  }
  for (const candidate of candidates) {
    const normalized = candidate.replace(/^\.\//, "");
    if (knownPaths.has(normalized)) return normalized;
  }
  return null;
}

function joinPath(directory: string, child: string): string | null {
  const parts = (directory ? `${directory}/${child}` : child).split("/");
  const result: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (result.length === 0) return null;
      result.pop();
    } else {
      result.push(part);
    }
  }
  return result.join("/");
}

export function orderWorkspaceGraphNodes(
  files: WorkspaceGraphNode[],
  edges: WorkspaceGraphEdge[],
): WorkspaceGraphNode[] {
  if (files.length <= 1) return files;
  const visibleIds = new Set(files.map((file) => file.id));
  const outDegree = new Map<string, number>();
  const inDegree = new Map<string, number>();
  let hasDependencyEdges = false;
  for (const edge of edges) {
    if (edge.kind === "contains") continue;
    if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) continue;
    hasDependencyEdges = true;
    outDegree.set(edge.source, (outDegree.get(edge.source) ?? 0) + 1);
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
  }
  if (!hasDependencyEdges) return files;
  const decorated = files.map((node) => {
    const path = node.path ?? "";
    const sep = path.lastIndexOf("/");
    const folder = sep < 0 ? "" : path.slice(0, sep);
    const inDeg = inDegree.get(node.id) ?? 0;
    const outDeg = outDegree.get(node.id) ?? 0;
    return {
      node,
      folder,
      connections: inDeg + outDeg,
      inDeg,
      sortKey: path || node.label,
    };
  });
  decorated.sort((left, right) => {
    if (left.folder !== right.folder) {
      return left.folder < right.folder ? -1 : 1;
    }
    if (left.connections !== right.connections) {
      return right.connections - left.connections;
    }
    if (left.inDeg !== right.inDeg) {
      return right.inDeg - left.inDeg;
    }
    return left.sortKey < right.sortKey ? -1 : left.sortKey > right.sortKey ? 1 : 0;
  });
  return decorated.map((item) => item.node);
}

export function workspaceGraphEdgePath(
  from: { x: number; y: number },
  to: { x: number; y: number },
): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const c1x = Math.round(from.x + dx * 0.45);
    const c2x = Math.round(to.x - dx * 0.45);
    return `M ${from.x} ${from.y} C ${c1x} ${from.y}, ${c2x} ${to.y}, ${to.x} ${to.y}`;
  }
  const c1y = Math.round(from.y + dy * 0.45);
  const c2y = Math.round(to.y - dy * 0.45);
  return `M ${from.x} ${from.y} C ${from.x} ${c1y}, ${to.x} ${c2y}, ${to.x} ${to.y}`;
}
