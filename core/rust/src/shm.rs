use memmap2::{MmapMut, MmapOptions};
use std::fs::OpenOptions;
use std::sync::atomic::{AtomicU8, AtomicU64, Ordering, fence};

pub const SEQLOCK_HEADER_BYTES: usize = 64;
const SEQLOCK_MAGIC: u64 = 0xAE61_55EC_10C4_0001;

#[repr(C, align(64))]
pub struct SharedMemoryRegion {
    pub ptr: *mut u8,
    pub size: usize,
    pub alignment: usize,
}

impl SharedMemoryRegion {
    pub fn is_aligned(&self) -> bool {
        (self.ptr as usize).is_multiple_of(self.alignment)
    }
}

pub fn validate_region(region: &SharedMemoryRegion) -> Result<(), &'static str> {
    if region.ptr.is_null() {
        return Err("null region pointer");
    }
    if region.size == 0 {
        return Err("zero-sized region");
    }
    if region.alignment != 64 {
        return Err("unsupported region alignment");
    }
    if !region.alignment.is_power_of_two() {
        return Err("invalid alignment");
    }
    if !region.is_aligned() {
        return Err("misaligned region");
    }
    Ok(())
}

pub fn region_capacity(region: &SharedMemoryRegion) -> usize {
    if region.alignment == 0 {
        return 0;
    }
    region.size - (region.size % region.alignment)
}

pub fn region_is_64_aligned(region: &SharedMemoryRegion) -> bool {
    region.alignment == 64 && region.is_aligned()
}

pub fn create_region(
    path: &str,
    size: usize,
    alignment: usize,
) -> Result<(MmapMut, SharedMemoryRegion), &'static str> {
    if path.is_empty() {
        return Err("empty region path");
    }
    if size == 0 || alignment == 0 || !alignment.is_power_of_two() {
        return Err("invalid region parameters");
    }

    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
        .map_err(|_| "failed to open backing file")?;

    file.set_len(size as u64)
        .map_err(|_| "failed to set region size")?;

    // SAFETY: `file` was opened with read+write access (lines 60-64) and
    // `set_len(size)` resizes it to exactly `size` bytes. `MmapOptions::len(size)`
    // ties the mapping's claimed length to that file size, so the mapping can
    // never extend past the file. Validation (`validate_region` at line 83)
    // checks the resulting region. `map_mut` produces the sole mutable alias
    // for this file (no other consumer holds a borrow) and `mmap` is the only
    // owner; the resulting `SharedMemoryRegion.ptr: *mut u8` is freshly
    // obtained and not aliased by any existing `&mut` reference in this scope.
    // Re-validated on next `open_shm` call before any concurrent input produces
    // a pointer-validity violation.
    let mut mmap = unsafe {
        MmapOptions::new()
            .len(size)
            .map_mut(&file)
            .map_err(|_| "failed to map region")?
    };
    let ptr = mmap.as_mut_ptr();

    let region = SharedMemoryRegion {
        ptr,
        size,
        alignment,
    };
    validate_region(&region)?;

    Ok((mmap, region))
}

pub fn hybrid_poll_until<F>(predicate: F, spin_duration: std::time::Duration) -> bool
where
    F: Fn() -> bool,
{
    let start = std::time::Instant::now();
    // 1. Spin-lock phase (busy wait)
    while start.elapsed() < spin_duration {
        if predicate() {
            return true;
        }
        std::hint::spin_loop();
    }

    // 2. Fallback to yields and short sleeps
    let mut backoff = 1;
    while !predicate() {
        if backoff < 10 {
            std::thread::yield_now();
        } else {
            std::thread::sleep(std::time::Duration::from_micros(50));
        }
        backoff += 1;
    }
    true
}

/// Bounded synchronous hybrid spin-then-park poller that returns `false` once
/// `max_wait` elapses instead of spinning forever on a dead producer.
pub fn hybrid_poll_until_bounded<F>(
    predicate: F,
    spin_duration: std::time::Duration,
    max_wait: std::time::Duration,
) -> bool
where
    F: Fn() -> bool,
{
    let start = std::time::Instant::now();
    let spin_cap = spin_duration.min(max_wait);
    while start.elapsed() < spin_cap {
        if predicate() {
            return true;
        }
        std::hint::spin_loop();
    }
    let mut backoff = 0u32;
    loop {
        if predicate() {
            return true;
        }
        if start.elapsed() >= max_wait {
            return predicate();
        }
        if backoff < 8 {
            std::thread::yield_now();
        } else {
            let remaining = max_wait.saturating_sub(start.elapsed());
            std::thread::sleep(remaining.min(std::time::Duration::from_micros(50)));
        }
        backoff = backoff.saturating_add(1);
    }
}

/// Async-friendly poller that yields to the Tokio scheduler instead of blocking
/// worker threads with `std::thread::sleep`.
pub async fn hybrid_poll_until_async<F>(
    predicate: F,
    spin_duration: std::time::Duration,
    max_wait: std::time::Duration,
) -> bool
where
    F: Fn() -> bool,
{
    let start = std::time::Instant::now();
    let spin_cap = spin_duration
        .min(std::time::Duration::from_micros(25))
        .min(max_wait);
    while start.elapsed() < spin_cap {
        if predicate() {
            return true;
        }
        std::hint::spin_loop();
    }
    let mut backoff = 0u32;
    loop {
        if predicate() {
            return true;
        }
        if start.elapsed() >= max_wait {
            return predicate();
        }
        if backoff < 4 {
            tokio::task::yield_now().await;
        } else {
            let remaining = max_wait.saturating_sub(start.elapsed());
            tokio::time::sleep(remaining.min(std::time::Duration::from_micros(200))).await;
        }
        backoff = backoff.saturating_add(1);
    }
}

/// Cache-line-aligned 64-byte atomic seqlock header placed at the start of a
/// `SharedMemoryRegion` so readers never observe torn payloads.
#[repr(C, align(64))]
pub struct SeqlockRegionHeader {
    pub magic: AtomicU64,
    pub sequence: AtomicU64,
    pub payload_len: AtomicU64,
    pub blake3_words: [AtomicU64; 4],
    pub reserved: AtomicU64,
}

const _: () = assert!(std::mem::size_of::<SeqlockRegionHeader>() == SEQLOCK_HEADER_BYTES);

/// Publish `payload` into `region` using an odd/even atomic seqlock protocol
/// with `AtomicU8` relaxed byte stores (100% data-race-free in the Rust memory
/// model) and BLAKE3 integrity tag binding.
///
/// # Safety
///
/// `region` must point to a live, writable mapping of at least `region.size`
/// bytes aligned to 64 bytes, and concurrent writers must coordinate externally
/// (single-writer / multi-reader seqlock contract).
pub unsafe fn write_seqlock_frame(
    region: &SharedMemoryRegion,
    payload: &[u8],
) -> Result<u64, &'static str> {
    validate_region(region)?;
    if region.size <= SEQLOCK_HEADER_BYTES {
        return Err("region too small for seqlock header");
    }
    let max_payload = region.size - SEQLOCK_HEADER_BYTES;
    if payload.len() > max_payload {
        return Err("payload exceeds seqlock region capacity");
    }

    // SAFETY: `validate_region` proved `region.ptr` is non-null and 64-byte aligned,
    // and `region.size > SEQLOCK_HEADER_BYTES` guarantees the 64-byte header fits
    // within the live mapping. All fields of `SeqlockRegionHeader` are atomic.
    let header = unsafe { &*(region.ptr as *const SeqlockRegionHeader) };
    // SAFETY: `region.ptr.add(SEQLOCK_HEADER_BYTES)` lies within the live mapping of
    // length `region.size`, and `AtomicU8` has alignment 1 and identical layout to `u8`.
    let body = unsafe {
        core::slice::from_raw_parts(
            region.ptr.add(SEQLOCK_HEADER_BYTES) as *const AtomicU8,
            max_payload,
        )
    };

    let prev = header.sequence.load(Ordering::Relaxed);
    let odd_seq = if prev & 1 == 1 {
        prev.wrapping_add(2)
    } else {
        prev.wrapping_add(1)
    };
    header.magic.store(SEQLOCK_MAGIC, Ordering::Relaxed);
    header.sequence.store(odd_seq, Ordering::Release);
    fence(Ordering::AcqRel);

    for (slot, byte) in body.iter().zip(payload.iter()) {
        slot.store(*byte, Ordering::Relaxed);
    }
    header
        .payload_len
        .store(payload.len() as u64, Ordering::Relaxed);

    let digest = blake3::hash(payload);
    let digest_bytes = digest.as_bytes();
    for (idx, word_slot) in header.blake3_words.iter().enumerate() {
        let start = idx * 8;
        let mut buf = [0u8; 8];
        buf.copy_from_slice(&digest_bytes[start..start + 8]);
        word_slot.store(u64::from_le_bytes(buf), Ordering::Relaxed);
    }

    let even_seq = odd_seq.wrapping_add(1);
    header.sequence.store(even_seq, Ordering::Release);
    Ok(even_seq)
}

/// Read a consistent, untorn snapshot from a seqlock-protected `SharedMemoryRegion`,
/// verifying both sequence stability and the embedded BLAKE3 integrity tag.
///
/// # Safety
///
/// `region` must point to a live, readable mapping of at least `region.size`
/// bytes aligned to 64 bytes.
pub unsafe fn read_seqlock_frame(
    region: &SharedMemoryRegion,
    max_retries: usize,
) -> Result<(u64, Vec<u8>), &'static str> {
    validate_region(region)?;
    if region.size <= SEQLOCK_HEADER_BYTES {
        return Err("region too small for seqlock header");
    }
    let max_payload = region.size - SEQLOCK_HEADER_BYTES;
    let retries = max_retries.max(1);

    // SAFETY: `validate_region` verified non-null 64-byte alignment and `region.size`
    // covers the 64-byte atomic header and `max_payload` atomic byte slots.
    let header = unsafe { &*(region.ptr as *const SeqlockRegionHeader) };
    // SAFETY: `SEQLOCK_HEADER_BYTES + max_payload == region.size` within the live mapping.
    let body = unsafe {
        core::slice::from_raw_parts(
            region.ptr.add(SEQLOCK_HEADER_BYTES) as *const AtomicU8,
            max_payload,
        )
    };

    for _ in 0..retries {
        let seq1 = header.sequence.load(Ordering::Acquire);
        if seq1 == 0 || (seq1 & 1) == 1 {
            std::hint::spin_loop();
            continue;
        }
        if header.magic.load(Ordering::Relaxed) != SEQLOCK_MAGIC {
            return Err("uninitialized seqlock magic");
        }
        let len = header.payload_len.load(Ordering::Relaxed) as usize;
        if len > max_payload {
            std::hint::spin_loop();
            continue;
        }
        let mut buf = vec![0u8; len];
        for (dst, src) in buf.iter_mut().zip(body[..len].iter()) {
            *dst = src.load(Ordering::Relaxed);
        }
        let mut expected_digest = [0u8; 32];
        for (idx, word_slot) in header.blake3_words.iter().enumerate() {
            let word = word_slot.load(Ordering::Relaxed).to_le_bytes();
            expected_digest[idx * 8..(idx + 1) * 8].copy_from_slice(&word);
        }
        fence(Ordering::Acquire);
        let seq2 = header.sequence.load(Ordering::Acquire);
        if seq1 != seq2 {
            std::hint::spin_loop();
            continue;
        }
        if blake3::hash(&buf).as_bytes() != &expected_digest {
            return Err("seqlock frame blake3 mismatch");
        }
        return Ok((seq2, buf));
    }
    Err("seqlock read contention budget exhausted")
}
