use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::net::TcpListener;
use thiserror::Error;

pub const RUNTIME_TELEMETRY_SCHEMA_V1: &str = "aegis-runtime-telemetry-v1";

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TelemetryCorrelation {
    pub mission_id: String,
    pub task_id: u128,
    pub run_id: u128,
    pub attempt_id: u64,
    pub lease_id: Option<u128>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeTelemetryKind {
    Admission,
    Queue,
    Lane,
    Resource,
    Cancellation,
    Timeout,
    Sandbox,
    Provider,
    Replay,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RuntimeTelemetryEvent {
    pub schema: String,
    pub emitted_at_ms: u64,
    pub correlation: TelemetryCorrelation,
    pub kind: RuntimeTelemetryKind,
    pub outcome: String,
    pub queue_depth: Option<u32>,
    pub active: Option<u32>,
    pub limit: Option<u32>,
}

impl RuntimeTelemetryEvent {
    pub fn new(
        emitted_at_ms: u64,
        correlation: TelemetryCorrelation,
        kind: RuntimeTelemetryKind,
        outcome: impl Into<String>,
    ) -> Result<Self, TelemetryError> {
        if correlation.mission_id.trim().is_empty()
            || correlation.task_id == 0
            || correlation.run_id == 0
            || correlation.attempt_id == 0
        {
            return Err(TelemetryError::InvalidCorrelation);
        }
        let outcome = outcome.into();
        if outcome.is_empty() || outcome.len() > 128 {
            return Err(TelemetryError::InvalidOutcome);
        }
        Ok(Self {
            schema: RUNTIME_TELEMETRY_SCHEMA_V1.to_string(),
            emitted_at_ms,
            correlation,
            kind,
            outcome,
            queue_depth: None,
            active: None,
            limit: None,
        })
    }
}

#[derive(Debug, Error, Eq, PartialEq)]
pub enum TelemetryError {
    #[error("invalid telemetry correlation")]
    InvalidCorrelation,
    #[error("invalid telemetry outcome")]
    InvalidOutcome,
    #[error("telemetry sink capacity must be non-zero")]
    InvalidCapacity,
}

pub trait TelemetrySink: Send {
    fn record(&mut self, event: RuntimeTelemetryEvent) -> Result<(), TelemetryError>;
    fn snapshot(&self) -> Vec<RuntimeTelemetryEvent>;
    fn dropped(&self) -> u64;
}

#[derive(Clone, Debug)]
pub struct BoundedTelemetrySink {
    capacity: usize,
    events: VecDeque<RuntimeTelemetryEvent>,
    dropped: u64,
}

impl BoundedTelemetrySink {
    pub fn new(capacity: usize) -> Result<Self, TelemetryError> {
        if capacity == 0 {
            return Err(TelemetryError::InvalidCapacity);
        }
        Ok(Self {
            capacity,
            events: VecDeque::with_capacity(capacity),
            dropped: 0,
        })
    }
}

impl TelemetrySink for BoundedTelemetrySink {
    fn record(&mut self, event: RuntimeTelemetryEvent) -> Result<(), TelemetryError> {
        if self.events.len() == self.capacity {
            self.events.pop_front();
            self.dropped = self.dropped.saturating_add(1);
        }
        self.events.push_back(event);
        Ok(())
    }

    fn snapshot(&self) -> Vec<RuntimeTelemetryEvent> {
        self.events.iter().cloned().collect()
    }

    fn dropped(&self) -> u64 {
        self.dropped
    }
}

const REQUEST_BUFFER_BYTES: usize = 1024;

pub fn physical_metrics_http_response() -> String {
    prometheus_http_response(&crate::physical::physical_metrics_prometheus())
}

pub fn prometheus_http_response(body: &str) -> String {
    format!(
        concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n",
            "Content-Length: {}\r\n",
            "Connection: close\r\n",
            "\r\n",
            "{}"
        ),
        body.len(),
        body
    )
}

pub fn serve_physical_metrics_once(listener: &TcpListener) -> std::io::Result<()> {
    let (mut stream, _) = listener.accept()?;
    let mut request = [0u8; REQUEST_BUFFER_BYTES];
    let bytes_read = stream.read(&mut request)?;
    let request_text = std::str::from_utf8(&request[..bytes_read]).unwrap_or("");

    let response =
        if request_text.starts_with("GET /metrics ") || request_text.starts_with("GET /metrics?") {
            physical_metrics_http_response()
        } else {
            not_found_response()
        };

    stream.write_all(response.as_bytes())?;
    stream.flush()
}

fn not_found_response() -> String {
    let body = "not found\n";
    format!(
        concat!(
            "HTTP/1.1 404 Not Found\r\n",
            "Content-Type: text/plain; charset=utf-8\r\n",
            "Content-Length: {}\r\n",
            "Connection: close\r\n",
            "\r\n",
            "{}"
        ),
        body.len(),
        body
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(outcome: &str) -> RuntimeTelemetryEvent {
        RuntimeTelemetryEvent::new(
            1,
            TelemetryCorrelation {
                mission_id: "mission-1".to_string(),
                task_id: 1,
                run_id: 2,
                attempt_id: 1,
                lease_id: Some(3),
            },
            RuntimeTelemetryKind::Admission,
            outcome,
        )
        .unwrap()
    }

    #[test]
    fn bounded_sink_drops_oldest_without_becoming_authority() {
        let mut sink = BoundedTelemetrySink::new(2).unwrap();
        sink.record(event("admitted")).unwrap();
        sink.record(event("queued")).unwrap();
        sink.record(event("rejected")).unwrap();

        let snapshot = sink.snapshot();
        assert_eq!(snapshot.len(), 2);
        assert_eq!(snapshot[0].outcome, "queued");
        assert_eq!(snapshot[1].outcome, "rejected");
        assert_eq!(sink.dropped(), 1);
    }

    #[test]
    fn invalid_correlation_is_rejected_before_recording() {
        let error = RuntimeTelemetryEvent::new(
            1,
            TelemetryCorrelation {
                mission_id: String::new(),
                task_id: 1,
                run_id: 2,
                attempt_id: 1,
                lease_id: None,
            },
            RuntimeTelemetryKind::Resource,
            "sampled",
        )
        .unwrap_err();
        assert_eq!(error, TelemetryError::InvalidCorrelation);
    }
}
