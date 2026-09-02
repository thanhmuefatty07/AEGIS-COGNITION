#![allow(clippy::useless_conversion)]

use crate::learning::LearningLedger;
use crate::memory::session_search::SessionSearchIndex;
use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::OnceLock;

mod compat;
mod eac;
mod hot;
mod lab;
mod learning;
mod mmap;
mod runtime;
mod status;
pub use compat::{
    aegis_harness_analyze_errors, aegis_harness_generate_skeleton, aegis_hot_hash,
    aegis_llm_bridge_key, aegis_llm_normalize, aegis_llm_reject, aegis_llm_request,
    aegis_llm_route, aegis_physical_metrics_prometheus, aegis_runtime_telemetry_emit,
    aegis_runtime_telemetry_snapshot, aegis_trust_level,
};
pub use eac::{
    aegis_eac_batch, aegis_eac_cache_insert, aegis_eac_cache_invalidate, aegis_eac_cache_lookup,
    aegis_eac_load_state, aegis_eac_persist_state,
};
pub use hot::{aegis_hot_commit, aegis_hot_commit_batch};
pub use lab::{
    PyLabController, aegis_lab_archive_events, aegis_lab_validate_snapshot,
    aegis_lab_validate_transition, aegis_lab_verify_archive,
    aegis_lab_verify_archive_against_events, aegis_lab_verify_archive_against_legacy_manifest,
    aegis_lab_verify_archive_against_manifest, aegis_lab_verify_event_chain,
};
pub use learning::{
    aegis_get_learning_stats, aegis_index_session, aegis_search_past_sessions,
    aegis_trigger_memory_nudge,
};
pub use mmap::{
    aegis_execute_mmap_wasm_bridge_frame, aegis_mmap_bridge_header_bytes,
    aegis_mmap_bridge_payload_alignment, aegis_validate_mmap_bridge_frame,
    aegis_write_mmap_bridge_pattern,
};
pub use runtime::{
    aegis_execution_lanes, aegis_hardware_profile, aegis_resource_admission_preview,
    aegis_resource_contract_version, aegis_resource_usage_sample, aegis_runtime_finish,
    aegis_runtime_retry, aegis_runtime_submit,
};
pub use status::{
    aegis_can_bridge_python, aegis_cli_schema, aegis_cli_status, aegis_descriptor_valid,
    aegis_frame_is_valid, aegis_layout_header_bytes, aegis_layout_payload_alignment,
    aegis_memory_alignment, aegis_message_frame_valid, aegis_nerve_schema_id,
    aegis_new_message_identity, aegis_release_ready, aegis_status, aegis_validate_layout,
    aegis_validate_schema, aegis_zero_copy_ready,
};

static SESSION_INDEX: OnceLock<Mutex<SessionSearchIndex>> = OnceLock::new();
static SESSION_LEDGER: OnceLock<Mutex<LearningLedger>> = OnceLock::new();
static AUTHORITATIVE_RUNTIME: OnceLock<Mutex<crate::runtime::AuthoritativeRuntime>> =
    OnceLock::new();

fn get_session_index() -> &'static Mutex<SessionSearchIndex> {
    SESSION_INDEX.get_or_init(|| {
        // The lexical index rejects the all-zero genesis epoch.  Derive a
        // stable non-zero process epoch instead of unwrapping an invalid
        // value (which would panic on the first completed Agent run).
        let epoch_hash = *blake3::hash(b"aegis-session-index-epoch-v1").as_bytes();
        Mutex::new(SessionSearchIndex::new(epoch_hash).unwrap())
    })
}

fn get_session_ledger() -> &'static Mutex<LearningLedger> {
    SESSION_LEDGER.get_or_init(|| Mutex::new(LearningLedger::new()))
}

fn get_authoritative_runtime() -> &'static Mutex<crate::runtime::AuthoritativeRuntime> {
    AUTHORITATIVE_RUNTIME.get_or_init(|| {
        let profile = crate::resource::HardwareProfile::probe();
        Mutex::new(crate::runtime::AuthoritativeRuntime::new(60_000, &profile))
    })
}

fn py_safe<T>(f: impl FnOnce() -> T) -> PyResult<T> {
    catch_unwind(AssertUnwindSafe(f)).map_err(|_| {
        pyo3::exceptions::PyRuntimeError::new_err("panic prevented across FFI boundary")
    })
}

// ─────────────────────────────────────────────────────────────────────────

#[pymodule]
pub fn aegis_nerve(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(aegis_status, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_validate_schema, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_validate_layout, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_memory_alignment, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_nerve_schema_id, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_frame_is_valid, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_message_frame_valid, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_zero_copy_ready, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_mmap_bridge_header_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_mmap_bridge_payload_alignment, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_write_mmap_bridge_pattern, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_validate_mmap_bridge_frame, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_execute_mmap_wasm_bridge_frame, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_new_message_identity, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_can_bridge_python, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_layout_header_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_layout_payload_alignment, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_descriptor_valid, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_cli_status, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_cli_schema, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_release_ready, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_hardware_profile, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_resource_contract_version, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_resource_admission_preview, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_execution_lanes, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_resource_usage_sample, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_submit, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_retry, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_finish, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_request, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_route, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_bridge_key, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_reject, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_harness_generate_skeleton, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_harness_analyze_errors, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_physical_metrics_prometheus, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_telemetry_emit, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_telemetry_snapshot, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_trust_level, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_hot_hash, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_hot_commit, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_hot_commit_batch, m)?)?;
    // EaC bindings
    m.add_function(wrap_pyfunction!(aegis_eac_cache_lookup, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_eac_cache_insert, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_eac_cache_invalidate, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_eac_batch, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_eac_persist_state, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_eac_load_state, m)?)?;
    // Learning Loop bindings (Sprint 5)
    m.add_function(wrap_pyfunction!(aegis_get_learning_stats, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_trigger_memory_nudge, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_search_past_sessions, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_index_session, m)?)?;
    m.add_class::<PyLabController>()?;
    m.add_function(wrap_pyfunction!(aegis_lab_verify_event_chain, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_lab_validate_transition, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_lab_validate_snapshot, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_lab_archive_events, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_lab_verify_archive, m)?)?;
    m.add_function(wrap_pyfunction!(
        aegis_lab_verify_archive_against_manifest,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        aegis_lab_verify_archive_against_events,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        aegis_lab_verify_archive_against_legacy_manifest,
        m
    )?)?;
    Ok(())
}

fn parse_trust_level(value: &str) -> PyResult<crate::hot_engine::TrustLevel> {
    match value.trim().to_ascii_uppercase().as_str() {
        "DEV" => Ok(crate::hot_engine::TrustLevel::Dev),
        "STAGING" => Ok(crate::hot_engine::TrustLevel::Staging),
        "PROD" => Ok(crate::hot_engine::TrustLevel::Prod),
        _ => Err(pyo3::exceptions::PyValueError::new_err(
            "AEGIS trust level must be DEV, STAGING, or PROD",
        )),
    }
}

fn trust_level_label(level: crate::hot_engine::TrustLevel) -> &'static str {
    match level {
        crate::hot_engine::TrustLevel::Dev => "DEV",
        crate::hot_engine::TrustLevel::Staging => "STAGING",
        crate::hot_engine::TrustLevel::Prod => "PROD",
    }
}

fn admission_label(admission: crate::hot_engine::ArenaAdmission) -> &'static str {
    match admission {
        crate::hot_engine::ArenaAdmission::Accepted => "accepted",
        crate::hot_engine::ArenaAdmission::DegradedWarning => "degraded_warning",
    }
}

fn hex32(bytes: &[u8; 32]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
