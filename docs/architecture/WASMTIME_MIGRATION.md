# Wasmtime 50.0.3 security update and migration evidence

## Context and decision (2026-11 remediation)

Prior revisions (ADR-018 / ADR-019) pinned Wasmtime `48.0.4` alongside a git-pinned `extism` wrapper. Subsequent Bytecode Alliance advisories reported issues affecting `48.0.4` and `50.0.1` (`RUSTSEC-2026-0566` through `RUSTSEC-2026-0573`, patched in `>=50.0.2`), including:
- [GHSA-p79w-pv2r-29qp / RUSTSEC-2026-0364](https://rustsec.org/advisories/RUSTSEC-2026-0364.html): host runtime structure misalignment when exception tags are handled in affected releases.
- [GHSA-wjc8-633q-pwxr / RUSTSEC-2026-0359](https://github.com/bytecodealliance/wasmtime/security/advisories/GHSA-wjc8-633q-pwxr): guest-controlled recursive type chains overflowing the host stack.

AEGIS now pins exact release `wasmtime = "=50.0.3"` from `crates.io` (`core/rust/Cargo.toml`), removes the git-pinned `extism` dependency (`deny.toml` `allow-git = []`), and hosts the `extism:host/env` guest ABI directly inside `core/rust/src/wasm_plugins.rs` using `wasmtime::PoolingAllocationConfig`, `StoreLimits`, fuel metering, and epoch interruption with zero WASI imports. `scripts/wasmtime_migration_gate.py`, `scripts/architecture_fitness.py`, `scripts/governance_gate.py`, and `scripts/dependency_audit_gate.py` are updated in lockstep. This dependency update does not claim that an engine version alone provides OS-level isolation or eliminates unknown future defects.

The update keeps AEGIS's fuel, epoch, memory, WASI, replay, and host-process controls subject to the same tests and gates.

## Controls verified in source

- Fuel consumption is enabled (`consume_fuel(true)`) for bounded instruction budgets.
- Epoch interruption is configured (`epoch_interruption(true)`) for wall-clock interruption.
- Linear-memory and pooling allocator limits (`PoolingAllocationConfig` + `ResourceLimiter`) are checked before and during execution.
- Guest WASM plugins run with zero WASI imports; sandboxed host controls remain allowlisted by policy, with the host OS process remaining the outer isolation boundary.
- Replay/evidence hashes include execution inputs and outputs where deterministic replay is requested.

## Required migration gate

Run from the repository root:

```powershell
cargo metadata --locked --no-deps
cargo test --manifest-path core/rust/Cargo.toml --lib --no-default-features sandbox
cargo test --locked --release -p aegis-nerve --features wasm-plugins --lib wasm_plugins::tests
cargo tree --locked -p aegis-nerve --features wasm-plugins -i wasmtime
cargo deny check advisories bans licenses sources
python scripts/wasmtime_migration_gate.py
```

The dependency tree must resolve a single `wasmtime v50.0.3` version across `aegis-nerve` and `wasm-plugins` with zero git dependencies. `cargo deny` checks known advisories, license policy, and registry-only sources; neither proves resistance to future engine defects or a compromised host kernel.

## Verification scope

- `VERIFIED` when the commands above and `wasm_plugins::tests` pass on local Windows (`rustc 1.99.1`) and remote macOS (`aarch64-apple-darwin`, `rustc 1.99.1`).
- Multi-OS Linux/Windows/macOS GitHub Actions release-matrix runs, external Sigstore release attestations, and multi-hour fuzz campaigns remain `NOT VERIFIED` until executed in authorized release CI. Rollback requires a reviewed lockstep pin update across `Cargo.toml`, `Cargo.lock`, and the static gates; reverting to `48.0.4` or `48.0.3` is not permitted.
