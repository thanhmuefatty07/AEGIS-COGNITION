"""Minimal OAuth authorization-code client for desktop MCP connections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import io
import json
import math
import secrets
import select
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .secrets import SecretStoreError


_MAX_JSON_BYTES = 64 * 1024
_MAX_AUTH_RESPONSE_BYTES = 8 * 1024
_CALLBACK_TIMEOUT_SECONDS = 180
_MAX_METADATA_SERVERS = 16
_MAX_SCOPE_COUNT = 128


class McpOAuthError(RuntimeError):
    """A bounded, user-safe MCP authorization failure."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


class McpOAuthSecretStore(Protocol):
    def lookup(self, secret_ref: str) -> str | None: ...

    def store(self, secret_ref: str, secret: str) -> None: ...

    def delete(self, secret_ref: str) -> None: ...


@dataclass(frozen=True, slots=True)
class OAuthHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


OAuthRequester = Callable[[str, str, Mapping[str, str], bytes | None, int], OAuthHttpResponse]
BrowserOpener = Callable[[str], bool]


class McpOAuthClient:
    """Perform MCP's public-client OAuth flow without exposing tokens to the UI."""

    def __init__(
        self,
        *,
        endpoint: str,
        server_id: str,
        profile_id: str,
        client_id: str | None,
        secret_store: McpOAuthSecretStore,
        request: OAuthRequester,
        browser_open: BrowserOpener = webbrowser.open,
    ) -> None:
        self.resource_uri = _canonical_resource_uri(endpoint)
        self.server_id = _bounded_identity(server_id, "server id")
        self.profile_id = _bounded_identity(profile_id, "profile id")
        if client_id is not None and (type(client_id) is not str or not client_id.strip() or len(client_id) > 2_048):
            raise McpOAuthError("OAuth client ID is invalid")
        if not callable(request) or not callable(browser_open):
            raise TypeError("MCP OAuth callbacks must be callable")
        self.client_id = client_id.strip() if client_id else None
        self._secret_store = secret_store
        self._request = request
        self._browser_open = browser_open
        self._secret_ref = _secret_reference(self.profile_id, self.server_id, self.resource_uri)
        self._record: dict[str, object] | None = None
        self._loaded = False
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Stop a pending interactive authorization without blocking its caller."""

        self._cancel_event.set()

    def access_token(self) -> str | None:
        """Return a usable cached token; refresh quietly, but never launch a browser."""

        with self._lock:
            record = self._load_record()
            if record is None:
                return None
            access_token = record.get("access_token")
            expires_at = record.get("expires_at")
            if type(access_token) is str and (
                expires_at is None or (type(expires_at) in (int, float) and expires_at > time.time() + 45)
            ):
                return access_token
            refresh_token = record.get("refresh_token")
            if type(refresh_token) is not str or not refresh_token:
                return None
            try:
                self._refresh(record, refresh_token)
            except McpOAuthError as error:
                if error.code == "invalid_grant":
                    return None
                raise
            return self._string_field(self._record or {}, "access_token")

    def refresh_after_unauthorized(self) -> bool:
        """Refresh without opening a browser after a resource server rejects a token."""

        with self._lock:
            record = self._load_record()
            refresh_token = record.get("refresh_token") if record is not None else None
            if record is None or type(refresh_token) is not str or not refresh_token:
                return False
            try:
                self._refresh(record, refresh_token)
            except McpOAuthError as error:
                if error.code == "invalid_grant":
                    return False
                raise
            return True

    def authorize(self, challenge_headers: Mapping[str, str]) -> None:
        """Refresh or start one user-approved authorization flow for this MCP resource."""

        challenge = _parse_bearer_challenge(challenge_headers)
        self._authorize_challenge(challenge, force_reauthorization=False)

    def authorize_step_up(self, challenge_headers: Mapping[str, str]) -> None:
        """Run an explicit, user-mediated OAuth scope upgrade."""

        challenge = _parse_bearer_challenge(challenge_headers)
        if challenge.get("error") != "insufficient_scope" or not _scope_tokens(challenge.get("scope")):
            raise McpOAuthError("The MCP server did not request an OAuth scope upgrade")
        self._authorize_challenge(challenge, force_reauthorization=True)

    def queue_step_up(self, challenge_headers: Mapping[str, str]) -> bool:
        """Remember a 403 scope challenge without opening a browser or retrying a tool."""

        try:
            challenge = _parse_bearer_challenge(challenge_headers)
        except McpOAuthError:
            return False
        if challenge.get("error") != "insufficient_scope":
            return False
        added_scopes = _scope_tokens(challenge.get("scope"))
        if not added_scopes:
            raise McpOAuthError("The MCP server requested more permission without naming the required scopes")
        with self._lock:
            record = self._load_record()
            if record is None or not (record.get("access_token") or record.get("refresh_token")):
                raise McpOAuthError("MCP sign-in is required before additional permission can be requested")
            requested_scopes = _scope_tokens_from_record(record.get("requested_scopes"))
            pending_scopes = _scope_tokens_from_record(record.get("pending_step_up_scopes"))
            record["pending_step_up_scopes"] = list(dict.fromkeys((*requested_scopes, *pending_scopes, *added_scopes)))
            record["pending_step_up_resource_metadata"] = challenge.get("resource_metadata")
            record["pending_step_up_issuer"] = record["issuer"]
            self._save_record(record)
        return True

    def resume_pending_step_up(self) -> bool:
        """Complete a queued scope upgrade only when the user explicitly reconnects in Settings."""

        with self._lock:
            record = self._load_record()
            if record is None:
                return False
            pending_scopes = _scope_tokens_from_record(record.get("pending_step_up_scopes"))
            if not pending_scopes:
                return False
            challenge: dict[str, object] = {"scope": " ".join(pending_scopes)}
            resource_metadata = record.get("pending_step_up_resource_metadata")
            if type(resource_metadata) is str:
                challenge["resource_metadata"] = resource_metadata
            expected_issuer = record.get("pending_step_up_issuer")
            if type(expected_issuer) is not str:
                raise McpOAuthError("Saved MCP permission request is invalid")
            self._authorize_challenge(
                challenge,
                force_reauthorization=True,
                expected_issuer=expected_issuer,
            )
            return True

    def _authorize_challenge(
        self,
        challenge: Mapping[str, object],
        *,
        force_reauthorization: bool,
        expected_issuer: str | None = None,
    ) -> None:
        with self._lock:
            raw_resource_metadata = challenge.get("resource_metadata")
            if raw_resource_metadata is not None and type(raw_resource_metadata) is not str:
                raise McpOAuthError("The MCP authorization challenge is invalid")
            challenge_scope = challenge.get("scope")
            if challenge_scope is not None and type(challenge_scope) is not str:
                raise McpOAuthError("The MCP authorization challenge is invalid")
            resource_metadata = self._discover_resource_metadata(raw_resource_metadata)
            issuer = self._first_authorization_server(resource_metadata)
            if expected_issuer is not None and issuer != expected_issuer:
                raise McpOAuthError("The MCP authorization server changed; reconnect and review this server again")
            metadata = self._discover_authorization_server(issuer)
            record = self._load_record()
            if record is not None and record.get("issuer") != issuer:
                self._secret_store.delete(self._secret_ref)
                self._record = None
                self._loaded = True
                record = None
            if record is not None and not force_reauthorization:
                refresh_token = record.get("refresh_token")
                if type(refresh_token) is str and refresh_token:
                    try:
                        self._refresh(record, refresh_token)
                        return
                    except McpOAuthError as error:
                        if error.code != "invalid_grant":
                            raise
                        record.pop("access_token", None)
                        record.pop("refresh_token", None)
                        record.pop("expires_at", None)

            previous_scopes = _scope_tokens_from_record(record.get("requested_scopes")) if record else []
            scope = _requested_scope(
                challenge.get("scope"),
                resource_metadata,
                metadata,
                previously_requested=previous_scopes if force_reauthorization else (),
            )
            state = secrets.token_urlsafe(32)
            with _LoopbackCallback(expected_state=state, cancel_event=self._cancel_event) as callback:
                client_id = self._client_for_issuer(metadata, callback.redirect_uri, record)
                verifier = secrets.token_urlsafe(64)
                challenge_value = _pkce_challenge(verifier)
                authorization_url = _authorization_url(
                    self._string_field(metadata, "authorization_endpoint"),
                    client_id=client_id,
                    redirect_uri=callback.redirect_uri,
                    state=state,
                    challenge=challenge_value,
                    resource=self.resource_uri,
                    scope=scope,
                )
                stored: dict[str, object] = dict(record or {})
                stored.update({
                    "version": 1,
                    "resource": self.resource_uri,
                    "issuer": issuer,
                    "client_id": client_id,
                    "authorization_endpoint": self._string_field(metadata, "authorization_endpoint"),
                    "token_endpoint": self._string_field(metadata, "token_endpoint"),
                    "authorization_response_iss_parameter_supported": (
                        metadata.get("authorization_response_iss_parameter_supported") is True
                    ),
                    "requested_scopes": scope.split() if scope is not None else [],
                })
                self._record = stored
                self._save_record(stored)
                try:
                    if self._cancel_event.is_set():
                        raise McpOAuthError("MCP sign-in was cancelled", code="cancelled")
                    if not self._browser_open(authorization_url):
                        raise McpOAuthError("The system browser could not be opened for MCP sign-in")
                    code = callback.wait(
                        state=state,
                        issuer=issuer,
                        require_issuer=stored["authorization_response_iss_parameter_supported"] is True,
                    )
                    self._exchange_code(stored, code, callback.redirect_uri, verifier)
                except BaseException:
                    if self._record is stored and not stored.get("access_token"):
                        self._save_record(stored)
                    raise

    def _client_for_issuer(
        self,
        metadata: Mapping[str, object],
        redirect_uri: str,
        previous: dict[str, object] | None,
    ) -> str:
        if self.client_id is not None:
            return self.client_id
        if previous is not None:
            stored_client_id = previous.get("client_id")
            if type(stored_client_id) is str and stored_client_id:
                return stored_client_id
        registration_endpoint = metadata.get("registration_endpoint")
        if type(registration_endpoint) is str:
            registration = {
                "client_name": "AEGIS Cognition",
                "application_type": "native",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
            try:
                response = self._json_request(registration_endpoint, "POST", registration)
                client_id = _optional_string(response, "client_id", 2_048)
                auth_method = response.get("token_endpoint_auth_method")
                if client_id is not None and auth_method in {None, "none"}:
                    return client_id
            except McpOAuthError:
                if self.client_id is None:
                    raise McpOAuthError(
                        "Automatic client registration failed. Enter a pre-registered OAuth Client ID.",
                        code="client_id_required",
                    ) from None
            if self.client_id is None:
                raise McpOAuthError(
                    "Automatic client registration failed. Enter a pre-registered OAuth Client ID.",
                    code="client_id_required",
                )
            return self.client_id
        if self.client_id is None:
            raise McpOAuthError(
                "This server cannot register a client automatically. Enter a pre-registered OAuth Client ID.",
                code="client_id_required",
            )
        return self.client_id

    def _refresh(self, record: dict[str, object], refresh_token: str) -> None:
        token_endpoint = self._string_field(record, "token_endpoint")
        client_id = self._string_field(record, "client_id")
        response = self._form_request(
            token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "resource": self.resource_uri,
            },
        )
        token = self._token_response(response)
        self._update_tokens(record, token, previous_refresh_token=refresh_token)

    def _exchange_code(
        self,
        record: dict[str, object],
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> None:
        response = self._form_request(
            self._string_field(record, "token_endpoint"),
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._string_field(record, "client_id"),
                "code_verifier": verifier,
                "resource": self.resource_uri,
            },
        )
        token = self._token_response(response)
        record.pop("pending_step_up_scopes", None)
        record.pop("pending_step_up_resource_metadata", None)
        record.pop("pending_step_up_issuer", None)
        self._update_tokens(record, token)

    def _token_response(self, response: Mapping[str, object]) -> Mapping[str, object]:
        access_token = _optional_string(response, "access_token", 24 * 1024)
        token_type = _optional_string(response, "token_type", 32)
        if (
            access_token is None
            or not re_full_bearer_token(access_token)
            or token_type is None
            or token_type.casefold() != "bearer"
        ):
            raise McpOAuthError("The authorization server returned an invalid access token")
        expires_in = response.get("expires_in")
        if expires_in is not None and (type(expires_in) not in (int, float) or not 1 <= expires_in <= 31_536_000):
            raise McpOAuthError("The authorization server returned an invalid token lifetime")
        refresh_token = _optional_string(response, "refresh_token", 24 * 1024)
        if refresh_token is not None and any(ord(char) < 0x21 or ord(char) > 0x7E for char in refresh_token):
            raise McpOAuthError("The authorization server returned an invalid refresh token")
        result: dict[str, object] = {"access_token": access_token}
        if refresh_token is not None:
            result["refresh_token"] = refresh_token
        if expires_in is not None:
            result["expires_at"] = time.time() + float(expires_in)
        return result

    def _update_tokens(
        self,
        record: dict[str, object],
        response: Mapping[str, object],
        *,
        previous_refresh_token: str | None = None,
    ) -> None:
        record["access_token"] = response["access_token"]
        if "expires_at" in response:
            record["expires_at"] = response["expires_at"]
        else:
            record.pop("expires_at", None)
        refresh_token = response.get("refresh_token") or previous_refresh_token
        if refresh_token is not None:
            record["refresh_token"] = refresh_token
        self._record = record
        self._save_record(record)

    def _load_record(self) -> dict[str, object] | None:
        if self._loaded:
            return self._record
        try:
            raw = self._secret_store.lookup(self._secret_ref)
        except SecretStoreError:
            raise McpOAuthError(
                "The operating system secure credential store is unavailable; unlock or configure it and retry.",
                code="secure_store_unavailable",
            ) from None
        if raw is None:
            self._record = None
            self._loaded = True
            return None
        try:
            parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError):
            raise McpOAuthError("Saved MCP sign-in data is invalid; remove and reconnect this server") from None
        if (
            not isinstance(parsed, dict)
            or type(parsed.get("version")) is not int
            or parsed.get("version") != 1
            or parsed.get("resource") != self.resource_uri
            or type(parsed.get("issuer")) is not str
            or not parsed.get("issuer")
            or type(parsed.get("client_id")) is not str
            or not parsed.get("client_id")
            or len(parsed["client_id"]) > 2_048
            or type(parsed.get("authorization_endpoint")) is not str
            or type(parsed.get("token_endpoint")) is not str
        ):
            raise McpOAuthError("Saved MCP sign-in data does not match this server")
        if any(ord(char) < 0x20 for char in cast(str, parsed["client_id"])):
            raise McpOAuthError("Saved MCP sign-in data is invalid")
        _validate_https_url(cast(str, parsed["issuer"]), "saved authorization server issuer", allow_query=False)
        _validate_https_url(cast(str, parsed["authorization_endpoint"]), "saved authorization endpoint")
        _validate_https_url(cast(str, parsed["token_endpoint"]), "saved token endpoint")
        for field in ("access_token", "refresh_token"):
            token = parsed.get(field)
            if token is not None and (
                type(token) is not str
                or not token
                or len(token) > 24 * 1024
                or any(ord(char) < 0x21 or ord(char) > 0x7E for char in token)
            ):
                raise McpOAuthError("Saved MCP sign-in data contains an invalid token")
        access_token = parsed.get("access_token")
        if access_token is not None and not re_full_bearer_token(cast(str, access_token)):
            raise McpOAuthError("Saved MCP sign-in data contains an invalid access token")
        expires_at = parsed.get("expires_at")
        invalid_expiry = type(expires_at) not in (int, float)
        if type(expires_at) is int:
            invalid_expiry = expires_at <= 0 or expires_at > time.time() + 31_536_060
        elif type(expires_at) is float:
            invalid_expiry = not math.isfinite(expires_at) or expires_at <= 0 or expires_at > time.time() + 31_536_060
        if expires_at is not None and invalid_expiry:
            raise McpOAuthError("Saved MCP sign-in data contains an invalid token lifetime")
        issuer_check = parsed.get("authorization_response_iss_parameter_supported")
        if issuer_check is not None and type(issuer_check) is not bool:
            raise McpOAuthError("Saved MCP sign-in data is invalid")
        if "requested_scopes" in parsed:
            _scope_tokens_from_record(parsed["requested_scopes"])
        pending_scope_keys = {
            "pending_step_up_scopes",
            "pending_step_up_resource_metadata",
            "pending_step_up_issuer",
        }
        if pending_scope_keys.intersection(parsed):
            pending_scopes = _scope_tokens_from_record(parsed.get("pending_step_up_scopes"))
            pending_issuer = parsed.get("pending_step_up_issuer")
            pending_metadata = parsed.get("pending_step_up_resource_metadata")
            if (
                not pending_scopes
                or type(pending_issuer) is not str
                or pending_issuer != parsed["issuer"]
                or (pending_metadata is not None and type(pending_metadata) is not str)
            ):
                raise McpOAuthError("Saved MCP permission request is invalid")
            if pending_metadata is not None:
                _validate_https_url(pending_metadata, "saved protected resource metadata URL")
        if self.client_id is not None and parsed["client_id"] != self.client_id:
            self._record = None
            self._loaded = True
            return None
        self._record = parsed
        self._loaded = True
        return parsed

    def _save_record(self, record: Mapping[str, object]) -> None:
        try:
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            self._secret_store.store(self._secret_ref, encoded)
        except Exception:
            raise McpOAuthError("MCP sign-in could not be saved in the operating system's secure store") from None

    def _discover_resource_metadata(self, challenge_url: str | None) -> Mapping[str, object]:
        urls = (challenge_url,) if challenge_url is not None else _resource_metadata_urls(self.resource_uri)
        for url in urls:
            assert url is not None
            _validate_https_url(url, "protected resource metadata URL")
            response = self._request_json(url)
            if response.status == 200:
                metadata = _response_json(response)
                if metadata.get("resource") != self.resource_uri:
                    raise McpOAuthError("Protected resource metadata does not identify this MCP server")
                return metadata
        raise McpOAuthError("The MCP server did not provide protected resource metadata")

    def _first_authorization_server(self, metadata: Mapping[str, object]) -> str:
        servers = metadata.get("authorization_servers")
        if type(servers) is not list or not servers or len(servers) > _MAX_METADATA_SERVERS:
            raise McpOAuthError("Protected resource metadata has no supported authorization server")
        issuer = servers[0]
        if type(issuer) is not str:
            raise McpOAuthError("Protected resource metadata contains an invalid authorization server")
        _validate_https_url(issuer, "authorization server issuer", allow_query=False)
        if urlsplit(issuer).path not in {"", "/"}:
            issuer = issuer.rstrip("/")
        return issuer

    def _discover_authorization_server(self, issuer: str) -> Mapping[str, object]:
        for url in _authorization_metadata_urls(issuer):
            response = self._request_json(url)
            if response.status != 200:
                continue
            metadata = _response_json(response)
            if metadata.get("issuer") != issuer:
                raise McpOAuthError("Authorization server metadata issuer did not match its source URL")
            for field in ("authorization_endpoint", "token_endpoint"):
                endpoint = metadata.get(field)
                if type(endpoint) is not str:
                    raise McpOAuthError("Authorization server metadata is missing a required endpoint")
                _validate_https_url(endpoint, f"authorization {field}")
            challenge_methods = metadata.get("code_challenge_methods_supported")
            if type(challenge_methods) is list and "S256" not in challenge_methods:
                raise McpOAuthError("The authorization server does not support secure PKCE")
            return metadata
        raise McpOAuthError("The authorization server does not publish supported OAuth metadata")

    def _request_json(self, url: str) -> OAuthHttpResponse:
        return self._perform_request(url, "GET", {"Accept": "application/json"}, None, _MAX_JSON_BYTES)

    def _json_request(self, url: str, method: str, value: Mapping[str, object]) -> Mapping[str, object]:
        body = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
        response = self._perform_request(
            url,
            method,
            {"Accept": "application/json", "Content-Type": "application/json"},
            body,
            _MAX_JSON_BYTES,
        )
        return _response_json(response) if response.status in {200, 201} else {}

    def _form_request(self, url: str, fields: Mapping[str, str]) -> Mapping[str, object]:
        body = urlencode(fields).encode("ascii")
        response = self._perform_request(
            url,
            "POST",
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            body,
            _MAX_JSON_BYTES,
        )
        if response.status >= 400:
            try:
                error = _response_json(response).get("error")
            except McpOAuthError:
                error = None
            raise McpOAuthError(
                "The authorization server rejected the token request",
                code=error if type(error) is str else None,
            )
        if response.status != 200:
            raise McpOAuthError("The authorization server returned an unexpected token response")
        return _response_json(response)

    def _perform_request(
        self,
        url: str,
        method: str,
        headers: Mapping[str, str],
        body: bytes | None,
        max_bytes: int,
    ) -> OAuthHttpResponse:
        _validate_https_url(url, "OAuth endpoint")
        try:
            response = self._request(url, method, headers, body, max_bytes)
        except McpOAuthError:
            raise
        except Exception:
            raise McpOAuthError("An OAuth endpoint request failed") from None
        if (
            type(response) is not OAuthHttpResponse
            or type(response.body) is not bytes
            or len(response.body) > max_bytes
        ):
            raise McpOAuthError("An OAuth endpoint returned an invalid response")
        if 300 <= response.status < 400:
            raise McpOAuthError("OAuth endpoint redirects are not allowed")
        return response

    @staticmethod
    def _string_field(record: Mapping[str, object], name: str) -> str:
        value = record.get(name)
        if type(value) is not str or not value:
            raise McpOAuthError("Saved MCP sign-in data is incomplete")
        return value


class _LoopbackCallback:
    def __init__(
        self,
        *,
        expected_state: str,
        cancel_event: threading.Event,
        timeout_seconds: int = _CALLBACK_TIMEOUT_SECONDS,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._cancel_event = cancel_event
        self._result: dict[str, str] = {}
        self._attempts = 0
        result = self._result

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def handle(self) -> None:
                owner = cast(CallbackServer, self.server)
                request = bytearray()
                try:
                    self.connection.settimeout(0.25)
                    while b"\r\n\r\n" not in request:
                        remaining = owner.callback_deadline - time.monotonic()
                        if cancel_event.is_set() or remaining <= 0:
                            return
                        if not select.select([self.connection], [], [], min(0.25, remaining))[0]:
                            continue
                        chunk = self.connection.recv(min(4_096, _MAX_AUTH_RESPONSE_BYTES + 1 - len(request)))
                        if not chunk:
                            return
                        request.extend(chunk)
                        if len(request) > _MAX_AUTH_RESPONSE_BYTES:
                            return
                    remaining = owner.callback_deadline - time.monotonic()
                    if cancel_event.is_set() or remaining <= 0:
                        return
                    # Parse complete bounded headers; stdlib reads must not block the OAuth worker.
                    self.rfile.close()
                    self.rfile = io.BytesIO(request)
                    self.connection.settimeout(min(0.25, remaining))
                    super().handle()
                except OSError:
                    return

            def do_GET(self) -> None:
                owner = cast(CallbackServer, self.server)
                if (
                    self.client_address[0] != "127.0.0.1"
                    or self.headers.get("Host") != f"127.0.0.1:{owner.server_port}"
                    or len(self.path) > _MAX_AUTH_RESPONSE_BYTES
                ):
                    self._reply(400, "Invalid callback")
                    return
                parsed = urlsplit(self.path)
                if parsed.path != "/oauth/callback" or parsed.fragment:
                    self._reply(404, "Invalid callback")
                    return
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
                except ValueError:
                    self._reply(400, "Invalid callback")
                    return
                if any(len(values) != 1 for values in query.values()):
                    self._reply(400, "Invalid callback")
                    return
                owner.callback_attempts += 1
                if not hmac.compare_digest(query.get("state", [""])[0], expected_state):
                    self._reply(400, "Invalid callback")
                    return
                for key in ("state", "code", "iss", "error"):
                    if key in query:
                        result[key] = query[key][0]
                self._reply(200, "Sign-in response received. You can return to AEGIS.")

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _reply(self, status: int, message: str) -> None:
                body = message.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

        class CallbackServer(http.server.HTTPServer):
            callback_attempts: int
            callback_deadline: float

        try:
            self._server = CallbackServer(("127.0.0.1", 0), CallbackHandler)
        except OSError:
            raise McpOAuthError("A secure local sign-in callback could not be started") from None
        self._server.timeout = 0.25
        self._server.callback_attempts = 0
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_port}/oauth/callback"

    def __enter__(self) -> _LoopbackCallback:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._server.server_close()

    def wait(self, *, state: str, issuer: str, require_issuer: bool) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        self._server.callback_deadline = deadline
        while (
            time.monotonic() < deadline
            and self._attempts < 3
            and not self._result
            and not self._cancel_event.is_set()
        ):
            self._server.timeout = min(0.25, max(0.0, deadline - time.monotonic()))
            self._server.handle_request()
            self._attempts = self._server.callback_attempts
        if not self._result:
            if self._cancel_event.is_set():
                raise McpOAuthError("MCP sign-in was cancelled", code="cancelled")
            raise McpOAuthError("MCP sign-in was cancelled or timed out")
        if not hmac.compare_digest(self._result.get("state", ""), state):
            raise McpOAuthError("MCP sign-in response state did not match")
        response_issuer = self._result.get("iss")
        if (require_issuer and response_issuer is None) or (response_issuer is not None and response_issuer != issuer):
            raise McpOAuthError("MCP sign-in response issuer did not match")
        if self._result.get("error") is not None or not self._result.get("code"):
            raise McpOAuthError("MCP sign-in was declined by the authorization server")
        code = self._result.get("code")
        if not code or len(code) > _MAX_AUTH_RESPONSE_BYTES:
            raise McpOAuthError("MCP sign-in response did not contain a valid authorization code")
        return code


def _parse_bearer_challenge(headers: Mapping[str, str]) -> dict[str, str]:
    raw = next((value for key, value in headers.items() if key.casefold() == "www-authenticate"), "")
    pieces: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for char in raw:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
            current.append(char)
        elif char == "," and not quoted:
            pieces.append("".join(current).strip())
            current.clear()
        else:
            current.append(char)
    if current:
        pieces.append("".join(current).strip())
    result: dict[str, str] = {}
    bearer = False
    for piece in pieces:
        if piece.casefold() == "bearer":
            bearer = True
            continue
        if piece.casefold().startswith("bearer "):
            bearer = True
            piece = piece[7:].strip()
        name, separator, value = piece.partition("=")
        if separator and re_full_token(name.strip()):
            normalized = value.strip()
            if normalized.startswith('"') and normalized.endswith('"') and len(normalized) >= 2:
                normalized = normalized[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            if normalized:
                result[name.strip().casefold()] = normalized
    if not bearer:
        raise McpOAuthError("MCP server did not provide a Bearer authorization challenge")
    metadata_url = result.get("resource_metadata")
    if metadata_url is not None:
        _validate_https_url(metadata_url, "protected resource metadata URL")
    scope = result.get("scope")
    if scope is not None and (len(scope) > 4_096 or any(ord(char) < 0x20 for char in scope)):
        raise McpOAuthError("MCP server returned an invalid OAuth scope challenge")
    return result


def _canonical_resource_uri(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
    ):
        raise McpOAuthError("OAuth requires an HTTPS MCP endpoint without URL credentials, query, or fragment")
    host = parsed.hostname.casefold().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise McpOAuthError("MCP server endpoint port is invalid") from None
    host_literal = f"[{host}]" if ":" in host else host
    netloc = f"{host_literal}:{port}" if port not in (None, 443) else host_literal
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def _resource_metadata_urls(resource_uri: str) -> tuple[str, ...]:
    parsed = urlsplit(resource_uri)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    suffix = parsed.path if parsed.path not in {"", "/"} else ""
    path_specific = f"{origin}/.well-known/oauth-protected-resource{suffix}"
    root = f"{origin}/.well-known/oauth-protected-resource"
    return (path_specific, root) if suffix else (root,)


def _authorization_metadata_urls(issuer: str) -> tuple[str, ...]:
    parsed = urlsplit(issuer)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    path = parsed.path.rstrip("/")
    if not path:
        return (
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        )
    return (
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
        f"{issuer.rstrip('/')}/.well-known/openid-configuration",
    )


def _validate_https_url(value: str, label: str, *, allow_query: bool = True) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 8_192
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise McpOAuthError(f"{label} is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise McpOAuthError(f"{label} is invalid") from None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (not allow_query and parsed.query)
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise McpOAuthError(f"{label} must be an absolute HTTPS URL without credentials or a fragment")


def _requested_scope(
    challenge_scope: str | None,
    resource_metadata: Mapping[str, object],
    authorization_metadata: Mapping[str, object],
    *,
    previously_requested: tuple[str, ...] | list[str] = (),
) -> str | None:
    scopes: list[str]
    if challenge_scope is not None:
        scopes = _scope_tokens(challenge_scope)
    else:
        raw = resource_metadata.get("scopes_supported")
        if raw is None:
            return None
        if type(raw) is not list or len(raw) > _MAX_SCOPE_COUNT:
            raise McpOAuthError("Protected resource metadata contains invalid scopes")
        scopes = []
        for item in raw:
            item_scopes = _scope_tokens(item)
            if len(item_scopes) != 1:
                raise McpOAuthError("Protected resource metadata contains invalid scopes")
            scopes.extend(item_scopes)
    scopes = [*_scope_tokens_from_record(list(previously_requested)), *scopes]
    authorization_scopes = authorization_metadata.get("scopes_supported")
    if authorization_scopes is not None:
        if (
            type(authorization_scopes) is not list
            or len(authorization_scopes) > _MAX_SCOPE_COUNT
        ):
            raise McpOAuthError("Authorization server metadata contains invalid scopes")
        if any(len(_scope_tokens(item)) != 1 for item in authorization_scopes):
            raise McpOAuthError("Authorization server metadata contains invalid scopes")
        if "offline_access" in authorization_scopes and "offline_access" not in scopes:
            scopes.append("offline_access")
    if len(scopes) > _MAX_SCOPE_COUNT:
        raise McpOAuthError("OAuth scope set is invalid")
    return " ".join(dict.fromkeys(scopes)) if scopes else None


def _scope_tokens(value: object) -> list[str]:
    if value is None:
        return []
    if type(value) is not str or len(value) > 4_096 or any(ord(char) < 0x20 or ord(char) > 0x7E for char in value):
        raise McpOAuthError("OAuth scope set is invalid")
    tokens = value.split()
    if len(tokens) > _MAX_SCOPE_COUNT or any(
        not token
        or len(token) > 256
        or any(not (char == "!" or "#" <= char <= "[" or "]" <= char <= "~") for char in token)
        for token in tokens
    ):
        raise McpOAuthError("OAuth scope set is invalid")
    return list(dict.fromkeys(tokens))


def _scope_tokens_from_record(value: object) -> list[str]:
    if value is None:
        return []
    if type(value) is not list or len(value) > _MAX_SCOPE_COUNT:
        raise McpOAuthError("Saved MCP sign-in data contains invalid scopes")
    result: list[str] = []
    for item in value:
        tokens = _scope_tokens(item)
        if len(tokens) != 1:
            raise McpOAuthError("Saved MCP sign-in data contains invalid scopes")
        result.extend(tokens)
    return list(dict.fromkeys(result))


def _authorization_url(
    endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    resource: str,
    scope: str | None,
) -> str:
    parsed = urlsplit(endpoint)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    if scope is not None:
        params["scope"] = scope
    try:
        existing_query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        raise McpOAuthError("Authorization endpoint query is invalid") from None
    if set(existing_query).intersection(params):
        raise McpOAuthError("Authorization endpoint query conflicts with required OAuth parameters")
    query = "&".join(item for item in (parsed.query, urlencode(params)) if item)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _secret_reference(profile_id: str, server_id: str, resource_uri: str) -> str:
    material = "\x00".join((profile_id, server_id, resource_uri)).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return f"os-keyring:mcp-oauth:{digest}"


def _bounded_identity(value: str, label: str) -> str:
    if type(value) is not str or not value or len(value) > 128 or any(ord(char) < 0x20 for char in value):
        raise McpOAuthError(f"MCP OAuth {label} is invalid")
    return value


def _optional_string(value: Mapping[str, object], key: str, max_length: int) -> str | None:
    candidate = value.get(key)
    if candidate is None:
        return None
    if (
        type(candidate) is not str
        or not candidate
        or len(candidate) > max_length
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate)
    ):
        raise McpOAuthError("The authorization server returned invalid metadata")
    return candidate


def _response_json(response: OAuthHttpResponse) -> Mapping[str, object]:
    if response.status >= 400 or len(response.body) > _MAX_JSON_BYTES:
        raise McpOAuthError("An OAuth metadata request failed")
    content_type = next((value for key, value in response.headers.items() if key.casefold() == "content-type"), "")
    media_type = content_type.partition(";")[0].strip().casefold()
    if media_type and media_type != "application/json" and not media_type.endswith("+json"):
        raise McpOAuthError("An OAuth endpoint did not return JSON")
    try:
        result = json.loads(
            response.body.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise McpOAuthError("An OAuth endpoint returned invalid JSON") from None
    if not isinstance(result, dict):
        raise McpOAuthError("An OAuth endpoint returned an invalid object")
    return result


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is invalid: {value}")


def re_full_token(value: str) -> bool:
    return bool(value) and all(char.isalnum() or char in "!#$%&'*+-.^_`|~" for char in value)


def re_full_bearer_token(value: str) -> bool:
    token = value.rstrip("=")
    return (
        bool(token)
        and all(char.isascii() and (char.isalnum() or char in "-._~+/") for char in token)
        and value.count("=") == len(value) - len(token)
    )


__all__ = ["McpOAuthClient", "McpOAuthError", "OAuthHttpResponse"]
