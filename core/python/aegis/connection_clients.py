"""Provider clients bound to a stored connection record.

The first adapter is the OpenAI-compatible chat protocol. It covers local
compatible servers and hosted compatible endpoints while keeping endpoint,
credential, timeout and response-size policy in one small boundary.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .connections import ConnectionRecord
from .secrets import PlatformSecretStore

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 180.0


class ConnectionClientError(RuntimeError):
    """A provider request failed at the connection boundary."""


@dataclass(frozen=True)
class ConnectionResponse:
    status: int
    body: bytes


ConnectionRequester = Callable[[str, Mapping[str, str], bytes, float], ConnectionResponse]
SecretResolver = Callable[[str], str | None]


class OpenAICompatibleClient:
    """Small synchronous/async client for the chat-completions contract."""

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
        self._secret_resolver = secret_resolver or PlatformSecretStore().resolve
        self._requester = requester or _request_json
        self._egress_check = egress_check
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes

    def invoke(self, prompt: str, **kwargs: object) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        for name in ("temperature", "top_p", "max_tokens", "response_format"):
            if name in kwargs:
                payload[name] = kwargs[name]
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
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ConnectionClientError("provider response has no text content")
        return str(message["content"])

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
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.connection.secret_ref is not None:
            try:
                secret = self._secret_resolver(self.connection.secret_ref)
            except Exception as exc:
                raise ConnectionClientError("connection credential is unavailable") from exc
            if not isinstance(secret, str) or not secret:
                raise ConnectionClientError("connection credential is unavailable")
            headers["Authorization"] = f"Bearer {secret}"
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        response = self._requester(url, headers, body, self._timeout_seconds)
        if not isinstance(response.status, int) or isinstance(response.status, bool):
            raise ConnectionClientError("provider response status is invalid")
        if response.status < 200 or response.status >= 300:
            raise ConnectionClientError(f"provider request returned HTTP {response.status}")
        if len(response.body) > self._max_response_bytes:
            raise ConnectionClientError("provider response exceeds the configured size limit")
        return response


def _chat_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ConnectionClientError("provider endpoint is not a supported URL")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise ConnectionClientError("provider endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname or ""):
        raise ConnectionClientError("plain HTTP requests are allowed only for loopback endpoints")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/chat/completions", "", ""))


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


__all__ = ["ConnectionClientError", "ConnectionResponse", "OpenAICompatibleClient"]
