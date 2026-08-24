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
    pub process_count: EnforcementLevel,
    pub thread_count: EnforcementLevel,
    pub io: EnforcementLevel,
    pub termination: EnforcementLevel,
    pub backend: String,
}

impl ResourceControlCapabilities {
    pub fn for_current_platform() -> Self {
        #[cfg(target_os = "linux")]
        {
            return Self {
                cpu: EnforcementLevel::MeasurementOnly,
                memory: EnforcementLevel::MeasurementOnly,
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
            storage: Vec::new(),
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

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub enum Priority {
    Background,
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
    used: GrantedResources,
    queue_limit: usize,
    queued_requests: VecDeque<ResourceRequest>,
    next_lease_id: LeaseId,
    generation: u64,
    active: BTreeMap<LeaseId, ResourceLease>,
    accelerator_capabilities: BTreeSet<String>,
    accelerator_kinds: BTreeSet<AcceleratorKind>,
    accelerator_backends: BTreeSet<BackendKind>,
    accelerator_available: bool,
}

impl AdmissionController {
    pub fn new(capacity: GrantedResources, queue_limit: usize) -> Self {
        Self {
            capacity,
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
            accelerator_capabilities: BTreeSet::new(),
            accelerator_kinds: BTreeSet::new(),
            accelerator_backends: BTreeSet::new(),
            accelerator_available: false,
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

    pub fn capacity_feedback(&mut self, sample: &ResourceUsageSample) -> CapacityFeedback {
        let previous = self.capacity;
        if sample.memory_pressure {
            let safe_capacity = self
                .used
                .host_memory_bytes
                .saturating_add(previous.host_memory_bytes.saturating_mul(75) / 100)
                .max(self.used.host_memory_bytes);
            self.capacity.host_memory_bytes = self.capacity.host_memory_bytes.min(safe_capacity);
        }
        if sample.cpu_threads_active > 0 {
            self.capacity.cpu_threads = self
                .capacity
                .cpu_threads
                .min(sample.cpu_threads_active.max(self.used.cpu_threads));
        }
        let changed = self.capacity != previous;
        CapacityFeedback {
            previous,
            current: self.capacity,
            changed,
            reason: if sample.memory_pressure {
                "memory pressure reduced admission headroom".to_string()
            } else if sample.cpu_threads_active > 0 {
                "observed CPU activity bounded admission width".to_string()
            } else {
                "no capacity change".to_string()
            },
        }
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
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CapacityFeedback {
    pub previous: GrantedResources,
    pub current: GrantedResources,
    pub changed: bool,
    pub reason: String,
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
                max_in_flight: profile.accelerators.len().min(u32::MAX as usize) as u32,
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
    fn terminate(&self, lease: &ResourceLease) -> Result<(), ResourceError>;
}

#[derive(Clone, Debug, Default)]
pub struct PortableResourceController;

impl ResourceController for PortableResourceController {
    fn capabilities(&self) -> ResourceControlCapabilities {
        ResourceControlCapabilities::for_current_platform()
    }

    fn sample(&self) -> ResourceUsageSample {
        ResourceUsageSample {
            schema: RESOURCE_CONTRACT_SCHEMA_V1.to_string(),
            sampled_at_ms: unix_time_millis(),
            cpu_threads_active: 0,
            host_memory_bytes: detect_host_memory().0,
            queue_depth: 0,
            memory_pressure: false,
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
    // Windows and macOS adapters will provide exact capacity without making
    // the portable contract depend on shell commands or unsafe FFI here.
    #[allow(unreachable_code)]
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
    fn probe_has_a_safe_cpu_floor_and_explicit_schema() {
        let profile = HardwareProfile::probe();
        assert_eq!(profile.schema, RESOURCE_CONTRACT_SCHEMA_V1);
        assert!(profile.cpu.usable_parallelism >= 1);
        assert_eq!(profile.memory_domains.len(), 1);
        assert!(profile.policy.source.contains("assumed-defaults:v1"));
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
            queue_depth: 1,
            memory_pressure: true,
        };
        let feedback = controller.capacity_feedback(&sample);
        assert!(feedback.changed);
        assert!(feedback.current.host_memory_bytes < feedback.previous.host_memory_bytes);
        assert!(feedback.current.host_memory_bytes >= controller.used().host_memory_bytes);
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
}
