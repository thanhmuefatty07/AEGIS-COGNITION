from __future__ import annotations

import base64
import hashlib
import http.client
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

from core.python.aegis.mcp_oauth import (
    McpOAuthClient,
    McpOAuthError,
    OAuthHttpResponse,
    _LoopbackCallback,
    _authorization_metadata_urls,
    _authorization_url,
    _canonical_resource_uri,
    _parse_bearer_challenge,
    _resource_metadata_urls,
    _secret_reference,
    _validate_https_url,
)


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def lookup(self, secret_ref: str) -> str | None:
        return self.values.get(secret_ref)

    def store(self, secret_ref: str, secret: str) -> None:
        self.values[secret_ref] = secret

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)


def test_mcp_oauth_discovery_urls_preserve_resource_and_issuer_paths():
    resource = "https://MCP.example:443/team/server/"
    issuer = "https://auth.example/tenant-1/"

    assert _canonical_resource_uri(resource) == "https://mcp.example/team/server"
    assert _canonical_resource_uri("https://[2001:db8::1]:443/mcp") == "https://[2001:db8::1]/mcp"
    assert _resource_metadata_urls(_canonical_resource_uri(resource)) == (
        "https://mcp.example/.well-known/oauth-protected-resource/team/server",
        "https://mcp.example/.well-known/oauth-protected-resource",
    )
    assert _authorization_metadata_urls(issuer) == (
        "https://auth.example/.well-known/oauth-authorization-server/tenant-1",
        "https://auth.example/.well-known/openid-configuration/tenant-1",
        "https://auth.example/tenant-1/.well-known/openid-configuration",
    )


def test_mcp_oauth_rejects_unsafe_resource_and_metadata_urls():
    for endpoint in (
        "http://mcp.example",
        "https://user@mcp.example",
        "https://mcp.example/#fragment",
        "https://mcp.example/?tenant=secret",
    ):
        with pytest.raises(McpOAuthError):
            _canonical_resource_uri(endpoint)
    for endpoint in (
        "http://auth.example/token",
        "https://auth.example/token#fragment",
        "https://user@auth.example/token",
    ):
        with pytest.raises(McpOAuthError):
            _validate_https_url(endpoint, "test endpoint")


def test_mcp_oauth_callback_wait_stops_promptly_when_cancelled():
    cancel_event = threading.Event()
    with (
        _LoopbackCallback(expected_state="expected", cancel_event=cancel_event, timeout_seconds=30) as callback,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        pending = executor.submit(callback.wait, state="expected", issuer="https://auth.example", require_issuer=False)
        cancel_event.set()
        with pytest.raises(McpOAuthError, match="cancelled") as error:
            pending.result(timeout=1)
    assert error.value.code == "cancelled"


@pytest.mark.parametrize("cancel", [False, True], ids=["deadline", "cancel"])
@pytest.mark.parametrize("trickle", [False, True], ids=["stalled", "trickling"])
def test_mcp_oauth_partial_callback_is_bounded(monkeypatch, cancel, trickle):
    cancel_event = threading.Event()
    accepted = threading.Event()
    stop_trickle = threading.Event()
    with _LoopbackCallback(expected_state="expected", cancel_event=cancel_event, timeout_seconds=1) as callback:
        get_request = callback._server.get_request

        def accept_request():
            request = get_request()
            accepted.set()
            return request

        monkeypatch.setattr(callback._server, "get_request", accept_request)
        redirect = urlsplit(callback.redirect_uri)
        with (
            socket.create_connection((redirect.hostname, redirect.port), timeout=1) as connection,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            connection.sendall(b"GET /oauth/callback HTTP/1.1\r\nHost:")
            pending = executor.submit(callback.wait, state="expected", issuer="https://auth.example", require_issuer=False)

            def trickle_bytes():
                while not stop_trickle.wait(0.05):
                    try:
                        connection.sendall(b"x")
                    except OSError:
                        return

            try:
                assert accepted.wait(timeout=1), "the partial callback was not accepted"
                if trickle:
                    executor.submit(trickle_bytes)
                if cancel:
                    cancel_event.set()
                with pytest.raises(McpOAuthError, match=r"cancelled|timed out") as error:
                    pending.result(timeout=1 if cancel else 2)
                assert error.value.code == ("cancelled" if cancel else None)
            finally:
                stop_trickle.set()
                with suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)
                connection.close()


def test_mcp_oauth_rejects_authorization_endpoint_parameter_override():
    with pytest.raises(McpOAuthError, match="conflicts"):
        _authorization_url(
            "https://auth.example/authorize?client_id=attacker",
            client_id="registered-client",
            redirect_uri="http://127.0.0.1:12345/oauth/callback",
            state="state",
            challenge="challenge",
            resource="https://mcp.example/mcp",
            scope=None,
        )


def test_mcp_oauth_parses_bearer_metadata_and_scopes_without_splitting_quoted_commas():
    parsed = _parse_bearer_challenge(
        {
            "www-authenticate": (
                'Basic realm="old", Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource", '
                'scope="files:read,projects:read"'
            )
        }
    )

    assert parsed["resource_metadata"] == "https://mcp.example/.well-known/oauth-protected-resource"
    assert parsed["scope"] == "files:read,projects:read"


def test_mcp_oauth_automatic_registration_pkce_resource_and_secure_token_persistence():
    issuer = "https://auth.example/tenant"
    resource_uri = "https://mcp.example/api"
    store = MemorySecretStore()
    requests: list[tuple[str, str, dict[str, str], bytes | None]] = []
    auth_result: dict[str, str] = {}
    callback_threads: list[threading.Thread] = []

    def exchange(url: str, method: str, headers: dict[str, str], body: bytes | None, _limit: int) -> OAuthHttpResponse:
        requests.append((url, method, dict(headers), body))
        if url == "https://mcp.example/.well-known/oauth-protected-resource/api":
            return _json_response(
                {
                    "resource": resource_uri,
                    "authorization_servers": [issuer],
                    "scopes_supported": ["files:read", "offline_access"],
                }
            )
        if url == "https://auth.example/.well-known/oauth-authorization-server/tenant":
            return _json_response(
                {
                    "issuer": issuer,
                    "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token",
                    "registration_endpoint": "https://auth.example/register",
                    "scopes_supported": ["files:read", "offline_access"],
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True,
                }
            )
        if url == "https://auth.example/register":
            registration = json.loads(body or b"{}")
            assert registration["application_type"] == "native"
            assert registration["token_endpoint_auth_method"] == "none"
            assert registration["grant_types"] == ["authorization_code", "refresh_token"]
            auth_result["redirect_uri"] = registration["redirect_uris"][0]
            return _json_response({"client_id": "public-client", "token_endpoint_auth_method": "none"})
        if url == "https://auth.example/token":
            form = dict(parse_qsl((body or b"").decode("ascii")))
            assert form["resource"] == resource_uri
            assert form["grant_type"] == "authorization_code"
            verifier = form["code_verifier"]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
            assert challenge.decode("ascii") == auth_result["code_challenge"]
            return _json_response(
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        raise AssertionError(f"unexpected OAuth request: {method} {url}")

    def open_browser(url: str) -> bool:
        authorization = dict(parse_qsl(urlsplit(url).query))
        assert authorization["client_id"] == "public-client"
        assert authorization["code_challenge_method"] == "S256"
        assert authorization["resource"] == resource_uri
        assert authorization["scope"] == "files:read offline_access"
        auth_result["code_challenge"] = authorization["code_challenge"]
        auth_result["state"] = authorization["state"]
        redirect = urlsplit(authorization["redirect_uri"])
        assert redirect.hostname == "127.0.0.1"

        def send_callback() -> None:
            for callback_state, expected_status in (("attacker-state", 400), (authorization["state"], 200)):
                connection = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=2)
                connection.request(
                    "GET",
                    f"{redirect.path}?{urlencode({'state': callback_state, 'code': 'one-time-code', 'iss': issuer})}",
                    headers={"Host": f"127.0.0.1:{redirect.port}"},
                )
                response = connection.getresponse()
                assert response.status == expected_status
                response.read()
                connection.close()

        callback_thread = threading.Thread(target=send_callback, daemon=True)
        callback_threads.append(callback_thread)
        callback_thread.start()
        return True

    client = _client(exchange, store, resource_uri=resource_uri, browser_open=open_browser)
    client.authorize(
        {
            "www-authenticate": 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/api", scope="files:read"'
        }
    )
    for callback_thread in callback_threads:
        callback_thread.join(timeout=2)
        assert not callback_thread.is_alive()

    assert client.access_token() == "access-secret"
    assert len(store.values) == 1
    record = json.loads(next(iter(store.values.values())))
    assert record["issuer"] == issuer
    assert record["refresh_token"] == "refresh-secret"
    assert record["requested_scopes"] == ["files:read", "offline_access"]
    assert "access-secret" not in auth_result["state"]
    assert not any("access-secret" in url or "refresh-secret" in url for url, *_rest in requests)


def test_mcp_oauth_scope_step_up_is_queued_then_explicitly_resumed_with_union():
    issuer = "https://auth.example"
    resource_uri = "https://mcp.example/mcp"
    resource_metadata_url = "https://mcp.example/.well-known/oauth-protected-resource/mcp"
    store = MemorySecretStore()
    secret_ref = _secret_reference("profile", "server", resource_uri)
    store.store(
        secret_ref,
        json.dumps(
            {
                "version": 1,
                "resource": resource_uri,
                "issuer": issuer,
                "client_id": "public-client",
                "authorization_endpoint": "https://auth.example/authorize",
                "token_endpoint": "https://auth.example/token",
                "authorization_response_iss_parameter_supported": True,
                "requested_scopes": ["files:read", "offline_access"],
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": time.time() + 3600,
            }
        ),
    )
    auth_result: dict[str, str] = {}
    callback_threads: list[threading.Thread] = []
    requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def exchange(url: str, method: str, headers: dict[str, str], body: bytes | None, _limit: int) -> OAuthHttpResponse:
        requests.append((url, method, dict(headers), body))
        if url == resource_metadata_url:
            return _json_response({"resource": resource_uri, "authorization_servers": [issuer]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return _json_response(
                {
                    "issuer": issuer,
                    "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token",
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True,
                }
            )
        if url == "https://auth.example/token":
            fields = dict(parse_qsl((body or b"").decode("ascii")))
            assert fields["grant_type"] == "authorization_code"
            assert fields["client_id"] == "public-client"
            assert fields["resource"] == resource_uri
            return _json_response(
                {
                    "access_token": "upgraded-access",
                    "refresh_token": "upgraded-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        raise AssertionError(f"unexpected OAuth request: {method} {url}")

    def open_browser(url: str) -> bool:
        authorization = dict(parse_qsl(urlsplit(url).query))
        assert authorization["scope"] == "files:read offline_access files:write"
        auth_result.update(authorization)
        redirect = urlsplit(authorization["redirect_uri"])

        def send_callback() -> None:
            connection = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=2)
            connection.request(
                "GET",
                f"{redirect.path}?{urlencode({'state': authorization['state'], 'code': 'step-up-code', 'iss': issuer})}",
                headers={"Host": f"127.0.0.1:{redirect.port}"},
            )
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            connection.close()

        callback_thread = threading.Thread(target=send_callback, daemon=True)
        callback_threads.append(callback_thread)
        callback_thread.start()
        return True

    client = _client(exchange, store, resource_uri=resource_uri, browser_open=open_browser)
    challenge = {
        "www-authenticate": (
            'Bearer error="insufficient_scope", scope="files:write", '
            f'resource_metadata="{resource_metadata_url}"'
        )
    }
    assert client.queue_step_up(challenge)
    assert not auth_result
    pending = json.loads(store.values[secret_ref])
    assert pending["pending_step_up_scopes"] == ["files:read", "offline_access", "files:write"]
    assert client.resume_pending_step_up()

    for callback_thread in callback_threads:
        callback_thread.join(timeout=2)
        assert not callback_thread.is_alive()
    assert not any(dict(parse_qsl((body or b"").decode("ascii"))).get("grant_type") == "refresh_token" for _, _, _, body in requests if body)
    completed = json.loads(store.values[secret_ref])
    assert completed["access_token"] == "upgraded-access"
    assert completed["requested_scopes"] == ["files:read", "offline_access", "files:write"]
    assert "pending_step_up_scopes" not in completed


def test_mcp_oauth_requires_preregistered_client_when_dynamic_registration_is_unavailable():
    issuer = "https://auth.example"
    resource_uri = "https://mcp.example/mcp"
    store = MemorySecretStore()
    opened = False

    def exchange(
        url: str, _method: str, _headers: dict[str, str], _body: bytes | None, _limit: int
    ) -> OAuthHttpResponse:
        if url == "https://mcp.example/.well-known/oauth-protected-resource/mcp":
            return _json_response({"resource": resource_uri, "authorization_servers": [issuer]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return _json_response(
                {
                    "issuer": issuer,
                    "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token",
                }
            )
        if url == "https://auth.example/.well-known/openid-configuration":
            return OAuthHttpResponse(404, {"content-type": "application/json"}, b"{}")
        raise AssertionError(f"unexpected OAuth request: {url}")

    def browser_open(_url: str) -> bool:
        nonlocal opened
        opened = True
        return True

    client = _client(exchange, store, resource_uri=resource_uri, browser_open=browser_open)
    with pytest.raises(McpOAuthError, match="pre-registered OAuth Client ID"):
        client.authorize({"www-authenticate": 'Bearer scope="read"'})
    assert not opened


@pytest.mark.parametrize("registration_available", [False, True])
def test_mcp_oauth_uses_user_supplied_client_id_without_registration(registration_available):
    issuer = "https://auth.example"
    resource_uri = "https://mcp.example/mcp"
    store = MemorySecretStore()
    requests: list[tuple[str, str, dict[str, str], bytes | None]] = []
    callback_threads: list[threading.Thread] = []

    def exchange(url: str, method: str, headers: dict[str, str], body: bytes | None, _limit: int) -> OAuthHttpResponse:
        requests.append((url, method, dict(headers), body))
        if url == "https://mcp.example/.well-known/oauth-protected-resource/mcp":
            return _json_response({"resource": resource_uri, "authorization_servers": [issuer]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            metadata = {
                "issuer": issuer,
                "authorization_endpoint": "https://auth.example/authorize",
                "token_endpoint": "https://auth.example/token",
                "code_challenge_methods_supported": ["S256"],
            }
            if registration_available:
                metadata["registration_endpoint"] = "https://auth.example/register"
            return _json_response(metadata)
        if url == "https://auth.example/register":
            return _json_response({"client_id": "dynamic-client", "token_endpoint_auth_method": "none"})
        if url == "https://auth.example/token":
            fields = dict(parse_qsl((body or b"").decode("ascii")))
            assert fields["client_id"] == "manual-client"
            assert fields["resource"] == resource_uri
            assert fields["code_verifier"]
            return _json_response({"access_token": "manual-access", "token_type": "Bearer", "expires_in": 3600})
        raise AssertionError(f"unexpected OAuth request: {method} {url}")

    def open_browser(url: str) -> bool:
        authorization = dict(parse_qsl(urlsplit(url).query))
        assert authorization["client_id"] == "manual-client"
        assert authorization["code_challenge_method"] == "S256"
        redirect = urlsplit(authorization["redirect_uri"])

        def send_callback() -> None:
            connection = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=2)
            connection.request(
                "GET",
                f"{redirect.path}?{urlencode({'state': authorization['state'], 'code': 'manual-code'})}",
                headers={"Host": f"127.0.0.1:{redirect.port}"},
            )
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            connection.close()

        callback_thread = threading.Thread(target=send_callback, daemon=True)
        callback_threads.append(callback_thread)
        callback_thread.start()
        return True

    client = _client(
        exchange,
        store,
        resource_uri=resource_uri,
        client_id="manual-client",
        browser_open=open_browser,
    )
    client.authorize({"www-authenticate": 'Bearer scope="files:read"'})
    for callback_thread in callback_threads:
        callback_thread.join(timeout=2)
        assert not callback_thread.is_alive()

    assert client.access_token() == "manual-access"
    assert not any(url.endswith("/register") for url, *_rest in requests)
    record = json.loads(next(iter(store.values.values())))
    assert record["client_id"] == "manual-client"
    reloaded = _client(exchange, store, resource_uri=resource_uri, client_id="manual-client")
    assert reloaded.access_token() == "manual-access"


def test_mcp_oauth_rejects_issuer_mismatch_before_registration_or_browser():
    store = MemorySecretStore()
    browser_opened = False

    def exchange(
        url: str, _method: str, _headers: dict[str, str], _body: bytes | None, _limit: int
    ) -> OAuthHttpResponse:
        if url == "https://mcp.example/.well-known/oauth-protected-resource":
            return _json_response(
                {"resource": "https://mcp.example", "authorization_servers": ["https://auth.example"]}
            )
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return _json_response(
                {
                    "issuer": "https://wrong.example",
                    "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token",
                }
            )
        raise AssertionError(f"unexpected OAuth request: {url}")

    def browser_open(_url: str) -> bool:
        nonlocal browser_opened
        browser_opened = True
        return True

    client = _client(exchange, store, resource_uri="https://mcp.example", browser_open=browser_open)
    with pytest.raises(McpOAuthError, match="issuer did not match"):
        client.authorize({"www-authenticate": "Bearer"})
    assert not browser_opened
    assert store.values == {}


def test_mcp_oauth_refreshes_once_when_multiple_threads_request_the_expired_token():
    resource_uri = "https://mcp.example"
    issuer = "https://auth.example"
    store = MemorySecretStore()
    secret_ref = _secret_reference("profile", "server", resource_uri)
    store.store(
        secret_ref,
        json.dumps(
            {
                "version": 1,
                "resource": resource_uri,
                "issuer": issuer,
                "client_id": "client",
                "authorization_endpoint": "https://auth.example/authorize",
                "token_endpoint": "https://auth.example/token",
                "access_token": "expired",
                "refresh_token": "refresh",
                "expires_at": time.time() - 1,
            }
        ),
    )
    refresh_count = 0
    lock = threading.Lock()

    def exchange(
        url: str, _method: str, _headers: dict[str, str], body: bytes | None, _limit: int
    ) -> OAuthHttpResponse:
        nonlocal refresh_count
        assert url == "https://auth.example/token"
        fields = dict(parse_qsl((body or b"").decode("ascii")))
        assert fields["grant_type"] == "refresh_token"
        assert fields["resource"] == resource_uri
        with lock:
            refresh_count += 1
        return _json_response({"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600})

    client = _client(exchange, store, resource_uri=resource_uri)
    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = tuple(executor.map(lambda _index: client.access_token(), range(8)))

    assert tokens == ("fresh",) * 8
    assert refresh_count == 1


def test_mcp_oauth_ignores_cached_credentials_when_explicit_client_id_changes():
    resource_uri = "https://mcp.example"
    store = MemorySecretStore()
    secret_ref = _secret_reference("profile", "server", resource_uri)
    store.store(
        secret_ref,
        json.dumps(
            {
                "version": 1,
                "resource": resource_uri,
                "issuer": "https://auth.example",
                "client_id": "old-client",
                "authorization_endpoint": "https://auth.example/authorize",
                "token_endpoint": "https://auth.example/token",
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": time.time() + 3600,
            }
        ),
    )
    client = McpOAuthClient(
        endpoint=resource_uri,
        server_id="server",
        profile_id="profile",
        client_id="new-client",
        secret_store=store,
        request=lambda *_args: None,  # type: ignore[arg-type]
    )

    assert client.access_token() is None
    assert secret_ref in store.values


def _client(
    exchange: object,
    store: MemorySecretStore,
    *,
    resource_uri: str,
    client_id: str | None = None,
    browser_open: object | None = None,
) -> McpOAuthClient:
    kwargs = {
        "endpoint": resource_uri,
        "server_id": "server",
        "profile_id": "profile",
        "client_id": client_id,
        "secret_store": store,
        "request": exchange,
    }
    if browser_open is not None:
        kwargs["browser_open"] = browser_open
    return McpOAuthClient(**kwargs)  # type: ignore[arg-type]


def _json_response(value: dict[str, object]) -> OAuthHttpResponse:
    return OAuthHttpResponse(200, {"content-type": "application/json"}, json.dumps(value).encode("utf-8"))
