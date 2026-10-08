export type WorkbenchCommand = {
  id: string;
  label: string;
  keys: { mac: string; other: string };
};

export const workbenchCommands: readonly WorkbenchCommand[];
export function resolveWorkbenchShortcut(
  event: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">,
  platform?: string,
): string | null;
