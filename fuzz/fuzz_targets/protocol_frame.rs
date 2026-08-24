#![no_main]

use aegis_nerve::message::MessageFrame;
use libfuzzer_sys::fuzz_target;

// Protocol framing must remain panic-free for arbitrary payload sizes and
// identity values. This does not claim end-to-end zero-copy performance.
fuzz_target!(|payload: &[u8]| {
    let frame = MessageFrame::new(payload.to_vec());
    let _ = frame.is_valid();
    let framed = MessageFrame::with_identity(1, 1, payload.to_vec());
    let _ = framed.is_valid();
});
