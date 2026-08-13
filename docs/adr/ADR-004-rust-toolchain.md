# ADR-004: Reproducible Rust toolchain

Status: Accepted

Release and CI use a repository-pinned Rust toolchain. The project targets Rust 2024
edition and keeps an explicit MSRV policy separate from the recommended release
compiler. Dependency security fixes may raise MSRV when preserving an old floor would
require stale dependencies.
