import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  desktopRequest,
  parseConnectionList,
  parseConversationList,
  parseConversationInspection,
  parseConversationRecordResult,
  parseConversationSnapshot,
  parseMemorySearchResult,
  parseModelList,
  parseSourceSnapshot,
  parseSubagentCancelResult,
  parseSubagentEventPage,
  parseSubagentGraph,
  parseSubagentRunResult,
  parseSubagentStartResult,
  parseSubagentStatusResult,
  parseWorkspaceCloneResult,
  parseWorkspaceSnapshot,
  type Conversation,
  type ConversationInspection,
  type ConversationSnapshot,
  type ModelDescriptor,
  type MemoryRecord,
  type SourceSnapshot,
  type SubagentCancelResult,
  type SubagentEvent,
  type SubagentGraph,
  type SubagentRunResult,
  type SubagentStartResult,
  type SubagentStatusResult,
  type WorkspaceSnapshot,
} from "./protocol";
import WorkspaceGraphView from "./WorkspaceGraphView";
import { buildWorkspaceGraph, type WorkspaceGraph } from "./workspace_graph";
import { CapabilityIcon } from "./capability-icons";
import mascotDarkUrl from "./assets/assistant-mascot-dark.gif";
import mascotLightUrl from "./assets/assistant-mascot-light.gif";
import mascotStillDarkUrl from "./assets/assistant-mascot-still-dark.png";
import mascotStillLightUrl from "./assets/assistant-mascot-still-light.png";

type Destination = "chat" | "settings" | "extensions" | "pulls" | "scheduled" | "plugins";
type WorkTab = "files" | "map" | "activity" | "context";
type Theme = "system" | "dark" | "light";
const defaultEndpoint = "http://127.0.0.1:8080/v1";
const SIDEBAR_WIDTH_MIN = 240;
const SIDEBAR_WIDTH_DEFAULT = 275;
const SIDEBAR_WIDTH_MAX = 520;
const SIDEBAR_COLLAPSE_THRESHOLD = 160;
const SIDEBAR_RESIZE_STEP = 16;
const WORK_PANEL_WIDTH_MIN = 244;
const WORK_PANEL_WIDTH_DEFAULT = 360;
const WORK_PANEL_WIDTH_MAX = 10000;
const WORK_PANEL_OVERLAY_BREAKPOINT = 1180;

type DesktopPlatform = "darwin" | "linux" | "win32";
type SessionSort = "recent" | "oldest" | "name" | "created";

function desktopPlatform(): DesktopPlatform {
  const platform = typeof navigator === "undefined" ? "" : navigator.platform.toLowerCase();
  return platform.includes("mac") ? "darwin" : platform.includes("linux") ? "linux" : "win32";
}

function hasNativeWindow() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function shortPath(value: string | null | undefined, length = 32) {
  if (!value) return "—";
  return value.length > length ? `…${value.slice(-length + 1)}` : value;
}

function clampSidebarWidth(value: number, max = SIDEBAR_WIDTH_MAX) {
  const upper = Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, Math.round(max)));
  return Math.round(Math.min(upper, Math.max(SIDEBAR_WIDTH_MIN, value)));
}

function formatTime(value: number) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(value);
}

function displaySessionTitle(title: string) {
  return title === "New thread" ? "New task" : title;
}

const TOPBAR_TITLE_MAX_LENGTH = 10;

function truncateTopbarTitle(title: string) {
  const characters = Array.from(title);
  return characters.length > TOPBAR_TITLE_MAX_LENGTH
    ? `${characters.slice(0, TOPBAR_TITLE_MAX_LENGTH).join("")}…`
    : title;
}

function workspaceName(path: string | null | undefined) {
  if (!path) return null;
  const clean = path.replace(/[\\/]+$/, "");
  const parts = clean.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? clean;
}

type IconName =
  | "plus"
  | "chevron-left"
  | "chevron-right"
  | "chevron-down"
  | "sidebar"
  | "search"
  | "sessions"
  | "folder"
  | "new-project"
  | "branch"
  | "extensions"
  | "settings"
  | "panel"
  | "more"
  | "spark"
  | "attach"
  | "arrow-up"
  | "check"
  | "activity"
  | "map"
  | "files"
  | "circle"
  | "external"
  | "sun"
  | "bell"
  | "plug"
  | "new-chat"
  | "shield"
  | "sliders"
  | "bot"
  | "keyboard"
  | "file"
  | "server"
  | "globe"
  | "download"
  | "info"
  | "refresh"
   | "sort"
   | "clock"
   | "minimize"
  | "maximize"
  | "restore"
  | "close";

function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return <CapabilityIcon name={name} size={size} />;
}

function HomeMascotLogo() {
  return (
    <span className="home-mascot-logo" data-testid="home-mascot-logo" aria-hidden="true">
      <img className="home-mascot-motion home-mascot-dark" src={mascotDarkUrl} alt="" width={100} height={100} draggable={false} />
      <img className="home-mascot-motion home-mascot-light" src={mascotLightUrl} alt="" width={100} height={100} draggable={false} />
      <img className="home-mascot-still home-mascot-dark" src={mascotStillDarkUrl} alt="" width={100} height={100} draggable={false} />
      <img className="home-mascot-still home-mascot-light" src={mascotStillLightUrl} alt="" width={100} height={100} draggable={false} />
    </span>
  );
}

function AegisBrandLogo() {
  return (
    <svg className="brand-logo aegis-brand-logo" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path d="M12 2.5 20 6v5.4c0 4.8-3.2 8.7-8 10.1-4.8-1.4-8-5.3-8-10.1V6l8-3.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="m8.2 15.2 1.5-4.6h4.6l1.5 4.6M9 13h6" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

type ComposerInputProps = {
  value: string;
  placeholder: string;
  disabled: boolean;
  enterToSend: boolean;
  onChange: (value: string) => void;
  onKeyDown?: (event: ReactKeyboardEvent<HTMLDivElement>) => boolean;
  onSubmit: () => void;
};

/** Keep the AEGIS send callback at the composer boundary. */
function ComposerInput({ value, placeholder, disabled, enterToSend, onChange, onKeyDown, onSubmit }: ComposerInputProps) {
  const inputRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = inputRef.current;
    if (element && element.textContent !== value) element.textContent = value;
  }, [value]);

  return (
    <div className="composer-input-wrap">
      <div className="composer-input-stage">
        <div
          ref={inputRef}
          className="composer-input"
          role="textbox"
          aria-multiline="true"
          aria-readonly={disabled}
          aria-busy={disabled}
          aria-placeholder={placeholder}
          contentEditable={!disabled}
          suppressContentEditableWarning
          spellCheck={false}
          autoCorrect="off"
          autoCapitalize="off"
          translate="no"
          onInput={(event) => onChange(event.currentTarget.textContent ?? "")}
          onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => {
            if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
            if (onKeyDown?.(event)) return;
            const submit = event.key === "Enter" && !event.shiftKey && (enterToSend || event.ctrlKey || event.metaKey);
            if (submit) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        {value.length === 0 && <span className="composer-placeholder" aria-hidden="true">{placeholder}</span>}
      </div>
    </div>
  );
}

type ComposerAutocompleteMode = "slash" | "file";

type ComposerSuggestion = {
  mode: ComposerAutocompleteMode;
  value: string;
  label: string;
  detail: string;
  icon: IconName;
  insertText: string;
};

function ComposerAutocomplete({
  mode,
  items,
  highlight,
  onHighlight,
  onAccept,
}: {
  mode: ComposerAutocompleteMode;
  items: ComposerSuggestion[];
  highlight: number;
  onHighlight: (index: number) => void;
  onAccept: (item: ComposerSuggestion) => void;
}) {
  return (
    <div className="composer-autocomplete" role="listbox" aria-label={mode === "file" ? "Files" : "Commands"}>
      <div className="composer-ac-list">
        {items.length > 0 ? items.map((item, index) => (
          <button
            key={`${item.mode}:${item.value}`}
            className={`composer-ac-item${highlight === index ? " kb-active" : ""}`}
            type="button"
            role="option"
            aria-selected={highlight === index}
            onMouseDown={(event) => event.preventDefault()}
            onMouseMove={() => onHighlight(index)}
            onClick={() => onAccept(item)}
          >
            <span className="composer-ac-icon"><Icon name={item.icon} size={14} /></span>
            <span className="composer-ac-name">{item.label}</span>
            <span className="composer-ac-desc">{item.detail}</span>
          </button>
        )) : (
          <div className="composer-model-empty">{mode === "file" ? "No matching files" : "No matching commands"}</div>
        )}
      </div>
      <div className="composer-ac-footer">
        <span>↑↓ navigate · Enter select · Esc close</span>
        <span>{mode === "file" ? "Workspace files" : "AEGIS commands"}</span>
      </div>
    </div>
  );
}

function WindowControls() {
  const platform = desktopPlatform();
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (platform === "darwin") return;
    document.documentElement.dataset.nativeWindow = hasNativeWindow() ? "true" : "false";
    if (!hasNativeWindow()) return;
    const appWindow = getCurrentWindow();
    void appWindow.setDecorations(false).catch(() => undefined);
    void appWindow.isMaximized().then(setMaximized).catch(() => undefined);
  }, [platform]);

  if (platform === "darwin") return null;

  const minimize = () => {
    if (!hasNativeWindow()) return;
    void getCurrentWindow().minimize().catch(() => undefined);
  };
  const toggleMaximize = () => {
    if (!hasNativeWindow()) {
      setMaximized((current) => !current);
      return;
    }
    void getCurrentWindow().toggleMaximize().then(() => getCurrentWindow().isMaximized()).then(setMaximized).catch(() => undefined);
  };
  const close = () => {
    if (!hasNativeWindow()) return;
    void getCurrentWindow().close().catch(() => undefined);
  };

  return (
    <div className="window-controls" aria-label="Window controls">
      <button className="window-control-btn" type="button" aria-label="Minimize" title="Minimize" onClick={minimize}><Icon name="minimize" size={12} /></button>
      <button className="window-control-btn" type="button" aria-label={maximized ? "Restore" : "Maximize"} title={maximized ? "Restore" : "Maximize"} onClick={toggleMaximize}><Icon name={maximized ? "restore" : "maximize"} size={11} /></button>
      <button className="window-control-btn window-control-close" type="button" aria-label="Close" title="Close" onClick={close}><Icon name="close" size={12} /></button>
    </div>
  );
}

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [workspaceGraph, setWorkspaceGraph] = useState<WorkspaceGraph | null>(null);
  const [sourceSnapshot, setSourceSnapshot] = useState<SourceSnapshot | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationSnapshot | null>(null);
  const [conversationInspection, setConversationInspection] = useState<ConversationInspection | null>(null);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [endpoint, setEndpoint] = useState(defaultEndpoint);
  const [modelId, setModelId] = useState("local-model");
  const [connectionSaved, setConnectionSaved] = useState(false);
  const [destination, setDestination] = useState<Destination>("chat");
  const [workTab, setWorkTab] = useState<WorkTab>("files");
  const [workTabMenuOpen, setWorkTabMenuOpen] = useState(false);
  const [workPanelOpen, setWorkPanelOpen] = useState(false);
  const [workPanelWidth, setWorkPanelWidth] = useState(() => {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem("aegis-work-panel-width");
    const parsed = stored ? Number(stored) : NaN;
    return Number.isFinite(parsed) ? Math.max(WORK_PANEL_WIDTH_MIN, Math.min(WORK_PANEL_WIDTH_MAX, Math.round(parsed))) : WORK_PANEL_WIDTH_DEFAULT;
  });
  const [workPanelResizing, setWorkPanelResizing] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem("aegis-sidebar-width");
    const parsed = stored ? Number(stored) : NaN;
    return Number.isFinite(parsed) ? clampSidebarWidth(parsed) : SIDEBAR_WIDTH_DEFAULT;
  });
  const [viewportWidth, setViewportWidth] = useState(() => typeof window === "undefined" ? 1280 : window.innerWidth);
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [sessionSort, setSessionSort] = useState<SessionSort>("recent");
  const [sessionSortOpen, setSessionSortOpen] = useState(false);
  const [sessionMenuOpen, setSessionMenuOpen] = useState<string | null>(null);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [projectCollapsed, setProjectCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("aegis-project-collapsed") === "true";
  });
  const [pullFilter, setPullFilter] = useState<"open" | "draft" | "all">("open");
  const [scheduledTitle, setScheduledTitle] = useState("");
  const [scheduledPrompt, setScheduledPrompt] = useState("");
  const [scheduledCadence, setScheduledCadence] = useState<"manual" | "hourly" | "daily" | "weekly">("manual");
  const [homeActionsOpen, setHomeActionsOpen] = useState(false);
  const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
  const [projectSwitcherQuery, setProjectSwitcherQuery] = useState("");
  const [projectSwitcherView, setProjectSwitcherView] = useState<"list" | "clone">("list");
  const [projectCloneUrl, setProjectCloneUrl] = useState("");
  const [projectSwitcherBusy, setProjectSwitcherBusy] = useState(false);
  const [fileFilter, setFileFilter] = useState("");
  const [settingsSection, setSettingsSection] = useState("General");
  const [settingsQuery, setSettingsQuery] = useState("");
  const [fontSizePreset, setFontSizePreset] = useState<"Tall" | "Grande" | "Venti" | "Trenta">("Grande");
  const [proxyMode, setProxyMode] = useState<"System" | "Direct" | "Custom">("Direct");
  const [permissionMode, setPermissionMode] = useState<"Ask every time" | "Accept edits" | "Auto">("Ask every time");
  const [agentDefaultMode, setAgentDefaultMode] = useState<"Agent" | "Plan" | "Goal">("Agent");
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [thinkingMode, setThinkingMode] = useState<"Detailed" | "Compact">("Detailed");
  const [contextUsage, setContextUsage] = useState<"Remaining" | "Used">("Remaining");
  const [enterToSend, setEnterToSend] = useState(true);
  const [projectSort, setProjectSort] = useState<"Recent" | "Name">("Recent");
  const [projectFilter, setProjectFilter] = useState("");
  const [modelEditorOpen, setModelEditorOpen] = useState(false);
  const [composerMenuOpen, setComposerMenuOpen] = useState<"permission" | "model" | null>(null);
  const [composerModelView, setComposerModelView] = useState<"root" | "model" | "thinking">("root");
  const [composerModelQuery, setComposerModelQuery] = useState("");
  const [composerAutocompleteMode, setComposerAutocompleteMode] = useState<ComposerAutocompleteMode | null>(null);
  const [composerAutocompleteQuery, setComposerAutocompleteQuery] = useState("");
  const [composerAutocompleteHighlight, setComposerAutocompleteHighlight] = useState(0);
  const [availableModels, setAvailableModels] = useState<ModelDescriptor[]>([]);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [capabilityQuery, setCapabilityQuery] = useState("");
  const [instructionsDraft, setInstructionsDraft] = useState("Use the local workspace context, preserve evidence boundaries, and report uncertainty explicitly.");
  const [instructionsNotice, setInstructionsNotice] = useState<string | null>(null);
  const [capabilityNotice, setCapabilityNotice] = useState<string | null>(null);
  const [parallelWorkersEnabled, setParallelWorkersEnabled] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem("aegis-theme");
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
  });
  const [reducedMotion, setReducedMotion] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [extensionView, setExtensionView] = useState<"installed" | "marketplace">("installed");
  const [extensionQuery, setExtensionQuery] = useState("");
  const [extensionMenuOpen, setExtensionMenuOpen] = useState(false);
  const [extensionNotice, setExtensionNotice] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subagentBusy, setSubagentBusy] = useState(false);
  const [subagentResult, setSubagentResult] = useState<SubagentRunResult | null>(null);
  const [subagentRunId, setSubagentRunId] = useState<string | null>(null);
  const [subagentStatus, setSubagentStatus] = useState<SubagentStatusResult | null>(null);
  const [subagentEvents, setSubagentEvents] = useState<SubagentEvent[]>([]);
  const [subagentGraph, setSubagentGraph] = useState<SubagentGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const globalSearchRef = useRef<HTMLInputElement>(null);
  const projectSwitcherInputRef = useRef<HTMLInputElement>(null);
  const bootstrapStarted = useRef(false);
  const sidebarResizeRef = useRef<{ pointerId: number; startX: number; startWidth: number; currentWidth: number; handle: HTMLDivElement } | null>(null);
  const workPanelResizeRef = useRef<{ pointerId: number; startX: number; startWidth: number; currentWidth: number; handle: HTMLDivElement } | null>(null);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    void bootstrap();
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const applyTheme = () => {
      document.documentElement.dataset.theme = theme === "system" ? (media.matches ? "light" : "dark") : theme;
    };
    applyTheme();
    window.localStorage.setItem("aegis-theme", theme);
    if (theme !== "system") return;
    media.addEventListener("change", applyTheme);
    return () => media.removeEventListener("change", applyTheme);
  }, [theme]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSearchOpen(false);
        setSessionSortOpen(false);
        setSessionMenuOpen(null);
        setProjectMenuOpen(false);
        setExtensionMenuOpen(false);
        setProjectSwitcherOpen(false);
        setWorkTabMenuOpen(false);
        setComposerMenuOpen(null);
        setComposerModelView("root");
        setComposerModelQuery("");
        setComposerAutocompleteMode(null);
        setComposerAutocompleteQuery("");
        searchRef.current?.blur();
        globalSearchRef.current?.blur();
        return;
      }
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === "k") {
        event.preventDefault();
        setSearchOpen(true);
        window.requestAnimationFrame(() => globalSearchRef.current?.focus());
      } else if (key === "b") {
        event.preventDefault();
        setSidebarCollapsed((current) => !current);
      } else if (key === "j") {
        event.preventDefault();
        setDestination("chat");
        setWorkPanelOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, []);

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    window.localStorage.setItem("aegis-sidebar-width", String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    window.localStorage.setItem("aegis-work-panel-width", String(workPanelWidth));
  }, [workPanelWidth]);

  useEffect(() => {
    window.localStorage.setItem("aegis-project-collapsed", String(projectCollapsed));
  }, [projectCollapsed]);

  useEffect(() => {
    if (sidebarResizing) document.documentElement.dataset.sidebarResizing = "true";
    else delete document.documentElement.dataset.sidebarResizing;
    return () => {
      delete document.documentElement.dataset.sidebarResizing;
    };
  }, [sidebarResizing]);

  useEffect(() => {
    if (workPanelResizing) document.documentElement.dataset.workPanelResizing = "true";
    else delete document.documentElement.dataset.workPanelResizing;
    return () => {
      delete document.documentElement.dataset.workPanelResizing;
    };
  }, [workPanelResizing]);

  useEffect(() => {
    setCapabilityNotice(null);
    setInstructionsNotice(null);
  }, [settingsSection]);

  useEffect(() => {
    if (!projectSwitcherOpen) return;
    projectSwitcherInputRef.current?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".home-project-switcher")) setProjectSwitcherOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [projectSwitcherOpen, projectSwitcherView]);

  useEffect(() => {
    if (!workTabMenuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".work-tab-active-wrap")) setWorkTabMenuOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [workTabMenuOpen]);

  useEffect(() => {
    if (!composerMenuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".composer-menu-anchor")) setComposerMenuOpen(null);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [composerMenuOpen]);

  useEffect(() => {
    if (!projectMenuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".project-menu-anchor")) setProjectMenuOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [projectMenuOpen]);

  useEffect(() => {
    if (!workPanelOpen) setWorkTabMenuOpen(false);
  }, [workPanelOpen]);

  useEffect(() => {
    // Destination pages share the document scroll container. Reset it when
    // switching routes so a long Extensions/Marketplace page cannot leave
    // Settings or Pull Requests vertically clipped on entry.
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }, [destination]);

  useEffect(() => {
    const platform = navigator.platform.toLowerCase().includes("mac")
      ? "darwin"
      : navigator.platform.toLowerCase().includes("linux")
        ? "linux"
        : "win32";
    document.documentElement.dataset.platform = platform;
  }, []);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    const runId = subagentRunId;
    if (!runId) return;
    let stopped = false;
    let cursor = 0;
    let timer: number | undefined;

    async function pollSubagent() {
      try {
        const page = await desktopRequest("subagents.events", {
          run_id: runId,
          cursor,
          max_messages: 64,
        }, parseSubagentEventPage);
        if (stopped) return;
        cursor = page.latest_cursor;
        setSubagentEvents((current) => {
          const merged = new Map<number, SubagentEvent>(page.resync_required ? [] : current.map((event) => [event.cursor, event]));
          page.events.forEach((event) => merged.set(event.cursor, event));
          return [...merged.values()].sort((left, right) => left.cursor - right.cursor).slice(-128);
        });
        const status = await desktopRequest("subagents.status", { run_id: runId }, parseSubagentStatusResult);
        if (stopped) return;
        setSubagentStatus(status);
        const graph = await desktopRequest("subagents.graph", { run_id: runId }, parseSubagentGraph);
        if (stopped) return;
        setSubagentGraph(graph);
        if (status.result !== null || status.status !== "RUNNING") {
          setSubagentResult(status.result);
          setSubagentBusy(false);
          setSubagentRunId(null);
          return;
        }
        timer = window.setTimeout(() => void pollSubagent(), 250);
      } catch (reason) {
        if (stopped) return;
        setError(reason instanceof Error ? reason.message : "The parallel run status could not be read");
        setSubagentBusy(false);
        setSubagentRunId(null);
      }
    }

    void pollSubagent();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [subagentRunId]);

  async function bootstrap() {
    try {
      setError(null);
      const snapshot = await desktopRequest("workspace.open", {}, parseWorkspaceSnapshot);
      setWorkspace(snapshot);
      await loadWorkspaceGraph();
      await configureConnection();
      const records = await loadConversations();
      if (records.length > 0) await selectConversation(records[0].conversation_id);
      else await createConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to open the local workspace");
    }
  }

  async function activateWorkspace(workspacePath: string) {
    const snapshot = await desktopRequest("workspace.switch", { workspace_path: workspacePath }, parseWorkspaceSnapshot);
    setWorkspace(snapshot);
    setSourceSnapshot(null);
    setWorkspaceGraph(null);
    setConversation(null);
    setConversationInspection(null);
    setActiveConversationId(null);
    setConversations([]);
    setSelectedFile(null);
    setConnectionSaved(false);
    setProjectCollapsed(false);
    await loadWorkspaceGraph();
    await configureConnection();
    const records = await loadConversations();
    if (records.length > 0) await selectConversation(records[0].conversation_id);
    else await createConversation();
  }

  async function switchWorkspace(workspacePath: string) {
    if (projectSwitcherBusy || !workspacePath.trim()) return;
    setProjectSwitcherBusy(true);
    setError(null);
    try {
      await activateWorkspace(workspacePath.trim());
      setProjectSwitcherOpen(false);
      setProjectSwitcherView("list");
      setProjectCloneUrl("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to switch the workspace");
    } finally {
      setProjectSwitcherBusy(false);
    }
  }

  async function openWorkspacePicker() {
    if (projectSwitcherBusy) return;
    if (!hasNativeWindow()) {
      setError("Open project is available in the packaged desktop app.");
      return;
    }
    setProjectSwitcherBusy(true);
    setError(null);
    try {
      const workspacePath = await invoke<string | null>("pick_workspace");
      if (workspacePath) await activateWorkspace(workspacePath);
      setProjectSwitcherOpen(false);
      setProjectSwitcherView("list");
      setProjectCloneUrl("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to open the selected project");
    } finally {
      setProjectSwitcherBusy(false);
    }
  }

  async function cloneWorkspace() {
    const cloneUrl = projectCloneUrl.trim();
    if (projectSwitcherBusy || !cloneUrl) return;
    setProjectSwitcherBusy(true);
    setError(null);
    try {
      const result = await desktopRequest("workspace.clone", { clone_url: cloneUrl }, parseWorkspaceCloneResult);
      await activateWorkspace(result.workspace_path);
      setProjectSwitcherOpen(false);
      setProjectSwitcherView("list");
      setProjectCloneUrl("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to clone the selected project");
    } finally {
      setProjectSwitcherBusy(false);
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
    const connections = await desktopRequest("connections.list", {}, parseConnectionList);
    const existingConnection = connections.find((item) => item.connection_id === "local");
    const payload: Record<string, unknown> = {
      connection_id: "local",
      provider_kind: "openai-compatible",
      endpoint,
      protocol: "chat-completions",
    };
    if (existingConnection) {
      payload.expected_revision = existingConnection.revision;
      const models = await desktopRequest("models.list", { connection_id: "local" }, parseModelList);
      if (!models.some((item) => item.model_id === modelId)) payload.model_id = modelId;
    } else {
      payload.model_id = modelId;
    }
    await desktopRequest("connections.save", payload, (value) => value);
    setConnectionSaved(true);
  }

  async function openComposerModelMenu() {
    const opening = composerMenuOpen !== "model";
    setComposerModelView("root");
    setComposerModelQuery("");
    setComposerMenuOpen(opening ? "model" : null);
    if (!opening) return;
    try {
      setAvailableModels(await desktopRequest("models.list", { connection_id: "local" }, parseModelList));
    } catch (reason) {
      setAvailableModels([]);
      setError(reason instanceof Error ? reason.message : "The model catalog could not be read");
    }
  }

  async function switchConversationModel(model: ModelDescriptor) {
    if (!conversation || busy || subagentBusy) return;
    try {
      setError(null);
      const record = await desktopRequest("conversations.switch_model", {
        conversation_id: conversation.conversation.conversation_id,
        connection_id: model.connection_id,
        model_id: model.model_id,
        expected_revision: conversation.conversation.revision,
      }, parseConversationRecordResult);
      setConversation((current) => current ? { ...current, conversation: record } : current);
      setConversations((current) => current.map((item) => item.conversation_id === record.conversation_id ? record : item));
      setModelId(record.model_id);
      setConnectionSaved(true);
      setComposerMenuOpen(null);
      setComposerModelView("root");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The model could not be selected");
    }
  }

  async function loadConversations(): Promise<Conversation[]> {
    const records = await desktopRequest("conversations.list", {}, parseConversationList);
    const sorted = [...records].sort((left, right) => right.updated_at_ms - left.updated_at_ms);
    setConversations(sorted);
    return sorted;
  }

  async function createConversation() {
    const id = `desktop-${crypto.randomUUID()}`;
    const record = await desktopRequest("conversations.create", {
      conversation_id: id,
      title: "New thread",
      connection_id: "local",
      model_id: modelId,
    }, parseConversationRecordResult);
    setConversations((current) => [record, ...current.filter((item) => item.conversation_id !== id)]);
    await selectConversation(id);
    setDestination("chat");
  }

  async function selectConversation(id: string) {
    setDestination("chat");
    setSearchOpen(false);
    setActiveConversationId(id);
    setConversationInspection(null);
    setSubagentResult(null);
    setSubagentRunId(null);
    setSubagentStatus(null);
    setSubagentEvents([]);
    setSubagentGraph(null);
    await loadConversation(id);
  }

  async function loadConversation(id: string) {
    try {
      const [snapshot, inspection] = await Promise.all([
        desktopRequest("conversations.read", { conversation_id: id }, parseConversationSnapshot),
        desktopRequest("conversations.inspect", { conversation_id: id }, parseConversationInspection),
      ]);
      setConversation(snapshot);
      setConversationInspection(inspection);
      if (snapshot.conversation.model_id !== modelId) setConnectionSaved(false);
      setModelId(snapshot.conversation.model_id);
    } catch (reason) {
      setConversation(null);
      if (reason instanceof Error && !reason.message.includes("conversation not found")) setError(reason.message);
    }
  }

  async function sendMessage() {
    const text = message.trim();
    if (!text || busy || !activeConversationId) return;
    setBusy(true);
    try {
      setError(null);
      const snapshot = await desktopRequest("conversations.send", {
        conversation_id: activeConversationId,
        message: text,
        mode,
      }, parseConversationSnapshot);
      setConversation(snapshot);
      setMessage("");
      await loadConversations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation failed");
    } finally {
      setBusy(false);
    }
  }

  async function loadMemoriesForFile(node: { path: string | null }): Promise<MemoryRecord[]> {
    if (!node.path) return [];
    return desktopRequest("memory.search", { query: node.path, top_k: 6, scope_kind: "USER_PRIVATE" }, parseMemorySearchResult);
  }

  async function runSubagents() {
    const task = message.trim() || "Inspect this workspace in parallel and summarize the relevant evidence.";
    if (subagentBusy || !activeConversationId || !runtimeReady || !connectionSaved) return;
    setSubagentBusy(true);
    let started = false;
    try {
      setError(null);
      const result = await desktopRequest("subagents.start", {
        task,
        conversation_id: activeConversationId,
        max_concurrency: 4,
      }, parseSubagentStartResult);
      setSubagentResult(null);
      setSubagentStatus({
        schema: "aegis-desktop-subagents-status-v1",
        run_id: result.run_id,
        status: result.status,
        event_cursor: result.event_cursor,
        started_at_ms: Date.now(),
        finished_at_ms: null,
        cancel_requested_at_ms: null,
        cancel_supported: true,
        thread_alive: true,
        result: null,
        error: null,
      });
      setSubagentEvents([]);
      setSubagentRunId(result.run_id);
      setMessage("");
      started = true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The parallel run failed");
    } finally {
      if (!started) setSubagentBusy(false);
    }
  }

  async function cancelSubagents() {
    if (!subagentRunId) return;
    try {
      const result = await desktopRequest<SubagentCancelResult>(
        "subagents.cancel",
        { run_id: subagentRunId },
        parseSubagentCancelResult,
      );
      setSubagentStatus((current) => current ? { ...current, status: result.status, event_cursor: result.event_cursor } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The parallel run could not be cancelled");
    }
  }

  function closeComposerAutocomplete() {
    setComposerAutocompleteMode(null);
    setComposerAutocompleteQuery("");
    setComposerAutocompleteHighlight(0);
  }

  function handleComposerChange(value: string) {
    setMessage(value);
    const trigger = /(^|\s)([\/@])([^\s]*)$/.exec(value);
    if (!trigger) {
      closeComposerAutocomplete();
      return;
    }
    setComposerAutocompleteMode(trigger[2] === "/" ? "slash" : "file");
    setComposerAutocompleteQuery(trigger[3] ?? "");
    setComposerAutocompleteHighlight(0);
  }

  function acceptComposerSuggestion(item: ComposerSuggestion) {
    const trigger = /(^|\s)([\/@])([^\s]*)$/.exec(message);
    const start = trigger?.index === undefined ? message.length : trigger.index + trigger[1].length;
    setMessage(`${message.slice(0, start)}${item.insertText}`);
    closeComposerAutocomplete();
  }

  function handleComposerKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!composerAutocompleteMode) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      closeComposerAutocomplete();
      return true;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (composerSuggestions.length === 0) return false;
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setComposerAutocompleteHighlight((current) => (current + delta + composerSuggestions.length) % composerSuggestions.length);
      return true;
    }
    if ((event.key === "Enter" || event.key === "Tab") && !event.shiftKey && composerSuggestions.length > 0) {
      event.preventDefault();
      acceptComposerSuggestion(composerSuggestions[composerAutocompleteHighlight] ?? composerSuggestions[0]);
      return true;
    }
    return false;
  }

  const previewMode = workspace?.preview_mode === true;
  const runtimeReady = previewMode || workspace?.native_runtime_available === true;
  const activeTitle = conversation?.conversation.title ?? "New thread";
  const displayActiveTitle = activeTitle === "New thread" ? "New task" : activeTitle;
  const topbarTitle = truncateTopbarTitle(displayActiveTitle);
  const activeConversation = conversations.find((item) => item.conversation_id === activeConversationId);
  const sidebarConversations = useMemo(() => conversations.filter((item) => {
    // Keep the blank composer session as the active surface, not as a
    // misleading historical row in the Sessions list.
    return !(item.conversation_id === activeConversationId && !(conversation?.turns.length ?? 0));
  }).sort((left, right) => {
    if (sessionSort === "name") return left.title.localeCompare(right.title);
    if (sessionSort === "oldest") return left.created_at_ms - right.created_at_ms;
    if (sessionSort === "created") return right.created_at_ms - left.created_at_ms;
    return right.updated_at_ms - left.updated_at_ms;
  }), [activeConversationId, conversation?.turns.length, conversations, sessionSort]);
  const filteredFiles = useMemo(() => {
    const query = fileFilter.trim().toLowerCase();
    return (sourceSnapshot?.files ?? [])
      .filter((file) => !query || file.relative_path.toLowerCase().includes(query))
      .sort((left, right) => left.relative_path.localeCompare(right.relative_path))
      .slice(0, 120);
  }, [fileFilter, sourceSnapshot]);
  const composerSuggestions = useMemo<ComposerSuggestion[]>(() => {
    const query = composerAutocompleteQuery.trim().toLowerCase();
    if (composerAutocompleteMode === "slash") {
      const commands: ComposerSuggestion[] = [
        { mode: "slash", value: "summarize", label: "/summarize", detail: "Summarize this workspace", icon: "spark", insertText: "Summarize this workspace" },
        { mode: "slash", value: "inspect", label: "/inspect", detail: "Inspect the current project", icon: "search", insertText: "Inspect the current project" },
        { mode: "slash", value: "workers", label: "/workers", detail: "Run parallel workers", icon: "extensions", insertText: "Run parallel workers" },
        { mode: "slash", value: "files", label: "/files", detail: "Open project files", icon: "files", insertText: "Inspect the project files" },
      ];
      return commands.filter((item) => !query || `${item.value} ${item.detail}`.toLowerCase().includes(query));
    }
    if (composerAutocompleteMode === "file") {
      return (sourceSnapshot?.files ?? [])
        .filter((file) => !query || file.relative_path.toLowerCase().includes(query))
        .sort((left, right) => left.relative_path.localeCompare(right.relative_path))
        .slice(0, 24)
        .map((file) => ({
          mode: "file" as const,
          value: file.relative_path,
          label: `@${file.relative_path}`,
          detail: file.language || "workspace file",
          icon: "file" as const,
          insertText: `@${file.relative_path} `,
        }));
    }
    return [];
  }, [composerAutocompleteMode, composerAutocompleteQuery, sourceSnapshot]);
  const activityItems = useMemo(() => {
    const items: Array<{ icon: IconName; title: string; detail: string; timestamp: number }> = [
      {
        icon: "check",
        title: "Workspace opened",
        detail: "Rust host admitted the local session",
        timestamp: workspace ? (sourceSnapshot?.created_at_ms ?? 1) : 0,
      },
      {
        icon: "map",
        title: "Source map ready",
        detail: `${workspaceGraph?.stats.files ?? 0} files available to inspect`,
        timestamp: sourceSnapshot?.created_at_ms ?? 0,
      },
    ];
    if (conversation) {
      for (const execution of conversation.executions) {
        items.push({
          icon: execution.status === "COMPLETED" ? "check" : "activity",
          title: `Provider execution ${execution.status.toLowerCase()}`,
          detail: `${execution.model_id} · checkpoint ${execution.checkpoint_seq}`,
          timestamp: execution.finished_at_ms ?? execution.started_at_ms,
        });
      }
      for (const checkpoint of conversation.checkpoints) {
        items.push({
          icon: "activity",
          title: `Checkpoint ${checkpoint.state.toLowerCase()}`,
          detail: `Sequence ${checkpoint.sequence} · execution ${checkpoint.execution_id.slice(-12)}`,
          timestamp: checkpoint.created_at_ms,
        });
      }
      for (const toolCall of conversation.tool_calls) {
        items.push({
          icon: toolCall.status === "COMPLETED" ? "check" : "activity",
          title: `Tool ${toolCall.status.toLowerCase()}`,
          detail: `${toolCall.tool_name} · ${toolCall.call_id.slice(-12)}`,
          timestamp: toolCall.completed_at_ms ?? toolCall.created_at_ms,
        });
      }
    }
    return items
      .filter((item) => item.timestamp > 0)
      .sort((left, right) => right.timestamp - left.timestamp)
      .slice(0, 8);
  }, [conversation, sourceSnapshot, workspace, workspaceGraph]);
  const sidebarWidthMax = useMemo(() => {
    // Below the compact breakpoint the panel is a fixed overlay, so it
    // does not consume grid width and must not falsely clamp the sidebar to
    // its minimum while the panel is open.
    const panelOverlays = viewportWidth <= WORK_PANEL_OVERLAY_BREAKPOINT;
    const panelWidth = destination === "chat" && workPanelOpen && !panelOverlays ? workPanelWidth : 0;
    return Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, viewportWidth - 450 - panelWidth - 1));
  }, [destination, viewportWidth, workPanelOpen, workPanelWidth]);

  const workPanelWidthMax = useMemo(() => {
    const leftWidth = sidebarCollapsed ? 0 : sidebarWidth;
    const compactViewport = viewportWidth < leftWidth + 450 + WORK_PANEL_WIDTH_MIN;
    const available = compactViewport ? viewportWidth - leftWidth : viewportWidth - leftWidth - 450;
    return Math.max(WORK_PANEL_WIDTH_MIN, Math.min(WORK_PANEL_WIDTH_MAX, available));
  }, [sidebarCollapsed, sidebarWidth, viewportWidth]);

  function openDestination(next: Destination) {
    setDestination(next);
    setSearchOpen(false);
    setSessionSortOpen(false);
    setSessionMenuOpen(null);
    setExtensionMenuOpen(false);
    setCapabilityNotice(null);
    setInstructionsNotice(null);
    setExtensionNotice(null);
    if (next !== "chat") setWorkTab("files");
  }

  function chooseSearchResult(kind: "conversation" | "file" | "destination" | "setting", value: string) {
    setSearchOpen(false);
    setSearchQuery("");
    if (kind === "conversation") {
      setDestination("chat");
      void selectConversation(value);
      return;
    }
    if (kind === "file") {
      setDestination("chat");
      setWorkTab("files");
      setWorkPanelOpen(true);
      setFileFilter(value);
      return;
    }
    if (kind === "setting") {
      setSettingsSection(value);
      setDestination("settings");
      return;
    }
    openDestination(value as Destination);
  }

  function finishSidebarResize(cancelled: boolean) {
    const state = sidebarResizeRef.current;
    if (!state) return;
    if (cancelled) setSidebarWidth(state.startWidth);
    else setSidebarWidth(state.currentWidth);
    if (state.handle.hasPointerCapture(state.pointerId)) state.handle.releasePointerCapture(state.pointerId);
    sidebarResizeRef.current = null;
    setSidebarResizing(false);
  }

  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || sidebarResizeRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true });
    const startWidth = clampSidebarWidth(sidebarWidth, sidebarWidthMax);
    sidebarResizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth,
      currentWidth: startWidth,
      handle: event.currentTarget,
    };
    setSidebarResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    const state = sidebarResizeRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    const rawWidth = state.startWidth + event.clientX - state.startX;
    if (rawWidth < SIDEBAR_COLLAPSE_THRESHOLD) {
      finishSidebarResize(true);
      setSidebarCollapsed(true);
      return;
    }
    state.currentWidth = clampSidebarWidth(rawWidth, sidebarWidthMax);
    setSidebarWidth(state.currentWidth);
  }

  function endSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (sidebarResizeRef.current?.pointerId === event.pointerId) finishSidebarResize(false);
  }

  function resizeSidebarWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const current = clampSidebarWidth(sidebarWidth, sidebarWidthMax);
    const next = event.key === "Home"
      ? SIDEBAR_WIDTH_MIN
      : event.key === "End"
        ? sidebarWidthMax
        : clampSidebarWidth(current + (event.key === "ArrowRight" ? SIDEBAR_RESIZE_STEP : -SIDEBAR_RESIZE_STEP), sidebarWidthMax);
    setSidebarWidth(next);
  }

  function finishWorkPanelResize(cancelled: boolean) {
    const state = workPanelResizeRef.current;
    if (!state) return;
    if (cancelled) setWorkPanelWidth(state.startWidth);
    else setWorkPanelWidth(state.currentWidth);
    if (state.handle.hasPointerCapture(state.pointerId)) state.handle.releasePointerCapture(state.pointerId);
    workPanelResizeRef.current = null;
    setWorkPanelResizing(false);
  }

  function startWorkPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || workPanelResizeRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true });
    const startWidth = Math.max(WORK_PANEL_WIDTH_MIN, Math.min(workPanelWidthMax, workPanelWidth));
    workPanelResizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth,
      currentWidth: startWidth,
      handle: event.currentTarget,
    };
    setWorkPanelResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveWorkPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    const state = workPanelResizeRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    // The separator lives on the panel's left edge: dragging left widens it.
    state.currentWidth = Math.max(WORK_PANEL_WIDTH_MIN, Math.min(workPanelWidthMax, Math.round(state.startWidth - (event.clientX - state.startX))));
    setWorkPanelWidth(state.currentWidth);
  }

  function endWorkPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (workPanelResizeRef.current?.pointerId === event.pointerId) finishWorkPanelResize(false);
  }

  function resizeWorkPanelWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const current = Math.max(WORK_PANEL_WIDTH_MIN, Math.min(workPanelWidthMax, workPanelWidth));
    const next = event.key === "Home"
      ? WORK_PANEL_WIDTH_MIN
      : event.key === "End"
        ? workPanelWidthMax
        : Math.max(WORK_PANEL_WIDTH_MIN, Math.min(workPanelWidthMax, current + (event.key === "ArrowLeft" ? SIDEBAR_RESIZE_STEP : -SIDEBAR_RESIZE_STEP)));
    setWorkPanelWidth(next);
  }

  function renderSearchOverlay() {
    if (!searchOpen) return null;
    const query = searchQuery.trim().toLocaleLowerCase();
    const matches = (value: string) => !query || value.toLocaleLowerCase().includes(query);
    const sessionResults = conversations.filter((item) => matches(item.title)).slice(0, 6);
    const fileResults = (sourceSnapshot?.files ?? []).filter((item) => matches(item.relative_path)).slice(0, 8);
    const destinations: Array<[string, string, IconName]> = [
      ["Settings", "settings", "settings"],
      ["Extensions", "extensions", "plug"],
      ["Pull requests", "pulls", "branch"],
      ["Scheduled", "scheduled", "activity"],
      ["Plugins", "plugins", "extensions"],
    ];
    const settings: Array<[string, string]> = [
      ["General", "General appearance and network"],
      ["AI", "Agent defaults and permissions"],
      ["Shortcuts", "Keyboard shortcuts"],
      ["Instructions", "Global agent instructions"],
      ["Models", "Model configuration"],
      ["Skills", "Local skills"],
      ["MCP", "MCP servers"],
      ["Subagents", "Parallel workers"],
    ];
    const pageResults = destinations.filter(([label]) => matches(label));
    const settingResults = settings.filter(([label, description]) => matches(`${label} ${description}`));
    const hasResults = sessionResults.length > 0 || fileResults.length > 0 || pageResults.length > 0 || settingResults.length > 0;
    return (
      <div className="search-overlay" onClick={() => setSearchOpen(false)}>
        <div className="search-dialog" role="dialog" aria-modal="true" aria-label="Search" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            setSearchOpen(false);
            return;
          }
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          const items = Array.from(document.querySelectorAll<HTMLButtonElement>("#aegis-search-results .search-item"));
          if (!items.length) return;
          const focusedIndex = items.indexOf(document.activeElement as HTMLButtonElement);
          const activeIndex = items.findIndex((item) => item.classList.contains("active"));
          const currentIndex = focusedIndex >= 0 ? focusedIndex : Math.max(activeIndex, 0);
          const nextIndex = event.key === "ArrowDown"
            ? (currentIndex + 1) % items.length
            : (currentIndex - 1 + items.length) % items.length;
          items.forEach((item, index) => item.classList.toggle("active", index === nextIndex));
          items[nextIndex]?.focus();
        }}>
          <div className="search-input-row"><Icon name="search" size={16} /><input ref={globalSearchRef} className="search-input" role="combobox" aria-expanded="true" aria-controls="aegis-search-results" aria-label="Search" placeholder="Search sessions, files, pages, and settings…" value={searchQuery} maxLength={500} autoFocus spellCheck={false} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setSearchOpen(false); } else if (event.key === "Enter") { event.preventDefault(); document.querySelector<HTMLButtonElement>("#aegis-search-results .search-item.active")?.click(); } }} /></div>
          <div id="aegis-search-results" className="search-results" role="listbox" aria-label="Search results">
            <button className="search-item active" type="button" role="option" onClick={() => { setSearchOpen(false); void createConversation(); }}><Icon name="new-chat" size={15} /><span className="search-item-title">New task</span></button>
            {sessionResults.length > 0 && <><div className="search-group-label">Sessions</div>{sessionResults.map((item) => <button className="search-item" type="button" role="option" key={item.conversation_id} onClick={() => chooseSearchResult("conversation", item.conversation_id)}><Icon name="new-chat" size={15} /><span className="search-item-title">{displaySessionTitle(item.title)}</span><span className="search-item-meta">{formatTime(item.updated_at_ms)}</span></button>)}</>}
            {fileResults.length > 0 && <><div className="search-group-label">Files</div>{fileResults.map((item) => <button className="search-item" type="button" role="option" key={item.relative_path} onClick={() => chooseSearchResult("file", item.relative_path)}><Icon name="file" size={15} /><span className="search-item-title">{item.relative_path}</span><span className="search-item-meta">{item.language || "file"}</span></button>)}</>}
            {pageResults.length > 0 && <><div className="search-group-label">Pages</div>{pageResults.map(([label, value, icon]) => <button className="search-item" type="button" role="option" key={value} onClick={() => chooseSearchResult("destination", value)}><Icon name={icon} size={15} /><span className="search-item-title">{label}</span></button>)}</>}
            {settingResults.length > 0 && <><div className="search-group-label">Settings</div>{settingResults.map(([label, description]) => <button className="search-item" type="button" role="option" key={label} onClick={() => chooseSearchResult("setting", label)}><Icon name="settings" size={15} /><span className="search-item-title">{label}</span><span className="search-item-meta">{description}</span></button>)}</>}
            {!hasResults && query && <div className="search-empty">No matching results</div>}
          </div>
        </div>
      </div>
    );
  }

  function renderProjectSwitcherMenu(menuClassName = "home-project-switcher-menu") {
    const currentProjectName = workspaceName(workspace?.workspace_path);
    const projectMatches = !projectSwitcherQuery.trim()
      || currentProjectName?.toLocaleLowerCase().includes(projectSwitcherQuery.trim().toLocaleLowerCase());
    return (
      <div className={`${menuClassName} is-open`} role="menu" aria-label="Switch project" onClick={(event) => event.stopPropagation()}>
        {projectSwitcherView === "clone" ? <div className="home-project-switcher-clone">
          <button className="home-project-switcher-item" type="button" role="menuitem" disabled={projectSwitcherBusy} onClick={() => setProjectSwitcherView("list")}>
            <Icon name="chevron-left" size={14} />
            <span className="home-project-switcher-item-name">Clone project</span>
          </button>
          <label className="home-project-switcher-search">
            <Icon name="branch" size={13} />
            <span className="sr-only">Repository URL</span>
            <input
              ref={projectSwitcherInputRef}
              value={projectCloneUrl}
              onChange={(event) => setProjectCloneUrl(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void cloneWorkspace(); } }}
              placeholder="https://github.com/owner/repository"
              aria-label="Repository URL"
              spellCheck={false}
              autoComplete="off"
              disabled={projectSwitcherBusy}
            />
          </label>
          <p className="home-project-switcher-clone-hint">Clone into the local AEGIS projects directory.</p>
          <button className="home-project-switcher-clone-submit" type="button" disabled={projectSwitcherBusy || !projectCloneUrl.trim()} onClick={() => void cloneWorkspace()}>
            {projectSwitcherBusy ? "Cloning…" : "Clone"}
          </button>
        </div> : <>
          <label className="home-project-switcher-search">
            <Icon name="search" size={13} />
            <input
              ref={projectSwitcherInputRef}
              value={projectSwitcherQuery}
              onChange={(event) => setProjectSwitcherQuery(event.target.value)}
              placeholder="Search projects"
              aria-label="Search projects"
              spellCheck={false}
              disabled={projectSwitcherBusy}
            />
          </label>
          <div className="home-project-switcher-list">
            {projectMatches ? <button className="home-project-switcher-item is-current is-active" type="button" role="menuitemradio" aria-checked="true" disabled={projectSwitcherBusy} onClick={() => setProjectSwitcherOpen(false)}>
              <Icon name="folder" size={14} />
              <span className="home-project-switcher-item-name">{currentProjectName ?? "Workspace"}</span>
              <Icon name="check" size={14} />
            </button> : <div className="home-project-switcher-empty">No matching projects</div>}
          </div>
          <div className="home-project-switcher-divider" />
          <button className="home-project-switcher-item" type="button" role="menuitem" disabled={projectSwitcherBusy} onClick={() => { setProjectCloneUrl(""); setProjectSwitcherView("clone"); }}>
            <Icon name="branch" size={14} />
            <span className="home-project-switcher-item-name">Clone project</span>
          </button>
          <button className="home-project-switcher-item" type="button" role="menuitem" disabled={projectSwitcherBusy} onClick={() => void openWorkspacePicker()}>
            <Icon name="new-project" size={14} />
            <span className="home-project-switcher-item-name">Open project</span>
          </button>
        </>}
      </div>
    );
  }

  function renderSidebar() {
    return (
      <aside className={`sidebar sidebar-surface ${sidebarCollapsed ? "collapsed" : ""}`} aria-label="Workspace navigation">
        <div className="sidebar-header sidebar-brand" data-tauri-drag-region="true">
          <button className="brand no-drag" type="button" onClick={() => openDestination("chat")} aria-label="Home" title="Home">
            <AegisBrandLogo />
            {!sidebarCollapsed && <span>AEGIS</span>}
          </button>
          <div className="sidebar-header-actions no-drag">
            {sidebarCollapsed && <button className="new-task compact-new-task" type="button" onClick={() => void createConversation()} disabled={!workspace || busy} aria-label="New task" title="New task"><Icon name="plus" size={15} /></button>}
            <button className="sidebar-toggle" type="button" onClick={() => setSidebarCollapsed((current) => !current)} aria-label="Toggle sidebar"><Icon name="sidebar" size={15} /></button>
          </div>
        </div>
        <div className="sidebar-body sidebar-scroll no-drag">
          <section className="sidebar-standalone-sessions sidebar-group" aria-labelledby="sidebar-standalone-sessions-label" data-sidebar-session-section="temporary">
            {!sidebarCollapsed && <div className="sidebar-list-toolbar sidebar-list-toolbar-secondary sidebar-label-with-action" data-sidebar-section="sessions"><span id="sidebar-standalone-sessions-label" className="sidebar-list-label sidebar-label">SESSIONS</span><span className="sidebar-toolbar-actions"><span className="sidebar-menu-anchor"><button className={`sidebar-toolbar-button ${sessionSortOpen ? "active" : ""}`} type="button" onClick={() => { setSessionSortOpen((current) => !current); setSessionMenuOpen(null); }} aria-label="Sort sessions" aria-expanded={sessionSortOpen} title="Sort sessions"><Icon name="sort" size={14} /></button>{sessionSortOpen && <div className="sidebar-mini-menu" role="menu"><span className="sidebar-mini-menu-title">Sort sessions</span>{([['recent', 'Recently updated'], ['oldest', 'Oldest first'], ['name', 'Name'], ['created', 'Created date']] as const).map(([value, label]) => <button key={value} className={sessionSort === value ? "selected" : ""} type="button" role="menuitemradio" aria-checked={sessionSort === value} onClick={() => { setSessionSort(value); setSessionSortOpen(false); }}>{label}</button>)}</div>}</span><button className="sidebar-toolbar-button" type="button" onClick={() => void createConversation()} disabled={!workspace || busy} aria-label="New task" title="New task"><Icon name="new-chat" size={14} /></button></span></div>}
            {!sidebarCollapsed && <div className="session-list sidebar-session-group-body standalone">
              {sidebarConversations.length === 0 ? <p className="sidebar-empty">No temporary chats yet.</p> : sidebarConversations.map((item) => (
                <div className={`session-row-wrap thread-item ${item.conversation_id === activeConversationId ? "active" : ""}`} data-sidebar-session-row key={item.conversation_id}>
                  <button className="session-row thread-item-main" type="button" onClick={() => { setSessionMenuOpen(null); void selectConversation(item.conversation_id); }}>
                    <span className="session-info"><strong className="thread-item-title">{displaySessionTitle(item.title)}</strong><small>{formatTime(item.updated_at_ms)}</small></span>
                  </button>
                  <span className="session-menu-anchor sidebar-row-actions"><button className="session-row-action thread-item-more" type="button" onClick={() => { setSessionSortOpen(false); setSessionMenuOpen((current) => current === item.conversation_id ? null : item.conversation_id); }} aria-label={`More actions for ${displaySessionTitle(item.title)}`} aria-haspopup="menu" aria-expanded={sessionMenuOpen === item.conversation_id} title="More actions"><Icon name="more" size={14} /></button>{sessionMenuOpen === item.conversation_id && <div className="session-row-menu" role="menu"><span className="sidebar-mini-menu-title">Session actions</span><button type="button" role="menuitem" onClick={() => { setSessionMenuOpen(null); void selectConversation(item.conversation_id); setWorkTab("activity"); setWorkPanelOpen(true); }}>Open activity</button><button type="button" role="menuitem" onClick={() => { setSessionMenuOpen(null); void selectConversation(item.conversation_id); setWorkTab("context"); setWorkPanelOpen(true); }}>Inspect context</button><button type="button" role="menuitem" onClick={() => { setSessionMenuOpen(null); void selectConversation(item.conversation_id); setWorkTab("files"); setWorkPanelOpen(true); }}>Browse files</button></div>}</span>
                </div>
               ))}
            </div>}
          </section>
          <div className="sidebar-group project-group sidebar-session-group">
            {!sidebarCollapsed && <div className="sidebar-list-toolbar sidebar-label-with-action" data-sidebar-section="projects"><span className="sidebar-list-label sidebar-label">PROJECTS</span><button className="sidebar-toolbar-button" type="button" onClick={() => { openDestination("chat"); setWorkTab("files"); setWorkPanelOpen(true); }} aria-label="New project" title="Open project"><Icon name="new-project" size={14} /></button></div>}
            <div className="project-row-wrap sidebar-session-group-header">
              <button className="project-row sidebar-session-group-title project-toggle" type="button" onClick={() => setProjectCollapsed((current) => !current)} title={workspace?.workspace_path ?? "Workspace"} aria-expanded={!projectCollapsed}>
                  <span className="project-chevron"><Icon name={projectCollapsed ? "chevron-right" : "chevron-down"} size={13} /></span><span className="project-icon"><Icon name="folder" size={14} /></span>{!sidebarCollapsed && <><span className="project-name">{workspaceName(workspace?.workspace_path) ?? "Workspace"}</span><span className="project-active-dot" aria-label="Active" title="Active" /></>}
               </button>
             {!sidebarCollapsed && <span className="project-menu-anchor"><button className="project-row-action project-menu-button" type="button" onClick={() => setProjectMenuOpen((current) => !current)} aria-label="More project actions" aria-haspopup="menu" aria-expanded={projectMenuOpen} title="More project actions"><Icon name="more" size={14} /></button>{projectMenuOpen && <div className="project-row-menu" role="menu"><span className="sidebar-mini-menu-title">Project actions</span><button type="button" role="menuitem" onClick={() => { setProjectMenuOpen(false); openDestination("chat"); setWorkTab("files"); setWorkPanelOpen(true); }}>Open project files</button><button type="button" role="menuitem" onClick={() => { setProjectMenuOpen(false); setProjectSwitcherQuery(""); setProjectSwitcherView("list"); setProjectSwitcherOpen(true); }}>Switch project</button><button type="button" role="menuitem" onClick={() => { setProjectMenuOpen(false); void createConversation(); }}>New task</button><button type="button" role="menuitem" onClick={() => { setProjectMenuOpen(false); setSettingsSection("Projects"); openDestination("settings"); }}>Project settings</button></div>}</span>}
             {!sidebarCollapsed && projectSwitcherOpen && <span className="project-switcher-anchor home-project-switcher">{renderProjectSwitcherMenu("home-project-switcher-menu sidebar-project-switcher-menu")}</span>}
             {!sidebarCollapsed && <button className="project-row-action" type="button" onClick={() => void createConversation()} aria-label="New task in project" title="New task in project"><Icon name="new-chat" size={14} /></button>}
             </div>
             {!sidebarCollapsed && <div className={`sidebar-session-group-body project${projectCollapsed ? " collapsed" : ""}`}><div className="sidebar-session-group-clip"><div className="sidebar-session-group-list"><div className="project-subrow">{conversation?.turns.length ? activeTitle : "No chats in this project yet"}</div></div></div></div>}
            </div>
          </div>
        <div className="sidebar-footer sidebar-bottom no-drag">
          <div className="footer-actions">
            <button className={`footer-action utility-row ${destination === "settings" ? "active" : ""}`} type="button" onClick={() => openDestination("settings")} title="Settings" aria-label="Settings"><span><Icon name="settings" size={15} /></span>{!sidebarCollapsed && <span className="utility-label">Settings</span>}</button>
            <button className={`footer-action utility-row ${destination === "extensions" || destination === "plugins" ? "active" : ""}`} type="button" onClick={() => openDestination("plugins")} title="Plugins" aria-label="Plugins"><span><Icon name="plug" size={15} /></span>{!sidebarCollapsed && <span className="utility-label">Plugins</span>}</button>
            <button className="footer-action utility-row" type="button" onClick={() => { openDestination("chat"); setWorkTab("activity"); setWorkPanelOpen(true); }} title="Activity" aria-label="Activity"><span><Icon name="bell" size={15} /></span>{!sidebarCollapsed && <span className="utility-label">Activity</span>}</button>
          </div>
          {!sidebarCollapsed && <div className="footer-build sidebar-version"><span className="footer-build-version">v0.1.0</span></div>}
        </div>
        <div
          className={`sidebar-resize-handle no-drag${sidebarResizing ? " is-resizing" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          aria-valuemin={SIDEBAR_WIDTH_MIN}
          aria-valuemax={sidebarWidthMax}
          aria-valuenow={clampSidebarWidth(sidebarWidth, sidebarWidthMax)}
          aria-valuetext={`${clampSidebarWidth(sidebarWidth, sidebarWidthMax)} pixels`}
          tabIndex={0}
          onPointerDown={startSidebarResize}
          onPointerMove={moveSidebarResize}
          onPointerUp={endSidebarResize}
          onPointerCancel={(event) => { if (sidebarResizeRef.current?.pointerId === event.pointerId) finishSidebarResize(true); }}
          onLostPointerCapture={() => { if (sidebarResizeRef.current) finishSidebarResize(true); }}
          onKeyDown={resizeSidebarWithKeyboard}
        />
      </aside>
    );
  }

  function renderConversation() {
    function renderTurnContent(turn: ConversationSnapshot["turns"][number]) {
      const parts = conversation?.parts.filter((part) => part.turn_id === turn.turn_id) ?? [];
      if (parts.length === 0) return <p>{turn.content || "…"}</p>;
      return (
        <div className="message-parts">
          {parts.map((part) => part.kind === "TEXT" ? (
            <p key={`${part.turn_id}-${part.part_index}`}>{part.content}</p>
          ) : (
            <div className="message-part-record" key={`${part.turn_id}-${part.part_index}`}>
              <span>{part.kind}</span>
              <code>{part.content}</code>
            </div>
          ))}
        </div>
      );
    }

    function renderSubagentActivity() {
      if (!subagentRunId && subagentEvents.length === 0) return null;
      const status = subagentStatus?.status ?? "RUNNING";
      return (
        <article className="subagent-live" aria-label="Live subagent activity">
          <div className="subagent-result-header">
            <div><span className="eyebrow">LIVE WORKFLOW</span><strong>{status}</strong></div>
            <div className="subagent-live-actions"><span className="status-chip"><i /> {subagentEvents.length} events</span>{status === "RUNNING" && <button className="subagent-cancel" type="button" onClick={() => void cancelSubagents()}>Stop</button>}</div>
          </div>
          <div className="subagent-event-list">
            {subagentEvents.slice(-8).map((event) => {
              const payload = event.payload;
              const summary = typeof payload.summary === "string" ? payload.summary : "worker admitted";
              const detail = event.message_kind === "TASK_REQUEST"
                ? `Worker ${event.task_id} started · ${String(payload.role ?? "task")}`
                : `Worker ${event.task_id} · ${String(payload.status ?? "result")}`;
              return <div className="subagent-event" key={event.cursor}><span className="activity-icon"><Icon name={event.message_kind === "TASK_RESULT" ? "check" : "activity"} size={12} /></span><div><strong>{detail}</strong><small>{summary.slice(0, 180)}</small></div></div>;
            })}
          </div>
          {subagentGraph && subagentGraph.nodes.length > 0 && <div className="subagent-graph" aria-label="Subagent task graph">
            <div className="eyebrow">TASK GRAPH</div>
            {subagentGraph.nodes.map((node) => <div className="subagent-graph-row" key={node.task_id}><span className={`worker-dot ${node.status.toLowerCase()}`} /><strong>Worker {node.task_id}</strong><span>{node.role || "task"}</span><em>{node.status}</em></div>)}
          </div>}
        </article>
      );
    }

    const isHome = !conversation?.turns.length;

    function renderComposer(home: boolean) {
      const permissionOptions = ["Ask every time", "Accept edits", "Auto"] as const;
      const modelOptions = availableModels.length > 0
        ? availableModels
        : [{ connection_id: conversation?.conversation.connection_id ?? "local", model_id: modelId, family: null, capabilities: [], context_limit: null, output_limit: null, source: "current", revision: 0, observed_at_ms: 0 } satisfies ModelDescriptor];
      const filteredModelOptions = modelOptions.filter((model) => model.model_id.toLocaleLowerCase().includes(composerModelQuery.trim().toLocaleLowerCase()));
      return (
        <div className={home ? "home-composer-wrap" : "composer-wrap"}>
          {home && homeActionsOpen && <div className="home-actions-menu" role="menu" aria-label="Explore workspace actions"><button type="button" role="menuitem" onClick={() => { setHomeActionsOpen(false); setMessage("Summarize this workspace"); }}><Icon name="spark" size={14} /><span>Summarize workspace</span></button><button type="button" role="menuitem" onClick={() => { setHomeActionsOpen(false); setMessage("Inspect the current project"); }}><Icon name="search" size={14} /><span>Inspect project</span></button><button type="button" role="menuitem" onClick={() => { setHomeActionsOpen(false); void runSubagents(); }} disabled={!runtimeReady || !connectionSaved || subagentBusy}><Icon name="extensions" size={14} /><span>Run parallel workers</span></button></div>}
          <div className={`composer-dock composer-dock-${home ? "home" : "docked"}`} data-composer-dock={home ? "home" : "docked"}>
            <div className="composer-stack">
              {composerAutocompleteMode && <ComposerAutocomplete mode={composerAutocompleteMode} items={composerSuggestions} highlight={composerAutocompleteHighlight} onHighlight={setComposerAutocompleteHighlight} onAccept={acceptComposerSuggestion} />}
              <div className="composer-shell">
                <ComposerInput
                  value={message}
                  placeholder={home ? "Type / for commands · @ for files" : "Ask anything"}
                  disabled={busy || subagentBusy || !activeConversationId}
                  enterToSend={enterToSend}
                  onChange={handleComposerChange}
                  onKeyDown={handleComposerKeyDown}
                  onSubmit={() => void sendMessage()}
                />
                <div className="composer-toolbar">
                  <div className="composer-left">
                    <div className="composer-plus"><button className="icon-btn icon-btn-square composer-tool" type="button" onClick={() => { setComposerMenuOpen(null); setWorkPanelOpen(true); setWorkTab("files"); }} aria-label="Add files" title="Add files"><Icon name="plus" size={16} /></button></div>
                    <button className="icon-btn mode-chip composer-mode-chip" type="button" onClick={() => { setComposerMenuOpen(null); setMode(mode === "mock" ? "live" : "mock"); }} aria-label={`Agent mode · ${mode === "mock" ? "safe preview" : "live provider"}`} title={`Agent mode · ${mode === "mock" ? "Safe preview" : "Live provider"}`}><span className="composer-mode-chip-face"><Icon name="shield" size={14} /><span className="composer-mode-chip-label text-sm">Agent</span></span></button>
                    <div className="composer-menu-anchor">
                      <button className={`icon-btn permission-chip mode-chip ${composerMenuOpen === "permission" ? "active" : ""}`} type="button" onClick={() => setComposerMenuOpen((current) => current === "permission" ? null : "permission")} aria-haspopup="menu" aria-expanded={composerMenuOpen === "permission"} title="Permission mode"><span>{permissionMode}</span><span className="chevron"><Icon name="chevron-down" size={12} /></span></button>
                      {composerMenuOpen === "permission" && <div className="composer-menu composer-permission-menu" role="menu" aria-label="Permission mode">
                        {permissionOptions.map((option) => <button key={option} type="button" role="menuitemradio" aria-checked={permissionMode === option} className={permissionMode === option ? "active" : ""} onClick={() => { setPermissionMode(option); setComposerMenuOpen(null); }}>{permissionMode === option && <Icon name="check" size={13} />}<span>{option}</span></button>)}
                      </div>}
                    </div>
                  </div>
                  <div className="composer-right">
                    <div className="composer-menu-anchor">
                      <button className={`icon-btn composer-model mode-chip ${composerMenuOpen === "model" ? "active" : ""}`} type="button" onClick={() => void openComposerModelMenu()} aria-haspopup="menu" aria-expanded={composerMenuOpen === "model"} title={`${modelId} · ${thinkingMode}`}><Icon name="bot" size={14} /><span>Model</span><span className="chevron"><Icon name="chevron-down" size={12} /></span></button>
                      {composerMenuOpen === "model" && <div className="composer-menu composer-model-menu composer-model-thinking-menu" role="menu" aria-label="Model and reasoning">
                        {composerModelView === "root" ? <div className="composer-menu-root">
                          <button className="composer-menu-entry" type="button" role="menuitem" aria-haspopup="menu" onClick={() => setComposerModelView("model")}><Icon name="bot" size={14} /><span className="composer-menu-entry-label">Model</span><span className="composer-menu-entry-value" title={modelId}>{modelId}</span><Icon name="chevron-right" size={14} /></button>
                          <button className="composer-menu-entry" type="button" role="menuitem" aria-haspopup="menu" onClick={() => setComposerModelView("thinking")}><Icon name="spark" size={14} /><span className="composer-menu-entry-label">Reasoning</span><span className="composer-menu-entry-value">{thinkingMode}</span><Icon name="chevron-right" size={14} /></button>
                        </div> : <>
                          <button className="composer-menu-back" type="button" role="menuitem" onClick={() => setComposerModelView("root")}><Icon name="chevron-left" size={14} /><span>{composerModelView === "model" ? "Model" : "Reasoning"}</span></button>
                          <div className="composer-menu-separator" />
                          {composerModelView === "model" ? <>
                            <label className="composer-model-search"><Icon name="search" size={13} /><input value={composerModelQuery} onChange={(event) => setComposerModelQuery(event.target.value)} placeholder="Search models" aria-label="Search models" /></label>
                            <div className="composer-model-list">
                              {filteredModelOptions.map((model) => <button key={`${model.connection_id}:${model.model_id}`} type="button" role="menuitemradio" aria-checked={model.model_id === modelId} className={model.model_id === modelId ? "active" : ""} onClick={() => void switchConversationModel(model)}><span className="composer-model-option-main"><span className="truncate">{model.model_id}</span><span className="composer-model-option-meta">{model.family || "local model"}</span></span>{model.model_id === modelId && <Icon name="check" size={13} />}</button>)}
                              {filteredModelOptions.length === 0 && <div className="composer-model-empty">No matching models</div>}
                            </div>
                            <button className="composer-menu-secondary" type="button" role="menuitem" onClick={() => { setComposerMenuOpen(null); setComposerModelView("root"); setDestination("settings"); setSettingsSection("Models"); }}>Configure model<Icon name="chevron-right" size={13} /></button>
                          </> : <>
                            <div className="composer-thinking-heading">Reasoning display</div>
                            <div className="composer-thinking-list">{(["Detailed", "Compact"] as const).map((level) => <button key={level} className={`composer-plus-item ${thinkingMode === level ? "active" : ""}`} type="button" role="menuitemradio" aria-checked={thinkingMode === level} onClick={() => { setThinkingMode(level); setComposerMenuOpen(null); setComposerModelView("root"); }}>{level}{thinkingMode === level && <Icon name="check" size={13} />}</button>)}</div>
                          </>}
                        </>}
                      </div>}
                    </div>
                    <button className="icon-btn composer-tool composer-enhance-btn" type="button" onClick={() => conversation?.turns.length ? void runSubagents() : setHomeActionsOpen((current) => !current)} disabled={conversation?.turns.length ? subagentBusy || !runtimeReady || !connectionSaved || !activeConversationId : false} aria-label={conversation?.turns.length ? "Run parallel workers" : "Explore workspace actions"} aria-expanded={conversation?.turns.length ? undefined : homeActionsOpen} title={conversation?.turns.length ? "Run parallel workers" : "Explore workspace actions"}><Icon name="spark" size={15} /></button><button className="send-btn send-button" type="button" onClick={() => void sendMessage()} aria-label="Send message" title="Send message" disabled={busy || subagentBusy || !message.trim() || !activeConversationId}>{busy || subagentBusy ? <span className="send-loading" /> : <Icon name="arrow-up" size={16} />}</button>
                  </div>
                </div>
              </div>
              <div className="composer-status" aria-label="Workspace environment"><div className="environment-status"><span className="environment-option active">Local</span><span className="environment-option">Checkout</span></div><span className="branch-status"><Icon name="branch" size={12} />{shortPath(sourceSnapshot?.identity.branch, 32)}</span></div>
            </div>
          </div>
        </div>
      );
    }

    const homeProjectName = workspaceName(workspace?.workspace_path);

    return (
      <section className={`chat-view ${isHome ? "home-view" : "thread-view"}`} aria-label="Conversation">
        <header className={`conversation-topbar${sidebarCollapsed ? " ct-collapsed" : ""}${workPanelOpen ? " ct-work-panel-open" : ""}`} data-tauri-drag-region="true" role="toolbar" aria-label="Conversation">
          <div className="ct-left">
            <div className="ct-lead" aria-hidden={!sidebarCollapsed}>
              <button className="ct-icon-btn" type="button" tabIndex={sidebarCollapsed ? undefined : -1} onClick={() => setSidebarCollapsed(false)} aria-label="Expand sidebar" title="Expand sidebar"><Icon name="sidebar" size={15} /></button>
            </div>
            <div className="ct-title-wrap" title={homeProjectName ? `${homeProjectName} · ${displayActiveTitle}` : displayActiveTitle}>
              <span className="ct-title">{topbarTitle}</span>
            </div>
          </div>
          <div className="ct-right">
            <div className="ct-actions">
              <button className="ct-icon-btn" type="button" onClick={() => void createConversation()} disabled={!workspace || busy} aria-label="New task" title="New task"><Icon name="new-chat" size={15} /></button>
              <button className="ct-icon-btn" type="button" onClick={() => { setSearchQuery(""); setSearchOpen(true); window.requestAnimationFrame(() => globalSearchRef.current?.focus()); }} aria-label="Search" title="Search"><Icon name="search" size={15} /></button>
            </div>
          </div>
        </header>
        {error && <div className="error" role="alert"><span className="error-mark">!</span>{error}</div>}
        {isHome ? (
          <div className="home-main-content">
            <div className="home-scroll">
              <div className="home-stack-inner">
                <div className="empty-hero">
                  <div className="empty-hero-icon"><HomeMascotLogo /></div>
                  <h1>What would you like to explore temporarily?</h1>
                </div>
              </div>
            </div>
            {renderComposer(true)}
          </div>
        ) : (
          <>
            <div className="chat-scroll thread-surface">
              <div className="chat-column">
                <div className="chat-intro"><div className="intro-mark"><Icon name="spark" size={17} /></div><div><strong>AEGIS is ready</strong><span>{mode === "mock" ? "Safe preview mode" : "Live provider mode"} · {conversationInspection?.context.item_count ?? 0} context items</span></div><span className="context-chip">{runtimeReady ? "Local" : "Opening"}</span></div>
                {renderSubagentActivity()}
                {subagentResult && <article className="subagent-result" aria-label="Subagent run result"><div className="subagent-result-header"><div><span className="eyebrow">PARALLEL RUN</span><strong>{subagentResult.status}</strong></div><span className="status-chip"><i /> {subagentResult.child_results.length} workers</span></div><p>{subagentResult.root_output}</p><div className="subagent-workers">{subagentResult.child_results.map((worker) => <span className={`worker-chip ${worker.status.toLowerCase()}`} key={`${worker.task_id}-${worker.packet_hash}`}><i />Worker {worker.task_id} · {worker.status}</span>)}</div><small>Graph {shortPath(subagentResult.graph_hash, 18)} · {subagentResult.graph_authority}</small></article>}
                {conversationInspection?.approvals.map((approval) => <article className="approval-card" key={approval.approval_id} aria-label="Approval required"><div><span className="eyebrow">REVIEW REQUIRED</span><strong>{approval.tool_name}</strong></div><span className="status-chip warning"><i />{approval.status}</span><p>AEGIS is waiting for a policy decision. Arguments are redacted at the renderer boundary.</p><small>Risk {approval.risk} · {approval.argument_keys.length} argument keys</small></article>)}
                {conversation.turns.map((turn) => (
                  <article className={`message-row ${turn.role}`} key={turn.turn_id}>
                    <div className="message-avatar">{turn.role === "user" ? "Y" : "A"}</div>
                    <div className="message-content"><div className="message-meta"><strong>{turn.role === "user" ? "You" : "AEGIS"}</strong><span>r{turn.revision}</span></div>{renderTurnContent(turn)}</div>
                  </article>
                ))}
              </div>
            </div>
            {renderComposer(false)}
          </>
        )}
      </section>
    );
  }

  function renderWorkPanel() {
    const selected = sourceSnapshot?.files.find((file) => file.relative_path === selectedFile);
    const workTabLabels: Record<WorkTab, string> = {
      files: selected?.relative_path.split(/[\\/]/).at(-1) ?? "Files",
      map: "Map",
      activity: "Activity",
      context: "Context",
    };
    const workTabIcons: Record<WorkTab, IconName> = {
      files: "file",
      map: "map",
      activity: "activity",
      context: "spark",
    };
    const activeWorkTabLabel = workTabLabels[workTab];
    type FileTreeFolder = {
      folders: Map<string, FileTreeFolder>;
      files: SourceSnapshot["files"];
    };
    const fileTreeRoot: FileTreeFolder = { folders: new Map(), files: [] };
    for (const file of filteredFiles) {
      const parts = file.relative_path.split(/[\\/]/).filter(Boolean);
      let folder = fileTreeRoot;
      for (const part of parts.slice(0, -1)) {
        let child = folder.folders.get(part);
        if (!child) {
          child = { folders: new Map(), files: [] };
          folder.folders.set(part, child);
        }
        folder = child;
      }
      folder.files.push(file);
    }
    const renderFileTree = (folder: FileTreeFolder, depth = 0): ReactNode => (
      <>
        {[...folder.folders.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([name, child]) => (
          <div key={`${depth}-${name}`}>
            <div className="file-tree-row file-tree-folder" style={{ paddingLeft: 12 + depth * 14 }}>
              <Icon name="chevron-down" size={12} />
              <Icon name="folder" size={14} />
              <span className="file-tree-name">{name}</span>
            </div>
            {renderFileTree(child, depth + 1)}
          </div>
        ))}
        {folder.files.slice().sort((left, right) => left.relative_path.localeCompare(right.relative_path)).map((file) => (
          <button className={`file-tree-row ${selectedFile === file.relative_path ? "active" : ""}`} style={{ paddingLeft: 12 + depth * 14 + 16 }} key={file.relative_path} type="button" title={file.relative_path} onClick={() => setSelectedFile(file.relative_path)}>
            <Icon name="file" size={14} />
            <span className="file-tree-name">{file.relative_path.split(/[\\/]/).filter(Boolean).at(-1) ?? file.relative_path}</span>
          </button>
        ))}
      </>
    );
    return (
      <aside className="work-panel" aria-label="Workspace panel">
        <div
          className={`work-panel-resize no-drag${workPanelResizing ? " is-resizing" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize workspace panel"
          aria-valuemin={WORK_PANEL_WIDTH_MIN}
          aria-valuemax={workPanelWidthMax}
          aria-valuenow={Math.max(WORK_PANEL_WIDTH_MIN, Math.min(workPanelWidthMax, workPanelWidth))}
          aria-valuetext={`${Math.max(WORK_PANEL_WIDTH_MIN, Math.min(workPanelWidthMax, workPanelWidth))} pixels`}
          tabIndex={0}
          onPointerDown={startWorkPanelResize}
          onPointerMove={moveWorkPanelResize}
          onPointerUp={endWorkPanelResize}
          onPointerCancel={(event) => { if (workPanelResizeRef.current?.pointerId === event.pointerId) finishWorkPanelResize(true); }}
          onLostPointerCapture={() => { if (workPanelResizeRef.current) finishWorkPanelResize(true); }}
          onKeyDown={resizeWorkPanelWithKeyboard}
        />
        <div className="work-panel-main">
          <div className="work-panel-header" data-tauri-drag-region="true">
            <div className="work-panel-tab-strip-wrap no-drag work-tabs" role="tablist" aria-label="Workspace panel tabs">
              <div className="work-panel-tab-strip">
                <div className="work-panel-tab active">
                  <div className="work-tab-active-wrap">
                  <button className="work-panel-tab-button work-tab-active no-drag" type="button" role="tab" aria-selected="true" aria-haspopup="menu" aria-expanded={workTabMenuOpen} aria-controls="work-panel-tab-menu" onClick={() => setWorkTabMenuOpen((current) => !current)}>
                    <Icon name={workTabIcons[workTab]} size={14} />
                    <span className="work-panel-tab-label work-tab-active-label">{activeWorkTabLabel}</span>
                    <Icon name="chevron-down" size={12} />
                  </button>
                    {workTabMenuOpen && <div id="work-panel-tab-menu" className="work-tab-menu no-drag" role="menu" aria-label="Workspace panel tabs">
                      {(Object.keys(workTabLabels) as WorkTab[]).map((tab) => <button key={tab} className={tab === workTab ? "active" : ""} type="button" role="menuitemradio" aria-checked={tab === workTab} onClick={() => { setWorkTab(tab); setWorkTabMenuOpen(false); }}>
                        <Icon name={workTabIcons[tab]} size={14} />
                        <span>{workTabLabels[tab]}</span>
                      </button>)}
                    </div>}
                  </div>
                  <button className="work-panel-tab-close no-drag" type="button" aria-label={`Close ${activeWorkTabLabel}`} title={`Close ${activeWorkTabLabel}`} onClick={() => { setWorkTabMenuOpen(false); setWorkPanelOpen(false); }}><Icon name="close" size={12} /></button>
                </div>
              </div>
            </div>
            <div className="work-panel-actions no-drag">
              <button className="work-panel-toggle" type="button" onClick={() => { setWorkTabMenuOpen(false); setWorkPanelOpen(false); }} aria-label="Toggle work panel" aria-expanded={workPanelOpen} title="Toggle work panel"><Icon name="panel" size={15} /></button>
            </div>
          </div>
          <div className="work-panel-body">
            {workTab === "files" && <div className="file-panel"><div className="panel-title"><div><span className="eyebrow">WORKSPACE</span><strong>Project files</strong></div><span className="file-count">{sourceSnapshot?.files.length ?? "—"}</span></div><label className="file-search"><Icon name="search" size={14} /><input ref={searchRef} value={fileFilter} onChange={(event) => setFileFilter(event.target.value)} placeholder="Search files" /></label><div className="file-tree">{filteredFiles.length ? renderFileTree(fileTreeRoot) : <p className="panel-empty">No indexed files match this search.</p>}</div>{selected && <div className="file-inspector"><span className="eyebrow">SELECTED FILE</span><strong>{selected.relative_path}</strong><div><span>{selected.language || "unknown"}</span><span>{Math.round(selected.size_bytes / 1024)} KB</span></div><p>{selected.extraction_status === "ok" ? "Symbols and imports are indexed." : selected.extraction_status}</p></div>}</div>}
            {workTab === "map" && <div className="map-panel">{workspaceGraph && sourceSnapshot ? <WorkspaceGraphView graph={workspaceGraph} loadMemories={loadMemoriesForFile} /> : <p className="panel-empty">The source map is opening.</p>}</div>}
            {workTab === "activity" && <div className="activity-panel"><div className="panel-title"><div><span className="eyebrow">SESSION</span><strong>Activity</strong></div><span className="status-chip"><i /> {conversation?.executions.some((item) => item.status === "RUNNING") ? "Live" : "Ready"}</span></div>{conversationInspection?.timeline.length ? conversationInspection.timeline.slice().reverse().map((item) => <div className="activity-item" key={item.event_id}><span className="activity-icon"><Icon name={item.kind === "EXECUTION" && item.status === "COMPLETED" ? "check" : "activity"} size={13} /></span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>) : activityItems.length ? activityItems.map((item) => <div className="activity-item" key={`${item.title}-${item.timestamp}-${item.detail}`}><span className="activity-icon"><Icon name={item.icon} size={13} /></span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>) : <p className="panel-empty">No session activity yet.</p>}</div>}
            {workTab === "context" && <div className="context-panel"><div className="panel-title"><div><span className="eyebrow">CONTEXT COCKPIT</span><strong>What AEGIS used</strong></div><span className="status-chip"><i />{conversationInspection?.context.status ?? "Unknown"}</span></div>{conversationInspection ? <><div className="context-metrics"><div><span>Items</span><strong>{conversationInspection.context.item_count ?? 0}</strong></div><div><span>Tokens</span><strong>{conversationInspection.context.token_count ?? "—"}</strong></div><div><span>History</span><strong>{conversationInspection.context.history_turn_count}</strong></div></div><div className="context-detail"><span>Source revision</span><code>{shortPath(conversationInspection.context.source_revision, 22)}</code></div><div className="context-detail"><span>Manifest</span><code>{shortPath(conversationInspection.context.context_manifest_hash, 22)}</code></div><div className="context-detail"><span>Selector</span><code>{conversationInspection.context.selection_backend ?? "—"}</code></div><p className="context-redaction">Sensitive prompt, tool arguments, and provider output remain outside this observer view.</p></> : <p className="panel-empty">Context inspection is opening.</p>}</div>}
          </div>
        </div>
      </aside>
    );
  }

  function renderSettings() {
    const groups = [
      { label: "Preferences", items: [["General", "sliders"], ["AI", "spark"], ["Shortcuts", "keyboard"]] },
      { label: "Agent", items: [["Instructions", "file"], ["Models", "bot"], ["Skills", "plug"], ["MCP", "server"], ["Extensions", "plug"], ["Subagents", "bot"]] },
      { label: "Workspace", items: [["Import", "download"], ["Projects", "folder"]] },
      { label: "System", items: [["Remote Hosts", "globe"], ["Info", "info"]] },
    ] as const;

    const icons: Record<string, IconName> = {
      General: "sliders",
      AI: "spark",
      Shortcuts: "keyboard",
      Instructions: "file",
      Models: "bot",
      Skills: "plug",
      MCP: "server",
      Extensions: "plug",
      Subagents: "bot",
      Import: "download",
      Projects: "folder",
      Info: "info",
      "Remote Hosts": "globe",
    };
    const settingsSearch = settingsQuery.trim().toLocaleLowerCase();
    const visibleGroups = groups.map((group) => ({ ...group, items: group.items.filter(([section]) => !settingsSearch || group.label.toLocaleLowerCase().includes(settingsSearch) || section.toLocaleLowerCase().includes(settingsSearch)) })).filter((group) => group.items.length > 0);

    const row = (title: string, description: string, value: ReactNode, onClick?: () => void) => (
      <div className={`pi-settings-row ${onClick ? "is-action" : ""}`}>
        <div><strong>{title}</strong><span>{description}</span></div>
        {onClick ? <button type="button" onClick={onClick}>{value}<Icon name="chevron-down" size={14} /></button> : <span className="pi-settings-value">{value}</span>}
      </div>
    );

    const settingsTitle = settingsSection === "Projects" ? "Project archive" : settingsSection === "Models" ? "Model configuration" : settingsSection;
    const projectName = workspace?.workspace_path?.split(/[\\/]/).filter(Boolean).pop() ?? "AEGIS workspace";
    const projectMatches = projectName.toLocaleLowerCase().includes(projectFilter.trim().toLocaleLowerCase()) || (workspace?.workspace_path ?? "").toLocaleLowerCase().includes(projectFilter.trim().toLocaleLowerCase());
    const capabilitySearch = capabilityQuery.trim().toLocaleLowerCase();
    const subagentCapabilities = [
      ["Research worker", "Bounded evidence-gathering worker using the native task graph.", "research"],
      ["Code worker", "Workspace-aware implementation worker with hash-bound result packets.", "code"],
      ["Review worker", "Independent verification worker for tests, contracts, and regressions.", "review"],
    ] as const;
    const visibleSubagents = subagentCapabilities.filter(([name, description]) => !capabilitySearch || `${name} ${description}`.toLocaleLowerCase().includes(capabilitySearch));
    const mcpServers = [["Local host boundary", "No MCP servers connected", "Global"]] as const;
    const visibleMcpServers = mcpServers.filter(([name, description, level]) => !capabilitySearch || `${name} ${description} ${level}`.toLocaleLowerCase().includes(capabilitySearch));

    const content = settingsSection === "General" ? (
      <div className="pi-settings-stack">
        <section className="pi-settings-group"><h2>Appearance</h2><div className="pi-settings-card">
          {row("Theme", "Follow system, light, or dark.", theme === "system" ? "System" : theme === "dark" ? "Dark" : "Light", () => setTheme(theme === "system" ? "dark" : theme === "dark" ? "light" : "system"))}
          {row("Language", "Menus, settings, and the rest of the app.", <span className="pi-settings-select-value">English <Icon name="chevron-down" size={14} /></span>)}
          {row("Font", "Global UI font.", <span className="pi-settings-select-value">System default <Icon name="chevron-down" size={14} /></span>)}
          <div className="pi-settings-row pi-settings-font-size-row">
            <div><strong>Font size</strong><span>Pick a cup size. Window zoom is unchanged.</span></div>
            <div className="font-size-control">
              <div className="font-size-options" role="group" aria-label="Font size preset">
                {(["Tall", "Grande", "Venti", "Trenta"] as const).map((preset) => <button key={preset} className={fontSizePreset === preset ? "active" : ""} type="button" onClick={() => setFontSizePreset(preset)}>{preset}</button>)}
              </div>
              <div className="font-size-slider-row"><input type="range" min="0" max="3" step="1" value={["Tall", "Grande", "Venti", "Trenta"].indexOf(fontSizePreset)} onChange={(event) => setFontSizePreset((["Tall", "Grande", "Venti", "Trenta"] as const)[Number(event.target.value)])} aria-label="Font size" /><span>100%</span></div>
            </div>
          </div>
        </div></section>
        <section className="pi-settings-group"><h2>Network</h2><div className="pi-settings-card">
          <div className="pi-settings-row pi-settings-proxy-row">
            <div><strong>Proxy</strong><span>HTTP, HTTPS, and SOCKS5 for model calls, marketplace, updates, and the in-app browser.</span></div>
            <div className="proxy-options" role="group" aria-label="Proxy mode">
              {(["System", "Direct", "Custom"] as const).map((option) => <button key={option} className={proxyMode === option ? "active" : ""} type="button" onClick={() => setProxyMode(option)}>{option}</button>)}
            </div>
          </div>
        </div></section>
      </div>
    ) : settingsSection === "AI" ? (
      <div className="ai-settings-page">
        <section className="pi-settings-group"><h2>Permissions</h2><div className="pi-settings-card">
          {row("Permission mode", "Controls how tool actions are approved.", permissionMode, () => setPermissionMode(permissionMode === "Ask every time" ? "Accept edits" : permissionMode === "Accept edits" ? "Auto" : "Ask every time"))}
        </div></section>
        <section className="pi-settings-group"><h2>Voice</h2><div className="pi-settings-card">
          <div className="pi-settings-row"><div><strong>Voice input</strong><span>Use the host microphone for hands-free prompts.</span></div><button className={`settings-toggle ${voiceEnabled ? "on" : ""}`} type="button" role="switch" aria-checked={voiceEnabled} aria-label="Voice input" onClick={() => setVoiceEnabled((current) => !current)}><span /></button></div>
        </div></section>
        <section className="pi-settings-group"><h2>Defaults</h2><div className="pi-settings-card">
          <div className="pi-settings-row"><div><strong>Mode</strong><span>Mode for new sessions.</span></div><div className="ai-mode-options" role="group" aria-label="Default mode">{(["Agent", "Plan", "Goal"] as const).map((option) => <button key={option} className={agentDefaultMode === option ? "active" : ""} type="button" aria-pressed={agentDefaultMode === option} onClick={() => setAgentDefaultMode(option)}>{option}</button>)}</div></div>
          {row("Command shell", "Run local shell commands through the host.", "Disabled", () => undefined)}
          {row("Link open destination", "Where links opened by the agent are shown.", "Work panel browser", () => undefined)}
          {row("Thinking display mode", "Detailed shows full reasoning text.", thinkingMode, () => setThinkingMode(thinkingMode === "Detailed" ? "Compact" : "Detailed"))}
          {row("Context usage readout", "Choose the value shown near the conversation.", contextUsage, () => setContextUsage(contextUsage === "Remaining" ? "Used" : "Remaining"))}
          <div className="pi-settings-row"><div><strong>Enter to send</strong><span>{enterToSend ? "Press Enter to submit a message." : "Press Ctrl/⌘+Enter to submit a message."}</span></div><button className={`settings-toggle ${enterToSend ? "on" : ""}`} type="button" role="switch" aria-checked={enterToSend} aria-label="Enter to send" onClick={() => setEnterToSend((current) => !current)}><span /></button></div>
          {row("Large paste threshold", "Long text pastes become a temporary session file.", "100,000 characters", () => undefined)}
        </div></section>
      </div>
    ) : settingsSection === "Shortcuts" ? (
      <div className="shortcut-settings-page"><section className="shortcut-settings-card"><div className="shortcut-settings-header"><div><h2>Keyboard shortcuts</h2><p>Change the keys used by the desktop shell.</p></div><button type="button" onClick={() => setCapabilityNotice("Default shortcuts restored for this session.")}>Reset to defaults</button></div>{[["Open search", "Find files and workspace actions.", "Ctrl/⌘ K"], ["Toggle sidebar", "Show or hide the session rail.", "Ctrl/⌘ B"], ["Open work panel", "Inspect files, map, activity, and context.", "Ctrl/⌘ J"], ["Send message", "Submit the current composer prompt.", enterToSend ? "Enter" : "Ctrl/⌘ Enter"]].map(([title, description, binding]) => <div className="shortcut-settings-row" key={title}><div><strong>{title}</strong><span>{description}</span></div><button type="button" onClick={() => setCapabilityNotice(`${title} is currently ${binding}.`)}>{binding}</button></div>)}</section>{capabilityNotice && <div className="shortcut-settings-notice" role="status">{capabilityNotice}</div>}</div>
    ) : settingsSection === "Instructions" ? (
      <div className="instruction-settings-page"><section className="instruction-settings-card"><div className="instruction-settings-heading"><div><h2>Global instructions</h2><p>Instructions applied to new AEGIS conversations.</p><span>Host persistence is not connected; this editor is session-local.</span></div><span className="instruction-file-label">workspace instructions</span></div><textarea value={instructionsDraft} onChange={(event) => { setInstructionsDraft(event.target.value); setInstructionsNotice(null); }} aria-label="Global instructions" spellCheck={false} /><div className="instruction-settings-actions"><button className="settings-primary" type="button" onClick={() => setInstructionsNotice("Draft saved for this session.")}>Save instructions</button>{instructionsNotice && <span role="status">{instructionsNotice}</span>}</div></section></div>
    ) : settingsSection === "Models" ? (
      <div className="model-configuration-page">
        <section className="model-section"><h2>Defaults</h2><div className="model-default-row"><div><strong>Default model</strong><span>{connectionSaved ? modelId : "No default"}</span></div><button type="button" onClick={() => setModelEditorOpen(true)}>Change <Icon name="chevron-down" size={14} /></button></div></section>
        <section className="model-section"><div className="model-section-heading"><h2>AI providers <span>1</span></h2><button type="button" onClick={() => setModelEditorOpen(true)}><Icon name="plus" size={14} />Add provider</button></div><div className="model-provider-row"><div><strong>AEGIS local</strong><span>{shortPath(endpoint, 42)} · 1 model</span></div><span className="model-provider-default">Make default</span><button type="button" onClick={() => setModelEditorOpen(true)} aria-label="Edit provider">✎</button><button type="button" aria-label="More provider actions">⋯</button><button className={`model-provider-toggle ${connectionSaved ? "active" : ""}`} type="button" aria-label="Toggle provider" aria-pressed={connectionSaved} onClick={() => setConnectionSaved((current) => !current)}><span /></button></div></section>
        <section className="model-section"><div className="model-section-heading"><h2>Vendor accounts</h2><button type="button" onClick={() => setModelEditorOpen(true)}>⌕ Add account</button></div><div className="model-vendor-empty">No vendor account is signed in yet.</div></section>
        <div className="model-catalog-row"><span>Catalog: local provider snapshot · 1 model · updated live</span><button type="button" onClick={() => setConnectionSaved(false)}><Icon name="refresh" size={14} />Refresh model catalog</button></div>
        {modelEditorOpen && <div className="model-editor"><div className="model-editor-heading"><strong>Configure local provider</strong><button type="button" onClick={() => setModelEditorOpen(false)} aria-label="Close provider editor">×</button></div><label>Endpoint<input aria-label="Provider endpoint" value={endpoint} onChange={(event) => { setEndpoint(event.target.value); setConnectionSaved(false); }} /></label><label>Model<input aria-label="Provider model" value={modelId} onChange={(event) => { setModelId(event.target.value); setConnectionSaved(false); }} /></label><div className="model-editor-actions"><button className="settings-primary" type="button" onClick={() => void configureConnection().then(() => setModelEditorOpen(false))} disabled={!endpoint.trim() || !modelId.trim()}>Save connection</button><span className={connectionSaved ? "good" : "muted"}>{connectionSaved ? "Saved" : "Secrets stay outside the renderer."}</span></div></div>}
      </div>
    ) : settingsSection === "Skills" ? (
      <div className="capability-settings-page"><div className="capability-settings-toolbar"><label><Icon name="search" size={14} /><input value={capabilityQuery} onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search skills" aria-label="Search skills" /></label><button className="settings-primary" type="button" onClick={() => openDestination("extensions")}><Icon name="plug" size={14} />Browse skills</button></div><div className="capability-settings-group"><div className="capability-settings-group-heading"><strong>Local capabilities</strong><span>3</span></div>{[["Browser research", "Host-injected public research boundary."], ["Vision capture", "Read workspace screenshots and visual artifacts."], ["Code reuse", "Search approved snippets before generating new code."]].filter(([name, description]) => !capabilitySearch || `${name} ${description}`.toLocaleLowerCase().includes(capabilitySearch)).map(([name, description]) => <div className="capability-settings-row" key={name}><span className="capability-settings-glyph"><Icon name="plug" size={15} /></span><div><strong>{name}</strong><span>{description}</span></div><button type="button" onClick={() => openDestination("extensions")}>View details <Icon name="chevron-right" size={13} /></button></div>)}</div></div>
    ) : settingsSection === "Extensions" ? (
      <div className="pi-settings-stack"><section className="pi-settings-group"><h2>Extensions</h2><div className="pi-settings-card"><div className="pi-settings-empty"><Icon name="plug" size={18} /><div><strong>Local capability catalog</strong><span>Open the AEGIS extension surface to inspect connected skills.</span></div><button className="settings-primary" type="button" onClick={() => openDestination("extensions")}>Open extensions</button></div></div></section></div>
    ) : settingsSection === "MCP" ? (
      <div className="capability-settings-page"><div className="capability-settings-toolbar"><label><Icon name="search" size={14} /><input value={capabilityQuery} onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search MCP servers" aria-label="Search MCP servers" /></label><div><button className="settings-secondary" type="button" onClick={() => setCapabilityNotice("MCP adapter is not connected to the local host yet.")}>Add MCP server</button><button className="settings-primary" type="button" onClick={() => setCapabilityNotice("MCP catalog adapter is not connected to the local host yet.")}><Icon name="server" size={14} />Browse catalog</button></div></div><div className="capability-settings-group"><div className="capability-settings-group-heading"><strong>Global</strong><span>{visibleMcpServers.length}</span></div>{visibleMcpServers.map(([name, description, level]) => <div className="capability-settings-row" key={name}><span className="capability-settings-glyph"><Icon name="server" size={15} /></span><div><strong>{name}</strong><span>{description} · {level}</span></div><span className="capability-settings-status">Needs setup</span></div>)}</div>{capabilityNotice && <div className="capability-settings-notice" role="status">{capabilityNotice}</div>}</div>
    ) : settingsSection === "Subagents" ? (
      <div className="capability-settings-page"><div className="capability-settings-toolbar"><label><Icon name="search" size={14} /><input value={capabilityQuery} onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search subagents" aria-label="Search subagents" /></label><button className="settings-primary" type="button" onClick={() => { setCapabilityNotice("Create a subagent from the bounded worker template in the workspace."); openDestination("chat"); }}><Icon name="plus" size={14} />Add subagent</button></div><div className="capability-settings-group"><div className="capability-settings-group-heading"><strong>Built-in workers</strong><span>{visibleSubagents.length}</span></div>{visibleSubagents.map(([name, description, role]) => <div className="capability-settings-row" key={name}><span className="capability-settings-glyph"><Icon name="bot" size={15} /></span><div><strong>{name}</strong><span>{description}</span><small>AEGIS local · {role} · mailbox scoped</small></div><button className={`settings-toggle ${parallelWorkersEnabled ? "on" : ""}`} type="button" role="switch" aria-checked={parallelWorkersEnabled} aria-label={`Enable ${name}`} onClick={() => setParallelWorkersEnabled((current) => !current)}><span /></button></div>)}</div>{capabilityNotice && <div className="capability-settings-notice" role="status">{capabilityNotice}</div>}</div>
    ) : settingsSection === "Remote Hosts" ? (
      <div className="remote-hosts-settings-page"><section className="pi-settings-group"><h2>Remote hosts</h2><div className="pi-settings-card"><div className="pi-settings-empty"><Icon name="globe" size={18} /><div><strong>Pair a remote AEGIS host</strong><span>Remote execution is intentionally disabled until a host adapter is connected.</span></div><span className="pi-settings-value">Not connected</span></div></div></section><div className="capability-settings-notice" role="status">Remote host pairing is not connected to the local host yet.</div></div>
    ) : settingsSection === "Info" ? (
      <div className="pi-settings-stack"><section className="pi-settings-group"><h2>Application</h2><div className="pi-settings-card">
        {row("AEGIS", "Local desktop agent.", "0.1.0")}
        {row("Protocol", "Renderer-to-host command contract.", "v1")}
        {row("Runtime", "Native host status.", runtimeReady ? "Online" : "Opening")}
      </div></section></div>
    ) : settingsSection === "Import" ? (
      <div className="import-configuration-page">
        {importNotice && <div className="import-notice" role="status">{importNotice}</div>}
        {(["Import from other tools", "Model configuration", "Skills", "MCP servers"] as const).map((title) => <section className="import-settings-card" key={title}><div className="import-settings-heading"><div><h2>{title}</h2><p>{title === "Import from other tools" ? "Find local sessions from other agent tools." : title === "Model configuration" ? "Find local provider and model settings." : title === "Skills" ? "Scan local skill directories." : "Scan local MCP server configurations."}</p></div><button type="button" onClick={() => setImportNotice(title === "Import from other tools" ? "No importable sessions found on this machine." : `${title} import adapter is not connected to the local host yet.`)}>Scan</button></div><div className="import-settings-empty">{importNotice && title === "Import from other tools" ? "No importable sessions found on this machine." : "Nothing scanned yet."}</div></section>)}
      </div>
    ) : settingsSection === "Projects" ? (
      <div className="project-archive-page">
        <p className="project-archive-subtitle">Opened folders and their chats.</p>
        <div className="project-archive-toolbar">
          <div className="project-sort-control" role="group" aria-label="Project sort">
            {(["Recent", "Name"] as const).map((sort) => <button key={sort} className={projectSort === sort ? "active" : ""} type="button" onClick={() => setProjectSort(sort)}>{sort}</button>)}
          </div>
          <label className="project-search-control"><Icon name="search" size={14} /><input value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)} placeholder="Search projects" aria-label="Search projects" /></label>
          <button className="project-add-button" type="button" onClick={() => { openDestination("chat"); setWorkTab("files"); setWorkPanelOpen(true); }}><Icon name="plus" size={14} />Add project</button>
        </div>
        <div className="project-archive-label">ALL PROJECTS <span>1</span></div>
        {projectMatches ? <div className="project-archive-list"><div className="project-archive-row"><span className="project-archive-chevron"><Icon name="chevron-right" size={15} /></span><span className="project-archive-icon"><Icon name="folder" size={18} /></span><div className="project-archive-main"><strong>{projectName}</strong><span>{shortPath(workspace?.workspace_path, 66)} · {conversations.length} session{conversations.length === 1 ? "" : "s"}</span></div><span className="project-archive-status">{workspace ? "Open" : "Opening"}</span><span className="project-archive-time">{projectSort === "Recent" ? "this minute" : projectName}</span></div></div> : <div className="project-archive-empty"><Icon name="folder" size={18} /><strong>No projects found</strong><span>Try another search.</span></div>}
      </div>
    ) : (
      <div className="pi-settings-stack"><section className="pi-settings-group"><h2>{settingsSection}</h2><div className="pi-settings-card"><div className="pi-settings-empty"><Icon name={icons[settingsSection] ?? "info"} size={18} /><div><strong>{settingsSection === "Projects" ? shortPath(workspace?.workspace_path, 42) : "AEGIS local workspace"}</strong><span>{settingsSection === "Projects" ? "Project state is owned by the local host." : "This destination is available in the local-first shell."}</span></div>{settingsSection === "Projects" && <span className="pi-settings-value">{sourceSnapshot?.files.length ?? "—"} files</span>}</div></div></section></div>
    );

    return <main className="destination-page settings-page"><aside className="settings-nav" data-tauri-drag-region="true" aria-label="Settings sections"><div className="settings-nav-scroll"><button className="settings-back" type="button" onClick={() => openDestination("chat")}><Icon name="chevron-left" size={14} />Back to app</button><label className="settings-search"><Icon name="search" size={14} /><input value={settingsQuery} onChange={(event) => setSettingsQuery(event.target.value)} placeholder="Search settings…" aria-label="Search settings" /></label>{visibleGroups.map((group) => <div className="settings-nav-block" key={group.label}><span className="settings-nav-group">{group.label}</span>{group.items.map(([section]) => <button className={settingsSection === section ? "active" : ""} key={section} type="button" onClick={() => setSettingsSection(section)}><span className="settings-nav-item-label"><Icon name={icons[section]} size={14} />{section}</span></button>)}</div>)}{settingsSearch && visibleGroups.length === 0 && <p className="settings-search-empty">No matching settings</p>}</div></aside><section className="settings-content"><div className="settings-content-inner"><h1 className="settings-section-title">{settingsTitle}</h1>{content}</div></section></main>;
  }

  function renderExtensions() {
    const extensionIcon = (title: string): IconName => {
      if (title === "Source map") return "map";
      if (title === "Memory") return "shield";
      if (title === "Conversations") return "sessions";
      if (title === "Provider adapter") return "server";
      if (title === "Browser research" || title === "Reddit adapter") return "search";
      if (title === "Vision capture") return "files";
      if (title === "Code reuse") return "file";
      return "bot";
    };
    const installedCards = [
      ["Source map", "Inspect indexed files, symbols, imports, and lineage.", "Connected", "map"],
      ["Memory", "Search workspace-aware local memory from the work panel.", "Connected", "chat"],
      ["Conversations", "Append-only local sessions with revisioned turns.", "Connected", "chat"],
      ["Provider adapter", "Use the configured OpenAI-compatible local endpoint.", connectionSaved ? "Connected" : "Needs setup", "settings"],
      ["Browser research", "Public research adapters are available; browser capture remains explicitly host-injected.", "Needs setup", "extensions"],
      ["Subagents", "Bounded parallel workers with native graph validation and hash-bound result packets.", runtimeReady && connectionSaved ? "Connected" : "Needs setup", "chat"],
    ];
    const marketplaceCards = [
      ["Browser research", "Public web research through an explicitly host-injected browser boundary.", "Needs setup", "extensions"],
      ["Vision capture", "Inspect screenshots and visual artifacts without granting the renderer native access.", "Needs setup", "extensions"],
      ["Code reuse", "Search approved local and public snippets before generating new implementation text.", "Needs setup", "extensions"],
      ["Reddit adapter", "Connect a user-approved public research adapter at the host boundary.", "Needs setup", "extensions"],
    ];
    const cards = extensionView === "installed" ? installedCards : marketplaceCards;
    const normalizedExtensionQuery = extensionQuery.trim().toLocaleLowerCase();
    const visibleCards = cards.filter(([title, description]) => !normalizedExtensionQuery || `${title} ${description}`.toLocaleLowerCase().includes(normalizedExtensionQuery));
    const selected = cards.find(([title]) => title === selectedSkill);
    const readyCards = visibleCards.filter(([, , status]) => status === "Connected");
    const setupCards = visibleCards.filter(([, , status]) => status !== "Connected");
    const renderExtensionCard = ([title, description, status, target]: string[]) => <article className="extension-card" key={title}>
      <div className="extension-card-main">
        <div className="extension-card-title"><div className="extension-icon"><Icon name={extensionIcon(title)} size={15} /></div><div><h2>{title}</h2><p className="extension-meta">AEGIS local · v0.1.0 · {status === "Connected" ? "ready" : "setup needed"}</p></div></div>
        <p className="extension-description">{description}</p>
        <div className="extension-capabilities"><span>{title === "Browser research" ? "Host injected" : "Read workspace"}</span><span>{status === "Connected" ? "Available" : "Needs setup"}</span></div>
      </div>
      <div className="extension-card-footer"><span className={status === "Connected" ? "connected" : "pending"}>{status === "Connected" ? "✓ Connected" : "Needs setup"}</span><button type="button" onClick={() => target === "settings" ? openDestination("settings") : target === "map" ? (openDestination("chat"), setWorkTab("map"), setWorkPanelOpen(true)) : target === "chat" ? openDestination("chat") : setSelectedSkill(title)}>{status === "Connected" ? "Open" : "View details"}<Icon name="chevron-right" size={13} /></button></div>
    </article>;
    const renderInstalledRow = ([title, description, status, target]: string[]) => <div className={`extension-installed-row ${status === "Connected" ? "active" : "attention"}`} key={title}>
      <span className="extension-installed-glyph"><Icon name={status === "Connected" ? extensionIcon(title) : "info"} size={15} /></span>
      <div className="extension-installed-copy"><div className="extension-installed-title"><strong>{title}</strong>{status !== "Connected" && <span className="extension-local-tag">Needs setup</span>}</div><div className="extension-installed-meta">AEGIS local · v0.1.0</div>{status !== "Connected" ? <p className="extension-installed-error">Host adapter is not connected.</p> : <div className="extension-installed-details">⌄ Details</div>}</div>
      <div className="extension-installed-actions"><span className="extension-scope-chip">Local</span><button type="button" onClick={() => status === "Connected" ? (target === "map" ? (openDestination("chat"), setWorkTab("map"), setWorkPanelOpen(true)) : target === "settings" ? openDestination("settings") : openDestination("chat")) : setSelectedSkill(title)}>{status === "Connected" ? "Open" : "View details"}</button><button className="extension-row-more" type="button" onClick={() => setSelectedSkill(title)} aria-label={`More actions for ${title}`} title="More actions"><Icon name="more" size={15} /></button></div>
    </div>;
    return (
      <main className="destination-page extensions-page">
        <header className="destination-header extension-header">
          <div className="extension-heading">
            <span className="extension-page-icon"><Icon name="plug" size={18} /></span>
            <div><h1>Extensions</h1></div>
          </div>
          <div className="extension-actions">
            <button className="primary-extension-action" type="button" onClick={() => { setExtensionView("marketplace"); setSelectedSkill(null); }}><Icon name={extensionView === "installed" ? "download" : "refresh"} size={14} />{extensionView === "installed" ? "Browse marketplace" : "Refresh marketplace"}</button>
            <span className="extension-menu-anchor"><button className={`topbar-icon ${extensionMenuOpen ? "active" : ""}`} type="button" onClick={() => setExtensionMenuOpen((current) => !current)} aria-label="More extension actions" aria-expanded={extensionMenuOpen} title="More extension actions"><Icon name="more" size={16} /></button>{extensionMenuOpen && <div className="extension-menu" role="menu"><button type="button" role="menuitem" onClick={() => { setExtensionMenuOpen(false); setExtensionNotice("Plugin update adapter is not connected to the local host yet."); }}>Check for updates</button><button type="button" role="menuitem" onClick={() => { setExtensionMenuOpen(false); setExtensionNotice("Automatic plugin updates are not enabled in the local-first shell."); }}>Apply automatic updates</button><button type="button" role="menuitem" onClick={() => { setExtensionMenuOpen(false); setExtensionNotice("Package installation requires a host-approved plugin adapter."); }}>Install package</button><button type="button" role="menuitem" onClick={() => { setExtensionMenuOpen(false); setExtensionNotice("Loading local plugins requires a host-approved plugin adapter."); }}>Load local plugin</button><button type="button" role="menuitem" onClick={() => { setExtensionMenuOpen(false); setExtensionNotice("Plugin templates are not connected to the local host yet."); }}>New plugin from template</button></div>}</span>
          </div>
        </header>
        {extensionNotice && <div className="extension-notice" role="status">{extensionNotice}</div>}
        <div className="extension-ready-banner">
          <div><span className="ready-mark"><Icon name="check" size={16} /></span><strong>{installedCards.filter(([, , status]) => status === "Connected").length} local skill(s) ready</strong></div>
          <button type="button" onClick={() => openDestination("settings")}>Review setup</button>
        </div>
        <div className="extension-toolbar">
          <div className="extension-tabs" role="tablist" aria-label="Extension views"><button className={extensionView === "installed" ? "active" : ""} type="button" role="tab" aria-selected={extensionView === "installed"} onClick={() => { setExtensionView("installed"); setSelectedSkill(null); }}>Installed <span>{installedCards.length}</span></button><button className={extensionView === "marketplace" ? "active" : ""} type="button" role="tab" aria-selected={extensionView === "marketplace"} onClick={() => { setExtensionView("marketplace"); setSelectedSkill(null); }}>Marketplace <span>{marketplaceCards.length}</span></button></div>
          <label className="extension-search"><Icon name="search" size={14} /><input value={extensionQuery} onChange={(event) => setExtensionQuery(event.target.value)} placeholder={extensionView === "installed" ? "Search installed plugins" : "Search plugins"} /></label>
        </div>
        {extensionView === "marketplace" && <><div className="extension-marketplace-bar"><strong>Extension marketplace</strong><button type="button" onClick={() => openDestination("settings")}><span>AEGIS local catalog</span><Icon name="chevron-down" size={14} /></button></div><div className="extension-categories"><button className="active" type="button">All</button><button type="button">research</button><button type="button">editing</button><button type="button">productivity</button></div></>}
        {selected && <article className="skill-detail"><div><span className="eyebrow">CAPABILITY DETAILS</span><h2>{selected[0]}</h2><p>{selected[1]}</p><small>{selected[2]} · Host boundary enforced</small></div><button type="button" onClick={() => setSelectedSkill(null)} aria-label="Close skill details">Close</button></article>}
        {extensionView === "installed" ? <div className="extension-installed-list">
          {setupCards.length > 0 && <section className="extension-installed-group"><div className="extension-installed-group-label">NEEDS ATTENTION <span>{setupCards.length}</span></div>{setupCards.map(renderInstalledRow)}</section>}
          {readyCards.length > 0 && <section className="extension-installed-group"><div className="extension-installed-group-label">ACTIVE <span>{readyCards.length}</span></div>{readyCards.map(renderInstalledRow)}</section>}
          {visibleCards.length === 0 && <div className="extension-installed-empty">No matching plugins</div>}
        </div> : <div className="extension-grid">{visibleCards.map(renderExtensionCard)}</div>}
      </main>
    );
  }

  function renderDestination() {
    const isPulls = destination === "pulls";
    const isScheduled = destination === "scheduled";
    const emptyCounts = { open: 0, draft: 0, all: 0 };
    if (destination === "plugins") return renderExtensions();
    return (
      <main className="destination-page pi-route-page">
        <div className="page-frame pi-destination-frame">
          <header className="page-header pi-route-header">
            <div><h1 className="page-title">{isPulls ? "Pull requests" : "Scheduled"}</h1></div>
            <div className="pi-route-actions">
              {isPulls && <button className="pi-route-refresh" type="button" onClick={() => setCapabilityNotice("Pull request adapter is not connected to the local host yet.")}><Icon name="refresh" size={14} />Refresh</button>}
              {isPulls && <button className="pi-route-review" type="button" onClick={() => { setDestination("chat"); setMessage("List open pull requests and current branch status for this repository. Summarize what needs review."); }}>Review</button>}
            </div>
          </header>
          {isPulls && <div className="dest-toolbar">
            <div className="dest-filters" role="tablist" aria-label="Pull request filters">
              {([['open', 'Open'], ['draft', 'Draft'], ['all', 'All']] as const).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={pullFilter === id} className={`dest-filter ${pullFilter === id ? "active" : ""}`} onClick={() => setPullFilter(id)}>{label}<span>{emptyCounts[id]}</span></button>)}
            </div>
          </div>}
          {isScheduled && <section className="dest-create pi-scheduled-create">
            <div className="pi-route-create-heading">Create task</div>
            <label><span>New task</span><input value={scheduledTitle} onChange={(event) => setScheduledTitle(event.target.value)} placeholder="New task" /></label>
            <label><span>Prompt</span><textarea rows={3} value={scheduledPrompt} onChange={(event) => setScheduledPrompt(event.target.value)} placeholder="e.g. Summarize git status and open issues every morning" /></label>
            <div className="pi-scheduled-create-actions"><label className="pi-cadence-field"><span>Cadence</span><select value={scheduledCadence} onChange={(event) => setScheduledCadence(event.target.value as typeof scheduledCadence)}><option value="manual">Manual</option><option value="hourly">Hourly</option><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label><button className="pi-route-review" type="button" disabled={!scheduledPrompt.trim()} onClick={() => setCapabilityNotice("Scheduled-task adapter is not connected to the local host yet.")}>Create task</button></div>
          </section>}
          {isScheduled && <div className="dest-section-label">Tasks</div>}
          <section className="page-card page-empty pi-route-empty" aria-live="polite">
            <span className="page-empty-icon"><Icon name={isPulls ? "branch" : "clock"} size={20} /></span>
            <div className="pi-route-empty-title">{isPulls ? "No pull requests" : "No scheduled tasks"}</div>
            {isPulls ? <><p>Connect the repository adapter to inspect pull requests here.</p><button className="pi-route-open" type="button" onClick={() => { setDestination("chat"); setWorkTab("files"); setWorkPanelOpen(true); }}>Open project</button></> : null}
          </section>
          {capabilityNotice && <div className="capability-settings-notice" role="status">{capabilityNotice}</div>}
        </div>
      </main>
    );
  }

  const shellStyle = { "--pi-sidebar-width": `${sidebarWidth}px`, "--work-width": `${workPanelWidth}px` } as CSSProperties;
  return <div style={shellStyle} className={`app-frame ${destination !== "chat" ? "destination-frame" : ""} ${destination === "settings" ? "settings-frame" : ""} ${sidebarCollapsed ? "sidebar-is-collapsed" : ""} ${workPanelOpen && destination === "chat" ? "work-panel-open" : ""}`}>
    <WindowControls />
    {destination !== "settings" && !sidebarCollapsed && renderSidebar()}
     {destination === "chat" ? <>{renderConversation()}{workPanelOpen && renderWorkPanel()}{!workPanelOpen && <button className="app-work-panel-toggle no-drag" type="button" onClick={() => setWorkPanelOpen(true)} aria-label="Toggle work panel" aria-expanded={false} title="Toggle work panel"><Icon name="panel" size={15} /></button>}</> : destination === "settings" ? renderSettings() : destination === "extensions" || destination === "plugins" ? renderExtensions() : renderDestination()}
    {renderSearchOverlay()}
  </div>;
}
