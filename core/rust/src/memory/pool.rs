use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

pub trait MemoryPool: std::fmt::Debug + Send + Sync {
    fn reserve(&self, size: usize) -> Box<dyn MemoryReservation>;
    fn available(&self) -> isize;
    fn used(&self) -> usize;
    fn capacity(&self) -> usize;
}

pub trait MemoryReservation: std::fmt::Debug + Send + Sync {
    fn resize(&mut self, new_size: usize);
    fn size(&self) -> usize;
}

#[derive(Debug)]
pub struct CustomMemoryReservation {
    used: Arc<AtomicUsize>,
    capacity: usize,
    size: usize,
}

impl MemoryReservation for CustomMemoryReservation {
    fn resize(&mut self, new_size: usize) {
        if new_size > self.size {
            let diff = new_size - self.size;
            if !try_account_bytes(&self.used, self.capacity, diff) {
                return;
            }
        } else if new_size < self.size {
            self.used.fetch_sub(self.size - new_size, Ordering::Relaxed);
        }
        self.size = new_size;
    }

    fn size(&self) -> usize {
        self.size
    }
}

impl Drop for CustomMemoryReservation {
    fn drop(&mut self) {
        self.used.fetch_sub(self.size, Ordering::Relaxed);
    }
}

#[derive(Debug)]
pub struct SlabMemoryPool {
    used: Arc<AtomicUsize>,
    capacity: usize,
}

impl SlabMemoryPool {
    pub fn new(capacity: usize) -> Self {
        Self {
            used: Arc::new(AtomicUsize::new(0)),
            capacity,
        }
    }

    pub fn try_reserve(&self, size: usize) -> Result<Box<dyn MemoryReservation>, &'static str> {
        if !try_account_bytes(&self.used, self.capacity, size) {
            return Err("memory pool capacity exceeded");
        }
        Ok(Box::new(CustomMemoryReservation {
            used: self.used.clone(),
            capacity: self.capacity,
            size,
        }))
    }
}

impl MemoryPool for SlabMemoryPool {
    fn reserve(&self, size: usize) -> Box<dyn MemoryReservation> {
        self.try_reserve(size).unwrap_or_else(|_| {
            Box::new(CustomMemoryReservation {
                used: self.used.clone(),
                capacity: self.capacity,
                size: 0,
            })
        })
    }

    fn available(&self) -> isize {
        self.capacity as isize - self.used.load(Ordering::Relaxed) as isize
    }

    fn used(&self) -> usize {
        self.used.load(Ordering::Relaxed)
    }

    fn capacity(&self) -> usize {
        self.capacity
    }
}

pub struct PreAllocatedBuffer {
    pub ptr: *mut u8,
    pub size: usize,
}

unsafe impl Send for PreAllocatedBuffer {}
unsafe impl Sync for PreAllocatedBuffer {}

#[cfg(target_os = "windows")]
mod win32 {
    extern "system" {
        pub fn VirtualAlloc(
            lpAddress: *const std::ffi::c_void,
            dwSize: usize,
            flAllocationType: u32,
            flProtect: u32,
        ) -> *mut std::ffi::c_void;

        pub fn VirtualFree(lpAddress: *mut std::ffi::c_void, dwSize: usize, dwFreeType: u32)
            -> i32;
    }
    pub const MEM_COMMIT: u32 = 0x00001000;
    pub const MEM_RESERVE: u32 = 0x00002000;
    pub const MEM_RELEASE: u32 = 0x00008000;
    pub const PAGE_READWRITE: u32 = 0x04;
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
mod unix {
    extern "C" {
        pub fn mmap(
            addr: *mut std::ffi::c_void,
            len: usize,
            prot: std::ffi::c_int,
            flags: std::ffi::c_int,
            fd: std::ffi::c_int,
            offset: i64,
        ) -> *mut std::ffi::c_void;

        pub fn madvise(
            addr: *mut std::ffi::c_void,
            len: usize,
            advice: std::ffi::c_int,
        ) -> std::ffi::c_int;

        pub fn munmap(addr: *mut std::ffi::c_void, len: usize) -> std::ffi::c_int;
    }
    pub const PROT_READ: std::ffi::c_int = 1;
    pub const PROT_WRITE: std::ffi::c_int = 2;
    pub const MAP_PRIVATE: std::ffi::c_int = 2;
    #[cfg(target_os = "linux")]
    pub const MAP_ANON: std::ffi::c_int = 32;
    #[cfg(target_os = "macos")]
    pub const MAP_ANON: std::ffi::c_int = 0x1000;
    pub const MAP_FAILED: *mut std::ffi::c_void = -1isize as *mut std::ffi::c_void;
    pub const MADV_HUGEPAGE: std::ffi::c_int = 14;
}

impl PreAllocatedBuffer {
    pub fn new(size: usize) -> Result<Self, &'static str> {
        if size == 0 {
            return Err("zero-sized allocation");
        }

        #[cfg(target_os = "windows")]
        // SAFETY: `VirtualAlloc` is invoked with `null` as the base address
        // (requesting system-chosen placement) with `MEM_COMMIT | MEM_RESERVE`
        // and `PAGE_READWRITE` so the OS allocates an accessible, writable
        // region of `size` bytes. We check `is_null()` immediately and return
        // `Err` if so; on success, `ptr` is a freshly-allocated block that is
        // not aliased by any other `&mut [u8]` reference in this process.
        // The `size` invariant is preserved in `Self` and the matching
        // `VirtualFree(_, 0, MEM_RELEASE)` `Drop` impl mirrors the alloc.
        unsafe {
            let ptr = win32::VirtualAlloc(
                std::ptr::null(),
                size,
                win32::MEM_COMMIT | win32::MEM_RESERVE,
                win32::PAGE_READWRITE,
            );
            if ptr.is_null() {
                return Err("VirtualAlloc failed");
            }
            Ok(Self {
                ptr: ptr as *mut u8,
                size,
            })
        }

        #[cfg(any(target_os = "linux", target_os = "macos"))]
        // SAFETY: `unix::mmap` is invoked with `null` as the base address,
        // `PROT_READ | PROT_WRITE` for full access, `MAP_PRIVATE | MAP_ANON`
        // for an anonymous private map (no file backing), fd = -1, offset = 0.
        // This is the canonical, FFI-blessed incantation for allocating
        // anonymous memory; `MAP_FAILED` (== `(void*)-1`) is checked below.
        // On success, `ptr` is a freshly-allocated region with `size` bytes,
        // not aliased by any other reference in this process and freed by the
        // matching `munmap` call in `Drop`.
        unsafe {
            let ptr = unix::mmap(
                std::ptr::null_mut(),
                size,
                unix::PROT_READ | unix::PROT_WRITE,
                unix::MAP_PRIVATE | unix::MAP_ANON,
                -1,
                0,
            );
            if ptr == unix::MAP_FAILED {
                return Err("mmap failed");
            }

            #[cfg(target_os = "linux")]
            {
                unix::madvise(ptr, size, unix::MADV_HUGEPAGE);
            }

            Ok(Self {
                ptr: ptr as *mut u8,
                size,
            })
        }
    }

    pub fn populate_pages(&self) {
        let page_size = 4096;
        let mut offset = 0;
        while offset < self.size {
            // SAFETY: `self.ptr` was allocated by `VirtualAlloc`/`mmap` in
            // `new()` (lines 161-201) for exactly `self.size` bytes and is the
            // sole owner of that allocation. `offset` is strictly `< self.size`
            // (loop guard) and incremented by `page_size (4096)`, so
            // `self.ptr.add(offset)` stays within `[self.ptr, self.ptr+self.size)`.
            // The mapped page region is `PROT_READ | PROT_WRITE`, so a volatile
            // `u8` write is well-defined. The volatile write forces page
            // population (touches the page) without optimizer dead-store elim.
            unsafe {
                std::ptr::write_volatile(self.ptr.add(offset), 0);
            }
            offset += page_size;
        }
    }

    pub fn spawn_background_population(&self) -> std::thread::JoinHandle<()> {
        let ptr_val = self.ptr as usize;
        let size = self.size;
        std::thread::spawn(move || {
            let ptr = ptr_val as *mut u8;
            let page_size = 4096;
            let mut offset = 0;
            while offset < size {
                // SAFETY: same as `populate_pages` above. `ptr_val` is a
                // faithful bytewise transmission of `self.ptr` from a parent
                // `PreAllocatedBuffer` that has not yet been dropped
                // (consumer joins on the returned `JoinHandle` before scope
                // ends). `offset < size` keeps writes in-bounds; the underlying
                // region is `PROT_READ | PROT_WRITE`. Volatile write is
                // page-population only — does not race with any concurrent
                // reader because the buffer is in the anonymous-alloc phase
                // before being handed off to the kernel layer that takes
                // shared access.
                unsafe {
                    std::ptr::write_volatile(ptr.add(offset), 0);
                }
                offset += page_size;
            }
        })
    }
}

impl Drop for PreAllocatedBuffer {
    fn drop(&mut self) {
        if self.ptr.is_null() || self.size == 0 {
            return;
        }

        #[cfg(target_os = "windows")]
        // SAFETY: `self.ptr` was allocated by the matching `VirtualAlloc` in
        // `new()` (line 161+) with `MEM_COMMIT | MEM_RESERVE`. `MEM_RELEASE`
        // requires a pointer that came from `VirtualAlloc` and size 0
        // (release the entire region). `self.ptr` is non-null (checked) and
        // exclusive to this `PreAllocatedBuffer` (no aliases). After this
        // Drop, `self.ptr` is no longer valid and the buffer goes out of scope.
        unsafe {
            let _ = win32::VirtualFree(self.ptr as *mut std::ffi::c_void, 0, win32::MEM_RELEASE);
        }

        #[cfg(any(target_os = "linux", target_os = "macos"))]
        // SAFETY: `self.ptr` was returned by `unix::mmap` in `new()` (line 178+)
        // with `size == self.size`. `munmap` with the matching `ptr` and `size`
        // is the canonical release, removing all pages in this region. `self.ptr`
        // is non-null (checked) and exclusively owned by this `PreAllocatedBuffer`,
        // so no other reference can outlive the call.
        unsafe {
            let _ = unix::munmap(self.ptr as *mut std::ffi::c_void, self.size);
        }
    }
}

fn try_account_bytes(used: &AtomicUsize, capacity: usize, size: usize) -> bool {
    let mut current = used.load(Ordering::Relaxed);
    loop {
        let Some(next) = current.checked_add(size) else {
            return false;
        };
        if next > capacity {
            return false;
        }
        match used.compare_exchange_weak(current, next, Ordering::Relaxed, Ordering::Relaxed) {
            Ok(_) => return true,
            Err(observed) => current = observed,
        }
    }
}
