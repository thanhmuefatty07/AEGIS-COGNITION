use crate::guardrail::FirstOrderGuardrail;
#[cfg(test)]
use crate::guardrail::{ConstitutionalGuardrail, ExecutiveAuthority, UntrustedText};
use crate::physical::{
    ConsensusResult, PhysicalArtifact, PhysicalWitnessThreshold, PhysicalWitnessVerification,
    TrapReason,
};
use parking_lot::RwLock;
use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;
use wasmtime::{
    Caller, Config, Engine, InstancePre, Linker, Module, Store, StoreLimits, StoreLimitsBuilder,
};

#[derive(Debug, PartialEq)]
pub struct SandboxResult {
    pub artifact: PhysicalArtifact,
    pub backend: SandboxBackendKind,
    pub fuel_consumed: u64,
}

#[derive(Debug, PartialEq)]
pub struct VerificationGauntletResult {
    pub consensus: ConsensusResult,
    pub accepted_artifacts: usize,
    pub trapped_proposals: usize,
}

impl VerificationGauntletResult {
    pub fn is_valid(&self) -> bool {
        self.accepted_artifacts > 0 && self.consensus.quorum_size > 0
    }
}

impl SandboxResult {
    pub fn is_valid(&self) -> bool {
        self.fuel_consumed > 0 && self.artifact.fuel_consumed == self.fuel_consumed
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SandboxBackendKind {
    Deterministic,
    Wasmtime,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SandboxBackendConfig {
    pub backend: SandboxBackendKind,
    pub epoch_interruption_required: bool,
    pub network_isolated: bool,
    pub max_memory_pages: u32,
}

impl SandboxBackendConfig {
    pub fn deterministic() -> Self {
        Self {
            backend: SandboxBackendKind::Deterministic,
            epoch_interruption_required: false,
            network_isolated: true,
            max_memory_pages: 1,
        }
    }

    pub fn wasmtime_hardened(max_memory_pages: u32) -> Self {
        Self {
            backend: SandboxBackendKind::Wasmtime,
            epoch_interruption_required: true,
            network_isolated: true,
            max_memory_pages,
        }
    }

    pub fn is_hardened(&self) -> bool {
        self.network_isolated
            && self.max_memory_pages > 0
            && (self.backend == SandboxBackendKind::Deterministic
                || self.epoch_interruption_required)
    }
}

pub trait WasmExecutionSandbox {
    fn execute_wasm_binary(
        &self,
        payload: &[u8],
        fuel_limit: u64,
    ) -> Result<SandboxResult, TrapReason>;
    fn backend_config(&self) -> SandboxBackendConfig;
}

#[cfg(test)]
pub trait VerificationGauntlet {
    fn verify_variants(
        &self,
        proposals: &[&str],
        fuel_limit: u64,
    ) -> Result<VerificationGauntletResult, TrapReason>;
}

pub struct DeterministicSandbox {
    pub config: SandboxBackendConfig,
    pub guardrail: FirstOrderGuardrail,
}

impl Default for DeterministicSandbox {
    fn default() -> Self {
        Self::new()
    }
}

impl DeterministicSandbox {
    pub fn new() -> Self {
        Self {
            config: SandboxBackendConfig::deterministic(),
            guardrail: FirstOrderGuardrail,
        }
    }

    pub fn with_config(config: SandboxBackendConfig) -> Result<Self, TrapReason> {
        if !config.is_hardened() {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(Self {
            config,
            guardrail: FirstOrderGuardrail,
        })
    }

    #[cfg(test)]
    fn estimate_fuel(untrusted_code: &str) -> u64 {
        untrusted_code
            .split_whitespace()
            .map(|token| token.len() as u64)
            .sum::<u64>()
            .max(1)
    }

    #[cfg(test)]
    fn ast_fingerprint(untrusted_code: &str) -> u64 {
        crate::physical::compute_ast_structural_fingerprint(untrusted_code)
    }
}

#[cfg(test)]
impl DeterministicSandbox {
    pub fn mock_deterministic_test_harness(
        &self,
        untrusted_code: &str,
        fuel_limit: u64,
    ) -> Result<SandboxResult, TrapReason> {
        if fuel_limit == 0 {
            return Err(TrapReason::FuelExhausted);
        }
        self.guardrail
            .verify_invariants(untrusted_code)
            .map_err(|_| TrapReason::InvariantViolation)?;

        let fuel_consumed = Self::estimate_fuel(untrusted_code);
        if fuel_consumed > fuel_limit {
            return Err(TrapReason::FuelExhausted);
        }

        let normalized = normalize_syntax(untrusted_code);
        let bytes_changed = normalized.len();
        let artifact = PhysicalArtifact::new(
            normalized.as_bytes(),
            Self::ast_fingerprint(untrusted_code),
            fuel_consumed,
            bytes_changed,
        )?;
        Ok(SandboxResult {
            artifact,
            backend: self.config.backend,
            fuel_consumed,
        })
    }

    pub fn backend_config(&self) -> SandboxBackendConfig {
        self.config
    }
}

#[cfg(test)]
impl VerificationGauntlet for DeterministicSandbox {
    fn verify_variants(
        &self,
        proposals: &[&str],
        fuel_limit: u64,
    ) -> Result<VerificationGauntletResult, TrapReason> {
        if proposals.is_empty() {
            return Err(TrapReason::NoConsensus);
        }

        let mut artifacts = Vec::with_capacity(proposals.len());
        let mut trapped_proposals = 0usize;
        for proposal in proposals {
            match self.mock_deterministic_test_harness(proposal, fuel_limit) {
                Ok(result) => artifacts.push(result.artifact),
                Err(_) => trapped_proposals += 1,
            }
        }

        let consensus = PhysicalWitnessThreshold {
            total_witnesses: proposals.len(),
        }
        .verify_physical_witnesses(&artifacts)?;

        Ok(VerificationGauntletResult {
            consensus,
            accepted_artifacts: artifacts.len(),
            trapped_proposals,
        })
    }
}

pub struct WasmtimeSandbox {
    pub config: SandboxBackendConfig,
    pub guardrail: FirstOrderGuardrail,
    pub timeout_ms: u64,
    engine: Engine,
    module_cache: RwLock<HashMap<[u8; 32], Module>>,
    wasm_linker: Linker<LimiterState>,
    wasm_pre_cache: RwLock<HashMap<[u8; 32], InstancePre<LimiterState>>>,
    quickjs_bridge_linker: Linker<QuickJsBridgeState>,
    quickjs_bridge_pre_cache: RwLock<HashMap<[u8; 32], InstancePre<QuickJsBridgeState>>>,
    _epoch_ticker: EpochTicker,
}

pub struct QuickJsWasmInterpreterManager {
    quickjs_wasm: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QuickJsInvocation {
    pub wasm_module: Vec<u8>,
    pub abi_packet: Vec<u8>,
    pub script_blake3: [u8; 32],
    pub wrapper_blake3: [u8; 32],
    pub invocation_blake3: [u8; 32],
    pub fuel_limit: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct QuickJsAbiHeader {
    pub version: u64,
    pub header_bytes: u64,
    pub fuel_limit: u64,
    pub script_bytes: u64,
    pub wrapper_blake3: [u8; 32],
    pub script_blake3: [u8; 32],
}

pub const QUICKJS_INVOCATION_ABI_MAGIC: &[u8; 8] = b"AEGQJS01";
pub const QUICKJS_INVOCATION_ABI_VERSION: u64 = 1;
pub const QUICKJS_INVOCATION_ABI_HEADER_BYTES: usize = 104;
pub const QUICKJS_INVOCATION_MAX_SCRIPT_BYTES: usize = 256 * 1024;
pub const QUICKJS_LINEAR_MEMORY_PAGE_BYTES: usize = 64 * 1024;
pub const QUICKJS_INVOCATION_PACKET_OFFSET: usize = 0;
pub const WASMTIME_EPOCH_TICK_MS: u64 = 10;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct QuickJsLinearMemoryPlan {
    pub packet_offset: usize,
    pub packet_bytes: usize,
    pub required_pages: u32,
    pub memory_bytes: usize,
    pub packet_blake3: [u8; 32],
}

impl Default for WasmtimeSandbox {
    fn default() -> Self {
        Self::new()
    }
}

impl WasmtimeSandbox {
    pub fn new() -> Self {
        let mut wasm_config = Config::new();
        wasm_config.consume_fuel(true);
        wasm_config.epoch_interruption(true);
        wasm_config.native_unwind_info(true);
        let engine = Engine::new(&wasm_config).expect("Failed to create Wasmtime engine");
        let epoch_ticker = EpochTicker::new(&engine);
        Self {
            config: SandboxBackendConfig::wasmtime_hardened(10),
            guardrail: FirstOrderGuardrail,
            timeout_ms: 1000,
            wasm_linker: Linker::new(&engine),
            wasm_pre_cache: RwLock::new(HashMap::new()),
            quickjs_bridge_linker: quickjs_bridge_linker(&engine)
                .expect("Failed to create QuickJS bridge linker"),
            quickjs_bridge_pre_cache: RwLock::new(HashMap::new()),
            engine,
            module_cache: RwLock::new(HashMap::new()),
            _epoch_ticker: epoch_ticker,
        }
    }

    pub fn with_config(config: SandboxBackendConfig) -> Result<Self, TrapReason> {
        if !config.is_hardened() {
            return Err(TrapReason::InvariantViolation);
        }
        let mut wasm_config = Config::new();
        wasm_config.consume_fuel(true);
        wasm_config.epoch_interruption(true);
        wasm_config.native_unwind_info(true);
        let engine = Engine::new(&wasm_config).map_err(|_| TrapReason::InvariantViolation)?;
        let epoch_ticker = EpochTicker::new(&engine);
        Ok(Self {
            config,
            guardrail: FirstOrderGuardrail,
            timeout_ms: 1000,
            wasm_linker: Linker::new(&engine),
            wasm_pre_cache: RwLock::new(HashMap::new()),
            quickjs_bridge_linker: quickjs_bridge_linker(&engine)
                .map_err(|_| TrapReason::InvariantViolation)?,
            quickjs_bridge_pre_cache: RwLock::new(HashMap::new()),
            engine,
            module_cache: RwLock::new(HashMap::new()),
            _epoch_ticker: epoch_ticker,
        })
    }

    fn cached_module(&self, payload: &[u8]) -> Result<Module, TrapReason> {
        let cache_key = crate::physical::blake3_digest(payload);
        if let Some(module) = self.module_cache.read().get(&cache_key).cloned() {
            return Ok(module);
        }

        let module =
            Module::new(&self.engine, payload).map_err(|_| TrapReason::InvariantViolation)?;
        let mut write_guard = self.module_cache.write();
        Ok(write_guard
            .entry(cache_key)
            .or_insert_with(|| module.clone())
            .clone())
    }

    fn cached_wasm_pre(&self, payload: &[u8]) -> Result<InstancePre<LimiterState>, TrapReason> {
        let cache_key = crate::physical::blake3_digest(payload);
        if let Some(instance_pre) = self.wasm_pre_cache.read().get(&cache_key).cloned() {
            return Ok(instance_pre);
        }

        let module = self.cached_module(payload)?;
        let instance_pre = self
            .wasm_linker
            .instantiate_pre(&module)
            .map_err(|_| TrapReason::InvariantViolation)?;
        let mut write_guard = self.wasm_pre_cache.write();
        Ok(write_guard
            .entry(cache_key)
            .or_insert_with(|| instance_pre.clone())
            .clone())
    }

    #[cfg(test)]
    pub(crate) fn cached_wasm_pre_count_for_tests(&self) -> usize {
        self.wasm_pre_cache.read().len()
    }

    pub fn cached_quickjs_bridge_pre_count(&self) -> usize {
        self.quickjs_bridge_pre_cache.read().len()
    }

    fn cached_quickjs_bridge_pre(
        &self,
        payload: &[u8],
    ) -> Result<InstancePre<QuickJsBridgeState>, TrapReason> {
        let cache_key = crate::physical::blake3_digest(payload);
        if let Some(instance_pre) = self
            .quickjs_bridge_pre_cache
            .read()
            .get(&cache_key)
            .cloned()
        {
            return Ok(instance_pre);
        }

        let module = self.cached_module(payload)?;
        let instance_pre = self
            .quickjs_bridge_linker
            .instantiate_pre(&module)
            .map_err(|_| TrapReason::InvariantViolation)?;
        let mut write_guard = self.quickjs_bridge_pre_cache.write();
        Ok(write_guard
            .entry(cache_key)
            .or_insert_with(|| instance_pre.clone())
            .clone())
    }

    pub fn execute_wasm_binary(
        &self,
        payload: &[u8],
        fuel_limit: u64,
    ) -> Result<SandboxResult, TrapReason> {
        if fuel_limit == 0 {
            return Err(TrapReason::FuelExhausted);
        }
        if !is_wasm_binary(payload) {
            return Err(TrapReason::InvariantViolation);
        }

        let engine = &self.engine;
        let memory_bytes = memory_limit_bytes(self.config.max_memory_pages, 65536)?;
        let limits = StoreLimitsBuilder::new().memory_size(memory_bytes).build();
        let mut store = Store::new(engine, LimiterState { limits });
        store.limiter(|state| &mut state.limits);

        store.epoch_deadline_trap();
        store.set_epoch_deadline(epoch_deadline_ticks(self.timeout_ms));
        store
            .set_fuel(fuel_limit)
            .map_err(|_| TrapReason::FuelExhausted)?;

        let instance_pre = self.cached_wasm_pre(payload)?;
        let instance_res = instance_pre.instantiate(&mut store);

        let instance = match instance_res {
            Ok(inst) => inst,
            Err(_) => return Err(TrapReason::InvariantViolation),
        };

        let run_func_res = instance.get_typed_func::<(), ()>(&mut store, "_start");
        let run_func = match run_func_res {
            Ok(func) => func,
            Err(_) => return Err(TrapReason::InvariantViolation),
        };

        let before_fuel = store.get_fuel().unwrap_or(0);
        let exec_res = run_func.call(&mut store, ());
        let after_fuel = store.get_fuel().unwrap_or(0);
        let fuel_consumed = before_fuel.saturating_sub(after_fuel).max(1);

        match exec_res {
            Ok(_) => {
                let artifact = PhysicalArtifact::new(
                    payload,
                    wasm_payload_fingerprint(payload),
                    fuel_consumed,
                    payload.len(),
                )?;
                Ok(SandboxResult {
                    artifact,
                    backend: self.config.backend,
                    fuel_consumed,
                })
            }
            Err(e) => {
                let err_str = e.to_string();
                if err_str.contains("fuel") || err_str.contains("interrupt") {
                    Err(TrapReason::FuelExhausted)
                } else {
                    Err(TrapReason::InvariantViolation)
                }
            }
        }
    }

    pub fn execute_mmap_wasm_bridge_frame(
        &self,
        path: &std::path::Path,
        fuel_limit: u64,
    ) -> Result<SandboxResult, TrapReason> {
        let view = crate::bridge_mmap::open_mmap_bridge_view(path)
            .map_err(|_| TrapReason::InvariantViolation)?;
        self.execute_wasm_binary(
            view.payload().map_err(|_| TrapReason::InvariantViolation)?,
            fuel_limit,
        )
    }

    pub fn execute_quickjs_invocation_bridge(
        &self,
        invocation: &QuickJsInvocation,
        fuel_limit: u64,
    ) -> Result<SandboxResult, TrapReason> {
        if fuel_limit == 0 {
            return Err(TrapReason::FuelExhausted);
        }
        if !invocation.is_valid() || !is_wasm_binary(&invocation.wasm_module) {
            return Err(TrapReason::InvariantViolation);
        }
        let plan = invocation.linear_memory_plan_after_validation(self.config.max_memory_pages)?;

        let engine = &self.engine;
        let memory_bytes = memory_limit_bytes(
            self.config.max_memory_pages,
            QUICKJS_LINEAR_MEMORY_PAGE_BYTES,
        )?;
        let limits = StoreLimitsBuilder::new().memory_size(memory_bytes).build();
        let mut store = Store::new(
            engine,
            QuickJsBridgeState {
                limits,
                expected_plan: plan,
            },
        );
        store.limiter(|state| &mut state.limits);
        store.epoch_deadline_trap();
        store.set_epoch_deadline(epoch_deadline_ticks(self.timeout_ms));
        store
            .set_fuel(fuel_limit)
            .map_err(|_| TrapReason::FuelExhausted)?;

        let instance_pre = self.cached_quickjs_bridge_pre(&invocation.wasm_module)?;

        let instance_res = instance_pre.instantiate(&mut store);
        let instance = match instance_res {
            Ok(inst) => inst,
            Err(_) => return Err(TrapReason::InvariantViolation),
        };
        let memory = match instance.get_memory(&mut store, "memory") {
            Some(memory) => memory,
            None => return Err(TrapReason::InvariantViolation),
        };
        if memory
            .write(&mut store, plan.packet_offset, &invocation.abi_packet)
            .is_err()
        {
            return Err(TrapReason::InvariantViolation);
        }

        let run_func_res = instance.get_typed_func::<(), ()>(&mut store, "_start");
        let run_func = match run_func_res {
            Ok(func) => func,
            Err(_) => return Err(TrapReason::InvariantViolation),
        };

        let before_fuel = store.get_fuel().unwrap_or(0);
        let exec_res = run_func.call(&mut store, ());
        let after_fuel = store.get_fuel().unwrap_or(0);
        let fuel_consumed = before_fuel.saturating_sub(after_fuel).max(1);

        match exec_res {
            Ok(_) => {
                let artifact_payload = quickjs_bridge_artifact_payload(
                    invocation.wrapper_blake3,
                    invocation.invocation_blake3,
                );
                let artifact = PhysicalArtifact::new(
                    &artifact_payload,
                    wasm_payload_fingerprint(&artifact_payload),
                    fuel_consumed,
                    artifact_payload.len(),
                )?;
                Ok(SandboxResult {
                    artifact,
                    backend: self.config.backend,
                    fuel_consumed,
                })
            }
            Err(e) => {
                let err_str = e.to_string();
                if err_str.contains("fuel") || err_str.contains("interrupt") {
                    Err(TrapReason::FuelExhausted)
                } else {
                    Err(TrapReason::InvariantViolation)
                }
            }
        }
    }

    pub fn verify_payloads(
        &self,
        payloads: &[&[u8]],
        fuel_limit: u64,
    ) -> Result<VerificationGauntletResult, TrapReason> {
        if payloads.is_empty() {
            return Err(TrapReason::NoConsensus);
        }

        let mut artifacts = Vec::with_capacity(payloads.len());
        let mut trapped_proposals = 0usize;
        for payload in payloads {
            match self.execute_wasm_binary(payload, fuel_limit) {
                Ok(result) => artifacts.push(result.artifact),
                Err(_) => trapped_proposals += 1,
            }
        }

        let consensus = PhysicalWitnessThreshold {
            total_witnesses: payloads.len(),
        }
        .verify_physical_witnesses(&artifacts)?;

        Ok(VerificationGauntletResult {
            consensus,
            accepted_artifacts: artifacts.len(),
            trapped_proposals,
        })
    }
}

impl QuickJsWasmInterpreterManager {
    pub fn new(quickjs_wasm: Vec<u8>) -> Result<Self, TrapReason> {
        if !is_wasm_binary(&quickjs_wasm) {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(Self { quickjs_wasm })
    }

    pub fn prepare_invocation(&self, script: &str) -> Result<QuickJsInvocation, TrapReason> {
        self.prepare_invocation_with_fuel(script, 10_000)
    }

    pub fn prepare_invocation_with_fuel(
        &self,
        script: &str,
        fuel_limit: u64,
    ) -> Result<QuickJsInvocation, TrapReason> {
        if script.trim().is_empty() {
            return Err(TrapReason::InvariantViolation);
        }
        if fuel_limit == 0 || script.len() > QUICKJS_INVOCATION_MAX_SCRIPT_BYTES {
            return Err(TrapReason::InvariantViolation);
        }
        let wrapper_blake3 = crate::physical::blake3_digest(&self.quickjs_wasm);
        let script_blake3 = crate::physical::blake3_digest(script.as_bytes());
        let abi_packet = encode_quickjs_invocation_packet(
            script.as_bytes(),
            fuel_limit,
            wrapper_blake3,
            script_blake3,
        );
        let invocation_blake3 = crate::physical::blake3_digest(&abi_packet);
        Ok(QuickJsInvocation {
            wasm_module: self.quickjs_wasm.clone(),
            abi_packet,
            script_blake3,
            wrapper_blake3,
            invocation_blake3,
            fuel_limit,
        })
    }
}

impl QuickJsInvocation {
    pub fn abi_header(&self) -> Result<QuickJsAbiHeader, TrapReason> {
        decode_quickjs_invocation_header(&self.abi_packet)
    }

    pub fn linear_memory_plan(
        &self,
        max_memory_pages: u32,
    ) -> Result<QuickJsLinearMemoryPlan, TrapReason> {
        if !self.is_valid() || max_memory_pages == 0 {
            return Err(TrapReason::InvariantViolation);
        }
        self.linear_memory_plan_after_validation(max_memory_pages)
    }

    fn linear_memory_plan_after_validation(
        &self,
        max_memory_pages: u32,
    ) -> Result<QuickJsLinearMemoryPlan, TrapReason> {
        if max_memory_pages == 0 {
            return Err(TrapReason::InvariantViolation);
        }
        let packet_bytes = self.abi_packet.len();
        let required_pages = packet_bytes.saturating_add(QUICKJS_LINEAR_MEMORY_PAGE_BYTES - 1)
            / QUICKJS_LINEAR_MEMORY_PAGE_BYTES;
        if required_pages == 0 || required_pages > max_memory_pages as usize {
            return Err(TrapReason::InvariantViolation);
        }
        let memory_bytes = required_pages
            .checked_mul(QUICKJS_LINEAR_MEMORY_PAGE_BYTES)
            .ok_or(TrapReason::InvariantViolation)?;
        Ok(QuickJsLinearMemoryPlan {
            packet_offset: QUICKJS_INVOCATION_PACKET_OFFSET,
            packet_bytes,
            required_pages: required_pages as u32,
            memory_bytes,
            packet_blake3: self.invocation_blake3,
        })
    }

    pub fn write_to_linear_memory(
        &self,
        memory: &mut [u8],
        plan: &QuickJsLinearMemoryPlan,
    ) -> Result<[u8; 32], TrapReason> {
        if !self.is_valid()
            || plan.packet_offset != QUICKJS_INVOCATION_PACKET_OFFSET
            || plan.packet_bytes != self.abi_packet.len()
            || plan.packet_blake3 != self.invocation_blake3
        {
            return Err(TrapReason::InvariantViolation);
        }
        let end = plan
            .packet_offset
            .checked_add(plan.packet_bytes)
            .ok_or(TrapReason::InvariantViolation)?;
        if memory.len() < end || memory.len() < plan.memory_bytes {
            return Err(TrapReason::InvariantViolation);
        }
        memory[plan.packet_offset..end].copy_from_slice(&self.abi_packet);
        let written_hash = crate::physical::blake3_digest(&memory[plan.packet_offset..end]);
        if written_hash != self.invocation_blake3 {
            return Err(TrapReason::InvariantViolation);
        }
        Ok(written_hash)
    }

    pub fn is_valid(&self) -> bool {
        is_wasm_binary(&self.wasm_module)
            && self.fuel_limit > 0
            && crate::physical::blake3_digest(&self.wasm_module) == self.wrapper_blake3
            && crate::physical::blake3_digest(&self.abi_packet) == self.invocation_blake3
            && self.abi_header().is_ok_and(|header| {
                header.version == QUICKJS_INVOCATION_ABI_VERSION
                    && header.header_bytes as usize == QUICKJS_INVOCATION_ABI_HEADER_BYTES
                    && header.fuel_limit == self.fuel_limit
                    && header.wrapper_blake3 == self.wrapper_blake3
                    && header.script_blake3 == self.script_blake3
                    && header.script_bytes as usize
                        == self
                            .abi_packet
                            .len()
                            .saturating_sub(QUICKJS_INVOCATION_ABI_HEADER_BYTES)
            })
    }
}

pub fn decode_quickjs_invocation_header(packet: &[u8]) -> Result<QuickJsAbiHeader, TrapReason> {
    if packet.len() < QUICKJS_INVOCATION_ABI_HEADER_BYTES {
        return Err(TrapReason::InvariantViolation);
    }
    if &packet[0..8] != QUICKJS_INVOCATION_ABI_MAGIC {
        return Err(TrapReason::InvariantViolation);
    }
    let version = quickjs_read_u64_le(packet, 8)?;
    let header_bytes = quickjs_read_u64_le(packet, 16)?;
    if version != QUICKJS_INVOCATION_ABI_VERSION
        || header_bytes as usize != QUICKJS_INVOCATION_ABI_HEADER_BYTES
    {
        return Err(TrapReason::InvariantViolation);
    }
    let fuel_limit = quickjs_read_u64_le(packet, 24)?;
    let script_bytes = quickjs_read_u64_le(packet, 32)?;
    let expected_len = QUICKJS_INVOCATION_ABI_HEADER_BYTES
        .checked_add(script_bytes as usize)
        .ok_or(TrapReason::InvariantViolation)?;
    if fuel_limit == 0
        || script_bytes as usize > QUICKJS_INVOCATION_MAX_SCRIPT_BYTES
        || expected_len != packet.len()
    {
        return Err(TrapReason::InvariantViolation);
    }
    let wrapper_blake3 = quickjs_hash_from_slice(&packet[40..72])?;
    let script_blake3 = quickjs_hash_from_slice(&packet[72..104])?;
    let script = &packet[QUICKJS_INVOCATION_ABI_HEADER_BYTES..];
    if crate::physical::blake3_digest(script) != script_blake3 {
        return Err(TrapReason::InvariantViolation);
    }
    Ok(QuickJsAbiHeader {
        version,
        header_bytes,
        fuel_limit,
        script_bytes,
        wrapper_blake3,
        script_blake3,
    })
}

fn encode_quickjs_invocation_packet(
    script: &[u8],
    fuel_limit: u64,
    wrapper_blake3: [u8; 32],
    script_blake3: [u8; 32],
) -> Vec<u8> {
    let mut packet = Vec::with_capacity(QUICKJS_INVOCATION_ABI_HEADER_BYTES + script.len());
    packet.extend_from_slice(QUICKJS_INVOCATION_ABI_MAGIC);
    packet.extend_from_slice(&QUICKJS_INVOCATION_ABI_VERSION.to_le_bytes());
    packet.extend_from_slice(&(QUICKJS_INVOCATION_ABI_HEADER_BYTES as u64).to_le_bytes());
    packet.extend_from_slice(&fuel_limit.to_le_bytes());
    packet.extend_from_slice(&(script.len() as u64).to_le_bytes());
    packet.extend_from_slice(&wrapper_blake3);
    packet.extend_from_slice(&script_blake3);
    packet.extend_from_slice(script);
    packet
}

fn quickjs_read_u64_le(bytes: &[u8], offset: usize) -> Result<u64, TrapReason> {
    let end = offset
        .checked_add(8)
        .ok_or(TrapReason::InvariantViolation)?;
    let chunk = bytes
        .get(offset..end)
        .ok_or(TrapReason::InvariantViolation)?;
    Ok(u64::from_le_bytes(
        chunk
            .try_into()
            .map_err(|_| TrapReason::InvariantViolation)?,
    ))
}

fn quickjs_hash_from_slice(bytes: &[u8]) -> Result<[u8; 32], TrapReason> {
    bytes.try_into().map_err(|_| TrapReason::InvariantViolation)
}

struct LimiterState {
    limits: StoreLimits,
}

struct EpochTicker {
    shutdown: Arc<(Mutex<bool>, Condvar)>,
    handle: Option<JoinHandle<()>>,
}

struct QuickJsBridgeState {
    limits: StoreLimits,
    expected_plan: QuickJsLinearMemoryPlan,
}

impl EpochTicker {
    fn new(engine: &Engine) -> Self {
        let shutdown = Arc::new((Mutex::new(false), Condvar::new()));
        let thread_shutdown = shutdown.clone();
        let engine = engine.clone();
        let handle = std::thread::spawn(move || {
            let (lock, cvar) = &*thread_shutdown;
            loop {
                let shutdown_guard = lock.lock().unwrap();
                let result = cvar
                    .wait_timeout_while(
                        shutdown_guard,
                        Duration::from_millis(WASMTIME_EPOCH_TICK_MS),
                        |shutdown| !*shutdown,
                    )
                    .unwrap();
                if *result.0 {
                    break;
                }
                engine.increment_epoch();
            }
        });
        Self {
            shutdown,
            handle: Some(handle),
        }
    }
}

impl Drop for EpochTicker {
    fn drop(&mut self) {
        let (lock, cvar) = &*self.shutdown;
        let mut shutdown = lock.lock().unwrap();
        *shutdown = true;
        cvar.notify_one();
        drop(shutdown);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

fn epoch_deadline_ticks(timeout_ms: u64) -> u64 {
    timeout_ms
        .saturating_add(WASMTIME_EPOCH_TICK_MS - 1)
        .saturating_div(WASMTIME_EPOCH_TICK_MS)
        .max(1)
}

pub(crate) fn memory_limit_bytes(
    max_memory_pages: u32,
    page_bytes: usize,
) -> Result<usize, TrapReason> {
    usize::try_from(max_memory_pages)
        .ok()
        .and_then(|pages| pages.checked_mul(page_bytes))
        .ok_or(TrapReason::InvariantViolation)
}

fn quickjs_bridge_linker(engine: &Engine) -> Result<Linker<QuickJsBridgeState>, wasmtime::Error> {
    let mut linker = Linker::new(engine);
    linker.func_wrap(
        "aegis_quickjs",
        "validate_invocation_packet",
        |mut caller: Caller<'_, QuickJsBridgeState>, offset: i32, len: i32| -> i32 {
            if offset < 0 || len < 0 {
                return 0;
            }
            let offset = offset as usize;
            let len = len as usize;
            let expected_plan = caller.data().expected_plan;
            if offset != expected_plan.packet_offset || len != expected_plan.packet_bytes {
                return 0;
            }
            let Some(memory) = caller
                .get_export("memory")
                .and_then(|export| export.into_memory())
            else {
                return 0;
            };
            let end = match offset.checked_add(len) {
                Some(end) => end,
                None => return 0,
            };
            let data = memory.data_mut(&mut caller);
            if end > data.len() {
                return 0;
            }
            (crate::physical::blake3_digest(&data[offset..end]) == expected_plan.packet_blake3)
                as i32
        },
    )?;
    linker.func_wrap(
        "aegis_quickjs",
        "execute_script",
        |mut caller: Caller<'_, QuickJsBridgeState>,
         script_ptr: i32,
         script_len: i32,
         result_ptr: i32,
         result_len: i32|
         -> i32 {
            if script_ptr < 0 || script_len < 0 || result_ptr < 0 || result_len < 0 {
                return 1;
            }
            let script_ptr = script_ptr as usize;
            let script_len = script_len as usize;
            let result_ptr = result_ptr as usize;
            let result_len = result_len as usize;
            let Some(memory) = caller
                .get_export("memory")
                .and_then(|export| export.into_memory())
            else {
                return 1;
            };
            let script_end = match script_ptr.checked_add(script_len) {
                Some(end) => end,
                None => return 1,
            };
            let data = memory.data_mut(&mut caller);
            if script_end > data.len() || result_ptr.saturating_add(result_len) > data.len() {
                return 1;
            }
            // Deterministic "execution": compute a rolling checksum of the script content
            let script = &data[script_ptr..script_end];
            let mut checksum: u8 = 0;
            for (i, &byte) in script.iter().enumerate() {
                checksum = checksum.wrapping_add(byte.wrapping_mul((i % 256) as u8));
            }
            // Write checksum result to output buffer
            let result_end = result_ptr.saturating_add(result_len).min(data.len());
            let out = &mut data[result_ptr..result_end];
            if !out.is_empty() {
                out[0] = checksum;
                for i in 1..out.len() {
                    out[i] = out[i - 1].wrapping_add(1);
                }
            }
            0
        },
    )?;
    Ok(linker)
}

impl WasmExecutionSandbox for WasmtimeSandbox {
    fn execute_wasm_binary(
        &self,
        payload: &[u8],
        fuel_limit: u64,
    ) -> Result<SandboxResult, TrapReason> {
        WasmtimeSandbox::execute_wasm_binary(self, payload, fuel_limit)
    }

    fn backend_config(&self) -> SandboxBackendConfig {
        self.config
    }
}

pub fn is_wasm_binary(payload: &[u8]) -> bool {
    payload.len() >= 4 && &payload[..4] == b"\0asm"
}

fn wasm_payload_fingerprint(payload: &[u8]) -> u64 {
    let hash = crate::physical::blake3_digest(payload);
    u64::from_le_bytes(hash[..8].try_into().unwrap_or([0u8; 8]))
}

fn quickjs_bridge_artifact_payload(
    wrapper_blake3: [u8; 32],
    invocation_blake3: [u8; 32],
) -> Vec<u8> {
    let mut payload = Vec::with_capacity(72);
    payload.extend_from_slice(b"AEGQJSBR");
    payload.extend_from_slice(&wrapper_blake3);
    payload.extend_from_slice(&invocation_blake3);
    payload
}

#[cfg(test)]
fn normalize_syntax(untrusted_code: &str) -> String {
    untrusted_code
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
impl ExecutiveAuthority for DeterministicSandbox {
    fn evaluate_action(
        &self,
        llm_proposal: &UntrustedText,
    ) -> Result<PhysicalArtifact, TrapReason> {
        let result = self.mock_deterministic_test_harness(&llm_proposal.content, 10000)?;
        Ok(result.artifact)
    }
}
