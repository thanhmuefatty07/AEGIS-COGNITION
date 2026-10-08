"""Bounded, non-secret activation state for local capabilities.

The state is scoped to one canonical workspace and stores only descriptor
hashes plus explicit user decisions.  It never stores commands, API keys,
cookies, HTTP headers, or skill bodies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


CAPABILITY_STATE_SCHEMA_V1 = "aegis-capability-state-v1"
MAX_RECORDS = 512
MAX_ID_LENGTH = 256
MAX_HASH_LENGTH = 128
CAPABILITY_KINDS = frozenset({"skill", "extension", "mcp"})
_CAPABILITY_STATE_IO_LOCKS: weakref.WeakValueDictionary[Path, threading.Lock] = weakref.WeakValueDictionary()
_CAPABILITY_STATE_IO_LOCKS_GUARD = threading.Lock()


def _capability_state_io_lock(path: Path) -> threading.Lock:
    with _CAPABILITY_STATE_IO_LOCKS_GUARD:
        lock = _CAPABILITY_STATE_IO_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _CAPABILITY_STATE_IO_LOCKS[path] = lock
        return lock


class CapabilityStateError(ValueError):
    """Capability state is malformed or unavailable."""


class CapabilityStateConflict(CapabilityStateError):
    """Capability state changed since the caller last read it."""

    def __init__(self, actual_revision: int) -> None:
        super().__init__("capability state changed; reload before updating")
        self.actual_revision = actual_revision


@contextmanager
def _capability_state_process_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError as error:
        raise CapabilityStateError("capability state lock is unavailable") from error
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError, ValueError) as error:
        with suppress(OSError):
            handle.close()
        raise CapabilityStateError("capability state lock could not be acquired") from error
    try:
        yield
    finally:
        with suppress(ImportError, OSError, ValueError):
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        with suppress(OSError):
            handle.close()


def workspace_scope_id(path: str | Path) -> str:
    value = path if isinstance(path, Path) else Path(path)
    canonical = value.expanduser().resolve(strict=False)
    normalized = str(canonical).casefold() if os.name == "nt" else str(canonical)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class CapabilityStateStore:
    """Thread-safe atomic state store for one workspace scope."""

    def __init__(self, path: str | Path) -> None:
        self.path = (path if isinstance(path, Path) else Path(path)).expanduser().resolve(strict=False)
        self._lock = _capability_state_io_lock(self.path)

    def get(self) -> dict[str, object]:
        with self._lock:
            revision, records = self._read()
        return self._result(revision, records)

    def set(
        self,
        *,
        kind: str,
        capability_id: str,
        descriptor_hash: str,
        enabled: bool,
        approved: bool,
        expected_revision: int,
        source_server_id: str | None = None,
        resource_uri: str | None = None,
        skill_name: str | None = None,
        skill_description: str | None = None,
    ) -> dict[str, object]:
        self._validate_record(
            kind,
            capability_id,
            descriptor_hash,
            enabled,
            approved,
            source_server_id=source_server_id,
            resource_uri=resource_uri,
            skill_name=skill_name,
            skill_description=skill_description,
        )
        if type(expected_revision) is not int or expected_revision < 0:
            raise CapabilityStateError("expected_revision must be a non-negative integer")
        with self._lock, _capability_state_process_lock(self.path):
            revision, records = self._read()
            if revision != expected_revision:
                raise CapabilityStateConflict(revision)
            key = (kind, capability_id)
            retained = [item for item in records if (item["kind"], item["id"]) != key]
            record: dict[str, object] = {
                "kind": kind,
                "id": capability_id,
                "descriptor_hash": descriptor_hash,
                "enabled": enabled,
                "approved": approved,
                "updated_at_ms": int(time.time() * 1000),
            }
            if source_server_id is not None and resource_uri is not None:
                record["source_server_id"] = source_server_id
                record["resource_uri"] = resource_uri
            if skill_name is not None and skill_description is not None:
                record["skill_name"] = skill_name
                record["skill_description"] = skill_description
            retained.append(record)
            retained.sort(key=lambda item: (str(item["kind"]), str(item["id"])))
            if len(retained) > MAX_RECORDS:
                raise CapabilityStateError("capability state capacity is exhausted")
            next_revision = revision + 1
            self._write(next_revision, retained)
        return self._result(next_revision, retained)

    def _read(self) -> tuple[int, list[dict[str, object]]]:
        if self.path.is_symlink() or not self.path.exists():
            return 0, []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CapabilityStateError("capability state cannot be read") from error
        if not isinstance(raw, dict) or raw.get("schema") != CAPABILITY_STATE_SCHEMA_V1:
            raise CapabilityStateError("capability state schema is unsupported")
        revision = raw.get("revision")
        records = raw.get("records")
        if type(revision) is not int or revision < 0 or not isinstance(records, list) or len(records) > MAX_RECORDS:
            raise CapabilityStateError("capability state document is invalid")
        typed: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for item in records:
            if not isinstance(item, dict):
                raise CapabilityStateError("capability state record is invalid")
            kind = item.get("kind")
            capability_id = item.get("id")
            descriptor_hash = item.get("descriptor_hash")
            enabled = item.get("enabled")
            approved = item.get("approved")
            updated_at_ms = item.get("updated_at_ms")
            source_server_id = item.get("source_server_id")
            resource_uri = item.get("resource_uri")
            skill_name = item.get("skill_name")
            skill_description = item.get("skill_description")
            self._validate_record(
                kind,
                capability_id,
                descriptor_hash,
                enabled,
                approved,
                source_server_id=source_server_id,
                resource_uri=resource_uri,
                skill_name=skill_name,
                skill_description=skill_description,
            )
            if type(updated_at_ms) is not int or updated_at_ms < 0:
                raise CapabilityStateError("capability state timestamp is invalid")
            key = (str(kind), str(capability_id))
            if key in seen:
                raise CapabilityStateError("capability state contains duplicate records")
            seen.add(key)
            record: dict[str, object] = {
                "kind": str(kind),
                "id": str(capability_id),
                "descriptor_hash": str(descriptor_hash),
                "enabled": bool(enabled),
                "approved": bool(approved),
                "updated_at_ms": updated_at_ms,
            }
            if source_server_id is not None and resource_uri is not None:
                record["source_server_id"] = source_server_id
                record["resource_uri"] = resource_uri
            if skill_name is not None and skill_description is not None:
                record["skill_name"] = skill_name
                record["skill_description"] = skill_description
            typed.append(record)
        return revision, typed

    def _write(self, revision: int, records: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": CAPABILITY_STATE_SCHEMA_V1, "revision": revision, "records": records}
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temp.write_bytes(encoded)
            os.replace(temp, self.path)
        except OSError as error:
            raise CapabilityStateError("capability state cannot be written") from error
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    @staticmethod
    def _validate_record(
        kind: object,
        capability_id: object,
        descriptor_hash: object,
        enabled: object,
        approved: object,
        *,
        source_server_id: object = None,
        resource_uri: object = None,
        skill_name: object = None,
        skill_description: object = None,
    ) -> None:
        if type(kind) is not str or kind not in CAPABILITY_KINDS:
            raise CapabilityStateError("capability kind is invalid")
        if type(capability_id) is not str or not capability_id.strip() or len(capability_id) > MAX_ID_LENGTH:
            raise CapabilityStateError("capability id is invalid")
        if type(descriptor_hash) is not str or not descriptor_hash or len(descriptor_hash) > MAX_HASH_LENGTH:
            raise CapabilityStateError("capability descriptor hash is invalid")
        if type(enabled) is not bool or type(approved) is not bool:
            raise CapabilityStateError("capability state flags are invalid")
        if kind == "mcp" and enabled and not approved:
            raise CapabilityStateError("an MCP capability must be approved before it can be enabled")
        if (source_server_id is None) != (resource_uri is None):
            raise CapabilityStateError("remote skill origin fields must be supplied together")
        if (skill_name is None) != (skill_description is None):
            raise CapabilityStateError("remote skill metadata fields must be supplied together")
        if source_server_id is not None:
            if kind != "skill":
                raise CapabilityStateError("only a skill can have a remote MCP origin")
            if (
                type(source_server_id) is not str
                or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_server_id) is None
            ):
                raise CapabilityStateError("remote skill server id is invalid")
            if (
                type(resource_uri) is not str
                or not resource_uri
                or len(resource_uri) > 4_096
                or any(ord(char) < 0x20 or ord(char) == 0x7F for char in resource_uri)
            ):
                raise CapabilityStateError("remote skill resource URI is invalid")
            try:
                resource_uri.encode("utf-8")
            except UnicodeEncodeError as error:
                raise CapabilityStateError("remote skill resource URI is invalid") from error
            uri_hash = hashlib.sha256(resource_uri.encode("utf-8")).hexdigest()
            if capability_id != f"mcp:{source_server_id}:{uri_hash}":
                raise CapabilityStateError("remote skill id does not match its server and URI")
            if type(descriptor_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", descriptor_hash) is None:
                raise CapabilityStateError("remote skill manifest hash is invalid")
            if skill_name is not None and (
                type(skill_name) is not str
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name) is None
                or len(skill_name) > 64
                or type(skill_description) is not str
                or not skill_description.strip()
                or len(skill_description) > 1_024
                or any(ord(char) < 0x20 and char not in "\t\r\n" for char in skill_description)
            ):
                raise CapabilityStateError("remote skill display metadata is invalid")
        elif skill_name is not None:
            raise CapabilityStateError("only a remote skill can have remote skill metadata")

    @staticmethod
    def _result(revision: int, records: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema": CAPABILITY_STATE_SCHEMA_V1,
            "revision": revision,
            "records": [dict(item) for item in records],
        }


__all__ = [
    "CAPABILITY_KINDS",
    "CAPABILITY_STATE_SCHEMA_V1",
    "CapabilityStateConflict",
    "CapabilityStateError",
    "CapabilityStateStore",
    "workspace_scope_id",
]
