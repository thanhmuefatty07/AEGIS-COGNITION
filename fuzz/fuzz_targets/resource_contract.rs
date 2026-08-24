#![no_main]

use aegis_nerve::resource::ResourceRequest;
use libfuzzer_sys::fuzz_target;

// Keep malformed JSON inside the fuzzer: parsing is a trust-boundary contract,
// and validation must remain panic-free for arbitrary bytes.
fuzz_target!(|payload: &[u8]| {
    if let Ok(request) = serde_json::from_slice::<ResourceRequest>(payload) {
        let _ = request.validate(u64::MAX);
    }
});
