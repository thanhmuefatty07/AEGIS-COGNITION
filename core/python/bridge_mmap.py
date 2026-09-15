from __future__ import annotations

from dataclasses import dataclass
import mmap
import struct
import sys
from pathlib import Path
from typing import BinaryIO


MMAP_BRIDGE_MAGIC = b"AEGMMAP1"
MMAP_BRIDGE_VERSION = 1
MMAP_BRIDGE_HEADER_BYTES = 128
MMAP_BRIDGE_PAYLOAD_ALIGNMENT = 64
MIN_FILE_STREAM_CHUNK_BYTES = 64 * 1024
DEFAULT_FILE_STREAM_CHUNK_BYTES = 1024 * 1024
HIGH_HEADROOM_MIN_AVAILABLE_BYTES = 1024 * 1024 * 1024
HIGH_HEADROOM_MIN_PAYLOAD_BYTES = 64 * 1024 * 1024
HIGH_HEADROOM_FILE_STREAM_CHUNK_BYTES = 8 * 1024 * 1024


def _native_module(error_message: str):
    if any(
        name in sys.modules and sys.modules[name] is None for name in ("aegis_nerve", "aegis_cognition.aegis_nerve")
    ):
        raise RuntimeError(error_message)
    try:
        import aegis_nerve
    except ImportError:
        try:
            from aegis_cognition import aegis_nerve
        except ImportError as exc:
            raise RuntimeError(error_message) from exc
    return aegis_nerve


def recommended_file_stream_chunk_bytes(
    *,
    available_bytes: int | None = None,
    capacity_bytes: int | None = None,
    payload_bytes: int | None = None,
) -> int:
    """Choose a file-stream buffer from an OS memory snapshot.

    Automatic selection reduces the normal 1 MiB buffer under guarded or
    critical pressure and enables the measured 8 MiB throughput lane only for
    payloads of at least 64 MiB when at least 1 GiB and 50% of host memory are
    available. Unknown or malformed observations keep the safe default; this
    function never treats a memory snapshot as a reservation for the current
    operation.
    """

    if available_bytes is None or capacity_bytes is None:
        try:
            import json

            sample = json.loads(
                _native_module(
                    "aegis_nerve extension is required for automatic file-stream sizing"
                ).aegis_resource_usage_sample()
            )
        except (
            AttributeError,
            ImportError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            return DEFAULT_FILE_STREAM_CHUNK_BYTES
        if not isinstance(sample, dict):
            return DEFAULT_FILE_STREAM_CHUNK_BYTES
        if available_bytes is None:
            available_bytes = sample.get("host_memory_available_bytes")
        if capacity_bytes is None:
            capacity_bytes = sample.get("host_memory_bytes")

    if (
        type(available_bytes) is not int
        or type(capacity_bytes) is not int
        or available_bytes < 0
        or capacity_bytes <= 0
    ):
        return DEFAULT_FILE_STREAM_CHUNK_BYTES

    available_bytes = min(available_bytes, capacity_bytes)
    if available_bytes * 100 < capacity_bytes * 10:
        return MIN_FILE_STREAM_CHUNK_BYTES
    if available_bytes * 100 < capacity_bytes * 25:
        return 256 * 1024
    if (
        available_bytes >= HIGH_HEADROOM_MIN_AVAILABLE_BYTES
        and available_bytes * 100 >= capacity_bytes * 50
        and type(payload_bytes) is int
        and payload_bytes >= HIGH_HEADROOM_MIN_PAYLOAD_BYTES
    ):
        # The 8 MiB cap is an explicit throughput lane for a genuinely
        # well-provisioned host.  Native code still clamps it and reduces it
        # to the remaining payload, so this cannot over-allocate small files.
        return HIGH_HEADROOM_FILE_STREAM_CHUNK_BYTES
    return DEFAULT_FILE_STREAM_CHUNK_BYTES


@dataclass(frozen=True)
class MmapBridgeHeader:
    version: int
    header_bytes: int
    schema_id: int
    schema_version: int
    alignment: int
    message_id: int
    session_id: int
    payload_offset: int
    payload_len: int
    payload_blake3: bytes

    def is_valid(self, mapped_len: int) -> bool:
        if self.version != MMAP_BRIDGE_VERSION:
            return False
        if self.header_bytes != MMAP_BRIDGE_HEADER_BYTES:
            return False
        if self.schema_id != 0xAE1515 or self.schema_version != 1:
            return False
        if self.alignment != MMAP_BRIDGE_PAYLOAD_ALIGNMENT:
            return False
        if self.message_id <= 0 or self.session_id <= 0 or self.payload_len <= 0:
            return False
        if self.payload_offset % MMAP_BRIDGE_PAYLOAD_ALIGNMENT != 0:
            return False
        return self.header_bytes <= self.payload_offset <= mapped_len and (
            self.payload_offset + self.payload_len <= mapped_len
        )


class MmapBridgeFrame:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file: BinaryIO = self.path.open("rb")
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.header = _decode_header(self._mmap[:MMAP_BRIDGE_HEADER_BYTES])
        if not self.header.is_valid(len(self._mmap)):
            self.close()
            raise ValueError("invalid mmap bridge frame")

    def payload_view(self) -> memoryview:
        start = self.header.payload_offset
        end = start + self.header.payload_len
        return memoryview(self._mmap)[start:end]

    def first_payload_byte(self) -> int:
        return self._mmap[self.header.payload_offset]

    def close(self) -> None:
        self._mmap.close()
        self._file.close()

    def __enter__(self) -> MmapBridgeFrame:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class MmapBridgeWriter:
    """Stream bounded byte buffers into a Rust-owned bridge frame.

    ``payload_len`` is declared up front so the native writer can reject both
    truncated and oversized frames before publishing a valid header. The
    caller should keep chunks bounded; any object implementing the Python
    buffer protocol is accepted. A reusable ``bytearray`` plus a
    ``memoryview`` can avoid allocating a new Python object for every chunk.
    Read-only buffers can let the native call run without the GIL; callers
    must not mutate their underlying storage through another alias until
    ``write`` returns.
    """

    def __init__(
        self,
        path: str | Path,
        message_id: int,
        session_id: int,
        payload_len: int,
    ) -> None:
        self.path = Path(path)
        self._payload_len = payload_len
        self._writer = _native_module(
            "aegis_nerve extension is required to write Rust-owned mmap bridge frames"
        ).MmapBridgeWriter(str(self.path), message_id, session_id, payload_len)

    def write(self, payload: object) -> None:
        """Write one C-contiguous buffer-protocol export without staging it."""
        self._writer.write(payload)

    def write_file(self, source: str | Path, chunk_bytes: int | None = None) -> None:
        """Stream an existing file through one bounded native buffer.

        The source is read by Rust in bounded chunks, so callers can move a
        large cold artifact into a bridge frame without materializing the
        complete file in Python memory or crossing the Python/Rust boundary
        once per chunk. The declared frame length remains authoritative: an
        early EOF or trailing source byte is rejected before the frame can be
        finalized. If ``chunk_bytes`` is omitted, the native memory snapshot
        reduces the normal 1 MiB buffer under host pressure and enables the
        8 MiB throughput lane only for large payloads on a high-headroom host;
        an unknown snapshot keeps the default.
        """
        source_path = Path(source)
        if source_path.resolve() == self.path.resolve():
            raise ValueError("mmap bridge source and destination must differ")
        try:
            source_size = source_path.stat().st_size
        except OSError as exc:
            raise OSError(f"unable to stat mmap bridge source: {source_path}") from exc
        if source_size != self._payload_len:
            raise ValueError("mmap bridge source size does not match declared payload length")
        if chunk_bytes is None:
            chunk_bytes = recommended_file_stream_chunk_bytes(payload_bytes=source_size)
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")
        self._writer.write_file(str(source_path), chunk_bytes)

    def finish(self) -> MmapBridgeHeader:
        metadata = self._writer.finish()
        return MmapBridgeHeader(
            version=MMAP_BRIDGE_VERSION,
            header_bytes=MMAP_BRIDGE_HEADER_BYTES,
            schema_id=0xAE1515,
            schema_version=1,
            alignment=MMAP_BRIDGE_PAYLOAD_ALIGNMENT,
            message_id=int(metadata[0]),
            session_id=int(metadata[1]),
            payload_offset=int(metadata[2]),
            payload_len=int(metadata[3]),
            payload_blake3=bytes(metadata[4]),
        )

    def close(self) -> None:
        """Release the native file handle without publishing an incomplete frame."""
        if not self._writer.is_finished():
            self._writer.abort()

    def __enter__(self) -> MmapBridgeWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None and not self._writer.is_finished():
            self.finish()
        elif exc_type is not None:
            self.close()


def open_mmap_bridge_frame(path: str | Path) -> MmapBridgeFrame:
    return MmapBridgeFrame(path)


def write_mmap_bridge_pattern(
    path: str | Path,
    message_id: int,
    session_id: int,
    payload_len: int,
) -> MmapBridgeHeader:
    _native_module(
        "aegis_nerve extension is required to write Rust-owned mmap bridge frames"
    ).aegis_write_mmap_bridge_pattern(
        str(path),
        message_id,
        session_id,
        payload_len,
    )
    frame = MmapBridgeFrame(path)
    try:
        return frame.header
    finally:
        frame.close()


def validate_mmap_bridge_frame(path: str | Path) -> bool:
    return bool(
        _native_module(
            "aegis_nerve extension is required to verify mmap bridge payload hash"
        ).aegis_validate_mmap_bridge_frame(str(path))
    )


def execute_mmap_wasm_bridge_frame(path: str | Path, fuel_limit: int) -> tuple[int, bytes]:
    fuel_consumed, artifact_hash = _native_module(
        "aegis_nerve extension is required to execute mmap Wasm bridge frames"
    ).aegis_execute_mmap_wasm_bridge_frame(
        str(path),
        fuel_limit,
    )
    return int(fuel_consumed), bytes(artifact_hash)


def _decode_header(raw: bytes) -> MmapBridgeHeader:
    if len(raw) < MMAP_BRIDGE_HEADER_BYTES:
        raise ValueError("short mmap bridge header")
    if raw[:8] != MMAP_BRIDGE_MAGIC:
        raise ValueError("mmap bridge magic mismatch")
    version, header_bytes = struct.unpack_from("<II", raw, 8)
    schema_id = struct.unpack_from("<Q", raw, 16)[0]
    schema_version, alignment = struct.unpack_from("<II", raw, 24)
    message_id = int.from_bytes(raw[32:48], "little")
    session_id = int.from_bytes(raw[48:64], "little")
    payload_offset, payload_len = struct.unpack_from("<QQ", raw, 64)
    payload_blake3 = raw[80:112]
    return MmapBridgeHeader(
        version=version,
        header_bytes=header_bytes,
        schema_id=schema_id,
        schema_version=schema_version,
        alignment=alignment,
        message_id=message_id,
        session_id=session_id,
        payload_offset=payload_offset,
        payload_len=payload_len,
        payload_blake3=payload_blake3,
    )
