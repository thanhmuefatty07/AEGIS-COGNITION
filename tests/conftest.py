from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _hermetic_git_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from external git configuration, locks, and parent repository discovery."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Aegis Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@aegis.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Aegis Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@aegis.local")
