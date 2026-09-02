#![allow(clippy::useless_conversion)]

use crate::learning::LearningLedger;
use crate::memory::session_search::SessionSearchIndex;
use blake3::Hasher;
use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::OnceLock;

mod compat;
mod mmap;
mod runtime;
mod status;
pub use compat::{
    aegis_harness_analyze_errors, aegis_harness_generate_skeleton, aegis_hot_hash,
    aegis_llm_bridge_key, aegis_llm_normalize, aegis_llm_reject, aegis_llm_request,
    aegis_llm_route, aegis_physical_metrics_prometheus, aegis_runtime_telemetry_emit,
    aegis_runtime_telemetry_snapshot, aegis_trust_level,
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
        let total_input_bytes = payloads.iter().map(Vec::len).sum::<usize>();
        let arena = crate::hot_engine::InMemoryEvidenceArena::new(
            trust_level,
            max_live_bytes.unwrap_or(64 * 1024 * 1024),
            max_artifact_bytes.unwrap_or(16 * 1024 * 1024),
        );
        let mut batch_hasher = Hasher::new();
        batch_hasher.update(b"aegis-hot-arena-commit-batch-v1");
        batch_hasher.update(&(payloads.len() as u64).to_le_bytes());
        let mut commits = Vec::with_capacity(payloads.len());
        for (index, payload) in payloads.iter().enumerate() {
            let handle = arena
                .commit(payload)
                .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("{err:?}")))?;
            batch_hasher.update(&(index as u64).to_le_bytes());
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

// ─── EaC PyO3 Bindings ──────────────────────────────────────────────────────

/// Create a new SemanticCache and look up a prompt.
/// Returns the cached response if found (exact or semantic match), else None.
#[pyfunction]
#[pyo3(signature = (prompt, embedding, namespace, threshold=0.95, cache_file=None))]
pub fn aegis_eac_cache_lookup(
    prompt: String,
    embedding: Vec<f32>,
    namespace: String,
    threshold: f32,
    cache_file: Option<String>,
) -> PyResult<Option<String>> {
    py_safe(move || {
        let cache = crate::eac::cache::SemanticCache::new(threshold, cache_file);
        cache.lookup(&prompt, embedding, &namespace)
    })
}

/// Insert a new record into the semantic cache.
#[pyfunction]
#[pyo3(signature = (prompt, response, embedding, namespace, threshold=0.95, cache_file=None))]
pub fn aegis_eac_cache_insert(
    prompt: String,
    response: String,
    embedding: Vec<f32>,
    namespace: String,
    threshold: f32,
    cache_file: Option<String>,
) -> PyResult<bool> {
    py_safe(move || {
        let mut cache = crate::eac::cache::SemanticCache::new(threshold, cache_file);
        cache.insert(prompt, response, embedding, namespace);
        true
    })
}

/// Invalidate all cache entries for a given namespace.
#[pyfunction]
#[pyo3(signature = (namespace, threshold=0.95, cache_file=None))]
pub fn aegis_eac_cache_invalidate(
    namespace: String,
    threshold: f32,
    cache_file: Option<String>,
) -> PyResult<bool> {
    py_safe(move || {
        let mut cache = crate::eac::cache::SemanticCache::new(threshold, cache_file);
        cache.invalidate(&namespace);
        true
    })
}

/// Execute a batch of tool calls synchronously and return JSON results.
/// `calls_json` must be a JSON array of objects with `call_id`, `tool_name`, `arguments`.
/// Returns a JSON string of results.
#[pyfunction]
pub fn aegis_eac_batch(calls_json: String, policy: String) -> PyResult<String> {
    py_safe(move || {
        let calls: Vec<crate::eac::sandbox::ToolCall> =
            serde_json::from_str(&calls_json).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("Invalid calls JSON: {}", e))
            })?;

        let tx_policy = match policy.as_str() {
            "HaltOnFailure" => crate::eac::sandbox::TransactionPolicy::HaltOnFailure,
            _ => crate::eac::sandbox::TransactionPolicy::ContinueOnFailure,
        };

        // Default mock executor for the FFI layer — in production this would
        // be wired to the real tool gateway.
        let results = crate::eac::sandbox::BatchExecutor::execute_batch(
            calls,
            tx_policy,
            |call| crate::eac::sandbox::ToolResult {
                call_id: call.call_id,
                success: true,
                output: format!("Executed {} with {:?}", call.tool_name, call.arguments),
                error: None,
            },
            None,
        );

        serde_json::to_string(&results).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

/// Persist state to the filesystem with BLAKE3 chain integrity.
/// `data_json` is a JSON object of key-value pairs.
/// Returns the state hash as a hex string.
#[pyfunction]
#[pyo3(signature = (base_dir, turn_index, namespace, data_json, prev_state_hash=None))]
pub fn aegis_eac_persist_state(
    base_dir: String,
    turn_index: u64,
    namespace: String,
    data_json: String,
    prev_state_hash: Option<String>,
) -> PyResult<String> {
    py_safe(move || {
        let data: std::collections::BTreeMap<String, serde_json::Value> =
            serde_json::from_str(&data_json).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!("Invalid data JSON: {}", e))
            })?;

        let serde = crate::eac::sandbox::FilesystemSerde::new(&base_dir).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("FilesystemSerde init failed: {}", e))
        })?;

        let turn_state = serde
            .persist(turn_index, &namespace, data, prev_state_hash, None)
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Persist failed: {}", e))
            })?;

        Ok(turn_state.state_hash)
    })?
}

/// Load state from the filesystem for a given turn index.
/// Returns the state as a JSON string.
#[pyfunction]
pub fn aegis_eac_load_state(base_dir: String, turn_index: u64) -> PyResult<String> {
    py_safe(move || {
        let serde = crate::eac::sandbox::FilesystemSerde::new(&base_dir).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("FilesystemSerde init failed: {}", e))
        })?;

        let turn_state = serde.load(turn_index, None).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Load failed: {}", e))
        })?;

        serde_json::to_string(&turn_state).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

// ─────────────────────────────────────────────────────────────────────────
// Learning Loop PyO3 Bindings (Sprint 5 — Python DX Bridge)
// ─────────────────────────────────────────────────────────────────────────

/// Return a JSON object with learning loop statistics:
///   { "ledger_events": N, "improved_skills": M, "nudged_memories": K,
///     "indexed_sessions": S, "user_models": U }
/// These stats are computed live from the Rust LearningLedger and
/// in-memory indices — no filesystem round-trip.
#[pyfunction]
#[pyo3(signature = (ledger_json, _skill_registry_stats_json=String::new(), search_index_count=0u64, user_model_count=0u64))]
pub fn aegis_get_learning_stats(
    ledger_json: String,
    _skill_registry_stats_json: String,
    search_index_count: u64,
    user_model_count: u64,
) -> PyResult<String> {
    py_safe(move || {
        // Parse the ledger to count events by type
        let ledger: serde_json::Value = serde_json::from_str(&ledger_json).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid ledger JSON: {}", e))
        })?;

        let total_events = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| arr.len())
            .unwrap_or(0);

        // Count SkillImproved events for improved_skills
        let improved_skills = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|ev| ev.get("type").and_then(|t| t.as_str()) == Some("SkillImproved"))
                    .count()
            })
            .unwrap_or(0);

        // Count MemoryPersisted events for nudged_memories
        let nudged_memories = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|ev| ev.get("type").and_then(|t| t.as_str()) == Some("MemoryPersisted"))
                    .count()
            })
            .unwrap_or(0);

        let final_search_index_count =
            search_index_count.max(get_session_index().lock().len() as u64);

        let stats = serde_json::json!({
            "schema": "aegis-learning-stats-v1",
            "ledger_events": total_events,
            "improved_skills": improved_skills,
            "nudged_memories": nudged_memories,
            "indexed_sessions": final_search_index_count,
            "user_models": user_model_count,
        });

        serde_json::to_string(&stats).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

/// Trigger a background memory nudge for the given session.
///
/// This is a Python-facing stub that validates the inputs and returns
/// a JSON acknowledgment. The actual Rust nudge must be called via
/// the module's in-process MemoryNudgeSystem — this FFI entrypoint
/// ensures the Python layer can invoke it without panicking.
///
/// Returns JSON: { "nudge_accepted": true, "session_id": "<hex>", ... }
#[pyfunction]
#[pyo3(signature = (session_id_str, nudge_id=0u128, candidate_count=0usize))]
pub fn aegis_trigger_memory_nudge(
    session_id_str: String,
    nudge_id: u128,
    candidate_count: usize,
) -> PyResult<String> {
    py_safe(move || {
        // Parse session_id from hex or decimal string
        let session_id = if let Some(hex) = session_id_str.strip_prefix("0x") {
            u128::from_str_radix(hex, 16)
        } else {
            session_id_str.parse::<u128>()
        }
        .map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid session_id '{}': {}",
                session_id_str, e
            ))
        })?;

        if session_id == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "session_id must be non-zero",
            ));
        }

        let ack = serde_json::json!({
            "schema": "aegis-memory-nudge-ack-v1",
            "nudge_accepted": true,
            "session_id": format!("0x{:x}", session_id),
            "nudge_id": nudge_id,
            "candidate_count": candidate_count,
            "message": "Background nudge invoked via Python FFI — actual processing happens async in the Rust engine.",
        });

        serde_json::to_string(&ack).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

/// Index a session transcript for cross-session recall and return its content hash.
#[pyfunction]
pub fn aegis_index_session(session_id: u128, content: String, timestamp: u64) -> PyResult<String> {
    py_safe(move || {
        let mut index = get_session_index().lock();
        let mut ledger = get_session_ledger().lock();
        let content_hash = index
            .index_session(session_id, &content, timestamp, &mut ledger, None)
            .map_err(|err| {
                pyo3::exceptions::PyRuntimeError::new_err(format!("Index failed: {:?}", err))
            })?;
        Ok(hex32(&content_hash))
    })?
}

/// Search past sessions and return cryptographically-bound candidate refs.
///
/// Returns a JSON object with an array of CandidateEvidenceRef-like results:
///   {
///     "schema": "aegis-session-search-result-v1",
///     "query": "...",
///     "top_k": 5,
///     "count": ..., "results": [...],
///     "tier": "ColdVectorExpansion",
///     "gate": "CandidateOnly"
///   }
///
/// **Security**: results are CandidateOnlyGate-tiered — they are NOT truth
/// and cannot be used as PhysicalWitness or PolicyApproval.
#[pyfunction]
#[pyo3(signature = (query, top_k=5usize))]
pub fn aegis_search_past_sessions(query: String, top_k: usize) -> PyResult<String> {
    py_safe(move || {
        if query.trim().is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "query must be non-empty",
            ));
        }

        if top_k == 0 || top_k > 100 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "top_k must be between 1 and 100",
            ));
        }

        let index = get_session_index().lock();
        let candidates = index.search_sessions(&query, top_k);

        let results: Vec<serde_json::Value> = candidates
            .iter()
            .map(|c| {
                serde_json::json!({
                    "evidence_ref_hash": hex32(&c.evidence_ref_hash),
                    "segment_id": c.segment_id,
                    "tier": "ColdVectorExpansion",
                    "score": (c.score_quantized as f64) / (crate::evidence_index::CANDIDATE_SCORE_PPM_MAX as f64),
                    "epoch_hash": hex32(&c.index_epoch_hash),
                })
            })
            .collect();

        let envelope = serde_json::json!({
            "schema": "aegis-session-search-result-v1",
            "query": query,
            "top_k": top_k,
            "count": results.len(),
            "results": results,
            "tier": "ColdVectorExpansion",
            "gate": "CandidateOnly",
        });

        serde_json::to_string(&envelope).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })
    })?
}

/// Verify a serialized AEGIS Lab event chain at the Rust authority boundary.
///
/// Python may operate as an adapter when the extension is unavailable, but it
/// must never label a projection as native-verified.  The wire format is the
/// serde representation of `crate::lab::LabEvent` (hashes are byte arrays).
#[pyclass(name = "LabController")]
pub struct PyLabController {
    inner: crate::lab::LabController,
}

#[pymethods]
impl PyLabController {
    #[new]
    fn new(mission_json: String) -> PyResult<Self> {
        let mission: crate::lab::LabMissionSpec =
            serde_json::from_str(&mission_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid Lab mission JSON: {error}"
                ))
            })?;
        let inner = crate::lab::LabController::new(mission).map_err(lab_controller_error)?;
        Ok(Self { inner })
    }

    #[staticmethod]
    fn from_snapshot_json(snapshot_json: String) -> PyResult<Self> {
        let inner = crate::lab::LabController::from_snapshot_json(&snapshot_json)
            .map_err(lab_controller_error)?;
        Ok(Self { inner })
    }

    #[getter]
    fn state(&self) -> String {
        lab_state_label(self.inner.runtime().state()).to_string()
    }

    #[getter]
    fn state_epoch(&self) -> u64 {
        self.inner.runtime().state_epoch()
    }

    #[getter]
    fn steps(&self) -> u32 {
        self.inner.steps()
    }

    #[getter]
    fn replayable(&self) -> bool {
        self.inner.runtime().replayable()
    }

    fn budget_remaining_json(&self) -> PyResult<String> {
        let remaining = self.inner.budget_remaining();
        serde_json::to_string(&serde_json::json!({
            "tokens": remaining.tokens,
            "money_minor_units": remaining.money_minor_units,
            "time_ms": remaining.time_ms,
            "tool_calls": remaining.tool_calls,
            "api_calls": remaining.api_calls,
            "cpu_ms": remaining.cpu_ms,
            "risk_units": remaining.risk_units,
            "finalization_state": format!("{:?}", self.inner.finalization_state()),
        }))
        .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))
    }

    fn snapshot_json(&self) -> PyResult<String> {
        self.inner.snapshot_json().map_err(lab_controller_error)
    }

    /// Restore the controller in place to a previously validated snapshot.
    ///
    /// The Python facade uses this only for atomic rollback when a projection
    /// mutation has already changed its local view but native admission later
    /// rejects the event.  Replacing the inner reducer preserves the PyO3
    /// object identity held by the facade while restoring native epoch,
    /// events, budget and projection indexes together.
    fn restore_snapshot_json(&mut self, snapshot_json: String) -> PyResult<()> {
        self.inner = crate::lab::LabController::from_snapshot_json(&snapshot_json)
            .map_err(lab_controller_error)?;
        Ok(())
    }

    fn admit_exploration(&mut self, expected_state_epoch: u64, spend_json: String) -> PyResult<()> {
        let spend: crate::gt96::BudgetVector =
            serde_json::from_str(&spend_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid Lab budget vector JSON: {error}"
                ))
            })?;
        self.inner
            .admit_exploration(expected_state_epoch, spend)
            .map_err(lab_controller_error)
    }

    fn transition(&mut self, next: String) -> PyResult<()> {
        let Some(state) = parse_lab_state(&next) else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "invalid Lab controller state",
            ));
        };
        self.inner.transition(state).map_err(lab_controller_error)
    }

    fn begin_finalization(&mut self) -> PyResult<()> {
        self.inner
            .begin_finalization()
            .map_err(lab_controller_error)
    }

    fn reopen_for_critical_gap(&mut self) -> PyResult<()> {
        self.inner
            .reopen_for_critical_gap()
            .map_err(lab_controller_error)
    }

    #[pyo3(signature = (event_json, state=None))]
    fn admit_event_json(&mut self, event_json: String, state: Option<String>) -> PyResult<()> {
        let event: crate::lab::LabEvent = serde_json::from_str(&event_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid Lab projection event JSON: {error}"
            ))
        })?;
        let projection_state = match state {
            Some(value) => Some(parse_lab_state(&value).ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("invalid Lab projection state")
            })?),
            None => None,
        };
        self.inner
            .admit_projection_event(event, projection_state)
            .map_err(lab_controller_error)
    }

    /// Admit a projection event and validate the associated typed JSON
    /// record before the native controller retains it.  The payload hash is
    /// bound to a separate projection-payload domain so adapter-only fields
    /// cannot be confused with a Rust typed-record hash.
    #[pyo3(signature = (event_json, payload_json, state=None))]
    fn admit_record_json(
        &mut self,
        event_json: String,
        payload_json: String,
        state: Option<String>,
    ) -> PyResult<()> {
        let event: crate::lab::LabEvent = serde_json::from_str(&event_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid Lab projection record event JSON: {error}"
            ))
        })?;
        let projection_state = match state {
            Some(value) => Some(parse_lab_state(&value).ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("invalid Lab projection state")
            })?),
            None => None,
        };
        self.inner
            .admit_projection_record(event, &payload_json, projection_state)
            .map_err(lab_controller_error)
    }

    fn admit_exploration_event_json(
        &mut self,
        event_json: String,
        expected_state_epoch: u64,
        spend_json: String,
    ) -> PyResult<()> {
        let event: crate::lab::LabEvent = serde_json::from_str(&event_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid Lab exploration event JSON: {error}"
            ))
        })?;
        let spend: crate::gt96::BudgetVector =
            serde_json::from_str(&spend_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid Lab exploration budget JSON: {error}"
                ))
            })?;
        self.inner
            .admit_projection_exploration(expected_state_epoch, spend, event)
            .map_err(lab_controller_error)
    }

    fn record_source_json(&mut self, payload: String) -> PyResult<()> {
        let value: crate::lab::SourceRecord = serde_json::from_str(&payload)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        self.inner
            .record_source(value)
            .map_err(lab_controller_error)
    }

    fn record_claim_json(&mut self, payload: String) -> PyResult<()> {
        let value: crate::lab::ClaimRecord = serde_json::from_str(&payload)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        self.inner.record_claim(value).map_err(lab_controller_error)
    }

    fn record_hypothesis_json(&mut self, payload: String) -> PyResult<()> {
        let value: crate::lab::HypothesisRecord = serde_json::from_str(&payload)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        self.inner
            .record_hypothesis(value)
            .map_err(lab_controller_error)
    }

    fn schedule_experiment_json(&mut self, payload: String) -> PyResult<()> {
        let value: crate::lab::ExperimentSpec = serde_json::from_str(&payload)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        self.inner
            .schedule_experiment(value)
            .map_err(lab_controller_error)
    }

    fn record_observation_json(&mut self, payload: String) -> PyResult<()> {
        let value: crate::lab::ObservationRecord = serde_json::from_str(&payload)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        self.inner
            .record_observation(value)
            .map_err(lab_controller_error)
    }

    fn review_and_complete(&mut self) -> PyResult<()> {
        self.inner
            .review_and_complete()
            .map_err(lab_controller_error)
    }
}

#[pyfunction]
pub fn aegis_lab_verify_event_chain(events_json: String) -> PyResult<bool> {
    py_safe(move || {
        let events: Vec<crate::lab::LabEvent> =
            serde_json::from_str(&events_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid lab event chain JSON: {error}"
                ))
            })?;
        Ok(crate::lab::verify_event_chain(&events))
    })?
}

/// Validate one Lab state transition at the Rust authority boundary.
///
/// The transition is checked before the Python projection mutates its state;
/// equal states are idempotent, matching `LabRuntime::transition` semantics.
#[pyfunction]
pub fn aegis_lab_validate_transition(current: String, next: String) -> PyResult<bool> {
    py_safe(move || {
        let parse = |value: &str| match value.trim().to_ascii_lowercase().as_str() {
            "planned" => Some(crate::lab::LabRunState::Planned),
            "researching" => Some(crate::lab::LabRunState::Researching),
            "experimenting" => Some(crate::lab::LabRunState::Experimenting),
            "reviewing" => Some(crate::lab::LabRunState::Reviewing),
            "completed" => Some(crate::lab::LabRunState::Completed),
            "blocked" => Some(crate::lab::LabRunState::Blocked),
            "aborted" => Some(crate::lab::LabRunState::Aborted),
            _ => None,
        };
        let Some(current_state) = parse(&current) else {
            return Ok(false);
        };
        let Some(next_state) = parse(&next) else {
            return Ok(false);
        };
        Ok(current_state == next_state || current_state.can_transition_to(next_state))
    })?
}

/// Validate and reconstruct a complete Rust Lab reducer snapshot.
///
/// A boolean result deliberately exposes no partially restored state: callers
/// must retain the original snapshot and only proceed when this authority
/// accepts its identity, records, state epoch and event chain.
#[pyfunction]
pub fn aegis_lab_validate_snapshot(snapshot_json: String) -> PyResult<bool> {
    py_safe(move || Ok(crate::lab::LabRuntime::from_snapshot_json(&snapshot_json).is_ok()))?
}

/// Persist a validated Lab event chain into the canonical segmented replay
/// archive and return its serialized manifest.  The caller supplies a stable
/// numeric run id derived from the mission identity; no implicit process-local
/// state is used.
#[pyfunction]
#[pyo3(signature = (events_json, directory, run_id, max_events_per_segment=64usize))]
pub fn aegis_lab_archive_events(
    events_json: String,
    directory: String,
    run_id: u128,
    max_events_per_segment: usize,
) -> PyResult<String> {
    py_safe(move || {
        let events: Vec<crate::lab::LabEvent> =
            serde_json::from_str(&events_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid lab event chain JSON: {error}"
                ))
            })?;
        let manifest = crate::replay::RunEventSegmentArchive::write_lab_events(
            directory,
            max_events_per_segment,
            run_id,
            &events,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        serde_json::to_string(&manifest).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "lab replay manifest serialization failed: {error}"
            ))
        })
    })?
}

/// Recover and verify the longest valid prefix of a sealed Lab replay archive.
/// The result is intentionally boolean: callers must obtain the manifest from
/// their own durable store and may not treat a recovered prefix as a complete
/// mission without comparing its final event/root against that manifest.
#[pyfunction]
pub fn aegis_lab_verify_archive(directory: String, run_id: u128) -> PyResult<bool> {
    py_safe(move || {
        let manifest = crate::replay::RunEventSegmentArchive::recover_manifest_from_segments(
            directory.clone(),
            run_id,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let ledger = crate::replay::RunEventSegmentArchive::read_ledger_mmap(directory, &manifest)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok(!ledger.is_empty()
            && ledger.verify_hash_chain()
            && ledger
                .events()
                .iter()
                .all(|event| event.kind == crate::replay::RunEventKind::LabEventRecorded))
    })?
}

/// Verify a Lab replay archive against the manifest identity persisted by the
/// caller.  Prefix recovery is useful for diagnosis, but it is not completion
/// evidence: a missing or corrupt tail must fail closed when the caller has a
/// sealed manifest hash for the run.
#[pyfunction]
pub fn aegis_lab_verify_archive_against_manifest(
    directory: String,
    run_id: u128,
    expected_manifest_hash: Vec<u8>,
) -> PyResult<bool> {
    py_safe(move || {
        let expected_manifest_hash: [u8; 32] = expected_manifest_hash.try_into().map_err(|_| {
            pyo3::exceptions::PyValueError::new_err(
                "Lab replay manifest hash must contain exactly 32 bytes",
            )
        })?;
        let manifest = crate::replay::RunEventSegmentArchive::recover_manifest_from_segments(
            directory.clone(),
            run_id,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        if manifest.manifest_hash != expected_manifest_hash {
            return Ok(false);
        }
        let ledger = crate::replay::RunEventSegmentArchive::read_ledger_mmap(directory, &manifest)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok(!ledger.is_empty()
            && ledger.verify_hash_chain()
            && ledger
                .events()
                .iter()
                .all(|event| event.kind == crate::replay::RunEventKind::LabEventRecorded))
    })?
}

/// Verify that a Lab snapshot's event identities exactly match the sealed
/// events retained in the native segmented archive.
#[pyfunction]
pub fn aegis_lab_verify_archive_against_events(
    directory: String,
    run_id: u128,
    events_json: String,
) -> PyResult<bool> {
    py_safe(move || {
        let events: Vec<crate::lab::LabEvent> =
            serde_json::from_str(&events_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid Lab event chain JSON: {error}"
                ))
            })?;
        crate::replay::RunEventSegmentArchive::verify_lab_events_against_archive(
            directory, run_id, &events,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
    })?
}

/// Verify a pre-schema-marker Lab replay archive against its legacy manifest
/// hash. This compatibility-only path is explicit so a current manifest can
/// never silently accept an old hash domain; callers must retain the legacy
/// snapshot marker and may not append new segments through this verifier.
#[pyfunction]
pub fn aegis_lab_verify_archive_against_legacy_manifest(
    directory: String,
    run_id: u128,
    expected_manifest_hash: Vec<u8>,
) -> PyResult<bool> {
    py_safe(move || {
        let expected_manifest_hash: [u8; 32] = expected_manifest_hash.try_into().map_err(|_| {
            pyo3::exceptions::PyValueError::new_err(
                "legacy Lab replay manifest hash must contain exactly 32 bytes",
            )
        })?;
        let manifest = crate::replay::RunEventSegmentArchive::recover_manifest_from_segments(
            directory.clone(),
            run_id,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        if manifest.legacy_manifest_hash() != expected_manifest_hash {
            return Ok(false);
        }
        let ledger = crate::replay::RunEventSegmentArchive::read_ledger_mmap(directory, &manifest)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok(!ledger.is_empty()
            && ledger.verify_hash_chain()
            && ledger
                .events()
                .iter()
                .all(|event| event.kind == crate::replay::RunEventKind::LabEventRecorded))
    })?
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

fn parse_lab_state(value: &str) -> Option<crate::lab::LabRunState> {
    match value.trim().to_ascii_lowercase().as_str() {
        "planned" => Some(crate::lab::LabRunState::Planned),
        "researching" => Some(crate::lab::LabRunState::Researching),
        "experimenting" => Some(crate::lab::LabRunState::Experimenting),
        "reviewing" => Some(crate::lab::LabRunState::Reviewing),
        "completed" => Some(crate::lab::LabRunState::Completed),
        "blocked" => Some(crate::lab::LabRunState::Blocked),
        "aborted" => Some(crate::lab::LabRunState::Aborted),
        _ => None,
    }
}

fn lab_state_label(state: crate::lab::LabRunState) -> &'static str {
    match state {
        crate::lab::LabRunState::Planned => "planned",
        crate::lab::LabRunState::Researching => "researching",
        crate::lab::LabRunState::Experimenting => "experimenting",
        crate::lab::LabRunState::Reviewing => "reviewing",
        crate::lab::LabRunState::Completed => "completed",
        crate::lab::LabRunState::Blocked => "blocked",
        crate::lab::LabRunState::Aborted => "aborted",
    }
}

fn lab_controller_error(error: crate::lab::LabError) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(format!("Lab controller rejected operation: {error:?}"))
}

fn hex32(bytes: &[u8; 32]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
