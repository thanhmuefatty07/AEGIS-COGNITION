use crate::hot_engine::TrustLevel;
use crate::learning::{LearningEventType, LearningLedger};
use crate::licensing::{Feature as LicenseFeature, LicenseError, LicenseManager};
use blake3::Hasher;
use std::collections::BTreeMap;

/// Domain-tagged BLAKE3 hasher.
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
pub enum UserModelError {
    ApprovalRequired,
    ReviewRequired,
    InvalidInteraction,
    InvalidUserModel,
    LicenseRequired(LicenseError),
}

// ── InteractionPattern ───────────────────────────────────────────────────

/// A single observed interaction pattern extracted during a session.
#[derive(Clone, Debug, PartialEq)]
pub struct InteractionPattern {
    pub task_type: String,
    pub success_rate: f32,
    pub avg_duration_ms: u64,
    pub pattern_hash: [u8; 32],
}

impl InteractionPattern {
    pub fn new(task_type: String, success_rate: f32, avg_duration_ms: u64) -> Option<Self> {
        if task_type.is_empty()
            || !success_rate.is_finite()
            || success_rate < 0.0
            || success_rate > 1.0
            || avg_duration_ms == 0
        {
            return None;
        }
        let pattern_hash =
            interaction_pattern_domain_hash(&task_type, success_rate, avg_duration_ms);
        Some(Self {
            task_type,
            success_rate,
            avg_duration_ms,
            pattern_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        !self.task_type.is_empty()
            && self.success_rate.is_finite()
            && self.success_rate >= 0.0
            && self.avg_duration_ms > 0
            && nonzero_hash(&self.pattern_hash)
            && self.pattern_hash
                == interaction_pattern_domain_hash(
                    &self.task_type,
                    self.success_rate,
                    self.avg_duration_ms,
                )
    }
}

// ── UserModel ────────────────────────────────────────────────────────────

/// A user's accumulated interaction profile.
/// Stored in CogniFold with TrustLevel-gated updates.
#[derive(Clone, Debug, PartialEq)]
pub struct UserModel {
    pub user_id: u128,
    pub preferences: BTreeMap<String, String>,
    pub interaction_patterns: Vec<InteractionPattern>,
    pub model_hash: [u8; 32],
    pub last_updated: u64,
    pub trust_level_snapshot: TrustLevel,
}

impl UserModel {
    pub fn new(user_id: u128, trust_level: TrustLevel, timestamp: u64) -> Option<Self> {
        if user_id == 0 || timestamp == 0 {
            return None;
        }
        let empty_patterns: Vec<InteractionPattern> = Vec::new();
        let empty_prefs: BTreeMap<String, String> = BTreeMap::new();
        let model_hash = user_model_domain_hash(
            user_id,
            &empty_prefs,
            &empty_patterns,
            timestamp,
            trust_level,
        );
        Some(Self {
            user_id,
            preferences: empty_prefs,
            interaction_patterns: empty_patterns,
            model_hash,
            last_updated: timestamp,
            trust_level_snapshot: trust_level,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.user_id > 0
            && self
                .interaction_patterns
                .iter()
                .all(InteractionPattern::is_valid)
            && self.last_updated > 0
            && nonzero_hash(&self.model_hash)
            && self.model_hash
                == user_model_domain_hash(
                    self.user_id,
                    &self.preferences,
                    &self.interaction_patterns,
                    self.last_updated,
                    self.trust_level_snapshot,
                )
    }

    fn compute_hash(&self) -> [u8; 32] {
        user_model_domain_hash(
            self.user_id,
            &self.preferences,
            &self.interaction_patterns,
            self.last_updated,
            self.trust_level_snapshot,
        )
    }
}

// ── UserModelStore ───────────────────────────────────────────────────────

/// Mutable store for user models with TrustLevel enforcement.
///
/// TrustLevel semantics:
/// - **DEV**: auto-update on every interaction — learning is unrestricted.
/// - **STAGING**: flags the model for review; the caller must handle the
///   `Err(UserModelError::ReviewRequired)` and route to a review pipeline.
/// - **PROD**: hard-closed — any update attempt returns
///   `Err(UserModelError::ApprovalRequired)`.
///
/// Every successful update appends `LearningEventType::UserModelUpdated`
/// to the learning audit trail.
pub struct UserModelStore {
    models: BTreeMap<u128, UserModel>,
}

impl UserModelStore {
    pub fn new() -> Self {
        Self {
            models: BTreeMap::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.models.len()
    }

    pub fn is_empty(&self) -> bool {
        self.models.is_empty()
    }

    pub fn get(&self, user_id: u128) -> Option<&UserModel> {
        self.models.get(&user_id)
    }

    /// Update or create a user model based on a new interaction.
    ///
    /// # Errors
    /// - `TrustLevel::Prod` → `ApprovalRequired` (always fail-closed)
    /// - `TrustLevel::Staging` → `ReviewRequired` (caller must review)
    /// - `TrustLevel::Dev` → proceeds with auto-update
    pub fn update_user_model(
        &mut self,
        user_id: u128,
        task_type: String,
        success_rate: f32,
        avg_duration_ms: u64,
        trust_level: TrustLevel,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<&UserModel, UserModelError> {
        // ── Trust Level Gate ─────────────────────────────────────────
        match trust_level {
            TrustLevel::Prod => return Err(UserModelError::ApprovalRequired),
            TrustLevel::Staging => return Err(UserModelError::ReviewRequired),
            TrustLevel::Dev => { /* auto-update — continue */ }
        }

        // ── Commercial Gate: AdvancedUserModeling requires Enterprise ──
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::AdvancedUserModeling)
                .map_err(UserModelError::LicenseRequired)?;
        }

        // Extract interaction pattern
        let pattern = InteractionPattern::new(task_type, success_rate, avg_duration_ms)
            .ok_or(UserModelError::InvalidInteraction)?;

        let timestamp = now_millis();

        // Get or create user model
        let model = self.models.entry(user_id).or_insert_with(|| {
            UserModel::new(user_id, trust_level, timestamp).expect("valid user model")
        });

        // Update
        model.interaction_patterns.push(pattern);
        model.last_updated = timestamp;
        model.trust_level_snapshot = trust_level;
        model.model_hash = model.compute_hash();

        // Append to learning ledger
        learning_ledger.append(
            LearningEventType::UserModelUpdated {
                model_hash: model.model_hash,
            },
            None,
            timestamp,
            1, // session_id: autonomous background
        );

        Ok(model)
    }
}

// ── Hash helpers ─────────────────────────────────────────────────────────

fn interaction_pattern_domain_hash(
    task_type: &str,
    success_rate: f32,
    avg_duration_ms: u64,
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-interaction-pattern-v1");
    hasher.update(task_type.as_bytes());
    hasher.update(&success_rate.to_le_bytes());
    hasher.update(&avg_duration_ms.to_le_bytes());
    *hasher.finalize().as_bytes()
}

fn user_model_domain_hash(
    user_id: u128,
    preferences: &BTreeMap<String, String>,
    patterns: &[InteractionPattern],
    last_updated: u64,
    trust_level: TrustLevel,
) -> [u8; 32] {
    let mut hasher = domain_hasher(b"aegis-user-model-v1");
    hasher.update(&user_id.to_le_bytes());

    // Deterministic ordering via BTreeMap
    hasher.update(&(preferences.len() as u32).to_le_bytes());
    for (key, val) in preferences {
        hasher.update(key.as_bytes());
        hasher.update(val.as_bytes());
    }

    hasher.update(&(patterns.len() as u32).to_le_bytes());
    for pattern in patterns {
        hasher.update(&pattern.pattern_hash);
    }

    hasher.update(&last_updated.to_le_bytes());
    hasher.update(&[trust_level as u8]);
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
    use crate::hot_engine::TrustLevel;
    use crate::learning::LearningLedger;

    fn _ts(offset: u64) -> u64 {
        1_700_000_000_000 + offset
    }

    #[test]
    fn test_interaction_pattern_rejects_empty_task() {
        let p = InteractionPattern::new(String::new(), 0.8, 1000);
        assert!(p.is_none());
    }

    #[test]
    fn test_interaction_pattern_rejects_zero_duration() {
        let p = InteractionPattern::new("task".to_string(), 0.8, 0);
        assert!(p.is_none());
    }

    #[test]
    fn test_interaction_pattern_valid_roundtrip() {
        let p = InteractionPattern::new("research".to_string(), 0.95, 5000).unwrap();
        assert!(p.is_valid());
        assert_ne!(p.pattern_hash, [0; 32]);
    }

    #[test]
    fn test_interaction_pattern_hash_deterministic() {
        let p1 = InteractionPattern::new("task".to_string(), 0.8, 1000).unwrap();
        let p2 = InteractionPattern::new("task".to_string(), 0.8, 1000).unwrap();
        assert_eq!(p1.pattern_hash, p2.pattern_hash);
    }

    #[test]
    fn test_interaction_pattern_hash_differs_per_type() {
        let p1 = InteractionPattern::new("task-a".to_string(), 0.8, 1000).unwrap();
        let p2 = InteractionPattern::new("task-b".to_string(), 0.8, 1000).unwrap();
        assert_ne!(p1.pattern_hash, p2.pattern_hash);
    }

    #[test]
    fn test_user_model_prod_requires_approval() {
        let mut store = UserModelStore::new();
        let mut ledger = LearningLedger::new();

        let result = store.update_user_model(
            1,
            "research".to_string(),
            0.9,
            5000,
            TrustLevel::Prod,
            &mut ledger,
            None,
        );

        assert_eq!(result, Err(UserModelError::ApprovalRequired));
        assert!(store.is_empty());
        assert!(ledger.is_empty());
    }

    #[test]
    fn test_user_model_staging_requires_review() {
        let mut store = UserModelStore::new();
        let mut ledger = LearningLedger::new();

        let result = store.update_user_model(
            1,
            "research".to_string(),
            0.9,
            5000,
            TrustLevel::Staging,
            &mut ledger,
            None,
        );

        assert_eq!(result, Err(UserModelError::ReviewRequired));
        assert!(store.is_empty());
        assert!(ledger.is_empty());
    }

    #[test]
    fn test_user_model_dev_auto_updates() {
        let mut store = UserModelStore::new();
        let mut ledger = LearningLedger::new();

        let model = store
            .update_user_model(
                1,
                "research".to_string(),
                0.9,
                5000,
                TrustLevel::Dev,
                &mut ledger,
                None,
            )
            .unwrap();

        assert_eq!(model.user_id, 1);
        assert_eq!(model.interaction_patterns.len(), 1);
        assert_eq!(model.trust_level_snapshot, TrustLevel::Dev);
        assert!(model.is_valid());
        assert_ne!(model.model_hash, [0; 32]);
        assert_eq!(ledger.len(), 1);
        assert!(ledger.verify_integrity());
    }

    #[test]
    fn test_user_model_accumulates_patterns() {
        let mut store = UserModelStore::new();
        let mut ledger = LearningLedger::new();

        store
            .update_user_model(
                1,
                "task-a".to_string(),
                0.9,
                1000,
                TrustLevel::Dev,
                &mut ledger,
                None,
            )
            .unwrap();

        store
            .update_user_model(
                1,
                "task-b".to_string(),
                0.8,
                2000,
                TrustLevel::Dev,
                &mut ledger,
                None,
            )
            .unwrap();

        let model = store.get(1).unwrap();
        assert_eq!(model.interaction_patterns.len(), 2);
        assert_eq!(ledger.len(), 2);
        assert!(ledger.verify_integrity());

        // Model hash must have changed between first and second update
        let mut single_ledger = LearningLedger::new();
        let mut single_store = UserModelStore::new();
        let single_model = single_store
            .update_user_model(
                1,
                "task-a".to_string(),
                0.9,
                1000,
                TrustLevel::Dev,
                &mut single_ledger,
                None,
            )
            .unwrap();

        // After only one pattern, the model hash should differ from the
        // two-pattern model (different pattern count → different hash)
        assert_ne!(single_model.model_hash, model.model_hash);
    }
}
