"""Application service for the canonical Agent facade."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Any
from collections.abc import Callable

from .config import AgentConfig
from .infrastructure import build_gateway, build_learning_manager
from .lab import LabApplication
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
        rag_context = self._retrieve_context(task)
        system_context = self._build_system_context(task)
        formatted_task = f"{task}\n\n{rag_context}" if rag_context else task
        self.telemetry.emit("memory", "retrieval_completed", correlation=self.correlation)
        self.telemetry.emit("agent", "prompt_built", correlation=self.correlation)
        return formatted_task, system_context

    def _retrieve_context(self, task: str) -> str:
        options = self.config.options
        rag_manager = RAGManager(
            top_k=int(options.get("top_k", 3)),
            learning_manager=build_learning_manager(),
        )
        return rag_manager.retrieve_and_format(task)

    def _build_system_context(self, task: str) -> str:
        options = self.config.options
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
        return system_context

    def _gateway(self, formatted_task: str) -> Any:
        options = self.config.options
        gateway_options = {
            key: options[key]
            for key in ("model", "provider", "fallback_providers", "provider_budgets", "required_tokens")
            if key in options
        }
        gateway_kwargs = {
            "task": formatted_task,
            "llm": self.config.llm,
            "trust_level": self.config.trust_level,
            "correlation": self.correlation,
            "telemetry": self.telemetry,
            **gateway_options,
        }
        # Direct legacy AgentConfig construction predates the mission-bound
        # hash field.  Preserve that compatibility path while every validated
        # ``from_inputs`` mission carries and propagates its hash.
        if self.config.trust_policy_hash:
            gateway_kwargs["trust_policy_hash"] = self.config.trust_policy_hash
        return self._gateway_factory(
            **gateway_kwargs,
        )

    async def arun(self) -> RunResult:
        started = time.perf_counter()
        with self.telemetry.bind(self.correlation):
            self.telemetry.emit("agent", "run_started", correlation=self.correlation)
            try:
                if self._lab_enabled():
                    # Lab owns compatibility retrieval as an admitted
                    # read-only execution cell. Prompt construction is pure
                    # and is passed to the Lab so no RAG/database read occurs
                    # before the run ledger exists.
                    system_context = self._build_system_context(self.config.task)

                    def retrieve_lab_context(*, query: str, **_: Any) -> str:
                        return self._retrieve_context(query)

                    def persist_lab_result(*, result: Any, run: Any) -> Any:
                        del run
                        return self._index_completed_run(
                            self.config.task,
                            result.output,
                            result,
                            strict=True,
                        )

                    lab_result, dossier = await LabApplication(
                        config=self.config,
                        gateway_factory=self._gateway_factory,
                        telemetry=self.telemetry,
                        correlation=self.correlation,
                        context_retriever=retrieve_lab_context,
                        system_context=system_context,
                        post_completion_effect=persist_lab_result,
                    ).run()
                    self.telemetry.emit("lab", "dossier_committed", correlation=self.correlation)
                    self.telemetry.emit("agent", "run_completed", correlation=self.correlation)
                    return RunResult(
                        task=self.config.task,
                        output=lab_result.output,
                        trust_level=lab_result.trust_level,
                        provider=lab_result.provider,
                        hot_commit=lab_result.hot_commit,
                        correlation=lab_result.correlation or self.correlation.as_mapping(),
                        lab_manifest=dossier.manifest,
                        lab_events=tuple(asdict(event) for event in dossier.events),
                    )
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

    def _lab_enabled(self) -> bool:
        options = self.config.options
        return bool(
            options.get("lab")
            or str(options.get("mode", "")).strip().lower() == "lab"
            or options.get("search_as_code")
            or options.get("researcher")
            or options.get("search_program")
            or options.get("experiment_runner")
            or options.get("simulation_runner")
            or options.get("tool_runner")
            or options.get("tool_calls")
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

    def _index_completed_run(self, task: str, output: Any, result: Any, *, strict: bool = False) -> Any:
        try:
            manager = build_learning_manager()
            # ``LearningManager.index_session`` uses a bounded native integer
            # session identity, while hot-commit artifacts are 32-byte hex
            # digests.  Bind the first 64 digest bits explicitly instead of
            # passing the full digest through its decimal-string parser.
            artifact_hash = str(result.hot_commit.artifact_hash)
            session_id = int(artifact_hash[:16], 16)
            indexed = manager.index_session(session_id=session_id, content=f"Task: {task}\nOutput: {output}")
            self.telemetry.emit("memory", "index_completed", correlation=self.correlation)
            return indexed
        except Exception:
            self.telemetry.emit("memory", "index_degraded", correlation=self.correlation)
            if strict:
                raise
            return None
