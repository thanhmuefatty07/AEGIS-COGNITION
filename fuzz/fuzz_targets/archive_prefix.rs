#![no_main]

use aegis_nerve::replay::BinaryRunEventSegment;
use libfuzzer_sys::fuzz_target;
use std::fs;

// The archive recovery API must reject or recover arbitrary partial bytes
// without panicking. This target is deliberately an I/O-bound campaign.
fuzz_target!(|payload: &[u8]| {
    let path = std::env::temp_dir().join(format!(
        "aegis-fuzz-archive-{}-{}",
        std::process::id(),
        payload.len()
    ));
    if fs::write(&path, payload).is_ok() {
        let _ = BinaryRunEventSegment::recover_last_valid_prefix_mmap(&path);
        let _ = fs::remove_file(path);
    }
});
