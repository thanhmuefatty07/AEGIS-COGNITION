"""Offline wire-contract tests for every built-in provider adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from aegis_cognition.desktop_service import DesktopService
from core.python.aegis import connection_clients, discovery
from core.python.aegis.connection_clients import (
    AnthropicMessagesClient,
    ConnectionClientError,
    ConnectionResponse,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    XaiResponsesClient,
)
from core.python.aegis.connections import ConnectionRecord
from core.python.aegis.discovery import DiscoveryError, DiscoveryResponse, discover_connection_models
from core.python.aegis.provider_identity import identify_api_key
from core.python.aegis.provider_registry import PROVIDER_SPECS, ProviderSpec, provider_spec_for_host
from core.python.aegis_adapter import AegisAdapter


@dataclass(frozen=True)
class ProviderContract:
    endpoint: str
    protocol: str
    discovery_url: str
    inference_url: str
    discovery_auth_header: str
    inference_auth_header: str


_PROVIDER_CONTRACTS = {
    "anthropic": ProviderContract(
        "https://api.anthropic.com/v1",
        "anthropic-messages",
        "https://api.anthropic.com/v1/models?limit=1000",
        "https://api.anthropic.com/v1/messages",
        "x-api-key",
        "x-api-key",
    ),
    "openrouter": ProviderContract(
        "https://openrouter.ai/api/v1",
        "chat-completions",
        "https://openrouter.ai/api/v1/models/user?offset=0&limit=1000",
        "https://openrouter.ai/api/v1/chat/completions",
        "Authorization",
        "Authorization",
    ),
    "google-gemini": ProviderContract(
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "chat-completions",
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "x-goog-api-key",
        "Authorization",
    ),
    "groq": ProviderContract(
        "https://api.groq.com/openai/v1",
        "chat-completions",
        "https://api.groq.com/openai/v1/models",
        "https://api.groq.com/openai/v1/chat/completions",
        "Authorization",
        "Authorization",
    ),
    "xai": ProviderContract(
        "https://api.x.ai/v1",
        "xai-responses",
        "https://api.x.ai/v1/language-models",
        "https://api.x.ai/v1/responses",
        "Authorization",
        "Authorization",
    ),
    "perplexity": ProviderContract(
        "https://api.perplexity.ai/router/v1",
        "chat-completions",
        "https://api.perplexity.ai/router/v1/models",
        "https://api.perplexity.ai/router/v1/chat/completions",
        "Authorization",
        "Authorization",
    ),
    "nvidia-nim": ProviderContract(
        "https://integrate.api.nvidia.com/v1",
        "chat-completions",
        "https://integrate.api.nvidia.com/v1/models",
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "Authorization",
        "Authorization",
    ),
    "deepseek": ProviderContract(
        "https://api.deepseek.com",
        "chat-completions",
        "https://api.deepseek.com/models",
        "https://api.deepseek.com/chat/completions",
        "Authorization",
        "Authorization",
    ),
    "openai": ProviderContract(
        "https://api.openai.com/v1",
        "openai-responses",
        "https://api.openai.com/v1/models",
        "https://api.openai.com/v1/responses",
        "Authorization",
        "Authorization",
    ),
}

_CLIENT_BY_PROTOCOL = {
    "anthropic-messages": AnthropicMessagesClient,
    "chat-completions": OpenAICompatibleClient,
    "openai-responses": OpenAIResponsesClient,
    "xai-responses": XaiResponsesClient,
}
_TEST_MODEL = "offline-contract-model"
_TEST_PROMPT = "This is an offline provider contract test."
_KEY_HINT_CASES = {
    "anthropic": ("sk-ant-offline-test", False),
    "openrouter": ("sk-or-offline-test", False),
    "google-gemini": ("AIza-offline-test", True),
    "groq": ("gsk_offline-test", False),
    "xai": ("xai-offline-test", False),
    "perplexity": ("pplx-offline-test", False),
    "nvidia-nim": ("nvapi-offline-test", False),
    "openai": ("sk-proj-offline-test", True),
}
_PUBLIC_CATALOG_PROVIDERS = {"openrouter", "nvidia-nim"}


def _connection(spec: ProviderSpec, contract: ProviderContract) -> ConnectionRecord:
    assert spec.endpoint == contract.endpoint
    assert spec.protocol == contract.protocol
    return ConnectionRecord(
        connection_id=f"offline-test:{spec.provider_kind}",
        provider_kind=spec.provider_kind,
        endpoint=spec.endpoint,
        protocol=spec.protocol,
        secret_ref=f"offline-test:{spec.provider_kind}:credential",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )


def _catalog_payload(spec: ProviderSpec) -> dict[str, object]:
    if spec.catalog_strategy == "anthropic":
        return {"data": [{"id": _TEST_MODEL, "type": "model"}], "has_more": False}
    if spec.catalog_strategy == "google":
        return {"models": [{"name": f"models/{_TEST_MODEL}", "supportedGenerationMethods": ["generateContent"]}]}
    if spec.catalog_strategy == "openrouter":
        return {
            "data": [
                {
                    "id": _TEST_MODEL,
                    "supported_parameters": ["reasoning_effort"],
                    "reasoning": {"supported_efforts": ["low", "high", "max"]},
                }
            ]
        }
    return {"data": [{"id": _TEST_MODEL}]}


def _inference_payload(protocol: str) -> dict[str, object]:
    if protocol == "anthropic-messages":
        return {
            "content": [{"type": "text", "text": "offline answer"}],
            "usage": {
                "input_tokens": 65,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 5,
                "output_tokens": 8,
            },
        }
    if protocol in {"openai-responses", "xai-responses"}:
        return {
            "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "offline answer"}]}
            ],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 30},
                "output_tokens": 8,
            },
        }
    return {
        "choices": [{"message": {"content": "offline answer"}}],
        "usage": {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens": 8,
        },
    }


def _assert_auth_header(headers: Mapping[str, str], name: str, value: str) -> None:
    assert headers[name] == (f"Bearer {value}" if name == "Authorization" else value)


def test_provider_matrix_covers_every_builtin_provider_and_protocol():
    assert set(_PROVIDER_CONTRACTS) == {spec.provider_kind for spec in PROVIDER_SPECS}
    assert {spec.protocol for spec in PROVIDER_SPECS} == set(_CLIENT_BY_PROTOCOL)
    assert set(_KEY_HINT_CASES) == {spec.provider_kind for spec in PROVIDER_SPECS if spec.key_prefixes}
    assert {spec.provider_kind for spec in PROVIDER_SPECS if not spec.key_prefixes} == {"deepseek"}


@pytest.mark.parametrize(("provider_kind", "key_case"), _KEY_HINT_CASES.items())
def test_synthetic_key_hints_route_to_the_expected_provider_without_retaining_the_key(provider_kind, key_case):
    synthetic_key, requires_confirmation = key_case

    identity = identify_api_key(synthetic_key)

    contract = _PROVIDER_CONTRACTS[provider_kind]
    assert identity.provider_kind == provider_kind
    assert identity.endpoint == contract.endpoint
    assert identity.protocol == contract.protocol
    assert identity.requires_confirmation is requires_confirmation
    assert synthetic_key not in repr(identity.as_dict())


def test_deepseek_key_prefix_is_not_misidentified_as_deepseek():
    synthetic_key = "sk-offline-test"

    identity = identify_api_key(synthetic_key)

    assert identity.provider_kind == "openai"
    assert identity.requires_confirmation is True
    assert synthetic_key not in repr(identity.as_dict())
    deepseek = provider_spec_for_host("api.deepseek.com")
    assert deepseek is not None and deepseek.provider_kind == "deepseek"


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_desktop_service_selects_the_registered_provider_protocol(spec: ProviderSpec, tmp_path: Path):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]

    class EgressCatalog:
        def check_egress(self, *_args: object) -> bool:
            return True

    service = DesktopService(
        profile_root=tmp_path / "profile",
        connection_catalog_factory=EgressCatalog,
    )
    # Keep this routing check independent of global native-profile bindings.
    service._opened_root = tmp_path
    try:
        client = service._provider_client(_connection(spec, contract), _TEST_MODEL)

        assert type(client) is _CLIENT_BY_PROTOCOL[spec.protocol]
    finally:
        service.close()


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_each_provider_discovers_models_with_its_catalog_and_auth_contract(spec: ProviderSpec):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]
    connection = _connection(spec, contract)
    fake_secret = f"offline-test-not-a-real-key:{spec.provider_kind}"
    requests: list[tuple[str, Mapping[str, str], float]] = []
    egress_checks: list[str] = []

    def requester(url: str, headers: Mapping[str, str], timeout: float) -> DiscoveryResponse:
        requests.append((url, headers, timeout))
        return DiscoveryResponse(200, json.dumps(_catalog_payload(spec)).encode())

    models = discover_connection_models(
        connection,
        secret_resolver=lambda secret_ref: fake_secret if secret_ref == connection.secret_ref else None,
        requester=requester,
        egress_check=lambda connection_id: egress_checks.append(connection_id) is None,
    )

    assert [model["model_id"] for model in models] == [_TEST_MODEL]
    reasoning_capabilities = {
        capability for capability in models[0]["capabilities"] if capability.startswith("reasoning:")
    }
    if spec.provider_kind == "openrouter":
        assert reasoning_capabilities == {"reasoning:declared", "reasoning:low", "reasoning:high", "reasoning:max"}
    else:
        assert not reasoning_capabilities
    assert len(requests) == 1
    url, headers, timeout = requests[0]
    assert url == contract.discovery_url
    assert headers["Accept"] == "application/json"
    _assert_auth_header(headers, contract.discovery_auth_header, fake_secret)
    if spec.provider_kind == "anthropic":
        assert headers["anthropic-version"] == "2023-06-01"
    assert 0 < timeout <= 30
    assert egress_checks == [connection.connection_id]


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_keyless_model_discovery_is_public_or_fails_closed(spec: ProviderSpec):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]
    connection = replace(_connection(spec, contract), secret_ref=None)
    requests: list[tuple[str, Mapping[str, str]]] = []
    egress_checks: list[str] = []

    def requester(url: str, headers: Mapping[str, str], _timeout: float) -> DiscoveryResponse:
        requests.append((url, headers))
        if spec.provider_kind in _PUBLIC_CATALOG_PROVIDERS:
            return DiscoveryResponse(200, json.dumps(_catalog_payload(spec)).encode())
        return DiscoveryResponse(401, b'{"error":"credential required"}')

    def reject_secret_lookup(_secret_ref: str) -> str | None:
        pytest.fail("keyless discovery must not resolve a credential")

    if spec.provider_kind in _PUBLIC_CATALOG_PROVIDERS:
        models = discover_connection_models(
            connection,
            secret_resolver=reject_secret_lookup,
            requester=requester,
            egress_check=lambda connection_id: egress_checks.append(connection_id) is None,
        )
        assert [model["model_id"] for model in models] == [_TEST_MODEL]
    else:
        with pytest.raises(DiscoveryError, match="HTTP 401") as error:
            discover_connection_models(
                connection,
                secret_resolver=reject_secret_lookup,
                requester=requester,
                egress_check=lambda connection_id: egress_checks.append(connection_id) is None,
            )
        assert error.value.status_code == 401

    assert requests
    assert egress_checks == [connection.connection_id]
    assert all(
        header.casefold() not in {"authorization", "x-api-key", "x-goog-api-key"}
        for _, headers in requests
        for header in headers
    )
    expected_url = contract.discovery_url
    if spec.provider_kind == "openrouter":
        expected_url = expected_url.replace("/models/user?", "/models?", 1)
    assert requests[0][0] == expected_url


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_each_provider_runs_an_offline_inference_through_its_protocol_adapter(spec: ProviderSpec):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]
    connection = replace(_connection(spec, contract), secret_ref=None)
    requests: list[tuple[str, Mapping[str, str], bytes, float]] = []
    egress_checks: list[str] = []

    def requester(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> ConnectionResponse:
        requests.append((url, headers, body, timeout))
        return ConnectionResponse(200, json.dumps(_inference_payload(spec.protocol)).encode())

    client_class = _CLIENT_BY_PROTOCOL[spec.protocol]

    def reject_secret_lookup(_secret_ref: str) -> str | None:
        pytest.fail("keyless offline inference must not resolve a credential")

    client = client_class(
        connection,
        model=_TEST_MODEL,
        secret_resolver=reject_secret_lookup,
        requester=requester,
        egress_check=lambda connection_id: egress_checks.append(connection_id) is None,
    )

    system_context = "offline system contract"
    gateway = AegisAdapter(llm=client, provider=spec.provider_kind, model=_TEST_MODEL)
    result = asyncio.run(
        gateway.ainvoke_with_usage(_TEST_PROMPT, system_context=system_context, max_output_tokens=73)
    )
    assert result.output == "offline answer"
    assert result.usage["input_tokens"] == (65 if spec.protocol == "anthropic-messages" else 100)
    assert result.usage["output_tokens"] == 8
    assert len(requests) == 1
    url, headers, body, timeout = requests[0]
    assert url == contract.inference_url
    assert headers["Accept"] == "application/json"
    assert all(name.casefold() not in {"authorization", "x-api-key", "x-goog-api-key"} for name in headers)
    assert 0 < timeout <= 180
    assert egress_checks == [connection.connection_id]
    assert gateway.last_cache_observation is not None
    normalized_usage = gateway.last_cache_observation.usage
    assert normalized_usage.input_tokens == (65 if spec.protocol == "anthropic-messages" else 100)
    assert normalized_usage.cached_read_tokens == 30
    assert normalized_usage.cache_write_tokens == (5 if spec.protocol == "anthropic-messages" else 0)
    assert normalized_usage.output_tokens == 8
    payload = json.loads(body)
    assert "_aegis_usage_observer" not in payload
    assert payload["model"] == _TEST_MODEL
    if spec.protocol == "chat-completions":
        assert payload["messages"] == [
            {"role": "system", "content": system_context},
            {"role": "user", "content": _TEST_PROMPT},
        ]
        assert payload["stream"] is False
        assert payload["max_completion_tokens" if spec.provider_kind == "openai" else "max_tokens"] == 73
    elif spec.protocol == "anthropic-messages":
        assert payload["system"] == system_context
        assert payload["messages"] == [{"role": "user", "content": _TEST_PROMPT}]
        assert payload["max_tokens"] == 73
    elif spec.protocol == "openai-responses":
        assert payload["instructions"] == system_context
        assert payload["input"] == [{"role": "user", "content": [{"type": "input_text", "text": _TEST_PROMPT}]}]
        assert payload["store"] is False
        assert payload["max_output_tokens"] == 73
    else:
        assert payload["input"] == [
            {"role": "system", "content": system_context},
            {"role": "user", "content": [{"type": "input_text", "text": _TEST_PROMPT}]},
        ]
        assert payload["store"] is False
        assert payload["max_output_tokens"] == 73


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_desktop_connect_discover_and_chat_flow_for_every_provider(
    spec: ProviderSpec,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]
    secret = f"synthetic-explicit-provider-key:{spec.provider_kind}"
    connect_payload: dict[str, str] = {"api_key": secret, "provider_kind": spec.provider_kind}
    observed: dict[str, object] = {}

    def discovery_request(url: str, headers: Mapping[str, str], timeout: float) -> DiscoveryResponse:
        observed["discovery"] = (url, headers, timeout)
        return DiscoveryResponse(200, json.dumps(_catalog_payload(spec)).encode())

    def inference_request(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> ConnectionResponse:
        observed["inference"] = (url, headers, json.loads(body), timeout)
        return ConnectionResponse(200, json.dumps(_inference_payload(spec.protocol)).encode())

    monkeypatch.setattr(discovery, "_request_json", discovery_request)
    monkeypatch.setattr(connection_clients, "_request_json", inference_request)
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")

    def dispatch(command: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        request = {
            "schema": "aegis-desktop-command-v1",
            "protocol_version": 1,
            "request_id": "provider-contract-test",
            "command": command,
            "payload": payload or {},
        }
        return json.loads(service.dispatch(json.dumps(request).encode()))

    try:
        assert dispatch("workspace.open")["status"] == "ok"
        connected = dispatch("connections.connect", connect_payload)
        assert connected["status"] == "ok"
        connected_result = connected["result"]
        assert isinstance(connected_result, dict)
        assert connected_result["identity"]["provider_kind"] == spec.provider_kind
        assert connected_result["identity"]["confidence"] == "explicit"
        assert connected_result["record"]["provider_kind"] == spec.provider_kind
        assert connected_result["models"][0]["model_id"] == _TEST_MODEL
        assert secret not in json.dumps(connected)

        discovery_url, discovery_headers, discovery_timeout = observed["discovery"]
        assert discovery_url == contract.discovery_url
        _assert_auth_header(discovery_headers, contract.discovery_auth_header, secret)
        assert 0 < discovery_timeout <= 30

        conversation_id = f"provider-contract:{spec.provider_kind}"
        created = dispatch(
            "conversations.create",
            {
                "conversation_id": conversation_id,
                "title": "Provider contract test",
                "connection_id": "local",
                "model_id": _TEST_MODEL,
            },
        )
        assert created["status"] == "ok"
        sent = dispatch(
            "conversations.send",
            {"conversation_id": conversation_id, "message": _TEST_PROMPT, "mode": "live"},
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "offline answer"

        inference_url, inference_headers, inference_payload, inference_timeout = observed["inference"]
        assert inference_url == contract.inference_url
        _assert_auth_header(inference_headers, contract.inference_auth_header, secret)
        assert inference_payload["model"] == _TEST_MODEL
        assert 0 < inference_timeout <= 180
    finally:
        service.close()


def test_desktop_custom_openai_compatible_endpoint_connects_discovers_and_chats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    endpoint = "https://gateway.example/v1"
    secret = "offline-key-1234"
    observed: dict[str, object] = {}

    def discovery_request(url: str, headers: Mapping[str, str], timeout: float) -> DiscoveryResponse:
        observed["discovery"] = (url, headers, timeout)
        return DiscoveryResponse(200, json.dumps({"data": [{"id": _TEST_MODEL}]}).encode())

    def inference_request(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> ConnectionResponse:
        observed["inference"] = (url, headers, json.loads(body), timeout)
        return ConnectionResponse(200, json.dumps(_inference_payload("chat-completions")).encode())

    monkeypatch.setattr(discovery, "_request_json", discovery_request)
    monkeypatch.setattr(connection_clients, "_request_json", inference_request)
    monkeypatch.delenv("AEGIS_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("AEGIS_PROFILE_ID", raising=False)
    service = DesktopService(profile_root=tmp_path / "profile")

    def dispatch(command: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        request = {
            "schema": "aegis-desktop-command-v1",
            "protocol_version": 1,
            "request_id": "custom-provider-contract-test",
            "command": command,
            "payload": payload or {},
        }
        return json.loads(service.dispatch(json.dumps(request).encode()))

    try:
        assert dispatch("workspace.open")["status"] == "ok"
        connected = dispatch(
            "connections.connect",
            {"api_key": secret, "provider_kind": "openai-compatible", "endpoint": endpoint},
        )
        assert connected["status"] == "ok"
        result = connected["result"]
        assert isinstance(result, dict)
        assert result["identity"]["provider_kind"] == "openai-compatible"
        assert result["identity"]["endpoint"] == endpoint
        assert result["record"]["protocol"] == "chat-completions"
        assert result["secret_scope"] == "session"
        assert result["persistent"] is False
        assert secret not in json.dumps(connected)

        discovery_url, discovery_headers, discovery_timeout = observed["discovery"]
        assert discovery_url == f"{endpoint}/models"
        _assert_auth_header(discovery_headers, "Authorization", secret)
        assert 0 < discovery_timeout <= 30
        assert result["models"][0]["model_id"] == _TEST_MODEL

        created = dispatch(
            "conversations.create",
            {
                "conversation_id": "provider-contract:custom-compatible",
                "title": "Custom provider contract test",
                "connection_id": "local",
                "model_id": _TEST_MODEL,
            },
        )
        assert created["status"] == "ok"
        sent = dispatch(
            "conversations.send",
            {"conversation_id": "provider-contract:custom-compatible", "message": _TEST_PROMPT, "mode": "live"},
        )
        assert sent["status"] == "ok"
        assert sent["result"]["output"] == "offline answer"

        inference_url, inference_headers, inference_payload, inference_timeout = observed["inference"]
        assert inference_url == f"{endpoint}/chat/completions"
        _assert_auth_header(inference_headers, "Authorization", secret)
        assert inference_payload["model"] == _TEST_MODEL
        user_message = inference_payload["messages"][0]
        assert user_message["role"] == "user"
        assert _TEST_PROMPT in user_message["content"]
        assert secret not in json.dumps(inference_payload)
        assert 0 < inference_timeout <= 180
    finally:
        service.close()


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_provider_auth_failures_do_not_echo_credentials(spec: ProviderSpec):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]
    connection = _connection(spec, contract)
    fake_secret = f"offline-test-not-a-real-key:{spec.provider_kind}"
    client_class = _CLIENT_BY_PROTOCOL[spec.protocol]
    client = client_class(
        connection,
        model=_TEST_MODEL,
        secret_resolver=lambda _secret_ref: fake_secret,
        requester=lambda *_: ConnectionResponse(401, f"invalid credential: {fake_secret}".encode()),
        egress_check=lambda _connection_id: True,
    )

    with pytest.raises(ConnectionClientError, match="HTTP 401") as error:
        client.invoke(_TEST_PROMPT)

    assert fake_secret not in str(error.value)


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_provider_catalog_auth_failures_do_not_echo_credentials(spec: ProviderSpec):
    contract = _PROVIDER_CONTRACTS[spec.provider_kind]
    connection = _connection(spec, contract)
    fake_secret = f"offline-test-not-a-real-key:{spec.provider_kind}"

    with pytest.raises(DiscoveryError, match="HTTP 401") as error:
        discover_connection_models(
            connection,
            secret_resolver=lambda _secret_ref: fake_secret,
            requester=lambda *_: DiscoveryResponse(401, f"invalid credential: {fake_secret}".encode()),
            egress_check=lambda _connection_id: True,
        )

    assert fake_secret not in str(error.value)
