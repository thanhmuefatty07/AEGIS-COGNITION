"""Configuration boundary for the canonical Agent API."""

from __future__ import annotations

import os
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, cast
import json
import tempfile

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

_SENSITIVE_KEY_PARTS = ("api_key", "secret", "token", "password", "credential")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a display-safe copy of configuration data."""

    def redact_value(key: str, value: Any) -> Any:
        if _is_sensitive_key(key):
            return "<redacted>" if value else value
        if isinstance(value, Mapping):
            mapping = cast(Mapping[str, Any], value)
            return {
                str(child_key): redact_value(str(child_key), child_value) for child_key, child_value in mapping.items()
            }
        if isinstance(value, list):
            items = cast(list[Any], value)
            return [redact_value(key, item) for item in items]
        return value

    return {str(key): redact_value(str(key), value) for key, value in config.items()}


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        items = cast(list[Any], value)
        return "[" + ", ".join(_toml_literal(item) for item in items) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def render_toml(config: Mapping[str, Any]) -> str:
    """Render the supported TOML subset deterministically."""

    lines: list[str] = []

    def render_section(section: Mapping[str, Any], prefix: str | None = None) -> None:
        scalars: list[tuple[str, Any]] = [
            (str(key), value) for key, value in section.items() if not isinstance(value, Mapping)
        ]
        nested: list[tuple[str, Mapping[str, Any]]] = [
            (str(key), cast(Mapping[str, Any], value)) for key, value in section.items() if isinstance(value, Mapping)
        ]
        if prefix is not None:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{prefix}]")
        for key, value in scalars:
            lines.append(f"{key} = {_toml_literal(value)}")
        for key, value in nested:
            render_section(value, f"{prefix}.{key}" if prefix else key)

    render_section(config)
    return "\n".join(lines) + ("\n" if lines else "")


def save_config(config: Mapping[str, Any], path: Path = CONFIG_FILE) -> None:
    """Persist configuration atomically and apply a user-only mode where supported."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(render_toml(config))
            handle.flush()
            os.fsync(handle.fileno())
        with suppress(OSError):
            temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()


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
    provider = cast(Mapping[str, object], llm).get("provider") if isinstance(llm, Mapping) else None
    # ChatGPT Web is an explicitly local browser bridge.  It has no model API
    # credential; the sentinel keeps the existing AgentConfig contract intact
    # without asking users to invent or store a fake secret in config.toml.
    if isinstance(provider, str) and provider.strip().lower() == "chatgpt-web":
        return "local-loopback"
    return (
        configured
        if isinstance(configured, str) and configured
        else os.environ.get("OPENAI_API_KEY") or os.environ.get("AEGIS_API_KEY")
    )


def resolve_trust_level(config: Mapping[str, Any], override: str | None = None) -> str:
    trust = config.get("trust", {})
    configured: object = cast(Mapping[str, object], trust).get("level") if isinstance(trust, Mapping) else None
    if override is not None:
        if type(override) is not str:
            raise ConfigError.invalid_trust_level(f"non-string override ({type(override).__name__})")
        selected = override
    elif configured is not None:
        if type(configured) is not str:
            raise ConfigError.invalid_trust_level(f"non-string configured value ({type(configured).__name__})")
        selected = configured
    else:
        selected = os.environ.get("AEGIS_TRUST_LEVEL") or "DEV"
    return selected.strip().upper()


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
        llm_requires_api_key = getattr(self.llm, "requires_api_key", True)
        if not self.api_key and llm_requires_api_key is not False:
            raise ConfigError.api_key_missing()
        if type(self.trust_level) is not str or self.trust_level not in VALID_TRUST_LEVELS:
            raise ConfigError.invalid_trust_level(self.trust_level)
        if type(self.browser) is not bool:
            raise ValueError("Agent browser flag must be boolean")
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise ConfigError.max_steps_invalid(self.max_steps)
