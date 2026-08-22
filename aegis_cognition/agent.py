"""Thin public Agent facade over the application and infrastructure boundaries."""

from __future__ import annotations

from typing import Any

from .application import AgentApplication
from .config import AgentConfig, load_config
from .errors import ConfigError, ProviderError
from .models import RunResult
from .observability import RuntimeTelemetry


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
        max_steps: int = 100,
        telemetry: RuntimeTelemetry | None = None,
        **kwargs: Any,
    ) -> None:
        self._config = AgentConfig.from_inputs(
            task,
            llm=llm,
            trust_level=trust_level,
            browser=browser,
            max_steps=max_steps,
            **kwargs,
        )
        self.task = self._config.task
        self.llm = self._config.llm
        self.trust_level = self._config.trust_level
        self.browser = self._config.browser
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
