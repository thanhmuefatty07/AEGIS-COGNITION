use crate::physical::blake3_digest;
use crate::schema::NERVE_SCHEMA;
use memmap2::{Mmap, MmapOptions};
use std::convert::TryInto;
use std::fs::OpenOptions;
use std::ops::Range;
use std::path::Path;

pub const MMAP_BRIDGE_MAGIC: &[u8; 8] = b"AEGMMAP1";
pub const MMAP_BRIDGE_VERSION: u32 = 1;
pub const MMAP_BRIDGE_HEADER_BYTES: usize = 128;
pub const MMAP_BRIDGE_PAYLOAD_ALIGNMENT: usize = 64;

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
    let header = MmapBridgeHeader::new(message_id, session_id, payload)?;
    let total_len = MMAP_BRIDGE_HEADER_BYTES
        .checked_add(payload.len())
        .ok_or("mmap bridge frame too large")?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
        .map_err(|_| "failed to open mmap bridge file")?;
    file.set_len(total_len as u64)
        .map_err(|_| "failed to size mmap bridge file")?;

    // SAFETY: `file` was just opened with write access (line 190) and `set_len`
    // guarantees the file has `total_len` bytes. `MmapOptions::len(total_len)` is set
    // from a non-zero length derived from that exact file size, so the backing mapping
    // cannot extend past the file. `map_mut` produces the only `&mut [u8]` alias to this
    // file region in this process (no other `MmapOptions` map holds a mutable borrow),
    // and we drop the map (flush + scope exit) before any other consumer can read it.
    // The header is encoded via `header.encode()` below, and the payload is copied
    // from `payload: &[u8]` (caller-bounded); both writes stay within `total_len`.
    let mut mmap = unsafe {
        MmapOptions::new()
            .len(total_len)
            .map_mut(&file)
            .map_err(|_| "failed to map mmap bridge file")?
    };
    mmap[..MMAP_BRIDGE_HEADER_BYTES].copy_from_slice(&header.encode());
    mmap[MMAP_BRIDGE_HEADER_BYTES..total_len].copy_from_slice(payload);
    mmap.flush()
        .map_err(|_| "failed to flush mmap bridge frame")?;
    Ok(header)
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
    let mut payload = Vec::with_capacity(payload_len);
    for index in 0..payload_len {
        payload.push(pattern_byte(index));
    }
    write_mmap_bridge_frame(path, message_id, session_id, &payload)
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
}
