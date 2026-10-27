# Wasmtime 48.0.4 security update and migration evidence

> **Advisory update — 2026-10-14:** The 2026-10-02 decision below is now a
> historical baseline. Bytecode Alliance advisory
> [GHSA-p79w-pv2r-29qp](https://github.com/bytecodealliance/wasmtime/security/advisories/GHSA-p79w-pv2r-29qp)
> and [RUSTSEC-2026-0364](https://rustsec.org/advisories/RUSTSEC-2026-0364.html)
> report undefined behavior in affected Wasmtime releases when exception tags
> misalign host runtime structures. Wasmtime `48.0.4` is affected; the advisory
> lists `48.0.6` and `49.0.3` as patched releases. This repository still pins
> `48.0.4`, and `scripts/wasmtime_migration_gate.py` plus
> `scripts/architecture_fitness.py` still require that version. Existing passes
> of those gates and the dependency audit below do not establish the current
> advisory state. No exploit or sandbox escape is established here; the upstream
> advisory reports no known exploitation method. Update the dependency and its
> version gates together when remediation is authorized.
>
> The upstream also reports [GHSA-wjc8-633q-pwxr / RUSTSEC-2026-0359](https://github.com/bytecodealliance/wasmtime/security/advisories/GHSA-wjc8-633q-pwxr): guest-controlled recursive type chains can overflow the host stack. Wasmtime `48.0.4` is affected; `48.0.6` and `49.0.3` are patched. This is a separate availability advisory and further confirms that the current pin is not a patched release.

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

At the original 2026-10-02 review, the lockfile passed local `cargo deny` and
`cargo audit`, and the dependency gate and exact Wasmtime/Extism tree were
verified at `48.0.4`. That result is historical: the 2026-10-14 advisory above
means it no longer establishes the current known-advisory state.

Local Windows Rust linking lacks Windows SDK libraries. The updated source
snapshot is present on the existing Linux test VM, but runtime-test execution
and result are still `NOT VERIFIED`; this working tree has not passed the
Windows/macOS/Linux CI matrix. Fuzz campaigns, adversarial module
corpus results, cross-platform replay parity, and privileged OS isolation tests
remain `NOT VERIFIED`. Rollback is a pinned dependency change reviewed against
the same gates; silently reverting to affected 48.0.3 is not an accepted fix.
