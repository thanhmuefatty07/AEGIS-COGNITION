# Scope: Implementation Spec Generation (Milestone 3)

## Context
- The `c:\Users\ADMIN\AEGIS-COGNITION\AEGIS_DX_RESEARCH_REPORT.md` has been created by the previous subagent and outlines the DX strategy (hidden input, auto-detection regexes, setup wizard flow, etc.).
- The target files for implementation are:
  - Python Bridge: `c:\Users\ADMIN\AEGIS-COGNITION\core\python\aegis_adapter.py`
  - Rust Core: `c:\Users\ADMIN\AEGIS-COGNITION\core\rust\src\llm.rs`
- Constraint: DO NOT modify the core architecture or `hot_engine.rs`. DO NOT actually modify any source code. Just write the spec.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 3 | Implementation Spec Generation | Develop `c:\Users\ADMIN\AEGIS-COGNITION\IMPLEMENTATION_SPEC.md` mapping to `aegis_adapter.py` and `llm.rs` | M2 (Done) | IN_PROGRESS |

## Specific Tasks
1. Read `c:\Users\ADMIN\AEGIS-COGNITION\AEGIS_DX_RESEARCH_REPORT.md` to understand the exact mechanisms required.
2. Read `c:\Users\ADMIN\AEGIS-COGNITION\core\python\aegis_adapter.py` and `c:\Users\ADMIN\AEGIS-COGNITION\core\rust\src\llm.rs` to determine the exact integration points where the new DX setup wizard and configuration overrides will be injected.
3. Write `c:\Users\ADMIN\AEGIS-COGNITION\IMPLEMENTATION_SPEC.md`. Detail the exact files to modify (or create), provide pseudocode for the setup wizard (including the cross-platform hidden input and regex auto-detection), and explain how the generated `config.yaml`/`.env` values hook into `aegis_adapter.py` and `llm.rs`'s existing structures (like the Token Bucket or ProviderRateLimitError).

## Output Requirements
- Provide `IMPLEMENTATION_SPEC.md` at project root.
- Reply back with a summary of the implementation details using the `send_message` tool. Include your handoff report.
