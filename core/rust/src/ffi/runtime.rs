//! Resource admission and process-local runtime bindings.
//!
//! The wrappers expose the existing Rust resource/runtime authority.  They do
//! not create a second scheduler or normalize caller metadata implicitly.

use super::{
    CooperativeAdmissionState, get_authoritative_runtime, get_cooperative_admission, hex32, py_safe,
};
use crate::resource::ResourceController;
use parking_lot::Mutex;
use pyo3::prelude::*;
use std::collections::BTreeMap;
use std::sync::OnceLock;

const MAX_RUNTIME_INPUT_JSON_BYTES: usize = 1024 * 1024;
const MAX_RUNTIME_DEPENDENCY_IDS: usize = 4_096;
const COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1: &str = "aegis-cooperative-placement-preview-v1";
const COOPERATIVE_ADMISSION_SCHEMA_V1: &str = "aegis-cooperative-admission-v1";
// The PyO3 registration makes these support items reachable in extension
// builds. In a no-feature Rust library build they remain available to the
// internal tests, so keep the dead-code exception scoped to that build mode.
#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
const COOPERATIVE_RUNTIME_SCHEMA_V1: &str = "aegis-cooperative-runtime-v1";

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
struct CooperativeRuntimeHandleTable {
    next_handle: u64,
    tokens: BTreeMap<u64, crate::runtime::CooperativeLeaseToken>,
}

impl Default for CooperativeRuntimeHandleTable {
    fn default() -> Self {
        Self {
            next_handle: 1,
            tokens: BTreeMap::new(),
        }
    }
}

impl CooperativeRuntimeHandleTable {
    #[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
    fn insert_or_reuse(
        &mut self,
        token: crate::runtime::CooperativeLeaseToken,
    ) -> Result<u64, String> {
        if let Some((handle, _)) = self.tokens.iter().find(|(_, current)| *current == &token) {
            return Ok(*handle);
        }
        let handle = self.next_handle;
        if handle == 0 {
            return Err("cooperative runtime handle space is exhausted".to_string());
        }
        self.next_handle = handle
            .checked_add(1)
            .ok_or_else(|| "cooperative runtime handle space is exhausted".to_string())?;
        self.tokens.insert(handle, token);
        Ok(handle)
    }
}

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
static COOPERATIVE_RUNTIME_HANDLES: OnceLock<Mutex<CooperativeRuntimeHandleTable>> =
    OnceLock::new();
#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
static COOPERATIVE_RUNTIME_BRIDGE: OnceLock<Mutex<()>> = OnceLock::new();

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
fn cooperative_runtime_handles() -> &'static Mutex<CooperativeRuntimeHandleTable> {
    COOPERATIVE_RUNTIME_HANDLES.get_or_init(|| Mutex::new(CooperativeRuntimeHandleTable::default()))
}

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
fn cooperative_runtime_bridge() -> &'static Mutex<()> {
    COOPERATIVE_RUNTIME_BRIDGE.get_or_init(|| Mutex::new(()))
}

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
fn parse_cooperative_runtime_mode(value: &str) -> PyResult<crate::runtime::CooperativeRuntimeMode> {
    match value.trim().to_ascii_lowercase().as_str() {
        "disabled" => Ok(crate::runtime::CooperativeRuntimeMode::Disabled),
        "shadow" => Ok(crate::runtime::CooperativeRuntimeMode::Shadow),
        "enforced" => Ok(crate::runtime::CooperativeRuntimeMode::Enforced),
        _ => Err(pyo3::exceptions::PyValueError::new_err(
            "cooperative runtime mode must be disabled, shadow, or enforced",
        )),
    }
}

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
fn cooperative_runtime_mode_label(mode: crate::runtime::CooperativeRuntimeMode) -> &'static str {
    match mode {
        crate::runtime::CooperativeRuntimeMode::Disabled => "disabled",
        crate::runtime::CooperativeRuntimeMode::Shadow => "shadow",
        crate::runtime::CooperativeRuntimeMode::Enforced => "enforced",
    }
}

#[cfg_attr(not(feature = "python-extension"), allow(dead_code))]
fn parse_runtime_outcome(value: String) -> PyResult<crate::runtime::RuntimeOutcome> {
    match value.trim().to_ascii_lowercase().as_str() {
        "done" => Ok(crate::runtime::RuntimeOutcome::Done),
        "failed" => Ok(crate::runtime::RuntimeOutcome::Failed),
        "retry_wait" | "retry-wait" => Ok(crate::runtime::RuntimeOutcome::RetryWait),
        "timed_out" | "timed-out" | "timeout" => Ok(crate::runtime::RuntimeOutcome::TimedOut),
        "cancelled" | "canceled" => Ok(crate::runtime::RuntimeOutcome::Cancelled),
        _ => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "invalid runtime outcome: {value}"
        ))),
    }
}

fn ensure_runtime_json_bound(value: &str, field: &'static str) -> PyResult<()> {
    if value.len() > MAX_RUNTIME_INPUT_JSON_BYTES {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{field} exceeds the bounded runtime input size",
        )));
    }
    Ok(())
}

fn parse_cooperative_inventory(
    capabilities_json: &str,
    paths_json: &str,
) -> PyResult<(
    Vec<crate::placement::PlacementCapability>,
    Vec<crate::placement::PlacementTransferPath>,
    [u8; 32],
)> {
    ensure_runtime_json_bound(capabilities_json, "capabilities_json")?;
    ensure_runtime_json_bound(paths_json, "paths_json")?;
    let mut capabilities: Vec<crate::placement::PlacementCapability> =
        serde_json::from_str(capabilities_json).map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid cooperative placement capabilities JSON: {error}"
            ))
        })?;
    let mut paths: Vec<crate::placement::PlacementTransferPath> = serde_json::from_str(paths_json)
        .map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid cooperative placement paths JSON: {error}"
            ))
        })?;
    capabilities.sort_by(|left, right| left.id.cmp(&right.id));
    paths.sort_by(|left, right| left.id.cmp(&right.id));
    let digest_input = serde_json::to_vec(&(&capabilities, &paths)).map_err(|error| {
        pyo3::exceptions::PyRuntimeError::new_err(format!(
            "cooperative inventory serialization failed: {error}"
        ))
    })?;
    let inventory_hash = *blake3::hash(&digest_input).as_bytes();
    Ok((capabilities, paths, inventory_hash))
}

fn cooperative_plan_digest(
    plan: &crate::placement::CooperativePlacementPlan,
) -> PyResult<[u8; 32]> {
    // Planning time is an observation timestamp, not an idempotency input.
    // Excluding it lets a retried `(task_id, attempt_id)` use the same fence
    // after a caller recomputes an otherwise identical plan.
    let mut canonical = plan.clone();
    canonical.planned_at_ms = 0;
    let bytes = serde_json::to_vec(&canonical).map_err(|error| {
        pyo3::exceptions::PyRuntimeError::new_err(format!(
            "cooperative plan serialization failed: {error}"
        ))
    })?;
    Ok(*blake3::hash(&bytes).as_bytes())
}

fn cooperative_spill_paths(
    plan: &crate::placement::CooperativePlacementPlan,
) -> PyResult<std::collections::BTreeMap<String, String>> {
    let mut spill_paths = std::collections::BTreeMap::new();
    for buffer in &plan.buffers {
        let Some(spill) = &buffer.spill_reservation else {
            continue;
        };
        if let Some(previous) =
            spill_paths.insert(spill.storage_id.clone(), spill.transfer_path_id.clone())
        {
            if previous != spill.transfer_path_id {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "cooperative plan maps one storage identity to multiple spill paths",
                ));
            }
        }
    }
    Ok(spill_paths)
}

/// Return the normalized hardware contract used by the authoritative runtime.
#[pyfunction]
pub fn aegis_hardware_profile() -> PyResult<String> {
    py_safe(|| {
        serde_json::to_string(&crate::resource::HardwareProfile::probe()).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "hardware profile serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_resource_contract_version() -> PyResult<&'static str> {
    py_safe(|| crate::resource::RESOURCE_CONTRACT_SCHEMA_V1)
}

/// Validate and preview admission for one typed resource request.
#[pyfunction]
#[pyo3(signature = (request_json, now_ms=None))]
pub fn aegis_resource_admission_preview(
    request_json: String,
    now_ms: Option<u64>,
) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&request_json, "request_json")?;
        let request: crate::resource::ResourceRequest = serde_json::from_str(&request_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource request JSON: {error}"
                ))
            })?;
        let profile = crate::resource::HardwareProfile::probe();
        let mut controller = crate::resource::AdmissionController::from_hardware(&profile);
        let decision = controller.admit(request, now_ms.unwrap_or(profile.profile_epoch));
        serde_json::to_string(&decision).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "admission decision serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_execution_lanes() -> PyResult<String> {
    py_safe(|| {
        let profile = crate::resource::HardwareProfile::probe();
        serde_json::to_string(&crate::resource::ExecutionLaneRegistry::for_profile(
            &profile,
        ))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "execution lane serialization failed: {error}"
            ))
        })
    })?
}

/// Return the probed resource inventory in the placement contract.  Missing
/// throughput/bandwidth values are deliberately serialized as null so the
/// planner can defer rather than inventing a performance advantage.
#[pyfunction]
pub fn aegis_placement_capabilities() -> PyResult<String> {
    py_safe(|| {
        let profile = crate::resource::HardwareProfile::probe();
        serde_json::to_string(&profile.placement_capabilities()).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "placement capability serialization failed: {error}"
            ))
        })
    })?
}

/// Run bounded opt-in CPU/filesystem calibration for placement cost inputs.
#[pyfunction]
#[pyo3(signature = (cpu_iterations, storage_bytes=None))]
pub fn aegis_placement_calibrate(
    cpu_iterations: u64,
    storage_bytes: Option<u64>,
) -> PyResult<String> {
    py_safe(move || {
        let calibration =
            crate::placement::PlacementCalibration::run(cpu_iterations, storage_bytes).map_err(
                |error| {
                    pyo3::exceptions::PyValueError::new_err(format!(
                        "placement calibration failed: {error:?}"
                    ))
                },
            )?;
        serde_json::to_string(&calibration).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "placement calibration serialization failed: {error}"
            ))
        })
    })?
}

/// Build a conservative placement plan from caller-supplied observations.
/// The native runtime does not infer missing device measurements.
#[pyfunction]
pub fn aegis_placement_plan(
    task_json: String,
    capabilities_json: String,
    now_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&task_json, "task_json")?;
        ensure_runtime_json_bound(&capabilities_json, "capabilities_json")?;
        let task: crate::placement::PlacementTask =
            serde_json::from_str(&task_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid placement task JSON: {error}"
                ))
            })?;
        let capabilities: Vec<crate::placement::PlacementCapability> =
            serde_json::from_str(&capabilities_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid placement capabilities JSON: {error}"
                ))
            })?;
        let plan = crate::placement::PlacementPlan::build(&task, &capabilities, now_ms).map_err(
            |error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "placement planning failed: {error:?}"
                ))
            },
        )?;
        serde_json::to_string(&plan).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "placement plan serialization failed: {error}"
            ))
        })
    })?
}

/// Configure the H2c-B cooperative lifecycle owned by AuthoritativeRuntime.
/// The FFI layer keeps only opaque handles; the runtime retains the lease.
#[pyfunction]
#[pyo3(signature = (mode, capabilities_json, paths_json))]
pub fn aegis_runtime_cooperative_configure(
    mode: String,
    capabilities_json: String,
    paths_json: String,
) -> PyResult<String> {
    py_safe(move || {
        let mode = parse_cooperative_runtime_mode(&mode)?;
        let (capabilities, paths, inventory_hash) =
            parse_cooperative_inventory(&capabilities_json, &paths_json)?;
        let _bridge_guard = cooperative_runtime_bridge().lock();
        if !cooperative_runtime_handles().lock().tokens.is_empty() {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "cooperative runtime cannot be reconfigured while handles are active",
            ));
        }
        get_authoritative_runtime()
            .lock()
            .configure_cooperative(mode, &capabilities, &paths)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "cooperative runtime configuration failed: {error:?}"
                ))
            })?;
        serde_json::to_string(&serde_json::json!({
            "schema": COOPERATIVE_RUNTIME_SCHEMA_V1,
            "authority": "authoritative_runtime",
            "status": "configured",
            "mode": cooperative_runtime_mode_label(mode),
            "executable": mode == crate::runtime::CooperativeRuntimeMode::Enforced,
            "inventory_hash": hex32(&inventory_hash),
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "cooperative runtime configuration serialization failed: {error}"
            ))
        })
    })?
}

/// Submit one already planned aggregate request to AuthoritativeRuntime.
/// No reservation or authoritative token is serialized across the FFI edge.
#[pyfunction]
pub fn aegis_runtime_cooperative_submit(request_json: String, now_ms: u64) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&request_json, "request_json")?;
        if now_ms == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "cooperative runtime now_ms must be non-zero",
            ));
        }
        let request: crate::resource::CooperativeAdmissionRequest =
            serde_json::from_str(&request_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid cooperative runtime request JSON: {error}"
                ))
            })?;
        let _bridge_guard = cooperative_runtime_bridge().lock();
        let admission = get_authoritative_runtime()
            .lock()
            .submit_cooperative(request, now_ms)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "cooperative runtime submit failed: {error:?}"
                ))
            })?;
        let response = match admission {
            crate::runtime::CooperativeRuntimeAdmission::Shadow => serde_json::json!({
                "schema": COOPERATIVE_RUNTIME_SCHEMA_V1,
                "authority": "authoritative_runtime",
                "status": "shadow",
                "executable": false,
                "reservation_status": "VALIDATED_NOT_ADMITTED",
                "handle": null,
            }),
            crate::runtime::CooperativeRuntimeAdmission::Admitted(token) => {
                let mut handles = cooperative_runtime_handles().lock();
                let token = (*token).clone();
                let handle = handles.insert_or_reuse(token.clone()).map_err(|error| {
                    drop(handles);
                    let _ = get_authoritative_runtime().lock().cancel_cooperative(token);
                    pyo3::exceptions::PyRuntimeError::new_err(error)
                })?;
                serde_json::json!({
                    "schema": COOPERATIVE_RUNTIME_SCHEMA_V1,
                    "authority": "authoritative_runtime",
                    "status": "admitted",
                    "executable": true,
                    "reservation_status": "ADMITTED",
                    "handle": handle,
                })
            }
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "cooperative runtime submit serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_runtime_cooperative_finish(handle: u64, outcome: String) -> PyResult<bool> {
    py_safe(move || {
        if handle == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "cooperative runtime handle must be non-zero",
            ));
        }
        let outcome = parse_runtime_outcome(outcome)?;
        let _bridge_guard = cooperative_runtime_bridge().lock();
        let token = cooperative_runtime_handles()
            .lock()
            .tokens
            .get(&handle)
            .cloned()
            .ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err(
                    "cooperative runtime handle is unknown or already released",
                )
            })?;
        get_authoritative_runtime()
            .lock()
            .finish_cooperative(token, outcome)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "cooperative runtime finish failed: {error:?}"
                ))
            })?;
        cooperative_runtime_handles().lock().tokens.remove(&handle);
        Ok(true)
    })?
}

#[pyfunction]
pub fn aegis_runtime_cooperative_cancel(handle: u64) -> PyResult<bool> {
    py_safe(move || {
        if handle == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "cooperative runtime handle must be non-zero",
            ));
        }
        let _bridge_guard = cooperative_runtime_bridge().lock();
        let token = cooperative_runtime_handles()
            .lock()
            .tokens
            .get(&handle)
            .cloned()
            .ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err(
                    "cooperative runtime handle is unknown or already released",
                )
            })?;
        get_authoritative_runtime()
            .lock()
            .cancel_cooperative(token)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "cooperative runtime cancel failed: {error:?}"
                ))
            })?;
        cooperative_runtime_handles().lock().tokens.remove(&handle);
        Ok(true)
    })?
}

/// Build a cooperative placement preview without reserving or submitting any
/// runtime resource.  The response contract deliberately makes that boundary
/// explicit so callers cannot mistake planning for execution admission.
#[pyfunction]
pub fn aegis_cooperative_placement_preview(
    task_json: String,
    capabilities_json: String,
    paths_json: String,
    now_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&task_json, "task_json")?;
        ensure_runtime_json_bound(&capabilities_json, "capabilities_json")?;
        ensure_runtime_json_bound(&paths_json, "paths_json")?;
        let task: crate::placement::CooperativePlacementTask = serde_json::from_str(&task_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid cooperative placement task JSON: {error}"
                ))
            })?;
        let capabilities: Vec<crate::placement::PlacementCapability> =
            serde_json::from_str(&capabilities_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid cooperative placement capabilities JSON: {error}"
                ))
            })?;
        let paths: Vec<crate::placement::PlacementTransferPath> = serde_json::from_str(&paths_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid cooperative placement paths JSON: {error}"
                ))
            })?;
        let plan =
            crate::placement::CooperativePlacementPlan::build(&task, &capabilities, &paths, now_ms)
                .map_err(|error| {
                    pyo3::exceptions::PyValueError::new_err(format!(
                        "cooperative placement preview failed: {error:?}"
                    ))
                })?;
        serde_json::to_string(&serde_json::json!({
            "schema": COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1,
            "authority": "planner_only",
            "executable": false,
            "reservation_status": "NOT_ADMITTED",
            "plan": plan,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "cooperative placement preview serialization failed: {error}"
            ))
        })
    })?
}

/// Build and admit one cooperative placement through the process-local Rust
/// ledger. Planning and admission share the sorted inventory supplied in the
/// same call, so a preview cannot silently become an admission against a
/// different capacity model.
#[pyfunction]
#[pyo3(signature = (task_json, capabilities_json, paths_json, attempt_id=1, now_ms=0))]
pub fn aegis_cooperative_placement_admit(
    task_json: String,
    capabilities_json: String,
    paths_json: String,
    attempt_id: u64,
    now_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&task_json, "task_json")?;
        if attempt_id == 0 || now_ms == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "cooperative admission attempt_id and now_ms must be non-zero",
            ));
        }
        let task: crate::placement::CooperativePlacementTask = serde_json::from_str(&task_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid cooperative placement task JSON: {error}"
                ))
            })?;
        let (capabilities, paths, inventory_hash) =
            parse_cooperative_inventory(&capabilities_json, &paths_json)?;
        let plan =
            crate::placement::CooperativePlacementPlan::build(&task, &capabilities, &paths, now_ms)
                .map_err(|error| {
                    pyo3::exceptions::PyValueError::new_err(format!(
                        "cooperative placement admission planning failed: {error:?}"
                    ))
                })?;
        if plan.decision != "selected" || plan.reservation_status != "READY_ALL_OR_NONE" {
            return serde_json::to_string(&serde_json::json!({
                "schema": COOPERATIVE_ADMISSION_SCHEMA_V1,
                "authority": "native_runtime",
                "status": "deferred",
                "executable": false,
                "reservation_status": plan.reservation_status,
                "inventory_hash": hex32(&inventory_hash),
                "plan": plan,
            }))
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "cooperative admission response serialization failed: {error}"
                ))
            });
        }

        let plan_hash = cooperative_plan_digest(&plan)?;
        let request = crate::resource::CooperativeAdmissionRequest {
            schema: crate::resource::COOPERATIVE_ADMISSION_SCHEMA_V1.to_string(),
            task_id: plan.task_id,
            attempt_id,
            plan_digest: hex32(&plan_hash),
            reservation: plan.reservation.clone(),
            spill_paths: cooperative_spill_paths(&plan)?,
        };
        let mut guard = get_cooperative_admission().lock();
        let needs_new_inventory = guard
            .as_ref()
            .is_none_or(|state| state.inventory_hash != inventory_hash);
        if needs_new_inventory {
            if guard
                .as_ref()
                .is_some_and(|state| state.ledger.active_leases() != 0)
            {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "cooperative admission inventory cannot change while leases are active",
                ));
            }
            let ledger =
                crate::resource::CooperativeAdmissionLedger::from_inventory(&capabilities, &paths)
                    .map_err(|error| {
                        pyo3::exceptions::PyValueError::new_err(format!(
                            "cooperative admission inventory rejected: {error:?}"
                        ))
                    })?;
            *guard = Some(CooperativeAdmissionState {
                inventory_hash,
                ledger,
            });
        }
        let Some(state) = guard.as_mut() else {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "cooperative admission state was not initialized",
            ));
        };
        let decision = state.ledger.admit(request, now_ms).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "cooperative admission failed: {error:?}"
            ))
        })?;
        let response = match decision {
            crate::resource::CooperativeAdmissionDecision::Admitted(lease) => {
                serde_json::json!({
                    "schema": COOPERATIVE_ADMISSION_SCHEMA_V1,
                    "authority": "native_runtime",
                    "status": "admitted",
                    "executable": true,
                    "reservation_status": "ADMITTED",
                    "inventory_hash": hex32(&inventory_hash),
                    "plan": plan,
                    "lease": lease,
                })
            }
            crate::resource::CooperativeAdmissionDecision::AlreadyAdmitted(lease) => {
                serde_json::json!({
                    "schema": COOPERATIVE_ADMISSION_SCHEMA_V1,
                    "authority": "native_runtime",
                    "status": "already_admitted",
                    "executable": true,
                    "reservation_status": "ADMITTED",
                    "inventory_hash": hex32(&inventory_hash),
                    "plan": plan,
                    "lease": lease,
                })
            }
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "cooperative admission response serialization failed: {error}"
            ))
        })
    })?
}

/// Release exactly one cooperative admission lease. The complete serialized
/// lease is compared by Rust, so a stale or forged reservation cannot reclaim
/// another attempt's capacity.
#[pyfunction]
pub fn aegis_cooperative_placement_release(lease_json: String) -> PyResult<bool> {
    py_safe(move || {
        ensure_runtime_json_bound(&lease_json, "lease_json")?;
        let lease: crate::resource::CooperativeAdmissionLease = serde_json::from_str(&lease_json)
            .map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "invalid cooperative admission lease JSON: {error}"
            ))
        })?;
        let mut guard = get_cooperative_admission().lock();
        let state = guard.as_mut().ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "cooperative admission runtime has no active inventory",
            )
        })?;
        state.ledger.release(&lease).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "cooperative admission release failed: {error:?}"
            ))
        })?;
        Ok(true)
    })?
}

/// Submit one task/resource proposal to the process-local authoritative runtime.
#[pyfunction]
pub fn aegis_runtime_submit(
    task_id: u128,
    dependency_ids_json: String,
    request_json: String,
    now_ms: u64,
) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&dependency_ids_json, "dependency_ids_json")?;
        let dependency_ids: Vec<crate::task_ledger::TaskId> =
            serde_json::from_str(&dependency_ids_json).map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid dependency IDs JSON: {error}"
                ))
            })?;
        if dependency_ids.len() > MAX_RUNTIME_DEPENDENCY_IDS {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "dependency_ids_json exceeds the bounded dependency count",
            ));
        }
        ensure_runtime_json_bound(&request_json, "request_json")?;
        let request: crate::resource::ResourceRequest = serde_json::from_str(&request_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource request JSON: {error}"
                ))
            })?;
        let task = crate::task_ledger::TaskCard::new(task_id, dependency_ids, 0, None, None);
        let mut runtime = get_authoritative_runtime().lock();
        let sample = crate::resource::PortableResourceController.sample();
        runtime.observe_resource_sample(&sample);
        let admission = runtime.submit(task, request, now_ms).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("runtime submit failed: {error:?}"))
        })?;
        let response = match admission {
            crate::runtime::RuntimeAdmission::Admitted(token) => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "admitted",
                "lease_token": token,
            }),
            crate::runtime::RuntimeAdmission::Queued { position, reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "queued",
                "position": position,
                "reason": reason,
            }),
            crate::runtime::RuntimeAdmission::Rejected { reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "rejected",
                "reason": reason,
            }),
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "runtime response serialization failed: {error}"
            ))
        })
    })?
}

/// Start a strictly newer attempt for a task that is in RetryWait.
#[pyfunction]
pub fn aegis_runtime_retry(task_id: u128, request_json: String, now_ms: u64) -> PyResult<String> {
    py_safe(move || {
        ensure_runtime_json_bound(&request_json, "request_json")?;
        let request: crate::resource::ResourceRequest = serde_json::from_str(&request_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource request JSON: {error}"
                ))
            })?;
        let mut runtime = get_authoritative_runtime().lock();
        let sample = crate::resource::PortableResourceController.sample();
        runtime.observe_resource_sample(&sample);
        let admission = runtime.retry(task_id, request, now_ms).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("runtime retry failed: {error:?}"))
        })?;
        let response = match admission {
            crate::runtime::RuntimeAdmission::Admitted(token) => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "admitted",
                "lease_token": token,
            }),
            crate::runtime::RuntimeAdmission::Queued { position, reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "queued",
                "position": position,
                "reason": reason,
            }),
            crate::runtime::RuntimeAdmission::Rejected { reason } => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "rejected",
                "reason": reason,
            }),
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "runtime retry response serialization failed: {error}"
            ))
        })
    })?
}

#[pyfunction]
pub fn aegis_runtime_finish(lease_token_json: String, outcome: String) -> PyResult<bool> {
    py_safe(move || {
        ensure_runtime_json_bound(&lease_token_json, "lease_token_json")?;
        let token: crate::resource::ResourceLeaseToken = serde_json::from_str(&lease_token_json)
            .map_err(|error| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid resource lease token JSON: {error}"
                ))
            })?;
        let mut runtime = get_authoritative_runtime().lock();
        let runtime_outcome = match outcome.trim().to_ascii_lowercase().as_str() {
            "done" => Ok(crate::runtime::RuntimeOutcome::Done),
            "failed" => Ok(crate::runtime::RuntimeOutcome::Failed),
            "retry_wait" | "retry-wait" => Ok(crate::runtime::RuntimeOutcome::RetryWait),
            "timed_out" | "timed-out" | "timeout" => Ok(crate::runtime::RuntimeOutcome::TimedOut),
            "cancelled" | "canceled" => Ok(crate::runtime::RuntimeOutcome::Cancelled),
            _ => Err(crate::runtime::RuntimeError::InvalidOutcome(outcome)),
        }
        .map_err(|error| {
            pyo3::exceptions::PyValueError::new_err(format!("invalid runtime outcome: {error:?}"))
        })?;
        runtime
            .finish(token, runtime_outcome)
            .map(|_| true)
            .map_err(|error| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "runtime finish failed: {error:?}"
                ))
            })
    })?
}

/// Poll the process-local bounded runtime queue for one task/attempt.
#[pyfunction]
pub fn aegis_runtime_poll(task_id: u128, attempt_id: u64, now_ms: u64) -> PyResult<String> {
    py_safe(move || {
        let mut runtime = get_authoritative_runtime().lock();
        let sample = crate::resource::PortableResourceController.sample();
        runtime.observe_resource_sample(&sample);
        let response = match runtime.poll_queued(task_id, attempt_id, now_ms) {
            Some(crate::runtime::RuntimeAdmission::Admitted(token)) => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "admitted",
                "lease_token": token,
            }),
            Some(crate::runtime::RuntimeAdmission::Queued { position, reason }) => {
                serde_json::json!({
                    "schema": "aegis-runtime-admission-v1",
                    "status": "queued",
                    "position": position,
                    "reason": reason,
                })
            }
            Some(crate::runtime::RuntimeAdmission::Rejected { reason }) => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "rejected",
                "reason": reason,
            }),
            None => serde_json::json!({
                "schema": "aegis-runtime-admission-v1",
                "status": "pending",
            }),
        };
        serde_json::to_string(&response).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "runtime poll response serialization failed: {error}"
            ))
        })
    })?
}

/// Cancel one queued task/attempt without touching admitted leases.
#[pyfunction]
pub fn aegis_runtime_cancel(task_id: u128, attempt_id: u64) -> PyResult<bool> {
    py_safe(move || {
        let mut runtime = get_authoritative_runtime().lock();
        runtime.cancel_queued(task_id, attempt_id).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "runtime queue cancellation failed: {error:?}"
            ))
        })
    })?
}

/// Return one observation-only resource sample.
#[pyfunction]
pub fn aegis_resource_usage_sample() -> PyResult<String> {
    py_safe(|| {
        let sample = crate::resource::PortableResourceController.sample();
        serde_json::to_string(&sample).map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "resource usage serialization failed: {error}"
            ))
        })
    })?
}

/// Observe host pressure and feed it into the authoritative admission headroom.
#[pyfunction]
pub fn aegis_runtime_observe_resources() -> PyResult<String> {
    py_safe(|| {
        let sample = crate::resource::PortableResourceController.sample();
        let mut runtime = get_authoritative_runtime().lock();
        let feedback = runtime.observe_resource_sample(&sample);
        serde_json::to_string(&serde_json::json!({
            "schema": crate::resource::RESOURCE_CONTRACT_SCHEMA_V1,
            "sample": sample,
            "feedback": feedback,
            "authoritative": true,
        }))
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "resource observation serialization failed: {error}"
            ))
        })
    })?
}

#[cfg(test)]
mod tests {
    use super::{
        COOPERATIVE_ADMISSION_SCHEMA_V1, COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1,
        COOPERATIVE_RUNTIME_SCHEMA_V1, aegis_cooperative_placement_admit,
        aegis_cooperative_placement_preview, aegis_cooperative_placement_release,
        aegis_placement_calibrate, aegis_placement_capabilities, aegis_placement_plan,
        aegis_resource_admission_preview, aegis_runtime_cooperative_cancel,
        aegis_runtime_cooperative_configure, aegis_runtime_cooperative_finish,
        aegis_runtime_cooperative_submit, aegis_runtime_submit,
    };
    use parking_lot::{Mutex, MutexGuard};
    use std::sync::OnceLock;

    fn cooperative_runtime_capabilities_json() -> String {
        serde_json::json!([
            {
                "id": "cpu-0",
                "domain": "Cpu",
                "usable_bytes": 1024,
                "compute_units_per_us": 1,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 2,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured",
                "transfer_paths": []
            },
            {
                "id": "ram-0",
                "domain": "HostMemory",
                "usable_bytes": 4096,
                "compute_units_per_us": null,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 2,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured",
                "transfer_paths": []
            }
        ])
        .to_string()
    }

    fn cooperative_runtime_request_json(
        task_id: u128,
        attempt_id: u64,
        plan_digest: &str,
    ) -> String {
        let mut reservation = crate::placement::CooperativeReservation::default();
        reservation
            .executor_queue_slots
            .insert("cpu-0".to_string(), 1);
        reservation
            .executor_memory_bytes
            .insert("cpu-0".to_string(), 128);
        serde_json::to_string(&crate::resource::CooperativeAdmissionRequest {
            schema: crate::resource::COOPERATIVE_ADMISSION_SCHEMA_V1.to_string(),
            task_id,
            attempt_id,
            plan_digest: plan_digest.to_string(),
            reservation,
            spill_paths: std::collections::BTreeMap::new(),
        })
        .expect("serialize cooperative runtime request")
    }

    fn configure_cooperative_runtime(mode: &str) -> serde_json::Value {
        let result = aegis_runtime_cooperative_configure(
            mode.to_string(),
            cooperative_runtime_capabilities_json(),
            "[]".to_string(),
        )
        .expect("configure cooperative runtime");
        serde_json::from_str(&result).expect("parse cooperative runtime configuration")
    }

    static COOPERATIVE_RUNTIME_TEST_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    fn cooperative_runtime_test_guard() -> MutexGuard<'static, ()> {
        COOPERATIVE_RUNTIME_TEST_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
    }

    #[test]
    fn resource_preview_rejects_oversized_request_before_json_parse() {
        let result = aegis_resource_admission_preview(
            "x".repeat(super::MAX_RUNTIME_INPUT_JSON_BYTES + 1),
            None,
        );
        assert!(result.is_err());
    }

    #[test]
    fn runtime_submit_rejects_oversized_dependencies_before_json_parse() {
        let result = aegis_runtime_submit(
            1,
            "x".repeat(super::MAX_RUNTIME_INPUT_JSON_BYTES + 1),
            "{}".to_owned(),
            0,
        );
        assert!(result.is_err());
    }

    #[test]
    fn runtime_submit_rejects_unbounded_dependency_count() {
        let dependency_ids: Vec<u128> =
            (1..=(super::MAX_RUNTIME_DEPENDENCY_IDS as u128 + 1)).collect();
        let dependency_ids_json = serde_json::to_string(&dependency_ids).expect("serialize IDs");
        let request_json = serde_json::to_string(&crate::resource::ResourceRequest::minimal(
            1,
            crate::resource::WorkKind::NativeTask,
        ))
        .expect("serialize request");
        let result = aegis_runtime_submit(1, dependency_ids_json, request_json, 0);
        assert!(result.is_err());
    }

    #[test]
    fn runtime_cooperative_bridge_disabled_fails_closed() {
        let _test_guard = cooperative_runtime_test_guard();
        let result = configure_cooperative_runtime("disabled");
        assert_eq!(result["schema"], COOPERATIVE_RUNTIME_SCHEMA_V1);
        assert_eq!(result["status"], "configured");
        assert_eq!(result["executable"], false);
        assert!(
            aegis_runtime_cooperative_submit(
                cooperative_runtime_request_json(9101, 1, "disabled-plan"),
                1_000,
            )
            .is_err()
        );
        assert!(aegis_runtime_cooperative_finish(99_999, "done".to_string()).is_err());
        assert!(aegis_runtime_cooperative_cancel(99_999).is_err());
    }

    #[test]
    fn runtime_cooperative_bridge_shadow_is_non_executable() {
        let _test_guard = cooperative_runtime_test_guard();
        let result = configure_cooperative_runtime("shadow");
        assert_eq!(result["mode"], "shadow");
        assert_eq!(result["executable"], false);

        let submission = aegis_runtime_cooperative_submit(
            cooperative_runtime_request_json(9102, 1, "shadow-plan"),
            2_000,
        )
        .expect("shadow submission should validate");
        let submission: serde_json::Value =
            serde_json::from_str(&submission).expect("parse shadow submission");
        assert_eq!(submission["schema"], COOPERATIVE_RUNTIME_SCHEMA_V1);
        assert_eq!(submission["authority"], "authoritative_runtime");
        assert_eq!(submission["status"], "shadow");
        assert_eq!(submission["executable"], false);
        assert_eq!(submission["reservation_status"], "VALIDATED_NOT_ADMITTED");
        assert!(submission["handle"].is_null());

        configure_cooperative_runtime("disabled");
    }

    #[test]
    fn runtime_cooperative_bridge_enforced_uses_opaque_idempotent_handle() {
        let _test_guard = cooperative_runtime_test_guard();
        let result = configure_cooperative_runtime("enforced");
        assert_eq!(result["mode"], "enforced");
        assert_eq!(result["executable"], true);
        let request = cooperative_runtime_request_json(9103, 1, "enforced-plan");

        let first = aegis_runtime_cooperative_submit(request.clone(), 3_000)
            .expect("first enforced submission");
        let first: serde_json::Value = serde_json::from_str(&first).expect("parse first handle");
        let second = aegis_runtime_cooperative_submit(request, 4_000)
            .expect("duplicate enforced submission");
        let second: serde_json::Value =
            serde_json::from_str(&second).expect("parse duplicate handle");
        assert_eq!(first["status"], "admitted");
        assert_eq!(first["executable"], true);
        assert_eq!(first["reservation_status"], "ADMITTED");
        assert!(first["handle"].as_u64().is_some_and(|handle| handle > 0));
        assert_eq!(first["handle"], second["handle"]);
        assert!(first.get("lease").is_none());
        assert!(first.get("token").is_none());

        let handle = first["handle"].as_u64().expect("opaque handle");
        assert!(aegis_runtime_cooperative_finish(handle, "done".to_string()).unwrap());
        assert!(aegis_runtime_cooperative_finish(handle, "done".to_string()).is_err());
        configure_cooperative_runtime("disabled");
    }

    #[test]
    fn runtime_cooperative_bridge_reconfiguration_is_fenced_until_release() {
        let _test_guard = cooperative_runtime_test_guard();
        configure_cooperative_runtime("enforced");
        let submission = aegis_runtime_cooperative_submit(
            cooperative_runtime_request_json(9104, 1, "fenced-plan"),
            5_000,
        )
        .expect("fenced submission");
        let submission: serde_json::Value = serde_json::from_str(&submission).unwrap();
        let handle = submission["handle"].as_u64().expect("fenced handle");

        assert!(
            aegis_runtime_cooperative_configure(
                "shadow".to_string(),
                cooperative_runtime_capabilities_json(),
                "[]".to_string(),
            )
            .is_err()
        );
        assert!(aegis_runtime_cooperative_cancel(handle).unwrap());
        let result = configure_cooperative_runtime("shadow");
        assert_eq!(result["status"], "configured");
        assert_eq!(result["mode"], "shadow");
        configure_cooperative_runtime("disabled");
    }

    #[test]
    fn placement_ffi_roundtrips_executor_and_data_tier() {
        let task = serde_json::json!({
            "schema": crate::placement::PLACEMENT_SCHEMA_V1,
            "task_id": 7,
            "input_bytes": 64,
            "output_bytes": 16,
            "working_memory_bytes": 64,
            "cpu_work_units": 100,
            "accelerator_work_units": 0,
            "deadline_ms": null,
            "requires_accelerator": false,
            "allow_storage": true,
            "privacy_local_only": true
        });
        let capabilities = serde_json::json!([
            {
                "id": "cpu",
                "domain": "Cpu",
                "usable_bytes": 4096,
                "compute_units_per_us": 10,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 4,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured"
            },
            {
                "id": "ram-hot",
                "domain": "HostMemory",
                "usable_bytes": 4096,
                "compute_units_per_us": null,
                "bandwidth_bytes_per_s": null,
                "latency_us": 0,
                "queue_depth": 0,
                "queue_capacity": null,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured"
            }
        ]);
        let result = aegis_placement_plan(
            serde_json::to_string(&task).unwrap(),
            serde_json::to_string(&capabilities).unwrap(),
            1_000,
        )
        .unwrap();
        let result: serde_json::Value = serde_json::from_str(&result).unwrap();
        assert_eq!(result["decision"], "selected");
        assert_eq!(result["executor"], "cpu");
        assert_eq!(result["data_tier"], "ram-hot");
    }

    #[test]
    fn cooperative_placement_preview_is_explicitly_non_executable() {
        let task = serde_json::json!({
            "schema": crate::placement::COOPERATIVE_PLACEMENT_TASK_SCHEMA_V1,
            "task_id": 7,
            "mode": "ParallelSlices",
            "slices": [
                {
                    "id": "first",
                    "stage": 0,
                    "depends_on": [],
                    "executor_domain": "Cpu",
                    "data_tier_domain": "HostMemory",
                    "required_data_tier_id": null,
                    "input_bytes": 1,
                    "output_bytes": 1,
                    "working_memory_bytes": 64,
                    "work_units": 10
                },
                {
                    "id": "second",
                    "stage": 0,
                    "depends_on": [],
                    "executor_domain": "Cpu",
                    "data_tier_domain": "HostMemory",
                    "required_data_tier_id": null,
                    "input_bytes": 1,
                    "output_bytes": 1,
                    "working_memory_bytes": 64,
                    "work_units": 10
                }
            ],
            "buffer_policy": {
                "capacity_bytes": 4,
                "max_in_flight_buffers": 2,
                "high_watermark_bytes": 3,
                "low_watermark_bytes": 1,
                "allow_spill_to_storage": false
            },
            "deadline_ms": null,
            "requires_local_only": true,
            "allow_storage": false
        });
        let capabilities = serde_json::json!([
            {
                "id": "cpu",
                "domain": "Cpu",
                "usable_bytes": 4096,
                "compute_units_per_us": 10,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 4,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured"
            },
            {
                "id": "ram-hot",
                "domain": "HostMemory",
                "usable_bytes": 4096,
                "compute_units_per_us": null,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 4,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured"
            }
        ]);
        let result = aegis_cooperative_placement_preview(
            serde_json::to_string(&task).unwrap(),
            serde_json::to_string(&capabilities).unwrap(),
            "[]".to_owned(),
            1_000,
        )
        .unwrap();
        let result: serde_json::Value = serde_json::from_str(&result).unwrap();
        assert_eq!(result["schema"], COOPERATIVE_PLACEMENT_PREVIEW_SCHEMA_V1);
        assert_eq!(result["authority"], "planner_only");
        assert_eq!(result["executable"], false);
        assert_eq!(result["reservation_status"], "NOT_ADMITTED");
        assert_eq!(result["plan"]["decision"], "selected");
    }

    #[test]
    fn cooperative_placement_preview_rejects_unknown_task_schema() {
        let result = aegis_cooperative_placement_preview(
            serde_json::json!({"schema": "unknown"}).to_string(),
            "[]".to_owned(),
            "[]".to_owned(),
            1_000,
        );
        assert!(result.is_err());
    }

    #[test]
    fn cooperative_placement_admission_is_idempotent_and_fenced() {
        let task = serde_json::json!({
            "schema": crate::placement::COOPERATIVE_PLACEMENT_TASK_SCHEMA_V1,
            "task_id": 7001,
            "mode": "ParallelSlices",
            "slices": [
                {
                    "id": "first",
                    "stage": 0,
                    "depends_on": [],
                    "executor_domain": "Cpu",
                    "data_tier_domain": "HostMemory",
                    "required_data_tier_id": null,
                    "input_bytes": 1,
                    "output_bytes": 1,
                    "working_memory_bytes": 64,
                    "work_units": 10
                },
                {
                    "id": "second",
                    "stage": 0,
                    "depends_on": [],
                    "executor_domain": "Cpu",
                    "data_tier_domain": "HostMemory",
                    "required_data_tier_id": null,
                    "input_bytes": 1,
                    "output_bytes": 1,
                    "working_memory_bytes": 64,
                    "work_units": 10
                }
            ],
            "buffer_policy": {
                "capacity_bytes": 4,
                "max_in_flight_buffers": 2,
                "high_watermark_bytes": 3,
                "low_watermark_bytes": 1,
                "allow_spill_to_storage": false
            },
            "deadline_ms": null,
            "requires_local_only": true,
            "allow_storage": false
        });
        let capabilities = serde_json::json!([
            {
                "id": "cpu",
                "domain": "Cpu",
                "usable_bytes": 4096,
                "compute_units_per_us": 10,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 4,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured"
            },
            {
                "id": "ram-hot",
                "domain": "HostMemory",
                "usable_bytes": 4096,
                "compute_units_per_us": null,
                "bandwidth_bytes_per_s": null,
                "latency_us": 1,
                "queue_depth": 0,
                "queue_capacity": 4,
                "pressure": false,
                "local_only": true,
                "confidence": "Measured"
            }
        ]);
        let first = aegis_cooperative_placement_admit(
            task.to_string(),
            capabilities.to_string(),
            "[]".to_owned(),
            1,
            1_000,
        )
        .unwrap();
        let first: serde_json::Value = serde_json::from_str(&first).unwrap();
        assert_eq!(first["schema"], COOPERATIVE_ADMISSION_SCHEMA_V1);
        assert_eq!(first["authority"], "native_runtime");
        assert_eq!(first["status"], "admitted");
        assert_eq!(first["executable"], true);
        let lease = first["lease"].clone();

        let second = aegis_cooperative_placement_admit(
            task.to_string(),
            capabilities.to_string(),
            "[]".to_owned(),
            1,
            2_000,
        )
        .unwrap();
        let second: serde_json::Value = serde_json::from_str(&second).unwrap();
        assert_eq!(second["status"], "already_admitted");
        assert_eq!(second["lease"], lease);

        let mut forged = lease.clone();
        forged["generation"] = serde_json::json!(2);
        assert!(aegis_cooperative_placement_release(forged.to_string()).is_err());
        assert!(aegis_cooperative_placement_release(lease.to_string()).unwrap());
        assert!(aegis_cooperative_placement_release(lease.to_string()).is_err());
    }

    #[test]
    fn placement_capability_ffi_exposes_unknown_costs_as_null() {
        let result = aegis_placement_capabilities().unwrap();
        let result: serde_json::Value = serde_json::from_str(&result).unwrap();
        let cpu = result
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["id"] == "cpu")
            .unwrap();
        assert!(cpu["compute_units_per_us"].is_null());
        assert_eq!(cpu["confidence"], "Unknown");
    }

    #[test]
    fn placement_calibration_ffi_returns_bounded_measured_samples() {
        let result = aegis_placement_calibrate(10_000, Some(4 * 1024)).unwrap();
        let result: serde_json::Value = serde_json::from_str(&result).unwrap();
        let measurements = result["measurements"].as_array().unwrap();
        assert_eq!(measurements.len(), 2);
        assert!(
            measurements
                .iter()
                .all(|measurement| measurement["confidence"] == "Measured")
        );
        assert!(
            measurements
                .iter()
                .all(|measurement| measurement["elapsed_us"].as_u64().unwrap() > 0)
        );
    }

    #[test]
    fn runtime_resource_observation_returns_a_typed_feedback_envelope() {
        let result = super::aegis_runtime_observe_resources().expect("observe resources");
        let result: serde_json::Value = serde_json::from_str(&result).expect("valid JSON");
        assert_eq!(
            result["schema"],
            crate::resource::RESOURCE_CONTRACT_SCHEMA_V1
        );
        assert_eq!(result["authoritative"], true);
        assert_eq!(
            result["sample"]["schema"],
            crate::resource::RESOURCE_CONTRACT_SCHEMA_V1
        );
        assert!(result["feedback"]["reason"].is_string());
    }
}
