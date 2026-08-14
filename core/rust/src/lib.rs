// The core exposes a compatibility-heavy proof/hash API and intentionally
// keeps several nested state-validation branches readable. Keep the pinned
// Clippy policy strict for new lint classes while documenting this bounded
// legacy allow-list explicitly.
#![allow(
    clippy::collapsible_if,
    clippy::len_without_is_empty,
    clippy::manual_range_contains,
    clippy::needless_question_mark,
    clippy::new_without_default,
    clippy::redundant_closure,
    clippy::redundant_pattern_matching,
    clippy::unnecessary_map_or,
    clippy::unnecessary_sort_by,
    clippy::too_many_arguments
)]

pub mod bridge_mmap;
pub mod browser_witness;
pub mod circuit_breaker;
pub mod cli;
pub mod context;
pub mod descriptor;
pub mod distributed;
pub mod eac;
pub mod evidence_index;
pub mod execution;
pub mod ffi;
pub mod goal_intake;
pub mod governance;
pub mod guardrail;
pub mod harness;
pub mod hot_engine;
pub mod integrations;
pub mod ipc;
pub mod layout;
pub mod learning;
pub mod licensing;
pub mod llm;
pub mod memory;
pub mod message;
pub mod mvcc;
pub mod orchestrator;
pub mod physical;
pub mod policy;
pub mod replay;
pub mod resource;
pub mod resource_platform;
pub mod runtime;
pub mod sac;
pub mod sandbox;
pub mod schema;
pub mod shm;
pub mod skill_registry;
pub mod speculative;
pub mod task_ledger;
pub mod telemetry;
pub mod tool_gateway;

#[cfg(test)]
mod tests;

pub use cli::run as run_cli;

pub fn init_tracing() {
    let _ = tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .try_init();
}
