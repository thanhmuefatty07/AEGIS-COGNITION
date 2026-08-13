//! EaC Sandbox — tool batching and code-first orchestration.

pub mod batch;
pub mod runtime;

pub use batch::{BatchExecutor, ToolCall, ToolResult, TransactionPolicy};
pub use runtime::{
    Command, FilesystemSerde, ProgramTrace, SandboxRuntime, SkillBlueprint, StepProof, TurnState,
};
