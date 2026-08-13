//! EaC Event Sourcing — typed events emitted by all EaC subsystems.
//!
//! Every cache hit, batch execution, and state persistence turn emits a
//! corresponding event into this event log to preserve trajectory replay
//! determinism under the [`ReplayValidator`].

use serde::{Deserialize, Serialize};

/// Unique event identifier.
pub type EventId = u64;

/// Event types emitted by the EaC subsystem.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub enum EacEvent {
    /// A cache lookup was performed. `hit` indicates whether a cached response
    /// was found. `match_kind` distinguishes L1 (BLAKE3 exact) vs L2 (cosine
    /// similarity) matches.
    CacheLookup {
        event_id: EventId,
        prompt_hash: String,
        namespace: String,
        hit: bool,
        match_kind: Option<CacheMatchKind>,
        similarity_score: Option<f32>,
        timestamp_ms: u64,
    },

    /// A new record was inserted into the semantic cache.
    CacheInsert {
        event_id: EventId,
        prompt_hash: String,
        namespace: String,
        timestamp_ms: u64,
    },

    /// A namespace was invalidated from the semantic cache.
    CacheInvalidate {
        event_id: EventId,
        namespace: String,
        records_removed: usize,
        timestamp_ms: u64,
    },

    /// A batch of tool calls was executed.
    BatchExecuted {
        event_id: EventId,
        batch_size: usize,
        succeeded: usize,
        failed: usize,
        policy: String,
        elapsed_us: u64,
        timestamp_ms: u64,
    },

    /// A sandbox program was executed.
    SandboxExecution {
        event_id: EventId,
        program_name: String,
        program_hash: [u8; 32],
        steps_executed: usize,
        fuel_consumed: u32,
        trace_hash: [u8; 32],
        timestamp_ms: u64,
    },

    /// State was persisted to the filesystem.
    StatePersisted {
        event_id: EventId,
        state_hash: [u8; 32],
        turn_index: u64,
        file_path: String,
        bytes_written: usize,
        timestamp_ms: u64,
    },

    /// State was loaded from the filesystem.
    StateLoaded {
        event_id: EventId,
        state_hash: [u8; 32],
        turn_index: u64,
        file_path: String,
        timestamp_ms: u64,
    },

    /// A security violation was detected and blocked.
    SecurityViolation {
        event_id: EventId,
        violation_kind: String,
        blocked_pattern: String,
        timestamp_ms: u64,
    },
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq)]
pub enum CacheMatchKind {
    /// BLAKE3 exact hash match (L1).
    ExactBlake3,
    /// Cosine similarity match above threshold (L2).
    SemanticCosine,
}

/// Thread-safe event bus for collecting EaC events.
///
/// Events are stored in insertion order and can be drained for batch export
/// or replayed for deterministic trajectory verification.
#[derive(Debug)]
pub struct EventBus {
    events: parking_lot::Mutex<Vec<EacEvent>>,
    next_id: std::sync::atomic::AtomicU64,
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}

impl EventBus {
    pub fn new() -> Self {
        Self {
            events: parking_lot::Mutex::new(Vec::new()),
            next_id: std::sync::atomic::AtomicU64::new(1),
        }
    }

    /// Allocate a monotonically increasing event ID.
    pub fn next_event_id(&self) -> EventId {
        self.next_id
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    }

    /// Emit an event to the bus.
    pub fn emit(&self, event: EacEvent) {
        self.events.lock().push(event);
    }

    /// Return the number of recorded events.
    pub fn len(&self) -> usize {
        self.events.lock().len()
    }

    /// Check if the event bus is empty.
    pub fn is_empty(&self) -> bool {
        self.events.lock().is_empty()
    }

    /// Drain all events from the bus, returning them in insertion order.
    pub fn drain(&self) -> Vec<EacEvent> {
        let mut guard = self.events.lock();
        std::mem::take(&mut *guard)
    }

    /// Read a snapshot of all events without draining.
    pub fn snapshot(&self) -> Vec<EacEvent> {
        self.events.lock().clone()
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

/// Helper to emit a `CacheLookup` event.
pub fn emit_cache_lookup(
    bus: &EventBus,
    prompt_hash: String,
    namespace: String,
    hit: bool,
    match_kind: Option<CacheMatchKind>,
    similarity_score: Option<f32>,
) {
    bus.emit(EacEvent::CacheLookup {
        event_id: bus.next_event_id(),
        prompt_hash,
        namespace,
        hit,
        match_kind,
        similarity_score,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `CacheInsert` event.
pub fn emit_cache_insert(bus: &EventBus, prompt_hash: String, namespace: String) {
    bus.emit(EacEvent::CacheInsert {
        event_id: bus.next_event_id(),
        prompt_hash,
        namespace,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `CacheInvalidate` event.
pub fn emit_cache_invalidate(bus: &EventBus, namespace: String, records_removed: usize) {
    bus.emit(EacEvent::CacheInvalidate {
        event_id: bus.next_event_id(),
        namespace,
        records_removed,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `BatchExecuted` event.
pub fn emit_batch_executed(
    bus: &EventBus,
    batch_size: usize,
    succeeded: usize,
    failed: usize,
    policy: &str,
    elapsed_us: u64,
) {
    bus.emit(EacEvent::BatchExecuted {
        event_id: bus.next_event_id(),
        batch_size,
        succeeded,
        failed,
        policy: policy.to_string(),
        elapsed_us,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `SandboxExecution` event.
pub fn emit_sandbox_execution(
    bus: &EventBus,
    program_name: String,
    program_hash: [u8; 32],
    steps_executed: usize,
    fuel_consumed: u32,
    trace_hash: [u8; 32],
) {
    bus.emit(EacEvent::SandboxExecution {
        event_id: bus.next_event_id(),
        program_name,
        program_hash,
        steps_executed,
        fuel_consumed,
        trace_hash,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `StatePersisted` event.
pub fn emit_state_persisted(
    bus: &EventBus,
    state_hash: [u8; 32],
    turn_index: u64,
    file_path: String,
    bytes_written: usize,
) {
    bus.emit(EacEvent::StatePersisted {
        event_id: bus.next_event_id(),
        state_hash,
        turn_index,
        file_path,
        bytes_written,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `StateLoaded` event.
pub fn emit_state_loaded(bus: &EventBus, state_hash: [u8; 32], turn_index: u64, file_path: String) {
    bus.emit(EacEvent::StateLoaded {
        event_id: bus.next_event_id(),
        state_hash,
        turn_index,
        file_path,
        timestamp_ms: now_ms(),
    });
}

/// Helper to emit a `SecurityViolation` event.
pub fn emit_security_violation(bus: &EventBus, violation_kind: &str, blocked_pattern: &str) {
    bus.emit(EacEvent::SecurityViolation {
        event_id: bus.next_event_id(),
        violation_kind: violation_kind.to_string(),
        blocked_pattern: blocked_pattern.to_string(),
        timestamp_ms: now_ms(),
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_event_bus_emit_and_drain() {
        let bus = EventBus::new();
        assert!(bus.is_empty());

        emit_cache_insert(&bus, "abc123".to_string(), "ns1".to_string());
        emit_cache_lookup(
            &bus,
            "abc123".to_string(),
            "ns1".to_string(),
            true,
            Some(CacheMatchKind::ExactBlake3),
            None,
        );

        assert_eq!(bus.len(), 2);

        let events = bus.drain();
        assert_eq!(events.len(), 2);
        assert!(bus.is_empty());

        match &events[0] {
            EacEvent::CacheInsert {
                event_id,
                prompt_hash,
                namespace,
                ..
            } => {
                assert_eq!(*event_id, 1);
                assert_eq!(prompt_hash, "abc123");
                assert_eq!(namespace, "ns1");
            }
            other => panic!("Expected CacheInsert, got {:?}", other),
        }

        match &events[1] {
            EacEvent::CacheLookup {
                event_id,
                hit,
                match_kind,
                ..
            } => {
                assert_eq!(*event_id, 2);
                assert!(*hit);
                assert_eq!(*match_kind, Some(CacheMatchKind::ExactBlake3));
            }
            other => panic!("Expected CacheLookup, got {:?}", other),
        }
    }

    #[test]
    fn test_event_bus_snapshot_does_not_drain() {
        let bus = EventBus::new();
        emit_cache_invalidate(&bus, "ns1".to_string(), 5);

        let snapshot = bus.snapshot();
        assert_eq!(snapshot.len(), 1);
        assert_eq!(bus.len(), 1); // Still there

        let drained = bus.drain();
        assert_eq!(drained.len(), 1);
        assert!(bus.is_empty());
    }

    #[test]
    fn test_event_ids_are_monotonic() {
        let bus = EventBus::new();
        emit_cache_insert(&bus, "a".to_string(), "ns".to_string());
        emit_cache_insert(&bus, "b".to_string(), "ns".to_string());
        emit_cache_insert(&bus, "c".to_string(), "ns".to_string());

        let events = bus.drain();
        let ids: Vec<EventId> = events
            .iter()
            .map(|e| match e {
                EacEvent::CacheInsert { event_id, .. } => *event_id,
                _ => 0,
            })
            .collect();

        assert_eq!(ids, vec![1, 2, 3]);
    }

    #[test]
    fn test_batch_executed_event() {
        let bus = EventBus::new();
        emit_batch_executed(&bus, 5, 4, 1, "ContinueOnFailure", 1234);

        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            EacEvent::BatchExecuted {
                batch_size,
                succeeded,
                failed,
                policy,
                elapsed_us,
                ..
            } => {
                assert_eq!(*batch_size, 5);
                assert_eq!(*succeeded, 4);
                assert_eq!(*failed, 1);
                assert_eq!(policy, "ContinueOnFailure");
                assert_eq!(*elapsed_us, 1234);
            }
            other => panic!("Expected BatchExecuted, got {:?}", other),
        }
    }

    #[test]
    fn test_security_violation_event() {
        let bus = EventBus::new();
        emit_security_violation(&bus, "PathTraversal", "../etc/passwd");

        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            EacEvent::SecurityViolation {
                violation_kind,
                blocked_pattern,
                ..
            } => {
                assert_eq!(violation_kind, "PathTraversal");
                assert_eq!(blocked_pattern, "../etc/passwd");
            }
            other => panic!("Expected SecurityViolation, got {:?}", other),
        }
    }
}
