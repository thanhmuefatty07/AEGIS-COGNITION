//! Bounded native Wasmtime 50.0.3 execution for untrusted, compute-only WebAssembly plugins.
//!
//! Replaces external git-pinned Extism wrapper with an audited, zero-IO, zero-network
//! `extism:host/env` guest ABI hosted directly on `wasmtime::Engine` + `PoolingAllocationConfig`.

use std::collections::BTreeMap;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;
use wasmtime::{
    Caller, Config, Engine, ExternType, InstanceAllocationStrategy, InstancePre, Linker, Module,
    PoolingAllocationConfig, ResourceLimiter, Store, ValType,
};

pub(crate) const MAX_PLUGIN_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const MAX_INPUT_BYTES: usize = 1024 * 1024;
pub(crate) const MAX_FUNCTION_NAME_BYTES: usize = 128;
const MAX_OUTPUT_BYTES: usize = 8 * 1024 * 1024;
const MAX_HOST_KERNEL_ARENA_BYTES: usize = MAX_INPUT_BYTES + MAX_OUTPUT_BYTES + (64 * 1024);
const MAX_FUEL: u64 = 100_000_000;
const MIN_POOL_MEMORY_PAGES: u32 = 16;
const MAX_MEMORY_PAGES: u32 = 256;
const MAX_TABLE_ELEMENTS: u32 = 65_536;
const MAX_CONCURRENT_PLUGIN_INSTANCES: u32 = 32;
const MAX_TIMEOUT_MS: u64 = 30_000;
const WASM_PAGE_SIZE_BYTES: u64 = 65_536;
const EPOCH_TICK_MS: u64 = 2;
const EXTISM_ENV_MODULE: &str = "extism:host/env";

#[derive(Debug, thiserror::Error)]
pub enum WasmPluginError {
    #[error("WebAssembly plugin policy is invalid")]
    InvalidPolicy,
    #[error("WebAssembly plugin input exceeds its limit")]
    InputTooLarge,
    #[error("WebAssembly plugin could not be compiled")]
    CompileFailed,
    #[error("WebAssembly plugin execution failed or exceeded its resource budget")]
    ExecutionFailed,
    #[error("WebAssembly plugin output exceeds its limit")]
    OutputTooLarge,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WasmCallFailureReason {
    Timeout,
    TrapOrPolicy,
}

#[derive(Default)]
struct EpochTickerState {
    active_calls: usize,
    shutdown: bool,
}

/// Demand-parked runtime epoch ticker. Sleeps indefinitely with zero wakeups while idle,
/// and advances `engine.increment_epoch()` on a single steady wall-clock schedule regardless
/// of concurrent plugin call fan-out.
struct DemandEpochTicker {
    shared: Arc<(Mutex<EpochTickerState>, Condvar)>,
    handle: Option<JoinHandle<()>>,
}

struct ActiveEpochGuard {
    shared: Arc<(Mutex<EpochTickerState>, Condvar)>,
}

impl Drop for ActiveEpochGuard {
    fn drop(&mut self) {
        let (lock, cvar) = &*self.shared;
        if let Ok(mut state) = lock.lock() {
            state.active_calls = state.active_calls.saturating_sub(1);
            if state.active_calls == 0 {
                cvar.notify_one();
            }
        }
    }
}

impl DemandEpochTicker {
    fn try_new(engine: &Engine) -> Result<Self, WasmPluginError> {
        let shared = Arc::new((Mutex::new(EpochTickerState::default()), Condvar::new()));
        let thread_shared = Arc::clone(&shared);
        let engine = engine.clone();
        let handle = std::thread::Builder::new()
            .name("aegis-plugin-epoch".to_owned())
            .spawn(move || {
                let (lock, cvar) = &*thread_shared;
                loop {
                    let mut guard = match lock.lock() {
                        Ok(guard) => guard,
                        Err(_) => break,
                    };
                    while !guard.shutdown && guard.active_calls == 0 {
                        guard = match cvar.wait(guard) {
                            Ok(next) => next,
                            Err(_) => return,
                        };
                    }
                    if guard.shutdown {
                        break;
                    }
                    let waited = match cvar.wait_timeout_while(
                        guard,
                        Duration::from_millis(EPOCH_TICK_MS),
                        |s| !s.shutdown && s.active_calls > 0,
                    ) {
                        Ok(result) => result,
                        Err(_) => break,
                    };
                    if waited.0.shutdown {
                        break;
                    }
                    if waited.0.active_calls > 0 && waited.1.timed_out() {
                        drop(waited);
                        engine.increment_epoch();
                    }
                }
            })
            .map_err(|_| WasmPluginError::CompileFailed)?;
        Ok(Self {
            shared,
            handle: Some(handle),
        })
    }

    fn enter_call(&self) -> Result<ActiveEpochGuard, WasmPluginError> {
        let (lock, cvar) = &*self.shared;
        let mut state = lock.lock().map_err(|_| WasmPluginError::ExecutionFailed)?;
        let was_idle = state.active_calls == 0;
        state.active_calls = state
            .active_calls
            .checked_add(1)
            .ok_or(WasmPluginError::ExecutionFailed)?;
        if was_idle {
            cvar.notify_one();
        }
        Ok(ActiveEpochGuard {
            shared: Arc::clone(&self.shared),
        })
    }
}

impl Drop for DemandEpochTicker {
    fn drop(&mut self) {
        let (lock, cvar) = &*self.shared;
        if let Ok(mut state) = lock.lock() {
            state.shutdown = true;
            cvar.notify_one();
        }
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

struct GuestResourceLimiter {
    max_memory_bytes: usize,
    max_table_elements: usize,
}

impl ResourceLimiter for GuestResourceLimiter {
    fn memory_growing(
        &mut self,
        _current: usize,
        desired: usize,
        maximum: Option<usize>,
    ) -> Result<bool, wasmtime::Error> {
        let cap = maximum.map_or(self.max_memory_bytes, |m| m.min(self.max_memory_bytes));
        Ok(desired <= cap)
    }

    fn table_growing(
        &mut self,
        _current: usize,
        desired: usize,
        maximum: Option<usize>,
    ) -> Result<bool, wasmtime::Error> {
        let cap = maximum.map_or(self.max_table_elements, |m| m.min(self.max_table_elements));
        Ok(desired <= cap)
    }
}

/// Host-side isolated buffer arena implementing the `extism:host/env` handle ABI
/// without touching guest linear memory, cloning input buffers, or granting host I/O.
struct HostKernelArena {
    blocks: BTreeMap<u64, Vec<u8>>,
    next_offset: u64,
    allocated_bytes: usize,
    input_handle: u64,
    output_range: Option<(u64, usize)>,
    error_set: bool,
}

impl HostKernelArena {
    fn new(input: Vec<u8>) -> Self {
        let mut blocks = BTreeMap::new();
        let allocated_bytes = input.len();
        let (input_handle, next_offset) = if input.is_empty() {
            (0, 8_u64)
        } else {
            let handle = 1_u64;
            let next = (1_u64).saturating_add(input.len() as u64).saturating_add(8);
            let aligned_next = (next + 7) & !7;
            blocks.insert(handle, input);
            (handle, aligned_next.max(16))
        };
        Self {
            blocks,
            next_offset,
            allocated_bytes,
            input_handle,
            output_range: None,
            error_set: false,
        }
    }

    fn input_bytes(&self) -> &[u8] {
        if self.input_handle == 0 {
            &[]
        } else {
            self.blocks
                .get(&self.input_handle)
                .map_or(&[], Vec::as_slice)
        }
    }

    fn alloc(&mut self, length: u64) -> Result<u64, wasmtime::Error> {
        if length == 0 {
            return Ok(0);
        }
        let len = usize::try_from(length).map_err(|_| wasmtime::Error::msg("invalid alloc"))?;
        let new_total = self
            .allocated_bytes
            .checked_add(len)
            .ok_or_else(|| wasmtime::Error::msg("arena overflow"))?;
        if new_total > MAX_HOST_KERNEL_ARENA_BYTES {
            return Err(wasmtime::Error::msg("host kernel arena limit exceeded"));
        }
        let offset = self.next_offset;
        self.next_offset = self
            .next_offset
            .checked_add(length)
            .and_then(|v| v.checked_add(8))
            .ok_or_else(|| wasmtime::Error::msg("handle offset overflow"))?;
        self.blocks.insert(offset, vec![0_u8; len]);
        self.allocated_bytes = new_total;
        Ok(offset)
    }

    fn free(&mut self, offset: u64) {
        if offset == 0 || offset == self.input_handle {
            return;
        }
        if let Some(removed) = self.blocks.remove(&offset) {
            self.allocated_bytes = self.allocated_bytes.saturating_sub(removed.len());
        }
    }

    fn block_length(&self, offset: u64) -> u64 {
        if offset == 0 {
            return 0;
        }
        self.blocks.get(&offset).map_or(0, |b| b.len() as u64)
    }

    fn find_slice(&self, offset: u64, len: usize) -> Option<&[u8]> {
        if len == 0 {
            return Some(&[]);
        }
        let (&base, block) = self.blocks.range(..=offset).next_back()?;
        let rel = usize::try_from(offset.checked_sub(base)?).ok()?;
        let end = rel.checked_add(len)?;
        block.get(rel..end)
    }

    fn find_slice_mut(&mut self, offset: u64, len: usize) -> Option<&mut [u8]> {
        if len == 0 {
            return Some(&mut []);
        }
        let (&base, block) = self.blocks.range_mut(..=offset).next_back()?;
        if base == self.input_handle {
            return None;
        }
        let rel = usize::try_from(offset.checked_sub(base)?).ok()?;
        let end = rel.checked_add(len)?;
        block.get_mut(rel..end)
    }

    fn read_output(&self) -> Result<Vec<u8>, WasmPluginError> {
        if self.error_set {
            return Err(WasmPluginError::ExecutionFailed);
        }
        let Some((offset, length)) = self.output_range else {
            return Ok(Vec::new());
        };
        if length == 0 {
            return Ok(Vec::new());
        }
        let slice = self
            .find_slice(offset, length)
            .ok_or(WasmPluginError::ExecutionFailed)?;
        Ok(slice.to_vec())
    }
}

struct PluginCallState {
    limiter: GuestResourceLimiter,
    arena: HostKernelArena,
}

/// Immutable compiled plugin. Every invocation gets a fresh `Store` and `Instance`,
/// so guest state cannot leak between tool calls and concurrent calls do not share state.
pub struct WasmPluginRuntime {
    engine: Engine,
    instance_pre: InstancePre<PluginCallState>,
    epoch_ticker: DemandEpochTicker,
    module_hash: [u8; 32],
    fuel_limit: u64,
    max_memory_pages: u32,
    timeout_ms: u64,
    max_output_bytes: usize,
}

impl WasmPluginRuntime {
    pub fn compile(
        module: &[u8],
        fuel_limit: u64,
        max_memory_pages: u32,
        timeout_ms: u64,
        max_output_bytes: usize,
    ) -> Result<Self, WasmPluginError> {
        if module.len() < 8
            || module.len() > MAX_PLUGIN_BYTES
            || module.get(..4) != Some(&b"\0asm"[..])
            || fuel_limit == 0
            || fuel_limit > MAX_FUEL
            || !(1..=MAX_MEMORY_PAGES).contains(&max_memory_pages)
            || !(1..=MAX_TIMEOUT_MS).contains(&timeout_ms)
            || !(1..=MAX_OUTPUT_BYTES).contains(&max_output_bytes)
        {
            return Err(WasmPluginError::InvalidPolicy);
        }

        validate_module_resources(module, max_memory_pages)?;

        let pool_memory_limit_bytes =
            u64::from(max_memory_pages.max(MIN_POOL_MEMORY_PAGES)) * WASM_PAGE_SIZE_BYTES;
        let mut wasmtime_config = Config::new();
        let mut pooling_config = PoolingAllocationConfig::default();
        pooling_config
            .total_core_instances(MAX_CONCURRENT_PLUGIN_INSTANCES)
            .total_memories(MAX_CONCURRENT_PLUGIN_INSTANCES)
            .total_tables(MAX_CONCURRENT_PLUGIN_INSTANCES)
            .max_memories_per_module(1)
            .max_tables_per_module(1)
            .max_memory_size(pool_memory_limit_bytes as usize)
            .table_elements(MAX_TABLE_ELEMENTS as usize);
        // Note: `wasmtime` is compiled with `default-features = false` without `threads`,
        // so Wasm threads/shared-memory proposals are disabled at compile time.
        wasmtime_config
            .consume_fuel(true)
            .epoch_interruption(true)
            .memory_reservation(pool_memory_limit_bytes)
            .memory_reservation_for_growth(0)
            .memory_may_move(false)
            .allocation_strategy(InstanceAllocationStrategy::Pooling(pooling_config))
            .wasm_multi_memory(false)
            .wasm_memory64(false);

        let engine = Engine::new(&wasmtime_config).map_err(|_| WasmPluginError::CompileFailed)?;
        let compiled_module =
            Module::new(&engine, module).map_err(|_| WasmPluginError::CompileFailed)?;
        let linker = build_extism_host_linker(&engine, &compiled_module)?;
        let instance_pre = linker
            .instantiate_pre(&compiled_module)
            .map_err(|_| WasmPluginError::CompileFailed)?;
        let epoch_ticker = DemandEpochTicker::try_new(&engine)?;

        Ok(Self {
            engine,
            instance_pre,
            epoch_ticker,
            module_hash: *blake3::hash(module).as_bytes(),
            fuel_limit,
            max_memory_pages,
            timeout_ms,
            max_output_bytes,
        })
    }

    pub fn module_hash(&self) -> [u8; 32] {
        self.module_hash
    }

    pub fn call(&self, function: &str, input: &[u8]) -> Result<(Vec<u8>, u64), WasmPluginError> {
        self.call_internal(function, input).map_err(|(err, _)| err)
    }

    pub fn call_owned(
        &self,
        function: &str,
        input: Vec<u8>,
    ) -> Result<(Vec<u8>, u64), WasmPluginError> {
        self.call_internal_owned(function, input)
            .map_err(|(err, _)| err)
    }

    pub(crate) fn call_internal(
        &self,
        function: &str,
        input: &[u8],
    ) -> Result<(Vec<u8>, u64), (WasmPluginError, Option<WasmCallFailureReason>)> {
        if input.len() > MAX_INPUT_BYTES {
            return Err((WasmPluginError::InputTooLarge, None));
        }
        self.call_internal_owned(function, input.to_vec())
    }

    pub(crate) fn call_internal_owned(
        &self,
        function: &str,
        input: Vec<u8>,
    ) -> Result<(Vec<u8>, u64), (WasmPluginError, Option<WasmCallFailureReason>)> {
        if function.is_empty()
            || function.len() > MAX_FUNCTION_NAME_BYTES
            || !function
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        {
            return Err((WasmPluginError::InvalidPolicy, None));
        }
        if input.len() > MAX_INPUT_BYTES {
            return Err((WasmPluginError::InputTooLarge, None));
        }

        let max_memory_bytes =
            usize::try_from(u64::from(self.max_memory_pages) * WASM_PAGE_SIZE_BYTES)
                .map_err(|_| (WasmPluginError::InvalidPolicy, None))?;
        let state = PluginCallState {
            limiter: GuestResourceLimiter {
                max_memory_bytes,
                max_table_elements: MAX_TABLE_ELEMENTS as usize,
            },
            arena: HostKernelArena::new(input),
        };
        let mut store = Store::new(&self.engine, state);
        store.limiter(|s| &mut s.limiter);
        store
            .set_fuel(self.fuel_limit)
            .map_err(|_| (WasmPluginError::ExecutionFailed, None))?;

        let deadline_ticks = self
            .timeout_ms
            .saturating_add(EPOCH_TICK_MS - 1)
            .saturating_div(EPOCH_TICK_MS)
            .max(1);
        store.set_epoch_deadline(deadline_ticks);
        let _epoch_guard = self.epoch_ticker.enter_call().map_err(|err| (err, None))?;

        let instance = self.instance_pre.instantiate(&mut store).map_err(|_| {
            (
                WasmPluginError::ExecutionFailed,
                Some(WasmCallFailureReason::TrapOrPolicy),
            )
        })?;

        let func = instance
            .get_func(&mut store, function)
            .ok_or((WasmPluginError::ExecutionFailed, None))?;
        let func_ty = func.ty(&store);
        if func_ty.params().len() != 0 {
            return Err((WasmPluginError::ExecutionFailed, None));
        }
        let result_arity = func_ty.results().len();
        if result_arity > 1 {
            return Err((WasmPluginError::ExecutionFailed, None));
        }
        if result_arity == 1 && !matches!(func_ty.results().next(), Some(ValType::I32)) {
            return Err((WasmPluginError::ExecutionFailed, None));
        }

        let mut results = vec![wasmtime::Val::I32(0); result_arity];
        if let Err(trap_err) = func.call(&mut store, &[], &mut results) {
            let remaining_fuel = store.get_fuel().unwrap_or(0);
            let trap_code = trap_err.downcast_ref::<wasmtime::Trap>().copied();
            let is_timeout =
                matches!(trap_code, Some(wasmtime::Trap::Interrupt)) && remaining_fuel > 0;
            let reason = if is_timeout {
                WasmCallFailureReason::Timeout
            } else {
                WasmCallFailureReason::TrapOrPolicy
            };
            return Err((WasmPluginError::ExecutionFailed, Some(reason)));
        }
        if result_arity == 1
            && let Some(wasmtime::Val::I32(rc)) = results.first()
            && *rc != 0
        {
            return Err((
                WasmPluginError::ExecutionFailed,
                Some(WasmCallFailureReason::TrapOrPolicy),
            ));
        }

        let output = store
            .data()
            .arena
            .read_output()
            .map_err(|err| (err, Some(WasmCallFailureReason::TrapOrPolicy)))?;
        if output.len() > self.max_output_bytes {
            return Err((WasmPluginError::OutputTooLarge, None));
        }
        let remaining_fuel = store
            .get_fuel()
            .map_err(|_| (WasmPluginError::ExecutionFailed, None))?;
        let fuel_consumed = self.fuel_limit.saturating_sub(remaining_fuel);
        Ok((output, fuel_consumed))
    }
}

fn build_extism_host_linker(
    engine: &Engine,
    module: &Module,
) -> Result<Linker<PluginCallState>, WasmPluginError> {
    let mut linker = Linker::new(engine);

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "alloc",
            |mut caller: Caller<'_, PluginCallState>, len: i64| -> Result<i64, wasmtime::Error> {
                if len < 0 {
                    return Err(wasmtime::Error::msg("negative alloc"));
                }
                let handle = caller.data_mut().arena.alloc(len as u64)?;
                Ok(handle as i64)
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "free",
            |mut caller: Caller<'_, PluginCallState>, offset: i64| {
                if offset > 0 {
                    caller.data_mut().arena.free(offset as u64);
                }
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    for name in ["length", "length_unsafe"] {
        linker
            .func_wrap(
                EXTISM_ENV_MODULE,
                name,
                |caller: Caller<'_, PluginCallState>, offset: i64| -> i64 {
                    if offset <= 0 {
                        return 0;
                    }
                    caller.data().arena.block_length(offset as u64) as i64
                },
            )
            .map_err(|_| WasmPluginError::CompileFailed)?;
    }

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "store_u8",
            |mut caller: Caller<'_, PluginCallState>,
             offset: i64,
             value: i32|
             -> Result<(), wasmtime::Error> {
                if offset <= 0 {
                    return Err(wasmtime::Error::msg("invalid store_u8 offset"));
                }
                let slice = caller
                    .data_mut()
                    .arena
                    .find_slice_mut(offset as u64, 1)
                    .ok_or_else(|| wasmtime::Error::msg("store_u8 out of bounds"))?;
                slice[0] = value as u8;
                Ok(())
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "store_u64",
            |mut caller: Caller<'_, PluginCallState>,
             offset: i64,
             value: i64|
             -> Result<(), wasmtime::Error> {
                if offset <= 0 {
                    return Err(wasmtime::Error::msg("invalid store_u64 offset"));
                }
                let slice = caller
                    .data_mut()
                    .arena
                    .find_slice_mut(offset as u64, 8)
                    .ok_or_else(|| wasmtime::Error::msg("store_u64 out of bounds"))?;
                slice.copy_from_slice(&(value as u64).to_le_bytes());
                Ok(())
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "load_u8",
            |caller: Caller<'_, PluginCallState>, offset: i64| -> Result<i32, wasmtime::Error> {
                if offset <= 0 {
                    return Err(wasmtime::Error::msg("invalid load_u8 offset"));
                }
                let slice = caller
                    .data()
                    .arena
                    .find_slice(offset as u64, 1)
                    .ok_or_else(|| wasmtime::Error::msg("load_u8 out of bounds"))?;
                Ok(i32::from(slice[0]))
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "load_u64",
            |caller: Caller<'_, PluginCallState>, offset: i64| -> Result<i64, wasmtime::Error> {
                if offset <= 0 {
                    return Err(wasmtime::Error::msg("invalid load_u64 offset"));
                }
                let slice = caller
                    .data()
                    .arena
                    .find_slice(offset as u64, 8)
                    .ok_or_else(|| wasmtime::Error::msg("load_u64 out of bounds"))?;
                let mut buf = [0_u8; 8];
                buf.copy_from_slice(slice);
                Ok(u64::from_le_bytes(buf) as i64)
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "input_length",
            |caller: Caller<'_, PluginCallState>| -> i64 {
                caller.data().arena.input_bytes().len() as i64
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "input_offset",
            |caller: Caller<'_, PluginCallState>| -> i64 {
                caller.data().arena.input_handle as i64
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "input_load_u8",
            |caller: Caller<'_, PluginCallState>, offset: i64| -> Result<i32, wasmtime::Error> {
                let idx = usize::try_from(offset)
                    .map_err(|_| wasmtime::Error::msg("negative input_load_u8 offset"))?;
                let byte = caller
                    .data()
                    .arena
                    .input_bytes()
                    .get(idx)
                    .copied()
                    .ok_or_else(|| wasmtime::Error::msg("input_load_u8 out of bounds"))?;
                Ok(i32::from(byte))
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "input_load_u64",
            |caller: Caller<'_, PluginCallState>, offset: i64| -> Result<i64, wasmtime::Error> {
                let idx = usize::try_from(offset)
                    .map_err(|_| wasmtime::Error::msg("negative input_load_u64 offset"))?;
                let end = idx
                    .checked_add(8)
                    .ok_or_else(|| wasmtime::Error::msg("input_load_u64 overflow"))?;
                let slice = caller
                    .data()
                    .arena
                    .input_bytes()
                    .get(idx..end)
                    .ok_or_else(|| wasmtime::Error::msg("input_load_u64 out of bounds"))?;
                let mut buf = [0_u8; 8];
                buf.copy_from_slice(slice);
                Ok(u64::from_le_bytes(buf) as i64)
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "output_set",
            |mut caller: Caller<'_, PluginCallState>,
             offset: i64,
             length: i64|
             -> Result<(), wasmtime::Error> {
                if length < 0 || offset < 0 {
                    return Err(wasmtime::Error::msg("negative output_set bounds"));
                }
                let len = usize::try_from(length)
                    .map_err(|_| wasmtime::Error::msg("invalid output_set length"))?;
                if len > 0 && caller.data().arena.find_slice(offset as u64, len).is_none() {
                    return Err(wasmtime::Error::msg("output_set handle out of bounds"));
                }
                caller.data_mut().arena.output_range = Some((offset as u64, len));
                Ok(())
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "error_set",
            |mut caller: Caller<'_, PluginCallState>, _offset: i64| {
                caller.data_mut().arena.error_set = true;
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;

    linker
        .func_wrap(EXTISM_ENV_MODULE, "config_get", |_offset: i64| -> i64 { 0 })
        .map_err(|_| WasmPluginError::CompileFailed)?;
    linker
        .func_wrap(EXTISM_ENV_MODULE, "var_get", |_offset: i64| -> i64 { 0 })
        .map_err(|_| WasmPluginError::CompileFailed)?;
    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "var_set",
            |_key: i64, _val: i64| -> Result<(), wasmtime::Error> {
                Err(wasmtime::Error::msg("plugin variables are disabled"))
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;
    linker
        .func_wrap(
            EXTISM_ENV_MODULE,
            "http_request",
            |_req: i64, _body: i64| -> Result<i64, wasmtime::Error> {
                Err(wasmtime::Error::msg("network requests are disabled"))
            },
        )
        .map_err(|_| WasmPluginError::CompileFailed)?;
    linker
        .func_wrap(EXTISM_ENV_MODULE, "http_status_code", || -> i32 { 0 })
        .map_err(|_| WasmPluginError::CompileFailed)?;
    linker
        .func_wrap(EXTISM_ENV_MODULE, "http_headers", || -> i64 { 0 })
        .map_err(|_| WasmPluginError::CompileFailed)?;
    linker
        .func_wrap(EXTISM_ENV_MODULE, "get_log_level", || -> i32 { i32::MAX })
        .map_err(|_| WasmPluginError::CompileFailed)?;
    for log_fn in [
        "log_trace",
        "log_debug",
        "log_info",
        "log_warn",
        "log_error",
    ] {
        linker
            .func_wrap(EXTISM_ENV_MODULE, log_fn, |_offset: i64| {})
            .map_err(|_| WasmPluginError::CompileFailed)?;
    }

    // Reject modules requiring WASI or unprovided host imports at compile time.
    for import in module.imports() {
        if import.module() != EXTISM_ENV_MODULE {
            return Err(WasmPluginError::CompileFailed);
        }
        if !matches!(import.ty(), ExternType::Func(_)) {
            return Err(WasmPluginError::CompileFailed);
        }
    }

    Ok(linker)
}

fn validate_module_resources(module: &[u8], max_memory_pages: u32) -> Result<(), WasmPluginError> {
    let mut memory_count = 0_u32;
    let mut table_count = 0_u32;
    for payload in wasmparser::Parser::new(0).parse_all(module) {
        let payload = payload.map_err(|_| WasmPluginError::CompileFailed)?;
        let resources: Vec<wasmparser::TypeRef> = match payload {
            wasmparser::Payload::ImportSection(imports) => imports
                .into_imports()
                .map(|import| {
                    import
                        .map(|import| import.ty)
                        .map_err(|_| WasmPluginError::CompileFailed)
                })
                .collect::<Result<Vec<_>, _>>()?,
            wasmparser::Payload::MemorySection(memories) => memories
                .into_iter()
                .map(|memory| {
                    memory
                        .map(wasmparser::TypeRef::Memory)
                        .map_err(|_| WasmPluginError::CompileFailed)
                })
                .collect::<Result<Vec<_>, _>>()?,
            wasmparser::Payload::TableSection(tables) => tables
                .into_iter()
                .map(|table| {
                    table
                        .map(|table| wasmparser::TypeRef::Table(table.ty))
                        .map_err(|_| WasmPluginError::CompileFailed)
                })
                .collect::<Result<Vec<_>, _>>()?,
            _ => continue,
        };

        for resource in resources {
            match resource {
                wasmparser::TypeRef::Memory(memory) => {
                    memory_count = memory_count
                        .checked_add(1)
                        .ok_or(WasmPluginError::InvalidPolicy)?;
                    if memory_count > 1
                        || memory.initial > u64::from(max_memory_pages)
                        || memory.memory64
                        || memory.shared
                        || memory
                            .page_size_log2
                            .is_some_and(|page_size| page_size != 16)
                    {
                        return Err(WasmPluginError::InvalidPolicy);
                    }
                }
                wasmparser::TypeRef::Table(table) => {
                    table_count = table_count
                        .checked_add(1)
                        .ok_or(WasmPluginError::InvalidPolicy)?;
                    if table_count > 1
                        || table.initial > u64::from(MAX_TABLE_ELEMENTS)
                        || table.table64
                        || table.shared
                    {
                        return Err(WasmPluginError::InvalidPolicy);
                    }
                }
                _ => {}
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        MAX_MEMORY_PAGES, MAX_TABLE_ELEMENTS, WasmCallFailureReason, WasmPluginError,
        WasmPluginRuntime,
    };

    const OUTPUT_WAT: &str = r#"
        (module
            (import "extism:host/env" "alloc" (func $alloc (param i64) (result i64)))
            (import "extism:host/env" "store_u64" (func $store_u64 (param i64 i64)))
            (import "extism:host/env" "output_set" (func $output_set (param i64 i64)))
            (memory (export "memory") 1)
            (func (export "answer") (result i32)
                (local $output i64)
                (local.set $output (call $alloc (i64.const 8)))
                (call $store_u64 (local.get $output) (i64.const 42))
                (call $output_set (local.get $output) (i64.const 8))
                (i32.const 0)
            )
            (func (export "grow_past_one_page") (result i32)
                (if (i32.ne (memory.grow (i32.const 1)) (i32.const -1))
                    (then unreachable))
                (drop (i32.load (i32.const 65536)))
                (i32.const 0)
            )
        )
    "#;

    fn runtime(
        module: &[u8],
        fuel: u64,
        memory_pages: u32,
        max_output: usize,
    ) -> WasmPluginRuntime {
        WasmPluginRuntime::compile(module, fuel, memory_pages, 2_000, max_output)
            .expect("fixture plugin should compile")
    }

    #[test]
    fn runs_a_valid_module_with_bounded_output_and_fuel_evidence() {
        let module = wat::parse_str(OUTPUT_WAT).expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 16, 32);
        let (output, fuel) = runtime.call("answer", b"{}").expect("module should run");

        assert_eq!(
            u64::from_le_bytes(output.try_into().expect("8-byte result")),
            42
        );
        assert!(fuel > 0);
        assert_eq!(runtime.module_hash(), *blake3::hash(&module).as_bytes());
    }

    #[test]
    fn echoes_input_through_native_extism_host_abi_without_state_leakage() {
        let module = wat::parse_str(
            r#"(module
                (import "extism:host/env" "input_length" (func $input_length (result i64)))
                (import "extism:host/env" "input_load_u8" (func $input_load_u8 (param i64) (result i32)))
                (import "extism:host/env" "alloc" (func $alloc (param i64) (result i64)))
                (import "extism:host/env" "store_u8" (func $store_u8 (param i64 i32)))
                (import "extism:host/env" "output_set" (func $output_set (param i64 i64)))
                (memory (export "memory") 1)
                (func (export "upper_first") (result i32)
                    (local $len i64)
                    (local $out i64)
                    (local.set $len (call $input_length))
                    (local.set $out (call $alloc (local.get $len)))
                    (if (i64.gt_u (local.get $len) (i64.const 0))
                        (then
                            (call $store_u8
                                (local.get $out)
                                (i32.sub (call $input_load_u8 (i64.const 0)) (i32.const 32)))
                        )
                    )
                    (call $output_set (local.get $out) (local.get $len))
                    (i32.const 0)
                )
            )"#,
        )
        .expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 16, 64);
        let (out_a, fuel_a) = runtime.call("upper_first", b"a").expect("first call");
        let (out_b, fuel_b) = runtime.call("upper_first", b"z").expect("second call");
        assert_eq!(out_a, b"A");
        assert_eq!(out_b, b"Z");
        assert!(fuel_a > 0 && fuel_b > 0);
    }

    #[test]
    fn honors_a_one_page_manifest_limit_with_pooling_allocator_floor() {
        let module = wat::parse_str(OUTPUT_WAT).expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 1, 32);
        let (output, _) = runtime
            .call("answer", b"{}")
            .expect("one-page plugin should run within its declared limit");

        assert_eq!(
            u64::from_le_bytes(output.try_into().expect("8-byte result")),
            42
        );
        assert!(matches!(
            runtime.call("grow_past_one_page", b"{}"),
            Err(WasmPluginError::ExecutionFailed)
        ));
    }

    #[test]
    fn does_not_instantiate_modules_with_unavailable_wasi_imports() {
        let module = wat::parse_str(
            r#"(module
                (import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32)))
                (func (export "run") (result i32) (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");

        match WasmPluginRuntime::compile(&module, 1_000, 16, 1_000, 64) {
            Err(WasmPluginError::CompileFailed) => {}
            Err(error) => panic!("unexpected policy error: {error}"),
            Ok(runtime) => assert!(
                runtime.call("run", b"").is_err(),
                "a module requiring WASI must not instantiate when the host disables WASI"
            ),
        }
    }

    #[test]
    fn execution_is_stopped_when_the_fuel_budget_is_exhausted() {
        let module = wat::parse_str(
            r#"(module
                (func (export "spin") (result i32)
                    (loop $again (br $again))
                    (i32.const 0)
                )
            )"#,
        )
        .expect("fixture WAT should parse");
        let runtime = runtime(&module, 1_000, 16, 64);

        assert!(matches!(
            runtime.call("spin", b""),
            Err(WasmPluginError::ExecutionFailed)
        ));
    }

    #[test]
    fn execution_is_stopped_by_the_deadline_before_fuel_is_exhausted() {
        let module = wat::parse_str(
            r#"(module
                (func (export "spin") (result i32)
                    (local $value f64)
                    (local.set $value (f64.const 123456789.0))
                    (loop $again
                        (local.set $value
                            (f64.add
                                (f64.sqrt (f64.sqrt (f64.sqrt (f64.sqrt (local.get $value)))))
                                (f64.const 1000.0)))
                        (local.set $value
                            (f64.add
                                (f64.sqrt (f64.sqrt (f64.sqrt (f64.sqrt (local.get $value)))))
                                (f64.const 1000.0)))
                        (local.set $value
                            (f64.add
                                (f64.sqrt (f64.sqrt (f64.sqrt (f64.sqrt (local.get $value)))))
                                (f64.const 1000.0)))
                        (local.set $value
                            (f64.add
                                (f64.sqrt (f64.sqrt (f64.sqrt (f64.sqrt (local.get $value)))))
                                (f64.const 1000.0)))
                        (br $again))
                    (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");
        let runtime = WasmPluginRuntime::compile(&module, super::MAX_FUEL, 16, 10, 64)
            .expect("fixture plugin should compile");

        let (error, reason) = runtime
            .call_internal("spin", b"")
            .expect_err("compute loop should exceed its deadline");
        assert!(matches!(error, WasmPluginError::ExecutionFailed));
        assert_eq!(reason, Some(WasmCallFailureReason::Timeout));
    }

    #[test]
    fn enforces_output_and_manifest_limits() {
        let module = wat::parse_str(OUTPUT_WAT).expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 16, 4);
        assert!(matches!(
            runtime.call("answer", b""),
            Err(WasmPluginError::OutputTooLarge)
        ));
        assert!(matches!(
            WasmPluginRuntime::compile(&module, 0, 4, 1_000, 64),
            Err(WasmPluginError::InvalidPolicy)
        ));
        assert!(matches!(
            WasmPluginRuntime::compile(&module, 100, MAX_MEMORY_PAGES + 1, 1_000, 64),
            Err(WasmPluginError::InvalidPolicy)
        ));
        assert!(matches!(
            WasmPluginRuntime::compile(&module, 100, 0, 1_000, 64),
            Err(WasmPluginError::InvalidPolicy)
        ));
    }

    #[test]
    fn guest_cannot_grow_memory_past_the_manifest_limit() {
        let module = wat::parse_str(
            r#"(module
                (import "extism:host/env" "alloc" (func $alloc (param i64) (result i64)))
                (import "extism:host/env" "store_u64" (func $store_u64 (param i64 i64)))
                (import "extism:host/env" "output_set" (func $output_set (param i64 i64)))
                (memory (export "memory") 1)
                (func (export "grow_past_limit") (result i32)
                    (local $output i64)
                    i32.const 15
                    memory.grow
                    i32.const 1
                    i32.ne
                    if
                        unreachable
                    end
                    i32.const 1
                    memory.grow
                    i32.const -1
                    i32.ne
                    if
                        unreachable
                    end
                    (local.set $output (call $alloc (i64.const 8)))
                    (call $store_u64 (local.get $output) (i64.const 42))
                    (call $output_set (local.get $output) (i64.const 8))
                    (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 16, 64);

        let (output, fuel) = runtime
            .call("grow_past_limit", b"")
            .expect("the guest should continue after memory.grow is rejected");
        assert_eq!(
            u64::from_le_bytes(output.try_into().expect("8-byte result")),
            42
        );
        assert!(fuel > 0);
    }

    #[test]
    fn rejects_initial_memory_above_the_manifest_limit() {
        let module = wat::parse_str(
            r#"(module
                (memory (export "memory") 2)
                (func (export "run") (result i32) (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");

        assert!(matches!(
            WasmPluginRuntime::compile(&module, 100_000, 1, 1_000, 64),
            Err(WasmPluginError::InvalidPolicy)
        ));
    }

    #[test]
    fn rejects_modules_with_more_than_one_linear_memory() {
        let module = wat::parse_str(
            r#"(module
                (memory 1)
                (memory 1)
                (func (export "run") (result i32) (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");

        assert!(matches!(
            WasmPluginRuntime::compile(&module, 100_000, 4, 1_000, 64),
            Err(WasmPluginError::InvalidPolicy)
        ));
    }

    #[test]
    fn rejects_modules_with_oversized_or_multiple_tables() {
        let oversized_table = wat::parse_str(
            r#"(module
                (memory 1)
                (table 65537 funcref)
                (func (export "run") (result i32) (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");
        let multiple_tables = wat::parse_str(
            r#"(module
                (memory 1)
                (table 0 funcref)
                (table 0 funcref)
                (func (export "run") (result i32) (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");

        for module in [&oversized_table, &multiple_tables] {
            assert!(matches!(
                WasmPluginRuntime::compile(module, 100_000, 16, 1_000, 64),
                Err(WasmPluginError::InvalidPolicy)
            ));
        }
    }

    #[test]
    fn caps_unbounded_table_growth_at_the_configured_element_limit() {
        let source = format!(
            r#"(module
                (import "extism:host/env" "alloc" (func $alloc (param i64) (result i64)))
                (import "extism:host/env" "store_u64" (func $store_u64 (param i64 i64)))
                (import "extism:host/env" "output_set" (func $output_set (param i64 i64)))
                (memory (export "memory") 1)
                (table 0 funcref)
                (func (export "grow_table") (result i32)
                    (local $output i64)
                    ref.null func
                    i32.const {MAX_TABLE_ELEMENTS}
                    table.grow 0
                    i32.const 0
                    i32.ne
                    if
                        unreachable
                    end
                    ref.null func
                    i32.const 1
                    table.grow 0
                    i32.const -1
                    i32.ne
                    if
                        unreachable
                    end
                    (local.set $output (call $alloc (i64.const 8)))
                    (call $store_u64 (local.get $output) (i64.const 42))
                    (call $output_set (local.get $output) (i64.const 8))
                    (i32.const 0))
            )"#
        );
        let module = wat::parse_str(source).expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 16, 64);

        let (output, fuel) = runtime
            .call("grow_table", b"")
            .expect("growth through the cap should work and growth beyond it should fail");
        assert_eq!(
            u64::from_le_bytes(output.try_into().expect("8-byte result")),
            42
        );
        assert!(fuel > 0);
    }

    #[test]
    fn single_copy_input_handle_preserves_length_and_blocks_guest_free_or_mutation() {
        let module = wat::parse_str(
            r#"(module
                (import "extism:host/env" "input_offset" (func $input_offset (result i64)))
                (import "extism:host/env" "length" (func $length (param i64) (result i64)))
                (import "extism:host/env" "length_unsafe" (func $length_unsafe (param i64) (result i64)))
                (import "extism:host/env" "free" (func $free (param i64)))
                (import "extism:host/env" "load_u8" (func $load_u8 (param i64) (result i32)))
                (import "extism:host/env" "alloc" (func $alloc (param i64) (result i64)))
                (import "extism:host/env" "store_u64" (func $store_u64 (param i64 i64)))
                (import "extism:host/env" "output_set" (func $output_set (param i64 i64)))
                (memory (export "memory") 1)
                (func (export "inspect_input") (result i32)
                    (local $in_h i64)
                    (local $len_a i64)
                    (local $len_b i64)
                    (local $out i64)
                    (local.set $in_h (call $input_offset))
                    (call $free (local.get $in_h))
                    (local.set $len_a (call $length (local.get $in_h)))
                    (local.set $len_b (call $length_unsafe (local.get $in_h)))
                    (if (i64.ne (local.get $len_a) (local.get $len_b))
                        (then unreachable))
                    (if (i32.ne (call $load_u8 (local.get $in_h)) (i32.const 112))
                        (then unreachable))
                    (local.set $out (call $alloc (i64.const 8)))
                    (call $store_u64 (local.get $out) (local.get $len_a))
                    (call $output_set (local.get $out) (i64.const 8))
                    (i32.const 0)
                )
            )"#,
        )
        .expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 16, 64);
        let payload = b"payload-11-b".to_vec();
        let expected_len = payload.len() as u64;
        let (output, _) = runtime
            .call_owned("inspect_input", payload)
            .expect("single-copy input handle should resolve length and bytes");
        assert_eq!(
            u64::from_le_bytes(output.try_into().expect("8-byte result")),
            expected_len
        );
    }

    #[test]
    fn concurrent_calls_share_demand_epoch_ticker_without_clock_acceleration() {
        let module = wat::parse_str(OUTPUT_WAT).expect("fixture WAT should parse");
        let runtime = std::sync::Arc::new(
            WasmPluginRuntime::compile(&module, 500_000, 16, 500, 64)
                .expect("fixture plugin should compile"),
        );
        std::thread::scope(|scope| {
            let mut handles = Vec::new();
            for _ in 0..16 {
                let rt = std::sync::Arc::clone(&runtime);
                handles.push(scope.spawn(move || {
                    for _ in 0..8 {
                        let (out, _) = rt.call("answer", b"{}").expect("concurrent call ok");
                        assert_eq!(
                            u64::from_le_bytes(out.try_into().expect("8-byte result")),
                            42
                        );
                    }
                }));
            }
            for handle in handles {
                handle.join().expect("worker thread should succeed");
            }
        });
    }
}
