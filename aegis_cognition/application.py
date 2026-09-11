"""Application service for the canonical Agent facade."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import asdict
from typing import Any, cast
from collections.abc import Callable

from .config import AgentConfig
from .infrastructure import build_gateway, build_learning_manager
from .lab import LabApplication
from .models import RunResult
from .observability import CorrelationContext, RuntimeTelemetry
from .prompt import PromptBuilder
from .rag import RAGManager
from .runtime import coordinated_runtime_task

try:
    from core.python.aegis.conversations import ConversationManager
except ImportError:
    from aegis.conversations import ConversationManager  # type: ignore[import-not-found]


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
        raw_top_k = options.get("top_k", 3)
        if isinstance(raw_top_k, bool) or not isinstance(raw_top_k, int) or raw_top_k < 1:
            raise ValueError("context retrieval top_k must be a positive integer")
        rag_manager = RAGManager(
            top_k=raw_top_k,
            learning_manager=build_learning_manager(),
        )
        if bool(options.get("hydrate_context", False)):
            raw_budget = options.get("context_token_budget", 2048)
            if isinstance(raw_budget, bool) or not isinstance(raw_budget, int) or raw_budget < 1:
                raise ValueError("context_token_budget must be a positive integer")
            raw_mandatory = options.get("mandatory_session_ids", ())
            if not isinstance(raw_mandatory, list | tuple):
                raise ValueError("mandatory_session_ids must be a list or tuple")
            mandatory_values = cast(list[Any] | tuple[Any, ...], raw_mandatory)
            compiled = rag_manager.compile_context(
                task,
                scope_kind=str(options.get("memory_scope", "USER_PRIVATE")),
                owner_id=options.get("memory_owner_id"),
                token_budget=raw_budget,
                mandatory_session_ids=tuple(int(value) for value in mandatory_values),
            )
            if compiled.status == "compiled":
                self.telemetry.emit("memory", "context_hydrated", correlation=self.correlation)
            return compiled.rendered
        candidates = rag_manager.retrieve_candidates(task)
        if rag_manager.retrieval_error is not None:
            self.telemetry.emit("memory", "retrieval_degraded", correlation=self.correlation)
        if candidates:
            # The current learning bridge exposes candidate references only.
            # Do not append hashes/segments to the user task and present them
            # to a model as if they were hydrated evidence.  Milestone 5 will
            # replace this observation with the authorized hydration/compiler
            # pipeline.
            self.telemetry.emit("memory", "candidate_retrieval_only", correlation=self.correlation)
        return ""

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

    def _begin_conversation(self, task: str) -> dict[str, Any] | None:
        """Create the canonical user/assistant execution envelope when opted in."""

        options = self.config.options
        conversation_id = options.get("conversation_id")
        if conversation_id is None:
            return None
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        owner_id = options.get("conversation_owner_id", "local-profile")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("conversation_owner_id must be a non-empty string")
        manager = ConversationManager()
        connection_id = options.get("conversation_connection_id")
        model_id = options.get("conversation_model_id")
        try:
            snapshot = manager.read(conversation_id, owner_id=owner_id)
        except Exception as error:
            if "conversation not found" not in str(error).lower():
                raise
            if not isinstance(connection_id, str) or not connection_id.strip():
                raise ValueError("conversation_connection_id is required for a new conversation") from None
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("conversation_model_id is required for a new conversation") from None
            title = options.get("conversation_title", task[:120] or "Conversation")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("conversation_title must be a non-empty string") from None
            record = manager.create(
                conversation_id,
                owner_id=owner_id,
                title=title,
                connection_id=connection_id,
                model_id=model_id,
            )
            revision = record.revision
        else:
            record = snapshot.conversation
            if record.status != "ACTIVE":
                raise RuntimeError("conversation is not active")
            if any(turn.status in {"QUEUED", "RUNNING", "WAITING_APPROVAL"} for turn in snapshot.turns) or any(
                call.status in {"REQUESTED", "AMBIGUOUS"} for call in snapshot.tool_calls
            ):
                raise RuntimeError("conversation has unresolved execution state")
            connection_id = record.connection_id if connection_id is None else connection_id
            model_id = record.model_id if model_id is None else model_id
            if (connection_id, model_id) != (record.connection_id, record.model_id):
                raise RuntimeError("conversation selection must be changed at a safe switch boundary")
            revision = record.revision
        assert isinstance(connection_id, str)
        assert isinstance(model_id, str)
        user_turn_id = f"turn-{uuid.uuid4().hex}"
        user_turn = manager.append_turn(
            conversation_id,
            owner_id=owner_id,
            turn_id=user_turn_id,
            role="user",
            content=task,
            connection_id=connection_id,
            model_id=model_id,
            status="COMPLETED",
            expected_revision=revision,
        )
        assistant_turn_id = f"turn-{uuid.uuid4().hex}"
        assistant_turn = manager.append_turn(
            conversation_id,
            owner_id=owner_id,
            turn_id=assistant_turn_id,
            role="assistant",
            content="",
            connection_id=connection_id,
            model_id=model_id,
            status="QUEUED",
            expected_revision=user_turn.revision,
        )
        raw_provider_kind = options.get("conversation_provider_kind", options.get("provider"))
        provider_kind = raw_provider_kind if isinstance(raw_provider_kind, str) else "unknown"
        execution = manager.start_execution(
            conversation_id,
            owner_id=owner_id,
            execution_id=f"exec-{uuid.uuid4().hex}",
            turn_id=assistant_turn.turn_id,
            provider_kind=provider_kind,
            connection_id=connection_id,
            model_id=model_id,
            expected_revision=assistant_turn.revision,
        )
        return {
            "manager": manager,
            "conversation_id": conversation_id,
            "owner_id": owner_id,
            "turn_id": assistant_turn.turn_id,
            "execution_id": execution.execution_id,
            "revision": execution.revision,
        }

    @staticmethod
    def _conversation_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)

    def _finish_conversation(
        self,
        run: dict[str, Any] | None,
        *,
        output: Any = None,
        status: str,
    ) -> None:
        if run is None:
            return
        manager = run["manager"]
        revision = int(run["revision"])
        if status == "COMPLETED":
            content = self._conversation_output(output)
            if content:
                turn = manager.append_part(
                    run["conversation_id"],
                    owner_id=run["owner_id"],
                    turn_id=run["turn_id"],
                    kind="TEXT",
                    content=content,
                    expected_revision=revision,
                )
                revision = turn.revision
        manager.finish_execution(
            run["conversation_id"],
            owner_id=run["owner_id"],
            execution_id=run["execution_id"],
            status=status,
            expected_revision=revision,
        )

    async def arun(self) -> RunResult:
        started = time.perf_counter()
        with self.telemetry.bind(self.correlation):
            self.telemetry.emit("agent", "run_started", correlation=self.correlation)
            conversation_run: dict[str, Any] | None = None
            try:
                conversation_run = self._begin_conversation(self.config.task)
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

                    def persist_lab_checkpoint(*, checkpoint: dict[str, Any], run: Any) -> Any:
                        """Bind Lab progress to the same durable conversation execution."""

                        del run
                        if conversation_run is None:
                            raise RuntimeError("Lab checkpoint has no conversation execution")
                        manager = conversation_run["manager"]
                        sequence = checkpoint.get("step")
                        if type(sequence) is not int or sequence < 1:
                            raise ValueError("Lab checkpoint sequence is invalid")
                        continuation_json = json.dumps(
                            checkpoint,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                        execution = manager.checkpoint_execution(
                            conversation_run["conversation_id"],
                            owner_id=conversation_run["owner_id"],
                            execution_id=conversation_run["execution_id"],
                            sequence=sequence,
                            state="LAB_STEP_COMPLETED",
                            continuation_json=continuation_json,
                            expected_revision=conversation_run["revision"],
                        )
                        conversation_run["revision"] = execution.revision
                        return {
                            "status": "COMMITTED",
                            "sequence": execution.checkpoint_seq,
                            "revision": execution.revision,
                        }

                    lab_result, dossier = await LabApplication(
                        config=self.config,
                        gateway_factory=self._gateway_factory,
                        telemetry=self.telemetry,
                        correlation=self.correlation,
                        context_retriever=retrieve_lab_context,
                        system_context=system_context,
                        post_completion_effect=persist_lab_result,
                        checkpoint_effect=(persist_lab_checkpoint if conversation_run is not None else None),
                    ).run()
                    self.telemetry.emit("lab", "dossier_committed", correlation=self.correlation)
                    self._finish_conversation(conversation_run, output=lab_result.output, status="COMPLETED")
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
                runtime_options = self.config.options
                async with coordinated_runtime_task(
                    task_id=self.correlation.task_id,
                    work_kind="Agent",
                    timeout_seconds=runtime_options.get("runtime_timeout_seconds", 60.0),
                    memory_bytes=runtime_options.get("runtime_memory_bytes", 64 * 1024 * 1024),
                    priority="Foreground",
                    side_effect_class="ExternalSideEffect",
                    trust_level=self.config.trust_level,
                ):
                    result = await self._gateway(formatted_task).run(
                        formatted_task,
                        system_context=system_context,
                    )
                self._finish_conversation(conversation_run, output=result.output, status="COMPLETED")
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
            except asyncio.CancelledError:
                if conversation_run is not None:
                    self._finish_conversation(conversation_run, status="INTERRUPTED")
                self.telemetry.emit("agent", "run_interrupted", correlation=self.correlation)
                raise
            except Exception:
                if conversation_run is not None:
                    self._finish_conversation(conversation_run, status="FAILED")
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
            # ``LearningManager.index_session`` uses a bounded native integer
            # session identity, while hot-commit artifacts are 32-byte hex
            # digests.  Bind the first 64 digest bits explicitly instead of
            # passing the full digest through its decimal-string parser.
            artifact_hash = result.hot_commit.artifact_hash
            if not isinstance(artifact_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_hash):
                raise ValueError("completed result artifact hash must be a canonical digest")
            manager = build_learning_manager()
            session_id = int(artifact_hash[:16], 16)
            indexed = manager.index_session(session_id=session_id, content=f"Task: {task}\nOutput: {output}")
            self.telemetry.emit("memory", "index_completed", correlation=self.correlation)
            return indexed
        except Exception:
            self.telemetry.emit("memory", "index_degraded", correlation=self.correlation)
            if strict:
                raise
            return None
