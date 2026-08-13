---
name: aegis-optimal
description: Optimize AEGIS-COGNITION AI agent framework. Use when editing core/, brain/, cluster/, AEGIS-COGNITION/, Rust sources, FFI boundary, speculative decoding, SAC, nerve, or any of the 23 aegis-* agents. Enforce Ponytail YAGNI inside AEGIS planning rules: minimize speculative abstractions, keep the cognitive ladder intact, prefer native platform (Rust) over Python reimplementation.
license: MIT
---

# aegis-cognition optimal mode

You are working in `AEGIS-COGNITION` — a hybrid Rust+Python AI agent framework for long-horizon cognitive planning. Stack: Rust core (cargolock ~100kb), Python orchestrator, 23+ Cursor agents (aegis-checkpoint-warden, aegis-decision-oracle, aegis-nerve-architect, etc.), speculative decoding, zero-trust plugin system.

## Hard rules (non-negotiable)

- **Cognitive ladder integrity**: Plan → Decision → Execution → Checkpoint → Crystallize. Don't reorder or merge steps; the timing is the contract.
- **Rust ↔ Python FFI boundary** (`aegis-rust-ffi-auditor`): all inter-language calls go through the schema in `.cursor/rules/aegis-zero-trust-plugin-creator.mdc`. Don't add a new FFI call without updating the schema first.
- **Checkpoint discipline**: every state mutation lands a checkpoint. `aegis-checkpoint-supervisor` enforces this. Don't skip checkpoints to "ship faster" — that's a regression to pre-Sprint-Hardening state.
- **Speculative decoding budget**: the `aegis-speculative-orchestrator` decides which paths to explore speculatively. Don't spawn parallel speculative branches outside its gate.
- **23 agents ≠ 23 processes**: they share a single AEGIS runtime. New agent type must register with the orchestrator.

## Stack-aware YAGNI

- **Native (Rust) over Python**: when a hot path needs to be fast, push it to Rust via FFI; don't optimize the Python side. Ponytail's "native over deps" maps to "Rust over Python loop" here.
- **Existing plugins first**: `aegis-plugins/` ships dozens. Don't write a new plugin before checking if one already covers the case.
- **`.agents/rules/` over inline guidance**: ship a rule, don't hardcode behavior into the agent.
- **No new Cargo dep** if `std`, `tokio`, `serde`, or `anyhow` already cover it. The lockfile is curated.
- **No new Python dep** if `asyncio`, `typing`, `dataclasses`, `pathlib`, `subprocess` cover it.

## Pattern kills

- Wrapper trait with one impl. Inline.
- Custom error type that just wraps `anyhow::Error`. Use `anyhow` directly.
- Python abstract base class with one concrete impl. Inline or use `Protocol`.
- Per-agent config file when a default works. Delete.
- "Future-proof" enum variant. Delete until needed.

## When editing

1. Read `PROJECT_OVERVIEW_DETAILED.md` (~106k bytes — only the section you need) and `EXTREME_AUDIT_REPORT.md` for known anti-patterns.
2. The 7 rules in `.cursor/rules/` (project.mdc, style.mdc, planning-architecture.mdc, etc.) are authoritative. They take precedence over this skill.
3. Run `cargo build --release` + `pytest tests/` before claiming done.
4. If you touch FFI: run the rust-ffi-auditor agent afterwards.

## Output style

Code first, then 1-2 lines: what was skipped, when to add it. No essays. The 23 agents are already verbose; this skill is terse on purpose.