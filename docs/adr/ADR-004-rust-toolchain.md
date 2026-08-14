# ADR-004: Reproducible Rust toolchain

Status: Accepted (2026-08-14)

## Context and problem

The crate uses platform FFI, PyO3, Wasmtime, and large test targets. Toolchain
drift can alter ABI, diagnostics, and generated artifacts.

## Constraints and options

The workspace must build on CI and Tier-1 platforms. Options were floating
stable, a minimum-version policy, or a pinned toolchain plus Rust 2024.

## Decision and rationale

Pin Rust `1.97.1` in `rust-toolchain.toml` and migrate all workspace packages to
edition 2024. CI uses the pinned toolchain and locked dependencies.

## Trade-offs and consequences

Updates are deliberate rather than automatic. Edition 2024 exposes unsafe FFI
declarations explicitly, increasing review cost while reducing ambiguity.

## Rejected alternatives

Floating stable was rejected because reproducible native wheels and audit runs
need one compiler baseline.

## Migration, security, performance, operations, rollback

Run workspace check, fmt, clippy, tests, and target checks when updating. Review
new unsafe blocks and linker behavior. Performance baselines must include the
toolchain. Rollback is a pinned toolchain change with a clean lock-compatible
build.

## Evidence

`rust-toolchain.toml`, all Cargo manifests, and the workspace library check.
