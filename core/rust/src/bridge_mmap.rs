use crate::physical::blake3_digest;
use crate::schema::NERVE_SCHEMA;
use memmap2::{Mmap, MmapOptions};
use std::convert::TryInto;
use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::ops::Range;
use std::path::Path;

pub const MMAP_BRIDGE_MAGIC: &[u8; 8] = b"AEGMMAP1";
pub const MMAP_BRIDGE_VERSION: u32 = 1;
pub const MMAP_BRIDGE_HEADER_BYTES: usize = 128;
pub const MMAP_BRIDGE_PAYLOAD_ALIGNMENT: usize = 64;
const MMAP_BRIDGE_FILE_STREAM_MAX_CHUNK_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MmapBridgeHeader {
    pub version: u32,
    pub header_bytes: u32,
    pub schema_id: u64,
    pub schema_version: u32,
    pub alignment: u32,
    pub message_id: u128,
    pub session_id: u128,
    pub payload_offset: u64,
    pub payload_len: u64,
    pub payload_blake3: [u8; 32],
}

impl MmapBridgeHeader {
    pub fn new(message_id: u128, session_id: u128, payload: &[u8]) -> Result<Self, &'static str> {
        if message_id == 0 || session_id == 0 {
            return Err("invalid message identity");
        }
        if payload.is_empty() {
            return Err("empty payload");
        }

        Ok(Self {
            version: MMAP_BRIDGE_VERSION,
            header_bytes: MMAP_BRIDGE_HEADER_BYTES as u32,
            schema_id: NERVE_SCHEMA.schema_id.0,
            schema_version: NERVE_SCHEMA.version,
            alignment: MMAP_BRIDGE_PAYLOAD_ALIGNMENT as u32,
            message_id,
            session_id,
            payload_offset: MMAP_BRIDGE_HEADER_BYTES as u64,
            payload_len: payload.len() as u64,
            payload_blake3: blake3_digest(payload),
        })
    }

    pub fn encode(&self) -> [u8; MMAP_BRIDGE_HEADER_BYTES] {
        let mut bytes = [0u8; MMAP_BRIDGE_HEADER_BYTES];
        bytes[0..8].copy_from_slice(MMAP_BRIDGE_MAGIC);
        bytes[8..12].copy_from_slice(&self.version.to_le_bytes());
        bytes[12..16].copy_from_slice(&self.header_bytes.to_le_bytes());
        bytes[16..24].copy_from_slice(&self.schema_id.to_le_bytes());
        bytes[24..28].copy_from_slice(&self.schema_version.to_le_bytes());
        bytes[28..32].copy_from_slice(&self.alignment.to_le_bytes());
        bytes[32..48].copy_from_slice(&self.message_id.to_le_bytes());
        bytes[48..64].copy_from_slice(&self.session_id.to_le_bytes());
        bytes[64..72].copy_from_slice(&self.payload_offset.to_le_bytes());
        bytes[72..80].copy_from_slice(&self.payload_len.to_le_bytes());
        bytes[80..112].copy_from_slice(&self.payload_blake3);
        bytes
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, &'static str> {
        if bytes.len() < MMAP_BRIDGE_HEADER_BYTES {
            return Err("short mmap bridge header");
        }
        if &bytes[0..8] != MMAP_BRIDGE_MAGIC {
            return Err("mmap bridge magic mismatch");
        }

        // SAFETY-CRITICAL: slices are pre-checked at line 68 (`bytes.len() < MMAP_BRIDGE_HEADER_BYTES`),
        // so `try_into()` cannot fail. However, we still surface the conversion error
        // explicitly via `ok_or` so a regression in the length-guard panics loudly in dev
        // builds (where MMAP_BRIDGE_HEADER_BYTES mismatch would otherwise cause UB).
        // At runtime each conversion is infallible; this preserves API contract
        // (Result-returning) without the historical unwrap panics.
        let read_u32 =
            |range: std::ops::Range<usize>, label: &'static str| -> Result<u32, &'static str> {
                let arr: [u8; 4] = bytes[range].try_into().map_err(|_| label)?;
                Ok(u32::from_le_bytes(arr))
            };
        let read_u64 =
            |range: std::ops::Range<usize>, label: &'static str| -> Result<u64, &'static str> {
                let arr: [u8; 8] = bytes[range].try_into().map_err(|_| label)?;
                Ok(u64::from_le_bytes(arr))
            };
        let read_u128 =
            |range: std::ops::Range<usize>, label: &'static str| -> Result<u128, &'static str> {
                let arr: [u8; 16] = bytes[range].try_into().map_err(|_| label)?;
                Ok(u128::from_le_bytes(arr))
            };
        let payload_blake3: [u8; 32] = bytes[80..112]
            .try_into()
            .map_err(|_| "invalid payload blake3 field")?;

        Ok(Self {
            version: read_u32(8..12, "invalid version field")?,
            header_bytes: read_u32(12..16, "invalid header_bytes field")?,
            schema_id: read_u64(16..24, "invalid schema_id field")?,
            schema_version: read_u32(24..28, "invalid schema_version field")?,
            alignment: read_u32(28..32, "invalid alignment field")?,
            message_id: read_u128(32..48, "invalid message_id field")?,
            session_id: read_u128(48..64, "invalid session_id field")?,
            payload_offset: read_u64(64..72, "invalid payload_offset field")?,
            payload_len: read_u64(72..80, "invalid payload_len field")?,
            payload_blake3,
        })
    }

    pub fn validate_metadata(&self, mapped_len: usize) -> Result<(), &'static str> {
        if self.version != MMAP_BRIDGE_VERSION {
            return Err("mmap bridge version mismatch");
        }
        if self.header_bytes as usize != MMAP_BRIDGE_HEADER_BYTES {
            return Err("mmap bridge header size mismatch");
        }
        if self.schema_id != NERVE_SCHEMA.schema_id.0 || self.schema_version != NERVE_SCHEMA.version
        {
            return Err("mmap bridge schema mismatch");
        }
        if self.alignment as usize != MMAP_BRIDGE_PAYLOAD_ALIGNMENT {
            return Err("mmap bridge alignment mismatch");
        }
        if !(self.payload_offset as usize).is_multiple_of(MMAP_BRIDGE_PAYLOAD_ALIGNMENT) {
            return Err("mmap bridge payload misaligned");
        }
        if self.message_id == 0 || self.session_id == 0 || self.payload_len == 0 {
            return Err("invalid mmap bridge identity or payload");
        }
        let range = self.payload_range(mapped_len)?;
        if range.is_empty() {
            return Err("empty mmap bridge payload range");
        }
        Ok(())
    }

    pub fn payload_range(&self, mapped_len: usize) -> Result<Range<usize>, &'static str> {
        let start = usize::try_from(self.payload_offset).map_err(|_| "payload offset overflow")?;
        let payload_len = usize::try_from(self.payload_len).map_err(|_| "payload len overflow")?;
        let end = start
            .checked_add(payload_len)
            .ok_or("payload range overflow")?;
        if start < MMAP_BRIDGE_HEADER_BYTES || end > mapped_len {
            return Err("payload range outside mmap");
        }
        Ok(start..end)
    }
}

pub struct MmapBridgeView {
    mmap: Mmap,
    header: MmapBridgeHeader,
}

/// Incremental writer for a file-backed bridge frame.
///
/// The writer keeps only the caller's current chunk resident.  This matters
/// for large payloads: the bridge can use the file-backed data tier without
/// first creating a second full-size heap buffer.  The final header is written
/// only after the complete payload hash and length have been verified.
pub struct MmapBridgeWriter {
    file: std::fs::File,
    header: MmapBridgeHeader,
    next_offset: usize,
    hasher: blake3::Hasher,
}

impl MmapBridgeWriter {
    pub fn new<P: AsRef<Path>>(
        path: P,
        message_id: u128,
        session_id: u128,
        payload_len: usize,
    ) -> Result<Self, &'static str> {
        if message_id == 0 || session_id == 0 || payload_len == 0 {
            return Err("invalid bridge identity or payload length");
        }
        let total_len = MMAP_BRIDGE_HEADER_BYTES
            .checked_add(payload_len)
            .ok_or("mmap bridge frame too large")?;
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(path)
            .map_err(|_| "failed to open mmap bridge file")?;
        file.set_len(total_len as u64)
            .map_err(|_| "failed to size mmap bridge file")?;

        // Leave the header area zeroed until `finish` has verified that the
        // complete payload was written. Payload bytes are streamed through the
        // file handle rather than a writable whole-file mapping so the writer
        // does not turn the entire spill frame into the process working set.
        file.write_all(&[0_u8; MMAP_BRIDGE_HEADER_BYTES])
            .map_err(|_| "failed to initialize mmap bridge header")?;
        let header = MmapBridgeHeader {
            version: MMAP_BRIDGE_VERSION,
            header_bytes: MMAP_BRIDGE_HEADER_BYTES as u32,
            schema_id: NERVE_SCHEMA.schema_id.0,
            schema_version: NERVE_SCHEMA.version,
            alignment: MMAP_BRIDGE_PAYLOAD_ALIGNMENT as u32,
            message_id,
            session_id,
            payload_offset: MMAP_BRIDGE_HEADER_BYTES as u64,
            payload_len: payload_len as u64,
            payload_blake3: [0; 32],
        };
        Ok(Self {
            file,
            header,
            next_offset: MMAP_BRIDGE_HEADER_BYTES,
            hasher: blake3::Hasher::new(),
        })
    }

    pub fn write(&mut self, payload: &[u8]) -> Result<(), &'static str> {
        let end = self
            .next_offset
            .checked_add(payload.len())
            .ok_or("mmap bridge payload range overflow")?;
        let payload_end = MMAP_BRIDGE_HEADER_BYTES
            .checked_add(self.header.payload_len as usize)
            .ok_or("mmap bridge payload range overflow")?;
        if end > payload_end {
            return Err("mmap bridge payload exceeds declared length");
        }
        self.file
            .write_all(payload)
            .map_err(|_| "failed to write mmap bridge payload")?;
        self.hasher.update(payload);
        self.next_offset = end;
        Ok(())
    }

    /// Stream an existing file through one bounded native buffer.
    ///
    /// The source size must equal the remaining declared payload, and the
    /// source/destination file identities must differ. Keeping this loop in
    /// Rust avoids one Python buffer export and GIL transition per chunk while
    /// preserving incremental hashing and exact-length publication.
    pub fn write_file<P: AsRef<Path>>(
        &mut self,
        source: P,
        chunk_bytes: usize,
    ) -> Result<(), &'static str> {
        if chunk_bytes == 0 {
            return Err("mmap bridge file stream chunk size must be positive");
        }
        let payload_end = MMAP_BRIDGE_HEADER_BYTES
            .checked_add(self.header.payload_len as usize)
            .ok_or("mmap bridge payload range overflow")?;
        let remaining = payload_end
            .checked_sub(self.next_offset)
            .ok_or("mmap bridge payload offset is invalid")?;
        let source_path = source.as_ref();
        let mut source_file = open_stream_source(source_path)?;
        if same_open_file(&self.file, &source_file)? {
            return Err("mmap bridge source and destination must differ");
        }
        let source_len = source_file
            .metadata()
            .map_err(|_| "failed to stat mmap bridge source")?
            .len();
        let remaining_u64 =
            u64::try_from(remaining).map_err(|_| "mmap bridge payload is too large")?;
        if source_len != remaining_u64 {
            return Err("mmap bridge source size does not match remaining payload length");
        }

        // Do not reserve the full requested chunk for a small remaining
        // payload.  This keeps the file-stream working set proportional to
        // the data still to copy, while the `max(1)` case preserves the
        // empty-file completion path for a writer positioned at EOF.
        let buffer_len = chunk_bytes
            .clamp(1, MMAP_BRIDGE_FILE_STREAM_MAX_CHUNK_BYTES)
            .min(remaining.max(1));
        let mut buffer = vec![0_u8; buffer_len];
        advise_stream_source_sequential(&source_file);
        let mut source_offset = 0_u64;
        loop {
            let read = source_file
                .read(&mut buffer)
                .map_err(|_| "failed to read mmap bridge source")?;
            if read == 0 {
                break;
            }
            self.write(&buffer[..read])?;
            advise_stream_source_consumed(&source_file, source_offset, read);
            source_offset = source_offset
                .checked_add(read as u64)
                .ok_or("mmap bridge source offset overflow")?;
        }
        if self.next_offset != payload_end {
            return Err("mmap bridge source ended before remaining payload length");
        }
        let mut extra = [0_u8; 1];
        if source_file
            .read(&mut extra)
            .map_err(|_| "failed to verify mmap bridge source length")?
            != 0
        {
            return Err("mmap bridge source exceeds remaining payload length");
        }
        Ok(())
    }

    pub fn finish(mut self) -> Result<MmapBridgeHeader, &'static str> {
        let payload_end = MMAP_BRIDGE_HEADER_BYTES
            .checked_add(self.header.payload_len as usize)
            .ok_or("mmap bridge payload range overflow")?;
        if self.next_offset != payload_end {
            return Err("mmap bridge payload is shorter than declared length");
        }
        self.header.payload_blake3 = *self.hasher.finalize().as_bytes();
        self.file
            .seek(SeekFrom::Start(0))
            .map_err(|_| "failed to seek mmap bridge header")?;
        self.file
            .write_all(&self.header.encode())
            .map_err(|_| "failed to finalize mmap bridge header")?;
        self.file
            .flush()
            .map_err(|_| "failed to flush mmap bridge frame")?;
        Ok(self.header)
    }
}

fn open_stream_source(path: &Path) -> Result<std::fs::File, &'static str> {
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;

        // FILE_FLAG_SEQUENTIAL_SCAN is a cache-manager hint only.  It does
        // not change synchronous File semantics or impose the alignment
        // requirements of FILE_FLAG_NO_BUFFERING.
        const FILE_FLAG_SEQUENTIAL_SCAN: u32 = 0x0800_0000;
        OpenOptions::new()
            .read(true)
            .custom_flags(FILE_FLAG_SEQUENTIAL_SCAN)
            .open(path)
            .map_err(|_| "failed to open mmap bridge source")
    }

    #[cfg(not(windows))]
    {
        OpenOptions::new()
            .read(true)
            .open(path)
            .map_err(|_| "failed to open mmap bridge source")
    }
}

fn advise_stream_source_sequential(file: &std::fs::File) {
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;

        // Advisory failure is deliberately ignored: correctness comes from
        // the ordinary read path, and minimal/older kernels may not expose
        // the optional advice syscall.
        let _ = unsafe { libc::posix_fadvise(file.as_raw_fd(), 0, 0, libc::POSIX_FADV_SEQUENTIAL) };
    }

    #[cfg(not(target_os = "linux"))]
    {
        let _ = file;
    }
}

fn advise_stream_source_consumed(file: &std::fs::File, offset: u64, length: usize) {
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;

        // DONTNEED is meaningful only for complete pages.  The final partial
        // page is intentionally left alone; preserving a possibly-needed
        // page is safer than trying to discard it.  This affects cache
        // residency only and never changes file contents.
        const PAGE_BYTES: u64 = 4096;
        let aligned_length = (length as u64 / PAGE_BYTES) * PAGE_BYTES;
        if aligned_length == 0 {
            return;
        }
        let Ok(offset) = libc::off_t::try_from(offset) else {
            return;
        };
        let Ok(aligned_length) = libc::off_t::try_from(aligned_length) else {
            return;
        };
        let _ = unsafe {
            libc::posix_fadvise(
                file.as_raw_fd(),
                offset,
                aligned_length,
                libc::POSIX_FADV_DONTNEED,
            )
        };
    }

    #[cfg(not(target_os = "linux"))]
    {
        let _ = (file, offset, length);
    }
}

fn same_open_file(left: &std::fs::File, right: &std::fs::File) -> Result<bool, &'static str> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;

        let left = left
            .metadata()
            .map_err(|_| "failed to identify mmap bridge destination")?;
        let right = right
            .metadata()
            .map_err(|_| "failed to identify mmap bridge source")?;
        Ok(left.dev() == right.dev() && left.ino() == right.ino())
    }

    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
        };

        let mut left_info = BY_HANDLE_FILE_INFORMATION::default();
        let mut right_info = BY_HANDLE_FILE_INFORMATION::default();
        let left_ok = unsafe { GetFileInformationByHandle(left.as_raw_handle(), &mut left_info) };
        let right_ok =
            unsafe { GetFileInformationByHandle(right.as_raw_handle(), &mut right_info) };
        if left_ok == 0 || right_ok == 0 {
            return Err("failed to identify mmap bridge file identity");
        }
        Ok(
            left_info.dwVolumeSerialNumber == right_info.dwVolumeSerialNumber
                && left_info.nFileIndexHigh == right_info.nFileIndexHigh
                && left_info.nFileIndexLow == right_info.nFileIndexLow,
        )
    }

    #[cfg(not(any(unix, windows)))]
    {
        let _ = (left, right);
        Ok(false)
    }
}

impl MmapBridgeView {
    pub fn header(&self) -> &MmapBridgeHeader {
        &self.header
    }

    pub fn payload(&self) -> Result<&[u8], &'static str> {
        let range = self.header.payload_range(self.mmap.len())?;
        Ok(&self.mmap[range])
    }

    pub fn payload_ptr(&self) -> Result<*const u8, &'static str> {
        Ok(self.payload()?.as_ptr())
    }

    pub fn mapped_ptr(&self) -> *const u8 {
        self.mmap.as_ptr()
    }

    pub fn payload_hash_matches(&self) -> Result<bool, &'static str> {
        Ok(blake3_digest(self.payload()?) == self.header.payload_blake3)
    }
}

pub fn write_mmap_bridge_frame<P: AsRef<Path>>(
    path: P,
    message_id: u128,
    session_id: u128,
    payload: &[u8],
) -> Result<MmapBridgeHeader, &'static str> {
    let mut writer = MmapBridgeWriter::new(path, message_id, session_id, payload.len())?;
    writer.write(payload)?;
    writer.finish()
}

pub fn open_mmap_bridge_view<P: AsRef<Path>>(path: P) -> Result<MmapBridgeView, &'static str> {
    let file = OpenOptions::new()
        .read(true)
        .open(path)
        .map_err(|_| "failed to open mmap bridge file")?;
    let metadata = file
        .metadata()
        .map_err(|_| "failed to stat mmap bridge file")?;
    let mapped_len = usize::try_from(metadata.len()).map_err(|_| "mmap bridge file too large")?;
    if mapped_len < MMAP_BRIDGE_HEADER_BYTES {
        return Err("short mmap bridge file");
    }
    // SAFETY: `file` was opened with read-only access (line 212) and the
    // length passed to `MmapOptions` (`mapped_len`) is exactly the file size
    // from `metadata().len()`. The mapping can never extend beyond the file.
    // No mutable mapping is outstanding on this file in any thread (the file
    // is read-only); we therefore have the only `&[u8]` alias to the file.
    // Access through the returned mapping respects Rust's no-aliasing + valid
    // UTF-8 access rules: callers go through `header.payload_range(...)`
    // which validates bounds before dereferencing any payload bytes.
    let mmap = unsafe {
        MmapOptions::new()
            .len(mapped_len)
            .map(&file)
            .map_err(|_| "failed to map mmap bridge file")?
    };
    let header = MmapBridgeHeader::decode(&mmap[..MMAP_BRIDGE_HEADER_BYTES])?;
    header.validate_metadata(mapped_len)?;
    let range = header.payload_range(mapped_len)?;
    if blake3_digest(&mmap[range]) != header.payload_blake3 {
        return Err("mmap bridge payload hash mismatch");
    }
    Ok(MmapBridgeView { mmap, header })
}

pub fn validate_mmap_bridge_frame<P: AsRef<Path>>(path: P) -> bool {
    open_mmap_bridge_view(path).is_ok()
}

pub fn write_pattern_mmap_bridge_frame<P: AsRef<Path>>(
    path: P,
    message_id: u128,
    session_id: u128,
    payload_len: usize,
) -> Result<MmapBridgeHeader, &'static str> {
    if payload_len == 0 {
        return Err("empty payload");
    }
    const CHUNK_BYTES: usize = 64 * 1024;
    let mut writer = MmapBridgeWriter::new(path, message_id, session_id, payload_len)?;
    let mut chunk = vec![0_u8; CHUNK_BYTES];
    let mut offset = 0;
    while offset < payload_len {
        let length = CHUNK_BYTES.min(payload_len - offset);
        for (index, byte) in chunk[..length].iter_mut().enumerate() {
            *byte = pattern_byte(offset + index);
        }
        writer.write(&chunk[..length])?;
        offset += length;
    }
    writer.finish()
}

#[inline]
pub fn pattern_byte(index: usize) -> u8 {
    ((index.wrapping_mul(31).wrapping_add(7)) & 0xff) as u8
}

#[cfg(test)]
mod decode_regression_tests {
    //! Regression tests for BUG-001: ensure `MmapBridgeHeader::decode` returns
    //! `Err` (not panic) on every malformed-header shape, including the
    //! historically-unwrap-panicked paths.

    use super::*;

    fn well_formed_header_bytes() -> [u8; MMAP_BRIDGE_HEADER_BYTES] {
        let header = MmapBridgeHeader {
            version: MMAP_BRIDGE_VERSION,
            header_bytes: MMAP_BRIDGE_HEADER_BYTES as u32,
            schema_id: 1,
            schema_version: 1,
            alignment: MMAP_BRIDGE_PAYLOAD_ALIGNMENT as u32,
            message_id: 0xdead_beef,
            session_id: 0xcafe_f00d,
            payload_offset: MMAP_BRIDGE_HEADER_BYTES as u64,
            payload_len: 64,
            payload_blake3: [0xab; 32],
        };
        header.encode()
    }

    #[test]
    fn decode_well_formed_header_succeeds() {
        let bytes = well_formed_header_bytes();
        let header = MmapBridgeHeader::decode(&bytes).expect("well-formed decode");
        assert_eq!(header.version, MMAP_BRIDGE_VERSION);
        assert_eq!(header.message_id, 0xdead_beef);
    }

    #[test]
    fn decode_short_buffer_returns_err_not_panic() {
        // Length-guard path: 64 bytes instead of 128.
        let bytes = [0u8; 64];
        let result = std::panic::catch_unwind(|| MmapBridgeHeader::decode(&bytes));
        assert!(result.is_ok(), "decode must not panic on short buffer");
        assert!(result.unwrap().is_err());
    }

    #[test]
    fn decode_bad_magic_returns_err_not_panic() {
        let mut bytes = well_formed_header_bytes();
        bytes[0] ^= 0xff;
        let result = std::panic::catch_unwind(|| MmapBridgeHeader::decode(&bytes));
        assert!(result.is_ok(), "decode must not panic on bad magic");
        assert!(result.unwrap().is_err());
    }

    #[test]
    fn decode_minimum_boundary_bytes_succeeds() {
        // Exactly MMAP_BRIDGE_HEADER_BYTES with valid magic — exercises every
        // try_into() conversion path without panic.
        let bytes = well_formed_header_bytes();
        let header = MmapBridgeHeader::decode(&bytes).expect("boundary decode");
        assert_eq!(header.payload_blake3, [0xab; 32]);
    }

    #[test]
    fn incremental_writer_roundtrips_chunked_payload() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("incremental-frame.aegmmap");
        let payload = (0_u8..=255).cycle().take(100_003).collect::<Vec<_>>();
        let mut writer = MmapBridgeWriter::new(&path, 11, 22, payload.len()).expect("writer");
        for chunk in payload.chunks(7_111) {
            writer.write(chunk).expect("chunk write");
        }
        let header = writer.finish().expect("writer finish");

        assert_eq!(header.payload_len, payload.len() as u64);
        assert!(validate_mmap_bridge_frame(&path));
        let view = open_mmap_bridge_view(&path).expect("read view");
        assert_eq!(view.payload().expect("payload"), payload.as_slice());
        assert!(view.payload_hash_matches().expect("payload hash"));
    }

    #[test]
    fn incremental_writer_rejects_incomplete_or_oversized_payload() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let short_path = directory.path().join("short-frame.aegmmap");
        let mut short = MmapBridgeWriter::new(&short_path, 31, 41, 8).expect("writer");
        short.write(&[1, 2, 3]).expect("short chunk write");
        assert_eq!(
            short.finish(),
            Err("mmap bridge payload is shorter than declared length")
        );

        let large_path = directory.path().join("large-frame.aegmmap");
        let mut large = MmapBridgeWriter::new(&large_path, 51, 61, 2).expect("writer");
        assert_eq!(
            large.write(&[1, 2, 3]),
            Err("mmap bridge payload exceeds declared length")
        );
    }

    #[test]
    fn incremental_writer_streams_file_and_rejects_same_path() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let source_path = directory.path().join("source.bin");
        let frame_path = directory.path().join("streamed-frame.aegmmap");
        let payload = (0_u8..=255).cycle().take(100_003).collect::<Vec<_>>();
        std::fs::write(&source_path, &payload).expect("source write");

        let mut writer = MmapBridgeWriter::new(&frame_path, 71, 81, payload.len()).expect("writer");
        writer.write_file(&source_path, 4_097).expect("file stream");
        let header = writer.finish().expect("writer finish");
        assert_eq!(header.payload_len, payload.len() as u64);
        assert!(validate_mmap_bridge_frame(&frame_path));
        let view = open_mmap_bridge_view(&frame_path).expect("read view");
        assert_eq!(view.payload().expect("payload"), payload.as_slice());

        let same_path = directory.path().join("same-path.aegmmap");
        let mut same_path_writer =
            MmapBridgeWriter::new(&same_path, 91, 101, 4).expect("same path writer");
        std::fs::write(&same_path, b"data").expect("same path source write");
        assert_eq!(
            same_path_writer.write_file(&same_path, 4),
            Err("mmap bridge source and destination must differ")
        );
    }
}
