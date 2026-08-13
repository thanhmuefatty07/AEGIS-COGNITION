//! EaC Sandbox Batch Executor — production-grade tool call batching.
//!
//! Graduated from `pocs/tool_batching_poc`. Provides synchronous thread-based
//! concurrency (no tokio dependency) for batching independent tool calls with
//! transactional recovery policies (`HaltOnFailure`, `ContinueOnFailure`).
//!
//! Integrates with [`EventBus`] to emit `BatchExecuted` events for replay
//! determinism.

use crate::eac::events::{self, EventBus};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::Instant;

/// A single tool invocation request within a batch.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolCall {
    /// Unique identifier for this call within the batch.
    pub call_id: u32,
    /// Name of the tool to invoke.
    pub tool_name: String,
    /// JSON arguments for the tool.
    pub arguments: serde_json::Value,
}

/// Result of executing a single tool call.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolResult {
    /// The call_id from the originating [`ToolCall`].
    pub call_id: u32,
    /// Whether the execution succeeded.
    pub success: bool,
    /// Output text on success.
    pub output: String,
    /// Error message on failure.
    pub error: Option<String>,
}

/// Transaction policy governing batch failure behavior.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub enum TransactionPolicy {
    /// Stop executing remaining calls on first failure.
    HaltOnFailure,
    /// Execute all calls regardless of individual failures.
    ContinueOnFailure,
}

impl std::fmt::Display for TransactionPolicy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TransactionPolicy::HaltOnFailure => write!(f, "HaltOnFailure"),
            TransactionPolicy::ContinueOnFailure => write!(f, "ContinueOnFailure"),
        }
    }
}

/// Batch executor that runs tool calls with transactional semantics.
///
/// Uses `std::thread` for concurrency instead of async to avoid pulling
/// in a tokio runtime dependency for the core crate.
pub struct BatchExecutor;

impl BatchExecutor {
    /// Execute a batch of tool calls using the provided executor function.
    ///
    /// - `ContinueOnFailure`: runs all calls in parallel using thread pool.
    /// - `HaltOnFailure`: runs calls sequentially, stopping on first failure.
    ///
    /// Optionally emits a `BatchExecuted` event to the provided `EventBus`.
    pub fn execute_batch<F>(
        calls: Vec<ToolCall>,
        policy: TransactionPolicy,
        executor: F,
        event_bus: Option<&EventBus>,
    ) -> Vec<ToolResult>
    where
        F: Fn(ToolCall) -> ToolResult + Send + Sync + 'static,
    {
        let start = Instant::now();
        let executor = Arc::new(executor);
        let batch_size = calls.len();

        let results = match policy {
            TransactionPolicy::ContinueOnFailure => Self::execute_parallel(calls, executor),
            TransactionPolicy::HaltOnFailure => Self::execute_sequential(calls, executor),
        };

        let elapsed_us = start.elapsed().as_micros() as u64;
        let succeeded = results.iter().filter(|r| r.success).count();
        let failed = results.iter().filter(|r| !r.success).count();

        if let Some(bus) = event_bus {
            events::emit_batch_executed(
                bus,
                batch_size,
                succeeded,
                failed,
                &policy.to_string(),
                elapsed_us,
            );
        }

        results
    }

    /// Run all calls concurrently using OS threads.
    fn execute_parallel<F>(calls: Vec<ToolCall>, executor: Arc<F>) -> Vec<ToolResult>
    where
        F: Fn(ToolCall) -> ToolResult + Send + Sync + 'static,
    {
        let handles: Vec<_> = calls
            .into_iter()
            .map(|call| {
                let exec = Arc::clone(&executor);
                std::thread::spawn(move || exec(call))
            })
            .collect();

        handles.into_iter().filter_map(|h| h.join().ok()).collect()
    }

    /// Run calls sequentially, halting on first failure.
    fn execute_sequential<F>(calls: Vec<ToolCall>, executor: Arc<F>) -> Vec<ToolResult>
    where
        F: Fn(ToolCall) -> ToolResult + Send + Sync + 'static,
    {
        let mut results = Vec::with_capacity(calls.len());
        for call in calls {
            let call_id = call.call_id;
            let result = executor(call);
            let success = result.success;
            results.push(result);
            if !success {
                tracing::warn!(
                    call_id = call_id,
                    "Batch halted: tool call {} failed under HaltOnFailure policy",
                    call_id
                );
                break;
            }
        }
        results
    }

    /// Aggregate batch results into a single summary string for inclusion
    /// in an LLM turn message.
    pub fn aggregate_results(results: &[ToolResult]) -> String {
        let mut summary = String::new();
        for result in results {
            if result.success {
                summary.push_str(&format!(
                    "[call_id={}] OK: {}\n",
                    result.call_id, result.output
                ));
            } else {
                summary.push_str(&format!(
                    "[call_id={}] FAIL: {}\n",
                    result.call_id,
                    result.error.as_deref().unwrap_or("unknown error")
                ));
            }
        }
        summary
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mock_executor(call: ToolCall) -> ToolResult {
        match call.tool_name.as_str() {
            "click" => {
                let index = call
                    .arguments
                    .get("index")
                    .and_then(|i| i.as_i64())
                    .unwrap_or(-1);
                if index == 0 {
                    ToolResult {
                        call_id: call.call_id,
                        success: false,
                        output: String::new(),
                        error: Some("Cannot click element with index 0".to_string()),
                    }
                } else {
                    ToolResult {
                        call_id: call.call_id,
                        success: true,
                        output: format!("Clicked element {}", index),
                        error: None,
                    }
                }
            }
            "input" => {
                let text = call
                    .arguments
                    .get("text")
                    .and_then(|t| t.as_str())
                    .unwrap_or("");
                ToolResult {
                    call_id: call.call_id,
                    success: true,
                    output: format!("Typed '{}'", text),
                    error: None,
                }
            }
            _ => ToolResult {
                call_id: call.call_id,
                success: false,
                output: String::new(),
                error: Some(format!("Unknown tool: {}", call.tool_name)),
            },
        }
    }

    #[test]
    fn test_continue_on_failure() {
        let calls = vec![
            ToolCall {
                call_id: 1,
                tool_name: "click".to_string(),
                arguments: serde_json::json!({ "index": 10 }),
            },
            ToolCall {
                call_id: 2,
                tool_name: "click".to_string(),
                arguments: serde_json::json!({ "index": 0 }),
            },
            ToolCall {
                call_id: 3,
                tool_name: "input".to_string(),
                arguments: serde_json::json!({ "text": "hello" }),
            },
        ];

        let bus = EventBus::new();
        let results = BatchExecutor::execute_batch(
            calls,
            TransactionPolicy::ContinueOnFailure,
            mock_executor,
            Some(&bus),
        );

        assert_eq!(results.len(), 3);

        // Count successes/failures across all results (order may vary for parallel)
        let succeeded: Vec<_> = results.iter().filter(|r| r.success).collect();
        let failed: Vec<_> = results.iter().filter(|r| !r.success).collect();
        assert_eq!(succeeded.len(), 2);
        assert_eq!(failed.len(), 1);

        // Verify event was emitted
        let events = bus.drain();
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn test_halt_on_failure() {
        let calls = vec![
            ToolCall {
                call_id: 1,
                tool_name: "click".to_string(),
                arguments: serde_json::json!({ "index": 10 }),
            },
            ToolCall {
                call_id: 2,
                tool_name: "click".to_string(),
                arguments: serde_json::json!({ "index": 0 }),
            },
            ToolCall {
                call_id: 3,
                tool_name: "input".to_string(),
                arguments: serde_json::json!({ "text": "hello" }),
            },
        ];

        let bus = EventBus::new();
        let results = BatchExecutor::execute_batch(
            calls,
            TransactionPolicy::HaltOnFailure,
            mock_executor,
            Some(&bus),
        );

        // Should halt after call_id=2 fails
        assert_eq!(results.len(), 2);
        assert!(results[0].success);
        assert!(!results[1].success);

        let events = bus.drain();
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn test_empty_batch() {
        let bus = EventBus::new();
        let results = BatchExecutor::execute_batch(
            vec![],
            TransactionPolicy::ContinueOnFailure,
            mock_executor,
            Some(&bus),
        );

        assert!(results.is_empty());
        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            crate::eac::events::EacEvent::BatchExecuted {
                batch_size,
                succeeded,
                failed,
                ..
            } => {
                assert_eq!(*batch_size, 0);
                assert_eq!(*succeeded, 0);
                assert_eq!(*failed, 0);
            }
            _ => panic!("Expected BatchExecuted event"),
        }
    }

    #[test]
    fn test_aggregate_results() {
        let results = vec![
            ToolResult {
                call_id: 1,
                success: true,
                output: "done".to_string(),
                error: None,
            },
            ToolResult {
                call_id: 2,
                success: false,
                output: String::new(),
                error: Some("timeout".to_string()),
            },
        ];

        let summary = BatchExecutor::aggregate_results(&results);
        assert!(summary.contains("[call_id=1] OK: done"));
        assert!(summary.contains("[call_id=2] FAIL: timeout"));
    }
}
