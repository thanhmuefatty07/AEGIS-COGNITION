# CogniFold Memory

## origin
CogniFold is the physical memory layer for AEGIS-COGNITION. It preserves episodic frames, tracks fidelity, and supports compaction without losing the system's long-horizon state.

## proof
A tiered memory model makes retention and crystallization explicit, allowing the runtime to preserve what matters while controlling growth.

## implementation
- `core/rust/src/memory/frame.rs`
- `core/rust/src/memory/fold.rs`
- `core/rust/src/memory/fidelity.rs`

## risks
- memory poisoning
- fidelity decay
- unbounded growth
- poor crystallization policy

## tests
- frame ingest
- fidelity computation
- compaction behavior
- retention policy checks
