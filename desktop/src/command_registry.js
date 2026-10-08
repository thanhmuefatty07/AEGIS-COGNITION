export const workbenchCommands = Object.freeze([
  { id: "command-palette", label: "Show Command Palette", keys: { mac: "⌘⇧P", other: "Ctrl+Shift+P" } },
  { id: "quick-open", label: "Quick Open", keys: { mac: "⌘P", other: "Ctrl+P" } },
  { id: "new-task", label: "New Task", keys: { mac: "⌘N", other: "Ctrl+N" } },
  { id: "toggle-navigator", label: "Toggle Navigator", keys: { mac: "⌘B", other: "Ctrl+B" } },
  { id: "find-in-project", label: "Find in Project", keys: { mac: "⌘⇧F", other: "Ctrl+Shift+F" } },
  { id: "send", label: "Send Message", keys: { mac: "⌘Enter", other: "Ctrl+Enter" } },
  { id: "stop-task", label: "Stop Active Task", keys: { mac: "⌘.", other: "Ctrl+." } },
  { id: "preferences", label: "Preferences", keys: { mac: "⌘,", other: "Ctrl+," } },
  { id: "focus-next-pane", label: "Focus Next Pane", keys: { mac: "F6", other: "F6" } },
  { id: "focus-previous-pane", label: "Focus Previous Pane", keys: { mac: "⇧F6", other: "Shift+F6" } },
]);

export function resolveWorkbenchShortcut(event, platform = "") {
  const key = String(event.key ?? "").toLowerCase();
  const mac = /mac|iphone|ipad/i.test(platform);
  const primary = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  if (event.altKey) return null;
  if (key === "f6" && !event.ctrlKey && !event.metaKey && !event.altKey) {
    return event.shiftKey ? "focus-previous-pane" : "focus-next-pane";
  }
  if (!primary) return null;
  if (key === "p" && event.shiftKey) return "command-palette";
  if (key === "p" && !event.shiftKey) return "quick-open";
  if (key === "n" && !event.shiftKey) return "new-task";
  if (key === "b" && !event.shiftKey) return "toggle-navigator";
  if (key === "f" && event.shiftKey) return "find-in-project";
  if (key === "enter" && !event.shiftKey) return "send";
  if (key === "." && !event.shiftKey) return "stop-task";
  if (key === "," && !event.shiftKey) return "preferences";
  return null;
}
