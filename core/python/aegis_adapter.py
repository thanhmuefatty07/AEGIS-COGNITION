from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from pathlib import Path
from queue import Empty, SimpleQueue
from types import SimpleNamespace
from typing import Any

try:
    from .browser_live_collector import BrowserLiveCollectorProducer
    from .browser_runtime_adapter import (
        BrowserRuntimeCapture,
        BrowserRuntimeCollectorAdapter,
        BrowserRuntimeHotFirstCapture,
    )
except ImportError:
    from browser_live_collector import BrowserLiveCollectorProducer
    from browser_runtime_adapter import (
        BrowserRuntimeCapture,
        BrowserRuntimeCollectorAdapter,
        BrowserRuntimeHotFirstCapture,
    )


try:
    from .aegis.contracts import (
        AegisBrowserActionResult,
        AegisBrowserHotFirstActionResult,
        AegisRunResult,
        HotCommitRecord,
        ProviderBudgetEvidence,
        ProviderBudgetRecord,
        ProviderRateLimitError,
        ProviderRouteRecord,
        TrustPolicySnapshot,
        normalize_correlation as _normalize_correlation,
    )
    from .aegis.hashing import stable_hash as _stable_hash, stable_u128 as _stable_u128
    from .aegis.discovery import DiscoveryError, DiscoveryResponse, discover_connection_models
    from .aegis.desktop_protocol import (
        DesktopCommandRouter,
        DesktopProtocolError,
        DesktopRequest,
        decode_request,
        encode_response,
    )
    from .aegis.connection_clients import ConnectionClientError, ConnectionResponse, OpenAICompatibleClient
    from .aegis.conversations import (
        ConversationCheckpoint,
        ConversationExecution,
        ConversationManager,
        ConversationPart,
        ConversationRecord,
        ConversationSnapshot,
        ConversationToolCall,
        ConversationTurn,
    )
    from .aegis.secrets import PlatformSecretStore, SecretStoreError
    from .aegis.evidence import (
        commit_hot_evidence,
        commit_hot_evidence_batch,
        gateway_evidence_payload as _evidence_payload,
        normalize_trust_level as _normalize_trust_level,
        trust_policy_snapshot,
    )
    from .aegis.provider import (
        invoke_with_provider_route as _invoke_with_provider_route,
        normalize_fallback_providers as _normalize_fallback_providers,
        normalize_positive_int as _normalize_positive_int,
        normalize_provider_budgets as _normalize_provider_budgets,
        provider_name as _provider_name,
        task_from_runnable_input as _task_from_runnable_input,
    )
    from .aegis.cache_economics import CacheObservation, CachePromptPlan, extract_cache_usage
except ImportError:
    from aegis.contracts import (
        AegisBrowserActionResult,
        AegisBrowserHotFirstActionResult,
        AegisRunResult,
        HotCommitRecord,
        ProviderBudgetRecord,
        ProviderBudgetEvidence,
        ProviderRateLimitError,
        ProviderRouteRecord,
        TrustPolicySnapshot,
        normalize_correlation as _normalize_correlation,
    )
    from aegis.hashing import stable_hash as _stable_hash, stable_u128 as _stable_u128
    from aegis.discovery import DiscoveryError, DiscoveryResponse, discover_connection_models
    from aegis.desktop_protocol import (
        DesktopCommandRouter,
        DesktopProtocolError,
        DesktopRequest,
        decode_request,
        encode_response,
    )
    from aegis.connection_clients import ConnectionClientError, ConnectionResponse, OpenAICompatibleClient
    from aegis.conversations import (
        ConversationCheckpoint,
        ConversationExecution,
        ConversationManager,
        ConversationPart,
        ConversationRecord,
        ConversationSnapshot,
        ConversationToolCall,
        ConversationTurn,
    )
    from aegis.secrets import PlatformSecretStore, SecretStoreError
    from aegis.evidence import (
        commit_hot_evidence,
        commit_hot_evidence_batch,
        gateway_evidence_payload as _evidence_payload,
        normalize_trust_level as _normalize_trust_level,
        trust_policy_snapshot,
    )
    from aegis.provider import (
        invoke_with_provider_route as _invoke_with_provider_route,
        normalize_fallback_providers as _normalize_fallback_providers,
        normalize_positive_int as _normalize_positive_int,
        normalize_provider_budgets as _normalize_provider_budgets,
        provider_name as _provider_name,
        task_from_runnable_input as _task_from_runnable_input,
    )
    from aegis.cache_economics import CacheObservation, CachePromptPlan, extract_cache_usage


class AegisAdapter:
    """Friendly Gateway adapter with Browser-Use/LangGraph-style run/invoke/batch entrypoints."""

    def __init__(
        self,
        task: str | None = None,
        *,
        llm: Any = None,
        model: str | None = None,
        trust_level: str | None = None,
        trust_policy_hash: str | None = None,
        provider: str | None = None,
        fallback_providers: Any = None,
        provider_budgets: Any = None,
        required_tokens: int = 1,
        correlation: Any = None,
        telemetry: Any = None,
        provider_attempt_hook: Callable[[str, Mapping[str, Any]], Any] | None = None,
        provider_egress_check: Callable[[str], Any] | None = None,
        cache_first_prompts: bool = True,
        cache_namespace: str | None = None,
        cache_prompt_ttl: str | None = None,
        **_: Any,
    ) -> None:
        self.task = task
        self.llm = llm
        self.model = model
        self.trust_level = _normalize_trust_level(trust_level)
        self.trust_policy = trust_policy_snapshot(self.trust_level)
        if trust_policy_hash is not None and trust_policy_hash != self.trust_policy.trust_policy_hash:
            raise ValueError("trust policy hash does not match trust level")
        self.trust_policy_hash = self.trust_policy.trust_policy_hash
        self.provider = provider or _provider_name(llm)
        self.fallback_providers = _normalize_fallback_providers(fallback_providers)
        self.required_tokens = _normalize_positive_int(required_tokens, "required_tokens")
        self.correlation = _normalize_correlation(correlation)
        self.telemetry = telemetry
        self.provider_attempt_hook = provider_attempt_hook
        self.provider_egress_check = provider_egress_check
        if type(cache_first_prompts) is not bool:
            raise ValueError("cache_first_prompts must be boolean")
        self.cache_first_prompts = cache_first_prompts
        self.cache_namespace = cache_namespace or "local-profile"
        self.cache_prompt_ttl = cache_prompt_ttl
        self.provider_budgets = _normalize_provider_budgets(
            provider_budgets,
            required_tokens=self.required_tokens,
        )
        self._browser_sequence_number = 0
        self.last_result: AegisRunResult | None = None
        self.last_results: tuple[AegisRunResult, ...] = ()
        self.last_batch_results: tuple[AegisRunResult, ...] = ()
        # Provider cache state is an observation only.  The adapter never
        # treats a cache hit as an authoritative answer or durable memory.
        self.last_cache_observation: CacheObservation | None = None
        self.last_cache_prompt: CachePromptPlan | None = None

    def _emit(self, kind: str, outcome: str) -> None:
        """Emit observation-only telemetry without changing gateway authority."""

        emit = getattr(self.telemetry, "emit", None)
        if not callable(emit) or self.correlation is None:
            return
        try:
            emit(kind, outcome, correlation=self.correlation)
        except Exception:
            return

    async def run(self, task: str | None = None, **kwargs: Any) -> AegisRunResult:
        effective_task = task or self.task
        if not effective_task:
            raise ValueError("Agent.run requires a task")

        # Private Lab metadata is consumed at this boundary and is never
        # forwarded to a provider adapter.  The compatibility API remains
        # unchanged for callers that do not install a fence.
        attempt_hook = kwargs.pop("_provider_attempt_hook", self.provider_attempt_hook)
        attempt_context = kwargs.pop("_provider_attempt_context", None)
        egress_check = kwargs.pop("_provider_egress_check", self.provider_egress_check)
        kwargs.pop("_aegis_usage_observer", None)
        result_usage_observer = kwargs.pop("_aegis_result_usage_observer", None)
        if result_usage_observer is not None and not callable(result_usage_observer):
            raise TypeError("_aegis_result_usage_observer must be callable")

        system_context = kwargs.get("system_context")
        cache_plan: CachePromptPlan | None = None
        if self.cache_first_prompts and isinstance(system_context, str):
            cache_plan = CachePromptPlan.from_parts(
                provider=str(self.provider or _provider_name(self.llm) or "unknown"),
                model=self.model,
                namespace=str(self.cache_namespace),
                stable_prefix=system_context,
                dynamic_suffix=effective_task,
                prompt_cache_ttl=self.cache_prompt_ttl,
            )
            kwargs["system_context"] = cache_plan.stable_prefix
            kwargs["_aegis_cache_plan"] = cache_plan
            self.last_cache_prompt = cache_plan
            self._emit("provider", "cache_prefix_ready")
        else:
            self.last_cache_prompt = None

        provider_usage_reports: SimpleQueue[Mapping[str, Any]] = SimpleQueue()

        def observe_provider_usage(usage: Mapping[str, Any]) -> None:
            provider_usage_reports.put(usage)

        self._emit("provider", "request_started")
        output, provider, provider_route, provider_budget = await _invoke_with_provider_route(
            self.llm,
            self.provider,
            self.fallback_providers,
            self.trust_level,
            effective_task,
            self.provider_budgets,
            self.required_tokens,
            attempt_hook=attempt_hook,
            attempt_context=attempt_context,
            egress_check=egress_check,
            _aegis_usage_observer=observe_provider_usage,
            **kwargs,
        )
        try:
            usage_source: object = provider_usage_reports.get_nowait()
        except Empty:
            usage_source = output
        system_context = kwargs.get("system_context")
        selected_prefix_hash = (
            cache_plan.for_provider(str(provider or self.provider or "unknown"), self.model).prefix_hash
            if cache_plan is not None
            else None
        )
        prefix_hash = (
            selected_prefix_hash
            if isinstance(system_context, str)
            else None
        )
        usage = extract_cache_usage(usage_source)
        self.last_cache_observation = CacheObservation.now(
            provider=str(provider or self.provider or "unknown"),
            model=self.model,
            prefix_hash=prefix_hash,
            usage=usage,
        )
        if callable(result_usage_observer):
            result_usage_observer(
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                }
            )
        self._emit("provider", f"cache_{self.last_cache_observation.usage.cache_status.lower()}")
        self._emit(
            "provider",
            "fallback_succeeded" if provider_route.fallback_used else "request_succeeded",
        )
        payload = _evidence_payload(
            effective_task,
            output,
            self.model,
            provider,
            self.trust_policy,
            provider_route,
            provider_budget,
        )
        hot_commit = commit_hot_evidence(payload, self.trust_level)
        self._emit("evidence", "hot_commit_completed")
        return AegisRunResult(
            schema="aegis-friendly-gateway-run-v1",
            task=effective_task,
            output=output,
            hot_commit=hot_commit,
            trust_level=self.trust_level,
            trust_policy=self.trust_policy,
            provider=provider,
            provider_route=provider_route,
            provider_budget=provider_budget,
            truth_claim=False,
            correlation=self.correlation,
        )

    def run_sync(self, task: str | None = None, **kwargs: Any) -> AegisRunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(task, **kwargs))
        raise RuntimeError("run_sync cannot be called while an event loop is already running")

    async def ainvoke(self, input: Any = None, **kwargs: Any) -> Any:
        task = _task_from_runnable_input(input, self.task)
        result = await self.run(task, **kwargs)
        self.last_result = result
        self.last_results = (*self.last_results, result)
        return result.output

    async def ainvoke_with_usage(self, input: Any = None, **kwargs: Any) -> Any:
        """Return one invocation's output and usage without reading shared last-call state."""

        if "_aegis_result_usage_observer" in kwargs:
            raise TypeError("_aegis_result_usage_observer is reserved for internal invocation tracking")
        task = _task_from_runnable_input(input, self.task)
        usage_reports: list[Mapping[str, int]] = []
        result = await self.run(
            task,
            _aegis_result_usage_observer=usage_reports.append,
            **kwargs,
        )
        self.last_result = result
        self.last_results = (*self.last_results, result)
        return SimpleNamespace(
            output=result.output,
            usage=(usage_reports[-1] if usage_reports else {}),
        )

    def invoke(self, input: Any = None, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.ainvoke(input, **kwargs))
        raise RuntimeError("invoke cannot be called while an event loop is already running")

    async def abatch(self, inputs: Any, **kwargs: Any) -> list[Any]:
        if isinstance(inputs, str | bytes) or not hasattr(inputs, "__iter__"):
            raise TypeError("Agent.abatch requires an iterable of runnable inputs")
        start = len(self.last_results)
        outputs = [await self.ainvoke(item, **kwargs) for item in inputs]
        self.last_batch_results = self.last_results[start:]
        return outputs

    def batch(self, inputs: Any, **kwargs: Any) -> list[Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.abatch(inputs, **kwargs))
        raise RuntimeError("batch cannot be called while an event loop is already running")

    def __call__(self, input: Any = None, **kwargs: Any) -> Any:
        return self.invoke(input, **kwargs)

    async def capture_browser_action(
        self,
        *,
        browser_session: Any,
        action: Any,
        output_root: str | Path | None = None,
        run_id: int | None = None,
        action_id: int | None = None,
        sequence_number: int | None = None,
        producer: BrowserLiveCollectorProducer | None = None,
        hot_evidence_publisher: Any = None,
        hot_evidence_batch_publisher: Any = None,
    ) -> AegisBrowserActionResult:
        self._emit("provider", "browser_action_started")
        if sequence_number is None:
            self._browser_sequence_number += 1
            sequence_number = self._browser_sequence_number
        run_id = run_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-run-v1",
                "task": self.task,
                "model": self.model,
                "provider": self.provider,
            }
        )
        action_id = action_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-action-v1",
                "task": self.task,
                "sequence_number": sequence_number,
            }
        )
        producer = producer or BrowserLiveCollectorProducer(
            output_root or Path("artifacts") / "browser-runtime-captures"
        )
        publisher = hot_evidence_publisher
        batch_publisher = None
        if publisher is None:
            batch_publisher = hot_evidence_batch_publisher or (
                lambda artifacts: commit_hot_evidence_batch(artifacts, self.trust_level)
            )
        adapter = BrowserRuntimeCollectorAdapter(
            producer,
            hot_evidence_publisher=publisher,
            hot_evidence_batch_publisher=batch_publisher,
        )
        capture = await adapter.capture_action(
            browser_session=browser_session,
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            action=_bind_browser_action(action, browser_session),
        )
        self._emit("provider", "browser_action_completed")
        return _browser_action_result(capture, self.trust_level, self.trust_policy)

    def capture_browser_action_sync(self, **kwargs: Any) -> AegisBrowserActionResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.capture_browser_action(**kwargs))
        raise RuntimeError("capture_browser_action_sync cannot be called while an event loop is already running")

    async def capture_browser_action_hot_first(
        self,
        *,
        browser_session: Any,
        action: Any,
        output_root: str | Path | None = None,
        run_id: int | None = None,
        action_id: int | None = None,
        sequence_number: int | None = None,
        producer: BrowserLiveCollectorProducer | None = None,
        hot_evidence_publisher: Any = None,
        hot_evidence_batch_publisher: Any = None,
    ) -> AegisBrowserHotFirstActionResult:
        self._emit("provider", "browser_hot_first_started")
        if sequence_number is None:
            self._browser_sequence_number += 1
            sequence_number = self._browser_sequence_number
        run_id = run_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-run-v1",
                "task": self.task,
                "model": self.model,
                "provider": self.provider,
            }
        )
        action_id = action_id or _stable_u128(
            {
                "schema": "aegis-friendly-browser-default-action-v1",
                "task": self.task,
                "sequence_number": sequence_number,
            }
        )
        producer = producer or BrowserLiveCollectorProducer(
            output_root or Path("artifacts") / "browser-runtime-captures"
        )
        publisher = hot_evidence_publisher
        batch_publisher = None
        if publisher is None:
            batch_publisher = hot_evidence_batch_publisher or (
                lambda artifacts: commit_hot_evidence_batch(artifacts, self.trust_level)
            )
        adapter = BrowserRuntimeCollectorAdapter(
            producer,
            hot_evidence_publisher=publisher,
            hot_evidence_batch_publisher=batch_publisher,
        )
        capture = await adapter.capture_action_hot_first(
            browser_session=browser_session,
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            action=_bind_browser_action(action, browser_session),
        )
        self._emit("provider", "browser_hot_first_completed")
        return _browser_hot_first_action_result(capture, self.trust_level, self.trust_policy)

    def capture_browser_action_hot_first_sync(self, **kwargs: Any) -> AegisBrowserHotFirstActionResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.capture_browser_action_hot_first(**kwargs))
        raise RuntimeError("capture_browser_action_hot_first_sync cannot be called while an event loop is already running")


Agent = AegisAdapter


def _bind_browser_action(action: Any, browser_session: Any) -> Any:
    if not callable(action):
        raise TypeError("browser action must be callable")
    if _accepts_browser_session(action):
        return lambda: action(browser_session)
    return action


def _accepts_browser_session(action: Any) -> bool:
    try:
        signature = inspect.signature(action)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
    return False


def _browser_action_result(
    capture: BrowserRuntimeCapture,
    trust_level: str,
    trust_policy: TrustPolicySnapshot,
) -> AegisBrowserActionResult:
    hot_evidence = capture.hot_evidence
    if hot_evidence is None:
        raise RuntimeError("friendly browser action capture requires hot evidence")
    payload = {
        "schema": "aegis-friendly-browser-action-result-v1",
        "run_id": hot_evidence.run_id,
        "action_id": hot_evidence.action_id,
        "sequence_number": hot_evidence.sequence_number,
        "hot_evidence_batch_digest": hot_evidence.batch_digest,
        "producer_metadata_path": str(capture.producer_run.metadata_path),
        "trust_level": trust_level,
        "trust_policy_hash": trust_policy.trust_policy_hash,
        "no_file_roundtrip_on_hot_path": hot_evidence.no_file_roundtrip_on_hot_path,
        "truth_claim": False,
    }
    return AegisBrowserActionResult(
        schema="aegis-friendly-browser-action-result-v1",
        capture=capture,
        hot_evidence=hot_evidence,
        trust_level=trust_level,
        trust_policy=trust_policy,
        no_file_roundtrip_on_hot_path=hot_evidence.no_file_roundtrip_on_hot_path,
        browser_action_result_hash=_stable_hash(payload),
        truth_claim=False,
    )


def _browser_hot_first_action_result(
    capture: BrowserRuntimeHotFirstCapture,
    trust_level: str,
    trust_policy: TrustPolicySnapshot,
) -> AegisBrowserHotFirstActionResult:
    payload = {
        "schema": "aegis-friendly-browser-hot-first-action-result-v1",
        "trust_level": trust_level,
        "trust_policy_hash": trust_policy.trust_policy_hash,
        "hot_evidence_batch_digest": capture.hot_evidence.batch_digest,
        "cold_publish_handle_hash": capture.cold_publish.cold_publish_handle_hash,
        "hot_returned_before_cold_publish": capture.cold_publish.hot_returned_before_cold_publish,
        "run_id": capture.hot_evidence.run_id,
        "action_id": capture.hot_evidence.action_id,
        "sequence_number": capture.hot_evidence.sequence_number,
        "truth_claim": False,
    }
    return AegisBrowserHotFirstActionResult(
        schema="aegis-friendly-browser-hot-first-action-result-v1",
        capture=capture,
        hot_evidence=capture.hot_evidence,
        trust_level=trust_level,
        trust_policy=trust_policy,
        no_file_roundtrip_on_hot_path=capture.hot_evidence.no_file_roundtrip_on_hot_path,
        hot_returned_before_cold_publish=capture.cold_publish.hot_returned_before_cold_publish,
        browser_action_result_hash=_stable_hash(payload),
        truth_claim=False,
    )


class AegisAgent(AegisAdapter):
    """
    Friendly DX wrapper around AegisAdapter.
    """
    def __init__(self, *args: Any, max_retries: int = 3, **kwargs: Any) -> None:
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 1:
            raise ValueError("max_retries must be a positive integer")
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries

    async def run(self, task: str | None = None, **kwargs: Any) -> AegisRunResult:
        retries = 0
        while True:
            try:
                return await super().run(task, **kwargs)
            except ProviderRateLimitError as exc:
                print("\u26a0\ufe0f Provider bi gioi han, AEGIS dang tu dong chuyen sang du phong...")
                retries += 1
                if retries >= self.max_retries:
                    raise exc
                await asyncio.sleep(2 ** retries)


try:
    from .aegis.connections import ConnectionCatalog, ConnectionRecord, EgressGrant, ModelDescriptor
    from .aegis.learning import (
        LearningManager,
        LearningStats,
        MemoryRecord,
        SessionSearchCandidate,
        SessionSearchResult,
        SessionSourceRecord,
    )
except ImportError:
    from aegis.connections import ConnectionCatalog, ConnectionRecord, EgressGrant, ModelDescriptor
    from aegis.learning import (
        LearningManager,
        LearningStats,
        MemoryRecord,
        SessionSearchCandidate,
        SessionSearchResult,
        SessionSourceRecord,
    )


__all__ = [
    "AegisAdapter",
    "AegisAgent",
    "AegisBrowserActionResult",
    "AegisBrowserHotFirstActionResult",
    "AegisRunResult",
    "Agent",
    "ConnectionCatalog",
    "ConnectionClientError",
    "ConnectionRecord",
    "ConnectionResponse",
    "ConversationCheckpoint",
    "ConversationExecution",
    "ConversationManager",
    "ConversationPart",
    "ConversationRecord",
    "ConversationSnapshot",
    "ConversationToolCall",
    "ConversationTurn",
    "DesktopCommandRouter",
    "DesktopProtocolError",
    "DesktopRequest",
    "DiscoveryError",
    "DiscoveryResponse",
    "EgressGrant",
    "HotCommitRecord",
    "LearningManager",
    "LearningStats",
    "MemoryRecord",
    "ModelDescriptor",
    "OpenAICompatibleClient",
    "PlatformSecretStore",
    "ProviderBudgetEvidence",
    "ProviderBudgetRecord",
    "ProviderRateLimitError",
    "ProviderRouteRecord",
    "SecretStoreError",
    "SessionSearchCandidate",
    "SessionSearchResult",
    "SessionSourceRecord",
    "TrustPolicySnapshot",
    "commit_hot_evidence",
    "commit_hot_evidence_batch",
    "decode_request",
    "discover_connection_models",
    "encode_response",
    "trust_policy_snapshot",
]
