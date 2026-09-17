# AEGIS desktop

The desktop package is a Tauri 2 shell around the same local Python/Rust
authority used by the SDK. The renderer sends only versioned command frames;
it never opens the state database or starts a provider itself.

The renderer is intentionally conversation-first: the sidebar reads the
canonical conversation list, the center pane sends through the existing
conversation commands, and the docked right-hand work panel exposes indexed
files, the bounded project map, and session activity. Settings and Extensions
are full-page destinations inside the same shell; they do not create a second
state store or pretend that an unimplemented connector is installed. The
compact three-pane layout, floating composer, dense neutral palette, and
session/project navigation take visual cues from PI Desktop without importing
its runtime.

The renderer also supports the desktop interaction baseline: `Ctrl/Cmd+B`
toggles the sidebar, `Ctrl/Cmd+K` focuses file search, `Escape` releases search
focus, and `Shift+Enter` inserts a new line in the composer. These controls are
presentation-only and continue to use the existing versioned desktop protocol.

The `subagents.run` command is the desktop projection of the canonical local
subagent runtime. It accepts bounded task/plan metadata only; handler binding,
native graph validation, resource limits, and result packet hashing remain in
`AgentApplication`. The renderer receives a compact worker/result projection,
not executable callbacks or an additional state store. Browser capture is still
host-injected and public research does not use user cookies or login sessions.

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

For a Windows release bundle, build the sidecar with the project Python
environment first, then build Tauri:

```text
uv pip install --python .venv\\Scripts\\python.exe -r desktop/packaging/requirements.txt
.venv\\Scripts\\python.exe desktop/packaging/build_sidecar.py
npm run tauri:build
```

The release configuration requires the generated native authority binary in
`src-tauri/resources/`: PyInstaller emits
`aegis-desktop-service.exe` on Windows and `aegis-desktop-service` on macOS or
Linux. The bundle intentionally fails when that authority binary is missing.
