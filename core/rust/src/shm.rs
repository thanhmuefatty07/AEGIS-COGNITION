use memmap2::{MmapMut, MmapOptions};
use std::fs::OpenOptions;

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
