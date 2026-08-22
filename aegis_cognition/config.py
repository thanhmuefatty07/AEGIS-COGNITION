"""Configuration boundary for the canonical Agent API."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, cast

from .errors import ConfigError


CONFIG_DIR = Path.home() / ".aegis"
CONFIG_FILE = CONFIG_DIR / "config.toml"
VALID_TRUST_LEVELS = frozenset({"DEV", "STAGING", "PROD"})


def load_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    """Load user configuration without allowing malformed data to escape."""

    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError.invalid_file(path, str(exc)) from exc
    return loaded


def resolve_api_key(config: Mapping[str, Any]) -> str | None:
    llm = config.get("llm", {})
    configured: object = cast(Mapping[str, object], llm).get("api_key") if isinstance(llm, Mapping) else None
    return (
        configured
        if isinstance(configured, str) and configured
        else os.environ.get("OPENAI_API_KEY") or os.environ.get("AEGIS_API_KEY")
    )


def resolve_trust_level(config: Mapping[str, Any], override: str | None = None) -> str:
    trust = config.get("trust", {})
    configured: object = cast(Mapping[str, object], trust).get("level") if isinstance(trust, Mapping) else None
    selected: object = override or configured or os.environ.get("AEGIS_TRUST_LEVEL") or "DEV"
    return str(selected).strip().upper()


@dataclass(frozen=True)
class AgentConfig:
    """Validated immutable input for the application service."""

    task: str
    llm: Any
    trust_level: str
    browser: bool
    max_steps: int
    options: dict[str, Any]
    config: dict[str, Any]
    api_key: str | None

    @classmethod
    def from_inputs(
        cls,
        task: str,
        *,
        llm: Any = None,
        trust_level: str | None = None,
        browser: bool = False,
        max_steps: int = 100,
        **kwargs: Any,
    ) -> AgentConfig:
        config = load_config()
        resolved = cls(
            task=task,
            llm=llm,
            trust_level=resolve_trust_level(config, trust_level),
            browser=browser,
            max_steps=max_steps,
            options=dict(kwargs),
            config=config,
            api_key=resolve_api_key(config),
        )
        resolved.validate()
        return resolved

    def validate(self) -> None:
        if not self.task.strip():
            raise ConfigError.task_missing()
        if not self.api_key:
            raise ConfigError.api_key_missing()
        if self.trust_level not in VALID_TRUST_LEVELS:
            raise ConfigError.invalid_trust_level(self.trust_level)
        if self.max_steps < 1:
            raise ConfigError.max_steps_invalid(self.max_steps)
