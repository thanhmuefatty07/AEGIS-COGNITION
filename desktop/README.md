# AEGIS desktop

The desktop package is a Tauri 2 shell around the same local Python/Rust
authority used by the SDK. The renderer sends only versioned command frames;
it never opens the state database or starts a provider itself.

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

The release configuration requires the generated
`src-tauri/resources/aegis-desktop-service.exe`; the bundle intentionally
fails when that authority binary is missing.
