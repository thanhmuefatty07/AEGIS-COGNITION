from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.python.chatgpt_web_client import (
    CHATGPT_WEB_BASE_URL,
    ChatGPTWebClient,
    ChatGPTWebConfig,
    ChatGPTWebUnavailableError,
)
from aegis_cognition.config import resolve_api_key
from aegis_cognition.config import AgentConfig


def test_chatgpt_web_config_rejects_non_loopback_urls() -> None:
    with pytest.raises(ValueError, match="loopback"):
        ChatGPTWebConfig(base_url="https://example.invalid/v1")
    with pytest.raises(ValueError, match="credentials"):
        ChatGPTWebConfig(base_url="http://user:pass@127.0.0.1:17841/v1")


def test_chatgpt_web_config_accepts_pinned_defaults() -> None:
    config = ChatGPTWebConfig()
    assert config.base_url == CHATGPT_WEB_BASE_URL
    assert config.model == "chatgpt-web/high"
    assert config.timeout == 120.0


def test_chatgpt_web_provider_uses_a_non_secret_local_sentinel() -> None:
    assert resolve_api_key({"llm": {"provider": "chatgpt-web"}}) == "local-loopback"


def test_chatgpt_web_client_can_be_supplied_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGIS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = AgentConfig.from_inputs("local bridge", llm=ChatGPTWebClient())
    assert config.api_key is None


def test_chatgpt_web_client_maps_responses_output_and_drops_orchestration_metadata() -> None:
    calls: list[dict[str, object]] = []

    class Responses:
        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(output_text="bridge reply")

    client = ChatGPTWebClient(client=SimpleNamespace(responses=Responses()))
    assert client.invoke(
        "hello",
        system_context="be concise",
        mode="lab",
        max_output_tokens=256,
    ) == "bridge reply"
    assert calls == [{
        "model": "chatgpt-web/high",
        "input": "hello",
        "max_output_tokens": 256,
        "instructions": "be concise",
    }]


def test_chatgpt_web_client_rejects_empty_response() -> None:
    class Responses:
        def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(output_text="")

    client = ChatGPTWebClient(client=SimpleNamespace(responses=Responses()))
    with pytest.raises(ChatGPTWebUnavailableError, match="no output"):
        client.invoke("hello")
