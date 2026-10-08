"""Small profile-owned registry for recently opened local projects.

The registry stores only canonical paths and display metadata.  It never
copies source files, conversation data, credentials, or provider settings.
Writes are atomic so a power loss cannot leave a partially written registry.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_REGISTRY_SCHEMA_V1 = "aegis-project-registry-v1"
MAX_PROJECTS = 256
MAX_PATH_BYTES = 8_192
MAX_NAME_LENGTH = 256


class ProjectRegistryError(ValueError):
    """The profile-owned project registry is malformed or unavailable."""


def _canonical_path(raw_path: str | Path) -> Path:
    if isinstance(raw_path, Path):
        path = raw_path
    elif isinstance(raw_path, str):
        path = Path(raw_path)
    else:
        raise ProjectRegistryError("project path must be a path")
    if "\x00" in str(path):
        raise ProjectRegistryError("project path contains NUL")
    resolved = path.expanduser().resolve(strict=False)
    if len(os.fsencode(str(resolved))) > MAX_PATH_BYTES:
        raise ProjectRegistryError("project path exceeds its byte bound")
    return resolved


def project_id_for_path(path: str | Path) -> str:
    """Return a stable, non-secret identity derived from a canonical path."""

    canonical = _canonical_path(path)
    normalized = str(canonical).casefold() if os.name == "nt" else str(canonical)
    return f"project:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    path: str
    name: str
    parent_path: str
    last_opened_at_ms: int
    open_count: int

    @property
    def exists(self) -> bool:
        return Path(self.path).is_dir()

    def as_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "path": self.path,
            "name": self.name,
            "parent_path": self.parent_path,
            "last_opened_at_ms": self.last_opened_at_ms,
            "open_count": self.open_count,
            "status": "AVAILABLE" if self.exists else "MISSING",
        }


class ProjectRegistry:
    """Thread-safe, bounded registry owned by one local profile."""

    def __init__(self, path: str | Path, *, max_projects: int = MAX_PROJECTS) -> None:
        if type(max_projects) is not int or not 1 <= max_projects <= MAX_PROJECTS:
            raise ProjectRegistryError("project registry capacity is invalid")
        self.path = _canonical_path(path)
        self.max_projects = max_projects
        self._lock = threading.RLock()

    def list(self) -> tuple[ProjectRecord, ...]:
        with self._lock:
            entries = self._read()
        return tuple(sorted(entries, key=lambda item: (-item.last_opened_at_ms, item.name.casefold(), item.path)))

    def upsert(self, project_path: str | Path, *, opened_at_ms: int | None = None) -> ProjectRecord:
        canonical = _canonical_path(project_path)
        if not canonical.is_dir():
            raise ProjectRegistryError("project path must identify an existing directory")
        name = canonical.name or str(canonical)
        if not 1 <= len(name) <= MAX_NAME_LENGTH:
            raise ProjectRegistryError("project name is invalid")
        timestamp = int(time.time() * 1000) if opened_at_ms is None else opened_at_ms
        if type(timestamp) is not int or timestamp < 0:
            raise ProjectRegistryError("opened_at_ms must be a non-negative integer")
        project_id = project_id_for_path(canonical)
        with self._lock:
            entries = {entry.project_id: entry for entry in self._read()}
            previous = entries.get(project_id)
            record = ProjectRecord(
                project_id=project_id,
                path=str(canonical),
                name=name,
                parent_path=str(canonical.parent),
                last_opened_at_ms=timestamp,
                open_count=(previous.open_count + 1 if previous is not None else 1),
            )
            entries[project_id] = record
            ordered = sorted(entries.values(), key=lambda item: (-item.last_opened_at_ms, item.path))[: self.max_projects]
            self._write(ordered)
            return record

    def remove(self, project_id: str) -> bool:
        if type(project_id) is not str or not project_id.startswith("project:") or len(project_id) != 40:
            raise ProjectRegistryError("project_id is invalid")
        with self._lock:
            entries = list(self._read())
            retained = [entry for entry in entries if entry.project_id != project_id]
            if len(retained) == len(entries):
                return False
            self._write(retained)
            return True

    def _read(self) -> list[ProjectRecord]:
        if self.path.is_symlink() or not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectRegistryError("project registry cannot be read") from error
        if not isinstance(raw, dict) or raw.get("schema") != PROJECT_REGISTRY_SCHEMA_V1:
            raise ProjectRegistryError("project registry schema is unsupported")
        raw_projects = raw.get("projects", [])
        if not isinstance(raw_projects, list) or len(raw_projects) > self.max_projects:
            raise ProjectRegistryError("project registry project list is invalid")
        records: list[ProjectRecord] = []
        for item in raw_projects:
            if not isinstance(item, dict):
                raise ProjectRegistryError("project registry entry is invalid")
            record = self._record_from_json(item)
            if record.project_id != project_id_for_path(record.path):
                raise ProjectRegistryError("project registry identity does not match its path")
            records.append(record)
        if len({item.project_id for item in records}) != len(records):
            raise ProjectRegistryError("project registry contains duplicate identities")
        return records

    def _write(self, records: list[ProjectRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": PROJECT_REGISTRY_SCHEMA_V1,
            # Status is derived from the current filesystem and is deliberately
            # not persisted.  The on-disk contract contains metadata only.
            "projects": [
                {
                    key: value
                    for key, value in record.as_dict().items()
                    if key != "status"
                }
                for record in records
            ],
        }
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_PROJECTS * MAX_PATH_BYTES:
            raise ProjectRegistryError("project registry exceeds its byte bound")
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temp.write_bytes(encoded)
            os.replace(temp, self.path)
        except OSError as error:
            raise ProjectRegistryError("project registry cannot be written") from error
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    @staticmethod
    def _record_from_json(item: dict[str, Any]) -> ProjectRecord:
        project_id = item.get("project_id")
        path = item.get("path")
        name = item.get("name")
        parent_path = item.get("parent_path")
        last_opened = item.get("last_opened_at_ms")
        open_count = item.get("open_count")
        if (
            type(project_id) is not str
            or type(path) is not str
            or type(name) is not str
            or type(parent_path) is not str
            or type(last_opened) is not int
            or last_opened < 0
            or type(open_count) is not int
            or open_count < 1
            or not name
            or len(name) > MAX_NAME_LENGTH
        ):
            raise ProjectRegistryError("project registry entry fields are invalid")
        canonical = _canonical_path(path)
        if str(canonical) != path or str(canonical.parent) != parent_path:
            raise ProjectRegistryError("project registry path is not canonical")
        return ProjectRecord(
            project_id=project_id,
            path=path,
            name=name,
            parent_path=parent_path,
            last_opened_at_ms=last_opened,
            open_count=open_count,
        )


__all__ = [
    "MAX_PROJECTS",
    "PROJECT_REGISTRY_SCHEMA_V1",
    "ProjectRecord",
    "ProjectRegistry",
    "ProjectRegistryError",
    "project_id_for_path",
]
