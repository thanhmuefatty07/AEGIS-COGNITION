import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.python.aegis.connections import (
    ConnectionCatalog,
    ConnectionRecord,
    ModelDescriptor,
    MODEL_DISCOVERY_CACHE_TTL_SECONDS,
    max_image_inputs_for_model,
    reasoning_efforts_for_model,
    supports_vision_for_model,
)
from core.python.aegis.connection_clients import (
    AnthropicMessagesClient,
    ConnectionClientError,
    ConnectionResponse,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    XaiResponsesClient,
)
from core.python.aegis.discovery import DiscoveryError, DiscoveryResponse, discover_connection_models
from core.python.aegis.provider_identity import identify_api_key
from core.python.aegis.provider_models import provider_reasoning_profile
from core.python.aegis.secrets import PlatformSecretStore, SecretStoreError
from core.python.aegis_adapter import AegisAdapter, Agent
from aegis_cognition.desktop_service import DesktopService, DesktopServiceError, _require_provider_endpoint


def test_connection_catalog_keeps_credentials_as_opaque_references():
    bridge = MagicMock()
    bridge.aegis_upsert_connection.return_value = (
        '{"record": {"connection_id": "local", "provider_kind": "openai-compatible", '
        '"endpoint": "http://127.0.0.1:8080/v1", "protocol": "chat-completions", '
        '"secret_ref": "windows-credential:local", "enabled": true, "revision": 1, '
        '"updated_at_ms": 1700000000000}}'
    )
    catalog = ConnectionCatalog(bridge)

    record = catalog.register_connection(
        "local",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref="windows-credential:local",
        timestamp=1_700_000_000_000,
    )

    assert record.secret_ref == "windows-credential:local"
    bridge.aegis_upsert_connection.assert_called_once_with(
        "local",
        "openai-compatible",
        "http://127.0.0.1:8080/v1",
        "chat-completions",
        "windows-credential:local",
        True,
        None,
        1_700_000_000_000,
    )


def test_connection_catalog_rejects_empty_identifiers_before_native_call():
    bridge = MagicMock()
    catalog = ConnectionCatalog(bridge)

    try:
        catalog.register_connection(
            "",
            provider_kind="openai-compatible",
            endpoint="http://127.0.0.1:8080/v1",
            protocol="chat-completions",
        )
    except ValueError as error:
        assert "connection_id" in str(error)
    else:
        raise AssertionError("empty connection id must be rejected")
    bridge.aegis_upsert_connection.assert_not_called()


def test_connection_catalog_exposes_revision_bound_egress_grants():
    bridge = MagicMock()
    bridge.aegis_grant_connection_egress.return_value = (
        '{"record": {"grant_id": "grant-1", "connection_id": "local", '
        '"connection_revision": 2, "data_class": "PROJECT_TEXT", '
        '"operation": "foreground_inference", "expires_at_ms": 1800000000000, '
        '"revision": 1, "updated_at_ms": 1700000000000, "revoked_at_ms": null}}'
    )
    bridge.aegis_check_connection_egress.return_value = '{"allowed": true}'
    bridge.aegis_revoke_connection_egress.return_value = (
        '{"record": {"grant_id": "grant-1", "connection_id": "local", '
        '"connection_revision": 2, "data_class": "PROJECT_TEXT", '
        '"operation": "foreground_inference", "expires_at_ms": 1800000000000, '
        '"revision": 2, "updated_at_ms": 1700000000001, "revoked_at_ms": 1700000000001}}'
    )
    catalog = ConnectionCatalog(bridge)

    grant = catalog.grant_egress(
        "grant-1",
        "local",
        "PROJECT_TEXT",
        "foreground_inference",
        expires_at_ms=1_800_000_000_000,
        timestamp=1_700_000_000_000,
    )
    assert grant.connection_revision == 2
    assert catalog.check_egress("local", "PROJECT_TEXT", "foreground_inference", timestamp=1_700_000_000_001)
    revoked = catalog.revoke_egress("grant-1", expected_revision=1, timestamp=1_700_000_000_001)
    assert revoked.revoked_at_ms == 1_700_000_000_001
    bridge.aegis_grant_connection_egress.assert_called_once_with(
        "grant-1",
        "local",
        "PROJECT_TEXT",
        "foreground_inference",
        1_800_000_000_000,
        None,
        1_700_000_000_000,
    )


def test_provider_route_checks_egress_before_invoking_a_candidate():
    calls: list[str] = []

    async def primary(_task: str) -> object:
        calls.append("primary")
        return {"provider": "primary"}

    async def fallback(_task: str) -> object:
        calls.append("fallback")
        return {"provider": "fallback"}

    result = asyncio.run(
        Agent(
            "egress guarded",
            llm=primary,
            provider="primary",
            fallback_providers=(("fallback", fallback),),
            provider_egress_check=lambda provider: provider == "fallback",
            trust_level="DEV",
        ).run()
    )

    assert calls == ["fallback"]
    assert result.provider == "fallback"
    assert result.provider_route.egress_denied_providers == ("primary",)


def test_gateway_records_provider_reported_prompt_cache_usage_without_promoting_it():
    async def llm(_task: str, **_: object) -> object:
        return {
            "output": "answer",
            "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens": 5,
            },
        }

    gateway = AegisAdapter("cache observation", llm=llm, provider="openai", model="model")
    result = asyncio.run(gateway.run())
    assert result.output["output"] == "answer"
    assert gateway.last_cache_observation is not None
    assert gateway.last_cache_observation.usage.cached_read_tokens == 80
    assert gateway.last_cache_observation.usage.cache_status == "HIT"


def test_gateway_reuses_cache_key_for_dynamic_tasks_on_opt_in_provider_client():
    calls: list[dict[str, object]] = []

    class CacheAwareModel:
        model = "gpt-5.6"
        supports_prompt_cache_controls = True

        async def ainvoke(self, task: str, **kwargs: object) -> object:
            calls.append({"task": task, **kwargs})
            return {"output": "answer", "usage": {"prompt_tokens": 100, "completion_tokens": 5}}

    gateway = AegisAdapter(
        llm=CacheAwareModel(),
        provider="openai",
        model="gpt-5.6",
        cache_namespace="user-1",
        cache_prompt_ttl="30m",
    )
    asyncio.run(gateway.run("first", system_context="stable instructions"))
    asyncio.run(gateway.run("second", system_context="stable instructions"))

    assert calls[0]["prompt_cache_key"] == calls[1]["prompt_cache_key"]
    assert calls[0]["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert gateway.last_cache_prompt is not None
    assert calls[0]["task"] != calls[1]["task"]


def test_model_discovery_is_bounded_and_preserves_unknown_capabilities():
    connection = ConnectionRecord(
        connection_id="local",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref="env:AEGIS_TEST_KEY",
        enabled=True,
        revision=3,
        updated_at_ms=1_700_000_000_000,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, timeout: float) -> DiscoveryResponse:
        observed.update({"url": url, "headers": headers, "timeout": timeout})
        return DiscoveryResponse(
            200,
            b'{"data": [{"id": "local-model"}, {"id": "local-model"}, '
            b'{"id": "vision", "capabilities": ["vision", "vision"]}]}',
        )

    models = discover_connection_models(
        connection,
        secret_resolver=lambda ref: "secret-value" if ref == "env:AEGIS_TEST_KEY" else None,
        requester=requester,
    )

    assert observed["url"] == "http://127.0.0.1:8080/v1/models"
    assert observed["headers"] == {"Accept": "application/json", "Authorization": "Bearer secret-value"}
    assert observed["timeout"] == 30.0
    assert models[0]["capabilities"] == []
    assert models[1]["capabilities"] == ["vision"]
    assert all(model["source"] == "discovered" for model in models)


def test_model_discovery_extracts_explicit_reasoning_and_image_metadata():
    connection = ConnectionRecord(
        connection_id="local",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"model-a","input_modalities":["text","image"],'
            b'"reasoning_efforts":["minimal","low","high"]}]}',
        ),
    )

    assert set(models[0]["capabilities"]) >= {
        "vision",
        "input:image",
        "reasoning:minimal",
        "reasoning:low",
        "reasoning:high",
    }


def test_first_party_profiles_filter_non_chat_models_and_expose_exact_openai_controls():
    connection = ConnectionRecord(
        connection_id="openai",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda connection_id: connection_id == "openai",
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"gpt-5.2"},{"id":"gpt-5.5"},{"id":"gpt-5.6"},{"id":"gpt-5.3-codex"},{"id":"gpt-5-pro"},{"id":"gpt-4o"},{"id":"text-embedding-3-large"}]}',
        ),
    )

    by_id = {item["model_id"]: item for item in models}
    assert set(by_id) == {"gpt-5.2", "gpt-5.5", "gpt-5.6", "gpt-5.3-codex", "gpt-4o"}
    assert {"reasoning:none", "reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:xhigh"} <= set(
        by_id["gpt-5.2"]["capabilities"]
    )
    assert "reasoning:max" not in by_id["gpt-5.5"]["capabilities"]
    assert "reasoning:max" in by_id["gpt-5.6"]["capabilities"]
    assert {"vision", "input:image"} <= set(by_id["gpt-4o"]["capabilities"])
    assert {"reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:xhigh"} <= set(
        by_id["gpt-5.3-codex"]["capabilities"]
    )
    assert {"vision", "input:image"} <= set(by_id["gpt-5.3-codex"]["capabilities"])


def test_first_party_profiles_map_google_reasoning_by_model_family():
    connection = ConnectionRecord(
        connection_id="gemini",
        provider_kind="google-gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"models":[{"name":"models/gemini-2.5-flash","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-2.5-pro","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3.1-pro","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3.1-pro-preview","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3-pro-preview","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3-flash","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3.1-flash-lite-image","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3.5-flash","supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-3.8-flash","supportedGenerationMethods":["generateContent"]}]}',
        ),
    )

    by_id = {item["model_id"]: set(item["capabilities"]) for item in models}
    assert {
        "vision",
        "input:image",
        "reasoning:none",
        "reasoning:minimal",
        "reasoning:low",
        "reasoning:medium",
        "reasoning:high",
    } <= by_id["gemini-2.5-flash"]
    assert "reasoning:none" not in by_id["gemini-2.5-pro"]
    assert by_id["gemini-3.1-pro"].issuperset({"reasoning:low", "reasoning:medium", "reasoning:high"})
    assert "reasoning:minimal" not in by_id["gemini-3.1-pro"]
    assert "reasoning:minimal" not in by_id["gemini-3.1-pro-preview"]
    assert {"reasoning:low", "reasoning:high"} <= by_id["gemini-3-pro-preview"]
    assert "reasoning:medium" not in by_id["gemini-3-pro-preview"]
    assert by_id["gemini-3-flash"].issuperset(
        {"reasoning:minimal", "reasoning:low", "reasoning:medium", "reasoning:high"}
    )
    assert {"reasoning:minimal", "reasoning:high"} <= by_id["gemini-3.1-flash-lite-image"]
    assert not by_id["gemini-3.1-flash-lite-image"].intersection({"reasoning:low", "reasoning:medium"})
    assert {"reasoning:minimal", "reasoning:low", "reasoning:medium", "reasoning:high"} <= by_id["gemini-3.5-flash"]
    assert {"reasoning:low", "reasoning:medium", "reasoning:high"} <= by_id["gemini-3.8-flash"]


def test_gemini_native_catalog_paginates_and_overrides_model_reasoning_profile():
    connection = ConnectionRecord(
        connection_id="gemini",
        provider_kind="google-gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        protocol="chat-completions",
        secret_ref="session:gemini",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    requests: list[tuple[str, object]] = []

    def requester(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        requests.append((url, headers))
        if "pageToken=next-page" in url:
            return DiscoveryResponse(
                200,
                b'{"models":[{"name":"models/unknown-thinking-model","thinking":true,'
                b'"supportedGenerationMethods":["generateContent"]}]}',
            )
        return DiscoveryResponse(
            200,
            b'{"models":[{"name":"models/gemini-3.8-flash","thinking":true,'
            b'"supportedGenerationMethods":["generateContent"],"inputTokenLimit":1000,"outputTokenLimit":200},'
            b'{"name":"models/gemini-2.5-flash","thinking":false,'
            b'"supportedGenerationMethods":["generateContent"]},'
            b'{"name":"models/gemini-embedding-001","supportedGenerationMethods":["embedContent"]}],'
            b'"nextPageToken":"next-page"}',
        )

    models = discover_connection_models(
        connection,
        secret_resolver=lambda _reference: "gemini-secret",
        egress_check=lambda _connection_id: True,
        requester=requester,
    )
    by_id = {
        item["model_id"]: ModelDescriptor.from_mapping(
            {
                "connection_id": "gemini",
                **item,
                "revision": 1,
                "observed_at_ms": 1,
            }
        )
        for item in models
    }

    assert set(by_id) == {"gemini-3.8-flash", "gemini-2.5-flash", "unknown-thinking-model"}
    assert by_id["gemini-3.8-flash"].reasoning_efforts == ("low", "medium", "high")
    assert by_id["gemini-3.8-flash"].context_limit == 1000
    assert by_id["gemini-2.5-flash"].reasoning_efforts == ()
    assert by_id["unknown-thinking-model"].reasoning_efforts == ()
    assert len(requests) == 2
    assert all(url.startswith("https://generativelanguage.googleapis.com/v1beta/models?") for url, _ in requests)
    assert all(headers == {"Accept": "application/json", "x-goog-api-key": "gemini-secret"} for _, headers in requests)


def test_gemini_native_catalog_rejects_a_repeated_next_page_token():
    connection = ConnectionRecord(
        connection_id="gemini-repeated-page",
        provider_kind="google-gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        protocol="chat-completions",
        secret_ref="session:gemini",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    requests: list[str] = []

    def requester(url: str, _headers: object, _timeout: float) -> DiscoveryResponse:
        requests.append(url)
        return DiscoveryResponse(
            200,
            b'{"models":[{"name":"models/gemini-3.8-flash"}],"nextPageToken":"repeat-token"}',
        )

    with pytest.raises(DiscoveryError, match="pagination token is invalid"):
        discover_connection_models(
            connection,
            secret_resolver=lambda _reference: "gemini-secret",
            egress_check=lambda _connection_id: True,
            requester=requester,
        )

    assert len(requests) == 2
    assert "pageToken=repeat-token" not in requests[0]
    assert "pageToken=repeat-token" in requests[1]


def test_openai_profiles_are_exact_and_unknown_models_need_explicit_catalog_metadata():
    official = ConnectionRecord(
        connection_id="openai",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    unknown = discover_connection_models(
        official,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"gpt-5.7"},{"id":"gpt-5.6-sol"},{"id":"gpt-5.6-terra"},{"id":"gpt-5.6-luna"},{"id":"gpt-6-astra"},{"id":"gpt-6-sol"},{"id":"gpt-6-luna"}]}',
        ),
    )
    by_id = {model["model_id"]: model for model in unknown}
    assert not any(item.startswith("reasoning:") for item in by_id["gpt-5.7"]["capabilities"])
    assert "vision" not in by_id["gpt-5.7"]["capabilities"]
    expected_gpt56_capabilities = {
        "reasoning:none",
        "reasoning:low",
        "reasoning:medium",
        "reasoning:high",
        "reasoning:xhigh",
        "reasoning:max",
        "vision",
        "input:image",
    }
    for model_id in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert expected_gpt56_capabilities <= set(by_id[model_id]["capabilities"])
    for model_id in ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna"):
        assert {
            "vision",
            "input:image",
            "reasoning:declared",
        }.isdisjoint(by_id[model_id]["capabilities"])
        assert not any(item.startswith("reasoning:") for item in by_id[model_id]["capabilities"])

    gateway = ConnectionRecord(
        connection_id="gateway",
        provider_kind="openai",
        endpoint="https://gateway.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    unprofiled = discover_connection_models(
        gateway,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"gpt-6-astra"}]}'),
    )
    assert not any(item.startswith("reasoning:") for item in unprofiled[0]["capabilities"])
    assert "vision" not in unprofiled[0]["capabilities"]


def test_openai_unknown_model_uses_explicit_catalog_capabilities():
    connection = ConnectionRecord(
        connection_id="openai",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"future-model","input_modalities":["text","image"],"reasoning_efforts":["low","high"]}]}',
        ),
    )

    capabilities = set(models[0]["capabilities"])
    assert {"vision", "input:image", "reasoning:low", "reasoning:high"} <= capabilities
    assert "reasoning:medium" not in capabilities


def test_gateway_catalog_reads_nested_modalities_and_reasoning_parameter_metadata():
    connection = ConnectionRecord(
        connection_id="openrouter",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed.update({"url": url, "headers": headers})
        return DiscoveryResponse(
            200,
            b'{"data":[{"id":"provider/vision-reasoner","architecture":{"input_modalities":["text","image"]},"supported_parameters":["reasoning","tools"]}]}',
        )

    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=requester,
    )
    capabilities = set(models[0]["capabilities"])
    assert {"vision", "input:image", "reasoning", "tool_calling:declared"} <= capabilities
    assert observed["url"] == "https://openrouter.ai/api/v1/models?offset=0&limit=1000"
    assert observed["headers"] == {"Accept": "application/json"}


def test_openrouter_catalog_does_not_guess_effort_values_from_parameter_support():
    connection = ConnectionRecord(
        connection_id="openrouter",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"custom/gateway-reasoning-model","architecture":{"input_modalities":["text","image"]},"supported_parameters":["reasoning_effort"]}]}',
        ),
    )

    capabilities = set(models[0]["capabilities"])
    assert {"vision", "input:image", "reasoning"} <= capabilities
    assert not any(capability.startswith("reasoning:") for capability in capabilities)
    assert "tool_calling:declared" not in capabilities


def test_openrouter_catalog_uses_explicit_per_model_reasoning_efforts():
    connection = ConnectionRecord(
        connection_id="openrouter",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"provider/reasoning-model","supported_parameters":["reasoning_effort"],"reasoning":{"mandatory":false,"supported_efforts":["low","high","max"]}}]}',
        ),
    )

    capabilities = set(models[0]["capabilities"])
    assert {"reasoning:declared", "reasoning:low", "reasoning:high", "reasoning:max"} <= capabilities
    assert "reasoning:medium" not in capabilities


def test_catalog_rejects_unbounded_per_model_reasoning_values():
    connection = ConnectionRecord(
        connection_id="openrouter",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )

    with pytest.raises(DiscoveryError, match="reasoning metadata exceeds"):
        discover_connection_models(
            connection,
            egress_check=lambda _connection_id: True,
            requester=lambda *_: DiscoveryResponse(
                200,
                json.dumps(
                    {"data": [{"id": "model-with-invalid-catalog", "reasoning": {"supported_efforts": ["x"] * 65}}]}
                ).encode(),
            ),
        )


def test_openrouter_model_catalog_follows_offset_pages(monkeypatch):
    connection = ConnectionRecord(
        connection_id="openrouter",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref="session:openrouter",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    monkeypatch.setattr("core.python.aegis.discovery.OPENROUTER_MODELS_PAGE_SIZE", 2)
    observed: list[tuple[str, object]] = []

    def requester(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed.append((url, headers))
        body = b'{"data":[{"id":"model-a"},{"id":"model-b"}]}' if "offset=0" in url else b'{"data":[{"id":"model-c"}]}'
        return DiscoveryResponse(200, body)

    models = discover_connection_models(
        connection,
        secret_resolver=lambda _reference: "secret-value",
        egress_check=lambda _connection_id: True,
        requester=requester,
    )

    assert [item["model_id"] for item in models] == ["model-a", "model-b", "model-c"]
    assert [url for url, _headers in observed] == [
        "https://openrouter.ai/api/v1/models/user?offset=0&limit=2",
        "https://openrouter.ai/api/v1/models/user?offset=2&limit=2",
    ]
    assert all(
        headers == {"Accept": "application/json", "Authorization": "Bearer secret-value"} for _, headers in observed
    )


def test_openrouter_keyless_catalog_follows_offset_pages_without_resolving_a_secret(monkeypatch):
    connection = ConnectionRecord(
        connection_id="openrouter-public",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    monkeypatch.setattr("core.python.aegis.discovery.OPENROUTER_MODELS_PAGE_SIZE", 2)
    observed: list[tuple[str, object]] = []

    def requester(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed.append((url, headers))
        body = b'{"data":[{"id":"model-a"},{"id":"model-b"}]}' if "offset=0" in url else b'{"data":[{"id":"model-c"}]}'
        return DiscoveryResponse(200, body)

    models = discover_connection_models(
        connection,
        secret_resolver=lambda _reference: pytest.fail("keyless catalog discovery must not resolve a secret"),
        egress_check=lambda _connection_id: True,
        requester=requester,
    )

    assert [item["model_id"] for item in models] == ["model-a", "model-b", "model-c"]
    assert [url for url, _headers in observed] == [
        "https://openrouter.ai/api/v1/models?offset=0&limit=2",
        "https://openrouter.ai/api/v1/models?offset=2&limit=2",
    ]
    assert all(headers == {"Accept": "application/json"} for _, headers in observed)


def test_openrouter_regional_endpoint_uses_regional_catalog():
    connection = ConnectionRecord(
        connection_id="openrouter-eu",
        provider_kind="openrouter",
        endpoint="https://eu.openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda url, _headers, _timeout: (
            observed.update({"url": url}) or DiscoveryResponse(200, b'{"data":[{"id":"regional-model"}]}')
        ),
    )

    assert models[0]["model_id"] == "regional-model"
    assert observed["url"] == "https://eu.openrouter.ai/api/v1/models?offset=0&limit=1000"


def test_xai_reasoning_profiles_match_documented_model_specific_efforts():
    connection = ConnectionRecord(
        connection_id="xai",
        provider_kind="xai",
        endpoint="https://api.x.ai/v1",
        protocol="xai-responses",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"models":[{"id":"grok-4.5"},{"id":"grok-4.6"},{"id":"grok-4.7"},{"id":"grok-4.8"}]}',
        ),
    )
    by_id = {
        item["model_id"]: ModelDescriptor.from_mapping(
            {
                "connection_id": "xai",
                **item,
                "revision": 1,
                "observed_at_ms": 1,
            }
        ).reasoning_efforts
        for item in models
    }

    assert by_id["grok-4.5"] == ("low", "medium", "high")
    assert by_id["grok-4.6"] == ("low", "medium", "high", "xhigh")
    assert by_id["grok-4.7"] == ("low", "medium", "high", "xhigh")
    assert by_id["grok-4.8"] == ()


def test_xai_model_catalog_exposes_exact_nested_reasoning_efforts():
    connection = ConnectionRecord(
        connection_id="xai",
        provider_kind="xai",
        endpoint="https://api.x.ai/v1",
        protocol="xai-responses",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, _timeout: float) -> DiscoveryResponse:
        observed.update({"url": url, "headers": headers})
        return DiscoveryResponse(
            200,
            json.dumps(
                {
                    "models": [
                        {
                            "id": "grok-4.6",
                            "input_modalities": ["text", "image"],
                            "context_length": 256000,
                            "capabilities": {"reasoning_effort": ["low", "medium", "high", "xhigh"]},
                        }
                    ]
                }
            ).encode(),
        )

    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=requester,
    )

    capabilities = set(models[0]["capabilities"])
    assert {"reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:xhigh"} <= capabilities
    assert "reasoning:max" not in capabilities
    assert {"vision", "input:image"} <= capabilities
    assert models[0]["context_limit"] == 256000
    assert observed["url"] == "https://api.x.ai/v1/language-models"


def test_groq_catalog_uses_model_specific_documented_reasoning_efforts():
    connection = ConnectionRecord(
        connection_id="groq",
        provider_kind="groq",
        endpoint="https://api.groq.com/openai/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    discovered = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"openai/gpt-oss-20b"},{"id":"openai/gpt-oss-120b"},'
            b'{"id":"qwen/qwen3.8-27b"},{"id":"unknown/reasoner"}]}',
        ),
    )
    by_id = {
        item["model_id"]: ModelDescriptor.from_mapping(
            {
                "connection_id": "groq",
                **item,
                "revision": 1,
                "observed_at_ms": 1,
            }
        )
        for item in discovered
    }

    assert by_id["openai/gpt-oss-20b"].reasoning_efforts == ("low", "medium", "high")
    assert by_id["openai/gpt-oss-120b"].reasoning_efforts == ("low", "medium", "high")
    assert by_id["qwen/qwen3.8-27b"].reasoning_efforts == ("none", "low", "medium", "high")
    assert by_id["unknown/reasoner"].reasoning_efforts == ()


@pytest.mark.parametrize(
    ("model_id", "expected_efforts"),
    (
        ("openai/gpt-oss-20b", ("low", "medium", "high")),
        ("openai/gpt-oss-120b", ("low", "medium", "high")),
        ("meta/muse-glimmer-30b", ("none", "minimal", "low", "medium", "high", "max")),
        ("moonshotai/kimi-k3", ("low", "high", "max")),
        ("mistralai/mistral-small-4-119b-2603", ("none", "high")),
        ("nvidia/nemotron-3-super-120b-a12b", ("none", "low", "high")),
        ("z-ai/glm-5.3", ("low", "high", "max")),
        ("z-ai/glm-5.3-flash", ("low", "high", "max")),
        ("deepseek-ai/deepseek-v4-flash", ("none", "high", "max")),
    ),
)
def test_nvidia_nim_profiles_match_current_model_contract_and_chat_wire(
    model_id: str,
    expected_efforts: tuple[str, ...],
):
    connection = ConnectionRecord(
        connection_id="nvidia-nim",
        provider_kind="nvidia-nim",
        endpoint="https://integrate.api.nvidia.com/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            json.dumps({"data": [{"id": model_id}]}).encode(),
        ),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "nvidia-nim",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )

    assert descriptor.reasoning_efforts == expected_efforts
    for effort in expected_efforts:
        assert DesktopService._reasoning_effort({"reasoning_effort": effort}, model=descriptor) == effort
    with pytest.raises(DesktopServiceError, match="supports only"):
        DesktopService._reasoning_effort({"reasoning_effort": "default"}, model=descriptor)

    observed: dict[str, object] = {}

    def requester(url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "body": json.loads(body)})
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"ready"}}]}')

    client = OpenAICompatibleClient(
        connection,
        model=model_id,
        egress_check=lambda _connection_id: True,
        requester=requester,
    )
    for effort in expected_efforts:
        assert client.invoke("hello", reasoning_effort=effort) == "ready"
        assert observed["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert observed["body"]["reasoning_effort"] == effort


def test_nvidia_nim_reasoning_profiles_do_not_apply_to_custom_endpoints():
    connection = ConnectionRecord(
        connection_id="custom-nim-gateway",
        provider_kind="nvidia-nim",
        endpoint="https://gateway.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"openai/gpt-oss-20b"}]}'),
    )

    assert reasoning_efforts_for_model(SimpleNamespace(capabilities=tuple(models[0]["capabilities"]))) == ()


@pytest.mark.parametrize(
    ("model_id", "supports_vision"),
    (
        ("deepseek-v4-flash", True),
        ("deepseek-v4-flash-vision-exp", True),
        ("deepseek-v4-pro", False),
    ),
)
def test_deepseek_v4_profiles_match_the_current_effort_contract(model_id: str, supports_vision: bool):
    connection = ConnectionRecord(
        connection_id="deepseek",
        provider_kind="deepseek",
        endpoint="https://api.deepseek.com",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, json.dumps({"data": [{"id": model_id}]}).encode()),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "deepseek",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )

    assert descriptor.reasoning_efforts == ("none", "low", "high", "max")
    assert descriptor.supports_vision is supports_vision
    for effort in descriptor.reasoning_efforts:
        assert DesktopService._reasoning_effort({"reasoning_effort": effort}, model=descriptor) == effort


def test_deepseek_live_catalog_metadata_overrides_profiles_and_preserves_model_limits():
    connection = ConnectionRecord(
        connection_id="deepseek",
        provider_kind="deepseek",
        endpoint="https://api.deepseek.com",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"deepseek-flash","name":"DeepSeek-V4.1-Flash",'
            b'"context_window":1048576,"max_output_tokens":393216,'
            b'"input_modalities":["text","image"],"output_modalities":["text"],'
            b'"effort":{"supported_levels":["low","high","max"],"default_level":"high"}},'
            b'{"id":"deepseek-v4-pro","name":"DeepSeek-V4-Pro",'
            b'"context_window":1048576,"max_output_tokens":393216,'
            b'"input_modalities":["text"],"output_modalities":["text"],'
            b'"effort":{"supported_levels":["low","high","max"],"default_level":"high"}}]}',
        ),
    )
    by_id = {
        model["model_id"]: ModelDescriptor.from_mapping(
            {
                "connection_id": "deepseek",
                **model,
                "revision": 1,
                "observed_at_ms": 1,
            }
        )
        for model in models
    }

    flash = by_id["deepseek-flash"]
    pro = by_id["deepseek-v4-pro"]
    assert flash.family == "DeepSeek-V4.1-Flash"
    assert flash.context_limit == pro.context_limit == 1_048_576
    assert flash.output_limit == pro.output_limit == 393_216
    assert flash.reasoning_efforts == pro.reasoning_efforts == ("none", "low", "high", "max")
    assert flash.supports_vision
    assert not pro.supports_vision
    profile = provider_reasoning_profile("deepseek", "deepseek-flash", connection.endpoint, flash.capabilities)
    assert profile is not None and profile.efforts == flash.reasoning_efforts


def test_deepseek_catalog_declared_effort_replaces_stale_static_levels():
    connection = ConnectionRecord(
        connection_id="deepseek",
        provider_kind="deepseek",
        endpoint="https://api.deepseek.com",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    model = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"deepseek-flash","effort":{"supported_levels":["low"]}}]}',
        ),
    )[0]

    assert {"reasoning:declared", "reasoning:none", "reasoning:low"} <= set(model["capabilities"])
    assert not {"reasoning:high", "reasoning:max"} & set(model["capabilities"])


def test_deepseek_none_effort_uses_the_chat_completions_thinking_toggle():
    connection = ConnectionRecord(
        connection_id="deepseek",
        provider_kind="deepseek",
        endpoint="https://api.deepseek.com",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed["body"] = json.loads(body)
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"ready"}}]}')

    client = OpenAICompatibleClient(
        connection,
        model="deepseek-v4-flash",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )

    assert client.invoke("hello", reasoning_effort="none") == "ready"
    assert observed["body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in observed["body"]

    assert client.invoke("hello", reasoning_effort="high") == "ready"
    assert observed["body"]["reasoning_effort"] == "high"
    assert "thinking" not in observed["body"]


@pytest.mark.parametrize("model_id", ("meta/muse-glimmer-30b", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash"))
def test_nvidia_nim_vision_profiles_reach_the_chat_image_wire(model_id: str):
    connection = ConnectionRecord(
        connection_id="nvidia-nim",
        provider_kind="nvidia-nim",
        endpoint="https://integrate.api.nvidia.com/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, json.dumps({"data": [{"id": model_id}]}).encode()),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "nvidia-nim",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )

    assert supports_vision_for_model(descriptor)
    assert {"vision", "input:image", "capability:provider-profile"} <= set(descriptor.capabilities)
    assert "vision:inferred" not in descriptor.capabilities

    observed: dict[str, object] = {}

    def requester(url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "body": json.loads(body)})
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"image understood"}}]}')

    client = OpenAICompatibleClient(
        connection,
        model=model_id,
        egress_check=lambda _connection_id: True,
        requester=requester,
    )
    assert (
        client.invoke(
            "describe this image",
            attachments=[{"name": "sample.png", "mime_type": "image/png", "data": "aGVsbG8="}],
        )
        == "image understood"
    )
    assert observed["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert observed["body"]["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
    ]


def test_nvidia_nim_vision_profiles_do_not_apply_to_custom_endpoints():
    connection = ConnectionRecord(
        connection_id="custom-nim-gateway",
        provider_kind="nvidia-nim",
        endpoint="https://gateway.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"meta/muse-glimmer-30b"}]}'),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "custom-nim-gateway",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )

    assert not supports_vision_for_model(descriptor)


def test_openai_catalog_includes_gpt_53_codex_with_documented_reasoning_options():
    connection = ConnectionRecord(
        connection_id="openai",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"gpt-5.3-codex"}]}'),
    )

    assert len(models) == 1
    assert models[0]["model_id"] == "gpt-5.3-codex"
    assert {"vision", "input:image", "reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:xhigh"} <= set(
        models[0]["capabilities"]
    )


def test_explicit_catalog_efforts_override_stale_provider_profile():
    connection = ConnectionRecord(
        connection_id="xai",
        provider_kind="xai",
        endpoint="https://api.x.ai/v1",
        protocol="xai-responses",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"grok-4.6","reasoning_efforts":["low"]}]}',
        ),
    )

    capabilities = set(models[0]["capabilities"])
    assert "reasoning:declared" in capabilities
    assert "reasoning:low" in capabilities
    assert not {"reasoning:medium", "reasoning:high", "reasoning:xhigh"} & capabilities


def test_xai_responses_client_maps_images_reasoning_and_extracts_only_answer_text():
    connection = ConnectionRecord(
        connection_id="xai",
        provider_kind="xai",
        endpoint="https://api.x.ai/v1",
        protocol="xai-responses",
        secret_ref="session:xai",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "headers": headers, "body": json.loads(body)})
        return ConnectionResponse(
            200,
            b'{"output":[{"type":"reasoning","summary":[{"type":"summary_text","text":"private thought"}]},{"type":"message","role":"assistant","content":[{"type":"output_text","text":"image understood"}]}]}',
        )

    client = XaiResponsesClient(
        connection,
        model="grok-4.6",
        secret_resolver=lambda ref: "xai-secret" if ref == "session:xai" else None,
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    assert (
        client.invoke(
            "Describe this",
            system_context="xAI system contract",
            reasoning_effort="xhigh",
            attachments=[{"name": "image.png", "mime_type": "image/png", "data": "aGVsbG8="}],
        )
        == "image understood"
    )
    assert observed["url"] == "https://api.x.ai/v1/responses"
    assert observed["headers"]["Authorization"] == "Bearer xai-secret"
    assert observed["body"] == {
        "model": "grok-4.6",
        "input": [
            {"role": "system", "content": "xAI system contract"},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
                ],
            },
        ],
        "store": False,
        "reasoning": {"effort": "xhigh"},
    }


def test_openai_responses_client_maps_reasoning_images_cache_and_hides_reasoning_output():
    connection = ConnectionRecord(
        connection_id="openai",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="openai-responses",
        secret_ref="session:openai",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "headers": headers, "body": json.loads(body)})
        return ConnectionResponse(
            200,
            b'{"output":[{"type":"reasoning","summary":[{"type":"summary_text","text":"private thought"}]},{"type":"message","role":"assistant","content":[{"type":"output_text","text":"image understood"}]}]}',
        )

    client = OpenAIResponsesClient(
        connection,
        model="gpt-5.6-sol",
        secret_resolver=lambda ref: "openai-secret" if ref == "session:openai" else None,
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    assert client.supports_prompt_cache_controls is True
    assert (
        client.invoke(
            "Describe this",
            system_context="OpenAI system contract",
            reasoning_effort="high",
            prompt_cache_key="aegis-cache-test",
            prompt_cache_options={"mode": "implicit", "ttl": "30m"},
            attachments=[{"name": "image.webp", "mime_type": "image/webp", "data": "aGVsbG8="}],
        )
        == "image understood"
    )
    assert observed["url"] == "https://api.openai.com/v1/responses"
    assert observed["headers"]["Authorization"] == "Bearer openai-secret"
    assert observed["body"] == {
        "model": "gpt-5.6-sol",
        "instructions": "OpenAI system contract",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe this"},
                    {"type": "input_image", "image_url": "data:image/webp;base64,aGVsbG8="},
                ],
            }
        ],
        "store": False,
        "reasoning": {"effort": "high"},
        "prompt_cache_key": "aegis-cache-test",
        "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
    }


def test_openai_responses_client_rejects_effort_not_advertised_for_model():
    connection = ConnectionRecord(
        connection_id="openai",
        provider_kind="openai",
        endpoint="https://api.openai.com/v1",
        protocol="openai-responses",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    client = OpenAIResponsesClient(
        connection,
        model="gpt-5.6-sol",
        requester=lambda *_: pytest.fail("unsupported reasoning effort must not reach the provider"),
        egress_check=lambda _connection_id: True,
    )
    with pytest.raises(ValueError, match="unsupported by the selected OpenAI model"):
        client.invoke("hello", reasoning_effort="minimal")


def test_model_name_heuristic_does_not_enable_unverified_vision():
    connection = ConnectionRecord(
        connection_id="gateway",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"gemini-2.5-flash"}]}'),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "gateway",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )
    assert "vision:inferred" in descriptor.capabilities
    assert supports_vision_for_model(descriptor) is False


def test_groq_qwen_catalog_profile_exposes_vision_and_documented_image_limit():
    connection = ConnectionRecord(
        connection_id="groq",
        provider_kind="groq",
        endpoint="https://api.groq.com/openai/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"qwen/qwen3.8-27b"}]}'),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "groq",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )
    assert supports_vision_for_model(descriptor) is True
    assert max_image_inputs_for_model(descriptor) == 3


@pytest.mark.parametrize(
    ("reported_vision", "expected_vision"),
    [(True, True), (False, False)],
)
def test_openai_compatible_catalog_honors_boolean_vision_capabilities(reported_vision, expected_vision):
    connection = ConnectionRecord(
        connection_id="gateway",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        requester=lambda *_: DiscoveryResponse(
            200,
            json.dumps(
                {
                    "data": [
                        {
                            "id": "mistral-medium-3-5",
                            "capabilities": {"completion_chat": True, "vision": reported_vision},
                        }
                    ],
                }
            ).encode(),
        ),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "gateway",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )
    assert supports_vision_for_model(descriptor) is expected_vision


def test_anthropic_adaptive_profile_accepts_hyphenated_model_ids():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"claude-sonnet-4-6"}],"has_more":false,"last_id":"claude-sonnet-4-6"}',
        ),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "anthropic",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )
    assert descriptor.supports_vision is True
    assert descriptor.reasoning_efforts == ("low", "medium", "high", "max")


def test_anthropic_model_catalog_exposes_effort_and_image_support_per_model():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(
            200,
            b'{"data":[{"id":"claude-opus-4-6"},{"id":"claude-opus-4-7"},{"id":"claude-sonnet-5"},{"id":"claude-fable-5-1"}],"has_more":false,"last_id":"claude-fable-5-1"}',
        ),
    )
    by_id = {item["model_id"]: set(item["capabilities"]) for item in models}

    assert {"reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:max"} <= by_id["claude-opus-4-6"]
    assert "reasoning:xhigh" not in by_id["claude-opus-4-6"]
    assert {"reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:xhigh", "reasoning:max"} <= by_id[
        "claude-opus-4-7"
    ]
    for model_id in ("claude-sonnet-5", "claude-fable-5-1"):
        assert {"reasoning:low", "reasoning:medium", "reasoning:high", "reasoning:xhigh", "reasoning:max"} <= by_id[
            model_id
        ]
        assert {"vision", "input:image"} <= by_id[model_id]


def test_anthropic_model_catalog_accepts_structured_capability_metadata():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    payload = {
        "data": [
            {
                "id": "claude-opus-4-6",
                "capabilities": {"batch": True},
                "max_input_tokens": 200_000,
                "max_tokens": 64_000,
            }
        ],
        "has_more": False,
        "last_id": "claude-opus-4-6",
    }

    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, json.dumps(payload).encode()),
    )

    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "anthropic",
            **models[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )
    assert descriptor.supports_vision is True
    assert descriptor.context_limit == 200_000
    assert descriptor.output_limit == 64_000


def test_anthropic_model_catalog_uses_exact_capabilities_for_new_models():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref="session:anthropic",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    payload = {
        "data": [
            {
                "id": "claude-orchid-2026",
                "capabilities": {
                    "effort": {
                        "supported": True,
                        "low": {"supported": True},
                        "medium": {"supported": True},
                        "high": {"supported": True},
                        "max": {"supported": False},
                    },
                    "image_input": {"supported": True},
                    "thinking": {
                        "supported": True,
                        "types": {
                            "adaptive": {"supported": True},
                            "enabled": {"supported": False},
                        },
                    },
                },
                "max_input_tokens": 0,
                "max_tokens": 0,
            }
        ],
        "has_more": False,
        "last_id": "claude-orchid-2026",
    }
    discovered = discover_connection_models(
        connection,
        secret_resolver=lambda _reference: "secret-value",
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, json.dumps(payload).encode()),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "anthropic",
            **discovered[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )
    observed: dict[str, object] = {}

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update(json.loads(body))
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model=descriptor.model_id,
        model_capabilities=descriptor.capabilities,
        secret_resolver=lambda _reference: "secret-value",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )

    assert descriptor.reasoning_efforts == ("low", "medium", "high")
    assert descriptor.supports_vision is True
    assert descriptor.context_limit is None
    assert descriptor.output_limit is None
    assert (
        client.invoke(
            "hello",
            reasoning_effort="high",
            attachments=[{"name": "screen.png", "mime_type": "image/png", "data": "aGVsbG8="}],
        )
        == "ready"
    )
    assert observed["thinking"] == {"type": "adaptive"}
    assert observed["output_config"] == {"effort": "high"}


def test_anthropic_catalog_metadata_overrides_a_known_model_profile():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    payload = {
        "data": [
            {
                "id": "claude-opus-4-6",
                "capabilities": {
                    "effort": {
                        "supported": True,
                        "low": {"supported": True},
                        "high": {"supported": True},
                        "max": {"supported": False},
                        "xhigh": {"supported": False},
                    },
                    "image_input": {"supported": False},
                    "thinking": {
                        "supported": True,
                        "types": {"adaptive": {"supported": True}},
                    },
                },
                "max_input_tokens": 200_000,
                "max_tokens": 64_000,
            }
        ],
        "has_more": False,
        "last_id": "claude-opus-4-6",
    }
    discovered = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, json.dumps(payload).encode()),
    )
    descriptor = ModelDescriptor.from_mapping(
        {
            "connection_id": "anthropic",
            **discovered[0],
            "revision": 1,
            "observed_at_ms": 1,
        }
    )

    assert descriptor.reasoning_efforts == ("low", "high")
    assert descriptor.supports_vision is False


def test_anthropic_explicitly_disabled_thinking_overrides_static_profile():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    payload = {
        "data": [
            {
                "id": "claude-opus-4-6",
                "capabilities": {"thinking": {"supported": False}},
            }
        ],
        "has_more": False,
        "last_id": "claude-opus-4-6",
    }
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, json.dumps(payload).encode()),
    )

    assert not any(capability.startswith("reasoning:") for capability in models[0]["capabilities"])


def test_anthropic_model_catalog_follows_cursor_pages():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    responses = iter(
        (
            DiscoveryResponse(
                200,
                b'{"data":[{"id":"claude-opus-4-6"}],"has_more":true,"last_id":"claude-opus-4-6"}',
            ),
            DiscoveryResponse(
                200,
                b'{"data":[{"id":"claude-opus-4-8"}],"has_more":false,"last_id":"claude-opus-4-8"}',
            ),
        )
    )
    requested_urls: list[str] = []

    def request_page(url: str, _headers: object, _timeout: float) -> DiscoveryResponse:
        requested_urls.append(url)
        return next(responses)

    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=request_page,
    )

    assert [model["model_id"] for model in models] == ["claude-opus-4-6", "claude-opus-4-8"]
    assert len(requested_urls) == 2
    assert requested_urls[0].endswith("/models?limit=1000")
    assert requested_urls[1].endswith("/models?limit=1000&after_id=claude-opus-4-6")


def test_anthropic_model_catalog_rejects_missing_pagination_state():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )

    with pytest.raises(DiscoveryError, match="pagination state"):
        discover_connection_models(
            connection,
            egress_check=lambda _connection_id: True,
            requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"claude-opus-4-6"}]}'),
        )


def test_anthropic_model_catalog_rejects_a_repeated_cursor():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    responses = iter(
        (
            DiscoveryResponse(200, b'{"data":[{"id":"claude-opus-4-6"}],"has_more":true,"last_id":"cursor"}'),
            DiscoveryResponse(200, b'{"data":[{"id":"claude-opus-4-7"}],"has_more":true,"last_id":"cursor"}'),
        )
    )
    request_count = 0

    def request_page(_url: str, _headers: object, _timeout: float) -> DiscoveryResponse:
        nonlocal request_count
        request_count += 1
        return next(responses)

    with pytest.raises(DiscoveryError, match="pagination cursor"):
        discover_connection_models(
            connection,
            egress_check=lambda _connection_id: True,
            requester=request_page,
        )
    assert request_count == 2


def test_provider_endpoint_accepts_documented_request_urls_without_query_data():
    assert _require_provider_endpoint("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"
    assert _require_provider_endpoint("https://api.anthropic.com/v1/messages/") == "https://api.anthropic.com/v1"
    with pytest.raises(Exception, match="query"):
        _require_provider_endpoint("https://api.openai.com/v1?api_key=leak")


def test_openai_profile_is_not_applied_to_a_custom_gateway_endpoint():
    connection = ConnectionRecord(
        connection_id="gateway",
        provider_kind="openai",
        endpoint="https://gateway.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    models = discover_connection_models(
        connection,
        egress_check=lambda _connection_id: True,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"gpt-5.2"}]}'),
    )
    assert not any(item.startswith("reasoning:") for item in models[0]["capabilities"])
    assert "input:image" not in models[0]["capabilities"]


def test_model_discovery_rejects_remote_plain_http_and_oversized_payload():
    remote = ConnectionRecord(
        connection_id="remote",
        provider_kind="openai-compatible",
        endpoint="http://provider.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    try:
        discover_connection_models(remote, requester=lambda *_: DiscoveryResponse(200, b"{}"))
    except DiscoveryError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("remote plain HTTP discovery must be rejected")

    spoofed_loopback = remote.__class__(**{**remote.__dict__, "endpoint": "http://127.evil/v1"})
    try:
        discover_connection_models(spoofed_loopback, requester=lambda *_: DiscoveryResponse(200, b"{}"))
    except DiscoveryError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("spoofed loopback hostname must be rejected")

    local = remote.__class__(**{**remote.__dict__, "endpoint": "http://localhost/v1"})
    try:
        discover_connection_models(
            local,
            requester=lambda *_: DiscoveryResponse(200, b"x" * 20),
            max_body_bytes=10,
        )
    except DiscoveryError as error:
        assert "size limit" in str(error)
    else:
        raise AssertionError("oversized discovery payload must be rejected")


def test_model_discovery_does_not_follow_redirects_for_a_secret_bearing_connection():
    requests: list[tuple[str, str | None]] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/v1/models":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/redirect-target")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"must-not-be-read"}]}')

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = ConnectionRecord(
            connection_id="local-redirect",
            provider_kind="openai-compatible",
            endpoint=f"http://127.0.0.1:{server.server_port}/v1",
            protocol="chat-completions",
            secret_ref="session:synthetic",
            enabled=True,
            revision=1,
            updated_at_ms=1,
        )

        with pytest.raises(DiscoveryError, match="redirects are not allowed") as error:
            discover_connection_models(
                connection,
                secret_resolver=lambda _reference: "synthetic-secret",
                egress_check=lambda _connection_id: True,
                timeout_seconds=2,
            )
        assert "synthetic-secret" not in str(error.value)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requests == [("/v1/models", "Bearer synthetic-secret")]


def test_connection_catalog_reuses_fresh_discovered_models_without_network():
    bridge = MagicMock()
    bridge.aegis_list_connections.return_value = json.dumps(
        {
            "records": [
                {
                    "connection_id": "local",
                    "provider_kind": "openai-compatible",
                    "endpoint": "http://127.0.0.1:8080/v1",
                    "protocol": "chat-completions",
                    "secret_ref": None,
                    "enabled": True,
                    "revision": 1,
                    "updated_at_ms": 1_000,
                }
            ]
        }
    )
    bridge.aegis_list_model_descriptors.return_value = json.dumps(
        {
            "records": [
                {
                    "connection_id": "local",
                    "model_id": "cached-model",
                    "family": "cached",
                    "capabilities": ["chat"],
                    "context_limit": 128_000,
                    "output_limit": 8_000,
                    "source": "discovered",
                    "revision": 2,
                    "observed_at_ms": 1_000,
                }
            ]
        }
    )
    catalog = ConnectionCatalog(bridge)
    models = catalog.discover_models(
        "local",
        requester=lambda *_: pytest.fail("fresh catalog must not make a network request"),
        now_ms=1_000 + MODEL_DISCOVERY_CACHE_TTL_SECONDS * 1_000,
    )

    assert [model.model_id for model in models] == ["cached-model"]
    bridge.aegis_upsert_model_descriptor.assert_not_called()


def test_connection_catalog_force_refresh_bypasses_fresh_catalog():
    bridge = MagicMock()
    bridge.aegis_list_connections.return_value = json.dumps(
        {
            "records": [
                {
                    "connection_id": "local",
                    "provider_kind": "openai-compatible",
                    "endpoint": "http://127.0.0.1:8080/v1",
                    "protocol": "chat-completions",
                    "secret_ref": None,
                    "enabled": True,
                    "revision": 1,
                    "updated_at_ms": 1_000,
                }
            ]
        }
    )
    bridge.aegis_list_model_descriptors.return_value = json.dumps(
        {
            "records": [
                {
                    "connection_id": "local",
                    "model_id": "old-model",
                    "family": None,
                    "capabilities": [],
                    "context_limit": None,
                    "output_limit": None,
                    "source": "discovered",
                    "revision": 2,
                    "observed_at_ms": 1_000,
                }
            ]
        }
    )
    bridge.aegis_upsert_model_descriptor.return_value = json.dumps(
        {
            "record": {
                "connection_id": "local",
                "model_id": "fresh-model",
                "family": None,
                "capabilities": ["chat"],
                "context_limit": None,
                "output_limit": None,
                "source": "discovered",
                "revision": 3,
                "observed_at_ms": 2_000,
            }
        }
    )
    bridge.aegis_remove_model_descriptor.return_value = '{"removed":true}'
    catalog = ConnectionCatalog(bridge)
    models = catalog.discover_models(
        "local",
        force_refresh=True,
        now_ms=2_000,
        requester=lambda *_: DiscoveryResponse(200, b'{"data":[{"id":"fresh-model","capabilities":["chat"]}]}'),
    )

    assert [model.model_id for model in models] == ["fresh-model"]
    bridge.aegis_upsert_model_descriptor.assert_called_once()
    bridge.aegis_remove_model_descriptor.assert_called_once_with("local", "old-model", 2)


def test_connection_catalog_refreshes_models_after_the_five_minute_cache_expires():
    bridge = MagicMock()
    bridge.aegis_list_connections.return_value = json.dumps(
        {
            "records": [
                {
                    "connection_id": "local",
                    "provider_kind": "openai-compatible",
                    "endpoint": "http://127.0.0.1:8080/v1",
                    "protocol": "chat-completions",
                    "secret_ref": None,
                    "enabled": True,
                    "revision": 1,
                    "updated_at_ms": 1_000,
                }
            ]
        }
    )
    bridge.aegis_list_model_descriptors.return_value = json.dumps(
        {
            "records": [
                {
                    "connection_id": "local",
                    "model_id": "old-model",
                    "family": None,
                    "capabilities": [],
                    "context_limit": None,
                    "output_limit": None,
                    "source": "discovered",
                    "revision": 2,
                    "observed_at_ms": 1_000,
                }
            ]
        }
    )
    bridge.aegis_upsert_model_descriptor.return_value = json.dumps(
        {
            "record": {
                "connection_id": "local",
                "model_id": "new-model",
                "family": None,
                "capabilities": ["chat"],
                "context_limit": None,
                "output_limit": None,
                "source": "discovered",
                "revision": 3,
                "observed_at_ms": 301_001,
            }
        }
    )
    bridge.aegis_remove_model_descriptor.return_value = '{"removed":true}'
    catalog = ConnectionCatalog(bridge)
    observed_urls: list[str] = []

    models = catalog.discover_models(
        "local",
        requester=lambda url, _headers, _timeout: (
            observed_urls.append(url) or DiscoveryResponse(200, b'{"data":[{"id":"new-model"}]}')
        ),
        timestamp=301_001,
        now_ms=1_001 + MODEL_DISCOVERY_CACHE_TTL_SECONDS * 1_000,
    )

    assert [model.model_id for model in models] == ["new-model"]
    assert observed_urls == ["http://127.0.0.1:8080/v1/models"]
    bridge.aegis_remove_model_descriptor.assert_called_once_with("local", "old-model", 2)


def test_platform_secret_store_resolves_only_explicit_scopes():
    store = PlatformSecretStore(
        session_secrets={"temporary": "session-secret"},
        environ={"AEGIS_TEST_KEY": "environment-secret"},
    )
    assert store.resolve("session:temporary") == "session-secret"
    assert store.resolve("env:AEGIS_TEST_KEY") == "environment-secret"
    for reference in ("keychain:account", "secret-service:account", "unknown:account"):
        with pytest.raises(SecretStoreError):
            store.resolve(reference)


def test_openai_compatible_client_binds_endpoint_secret_and_model():
    connection = ConnectionRecord(
        connection_id="local",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref="session:local",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, body: bytes, timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        return ConnectionResponse(
            200,
            b'{"choices": [{"message": {"content": "ready"}}]}',
        )

    client = OpenAICompatibleClient(
        connection,
        model="local-model",
        secret_resolver=lambda ref: "secret-value" if ref == "session:local" else None,
        requester=requester,
    )
    assert client.invoke("hello", temperature=0.2, reasoning_effort="high") == "ready"
    assert observed["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert observed["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer secret-value",
    }
    assert observed["body"] == {
        "model": "local-model",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "temperature": 0.2,
        "reasoning_effort": "high",
    }
    assert client.supports_prompt_cache_controls is False

    openai_client = OpenAICompatibleClient(
        connection.__class__(
            **{
                **connection.__dict__,
                "provider_kind": "openai",
                "endpoint": "https://api.openai.com/v1",
            }
        ),
        model="gpt-5.6",
        secret_resolver=lambda ref: "secret-value" if ref == "session:local" else None,
        requester=requester,
    )
    assert openai_client.supports_prompt_cache_controls is True


def test_openrouter_uses_cache_sticky_key_only_on_its_verified_host():
    connection = ConnectionRecord(
        connection_id="openrouter",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref="session:openrouter",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "body": json.loads(body)})
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"ready"}}]}')

    client = OpenAICompatibleClient(
        connection,
        model="openai/gpt-5.6",
        secret_resolver=lambda ref: "secret-value" if ref == "session:openrouter" else None,
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    assert client.supports_prompt_cache_controls is True
    assert client.invoke("hello", prompt_cache_key="aegis-cache-test") == "ready"
    assert observed["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert observed["body"]["prompt_cache_key"] == "aegis-cache-test"

    off_host = connection.__class__(**{**connection.__dict__, "endpoint": "https://proxy.example/v1"})
    unverified_client = OpenAICompatibleClient(
        off_host,
        model="openai/gpt-5.6",
        secret_resolver=lambda _ref: "secret-value",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    assert unverified_client.supports_prompt_cache_controls is False


def test_openrouter_gateway_sends_cache_key_derived_from_stable_system_prefix():
    connection = ConnectionRecord(
        connection_id="openrouter-gateway",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update(json.loads(body))
        observed["headers"] = _headers
        return ConnectionResponse(
            200,
            b'{"choices":[{"message":{"content":"ready"}}],"usage":{"prompt_tokens":100,"prompt_tokens_details":{"cached_tokens":80},"completion_tokens":5}}',
        )

    def reject_secret_lookup(_reference: str) -> str | None:
        pytest.fail("keyless adapter inference must not resolve a credential")

    client = OpenAICompatibleClient(
        connection,
        model="openai/gpt-5.6",
        secret_resolver=reject_secret_lookup,
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    gateway = AegisAdapter(
        llm=client,
        provider="openrouter",
        model="openai/gpt-5.6",
        cache_namespace="local-profile",
    )

    result = asyncio.run(gateway.run("dynamic task", system_context="stable system contract"))

    assert result.output == "ready"
    assert gateway.last_cache_observation is not None
    assert gateway.last_cache_observation.usage.cached_read_tokens == 80
    assert gateway.last_cache_observation.usage.cache_status == "HIT"
    assert all(name.casefold() != "authorization" for name in observed["headers"])
    assert gateway.last_cache_prompt is not None
    assert (
        observed["prompt_cache_key"]
        == gateway.last_cache_prompt.for_provider(
            "openrouter",
            "openai/gpt-5.6",
        ).cache_key
    )
    assert observed["messages"] == [
        {"role": "system", "content": "stable system contract"},
        {"role": "user", "content": "dynamic task"},
    ]


def test_shared_provider_client_keeps_concurrent_usage_with_its_own_run():
    rendezvous = Barrier(2)

    connection = ConnectionRecord(
        connection_id="openrouter-parallel",
        provider_kind="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        prompt = json.loads(body)["messages"][-1]["content"]
        rendezvous.wait(timeout=5)
        cached_tokens = 11 if prompt == "first run" else 22
        response = {
            "choices": [{"message": {"content": prompt}}],
            "usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": cached_tokens}},
        }
        return ConnectionResponse(200, json.dumps(response).encode())

    client = OpenAICompatibleClient(
        connection,
        model="openai/gpt-5.6",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    first = AegisAdapter(llm=client, provider="openrouter", model="openai/gpt-5.6")
    second = AegisAdapter(llm=client, provider="openrouter", model="openai/gpt-5.6")

    async def run_in_parallel():
        return await asyncio.gather(first.run("first run"), second.run("second run"))

    results = asyncio.run(run_in_parallel())

    assert [result.output for result in results] == ["first run", "second run"]
    assert first.last_cache_observation is not None
    assert first.last_cache_observation.usage.cached_read_tokens == 11
    assert second.last_cache_observation is not None
    assert second.last_cache_observation.usage.cached_read_tokens == 22


def test_gemini_openai_compatibility_forwards_documented_reasoning_effort():
    connection = ConnectionRecord(
        connection_id="gemini",
        provider_kind="google-gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        protocol="chat-completions",
        secret_ref="session:gemini",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "body": json.loads(body)})
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"ready"}}]}')

    client = OpenAICompatibleClient(
        connection,
        model="gemini-3.8-flash",
        secret_resolver=lambda _reference: "test-secret",
        egress_check=lambda _connection_id: True,
        requester=requester,
    )

    assert client.invoke("hello", reasoning_effort="medium") == "ready"
    assert observed["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert observed["body"]["reasoning_effort"] == "medium"


def test_provider_clients_encode_image_attachments_without_changing_text_contract():
    connection = ConnectionRecord(
        connection_id="local",
        provider_kind="openai-compatible",
        endpoint="http://127.0.0.1:8080/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: list[dict[str, object]] = []

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.append(json.loads(body))
        return ConnectionResponse(200, b'{"choices":[{"message":{"content":"ok"}}]}')

    client = OpenAICompatibleClient(connection, model="vision-model", requester=requester)
    assert (
        client.invoke(
            "describe this",
            attachments=[{"name": "screen.png", "mime_type": "image/png", "data": "aGVsbG8="}],
        )
        == "ok"
    )
    assert observed[0]["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
    ]


def test_key_only_identity_is_a_hint_and_never_contains_secret_material():
    key = "sk-ant-test-secret-value"
    identity = identify_api_key(key)

    assert identity.provider_kind == "anthropic"
    assert identity.endpoint == "https://api.anthropic.com/v1"
    assert key not in repr(identity.as_dict())
    assert identity.as_dict()["requires_endpoint"] is False


def test_key_only_identity_recognizes_the_existing_nvidia_nim_adapter():
    identity = identify_api_key("nvapi-test-secret")

    assert identity.provider_kind == "nvidia-nim"
    assert identity.endpoint == "https://integrate.api.nvidia.com/v1"
    assert identity.protocol == "chat-completions"
    assert identity.as_dict()["requires_endpoint"] is False


def test_generic_openai_style_prefix_is_never_routed_without_destination_confirmation():
    identity = identify_api_key("sk-proj-test-secret-value")

    assert identity.provider_kind == "openai"
    assert identity.endpoint == "https://api.openai.com/v1"
    assert identity.protocol == "openai-responses"
    assert identity.confidence == "medium"
    assert identity.requires_confirmation is True
    assert "compatible providers" in " ".join(identity.hints)


def test_google_api_key_prefix_requires_confirmation_before_gemini_routing():
    identity = identify_api_key("AIza-test-secret-value")

    assert identity.provider_kind == "google-gemini"
    assert identity.endpoint == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert identity.confidence == "medium"
    assert identity.requires_confirmation is True
    assert "does not uniquely identify Gemini" in " ".join(identity.hints)


def test_reasoning_picker_exposes_only_explicit_provider_capabilities():
    assert reasoning_efforts_for_model(
        SimpleNamespace(capabilities=("reasoning:minimal", "reasoning:high", "reasoning:inferred"))
    ) == ("minimal", "high")
    assert reasoning_efforts_for_model(SimpleNamespace(capabilities=("reasoning:inferred",))) == ()


def test_unknown_key_requires_an_explicit_endpoint_without_echoing_key():
    key = "opaque-private-key-value"
    identity = identify_api_key(key)

    assert identity.provider_kind == "openai-compatible"
    assert identity.requires_endpoint is True
    assert identity.requires_confirmation is False
    assert key not in repr(identity.as_dict())


def test_anthropic_messages_client_uses_native_headers_and_reasoning_payload():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref="session:anthropic",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, body: bytes, timeout: float) -> ConnectionResponse:
        observed.update({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model="claude-opus-4-5",
        secret_resolver=lambda ref: "secret-value" if ref == "session:anthropic" else None,
        requester=requester,
        egress_check=lambda connection_id: connection_id == "anthropic",
    )

    assert client.invoke("hello", system_context="Anthropic system contract", reasoning_effort="high") == "ready"
    assert observed["url"] == "https://api.anthropic.com/v1/messages"
    assert observed["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": "secret-value",
        "anthropic-version": "2023-06-01",
    }
    assert observed["body"] == {
        "model": "claude-opus-4-5",
        "max_tokens": 9216,
        "system": "Anthropic system contract",
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "enabled", "budget_tokens": 8192},
        "output_config": {"effort": "high"},
    }


def test_anthropic_budgeted_model_auto_leaves_provider_thinking_defaults_unchanged():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref="session:anthropic",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed["body"] = json.loads(body)
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model="claude-opus-4-5",
        secret_resolver=lambda _ref: "secret-value",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )

    assert client.invoke("hello") == "ready"
    assert observed["body"] == {
        "model": "claude-opus-4-5",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_anthropic_adaptive_thinking_uses_the_provider_effort_contract():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref="session:anthropic",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(url: str, headers: object, body: bytes, timeout: float) -> ConnectionResponse:
        observed["body"] = json.loads(body)
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model="claude-opus-4-6",
        secret_resolver=lambda _ref: "secret-value",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )

    assert client.invoke("hello", reasoning_effort="high") == "ready"
    assert observed["body"] == {
        "model": "claude-opus-4-6",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }


def test_anthropic_always_thinking_model_uses_effort_without_legacy_budget():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref="session:anthropic",
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed["body"] = json.loads(body)
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model="claude-fable-5-1",
        secret_resolver=lambda _ref: "secret-value",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )

    assert client.invoke("hello", reasoning_effort="low") == "ready"
    assert observed["body"] == {
        "model": "claude-fable-5-1",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "hello"}],
        "output_config": {"effort": "low"},
    }


def test_anthropic_client_rejects_effort_not_supported_by_model_profile():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    client = AnthropicMessagesClient(connection, model="claude-opus-4-6", requester=lambda *_: pytest.fail())
    with pytest.raises(ValueError, match="not supported"):
        client.invoke("hello", reasoning_effort="xhigh")


def test_anthropic_effort_profile_has_distinct_max_and_xhigh_support():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    requests: list[dict[str, object]] = []

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        requests.append(json.loads(body))
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model="claude-opus-4-7",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    assert client.invoke("hello", reasoning_effort="xhigh") == "ready"
    assert requests[0]["output_config"] == {"effort": "xhigh"}
    assert requests[0]["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in requests[0]["thinking"]


def test_anthropic_fable_accepts_max_without_manually_enabling_thinking():
    connection = ConnectionRecord(
        connection_id="anthropic",
        provider_kind="anthropic",
        endpoint="https://api.anthropic.com/v1",
        protocol="anthropic-messages",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    observed: dict[str, object] = {}

    def requester(_url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        observed.update(json.loads(body))
        return ConnectionResponse(200, b'{"content":[{"type":"text","text":"ready"}]}')

    client = AnthropicMessagesClient(
        connection,
        model="claude-fable-5-1",
        requester=requester,
        egress_check=lambda _connection_id: True,
    )
    assert client.invoke("hello", reasoning_effort="max") == "ready"
    assert observed["output_config"] == {"effort": "max"}
    assert "thinking" not in observed


def test_openai_compatible_client_rejects_remote_plain_http_and_oversized_response():
    remote = ConnectionRecord(
        connection_id="remote",
        provider_kind="openai-compatible",
        endpoint="http://provider.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    client = OpenAICompatibleClient(
        remote,
        model="remote-model",
        requester=lambda *_: ConnectionResponse(200, b"{}"),
    )
    try:
        client.invoke("hello")
    except ConnectionClientError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("remote plain HTTP requests must be rejected")

    spoofed = remote.__class__(**{**remote.__dict__, "endpoint": "http://127.evil/v1"})
    spoofed_client = OpenAICompatibleClient(
        spoofed,
        model="local-model",
        requester=lambda *_: ConnectionResponse(200, b"{}"),
    )
    try:
        spoofed_client.invoke("hello")
    except ConnectionClientError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("spoofed loopback hostname must be rejected")

    local = remote.__class__(**{**remote.__dict__, "endpoint": "http://localhost/v1"})
    oversized = OpenAICompatibleClient(
        local,
        model="local-model",
        requester=lambda *_: ConnectionResponse(200, b"x" * 20),
        max_response_bytes=10,
    )
    try:
        oversized.invoke("hello")
    except ConnectionClientError as error:
        assert "size limit" in str(error)
    else:
        raise AssertionError("oversized provider responses must be rejected")


def test_openai_compatible_client_checks_connection_egress_before_request():
    connection = ConnectionRecord(
        connection_id="remote",
        provider_kind="openai-compatible",
        endpoint="https://provider.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    called = False

    def requester(*_args: object) -> ConnectionResponse:
        nonlocal called
        called = True
        return ConnectionResponse(200, b'{"choices": [{"message": {"content": "no"}}]}')

    client = OpenAICompatibleClient(
        connection,
        model="remote-model",
        requester=requester,
        egress_check=lambda connection_id: connection_id == "local",
    )
    try:
        client.invoke("hello")
    except ConnectionClientError as error:
        assert "egress" in str(error)
    else:
        raise AssertionError("denied connection egress must block the request")
    assert called is False


def test_remote_https_connection_requires_explicit_egress_callback():
    connection = ConnectionRecord(
        connection_id="remote",
        provider_kind="openai-compatible",
        endpoint="https://provider.example/v1",
        protocol="chat-completions",
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )
    client = OpenAICompatibleClient(
        connection,
        model="remote-model",
        requester=lambda *_: ConnectionResponse(200, b"{}"),
    )
    try:
        client.invoke("hello")
    except ConnectionClientError as error:
        assert "egress grant" in str(error)
    else:
        raise AssertionError("remote HTTPS requests require an explicit egress grant")
