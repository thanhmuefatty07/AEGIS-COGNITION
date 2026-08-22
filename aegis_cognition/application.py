"""Application service for the canonical Agent facade."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from collections.abc import Callable

from .config import AgentConfig
from .infrastructure import build_gateway, build_learning_manager
from .models import RunResult
from .observability import CorrelationContext, RuntimeTelemetry
from .prompt import PromptBuilder
from .rag import RAGManager


class AgentApplication:
    """Orchestrate one agent run while keeping the public facade thin."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        telemetry: RuntimeTelemetry | None = None,
        gateway_factory: Callable[..., Any] = build_gateway,
    ) -> None:
        self.config = config
        self.telemetry = telemetry or RuntimeTelemetry()
        self.correlation = CorrelationContext.new()
        self._gateway_factory = gateway_factory

    def prepare(self, task: str) -> tuple[str, str]:
        options = self.config.options
        rag_manager = RAGManager(
            top_k=int(options.get("top_k", 3)),
            learning_manager=build_learning_manager(),
        )
        rag_context = rag_manager.retrieve_and_format(task)
        builder = PromptBuilder(
            trust_level=self.config.trust_level,
            task_type=str(options.get("task_type", "R1")),
            security_level=options.get("security_level"),
            constraints=options.get("constraints"),
        )
        system_context = builder.build(
            task,
            primary_objective=options.get("primary_objective"),
            secondary_objectives=options.get("secondary_objectives"),
            output_format=options.get("output_format", "JSON"),
            output_requirements=options.get("output_requirements"),
            output_limitations=options.get("output_limitations"),
            steps=options.get("steps"),
            risks=options.get("risks"),
            error_handlers=options.get("error_handlers"),
            completion_conditions=options.get("completion_conditions"),
        )
        formatted_task = f"{task}\n\n{rag_context}" if rag_context else task
        self.telemetry.emit("memory", "retrieval_completed", correlation=self.correlation)
        self.telemetry.emit("agent", "prompt_built", correlation=self.correlation)
        return formatted_task, system_context

    def _gateway(self, formatted_task: str) -> Any:
        options = self.config.options
        gateway_options = {
            key: options[key]
            for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")
            if key in options
        }
        return self._gateway_factory(
            task=formatted_task,
            llm=self.config.llm,
            trust_level=self.config.trust_level,
            correlation=self.correlation,
            telemetry=self.telemetry,
            **gateway_options,
        )

    async def arun(self) -> RunResult:
        started = time.perf_counter()
        with self.telemetry.bind(self.correlation):
            self.telemetry.emit("agent", "run_started", correlation=self.correlation)
            try:
                formatted_task, system_context = self.prepare(self.config.task)
                result = await self._gateway(formatted_task).run(
                    formatted_task,
                    system_context=system_context,
                )
                self.telemetry.emit("evidence", "commit_completed", correlation=self.correlation)
                self._index_completed_run(self.config.task, result.output, result)
                self.telemetry.emit("agent", "run_completed", correlation=self.correlation)
                return RunResult(
                    task=result.task,
                    output=result.output,
                    trust_level=result.trust_level,
                    provider=result.provider,
                    hot_commit=result.hot_commit,
                    correlation=result.correlation or self.correlation.as_mapping(),
                )
            except Exception:
                self.telemetry.emit("agent", "run_failed", correlation=self.correlation)
                raise
            finally:
                self.telemetry.metrics.observe_ms(
                    "runtime.agent.run_duration",
                    (time.perf_counter() - started) * 1000,
                )

    def run(self) -> RunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun())
        raise RuntimeError(
            "Agent.run() cannot be called inside an async event loop. "
            "Use `await agent.arun()` or call from a sync context."
        )

    def _index_completed_run(self, task: str, output: Any, result: Any) -> None:
        try:
            manager = build_learning_manager()
            session_id = result.hot_commit.artifact_hash
            manager.index_session(session_id=session_id, content=f"Task: {task}\nOutput: {output}")
            self.telemetry.emit("memory", "index_completed", correlation=self.correlation)
        except Exception:
            self.telemetry.emit("memory", "index_degraded", correlation=self.correlation)
