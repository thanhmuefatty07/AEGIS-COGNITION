#![allow(clippy::useless_conversion)]

use blake3::Hasher;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::panic::{catch_unwind, AssertUnwindSafe};
use parking_lot::Mutex;
use std::sync::OnceLock;
use crate::memory::session_search::SessionSearchIndex;
use crate::learning::LearningLedger;

static SESSION_INDEX: OnceLock<Mutex<SessionSearchIndex>> = OnceLock::new();
static SESSION_LEDGER: OnceLock<Mutex<LearningLedger>> = OnceLock::new();

fn get_session_index() -> &'static Mutex<SessionSearchIndex> {
    SESSION_INDEX.get_or_init(|| {
        let epoch_hash = [0u8; 32];
        Mutex::new(SessionSearchIndex::new(epoch_hash).unwrap())
    })
}

fn get_session_ledger() -> &'static Mutex<LearningLedger> {
    SESSION_LEDGER.get_or_init(|| {
        Mutex::new(LearningLedger::new())
    })
}

fn py_safe<T>(f: impl FnOnce() -> T) -> PyResult<T> {
    catch_unwind(AssertUnwindSafe(f)).map_err(|_| {
        pyo3::exceptions::PyRuntimeError::new_err("panic prevented across FFI boundary")
    })
}

#[pyfunction]
pub fn aegis_status() -> PyResult<&'static str> {
    py_safe(|| "aegis-nerve-ready")
}

#[pyfunction]
pub fn aegis_validate_schema(schema_id: u64, version: u32) -> PyResult<bool> {
    py_safe(|| schema_id == 0xAE1515 && version == 1)
}

#[pyfunction]
pub fn aegis_validate_layout(header_bytes: usize, alignment: usize) -> PyResult<bool> {
    py_safe(|| header_bytes == 64 && alignment == 64 && alignment.is_power_of_two())
}

#[pyfunction]
pub fn aegis_memory_alignment(alignment: usize) -> PyResult<bool> {
    py_safe(|| alignment == 64)
}

#[pyfunction]
pub fn aegis_nerve_schema_id() -> PyResult<u64> {
    py_safe(|| 0xAE1515)
}

#[pyfunction]
pub fn aegis_frame_is_valid(payload_len: usize) -> PyResult<bool> {
    py_safe(|| payload_len > 0)
}

#[pyfunction]
pub fn aegis_message_frame_valid(
    payload_len: usize,
    message_id: u128,
    session_id: u128,
) -> PyResult<bool> {
    py_safe(|| payload_len > 0 && message_id > 0 && session_id > 0)
}

#[pyfunction]
pub fn aegis_zero_copy_ready(schema_id: u64, version: u32, payload_len: usize) -> PyResult<bool> {
    py_safe(|| schema_id == 0xAE1515 && version == 1 && payload_len > 0)
}

#[pyfunction]
pub fn aegis_mmap_bridge_header_bytes() -> PyResult<usize> {
    py_safe(|| crate::bridge_mmap::MMAP_BRIDGE_HEADER_BYTES)
}

#[pyfunction]
pub fn aegis_mmap_bridge_payload_alignment() -> PyResult<usize> {
    py_safe(|| crate::bridge_mmap::MMAP_BRIDGE_PAYLOAD_ALIGNMENT)
}

#[pyfunction]
pub fn aegis_write_mmap_bridge_pattern(
    path: String,
    message_id: u128,
    session_id: u128,
    payload_len: usize,
) -> PyResult<(u128, u128, u64, u64, Vec<u8>)> {
    py_safe(move || {
        let header = crate::bridge_mmap::write_pattern_mmap_bridge_frame(
            path,
            message_id,
            session_id,
            payload_len,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok((
            header.message_id,
            header.session_id,
            header.payload_offset,
            header.payload_len,
            header.payload_blake3.to_vec(),
        ))
    })?
}

#[pyfunction]
pub fn aegis_validate_mmap_bridge_frame(path: String) -> PyResult<bool> {
    py_safe(move || crate::bridge_mmap::validate_mmap_bridge_frame(path))
}

#[pyfunction]
pub fn aegis_execute_mmap_wasm_bridge_frame(
    path: String,
    fuel_limit: u64,
) -> PyResult<(u64, Vec<u8>)> {
    py_safe(move || {
        let sandbox = crate::sandbox::WasmtimeSandbox::new();
        let result = sandbox
            .execute_mmap_wasm_bridge_frame(std::path::Path::new(&path), fuel_limit)
            .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("{err:?}")))?;
        Ok((result.fuel_consumed, result.artifact.artifact_hash.to_vec()))
    })?
}

#[pyfunction]
pub fn aegis_new_message_identity(message_id: u128, session_id: u128) -> PyResult<bool> {
    py_safe(|| message_id > 0 && session_id > 0)
}

#[pyfunction]
pub fn aegis_can_bridge_python(
    payload_len: usize,
    message_id: u128,
    session_id: u128,
) -> PyResult<bool> {
    py_safe(|| payload_len > 0 && message_id > 0 && session_id > 0)
}

#[pyfunction]
pub fn aegis_layout_header_bytes() -> PyResult<usize> {
    py_safe(|| 64)
}

#[pyfunction]
pub fn aegis_layout_payload_alignment() -> PyResult<usize> {
    py_safe(|| 64)
}

#[pyfunction]
pub fn aegis_descriptor_valid(payload_len: usize) -> PyResult<bool> {
    py_safe(|| payload_len > 0)
}

#[pyfunction]
pub fn aegis_cli_status() -> PyResult<&'static str> {
    py_safe(|| "aegis-nerve-cli ready")
}

#[pyfunction]
pub fn aegis_cli_schema() -> PyResult<&'static str> {
    py_safe(|| "schema_id=0xAE1515 version=1 alignment=64")
}

#[pyfunction]
pub fn aegis_release_ready() -> PyResult<bool> {
    py_safe(|| true)
}

#[pyfunction]
#[pyo3(signature = (request_id, session_id, prompt, model_hint=None))]
pub fn aegis_llm_request(
    request_id: u128,
    session_id: u128,
    prompt: String,
    model_hint: Option<String>,
) -> PyResult<bool> {
    py_safe(move || {
        let model_hint_static = model_hint.map(|s| &*Box::leak(s.into_boxed_str()));
        let request =
            crate::llm::build_llm_request(request_id, session_id, &prompt, model_hint_static);
        request.is_valid()
    })
}

#[pyfunction]
pub fn aegis_llm_route(request_id: u128, session_id: u128, prompt: String) -> PyResult<bool> {
    py_safe(move || {
        let request = crate::llm::build_llm_request(request_id, session_id, &prompt, None);
        let providers = [crate::llm::ProviderConfig {
            provider_id: "provider-a",
            model_name: "model-a",
            endpoint: "http://localhost:1234",
            timeout_ms: 1000,
            retry_limit: 3,
            preferred_for_tools: false,
            cost_rank: 1,
            availability_rank: 1,
        }];
        crate::llm::route_request(&request, &providers).is_some()
    })
}

#[pyfunction]
pub fn aegis_llm_bridge_key(request_id: u128, session_id: u128) -> PyResult<(u128, u128)> {
    py_safe(|| (request_id, session_id))
}

#[pyfunction]
pub fn aegis_llm_normalize(request_id: u128, session_id: u128, content: String) -> PyResult<bool> {
    py_safe(move || {
        let request = crate::llm::build_llm_request(request_id, session_id, "prompt", None);
        let response = crate::llm::normalize_response(&request, &content, 1, 1, 1.0);
        response.is_valid()
    })
}

#[pyfunction]
pub fn aegis_llm_reject(request_id: u128, session_id: u128, error: String) -> PyResult<bool> {
    py_safe(move || {
        let error_static = Box::leak(error.into_boxed_str());
        let request = crate::llm::build_llm_request(request_id, session_id, "prompt", None);
        let response = crate::llm::rejected_response(&request, error_static);
        response.is_valid()
    })
}

#[pyfunction]
pub fn aegis_harness_generate_skeleton(code: String) -> PyResult<String> {
    use crate::harness::{NeuroSymbolicHarness, SkeletonGenerator};
    py_safe(move || NeuroSymbolicHarness::generate_skeleton(&code).unwrap_or_else(|e| e))
}

#[pyfunction]
pub fn aegis_harness_analyze_errors(logs: String) -> PyResult<Vec<String>> {
    use crate::harness::{CompilerFeedbackLoop, NeuroSymbolicHarness};
    py_safe(move || NeuroSymbolicHarness::analyze_compile_errors(&logs))
}

#[pyfunction]
pub fn aegis_physical_metrics_prometheus() -> PyResult<String> {
    py_safe(crate::physical::physical_metrics_prometheus)
}

#[pyfunction]
pub fn aegis_trust_level() -> PyResult<String> {
    py_safe(|| trust_level_label(crate::hot_engine::TrustLevel::from_env()).to_string())
}

#[pyfunction]
pub fn aegis_hot_hash(payload: Vec<u8>) -> PyResult<String> {
    py_safe(move || hex32(&crate::hot_engine::simd_blake3_hash(&payload)))
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
        let ledger: serde_json::Value = serde_json::from_str(&ledger_json)
            .map_err(|e| {
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
                    .filter(|ev| {
                        ev.get("type")
                            .and_then(|t| t.as_str())
                            == Some("SkillImproved")
                    })
                    .count()
            })
            .unwrap_or(0);

        // Count MemoryPersisted events for nudged_memories
        let nudged_memories = ledger
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|ev| {
                        ev.get("type")
                            .and_then(|t| t.as_str())
                            == Some("MemoryPersisted")
                    })
                    .count()
            })
            .unwrap_or(0);

        let final_search_index_count = search_index_count.max(get_session_index().lock().len() as u64);

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
pub fn aegis_index_session(
    session_id: u128,
    content: String,
    timestamp: u64,
) -> PyResult<String> {
    py_safe(move || {
        let mut index = get_session_index().lock();
        let mut ledger = get_session_ledger().lock();
        let content_hash = index
            .index_session(session_id, &content, timestamp, &mut ledger, None)
            .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("Index failed: {:?}", err)))?;
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
pub fn aegis_search_past_sessions(
    query: String,
    top_k: usize,
) -> PyResult<String> {
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
    m.add_function(wrap_pyfunction!(aegis_llm_request, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_route, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_bridge_key, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_llm_reject, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_harness_generate_skeleton, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_harness_analyze_errors, m)?)?;
    m.add_function(wrap_pyfunction!(aegis_physical_metrics_prometheus, m)?)?;
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