"""Application service for the canonical Agent facade."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from collections.abc import Callable, Mapping

from .config import AgentConfig
from .infrastructure import build_gateway, build_learning_manager
from .lab import LabApplication, ProcessExecutionCell
from .models import RunResult
from .observability import CorrelationContext, RuntimeTelemetry
from .prompt import PromptBuilder
from .rag import RAGManager
from .runtime import coordinated_runtime_task
from .verification import (
    AgentImplementationPacket,
    LocalVerificationCommand,
    ProjectProfile,
    VerificationFacade,
    VerificationSessionError,
    build_local_verification_commands,
    run_local_verification_command,
)
from .verification.discovery import workspace_change_paths

try:
    from core.python.aegis.conversations import ConversationManager
except ImportError:
    from aegis.conversations import ConversationManager  # type: ignore[import-not-found]


_DEVELOPMENT_TASK_MARKERS = (
    "implement",
    "implementation",
    "develop",
    "development",
    "feature",
    "fix",
    "bug",
    "refactor",
    "code",
    "source",
    "module",
    "function",
    "class",
    "test",
    "pytest",
    "cargo",
    "rust",
    "python",
    "phát triển",
    "triển khai",
    "sửa",
    "thêm",
    "mã nguồn",
    "kiểm thử",
)


def _looks_like_development_task(task: str) -> bool:
    normalized = task.casefold()
    return any(
        (
            marker in normalized
            if any(not character.isascii() for character in marker)
            else re.search(rf"\b{re.escape(marker)}\b", normalized) is not None
        )
        for marker in _DEVELOPMENT_TASK_MARKERS
    )


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
        self._verification_facade: VerificationFacade | None = None
        self._verification_session_id: str | None = None
        self._verification_packet: AgentImplementationPacket | None = None
        self._aese_profile: ProjectProfile | None = None
        self._aese_project_root: Path | None = None
        self._aese_baseline_paths: frozenset[str] = frozenset()
        self._aese_run_id: str | None = None
        self._aese_commands: dict[str, LocalVerificationCommand] = {}
        self._aese_delegate_tool_runner: Any = None

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
        if self._verification_packet is not None:
            packet = self._verification_packet
            system_context = (
                f"{system_context}\n\n"
                "AESE IMPLEMENTATION PACKET (PROVISIONAL CONTROL-PLANE CONTEXT):\n"
                f"session_id={packet.session_id}\n"
                f"source_revision={packet.source_revision}\n"
                f"requirements={json.dumps(packet.requirements, ensure_ascii=False, sort_keys=True)}\n"
                f"rules={json.dumps(packet.rules, ensure_ascii=False)}\n"
                "Do not treat provisional feedback as final assurance."
            )
        return system_context

    def _aese_enabled(self) -> bool:
        options = self.config.options
        if options.get("aese") is False:
            return False
        if options.get("aese") is True or options.get("verification") is True:
            return True
        if options.get("aese_auto", True) is not True:
            return False
        return _looks_like_development_task(self.config.task)

    def _ensure_verification_session(self) -> None:
        if not self._aese_enabled() or self._verification_session_id is not None:
            return
        raw_root = self.config.options.get("aese_project_root", str(Path.cwd()))
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise VerificationSessionError("aese_project_root must be a non-empty path")
        expected = self.config.options.get("aese_expected_behavior")
        if expected is not None and not isinstance(expected, str):
            raise VerificationSessionError("aese_expected_behavior must be a string")
        facade = VerificationFacade()
        profile = facade.inspect_project(raw_root)
        self._aese_profile = profile
        self._aese_project_root = Path(profile.root_path)
        self._aese_baseline_paths = frozenset(workspace_change_paths(self._aese_project_root))
        requirements = facade.create_contract(
            self.config.task,
            expected_behavior=expected,
            source_revision=profile.source_revision,
            policy_hash=self.config.trust_policy_hash or "UNKNOWN",
        )
        session = facade.start_session(profile, requirements)
        packet = facade.get_agent_packet(session.session_id)
        if not packet.can_start:
            raise VerificationSessionError(
                "AESE contract is incomplete; provide aese_expected_behavior before source implementation"
            )
        self._verification_facade = facade
        self._verification_session_id = session.session_id
        self._verification_packet = packet
        self.telemetry.emit(
            "verification",
            "contract_ready_before_agent_execution",
            correlation=self.correlation,
        )

    def _prepare_aese_runtime(self) -> None:
        """Attach AESE's local lane to the existing Lab tool boundary."""

        if (
            self._verification_facade is None
            or self._verification_session_id is None
            or self._verification_packet is None
            or self._aese_profile is None
            or not self._verification_packet.can_start
        ):
            return
        options = self.config.options
        execute_local = options.get("aese_execute_local", True)
        if type(execute_local) is not bool:
            raise VerificationSessionError("aese_execute_local must be boolean")
        if not execute_local:
            self.telemetry.emit("verification", "local_execution_disabled", correlation=self.correlation)
            return
        deep = options.get("aese_deep", False)
        include_rust = options.get("aese_include_rust", True)
        if type(deep) is not bool or type(include_rust) is not bool:
            raise VerificationSessionError("aese_deep and aese_include_rust must be boolean")
        commands = build_local_verification_commands(
            self._aese_profile,
            deep=deep,
            include_rust=include_rust,
        )
        if not commands:
            self.telemetry.emit("verification", "no_conformant_local_adapter", correlation=self.correlation)
            return
        if self._aese_run_id is None:
            run = self._verification_facade.request_deep_run(
                self._verification_session_id,
                reason="local_deep_lane" if deep else "local_fast_lane",
            )
            raw_run_id = run.get("run_id")
            if type(raw_run_id) is not str or not raw_run_id.strip():
                raise VerificationSessionError("AESE Lab run did not return a valid run id")
            self._aese_run_id = raw_run_id
        self._aese_commands = {command.command_id: command for command in commands}
        raw_existing_calls = options.get("tool_calls", ())
        if raw_existing_calls is None:
            existing_calls: tuple[Any, ...] = ()
        elif isinstance(raw_existing_calls, (list, tuple)):
            existing_calls = tuple(cast(list[Any] | tuple[Any, ...], raw_existing_calls))
        else:
            raise VerificationSessionError("tool_calls must be a list or tuple when AESE is active")
        self._aese_delegate_tool_runner = options.get("tool_runner")
        aese_calls = tuple(
            {
                "tool_name": "aese.local_verification",
                "call_id": f"aese-{command.command_id}",
                "effect_class": "compute",
                "actor_role": "observer",
                "expected_observation_schema": "aese.execution-result-v1",
                "stop_rule": "single_call",
                "lease_id": index,
                "input": command.as_dict(),
            }
            for index, command in enumerate(commands, start=1)
        )
        # AESE calls are placed first so a user-supplied max_steps value cannot
        # silently omit verification.  The existing Lab registry still owns
        # the one generic tool cell and the one admission/settlement path.
        options["tool_calls"] = (*aese_calls, *existing_calls)
        options["tool_runner"] = self._aese_tool_runner
        options.setdefault(
            "tool_timeout_seconds",
            min(3_600.0, max(command.timeout_seconds for command in commands) + 10.0),
        )
        self.telemetry.emit(
            "verification",
            "local_lane_bound_to_lab",
            correlation=self.correlation,
        )

    async def _aese_tool_runner(self, request: Any, **kwargs: Any) -> dict[str, object]:
        """Dispatch only pre-built AESE commands through a Lab process cell."""

        if not isinstance(request, dict):
            raise VerificationSessionError("AESE tool request must be a mapping")
        typed_request = cast(dict[str, Any], request)
        tool_name = typed_request.get("tool_name", typed_request.get("name", ""))
        if tool_name != "aese.local_verification":
            delegate = self._aese_delegate_tool_runner
            if not callable(delegate):
                raise VerificationSessionError("unregistered generic tool request")
            delegated = delegate(typed_request, **kwargs)
            return cast(dict[str, object], await delegated if inspect.isawaitable(delegated) else delegated)
        if self._aese_run_id is None or self._verification_facade is None or self._verification_session_id is None:
            raise VerificationSessionError("AESE local run is not initialized")
        input_payload = typed_request.get("input")
        if not isinstance(input_payload, Mapping):
            raise VerificationSessionError("AESE local command payload is missing")
        typed_input = cast(Mapping[str, object], input_payload)
        command_id = typed_input.get("command_id")
        if type(command_id) is not str:
            raise VerificationSessionError("AESE local command id is invalid")
        command = self._aese_commands.get(command_id)
        try:
            normalized_input = LocalVerificationCommand.from_mapping(typed_input)
        except (TypeError, ValueError) as error:
            raise VerificationSessionError("AESE local command payload is invalid") from error
        if command is None or normalized_input.as_dict() != command.as_dict():
            raise VerificationSessionError("AESE local command is not an admitted command")
        runner = ProcessExecutionCell(
            run_local_verification_command,
            timeout_seconds=min(3_600.0, command.timeout_seconds + 5.0),
        )
        lab_run = kwargs.get("run")
        try:
            raw_result = await runner(command.as_dict())
            if not isinstance(raw_result, Mapping):
                raise VerificationSessionError("AESE local runner returned a malformed result")
            result = dict(cast(Mapping[str, object], raw_result))
            result["source_revision"] = command.source_revision
            receipt = self._verification_facade.record_execution_result(
                self._verification_session_id,
                self._aese_run_id,
                result,
            )
            if receipt.status != "PASS" and lab_run is not None:
                lab_run.record_blocker(f"aese_verification_{receipt.status.casefold()}")
            return result
        except asyncio.CancelledError:
            self._record_aese_runner_failure(command, status="CANCELLED", lab_run=lab_run)
            raise
        except Exception as error:
            self._record_aese_runner_failure(command, status="ERROR", lab_run=lab_run, error=error)
            raise

    def _record_aese_runner_failure(
        self,
        command: LocalVerificationCommand,
        *,
        status: str,
        lab_run: Any,
        error: Exception | None = None,
    ) -> None:
        if self._verification_facade is None or self._verification_session_id is None or self._aese_run_id is None:
            return
        result: dict[str, object] = {
            "source_revision": command.source_revision,
            "command_id": command.command_id,
            "adapter": command.adapter,
            "framework": command.framework,
            "toolchain": "UNKNOWN",
            "platform": "UNKNOWN",
            "executable": command.executable,
            "argv": command.argv,
            "working_directory": command.working_directory,
            "environment_fingerprint": dict(command.environment),
            "timeout_seconds": command.timeout_seconds,
            "exit_semantics": type(error).__name__ if error is not None else status,
            "status": status,
            "detail": str(error)[:256] if error is not None else status,
            "started_at": "AESE_UNKNOWN_START",
            "finished_at": "AESE_UNKNOWN_FINISH",
        }
        try:
            receipt = self._verification_facade.record_execution_result(
                self._verification_session_id,
                self._aese_run_id,
                result,
            )
            if lab_run is not None:
                lab_run.record_blocker(f"aese_verification_{receipt.status.casefold()}")
        except VerificationSessionError, TypeError, ValueError:
            if lab_run is not None:
                lab_run.record_blocker("aese_verification_receipt_rejected")

    def _observe_verification_change(self) -> None:
        if self._verification_facade is None or self._verification_session_id is None:
            return
        raw_paths = self.config.options.get("aese_changed_paths")
        if raw_paths is not None:
            if not isinstance(raw_paths, (tuple, list)):
                raise VerificationSessionError("aese_changed_paths must be a list or tuple")
            typed_paths = tuple(cast(list[str] | tuple[str, ...], raw_paths))
            source_revision = None
        else:
            root = self._aese_project_root
            if root is None:
                raise VerificationSessionError("AESE project root is unavailable")
            current_profile = self._verification_facade.inspect_project(root)
            current_paths = frozenset(workspace_change_paths(root))
            typed_paths = tuple(sorted(current_paths.symmetric_difference(self._aese_baseline_paths)))
            source_revision = current_profile.source_revision
        self._verification_facade.observe_change(
            self._verification_session_id,
            typed_paths,
            source_revision=source_revision,
        )
        self.telemetry.emit("verification", "change_observed", correlation=self.correlation)

    def _publish_aese_report(self) -> None:
        if self._verification_facade is None or self._verification_session_id is None:
            return
        report = self._verification_facade.read_report(self._verification_session_id)
        raw_assessment = report.get("assessment")
        if isinstance(raw_assessment, Mapping):
            typed_assessment = cast(Mapping[str, object], raw_assessment)
            status: object = typed_assessment.get("requirement_status", "UNKNOWN")
        else:
            status = "UNKNOWN"
        self.telemetry.emit(
            "verification",
            f"report_ready:{str(status)[:96]}",
            correlation=self.correlation,
        )

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
                self._ensure_verification_session()
                self._prepare_aese_runtime()
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
                    self._publish_aese_report()
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
                if self._verification_facade is not None and self._verification_session_id is not None:
                    self._observe_verification_change()
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
