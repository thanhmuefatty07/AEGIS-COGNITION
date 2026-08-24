#![no_main]

use aegis_nerve::resource::ResourceRequest;
use libfuzzer_sys::fuzz_target;

// Exercise the JSON boundary used by runtime/FFI callers. Parsed requests are
// still validated by the Rust authority; malformed bytes must not panic.
fuzz_target!(|payload: &[u8]| {
    if let Ok(request) = serde_json::from_slice::<ResourceRequest>(payload) {
        let _ = request.validate(u64::MAX);
    }
});
