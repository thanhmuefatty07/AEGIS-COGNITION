import type { SourceSnapshot } from "./protocol";

export const WORKSPACE_GRAPH_SCHEMA = "aegis-workspace-graph-v1" as const;

export type WorkspaceGraphNodeKind = "project" | "directory" | "file";
export type WorkspaceGraphEdgeKind = "contains" | "import_candidate" | "lineage_candidate";
export type WorkspaceGraphCertainty = "observed" | "candidate";

export type WorkspaceGraphNode = {
  id: string;
  kind: WorkspaceGraphNodeKind;
  label: string;
  path: string | null;
  language: string | null;
  extraction_status: string | null;
  symbols: SourceSnapshot["files"][number]["symbols"];
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
    directories: number;
    symbols: number;
    import_candidates: number;
    lineage_candidates: number;
    unresolved_candidates: number;
    omitted_files: number;
  };
};

const DEFAULT_FILE_LIMIT = 600;
const IMPORT_SUFFIXES = ["", ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".json"];

export function buildWorkspaceGraph(
  snapshot: SourceSnapshot,
  options: { maxFiles?: number } = {},
): WorkspaceGraph {
  const maxFiles = options.maxFiles ?? DEFAULT_FILE_LIMIT;
  const files = [...snapshot.files].sort((left, right) => left.relative_path.localeCompare(right.relative_path));
  const selectedFiles = files.slice(0, maxFiles);
  const selectedPaths = new Set(selectedFiles.map((file) => file.relative_path));
  const projectId = `project:${snapshot.identity.project_id}`;
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
      directories: sortedDirectories.length,
      symbols: fileNodes.reduce((total, node) => total + node.symbols.length, 0),
      import_candidates: edges.filter((edge) => edge.kind === "import_candidate").length,
      lineage_candidates: edges.filter((edge) => edge.kind === "lineage_candidate").length,
      unresolved_candidates: unresolvedCandidates,
      omitted_files: Math.max(0, files.length - selectedFiles.length),
    },
  };
}

export function fileNodeId(projectId: string, path: string): string {
  return `file:${projectId}:${path}`;
}

function directoryNodeId(projectId: string, path: string): string {
  return `directory:${projectId}:${path}`;
}

function resolveImport(sourcePath: string, imported: string, knownPaths: Set<string>): string | null {
  const raw = imported.trim().replace(/^['"]|['"]$/g, "").replaceAll("\\", "/");
  if (!raw) return null;
  const candidates = new Set<string>();
  const sourceDirectory = sourcePath.includes("/") ? sourcePath.slice(0, sourcePath.lastIndexOf("/")) : "";
  const base = raw.startsWith(".") ? joinPath(sourceDirectory, raw) : raw;
  candidates.add(base);
  if (!raw.includes("/") && raw.includes(".")) candidates.add(raw.replaceAll(".", "/"));
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

function joinPath(directory: string, child: string): string {
  const parts = `${directory}/${child}`.split("/");
  const result: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") result.pop();
    else result.push(part);
  }
  return result.join("/");
}
