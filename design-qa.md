# Desktop UI design QA

## Comparison target

- Pi reference home: `C:\Users\ADMIN\pi-desktop\docs\public\screenshots\app\en\dark-home.webp`
- Pi reference settings: `C:\Users\ADMIN\pi-desktop\docs\public\screenshots\app\en\dark-settings.webp`
- Pi reference extensions: `C:\Users\ADMIN\pi-desktop\docs\public\screenshots\app\en\extensions-mcp-dark.webp`
- AEGIS preview: `http://127.0.0.1:1421/`
- Normalized Pi references: `artifacts/design-qa/pi-dark-home-normalized.png`, `artifacts/design-qa/pi-settings-dark-normalized.png`, `artifacts/design-qa/pi-extensions-dark-normalized.png`
- Latest captured AEGIS references before the current live-only settings polish: `artifacts/design-qa/aegis-home-dark-pi-port-v11-1200.png`, `artifacts/design-qa/aegis-thread-dark-pi-port-v3-1200.png`, `artifacts/design-qa/aegis-settings-pi-port-v11-1200.png`, `artifacts/design-qa/aegis-marketplace-pi-port-v7-1200.png`
- Side-by-side references: `artifacts/design-qa/pi-vs-aegis-home-dark-port-v1.png`, `artifacts/design-qa/pi-vs-aegis-settings-dark-port-v1.png`, `artifacts/design-qa/pi-vs-aegis-marketplace-dark-port-v1.png`

## Findings and fixes

### P1 — Full Pi surface parity is not yet proven

- Evidence: Pi has additional product pages and controls that AEGIS does not yet implement as the same routes/backend, including its real extension marketplace, search surfaces, richer model/import/project settings, and native titlebar behavior.
- Impact: the implemented AEGIS shell can look Pi-like, but it would be inaccurate to call the whole application a 1:1 clone.
- Status: open by design; AEGIS-specific backend and local-first safety boundaries remain authoritative.

### P1 — Home empty-state ownership differed from Pi

- Evidence: AEGIS always had a global workspace, so the blank Home surface rendered `What can we build in AEGIS-COGNITION?`; Pi's `ChatSurface` only injects a project name when the active session itself is project-bound. Its ordinary blank surface therefore uses `What would you like to explore temporarily?` even when the sidebar contains an open project.
- Fix: keep the active blank conversation out of the historical Sessions list and restore Pi's temporary Home hero. The searchable project switcher remains available from the project overflow menu, so switching, Clone, and native Open actions are not removed from the product shell.
- Verification: at the live preview, the AX heading is now exactly `What would you like to explore temporarily?`; the project overflow exposes `Switch project`, whose menu exposes `Search projects`, the checked current project, `Clone project`, and `Open project`.

### P1 — Project switcher actions were initially visual-only

- Evidence: the Pi switcher provides project search, Clone, and Open actions; the earlier AEGIS menu only listed the current workspace and routed Open to the work panel.
- Fix: port the list/Clone view shape, add a bounded `workspace.switch` command, add a host-side `workspace.clone` command with public-host validation and a time limit, reset workspace-owned managers on switch, and add a cross-platform native folder picker command through `rfd`.
- Verification: preview opened the full `Search → current project → Clone → Open` menu; Clone moved the preview into `project`; Python tests cover switch state-store rebinding and approved-host clone behavior; the Windows Tauri build produced fresh EXE/MSI/NSIS artifacts. Native folder-picker interaction itself remains `NOT VERIFIED` because the Windows computer-use bridge was unavailable.

### P1 — Work Panel lacked Pi's independent resize contract

- Evidence: Pi's `WorkPanel` has a left-edge `role="separator"`, a 244px minimum, persistent width, pointer resize, keyboard resize, and a raised tab strip. AEGIS previously had a fixed 360px panel with no separator.
- Fix: add the Pi-shaped separator, persisted panel width, live compact-viewport budget, pointer dragging, and ArrowLeft/ArrowRight/Home/End keyboard control while preserving AEGIS' Files/Map/Activity/Context tabs.
- Verification: live accessibility tree exposes `Resize workspace panel` with value `360`; ArrowLeft changed it to `376`, ArrowRight restored `360`. Production build passed after the change.

### P2 — Settings row geometry was visibly different

- Evidence: the first AEGIS port rendered one tall grouped panel, while Pi renders separate rounded rows with gaps; AEGIS General also exposed a provider endpoint row that Pi keeps out of General.
- Fix: split the visual rows into separate Pi-style cards, add the Pi-like font-size controls and proxy segmented control, add the missing Agent → Extensions destination, and keep provider configuration under Models.
- Verification: live browser screenshot and accessibility tree after hot reload; `General`, `Extensions`, `Font size`, `Proxy mode`, and `Back to app` were all present.

### P2 — Extensions header and marketplace rhythm were too tall

- Evidence: AEGIS initially added a secondary explanatory subtitle and taller cards; Pi uses a compact title row, readiness band, tabs, catalog selector, filters, and compact three-column cards.
- Fix: remove the redundant subtitle, retain the AEGIS capability catalog and action wiring, keep the three-column marketplace layout, and preserve Installed/Marketplace tab behavior.
- Verification: live browser navigation from Settings → Extensions, then Installed → Marketplace; selected tab and four marketplace cards were visible.

### P1 — Settings content retained an AEGIS width cap

- Evidence: at an explicit `1600 × 1080` viewport, `.settings-content` computed to `900px` and left a large unused band on the right, while Pi's Settings content fills the main column.
- Fix: remove the legacy `max-width: 900px` cap from the Settings content and make `.settings-content-inner` fill the available column, preserving Pi's 275px rail and content padding.
- Verification: live measurement now reports `.settings-content = 1325px`, `.settings-content-inner = 1245px`, and the preference card spans the full content envelope.

### P1 — Installed Extensions used the wrong layout primitive

- Evidence: Pi's `InstalledPluginsPanel` renders grouped full-width rows (`Needs attention`, `Active`, and other states), while AEGIS initially rendered Installed with the same card grid used by Marketplace.
- Fix: split the renderer into Pi-style grouped installed rows with scope/action controls, setup/error state, search filtering, and a separate Marketplace-only catalog bar/category row. Marketplace keeps the Pi-style card grid.
- Verification: live preview showed `Needs attention 1`, `Active 5`, row-level Open/View details/More actions, and the Marketplace tab retained its separate 2/3-column card surface.

### P2 — Plugin header overflow actions were inert

- Evidence: the Pi plugin page exposes a real overflow menu; AEGIS' former More button only cleared a selection and gave no visible menu.
- Fix: add the Pi-shaped five-item overflow menu and connect each item to an explicit host-adapter notice rather than claiming unsupported install/update behavior.
- Verification: live accessibility state exposed the menu items and `Check for updates` produced a visible status notice.

### P2 — Pi's Projects and Models settings were under-modeled

- Evidence: AEGIS previously showed a generic fallback card for Projects and a raw endpoint form for Models; Pi presents a Project archive and a Model configuration page with defaults, providers, vendor accounts, and catalog controls.
- Fix: add a Pi-shaped Project archive backed by the current workspace/session state, and a Pi-shaped Model configuration page that keeps endpoint/model editing behind provider actions.
- Verification: live browser navigation reached `Project archive` and `Model configuration`; `Add project` opened the existing workspace file panel and `Edit provider` exposed the existing connection editor.

### P2 — Settings search was only visual

- Evidence: the search pill existed but did not filter destinations.
- Fix: connect it to the Settings rail; matching groups/items remain visible and an empty result is explicit.
- Verification: entering `Models` left only the matching Agent → Models item in the rail.

### P2 — Import was a generic placeholder

- Evidence: Pi's Import section has separate session, model, skills, and MCP scan cards; AEGIS had one generic workspace card.
- Fix: add the four Pi-shaped scan cards and an explicit host-adapter status message. No external data is claimed to be imported without a real adapter.
- Verification: live browser opened all four cards; the session Scan action reported `No importable sessions found on this machine.`

### P2 — AI settings lacked Pi's control hierarchy

- Evidence: AEGIS previously exposed only three generic rows; Pi separates Permissions, Voice, and Defaults and exposes mode, thinking, context, paste, and Enter-to-send controls.
- Fix: add the same grouped hierarchy and connect Enter-to-send to the composer behavior; the remaining controls keep local AEGIS state without pretending to persist unsupported host settings.
- Verification: live browser showed the full AI page; toggling Enter to send changed its switch state and description to Ctrl/⌘+Enter.

### P2 — Agent capability destinations were still generic fallbacks

- Evidence: Pi separates Instructions, Shortcuts, Skills, MCP, and Subagents into dedicated capability pages; AEGIS previously routed several of these destinations to one generic card.
- Fix: add Pi-shaped pages for global instructions, keyboard shortcuts, skills, MCP servers, and built-in subagents. The pages expose the existing AEGIS local worker concepts and clearly label MCP/instruction persistence boundaries instead of simulating unavailable host adapters.
- Fix: consolidate the global keyboard shortcut listener so Ctrl/⌘ K, Ctrl/⌘ B, Ctrl/⌘ J, and Escape have one owner; clear page-local notices when changing Settings destinations.
- Verification: live accessibility checks reached all five pages; Save instructions reported a session-local save, MCP Add reported the disconnected adapter state, and a worker switch changed `aria-checked` from `true` to `false`.

### P1 — Global search behavior did not match the Pi interaction model

- Evidence: the Pi topbar search button opens a global spotlight; AEGIS initially reused it as a file-panel shortcut.
- Fix: add a Pi-shaped search overlay with local Sessions, Files, Pages, and Settings results. Selecting a result returns to the existing AEGIS conversation, file panel, settings, or destination route.
- Verification: live browser opened the overlay, showed the grouped result tree, filtered `Models`, and navigated to `Model configuration`; `Pull requests` also navigated to its route.

### P1 — Settings collapsed at the constrained desktop breakpoint

- Evidence: at the 762px preview viewport, the legacy `.app-frame.destination-frame` breakpoint overrode the Pi Settings shell. Direct layout measurement showed `.settings-page` width 236px, `.settings-content` width 56px, and the content was visually absent even though the accessibility tree still exposed it.
- Fix: add a final breakpoint override so the outer Settings frame remains one column; the Settings page owns its internal navigation rail at every supported width.
- Verification: after hot reload, direct layout measurement showed `.settings-page` width 762.4px, `.settings-content` width 542.4px, and `rootGrid` `762.4px`; the General page rendered visibly.

### P2 — Pi destination routes were missing from the AEGIS shell

- Evidence: Pi exposes Pull requests, Scheduled, and Plugins surfaces; AEGIS previously routed all non-settings destinations to Extensions.
- Fix: add Pi-shaped Pull requests and Scheduled empty/create surfaces with explicit local-adapter status, and distinguish the Plugins route while reusing the existing capability catalog and host-safe actions.
- Verification: global search navigated to Pull requests and exposed Refresh/Open project; Plugins is reachable from the sidebar and Scheduled from global search.

### P2 — Destination pages still used an AEGIS-specific placeholder rhythm

- Evidence: the first route pass used a custom title/form/empty card, while Pi's source uses `.page-frame`, `.page-header`, segmented `.dest-filters`, `.dest-create`, `.dest-section-label`, and `.page-empty` primitives.
- Fix: port those shared Pi primitives into the AEGIS route markup. Pull requests now has Open/Draft/All tabs plus Review/Refresh actions; Scheduled now has Pi-shaped Create task, New task, Prompt, Cadence, Create task, Tasks, and clock empty-state surfaces.
- Verification: live preview showed the route accessibility tree and screenshots after hot reload; native AEGIS state remains explicit where no repository/scheduler adapter exists.

### P2 — Preview status crowded Pi's sidebar footer

- Evidence: the visible `Preview · local` status wrapped under the footer icons at the constrained preview width, while Pi keeps the footer limited to actions and version.
- Fix: remove the status line from the visual sidebar; runtime state remains available in the Settings/Info and conversation/work-panel status surfaces.
- Verification: the live Home screenshot now ends with the Pi-like footer action row and version only.

### P1 — Native chrome and collapsed-sidebar behavior were visibly different

- Evidence: the earlier shell kept a collapsed sidebar column in the layout, had no renderer window controls, and left the Tauri window using the default titlebar path.
- Fix: add Pi-shaped minimize/maximize/restore/close controls for Windows/Linux, reserve their 120px hit band, use a macOS overlay titlebar configuration, and make sidebar collapse remove the rail while exposing expand/new-thread controls in the topbar.
- Verification: live browser screenshot showed the controls, collapsed shell, and restored shell; native Tauri calls are guarded behind runtime detection. Packaged OS rendering remains `NOT VERIFIED`.

### P2 — Sidebar section controls and composer copy were not exact

- Evidence: the Sessions header only had one generic plus action, Projects had no matching action, and the dark composer used `Ask anything` instead of Pi's command/file hint.
- Fix: add Pi-like sort/new-session controls, a Projects action that opens the existing workspace panel, a working local sort menu, and the exact Pi composer placeholder. The AEGIS logo mark was redesigned as a shield/check mark while retaining the AEGIS identity.
- Verification: live screenshot showed both section toolbars and the exact composer hint; the sort menu opened and changed its selected option.

### P2 — Sidebar section actions were too visually loud

- Evidence: AEGIS rendered the Sessions and Projects action icons permanently, while Pi keeps those controls quiet until hover or keyboard focus.
- Fix: apply Pi's reveal-on-hover/focus opacity behavior without removing the buttons or their existing handlers.
- Verification: latest Home screenshot shows a quiet Sessions/Projects rail; the controls remain in the accessibility tree and are still interaction targets.

### P2 — New-session wording was inconsistent across the shell

- Evidence: the visual topbar used `New task`, while the newly created backend record and sidebar row exposed `New thread`.
- Fix: keep the backend record/API title unchanged for compatibility, but normalize the visible and accessible shell labels to `New task`.
- Verification: the latest accessibility tree reports `New task` consistently for the active row, topbar action, search result, and project action.

### P2 — Theme setting claimed system support but only toggled two modes

- Evidence: the Pi General page exposes a System/Dark/Light choice, while AEGIS showed the Pi description but only toggled Dark and Light.
- Fix: add the `System` state, resolve it through `prefers-color-scheme`, update the document theme reactively, and keep the existing local persistence key.
- Verification: TypeScript/Vite build passed and the refreshed native bundle was rebuilt after the change.

### P1 — Sidebar width was fixed instead of using Pi's resize contract

- Evidence: Pi exposes a vertical separator with a 240–520px width range, pointer dragging, keyboard arrows/Home/End, persistence, and collapse-on-narrow-drag. AEGIS previously hard-coded 275px and had no separator in its accessibility tree.
- Fix: add the Pi-shaped separator, live width budget for the main pane/work panel, pointer resize, keyboard resize, local persistence, and collapse threshold; the existing collapse button remains unchanged.
- Verification: live accessibility tree exposes `splitter — Resize sidebar — Value: 275`; ArrowRight changed the measured CSS variable and value to `291px`, ArrowLeft restored `275px`.

### P2 — Sidebar and search keyboard behavior needed Pi interaction parity

- Evidence: global search had only click selection, and selecting a session from another route did not always return to Chat.
- Fix: add ArrowUp/ArrowDown selection and Enter activation to the search spotlight; selecting a conversation now explicitly returns to Chat and closes the overlay.
- Verification: live browser moved the active result with ArrowDown and selected a session from Plugins back to Chat.

### P2 — Sidebar row actions were visually absent

- Evidence: Pi keeps session/project row actions available on hover; AEGIS only exposed section-level actions, so the sidebar looked flatter during interaction.
- Fix: add Pi-shaped hover actions to session rows, with a real menu for Activity, Context, and Files; keep the project new-thread action and preserve the existing conversation/workspace handlers. Expand session sorting to Recently updated, Oldest first, Name, and Created date.
- Verification: the fresh preview accessibility tree exposes the row menus and four sort choices; the Home screenshot retains Pi's quiet default state because the controls reveal on hover/focus.

### P2 — Surface tokens and Settings route coverage were incomplete

- Evidence: the dark sidebar was visibly lighter than Pi's `#000` rail, the composer surface was slightly off the Pi neutral ramp, and the Settings rail lacked Pi's Remote Hosts destination.
- Fix: align the dark Pi surface ramp (`#181818` canvas, `#212121` raised surface, `#282828` secondary surface, black sidebar), remove the always-on session dots, restore the Pi `Ask anything` placeholder, add Remote Hosts with an explicit disconnected-host state, and keep adapter notices neutral rather than green-success.
- Verification: live Home/Settings/Remote Hosts screenshots and accessibility trees after hot reload; production build passed.

### P2 — Home and thread parity required source assets, not a recreated mascot

- Evidence: the exact Pi mascot animation and still fallback were available in the cloned Pi repository.
- Fix: use the Pi GIF assets in normal motion and Pi PNG assets under reduced motion; keep AEGIS message, subagent, file, and host callbacks intact.
- Verification: production build emitted all four assets; home screenshot showed the Pi mascot; non-empty thread smoke test still completed.

### P2 — Home visual silhouette still exposed AEGIS-only status chrome

- Evidence: the Home composer kept a local environment/branch status row below the rounded shell, while Pi's Home surface ends at the composer shell. This was a visible difference even though the state itself is useful elsewhere.
- Fix: keep the status available on thread/work-panel surfaces but hide it only from the Home composer; tighten the Windows/Linux brand slot to Pi's compact toolbar spacing.
- Verification: live Home reload shows the composer ending at the same visual boundary as Pi; backend controls and the composer action handlers remain present in the DOM.

### P1 — Visual comparison must separate layout mismatch from capture conditions

- Evidence: the live preview is `762.4 × 697.6` CSS pixels at DPR `1.25`; the checked-in Pi references are captured at a larger desktop size and the Home reference uses a macOS-style titlebar context. The live Windows/Linux shell therefore shows native window controls and a narrower content area even when the shared Pi geometry is the same.
- Conclusion: current live evidence proves the shared shell geometry at the constrained viewport, not pixel identity across OS titlebars or larger windows. A packaged Windows/macOS/Linux pass is still required before claiming 1:1 parity.

### P1 — Topbar DOM and panel-control ownership caused a visible Pi mismatch

- Evidence: Pi's source uses `ct-left`, `ct-title-wrap`, `ct-right`, and `ct-actions`; its Work Panel toggle is outside the conversation titlebar and the open panel owns its header controls. AEGIS initially kept a single custom `.conversation-title`/`.topbar-actions` block and placed the panel button inside it.
- Fix: port the Pi topbar grouping while retaining AEGIS callbacks; render the closed-panel toggle in the Pi control lane and render Close/Toggle controls in the Work Panel header. The active project dot and brand button now also follow the Pi header slots.
- Verification: live AX state exposes a `Conversation` toolbar with New task/Search, a separate Toggle work panel button, and an open panel with Close workspace panel + Toggle work panel. Live screenshots show the controls in the same visual lanes as the Pi Home and panel references.

### P1 — Home stack stretched across the conversation pane at desktop width

- Evidence: at an explicit `1280 × 800` desktop viewport, the live Home stack computed to the full `1005px` conversation width even though Pi's source caps `.home-stack-inner` at `768px`. The narrow preview hid the defect because the conversation pane was already smaller than the cap.
- Fix: remove the unintended flex growth and keep the stack at `min(100%, 768px)`, matching Pi's `home-stack-inner` contract. The composer already used the same 768px cap.
- Verification: live layout measurement after hot reload reports `home-stack-inner = 768px` at `x = 393.5px`, `empty-hero = 744px`, and `.composer-pill = 768px`; the desktop screenshot now keeps the mascot, headline, and composer in the same centered width envelope as the Pi reference.

### P2 — Files panel used a flat AEGIS list instead of Pi's tree primitive

- Evidence: live Work Panel Files showed language badges (`PY`, `RS`, `JS`) and full relative paths in flat rows; Pi's `FilesTab` uses folder/file rows, indentation, quiet icons, and basename labels.
- Fix: keep the indexed AEGIS snapshot, search, selection, and inspector, but render the visible list as a deterministic hierarchical folder/file tree using Pi's row spacing and icon treatment.
- Verification: live AX now exposes folder nodes (`aegis_cognition`, `core`, `desktop`, `src`) and basename file buttons (`agent.py`, `gt96.rs`, `App.tsx`, `styles.css`); `npm run build` passed after the renderer/CSS change.

### P2 — Compact Work Panel falsely clamped the sidebar accessibility value

- Evidence: at the live `762px` preview width, CSS correctly rendered the Work Panel as a fixed overlay, but the sidebar splitter reported `240px` because its max-width calculation subtracted the overlay width.
- Fix: share the Pi `1180px` overlay breakpoint in the sidebar budget calculation; overlay panels no longer consume grid width when calculating the sidebar range.
- Verification: live DOM measurement now reports sidebar splitter `aria-valuenow = 275`, actual sidebar width `275px`, and Work Panel width `360px` at the same compact viewport.

### P2 — Work Panel header exposed four tabs instead of Pi's compact active-tab model

- Evidence: the AEGIS panel header previously displayed Files/Map/Activity/Context simultaneously, while Pi shows one active tab with a compact selector, then close/toggle controls.
- Fix: keep all four AEGIS panel handlers, but render one active Pi-shaped tab and a keyboard-accessible menu for switching between Files, Map, Activity, and Context. Move the close affordance into the active tab, matching Pi's resource-tab ownership; the panel toggle remains in the dock action lane.
- Verification: live AX state exposes one selected `Files` tab, `Close Files`, and `Toggle work panel`; opening the tab exposes a `Workspace panel tabs` menu with all four real destinations, and closing the active tab returns to the clean Home surface.

### P2 — Renderer typography and brand mark still carried legacy AEGIS/Pi mixing

- Evidence: Pi's base contract uses a 14px body rhythm; AEGIS was still inheriting the legacy 16px body size. The visible header mark also came from copied Pi brand assets while the product label remained AEGIS.
- Fix: set the renderer body to Pi's 14px/1.45 rhythm and replace the copied Pi brand image with the local AEGIS shield mark at the same 20px hitbox. The unused copied brand files were removed; the mascot assets remain documented and used only by Home.
- Verification: live DOM reports body `14px` and `20.3px` line-height, the AEGIS SVG mark is `20 × 20px`, `npm run build` passed, and the latest `npm run tauri:build` regenerated all Windows artifacts.

### P2 — Sidebar section typography was visually smaller than Pi

- Evidence: the live sidebar section labels were 11px/0.06em while Pi's source token is 12px/0.02em; the difference was visible even when the shell geometry matched.
- Fix: align the section labels to the Pi token rhythm without changing sidebar state or action ownership.
- Verification: the live Home/Settings/Extensions surfaces remain overflow-free; frontend and native builds passed after the typography adjustment.

### P2 — Home state correction required a native rebuild

- Evidence: the Home-state and sidebar project-switcher changes are renderer changes, so a browser build alone would not prove that the packaged desktop bundle contains them.
- Fix: rebuild the Tauri release after the visual correction while keeping the existing native workspace picker/clone commands unchanged.
- Verification: `npm run tauri:build` passed; fresh artifacts were produced at `desktop/src-tauri/target/release/aegis-desktop.exe`, `desktop/src-tauri/target/release/bundle/msi/AEGIS_0.1.0_x64_en-US.msi`, and `desktop/src-tauri/target/release/bundle/nsis/AEGIS_0.1.0_x64-setup.exe`. The native build was rerun after the Files tree and compact-sidebar fixes. Native interactive rendering remains `NOT VERIFIED` because the Windows computer-use bridge is unavailable.

### P2 — Sidebar collapse glyph and macOS brand selector were not source-aligned

- Evidence: Pi's `IconSidebar` is a panel-with-divider glyph, not a chevron; Pi hides the complete `.sidebar-header > .brand` slot on macOS. AEGIS had used chevrons and targeted retired brand sub-elements, so the macOS rule could not remove the visible slot.
- Fix: use the panel glyph for expanded/collapsed sidebar controls and target AEGIS's actual `.sidebar-brand > .brand` wrapper for the macOS rule. Windows/Linux keep the AEGIS label, matching Pi's platform-specific source behavior.
- Verification: the live Windows preview shows the panel glyph in the header; the platform selector is `win32`; the latest TypeScript/Vite build passed. Cross-OS native rendering remains `NOT VERIFIED`.

### P2 — Home and conversation title copy/behavior differed from Pi (superseded by current-state correction)

- Evidence: Pi's English locale uses `What can we build in {{project}}?`; AEGIS rendered a different sentence. Pi also truncates conversation titles to ten Unicode characters in the topbar while retaining the full title for the tooltip.
- Fix: keep Pi's ten-character Unicode topbar truncation while applying Pi's temporary-home copy when no project-bound transcript is active; project switching remains available in the sidebar overflow.
- Verification: current live AX exposes `What would you like to explore temporarily?`; the title tooltip still uses the full task title. `npm run build` and the latest Tauri bundle passed after this renderer change.

### P2 — Composer containment did not use Pi's source layers

- Evidence: the visible shell matched Pi, but the renderer had only the AEGIS-specific `composer-pill` wrapper; Pi's source separates `composer-dock`, `composer-stack`, and `composer-shell` for pointer ownership, width caps, and focus behavior.
- Fix: retain the AEGIS controls and callbacks while porting those Pi containment layers and the source-aligned topbar lead semantics.
- Verification: the live Home AX tree now exposes the composer as a nested container around the text entry, and layout measurement preserves the Pi geometry (`composer-dock = 439px` at the default preview and the 768px cap at a wide viewport). `npm run build` passed.

### P2 — Project row ownership differed from Pi

- Evidence: clicking the only project row opened the AEGIS Work Panel, while Pi's project group toggles its session body and keeps project actions in a separate overflow menu.
- Fix: make the project row expand/collapse its session body, add the Pi-style hidden overflow control, and connect its supported actions to AEGIS file inspection, task creation, and Project settings.
- Verification: live AX exposes `expanded/collapsed` project state and the overflow menu exposes `Open project files`, `New task`, and `Project settings`; no unsupported archive/delete actions were invented.

## Verified behavior

- Home: Pi-like 275px sidebar, 46px topbar, centered mascot/headline, 768px composer cap, 20px composer radius, input-first toolbar, and the Pi temporary-home copy when no active transcript exists.
- Thread: compact right-aligned user bubble, left-aligned assistant prose, hidden avatar/meta rail, and docked composer; the local send flow returned a response.
- Sidebar: sessions, project, Settings, Skills/Extensions, Activity, and version controls remain wired to AEGIS actions.
- Sidebar rows: session Activity and project New thread hover actions are present and wired to existing AEGIS state.
- Sidebar row menus: session More actions expose Activity, Context, and Files; Sort sessions exposes all four Pi choices.
- Settings: dedicated Pi-style rail, grouped navigation, General visual controls, provider configuration under Models, and local Extensions route.
- Settings Projects/Models: archive, sort/search/add controls, provider summary, default model, vendor-account empty state, catalog action, and working editor action.
- Settings search: filters navigation without changing the current content until the user selects a result.
- Global search: Pi-style overlay, grouped local results, filtered query state, and route selection are present.
- Import: four scan surfaces are visible and their empty/adapter state is explicit.
- AI: Permissions, Voice, Defaults, mode, thinking, context, paste, and Enter-to-send surfaces are present.
- Agent capabilities: Instructions, Shortcuts, Skills, MCP, and Subagents pages are present; local worker toggles and explicit adapter boundaries are visible.
- Settings route coverage: Remote Hosts is present with an honest disconnected adapter state.
- Extensions: Installed/Marketplace tabs, search field, catalog selector, category filters, readiness band, and three-column cards.
- Extensions parity: Installed now uses grouped list rows; Marketplace alone uses the card grid and catalog/category controls.
- Pi destinations: Pull requests, Scheduled, and Plugins are routable; unavailable adapters are explicit.
- Pi destination geometry: Pull requests and Scheduled use the copied frame/filter/form/empty-state rhythm; their adapter boundaries remain explicit.
- Window shell: renderer window controls, Pi-style collapsed-sidebar flow, section sort controls, and project action are present; native packaged behavior is still explicitly unverified.
- Home project surface: blank active sessions stay out of the historical sidebar list; project switching stays in the project overflow surface so the temporary Home hero keeps Pi's exact blank-state copy.
- Project row behavior: the project group now expands/collapses like Pi and exposes a scoped overflow menu for supported AEGIS actions.
- Project preference: the expanded/collapsed state is persisted locally and was verified across a preview reload; it does not alter workspace/session backend data.
- Work Panel: Pi-shaped left-edge resize separator, persisted width, compact-viewport budget, and keyboard resize are present; AEGIS' real workspace tabs remain connected.
- Work Panel chrome: Pi-shaped close/toggle control ownership is preserved; AEGIS Files/Map/Activity/Context tab callbacks remain connected.
- Work Panel tab chrome: the active surface uses Pi's compact tab-selector shape; the four AEGIS tabs remain reachable through its menu without adding a fake backend surface.
- Brand slot: the clickable brand slot keeps Pi's geometry and platform behavior, uses Pi's source dark/light brand pair at 20px, and now renders the visible Pi shell label; AEGIS backend ownership remains unchanged.
- Conversation chrome: Pi-shaped left/right titlebar groups are present; the separate panel toggle remains an AEGIS-connected control.
- Conversation copy: the temporary Home copy and topbar title truncation are source-aligned while AEGIS keeps its own project/session data; project-specific copy is reserved for project-bound transcript states.
- Search: keyboard result navigation and Enter activation are present in addition to click selection.
- Platform rule: the Pi shell brand is shown on Windows/Linux and hidden on macOS, matching the Pi titlebar distinction.
- A fresh browser tab reported no console errors; the original hot-reload tab retained one historical HMR error from an intermediate syntax patch, which disappeared after the final build/reload and did not recur.

## Intentional differences and residual risk

- AEGIS keeps real local workspace, protocol, runtime, mailbox/subagent, and provider state instead of Pi's sample session data and marketplace records.
- The AEGIS marketplace cards are a local capability catalog; they are not evidence of a live Pi/GitHub marketplace integration.
- AEGIS retains its backend identity and local data ownership; the visible desktop shell follows Pi's brand treatment, while the Home mascot and compact brand slot use documented Pi visual assets with the upstream notice preserved.
- Thread parity is source-structure-checked and smoke-tested, but Pi has no matching checked-in thread screenshot in the captured reference set: `NOT VERIFIED` for pixel-level thread parity.
- Tauri now requests macOS overlay titlebar behavior and disables decorations at runtime only for non-macOS native windows; OS-specific device pixel ratio, packaged-window rendering, and actual native window control calls remain `NOT VERIFIED` until each target OS is run.

## Verification commands

- `npm run build` — passed after the capped Home stack, Pi topbar grouping, compact Work Panel tab selector, outside-click state cleanup, sidebar brand slot, and Pi assets; TypeScript and Vite completed.
- `uv run pytest tests/test_desktop_protocol.py tests/test_desktop_projection.py` — 21 passed on Python 3.14.7.
- `cargo check --manifest-path desktop/src-tauri/Cargo.toml --no-default-features` — passed.
- `git diff --check` — passed; only expected Git line-ending warnings remain.
- Latest live reload — no console error entries; only Vite connection/HMR diagnostics and the normal React DevTools info message were observed.
- `npm run tauri:build` — passed on Windows after the Home stack cap, compact Work Panel tab selector, and outside-click state cleanup; produced `desktop/src-tauri/target/release/aegis-desktop.exe`, the MSI bundle, and the NSIS installer. Native visual interaction remains `NOT VERIFIED` because the Computer Use Windows bridge was unavailable in this run.
- Live browser navigation and accessibility checks — passed for Home, Settings, Extensions, Marketplace, and thread smoke flow.
- Live browser navigation and accessibility checks — passed for Shortcuts, Instructions, MCP, and Subagents; no user-facing window was opened.
- Live browser visual/geometry check — caught and verified the Settings breakpoint regression at a constrained desktop viewport; global search and Pull requests route were exercised in the visible preview.
- Live browser visual check — verified the Pi-shaped titlebar controls, section toolbars, sort menu, collapsed/restored sidebar, composer hint, work-panel control lane, and Settings back-row alignment.
- Live browser visual check — verified the black Pi-style sidebar surface, neutral disconnected-host notice, session action menu, four-option sort menu, and Extensions title after the final hot reload.
- Live browser visual check — at `1280 × 800`, caught the stretched Home stack, verified the 768px cap fix, opened the Work Panel tab selector, and verified its four real AEGIS destinations through the accessibility tree.
- Live browser visual check — after comparing at the Pi reference scale, confirmed the shared geometry but also confirmed the remaining visible differences are data/state differences, icon/component details, and incomplete packaged-native verification; the shell now follows Pi's visible brand treatment.
- Live browser visual check — moved the conversation topbar to Pi's absolute overlay geometry and reserved its 46px band only for transcript content; Home now keeps the full-height Pi canvas.
- Live browser visual check — aligned the project row with Pi's neutral 5px active-project dot; runtime state remains a quiet cue rather than the former colored status treatment.
- Live browser visual check — aligned the Home project switcher's `Open project` affordance with Pi's folder-plus glyph and verified the opened menu visually before returning it to a closed state.
- Live browser accessibility/visual check — aligned the project-row action order with Pi (`More` before `New task`), restored Pi's active-project cue with its neutral 5px dot, and matched the empty-session copy and sidebar typography to the upstream tokens.
- Live browser visual comparison — checked General Settings against Pi's `dark-settings.webp` at the constrained preview size; rail width, top inset, content padding, section/card rhythm, control placement, and dark surface hierarchy follow the Pi contract. The visible remaining differences are viewport height, platform chrome, and AEGIS-owned data/copy.
- Live browser visual comparison — after the typography/brand pass, verified the local shield mark, 14px base rhythm, Home, Work Panel file tree, Settings, and Extensions at the default preview; measured the desktop breakpoint with a temporary `1600 × 1080` viewport and reset it before handoff.
- Live browser audit (2026-09-19) — compared the current Home, General Settings, and Work Panel states against Pi's `dark-home.webp`, `dark-settings.webp`, and `panel-files.webp` in the same audit run. The shared shell, dark surfaces, 46px chrome, 275px rail, 768px Home composer, Settings rhythm, and file-panel interaction are visually aligned at the checked states. The audit also exposed two real cascade deviations: the Windows/Linux titlebar reserved space with padding instead of Pi's 120px right boundary, and the sidebar header used 14px horizontal insets instead of Pi's 8px header gutter; both were corrected in `desktop/src/pi-port.css`.
- Live browser geometry evidence (2026-09-19) — at `1280 × 800`, measured sidebar `275px`, titlebar `46px`, Home hero `744px`, composer `768px`, Work Panel `360px`, and no document overflow. The wide screenshot bitmap is cropped by the CUA capture surface, so DOM geometry is authoritative for that viewport; native packaged rendering remains `NOT VERIFIED`.
- Live browser interaction evidence (2026-09-19) — opened Settings, returned to Home, opened Work Panel, selected `desktop/src/App.tsx`, verified the active tab changed to `App.tsx` and the selected-file inspector appeared, then closed the panel and left the single preview tab on clean Home.
- Verification (2026-09-19) — `npm run build` passed after the chrome corrections; `git diff --check` passed. `npm run tauri:build` passed and regenerated the Windows EXE, MSI, and NSIS bundles. This proves compilation and packaging, not native visual identity; packaged interaction remains `NOT VERIFIED` without the Windows Computer Use bridge.
- Live browser visual check — verified the project switcher list and Clone view after the backend wiring pass; the Clone input keeps its accessible label visually hidden so it does not distort the Pi menu geometry.
- `npm run build` — passed after the topbar/project-row fidelity pass.
- `uv run pytest tests/test_desktop_protocol.py tests/test_desktop_projection.py` — 21 passed after the fidelity pass.
- `cargo check --manifest-path desktop/src-tauri/Cargo.toml --no-default-features` — passed after the fidelity pass.
- `npm run tauri:build` — passed after the fidelity pass and regenerated the Windows EXE, MSI, and NSIS bundles.
- `npm run tauri:build` — passed again after the switcher glyph correction and regenerated the Windows bundles.
- `npm run build` — passed after the final sidebar typography/action-order pass.
- `uv run pytest tests/test_desktop_protocol.py tests/test_desktop_projection.py` — 21 passed after the final sidebar pass.
- `cargo check --manifest-path desktop/src-tauri/Cargo.toml --no-default-features` — passed after the final sidebar pass.
- `npm run tauri:build` — passed after the final sidebar pass and regenerated the Windows EXE, MSI, and NSIS bundles.
- `npm run tauri:build` — passed after the local AEGIS brand/typography pass; current artifacts are `desktop/src-tauri/target/release/aegis-desktop.exe` (8,714,752 bytes), `desktop/src-tauri/target/release/bundle/msi/AEGIS_0.1.0_x64_en-US.msi` (31,997,952 bytes), and `desktop/src-tauri/target/release/bundle/nsis/AEGIS_0.1.0_x64-setup.exe` (31,134,045 bytes).
- `npm run build` — passed after workspace switching, Clone/Open wiring, accessible Clone-menu polish, and restored Pi topbar title truncation.
- `uv run pytest tests/test_desktop_protocol.py tests/test_desktop_projection.py` — 23 passed after workspace switching and Clone/Open wiring.
- `uv run pytest` — 858 passed, 1 skipped in 983.40s (16:23) after regenerating the tracked AESE registry artifacts.
- `uv run python scripts/aese_inventory.py --check` and `uv run python scripts/aese_claim_graph.py --check` — both passed after registry regeneration.
- `npm run tauri:build` — passed after workspace switching and Clone/Open wiring; produced the Windows EXE, MSI, and NSIS installer.
- `npm run build` — passed after porting Pi's composer containment layers and removing the Settings content width cap.
- Live browser verification at `1600 × 1080` — confirmed Home geometry, work-panel opening, and Settings content expansion from the legacy `900px` cap to the full main-column envelope.
- Live browser verification (2026-09-19 current pass) — the composer permission and model controls now expose Pi-shaped anchored menus. Permission selection updates the shared AEGIS settings state; selecting the model calls the existing `conversations.switch_model` command, closes the menu, and leaves the preview without an error. The model button keeps Pi's visible `Model` label while retaining the configured model in its accessible title/menu.
- Live browser visual comparison (2026-09-19 current pass) — rechecked clean Home against Pi's dark reference after the brand and icon correction. The current shell, rail, titlebar, mascot, composer, Pi logo/label, and controls render without browser errors; remaining visible differences are component-level details, live AEGIS project/session state, and incomplete native interaction parity.
- Live browser geometry (2026-09-19 current pass) — at `1280 × 800`, DOM measurement confirmed sidebar `275px`, titlebar `46px`, Home composer `768px`, Work Panel toggle at `x=1120`, and document size exactly `1280 × 800`; the temporary viewport override was reset before handoff.
- Verification (2026-09-19 current pass) — `npm run build` passed after importing the Pi brand asset pair; `git diff --check` passed. Earlier `uv run pytest tests/test_desktop_protocol.py tests/test_desktop_projection.py` passed with 23 tests and an earlier `npm run tauri:build` produced the EXE, MSI, and NSIS bundles before this renderer-only asset correction. A clean full-suite rerun completed with `853 passed, 1 skipped, 5 failed in 1281.47s`; three failures are in `tests/test_application_subagents.py` (deadline/partial execution) and two are in `tests/test_desktop_protocol.py` live-send cases. A focused replay of those two test modules reproduced the same five failures (`25 passed, 5 failed in 303.59s`), while a direct sequential runtime probe completed the equivalent native/public subagent path successfully; the discrepancy remains unresolved and full verification is not claimed. Native visual interaction remains `NOT VERIFIED` without the Windows Computer Use bridge.
- Visual self-review (2026-09-19 current pass) — direct comparison of the live Home screenshot with Pi's `dark-home.webp` confirms the previous concern was valid: the shell had been close but the icon family and visible brand label were still visibly different. The renderer now uses the same Lucide icon family as Pi for its chrome/composer glyphs and shows the Pi shell label while keeping AEGIS handlers. The implementation is still not a 1:1 visual copy: component composition, live project/session state, some spacing/vertical placement at the constrained viewport, and native packaged interaction remain different or unverified.
- Live browser visual check (2026-09-19 icon pass) — reloaded the single existing preview tab after switching the renderer from the handcrafted SVG map to `lucide-react@1.31.0`; the Pi logo/label, topbar controls, sidebar controls, composer controls, and window controls rendered and the browser error log was empty.
- Live browser visual check (2026-09-19 composer structure pass) — removed the AEGIS-only `composer-pill` wrapper so the Home input now uses Pi's `composer-dock → composer-stack → composer-shell` layers. The first reload exposed a native textarea border; the exact shell textarea reset was restored immediately, and the final reload showed the borderless Pi surface with an empty browser error log.
- Live browser geometry evidence (2026-09-19 composer structure pass) — at the current `762 × 698` preview, the sidebar measured `275px`, titlebar `46px`, composer shell `439.4 × 110.9px` with `20px` radius, toolbar `40px`, and send control `28 × 28px`; computed values match the Pi token contract for the constrained viewport.
- Live browser interaction evidence (2026-09-19 composer structure pass) — opened and closed the permission menu (`Ask every time`, `Accept edits`, `Auto`) and Model menu (`local-model`, `Configure model`) through the accessibility surface, then returned the single preview tab to clean Home.
- Live browser visual comparison (2026-09-19 cross-surface pass) — checked the live General Settings page against `dark-settings.webp` and the live Files Work Panel against `panel-files.webp` in the same preview tab. Rail/card hierarchy, dark surface rhythm, panel width (`360px`), tab control, search field, and file-tree grouping remain aligned; visible differences are the real AEGIS workspace data, constrained viewport, and OS chrome.
- Source/component parity (2026-09-19 composer input pass) — ported Pi's `composer-input-wrap → composer-input-stage → contenteditable composer-input → composer-placeholder` structure while keeping AEGIS's existing `sendMessage` callback. A live keyboard test inserted text, enabled Send, then selected/backspaced it and restored the disabled empty state without browser errors.
- Packaging verification (2026-09-19 composer input pass) — `npm run tauri:build` passed with the contenteditable composer included in the Windows artifacts. Fresh SHA-256: `125596B8B3D456840A3241B090691D1721489AC09217ADE37496B3302400501F` (EXE), `9F8C3EC09B0FBCA91EC5AC31D747E464078D3F89CD15B8D9BDE613D3DF23014D` (MSI), `7178EE4F5CC2047BFEA4589D220CCB4F8CB4AB32F95869A914D71C0F5A108B24` (NSIS). This proves packaging integrity, not native visual parity.
- Live DOM evidence (2026-09-19 composer input pass) — the final Home input is a `DIV[contenteditable=true]`, `14px/20.3px` system font, `white-space: pre-wrap`, transparent background, and `0px` border; it matches Pi's editable input contract rather than the former native textarea.
- Packaging verification (2026-09-19 current pass) — `npm run tauri:build` passed after the Pi logo asset correction and produced the Windows EXE, MSI, and NSIS installers. SHA-256: `31EAB015165EE134050478EAC0AE7C370074D5C7E7326708DADBE93E86C197EF` (EXE), `67FD84DBE92943292255DD56307AA29F3754B754572862D3DB8DFE0F197BE616` (MSI), `1BA1CAD5424F5CEE3E084AC57E920ADBE3C6A33AE8A46E30A17DD0CC7F65A56C` (NSIS). This proves packaging integrity, not native visual parity.
- Packaging verification (2026-09-19 icon/label pass) — `npm run tauri:build` passed after adding `lucide-react@1.31.0` and matching the visible Pi shell label. Fresh SHA-256: `E13D1DA64B85996B8DF00D90AC0401CB6E0EC35813B3FB3E1B47EE32D56814D9` (EXE), `DAE990FB6A049A6965EFECA88DBB9F17FF08E29285E98CC47BDF56EEFC14844E` (MSI), `4FF3E7FCCF142D0C1EF06A7AABDC9C09DCCD957EF6052AFA03CF7C2790F35BFF` (NSIS). This proves packaging integrity, not native visual parity.
- Packaging verification (2026-09-19 final renderer pass) — `npm run tauri:build` passed after removing the AEGIS-only composer wrapper, restoring the Pi textarea reset, and aligning the sidebar component class structure. Fresh SHA-256: `B1CFFFEA88DE2D6E8B620CDEDA1AD1F987C4A26377E41E32EC679F571700D37F` (EXE), `EA7A3CECF1A77C40BF65E1F2AF98599AC956C93F79D578364FC2116C7D57CC5F` (MSI), `57DD008C3FD5B25AA0C3EA392AA89F61C89CDF0C6F3B5889C21194EA77CA8281` (NSIS). This proves packaging integrity, not native visual parity.
- Live browser visual check (2026-09-19 placeholder fidelity pass) — compared the single live preview with Pi's `home-dark.webp` at a temporary `1200 × 800` CSS viewport (the reference capture is a higher-DPI equivalent). The measured contracts remain sidebar `275px`, topbar `46px`, Home stack/composer `768px`, and composer shell `20px` radius; the Home placeholder now reads exactly `Type / for commands · @ for files`. Reloaded AX state is clean and the browser error log is empty. Remaining differences are state/content and native platform chrome, not this geometry contract.
- Live browser interaction evidence (2026-09-19 autocomplete pass) — typed `/` into the contenteditable composer and verified a Pi-shaped `listbox` with `/summarize`, `/inspect`, `/workers`, and `/files`; ArrowDown+Enter inserted the selected AEGIS action text and closed the menu. Typed `@` and verified workspace-backed file suggestions; Enter inserted `@aegis_cognition/agent.py` and closed the menu. Escape/clear restored the empty Home composer and the browser error log remained empty.
- Packaging verification (2026-09-19 autocomplete pass) — `npm run tauri:build` passed with the Pi-shaped `/` and `@` composer completion surface included. Fresh SHA-256: `FBB20FD7E195B5BB6E646A42F9B9F938B68A1E8F5DB4456A38C8EA31556E84FC` (EXE, 8,778,240 bytes), `8E34D2453EA883FCD8ADB1B0ED3BE251A374906B217D05D3796E6E3307EA76E7` (MSI, 32,059,392 bytes), `1D90B650A69F205C4D8E24D9ED2A7143B5DF4F442DC41FA3DE80A9132B88F5E0` (NSIS, 31,197,290 bytes). This proves the current UI is packaged, not native visual parity on every OS.
- Live browser interaction evidence (2026-09-19 model-picker pass) — the Model control now follows Pi's two-level surface: root entries for Model and Reasoning, a back row, model search/list with active selection, Configure model routing, and Detailed/Compact reasoning choices. The existing `conversations.switch_model` backend path remains the model-selection owner; the live test selected Compact and verified the button label/accessible title changed, with no browser errors.
- Packaging verification (2026-09-19 model-picker pass) — `npm run tauri:build` passed with the two-level Model/Reasoning picker included. Fresh SHA-256: `A76C80C46910EE11C28E6D5E80971A17AB86F8DA4301590B9D7D24DA63240696` (EXE, 8,778,752 bytes), `C8C4F2ECCC966E145BBF1BEFD63ACBA4B1443231DF95F9DC31AF57D0D6F2BE66` (MSI, 32,059,392 bytes), `8F44FD8CA6D1E641539A814170BD7784B4F06D780ABD5C1CC712467F17969F06` (NSIS, 31,197,774 bytes). This proves packaging integrity, not native visual parity on every OS.
- Live browser visual/interaction evidence (2026-09-19 Sidebar structure pass) — changed the live Sidebar DOM to Pi's section-toolbar/session-group/project-group/footer ownership while preserving AEGIS handlers. The footer is now a horizontal Pi-style action row with the version chip aligned to the trailing edge; project collapse/expand was exercised and restored, and the browser error log was empty.
- Verification (2026-09-19 Sidebar structure pass) — `npm run build` passed after the Sidebar DOM/CSS port and `git diff --check` passed. Packaged native rendering has not been rerun for this latest renderer-only change and remains `NOT VERIFIED`.
- Packaging verification (2026-09-19 Sidebar structure pass) — `npm run tauri:build` passed after the Sidebar DOM/CSS port. Fresh SHA-256: `64CEE1879D497A976839E9EC2E7BF57DF646F5F69289562848BAABBDA38AD53A` (EXE, 8,779,264 bytes), `1F7AD98F0E8B009863F9587088F7F64FD6B51154D828D894EF841C41F902ABE4` (MSI, 32,059,392 bytes), `C31025A6E4703F4B339C67046B971B89D018322A86E351C0B4F1623054BDD952` (NSIS, 31,197,388 bytes). This proves packaging integrity; native visual identity on Windows/macOS/Linux remains `NOT VERIFIED`.
- Packaging verification (2026-09-19 Sidebar session-row pass) — `npm run tauri:build` passed after adding Pi's `thread-item`/`thread-item-main`/`sidebar-row-actions` semantics to populated session rows. Fresh SHA-256: `2568B7CD543639BCEEDBD959B17ED2106E28275B7DDB9254AE0EFC3FEF88B80A` (EXE, 8,779,264 bytes), `915501AE18A5BFC3DA15F5444FD069457E44378610CE90F9BA78E3AD341E7FAF` (MSI, 32,059,392 bytes), `EA69411E56602BF63CE291626742BAF7E047487804CE8E9D25DB057A8E16BBE8` (NSIS, 31,196,730 bytes). This proves build/package integrity, not complete native 1:1 visual parity.

## Implementation checklist

- [x] Use Pi visual assets without importing Pi's Electron/backend runtime.
- [x] Preserve AEGIS renderer-to-host contracts and existing backend callbacks.
- [x] Match Home and composer geometry.
- [x] Match Settings rail and row geometry.
- [x] Add Pi-shaped Projects archive and Models configuration surfaces.
- [x] Make the Settings search control functional.
- [x] Add Pi-shaped Import scan surfaces with honest empty states.
- [x] Add Pi-shaped AI settings hierarchy and a working Enter-to-send preference.
- [x] Add Pi-shaped Instructions, Shortcuts, Skills, MCP, and Subagents capability surfaces.
- [x] Remove duplicate global shortcut handling and prevent Settings notice leakage between pages.
- [x] Port the Pi-style global search interaction and connect local result selection to AEGIS routes.
- [x] Port Pi destination route shells and shared destination primitives for Pull requests, Scheduled, and Plugins.
- [x] Verify Settings at a constrained viewport and protect its full-page layout from legacy breakpoints.
- [x] Match Pi's capped Home stack at a standard desktop viewport instead of allowing flex growth to stretch it.
- [x] Port Pi's compact Work Panel active-tab/header interaction while preserving AEGIS panel callbacks.
- [x] Keep the sidebar footer visually aligned with Pi while retaining runtime status in functional surfaces.
- [x] Port Pi-style window-control geometry and collapsed-sidebar interaction without replacing AEGIS host contracts.
- [x] Port Pi-style Sessions/Projects section toolbar actions and search keyboard navigation.
- [x] Port Pi-style session action menu and complete session sort choices using existing AEGIS work-panel callbacks.
- [x] Align Pi dark surface tokens, composer copy, and Remote Hosts route coverage.
- [x] Match Extensions/Marketplace page structure.
- [x] Keep copied Pi mascot/brand asset license boundaries documented while keeping AEGIS backend ownership separate.
- [x] Remove the Home-only status row that visibly diverged from Pi's composer silhouette.
- [x] Port the Pi topbar left/right DOM grouping while preserving AEGIS New task/Search handlers.
- [x] Move the Work Panel toggle into Pi's separate control lane and add Pi-shaped open-panel header controls.
- [x] Port the Pi clickable brand/header slot and preserve AEGIS backend ownership while matching the Pi visible shell label.
- [x] Align sidebar collapse controls with Pi's `PanelLeft` glyph and correct the platform-specific brand selector.
- [x] Port Pi's Home project switcher list/Clone/Open surface while preserving AEGIS data ownership.
- [x] Connect project switching and cloning to bounded desktop-service commands with state-store reset and active-subagent protection.
- [x] Add a native cross-platform folder-picker bridge for packaged Open project behavior.
- [x] Port Pi's `composer-dock → composer-stack → composer-shell` containment without replacing AEGIS controls.
- [x] Remove the legacy Settings content width cap so wide windows use Pi's full-column geometry.
- [x] Replace the handcrafted compact brand mark with the original AEGIS inline shield mark at the native 20px sidebar slot; upstream product logos are not rendered.
- [x] Port Pi-shaped composer permission/model menus while preserving AEGIS state and routing model changes through `conversations.switch_model`.
- [x] Replace the renderer's handcrafted chrome/composer SVG map with Pi's `lucide-react@1.31.0` icon family.
- [x] Port Pi's Sidebar section-toolbar, session-group, project-group, and footer DOM structure while retaining AEGIS state/actions.
- [ ] Prove complete 1:1 parity for every Pi-only route and packaged native window.

## Follow-up polish

- The current single preview tab was rechecked after the final renderer changes: Home, Settings, Installed, and Marketplace expose the expected semantic controls and render without browser errors. Existing file-backed comparison artifacts remain historical snapshots and are not treated as proof of the latest pixels.
- The Pi-like marketplace is intentionally a bounded local catalog, not an automatic installer. Real install/update operations remain disabled until a signed package source, host approval, dependency/license checks, rollback path, and audit record are implemented. The UI reports `Needs setup` or an explicit adapter notice rather than claiming connectivity.
- A packaged Tauri visual pass for Windows, macOS, and Linux is still required before claiming full native parity. The current evidence proves Windows packaging/build integrity and live renderer behavior, not native pixel parity on every OS.

final result: open — full 1:1 parity and packaged native verification remain incomplete
