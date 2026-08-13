pub mod fidelity;
pub mod fold;
pub mod frame;
pub mod nudge;
pub mod pool;
pub mod session_search;
pub mod user_model;

pub use fold::{
    CogniFoldEngine, CogniFoldStore, CognitiveFolding, FoldingConfig, MemoryCrystallization,
    RuntimeLayoutBudget, SemanticPointerResolver, RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT,
};
pub use frame::{
    ContextPrefix, MemoryEdge, MemoryFrame, MemoryGraph, SemanticNode, SemanticPointer,
};
pub use pool::{PreAllocatedBuffer, SlabMemoryPool};
