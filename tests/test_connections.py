import asyncio
import json
from unittest.mock import MagicMock

from core.python.aegis.connections import ConnectionCatalog, ConnectionRecord
from core.python.aegis.connection_clients import ConnectionClientError, ConnectionResponse, OpenAICompatibleClient
from core.python.aegis.discovery import DiscoveryError, DiscoveryResponse, discover_connection_models
from core.python.aegis.secrets import PlatformSecretStore, SecretStoreError
from core.python.aegis_adapter import Agent


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
    assert catalog.check_egress(
        "local", "PROJECT_TEXT", "foreground_inference", timestamp=1_700_000_000_001
    )
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


def test_platform_secret_store_resolves_only_explicit_scopes():
    store = PlatformSecretStore(
        session_secrets={"temporary": "session-secret"},
        environ={"AEGIS_TEST_KEY": "environment-secret"},
    )
    assert store.resolve("session:temporary") == "session-secret"
    assert store.resolve("env:AEGIS_TEST_KEY") == "environment-secret"
    for reference in ("keychain:account", "secret-service:account", "unknown:account"):
        try:
            store.resolve(reference)
        except SecretStoreError:
            pass
        else:
            raise AssertionError("unsupported secret backend must fail closed")


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
    assert client.invoke("hello", temperature=0.2) == "ready"
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
    }


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
