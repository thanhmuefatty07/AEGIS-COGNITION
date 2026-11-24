use crate::message::{MessageFrame, MessageHeader};
use crate::schema::{BinarySchemaId, NERVE_SCHEMA, SchemaRegistry};
use std::cell::UnsafeCell;
use std::sync::atomic::{AtomicUsize, Ordering};

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

#[repr(C, align(64))]
struct CachePaddedAtomicUsize(AtomicUsize);

#[repr(C, align(64))]
struct MpmcSlot {
    sequence: AtomicUsize,
    frame: UnsafeCell<Option<MessageFrame>>,
}

/// Lock-free bounded multi-producer/multi-consumer (MPMC) frame ring buffer
/// implementing Dmitry Vyukov's sequence-tagged circular queue with 64-byte
/// cache-line isolation between head, tail, and per-slot sequence counters.
pub struct MpmcFrameRing {
    mask: usize,
    slots: Box<[MpmcSlot]>,
    enqueue_pos: CachePaddedAtomicUsize,
    dequeue_pos: CachePaddedAtomicUsize,
}

// SAFETY: `MessageFrame` owns its `Vec<u8>` payload (`Send + Sync`), and all
// concurrent access to each `UnsafeCell<Option<MessageFrame>>` is strictly
// serialized by its slot's `Acquire`/`Release` `sequence` state transitions.
unsafe impl Send for MpmcFrameRing {}
// SAFETY: Multiple threads may concurrently invoke `try_send` and `try_recv`
// because CAS on `enqueue_pos` / `dequeue_pos` grants exclusive access to one
// slot until `sequence.store(..., Ordering::Release)` transfers ownership.
unsafe impl Sync for MpmcFrameRing {}

impl MpmcFrameRing {
    /// Construct a bounded MPMC ring rounded up to the next power of two (`>= 2`).
    pub fn with_capacity(min_capacity: usize) -> Result<Self, &'static str> {
        let cap = min_capacity
            .max(2)
            .checked_next_power_of_two()
            .ok_or("ring capacity overflow")?;
        let mut slots = Vec::with_capacity(cap);
        for idx in 0..cap {
            slots.push(MpmcSlot {
                sequence: AtomicUsize::new(idx),
                frame: UnsafeCell::new(None),
            });
        }
        Ok(Self {
            mask: cap - 1,
            slots: slots.into_boxed_slice(),
            enqueue_pos: CachePaddedAtomicUsize(AtomicUsize::new(0)),
            dequeue_pos: CachePaddedAtomicUsize(AtomicUsize::new(0)),
        })
    }

    #[inline]
    pub fn capacity(&self) -> usize {
        self.slots.len()
    }

    #[inline]
    pub fn len_approx(&self) -> usize {
        let head = self.enqueue_pos.0.load(Ordering::Acquire);
        let tail = self.dequeue_pos.0.load(Ordering::Acquire);
        head.wrapping_sub(tail).min(self.slots.len())
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.len_approx() == 0
    }

    /// Attempt to enqueue a validated `MessageFrame` without blocking.
    /// Returns `Err(frame)` if the frame is invalid or the ring is currently full.
    pub fn try_send(&self, frame: MessageFrame) -> Result<(), MessageFrame> {
        if !frame.is_valid() {
            return Err(frame);
        }
        let mut pos = self.enqueue_pos.0.load(Ordering::Relaxed);
        loop {
            let slot = &self.slots[pos & self.mask];
            let seq = slot.sequence.load(Ordering::Acquire);
            let diff = (seq as isize).wrapping_sub(pos as isize);
            if diff == 0 {
                match self.enqueue_pos.0.compare_exchange_weak(
                    pos,
                    pos.wrapping_add(1),
                    Ordering::Relaxed,
                    Ordering::Relaxed,
                ) {
                    Ok(_) => {
                        // SAFETY: Winning the CAS on `enqueue_pos` when `seq == pos`
                        // guarantees exclusive write ownership of `slot.frame` until
                        // `slot.sequence` is published as `pos + 1` with `Release`.
                        unsafe {
                            *slot.frame.get() = Some(frame);
                        }
                        slot.sequence.store(pos.wrapping_add(1), Ordering::Release);
                        return Ok(());
                    }
                    Err(actual) => pos = actual,
                }
            } else if diff < 0 {
                return Err(frame);
            } else {
                pos = self.enqueue_pos.0.load(Ordering::Relaxed);
            }
            std::hint::spin_loop();
        }
    }

    /// Attempt to dequeue the oldest `MessageFrame` without blocking.
    pub fn try_recv(&self) -> Option<MessageFrame> {
        let mut pos = self.dequeue_pos.0.load(Ordering::Relaxed);
        loop {
            let slot = &self.slots[pos & self.mask];
            let seq = slot.sequence.load(Ordering::Acquire);
            let diff = (seq as isize).wrapping_sub(pos.wrapping_add(1) as isize);
            if diff == 0 {
                match self.dequeue_pos.0.compare_exchange_weak(
                    pos,
                    pos.wrapping_add(1),
                    Ordering::Relaxed,
                    Ordering::Relaxed,
                ) {
                    Ok(_) => {
                        // SAFETY: Winning the CAS on `dequeue_pos` when `seq == pos + 1`
                        // pairs with the producer's `Release` store via `Acquire` load,
                        // granting exclusive ownership of `slot.frame` until `sequence`
                        // is advanced to `pos + mask + 1` with `Release`.
                        let item = unsafe { (*slot.frame.get()).take() };
                        slot.sequence.store(
                            pos.wrapping_add(self.mask).wrapping_add(1),
                            Ordering::Release,
                        );
                        return item;
                    }
                    Err(actual) => pos = actual,
                }
            } else if diff < 0 {
                return None;
            } else {
                pos = self.dequeue_pos.0.load(Ordering::Relaxed);
            }
            std::hint::spin_loop();
        }
    }
}
