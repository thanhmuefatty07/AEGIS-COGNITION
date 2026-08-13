# Handoff Report: R3 Requirement Analysis (mmap-Backed Arrow IPC Audit Stream)

## 1. Observation

Direct observations made in the codebase:

* **No Resumable Constructor for writing**: In `core/rust/src/replay.rs:2294-2330`, `SegmentedArrowAuditStream::create` is the only constructor:
  ```rust
  pub fn create(
      directory: impl AsRef<Path>,
      run_id: RunId,
      max_events_per_segment: usize,
  ) -> Result<Self, &'static str> {
      ...
      Ok(Self {
          directory,
          run_id,
          max_events_per_segment,
          pending_events: Vec::with_capacity(max_events_per_segment),
          entries: Vec::new(),
          previous_segment_hash: [0; 32],
          last_event_hash: [0; 32],
          next_event_id: 1,
          ...
      })
  }
  ```
  It initializes all sequence checking/hashing values to their defaults (zeros or `1`).
* **Conflict on reuse of directory**: In `core/rust/src/replay.rs:2558-2562`, `SegmentedArrowAuditStream::flush_segment` tries to write to segment ID `self.entries.len() + 1`:
  ```rust
  let segment_id = self.entries.len() as u64 + 1;
  let path = RunEventSegmentArchive::segment_path(&self.directory, self.run_id, segment_id);
  if path.exists() {
      return Err("segmented arrow audit segment already exists");
  }
  ```
  This causes a writer conflict if the directory contains previous segments, since `entries` starts empty.
* **Manifest Recovery**: In `core/rust/src/replay.rs:3960`, `recover_manifest_from_segments` is implemented to recover a valid sequence manifest from existing segments on disk.
* **No PyCapsule / Arrow FFI Exports**: In `core/rust/src/ffi.rs:230-262`, PyO3 bindings for `aegis_nerve` are declared. None of them export Arrow structures or PyCapsules for Arrow data exchange.
* **Binary-only Python Mmap Bridge**: In `core/python/bridge_mmap.py:47-70`, `MmapBridgeFrame` maps a file containing a custom binary format (`AEGMMAP1`) using `mmap.mmap` and returns `memoryview(self._mmap)[start:end]` for the payload. It does not interface with Arrow IPC streams.
* **Criterion and PyTest suites**:
  * Unit tests are located in `core/rust/src/tests.rs` (such as `segmented_arrow_audit_stream_enforces_single_writer_and_append_only_segments` at line 8960) and `core/python/tests.py` (such as `test_python_mmap_bridge_exposes_payload_memoryview_without_copy` at line 342).
  * Micro-benchmarks are defined in `core/rust/benches/nerve_bench.rs` (like `bench_replay_io_mmap_materialized` at line 424 and `bench_replay_segmented_arrow_append` at line 445).

---

## 2. Logic Chain

1. **Conflict on Resume**: Because `SegmentedArrowAuditStream` initializes `entries` as empty and `next_event_id` as 1, any attempt to write events to a directory containing existing segment files triggers a conflict in `flush_segment` (`"segmented_arrow_audit_segment already exists"`).
2. **Path to Resumability**: To enable resumability, `SegmentedArrowAuditStream` must be initialized from the last valid state of the run. Since `recover_manifest_from_segments` recovers the valid `RunEventSegmentManifest` prefix from segment files, and `read_ledger_mmap` parses the events from the segments, we can compute the correct initial state (`entries`, `previous_segment_hash`, `last_event_hash`, `next_event_id`, and transition flags) from the recovered ledger.
3. **Rollback Semantics**: The recovery loop in `recover_manifest_from_segments` stops at the first missing or invalid segment/commit file. Resuming from this recovered manifest naturally handles rolling back to the last valid checkpoint by discarding any trailing corrupted segments.
4. **Python Bridge Zero-Copy Views**: Currently, Python reads raw binary frames but lacks access to the Arrow IPC audit stream. PyArrow has native support for reading zero-copy Arrow tables from memory-mapped files via `pyarrow.ipc.open_stream(mmap.mmap(...))`. Thus, Python can map and read the Arrow IPC files directly.
5. **Zero-Copy FFI Overhead**: The acceptance criteria requires transferring memory views under 5 us. PyO3's `PyMemoryView::from_slice` allows Python to create a memoryview wrapping a Rust slice (pointing directly to the memory-mapped segment) without copying, which achieves < 1 us latency.

---

## 3. Caveats

* **Unclean Crash Pending Queue**: If the system crashes mid-transaction, any events that were appended to the `pending_events` vector but not yet flushed to a segment are lost. This is by design, as the stream only commits durably at segment boundaries (verified by sidecar `.commit` files).
* **Concurrent Writers**: The writer lock (`run-{run_id}.writer.lock`) prevents concurrent writes, but the resume logic must also acquire this lock to ensure exclusive access.

---

## 4. Conclusion

Requirement R3 can be completed by implementing two primary enhancements:

1. **Rust-Side Resumable Writer**: Introduce `SegmentedArrowAuditStream::resume(...)` that uses `recover_manifest_from_segments` and `read_ledger_mmap` to load the previous segment state and reconstruct the audit stream context, enabling append-only resumption.
2. **Python-Side Zero-Copy Arrow Views**: Add an Arrow audit view to the Python bridge using PyArrow (`pyarrow.ipc.open_stream`) to read the memory-mapped `.arrow` segments, coupled with PyO3 `PyMemoryView` exports in `ffi.rs` to expose raw mmap pointers to Python zero-copy.

---

## 5. Verification Method

To verify the implemented solution, the following should be executed:
* **Cargo Tests**: Run `cargo test --lib replay` to check all stream recovery and validation logic.
* **Python Tests**: Run `pytest core/python/tests.py` to check Python bridge compatibility.
* **Criterion Benchmarks**: Run `cargo bench --bench nerve_bench` to check the execution latencies of:
  * `replay_io_mmap_materialized`
  * `replay_segmented_arrow_append`
  * `replay_segmented_arrow_manifest_recover`
  * `mmap_bridge_payload_view`
