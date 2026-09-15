//! Hardware-aware resource contracts owned by the Rust runtime.
//!
//! This module deliberately contains policy-neutral contracts and a deterministic
//! single-process admission controller. OS-specific enforcement belongs behind the
//! [`ResourceController`] trait; unsupported enforcement is reported explicitly.

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::{SystemTime, UNIX_EPOCH};

pub const RESOURCE_CONTRACT_SCHEMA_V1: &str = "aegis-resource-contract-v1";
pub const LEASE_TOKEN_SCHEMA_V1: &str = "aegis-resource-lease-token-v1";
pub const UNKNOWN_HOST_MEMORY_CAPACITY_BYTES: u64 = 256 * 1024 * 1024;

pub type TaskId = u128;
pub type LeaseId = u128;
pub type MemoryDomainId = String;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum CpuArchitecture {
    X86,
    X86_64,
    Arm,
    Aarch64,
    Wasm,
    Unknown,
}

impl CpuArchitecture {
    pub const fn current() -> Self {
        #[cfg(target_arch = "x86")]
        {
            return Self::X86;
        }
        #[cfg(target_arch = "x86_64")]
        {
            return Self::X86_64;
        }
        #[cfg(target_arch = "arm")]
        {
            return Self::Arm;
        }
        #[cfg(target_arch = "aarch64")]
        {
            return Self::Aarch64;
        }
        #[cfg(target_arch = "wasm32")]
        {
            return Self::Wasm;
        }
        #[allow(unreachable_code)]
        Self::Unknown
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CpuFeatureSet {
    pub avx: bool,
    pub avx2: bool,
    pub avx512f: bool,
    pub neon: bool,
    pub sve: bool,
}

impl CpuFeatureSet {
    pub fn detect() -> Self {
        Self {
            #[cfg(target_arch = "x86_64")]
            avx: std::is_x86_feature_detected!("avx"),
            #[cfg(not(target_arch = "x86_64"))]
            avx: false,
            #[cfg(target_arch = "x86_64")]
            avx2: std::is_x86_feature_detected!("avx2"),
            #[cfg(not(target_arch = "x86_64"))]
            avx2: false,
            #[cfg(target_arch = "x86_64")]
            avx512f: std::is_x86_feature_detected!("avx512f"),
            #[cfg(not(target_arch = "x86_64"))]
            avx512f: false,
            #[cfg(any(target_arch = "aarch64", target_arch = "arm"))]
            neon: true,
            #[cfg(not(any(target_arch = "aarch64", target_arch = "arm")))]
            neon: false,
            // Rust does not expose a portable runtime SVE probe. Keep this
            // conservative until a platform adapter supplies one.
            sve: false,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CpuQuota {
    pub quota_micros: Option<u64>,
    pub period_micros: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NumaNode {
    pub id: u32,
    pub usable_parallelism: usize,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CpuProfile {
    pub architecture: CpuArchitecture,
    pub usable_parallelism: usize,
    pub logical_processors: Option<usize>,
    pub physical_cores: Option<usize>,
    pub quota: Option<CpuQuota>,
    pub affinity: Option<Vec<usize>>,
    pub numa_nodes: Vec<NumaNode>,
    pub features: CpuFeatureSet,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum MemoryDomainKind {
    Host,
    Unified,
    DiscreteAccelerator,
    Shared,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MemoryDomain {
    pub id: MemoryDomainId,
    pub kind: MemoryDomainKind,
    pub capacity_bytes: Option<u64>,
    pub available_bytes: Option<u64>,
    pub reserved_bytes: u64,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum AcceleratorKind {
    Cpu,
    Gpu,
    Npu,
    Other,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum BackendKind {
    Cpu,
    Cuda,
    Rocm,
    Metal,
    Vulkan,
    Other,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum DeviceHealth {
    Unknown,
    Healthy,
    Degraded,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AcceleratorProfile {
    pub id: String,
    pub kind: AcceleratorKind,
    pub backend: BackendKind,
    pub vendor: String,
    pub memory_domain: MemoryDomainId,
    pub capabilities: Vec<String>,
    pub health: DeviceHealth,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AcceleratorRequest {
    pub kind: AcceleratorKind,
    pub backend: Option<BackendKind>,
    pub required_capabilities: Vec<String>,
    pub memory: MemoryRequest,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum EnforcementLevel {
    KernelEnforced,
    BestEffort,
    MeasurementOnly,
    Unsupported,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceControlCapabilities {
    pub cpu: EnforcementLevel,
    pub memory: EnforcementLevel,
    /// The OS may use this as a reclaim-order hint; it is not a hard memory
    /// limit and must not be reported as kernel-enforced capacity.
    #[serde(default = "default_measurement_only_enforcement")]
    pub memory_priority: EnforcementLevel,
    pub process_count: EnforcementLevel,
    pub thread_count: EnforcementLevel,
    pub io: EnforcementLevel,
    pub termination: EnforcementLevel,
    pub backend: String,
}

fn default_measurement_only_enforcement() -> EnforcementLevel {
    EnforcementLevel::MeasurementOnly
}

impl ResourceControlCapabilities {
    pub fn for_current_platform() -> Self {
        #[cfg(target_os = "linux")]
        {
            return Self {
                cpu: EnforcementLevel::MeasurementOnly,
                memory: EnforcementLevel::MeasurementOnly,
                memory_priority: EnforcementLevel::MeasurementOnly,
                process_count: EnforcementLevel::MeasurementOnly,
                thread_count: EnforcementLevel::MeasurementOnly,
                io: EnforcementLevel::MeasurementOnly,
                termination: EnforcementLevel::MeasurementOnly,
                backend: "linux-cgroup-v2-controller-available".to_string(),
            };
        }
        #[cfg(target_os = "windows")]
        {
            return Self {
                cpu: EnforcementLevel::MeasurementOnly,
                memory: EnforcementLevel::MeasurementOnly,
                memory_priority: EnforcementLevel::MeasurementOnly,
                process_count: EnforcementLevel::MeasurementOnly,
                thread_count: EnforcementLevel::MeasurementOnly,
                io: EnforcementLevel::MeasurementOnly,
                termination: EnforcementLevel::MeasurementOnly,
                backend: "windows-job-object-controller-available".to_string(),
            };
        }
        #[cfg(target_os = "macos")]
        {
            return Self {
                cpu: EnforcementLevel::MeasurementOnly,
                memory: EnforcementLevel::MeasurementOnly,
                memory_priority: EnforcementLevel::MeasurementOnly,
                process_count: EnforcementLevel::MeasurementOnly,
                thread_count: EnforcementLevel::MeasurementOnly,
                io: EnforcementLevel::Unsupported,
                termination: EnforcementLevel::BestEffort,
                backend: "macos-cooperative-controller".to_string(),
            };
        }
        #[allow(unreachable_code)]
        Self {
            cpu: EnforcementLevel::MeasurementOnly,
            memory: EnforcementLevel::MeasurementOnly,
            memory_priority: EnforcementLevel::MeasurementOnly,
            process_count: EnforcementLevel::MeasurementOnly,
            thread_count: EnforcementLevel::MeasurementOnly,
            io: EnforcementLevel::Unsupported,
            termination: EnforcementLevel::BestEffort,
            backend: "portable-cooperative-controller".to_string(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct StorageProfile {
    pub id: String,
    pub kind: String,
    pub capacity_bytes: Option<u64>,
    pub available_bytes: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HardwareProfile {
    pub schema: String,
    pub cpu: CpuProfile,
    pub memory_domains: Vec<MemoryDomain>,
    pub accelerators: Vec<AcceleratorProfile>,
    pub storage: Vec<StorageProfile>,
    pub os: ResourceControlCapabilities,
    pub policy: ResourcePolicy,
    pub profile_epoch: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourcePolicy {
    pub source: String,
    pub constrained_cpu_threshold: usize,
    pub constrained_memory_threshold_bytes: u64,
    pub balanced_cpu_threshold: usize,
    pub host_memory_headroom_percent: u8,
    pub unknown_host_memory_cap_bytes: u64,
    pub constrained_io_lane_limit: u32,
    pub default_io_lane_limit: u32,
    pub constrained_python_lane_limit: u32,
    pub default_python_lane_limit: u32,
    pub constrained_untrusted_lane_limit: u32,
    pub default_untrusted_lane_limit: u32,
    pub queue_multiplier: u32,
}

/// Adaptive host-memory mode used by the admission controller.
///
/// The controller only changes AEGIS-owned future work. It does not revoke
/// leases that are already running and it cannot impose priority on unrelated
/// processes owned by the operating system.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryPressureState {
    #[default]
    Normal,
    Guarded,
    Critical,
}

impl Default for ResourcePolicy {
    fn default() -> Self {
        Self {
            source: "assumed-defaults:v1; replace with measured policy after H0-H2 benchmarks"
                .to_string(),
            constrained_cpu_threshold: 4,
            constrained_memory_threshold_bytes: 8 * 1024 * 1024 * 1024,
            balanced_cpu_threshold: 16,
            host_memory_headroom_percent: 25,
            unknown_host_memory_cap_bytes: UNKNOWN_HOST_MEMORY_CAPACITY_BYTES,
            constrained_io_lane_limit: 2,
            default_io_lane_limit: 8,
            constrained_python_lane_limit: 1,
            default_python_lane_limit: 4,
            constrained_untrusted_lane_limit: 1,
            default_untrusted_lane_limit: 4,
            queue_multiplier: 4,
        }
    }
}

impl HardwareProfile {
    pub fn probe() -> Self {
        let usable_parallelism = std::thread::available_parallelism()
            .map(|value| value.get())
            .unwrap_or(1)
            .max(1);
        let (capacity_bytes, available_bytes) = detect_host_memory();
        let policy = ResourcePolicy::default();
        Self {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            cpu: CpuProfile {
                architecture: CpuArchitecture::current(),
                usable_parallelism,
                logical_processors: Some(usable_parallelism),
                physical_cores: None,
                quota: None,
                affinity: None,
                numa_nodes: vec![NumaNode {
                    id: 0,
                    usable_parallelism,
                }],
                features: CpuFeatureSet::detect(),
            },
            memory_domains: vec![MemoryDomain {
                id: "host".to_string(),
                kind: MemoryDomainKind::Host,
                capacity_bytes,
                available_bytes,
                reserved_bytes: 0,
            }],
            accelerators: Vec::new(),
            storage: detect_storage(),
            os: ResourceControlCapabilities::for_current_platform(),
            policy,
            profile_epoch: unix_time_millis(),
        }
    }

    pub fn host_memory(&self) -> Option<&MemoryDomain> {
        self.memory_domains
            .iter()
            .find(|domain| domain.id == "host")
            .or_else(|| {
                self.memory_domains
                    .iter()
                    .find(|domain| domain.kind == MemoryDomainKind::Host)
            })
            .or_else(|| {
                self.memory_domains
                    .iter()
                    .find(|domain| domain.kind == MemoryDomainKind::Unified)
            })
    }

    pub fn operating_profile(&self) -> OperatingProfile {
        let low_memory = self
            .host_memory()
            .and_then(|domain| domain.capacity_bytes)
            .map(|bytes| bytes < self.policy.constrained_memory_threshold_bytes)
            .unwrap_or(true);
        if self.cpu.usable_parallelism <= self.policy.constrained_cpu_threshold || low_memory {
            OperatingProfile::Constrained
        } else if self.cpu.usable_parallelism <= self.policy.balanced_cpu_threshold {
            OperatingProfile::BalancedLaptop
        } else {
            OperatingProfile::Performance
        }
    }

    /// Project the probed inventory into the placement contract.  This is an
    /// inventory snapshot only: throughput, transfer bandwidth and device
    /// latency remain unknown until a backend calibration supplies them.
    pub fn placement_capabilities(&self) -> Vec<crate::placement::PlacementCapability> {
        let host_available = self.host_memory().and_then(|domain| domain.available_bytes);
        let mut capabilities = vec![crate::placement::PlacementCapability {
            id: "cpu".to_string(),
            domain: crate::placement::PlacementDomain::Cpu,
            usable_bytes: host_available,
            compute_units_per_us: None,
            bandwidth_bytes_per_s: None,
            latency_us: None,
            queue_depth: 0,
            queue_capacity: u32::try_from(self.cpu.usable_parallelism).ok(),
            pressure: false,
            local_only: true,
            confidence: crate::placement::PlacementConfidence::Unknown,
            transfer_paths: Vec::new(),
        }];

        if let Some(host) = self.host_memory() {
            capabilities.push(crate::placement::PlacementCapability {
                id: host.id.clone(),
                domain: crate::placement::PlacementDomain::HostMemory,
                usable_bytes: host.available_bytes.or(host.capacity_bytes),
                compute_units_per_us: None,
                bandwidth_bytes_per_s: None,
                latency_us: Some(0),
                queue_depth: 0,
                queue_capacity: None,
                pressure: host.available_bytes.zip(host.capacity_bytes).is_some_and(
                    |(available, capacity)| {
                        available.saturating_mul(100) < capacity.saturating_mul(10)
                    },
                ),
                local_only: true,
                // RAM residency has no transfer operation in this contract,
                // but the policy remains estimated until workload costs are
                // measured.
                confidence: crate::placement::PlacementConfidence::Estimated,
                transfer_paths: Vec::new(),
            });
        }

        capabilities.extend(self.storage.iter().map(|storage| {
            crate::placement::PlacementCapability {
                id: storage.id.clone(),
                domain: crate::placement::PlacementDomain::Storage,
                usable_bytes: storage.available_bytes.or(storage.capacity_bytes),
                compute_units_per_us: None,
                bandwidth_bytes_per_s: None,
                latency_us: None,
                queue_depth: 0,
                queue_capacity: None,
                pressure: false,
                local_only: true,
                confidence: crate::placement::PlacementConfidence::Unknown,
                transfer_paths: Vec::new(),
            }
        }));

        capabilities.extend(self.accelerators.iter().map(|accelerator| {
            let usable_bytes = self
                .memory_domains
                .iter()
                .find(|domain| domain.id == accelerator.memory_domain)
                .and_then(|domain| domain.available_bytes.or(domain.capacity_bytes));
            crate::placement::PlacementCapability {
                id: accelerator.id.clone(),
                domain: crate::placement::PlacementDomain::Accelerator,
                usable_bytes,
                compute_units_per_us: None,
                bandwidth_bytes_per_s: None,
                latency_us: None,
                queue_depth: 0,
                queue_capacity: Some(1),
                pressure: accelerator.health != DeviceHealth::Healthy,
                local_only: true,
                confidence: crate::placement::PlacementConfidence::Unknown,
                transfer_paths: Vec::new(),
            }
        }));

        capabilities
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum OperatingProfile {
    Constrained,
    BalancedLaptop,
    Performance,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum WorkKind {
    NativeTask,
    PythonCognition,
    Tool,
    Agent,
    Accelerator,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum SideEffectClass {
    ReadOnly,
    Reversible,
    ExternalSideEffect,
    Irreversible,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CpuRequest {
    pub min_threads: u32,
    pub max_threads: u32,
}

impl Default for CpuRequest {
    fn default() -> Self {
        Self {
            min_threads: 1,
            max_threads: 1,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MemoryRequest {
    pub bytes: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IoRequest {
    pub max_in_flight: u32,
}

impl Default for IoRequest {
    fn default() -> Self {
        Self { max_in_flight: 1 }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ApiBudget {
    pub requests: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TokenBudget {
    pub tokens: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Deadline {
    pub deadline_ms: Option<u64>,
}

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum Priority {
    Background,
    #[default]
    Normal,
    Foreground,
    Critical,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceRequest {
    pub schema: String,
    pub task_id: TaskId,
    #[serde(default = "default_attempt_id")]
    pub attempt_id: u64,
    pub work_kind: WorkKind,
    pub cpu: CpuRequest,
    pub host_memory: MemoryRequest,
    #[serde(default)]
    pub accelerator: Option<AcceleratorRequest>,
    /// Compatibility field retained for the v1 JSON contract. New callers
    /// should use `accelerator.memory` so capability requirements travel with
    /// the request instead of being inferred from a byte count.
    #[serde(default)]
    pub accelerator_memory: Option<MemoryRequest>,
    pub io: IoRequest,
    pub process_limit: Option<u32>,
    pub thread_limit: Option<u32>,
    pub fd_limit: Option<u32>,
    pub api_budget: Option<ApiBudget>,
    pub token_budget: Option<TokenBudget>,
    pub deadline: Deadline,
    pub priority: Priority,
    pub side_effect_class: SideEffectClass,
}

impl ResourceRequest {
    pub fn minimal(task_id: TaskId, work_kind: WorkKind) -> Self {
        Self {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            task_id,
            attempt_id: 1,
            work_kind,
            cpu: CpuRequest::default(),
            host_memory: MemoryRequest { bytes: 1 },
            accelerator: None,
            accelerator_memory: None,
            io: IoRequest::default(),
            process_limit: None,
            thread_limit: None,
            fd_limit: None,
            api_budget: None,
            token_budget: None,
            deadline: Deadline { deadline_ms: None },
            priority: Priority::Normal,
            side_effect_class: SideEffectClass::ReadOnly,
        }
    }

    pub fn validate(&self, now_ms: u64) -> Result<(), ResourceError> {
        if self.schema != RESOURCE_CONTRACT_SCHEMA_V1 {
            return Err(ResourceError::InvalidRequest(
                "unsupported resource schema".to_string(),
            ));
        }
        if self.task_id == 0 || self.attempt_id == 0 {
            return Err(ResourceError::InvalidRequest(
                "task_id and attempt_id must be non-zero".to_string(),
            ));
        }
        if self.cpu.min_threads == 0 || self.cpu.max_threads < self.cpu.min_threads {
            return Err(ResourceError::InvalidRequest(
                "invalid CPU thread bounds".to_string(),
            ));
        }
        if self.host_memory.bytes == 0 || self.io.max_in_flight == 0 {
            return Err(ResourceError::InvalidRequest(
                "memory and IO budgets must be non-zero".to_string(),
            ));
        }
        if self
            .accelerator
            .as_ref()
            .is_some_and(|request| request.memory.bytes == 0)
            || self
                .accelerator_memory
                .is_some_and(|request| request.bytes == 0)
        {
            return Err(ResourceError::InvalidRequest(
                "accelerator memory must be non-zero".to_string(),
            ));
        }
        if self
            .deadline
            .deadline_ms
            .is_some_and(|deadline| deadline <= now_ms)
        {
            return Err(ResourceError::DeadlineExceeded);
        }
        for limit in [self.process_limit, self.thread_limit, self.fd_limit] {
            if limit == Some(0) {
                return Err(ResourceError::InvalidRequest(
                    "process/thread/fd limits must be non-zero".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Attach the placement identities and transfer reservations without
    /// changing the established v1 request fields or serialized shape.
    pub fn bind_placement(
        self,
        binding: crate::placement::PlacementBinding,
    ) -> PlacementResourceRequest {
        PlacementResourceRequest {
            request: self,
            binding,
        }
    }
}

/// Bounded capacities for the placement-specific resources that are not part
/// of the legacy `GrantedResources` v1 shape.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementResourceBudget {
    pub storage_bytes: Option<u64>,
    pub storage_in_flight: Option<u32>,
    pub transfer_tokens: Option<u32>,
    pub shared_memory_bytes: Option<u64>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementReservation {
    pub storage_bytes: u64,
    pub storage_in_flight: u32,
    pub transfer_tokens: u32,
    pub shared_memory_bytes: u64,
}

impl PlacementReservation {
    fn from_binding(binding: &crate::placement::PlacementBinding) -> Self {
        Self {
            storage_bytes: binding.storage_bytes,
            storage_in_flight: binding.storage_in_flight,
            transfer_tokens: binding.transfer_tokens,
            shared_memory_bytes: binding.shared_memory_bytes,
        }
    }

    fn fits_within(self, capacity: PlacementResourceBudget, used: Self) -> bool {
        fits_optional_u64(
            used.storage_bytes,
            self.storage_bytes,
            capacity.storage_bytes,
        ) && fits_optional_u32(
            used.storage_in_flight,
            self.storage_in_flight,
            capacity.storage_in_flight,
        ) && fits_optional_u32(
            used.transfer_tokens,
            self.transfer_tokens,
            capacity.transfer_tokens,
        ) && fits_optional_u64(
            used.shared_memory_bytes,
            self.shared_memory_bytes,
            capacity.shared_memory_bytes,
        )
    }
}

/// Check an additive reservation against an optionally known capacity.
///
/// ``None`` means that the platform did not provide a bounded capacity.  A
/// non-zero reservation therefore cannot be admitted fail-open; only an empty
/// reservation fits an unknown budget.
fn fits_optional_u64(used: u64, requested: u64, capacity: Option<u64>) -> bool {
    let Some(capacity) = capacity else {
        return used == 0 && requested == 0;
    };
    used.checked_add(requested)
        .is_some_and(|total| total <= capacity)
}

fn fits_optional_u32(used: u32, requested: u32, capacity: Option<u32>) -> bool {
    let Some(capacity) = capacity else {
        return used == 0 && requested == 0;
    };
    used.checked_add(requested)
        .is_some_and(|total| total <= capacity)
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PlacementResourceRequest {
    pub request: ResourceRequest,
    pub binding: crate::placement::PlacementBinding,
}

impl PlacementResourceRequest {
    pub fn validate(&self, now_ms: u64) -> Result<(), ResourceError> {
        self.request.validate(now_ms)?;
        self.binding.validate().map_err(|error| {
            ResourceError::InvalidRequest(format!("invalid placement binding: {error:?}"))
        })
    }
}

impl PartialEq for PlacementReservationLease {
    fn eq(&self, other: &Self) -> bool {
        self.lease_id == other.lease_id
            && self.task_id == other.task_id
            && self.attempt_id == other.attempt_id
            && self.binding == other.binding
            && self.reserved == other.reserved
            && self.generation == other.generation
            && self.issued_at_ms == other.issued_at_ms
    }
}

impl Eq for PlacementReservationLease {}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlacementReservationLease {
    pub lease_id: LeaseId,
    pub task_id: TaskId,
    pub attempt_id: u64,
    pub binding: crate::placement::PlacementBinding,
    pub reserved: PlacementReservation,
    pub generation: u64,
    pub issued_at_ms: u64,
    #[serde(skip)]
    cancelled: Arc<AtomicBool>,
}

impl PlacementReservationLease {
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }
}

pub const COOPERATIVE_ADMISSION_SCHEMA_V1: &str = "aegis-cooperative-admission-v1";
const MAX_COOPERATIVE_PLAN_DIGEST_BYTES: usize = 256;

/// The resource-only admission request for a previously planned cooperative
/// execution.  It carries the complete aggregate reservation so admission is
/// one transaction rather than a sequence of per-device guesses.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeAdmissionRequest {
    pub schema: String,
    pub task_id: TaskId,
    pub attempt_id: u64,
    /// Opaque plan binding only.  Admission does not authenticate it or bind
    /// it to Goal/Lab evidence; those checks belong to a higher layer.
    pub plan_digest: String,
    pub reservation: crate::placement::CooperativeReservation,
    /// Storage-to-transfer-path metadata for spill reservations.  Ordinary
    /// transfer edges do not need an entry here.
    #[serde(default)]
    pub spill_paths: BTreeMap<String, String>,
}

impl CooperativeAdmissionRequest {
    pub fn validate(&self) -> Result<(), ResourceError> {
        if self.schema != COOPERATIVE_ADMISSION_SCHEMA_V1 {
            return Err(ResourceError::InvalidRequest(
                "unsupported cooperative admission schema".to_string(),
            ));
        }
        if self.task_id == 0 || self.attempt_id == 0 {
            return Err(ResourceError::InvalidRequest(
                "cooperative admission task and attempt must be non-zero".to_string(),
            ));
        }
        if self.plan_digest.trim().is_empty()
            || self.plan_digest.len() > MAX_COOPERATIVE_PLAN_DIGEST_BYTES
        {
            return Err(ResourceError::InvalidRequest(
                "cooperative admission plan digest is empty or too large".to_string(),
            ));
        }
        validate_cooperative_reservation(&self.reservation)?;
        for (storage_id, path_id) in &self.spill_paths {
            if storage_id.trim().is_empty() || path_id.trim().is_empty() {
                return Err(ResourceError::InvalidRequest(
                    "cooperative spill identities must be non-empty".to_string(),
                ));
            }
            if self
                .reservation
                .storage_bytes
                .get(storage_id)
                .copied()
                .unwrap_or(0)
                == 0
                || self
                    .reservation
                    .storage_in_flight
                    .get(storage_id)
                    .copied()
                    .unwrap_or(0)
                    == 0
                || self
                    .reservation
                    .transfer_tokens
                    .get(path_id)
                    .copied()
                    .unwrap_or(0)
                    == 0
            {
                return Err(ResourceError::InvalidRequest(
                    "cooperative spill metadata does not match its reservation".to_string(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CooperativeAdmissionLease {
    pub schema: String,
    pub lease_id: LeaseId,
    pub task_id: TaskId,
    pub attempt_id: u64,
    pub plan_digest: String,
    pub reservation: crate::placement::CooperativeReservation,
    pub spill_paths: BTreeMap<String, String>,
    pub generation: u64,
    pub issued_at_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CooperativeAdmissionDecision {
    Admitted(CooperativeAdmissionLease),
    AlreadyAdmitted(CooperativeAdmissionLease),
}

/// Resource-model admission for cooperative plans.  This is deliberately
/// independent from the planner-only `placement::CooperativeReservationLedger`
/// and the legacy v1 placement reservation API until runtime wiring is
/// explicitly enabled.  This ledger is the future runtime-admission authority
/// for the aggregate maps; it does not execute or authenticate a plan.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CooperativeAdmissionLedger {
    capacity: crate::placement::CooperativeReservation,
    used: crate::placement::CooperativeReservation,
    next_lease_id: LeaseId,
    next_generation: u64,
    active: BTreeMap<LeaseId, CooperativeAdmissionLease>,
    active_by_attempt: BTreeMap<(TaskId, u64), LeaseId>,
    attempt_digests: BTreeMap<(TaskId, u64), String>,
}

impl CooperativeAdmissionLedger {
    pub fn new(capacity: crate::placement::CooperativeReservation) -> Result<Self, ResourceError> {
        validate_cooperative_capacity(&capacity)?;
        Ok(Self {
            capacity,
            used: crate::placement::CooperativeReservation::default(),
            next_lease_id: 1,
            next_generation: 1,
            active: BTreeMap::new(),
            active_by_attempt: BTreeMap::new(),
            attempt_digests: BTreeMap::new(),
        })
    }

    /// Create the runtime admission ledger from the same observed inventory
    /// used by the cooperative planner.  The planner's current queue/in-flight
    /// usage is retained as already-consumed capacity; admission can therefore
    /// never treat a preview as an unbounded fresh resource pool.
    pub fn from_inventory(
        capabilities: &[crate::placement::PlacementCapability],
        paths: &[crate::placement::PlacementTransferPath],
    ) -> Result<Self, ResourceError> {
        let inventory =
            crate::placement::CooperativeReservationLedger::from_inventory(capabilities, paths)
                .map_err(|error| {
                    ResourceError::InvalidRequest(format!(
                        "cooperative inventory validation failed: {error:?}"
                    ))
                })?;
        let capacity = inventory.capacity().clone();
        let used = inventory.used().clone();
        let mut ledger = Self::new(capacity)?;
        validate_cooperative_usage_reservation(&used)?;
        if !cooperative_reservation_is_empty(&used) {
            ledger.ensure_maps_fit(&used)?;
        }
        ledger.used = used;
        Ok(ledger)
    }

    pub fn capacity(&self) -> &crate::placement::CooperativeReservation {
        &self.capacity
    }

    pub fn used(&self) -> &crate::placement::CooperativeReservation {
        &self.used
    }

    pub fn active_leases(&self) -> usize {
        self.active.len()
    }

    pub fn ensure_fits(
        &self,
        reservation: &crate::placement::CooperativeReservation,
    ) -> Result<(), ResourceError> {
        validate_cooperative_reservation(reservation)?;
        self.ensure_maps_fit(reservation)
    }

    fn ensure_maps_fit(
        &self,
        reservation: &crate::placement::CooperativeReservation,
    ) -> Result<(), ResourceError> {
        ensure_cooperative_u32_map_fits(
            "executor queue",
            &self.capacity.executor_queue_slots,
            &self.used.executor_queue_slots,
            &reservation.executor_queue_slots,
        )?;
        ensure_cooperative_u64_map_fits(
            "executor memory",
            &self.capacity.executor_memory_bytes,
            &self.used.executor_memory_bytes,
            &reservation.executor_memory_bytes,
        )?;
        ensure_cooperative_combined_u64_map_fits(
            "data tier",
            &self.capacity.data_tier_bytes,
            &self.used.data_tier_bytes,
            &self.used.buffer_bytes,
            &reservation.data_tier_bytes,
            &reservation.buffer_bytes,
        )?;
        ensure_cooperative_u64_map_fits(
            "storage bytes",
            &self.capacity.storage_bytes,
            &self.used.storage_bytes,
            &reservation.storage_bytes,
        )?;
        ensure_cooperative_u32_map_fits(
            "storage in-flight",
            &self.capacity.storage_in_flight,
            &self.used.storage_in_flight,
            &reservation.storage_in_flight,
        )?;
        ensure_cooperative_u32_map_fits(
            "transfer tokens",
            &self.capacity.transfer_tokens,
            &self.used.transfer_tokens,
            &reservation.transfer_tokens,
        )?;
        ensure_cooperative_u64_map_fits(
            "shared memory",
            &self.capacity.shared_memory_bytes,
            &self.used.shared_memory_bytes,
            &reservation.shared_memory_bytes,
        )?;
        Ok(())
    }

    pub fn admit(
        &mut self,
        request: CooperativeAdmissionRequest,
        now_ms: u64,
    ) -> Result<CooperativeAdmissionDecision, ResourceError> {
        request.validate()?;
        let attempt_key = (request.task_id, request.attempt_id);
        if let Some(previous_digest) = self.attempt_digests.get(&attempt_key) {
            if previous_digest != &request.plan_digest {
                return Err(ResourceError::InvalidRequest(
                    "cooperative attempt plan digest mismatch".to_string(),
                ));
            }
            let Some(lease_id) = self.active_by_attempt.get(&attempt_key).copied() else {
                return Err(ResourceError::ResourceUnavailable(
                    "cooperative attempt has already been released".to_string(),
                ));
            };
            let lease = self
                .active
                .get(&lease_id)
                .cloned()
                .ok_or(ResourceError::UnknownLease(lease_id))?;
            return Ok(CooperativeAdmissionDecision::AlreadyAdmitted(lease));
        }

        self.ensure_fits(&request.reservation)?;
        let used = add_cooperative_admission_reservation(&self.used, &request.reservation)?;
        let lease_id = self.next_lease_id;
        let generation = self.next_generation;
        self.next_lease_id = self.next_lease_id.checked_add(1).ok_or_else(|| {
            ResourceError::ResourceUnavailable("cooperative lease id exhausted".to_string())
        })?;
        self.next_generation = self.next_generation.checked_add(1).ok_or_else(|| {
            ResourceError::ResourceUnavailable("cooperative generation exhausted".to_string())
        })?;
        let lease = CooperativeAdmissionLease {
            schema: COOPERATIVE_ADMISSION_SCHEMA_V1.to_string(),
            lease_id,
            task_id: request.task_id,
            attempt_id: request.attempt_id,
            plan_digest: request.plan_digest.clone(),
            reservation: request.reservation.clone(),
            spill_paths: request.spill_paths.clone(),
            generation,
            issued_at_ms: now_ms,
        };
        self.used = used;
        self.attempt_digests
            .insert(attempt_key, request.plan_digest);
        self.active_by_attempt.insert(attempt_key, lease_id);
        self.active.insert(lease_id, lease.clone());
        Ok(CooperativeAdmissionDecision::Admitted(lease))
    }

    /// Release only the exact lease generation that was admitted.  A stale
    /// token cannot release a newer or otherwise different reservation.
    pub fn release(
        &mut self,
        lease: &CooperativeAdmissionLease,
    ) -> Result<CooperativeAdmissionLease, ResourceError> {
        let Some(current) = self.active.get(&lease.lease_id) else {
            return Err(ResourceError::UnknownLease(lease.lease_id));
        };
        if current != lease {
            return Err(ResourceError::LeaseFenced(lease.lease_id));
        }
        let current = self
            .active
            .remove(&lease.lease_id)
            .ok_or(ResourceError::UnknownLease(lease.lease_id))?;
        self.used = subtract_cooperative_admission_reservation(&self.used, &current.reservation)?;
        self.active_by_attempt
            .remove(&(current.task_id, current.attempt_id));
        Ok(current)
    }

    pub fn cancel(
        &mut self,
        lease: &CooperativeAdmissionLease,
    ) -> Result<CooperativeAdmissionLease, ResourceError> {
        self.release(lease)
    }
}

fn validate_cooperative_capacity(
    capacity: &crate::placement::CooperativeReservation,
) -> Result<(), ResourceError> {
    validate_cooperative_u32_map(&capacity.executor_queue_slots, "executor queue", false)?;
    validate_cooperative_u64_map(&capacity.executor_memory_bytes, "executor memory", false)?;
    validate_cooperative_u64_map(&capacity.data_tier_bytes, "data tier", false)?;
    validate_cooperative_u64_map(&capacity.buffer_bytes, "buffer", false)?;
    if !capacity.buffer_bytes.is_empty() {
        return Err(ResourceError::InvalidRequest(
            "buffer capacity is accounted through data-tier capacity, not an independent map"
                .to_string(),
        ));
    }
    validate_cooperative_u64_map(&capacity.storage_bytes, "storage bytes", false)?;
    validate_cooperative_u32_map(&capacity.storage_in_flight, "storage in-flight", false)?;
    validate_cooperative_u32_map(&capacity.transfer_tokens, "transfer tokens", false)?;
    validate_cooperative_u64_map(&capacity.shared_memory_bytes, "shared memory", false)?;
    Ok(())
}

fn validate_cooperative_reservation(
    reservation: &crate::placement::CooperativeReservation,
) -> Result<(), ResourceError> {
    validate_cooperative_accounting_reservation(reservation)?;
    if cooperative_reservation_is_empty(reservation) {
        return Err(ResourceError::InvalidRequest(
            "cooperative reservation must contain at least one resource".to_string(),
        ));
    }
    Ok(())
}

fn validate_cooperative_accounting_reservation(
    reservation: &crate::placement::CooperativeReservation,
) -> Result<(), ResourceError> {
    validate_cooperative_u32_map(&reservation.executor_queue_slots, "executor queue", true)?;
    validate_cooperative_u64_map(&reservation.executor_memory_bytes, "executor memory", true)?;
    validate_cooperative_u64_map(&reservation.data_tier_bytes, "data tier", true)?;
    validate_cooperative_u64_map(&reservation.buffer_bytes, "buffer", true)?;
    validate_cooperative_u64_map(&reservation.storage_bytes, "storage bytes", true)?;
    validate_cooperative_u32_map(&reservation.storage_in_flight, "storage in-flight", true)?;
    validate_cooperative_u32_map(&reservation.transfer_tokens, "transfer tokens", true)?;
    validate_cooperative_u64_map(&reservation.shared_memory_bytes, "shared memory", true)?;
    if reservation.storage_bytes.keys().any(|storage_id| {
        reservation
            .storage_in_flight
            .get(storage_id)
            .copied()
            .unwrap_or(0)
            == 0
    }) || reservation.storage_in_flight.keys().any(|storage_id| {
        reservation
            .storage_bytes
            .get(storage_id)
            .copied()
            .unwrap_or(0)
            == 0
    }) {
        return Err(ResourceError::InvalidRequest(
            "storage bytes and storage in-flight reservations must match".to_string(),
        ));
    }
    Ok(())
}

fn validate_cooperative_usage_reservation(
    reservation: &crate::placement::CooperativeReservation,
) -> Result<(), ResourceError> {
    validate_cooperative_u32_map(&reservation.executor_queue_slots, "executor queue", true)?;
    validate_cooperative_u64_map(&reservation.executor_memory_bytes, "executor memory", true)?;
    validate_cooperative_u64_map(&reservation.data_tier_bytes, "data tier", true)?;
    validate_cooperative_u64_map(&reservation.buffer_bytes, "buffer", true)?;
    validate_cooperative_u64_map(&reservation.storage_bytes, "storage bytes", true)?;
    validate_cooperative_u32_map(&reservation.storage_in_flight, "storage in-flight", true)?;
    validate_cooperative_u32_map(&reservation.transfer_tokens, "transfer tokens", true)?;
    validate_cooperative_u64_map(&reservation.shared_memory_bytes, "shared memory", true)?;
    Ok(())
}

fn cooperative_reservation_is_empty(
    reservation: &crate::placement::CooperativeReservation,
) -> bool {
    reservation.executor_queue_slots.is_empty()
        && reservation.executor_memory_bytes.is_empty()
        && reservation.data_tier_bytes.is_empty()
        && reservation.buffer_bytes.is_empty()
        && reservation.storage_bytes.is_empty()
        && reservation.storage_in_flight.is_empty()
        && reservation.transfer_tokens.is_empty()
        && reservation.shared_memory_bytes.is_empty()
}

fn validate_cooperative_u32_map(
    map: &BTreeMap<String, u32>,
    resource: &str,
    reject_zero: bool,
) -> Result<(), ResourceError> {
    for (id, value) in map {
        if id.trim().is_empty() || (reject_zero && *value == 0) {
            return Err(ResourceError::InvalidRequest(format!(
                "{resource} reservation has an invalid identity or amount"
            )));
        }
    }
    Ok(())
}

fn validate_cooperative_u64_map(
    map: &BTreeMap<String, u64>,
    resource: &str,
    reject_zero: bool,
) -> Result<(), ResourceError> {
    for (id, value) in map {
        if id.trim().is_empty() || (reject_zero && *value == 0) {
            return Err(ResourceError::InvalidRequest(format!(
                "{resource} reservation has an invalid identity or amount"
            )));
        }
    }
    Ok(())
}

fn ensure_cooperative_u32_map_fits(
    resource: &str,
    capacity: &BTreeMap<String, u32>,
    used: &BTreeMap<String, u32>,
    requested: &BTreeMap<String, u32>,
) -> Result<(), ResourceError> {
    for (id, amount) in requested {
        let Some(limit) = capacity.get(id) else {
            return Err(ResourceError::CapabilityDenied(format!(
                "{resource} capacity is unavailable for identity {id}"
            )));
        };
        let current = used.get(id).copied().unwrap_or(0);
        if current
            .checked_add(*amount)
            .is_none_or(|total| total > *limit)
        {
            return Err(ResourceError::ResourceExhausted);
        }
    }
    Ok(())
}

fn ensure_cooperative_u64_map_fits(
    resource: &str,
    capacity: &BTreeMap<String, u64>,
    used: &BTreeMap<String, u64>,
    requested: &BTreeMap<String, u64>,
) -> Result<(), ResourceError> {
    for (id, amount) in requested {
        let Some(limit) = capacity.get(id) else {
            return Err(ResourceError::CapabilityDenied(format!(
                "{resource} capacity is unavailable for identity {id}"
            )));
        };
        let current = used.get(id).copied().unwrap_or(0);
        if current
            .checked_add(*amount)
            .is_none_or(|total| total > *limit)
        {
            return Err(ResourceError::ResourceExhausted);
        }
    }
    Ok(())
}

fn ensure_cooperative_combined_u64_map_fits(
    resource: &str,
    capacity: &BTreeMap<String, u64>,
    used_primary: &BTreeMap<String, u64>,
    used_buffers: &BTreeMap<String, u64>,
    requested_primary: &BTreeMap<String, u64>,
    requested_buffers: &BTreeMap<String, u64>,
) -> Result<(), ResourceError> {
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
            .checked_add(used_buffers.get(id).copied().unwrap_or(0))
            .ok_or(ResourceError::ResourceExhausted)?;
        let requested = requested_primary
            .get(id)
            .copied()
            .unwrap_or(0)
            .checked_add(requested_buffers.get(id).copied().unwrap_or(0))
            .ok_or(ResourceError::ResourceExhausted)?;
        if requested == 0 {
            continue;
        }
        let Some(limit) = capacity.get(id) else {
            return Err(ResourceError::CapabilityDenied(format!(
                "{resource} capacity is unavailable for identity {id}"
            )));
        };
        if current
            .checked_add(requested)
            .is_none_or(|total| total > *limit)
        {
            return Err(ResourceError::ResourceExhausted);
        }
    }
    Ok(())
}

fn add_cooperative_admission_reservation(
    left: &crate::placement::CooperativeReservation,
    right: &crate::placement::CooperativeReservation,
) -> Result<crate::placement::CooperativeReservation, ResourceError> {
    let mut result = left.clone();
    add_cooperative_u32_map(
        &mut result.executor_queue_slots,
        &right.executor_queue_slots,
    )?;
    add_cooperative_u64_map(
        &mut result.executor_memory_bytes,
        &right.executor_memory_bytes,
    )?;
    add_cooperative_u64_map(&mut result.data_tier_bytes, &right.data_tier_bytes)?;
    add_cooperative_u64_map(&mut result.buffer_bytes, &right.buffer_bytes)?;
    add_cooperative_u64_map(&mut result.storage_bytes, &right.storage_bytes)?;
    add_cooperative_u32_map(&mut result.storage_in_flight, &right.storage_in_flight)?;
    add_cooperative_u32_map(&mut result.transfer_tokens, &right.transfer_tokens)?;
    add_cooperative_u64_map(&mut result.shared_memory_bytes, &right.shared_memory_bytes)?;
    Ok(result)
}

fn add_cooperative_u32_map(
    target: &mut BTreeMap<String, u32>,
    source: &BTreeMap<String, u32>,
) -> Result<(), ResourceError> {
    for (id, value) in source {
        let entry = target.entry(id.clone()).or_default();
        *entry = entry
            .checked_add(*value)
            .ok_or(ResourceError::ResourceExhausted)?;
    }
    Ok(())
}

fn add_cooperative_u64_map(
    target: &mut BTreeMap<String, u64>,
    source: &BTreeMap<String, u64>,
) -> Result<(), ResourceError> {
    for (id, value) in source {
        let entry = target.entry(id.clone()).or_default();
        *entry = entry
            .checked_add(*value)
            .ok_or(ResourceError::ResourceExhausted)?;
    }
    Ok(())
}

fn subtract_cooperative_admission_reservation(
    left: &crate::placement::CooperativeReservation,
    right: &crate::placement::CooperativeReservation,
) -> Result<crate::placement::CooperativeReservation, ResourceError> {
    let mut result = left.clone();
    subtract_cooperative_u32_map(
        &mut result.executor_queue_slots,
        &right.executor_queue_slots,
    )?;
    subtract_cooperative_u64_map(
        &mut result.executor_memory_bytes,
        &right.executor_memory_bytes,
    )?;
    subtract_cooperative_u64_map(&mut result.data_tier_bytes, &right.data_tier_bytes)?;
    subtract_cooperative_u64_map(&mut result.buffer_bytes, &right.buffer_bytes)?;
    subtract_cooperative_u64_map(&mut result.storage_bytes, &right.storage_bytes)?;
    subtract_cooperative_u32_map(&mut result.storage_in_flight, &right.storage_in_flight)?;
    subtract_cooperative_u32_map(&mut result.transfer_tokens, &right.transfer_tokens)?;
    subtract_cooperative_u64_map(&mut result.shared_memory_bytes, &right.shared_memory_bytes)?;
    Ok(result)
}

fn subtract_cooperative_u32_map(
    target: &mut BTreeMap<String, u32>,
    source: &BTreeMap<String, u32>,
) -> Result<(), ResourceError> {
    for (id, value) in source {
        let Some(current) = target.get_mut(id) else {
            return Err(ResourceError::ResourceUnavailable(
                "cooperative accounting underflow".to_string(),
            ));
        };
        *current = current.checked_sub(*value).ok_or_else(|| {
            ResourceError::ResourceUnavailable("cooperative accounting underflow".to_string())
        })?;
        if *current == 0 {
            target.remove(id);
        }
    }
    Ok(())
}

fn subtract_cooperative_u64_map(
    target: &mut BTreeMap<String, u64>,
    source: &BTreeMap<String, u64>,
) -> Result<(), ResourceError> {
    for (id, value) in source {
        let Some(current) = target.get_mut(id) else {
            return Err(ResourceError::ResourceUnavailable(
                "cooperative accounting underflow".to_string(),
            ));
        };
        *current = current.checked_sub(*value).ok_or_else(|| {
            ResourceError::ResourceUnavailable("cooperative accounting underflow".to_string())
        })?;
        if *current == 0 {
            target.remove(id);
        }
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct GrantedResources {
    pub cpu_threads: u32,
    pub host_memory_bytes: u64,
    pub accelerator_memory_bytes: u64,
    pub io_in_flight: u32,
    pub process_limit: u32,
    pub thread_limit: u32,
    pub fd_limit: u32,
}

impl GrantedResources {
    fn from_request(request: &ResourceRequest) -> Self {
        Self {
            cpu_threads: request.cpu.max_threads,
            host_memory_bytes: request.host_memory.bytes,
            accelerator_memory_bytes: request
                .accelerator
                .as_ref()
                .map(|value| value.memory.bytes)
                .or_else(|| request.accelerator_memory.map(|value| value.bytes))
                .unwrap_or(0),
            io_in_flight: request.io.max_in_flight,
            process_limit: request.process_limit.unwrap_or(1),
            thread_limit: request.thread_limit.unwrap_or(request.cpu.max_threads),
            fd_limit: request.fd_limit.unwrap_or(256),
        }
    }

    fn fits_within(self, capacity: Self, used: Self) -> bool {
        add_u32(used.cpu_threads, self.cpu_threads)
            .is_some_and(|value| value <= capacity.cpu_threads)
            && add_u64(used.host_memory_bytes, self.host_memory_bytes)
                .is_some_and(|value| value <= capacity.host_memory_bytes)
            && add_u64(used.accelerator_memory_bytes, self.accelerator_memory_bytes)
                .is_some_and(|value| value <= capacity.accelerator_memory_bytes)
            && add_u32(used.io_in_flight, self.io_in_flight)
                .is_some_and(|value| value <= capacity.io_in_flight)
            && add_u32(used.process_limit, self.process_limit)
                .is_some_and(|value| value <= capacity.process_limit)
            && add_u32(used.thread_limit, self.thread_limit)
                .is_some_and(|value| value <= capacity.thread_limit)
            && add_u32(used.fd_limit, self.fd_limit).is_some_and(|value| value <= capacity.fd_limit)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ResourceError {
    InvalidRequest(String),
    CapabilityDenied(String),
    ResourceUnavailable(String),
    DeadlineExceeded,
    ResourceExhausted,
    UnknownLease(LeaseId),
    InvalidLeaseToken(String),
    LeaseFenced(LeaseId),
    UnsupportedControl(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AdmissionDecision {
    Admitted(ResourceLease),
    Queued { position: usize, reason: String },
    Rejected { reason: ResourceError },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceLease {
    pub schema: String,
    pub lease_id: LeaseId,
    pub task_id: TaskId,
    pub attempt_id: u64,
    pub work_kind: WorkKind,
    #[serde(default)]
    pub priority: Priority,
    pub granted: GrantedResources,
    pub generation: u64,
    pub issued_at_ms: u64,
    pub expires_at_ms: Option<u64>,
    #[serde(skip)]
    cancelled: Arc<AtomicBool>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceLeaseToken {
    pub schema: String,
    pub lease_id: LeaseId,
    pub generation: u64,
    pub attempt_id: u64,
}

impl ResourceLeaseToken {
    pub fn from_lease(lease: &ResourceLease) -> Self {
        Self {
            schema: LEASE_TOKEN_SCHEMA_V1.to_string(),
            lease_id: lease.lease_id,
            generation: lease.generation,
            attempt_id: lease.attempt_id,
        }
    }
}

impl PartialEq for ResourceLease {
    fn eq(&self, other: &Self) -> bool {
        self.schema == other.schema
            && self.lease_id == other.lease_id
            && self.task_id == other.task_id
            && self.attempt_id == other.attempt_id
            && self.work_kind == other.work_kind
            && self.priority == other.priority
            && self.granted == other.granted
            && self.generation == other.generation
            && self.issued_at_ms == other.issued_at_ms
            && self.expires_at_ms == other.expires_at_ms
    }
}

impl Eq for ResourceLease {}

impl ResourceLease {
    pub fn is_expired(&self, now_ms: u64) -> bool {
        self.expires_at_ms
            .is_some_and(|deadline| now_ms >= deadline)
    }

    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }
}

#[derive(Clone, Debug)]
pub struct AdmissionController {
    capacity: GrantedResources,
    baseline_capacity: GrantedResources,
    used: GrantedResources,
    queue_limit: usize,
    queued_requests: VecDeque<ResourceRequest>,
    next_lease_id: LeaseId,
    generation: u64,
    active: BTreeMap<LeaseId, ResourceLease>,
    memory_pressure_state: MemoryPressureState,
    healthy_memory_samples: u8,
    adaptive_max_process_limit: u32,
    guarded_process_limit: u32,
    accelerator_capabilities: BTreeSet<String>,
    accelerator_kinds: BTreeSet<AcceleratorKind>,
    accelerator_backends: BTreeSet<BackendKind>,
    accelerator_available: bool,
    placement_capacity: PlacementResourceBudget,
    placement_used: PlacementReservation,
    placement_executor_ids: BTreeSet<String>,
    placement_data_tier_ids: BTreeSet<String>,
    placement_data_tier_domains: BTreeMap<String, crate::placement::PlacementDomain>,
    placement_path_endpoints: BTreeMap<String, (String, String)>,
    next_placement_lease_id: LeaseId,
    placement_generation: u64,
    active_placement: BTreeMap<LeaseId, PlacementReservationLease>,
}

impl AdmissionController {
    pub fn new(capacity: GrantedResources, queue_limit: usize) -> Self {
        Self {
            capacity,
            baseline_capacity: capacity,
            used: GrantedResources {
                cpu_threads: 0,
                host_memory_bytes: 0,
                accelerator_memory_bytes: 0,
                io_in_flight: 0,
                process_limit: 0,
                thread_limit: 0,
                fd_limit: 0,
            },
            queue_limit,
            queued_requests: VecDeque::new(),
            next_lease_id: 1,
            generation: 0,
            active: BTreeMap::new(),
            memory_pressure_state: MemoryPressureState::Normal,
            healthy_memory_samples: 0,
            adaptive_max_process_limit: capacity.process_limit.max(1),
            guarded_process_limit: capacity.process_limit.max(1),
            accelerator_capabilities: BTreeSet::new(),
            accelerator_kinds: BTreeSet::new(),
            accelerator_backends: BTreeSet::new(),
            accelerator_available: false,
            placement_capacity: PlacementResourceBudget::default(),
            placement_used: PlacementReservation::default(),
            placement_executor_ids: BTreeSet::new(),
            placement_data_tier_ids: BTreeSet::new(),
            placement_data_tier_domains: BTreeMap::new(),
            placement_path_endpoints: BTreeMap::new(),
            next_placement_lease_id: 1,
            placement_generation: 0,
            active_placement: BTreeMap::new(),
        }
    }

    pub fn from_hardware(profile: &HardwareProfile) -> Self {
        let cpu = profile.cpu.usable_parallelism.min(u32::MAX as usize) as u32;
        let host_memory = profile
            .host_memory()
            .and_then(|domain| domain.capacity_bytes)
            .unwrap_or(profile.policy.unknown_host_memory_cap_bytes);
        let headroom = u64::from(profile.policy.host_memory_headroom_percent.min(99));
        let host_memory = host_memory
            .saturating_mul(100_u64.saturating_sub(headroom))
            .saturating_div(100)
            .max(1);
        let concurrency = match profile.operating_profile() {
            OperatingProfile::Constrained => 2,
            OperatingProfile::BalancedLaptop => cpu.max(2),
            OperatingProfile::Performance => cpu.max(4),
        };
        let healthy_accelerators = profile
            .accelerators
            .iter()
            .filter(|accelerator| accelerator.health == DeviceHealth::Healthy)
            .collect::<Vec<_>>();
        let accelerator_memory_domains = healthy_accelerators
            .iter()
            .map(|accelerator| accelerator.memory_domain.clone())
            .collect::<BTreeSet<_>>();
        let accelerator_memory_bytes = accelerator_memory_domains
            .iter()
            .filter_map(|domain_id| {
                profile
                    .memory_domains
                    .iter()
                    .find(|domain| &domain.id == domain_id)
                    .filter(|domain| domain.kind == MemoryDomainKind::DiscreteAccelerator)
                    .and_then(|domain| domain.capacity_bytes)
            })
            .fold(0_u64, u64::saturating_add);
        let accelerator_capabilities = healthy_accelerators
            .iter()
            .flat_map(|accelerator| accelerator.capabilities.iter().cloned())
            .collect::<BTreeSet<_>>();
        let accelerator_kinds = healthy_accelerators
            .iter()
            .map(|accelerator| accelerator.kind)
            .collect::<BTreeSet<_>>();
        let accelerator_backends = healthy_accelerators
            .iter()
            .map(|accelerator| accelerator.backend)
            .collect::<BTreeSet<_>>();
        let mut controller = Self::new(
            GrantedResources {
                cpu_threads: cpu.max(1),
                host_memory_bytes: host_memory,
                accelerator_memory_bytes,
                io_in_flight: concurrency,
                process_limit: concurrency,
                thread_limit: cpu.max(1),
                fd_limit: 4096,
            },
            concurrency
                .saturating_mul(profile.policy.queue_multiplier)
                .max(1) as usize,
        );
        controller.accelerator_available = !healthy_accelerators.is_empty();
        controller.adaptive_max_process_limit = match profile.operating_profile() {
            OperatingProfile::Constrained => cpu.clamp(2, 4),
            OperatingProfile::BalancedLaptop | OperatingProfile::Performance => concurrency,
        };
        controller.guarded_process_limit = concurrency;
        controller.accelerator_capabilities = accelerator_capabilities;
        controller.accelerator_kinds = accelerator_kinds;
        controller.accelerator_backends = accelerator_backends;
        controller
    }

    pub fn queued(&self) -> usize {
        self.queued_requests.len()
    }
    pub fn active_leases(&self) -> usize {
        self.active.len()
    }
    pub fn capacity(&self) -> GrantedResources {
        self.capacity
    }
    pub fn used(&self) -> GrantedResources {
        self.used
    }

    pub fn memory_pressure_state(&self) -> MemoryPressureState {
        self.memory_pressure_state
    }

    pub fn placement_capacity(&self) -> PlacementResourceBudget {
        self.placement_capacity
    }

    pub fn placement_used(&self) -> PlacementReservation {
        self.placement_used
    }

    /// Configure the authoritative identity set for placement reservations.
    ///
    /// A path is valid only when both endpoint identities are present in the
    /// same inventory.  This turns a typo or stale device ID into a rejection
    /// instead of silently routing work through another device.
    pub fn configure_placement_inventory(
        &mut self,
        capabilities: &[crate::placement::PlacementCapability],
        paths: &[crate::placement::PlacementTransferPath],
        capacity: PlacementResourceBudget,
    ) -> Result<(), ResourceError> {
        if self.placement_used != PlacementReservation::default()
            || !self.active_placement.is_empty()
        {
            return Err(ResourceError::ResourceUnavailable(
                "cannot replace placement inventory while reservations are active".to_string(),
            ));
        }
        let executor_ids = capabilities
            .iter()
            .filter(|capability| {
                matches!(
                    capability.domain,
                    crate::placement::PlacementDomain::Cpu
                        | crate::placement::PlacementDomain::Accelerator
                )
            })
            .map(|capability| capability.id.clone())
            .collect::<BTreeSet<_>>();
        let data_tier_ids = capabilities
            .iter()
            .filter(|capability| {
                matches!(
                    capability.domain,
                    crate::placement::PlacementDomain::HostMemory
                        | crate::placement::PlacementDomain::Storage
                )
            })
            .map(|capability| capability.id.clone())
            .collect::<BTreeSet<_>>();
        let data_tier_domains = capabilities
            .iter()
            .filter(|capability| {
                matches!(
                    capability.domain,
                    crate::placement::PlacementDomain::HostMemory
                        | crate::placement::PlacementDomain::Storage
                )
            })
            .map(|capability| (capability.id.clone(), capability.domain))
            .collect::<BTreeMap<_, _>>();
        if executor_ids.iter().any(|id| id.trim().is_empty())
            || data_tier_ids.iter().any(|id| id.trim().is_empty())
        {
            return Err(ResourceError::InvalidRequest(
                "placement inventory contains an empty identity".to_string(),
            ));
        }

        let mut path_endpoints = BTreeMap::new();
        let mut all_paths = paths.to_vec();
        all_paths.extend(
            capabilities
                .iter()
                .flat_map(|capability| capability.transfer_paths.iter().cloned()),
        );
        for path in all_paths {
            path.validate().map_err(|error| {
                ResourceError::InvalidRequest(format!("invalid placement path: {error:?}"))
            })?;
            if !executor_ids.contains(&path.from_executor)
                || !data_tier_ids.contains(&path.to_data_tier)
            {
                return Err(ResourceError::CapabilityDenied(
                    "transfer path endpoint identity is not in the placement inventory".to_string(),
                ));
            }
            if let Some(previous) = path_endpoints.insert(
                path.id.clone(),
                (path.from_executor.clone(), path.to_data_tier.clone()),
            ) {
                if previous != (path.from_executor, path.to_data_tier) {
                    return Err(ResourceError::InvalidRequest(
                        "transfer path identity maps to multiple endpoint pairs".to_string(),
                    ));
                }
            }
        }
        self.placement_capacity = capacity;
        self.placement_executor_ids = executor_ids;
        self.placement_data_tier_ids = data_tier_ids;
        self.placement_data_tier_domains = data_tier_domains;
        self.placement_path_endpoints = path_endpoints;
        Ok(())
    }

    /// Reserve the placement-specific budgets after the regular v1 request
    /// has been constructed.  This is intentionally non-queuing: a caller
    /// must retain the complete binding and retry explicitly when capacity is
    /// unavailable.
    pub fn reserve_placement(
        &mut self,
        request: PlacementResourceRequest,
        now_ms: u64,
    ) -> Result<PlacementReservationLease, ResourceError> {
        request.validate(now_ms)?;
        if self.placement_executor_ids.is_empty() || self.placement_data_tier_ids.is_empty() {
            return Err(ResourceError::CapabilityDenied(
                "placement inventory is not configured".to_string(),
            ));
        }
        if !self
            .placement_executor_ids
            .contains(&request.binding.executor_id)
        {
            return Err(ResourceError::CapabilityDenied(
                "placement executor identity is unavailable".to_string(),
            ));
        }
        if !self
            .placement_data_tier_ids
            .contains(&request.binding.data_tier_id)
        {
            return Err(ResourceError::CapabilityDenied(
                "placement data-tier identity is unavailable".to_string(),
            ));
        }
        if request.binding.storage_bytes > 0
            && self
                .placement_data_tier_domains
                .get(&request.binding.data_tier_id)
                != Some(&crate::placement::PlacementDomain::Storage)
        {
            return Err(ResourceError::CapabilityDenied(
                "storage reservation is bound to a non-storage data tier".to_string(),
            ));
        }
        if let Some(path_id) = request.binding.transfer_path_id.as_deref() {
            let Some((from_executor, to_data_tier)) = self.placement_path_endpoints.get(path_id)
            else {
                return Err(ResourceError::CapabilityDenied(
                    "placement transfer-path identity is unavailable".to_string(),
                ));
            };
            if from_executor != &request.binding.executor_id
                || to_data_tier != &request.binding.data_tier_id
            {
                return Err(ResourceError::CapabilityDenied(
                    "placement transfer path is bound to the wrong endpoints".to_string(),
                ));
            }
        } else if request.binding.storage_bytes > 0
            || request.binding.transfer_tokens > 0
            || request.binding.shared_memory_bytes > 0
        {
            return Err(ResourceError::InvalidRequest(
                "non-zero transfer/storage reservations require a path identity".to_string(),
            ));
        }

        let reserved = PlacementReservation::from_binding(&request.binding);
        if !reserved.fits_within(self.placement_capacity, self.placement_used) {
            return Err(ResourceError::ResourceExhausted);
        }
        let lease_id = self.next_placement_lease_id;
        self.next_placement_lease_id = self.next_placement_lease_id.saturating_add(1);
        self.placement_generation = self.placement_generation.saturating_add(1);
        self.placement_used = add_placement_reservation(self.placement_used, reserved);
        let lease = PlacementReservationLease {
            lease_id,
            task_id: request.request.task_id,
            attempt_id: request.request.attempt_id,
            binding: request.binding,
            reserved,
            generation: self.placement_generation,
            issued_at_ms: now_ms,
            cancelled: Arc::new(AtomicBool::new(false)),
        };
        self.active_placement.insert(lease_id, lease.clone());
        Ok(lease)
    }

    pub fn release_placement(
        &mut self,
        lease_id: LeaseId,
    ) -> Result<PlacementReservationLease, ResourceError> {
        let lease = self
            .active_placement
            .remove(&lease_id)
            .ok_or(ResourceError::UnknownLease(lease_id))?;
        self.placement_used = subtract_placement_reservation(self.placement_used, lease.reserved);
        Ok(lease)
    }

    pub fn cancel_placement(
        &mut self,
        lease_id: LeaseId,
    ) -> Result<PlacementReservationLease, ResourceError> {
        let lease = self.release_placement(lease_id)?;
        lease.cancel();
        Ok(lease)
    }

    pub fn active_placement_leases(&self) -> usize {
        self.active_placement.len()
    }

    pub fn capacity_feedback(&mut self, sample: &ResourceUsageSample) -> CapacityFeedback {
        let previous = self.capacity;
        let previous_memory_state = self.memory_pressure_state;
        self.update_memory_pressure_state(sample);
        let immediate_memory_pressure = sample.memory_pressure
            || available_memory_percent(sample).is_some_and(|percent| percent < 10);
        let observed_memory_target = sample.host_memory_available_bytes.map(|available| {
            self.used
                .host_memory_bytes
                .saturating_add(available.saturating_mul(75) / 100)
                .max(self.used.host_memory_bytes)
        });
        if immediate_memory_pressure {
            let pressure_target = self
                .used
                .host_memory_bytes
                .saturating_add(previous.host_memory_bytes.saturating_mul(75) / 100)
                .max(self.used.host_memory_bytes);
            let target = observed_memory_target
                .map_or(pressure_target, |observed| pressure_target.min(observed));
            self.capacity.host_memory_bytes = self.capacity.host_memory_bytes.min(target);
        } else if previous_memory_state == MemoryPressureState::Critical
            && self.memory_pressure_state == MemoryPressureState::Critical
        {
            // Keep the reduced headroom while the recovery hysteresis is
            // collecting healthy samples. This prevents a single optimistic
            // sample from restoring load while the host is still recovering.
        } else if let Some(target) = observed_memory_target {
            self.capacity.host_memory_bytes = move_towards(
                self.capacity.host_memory_bytes,
                target.min(self.baseline_capacity.host_memory_bytes),
                self.baseline_capacity.host_memory_bytes / 4,
            );
        }
        if sample.cpu_threads_active > 0 {
            self.capacity.cpu_threads = self
                .capacity
                .cpu_threads
                .min(sample.cpu_threads_active.max(self.used.cpu_threads));
        }
        if available_memory_percent(sample).is_some() {
            let target_process_limit = match self.memory_pressure_state {
                MemoryPressureState::Normal => self.adaptive_max_process_limit,
                MemoryPressureState::Guarded => self
                    .guarded_process_limit
                    .max(self.used.process_limit)
                    .min(self.adaptive_max_process_limit),
                MemoryPressureState::Critical => self.used.process_limit.max(1),
            };
            self.capacity.process_limit = if self.capacity.process_limit < target_process_limit {
                self.capacity
                    .process_limit
                    .saturating_add(1)
                    .min(target_process_limit)
            } else {
                self.capacity
                    .process_limit
                    .saturating_sub(1)
                    .max(target_process_limit)
            };
        }
        let changed =
            self.capacity != previous || previous_memory_state != self.memory_pressure_state;
        let reason = if previous_memory_state != self.memory_pressure_state {
            format!(
                "memory pressure state changed to {:?}",
                self.memory_pressure_state
            )
        } else if immediate_memory_pressure {
            "memory pressure reduced admission headroom".to_string()
        } else if sample.host_memory_available_bytes.is_some()
            && self.capacity.host_memory_bytes != previous.host_memory_bytes
        {
            "available memory adjusted admission headroom".to_string()
        } else if sample.cpu_threads_active > 0 {
            "observed CPU activity bounded admission width".to_string()
        } else if self.capacity.process_limit != previous.process_limit {
            "available memory adjusted process admission width".to_string()
        } else {
            "no capacity change".to_string()
        };
        CapacityFeedback {
            previous,
            current: self.capacity,
            changed,
            reason,
            memory_state: self.memory_pressure_state,
        }
    }

    fn update_memory_pressure_state(&mut self, sample: &ResourceUsageSample) {
        const GUARDED_ENTER_PERCENT: u64 = 25;
        const GUARDED_EXIT_PERCENT: u64 = 35;
        const CRITICAL_EXIT_PERCENT: u64 = 15;
        const RECOVERY_SAMPLES_REQUIRED: u8 = 3;

        let available_percent = available_memory_percent(sample);
        let critical_signal =
            sample.memory_pressure || available_percent.is_some_and(|percent| percent < 10);
        let previous = self.memory_pressure_state;
        let next = match previous {
            MemoryPressureState::Normal => {
                if critical_signal {
                    MemoryPressureState::Critical
                } else if available_percent.is_some_and(|percent| percent < GUARDED_ENTER_PERCENT) {
                    MemoryPressureState::Guarded
                } else {
                    MemoryPressureState::Normal
                }
            }
            MemoryPressureState::Guarded => {
                if critical_signal {
                    MemoryPressureState::Critical
                } else if available_percent.is_some_and(|percent| percent >= GUARDED_EXIT_PERCENT) {
                    self.healthy_memory_samples = self.healthy_memory_samples.saturating_add(1);
                    if self.healthy_memory_samples >= RECOVERY_SAMPLES_REQUIRED {
                        MemoryPressureState::Normal
                    } else {
                        MemoryPressureState::Guarded
                    }
                } else {
                    self.healthy_memory_samples = 0;
                    MemoryPressureState::Guarded
                }
            }
            MemoryPressureState::Critical => {
                if critical_signal {
                    self.healthy_memory_samples = 0;
                    MemoryPressureState::Critical
                } else if available_percent.is_some_and(|percent| percent >= CRITICAL_EXIT_PERCENT)
                {
                    self.healthy_memory_samples = self.healthy_memory_samples.saturating_add(1);
                    if self.healthy_memory_samples >= RECOVERY_SAMPLES_REQUIRED {
                        MemoryPressureState::Guarded
                    } else {
                        MemoryPressureState::Critical
                    }
                } else {
                    self.healthy_memory_samples = 0;
                    MemoryPressureState::Critical
                }
            }
        };
        if next != previous {
            self.healthy_memory_samples = 0;
        }
        self.memory_pressure_state = next;
    }

    pub fn enqueue(
        &mut self,
        request: ResourceRequest,
        now_ms: u64,
    ) -> Result<usize, ResourceError> {
        request.validate(now_ms)?;
        if self.queued_requests.len() >= self.queue_limit {
            return Err(ResourceError::ResourceExhausted);
        }
        self.queued_requests.push_back(request);
        Ok(self.queued_requests.len())
    }

    pub fn admit(&mut self, request: ResourceRequest, now_ms: u64) -> AdmissionDecision {
        if let Err(error) = request.validate(now_ms) {
            return AdmissionDecision::Rejected { reason: error };
        }
        let accelerator_requested = request.work_kind == WorkKind::Accelerator
            || request.accelerator.is_some()
            || request.accelerator_memory.is_some();
        if accelerator_requested && !self.accelerator_available {
            return AdmissionDecision::Rejected {
                reason: ResourceError::CapabilityDenied(
                    "no healthy accelerator backend is advertised".to_string(),
                ),
            };
        }
        if let Some(accelerator) = &request.accelerator {
            if !self.accelerator_kinds.contains(&accelerator.kind) {
                return AdmissionDecision::Rejected {
                    reason: ResourceError::CapabilityDenied(
                        "requested accelerator kind is unavailable".to_string(),
                    ),
                };
            }
            if accelerator
                .backend
                .is_some_and(|backend| !self.accelerator_backends.contains(&backend))
            {
                return AdmissionDecision::Rejected {
                    reason: ResourceError::CapabilityDenied(
                        "requested accelerator backend is unavailable".to_string(),
                    ),
                };
            }
            if accelerator
                .required_capabilities
                .iter()
                .any(|capability| !self.accelerator_capabilities.contains(capability))
            {
                return AdmissionDecision::Rejected {
                    reason: ResourceError::CapabilityDenied(
                        "requested accelerator capability is unavailable".to_string(),
                    ),
                };
            }
        }
        if self.memory_pressure_state == MemoryPressureState::Critical
            && request.priority == Priority::Background
        {
            if self.queued_requests.len() < self.queue_limit {
                let position = self
                    .enqueue(request, now_ms)
                    .expect("validated request must be enqueueable");
                return AdmissionDecision::Queued {
                    position,
                    reason: "critical host memory pressure deferred background work".to_string(),
                };
            }
            return AdmissionDecision::Rejected {
                reason: ResourceError::ResourceExhausted,
            };
        }
        let granted = GrantedResources::from_request(&request);
        if granted.fits_within(self.capacity, self.used) {
            let lease_id = self.next_lease_id;
            self.next_lease_id = self.next_lease_id.saturating_add(1);
            self.generation = self.generation.saturating_add(1);
            self.used = add_resources(self.used, granted);
            let lease = ResourceLease {
                schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                lease_id,
                task_id: request.task_id,
                attempt_id: request.attempt_id,
                work_kind: request.work_kind,
                priority: request.priority,
                granted,
                generation: self.generation,
                issued_at_ms: now_ms,
                expires_at_ms: request.deadline.deadline_ms,
                cancelled: Arc::new(AtomicBool::new(false)),
            };
            self.active.insert(lease_id, lease.clone());
            AdmissionDecision::Admitted(lease)
        } else if self.queued_requests.len() < self.queue_limit {
            match self.enqueue(request, now_ms) {
                Ok(position) => AdmissionDecision::Queued {
                    position,
                    reason: "capacity unavailable; bounded queue admission".to_string(),
                },
                Err(reason) => AdmissionDecision::Rejected { reason },
            }
        } else {
            AdmissionDecision::Rejected {
                reason: ResourceError::ResourceExhausted,
            }
        }
    }

    pub fn release(&mut self, lease_id: LeaseId) -> Result<ResourceLease, ResourceError> {
        let lease = self
            .active
            .remove(&lease_id)
            .ok_or(ResourceError::UnknownLease(lease_id))?;
        self.used = subtract_resources(self.used, lease.granted);
        Ok(lease)
    }

    pub fn inspect_token(
        &self,
        token: &ResourceLeaseToken,
    ) -> Result<&ResourceLease, ResourceError> {
        if token.schema != LEASE_TOKEN_SCHEMA_V1 || token.lease_id == 0 || token.generation == 0 {
            return Err(ResourceError::InvalidLeaseToken(
                "invalid lease token schema or identifiers".to_string(),
            ));
        }
        let lease = self
            .active
            .get(&token.lease_id)
            .ok_or(ResourceError::UnknownLease(token.lease_id))?;
        if lease.generation != token.generation || lease.attempt_id != token.attempt_id {
            return Err(ResourceError::LeaseFenced(token.lease_id));
        }
        Ok(lease)
    }

    pub fn release_fenced(
        &mut self,
        token: &ResourceLeaseToken,
    ) -> Result<ResourceLease, ResourceError> {
        self.inspect_token(token)?;
        self.release(token.lease_id)
    }

    pub fn expired_tokens(&self, now_ms: u64) -> Vec<ResourceLeaseToken> {
        self.active
            .values()
            .filter(|lease| lease.is_expired(now_ms))
            .map(ResourceLeaseToken::from_lease)
            .collect()
    }

    /// Remove the oldest bounded queue item for a caller that is ready to
    /// retry admission after releasing capacity. The controller does not
    /// spawn or execute work implicitly.
    pub fn pop_queued(&mut self) -> Option<ResourceRequest> {
        self.queued_requests.pop_front()
    }

    /// Remove one queued attempt without touching an admitted lease.
    ///
    /// Queue cancellation is deliberately keyed by both task and attempt so
    /// a stale retry cannot cancel a newer attempt that happens to reuse the
    /// task identity.
    pub fn remove_queued(&mut self, task_id: TaskId, attempt_id: u64) -> bool {
        let before = self.queued_requests.len();
        self.queued_requests
            .retain(|request| request.task_id != task_id || request.attempt_id != attempt_id);
        self.queued_requests.len() != before
    }
}

fn move_towards(current: u64, target: u64, step: u64) -> u64 {
    let step = step.max(1);
    if current < target {
        current.saturating_add(step).min(target)
    } else {
        current.saturating_sub(step).max(target)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapacityFeedback {
    pub previous: GrantedResources,
    pub current: GrantedResources,
    pub changed: bool,
    pub reason: String,
    /// Additive observability field; older feedback JSON remains readable.
    #[serde(default)]
    pub memory_state: MemoryPressureState,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum ExecutionLane {
    Io,
    Cpu,
    PythonCognition,
    Accelerator,
    Untrusted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LaneLimit {
    pub max_in_flight: u32,
    pub active: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionLaneRegistry {
    pub lanes: BTreeMap<ExecutionLane, LaneLimit>,
}

impl ExecutionLaneRegistry {
    pub fn for_profile(profile: &HardwareProfile) -> Self {
        let cpu = profile.cpu.usable_parallelism.min(u32::MAX as usize) as u32;
        let constrained = profile.operating_profile() == OperatingProfile::Constrained;
        let mut lanes = BTreeMap::new();
        lanes.insert(
            ExecutionLane::Io,
            LaneLimit {
                max_in_flight: if constrained {
                    profile.policy.constrained_io_lane_limit
                } else {
                    profile.policy.default_io_lane_limit
                },
                active: 0,
            },
        );
        lanes.insert(
            ExecutionLane::Cpu,
            LaneLimit {
                max_in_flight: cpu.max(1),
                active: 0,
            },
        );
        lanes.insert(
            ExecutionLane::PythonCognition,
            LaneLimit {
                max_in_flight: if constrained {
                    profile.policy.constrained_python_lane_limit
                } else {
                    profile.policy.default_python_lane_limit
                },
                active: 0,
            },
        );
        lanes.insert(
            ExecutionLane::Accelerator,
            LaneLimit {
                max_in_flight: profile
                    .accelerators
                    .iter()
                    .filter(|accelerator| {
                        accelerator.health == DeviceHealth::Healthy
                            && !accelerator.id.trim().is_empty()
                    })
                    .map(|accelerator| accelerator.id.as_str())
                    .collect::<BTreeSet<_>>()
                    .len()
                    .min(u32::MAX as usize) as u32,
                active: 0,
            },
        );
        lanes.insert(
            ExecutionLane::Untrusted,
            LaneLimit {
                max_in_flight: if constrained {
                    profile.policy.constrained_untrusted_lane_limit
                } else {
                    profile.policy.default_untrusted_lane_limit
                },
                active: 0,
            },
        );
        Self { lanes }
    }

    pub fn try_acquire(&mut self, lane: ExecutionLane) -> bool {
        let Some(limit) = self.lanes.get_mut(&lane) else {
            return false;
        };
        if limit.active >= limit.max_in_flight {
            return false;
        }
        limit.active += 1;
        true
    }

    pub fn release(&mut self, lane: ExecutionLane) -> bool {
        let Some(limit) = self.lanes.get_mut(&lane) else {
            return false;
        };
        if limit.active == 0 {
            return false;
        }
        limit.active -= 1;
        true
    }

    pub fn can_release(&self, lane: ExecutionLane) -> bool {
        self.lanes.get(&lane).is_some_and(|limit| limit.active > 0)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceUsageSample {
    pub schema: String,
    pub sampled_at_ms: u64,
    pub cpu_threads_active: u32,
    pub host_memory_bytes: Option<u64>,
    pub host_memory_available_bytes: Option<u64>,
    pub queue_depth: u32,
    pub memory_pressure: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceScope {
    pub schema: String,
    pub lease_id: LeaseId,
    pub backend: String,
    pub enforcement: ResourceControlCapabilities,
}

pub trait ResourceController: Send + Sync {
    fn capabilities(&self) -> ResourceControlCapabilities;
    fn sample(&self) -> ResourceUsageSample;
    fn create_scope(&self, lease: &ResourceLease) -> Result<ResourceScope, ResourceError> {
        Ok(ResourceScope {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            lease_id: lease.lease_id,
            backend: self.capabilities().backend.clone(),
            enforcement: self.capabilities(),
        })
    }
    fn apply_to_process(&self, _lease: &ResourceLease, _pid: u32) -> Result<(), ResourceError> {
        Err(ResourceError::UnsupportedControl(
            "controller does not expose process attachment".to_string(),
        ))
    }
    fn release_scope(&self, _lease: &ResourceLease) -> Result<(), ResourceError> {
        Ok(())
    }
    fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError>;
}

#[derive(Clone, Debug, Default)]
pub struct PortableResourceController;

impl ResourceController for PortableResourceController {
    fn capabilities(&self) -> ResourceControlCapabilities {
        ResourceControlCapabilities::for_current_platform()
    }

    fn sample(&self) -> ResourceUsageSample {
        let (host_memory_bytes, host_memory_available_bytes) = detect_host_memory();
        ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms: unix_time_millis(),
            cpu_threads_active: 0,
            host_memory_bytes,
            host_memory_available_bytes,
            queue_depth: 0,
            memory_pressure: host_memory_pressure(host_memory_bytes, host_memory_available_bytes),
        }
    }

    fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError> {
        lease.cancel();
        Ok(())
    }
}

fn add_resources(left: GrantedResources, right: GrantedResources) -> GrantedResources {
    GrantedResources {
        cpu_threads: add_u32(left.cpu_threads, right.cpu_threads).unwrap_or(u32::MAX),
        host_memory_bytes: add_u64(left.host_memory_bytes, right.host_memory_bytes)
            .unwrap_or(u64::MAX),
        accelerator_memory_bytes: add_u64(
            left.accelerator_memory_bytes,
            right.accelerator_memory_bytes,
        )
        .unwrap_or(u64::MAX),
        io_in_flight: add_u32(left.io_in_flight, right.io_in_flight).unwrap_or(u32::MAX),
        process_limit: add_u32(left.process_limit, right.process_limit).unwrap_or(u32::MAX),
        thread_limit: add_u32(left.thread_limit, right.thread_limit).unwrap_or(u32::MAX),
        fd_limit: add_u32(left.fd_limit, right.fd_limit).unwrap_or(u32::MAX),
    }
}

fn subtract_resources(left: GrantedResources, right: GrantedResources) -> GrantedResources {
    GrantedResources {
        cpu_threads: left.cpu_threads.saturating_sub(right.cpu_threads),
        host_memory_bytes: left
            .host_memory_bytes
            .saturating_sub(right.host_memory_bytes),
        accelerator_memory_bytes: left
            .accelerator_memory_bytes
            .saturating_sub(right.accelerator_memory_bytes),
        io_in_flight: left.io_in_flight.saturating_sub(right.io_in_flight),
        process_limit: left.process_limit.saturating_sub(right.process_limit),
        thread_limit: left.thread_limit.saturating_sub(right.thread_limit),
        fd_limit: left.fd_limit.saturating_sub(right.fd_limit),
    }
}

fn add_placement_reservation(
    left: PlacementReservation,
    right: PlacementReservation,
) -> PlacementReservation {
    PlacementReservation {
        storage_bytes: left.storage_bytes.saturating_add(right.storage_bytes),
        storage_in_flight: left
            .storage_in_flight
            .saturating_add(right.storage_in_flight),
        transfer_tokens: left.transfer_tokens.saturating_add(right.transfer_tokens),
        shared_memory_bytes: left
            .shared_memory_bytes
            .saturating_add(right.shared_memory_bytes),
    }
}

fn subtract_placement_reservation(
    left: PlacementReservation,
    right: PlacementReservation,
) -> PlacementReservation {
    PlacementReservation {
        storage_bytes: left.storage_bytes.saturating_sub(right.storage_bytes),
        storage_in_flight: left
            .storage_in_flight
            .saturating_sub(right.storage_in_flight),
        transfer_tokens: left.transfer_tokens.saturating_sub(right.transfer_tokens),
        shared_memory_bytes: left
            .shared_memory_bytes
            .saturating_sub(right.shared_memory_bytes),
    }
}

fn add_u32(left: u32, right: u32) -> Option<u32> {
    left.checked_add(right)
}
fn add_u64(left: u64, right: u64) -> Option<u64> {
    left.checked_add(right)
}

fn unix_time_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

pub(crate) fn unix_time_millis_for_runtime() -> u64 {
    unix_time_millis()
}

fn default_attempt_id() -> u64 {
    1
}

fn detect_host_memory() -> (Option<u64>, Option<u64>) {
    #[cfg(target_os = "linux")]
    {
        let contents = match std::fs::read_to_string("/proc/meminfo") {
            Ok(contents) => contents,
            Err(_) => return (None, None),
        };
        let value_kib = |label: &str| {
            contents.lines().find_map(|line| {
                let mut parts = line.split_whitespace();
                (parts.next() == Some(label)).then(|| parts.next()?.parse::<u64>().ok())
            })?
        };
        let total = value_kib("MemTotal:").and_then(|value| value.checked_mul(1024));
        let available = value_kib("MemAvailable:").and_then(|value| value.checked_mul(1024));
        let cgroup_max = std::fs::read_to_string("/sys/fs/cgroup/memory.max")
            .ok()
            .and_then(|value| parse_cgroup_limit(&value));
        let cgroup_current = std::fs::read_to_string("/sys/fs/cgroup/memory.current")
            .ok()
            .and_then(|value| value.trim().parse::<u64>().ok());
        let capacity = match (total, cgroup_max) {
            (Some(total), Some(limit)) => Some(total.min(limit)),
            (Some(total), None) => Some(total),
            (None, Some(limit)) => Some(limit),
            (None, None) => None,
        };
        let available = match (available, cgroup_max, cgroup_current) {
            (Some(available), Some(limit), Some(current)) => {
                Some(available.min(limit.saturating_sub(current)))
            }
            (Some(available), _, _) => Some(available),
            (None, Some(limit), Some(current)) => Some(limit.saturating_sub(current)),
            _ => None,
        };
        return (capacity, available);
    }
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};

        let mut status = MEMORYSTATUSEX {
            dwLength: std::mem::size_of::<MEMORYSTATUSEX>() as u32,
            dwMemoryLoad: 0,
            ullTotalPhys: 0,
            ullAvailPhys: 0,
            ullTotalPageFile: 0,
            ullAvailPageFile: 0,
            ullTotalVirtual: 0,
            ullAvailVirtual: 0,
            ullAvailExtendedVirtual: 0,
        };
        // SAFETY: `status` is fully initialized with the documented structure
        // size and lives for the duration of this synchronous OS call.
        let success = unsafe { GlobalMemoryStatusEx(&mut status) };
        if success != 0 {
            return (Some(status.ullTotalPhys), Some(status.ullAvailPhys));
        }
        return (None, None);
    }
    // macOS and other targets remain unknown until a native adapter supplies
    // a versioned, testable probe; unknown is safer than a guessed capacity.
    #[allow(unreachable_code)]
    (None, None)
}

fn detect_storage() -> Vec<StorageProfile> {
    let path = std::env::current_dir().ok();
    let Some(path) = path else {
        return Vec::new();
    };
    let (capacity_bytes, available_bytes) = detect_filesystem_space(&path);
    vec![StorageProfile {
        id: path.to_string_lossy().into_owned(),
        kind: "filesystem".to_string(),
        capacity_bytes,
        available_bytes,
    }]
}

fn host_memory_pressure(total: Option<u64>, available: Option<u64>) -> bool {
    total.zip(available).is_some_and(|(total, available)| {
        total > 0 && available.saturating_mul(100) < total.saturating_mul(10)
    })
}

fn available_memory_percent(sample: &ResourceUsageSample) -> Option<u64> {
    sample
        .host_memory_bytes
        .zip(sample.host_memory_available_bytes)
        .filter(|(total, _)| *total > 0)
        .map(|(total, available)| available.saturating_mul(100).saturating_div(total).min(100))
}

#[cfg(target_os = "windows")]
fn detect_filesystem_space(path: &std::path::Path) -> (Option<u64>, Option<u64>) {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

    let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    wide.push(0);
    let mut available = 0_u64;
    let mut capacity = 0_u64;
    let mut total_free = 0_u64;
    // SAFETY: `wide` is NUL-terminated and all output pointers reference
    // initialized storage that lives until the synchronous OS call returns.
    let success = unsafe {
        GetDiskFreeSpaceExW(
            wide.as_ptr(),
            &mut available,
            &mut capacity,
            &mut total_free,
        )
    };
    if success != 0 {
        (Some(capacity), Some(available))
    } else {
        (None, None)
    }
}

#[cfg(all(not(miri), any(target_os = "linux", target_os = "macos")))]
fn statvfs_field_u64<T>(value: T) -> Option<u64>
where
    T: TryInto<u64>,
{
    // libc maps these fields to the platform's native unsigned width.  The
    // conversion is required on 32-bit Unix and intentionally retained on
    // 64-bit targets where it is infallible.
    value.try_into().ok()
}

#[cfg(all(not(miri), any(target_os = "linux", target_os = "macos")))]
fn detect_filesystem_space(path: &std::path::Path) -> (Option<u64>, Option<u64>) {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let Ok(path) = CString::new(path.as_os_str().as_bytes()) else {
        return (None, None);
    };
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::zeroed();
    // SAFETY: `path` is a NUL-terminated path owned by this call and `stats`
    // points to writable storage whose lifetime covers the synchronous probe.
    let status = unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) };
    if status != 0 {
        return (None, None);
    }
    // SAFETY: a successful `statvfs` call initializes the output structure.
    let stats = unsafe { stats.assume_init() };
    let block_size = statvfs_field_u64(stats.f_frsize)
        .filter(|value| *value > 0)
        .or_else(|| statvfs_field_u64(stats.f_bsize).filter(|value| *value > 0));
    let Some(block_size) = block_size else {
        return (None, None);
    };
    let capacity =
        statvfs_field_u64(stats.f_blocks).and_then(|blocks| blocks.checked_mul(block_size));
    let available =
        statvfs_field_u64(stats.f_bavail).and_then(|blocks| blocks.checked_mul(block_size));
    (capacity, available)
}

#[cfg(miri)]
fn detect_filesystem_space(_path: &std::path::Path) -> (Option<u64>, Option<u64>) {
    // Miri deliberately rejects foreign calls such as `statvfs`; preserving
    // an explicit unknown observation keeps the probe fail-closed in Miri.
    (None, None)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn detect_filesystem_space(_path: &std::path::Path) -> (Option<u64>, Option<u64>) {
    // Other targets remain unknown until a platform adapter supplies this
    // observation rather than using a shell or a guessed value.
    (None, None)
}

#[cfg(target_os = "linux")]
fn parse_cgroup_limit(value: &str) -> Option<u64> {
    let value = value.trim();
    if value == "max" {
        None
    } else {
        value.parse::<u64>().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn capacity() -> GrantedResources {
        GrantedResources {
            cpu_threads: 4,
            host_memory_bytes: 1024,
            accelerator_memory_bytes: 0,
            io_in_flight: 4,
            process_limit: 4,
            thread_limit: 4,
            fd_limit: 1024,
        }
    }

    #[test]
    fn cooperative_inventory_preserves_storage_queue_usage_without_fake_bytes() {
        let capability = crate::placement::PlacementCapability {
            id: "storage-1".to_string(),
            domain: crate::placement::PlacementDomain::Storage,
            usable_bytes: Some(1024),
            compute_units_per_us: None,
            bandwidth_bytes_per_s: None,
            latency_us: None,
            queue_depth: 1,
            queue_capacity: Some(4),
            pressure: false,
            local_only: true,
            confidence: crate::placement::PlacementConfidence::Measured,
            transfer_paths: Vec::new(),
        };
        let ledger = CooperativeAdmissionLedger::from_inventory(&[capability], &[])
            .expect("observed storage inventory should seed the ledger");
        assert_eq!(ledger.used().storage_in_flight.get("storage-1"), Some(&1));
        assert!(ledger.used().storage_bytes.is_empty());

        let mut reservation = crate::placement::CooperativeReservation::default();
        reservation
            .storage_bytes
            .insert("storage-1".to_string(), 128);
        reservation
            .storage_in_flight
            .insert("storage-1".to_string(), 1);
        ledger
            .ensure_fits(&reservation)
            .expect("storage bytes and in-flight capacity should be checked independently");
    }

    #[test]
    fn probe_has_a_safe_cpu_floor_and_explicit_schema() {
        let profile = HardwareProfile::probe();
        assert_eq!(profile.schema, RESOURCE_CONTRACT_SCHEMA_V1);
        assert!(profile.cpu.usable_parallelism >= 1);
        assert_eq!(profile.memory_domains.len(), 1);
        assert!(profile.policy.source.contains("assumed-defaults:v1"));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_probe_reports_filesystem_capacity_without_guessing() {
        let profile = HardwareProfile::probe();
        let storage = profile.storage.first().expect("current filesystem profile");
        assert_eq!(storage.kind, "filesystem");
        let capacity = storage.capacity_bytes.expect("filesystem capacity");
        let available = storage.available_bytes.expect("filesystem availability");
        assert!(capacity > 0);
        assert!(available <= capacity);
    }

    #[cfg(all(not(miri), any(target_os = "linux", target_os = "macos")))]
    #[test]
    fn unix_probe_reports_filesystem_capacity_without_guessing() {
        let profile = HardwareProfile::probe();
        let storage = profile.storage.first().expect("current filesystem profile");
        assert_eq!(storage.kind, "filesystem");
        let capacity = storage.capacity_bytes.expect("filesystem capacity");
        let available = storage.available_bytes.expect("filesystem availability");
        assert!(capacity > 0);
        assert!(available <= capacity);
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_usage_sample_keeps_total_and_available_memory_distinct() {
        let sample = PortableResourceController.sample();
        let total = sample.host_memory_bytes.expect("total physical memory");
        let available = sample
            .host_memory_available_bytes
            .expect("available physical memory");
        assert!(total > 0);
        assert!(available <= total);
    }

    #[cfg(all(miri, not(target_os = "windows")))]
    #[test]
    fn miri_filesystem_probe_stays_explicitly_unknown() {
        let profile = HardwareProfile::probe();
        assert!(
            profile
                .storage
                .iter()
                .all(|storage| storage.capacity_bytes.is_none())
        );
    }

    #[cfg(all(
        not(miri),
        not(any(target_os = "windows", target_os = "linux", target_os = "macos"))
    ))]
    #[test]
    fn unsupported_filesystem_probe_stays_explicitly_unknown() {
        let profile = HardwareProfile::probe();
        assert!(
            profile
                .storage
                .iter()
                .all(|storage| storage.capacity_bytes.is_none())
        );
    }

    #[test]
    fn unknown_host_memory_uses_a_finite_conservative_cap() {
        let mut profile = HardwareProfile::probe();
        profile.memory_domains[0].capacity_bytes = None;
        let controller = AdmissionController::from_hardware(&profile);
        assert_eq!(
            controller.capacity().host_memory_bytes,
            profile
                .policy
                .unknown_host_memory_cap_bytes
                .saturating_mul(75)
                .saturating_div(100)
        );
        assert!(controller.capacity().host_memory_bytes < u64::MAX / 4);
    }

    #[test]
    fn request_validation_rejects_invalid_boundaries() {
        let mut request = ResourceRequest::minimal(1, WorkKind::NativeTask);
        assert!(request.validate(0).is_ok());
        request.task_id = 0;
        assert!(matches!(
            request.validate(0),
            Err(ResourceError::InvalidRequest(_))
        ));
    }

    #[test]
    fn admission_is_bounded_and_release_reclaims_capacity() {
        let mut controller = AdmissionController::new(capacity(), 1);
        let mut request = ResourceRequest::minimal(1, WorkKind::NativeTask);
        request.cpu.max_threads = 4;
        request.host_memory.bytes = 1024;
        let lease = match controller.admit(request.clone(), 1) {
            AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected admission, got {other:?}"),
        };
        request.task_id = 2;
        assert!(matches!(
            controller.admit(request.clone(), 2),
            AdmissionDecision::Queued { position: 1, .. }
        ));
        request.task_id = 3;
        assert!(matches!(
            controller.admit(request, 3),
            AdmissionDecision::Rejected {
                reason: ResourceError::ResourceExhausted
            }
        ));
        assert!(controller.release(lease.lease_id).is_ok());
        assert_eq!(controller.active_leases(), 0);
        assert_eq!(controller.queued(), 1);
        assert_eq!(
            controller.pop_queued().map(|request| request.task_id),
            Some(2)
        );
        assert_eq!(controller.queued(), 0);
    }

    #[test]
    fn additive_priority_fields_preserve_older_json_shapes() {
        let mut controller = AdmissionController::new(capacity(), 0);
        let lease = match controller.admit(ResourceRequest::minimal(1, WorkKind::NativeTask), 1) {
            AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected lease, got {other:?}"),
        };
        let mut lease_json = serde_json::to_value(&lease).expect("lease JSON");
        lease_json
            .as_object_mut()
            .expect("lease object")
            .remove("priority");
        let decoded_lease: ResourceLease =
            serde_json::from_value(lease_json).expect("legacy lease JSON");
        assert_eq!(decoded_lease.priority, Priority::Normal);

        let mut capability_json =
            serde_json::to_value(ResourceControlCapabilities::for_current_platform())
                .expect("capability JSON");
        capability_json
            .as_object_mut()
            .expect("capability object")
            .remove("memory_priority");
        let decoded_capability: ResourceControlCapabilities =
            serde_json::from_value(capability_json).expect("legacy capability JSON");
        assert_eq!(
            decoded_capability.memory_priority,
            EnforcementLevel::MeasurementOnly
        );
    }

    #[test]
    fn cancellation_is_explicit_and_local() {
        let mut controller = AdmissionController::new(capacity(), 0);
        let request = ResourceRequest::minimal(9, WorkKind::PythonCognition);
        let lease = match controller.admit(request, 10) {
            AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected admission, got {other:?}"),
        };
        assert!(!lease.is_cancelled());
        lease.cancel();
        assert!(lease.is_cancelled());
    }

    #[test]
    fn lane_registry_prevents_oversubscription() {
        let profile = HardwareProfile::probe();
        let mut lanes = ExecutionLaneRegistry::for_profile(&profile);
        let limit = lanes.lanes[&ExecutionLane::Cpu].max_in_flight;
        for _ in 0..limit {
            assert!(lanes.try_acquire(ExecutionLane::Cpu));
        }
        assert!(!lanes.try_acquire(ExecutionLane::Cpu));
        assert!(lanes.release(ExecutionLane::Cpu));
    }

    #[test]
    fn accelerator_requests_fail_closed_without_capability() {
        let mut controller = AdmissionController::new(capacity(), 1);
        let mut request = ResourceRequest::minimal(1, WorkKind::Accelerator);
        request.accelerator = Some(AcceleratorRequest {
            kind: AcceleratorKind::Gpu,
            backend: Some(BackendKind::Cuda),
            required_capabilities: vec!["matrix".to_string()],
            memory: MemoryRequest { bytes: 1 },
        });
        assert!(matches!(
            controller.admit(request, 1),
            AdmissionDecision::Rejected {
                reason: ResourceError::CapabilityDenied(_)
            }
        ));
    }

    #[test]
    fn accelerator_admission_matches_healthy_kind_backend_and_capabilities() {
        let mut profile = HardwareProfile::probe();
        profile.memory_domains.push(MemoryDomain {
            id: "gpu-memory".to_string(),
            kind: MemoryDomainKind::DiscreteAccelerator,
            capacity_bytes: Some(1024),
            available_bytes: Some(1024),
            reserved_bytes: 0,
        });
        profile.accelerators.push(AcceleratorProfile {
            id: "gpu-0".to_string(),
            kind: AcceleratorKind::Gpu,
            backend: BackendKind::Cuda,
            vendor: "test".to_string(),
            memory_domain: "gpu-memory".to_string(),
            capabilities: vec!["matrix".to_string()],
            health: DeviceHealth::Healthy,
        });
        profile.accelerators.push(AcceleratorProfile {
            id: "failed-npu".to_string(),
            kind: AcceleratorKind::Npu,
            backend: BackendKind::Other,
            vendor: "test".to_string(),
            memory_domain: "missing".to_string(),
            capabilities: vec!["inference".to_string()],
            health: DeviceHealth::Failed,
        });
        let mut controller = AdmissionController::from_hardware(&profile);
        let mut request = ResourceRequest::minimal(1, WorkKind::Accelerator);
        request.accelerator = Some(AcceleratorRequest {
            kind: AcceleratorKind::Npu,
            backend: Some(BackendKind::Other),
            required_capabilities: vec!["inference".to_string()],
            memory: MemoryRequest { bytes: 1 },
        });
        assert!(matches!(
            controller.admit(request.clone(), 1),
            AdmissionDecision::Rejected {
                reason: ResourceError::CapabilityDenied(_)
            }
        ));
        request.accelerator = Some(AcceleratorRequest {
            kind: AcceleratorKind::Gpu,
            backend: Some(BackendKind::Cuda),
            required_capabilities: vec!["matrix".to_string()],
            memory: MemoryRequest { bytes: 1 },
        });
        assert!(matches!(
            controller.admit(request, 1),
            AdmissionDecision::Admitted(_)
        ));
    }

    #[test]
    fn pressure_feedback_reduces_headroom_without_revoking_used_capacity() {
        let mut controller = AdmissionController::new(capacity(), 1);
        let sample = ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms: 1,
            cpu_threads_active: 0,
            host_memory_bytes: Some(900),
            host_memory_available_bytes: Some(100),
            queue_depth: 1,
            memory_pressure: true,
        };
        let feedback = controller.capacity_feedback(&sample);
        assert!(feedback.changed);
        assert!(feedback.current.host_memory_bytes < feedback.previous.host_memory_bytes);
        assert!(feedback.current.host_memory_bytes >= controller.used().host_memory_bytes);
    }

    #[test]
    fn pressure_feedback_recovers_towards_baseline_after_healthy_samples() {
        let mut controller = AdmissionController::new(capacity(), 1);
        let pressured = ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms: 1,
            cpu_threads_active: 0,
            host_memory_bytes: Some(1_000),
            host_memory_available_bytes: Some(50),
            queue_depth: 1,
            memory_pressure: true,
        };
        let reduced = controller.capacity_feedback(&pressured).current;
        let mut recovered = reduced;
        for sampled_at_ms in 2..=4 {
            recovered = controller
                .capacity_feedback(&ResourceUsageSample {
                    schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
                    sampled_at_ms,
                    cpu_threads_active: 0,
                    host_memory_bytes: Some(1_000),
                    host_memory_available_bytes: Some(900),
                    queue_depth: 0,
                    memory_pressure: false,
                })
                .current;
        }
        assert!(recovered.host_memory_bytes > reduced.host_memory_bytes);
        assert!(recovered.host_memory_bytes < capacity().host_memory_bytes);
    }

    #[test]
    fn memory_pressure_recovery_uses_hysteresis_and_incremental_headroom() {
        let mut controller = AdmissionController::new(capacity(), 1);
        let sample = |sampled_at_ms, available, memory_pressure| ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms,
            cpu_threads_active: 0,
            host_memory_bytes: Some(1_000),
            host_memory_available_bytes: Some(available),
            queue_depth: 0,
            memory_pressure,
        };

        let reduced = controller.capacity_feedback(&sample(1, 50, true));
        assert_eq!(reduced.memory_state, MemoryPressureState::Critical);
        let held_capacity = reduced.current.host_memory_bytes;

        for sampled_at_ms in 2..=3 {
            let feedback = controller.capacity_feedback(&sample(sampled_at_ms, 900, false));
            assert_eq!(feedback.memory_state, MemoryPressureState::Critical);
            assert_eq!(feedback.current.host_memory_bytes, held_capacity);
        }

        let guarded = controller.capacity_feedback(&sample(4, 900, false));
        assert_eq!(guarded.memory_state, MemoryPressureState::Guarded);
        assert!(guarded.current.host_memory_bytes > held_capacity);
        assert!(guarded.current.host_memory_bytes < capacity().host_memory_bytes);

        for sampled_at_ms in 5..=6 {
            assert_eq!(
                controller
                    .capacity_feedback(&sample(sampled_at_ms, 900, false))
                    .memory_state,
                MemoryPressureState::Guarded
            );
        }
        assert_eq!(
            controller
                .capacity_feedback(&sample(7, 900, false))
                .memory_state,
            MemoryPressureState::Normal
        );
    }

    #[test]
    fn healthy_constrained_host_expands_process_width_only_after_observation() {
        let mut profile = HardwareProfile::probe();
        // Keep this synthetic capacity test independent of the runner's
        // advertised CPU quota (Miri and hosted sandboxes may expose one
        // logical processor even when the contract under test allows four).
        profile.cpu.usable_parallelism = 4;
        profile.cpu.logical_processors = Some(4);
        if let Some(node) = profile.cpu.numa_nodes.first_mut() {
            node.usable_parallelism = 4;
        }
        profile.memory_domains[0].capacity_bytes = Some(4 * 1024 * 1024 * 1024);
        profile.memory_domains[0].available_bytes = Some(2 * 1024 * 1024 * 1024);
        let mut controller = AdmissionController::from_hardware(&profile);
        assert_eq!(controller.capacity().process_limit, 2);

        let healthy_sample = |sampled_at_ms| ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms,
            cpu_threads_active: 0,
            host_memory_bytes: Some(4 * 1024 * 1024 * 1024),
            host_memory_available_bytes: Some(2 * 1024 * 1024 * 1024),
            queue_depth: 0,
            memory_pressure: false,
        };
        controller.capacity_feedback(&healthy_sample(1));
        assert_eq!(
            controller.memory_pressure_state(),
            MemoryPressureState::Normal
        );
        assert_eq!(controller.capacity().process_limit, 3);
        controller.capacity_feedback(&healthy_sample(2));
        assert_eq!(controller.capacity().process_limit, 4);
    }

    #[test]
    fn critical_memory_defers_background_work_without_revoking_active_leases() {
        let mut controller = AdmissionController::new(capacity(), 1);
        let active = match controller.admit(ResourceRequest::minimal(1, WorkKind::NativeTask), 1) {
            AdmissionDecision::Admitted(lease) => lease,
            other => panic!("expected active lease, got {other:?}"),
        };
        controller.capacity_feedback(&ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms: 2,
            cpu_threads_active: 0,
            host_memory_bytes: Some(1_000),
            host_memory_available_bytes: Some(900),
            queue_depth: 0,
            memory_pressure: true,
        });

        let mut background = ResourceRequest::minimal(2, WorkKind::NativeTask);
        background.priority = Priority::Background;
        assert!(matches!(
            controller.admit(background, 3),
            AdmissionDecision::Queued { .. }
        ));
        assert_eq!(controller.active_leases(), 1);
        assert_eq!(
            controller.used().host_memory_bytes,
            active.granted.host_memory_bytes
        );
        controller.release(active.lease_id).unwrap();
    }

    #[test]
    fn host_memory_pressure_uses_strict_low_water_mark() {
        assert!(host_memory_pressure(Some(1_000), Some(99)));
        assert!(!host_memory_pressure(Some(1_000), Some(100)));
        assert!(!host_memory_pressure(None, Some(1)));
    }

    #[test]
    fn accelerator_memory_uses_the_profile_memory_domain_once() {
        let mut profile = HardwareProfile::probe();
        profile.memory_domains.push(MemoryDomain {
            id: "gpu-0-memory".to_string(),
            kind: MemoryDomainKind::DiscreteAccelerator,
            capacity_bytes: Some(4096),
            available_bytes: Some(4096),
            reserved_bytes: 0,
        });
        profile.accelerators.push(AcceleratorProfile {
            id: "gpu-0".to_string(),
            kind: AcceleratorKind::Gpu,
            backend: BackendKind::Cuda,
            vendor: "test".to_string(),
            memory_domain: "gpu-0-memory".to_string(),
            capabilities: vec!["matrix".to_string()],
            health: DeviceHealth::Healthy,
        });
        profile.accelerators.push(AcceleratorProfile {
            id: "gpu-1".to_string(),
            kind: AcceleratorKind::Gpu,
            backend: BackendKind::Cuda,
            vendor: "test".to_string(),
            memory_domain: "gpu-0-memory".to_string(),
            capabilities: vec!["matrix".to_string()],
            health: DeviceHealth::Healthy,
        });
        let controller = AdmissionController::from_hardware(&profile);
        assert_eq!(controller.capacity().accelerator_memory_bytes, 4096);
        let mut request = ResourceRequest::minimal(1, WorkKind::Accelerator);
        request.accelerator = Some(AcceleratorRequest {
            kind: AcceleratorKind::Gpu,
            backend: Some(BackendKind::Cuda),
            required_capabilities: vec!["matrix".to_string()],
            memory: MemoryRequest { bytes: 1024 },
        });
        assert!(matches!(
            controller.clone().admit(request, 1),
            AdmissionDecision::Admitted(_)
        ));
    }

    #[test]
    fn unified_memory_is_not_double_counted_as_discrete_accelerator_memory() {
        let mut profile = HardwareProfile::probe();
        profile.memory_domains[0].kind = MemoryDomainKind::Unified;
        profile.memory_domains[0].capacity_bytes = Some(4096);
        profile.memory_domains[0].available_bytes = Some(4096);
        profile.accelerators.push(AcceleratorProfile {
            id: "integrated-gpu".to_string(),
            kind: AcceleratorKind::Gpu,
            backend: BackendKind::Metal,
            vendor: "test".to_string(),
            memory_domain: "host".to_string(),
            capabilities: vec!["fp16".to_string()],
            health: DeviceHealth::Healthy,
        });

        let mut controller = AdmissionController::from_hardware(&profile);
        assert_eq!(controller.capacity().accelerator_memory_bytes, 0);
        assert_eq!(
            controller.capacity().host_memory_bytes,
            4096 * (100 - u64::from(profile.policy.host_memory_headroom_percent)) / 100
        );

        let mut request = ResourceRequest::minimal(1, WorkKind::Accelerator);
        request.accelerator = Some(AcceleratorRequest {
            kind: AcceleratorKind::Gpu,
            backend: Some(BackendKind::Metal),
            required_capabilities: vec!["fp16".to_string()],
            memory: MemoryRequest { bytes: 1 },
        });
        assert!(matches!(
            controller.admit(request, 1),
            AdmissionDecision::Queued { .. }
        ));
    }

    fn placement_capability(
        id: &str,
        domain: crate::placement::PlacementDomain,
    ) -> crate::placement::PlacementCapability {
        crate::placement::PlacementCapability {
            id: id.to_string(),
            domain,
            usable_bytes: Some(4096),
            compute_units_per_us: (domain == crate::placement::PlacementDomain::Cpu).then_some(10),
            bandwidth_bytes_per_s: None,
            latency_us: Some(1),
            queue_depth: 0,
            queue_capacity: Some(4),
            pressure: false,
            local_only: true,
            confidence: crate::placement::PlacementConfidence::Measured,
            transfer_paths: Vec::new(),
        }
    }

    fn placement_request(
        task_id: u128,
        binding: crate::placement::PlacementBinding,
    ) -> PlacementResourceRequest {
        ResourceRequest::minimal(task_id, WorkKind::NativeTask).bind_placement(binding)
    }

    fn cooperative_capacity() -> crate::placement::CooperativeReservation {
        let mut capacity = crate::placement::CooperativeReservation::default();
        capacity.executor_queue_slots.insert("cpu-0".to_string(), 2);
        capacity
            .executor_memory_bytes
            .insert("cpu-0".to_string(), 1024);
        capacity.data_tier_bytes.insert("ram".to_string(), 4096);
        capacity.storage_bytes.insert("ssd-0".to_string(), 4096);
        capacity.storage_in_flight.insert("ssd-0".to_string(), 2);
        capacity.transfer_tokens.insert("cpu-to-ssd".to_string(), 2);
        capacity
            .shared_memory_bytes
            .insert("cpu-to-ssd".to_string(), 1024);
        capacity
    }

    fn cooperative_request(
        task_id: TaskId,
        attempt_id: u64,
        plan_digest: &str,
        reservation: crate::placement::CooperativeReservation,
    ) -> CooperativeAdmissionRequest {
        let mut spill_paths = BTreeMap::new();
        if reservation.storage_bytes.contains_key("ssd-0") {
            spill_paths.insert("ssd-0".to_string(), "cpu-to-ssd".to_string());
        }
        CooperativeAdmissionRequest {
            schema: COOPERATIVE_ADMISSION_SCHEMA_V1.to_string(),
            task_id,
            attempt_id,
            plan_digest: plan_digest.to_string(),
            reservation,
            spill_paths,
        }
    }

    fn cooperative_reservation() -> crate::placement::CooperativeReservation {
        let mut reservation = crate::placement::CooperativeReservation::default();
        reservation
            .executor_queue_slots
            .insert("cpu-0".to_string(), 1);
        reservation
            .executor_memory_bytes
            .insert("cpu-0".to_string(), 128);
        reservation.data_tier_bytes.insert("ram".to_string(), 512);
        reservation.buffer_bytes.insert("ram".to_string(), 256);
        reservation.storage_bytes.insert("ssd-0".to_string(), 256);
        reservation.storage_in_flight.insert("ssd-0".to_string(), 1);
        reservation
            .transfer_tokens
            .insert("cpu-to-ssd".to_string(), 1);
        reservation
            .shared_memory_bytes
            .insert("cpu-to-ssd".to_string(), 128);
        reservation
    }

    #[test]
    fn cooperative_admission_accounts_for_all_maps_and_releases_exactly() {
        let reservation = cooperative_reservation();
        let mut ledger = CooperativeAdmissionLedger::new(cooperative_capacity()).unwrap();
        let decision = ledger
            .admit(
                cooperative_request(10, 1, "digest-a", reservation.clone()),
                42,
            )
            .unwrap();
        let CooperativeAdmissionDecision::Admitted(lease) = decision else {
            panic!("first cooperative admission must create a lease");
        };
        assert_eq!(lease.reservation, reservation);
        assert_eq!(ledger.used(), &reservation);
        assert_eq!(ledger.active_leases(), 1);

        assert_eq!(ledger.release(&lease).unwrap(), lease);
        assert_eq!(
            ledger.used(),
            &crate::placement::CooperativeReservation::default()
        );
        assert_eq!(ledger.active_leases(), 0);
    }

    #[test]
    fn cooperative_admission_exhaustion_does_not_partially_consume_maps() {
        let first = cooperative_reservation();
        let mut ledger = CooperativeAdmissionLedger::new(cooperative_capacity()).unwrap();
        let first_lease = match ledger
            .admit(cooperative_request(11, 1, "digest-a", first.clone()), 42)
            .unwrap()
        {
            CooperativeAdmissionDecision::Admitted(lease) => lease,
            CooperativeAdmissionDecision::AlreadyAdmitted(_) => {
                panic!("first cooperative admission must not be a duplicate")
            }
        };
        let used_before = ledger.used().clone();

        let mut second = crate::placement::CooperativeReservation::default();
        second.executor_queue_slots.insert("cpu-0".to_string(), 2);
        second.executor_memory_bytes.insert("cpu-0".to_string(), 1);
        second.data_tier_bytes.insert("ram".to_string(), 1);
        let result = ledger.admit(cooperative_request(12, 1, "digest-b", second), 43);
        assert!(matches!(result, Err(ResourceError::ResourceExhausted)));
        assert_eq!(ledger.used(), &used_before);
        assert_eq!(ledger.active_leases(), 1);
        ledger.release(&first_lease).unwrap();
    }

    #[test]
    fn cooperative_admission_duplicate_is_idempotent_and_digest_mismatch_rejected() {
        let request = cooperative_request(13, 1, "digest-a", cooperative_reservation());
        let mut ledger = CooperativeAdmissionLedger::new(cooperative_capacity()).unwrap();
        let first = match ledger.admit(request.clone(), 42).unwrap() {
            CooperativeAdmissionDecision::Admitted(lease) => lease,
            CooperativeAdmissionDecision::AlreadyAdmitted(_) => {
                panic!("first cooperative admission must not be a duplicate")
            }
        };
        let used_before = ledger.used().clone();
        let duplicate = ledger.admit(request, 99).unwrap();
        assert_eq!(
            duplicate,
            CooperativeAdmissionDecision::AlreadyAdmitted(first.clone())
        );
        assert_eq!(ledger.used(), &used_before);

        let mismatch = cooperative_request(13, 1, "digest-b", cooperative_reservation());
        assert!(matches!(
            ledger.admit(mismatch, 100),
            Err(ResourceError::InvalidRequest(reason))
                if reason.contains("digest mismatch")
        ));
        ledger.release(&first).unwrap();
    }

    #[test]
    fn cooperative_admission_stale_generation_is_fenced() {
        let mut ledger = CooperativeAdmissionLedger::new(cooperative_capacity()).unwrap();
        let lease = match ledger
            .admit(
                cooperative_request(14, 1, "digest-a", cooperative_reservation()),
                42,
            )
            .unwrap()
        {
            CooperativeAdmissionDecision::Admitted(lease) => lease,
            CooperativeAdmissionDecision::AlreadyAdmitted(_) => {
                panic!("first cooperative admission must not be a duplicate")
            }
        };
        let mut stale = lease.clone();
        stale.generation = stale.generation.saturating_sub(1);
        assert!(matches!(
            ledger.release(&stale),
            Err(ResourceError::LeaseFenced(lease_id)) if lease_id == lease.lease_id
        ));
        assert_eq!(ledger.active_leases(), 1);
        ledger.release(&lease).unwrap();
    }

    #[test]
    fn cooperative_admission_unknown_capacity_path_and_spill_fail_closed() {
        let mut ledger = CooperativeAdmissionLedger::new(cooperative_capacity()).unwrap();
        let mut unknown_executor = crate::placement::CooperativeReservation::default();
        unknown_executor
            .executor_queue_slots
            .insert("unknown-cpu".to_string(), 1);
        assert!(matches!(
            ledger.admit(cooperative_request(15, 1, "digest-a", unknown_executor), 42),
            Err(ResourceError::CapabilityDenied(_))
        ));

        let mut unknown_path = crate::placement::CooperativeReservation::default();
        unknown_path.storage_bytes.insert("ssd-0".to_string(), 1);
        unknown_path
            .storage_in_flight
            .insert("ssd-0".to_string(), 1);
        unknown_path
            .transfer_tokens
            .insert("missing-path".to_string(), 1);
        let mut request = cooperative_request(16, 1, "digest-a", unknown_path);
        request
            .spill_paths
            .insert("ssd-0".to_string(), "missing-path".to_string());
        assert!(matches!(
            ledger.admit(request, 42),
            Err(ResourceError::CapabilityDenied(_))
        ));
        assert_eq!(
            ledger.used(),
            &crate::placement::CooperativeReservation::default()
        );
    }

    #[test]
    fn cooperative_admission_rejects_malformed_requests() {
        let mut ledger = CooperativeAdmissionLedger::new(cooperative_capacity()).unwrap();
        let mut request = cooperative_request(17, 1, "digest-a", cooperative_reservation());
        request.schema = "unknown-schema".to_string();
        assert!(matches!(
            ledger.admit(request, 42),
            Err(ResourceError::InvalidRequest(_))
        ));

        let request = cooperative_request(18, 1, "", cooperative_reservation());
        assert!(matches!(
            ledger.admit(request, 42),
            Err(ResourceError::InvalidRequest(_))
        ));

        let mut request = cooperative_request(19, 1, "digest-a", cooperative_reservation());
        request.reservation.buffer_bytes.insert(String::new(), 1);
        assert!(matches!(
            ledger.admit(request, 42),
            Err(ResourceError::InvalidRequest(_))
        ));
    }

    #[test]
    fn placement_reservation_rejects_wrong_identity_and_reclaims_on_cancel() {
        let capabilities = vec![
            placement_capability("cpu", crate::placement::PlacementDomain::Cpu),
            placement_capability("ssd-0", crate::placement::PlacementDomain::Storage),
        ];
        let paths = vec![crate::placement::PlacementTransferPath {
            schema: crate::placement::PLACEMENT_TRANSFER_PATH_SCHEMA_V1.to_string(),
            id: "cpu-to-ssd".to_string(),
            from_executor: "cpu".to_string(),
            to_data_tier: "ssd-0".to_string(),
            bandwidth_bytes_per_s: Some(1_000_000_000),
            latency_us: Some(5),
            queue_depth: 0,
            queue_capacity: Some(2),
            transfer_token_capacity: Some(1),
            transfer_tokens_in_use: 0,
            shared_memory_bytes: None,
            local_only: true,
            confidence: crate::placement::PlacementConfidence::Measured,
        }];
        let mut controller = AdmissionController::new(capacity(), 0);
        controller
            .configure_placement_inventory(
                &capabilities,
                &paths,
                PlacementResourceBudget {
                    storage_bytes: Some(2048),
                    storage_in_flight: Some(1),
                    transfer_tokens: Some(1),
                    shared_memory_bytes: Some(1),
                },
            )
            .unwrap();
        let binding = crate::placement::PlacementBinding {
            schema: crate::placement::PLACEMENT_BINDING_SCHEMA_V1.to_string(),
            executor_id: "cpu".to_string(),
            data_tier_id: "ssd-0".to_string(),
            transfer_path_id: Some("cpu-to-ssd".to_string()),
            storage_bytes: 1024,
            storage_in_flight: 1,
            transfer_tokens: 1,
            shared_memory_bytes: 0,
        };
        let lease = controller
            .reserve_placement(placement_request(1, binding.clone()), 1)
            .unwrap();
        assert_eq!(controller.placement_used().storage_bytes, 1024);
        assert_eq!(controller.placement_used().transfer_tokens, 1);
        assert!(matches!(
            controller.reserve_placement(
                placement_request(
                    2,
                    crate::placement::PlacementBinding {
                        executor_id: "wrong-cpu".to_string(),
                        ..binding.clone()
                    }
                ),
                2
            ),
            Err(ResourceError::CapabilityDenied(_))
        ));
        let cancelled = controller.cancel_placement(lease.lease_id).unwrap();
        assert!(cancelled.is_cancelled());
        assert_eq!(controller.placement_used(), PlacementReservation::default());
        assert_eq!(controller.active_placement_leases(), 0);
    }

    #[test]
    fn placement_uma_reservation_is_bounded_and_released() {
        let capabilities = vec![
            placement_capability("igpu-0", crate::placement::PlacementDomain::Accelerator),
            placement_capability("ram", crate::placement::PlacementDomain::HostMemory),
        ];
        let path = crate::placement::PlacementTransferPath {
            schema: crate::placement::PLACEMENT_TRANSFER_PATH_SCHEMA_V1.to_string(),
            id: "igpu-to-ram-uma".to_string(),
            from_executor: "igpu-0".to_string(),
            to_data_tier: "ram".to_string(),
            bandwidth_bytes_per_s: None,
            latency_us: Some(3),
            queue_depth: 0,
            queue_capacity: Some(1),
            transfer_token_capacity: Some(1),
            transfer_tokens_in_use: 0,
            shared_memory_bytes: Some(512),
            local_only: true,
            confidence: crate::placement::PlacementConfidence::Measured,
        };
        let mut controller = AdmissionController::new(capacity(), 0);
        controller
            .configure_placement_inventory(
                &capabilities,
                std::slice::from_ref(&path),
                PlacementResourceBudget {
                    storage_bytes: Some(1),
                    storage_in_flight: Some(1),
                    transfer_tokens: Some(1),
                    shared_memory_bytes: Some(512),
                },
            )
            .unwrap();
        let binding = crate::placement::PlacementBinding {
            schema: crate::placement::PLACEMENT_BINDING_SCHEMA_V1.to_string(),
            executor_id: "igpu-0".to_string(),
            data_tier_id: "ram".to_string(),
            transfer_path_id: Some(path.id),
            storage_bytes: 0,
            storage_in_flight: 0,
            transfer_tokens: 1,
            shared_memory_bytes: 512,
        };
        let lease = controller
            .reserve_placement(placement_request(3, binding), 1)
            .unwrap();
        assert_eq!(controller.placement_used().shared_memory_bytes, 512);
        assert!(matches!(
            controller.reserve_placement(
                placement_request(
                    4,
                    crate::placement::PlacementBinding {
                        schema: crate::placement::PLACEMENT_BINDING_SCHEMA_V1.to_string(),
                        executor_id: "igpu-0".to_string(),
                        data_tier_id: "ram".to_string(),
                        transfer_path_id: Some("igpu-to-ram-uma".to_string()),
                        storage_bytes: 0,
                        storage_in_flight: 0,
                        transfer_tokens: 1,
                        shared_memory_bytes: 1,
                    }
                ),
                2
            ),
            Err(ResourceError::ResourceExhausted)
        ));
        controller.release_placement(lease.lease_id).unwrap();
        assert_eq!(controller.placement_used(), PlacementReservation::default());
    }
}
