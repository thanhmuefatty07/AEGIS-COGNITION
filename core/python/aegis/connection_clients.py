"""Provider clients bound to a stored connection record.

The first adapter is the OpenAI-compatible chat protocol. It covers local
compatible servers and hosted compatible endpoints while keeping endpoint,
credential, timeout and response-size policy in one small boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .connections import ConnectionRecord
from .provider_models import provider_reasoning_profile
from .provider_registry import provider_spec_for_endpoint
from .secrets import PlatformSecretStore

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 180.0
MAX_PROVIDER_TOOLS = 256
MAX_PROVIDER_TOOL_CALLS = 64
MAX_PROVIDER_TOOL_ROUNDS = 16
MAX_PROVIDER_TOOL_SCHEMA_BYTES = 256 * 1024
MAX_PROVIDER_TOOL_HISTORY_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_TOOL_ARGUMENT_BYTES = 1024 * 1024
MAX_PROVIDER_TOOL_RESULT_BYTES = 1024 * 1024


class ConnectionClientError(RuntimeError):
    """A provider request failed at the connection boundary."""


@dataclass(frozen=True)
class ConnectionResponse:
    status: int
    body: bytes


ConnectionRequester = Callable[[str, Mapping[str, str], bytes, float], ConnectionResponse]
SecretResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class ProviderToolDefinition:
    """Provider-neutral tool metadata; names are mapped to API-safe aliases."""

    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProviderToolOutput:
    call_id: str
    output: str


@dataclass(frozen=True, slots=True)
class ProviderToolExchange:
    """One provider assistant tool turn and the matching local tool outputs."""

    assistant_state: object
    outputs: tuple[ProviderToolOutput, ...]


@dataclass(frozen=True, slots=True)
class ProviderTurn:
    text: str
    tool_calls: tuple[ProviderToolCall, ...]
    assistant_state: object
    finish_reason: str | None = None


def provider_tool_alias(name: str) -> str:
    """Return a stable provider-safe name while retaining dots in local names."""

    if type(name) is not str or not name.strip() or len(name) > 512:
        raise ValueError("tool name is invalid")
    readable = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:40] or "tool"
    suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"aegis_{readable}_{suffix}"


def _provider_tool_set(
    raw_tools: object,
    *,
    api: str,
) -> tuple[list[dict[str, object]], dict[str, str]]:
    if isinstance(raw_tools, (str, bytes)) or not isinstance(raw_tools, Sequence):
        raise ValueError("tools must be a sequence")
    if len(raw_tools) > MAX_PROVIDER_TOOLS:
        raise ValueError("provider tool catalog exceeds its limit")
    wire_tools: list[dict[str, object]] = []
    aliases: dict[str, str] = {}
    total_bytes = 0
    for raw_tool in raw_tools:
        if type(raw_tool) is not ProviderToolDefinition:
            raise ValueError("tools must contain ProviderToolDefinition values")
        tool = raw_tool
        if (
            type(tool.name) is not str
            or not tool.name.strip()
            or len(tool.name) > 512
            or type(tool.description) is not str
            or not tool.description.strip()
            or "\x00" in tool.description
            or len(tool.description) > 8_192
            or not isinstance(tool.input_schema, Mapping)
        ):
            raise ValueError("provider tool metadata is invalid")
        try:
            schema_json = json.dumps(
                tool.input_schema,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            schema_size = len(schema_json.encode("utf-8"))
            schema = json.loads(schema_json)
        except (TypeError, ValueError, UnicodeEncodeError, json.JSONDecodeError) as error:
            raise ValueError("provider tool schema must be JSON-compatible") from error
        if not isinstance(schema, dict) or schema_size > MAX_PROVIDER_TOOL_SCHEMA_BYTES:
            raise ValueError("provider tool schema exceeds its limit")
        if not schema:
            schema = {"type": "object", "properties": {}}
        total_bytes += schema_size + len(tool.description.encode("utf-8")) + len(tool.name.encode("utf-8"))
        if total_bytes > MAX_PROVIDER_TOOL_HISTORY_BYTES:
            raise ValueError("provider tool catalog exceeds its byte limit")
        alias = provider_tool_alias(tool.name)
        if alias in aliases:
            raise ValueError("provider tool aliases collided")
        aliases[alias] = tool.name
        if api == "chat-completions":
            wire_tools.append(
                {
                    "type": "function",
                    "function": {"name": alias, "description": tool.description, "parameters": schema},
                }
            )
        elif api == "responses":
            wire_tools.append(
                {"type": "function", "name": alias, "description": tool.description, "parameters": schema}
            )
        elif api == "anthropic":
            wire_tools.append({"name": alias, "description": tool.description, "input_schema": schema})
        else:
            raise ValueError("provider tool API is unsupported")
    return wire_tools, aliases


def _provider_tool_history(raw_history: object, *, api: str) -> tuple[ProviderToolExchange, ...]:
    if isinstance(raw_history, (str, bytes)) or not isinstance(raw_history, Sequence):
        raise ValueError("tool_history must be a sequence")
    if len(raw_history) > MAX_PROVIDER_TOOL_ROUNDS:
        raise ValueError("provider tool history exceeds its round limit")
    history: list[ProviderToolExchange] = []
    total_bytes = 0
    for raw_exchange in raw_history:
        if type(raw_exchange) is not ProviderToolExchange or not raw_exchange.outputs:
            raise ValueError("provider tool history contains an invalid exchange")
        if len(raw_exchange.outputs) > MAX_PROVIDER_TOOL_CALLS:
            raise ValueError("provider tool history exceeds its call limit")
        try:
            state_json = json.dumps(
                raw_exchange.assistant_state,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            state_size = len(state_json.encode("utf-8"))
            state = json.loads(state_json)
        except (TypeError, ValueError, UnicodeEncodeError, json.JSONDecodeError) as error:
            raise ValueError("provider assistant state must be JSON-compatible") from error
        if state_size > MAX_RESPONSE_BYTES:
            raise ValueError("provider assistant state exceeds its byte limit")
        seen_call_ids: set[str] = set()
        outputs: list[ProviderToolOutput] = []
        for raw_output in raw_exchange.outputs:
            if (
                type(raw_output) is not ProviderToolOutput
                or type(raw_output.call_id) is not str
                or not raw_output.call_id.strip()
                or len(raw_output.call_id) > 256
                or type(raw_output.output) is not str
                or not raw_output.output
            ):
                raise ValueError("provider tool output is invalid")
            try:
                output_size = len(raw_output.output.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise ValueError("provider tool output is not valid UTF-8 text") from error
            if output_size > MAX_PROVIDER_TOOL_RESULT_BYTES or raw_output.call_id in seen_call_ids:
                raise ValueError("provider tool output exceeds its limit or duplicates a call id")
            total_bytes += output_size
            seen_call_ids.add(raw_output.call_id)
            outputs.append(raw_output)
        total_bytes += state_size
        if total_bytes > MAX_PROVIDER_TOOL_HISTORY_BYTES:
            raise ValueError("provider tool history exceeds its byte limit")
        output_ids = [output.call_id for output in outputs]
        if api == "chat-completions":
            if not isinstance(state, Mapping) or state.get("role") != "assistant":
                raise ValueError("chat-completions history requires an assistant message")
            raw_calls = state.get("tool_calls")
            if not isinstance(raw_calls, list):
                raise ValueError("chat-completions history is missing tool calls")
            state_ids = [call.get("id") for call in raw_calls if isinstance(call, Mapping)]
        elif api == "responses":
            if type(state) is not list:
                raise ValueError("Responses history requires an output array")
            state_ids = [
                item.get("call_id")
                for item in state
                if isinstance(item, Mapping) and item.get("type") == "function_call"
            ]
        elif api == "anthropic":
            if type(state) is not list:
                raise ValueError("Anthropic history requires content blocks")
            state_ids = [
                item.get("id")
                for item in state
                if isinstance(item, Mapping) and item.get("type") == "tool_use"
            ]
        else:
            raise ValueError("provider tool history API is unsupported")
        if state_ids != output_ids:
            raise ValueError("provider tool history results do not match the assistant call order")
        history.append(ProviderToolExchange(state, tuple(outputs)))
    return tuple(history)


def _provider_tool_calls(raw_calls: object, aliases: Mapping[str, str]) -> tuple[ProviderToolCall, ...]:
    if raw_calls is None:
        return ()
    if type(raw_calls) is not list:
        raise ConnectionClientError("provider tool calls must be an array")
    if len(raw_calls) > MAX_PROVIDER_TOOL_CALLS:
        raise ConnectionClientError("provider returned too many tool calls")
    calls: list[ProviderToolCall] = []
    seen_ids: set[str] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            raise ConnectionClientError("provider tool call is invalid")
        call_id = raw_call.get("call_id", raw_call.get("id"))
        function = raw_call.get("function", raw_call)
        if not isinstance(function, Mapping):
            raise ConnectionClientError("provider tool function is invalid")
        raw_name = function.get("name")
        raw_arguments = function.get("arguments", function.get("input"))
        if type(call_id) is not str or not call_id.strip() or len(call_id) > 256 or call_id in seen_ids:
            raise ConnectionClientError("provider tool call id is invalid")
        if type(raw_name) is not str or raw_name not in aliases:
            raise ConnectionClientError("provider requested an unknown tool")
        if isinstance(raw_arguments, str):
            if len(raw_arguments.encode("utf-8")) > MAX_PROVIDER_TOOL_ARGUMENT_BYTES:
                raise ConnectionClientError("provider tool arguments exceed their byte limit")
            try:
                arguments = json.loads(raw_arguments, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            except (UnicodeEncodeError, json.JSONDecodeError, ValueError) as error:
                raise ConnectionClientError("provider tool arguments are not valid JSON") from error
        else:
            arguments = raw_arguments
        if not isinstance(arguments, Mapping):
            raise ConnectionClientError("provider tool arguments must be an object")
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_PROVIDER_TOOL_ARGUMENT_BYTES:
                raise ValueError("arguments too large")
            detached = json.loads(encoded)
        except (TypeError, ValueError, UnicodeEncodeError, json.JSONDecodeError) as error:
            raise ConnectionClientError("provider tool arguments are invalid") from error
        seen_ids.add(call_id)
        calls.append(ProviderToolCall(call_id, aliases[raw_name], detached))
    return tuple(calls)


def _responses_tool_turn(document: Mapping[str, object], aliases: Mapping[str, str]) -> ProviderTurn:
    output = document.get("output")
    if type(output) is not list:
        raise ConnectionClientError("provider response has no output")
    text_parts: list[str] = []
    raw_calls: list[Mapping[str, object]] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "message" and item.get("role") == "assistant":
            blocks = item.get("content")
            if isinstance(blocks, list):
                text_parts.extend(
                    block["text"]
                    for block in blocks
                    if isinstance(block, Mapping)
                    and block.get("type") == "output_text"
                    and isinstance(block.get("text"), str)
                )
        elif item.get("type") == "function_call":
            raw_calls.append(item)
    text = "".join(text_parts)
    calls = _provider_tool_calls(raw_calls, aliases)
    if not text and not calls:
        raise ConnectionClientError("provider response has no text or tool calls")
    return ProviderTurn(text, calls, output, document.get("status") if isinstance(document.get("status"), str) else None)


def _system_context(kwargs: Mapping[str, object]) -> str | None:
    value = kwargs.get("system_context")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("system_context must be a string")
    return value if value.strip() else None


def _max_output_tokens(kwargs: Mapping[str, object]) -> int | None:
    value = kwargs.get("max_output_tokens")
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError("max_output_tokens must be a positive integer")
    return value


def _report_usage(document: Mapping[str, object], kwargs: Mapping[str, object]) -> None:
    observer = kwargs.get("_aegis_usage_observer")
    usage = document.get("usage")
    if callable(observer) and isinstance(usage, Mapping):
        observer(usage)


def _text_only(turn: ProviderTurn) -> str:
    if turn.tool_calls:
        raise ConnectionClientError("provider returned tool calls; use invoke_turn() to handle them")
    return turn.text


class OpenAICompatibleClient:
    """Small synchronous/async client for the chat-completions contract."""

    # ``AgentConfig`` uses this capability marker to distinguish a provider
    # object that resolves credentials from the platform secret store from a
    # provider that expects the process-wide OPENAI_API_KEY contract.  The
    # connection boundary still enforces ``secret_ref`` when one is supplied.
    requires_api_key = False
    # A generic OpenAI-compatible endpoint may reject provider-specific cache
    # fields. Stable prompt ordering still helps implicit caches, but explicit
    # controls are enabled only after the provider identity is known to support
    # the OpenAI contract.
    supports_prompt_cache_controls = False
    supports_usage_observer = True

    def __init__(
        self,
        connection: ConnectionRecord,
        *,
        model: str,
        secret_resolver: SecretResolver | None = None,
        requester: ConnectionRequester | None = None,
        egress_check: Callable[[str], bool] | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not connection.enabled:
            raise ValueError("disabled connections cannot create a client")
        if connection.protocol != "chat-completions":
            raise ValueError("connection protocol is not chat-completions")
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must be a non-empty string of at most 256 bytes")
        if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be numeric")
        if timeout_seconds <= 0 or timeout_seconds > REQUEST_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0 and {REQUEST_TIMEOUT_SECONDS:g}")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes < 1
            or max_response_bytes > MAX_RESPONSE_BYTES
        ):
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self.connection = connection
        self.model = model
        provider_kind = connection.provider_kind.casefold()
        endpoint_host = urlsplit(connection.endpoint).hostname or ""
        self.supports_prompt_cache_controls = (
            provider_kind in {"openai", "openrouter"}
            and provider_spec_for_endpoint(provider_kind, endpoint_host) is not None
        )
        self._secret_resolver = secret_resolver or PlatformSecretStore().resolve
        self._requester = requester or _request_json
        self._egress_check = egress_check
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes

    def invoke(self, prompt: str, **kwargs: object) -> str:
        return _text_only(self.invoke_turn(prompt, **kwargs))

    def invoke_turn(
        self,
        prompt: str,
        *,
        tools: Sequence[ProviderToolDefinition] = (),
        tool_history: Sequence[ProviderToolExchange] = (),
        **kwargs: object,
    ) -> ProviderTurn:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        wire_tools, aliases = _provider_tool_set(tools, api="chat-completions")
        history = _provider_tool_history(tool_history, api="chat-completions")
        if history and not wire_tools:
            raise ValueError("provider tool history requires tool definitions")
        attachments = _attachment_messages(kwargs.get("attachments"), protocol="openai")
        content: object = prompt
        if attachments:
            content = [{"type": "text", "text": prompt}, *attachments]
        messages: list[dict[str, object]] = []
        system_context = _system_context(kwargs)
        if system_context is not None:
            messages.append({"role": "system", "content": system_context})
        messages.append({"role": "user", "content": content})
        for exchange in history:
            if not isinstance(exchange.assistant_state, Mapping):
                raise ValueError("chat-completions tool history requires assistant message objects")
            assistant_message = dict(exchange.assistant_state)
            if assistant_message.get("role") != "assistant":
                raise ValueError("chat-completions tool history contains a non-assistant message")
            messages.append(assistant_message)
            messages.extend(
                {"role": "tool", "tool_call_id": result.call_id, "content": result.output}
                for result in exchange.outputs
            )
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if wire_tools:
            payload["tools"] = wire_tools
        for name in (
            "temperature",
            "top_p",
            "max_tokens",
            "response_format",
            "prompt_cache_key",
            "prompt_cache_options",
            "prompt_cache_retention",
        ):
            if name in kwargs:
                payload[name] = kwargs[name]
        output_limit = _max_output_tokens(kwargs)
        if output_limit is not None:
            if "max_tokens" in kwargs or "max_completion_tokens" in kwargs:
                raise ValueError("max_output_tokens cannot be combined with a protocol-specific output limit")
            official_openai = (
                self.connection.provider_kind.casefold() == "openai"
                and (urlsplit(self.connection.endpoint).hostname or "").casefold() == "api.openai.com"
            )
            payload["max_completion_tokens" if official_openai else "max_tokens"] = output_limit
        if "reasoning_effort" in kwargs:
            effort = kwargs["reasoning_effort"]
            is_native_deepseek = (
                self.connection.provider_kind.casefold() == "deepseek"
                and (urlsplit(self.connection.endpoint).hostname or "").casefold() == "api.deepseek.com"
            )
            if is_native_deepseek and effort == "none":
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["reasoning_effort"] = effort
        response = self._request(payload)
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionClientError("provider response is not valid UTF-8 JSON") from exc
        if not isinstance(document, Mapping):
            raise ConnectionClientError("provider response must be an object")
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ConnectionClientError("provider response has no choices")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ConnectionClientError("provider response has no assistant message")
        content_value = message.get("content")
        if isinstance(content_value, str):
            text = content_value
        elif content_value is None:
            text = ""
        elif isinstance(content_value, list):
            text = "".join(
                block["text"]
                for block in content_value
                if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)
            )
        else:
            raise ConnectionClientError("provider response text content is invalid")
        calls = _provider_tool_calls(message.get("tool_calls"), aliases)
        if not text and not calls:
            raise ConnectionClientError("provider response has no text or tool calls")
        _report_usage(document, kwargs)
        state = json.loads(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        finish_reason = choice.get("finish_reason")
        return ProviderTurn(text, calls, state, finish_reason if isinstance(finish_reason, str) else None)

    async def ainvoke(self, prompt: str, **kwargs: object) -> str:
        return await asyncio.to_thread(self.invoke, prompt, **kwargs)

    def _request(self, payload: Mapping[str, object]) -> ConnectionResponse:
        url = _chat_url(self.connection.endpoint)
        endpoint_host = urlsplit(self.connection.endpoint).hostname or ""
        if self._egress_check is None and not _is_loopback_host(endpoint_host):
            raise ConnectionClientError("connection egress grant is required")
        if self._egress_check is not None:
            try:
                allowed = self._egress_check(self.connection.connection_id)
            except Exception as exc:
                raise ConnectionClientError("connection egress policy could not be evaluated") from exc
            if not isinstance(allowed, bool) or not allowed:
                raise ConnectionClientError("connection egress is denied")
        headers = _provider_headers(self.connection, self._secret_resolver)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = self._requester(url, headers, body, self._timeout_seconds)
        if not isinstance(response.status, int) or isinstance(response.status, bool):
            raise ConnectionClientError("provider response status is invalid")
        if response.status < 200 or response.status >= 300:
            raise ConnectionClientError(f"provider request returned HTTP {response.status}")
        if len(response.body) > self._max_response_bytes:
            raise ConnectionClientError("provider response exceeds the configured size limit")
        return response


class XaiResponsesClient:
    """Client for xAI's Responses API and its model-specific image format."""

    requires_api_key = False
    supports_prompt_cache_controls = False
    supports_usage_observer = True

    def __init__(
        self,
        connection: ConnectionRecord,
        *,
        model: str,
        model_capabilities: tuple[str, ...] = (),
        secret_resolver: SecretResolver | None = None,
        requester: ConnectionRequester | None = None,
        egress_check: Callable[[str], bool] | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not connection.enabled:
            raise ValueError("disabled connections cannot create a client")
        if connection.protocol != "xai-responses":
            raise ValueError("connection protocol is not xai-responses")
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must be a non-empty string of at most 256 bytes")
        if (
            not isinstance(model_capabilities, tuple)
            or len(model_capabilities) > 64
            or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in model_capabilities)
        ):
            raise ValueError("model_capabilities must be a bounded tuple of non-empty strings")
        if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be numeric")
        if timeout_seconds <= 0 or timeout_seconds > REQUEST_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0 and {REQUEST_TIMEOUT_SECONDS:g}")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes < 1
            or max_response_bytes > MAX_RESPONSE_BYTES
        ):
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self.connection = connection
        self.model = model
        self._model_capabilities = model_capabilities
        self._secret_resolver = secret_resolver or PlatformSecretStore().resolve
        self._requester = requester or _request_json
        self._egress_check = egress_check
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes

    def invoke(self, prompt: str, **kwargs: object) -> str:
        return _text_only(self.invoke_turn(prompt, **kwargs))

    def invoke_turn(
        self,
        prompt: str,
        *,
        tools: Sequence[ProviderToolDefinition] = (),
        tool_history: Sequence[ProviderToolExchange] = (),
        **kwargs: object,
    ) -> ProviderTurn:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        wire_tools, aliases = _provider_tool_set(tools, api="responses")
        history = _provider_tool_history(tool_history, api="responses")
        if history and not wire_tools:
            raise ValueError("provider tool history requires tool definitions")
        input_messages: list[dict[str, object]] = []
        system_context = _system_context(kwargs)
        if system_context is not None:
            input_messages.append({"role": "system", "content": system_context})
        content: list[dict[str, object]] = [{"type": "input_text", "text": prompt}]
        content.extend(_responses_image_inputs(kwargs.get("attachments")))
        input_messages.append({"role": "user", "content": content})
        for exchange in history:
            if type(exchange.assistant_state) is not list:
                raise ValueError("Responses tool history requires assistant output arrays")
            input_messages.extend(cast(list[dict[str, object]], exchange.assistant_state))
            input_messages.extend(
                {"type": "function_call_output", "call_id": result.call_id, "output": result.output}
                for result in exchange.outputs
            )
        payload: dict[str, object] = {
            "model": self.model,
            "input": input_messages,
            # The desktop owns conversation history. Do not retain user/project
            # content in a second provider-side history store.
            "store": False,
        }
        output_limit = _max_output_tokens(kwargs)
        if output_limit is not None:
            payload["max_output_tokens"] = output_limit
        if wire_tools:
            payload["tools"] = wire_tools
        effort = kwargs.get("reasoning_effort")
        if effort is not None:
            profile = provider_reasoning_profile(
                self.connection.provider_kind,
                self.model,
                self.connection.endpoint,
                capabilities=self._model_capabilities,
            )
            if not isinstance(effort, str) or profile is None or effort not in profile.efforts:
                raise ValueError("reasoning_effort is unsupported by the xAI Responses API")
            payload["reasoning"] = {"effort": effort}

        response = self._request(payload)
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionClientError("provider response is not valid UTF-8 JSON") from exc
        if not isinstance(document, Mapping):
            raise ConnectionClientError("provider response must be an object")
        output = _responses_tool_turn(document, aliases)
        _report_usage(document, kwargs)
        return output

    async def ainvoke(self, prompt: str, **kwargs: object) -> str:
        return await asyncio.to_thread(self.invoke, prompt, **kwargs)

    def _request(self, payload: Mapping[str, object]) -> ConnectionResponse:
        url = _responses_url(self.connection.endpoint)
        _check_egress(self.connection, self._egress_check)
        headers = _provider_headers(self.connection, self._secret_resolver)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = self._requester(url, headers, body, self._timeout_seconds)
        _validate_response(response, self._max_response_bytes)
        return response


class OpenAIResponsesClient:
    """Client for OpenAI's Responses API with model-verified reasoning controls."""

    requires_api_key = False
    supports_prompt_cache_controls = False
    supports_usage_observer = True

    def __init__(
        self,
        connection: ConnectionRecord,
        *,
        model: str,
        model_capabilities: tuple[str, ...] = (),
        secret_resolver: SecretResolver | None = None,
        requester: ConnectionRequester | None = None,
        egress_check: Callable[[str], bool] | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not connection.enabled:
            raise ValueError("disabled connections cannot create a client")
        if connection.protocol != "openai-responses":
            raise ValueError("connection protocol is not openai-responses")
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must be a non-empty string of at most 256 bytes")
        if (
            not isinstance(model_capabilities, tuple)
            or len(model_capabilities) > 64
            or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in model_capabilities)
        ):
            raise ValueError("model_capabilities must be a bounded tuple of non-empty strings")
        if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be numeric")
        if timeout_seconds <= 0 or timeout_seconds > REQUEST_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0 and {REQUEST_TIMEOUT_SECONDS:g}")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes < 1
            or max_response_bytes > MAX_RESPONSE_BYTES
        ):
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self.connection = connection
        self.model = model
        self._model_capabilities = model_capabilities
        self._secret_resolver = secret_resolver or PlatformSecretStore().resolve
        self._requester = requester or _request_json
        self._egress_check = egress_check
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self.supports_prompt_cache_controls = (
            connection.provider_kind.casefold() == "openai"
            and (urlsplit(connection.endpoint).hostname or "").casefold() == "api.openai.com"
        )

    def invoke(self, prompt: str, **kwargs: object) -> str:
        return _text_only(self.invoke_turn(prompt, **kwargs))

    def invoke_turn(
        self,
        prompt: str,
        *,
        tools: Sequence[ProviderToolDefinition] = (),
        tool_history: Sequence[ProviderToolExchange] = (),
        **kwargs: object,
    ) -> ProviderTurn:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        wire_tools, aliases = _provider_tool_set(tools, api="responses")
        history = _provider_tool_history(tool_history, api="responses")
        if history and not wire_tools:
            raise ValueError("provider tool history requires tool definitions")
        system_context = _system_context(kwargs)
        content: list[dict[str, object]] = [{"type": "input_text", "text": prompt}]
        content.extend(
            _responses_image_inputs(
                kwargs.get("attachments"),
                allowed_mime_types=("image/jpeg", "image/png", "image/webp", "image/gif"),
            )
        )
        input_messages: list[dict[str, object]] = [{"role": "user", "content": content}]
        for exchange in history:
            if type(exchange.assistant_state) is not list:
                raise ValueError("Responses tool history requires assistant output arrays")
            input_messages.extend(cast(list[dict[str, object]], exchange.assistant_state))
            input_messages.extend(
                {"type": "function_call_output", "call_id": result.call_id, "output": result.output}
                for result in exchange.outputs
            )
        payload: dict[str, object] = {
            "model": self.model,
            "input": input_messages,
            "store": False,
        }
        output_limit = _max_output_tokens(kwargs)
        if output_limit is not None:
            payload["max_output_tokens"] = output_limit
        if system_context is not None:
            payload["instructions"] = system_context
        effort = kwargs.get("reasoning_effort")
        profile = provider_reasoning_profile(
            self.connection.provider_kind,
            self.model,
            self.connection.endpoint,
            capabilities=self._model_capabilities,
        )
        if effort is not None:
            if not isinstance(effort, str) or profile is None or effort not in profile.efforts:
                raise ValueError("reasoning_effort is unsupported by the selected OpenAI model")
            payload["reasoning"] = {"effort": effort}

        if self.supports_prompt_cache_controls:
            for name in ("prompt_cache_key", "prompt_cache_options", "prompt_cache_retention"):
                if name in kwargs:
                    payload[name] = kwargs[name]
        if wire_tools:
            payload["tools"] = wire_tools

        response = self._request(payload)
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionClientError("provider response is not valid UTF-8 JSON") from exc
        if not isinstance(document, Mapping):
            raise ConnectionClientError("provider response must be an object")
        output = _responses_tool_turn(document, aliases)
        _report_usage(document, kwargs)
        return output

    async def ainvoke(self, prompt: str, **kwargs: object) -> str:
        return await asyncio.to_thread(self.invoke, prompt, **kwargs)

    def _request(self, payload: Mapping[str, object]) -> ConnectionResponse:
        url = _responses_url(self.connection.endpoint)
        _check_egress(self.connection, self._egress_check)
        headers = _provider_headers(self.connection, self._secret_resolver)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = self._requester(url, headers, body, self._timeout_seconds)
        _validate_response(response, self._max_response_bytes)
        return response


class AnthropicMessagesClient:
    """Client for Anthropic's native Messages API.

    The provider is kept separate from the OpenAI-compatible adapter so the
    authentication header and response envelope cannot be mixed accidentally.
    """

    requires_api_key = False
    supports_usage_observer = True

    def __init__(
        self,
        connection: ConnectionRecord,
        *,
        model: str,
        model_capabilities: tuple[str, ...] = (),
        secret_resolver: SecretResolver | None = None,
        requester: ConnectionRequester | None = None,
        egress_check: Callable[[str], bool] | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not connection.enabled:
            raise ValueError("disabled connections cannot create a client")
        if connection.protocol != "anthropic-messages":
            raise ValueError("connection protocol is not anthropic-messages")
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must be a non-empty string of at most 256 bytes")
        if (
            not isinstance(model_capabilities, tuple)
            or len(model_capabilities) > 64
            or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in model_capabilities)
        ):
            raise ValueError("model_capabilities must be a bounded tuple of non-empty strings")
        if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be numeric")
        if timeout_seconds <= 0 or timeout_seconds > REQUEST_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0 and {REQUEST_TIMEOUT_SECONDS:g}")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes < 1
            or max_response_bytes > MAX_RESPONSE_BYTES
        ):
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        self.connection = connection
        self.model = model
        self._model_capabilities = model_capabilities
        self._secret_resolver = secret_resolver or PlatformSecretStore().resolve
        self._requester = requester or _request_json
        self._egress_check = egress_check
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes

    def invoke(self, prompt: str, **kwargs: object) -> str:
        return _text_only(self.invoke_turn(prompt, **kwargs))

    def invoke_turn(
        self,
        prompt: str,
        *,
        tools: Sequence[ProviderToolDefinition] = (),
        tool_history: Sequence[ProviderToolExchange] = (),
        **kwargs: object,
    ) -> ProviderTurn:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        wire_tools, aliases = _provider_tool_set(tools, api="anthropic")
        history = _provider_tool_history(tool_history, api="anthropic")
        if history and not wire_tools:
            raise ValueError("provider tool history requires tool definitions")
        attachments = _attachment_messages(kwargs.get("attachments"), protocol="anthropic")
        content: object = prompt
        if attachments:
            content = [{"type": "text", "text": prompt}, *attachments]
        system_context = _system_context(kwargs)
        reasoning_effort = kwargs.get("reasoning_effort")
        profile = provider_reasoning_profile(
            self.connection.provider_kind,
            self.model,
            self.connection.endpoint,
            capabilities=self._model_capabilities,
        )
        if reasoning_effort is not None and (
            profile is None or profile.anthropic_thinking_mode is None or reasoning_effort not in profile.efforts
        ):
            raise ValueError("reasoning_effort is not supported by the selected Anthropic model")
        thinking_mode = profile.anthropic_thinking_mode if profile is not None else None
        adaptive_effort = reasoning_effort is not None and thinking_mode == "adaptive"
        effort_only = reasoning_effort is not None and thinking_mode == "effort"
        budgeted_model = thinking_mode == "budgeted"
        budget_tokens = None
        if budgeted_model and reasoning_effort is not None:
            # Opus 4.5 requires manual thinking. These are bounded local
            # presets that map the UI choices to the model's supported control.
            budget_tokens = {
                "low": 1_024,
                "medium": 4_096,
                "high": 8_192,
            }[str(reasoning_effort)]
        output_limit = _max_output_tokens(kwargs)
        if output_limit is not None and "max_tokens" in kwargs:
            raise ValueError("max_output_tokens cannot be combined with Anthropic max_tokens")
        max_tokens = (
            output_limit
            if output_limit is not None
            else kwargs.get("max_tokens", max(4096, (budget_tokens or 0) + 1024))
        )
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        if budget_tokens is not None and budget_tokens >= max_tokens:
            raise ValueError("max_tokens must exceed the selected thinking budget")
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        messages = cast(list[dict[str, object]], payload["messages"])
        for exchange in history:
            if type(exchange.assistant_state) is not list:
                raise ValueError("Anthropic tool history requires assistant content arrays")
            messages.append({"role": "assistant", "content": exchange.assistant_state})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": result.call_id, "content": result.output}
                        for result in exchange.outputs
                    ],
                }
            )
        if system_context is not None:
            payload["system"] = system_context
        if wire_tools:
            payload["tools"] = wire_tools
        if adaptive_effort:
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": reasoning_effort}
        elif effort_only:
            payload["output_config"] = {"effort": reasoning_effort}
        elif budgeted_model and budget_tokens is not None:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
            payload["output_config"] = {"effort": reasoning_effort}
        for name in ("temperature", "top_p"):
            if name in kwargs:
                payload[name] = kwargs[name]
        response = self._request(payload)
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionClientError("provider response is not valid UTF-8 JSON") from exc
        if not isinstance(document, Mapping):
            raise ConnectionClientError("provider response must be an object")
        content = document.get("content")
        if not isinstance(content, list):
            raise ConnectionClientError("provider response has no content")
        text = "".join(
            str(block.get("text"))
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
        calls = _provider_tool_calls(
            [block for block in content if isinstance(block, Mapping) and block.get("type") == "tool_use"],
            aliases,
        )
        if not text and not calls:
            raise ConnectionClientError("provider response has no text or tool calls")
        _report_usage(document, kwargs)
        reason = document.get("stop_reason")
        return ProviderTurn(text, calls, content, reason if isinstance(reason, str) else None)

    async def ainvoke(self, prompt: str, **kwargs: object) -> str:
        return await asyncio.to_thread(self.invoke, prompt, **kwargs)

    def _request(self, payload: Mapping[str, object]) -> ConnectionResponse:
        url = _messages_url(self.connection.endpoint)
        _check_egress(self.connection, self._egress_check)
        headers = _provider_headers(self.connection, self._secret_resolver)
        headers["anthropic-version"] = "2023-06-01"
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = self._requester(url, headers, body, self._timeout_seconds)
        _validate_response(response, self._max_response_bytes)
        return response


def _provider_headers(connection: ConnectionRecord, secret_resolver: SecretResolver) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if connection.secret_ref is None:
        return headers
    try:
        secret = secret_resolver(connection.secret_ref)
    except Exception as exc:
        raise ConnectionClientError("connection credential is unavailable") from exc
    if not isinstance(secret, str) or not secret:
        raise ConnectionClientError("connection credential is unavailable")
    if connection.protocol == "anthropic-messages":
        headers["x-api-key"] = secret
    else:
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _attachment_messages(value: object, *, protocol: str) -> list[dict[str, object]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise ValueError("attachments must be a list")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("attachment must be an object")
        mime_type = item.get("mime_type")
        data = item.get("data")
        if (
            not isinstance(mime_type, str)
            or not mime_type.startswith("image/")
            or not isinstance(data, str)
            or not data
        ):
            raise ValueError("attachment data is invalid")
        if protocol == "anthropic":
            result.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime_type, "data": data},
                }
            )
        else:
            result.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{data}"},
                }
            )
    return result


def _responses_image_inputs(
    value: object,
    *,
    allowed_mime_types: tuple[str, ...] = ("image/jpeg", "image/png"),
) -> list[dict[str, object]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise ValueError("attachments must be a list")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("attachment must be an object")
        mime_type = item.get("mime_type")
        data = item.get("data")
        if mime_type not in allowed_mime_types or not isinstance(data, str) or not data:
            supported = " or ".join(allowed_mime_types)
            raise ValueError(f"Responses API image inputs must use {supported}")
        result.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{data}",
            }
        )
    return result


def _responses_output_text(document: Mapping[str, object]) -> str:
    output = document.get("output")
    if not isinstance(output, list):
        raise ConnectionClientError("provider response has no output")
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message" or item.get("role") != "assistant":
            continue
        blocks = item.get("content")
        if not isinstance(blocks, list):
            continue
        text_parts.extend(
            block["text"]
            for block in blocks
            if isinstance(block, Mapping) and block.get("type") == "output_text" and isinstance(block.get("text"), str)
        )
    text = "".join(text_parts)
    if not text:
        raise ConnectionClientError("provider response has no text output")
    return text


def _check_egress(connection: ConnectionRecord, egress_check: Callable[[str], bool] | None) -> None:
    endpoint_host = urlsplit(connection.endpoint).hostname or ""
    if egress_check is None and not _is_loopback_host(endpoint_host):
        raise ConnectionClientError("connection egress grant is required")
    if egress_check is not None:
        try:
            allowed = egress_check(connection.connection_id)
        except Exception as exc:
            raise ConnectionClientError("connection egress policy could not be evaluated") from exc
        if not isinstance(allowed, bool) or not allowed:
            raise ConnectionClientError("connection egress is denied")


def _validate_response(response: ConnectionResponse, max_response_bytes: int) -> None:
    if not isinstance(response.status, int) or isinstance(response.status, bool):
        raise ConnectionClientError("provider response status is invalid")
    if response.status < 200 or response.status >= 300:
        raise ConnectionClientError(f"provider request returned HTTP {response.status}")
    if len(response.body) > max_response_bytes:
        raise ConnectionClientError("provider response exceeds the configured size limit")


def _chat_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ConnectionClientError("provider endpoint is not a supported URL")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise ConnectionClientError("provider endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname or ""):
        raise ConnectionClientError("plain HTTP requests are allowed only for loopback endpoints")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/chat/completions", "", ""))


def _messages_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ConnectionClientError("provider endpoint is not a supported URL")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise ConnectionClientError("provider endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname or ""):
        raise ConnectionClientError("plain HTTP requests are allowed only for loopback endpoints")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/messages", "", ""))


def _responses_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ConnectionClientError("provider endpoint is not a supported URL")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise ConnectionClientError("provider endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname or ""):
        raise ConnectionClientError("plain HTTP requests are allowed only for loopback endpoints")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/responses", "", ""))


def _is_loopback_host(host: str) -> bool:
    normalized = host.lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _request_json(url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> ConnectionResponse:
    request = Request(url, headers=dict(headers), data=body, method="POST")
    opener = build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return ConnectionResponse(int(response.status), response.read(MAX_RESPONSE_BYTES + 1))
    except HTTPError as exc:
        raise ConnectionClientError(f"provider request returned HTTP {exc.code}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise ConnectionClientError("provider request failed") from exc


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_301(self, _request: Request, _fp: object, _code: int, _msg: str, _headers: object) -> None:
        raise ConnectionClientError("provider redirects are not allowed")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


__all__ = [
    "AnthropicMessagesClient",
    "ConnectionClientError",
    "ConnectionResponse",
    "OpenAICompatibleClient",
    "OpenAIResponsesClient",
]
