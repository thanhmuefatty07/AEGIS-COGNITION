use crate::message::{MessageFrame, MessageHeader};
use crate::schema::{BinarySchemaId, NERVE_SCHEMA, SchemaRegistry};

pub struct ZeroCopyFrame {
    pub schema_id: BinarySchemaId,
    pub message_id: u128,
    pub session_id: u128,
    pub payload_ptr: *const u8,
    pub payload_len: usize,
}

impl ZeroCopyFrame {
    #[inline]
    pub fn is_valid(&self) -> bool {
        !self.payload_ptr.is_null()
            && self.payload_len > 0
            && self.message_id > 0
            && self.session_id > 0
    }
}

pub trait InterProcessCommunication {
    fn transmit_state(&self, buffer: &[u8]) -> Result<(), &'static str>;
}

pub fn validate_frame(
    frame: &ZeroCopyFrame,
    registry: &SchemaRegistry,
    version: u32,
) -> Result<(), &'static str> {
    if !registry.is_valid() {
        return Err("invalid schema registry");
    }
    registry.validate(frame.schema_id, version)?;
    if frame.payload_ptr.is_null() {
        return Err("null payload pointer");
    }
    if frame.payload_len == 0 {
        return Err("empty payload");
    }
    if frame.message_id == 0 || frame.session_id == 0 {
        return Err("invalid message identity");
    }
    Ok(())
}

#[inline]
pub fn validate_zero_copy(
    frame: &ZeroCopyFrame,
    registry: &SchemaRegistry,
) -> Result<(), &'static str> {
    validate_frame(frame, registry, registry.version)
}

#[inline]
pub fn validate_default_zero_copy(frame: &ZeroCopyFrame) -> Result<(), &'static str> {
    validate_zero_copy(frame, &NERVE_SCHEMA)
}

#[inline]
pub fn payload_span(frame: &ZeroCopyFrame) -> Result<(*const u8, usize), &'static str> {
    if !frame.is_valid() {
        return Err("invalid frame");
    }
    Ok((frame.payload_ptr, frame.payload_len))
}

#[inline]
pub fn message_to_zero_copy(message: &MessageFrame) -> Result<ZeroCopyFrame, &'static str> {
    if !message.is_valid() {
        return Err("invalid message frame");
    }
    Ok(ZeroCopyFrame {
        schema_id: message.header.schema_id,
        message_id: message.header.message_id,
        session_id: message.header.session_id,
        payload_ptr: message.payload.as_ptr(),
        payload_len: message.payload.len(),
    })
}

#[inline]
pub fn zero_copy_to_message(frame: &ZeroCopyFrame) -> Result<MessageFrame, &'static str> {
    if !frame.is_valid() {
        return Err("invalid zero-copy frame");
    }
    // SAFETY: `frame.is_valid()` (lines 13-19) and `validate_frame` (lines 26-45)
    // both verify `payload_ptr` is non-null, `payload_len > 0`, and `message_id`
    // / `session_id` are non-zero. Callers that produce a `ZeroCopyFrame` via
    // `message_to_zero_copy` (line 68) or `mmap`-backed readers pass a pointer
    // pointing into the backing allocation (MessageFrame buffer or mmap region)
    // with `payload_len <= allocation.len()`. Resulting slice is read-only and
    // copied (`.to_vec()`) into a fresh `MessageFrame` so no aliasing survives.
    // Lifetime: the slice is dropped at end of expression; the `Vec` outlives
    // any reference to the original `ZeroCopyFrame`.
    let payload =
        unsafe { core::slice::from_raw_parts(frame.payload_ptr, frame.payload_len) }.to_vec();
    Ok(MessageFrame {
        header: MessageHeader {
            message_id: frame.message_id,
            session_id: frame.session_id,
            schema_id: NERVE_SCHEMA.schema_id,
            version: NERVE_SCHEMA.version,
            payload_len: payload.len(),
            alignment: NERVE_SCHEMA.alignment,
        },
        payload,
    })
}

#[inline]
pub fn zero_copy_is_default_schema(frame: &ZeroCopyFrame) -> bool {
    frame.schema_id == NERVE_SCHEMA.schema_id
}
