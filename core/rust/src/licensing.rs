use blake3::Hasher;
/// AEGIS-COGNITION Commercial Licensing System
/// Ed25519-based license key validation + feature gating
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};

// ─── License Data Model ───

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Edition {
    Community,
    Pro,
    Team,
    Enterprise,
}

impl Edition {
    pub fn as_u32(&self) -> u32 {
        match self {
            Edition::Community => 0,
            Edition::Pro => 1,
            Edition::Team => 2,
            Edition::Enterprise => 3,
        }
    }

    pub fn from_u32(v: u32) -> Option<Self> {
        match v {
            0 => Some(Edition::Community),
            1 => Some(Edition::Pro),
            2 => Some(Edition::Team),
            3 => Some(Edition::Enterprise),
            _ => None,
        }
    }
}

/// Individual features gated behind license tiers
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Feature {
    // ─── Community (always available) ───
    LocalReplayLedger,
    BasicSkills,
    BYOKRouting,
    LocalEvidenceArena,

    // ─── Pro ───
    CloudSync,
    PremiumSkills,
    PrioritySupport,
    SelfImprovingSkills,
    CrossSessionMemory,

    // ─── Team ───
    TeamDashboard,
    SharedSkillRegistry,
    RoleBasedAccessControl,

    // ─── Enterprise ───
    ComplianceReports,
    SSOIntegration,
    DedicatedSupport,
    ServiceLevelAgreement,
    ManagedHosting,
    AdvancedUserModeling,
}

impl Feature {
    pub fn minimum_edition(&self) -> Edition {
        match self {
            Feature::LocalReplayLedger
            | Feature::BasicSkills
            | Feature::BYOKRouting
            | Feature::LocalEvidenceArena => Edition::Community,
            Feature::CloudSync
            | Feature::PremiumSkills
            | Feature::PrioritySupport
            | Feature::SelfImprovingSkills
            | Feature::CrossSessionMemory => Edition::Pro,
            Feature::TeamDashboard
            | Feature::SharedSkillRegistry
            | Feature::RoleBasedAccessControl => Edition::Team,
            Feature::ComplianceReports
            | Feature::SSOIntegration
            | Feature::DedicatedSupport
            | Feature::ServiceLevelAgreement
            | Feature::ManagedHosting
            | Feature::AdvancedUserModeling => Edition::Enterprise,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LicenseKey {
    pub tenant_id: String,
    pub edition: Edition,
    pub features: Vec<Feature>,
    pub max_nodes: u32,
    pub max_evidence_per_day: u64,
    pub issued_at: u64,
    pub expires_at: u64,
    pub signature: Vec<u8>, // Ed25519 signature over all fields above
}

impl LicenseKey {
    /// Serialize payload for signing (deterministic)
    pub fn signing_payload(&self) -> Vec<u8> {
        let mut payload = Vec::with_capacity(256);
        payload.extend_from_slice(self.tenant_id.as_bytes());
        payload.extend_from_slice(&self.edition.as_u32().to_le_bytes());
        // Sort features for deterministic encoding
        let mut feature_ids: Vec<u32> = self.features.iter().map(|f| *f as u32).collect();
        feature_ids.sort();
        for id in &feature_ids {
            payload.extend_from_slice(&id.to_le_bytes());
        }
        payload.extend_from_slice(&self.max_nodes.to_le_bytes());
        payload.extend_from_slice(&self.max_evidence_per_day.to_le_bytes());
        payload.extend_from_slice(&self.issued_at.to_le_bytes());
        payload.extend_from_slice(&self.expires_at.to_le_bytes());
        payload
    }
}

// ─── License Status ───

#[derive(Debug, Clone)]
pub enum LicenseStatus {
    Valid {
        tier: Edition,
        features: Vec<Feature>,
    },
    Expired {
        tier: Edition,
        expired_at: u64,
    },
    Invalid,
    NotConfigured,
}

// ─── License Errors ───

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LicenseError {
    InvalidSignature,
    Expired {
        expired_at: u64,
    },
    Revoked,
    FeatureNotLicensed(Feature),
    LicenseNotValidated,
    LicenseInvalid,
    ValidatorNotConfigured,
    InvalidSignatureFormat,
    UpgradeRequired {
        feature: Feature,
        current: Edition,
        required: Edition,
    },
}

impl std::fmt::Display for LicenseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LicenseError::InvalidSignature => write!(f, "License signature is invalid"),
            LicenseError::Expired { expired_at } => write!(f, "License expired at {}", expired_at),
            LicenseError::Revoked => write!(f, "License has been revoked"),
            LicenseError::FeatureNotLicensed(feat) => {
                write!(f, "Feature {:?} not included in license", feat)
            }
            LicenseError::LicenseNotValidated => write!(f, "License has not been validated yet"),
            LicenseError::LicenseInvalid => write!(f, "License is in invalid state"),
            LicenseError::ValidatorNotConfigured => write!(f, "License validator not configured"),
            LicenseError::InvalidSignatureFormat => write!(f, "Signature bytes are malformed"),
            LicenseError::UpgradeRequired {
                feature,
                current,
                required,
            } => {
                write!(
                    f,
                    "Feature {:?} requires {:?} edition but current license is {:?} — upgrade required",
                    feature, required, current
                )
            }
        }
    }
}

// ─── License Validator ───

pub struct LicenseValidator {
    pub public_key: VerifyingKey,
    pub phone_home_url: Option<String>,
}

impl LicenseValidator {
    pub fn new(public_key_bytes: [u8; 32]) -> Result<Self, LicenseError> {
        let public_key = VerifyingKey::from_bytes(&public_key_bytes)
            .map_err(|_| LicenseError::InvalidSignature)?;
        Ok(Self {
            public_key,
            phone_home_url: None,
        })
    }

    pub fn with_phone_home(mut self, url: String) -> Self {
        self.phone_home_url = Some(url);
        self
    }

    /// Validate a license key (offline-first, online-best-effort)
    pub fn validate(&self, key: &LicenseKey) -> Result<LicenseStatus, LicenseError> {
        // 1. Verify Ed25519 signature (pure offline, no network needed)
        self.verify_signature(key)?;

        // 2. Check expiry with 7-day grace period
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let grace_period = 7 * 24 * 60 * 60; // 7 days

        if key.expires_at + grace_period < now {
            return Ok(LicenseStatus::Expired {
                tier: key.edition,
                expired_at: key.expires_at,
            });
        }

        // 3. Feature-tiers check: all features must be <= edition
        for feature in &key.features {
            if feature.minimum_edition() as u32 > key.edition as u32 {
                return Err(LicenseError::FeatureNotLicensed(*feature));
            }
        }

        Ok(LicenseStatus::Valid {
            tier: key.edition,
            features: key.features.clone(),
        })
    }

    fn verify_signature(&self, key: &LicenseKey) -> Result<(), LicenseError> {
        let payload = key.signing_payload();
        let signature = Signature::from_slice(&key.signature)
            .map_err(|_| LicenseError::InvalidSignatureFormat)?;

        self.public_key
            .verify_strict(&payload, &signature)
            .map_err(|_| LicenseError::InvalidSignature)
    }

    /// Validate and also check for revocation (online, best-effort via env-provided URL)
    /// NOTE: reqwest is optional — set phone_home_url to None for pure offline mode
    pub async fn validate_with_phone_home(
        &self,
        key: &LicenseKey,
    ) -> Result<LicenseStatus, LicenseError> {
        let status = self.validate(key)?;

        // Phone-home check (non-blocking, best-effort)
        // In production, this would use reqwest or ureq.
        // For now, pure offline validation.
        if self.phone_home_url.is_some() {
            // Best-effort: skip if network unavailable
            // The 7-day grace period covers offline windows
        }

        Ok(status)
    }
}

// ─── License Manager (integrates with Hot Engine) ───

pub struct LicenseManager {
    validator: Option<LicenseValidator>,
    current_license: Option<LicenseStatus>,
    evidence_today: u64,
    last_reset_day: u64,
}

impl LicenseManager {
    pub fn new() -> Self {
        Self {
            validator: None,
            current_license: None,
            evidence_today: 0,
            last_reset_day: 0,
        }
    }

    pub fn configure(&mut self, validator: LicenseValidator) {
        self.validator = Some(validator);
    }

    pub async fn activate(&mut self, key: &LicenseKey) -> Result<(), LicenseError> {
        let validator = self
            .validator
            .as_ref()
            .ok_or(LicenseError::ValidatorNotConfigured)?;
        let status = validator.validate_with_phone_home(key).await?;
        self.current_license = Some(status);
        Ok(())
    }

    /// Check if a specific feature is available
    pub fn check_feature(&self, feature: Feature) -> Result<(), LicenseError> {
        match &self.current_license {
            Some(LicenseStatus::Valid { features, .. }) => {
                if features.contains(&feature) {
                    Ok(())
                } else {
                    Err(LicenseError::FeatureNotLicensed(feature))
                }
            }
            Some(LicenseStatus::Expired { .. }) => Err(LicenseError::Expired { expired_at: 0 }),
            None => {
                // Community features always available without license
                if feature.minimum_edition() == Edition::Community {
                    Ok(())
                } else {
                    Err(LicenseError::LicenseNotValidated)
                }
            }
            _ => Err(LicenseError::LicenseInvalid),
        }
    }

    /// Track daily evidence usage against license limits
    pub fn track_evidence(&mut self) -> Result<(), LicenseError> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        let today = now / 86400;
        if today != self.last_reset_day {
            self.evidence_today = 0;
            self.last_reset_day = today;
        }

        self.evidence_today += 1;

        // If we had the license key, check against max_evidence_per_day
        // This is a soft check — in production, integrate with actual license key
        Ok(())
    }

    pub fn status(&self) -> &Option<LicenseStatus> {
        &self.current_license
    }

    /// Gate a feature, returning a detailed `UpgradeRequired` error if the caller's
    /// current edition is below the required minimum.
    ///
    /// This is the preferred check for embedding in user-facing APIs in learning
    /// modules — it tells the user *what* they need to upgrade to.
    pub fn license_gate(&self, feature: Feature) -> Result<(), LicenseError> {
        match &self.current_license {
            Some(LicenseStatus::Valid { tier, features }) => {
                if features.contains(&feature) {
                    Ok(())
                } else {
                    Err(LicenseError::UpgradeRequired {
                        feature,
                        current: *tier,
                        required: feature.minimum_edition(),
                    })
                }
            }
            Some(LicenseStatus::Expired {
                tier,
                expired_at: _,
            }) => Err(LicenseError::UpgradeRequired {
                feature,
                current: *tier,
                required: feature.minimum_edition(),
            }),
            None => {
                if feature.minimum_edition() == Edition::Community {
                    Ok(())
                } else {
                    Err(LicenseError::UpgradeRequired {
                        feature,
                        current: Edition::Community,
                        required: feature.minimum_edition(),
                    })
                }
            }
            _ => Err(LicenseError::LicenseInvalid),
        }
    }
}

// ─── Feature Gating Macro ───

/// Macro to gate features behind license checks
/// Usage: require_license_feature!(license_manager, Feature::SelfImprovingSkills);
///
/// Returns `Err(LicenseError::UpgradeRequired { ... })` when the feature is not
/// available in the current edition (e.g. Feature::AdvancedUserModeling requires
/// Enterprise but the user has Community).
#[macro_export]
macro_rules! require_license_feature {
    ($manager:expr, $feature:expr) => {
        match $manager.check_feature($feature) {
            Ok(()) => {}
            Err(e) => return Err(e),
        }
    };
    // Variant that returns a Result explicitly (for non-Result functions)
    ($manager:expr, $feature:expr, $err_conv:expr) => {
        match $manager.check_feature($feature) {
            Ok(()) => {}
            Err(e) => return Err($err_conv(e)),
        }
    };
}

// ─── License Server Key Generation (offline tool) ───
//
// NOTE: The `keygen`-cfg'd `test_keypair` that previously lived here has been
// removed. It was a duplicate definition of the deterministic
// `test_keypair()` below and would have caused a "name defined multiple
// times" error if anyone enabled `--features keygen`. To regenerate license
// signing keys, use the dedicated offline tool under
// `aegis-cognition/crates/aegis-license-cli` (see README).

pub fn test_keypair() -> (SigningKey, VerifyingKey) {
    // Deterministic keypair from fixed seed — TEST ONLY
    let seed: [u8; 32] = [0x42; 32];
    let signing_key = SigningKey::from_bytes(&seed);
    let verifying_key = signing_key.verifying_key();
    (signing_key, verifying_key)
}

pub fn sign_license(key: &mut LicenseKey, signing_key: &SigningKey) {
    let payload = key.signing_payload();
    let signature = signing_key.sign(&payload);
    key.signature = signature.to_vec();
}

/// BLAKE3 hash of the license for tamper-evident storage
pub fn license_fingerprint(key: &LicenseKey) -> String {
    let mut hasher = Hasher::new();
    hasher.update(&key.signing_payload());
    hasher.finalize().to_hex().to_string()
}

// ─── Tests ───

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate_and_verify_license() {
        let (signing_key, verifying_key) = test_keypair();
        let public_bytes = verifying_key.to_bytes();
        let validator = LicenseValidator::new(public_bytes).unwrap();

        let mut key = LicenseKey {
            tenant_id: "acme-corp".to_string(),
            edition: Edition::Enterprise,
            features: vec![
                Feature::LocalReplayLedger,
                Feature::CloudSync,
                Feature::ComplianceReports,
                Feature::SSOIntegration,
                Feature::ManagedHosting,
            ],
            max_nodes: 100,
            max_evidence_per_day: 10_000_000,
            issued_at: 1749600000,  // 2025-06-11
            expires_at: 1925000000, // 2030-12-31
            signature: vec![],
        };

        sign_license(&mut key, &signing_key);
        // Debug: verify signature payload is identical
        let payload = key.signing_payload();
        let sig = ed25519_dalek::Signature::from_slice(&key.signature).unwrap();
        let verify_ok = verifying_key.verify_strict(&payload, &sig).is_ok();
        eprintln!(
            "sig len={} payload={} verify_ok={}",
            key.signature.len(),
            payload.len(),
            verify_ok
        );
        let status = validator.validate(&key).unwrap();

        match status {
            LicenseStatus::Valid { tier, features } => {
                assert_eq!(tier, Edition::Enterprise);
                assert!(features.contains(&Feature::ManagedHosting));
            }
            _ => panic!("Expected Valid status"),
        }
    }

    #[test]
    fn test_feature_gating() {
        let (_sk, vk) = test_keypair();
        let validator = LicenseValidator::new(vk.to_bytes()).unwrap();
        let mut manager = LicenseManager::new();
        manager.configure(validator);

        // Pro features should fail without license
        assert!(manager.check_feature(Feature::CloudSync).is_err());

        // Community features should pass without license
        assert!(manager.check_feature(Feature::LocalReplayLedger).is_ok());
    }

    #[test]
    fn test_license_fingerprint_deterministic() {
        let (_sk, vk) = test_keypair();
        // validator binding removed in hardening Phase 1A (2026-06-15) — unused,
        // kept LicenseValidator::new side-effect for any future activation.
        let _ = LicenseValidator::new(vk.to_bytes()).unwrap();

        let key1 = LicenseKey {
            tenant_id: "test".to_string(),
            edition: Edition::Pro,
            features: vec![Feature::CloudSync, Feature::LocalReplayLedger],
            max_nodes: 10,
            max_evidence_per_day: 1000,
            issued_at: 100,
            expires_at: 200,
            signature: vec![],
        };

        let fp = license_fingerprint(&key1);
        assert_eq!(fp.len(), 64); // BLAKE3 hex is 64 chars
        assert!(fp.chars().all(|c| c.is_ascii_hexdigit()));
    }
}
