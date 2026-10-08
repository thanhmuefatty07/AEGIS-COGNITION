import { buildWorkspaceGraph } from "./workspace_graph";
import type { SourceSnapshot } from "./protocol";

self.onmessage = (event: MessageEvent<SourceSnapshot>) => {
  try {
    self.postMessage({ graph: buildWorkspaceGraph(event.data) });
  } catch {
    self.postMessage({ error: "The source map could not be built from this snapshot." });
  }
};
