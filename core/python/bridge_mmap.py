from __future__ import annotations

from dataclasses import dataclass
import mmap
import struct
from pathlib import Path
from typing import BinaryIO


MMAP_BRIDGE_MAGIC = b"AEGMMAP1"
MMAP_BRIDGE_VERSION = 1
MMAP_BRIDGE_HEADER_BYTES = 128
MMAP_BRIDGE_PAYLOAD_ALIGNMENT = 64


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

    def __enter__(self) -> "MmapBridgeFrame":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def open_mmap_bridge_frame(path: str | Path) -> MmapBridgeFrame:
    return MmapBridgeFrame(path)


def write_mmap_bridge_pattern(
    path: str | Path,
    message_id: int,
    session_id: int,
    payload_len: int,
) -> MmapBridgeHeader:
    try:
        import aegis_nerve
    except ImportError as exc:
        raise RuntimeError("aegis_nerve extension is required to write Rust-owned mmap bridge frames") from exc

    aegis_nerve.aegis_write_mmap_bridge_pattern(
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
    try:
        import aegis_nerve
    except ImportError as exc:
        raise RuntimeError("aegis_nerve extension is required to verify mmap bridge payload hash") from exc
    return bool(aegis_nerve.aegis_validate_mmap_bridge_frame(str(path)))


def execute_mmap_wasm_bridge_frame(path: str | Path, fuel_limit: int) -> tuple[int, bytes]:
    try:
        import aegis_nerve
    except ImportError as exc:
        raise RuntimeError("aegis_nerve extension is required to execute mmap Wasm bridge frames") from exc
    fuel_consumed, artifact_hash = aegis_nerve.aegis_execute_mmap_wasm_bridge_frame(
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
