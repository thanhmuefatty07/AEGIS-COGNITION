import json
from pathlib import Path

import pytest

from core.python.aegis.desktop_settings import (
    DESKTOP_SETTINGS_SCHEMA_V1,
    DesktopSettingsConflict,
    DesktopSettingsError,
    DesktopSettingsStore,
)


def test_settings_store_returns_defaults_and_increments_revision(tmp_path: Path):
    store = DesktopSettingsStore(tmp_path / "profile" / "settings.json")
    defaults = store.get()
    assert defaults["schema"] == DESKTOP_SETTINGS_SCHEMA_V1
    assert defaults["revision"] == 0
    assert defaults["settings"]["theme"] == "system"

    updated = store.update({"theme": "dark", "enter_to_send": False}, expected_revision=0)
    assert updated["revision"] == 1
    assert updated["settings"]["theme"] == "dark"
    assert updated["settings"]["enter_to_send"] is False
    assert store.get() == updated

    persisted = json.loads((tmp_path / "profile" / "settings.json").read_text(encoding="utf-8"))
    assert persisted["schema"] == DESKTOP_SETTINGS_SCHEMA_V1
    assert persisted["revision"] == 1


def test_settings_store_persists_provider_advertised_reasoning_effort(tmp_path: Path):
    store = DesktopSettingsStore(tmp_path / "settings.json")

    updated = store.update({"thinking_default": "ultra_high"}, expected_revision=0)

    assert updated["settings"]["thinking_default"] == "ultra_high"
    assert store.get() == updated
    with pytest.raises(DesktopSettingsError, match="thinking_default"):
        store.update({"thinking_default": "not an effort"}, expected_revision=1)


def test_settings_store_rejects_stale_revision_unknown_fields_and_invalid_values(tmp_path: Path):
    store = DesktopSettingsStore(tmp_path / "settings.json")
    store.update({"proxy_mode": "System"}, expected_revision=0)

    with pytest.raises(DesktopSettingsConflict) as conflict:
        store.update({"theme": "light"}, expected_revision=0)
    assert conflict.value.actual_revision == 1
    with pytest.raises(DesktopSettingsError, match="unsupported field"):
        store.update({"api_key": "secret"}, expected_revision=1)
    with pytest.raises(DesktopSettingsError, match="theme"):
        store.update({"theme": "invalid"}, expected_revision=1)


def test_settings_store_rejects_corrupt_documents_and_oversized_instructions(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema": "wrong", "revision": 0, "settings": {}}), encoding="utf-8")
    with pytest.raises(DesktopSettingsError, match="schema"):
        DesktopSettingsStore(path).get()

    store = DesktopSettingsStore(tmp_path / "other.json")
    with pytest.raises(DesktopSettingsError, match="instructions"):
        store.update({"instructions": "x" * 65_537}, expected_revision=0)
