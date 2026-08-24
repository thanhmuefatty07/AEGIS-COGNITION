# AEGIS-NERVE

## origin
AEGIS-NERVE is the communication layer of AEGIS-COGNITION. It provides binary,
schema-driven framing and shared-memory metadata for bounded IPC paths. Some
paths validate mmap-backed buffers, but end-to-end zero-copy performance is not
claimed until payload-class benchmarks prove it.

## proof
The design can reduce redundant allocation and serialization overhead. A
versioned schema makes payload validation deterministic and rejects
incompatible frames early. This is a design property, not a measured product
speed claim.

## implementation
- Rust workspace root
- `core/rust/src/schema.rs` for schema registry
- `core/rust/src/shm.rs` for shared memory region metadata
- `core/rust/src/ipc.rs` for frame and alignment validation
- alignment preserved with 64-byte boundaries where shared memory is involved

## risks
- schema drift
- pointer invalidity
- alignment mismatch
- accidental copy-based fallback paths

## tests
- schema version validation
- null pointer rejection
- zero-length payload rejection
- alignment check
- roundtrip frame validation
