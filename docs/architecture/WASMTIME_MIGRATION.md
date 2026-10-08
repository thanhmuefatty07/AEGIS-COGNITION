# Wasmtime 48.0.4 security update and migration evidence

## Context and decision

ADR-018 previously selected Wasmtime `48.0.3` to address RUSTSEC-2026-0315 and
RUSTSEC-2026-0316, which affected the prior `47.0.4` pin. Three new advisories
issued 2026-10-02 also affect `48.0.3`: RUSTSEC-2026-0325 and -0326 describe GC
heap-corruption risks; RUSTSEC-2026-0327 describes a native stack-buffer
overflow. RustSec lists `>=48.0.4, <49.0.0` or `>=49.0.2` as patched. ADR-019
therefore updates AEGIS to exact version `48.0.4`, the patch release in the
existing 48.x line. This dependency update is not a claim that a version alone
provides sandbox isolation or eliminates unknown vulnerabilities.

The update keeps AEGIS's fuel, epoch, memory, WASI, replay, and host-process
controls subject to the same tests and gates.

## Controls verified in source

- Fuel consumption is enabled for bounded instruction budgets.
- Epoch interruption is configured for wall-clock interruption.
- Linear-memory limits are checked before execution.
- WASI capabilities are allowlisted by the sandbox policy; the host process is
  still the security boundary for OS-level isolation.
- Replay/evidence hashes include execution inputs and outputs where the caller
  requests deterministic replay.

## Required migration gate

Run from the repository root:

```powershell
cargo metadata --locked --no-deps
cargo test --manifest-path core/rust/Cargo.toml --lib --no-default-features sandbox
cargo test --locked --release -p aegis-nerve --features wasm-plugins --lib wasm_plugins::tests
cargo tree --locked -p aegis-nerve --features wasm-plugins -i wasmtime
cargo deny check advisories bans licenses sources
cargo audit
python scripts/wasmtime_migration_gate.py
```

The dependency tree must show one Wasmtime version for the AEGIS and Extism WASM
path. The static gate checks the pinned version and required fuel, epoch,
memory, and WASI controls; Rust tests check runtime behavior. `cargo deny` and
`cargo audit` check known dependency advisories and policy; neither proves
resistance to future engine defects or a malicious host kernel. The current
`48.0.4` runtime test is not marked verified until it passes on supported CI
platforms.

## Open evidence

The updated lockfile passes local `cargo deny` and `cargo audit`; the dependency
gate and exact Wasmtime/Extism tree are verified at `48.0.4`. These checks
establish the current known-advisory state, not runtime compatibility.

Local Windows Rust linking lacks Windows SDK libraries. The updated source
snapshot is present on the existing Linux test VM, but runtime-test execution
and result are still `NOT VERIFIED`; this working tree has not passed the
Windows/macOS/Linux CI matrix. Fuzz campaigns, adversarial module
corpus results, cross-platform replay parity, and privileged OS isolation tests
remain `NOT VERIFIED`. Rollback is a pinned dependency change reviewed against
the same gates; silently reverting to affected 48.0.3 is not an accepted fix.
