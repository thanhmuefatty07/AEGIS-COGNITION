//! Conservative transfer-aware placement for heterogeneous local resources.
//!
//! This module is a decision surface, not a device backend. It never treats
//! an unknown bandwidth, capacity, or accelerator capability as measured. A
//! caller may provide observations from the platform adapter; until then the
//! planner defers rather than inventing a faster placement.

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{Read, Write};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub const PLACEMENT_SCHEMA_V1: &str = "aegis-placement-plan-v1";
pub const PLACEMENT_MEASUREMENT_SCHEMA_V1: &str = "aegis-placement-measurement-v1";
pub const PLACEMENT_TRANSFER_PATH_SCHEMA_V1: &str = "aegis-placement-transfer-path-v1";
pub const PLACEMENT_BINDING_SCHEMA_V1: &str = "aegis-placement-binding-v1";
pub const COOPERATIVE_PLACEMENT_TASK_SCHEMA_V1: &str = "aegis-cooperative-placement-task-v1";
pub const COOPERATIVE_PLACEMENT_PLAN_SCHEMA_V1: &str = "aegis-cooperative-placement-plan-v1";
pub const COOPERATIVE_PLACEMENT_BUFFER_SCHEMA_V1: &str = "aegis-cooperative-placement-buffer-v1";
pub const COOPERATIVE_PLACEMENT_SPILL_SCHEMA_V1: &str = "aegis-cooperative-placement-spill-v1";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum PlacementDomain {
    Cpu,
    HostMemory,
    Storage,
    Accelerator,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum PlacementConfidence {
    Measured,
    Estimated,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementCapability {
    pub id: String,
    pub domain: PlacementDomain,
    pub usable_bytes: Option<u64>,
    /// Measured or bounded work units per microsecond.
    pub compute_units_per_us: Option<u64>,
    pub bandwidth_bytes_per_s: Option<u64>,
    pub latency_us: Option<u64>,
    pub queue_depth: u32,
    pub queue_capacity: Option<u32>,
    pub pressure: bool,
    pub local_only: bool,
    pub confidence: PlacementConfidence,
    /// Explicit link observations are kept with the inventory entry so the
    /// legacy FFI shape can carry pairwise topology without a second global
    /// JSON envelope.  An empty list means that no link was observed.
    #[serde(default)]
    pub transfer_paths: Vec<PlacementTransferPath>,
}

/// An observed route between an executor and a data tier.
///
/// Device capability and device-to-memory/storage transfer are deliberately
/// separate facts.  The planner will not synthesize a route for an
/// accelerator or storage tier when this record is absent or unknown.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementTransferPath {
    pub schema: String,
    pub id: String,
    pub from_executor: String,
    pub to_data_tier: String,
    pub bandwidth_bytes_per_s: Option<u64>,
    pub latency_us: Option<u64>,
    pub queue_depth: u32,
    pub queue_capacity: Option<u32>,
    pub transfer_token_capacity: Option<u32>,
    pub transfer_tokens_in_use: u32,
    /// A non-None value is an observed budget for a shared/UMA buffer.  It is
    /// not a claim that the platform is coherent or zero-copy.
    pub shared_memory_bytes: Option<u64>,
    pub local_only: bool,
    pub confidence: PlacementConfidence,
}

impl PlacementTransferPath {
    pub fn validate(&self) -> Result<(), PlacementError> {
        if self.schema != PLACEMENT_TRANSFER_PATH_SCHEMA_V1 {
            return Err(PlacementError::InvalidTask(
                "unsupported placement transfer-path schema".to_string(),
            ));
        }
        if self.id.trim().is_empty()
            || self.from_executor.trim().is_empty()
            || self.to_data_tier.trim().is_empty()
        {
            return Err(PlacementError::InvalidTask(
                "transfer path identity must be non-empty".to_string(),
            ));
        }
        if self
            .queue_capacity
            .is_some_and(|capacity| capacity == 0 || self.queue_depth > capacity)
        {
            return Err(PlacementError::InvalidTask(
                "transfer path queue capacity is invalid".to_string(),
            ));
        }
        if self
            .transfer_token_capacity
            .is_some_and(|capacity| capacity == 0 || self.transfer_tokens_in_use > capacity)
        {
            return Err(PlacementError::InvalidTask(
                "transfer token capacity is invalid".to_string(),
            ));
        }
        Ok(())
    }
}

/// The identity and bounded reservations that must travel with an executable
/// placement.  It is separate from the v1 resource request so old request
/// literals and JSON remain source-compatible.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementBinding {
    pub schema: String,
    pub executor_id: String,
    pub data_tier_id: String,
    pub transfer_path_id: Option<String>,
    pub storage_bytes: u64,
    pub storage_in_flight: u32,
    pub transfer_tokens: u32,
    pub shared_memory_bytes: u64,
}

impl PlacementBinding {
    pub fn validate(&self) -> Result<(), PlacementError> {
        if self.schema != PLACEMENT_BINDING_SCHEMA_V1 {
            return Err(PlacementError::InvalidTask(
                "unsupported placement binding schema".to_string(),
            ));
        }
        if self.executor_id.trim().is_empty() || self.data_tier_id.trim().is_empty() {
            return Err(PlacementError::InvalidTask(
                "placement executor and data-tier identities must be non-empty".to_string(),
            ));
        }
        if self
            .transfer_path_id
            .as_deref()
            .is_some_and(|path_id| path_id.trim().is_empty())
        {
            return Err(PlacementError::InvalidTask(
                "transfer path identity must be non-empty when present".to_string(),
            ));
        }
        if self.storage_bytes > 0 && self.storage_in_flight == 0 {
            return Err(PlacementError::InvalidTask(
                "storage bytes require a non-zero storage in-flight reservation".to_string(),
            ));
        }
        if self.storage_in_flight > 0 && self.storage_bytes == 0 {
            return Err(PlacementError::InvalidTask(
                "storage in-flight reservation requires storage bytes".to_string(),
            ));
        }
        if self.transfer_tokens > 0 && self.transfer_path_id.is_none() {
            return Err(PlacementError::InvalidTask(
                "transfer tokens require a transfer-path identity".to_string(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementTask {
    pub schema: String,
    pub task_id: u128,
    pub input_bytes: u64,
    pub output_bytes: u64,
    pub working_memory_bytes: u64,
    pub cpu_work_units: u64,
    pub accelerator_work_units: u64,
    pub deadline_ms: Option<u64>,
    pub requires_accelerator: bool,
    pub allow_storage: bool,
    pub privacy_local_only: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementCandidate {
    pub id: String,
    pub domain: PlacementDomain,
    pub eligible: bool,
    pub estimated_latency_us: Option<u64>,
    pub reason: String,
    pub confidence: PlacementConfidence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementPairCandidate {
    pub executor_id: Option<String>,
    pub data_tier_id: Option<String>,
    pub transfer_path_id: Option<String>,
    pub eligible: bool,
    pub estimated_latency_us: Option<u64>,
    pub shared_memory_bytes: u64,
    pub reason: String,
    pub confidence: PlacementConfidence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementPlan {
    pub schema: String,
    pub task_id: u128,
    /// Compatibility alias for the selected executor. RAM and SSD are data
    /// tiers, not compute devices, so a complete plan also exposes both
    /// placements explicitly.
    pub selected: Option<String>,
    pub executor: Option<String>,
    pub data_tier: Option<String>,
    pub decision: String,
    pub reason: String,
    pub candidates: Vec<PlacementCandidate>,
    #[serde(default)]
    pub pair_candidates: Vec<PlacementPairCandidate>,
    #[serde(default)]
    pub binding: Option<PlacementBinding>,
    pub planned_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PlacementError {
    InvalidTask(String),
    NoCapabilities,
    CalibrationFailed(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementMeasurement {
    pub schema: String,
    pub resource_id: String,
    pub domain: PlacementDomain,
    pub workload: String,
    pub bytes: u64,
    pub work_units: u64,
    pub elapsed_us: u64,
    pub compute_units_per_us: Option<u64>,
    pub bandwidth_bytes_per_s: Option<u64>,
    pub latency_us: Option<u64>,
    pub confidence: PlacementConfidence,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementCalibration {
    pub schema: String,
    pub measurements: Vec<PlacementMeasurement>,
}

impl PlacementCalibration {
    /// Run bounded, opt-in local calibration.  It measures a synthetic CPU
    /// kernel and sequential filesystem I/O; it does not claim to represent
    /// an arbitrary application kernel or uncached physical-device latency.
    pub fn run(cpu_iterations: u64, storage_bytes: Option<u64>) -> Result<Self, PlacementError> {
        let mut measurements = vec![calibrate_cpu(cpu_iterations)?];
        if let Some(bytes) = storage_bytes {
            measurements.push(calibrate_storage(bytes)?);
        }
        Ok(Self {
            schema: PLACEMENT_MEASUREMENT_SCHEMA_V1.to_string(),
            measurements,
        })
    }
}

fn calibrate_cpu(iterations: u64) -> Result<PlacementMeasurement, PlacementError> {
    const MAX_CPU_ITERATIONS: u64 = 100_000_000;
    if !(1..=MAX_CPU_ITERATIONS).contains(&iterations) {
        return Err(PlacementError::InvalidTask(format!(
            "cpu calibration iterations must be between 1 and {MAX_CPU_ITERATIONS}"
        )));
    }
    let started = Instant::now();
    let mut state = 0_u64;
    for value in 0..iterations {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(value ^ 0x9e37_79b9_7f4a_7c15);
    }
    std::hint::black_box(state);
    let elapsed_us = elapsed_us(started);
    Ok(PlacementMeasurement {
        schema: PLACEMENT_MEASUREMENT_SCHEMA_V1.to_string(),
        resource_id: "cpu".to_string(),
        domain: PlacementDomain::Cpu,
        workload: "scalar-u64-mix-v1".to_string(),
        bytes: 0,
        work_units: iterations,
        elapsed_us,
        compute_units_per_us: Some(div_ceil(iterations, elapsed_us)),
        bandwidth_bytes_per_s: None,
        latency_us: Some(elapsed_us),
        confidence: PlacementConfidence::Measured,
    })
}

fn calibrate_storage(bytes: u64) -> Result<PlacementMeasurement, PlacementError> {
    const MIN_STORAGE_BYTES: u64 = 4 * 1024;
    const MAX_STORAGE_BYTES: u64 = 64 * 1024 * 1024;
    const CHUNK_BYTES: usize = 64 * 1024;
    if !(MIN_STORAGE_BYTES..=MAX_STORAGE_BYTES).contains(&bytes) {
        return Err(PlacementError::InvalidTask(format!(
            "storage calibration bytes must be between {MIN_STORAGE_BYTES} and {MAX_STORAGE_BYTES}"
        )));
    }
    let bytes_usize = usize::try_from(bytes).map_err(|_| {
        PlacementError::CalibrationFailed("storage calibration size exceeds usize".to_string())
    })?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or(0);
    let path = std::env::temp_dir().join(format!(
        "aegis-placement-calibration-{}-{stamp}.bin",
        std::process::id()
    ));
    let result = (|| {
        let mut buffer = [0_u8; CHUNK_BYTES];
        let write_started = Instant::now();
        let mut file = File::create(&path).map_err(|error| {
            PlacementError::CalibrationFailed(format!("storage calibration create failed: {error}"))
        })?;
        let mut remaining = bytes_usize;
        while remaining > 0 {
            let length = remaining.min(buffer.len());
            file.write_all(&buffer[..length]).map_err(|error| {
                PlacementError::CalibrationFailed(format!(
                    "storage calibration write failed: {error}"
                ))
            })?;
            remaining -= length;
        }
        file.sync_all().map_err(|error| {
            PlacementError::CalibrationFailed(format!("storage calibration sync failed: {error}"))
        })?;
        let write_us = elapsed_us(write_started);
        drop(file);

        let read_started = Instant::now();
        let mut file = File::open(&path).map_err(|error| {
            PlacementError::CalibrationFailed(format!("storage calibration open failed: {error}"))
        })?;
        let mut observed_bytes = 0_usize;
        let mut checksum = 0_u8;
        loop {
            let read = file.read(&mut buffer).map_err(|error| {
                PlacementError::CalibrationFailed(format!(
                    "storage calibration read failed: {error}"
                ))
            })?;
            if read == 0 {
                break;
            }
            observed_bytes = observed_bytes.checked_add(read).ok_or_else(|| {
                PlacementError::CalibrationFailed(
                    "storage calibration read length overflow".to_string(),
                )
            })?;
            checksum = buffer[..read]
                .iter()
                .fold(checksum, |state, byte| state ^ byte);
        }
        if observed_bytes != bytes_usize {
            return Err(PlacementError::CalibrationFailed(
                "storage calibration read length mismatch".to_string(),
            ));
        }
        std::hint::black_box(checksum);
        let read_us = elapsed_us(read_started);
        let elapsed = write_us.saturating_add(read_us).max(1);
        let transfer_bytes = bytes.saturating_mul(2);
        let resource_id = std::env::current_dir()
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "filesystem".to_string());
        Ok(PlacementMeasurement {
            schema: PLACEMENT_MEASUREMENT_SCHEMA_V1.to_string(),
            resource_id,
            domain: PlacementDomain::Storage,
            workload: "sequential-write-sync-read-v1".to_string(),
            bytes: transfer_bytes,
            work_units: 0,
            elapsed_us: elapsed,
            compute_units_per_us: None,
            bandwidth_bytes_per_s: Some(div_ceil(
                transfer_bytes.saturating_mul(1_000_000),
                elapsed,
            )),
            latency_us: Some(elapsed),
            confidence: PlacementConfidence::Measured,
        })
    })();
    let cleanup = std::fs::remove_file(&path);
    match (result, cleanup) {
        (Ok(measurement), Ok(())) => Ok(measurement),
        (Ok(_), Err(error)) => Err(PlacementError::CalibrationFailed(format!(
            "storage calibration cleanup failed: {error}"
        ))),
        (Err(error), _) => Err(error),
    }
}

fn elapsed_us(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_micros())
        .unwrap_or(u64::MAX)
        .max(1)
}

impl PlacementTask {
    pub fn validate(&self) -> Result<(), PlacementError> {
        if self.schema != PLACEMENT_SCHEMA_V1 {
            return Err(PlacementError::InvalidTask(
                "unsupported placement schema".to_string(),
            ));
        }
        if self.task_id == 0 {
            return Err(PlacementError::InvalidTask(
                "task_id must be non-zero".to_string(),
            ));
        }
        if self.requires_accelerator && self.accelerator_work_units == 0 {
            return Err(PlacementError::InvalidTask(
                "accelerator work is required for an accelerator task".to_string(),
            ));
        }
        Ok(())
    }
}

impl PlacementPlan {
    pub fn build(
        task: &PlacementTask,
        capabilities: &[PlacementCapability],
        now_ms: u64,
    ) -> Result<Self, PlacementError> {
        let paths = capabilities
            .iter()
            .flat_map(|capability| capability.transfer_paths.iter().cloned())
            .collect::<Vec<_>>();
        Self::build_with_paths(task, capabilities, &paths, now_ms)
    }

    /// Build a plan from explicit resource and link observations.
    ///
    /// The selection unit is an executor/data-tier pair.  This prevents a
    /// fast executor and a fast data tier from being selected independently
    /// when their connecting route is slow, full, or unknown.
    pub fn build_with_paths(
        task: &PlacementTask,
        capabilities: &[PlacementCapability],
        paths: &[PlacementTransferPath],
        now_ms: u64,
    ) -> Result<Self, PlacementError> {
        task.validate()?;
        if capabilities.is_empty() {
            return Err(PlacementError::NoCapabilities);
        }
        for path in paths {
            path.validate()?;
        }

        let mut candidates = capabilities
            .iter()
            .map(|capability| evaluate_candidate(task, capability, now_ms))
            .collect::<Vec<_>>();
        candidates.sort_by(|left, right| left.id.cmp(&right.id));

        let executor_required = task.cpu_work_units > 0 || task.accelerator_work_units > 0;
        let data_required = task.input_bytes > 0 || task.output_bytes > 0;

        let mut pair_candidates = Vec::new();
        if executor_required {
            for executor in candidates.iter().filter(|candidate| {
                matches!(
                    candidate.domain,
                    PlacementDomain::Cpu | PlacementDomain::Accelerator
                )
            }) {
                if data_required {
                    for data_tier in candidates.iter().filter(|candidate| {
                        matches!(
                            candidate.domain,
                            PlacementDomain::HostMemory | PlacementDomain::Storage
                        )
                    }) {
                        pair_candidates.push(evaluate_pair(
                            task,
                            Some(executor),
                            Some(data_tier),
                            paths,
                            now_ms,
                        ));
                    }
                } else {
                    pair_candidates.push(evaluate_pair(task, Some(executor), None, paths, now_ms));
                }
            }
        } else if data_required {
            for data_tier in candidates.iter().filter(|candidate| {
                matches!(
                    candidate.domain,
                    PlacementDomain::HostMemory | PlacementDomain::Storage
                )
            }) {
                pair_candidates.push(evaluate_pair(task, None, Some(data_tier), paths, now_ms));
            }
        }
        pair_candidates.sort_by(|left, right| {
            left.executor_id
                .cmp(&right.executor_id)
                .then_with(|| left.data_tier_id.cmp(&right.data_tier_id))
                .then_with(|| left.transfer_path_id.cmp(&right.transfer_path_id))
        });

        let selected_pair = pair_candidates
            .iter()
            .filter(|pair| pair.eligible && pair.estimated_latency_us.is_some())
            .min_by(|left, right| {
                left.estimated_latency_us
                    .cmp(&right.estimated_latency_us)
                    .then_with(|| left.executor_id.cmp(&right.executor_id))
                    .then_with(|| left.data_tier_id.cmp(&right.data_tier_id))
                    .then_with(|| left.transfer_path_id.cmp(&right.transfer_path_id))
            });
        let has_unknown_pair = pair_candidates
            .iter()
            .any(|pair| pair.eligible && pair.estimated_latency_us.is_none());

        let (selected_id, executor_id, data_tier_id, binding, decision, reason) = if let Some(
            pair,
        ) =
            selected_pair
        {
            let executor_id = pair.executor_id.clone();
            let data_tier_id = pair.data_tier_id.clone();
            let binding = match (executor_id.clone(), data_tier_id.clone()) {
                (Some(executor_id), Some(data_tier_id)) => {
                    let data_domain = capabilities
                        .iter()
                        .find(|capability| capability.id == data_tier_id)
                        .map(|capability| capability.domain);
                    Some(PlacementBinding {
                        schema: PLACEMENT_BINDING_SCHEMA_V1.to_string(),
                        executor_id,
                        data_tier_id,
                        transfer_path_id: pair.transfer_path_id.clone(),
                        storage_bytes: if data_domain == Some(PlacementDomain::Storage) {
                            task.input_bytes.saturating_add(task.output_bytes)
                        } else {
                            0
                        },
                        storage_in_flight: if data_domain == Some(PlacementDomain::Storage) {
                            1
                        } else {
                            0
                        },
                        transfer_tokens: u32::from(pair.transfer_path_id.is_some()),
                        shared_memory_bytes: pair.shared_memory_bytes,
                    })
                }
                _ => None,
            };
            (
                pair.executor_id
                    .clone()
                    .or_else(|| pair.data_tier_id.clone()),
                executor_id,
                data_tier_id,
                binding,
                "selected".to_string(),
                "minimum estimated executor + data-tier + transfer-path cost".to_string(),
            )
        } else if has_unknown_pair {
            (
                None,
                None,
                None,
                None,
                "deferred".to_string(),
                "required executor/data tier or its transfer path has no measured cost".to_string(),
            )
        } else {
            (
                    None,
                    None,
                    None,
                    None,
                    "rejected".to_string(),
                    "no executor/data-tier pair satisfies capability, identity, memory, pressure, or deadline constraints"
                        .to_string(),
                )
        };

        Ok(Self {
            schema: PLACEMENT_SCHEMA_V1.to_string(),
            task_id: task.task_id,
            selected: selected_id,
            executor: executor_id,
            data_tier: data_tier_id,
            decision,
            reason,
            candidates,
            pair_candidates,
            binding,
            planned_at_ms: now_ms,
        })
    }
}

/// The requested execution topology for the cooperative planner.  The mode
/// is explicit: the planner never infers that a task may be split merely
/// because it has both CPU and accelerator work fields.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum CooperativePlacementMode {
    SingleExecutor,
    ParallelSlices,
    Pipeline,
}

/// A bounded buffer policy for cooperative slices.  These are scheduling
/// constraints only; they do not allocate memory or perform I/O.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeBufferPolicy {
    pub capacity_bytes: u64,
    pub max_in_flight_buffers: u32,
    pub high_watermark_bytes: u64,
    pub low_watermark_bytes: u64,
    pub allow_spill_to_storage: bool,
}

impl CooperativeBufferPolicy {
    fn validate(&self, mode: CooperativePlacementMode) -> Result<(), PlacementError> {
        let has_buffers = mode != CooperativePlacementMode::SingleExecutor;
        if has_buffers && (self.capacity_bytes == 0 || self.max_in_flight_buffers == 0) {
            return Err(PlacementError::InvalidTask(
                "cooperative parallel/pipeline mode requires a bounded buffer and in-flight limit"
                    .to_string(),
            ));
        }
        // `capacity_bytes` is the total resident-byte budget for each edge;
        // `max_in_flight_buffers` is an independent item-count ceiling.  A
        // generated buffer is always at least one byte, so the count cannot
        // exceed the total byte budget.
        if has_buffers && u64::from(self.max_in_flight_buffers) > self.capacity_bytes {
            return Err(PlacementError::InvalidTask(
                "cooperative buffer byte capacity must cover one byte per in-flight buffer"
                    .to_string(),
            ));
        }
        if self.high_watermark_bytes > self.capacity_bytes
            || self.low_watermark_bytes > self.high_watermark_bytes
        {
            return Err(PlacementError::InvalidTask(
                "cooperative buffer watermarks must be bounded and ordered".to_string(),
            ));
        }
        if !has_buffers
            && (self.max_in_flight_buffers != 0
                || self.high_watermark_bytes != 0
                || self.low_watermark_bytes != 0)
        {
            return Err(PlacementError::InvalidTask(
                "single-executor mode cannot declare active cooperative buffers".to_string(),
            ));
        }
        Ok(())
    }
}

/// A user-declared slice.  Executor and data-tier domains are explicit so a
/// cooperative plan cannot silently reinterpret CPU work as accelerator work
/// or turn a data residency request into a compute placement.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeSliceSpec {
    pub id: String,
    pub stage: u32,
    pub depends_on: Vec<String>,
    pub executor_domain: PlacementDomain,
    pub data_tier_domain: PlacementDomain,
    pub required_data_tier_id: Option<String>,
    pub input_bytes: u64,
    pub output_bytes: u64,
    pub working_memory_bytes: u64,
    pub work_units: u64,
}

/// A cooperative task is a planning contract, not an execution command.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativePlacementTask {
    pub schema: String,
    pub task_id: u128,
    pub mode: CooperativePlacementMode,
    pub slices: Vec<CooperativeSliceSpec>,
    pub buffer_policy: CooperativeBufferPolicy,
    pub deadline_ms: Option<u64>,
    pub requires_local_only: bool,
    pub allow_storage: bool,
}

impl CooperativePlacementTask {
    pub fn validate(&self) -> Result<(), PlacementError> {
        if self.schema != COOPERATIVE_PLACEMENT_TASK_SCHEMA_V1 {
            return Err(PlacementError::InvalidTask(
                "unsupported cooperative placement task schema".to_string(),
            ));
        }
        if self.task_id == 0 || self.slices.is_empty() {
            return Err(PlacementError::InvalidTask(
                "cooperative task id and slices must be non-empty".to_string(),
            ));
        }
        self.buffer_policy.validate(self.mode)?;
        let mut ids = BTreeSet::new();
        let stage_by_id = self
            .slices
            .iter()
            .map(|slice| (slice.id.as_str(), slice.stage))
            .collect::<BTreeMap<_, _>>();
        if stage_by_id.len() != self.slices.len() {
            return Err(PlacementError::InvalidTask(
                "cooperative slice identities must be unique".to_string(),
            ));
        }
        for slice in &self.slices {
            if slice.id.trim().is_empty() || !ids.insert(slice.id.as_str()) {
                return Err(PlacementError::InvalidTask(
                    "cooperative slice identity must be non-empty and unique".to_string(),
                ));
            }
            if !matches!(
                slice.executor_domain,
                PlacementDomain::Cpu | PlacementDomain::Accelerator
            ) {
                return Err(PlacementError::InvalidTask(
                    "cooperative slices require CPU or accelerator executors".to_string(),
                ));
            }
            if !matches!(
                slice.data_tier_domain,
                PlacementDomain::HostMemory | PlacementDomain::Storage
            ) {
                return Err(PlacementError::InvalidTask(
                    "cooperative slices require host-memory or storage data tiers".to_string(),
                ));
            }
            if slice
                .required_data_tier_id
                .as_deref()
                .is_some_and(|id| id.trim().is_empty())
            {
                return Err(PlacementError::InvalidTask(
                    "required cooperative data-tier identity must be non-empty".to_string(),
                ));
            }
            if slice.work_units == 0 {
                return Err(PlacementError::InvalidTask(
                    "cooperative slice work_units must be non-zero".to_string(),
                ));
            }
            if slice.data_tier_domain == PlacementDomain::Storage && !self.allow_storage {
                return Err(PlacementError::InvalidTask(
                    "storage data tiers are disabled for this cooperative task".to_string(),
                ));
            }
            let mut dependencies = BTreeSet::new();
            for dependency in &slice.depends_on {
                if !dependencies.insert(dependency.as_str()) {
                    return Err(PlacementError::InvalidTask(
                        "cooperative slice dependencies must be unique".to_string(),
                    ));
                }
                let Some(dependency_stage) = stage_by_id.get(dependency.as_str()) else {
                    return Err(PlacementError::InvalidTask(
                        "cooperative slice dependency is unknown".to_string(),
                    ));
                };
                if dependency == &slice.id || dependency_stage >= &slice.stage {
                    return Err(PlacementError::InvalidTask(
                        "cooperative dependencies must point to an earlier stage".to_string(),
                    ));
                }
            }
        }
        match self.mode {
            CooperativePlacementMode::SingleExecutor => {
                if self.slices.len() != 1 || !self.slices[0].depends_on.is_empty() {
                    return Err(PlacementError::InvalidTask(
                        "single-executor mode requires exactly one independent slice".to_string(),
                    ));
                }
            }
            CooperativePlacementMode::ParallelSlices => {
                if self.slices.len() < 2
                    || self.slices.iter().any(|slice| !slice.depends_on.is_empty())
                {
                    return Err(PlacementError::InvalidTask(
                        "parallel-slices mode requires at least two independent slices".to_string(),
                    ));
                }
            }
            CooperativePlacementMode::Pipeline => {
                if self.slices.len() < 2 {
                    return Err(PlacementError::InvalidTask(
                        "pipeline mode requires at least two slices".to_string(),
                    ));
                }
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeSliceReservation {
    pub executor_queue_slots: u32,
    pub executor_memory_bytes: u64,
    pub data_tier_bytes: u64,
    pub storage_bytes: u64,
    pub storage_in_flight: u32,
    pub transfer_tokens: u32,
    pub shared_memory_bytes: u64,
}

/// Aggregate reservation for a cooperative plan.  Maps are ordered so a
/// serialized plan and its accounting are deterministic.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeReservation {
    pub executor_queue_slots: BTreeMap<String, u32>,
    pub executor_memory_bytes: BTreeMap<String, u64>,
    pub data_tier_bytes: BTreeMap<String, u64>,
    pub buffer_bytes: BTreeMap<String, u64>,
    pub storage_bytes: BTreeMap<String, u64>,
    pub storage_in_flight: BTreeMap<String, u32>,
    pub transfer_tokens: BTreeMap<String, u32>,
    pub shared_memory_bytes: BTreeMap<String, u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeBufferPlan {
    pub schema: String,
    pub id: String,
    pub from_slice: Option<String>,
    pub to_slice: Option<String>,
    pub data_tier_id: Option<String>,
    pub capacity_bytes: u64,
    pub max_in_flight_buffers: u32,
    pub high_watermark_bytes: u64,
    pub low_watermark_bytes: u64,
    /// True only when `spill_reservation` contains a validated, aggregate
    /// reservation for storage bytes, storage in-flight capacity, and a
    /// concrete transfer path/token.  A bare boolean never authorizes spill.
    pub spill_to_storage: bool,
    #[serde(default)]
    pub spill_reservation: Option<CooperativeSpillReservation>,
    pub backpressure: String,
}

/// The executable metadata for one buffer's optional SSD/storage spill path.
/// `Some` is emitted only after every field is validated and included in the
/// cooperative aggregate reservation; `None` means spill is not executable.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeSpillReservation {
    pub schema: String,
    pub storage_id: String,
    pub storage_bytes: u64,
    pub storage_in_flight: u32,
    pub transfer_path_id: String,
    pub transfer_tokens: u32,
}

impl CooperativeSpillReservation {
    pub fn validate(&self) -> Result<(), PlacementError> {
        if self.schema != COOPERATIVE_PLACEMENT_SPILL_SCHEMA_V1
            || self.storage_id.trim().is_empty()
            || self.transfer_path_id.trim().is_empty()
        {
            return Err(PlacementError::InvalidTask(
                "cooperative spill metadata has an invalid identity or schema".to_string(),
            ));
        }
        if self.storage_bytes == 0 || self.storage_in_flight == 0 || self.transfer_tokens == 0 {
            return Err(PlacementError::InvalidTask(
                "cooperative spill metadata must reserve storage bytes, in-flight capacity, and transfer tokens"
                    .to_string(),
            ));
        }
        Ok(())
    }
}

impl CooperativeBufferPlan {
    pub fn validate(&self) -> Result<(), PlacementError> {
        if self.schema != COOPERATIVE_PLACEMENT_BUFFER_SCHEMA_V1 || self.id.trim().is_empty() {
            return Err(PlacementError::InvalidTask(
                "cooperative buffer metadata has an invalid identity or schema".to_string(),
            ));
        }
        if self.capacity_bytes == 0
            || self.max_in_flight_buffers == 0
            || u64::from(self.max_in_flight_buffers) > self.capacity_bytes
            || self.low_watermark_bytes > self.high_watermark_bytes
            || self.high_watermark_bytes > self.capacity_bytes
        {
            return Err(PlacementError::InvalidTask(
                "cooperative buffer capacity and watermarks are invalid".to_string(),
            ));
        }
        if self.spill_to_storage != self.spill_reservation.is_some() {
            return Err(PlacementError::InvalidTask(
                "cooperative spill flag and reservation metadata disagree".to_string(),
            ));
        }
        if let Some(spill) = &self.spill_reservation {
            spill.validate()?;
            if spill.storage_bytes < self.capacity_bytes {
                return Err(PlacementError::InvalidTask(
                    "cooperative spill reservation is smaller than the buffer budget".to_string(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativePlacementSlice {
    pub id: String,
    pub stage: u32,
    pub depends_on: Vec<String>,
    pub executor_domain: PlacementDomain,
    pub data_tier_domain: PlacementDomain,
    pub executor_id: Option<String>,
    pub data_tier_id: Option<String>,
    pub transfer_path_id: Option<String>,
    pub work_units: u64,
    pub input_bytes: u64,
    pub output_bytes: u64,
    pub working_memory_bytes: u64,
    pub reservation: CooperativeSliceReservation,
    pub estimated_latency_us: Option<u64>,
    pub confidence: PlacementConfidence,
    pub decision: String,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativePlacementPlan {
    pub schema: String,
    pub task_id: u128,
    pub mode: CooperativePlacementMode,
    pub decision: String,
    pub reason: String,
    pub slices: Vec<CooperativePlacementSlice>,
    pub buffers: Vec<CooperativeBufferPlan>,
    pub reservation: CooperativeReservation,
    pub reservation_status: String,
    pub estimated_makespan_us: Option<u64>,
    pub planned_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CooperativeReservationLease {
    pub lease_id: u64,
    pub reservation: CooperativeReservation,
}

/// Pure in-memory accounting used to prove all-or-none reservation semantics.
/// It does not touch OS handles, queues, devices, files, or external state.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CooperativeReservationLedger {
    capacity: CooperativeReservation,
    used: CooperativeReservation,
    next_lease_id: u64,
    active: BTreeMap<u64, CooperativeReservationLease>,
}

impl CooperativePlacementPlan {
    /// Build a cooperative plan without starting work or reserving an OS
    /// resource.  Each slice is planned independently first, then the
    /// aggregate reservation is checked as one transaction.
    pub fn build(
        task: &CooperativePlacementTask,
        capabilities: &[PlacementCapability],
        paths: &[PlacementTransferPath],
        now_ms: u64,
    ) -> Result<Self, PlacementError> {
        task.validate()?;
        if capabilities.is_empty() {
            return Err(PlacementError::NoCapabilities);
        }
        let inventory = CooperativeReservationLedger::from_inventory(capabilities, paths)?;
        let buffer_tier_id = if task.buffer_policy.capacity_bytes == 0 {
            None
        } else {
            match select_cooperative_buffer_tier(task, capabilities) {
                Some(id) => Some(id),
                None => {
                    return Ok(cooperative_deferred(
                        task,
                        Vec::new(),
                        "bounded cooperative buffers have no available host-memory tier",
                        now_ms,
                    ));
                }
            }
        };

        let mut slices = Vec::with_capacity(task.slices.len());
        for specification in &task.slices {
            let scoped_capabilities = capabilities
                .iter()
                .filter(|capability| {
                    capability.domain == specification.executor_domain
                        || capability.domain == specification.data_tier_domain
                })
                .filter(|capability| {
                    specification
                        .required_data_tier_id
                        .as_deref()
                        .is_none_or(|id| {
                            capability.domain != specification.data_tier_domain
                                || capability.id == id
                        })
                })
                .cloned()
                .collect::<Vec<_>>();
            let placement_task = cooperative_slice_task(task, specification);
            let placement = match PlacementPlan::build_with_paths(
                &placement_task,
                &scoped_capabilities,
                paths,
                now_ms,
            ) {
                Ok(plan) => plan,
                Err(PlacementError::NoCapabilities) => {
                    slices.push(cooperative_unbound_slice(
                        specification,
                        "required executor or data-tier capability is unavailable",
                    ));
                    return Ok(cooperative_deferred(
                        task,
                        slices,
                        "one or more cooperative slices have unavailable capabilities",
                        now_ms,
                    ));
                }
                Err(error) => return Err(error),
            };
            if placement.decision != "selected"
                || placement.executor.is_none()
                || (specification
                    .input_bytes
                    .saturating_add(specification.output_bytes)
                    > 0
                    && placement.data_tier.is_none())
            {
                let reason = format!(
                    "slice {} placement {}: {}",
                    specification.id, placement.decision, placement.reason
                );
                slices.push(cooperative_unbound_slice(specification, &reason));
                return Ok(cooperative_deferred(task, slices, &reason, now_ms));
            }
            slices.push(cooperative_bound_slice(specification, &placement));
        }

        let buffers =
            build_cooperative_buffers(task, &slices, buffer_tier_id.clone(), capabilities, paths);
        let reservation = aggregate_cooperative_reservation(&slices, &buffers);
        if let Err(error) = inventory.ensure_fits(&reservation) {
            return Ok(cooperative_deferred(
                task,
                slices,
                &format!("cooperative reservation is not ready: {error:?}"),
                now_ms,
            ));
        }
        let estimated_makespan_us = cooperative_makespan(task.mode, &slices);
        Ok(Self {
            schema: COOPERATIVE_PLACEMENT_PLAN_SCHEMA_V1.to_string(),
            task_id: task.task_id,
            mode: task.mode,
            decision: "selected".to_string(),
            reason: match task.mode {
                CooperativePlacementMode::SingleExecutor => {
                    "explicit single-executor baseline selected".to_string()
                }
                CooperativePlacementMode::ParallelSlices => {
                    "explicit independent slices selected for parallel coordination".to_string()
                }
                CooperativePlacementMode::Pipeline => {
                    "explicit dependency stages selected with bounded backpressure".to_string()
                }
            },
            slices,
            buffers,
            reservation,
            reservation_status: "READY_ALL_OR_NONE".to_string(),
            estimated_makespan_us,
            planned_at_ms: now_ms,
        })
    }
}

impl CooperativeReservationLedger {
    /// Build a zero-side-effect accounting ledger from the observed inventory.
    /// Unknown capacities are represented as zero and therefore fail closed.
    pub fn from_inventory(
        capabilities: &[PlacementCapability],
        paths: &[PlacementTransferPath],
    ) -> Result<Self, PlacementError> {
        let mut capacity = CooperativeReservation::default();
        let mut used = CooperativeReservation::default();
        let mut capability_ids = BTreeSet::new();
        for capability in capabilities {
            if capability.id.trim().is_empty() || !capability_ids.insert(capability.id.clone()) {
                return Err(PlacementError::InvalidTask(
                    "cooperative capability identities must be non-empty and unique".to_string(),
                ));
            }
            match capability.domain {
                PlacementDomain::Cpu | PlacementDomain::Accelerator => {
                    insert_u32_capacity(
                        &mut capacity.executor_queue_slots,
                        &capability.id,
                        capability.queue_capacity.unwrap_or(0),
                    );
                    insert_u64_capacity(
                        &mut capacity.executor_memory_bytes,
                        &capability.id,
                        capability.usable_bytes.unwrap_or(0),
                    );
                    if capability.queue_depth > 0 {
                        used.executor_queue_slots
                            .insert(capability.id.clone(), capability.queue_depth);
                    }
                }
                PlacementDomain::HostMemory => {
                    insert_u64_capacity(
                        &mut capacity.data_tier_bytes,
                        &capability.id,
                        capability.usable_bytes.unwrap_or(0),
                    );
                }
                PlacementDomain::Storage => {
                    let available = capability.usable_bytes.unwrap_or(0);
                    insert_u64_capacity(&mut capacity.data_tier_bytes, &capability.id, available);
                    insert_u64_capacity(&mut capacity.storage_bytes, &capability.id, available);
                    insert_u32_capacity(
                        &mut capacity.storage_in_flight,
                        &capability.id,
                        capability.queue_capacity.unwrap_or(0),
                    );
                    if capability.queue_depth > 0 {
                        used.storage_in_flight
                            .insert(capability.id.clone(), capability.queue_depth);
                    }
                }
            }
        }
        let mut path_ids = BTreeSet::new();
        for path in paths {
            path.validate()?;
            if !path_ids.insert(path.id.clone()) {
                return Err(PlacementError::InvalidTask(
                    "cooperative transfer-path identities must be unique".to_string(),
                ));
            }
            insert_u32_capacity(
                &mut capacity.transfer_tokens,
                &path.id,
                path.transfer_token_capacity.unwrap_or(0),
            );
            if path.transfer_tokens_in_use > 0 {
                used.transfer_tokens
                    .insert(path.id.clone(), path.transfer_tokens_in_use);
            }
            insert_u64_capacity(
                &mut capacity.shared_memory_bytes,
                &path.id,
                path.shared_memory_bytes.unwrap_or(0),
            );
        }
        Ok(Self {
            capacity,
            used,
            next_lease_id: 1,
            active: BTreeMap::new(),
        })
    }

    pub fn capacity(&self) -> &CooperativeReservation {
        &self.capacity
    }

    pub fn used(&self) -> &CooperativeReservation {
        &self.used
    }

    pub fn active_leases(&self) -> usize {
        self.active.len()
    }

    /// Check a complete aggregate without mutating the ledger.
    pub fn ensure_fits(&self, reservation: &CooperativeReservation) -> Result<(), PlacementError> {
        ensure_u32_map_fits(
            "executor queue",
            &self.capacity.executor_queue_slots,
            &self.used.executor_queue_slots,
            &reservation.executor_queue_slots,
        )?;
        ensure_u64_map_fits(
            "executor memory",
            &self.capacity.executor_memory_bytes,
            &self.used.executor_memory_bytes,
            &reservation.executor_memory_bytes,
        )?;
        ensure_combined_u64_map_fits(
            "data tier",
            &self.capacity.data_tier_bytes,
            &self.used.data_tier_bytes,
            &self.used.buffer_bytes,
            &reservation.data_tier_bytes,
            &reservation.buffer_bytes,
        )?;
        ensure_u64_map_fits(
            "storage bytes",
            &self.capacity.storage_bytes,
            &self.used.storage_bytes,
            &reservation.storage_bytes,
        )?;
        ensure_u32_map_fits(
            "storage in-flight",
            &self.capacity.storage_in_flight,
            &self.used.storage_in_flight,
            &reservation.storage_in_flight,
        )?;
        ensure_u32_map_fits(
            "transfer tokens",
            &self.capacity.transfer_tokens,
            &self.used.transfer_tokens,
            &reservation.transfer_tokens,
        )?;
        ensure_u64_map_fits(
            "shared memory",
            &self.capacity.shared_memory_bytes,
            &self.used.shared_memory_bytes,
            &reservation.shared_memory_bytes,
        )?;
        Ok(())
    }

    /// Reserve all slices atomically.  Validation happens before `used` or
    /// `active` changes, so a partial cooperative lease cannot be created.
    pub fn reserve(
        &mut self,
        plan: &CooperativePlacementPlan,
    ) -> Result<CooperativeReservationLease, PlacementError> {
        if plan.decision != "selected" || plan.reservation_status != "READY_ALL_OR_NONE" {
            return Err(PlacementError::InvalidTask(
                "only a ready cooperative plan can be reserved".to_string(),
            ));
        }
        self.ensure_fits(&plan.reservation)?;
        let lease_id = self.next_lease_id;
        self.next_lease_id = self.next_lease_id.saturating_add(1);
        self.used = add_cooperative_reservation(&self.used, &plan.reservation);
        let lease = CooperativeReservationLease {
            lease_id,
            reservation: plan.reservation.clone(),
        };
        self.active.insert(lease_id, lease.clone());
        Ok(lease)
    }

    /// Cancel a complete lease and return its exact accounting.  A second
    /// cancellation is rejected, preserving conservation rather than silently
    /// minting capacity.
    pub fn cancel(&mut self, lease_id: u64) -> Result<CooperativeReservationLease, PlacementError> {
        let Some(lease) = self.active.remove(&lease_id) else {
            return Err(PlacementError::InvalidTask(
                "cooperative reservation lease is unknown or already cancelled".to_string(),
            ));
        };
        self.used = subtract_cooperative_reservation(&self.used, &lease.reservation);
        Ok(lease)
    }
}

fn cooperative_slice_task(
    task: &CooperativePlacementTask,
    specification: &CooperativeSliceSpec,
) -> PlacementTask {
    PlacementTask {
        schema: PLACEMENT_SCHEMA_V1.to_string(),
        task_id: task.task_id,
        input_bytes: specification.input_bytes,
        output_bytes: specification.output_bytes,
        working_memory_bytes: specification.working_memory_bytes,
        cpu_work_units: if specification.executor_domain == PlacementDomain::Cpu {
            specification.work_units
        } else {
            0
        },
        accelerator_work_units: if specification.executor_domain == PlacementDomain::Accelerator {
            specification.work_units
        } else {
            0
        },
        deadline_ms: task.deadline_ms,
        requires_accelerator: specification.executor_domain == PlacementDomain::Accelerator,
        allow_storage: task.allow_storage,
        privacy_local_only: task.requires_local_only,
    }
}

fn cooperative_bound_slice(
    specification: &CooperativeSliceSpec,
    placement: &PlacementPlan,
) -> CooperativePlacementSlice {
    let binding = placement.binding.as_ref();
    let data_bytes = specification
        .input_bytes
        .saturating_add(specification.output_bytes);
    let storage_bytes = if specification.data_tier_domain == PlacementDomain::Storage {
        data_bytes
    } else {
        0
    };
    let pair = placement.pair_candidates.iter().find(|candidate| {
        candidate.eligible
            && candidate.executor_id.as_deref() == placement.executor.as_deref()
            && candidate.data_tier_id.as_deref() == placement.data_tier.as_deref()
    });
    let mut depends_on = specification.depends_on.clone();
    depends_on.sort();
    CooperativePlacementSlice {
        id: specification.id.clone(),
        stage: specification.stage,
        depends_on,
        executor_domain: specification.executor_domain,
        data_tier_domain: specification.data_tier_domain,
        executor_id: placement.executor.clone(),
        data_tier_id: placement.data_tier.clone(),
        transfer_path_id: binding.and_then(|value| value.transfer_path_id.clone()),
        work_units: specification.work_units,
        input_bytes: specification.input_bytes,
        output_bytes: specification.output_bytes,
        working_memory_bytes: specification.working_memory_bytes,
        reservation: CooperativeSliceReservation {
            executor_queue_slots: 1,
            executor_memory_bytes: specification.working_memory_bytes,
            data_tier_bytes: data_bytes,
            storage_bytes,
            storage_in_flight: if storage_bytes > 0 { 1 } else { 0 },
            transfer_tokens: binding.map(|value| value.transfer_tokens).unwrap_or(0),
            shared_memory_bytes: binding.map(|value| value.shared_memory_bytes).unwrap_or(0),
        },
        estimated_latency_us: pair.and_then(|value| value.estimated_latency_us),
        confidence: pair
            .map(|value| value.confidence)
            .unwrap_or(PlacementConfidence::Unknown),
        decision: "selected".to_string(),
        reason: placement.reason.clone(),
    }
}

fn cooperative_unbound_slice(
    specification: &CooperativeSliceSpec,
    reason: &str,
) -> CooperativePlacementSlice {
    let mut depends_on = specification.depends_on.clone();
    depends_on.sort();
    CooperativePlacementSlice {
        id: specification.id.clone(),
        stage: specification.stage,
        depends_on,
        executor_domain: specification.executor_domain,
        data_tier_domain: specification.data_tier_domain,
        executor_id: None,
        data_tier_id: None,
        transfer_path_id: None,
        work_units: specification.work_units,
        input_bytes: specification.input_bytes,
        output_bytes: specification.output_bytes,
        working_memory_bytes: specification.working_memory_bytes,
        reservation: CooperativeSliceReservation::default(),
        estimated_latency_us: None,
        confidence: PlacementConfidence::Unknown,
        decision: "deferred".to_string(),
        reason: reason.to_string(),
    }
}

fn cooperative_deferred(
    task: &CooperativePlacementTask,
    mut slices: Vec<CooperativePlacementSlice>,
    reason: &str,
    planned_at_ms: u64,
) -> CooperativePlacementPlan {
    for slice in &mut slices {
        // A deferred plan is not a reservation or an executable binding.
        // Clear any earlier slice candidates so callers cannot mistake a
        // partially planned prefix for resources already owned by the task.
        slice.executor_id = None;
        slice.data_tier_id = None;
        slice.transfer_path_id = None;
        slice.estimated_latency_us = None;
        slice.confidence = PlacementConfidence::Unknown;
        slice.reservation = CooperativeSliceReservation::default();
        if slice.decision == "selected" {
            slice.decision = "deferred".to_string();
        }
    }
    CooperativePlacementPlan {
        schema: COOPERATIVE_PLACEMENT_PLAN_SCHEMA_V1.to_string(),
        task_id: task.task_id,
        mode: task.mode,
        decision: "deferred".to_string(),
        reason: reason.to_string(),
        slices,
        buffers: Vec::new(),
        reservation: CooperativeReservation::default(),
        reservation_status: "NOT_READY".to_string(),
        estimated_makespan_us: None,
        planned_at_ms,
    }
}

fn select_cooperative_buffer_tier(
    task: &CooperativePlacementTask,
    capabilities: &[PlacementCapability],
) -> Option<String> {
    capabilities
        .iter()
        .filter(|capability| {
            capability.domain == PlacementDomain::HostMemory
                && !capability.pressure
                && (!task.requires_local_only || capability.local_only)
                && capability
                    .usable_bytes
                    .is_some_and(|bytes| bytes >= task.buffer_policy.capacity_bytes)
        })
        .min_by(|left, right| {
            left.latency_us
                .cmp(&right.latency_us)
                .then_with(|| left.id.cmp(&right.id))
        })
        .map(|capability| capability.id.clone())
}

fn build_cooperative_buffers(
    task: &CooperativePlacementTask,
    slices: &[CooperativePlacementSlice],
    buffer_tier_id: Option<String>,
    capabilities: &[PlacementCapability],
    paths: &[PlacementTransferPath],
) -> Vec<CooperativeBufferPlan> {
    let Some(data_tier_id) = buffer_tier_id else {
        return Vec::new();
    };
    let policy = &task.buffer_policy;
    let spill_to_storage = policy.allow_spill_to_storage
        && capabilities.iter().any(|capability| {
            capability.domain == PlacementDomain::Storage
                && capability.usable_bytes.is_some_and(|bytes| bytes > 0)
        });
    // Slice identifiers are validated as unique before this helper runs.
    // Keep the producer-size lookup O(1) instead of scanning every slice for
    // every pipeline dependency; large explicit pipelines should spend their
    // time planning transfers, not rediscovering the same metadata.
    let output_bytes_by_id = slices
        .iter()
        .map(|slice| (slice.id.as_str(), slice.output_bytes))
        .collect::<BTreeMap<_, _>>();
    let executor_by_slice_id = slices
        .iter()
        .map(|slice| (slice.id.as_str(), slice.executor_id.as_deref()))
        .collect::<BTreeMap<_, _>>();
    let make_buffer = |id: String,
                       from_slice: Option<String>,
                       to_slice: Option<String>,
                       data_bytes: u64|
     -> CooperativeBufferPlan {
        let mut buffer = CooperativeBufferPlan {
            schema: COOPERATIVE_PLACEMENT_BUFFER_SCHEMA_V1.to_string(),
            id,
            from_slice,
            to_slice,
            data_tier_id: Some(data_tier_id.clone()),
            capacity_bytes: policy
                .capacity_bytes
                .min(data_bytes.max(u64::from(policy.max_in_flight_buffers))),
            max_in_flight_buffers: policy.max_in_flight_buffers,
            high_watermark_bytes: policy.high_watermark_bytes,
            low_watermark_bytes: policy.low_watermark_bytes,
            spill_to_storage: false,
            spill_reservation: None,
            backpressure: "BLOCK_PRODUCER_AT_HIGH_WATERMARK".to_string(),
        };
        if spill_to_storage {
            buffer.spill_reservation = select_cooperative_spill_reservation(
                task,
                &buffer,
                &executor_by_slice_id,
                capabilities,
                paths,
            );
            buffer.spill_to_storage = buffer.spill_reservation.is_some();
        }
        buffer
    };
    match task.mode {
        CooperativePlacementMode::SingleExecutor => Vec::new(),
        CooperativePlacementMode::ParallelSlices => slices
            .iter()
            .map(|slice| {
                make_buffer(
                    format!("cooperative-buffer-{}", slice.id),
                    None,
                    Some(slice.id.clone()),
                    slice.input_bytes.max(slice.output_bytes),
                )
            })
            .collect(),
        CooperativePlacementMode::Pipeline => {
            let mut buffers = Vec::new();
            for slice in slices {
                let mut dependencies = slice.depends_on.clone();
                dependencies.sort();
                for dependency in dependencies {
                    let producer_bytes = output_bytes_by_id
                        .get(dependency.as_str())
                        .copied()
                        .unwrap_or(0);
                    buffers.push(make_buffer(
                        format!("cooperative-buffer-{}-{}", dependency, slice.id),
                        Some(dependency),
                        Some(slice.id.clone()),
                        producer_bytes.max(slice.input_bytes),
                    ));
                }
            }
            buffers
        }
    }
}

fn select_cooperative_spill_reservation(
    task: &CooperativePlacementTask,
    buffer: &CooperativeBufferPlan,
    executor_by_slice_id: &BTreeMap<&str, Option<&str>>,
    capabilities: &[PlacementCapability],
    paths: &[PlacementTransferPath],
) -> Option<CooperativeSpillReservation> {
    if !task.allow_storage {
        return None;
    }
    let source_executor = buffer
        .from_slice
        .as_deref()
        .and_then(|slice_id| executor_by_slice_id.get(slice_id).copied().flatten())?;

    let mut best_candidate: Option<(&str, &str)> = None;
    for storage in capabilities.iter().filter(|capability| {
        capability.domain == PlacementDomain::Storage
            && !capability.pressure
            && (!task.requires_local_only || capability.local_only)
            && capability.confidence != PlacementConfidence::Unknown
            && capability
                .usable_bytes
                .is_some_and(|bytes| bytes >= buffer.capacity_bytes)
            && capability
                .queue_capacity
                .is_some_and(|capacity| capacity > capability.queue_depth)
    }) {
        let Some(path) = paths
            .iter()
            .filter(|path| {
                path.from_executor == source_executor
                    && path.to_data_tier == storage.id
                    && (!task.requires_local_only || path.local_only)
                    && path.confidence != PlacementConfidence::Unknown
                    && path
                        .bandwidth_bytes_per_s
                        .is_some_and(|bandwidth| bandwidth > 0)
                    && path.latency_us.is_some()
                    && path
                        .queue_capacity
                        .is_some_and(|capacity| capacity > path.queue_depth)
                    && path
                        .transfer_token_capacity
                        .is_some_and(|capacity| capacity > path.transfer_tokens_in_use)
            })
            .min_by(|left, right| left.id.cmp(&right.id))
        else {
            continue;
        };
        let candidate = (storage.id.as_str(), path.id.as_str());
        let is_better = best_candidate.as_ref().is_none_or(|best| {
            candidate.0 < best.0 || (candidate.0 == best.0 && candidate.1 < best.1)
        });
        if is_better {
            best_candidate = Some(candidate);
        }
    }

    best_candidate.map(
        |(storage_id, transfer_path_id)| CooperativeSpillReservation {
            schema: COOPERATIVE_PLACEMENT_SPILL_SCHEMA_V1.to_string(),
            storage_id: storage_id.to_string(),
            storage_bytes: buffer.capacity_bytes,
            storage_in_flight: 1,
            transfer_path_id: transfer_path_id.to_string(),
            transfer_tokens: 1,
        },
    )
}

fn aggregate_cooperative_reservation(
    slices: &[CooperativePlacementSlice],
    buffers: &[CooperativeBufferPlan],
) -> CooperativeReservation {
    let mut reservation = CooperativeReservation::default();
    for slice in slices {
        if let Some(executor_id) = &slice.executor_id {
            add_u32(
                &mut reservation.executor_queue_slots,
                executor_id,
                slice.reservation.executor_queue_slots,
            );
            add_u64(
                &mut reservation.executor_memory_bytes,
                executor_id,
                slice.reservation.executor_memory_bytes,
            );
        }
        if let Some(data_tier_id) = &slice.data_tier_id {
            add_u64(
                &mut reservation.data_tier_bytes,
                data_tier_id,
                slice.reservation.data_tier_bytes,
            );
            add_u64(
                &mut reservation.storage_bytes,
                data_tier_id,
                slice.reservation.storage_bytes,
            );
            add_u32(
                &mut reservation.storage_in_flight,
                data_tier_id,
                slice.reservation.storage_in_flight,
            );
        }
        if let Some(path_id) = &slice.transfer_path_id {
            add_u32(
                &mut reservation.transfer_tokens,
                path_id,
                slice.reservation.transfer_tokens,
            );
            add_u64(
                &mut reservation.shared_memory_bytes,
                path_id,
                slice.reservation.shared_memory_bytes,
            );
        }
    }
    for buffer in buffers {
        if let Some(data_tier_id) = &buffer.data_tier_id {
            add_u64(
                &mut reservation.buffer_bytes,
                data_tier_id,
                buffer.capacity_bytes,
            );
        }
        if let Some(spill) = &buffer.spill_reservation {
            add_u64(
                &mut reservation.storage_bytes,
                &spill.storage_id,
                spill.storage_bytes,
            );
            add_u32(
                &mut reservation.storage_in_flight,
                &spill.storage_id,
                spill.storage_in_flight,
            );
            add_u32(
                &mut reservation.transfer_tokens,
                &spill.transfer_path_id,
                spill.transfer_tokens,
            );
        }
    }
    reservation
}

fn cooperative_makespan(
    mode: CooperativePlacementMode,
    slices: &[CooperativePlacementSlice],
) -> Option<u64> {
    if slices
        .iter()
        .any(|slice| slice.estimated_latency_us.is_none())
    {
        return None;
    }
    match mode {
        CooperativePlacementMode::SingleExecutor => {
            slices.iter().try_fold(0_u64, |total, slice| {
                Some(total.saturating_add(slice.estimated_latency_us?))
            })
        }
        CooperativePlacementMode::ParallelSlices => slices
            .iter()
            .filter_map(|slice| slice.estimated_latency_us)
            .max(),
        CooperativePlacementMode::Pipeline => {
            let mut finish_times = BTreeMap::new();
            let mut ordered = slices.iter().collect::<Vec<_>>();
            ordered.sort_by(|left, right| {
                left.stage
                    .cmp(&right.stage)
                    .then_with(|| left.id.cmp(&right.id))
            });
            for slice in ordered {
                let mut start: u64 = 0;
                for dependency in &slice.depends_on {
                    start = start.max(*finish_times.get(dependency)?);
                }
                finish_times.insert(
                    slice.id.clone(),
                    start.saturating_add(slice.estimated_latency_us?),
                );
            }
            finish_times.values().copied().max()
        }
    }
}

fn insert_u32_capacity(map: &mut BTreeMap<String, u32>, id: &str, value: u32) {
    map.insert(id.to_string(), value);
}

fn insert_u64_capacity(map: &mut BTreeMap<String, u64>, id: &str, value: u64) {
    map.insert(id.to_string(), value);
}

fn add_u32(map: &mut BTreeMap<String, u32>, id: &str, value: u32) {
    if value == 0 {
        return;
    }
    let entry = map.entry(id.to_string()).or_default();
    *entry = entry.saturating_add(value);
}

fn add_u64(map: &mut BTreeMap<String, u64>, id: &str, value: u64) {
    if value == 0 {
        return;
    }
    let entry = map.entry(id.to_string()).or_default();
    *entry = entry.saturating_add(value);
}

fn ensure_u32_map_fits(
    resource: &str,
    capacity: &BTreeMap<String, u32>,
    used: &BTreeMap<String, u32>,
    requested: &BTreeMap<String, u32>,
) -> Result<(), PlacementError> {
    for (id, amount) in requested {
        if *amount == 0 {
            continue;
        }
        let Some(limit) = capacity.get(id) else {
            return Err(PlacementError::InvalidTask(format!(
                "{resource} capacity is unavailable for identity {id}"
            )));
        };
        let current = used.get(id).copied().unwrap_or(0);
        if current.saturating_add(*amount) > *limit {
            return Err(PlacementError::InvalidTask(format!(
                "{resource} capacity is exhausted for identity {id}"
            )));
        }
    }
    Ok(())
}

fn ensure_u64_map_fits(
    resource: &str,
    capacity: &BTreeMap<String, u64>,
    used: &BTreeMap<String, u64>,
    requested: &BTreeMap<String, u64>,
) -> Result<(), PlacementError> {
    for (id, amount) in requested {
        if *amount == 0 {
            continue;
        }
        let Some(limit) = capacity.get(id) else {
            return Err(PlacementError::InvalidTask(format!(
                "{resource} capacity is unavailable for identity {id}"
            )));
        };
        let current = used.get(id).copied().unwrap_or(0);
        if current.saturating_add(*amount) > *limit {
            return Err(PlacementError::InvalidTask(format!(
                "{resource} capacity is exhausted for identity {id}"
            )));
        }
    }
    Ok(())
}

fn ensure_combined_u64_map_fits(
    resource: &str,
    capacity: &BTreeMap<String, u64>,
    used_primary: &BTreeMap<String, u64>,
    used_buffers: &BTreeMap<String, u64>,
    requested_primary: &BTreeMap<String, u64>,
    requested_buffers: &BTreeMap<String, u64>,
) -> Result<(), PlacementError> {
    let ids = used_primary
        .keys()
        .chain(used_buffers.keys())
        .chain(requested_primary.keys())
        .chain(requested_buffers.keys())
        .collect::<BTreeSet<_>>();
    for id in ids {
        let current = used_primary
            .get(id)
            .copied()
            .unwrap_or(0)
            .saturating_add(used_buffers.get(id).copied().unwrap_or(0));
        let requested = requested_primary
            .get(id)
            .copied()
            .unwrap_or(0)
            .saturating_add(requested_buffers.get(id).copied().unwrap_or(0));
        if requested == 0 {
            continue;
        }
        let Some(limit) = capacity.get(id) else {
            return Err(PlacementError::InvalidTask(format!(
                "{resource} capacity is unavailable for identity {id}"
            )));
        };
        if current.saturating_add(requested) > *limit {
            return Err(PlacementError::InvalidTask(format!(
                "{resource} capacity is exhausted for identity {id}"
            )));
        }
    }
    Ok(())
}

fn add_cooperative_reservation(
    left: &CooperativeReservation,
    right: &CooperativeReservation,
) -> CooperativeReservation {
    let mut result = left.clone();
    for (id, value) in &right.executor_queue_slots {
        add_u32(&mut result.executor_queue_slots, id, *value);
    }
    for (id, value) in &right.executor_memory_bytes {
        add_u64(&mut result.executor_memory_bytes, id, *value);
    }
    for (id, value) in &right.data_tier_bytes {
        add_u64(&mut result.data_tier_bytes, id, *value);
    }
    for (id, value) in &right.buffer_bytes {
        add_u64(&mut result.buffer_bytes, id, *value);
    }
    for (id, value) in &right.storage_bytes {
        add_u64(&mut result.storage_bytes, id, *value);
    }
    for (id, value) in &right.storage_in_flight {
        add_u32(&mut result.storage_in_flight, id, *value);
    }
    for (id, value) in &right.transfer_tokens {
        add_u32(&mut result.transfer_tokens, id, *value);
    }
    for (id, value) in &right.shared_memory_bytes {
        add_u64(&mut result.shared_memory_bytes, id, *value);
    }
    result
}

fn subtract_cooperative_reservation(
    left: &CooperativeReservation,
    right: &CooperativeReservation,
) -> CooperativeReservation {
    let mut result = left.clone();
    for (id, value) in &right.executor_queue_slots {
        subtract_u32(&mut result.executor_queue_slots, id, *value);
    }
    for (id, value) in &right.executor_memory_bytes {
        subtract_u64(&mut result.executor_memory_bytes, id, *value);
    }
    for (id, value) in &right.data_tier_bytes {
        subtract_u64(&mut result.data_tier_bytes, id, *value);
    }
    for (id, value) in &right.buffer_bytes {
        subtract_u64(&mut result.buffer_bytes, id, *value);
    }
    for (id, value) in &right.storage_bytes {
        subtract_u64(&mut result.storage_bytes, id, *value);
    }
    for (id, value) in &right.storage_in_flight {
        subtract_u32(&mut result.storage_in_flight, id, *value);
    }
    for (id, value) in &right.transfer_tokens {
        subtract_u32(&mut result.transfer_tokens, id, *value);
    }
    for (id, value) in &right.shared_memory_bytes {
        subtract_u64(&mut result.shared_memory_bytes, id, *value);
    }
    result
}

fn subtract_u32(map: &mut BTreeMap<String, u32>, id: &str, value: u32) {
    if let Some(current) = map.get_mut(id) {
        *current = current.saturating_sub(value);
        if *current == 0 {
            map.remove(id);
        }
    }
}

fn subtract_u64(map: &mut BTreeMap<String, u64>, id: &str, value: u64) {
    if let Some(current) = map.get_mut(id) {
        *current = current.saturating_sub(value);
        if *current == 0 {
            map.remove(id);
        }
    }
}

fn evaluate_candidate(
    task: &PlacementTask,
    capability: &PlacementCapability,
    now_ms: u64,
) -> PlacementCandidate {
    let mut candidate = PlacementCandidate {
        id: capability.id.clone(),
        domain: capability.domain,
        eligible: true,
        estimated_latency_us: None,
        reason: "eligible".to_string(),
        confidence: capability.confidence,
    };

    if capability.id.trim().is_empty() {
        return ineligible(candidate, "resource id is empty");
    }
    let compute_domain = matches!(
        capability.domain,
        PlacementDomain::Cpu | PlacementDomain::Accelerator
    );
    if compute_domain
        && task.requires_accelerator
        && capability.domain != PlacementDomain::Accelerator
    {
        return ineligible(candidate, "task requires an accelerator");
    }
    if capability.domain == PlacementDomain::Accelerator && task.accelerator_work_units == 0 {
        return ineligible(candidate, "task has no accelerator work");
    }
    if capability.domain == PlacementDomain::Cpu && task.cpu_work_units == 0 {
        return ineligible(candidate, "task has no CPU work");
    }
    if !compute_domain && task.input_bytes.saturating_add(task.output_bytes) == 0 {
        return ineligible(candidate, "task has no data requiring a residency tier");
    }
    if capability.domain == PlacementDomain::Storage && !task.allow_storage {
        return ineligible(candidate, "storage placement is not allowed for this task");
    }
    if task.privacy_local_only && !capability.local_only {
        return ineligible(candidate, "privacy policy requires a local resource");
    }
    if capability.pressure {
        return ineligible(candidate, "resource is under pressure");
    }
    if capability.confidence == PlacementConfidence::Unknown {
        candidate.reason = "resource measurement confidence is unknown".to_string();
        return candidate;
    }
    if capability
        .queue_capacity
        .is_some_and(|capacity| capacity == 0 || capability.queue_depth >= capacity)
    {
        return ineligible(candidate, "resource queue is full");
    }
    let data_bytes = task.input_bytes.saturating_add(task.output_bytes);
    let required_bytes = match capability.domain {
        PlacementDomain::HostMemory => task.working_memory_bytes.saturating_add(data_bytes),
        PlacementDomain::Storage => data_bytes,
        PlacementDomain::Cpu | PlacementDomain::Accelerator => task.working_memory_bytes,
    };
    if capability
        .usable_bytes
        .is_some_and(|available| available < required_bytes)
    {
        return ineligible(
            candidate,
            "working set or data residency does not fit the available resource memory",
        );
    }
    if required_bytes > 0 && capability.usable_bytes.is_none() {
        candidate.reason = "usable resource memory is unknown".to_string();
        return candidate;
    }

    let work_units = match capability.domain {
        PlacementDomain::Accelerator => task.accelerator_work_units,
        PlacementDomain::Cpu => task.cpu_work_units,
        PlacementDomain::HostMemory | PlacementDomain::Storage => 0,
    };
    let compute_cost = if work_units == 0 {
        0
    } else {
        let Some(throughput) = capability.compute_units_per_us else {
            candidate.reason = "compute throughput is unknown".to_string();
            return candidate;
        };
        if throughput == 0 {
            return ineligible(candidate, "resource compute throughput is zero");
        }
        div_ceil(work_units, throughput)
    };
    let queue_cost =
        u64::from(capability.queue_depth).saturating_mul(capability.latency_us.unwrap_or(0));
    // Transfer latency/bandwidth belongs to the executor↔data-tier path, not
    // to either endpoint.  Keeping endpoint scoring independent here is only
    // for candidate diagnostics; selection below is pairwise.
    let latency_cost = capability.latency_us.unwrap_or(0);
    let total = compute_cost
        .saturating_add(queue_cost)
        .saturating_add(latency_cost);

    if task.deadline_ms.is_some_and(|deadline| {
        deadline <= now_ms || total > deadline.saturating_sub(now_ms).saturating_mul(1_000)
    }) {
        return ineligible(candidate, "estimated cost misses the task deadline");
    }
    candidate.estimated_latency_us = Some(total);
    candidate
}

fn evaluate_pair(
    task: &PlacementTask,
    executor: Option<&PlacementCandidate>,
    data_tier: Option<&PlacementCandidate>,
    paths: &[PlacementTransferPath],
    now_ms: u64,
) -> PlacementPairCandidate {
    let executor_id = executor.map(|candidate| candidate.id.clone());
    let data_tier_id = data_tier.map(|candidate| candidate.id.clone());
    let base_confidence = executor
        .map(|candidate| candidate.confidence)
        .into_iter()
        .chain(data_tier.map(|candidate| candidate.confidence))
        .fold(PlacementConfidence::Measured, combine_confidence);
    let mut pair = PlacementPairCandidate {
        executor_id: executor_id.clone(),
        data_tier_id: data_tier_id.clone(),
        transfer_path_id: None,
        eligible: true,
        estimated_latency_us: None,
        shared_memory_bytes: 0,
        reason: "eligible".to_string(),
        confidence: base_confidence,
    };

    if let Some(candidate) = executor {
        if !candidate.eligible {
            return pair_ineligible(pair, "executor candidate is ineligible");
        }
        let Some(_) = candidate.estimated_latency_us else {
            pair.reason = "executor cost is unknown".to_string();
            pair.confidence = combine_confidence(pair.confidence, PlacementConfidence::Unknown);
            return pair;
        };
    } else if task.cpu_work_units > 0 || task.accelerator_work_units > 0 {
        return pair_ineligible(pair, "required executor is missing");
    }

    if let Some(candidate) = data_tier {
        if !candidate.eligible {
            return pair_ineligible(pair, "data-tier candidate is ineligible");
        }
        let Some(_) = candidate.estimated_latency_us else {
            pair.reason = "data-tier cost is unknown".to_string();
            pair.confidence = combine_confidence(pair.confidence, PlacementConfidence::Unknown);
            return pair;
        };
    } else if task.input_bytes > 0 || task.output_bytes > 0 {
        return pair_ineligible(pair, "required data tier is missing");
    }

    let data_bytes = task.input_bytes.saturating_add(task.output_bytes);
    let mut path_cost = 0_u64;
    if data_bytes > 0 {
        let executor_domain = executor.map(|candidate| candidate.domain);
        let data_domain = data_tier.map(|candidate| candidate.domain);

        // CPU access to host RAM is the one bounded path that requires no
        // transfer token.  Accelerator/UMA and storage routes still need an
        // explicit observation so the planner cannot assume zero-copy.
        let host_coherent = executor_domain == Some(PlacementDomain::Cpu)
            && data_domain == Some(PlacementDomain::HostMemory);
        if !host_coherent {
            let matching_paths = paths
                .iter()
                .filter(|path| {
                    path.from_executor == executor_id.as_deref().unwrap_or_default()
                        && path.to_data_tier == data_tier_id.as_deref().unwrap_or_default()
                })
                .collect::<Vec<_>>();
            let Some(path) = matching_paths
                .iter()
                .copied()
                .min_by(|left, right| left.id.cmp(&right.id))
            else {
                pair.reason =
                    "executor/data-tier transfer path is unknown; placement deferred".to_string();
                pair.confidence = combine_confidence(pair.confidence, PlacementConfidence::Unknown);
                return pair;
            };
            if matching_paths
                .iter()
                .skip(1)
                .any(|other| other.id == path.id)
            {
                return pair_ineligible(pair, "transfer path identity is ambiguous");
            }
            pair.transfer_path_id = Some(path.id.clone());
            pair.confidence = combine_confidence(pair.confidence, path.confidence);
            if task.privacy_local_only && !path.local_only {
                return pair_ineligible(pair, "privacy policy requires a local transfer path");
            }
            if path.confidence == PlacementConfidence::Unknown {
                pair.reason = "transfer path measurement confidence is unknown".to_string();
                return pair;
            }
            if path
                .queue_capacity
                .is_some_and(|capacity| capacity == 0 || path.queue_depth >= capacity)
            {
                return pair_ineligible(pair, "transfer path queue is full");
            }
            if path
                .transfer_token_capacity
                .is_none_or(|capacity| capacity == 0)
            {
                pair.reason = "transfer token capacity is unknown".to_string();
                pair.confidence = combine_confidence(pair.confidence, PlacementConfidence::Unknown);
                return pair;
            }
            if path
                .transfer_token_capacity
                .is_some_and(|capacity| path.transfer_tokens_in_use >= capacity)
            {
                return pair_ineligible(pair, "transfer token budget is exhausted");
            }

            if let Some(shared_capacity) = path.shared_memory_bytes {
                let required_shared = task.working_memory_bytes.saturating_add(data_bytes);
                if shared_capacity < required_shared {
                    return pair_ineligible(pair, "UMA/shared-memory budget is insufficient");
                }
                pair.shared_memory_bytes = required_shared;
            } else {
                let Some(bandwidth) = path.bandwidth_bytes_per_s else {
                    pair.reason = "transfer bandwidth is unknown".to_string();
                    pair.confidence =
                        combine_confidence(pair.confidence, PlacementConfidence::Unknown);
                    return pair;
                };
                if bandwidth == 0 {
                    return pair_ineligible(pair, "transfer bandwidth is zero");
                }
                path_cost = path_cost
                    .saturating_add(div_ceil(data_bytes.saturating_mul(1_000_000), bandwidth));
            }
            let Some(latency) = path.latency_us else {
                pair.reason = "transfer latency is unknown".to_string();
                pair.confidence = combine_confidence(pair.confidence, PlacementConfidence::Unknown);
                return pair;
            };
            path_cost = path_cost
                .saturating_add(latency)
                .saturating_add(u64::from(path.queue_depth).saturating_mul(latency));
        }
    }

    let executor_cost = executor
        .and_then(|candidate| candidate.estimated_latency_us)
        .unwrap_or(0);
    let data_cost = data_tier
        .and_then(|candidate| candidate.estimated_latency_us)
        .unwrap_or(0);
    let total = executor_cost
        .saturating_add(data_cost)
        .saturating_add(path_cost);
    if task.deadline_ms.is_some_and(|deadline| {
        deadline <= now_ms || total > deadline.saturating_sub(now_ms).saturating_mul(1_000)
    }) {
        return pair_ineligible(pair, "estimated pair cost misses the task deadline");
    }
    pair.estimated_latency_us = Some(total);
    pair
}

fn pair_ineligible(mut pair: PlacementPairCandidate, reason: &str) -> PlacementPairCandidate {
    pair.eligible = false;
    pair.reason = reason.to_string();
    pair
}

fn combine_confidence(
    left: PlacementConfidence,
    right: PlacementConfidence,
) -> PlacementConfidence {
    match (left, right) {
        (PlacementConfidence::Unknown, _) | (_, PlacementConfidence::Unknown) => {
            PlacementConfidence::Unknown
        }
        (PlacementConfidence::Estimated, _) | (_, PlacementConfidence::Estimated) => {
            PlacementConfidence::Estimated
        }
        _ => PlacementConfidence::Measured,
    }
}

fn ineligible(mut candidate: PlacementCandidate, reason: &str) -> PlacementCandidate {
    candidate.eligible = false;
    candidate.reason = reason.to_string();
    candidate
}

fn div_ceil(value: u64, divisor: u64) -> u64 {
    value.saturating_add(divisor.saturating_sub(1)) / divisor.max(1)
}

/// A bounded exponentially weighted moving average for observed costs.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Ewma {
    /// Alpha in per-thousand units. 100 means slow, 1000 means newest sample.
    pub alpha_per_mille: u16,
    pub value: Option<u64>,
}

impl Ewma {
    pub fn new(alpha_per_mille: u16) -> Result<Self, PlacementError> {
        if !(1..=1_000).contains(&alpha_per_mille) {
            return Err(PlacementError::InvalidTask(
                "EWMA alpha must be between 1 and 1000 per mille".to_string(),
            ));
        }
        Ok(Self {
            alpha_per_mille,
            value: None,
        })
    }

    pub fn observe(&mut self, sample: u64) -> u64 {
        let next = match self.value {
            None => sample,
            Some(previous) => {
                let alpha = u64::from(self.alpha_per_mille);
                previous
                    .saturating_mul(1_000 - alpha)
                    .saturating_add(sample.saturating_mul(alpha))
                    / 1_000
            }
        };
        self.value = Some(next);
        next
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn task() -> PlacementTask {
        PlacementTask {
            schema: PLACEMENT_SCHEMA_V1.to_string(),
            task_id: 1,
            input_bytes: 1_000_000,
            output_bytes: 1_000,
            working_memory_bytes: 1024,
            cpu_work_units: 100,
            accelerator_work_units: 10,
            deadline_ms: None,
            requires_accelerator: false,
            allow_storage: true,
            privacy_local_only: true,
        }
    }

    fn cpu(id: &str, throughput: u64) -> PlacementCapability {
        PlacementCapability {
            id: id.to_string(),
            domain: PlacementDomain::Cpu,
            usable_bytes: Some(4 * 1024 * 1024),
            compute_units_per_us: Some(throughput),
            bandwidth_bytes_per_s: None,
            latency_us: Some(2),
            queue_depth: 0,
            queue_capacity: Some(4),
            pressure: false,
            local_only: true,
            confidence: PlacementConfidence::Measured,
            transfer_paths: Vec::new(),
        }
    }

    fn host_memory(id: &str, usable_bytes: u64) -> PlacementCapability {
        PlacementCapability {
            id: id.to_string(),
            domain: PlacementDomain::HostMemory,
            usable_bytes: Some(usable_bytes),
            compute_units_per_us: None,
            bandwidth_bytes_per_s: None,
            latency_us: Some(1),
            queue_depth: 0,
            queue_capacity: Some(8),
            pressure: false,
            local_only: true,
            confidence: PlacementConfidence::Measured,
            transfer_paths: Vec::new(),
        }
    }

    fn path(
        id: &str,
        from_executor: &str,
        to_data_tier: &str,
        bandwidth_bytes_per_s: Option<u64>,
        shared_memory_bytes: Option<u64>,
    ) -> PlacementTransferPath {
        PlacementTransferPath {
            schema: PLACEMENT_TRANSFER_PATH_SCHEMA_V1.to_string(),
            id: id.to_string(),
            from_executor: from_executor.to_string(),
            to_data_tier: to_data_tier.to_string(),
            bandwidth_bytes_per_s,
            latency_us: Some(5),
            queue_depth: 0,
            queue_capacity: Some(4),
            transfer_token_capacity: Some(4),
            transfer_tokens_in_use: 0,
            shared_memory_bytes,
            local_only: true,
            confidence: PlacementConfidence::Measured,
        }
    }

    #[test]
    fn selects_lower_total_cost_and_accounts_for_transfer() {
        let mut fast_cpu = cpu("cpu-fast", 1);
        fast_cpu.queue_depth = 20;
        let plan = PlacementPlan::build(
            &task(),
            &[
                fast_cpu,
                host_memory("ram-hot", 4 * 1024 * 1024),
                PlacementCapability {
                    id: "accelerator-batch".to_string(),
                    domain: PlacementDomain::Accelerator,
                    usable_bytes: Some(4 * 1024 * 1024),
                    compute_units_per_us: Some(100),
                    bandwidth_bytes_per_s: Some(1_000_000_000),
                    latency_us: Some(20),
                    queue_depth: 0,
                    queue_capacity: Some(2),
                    pressure: false,
                    local_only: true,
                    confidence: PlacementConfidence::Measured,
                    transfer_paths: vec![PlacementTransferPath {
                        schema: PLACEMENT_TRANSFER_PATH_SCHEMA_V1.to_string(),
                        id: "path-accelerator-to-ram".to_string(),
                        from_executor: "accelerator-batch".to_string(),
                        to_data_tier: "ram-hot".to_string(),
                        bandwidth_bytes_per_s: Some(1_000_000_000),
                        latency_us: Some(20),
                        queue_depth: 0,
                        queue_capacity: Some(2),
                        transfer_token_capacity: Some(2),
                        transfer_tokens_in_use: 0,
                        shared_memory_bytes: None,
                        local_only: true,
                        confidence: PlacementConfidence::Measured,
                    }],
                },
            ],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.selected.as_deref(), Some("accelerator-batch"));
        assert_eq!(plan.executor.as_deref(), Some("accelerator-batch"));
        assert_eq!(plan.data_tier.as_deref(), Some("ram-hot"));
        assert_eq!(plan.decision, "selected");
    }

    #[test]
    fn unknown_cost_defers_instead_of_claiming_a_fast_resource() {
        let mut unknown = cpu("cpu", 1);
        unknown.compute_units_per_us = None;
        let plan = PlacementPlan::build(
            &task(),
            &[unknown, host_memory("ram-hot", 4 * 1024 * 1024)],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.selected, None);
        assert_eq!(plan.decision, "deferred");
        assert!(plan.candidates[0].eligible);
    }

    #[test]
    fn pressure_and_privacy_are_hard_constraints() {
        let mut pressured = cpu("pressured", 100);
        pressured.pressure = true;
        let mut external = cpu("external", 100);
        external.local_only = false;
        let plan = PlacementPlan::build(
            &task(),
            &[pressured, external, host_memory("ram-hot", 4 * 1024 * 1024)],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.decision, "rejected");
        assert!(
            plan.candidates
                .iter()
                .filter(|candidate| candidate.domain == PlacementDomain::Cpu)
                .all(|candidate| !candidate.eligible)
        );
    }

    #[test]
    fn data_residency_can_fall_back_to_storage_without_making_storage_compute() {
        let mut small_ram = host_memory("ram-hot", 512);
        small_ram.latency_us = Some(1);
        let storage = PlacementCapability {
            id: "ssd-cold".to_string(),
            domain: PlacementDomain::Storage,
            usable_bytes: Some(8 * 1024 * 1024),
            compute_units_per_us: None,
            bandwidth_bytes_per_s: Some(500_000_000),
            latency_us: Some(80),
            queue_depth: 0,
            queue_capacity: Some(4),
            pressure: false,
            local_only: true,
            confidence: PlacementConfidence::Measured,
            transfer_paths: vec![PlacementTransferPath {
                schema: PLACEMENT_TRANSFER_PATH_SCHEMA_V1.to_string(),
                id: "path-cpu-to-ssd".to_string(),
                from_executor: "cpu".to_string(),
                to_data_tier: "ssd-cold".to_string(),
                bandwidth_bytes_per_s: Some(500_000_000),
                latency_us: Some(80),
                queue_depth: 0,
                queue_capacity: Some(4),
                transfer_token_capacity: Some(4),
                transfer_tokens_in_use: 0,
                shared_memory_bytes: None,
                local_only: true,
                confidence: PlacementConfidence::Measured,
            }],
        };
        let plan =
            PlacementPlan::build(&task(), &[cpu("cpu", 10), small_ram, storage], 1_000).unwrap();
        assert_eq!(plan.executor.as_deref(), Some("cpu"));
        assert_eq!(plan.data_tier.as_deref(), Some("ssd-cold"));
        assert_eq!(plan.selected.as_deref(), Some("cpu"));
        assert_eq!(plan.decision, "selected");
        let binding = plan.binding.as_ref().expect("selected binding");
        assert_eq!(binding.transfer_path_id.as_deref(), Some("path-cpu-to-ssd"));
        assert_eq!(binding.storage_bytes, 1_001_000);
        assert_eq!(binding.storage_in_flight, 1);
        assert_eq!(binding.transfer_tokens, 1);
    }

    #[test]
    fn pairwise_selection_rejects_fast_endpoints_with_a_slow_link() {
        let mut pairwise_task = task();
        pairwise_task.cpu_work_units = 0;
        pairwise_task.accelerator_work_units = 100;
        pairwise_task.requires_accelerator = true;
        let mut fast_accelerator = cpu("accelerator-fast", 100);
        fast_accelerator.domain = PlacementDomain::Accelerator;
        fast_accelerator.transfer_paths = vec![path(
            "path-fast-slow",
            "accelerator-fast",
            "ram-hot",
            Some(1_000_000),
            None,
        )];
        let mut slow_accelerator = cpu("accelerator-slow", 10);
        slow_accelerator.domain = PlacementDomain::Accelerator;
        slow_accelerator.transfer_paths = vec![path(
            "path-slow-fast",
            "accelerator-slow",
            "ram-hot",
            Some(1_000_000_000_000),
            None,
        )];
        let plan = PlacementPlan::build(
            &pairwise_task,
            &[
                fast_accelerator,
                slow_accelerator,
                host_memory("ram-hot", 4 * 1024 * 1024),
            ],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.decision, "selected");
        assert_eq!(plan.executor.as_deref(), Some("accelerator-slow"));
        assert_eq!(plan.data_tier.as_deref(), Some("ram-hot"));
        assert_eq!(
            plan.binding
                .as_ref()
                .and_then(|binding| binding.transfer_path_id.as_deref()),
            Some("path-slow-fast")
        );
    }

    #[test]
    fn unknown_accelerator_path_defers_instead_of_assuming_uma() {
        let mut accelerator_task = task();
        accelerator_task.requires_accelerator = true;
        let accelerator = PlacementCapability {
            id: "gpu-0".to_string(),
            domain: PlacementDomain::Accelerator,
            usable_bytes: Some(4 * 1024 * 1024),
            compute_units_per_us: Some(100),
            bandwidth_bytes_per_s: None,
            latency_us: Some(2),
            queue_depth: 0,
            queue_capacity: Some(1),
            pressure: false,
            local_only: true,
            confidence: PlacementConfidence::Measured,
            transfer_paths: Vec::new(),
        };
        let plan = PlacementPlan::build(
            &accelerator_task,
            &[accelerator, host_memory("ram-hot", 4 * 1024 * 1024)],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.decision, "deferred");
        assert!(
            plan.pair_candidates
                .iter()
                .any(|pair| pair.reason.contains("transfer path"))
        );
    }

    #[test]
    fn uma_shared_memory_is_budgeted_without_claiming_zero_copy() {
        let mut accelerator_task = task();
        accelerator_task.requires_accelerator = true;
        let accelerator = PlacementCapability {
            id: "igpu-0".to_string(),
            domain: PlacementDomain::Accelerator,
            usable_bytes: Some(4 * 1024 * 1024),
            compute_units_per_us: Some(100),
            bandwidth_bytes_per_s: None,
            latency_us: Some(2),
            queue_depth: 0,
            queue_capacity: Some(1),
            pressure: false,
            local_only: true,
            confidence: PlacementConfidence::Measured,
            transfer_paths: vec![path(
                "path-igpu-uma",
                "igpu-0",
                "ram-hot",
                None,
                Some(1_001_000 + accelerator_task.working_memory_bytes),
            )],
        };
        let plan = PlacementPlan::build(
            &accelerator_task,
            &[accelerator, host_memory("ram-hot", 4 * 1024 * 1024)],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.decision, "selected");
        let binding = plan.binding.as_ref().expect("selected UMA binding");
        assert_eq!(binding.executor_id, "igpu-0");
        assert_eq!(binding.shared_memory_bytes, 1_001_000 + 1024);
        assert_eq!(binding.transfer_tokens, 1);
        assert_eq!(binding.storage_bytes, 0);
    }

    fn cooperative_slice(
        id: &str,
        stage: u32,
        depends_on: &[&str],
        executor_domain: PlacementDomain,
        data_tier_domain: PlacementDomain,
        input_bytes: u64,
        output_bytes: u64,
    ) -> CooperativeSliceSpec {
        CooperativeSliceSpec {
            id: id.to_string(),
            stage,
            depends_on: depends_on
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
            executor_domain,
            data_tier_domain,
            required_data_tier_id: None,
            input_bytes,
            output_bytes,
            working_memory_bytes: 1024,
            work_units: 100,
        }
    }

    fn cooperative_task(
        mode: CooperativePlacementMode,
        slices: Vec<CooperativeSliceSpec>,
    ) -> CooperativePlacementTask {
        let buffer_policy = if mode == CooperativePlacementMode::SingleExecutor {
            CooperativeBufferPolicy {
                capacity_bytes: 0,
                max_in_flight_buffers: 0,
                high_watermark_bytes: 0,
                low_watermark_bytes: 0,
                allow_spill_to_storage: false,
            }
        } else {
            CooperativeBufferPolicy {
                capacity_bytes: 4096,
                max_in_flight_buffers: 2,
                high_watermark_bytes: 3072,
                low_watermark_bytes: 1024,
                allow_spill_to_storage: true,
            }
        };
        CooperativePlacementTask {
            schema: COOPERATIVE_PLACEMENT_TASK_SCHEMA_V1.to_string(),
            task_id: 7,
            mode,
            slices,
            buffer_policy,
            deadline_ms: None,
            requires_local_only: true,
            allow_storage: true,
        }
    }

    fn accelerator(id: &str, transfer_path: Option<PlacementTransferPath>) -> PlacementCapability {
        let mut capability = cpu(id, 100);
        capability.domain = PlacementDomain::Accelerator;
        capability.transfer_paths = transfer_path.into_iter().collect();
        capability
    }

    fn storage(id: &str) -> PlacementCapability {
        PlacementCapability {
            id: id.to_string(),
            domain: PlacementDomain::Storage,
            usable_bytes: Some(32 * 1024 * 1024),
            compute_units_per_us: None,
            bandwidth_bytes_per_s: Some(1_000_000_000),
            latency_us: Some(40),
            queue_depth: 0,
            queue_capacity: Some(4),
            pressure: false,
            local_only: true,
            confidence: PlacementConfidence::Measured,
            transfer_paths: Vec::new(),
        }
    }

    #[test]
    fn cooperative_parallel_slices_use_explicit_cpu_and_accelerator_lanes() {
        let gpu_path = path(
            "path-gpu-ram",
            "gpu-0",
            "ram-hot",
            Some(1_000_000_000),
            None,
        );
        let task = cooperative_task(
            CooperativePlacementMode::ParallelSlices,
            vec![
                cooperative_slice(
                    "cpu-slice",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    2048,
                    1024,
                ),
                cooperative_slice(
                    "gpu-slice",
                    0,
                    &[],
                    PlacementDomain::Accelerator,
                    PlacementDomain::HostMemory,
                    2048,
                    1024,
                ),
            ],
        );
        let capabilities = vec![
            cpu("cpu-0", 100),
            accelerator("gpu-0", Some(gpu_path.clone())),
            host_memory("ram-hot", 16 * 1024 * 1024),
        ];
        let plan =
            CooperativePlacementPlan::build(&task, &capabilities, &[gpu_path], 1_000).unwrap();
        assert_eq!(plan.decision, "selected");
        assert_eq!(plan.mode, CooperativePlacementMode::ParallelSlices);
        assert_eq!(plan.slices.len(), 2);
        assert_eq!(plan.buffers.len(), 2);
        assert_eq!(plan.slices[0].executor_id.as_deref(), Some("cpu-0"));
        assert_eq!(plan.slices[1].executor_id.as_deref(), Some("gpu-0"));
        assert_eq!(plan.reservation.executor_queue_slots.len(), 2);
        assert_eq!(plan.reservation_status, "READY_ALL_OR_NONE");
        assert!(
            plan.buffers
                .iter()
                .all(|buffer| buffer.backpressure == "BLOCK_PRODUCER_AT_HIGH_WATERMARK")
        );
    }

    #[test]
    fn cooperative_pipeline_keeps_ram_buffer_and_ssd_residency_distinct() {
        let cpu_path = path("path-cpu-ssd", "cpu-0", "ssd-cold", Some(500_000_000), None);
        let gpu_path = path(
            "path-gpu-ssd",
            "gpu-0",
            "ssd-cold",
            Some(1_000_000_000),
            None,
        );
        let task = cooperative_task(
            CooperativePlacementMode::Pipeline,
            vec![
                cooperative_slice(
                    "preprocess",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    4096,
                    4096,
                ),
                cooperative_slice(
                    "accelerated-stage",
                    1,
                    &["preprocess"],
                    PlacementDomain::Accelerator,
                    PlacementDomain::Storage,
                    4096,
                    4096,
                ),
            ],
        );
        let capabilities = vec![
            cpu("cpu-0", 100),
            accelerator("gpu-0", Some(gpu_path.clone())),
            host_memory("ram-hot", 16 * 1024 * 1024),
            storage("ssd-cold"),
        ];
        let paths = vec![cpu_path, gpu_path];
        let plan = CooperativePlacementPlan::build(&task, &capabilities, &paths, 1_000).unwrap();
        assert_eq!(plan.decision, "selected");
        assert_eq!(plan.buffers.len(), 1);
        assert_eq!(plan.buffers[0].data_tier_id.as_deref(), Some("ram-hot"));
        assert!(plan.buffers[0].spill_to_storage);
        let spill = plan.buffers[0]
            .spill_reservation
            .as_ref()
            .expect("validated spill reservation");
        assert_eq!(spill.storage_id, "ssd-cold");
        assert_eq!(spill.storage_bytes, plan.buffers[0].capacity_bytes);
        assert_eq!(spill.storage_in_flight, 1);
        assert_eq!(spill.transfer_path_id, "path-cpu-ssd");
        assert_eq!(spill.transfer_tokens, 1);
        assert!(plan.buffers[0].validate().is_ok());
        assert!(plan.buffers[0].capacity_bytes <= task.buffer_policy.capacity_bytes);
        assert!(plan.buffers[0].low_watermark_bytes <= plan.buffers[0].high_watermark_bytes);
        assert_eq!(plan.slices[0].data_tier_id.as_deref(), Some("ram-hot"));
        assert_eq!(plan.slices[1].data_tier_id.as_deref(), Some("ssd-cold"));
        assert!(plan.estimated_makespan_us.is_some());
        assert_eq!(
            plan.reservation.storage_bytes.get("ssd-cold"),
            Some(&12_288)
        );
        assert_eq!(plan.reservation.storage_in_flight.get("ssd-cold"), Some(&2));
        assert_eq!(
            plan.reservation.transfer_tokens.get("path-cpu-ssd"),
            Some(&1)
        );
        assert_eq!(
            plan.reservation.transfer_tokens.get("path-gpu-ssd"),
            Some(&1)
        );

        let mut ledger =
            CooperativeReservationLedger::from_inventory(&capabilities, &paths).unwrap();
        let baseline = ledger.used().clone();
        let lease = ledger.reserve(&plan).unwrap();
        assert_eq!(ledger.active_leases(), 1);
        assert_eq!(ledger.cancel(lease.lease_id).unwrap(), lease);
        assert_eq!(ledger.active_leases(), 0);
        assert_eq!(ledger.used(), &baseline);
        assert!(ledger.cancel(lease.lease_id).is_err());
    }

    #[test]
    fn cooperative_buffer_uses_task_bound_without_host_queue_claim() {
        let mut ram = host_memory("ram-hot", 16 * 1024 * 1024);
        ram.queue_capacity = None;
        let task = cooperative_task(
            CooperativePlacementMode::Pipeline,
            vec![
                cooperative_slice(
                    "stage-a",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    4096,
                    4096,
                ),
                cooperative_slice(
                    "stage-b",
                    1,
                    &["stage-a"],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    4096,
                    4096,
                ),
            ],
        );
        let plan =
            CooperativePlacementPlan::build(&task, &[cpu("cpu-0", 100), ram], &[], 1_000).unwrap();
        assert_eq!(plan.decision, "selected");
        assert_eq!(plan.reservation_status, "READY_ALL_OR_NONE");
        assert_eq!(plan.buffers.len(), 1);
        assert_eq!(plan.buffers[0].data_tier_id.as_deref(), Some("ram-hot"));
        assert!(!plan.buffers[0].spill_to_storage);
    }

    #[test]
    fn cooperative_unknown_accelerator_path_defers_without_partial_reservation() {
        let task = cooperative_task(
            CooperativePlacementMode::Pipeline,
            vec![
                cooperative_slice(
                    "cpu-stage",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    1024,
                    1024,
                ),
                cooperative_slice(
                    "gpu-stage",
                    1,
                    &["cpu-stage"],
                    PlacementDomain::Accelerator,
                    PlacementDomain::HostMemory,
                    1024,
                    1024,
                ),
            ],
        );
        let plan = CooperativePlacementPlan::build(
            &task,
            &[
                cpu("cpu-0", 100),
                accelerator("gpu-0", None),
                host_memory("ram-hot", 16 * 1024 * 1024),
            ],
            &[],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.decision, "deferred");
        assert_eq!(plan.reservation_status, "NOT_READY");
        assert_eq!(plan.reservation, CooperativeReservation::default());
        assert!(
            plan.slices
                .iter()
                .all(|slice| slice.reservation == CooperativeSliceReservation::default())
        );
        assert!(plan.slices.iter().all(|slice| {
            slice.executor_id.is_none()
                && slice.data_tier_id.is_none()
                && slice.transfer_path_id.is_none()
                && slice.estimated_latency_us.is_none()
        }));
        assert!(plan.reason.contains("transfer path"));
    }

    #[test]
    fn cooperative_spill_requires_a_validated_storage_path() {
        let gpu_path = path(
            "path-gpu-ssd",
            "gpu-0",
            "ssd-cold",
            Some(1_000_000_000),
            None,
        );
        let task = cooperative_task(
            CooperativePlacementMode::Pipeline,
            vec![
                cooperative_slice(
                    "preprocess",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    4096,
                    4096,
                ),
                cooperative_slice(
                    "accelerated-stage",
                    1,
                    &["preprocess"],
                    PlacementDomain::Accelerator,
                    PlacementDomain::Storage,
                    4096,
                    4096,
                ),
            ],
        );
        let capabilities = vec![
            cpu("cpu-0", 100),
            accelerator("gpu-0", Some(gpu_path.clone())),
            host_memory("ram-hot", 16 * 1024 * 1024),
            storage("ssd-cold"),
        ];
        let plan =
            CooperativePlacementPlan::build(&task, &capabilities, &[gpu_path], 1_000).unwrap();
        assert_eq!(plan.decision, "selected");
        assert!(!plan.buffers[0].spill_to_storage);
        assert!(plan.buffers[0].spill_reservation.is_none());
        assert_eq!(plan.reservation.storage_bytes.get("ssd-cold"), Some(&8_192));

        let mut malformed = plan.buffers[0].clone();
        malformed.spill_to_storage = true;
        assert!(malformed.validate().is_err());
    }

    #[test]
    fn cooperative_buffer_byte_budget_covers_in_flight_count() {
        let mut task = cooperative_task(
            CooperativePlacementMode::ParallelSlices,
            vec![
                cooperative_slice(
                    "first",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    1,
                    1,
                ),
                cooperative_slice(
                    "second",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    1,
                    1,
                ),
            ],
        );
        assert!(task.validate().is_ok());
        task.buffer_policy.capacity_bytes = 1;
        assert_eq!(task.buffer_policy.max_in_flight_buffers, 2);
        assert!(task.validate().is_err());
    }

    #[test]
    fn cooperative_tiny_edges_raise_capacity_to_the_in_flight_floor() {
        let mut task = cooperative_task(
            CooperativePlacementMode::ParallelSlices,
            vec![
                cooperative_slice(
                    "first",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    0,
                    1,
                ),
                cooperative_slice(
                    "second",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    0,
                    1,
                ),
            ],
        );
        task.buffer_policy.capacity_bytes = 2;
        task.buffer_policy.high_watermark_bytes = 2;
        task.buffer_policy.low_watermark_bytes = 1;
        assert!(task.validate().is_ok());

        let capabilities = vec![cpu("cpu-0", 100), host_memory("ram-hot", 16 * 1024)];
        let plan = CooperativePlacementPlan::build(&task, &capabilities, &[], 1_000).unwrap();
        assert_eq!(plan.decision, "selected");
        assert!(plan.buffers.iter().all(|buffer| {
            buffer.capacity_bytes == 2
                && buffer.max_in_flight_buffers == 2
                && buffer.validate().is_ok()
        }));
        assert_eq!(plan.reservation.buffer_bytes.get("ram-hot"), Some(&4));
    }

    #[test]
    fn cooperative_reservation_rejects_partial_capacity_and_preserves_usage() {
        let task = cooperative_task(
            CooperativePlacementMode::ParallelSlices,
            vec![
                cooperative_slice(
                    "first",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    1024,
                    1024,
                ),
                cooperative_slice(
                    "second",
                    0,
                    &[],
                    PlacementDomain::Cpu,
                    PlacementDomain::HostMemory,
                    1024,
                    1024,
                ),
            ],
        );
        let planning_capabilities =
            vec![cpu("cpu-0", 100), host_memory("ram-hot", 16 * 1024 * 1024)];
        let plan =
            CooperativePlacementPlan::build(&task, &planning_capabilities, &[], 1_000).unwrap();
        assert_eq!(plan.decision, "selected");

        let mut constrained_cpu = cpu("cpu-0", 100);
        constrained_cpu.queue_capacity = Some(1);
        let mut ledger = CooperativeReservationLedger::from_inventory(
            &[constrained_cpu, host_memory("ram-hot", 16 * 1024 * 1024)],
            &[],
        )
        .unwrap();
        let baseline = ledger.used().clone();
        assert!(ledger.reserve(&plan).is_err());
        assert_eq!(ledger.active_leases(), 0);
        assert_eq!(ledger.used(), &baseline);
    }

    #[test]
    fn cooperative_single_executor_does_not_infer_work_splitting() {
        let task = cooperative_task(
            CooperativePlacementMode::SingleExecutor,
            vec![cooperative_slice(
                "whole-task",
                0,
                &[],
                PlacementDomain::Cpu,
                PlacementDomain::HostMemory,
                1024,
                1024,
            )],
        );
        let plan = CooperativePlacementPlan::build(
            &task,
            &[
                cpu("cpu-0", 100),
                accelerator("gpu-0", None),
                host_memory("ram-hot", 16 * 1024 * 1024),
            ],
            &[],
            1_000,
        )
        .unwrap();
        assert_eq!(plan.decision, "selected");
        assert_eq!(plan.slices[0].executor_id.as_deref(), Some("cpu-0"));
        assert!(plan.buffers.is_empty());
    }

    #[test]
    fn ewma_is_bounded_and_explicitly_initialized() {
        let mut ewma = Ewma::new(250).unwrap();
        assert_eq!(ewma.observe(100), 100);
        assert_eq!(ewma.observe(200), 125);
        assert!(Ewma::new(0).is_err());
        assert!(Ewma::new(1_001).is_err());
    }
}
