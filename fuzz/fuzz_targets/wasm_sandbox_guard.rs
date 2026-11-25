#![no_main]

use aegis_nerve::sandbox::WasmtimeSandbox;
use libfuzzer_sys::fuzz_target;
use std::sync::OnceLock;

static SANDBOX: OnceLock<WasmtimeSandbox> = OnceLock::new();

fuzz_target!(|payload: &[u8]| {
    let sandbox = SANDBOX.get_or_init(WasmtimeSandbox::new);
    let _ = sandbox.execute_wasm_binary(payload, 10_000);
});
