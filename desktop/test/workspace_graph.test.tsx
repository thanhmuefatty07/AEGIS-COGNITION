// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspaceGraphView from "../src/WorkspaceGraphView";
import * as desktopProtocol from "../src/protocol";
import type { MemoryFileRelationPage, MemoryRecord, SourceSnapshot } from "../src/protocol";
import {
  buildWorkspaceGraph,
  isMemorySearchCandidateCurrent,
  memorySearchCandidateSource,
} from "../src/workspace_graph";

afterEach(cleanup);

function snapshot({
  projectId = "project-a",
  revision = "revision-a",
  contentHash = "a".repeat(64),
  stalePaths = [],
}: {
  projectId?: string;
  revision?: string;
  contentHash?: string;
  stalePaths?: string[];
} = {}): SourceSnapshot {
  return {
    identity: {
      project_id: projectId,
      root: "workspace",
      vcs: "git",
      repository_root: null,
      common_git_dir: null,
      checkout_id: "checkout-a",
      branch: "main",
      head: "head-a",
      dirty: false,
      dirty_paths: [],
    },
    revision,
    created_at_ms: 1,
    files: [{
      relative_path: "src/agent.py",
      language: "Python",
      size_bytes: 10,
      modified_ns: 1,
      content_hash: contentHash,
      extraction_status: "COMPLETE",
      symbols: [],
      imports: [],
      error: null,
      symlink_target: null,
    }],
    lineage: [],
    stale_paths: stalePaths,
    deleted_paths: [],
    overflowed: false,
  };
}

function snapshotWithFileCount(count: number, overflowed = false): SourceSnapshot {
  const base = snapshot();
  return {
    ...base,
    files: Array.from({ length: count }, (_, index) => ({
      ...base.files[0],
      relative_path: (index % 2 === 0 ? "src" : "tests") + "/file-" + String(index).padStart(3, "0") + ".py",
      content_hash: index.toString(16).padStart(64, "0"),
    })),
    overflowed,
  };
}

function snapshotWithPaths(paths: string[]): SourceSnapshot {
  const base = snapshot();
  return {
    ...base,
    files: paths.map((relative_path, index) => ({
      ...base.files[0],
      relative_path,
      content_hash: index.toString(16).padStart(64, "0"),
    })),
  };
}

const memoryRecord: MemoryRecord = {
  memory_id: "0x2a",
  owner_id: "private-owner",
  scope_kind: "USER_PRIVATE",
  lifecycle: "ACTIVE",
  validation: "ACCEPTED",
  revision: 4,
  observed_at_ms: 10,
  content_hash: "b".repeat(64),
  content: null,
  memory_kind: "SEMANTIC_FACT",
};

const fileRelation = {
  relation_id: "0x01",
  memory_id: "0x2a",
  scope_kind: "USER_PRIVATE",
  project_id: "project:project-a",
  relative_path: "src/agent.py",
  source_revision: "revision-a",
  source_content_hash: "a".repeat(64),
  memory_revision: 4,
  memory_content_hash: "b".repeat(64),
  created_at_ms: 11,
  memory_lifecycle: "ACTIVE",
  memory_validation: "ACCEPTED",
  memory_content_current: true,
};

describe("workspace graph memory search candidates", () => {
  it("explains how to reach memory details before a file is selected", () => {
    const loadMemories = vi.fn().mockResolvedValue([]);
    const loadFileRelations = vi.fn().mockResolvedValue([]);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={loadMemories}
      loadFileRelations={loadFileRelations}
    />);

    const inspector = screen.getByRole("complementary", { name: "Selected project map item" });
    expect(within(inspector).getByText(
      /Select a file card in Graph or a row in List to inspect saved memory links and search local memory candidates\./,
    )).toBeDefined();
    expect(within(inspector).getByText(
      /Save link button is offered only for a current file and a current, eligible candidate that is not already linked\./,
    )).toBeDefined();
    expect(within(inspector).queryByRole("textbox", { name: "Search memory text for this file" })).toBeNull();
    expect(within(inspector).queryByRole("button", { name: /Save link/ })).toBeNull();
    expect(loadMemories).not.toHaveBeenCalled();
    expect(loadFileRelations).not.toHaveBeenCalled();
  });

  it("binds lexical candidates to project, revision, path, and source content hash", () => {
    const graph = buildWorkspaceGraph(snapshot());
    const file = graph.nodes.find((node) => node.kind === "file");
    expect(file).toBeDefined();
    const candidate = memorySearchCandidateSource(graph, file!);

    expect(candidate).toMatchObject({
      certainty: "candidate",
      basis: "lexical_path_search",
      verified_relation: false,
      project_id: "project-a",
      source_revision: "revision-a",
      source_path: "src/agent.py",
      source_content_hash: "a".repeat(64),
      source_state: "current",
    });
    expect(isMemorySearchCandidateCurrent(candidate!, graph, file!)).toBe(true);
    const otherGraph = buildWorkspaceGraph(snapshot({ projectId: "project-b" }));
    const otherFile = otherGraph.nodes.find((node) => node.kind === "file")!;
    expect(isMemorySearchCandidateCurrent(candidate!, otherGraph, otherFile)).toBe(false);
  });

  it("marks stale source snapshots without promoting their path matches", () => {
    const graph = buildWorkspaceGraph(snapshot({ stalePaths: ["src/agent.py"] }));
    const file = graph.nodes.find((node) => node.kind === "file")!;

    expect(memorySearchCandidateSource(graph, file)).toMatchObject({
      certainty: "candidate",
      verified_relation: false,
      source_state: "stale",
    });
  });

  it("shows a stale-source warning beside file-memory candidates", async () => {
    const graph = buildWorkspaceGraph(snapshot({ stalePaths: ["src/agent.py"] }));
    render(<WorkspaceGraphView graph={graph} loadMemories={vi.fn().mockResolvedValue([memoryRecord])} />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText(/file path is marked stale/i)).toBeDefined();
    expect(screen.getByText(/not verified file-memory relationships/i)).toBeDefined();
  });

  it("does not offer to promote a stale file-path candidate to a saved relation", async () => {
    const linkMemoryToFile = vi.fn().mockResolvedValue(fileRelation);
    const graph = buildWorkspaceGraph(snapshot({ stalePaths: ["src/agent.py"] }));
    render(<WorkspaceGraphView
      graph={graph}
      loadMemories={vi.fn().mockResolvedValue([memoryRecord])}
      linkMemoryToFile={linkMemoryToFile}
    />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText("0x2a")).toBeDefined();
    expect(screen.getByText(/file path is marked stale/i)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save link from 0x2a to src/agent.py" })).toBeNull();
    expect(screen.getByText(/link can be saved only when the selected source snapshot and candidate hashes are current/i)).toBeDefined();
    expect(linkMemoryToFile).not.toHaveBeenCalled();
  });

  it("finds a candidate with explicit memory text search and saves only after the user chooses it", async () => {
    const loadMemories = vi.fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([memoryRecord]);
    const linkMemoryToFile = vi.fn().mockResolvedValue(fileRelation);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={loadMemories}
      loadFileRelations={vi.fn().mockResolvedValue([])}
      linkMemoryToFile={linkMemoryToFile}
    />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    expect(await screen.findByText("No lexical path matches.")).toBeDefined();
    const memoryQuery = screen.getByRole("textbox", { name: "Search memory text for this file" });
    fireEvent.change(memoryQuery, { target: { value: "parser limit 100" } });
    fireEvent.click(screen.getByRole("button", { name: "Search memories" }));

    expect(await screen.findByText("0x2a")).toBeDefined();
    expect(loadMemories).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ kind: "file", path: "src/agent.py" }),
      "parser limit 100",
    );
    expect(linkMemoryToFile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save link from 0x2a to src/agent.py" }));
    expect(await screen.findByText("Saved relation added.")).toBeDefined();
    expect(linkMemoryToFile).toHaveBeenCalledWith({
      project_id: "project:project-a",
      source_revision: "revision-a",
      relative_path: "src/agent.py",
      source_content_hash: "a".repeat(64),
      memory_id: "0x2a",
      scope_kind: "USER_PRIVATE",
      memory_revision: 4,
      memory_content_hash: "b".repeat(64),
    });
  });

  it("distinguishes an empty manual memory search from an empty path search", async () => {
    const loadMemories = vi.fn().mockResolvedValue([]);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={loadMemories}
    />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    expect(await screen.findByText("No lexical path matches.")).toBeDefined();

    const memoryQuery = screen.getByRole("textbox", { name: "Search memory text for this file" });
    fireEvent.change(memoryQuery, { target: { value: "no matching memory text" } });
    fireEvent.click(screen.getByRole("button", { name: "Search memories" }));

    expect(await screen.findByText("No memory candidates matched that search.")).toBeDefined();
    expect(screen.queryByText("No lexical path matches.")).toBeNull();
    expect(loadMemories).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ kind: "file", path: "src/agent.py" }),
      "no matching memory text",
    );
  });

  it("labels search results as candidates and hides them when the source revision changes", async () => {
    let resolveSearch!: (records: MemoryRecord[]) => void;
    const loadMemories = vi.fn(() => new Promise<MemoryRecord[]>((resolve) => {
      resolveSearch = resolve;
    }));
    const firstGraph = buildWorkspaceGraph(snapshot());
    const view = render(<WorkspaceGraphView graph={firstGraph} loadMemories={loadMemories} />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    expect(screen.getByText(/local memory search only/i)).toBeDefined();
    expect(screen.getByText(/searching owner-scoped memory candidates/i)).toBeDefined();
    await act(async () => { await Promise.resolve(); });

    view.rerender(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-b", contentHash: "c".repeat(64) }))}
      loadMemories={loadMemories}
    />);

    expect(screen.getByText(/results hidden because the source snapshot changed/i)).toBeDefined();
    expect(screen.queryByText("0x2a")).toBeNull();
    expect(screen.queryByText("b".repeat(64))).toBeNull();
    await act(async () => resolveSearch([memoryRecord]));
    expect(screen.queryByText("0x2a")).toBeNull();
    expect(loadMemories).toHaveBeenCalledTimes(1);
  });

  it("shows record and file provenance without exposing the owner ID", async () => {
    const loadMemories = vi.fn().mockResolvedValue([memoryRecord]);
    const graph = buildWorkspaceGraph(snapshot());
    render(<WorkspaceGraphView graph={graph} loadMemories={loadMemories} />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    expect(await screen.findByText("0x2a")).toBeDefined();
    expect(screen.getByText(/USER_PRIVATE/)).toBeDefined();
    expect(screen.getByText(/active.*accepted.*r4/i)).toBeDefined();
    expect(screen.queryByText("private-owner")).toBeNull();
    fireEvent.click(screen.getByText("Source and memory provenance"));
    expect(screen.getByText("revision-a")).toBeDefined();
    expect(screen.getByText("a".repeat(64))).toBeDefined();
    expect(screen.getByText("b".repeat(64))).toBeDefined();
  });
});

describe("confirmed file-memory relations", () => {
  it("parses relation command results and rejects owner IDs or malformed provenance", () => {
    expect(desktopProtocol.parseMemoryFileRelationsResult({ relations: [fileRelation] })).toEqual({
      relations: [fileRelation],
      next_cursor: null,
    });
    expect(desktopProtocol.parseMemoryFileRelationsResult({
      relations: [fileRelation],
      next_cursor: "opaque-page-token",
    })).toEqual({
      relations: [fileRelation],
      next_cursor: "opaque-page-token",
    });
    expect(() => desktopProtocol.parseMemoryFileRelationsResult({
      relations: [fileRelation],
      next_cursor: { created_at_ms: 10, relation_id: "0x01" },
    })).toThrow();
    expect(() => desktopProtocol.parseMemoryFileRelationsResult({ relations: [fileRelation], next_cursor: "" })).toThrow();
    expect(() => desktopProtocol.parseMemoryFileRelationsResult({
      relations: [fileRelation],
      next_cursor: "x".repeat(513),
    })).toThrow();
    expect(desktopProtocol.parseMemoryFileRelationLinkResult({ relation: fileRelation })).toEqual(fileRelation);
    expect(desktopProtocol.parseMemoryFileRelationUnlinkResult({ removed: true })).toEqual({ removed: true });
    expect(() => desktopProtocol.parseMemoryFileRelationsResult({ relations: [{ ...fileRelation, owner_id: "private-owner" }] })).toThrow();
    expect(() => desktopProtocol.parseMemoryFileRelationsResult({ relations: [{ ...fileRelation, source_content_hash: "A".repeat(64) }] })).toThrow();
    expect(() => desktopProtocol.parseMemoryFileRelationUnlinkResult({ removed: "true" })).toThrow();
  });

  it("keeps lexical results as candidates and saves the exact relation only after an explicit click", async () => {
    const loadMemories = vi.fn().mockResolvedValue([memoryRecord]);
    const loadFileRelations = vi.fn().mockResolvedValue([]);
    const linkMemoryToFile = vi.fn().mockResolvedValue(fileRelation);
    const unlinkFileRelation = vi.fn().mockResolvedValue(true);
    const graph = buildWorkspaceGraph(snapshot());
    render(<WorkspaceGraphView
      graph={graph}
      loadMemories={loadMemories}
      loadFileRelations={loadFileRelations}
      linkMemoryToFile={linkMemoryToFile}
      unlinkFileRelation={unlinkFileRelation}
    />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    expect(await screen.findByRole("heading", { name: "Saved file-memory relations" })).toBeDefined();
    expect(loadFileRelations).toHaveBeenCalledWith({ project_id: "project:project-a", relative_path: "src/agent.py", limit: 24 });
    expect(screen.getByText(/Candidate · semantic fact/)).toBeDefined();
    expect(linkMemoryToFile).not.toHaveBeenCalled();
    expect(unlinkFileRelation).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save link from 0x2a to src/agent.py" }));

    expect(await screen.findByText("Current")).toBeDefined();
    expect(linkMemoryToFile).toHaveBeenCalledWith({
      project_id: "project:project-a",
      source_revision: "revision-a",
      relative_path: "src/agent.py",
      source_content_hash: "a".repeat(64),
      memory_id: "0x2a",
      scope_kind: "USER_PRIVATE",
      memory_revision: 4,
      memory_content_hash: "b".repeat(64),
    });
    expect(screen.getByText((_, element) => (
      element?.textContent === "Project project:project-a · owner ID hidden (enforced by local memory service)"
    ))).toBeDefined();
    expect(screen.getByText(/Candidate · semantic fact/)).toBeDefined();
    expect(screen.queryByText("private-owner")).toBeNull();
    expect(unlinkFileRelation).not.toHaveBeenCalled();
  });

  it("shows the memory inspector and saves a provenance-bound relation after selecting a List row", async () => {
    const loadMemories = vi.fn().mockResolvedValue([memoryRecord]);
    const loadFileRelations = vi.fn().mockResolvedValue([]);
    const linkMemoryToFile = vi.fn().mockResolvedValue(fileRelation);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={loadMemories}
      loadFileRelations={loadFileRelations}
      linkMemoryToFile={linkMemoryToFile}
    />);

    fireEvent.click(screen.getByRole("button", { name: "List" }));
    const listRegion = screen.getByRole("region", { name: "Project files and relationships" });
    fireEvent.click(screen.getByRole("button", { name: /src\/agent\.py/ }));

    const inspector = await screen.findByRole("complementary", { name: "Selected project map item" });
    expect(within(inspector).getByRole("heading", { name: "Saved file-memory relations" })).toBeDefined();
    expect(within(inspector).getByRole("heading", { name: "Memory search candidates" })).toBeDefined();
    expect(await within(inspector).findByText(/Candidate · semantic fact/)).toBeDefined();
    expect(linkMemoryToFile).not.toHaveBeenCalled();

    fireEvent.click(within(inspector).getByRole("button", { name: "Save link from 0x2a to src/agent.py" }));

    expect(await within(inspector).findByText("Current")).toBeDefined();
    expect(linkMemoryToFile).toHaveBeenCalledWith({
      project_id: "project:project-a",
      source_revision: "revision-a",
      relative_path: "src/agent.py",
      source_content_hash: "a".repeat(64),
      memory_id: "0x2a",
      scope_kind: "USER_PRIVATE",
      memory_revision: 4,
      memory_content_hash: "b".repeat(64),
    });
    expect(within(inspector).getByText((_, element) => (
      element?.textContent === "Project project:project-a · owner ID hidden (enforced by local memory service)"
    ))).toBeDefined();
    expect(within(inspector).queryByText("private-owner")).toBeNull();
    expect(listRegion.getAttribute("tabindex")).toBe("0");
  });

  it("loads saved relations by bounded keyset pages and announces appended results", async () => {
    const nextCursor = "opaque-page-token";
    const secondRelation = { ...fileRelation, relation_id: "0x02", memory_id: "0x2b", created_at_ms: 11 };
    const loadFileRelations = vi.fn()
      .mockResolvedValueOnce({ relations: [fileRelation], next_cursor: nextCursor } as never)
      .mockResolvedValueOnce({ relations: [secondRelation], next_cursor: null } as never);
    render(<WorkspaceGraphView graph={buildWorkspaceGraph(snapshot())} loadFileRelations={loadFileRelations} />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    const loadMore = await screen.findByRole("button", { name: "Load more saved relations" });
    expect(loadFileRelations).toHaveBeenNthCalledWith(1, {
      project_id: "project:project-a",
      relative_path: "src/agent.py",
      limit: 24,
    });
    fireEvent.click(loadMore);

    expect(await screen.findByText((_, element) => element?.textContent === "0x2b · USER_PRIVATE")).toBeDefined();
    expect(loadFileRelations).toHaveBeenNthCalledWith(2, {
      project_id: "project:project-a",
      relative_path: "src/agent.py",
      limit: 24,
      cursor: nextCursor,
    });
    const relationsRegion = screen.getByRole("region", { name: "Saved file-memory relations" });
    expect(within(relationsRegion).getByRole("status").textContent).toMatch(/loaded 1 more saved relation/i);
  });

  it("restarts saved relation history after a cursor expires", async () => {
    const loadFileRelations = vi.fn()
      .mockResolvedValueOnce({ relations: [fileRelation], next_cursor: "expired-page-token" } as never)
      .mockRejectedValueOnce(new Error("INVALID_ARGUMENT: cursor is invalid for this relation query"))
      .mockResolvedValueOnce({
        relations: [{ ...fileRelation, relation_id: "0x02", memory_id: "0x2b" }],
        next_cursor: "fresh-page-token",
      } as never);
    render(<WorkspaceGraphView graph={buildWorkspaceGraph(snapshot())} loadFileRelations={loadFileRelations} />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Load more saved relations" }));
    expect(await screen.findByRole("alert")).toBeDefined();

    fireEvent.click(await screen.findByRole("button", { name: "Restart saved relation history" }));

    expect(await screen.findByText((_, element) => element?.textContent === "0x2b · USER_PRIVATE")).toBeDefined();
    expect(loadFileRelations).toHaveBeenNthCalledWith(3, {
      project_id: "project:project-a",
      relative_path: "src/agent.py",
      limit: 24,
    });
    expect(screen.getByRole("button", { name: "Load more saved relations" })).toBeDefined();
  });

  it("links retrieval-eligible candidate memories without promoting their lifecycle or validation", async () => {
    const candidateMemory = { ...memoryRecord, lifecycle: "CANDIDATE", validation: "UNREVIEWED" };
    const candidateRelation = { ...fileRelation, memory_lifecycle: "CANDIDATE", memory_validation: "UNREVIEWED" };
    const linkMemoryToFile = vi.fn().mockResolvedValue(candidateRelation);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([candidateMemory])}
      loadFileRelations={vi.fn().mockResolvedValue([])}
      linkMemoryToFile={linkMemoryToFile}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText(/candidate · unreviewed · r4/i)).toBeDefined();
    fireEvent.click(await screen.findByRole("button", { name: "Save link from 0x2a to src/agent.py" }));

    expect(await screen.findByText("Current")).toBeDefined();
    expect(screen.getByText(/candidate · unreviewed · r4/i)).toBeDefined();
    expect(linkMemoryToFile).toHaveBeenCalledWith({
      project_id: "project:project-a",
      source_revision: "revision-a",
      relative_path: "src/agent.py",
      source_content_hash: "a".repeat(64),
      memory_id: "0x2a",
      scope_kind: "USER_PRIVATE",
      memory_revision: 4,
      memory_content_hash: "b".repeat(64),
    });
  });

  it("preserves relation history pagination when a link is saved", async () => {
    let resolveFirstPage!: (page: MemoryFileRelationPage) => void;
    const loadFileRelations = vi.fn()
      .mockImplementationOnce(() => new Promise<MemoryFileRelationPage>((resolve) => {
        resolveFirstPage = resolve;
      }))
      .mockResolvedValueOnce({ relations: [], next_cursor: null } as never);
    const candidateMemory = {
      ...memoryRecord,
      memory_id: "0x2b",
      revision: 5,
      content_hash: "c".repeat(64),
    };
    const savedRelation = {
      ...fileRelation,
      relation_id: "0x02",
      memory_id: candidateMemory.memory_id,
      memory_revision: candidateMemory.revision,
      memory_content_hash: candidateMemory.content_hash,
    };
    const linkMemoryToFile = vi.fn().mockResolvedValue(savedRelation);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([candidateMemory])}
      loadFileRelations={loadFileRelations}
      linkMemoryToFile={linkMemoryToFile}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    const saveLink = await screen.findByRole("button", { name: "Save link from 0x2b to src/agent.py" });
    await waitFor(() => expect(loadFileRelations).toHaveBeenCalledTimes(1));
    expect((saveLink as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(saveLink);
    expect(linkMemoryToFile).not.toHaveBeenCalled();

    await act(async () => resolveFirstPage({ relations: [fileRelation], next_cursor: "page-2" }));
    expect((saveLink as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(saveLink);
    expect(await screen.findByText("Saved relation added.")).toBeDefined();
    expect(screen.getByText((_, element) => element?.textContent === "0x2b · USER_PRIVATE")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Load more saved relations" }));
    await waitFor(() => expect(loadFileRelations).toHaveBeenCalledTimes(2));
    expect(loadFileRelations).toHaveBeenNthCalledWith(2, {
      project_id: "project:project-a",
      relative_path: "src/agent.py",
      limit: 24,
      cursor: "page-2",
    });
  });

  it("keeps relation pagination active while a later page is loading", async () => {
    let resolveNextPage!: (page: MemoryFileRelationPage) => void;
    const loadFileRelations = vi.fn()
      .mockResolvedValueOnce({ relations: [fileRelation], next_cursor: "page-2" } as never)
      .mockImplementationOnce(() => new Promise<MemoryFileRelationPage>((resolve) => {
        resolveNextPage = resolve;
      }));
    const unlinkFileRelation = vi.fn().mockResolvedValue(true);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadFileRelations={loadFileRelations}
      unlinkFileRelation={unlinkFileRelation}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Load more saved relations" }));

    const removeLink = await screen.findByRole("button", { name: "Remove only relation 0x01" });
    await waitFor(() => expect(loadFileRelations).toHaveBeenCalledTimes(2));
    expect((removeLink as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(removeLink);
    expect(unlinkFileRelation).not.toHaveBeenCalled();

    await act(async () => resolveNextPage({ relations: [], next_cursor: null }));
    expect(screen.queryByText("Loading saved relations…")).toBeNull();
  });

  it("hides saved relations and clears pagination when the source changes during a later page", async () => {
    let resolveNextPage!: (page: MemoryFileRelationPage) => void;
    const loadFileRelations = vi.fn()
      .mockResolvedValueOnce({ relations: [fileRelation], next_cursor: "page-2" } as never)
      .mockImplementationOnce(() => new Promise<MemoryFileRelationPage>((resolve) => {
        resolveNextPage = resolve;
      }));
    const view = render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadFileRelations={loadFileRelations}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Load more saved relations" }));
    await waitFor(() => expect(loadFileRelations).toHaveBeenCalledTimes(2));
    expect(screen.getAllByText("Loading saved relations…")).toHaveLength(2);

    view.rerender(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-b", contentHash: "c".repeat(64) }))}
      loadFileRelations={loadFileRelations}
    />);

    expect(screen.getByText(/saved links were hidden because the source changed/i)).toBeDefined();
    expect(screen.queryByText("Loading saved relations…")).toBeNull();
    expect(screen.queryByText("Saved file hash")).toBeNull();

    await act(async () => resolveNextPage({ relations: [{ ...fileRelation, relation_id: "0x02" }], next_cursor: null }));
    expect(screen.getByText(/saved links were hidden because the source changed/i)).toBeDefined();
    expect(screen.queryByText("Saved file hash")).toBeNull();
  });

  it("hides a confirmed-link response after the selected source hash changes", async () => {
    let resolveLink!: (relation: typeof fileRelation) => void;
    const linkMemoryToFile = vi.fn(() => new Promise<typeof fileRelation>((resolve) => {
      resolveLink = resolve;
    }));
    const view = render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([memoryRecord])}
      loadFileRelations={vi.fn().mockResolvedValue([])}
      linkMemoryToFile={linkMemoryToFile}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Save link from 0x2a to src/agent.py" }));

    view.rerender(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-b", contentHash: "c".repeat(64) }))}
      loadMemories={vi.fn().mockResolvedValue([memoryRecord])}
      loadFileRelations={vi.fn().mockResolvedValue([])}
      linkMemoryToFile={linkMemoryToFile}
    />);
    await act(async () => resolveLink(fileRelation));

    expect(screen.queryByText("Saved file hash")).toBeNull();
    expect(screen.queryByText("Stale source")).toBeNull();
    expect(screen.getByText(/results hidden because the source snapshot changed/i)).toBeDefined();
    expect(linkMemoryToFile).toHaveBeenCalledTimes(1);
  });

  it("shows stale source when the target file hash changes, but not when only the workspace revision changes", async () => {
    const graph = buildWorkspaceGraph(snapshot());
    const view = render(<WorkspaceGraphView
      graph={graph}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={vi.fn().mockResolvedValue([fileRelation])}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText("Current")).toBeDefined();
    view.rerender(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-b" }))}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={vi.fn().mockResolvedValue([fileRelation])}
    />);
    expect(screen.getByText("Current")).toBeDefined();

    view.rerender(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-c", contentHash: "c".repeat(64) }))}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={vi.fn().mockResolvedValue([fileRelation])}
    />);
    expect(screen.getByText("Stale source")).toBeDefined();
  });

  it("does not offer a duplicate link when only the workspace revision changed", async () => {
    const linkMemoryToFile = vi.fn().mockResolvedValue({ ...fileRelation, source_revision: "revision-b" });
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-b" }))}
      loadMemories={vi.fn().mockResolvedValue([memoryRecord])}
      loadFileRelations={vi.fn().mockResolvedValue([fileRelation])}
      linkMemoryToFile={linkMemoryToFile}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText("This memory version is already linked to this file.")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Save link from 0x2a to src/agent.py" })).toBeNull();
    expect(linkMemoryToFile).not.toHaveBeenCalled();
  });

  it("requires removing a stale relation before linking newer evidence for the same target", async () => {
    const updatedMemory = { ...memoryRecord, revision: 5, content_hash: "c".repeat(64) };
    const linkMemoryToFile = vi.fn();
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([updatedMemory])}
      loadFileRelations={vi.fn().mockResolvedValue([{ ...fileRelation, memory_content_current: false }])}
      linkMemoryToFile={linkMemoryToFile}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText("Stale memory")).toBeDefined();
    expect(await screen.findByText("(metadata-only record)")).toBeDefined();
    expect(screen.queryByText(/remove the saved link above before linking updated evidence/i)).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Save link from 0x2a to src/agent.py" })).toBeNull();
    expect(linkMemoryToFile).not.toHaveBeenCalled();
  });

  it("labels memory content changes and inactive memories without changing memory lifecycle", async () => {
    const staleContentRelation = { ...fileRelation, relation_id: "0x02", memory_content_current: false };
    const inactiveRelation = { ...fileRelation, relation_id: "0x03", memory_id: "0x2b", memory_lifecycle: "FORGOTTEN" };
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={vi.fn().mockResolvedValue([staleContentRelation, inactiveRelation])}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));

    expect(await screen.findByText("Stale memory")).toBeDefined();
    expect(screen.getByText("Memory not active")).toBeDefined();
  });

  it("does not show a saved-link response that arrives after the selected file hash changes", async () => {
    let resolveRelations!: (relations: typeof fileRelation[]) => void;
    const loadFileRelations = vi.fn(() => new Promise<typeof fileRelation[]>((resolve) => {
      resolveRelations = resolve;
    }));
    const view = render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={loadFileRelations}
    />);

    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    await act(async () => { await Promise.resolve(); });
    view.rerender(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot({ revision: "revision-b", contentHash: "c".repeat(64) }))}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={loadFileRelations}
    />);
    await act(async () => resolveRelations([fileRelation]));

    expect(screen.queryByText("0x01")).toBeNull();
    expect(screen.getByText(/saved links were hidden because the source changed/i)).toBeDefined();
  });

  it("unlinks only the selected relation ID", async () => {
    const unlinkFileRelation = vi.fn().mockResolvedValue(true);
    render(<WorkspaceGraphView
      graph={buildWorkspaceGraph(snapshot())}
      loadMemories={vi.fn().mockResolvedValue([])}
      loadFileRelations={vi.fn().mockResolvedValue([fileRelation])}
      unlinkFileRelation={unlinkFileRelation}
    />);
    fireEvent.click(screen.getByRole("button", { name: /agent\.py/ }));
    expect(await screen.findByText("Current")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Remove only relation 0x01" }));

    expect(unlinkFileRelation).toHaveBeenCalledWith({
      relation_id: "0x01",
      project_id: "project:project-a",
      relative_path: "src/agent.py",
      scope_kind: "USER_PRIVATE",
    });
    expect(await screen.findByText(/no saved file-memory links/i)).toBeDefined();
    expect(screen.queryByText("0x2a")).toBeNull();
  });
});

describe("bounded workspace graph and navigation", () => {
  it("reports source snapshot and projection counts separately and never exceeds the 600-file cap", () => {
    const graph = buildWorkspaceGraph(snapshotWithFileCount(605), { maxFiles: 10_000 });

    expect(graph.stats).toMatchObject({
      files: 600,
      snapshot_files: 605,
      source_overflowed: false,
      omitted_files: 5,
    });
    expect(graph.truncated).toBe(true);
    expect(graph.nodes.filter((node) => node.kind === "file")).toHaveLength(600);
  });

  it("marks source overflow independently of files omitted by projection", () => {
    const graph = buildWorkspaceGraph(snapshotWithFileCount(3, true));

    expect(graph.stats).toMatchObject({
      files: 3,
      snapshot_files: 3,
      source_overflowed: true,
      omitted_files: 0,
    });
    expect(graph.truncated).toBe(true);
    render(<WorkspaceGraphView graph={graph} />);
    expect(screen.getByText(
      "The source scan reported overflow. 3 files are present in this snapshot; the full workspace total is unavailable.",
    )).toBeDefined();
  });

  it("keeps workspace-root and a directory literally named root as distinct list groups", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(<WorkspaceGraphView graph={buildWorkspaceGraph(snapshotWithPaths(["root.py", "root/file.py"]))} />);
      fireEvent.click(screen.getByRole("button", { name: "List" }));

      const headings = [...document.querySelectorAll(".graph-list-group h3")];
      expect(headings.map((heading) => heading.textContent)).toEqual(["Workspace root 1 files", "root 1 files"]);
      expect(new Set(headings.map((heading) => heading.id)).size).toBe(2);
      expect(consoleError.mock.calls.flat().join(" ")).not.toContain("Encountered two children with the same key");
    } finally {
      consoleError.mockRestore();
    }
  });

  it("adapts graph columns to constrained and wide canvas widths while zoom remains bounded", async () => {
    const { workspaceGraphLayout } = await import("../src/WorkspaceGraphView");

    expect(workspaceGraphLayout(640, 1)).toMatchObject({ columns: 2, nodeWidth: 218 });
    expect(workspaceGraphLayout(1280, 1)).toMatchObject({ columns: 4, nodeWidth: 218 });
    expect(workspaceGraphLayout(1280, 9)).toMatchObject({ columns: 3, nodeWidth: 327 });
    expect(workspaceGraphLayout(1280, Number.NaN).nodeWidth).toBe(218);
  });

  it("keeps list counts accurate, filters by folder, and exposes graph keyboard pan and fit controls", () => {
    render(<WorkspaceGraphView graph={buildWorkspaceGraph(snapshotWithFileCount(200))} />);

    expect(screen.getByText(/Showing 180 of 200 matching projected files/)).toBeDefined();
    expect(screen.getByText(/graph draws at most 180 matching files/i)).toBeDefined();
    const canvas = screen.getByRole("region", { name: "Project graph canvas" });
    expect(canvas.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(canvas, { key: "ArrowDown" });
    expect((canvas as HTMLDivElement).scrollTop).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByLabelText("Graph zoom").textContent).toBe("125%");
    fireEvent.click(screen.getByRole("button", { name: "Fit width" }));
    expect(screen.getByLabelText("Graph zoom").textContent).toBe("100%");
    expect((canvas as HTMLDivElement).scrollLeft).toBe(0);

    fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "folder:tests" } });
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getByText(/Showing 100 of 100 matching projected files/)).toBeDefined();
    expect(screen.getByRole("heading", { name: /tests 100 files/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /tests\/file-001\.py/i })).toBeDefined();
    expect(screen.queryByRole("button", { name: /src\/file-000\.py/i })).toBeNull();
  });

  it("clusters graph nodes by directory and degree and routes edges with cubic-bezier paths", async () => {
    const { orderWorkspaceGraphNodes, workspaceGraphEdgePath } = await import("../src/workspace_graph");
    const base = snapshotWithPaths(["src/z_leaf.py", "src/a_hub.py", "tests/test_hub.py"]);
    base.files[1].imports = ["src/z_leaf.py"];
    base.files[2].imports = ["src/a_hub.py"];
    const graph = buildWorkspaceGraph(base);
    const files = graph.nodes.filter((node) => node.kind === "file");
    const ordered = orderWorkspaceGraphNodes(files, graph.edges);
    expect(ordered.map((node) => node.path)).toEqual([
      "src/a_hub.py",
      "src/z_leaf.py",
      "tests/test_hub.py",
    ]);
    const horizontalPath = workspaceGraphEdgePath({ x: 100, y: 80 }, { x: 340, y: 120 });
    expect(horizontalPath).toMatch(/^M 100 80 C \d+ 80, \d+ 120, 340 120$/);
    const verticalPath = workspaceGraphEdgePath({ x: 100, y: 80 }, { x: 120, y: 360 });
    expect(verticalPath).toMatch(/^M 100 80 C 100 \d+, 120 \d+, 120 360$/);
  });

  it("resolves Python dot-prefixed relative imports and rejects parent traversals above workspace root", () => {
    const base = snapshotWithPaths([
      "root_mod.py",
      "src/pkg/mod.py",
      "src/pkg/helper.py",
      "src/shared/util.py",
    ]);
    base.files[1].imports = [
      ".helper",
      "..shared.util",
      "../../root_mod",
      "../../../root_mod",
      "....root_mod",
      "sub/../../root_mod.py",
    ];
    const graph = buildWorkspaceGraph(base);
    const importEdges = graph.edges
      .filter((edge) => edge.kind === "import_candidate")
      .map((edge) => `${edge.source} -> ${edge.target}`);
    expect(importEdges).toEqual([
      "file:project-a:src/pkg/mod.py -> file:project-a:src/pkg/helper.py",
      "file:project-a:src/pkg/mod.py -> file:project-a:src/shared/util.py",
      "file:project-a:src/pkg/mod.py -> file:project-a:root_mod.py",
    ]);
    expect(graph.stats.unresolved_candidates).toBe(3);
  });
});

