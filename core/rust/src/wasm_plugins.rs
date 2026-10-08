//! Bounded Extism execution for untrusted, compute-only WebAssembly plugins.

use extism::{CompiledPlugin, Manifest, Plugin, PluginBuilder, Wasm};

pub(crate) const MAX_PLUGIN_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const MAX_INPUT_BYTES: usize = 1024 * 1024;
pub(crate) const MAX_FUNCTION_NAME_BYTES: usize = 128;
const MAX_OUTPUT_BYTES: usize = 8 * 1024 * 1024;
const MAX_FUEL: u64 = 100_000_000;
const MIN_POOL_MEMORY_PAGES: u32 = 16;
const MAX_MEMORY_PAGES: u32 = 256;
const MAX_TABLE_ELEMENTS: u32 = 65_536;
const MAX_CONCURRENT_PLUGIN_INSTANCES: u32 = 32;
const MAX_TIMEOUT_MS: u64 = 30_000;
const WASM_PAGE_SIZE_BYTES: u64 = 65_536;

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

/// Immutable compiled plugin. Every invocation gets a fresh instance, so guest
/// state cannot leak between tool calls and concurrent calls do not share state.
pub struct WasmPluginRuntime {
    compiled: CompiledPlugin,
    module_hash: [u8; 32],
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

        let mut manifest = Manifest::new([Wasm::data(module.to_vec())])
            .with_memory_max(max_memory_pages)
            .disallow_all_hosts();
        manifest.timeout_ms = Some(timeout_ms);
        manifest.memory.max_var_bytes = Some(0);

        let pool_memory_limit_bytes =
            u64::from(max_memory_pages.max(MIN_POOL_MEMORY_PAGES)) * WASM_PAGE_SIZE_BYTES;
        let mut wasmtime_config = wasmtime::Config::new();
        let mut pooling_config = wasmtime::PoolingAllocationConfig::default();
        pooling_config
            .total_core_instances(MAX_CONCURRENT_PLUGIN_INSTANCES)
            .total_memories(MAX_CONCURRENT_PLUGIN_INSTANCES)
            .total_tables(MAX_CONCURRENT_PLUGIN_INSTANCES)
            .max_memories_per_module(1)
            .max_tables_per_module(1)
            .max_memory_size(pool_memory_limit_bytes as usize)
            .table_elements(MAX_TABLE_ELEMENTS as usize);
        wasmtime_config
            .memory_reservation(pool_memory_limit_bytes)
            .memory_reservation_for_growth(0)
            .memory_may_move(false)
            .allocation_strategy(wasmtime::InstanceAllocationStrategy::Pooling(
                pooling_config,
            ))
            .wasm_multi_memory(false)
            .wasm_memory64(false);

        let compiled = PluginBuilder::new(manifest)
            .with_wasi(false)
            .with_fuel_limit(fuel_limit)
            .with_wasmtime_config(wasmtime_config)
            .compile()
            .map_err(|_| WasmPluginError::CompileFailed)?;

        Ok(Self {
            compiled,
            module_hash: *blake3::hash(module).as_bytes(),
            max_output_bytes,
        })
    }

    pub fn module_hash(&self) -> [u8; 32] {
        self.module_hash
    }

    pub fn call(&self, function: &str, input: &[u8]) -> Result<(Vec<u8>, u64), WasmPluginError> {
        if function.is_empty()
            || function.len() > MAX_FUNCTION_NAME_BYTES
            || !function
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        {
            return Err(WasmPluginError::InvalidPolicy);
        }
        if input.len() > MAX_INPUT_BYTES {
            return Err(WasmPluginError::InputTooLarge);
        }

        let mut plugin = Plugin::new_from_compiled(&self.compiled)
            .map_err(|_| WasmPluginError::ExecutionFailed)?;
        let output = plugin
            .call::<&[u8], &[u8]>(function, input)
            .map_err(|_| WasmPluginError::ExecutionFailed)?;
        if output.len() > self.max_output_bytes {
            return Err(WasmPluginError::OutputTooLarge);
        }
        let output = output.to_vec();
        let fuel_consumed = plugin
            .fuel_consumed()
            .ok_or(WasmPluginError::ExecutionFailed)?;
        Ok((output, fuel_consumed))
    }
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
    use super::{MAX_MEMORY_PAGES, MAX_TABLE_ELEMENTS, WasmPluginError, WasmPluginRuntime};
    use extism::Plugin;

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
                (local $output i64)
                (if (i32.ne (memory.grow (i32.const 1)) (i32.const -1))
                    (then unreachable))
                (local.set $output (call $alloc (i64.const 8)))
                (call $store_u64 (local.get $output) (i64.const 42))
                (call $output_set (local.get $output) (i64.const 8))
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
                Plugin::new_from_compiled(&runtime.compiled).is_err(),
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
                    (local $value i64)
                    (local $divisor i64)
                    (local.set $value (i64.const 123456789))
                    (local.set $divisor (i64.const 3))
                    (loop $again
                        (local.set $value
                            (i64.div_s (i64.add (local.get $value) (i64.const 12345))
                                (local.get $divisor)))
                        (local.set $divisor
                            (i64.add (i64.and (local.get $value) (i64.const 31)) (i64.const 1)))
                        (br $again))
                    (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");
        let runtime = WasmPluginRuntime::compile(&module, super::MAX_FUEL, 16, 10, 64)
            .expect("fixture plugin should compile");
        let mut plugin = Plugin::new_from_compiled(&runtime.compiled)
            .expect("fixture plugin instance should initialize");

        let error = plugin
            .call::<&[u8], &[u8]>("spin", b"")
            .expect_err("compute loop should exceed its deadline");
        assert_eq!(error.root_cause().to_string(), "timeout");
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
}
