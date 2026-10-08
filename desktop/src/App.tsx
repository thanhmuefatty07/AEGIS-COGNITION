import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties, type ClipboardEvent as ReactClipboardEvent, type DragEvent as ReactDragEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ThinkingOrb } from "thinking-orbs";
import { invoke } from "@tauri-apps/api/core";
import {
  desktopRequest,
  parseExtensionCatalog,
  parseMcpActivation,
  parseMcpMetadata,
  parseMcpCatalog,
  parseMcpRegistrySearch,
  parseMcpPromptsList,
  parseMcpPromptResult,
  parseMcpSkillsList,
  parseMcpSkillState,
  parseMcpTest,
  parseConnectionList,
  parseConnectionConnectResult,
  parseConnectionRecordResult,
  parseCapabilityState,
  parseProjectList,
  parseProjectRemove,
  parseConversationList,
  parseConversationInspection,
  parseConversationSendResult,
  parseDesktopRunStatus,
  parseApprovalResolutionResult,
  parseToolCallReconciliationResult,
  parseConversationRecordResult,
  parseConversationSnapshot,
  parseMemorySearchResult,
  parseModelDiscoveryResult,
  parseSourceSnapshot,
  parseWorkspaceChangesResult,
  parseWorkspaceDiffResult,
  parseWorkspaceFileResult,
  parseWorkspaceOpenExternalResult,
  parseSettingsResult,
  parseSkillLoadResult,
  parseSubagentCancelResult,
  parseSubagentEventPage,
  parseSubagentGraph,
  parseSubagentRunResult,
  parseSubagentStartResult,
  parseSubagentStatusResult,
  parseWorkspaceCloneResult,
  parseWorkspaceSnapshot,
  type ExtensionCatalogResult,
  type McpCatalogResult,
  type McpRegistryEnvironmentVariable,
  type McpRegistrySearchResult,
  type McpRegistryServer,
  type McpServerSummary,
  type McpTestResult,
  type McpPromptDescriptor,
  type McpPromptResult,
  type McpSkillsListResult,
  type McpSkillSummary,
  type McpSkillStateResult,
  type ProjectRecord,
  type SkillLoadResult,
  type Conversation,
  type DesktopRunStatus,
  type ConversationInspection,
  type PromptCacheUsage,
  type ConversationSnapshot,
  type ConnectionConnectResult,
  type ConnectionRecord,
  type ProviderChoice,
  type ModelDescriptor,
  type ModelCatalogState,
  type MemoryRecord,
  type SourceSnapshot,
  type WorkspaceChangesResult,
  type WorkspaceDiffResult,
  type WorkspaceFileResult,
  type SubagentCancelResult,
  type SubagentEvent,
  type SubagentGraph,
  type SubagentRunResult,
  type SubagentStartResult,
  type SubagentStatusResult,
  type WorkspaceSnapshot,
  type DesktopSettings,
  type CapabilityStateResult,
} from "./protocol";
import { deriveTaskState, interruptedDesktopRunStatus, isProjectSwitchBlocked, isTaskRunActive, shouldDrainTaskQueue, taskStateLabel } from "./task_state.js";
import { resolveWorkbenchShortcut, workbenchCommands } from "./command_registry.js";
import VirtualizedTimeline from "./VirtualizedTimeline";
import type { WorkspaceGraph } from "./workspace_graph";
import { CapabilityIcon } from "./capability-icons";
import AmbiguousToolCallReconciliation from "./AmbiguousToolCallReconciliation";
import { clipboardImageFiles } from "./clipboard_images.js";
import { findModelForConnectionAndId } from "./model_selection.js";
import { parseExtensionPackImport } from "./mcp_setup_suggestions.js";
import {
  findMcpRegistrySetupOption,
  getMcpRegistrySetupOptions,
  getSelectedMcpAutoRunHashes,
  missingRequiredEnvironment,
} from "./mcp_registry_selection.js";
import aegisLogoUrl from "./assets/aegis-icon.svg";

type Destination = "chat" | "settings" | "plugins";
type WorkTab = "files" | "changes" | "map" | "activity" | "context";
type Theme = "system" | "dark" | "light";
type ThinkingMode = DesktopSettings["thinking_default"];
type PendingImage = {
  id: string;
  name: string;
  mimeType: string;
  data: string;
  dataUrl: string;
  sizeBytes: number;
};
type QueuedPrompt = {
  id: string;
  conversationId: string;
  message: string;
  mode: "mock" | "live";
  reasoningEffort: string;
  attachments: PendingImage[];
};

const SUPPORTED_IMAGE_MIME_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const isMacOS = typeof navigator !== "undefined" && /mac/i.test(`${navigator.platform} ${navigator.userAgent}`);
const primaryShortcutPrefix = isMacOS ? "⌘" : "Ctrl+";
const WorkspaceGraphView = lazy(() => import("./WorkspaceGraphView"));
const SHORTCUT_DESCRIPTIONS: Record<string, string> = {
  "command-palette": "Search available workbench actions.",
  "quick-open": "Find a task, file, or settings page.",
  "new-task": "Start a separate task in this project.",
  "toggle-navigator": "Show or hide task navigation.",
  "find-in-project": "Search the project file list.",
  send: "Send or queue the composer message.",
  "stop-task": "Request a safe stop for the active task.",
  preferences: "Open application settings.",
  "focus-next-pane": "Move focus to the next workbench area.",
  "focus-previous-pane": "Move focus to the previous workbench area.",
};

function hasPrimaryModifier(event: Pick<KeyboardEvent, "ctrlKey" | "metaKey">) {
  return isMacOS ? event.metaKey : event.ctrlKey;
}

const REASONING_LABELS: Record<string, string> = {
  none: "None",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Xhigh",
  max: "Max",
};
const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  deepseek: "DeepSeek",
  groq: "Groq",
  "google-gemini": "Google Gemini",
  openai: "OpenAI",
  "openai-compatible": "Compatible provider",
  openrouter: "OpenRouter",
  perplexity: "Perplexity",
  "nvidia-nim": "NVIDIA NIM",
  xai: "xAI",
};

function reasoningChoices(model: ModelDescriptor | undefined): ThinkingMode[] {
  return ["Auto", ...new Set(model?.reasoning_efforts ?? [])];
}

function reasoningSelectionForModel(model: ModelDescriptor | undefined, value: ThinkingMode): ThinkingMode {
  if (value === "Auto" || value.toLowerCase() === "auto") return "Auto";
  const supported = model?.reasoning_efforts ?? [];
  return supported.find((level) => level === value)
    ?? supported.find((level) => level.toLowerCase() === value.toLowerCase())
    ?? "Auto";
}

function reasoningLabel(value: ThinkingMode): string {
  return REASONING_LABELS[value.toLowerCase()] ?? value;
}

function providerLabelForKind(providerKind: string): string {
  return PROVIDER_LABELS[providerKind.toLowerCase()] ?? providerKind;
}
const defaultEndpoint = "http://127.0.0.1:8080/v1";
const SIDEBAR_WIDTH_MIN = 240;
const SIDEBAR_WIDTH_DEFAULT = 343;
const SIDEBAR_WIDTH_MAX = 520;
const SIDEBAR_COLLAPSE_THRESHOLD = 160;
const SIDEBAR_RESIZE_STEP = 16;
const WORK_PANEL_WIDTH_MIN = 280;
const WORK_PANEL_WIDTH_DEFAULT = 320;
const WORK_PANEL_WIDTH_MAX = 400;
const WORK_PANEL_OVERLAY_BREAKPOINT = 1180;
const MODEL_CATALOG_REFRESH_INTERVAL_MS = 60_000;

async function loadConnectedModelCatalogs(connections: ConnectionRecord[]): Promise<{
  models: ModelDescriptor[];
  failedConnectionIds: string[];
  catalogStates: Map<string, ModelCatalogState>;
}> {
  const models: ModelDescriptor[] = [];
  const failedConnectionIds: string[] = [];
  const catalogStates = new Map<string, ModelCatalogState>();
  const concurrency = 4;
  for (let offset = 0; offset < connections.length; offset += concurrency) {
    const batch = connections.slice(offset, offset + concurrency);
    const results = await Promise.all(batch.map(async (connection) => {
      try {
        const catalog = await desktopRequest(
          "connections.discover",
          { connection_id: connection.connection_id },
          parseModelDiscoveryResult,
        );
        return {
          connectionId: connection.connection_id,
          models: catalog.models,
          state: catalog.model_catalog_state,
          failed: catalog.discovery_error !== null,
        };
      } catch {
        return { connectionId: connection.connection_id, models: null, state: "UNAVAILABLE" as const, failed: true };
      }
    }));
    for (const result of results) {
      catalogStates.set(result.connectionId, result.state);
      if (result.models !== null) models.push(...result.models);
      if (result.failed) failedConnectionIds.push(result.connectionId);
    }
  }
  return { models, failedConnectionIds, catalogStates };
}

// Keep the rail comfortable at small desktop sizes while preserving the
// reference proportion on large displays.
function responsiveSidebarMax(viewportWidth: number) {
  return Math.round(Math.max(SIDEBAR_WIDTH_MIN, Math.min(343, viewportWidth * 0.18)));
}

type SessionSort = "recent" | "oldest" | "name" | "created";

type McpDraft = {
  command: string;
  cwd: string;
  environment: string;
  endpoint: string;
  allowedHosts: string;
  headers: string;
  oauthClientId: string;
};

const emptyMcpDraft: McpDraft = {
  command: "",
  cwd: "",
  environment: "{}",
  endpoint: "",
  allowedHosts: "",
  headers: "{}",
  oauthClientId: "",
};

function hasNativeWindow() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function shortPath(value: string | null | undefined, length = 32) {
  if (!value) return "—";
  return value.length > length ? `…${value.slice(-length + 1)}` : value;
}

function describeError(reason: unknown, fallback: string) {
  if (reason instanceof Error && reason.message.trim()) return reason.message;
  if (typeof reason === "string" && reason.trim()) return reason;
  if (typeof reason === "object" && reason !== null && "message" in reason) {
    const message = (reason as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return fallback;
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

function isUnstartedConversation(conversation: Conversation) {
  return conversation.revision === 1;
}

const TOPBAR_TITLE_MAX_LENGTH = 48;

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
  | "mic"
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

function AegisBrandLogo() {
  const [assetFailed, setAssetFailed] = useState(false);
  if (assetFailed) {
    return <span className="brand-logo aegis-brand-logo-fallback" aria-hidden="true">A</span>;
  }
  return <img className="brand-logo aegis-brand-logo" src={aegisLogoUrl} alt="" width="22" height="21" aria-hidden="true" draggable={false} onError={() => setAssetFailed(true)} />;
}

type SettingsSelectOption = {
  value: string;
  label: string;
  description?: string;
};

function SettingsSelect({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: string;
  options: SettingsSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const anchorRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const selected = options[selectedIndex];

  useEffect(() => {
    if (!open) return;
    setActiveIndex(selectedIndex);
    optionRefs.current[selectedIndex]?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!anchorRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      } else if (event.key === "Tab") {
        setOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, selectedIndex]);

  return (
    <div className="settings-select-anchor" ref={anchorRef}>
      <button
        ref={triggerRef}
        className="aegis-settings-select-value"
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onKeyDown={(event) => {
          if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
            event.preventDefault();
            setOpen(true);
          }
        }}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{selected?.label ?? value}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <div className="settings-select-menu" role="listbox" aria-label={ariaLabel} onKeyDown={(event) => {
          if (options.length === 0) return;
          let nextIndex: number | null = null;
          if (event.key === "ArrowDown") nextIndex = Math.min(options.length - 1, activeIndex + 1);
          else if (event.key === "ArrowUp") nextIndex = Math.max(0, activeIndex - 1);
          else if (event.key === "Home") nextIndex = 0;
          else if (event.key === "End") nextIndex = options.length - 1;
          if (nextIndex !== null) {
            event.preventDefault();
            setActiveIndex(nextIndex);
            optionRefs.current[nextIndex]?.focus();
          }
        }}>
          {options.map((option, index) => (
            <button
              ref={(element) => { optionRefs.current[index] = element; }}
              key={option.value}
              type="button"
              role="option"
              tabIndex={activeIndex === index ? 0 : -1}
              aria-selected={option.value === value}
              className={option.value === value ? "active" : ""}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
                triggerRef.current?.focus();
              }}
            >
              <span className="settings-select-option-copy">
                <strong>{option.label}</strong>
                {option.description && <small>{option.description}</small>}
              </span>
              {option.value === value && <Icon name="check" size={13} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type ComposerInputProps = {
  value: string;
  placeholder: string;
  disabled: boolean;
  enterToSend: boolean;
  onChange: (value: string) => void;
  onKeyDown?: (event: ReactKeyboardEvent<HTMLDivElement>) => boolean;
  onImageFiles?: (files: File[]) => void;
  onSubmit: () => void;
};

/** Keep the AEGIS send callback at the composer boundary. */
function ComposerInput({ value, placeholder, disabled, enterToSend, onChange, onKeyDown, onImageFiles, onSubmit }: ComposerInputProps) {
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
          data-workbench-pane="composer"
          role="textbox"
          aria-label="Message"
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
          onPaste={(event: ReactClipboardEvent<HTMLDivElement>) => {
            const files = clipboardImageFiles(event.clipboardData);
            if (files.length > 0) {
              event.preventDefault();
              onImageFiles?.(files);
              const pastedText = event.clipboardData.getData("text/plain");
              if (pastedText) {
                // Read the DOM value at paste time. React state can lag one
                // input event behind in a contentEditable surface, and using
                // the captured prop could overwrite text typed immediately
                // before the image was pasted.
                const currentText = event.currentTarget.textContent ?? value;
                const separator = currentText.length > 0 && !/\s$/.test(currentText) ? "\n" : "";
                onChange(`${currentText}${separator}${pastedText}`);
              }
            }
          }}
          onDragOver={(event: ReactDragEvent<HTMLDivElement>) => {
            if (Array.from(event.dataTransfer.items).some((item) => item.type.startsWith("image/"))) event.preventDefault();
          }}
          onDrop={(event: ReactDragEvent<HTMLDivElement>) => {
            const files = Array.from(event.dataTransfer.files).filter((file) => file.type.startsWith("image/"));
            if (files.length > 0) {
              event.preventDefault();
              onImageFiles?.(files);
            }
          }}
          onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => {
            if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
            if (onKeyDown?.(event)) return;
            const submit = event.key === "Enter" && !event.shiftKey && (enterToSend || hasPrimaryModifier(event));
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

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceSnapshot | null>(null);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [workspaceGraph, setWorkspaceGraph] = useState<WorkspaceGraph | null>(null);
  const [sourceSnapshot, setSourceSnapshot] = useState<SourceSnapshot | null>(null);
  const [selectedFileContent, setSelectedFileContent] = useState<WorkspaceFileResult | null>(null);
  const [fileReadError, setFileReadError] = useState<string | null>(null);
  const [fileOpenNotice, setFileOpenNotice] = useState<string | null>(null);
  const [workspaceChanges, setWorkspaceChanges] = useState<WorkspaceChangesResult | null>(null);
  const [changesRefreshKey, setChangesRefreshKey] = useState(0);
  const [selectedChangePath, setSelectedChangePath] = useState<string | null>(null);
  const [selectedChangeDiff, setSelectedChangeDiff] = useState<WorkspaceDiffResult | null>(null);
  const [changesError, setChangesError] = useState<string | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationSnapshot | null>(null);
  const [conversationInspection, setConversationInspection] = useState<ConversationInspection | null>(null);
  const [message, setMessage] = useState("");
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [imageProcessing, setImageProcessing] = useState(false);
  const imageProcessingRef = useRef(false);
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [endpoint, setEndpoint] = useState(defaultEndpoint);
  const [modelId, setModelId] = useState("local-model");
  const [connectionSaved, setConnectionSaved] = useState(false);
  const [connectionMode, setConnectionMode] = useState<"local" | "api-key">("local");
  const [connectedProviders, setConnectedProviders] = useState<ConnectionRecord[]>([]);
  const [activeConnectionId, setActiveConnectionId] = useState<string | null>(null);
  const [disconnectConfirmationId, setDisconnectConfirmationId] = useState<string | null>(null);
  const [apiKeyDraft, setApiKeyDraft] = useState("");
  const [customProviderEndpoint, setCustomProviderEndpoint] = useState("");
  const [providerChoices, setProviderChoices] = useState<ProviderChoice[]>([]);
  const [selectedProviderKind, setSelectedProviderKind] = useState("");
  const [connectionBusy, setConnectionBusy] = useState<"connect" | "disconnect" | null>(null);
  const [connectionNotice, setConnectionNotice] = useState<string | null>(null);
  const selectedProvider = providerChoices.find((item) => item.provider_kind === selectedProviderKind) ?? null;
  const [destination, setDestination] = useState<Destination>("chat");
  const [workTab, setWorkTab] = useState<WorkTab>("files");
  const [workPanelOpen, setWorkPanelOpen] = useState(false);
  const [workPanelWidth, setWorkPanelWidth] = useState(() => {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem("aegis-work-panel-width");
    const parsed = stored ? Number(stored) : NaN;
    return Number.isFinite(parsed) ? Math.max(WORK_PANEL_WIDTH_MIN, Math.min(WORK_PANEL_WIDTH_MAX, Math.round(parsed))) : WORK_PANEL_WIDTH_DEFAULT;
  });
  const [workPanelResizing, setWorkPanelResizing] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth <= 1100);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem("aegis-sidebar-width");
    const parsed = stored ? Number(stored) : NaN;
    const viewport = typeof window === "undefined" ? 1920 : window.innerWidth;
    const responsiveMax = responsiveSidebarMax(viewport);
    return Number.isFinite(parsed) ? clampSidebarWidth(parsed, responsiveMax) : Math.min(SIDEBAR_WIDTH_DEFAULT, responsiveMax);
  });
  const [viewportWidth, setViewportWidth] = useState(() => typeof window === "undefined" ? 1280 : window.innerWidth);
  const workPanelVisible = destination === "chat" && workPanelOpen;
  const workPanelModal = workPanelVisible && viewportWidth <= WORK_PANEL_OVERLAY_BREAKPOINT;
  const [sidebarResizing, setSidebarResizing] = useState(false);
  const [sessionSort, setSessionSort] = useState<SessionSort>("recent");
  const [sessionSortOpen, setSessionSortOpen] = useState(false);
  const [sessionMenuOpen, setSessionMenuOpen] = useState<string | null>(null);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [homeActionsOpen, setHomeActionsOpen] = useState(false);
  const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
  const [projectSwitcherPosition, setProjectSwitcherPosition] = useState({ left: 0, top: 0 });
  const [projectSwitcherQuery, setProjectSwitcherQuery] = useState("");
  const [projectSwitcherView, setProjectSwitcherView] = useState<"list" | "clone">("list");
  const [projectCloneUrl, setProjectCloneUrl] = useState("");
  const [projectSwitcherBusy, setProjectSwitcherBusy] = useState(false);
  const [fileFilter, setFileFilter] = useState("");
  const [settingsSection, setSettingsSection] = useState("General");
  const [settingsQuery, setSettingsQuery] = useState("");
  const [fontSizePreset, setFontSizePreset] = useState<"Tall" | "Grande" | "Venti" | "Trenta">("Grande");
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>("Auto");
  const [contextUsage, setContextUsage] = useState<"Remaining" | "Used">("Remaining");
  const [permissionMode, setPermissionMode] = useState<DesktopSettings["permission_mode"]>("Ask every time");
  const [enterToSend, setEnterToSend] = useState(true);
  const [projectSort, setProjectSort] = useState<"Recent" | "Name">("Recent");
  const [projectFilter, setProjectFilter] = useState("");
  const [composerMenuOpen, setComposerMenuOpen] = useState<"permission" | "model" | null>(null);
  const [composerModelView, setComposerModelView] = useState<"root" | "model" | "thinking">("root");
  const [composerModelQuery, setComposerModelQuery] = useState("");
  const [composerAutocompleteMode, setComposerAutocompleteMode] = useState<ComposerAutocompleteMode | null>(null);
  const [composerAutocompleteQuery, setComposerAutocompleteQuery] = useState("");
  const [composerAutocompleteHighlight, setComposerAutocompleteHighlight] = useState(0);
  const [availableModels, setAvailableModels] = useState<ModelDescriptor[]>([]);
  const [modelCatalogState, setModelCatalogState] = useState<ModelCatalogState>("UNAVAILABLE");
  const [lastPromptCacheUsage, setLastPromptCacheUsage] = useState<PromptCacheUsage | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [capabilityQuery, setCapabilityQuery] = useState("");
  const [instructionsDraft, setInstructionsDraft] = useState("Use the local workspace context, preserve evidence boundaries, and report uncertainty explicitly.");
  const [instructionsNotice, setInstructionsNotice] = useState<string | null>(null);
  const [capabilityNotice, setCapabilityNotice] = useState<string | null>(null);
  const [parallelWorkersEnabled, setParallelWorkersEnabled] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [commandSearchQuery, setCommandSearchQuery] = useState("");
  const [commandHighlight, setCommandHighlight] = useState(0);
  const [workbenchV2Enabled, setWorkbenchV2Enabled] = useState(() => (
    typeof window === "undefined" || window.localStorage.getItem("aegis-workbench-v2") !== "disabled"
  ));
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem("aegis-theme");
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
  });
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [extensionQuery, setExtensionQuery] = useState("");
  const [extensionNotice, setExtensionNotice] = useState<string | null>(null);
  const [extensionCatalog, setExtensionCatalog] = useState<ExtensionCatalogResult | null>(null);
  const [mcpCatalog, setMcpCatalog] = useState<McpCatalogResult | null>(null);
  const [mcpRegistryQuery, setMcpRegistryQuery] = useState("");
  const [mcpRegistryResults, setMcpRegistryResults] = useState<McpRegistrySearchResult | null>(null);
  const [mcpRegistrySelection, setMcpRegistrySelection] = useState<McpRegistryServer | null>(null);
  const [mcpRegistryChoice, setMcpRegistryChoice] = useState("");
  const [mcpRegistryHints, setMcpRegistryHints] = useState<Record<string, string>>({});
  const [mcpRegistryEnvironmentVariables, setMcpRegistryEnvironmentVariables] = useState<Record<string, McpRegistryEnvironmentVariable[]>>({});
  const [mcpRegistryEnvironmentValues, setMcpRegistryEnvironmentValues] = useState<Record<string, Record<string, string>>>({});
  const [mcpRegistryBusy, setMcpRegistryBusy] = useState(false);
  const [mcpDrafts, setMcpDrafts] = useState<Record<string, McpDraft>>({});
  const [mcpImportedSetupDrafts, setMcpImportedSetupDrafts] = useState<Record<string, boolean>>({});
  const [mcpActivationBusy, setMcpActivationBusy] = useState<string | null>(null);
  const [mcpTestBusy, setMcpTestBusy] = useState<string | null>(null);
  const [mcpTestResults, setMcpTestResults] = useState<Record<string, McpTestResult>>({});
  const [mcpAutoRunReadOnly, setMcpAutoRunReadOnly] = useState<Record<string, Record<string, boolean>>>({});
  const [mcpPromptCatalogs, setMcpPromptCatalogs] = useState<Record<string, { prompts: McpPromptDescriptor[] }>>({});
  const [mcpPromptArguments, setMcpPromptArguments] = useState<Record<string, Record<string, Record<string, string>>>>({});
  const [mcpPromptResults, setMcpPromptResults] = useState<Record<string, Record<string, McpPromptResult>>>({});
  const [mcpPromptsBusy, setMcpPromptsBusy] = useState<string | null>(null);
  const [mcpSkillCatalogs, setMcpSkillCatalogs] = useState<Record<string, McpSkillsListResult>>({});
  const [mcpSkillsBusy, setMcpSkillsBusy] = useState<string | null>(null);
  const [mcpSkillBusyId, setMcpSkillBusyId] = useState<string | null>(null);
  const [mcpAddOpen, setMcpAddOpen] = useState(false);
  const [mcpNewId, setMcpNewId] = useState("");
  const [mcpNewDescription, setMcpNewDescription] = useState("");
  const [mcpNewTransport, setMcpNewTransport] = useState<"stdio" | "streamable-http">("stdio");
  const [capabilityState, setCapabilityState] = useState<CapabilityStateResult | null>(null);
  const [extensionBusy, setExtensionBusy] = useState(false);
  const [selectedSkillBody, setSelectedSkillBody] = useState<string | null>(null);
  const [skillLoadBusy, setSkillLoadBusy] = useState(false);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [reconciliationNotice, setReconciliationNotice] = useState<string | null>(null);
  const [subagentBusy, setSubagentBusy] = useState(false);
  const conversationActionBusy = busy || subagentBusy || approvalBusyId !== null;
  const [subagentResult, setSubagentResult] = useState<SubagentRunResult | null>(null);
  const [subagentRunId, setSubagentRunId] = useState<string | null>(null);
  const [subagentStatus, setSubagentStatus] = useState<SubagentStatusResult | null>(null);
  const [subagentEvents, setSubagentEvents] = useState<SubagentEvent[]>([]);
  const [subagentGraph, setSubagentGraph] = useState<SubagentGraph | null>(null);
  const [desktopRunStatuses, setDesktopRunStatuses] = useState<Record<string, DesktopRunStatus>>({});
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);
  const [queuedPromptsPaused, setQueuedPromptsPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasPendingToolApproval = conversationInspection?.approvals.some(
    (approval) => approval.status === "REQUESTED" || approval.status === "AMBIGUOUS",
  ) ?? false;
  const activeDesktopRun = Object.values(desktopRunStatuses).find((run) => isTaskRunActive(run.status)) ?? null;
  const projectSwitchBlocked = isProjectSwitchBlocked(conversationActionBusy, activeDesktopRun !== null);
  const selectedDesktopRun = activeConversationId ? desktopRunStatuses[activeConversationId] ?? null : null;
  const selectedTaskState = deriveTaskState(conversation, selectedDesktopRun, hasPendingToolApproval);
  const activeDesktopRunIds = Object.values(desktopRunStatuses)
    .filter((run) => isTaskRunActive(run.status))
    .map((run) => run.run_id)
    .sort()
    .join(",");
  const searchRef = useRef<HTMLInputElement>(null);
  const globalSearchRef = useRef<HTMLInputElement>(null);
  const commandPaletteInputRef = useRef<HTMLInputElement>(null);
  const commandPaletteOpenerRef = useRef<HTMLElement | null>(null);
  const projectSwitcherInputRef = useRef<HTMLInputElement>(null);
  const projectSwitcherTriggerRef = useRef<HTMLButtonElement>(null);
  const projectSwitcherOpenerRef = useRef<HTMLElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const settingsRevisionRef = useRef(0);
  const settingsUpdateQueueRef = useRef<Promise<void>>(Promise.resolve());
  const bootstrapStarted = useRef(false);
  const catalogRefreshInFlightRef = useRef(false);
  const conversationLoadRevision = useRef(0);
  const graphLoadRevision = useRef(0);
  const workPanelRef = useRef<HTMLElement>(null);
  const fileReadRevision = useRef(0);
  const changesListRevision = useRef(0);
  const changesDiffRevision = useRef(0);
  const runQueueRef = useRef<QueuedPrompt[]>([]);
  const runQueueDrainRef = useRef(false);
  const activeConversationIdRef = useRef(activeConversationId);
  activeConversationIdRef.current = activeConversationId;
  const sidebarResizeRef = useRef<{ pointerId: number; startX: number; startWidth: number; currentWidth: number; handle: HTMLDivElement } | null>(null);
  const workPanelResizeRef = useRef<{ pointerId: number; startX: number; startWidth: number; currentWidth: number; handle: HTMLDivElement } | null>(null);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    void bootstrap();
  }, []);

  useEffect(() => {
    if (!connectionSaved || connectedProviders.length === 0) return;
    let current = true;
    const refreshStaleCatalogs = async () => {
      if (!current
        || document.visibilityState === "hidden"
        || connectionBusy !== null
        || busy
        || catalogRefreshInFlightRef.current) return;
      catalogRefreshInFlightRef.current = true;
      try {
        const { models, catalogStates } = await loadConnectedModelCatalogs(connectedProviders);
        if (!current) return;
        setAvailableModels(models);
        const selectedConnectionId = conversation?.conversation.connection_id ?? activeConnectionId;
        const selectedModelId = conversation?.conversation.model_id ?? modelId;
        const selectedModelStillAvailable = selectedConnectionId !== null
          && models.some((item) => item.connection_id === selectedConnectionId && item.model_id === selectedModelId);
        if (mode === "live" && selectedConnectionId !== null && !selectedModelStillAvailable) {
          setMode("mock");
          setConnectionNotice("The selected model is no longer available. Live mode was paused; choose an available model.");
        }
        setModelCatalogState(
          catalogStates.get(activeConnectionId ?? "") ?? (models.length > 0 ? "CACHED" : "UNAVAILABLE"),
        );
      } finally {
        catalogRefreshInFlightRef.current = false;
      }
    };
    const timer = window.setInterval(() => void refreshStaleCatalogs(), MODEL_CATALOG_REFRESH_INTERVAL_MS);
    window.addEventListener("focus", refreshStaleCatalogs);
    document.addEventListener("visibilitychange", refreshStaleCatalogs);
    return () => {
      current = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshStaleCatalogs);
      document.removeEventListener("visibilitychange", refreshStaleCatalogs);
    };
  }, [connectionSaved, connectedProviders, activeConnectionId, connectionBusy, busy, conversation?.conversation.connection_id, conversation?.conversation.model_id, modelId, mode]);

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
    document.documentElement.dataset.workbenchV2 = String(workbenchV2Enabled);
    window.localStorage.setItem("aegis-workbench-v2", workbenchV2Enabled ? "enabled" : "disabled");
  }, [workbenchV2Enabled]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        setCommandPaletteOpen(false);
        setSearchOpen(false);
        setSessionSortOpen(false);
        setSessionMenuOpen(null);
        setProjectMenuOpen(false);
        setProjectSwitcherOpen(false);
        setComposerMenuOpen(null);
        setComposerModelView("root");
        setComposerModelQuery("");
        setComposerAutocompleteMode(null);
        setComposerAutocompleteQuery("");
        searchRef.current?.blur();
        globalSearchRef.current?.blur();
        return;
      }
      const command = workbenchV2Enabled
        ? resolveWorkbenchShortcut(event, navigator.platform + " " + navigator.userAgent)
        : null;
      if (command) {
        event.preventDefault();
        switch (command) {
          case "command-palette":
            openCommandPalette();
            break;
          case "quick-open":
            openQuickOpen();
            break;
          case "new-task":
            startNewTask();
            break;
          case "toggle-navigator":
            setSidebarCollapsed((current) => !current);
            break;
          case "find-in-project":
            openDestination("chat");
            setWorkTab("files");
            setWorkPanelOpen(true);
            window.requestAnimationFrame(() => searchRef.current?.focus());
            break;
          case "send":
            void sendMessage();
            break;
          case "stop-task":
            if (activeDesktopRun) void cancelDesktopRun(activeDesktopRun);
            else if (subagentRunId) void cancelSubagents();
            break;
          case "preferences":
            openDestination("settings");
            break;
          case "focus-next-pane":
            moveFocusToWorkbenchPane(false);
            break;
          case "focus-previous-pane":
            moveFocusToWorkbenchPane(true);
            break;
        }
        return;
      }
      if (!hasPrimaryModifier(event)) return;
      const key = event.key.toLowerCase();
      if (key === "k") {
        openQuickOpen();
      } else if (key === "j") {
        event.preventDefault();
        setDestination("chat");
        setWorkPanelOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [activeDesktopRun, subagentRunId, workbenchV2Enabled]);

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!activeDesktopRunIds) return;
    let current = true;
    let timer = 0;
    const runs = Object.values(desktopRunStatuses).filter((run) => isTaskRunActive(run.status));
    const poll = async () => {
      let stillActive = false;
      const terminalStatuses: DesktopRunStatus[] = [];
      for (const run of runs) {
        if (!current) return;
        try {
          const status = await desktopRequest("runs.inspect", {
            run_id: run.run_id,
            conversation_id: run.conversation_id,
          }, parseDesktopRunStatus);
          if (!current) return;
          if (isTaskRunActive(status.status)) {
            setDesktopRunStatuses((previous) => ({ ...previous, [status.conversation_id]: status }));
            stillActive = true;
            continue;
          }
          terminalStatuses.push(status);
          const [snapshot, inspection] = await Promise.all([
            desktopRequest("conversations.read", { conversation_id: run.conversation_id }, parseConversationSnapshot),
            desktopRequest("conversations.inspect", { conversation_id: run.conversation_id }, parseConversationInspection),
          ]);
          if (current && activeConversationIdRef.current === run.conversation_id) {
            setConversation(snapshot);
            setConversationInspection(inspection);
          }
          void loadConversations();
        } catch (reason) {
          const interrupted = interruptedDesktopRunStatus(run, reason);
          if (interrupted) {
            setDesktopRunStatuses((previous) => ({ ...previous, [run.conversation_id]: interrupted }));
            setQueuedPromptsPaused(runQueueRef.current.length > 0);
            if (activeConversationIdRef.current === run.conversation_id) void loadConversation(run.conversation_id, true);
            void loadConversations();
            if (current) setError("Task status is no longer available. The task is marked interrupted and queued messages are paused for review.");
            continue;
          }
          stillActive = true;
          if (current) setError(describeError(reason, "Task status could not be refreshed"));
        }
      }
      if (!current) return;
      if (terminalStatuses.length > 0) {
        setDesktopRunStatuses((previous) => {
          const updated = { ...previous };
          for (const status of terminalStatuses) {
            if (updated[status.conversation_id]?.run_id === status.run_id) {
              updated[status.conversation_id] = status;
            }
          }
          return updated;
        });
      }
      if (stillActive) {
        timer = window.setTimeout(() => void poll(), 500);
      }
    };
    timer = window.setTimeout(() => void poll(), 400);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [activeDesktopRunIds]);

  useEffect(() => {
    if (!shouldDrainTaskQueue(activeDesktopRunIds, runQueueRef.current.length, runQueueDrainRef.current, queuedPromptsPaused)) return;
    const [next, ...remaining] = runQueueRef.current;
    if (!next) return;
    runQueueDrainRef.current = true;
    updateQueuedPrompts(remaining);
    void startDesktopRun(next).finally(() => {
      runQueueDrainRef.current = false;
    });
  }, [activeDesktopRunIds, queuedPrompts.length, queuedPromptsPaused]);

  useEffect(() => {
    if (!workPanelVisible) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => workPanelRef.current?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      if (previous?.isConnected && previous !== document.body && previous.getClientRects().length && !previous.closest("[inert]")) previous.focus();
      else document.querySelector<HTMLElement>(".ct-detail-button, .app-work-panel-toggle, .chat-view [role=textbox]")?.focus();
    };
  }, [workPanelVisible]);

  useEffect(() => {
    if (commandPaletteOpen) {
      const frame = window.requestAnimationFrame(() => commandPaletteInputRef.current?.focus());
      return () => window.cancelAnimationFrame(frame);
    }
    const opener = commandPaletteOpenerRef.current;
    commandPaletteOpenerRef.current = null;
    if (opener?.isConnected && opener.getClientRects().length > 0 && !opener.closest("[inert]")) opener.focus();
  }, [commandPaletteOpen]);

  useEffect(() => {
    if (viewportWidth <= 1100) {
      setSidebarCollapsed(true);
      setWorkPanelOpen(false);
    }
  }, [viewportWidth]);

  useEffect(() => {
    window.localStorage.setItem("aegis-sidebar-width", String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    window.localStorage.setItem("aegis-work-panel-width", String(workPanelWidth));
  }, [workPanelWidth]);

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
    document.querySelector<HTMLElement>(".settings-content")?.scrollTo(0, 0);
  }, [settingsSection]);

  useEffect(() => {
    if (!projectSwitcherOpen) return;
    projectSwitcherInputRef.current?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".project-switcher-trigger, .home-project-switcher-menu")) setProjectSwitcherOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [projectSwitcherOpen, projectSwitcherView]);

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
    // Destination pages share the document scroll container. Reset it when
    // switching routes so long utility pages cannot leave Settings clipped on entry.
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }, [destination]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => {
      document.documentElement.dataset.reducedMotion = query.matches ? "true" : "false";
    };
    sync();
    query.addEventListener("change", sync);
    return () => {
      query.removeEventListener("change", sync);
      delete document.documentElement.dataset.reducedMotion;
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.fontSize = fontSizePreset.toLocaleLowerCase();
    return () => {
      delete document.documentElement.dataset.fontSize;
    };
  }, [fontSizePreset]);

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

  function applyDesktopSettings(settings: DesktopSettings) {
    setTheme(settings.theme);
    setFontSizePreset(settings.font_size_preset);
    setThinkingMode(settings.thinking_default);
    setContextUsage(settings.context_usage);
    setPermissionMode(settings.permission_mode);
    setEnterToSend(settings.enter_to_send);
    setInstructionsDraft(settings.instructions);
    setParallelWorkersEnabled(settings.parallel_workers_enabled);
  }

  async function loadSettings() {
    const result = await desktopRequest("settings.get", {}, parseSettingsResult);
    settingsRevisionRef.current = result.revision;
    applyDesktopSettings(result.settings);
  }

  function saveSettingsPatch(patch: Partial<DesktopSettings>, onSaved?: () => void) {
    const run = async () => {
      try {
        const result = await desktopRequest(
          "settings.update",
          { expected_revision: settingsRevisionRef.current, settings: patch },
          parseSettingsResult,
        );
        settingsRevisionRef.current = result.revision;
        applyDesktopSettings(result.settings);
        onSaved?.();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Desktop settings could not be saved");
        try {
          await loadSettings();
        } catch (reloadReason) {
          setError(reloadReason instanceof Error ? reloadReason.message : "Desktop settings could not be reloaded");
        }
      }
    };
    settingsUpdateQueueRef.current = settingsUpdateQueueRef.current.then(run, run);
    return settingsUpdateQueueRef.current;
  }

  async function bootstrap() {
    setError(null);
    try {
      await loadSettings();
    } catch (reason) {
      setError(`Unable to load desktop settings: ${describeError(reason, "the settings service did not respond")}`);
      return;
    }

    let snapshot: WorkspaceSnapshot;
    try {
      snapshot = await desktopRequest("workspace.open", {}, parseWorkspaceSnapshot);
    } catch (reason) {
      setError(`Unable to open the local workspace: ${describeError(reason, "the local host did not respond")}`);
      return;
    }
    setWorkspace(snapshot);
    // These surfaces are optional. A source-map or capability problem must
    // not make the usable chat shell look as if opening the workspace failed.
    const projectId = await loadProjects();
    await loadSourceSnapshot(projectId !== null);
    try {
      await configureConnection();
    } catch (reason) {
      setConnectionSaved(false);
      setConnectedProviders([]);
      setActiveConnectionId(null);
      setAvailableModels([]);
      setModelCatalogState("UNAVAILABLE");
      setConnectionNotice(`Provider state is unavailable: ${describeError(reason, "connect an API key again from Models")}`);
    }
    await loadExtensionCatalog();

    let records: Conversation[];
    try {
      records = await loadConversations();
    } catch (reason) {
      setError(`Unable to load conversations: ${describeError(reason, "the conversation store did not respond")}`);
      return;
    }
    try {
      const latestStartedConversation = records.find((record) => !isUnstartedConversation(record));
      if (latestStartedConversation) await selectConversation(latestStartedConversation.conversation_id);
      else startNewTask();
    } catch (reason) {
      setError(`Unable to start a conversation: ${describeError(reason, "the local conversation store did not respond")}`);
    }
  }

  async function activateWorkspace(workspacePath: string) {
    if (projectSwitchBlocked) throw new Error("Wait for the active task to finish before switching projects.");
    let approvedWorkspacePath = workspacePath;
    if (hasNativeWindow()) {
      // Every activation path (picker, project list, and clone) must pass
      // through the same native approval gate before the sidecar receives a
      // filesystem path.
      approvedWorkspacePath = await invoke<string>("approve_workspace_path", { path: workspacePath.trim() });
    }
    const snapshot = await desktopRequest("workspace.switch", { workspace_path: approvedWorkspacePath }, parseWorkspaceSnapshot);
    conversationLoadRevision.current += 1;
    graphLoadRevision.current += 1;
    setConversationLoading(false);
    setConversationError(null);
    setWorkspace(snapshot);
    setActiveProjectId(null);
    const projectId = await loadProjects();
    setSourceSnapshot(null);
    setWorkspaceGraph(null);
    setConversation(null);
    setConversationInspection(null);
    setReconciliationNotice(null);
    setActiveConversationId(null);
    setConversations([]);
    setMessage("");
    setPendingImages([]);
    setComposerMenuOpen(null);
    setComposerModelView("root");
    setComposerModelQuery("");
    setSelectedFile(null);
    setConnectionSaved(false);
    setConnectionMode("local");
    setConnectedProviders([]);
    setActiveConnectionId(null);
    setDisconnectConfirmationId(null);
    setMode("mock");
    setModelId("local-model");
    setEndpoint(defaultEndpoint);
    setApiKeyDraft("");
    setCustomProviderEndpoint("");
    setMcpDrafts({});
    setMcpImportedSetupDrafts({});
    setMcpRegistryEnvironmentVariables({});
    setMcpRegistryEnvironmentValues({});
    setMcpRegistryHints({});
    setMcpTestResults({});
    setMcpAutoRunReadOnly({});
    setMcpPromptCatalogs({});
    setMcpPromptArguments({});
    setMcpPromptResults({});
    setConnectionNotice(null);
    setAvailableModels([]);
    setModelCatalogState("UNAVAILABLE");
    await loadSourceSnapshot(projectId !== null);
    try {
      await configureConnection();
    } catch (reason) {
      setConnectionSaved(false);
      setConnectedProviders([]);
      setActiveConnectionId(null);
      setAvailableModels([]);
      setModelCatalogState("UNAVAILABLE");
      setConnectionNotice(`Provider state is unavailable: ${describeError(reason, "connect an API key again from Models")}`);
    }
    await loadExtensionCatalog();
    const records = await loadConversations();
    const latestStartedConversation = records.find((record) => !isUnstartedConversation(record));
    if (latestStartedConversation) await selectConversation(latestStartedConversation.conversation_id);
    else startNewTask();
  }

  async function switchWorkspace(workspacePath: string) {
    if (projectSwitcherBusy || projectSwitchBlocked || !workspacePath.trim()) return;
    setProjectSwitcherBusy(true);
    setError(null);
    try {
      await activateWorkspace(workspacePath.trim());
      setProjectSwitcherOpen(false);
      setProjectSwitcherView("list");
      setProjectCloneUrl("");
    } catch (reason) {
      setError(describeError(reason, "Unable to switch the workspace"));
    } finally {
      setProjectSwitcherBusy(false);
    }
  }

  async function openWorkspacePicker() {
    if (projectSwitcherBusy || projectSwitchBlocked) return;
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
      setError(describeError(reason, "Unable to open the selected project"));
    } finally {
      setProjectSwitcherBusy(false);
    }
  }

  async function cloneWorkspace() {
    const cloneUrl = projectCloneUrl.trim();
    if (projectSwitcherBusy || projectSwitchBlocked || !cloneUrl) return;
    setProjectSwitcherBusy(true);
    setError(null);
    try {
      const result = await desktopRequest("workspace.clone", { clone_url: cloneUrl }, parseWorkspaceCloneResult);
      await activateWorkspace(result.workspace_path);
      setProjectSwitcherOpen(false);
      setProjectSwitcherView("list");
      setProjectCloneUrl("");
    } catch (reason) {
      setError(describeError(reason, "Unable to clone the selected project"));
    } finally {
      setProjectSwitcherBusy(false);
    }
  }

  async function loadSourceSnapshot(hasActiveProject: boolean) {
    const revision = ++graphLoadRevision.current;
    setGraphError(null);
    if (!hasActiveProject) {
      setSourceSnapshot(null);
      setWorkspaceGraph(null);
      setGraphLoading(false);
      return;
    }
    setGraphLoading(true);
    try {
      const snapshot = await desktopRequest("workspace.source_snapshot", {}, parseSourceSnapshot);
      if (revision !== graphLoadRevision.current) return;
      setSourceSnapshot(snapshot);
      setWorkspaceGraph((current) => current?.source_revision === snapshot.revision ? current : null);
    } catch (reason) {
      if (revision !== graphLoadRevision.current) return;
      setSourceSnapshot(null);
      setWorkspaceGraph(null);
      const detail = describeError(reason, "choose the project again to grant local access");
      setGraphError(detail);
      setError(`Unable to read the local workspace: ${detail}`);
    } finally {
      if (revision === graphLoadRevision.current) setGraphLoading(false);
    }
  }

  async function loadWorkspaceGraph(hasActiveProject: boolean, refreshSnapshot = false) {
    const revision = ++graphLoadRevision.current;
    setGraphError(null);
    if (!hasActiveProject) {
      setWorkspaceGraph(null);
      setGraphLoading(false);
      return;
    }
    setGraphLoading(true);
    try {
      let snapshot = sourceSnapshot;
      if (!snapshot || refreshSnapshot) {
        snapshot = await desktopRequest("workspace.source_snapshot", {}, parseSourceSnapshot);
        if (revision !== graphLoadRevision.current) return;
        setSourceSnapshot(snapshot);
      }
      const worker = new Worker(new URL("./workspace_graph.worker.ts", import.meta.url), { type: "module" });
      const graph = await new Promise<WorkspaceGraph>((resolve, reject) => {
        worker.onmessage = (event: MessageEvent<{ graph?: WorkspaceGraph; error?: string }>) => {
          if (event.data.graph) resolve(event.data.graph);
          else reject(new Error(event.data.error ?? "The source map could not be built"));
        };
        worker.onerror = () => reject(new Error("The source map worker failed"));
        worker.postMessage(snapshot);
      }).finally(() => worker.terminate());
      if (revision !== graphLoadRevision.current) return;
      setWorkspaceGraph(graph);
    } catch (reason) {
      if (revision !== graphLoadRevision.current) return;
      setWorkspaceGraph(null);
      const detail = describeError(reason, "choose the project again to grant local access");
      setGraphError(detail);
      setError(`Unable to read the local workspace: ${detail}`);
    } finally {
      if (revision === graphLoadRevision.current) setGraphLoading(false);
    }
  }

  useEffect(() => {
    if (destination === "chat" && workPanelOpen && workTab === "map" && activeProjectId && !workspaceGraph && !graphLoading && !graphError) {
      void loadWorkspaceGraph(true);
    }
  }, [destination, workPanelOpen, workTab, activeProjectId, workspaceGraph, graphLoading, graphError]);

  useEffect(() => {
    if (!selectedFile || !activeProjectId) {
      setSelectedFileContent(null);
      setFileReadError(null);
      return;
    }
    const revision = ++fileReadRevision.current;
    let current = true;
    setSelectedFileContent(null);
    setFileReadError(null);
    setFileOpenNotice(null);
    void desktopRequest("workspace.file_read", { relative_path: selectedFile }, parseWorkspaceFileResult)
      .then((file) => {
        if (current && revision === fileReadRevision.current) setSelectedFileContent(file);
      })
      .catch((reason) => {
        if (current && revision === fileReadRevision.current) setFileReadError(describeError(reason, "This file could not be opened"));
      });
    return () => { current = false; };
  }, [selectedFile, activeProjectId, sourceSnapshot?.revision]);

  useEffect(() => {
    if (destination !== "chat" || !workPanelOpen || workTab !== "changes" || !activeProjectId) return;
    const revision = ++changesListRevision.current;
    let current = true;
    setChangesError(null);
    setSelectedChangeDiff(null);
    void desktopRequest("workspace.changes", {}, parseWorkspaceChangesResult)
      .then((result) => {
        if (!current || revision !== changesListRevision.current) return;
        setWorkspaceChanges(result);
        if (!result.entries.some((entry) => entry.relative_path === selectedChangePath)) {
          setSelectedChangePath(result.entries[0]?.relative_path ?? null);
        }
      })
      .catch((reason) => {
        if (current && revision === changesListRevision.current) {
          setWorkspaceChanges(null);
          setChangesError(describeError(reason, "Tracked changes could not be read"));
        }
      });
    return () => { current = false; };
  }, [destination, workPanelOpen, workTab, activeProjectId, changesRefreshKey]);

  useEffect(() => {
    if (destination !== "chat" || !workPanelOpen || workTab !== "changes" || !selectedChangePath
      || !workspaceChanges?.entries.some((entry) => entry.relative_path === selectedChangePath)) {
      setSelectedChangeDiff(null);
      return;
    }
    const revision = ++changesDiffRevision.current;
    let current = true;
    setSelectedChangeDiff(null);
    void desktopRequest("workspace.changes", { relative_path: selectedChangePath }, parseWorkspaceDiffResult)
      .then((result) => {
        if (current && revision === changesDiffRevision.current) setSelectedChangeDiff(result);
      })
      .catch((reason) => {
        if (current && revision === changesDiffRevision.current) setChangesError(describeError(reason, "This diff could not be read"));
      });
    return () => { current = false; };
  }, [destination, workPanelOpen, workTab, selectedChangePath, workspaceChanges]);

  async function loadProjects(): Promise<string | null> {
    try {
      const result = await desktopRequest("projects.list", {}, parseProjectList);
      setProjects(result.projects);
      setActiveProjectId(result.active_project_id);
      return result.active_project_id;
    } catch (reason) {
      setProjects([]);
      setActiveProjectId(null);
      setError(reason instanceof Error ? reason.message : "Unable to load local projects");
      return null;
    }
  }

  async function removeProject(projectId: string) {
    try {
      const result = await desktopRequest("projects.remove", { project_id: projectId }, parseProjectRemove);
      if (result.removed) await loadProjects();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Project could not be removed from the list");
    }
  }

  async function loadCapabilityState() {
    try {
      const state = await desktopRequest("extensions.state", {}, parseCapabilityState);
      setCapabilityState(state);
    } catch (reason) {
      setCapabilityState(null);
      setCapabilityNotice(reason instanceof Error ? reason.message : "Capability state could not be loaded");
    }
  }

  async function setCapabilityActivation(
    kind: "skill" | "extension" | "mcp",
    id: string,
    descriptorHash: string,
    enabled: boolean,
    approved: boolean,
  ) {
    const current = capabilityState;
    if (!current) {
      await loadCapabilityState();
      return;
    }
    try {
      const state = await desktopRequest(
        "extensions.set_state",
        {
          kind,
          id,
          descriptor_hash: descriptorHash,
          enabled,
          approved,
          expected_revision: current.revision,
        },
        parseCapabilityState,
      );
      setCapabilityState(state);
      if (kind === "mcp") clearMcpTest(id);
      setCapabilityNotice(kind === "mcp" ? "Approval recorded. Configure the transport before starting it." : enabled ? "Capability enabled for this workspace." : "Capability disabled for this workspace.");
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "Capability state could not be changed");
      await loadCapabilityState();
    }
  }

  async function loadExtensionCatalog() {
    setExtensionBusy(true);
    try {
      const catalog = await desktopRequest("extensions.discover", {}, parseExtensionCatalog);
      setExtensionCatalog(catalog);
      setMcpCatalog({
        schema: "aegis-desktop-mcp-catalog-v1",
        servers: catalog.mcp_servers,
        activation: catalog.activation.mcp,
      });
      await loadCapabilityState();
      setExtensionNotice(null);
    } catch (reason) {
      setExtensionNotice(reason instanceof Error ? reason.message : "Local capability catalog could not be read");
    } finally {
      setExtensionBusy(false);
    }
  }

  async function importSkillFolder() {
    if (extensionBusy || !hasNativeWindow()) return;
    setExtensionBusy(true);
    try {
      const skillName = await invoke<string | null>("pick_and_import_skill");
      if (!skillName) return;
      const catalog = await desktopRequest("extensions.discover", {}, parseExtensionCatalog);
      setExtensionCatalog(catalog);
      setMcpCatalog({
        schema: "aegis-desktop-mcp-catalog-v1",
        servers: catalog.mcp_servers,
        activation: catalog.activation.mcp,
      });
      await loadCapabilityState();
      setExtensionNotice(`Imported “${skillName}”. It stays disabled until you enable it in Settings.`);
    } catch (reason) {
      setExtensionNotice(describeError(reason, "The selected skill could not be imported"));
    } finally {
      setExtensionBusy(false);
    }
  }

  async function importCapabilityPack(source: "folder" | "archive") {
    if (extensionBusy || !hasNativeWindow()) return;
    setExtensionBusy(true);
    try {
      const pickerCommand = source === "archive" ? "pick_and_import_extension_archive" : "pick_and_import_extension_pack";
      const rawImport = await invoke<unknown>(pickerCommand);
      if (!rawImport) return;
      const imported = parseExtensionPackImport(rawImport);
      if (!imported) throw new Error("The imported capability package returned an invalid setup summary.");
      const { extension_id: extensionId, mcp_setup_suggestions: setupSuggestions } = imported;
      if (setupSuggestions.length > 0) {
        setMcpDrafts((current) => ({
          ...current,
          ...Object.fromEntries(setupSuggestions.map((suggestion) => [suggestion.server_id, {
            ...emptyMcpDraft,
            ...(suggestion.transport === "stdio"
              ? { command: suggestion.command.join("\n"), cwd: suggestion.cwd ?? "" }
              : {}),
            ...(suggestion.transport === "streamable-http"
              ? { endpoint: suggestion.endpoint, allowedHosts: suggestion.allowed_host }
              : {}),
          }])),
        }));
        setMcpImportedSetupDrafts((current) => ({
          ...current,
          ...Object.fromEntries(setupSuggestions.map(({ server_id: serverId }) => [serverId, true])),
        }));
      }
      const catalog = await desktopRequest("extensions.discover", {}, parseExtensionCatalog);
      setExtensionCatalog(catalog);
      setMcpCatalog({
        schema: "aegis-desktop-mcp-catalog-v1",
        servers: catalog.mcp_servers,
        activation: catalog.activation.mcp,
      });
      await loadCapabilityState();
      setExtensionNotice(
        `Imported “${extensionId}”. Bundled scripts and hooks are not executed. Skills stay disabled; MCP servers stay unapproved and stopped; WASM tools require separate review and activation. ${setupSuggestions.length > 0 ? "Connection drafts are untrusted; review commands and URLs before testing. Environment and header values were not copied." : "No safe connection draft was available; configure it manually after approval."}`,
      );
    } catch (reason) {
      setExtensionNotice(describeError(reason, "The selected capability pack could not be imported"));
    } finally {
      setExtensionBusy(false);
    }
  }

  async function loadSkillBody(name: string) {
    setSkillLoadBusy(true);
    try {
      const result: SkillLoadResult = await desktopRequest("extensions.load_skill", { name }, parseSkillLoadResult);
      setSelectedSkillBody(result.body);
      setExtensionNotice(`Đã mở nội dung skill “${result.skill.name}” từ host cục bộ.`);
    } catch (reason) {
      setSelectedSkillBody(null);
      setExtensionNotice(reason instanceof Error ? reason.message : "Skill could not be loaded");
    } finally {
      setSkillLoadBusy(false);
    }
  }

  async function loadMcpCatalog() {
    setExtensionBusy(true);
    try {
      const catalog = await desktopRequest("extensions.mcp_preview", {}, parseMcpCatalog);
      setMcpCatalog(catalog);
      setExtensionNotice(null);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "Local MCP catalog could not be read");
    } finally {
      setExtensionBusy(false);
    }
  }

  async function searchMcpRegistry(cursor?: string) {
    const query = mcpRegistryQuery.trim();
    if (!query) {
      setCapabilityNotice("Enter a server name to search the public MCP directory.");
      return;
    }
    setMcpRegistryBusy(true);
    try {
      const page = await desktopRequest(
        "extensions.mcp_registry_search",
        { query, ...(cursor ? { cursor } : {}) },
        parseMcpRegistrySearch,
      );
      setMcpRegistryResults((current) => {
        if (!cursor || current?.query !== page.query) return page;
        const names = new Set(current.servers.map((server) => server.name.toLocaleLowerCase()));
        return {
          ...page,
          servers: [...current.servers, ...page.servers.filter((server) => !names.has(server.name.toLocaleLowerCase()))],
        };
      });
      setCapabilityNotice(null);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "The public MCP directory could not be searched.");
    } finally {
      setMcpRegistryBusy(false);
    }
  }

  function prepareMcpRegistryEntry(server: McpRegistryServer) {
    const options = getMcpRegistrySetupOptions(server);
    if (options.length === 0) {
      setCapabilityNotice("This entry has no transport AEGIS can configure yet. Nothing was installed or started.");
      return;
    }
    const safeId = server.name
      .toLocaleLowerCase()
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/^[^a-z0-9]+/, "")
      .slice(0, 64)
      .replace(/[._-]+$/, "") || "mcp-server";
    setMcpRegistrySelection(server);
    setMcpRegistryChoice(options.length === 1 ? options[0].key : "");
    setMcpNewId(safeId);
    setMcpNewDescription(server.description || server.title || server.name);
    if (options.length === 1) setMcpNewTransport(options[0].transport);
    setMcpAddOpen(true);
    setCapabilityNotice(options.length === 1
      ? "Setup draft prepared. Review it before adding; the directory entry was not installed or started."
      : `Choose one of the ${options.length} directory options before adding metadata. Nothing was installed or started.`);
  }

  async function addMcpMetadata() {
    if (!mcpNewId.trim() || !mcpNewDescription.trim()) {
      setCapabilityNotice("Enter an id and description before adding MCP metadata.");
      return;
    }
    const selected = mcpRegistrySelection;
    const selectedOption = selected ? findMcpRegistrySetupOption(selected, mcpRegistryChoice) : undefined;
    if (selected && !selectedOption) {
      setCapabilityNotice("Choose one exact package or remote endpoint before adding metadata.");
      return;
    }
    const transport = selectedOption?.transport ?? mcpNewTransport;
    const packageInfo = selectedOption?.kind === "package" ? selectedOption.packageInfo : undefined;
    const environmentVariables = packageInfo?.environment_variables ?? [];
    setExtensionBusy(true);
    try {
      const result = await desktopRequest(
        "extensions.add_mcp_metadata",
        {
          server_id: mcpNewId.trim(),
          description: mcpNewDescription.trim(),
          transport,
          environment_variables: environmentVariables,
        },
        parseMcpMetadata,
      );
      const requiredInputs = environmentVariables.filter((item) => item.is_required).map((item) => item.name);
      const remote = selectedOption?.kind === "remote" ? selectedOption.remote : undefined;
      const endpoint = remote?.endpoint ?? "";
      let allowedHost = "";
      if (endpoint) {
        try {
          const parsed = new URL(endpoint);
          if (parsed.protocol === "https:" && !parsed.username && !parsed.password && !parsed.search && !parsed.hash) {
            allowedHost = parsed.hostname;
          }
        } catch {
          allowedHost = "";
        }
      }
      setMcpDrafts((current) => ({
        ...current,
        [result.server_id]: {
          ...emptyMcpDraft,
          ...(allowedHost ? { endpoint, allowedHosts: allowedHost } : {}),
        },
      }));
      setMcpRegistryEnvironmentVariables((current) => ({ ...current, [result.server_id]: environmentVariables }));
      setMcpRegistryEnvironmentValues((current) => ({
        ...current,
        [result.server_id]: Object.fromEntries(environmentVariables.map(({ name }) => [name, ""])),
      }));
      if (selected) {
        const hint = [
          `Unverified directory metadata: ${selected.name} · ${selected.version}`,
          packageInfo ? `Selected package: ${packageInfo.registry_type} ${packageInfo.identifier}@${packageInfo.version}${packageInfo.runtime_hint ? ` · runtime hint ${packageInfo.runtime_hint}` : ""}` : "",
          remote ? `Selected remote: ${selectedOption?.label ?? "remote endpoint"}` : "",
          requiredInputs.length > 0 ? `Required environment names: ${requiredInputs.join(", ")}` : "",
          remote?.requires_headers ? "The remote entry declares headers; provide any credentials yourself in session-only settings." : "",
        ].filter(Boolean).join(" · ");
        setMcpRegistryHints((current) => ({ ...current, [result.server_id]: hint }));
      }
      setMcpRegistrySelection(null);
      setMcpRegistryChoice("");
      setMcpAddOpen(false);
      setMcpNewId("");
      setMcpNewDescription("");
      setCapabilityNotice(`${result.server_id} metadata added. Review and approve it before starting.`);
      await loadMcpCatalog();
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP metadata could not be added");
    } finally {
      setExtensionBusy(false);
    }
  }

  function updateMcpDraft(serverId: string, patch: Partial<McpDraft>) {
    setMcpDrafts((current) => ({
      ...current,
      [serverId]: { ...(current[serverId] ?? emptyMcpDraft), ...patch },
    }));
    clearMcpTest(serverId);
  }

  function clearMcpTest(serverId: string) {
    setMcpTestResults((current) => {
      if (!(serverId in current)) return current;
      const next = { ...current };
      delete next[serverId];
      return next;
    });
    setMcpAutoRunReadOnly((current) => {
      if (!(serverId in current)) return current;
      const next = { ...current };
      delete next[serverId];
      return next;
    });
  }

  function setMcpAutoRunTool(serverId: string, descriptorHash: string, enabled: boolean) {
    setMcpAutoRunReadOnly((current) => {
      const selected = { ...(current[serverId] ?? {}) };
      if (enabled) selected[descriptorHash] = true;
      else delete selected[descriptorHash];
      return { ...current, [serverId]: selected };
    });
  }

  async function loadMcpPrompts(server: McpServerSummary) {
    setMcpPromptsBusy(server.server_id);
    setMcpPromptCatalogs((current) => {
      const next = { ...current };
      delete next[server.server_id];
      return next;
    });
    setMcpPromptResults((current) => ({ ...current, [server.server_id]: {} }));
    setCapabilityNotice(null);
    try {
      const catalog = await desktopRequest(
        "extensions.mcp_prompts_list",
        { server_id: server.server_id },
        parseMcpPromptsList,
      );
      setMcpPromptCatalogs((current) => ({ ...current, [server.server_id]: { prompts: catalog.prompts } }));
      setMcpPromptResults((current) => ({ ...current, [server.server_id]: {} }));
      setCapabilityNotice(catalog.prompts.length > 0
        ? `Loaded ${catalog.prompts.length} prompt template${catalog.prompts.length === 1 ? "" : "s"} from ${server.server_id}. Nothing was sent to the model.`
        : `${server.server_id} does not currently advertise prompt templates.`);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP prompt templates could not be loaded");
    } finally {
      setMcpPromptsBusy(null);
    }
  }

  async function loadMcpSkills(server: McpServerSummary, cursor?: string | null) {
    setMcpSkillsBusy(server.server_id);
    setCapabilityNotice(null);
    try {
      const page = await desktopRequest(
        "extensions.mcp_skills_list",
        { server_id: server.server_id, ...(cursor ? { cursor } : {}) },
        parseMcpSkillsList,
      );
      setMcpSkillCatalogs((current) => {
        const previous = current[server.server_id];
        const byId = new Map<string, McpSkillSummary>();
        for (const skill of cursor ? previous?.skills ?? [] : []) byId.set(skill.id, skill);
        for (const skill of page.skills) byId.set(skill.id, skill);
        return {
          ...current,
          [server.server_id]: {
            ...page,
            skills: [...byId.values()],
          },
        };
      });
      setCapabilityState(page.state);
      setCapabilityNotice(!page.supports_skills
        ? server.server_id + " does not advertise MCP Skills."
        : page.skills.length > 0
          ? "Loaded " + page.skills.length + " Skill" + (page.skills.length === 1 ? "" : "s")
            + " from " + server.server_id + ". No instruction files were read."
          : "This server does not currently publish any Skills.");
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP Skills could not be loaded");
    } finally {
      setMcpSkillsBusy(null);
    }
  }

  async function setMcpSkillState(
    server: McpServerSummary,
    skill: McpSkillSummary,
    enabled: boolean,
    approved: boolean,
  ) {
    const state = capabilityState;
    const savedRecord = state?.records.find((item) => item.kind === "skill" && item.id === skill.id);
    const manifestHash = skill.manifest_hash ?? savedRecord?.descriptor_hash;
    if (!state || !manifestHash) {
      setCapabilityNotice("This dynamic Skill has no stable manifest and cannot be persistently approved.");
      return;
    }
    setMcpSkillBusyId(skill.id);
    try {
      const result: McpSkillStateResult = await desktopRequest(
        "extensions.mcp_skill_set_state",
        {
          server_id: server.server_id,
          uri: skill.uri,
          manifest_hash: manifestHash,
          enabled,
          approved,
          expected_revision: state.revision,
        },
        parseMcpSkillState,
      );
      setCapabilityState(result.state);
      setMcpSkillCatalogs((current) => {
        const catalog = current[server.server_id];
        if (!catalog) return current;
        return {
          ...current,
          [server.server_id]: {
            ...catalog,
            skills: catalog.skills.map((item) => item.id === skill.id
              ? { ...item, manifest_hash: result.manifest_hash, enabled: result.enabled, approved: result.approved }
              : item),
          },
        };
      });
      setCapabilityNotice(approved
        ? enabled ? skill.name + " is approved and enabled for this workspace."
          : skill.name + " is disabled; its approval is retained."
        : skill.name + " approval was revoked.");
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP Skill approval could not be changed");
      if (reason instanceof Error && reason.message.includes("changed")) {
        await loadMcpSkills(server);
      }
      await loadCapabilityState();
    } finally {
      setMcpSkillBusyId(null);
    }
  }

  async function renderMcpPrompt(server: McpServerSummary, prompt: McpPromptDescriptor) {
    const values = mcpPromptArguments[server.server_id]?.[prompt.name] ?? {};
    const missing = prompt.arguments.filter((argument) => argument.required && !(values[argument.name] ?? "").trim());
    if (missing.length > 0) {
      setCapabilityNotice(`Fill in the required prompt field${missing.length === 1 ? "" : "s"}: ${missing.map((item) => item.name).join(", ")}.`);
      return;
    }
    const argumentsValue = Object.fromEntries(
      prompt.arguments
        .filter((argument) => Object.prototype.hasOwnProperty.call(values, argument.name))
        .map((argument) => [argument.name, values[argument.name]]),
    );
    setMcpPromptsBusy(server.server_id);
    setCapabilityNotice("The selected values are being sent to this MCP server to render a preview; they are not being sent to the model.");
    try {
      const result = await desktopRequest(
        "extensions.mcp_prompts_get",
        { server_id: server.server_id, name: prompt.name, arguments: argumentsValue },
        parseMcpPromptResult,
      );
      setMcpPromptResults((current) => ({
        ...current,
        [server.server_id]: { ...(current[server.server_id] ?? {}), [prompt.name]: result },
      }));
      setCapabilityNotice("Preview loaded. Review its server-provided text before inserting anything into the chat.");
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "The selected MCP prompt could not be rendered");
    } finally {
      setMcpPromptsBusy(null);
    }
  }

  function insertMcpPrompt(serverId: string, promptName: string) {
    const result = mcpPromptResults[serverId]?.[promptName];
    if (!result || result.messages.length !== 1 || result.messages[0].role !== "user") return;
    const text = result.messages[0].text;
    setMessage((current) => current.trim() ? `${current}\n\n${text}` : text);
    openDestination("chat");
    setCapabilityNotice("Prompt text was added to the composer for your review. It has not been sent.");
  }

  function parseMcpObject(text: string, label: string): Record<string, string> {
    if (!text.trim()) return {};
    let value: unknown;
    try {
      value = JSON.parse(text);
    } catch {
      throw new Error(`${label} must be valid JSON`);
    }
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error(`${label} must be a JSON object`);
    }
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.some(([key, item]) => !key.trim() || typeof item !== "string")) {
      throw new Error(`${label} values must be non-empty strings`);
    }
    return Object.fromEntries(entries) as Record<string, string>;
  }

  async function activateMcp(server: McpServerSummary) {
    const state = capabilityState;
    const record = state?.records.find((item) => item.kind === "mcp" && item.id === server.server_id);
    if (!state || !record?.approved) {
      setCapabilityNotice("Approve this MCP server before starting it.");
      return;
    }
    let config: Record<string, unknown>;
    try {
      config = mcpConfigForServer(server);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP configuration is invalid");
      return;
    }
    const tested = mcpTestResults[server.server_id];
    if (!tested) {
      setCapabilityNotice("Test this exact MCP configuration before enabling tools.");
      return;
    }
    const selectedReadOnlyHashes = getSelectedMcpAutoRunHashes(
      tested.tools,
      mcpAutoRunReadOnly[server.server_id] ?? {},
    );
    setMcpActivationBusy(server.server_id);
    try {
      const result = await desktopRequest(
        "extensions.activate_mcp",
        {
          server_id: server.server_id,
          descriptor_hash: server.descriptor_hash,
          approved: true,
          expected_revision: state.revision,
          config,
          test_token: tested.test_token,
          ...(selectedReadOnlyHashes.length > 0
            ? { auto_run_tool_hashes: selectedReadOnlyHashes }
            : {}),
        },
        parseMcpActivation,
      );
      clearMcpTest(server.server_id);
      setMcpPromptCatalogs((current) => {
        const next = { ...current };
        delete next[server.server_id];
        return next;
      });
      setMcpPromptResults((current) => {
        const next = { ...current };
        delete next[server.server_id];
        return next;
      });
      setMcpPromptArguments((current) => {
        const next = { ...current };
        delete next[server.server_id];
        return next;
      });
      await loadCapabilityState();
      const grantNotice = selectedReadOnlyHashes.length > 0
        ? ` ${selectedReadOnlyHashes.length} reviewed, server-labeled read-only tool${selectedReadOnlyHashes.length === 1 ? "" : "s"} can run without repeated approval.`
        : " Tools will ask for approval when called.";
      setCapabilityNotice(`${server.server_id} started with ${result.tools.length} tool${result.tools.length === 1 ? "" : "s"}.${grantNotice}`);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP server could not be started");
      await loadCapabilityState();
    } finally {
      setMcpActivationBusy(null);
    }
  }

  function mcpConfigForServer(server: McpServerSummary): Record<string, unknown> {
    const draft = mcpDrafts[server.server_id] ?? emptyMcpDraft;
    if (server.transport.toLocaleLowerCase() === "stdio") {
      const command = draft.command.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      if (command.length === 0) throw new Error("Enter one executable/argument per line");
      const environmentVariables = mcpRegistryEnvironmentVariables[server.server_id] ?? server.environment_variables;
      const allowedRegistryNames = new Set(environmentVariables.map(({ name }) => name));
      const registryValues = Object.fromEntries(
        Object.entries(mcpRegistryEnvironmentValues[server.server_id] ?? {}).filter(([name, value]) =>
          allowedRegistryNames.has(name) && typeof value === "string" && value.trim().length > 0),
      );
      const environment = { ...parseMcpObject(draft.environment, "Environment"), ...registryValues };
      const missingEnvironment = missingRequiredEnvironment(
        environmentVariables.filter((item) => item.is_required).map((item) => item.name),
        environment,
      );
      if (missingEnvironment.length > 0) {
        throw new Error(`Enter values for required environment variables: ${missingEnvironment.join(", ")}`);
      }
      return {
        command,
        ...(draft.cwd.trim() ? { cwd: draft.cwd.trim() } : {}),
        environment,
      };
    }
    const endpoint = draft.endpoint.trim();
    const allowedHosts = draft.allowedHosts.split(",").map((item) => item.trim()).filter(Boolean);
    if (!endpoint) throw new Error("Enter the MCP HTTPS endpoint");
    if (allowedHosts.length === 0) throw new Error("Enter the endpoint host in Allowed hosts");
    return {
      endpoint,
      allowed_hosts: allowedHosts,
      headers: parseMcpObject(draft.headers, "Headers"),
      allow_local: false,
      ...(draft.oauthClientId.trim() ? { oauth_client_id: draft.oauthClientId.trim() } : {}),
    };
  }

  async function testMcpConnection(server: McpServerSummary) {
    const state = capabilityState;
    const record = state?.records.find((item) => item.kind === "mcp" && item.id === server.server_id);
    if (!state || !record?.approved) {
      setCapabilityNotice("Approve this MCP server before testing its connection.");
      return;
    }
    let config: Record<string, unknown>;
    try {
      config = mcpConfigForServer(server);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP configuration is invalid");
      return;
    }
    setMcpTestBusy(server.server_id);
    clearMcpTest(server.server_id);
    setCapabilityNotice(null);
    try {
      const result = await desktopRequest(
        "extensions.test_mcp",
        {
          server_id: server.server_id,
          descriptor_hash: server.descriptor_hash,
          expected_revision: state.revision,
          config,
        },
        parseMcpTest,
      );
      setMcpTestResults((current) => ({ ...current, [server.server_id]: result }));
      setCapabilityNotice(
          result.tools_total > 0
          ? `Connection worked. Found ${result.tools_total} available capabilities${result.tools_truncated ? "; showing the first 64" : ""}. None were run and the server was closed.`
          : "Connection worked, but this server advertised no tools or readable resources. It was closed without running any tool.",
      );
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP connection test failed");
    } finally {
      setMcpTestBusy(null);
    }
  }

  async function deactivateMcp(server: McpServerSummary) {
    const state = capabilityState;
    if (!state) return;
    setMcpActivationBusy(server.server_id);
    try {
      await desktopRequest(
        "extensions.deactivate_mcp",
        {
          server_id: server.server_id,
          descriptor_hash: server.descriptor_hash,
          expected_revision: state.revision,
        },
        parseMcpActivation,
      );
      clearMcpTest(server.server_id);
      await loadCapabilityState();
      setCapabilityNotice(`${server.server_id} stopped. Its configuration remains session-only.`);
    } catch (reason) {
      setCapabilityNotice(reason instanceof Error ? reason.message : "MCP server could not be stopped");
      await loadCapabilityState();
    } finally {
      setMcpActivationBusy(null);
    }
  }

  async function configureConnection() {
    const connectionList = await desktopRequest("connections.list", {}, parseConnectionList);
    const records = connectionList.records;
    setProviderChoices(connectionList.providers);
    setSelectedProviderKind((current) => connectionList.providers.some((item) => item.provider_kind === current) ? current : "");
    // A persisted record without a live session secret is intentionally not
    // treated as connected. Bootstrap must never create a synthetic provider
    // or model just to make the conversation header look ready.
    const usableConnections = records.filter((item) => item.enabled && Boolean(item.secret_ref));
    setConnectedProviders(usableConnections);
    setDisconnectConfirmationId(null);
    setConnectionSaved(usableConnections.length > 0);
    setConnectionMode(usableConnections.length > 0 ? "api-key" : "local");
    if (usableConnections.length === 0) {
      setActiveConnectionId(null);
      setAvailableModels([]);
      setModelCatalogState("UNAVAILABLE");
      setMode("mock");
      setModelId("local-model");
      setEndpoint(defaultEndpoint);
      return;
    }
    const { models, failedConnectionIds, catalogStates } = await loadConnectedModelCatalogs(usableConnections);
    const activeConnection = usableConnections.find((item) => item.connection_id === activeConnectionId)
      ?? usableConnections.find((item) => item.connection_id === conversation?.conversation.connection_id)
      ?? usableConnections[0];
    setActiveConnectionId(activeConnection.connection_id);
    setEndpoint(activeConnection.endpoint);
    setAvailableModels(models);
    setModelCatalogState(catalogStates.get(activeConnection.connection_id) ?? (models.length > 0 ? "CACHED" : "UNAVAILABLE"));
    const currentConversationModelIsAvailable = conversation && models.some(
      (item) => item.connection_id === conversation.conversation.connection_id
        && item.model_id === conversation.conversation.model_id,
    );
    if (!currentConversationModelIsAvailable) {
      const activeModel = models.find((item) => item.connection_id === activeConnection.connection_id);
      if (activeModel) setModelId(activeModel.model_id);
    }
    if (failedConnectionIds.length > 0) {
      setConnectionNotice(`Could not refresh ${failedConnectionIds.length} provider catalog(s); cached models are shown where available.`);
    }
  }

  async function refreshModelCatalog() {
    if (!connectionSaved || !activeConnectionId) {
      setConnectionNotice("Connect an API key before refreshing the model catalog.");
      setSettingsSection("Models");
      return;
    }
    setConnectionBusy("connect");
    setConnectionNotice(null);
    try {
      const result = await desktopRequest(
        "connections.discover",
        { connection_id: activeConnectionId, refresh_models: true },
        parseModelDiscoveryResult,
      );
      setAvailableModels((current) => [
        ...current.filter((item) => item.connection_id !== activeConnectionId),
        ...result.models,
      ]);
      setModelCatalogState(result.model_catalog_state);
      const conversationUsesRefreshedConnection = conversation?.conversation.connection_id === activeConnectionId;
      const currentCatalogModel = result.models.find(
        (item) => item.connection_id === activeConnectionId && item.model_id === conversation?.conversation.model_id,
      );
      if (result.models.length === 0 && conversationUsesRefreshedConnection) {
        setMode("mock");
      } else if (result.models.length > 0 && conversationUsesRefreshedConnection && !currentCatalogModel) {
        const fallback = result.models[0];
        setModelId(fallback.model_id);
        if (conversation) {
          const switched = await switchConversationModel(fallback, result.models, true);
          if (!switched) setMode("mock");
        } else {
          setMode("live");
        }
      } else if (!conversation && result.models.length > 0) {
        const activeModel = findModelForConnectionAndId(result.models, activeConnectionId, modelId) ?? result.models[0];
        setModelId(activeModel.model_id);
      }
      setConnectionNotice(result.discovery_error ?? `Catalog refreshed · ${result.models.length} model${result.models.length === 1 ? "" : "s"}.`);
    } catch (reason) {
      setConnectionNotice(reason instanceof Error ? reason.message : "Model catalog could not be refreshed");
    } finally {
      setConnectionBusy(null);
    }
  }

  async function disconnectProvider(connection: ConnectionRecord) {
    setConnectionBusy("disconnect");
    setConnectionNotice(null);
    try {
      await desktopRequest(
        "connections.disable",
        { connection_id: connection.connection_id, expected_revision: connection.revision },
        parseConnectionRecordResult,
      );
      const remaining = connectedProviders.filter((item) => item.connection_id !== connection.connection_id);
      setConnectedProviders(remaining);
      setAvailableModels((current) => current.filter((item) => item.connection_id !== connection.connection_id));
      setConnectionSaved(remaining.length > 0);
      setDisconnectConfirmationId(null);
      if (activeConnectionId === connection.connection_id) {
        const next = remaining[0];
        setActiveConnectionId(next?.connection_id ?? null);
        setEndpoint(next?.endpoint ?? defaultEndpoint);
        setConnectionMode(next ? "api-key" : "local");
        if (next) {
          const nextModel = availableModels.find((item) => item.connection_id === next.connection_id);
          if (nextModel) setModelId(nextModel.model_id);
        }
      }
      if (conversation?.conversation.connection_id === connection.connection_id) setMode("mock");
      setConnectionNotice(`${connection.provider_kind} disconnected. Its session key was cleared.`);
    } catch (reason) {
      setConnectionNotice(reason instanceof Error ? reason.message : "Provider could not be disconnected");
    } finally {
      setConnectionBusy(null);
    }
  }

  async function connectApiKey() {
    const key = apiKeyDraft.trim();
    if (!key || connectionBusy || !workspace || !selectedProvider) return;
    const selectedEndpoint = selectedProvider.requires_endpoint ? customProviderEndpoint.trim() : selectedProvider.endpoint;
    if (!selectedEndpoint) {
      setConnectionNotice("Enter the selected provider's HTTPS-compatible endpoint before connecting.");
      return;
    }
    setConnectionBusy("connect");
    setConnectionNotice(null);
    try {
      const connectionId = `api:${selectedProvider.provider_kind}:${crypto.randomUUID()}`;
      const payload: Record<string, unknown> = {
        api_key: key,
        provider_kind: selectedProvider.provider_kind,
        connection_id: connectionId,
      };
      if (selectedProvider.requires_endpoint) payload.endpoint = selectedEndpoint;
      const result: ConnectionConnectResult = await desktopRequest("connections.connect", payload, parseConnectionConnectResult);
      setApiKeyDraft("");
      setConnectedProviders((current) => [
        ...current.filter((item) => item.connection_id !== result.record.connection_id),
        result.record,
      ]);
      setActiveConnectionId(result.record.connection_id);
      setEndpoint(result.record.endpoint);
      setAvailableModels((current) => [
        ...current.filter((item) => item.connection_id !== result.record.connection_id),
        ...result.models,
      ]);
      setModelCatalogState(result.model_catalog_state);
      setConnectionSaved(true);
      setConnectionMode("api-key");
      if (result.models[0]) {
        const selected = result.models[0];
        if (conversation) {
          const switched = await switchConversationModel(selected, result.models, true);
          if (!switched) {
            // Keep the canonical conversation/model pair authoritative.  A
            // failed switch must not leave the renderer claiming Live while
            // the backend would still invoke the old preview model.
            setModelId(conversation.conversation.model_id);
            setMode("mock");
          }
        } else {
          setModelId(selected.model_id);
          setMode("live");
        }
      } else {
        setModelId("");
        setMode("mock");
      }
      const catalogNote = result.model_catalog_state === "CACHED"
        ? "dùng catalog đã cache"
        : result.model_catalog_state === "STALE"
          ? "dùng catalog cũ vì làm mới thất bại"
          : result.model_catalog_state === "UNAVAILABLE"
            ? "chưa đọc được catalog model"
            : "catalog mới";
      setConnectionNotice(result.discovery_error
        ? `${result.identity.provider_label} đã lưu khóa trong phiên này · ${catalogNote}. Bạn có thể làm mới sau.`
        : `${result.identity.provider_label} đã kết nối · ${result.models.length} model khả dụng · ${catalogNote} · khóa chỉ được giữ trong phiên này.`);
      setApiKeyDraft("");
    } catch (reason) {
      setConnectionNotice(reason instanceof Error ? reason.message : "Không thể kết nối nhà cung cấp");
    } finally {
      setConnectionBusy(null);
    }
  }

  async function addImageFiles(files: File[]) {
    const candidates = files.filter((file) => SUPPORTED_IMAGE_MIME_TYPES.has(file.type.toLowerCase()));
    const unsupportedImage = files.some((file) => file.type.startsWith("image/") && !SUPPORTED_IMAGE_MIME_TYPES.has(file.type.toLowerCase()));
    if (unsupportedImage) setError("Only PNG, JPEG, GIF, and WebP images are supported.");
    if (candidates.length === 0) return;
    if (imageProcessingRef.current) {
      setError("An image is still being prepared. Wait for it to finish before adding another.");
      return;
    }
    imageProcessingRef.current = true;
    setImageProcessing(true);
    try {
      await readAndAddImageFiles(candidates, unsupportedImage);
    } finally {
      imageProcessingRef.current = false;
      setImageProcessing(false);
    }
  }

  async function readAndAddImageFiles(candidates: File[], unsupportedImage: boolean) {
    const selectedModel = findModelForConnectionAndId(
      availableModels,
      conversation?.conversation.connection_id ?? activeConnectionId,
      modelId,
    );
    if (mode === "live" && !selectedModel?.supports_vision) {
      setError("The selected model has no verified image-input capability. Choose an image-capable model first.");
      return;
    }
    const imageLimit = selectedModel?.max_image_inputs ?? 4;
    const availableSlots = imageLimit - pendingImages.length;
    if (availableSlots <= 0) {
      setError(`This model accepts up to ${imageLimit} images per message.`);
      return;
    }
    const tooManyImages = candidates.length > availableSlots;
    const accepted: PendingImage[] = [];
    let readFailure = false;
    let totalBytes = pendingImages.reduce((sum, item) => sum + item.sizeBytes, 0);
    for (const file of candidates.slice(0, availableSlots)) {
      if (file.size <= 0 || file.size > 8 * 1024 * 1024 || totalBytes + file.size > 8 * 1024 * 1024) {
        setError("Images must be non-empty and the combined size must be at most 8 MB.");
        continue;
      }
      const image = await new Promise<PendingImage | null>((resolve) => {
        const reader = new FileReader();
        reader.onerror = () => resolve(null);
        reader.onload = () => {
          const dataUrl = typeof reader.result === "string" ? reader.result : "";
          const separator = dataUrl.indexOf(",");
          if (separator < 0) {
            resolve(null);
            return;
          }
          resolve({
            id: crypto.randomUUID(),
            name: file.name || "image",
            mimeType: file.type,
            data: dataUrl.slice(separator + 1),
            dataUrl,
            sizeBytes: file.size,
          });
        };
        reader.readAsDataURL(file);
      });
      if (image) {
        accepted.push(image);
        totalBytes += image.sizeBytes;
      } else {
        readFailure = true;
      }
    }
    if (accepted.length > 0) {
      setPendingImages((current) => [...current, ...accepted].slice(0, 4));
    }
    setError(unsupportedImage
      ? "Only PNG, JPEG, GIF, and WebP images are supported."
      : readFailure
        ? "Could not read one of the selected images."
        : tooManyImages
          ? `This model accepts up to ${imageLimit} images per message. Extra images were not added.`
        : null);
  }

  function removePendingImage(id: string) {
    setPendingImages((current) => current.filter((item) => item.id !== id));
  }

  async function openComposerModelMenu() {
    if (!connectionSaved || availableModels.length === 0) {
      setSettingsSection("Models");
      setConnectionNotice(connectionSaved ? "The provider is connected, but no verified model catalog is available yet." : null);
      openDestination("settings");
      return;
    }
    const opening = composerMenuOpen !== "model";
    setComposerModelView("root");
    setComposerModelQuery("");
    setComposerMenuOpen(opening ? "model" : null);
  }

  async function switchConversationModel(
    model: ModelDescriptor,
    catalog: ModelDescriptor[] = availableModels,
    allowFreshConnection = false,
  ): Promise<boolean> {
    if ((!allowFreshConnection && !connectionSaved) || !catalog.some((item) => item.connection_id === model.connection_id && item.model_id === model.model_id) || busy || subagentBusy || hasPendingToolApproval) return false;
    if (pendingImages.length > 0 && !model.supports_vision) {
      setError("Remove the attached images or choose an image-capable model before switching.");
      return false;
    }
    const imageLimit = model.max_image_inputs ?? 4;
    if (pendingImages.length > imageLimit) {
      setError(`This model accepts up to ${imageLimit} images. Remove extra images before switching.`);
      return false;
    }
    if (!conversation) {
      const selectedConnection = connectedProviders.find((item) => item.connection_id === model.connection_id);
      setModelId(model.model_id);
      setActiveConnectionId(model.connection_id);
      if (selectedConnection) setEndpoint(selectedConnection.endpoint);
      setMode("live");
      setComposerMenuOpen(null);
      setComposerModelView("root");
      return true;
    }
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
      setActiveConnectionId(record.connection_id);
      const selectedConnection = connectedProviders.find((item) => item.connection_id === record.connection_id);
      if (selectedConnection) setEndpoint(selectedConnection.endpoint);
      setMode("live");
      setConnectionSaved(true);
      setComposerMenuOpen(null);
      setComposerModelView("root");
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The model could not be selected");
      return false;
    }
  }

  async function loadConversations(): Promise<Conversation[]> {
    const records = await desktopRequest("conversations.list", {}, parseConversationList);
    const sorted = [...records].sort((left, right) => right.updated_at_ms - left.updated_at_ms);
    setConversations(sorted);
    return sorted;
  }

  function openCommandPalette() {
    commandPaletteOpenerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setCommandSearchQuery("");
    setCommandHighlight(0);
    setCommandPaletteOpen(true);
    window.requestAnimationFrame(() => commandPaletteInputRef.current?.focus());
  }

  function openQuickOpen() {
    setCommandPaletteOpen(false);
    setSearchQuery("");
    setSearchOpen(true);
    window.requestAnimationFrame(() => globalSearchRef.current?.focus());
  }

  function executeWorkbenchCommand(commandId: string) {
    commandPaletteOpenerRef.current = null;
    setCommandPaletteOpen(false);
    switch (commandId) {
      case "command-palette":
        openCommandPalette();
        break;
      case "quick-open":
        openQuickOpen();
        break;
      case "new-task":
        startNewTask();
        break;
      case "toggle-navigator":
        setSidebarCollapsed((current) => !current);
        break;
      case "find-in-project":
      case "open-files":
        openDestination("chat");
        setWorkTab("files");
        setWorkPanelOpen(true);
        window.requestAnimationFrame(() => searchRef.current?.focus());
        break;
      case "open-map":
        openDestination("chat");
        setWorkTab("map");
        setWorkPanelOpen(true);
        break;
      case "open-activity":
        openDestination("chat");
        setWorkTab("activity");
        setWorkPanelOpen(true);
        break;
      case "send":
        void sendMessage();
        break;
      case "stop-task":
        if (activeDesktopRun) void cancelDesktopRun(activeDesktopRun);
        else if (subagentRunId) void cancelSubagents();
        break;
      case "preferences":
        openDestination("settings");
        break;
      case "focus-next-pane":
        moveFocusToWorkbenchPane(false);
        break;
      case "focus-previous-pane":
        moveFocusToWorkbenchPane(true);
        break;
    }
  }

  function renderCommandPalette() {
    if (!workbenchV2Enabled || !commandPaletteOpen) return null;
    const query = commandSearchQuery.trim().toLocaleLowerCase();
    const commands = [
      ...workbenchCommands.filter((command) => command.id !== "command-palette").map((command) => ({
        id: command.id,
        label: command.label,
        keys: isMacOS ? command.keys.mac : command.keys.other,
      })),
      { id: "open-files", label: "Open Project Explorer", keys: "" },
      { id: "open-map", label: "Open Focus Graph", keys: "" },
      { id: "open-activity", label: "Open Task Activity", keys: "" },
    ].filter((command) => !query || command.label.toLocaleLowerCase().includes(query));
    const highlight = commands.length > 0 ? commandHighlight % commands.length : 0;
    return (
      <div className="command-palette-backdrop" onMouseDown={(event) => {
        if (event.target === event.currentTarget) setCommandPaletteOpen(false);
      }}>
        <section
          className="command-palette"
          role="dialog"
          aria-modal="true"
          aria-label="Command Palette"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              setCommandPaletteOpen(false);
            } else if (event.key === "ArrowDown" && commands.length > 0) {
              event.preventDefault();
              setCommandHighlight((current) => (current + 1) % commands.length);
            } else if (event.key === "ArrowUp" && commands.length > 0) {
              event.preventDefault();
              setCommandHighlight((current) => (current - 1 + commands.length) % commands.length);
            } else if (event.key === "Enter" && commands.length > 0) {
              event.preventDefault();
              executeWorkbenchCommand(commands[highlight].id);
            } else if (event.key === "Tab") {
              const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]),button:not([disabled])"));
              const first = controls[0];
              const last = controls.at(-1);
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }
          }}
        >
          <label className="command-palette-search">
            <Icon name="search" size={16} />
            <input
              ref={commandPaletteInputRef}
              role="combobox"
              aria-expanded="true"
              aria-controls="workbench-command-results"
              aria-label="Search commands"
              placeholder="Type a command…"
              value={commandSearchQuery}
              onChange={(event) => { setCommandSearchQuery(event.target.value); setCommandHighlight(0); }}
            />
            <kbd>Esc</kbd>
          </label>
          <div id="workbench-command-results" className="command-palette-results" role="listbox" aria-label="Commands">
            {commands.map((command, index) => <button
              key={command.id}
              type="button"
              role="option"
              aria-selected={index === highlight}
              className={index === highlight ? "active" : ""}
              onMouseEnter={() => setCommandHighlight(index)}
              onClick={() => executeWorkbenchCommand(command.id)}
            >
              <span>{command.label}</span>{command.keys && <kbd>{command.keys}</kbd>}
            </button>)}
            {commands.length === 0 && <p className="command-palette-empty">No matching commands.</p>}
          </div>
        </section>
      </div>
    );
  }

  function moveFocusToWorkbenchPane(reverse: boolean) {
    const panes = ["navigator", "work-surface", "inspector", "composer"]
      .map((name) => document.querySelector<HTMLElement>(`[data-workbench-pane="${name}"]`))
      .filter((pane): pane is HTMLElement => pane !== null && pane.getClientRects().length > 0 && !pane.closest("[inert]"));
    if (panes.length === 0) return;
    const focusedPaneIndex = panes.findIndex((pane) => pane === document.activeElement);
    const activeIndex = focusedPaneIndex >= 0
      ? focusedPaneIndex
      : panes.findIndex((pane) => pane.contains(document.activeElement));
    const nextIndex = activeIndex < 0
      ? (reverse ? panes.length - 1 : 0)
      : (activeIndex + (reverse ? -1 : 1) + panes.length) % panes.length;
    const pane = panes[nextIndex];
    const target = pane.matches("[contenteditable=true]")
      ? pane
      : pane.querySelector<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [role="textbox"], [tabindex="0"]')
        ?? pane;
    target.focus();
  }

  function updateQueuedPrompts(next: QueuedPrompt[]) {
    runQueueRef.current = next;
    setQueuedPrompts(next);
    if (next.length === 0) setQueuedPromptsPaused(false);
  }

  function enqueuePrompt(prompt: QueuedPrompt) {
    if (runQueueRef.current.length >= 4) {
      setError("The task queue is full. Wait for a queued message to start, then try again.");
      return false;
    }
    updateQueuedPrompts([...runQueueRef.current, prompt]);
    setError(null);
    return true;
  }

  async function startDesktopRun(prompt: QueuedPrompt): Promise<boolean> {
    setBusy(true);
    try {
      setError(null);
      const result = await desktopRequest("runs.start", {
        conversation_id: prompt.conversationId,
        message: prompt.message,
        mode: prompt.mode,
        reasoning_effort: prompt.reasoningEffort,
        attachments: prompt.attachments.map((item) => ({
          name: item.name,
          mime_type: item.mimeType,
          data: item.data,
        })),
      }, parseDesktopRunStatus);
      setDesktopRunStatuses((current) => ({ ...current, [prompt.conversationId]: result }));
      return true;
    } catch (reason) {
      const detail = describeError(reason, "The task could not be started");
      setError(detail);
      if (activeConversationIdRef.current === prompt.conversationId) {
        setMessage(prompt.message);
        setPendingImages(prompt.attachments);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function cancelDesktopRun(run: DesktopRunStatus) {
    try {
      setError(null);
      const result = await desktopRequest("runs.cancel", {
        run_id: run.run_id,
        conversation_id: run.conversation_id,
      }, parseDesktopRunStatus);
      setDesktopRunStatuses((current) => ({ ...current, [run.conversation_id]: result }));
    } catch (reason) {
      setError(describeError(reason, "The task could not be stopped"));
    }
  }

  function startNewTask() {
    if (!workspace || conversationActionBusy || projectSwitcherBusy) return;
    conversationLoadRevision.current += 1;
    setConversationLoading(false);
    setConversationError(null);
    openDestination("chat");
    setActiveConversationId(null);
    setConversation(null);
    setConversationInspection(null);
    setLastPromptCacheUsage(null);
    setSubagentResult(null);
    setSubagentRunId(null);
    setSubagentStatus(null);
    setSubagentEvents([]);
    setSubagentGraph(null);
    setReconciliationNotice(null);
    setError(null);
    setComposerMenuOpen(null);
    setComposerModelView("root");
    setComposerModelQuery("");
    const selectedModel = findModelForConnectionAndId(availableModels, activeConnectionId, modelId);
    setMode(connectionSaved && selectedModel ? "live" : "mock");
  }

  async function createConversation(firstMessage: string): Promise<string | null> {
    if (!workspace) return null;
    const id = `desktop-${crypto.randomUUID()}`;
    const selectedModel = availableModels.find(
      (item) => item.connection_id === activeConnectionId && item.model_id === modelId,
    );
    const record = await desktopRequest("conversations.create", {
      conversation_id: id,
      title: firstMessage.replace(/\s+/g, " ").slice(0, 72) || "Image conversation",
      connection_id: selectedModel?.connection_id ?? "preview",
      model_id: selectedModel?.model_id ?? "preview-model",
    }, parseConversationRecordResult);
    setConversations((current) => [record, ...current.filter((item) => item.conversation_id !== id)]);
    setActiveConversationId(id);
    openDestination("chat");
    return id;
  }

  async function selectConversation(id: string) {
    if (conversationActionBusy || projectSwitcherBusy) return false;
    openDestination("chat");
    setActiveConversationId(id);
    setConversation(null);
    setConversationInspection(null);
    setLastPromptCacheUsage(null);
    setSubagentResult(null);
    setSubagentRunId(null);
    setSubagentStatus(null);
    setSubagentEvents([]);
    setSubagentGraph(null);
    setPendingImages([]);
    setReconciliationNotice(null);
    return loadConversation(id);
  }

  async function inspectConversation(id: string, tab: WorkTab) {
    setSessionMenuOpen(null);
    if (!await selectConversation(id)) return;
    setWorkTab(tab);
    setWorkPanelOpen(true);
  }

  async function loadConversation(id: string, preserveMode = false) {
    const revision = ++conversationLoadRevision.current;
    setConversationLoading(true);
    setConversationError(null);
    try {
      const [snapshot, inspection, runStatus] = await Promise.all([
        desktopRequest("conversations.read", { conversation_id: id }, parseConversationSnapshot),
        desktopRequest("conversations.inspect", { conversation_id: id }, parseConversationInspection),
        desktopRequest("runs.inspect", { conversation_id: id }, parseDesktopRunStatus).catch(() => null),
      ]);
      if (revision !== conversationLoadRevision.current) return false;
      setConversation(snapshot);
      setConversationInspection(inspection);
      if (runStatus) setDesktopRunStatuses((current) => ({ ...current, [id]: runStatus }));
      setModelId(snapshot.conversation.model_id);
      const selectedConnection = connectedProviders.find(
        (item) => item.connection_id === snapshot.conversation.connection_id,
      );
      if (selectedConnection) {
        setActiveConnectionId(selectedConnection.connection_id);
        setEndpoint(selectedConnection.endpoint);
      }
      if (!preserveMode) {
        const hasLiveCatalogEntry = availableModels.some(
          (item) => item.connection_id === snapshot.conversation.connection_id && item.model_id === snapshot.conversation.model_id,
        );
        setMode(connectionSaved && hasLiveCatalogEntry ? "live" : "mock");
      }
      return true;
    } catch (reason) {
      if (revision !== conversationLoadRevision.current) return false;
      setConversation(null);
      setConversationInspection(null);
      const detail = describeError(reason, "Unable to open this conversation");
      setConversationError(detail);
      setError(detail);
      return false;
    } finally {
      if (revision === conversationLoadRevision.current) setConversationLoading(false);
    }
  }

  async function resolveToolApproval(approvalId: string, decision: "approve" | "deny") {
    const inspection = conversationInspection;
    if (!inspection || busy || approvalBusyId !== null) return;
    const approval = inspection.approvals.find((item) => item.approval_id === approvalId);
    if (!approval?.can_resolve) return;
    setApprovalBusyId(approvalId);
    setError(null);
    try {
      const result = await desktopRequest("approvals.resolve", {
        conversation_id: inspection.conversation_id,
        approval_id: approvalId,
        decision,
        expected_revision: inspection.revision,
      }, parseApprovalResolutionResult);
      setConversation(result.snapshot);
      await loadConversation(inspection.conversation_id);
      await loadConversations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The tool approval could not be resolved");
      await loadConversation(inspection.conversation_id);
    } finally {
      setApprovalBusyId(null);
    }
  }

  async function reconcileAmbiguousToolCall(callId: string) {
    const inspection = conversationInspection;
    if (!inspection || busy || approvalBusyId !== null) return;
    const approval = inspection.approvals.find((item) => item.approval_id === callId);
    if (approval?.status !== "AMBIGUOUS" || !approval.can_reconcile) return;
    setApprovalBusyId(callId);
    setError(null);
    try {
      await desktopRequest("tool_calls.reconcile", {
        conversation_id: inspection.conversation_id,
        call_id: callId,
        confirmed_not_applied: true,
        expected_revision: inspection.revision,
      }, parseToolCallReconciliationResult);
      setReconciliationNotice(
        "Your confirmation was recorded. AEGIS did not independently verify or undo the external action.",
      );
      await loadConversation(inspection.conversation_id);
      await loadConversations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The unresolved tool call could not be reconciled");
      await loadConversation(inspection.conversation_id);
    } finally {
      setApprovalBusyId(null);
    }
  }

  async function sendMessage() {
    const text = message.trim();
    if (imageProcessingRef.current) {
      setError("Wait for the attached image to finish preparing before sending.");
      return;
    }
    if ((!text && pendingImages.length === 0) || conversationActionBusy || projectSwitcherBusy || conversationLoading || conversationError) return;
    const liveReady = connectionSaved && Boolean(findModelForConnectionAndId(
      availableModels,
      conversation?.conversation.connection_id ?? activeConnectionId,
      conversation?.conversation.model_id ?? modelId,
    ));
    if (mode === "live" && !liveReady) {
      setConnectionNotice(connectionSaved ? "Connect a provider model before using live mode." : "Connect an API key before using a live provider.");
      setSettingsSection("Models");
      openDestination("settings");
      return;
    }
    const selectedModel = findModelForConnectionAndId(
      availableModels,
      conversation?.conversation.connection_id ?? activeConnectionId,
      conversation?.conversation.model_id ?? modelId,
    );
    if (mode === "live" && pendingImages.length > 0 && !selectedModel?.supports_vision) {
      setError("The selected model has no verified image-input capability. Choose an image-capable model first.");
      return;
    }
    let conversationId = activeConversationId;
    if (!conversationId) conversationId = await createConversation(text || "Image task");
    if (!conversationId) return;
    const selectedReasoning = reasoningSelectionForModel(selectedModel, thinkingMode);
    const prompt: QueuedPrompt = {
      id: crypto.randomUUID(),
      conversationId,
      message: text,
      mode,
      reasoningEffort: selectedReasoning === "Auto" ? "auto" : selectedReasoning,
      attachments: [...pendingImages],
    };
    if (!workbenchV2Enabled) {
      setBusy(true);
      try {
        setError(null);
        const result = await desktopRequest("conversations.send", {
          conversation_id: conversationId,
          message: text,
          mode,
          reasoning_effort: prompt.reasoningEffort,
          attachments: prompt.attachments.map((item) => ({ name: item.name, mime_type: item.mimeType, data: item.data })),
        }, parseConversationSendResult);
        setMessage("");
        setPendingImages([]);
        setConversation(result.snapshot);
        const inspection = await desktopRequest("conversations.inspect", { conversation_id: conversationId }, parseConversationInspection);
        setConversationInspection(inspection);
        void loadConversations();
      } catch (reason) {
        setError(describeError(reason, "The message could not be sent"));
      } finally {
        setBusy(false);
      }
      return;
    }
    if (activeDesktopRun) {
      if (enqueuePrompt(prompt)) {
        setMessage("");
        setPendingImages([]);
      }
      return;
    }
    if (hasPendingToolApproval) {
      setError("Resolve the pending approval before starting another task message.");
      return;
    }
    if (await startDesktopRun(prompt)) {
      setMessage("");
      setPendingImages([]);
      await loadConversation(conversationId, true);
      void loadConversations();
    }
  }

  async function loadMemoriesForFile(node: { path: string | null }): Promise<MemoryRecord[]> {
    if (!node.path) return [];
    return desktopRequest("memory.search", { query: node.path, top_k: 6, scope_kind: "USER_PRIVATE" }, parseMemorySearchResult);
  }

  async function openSelectedFileExternally() {
    if (!selectedFile || !hasNativeWindow()) return;
    setFileOpenNotice(null);
    try {
      const result = await desktopRequest("workspace.open_external", { relative_path: selectedFile }, parseWorkspaceOpenExternalResult);
      setFileOpenNotice(result.requested ? `Asked the operating system to open ${result.relative_path}.` : null);
    } catch (reason) {
      setFileReadError(describeError(reason, "The operating system could not open this file"));
    }
  }

  async function runSubagents() {
    const task = message.trim() || "Inspect this workspace in parallel and summarize the relevant evidence.";
    if (mode !== "live" || !parallelWorkersEnabled || conversationActionBusy || projectSwitcherBusy || conversationLoading || hasPendingToolApproval || !activeConversationId || !runtimeReady || !connectionSaved || !findModelForConnectionAndId(availableModels, conversation?.conversation.connection_id ?? activeConnectionId, modelId)) return;
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
  const selectedProject = activeProjectId
    ? projects.find((item) => item.project_id === activeProjectId) ?? null
    : null;
  const activeProjectPath = activeProjectId
    ? selectedProject?.path ?? workspace?.workspace_path ?? null
    : null;
  const activeProjectName = selectedProject?.name ?? (activeProjectId ? workspaceName(activeProjectPath) : null);
  const activeTitle = conversation?.conversation.title ?? "New thread";
  const displayActiveTitle = activeTitle === "New thread" ? "New task" : activeTitle;
  const topbarTitle = truncateTopbarTitle(displayActiveTitle);
  const activeConversation = conversations.find((item) => item.conversation_id === activeConversationId);
  const sidebarConversations = useMemo(() => conversations.filter((item) => {
    return !isUnstartedConversation(item);
  }).sort((left, right) => {
    if (sessionSort === "name") return left.title.localeCompare(right.title);
    if (sessionSort === "oldest") return left.created_at_ms - right.created_at_ms;
    if (sessionSort === "created") return right.created_at_ms - left.created_at_ms;
    return right.updated_at_ms - left.updated_at_ms;
  }), [conversations, sessionSort]);
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
    const items: Array<{ icon: IconName; title: string; detail: string; timestamp: number }> = [];
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
  }, [conversation]);
  const sidebarWidthMax = useMemo(() => {
    // Below the compact breakpoint the panel is a fixed overlay, so it
    // does not consume grid width and must not falsely clamp the sidebar to
    // its minimum while the panel is open.
    const panelOverlays = viewportWidth <= WORK_PANEL_OVERLAY_BREAKPOINT;
    const panelWidth = destination === "chat" && workPanelOpen && !panelOverlays ? workPanelWidth : 0;
    return Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, responsiveSidebarMax(viewportWidth), viewportWidth - 450 - panelWidth - 1));
  }, [destination, viewportWidth, workPanelOpen, workPanelWidth]);

  useEffect(() => {
    const responsiveMax = Math.min(sidebarWidthMax, responsiveSidebarMax(viewportWidth));
    if (sidebarWidth > responsiveMax) setSidebarWidth(responsiveMax);
  }, [sidebarWidth, sidebarWidthMax, viewportWidth]);

  const workPanelWidthMax = useMemo(() => {
    const leftWidth = sidebarCollapsed ? 0 : sidebarWidth;
    const compactViewport = viewportWidth < leftWidth + 450 + WORK_PANEL_WIDTH_MIN;
    const available = compactViewport ? viewportWidth - leftWidth : viewportWidth - leftWidth - 450;
    return Math.max(WORK_PANEL_WIDTH_MIN, Math.min(WORK_PANEL_WIDTH_MAX, available));
  }, [sidebarCollapsed, sidebarWidth, viewportWidth]);

  function openDestination(next: Destination) {
    setDestination(next);
    if (viewportWidth <= 1100) setSidebarCollapsed(true);
    setSearchOpen(false);
    setSessionSortOpen(false);
    setSessionMenuOpen(null);
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
    const sessionResults = conversations.filter((item) => !isUnstartedConversation(item) && matches(item.title)).slice(0, 6);
    const fileResults = (sourceSnapshot?.files ?? []).filter((item) => matches(item.relative_path)).slice(0, 8);
    const destinations: Array<[string, string, IconName]> = [
      ["Settings", "settings", "settings"],
      ["Utilities", "plugins", "plug"],
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
            <button className="search-item active" type="button" role="option" disabled={!workspace || conversationActionBusy || projectSwitcherBusy} onClick={startNewTask}><Icon name="new-chat" size={15} /><span className="search-item-title">New chat</span></button>
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

  function projectSwitcherPositionFor(trigger: HTMLElement) {
    const bounds = trigger.getBoundingClientRect();
    const menuWidth = Math.min(280, Math.max(0, window.innerWidth - 24));
    const menuHeight = Math.min(420, Math.max(0, window.innerHeight - 24));
    const maxLeft = Math.max(12, window.innerWidth - menuWidth - 12);
    const maxTop = Math.max(12, window.innerHeight - menuHeight - 12);
    return {
      left: Math.max(12, Math.min(Math.max(bounds.right + 8, sidebarWidth + 8), maxLeft)),
      top: Math.max(12, Math.min(bounds.bottom + 8, maxTop)),
    };
  }

  function prepareProjectSwitcher(trigger: HTMLElement) {
    projectSwitcherOpenerRef.current = trigger;
    setProjectSwitcherPosition(projectSwitcherPositionFor(trigger));
    setProjectSwitcherQuery("");
    setProjectSwitcherView("list");
  }

  function toggleProjectSwitcher(trigger: HTMLElement) {
    prepareProjectSwitcher(trigger);
    setProjectSwitcherOpen((current) => !current);
  }

  function openProjectSwitcher(trigger: HTMLElement | null) {
    if (!trigger) return;
    prepareProjectSwitcher(trigger);
    setProjectSwitcherOpen(true);
  }

  function renderProjectSwitcherMenu(menuClassName = "home-project-switcher-menu", menuStyle?: CSSProperties) {
    const currentPath = activeProjectPath ?? "";
    const knownProjects = [...projects];
    if (activeProjectId && currentPath && !knownProjects.some((project) => project.path === currentPath)) {
      knownProjects.unshift({
        project_id: activeProjectId,
        path: currentPath,
        name: activeProjectName ?? "Workspace",
        parent_path: currentPath.replace(/[\\/][^\\/]*$/, "") || currentPath,
        last_opened_at_ms: 0,
        open_count: 1,
        status: "AVAILABLE",
      });
    }
    const projectQuery = projectSwitcherQuery.trim().toLocaleLowerCase();
    const visibleProjects = knownProjects.filter((project) => !projectQuery || `${project.name} ${project.path}`.toLocaleLowerCase().includes(projectQuery));
    return (
      <div className={`${menuClassName} is-open`} style={menuStyle} role="menu" aria-label="Switch project" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        setProjectSwitcherOpen(false);
        projectSwitcherOpenerRef.current?.focus();
      }}>
        {projectSwitcherView === "clone" ? <div className="home-project-switcher-clone">
          <button className="home-project-switcher-item" type="button" role="menuitem" disabled={projectSwitcherBusy || projectSwitchBlocked} onClick={() => setProjectSwitcherView("list")}>
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
              disabled={projectSwitcherBusy || projectSwitchBlocked}
            />
          </label>
          <p className="home-project-switcher-clone-hint">Clone into the local AEGIS projects directory.</p>
          <button className="home-project-switcher-clone-submit" type="button" disabled={projectSwitcherBusy || projectSwitchBlocked || !projectCloneUrl.trim()} onClick={() => void cloneWorkspace()}>
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
              disabled={projectSwitcherBusy || projectSwitchBlocked}
            />
          </label>
          <div className="home-project-switcher-list">
            {visibleProjects.length > 0 ? visibleProjects.map((project) => <button className={`home-project-switcher-item ${project.path === currentPath ? "is-current is-active" : ""}`} type="button" role="menuitemradio" aria-checked={project.path === currentPath} key={project.project_id} disabled={projectSwitcherBusy || projectSwitchBlocked || project.status === "MISSING"} onClick={() => void switchWorkspace(project.path)}>
              <Icon name="folder" size={14} />
              <span className="home-project-switcher-item-name">{project.name}</span>
              <span className="home-project-switcher-item-meta" title={project.path}>{project.status === "MISSING" ? "Missing" : shortPath(project.path, 26)}</span>
              {project.path === currentPath && <Icon name="check" size={14} />}
            </button>) : <div className="home-project-switcher-empty">{projectQuery ? "No matching projects" : "No projects yet"}</div>}
          </div>
          <div className="home-project-switcher-divider" />
          <button className="home-project-switcher-item" type="button" role="menuitem" disabled={projectSwitcherBusy || projectSwitchBlocked} onClick={() => { setProjectCloneUrl(""); setProjectSwitcherView("clone"); }}>
            <Icon name="branch" size={14} />
            <span className="home-project-switcher-item-name">Clone project</span>
          </button>
          <button className="home-project-switcher-item" type="button" role="menuitem" disabled={projectSwitcherBusy || projectSwitchBlocked} onClick={() => void openWorkspacePicker()}>
            <Icon name="new-project" size={14} />
            <span className="home-project-switcher-item-name">Open project</span>
          </button>
        </>}
      </div>
    );
  }

  function renderSidebar() {
    const renderSessionRow = (item: Conversation) => {
      const run = desktopRunStatuses[item.conversation_id] ?? null;
      const taskState = deriveTaskState(
        item.conversation_id === activeConversationId ? conversation : null,
        run,
      );
      const showTaskState = run !== null || item.conversation_id === activeConversationId && conversation !== null;
      return <div className={`session-row-wrap thread-item ${item.conversation_id === activeConversationId ? "active" : ""}`} data-sidebar-session-row key={item.conversation_id}>
        <button className="session-row thread-item-main" type="button" disabled={conversationActionBusy || projectSwitcherBusy} onClick={() => void selectConversation(item.conversation_id)}>
          <span className="session-info"><strong className="thread-item-title">{displaySessionTitle(item.title)}</strong><small>{formatTime(item.updated_at_ms)}</small></span>
          {showTaskState && <span className={`task-state-badge task-state-${taskState.toLocaleLowerCase()}`}>{taskStateLabel(taskState)}</span>}
        </button>
        <button className="session-row-action thread-item-more" type="button" disabled={conversationActionBusy || projectSwitcherBusy} onClick={() => setSessionMenuOpen((current) => current === item.conversation_id ? null : item.conversation_id)} aria-label={`More actions for ${displaySessionTitle(item.title)}`} title="More actions"><Icon name="more" size={14} /></button>
        {sessionMenuOpen === item.conversation_id && <div className="session-row-menu" role="menu">
          <button type="button" role="menuitem" onClick={() => void inspectConversation(item.conversation_id, "activity")}>Open activity</button>
          <button type="button" role="menuitem" onClick={() => void inspectConversation(item.conversation_id, "context")}>Inspect context</button>
        </div>}
      </div>;
    };
    return (
        <aside data-workbench-pane="navigator" className={`sidebar sidebar-surface ${sidebarCollapsed ? "collapsed" : ""}`} aria-label="Task navigation" inert={workPanelModal}>
        <div className="sidebar-header sidebar-brand" data-tauri-drag-region="true">
          <button className="brand no-drag" type="button" onClick={() => openDestination("chat")} aria-label="Home" title="Home">
            <AegisBrandLogo />
            {!sidebarCollapsed && <span>AEGIS</span>}
          </button>
          <div className="sidebar-header-actions no-drag">
            {!sidebarCollapsed && <button className="sidebar-toolbar-button sidebar-header-action" type="button" onClick={() => { setSearchQuery(""); setSearchOpen(true); window.requestAnimationFrame(() => globalSearchRef.current?.focus()); }} aria-label="Search" title="Search"><Icon name="search" size={16} /></button>}
            {sidebarCollapsed && <button className="new-task compact-new-task" type="button" onClick={startNewTask} disabled={!workspace || conversationActionBusy || projectSwitcherBusy} aria-label="New chat" title="New chat"><Icon name="plus" size={15} /></button>}
            <button className="sidebar-toggle" type="button" onClick={() => setSidebarCollapsed((current) => !current)} aria-label="Toggle sidebar"><Icon name="sidebar" size={15} /></button>
          </div>
        </div>
        <div className="sidebar-body sidebar-scroll no-drag">
          {!sidebarCollapsed && <nav className="reference-primary-nav" aria-label="Primary navigation">
            <button className="reference-nav-item new-chat-command" type="button" onClick={startNewTask} disabled={!workspace || conversationActionBusy || projectSwitcherBusy}><span className="reference-nav-icon"><Icon name="new-chat" size={17} /></span><span>New chat</span></button>
            <button className={`reference-nav-item ${destination === "plugins" ? "active" : ""}`} type="button" onClick={() => openDestination("plugins")}><span className="reference-nav-icon"><Icon name="plug" size={17} /></span><span>Utilities</span></button>
          </nav>}
          <div className="sidebar-group project-group">
            {!sidebarCollapsed && <div className="sidebar-list-toolbar sidebar-label-with-action" data-sidebar-section="projects"><span className="sidebar-list-label sidebar-label">Project</span><span className="project-switcher-anchor"><button ref={projectSwitcherTriggerRef} className="sidebar-toolbar-button project-switcher-trigger" type="button" onClick={(event) => toggleProjectSwitcher(event.currentTarget)} aria-label={activeProjectId ? "Switch project" : "Choose project"} aria-haspopup="menu" aria-expanded={projectSwitcherOpen} title={activeProjectId ? "Switch project" : "Choose project"}><Icon name="new-project" size={14} /></button></span></div>}
            {activeProjectId ? <div className="project-row-wrap sidebar-session-group-header">
              <button className="project-row sidebar-session-group-title project-switcher-trigger" type="button" onClick={(event) => toggleProjectSwitcher(event.currentTarget)} title={activeProjectPath ?? "Switch project"} aria-haspopup="menu" aria-expanded={projectSwitcherOpen}>
                <span className="project-chevron"><Icon name="chevron-down" size={13} /></span><span className="project-icon"><Icon name="folder" size={14} /></span>{!sidebarCollapsed && <><span className="project-name">{activeProjectName ?? "Project"}</span><span className="project-active-dot" aria-label="Active" title="Active" /></>}
              </button>
              {!sidebarCollapsed && <span className="project-menu-anchor"><button className="project-row-action project-menu-button" type="button" onClick={() => setProjectMenuOpen((current) => !current)} aria-label="More project actions" aria-haspopup="menu" aria-expanded={projectMenuOpen} title="More project actions"><Icon name="more" size={14} /></button>{projectMenuOpen && <div className="project-row-menu" role="menu"><span className="sidebar-mini-menu-title">Project actions</span><button type="button" role="menuitem" onClick={() => { setProjectMenuOpen(false); openProjectSwitcher(projectSwitcherTriggerRef.current); }}>Switch project</button><button type="button" role="menuitem" onClick={() => { setProjectMenuOpen(false); setSettingsSection("Projects"); openDestination("settings"); }}>Project settings</button></div>}</span>}
            </div> : !sidebarCollapsed && <button className="project-row project-row-empty" type="button" onClick={(event) => toggleProjectSwitcher(event.currentTarget)} aria-haspopup="menu" aria-expanded={projectSwitcherOpen}><Icon name="folder" size={14} /><span>Choose a project</span></button>}
          </div>
          <div className="sidebar-group project-group sidebar-session-group">
            {!sidebarCollapsed && <div className="sidebar-list-toolbar"><span className="sidebar-list-label sidebar-label">{activeProjectId ? "Project tasks" : "Local tasks"}</span></div>}
            {!sidebarCollapsed && <div className="sidebar-session-group-body"><div className="sidebar-session-group-list">{sidebarConversations.length === 0 ? <div className="project-subrow">No tasks yet</div> : sidebarConversations.map(renderSessionRow)}</div></div>}
          </div>
        </div>
        <div className="sidebar-footer sidebar-bottom no-drag">
          <div className="footer-actions">
            <button className={`footer-action utility-row ${destination === "settings" ? "active" : ""}`} type="button" onClick={() => openDestination("settings")} title="Settings" aria-label="Settings"><span><Icon name="settings" size={15} /></span>{!sidebarCollapsed && <span className="utility-label">Settings</span>}</button>
            {sidebarCollapsed && <button className={`footer-action utility-row ${destination === "plugins" ? "active" : ""}`} type="button" onClick={() => openDestination("plugins")} title="Utilities" aria-label="Utilities"><span><Icon name="plug" size={15} /></span></button>}
          </div>
          {!sidebarCollapsed && <div className="footer-build sidebar-version" aria-hidden="true" />}
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

    const isHome = !activeConversationId || (!conversationLoading && !conversationError && !conversation?.turns.length);
    const selectedModelForComposer = findModelForConnectionAndId(
      availableModels,
      conversation?.conversation.connection_id ?? activeConnectionId,
      modelId,
    );
    const conversationModelReady = conversation
      ? availableModels.some(
        (item) => item.connection_id === conversation.conversation.connection_id && item.model_id === conversation.conversation.model_id,
      )
      : selectedModelForComposer !== null;
    const modelReady = connectionSaved && conversationModelReady;
    const imageInputReady = mode === "mock" || selectedModelForComposer?.supports_vision === true;
    const permissionLabels: Record<DesktopSettings["permission_mode"], string> = {
      "Ask every time": "Ask",
      "Accept edits": "Workspace",
      Auto: "Auto",
    };
    const permissionOptions: SettingsSelectOption[] = [
      { value: "Ask every time", label: "Ask every time", description: "Confirm each local write or tool action." },
      { value: "Accept edits", label: "Workspace edits", description: "Allow non-destructive edits inside this project." },
      { value: "Auto", label: "Automatic", description: "Allow low-risk local actions; deletion stays blocked." },
    ];
    const contextTokenCount = conversationInspection?.context.token_count;
    const contextTokenBudget = conversationInspection?.context.token_budget;
    const contextReadout = contextUsage === "Used"
      ? `${contextTokenCount ?? "—"} tokens used`
      : contextTokenBudget !== null && contextTokenBudget !== undefined && contextTokenCount !== null && contextTokenCount !== undefined
        ? `${Math.max(0, contextTokenBudget - contextTokenCount)} tokens remaining`
        : "Context remaining unavailable";

    function renderModelControl() {
      if (!connectionSaved || availableModels.length === 0) {
        return (
          <button
            className="ct-model-button ct-model-unconfigured"
            type="button"
            onClick={() => { setSettingsSection("Models"); openDestination("settings"); }}
            title={!connectionSaved ? "Open Models to connect a provider" : "Open Models to load a verified model catalog"}
          >
            <Icon name="bot" size={14} />
            <span className="ct-model-label">{!connectionSaved ? "Connect provider" : "Load models"}</span>
            <Icon name="chevron-right" size={12} />
          </button>
        );
      }
      const modelOptions = availableModels.length > 0
        ? availableModels
        : [{
          connection_id: conversation?.conversation.connection_id ?? activeConnectionId ?? "preview",
          model_id: modelId,
          family: null,
          capabilities: [],
          context_limit: null,
          output_limit: null,
          source: "current",
          revision: 0,
          observed_at_ms: 0,
          reasoning_efforts: [],
          supports_vision: false,
          input_modalities: ["text"],
          max_image_inputs: null,
        } satisfies ModelDescriptor];
      const selectedModel = findModelForConnectionAndId(
        modelOptions,
        conversation?.conversation.connection_id ?? activeConnectionId,
        modelId,
      );
      const supportedThinking = reasoningChoices(selectedModel);
      const visibleThinkingMode = reasoningSelectionForModel(selectedModel, thinkingMode);
      const filteredModelOptions = modelOptions.filter((model) => model.model_id.toLocaleLowerCase().includes(composerModelQuery.trim().toLocaleLowerCase()));
      return (
        <div className="composer-menu-anchor ct-model-control">
          <button className={`ct-model-button ${composerMenuOpen === "model" ? "active" : ""}`} type="button" onClick={() => void openComposerModelMenu()} aria-haspopup="menu" aria-expanded={composerMenuOpen === "model"} title={`${selectedModel ? modelId : "Choose model"} · ${reasoningLabel(visibleThinkingMode)}`}>
            <Icon name="bot" size={14} /><span className="ct-model-label">{selectedModel ? modelId : "Choose model"}</span><span className="ct-model-reasoning">{reasoningLabel(visibleThinkingMode)}</span><Icon name="chevron-down" size={12} />
          </button>
          {composerMenuOpen === "model" && <div className="composer-menu composer-model-menu composer-model-thinking-menu ct-model-menu" role="menu" aria-label="Model and reasoning">
            {composerModelView === "root" ? <div className="composer-menu-root">
              <button className="composer-menu-entry" type="button" role="menuitem" aria-haspopup="menu" onClick={() => setComposerModelView("model")}><Icon name="bot" size={14} /><span className="composer-menu-entry-label">Model</span><span className="composer-menu-entry-value" title={modelId}>{modelId}</span><Icon name="chevron-right" size={14} /></button>
              <button className="composer-menu-entry" type="button" role="menuitem" aria-haspopup="menu" onClick={() => setComposerModelView("thinking")}><Icon name="spark" size={14} /><span className="composer-menu-entry-label">Reasoning</span><span className="composer-menu-entry-value">{reasoningLabel(visibleThinkingMode)}</span><Icon name="chevron-right" size={14} /></button>
            </div> : <>
              <button className="composer-menu-back" type="button" role="menuitem" onClick={() => setComposerModelView("root")}><Icon name="chevron-left" size={14} /><span>{composerModelView === "model" ? "Model" : "Reasoning"}</span></button>
              <div className="composer-menu-separator" />
              {composerModelView === "model" ? <>
                <label className="composer-model-search"><Icon name="search" size={13} /><input value={composerModelQuery} onChange={(event) => setComposerModelQuery(event.target.value)} placeholder="Search models" aria-label="Search models" /></label>
                <div className="composer-model-list">
                  {filteredModelOptions.map((model) => {
                    const selected = model.connection_id === (conversation?.conversation.connection_id ?? activeConnectionId)
                      && model.model_id === modelId;
                    const provider = connectedProviders.find((item) => item.connection_id === model.connection_id);
                    return <button key={`${model.connection_id}:${model.model_id}`} type="button" role="menuitemradio" aria-checked={selected} className={selected ? "active" : ""} onClick={() => void switchConversationModel(model)}><span className="composer-model-option-main"><span className="truncate">{model.model_id}</span><span className="composer-model-option-meta">{provider?.provider_kind ?? model.family ?? "Provider model"} · {model.supports_vision ? "image" : "image unverified"} · {model.reasoning_efforts.length > 0 ? "reasoning" : "effort unverified"}</span></span>{selected && <Icon name="check" size={13} />}</button>;
                  })}
                  {filteredModelOptions.length === 0 && <div className="composer-model-empty">No matching models</div>}
                </div>
                <button className="composer-menu-secondary" type="button" role="menuitem" onClick={() => { setComposerMenuOpen(null); setComposerModelView("root"); setDestination("settings"); setSettingsSection("Models"); }}>Configure model<Icon name="chevron-right" size={13} /></button>
              </> : <>
                <div className="composer-thinking-heading">Reasoning effort</div>
                <div className="composer-thinking-list">{supportedThinking.map((level) => <button key={level} className={`composer-plus-item ${visibleThinkingMode === level ? "active" : ""}`} type="button" role="menuitemradio" aria-checked={visibleThinkingMode === level} onClick={() => { void setThinkingMode(level); void saveSettingsPatch({ thinking_default: level }); setComposerMenuOpen(null); setComposerModelView("root"); }}>{reasoningLabel(level)}{visibleThinkingMode === level && <Icon name="check" size={13} />}</button>)}</div>
              </>}
            </>}
          </div>}
        </div>
      );
    }

    function renderComposer(home: boolean) {
      const approvalPending = !home && (conversationInspection?.approvals.some(
        (approval) => approval.status === "REQUESTED" || approval.status === "AMBIGUOUS",
      ) ?? false);
      const taskQueue = queuedPromptsPaused ? queuedPrompts : queuedPrompts.filter((item) => item.conversationId === activeConversationId);
      return (
        <div className={home ? "home-composer-wrap" : "composer-wrap"}>
          {home && homeActionsOpen && <div className="home-actions-menu" role="menu" aria-label="Workspace actions"><button type="button" role="menuitem" disabled={!activeProjectId} onClick={() => { setHomeActionsOpen(false); setMessage("Summarize this workspace"); }}><Icon name="spark" size={14} /><span>Summarize workspace</span></button><button type="button" role="menuitem" disabled={!activeProjectId} onClick={() => { setHomeActionsOpen(false); setMessage("Inspect the current project"); }}><Icon name="search" size={14} /><span>Inspect project</span></button></div>}
          {taskQueue.length > 0 && <div className={`task-queue ${queuedPromptsPaused ? "is-paused" : ""}`} aria-label="Queued task messages">
            {queuedPromptsPaused && <div className="task-queue-paused" role="status"><strong>Queue paused for review</strong><span>The previous task status is unavailable. These messages were not started; copy any you still need into a new task or clear the queue.</span></div>}
            {taskQueue.map((item, index) => <div className="task-queue-item" key={item.id}>
              <span className="task-queue-number">{index + 1}</span>
              <span className="task-queue-copy"><small>{item.conversationId === activeConversationId ? "This task" : conversations.find((conversation) => conversation.conversation_id === item.conversationId)?.title ?? "Another task"}</small>{item.message || "Image attachment"}</span>
              <button type="button" onClick={() => updateQueuedPrompts(runQueueRef.current.filter((queued) => queued.id !== item.id))} aria-label="Remove queued message" title="Remove from queue"><Icon name="close" size={13} /></button>
            </div>)}
            {queuedPromptsPaused ? <button className="task-queue-clear" type="button" onClick={() => updateQueuedPrompts([])}>Clear queued messages</button> : <p>Sent in order after the active task finishes.</p>}
          </div>}
          <div className={`composer-dock composer-dock-${home ? "home" : "docked"}`} data-composer-dock={home ? "home" : "docked"}>
            <div className="composer-stack">
              {composerAutocompleteMode && <ComposerAutocomplete mode={composerAutocompleteMode} items={composerSuggestions} highlight={composerAutocompleteHighlight} onHighlight={setComposerAutocompleteHighlight} onAccept={acceptComposerSuggestion} />}
              <div className="composer-shell">
                <input
                  ref={imageInputRef}
                  className="composer-image-input"
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/webp"
                  multiple
                  tabIndex={-1}
                  aria-hidden="true"
                  onChange={(event) => {
                    const files = Array.from(event.currentTarget.files ?? []);
                    event.currentTarget.value = "";
                    void addImageFiles(files);
                  }}
                />
                <ComposerInput
                  value={message}
                  placeholder={home && activeProjectName ? `Ask about ${activeProjectName}` : "Ask anything"}
                  disabled={conversationActionBusy || projectSwitcherBusy || conversationLoading || Boolean(conversationError) || !workspace}
                  enterToSend={enterToSend}
                  onChange={handleComposerChange}
                  onKeyDown={handleComposerKeyDown}
                  onImageFiles={(files) => void addImageFiles(files)}
                  onSubmit={() => void sendMessage()}
                />
                {pendingImages.length > 0 && <div className="composer-attachments" aria-label="Attached images">
                  {pendingImages.map((image) => <div className="composer-attachment" key={image.id}>
                    <img className="composer-attachment-thumb" src={image.dataUrl} alt="" />
                    <span title={image.name}>{image.name}</span>
                    <button type="button" className="composer-attachment-remove" onClick={() => removePendingImage(image.id)} aria-label={"Remove " + image.name} title="Remove image">×</button>
                  </div>)}
                </div>}
                <div className="composer-toolbar">
                  <div className="composer-left">
                    <div className="composer-plus"><button className="icon-btn icon-btn-square composer-tool" type="button" onClick={() => imageInputRef.current?.click()} aria-label="Attach image" title={imageInputReady ? "Attach image" : "Choose an image-capable model first"} disabled={conversationActionBusy || projectSwitcherBusy || approvalPending || !imageInputReady}><Icon name="plus" size={16} /></button></div>
                    {modelReady ? <button className="icon-btn mode-chip composer-mode-chip" type="button" onClick={() => { if (mode === "mock" && pendingImages.length > 0 && !selectedModelForComposer?.supports_vision) { setError("Choose an image-capable model before enabling live mode with an image attached."); return; } setComposerMenuOpen(null); setMode(mode === "mock" ? "live" : "mock"); }} aria-label={`Execution mode · ${mode === "mock" ? "demo" : "live provider"}`} title={mode === "mock" ? "Demo · simulated replies. Switch to Live" : "Live provider · switch to Demo"}><span className="composer-mode-chip-face"><Icon name="shield" size={14} /><span className="composer-mode-chip-label text-sm">{mode === "mock" ? "Demo" : "Live"}</span></span></button> : <span className="mode-chip composer-mode-chip composer-mode-chip-static" title="Demo uses simulated replies. Connect a provider to use a live model."><span className="composer-mode-chip-face"><Icon name="shield" size={14} /><span className="composer-mode-chip-label text-sm">{mode === "mock" ? "Demo" : "Live unavailable"}</span></span></span>}
                    <div className="composer-menu-anchor composer-permission-anchor">
                      <button className={`icon-btn permission-chip ${composerMenuOpen === "permission" ? "active" : ""}`} type="button" onClick={() => setComposerMenuOpen((current) => current === "permission" ? null : "permission")} aria-haspopup="listbox" aria-expanded={composerMenuOpen === "permission"} aria-label={`Local access · ${permissionMode}`} title="Choose local access level"><Icon name="shield" size={14} /><span>{permissionLabels[permissionMode]}</span><Icon name="chevron-down" size={12} /></button>
                      {composerMenuOpen === "permission" && <div className="composer-menu composer-permission-menu" role="listbox" aria-label="Local access level">
                        <div className="composer-permission-heading">Local access</div>
                        {permissionOptions.map((option) => <button key={option.value} type="button" role="option" aria-selected={option.value === permissionMode} className={option.value === permissionMode ? "active" : ""} onClick={() => { void saveSettingsPatch({ permission_mode: option.value as DesktopSettings["permission_mode"] }); setComposerMenuOpen(null); }}><span className="composer-permission-copy"><strong>{option.label}</strong><small>{option.description}</small></span>{option.value === permissionMode && <Icon name="check" size={13} />}</button>)}
                        <div className="composer-permission-note">Only the selected project is in scope. Destructive deletion is not available from the desktop host.</div>
                      </div>}
                    </div>
                    <div className="composer-model-slot">{renderModelControl()}</div>
                  </div>
                  <div className="composer-right">
                    <button className="icon-btn composer-tool composer-enhance-btn" type="button" onClick={() => conversation?.turns.length ? void runSubagents() : setHomeActionsOpen((current) => !current)} disabled={conversation?.turns.length ? mode !== "live" || conversationActionBusy || conversationLoading || approvalPending || !runtimeReady || !modelReady || !activeConversationId || !parallelWorkersEnabled : !activeProjectId || conversationActionBusy} aria-label={conversation?.turns.length ? "Run parallel workers" : "Workspace actions"} aria-expanded={conversation?.turns.length ? undefined : homeActionsOpen} title={conversation?.turns.length ? "Run parallel workers with your live model" : "Workspace actions"}><Icon name="spark" size={15} /></button>
                    {activeDesktopRun && activeDesktopRun.status !== "WAITING_PERMISSION" && <button className="run-stop-button" type="button" onClick={() => void cancelDesktopRun(activeDesktopRun)} disabled={activeDesktopRun.status === "CANCELLING" || !activeDesktopRun.cancel_supported} aria-label={activeDesktopRun.status === "CANCELLING" ? "Stop requested" : "Stop active task"} title={activeDesktopRun.status === "CANCELLING" ? "Waiting for a safe stop point" : "Request a safe stop"}>{activeDesktopRun.status === "CANCELLING" ? "Stopping…" : "Stop"}</button>}
                    <button className="send-btn send-button" type="button" onClick={() => void sendMessage()} aria-label={imageProcessing ? "Preparing image" : activeDesktopRun ? "Queue message" : "Send message"} title={imageProcessing ? "Preparing image…" : activeDesktopRun ? "Queue after the active task" : "Send message"} aria-busy={imageProcessing} disabled={conversationActionBusy || projectSwitcherBusy || conversationLoading || Boolean(conversationError) || imageProcessing || (!message.trim() && pendingImages.length === 0) || !workspace}>{busy || subagentBusy ? <span className="send-loading" /> : <Icon name={activeDesktopRun ? "plus" : "arrow-up"} size={16} />}</button>
                  </div>
                </div>
              </div>
              <div className="composer-status" aria-label="Workspace environment">
                <button className="composer-under-action" type="button" onClick={(event) => toggleProjectSwitcher(event.currentTarget)} aria-haspopup="menu" aria-expanded={projectSwitcherOpen}><Icon name="folder" size={15} /><span>{activeProjectName ?? "Choose project"}</span></button>
                <span className="composer-under-spacer" />
                {activeDesktopRun ? <span className="composer-working-status" role="status"><ThinkingOrb state="working" size={20} aria-hidden="true" /><span>{taskStateLabel(activeDesktopRun.status)}</span></span> : busy || subagentBusy ? <span className="composer-working-status" role="status"><ThinkingOrb state="working" size={20} aria-hidden="true" /><span>{subagentBusy ? "Workers starting" : "Starting task"}</span></span> : <span className="composer-runtime-state"><Icon name="server" size={13} /><span>{runtimeReady ? "Local" : "Opening runtime"}</span>{sourceSnapshot?.identity.branch && <span className="branch-status"><Icon name="branch" size={11} />{shortPath(sourceSnapshot.identity.branch, 24)}</span>}</span>}
              </div>
              {mode === "mock" && <p className="composer-mode-note">Demo replies are simulated. Connect a provider for live AI.</p>}
            </div>
          </div>
        </div>
      );
    }

    const homeProjectName = activeProjectName;

    return (
      <section data-workbench-pane="work-surface" tabIndex={-1} className={`chat-view ${isHome ? "home-view" : "thread-view"}`} aria-label="Conversation" inert={workPanelModal}>
        <header className={`conversation-topbar${sidebarCollapsed ? " ct-collapsed" : ""}${workPanelOpen ? " ct-work-panel-open" : ""}`} data-tauri-drag-region="true" role="toolbar" aria-label="Conversation">
          <div className="ct-left">
            <div className="ct-lead" aria-hidden={!sidebarCollapsed}>
              <button className="ct-icon-btn" type="button" tabIndex={sidebarCollapsed ? undefined : -1} onClick={() => setSidebarCollapsed(false)} aria-label="Expand sidebar" title="Expand sidebar"><Icon name="sidebar" size={15} /></button>
            </div>
          <div className="ct-title-wrap" title={homeProjectName ? `${homeProjectName} · ${displayActiveTitle}` : displayActiveTitle}>
            <span className="ct-title">{topbarTitle}</span>
            {activeConversationId && <span className={`task-state-badge state-${String(selectedTaskState).toLowerCase()}`} role="status">{taskStateLabel(selectedTaskState)}</span>}
          </div>
          </div>
          <div className="ct-right">
            <div className="ct-actions">
              {homeProjectName && <span className="ct-project-label" title={homeProjectName}><Icon name="folder" size={13} />{homeProjectName}</span>}
              <button className="ct-detail-button" type="button" onClick={() => { setWorkTab("context"); setWorkPanelOpen((current) => !current); }} aria-expanded={workPanelOpen} title={activeProjectName ? "Show project details" : "Show chat details"}><Icon name="panel" size={14} /><span>Details</span></button>
              <button className="ct-icon-btn" type="button" onClick={startNewTask} disabled={!workspace || conversationActionBusy || projectSwitcherBusy} aria-label="New chat" title="New chat"><Icon name="new-chat" size={15} /></button>
              <button className="ct-icon-btn" type="button" onClick={() => { setSearchQuery(""); setSearchOpen(true); window.requestAnimationFrame(() => globalSearchRef.current?.focus()); }} aria-label="Search" title="Search"><Icon name="search" size={15} /></button>
            </div>
          </div>
        </header>
        {error && <div className="error" role="alert"><span className="error-mark">!</span>{error}</div>}
        {isHome ? (
            <div className="home-main-content">
              {sidebarCollapsed && <button className="home-collapsed-nav no-drag" type="button" onClick={() => setSidebarCollapsed(false)} aria-label="Open navigation" title="Open navigation"><AegisBrandLogo /><Icon name="sidebar" size={14} /></button>}
              <div className="home-scroll">
              <div className="home-stack-inner">
                <div className="empty-hero">
                  <span className="eyebrow">YOUR WORKSPACE</span>
                  <h1>What are you working on?</h1>
                  <p>Describe a task. AEGIS keeps its progress, project files, approvals, and reviewable changes close at hand.</p>
                </div>
                {renderComposer(true)}
                <div className="home-readiness"><Icon name={modelReady ? "check" : "info"} size={14} /><span>{modelReady ? "Your model is ready." : "Try Demo now, or connect your preferred AI model."}{!activeProjectId && " A project is optional."}</span></div>
                <div className="home-quick-actions" aria-label="Getting started">
                  <button className="home-quick-action" type="button" onClick={(event) => { if (activeProjectId) { setWorkTab("files"); setWorkPanelOpen(true); } else toggleProjectSwitcher(event.currentTarget); }}><Icon name="folder" size={17} /><span className="home-action-copy"><strong>{activeProjectId ? "Browse project" : "Open a project"}</strong><small>{activeProjectId ? "Files and source map" : "Bring your files into scope"}</small></span><Icon name="chevron-right" size={14} /></button>
                  <button className="home-quick-action" type="button" onClick={() => { setSettingsSection("Models"); openDestination("settings"); }}><Icon name="bot" size={17} /><span className="home-action-copy"><strong>{modelReady ? "Manage models" : "Connect a model"}</strong><small>Your provider, your choice</small></span><Icon name="chevron-right" size={14} /></button>
                  <button className="home-quick-action" type="button" onClick={() => openDestination("plugins")}><Icon name="plug" size={17} /><span className="home-action-copy"><strong>Explore utilities</strong><small>Skills and local capabilities</small></span><Icon name="chevron-right" size={14} /></button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="chat-scroll thread-surface">
              <div className="chat-column">
                {conversationLoading && <div className="panel-empty-state" role="status"><h3>Opening conversation</h3><p>Loading messages and their context.</p></div>}
                {conversationError && <div className="panel-empty-state" role="alert"><h3>Unable to open this chat</h3><p>{conversationError}</p><button className="settings-secondary" type="button" onClick={() => activeConversationId && void loadConversation(activeConversationId)}>Try again</button></div>}
                {conversation && <div className="chat-intro"><div className="intro-mark"><Icon name="spark" size={17} /></div><div><strong>{mode === "mock" ? "Demo conversation" : "Live conversation"}</strong><span>{mode === "mock" ? "Replies are simulated" : "Using your provider"} · {conversationInspection?.context.item_count ?? 0} context items · {contextReadout}</span></div><span className="context-chip">{runtimeReady ? "Local" : "Opening"}</span></div>}
                {conversation && selectedTaskState === "INTERRUPTED" && <div className="task-interrupted-banner" role="status"><div><strong>This task was interrupted.</strong><span>Its previous run is no longer active. Start a new task with the last user message to continue.</span></div><button className="settings-secondary" type="button" onClick={() => { const previousMessage = [...conversation.turns].reverse().find((turn) => turn.role === "user")?.content ?? ""; startNewTask(); setMessage(previousMessage); }}>Start a new task</button></div>}
                {renderSubagentActivity()}
                {subagentResult && <article className="subagent-result" aria-label="Subagent run result"><div className="subagent-result-header"><div><span className="eyebrow">PARALLEL RUN</span><strong>{subagentResult.status}</strong></div><span className="status-chip"><i /> {subagentResult.child_results.length} workers</span></div><p>{subagentResult.root_output}</p><div className="subagent-workers">{subagentResult.child_results.map((worker) => <span className={`worker-chip ${worker.status.toLowerCase()}`} key={`${worker.task_id}-${worker.packet_hash}`}><i />Worker {worker.task_id} · {worker.status}</span>)}</div><small>Graph {shortPath(subagentResult.graph_hash, 18)} · {subagentResult.graph_authority}</small></article>}
                {reconciliationNotice && <p className="approval-reconciliation-feedback" role="status">{reconciliationNotice}</p>}
                {conversationInspection?.approvals.map((approval) => <article className="approval-card" key={approval.approval_id} aria-label={approval.status === "AMBIGUOUS" ? "External action outcome unclear" : "Approval required"}>
                  <div><span className="eyebrow">REVIEW REQUIRED</span><strong>{approval.tool_name}</strong></div>
                  <span className="status-chip warning"><i />{approval.status}</span>
                  <p>{approval.status === "AMBIGUOUS"
                    ? "The previous session ended before the result was confirmed. The action may or may not have happened; AEGIS will not run it again automatically."
                    : "This action is paused. Review the safe argument preview, then approve this call once or decline it."}</p>
                  {approval.argument_preview.length > 0 && <div className="approval-arguments" aria-label="Safe argument preview">
                    {approval.argument_preview.map((item, index) => <div key={`${item.key}-${index}`}><code>{item.key}</code><span>{item.value}</span></div>)}
                  </div>}
                  <small>Effect {approval.effect_class.replaceAll("_", " ")} · {approval.argument_keys.length} argument keys · sensitive values hidden</small>
                  {approval.can_resolve && <div className="approval-actions">
                    <button type="button" className="approval-decline" disabled={busy || approvalBusyId !== null} onClick={() => void resolveToolApproval(approval.approval_id, "deny")}>Decline</button>
                    <button type="button" className="approval-approve" disabled={busy || approvalBusyId !== null} onClick={() => void resolveToolApproval(approval.approval_id, "approve")}>{approvalBusyId === approval.approval_id ? "Working…" : "Run once"}</button>
                  </div>}
                  {approval.status === "AMBIGUOUS" && <AmbiguousToolCallReconciliation
                    canReconcile={approval.can_reconcile}
                    busy={busy || approvalBusyId !== null}
                    onReconcile={() => void reconcileAmbiguousToolCall(approval.approval_id)}
                  />}
                  {!approval.can_resolve && approval.status !== "AMBIGUOUS" && <small>This request is no longer actionable in the current session. Refresh the conversation before continuing.</small>}
                </article>)}
                {conversation?.turns.map((turn) => (
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
    const selectedDiff = selectedChangeDiff;
    const cacheUsageSummary = lastPromptCacheUsage
      ? `${lastPromptCacheUsage.cache_status} · ${lastPromptCacheUsage.cached_read_tokens.toLocaleString()} read · ${lastPromptCacheUsage.cache_write_tokens.toLocaleString()} written · ${lastPromptCacheUsage.provider_input_tokens.toLocaleString()} provider input`
      : null;
    const workTabLabels: Record<WorkTab, string> = {
      files: "Files",
      changes: "Changes",
      map: "Focus Graph",
      activity: "Activity",
      context: "Context",
    };
    const projectEmptyState = !activeProjectId ? <div className="panel-empty-state"><Icon name="folder" /><h3>Bring a project into scope</h3><p>Choose a project to explore its files and source map. You can chat without one.</p><button className="settings-secondary" type="button" onClick={(event) => toggleProjectSwitcher(event.currentTarget)}>Choose project</button></div>
      : graphLoading ? <div className="panel-empty-state" role="status"><Icon name="refresh" /><h3>Reading project files</h3><p>The local index is opening.</p></div>
      : graphError ? <div className="panel-empty-state" role="alert"><Icon name="info" /><h3>Unable to read this project</h3><p>{graphError}</p><button className="settings-secondary" type="button" onClick={() => void loadWorkspaceGraph(true)}>Try again</button></div> : null;
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
      <aside data-workbench-pane="inspector" ref={workPanelRef} className="work-panel" role={workPanelModal ? "dialog" : undefined} aria-modal={workPanelModal ? true : undefined} aria-label="Workspace panel" onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          setWorkPanelOpen(false);
          return;
        }
        if (!workPanelModal || event.key !== "Tab") return;
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]):not([tabindex="-1"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]')).filter((element) => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          (event.shiftKey ? last : first)?.focus();
        }
      }}>
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
            <div className="work-panel-direct-tabs no-drag" role="tablist" aria-label="Workspace panel tabs" onKeyDown={(event) => {
              const tabs = (Object.keys(workTabLabels) as WorkTab[]).filter((tab) => workbenchV2Enabled || tab !== "changes");
              const current = tabs.indexOf(workTab);
              const next = event.key === "ArrowRight" ? (current + 1) % tabs.length : event.key === "ArrowLeft" ? (current - 1 + tabs.length) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
              if (next < 0) return;
              event.preventDefault();
              setWorkTab(tabs[next]);
              event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
            }}>
              {(Object.keys(workTabLabels) as WorkTab[]).filter((tab) => workbenchV2Enabled || tab !== "changes").map((tab) => <button key={tab} id={`inspector-tab-${tab}`} className={`work-panel-tab-button ${tab === workTab ? "active" : ""}`} type="button" role="tab" aria-selected={tab === workTab} aria-controls="inspector-panel" tabIndex={tab === workTab ? 0 : -1} onClick={() => setWorkTab(tab)}><span>{workTabLabels[tab]}</span></button>)}
            </div>
            <button className="work-panel-close no-drag" type="button" onClick={() => setWorkPanelOpen(false)} aria-label="Close details" title="Close details"><Icon name="close" size={15} /></button>
          </div>
          <div id="inspector-panel" className="work-panel-body" role="tabpanel" aria-labelledby={`inspector-tab-${workTab}`} tabIndex={0}>
            {workTab === "files" && (projectEmptyState ?? <div className="file-panel"><div className="panel-title"><div><span className="eyebrow">EXPLORER</span><strong>Project files</strong></div><span className="file-count">{sourceSnapshot?.files.length ?? "—"}</span></div><label className="file-search"><Icon name="search" size={14} /><input ref={searchRef} value={fileFilter} onChange={(event) => setFileFilter(event.target.value)} placeholder="Search files" aria-label="Search files" /></label><div className="file-tree">{filteredFiles.length ? renderFileTree(fileTreeRoot) : <p className="panel-empty">{fileFilter.trim() ? "No files match this search." : "No files have been indexed in this project."}</p>}</div>{selected && <div className="source-viewer"><div className="source-viewer-header"><div><span className="eyebrow">SOURCE</span><strong title={selected.relative_path}>{selected.relative_path}</strong></div><button className="settings-secondary" type="button" onClick={() => void openSelectedFileExternally()} disabled={!hasNativeWindow()} title={hasNativeWindow() ? "Open in the operating system’s default editor" : "Available in the desktop app"}>Open externally</button></div><div className="source-viewer-meta"><span>{selected.language || "Plain text"}</span><span>{selectedFileContent?.size_bytes.toLocaleString() ?? Math.round(selected.size_bytes)} bytes</span></div>{selectedFileContent?.source === "PREVIEW_ONLY" && <p className="preview-data-note" role="note">Preview only · local file contents are not available in the browser.</p>}{fileReadError ? <p className="panel-error" role="alert">{fileReadError}</p> : selectedFileContent ? <pre className="source-viewer-content" tabIndex={0} aria-label={`Source contents of ${selected.relative_path}`}>{selectedFileContent.content}</pre> : <p className="panel-empty" role="status">Opening file…</p>}{fileOpenNotice && <p className="panel-notice" role="status">{fileOpenNotice}</p>}</div>}</div>)}
            {workTab === "changes" && (projectEmptyState ?? <div className="changes-panel"><div className="panel-title"><div><span className="eyebrow">REVIEW</span><strong>Tracked changes</strong></div><button className="settings-secondary" type="button" onClick={() => { setWorkspaceChanges(null); setChangesRefreshKey((current) => current + 1); }}>Refresh</button></div><p className="changes-source-note">Compared with the current Git HEAD. Untracked files are not included.</p>{workspaceChanges?.source === "PREVIEW_ONLY" && <p className="preview-data-note" role="note">Preview only · open the desktop app to inspect local Git changes.</p>}{workspaceChanges?.truncated && <p className="panel-notice" role="status">The list is capped at 200 tracked files.</p>}{changesError ? <p className="panel-error" role="alert">{changesError}</p> : workspaceChanges ? workspaceChanges.entries.length ? <div className="changes-layout"><div className="changes-list" role="list" aria-label="Changed files">{workspaceChanges.entries.map((entry) => <button className={`changes-file ${selectedChangePath === entry.relative_path ? "active" : ""}`} key={entry.relative_path} type="button" role="listitem" onClick={() => setSelectedChangePath(entry.relative_path)}><Icon name="file" size={14} /><span>{entry.relative_path}</span></button>)}</div><section className="changes-diff" aria-label="Selected file diff"><div className="changes-diff-heading"><strong title={selectedDiff?.relative_path ?? selectedChangePath ?? ""}>{selectedDiff?.relative_path ?? selectedChangePath}</strong><span>{selectedDiff?.source}</span></div>{selectedDiff?.truncated && <p className="panel-notice" role="status">This diff is truncated at 256 KiB.</p>}{selectedDiff ? <pre tabIndex={0}>{selectedDiff.diff || "No textual diff is available for this file."}</pre> : <p className="panel-empty" role="status">Loading diff…</p>}</section></div> : <div className="panel-empty-state"><Icon name="check" /><h3>No tracked changes</h3><p>Changes to tracked files appear here for review.</p></div> : <div className="panel-empty-state" role="status"><Icon name="activity" /><h3>Reading Git changes</h3><p>Checking the current workspace against HEAD.</p></div>}</div>)}
            {workTab === "map" && (projectEmptyState ?? <div className="map-panel">{workspaceGraph && sourceSnapshot ? <Suspense fallback={<div className="panel-empty-state" role="status"><Icon name="map" /><h3>Loading Focus Graph</h3><p>The graph feature is opening on demand.</p></div>}><WorkspaceGraphView graph={workspaceGraph} loadMemories={loadMemoriesForFile} /></Suspense> : <div className="panel-empty-state"><Icon name="map" /><h3>{graphLoading ? "Preparing Focus Graph" : "No source map yet"}</h3><p>{graphLoading ? "The local project map is being prepared." : "Reload the index to inspect this project."}</p>{!graphLoading && <button className="settings-secondary" type="button" onClick={() => void loadWorkspaceGraph(true, true)}>Reload index</button>}</div>}</div>)}
            {workTab === "activity" && <div className="activity-panel"><div className="panel-title"><div><span className="eyebrow">TASK TIMELINE</span><strong>Activity</strong></div>{conversation && <span className="status-chip"><i />{selectedTaskState === "RUNNING" || selectedTaskState === "CANCELLING" ? taskStateLabel(selectedTaskState) : "Idle"}</span>}</div>{conversationInspection?.timeline.length ? <VirtualizedTimeline items={[...conversationInspection.timeline].reverse()} /> : activityItems.length ? activityItems.map((item) => <div className="activity-item" key={`${item.title}-${item.timestamp}-${item.detail}`}><span className="activity-icon"><Icon name={item.icon} size={13} /></span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>) : <div className="panel-empty-state"><Icon name="activity" /><h3>{conversationLoading ? "Loading activity" : activeConversationId ? "No activity yet" : "Follow your task"}</h3><p>{conversationError ?? (activeConversationId ? "Actions and checkpoints appear here as you work." : "Open a task to see its actions and checkpoints.")}</p></div>}</div>}
            {workTab === "context" && (
              <div className="context-panel">
                <div className="panel-title">
                  <div><span className="eyebrow">CONTEXT COCKPIT</span><strong>What AEGIS used</strong></div>
                  <span className="status-chip">{conversationInspection?.context.status ?? "Unknown"}</span>
                </div>
                {conversationInspection ? (
                  <>
                    <div className="context-metrics">
                      <div><span>Items</span><strong>{conversationInspection.context.item_count ?? 0}</strong></div>
                      <div><span>Tokens</span><strong>{conversationInspection.context.token_count ?? "—"}</strong></div>
                      <div><span>History</span><strong>{conversationInspection.context.history_turn_count}</strong></div>
                    </div>
                    <div className="context-detail"><span>Source revision</span><code>{shortPath(conversationInspection.context.source_revision, 22)}</code></div>
                    <div className="context-detail"><span>Manifest</span><code>{shortPath(conversationInspection.context.context_manifest_hash, 22)}</code></div>
                    <div className="context-detail"><span>Selector</span><code>{conversationInspection.context.selection_backend ?? "—"}</code></div>
                    {cacheUsageSummary && <div className="context-detail"><span>Last response cache</span><code>{cacheUsageSummary}</code></div>}
                    <p className="context-redaction">Sensitive prompt, tool arguments, and provider output remain outside this observer view.</p>
                  </>
                ) : <div className="panel-empty-state"><Icon name="spark" /><h3>{conversationLoading ? "Loading context" : activeConversationId ? "Context unavailable" : "Every reply has a context"}</h3><p>{conversationError ?? (activeConversationId ? "Context appears after this conversation is loaded." : "Start or open a chat to inspect what informed its replies.")}</p></div>}
              </div>
            )}
          </div>
        </div>
      </aside>
    );
  }

  function renderSettings() {
    const groups = [
      { label: "Preferences", items: [["General", "sliders"], ["AI", "spark"], ["Shortcuts", "keyboard"]] },
      { label: "Agent", items: [["Instructions", "file"], ["Models", "bot"], ["Skills", "plug"], ["MCP", "server"], ["Plugins", "plug"], ["Subagents", "bot"]] },
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
      Plugins: "plug",
      Subagents: "bot",
      Import: "download",
      Projects: "folder",
      Info: "info",
      "Remote Hosts": "globe",
    };
    const settingSearchText: Record<string, string> = {
      General: "appearance theme color light dark font size accessibility",
      AI: "agent defaults reasoning effort context usage enter send",
      Shortcuts: "keyboard hotkeys search sidebar project details send",
      Instructions: "global prompt system instructions profile behavior",
      Models: "api key provider model reasoning endpoint connection catalog",
      Skills: "local skill instructions enable disable capability",
      MCP: "model context protocol server transport tools approval",
      Plugins: "extensions utilities local capability",
      Subagents: "parallel workers research code review mailbox",
      Import: "import sessions model configuration skills MCP",
      Projects: "project folder workspace archive recent",
      "Remote Hosts": "remote host pairing",
      Info: "version protocol runtime diagnostics",
    };
    const settingsSearch = settingsQuery.trim().toLocaleLowerCase();
    const visibleGroups = groups
      .map((group) => ({
        ...group,
        items: group.items.filter(([section]) => {
          if (!settingsSearch) return true;
          const haystack = `${group.label} ${section} ${settingSearchText[section] ?? ""}`.toLocaleLowerCase();
          return haystack.includes(settingsSearch);
        }),
      }))
      .filter((group) => group.items.length > 0);

    const row = (title: string, description: string, value: ReactNode, onClick?: () => void) => (
      <div className={`aegis-settings-row ${onClick ? "is-action" : ""}`}>
        <div><strong>{title}</strong><span>{description}</span></div>
        {onClick ? <button type="button" onClick={onClick}>{value}<Icon name="chevron-down" size={14} /></button> : <span className="aegis-settings-value">{value}</span>}
      </div>
    );
    const selectRow = (title: string, description: string, value: string, options: SettingsSelectOption[], onChange: (value: string) => void) => (
      <div className="aegis-settings-row aegis-settings-select-row">
        <div><strong>{title}</strong><span>{description}</span></div>
        <SettingsSelect value={value} options={options} onChange={onChange} ariaLabel={title} />
      </div>
    );

    const settingsTitle = settingsSection === "Projects" ? "Project archive" : settingsSection === "Models" ? "Model configuration" : settingsSection;
    const fontScaleLabel = ({ Tall: "92%", Grande: "100%", Venti: "108%", Trenta: "116%" } as const)[fontSizePreset];
    const archiveProjects = projects;
    const normalizedProjectFilter = projectFilter.trim().toLocaleLowerCase();
    const visibleProjectArchive = archiveProjects
      .filter((project) => !normalizedProjectFilter || `${project.name} ${project.path}`.toLocaleLowerCase().includes(normalizedProjectFilter))
      .sort((left, right) => projectSort === "Recent"
        ? right.last_opened_at_ms - left.last_opened_at_ms || left.name.localeCompare(right.name)
        : left.name.localeCompare(right.name) || left.path.localeCompare(right.path));
    const capabilitySearch = capabilityQuery.trim().toLocaleLowerCase();
    const subagentCapabilities = [
      ["Research worker", "Bounded evidence-gathering worker using the native task graph.", "research"],
      ["Code worker", "Workspace-aware implementation worker with hash-bound result packets.", "code"],
      ["Review worker", "Independent verification worker for tests, contracts, and regressions.", "review"],
    ] as const;
    const visibleSubagents = subagentCapabilities.filter(([name, description]) => !capabilitySearch || `${name} ${description}`.toLocaleLowerCase().includes(capabilitySearch));
    const discoveredSkills = extensionCatalog?.skills ?? [];
    const visibleSkills = discoveredSkills.filter((skill) => !capabilitySearch || `${skill.name} ${skill.description} ${skill.keywords.join(" ")}`.toLocaleLowerCase().includes(capabilitySearch));
    const visibleMcpServers = (mcpCatalog?.servers ?? []).filter((server) => !capabilitySearch || `${server.server_id} ${server.description} ${server.transport}`.toLocaleLowerCase().includes(capabilitySearch));
    const capabilityRecordFor = (kind: "skill" | "extension" | "mcp", id: string) => capabilityState?.records.find((record) => record.kind === kind && record.id === id);
    const permissionOptions: SettingsSelectOption[] = [
      { value: "Ask every time", label: "Ask every time", description: "Confirm each local write or tool action." },
      { value: "Accept edits", label: "Workspace edits", description: "Allow non-destructive edits inside this project." },
      { value: "Auto", label: "Automatic", description: "Allow low-risk local actions; deletion stays blocked." },
    ];
    const settingsModel = findModelForConnectionAndId(
      availableModels,
      conversation?.conversation.connection_id ?? activeConnectionId,
      modelId,
    );
    const reasoningOptions: SettingsSelectOption[] = reasoningChoices(settingsModel).map((level) => ({ value: level, label: level }));
    const activeModelVerified = connectionSaved
      && conversation !== null
      && availableModels.some(
        (item) => item.connection_id === conversation.conversation.connection_id && item.model_id === conversation.conversation.model_id,
      );

    const content = settingsSection === "General" ? (
      <div className="aegis-settings-stack">
        <section className="aegis-settings-group"><h2>Appearance</h2><div className="aegis-settings-card">
          {selectRow("Theme", "Follow system, light, or dark.", theme, [{ value: "system", label: "System" }, { value: "dark", label: "Dark" }, { value: "light", label: "Light" }], (value) => void saveSettingsPatch({ theme: value as Theme }))}
          <div className="aegis-settings-row aegis-settings-font-size-row">
            <div><strong>Font size</strong><span>Scale the interface while keeping the layout proportional.</span></div>
            <div className="font-size-control">
              <div className="font-size-options" role="group" aria-label="Font size preset">
                {(["Tall", "Grande", "Venti", "Trenta"] as const).map((preset) => <button key={preset} className={fontSizePreset === preset ? "active" : ""} type="button" onClick={() => void saveSettingsPatch({ font_size_preset: preset })}>{preset}</button>)}
              </div>
              <div className="font-size-slider-row"><input type="range" min="0" max="3" step="1" value={["Tall", "Grande", "Venti", "Trenta"].indexOf(fontSizePreset)} onChange={(event) => void saveSettingsPatch({ font_size_preset: (["Tall", "Grande", "Venti", "Trenta"] as const)[Number(event.target.value)] })} aria-label="Font size" /><span>{fontScaleLabel}</span></div>
            </div>
          </div>
        </div></section>
        <section className="aegis-settings-group"><h2>Workbench</h2><div className="aegis-settings-card"><div className="aegis-settings-row"><div><strong>Workbench V2</strong><span>{workbenchV2Enabled ? "Background runs, queued messages, review surfaces, and command shortcuts are enabled." : "Messages use the original synchronous send path; Workbench V2 commands and review tab are disabled."}</span></div><button className={`settings-toggle ${workbenchV2Enabled ? "on" : ""}`} type="button" role="switch" aria-checked={workbenchV2Enabled} aria-label="Enable Workbench V2" disabled={busy || activeDesktopRun !== null || subagentBusy || approvalBusyId !== null} onClick={() => { const enabled = !workbenchV2Enabled; setWorkbenchV2Enabled(enabled); if (!enabled) { setCommandPaletteOpen(false); setWorkTab("files"); setWorkPanelOpen(false); } }}><span /></button></div></div></section>
      </div>
    ) : settingsSection === "AI" ? (
      <div className="ai-settings-page">
        <section className="aegis-settings-group"><h2>Defaults</h2><div className="aegis-settings-card">
          {selectRow("Reasoning effort", "Only levels declared by the model catalog or verified for this exact provider and model are available.", reasoningSelectionForModel(settingsModel, thinkingMode), reasoningOptions.map((option) => ({ ...option, label: reasoningLabel(option.value) })), (value) => void saveSettingsPatch({ thinking_default: value as DesktopSettings["thinking_default"] }))}
          {selectRow("Context usage readout", "Choose the value shown near the conversation.", contextUsage, [{ value: "Remaining", label: "Remaining" }, { value: "Used", label: "Used" }], (value) => void saveSettingsPatch({ context_usage: value as DesktopSettings["context_usage"] }))}
          {selectRow("Local access", "Choose how local writes and tool actions are approved.", permissionMode, permissionOptions, (value) => void saveSettingsPatch({ permission_mode: value as DesktopSettings["permission_mode"] }))}
          <div className="aegis-settings-row"><div><strong>Enter to send</strong><span>{enterToSend ? "Press Enter to submit a message." : `Press ${primaryShortcutPrefix}Enter to submit a message.`}</span></div><button className={`settings-toggle ${enterToSend ? "on" : ""}`} type="button" role="switch" aria-checked={enterToSend} aria-label="Enter to send" onClick={() => void saveSettingsPatch({ enter_to_send: !enterToSend })}><span /></button></div>
        </div></section>
      </div>
    ) : settingsSection === "Shortcuts" ? (
      <div className="shortcut-settings-page"><section className="shortcut-settings-card"><div className="shortcut-settings-header"><div><h2>Keyboard shortcuts</h2><p>Shortcuts come from the Workbench command registry.</p></div></div>{workbenchCommands.map((command) => <div className="shortcut-settings-row" key={command.id}><div><strong>{command.label}</strong><span>{SHORTCUT_DESCRIPTIONS[command.id]}</span></div><span className="shortcut-settings-binding">{isMacOS ? command.keys.mac : command.keys.other}</span></div>)}<div className="shortcut-settings-row"><div><strong>Send message</strong><span>Submit the current composer prompt.</span></div><span className="shortcut-settings-binding">{enterToSend ? "Enter" : `${primaryShortcutPrefix}Enter`}</span></div></section></div>
    ) : settingsSection === "Instructions" ? (
      <div className="instruction-settings-page"><section className="instruction-settings-card"><div className="instruction-settings-heading"><div><h2>Global instructions</h2><p>Instructions applied to new AEGIS conversations.</p><span>Stored in the local profile; secrets and provider responses are excluded.</span></div><span className="instruction-file-label">profile instructions</span></div><textarea value={instructionsDraft} onChange={(event) => { setInstructionsDraft(event.target.value); setInstructionsNotice(null); }} aria-label="Global instructions" spellCheck={false} /><div className="instruction-settings-actions"><button className="settings-primary" type="button" onClick={() => void saveSettingsPatch({ instructions: instructionsDraft }, () => setInstructionsNotice("Instructions saved to the local profile."))}>Save instructions</button>{instructionsNotice && <span role="status">{instructionsNotice}</span>}</div></section></div>
    ) : settingsSection === "Models" ? (
      <div className="model-configuration-page">
        <section className="model-section model-onboarding-section">
          <div className="model-section-heading"><div><h2>Connect a model provider</h2><p>Choose the provider first, then add its API key. AEGIS checks connected catalogs while the app is open; provider results are cached for five minutes. Use Refresh catalog to bypass the cache now.</p></div><span className="model-security-badge"><Icon name="shield" size={13} />Host boundary</span></div>
          <label className="model-key-field"><span>Provider</span><select aria-label="Provider" value={selectedProviderKind} onChange={(event) => { setSelectedProviderKind(event.target.value); setCustomProviderEndpoint(""); setConnectionNotice(null); }} disabled={connectionBusy !== null}><option value="">Choose a provider…</option>{providerChoices.map((provider) => <option key={provider.provider_kind} value={provider.provider_kind}>{provider.provider_label}</option>)}</select></label>
          {selectedProvider && <div className="provider-selection-card" role="status"><div><strong>{selectedProvider.provider_label}</strong><span>Selected by you</span></div><small>Key destination: <strong>{selectedProvider.requires_endpoint ? customProviderEndpoint.trim() || "Enter a compatible endpoint below" : selectedProvider.endpoint}</strong></small></div>}
          {selectedProvider?.requires_endpoint && <label className="model-key-field"><span>Compatible provider endpoint</span><input aria-label="Custom provider endpoint" aria-required value={customProviderEndpoint} onChange={(event) => { setCustomProviderEndpoint(event.target.value); setConnectionNotice(null); }} placeholder="https://…" inputMode="url" disabled={connectionBusy !== null} /></label>}
          <label className="model-key-field"><span>API key</span><input aria-label="API key" type="password" value={apiKeyDraft} onChange={(event) => { setApiKeyDraft(event.target.value); setConnectionNotice(null); }} placeholder="Paste API key" autoComplete="new-password" spellCheck={false} disabled={connectionBusy !== null} /></label>
          <div className="model-onboarding-actions"><button className="settings-primary" type="button" onClick={() => void connectApiKey()} disabled={!apiKeyDraft.trim() || !workspace || !selectedProvider || Boolean(selectedProvider.requires_endpoint && !customProviderEndpoint.trim()) || connectionBusy !== null}>{connectionBusy === "connect" ? "Connecting…" : "Connect & discover models"}</button></div>
          <p className="model-secret-note"><Icon name="shield" size={13} />When you connect, the key is sent to the destination shown above for model discovery and provider requests. It stays in local session memory and is not saved to the project database or application logs.</p>
          {connectionNotice && <div className="model-connection-notice" role="status">{connectionNotice}</div>}
        </section>
        <section className="model-section"><h2>Defaults</h2><div className="model-default-row"><div><strong>Default model</strong><span>{activeModelVerified ? conversation?.conversation.model_id : "No model selected"}</span></div>{!activeModelVerified && <button className="settings-secondary" type="button" onClick={() => { setSettingsSection("Models"); setConnectionNotice(connectionSaved ? "Choose a verified model for this conversation." : "Paste an API key to load the provider model catalog."); }}>Configure provider</button>}</div><p className="model-section-note">The selected model must come from the authenticated provider catalog. Manual model IDs are disabled so the UI cannot advertise an unverified model.</p></section>
        <section className="model-section">
          <div className="model-section-heading"><h2>Connected providers <span>{connectedProviders.length}</span></h2></div>
          {connectedProviders.length === 0
            ? <div className="model-vendor-empty">Connect an API key above to load models available to that account.</div>
            : connectedProviders.map((provider) => {
              const modelCount = availableModels.filter((model) => model.connection_id === provider.connection_id).length;
              const isActive = provider.connection_id === activeConnectionId;
              const confirmingDisconnect = disconnectConfirmationId === provider.connection_id;
              return <div className="model-provider-row" key={provider.connection_id}>
                <div>
                  <strong>{providerLabelForKind(provider.provider_kind)}</strong>
                  <span>{shortPath(provider.endpoint, 42)} · {modelCount} model{modelCount === 1 ? "" : "s"}</span>
                </div>
                {isActive && <span className="model-provider-default">Active</span>}
                {confirmingDisconnect
                  ? <>
                    <span className="model-provider-state">Clears this session key</span>
                    <button type="button" disabled={connectionBusy !== null} onClick={() => setDisconnectConfirmationId(null)}>Cancel</button>
                    <button type="button" disabled={connectionBusy !== null} aria-label={`Confirm disconnect ${providerLabelForKind(provider.provider_kind)}`} onClick={() => void disconnectProvider(provider)}>{connectionBusy === "disconnect" ? "Disconnecting…" : "Disconnect"}</button>
                  </>
                  : <button type="button" disabled={connectionBusy !== null} aria-label={`Disconnect ${providerLabelForKind(provider.provider_kind)}`} onClick={() => setDisconnectConfirmationId(provider.connection_id)}>Disconnect</button>}
              </div>;
            })}
        </section>
        <div className="model-catalog-row"><span>{availableModels.length > 0 ? `${availableModels.length} model${availableModels.length === 1 ? "" : "s"} · ${modelCatalogState.toLocaleLowerCase()} catalog` : "No model catalog loaded"}</span><button type="button" onClick={() => void refreshModelCatalog()} disabled={connectionBusy !== null || !connectionSaved}><Icon name="refresh" size={14} />Refresh catalog</button></div>
        {availableModels.length > 0 && <section className="model-catalog-list" aria-label="Verified models">
          {availableModels.map((model) => {
            const selected = conversation?.conversation.connection_id === model.connection_id && conversation.conversation.model_id === model.model_id;
            const modelReasoning = model.reasoning_efforts.length > 0 ? model.reasoning_efforts.join(" · ") : "No verified effort levels";
            return <div className={`model-catalog-item ${selected ? "active" : ""}`} key={`${model.connection_id}:${model.model_id}`}>
              <div className="model-catalog-item-copy"><strong>{model.model_id}</strong><span>{model.family || "Provider model"} · {model.supports_vision ? "Text + image" : "Image input not verified"} · Reasoning: {modelReasoning}</span></div>
              <button className="settings-secondary" type="button" disabled={selected || connectionBusy !== null || busy || subagentBusy} onClick={() => void switchConversationModel(model)}>{selected ? "Selected" : "Use model"}</button>
            </div>;
          })}
          <p className="model-section-note">Only capabilities returned or conservatively verified for this provider endpoint are shown. Models without an exact reasoning contract stay on Auto.</p>
        </section>}
      </div>
    ) : settingsSection === "Skills" ? (
      <div className="capability-settings-page">
        <div className="capability-settings-toolbar">
          <label><Icon name="search" size={14} /><input value={capabilityQuery} onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search skills" aria-label="Search skills" /></label>
          <div>
            <button className="settings-secondary" type="button" onClick={() => void importSkillFolder()} disabled={extensionBusy || !hasNativeWindow()}>Import skill</button>
            <button className="settings-primary" type="button" onClick={() => void loadExtensionCatalog()} disabled={extensionBusy}><Icon name="refresh" size={14} />{extensionBusy ? "Scanning…" : "Scan local skills"}</button>
          </div>
        </div>
        <p className="capability-settings-help">Choose a folder containing SKILL.md. Visible skill files are copied into this project; hidden files such as .env and .git are skipped. The skill stays disabled until enabled, and bundled scripts are never run by import.</p>
        <div className="capability-settings-group"><div className="capability-settings-group-heading"><strong>Discovered skills</strong><span>{visibleSkills.length}</span></div>{visibleSkills.length > 0 ? visibleSkills.map((skill) => { const state = capabilityRecordFor("skill", skill.name); return <div className="capability-settings-row" key={skill.name}><span className="capability-settings-glyph"><Icon name="plug" size={15} /></span><div><strong>{skill.name}</strong><span>{skill.description}</span><small>{skill.version} · sha256 {skill.content_hash.slice(0, 12)} · {state?.enabled ? "Enabled" : "Available"}</small></div><button type="button" onClick={() => void setCapabilityActivation("skill", skill.name, skill.content_hash, !state?.enabled, false)}>{state?.enabled ? "Disable" : "Enable"}</button><button type="button" onClick={() => { setSelectedSkill(skill.name); void loadSkillBody(skill.name); }}>{skillLoadBusy && selectedSkill === skill.name ? "Opening…" : "View details"} <Icon name="chevron-right" size={13} /></button></div>; }) : <div className="capability-settings-empty"><Icon name="plug" size={18} /><strong>No local skills discovered</strong><span>Place a bounded SKILL.md in a supported project or profile skills folder, then scan again.</span></div>}</div>
        {extensionNotice && <div className="capability-settings-notice" role="status">{extensionNotice}</div>}
      </div>
    ) : settingsSection === "Plugins" ? (
      <div className="aegis-settings-stack"><section className="aegis-settings-group"><h2>Utilities</h2><div className="aegis-settings-card"><div className="aegis-settings-empty"><Icon name="plug" size={18} /><div><strong>Local capability catalog</strong><span>Inspect connected skills, plugins, and MCP metadata.</span></div><button className="settings-primary" type="button" onClick={() => openDestination("plugins")}>Open utilities</button></div></div></section></div>
    ) : settingsSection === "MCP" ? (
      <div className="capability-settings-page">
        <div className="capability-settings-toolbar">
          <label><Icon name="search" size={14} /><input value={capabilityQuery} onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search MCP servers" aria-label="Search MCP servers" /></label>
          <div><button className="settings-secondary" type="button" onClick={() => { if (mcpAddOpen) { setMcpRegistrySelection(null); setMcpRegistryChoice(""); } setMcpAddOpen(!mcpAddOpen); }}>{mcpAddOpen ? "Cancel" : "Add MCP metadata"}</button><button className="settings-primary" type="button" onClick={() => void loadMcpCatalog()} disabled={extensionBusy}><Icon name="refresh" size={14} />{extensionBusy ? "Scanning…" : "Scan local MCP"}</button></div>
        </div>
        <section className="mcp-registry-search" aria-labelledby="mcp-registry-title">
          <div className="mcp-registry-search-heading"><div><h2 id="mcp-registry-title">Public MCP directory</h2><p>Search the official public directory. No API key or browser cookies are sent. Entries are untrusted metadata; nothing is installed or started. Direct internet access is required; proxy-only networks may block lookup.</p></div></div>
          <form className="mcp-registry-search-form" aria-busy={mcpRegistryBusy} onSubmit={(event) => { event.preventDefault(); void searchMcpRegistry(); }}>
            <input
              aria-label="Search the public MCP directory"
              disabled={mcpRegistryBusy}
              value={mcpRegistryQuery}
              onChange={(event) => { setMcpRegistryQuery(event.target.value); setMcpRegistryResults(null); setCapabilityNotice(null); }}
              placeholder="Search public MCP servers"
            />
            <button className="settings-secondary" type="submit" disabled={mcpRegistryBusy}>{mcpRegistryBusy ? "Searching…" : "Search directory"}</button>
          </form>
          {mcpRegistryResults && <div className="mcp-registry-results">
            <div className="mcp-registry-results-heading" role="status"><strong>Results for “{mcpRegistryResults.query}”</strong><span>{mcpRegistryResults.servers.length}</span></div>
            {mcpRegistryResults.servers.length > 0 ? mcpRegistryResults.servers.map((server) => {
              const setupOptions = getMcpRegistrySetupOptions(server);
              const stdioCount = setupOptions.filter((option) => option.kind === "package").length;
              const remoteCount = setupOptions.filter((option) => option.kind === "remote").length;
              const hasSetup = setupOptions.length > 0;
              const availableOptions = [
                stdioCount ? `${stdioCount} local package${stdioCount === 1 ? "" : "s"}` : "",
                remoteCount ? `${remoteCount} remote endpoint${remoteCount === 1 ? "" : "s"}` : "",
              ].filter(Boolean).join(" · ");
              return <article className="mcp-registry-result-card" key={`${server.name}@${server.version}`}>
                <div className="mcp-registry-result-copy">
                  <strong>{server.title || server.name}</strong>
                  <span>{server.name} · {server.version}</span>
                  {server.description && <p>{server.description}</p>}
                  <small>{availableOptions || "No supported transport details"}</small>
                </div>
                <button className="settings-secondary" type="button" aria-label={`Review ${setupOptions.length} setup option${setupOptions.length === 1 ? "" : "s"} for ${server.name}`} disabled={!hasSetup} onClick={() => prepareMcpRegistryEntry(server)}>Review setup options</button>
              </article>;
            }) : <p className="mcp-registry-empty">No matching public servers on this page.</p>}
            {mcpRegistryResults.next_cursor && <button className="settings-secondary mcp-registry-more" type="button" disabled={mcpRegistryBusy} onClick={() => void searchMcpRegistry(mcpRegistryResults.next_cursor ?? undefined)}>{mcpRegistryBusy ? "Loading…" : "Load more"}</button>}
          </div>}
        </section>
        {mcpAddOpen && <section className="mcp-add-card">
          <div><h2>{mcpRegistrySelection ? "Review setup draft" : "Add a local MCP server"}</h2><p>{mcpRegistrySelection ? "Registry metadata is untrusted. Review these fields; setup only adds local metadata and never installs or starts code." : "This creates metadata only. It does not start the program or connect to the network."}</p></div>
          {mcpRegistrySelection && (() => {
            const options = getMcpRegistrySetupOptions(mcpRegistrySelection);
            return <label><span>Package or remote endpoint</span><select aria-label="Choose MCP package or remote endpoint" value={mcpRegistryChoice} onChange={(event) => {
              const option = findMcpRegistrySetupOption(mcpRegistrySelection, event.target.value);
              setMcpRegistryChoice(event.target.value);
              if (option) setMcpNewTransport(option.transport);
            }}>
              {options.length > 1 && <option value="">Choose one option</option>}
              {options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
            </select><small>Choose the exact source to configure. AEGIS will not install or run it.</small></label>;
          })()}
          <label><span>Server id</span><input value={mcpNewId} onChange={(event) => setMcpNewId(event.target.value)} placeholder="my-server" /></label>
          <label><span>Description</span><input value={mcpNewDescription} onChange={(event) => setMcpNewDescription(event.target.value)} placeholder="What this server does" /></label>
          {!mcpRegistrySelection && <label><span>Transport</span><select value={mcpNewTransport} onChange={(event) => setMcpNewTransport(event.target.value as "stdio" | "streamable-http")}><option value="stdio">Local command (stdio)</option><option value="streamable-http">Remote HTTPS</option></select></label>}
          <button className="settings-primary" type="button" onClick={() => void addMcpMetadata()} disabled={extensionBusy || (mcpRegistrySelection !== null && !mcpRegistryChoice)}>{extensionBusy ? "Adding…" : "Add metadata"}</button>
        </section>}
        <p className="capability-settings-help">Metadata discovery is read-only. Approve a server first, then configure its local command or HTTPS endpoint. Transport settings stay in this session and are never written to the project. Local stdio commands run with your account’s normal file and network permissions; AEGIS does not sandbox them. Review the command and arguments before enabling.</p>
        <div className="capability-settings-group">
          <div className="capability-settings-group-heading"><strong>Discovered servers</strong><span>{visibleMcpServers.length}</span></div>
          {visibleMcpServers.length > 0 ? visibleMcpServers.map((server) => {
            const state = capabilityRecordFor("mcp", server.server_id);
            const draft = mcpDrafts[server.server_id] ?? emptyMcpDraft;
            const active = state?.enabled === true;
            const approved = state?.approved === true;
            const busy = mcpActivationBusy === server.server_id;
            const isStdio = server.transport.toLocaleLowerCase() === "stdio";
            const environmentVariables = mcpRegistryEnvironmentVariables[server.server_id] ?? server.environment_variables;
            const environmentValues = mcpRegistryEnvironmentValues[server.server_id] ?? {};
            const testResult = mcpTestResults[server.server_id];
            const readOnlyToolCount = testResult?.tools.filter((tool) => tool.read_only_candidate).length ?? 0;
            const selectedAutoRunTools = mcpAutoRunReadOnly[server.server_id] ?? {};
            return <div className="capability-settings-row mcp-capability-row" key={server.server_id}>
              <span className="capability-settings-glyph"><Icon name="server" size={15} /></span>
              <div className="mcp-capability-copy"><strong>{server.server_id}</strong><span>{server.description}</span><small>{server.transport} · {active ? "Active" : approved ? "Approved" : "Approval required"}</small>{mcpRegistryHints[server.server_id] && <small>{mcpRegistryHints[server.server_id]}</small>}</div>
              <div className="mcp-capability-actions">
                <button type="button" disabled={active || busy} onClick={() => void setCapabilityActivation("mcp", server.server_id, server.descriptor_hash, false, !approved)}>{approved ? active ? "Stop first" : "Revoke approval" : "Approve metadata"}</button>
                {approved && !active && <div className="mcp-config-fields">
                  {mcpImportedSetupDrafts[server.server_id] && <p className="mcp-config-explainer" role="note">This connection draft came from an imported package and is untrusted. Review every command, argument, and URL before testing; no process was started during import.</p>}
                  <p className="mcp-config-explainer">Testing starts the command or contacts the HTTPS endpoint to read tool and resource catalogs, then closes it without running a tool or reading resource contents. By default, every tool call asks for approval. You can opt in per tool when the server claims it is read-only; AEGIS cannot verify that claim, and read-only calls may still send data to the server. Host-managed resource tools are limited to resources the server advertises, but their content is still untrusted. Local commands run with your account’s normal permissions and are not sandboxed.</p>
                  {isStdio ? <>
                    <label><span>Command and arguments, one per line</span><textarea value={draft.command} onChange={(event) => updateMcpDraft(server.server_id, { command: event.target.value })} placeholder={'python\nserver.py'} rows={2} /></label>
                    <label><span>Working directory (optional)</span><input value={draft.cwd} onChange={(event) => updateMcpDraft(server.server_id, { cwd: event.target.value })} placeholder="C:\\path\\to\\server" /></label>
                    {environmentVariables.length > 0 ? <>
                      <p className="mcp-environment-note">Connection values stay in memory for this app session. Directory defaults and saved secrets are never used.</p>
                      <div className="mcp-environment-fields">
                        {environmentVariables.map((item) => <label key={item.name}>
                          <span>{item.name} · {item.is_required ? "Required" : "Optional"}{item.is_secret ? " · Secret" : ""}</span>
                          {item.description && <small>{item.description}</small>}
                          <input
                            type={item.is_secret ? "password" : "text"}
                            value={environmentValues[item.name] ?? ""}
                            onChange={(event) => {
                              setMcpRegistryEnvironmentValues((current) => ({
                                ...current,
                                [server.server_id]: { ...(current[server.server_id] ?? {}), [item.name]: event.target.value },
                              }));
                              clearMcpTest(server.server_id);
                            }}
                            aria-label={`${item.name}${item.is_required ? ", required" : ", optional"}${item.is_secret ? ", secret" : ""}`}
                            aria-required={item.is_required}
                            autoComplete={item.is_secret ? "new-password" : "off"}
                            spellCheck={false}
                          />
                        </label>)}
                      </div>
                      <details className="mcp-environment-advanced">
                        <summary>Additional environment values · advanced</summary>
                        <p className="mcp-environment-note">This JSON is visible while editing. Use the named secret fields above for credentials.</p>
                        <label><span>Environment JSON (session-only)</span><textarea value={draft.environment} onChange={(event) => updateMcpDraft(server.server_id, { environment: event.target.value })} placeholder={'{"EXTRA_OPTION":"value"}'} rows={3} spellCheck={false} /></label>
                      </details>
                    </> : <label><span>Environment JSON (session-only)</span><textarea value={draft.environment} onChange={(event) => updateMcpDraft(server.server_id, { environment: event.target.value })} placeholder={'{"API_KEY":"…"}'} rows={3} spellCheck={false} /></label>}
                  </> : <>
                    <label><span>HTTPS endpoint</span><input value={draft.endpoint} onChange={(event) => updateMcpDraft(server.server_id, { endpoint: event.target.value })} placeholder="https://mcp.example.com" inputMode="url" /></label>
                    <label><span>Allowed hosts, comma-separated</span><input value={draft.allowedHosts} onChange={(event) => updateMcpDraft(server.server_id, { allowedHosts: event.target.value })} placeholder="mcp.example.com" /></label>
                    <label><span>OAuth Client ID (only if requested)</span><input value={draft.oauthClientId} onChange={(event) => updateMcpDraft(server.server_id, { oauthClientId: event.target.value })} placeholder="Leave blank for automatic registration" autoComplete="off" spellCheck={false} /></label>
                    <label><span>Headers JSON (optional, session-only)</span><input value={draft.headers} onChange={(event) => updateMcpDraft(server.server_id, { headers: event.target.value })} placeholder={'{"Authorization":"Bearer …"}'} /></label>
                    <small>Leave this blank to let AEGIS reuse a saved Client ID or try automatic registration when needed. If the server requires a pre-registered Client ID, enter it here; AEGIS uses that ID directly and skips automatic registration. Sign-in opens your system browser; tokens stay in the OS secure store. Do not also set an Authorization header.</small>
                  </>}
                  <div className="mcp-config-actions">
                    {readOnlyToolCount > 0 && <p className="mcp-readonly-grant-note">Auto-run is off by default. Choose each tool below; its read-only label is the server’s claim, not a verified guarantee.</p>}
                    {!testResult && <p className="mcp-config-explainer" role="note">Test this exact connection before enabling. Any configuration change clears the test and requires another check.</p>}
                    <button className="settings-secondary" type="button" disabled={busy || mcpTestBusy !== null} onClick={() => void testMcpConnection(server)}>{mcpTestBusy === server.server_id ? "Testing…" : "Test connection"}</button>
                    <button className="settings-primary" type="button" disabled={busy || mcpTestBusy !== null || !testResult} onClick={() => void activateMcp(server)}>{busy ? "Starting…" : "Enable tools"}</button>
                  </div>
                </div>}
                {active && <button className="settings-secondary" type="button" disabled={busy} onClick={() => void deactivateMcp(server)}>{busy ? "Stopping…" : "Stop server"}</button>}
                {testResult && <div className="mcp-test-tool-list" role="group" aria-label="MCP tool permissions">
                  <strong>Available capabilities · up to 64</strong>
                  {testResult.tools.map((tool) => {
                    const selected = selectedAutoRunTools[tool.mcp_descriptor_hash] === true;
                    return <div className="mcp-test-tool-row" key={tool.mcp_descriptor_hash}>
                      <div className="mcp-test-tool-copy">
                        <span>{tool.name}</span>
                        <small>{tool.description}</small>
                        <small>{tool.host_managed_read_only
                          ? selected
                            ? "Selected · AEGIS limits reads to resources/templates advertised by this server; returned content is untrusted and sent to the model."
                            : "Not selected · asks before reading. AEGIS limits reads to resources/templates advertised by this server."
                          : tool.read_only_candidate
                            ? selected
                              ? "Selected · server claims read-only; AEGIS cannot verify behavior."
                              : "Not selected · still asks before running. Server’s claim is unverified."
                            : "Approval required before every use."}</small>
                        <small>{tool.host_managed_read_only
                          ? "This reads approved-server data into the model context; it may contain unsafe instructions."
                          : tool.open_world_hint
                            ? "Server says this tool may interact outside its domain; unverified."
                            : "Server says this tool stays within a closed domain; unverified."}</small>
                      </div>
                      {tool.read_only_candidate
                        ? <label className="mcp-tool-auto-run-grant">
                            <input
                              type="checkbox"
                              checked={selected}
                              aria-label={`Run automatically: ${tool.name}`}
                              onChange={(event) => setMcpAutoRunTool(server.server_id, tool.mcp_descriptor_hash, event.target.checked)}
                            />
                            <span>Run automatically</span>
                          </label>
                        : <span className="mcp-tool-approval-status">Approval required</span>}
                    </div>;
                  })}
                  {testResult.tools_truncated && <small>Only the first 64 tools are shown; unlisted tools do not receive automatic access.</small>}
                </div>}
                {active && <section className="mcp-prompt-panel" aria-label={`Prompt templates from ${server.server_id}`}>
                  <div className="mcp-prompt-heading">
                    <div><strong>MCP prompt templates</strong><small>When you render a preview, your values are sent to this approved server. Its text is untrusted; it reaches the model only if you insert it and send the chat.</small></div>
                    <button className="settings-secondary" type="button" disabled={mcpPromptsBusy !== null} onClick={() => void loadMcpPrompts(server)}>
                      {mcpPromptsBusy === server.server_id ? "Loading…" : "Browse prompts"}
                    </button>
                  </div>
                  {mcpPromptCatalogs[server.server_id] && (mcpPromptCatalogs[server.server_id].prompts.length > 0
                    ? <div className="mcp-prompt-list">{mcpPromptCatalogs[server.server_id].prompts.map((prompt) => {
                      const values = mcpPromptArguments[server.server_id]?.[prompt.name] ?? {};
                      const preview = mcpPromptResults[server.server_id]?.[prompt.name];
                      const canInsert = preview?.messages.length === 1 && preview.messages[0].role === "user";
                      return <details className="mcp-prompt-card" key={prompt.name}>
                        <summary>{prompt.title || prompt.name}<small>{prompt.description || "No description provided by server"}</small></summary>
                        {prompt.arguments.length > 0 && <div className="mcp-prompt-fields">{prompt.arguments.map((argument) => <label key={argument.name}>
                          <span>{argument.name}{argument.required ? " · Required" : " · Optional"}</span>
                          {argument.description && <small>{argument.description}</small>}
                          <textarea
                            rows={2}
                            maxLength={8_192}
                            value={values[argument.name] ?? ""}
                            aria-label={`${argument.name}${argument.required ? ", required" : ", optional"}`}
                            onChange={(event) => {
                              setMcpPromptArguments((current) => ({
                                ...current,
                                [server.server_id]: {
                                  ...(current[server.server_id] ?? {}),
                                  [prompt.name]: { ...(current[server.server_id]?.[prompt.name] ?? {}), [argument.name]: event.target.value },
                                },
                              }));
                              setMcpPromptResults((current) => {
                                const serverResults = { ...(current[server.server_id] ?? {}) };
                                delete serverResults[prompt.name];
                                return { ...current, [server.server_id]: serverResults };
                              });
                            }}
                          />
                        </label>)}</div>}
                        <button className="settings-secondary" type="button" disabled={mcpPromptsBusy !== null} onClick={() => void renderMcpPrompt(server, prompt)}>
                          {mcpPromptsBusy === server.server_id ? "Loading…" : "Render preview"}
                        </button>
                        {preview && <div className="mcp-prompt-preview" aria-label={`Preview of ${prompt.name}`}>
                          <small>{preview.trust_notice}</small>
                          {preview.description && <p>{preview.description}</p>}
                          {preview.messages.map((item, index) => <article key={`${item.role}-${index}`}>
                            <strong>{item.role === "user" ? "User message" : "Assistant example"}</strong>
                            <pre>{item.text}</pre>
                          </article>)}
                          {canInsert
                            ? <button className="settings-primary" type="button" onClick={() => insertMcpPrompt(server.server_id, prompt.name)}>Insert into composer</button>
                            : <small>This template contains multiple messages. AEGIS shows the roles faithfully but will not flatten them into one chat message.</small>}
                        </div>}
                      </details>;
                    })}</div>
                    : <p className="mcp-prompt-empty">This approved server currently advertises no prompt templates.</p>)}
                </section>}
                {(active || mcpSkillCatalogs[server.server_id]) && <section className="mcp-prompt-panel mcp-skills-panel" aria-label={"Skills from " + server.server_id}>
                  <div className="mcp-prompt-heading">
                    <div>
                      <strong>MCP Skills · {server.server_id}</strong>
                      <small>Browse metadata first. Each Skill needs separate approval; this list never fetches instruction files.</small>
                    </div>
                    <button
                      className="settings-secondary"
                      type="button"
                      disabled={!active || mcpSkillsBusy !== null || mcpSkillBusyId !== null}
                      onClick={() => void loadMcpSkills(server)}
                    >
                      {mcpSkillsBusy === server.server_id ? "Loading…" : "Browse Skills"}
                    </button>
                  </div>
                  {mcpSkillCatalogs[server.server_id] && <>
                    {!active && <p className="mcp-prompt-empty">The server is stopped. This is a cached list; it cannot be used or refreshed until the server is enabled again.</p>}
                    {!mcpSkillCatalogs[server.server_id].supports_skills
                      ? <p className="mcp-prompt-empty">This server does not advertise the MCP Skills extension.</p>
                      : mcpSkillCatalogs[server.server_id].skills.length > 0
                        ? <div className="mcp-skill-list">
                          {mcpSkillCatalogs[server.server_id].skills.map((skill) => {
                            const saved = capabilityState?.records.find((item) => item.kind === "skill" && item.id === skill.id);
                            const rowBusy = mcpSkillBusyId === skill.id;
                            const status = skill.dynamic
                              ? "Dynamic content · approval unavailable"
                              : !active
                                ? "Server stopped · cached metadata"
                                : skill.enabled
                                  ? "Enabled"
                                  : skill.approved
                                    ? "Approved · disabled"
                                    : "Approval required";
                            return <div className="mcp-skill-row" key={skill.id}>
                              <div className="mcp-skill-copy">
                                <strong>{skill.name}</strong>
                                <span>{skill.description || "No description provided by server."}</span>
                                <small className="mcp-skill-uri" title={skill.uri}>{skill.uri}</small>
                                <small>{status} · {skill.resource_count === null ? "dynamic files" : skill.resource_count + (skill.resource_count === 1 ? " file" : " files")}{skill.manifest_hash ? " · sha256 " + skill.manifest_hash.slice(0, 12) : ""}</small>
                              </div>
                              <div className="mcp-skill-actions">
                                {skill.dynamic
                                  ? <span className="mcp-skill-state">Cannot approve dynamic content</span>
                                  : active
                                    ? <>
                                      <button
                                        className={skill.approved ? "settings-secondary" : "settings-primary"}
                                        type="button"
                                        disabled={rowBusy || mcpSkillsBusy !== null}
                                        onClick={() => void setMcpSkillState(server, skill, !skill.enabled, true)}
                                      >
                                        {rowBusy ? "Saving…" : skill.enabled ? "Disable" : skill.approved ? "Enable" : "Approve & enable"}
                                      </button>
                                      {skill.approved && <button
                                        className="settings-secondary"
                                        type="button"
                                        disabled={rowBusy || mcpSkillsBusy !== null}
                                        onClick={() => void setMcpSkillState(server, skill, false, false)}
                                      >Revoke</button>}
                                    </>
                                    : saved?.approved === true && <button
                                      className="settings-secondary"
                                      type="button"
                                      disabled={rowBusy}
                                      onClick={() => void setMcpSkillState(server, skill, false, false)}
                                    >Revoke approval</button>}
                              </div>
                            </div>;
                          })}
                          {mcpSkillCatalogs[server.server_id].next_cursor && <button
                            className="settings-secondary mcp-skills-more"
                            type="button"
                            disabled={!active || mcpSkillsBusy !== null || mcpSkillBusyId !== null}
                            onClick={() => void loadMcpSkills(server, mcpSkillCatalogs[server.server_id].next_cursor)}
                          >{mcpSkillsBusy === server.server_id ? "Loading…" : "Load more Skills"}</button>}
                        </div>
                        : <p className="mcp-prompt-empty">This server currently publishes no Skills.</p>}
                  </>}
                </section>}
              </div>
            </div>;
          }) : <div className="capability-settings-empty"><Icon name="server" size={18} /><strong>No local MCP servers discovered</strong><span>Discovery reads metadata only. It never starts a process or sends a network request.</span></div>}
        </div>
        {capabilityNotice && <div className="capability-settings-notice" role="status">{capabilityNotice}</div>}
      </div>
    ) : settingsSection === "Subagents" ? (
      <div className="capability-settings-page">
        <div className="aegis-settings-card">{row("Parallel workers", "Allow worker runs from an active conversation. A connected model is required.", <button className={`settings-toggle ${parallelWorkersEnabled ? "on" : ""}`} type="button" role="switch" aria-checked={parallelWorkersEnabled} aria-label="Enable parallel workers" onClick={() => void saveSettingsPatch({ parallel_workers_enabled: !parallelWorkersEnabled })}><span /></button>)}</div>
        <div className="capability-settings-toolbar"><label><Icon name="search" size={14} /><input value={capabilityQuery} onChange={(event) => setCapabilityQuery(event.target.value)} placeholder="Search subagents" aria-label="Search subagents" /></label></div>
        <div className="capability-settings-group"><div className="capability-settings-group-heading"><strong>Built-in workers</strong><span>{visibleSubagents.length}</span></div>{visibleSubagents.map(([name, description, role]) => <div className="capability-settings-row" key={name}><span className="capability-settings-glyph"><Icon name="bot" size={15} /></span><div><strong>{name}</strong><span>{description}</span><small>AEGIS local · {role} · run-scoped</small></div><span className="extension-scope-chip">Built-in</span></div>)}</div>
        {capabilityNotice && <div className="capability-settings-notice" role="status">{capabilityNotice}</div>}
      </div>
    ) : settingsSection === "Remote Hosts" ? (
      <div className="remote-hosts-settings-page"><section className="aegis-settings-group"><h2>Remote hosts</h2><div className="aegis-settings-card"><div className="aegis-settings-empty"><Icon name="globe" size={18} /><div><strong>Pair a remote AEGIS host</strong><span>Remote execution is intentionally disabled until a host adapter is connected.</span></div><span className="aegis-settings-value">Not connected</span></div></div></section><div className="capability-settings-notice" role="status">Remote host pairing is not connected to the local host yet.</div></div>
    ) : settingsSection === "Info" ? (
      <div className="aegis-settings-stack"><section className="aegis-settings-group"><h2>Application</h2><div className="aegis-settings-card">
        {row("AEGIS", "Local desktop agent.", "Local")}
        {row("Protocol", "Renderer-to-host command contract.", "v1")}
        {row("Runtime", "Desktop host status.", previewMode ? "Browser demo" : runtimeReady ? "Online" : "Opening")}
      </div></section></div>
    ) : settingsSection === "Import" ? (
      <div className="import-configuration-page">
        {importNotice && <div className="import-notice" role="status">{importNotice}</div>}
        <section className="import-settings-card">
          <div className="import-settings-heading">
            <div>
              <h2>Open a project folder</h2>
              <p>Choose a folder on this device. AEGIS opens it in place and adds it to Projects; it does not copy or upload the folder.</p>
            </div>
            <button type="button" onClick={() => void openWorkspacePicker()} disabled={projectSwitcherBusy || !hasNativeWindow()}>
              <Icon name="folder" size={14} />{projectSwitcherBusy ? "Opening…" : "Choose folder"}
            </button>
          </div>
          <div className="import-settings-empty">
            {hasNativeWindow()
              ? "Project files stay on this computer. Workspace state is stored locally in the selected project."
              : "Folder selection is available in the packaged desktop app."}
          </div>
        </section>
        {(["Import from other tools", "Model configuration", "Skills", "MCP servers"] as const).map((title) => <section className="import-settings-card" key={title}><div className="import-settings-heading"><div><h2>{title}</h2><p>{title === "Import from other tools" ? "Session import is not available in this local-first build." : title === "Model configuration" ? "Find local provider and model settings." : title === "Skills" ? "Scan local skill directories." : "Scan local MCP server configurations."}</p></div>{title === "Import from other tools" ? <span className="aegis-settings-value">Not available</span> : <button type="button" onClick={() => { if (title === "Model configuration") setSettingsSection("Models"); else if (title === "Skills") { void loadExtensionCatalog(); setImportNotice("Đã quét catalog skill cục bộ."); } else { void loadMcpCatalog(); setImportNotice("Đã quét metadata MCP cục bộ."); } }}>{title === "Model configuration" ? "Open" : "Scan"}</button>}</div><div className="import-settings-empty">{title === "Skills" ? `${extensionCatalog?.skills.length ?? 0} skill(s) discovered` : title === "MCP servers" ? `${mcpCatalog?.servers.length ?? 0} MCP server(s) discovered` : title === "Import from other tools" ? "No local session importer is configured." : "Nothing scanned yet."}</div></section>)}
      </div>
    ) : settingsSection === "Projects" ? (
      <div className="project-archive-page">
        <p className="project-archive-subtitle">Opened folders and their chats.</p>
        <div className="project-archive-toolbar">
          <div className="project-sort-control" role="group" aria-label="Project sort">
            {(["Recent", "Name"] as const).map((sort) => <button key={sort} className={projectSort === sort ? "active" : ""} type="button" onClick={() => setProjectSort(sort)}>{sort}</button>)}
          </div>
          <label className="project-search-control"><Icon name="search" size={14} /><input value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)} placeholder="Search projects" aria-label="Search projects" /></label>
          <button className="project-add-button" type="button" onClick={() => void openWorkspacePicker()}><Icon name="plus" size={14} />Add project</button>
        </div>
        <div className="project-archive-label">ALL PROJECTS <span>{visibleProjectArchive.length}</span></div>
        {visibleProjectArchive.length > 0 ? <div className="project-archive-list">{visibleProjectArchive.map((project) => {
          const isCurrent = project.project_id === activeProjectId;
          const sessionCount = sidebarConversations.length;
          const sessionLabel = isCurrent ? `${sessionCount} chat${sessionCount === 1 ? "" : "s"}` : "Open to view chats";
          return <div className={`project-archive-row ${isCurrent ? "is-current" : ""}`} key={project.project_id}>
            <button className="project-archive-open" type="button" disabled={project.status === "MISSING" || projectSwitcherBusy || projectSwitchBlocked} onClick={() => void switchWorkspace(project.path)} aria-label={`Open ${project.name}`}>
              <span className="project-archive-chevron"><Icon name="chevron-right" size={15} /></span><span className="project-archive-icon"><Icon name="folder" size={18} /></span><span className="project-archive-main"><strong>{project.name}</strong><span>{shortPath(project.path, 66)} · {sessionLabel}</span></span>
            </button>
            <span className="project-archive-status">{project.status === "MISSING" ? "Missing" : isCurrent ? "Open" : "Available"}</span>
            <span className="project-archive-time">{projectSort === "Recent" ? formatTime(project.last_opened_at_ms) : project.name}</span>
            {!isCurrent && project.project_id.startsWith("project:") && <button className="project-archive-remove" type="button" onClick={() => void removeProject(project.project_id)} aria-label={`Remove ${project.name} from projects`} title="Remove from project list">Remove</button>}
          </div>;
        })}</div> : <div className="project-archive-empty"><Icon name="folder" size={18} /><strong>{normalizedProjectFilter ? "No projects found" : "No projects yet"}</strong><span>{normalizedProjectFilter ? "Try another search." : "Add a project to see it here."}</span></div>}
      </div>
    ) : (
      <div className="aegis-settings-stack"><section className="aegis-settings-group"><h2>{settingsSection}</h2><div className="aegis-settings-card"><div className="aegis-settings-empty"><Icon name={icons[settingsSection] ?? "info"} size={18} /><div><strong>{settingsSection === "Projects" ? shortPath(workspace?.workspace_path, 42) : "AEGIS local workspace"}</strong><span>{settingsSection === "Projects" ? "Project state is owned by the local host." : "This destination is available in the local-first shell."}</span></div>{settingsSection === "Projects" && <span className="aegis-settings-value">{sourceSnapshot?.files.length ?? "—"} files</span>}</div></div></section></div>
    );

    return <main className="destination-page settings-page"><aside className="settings-nav" aria-label="Settings sections"><div className="settings-nav-scroll"><button className="settings-back" type="button" onClick={() => openDestination("chat")}><Icon name="chevron-left" size={14} />Back to app</button><label className="settings-search"><Icon name="search" size={14} /><input value={settingsQuery} onChange={(event) => setSettingsQuery(event.target.value)} placeholder="Search settings…" aria-label="Search settings" /></label>{visibleGroups.map((group) => <div className="settings-nav-block" key={group.label}><span className="settings-nav-group">{group.label}</span>{group.items.map(([section]) => <button className={settingsSection === section ? "active" : ""} key={section} type="button" onClick={() => setSettingsSection(section)}><span className="settings-nav-item-label"><Icon name={icons[section]} size={14} />{section}</span></button>)}</div>)}{settingsSearch && visibleGroups.length === 0 && <p className="settings-search-empty">No matching settings</p>}</div></aside><section className="settings-content"><div className="settings-content-inner"><h1 className="settings-section-title">{settingsTitle}</h1>{content}</div></section></main>;
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
    const selectedModel = findModelForConnectionAndId(availableModels, activeConnectionId, modelId);
    const providerReady = runtimeReady && connectionSaved && Boolean(selectedModel);
    const projectReady = Boolean(activeProjectId) && runtimeReady;
    const mapReady = projectReady && Boolean(workspaceGraph && sourceSnapshot);
    const providerStatus = !runtimeReady ? "Runtime unavailable" : !connectionSaved ? "Connect provider" : !selectedModel ? "Choose model" : "Ready";
    const discoveredSkillCards = (extensionCatalog?.skills ?? []).map((skill) => [skill.name, skill.description, capabilityState?.records.some((record) => record.kind === "skill" && record.id === skill.name && record.enabled) ? "Enabled" : "Disabled", "skill"]);
    const discoveredExtensionCards = (extensionCatalog?.extensions ?? []).map((extension) => {
      const state = capabilityState?.records.find((record) => record.kind === "extension" && record.id === extension.extension_id);
      const approved = extension.wasm_plugin === true
        && state?.descriptor_hash === extension.descriptor_hash
        && state?.approved === true;
      const enabled = approved && state?.enabled === true;
      return [extension.extension_id, extension.description, enabled ? "Enabled" : approved ? "Approved" : "Available", "extension"];
    });
    const discoveredMcpCards = (mcpCatalog?.servers ?? []).map((server) => {
      const state = capabilityState?.records.find((record) => record.kind === "mcp" && record.id === server.server_id);
      return [server.server_id, `${server.description} · ${server.transport}`, state?.enabled ? "Enabled" : state?.approved ? "Approved" : "Metadata only", "mcp"];
    });
    const installedCards = [
      ["Source map", "Explore your project’s files, symbols, and dependencies.", mapReady ? "Available" : !activeProjectId ? "Choose project" : !runtimeReady ? "Runtime unavailable" : "Index unavailable", "map"],
      ["Memory", "Select a file in the project map to find related local memories.", mapReady ? "Available" : !activeProjectId ? "Choose project" : !runtimeReady ? "Runtime unavailable" : "Index unavailable", "memory"],
      ["Conversations", "Continue chats saved in the current workspace.", workspace?.open ? "Available" : "Workspace unavailable", "chat"],
      ["Provider adapter", "Choose the provider and model used for live replies.", providerStatus, "models"],
      ["Subagents", "Run parallel workers from a chat and follow their progress.", !parallelWorkersEnabled ? "Disabled" : providerReady ? "Ready" : providerStatus, "subagents"],
      ...discoveredSkillCards,
      ...discoveredExtensionCards,
      ...discoveredMcpCards,
    ];
    const cards = installedCards;
    const isConfigured = (status: string) => status === "Available" || status === "Ready" || status === "Enabled";
    const readyCount = installedCards.filter(([, , status]) => isConfigured(status)).length;
    const normalizedExtensionQuery = extensionQuery.trim().toLocaleLowerCase();
    const visibleCards = cards.filter(([title, description]) => !normalizedExtensionQuery || `${title} ${description}`.toLocaleLowerCase().includes(normalizedExtensionQuery));
    const selected = cards.find(([title]) => title === selectedSkill);
    const readyCards = visibleCards.filter(([, , status]) => isConfigured(status));
    const setupCards = visibleCards.filter(([, , status]) => !isConfigured(status));
    const openSetup = (section: string) => { setSettingsSection(section); openDestination("settings"); };
    const showDetails = (title: string, target: string) => {
      setSelectedSkill(title);
      setSelectedSkillBody(null);
      if (target === "skill") void loadSkillBody(title);
    };
    const openCapability = (title: string, target: string, status: string) => {
      if (target === "models") openSetup("Models");
      else if (target === "subagents") openSetup("Subagents");
      else if (target === "mcp") openSetup("MCP");
      else if (target === "skill") { if (status === "Enabled") showDetails(title, target); else openSetup("Skills"); }
      else if (target === "extension") showDetails(title, target);
      else if ((target === "map" || target === "memory") && !activeProjectId) openSetup("Projects");
      else { openDestination("chat"); if (target === "map" || target === "memory") { setWorkTab("map"); setWorkPanelOpen(true); } }
    };
    const renderInstalledRow = ([title, description, status, target]: string[]) => <div className={`extension-installed-row ${isConfigured(status) ? "active" : "attention"}`} key={`${target}:${title}`}>
      <span className="extension-installed-glyph"><Icon name={target === "mcp" ? "server" : target === "skill" || target === "extension" ? "plug" : extensionIcon(title)} size={15} /></span>
      <div className="extension-installed-copy"><div className="extension-installed-title"><strong>{title}</strong><span className="extension-local-tag">{status}</span></div><p className="extension-installed-note">{description}</p>{target === "extension" && (() => { const plugin = extensionCatalog?.extensions.find((item) => item.extension_id === title && item.wasm_plugin === true); return plugin ? <div className="extension-installed-details">Compute-only WASM · {plugin.tool_names.length} tool(s) · no direct network or file access · SHA-256 {plugin.module_sha256?.slice(0, 12) ?? "unavailable"}</div> : <div className="extension-installed-details">Metadata only; no executable tools are attached.</div>; })()}{target === "mcp" && <div className="extension-installed-details">{status === "Enabled" ? "Tools enabled. Check the server connection in MCP settings." : status === "Approved" ? "Approved for setup. Configure and test the connection in MCP settings." : "Review this server in MCP settings before enabling its tools."}</div>}</div>
      <div className="extension-installed-actions"><span className="extension-scope-chip">Local</span>{target === "extension" && (() => { const plugin = extensionCatalog?.extensions.find((item) => item.extension_id === title && item.wasm_plugin === true); const hash = plugin?.descriptor_hash; if (!plugin || !hash) return null; const record = capabilityState?.records.find((item) => item.kind === "extension" && item.id === title && item.descriptor_hash === hash); const approved = record?.approved === true; const enabled = approved && record?.enabled === true; const changeState = (nextEnabled: boolean, nextApproved: boolean) => void setCapabilityActivation("extension", title, hash, nextEnabled, nextApproved); return <><button type="button" onClick={() => changeState(!enabled, true)}>{enabled ? "Disable" : approved ? "Enable" : "Approve & enable"}</button>{approved && !enabled && <button type="button" onClick={() => changeState(false, false)}>Revoke</button>}</>; })()}<button type="button" disabled={target === "chat" && !workspace?.open} onClick={() => openCapability(title, target, status)}>{target === "extension" || target === "skill" && status === "Enabled" ? "Details" : (target === "map" || target === "memory") && !activeProjectId ? "Choose project" : target === "models" || target === "subagents" || target === "mcp" || target === "skill" ? isConfigured(status) ? "Manage" : "Set up" : "Open"}</button><button className="extension-row-more" type="button" onClick={() => showDetails(title, target)} aria-label={`Details for ${title}`} title="Details"><Icon name="more" size={15} /></button></div>
    </div>;
    return (
      <main className="destination-page extensions-page">
        <header className="destination-header extension-header">
          <div className="extension-heading">
            {sidebarCollapsed ? <button className="icon-btn" type="button" onClick={() => setSidebarCollapsed(false)} aria-label="Open navigation" title="Open navigation"><Icon name="sidebar" size={18} /></button> : <span className="extension-page-icon"><Icon name="plug" size={18} /></span>}
            <div><h1>Utilities</h1></div>
          </div>
          <div className="extension-actions">
            <button className="settings-secondary" type="button" onClick={() => void importCapabilityPack("folder")} disabled={extensionBusy || !hasNativeWindow()}>Import folder</button>
            <button className="settings-secondary" type="button" onClick={() => void importCapabilityPack("archive")} disabled={extensionBusy || !hasNativeWindow()}>Import ZIP</button>
            <button className="settings-secondary" type="button" onClick={() => void importSkillFolder()} disabled={extensionBusy || !hasNativeWindow()}>Import skill</button>
            <button className="primary-extension-action" type="button" onClick={() => void loadExtensionCatalog()} disabled={extensionBusy}><Icon name="refresh" size={14} />{extensionBusy ? "Scanning…" : "Scan local catalog"}</button>
          </div>
        </header>
        <p className="capability-settings-help">Import copies and validates files without running code. Review skills and server permissions before enabling tools.</p>
        {extensionNotice && <div className="extension-notice" role="status">{extensionNotice}</div>}
        <div className="extension-ready-banner">
          <div><span className="ready-mark"><Icon name="check" size={16} /></span><strong>{readyCount} local {readyCount === 1 ? "capability" : "capabilities"} available or enabled</strong></div>
          <button type="button" onClick={() => openSetup("Models")}>Model setup</button>
        </div>
        <div className="extension-toolbar">
          <div className="extension-local-summary"><strong>Local capabilities</strong><span>{extensionCatalog?.skills.length ?? 0} skills · {extensionCatalog?.extensions.length ?? 0} plugin manifests · {mcpCatalog?.servers.length ?? 0} MCP metadata</span></div>
          <label className="extension-search"><Icon name="search" size={14} /><input value={extensionQuery} onChange={(event) => setExtensionQuery(event.target.value)} placeholder="Search local capabilities" aria-label="Search local capabilities" /></label>
        </div>
        {selected && <article className="skill-detail"><div><span className="eyebrow">CAPABILITY DETAILS</span><h2>{selected[0]}</h2><p>{selected[1]}</p><small>{selected[2]}</small>{selected[3] === "skill" && selectedSkillBody && <pre className="skill-detail-body">{selectedSkillBody}</pre>}</div><button type="button" onClick={() => { setSelectedSkill(null); setSelectedSkillBody(null); }} aria-label="Close capability details">Close</button></article>}
        <div className="extension-installed-list">
          {setupCards.length > 0 && <section className="extension-installed-group"><div className="extension-installed-group-label">SETUP AND DETAILS <span>{setupCards.length}</span></div>{setupCards.map(renderInstalledRow)}</section>}
          {readyCards.length > 0 && <section className="extension-installed-group"><div className="extension-installed-group-label">AVAILABLE AND ENABLED <span>{readyCards.length}</span></div>{readyCards.map(renderInstalledRow)}</section>}
          {visibleCards.length === 0 && <div className="extension-installed-empty">No matching capabilities</div>}
        </div>
      </main>
    );
  }

  const shellSidebarWidth = clampSidebarWidth(sidebarWidth, sidebarWidthMax);
  const shellStyle = { "--aegis-sidebar-width": `${shellSidebarWidth}px`, "--work-width": `${workPanelWidth}px` } as CSSProperties;
  return <div style={shellStyle} className={`app-frame ${destination !== "chat" ? "destination-frame" : ""} ${destination === "settings" ? "settings-frame" : ""} ${sidebarCollapsed ? "sidebar-is-collapsed" : ""} ${workPanelOpen && destination === "chat" ? "work-panel-open" : ""}`}>
    {destination !== "settings" && !sidebarCollapsed && renderSidebar()}
    {projectSwitcherOpen && typeof document !== "undefined" && createPortal(renderProjectSwitcherMenu("home-project-switcher-menu project-switcher-popover", projectSwitcherPosition), document.body)}
    {destination === "chat" ? <>{renderConversation()}{workPanelOpen && renderWorkPanel()}{!workPanelOpen && !activeConversationId && <button className="app-work-panel-toggle no-drag" type="button" onClick={() => setWorkPanelOpen(true)} aria-label="Open project details" aria-expanded={false} title="Open project details"><Icon name="panel" size={15} /></button>}</> : destination === "settings" ? renderSettings() : renderExtensions()}
    {renderCommandPalette()}
    {renderSearchOverlay()}
  </div>;
}
