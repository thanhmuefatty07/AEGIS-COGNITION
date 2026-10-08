# Desktop conversation workbench

Design handoff, 2026-09-29. The implemented workspace centers on a conversation and an optional evidence inspector. A project supplies context; it is optional for starting a chat. This document records the current React/CSS contract, rendered observations, and remaining verification limits.

## Evidence and scope

| Item | Status | Basis / limit |
| --- | --- | --- |
| React/CSS in a Tauri desktop shell | Observed source | `desktop/src/main.tsx`, `desktop/package.json`. Retain the existing styling and interaction owners. |
| Profile-local and project-scoped chats | Observed source | `aegis_cognition/desktop_service.py`: `_workspace_root` defaults to `profile_root`; `_bind_workspace` uses the selected root's `.aegis/state.db`; `_list_conversations` uses the current manager and owner. |
| Current chat list/search | Observed source | `desktop/src/App.tsx`: `loadConversations` loads `conversations.list`; search filters that loaded list. This is not an all-project chat index. |
| Demo versus real inference | Observed source | `App.tsx` sends `mode: "mock" | "live"`; live sending checks the selected connection/model. `preview_backend.ts` supplies browser preview data. Neither Demo nor browser preview proves a provider request. |
| Font and icons | Observed source | System stack includes Segoe UI on Windows; `capability-icons.tsx` uses Lucide with stroke width 1.75. Manifest declares `lucide-react` 1.31.0. |
| Palette, geometry, and IA below | Rendered observations and source contract | Computed styles were read in both appearances. Home/General were checked across 48 viewport/theme/size combinations; the existing Windows debug shell displayed the current Vite renderer. This does not establish release packaging or Figma parity. |

Opening a project changes the active conversation store and file context. With no project, label the scope **Local chats** and offer **Choose a project**; with a project, show its name and label the list **Project chats**. Scope labels describe the active store, not a global aggregation. The profile store is not presented as an added project. New chat clears the conversation selection while retaining scope, draft, and attachments. A record is created on the first send and titled from that message. Unstarted records are filtered from the list without deleting their data. A scope switch refreshes the list and avoids displaying the previous scope's conversation as current.

## Information architecture

| Area | Purpose | Contract |
| --- | --- | --- |
| Left rail: New chat | Start work | Primary command, available without a project. |
| Left rail: Project scope | Choose context | Show the selected project or **Choose a project**; offer registered projects and existing clone/open-folder actions. |
| Left rail: Chats | Resume work | Show the current scope's chats, empty state, sort, and existing row actions. |
| Left rail: Utilities | Configure capabilities | Group existing tools and extensions into aligned lists; retain their real activation behavior. |
| Left rail: Settings | Set preferences | Group appearance, models/connections, instructions, and other existing settings. |
| Center | Converse | Transcript and composer share a reading column. Home presents a concise prompt and composer; avoid a feature-card dashboard. |
| Right inspector | Inspect evidence | Contextual **Files / Map / Activity / Context** tabs. Files and Map explain when a project is required. Activity and Context report available conversation evidence. |

Settings and Utilities share a layout: page title, short description, labeled groups, aligned rows, and trailing controls. Keep help beside the decision it explains. Preserve loading, empty, unavailable, and error states instead of replacing them with illustrative data.

## Use cases and states

| Use case / state | Visible response | Invariant |
| --- | --- | --- |
| No project, no active chat | Calm Home, **Choose a project**, Local chats, **Ask anything** composer | A repository is not required for a general chat. |
| New chat clicked repeatedly before sending | Home retains the draft; the chat list gains no rows | Persist a conversation only on its first send. |
| Project selected | Project name and Project chats; Files/Map become meaningful | Show only the current project's loaded chats and files. |
| Active conversation | Title, readable transcript, composer; inspector opens on demand | Maintain selection and transcript identity. |
| Chats loading / empty / failed | Loading cue / **No chats yet** / actionable error | An empty list must not stand in for a failed request. |
| Demo selected | Persistent **Demo** indicator near the composer/model control | Mock output is never labeled as real inference or completed external work. |
| Real inference ready | Selected provider and model are visible | Use the actual connected model catalog; do not invent availability. |
| Real inference unavailable | Explain the connection/model requirement and expose existing setup | Never silently imply a successful provider call. |
| Sending / waiting for approval | Progress or approval state; prevent conflicting sends | Preserve existing busy and approval guards. |
| External tool outcome is ambiguous after a restart | Explain that the effect may or may not have happened; offer an explicit two-step review only when no turn or local desktop run is active | Never retry the external action automatically. Reconcile only on the user's explicit assertion after checking the target; preserve that limitation in the audit record. AEGIS cannot independently verify, stop, or undo the external effect. |
| Inspector empty / unavailable | Specific explanation and a relevant next action | Never fabricate files, map nodes, activity, or context. |
| Scope switch blocked / failed | Explain the service result; preserve the current scope | Pending approvals and workspace-switch failures retain their existing safety behavior. |

Use explicit accessible names for icon actions, semantic buttons/inputs, visible keyboard focus, and the platform's existing primary modifier. Selection must remain visible without relying on accent color alone. Reduced-motion settings suppress decorative transitions. Native window chrome and operating-system behavior need separate evidence from a browser preview.

## Token contract

Current semantic tokens from `desktop/src/desktop-theme.css`, read back in the running renderer. A restrained cobalt accent carries selection and focus. Existing token names remain the styling contract.

| Role | CSS contract | Light | Dark |
| --- | --- | --- | --- |
| Canvas | `--surface-0` | `#F6F7F9` | `#171A20` |
| Sidebar | `--sidebar-surface` | `#EDF0F4` | `#13161B` |
| Elevated surface | `--surface-1` | `#FFFFFF` | `#1D222A` |
| Alternate surface | `--surface-2` | `#EDF0F4` | `#262D37` |
| Border | `--line` | `#DDE2EA` | `#333A45` |
| Primary text | `--text-primary` | `#20242D` | `#ECEFF4` |
| Secondary text | `--text-secondary` | `#626B7A` | `#B0BAC9` |
| Muted text | `--text-muted` | `#626B7A` | `#8894A7` |
| Accent | `--accent` | `#3157CD` | `#8BA4FF` |
| Selected surface | `--active` | `#E9EEFF` | `#242E4C` |

Do not infer accessible contrast from a token name. Check the actual foreground/background pairs, including muted metadata, selected rows, disabled controls, focus rings, and text placed on accent fills. A light foreground on the dark theme's lighter accent requires its own check.

| Geometry / type | Current contract / observed value |
| --- | --- |
| Reference viewport | 1280 × 800 CSS pixels |
| Major spacing | 8 / 12 / 16 / 24 / 32 px rhythm |
| Left rail | Observed 240 px at the reference viewport; default 343 px, allowed 240–520 px, limited by responsive available width and saved preference |
| Right inspector | Default 320 px, allowed 280–400 px; the browser review retained a saved 360 px preference |
| Reading column | 760 px maximum; shrink within available center width |
| Controls / navigation | 36 px primary controls/navigation; 32 px compact chrome controls |
| Radii | Controls 8 px; groups 12 px; composer 16 px |
| Interface body | Measured 14 px / 21 px line height at Grande |
| Transcript paragraphs | Measured 15 px / 23.25 px line height at Grande |
| Metadata / title | 12 px / 26 px |
| Typeface | Existing system stack; Segoe UI on Windows, native system fallback elsewhere |
| Icons | Existing Lucide family, stroke 1.75, consistent 16–18 px chrome slots |

At widths up to 1100 px, navigation collapses and an open inspector closes when the window is resized. Opening the inspector at widths up to 1180 px uses modal behavior; at widths up to 760 px it fills the content area and hides the inapplicable resize handle. Tabs use arrows/Home/End, modal Tab/Shift+Tab stays inside the panel, and Escape restores focus to the opener. The project picker also restores its opener on Escape. Narrow Utilities action rows account for their left indentation rather than extending beyond the group boundary.

UI presets are Tall 92%, Grande 100%, Venti 108%, and Trenta 116%. Transitions use 140/180 ms tokens. In the browser's observed reduced-motion environment, computed transition/animation durations were 0.01 ms. The busy-only 20 px ThinkingOrb supports a static reduced-motion frame in its inspected source; a sustained busy animation and frame rate were not measured.

## References and tooling limits

- [Libraries.dev Orbs](https://libraries.dev/orbs): public previews informed the motion reference. Installed `thinking-orbs@0.3.2`, whose inspected package declares MIT, React >=18, and no runtime dependencies. Its full license is retained in `desktop/THIRD_PARTY_NOTICES.md`. Studio Pro presets/customization/export were not accessed.
- Apple HIG [Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars), [Layout](https://developer.apple.com/design/human-interface-guidelines/layout), and [Motion](https://developer.apple.com/design/human-interface-guidelines/motion): references for hierarchy, navigation, and purposeful motion. The Tauri shell retains Windows chrome; these references do not certify Apple-native conformity.
- Apple HIG [Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons), [Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback), and [Keyboards](https://developer.apple.com/design/human-interface-guidelines/keyboards): references for action-labeled controls, nearby outcome feedback, and keyboard accessibility; they do not certify platform-native conformity.
- [Radix Tabs keyboard interaction](https://www.radix-ui.com/primitives/docs/components/tabs) and [W3C Tabs Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/): references for roving tab focus, keyboard movement, and panel relationships. Radix is not installed; the existing React/CSS controls implement these interactions.
- [Tailwind theme documentation](https://tailwindcss.com/docs/theme): reference for semantic design-token organization. The inspected renderer uses CSS; this is not a request to migrate styling systems.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/): reference for contrast, keyboard access, focus visibility, and reflow acceptance checks. Conformance is not established by this document.
- Design baseline: no approved visual reference was available for this audit snapshot; no pixel-parity claim is made.
- Firecrawl scrape returned insufficient credits on two attempts. Public browser inspection and primary-document fallbacks supplied the research evidence. This is a tool-access limit, not evidence that a page or asset is absent.

## Acceptance evidence and limits

The current audit records Home/General's 48 combinations, all 13 Settings sections in both themes at 1280×800/Grande, the four inspector tabs, active-chat/Context at all three viewports in both themes, and narrow Utilities/Instructions/picker behavior. Browser data came from the preview adapter; it does not prove live inference or a native folder picker. Native Windows observations include Home, an existing local conversation, General/Models, and the inspector's no-project/activity/context states. No native test message or provider credential was submitted.

The saved native Home capture is 1536×816 logical pixels; native checks also used outer windows of 1282×831 and 642×511, corresponding to 1280×800 and 640×480 content areas. The existing debug executable displayed the current Vite renderer. A fresh native build stopped because Windows SDK headers were unavailable; release packaging is not verified. Other operating systems, per-monitor DPI behavior, complete screen-reader/WCAG conformance, and live provider/worker behavior remain unverified. No approved Figma baseline exists, so no pixel-parity claim is made.

The ambiguous-outcome UI change was checked in Playwright with the full E2E suite (9 passed), including keyboard/focus semantics, reduced motion, active-run gating, light/dark viewports, and no horizontal overflow. Desktop tests passed (18 Node + 3 Vitest); focused Python protocol/projection tests passed (5). The broader Python protocol/projection sweep is not green (9 failed, 124 passed, 1 skipped): the bundled native runtime reports unavailable in this environment, and at least one source-snapshot fixture expects a non-Git directory while its temporary path is under this Git checkout. Native-dependent paths and the complete sweep therefore remain unverified here; do not present the broad suite as passing.
