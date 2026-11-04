import {
  Activity,
  ArrowUp,
  ArrowUpDown,
  Bell,
  Blocks,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock,
  Compass,
  Cpu,
  Download,
  ExternalLink,
  File,
  FileText,
  Folder,
  FolderPlus,
  GitFork,
  Globe2,
  Grid2x2,
  Info,
  Keyboard,
  Layers,
  Map,
  Maximize2,
  MessageSquare,
  MessageSquarePlus,
  Mic,
  Minus,
  MoreHorizontal,
  PanelLeft,
  PanelRight,
  Paperclip,
  Plug,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Server,
  Settings,
  Shield,
  SlidersHorizontal,
  Square,
  Sun,
  Terminal,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";

export type IconMotionArchetype =
  | "spin"
  | "gear"
  | "quarter-turn"
  | "nudge-up"
  | "nudge-down"
  | "nudge-right"
  | "nudge-left"
  | "search-tilt"
  | "panel-left"
  | "panel-right"
  | "lift"
  | "scale";

export type CanonicalIconSize = 12 | 14 | 16 | 18;
export type IconContext = "inline" | "control" | "action" | "feature";

export const AEGIS_BRAND_PATH =
  "M32 4L56 16V28C56 44 44 52 32 60C20 52 8 44 8 28V16ZM32 12L16 20V28C16 32 16 36 20 40L32 24L44 40C48 36 48 32 48 28V20ZM32 36L24 48L32 52L40 48Z";

export function normalizeIconSize(size = 16): CanonicalIconSize {
  if (size <= 12) return 12;
  if (size <= 14) return 14;
  if (size <= 16) return 16;
  return 18;
}

export function resolveIconContext(size: CanonicalIconSize): IconContext {
  if (size === 12) return "inline";
  if (size === 14) return "control";
  if (size === 16) return "action";
  return "feature";
}

/*
 * Clean-room first-party icon registry & motion archetype mapping inspired by
 * animate-ui (v2.3+ / v2.4+). Every icon is paired with a deterministic
 * geometric motion archetype driven by parent button/control state (:hover,
 * :active, :focus-within, [aria-expanded="true"], [aria-busy="true"]) while
 * honoring prefers-reduced-motion and the zero-layout-transition gate.
 */
const icons: Record<string, LucideIcon> = {
  plus: Plus,
  "chevron-left": ChevronLeft,
  "chevron-right": ChevronRight,
  "chevron-down": ChevronDown,
  sidebar: PanelLeft,
  search: Search,
  sessions: MessageSquare,
  folder: Folder,
  "new-project": FolderPlus,
  branch: GitFork,
  extensions: Workflow,
  workflow: Workflow,
  settings: Settings,
  panel: PanelRight,
  more: MoreHorizontal,
  cpu: Cpu,
  spark: Cpu,
  layers: Layers,
  blocks: Blocks,
  skill: Compass,
  utilities: Grid2x2,
  terminal: Terminal,
  radio: Radio,
  attach: Paperclip,
  "arrow-up": ArrowUp,
  check: Check,
  activity: Activity,
  map: Map,
  mic: Mic,
  files: FileText,
  circle: Circle,
  external: ExternalLink,
  sun: Sun,
  bell: Bell,
  plug: Plug,
  "new-chat": MessageSquarePlus,
  shield: Shield,
  sliders: SlidersHorizontal,
  bot: Bot,
  keyboard: Keyboard,
  file: File,
  server: Server,
  globe: Globe2,
  download: Download,
  info: Info,
  refresh: RefreshCw,
  sort: ArrowUpDown,
  clock: Clock,
  minimize: Minus,
  maximize: Square,
  restore: Maximize2,
  close: X,
};

export const REGISTERED_ICON_NAMES = Object.keys(icons);

export const ICON_MOTION_ARCHETYPES: Record<string, IconMotionArchetype> = {
  refresh: "spin",
  settings: "gear",
  plus: "quarter-turn",
  close: "quarter-turn",
  "arrow-up": "nudge-up",
  external: "nudge-up",
  download: "nudge-down",
  "chevron-down": "nudge-down",
  "chevron-right": "nudge-right",
  "chevron-left": "nudge-left",
  search: "search-tilt",
  sidebar: "panel-left",
  panel: "panel-right",
  folder: "lift",
  "new-project": "lift",
  branch: "lift",
  extensions: "lift",
  workflow: "lift",
  cpu: "lift",
  spark: "lift",
  layers: "lift",
  blocks: "lift",
  skill: "lift",
  utilities: "lift",
  terminal: "lift",
  radio: "scale",
  shield: "lift",
  sliders: "scale",
  bot: "lift",
  keyboard: "lift",
  files: "lift",
  file: "lift",
  server: "lift",
  globe: "lift",
  plug: "lift",
  sessions: "lift",
  "new-chat": "lift",
  map: "lift",
  activity: "scale",
  check: "scale",
  info: "scale",
  clock: "scale",
  sun: "gear",
  bell: "lift",
  attach: "lift",
  mic: "scale",
  sort: "scale",
  more: "scale",
  circle: "scale",
  minimize: "scale",
  maximize: "scale",
  restore: "scale",
};

export function CapabilityIcon({
  name,
  size = 16,
  context,
  state,
}: {
  name: string;
  size?: number;
  context?: IconContext;
  state?: "idle" | "active" | "loading";
}) {
  const Icon = icons[name] ?? Circle;
  const archetype = ICON_MOTION_ARCHETYPES[name] ?? "scale";
  const normalizedSize =
    context === "inline"
      ? 12
      : context === "control"
        ? 14
        : context === "action"
          ? 16
          : context === "feature"
            ? 18
            : normalizeIconSize(size);
  const resolvedContext = context ?? resolveIconContext(normalizedSize);
  return (
    <Icon
      className="aegis-capability-icon"
      data-capability-icon={name}
      data-animate-ui-icon={archetype}
      data-icon-state={state ?? "idle"}
      data-icon-size={normalizedSize}
      data-icon-context={resolvedContext}
      size={normalizedSize}
      strokeWidth={1.75}
      aria-hidden="true"
    />
  );
}

export function AegisBrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      className="brand-logo aegis-brand-logo"
      data-brand-mark="aegis-monolith"
      data-animate-ui-icon="scale"
      viewBox="0 0 64 64"
      width={size}
      height={size}
      fill="none"
      aria-hidden="true"
    >
      <path d={AEGIS_BRAND_PATH} fill="currentColor" />
    </svg>
  );
}

