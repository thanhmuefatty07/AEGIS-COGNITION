# AEGIS-NERVE

## origin
AEGIS-NERVE is the communication layer of AEGIS-COGNITION. It exists to replace slow, ambiguous, or copy-heavy IPC paths with a binary, schema-driven, zero-copy transport.

## proof
Zero-copy transport reduces redundant allocation and serialization overhead. A versioned schema makes payload validation deterministic and rejects incompatible frames early.

## implementation
- Rust workspace root
- `core/rust/src/schema.rs` for schema registry
- `core/rust/src/shm.rs` for shared memory region metadata
- `core/rust/src/ipc.rs` for zero-copy frame validation
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
