# Wasmtime 47.0.3 migration and security evidence

## Context and decision

The runtime is pinned to Wasmtime `47.0.3`. This is a security-sensitive
migration from the former 22.x line, not a claim that a dependency version by
itself provides sandbox isolation. The decision is to keep the newer release,
make resource controls explicit, and require evidence at the engine, policy,
and host-process layers.

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
cargo audit
python scripts/wasmtime_migration_gate.py
```

The static gate checks the pinned version and the required fuel, epoch, memory,
WASI, and replay symbols. The Rust tests check behavior for insufficient fuel,
memory bounds, and policy rejection. `cargo audit` is the advisory evidence;
it does not prove that a malicious host kernel or a future Wasmtime issue is
impossible.

## Open evidence

Fuzz campaigns, adversarial module corpus results, cross-platform replay
parity, and privileged OS isolation tests remain `NOT VERIFIED` until their
raw artifacts are retained. Rollback is a pinned dependency change reviewed
against the same gate; silently downgrading to 22.x is not an accepted fix.
