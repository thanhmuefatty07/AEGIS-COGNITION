"""Bounded model discovery for configured provider connections.

Discovery is metadata-only. It never persists credential material, follows a
redirect, or treats an unrecognized capability claim as known.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .provider_models import is_reasoning_effort_value, provider_reasoning_profile
from .provider_registry import provider_spec_for_endpoint
from .secrets import PlatformSecretStore

if TYPE_CHECKING:
    from .connections import ConnectionRecord

MAX_DISCOVERY_BODY_BYTES = 4 * 1024 * 1024
MAX_DISCOVERY_MODELS = 2_000
DISCOVERY_TIMEOUT_SECONDS = 30.0
ANTHROPIC_MODELS_PAGE_SIZE = 1_000
MAX_ANTHROPIC_MODELS_PAGES = 10
GOOGLE_MODELS_PAGE_SIZE = 1_000
MAX_GOOGLE_MODELS_PAGES = 10
OPENROUTER_MODELS_PAGE_SIZE = 1_000
MAX_OPENROUTER_MODELS_PAGES = 3
_PROFILE_CAPABILITY = "capability:provider-profile"


class DiscoveryError(RuntimeError):
    """A bounded, user-actionable model discovery failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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

    endpoint_host = urlsplit(connection.endpoint).hostname or ""
    provider = provider_spec_for_endpoint(connection.provider_kind, endpoint_host)
    catalog_strategy = provider.catalog_strategy if provider is not None else "compatible"
    is_google_api = catalog_strategy == "google"
    is_openrouter_api = catalog_strategy == "openrouter"
    compatible_models_url = _models_url(connection.endpoint, provider_kind=connection.provider_kind)
    url = _google_models_url(connection.endpoint) if is_google_api else compatible_models_url
    if is_openrouter_api and connection.secret_ref is not None:
        url = _openrouter_user_models_url(compatible_models_url)
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
        if provider is not None and provider.discovery_auth == "google-key":
            headers["x-goog-api-key"] = secret
        elif (
            provider is not None and provider.discovery_auth == "anthropic-key"
        ) or connection.protocol == "anthropic-messages":
            headers["x-api-key"] = secret
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {secret}"

    request_page = requester or _request_json
    if catalog_strategy == "anthropic":
        payload = _discover_anthropic_model_pages(
            url,
            headers,
            request_page,
            timeout_seconds=float(timeout_seconds),
            max_body_bytes=max_body_bytes,
        )
    elif is_google_api:
        payload = _discover_google_model_pages(
            url,
            headers,
            request_page,
            timeout_seconds=float(timeout_seconds),
            max_body_bytes=max_body_bytes,
        )
    elif is_openrouter_api:
        payload = _discover_openrouter_model_pages(
            url,
            headers,
            request_page,
            timeout_seconds=float(timeout_seconds),
            max_body_bytes=max_body_bytes,
        )
    else:
        payload = _request_model_page(url, headers, request_page, float(timeout_seconds), max_body_bytes)
    return _normalize_models(payload, provider_kind=connection.provider_kind, endpoint=connection.endpoint)


def _google_models_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/openai"):
        path = path.removesuffix("/openai")
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/models", "", ""))


def _openrouter_user_models_url(models_url: str) -> str:
    parsed = urlsplit(models_url)
    if not parsed.path.endswith("/models"):
        raise DiscoveryError("OpenRouter user model endpoint is invalid")
    path = parsed.path + "/user"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _request_model_page(
    url: str,
    headers: Mapping[str, str],
    requester: DiscoveryRequester,
    timeout_seconds: float,
    max_body_bytes: int,
) -> dict[str, object]:
    response = requester(url, headers, timeout_seconds)
    if not isinstance(response.status, int) or isinstance(response.status, bool) or response.status != 200:
        status_code = response.status if isinstance(response.status, int) and not isinstance(response.status, bool) else None
        raise DiscoveryError(f"model discovery returned HTTP {response.status}", status_code=status_code)
    if not isinstance(response.body, bytes) or len(response.body) > max_body_bytes:
        raise DiscoveryError("model discovery response exceeds the configured size limit")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("model discovery response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise DiscoveryError("model discovery payload must be an object")
    return payload


def _discover_anthropic_model_pages(
    base_url: str,
    headers: Mapping[str, str],
    requester: DiscoveryRequester,
    *,
    timeout_seconds: float,
    max_body_bytes: int,
) -> dict[str, object]:
    models: list[object] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _page_number in range(MAX_ANTHROPIC_MODELS_PAGES):
        query: dict[str, str] = {"limit": str(ANTHROPIC_MODELS_PAGE_SIZE)}
        if cursor is not None:
            query["after_id"] = cursor
        payload = _request_model_page(
            f"{base_url}?{urlencode(query)}",
            headers,
            requester,
            timeout_seconds,
            max_body_bytes,
        )
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise DiscoveryError("model discovery payload must contain a models array")
        if len(models) + len(raw_models) > MAX_DISCOVERY_MODELS:
            raise DiscoveryError("model discovery catalog exceeds the configured entry limit")
        models.extend(raw_models)

        has_more = payload.get("has_more")
        if type(has_more) is not bool:
            raise DiscoveryError("model discovery pagination state is invalid")
        if not has_more:
            payload["data"] = models
            return payload

        next_cursor = payload.get("last_id")
        if not isinstance(next_cursor, str) or not next_cursor.strip() or next_cursor in seen_cursors:
            raise DiscoveryError("model discovery pagination cursor is invalid")
        if len(models) >= MAX_DISCOVERY_MODELS:
            raise DiscoveryError("model discovery catalog exceeds the configured entry limit")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    raise DiscoveryError("model discovery pagination exceeds the configured page limit")


def _discover_google_model_pages(
    base_url: str,
    headers: Mapping[str, str],
    requester: DiscoveryRequester,
    *,
    timeout_seconds: float,
    max_body_bytes: int,
) -> dict[str, object]:
    models: list[object] = []
    seen_tokens: set[str] = set()
    page_token: str | None = None
    for _page_number in range(MAX_GOOGLE_MODELS_PAGES):
        query = {"pageSize": str(GOOGLE_MODELS_PAGE_SIZE)}
        if page_token is not None:
            query["pageToken"] = page_token
        payload = _request_model_page(
            f"{base_url}?{urlencode(query)}",
            headers,
            requester,
            timeout_seconds,
            max_body_bytes,
        )
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            raise DiscoveryError("model discovery payload must contain a models array")
        if len(models) + len(raw_models) > MAX_DISCOVERY_MODELS:
            raise DiscoveryError("model discovery catalog exceeds the configured entry limit")
        models.extend(raw_models)

        next_token = payload.get("nextPageToken")
        if next_token is None or next_token == "":
            return {"models": models}
        if not isinstance(next_token, str) or len(next_token) > 4_096 or next_token in seen_tokens:
            raise DiscoveryError("model discovery pagination token is invalid")
        if len(models) >= MAX_DISCOVERY_MODELS:
            raise DiscoveryError("model discovery catalog exceeds the configured entry limit")
        seen_tokens.add(next_token)
        page_token = next_token

    raise DiscoveryError("model discovery pagination exceeds the configured page limit")


def _discover_openrouter_model_pages(
    base_url: str,
    headers: Mapping[str, str],
    requester: DiscoveryRequester,
    *,
    timeout_seconds: float,
    max_body_bytes: int,
) -> dict[str, object]:
    models: list[object] = []
    offset = 0
    for _page_number in range(MAX_OPENROUTER_MODELS_PAGES):
        query = urlencode({"offset": str(offset), "limit": str(OPENROUTER_MODELS_PAGE_SIZE)})
        payload = _request_model_page(
            f"{base_url}?{query}",
            headers,
            requester,
            timeout_seconds,
            max_body_bytes,
        )
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise DiscoveryError("model discovery payload must contain a models array")
        if len(models) + len(raw_models) > MAX_DISCOVERY_MODELS:
            raise DiscoveryError("model discovery catalog exceeds the configured entry limit")
        models.extend(raw_models)
        if len(raw_models) < OPENROUTER_MODELS_PAGE_SIZE:
            return {"data": models}
        offset += len(raw_models)

    raise DiscoveryError("model discovery pagination exceeds the configured page limit")


def _models_url(endpoint: str, *, provider_kind: str = "openai-compatible") -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise DiscoveryError("provider endpoint is not a supported URL")
    if parsed.query or parsed.fragment or not parsed.netloc:
        raise DiscoveryError("provider endpoint must not contain query or fragment data")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname or ""):
        raise DiscoveryError("plain HTTP discovery is allowed only for loopback endpoints")
    spec = provider_spec_for_endpoint(provider_kind, parsed.hostname or "")
    resource = spec.models_resource if spec is not None else "models"
    path = parsed.path.rstrip("/") + f"/{resource}"
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
        raise DiscoveryError(f"model discovery returned HTTP {exc.code}", status_code=exc.code) from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise DiscoveryError("model discovery request failed") from exc


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_301(self, _request: Request, _fp: object, _code: int, _msg: str, _headers: object) -> None:
        raise DiscoveryError("model discovery redirects are not allowed")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def _normalize_models(
    payload: object,
    *,
    provider_kind: str = "openai-compatible",
    endpoint: str | None = None,
) -> tuple[dict[str, object], ...]:
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
        model_id = raw.get("id", raw.get("model_id", raw.get("name")))
        if isinstance(model_id, str) and model_id.startswith("models/"):
            model_id = model_id.removeprefix("models/")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 256:
            raise DiscoveryError("model discovery entry has an invalid model id")
        if model_id in seen:
            continue
        seen.add(model_id)
        capabilities = _normalized_capabilities(raw, model_id, provider_kind=provider_kind, endpoint=endpoint)
        if _is_non_chat_model(raw, model_id, capabilities, provider_kind=provider_kind):
            continue
        normalized.append(
            {
                "model_id": model_id,
                "family": _optional_text(
                    raw.get(
                        "family",
                        raw.get("name", raw.get("display_name", raw.get("displayName", raw.get("owned_by")))),
                    )
                ),
                "capabilities": capabilities,
                "context_limit": _optional_positive_int(
                    raw.get(
                        "context_limit",
                        raw.get(
                            "context_window",
                            raw.get(
                                "context_length",
                                raw.get("inputTokenLimit", raw.get("input_token_limit", raw.get("max_input_tokens"))),
                            ),
                        ),
                    )
                ),
                "output_limit": _optional_positive_int(
                    raw.get(
                        "output_limit",
                        raw.get(
                            "max_output_tokens",
                            raw.get("outputTokenLimit", raw.get("output_token_limit", raw.get("max_tokens"))),
                        ),
                    )
                ),
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


def _supported_capability(value: object) -> bool | None:
    if type(value) is bool:
        return value
    if not isinstance(value, Mapping):
        return None
    supported = value.get("supported")
    return supported if type(supported) is bool else None


def _normalized_effort_list(value: list[object]) -> tuple[str, ...]:
    if len(value) > 64:
        raise DiscoveryError("model discovery reasoning metadata exceeds the configured entry limit")
    declared: dict[str, str] = {}
    for item in value:
        if is_reasoning_effort_value(item):
            declared.setdefault(item.casefold(), item)
    return tuple(declared.values())


def _reported_effort_capabilities(
    capability_map: Mapping[str, object] | None,
) -> tuple[bool, tuple[str, ...]]:
    if capability_map is None:
        return False, ()
    effort = capability_map.get("effort", capability_map.get("reasoning_effort"))
    if isinstance(effort, list):
        return True, _normalized_effort_list(effort)
    if not isinstance(effort, Mapping):
        return False, ()
    if effort.get("supported") is False:
        return True, ()
    supported = _normalized_effort_list(
        [
            name
            for name, value in effort.items()
            if isinstance(name, str)
            and name.casefold() != "supported"
            and (_supported_capability(value) is True or value is True)
        ]
    )
    return True, supported


def _reported_thinking_modes(capability_map: Mapping[str, object] | None) -> tuple[bool, set[str]]:
    if capability_map is None or not isinstance(capability_map.get("thinking"), Mapping):
        return False, set()
    thinking = capability_map["thinking"]
    if thinking.get("supported") is False:
        return True, set()
    types = thinking.get("types")
    if not isinstance(types, Mapping):
        return True, set()
    modes = {
        name
        for name in ("adaptive", "enabled")
        if _supported_capability(types.get(name)) is True or types.get(name) is True
    }
    return True, modes


def _normalized_capabilities(
    raw: Mapping[str, object],
    model_id: str,
    *,
    provider_kind: str,
    endpoint: str | None,
) -> list[str]:
    """Keep provider metadata bounded while exposing only useful UI markers.

    Provider model-list endpoints do not share one capability schema.  We
    retain their declared strings and add canonical markers only when a
    field or a conservative model-family signal supports them.  The markers
    are stored in the existing capability column, so no catalog migration is
    needed.
    """

    endpoint_host = (urlsplit(endpoint).hostname or "").casefold() if isinstance(endpoint, str) else ""
    is_deepseek_catalog = provider_kind.casefold() == "deepseek" and endpoint_host == "api.deepseek.com"
    is_openrouter_catalog = provider_kind.casefold() == "openrouter" and endpoint_host in {
        "openrouter.ai",
        "eu.openrouter.ai",
    }
    capability_metadata = raw.get("capabilities", raw.get("supportedGenerationMethods"))
    capability_map = capability_metadata if isinstance(capability_metadata, Mapping) else None
    capabilities = [] if capability_map is not None else _string_list(capability_metadata)
    normalized = {item.casefold() for item in capabilities}
    added: set[str] = set()
    explicit_vision = any(
        item == "vision" or item == "image" or "vision" in item or "image" in item for item in normalized
    )

    modality_values: list[str] = []
    modality_sources: list[Mapping[str, object]] = [raw]
    architecture = raw.get("architecture")
    if isinstance(architecture, Mapping):
        modality_sources.append(architecture)
    for source in modality_sources:
        for key in (
            "input_modalities",
            "inputModalities",
            "supported_modalities",
            "supportedModalities",
            "modalities",
        ):
            value = source.get(key)
            if isinstance(value, list):
                modality_values.extend(item.casefold() for item in value if isinstance(item, str))

    reasoning_values: list[str] = []
    top_level_efforts: tuple[str, ...] | None = None
    deepseek_effort_declared = False
    effort_metadata = raw.get("effort")
    if is_deepseek_catalog and isinstance(effort_metadata, Mapping) and "supported_levels" in effort_metadata:
        supported_levels = effort_metadata["supported_levels"]
        if not isinstance(supported_levels, list):
            raise DiscoveryError("model discovery reasoning metadata is invalid")
        top_level_efforts = _normalized_effort_list(supported_levels)
        deepseek_effort_declared = True

    # OpenRouter and a few gateways put parameter support beside the model
    # architecture. The parameter name alone does not establish exact effort
    # levels; an explicit per-model supported_efforts list does.
    parameter_sources: list[object] = [
        raw.get("supported_parameters", raw.get("supportedParameters")),
    ]
    if isinstance(raw.get("supported_parameters"), Mapping):
        parameter_sources.append(raw["supported_parameters"].get("values"))  # type: ignore[index]
    parameter_sources.extend(
        source.get("supported_parameters", source.get("supportedParameters")) for source in modality_sources[1:]
    )

    supported_parameters: list[str] = []
    for value in parameter_sources:
        if isinstance(value, list):
            normalized_parameters = [item.casefold() for item in value if isinstance(item, str)]
            reasoning_values.extend(normalized_parameters)
            supported_parameters.extend(normalized_parameters)

    if normalized.intersection({"tools", "tool_calling", "function_calling"}) or any(
        value in {"tools", "tool_calling", "function_calling"} for value in supported_parameters
    ):
        added.add("tool_calling:declared")

    if any(
        value in {"image", "images", "vision", "visual", "multimodal"} or "image" in value for value in modality_values
    ):
        added.update({"vision", "input:image"})
    if any(value in {"text", "texts"} or "text" in value for value in modality_values):
        added.add("input:text")

    for key in ("reasoning_efforts", "reasoningEfforts", "reasoning_options", "reasoningOptions"):
        value = raw.get(key)
        if isinstance(value, list):
            normalized_efforts = _normalized_effort_list(value)
            if not is_openrouter_catalog and top_level_efforts is None:
                top_level_efforts = normalized_efforts
            if not is_openrouter_catalog:
                reasoning_values.extend(item.casefold() for item in value if isinstance(item, str))
    if any("reason" in value or "think" in value for value in reasoning_values):
        added.add("reasoning")
    if raw.get("reasoning") is True or raw.get("thinking") is True:
        added.add("reasoning")

    lowered_id = model_id.casefold()
    effort_declared, reported_efforts = _reported_effort_capabilities(capability_map)
    reasoning_metadata = raw.get("reasoning")
    if isinstance(reasoning_metadata, Mapping):
        value = reasoning_metadata.get("supported_efforts")
        if isinstance(value, list):
            normalized_efforts = _normalized_effort_list(value)
            if not effort_declared:
                effort_declared = True
                reported_efforts = normalized_efforts
    if not effort_declared and top_level_efforts is not None:
        effort_declared = True
        reported_efforts = top_level_efforts
    if deepseek_effort_declared:
        # DeepSeek's catalog omits `none`: that disables thinking in the API;
        # it is not one of the catalog's active reasoning levels.
        effort_declared = True
        reported_efforts = top_level_efforts or ()
    thinking_declared, reported_thinking_modes = _reported_thinking_modes(capability_map)
    thinking_metadata = capability_map.get("thinking") if capability_map is not None else None
    thinking_explicitly_unsupported = (
        isinstance(thinking_metadata, Mapping) and _supported_capability(thinking_metadata.get("supported")) is False
    )
    image_support_values = (
        [_supported_capability(capability_map[key]) for key in ("image_input", "vision") if key in capability_map]
        if capability_map is not None
        else []
    )
    declared_image_support = [value for value in image_support_values if value is not None]
    image_input_supported = (
        False if False in declared_image_support else True if True in declared_image_support else None
    )
    profile_capabilities = _provider_profile_capabilities(provider_kind, lowered_id, endpoint=endpoint)
    if provider_kind.casefold() == "google-gemini" and type(raw.get("thinking")) is bool and raw["thinking"] is False:
        profile_capabilities = {
            item for item in profile_capabilities if not item.startswith("reasoning:") and item != "reasoning"
        }
        added = {item for item in added if not item.startswith("reasoning:") and item != "reasoning"}
    if image_input_supported is not None:
        profile_capabilities.difference_update({"vision", "input:image"})
    if effort_declared:
        profile_capabilities = {
            item for item in profile_capabilities if not item.startswith("reasoning:") and item != "reasoning"
        }
        added = {item for item in added if not item.startswith("reasoning:") and item != "reasoning"}
        added.add("reasoning:declared")
        added.update(f"reasoning:{effort}" for effort in reported_efforts)
        if deepseek_effort_declared:
            added.add("reasoning:none")
    elif thinking_explicitly_unsupported:
        profile_capabilities = {item for item in profile_capabilities if not item.startswith("reasoning:")}
        added = {item for item in added if not item.startswith("reasoning:")}
    if thinking_declared:
        profile_capabilities = {item for item in profile_capabilities if not item.startswith("thinking:")}
        added.update({"thinking:declared"})
        added.update(f"thinking:{mode}" for mode in reported_thinking_modes)
    elif effort_declared and capability_map is not None and _supported_capability(capability_map.get("effort")) is True:
        added.add("thinking:effort")
    added.update(profile_capabilities)
    if image_input_supported is True:
        added.update({"vision", "input:image"})
    elif image_input_supported is False:
        added.difference_update({"vision", "input:image", "vision:inferred"})
    if (
        not explicit_vision
        and not added.intersection({"vision", "input:image"})
        and re.search(
            r"(?:gpt-4o|gpt-4\.1|gpt-5|claude-(?:3|4)|gemini|vision|llava|pixtral|qwen.*vl)",
            lowered_id,
        )
        and image_input_supported is None
    ):
        # A name match is useful diagnostics, but is deliberately not enough
        # to enable an image attachment in the UI or provider request.
        added.add("vision:inferred")
    if (
        "reasoning" not in added
        and not any(item.startswith("reasoning:") for item in added)
        and re.search(
            r"(?:^|[-_/])(?:o1|o3|o4|gpt-5|deepseek-r1|qwq)(?:[-_/]|$)",
            lowered_id,
        )
    ):
        added.add("reasoning:inferred")

    return sorted(set(capabilities).union(added))


def _provider_profile_capabilities(provider_kind: str, model_id: str, *, endpoint: str | None) -> set[str]:
    """Return narrowly scoped capabilities backed by provider model docs.

    A provider's generic ``/models`` response often contains only an id and
    owner.  These profiles cover stable first-party model families where the
    provider documents image input or exact effort values.  Gateway/custom
    endpoints are intentionally excluded: their model ids may route to a
    different deployment and must advertise capabilities themselves.
    """

    provider = provider_kind.casefold()
    endpoint_host = (urlsplit(endpoint).hostname or "").casefold() if isinstance(endpoint, str) else ""
    added: set[str] = set()
    reasoning_profile = provider_reasoning_profile(provider_kind, model_id, endpoint)
    if provider == "openai" and endpoint_host == "api.openai.com":
        if reasoning_profile is not None or re.search(
            r"(?:^|[-_/])(?:gpt-4o(?:[-_/]|$)|gpt-4\.1(?:[-_/]|$)|o[134](?:[-./_]|$))",
            model_id,
        ):
            added.update({"vision", "input:image", _PROFILE_CAPABILITY})
    elif provider == "anthropic" and endpoint_host == "api.anthropic.com":
        if re.search(
            r"(?:^|[-_/])claude-(?:(?:3|4)(?:[-./_]|$)|(?:opus|sonnet|haiku)-[34](?:[-./_]|$)|(?:opus|sonnet|fable|mythos)-5(?:[-./_]|$))",
            model_id,
        ):
            added.update({"vision", "input:image", _PROFILE_CAPABILITY})
    elif (
        provider == "google-gemini"
        and endpoint_host == "generativelanguage.googleapis.com"
        and model_id.startswith("gemini-")
        and "embedding" not in model_id
        and "text-embedding" not in model_id
    ):
        added.update({"vision", "input:image", _PROFILE_CAPABILITY})
    elif provider == "groq" and endpoint_host == "api.groq.com" and model_id == "qwen/qwen3.8-27b":
        added.update({"vision", "input:image", "vision:max-images:3", _PROFILE_CAPABILITY})
    elif (
        provider == "nvidia-nim"
        and endpoint_host == "integrate.api.nvidia.com"
        and model_id in {"meta/muse-glimmer-30b", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash"}
    ) or (
        provider == "deepseek"
        and endpoint_host == "api.deepseek.com"
        and model_id in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}
    ):
        added.update({"vision", "input:image", _PROFILE_CAPABILITY})
    if reasoning_profile is not None:
        added.update(f"reasoning:{effort}" for effort in reasoning_profile.efforts)
        added.add(_PROFILE_CAPABILITY)
        if reasoning_profile.anthropic_thinking_mode is not None:
            added.add(f"thinking:{reasoning_profile.anthropic_thinking_mode}")
    return added


def _is_non_chat_model(
    raw: Mapping[str, object],
    model_id: str,
    capabilities: list[str],
    *,
    provider_kind: str,
) -> bool:
    lowered = model_id.casefold()
    values = {item.casefold() for item in capabilities}
    if any(
        token in lowered
        for token in (
            "embedding",
            "moderation",
            "whisper",
            "transcription",
            "text-to-speech",
            "tts",
            "dall-e",
            "gpt-image",
            "sora",
        )
    ):
        return True
    if any("embedding" in value or "moderation" in value for value in values):
        return True
    methods = raw.get("supportedGenerationMethods", raw.get("supported_generation_methods"))
    if isinstance(methods, list) and methods:
        method_names = {item.casefold() for item in methods if isinstance(item, str)}
        if not method_names.intersection(
            {"generatecontent", "generatecontentstream", "chat", "chatcompletions", "completions"}
        ):
            return True
    return (
        provider_kind.casefold() == "openai" and re.fullmatch(r"(?:o[134]|gpt-5(?:\.[0-9]+)?)-pro", lowered) is not None
    )


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value == 0:
        # Some provider catalogs use zero to mean that a limit is undisclosed.
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
