# aegis-skills

Markdown skill registry with token budget checks and task-based selection.

## Runtime boundary

This is a standalone Rust workspace crate; the desktop does not currently call
it. Desktop skill discovery, explicit enable state, and bounded instruction
reads use `aegis_cognition.extensions.SkillCatalog` and the desktop-owned
capability state. The native `core/rust/src/skill_registry.rs` is a separate
admission/runtime subsystem, not this Markdown catalog. Keep these boundaries
distinct unless a concrete, verified requirement justifies a bridge.
