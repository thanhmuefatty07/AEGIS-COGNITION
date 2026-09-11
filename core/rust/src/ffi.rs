#![allow(clippy::useless_conversion)]

use crate::connections::ConnectionRepository;
use crate::conversations::ConversationRepository;
use crate::learning::LearningLedger;
use crate::memory::repository::MemoryRepository;
use crate::memory::session_search::SessionSearchIndex;
use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::fs::{File, OpenOptions};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

mod compat;
mod connections;
mod context;
mod conversations;
mod eac;
mod hot;
mod lab;
mod learning;
mod mmap;
mod runtime;
mod source_watcher;
mod status;
pub use compat::{
    aegis_harness_analyze_errors, aegis_harness_generate_skeleton, aegis_hot_hash,
    aegis_llm_bridge_key, aegis_llm_normalize, aegis_llm_reject, aegis_llm_request,
    aegis_llm_route, aegis_physical_metrics_prometheus, aegis_runtime_telemetry_emit,
    aegis_runtime_telemetry_snapshot, aegis_trust_level,
};
pub use connections::{
    aegis_check_connection_egress, aegis_grant_connection_egress, aegis_list_connections,
    aegis_list_model_descriptors, aegis_revoke_connection, aegis_revoke_connection_egress,
    aegis_upsert_connection, aegis_upsert_model_descriptor,
};
pub use context::aegis_select_context_items;
pub use conversations::{
    aegis_append_conversation_part, aegis_append_conversation_turn,
    aegis_checkpoint_conversation_execution, aegis_create_conversation,
    aegis_finish_conversation_execution, aegis_list_conversations, aegis_read_conversation,
    aegis_reconcile_conversation_tool_call, aegis_record_conversation_tool_call,
    aegis_record_conversation_tool_result, aegis_set_conversation_status,
    aegis_start_conversation_execution, aegis_switch_conversation_model,
    aegis_transition_conversation_turn,
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
    aegis_backup_memory_store, aegis_capture_memory, aegis_correct_memory, aegis_forget_memory,
    aegis_get_learning_stats, aegis_grant_memory_access, aegis_index_session,
    aegis_index_session_scoped, aegis_inspect_memory, aegis_purge_memory,
    aegis_read_session_content, aegis_read_session_content_scoped,
    aegis_read_session_record_scoped, aegis_restore_memory, aegis_revoke_memory_access,
    aegis_search_memories, aegis_search_past_sessions, aegis_search_past_sessions_scoped,
    aegis_trigger_memory_nudge, aegis_validate_memory,
};
pub use mmap::{
    aegis_execute_mmap_wasm_bridge_frame, aegis_mmap_bridge_header_bytes,
    aegis_mmap_bridge_payload_alignment, aegis_validate_mmap_bridge_frame,
    aegis_write_mmap_bridge_pattern,
};
pub use runtime::{
    aegis_cooperative_placement_admit, aegis_cooperative_placement_preview,
    aegis_cooperative_placement_release, aegis_execution_lanes, aegis_hardware_profile,
    aegis_placement_calibrate, aegis_placement_capabilities, aegis_placement_plan,
    aegis_resource_admission_preview, aegis_resource_contract_version, aegis_resource_usage_sample,
    aegis_runtime_cancel, aegis_runtime_cooperative_cancel, aegis_runtime_cooperative_configure,
    aegis_runtime_cooperative_finish, aegis_runtime_cooperative_submit, aegis_runtime_finish,
    aegis_runtime_observe_resources, aegis_runtime_poll, aegis_runtime_retry, aegis_runtime_submit,
};
pub use source_watcher::{
    aegis_poll_source_watcher, aegis_start_source_watcher, aegis_stop_source_watcher,
};
pub use status::{
    aegis_can_bridge_python, aegis_cli_schema, aegis_cli_status, aegis_descriptor_valid,
    aegis_frame_is_valid, aegis_layout_header_bytes, aegis_layout_payload_alignment,
    aegis_memory_alignment, aegis_message_frame_valid, aegis_nerve_schema_id,
    aegis_new_message_identity, aegis_release_ready, aegis_status, aegis_validate_layout,
    aegis_validate_schema, aegis_zero_copy_ready,
};

static SESSION_INDEX: OnceLock<Result<Mutex<SessionSearchIndex>, String>> = OnceLock::new();
static SESSION_LEDGER: OnceLock<Mutex<LearningLedger>> = OnceLock::new();
static PROFILE_LOCK: OnceLock<Result<File, String>> = OnceLock::new();
static MEMORY_REPOSITORY: OnceLock<Result<Mutex<MemoryRepository>, String>> = OnceLock::new();
static CONNECTION_REPOSITORY: OnceLock<Result<Mutex<ConnectionRepository>, String>> =
    OnceLock::new();
static CONVERSATION_REPOSITORY: OnceLock<Result<Mutex<ConversationRepository>, String>> =
    OnceLock::new();
static AUTHORITATIVE_RUNTIME: OnceLock<Mutex<crate::runtime::AuthoritativeRuntime>> =
    OnceLock::new();

pub(crate) struct CooperativeAdmissionState {
    pub inventory_hash: [u8; 32],
    pub ledger: crate::resource::CooperativeAdmissionLedger,
}

static COOPERATIVE_ADMISSION: OnceLock<Mutex<Option<CooperativeAdmissionState>>> = OnceLock::new();

fn session_store_path() -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("AEGIS_SESSION_DB_PATH") {
        let path = PathBuf::from(path);
        if path.as_os_str().is_empty() {
            return Err("AEGIS_SESSION_DB_PATH must not be empty".to_string());
        }
        return Ok(path);
    }

    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from)
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(|home| PathBuf::from(home).join(".local").join("share"))
            })
    }
    .unwrap_or_else(std::env::temp_dir);
    Ok(base.join("aegis").join("state.db"))
}

fn session_profile_id() -> String {
    std::env::var("AEGIS_PROFILE_ID")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "local-profile".to_string())
}

fn acquire_profile_lock(path: &Path) -> Result<(), String> {
    PROFILE_LOCK
        .get_or_init(|| {
            let lock_path = path.with_extension("lock");
            let file = OpenOptions::new()
                .create(true)
                .truncate(false)
                .read(true)
                .write(true)
                .open(lock_path)
                .map_err(|error| error.to_string())?;
            file.try_lock().map_err(|error| {
                format!("profile is already owned by another AEGIS process: {error}")
            })?;
            Ok(file)
        })
        .as_ref()
        .map(|_| ())
        .map_err(|error| error.clone())
}

fn get_session_index() -> Result<&'static Mutex<SessionSearchIndex>, String> {
    SESSION_INDEX
        .get_or_init(|| {
            // The lexical index rejects the all-zero genesis epoch.  Derive a
            // stable non-zero process epoch instead of unwrapping an invalid
            // value (which would panic on the first completed Agent run).
            let epoch_hash = *blake3::hash(b"aegis-session-index-epoch-v1").as_bytes();
            let path = session_store_path()?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            acquire_profile_lock(&path)?;
            let profile_id = session_profile_id();
            SessionSearchIndex::open_scoped(path, epoch_hash, &profile_id)
                .map(Mutex::new)
                .map_err(|error| format!("{error:?}"))
        })
        .as_ref()
        .map_err(|error| error.clone())
}

fn get_session_ledger() -> &'static Mutex<LearningLedger> {
    SESSION_LEDGER.get_or_init(|| Mutex::new(LearningLedger::new()))
}

fn get_memory_repository() -> Result<&'static Mutex<MemoryRepository>, String> {
    MEMORY_REPOSITORY
        .get_or_init(|| {
            let path = session_store_path()?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            acquire_profile_lock(&path)?;
            MemoryRepository::open(path)
                .map(Mutex::new)
                .map_err(|error| format!("{error:?}"))
        })
        .as_ref()
        .map_err(|error| error.clone())
}

fn get_connection_repository() -> Result<&'static Mutex<ConnectionRepository>, String> {
    CONNECTION_REPOSITORY
        .get_or_init(|| {
            let path = session_store_path()?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            acquire_profile_lock(&path)?;
            ConnectionRepository::open(path)
                .map(Mutex::new)
                .map_err(|error| format!("{error:?}"))
        })
        .as_ref()
        .map_err(|error| error.clone())
}

fn get_conversation_repository() -> Result<&'static Mutex<ConversationRepository>, String> {
    CONVERSATION_REPOSITORY
        .get_or_init(|| {
            let path = session_store_path()?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            acquire_profile_lock(&path)?;
            ConversationRepository::open(path)
                .map(Mutex::new)
                .map_err(|error| format!("{error:?}"))
        })
        .as_ref()
        .map_err(|error| error.clone())
}

fn get_authoritative_runtime() -> &'static Mutex<crate::runtime::AuthoritativeRuntime> {
    AUTHORITATIVE_RUNTIME.get_or_init(|| {
        let profile = crate::resource::HardwareProfile::probe();
        Mutex::new(crate::runtime::AuthoritativeRuntime::new(60_000, &profile))
    })
}

pub(crate) fn get_cooperative_admission() -> &'static Mutex<Option<CooperativeAdmissionState>> {
    COOPERATIVE_ADMISSION.get_or_init(|| Mutex::new(None))
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
    m.add_function(wrap_pyfunction!(aegis_placement_capabilities, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_placement_calibrate, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_placement_plan, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_cooperative_placement_preview, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_cooperative_placement_admit, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_cooperative_placement_release, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_cooperative_configure, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_cooperative_submit, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_cooperative_finish, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_cooperative_cancel, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_resource_contract_version, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_resource_admission_preview, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_execution_lanes, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_resource_usage_sample, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_observe_resources, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_submit, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_retry, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_finish, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_poll, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_runtime_cancel, m)?)?;
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
    m.add_function(wrap_pyfunction!(aegis_start_source_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_poll_source_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_stop_source_watcher, m)?)?;
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
    m.add_function(wrap_pyfunction!(aegis_upsert_connection, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_list_connections, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_revoke_connection, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_upsert_model_descriptor, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_list_model_descriptors, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_grant_connection_egress, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_revoke_connection_egress, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_check_connection_egress, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_create_conversation, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_list_conversations, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_read_conversation, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_append_conversation_turn, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_switch_conversation_model, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_set_conversation_status, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_append_conversation_part, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_transition_conversation_turn, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_start_conversation_execution, m)?)?;
    m.add_function(wrap_pyfunction!(
        aegis_checkpoint_conversation_execution,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(aegis_finish_conversation_execution, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_record_conversation_tool_call, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_record_conversation_tool_result, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_reconcile_conversation_tool_call, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_select_context_items, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_get_learning_stats, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_capture_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_backup_memory_store, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_correct_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_validate_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_grant_memory_access, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_revoke_memory_access, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_inspect_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_search_memories, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_forget_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_restore_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_purge_memory, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_trigger_memory_nudge, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_search_past_sessions, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_index_session, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_index_session_scoped, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_read_session_content, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_read_session_content_scoped, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_read_session_record_scoped, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_search_past_sessions_scoped, m)?)?;
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
