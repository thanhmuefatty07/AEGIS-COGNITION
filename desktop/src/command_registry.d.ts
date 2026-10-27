export type WorkbenchCommand = {
  id: string;
  label: string;
  keys: { mac: string; other: string };
};

export type WorkbenchInspectorCommand = {
  id: "open-files" | "open-changes" | "open-map" | "open-activity" | "open-context";
  label: string;
  tab: "files" | "changes" | "map" | "activity" | "context";
};

export const workbenchCommands: readonly WorkbenchCommand[];
export const workbenchInspectorCommands: readonly WorkbenchInspectorCommand[];
export function resolveWorkbenchShortcut(
  event: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">,
  platform?: string,
): string | null;
