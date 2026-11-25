#![no_main]

use aegis_nerve::bridge_mmap::MmapBridgeHeader;
use aegis_nerve::sandbox::decode_quickjs_invocation_header;
use libfuzzer_sys::fuzz_target;

fuzz_target!(|payload: &[u8]| {
    let _ = decode_quickjs_invocation_header(payload);

    if let Ok(header) = MmapBridgeHeader::decode(payload) {
        let _ = header.validate_metadata(payload.len());
        let _ = header.payload_range(payload.len());
        let _ = header.encode();
        if payload.len() >= 8 {
            let synth_len = u64::from_le_bytes(payload[0..8].try_into().unwrap_or([0; 8])) as usize;
            let _ = header.validate_metadata(synth_len);
            let _ = header.payload_range(synth_len);
        }
    }
});
