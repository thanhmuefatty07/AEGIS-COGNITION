//! Hot evidence-arena PyO3 bindings.
//!
//! The parent facade re-exports these functions to preserve the Python ABI.

use super::{admission_label, hex32, parse_trust_level, py_safe, trust_level_label};
use blake3::Hasher;
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (payload, trust_level=None, max_live_bytes=None, max_artifact_bytes=None))]
pub fn aegis_hot_commit(
    payload: Vec<u8>,
    trust_level: Option<String>,
    max_live_bytes: Option<usize>,
    max_artifact_bytes: Option<usize>,
) -> PyResult<String> {
    py_safe(move || {
        let trust_level = match trust_level {
            Some(value) => parse_trust_level(&value)?,
            None => crate::hot_engine::TrustLevel::from_env(),
        };
        let arena = crate::hot_engine::InMemoryEvidenceArena::new(
            trust_level,
            max_live_bytes.unwrap_or(64 * 1024 * 1024),
            max_artifact_bytes.unwrap_or(16 * 1024 * 1024),
        );
        let handle = arena
            .commit(&payload)
            .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("{err:?}")))?;
        let stats = arena.stats();
        let response = serde_json::json!({
            "schema": "aegis-hot-arena-commit-v1",
            "truth_claim": false,
            "verifier": "rust-hot-engine",
            "slot": handle.slot,
            "generation": handle.generation,
            "byte_len": handle.byte_len,
            "artifact_hash": hex32(&handle.artifact_hash),
            "storage_ref_hash": hex32(&handle.storage_ref_hash),
            "trust_level": trust_level_label(handle.trust_level),
            "admission": admission_label(handle.admission),
            "physical_witness_required": handle.trust_level.physical_witness_required(),
            "fail_closed": handle.trust_level.fail_closed(),
            "handle_valid": handle.is_valid(),
            "arena_live_bytes": stats.live_bytes,
            "arena_live_slots": stats.live_slots,
            "arena_total_commits": stats.total_commits,
            "arena_total_degraded_commits": stats.total_degraded_commits,
        });
        serde_json::to_string(&response).map_err(|err| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {err}"))
        })
    })?
}

#[pyfunction]
#[pyo3(signature = (payloads, trust_level=None, max_live_bytes=None, max_artifact_bytes=None))]
pub fn aegis_hot_commit_batch(
    payloads: Vec<Vec<u8>>,
    trust_level: Option<String>,
    max_live_bytes: Option<usize>,
    max_artifact_bytes: Option<usize>,
) -> PyResult<String> {
    py_safe(move || {
        if payloads.is_empty() || payloads.iter().any(Vec::is_empty) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "aegis_hot_commit_batch requires non-empty payloads",
            ));
        }
        let trust_level = match trust_level {
            Some(value) => parse_trust_level(&value)?,
            None => crate::hot_engine::TrustLevel::from_env(),
        };
        let total_input_bytes = payloads
            .iter()
            .try_fold(0usize, |total, payload| total.checked_add(payload.len()))
            .ok_or_else(|| {
                pyo3::exceptions::PyOverflowError::new_err(
                    "aegis_hot_commit_batch payload byte count overflow",
                )
            })?;
        let arena = crate::hot_engine::InMemoryEvidenceArena::new(
            trust_level,
            max_live_bytes.unwrap_or(64 * 1024 * 1024),
            max_artifact_bytes.unwrap_or(16 * 1024 * 1024),
        );
        let mut batch_hasher = Hasher::new();
        batch_hasher.update(b"aegis-hot-arena-commit-batch-v1");
        let payload_count = u64::try_from(payloads.len()).map_err(|_| {
            pyo3::exceptions::PyOverflowError::new_err(
                "aegis_hot_commit_batch payload count exceeds u64",
            )
        })?;
        batch_hasher.update(&payload_count.to_le_bytes());
        let mut commits = Vec::with_capacity(payloads.len());
        for (index, payload) in payloads.iter().enumerate() {
            let handle = arena
                .commit(payload)
                .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("{err:?}")))?;
            let index_u64 = u64::try_from(index).map_err(|_| {
                pyo3::exceptions::PyOverflowError::new_err(
                    "aegis_hot_commit_batch payload index exceeds u64",
                )
            })?;
            batch_hasher.update(&index_u64.to_le_bytes());
            batch_hasher.update(&handle.slot.to_le_bytes());
            batch_hasher.update(&handle.generation.to_le_bytes());
            batch_hasher.update(&handle.byte_len.to_le_bytes());
            batch_hasher.update(&handle.artifact_hash);
            batch_hasher.update(&handle.storage_ref_hash);
            commits.push(serde_json::json!({
                "schema": "aegis-hot-arena-commit-v1",
                "truth_claim": false,
                "verifier": "rust-hot-engine",
                "index": index,
                "slot": handle.slot,
                "generation": handle.generation,
                "byte_len": handle.byte_len,
                "artifact_hash": hex32(&handle.artifact_hash),
                "storage_ref_hash": hex32(&handle.storage_ref_hash),
                "trust_level": trust_level_label(handle.trust_level),
                "admission": admission_label(handle.admission),
                "physical_witness_required": handle.trust_level.physical_witness_required(),
                "fail_closed": handle.trust_level.fail_closed(),
                "handle_valid": handle.is_valid(),
            }));
        }
        let stats = arena.stats();
        let batch_digest = *batch_hasher.finalize().as_bytes();
        let response = serde_json::json!({
            "schema": "aegis-hot-arena-commit-batch-v1",
            "truth_claim": false,
            "verifier": "rust-hot-engine",
            "trust_level": trust_level_label(trust_level),
            "artifact_count": commits.len(),
            "total_bytes": total_input_bytes,
            "no_file_roundtrip_on_hot_path": true,
            "batch_digest": hex32(&batch_digest),
            "arena_live_bytes": stats.live_bytes,
            "arena_live_slots": stats.live_slots,
            "arena_total_commits": stats.total_commits,
            "arena_total_degraded_commits": stats.total_degraded_commits,
            "commits": commits,
        });
        serde_json::to_string(&response).map_err(|err| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {err}"))
        })
    })?
}

// ───
