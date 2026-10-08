# ADR-018: Align Wasmtime and Extism on a patched engine

## Status

Superseded on 2026-10-03 by ADR-019 after new Wasmtime advisories affected
48.0.3. This ADR records the prior 47.0.4 → 48.0.3 decision and its evidence.

## Context

The direct Wasmtime dependency was pinned to `47.0.4` and AEGIS enables both
fuel accounting and epoch interruption. The current RustSec audit rejects
`47.0.4` for RUSTSEC-2026-0315, where supported `call_ref`/exception paths can
under-account fuel and evade expected execution budgets. It also reports
RUSTSEC-2026-0316, a host allocation accounting issue for dynamic record
lifting. Upstream Wasmtime lists `48.0.3` and `49.0.1` as patched releases.

## Decision

Pin the direct Wasmtime dependency to exactly `48.0.3`, the earliest patched
release in the `48.x` line. Retain the existing fuel, epoch, memory, WASI, and
host-process controls. Update every active version assertion, dependency audit,
architecture gate, migration note, and traceability record with the pin.

This is a targeted security update, not a claim that WebAssembly or Wasmtime is
free of vulnerabilities, that the process is OS-sandboxed, or that the project
outperforms another agent runtime.

## Alternatives and trade-offs

- Staying on `47.0.4` is rejected because the advisory gate fails and fuel is an
  active sandbox control.
- `49.0.1` is also patched. `48.0.3` is chosen to minimize the Wasmtime API and
  behavior change needed to address the findings; the full affected test suite
  must still pass before treating the migration as verified.
- Removing fuel or silently suppressing the advisory is rejected because it
  weakens the untrusted-execution resource boundary.

Extism `1.30.0` brought a separate `wasmtime 43.0.2` engine into the same
desktop build. A current RustSec audit identified additional advisories in that
older engine, including a high-severity filesystem sandbox escape. Merely
pinning AEGIS's direct Wasmtime would therefore leave a vulnerable second
engine in the dependency graph.

Pin Extism to the exact upstream commit
`d5da29759bba88645f886d9e12d3f4e4376df7b3`, which upgrades its engine/WASI
integration to Wasmtime `48.0.3`. This is an upstream merged change, not a
stable Extism release. The immutable revision keeps builds from silently
moving, but adds a Git source/build-network dependency and has not yet passed
AEGIS's own compile/runtime suite. Replace it with a stable Extism release once
one provides the same patched engine line and passes the same gates.

## Migration, security, performance, operations, rollback

Verified locally: `cargo tree --locked -p aegis-nerve --features wasm-plugins
-i wasmtime` resolves exactly one Wasmtime version (`48.0.3`), and
`cargo audit --file Cargo.lock --no-fetch --color never` exits successfully
with no advisory findings. The focused `cargo deny` result for the main
checkout and AEGIS Rust compile/runtime tests are not verified. Windows local
Rust linking is blocked by a missing linker; the cross-platform GitHub Actions
matrix must provide that remaining evidence. Until then, runtime behavior for
this dependency migration remains `NOT VERIFIED`.

The update does not prove resistance to future engine defects, hostile-kernel
attacks, or compile-time resource exhaustion. Rollback requires an advisory
reviewed replacement and the same gates; reverting to the vulnerable pin is
not an acceptable silent fallback.

## Evidence

The direct Wasmtime advisories and release notes below establish the patched
version. The Extism upstream change and resulting lockfile establish the
engine alignment; the local RustSec scan establishes no currently known
advisories in the resolved lockfile. These static checks do not prove runtime
compatibility or sandbox security; AEGIS runtime tests remain `NOT VERIFIED`.

## Sources

- Wasmtime advisory RUSTSEC-2026-0315:
  https://github.com/bytecodealliance/wasmtime/security/advisories/GHSA-m63x-6p34-q65x
- Wasmtime advisory RUSTSEC-2026-0316:
  https://github.com/bytecodealliance/wasmtime/security/advisories/GHSA-jqpg-j7w6-42pr
- Wasmtime 48.0.3 release:
  https://github.com/bytecodealliance/wasmtime/releases/tag/v48.0.3
- Wasmtime 49.0.1 release:
  https://github.com/bytecodealliance/wasmtime/releases/tag/v49.0.1
- Extism upstream Wasmtime 48 migration (merged PR):
  https://github.com/extism/extism/pull/912
- Exact Extism source revision:
  https://github.com/extism/extism/commit/d5da29759bba88645f886d9e12d3f4e4376df7b3
