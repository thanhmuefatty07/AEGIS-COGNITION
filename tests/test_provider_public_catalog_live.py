"""Opt-in live checks for model-catalog auth boundaries; no API credentials are used.

Set AEGIS_TEST_PROVIDER_NO_KEY_SMOKE=1 in the shell, then run:
    uv run pytest tests/test_provider_public_catalog_live.py -q
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit

import pytest

from core.python.aegis.connections import ConnectionRecord
from core.python.aegis import discovery
from core.python.aegis.discovery import DiscoveryError, DiscoveryResponse, discover_connection_models
from core.python.aegis.provider_registry import PROVIDER_SPECS, ProviderSpec


pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_TEST_PROVIDER_NO_KEY_SMOKE") != "1",
    reason="set AEGIS_TEST_PROVIDER_NO_KEY_SMOKE=1 to run live no-key provider checks",
)

# Last directly measured at the provider-native catalog URLs on 2026-10-04.
# These are endpoint outcomes, not claims about why the provider denies access.
_PUBLIC_ANONYMOUS_CATALOGS = {"openrouter", "nvidia-nim"}
_ANONYMOUS_HTTP_STATUS = {
    "anthropic": 401,
    "google-gemini": 403,
    "groq": 403,
    "xai": 401,
    "perplexity": 401,
    "deepseek": 401,
    "openai": 401,
}
_CATALOG_PATHS = {
    "anthropic": "/v1/models",
    "openrouter": "/api/v1/models",
    "google-gemini": "/v1beta/models",
    "groq": "/openai/v1/models",
    "xai": "/v1/language-models",
    "perplexity": "/router/v1/models",
    "nvidia-nim": "/v1/models",
    "deepseek": "/models",
    "openai": "/v1/models",
}
_QUERY_PARAMETERS = {
    "anthropic": {"limit"},
    "openrouter": {"offset", "limit"},
    "google-gemini": {"pagesize"},
}


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.provider_kind)
def test_live_no_key_catalog_behavior_is_exact_and_credential_free(spec: ProviderSpec) -> None:
    connection = ConnectionRecord(
        connection_id=f"no-key-provider-smoke:{spec.provider_kind}",
        provider_kind=spec.provider_kind,
        endpoint=spec.endpoint,
        protocol=spec.protocol,
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )

    requests: list[tuple[str, Mapping[str, str], float]] = []
    responses: list[DiscoveryResponse] = []

    def reject_secret_lookup(_secret_ref: str) -> str | None:
        pytest.fail("public catalog discovery must not read a credential")

    def capture_request(url: str, headers: Mapping[str, str], timeout: float) -> DiscoveryResponse:
        parsed_url = urlsplit(url)
        assert parsed_url.scheme == "https"
        assert parsed_url.hostname in spec.native_hosts
        assert parsed_url.path == _CATALOG_PATHS[spec.provider_kind]
        assert parsed_url.username is None and parsed_url.password is None
        query = parse_qsl(parsed_url.query)
        assert {name.casefold() for name, _ in query} <= _QUERY_PARAMETERS.get(spec.provider_kind, set())
        if spec.provider_kind == "openrouter":
            assert dict(query).get("limit") == str(discovery.OPENROUTER_MODELS_PAGE_SIZE)
        assert 0 < timeout <= discovery.DISCOVERY_TIMEOUT_SECONDS
        assert dict(headers) == {"Accept": "application/json"}
        requests.append((url, headers, timeout))
        response = discovery._request_json(url, headers, timeout)
        responses.append(response)
        return response

    def discover() -> tuple[dict[str, object], ...]:
        return discover_connection_models(
            connection,
            secret_resolver=reject_secret_lookup,
            requester=capture_request,
            egress_check=lambda _connection_id: True,
        )

    if spec.provider_kind in _PUBLIC_ANONYMOUS_CATALOGS:
        models = discover()
        model_ids = [model["model_id"] for model in models]
        assert model_ids
        assert all(isinstance(model_id, str) and model_id.strip() for model_id in model_ids)
        assert len(model_ids) == len(set(model_ids))
        assert responses and all(response.status == 200 for response in responses)
        assert all(
            response.content_type is not None and "application/json" in response.content_type.casefold()
            for response in responses
        )
    else:
        expected_status = _ANONYMOUS_HTTP_STATUS[spec.provider_kind]
        with pytest.raises(DiscoveryError) as error:
            discover()
        assert error.value.status_code == expected_status, str(error.value)
        assert not responses, "an auth-denied provider must not be reported as a discovered catalog"

    if spec.provider_kind == "openrouter":
        assert 1 <= len(requests) <= discovery.MAX_OPENROUTER_MODELS_PAGES
        offsets = [dict(parse_qsl(urlsplit(url).query)).get("offset") for url, _headers, _timeout in requests]
        assert offsets == [
            str(index * discovery.OPENROUTER_MODELS_PAGE_SIZE) for index in range(len(requests))
        ]
    else:
        assert len(requests) == 1, "anonymous checks must call only the selected provider's catalog endpoint"
