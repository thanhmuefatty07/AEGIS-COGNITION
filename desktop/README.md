# AEGIS desktop

The desktop package is a Tauri 2 shell around the same local Python/Rust
authority used by the SDK. The renderer sends only versioned command frames;
it never opens the state database or starts a provider itself.

The renderer is intentionally conversation-first: the sidebar reads the
canonical conversation list, the center pane sends through the existing
conversation commands, and the optional project-details panel exposes indexed
files, the bounded project map, and session activity. Settings and Utilities
are full-page destinations inside the same shell; they do not create a second
state store or pretend that an unimplemented connector is installed. The
compact three-pane layout, floating composer, dense neutral palette, and
session/project navigation take visual cues from reference desktop without importing
its runtime.

The renderer also supports the desktop interaction baseline: `Ctrl/Cmd+B`
toggles the sidebar, `Ctrl/Cmd+K` focuses file search, `Escape` releases search
focus, and `Shift+Enter` inserts a new line in the composer. These controls use
the existing versioned desktop protocol and do not create a second state store.

Local access is project-scoped and host-enforced. `Choose project` uses the
native folder picker; the Rust host remembers the canonical folder only for the
current process and re-checks every workspace path before forwarding it to the
sidecar. Clone destinations must stay inside a user-approved root. The desktop
host does not expose a general delete command, shell passthrough, or wildcard
filesystem permission; removing a project removes only its registry metadata.

The renderer and native bundle use the same source logo at
`src/assets/aegis-icon.svg`. Tauri derives the platform icon set from that
source, so sidebar, compact navigation, window identity, taskbar/Dock assets,
and installers do not carry separate logo variants.

When the Vite renderer is opened outside a Tauri webview, it uses a clearly
labelled in-memory browser preview adapter. The preview exercises the same
request/response decoders, conversation flow, source-map surface, and
subagent event/cancel states without claiming that the native Rust/Python host
is running. Tauri builds always use the real `desktop_request` command.

The `subagents.run` command is the synchronous desktop projection of the
canonical local subagent runtime. For the interactive shell, `subagents.start`
returns a host-owned run id immediately; the renderer then polls
`subagents.events` with a cursor and `subagents.status` until the terminal
result arrives. `subagents.cancel` requests cooperative cancellation through
the same host-owned run state; it never kills a Python thread or discards
conversation state. Handler binding, native graph validation, resource limits,
result packet hashing, and cancellation propagation remain in
`AgentApplication`. The renderer receives a compact worker/result projection,
not executable callbacks or an additional state store. Browser capture is
still host-injected and public research does not use user cookies or login
sessions.

The shell also exposes two read-only observer projections. `conversations.inspect`
shows the active context budget, source revision, bounded timeline, and approval
metadata; `subagents.graph` shows task nodes, dependency edges, status, and
bounded evidence summaries. Approval metadata includes only a bounded,
allowlisted preview (such as a target path); sensitive and unrecognized values,
prompts, and full tool results remain redacted. `approvals.resolve` applies one
explicit approve/decline decision to exactly one pending tool call, tied to the
current conversation revision. The approval continuation is process-local: if
the process ends before the decision completes, AEGIS will not replay the call
and the unresolved result must be inspected. These are UI observability surfaces,
not a second scheduler, mailbox, or permission authority. The browser preview
implements the same shapes with `PREVIEW_ONLY` data and cannot resolve a live
approval.

The desktop host also exposes `code_reuse.assess` and
`code_reuse.materialize`. They only reuse exact bytes from a current local
source snapshot or a checked-out, explicitly licensed source inside the
workspace. The first command is read-only; the second requires an explicit
target and refuses implicit overwrite. Model output or unknown training-data
provenance is never treated as a source repository.

For a development build:

```text
npm install
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

The supported local baseline is CPython 3.14.7 with Rust 1.98.1. The
desktop sidecar uses the same Python/Rust native extension as the root package;
the native source watcher is an invalidation hint and every request still
performs a bounded, hash-bound source snapshot.

The Tauri host starts `aegis-desktop` from `PATH` by default. Set
`AEGIS_DESKTOP_SERVICE` to an executable path when using a local sidecar.

For a release bundle, use the release script. It resolves the pinned Python
environment and desktop-only PyInstaller dependency from `uv.lock`, then
rebuilds the sidecar before Tauri so a stale host binary cannot be copied into
a new UI bundle:

```text
cd desktop
npm run tauri:build
```

The equivalent explicit sidecar step is `npm run sidecar:build` from `desktop/`.

The release configuration requires the generated native authority binary in
`src-tauri/resources/`: PyInstaller emits
`aegis-desktop-service.exe` on Windows and `aegis-desktop-service` on macOS or
Linux. The bundle intentionally fails when that authority binary is missing.
