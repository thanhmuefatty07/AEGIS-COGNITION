"""Configuration boundary for the canonical Agent API."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, cast

from .errors import ConfigError

try:
    from core.python.aegis.trust_policy import (
        VALID_TRUST_LEVELS,
        trust_policy_hash as _shared_trust_policy_hash,
    )
except ImportError:
    from aegis.trust_policy import (  # type: ignore[import-not-found]
        VALID_TRUST_LEVELS,
        trust_policy_hash as _shared_trust_policy_hash,
    )


CONFIG_DIR = Path.home() / ".aegis"
CONFIG_FILE = CONFIG_DIR / "config.toml"
def trust_policy_hash(trust_level: str) -> str:
    """Return the canonical subject hash with the config error contract."""

    try:
        return _shared_trust_policy_hash(trust_level)
    except ValueError as exc:
        raise ConfigError.invalid_trust_level(str(trust_level).strip().upper()) from exc


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
    trust_policy_hash: str = ""

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
        resolved_level = resolve_trust_level(config, trust_level)
        resolved = cls(
            task=task,
            llm=llm,
            trust_level=resolved_level,
            browser=browser,
            max_steps=max_steps,
            options=dict(kwargs),
            config=config,
            api_key=resolve_api_key(config),
            trust_policy_hash=trust_policy_hash(resolved_level),
        )
        # Agent(lab=True) is also a mission boundary.  Bind the policy hash
        # before compatibility options reach LabApplication so a gateway or
        # native controller cannot silently operate under another policy.
        raw_lab = resolved.options.get("lab", False)
        if type(raw_lab) is not bool:
            raise ValueError("Agent lab flag must be boolean")
        if raw_lab:
            resolved.options.setdefault("lab_trust_policy_hash", resolved.trust_policy_hash)
        resolved.validate()
        return resolved

    def validate(self) -> None:
        if type(self.task) is not str or not self.task.strip():
            raise ConfigError.task_missing()
        if not self.api_key:
            raise ConfigError.api_key_missing()
        if type(self.trust_level) is not str or self.trust_level not in VALID_TRUST_LEVELS:
            raise ConfigError.invalid_trust_level(self.trust_level)
        if type(self.browser) is not bool:
            raise ValueError("Agent browser flag must be boolean")
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise ConfigError.max_steps_invalid(self.max_steps)
