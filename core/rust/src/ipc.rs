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

/// Copies the payload described by a raw zero-copy frame into an owned message.
///
/// Validation checks the frame's metadata, not whether its pointer is live or
/// readable. Safe callers must not be able to dereference a forged pointer:
///
/// ```compile_fail
/// use aegis_nerve::ipc::{zero_copy_to_message, ZeroCopyFrame};
/// use aegis_nerve::schema::BinarySchemaId;
///
/// let forged = ZeroCopyFrame {
///     schema_id: BinarySchemaId(1),
///     message_id: 1,
///     session_id: 1,
///     payload_ptr: std::ptr::NonNull::<u8>::dangling().as_ptr(),
///     payload_len: 1,
/// };
/// let _ = zero_copy_to_message(&forged);
/// ```
///
/// Nor may they dereference a frame after its backing message has been dropped:
///
/// ```compile_fail
/// use aegis_nerve::ipc::{message_to_zero_copy, zero_copy_to_message};
/// use aegis_nerve::message::MessageFrame;
///
/// let stale = {
///     let message = MessageFrame::with_identity(1, 1, vec![7]);
///     message_to_zero_copy(&message).unwrap()
/// };
/// let _ = zero_copy_to_message(&stale);
/// ```
///
/// # Safety
///
/// `frame.payload_ptr` must point to `payload_len` initialized, readable bytes
/// in one allocation. The allocation must remain alive and must not be mutated
/// for the duration of this call. Frame validation alone does not establish
/// these pointer invariants.
#[inline]
pub unsafe fn zero_copy_to_message(frame: &ZeroCopyFrame) -> Result<MessageFrame, &'static str> {
    if !frame.is_valid() {
        return Err("invalid zero-copy frame");
    }
    // SAFETY: The caller must uphold the pointer-validity and immutability
    // requirements documented on this function. The resulting slice is copied
    // immediately into an owned `Vec` and does not escape this call.
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
