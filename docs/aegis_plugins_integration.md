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

## Example Flow

1. Build a `Pipeline` in `aegis-search-sdk`.
2. Persist candidates through `SandboxStateStore`.
3. Execute restricted code through `SandboxRuntime`.
4. Search local artifacts through `EvidenceSearch`.
5. Bind verified evidence with `EvidenceStore`.
