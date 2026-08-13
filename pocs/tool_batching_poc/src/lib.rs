use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolCall {
    pub call_id: u32,
    pub tool_name: String,
    pub arguments: serde_json::Value,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolResult {
    pub call_id: u32,
    pub success: bool,
    pub output: String,
    pub error: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq)]
pub enum TransactionPolicy {
    HaltOnFailure,
    ContinueOnFailure,
}

pub struct ToolBatcher;

impl ToolBatcher {
    pub async fn execute_batch(
        calls: Vec<ToolCall>,
        policy: TransactionPolicy,
        // Mock handler that takes a tool call and simulates execution
        mock_executor: impl Fn(ToolCall) -> std::pin::Pin<Box<dyn std::future::Future<Output = ToolResult> + Send>>
            + Send
            + Sync
            + 'static,
    ) -> Vec<ToolResult> {
        let mut results = Vec::new();
        let mock_executor = std::sync::Arc::new(mock_executor);

        if policy == TransactionPolicy::ContinueOnFailure {
            // Run all in parallel concurrently
            let mut futures = Vec::new();
            for call in calls {
                let exec = mock_executor.clone();
                futures.push(tokio::spawn(async move { exec(call).await }));
            }

            for f in futures {
                if let Ok(res) = f.await {
                    results.push(res);
                }
            }
        } else {
            // Run sequentially, halt if any fails
            for call in calls {
                let call_id = call.call_id;
                let res = mock_executor(call).await;
                let success = res.success;
                results.push(res);
                if !success {
                    println!(
                        "[BATCH] Halting execution at call_id: {} due to failure.",
                        call_id
                    );
                    break;
                }
            }
        }

        results
    }
}

pub async fn run_mock_tool(call: ToolCall) -> ToolResult {
    // Simulate minor network or compute latency
    tokio::time::sleep(Duration::from_millis(50)).await;

    let tool_name = call.tool_name.as_str();
    match tool_name {
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
                    output: format!("Successfully clicked element {}", index),
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
                output: format!("Successfully typed '{}'", text),
                error: None,
            }
        }
        _ => ToolResult {
            call_id: call.call_id,
            success: false,
            output: String::new(),
            error: Some(format!("Unknown tool: {}", tool_name)),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_continue_on_failure() {
        let calls = vec![
            ToolCall {
                call_id: 1,
                tool_name: "click".to_string(),
                arguments: serde_json::json!({ "index": 10 }),
            },
            ToolCall {
                call_id: 2,
                tool_name: "click".to_string(), // index 0 fails
                arguments: serde_json::json!({ "index": 0 }),
            },
            ToolCall {
                call_id: 3,
                tool_name: "input".to_string(),
                arguments: serde_json::json!({ "text": "hello" }),
            },
        ];

        let results =
            ToolBatcher::execute_batch(calls, TransactionPolicy::ContinueOnFailure, |call| {
                Box::pin(run_mock_tool(call))
            })
            .await;

        assert_eq!(results.len(), 3);
        assert!(results[0].success);
        assert!(!results[1].success);
        assert!(results[2].success);
    }

    #[tokio::test]
    async fn test_halt_on_failure() {
        let calls = vec![
            ToolCall {
                call_id: 1,
                tool_name: "click".to_string(),
                arguments: serde_json::json!({ "index": 10 }),
            },
            ToolCall {
                call_id: 2,
                tool_name: "click".to_string(), // index 0 fails
                arguments: serde_json::json!({ "index": 0 }),
            },
            ToolCall {
                call_id: 3,
                tool_name: "input".to_string(),
                arguments: serde_json::json!({ "text": "hello" }),
            },
        ];

        let results = ToolBatcher::execute_batch(calls, TransactionPolicy::HaltOnFailure, |call| {
            Box::pin(run_mock_tool(call))
        })
        .await;

        assert_eq!(results.len(), 2); // halted after index 2
        assert!(results[0].success);
        assert!(!results[1].success);
    }
}
