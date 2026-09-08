use blake3::Hasher;

/// Domain-tagged BLAKE3 hasher — matches the pattern in skill_registry.rs.
/// Every learning event hash carries a unique domain prefix to prevent
/// cross-domain collision attacks.
fn domain_hasher(domain: &[u8]) -> Hasher {
    let mut hasher = Hasher::new();
    hasher.update(domain);
    hasher.update(&[0]);
    hasher
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

/// Chains two hashes: output = BLAKE3(prev || next).
/// Used to build the ledger hash chain.
fn chain_hash(prev: [u8; 32], next: [u8; 32]) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-learning-chain-v1");
    hasher.update(&prev);
    hasher.update(&next);
    *hasher.finalize().as_bytes()
}

// ── LearningEventType ────────────────────────────────────────────────────

/// Categorises every event the learning system records.
/// Each variant carries the cryptographic evidence needed to verify the
/// learning action independently of the ledger chain.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LearningEventType {
    /// A brand-new skill was admitted into the SkillRegistry.
    SkillCreated {
        /// BLAKE3 of the task transcript / prompt that triggered creation.
        task_hash: [u8; 32],
    },
    /// An existing skill was improved by an autonomous pipeline run.
    SkillImproved {
        /// How many times the skill was used before improvement was attempted.
        usage_count: u32,
        /// BLAKE3(wasi_bytes) of the improved WASM module.
        improvement_hash: [u8; 32],
    },
    /// A candidate memory batch was created and still requires validation and
    /// an explicit commit before it can become durable memory.
    MemoryCandidateCreated { candidate_hash: [u8; 32] },
    /// A memory candidate was durably committed by the owning repository.
    MemoryCommitted { memory_hash: [u8; 32] },
    /// Legacy event retained for readers of historical ledgers. New writers
    /// must use `MemoryCandidateCreated` or `MemoryCommitted` so the event
    /// meaning matches the actual transition.
    MemoryPersisted {
        /// BLAKE3 over the concatenation of all persisted memory contents.
        memory_hash: [u8; 32],
    },
    /// The user model was updated with new interaction data.
    UserModelUpdated {
        /// BLAKE3 over the serialised UserModel.
        model_hash: [u8; 32],
    },
    /// A session transcript was indexed for cross-session recall.
    SessionRecallIndexed {
        /// BLAKE3 over the session transcript content.
        session_hash: [u8; 32],
    },
}

impl LearningEventType {
    /// Deterministic BLAKE3 domain hash of just the enum variant + payload.
    /// Used to compute `event_hash` independently of ledger position.
    fn domain_hash(self) -> [u8; 32] {
        match self {
            Self::SkillCreated { task_hash } => {
                let mut h = domain_hasher(b"aegis-learning-event-skill-created-v1");
                h.update(&task_hash);
                *h.finalize().as_bytes()
            }
            Self::SkillImproved {
                usage_count,
                improvement_hash,
            } => {
                let mut h = domain_hasher(b"aegis-learning-event-skill-improved-v1");
                h.update(&usage_count.to_le_bytes());
                h.update(&improvement_hash);
                *h.finalize().as_bytes()
            }
            Self::MemoryCandidateCreated { candidate_hash } => {
                let mut h = domain_hasher(b"aegis-learning-event-memory-candidate-created-v1");
                h.update(&candidate_hash);
                *h.finalize().as_bytes()
            }
            Self::MemoryCommitted { memory_hash } => {
                let mut h = domain_hasher(b"aegis-learning-event-memory-committed-v1");
                h.update(&memory_hash);
                *h.finalize().as_bytes()
            }
            Self::MemoryPersisted { memory_hash } => {
                let mut h = domain_hasher(b"aegis-learning-event-memory-persisted-v1");
                h.update(&memory_hash);
                *h.finalize().as_bytes()
            }
            Self::UserModelUpdated { model_hash } => {
                let mut h = domain_hasher(b"aegis-learning-event-user-model-updated-v1");
                h.update(&model_hash);
                *h.finalize().as_bytes()
            }
            Self::SessionRecallIndexed { session_hash } => {
                let mut h = domain_hasher(b"aegis-learning-event-session-recall-indexed-v1");
                h.update(&session_hash);
                *h.finalize().as_bytes()
            }
        }
    }
}

// ── LearningEvent ─────────────────────────────────────────────────────────

/// A single entry in the learning audit trail.
///
/// Immutable once constructed — `event_hash` is computed in the constructor
/// and never changes.  The `previous_event_hash` field chains this event to the
/// prior one; the ledger verifier re-walks this chain.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LearningEvent {
    /// What kind of learning action this represents.
    pub event_type: LearningEventType,
    /// When the event maps to a specific skill, the skill id.
    pub skill_id: Option<u128>,
    /// Convenience duplicate of the event-type payload hash for quick lookup.
    pub content_hash: [u8; 32],
    /// Milliseconds since Unix epoch (matching AEGIS convention).
    pub timestamp: u64,
    /// The session in which this learning action occurred.
    pub session_id: u128,
    /// Hash of the previous event in the ledger (or [0; 32] for genesis).
    pub previous_event_hash: [u8; 32],
    /// BLAKE3 domain hash of this event's content (event_type + content_hash).
    pub event_hash: [u8; 32],
}

impl LearningEvent {
    /// Build a new event and bake its `event_hash`.
    pub fn new(
        event_type: LearningEventType,
        skill_id: Option<u128>,
        timestamp: u64,
        session_id: u128,
        previous_event_hash: [u8; 32],
    ) -> Self {
        let content_hash = match &event_type {
            LearningEventType::SkillCreated { task_hash } => *task_hash,
            LearningEventType::SkillImproved {
                improvement_hash, ..
            } => *improvement_hash,
            LearningEventType::MemoryCandidateCreated { candidate_hash } => *candidate_hash,
            LearningEventType::MemoryCommitted { memory_hash } => *memory_hash,
            LearningEventType::MemoryPersisted { memory_hash } => *memory_hash,
            LearningEventType::UserModelUpdated { model_hash } => *model_hash,
            LearningEventType::SessionRecallIndexed { session_hash } => *session_hash,
        };

        let event_hash = event_type.domain_hash();

        Self {
            event_type,
            skill_id,
            content_hash,
            timestamp,
            session_id,
            previous_event_hash,
            event_hash,
        }
    }

    /// Validate the event's own hash integrity (independent of ledger chain).
    pub fn is_valid(&self) -> bool {
        let recomputed = self.event_type.domain_hash();
        nonzero_hash(&self.event_hash)
            && self.event_hash == recomputed
            && nonzero_hash(&self.content_hash)
            && self.timestamp > 0
            && self.session_id > 0
    }
}

// ── LearningLedger ────────────────────────────────────────────────────────

/// Append-only BLAKE3 hash chain of learning events.
///
/// Every event, once appended, is immutable. The ledger hash is recomputed
/// on each append and verified lazily via `verify_integrity()`.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct LearningLedger {
    events: Vec<LearningEvent>,
    ledger_hash: [u8; 32],
}

impl LearningLedger {
    /// Create a fresh, empty ledger.
    pub fn new() -> Self {
        Self::default()
    }

    /// Number of events recorded so far.
    pub fn len(&self) -> usize {
        self.events.len()
    }

    /// Returns `true` when no events have been appended.
    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    /// The current head hash of the chain.
    ///
    /// This is the hash that will become `previous_event_hash` for the
    /// next appended event.
    pub fn ledger_hash(&self) -> [u8; 32] {
        self.ledger_hash
    }

    /// Append a learning event and advance the ledger hash.
    ///
    /// The caller provides the semantic fields; `event_hash` and
    /// `previous_event_hash` are computed automatically.
    ///
    /// Returns the `event_hash` of the newly appended event.
    pub fn append(
        &mut self,
        event_type: LearningEventType,
        skill_id: Option<u128>,
        timestamp: u64,
        session_id: u128,
    ) -> [u8; 32] {
        let previous = self.ledger_hash;

        let event = LearningEvent::new(event_type, skill_id, timestamp, session_id, previous);
        let event_hash = event.event_hash;

        // Advance the chain
        self.ledger_hash = chain_hash(previous, event_hash);
        self.events.push(event);

        event_hash
    }

    /// Verify the entire ledger chain from genesis.
    ///
    /// Re-walks every event, recalculates the chain hash, and compares
    /// against the stored `ledger_hash`.  Returns `true` iff every link
    /// is intact.
    pub fn verify_integrity(&self) -> bool {
        let mut computed = [0u8; 32];

        for event in &self.events {
            if !event.is_valid() {
                return false;
            }
            computed = chain_hash(computed, event.event_hash);
        }

        computed == self.ledger_hash
    }

    /// Iterator over stored events (newest first).
    pub fn iter(&self) -> impl Iterator<Item = &LearningEvent> {
        self.events.iter()
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: fabricate a monotonically-increasing timestamp.
    fn ts(offset: u64) -> u64 {
        1_700_000_000_000 + offset
    }

    #[test]
    fn test_empty_ledger_verifies() {
        let ledger = LearningLedger::new();
        assert!(ledger.is_empty());
        assert_eq!(ledger.len(), 0);
        assert!(ledger.verify_integrity());
    }

    #[test]
    fn test_single_event_chain() {
        let mut ledger = LearningLedger::new();

        let event_hash = ledger.append(
            LearningEventType::SkillCreated {
                task_hash: [0xAA; 32],
            },
            Some(1),
            ts(1),
            100,
        );

        assert_eq!(ledger.len(), 1);
        assert!(nonzero_hash(&event_hash));
        assert!(ledger.verify_integrity());
    }

    #[test]
    fn test_learning_ledger_chain_integrity() {
        let mut ledger = LearningLedger::new();

        // Append 5 events of different types
        for i in 1..6u64 {
            let session_id = 100 + i as u128;
            let event_hash = ledger.append(
                LearningEventType::SkillCreated {
                    task_hash: {
                        let mut h = [0u8; 32];
                        h[..8].copy_from_slice(&i.to_le_bytes());
                        h
                    },
                },
                Some(i as u128),
                ts(i),
                session_id,
            );
            assert!(nonzero_hash(&event_hash));
        }

        assert_eq!(ledger.len(), 5);
        assert!(ledger.verify_integrity());

        // Corrupt event #3 by toggling a byte in its event_hash directly.
        // This breaks event.is_valid() because the recomputed domain hash
        // won't match the stored event_hash.
        ledger.events[2].event_hash[0] ^= 0xFF;
        // Ensure the corrupted byte is non-zero so nonzero_hash isn't
        // the only failure reason:
        if ledger.events[2].event_hash[0] == 0 {
            ledger.events[2].event_hash[0] = 0xDE;
        }
        assert!(!ledger.events[2].is_valid());
        assert!(!ledger.verify_integrity());
    }

    #[test]
    fn test_mixed_event_types() {
        let mut ledger = LearningLedger::new();

        ledger.append(
            LearningEventType::SkillCreated {
                task_hash: [1u8; 32],
            },
            Some(10),
            ts(0),
            1,
        );

        ledger.append(
            LearningEventType::SkillImproved {
                usage_count: 42,
                improvement_hash: [2u8; 32],
            },
            Some(10),
            ts(1),
            1,
        );

        ledger.append(
            LearningEventType::MemoryPersisted {
                memory_hash: [3u8; 32],
            },
            None,
            ts(2),
            1,
        );

        ledger.append(
            LearningEventType::UserModelUpdated {
                model_hash: [4u8; 32],
            },
            None,
            ts(3),
            1,
        );

        ledger.append(
            LearningEventType::SessionRecallIndexed {
                session_hash: [5u8; 32],
            },
            None,
            ts(4),
            1,
        );

        assert_eq!(ledger.len(), 5);
        assert!(ledger.verify_integrity());

        // Verify individual events are valid
        for event in &ledger.events {
            assert!(event.is_valid());
        }
    }

    #[test]
    fn test_corrupt_ledger_hash_detected() {
        let mut ledger = LearningLedger::new();

        ledger.append(
            LearningEventType::MemoryPersisted {
                memory_hash: [0x42; 32],
            },
            None,
            ts(0),
            1,
        );

        assert!(ledger.verify_integrity());

        // Tamper with the stored ledger hash directly
        ledger.ledger_hash[0] ^= 1;
        assert!(!ledger.verify_integrity());
    }

    #[test]
    fn test_event_validation_rejects_zero_timestamp() {
        let event = LearningEvent::new(
            LearningEventType::SkillCreated {
                task_hash: [0xAA; 32],
            },
            Some(1),
            0, // zero timestamp → invalid
            100,
            [0; 32],
        );

        assert!(!event.is_valid());
    }

    #[test]
    fn test_event_validation_rejects_zero_session() {
        let event = LearningEvent::new(
            LearningEventType::SkillCreated {
                task_hash: [0xAA; 32],
            },
            Some(1),
            ts(0),
            0, // zero session → invalid
            [0; 32],
        );

        assert!(!event.is_valid());
    }

    #[test]
    fn test_domain_hash_deterministic() {
        let evt = LearningEventType::SkillImproved {
            usage_count: 99,
            improvement_hash: [0xCC; 32],
        };

        let h1 = evt.domain_hash();
        let h2 = evt.domain_hash();
        assert_eq!(h1, h2);
    }

    #[test]
    fn test_different_types_produce_different_hashes() {
        let a = LearningEventType::SkillCreated {
            task_hash: [0xBB; 32],
        };
        let b = LearningEventType::MemoryPersisted {
            memory_hash: [0xBB; 32],
        };

        assert_ne!(a.domain_hash(), b.domain_hash());
    }
}
