# ADR-019: Update Wasmtime after new 48.0.3 advisories

## Status

Accepted (2026-10-03); dependency/advisory gates pass, runtime verification
remains pending.

## Context

ADR-018 selected Wasmtime 48.0.3 to address two advisories affecting 47.0.4.
On 2026-10-02, RustSec published RUSTSEC-2026-0325, RUSTSEC-2026-0326, and
RUSTSEC-2026-0327, all affecting 48.0.3. The third advisory describes a
component async-lifted callback result-count bug that can cause a native stack
buffer overflow and is rated Critical by RustSec. The other two describe
conditions that can corrupt the GC heap.

## Decision

Update the exact direct pin, lockfile, version assertions, and migration
evidence to Wasmtime 48.0.4. RustSec lists `>=48.0.4, <49.0.0` and `>=49.0.2`
as patched for these findings. Version 48.0.4 keeps the current 48.x API line
while applying the published patch. Keep Extism and the direct AEGIS runtime on
one resolved Wasmtime version; do not suppress or ignore the new advisories.

This changes a dependency version only. It does not assert that Wasmtime is
secure against unknown defects, that WASM is an OS sandbox, or that runtime
compatibility is proven before the required tests pass.

## Alternatives and trade-offs

- Keeping 48.0.3 is rejected because the current RustSec database reports three
  advisories for the locked version.
- Moving to 49.0.2 is also patched, but is a larger API-line change than needed
  while the upstream-supported 48.x patch is available.
- Removing WASM or weakening fuel/memory controls is unnecessary to address
  these dependency findings and would reduce the intended compute capability.

## Migration, security, performance, operations, rollback

Update `core/rust/Cargo.toml`, `Cargo.lock`, the dependency and architecture
gates, fixtures, traceability, and the active migration guide together. Verify
that the WASM feature resolves one Wasmtime version across AEGIS and Extism;
run Rust WASM runtime tests and both RustSec policy scanners. Roll back only to
an advisory-reviewed version that passes the same gates. Windows local linking
is unavailable because the Windows SDK libraries are missing; runtime testing
on the existing Linux VM and current cross-platform CI remain separate evidence
requirements.

## Evidence

- RustSec 2026-0325 says patched `>=48.0.4, <49.0.0` or `>=49.0.2`:
  https://rustsec.org/advisories/RUSTSEC-2026-0325
- RustSec 2026-0326 says patched `>=48.0.4, <49.0.0` or `>=49.0.2`:
  https://rustsec.org/advisories/RUSTSEC-2026-0326
- RustSec 2026-0327 says patched `>=48.0.4, <49.0.0` or `>=49.0.2`:
  https://rustsec.org/advisories/RUSTSEC-2026-0327
- Wasmtime upstream 48.0.4 release:
  https://github.com/bytecodealliance/wasmtime/releases/tag/v48.0.4
- The pre-update local `cargo deny` scan found these advisories on the exact
  48.0.3 lock entry. After updating, `cargo deny check advisories bans licenses
  sources` (cargo-deny 0.20.2) and `cargo audit --color never` (cargo-audit
  0.22.2, 461 locked dependencies) both exited 0 with no advisory findings.
- `cargo tree --locked -p aegis-nerve --features wasm-plugins -i wasmtime`
  resolves AEGIS, Extism, and WASI components to Wasmtime 48.0.4 only. The
  dependency gate passed 8/8 checks; the matching Python regression test passed
  1/1.
- Rust WASM runtime tests and the Windows/macOS/Linux hosted matrix remain
  `NOT VERIFIED`. The updated source snapshot is on the existing Linux VM, but
  no runtime-test result is claimed until the actual test completes.
