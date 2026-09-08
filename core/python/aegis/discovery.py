"""Bounded model discovery for configured provider connections.

Discovery is metadata-only. It never persists credential material, follows a
redirect, or treats an unrecognized capability claim as known.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .secrets import PlatformSecretStore

if TYPE_CHECKING:
    from .connections import ConnectionRecord

MAX_DISCOVERY_BODY_BYTES = 4 * 1024 * 1024
MAX_DISCOVERY_MODELS = 2_000
DISCOVERY_TIMEOUT_SECONDS = 30.0


class DiscoveryError(RuntimeError):
    """A bounded, user-actionable model discovery failure."""


@dataclass(frozen=True)
class DiscoveryResponse:
    status: int
    body: bytes
    content_type: str | None = None


DiscoveryRequester = Callable[[str, Mapping[str, str], float], DiscoveryResponse]
SecretResolver = Callable[[str], str | None]
EgressCheck = Callable[[str], bool]


def discover_connection_models(
    connection: ConnectionRecord,
    *,
    secret_resolver: SecretResolver | None = None,
    requester: DiscoveryRequester | None = None,
    egress_check: EgressCheck | None = None,
    timeout_seconds: float = DISCOVERY_TIMEOUT_SECONDS,
    max_body_bytes: int = MAX_DISCOVERY_BODY_BYTES,
) -> tuple[dict[str, object], ...]:
    """Fetch and validate a provider model catalog without invoking a model."""

    url = _models_url(connection.endpoint)
    endpoint_host = urlsplit(connection.endpoint).hostname or ""
    if egress_check is None and not _is_loopback_host(endpoint_host):
        raise DiscoveryError("connection egress grant is required")
    if egress_check is not None:
        try:
            allowed = egress_check(connection.connection_id)
        except Exception as exc:
            raise DiscoveryError("connection egress policy could not be evaluated") from exc
        if not isinstance(allowed, bool) or not allowed:
            raise DiscoveryError("connection egress is denied")
    if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
        raise ValueError("timeout_seconds must be numeric")
    if timeout_seconds <= 0 or timeout_seconds > DISCOVERY_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 0 and {DISCOVERY_TIMEOUT_SECONDS:g}")
    if (
        not isinstance(max_body_bytes, int)
        or isinstance(max_body_bytes, bool)
        or max_body_bytes < 1
        or max_body_bytes > MAX_DISCOVERY_BODY_BYTES
    ):
        raise ValueError(f"max_body_bytes must be between 1 and {MAX_DISCOVERY_BODY_BYTES}")

    headers: dict[str, str] = {"Accept": "application/json"}
    if connection.secret_ref is not None:
        resolver = secret_resolver or PlatformSecretStore().resolve
        try:
            secret = resolver(connection.secret_ref)
        except Exception as exc:
            raise DiscoveryError("connection credential is unavailable") from exc
        if not isinstance(secret, str) or not secret:
            raise DiscoveryError("connection credential is unavailable")
        headers["Authorization"] = f"Bearer {secret}"

    response = (requester or _request_json)(url, headers, float(timeout_seconds))
    if not isinstance(response.status, int) or isinstance(response.status, bool) or response.status != 200:
        raise DiscoveryError(f"model discovery returned HTTP {response.status}")
    if len(response.body) > max_body_bytes:
        raise DiscoveryError("model discovery response exceeds the configured size limit")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("model discovery response is not valid UTF-8 JSON") from exc
    return _normalize_models(payload)


def _models_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise DiscoveryError("provider endpoint is not a supported URL")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise DiscoveryError("provider endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname or ""):
        raise DiscoveryError("plain HTTP discovery is allowed only for loopback endpoints")
    path = parsed.path.rstrip("/") + "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _is_loopback_host(host: str) -> bool:
    normalized = host.lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _request_json(url: str, headers: Mapping[str, str], timeout_seconds: float) -> DiscoveryResponse:
    request = Request(url, headers=dict(headers), method="GET")
    opener = build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read(MAX_DISCOVERY_BODY_BYTES + 1)
            return DiscoveryResponse(
                status=int(response.status),
                body=body,
                content_type=response.headers.get("Content-Type"),
            )
    except HTTPError as exc:
        raise DiscoveryError(f"model discovery returned HTTP {exc.code}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise DiscoveryError("model discovery request failed") from exc


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_301(self, _request: Request, _fp: object, _code: int, _msg: str, _headers: object) -> None:
        raise DiscoveryError("model discovery redirects are not allowed")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def _normalize_models(payload: object) -> tuple[dict[str, object], ...]:
    if not isinstance(payload, Mapping):
        raise DiscoveryError("model discovery payload must be an object")
    raw_models = payload.get("data", payload.get("models"))
    if not isinstance(raw_models, list):
        raise DiscoveryError("model discovery payload must contain a models array")
    if len(raw_models) > MAX_DISCOVERY_MODELS:
        raise DiscoveryError("model discovery catalog exceeds the configured entry limit")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            raise DiscoveryError("model discovery entries must be objects")
        model_id = raw.get("id", raw.get("model_id"))
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 256:
            raise DiscoveryError("model discovery entry has an invalid model id")
        if model_id in seen:
            continue
        seen.add(model_id)
        capabilities = _string_list(raw.get("capabilities"))
        normalized.append(
            {
                "model_id": model_id,
                "family": _optional_text(raw.get("family")),
                "capabilities": capabilities,
                "context_limit": _optional_positive_int(raw.get("context_limit")),
                "output_limit": _optional_positive_int(raw.get("output_limit")),
                "source": "discovered",
            }
        )
    return tuple(normalized)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise DiscoveryError("model discovery text metadata is invalid")
    return value


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64:
        raise DiscoveryError("model discovery capabilities are invalid")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in value):
        raise DiscoveryError("model discovery capabilities are invalid")
    return sorted(set(value))


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DiscoveryError("model discovery limits must be positive integers")
    return value


__all__ = [
    "DISCOVERY_TIMEOUT_SECONDS",
    "MAX_DISCOVERY_MODELS",
    "DiscoveryError",
    "DiscoveryResponse",
    "discover_connection_models",
]
