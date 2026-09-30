//! Bounded Extism execution for untrusted, compute-only WebAssembly plugins.

use extism::{CompiledPlugin, Manifest, Plugin, PluginBuilder, Wasm};

pub(crate) const MAX_PLUGIN_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const MAX_INPUT_BYTES: usize = 1024 * 1024;
pub(crate) const MAX_FUNCTION_NAME_BYTES: usize = 128;
const MAX_OUTPUT_BYTES: usize = 8 * 1024 * 1024;
const MAX_FUEL: u64 = 100_000_000;
const MAX_MEMORY_PAGES: u32 = 256;
const MAX_TIMEOUT_MS: u64 = 30_000;

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

        let mut manifest = Manifest::new([Wasm::data(module.to_vec())])
            .with_memory_max(max_memory_pages)
            .disallow_all_hosts();
        manifest.timeout_ms = Some(timeout_ms);
        manifest.memory.max_var_bytes = Some(0);

        let compiled = PluginBuilder::new(manifest)
            .with_wasi(false)
            .with_fuel_limit(fuel_limit)
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
            .map_err(|_| WasmPluginError::ExecutionFailed)?
            .to_vec();
        if output.len() > self.max_output_bytes {
            return Err(WasmPluginError::OutputTooLarge);
        }
        let fuel_consumed = plugin
            .fuel_consumed()
            .ok_or(WasmPluginError::ExecutionFailed)?;
        Ok((output, fuel_consumed))
    }
}

#[cfg(test)]
mod tests {
    use super::{MAX_MEMORY_PAGES, WasmPluginError, WasmPluginRuntime};

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
        let runtime = runtime(&module, 100_000, 4, 32);
        let (output, fuel) = runtime.call("answer", b"{}").expect("module should run");

        assert_eq!(
            u64::from_le_bytes(output.try_into().expect("8-byte result")),
            42
        );
        assert!(fuel > 0);
        assert_eq!(runtime.module_hash(), *blake3::hash(&module).as_bytes());
    }

    #[test]
    fn rejects_wasi_imports_without_granting_process_or_filesystem_access() {
        let module = wat::parse_str(
            r#"(module
                (import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32)))
                (func (export "run") (result i32) (i32.const 0))
            )"#,
        )
        .expect("fixture WAT should parse");

        assert!(matches!(
            WasmPluginRuntime::compile(&module, 1_000, 4, 1_000, 64),
            Err(WasmPluginError::CompileFailed)
        ));
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
        let runtime = runtime(&module, 1_000, 4, 64);

        assert!(matches!(
            runtime.call("spin", b""),
            Err(WasmPluginError::ExecutionFailed)
        ));
    }

    #[test]
    fn enforces_output_and_manifest_limits() {
        let module = wat::parse_str(OUTPUT_WAT).expect("fixture WAT should parse");
        let runtime = runtime(&module, 100_000, 4, 4);
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
    }
}
