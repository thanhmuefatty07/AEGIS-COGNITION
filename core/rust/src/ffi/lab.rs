//! Lab controller and replay-verification PyO3 bindings.
//!
//! This module owns the Python-facing Lab reducer/controller surface while the
//! parent façade keeps the public registration names stable.

use super::py_safe;
use pyo3::prelude::*;

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
