use crate::learning::{LearningEventType, LearningLedger};
use crate::licensing::{Feature as LicenseFeature, LicenseError, LicenseManager};
use crate::memory::CogniFoldStore;
use crate::physical::PhysicalWatchdog;
use blake3::Hasher;

/// Domain-tagged BLAKE3 hasher — matches the hasher pattern used in
/// `skill_registry.rs` and `learning/mod.rs`.
fn domain_hasher(domain: &[u8]) -> Hasher {
    let mut hasher = Hasher::new();
    hasher.update(domain);
    hasher.update(&[0]);
    hasher
}

fn nonzero_hash(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

// ── Error ─────────────────────────────────────────────────────────────────

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MemoryError {
    EmptyCandidates,
    InvalidCandidate,
    PAVRejected,
    CogniFoldCommitFailed,
    LicenseRequired(LicenseError),
}

// ── MemoryCandidate ──────────────────────────────────────────────────────

/// A single fact extracted from a session transcript that may warrant
/// persistence into CogniFold.
#[derive(Clone, Debug, PartialEq)]
pub struct MemoryCandidate {
    pub content: String,
    pub content_hash: [u8; 32],
    pub relevance_score: f32,
    pub source_session_id: u128,
}

impl MemoryCandidate {
    pub fn new(content: String, relevance_score: f32, source_session_id: u128) -> Option<Self> {
        if content.is_empty()
            || !relevance_score.is_finite()
            || relevance_score < 0.0
            || relevance_score > 1.0
            || source_session_id == 0
        {
            return None;
        }
        let content_hash = {
            let mut h = domain_hasher(b"aegis-memory-candidate-v1");
            h.update(content.as_bytes());
            *h.finalize().as_bytes()
        };
        Some(Self {
            content,
            content_hash,
            relevance_score,
            source_session_id,
        })
    }

    pub fn is_valid(&self) -> bool {
        !self.content.is_empty()
            && nonzero_hash(&self.content_hash)
            && self.relevance_score.is_finite()
            && self.relevance_score >= 0.0
            && self.relevance_score <= 1.0
            && self.source_session_id > 0
            && {
                let recomputed = {
                    let mut h = domain_hasher(b"aegis-memory-candidate-v1");
                    h.update(self.content.as_bytes());
                    *h.finalize().as_bytes()
                };
                self.content_hash == recomputed
            }
    }
}

// ── MemoryNudge ───────────────────────────────────────────────────────────

/// A batch of memory candidates presented for persistence.
/// Cryptographically sealed with a nudge-hash to enable audit.
#[derive(Clone, Debug, PartialEq)]
pub struct MemoryNudge {
    pub nudge_id: u128,
    pub session_id: u128,
    pub candidates: Vec<MemoryCandidate>,
    pub candidate_list_hash: [u8; 32],
    pub nudge_hash: [u8; 32],
    pub timestamp: u64,
}

impl MemoryNudge {
    pub fn new(
        nudge_id: u128,
        session_id: u128,
        candidates: Vec<MemoryCandidate>,
        timestamp: u64,
    ) -> Option<Self> {
        if nudge_id == 0 || session_id == 0 || candidates.is_empty() || timestamp == 0 {
            return None;
        }
        if candidates
            .iter()
            .any(|c| !c.is_valid() || c.source_session_id != session_id)
        {
            return None;
        }
        let candidate_list_hash = candidate_list_domain_hash(&candidates);
        let nudge_hash = nudge_domain_hash(nudge_id, session_id, candidate_list_hash, timestamp);
        Some(Self {
            nudge_id,
            session_id,
            candidates,
            candidate_list_hash,
            nudge_hash,
            timestamp,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.nudge_id > 0
            && self.session_id > 0
            && !self.candidates.is_empty()
            && self.candidates.iter().all(|candidate| {
                candidate.is_valid() && candidate.source_session_id == self.session_id
            })
            && self.timestamp > 0
            && self.candidate_list_hash == candidate_list_domain_hash(&self.candidates)
            && self.nudge_hash
                == nudge_domain_hash(
                    self.nudge_id,
                    self.session_id,
                    self.candidate_list_hash,
                    self.timestamp,
                )
    }
}

// ── MemoryNudgeSystem ────────────────────────────────────────────────────

/// Background subsystem that periodically scans recent sessions and extracts
/// candidate facts. Candidates require a separate explicit commit step.
///
/// Every state-mutating action is logged into the `LearningLedger`.
pub struct MemoryNudgeSystem {
    pub nudge_threshold: f32,
}

impl MemoryNudgeSystem {
    pub fn new(nudge_threshold: f32) -> Self {
        Self {
            nudge_threshold: nudge_threshold.clamp(0.0, 1.0),
        }
    }

    /// Filter raw candidates by relevance score, build a
    /// cryptographically-sealed `MemoryNudge`, and record candidate creation
    /// in the learning ledger. This does not claim persistence.
    pub fn periodic_nudge(
        &self,
        nudge_id: u128,
        session_id: u128,
        raw_candidates: Vec<MemoryCandidate>,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<MemoryNudge, MemoryError> {
        // ── Commercial Gate: CrossSessionMemory requires Pro/Team/Enterprise ──
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::CrossSessionMemory)
                .map_err(MemoryError::LicenseRequired)?;
        }

        let high_relevance: Vec<MemoryCandidate> = raw_candidates
            .into_iter()
            .filter(|c| c.relevance_score >= self.nudge_threshold)
            .collect();

        if high_relevance.is_empty() {
            return Err(MemoryError::EmptyCandidates);
        }

        let timestamp = now_millis();

        let nudge = MemoryNudge::new(nudge_id, session_id, high_relevance, timestamp)
            .ok_or(MemoryError::InvalidCandidate)?;

        // Append a candidate-only event. Durable repository state remains
        // separate; a compatibility projection is recorded only after the
        // explicit semantic projection step succeeds.
        learning_ledger.append(
            LearningEventType::MemoryCandidateCreated {
                candidate_hash: nudge.nudge_hash,
            },
            None,
            timestamp,
            session_id,
        );

        Ok(nudge)
    }

    /// Compatibility entrypoint for callers that still provide a physical
    /// watchdog. Semantic candidates no longer pass through that watchdog;
    /// the parameter remains only for source compatibility.
    #[deprecated(note = "use commit_nudged_memories_with_ledger for authoritative commits")]
    pub fn commit_nudged_memories(
        &self,
        nudge: &MemoryNudge,
        cognifold: &mut CogniFoldStore,
        _watchdog: &PhysicalWatchdog,
    ) -> Result<usize, MemoryError> {
        let mut compatibility_ledger = LearningLedger::new();
        self.commit_nudged_memories_semantic_with_ledger(
            nudge,
            cognifold,
            &mut compatibility_ledger,
        )
    }

    /// Commit each candidate as semantic memory. No physical AST/fuel/PAV
    /// sentinel is manufactured for this path. The watchdog parameter is kept
    /// for compatibility with the pre-separation API and is intentionally
    /// ignored.
    ///
    /// Returns the count of successfully committed candidates.
    pub fn commit_nudged_memories_with_ledger(
        &self,
        nudge: &MemoryNudge,
        cognifold: &mut CogniFoldStore,
        watchdog: &PhysicalWatchdog,
        learning_ledger: &mut LearningLedger,
    ) -> Result<usize, MemoryError> {
        let _ = watchdog;
        self.commit_nudged_memories_semantic_with_ledger(nudge, cognifold, learning_ledger)
    }

    /// Canonical semantic-memory commit path for nudge candidates.
    pub fn commit_nudged_memories_semantic_with_ledger(
        &self,
        nudge: &MemoryNudge,
        cognifold: &mut CogniFoldStore,
        learning_ledger: &mut LearningLedger,
    ) -> Result<usize, MemoryError> {
        if !nudge.is_valid() {
            return Err(MemoryError::InvalidCandidate);
        }

        let mut committed = 0usize;

        for candidate in &nudge.candidates {
            match cognifold.commit_semantic_memory(
                nudge.session_id,
                candidate.content.as_bytes(),
                candidate.relevance_score,
            ) {
                Ok(_) => {
                    learning_ledger.append(
                        LearningEventType::MemoryProjectionCommitted {
                            memory_hash: candidate.content_hash,
                        },
                        None,
                        now_millis(),
                        nudge.session_id,
                    );
                    committed += 1;
                }
                Err(_) => continue,
            }
        }

        Ok(committed)
    }
}

// ── Helper hash functions ────────────────────────────────────────────────

fn candidate_list_domain_hash(candidates: &[MemoryCandidate]) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-memory-candidates-v1");
    hasher.update(&(candidates.len() as u32).to_le_bytes());
    for c in candidates {
        hasher.update(&c.content_hash);
    }
    *hasher.finalize().as_bytes()
}

fn nudge_domain_hash(
    nudge_id: u128,
    session_id: u128,
    candidate_list_hash: [u8; 32],
    timestamp: u64,
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-memory-nudge-v1");
    hasher.update(&nudge_id.to_le_bytes());
    hasher.update(&session_id.to_le_bytes());
    hasher.update(&candidate_list_hash);
    hasher.update(&timestamp.to_le_bytes());
    *hasher.finalize().as_bytes()
}

fn now_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::learning::LearningLedger;
    // CogniFoldStore + PhysicalWatchdog removed in Phase 1A cleanup — unused in mod tests (verified by clippy 2026-06-15).

    fn _ts(offset: u64) -> u64 {
        1_700_000_000_000 + offset
    }

    fn valid_candidate(label: &str, score: f32) -> MemoryCandidate {
        MemoryCandidate::new(format!("fact-{label}"), score, 1).unwrap()
    }

    #[test]
    fn test_memory_candidate_rejects_zero_session() {
        let c = MemoryCandidate::new("fact".to_string(), 0.8, 0);
        assert!(c.is_none());
    }

    #[test]
    fn test_memory_candidate_rejects_empty_content() {
        let c = MemoryCandidate::new(String::new(), 0.8, 1);
        assert!(c.is_none());
    }

    #[test]
    fn test_memory_candidate_rejects_out_of_range_score() {
        let c = MemoryCandidate::new("fact".to_string(), 1.5, 1);
        assert!(c.is_none());
    }

    #[test]
    fn test_memory_candidate_is_valid_roundtrips() {
        let c = MemoryCandidate::new("fact-a".to_string(), 0.9, 42).unwrap();
        assert!(c.is_valid());
        assert_ne!(c.content_hash, [0; 32]);
    }

    #[test]
    fn test_memory_candidate_hash_is_deterministic() {
        let c1 = MemoryCandidate::new("fact".to_string(), 0.8, 1).unwrap();
        let c2 = MemoryCandidate::new("fact".to_string(), 0.8, 1).unwrap();
        assert_eq!(c1.content_hash, c2.content_hash);
    }

    #[test]
    fn test_nudge_below_threshold_rejected() {
        let system = MemoryNudgeSystem::new(0.7);
        let mut ledger = LearningLedger::new();

        let raw = vec![valid_candidate("a", 0.3), valid_candidate("b", 0.5)];

        let result = system.periodic_nudge(1, 1, raw, &mut ledger, None);
        assert!(matches!(result, Err(MemoryError::EmptyCandidates)));
        assert!(ledger.is_empty());
    }

    #[test]
    fn test_nudge_above_threshold_succeeds() {
        let system = MemoryNudgeSystem::new(0.7);
        let mut ledger = LearningLedger::new();

        let raw = vec![
            valid_candidate("a", 0.3),
            valid_candidate("b", 0.85),
            valid_candidate("c", 0.95),
        ];

        let nudge = system.periodic_nudge(1, 1, raw, &mut ledger, None).unwrap();
        assert_eq!(nudge.candidates.len(), 2);
        assert!(nudge.is_valid());
        assert_eq!(ledger.len(), 1);
        assert!(ledger.verify_integrity());
    }

    #[test]
    fn test_nudge_hash_changes_with_different_candidates() {
        let system = MemoryNudgeSystem::new(0.0);

        let raw1 = vec![valid_candidate("a", 0.9)];
        let raw2 = vec![valid_candidate("b", 0.9)];

        let mut ledger = LearningLedger::new();
        let n1 = system
            .periodic_nudge(1, 1, raw1, &mut ledger, None)
            .unwrap();

        let mut ledger2 = LearningLedger::new();
        let n2 = system
            .periodic_nudge(1, 1, raw2, &mut ledger2, None)
            .unwrap();

        assert_ne!(n1.nudge_hash, n2.nudge_hash);
    }

    #[test]
    fn nudge_rejects_candidates_from_another_session() {
        let candidate = valid_candidate("wrong-session", 0.9);
        assert!(MemoryNudge::new(1, 2, vec![candidate], _ts(0)).is_none());
    }

    #[test]
    fn semantic_commit_does_not_require_physical_validation_fields() {
        let system = MemoryNudgeSystem::new(0.0);
        let mut ledger = LearningLedger::new();
        let nudge = system
            .periodic_nudge(
                7,
                42,
                vec![MemoryCandidate::new("fact-semantic".to_string(), 0.75, 42).unwrap()],
                &mut ledger,
                None,
            )
            .unwrap();
        let mut cognifold = CogniFoldStore::new();

        let committed = system
            .commit_nudged_memories_semantic_with_ledger(&nudge, &mut cognifold, &mut ledger)
            .unwrap();

        assert_eq!(committed, 1);
        assert_eq!(cognifold.latest().map(|frame| frame.fidelity), Some(0.75));
        assert_eq!(
            ledger
                .iter()
                .filter(|event| matches!(
                    event.event_type,
                    LearningEventType::MemoryProjectionCommitted { .. }
                ))
                .count(),
            1
        );
    }
}
