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

pub const MAX_TASK_TYPE_BYTES: usize = 128;
pub const MAX_USER_PREFERENCES: usize = 64;
pub const MAX_PREFERENCE_KEY_BYTES: usize = 64;
pub const MAX_PREFERENCE_VALUE_BYTES: usize = 512;
pub const MAX_INTERACTION_PATTERNS: usize = 256;

fn is_valid_preference_entry(key: &str, val: &str) -> bool {
    !key.is_empty()
        && key.len() <= MAX_PREFERENCE_KEY_BYTES
        && !key.chars().any(char::is_control)
        && !val.is_empty()
        && val.len() <= MAX_PREFERENCE_VALUE_BYTES
        && !val.chars().any(char::is_control)
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
            || task_type.len() > MAX_TASK_TYPE_BYTES
            || task_type.chars().any(char::is_control)
            || !success_rate.is_finite()
            || !(0.0..=1.0).contains(&success_rate)
            || avg_duration_ms == 0
        {
            return None;
        }
        let normalized_rate = if success_rate == 0.0 {
            0.0
        } else {
            success_rate
        };
        let pattern_hash =
            interaction_pattern_domain_hash(&task_type, normalized_rate, avg_duration_ms);
        Some(Self {
            task_type,
            success_rate: normalized_rate,
            avg_duration_ms,
            pattern_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        !self.task_type.is_empty()
            && self.task_type.len() <= MAX_TASK_TYPE_BYTES
            && !self.task_type.chars().any(char::is_control)
            && self.success_rate.is_finite()
            && !self.success_rate.is_sign_negative()
            && (0.0..=1.0).contains(&self.success_rate)
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
            && self.preferences.len() <= MAX_USER_PREFERENCES
            && self
                .preferences
                .iter()
                .all(|(k, v)| is_valid_preference_entry(k, v))
            && self.interaction_patterns.len() <= MAX_INTERACTION_PATTERNS
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

    /// Compute an exponentially-weighted moving average (EMA) of `(success_rate, avg_duration_ms, sample_count)`
    /// for a given `task_type` so recent user interactions adapt faster than stale early-session trials.
    pub fn adaptive_task_summary(&self, task_type: &str) -> Option<(f32, u64, usize)> {
        const EMA_ALPHA: f32 = 0.35;
        let mut ema_success = 0.0_f32;
        let mut ema_duration = 0.0_f64;
        let mut count = 0_usize;
        for pattern in self
            .interaction_patterns
            .iter()
            .filter(|p| p.task_type == task_type && p.is_valid())
        {
            if count == 0 {
                ema_success = pattern.success_rate;
                ema_duration = pattern.avg_duration_ms as f64;
            } else {
                ema_success = EMA_ALPHA * pattern.success_rate + (1.0 - EMA_ALPHA) * ema_success;
                ema_duration = (EMA_ALPHA as f64) * (pattern.avg_duration_ms as f64)
                    + (1.0 - EMA_ALPHA as f64) * ema_duration;
            }
            count += 1;
        }
        if count == 0 {
            None
        } else {
            Some((
                ema_success.clamp(0.0, 1.0),
                ema_duration.round().max(1.0) as u64,
                count,
            ))
        }
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
        if user_id == 0 {
            return Err(UserModelError::InvalidUserModel);
        }
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

        let timestamp = now_millis().max(1);

        // Get or create user model
        let model = self.models.entry(user_id).or_insert_with(|| {
            UserModel::new(user_id, trust_level, timestamp).expect("valid user model")
        });

        // Bounded window eviction so interaction_patterns cannot grow without bound
        if model.interaction_patterns.len() >= MAX_INTERACTION_PATTERNS {
            let overflow = model.interaction_patterns.len() + 1 - MAX_INTERACTION_PATTERNS;
            model.interaction_patterns.drain(0..overflow);
        }
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

    /// Record or update a bounded user preference under the same TrustLevel and ledger gates.
    pub fn set_preference(
        &mut self,
        user_id: u128,
        key: String,
        value: String,
        trust_level: TrustLevel,
        learning_ledger: &mut LearningLedger,
        license_manager: Option<&LicenseManager>,
    ) -> Result<&UserModel, UserModelError> {
        if user_id == 0 {
            return Err(UserModelError::InvalidUserModel);
        }
        match trust_level {
            TrustLevel::Prod => return Err(UserModelError::ApprovalRequired),
            TrustLevel::Staging => return Err(UserModelError::ReviewRequired),
            TrustLevel::Dev => {}
        }
        if let Some(lm) = license_manager {
            lm.license_gate(LicenseFeature::AdvancedUserModeling)
                .map_err(UserModelError::LicenseRequired)?;
        }
        if !is_valid_preference_entry(&key, &value) {
            return Err(UserModelError::InvalidUserModel);
        }
        let timestamp = now_millis().max(1);
        let model = self.models.entry(user_id).or_insert_with(|| {
            UserModel::new(user_id, trust_level, timestamp).expect("valid user model")
        });
        if !model.preferences.contains_key(&key) && model.preferences.len() >= MAX_USER_PREFERENCES
        {
            return Err(UserModelError::InvalidUserModel);
        }
        model.preferences.insert(key, value);
        model.last_updated = timestamp;
        model.trust_level_snapshot = trust_level;
        model.model_hash = model.compute_hash();

        learning_ledger.append(
            LearningEventType::UserModelUpdated {
                model_hash: model.model_hash,
            },
            None,
            timestamp,
            1,
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
    let normalized_rate = if success_rate == 0.0 {
        0.0
    } else {
        success_rate
    };
    let mut hasher = domain_hasher(b"aegis-interaction-pattern-v1");
    hasher.update(task_type.as_bytes());
    hasher.update(&normalized_rate.to_le_bytes());
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

    // Deterministic ordering via BTreeMap with explicit length framing per key/value
    hasher.update(&(preferences.len() as u32).to_le_bytes());
    for (key, val) in preferences {
        hasher.update(&(key.len() as u32).to_le_bytes());
        hasher.update(key.as_bytes());
        hasher.update(&(val.len() as u32).to_le_bytes());
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
    fn test_interaction_pattern_is_valid_rejects_out_of_range_and_canonicalizes_negative_zero() {
        let mut p = InteractionPattern::new("research".to_string(), 0.95, 5000).unwrap();
        p.success_rate = 1.5;
        assert!(!p.is_valid());

        let pos_zero = InteractionPattern::new("research".to_string(), 0.0, 5000).unwrap();
        let neg_zero = InteractionPattern::new("research".to_string(), -0.0, 5000).unwrap();
        assert_eq!(pos_zero.pattern_hash, neg_zero.pattern_hash);
        assert!(neg_zero.is_valid());
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
    fn test_user_model_preference_length_framing_prevents_hash_collision() {
        let mut m1 = UserModel::new(1, TrustLevel::Dev, _ts(1)).unwrap();
        m1.preferences.insert("a".to_string(), "bc".to_string());
        m1.model_hash = m1.compute_hash();

        let mut m2 = UserModel::new(1, TrustLevel::Dev, _ts(1)).unwrap();
        m2.preferences.insert("ab".to_string(), "c".to_string());
        m2.model_hash = m2.compute_hash();

        assert!(m1.is_valid());
        assert!(m2.is_valid());
        assert_ne!(m1.model_hash, m2.model_hash);
    }

    #[test]
    fn test_user_model_set_preference_and_adaptive_summary() {
        let mut store = UserModelStore::new();
        let mut ledger = LearningLedger::new();

        assert_eq!(
            store.set_preference(
                1,
                "verbosity".to_string(),
                "concise".to_string(),
                TrustLevel::Prod,
                &mut ledger,
                None,
            ),
            Err(UserModelError::ApprovalRequired)
        );

        store
            .set_preference(
                1,
                "verbosity".to_string(),
                "concise".to_string(),
                TrustLevel::Dev,
                &mut ledger,
                None,
            )
            .unwrap();
        store
            .update_user_model(
                1,
                "code-review".to_string(),
                0.5,
                2000,
                TrustLevel::Dev,
                &mut ledger,
                None,
            )
            .unwrap();
        let model = store
            .update_user_model(
                1,
                "code-review".to_string(),
                1.0,
                1000,
                TrustLevel::Dev,
                &mut ledger,
                None,
            )
            .unwrap();

        assert_eq!(
            model.preferences.get("verbosity").map(String::as_str),
            Some("concise")
        );
        let (ema_rate, ema_duration, count) = model.adaptive_task_summary("code-review").unwrap();
        assert_eq!(count, 2);
        assert!(ema_rate > 0.6 && ema_rate < 0.75);
        assert!(ema_duration < 2000 && ema_duration > 1000);
        assert!(model.is_valid());
        assert!(ledger.verify_integrity());
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

    #[test]
    fn test_negative_zero_and_corrupted_pattern_rejected_in_ema() {
        let mut p = InteractionPattern::new("code-review".to_string(), 0.0, 100).unwrap();
        assert!(p.is_valid());
        p.success_rate = -0.0;
        assert!(!p.is_valid(), "-0.0 must be rejected by is_valid");

        let mut model = UserModel::new(1, TrustLevel::Dev, 100).unwrap();
        let valid_pattern = InteractionPattern::new("code-review".to_string(), 0.8, 250).unwrap();
        let mut nan_pattern = valid_pattern.clone();
        nan_pattern.success_rate = f32::NAN;
        model.interaction_patterns.push(valid_pattern);
        model.interaction_patterns.push(nan_pattern);

        let (ema_rate, ema_dur, count) = model.adaptive_task_summary("code-review").unwrap();
        assert!((ema_rate - 0.8).abs() < 1e-5);
        assert_eq!(ema_dur, 250);
        assert_eq!(count, 1);
    }
}
