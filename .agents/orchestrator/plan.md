# Plan - AEGIS-COGNITION

## Mission
Build AEGIS-COGNITION into a production-grade, long-horizon AI agent harness that is deterministic, zero-trust, evidence-backed, and replayable, optimizing SFoT and MTC metrics.

## Execution Pattern: Project Pattern
Dual Track Execution:
1. **Implementation Track**: Realize requirements R1 through R5.
2. **E2E Testing Track**: Build comprehensive opaque-box test suites to verify system properties and requirements.

## Milestones & Status
| Milestone | Description | Target Requirements | Status | Assigned Agent |
|---|---|---|---|---|
| M0 | Codebase Exploration & Analysis | R1, R2, R3, R4, R5 | In Progress | Explorer 1-5 |
| M1 | Durable Shift Manager & 100h Loop | R1 | Planned | TBD |
| M2 | QuickJS/Javy Wasm Sandbox Bridge | R2 | Planned | TBD |
| M3 | mmap-Backed Arrow IPC Audit Stream | R3 | Planned | TBD |
| M4 | Security-Gated Tool Gateway & Witness | R4 | Planned | TBD |
| M5 | Hot-Path Optimizations & Benchmarks | R5 | Planned | TBD |
| M6 | Final Verification & Audit | All | Planned | TBD |

## Detailed Plan for Milestone 0: Exploration (Current Phase)
1. Spawn 5 explorers in parallel to map out R1, R2, R3, R4, R5 code locations, current state, and gaps.
2. Collect handoff reports from Explorer 1-5.
3. Synthesize the findings into a comprehensive `PROJECT.md` at the project root.
4. Refine the implementation strategy and update milestone definitions.

## Key Controls
- Heartbeat cron running every 10 minutes to verify liveness.
- Succession threshold: 16 spawns (currently 5 spawns).
- Hard veto on forensic audit failures.
