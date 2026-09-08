# ADR-004: Reproducible Rust toolchain

Status: Accepted (2026-08-14); amended (2026-09-04)

## Context and problem

The crate uses platform FFI, PyO3, Wasmtime, and large test targets. Toolchain
drift can alter ABI, diagnostics, and generated artifacts.

## Constraints and options

The workspace must build on CI and Tier-1 platforms. Options were floating
stable, a minimum-version policy, or a pinned toolchain plus Rust 2024.

## Decision and rationale

Pin Rust `1.98.1` in `rust-toolchain.toml` and set the workspace MSRV to Rust
`1.98`. All workspace packages remain on edition 2024. CI uses the same pinned
stable compiler for the MSRV and release lanes so local and hosted builds have
one current baseline.

## Trade-offs and consequences

Updates are deliberate rather than automatic. Rust 1.98.1 is the current stable
patch baseline at this amendment and includes the upstream LLVM miscompilation
fix. Edition 2024 exposes unsafe FFI declarations explicitly, increasing review
cost while reducing ambiguity.

## Rejected alternatives

Floating stable was rejected because reproducible native wheels and audit runs
need one compiler baseline.

## Migration, security, performance, operations, rollback

Run workspace check, fmt, clippy, tests, and target checks when updating. Review
new unsafe blocks and linker behavior. Performance baselines must include the
toolchain. Rollback is a pinned toolchain change with a clean lock-compatible
build. Raising the MSRV from 1.97 to 1.98 is an intentional compatibility
change: downstream users on 1.97 must upgrade before building this snapshot.

## Evidence

`rust-toolchain.toml`, all Cargo manifests, and the workspace library check.
