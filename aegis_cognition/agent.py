"""Thin public Agent facade over the application and infrastructure boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .application import AgentApplication
from .config import AgentConfig, load_config
from .errors import ConfigError, ProviderError
from .models import RunResult
from .observability import RuntimeTelemetry
from .runtime import native_runtime_available


# Kept as compatibility aliases for callers that used the old private helpers.
_load_config = load_config


class Agent:
    """Small facade that delegates orchestration to ``AgentApplication``."""

    def __init__(
        self,
        task: str,
        *,
        llm: Any = None,
        trust_level: str | None = None,
        browser: bool = False,
        lab: bool = False,
        max_steps: int = 100,
        telemetry: RuntimeTelemetry | None = None,
        **kwargs: Any,
    ) -> None:
        if type(lab) is not bool:
            raise ValueError("Agent lab flag must be boolean")
        if type(browser) is not bool:
            raise ValueError("Agent browser flag must be boolean")
        if type(max_steps) is not int:
            raise ValueError("Agent max_steps must be an integer")
        replay_requested = kwargs.get("lab_replay_archive")
        if replay_requested is not None and type(replay_requested) is not bool:
            raise ValueError("Agent replay archive flag must be boolean")
        # Lab compatibility runs retain a durable replay snapshot by default,
        # matching the public ``Lab`` facade.  Operators can opt out with
        # ``lab_replay_archive=False`` or supply an explicit directory; the
        # archive remains a post-run evidence artifact and never widens the
        # model's execution capabilities.  A source checkout without the
        # compiled authority keeps the legacy no-archive development path;
        # explicitly requesting the archive still fails closed there.
        if lab and replay_requested is not False and (replay_requested is True or native_runtime_available()):
            configured_replay_dir = os.environ.get("AEGIS_LAB_REPLAY_DIR", "").strip()
            kwargs.setdefault(
                "lab_replay_directory",
                configured_replay_dir or str(Path.cwd() / ".aegis" / "lab-replay"),
            )
        self._config = AgentConfig.from_inputs(
            task,
            llm=llm,
            trust_level=trust_level,
            browser=browser,
            max_steps=max_steps,
            lab=lab,
            **kwargs,
        )
        self.task = self._config.task
        self.llm = self._config.llm
        self.trust_level = self._config.trust_level
        self.browser = self._config.browser
        self.lab = lab
        self.max_steps = self._config.max_steps
        self._kwargs = self._config.options
        self.telemetry = telemetry or RuntimeTelemetry()
        self._application = AgentApplication(self._config, telemetry=self.telemetry)

    def _validate(self) -> None:
        """Retain the old validation hook for source compatibility."""

        self._config.validate()

    def _prepare_rag_and_prompt(self, task: str) -> tuple[str, str]:
        return self._application.prepare(task)

    def _index_completed_run(self, task: str, output: Any, result: Any) -> None:
        self._application._index_completed_run(task, output, result)

    def run(self) -> RunResult:
        return self._application.run()

    async def arun(self) -> RunResult:
        return await self._application.arun()

    def __repr__(self) -> str:
        return f"Agent(task={self.task!r}, trust_level={self.trust_level!r})"


def run(task: str, **kwargs: Any) -> RunResult:
    """Convenience one-liner for the canonical Agent facade."""

    return Agent(task=task, **kwargs).run()


__all__ = ["Agent", "AgentConfig", "ConfigError", "ProviderError", "RunResult", "run"]
