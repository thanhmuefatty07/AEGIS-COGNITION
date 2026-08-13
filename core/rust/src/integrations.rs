use crate::ipc::{message_to_zero_copy, validate_default_zero_copy, zero_copy_to_message};
use crate::memory::frame::MemoryFrame;
use crate::message::MessageFrame;
use crate::orchestrator::NerveRuntime;

pub struct IntegrationProbe {
    pub runtime_processed: usize,
    pub latest_session_id: Option<u128>,
    pub latest_payload_len: Option<usize>,
    pub semantic_memory_empty: bool,
    pub zero_copy_ok: bool,
}

pub fn run_integration_probe(
    runtime: &mut NerveRuntime,
    message: MessageFrame,
) -> Result<IntegrationProbe, &'static str> {
    if !message.is_valid() {
        return Err("invalid message frame");
    }
    let zero_copy = message_to_zero_copy(&message)?;
    validate_default_zero_copy(&zero_copy)?;
    let rebuilt = zero_copy_to_message(&zero_copy)?;
    runtime.ingest_message(rebuilt)?;
    let latest = runtime.memory.latest();
    Ok(IntegrationProbe {
        runtime_processed: runtime.processed_count(),
        latest_session_id: latest.map(|frame| frame.session_id),
        latest_payload_len: latest.map(|frame| frame.payload_len()),
        semantic_memory_empty: runtime.memory.is_empty(),
        zero_copy_ok: zero_copy.is_valid(),
    })
}

pub fn frame_from_memory(runtime: &NerveRuntime) -> Option<MemoryFrame> {
    runtime.memory.latest().map(|frame| MemoryFrame {
        frame_id: frame.frame_id,
        session_id: frame.session_id,
        payload: frame.payload.clone(),
        fidelity: frame.fidelity,
    })
}
