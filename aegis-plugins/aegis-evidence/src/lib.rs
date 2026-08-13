use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum EvidenceError {
    #[error("invalid evidence binding")]
    InvalidBinding,
    #[error("audit chain mismatch")]
    AuditChainMismatch,
    #[error("evidence not found")]
    NotFound,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EvidenceLifecycleState {
    Created,
    Bound,
    Accessed,
    Archived,
    Deleted,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EvidenceBinding {
    pub evidence_ref_hash: [u8; 32],
    pub artifact_hash: [u8; 32],
    pub ast_signature_hash: [u8; 32],
    pub timestamp_ms: u64,
    pub binding_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AuditRecord {
    pub sequence: u64,
    pub evidence_ref_hash: [u8; 32],
    pub operation: String,
    pub state: EvidenceLifecycleState,
    pub previous_hash: [u8; 32],
    pub record_hash: [u8; 32],
}

#[derive(Clone, Debug, Default)]
pub struct EvidenceStore {
    bindings: BTreeMap<[u8; 32], EvidenceBinding>,
    audit: Vec<AuditRecord>,
}

impl EvidenceBinding {
    pub fn new(
        evidence_ref_hash: [u8; 32],
        artifact_hash: [u8; 32],
        ast_signature_hash: [u8; 32],
        timestamp_ms: u64,
    ) -> Result<Self, EvidenceError> {
        if !nonzero(&evidence_ref_hash) || !nonzero(&artifact_hash) || !nonzero(&ast_signature_hash)
        {
            return Err(EvidenceError::InvalidBinding);
        }
        let mut binding = Self {
            evidence_ref_hash,
            artifact_hash,
            ast_signature_hash,
            timestamp_ms,
            binding_hash: [0; 32],
        };
        binding.binding_hash = binding.compute_hash();
        Ok(binding)
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"aegis-evidence-binding-v1");
        hasher.update(&self.evidence_ref_hash);
        hasher.update(&self.artifact_hash);
        hasher.update(&self.ast_signature_hash);
        hasher.update(&self.timestamp_ms.to_le_bytes());
        *hasher.finalize().as_bytes()
    }

    pub fn verify(&self) -> bool {
        nonzero(&self.binding_hash) && self.binding_hash == self.compute_hash()
    }
}

impl AuditRecord {
    pub fn new(
        sequence: u64,
        evidence_ref_hash: [u8; 32],
        operation: impl Into<String>,
        state: EvidenceLifecycleState,
        previous_hash: [u8; 32],
    ) -> Self {
        let mut record = Self {
            sequence,
            evidence_ref_hash,
            operation: operation.into(),
            state,
            previous_hash,
            record_hash: [0; 32],
        };
        record.record_hash = record.compute_hash();
        record
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"aegis-evidence-audit-v1");
        hasher.update(&self.sequence.to_le_bytes());
        hasher.update(&self.evidence_ref_hash);
        hasher.update(self.operation.as_bytes());
        hasher.update(&[self.state as u8]);
        hasher.update(&self.previous_hash);
        *hasher.finalize().as_bytes()
    }
}

impl EvidenceStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bind(&mut self, binding: EvidenceBinding) -> Result<(), EvidenceError> {
        if !binding.verify() {
            return Err(EvidenceError::InvalidBinding);
        }
        let evidence_ref_hash = binding.evidence_ref_hash;
        self.bindings.insert(binding.evidence_ref_hash, binding);
        self.append_audit(evidence_ref_hash, "bind", EvidenceLifecycleState::Bound);
        Ok(())
    }

    pub fn get(&mut self, evidence_ref_hash: [u8; 32]) -> Result<EvidenceBinding, EvidenceError> {
        let binding = self
            .bindings
            .get(&evidence_ref_hash)
            .cloned()
            .ok_or(EvidenceError::NotFound)?;
        self.append_audit(
            evidence_ref_hash,
            "access",
            EvidenceLifecycleState::Accessed,
        );
        Ok(binding)
    }

    pub fn audit_trail(&self, evidence_ref_hash: [u8; 32]) -> Vec<&AuditRecord> {
        self.audit
            .iter()
            .filter(|record| record.evidence_ref_hash == evidence_ref_hash)
            .collect()
    }

    pub fn verify_audit_chain(&self) -> Result<(), EvidenceError> {
        let mut previous = [0; 32];
        for (index, record) in self.audit.iter().enumerate() {
            if record.sequence != index as u64 + 1
                || record.previous_hash != previous
                || record.record_hash != record.compute_hash()
            {
                return Err(EvidenceError::AuditChainMismatch);
            }
            previous = record.record_hash;
        }
        Ok(())
    }

    fn append_audit(
        &mut self,
        evidence_ref_hash: [u8; 32],
        operation: &str,
        state: EvidenceLifecycleState,
    ) {
        let previous_hash = self
            .audit
            .last()
            .map_or([0; 32], |record| record.record_hash);
        self.audit.push(AuditRecord::new(
            self.audit.len() as u64 + 1,
            evidence_ref_hash,
            operation,
            state,
            previous_hash,
        ));
    }
}

fn nonzero(hash: &[u8; 32]) -> bool {
    hash.iter().any(|byte| *byte != 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hash(label: &str) -> [u8; 32] {
        *blake3::hash(label.as_bytes()).as_bytes()
    }

    #[test]
    fn evidence_binding_and_audit_are_tamper_evident() {
        let binding = EvidenceBinding::new(hash("ref"), hash("artifact"), hash("ast"), 42).unwrap();
        assert!(binding.verify());
        let mut store = EvidenceStore::new();
        store.bind(binding.clone()).unwrap();
        assert_eq!(store.get(binding.evidence_ref_hash).unwrap(), binding);
        assert_eq!(store.audit_trail(binding.evidence_ref_hash).len(), 2);
        assert!(store.verify_audit_chain().is_ok());
        store.audit[0].operation = "tampered".to_string();
        assert!(store.verify_audit_chain().is_err());
    }
}
