"""Profile-owned, non-secret desktop preferences.

The renderer may edit only this bounded allow-list.  Credentials, provider
responses, filesystem contents, and executable capability configuration are
intentionally outside this store.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path

from .provider_models import is_reasoning_effort_value


DESKTOP_SETTINGS_SCHEMA_V1 = "aegis-desktop-settings-v1"
MAX_SETTINGS_BYTES = 256 * 1024
MAX_INSTRUCTIONS_CHARS = 64 * 1024

DEFAULT_SETTINGS: dict[str, object] = {
    "theme": "system",
    "font_size_preset": "Grande",
    "proxy_mode": "Direct",
    "permission_mode": "Ask every time",
    "agent_default_mode": "Agent",
    "voice_enabled": False,
    "thinking_default": "Auto",
    "context_usage": "Remaining",
    "enter_to_send": True,
    "instructions": "Use the local workspace context, preserve evidence boundaries, and report uncertainty explicitly.",
    "parallel_workers_enabled": True,
}

_ENUMS: dict[str, frozenset[str]] = {
    "theme": frozenset({"system", "dark", "light"}),
    "font_size_preset": frozenset({"Tall", "Grande", "Venti", "Trenta"}),
    "proxy_mode": frozenset({"System", "Direct", "Custom"}),
    "permission_mode": frozenset({"Ask every time", "Accept edits", "Auto"}),
    "agent_default_mode": frozenset({"Agent", "Plan", "Goal"}),
    "context_usage": frozenset({"Remaining", "Used"}),
}
_BOOLS = frozenset({"voice_enabled", "enter_to_send", "parallel_workers_enabled"})
_FIELDS = frozenset((*_ENUMS, *_BOOLS, "instructions", "thinking_default"))


class DesktopSettingsError(ValueError):
    """The profile-owned settings document is invalid or unavailable."""


class DesktopSettingsConflict(DesktopSettingsError):
    """The caller attempted to update a stale settings revision."""

    def __init__(self, actual_revision: int) -> None:
        super().__init__("desktop settings changed; reload before updating")
        self.actual_revision = actual_revision


def _canonical_path(path: str | Path) -> Path:
    value = path if isinstance(path, Path) else Path(path)
    if "\x00" in str(value):
        raise DesktopSettingsError("settings path contains NUL")
    return value.expanduser().resolve(strict=False)


def _validate_settings(settings: Mapping[str, object]) -> dict[str, object]:
    if set(settings) != _FIELDS:
        raise DesktopSettingsError("desktop settings fields are unsupported")
    validated: dict[str, object] = {}
    for key, allowed in _ENUMS.items():
        value = settings[key]
        if type(value) is not str or value not in allowed:
            raise DesktopSettingsError(f"desktop setting {key} is invalid")
        validated[key] = value
    thinking_default = settings["thinking_default"]
    if type(thinking_default) is not str or (
        thinking_default != "Auto" and not is_reasoning_effort_value(thinking_default)
    ):
        raise DesktopSettingsError("desktop setting thinking_default is invalid")
    validated["thinking_default"] = thinking_default
    for key in _BOOLS:
        value = settings[key]
        if type(value) is not bool:
            raise DesktopSettingsError(f"desktop setting {key} is invalid")
        validated[key] = value
    instructions = settings["instructions"]
    if type(instructions) is not str or len(instructions) > MAX_INSTRUCTIONS_CHARS or "\x00" in instructions:
        raise DesktopSettingsError("desktop instructions are invalid")
    validated["instructions"] = instructions
    return validated


class DesktopSettingsStore:
    """Thread-safe atomic settings store for one local profile."""

    def __init__(self, path: str | Path) -> None:
        self.path = _canonical_path(path)
        self._lock = threading.RLock()

    def get(self) -> dict[str, object]:
        with self._lock:
            revision, settings = self._read()
        return self._result(revision, settings)

    def update(self, patch: Mapping[str, object], *, expected_revision: int) -> dict[str, object]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise DesktopSettingsError("expected_revision must be a non-negative integer")
        if not isinstance(patch, Mapping) or not patch:
            raise DesktopSettingsError("settings patch must be a non-empty object")
        if any(key not in _FIELDS for key in patch):
            raise DesktopSettingsError("settings patch contains an unsupported field")
        with self._lock:
            revision, current = self._read()
            if revision != expected_revision:
                raise DesktopSettingsConflict(revision)
            candidate = dict(current)
            candidate.update(dict(patch))
            validated = _validate_settings(candidate)
            next_revision = revision + 1
            self._write(next_revision, validated)
        return self._result(next_revision, validated)

    def _read(self) -> tuple[int, dict[str, object]]:
        if self.path.is_symlink() or not self.path.exists():
            return 0, dict(DEFAULT_SETTINGS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DesktopSettingsError("desktop settings cannot be read") from error
        if not isinstance(raw, dict) or raw.get("schema") != DESKTOP_SETTINGS_SCHEMA_V1:
            raise DesktopSettingsError("desktop settings schema is unsupported")
        revision = raw.get("revision")
        settings = raw.get("settings")
        if type(revision) is not int or revision < 0 or not isinstance(settings, dict):
            raise DesktopSettingsError("desktop settings document is invalid")
        return revision, _validate_settings(settings)

    def _write(self, revision: int, settings: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": DESKTOP_SETTINGS_SCHEMA_V1, "revision": revision, "settings": dict(settings)}
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_SETTINGS_BYTES:
            raise DesktopSettingsError("desktop settings exceed their byte bound")
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temp.write_bytes(encoded)
            os.replace(temp, self.path)
        except OSError as error:
            raise DesktopSettingsError("desktop settings cannot be written") from error
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    @staticmethod
    def _result(revision: int, settings: Mapping[str, object]) -> dict[str, object]:
        return {
            "schema": DESKTOP_SETTINGS_SCHEMA_V1,
            "revision": revision,
            "settings": dict(settings),
        }


__all__ = [
    "DEFAULT_SETTINGS",
    "DESKTOP_SETTINGS_SCHEMA_V1",
    "MAX_INSTRUCTIONS_CHARS",
    "DesktopSettingsConflict",
    "DesktopSettingsError",
    "DesktopSettingsStore",
]
