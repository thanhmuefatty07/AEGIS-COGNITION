# AEGIS Plugin Suite Integration

## Scope

The plugin suite lives under `aegis-plugins/` as independent Rust crates in the
root Cargo workspace. The first implementation slice provides deterministic,
testable local cores:

- `aegis-search-sdk`: programmable search pipelines and HotEvidenceIndex adapter.
- `aegis-sandbox`: policy validation and filesystem JSON state store.
- `aegis-browser`: browser command abstractions and in-memory driver.
- `aegis-skills`: built-in markdown skills and task selection.
- `aegis-evidence`: evidence binding and tamper-evident audit chain.
- `aegis-bench`: plugin performance gate model.

## Runtime connection

Being a Cargo workspace member does not make a crate an agent capability. The
crates above are currently standalone Rust libraries; the desktop and Python
product runtime do not automatically discover, load, or expose them as tools.
In particular, crate tests prove those libraries in isolation, not their use by
an agent.

The product's current extension path is owned by
`aegis_cognition.extensions.ExtensionRegistry` and its host wiring:

- host tools and enabled Markdown skills are described and dispatched through
  the registry and existing Lab execution path;
- approved MCP connections are host-managed adapters;
- executable extension packages are limited to explicitly approved,
  compute-only WASM modules.

The standalone crates should be connected only through a concrete adapter that
preserves those owners' validation, authorization, resource limits, and tests.
Do not duplicate an existing runtime capability or describe a workspace-only
crate as integrated until an end-to-end test proves the product can call it.

## Security Notes

The sandbox crate currently enforces a portable policy validator and state root.
It does not claim Linux namespace, seccomp, cgroup, or Docker isolation until a
host backend physically proves those controls. Production callers should treat
`PolicyOnlyBackend` as a validation adapter, not an isolation boundary.

The browser crate exposes stealth configuration and deterministic command
packets. It does not claim Cloudflare/DataDome bypass without site-specific
evidence from a CDP backend.

## Verification

Run:

```powershell
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo bench -p aegis-search-sdk --bench search_bench
```

## Standalone crate example (not product-integrated)

1. Build a `Pipeline` in `aegis-search-sdk`.
2. Persist candidates through `SandboxStateStore`.
3. Validate source policy through `SandboxRuntime` with `PolicyOnlyBackend`;
   this does not execute code or provide OS isolation.
4. Search local artifacts through `EvidenceSearch`.
5. Bind verified evidence with `EvidenceStore`.
